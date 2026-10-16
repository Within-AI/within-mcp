#!/usr/bin/env python3
"""Post-run hygiene notes over an Agent Design run folder (the brief's criterion 21, as ruled in handoff 0492).

Usage: postrun.py --run RUN [--out FILE] [--page-dir DIR]

Scans, each counted in the report:
  vocabulary  banned and retired terms from formats/lists/vocabulary.json, matched as whole words
  observed    skills/fast-mode-build/tools/check_observed_cited.py over upstream/ (current-state, future-state);
              an Observed claim without a citation is carried as a recorded upstream problem
  ids         rule ids, architecture step ids and snake_case tokens in the visible page text
  paths       machine-specific absolute paths and scratch paths outside the run

Post-run never blocks and never asks. Every finding is a builder-log note: the notes, aggregated per file per scan,
go into RUN/checks/postrun.json (or FILE) with one plain summary line, and into the builder log
RUN/builder/problems.jsonl. No name scan of any kind runs here; the cross-client scan is a pre-ship CI check over
plugin files only (scripts/ci_hygiene.py).

Never opened by the scans (builder logs): NOTES.md, builder/**, stages/review-b/before/** (and any before/ folder),
and in status.md everything after a `## Builder` heading (status.md is skipped whole when it has no such heading).
upstream/ is read only by the Observed check, through its own tool. --page-dir is accepted and ignored.

Writes RUN/checks/postrun.json (or FILE), RUN/builder/problems.jsonl (its own earlier post-run lines replaced) and
RUN/stages/post-run/resolutions.json, an empty record the hand-off reads.
Exit 0 whenever the run is readable (the run folder exists and state.json parses, when present); 2 the run cannot be
read or the report cannot be written. Standard library only.
"""
import argparse
import json
import os
import re
import subprocess
import sys
from html.parser import HTMLParser

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
REPO = os.path.dirname(os.path.dirname(SKILL))
VOCABULARY = os.path.join(SKILL, "formats", "lists", "vocabulary.json")
OBSERVED_TOOL = os.path.join(REPO, "skills", "fast-mode-build", "tools", "check_observed_cited.py")
REPORT_REL = "checks/postrun.json"
RESOLUTIONS_REL = "stages/post-run/resolutions.json"
BUILDER_LOG_REL = "builder/problems.jsonl"
OBSERVED_TIMEOUT = 120
FALLBACK = "A post-run check agent lists banned words, uncited Observed claims, ids and paths as builder-log notes."

# whole-word boundary shared with scripts/ci_hygiene.py: a hyphen or any other non-word character is a boundary
LEFT, RIGHT = r"(?<![A-Za-z0-9_])", r"(?![A-Za-z0-9_])"


class RunUnreadable(Exception):
    pass


# ---------------------------------------------------------------- builder logs (never opened)

def builder_log(rel):
    """True when the run-relative path is a builder log the scans never open."""
    parts = rel.split("/")
    if parts[-1] == "NOTES.md" or parts[0] == "builder" or "before" in parts[:-1]:
        return True
    return False


def status_customer_part(text):
    """status.md up to its `## Builder` heading; None (skip the file) when there is no such heading."""
    m = re.search(r"(?m)^##\s+Builder\b", text)
    return text[:m.start()] if m else None


# ---------------------------------------------------------------- vocabulary scan

def anchored(pattern):
    """A vocabulary.json pattern matched as a whole word or phrase (multiword entries already match any whitespace)."""
    return re.compile(LEFT + "(?:" + pattern + ")" + RIGHT, re.I)


def _load_vocabulary():
    with open(VOCABULARY, encoding="utf-8") as fh:
        doc = json.load(fh)
    terms = []
    for listname in ("banned", "retired"):
        for t in doc.get(listname, []):
            terms.append((listname, t["name"], anchored(t["pattern"])))
    return doc.get("schema"), terms


def _is_vocabulary_copy(text, schema):
    try:
        doc = json.loads(text)
    except ValueError:
        return False
    return isinstance(doc, dict) and doc.get("schema") == schema and "banned" in doc and "retired" in doc


def _mask_own_counts(text, terms):
    """Blank this script's own count labels (`vocabulary:<term name>`, copied into state.json and status.md by
    ad.py), so a second post-run does not note the names of its own counts. Only that exact label shape is blanked;
    the term anywhere else still counts."""
    for _listname, name, _rx in terms:
        text = text.replace("vocabulary:" + name, " " * len("vocabulary:" + name))
    return text


