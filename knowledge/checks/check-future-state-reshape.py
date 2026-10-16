#!/usr/bin/env python3
"""Future-state reshape check: the future state is redrawn from the map's reshape, keeps every outcome, and keeps
every agent write whose confirmation the map keeps behind that confirmation. Usage: check-future-state-reshape.py <run_root>

<run_root> holds `stages/`; given a file, the nearest ancestor folder holding `stages/` is used. Reads
`stages/current-state/current-state.json`, `stages/future-state/future-state.json`, the future-state coverage
manifest (`stages/future-state/future-state-coverage-manifest*` when present, else the `coverage` list inside
future-state.json) and `stages/transformation-plan/transformation-map.md`. With no transformation map (a legacy or
detailed-mode run) it prints `PASS (no transformation map: reshape checks skipped)` and exits 0; with no `stages/`
folder at or above the argument (detailed mode keeps future.json at the run root) it prints
`PASS (no stages/ folder: reshape checks skipped)` and exits 0.

Table header cells are read bare (backticks and surrounding spaces stripped, as the map shape check does), so a
code-formatted heading such as `` `Who writes` `` reads the same as a plain one. Before any rule runs, each table the
check reads that is present must carry its required columns (Write grants: Grant, Who writes, Write kind, System of
record, Next rung; Reshape moves: Move, Kind, Current steps, Future steps, State; Outcomes: Current ending, Future
ending); a missing one fails `table columns` (`FAIL table columns: <table> table is missing <columns>`, exit 1) and
no other rule runs, so an unrecognised schema can never silently select zero rows.

It fails (one `FAIL <rule>: <detail>` line each) on:
- (a) id resolution: a step id in the Future steps cell of an adopted Reshape moves row (State not
  `not adopted: <reason>`), or an Outcomes Future ending other than `retired`, that is not a future-state node; or a
  `WG-<n>` named on a future-state node or edge that is not a row of the Write grants table.
- (b) moves land: a Reshape moves row that has neither landed nor a State of `not adopted: <reason>`; or no move has
  landed and the `### No reshape` lines do not cover all eight move kinds. A move has landed when its Future steps cell
  names at least one id and every named id is a future-state node (an empty, `—`, `n/a` or `none` cell has not
  landed); for `remove`, the cell may be empty, its current step ids are absent from the future graph and a coverage
  `summary` row cites the move id; for `merge`, at most one of its current step ids remains (as one of the move's
  future steps).
- (c) outcome guard: a current-state ending with no Outcomes row (matched in Current ending); or a named Future ending
  that does not exist or cannot be reached from a future start. Many-to-one and `retired` are allowed.
- (d) path check, write: a candidate is an agent-lane node (lane `Agent` or `Agent: <name>`) of any type other than
  start and end (a `decision` node included). It is in scope when it has an edge into a `system_of_record` surface
  that is not a read, whatever grant it names or does not name. Such an edge is a read only when its kind is
  `read_access`, or when its kind is `system` or absent and its label, with grant ids (`WG-<n>`) and surrounding
  spaces removed, is exactly the word `read` (case-insensitive). Every other edge into a system of record is a write,
  whatever its label says; a read whose label says more than `read` is marked `read_access`. It is also in
  scope when a grant with Who writes `agent`, Write kind `mechanical` and System of record `yes`/`unclear` is named in
  its title, detail, facts or write_grant(s), or in the label of one of its write edges (read edges are never searched
  for grants); or, naming no grant at all, when its type is `agent_write`/`write_interaction` and its `systems` include a
  `system_of_record` surface. So an agent step that only hands off to a person or dispatches to another agent lane,
  never writing into a system of record, needs no confirmation. Its own confirmations are
  person-lane nodes of type `decision`, `analyst_reconciliation` or `analyst_input`, or whose title says confirm,
  approve, review, decide or verify, found walking back from the write through agent-lane nodes (tier 1), and only if
  that finds none, through any node (tier 2). Since 0503 (R-writes) a selected write is judged when the future state
  draws a person confirmation before it (however the map carries the split, two grants included), or when the map
  keeps a confirmation step for it: `## Splits` names the write, a step in its `future.from` or a grant it names;
  or a Write grants row whose Steps name it (or a step in its `future.from`), or that it names, also names a
  person step (the split partner). A write with neither (values copied unchanged from a named source) needs none. Fails when it has none, or when the write is still reachable from a
  start once they are removed. Exempt: a selected write whose named grants (at least one) each carry Exception
  reason `own-working-surface` or `reversible-reviewed` in the Write grants table needs no confirmation. Lanes are
  read with whitespace trimmed and collapsed, as check-agent-architecture.py reads them.
- (e) path check, bypass: a protected step is a future-state person-lane node of type `write_interaction`,
  `analyst_reconciliation`, `decision` or `analyst_input`. Fails when an `exception` or `escalation` edge from a
  non-decision node leaves the nodes that can reach a protected step and reaches an ending that step also leads to,
  without passing it. An exception handler branching off the normal `sequence` is exempt.

- (f) control step (0503 R-people safeguard): an agent-lane node whose `future.from` names a step the map's
  `## Why people stay` lists with Reason `control` (read leniently by `_write_grants.why_people_stay`; its reading
  notes are printed as NOTE lines). The built agent would take an approval, sign-off or submission outside its
  authority.

It warns (`WARN <kind>: <detail>`, never a fail) on a loop that re-enters an agent write without re-confirmation, and
on the (e) skip through any other edge kind (handoff fan-outs and forks: routing or parallel work, or a real skip).
It prints a `NOTE <grant>: <detail>` for each flag-3 Write grants row (Who writes `person`, Next rung containing
"enter" and "confirmed", case-insensitive), re-derived from the map.

It writes `stages/future-state/reshape-check.json`:
`{"fails": [{"rule", "detail"}], "warnings": [{"kind", "detail"}], "notes": [{"grant", "detail"}]}`.
Exit 0 pass, 1 fail, 2 usage.
"""
import glob
import json
import os
import re
import sys
from collections import deque

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _write_grants  # noqa: E402  the one copy of the person-confirmation rule

