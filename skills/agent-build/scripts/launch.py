#!/usr/bin/env python3
"""Start one build unit headless, per engine, under a supervising parent.

    launch.py unit --run R --uid U --worktree ABS --repo ABS --branch B --base C
                   --round N --prompt FILE [--estimate-min N] [--dry-run]

The engine is R/config.json `engine`. Anthropic units run `claude -p` under Claude
Code's Bash sandbox (failIfUnavailable); OpenAI units run `codex exec` under
`workspace-write`. Neither gets R/frozen, R/records, R/residuals.json or
R/run-log.jsonl as a writable path; a worktree whose writable roots touch them is
refused with exit 3.

Exit codes: 0 launched (or printed with --dry-run); 2 usage or launch failure;
3 placement refused.
"""
import argparse
import fcntl
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import _records  # noqa: E402
import log  # noqa: E402

try:
    import config  # noqa: E402
    _load_config = config.load
except ImportError:
    # STUB-0482 U1b: config.load(run) -> (config, errors), used until config.py lands.
    def _load_config(run):
        obj, err = _records.load_json(os.path.join(run, "config.json"))
        if err:
            return None, ["config.json: %s" % err]
        if not isinstance(obj, dict):
            return None, ["config.json is not a JSON object"]
        return obj, []

ENGINES = ("anthropic", "openai")
PROTECTED = ("frozen", "records", "residuals.json", "run-log.jsonl")
TOOLS = ["Bash", "Read", "Write", "Edit", "Glob", "Grep"]
PID_WAIT_S = 15


class Refused(Exception):
    """Placement refused (exit 3)."""


class Usage(Exception):
    """Usage or launch failure (exit 2)."""


def _real(path):
    return os.path.realpath(path)


def _related(a, b):
    """True when a equals, sits inside or contains b."""
    a, b = _real(a), _real(b)
    if a == b:
        return True
    return a.startswith(b.rstrip(os.sep) + os.sep) or b.startswith(a.rstrip(os.sep) + os.sep)


def protected_paths(run):
    return [os.path.join(_real(run), name) for name in PROTECTED]


def unit_dir(run, uid):
    return os.path.join(_real(run), "units", uid)


def tmp_dir(run, uid):
    return os.path.join(unit_dir(run, uid), "tmp")


