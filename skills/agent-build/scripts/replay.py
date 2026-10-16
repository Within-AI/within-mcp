#!/usr/bin/env python3
"""Replay the rules that stop work against past cases (P2b, P3 `records/replay.json`).

Usage: replay.py --run R --cases DIR --command CMD...

Each past case file (pins G31: the case fields plus "history": {rule_id: true|false|null})
runs through _records.run_from_copy(R, CMD, case, "replay"). Per rule in R/brief.json with
stops_work true it counts fire, no_fire, agree, disagree and needs_more_information.
No pass threshold: exit 0 whenever the record is written. Exit 1 (record written,
verdict fail) when the package changed; exit 2 writing nothing on usage errors, a missing
or unreadable brief.json, an empty or missing DIR, or an entrypoint outside the package.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _records  # noqa: E402

NMI = "needs_more_information"
LABEL = "replay"


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        sys.stderr.write("replay.py: %s\n" % message)
        sys.exit(_records.USAGE)


def _usage(message):
    sys.stderr.write("replay.py: %s\n" % message)
    return _records.USAGE


def _rel(path, run):
    return os.path.relpath(os.path.realpath(path), os.path.realpath(run)).replace(os.sep, "/")


def stop_rule_ids(brief):
    """Return (ids, error) for brief rules[] entries with stops_work true."""
    rules = brief.get("rules") if isinstance(brief, dict) else None
    if not isinstance(rules, list):
        return None, "brief.json has no rules[] list"
    ids = []
    for rule in rules:
        if not isinstance(rule, dict) or not isinstance(rule.get("id"), str):
            return None, "brief.json rules[] entry without an id"
        if rule.get("stops_work") is True and rule["id"] not in ids:
            ids.append(rule["id"])
    return ids, None


def history_values(cases, rule_ids):
    """Map (case index, rule id) -> bool, or a reason string when it is needs more information."""
    by_case_id = {}
    for i, (_, doc, _) in enumerate(cases):
        if isinstance(doc, dict) and isinstance(doc.get("case_id"), str):
            by_case_id.setdefault(doc["case_id"], []).append(i)
    result = {}
    for i, (name, doc, err) in enumerate(cases):
        for rule_id in rule_ids:
            if err is not None:
                result[(i, rule_id)] = "case file unreadable (%s)" % err
                continue
            history = doc.get("history")
            if not isinstance(history, dict) or rule_id not in history or history[rule_id] is None:
                result[(i, rule_id)] = "no history value"
                continue
            value = history[rule_id]
            if not isinstance(value, bool):
                result[(i, rule_id)] = "history value is not a boolean"
                continue
            seen = set()
            for j in by_case_id.get(doc.get("case_id"), []):
                other = cases[j][1].get("history")
                if isinstance(other, dict):
                    seen.add(repr(other.get(rule_id)))
            if len(seen) > 1:
                result[(i, rule_id)] = "case_id %s has conflicting history values" % doc.get("case_id")
                continue
            result[(i, rule_id)] = value
    return result


def fired_value(output, rule_id):
    """Return the bool the run reports for rule_id, or a reason string."""
    if not isinstance(output, dict) or output.get("exit") == "crashed":
        error = output.get("error") if isinstance(output, dict) else None
        return "run crashed (%s)" % (error or "no output")
    results = output.get("rule_results")
    values = set()
    if isinstance(results, list):
        for entry in results:
            if isinstance(entry, dict) and entry.get("rule_id") == rule_id:
                values.add(repr(entry.get("fired")))
                fired = entry.get("fired")
    if not values:
        return "rule_results lack the rule"
    if len(values) > 1 or not isinstance(fired, bool):
        return "rule_results give no single boolean for the rule"
    return fired


def main(argv=None):
    parser = _Parser(prog="replay.py", allow_abbrev=False,
                     description="Write R/records/replay.json from past cases.")
    parser.add_argument("--run")
    parser.add_argument("--cases")
    parser.add_argument("--command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)

    if not args.run:
        return _usage("--run is required")
    if not os.path.isdir(args.run):
        return _usage("--run is not a folder: %s" % args.run)
    if not args.cases:
        return _usage("--cases is required")
    if not args.command:
        return _usage("--command needs at least one argument")
    run = args.run

    brief_path = os.path.join(run, "brief.json")
    brief, err = _records.load_json(brief_path)
    if err is not None:
        return _usage("brief.json: %s" % err)
    rule_ids, err = stop_rule_ids(brief)
    if err is not None:
        return _usage(err)

    cases_dir = args.cases if os.path.isabs(args.cases) else os.path.join(run, args.cases)
    if not os.path.isdir(cases_dir):
        return _usage("cases folder missing: %s" % args.cases)
    names = sorted(n for n in os.listdir(cases_dir)
                   if n.endswith(".json") and os.path.isfile(os.path.join(cases_dir, n)))
    if not names:
        return _usage("cases folder has no case files: %s" % args.cases)
    ok, reason = _records.entrypoint_in_package(run, args.command)
    if not ok:
        return _usage(reason)

    cases = []
    for name in names:
        doc, err = _records.load_json(os.path.join(cases_dir, name))
        if err is None and not isinstance(doc, dict):
            err = "not a JSON object"
        cases.append((name, doc, err))
    history = history_values(cases, rule_ids)

    inputs = {"brief.json": _records.sha256_file(brief_path)}
    for name in names:
        path = os.path.join(cases_dir, name)
        inputs[_rel(path, run)] = _records.sha256_file(path)
    sha_path = os.path.join(run, "package.sha256")
    package_sha = None
    if os.path.isfile(sha_path):
        with open(sha_path, encoding="utf-8") as f:
            package_sha = f.readline().strip() or None
    header = _records.record_header(_records.SKILL_VERSION, inputs, {"package_sha256": package_sha})

    counts = dict((r, {"rule_id": r, "fire": 0, "no_fire": 0, "agree": 0, "disagree": 0, NMI: 0})
                  for r in rule_ids)
    reasons = []
    out_path = os.path.join(run, "records", "replay.json")
    for i, (name, doc, err) in enumerate(cases):
        output = None
        if err is None:
            try:
                output, _ = _records.run_from_copy(run, args.command, os.path.join(cases_dir, name), LABEL)
            except ValueError as exc:
                return _usage(str(exc))
            except RuntimeError as exc:
                record = dict(header)
                record.update({"verdict": "fail", "reasons": [str(exc)], "rules": []})
                _write(out_path, record)
                return _records.FAILED
        for rule_id in rule_ids:
            known = history[(i, rule_id)]
            fired = fired_value(output, rule_id) if err is None else known
            row = counts[rule_id]
            if not isinstance(known, bool):
                row[NMI] += 1
                reasons.append("%s %s: %s: %s" % (name, rule_id, NMI, known))
                continue
            if not isinstance(fired, bool):
                row[NMI] += 1
                reasons.append("%s %s: %s: %s" % (name, rule_id, NMI, fired))
                continue
            row["fire" if fired else "no_fire"] += 1
            row["agree" if fired == known else "disagree"] += 1

    record = dict(header)
    record.update({"verdict": "pass", "reasons": reasons, "rules": [counts[r] for r in rule_ids]})
    _write(out_path, record)
    return _records.OK


def _write(path, record):
    parent = os.path.dirname(path)
    if not os.path.isdir(parent):
        os.makedirs(parent)
    _records.write_record(path, record)


if __name__ == "__main__":
    sys.exit(main())