MOVE_KINDS = ("route at entry", "consolidate repeated reads", "package handoffs", "improve decision quality",
              "consolidate exceptions", "remove", "merge", "reorder")
PROTECTED_TYPES = {"write_interaction", "analyst_reconciliation", "decision", "analyst_input"}
EXCEPTION_REASONS = {"own-working-surface", "reversible-reviewed"}
EMPTY_CELLS = {"", "-", "—", "none", "n/a"}
REQUIRED_COLUMNS = {
    "Write grants": ("Grant", "Who writes", "Write kind", "System of record", "Next rung"),
    "Reshape moves": ("Move", "Kind", "Current steps", "Future steps", "State"),
    "Outcomes": ("Current ending", "Future ending"),
}


def find_root(arg):
    path = os.path.abspath(arg)
    if os.path.isdir(os.path.join(path, "stages")):
        return path
    cur = path if os.path.isdir(path) else os.path.dirname(path)
    while True:
        if os.path.isdir(os.path.join(cur, "stages")):
            return cur
        up = os.path.dirname(cur)
        if up == cur:
            return None
        cur = up


def section_header(text, heading):
    """Bare header cells (backticks and spaces stripped) of the first table under a heading; None if no table."""
    m = re.search(r"^#+\s*" + re.escape(heading) + r"\s*$(.*?)(?=^#)", text + "\n#", re.M | re.S)
    if not m:
        return None
    lines = [ln for ln in m.group(1).splitlines() if ln.strip().startswith("|")]
    return [c.replace("`", "").strip() for c in lines[0].strip().strip("|").split("|")] if lines else None


def section_table(text, heading):
    """Rows (dicts keyed by bare header) of the first table under a heading, up to the next heading; None if absent."""
    m = re.search(r"^#+\s*" + re.escape(heading) + r"\s*$(.*?)(?=^#)", text + "\n#", re.M | re.S)
    if not m:
        return None
    lines = [ln for ln in m.group(1).splitlines() if ln.strip().startswith("|")]
    if len(lines) < 2:
        return []
    split = lambda ln: [c.strip() for c in ln.strip().strip("|").split("|")]
    header = [c.replace("`", "").strip() for c in split(lines[0])]
    return [dict(zip(header, split(ln))) for ln in lines[2:]]