def _text_hits(text, terms):
    """Term hits over the whole file text, so a term wrapped across a line break is found, each with the line of its
    start; a hit inside a longer hit (a banned term inside a retired one) counts once, as the longer."""
    found = []
    for listname, name, rx in terms:
        for m in rx.finditer(text):
            found.append((m.start(), m.end(), listname, name))
    found.sort(key=lambda h: (-(h[1] - h[0]), h[0]))
    kept = []
    for h in found:
        if not any(h[0] < k[1] and k[0] < h[1] for k in kept):
            kept.append(h)
    return [(text.count("\n", 0, s) + 1, listname, name) for s, _e, listname, name in sorted(kept)]


def vocabulary_scan(paths, root=None):
    """Every banned or retired term hit in the given files (a copy of the vocabulary list is skipped)."""
    schema, terms = _load_vocabulary()
    hits = []
    for p in paths:
        rel = os.path.relpath(p, root).replace(os.sep, "/") if root else str(p)
        if root and builder_log(rel):
            continue  # a builder log inside a run is never opened
        with open(p, encoding="utf-8") as fh:
            text = fh.read()
        if _is_vocabulary_copy(text, schema):
            continue
        for i, listname, name in _text_hits(text, terms):
            hits.append({"file": rel, "line": i, "term": name, "list": listname})
    return hits


# ---------------------------------------------------------------- id-shaped text

ID_PATTERNS = (
    ("rule-id", re.compile(r"\b[A-Z]{1,5}(-[A-Z]{1,5})*-\d+\b")),
    ("step-id", re.compile(r"\bdag-[a-z0-9-]+\b")),
    ("code-token", re.compile(r"\b[a-z][a-z0-9]*(_[a-z0-9]+)+\b")),
)
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
CODE_SLOTS = ("appendix", "simulation")  # slots whose <code> text may show raw ids


class _Visible(HTMLParser):
    """Visible text chunks with their line, each marked exempt when it is <code> text inside the appendix panel or the
    simulation slot (pins H 17, extended to the simulation slot by the orchestrator ruling of 13 Oct 2026, as in
    page/diagram/page_checks.py)."""

    def __init__(self):
        HTMLParser.__init__(self, convert_charrefs=True)
        self.stack = []  # (tag, is_appendix)
        self.chunks = []

    def handle_starttag(self, tag, attrs):
        if tag in VOID:
            return
        self.stack.append((tag, any(k == "data-slot" and v in CODE_SLOTS for k, v in attrs)))

    def handle_startendtag(self, tag, attrs):
        pass

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                return

    def handle_data(self, data):
        tags = [t for t, _ in self.stack]
        if "style" in tags or "script" in tags:
            return
        appendix = any(a for _, a in self.stack)
        exempt = appendix and "code" in tags
        self.chunks.append((self.getpos()[0], data, exempt))


def id_scan(run):
    """Id-shaped text in the visible page HTML. Builder logs (page/before/** and the like) are excluded by path
    before any file is opened."""
    files = []
    page = os.path.join(run, "page")
    if os.path.isdir(page) and not os.path.islink(page):
        for root, dirs, fs in os.walk(page, followlinks=False):
            rel_root = os.path.relpath(root, run).replace(os.sep, "/")
            dirs[:] = sorted(d for d in dirs if not builder_log(rel_root + "/" + d + "/x"))
            files.extend(os.path.join(root, f) for f in sorted(fs)
                         if f.endswith(".html") and not builder_log(rel_root + "/" + f))
    top = os.path.join(run, "agent-design.html")
    if os.path.isfile(top) and not os.path.islink(top):
        files.append(top)
    problems = []
    for p in files:
        if os.path.islink(p):
            continue
        rel = os.path.relpath(p, run).replace(os.sep, "/")
        try:
            with open(p, encoding="utf-8") as fh:
                text = fh.read()
        except (OSError, UnicodeDecodeError):
            continue  # counted by the file walk
        parser = _Visible()
        parser.feed(text)
        parser.close()
        for line, data, exempt in parser.chunks:
            if exempt:
                continue
            for defect, rx in ID_PATTERNS:
                for m in rx.finditer(data):
                    problems.append({"defect": defect, "where": "%s:%d" % (rel, line),
                                     "detail": "id-shaped text in the visible page (%d characters)" % len(m.group(0))})
    return problems, len(files)


