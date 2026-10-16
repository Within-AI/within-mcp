#!/usr/bin/env python3
"""Walk every case of the simulation input and compare it with the frozen expected values.

Usage: walk.py --run RUN

Reads simulation/sim-input.json, stages/rules/rules.json (read only, for the rule count) and the frozen
stages/expected-values/expected.json (through compute_expected.load_frozen). Each step evaluates its rule's
structured expression with expr.evaluate over the case's own values; nothing is run as code. Writes
simulation/walk.json on every exit once RUN exists.

A case for a step a person decides carries `person_decision: {"gate", "decision"}` as synthetic data: the walk
plays that decision at the gate (a step with part "person"), writing it as `person.decision` only. The decision is
an input, never the graded outcome: the walk then runs the case's held rules, which are what the agent does next,
and grades what they write against the case's expected next step. A case with no agent step after the decision
cannot pass: it is walked and graded "differs" with that reason. A decision with `"ends_process": true` writes
`next_step: "ended"` and is graded on that terminal state and the case's other expected values (the case must
expect `next_step: "ended"`); its label is graded only if an agent step set one. A missing or unmatched decision,
or a case for a gate a person decides (`gate`, marked `person_decides` in the input's gates) that states none,
leaves the case unrun with the recorded reason "can't be walked: decision missing". A case convert_cases marks
`handover: <gate id>` (no decision, and an expected result that does not depend on one) tests the hand-over: its held
rules are walked, it ends at the gate, and it is graded on reaching the gate and its non-outcome values, not on a label.

Exit 0 every case matches; 1 any case does not; 2 unreadable input or a frozen-hash mismatch.
"""
import argparse
import json
import os
import re
import sys
from decimal import Decimal

HERE = os.path.dirname(os.path.abspath(__file__))
AD = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(AD, "scripts"))
sys.path.insert(0, HERE)
import compute_expected  # noqa: E402
import expr  # noqa: E402
import grade  # noqa: E402
import schema_check  # noqa: E402

SIM_INPUT = "simulation/sim-input.json"
WALK = "simulation/walk.json"
RULES = "stages/rules/rules.json"
EXPECTED = "stages/expected-values/expected.json"
SIM_INPUT_SCHEMA = os.path.join(AD, "formats", "run", "sim-input.schema.json")
WALK_SCHEMA = os.path.join(AD, "formats", "run", "simulation.schema.json")
RULE_SCHEMA = os.path.join(AD, "formats", "design", "rule.schema.json")

# Legacy expression-language output token; explicit decision/output declarations take precedence.
OUTCOME = "outcome"
DECIMAL_TEXT = re.compile(r"^-?\d+(\.\d+)?$")
DROPPED = ("execution_error", "not_executed", "needs_more_information")
NEXT_STEP = "next_step"
PERSON_DECISION = "person.decision"  # the token the agent's next rules read the person's decision from
DECISION_MISSING = "can't be walked: decision missing"
HANDOVER = "handover"  # convert_cases marks a case that tests the hand-over to a person (no decision played)
NO_AGENT_STEP = "no_agent_step"  # walk_case's marker; the case is walked and differs


class InputProblem(Exception):
    """An unreadable input; the message is walk.json's run-level error."""


def _plain(value):
    """JSON-safe copy: amounts as decimal strings (pin I8)."""
    if isinstance(value, bool):
        return value
    if isinstance(value, Decimal):
        return compute_expected._text(value)
    if isinstance(value, int):
        return compute_expected._text(Decimal(value))
    if isinstance(value, float):
        return compute_expected._text(Decimal(repr(value)))
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_plain(v) for v in value]
    return value


def _number_or_text(value):
    """A decimal string is an amount: parsed straight to Decimal (pin I8). Other values pass through."""
    if isinstance(value, str) and DECIMAL_TEXT.match(value.strip()):
        return Decimal(value.strip())
    return value


def _load_json(run_dir, rel):
    path = os.path.join(run_dir, rel)
    if not os.path.isfile(path):
        raise InputProblem("missing: %s" % rel)
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh, parse_float=Decimal)
    except (OSError, ValueError, UnicodeDecodeError):
        raise InputProblem("not valid JSON: %s" % rel)


def load_sim_input(run_dir):
    doc = _load_json(run_dir, SIM_INPUT)
    if schema_check.problems(SIM_INPUT_SCHEMA, doc):
        raise InputProblem("not valid against its schema: %s" % SIM_INPUT)
    return doc


