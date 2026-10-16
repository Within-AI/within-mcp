#!/usr/bin/env python3
# This file is Python despite its .sh name: run it directly or as `python3 <this file>`; `bash <this file>` cannot run it.
"""Coherence-matrix closure gate — the coherence stage's load-bearing release check (deliverable #6a).

Plain-language: "Does every invariant in the closed set end the reconciliation in a resolved state — held,
driven-and-re-checked (routed), or named as an escalation — with NOTHING left flagged-but-un-dispositioned?"
The `check-coherence-matrix-closure` internal tag is a footnote; the plain-language question is the handle.

WHAT THIS CHECK IS — AND IS NOT (the enforcement model, R1 §2.5).
This check is *told* its three targets as explicit arguments (it never globs and never self-detects "am I a
coherence matrix" — §2.5 facet 1): the coherence matrix, the findings register, and the closed-invariant
index (a topology index enumerating the closed invariant set). It does exactly one kind of work — STRUCTURE
and a CLOSED SET: it tests that every invariant in the closed set is statused, that no `violated` row is left
un-dispositioned, and that every `routed`/`escalated` row resolves to a findings-register entry that is
itself well-formed (a routed finding names its owning stage; an escalated finding is decision-ready). It
NEVER judges whether the reconciled design is semantically SOUND, whether the RIGHT precedence call was made,
or whether an escalation SHOULD have been driven instead — those are the independent cross-engine reviewer's
and the human sign-off's job ("Sign off the stage"). A green closure gate is NEVER, on its own, evidence that
the reconciliation is honest. It also does NOT re-own set-completeness of any single artifact (that fired at
that artifact's owning stage); it reconciles the representations against each other only.

THE CLOSED-INVARIANT INDEX INPUT (topology-index.yaml). ONE top-level mapping read through the house
`yaml.safe_load` path, carrying a `closed_invariant_set:` LIST whose every element is `{id, name}`. The gate
builds the closed set from each element's `id`. Every invariant in that closed set must appear as a matrix
row (coverage), and the matrix may name no invariant outside it.

THE COHERENCE-MATRIX INPUT. A markdown artifact carrying a single machine block delimited by an HTML comment
`<!-- COHERENCE-MATRIX ... -->` whose body is YAML with a `rows:` list; every row is
`{invariant_id, status, finding_ref}` where `status` ∈ {holds, violated, routed, escalated} and `finding_ref`
is a findings-register `finding_id` (or `—` for a `holds` row). The block also carries (since 2026-09-25,
when coherence became the design-coherence gate that closes stage 10) a top-level
`design_set: {path, sha256}` pinning the design set the matrix was built against: the pinned file must exist
(the path resolves from the working directory, the run root) and hash to the pinned sha, else the matrix is
STALE (built against another design set) and the gate fails. A matrix without the key is an older matrix
(a run authored before the gate existed): it is still read, with a note; the gate itself
(scripts/_coherence_gate.py) refuses a matrix that does not pin the design set.

THE FINDINGS-REGISTER INPUT. A markdown artifact carrying a single `<!-- FINDINGS-REGISTER ... -->` machine
block whose body is YAML with a `findings:` list; every finding is
`{finding_id, invariant_id, disposition, status, decision_question}`. A `routed` finding's `disposition` must
begin `routed_to_owning_stage`; an `escalated` finding must carry a non-empty `decision_question` (it is
decision-ready — it states the exact precedence question or design decision needed).

VERDICT. Coverage complete, every invariant statused, NO `violated` left un-dispositioned, and every
`routed`/`escalated` row resolvable to a well-formed finding -> the gate clears (exit 0). A single un-covered
invariant, a single `violated` row, a single dangling finding_ref, a routed finding that names no owning
stage, or an escalated finding that is not decision-ready -> exit 1, and the emitted manifest names the
offending row(s). A malformed input shape -> exit 2 (setup error).

Parsing: PyYAML is REQUIRED (one `yaml.safe_load` path); absent -> exit 2, never a line-scan fallback. Like
the other factory checks this is writer-re-derivable and cwd-independent; `actor.engine == script`.

Usage: check-coherence-matrix-closure.sh [--verbose] COHERENCE_MATRIX FINDINGS_REGISTER INVARIANT_INDEX
Exit: 0 pass; 1 closure/coverage/disposition failure; 2 setup error (bad args, missing file, PyYAML absent,
      or a malformed machine block).
"""
import hashlib
import os
import re
import sys