# ---------------------------------------------------------------- path scans

PATH_PATTERNS = (
    ("machine-path", re.compile(r"/Users/[^/\s\"'<>]+/")),
    ("machine-path", re.compile(r"/home/[^/\s\"'<>]+/")),
    ("machine-path", re.compile(r"[A-Za-z]:(?:\\\\|\\|/)Users(?:\\\\|\\|/)", re.I)),
    ("machine-path", re.compile(r"/sessions/[^\s\"'<>]*?/mnt/")),
    ("scratch-path", re.compile(r"/private/tmp/")),
    ("scratch-path", re.compile(r"(?<!/private)/tmp/")),
    ("scratch-path", re.compile(r"/var/folders/")),
)
PATH_LABEL = {
    0: "home folder path (/Users/<name>/)", 1: "home folder path (/home/<name>/)", 2: "Windows home folder path",
    3: "cloud session path (/sessions/.../mnt/)", 4: "scratch path (/private/tmp/)", 5: "scratch path (/tmp/)",
    6: "scratch path (/var/folders/)",
}


def path_scan(rel, text):
    problems = []
    for i, line in enumerate(text.split("\n"), start=1):
        for n, (defect, rx) in enumerate(PATH_PATTERNS):
            for _m in rx.finditer(line):
                problems.append({"defect": defect, "where": "%s:%d" % (rel, i), "detail": PATH_LABEL[n]})
    return problems


# ---------------------------------------------------------------- observed check

def observed_check(run):
    """Upstream problems from check_observed_cited.py over upstream/ (0489-B1: carried, never a post-run failure).
    A check that cannot run is itself one upstream note naming the stage."""
    upstream = os.path.join(run, "upstream")
    problems, results = [], {}
    for stage in ("current-state", "future-state"):
        where = "upstream/stages/%s" % stage
        cmd = [sys.executable, OBSERVED_TOOL, upstream, "--stage", stage, "--snapshot-dir", os.path.join(upstream, "inputs")]
        try:
            proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=OBSERVED_TIMEOUT,
                                  universal_newlines=True)
        except subprocess.TimeoutExpired:
            results[stage] = None
            problems.append({"defect": "observed-not-run", "where": where, "detail": "Observed check timed out"})
            continue
        except OSError as e:
            results[stage] = None
            problems.append({"defect": "observed-not-run", "where": where,
                             "detail": "Observed check cannot run: %s" % e.strerror})
            continue
        results[stage] = proc.returncode
        if proc.returncode == 0:
            continue
        if proc.returncode != 1:
            problems.append({"defect": "observed-not-run", "where": where,
                             "detail": "Observed check could not run (exit %d)" % proc.returncode})
            continue
        summary = {}
        try:
            summary = json.loads(proc.stdout[:proc.stdout.index("\n}") + 2]) if "\n}" in proc.stdout else {}
        except ValueError:
            summary = {}
        named = False
        for key in ("no_citation", "unknown_source", "pointer_unresolved", "source_unreadable", "files_not_checked"):
            for item in summary.get(key, []):
                named = True
                problems.append({"defect": "observed-%s" % key.replace("_", "-"), "where": where, "detail": str(item)})
        if not named:
            problems.append({"defect": "observed-failed", "where": where, "detail": "check_observed_cited.py exit 1"})
    return problems, results


# ---------------------------------------------------------------- the command

def _walk_run(run, skip_abs, findings, counts):
    """Text files under RUN outside upstream/ and the builder logs, as (abs, rel, text). Symbolic links are findings,
    never followed. status.md is cut at its `## Builder` heading."""
    out = []
    for root, dirs, files in os.walk(run, followlinks=False):
        rel_root = os.path.relpath(root, run).replace(os.sep, "/")
        if rel_root == ".":
            dirs[:] = [d for d in dirs if d != "upstream"]
        keep = []
        for d in sorted(dirs):
            full = os.path.join(root, d)
            rel = os.path.relpath(full, run).replace(os.sep, "/")
            if builder_log(rel + "/x"):
                counts["builder_logs_skipped"] += 1
                continue
            if os.path.islink(full):
                findings.append({"defect": "symlink", "where": rel, "detail": "symbolic link not followed"})
            else:
                keep.append(d)
        dirs[:] = keep
        for f in sorted(files):
            full = os.path.join(root, f)
            rel = os.path.relpath(full, run).replace(os.sep, "/")
            if os.path.abspath(full) in skip_abs:
                continue
            if builder_log(rel):
                counts["builder_logs_skipped"] += 1
                continue
            if os.path.islink(full):
                findings.append({"defect": "symlink", "where": rel, "detail": "symbolic link not followed"})
                continue
            try:
                with open(full, "rb") as fh:
                    raw = fh.read()
            except OSError as e:
                findings.append({"defect": "unreadable-file", "where": rel, "detail": "cannot read: %s" % e.strerror})
                continue
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                counts["binary_skipped"] += 1
                continue
            if rel == "status.md":
                text = status_customer_part(text)
                if text is None:
                    counts["builder_logs_skipped"] += 1
                    continue
            counts["files_scanned"] += 1
            out.append((full, rel, text))
    return out


