#!/usr/bin/env python3
"""Check a snapshot-intake.json before it is frozen into snapshot.json.

Usage: check-snapshot-intake.py SNAPSHOT-INTAKE.json

First checks the optional scope.key_control: an object with one key per id in scope.process_ids, each value
true, false or the string "needs more information". It fails on a key that is not in scope.process_ids, on an id
with no key, and on any other value. An intake without scope.key_control (legacy) stays valid; readers treat every
process as "needs more information". When key_control passes, it then runs the shared snapshot-intake rules
(scope, sources, raw pulls, mappings, issues).
Prints PASS snapshot-intake, or FAIL snapshot-intake: <detail> on stderr. Exit 0 pass, 1 fail, 2 usage.
"""
import json
import sys

from workflow_contract import main

NEEDS_INFO = "needs more information"


def key_control_problem(path):
    """The first problem with scope.key_control, or None when it is valid, absent, or the file can't be read here."""
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
    except (OSError, ValueError):
        return None  # the shared rules report an unreadable intake
    scope = doc.get("scope") if isinstance(doc, dict) else None
    if not isinstance(scope, dict) or "key_control" not in scope:
        return None
    kc, ids = scope["key_control"], scope.get("process_ids")
    if not isinstance(kc, dict):
        return "scope.key_control must be an object keyed by process id"
    ids = ids if isinstance(ids, list) else []
    for pid, value in kc.items():
        if pid not in ids:
            return "scope.key_control names %r, which is not in scope.process_ids" % pid
        if not (value is True or value is False or value == NEEDS_INFO):
            return "scope.key_control[%r] is %s; use true, false or \"%s\"" % (pid, json.dumps(value), NEEDS_INFO)
    missing = [p for p in ids if p not in kc]
    if missing:
        return "scope.key_control has no entry for %s" % ", ".join(map(str, missing))
    return None


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    problem = key_control_problem(args[0]) if len(args) == 1 else None
    if problem:
        print("FAIL snapshot-intake: %s" % problem, file=sys.stderr)
        raise SystemExit(1)
    raise SystemExit(main("snapshot-intake"))
