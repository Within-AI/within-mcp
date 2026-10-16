"""Agent Build: stop-rule coverage over a label's raw outputs (criterion 10; pins P3, H16).

Usage: stoprules.py --run R --label LABEL

Every brief rule with stops_work: true needs a fired case (completed or non-crashed refused,
frozen, rule fired with effect stop|escalate, comparison pass, no write call after the firing)
and a not-fired case (completed, frozen, rule not fired, comparison pass). Writes
R/records/stoprules.json. Exit 0 pass, 1 fail, 3 a rule's stops_work is missing or not a
boolean, 2 usage or unreadable inputs (nothing written).

Entry point for the exit gate: evaluate(run, label) -> record dict (never writes).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import core  # noqa: E402

CAPABILITY = "Stop-rule coverage applies when any rule has stops_work: true"


def _rule_result(output, rule_id):
    for r in output.get("rule_results") or []:
        if isinstance(r, dict) and r.get("rule_id") == rule_id:
            return r
    return None


def _write_after(output, firing_seq):
    """Return (problem, writes_after) for the firing seq; problem names missing seq."""
    if not core.is_int(firing_seq):
        return "firing has no seq", None
    later = []
    for call in output.get("tool_calls") or []:
        if not isinstance(call, dict) or call.get("kind") != "write":
            continue
        if not core.is_int(call.get("seq")):
            return "write call %r has no seq" % call.get("name"), None
        if call["seq"] > firing_seq:
            later.append(call)
    return None, later


def assess_rule(ctx, rule_id, comparisons):
    fired, not_fired, problems, missing_seq = [], [], [], []
    for cid in sorted(ctx["outputs"]):
        out = ctx["outputs"][cid]
        if cid not in ctx["frozen"] or not core.executed(out):
            continue
        r = _rule_result(out, rule_id)
        if r is None:
            continue
        state = comparisons[cid]
        if r.get("fired") is True and r.get("effect") in ("stop", "escalate"):
            # Ordering evidence is checked first, so a failed comparison cannot hide a missing seq.
            seq_problem, later = _write_after(out, r.get("seq"))
            if seq_problem:
                problems.append("%s: %s" % (cid, seq_problem))
                missing_seq.append(cid)
                continue
            if state["state"] != "pass":
                problems.append("%s fired but its comparison is %s (%s)" % (cid, state["state"], state.get("reason")))
                continue
            if later:
                problems.append("%s: write call %r at seq %s after the firing at seq %s"
                                % (cid, later[0].get("name"), later[0].get("seq"), r.get("seq")))
                continue
            fired.append(cid)
        elif r.get("fired") is False and out.get("exit") == "completed":
            if state["state"] != "pass":
                problems.append("%s did not fire but its comparison is %s (%s)"
                                % (cid, state["state"], state.get("reason")))
                continue
            not_fired.append(cid)
    reasons = list(problems)
    if not fired:
        reasons.append("no fired case that took hold")
    if not not_fired:
        reasons.append("no not-fired case")
    # Missing ordering evidence fails coverage even beside another good firing case.
    ok = bool(fired) and bool(not_fired) and not missing_seq
    return {"rule_id": rule_id, "fired_cases": fired, "not_fired_cases": not_fired,
            "effect_took_hold": bool(fired) and not missing_seq,
            "verdict": "pass" if ok else "fail",
            "reason": "; ".join(reasons) if not ok else "fired and not-fired cases pass"}


def evaluate(run, label):
    """Build the stoprules.json record (pins G11). Never writes."""
    ctx = core.load_run(run, label)
    replay_path = os.path.join(run, "records", "replay.json")
    replay = "records/replay.json" if os.path.isfile(replay_path) else None
    record = core.header(ctx)
    record["replay"] = replay
    if core.package_changed(ctx):
        record.update({"verdict": "fail", "reasons": ["package changed"], "rules": []})
        return record
    rules = ctx["brief"].get("rules")
    if not isinstance(rules, list):
        raise core.InputError("brief.json rules is not a list")
    bad = [r.get("id") if isinstance(r, dict) else r for r in rules
           if not isinstance(r, dict) or not isinstance(r.get("stops_work"), bool)]
    if bad:
        record.update({"verdict": "needs_person", "rules": [],
                       "reasons": ["rule %s: stops_work is missing or not a boolean" % b for b in bad]})
        return record
    stop_rules = [r for r in rules if r["stops_work"] is True]
    if not stop_rules:
        record.update({"verdict": "pass", "rules": [],
                       "reasons": ["does not apply: no rule has stops_work: true (%s)" % CAPABILITY]})
        return record
    comparisons = {}
    for cid in ctx["outputs"]:
        if cid in ctx["frozen"]:
            comparisons[cid] = core.compare(ctx, cid, ctx["outputs"][cid])
    results = [assess_rule(ctx, r.get("id"), comparisons) for r in stop_rules]
    reasons = ["rule %s: %s" % (r["rule_id"], r["reason"]) for r in results if r["verdict"] != "pass"]
    record.update({"rules": results, "verdict": "fail" if reasons else "pass", "reasons": reasons})
    return record


def main(argv=None):
    return core.cli(argv, "Stop-rule coverage over a label's outputs.", [],
                    lambda a: evaluate(a.run, a.label), "records/stoprules.json")


if __name__ == "__main__":
    sys.exit(main())
