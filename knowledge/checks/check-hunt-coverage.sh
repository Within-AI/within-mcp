#!/usr/bin/env python3
# This file is Python despite its .sh name: run it directly or as `python3 <this file>`; `bash <this file>` cannot run it.
"""Hunt-coverage check — the decision-rules stage's counterexample-hunt coverage release gate.

Plain-language: "For the rules we chose to stress-test, did each one's stress-test clear with evidence — and
is every other rule accounted for as deliberately set aside, not silently skipped?" (Former internal tag:
the hunt-coverage half of the stage's checks. The plain-language question is the handle.)

WHAT THIS CHECK IS — AND IS NOT (the enforcement model, R1 §2.5). It is *told* two explicit arguments: the
decision policy AND the coverage manifest. It does exactly one kind of work — STRUCTURE and a CLOSED SET: the
frozen-set decision is present, every policy rule is accounted for as frozen XOR excluded-with-a-ratified-note,
and every frozen-set rule's hunt record is WELL-FORMED and CLEARED. It NEVER judges whether the hunt
*genuinely searched* the space its `search_scope` claims, nor whether the excluded rules were legitimately
excluded — those are the independent cross-engine reviewer's and the human sign-off's job. A passing check is
NEVER, on its own, evidence that a hunt was genuine.

The structural predicates:

  1. FROZEN-SET + EXCLUSIONS ACCOUNTED: the `frozen_hunt_set` decision record is present and non-empty; the
     `excluded_rules` record is present; and every rule id in the policy's `rule_sets` is accounted for as
     EITHER frozen OR excluded-with-a-ratified-note (a non-empty `exclusion_note`). No rule silently
     unaccounted; no rule both frozen and excluded.
  2. CLEARED HUNT RECORD PER FROZEN RULE: every rule in the `frozen_hunt_set` has a `hunt_record` whose
     `result` is in the closed set {empty, reconciled, contradictory}, and — for a CLEARED result (`empty`
     OR `reconciled`) — a NON-EMPTY `search_scope` AND a NON-EMPTY `reconciliation_evidence`. A record clears
     ONLY with both fields non-empty: for `empty`, the evidence is the named scope that came back clean; for
     `reconciled`, how it was reconciled + the named source. A `contradictory` record, a MISSING record, or a
     cleared result missing either field, for ANY frozen-set rule, FAILS.
  3. NO OVER-TRUST WITHOUT A CLEARED HUNT: no frozen-set rule is stamped better than
     `needs_more_information` in `rule_sets` (i.e. `Observed` / `Assumed`) unless its hunt record cleared
     (`empty` / `reconciled`).

Any predicate violated -> exit 1, each reported on its own problem line.

§2.5 NOTE: the check verifies the hunt records are PRESENT and WELL-FORMED; whether the hunt genuinely
searched its claimed scope is the review's job.

Parsing: PyYAML is REQUIRED. One `yaml.safe_load` path serves both the policy (a `.yaml`) and the coverage
manifest's embedded machine block (see below). PyYAML absent -> exit 2 (setup error); it never degrades to a
line scan. `actor.engine == script`; writer-re-derivable and cwd-independent.

THE COVERAGE MANIFEST'S MACHINE BLOCK. The coverage manifest is a human-readable Markdown roll-up, but its
frozen-set decision, its exclusions, and its per-rule hunt records are ALSO carried in a single fenced
`hunt-coverage` YAML block (delimited by the lines `<!-- HUNT-COVERAGE` and `-->`) so this check reads them
through the one `yaml.safe_load` path rather than scraping prose. The block carries exactly:
`frozen_hunt_set:` (a list of rule ids), `excluded_rules:` (a list of {rule_id, exclusion_note, rationale}),
and `hunt_records:` (a list of {rule_id, search_scope, result, reconciliation_evidence}).

Usage: check-hunt-coverage.sh [--verbose] DECISION_POLICY COVERAGE_MANIFEST
Exit: 0 pass; 1 structural assertion failure; 2 setup error (incl. PyYAML absent, or the machine block absent).
"""
import os
import sys

CLEARED_RESULTS = {"empty", "reconciled"}
RESULT_SET = {"empty", "reconciled", "contradictory"}
CANONICAL_FOUR = {"Observed", "Assumed", "Contradictory", "needs_more_information"}
BLOCK_START = "<!-- HUNT-COVERAGE"
BLOCK_END = "-->"