def section_bullets(text, heading):
    m = re.search(r"^#+\s*" + re.escape(heading) + r"\s*$(.*?)(?=^#)", text + "\n#", re.M | re.S)
    return [ln.strip()[1:].strip() for ln in m.group(1).splitlines() if ln.strip().startswith("-")] if m else []


def cell_ids(cell):
    cell = (cell or "").replace("`", "").strip()
    if cell.lower() in EMPTY_CELLS:
        return []
    return re.findall(r"[A-Za-z0-9][A-Za-z0-9_.:\-]*", cell)


def lane_text(node):
    """The lane with whitespace normalised as check-agent-architecture.py does (trimmed, runs collapsed)."""
    lane = node.get("lane")
    return " ".join(lane.split()) if isinstance(lane, str) else ""


def is_agent(node):
    s = lane_text(node)
    return s == "Agent" or bool(re.match(r"^Agent\s*:\s*\S", s))


def is_person(node):
    return not is_agent(node) and lane_text(node) != "System"


def graph_parts(g):
    nodes = {n["id"]: n for n in g.get("nodes", [])}
    adj, preds = {}, {}
    for e in g.get("edges", []):
        adj.setdefault(e["source"], []).append(e["target"])
        preds.setdefault(e["target"], []).append(e["source"])
    starts = g.get("starts") or [i for i, n in nodes.items() if n.get("type") == "start_event"]
    ends = g.get("outcomes") or [i for i, n in nodes.items() if n.get("type") == "end_event"]
    return nodes, adj, preds, list(starts), list(ends)


def reachable(adj, starts, blocked):
    seen = {s for s in starts if s not in blocked}
    queue = deque(seen)
    while queue:
        x = queue.popleft()
        for y in adj.get(x, []):
            if y not in seen and y not in blocked:
                seen.add(y)
                queue.append(y)
    return seen


def path_to(adj, starts, blocked, target):
    prev = {s: None for s in starts if s not in blocked}
    queue = deque(prev)
    while queue:
        x = queue.popleft()
        if x == target:
            out = []
            while x is not None:
                out.append(x)
                x = prev[x]
            return out[::-1]
        for y in adj.get(x, []):
            if y not in prev and y not in blocked:
                prev[y] = x
                queue.append(y)
    return []


def load_coverage(root, future):
    found = sorted(glob.glob(os.path.join(root, "stages/future-state/future-state-coverage-manifest*")))
    for path in found:
        if path.endswith(".json"):
            data = json.load(open(path, encoding="utf-8"))
            return data.get("coverage", []) if isinstance(data, dict) else data
    return future.get("coverage") or []


def check_ids_and_moves(fail, fnodes, fgraph, moves, outcomes, grants, no_reshape, coverage):
    grant_ids = set(grants)
    for row in moves:
        state = row.get("State", "")
        if state.lower().startswith("not adopted"):
            continue
        for sid in cell_ids(row.get("Future steps")):
            if sid not in fnodes:
                fail("(a) id resolution", f"{row.get('Move')} names future step {sid}, not a future-state node")
    for row in outcomes:
        end = (row.get("Future ending") or "").replace("`", "").strip()
        if end.lower() != "retired":
            for sid in cell_ids(end):
                if sid not in fnodes:
                    fail("(a) id resolution", f"Outcomes future ending {sid} is not a future-state node")
    for item in fgraph.get("nodes", []) + fgraph.get("edges", []):
        for wg in sorted(set(re.findall(r"WG-\d+", json.dumps(item)))):
            if wg not in grant_ids:
                fail("(a) id resolution", f"{wg} named on {item.get('id')} is not a Write grants row")

    landed_any = False
    for row in moves:
        move, kind = row.get("Move", "").strip("` "), row.get("Kind", "").strip("` ").lower()
        future_ids = cell_ids(row.get("Future steps"))
        current_ids = cell_ids(row.get("Current steps"))
        # an adopted move other than remove must name at least one future step; remove is checked below
        landed = all(s in fnodes for s in future_ids) and (bool(future_ids) or kind == "remove")
        if landed and kind == "remove":
            cites = re.compile(r"\b" + re.escape(move) + r"\b")
            landed = not any(s in fnodes for s in current_ids) and any(
                r.get("mapping") == "summary" and cites.search(json.dumps(r)) for r in coverage)
        if landed and kind == "merge":
            kept = [s for s in current_ids if s in fnodes]
            landed = len(kept) <= 1 and all(s in future_ids for s in kept)
        landed_any = landed_any or landed
        state = row.get("State", "").strip()
        if not landed and not re.match(r"not adopted:\s*\S", state, re.I):
            why = "names no future step" if not future_ids and kind != "remove" else "has not landed"
            fail("(b) moves land", f"{move} ({kind}) {why} and is not marked not adopted: <reason>")
    covered = {b.split(":", 1)[0].strip().lower() for b in no_reshape if ":" in b and b.split(":", 1)[1].strip()}
    if not landed_any and not set(MOVE_KINDS) <= covered:
        missing = ", ".join(k for k in MOVE_KINDS if k not in covered)
        fail("(b) moves land", f"no move landed and the No reshape lines miss: {missing}")


