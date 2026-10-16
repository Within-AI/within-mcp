"""Turn a run's test suite into simulation input, mechanically.

Usage: convert_cases.py --run RUN

Reads stages/test-suite/test-suite.json, stages/placement/placed-rules.json, stages/rules/outcomes.json and
stages/rules/rules.json (the rule expressions, scopes and item keys the placed rules point at), and, when the
architecture stage has written it, stages/architecture/agent.yaml (JSON syntax): each rule carries the `part` whose
owns_steps holds its placed step `<process_id>:<future_state_step>`, so the walk records which sub-agent ran each
step. Writes simulation/sim-input.json. Calls no model and writes no prose: anything it cannot convert goes to
unconvertible[] with a fixed reason from REASONS and is left out, never guessed.

A case names a rule by its rule id, or a gate by the gate's own id from outcomes.json (or `gate:<gate_id>`);
a gate stands for its rules. An id that names both a placed rule and a gate is unconvertible, never guessed.

A case for a step a person decides carries `person_decision: {"gate", "decision"}` (and, optionally,
`"ends_process": true`); it rides through unchanged, and the gates with their labels are copied into the output
`gates[]` so the walk can check the decision. A case whose `decision` names a gate keeps that gate as `gate`, and a
gate whose boundary comes from a person (stages/boundaries/boundaries.json, when present) is marked
`person_decides: true`, so the walk can tell a person-decided case that states no decision. Such a case whose
expected result does not depend on the decision (scripts/person_gates.py) tests the hand-over: it carries
`handover: <gate id>` instead of `gate`, and the walk checks it reaches the gate. A judgment answer
keeps its type: text (whitespace-only text is written as ""; "" and "explanation not found" are legal answers),
a number, true or false, or null (an absent `answer` is read as null, a missing answer). An object or list is
malformed.

Expected values may be any typed value the case schema allows: numbers, booleans, text (the text
"not_applicable" included), null, the not-applicable marker, and named derivations placed in values; only an
object that is neither the marker nor a named derivation is not applicable written another way.

Every suite value copied into the output (holds values, row values, judgment answers, final labels, expected
values) is checked for type and enum; a case whose content parses but falls outside U2's case schema is listed in
unconvertible[] with reason "case-malformed" and the field, and the other cases convert.

Exit 0 written; 2 usage, or a missing input file or one that will not parse or has the wrong top-level shape
(named on stderr; nothing written).
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import compute_expected  # noqa: E402
import expr  # noqa: E402
import person_gates as person_gate_reading  # noqa: E402

SUITE = "stages/test-suite/test-suite.json"
PLACED = "stages/placement/placed-rules.json"
OUTCOMES = "stages/rules/outcomes.json"
RULES = "stages/rules/rules.json"
AGENT = "stages/architecture/agent.yaml"
BOUNDARIES = "stages/boundaries/boundaries.json"
OUT = "simulation/sim-input.json"

GATE_PREFIX = "gate:"
PERSON_TOKENS = ("person.decision", "outcome")
HOLDS_STRINGS = ("needs_more_information", "not_applicable")
NA_SPELLINGS = {"not applicable", "n/a", "na", "not_applicable", "not-applicable"}

REASONS = {
    "token-no-value": "read token has no value and no unset mark",
    "item-rule-no-key": "item-scoped rule has no item key",
    "judgment-no-answer": "judged rule has no recorded answer",
    "no-final-label": "case has no final label",
    "na-other-form": "not applicable written other than {\"not_applicable\": true}",
    "unknown-gate": "gate id not in outcomes.json gates",
    "unknown-rule": "rule id not among the placed rules",
    "rule-gate-collision": "id names both a placed rule and a gate",
    "rule-no-definition": "placed rule has no definition in rules.json",
    "gate-no-rules": "gate lists no rules",
    "hold-item-not-in-rows": "hold names an item key that is not a row of the case",
    "duplicate-case-id": "case id shared with another case",
    "case-stamped-open": "case is a stamped-open exclusion",
    "case-no-id": "case has no id",
    "case-malformed": "case content has the wrong shape",
}


class InputError(Exception):
    pass


def _load(run, rel):
    path = os.path.join(run, rel)
    if not os.path.isfile(path):
        raise InputError("missing input file: %s" % rel)
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (ValueError, UnicodeDecodeError) as e:
        raise InputError("malformed input file: %s (%s)" % (rel, e))


def _list_of_dicts(value):
    return isinstance(value, list) and all(isinstance(x, dict) for x in value)


def load_inputs(run):
    suite = _load(run, SUITE)
    if not isinstance(suite, dict) or not isinstance(suite.get("cases"), list):
        raise InputError("malformed input file: %s (no cases list)" % SUITE)
    placed = expr.placed_rule_list(_load(run, PLACED))
    if not _list_of_dicts(placed) or not all(isinstance(p.get("rule_id"), str) for p in placed):
        raise InputError("malformed input file: %s (no placed rule list with rule_id)" % PLACED)
    outcomes = _load(run, OUTCOMES)
    if not isinstance(outcomes, dict) or not _list_of_dicts(outcomes.get("gates")) \
            or not _list_of_dicts(outcomes.get("decisions")):
        raise InputError("malformed input file: %s (no decisions and gates lists)" % OUTCOMES)
    rules = _load(run, RULES)
    if not isinstance(rules, dict) or not _list_of_dicts(rules.get("rules")):
        raise InputError("malformed input file: %s (no rules list)" % RULES)
    return suite, placed, outcomes, rules


def load_owners(run):
    """Qualified step -> owning part id from agent.yaml; {} when the architecture file is absent."""
    if not os.path.isfile(os.path.join(run, AGENT)):
        return {}
    agent = _load(run, AGENT)
    parts = agent.get("parts") if isinstance(agent, dict) else None
    if not _list_of_dicts(parts):
        raise InputError("malformed input file: %s (no parts list)" % AGENT)
    owners = {}
    for part in parts:
        if isinstance(part.get("id"), str) and part["id"]:
            for step in part.get("owns_steps") or []:
                if isinstance(step, str):
                    owners.setdefault(step, part["id"])
    return owners


def _is_na(value):
    """The pinned marker, by strict identity: exactly one key `not_applicable` whose value is True."""
    return isinstance(value, dict) and len(value) == 1 and value.get("not_applicable") is True


def _is_na_other_form(value):
    if isinstance(value, dict):
        return not _is_na(value)
    if isinstance(value, str):
        return value.strip().lower() in NA_SPELLINGS
    return False


def _plain(cell):
    """A row cell is a labelled value {value, label, ...}; the plain value is its value."""
    if isinstance(cell, dict) and "value" in cell and "label" in cell:
        return cell["value"]
    return cell


def _scalar(value):
    return value is None or isinstance(value, (str, bool, int, float))


def _plain_ok(value):
    """A value the output can carry: a scalar, null, or an object (an object other than the marker is
    not applicable written another way, listed as unconvertible, never copied)."""
    return _scalar(value) or isinstance(value, dict)


def _key_part(value):
    return isinstance(value, str) and value != "" and "|" not in value


class _Malformed(Exception):
    pass


def case_structure(case, n=0):
    """None, or "field: why" for the first value of one case whose type or enum falls outside U2's case schema
    among the values the converter copies. Semantic defects are left to convert_case."""
    try:
        _case_structure(case, n)
    except _Malformed as e:
        return str(e)
    return None


def check_structure(suite):
    """{case index: "field: why"} for every case whose content is the wrong shape."""
    out = {}
    for n, case in enumerate(suite["cases"]):
        why = case_structure(case, n)
        if why:
            out[n] = why
    return out


def _case_structure(case, n):
    def bad(cid, field, why):
        raise _Malformed("case %s, field %s: %s" % (cid, field, why))

    if not isinstance(case, dict):
        bad("cases[%d]" % n, "cases[%d]" % n, "not an object")
    cid = case.get("id") if isinstance(case.get("id"), str) and case.get("id") else "cases[%d]" % n
    if "exclusion" in case:
        return
    rows = case.get("rows")
    if rows is not None and not _list_of_dicts(rows):
        bad(cid, "rows", "not a list of objects")
    for i, row in enumerate(rows or []):
        if not _key_part(row.get("id")):
            bad(cid, "rows[%d].id" % i, "not a non-empty string without |")
        values = row.get("values")
        if values is not None and not isinstance(values, dict):
            bad(cid, "rows[%d].values" % i, "not an object")
        for token, cell in (values or {}).items():
            if not _plain_ok(_plain(cell)):
                bad(cid, "rows[%d].values.%s" % (i, token), "%r is not a scalar, null or object" % (cell,))
    for field, value_key, ok, why in (
            ("holds", "holds",
             lambda v: isinstance(v, bool) or (isinstance(v, str) and v in HOLDS_STRINGS),
             "is not one of true, false, \"needs_more_information\", \"not_applicable\""),
            ("judgment_answers", "answer", _answer_ok,
             "is not text, a number, true/false or null")):
        entries = case.get(field)
        if entries is not None and not _list_of_dicts(entries):
            bad(cid, field, "not a list of objects")
        for i, e in enumerate(entries or []):
            if not isinstance(e.get("rule_id"), str) or not e.get("rule_id"):
                bad(cid, "%s[%d].rule_id" % (field, i), "not a non-empty string")
            if not _key_part(e.get("item_key")):
                bad(cid, "%s[%d].item_key" % (field, i), "not a non-empty string without |")
            if not ok(e.get(value_key)):
                bad(cid, "%s[%d].%s" % (field, i, value_key), "%r %s" % (e.get(value_key), why))
    label = case.get("final_label")
    if label is not None and not isinstance(label, str):
        bad(cid, "final_label", "%r is not a string" % (label,))
    outcomes = [("expected", case.get("expected"))]
    alts = case.get("allowed_alternatives")
    if alts is not None and not _list_of_dicts(alts):
        bad(cid, "allowed_alternatives", "not a list of objects")
    outcomes += [("allowed_alternatives[%d]" % i, a) for i, a in enumerate(alts or [])]
    for name, outcome in outcomes:
        if outcome is None:
            continue
        if not isinstance(outcome, dict) or not isinstance(outcome.get("values", {}), dict):
            bad(cid, name, "not an object with a values object")
        for vname, value in (outcome.get("values") or {}).items():
            if not _plain_ok(value) and not compute_expected.is_named(value):
                bad(cid, "%s.values.%s" % (name, vname), "%r is not a scalar, null or object" % (value,))


def convert_rules(placed, rules_doc, owners=None):
    """Return ({rule_id: sim rule}, {rule_id: reason code}) over the placed rules. A rule whose placed step a part
    owns carries that part's id as `part` (the first placement, for a rule placed on several steps)."""
    defs = {}
    for r in rules_doc["rules"]:
        if isinstance(r.get("id"), str):
            defs.setdefault(r["id"], r)
    good, bad = {}, {}
    for p in placed:
        rid = p["rule_id"]
        r = defs.get(rid)
        if r is None:
            bad[rid] = "rule-no-definition"
            continue
        if r.get("scope") == "item" and not r.get("item_key"):
            bad[rid] = "item-rule-no-key"
            continue
        out = {"id": rid, "scope": "item" if r.get("scope") == "item" else "case", "expression": r.get("expression")}
        if out["scope"] == "item":
            out["item_key"] = r["item_key"]
        part = (owners or {}).get("%s:%s" % (p.get("process_id"), p.get("future_state_step")))
        if part:
            out["part"] = part
        good.setdefault(rid, out)
    return good, bad