def load_rule_ids(run_dir):
    doc = _load_json(run_dir, RULES)
    rules = doc.get("rules") if isinstance(doc, dict) else None
    if not isinstance(rules, list):
        raise InputProblem("not valid against its schema: %s" % RULES)
    for rule in rules:
        if schema_check.problems(RULE_SCHEMA, rule):
            raise InputProblem("not valid against its schema: %s" % RULES)
    return [r["id"] for r in rules]


def load_expected(run_dir):
    try:
        frozen = compute_expected.load_frozen(run_dir)
    except compute_expected.HashMismatch as exc:
        if "is missing" in str(exc):
            raise InputProblem("missing: %s" % str(exc).split(" is missing")[0].replace(os.sep, "/"))
        raise InputProblem("hash mismatch: %s" % EXPECTED)
    except (ValueError, UnicodeDecodeError):
        raise InputProblem("not valid JSON: %s" % EXPECTED)
    if not isinstance(frozen, dict) or not isinstance(frozen.get("cases"), list):
        raise InputProblem("not valid against its schema: %s" % EXPECTED)
    return frozen


def _steps(case, rule_order):
    """The case's steps: each `rule|item` it cites, in rule order, then item order."""
    item_order = [i["item_key"] for i in case["items"]]
    keys = list(case["holds"])

    def rank(key):
        rid, item = key.split("|", 1)
        return (rule_order.get(rid, len(rule_order)), item_order.index(item) if item in item_order else len(item_order))
    return [tuple(k.split("|", 1)) for k in sorted(keys, key=rank)]


def _person_step(case, gates, person_gates=()):
    """(step, written, problem) for a person-decided case; (None, {}, None) for a case with no decision."""
    if "person_decision" not in case:
        if case.get("gate") in person_gates:
            return None, {}, "%s (the case is for gate %s, which a person decides, and states no decision)" % (
                DECISION_MISSING, case["gate"])
        return None, {}, None
    pd = case.get("person_decision")
    if not isinstance(pd, dict) or not isinstance(pd.get("gate"), str) or not isinstance(pd.get("decision"), str):
        return None, {}, "%s (no gate and decision stated)" % DECISION_MISSING
    gid, decision = pd["gate"], pd["decision"]
    if gid.startswith("gate:"):
        gid = gid[len("gate:"):]
    if case.get("gate") is not None and gid != case["gate"]:
        return None, {}, "%s (the case is for gate %s and states a decision for gate %s)" % (
            DECISION_MISSING, case["gate"], gid)
    labels = gates.get(gid)
    if labels is None:
        return None, {}, "%s (gate %s is not a gate of the design)" % (DECISION_MISSING, gid)
    if decision not in labels:
        return None, {}, "%s (decision %s is not an outcome of gate %s)" % (DECISION_MISSING, decision, gid)
    written = {PERSON_DECISION: decision}
    if pd.get("ends_process") is True:
        written[NEXT_STEP] = "ended"
    step = {"part": "person", "rule_id": "gate:%s" % gid, "item_key": "person decision",
            "reads": [], "test_held": True, "writes": dict(written), "person_decision": True}
    return step, written, None


