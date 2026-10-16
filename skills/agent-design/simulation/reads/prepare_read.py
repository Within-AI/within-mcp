#!/usr/bin/env python3
"""Prepare the reader's input folder for the independent read of each rule's expression against its prose.

Usage: prepare_read.py --run RUN

Reads stages/rules/rules.json and writes RUN/simulation/reads/input/rules.json holding, per rule, only its
id, its prose (the When/Then text as the rules stage wrote it) and its structured expression. Nothing else
goes in the folder: no case, no expected value, no walk result, no other stage's file.
Exit 0 written; 2 rules file missing, unreadable or without a rules list (nothing written).
"""
import argparse
import os
import shutil
import sys

import _common

INPUT_DIR = "simulation/reads/input"


def prepare(run_dir):
    doc = _common.load(run_dir, _common.RULES)
    rules = doc.get("rules") if isinstance(doc, dict) else None
    if not isinstance(rules, list) or not all(isinstance(r, dict) and "id" in r for r in rules):
        raise _common.Invalid("%s has no rules list with an id on every rule" % _common.RULES)
    out_dir = os.path.join(run_dir, INPUT_DIR)
    if os.path.isdir(out_dir):
        shutil.rmtree(out_dir)
    body = {"rules": [{"id": r["id"], "prose": r.get("prose"), "expression": r.get("expression")} for r in rules]}
    _common.write_json(os.path.join(out_dir, "rules.json"), body)
    return body


def main(argv=None):
    ap = argparse.ArgumentParser(description="Write the reader's input folder for the encoding read.")
    ap.add_argument("--run", required=True)
    args = ap.parse_args(argv)
    try:
        body = prepare(args.run)
    except (_common.Unreadable, _common.Invalid) as exc:
        print("prepare_read: stopped: %s; nothing written" % exc, file=sys.stderr)
        return 2
    print("prepare_read: %d rules written to %s/rules.json" % (len(body["rules"]), INPUT_DIR))
    return 0


if __name__ == "__main__":
    sys.exit(main())
