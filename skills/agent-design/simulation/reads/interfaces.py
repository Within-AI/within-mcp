#!/usr/bin/env python3
"""Derive each sub-agent's inputs and outputs at its interfaces from the simulation walk.

Usage: interfaces.py --run RUN

Reads RUN/simulation/walk.json (U20a) and writes RUN/simulation/interfaces.json. Steps are taken in walk
order from each case's step rows. A hand-over occurs wherever the next step's part differs from the current
one, or the next step is a person step (each person step is its own receiver, part `person`); every
occurrence is its own entry. The interface id is `<sending part>-><receiving part>`. The output is each value
the receiving step reads whose recorded origin is a step of the sending part; the input is every value the
receiving step reads, with its origin. Values are copied as the walk recorded them, never filled in.
Exit 0 written; 2 walk.json missing, unreadable or not valid against its schema (nothing written).
"""
import argparse
import os
import re
import sys

import _common

OUT = "simulation/interfaces.json"
WALK_SCHEMA = os.path.join(_common.RUN_FORMATS, "simulation.schema.json")
DROPPED = ("execution_error", "not_executed", "needs_more_information")
ORIGIN_STEP = re.compile(r"^set by (\S+) on ")
PERSON = "person"
NO_PART = "not given"


def _part(step):
    part = step.get("part") or NO_PART
    return PERSON if is_person(step) else part


def is_person(step):
    part = step.get("part") or ""
    return part == PERSON or part.startswith(PERSON + ":")


def handovers(case):
    steps = case.get("steps") or []
    rule_part = {s.get("rule_id"): _part(s) for s in steps}
    out = []
    for i in range(len(steps) - 1):
        send, recv = steps[i], steps[i + 1]
        if _part(send) == _part(recv) and not is_person(recv):
            continue
        sender = _part(send)
        reads = recv.get("reads") or []
        inputs = [{"token": r["token"], "value": r.get("value"), "origin": r.get("origin")} for r in reads]
        output = {}
        for r in reads:
            m = ORIGIN_STEP.match(r.get("origin") or "")
            if m and rule_part.get(m.group(1)) == sender:
                output[r["token"]] = r.get("value")
        out.append({"case_id": case["case_id"], "state": case["state"],
                    "interface": "%s->%s" % (sender, _part(recv)), "from_part": sender, "to_part": _part(recv),
                    "from_step": {"rule_id": send.get("rule_id"), "item_key": send.get("item_key")},
                    "to_step": {"rule_id": recv.get("rule_id"), "item_key": recv.get("item_key")},
                    "output": output, "input": inputs})
    return out


def derive(walk):
    entries, grouping, dropped = [], {}, []
    for case in walk["cases"]:
        for h in handovers(case):
            entries.append(h)
            for part in (h["from_part"], h["to_part"]):
                ids = grouping.setdefault(part, {}).setdefault(h["interface"], [])
                if case["case_id"] not in ids:
                    ids.append(case["case_id"])
        if case["state"] in DROPPED:
            dropped.append({"case_id": case["case_id"], "state": case["state"], "drop_label": case.get("drop_label")})
    return {"source": _common.WALK, "handovers": entries, "grouping": grouping, "dropped_cases": dropped}


def main(argv=None):
    ap = argparse.ArgumentParser(description="Derive interface inputs and outputs from the simulation walk.")
    ap.add_argument("--run", required=True)
    args = ap.parse_args(argv)
    try:
        walk = _common.load(args.run, _common.WALK, WALK_SCHEMA)
    except (_common.Unreadable, _common.Invalid) as exc:
        print("interfaces: stopped: %s; nothing written" % exc, file=sys.stderr)
        return 2
    doc = derive(walk)
    _common.write_json(os.path.join(args.run, OUT), doc)
    print("interfaces: %d hand-overs, %d dropped cases written to %s" % (
        len(doc["handovers"]), len(doc["dropped_cases"]), OUT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
