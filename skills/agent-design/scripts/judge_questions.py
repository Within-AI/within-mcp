#!/usr/bin/env python3
"""Write one plain advisory question per placed rule whose expression reads a judgment answer.

Reads stages/placement/placed-rules.json (which rules are placed, their decision and step) and
stages/rules/rules.json (each rule's prose and expression). Writes
stages/expected-values/judge-questions.json. A judged rule with no prose or no decision, or whose
prose carries an internal id, gets no question and is listed in skipped[]; no text is invented.
A decision that is a reference (a plain id or a qualified `<process_id>:<id>`) is kept as the
question's `decision` and left out of the question text.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import expr  # noqa: E402

PLACED = os.path.join("stages", "placement", "placed-rules.json")
RULES = os.path.join("stages", "rules", "rules.json")
OUT = os.path.join("stages", "expected-values", "judge-questions.json")
VERDICT_FORMAT = "formats/lists/verdict.schema.json"


def _list(data, *keys):
    if isinstance(data, list):
        return data
    for key in keys:
        if isinstance(data, dict) and isinstance(data.get(key), list):
            return data[key]
    return []


def _token_name(entry):
    if isinstance(entry, dict):
        return entry.get("name") or entry.get("token") or entry.get("id")
    return entry


def _internal_ids(rules, placed_rules, token_list):
    """Every internal id across the loaded inputs, so no question carries any rule's ids."""
    ids = set()
    for placed in placed_rules:
        ids.update([placed.get("rule_id"), placed.get("future_state_step"), placed.get("process_id")])
        if placed.get("process_id") and placed.get("future_state_step"):
            ids.add("%s:%s" % (placed["process_id"], placed["future_state_step"]))
        ids.update(placed.get("observation_ids") or [])
    for rule in rules:
        ids.add(rule.get("id"))
        ids.update(rule.get("observation_ids") or [])
        try:
            ids.update(expr.reads(rule.get("expression")))
        except expr.ExprError:
            pass
    ids.update(_token_name(t) for t in token_list or [])
    return {i for i in ids if isinstance(i, str) and i}


def _is_reference(decision, internal):
    """A plain internal id, or a qualified reference whose parts are all internal ids."""
    decision = decision.strip()
    return decision in internal or all(part in internal for part in decision.split(":"))


def _question(decision, prose, internal):
    prose = prose.strip().rstrip(".")
    if _is_reference(decision, internal):
        return "Looking at this item, is the following true? %s." % prose
    decision = decision.strip().rstrip(".?")
    return ("For the decision \"%s\": looking at this item, is the following true? %s." % (decision, prose))


def build(run_dir):
    with open(os.path.join(run_dir, PLACED), encoding="utf-8") as fh:
        placed_rules = _list(json.load(fh), "placed_rules", "rules")
    with open(os.path.join(run_dir, RULES), encoding="utf-8") as fh:
        rules_data = json.load(fh)
    rule_list = [r for r in _list(rules_data, "rules") if isinstance(r, dict)]
    rules = {r.get("id"): r for r in rule_list}
    tokens = rules_data.get("tokens") if isinstance(rules_data, dict) else None
    internal = _internal_ids(rule_list, placed_rules, tokens if isinstance(tokens, list) else [])
    questions, skipped = [], []
    for placed in placed_rules:
        rid = placed.get("rule_id")
        rule = rules.get(rid)
        if rule is None:
            skipped.append({"rule_id": rid, "reason": "placed rule has no entry in %s" % RULES.replace(os.sep, "/")})
            continue
        try:
            reads = expr.judgment_reads(rule.get("expression"))
        except expr.ExprError as exc:
            skipped.append({"rule_id": rid, "reason": "expression cannot be read: %s" % exc})
            continue
        if not reads:
            continue
        prose = rule.get("prose")
        decision = placed.get("decision") or rule.get("decision")
        if not isinstance(prose, str) or not prose.strip():
            skipped.append({"rule_id": rid, "reason": "judged rule has no prose"})
            continue
        if not isinstance(decision, str) or not decision.strip():
            skipped.append({"rule_id": rid, "reason": "judged rule has no decision"})
            continue
        text = _question(decision, prose, internal)
        hits = sorted(i for i in internal if i in text)
        if hits:
            skipped.append({"rule_id": rid, "reason": "question text would carry internal ids: %s" % ", ".join(hits)})
            continue
        questions.append({"rule_id": rid, "question": text, "decision": decision.strip(), "reads": sorted(set(reads)),
                          "advisory": True, "verdict_format": VERDICT_FORMAT})
    return {"questions": questions, "skipped": skipped}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--run", required=True)
    args = ap.parse_args(argv)
    try:
        data = build(args.run)
    except (OSError, ValueError) as exc:
        print("cannot read placed rules or rules: %s" % exc, file=sys.stderr)
        return 2
    os.makedirs(os.path.join(args.run, os.path.dirname(OUT)), exist_ok=True)
    with open(os.path.join(args.run, OUT), "w", encoding="utf-8") as fh:
        fh.write(json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n")
    print("wrote %s: %d questions, %d skipped" % (OUT, len(data["questions"]), len(data["skipped"])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