def check_outcomes(fail, cgraph, fnodes, fadj, fstarts, outcomes):
    _, _, _, _, cends = graph_parts(cgraph)
    named_current = {sid for row in outcomes for sid in cell_ids(row.get("Current ending"))}
    for end in cends:
        if end not in named_current:
            fail("(c) outcome guard", f"current ending {end} has no Outcomes row")
    from_start = reachable(fadj, fstarts, set())
    for row in outcomes:
        end = (row.get("Future ending") or "").replace("`", "").strip()
        if end.lower() == "retired":
            continue
        for sid in cell_ids(end):
            if sid not in fnodes:
                fail("(c) outcome guard", f"future ending {sid} does not exist")
            elif sid not in from_start:
                fail("(c) outcome guard", f"future ending {sid} cannot be reached from a start")


def is_read_edge(e):
    """An edge into a system of record is a read only when its kind is `read_access`, or when its kind is `system` or
    absent and its label, with grant ids (`WG-<n>`) and surrounding spaces removed, is exactly the word `read`
    (case-insensitive). Every other edge into a system of record is a write and is path-checked; the edge kind, not
    the label wording, decides."""
    kind = e.get("kind")
    if kind == "read_access":
        return True
    if kind and kind != "system":
        return False
    label = re.sub(r"\bWG-\d+\b", " ", e.get("label") or "", flags=re.I)
    return label.strip().lower() == "read"


FS_PLACE = "future-state"   # the process place the one future-state graph takes in the shared Resolver


def write_steps(fgraph, fnodes, grants):
    """Agent-lane nodes writing into a system of record (rule (d) selection)."""
    if hasattr(grants, "rows"):
        members = sorted({n.get("process_id") for n in fnodes.values()
                          if isinstance(n.get("process_id"), str) and n["process_id"] != "set"}) or [FS_PLACE]
        grants = _write_grants.step_rows(grants.rows, [(pid, {"graph": fgraph}) for pid in members])
    # the shared structural reading of the cells (case, punctuation and articles aside), one rule for every check
    def in_scope(rows):
        return {g for g, r in rows.items() if _write_grants.is_agent_writer(r.get("Who writes", ""))
                and "mechanical" in _write_grants.words(r.get("Write kind", ""))
                and _write_grants.words(r.get("System of record", ""))[:1] in (["yes"], ["unclear"])}
    records = {i for i, n in fnodes.items() if n.get("surface_kind") == "system_of_record"}
    out, exempt = [], set()
    for i, n in fnodes.items():
        if not is_agent(n) or n.get("type") in ("start_event", "end_event"):
            continue
        # the shared reading for this step, through its lineage (`future.from`), as every other check reads it
        rows = grants.for_step("%s:%s" % (n.get("process_id") or FS_PLACE, i)) if hasattr(grants, "for_step") else grants
        in_scope_grants = in_scope(rows)
        # read edges stay out of grant extraction, so a read naming a grant never makes the step a write
        record_writes = [e for e in fgraph.get("edges", [])
                         if e["source"] == i and e["target"] in records and not is_read_edge(e)]
        text = json.dumps({k: n.get(k) for k in ("title", "detail", "facts", "write_grant", "write_grants")})
        text += " ".join(e.get("label") or "" for e in record_writes)
        named = set(re.findall(r"WG-\d+", text))
        # any write edge into a system of record selects the step, whatever grant it names or does not name
        if named & in_scope_grants or record_writes or (
                not named and n.get("type") in ("agent_write", "write_interaction")
                and set(n.get("systems") or []) & records):
            out.append(i)
            # a named Exception reason waives the confirmation only when every grant the step names carries one
            if named and all(((rows.get(g) or {}).get("Exception reason") or "").strip("` ") in EXCEPTION_REASONS
                             for g in named):
                exempt.add(i)
    return out, exempt


