#!/usr/bin/env python3
# This file is Python despite its .sh name: run it directly or as `python3 <this file>`; `bash <this file>` cannot run it.
"""Predicate-binding re-check — the coherence stage's re-verification of the decision-rules binding
(deliverable #6b; R1 §2.1).

Plain-language: "Does every decision-policy predicate STILL read only interface fields the built interface
designs actually PUBLISH?" The coherence stage re-runs this because a driven correction (a re-cut interface,
a re-owned rule) can silently break a binding that held at the decision-rules stage. It reuses the
decision-rules stage's binding logic (`check-predicate-binding.sh` + the
same token-index shape) — the same closed-membership test, run again at reconciliation time. The
`check-predicate-binding-recheck` internal tag is a footnote; the plain-language question is the handle.

WHAT THIS CHECK IS — AND IS NOT (the enforcement model, R1 §2.5). It is *told* its two targets as explicit
arguments (never globbing): the published interface-token index and the decision policy. It does exactly one
kind of work — STRUCTURE and a CLOSED TOKEN SET: it tests token MEMBERSHIP against the closed published set.
It NEVER judges whether a rule is semantically correct, whether the RIGHT token was chosen for the meaning,
or whether a predicate is honest — the independent reviewer's and the human sign-off's job. A passing
re-check is NEVER, on its own, evidence a policy is sound.

The two ratified resolutions for an UNBOUND predicate are both HUMAN-decided, never executor-chosen (the
"Resolve a flagged blocker" decision that rides G-E with `trigger: coherence-residual`): (a) extend the
owning interface with a *grounded* derived signal — routed back through the interface-design stage and
re-derived into the token index — or (b) drop the predicate. NO token is invented; no rule's meaning is bent
to fit an existing token. This script only REPORTS the UNBOUND rows; it never invents.

THE TOKEN-INDEX INPUT SCHEMA (identical to the decision-rules stage's). ONE top-level mapping read through
the house `yaml.safe_load` path: `schema_version`; a `published_tokens:` LIST whose every element carries at
least `token` (the exact `<interface-id>.<field>` string — the membership key); and a top-level integer
`token_count`. The gate builds the closed membership set from each record's `token` and reconciles
len(published_tokens) against `token_count` (a mismatch is a SETUP defect -> exit 2).

WHAT BINDS. For every rule across all `rule_sets[].rules[]`, the predicate token set is the UNION of the
rule's declared `operative_tokens[]` and the operative tokens EXTRACTED from its `condition` string (the
fixed, index-independent grammar below). Every token in that union must be present verbatim in the published
set (EXACT membership — a published parent `a.b` does NOT bind an unpublished subfield `a.b.c`).

THE EXTRACTION GRAMMAR (fixed, INDEX-INDEPENDENT — it never consults the index, so it can never be steered
down to a shorter published prefix). Scan the condition for maximal runs of token characters
{letter, digit, `_`, `.`, `-`}; drop a SINGLE trailing sentence period only; a run with no `.` is a bare
identifier (ignored); a run with a `.` is classified against the operative-token grammar
`^[A-Za-z][A-Za-z0-9]*(-[A-Za-z0-9]+)*(\.[a-z0-9_]+)+$` anchored WHOLE — a full match is the operative token
taken whole (then judged by membership); a dotted run that does not match in full is a MALFORMED token-like
span (rejected, reported, never truncated to a published-looking prefix).

VERDICT. Every predicate token BOUND -> the gate clears (exit 0). A single UNBOUND token OR a single malformed
token-like span -> exit 1, and the emitted manifest names the offending row(s). A malformed input shape or a
`token_count` mismatch -> exit 2.

Parsing: PyYAML REQUIRED (one `yaml.safe_load` path serves both the index and the policy); absent -> exit 2,
never a line scan. Writer-re-derivable, cwd-independent; `actor.engine == script`.

Usage: check-predicate-binding-recheck.sh [--verbose] TOKEN_INDEX DECISION_POLICY
Exit: 0 pass; 1 binding/malformed-token failure; 2 setup error.
"""
import os
import re
import sys

OPERATIVE_TOKEN_RE = re.compile(r"^[A-Za-z][A-Za-z0-9]*(?:-[A-Za-z0-9]+)*(?:\.[a-z0-9_]+)+$")
RUN_RE = re.compile(r"[A-Za-z0-9_.\-]+")


def die(msg, code):
    sys.stderr.write(msg + "\n")
    sys.exit(code)


def _nonempty(v):
    return isinstance(v, str) and v.strip() != ""


def load_one_yaml(path, what):
    if not os.path.isfile(path):
        die("Missing %s: %s" % (what, path), 2)
    try:
        import yaml
    except ImportError:
        die("PyYAML is required for the binding re-check parse but is not importable. Install it "
            "(pip install pyyaml) and re-run; this check does not fall back to a line scan.", 2)
    try:
        return yaml.safe_load(open(path, "r", encoding="utf-8", errors="replace").read())
    except Exception as e:
        die("%s is not parseable (JSON or YAML): %s" % (what, e), 1)


