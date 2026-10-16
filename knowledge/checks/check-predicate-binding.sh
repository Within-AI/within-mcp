#!/usr/bin/env python3
# This file is Python despite its .sh name: run it directly or as `python3 <this file>`; `bash <this file>` cannot run it.
"""Predicate-binding gate — the decision-rules stage's load-bearing token-binding release check.

Plain-language: "Does every rule's condition read only interface fields the built interface designs actually
PUBLISH — and does each rule's declared list of fields match the fields its condition really reads?" (Former
internal tag: the binding half of the stage's checks; FM-16. The plain-language question is the handle.)

WHAT THIS CHECK IS — AND IS NOT (the enforcement model, R1 §2.5). This check is *told* its two targets as
explicit arguments (never globbing): the published interface-token index and the decision policy. It does
exactly one kind of work — STRUCTURE and a CLOSED TOKEN SET: it tests token MEMBERSHIP against the closed
published set, and it reconciles each rule's declared binding surface against the tokens its condition
actually names. It NEVER judges whether a rule is semantically correct, whether the RIGHT token was chosen
for the meaning, or whether a rule is honest — those are the independent cross-engine reviewer's and the
human sign-off's job ("Sign off the stage"). A passing binding gate is NEVER, on its own, evidence that a
policy is sound.

The two ratified resolutions for an UNBOUND predicate are both HUMAN-decided, never executor-chosen (this is
the "Resolve a flagged blocker" decision): (a) extend the owning interface with a *grounded* derived signal —
a change routed back through the interface-design stage and re-derived into the token index — or (b) drop the
predicate. NO token is invented; no rule's meaning is bent to fit an existing token. This script only REPORTS
the UNBOUND rows; it never invents.

THE TOKEN-INDEX INPUT SCHEMA (what the gate reads). ONE file carrying a SINGLE top-level mapping, read by the
house `yaml.safe_load` path (a `.yaml` document; JSON is a YAML subset so an equivalently-shaped `.json`
parses through the same single load path). The mapping has exactly: `schema_version` (a format tag); a
`published_tokens:` LIST whose every element is one published-token record with the five keys
`token` (the exact <interface-id>.<field> string — the membership key) · `interface_id` · `surface`
(typed_input | invocation | decision_state | back_signal) · `evidence_state` (the token's own R4 canonical-
four stamp) · `open` (its open-register reference, or `—`); and a top-level integer `token_count`. The gate
builds the closed membership set from each record's `token`, and reconciles len(published_tokens) against
`token_count` (a mismatch is a SETUP defect -> exit 2).

THE EXTRACTION GRAMMAR (fixed, INDEX-INDEPENDENT — it never consults the index, so it can never be steered
down to a shorter *published* prefix). Applied with explicit match boundaries so a token is captured only at
segment boundaries — never as the tail of a longer identifier and never truncated inside a dotted path:
  * scan the condition for MAXIMAL runs of token characters {letter, digit, `_`, `.`, `-`}; a run is bounded
    on the left by start-of-string or a non-token character, and on the right by end-of-string or a non-token
    character (so a run is never carved out of the middle of a longer identifier — in `foo_bar.a` the whole
    run `foo_bar.a` is taken, and `bar.a` is never extracted from it). The only cleanup is dropping a SINGLE
    trailing `.` (one sentence period that is a token char) — never a greedy strip of every trailing dot, so a
    malformed span like `X.y..` keeps its dangling dot and is caught as malformed rather than silently repaired;
  * a run with no `.` is a bare identifier, not an interface token -> ignored;
  * a run containing a `.` is classified against the full operative-token grammar
    `^[A-Za-z][A-Za-z0-9]*(-[A-Za-z0-9]+)*(\.[a-z0-9_]+)+$` anchored to the WHOLE run (the interface-id
    `<iface>` admits neither `.` nor `_`, so the first `.` unambiguously begins the field path and the
    maximal dotted path is taken whole — a two-level `<iface>.field.subfield` is captured entire, never split
    to `<iface>.field`):
      - matches in full -> it IS the operative token (taken whole), then judged BOUND/UNBOUND by exact
        membership;
      - contains a `.` but does NOT match in full -> a MALFORMED token-like span (e.g. `foo_bar.a`, whose
        <iface> position illegally contains `_`; or `a.b.C`, whose subfield is not lowercase) -> REJECTED and
        REPORTED (exit 1), never partial-matched and never truncated to a shorter published-looking prefix.

RECONCILE. The set extracted from a rule's `condition` must EQUAL the rule's declared `operative_tokens`. A
token extracted but not declared, or declared but not extracted, is a RECONCILIATION defect (exit 1) — the
declared binding surface and the condition prose must agree.

MATCH. Each operative token is judged by EXACT MEMBERSHIP only — BOUND iff the token string is present
verbatim in the published set; UNBOUND otherwise. There is NO prefix/parent fallback — a published parent
(`a.b`) does NOT bind an unpublished operative subfield (`a.b.c`); the subfield must itself be published.

VERDICT. Every predicate BOUND and every rule reconciled -> the gate clears (exit 0). A single UNBOUND
predicate OR a single reconciliation defect OR a single malformed token-like span -> exit 1, and the emitted
manifest names the offending row(s).

Parsing: PyYAML is REQUIRED. One `yaml.safe_load` path serves both the token index and the policy (both a
`.yaml` and a `.json` shape). PyYAML absent -> exit 2 (setup error); it never degrades to a line scan.

Like the other factory checks this is writer-re-derivable and cwd-independent; `actor.engine == script`.

Usage: check-predicate-binding.sh [--verbose] TOKEN_INDEX DECISION_POLICY
Exit: 0 pass; 1 binding/reconciliation/malformed-token failure; 2 setup error (incl. PyYAML absent, a missing
file, or a token_count mismatch).
"""
import os
import re
import sys