def walk_case(case, rules, gates=None, person_gates=()):
    """Walk one case. Returns (steps, written values, failure state or None, drop label or None).

    A value written by an item-scoped rule is seen only by later steps on the same item; a value written by a
    case-scoped rule is seen by later steps on every item. The returned written values are flat: the last write
    of each token, whatever its scope (the outcome label is case-level)."""
    items = {i["item_key"]: i["values"] for i in case["items"]}
    written = _Written()
    result_token, result_rules, mapping_problem = _result_mapping(case, rules)
    written.result_token = result_token
    history = []  # (token, value, origin, item key the write is visible to, or None for every item)
    steps = []
    person, decided, problem = _person_step(case, gates or {}, person_gates)
    if problem:
        return steps, written, "not_executed", problem
    if person is not None:
        steps.append(person)
        for token, value in decided.items():
            written[token] = value
            history.append((token, value, "decided by the person at %s" % person["rule_id"], None))
    rule_order = {r["id"]: n for n, r in enumerate(rules.values())}
    for rid, item_key in _steps(case, rule_order):
        rule = rules.get(rid)
        if rule is None:
            return steps, written, "execution_error", "step %s on %s: rule %s is not in the simulation input" % (
                rid, item_key, rid)
        expression = rule["expression"]
        row, origin = {}, {}
        for token, value in (items.get(item_key) or {}).items():
            row[token] = _number_or_text(value)
            origin[token] = "case item %s" % item_key
        for token, value, came_from, visible_to in history:
            if visible_to is None or visible_to == item_key:
                row[token] = value
                origin[token] = came_from
        key = "%s|%s" % (rid, item_key)
        for token in expr.judgment_reads(expression):
            if key in case["judgments"]:
                row[token] = case["judgments"][key]
                origin[token] = "judgment recorded in the case"
        step = {"part": rule.get("part"), "rule_id": rid, "item_key": item_key,
                "reads": [{"token": t, "value": _plain(row.get(t)), "origin": origin.get(t, "absent")}
                          for t in expr.reads(expression)],
                "test_held": None, "writes": {}}
        steps.append(step)
        try:
            result = expr.evaluate(expression, row)
        except expr.MissingInput as exc:
            step["error"] = str(exc)
            return steps, written, "needs_more_information", "step %s on %s: input %s is %s" % (
                rid, item_key, exc.token, str(exc).rsplit("(", 1)[-1].rstrip(")"))
        except Exception as exc:  # an expression that raises is an execution error, whatever the kind
            step["error"] = str(exc)
            return steps, written, "execution_error", "step %s on %s: %s" % (rid, item_key, exc)
        step["test_held"] = result["test"]
        step["writes"] = _plain(result["sets"])
        if rid in result_rules and result_token in result["sets"]:
            written.result_value = _typed_actual(result["sets"][result_token])
            written.result_set = True
            step["writes"][result_token] = written.result_value
        visible_to = None if rule.get("scope") == "case" else item_key
        for token, value in result["sets"].items():
            written[token] = value
            history.append((token, value, "set by %s on %s" % (rid, item_key), visible_to))
    if case.get(HANDOVER):
        # A hand-over case: the walk ends where the agent hands over to the person; the choice is not played. The
        # gate counts as reached only when a walked rule leads to the gate's step.
        leading = set(case.get("handover_from") or [])
        if not any(st.get("rule_id") in leading for st in steps):
            return steps, written, "not_executed", ("no walked rule leads to the hand-over at gate %s; the case "
                                                    "walks no agent step before the gate" % case[HANDOVER])
        route = written.get(NEXT_STEP)
        step = case.get("handover_step")
        if route is not None and not (isinstance(step, str) and isinstance(route, str)
                                      and (route == step or route == step.split(":", 1)[-1])):
            # the walked rules route the case somewhere else (or end it) before the gate: the hand-over is missed
            return steps, written, "not_executed", ("the walked rules route the case to %r before the hand-over at "
                                                   "gate %s, so the case does not reach it" % (route, case[HANDOVER]))
        steps.append({"part": "person", "rule_id": "gate:%s" % case[HANDOVER], "item_key": "hand-over",
                      "reads": [], "test_held": True, "writes": {}, "handover": True})
        return steps, written, None, None
    if person is not None and len(steps) == 1 and NEXT_STEP not in decided:
        return steps, written, NO_AGENT_STEP, "no agent step after the person decision at %s; the case cannot pass" % (
            person["rule_id"])
    if not (person is not None and NEXT_STEP in decided):
        if mapping_problem:
            return steps, written, "not_executed", mapping_problem
        if not written.result_set:
            return steps, written, "not_executed", "no walked rule set the outcome (decision result output %s)" % result_token
    return steps, written, None, None


