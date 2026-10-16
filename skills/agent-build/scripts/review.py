#!/usr/bin/env python3
"""Agent Build: a fresh, read-only review of one unit (pins P2b, I8, I12).

The reviewer is the run's engine (R/config.json `engine`), or the other engine
when `cross_engine_review` is true. Each launch is a new session with a
read-only argument list; nothing resumes an earlier session.

Curated from the parallel-build-loop review.sh (copy of 8 Oct 2026) and the
autopilot launcher's start check (copy of 12 Oct 2026).
  Kept:    the prompt header line; the `verdict:` pattern (last match wins);
           the read-only Codex argument list; stdin closed, own process group;
           kill the group at the time limit; the start check (output growth by
           the check time, kill, relaunch once).
  Changed: an Anthropic reviewer with only Read, Glob and Grep; the diff and
           file list are written to R/reviews/<U>-r<N>.diff first, so a session
           without a shell can read the change; empty or verdict-less output is
           a failed attempt; the event payload is pin G28's.
  Dropped: --extra, the reviewer override variable, the note events, exit 5,
           6 and 124.

Usage:
  review.py --run R --uid U --path ABS --base C --brief FILE [--round N] [--integration]

Env: AB_REVIEW_CHECK_SECS (start-check window, default 300),
     AB_REVIEW_TIMEOUT (overall limit per attempt, default 900).

Exit: 0 verdict parsed (clean or must-fix); 1 failed review after one
relaunch, or the integration guard refused; 2 usage or unreadable input.
"""
import argparse
import json
import os
import re
import signal
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import _records  # noqa: E402
import log  # noqa: E402

PREAMBLE = os.path.join(HERE, "..", "prompts", "review-preamble.md")
ENGINES = ("anthropic", "openai")

_VERDICT_LINE = re.compile(r"^\s*\**verdict\**\s*:", re.I)
_VERDICT = re.compile(
    r"^\s*\**verdict\**\s*:\s*\**\s*(clean|must-fix\s*\(\s*(\d+)\s*\))\s*\**\s*$", re.I)


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        sys.stderr.write("review.py: %s\n" % message)
        sys.exit(_records.USAGE)


def die(code, msg):
    sys.stderr.write("review.py: %s\n" % msg)
    sys.exit(code)


def git(path, *args):
    return subprocess.run(["git", "-C", path] + list(args), stdin=subprocess.DEVNULL,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          universal_newlines=True)


def argv_for(engine, path, prompt):
    """The exact read-only argument list for one engine (criterion 2)."""
    if engine == "openai":
        return ["codex", "exec", "--skip-git-repo-check", "--sandbox", "read-only",
                "-C", path, prompt]
    return ["claude", "-p", prompt, "--strict-mcp-config", "--output-format", "stream-json",
            "--verbose", "--tools", "Read,Glob,Grep", "--allowedTools", "Read", "Glob", "Grep",
            "--disallowedTools", "Bash", "Write", "Edit", "NotebookEdit"]


def review_text(engine, out_path):
    """Codex's stdout, or the `result` of Claude's last stream-json result line."""
    try:
        with open(out_path, encoding="utf-8", errors="replace") as f:
            raw = f.read()
    except OSError:
        return ""
    if engine == "openai":
        return raw
    text = ""
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if isinstance(obj, dict) and obj.get("type") == "result":
            res = obj.get("result")
            text = res if isinstance(res, str) else ""
    return text


def parse_verdict(text):
    """(verdict, must_fix) from the last `verdict:` line, or (None, None)."""
    lines = [l for l in text.splitlines() if _VERDICT_LINE.match(l)]
    m = _VERDICT.match(lines[-1]) if lines else None
    if not m:
        return None, None
    if m.group(1).lower() == "clean":
        return "clean", 0
    return "must-fix", int(m.group(2))


def kill_group(p):
    try:
        os.killpg(p.pid, signal.SIGTERM)
    except OSError:
        pass
    deadline = time.time() + 5
    while time.time() < deadline:
        p.poll()
        try:
            os.killpg(p.pid, 0)
        except OSError:  # gone (EPERM on macOS when only zombies remain)
            break
        time.sleep(0.1)
    else:
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except OSError:
            pass
    p.wait()


def attempt(cmd, cwd, stem, engine, window, limit):
    """Run one session; return (ok, text)."""
    out, err = stem + ".out", stem + ".err"
    env = dict(os.environ, SHELL="/bin/bash")
    with open(out, "w") as fo, open(err, "w") as fe:
        try:
            p = subprocess.Popen(cmd, cwd=cwd, stdin=subprocess.DEVNULL, stdout=fo, stderr=fe,
                                 start_new_session=True, env=env)
        except OSError as e:
            fe.write("review.py: could not start %s: %s\n" % (cmd[0], e))
            return False, ""

    def size():
        return sum(os.path.getsize(f) for f in (out, err) if os.path.exists(f))

    interval = min(10.0, window / 10.0)
    t0 = time.time()
    deadline = t0 + limit
    prev = cur = size()
    # Start check: sample until the window ends or the session exits; the
    # overall limit applies inside the window too.
    while time.time() - t0 < window and p.poll() is None:
        left = deadline - time.time()
        if left <= 0:
            kill_group(p)
            return False, ""
        time.sleep(min(interval, left))
        prev, cur = cur, size()
    if p.poll() is None and time.time() >= deadline:
        kill_group(p)
        return False, ""
    if p.poll() is None:
        if cur <= prev:
            kill_group(p)
            return False, ""
        # Started: wait for the overall limit.
        try:
            p.wait(timeout=max(0.0, limit - (time.time() - t0)))
        except subprocess.TimeoutExpired:
            kill_group(p)
            return False, ""
    if p.returncode != 0:
        return False, review_text(engine, out)
    text = review_text(engine, out)
    if not text.strip():
        return False, text
    verdict, _ = parse_verdict(text)
    return verdict is not None, text


