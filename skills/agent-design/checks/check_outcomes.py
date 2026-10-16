#!/usr/bin/env python3
"""Outcome checks for the rules stage (U13): labels listed, outcomes exhaustive and non-overlapping on the value grid.

Usage: check_outcomes.py --run RUN [--out REPORT]; exit 0 pass, 1 fail, 2 usage or unreadable.
Standard library only. Expressions are evaluated only through scripts/expr.py. The rules, open-items and outcomes
outputs are read only through formats/readers.py. Each decision's grid is streamed and stops at
check_rules.GRID_BUDGET points; an uncovered remainder is reported as grid-partial, never as a pass.
"""
import itertools
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "scripts"))
import expr  # noqa: E402
import check_rules as cr  # noqa: E402
import readers  # noqa: E402  (check_rules puts formats/ on the path)

FALLBACK = ("Check that every label a rule sets, a test case expects or the simulation records is listed for its "
            "decision in stages/rules/outcomes.json, and that for each decision exactly one outcome holds on every "
            "combination of boundary values.")


def _label_uses(node, keys, decision, out, where):
    """(label, decision or None, where) for every value of the named keys, at any depth. A case's `rows` are
    skipped: each row value carries an evidence label (observed, synthetic), not an outcome label."""
    if isinstance(node, dict):
        decision = node.get("decision", decision) if isinstance(node.get("decision", decision), str) else decision
        for k, v in node.items():
            if k == "rows":
                continue
            if k in keys:
                for val in (v if isinstance(v, list) else [v]):
                    if isinstance(val, str):
                        out.append((val, decision, "%s.%s" % (where, k)))
            _label_uses(v, keys, decision, out, "%s.%s" % (where, k))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            _label_uses(v, keys, decision, out, "%s[%d]" % (where, i))


def _set_labels(result, allowed):
    """The outcome labels an evaluated rule sets: its evaluated `sets` values that are labels of its decision."""
    return frozenset(v for v in result["sets"].values() if isinstance(v, str) and v in allowed)


def _primitive_form_problems(outcomes):
    """The agent's primitive form and the decision it rests on, which the hand-off carries."""
    where = "stages/rules/outcomes.json primitive_form"
    if "primitive_form" not in outcomes:
        return [{"defect": "missing-primitive-form", "where": where,
                 "detail": "outcomes.json has no primitive_form {value, decision_ref}"}]
    pf = outcomes["primitive_form"]
    if not isinstance(pf, dict) or not isinstance(pf.get("value"), str) or not pf["value"].strip():
        return [{"defect": "missing-primitive-form", "where": where, "detail": "primitive_form value is empty"}]
    ref = pf.get("decision_ref")
    if not isinstance(ref, str) or not ref.strip():
        return [{"defect": "primitive-form-no-decision", "where": where,
                 "detail": "primitive_form names no decision_ref"}]
    ids = {d.get("id") for d in outcomes.get("decisions") or [] if isinstance(d, dict)}
    if ref not in ids:
        return [{"defect": "primitive-form-no-decision", "where": where,
                 "detail": "primitive_form decision_ref %s is not a decision in outcomes.json" % ref}]
    return []


