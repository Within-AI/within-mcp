"""Shared helpers for the simulation reads: loading run files, schema checks and check reports."""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
AD = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(AD, "simulation", "walk"))
sys.path.insert(0, os.path.join(AD, "scripts"))
import schema_check  # noqa: E402  (U20a's standard-library schema reader, used read-only)

LISTS = os.path.join(AD, "formats", "lists")
RUN_FORMATS = os.path.join(AD, "formats", "run")
RULES = "stages/rules/rules.json"
SIM_INPUT = "simulation/sim-input.json"
WALK = "simulation/walk.json"
FALLBACK = "none: a script check, no agent involved"


class Unreadable(Exception):
    """A required run file is missing or not JSON; the message names the run-relative file."""


class Invalid(Exception):
    """A required run file parses but does not fit its schema; the message names the file and the first problem."""


def load(run_dir, rel, schema=None):
    path = os.path.join(run_dir, rel)
    if not os.path.isfile(path):
        raise Unreadable("%s is missing" % rel)
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError, UnicodeDecodeError):
        raise Unreadable("%s cannot be read as JSON" % rel)
    if schema:
        problems = schema_check.problems(schema, doc)
        if problems:
            raise Invalid("%s does not fit %s: %s" % (rel, os.path.basename(schema), problems[0]))
    return doc


def write_json(path, doc):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2, sort_keys=True)
        fh.write("\n")


def report(name, problems, counts, **extra):
    """A check report in U1's check-report shape; exit code follows the result."""
    errored = any(p.get("error") for p in problems)
    clean = [{k: p[k] for k in ("defect", "where", "detail")} for p in problems]
    result = "error" if errored else ("fail" if clean else "pass")
    doc = {"check": name, "path": "skills/agent-design/simulation/reads/%s.py" % name, "run": ".",
           "exit_code": {"pass": 0, "fail": 1, "error": 2}[result], "result": result, "problems": clean,
           "counts": counts, "agent_fallback": FALLBACK}
    doc.update(extra)
    return doc


def check_main(name, check, argv):
    """The check entry point (pins C1): --run RUN [--out report.json]; default out RUN/checks/<name>.json."""
    import argparse
    ap = argparse.ArgumentParser(description="%s check" % name)
    ap.add_argument("--run", required=True)
    ap.add_argument("--out")
    args = ap.parse_args(argv)
    doc = check(args.run)
    out = args.out or os.path.join(args.run, "checks", "%s.json" % name)
    if os.path.isdir(args.run):
        write_json(out, doc)
    for p in doc["problems"]:
        print("%s: %s at %s: %s" % (name, p["defect"], p["where"], p["detail"]))
    print("%s: %s" % (name, doc["result"]))
    return doc["exit_code"]
