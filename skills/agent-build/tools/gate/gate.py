#!/usr/bin/env python3
"""Completion gate: check an agent's "done" against git and a fresh done-check run.

Report only. The gate never changes the checked repo, never retries, merges or pushes. It writes
its own logs under the log root: $GATE_LOG_ROOT when set, else --log-root DIR, else ./run/logs/gate/
under the current folder. The merge level of the source gate is not shipped: it needs gh and the
GitHub API, which a customer build may not have; merge state is checked by people and CI.
"""
import argparse
import json
import os
import posixpath
import re
import signal
import subprocess
import sys
import time
import uuid

NEW_REPO_COMMIT = "first commit of a new repo"
REQUIRED_HEADINGS = ["Unit", "Outcome", "North star", "Base", "Owns", "Must not edit", "Inputs (read-only)",
                     "Acceptance criteria", "Done-check", "Tested against", "Time box", "Permissions",
                     "Output and log paths", "Checkpoint rule", "Review set", "Return shape"]
RETURN_KEYS = ["status", "commit", "done_check_exit", "done_check_log", "criteria_met", "criteria_not_met"]
RETURN_STATUSES = {"done", "partial", "blocked", "stopped"}
LOCAL_CHECKS = ["committed", "right_base", "in_lane", "must_not_edit", "done_check"]
MERGE_NOT_SHIPPED = ("merge level not shipped: it needs gh and the GitHub API, which a customer build may not have; "
                     "merge state is checked by people and CI")


class InputError(Exception):
    """Unreadable or malformed input: the gate reports unknown and exits 2."""


# ---------------------------------------------------------------- spec parsing

def section_occurrences(text):
    """Every (heading, lines) occurrence in order; a repeated heading keeps each occurrence."""
    occ = []
    for line in text.splitlines():
        m = re.match(r"^##\s+(.+?)\s*$", line)
        if m:
            occ.append((m.group(1), []))
        elif occ:
            occ[-1][1].append(line)
    return occ


def split_sections(text):
    return dict(section_occurrences(text))  # the last occurrence of a repeated heading


def bullet_items(lines):
    return [l.strip()[2:].strip() for l in lines if l.strip().startswith("- ")]


def bullet_fields(lines):
    """Labelled items (Base, Done-check), with any supported list marker."""
    fields = {}
    for item in list_items(lines):
        body = re.sub(r"^%s\s+" % MARKER, "", item.split("\n", 1)[0]).strip()
        if ":" in body:
            k, v = body.split(":", 1)
            fields[k.strip()] = v.strip()
    return fields


def clean_path_entry(entry):
    """Strip backticks and a trailing parenthetical comment; return (path, ok)."""
    e = entry.replace("`", "").strip()
    e = re.sub(r"\s*\([^()]*\)\s*$", "", e).strip()
    ok = e.startswith("/") and "*" not in e.rstrip("*").rstrip("/") and ("*" not in e or e.endswith("/**"))
    return e, ok


# A list item starts, unindented, with -, * or + and a space, or digits and . or ) and a space.
MARKER = r"(?:[-*+]|\d+[.)])"

# Ruling (b): <...> is an unresolved placeholder only in a field value.
FIELD_PLACEHOLDER = re.compile(r"<[^<>\n]+>")
# 13 Oct 2026: in Outcome and North star, a backticked path template such as `<run>/stages/x.json` names a
# run-time folder the unit's code writes to; it is literal text, not an unfilled field (U7, U12 in run 0453).
PROSE_FIELDS = ("Outcome", "North star")
CODE_SPAN = re.compile(r"`[^`\n]+`")
PATH_TEMPLATE = re.compile(r"<[A-Za-z][\w.-]*>(?=/)|(?<=/)<[A-Za-z][\w.-]*>")


def strip_path_templates(where, value):
    """Remove path templates inside code spans of Outcome and North star; other placeholders stay."""
    if where not in PROSE_FIELDS:
        return value
    return CODE_SPAN.sub(lambda m: PATH_TEMPLATE.sub("", m.group(0)), value)
NEEDS_INFO = re.compile(r"needs more information")
FIELD_LABELS = ["Repo or folder", "Branch", "Commit", "Trunk state at launch", "Command", "Working directory",
                "Expected exit code", "Timeout", "Total", "Progress note at", "Cut order", "May write",
                "Tools allowed", "Tools denied", "Headless flags", "Deliverables", "Log", "Launcher log"]
FIELD_LABEL = re.compile(r"^(?:%s)(?![\w])" % "|".join(re.escape(l) for l in FIELD_LABELS))


def list_items(lines):
    """Unindented list items with their continuation lines, up to the next list item."""
    items, cur = [], None
    for line in lines:
        if re.match(r"^%s\s" % MARKER, line):
            cur = [line]
            items.append(cur)
        elif re.match(r"^\s+%s\s" % MARKER, line) or line.startswith("#"):
            cur = None  # an indented sub-item is prose, and a heading ends the field value
        elif cur is not None:
            cur.append(line)  # continuation, blank lines included, up to the next list item or heading
    return ["\n".join(i) for i in items]


