#!/usr/bin/env python3
"""Turn the Agent Design test suite into Agent Build case and expected files, mechanically.

Usage: cases.py from-design --run R --out DIR

Reads the Agent Design test suite (agent-design.suite/1; usually R/inputs/agent-design/stages/test-suite/test-suite.json,
found wherever the design landed by _locate.locate_design) and writes, per suite case,
DIR/cases/<case_id>.json (pins G1) and DIR/expected/<case_id>.json (pins P5), plus DIR/unconvertible.json
([{case_id, reason}]). The reading of case ids, gate ids and values follows Agent Design's own convert_cases.py.

Nothing is guessed. A missing, unreadable or conflicting field makes that case's expected file a single candidate
with no outcome and a top-level pending_reason naming the field, and lists the case in unconvertible.json with a
reason from REASONS. A stamped-open case becomes `exclusion: true` and is listed too.

Runnable input contains only source rows, unchanged. Holds and judgment answers are evaluation material:
they still inform rule references, validation and judgement_needed, but are never supplied to the agent.

Derivations: the top-level `derivations` is the primary expectation's, copied unchanged; each candidate also carries
its own outcome's derivations unchanged, so an alternative's calculation is kept. Missing derivations read as none.

Expected values: numbers go to the candidate's `values` (tolerance applies); true, false, null and strings are
exact-match expectations carried in the candidate's outcome object beside the label, types preserved.

Label sets: each Agent Design case decides one decision, so the outcome object is {<decision id>: <label>}.
judgement_needed: true when the case has a judgment answer for a rule and item with no scripted holds value
(true or false) for the same rule and item.

Exit 0 written (at least one convertible case); 1 written, but no case was convertible; 2 usage, or an empty or
unreadable suite (nothing written).
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _records  # noqa: E402
import _locate  # noqa: E402

SUITE = "inputs/agent-design/stages/test-suite/test-suite.json"
SOURCE = "agent-design-suite"
NMI = "needs_more_information"
GATE_PREFIX = "gate:"
# The expected-values field that holds the decision's label (the field Agent Design's rules set).
LABEL_FIELD = "outcome"
HOLDS_VALUES = (True, False, NMI, "not_applicable")

REASONS = {
    "case-no-id": "case has no id",
    "duplicate-case-id": "case id shared with another case",
    "case-stamped-open": "case is a stamped-open exclusion",
    "no-final-label": "case has no final label",
    "label-conflict": "final label differs from the expected outcome's label",
    "no-expected": "case has no expected outcome object",
    "expected-values-missing": "expected or alternative outcome has no values",
    "expected-values-unreadable": "expected values is not an object",
    "derivations-unreadable": "expected derivations is not an object",
    "alternative-unreadable": "allowed alternative is not an outcome with a label",
    "critical-unreadable": "critical is not true or false",
    "rows-unreadable": "rows is missing or not a list of rows with ids",
    "holds-unreadable": "holds is not a list of {rule_id, item_key, holds}",
    "judgment-unreadable": "judgment answers is not a list of {rule_id, item_key, answer}",
    "no-decision": "case has no decision id to name its label set",
}


def _is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _list_of_dicts(value):
    return isinstance(value, list) and all(isinstance(x, dict) for x in value)


def _nonempty_str(value):
    return isinstance(value, str) and value != ""


def process_of(case):
    """The process id the suite gives: the part of the decision id before ':' (as convert_cases qualifies steps)."""
    decision = case.get("decision")
    if _nonempty_str(decision) and ":" in decision and decision.split(":", 1)[0]:
        return decision.split(":", 1)[0]
    return NMI


def rule_ids(case):
    """Rule and gate ids the case names, in order; a `gate:<id>` reference is read as the gate's own id."""
    out = []
    for field in ("holds", "judgment_answers"):
        entries = case.get(field)
        for e in entries if isinstance(entries, list) else []:
            rid = e.get("rule_id") if isinstance(e, dict) else None
            if not _nonempty_str(rid):
                continue
            if rid.startswith(GATE_PREFIX):
                rid = rid[len(GATE_PREFIX):]
            if rid and rid not in out:
                out.append(rid)
    return out


