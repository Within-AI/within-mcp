#!/usr/bin/env python3
"""Screen the frozen judgment text the simulation shows, without changing it.

Usage: screen_judgments.py --run RUN [--out report.json]   (default report RUN/checks/screen_judgments.json)

Reads each case's `judgments{}` in RUN/simulation/sim-input.json and the step rows in RUN/simulation/walk.json
(neither is written). Flags: a judgment step with no frozen text; an internal code; a field name; the words
null or None or empty brackets; a number not among the values the step reads or writes; a banned or retired
term from formats/lists/vocabulary.json. Flags go to the report only; they never stop the walk result.
Exit 0 clean; 1 any flag; 2 sim-input.json or walk.json missing, unreadable or schema-invalid (no report).
"""
import json
import os
import re
import sys
from decimal import Decimal, InvalidOperation

import _common
import expr

NAME = "screen_judgments"
SIM_SCHEMA = os.path.join(_common.RUN_FORMATS, "sim-input.schema.json")
WALK_SCHEMA = os.path.join(_common.RUN_FORMATS, "simulation.schema.json")

# Copied from Fast Mode's skills/fast-mode-build/page/check/text_rules.json: plain.id_regexes.
ID_REGEXES = [
    r"\bR-[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*\b",
    r"\bI[RCHW]-\d(?:-fm-\d)?(?:\.\w+)?\b",
    r"\b(?:n|s|eg|iss|fx|bm|ag|an)-[a-z0-9]+(?:-[a-z0-9]+)*\b",
    r"\b[A-Z]{1,3}-[A-Z]?\d+\b",
]
# Copied from the same file: plain.field_token_regexes.
FIELD_REGEXES = [
    r"\b[a-z]+(?:_[a-z0-9]+)+\b",
    r"\b[A-Za-z0-9-]+\.[a-z_]{3,}\b(?!\s)",
]
NULL_WORDS = re.compile(r"(?<![A-Za-z])(?:[Nn][Uu][Ll][Ll]|None)(?![A-Za-z])|\[\s*\]|\{\s*\}|\(\s*\)")
CURRENCY = u"$€£¥₹"
NUMBER = re.compile(u"[-+]?[%s]?(?:\\d{1,3}(?:,\\d{3})+|\\d+)(?:\\.\\d+)?%%?" % re.escape(CURRENCY))


def _vocabulary():
    with open(os.path.join(_common.LISTS, "vocabulary.json"), encoding="utf-8") as fh:
        doc = json.load(fh)
    return [(t["name"], re.compile(t["pattern"], re.I)) for t in doc.get("banned", []) + doc.get("retired", [])]


def _decimal(text):
    """The magnitude of a recognized number: sign, currency mark, `%` and thousands commas are each stripped on
    their own, wherever they sit (criterion 4 compares numbers after stripping those, so -950 and 950 match)."""
    digits = "".join(c for c in text.strip() if c not in CURRENCY and c not in "+-%,")
    try:
        return abs(Decimal(digits))
    except (InvalidOperation, AttributeError):
        return None


def _numbers_in(value, out):
    """Every numeric value in a recorded read or write (numbers, decimal strings, nested lists and dicts)."""
    if isinstance(value, bool) or value is None:
        return
    if isinstance(value, (int, float)):
        out.add(Decimal(repr(value)))
    elif isinstance(value, str):
        if re.match(r"^\s*-?\d+(\.\d+)?\s*$", value):
            out.add(Decimal(value.strip()))
    elif isinstance(value, dict):
        for v in value.values():
            _numbers_in(v, out)
    elif isinstance(value, list):
        for v in value:
            _numbers_in(v, out)


def screen_text(text, allowed, vocab):
    """Flags for one frozen judgment text: list of (defect, detail)."""
    flags = []
    for pattern in ID_REGEXES:
        for m in re.finditer(pattern, text):
            flags.append(("internal-code", "internal code %r" % m.group(0)))
    for pattern in FIELD_REGEXES:
        for m in re.finditer(pattern, text):
            flags.append(("field-name", "field name %r" % m.group(0)))
    for m in NULL_WORDS.finditer(text):
        flags.append(("null-word", "%r in the text" % m.group(0)))
    magnitudes = {abs(a) for a in allowed}
    for m in NUMBER.finditer(text):
        n = _decimal(m.group(0))
        if n is None:
            flags.append(("number-not-in-step", "number %r could not be parsed, so it could not be compared with "
                          "the values the step reads or writes" % m.group(0)))
        elif n not in magnitudes:
            flags.append(("number-not-in-step", "number %r is not among the values the step reads or writes"
                          % m.group(0)))
    for name, pattern in vocab:
        if pattern.search(text):
            flags.append(("banned-term", "banned or retired term (%s)" % name))
    return flags


def check(run_dir):
    try:
        sim = _common.load(run_dir, _common.SIM_INPUT, SIM_SCHEMA)
        walk = _common.load(run_dir, _common.WALK, WALK_SCHEMA)
    except (_common.Unreadable, _common.Invalid) as exc:
        return _common.report(NAME, [{"defect": "input-unreadable", "where": str(exc).split(" ")[0],
                                      "detail": str(exc), "error": True}], {})
    vocab = _vocabulary()
    judged = {r["id"] for r in sim["rules"] if expr.judgment_reads(r["expression"])}
    steps = {}
    for case in walk["cases"]:
        for s in case.get("steps") or []:
            steps[(case["case_id"], "%s|%s" % (s.get("rule_id"), s.get("item_key")))] = s
    problems, screened = [], 0
    for case in sim["cases"]:
        cid = case["id"]
        for key in case["holds"]:
            answer = case["judgments"].get(key)
            if key.split("|", 1)[0] in judged and (answer is None or (isinstance(answer, str) and not answer.strip())):
                problems.append({"defect": "judgment-text-missing", "where": "case %s judgment %s" % (cid, key),
                                 "detail": "judgment step %s has no frozen text" % key})
        for key, text in sorted(case["judgments"].items()):
            if not isinstance(text, str):
                continue  # a number, true/false or null answer has no wording to screen
            screened += 1
            allowed = set()
            step = steps.get((cid, key)) or {}
            for r in step.get("reads") or []:
                _numbers_in(r.get("value"), allowed)
            _numbers_in(step.get("writes"), allowed)
            for defect, detail in screen_text(text, allowed, vocab):
                problems.append({"defect": defect, "where": "case %s judgment %s" % (cid, key), "detail": detail})
    return _common.report(NAME, problems, {"judgments_screened": screened, "flags": len(problems)})


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="Screen frozen judgment text.")
    ap.add_argument("--run", required=True)
    ap.add_argument("--out")
    args = ap.parse_args(argv)
    doc = check(args.run)
    if doc["result"] == "error":
        print("screen_judgments: stopped: %s; nothing written" % doc["problems"][0]["detail"], file=sys.stderr)
        return 2
    return _common.check_main(NAME, lambda _run: doc, ["--run", args.run] + (["--out", args.out] if args.out else []))


if __name__ == "__main__":
    sys.exit(main())