def field_values(sections):
    """(where, value) for every field value of the spec, as defined for ruling (b)."""
    out = []
    for heading in ("Unit", "Outcome", "North star"):
        out.append((heading, "\n".join(sections.get(heading, []))))
    for heading in ("Owns", "Must not edit"):
        for item in list_items(sections.get(heading, [])):
            out.append((heading, item))
    for heading, lines in sections.items():
        if heading in ("Unit", "Outcome", "North star", "Owns", "Must not edit"):
            continue
        for item in list_items(lines):
            if heading == "Acceptance criteria":
                continue  # criterion text is prose (ruling b)
            body = re.sub(r"^%s\s+" % MARKER, "", item)
            if ":" in body and FIELD_LABEL.match(body.split(":", 1)[0]):
                out.append(("%s / %s" % (heading, body.split(":", 1)[0].strip()), body.split(":", 1)[1]))
    return out


def must_not_edit_entry_ok(item):
    """Rulings (e) and (h): one absolute path, optionally ending in /**; one surrounding pair of backticks allowed.
    Used for Owns and Must not edit alike."""
    e = item.strip()
    if len(e) >= 2 and e.startswith("`") and e.endswith("`") and e.count("`") == 2:
        e = e[1:-1]
    if not e.startswith("/") or "`" in e or "\n" in e:
        return False
    if any(ch in e for ch in ",()"):
        return False
    core = e[:-3] if e.endswith("/**") else e
    return not any(ch in core for ch in "*?[]")  # glob syntax other than a final /** is a format error


def normalise_path(p):
    """POSIX normalisation (. and .. resolved, repeated / collapsed), keeping a final /**.
    None when a .. segment climbs above /."""
    glob = p.endswith("/**")
    core = p[:-3] if glob else p
    depth = 0
    for seg in core.split("/"):
        if seg == "..":
            depth -= 1
            if depth < 0:
                return None
        elif seg not in ("", "."):
            depth += 1
    core = posixpath.normpath(re.sub(r"/+", "/", core or "/"))
    return core.rstrip("/") + "/**" if glob else core


def collapse_path(p):
    """The slash-collapsing POSIX normalisation of normalise_path, for Base and relocation operands."""
    return posixpath.normpath(re.sub(r"/+", "/", p or "/"))


def path_entries(heading, lines):
    """Every entry of Owns or Must not edit (ruling h): only unindented one-line `- ` items, each one
    absolute path. Returns (normalised paths, format errors); every error is listed, not only the first."""
    paths, errors = [], []
    items = list_items(lines)
    for item in items:
        body = item.rstrip("\n")
        if not body.startswith("- ") or "\n" in body.strip():
            errors.append("format error: %s entry uses unsupported list syntax (only one-line `- ` items): %s"
                          % (heading, body.strip()))
            continue
        e = body[2:].strip()
        if not must_not_edit_entry_ok(e):
            errors.append("format error: %s entry is not one absolute path (ruling h): %s" % (heading, e))
            continue
        n = normalise_path(e[1:-1] if e.startswith("`") else e)
        if n is None:
            errors.append("format error: %s entry climbs above / with ..: %s" % (heading, e))
            continue
        paths.append(n)
    for line in lines:
        if line.strip() and not any(line in i.splitlines() for i in items):
            errors.append("format error: %s line is not a `- ` list item: %s" % (heading, line.strip()))
    return paths, errors


# Ruling (j): the checks each field stops when it has a format error. Base, Done-check and the headings
# leave the spec unreadable as a whole (every check not run).
FIELD_CHECKS = {"Owns": ["in_lane"], "Must not edit": ["must_not_edit"]}
WHOLE_SPEC_FIELDS = {"Base", "Done-check", "Headings"}


def parse_minutes(value):
    m = re.match(r"^\s*(\d+(?:\.\d+)?)\s*(seconds?|s|minutes?|min|m|hours?|h)\b", value or "")
    if not m:
        raise InputError("Done-check timeout not readable: %r" % value)
    n, unit = float(m.group(1)), m.group(2)
    if unit.startswith("s"):
        return n
    if unit.startswith("h"):
        return n * 3600
    return n * 60