def _input_problems(case):
    """Reason codes for source rows and evaluation holds/judgment answers; no source fields are rewritten."""
    found = []
    rows = case.get("rows")
    if not _list_of_dicts(rows) or not rows or not all(_nonempty_str(r.get("id")) for r in rows) \
            or not all(isinstance(r.get("values"), dict) for r in rows):
        found.append("rows-unreadable")
    holds = case.get("holds")
    if not _list_of_dicts(holds) or not all(
            _nonempty_str(h.get("rule_id")) and _nonempty_str(h.get("item_key"))
            and any(h.get("holds") is v if isinstance(v, bool) else h.get("holds") == v for v in HOLDS_VALUES)
            for h in holds):
        found.append("holds-unreadable")
    answers = case.get("judgment_answers")
    if not _list_of_dicts(answers) or not all(
            _nonempty_str(j.get("rule_id")) and _nonempty_str(j.get("item_key")) and _nonempty_str(j.get("answer"))
            for j in answers):
        found.append("judgment-unreadable")
    return found


def _candidate(decision, label, outcome):
    """(candidate, reason code or None) for one Agent Design outcome with its label.

    Numbers go to `values` (compared within the tolerance). Every other JSON scalar (true, false, null, a string)
    is an exact-match expectation, carried as its own entry in the outcome object (name -> value, type preserved),
    which the comparison matches exactly and type-aware like a label: an authority result such as "the agent created
    no record" must match exactly, never be dropped. Only the outcome field (LABEL_FIELD, the field the design's rules
    set) is the label itself; every other string is kept, even one equal to the label. Missing derivations read as
    none."""
    if "values" not in outcome:
        return None, "expected-values-missing"
    values = outcome["values"]
    if not isinstance(values, dict):
        return None, "expected-values-unreadable"
    derivations = outcome.get("derivations", {})
    if derivations is None:
        derivations = {}
    if not isinstance(derivations, dict):
        return None, "derivations-unreadable"
    numbers, exact = {}, {decision: label}
    for name, value in values.items():
        if name == LABEL_FIELD:
            if value != label:
                return None, "label-conflict"  # the outcome field disagrees with the label
            continue  # the outcome field holds the label itself
        if name == decision:
            if not isinstance(value, str) or value != label:
                return None, "label-conflict"  # a value for the decision itself that differs from the label
            continue  # the decision's own value repeats the label: one expectation, kept once
        if _is_number(value):
            numbers[name] = value
        elif isinstance(value, (dict, list)):
            return None, "expected-values-unreadable"
        else:
            exact[name] = value
    return {"outcome": exact, "no_verdict": False, "no_verdict_reason": None, "values": numbers,
            "artifact_checks": [], "derivations": derivations}, None


def _key(entry):
    """(rule_id, item_key) when both are strings, else None (a malformed entry scripts nothing)."""
    if isinstance(entry, dict) and isinstance(entry.get("rule_id"), str) and isinstance(entry.get("item_key"), str):
        return entry["rule_id"], entry["item_key"]
    return None


def _judgement_needed(case):
    holds = case.get("holds") if isinstance(case.get("holds"), list) else []
    answers = case.get("judgment_answers") if isinstance(case.get("judgment_answers"), list) else []
    scripted = set(_key(h) for h in holds if _key(h) and isinstance(h.get("holds"), bool))
    return any(isinstance(j, dict) and _key(j) not in scripted for j in answers)


def _pending(case_id, critical, exclusion, judgement, reason_code, detail=""):
    reason = "%s: %s" % (reason_code, REASONS[reason_code])
    if detail:
        reason += " (%s)" % detail
    return {"case_id": case_id, "candidates": [{"outcome": None, "no_verdict": False, "no_verdict_reason": None,
                                                "values": {}, "artifact_checks": []}],
            "tolerance": None, "critical": critical, "exclusion": exclusion, "judgement_needed": judgement,
            "derivations": {}, "pending_reason": reason}