TERMINAL_GOOD = {"holds", "routed", "escalated"}
ALL_STATUSES = {"holds", "violated", "routed", "escalated"}


def die(msg, code):
    sys.stderr.write(msg + "\n")
    sys.exit(code)


def _require_yaml():
    try:
        import yaml
        return yaml
    except ImportError:
        die("PyYAML is required for the closure-gate parse but is not importable. Install it "
            "(pip install pyyaml) and re-run; this check does not fall back to a line scan.", 2)


def load_yaml_file(path, what):
    if not os.path.isfile(path):
        die("Missing %s: %s" % (what, path), 2)
    yaml = _require_yaml()
    try:
        return yaml.safe_load(open(path, "r", encoding="utf-8", errors="replace").read())
    except Exception as e:
        die("%s is not parseable YAML: %s" % (what, e), 2)


def load_machine_block(path, what, marker):
    """Read the YAML body of a single `<!-- MARKER ... -->` machine block in a markdown file."""
    if not os.path.isfile(path):
        die("Missing %s: %s" % (what, path), 2)
    text = open(path, "r", encoding="utf-8", errors="replace").read()
    m = re.search(r"<!--\s*" + re.escape(marker) + r"\s*(.*?)-->", text, re.S)
    if not m:
        die("%s carries no `<!-- %s ... -->` machine block — the gate reads the machine block, not the "
            "human-readable table" % (what, marker), 2)
    yaml = _require_yaml()
    try:
        doc = yaml.safe_load(m.group(1))
    except Exception as e:
        die("%s `%s` machine block is not parseable YAML: %s" % (what, marker, e), 2)
    if not isinstance(doc, dict):
        die("%s `%s` machine block is not a mapping" % (what, marker), 2)
    return doc


def load_closed_set(path):
    doc = load_yaml_file(path, "invariant index")
    if not isinstance(doc, dict):
        die("invariant index is not a mapping (expected one top-level topology index)", 2)
    cis = doc.get("closed_invariant_set")
    if not isinstance(cis, list) or not cis:
        die("invariant index carries no non-empty `closed_invariant_set:` list — the gate reads the closed "
            "set of invariant ids from it", 2)
    closed = []
    for i, row in enumerate(cis):
        if not isinstance(row, dict) or not row.get("id"):
            die("invariant index `closed_invariant_set` row %d carries no `id`" % i, 2)
        closed.append(row["id"])
    return closed


def load_findings(path):
    doc = load_machine_block(path, "findings register", "FINDINGS-REGISTER")
    findings = doc.get("findings")
    if findings is None:
        findings = []
    if not isinstance(findings, list):
        die("findings register `findings:` is not a list", 2)
    by_id = {}
    for i, f in enumerate(findings):
        if not isinstance(f, dict) or not f.get("finding_id"):
            die("findings register `findings` row %d carries no `finding_id`" % i, 2)
        by_id[f["finding_id"]] = f
    return by_id


def load_matrix(path):
    doc = load_machine_block(path, "coherence matrix", "COHERENCE-MATRIX")
    rows = doc.get("rows")
    if not isinstance(rows, list) or not rows:
        die("coherence matrix `rows:` is not a non-empty list", 2)
    out = []
    for i, r in enumerate(rows):
        if not isinstance(r, dict) or not r.get("invariant_id") or not r.get("status"):
            die("coherence matrix `rows` row %d needs both `invariant_id` and `status`" % i, 2)
        out.append(r)
    return out, doc


def design_set_problem(doc):
    """None when the matrix pins the design set on disk (or pins none: an older matrix); else a problem line."""
    pin = doc.get("design_set")
    if pin is None:
        return None
    if not isinstance(pin, dict) or not isinstance(pin.get("path"), str) or not isinstance(pin.get("sha256"), str):
        die("coherence matrix `design_set` must be {path, sha256} with both as strings (quote the sha so YAML "
            "never reads it as a number); got %r" % (pin,), 2)
    if not os.path.isfile(pin["path"]):
        return ("STALE: the matrix pins design set %s, which is not on disk (run from the run root)" % pin["path"])
    h = hashlib.sha256()
    with open(pin["path"], "rb") as f:
        h.update(f.read())
    if h.hexdigest() != pin["sha256"]:
        return ("STALE: the matrix pins design set %s at sha %s but it now hashes to %s — a matrix built against "
                "another design set is stale; rebuild the matrix on the current design set"
                % (pin["path"], pin["sha256"][:12], h.hexdigest()[:12]))
    return None