SCAN_OF = {"vocabulary-banned": "vocabulary", "vocabulary-retired": "vocabulary", "rule-id": "ids", "step-id": "ids",
           "code-token": "ids", "machine-path": "paths", "scratch-path": "paths", "symlink": "files",
           "unreadable-file": "files"}


def notes_from(findings):
    """One builder-log note per file per scan, in first-seen order."""
    notes, at = [], {}
    for f in findings:
        scan = "observed" if f["defect"].startswith("observed-") else SCAN_OF.get(f["defect"], f["defect"])
        file = f["where"].rsplit(":", 1)[0] if re.search(r":\d+$", f["where"]) else f["where"]
        key = (file, scan)
        if key not in at:
            at[key] = len(notes)
            notes.append({"scan": scan, "file": file, "count": 0, "defects": [], "audience": "run-builder-log",
                          "upstream": scan == "observed"})
        n = notes[at[key]]
        n["count"] += 1
        if f["defect"] not in n["defects"]:
            n["defects"].append(f["defect"])
    for n in notes:
        what = "upstream problem(s) carried from the Future State Design run" if n["upstream"] else "finding(s)"
        n["detail"] = "%s: %d %s (%s)" % (n["scan"], n["count"], what, ", ".join(n["defects"]))
    return notes


def summary_line(notes):
    if not notes:
        return "post-run: no builder-log notes"
    scans = sorted(set(n["scan"] for n in notes))
    return "post-run: %d builder-log note(s) over %d file(s) (%s); nothing blocks" % (
        len(notes), len(set(n["file"] for n in notes)), ", ".join(scans))


def _report(exit_code, problems, counts, extra=None):
    rep = {"check": "postrun", "path": "skills/agent-design/scripts/postrun.py", "run": ".",
           "exit_code": exit_code, "result": {0: "pass", 2: "error"}[exit_code],
           "problems": problems, "counts": counts, "agent_fallback": FALLBACK, "audience": "run-builder-log"}
    if extra:
        rep.update(extra)
    return rep


def _readable(run):
    if not os.path.isdir(run):
        raise RunUnreadable("run folder is missing or not a folder")
    state = os.path.join(run, "state.json")
    if os.path.lexists(state):
        try:
            with open(state, encoding="utf-8") as fh:
                json.load(fh)
        except (OSError, ValueError, UnicodeDecodeError) as e:
            raise RunUnreadable("state.json cannot be read: %s" % e)