def gate_rules(outcomes, placed):
    """Gate id -> rule ids: the gate's own `rules` list, else the placed rules whose decision is the gate id."""
    out = {}
    for g in outcomes["gates"]:
        gid = g.get("id")
        if not isinstance(gid, str):
            continue
        listed = g.get("rules")
        if isinstance(listed, list) and listed:
            out[gid] = [x for x in listed if isinstance(x, str)]
        else:
            out[gid] = [p["rule_id"] for p in placed if p.get("decision") == gid]
    return out


def _entry(case_id, item, code):
    return {"case_id": case_id, "item": item, "reason": REASONS[code]}


def convert_case(case, good, bad, gates):
    """Return (sim case or None, [unconvertible entries]). Run check_structure on the suite first."""
    cid = case["id"]
    if "exclusion" in case:
        return None, [_entry(cid, None, "case-stamped-open")]
    problems = []

    def expand(rid):
        """Rule ids a holds/judgment rule_id stands for; None when it names nothing known."""
        if isinstance(rid, str) and rid.startswith(GATE_PREFIX):
            gid = rid[len(GATE_PREFIX):]
            if gid not in gates:
                problems.append(_entry(cid, gid, "unknown-gate"))
                return None
            if not gates[gid]:
                problems.append(_entry(cid, gid, "gate-no-rules"))
                return None
            return gates[gid]
        if rid in gates:
            if rid in good or rid in bad:
                problems.append(_entry(cid, rid, "rule-gate-collision"))
                return None
            if not gates[rid]:
                problems.append(_entry(cid, rid, "gate-no-rules"))
                return None
            return gates[rid]
        return [rid]

    def known(rid):
        if rid in bad:
            problems.append(_entry(cid, rid, bad[rid]))
            return False
        if rid not in good:
            problems.append(_entry(cid, rid, "unknown-rule"))
            return False
        return True

    rows = {}
    for row in case.get("rows") or []:
        rows[row.get("id")] = row.get("values") or {}

    for_gate = isinstance(case.get("decision"), str) and \
        (case["decision"] in gates or case["decision"][len(GATE_PREFIX):] in gates
         if case["decision"].startswith(GATE_PREFIX) else case["decision"] in gates)
    holds = {}
    for h in case.get("holds") or []:
        for rid in expand(h.get("rule_id")) or []:
            if known(rid):
                holds["%s|%s" % (rid, h.get("item_key"))] = h.get("holds")

    judgments = {}
    for j in case.get("judgment_answers") or []:
        for rid in expand(j.get("rule_id")) or []:
            if known(rid):
                answer = j.get("answer")  # a missing answer is null; the declared type is kept
                judgments["%s|%s" % (rid, j.get("item_key"))] = answer.strip() if isinstance(answer, str) else answer

    # Every rule held on an item: the item is a row, each read token has a value or an unset mark,
    # and a judged rule has a recorded answer for that item.
    for key in holds:
        rid, item_key = key.split("|", 1)
        if item_key not in rows:
            problems.append(_entry(cid, item_key, "hold-item-not-in-rows"))
            continue
        expression = good[rid]["expression"]
        for token in expr.reads(expression):
            if token.startswith("judgment."):
                continue
            if token in PERSON_TOKENS and (isinstance(case.get("person_decision"), dict) or for_gate):
                continue  # the walk writes these when it plays the person's decision, or records it missing
            if token not in rows[item_key]:
                problems.append(_entry(cid, token, "token-no-value"))
        if expr.judgment_reads(expression) and key not in judgments:
            problems.append(_entry(cid, key, "judgment-no-answer"))

    items = []
    for row in case.get("rows") or []:
        values = {}
        for token, cell in (row.get("values") or {}).items():
            value = _plain(cell)
            if _is_na_other_form(value):
                problems.append(_entry(cid, token, "na-other-form"))
            values[token] = value
        items.append({"item_key": row.get("id"), "values": values})

    for name, outcome in [("expected", case.get("expected"))] + \
            [("allowed_alternatives", a) for a in case.get("allowed_alternatives") or []]:
        for vname, value in ((outcome or {}).get("values") or {}).items():
            if isinstance(value, dict) and not _is_na(value) and not compute_expected.is_named(value):
                problems.append(_entry(cid, vname, "na-other-form"))

    label = case.get("final_label")
    if not isinstance(label, str) or not label:
        problems.append(_entry(cid, None, "no-final-label"))

    if problems:
        return None, _dedupe(problems)
    out = {"id": cid, "items": items, "holds": holds, "judgments": judgments, "final_label": label}
    # Computed-value expectations ride through unchanged for the accuracy check.
    for k in ("decision", "expected", "allowed_alternatives", "person_decision"):
        if k in case:
            out[k] = case[k]
    gate = case.get("decision")
    if isinstance(gate, str) and gate.startswith(GATE_PREFIX):
        gate = gate[len(GATE_PREFIX):]
    if gate in gates:
        out["gate"] = gate
    return out, []