def own_confirmations(write, fnodes, preds):
    """The person-confirmation rule lives in one copy, `_write_grants.own_confirmations`."""
    return _write_grants.own_confirmations(write, fnodes, preds, is_agent, is_person)


def keeps_confirmation(w, fnodes, grants, splits):
    """True when the map keeps a person confirmation step for agent write `w` (0503 R-writes): `## Splits` names `w`,
    one of its `future.from` steps or a grant it carries; or a grant whose Steps name `w` (or a step in its
    `future.from`), or a grant `w` names, also names a person step (the split partner: the person confirms, the
    agent enters). Otherwise the agent writes with no confirmation (values copied unchanged from a named source)."""
    n = fnodes[w]
    fut = n.get("future") if isinstance(n.get("future"), dict) else {}
    origin = {w} | {str(x) for x in (fut.get("from") or []) if isinstance(fut.get("from"), list)}
    named = set(re.findall(r"WG-\d+", json.dumps({k: n.get(k) for k in ("title", "detail", "facts", "write_grant",
                                                                            "write_grants")})))
    if (origin | named) & splits:
        return True
    person_refs = set()
    for i, m in fnodes.items():
        if is_person(m) and m.get("type") not in ("start_event", "end_event", "system_surface") \
                and m.get("surface_kind") != "system_of_record":
            mf = m.get("future") if isinstance(m.get("future"), dict) else {}
            person_refs |= {i} | {str(x) for x in (mf.get("from") or []) if isinstance(mf.get("from"), list)}
    rows = getattr(grants, "rows", None) or list(grants.values())
    for r in rows:
        steps = set(cell_ids(r.get("Steps")))
        gid = (r.get("Grant") or "").strip("` ")
        # a Steps id the write and a person node share (both drawn from one current step) is the split itself
        if (gid in named or steps & origin) and steps & person_refs:
            return True
        if (gid in named or steps & origin) and gid in splits:
            return True
    return False


def check_paths(fail, warn, fgraph, fnodes, fadj, preds, fstarts, fends, grants, splits=frozenset()):
    selected, exempt = write_steps(fgraph, fnodes, grants)
    for w in selected:
        if w in exempt:
            continue
        # a confirmation the future state draws before the write is path-checked however the map carries the split
        # (two grants, Steps or `## Splits`); a write with neither needs no confirmation (0503 rows 12 and 17)
        confirms = own_confirmations(w, fnodes, preds)
        if not confirms and not keeps_confirmation(w, fnodes, grants, splits):
            continue
        if not confirms:
            fail("(d) path check, write", f"agent write {w} has no person confirmation feeding it")
        elif w in reachable(fadj, fstarts, confirms):
            route = " > ".join(path_to(fadj, fstarts, confirms, w))
            fail("(d) path check, write", f"agent write {w} is reachable avoiding {', '.join(sorted(confirms))}: {route}")
        if confirms and w in reachable(fadj, fadj.get(w, []), confirms):
            warn("loop re-entry", f"agent write {w} sits on a loop that re-enters it without passing "
                                  f"{', '.join(sorted(confirms))}")

    endings, from_start = set(fends), reachable(fadj, fstarts, set())
    kind_of = {(e["source"], e["target"]): e.get("kind") for e in fgraph.get("edges", [])}
    skips = {}
    for x, n in fnodes.items():
        if not is_person(n) or n.get("type") not in PROTECTED_TYPES:
            continue
        ancestors, queue = {x}, deque([x])
        while queue:
            y = queue.popleft()
            for p in preds.get(y, []):
                if p not in ancestors:
                    ancestors.add(p)
                    queue.append(p)
        x_endings = reachable(fadj, [x], set()) & endings
        for u in ancestors - {x}:
            if u not in from_start or fnodes.get(u, {}).get("type") == "decision":
                continue
            for v in fadj.get(u, []):
                if v in ancestors or v not in fnodes or fnodes[v].get("type") == "system_surface":
                    continue
                if reachable(fadj, [v], {x}) & x_endings:
                    toward = [kind_of.get((u, t)) for t in fadj.get(u, []) if t in ancestors]
                    if kind_of.get((u, v)) == "sequence" and toward and "sequence" not in toward:
                        continue  # x is an exception handler off the normal sequence, not a skipped step
                    skips.setdefault((u, v), []).append(x)
    for (u, v), skipped in sorted(skips.items()):
        kind = kind_of.get((u, v))
        detail = f"{u} ({fnodes[u].get('type')}) -{kind}-> {v} skips {', '.join(sorted(skipped))}"
        if kind in ("exception", "escalation"):
            fail("(e) path check, bypass", detail)
        else:
            warn("skip", detail + " (routing, parallel work or a real skip: for the coherence agent)")