def check(run_dir, out=None, page_dir=None):
    run = os.path.abspath(run_dir)
    out_abs = os.path.abspath(out) if out else os.path.join(run, REPORT_REL)
    counts = {"files_scanned": 0, "binary_skipped": 0, "builder_logs_skipped": 0, "vocabulary_hits": 0,
              "observed_problems": 0, "id_problems": 0, "html_files": 0, "path_problems": 0, "symlinks": 0,
              "unreadable_files": 0, "scan_errors": 0, "notes": 0}
    try:
        _readable(run)
    except RunUnreadable as e:
        return _report(2, [{"defect": "unreadable-run", "where": ".", "detail": str(e)}], counts)

    findings = []
    skip = {os.path.join(run, REPORT_REL), out_abs, os.path.join(run, RESOLUTIONS_REL)}
    files = _walk_run(run, skip, findings, counts)
    counts["symlinks"] = sum(1 for p in findings if p["defect"] == "symlink")
    counts["unreadable_files"] = sum(1 for p in findings if p["defect"] == "unreadable-file")
    observed_exits = {}

    def scan_error(name, e):
        counts["scan_errors"] += 1
        findings.append({"defect": "scan-error", "where": name, "detail": "%s scan stopped: %s" % (name, e)})

    # vocabulary
    try:
        schema, terms = _load_vocabulary()
        for _listname, name, _rx in terms:
            counts["vocabulary:" + name] = 0
        for full, rel, text in files:
            if _is_vocabulary_copy(text, schema):
                continue
            for i, listname, name in _text_hits(_mask_own_counts(text, terms), terms):
                counts["vocabulary:" + name] += 1
                counts["vocabulary_hits"] += 1
                findings.append({"defect": "vocabulary-%s" % listname, "where": "%s:%d" % (rel, i),
                                 "detail": "%s term: %s" % (listname, name)})
    except Exception as e:  # one scan's error is a note; the others continue
        scan_error("vocabulary", e)

    # observed (upstream problems, carried)
    try:
        observed, observed_exits = observed_check(run)
        findings.extend(observed)
        counts["observed_problems"] = len(observed)
    except Exception as e:
        scan_error("observed", e)

    # ids
    try:
        ids, n_html = id_scan(run)
        findings.extend(ids)
        counts["id_problems"] = len(ids)
        counts["html_files"] = n_html
    except Exception as e:
        scan_error("ids", e)

    # paths
    try:
        n = 0
        for full, rel, text in files:
            found = path_scan(rel, text)
            n += len(found)
            findings.extend(found)
        counts["path_problems"] = n
    except Exception as e:
        scan_error("paths", e)

    notes = notes_from(findings)
    counts["notes"] = len(notes)
    return _report(0, [], counts, {"findings": findings, "notes": notes, "summary": summary_line(notes),
                                   "observed_exits": observed_exits})


def _write_builder_log(run, notes):
    """Replace this check's earlier lines in RUN/builder/problems.jsonl with the current notes."""
    p = os.path.join(run, BUILDER_LOG_REL)
    kept = []
    if os.path.isfile(p) and not os.path.islink(p):
        with open(p, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                try:
                    rec = json.loads(line)
                except ValueError:
                    kept.append(line.rstrip("\n"))
                    continue
                if not (isinstance(rec, dict) and rec.get("check") == "postrun"):
                    kept.append(line.rstrip("\n"))
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as fh:
        for line in kept:
            fh.write(line + "\n")
        for n in notes:
            fh.write(json.dumps({"kind": "upstream-problem" if n["upstream"] else "postrun-note", "item": n["file"],
                                 "detail": n["detail"], "stage": "post-run", "check": "postrun",
                                 "audience": "run-builder-log"}, ensure_ascii=False) + "\n")


def _write_resolutions(run):
    """The empty record the hand-off lists as a post-run output; it never holds names."""
    p = os.path.join(run, RESOLUTIONS_REL)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as fh:
        json.dump({"resolutions": [], "note": "post-run asks nothing; findings are builder-log notes"}, fh, indent=1)
        fh.write("\n")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Post-run hygiene notes over an Agent Design run folder.")
    ap.add_argument("--run", required=True)
    ap.add_argument("--out")
    ap.add_argument("--page-dir", help="accepted for older callers and ignored")
    args = ap.parse_args(argv)
    rep = check(args.run, args.out, args.page_dir)
    run = os.path.abspath(args.run)
    out = os.path.abspath(args.out) if args.out else os.path.join(run, REPORT_REL)
    if not args.out and not os.path.isdir(run):
        out = None  # never create a run folder that is not there
    written = True
    try:
        if out:
            os.makedirs(os.path.dirname(out), exist_ok=True)
            with open(out, "w", encoding="utf-8") as fh:
                json.dump(rep, fh, indent=1, ensure_ascii=False)
                fh.write("\n")
        if rep["exit_code"] == 0:
            _write_builder_log(run, rep["notes"])
            _write_resolutions(run)
    except OSError as e:
        written = False
        print("cannot write the report: %s" % e.strerror, file=sys.stderr)
    for p in rep["problems"]:
        print("%s %s %s" % (p["defect"], p["where"], p["detail"]))
    for n in rep.get("notes", []):
        print("note %s %s" % (n["file"], n["detail"]))
    if not written:
        # without its report the run is not checked: never a pass
        print("postrun: error, report not written (exit 2)")
        return 2
    print(rep.get("summary") or "postrun: error (exit 2)")
    print("postrun: %s (exit %d)" % (rep["result"], rep["exit_code"]))
    return rep["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