def _git(cwd, *args):
    p = subprocess.run(["git", "-C", cwd] + list(args), stdin=subprocess.DEVNULL,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
    return p.returncode, p.stdout.strip(), p.stderr.strip()


def git_roots(worktree):
    """The four git writable roots of a worktree: admin dir, objects, refs, logs."""
    rc, admin, err = _git(worktree, "rev-parse", "--absolute-git-dir")
    if rc != 0:
        raise Usage("git rev-parse --absolute-git-dir failed in %s: %s" % (worktree, err))
    rc, common, err = _git(worktree, "rev-parse", "--path-format=absolute", "--git-common-dir")
    if rc != 0:
        raise Usage("git rev-parse --git-common-dir failed in %s: %s" % (worktree, err))
    return [admin] + [os.path.join(common, n) for n in ("objects", "refs", "logs")]


def predicted_roots(repo, worktree):
    """Git roots a worktree not yet created would get (used before creating it)."""
    rc, common, err = _git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir")
    if rc != 0:
        raise Usage("--repo is not a git repository: %s" % err)
    admin = os.path.join(common, "worktrees", os.path.basename(worktree.rstrip(os.sep)))
    return [admin] + [os.path.join(common, n) for n in ("objects", "refs", "logs")]


def check_paths(run, worktree, repo):
    """Placement rule checks that need no git: exit 3 when broken."""
    for flag, value in (("--worktree", worktree), ("--repo", repo)):
        if not os.path.isabs(value):
            raise Refused("placement rule: %s must be an absolute path (got %s)" % (flag, value))
    w, r = _real(worktree), _real(repo)
    if w == r or w.startswith(r.rstrip(os.sep) + os.sep):
        raise Refused("placement rule: the worktree must not be the repo folder or sit inside it")
    for p in protected_paths(run):
        if _related(worktree, p):
            raise Refused("placement rule: the worktree equals, sits inside or contains %s" % p)


def check_roots(run, roots, kind="git writable root"):
    for root in roots:
        for p in protected_paths(run):
            if _related(root, p):
                raise Refused("placement rule: %s %s equals, sits inside or "
                              "contains %s" % (kind, root, p))


def existing_worktree_ok(worktree, repo, branch):
    """Return None when worktree is a worktree of repo on branch, else the reason."""
    rc, top, err = _git(worktree, "rev-parse", "--show-toplevel")
    if rc != 0 or _real(top) != _real(worktree):
        return "%s exists but is not a git worktree" % worktree
    rc1, c1, _ = _git(worktree, "rev-parse", "--path-format=absolute", "--git-common-dir")
    rc2, c2, err = _git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir")
    if rc1 != 0 or rc2 != 0 or _real(c1) != _real(c2):
        return "%s is not a worktree of %s" % (worktree, repo)
    rc, head, _ = _git(worktree, "rev-parse", "--abbrev-ref", "HEAD")
    if rc != 0 or head != branch:
        return "%s is on branch %s, not %s" % (worktree, head, branch)
    return None


def create_worktree(repo, worktree, branch, base):
    rc, _, _ = _git(repo, "rev-parse", "--verify", "--quiet", "refs/heads/" + branch)
    if rc == 0:
        args = ["worktree", "add", worktree, branch]
    else:
        args = ["worktree", "add", "-b", branch, worktree, base]
    rc, _, err = _git(repo, *args)
    if rc != 0:
        raise Usage("git worktree add failed: %s" % err)


def settings_json(run, uid, roots):
    deny = []
    for name in ("frozen/**", "records/**", "residuals.json", "run-log.jsonl"):
        path = _real(run) + "/" + name
        deny.append("Edit(/%s)" % path)
        deny.append("Write(/%s)" % path)
    settings = {
        "sandbox": {
            "enabled": True,
            "autoAllowBashIfSandboxed": True,
            "failIfUnavailable": True,
            "allowUnsandboxedCommands": False,
            "filesystem": {"allowWrite": [tmp_dir(run, uid)] + roots},
        },
        "permissions": {"deny": deny},
    }
    return json.dumps(settings, sort_keys=False)


def build_argv(engine, run, uid, worktree, roots, prompt):
    """Return (argv, extra_env) for the engine."""
    if engine == "anthropic":
        argv = ["claude", "-p", prompt, "--permission-mode", "acceptEdits",
                "--strict-mcp-config", "--output-format", "stream-json", "--verbose",
                "--allowedTools"] + TOOLS + ["--settings", settings_json(run, uid, roots)]
        return argv, {}
    argv = ["codex", "exec", "--ignore-user-config", "-c", 'approval_policy="never"',
            "--sandbox", "workspace-write"]
    for root in roots + [tmp_dir(run, uid)]:
        argv += ["--add-dir", root]
    argv += ["-C", worktree, "--json", prompt]
    return argv, {"TMPDIR": tmp_dir(run, uid)}


def _positive_int(value):
    try:
        n = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("must be a positive integer: %r" % value)
    if n < 1 or str(n) != value.strip():
        raise argparse.ArgumentTypeError("must be a positive integer: %r" % value)
    return n


def _parser():
    p = argparse.ArgumentParser(prog="launch.py", allow_abbrev=False)
    sub = p.add_subparsers(dest="cmd")
    u = sub.add_parser("unit", allow_abbrev=False)
    for flag in ("--run", "--uid", "--worktree", "--repo", "--branch", "--base", "--prompt"):
        u.add_argument(flag, required=True)
    u.add_argument("--round", required=True, type=_positive_int)
    u.add_argument("--estimate-min", type=_positive_int, default=None)
    u.add_argument("--dry-run", action="store_true")
    s = sub.add_parser("_supervise", allow_abbrev=False)
    s.add_argument("--unit-dir", required=True)
    s.add_argument("--uid", required=True)
    return p


def supervise(unit_folder, uid):
    """Internal: read the launch spec from stdin, run the agent, record its exit."""
    spec = json.loads(sys.stdin.read())
    sys.stdin.close()
    env = dict(os.environ)
    env.update(spec["env"])
    pid_path = os.path.join(unit_folder, uid + ".pid")
    exit_path = os.path.join(unit_folder, uid + ".exit")
    with open(os.path.join(unit_folder, uid + ".log"), "ab") as out:
        try:
            agent = subprocess.Popen(spec["argv"], cwd=spec["cwd"], env=env,
                                     stdin=subprocess.DEVNULL, stdout=out,
                                     stderr=subprocess.STDOUT, start_new_session=True)
        except OSError as err:
            out.write(("spawn failed: %s\n" % err).encode("utf-8"))
            _write_line(exit_path, "127 %s" % _records.now_iso())
            return 2
        _write_line(pid_path, str(agent.pid))
        code = agent.wait()
    if code < 0:
        code = 128 - code
    _write_line(exit_path, "%d %s" % (code, _records.now_iso()))
    return 0


def _write_line(path, text):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text + "\n")
    os.replace(tmp, path)