def check(run_dir):
    run_rel = "checks/check_outcomes.json"
    try:
        rules_doc, rule_findings = cr.load_rules(run_dir)
        outcomes, outcome_findings = readers.load_outcomes(run_dir)
        open_items, item_findings = cr.load_open_items(run_dir)
        cr.read_upstream(run_dir)
        suite = cr.load_json(run_dir, "stages/test-suite/test-suite.json", required=False)
        sim_input = cr.load_json(run_dir, "simulation/sim-input.json", required=False)
        walk = cr.load_json(run_dir, "simulation/walk.json", required=False)
        field_findings = []
        types = cr.run_types(run_dir, rules_doc["rules"], field_findings)
    except cr.Unreadable as exc:
        return cr.error_report("check_outcomes", run_rel, str(exc), FALLBACK)

    problems = cr.shape_problems(rule_findings + outcome_findings + item_findings + field_findings)
    problems += _primitive_form_problems(outcomes)
    labels = {}
    for group in ("decisions", "gates"):
        for d in outcomes.get(group) or []:
            if isinstance(d, dict):
                labels.setdefault(d.get("id"), set()).update(x for x in d.get("labels") or [] if isinstance(x, str))
    every = set().union(*labels.values()) if labels else set()
    rules = rules_doc["rules"]

    uses = []
    for rule in rules:
        if not cr.has_expression(rule):
            continue
        for name, node in (rule["expression"].get("sets") or {}).items():
            if isinstance(node, dict) and node.get("kind") == "literal" and isinstance(node.get("value"), str):
                uses.append((node["value"], rule.get("decision"), "rules.json rule %s sets.%s" % (rule.get("id"), name)))
    if suite is not None:
        _label_uses(suite, ("label", "final_label"), None, uses, "test-suite.json")
    if sim_input is not None:
        _label_uses(sim_input, ("final_label",), None, uses, "sim-input.json")
    if walk is not None:
        _label_uses(walk, ("outcome_expected", "outcome_actual"), None, uses, "walk.json")
    for label, decision, where in uses:
        allowed = labels.get(decision, set()) if decision is not None else every
        if label not in allowed:
            if decision is not None and decision not in labels:
                why = "decision %s is not in outcomes.json, so it has no labels" % decision
            else:
                why = "it is not listed%s in outcomes.json" % (" for decision %s" % decision if decision else "")
            problems.append({"defect": "label-unlisted", "where": where,
                             "detail": "label %r: %s" % (label, why)})

    by_decision = cr.decision_groups(rules)
    for d in outcomes.get("decisions") or []:
        if isinstance(d, dict):
            by_decision.setdefault(d.get("id"), [])
    missing_for = {}
    for item in open_items:
        if cr.stamped(item):
            missing_for.setdefault(item.get("decision"), set()).update(item.get("missing") or [])

    skipped = 0
    points = 0
    coverage = {}
    for decision in sorted(by_decision, key=str):
        group = by_decision[decision]
        gaps, first_gap, overlap, excused = 0, None, None, False
        own = labels.get(decision, every)
        unlisted = {}
        exprs = [r["expression"] for r in group]
        evaluated = 0
        for row in itertools.islice(cr.grid(exprs, types), cr.GRID_BUDGET):
            points += 1
            evaluated += 1
            holding, raised = [], set()
            for rule in group:
                if cr.is_contradictory(rule):
                    continue
                try:
                    result = expr.evaluate(rule["expression"], row)
                    if result["test"]:
                        holding.append((rule, _set_labels(result, own)))
                        for name, node in (rule["expression"].get("sets") or {}).items():
                            value = result["sets"].get(name)
                            if (not (isinstance(node, dict) and node.get("kind") == "literal")
                                    and isinstance(value, str) and value not in own):
                                unlisted.setdefault((str(rule.get("id")), name), (value, row))
                except expr.MissingInput as exc:
                    raised.add(exc.token)
                except expr.ExprError:
                    pass
            if overlap is None:
                for a, la in holding:
                    for b, lb in holding:
                        if a is not b and la and lb and la != lb and overlap is None:
                            overlap = (a, b, row)
            if not holding:
                if raised & missing_for.get(decision, set()) or (not group and decision in missing_for):
                    excused = True
                else:
                    gaps += 1
                    first_gap = row if first_gap is None else first_gap
        total = cr.grid_size(exprs, types)
        coverage[str(decision)] = {"evaluated": evaluated, "total": total, "partial": evaluated < total}
        if evaluated < total:
            problems.append({"defect": "grid-partial", "where": "decision %s" % decision,
                             "detail": "grid budget %d reached: %d of %d combinations evaluated; the rest are "
                                       "unchecked, so this decision is not passed"
                                       % (cr.GRID_BUDGET, evaluated, total),
                             "coverage": coverage[str(decision)]})
        for (rid, name), (value, row) in sorted(unlisted.items()):
            problems.append({"defect": "label-unlisted", "where": "rules.json rule %s sets.%s" % (rid, name),
                             "detail": "label %r, set at %s: it is not listed for decision %s in outcomes.json"
                                       % (value, json.dumps(row, sort_keys=True), decision)})
        if overlap:
            a, b, row = overlap
            problems.append({"defect": "outcome-overlap", "where": "decision %s" % decision,
                             "detail": "rules %s and %s set different outcomes and both hold at %s"
                                       % (a.get("id"), b.get("id"), json.dumps(row, sort_keys=True))})
        if gaps:
            problems.append({"defect": "outcome-gap", "where": "decision %s" % decision,
                             "detail": "no rule holds at %d grid point(s), first %s"
                                       % (gaps, json.dumps(first_gap, sort_keys=True))})
        if excused:
            skipped += 1
            problems.append({"defect": "NOTE", "where": "decision %s" % decision,
                             "detail": "NOTE: decision %s skipped for the gap check where a stamped open item names "
                                       "the missing token" % decision})

    counts = {"decisions": len(by_decision), "grid_points": points, "label_uses": len(uses),
              "skipped_open": skipped}
    return cr.report("check_outcomes", run_rel, problems, counts, FALLBACK, {"grid_coverage": coverage})


def main(argv=None):
    parser = argparse.ArgumentParser(description="Agent Design outcome checks")
    parser.add_argument("--run", required=True)
    parser.add_argument("--out")
    try:
        args = parser.parse_args(argv)
    except SystemExit:
        return 2
    if not os.path.isdir(args.run):
        print("run folder not found: %s" % args.run, file=sys.stderr)
        return 2
    rep = check(args.run)
    out = args.out or os.path.join(args.run, "checks", "check_outcomes.json")
    if not cr.write_report(rep, out):
        return 2
    cr.print_report(rep)
    return rep["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