def graph_shape_problems(doc):
    """Why a parsed current- or future-state document is not a graph the rules can read; empty when it is.
    The graph must be an object holding node and edge lists; every node an object with a unique text id; every edge
    an object with text source and target; starts and outcomes, when present, lists of text ids."""
    g = doc.get("graph") if isinstance(doc, dict) else None
    if not isinstance(g, dict):
        return ["graph is not an object with node and edge lists"]
    problems = [f"graph has no {k} list" for k in ("nodes", "edges") if not isinstance(g.get(k), list)]
    if problems:
        return problems
    seen = set()
    for i, n in enumerate(g["nodes"]):
        nid = n.get("id") if isinstance(n, dict) else None
        if not isinstance(nid, str) or not nid.strip():
            problems.append(f"node {i} is not an object with a text id")
        elif nid in seen:
            problems.append(f"node id {nid} appears more than once")
        else:
            seen.add(nid)
    for i, e in enumerate(g["edges"]):
        if not (isinstance(e, dict) and all(isinstance(e.get(k), str) and e.get(k) for k in ("source", "target"))):
            problems.append(f"edge {i} is not an object with text source and target")
    for k in ("starts", "outcomes"):
        v = g.get(k)
        if v is not None and not (isinstance(v, list) and all(isinstance(x, str) for x in v)):
            problems.append(f"graph {k} is not a list of text ids")
    return problems


def check_control_steps(fail, fnodes, text):
    """(f) control step (0503 R-people safeguard): an agent-lane node whose `future.from` names a step the map lists
    in `## Why people stay` with Reason `control`. The built agent would take the approval, sign-off or submission
    itself, an action outside its authority in a live system."""
    rows, _notes = _write_grants.why_people_stay(text)
    control = _write_grants.control_steps(rows)
    for i, n in fnodes.items():
        fut = n.get("future") if isinstance(n.get("future"), dict) else {}
        src = fut.get("from") if isinstance(fut.get("from"), list) else []
        hit = sorted(str(x) for x in src if str(x) in control)
        if is_agent(n) and hit:
            fail("(f) control step", f"agent node {i} comes from control step(s) {', '.join(hit)} (Why people stay); "
                                     "the agent would take an approval, sign-off or submission outside its authority")


def run_rules(fail, warn, text, grants, root, future, cgraph, fgraph):
    """The reshape rules on parsed, well-shaped current and future graphs."""
    fnodes, fadj, preds, fstarts, fends = graph_parts(fgraph)
    moves = section_table(text, "Reshape moves") or []
    outcomes = section_table(text, "Outcomes") or []
    check_ids_and_moves(fail, fnodes, fgraph, moves, outcomes, grants, section_bullets(text, "No reshape"),
                        load_coverage(root, future))
    check_outcomes(fail, cgraph, fnodes, fadj, fstarts, outcomes)
    check_paths(fail, warn, fgraph, fnodes, fadj, preds, fstarts, fends, grants, _write_grants.split_refs(text))
    check_control_steps(fail, fnodes, text)