def convert_case(case):
    """(case file, expected file, reason code or None) for one suite case with a usable unique id."""
    cid = case["id"]
    case_file = {"case_id": cid, "process": process_of(case), "source": SOURCE, "rules_under_test": rule_ids(case),
                 "input": {"rows": case["rows"]} if "rows" in case else {}}
    critical = case.get("critical")
    critical_ok = critical is None or isinstance(critical, bool)
    critical = critical if isinstance(critical, bool) else None
    if "exclusion" in case:
        missing = (case.get("exclusion") or {}).get("missing", "") if isinstance(case.get("exclusion"), dict) else ""
        return case_file, _pending(cid, critical, True, False, "case-stamped-open", missing), "case-stamped-open"
    judgement = _judgement_needed(case)
    problems = _input_problems(case)
    if not critical_ok:
        problems.append("critical-unreadable")
    decision = case.get("decision")
    if not _nonempty_str(decision):
        problems.append("no-decision")
    label = case.get("final_label")
    expected = case.get("expected")
    if not _nonempty_str(label):
        problems.append("no-final-label")
    if not isinstance(expected, dict):
        problems.append("no-expected")
    elif _nonempty_str(label) and expected.get("label") != label:
        problems.append("label-conflict")
    candidates, derivations = [], {}
    if not problems:
        cand, code = _candidate(decision, label, expected)
        if code:
            problems.append(code)
        else:
            candidates.append(cand)
            derivations = cand["derivations"]
        alts = case.get("allowed_alternatives")
        alts = [] if alts is None else alts  # no alternatives declared (absent or null)
        if not _list_of_dicts(alts) or not all(_nonempty_str(a.get("label")) for a in alts):
            problems.append("alternative-unreadable")
        else:
            for alt in alts:
                cand, code = _candidate(decision, alt["label"], alt)
                if code:
                    problems.append(code)
                    break
                candidates.append(cand)
    if problems:
        return case_file, _pending(cid, critical, False, judgement, problems[0],
                                   ", ".join(problems[1:]) and "also: " + ", ".join(problems[1:])), problems[0]
    expected_file = {"case_id": cid, "candidates": candidates, "tolerance": None, "critical": critical,
                     "exclusion": False, "judgement_needed": judgement, "derivations": derivations}
    return case_file, expected_file, None


def suite_path(run):
    """(absolute path, run-relative path) of the design's test suite, located wherever the design landed;
    (None, the usual name) when the locator found none or refused it (outside the run): read as missing."""
    loc = _locate.locate_design(run)
    if loc.suite:
        return loc.suite, loc.rel(loc.suite)
    return None, SUITE


def load_suite(run):
    """(cases, error): the suite's case list, or None and a reason for an empty or unreadable suite."""
    path, name = suite_path(run)
    suite, err = _records.load_json(path) if path else (None, "missing")
    if err:
        return None, "cannot read %s: %s" % (name, err)
    suite = _locate.normalise_suite(suite, [])  # key names and step ids only; values untouched
    cases = suite.get("cases") if isinstance(suite, dict) else None
    if not isinstance(cases, list):
        return None, "%s has no cases list" % name
    if not cases:
        return None, "%s has no cases" % name
    if not all(isinstance(c, dict) for c in cases):
        return None, "%s has a case that is not an object" % name
    return cases, None


def from_design(run, out):
    """Write the files; return the exit code."""
    cases, err = load_suite(run)
    if err:
        print(err, file=sys.stderr)
        return _records.USAGE
    rel = suite_path(run)[1]
    if rel != SUITE:
        print("cases.py: test suite read from %s (the design is not at the usual place)" % rel)
    counts = {}
    for c in cases:
        cid = c.get("id")
        if _nonempty_str(cid):
            counts[cid] = counts.get(cid, 0) + 1
    writes, unconvertible, converted, done = [], [], 0, set()
    for c in cases:
        cid = c.get("id")
        if not _nonempty_str(cid):
            unconvertible.append({"case_id": None, "reason": "case-no-id"})
            continue
        if counts[cid] > 1:
            if cid not in done:
                done.add(cid)
                unconvertible.append({"case_id": cid, "reason": "duplicate-case-id"})
                writes.append(("expected", cid, _pending(cid, None, False, False, "duplicate-case-id")))
            continue
        case_file, expected_file, code = convert_case(c)
        writes.append(("cases", cid, case_file))
        writes.append(("expected", cid, expected_file))
        if code:
            unconvertible.append({"case_id": cid, "reason": code})
        else:
            converted += 1
    for sub in ("cases", "expected"):
        os.makedirs(os.path.join(out, sub), exist_ok=True)
    for sub, cid, obj in writes:
        _records.write_record(os.path.join(out, sub, "%s.json" % cid), obj)
    _records.write_record(os.path.join(out, "unconvertible.json"), unconvertible)
    print("cases.py: %d cases, %d converted, %d unconvertible" % (len(cases), converted, len(unconvertible)))
    for u in unconvertible:
        print("unconvertible: %s: %s" % (u["case_id"], u["reason"]))
    return _records.OK if converted else _records.FAILED


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="command")
    f = sub.add_parser("from-design")
    f.add_argument("--run", required=True)
    f.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    if args.command != "from-design":
        ap.print_usage(sys.stderr)
        return _records.USAGE
    if not os.path.isdir(args.run):
        print("--run %s is not a folder" % args.run, file=sys.stderr)
        return _records.USAGE
    return from_design(args.run, args.out)


if __name__ == "__main__":
    sys.exit(main())