def die(msg, code):
    sys.stderr.write(msg + "\n")
    sys.exit(code)


def _nonempty(v):
    return isinstance(v, str) and v.strip() != ""


def _import_yaml():
    try:
        import yaml
        return yaml
    except ImportError:
        die("PyYAML is required for the hunt-coverage parse but is not importable. Install it "
            "(pip install pyyaml) and re-run; this check does not fall back to a line scan.", 2)


def load_policy(path):
    if not os.path.isfile(path):
        die("Missing decision policy: %s" % path, 2)
    yaml = _import_yaml()
    try:
        doc = yaml.safe_load(open(path, "r", encoding="utf-8", errors="replace").read())
    except Exception as e:
        die("decision policy is not parseable (JSON or YAML): %s" % e, 1)
    return doc


def load_coverage_block(path):
    """Extract and parse the single fenced `hunt-coverage` YAML block from the coverage manifest."""
    if not os.path.isfile(path):
        die("Missing coverage manifest: %s" % path, 2)
    text = open(path, "r", encoding="utf-8", errors="replace").read()
    # Anchor the block start structurally: the real block opener is `BLOCK_START` as the first
    # non-whitespace on its own line. A mid-line/backticked mention of the marker in prose (e.g. the
    # template's intro documenting the delimiters) is NOT the block opener and must not be matched.
    lines = text.splitlines(keepends=True)
    start_line_idx = None
    for i, line in enumerate(lines):
        if line.lstrip().startswith(BLOCK_START):
            start_line_idx = i
            break
    if start_line_idx is None:
        die("coverage manifest carries no `%s` machine block — this check reads the frozen-set decision, "
            "exclusions, and hunt records through it (not by scraping prose)" % BLOCK_START, 2)
    after = "".join(lines[start_line_idx:]).split(BLOCK_START, 1)[1]
    if BLOCK_END not in after:
        die("coverage manifest `%s` machine block is not closed with `%s`" % (BLOCK_START, BLOCK_END), 2)
    block = after.split(BLOCK_END, 1)[0]
    yaml = _import_yaml()
    try:
        doc = yaml.safe_load(block)
    except Exception as e:
        die("coverage manifest machine block is not parseable YAML: %s" % e, 1)
    if not isinstance(doc, dict):
        die("coverage manifest machine block is not a mapping", 1)
    return doc


def all_rule_ids(policy):
    ids = []
    for rs in (policy.get("rule_sets") if isinstance(policy.get("rule_sets"), list) else []):
        if isinstance(rs, dict):
            for rule in (rs.get("rules") if isinstance(rs.get("rules"), list) else []):
                if isinstance(rule, dict) and _nonempty(rule.get("rule_id")):
                    ids.append(rule["rule_id"])
    return ids


def rule_evidence_by_id(policy):
    out = {}
    for rs in (policy.get("rule_sets") if isinstance(policy.get("rule_sets"), list) else []):
        if isinstance(rs, dict):
            for rule in (rs.get("rules") if isinstance(rs.get("rules"), list) else []):
                if isinstance(rule, dict) and _nonempty(rule.get("rule_id")):
                    out[rule["rule_id"]] = rule.get("evidence_state")
    return out