# The full operative-token grammar, anchored to a whole run. `<iface>` admits neither `.` nor `_`.
OPERATIVE_TOKEN_RE = re.compile(r"^[A-Za-z][A-Za-z0-9]*(?:-[A-Za-z0-9]+)*(?:\.[a-z0-9_]+)+$")
# A maximal run of token characters {letter, digit, `_`, `.`, `-`}.
RUN_RE = re.compile(r"[A-Za-z0-9_.\-]+")


def die(msg, code):
    sys.stderr.write(msg + "\n")
    sys.exit(code)


def _nonempty(v):
    return isinstance(v, str) and v.strip() != ""


def load_one_yaml(path, what):
    if not os.path.isfile(path):
        die("Missing %s: %s" % (what, path), 2)
    text = open(path, "r", encoding="utf-8", errors="replace").read()
    try:
        import yaml
    except ImportError:
        die("PyYAML is required for the binding-gate parse but is not importable. Install it "
            "(pip install pyyaml) and re-run; this check does not fall back to a line scan.", 2)
    try:
        return yaml.safe_load(text)
    except Exception as e:
        die("%s is not parseable (JSON or YAML): %s" % (what, e), 1)


def load_index(path):
    """Return (published_set, records_by_token, declared_count). A malformed index shape is a SETUP error
    (exit 2) — the gate is TOLD a well-formed index; validating the index's own shape is upstream."""
    doc = load_one_yaml(path, "token index")
    if not isinstance(doc, dict):
        die("token index is not a mapping (expected one top-level published-token index)", 2)
    pubs = doc.get("published_tokens")
    if not isinstance(pubs, list):
        die("token index carries no `published_tokens:` list — the gate reads a closed set of published-token "
            "records", 2)
    published = set()
    records = {}
    for i, rec in enumerate(pubs):
        if not isinstance(rec, dict):
            die("token index `published_tokens` row %d is not a mapping" % i, 2)
        tok = rec.get("token")
        if not _nonempty(tok):
            die("token index `published_tokens` row %d carries no non-empty `token`" % i, 2)
        published.add(tok)
        records[tok] = rec
    declared = doc.get("token_count")
    if not isinstance(declared, int):
        die("token index carries no integer `token_count` — the gate reconciles the list length against it", 2)
    if declared != len(pubs):
        die("token index length %d != declared token_count %d — a setup defect, resolve the index before the "
            "gate runs" % (len(pubs), declared), 2)
    return published, records, declared


def extract_tokens(condition):
    """Return (operative_tokens, malformed_spans) from ONE condition string, by the fixed, index-independent
    grammar. `operative_tokens` is an ordered de-duplicated list; `malformed_spans` names every dotted run
    that is token-like but does not match the grammar in full (a hard defect)."""
    seen, out, malformed = set(), [], []
    for m in RUN_RE.finditer(condition or ""):
        run = m.group(0)
        if run.endswith("."):
            run = run[:-1]                     # SINGLE-mark cleanup: drop ONE trailing sentence period only (never a
                                               # greedy rstrip) — so `X.y..` keeps a dangling dot and is caught as a
                                               # malformed span below, never silently repaired to a published prefix
        if not run or "." not in run:
            continue                           # a bare identifier is not an interface token
        if OPERATIVE_TOKEN_RE.match(run):
            if run not in seen:
                seen.add(run)
                out.append(run)
        else:
            malformed.append(run)              # a malformed token-like span — rejected, never truncated
    return out, malformed


def iter_rules(policy):
    """Yield (owner, rule_id, rule_dict) for every rule across all rule_sets."""
    rule_sets = policy.get("rule_sets") if isinstance(policy.get("rule_sets"), list) else []
    for rs in rule_sets:
        if not isinstance(rs, dict):
            continue
        owner = rs.get("owner")
        for rule in (rs.get("rules") if isinstance(rs.get("rules"), list) else []):
            if isinstance(rule, dict):
                yield owner, rule.get("rule_id"), rule


