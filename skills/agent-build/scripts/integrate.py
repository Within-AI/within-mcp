#!/usr/bin/env python3
"""Agent Build: the integration step (pins P2b, P3, H9, I12).

  suite --run R --repo ABS --commit C --command CMD...
      Resolves C to a full sha, checks it out as a detached temporary worktree
      of --repo (removed afterwards; the repo's own checkout is untouched), runs
      CMD there as a plain subprocess (stdin closed, own process group), writes
      R/records/integration-suite.json and appends integration.suite
      {commit, exit, verdict}. Verdict pass only for exit 0 and no evidence hit.
      With CODEX_SANDBOX set it runs nothing and records sandboxed: true.
  check-evidence --repo ABS
      Scans tracked Python test files for reads or writes of evidence/ and
      prints "path:line: text" per hit.

Curated from the parallel-build-loop integrate.sh (copy of 8 Oct 2026).
  Kept:    the temporary worktree, removed with --force afterwards.
  Changed: a detached checkout of one named commit, no branch, no merge; the
           evidence/ folder is not stripped silently: a test that depends on it
           is reported, because integration never carries evidence/.
  Dropped: merging unit branches and the commit step.

Exit: 0 pass; 1 fail (or evidence hits); 2 usage error or unresolvable commit.
"""
import argparse
import ast
import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _records  # noqa: E402
import log  # noqa: E402

RECORD = os.path.join("records", "integration-suite.json")
FOLDER = "evidence"
LITERAL = FOLDER + "/"
CALLS = ("join", "Path", "PurePath", "open", "makedirs", "mkdir")


def git(repo, *args):
    return subprocess.run(["git", "-C", repo] + list(args), stdin=subprocess.DEVNULL,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          universal_newlines=True)


def is_test_file(rel):
    parts = rel.split("/")
    name = parts[-1]
    if not name.endswith(".py"):
        return False
    return name.startswith("test_") or name.endswith("_test.py") or "tests" in parts[:-1]


def _call_name(func):
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _is_folder_const(node):
    return isinstance(node, ast.Constant) and node.value == FOLDER


def ast_hit_lines(source):
    """Line numbers where the parsed source names the evidence folder (H9 forms)."""
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return set()
    lines = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) \
                and node.value.rstrip().endswith("/" + FOLDER):
            lines.add(node.lineno)
        elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            for side in (node.left, node.right):
                if _is_folder_const(side):
                    lines.add(side.lineno)
        elif isinstance(node, ast.Call):
            if _call_name(node.func) in CALLS:
                for arg in node.args:
                    if _is_folder_const(arg):
                        lines.add(arg.lineno)
            for kw in node.keywords:
                if kw.arg == "dir" and _is_folder_const(kw.value):
                    lines.add(kw.value.lineno)
    return lines


def scan_file(path, rel):
    """["rel:line: text", ...] for one test file."""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            source = f.read()
    except OSError:
        return []
    text_lines = source.splitlines()
    lines = {i for i, text in enumerate(text_lines, 1) if LITERAL in text}
    lines |= ast_hit_lines(source)
    return ["%s:%d: %s" % (rel, n, text_lines[n - 1].strip() if n <= len(text_lines) else "")
            for n in sorted(lines)]


def scan_repo(repo):
    """(hits, error) over the tracked test files of repo."""
    res = git(repo, "ls-files", "-z")
    if res.returncode != 0:
        return None, "not a git repository: %s" % repo
    hits = []
    for rel in sorted(p for p in res.stdout.split("\0") if p):
        if is_test_file(rel):
            hits.extend(scan_file(os.path.join(repo, rel), rel))
    return hits, None


def runtime_writes(checkout):
    """Every path under an evidence/ folder that exists in the checkout after the command ran.

    Tracked or not: files are listed one by one, a folder with no files as `<folder>/`.
    """
    found = []
    for root, dirs, files in os.walk(checkout):
        dirs[:] = sorted(d for d in dirs if d != ".git")
        if os.path.basename(root) != FOLDER:
            continue
        inside = []
        for sub, _, names in os.walk(root):
            inside.extend(os.path.join(sub, n) for n in names)
        rel_root = os.path.relpath(root, checkout).replace(os.sep, "/")
        if inside:
            found.extend(os.path.relpath(p, checkout).replace(os.sep, "/") for p in inside)
        else:
            found.append(rel_root + "/")
        dirs[:] = []
    return ["written at runtime: %s" % rel for rel in sorted(found)]