def _answer_ok(value):
    """A judgment answer is text, a number, a boolean or null (missing); its type is preserved."""
    return value is None or isinstance(value, (str, bool, int, float))


def _dedupe(entries):
    seen, out = set(), []
    for e in entries:
        key = (e["case_id"], e["item"], e["reason"])
        if key not in seen:
            seen.add(key)
            out.append(e)
    return out


def convert(suite, placed, outcomes, rules_doc, owners=None, person_gates=None, reading=None):
    """Return the sim-input document; a case whose content is the wrong shape is listed as unconvertible.

    A case for a gate a person decides that states no decision, and whose expected result does not depend on it
    (person_gates.Reading), is converted as a hand-over case: `handover: <gate id>` in place of `gate`, so the walk
    checks that the case reaches the hand-over instead of leaving it unrun."""
    if reading is None:
        reading = person_gate_reading.Reading(rules_doc=rules_doc, placed=placed, outcomes=outcomes)
    malformed = check_structure(suite)
    good, bad = convert_rules(placed, rules_doc, owners)
    gates = gate_rules(outcomes, placed)
    counts = {}
    for c in suite["cases"]:
        if isinstance(c, dict):
            counts[c.get("id")] = counts.get(c.get("id"), 0) + 1
    cases, unconvertible = [], []
    for n, c in enumerate(suite["cases"]):
        if n in malformed:
            cid = c.get("id") if isinstance(c, dict) and isinstance(c.get("id"), str) and c["id"] else "(no id)"
            entry = _entry(cid, None, "case-malformed")
            entry["item"] = malformed[n]
            unconvertible.append(entry)
            continue
        cid = c.get("id")
        if not isinstance(cid, str) or not cid:
            unconvertible.append(_entry("(no id)", None, "case-no-id"))
            continue
        if counts[cid] > 1:
            unconvertible.append(_entry(cid, None, "duplicate-case-id"))
            continue
        sim_case, problems = convert_case(c, good, bad, gates)
        unconvertible.extend(problems)
        if sim_case is not None and sim_case.get("gate") in (person_gates or ()) \
                and "person_decision" not in sim_case and not reading.needs_decision(c, sim_case["gate"]):
            sim_case["handover"] = sim_case.pop("gate")
            # the walked rules that lead to the gate's step; the walk counts the hand-over reached only through one
            sim_case["handover_from"] = sorted(reading.leads_to_gate(sim_case["handover"]))
            step = reading.gate_step_of(sim_case["handover"])
            if step:
                sim_case["handover_step"] = step  # a walked route to another step means the gate is not reached
        if sim_case is not None:
            cases.append(sim_case)
    # A rule placed on several steps appears once in rules[].
    order = []
    for p in placed:
        if p["rule_id"] in good and p["rule_id"] not in order:
            order.append(p["rule_id"])
    gate_list = [{"id": g["id"], "labels": [lab for lab in g.get("labels") or [] if isinstance(lab, str)]}
                 for g in outcomes["gates"] if isinstance(g.get("id"), str)]
    for g in gate_list:
        if g["id"] in (person_gates or ()):
            g["person_decides"] = True
    return {"rules": [good[rid] for rid in order], "cases": cases, "unconvertible": unconvertible,
            "gates": gate_list}


