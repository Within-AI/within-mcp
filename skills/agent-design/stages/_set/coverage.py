"""Set coverage: placed rules and test cases per process (U30).

report(run_dir) -> dict reads stages/intake/intake.json, stages/placement/placed-rules.json and
stages/test-suite/test-suite.json, counts per intake process, and writes <run_dir>/page/set-coverage.json.

Process tags: a placed rule names its process in `process_id` (a `process_ids` list is also read); a case names it
as the first part of its qualified `decision` (`<process_id>:<node_id>`, pins H2), because U2's case format holds no
separate field. A rule placed in several processes counts once per process it names. A missing or unreadable source
raises ValueError naming it (missing is never zero). An entry with no tag is counted under `untagged`, never given to
a process; a tag not in the intake is listed under `unknown_process_ids`.

Usage: coverage.py --run RUN   (prints the report; exit 0, or 2 when a source cannot be read)
"""
import json
import os
import sys

INTAKE = "stages/intake/intake.json"
PLACED = "stages/placement/placed-rules.json"
SUITE = "stages/test-suite/test-suite.json"
OUT = "page/set-coverage.json"


def _load(run_dir, rel):
    path = os.path.join(run_dir, rel)
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError) as e:
        raise ValueError("%s cannot be read: %s" % (rel, type(e).__name__))


def _list(doc, key, rel):
    items = doc.get(key) if isinstance(doc, dict) else None
    if not isinstance(items, list):
        raise ValueError("%s has no %s list" % (rel, key))
    return items


def _rule_tags(pr):
    tags = []
    if isinstance(pr.get("process_id"), str) and pr["process_id"]:
        tags.append(pr["process_id"])
    for p in pr.get("process_ids") or []:
        if isinstance(p, str) and p and p not in tags:
            tags.append(p)
    return tags


def _case_tags(case):
    d = case.get("decision")
    if isinstance(d, str) and ":" in d and d.split(":", 1)[0]:
        return [d.split(":", 1)[0]]
    return []


def report(run_dir):
    intake = _load(run_dir, INTAKE)
    order = []
    for p in _list(intake, "processes", INTAKE):
        pid = p.get("process_id") if isinstance(p, dict) else None
        if not isinstance(pid, str) or not pid:
            raise ValueError("%s has a process with no process_id" % INTAKE)
        order.append(pid)
    placed_doc = _load(run_dir, PLACED)
    # placed-rules.json is a list (U14); an older `{"placed_rules": [...]}` wrapper is also read
    placed = placed_doc if isinstance(placed_doc, list) else _list(placed_doc, "placed_rules", PLACED)
    cases = _list(_load(run_dir, SUITE), "cases", SUITE)

    rules_by = {pid: set() for pid in order}
    cases_by = {pid: set() for pid in order}
    untagged = {"rules": [], "cases": []}
    unknown = set()
    for n, pr in enumerate(placed):
        pr = pr if isinstance(pr, dict) else {}
        rid = pr.get("rule_id") or "placed rule %d" % (n + 1)
        tags = _rule_tags(pr)
        if not tags and rid not in untagged["rules"]:
            untagged["rules"].append(rid)
        for t in tags:
            if t in rules_by:
                rules_by[t].add(rid)
            else:
                unknown.add(t)
    for n, case in enumerate(cases):
        case = case if isinstance(case, dict) else {}
        cid = case.get("id") or "case %d" % (n + 1)
        tags = _case_tags(case)
        if not tags:
            untagged["cases"].append(cid)
        for t in tags:
            if t in cases_by:
                cases_by[t].add(cid)
            else:
                unknown.add(t)

    processes = [{"process_id": pid, "rules": len(rules_by[pid]), "cases": len(cases_by[pid]),
                  "rule_ids": sorted(rules_by[pid]), "case_ids": sorted(cases_by[pid])} for pid in order]
    no_rules = [p["process_id"] for p in processes if p["rules"] == 0]
    no_cases = [p["process_id"] for p in processes if p["cases"] == 0]
    out = {
        "schema": "agent-design.set-coverage/1",
        "sources": [INTAKE, PLACED, SUITE],
        "processes": processes,
        "uncovered": {"no_rules": no_rules, "no_cases": no_cases,
                      "processes": [pid for pid in order if pid in no_rules or pid in no_cases]},
        "untagged": {"rules": len(untagged["rules"]), "cases": len(untagged["cases"]),
                     "rule_ids": untagged["rules"], "case_ids": untagged["cases"]},
        "unknown_process_ids": sorted(unknown),
        "totals": {"placed_rules": len(placed), "cases": len(cases)},
    }
    os.makedirs(os.path.join(run_dir, "page"), exist_ok=True)
    with open(os.path.join(run_dir, OUT), "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, sort_keys=True)
        f.write("\n")
    return out


def main(argv):
    if len(argv) != 3 or argv[1] != "--run":
        print("usage: coverage.py --run RUN")
        return 2
    try:
        print(json.dumps(report(argv[2]), indent=2, sort_keys=True))
    except ValueError as e:
        print("refused: %s" % e)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