def main(argv):
    if len(argv) != 2:
        print("usage: check-future-state-reshape.py <run_root>", file=sys.stderr)
        return 2
    if not os.path.exists(argv[1]):
        # a path that is not there is a recorded input finding; exit 2 is kept for input that will not parse
        print(f"FAIL inputs: no such path: {argv[1]}; no reshape rule runs")
        return 1
    root = find_root(argv[1])
    if root is None:
        print("PASS (no stages/ folder: reshape checks skipped)")
        return 0
    stages = os.path.join(root, "stages")
    map_path = os.path.join(stages, "transformation-plan/transformation-map.md")
    if not os.path.isfile(map_path):
        print("PASS (no transformation map: reshape checks skipped)")
        return 0
    fails, warnings, notes = [], [], []
    parse_error = False
    fail = lambda rule, detail: fails.append({"rule": rule, "detail": detail})
    warn = lambda kind, detail: warnings.append({"kind": kind, "detail": detail})

    text = open(map_path, encoding="utf-8").read()
    for table, required in REQUIRED_COLUMNS.items():
        header = section_header(text, table)
        absent = [c for c in required if header is not None and c not in header]
        if absent:
            fail("table columns", f"{table} table is missing {', '.join(absent)}")

    # the shared reading of the Write grants rows (first row per grant; a step reads the row whose Steps names it)
    grants = _write_grants.step_rows(section_table(text, "Write grants") or [])
    for note in grants.res.notes:
        notes.append(dict(note, grant=note.get("where")))
    for why_note in _write_grants.why_people_stay(text)[1]:
        notes.append({"grant": "Why people stay", "detail": why_note})
    for g, r in grants.items():
        rung = (r.get("Next rung") or "").lower()
        if _write_grants.grant_kind(r) == "person_write" and "enter" in rung and "confirmed" in rung:
            notes.append({"grant": g, "detail": "person row whose Next rung restates the default "
                                                "(the agent enters confirmed values): judge whether it should be agent"})

    paths = {k: os.path.join(stages, k, k + ".json") for k in ("current-state", "future-state")}
    missing = [p for p in paths.values() if not os.path.isfile(p)]
    if fails:
        pass  # an unrecognised table schema would select nothing: no rule runs on it
    elif missing:
        for p in missing:
            fail("inputs", f"{os.path.relpath(p, root)} is missing")
    else:
        docs, parsed = {}, set()
        for key, path in paths.items():
            try:
                with open(path, encoding="utf-8") as fh:
                    docs[key] = json.load(fh)
                parsed.add(key)
            except ValueError as e:
                print(f"error: {os.path.relpath(path, root)} will not parse: {e}", file=sys.stderr)
                parse_error = True
                docs[key] = None
                fail("inputs", f"{os.path.relpath(path, root)} does not parse; no reshape rule runs on it")
            except OSError as e:
                docs[key] = None
                fail("inputs", f"{os.path.relpath(path, root)} cannot be read: {e.__class__.__name__}")
        shaped = {}
        for key, doc in docs.items():
            problems = graph_shape_problems(doc) if (doc is not None or key in parsed) else ["cannot be read"]
            for problem in problems if key in parsed else []:
                # JSON null, {} or a node without an id parses but is no graph: a recorded input finding, never a pass
                fail("inputs", f"{key}.json: {problem}; no rule runs on it")
            shaped[key] = doc["graph"] if not problems else None
        if shaped.get("current-state") is not None and shaped.get("future-state") is not None:
            run_rules(fail, warn, text, grants, root, docs["future-state"], shaped["current-state"],
                      shaped["future-state"])

    report_dir = os.path.join(stages, "future-state")
    os.makedirs(report_dir, exist_ok=True)
    with open(os.path.join(report_dir, "reshape-check.json"), "w", encoding="utf-8") as fh:
        json.dump({"fails": fails, "warnings": warnings, "notes": notes}, fh, indent=1)
        fh.write("\n")
    for f in fails:
        print(f"FAIL {f['rule']}: {f['detail']}")
    for w in warnings:
        print(f"WARN {w['kind']}: {w['detail']}")
    for n in notes:
        print(f"NOTE {n['grant']}: {n['detail']}")
    if parse_error:
        return 2
    if fails:
        return 1
    print(f"PASS future-state reshape ({len(warnings)} warning(s), {len(notes)} note(s))")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