def parse_spec(path):
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except OSError as e:
        raise InputError("spec not readable: %s" % e)
    headings = [m.group(1) for m in re.finditer(r"^##\s+(.+?)\s*$", text, re.M)]
    errors = []  # (field, message)
    if headings != REQUIRED_HEADINGS:
        # a heading error stops every check, but the identifiable sections are still validated so every error is listed
        missing = [h for h in REQUIRED_HEADINGS if h not in headings]
        extra = [h for h in headings if h not in REQUIRED_HEADINGS]
        errors.append(("Headings", "spec headings are not exactly the shared list in order (missing: %s; unexpected: %s; found: %s)"
                       % (missing, extra, headings)))
    sections = split_sections(text)
    for h in REQUIRED_HEADINGS:
        sections.setdefault(h, [])
    occurrences = section_occurrences(text)  # every occurrence of a repeated heading is validated
    for heading, lines in occurrences:
        if not any(l.strip() for l in lines):
            errors.append((heading, "spec section is empty: %s" % heading))
    for where, value in [fv for h, lines in occurrences for fv in field_values({h: lines}) if fv[0].split(" / ")[0] == h]:
        if FIELD_PLACEHOLDER.search(strip_path_templates(where, value)) or NEEDS_INFO.search(value):
            errors.append((where.split(" / ")[0], "unresolved placeholder in %s: %s" % (where, value.strip())))
    base = bullet_fields(sections["Base"])
    done = bullet_fields(sections["Done-check"])
    for key, src, field in [("Repo or folder", base, "Base"), ("Branch", base, "Base"), ("Commit", base, "Base"),
                            ("Command", done, "Done-check"), ("Working directory", done, "Done-check"),
                            ("Expected exit code", done, "Done-check"), ("Timeout", done, "Done-check")]:
        if not src.get(key):
            errors.append((field, "spec field missing: %s" % key))
    owns, owns_errors = path_entries("Owns", sections["Owns"])
    mne, mne_errors = path_entries("Must not edit", sections["Must not edit"])
    for heading, lines in occurrences:  # earlier occurrences of a repeated Owns or Must not edit heading
        if heading in ("Owns", "Must not edit") and lines is not dict(occurrences)[heading]:
            (owns_errors if heading == "Owns" else mne_errors).extend(path_entries(heading, lines)[1])
    errors += [("Owns", e) for e in owns_errors] + [("Must not edit", e) for e in mne_errors]
    expected = timeout_s = None
    if done.get("Expected exit code"):
        try:
            expected = int(done["Expected exit code"].strip("` "))
        except ValueError:
            errors.append(("Done-check", "Expected exit code not an integer: %r" % done["Expected exit code"]))
    if done.get("Timeout"):
        try:
            timeout_s = parse_minutes(done["Timeout"])
        except InputError as e:
            errors.append(("Done-check", str(e)))
    whole = any(f in WHOLE_SPEC_FIELDS for f, _ in errors)
    format_errors = [{"field": f, "error": m,
                      "stops": list(LOCAL_CHECKS) if whole else FIELD_CHECKS.get(f, [])} for f, m in errors]
    if whole:
        err = InputError("; ".join(m for _, m in errors))
        err.format_errors = format_errors
        raise err
    cmd = done["Command"].strip()
    return {
        "unit": " ".join(l.strip() for l in sections["Unit"] if l.strip()),
        "repo": collapse_path(base["Repo or folder"].strip("` ")),
        "branch": base["Branch"].strip("` "),
        "base_commit": base["Commit"].strip("` "),
        "owns": owns, "must_not_edit": mne,
        "format_errors": format_errors,
        "command": cmd[1:-1] if cmd.startswith("`") and cmd.endswith("`") else cmd,
        "workdir": collapse_path(done["Working directory"].strip("` ")),
        "expected_exit": expected,
        "timeout_s": timeout_s,
    }


def parse_return(path):
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.read().splitlines()
    except OSError as e:
        raise InputError("RETURN file not readable: %s" % e)
    if len(lines) < 6:
        raise InputError("RETURN header has fewer than six lines")
    claims = {}
    for i, key in enumerate(RETURN_KEYS):
        m = re.match(r"^%s:\s*(.*?)\s*$" % key, lines[i])
        if not m or not m.group(1):
            raise InputError("RETURN header line %d is not '%s: <value>': %r" % (i + 1, key, lines[i]))
        claims[key] = m.group(1)
    if claims["status"] not in RETURN_STATUSES:
        raise InputError("RETURN status not one of %s: %r" % (sorted(RETURN_STATUSES), claims["status"]))
    if not re.fullmatch(r"[0-9a-f]{40}", claims["commit"]):
        raise InputError("RETURN commit is not a full 40-character hash: %r" % claims["commit"])
    dl = claims["done_check_log"]
    if dl != "none" and (dl.startswith("/") or ".." in dl.split("/") or re.search(r"\s", dl)):
        raise InputError("RETURN done_check_log not a path inside the repo or none: %r" % dl)
    if claims["done_check_exit"] != "not_run" and not re.fullmatch(r"-?\d+", claims["done_check_exit"]):
        raise InputError("RETURN done_check_exit not an integer or not_run: %r" % claims["done_check_exit"])
    for key in ("criteria_met", "criteria_not_met"):
        if claims[key] != "none" and not re.fullmatch(r"\d+(\s*,\s*\d+)*", claims[key]):
            raise InputError("RETURN %s not a list of numbers or none: %r" % (key, claims[key]))
    return claims


# ---------------------------------------------------------------- running commands

