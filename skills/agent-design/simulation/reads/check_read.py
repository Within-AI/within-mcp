#!/usr/bin/env python3
"""Check the reader's verdict on each rule's expression against its prose.

Usage: check_read.py --run RUN [--out report.json]   (default report RUN/checks/check_read.json)

Validates RUN/simulation/reads/encoding-verdict.json against formats/lists/verdict.schema.json and against
the rules in stages/rules/rules.json: every rule needs exactly one verdict item whose `subject` is its id,
and no item may name a rule that does not exist. A `finding` or `cannot_judge` item is not a failure: it is
listed in the report's `residuals` by rule id, as a simulation residual for the register.
Exit 0 pass; 1 fail; 2 a required file missing or unreadable.
"""
import os
import sys

import _common

VERDICT = "simulation/reads/encoding-verdict.json"
VERDICT_SCHEMA = os.path.join(_common.LISTS, "verdict.schema.json")
NAME = "check_read"


def check(run_dir):
    problems, residuals = [], []
    try:
        rules = _common.load(run_dir, _common.RULES)
        rule_ids = [r["id"] for r in rules.get("rules", []) if isinstance(r, dict) and "id" in r]
    except _common.Unreadable as exc:
        return _common.report(NAME, [{"defect": "rules-unreadable", "where": _common.RULES, "detail": str(exc),
                                      "error": True}], {}, residuals=[])
    try:
        verdict = _common.load(run_dir, VERDICT, VERDICT_SCHEMA)
    except _common.Unreadable as exc:
        return _common.report(NAME, [{"defect": "verdict-missing", "where": VERDICT, "detail": str(exc),
                                      "error": True}], {"rules": len(rule_ids)}, residuals=[])
    except _common.Invalid as exc:
        return _common.report(NAME, [{"defect": "input-invalid", "where": VERDICT, "detail": str(exc)}],
                              {"rules": len(rule_ids)}, residuals=[])
    seen = {}
    for n, item in enumerate(verdict["items"]):
        subject = item["subject"]
        seen[subject] = seen.get(subject, 0) + 1
        if subject not in rule_ids:
            problems.append({"defect": "verdict-unknown-rule", "where": "%s items[%d]" % (VERDICT, n),
                             "detail": "verdict names rule %s, which is not in %s" % (subject, _common.RULES)})
        elif item["verdict"] != "ok":
            residuals.append({"rule_id": subject, "verdict": item["verdict"], "detail": item["detail"]})
    for rid in rule_ids:
        if rid not in seen:
            problems.append({"defect": "rule-without-verdict", "where": VERDICT,
                             "detail": "rule %s has no verdict" % rid})
        elif seen[rid] > 1:
            problems.append({"defect": "rule-with-several-verdicts", "where": VERDICT,
                             "detail": "rule %s has %d verdicts" % (rid, seen[rid])})
    counts = {"rules": len(rule_ids), "verdicts": len(verdict["items"]), "residuals": len(residuals)}
    return _common.report(NAME, problems, counts, residuals=residuals)


def main(argv=None):
    return _common.check_main(NAME, check, argv)


if __name__ == "__main__":
    sys.exit(main())