def guard(run, head):
    """The integration guard (pin I12): None when the review may start."""
    rec, err = _records.load_json(os.path.join(run, "records", "integration-suite.json"))
    if rec is None:
        return "records/integration-suite.json is not readable (%s)" % err
    if not isinstance(rec, dict):
        return "records/integration-suite.json is not a JSON object"
    ex = rec.get("exit")
    if not (isinstance(ex, int) and not isinstance(ex, bool) and ex == 0):
        return "integration suite exit is %r, not 0" % (ex,)
    if rec.get("sandboxed") is not False:
        return "integration suite sandboxed is %r, not false" % (rec.get("sandboxed"),)
    if rec.get("commit") != head:
        return "integration suite commit %r is not the reviewed HEAD %s" % (rec.get("commit"), head)
    if rec.get("verdict") != "pass":
        return "integration suite verdict is %r, not pass" % (rec.get("verdict"),)
    if rec.get("evidence_hits") != []:
        return "integration suite evidence_hits is %r, not empty" % (rec.get("evidence_hits"),)
    return None


def env_secs(name, default):
    try:
        v = float(os.environ.get(name) or default)
    except ValueError:
        die(_records.USAGE, "%s must be a number" % name)
    if v <= 0:
        die(_records.USAGE, "%s must be positive" % name)
    return v


def main(argv=None):
    ap = _Parser(prog="review.py")
    ap.add_argument("--run", required=True)
    ap.add_argument("--uid", required=True)
    ap.add_argument("--path", required=True)
    ap.add_argument("--base", required=True)
    ap.add_argument("--brief", required=True)
    ap.add_argument("--round", type=int, default=1)
    ap.add_argument("--integration", action="store_true")
    a = ap.parse_args(argv)
    window = env_secs("AB_REVIEW_CHECK_SECS", 300)
    limit = env_secs("AB_REVIEW_TIMEOUT", 900)

    run = a.run
    if not os.path.isdir(run):
        die(_records.USAGE, "--run is not a folder: %s" % run)
    cfg, err = _records.load_json(os.path.join(run, "config.json"))
    if cfg is None or not isinstance(cfg, dict):
        die(_records.USAGE, "config.json is not readable: %s" % (err or "not a JSON object"))
    engine = cfg.get("engine")
    if engine not in ENGINES:
        die(_records.USAGE, "config.json engine must be anthropic or openai, got %r" % (engine,))
    if cfg.get("cross_engine_review") is True:
        engine = "openai" if engine == "anthropic" else "anthropic"
    try:
        with open(a.brief, encoding="utf-8") as f:
            brief = f.read()
        with open(PREAMBLE, encoding="utf-8") as f:
            preamble = f.read()
    except (OSError, UnicodeDecodeError) as e:
        die(_records.USAGE, "brief or preamble is not readable: %s" % e)
    path = os.path.abspath(a.path)
    r = git(path, "rev-parse", "--is-inside-work-tree") if os.path.isdir(path) else None
    if r is None or r.returncode != 0 or r.stdout.strip() != "true":
        die(_records.USAGE, "--path is not a git work tree: %s" % a.path)
    r = git(path, "rev-parse", "--verify", "--quiet", a.base + "^{commit}")
    if r.returncode != 0 or not r.stdout.strip():
        die(_records.USAGE, "--base does not resolve to a commit: %s" % a.base)
    base = r.stdout.strip()
    r = git(path, "rev-parse", "--verify", "HEAD")
    if r.returncode != 0:
        die(_records.USAGE, "--path has no HEAD commit")
    head = r.stdout.strip()

    if a.integration:
        why = guard(run, head)
        if why:
            die(_records.FAILED, "integration review not started: " + why)

    reviews = os.path.join(run, "reviews")
    os.makedirs(reviews, exist_ok=True)
    stem = os.path.join(reviews, "%s-r%d" % (a.uid, a.round))
    diff_path = os.path.abspath(stem + ".diff")
    d = git(path, "diff", "%s..HEAD" % base)
    names = git(path, "diff", "--name-only", "%s..HEAD" % base)
    if d.returncode != 0 or names.returncode != 0:
        die(_records.USAGE, "git diff failed: %s" % (d.stderr or names.stderr).strip())
    with open(diff_path, "w", encoding="utf-8") as f:
        f.write(d.stdout)
        f.write("\n# Changed files\n")
        f.write(names.stdout)

    line = "Review %s at commit %s (diff from %s), round %d. The diff is at %s." % (
        path, head, base, a.round, diff_path)
    prompt = preamble.rstrip("\n") + "\n\n" + line + "\n\n" + brief
    cmd = argv_for(engine, path, prompt)

    attempts, ok, text = 0, False, ""
    while attempts < 2 and not ok:
        attempts += 1
        ok, text = attempt(cmd, path, stem, engine, window, limit)
    with open(stem + ".md", "w", encoding="utf-8") as f:
        f.write(text)
    verdict, must_fix = parse_verdict(text) if ok else ("failed", None)
    log.append(run, "review.returned", {
        "uid": a.uid, "round": a.round, "engine": engine, "verdict": verdict,
        "must_fix": must_fix, "attempts": attempts})
    if not ok:
        die(_records.FAILED, "review %s round %d failed after %d attempts" % (
            a.uid, a.round, attempts))
    print("review %s round %d: commit=%s verdict=%s must_fix=%d attempts=%d" % (
        a.uid, a.round, head, verdict, must_fix, attempts))
    return _records.OK


if __name__ == "__main__":
    sys.exit(main())