class Runner:
    """Runs read-only commands and appends each one to the check's log file."""

    def __init__(self, cwd, log_dir):
        self.cwd, self.log_dir = cwd, log_dir

    def log_path(self, check):
        return os.path.join(self.log_dir, "%s.log" % check)

    def run(self, check, argv, cwd=None):
        try:
            p = subprocess.run(argv, cwd=cwd or self.cwd, capture_output=True, text=True, timeout=120)
            code, out, err = p.returncode, p.stdout, p.stderr
        except (OSError, subprocess.SubprocessError) as e:
            code, out, err = None, "", "failed to start: %s" % e
        with open(self.log_path(check), "a", encoding="utf-8") as f:
            f.write("$ %s\n[exit %s]\n--- stdout\n%s\n--- stderr\n%s\n\n" % (" ".join(argv), code, out, err))
        return code, out, err

    def git(self, check, *args):
        return self.run(check, ["git"] + list(args))

def check(name, verified, evidence, required=True):
    verdict = {True: "pass", False: "fail", None: "unknown"}[verified]
    return {"name": name, "required": required, "verified": verified, "verdict": verdict, "evidence": evidence}


def ev(command, exit_code, commit, source, log_path, **extra):
    d = {"command": command, "exit_code": exit_code, "commit": commit, "source": source, "log_path": log_path}
    d.update(extra)
    return d


# ---------------------------------------------------------------- path rules

def map_path(p, spec, worktree):
    repo = spec["repo"]
    if worktree and (p == repo or p.startswith(repo.rstrip("/") + "/")):
        # a root Base `/` is a prefix of every absolute path; join, then collapse the doubled `/`
        return collapse_path(worktree + "/" + p[len(repo):])
    return p


def under(path, entry):
    if entry.endswith("/**"):
        return path.startswith(entry[:-2])
    return path == entry or path.startswith(entry.rstrip("/") + "/")


def changed_paths(r, base, head):
    """Every path changed in base..head, per commit, renames counted on both sides; merge commits are
    diffed against each of their parents (--diff-merges=separate)."""
    cmd = ["log", "--format=", "--name-status", "-M", "-z", "--diff-merges=separate", "%s..%s" % (base, head)]
    code, out, err = r.git("changed_paths", *cmd)
    if code != 0:
        return None, "git " + " ".join(cmd), code
    tokens = [t for t in out.split("\0")]
    paths, i = set(), 0
    while i < len(tokens):
        status = tokens[i].strip()
        if not status:
            i += 1
            continue
        n = 2 if status[0] in "RC" else 1
        for p in tokens[i + 1:i + 1 + n]:
            paths.add(p)
        i += 1 + n
    return sorted(paths), "git " + " ".join(cmd), code


# ---------------------------------------------------------------- local checks

def git_state(r, label):
    hc, head, _ = r.git(label, "rev-parse", "HEAD")
    sc, status, _ = r.git(label, "status", "--porcelain", "--untracked-files=all")
    return (head.strip() if hc == 0 and head.strip() else None), (status if sc == 0 else None)


def check_committed(head, before_status, log):
    """Check 1, decided from the status snapshot taken immediately before the done-check runs."""
    cmd = "git status --porcelain --untracked-files=all"
    if before_status is None:
        return check("committed", None, ev(cmd, None, head, "git", log,
                                           reason="git status not readable immediately before the done-check"))
    dirty = [l for l in before_status.splitlines() if l.strip()]
    return check("committed", not dirty, ev(cmd, 0, head, "git", log, uncommitted=dirty,
                                            snapshot="immediately before the done-check"))