def _typed_actual(value):
    """Keep authored JSON scalar types; Decimal is only the parser's numeric representation."""
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, dict):
        return {k: _typed_actual(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_typed_actual(v) for v in value]
    return value


class _Written(dict):
    """Flat writes remain compatible; retain the result before unrelated decisions overwrite it."""
    result_token = None
    result_value = None
    result_set = False


def _output_tokens(doc):
    tokens = set()
    for key in ("output", "output_token"):
        value = doc.get(key)
        if isinstance(value, dict):
            value = value.get("token")
        if isinstance(value, str) and value:
            tokens.add(value)
    return tokens


def _result_mapping(case, rules):
    """Resolve only declarations and expression write keys, never labels or expected answers.

    `output` (a token or {token: ...}) / `output_token` explicitly names the result.
    Otherwise the legacy outcome token or a sole decision-owned write is supported.
    Multiple named writes require an explicit output declaration.
    """
    if case.get("_result_problem"):
        return None, set(), case["_result_problem"]
    decision = case.get("decision")
    walked = {key.split("|", 1)[0] for key in case["holds"]}
    eligible = set()
    for rid in walked:
        rule = rules.get(rid) or {}
        decisions = rule.get("_decisions", [rule.get("decision")])
        if decision is None or decision in decisions:
            if decision is not None and len(set(decisions)) > 1:
                return None, set(), "ambiguous decision placement for rule %s" % rid
            eligible.add(rid)
    if decision is None and not (case.get("person_decision") or case.get(HANDOVER)):
        owners = {d for rid in eligible for d in (rules.get(rid) or {}).get(
            "_decisions", [(rules.get(rid) or {}).get("decision")]) if d is not None}
        if len(owners) > 1:
            return None, eligible, "ambiguous case decision for result output"
    declared = _output_tokens(case)
    if len(declared) > 1:
        return None, eligible, "ambiguous declared result output"
    keys = set()
    for rid in eligible:
        expression = (rules.get(rid) or {}).get("expression") or {}
        keys.update((expression.get("sets") or {}).keys())
    if declared:
        token = next(iter(declared))
    elif OUTCOME in keys:
        token = OUTCOME
    else:
        keys.difference_update((NEXT_STEP, PERSON_DECISION))
        if len(keys) != 1:
            return None, eligible, "missing or ambiguous decision result output"
        token = next(iter(keys))
    return token, eligible, None


def _result(written):
    if isinstance(written, _Written):
        return written.result_token, written.result_value if written.result_set else None
    return OUTCOME, written.get(OUTCOME)


def _context(run_dir, sim):
    """Recover metadata omitted by older converters without changing source or evaluation data."""
    def optional(rel, default):
        try:
            return _load_json(run_dir, rel)
        except InputProblem:
            return default

    def records(doc, key):
        values = doc.get(key, []) if isinstance(doc, dict) else []
        return [v for v in values if isinstance(v, dict)] if isinstance(values, list) else []

    suite = optional("stages/test-suite/test-suite.json", {})
    outcomes = optional("stages/rules/outcomes.json", {})
    placed = expr.placed_rule_list(optional("stages/placement/placed-rules.json", []))
    source_rules = optional(RULES, {})
    rules = {r["id"]: dict(r) for r in sim["rules"]}
    for rid, rule in rules.items():
        declarations = [r for r in records(source_rules, "rules") if r.get("id") == rid]
        declarations += [p for p in (placed or []) if isinstance(p, dict) and p.get("rule_id") == rid]
        decisions = {r.get("decision") for r in declarations + [rule] if isinstance(r.get("decision"), str)}
        for placement in declarations:
            step = "%s:%s" % (placement.get("process_id"), placement.get("future_state_step"))
            if not placement.get("decision") and any(d.get("id") == step for d in records(outcomes, "decisions")):
                decisions.add(step)
        if decisions:
            rule["_decisions"] = sorted(decisions)
    cases = []
    for original in sim["cases"]:
        case = dict(original)
        sources = [c for c in records(suite, "cases") if c.get("id") == case["id"]]
        decisions = {c.get("decision") for c in sources + [case] if isinstance(c.get("decision"), str)}
        if len(decisions) > 1 or len(sources) > 1:
            case["_result_problem"] = "ambiguous source case decision"
        elif decisions:
            case["decision"] = next(iter(decisions))
        declarations = sources + [case]
        declarations += [d for d in records(outcomes, "decisions") if d.get("id") == case.get("decision")]
        tokens = set().union(*(_output_tokens(d) for d in declarations))
        if len(tokens) > 1:
            case["_result_problem"] = "ambiguous declared result output"
        elif tokens:
            case["output"] = next(iter(tokens))
        # Person gates describe the input decision, not the agent's downstream result.
        if "person_decision" in case or case.get(HANDOVER) or case.get("gate"):
            case.pop("decision", None)
        cases.append(case)
    return cases, rules


def _interfaces(steps):
    return [{"part": s["part"] or "not given",
             "boundary_id": s["rule_id"] if s.get("person_decision") or s.get("handover") else "rule:%s" % s["rule_id"],
             "input": {r["token"]: r["value"] for r in s["reads"]}, "output": s["writes"]} for s in steps]


def compare(case, steps, written, frozen_by_id, excluded_by_id):
    """State, values rows and drop label for a fully walked case."""
    cid = case["id"]
    result_token, label = _result(written)
    label = _typed_actual(label)
    actual_values = _typed_actual(dict(written))
    if isinstance(written, _Written) and written.result_set:
        actual_values[result_token] = label
    exp = frozen_by_id.get(cid)
    if exp is None:
        why = excluded_by_id.get(cid)
        reason = ("frozen expected values exclude case %s: %s" % (cid, why)) if why else \
            "no frozen expected value for case %s in %s" % (cid, EXPECTED)
        return "needs_more_information", [], reason, ""
    if case.get(HANDOVER):
        # Graded on reaching the gate and on any non-outcome value the walked rules wrote; the outcome label is
        # the person's choice, which a hand-over case does not play.
        gate = "gate:%s" % case[HANDOVER]
        reached = bool(steps) and steps[-1].get("handover") is True
        rows = [{"token": "hand-over", "actual": gate if reached else None, "expected": gate, "match": reached}]
        for name in sorted(exp.get("amounts") or {}):
            want = Decimal(exp["amounts"][name])
            got = compute_expected._decimal(written.get(name))
            rows.append({"token": name, "actual": _plain(written.get(name)), "expected": exp["amounts"][name],
                         "match": got is not None and abs(got - want) <= grade.TOLERANCE})
        for name in sorted((exp.get("values") or {})):
            if name in (OUTCOME, result_token, NEXT_STEP) or name in (exp.get("amounts") or {}):
                continue
            actual = actual_values.get(name)
            rows.append({"token": name, "actual": actual, "expected": exp["values"][name],
                         "match": name in written and grade._same_typed(exp["values"][name], actual_values[name])})
        ok = all(r["match"] for r in rows)
        reason = ("the case reaches the hand-over at %s; the person's choice is not played, so its label is not "
                  "graded" % gate) if ok else "the case does not reach the hand-over at %s with the expected values" % gate
        return ("matches" if ok else "differs"), rows, reason, exp.get("label") or ""
    if label is None and written.get(NEXT_STEP) == "ended":
        # an ending person decision with no agent label: graded on the terminal state and the other values
        if (exp.get("values") or {}).get(NEXT_STEP) != "ended":
            return "differs", [], "the person decision ends the process; the case does not expect next_step ended", \
                exp.get("label") or ""
        # No actual label exists. Grade the terminal write and other values directly;
        # never borrow the expected label to manufacture an actual result.
        values = {k: v for k, v in (exp.get("values") or {}).items() if k != OUTCOME}
        problems = grade._outcome_problems(exp.get("amounts") or {}, actual_values, values,
                                           exp.get("directions") or {})
        rows = []
        for name, want in sorted(values.items()):
            rows.append({"token": name, "actual": actual_values.get(name), "expected": want,
                         "match": name in actual_values and grade._same_typed(want, actual_values[name])})
        for name, want in sorted((exp.get("amounts") or {}).items()):
            got = compute_expected._decimal(written.get(name))
            rows.append({"token": name, "actual": _plain(written.get(name)), "expected": want,
                         "match": got is not None and abs(got - Decimal(want)) <= grade.TOLERANCE})
        return ("differs" if problems else "matches"), rows, (
            "; ".join(problems) if problems else "the person decision ends the process with the expected values"), exp.get("label") or ""
    verdict = grade.grade_case(exp, label, actual_values)
    chosen = exp
    if exp.get("label") != label:
        chosen = next((a for a in exp.get("alternatives") or [] if a.get("label") == label), exp)
    rows = [{"token": result_token or OUTCOME, "actual": label, "expected": exp.get("label"),
             "match": label == exp.get("label") or label in [a.get("label") for a in exp.get("alternatives") or []]}]
    for name in sorted(chosen.get("amounts") or {}):
        want = Decimal(chosen["amounts"][name])
        got = compute_expected._decimal(written.get(name))
        rows.append({"token": name, "actual": _plain(written.get(name)), "expected": chosen["amounts"][name],
                     "match": got is not None and abs(got - want) <= grade.TOLERANCE})
    for name in sorted(chosen.get("values") or {}):
        if name == OUTCOME:
            continue
        actual = actual_values.get(name)
        rows.append({"token": name, "actual": actual, "expected": chosen["values"][name],
                     "match": name in written and grade._same_typed(chosen["values"][name], actual_values[name])})
    return ("matches" if verdict["pass"] else "differs"), rows, verdict["reason"], exp.get("label") or ""


def summarise(entries, rule_ids, reached, held_in_match):
    total = len(rule_ids)
    demonstrated = [r for r in rule_ids if r in held_in_match]
    return {
        "cases_total": len(entries),
        "cases_walked": sum(1 for e in entries if e["state"] in ("matches", "differs")),
        "person_decided_walked": sum(1 for e in entries if e.get("person_decision") and
                                     e["state"] in ("matches", "differs")),
        "rules_total": total,
        "rules_reached": sum(1 for r in rule_ids if r in reached),
        "rules_reached_share": (sum(1 for r in rule_ids if r in reached) / float(total)) if total else None,
        "rules_demonstrated": len(demonstrated),
        "rules_not_demonstrated": [r for r in rule_ids if r not in held_in_match],
        "status_line": "rules demonstrated %d of %d" % (len(demonstrated), total),
    }


def run(run_dir):
    """Walk and write walk.json. Returns (exit code, document)."""
    error, sim, rule_ids, frozen = None, None, [], None
    try:
        sim = load_sim_input(run_dir)
        rule_ids = load_rule_ids(run_dir)
        frozen = load_expected(run_dir)
    except InputProblem as exc:
        error = str(exc)
    entries, reached, held_in_match = [], set(), set()
    if error is not None:
        for case in (sim or {}).get("cases") or []:
            entries.append({"case_id": case["id"], "state": "not_executed", "drop_label": error,
                            "outcome_actual": None, "outcome_expected": "", "values": [], "interfaces": [],
                            "steps": []})
    else:
        cases, rules = _context(run_dir, sim)
        gates = {g.get("id"): list(g.get("labels") or []) for g in sim.get("gates") or [] if isinstance(g, dict)}
        person_gates = {g.get("id") for g in sim.get("gates") or [] if isinstance(g, dict) and g.get("person_decides")}
        frozen_by_id = {c.get("id"): c for c in frozen.get("cases") or []}
        excluded_by_id = {c.get("id"): c.get("excluded") for c in frozen.get("excluded") or []}
        for case in cases:
            steps, written, state, drop = walk_case(case, rules, gates, person_gates)
            # reached = evaluated in at least one case, including a step that stopped on a missing input or raised
            reached.update(s["rule_id"] for s in steps if not s.get("person_decision") and not s.get("handover"))
            exp = frozen_by_id.get(case["id"]) or {}
            entry = {"case_id": case["id"], "outcome_actual": None, "outcome_expected": exp.get("label") or "",
                     "values": [], "interfaces": _interfaces(steps), "steps": steps}
            if "person_decision" in case:
                entry["person_decision"] = case["person_decision"]
            if state == NO_AGENT_STEP:
                state, entry["reason"] = "differs", drop
            elif state is None:
                _, actual = _result(written)
                if case.get(HANDOVER) and actual is None:
                    actual = "handed over at gate:%s" % case[HANDOVER]
                entry["outcome_actual"] = _typed_actual(actual)
                state, rows, reason, _ = compare(case, steps, written, frozen_by_id, excluded_by_id)
                entry["values"] = rows
                if state == "needs_more_information":
                    drop = reason
                else:
                    entry["reason"] = reason
                if state == "matches":
                    held_in_match.update(s["rule_id"] for s in steps if s["test_held"] and not s.get("person_decision")
                                         and not s.get("handover"))
            entry["state"] = state
            if state in DROPPED:
                entry["drop_label"] = drop
            entries.append(entry)
    doc = {"error": error, "cases": entries, "summary": summarise(entries, rule_ids, reached, held_in_match),
           "repairs": []}
    out = os.path.join(run_dir, WALK)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2, sort_keys=True)
        fh.write("\n")
    if error is not None:
        return 2, doc
    return (0 if all(e["state"] == "matches" for e in entries) else 1), doc


def main(argv=None):
    ap = argparse.ArgumentParser(description="Walk the simulation input and compare with the frozen expected values.")
    ap.add_argument("--run", required=True)
    args = ap.parse_args(argv)
    if not os.path.isdir(args.run):
        print("walk: the run folder %s does not exist; nothing written" % args.run, file=sys.stderr)
        return 2
    code, doc = run(args.run)
    if doc["error"]:
        print("walk: stopped: %s (wrote %s)" % (doc["error"], WALK), file=sys.stderr)
    states = {}
    for e in doc["cases"]:
        states[e["state"]] = states.get(e["state"], 0) + 1
    print("walk: %d cases (%s)" % (len(doc["cases"]), ", ".join("%s %d" % kv for kv in sorted(states.items()))))
    print(doc["summary"]["status_line"])
    return code


if __name__ == "__main__":
    sys.exit(main())