def launch(a):
    run, uid = a.run, a.uid
    check_paths(run, a.worktree, a.repo)
    cfg, errors = _load_config(run)
    if errors or not isinstance(cfg, dict):
        raise Usage("unreadable config.json: %s" % "; ".join(errors or ["no object"]))
    engine = cfg.get("engine")
    if engine not in ENGINES:
        raise Usage("config.json engine must be anthropic or openai (got %r)" % (engine,))
    try:
        with open(a.prompt, encoding="utf-8") as f:
            prompt = f.read()
    except (OSError, UnicodeDecodeError) as err:
        raise Usage("unreadable prompt file: %s" % err)

    exists = os.path.exists(a.worktree)
    if exists:
        reason = existing_worktree_ok(a.worktree, a.repo, a.branch)
        if reason:
            raise Usage(reason)
        roots = git_roots(a.worktree)
    else:
        roots = predicted_roots(a.repo, a.worktree)
    check_roots(run, roots)
    # The temp folder is checked resolved, so a symlink left in R/units/<U>/ cannot
    # hand a protected path to the agent as a writable root.
    check_roots(run, [tmp_dir(run, uid)], kind="temp folder")
    worktree = _real(a.worktree) if exists else a.worktree

    if a.dry_run:
        argv, env = build_argv(engine, run, uid, worktree, roots, prompt)
        print(json.dumps({"argv": argv, "cwd": worktree, "env": env}))
        return 0

    if not exists:
        create_worktree(a.repo, a.worktree, a.branch, a.base)
        worktree = _real(a.worktree)
        roots = git_roots(worktree)
        check_roots(run, roots)
    argv, env = build_argv(engine, run, uid, worktree, roots, prompt)

    folder = unit_dir(run, uid)
    os.makedirs(tmp_dir(run, uid), exist_ok=True)
    for ext in (".exit", ".pid"):
        path = os.path.join(folder, uid + ext)
        if os.path.exists(path):
            os.remove(path)

    try:
        sup = subprocess.Popen([sys.executable, os.path.abspath(__file__), "_supervise",
                                "--unit-dir", folder, "--uid", uid],
                               stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, start_new_session=True)
        sup.stdin.write(json.dumps({"argv": argv, "cwd": worktree, "env": env}).encode("utf-8"))
        sup.stdin.close()
    except OSError as err:
        raise Usage("spawn failed: %s" % err)

    pid_path = os.path.join(folder, uid + ".pid")
    exit_path = os.path.join(folder, uid + ".exit")
    deadline = time.time() + PID_WAIT_S
    while not os.path.exists(pid_path):
        if os.path.exists(exit_path) or time.time() > deadline:
            raise Usage("spawn failed: no agent pid written for %s" % uid)
        time.sleep(0.05)
    with open(pid_path, encoding="utf-8") as f:
        pid = int(f.read().strip())

    # One run-wide lock around read, check and append, so launches started together
    # count attempts and the round boundary once.
    with open(os.path.join(_real(run), "units", ".launch.lock"), "a") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            events = log.read(run)
            attempt = 1 + sum(1 for e in events
                              if e.get("type") == "unit.launched"
                              and (e.get("payload") or {}).get("uid") == uid)
            started = any(e.get("type") == "round.started"
                          and (e.get("payload") or {}).get("round") == a.round
                          for e in events)
            if not started:
                log.append(run, "round.started",
                           {"round": a.round, "first_launch": _records.now_iso()})
            log.append(run, "unit.launched", {
                "uid": uid, "attempt": attempt, "engine": engine, "pid": pid,
                "supervisor_pid": sup.pid, "worktree": worktree, "branch": a.branch,
                "base": a.base, "estimate_min": a.estimate_min,
            })
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
    return 0


def main(argv=None):
    a = _parser().parse_args(argv)
    if a.cmd == "_supervise":
        return supervise(a.unit_dir, a.uid)
    if a.cmd != "unit":
        _parser().print_usage(sys.stderr)
        return 2
    try:
        return launch(a)
    except Refused as err:
        sys.stderr.write("launch.py: %s\n" % err)
        return 3
    except Usage as err:
        sys.stderr.write("launch.py: %s\n" % err)
        return 2


if __name__ == "__main__":
    sys.exit(main())