def group_alive(pgid):
    try:
        os.killpg(pgid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def kill_group(p):
    """SIGTERM, a grace period, then SIGKILL to the group whether or not the leader has exited,
    so a descendant that ignores SIGTERM is still killed."""
    try:
        os.killpg(p.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    grace = time.monotonic() + 2
    while time.monotonic() < grace and group_alive(p.pid):
        if p.poll() is None:
            try:
                p.wait(timeout=0.05)
            except subprocess.TimeoutExpired:
                pass
        else:
            time.sleep(0.05)
    try:
        os.killpg(p.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    p.wait()


def supervise(p, timeout_s):
    """Wait until every member of the done-check's process group has exited, or the timeout fires.
    Returns (leader exit code, timed_out). Descendants that start a new session are not covered."""
    deadline = time.monotonic() + timeout_s
    code = None
    while True:
        left = deadline - time.monotonic()
        if code is None:
            try:
                code = p.wait(timeout=max(0, min(left, 0.2)))
            except subprocess.TimeoutExpired:
                pass
        if code is not None and not group_alive(p.pid):
            return code, False
        if time.monotonic() >= deadline:
            kill_group(p)
            return code, True
        if code is not None:
            time.sleep(min(0.05, max(0, deadline - time.monotonic())))


def check_right_base(r, spec, base, head, warnings, unit_from=None):
    log = r.log_path("right_base")
    cmd = "git merge-base --is-ancestor %s HEAD" % base
    code, _, err = r.git("right_base", "merge-base", "--is-ancestor", base, "HEAD")
    verified = {0: True, 1: False}.get(code)
    extra = {} if verified is not None else {"reason": "ancestry not readable: %s" % err.strip()}
    if unit_from:
        # 13 Oct 2026: the orchestrator moved the unit's base (reset or rebase onto a newer commit); judge the
        # unit's own range, which starts at --from. The spec base's ancestry is kept as evidence and a warning.
        extra["spec_base_is_ancestor"] = verified
        if verified is not True:
            warnings.append("right_base: judged from --from %s; spec base %s is not an ancestor of HEAD" % (unit_from, base))
        cmd = "git merge-base --is-ancestor %s HEAD" % unit_from
        code, _, err = r.git("right_base", "merge-base", "--is-ancestor", unit_from, "HEAD")
        verified = {0: True, 1: False}.get(code)
        extra["unit_from"] = unit_from
        if verified is None:
            extra["reason"] = "ancestry not readable: %s" % err.strip()
    branch, behind = spec["branch"], None
    ref = None
    for cand in ("refs/heads/" + branch, "refs/remotes/origin/" + branch):
        c, _, _ = r.git("right_base", "rev-parse", "--verify", "--quiet", cand)
        if c == 0:
            ref = cand
            break
    if ref is None:
        warnings.append("right_base: branch %r not found locally; trailing count not read" % branch)
    else:
        c, out, _ = r.git("right_base", "rev-list", "--count", "HEAD..%s" % ref)
        if c == 0:
            behind = int(out.strip())
            if behind > 0:
                warnings.append("stale base: HEAD trails %s by %d commit(s) (git rev-list --count HEAD..%s)" % (branch, behind, ref))
        else:
            warnings.append("right_base: could not count HEAD..%s" % ref)
    return check("right_base", verified, ev(cmd, code, head, "git", log, base_commit=base,
                                            branch_ref=ref, head_trails_branch_by=behind, **extra))


def check_lanes(r, spec, base, head, worktree, root):
    paths, cmd, code = changed_paths(r, base, head)
    log = r.log_path("changed_paths")
    if paths is None:
        reason = "changed paths not readable"
        return (check("in_lane", None, ev(cmd, code, head, "git", log, reason=reason)),
                check("must_not_edit", None, ev(cmd, code, head, "git", log, reason=reason)))
    owns = [map_path(p, spec, worktree) for p in spec["owns"]]
    mne_bad = any(fe["field"] == "Must not edit" for fe in spec["format_errors"])
    mne = [] if mne_bad else [map_path(p, spec, worktree) for p in spec["must_not_edit"]]
    absolute = [collapse_path(root + "/" + p) for p in paths]
    forbidden = [p for p in absolute if any(under(p, e) for e in mne)]
    outside = [p for p in absolute if not any(under(p, e) for e in owns) or p in forbidden]
    common = dict(changed=len(absolute))
    c3 = check("in_lane", not outside, ev(cmd, code, head, "git", log, owns=owns, outside_owns=outside, **common))
    if forbidden:
        c4 = check("must_not_edit", False, ev(cmd, code, head, "git", log, must_not_edit=mne, touched=forbidden, **common))
    else:
        c4 = check("must_not_edit", True, ev(cmd, code, head, "git", log, must_not_edit=mne, touched=[], **common))
    return c3, c4


PS_ARGV = ["/bin/ps", "-A", "-o", "pid=,pgid=,stat=,command="]


def process_snapshot(log_dir, pgid):
    """Taken once, right after supervision ends: every process still in the done-check's process group.
    Records the exact argument list, the matching expression as applied, and the raw output. A snapshot
    that cannot be taken is recorded with its error; it never changes the done_check result."""
    path = os.path.join(log_dir, "process-snapshot.txt")
    match = "line.split()[1] == %r" % str(pgid)
    raw, survivors, error = "", [], None
    try:
        p = subprocess.run(PS_ARGV, capture_output=True, text=True, timeout=30)
        raw = p.stdout
        if p.returncode != 0:
            error = "ps exit %s: %s" % (p.returncode, p.stderr.strip())
        survivors = [line for line in raw.splitlines() if len(line.split()) > 1 and line.split()[1] == str(pgid)]
    except (OSError, subprocess.SubprocessError) as e:
        error = "snapshot not taken: %s" % e
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write("taken: %s, right after supervision of the done-check ended\n" % time.strftime("%F %T"))
            f.write("command (argument list): %s\n" % json.dumps(PS_ARGV))
            f.write("match (applied to each output line): %s  # pgid column equals the done-check's process group id %d\n"
                    % (match, pgid))
            f.write("error: %s\n" % error)
            f.write("survivors (%d):\n%s\n" % (len(survivors), "\n".join(survivors)))
            f.write("--- raw output\n%s" % raw)
    except OSError as e:
        return {"process_snapshot": None, "process_snapshot_error": "snapshot not written: %s" % e}
    return {"process_snapshot": path, "process_snapshot_survivors": survivors, "process_snapshot_error": error}


def run_done_check(r, spec, worktree, head):
    """Returns (check 5, fresh exit code, pre-run git status or None)."""
    log = os.path.join(r.log_dir, "done-check.log")
    workdir = map_path(spec["workdir"], spec, worktree)
    cmd = spec["command"]
    shown = "/bin/bash -lc %s  (cwd %s, timeout %ss)" % (json.dumps(cmd), workdir, int(spec["timeout_s"]))
    before = git_state(r, "done_check_state")
    start = time.monotonic()
    timed_out, code, reason = False, None, None
    with open(log, "w", encoding="utf-8") as f:
        f.write("$ %s\n" % shown)
        f.flush()
        try:
            p = subprocess.Popen(["/bin/bash", "-lc", cmd], cwd=workdir, stdout=f, stderr=subprocess.STDOUT,
                                 stdin=subprocess.DEVNULL, start_new_session=True)
        except OSError as e:
            reason = "done-check failed to start: %s" % e
            p = None
        if p is not None:
            code, timed_out = supervise(p, spec["timeout_s"])
        f.write("\n[gate] exit=%s timed_out=%s seconds=%.1f\n" % (code, timed_out, time.monotonic() - start))
    snapshot = process_snapshot(r.log_dir, p.pid) if p is not None else None
    after = git_state(r, "done_check_state")
    extra = dict(expected_exit=spec["expected_exit"], timed_out=timed_out, seconds=round(time.monotonic() - start, 1),
                 workdir=workdir)
    if snapshot:
        extra.update(snapshot)
    if reason:
        return check("done_check", None, ev(shown, None, head, "fresh run", log, reason=reason, **extra)), None, before[1]
    state = {"before": {"head": before[0], "status": before[1]}, "after": {"head": after[0], "status": after[1]}}
    unreadable = None in before or None in after
    if unreadable:
        state["state_note"] = "HEAD or git status not readable before or after the done-check"
    # A known failure stays a fail whatever the git state; the state is kept as evidence.
    if timed_out:
        return check("done_check", False, ev(shown, None, head, "fresh run", log, reason="timed out",
                                             **state, **extra)), None, before[1]
    if code != spec["expected_exit"]:
        return check("done_check", False, ev(shown, code, head, "fresh run", log,
                                             reason="exit %s, expected %s" % (code, spec["expected_exit"]),
                                             **state, **extra)), code, before[1]
    if unreadable:
        return check("done_check", None, ev(shown, code, head, "fresh run", log,
                                            reason=state["state_note"], **state, **extra)), code, before[1]
    if before[0] != head:
        return check("done_check", False, ev(shown, code, head, "fresh run", log,
                                             reason="HEAD before the done-check is not the checked commit",
                                             **state, **extra)), code, before[1]
    if before[0] != after[0]:
        return check("done_check", False, ev(shown, code, head, "fresh run", log,
                                             reason="HEAD changed during the done-check",
                                             **state, **extra)), code, before[1]
    # 13 Oct 2026: judged on exit code and HEAD. A done-check that writes files (evidence, screenshots) changes
    # git status; that is recorded, not failed. Done-checks should write to untracked paths (outside the
    # worktree or git-ignored) so the next gate run's committed check stays clean.
    if before[1] != after[1]:
        state["status_changed_during"] = sorted(set(after[1].splitlines()) ^ set(before[1].splitlines()))
    return check("done_check", True, ev(shown, code, head, "fresh run", log, **state, **extra)), code, before[1]


def not_run(name, reason):
    return check(name, None, ev(None, None, None, None, None, reason=reason))


# ---------------------------------------------------------------- claims

RETURN_ONLY_PATHS = {"RETURN.md", "done-check.log"}


def commit_claim(r, c, head):
    """Ruling (a): the claimed commit matches when it is HEAD, or an ancestor of HEAD with every commit in
    c..HEAD (second parents included; a merge diffed against each parent) changing only RETURN.md and/or
    done-check.log at the repo top level. Unreadable ancestry or paths give match None."""
    field = {"claimed": c, "verified": head, "match": head == c}
    if field["match"]:
        return field
    code, _, err = r.git("claims", "merge-base", "--is-ancestor", c, head)
    if code == 1:
        field.update(match=False, note="claimed commit is not an ancestor of HEAD")
        return field
    if code != 0:
        field.update(match=None, note="ancestry not readable: %s" % err.strip())
        return field
    code, out, err = r.git("claims", "rev-list", "%s..%s" % (c, head))
    if code != 0:
        field.update(match=None, note="git rev-list %s..HEAD not readable: %s" % (c, err.strip()))
        return field
    later, offending = out.split(), []
    for sha in later:
        code, files, err = r.git("claims", "diff-tree", "--no-commit-id", "--name-only", "-r", "-m", "--root", "-z", sha)
        if code != 0:
            field.update(match=None, note="paths of %s not readable: %s" % (sha, err.strip()))
            return field
        other = sorted(set(p for p in files.split("\0") if p) - RETURN_ONLY_PATHS)
        if other:
            offending.append({"commit": sha, "paths": other})
    field["later_commits"] = later
    if offending:
        field.update(match=False, offending=offending,
                     note="commits after the claimed commit change more than RETURN.md/done-check.log: %s"
                          % "; ".join("%s: %s" % (o["commit"], ", ".join(o["paths"])) for o in offending))
    else:
        field.update(match=True, note="HEAD is later than the claimed commit by %d commit(s) touching only "
                                      "RETURN.md and/or done-check.log (ruling a)" % len(later))
    return field


def compare_claims(claims, r, head, fresh_exit):
    out = {"source": "RETURN header (claims)", "fields": {}, "mismatches": [],
           "unverified": {k: claims[k] for k in ("status", "done_check_log", "criteria_met", "criteria_not_met")}}
    out["fields"]["commit"] = commit_claim(r, claims["commit"], head)
    d = claims["done_check_exit"]
    if fresh_exit is None:
        match = None
    elif d == "not_run":
        match = False
    else:
        match = int(d) == fresh_exit
    out["fields"]["done_check_exit"] = {"claimed": d, "verified": fresh_exit, "match": match}
    for k, f in out["fields"].items():
        if f["match"] is not True:
            out["mismatches"].append(k if f["match"] is False else "%s (not verifiable)" % k)
    return out


# ---------------------------------------------------------------- main

def overall(checks):
    vs = [c["verified"] for c in checks if c["required"]]
    if any(v is False for v in vs):
        return "fail"
    if any(v is None for v in vs) or not vs:
        return "unknown"
    return "pass"


def table(report):
    rows = ["| # | check | verified | verdict | detail |", "|---|---|---|---|---|"]
    for i, c in enumerate(report["checks"], 1):
        e = c["evidence"]
        detail = e.get("reason") or ""
        if not detail:
            for k in ("uncommitted", "outside_owns", "touched"):
                if e.get(k):
                    detail = "%s: %s" % (k, json.dumps(e[k])[:120])
        if c["name"] == "done_check" and "expected_exit" in e:
            if detail.startswith("exit "):
                detail = ""
            detail = ("exit %s, expected %s. %s" % (e.get("exit_code"), e.get("expected_exit"), detail)).strip(". ")
        rows.append("| %d | %s | %s | %s | %s |" % (i, c["name"], json.dumps(c["verified"]), c["verdict"],
                                                    detail.replace("|", "\\|").replace("\n", " ")))
    rows.append("")
    rows.append("verdict: **%s** at level %s, commit %s" % (report["verdict"], report["level"], report["checked_commit"]))
    for fe in report.get("format_errors") or []:
        rows.append("format error in %s (stops: %s): %s" % (fe["field"], ", ".join(fe["stops"]) or "no check", fe["error"]))
    for w in report["warnings"]:
        rows.append("warning: %s" % w)
    if report["claims"]:
        for k, f in report["claims"].get("fields", {}).items():
            rows.append("claim %s: claimed %s, verified %s, match %s" % (k, f["claimed"], f["verified"], f["match"]))
    return "\n".join(rows)


def emit(report, log_dir):
    text = json.dumps(report, indent=2)
    if log_dir:
        with open(os.path.join(log_dir, "report.json"), "w", encoding="utf-8") as f:
            f.write(text + "\n")
    sys.stdout.write(text + "\n")
    sys.stderr.write(table(report) + "\n")
    return {"pass": 0, "fail": 1, "unknown": 2}[report["verdict"]]


def input_error_report(level, reason, log_dir, head=None, format_errors=None):
    names = LOCAL_CHECKS
    return {"level": level, "checked_commit": head, "verdict": "unknown",
            "checks": [not_run(n, "not run: input error: %s" % reason) for n in names],
            "claims": None, "format_errors": format_errors or [],
            "warnings": ["input error: %s" % reason, "log dir: %s" % log_dir]}


def apply_format_errors(checks, format_errors):
    """Ruling (j): a check that reads a malformed field is reported as not run, naming the error(s)."""
    stopped = {}
    for fe in format_errors:
        for name in fe["stops"]:
            stopped.setdefault(name, []).append(fe["error"])
    return [not_run(c["name"], "not run: %s" % "; ".join(stopped[c["name"]])) if c["name"] in stopped else c
            for c in checks]


def log_root(args):
    """$GATE_LOG_ROOT when set (even to empty: the current folder), else --log-root DIR, else ./run/logs/gate/ under the current folder."""
    if "GATE_LOG_ROOT" in os.environ:
        return os.environ["GATE_LOG_ROOT"]
    if args.log_root:
        return args.log_root
    return os.path.join(os.getcwd(), "run", "logs", "gate")


def cmd_check(args):
    run_id = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
    log_dir = os.path.join(log_root(args), run_id)
    try:
        try:
            os.makedirs(log_dir, exist_ok=True)
        except OSError as e:
            log_dir = None
            raise InputError("log directory not writable: %s" % e)
        spec = parse_spec(args.spec)
        claims = parse_return(args.return_file) if args.return_file else None
        worktree = collapse_path(os.path.abspath(args.worktree)) if args.worktree else None
        root = worktree or spec["repo"]
        if not os.path.isdir(root):
            raise InputError("worktree missing: %s" % root)
        r = Runner(root, log_dir)
        code, top, err = r.git("setup", "rev-parse", "--show-toplevel")
        if code != 0:
            raise InputError("not a git worktree: %s (%s)" % (root, err.strip()))
        code, head, _ = r.git("setup", "rev-parse", "HEAD")
        if code != 0:
            raise InputError("HEAD not readable in %s" % root)
        head = head.strip()
        base = spec["base_commit"]
        if base.lower().startswith(NEW_REPO_COMMIT):
            code, out, _ = r.git("setup", "rev-list", "--max-parents=0", "HEAD")
            roots = out.split()
            if code != 0 or len(roots) != 1:
                raise InputError("base is 'first commit of a new repo' but root commit not unique: %s" % roots)
            base = roots[0]
        else:
            code, out, _ = r.git("setup", "rev-parse", "--verify", "--quiet", base + "^{commit}")
            if code != 0:
                raise InputError("base commit not found: %s" % base)
            base = out.strip()
        unit_from = None
        if getattr(args, "unit_from", None):
            code, out, _ = r.git("setup", "rev-parse", "--verify", "--quiet", args.unit_from + "^{commit}")
            if code != 0:
                raise InputError("--from commit not found: %s" % args.unit_from)
            unit_from = out.strip()
    except InputError as e:
        return emit(input_error_report(args.level, str(e), log_dir,
                                       format_errors=getattr(e, "format_errors", None)), log_dir)

    warnings = ["log dir: %s" % log_dir]
    if os.path.realpath(top.strip()) != os.path.realpath(root):
        warnings.append("worktree %s is inside repo top level %s; changed paths are taken relative to the top level" % (root, top.strip()))
    root_for_paths = top.strip() if os.path.realpath(top.strip()) != os.path.realpath(root) else root
    c2 = check_right_base(r, spec, base, head, warnings, unit_from)
    c3, c4 = check_lanes(r, spec, unit_from or base, head, worktree, root_for_paths)
    if unit_from:
        warnings.append("in_lane and must_not_edit judged on the unit's own range %s..HEAD (--from)" % unit_from[:12])
    c5, fresh_exit, before_status = run_done_check(r, spec, worktree, head)
    if c5["evidence"].get("status_changed_during"):
        warnings.append("done-check changed git status (not a fail; write evidence to an untracked path): %s"
                        % "; ".join(c5["evidence"]["status_changed_during"][:10]))
    checks = [check_committed(head, before_status, r.log_path("done_check_state")), c2, c3, c4, c5]
    checks = apply_format_errors(checks, spec["format_errors"])

    # Ruling (j): any format error keeps the verdict unknown, never pass and never fail.
    verdict = "unknown" if spec["format_errors"] else overall(checks)
    report = {"level": "local", "checked_commit": head, "verdict": verdict, "checks": checks,
              "claims": compare_claims(claims, r, head, fresh_exit) if claims else None,
              "format_errors": spec["format_errors"],
              "warnings": [fe["error"] for fe in spec["format_errors"]] + warnings}
    return emit(report, log_dir)


def main(argv=None):
    p = argparse.ArgumentParser(prog="gate", description="Check an agent's done against the facts. Report only.")
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check", help="check a unit")
    c.add_argument("spec")
    c.add_argument("--return", dest="return_file")
    c.add_argument("--level", help=argparse.SUPPRESS)  # only to answer --level merge: not shipped
    c.add_argument("--worktree")
    c.add_argument("--log-root", dest="log_root",
                   help="folder for the gate's logs when $GATE_LOG_ROOT is not set (default: ./run/logs/gate/)")
    c.add_argument("--from", dest="unit_from",
                   help="the commit the orchestrator last moved the unit onto (after a reset, rebase or merge); "
                        "right_base, in_lane and must_not_edit are then judged on <from>..HEAD, the unit's own range")
    def usage_error(message):
        report = {"level": None, "checked_commit": None, "verdict": "unknown", "checks": [],
                  "claims": None, "warnings": ["usage error: %s" % message]}
        sys.stdout.write(json.dumps(report) + "\n")
        sys.stderr.write(table(report) + "\n")
        p.print_usage(sys.stderr)
        sys.stderr.write("gate: error: %s\n" % message)
        sys.exit(2)
    p.error = usage_error
    c.error = usage_error
    args = p.parse_args(argv)
    if args.level == "merge":
        report = {"level": "merge", "checked_commit": None, "verdict": "unknown", "checks": [],
                  "claims": None, "format_errors": [], "warnings": [MERGE_NOT_SHIPPED]}
        sys.stdout.write(json.dumps(report) + "\n")
        sys.stderr.write(table(report) + "\n")
        sys.stderr.write("gate: %s\n" % MERGE_NOT_SHIPPED)
        return 2
    if args.level is not None:
        usage_error("--level is not an option of this gate (only level local is shipped): %r" % args.level)
    args.level = "local"
    try:
        return cmd_check(args)
    except Exception as e:  # any crash is unknown, never pass; same JSON shape and table
        report = input_error_report(args.level, "gate error: %r" % e, None)
        report["warnings"][0] = "gate error: %r" % e
        report["checks"] = [not_run(n, "not run: gate error: %r" % e) for n in LOCAL_CHECKS]
        return emit(report, None)


if __name__ == "__main__":
    sys.exit(main())
