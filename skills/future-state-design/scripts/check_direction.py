#!/usr/bin/env python3
"""Design-direction check: the transformation map carries every Agent Recommendations direction item.

Usage: check_direction.py RUN_ROOT

THE INPUT: `RUN_ROOT/inputs/design-direction.json` (`items[].id`, DD-1, DD-2, ...) and the section `## Design direction` of
`RUN_ROOT/stages/transformation-plan/transformation-map.md`, a table with the header
`| Item | Followed | How the map carries it |`. Every item id has exactly one row. Followed is `yes` or `departed: <reason>` with a
non-empty reason. How the map carries it is non-empty. A row whose Item is a process the recommendation leaves out (in
`inputs/snapshot.json` `scope.process_ids`, not in the direction's `process_ids`) is the row the stage instructions require
for that process; it is not an unknown item here, and the writes-and-scope check judges its value. The section holds the
table as one unbroken block of lines that each start and end with `|`, header and separator first; any other line in the
section with a pipe outside inline code fails, naming its line. The section is the first `## Design direction` heading
outside fenced blocks, read by the same strict table rule v2 reader as the writes-and-scope check (`section_table`).

Exit 0 pass (or no direction recorded); 1 a rule fails (each failure printed); 2 the inputs cannot be read. Stdlib only.
"""
import json, os, re, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from check_writes_and_scope import section_table  # noqa: E402


def left_out_processes(run, direction):
    """Snapshot processes the direction does not cover; empty when either list is missing or unreadable."""
    try:
        snap = json.load(open(os.path.join(run, "inputs", "snapshot.json"), encoding="utf-8"))
        scope = snap.get("scope", {}).get("process_ids")
        covered = direction.get("process_ids")
    except (OSError, ValueError, AttributeError):
        return set()
    if not isinstance(scope, list) or not isinstance(covered, list):   # an empty covered list leaves every process out
        return set()
    return {str(p) for p in scope} - {str(p) for p in covered}


def main(argv):
    run = argv[1]
    dp = os.path.join(run, "inputs", "design-direction.json")
    if not os.path.isfile(dp):
        print("no direction recorded; nothing to check"); return 0
    try:
        direction = json.load(open(dp, encoding="utf-8"))
        ids = [i["id"] for i in direction["items"]]
        text = open(os.path.join(run, "stages", "transformation-plan", "transformation-map.md"), encoding="utf-8").read()
    except (OSError, ValueError, KeyError) as e:
        print("cannot read inputs: %s" % e); return 2
    table, problems = section_table(text, "## Design direction")
    if table is None:
        print("FAIL: no '## Design direction' section in the transformation map"); return 1
    rows = {}
    fails = ["Design direction line %d holds a pipe outside the table (a row without both outer pipes, a row after a "
             "blank line, or a second table): %r" % (n, ln) for n, ln in problems]
    header = False
    for cells in table:
        ln = "| %s |" % " | ".join(cells)
        if not header and [c.lower() for c in cells[:3]] == ["item", "followed", "how the map carries it"]:
            header = True; continue
        if len(cells) < 3:
            fails.append("row has fewer than 3 cells: %s" % ln); continue
        item = cells[0].strip("`* ")
        if item in rows:
            fails.append("%s has more than one row" % item)
        rows[item] = cells
    if not header:
        fails.append("table header '| Item | Followed | How the map carries it |' not found")
    for i in ids:
        if i not in rows:
            fails.append("%s has no row" % i); continue
        f, how = rows[i][1], rows[i][2]
        if not (f.lower() == "yes" or re.match(r"(?i)departed:\s*\S", f)):
            fails.append("%s: Followed is %r, not 'yes' or 'departed: <reason>'" % (i, f))
        if not how or how in ("-", "—"):
            fails.append("%s: How the map carries it is empty" % i)
    extra = sorted(set(rows) - set(ids) - left_out_processes(run, direction))
    if extra:
        fails.append("rows for unknown items: %s" % ", ".join(extra))
    departed = [i for i in ids if i in rows and rows[i][1].lower().startswith("departed")]
    for f in fails:
        print("FAIL:", f)
    print("direction items %d, rows %d, departed %d%s" % (len(ids), len(rows), len(departed), (": " + ", ".join(departed)) if departed else ""))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