def _nonempty(v):
    return isinstance(v, str) and v.strip() not in ("", "—", "-")


def main(argv):
    verbose = False
    rest = []
    for a in argv:
        if a in ("--verbose", "-v"):
            verbose = True
        else:
            rest.append(a)
    if len(rest) != 3:
        die("usage: check-coherence-matrix-closure.sh [--verbose] COHERENCE_MATRIX FINDINGS_REGISTER "
            "INVARIANT_INDEX", 2)
    matrix_path, findings_path, index_path = rest

    closed = load_closed_set(index_path)
    closed_set = set(closed)
    findings = load_findings(findings_path)
    rows, matrix_doc = load_matrix(matrix_path)

    problems = []
    seen = {}
    for r in rows:
        inv = r["invariant_id"]
        st = r["status"]
        fr = r.get("finding_ref")
        seen[inv] = st
        if inv not in closed_set:
            problems.append("COVERAGE: matrix row names invariant %r which is not in the closed set "
                            "(the matrix reconciles the closed set only, adds none)" % inv)
            continue
        if st not in ALL_STATUSES:
            problems.append("STATUS: invariant %r carries status %r — must be one of holds/violated/routed/"
                            "escalated" % (inv, st))
            continue
        if st == "violated":
            problems.append("NOT CLOSED: invariant %r is `violated` and left un-dispositioned — the stage "
                            "clears only when every invariant is holds/routed/escalated (drive it through the "
                            "owning stage or name an escalation)" % inv)
            continue
        if st in ("routed", "escalated"):
            if not _nonempty(fr):
                problems.append("DANGLING: invariant %r is %r but carries no findings-register `finding_ref`"
                                % (inv, st))
                continue
            f = findings.get(fr)
            if f is None:
                problems.append("DANGLING: invariant %r (%s) links finding_ref %r which resolves to no "
                                "findings-register entry" % (inv, st, fr))
                continue
            if st == "routed" and not str(f.get("disposition", "")).startswith("routed_to_owning_stage"):
                problems.append("DISPOSITION: routed invariant %r links finding %r whose disposition %r does "
                                "not name an owning stage (must begin `routed_to_owning_stage`)"
                                % (inv, fr, f.get("disposition")))
            if st == "escalated" and not _nonempty(f.get("decision_question")):
                problems.append("NOT DECISION-READY: escalated invariant %r links finding %r that states no "
                                "`decision_question` (a named escalation must state the exact precedence "
                                "question or design decision needed)" % (inv, fr))

    stale = design_set_problem(matrix_doc)
    if stale:
        problems.append(stale)

    for inv in closed:
        if inv not in seen:
            problems.append("COVERAGE: invariant %r in the closed set has NO matrix row (every invariant must "
                            "be statused; coverage of the closed set is graded, not a row count)" % inv)

    n_hold = sum(1 for r in rows if r["status"] == "holds")
    n_routed = sum(1 for r in rows if r["status"] == "routed")
    n_esc = sum(1 for r in rows if r["status"] == "escalated")
    n_viol = sum(1 for r in rows if r["status"] == "violated")
    if verbose:
        sys.stderr.write("parse engine: pyyaml safe_load (matrix + findings machine blocks + invariant "
                         "index)\n")
    print("closure manifest (generated by check-coherence-matrix-closure.sh — do not hand-edit):")
    print("  closed invariant set: %d | matrix rows: %d | holds %d · routed %d · escalated %d · violated %d"
          % (len(closed), len(rows), n_hold, n_routed, n_esc, n_viol))

    if matrix_doc.get("design_set") is None:
        print("NOTE: this matrix carries no design_set pin (an older matrix); the design-coherence gate requires "
              "one")
    if problems:
        die("coherence-matrix closure gate FAILED (%s):\n  - %s"
            % (matrix_path, "\n  - ".join(problems)), 1)
    print("PASS: every invariant in the closed set is statused; no `violated` left un-dispositioned; every "
          "routed/escalated row resolves to a well-formed finding (routed names its owning stage, escalated "
          "is decision-ready). (Structure + closed set only — semantic soundness is the review's job.)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
