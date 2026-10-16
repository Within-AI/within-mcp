#!/usr/bin/env python3
"""Grade simulation results against the frozen expected values, with critical gating.

A case passes when its label is the expected label or one of its allowed alternatives and every
declared expected value of that outcome matches: amounts within 0.01; booleans, text, null and
not applicable by type and value (a boolean never equals a number; "" , "explanation not found",
null and not applicable are four different values; whitespace-only text is the empty answer "");
and each declared `amount_direction`, read from the actual value `<amount>.amount_direction`
(present on one side only fails; absent on both passes). A missing actual value fails; extra
actual values are ignored. One failed critical case fails the suite. The
non-critical pass share is reported with no numeric floor. Judge verdicts are advisory and
change no result.
"""
import os
import sys
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import compute_expected  # noqa: E402

TOLERANCE = Decimal("0.01")


def _number(value):
    return compute_expected._decimal(value)


DIRECTION_SUFFIX = ".amount_direction"
_ABSENT = object()


def _same_typed(want, got):
    want, got = compute_expected.typed(want), compute_expected.typed(got)
    if isinstance(want, bool) or isinstance(got, bool):
        return isinstance(want, bool) and isinstance(got, bool) and want == got
    if want is None or got is None:
        return want is None and got is None
    return type(want) is type(got) and want == got


def _outcome_problems(amounts, values, typed_values=None, directions=None):
    problems = []
    values = values if isinstance(values, dict) else {}
    for name in sorted(amounts):
        raw = values.get(name)
        got = None if isinstance(raw, bool) else _number(raw)
        want = _number(amounts[name])
        if want is None:
            problems.append("expected amount %r is null or not a number" % name)
        elif got is None:
            problems.append("amount %r is missing, null or not a number" % name)
        elif abs(got - want) > TOLERANCE:
            problems.append("amount %r is %s, expected %s" % (name, got, amounts[name]))
    for name in sorted(typed_values or {}):
        want = typed_values[name]
        if name not in values:
            problems.append("value %r is missing, expected %r" % (name, want))
        elif not _same_typed(want, values[name]):
            problems.append("value %r is %r, expected %r" % (name, values[name], want))
    declared = dict(directions or {})
    for key in values:
        if isinstance(key, str) and key.endswith(DIRECTION_SUFFIX) and key[:-len(DIRECTION_SUFFIX)] in amounts:
            declared.setdefault(key[:-len(DIRECTION_SUFFIX)], _ABSENT)
    for name in sorted(declared):
        want = declared[name]
        got = values.get(name + DIRECTION_SUFFIX, _ABSENT)
        if want is _ABSENT:
            problems.append("amount %r carries amount_direction %r, the expected value declares none" % (name, got))
        elif got is _ABSENT:
            problems.append("amount %r has no amount_direction, expected %r" % (name, want))
        elif got != want:
            problems.append("amount %r amount_direction is %r, expected %r" % (name, got, want))
    return problems


def grade_case(expected_case, label, values):
    outcomes = [expected_case] + list(expected_case.get("alternatives") or [])
    values = dict(values) if isinstance(values, dict) else {}
    values.setdefault("outcome", label)  # the label is the outcome value when the result does not repeat it
    tried = []
    for outcome in outcomes:
        if outcome.get("label") != label:
            continue
        problems = _outcome_problems(outcome.get("amounts") or {}, values, outcome.get("values") or {},
                                     outcome.get("directions") or {})
        if not problems:
            which = "expected" if outcome is expected_case else "allowed alternative"
            return {"pass": True, "matched": label, "reason": "label %r matches the %s outcome" % (label, which)}
        tried.extend(problems)
    if not tried:
        allowed = [o.get("label") for o in outcomes]
        return {"pass": False, "matched": None,
                "reason": "label %r is not the expected label or an allowed alternative (%s)"
                          % (label, ", ".join(repr(a) for a in allowed))}
    return {"pass": False, "matched": None, "reason": "label %r: %s" % (label, "; ".join(tried))}


def grade(run_dir, results, judge_verdicts=None):
    """results: list of {"case_id", "label", "values"}. judge_verdicts are advisory and ignored."""
    frozen = compute_expected.load_frozen(run_dir)
    by_case = {}
    for result in results or []:
        by_case.setdefault(result.get("case_id"), []).append(result)
    known = set()
    cases = []
    for case in frozen.get("cases") or []:
        cid = case.get("id")
        known.add(cid)
        found = by_case.get(cid, [])
        if not found:
            verdict = {"pass": False, "matched": None, "reason": "no result for this case"}
        elif len(found) > 1:
            verdict = {"pass": False, "matched": None, "reason": "%d results for this case" % len(found)}
        else:
            verdict = grade_case(case, found[0].get("label"), found[0].get("values"))
        cases.append(dict(verdict, id=cid, critical=bool(case.get("critical"))))
    excluded = []
    for case in frozen.get("excluded") or []:
        known.add(case.get("id"))
        excluded.append({"id": case.get("id"), "critical": bool(case.get("critical")), "reason": case.get("excluded")})
    unknown = sorted({str(r.get("case_id")) for r in results or [] if r.get("case_id") not in known})
    critical_failed = [c["id"] for c in cases if c["critical"] and not c["pass"]]
    critical_excluded = [c["id"] for c in excluded if c["critical"]]
    non_critical = [c for c in cases if not c["critical"]]
    share = (sum(1 for c in non_critical if c["pass"]) / float(len(non_critical))) if non_critical else None
    if critical_failed:
        suite = "fail"
        reason = "critical case failed: %s" % ", ".join(critical_failed)
    elif critical_excluded:
        suite = "incomplete"
        reason = "critical case excluded (stamped open): %s" % ", ".join(critical_excluded)
    else:
        suite = "pass"
        reason = "every critical case passed"
    return {"suite": suite, "reason": reason, "cases": cases, "excluded": excluded,
            "unknown_results": unknown, "critical_failed": critical_failed,
            "critical_excluded": critical_excluded, "non_critical_pass_share": share,
            "numeric_floor": None, "judge_verdicts": "advisory; not used in grading"}