def validate(policy, cov):
    P = []
    if not isinstance(policy, dict):
        return ["the decision policy is not a mapping"]

    policy_ids = all_rule_ids(policy)
    ev_by_id = rule_evidence_by_id(policy)

    frozen = cov.get("frozen_hunt_set")
    if not (isinstance(frozen, list) and len(frozen) > 0):
        P.append("(1) `frozen_hunt_set` decision record is absent or empty — the ratified freeze decision is "
                 "required")
        frozen = frozen if isinstance(frozen, list) else []
    frozen_set = set(frozen)

    excluded = cov.get("excluded_rules")
    if not isinstance(excluded, list):
        P.append("(1) `excluded_rules` record is absent — every non-frozen rule is accounted for here, never "
                 "silently skipped")
        excluded = []
    excluded_ok = {}
    for e in excluded:
        if not isinstance(e, dict):
            P.append("(1) an `excluded_rules` entry is not a mapping")
            continue
        rid = e.get("rule_id")
        if not _nonempty(rid):
            P.append("(1) an `excluded_rules` entry carries no `rule_id`")
            continue
        if not _nonempty(e.get("exclusion_note")):
            P.append("(1) excluded rule %r carries no ratified `exclusion_note` — an exclusion is "
                     "auditable, never silent" % rid)
        else:
            excluded_ok[rid] = True

    # (1) every policy rule accounted for as frozen XOR excluded-with-a-note; none both
    for rid in policy_ids:
        in_frozen = rid in frozen_set
        in_excluded = rid in excluded_ok
        if in_frozen and in_excluded:
            P.append("(1) rule %r is BOTH frozen and excluded — a rule is one or the other, never both" % rid)
        elif not in_frozen and not in_excluded:
            P.append("(1) rule %r is accounted for as NEITHER frozen NOR excluded-with-a-ratified-note — no "
                     "rule may be silently unaccounted" % rid)

    # (2) a cleared, well-formed hunt record for every frozen-set rule
    records = cov.get("hunt_records") if isinstance(cov.get("hunt_records"), list) else []
    rec_by_id = {}
    for r in records:
        if isinstance(r, dict) and _nonempty(r.get("rule_id")):
            rec_by_id[r["rule_id"]] = r
    for rid in frozen:
        rec = rec_by_id.get(rid)
        if rec is None:
            P.append("(2) frozen-set rule %r has NO hunt record — every frozen rule carries a cleared record"
                     % rid)
            continue
        result = rec.get("result")
        if result not in RESULT_SET:
            P.append("(2) frozen-set rule %r hunt `result` is %r — must be one of {empty, reconciled, "
                     "contradictory}" % (rid, result))
            continue
        if result == "contradictory":
            P.append("(2) frozen-set rule %r hunt `result` is `contradictory` (unreconciled) — it must be "
                     "reconciled (recorded `reconciled` with evidence) or the rule dropped from trust and "
                     "re-frozen / handled; a contradiction does not clear" % rid)
            continue
        # cleared result: require BOTH fields non-empty
        if not _nonempty(rec.get("search_scope")):
            P.append("(2) frozen-set rule %r hunt (result: %s) carries no non-empty `search_scope` — a "
                     "cleared record names the scope it covered" % (rid, result))
        if not _nonempty(rec.get("reconciliation_evidence")):
            P.append("(2) frozen-set rule %r hunt (result: %s) carries no non-empty `reconciliation_evidence` "
                     "— for `empty`, the named clean scope; for `reconciled`, how it was reconciled + the "
                     "source" % (rid, result))

    # (3) no over-trust without a cleared hunt
    for rid in frozen:
        es = ev_by_id.get(rid)
        rec = rec_by_id.get(rid)
        cleared = isinstance(rec, dict) and rec.get("result") in CLEARED_RESULTS \
            and _nonempty(rec.get("search_scope")) and _nonempty(rec.get("reconciliation_evidence"))
        if es in ("Observed", "Assumed") and not cleared:
            P.append("(3) frozen-set rule %r is trusted %r in `rule_sets` but its hunt did not clear — a "
                     "rule stays no better than needs_more_information until its hunt clears" % (rid, es))

    return P


def main(argv):
    verbose = False
    rest = []
    for a in argv:
        if a in ("--verbose", "-v"):
            verbose = True
        else:
            rest.append(a)
    if len(rest) != 2:
        die("usage: check-hunt-coverage.sh [--verbose] DECISION_POLICY COVERAGE_MANIFEST", 2)
    policy_path, cov_path = rest
    policy = load_policy(policy_path)
    cov = load_coverage_block(cov_path)

    problems = validate(policy, cov)
    if verbose:
        sys.stderr.write("parse engine: pyyaml safe_load (policy + the coverage manifest's hunt-coverage "
                         "block)\n")
    if problems:
        die("hunt-coverage check FAILED for %s:\n  - %s" % (cov_path, "\n  - ".join(problems)), 1)
    print("PASS: the frozen stress-test set is recorded, every policy rule is accounted for as frozen or "
          "excluded-with-a-ratified-note, every frozen rule's hunt cleared with a named scope and "
          "reconciliation evidence, and no frozen rule is over-trusted. (Structure only — whether the hunt "
          "genuinely searched is the review's job.)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