def build(policy, published):
    """Return (rows, problems). rows = [(owner, rule_id, [(token, BOUND/UNBOUND)], declared_ok)]."""
    rows, problems = [], []
    for owner, rid, rule in iter_rules(policy):
        cond = rule.get("condition", "")
        declared = rule.get("operative_tokens")
        declared_list = declared if isinstance(declared, list) else []
        extracted, malformed = extract_tokens(cond)
        if malformed:
            for span in malformed:
                problems.append("MALFORMED TOKEN in rule %r (owner %r): the run %r is token-like but does not "
                                "match the operative-token grammar — rejected, never truncated to a published "
                                "prefix" % (rid, owner, span))
        # reconcile extracted-from-condition against declared operative_tokens (sets must be equal)
        ex_set, dec_set = set(extracted), set(declared_list)
        for t in sorted(ex_set - dec_set):
            problems.append("RECONCILIATION DEFECT in rule %r (owner %r): token %r appears in the condition "
                            "but not in declared `operative_tokens`" % (rid, owner, t))
        for t in sorted(dec_set - ex_set):
            problems.append("RECONCILIATION DEFECT in rule %r (owner %r): token %r is declared in "
                            "`operative_tokens` but not read by the condition" % (rid, owner, t))
        statuses = []
        for t in extracted:
            status = "BOUND" if t in published else "UNBOUND"
            statuses.append((t, status))
            if status == "UNBOUND":
                problems.append("UNBOUND PREDICATE in rule %r (owner %r): token %r is published by no "
                                "interface design (exact membership; no prefix/parent fallback)"
                                % (rid, owner, t))
        rows.append((owner, rid, statuses, ex_set == dec_set and not malformed))
    return rows, problems


def render_manifest(rows, problems, declared_count):
    n_rules = len(rows)
    n_bindings = sum(1 for _, _, ss, _ in rows for _, st in ss if st == "BOUND")
    n_unbound = sum(1 for _, _, ss, _ in rows for _, st in ss if st == "UNBOUND")
    n_recon = sum(1 for p in problems if p.startswith("RECONCILIATION"))
    n_malformed = sum(1 for p in problems if p.startswith("MALFORMED"))
    L = []
    L.append("binding manifest (generated by check-predicate-binding.sh — do not hand-edit):")
    L.append("")
    for owner, rid, statuses, ok in rows:
        if statuses:
            cell = ", ".join("%s [%s]" % (t, st) for t, st in statuses)
        else:
            cell = "(no interface-token predicate; routing / structural condition)"
        L.append("  rule %s (owner %s): %s" % (rid, owner, cell))
    L.append("")
    L.append("reconciliation line: %d rules | %d operative bindings | %d UNBOUND | %d reconciliation defects | "
             "%d malformed token-like spans" % (n_rules, n_bindings, n_unbound, n_recon, n_malformed))
    L.append("token_count match line: index declares %d published tokens." % declared_count)
    # UNBOUND-action section — the two ratified, HUMAN-decided resolutions the gate only REPORTS (never invents).
    L.append("")
    unbound = [(owner, rid, t) for owner, rid, statuses, _ in rows for t, st in statuses if st == "UNBOUND"]
    if unbound:
        L.append("UNBOUND predicates — ACTION REQUIRED:")
        L.append("  The coherence gate halted. Each row below awaits a HUMAN-ratified resolution, never an "
                 "executor-chosen one — (a) extend the owning interface with a grounded derived signal (routed "
                 "back through the interface-design stage and re-derived into the token index), or (b) drop the "
                 "predicate. No token is invented and no rule's meaning is bent to fit an existing token; the "
                 "gate only reports these rows.")
        for owner, rid, t in unbound:
            L.append("  rule %s (owner %s): unpublished field %s" % (rid, owner, t))
    else:
        L.append("UNBOUND predicates — none: every operative token across all rules resolves to a published "
                 "index token by exact membership (no published parent was allowed to bind an unpublished "
                 "subfield).")
    return "\n".join(L)


def main(argv):
    verbose = False
    rest = []
    for a in argv:
        if a in ("--verbose", "-v"):
            verbose = True
        else:
            rest.append(a)
    if len(rest) != 2:
        die("usage: check-predicate-binding.sh [--verbose] TOKEN_INDEX DECISION_POLICY", 2)
    index_path, policy_path = rest
    published, _records, declared = load_index(index_path)
    policy = load_one_yaml(policy_path, "decision policy")
    if not isinstance(policy, dict):
        die("decision policy is not a mapping (expected one decision-policy document)", 1)

    rows, problems = build(policy, published)
    manifest = render_manifest(rows, problems, declared)
    if verbose:
        sys.stderr.write("parse engine: pyyaml safe_load (serves both the token index and the policy)\n")
    print(manifest)
    if problems:
        die("predicate-binding gate FAILED (%s):\n  - %s"
            % (policy_path, "\n  - ".join(problems)), 1)
    print("PASS: every rule predicate binds to a published <interface-id>.<field> token by exact membership; "
          "every rule's declared operative_tokens reconciles with its condition; 0 UNBOUND, 0 reconciliation "
          "defects, 0 malformed spans. (Membership only — semantic honesty is the review's job.)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