def load_person_gates(run):
    """Gate ids whose boundary comes from a person, from boundaries.json; empty when it is absent or has no
    boundary list (the walk then cannot tell a person-decided case that states no decision)."""
    if not os.path.isfile(os.path.join(run, BOUNDARIES)):
        return set()
    doc = _load(run, BOUNDARIES)
    items = doc.get("boundaries") if isinstance(doc, dict) else doc
    out = set()
    for b in items if isinstance(items, list) else []:
        if isinstance(b, dict) and isinstance(b.get("from"), dict) and b["from"].get("side") == "person" \
                and isinstance(b.get("id"), str):
            out.add(b["id"][len(GATE_PREFIX):] if b["id"].startswith(GATE_PREFIX) else b["id"])
    return out


def run(run_dir):
    suite, placed, outcomes, rules_doc = load_inputs(run_dir)
    result = convert(suite, placed, outcomes, rules_doc, load_owners(run_dir), load_person_gates(run_dir),
                     person_gate_reading.Reading(run_dir, rules_doc=rules_doc, placed=placed, outcomes=outcomes))
    out = os.path.join(run_dir, OUT)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, sort_keys=True)
        f.write("\n")
    return result


def main(argv=None):
    ap = argparse.ArgumentParser(description="Convert the test suite into simulation input.")
    ap.add_argument("--run", required=True)
    args = ap.parse_args(argv)
    try:
        result = run(args.run)
    except InputError as e:
        print("convert_cases: error: %s" % e, file=sys.stderr)
        return 2
    print("convert_cases: %d cases, %d unconvertible" % (len(result["cases"]), len(result["unconvertible"])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