def cmd_check_evidence(args):
    if not os.path.isdir(args.repo):
        sys.stderr.write("--repo is not a folder: %s\n" % args.repo)
        return _records.USAGE
    hits, err = scan_repo(args.repo)
    if err:
        sys.stderr.write(err + "\n")
        return _records.USAGE
    for hit in hits:
        print(hit)
    return _records.FAILED if hits else _records.OK


def run_in_checkout(repo, sha, command):
    """(exit code, evidence hits, reasons) from a detached temporary worktree."""
    tmp = tempfile.mkdtemp(prefix="ab-integrate-")
    checkout = os.path.join(tmp, "checkout")
    reasons = []
    try:
        res = git(repo, "worktree", "add", "--detach", checkout, sha)
        if res.returncode != 0:
            return None, [], ["could not check out %s: %s" % (sha, res.stderr.strip())]
        hits, _ = scan_repo(checkout)
        env = dict(os.environ)
        env.pop("AB_RUN_DIR", None)
        env.pop("AB_ACTIVITY_DIR", None)
        try:
            proc = subprocess.run(command, cwd=checkout, stdin=subprocess.DEVNULL,
                                  start_new_session=True, env=env)
            code = proc.returncode
        except OSError as err:
            code = None
            reasons.append("command could not start: %s" % err)
        hits = hits + runtime_writes(checkout)
        return code, hits, reasons
    finally:
        git(repo, "worktree", "remove", "--force", checkout)
        git(repo, "worktree", "prune")
        shutil.rmtree(tmp, ignore_errors=True)


def cmd_suite(args):
    if not os.path.isdir(args.run):
        sys.stderr.write("--run is not a folder: %s\n" % args.run)
        return _records.USAGE
    if not os.path.isdir(args.repo):
        sys.stderr.write("--repo is not a folder: %s\n" % args.repo)
        return _records.USAGE
    command = list(args.command or [])
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        sys.stderr.write("--command needs at least one word\n")
        return _records.USAGE
    res = git(args.repo, "rev-parse", "--verify", "--quiet", args.commit + "^{commit}")
    sha = res.stdout.strip()
    if res.returncode != 0 or not sha:
        sys.stderr.write("cannot resolve commit: %s\n" % args.commit)
        return _records.USAGE

    started = _records.now_iso()
    sandboxed = "CODEX_SANDBOX" in os.environ
    if sandboxed:
        code, hits = None, []
        reasons = ["CODEX_SANDBOX is set: the suite must run outside any sandbox; nothing ran"]
    else:
        code, hits, reasons = run_in_checkout(args.repo, sha, command)
    ended = _records.now_iso()

    if code is not None and code != 0:
        reasons.append("command exited %d" % code)
    if hits:
        reasons.append("%d evidence hit(s): tests must not read or write evidence/" % len(hits))
    verdict = "pass" if (not sandboxed and code == 0 and not hits) else "fail"
    record = _records.record_header(_records.SKILL_VERSION, {}, {"commit": sha})
    record.update({
        "verdict": verdict, "reasons": reasons, "commit": sha, "command": command,
        "sandboxed": sandboxed, "exit": code, "started_at": started, "ended_at": ended,
        "evidence_hits": hits,
    })
    os.makedirs(os.path.join(args.run, "records"), exist_ok=True)
    _records.write_record(os.path.join(args.run, RECORD), record)
    log.append(args.run, "integration.suite", {"commit": sha, "exit": code, "verdict": verdict})
    for hit in hits:
        print(hit)
    print("integration suite at %s: %s" % (sha, verdict))
    return _records.OK if verdict == "pass" else _records.FAILED


def main(argv=None):
    parser = argparse.ArgumentParser(prog="integrate.py", description="Agent Build integration step.")
    sub = parser.add_subparsers(dest="cmd")
    p_suite = sub.add_parser("suite", help="run the full suite on a clean checkout of a commit")
    p_suite.add_argument("--run", required=True)
    p_suite.add_argument("--repo", required=True)
    p_suite.add_argument("--commit", required=True)
    p_suite.add_argument("--command", nargs=argparse.REMAINDER, required=True)
    p_check = sub.add_parser("check-evidence", help="scan tracked test files for evidence/ use")
    p_check.add_argument("--repo", required=True)
    args = parser.parse_args(argv)
    if args.cmd == "suite":
        return cmd_suite(args)
    if args.cmd == "check-evidence":
        return cmd_check_evidence(args)
    parser.print_usage(sys.stderr)
    return _records.USAGE


if __name__ == "__main__":
    sys.exit(main())