def load_index(path):
    doc = load_one_yaml(path, "token index")
    if not isinstance(doc, dict):
        die("token index is not a mapping (expected one top-level published-token index)", 2)
    pubs = doc.get("published_tokens")
    if not isinstance(pubs, list):
        die("token index carries no `published_tokens:` list — the gate reads a closed set of published "
            "tokens", 2)
    published = set()
    for i, rec in enumerate(pubs):
        if not isinstance(rec, dict):
            die("token index `published_tokens` row %d is not a mapping" % i, 2)
        tok = rec.get("token")
        if not _nonempty(tok):
            die("token index `published_tokens` row %d carries no non-empty `token`" % i, 2)
        published.add(tok)
    declared = doc.get("token_count")
    if not isinstance(declared, int):
        die("token index carries no integer `token_count` — the gate reconciles the list length against "
            "it", 2)
    if declared != len(pubs):
        die("token index length %d != declared token_count %d — a setup defect, resolve the index before "
            "the gate runs" % (len(pubs), declared), 2)
    return published, declared


def extract_tokens(condition):
    seen, out, malformed = set(), [], []
    for m in RUN_RE.finditer(condition or ""):
        run = m.group(0)
        if run.endswith("."):
            run = run[:-1]
        if not run or "." not in run:
            continue
        if OPERATIVE_TOKEN_RE.match(run):
            if run not in seen:
                seen.add(run)
                out.append(run)
        else:
            malformed.append(run)
    return out, malformed


def iter_rules(policy):
    rule_sets = policy.get("rule_sets") if isinstance(policy.get("rule_sets"), list) else []
    for rs in rule_sets:
        if not isinstance(rs, dict):
            continue
        owner = rs.get("owner")
        for rule in (rs.get("rules") if isinstance(rs.get("rules"), list) else []):
            if isinstance(rule, dict):
                yield owner, rule.get("rule_id"), rule


def build(policy, published):
    rows, problems = [], []
    for owner, rid, rule in iter_rules(policy):
        cond = rule.get("condition", "")
        declared = rule.get("operative_tokens")
        declared_list = declared if isinstance(declared, list) else []
        extracted, malformed = extract_tokens(cond)
        for span in malformed:
            problems.append("MALFORMED TOKEN in rule %r (owner %r): the run %r is token-like but does not "
                            "match the operative-token grammar — rejected, never truncated to a published "
                            "prefix" % (rid, owner, span))
        # union of declared operative_tokens and tokens read by the condition; both must bind
        union, order = set(), []
        for t in list(declared_list) + extracted:
            if isinstance(t, str) and t not in union:
                union.add(t)
                order.append(t)
        statuses = []
        for t in order:
            status = "BOUND" if t in published else "UNBOUND"
            statuses.append((t, status))
            if status == "UNBOUND":
                problems.append("UNBOUND PREDICATE in rule %r (owner %r): token %r is published by no "
                                "interface design (exact membership; no prefix/parent fallback)"
                                % (rid, owner, t))
        rows.append((owner, rid, statuses))
    return rows, problems


def main(argv):
    verbose = False
    rest = []
    for a in argv:
        if a in ("--verbose", "-v"):
            verbose = True
        else:
            rest.append(a)
    if len(rest) != 2:
        die("usage: check-predicate-binding-recheck.sh [--verbose] TOKEN_INDEX DECISION_POLICY", 2)
    index_path, policy_path = rest
    published, declared = load_index(index_path)
    policy = load_one_yaml(policy_path, "decision policy")
    if not isinstance(policy, dict):
        die("decision policy is not a mapping (expected one decision-policy document)", 1)

    rows, problems = build(policy, published)
    n_bind = sum(1 for _, _, ss in rows for _, st in ss if st == "BOUND")
    n_unbound = sum(1 for _, _, ss in rows for _, st in ss if st == "UNBOUND")
    if verbose:
        sys.stderr.write("parse engine: pyyaml safe_load (serves both the token index and the policy)\n")
    print("binding re-check manifest (generated by check-predicate-binding-recheck.sh — do not hand-edit):")
    print("  %d rules | %d operative bindings | %d UNBOUND | index declares %d published tokens"
          % (len(rows), n_bind, n_unbound, declared))
    if problems:
        die("predicate-binding re-check FAILED (%s):\n  - %s\n  Each UNBOUND row awaits a HUMAN-ratified "
            "resolution (extend the owning interface with a grounded derived signal, or drop the predicate) "
            "— never an executor-chosen token." % (policy_path, "\n  - ".join(problems)), 1)
    print("PASS: every rule predicate still binds to a published <interface-id>.<field> token by exact "
          "membership (declared operative_tokens and condition spans both). (Membership only — semantic "
          "honesty is the review's job.)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
