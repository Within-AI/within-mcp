#!/usr/bin/env python3
"""The coherence gate as a check of its own stage (ad.py runs it at finish of coherence-gate).

    check_coherence_gate.py --run RUN --out FILE

Reads the gate's own report, RUN/stages/coherence-gate/report.json, when it is newer than the coherence-gate stage's
start in RUN/state.json; otherwise runs `coherence_gate.py run --run RUN` once and reads the report it leaves. Writes
a check report to FILE: result pass (exit 0), fail with the gate's failing items (exit 1), or unchecked (exit 2) when
the gate left no current, readable report. The gate script itself is never changed here. Stdlib only, Python 3.9.
"""
import argparse, json, os, subprocess, sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
GATE = os.path.join(SKILL, "scripts", "coherence_gate.py")
REPORT = os.path.join("stages", "coherence-gate", "report.json")
PATH = "skills/agent-design/checks/check_coherence_gate.py"
FALLBACK = ("If this check cannot run, open stages/coherence-gate/report.json, or run each stage check by hand, and "
            "list every failing or unchecked item with its file and id.")


def stage_start(run):
    try:
        with open(os.path.join(run, "state.json"), encoding="utf-8") as f:
            s = json.load(f)["stages"]["coherence-gate"]["started_at"]
        return datetime.fromisoformat(s).timestamp()
    except (OSError, ValueError, KeyError, TypeError):
        return None


def current_report(run):
    """The gate report when it exists, is newer than the stage start and parses as an object; else None."""
    p = os.path.join(run, REPORT)
    if not os.path.isfile(p):
        return None, "no gate report"
    t0 = stage_start(run)
    if t0 is not None and os.path.getmtime(p) < t0:
        return None, "the gate report is older than the coherence-gate stage start"
    try:
        with open(p, encoding="utf-8") as f:
            doc = json.load(f)
    except (OSError, ValueError) as e:
        return None, "the gate report cannot be read (%s)" % e
    if not isinstance(doc, dict) or doc.get("result") not in ("pass", "fail"):
        return None, "the gate report has no pass or fail result"
    return doc, ""


def failing_items(doc):
    items = []
    for r in doc.get("checks") or []:
        if isinstance(r, dict) and r.get("state") != "passed":
            items.append({"item": r.get("name"), "state": r.get("state"), "reason": r.get("reason")})
    return items


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--run", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    run = os.path.abspath(a.run)
    doc, why = current_report(run)
    gate_exit = None
    if doc is None:
        try:
            p = subprocess.run([sys.executable, GATE, "run", "--run", run], capture_output=True, text=True,
                               timeout=1800, env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
            gate_exit = p.returncode
        except (OSError, subprocess.SubprocessError) as e:
            why = "the gate could not run (%s)" % e
        else:
            doc, why2 = current_report(run)
            if doc is None:
                why = "the gate exited %d and %s" % (gate_exit, why2)
    out = {"check": "check_coherence_gate", "path": PATH, "run": REPORT, "agent_fallback": FALLBACK,
           "gate_exit": gate_exit}
    if doc is None:
        out.update(result="unchecked", exit_code=2, problems=[{"defect": "unchecked", "where": REPORT, "detail": why}],
                   counts={"failing": 0})
        code = 2
    elif doc["result"] == "pass":
        out.update(result="pass", exit_code=0, problems=[], counts={"failing": 0, "gate": doc.get("counts")})
        code = 0
    else:
        problems = [p for p in doc.get("problems") or [] if isinstance(p, dict)]
        items = failing_items(doc)
        out.update(result="fail", exit_code=1, problems=problems, failing=items,
                   counts={"failing": len(items) or len(problems) or 1, "gate": doc.get("counts")})
        code = 1
    d = os.path.dirname(os.path.abspath(a.out))
    os.makedirs(d, exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)
    print("check_coherence_gate: %s%s" % (out["result"], "" if code != 2 else " (%s)" % why))
    return code


if __name__ == "__main__":
    sys.exit(main())
