#!/usr/bin/env python3
"""Agent architecture check: an id lookup proving the cooperating agents carry out the future state.

Usage: check-agent-architecture.py RUN_ROOT
  RUN_ROOT is the run folder holding `stages/`. When the argument is a file, the nearest ancestor folder
  that contains a `stages/` folder is used.

Inputs (all under RUN_ROOT/stages/):
  * future-state/future-state.json            the Future state Workflow (`graph.nodes` / `graph.edges`,
                                              or `nodes` / `edges` at the top level);
  * transformation-plan/transformation-map.md the map; only its `Write grants` table is read (the `WG-<n>`
                                              ids in the Grant column);
  * agent-build/internal-dag.graph.json       the internal map: the `kind: agent` entries of `sub_agents`
                                              with `lane` (`Agent` or `Agent: <name>`), `write_grants`
                                              (list of `WG-<n>`) and `future_state_steps` (list of
                                              future-state node ids); `interfaces` with
                                              `direction: dispatch`, `seated_in` and `target`.

An agent lane is a future-state lane spelled `Agent` or `Agent: <name>`. An agent step is a node in an agent
lane whose type is not a start or end event. A sub-agent belongs to the lane its `lane` field names.

It FAILS (exit 1, one `FAIL <rule>: <detail>` line per problem) on these rules:
  * lane:        an agent lane of the future state names no sub-agent (no `kind: agent` sub-agent has that
                 `lane`);
  * steps:       an agent step id is not in the `future_state_steps` of any sub-agent of its lane;
  * grant-table: a sub-agent's `write_grants` entry is not a `WG-<n>` row of the map's Write grants table;
  * step-grant:  a `WG-<n>` named on a future-state step a sub-agent carries (its structured write_grant or
                 write_grants fields, or a `facts` entry labelled "Write grant" / "Write grants"; grant ids
                 in the step's title, detail or other facts are not read, since that prose may name a grant
                 a person holds), or on that step's edges (their label, write_grant, write_grants or
                 "Write grant" facts; every outgoing edge, and every incoming edge whose source is not an
                 agent step), is not in that sub-agent's `write_grants` (a single string counts as one grant);
  * legacy:      no `kind: agent` sub-agent carries any of `lane`, `write_grants`, `future_state_steps`, yet
                 the future state draws two or more agent lanes or any named `Agent: <name>` lane (on any
                 node, events included);
  * dispatch:    with two or more agent lanes, a future-state edge from an agent step in one agent lane to
                 an agent step in another has no internal-map interface with `direction: dispatch` whose
                 `seated_in` is a sub-agent of the source lane and whose `target` is a sub-agent of the
                 destination lane.

It PASSES (exit 0) with:
  * `PASS (inputs absent: architecture check skipped)` when the internal map, the future state or the map
    is absent;
  * a `WARN legacy internal map: no cooperating-agent fields` line, then `PASS ...`, when no `kind: agent`
    sub-agent carries any of `lane`, `write_grants`, `future_state_steps` and the future state draws no
    agent lane or exactly one plain `Agent` lane;
  * `PASS ...` when no rule fails (one plain `Agent` lane with one sub-agent is valid).
Exit: 0 pass, 1 fail, 2 usage or unreadable input.
"""
import json
import os
import re
import sys

WG_RE = re.compile(r"\bWG-\d+\b")
WG_FULL = re.compile(r"^WG-\d+$")
EVENT_TYPES = {"start_event", "end_event", "start", "end"}
# Grants come from structured fields and facts labelled "Write grant(s)"; step prose ("The person submits it (WG-11)") is not read.
# An edge label names its own action.
STEP_FIELDS = ("write_grant", "write_grants")
EDGE_FIELDS = ("label", "write_grant", "write_grants")
GRANT_FACT_LABELS = ("write grant", "write grants")
COOP_FIELDS = ("lane", "write_grants", "future_state_steps")


def usage(msg):
    sys.stderr.write(msg + "\nUsage: check-agent-architecture.py RUN_ROOT\n")
    sys.exit(2)


def run_root_of(arg):
    path = os.path.abspath(arg)
    if not os.path.exists(path):
        usage("no such path: %s" % arg)
    cur = path if os.path.isdir(path) else os.path.dirname(path)
    while True:
        if os.path.isdir(os.path.join(cur, "stages")):
            return cur
        parent = os.path.dirname(cur)
        if parent == cur:
            return path if os.path.isdir(path) else os.path.dirname(path)
        cur = parent


def lane_key(lane):
    """Return the normalised agent lane, or None when the lane is not an agent lane."""
    if not isinstance(lane, str):
        return None
    s = " ".join(lane.split())
    if s == "Agent":
        return s
    m = re.match(r"^Agent\s*:\s*(\S.*)$", s)
    return "Agent: " + m.group(1) if m else None


def load_json(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError) as exc:
        usage("cannot read %s: %s" % (path, exc))


def grant_table_ids(map_text):
    """WG-<n> ids in the Grant column of the map's Write grants table."""
    ids, inside = set(), False
    for line in map_text.splitlines():
        s = line.strip()
        if s.startswith("#"):
            inside = "write grants" in s.lower()
            continue
        if inside and s.startswith("|"):
            cells = [c.strip().strip("`") for c in s.strip("|").split("|")]
            if cells and WG_FULL.match(cells[0]):
                ids.add(cells[0])
    return ids


def wg_in(obj, fields):
    found = set()
    for f in fields:
        val = obj.get(f)
        if val is None:
            continue
        text = val if isinstance(val, str) else json.dumps(val)
        found.update(WG_RE.findall(text))
    facts = obj.get("facts")
    for fct in facts if isinstance(facts, list) else []:
        if isinstance(fct, dict) and " ".join(str(fct.get("label", "")).split()).lower() in GRANT_FACT_LABELS:
            text = fct.get("text")
            found.update(WG_RE.findall(text if isinstance(text, str) else json.dumps(text)))
    return found


def sub_grants(sub):
    """A sub-agent's write_grants as a list; a single string is one grant, not a list of characters."""
    val = sub.get("write_grants")
    if isinstance(val, str):
        return [val]
    return list(val) if isinstance(val, list) else []


def check(root):
    stages = os.path.join(root, "stages")
    fs_path = os.path.join(stages, "future-state", "future-state.json")
    map_path = os.path.join(stages, "transformation-plan", "transformation-map.md")
    dag_path = os.path.join(stages, "agent-build", "internal-dag.graph.json")
    if not all(os.path.isfile(p) for p in (fs_path, map_path, dag_path)):
        return ["PASS (inputs absent: architecture check skipped)"], []

    fs = load_json(fs_path)
    graph = fs.get("graph", fs) if isinstance(fs, dict) else {}
    nodes = [n for n in graph.get("nodes") or [] if isinstance(n, dict) and n.get("id")]
    edges = [e for e in graph.get("edges") or [] if isinstance(e, dict)]
    dag = load_json(dag_path)
    dag = dag if isinstance(dag, dict) else {}
    subs = [s for s in dag.get("sub_agents") or [] if isinstance(s, dict) and s.get("kind") == "agent"]
    ifaces = [i for i in dag.get("interfaces") or [] if isinstance(i, dict)]
    with open(map_path, encoding="utf-8") as fh:
        table = grant_table_ids(fh.read())

    by_id = {n["id"]: n for n in nodes}
    node_lane = {n["id"]: lane_key(n.get("lane")) for n in nodes}
    agent_step = {nid for nid, n in by_id.items() if node_lane[nid] and n.get("type") not in EVENT_TYPES}
    # Agent lanes drawn on any node, event nodes included, so a named lane holding only events still counts.
    lanes = sorted({node_lane[nid] for nid in by_id if node_lane[nid]})

    if not any(f in s for s in subs for f in COOP_FIELDS):
        # The legacy pass covers only designs with no agent lane or one plain `Agent` lane.
        if lanes and lanes != ["Agent"]:
            return [], ["FAIL legacy: the future state draws cooperating agents (%s) but the internal map does "
                        "not map them (no kind: agent sub-agent carries lane, write_grants or future_state_steps)"
                        % ", ".join(repr(l) for l in lanes)]
        return ["WARN legacy internal map: no cooperating-agent fields",
                "PASS (legacy internal map: architecture rules not enforced)"], []

    fails = []
    subs_of = {}
    for s in subs:
        key = lane_key(s.get("lane"))
        if key:
            subs_of.setdefault(key, []).append(s)

    for lane in lanes:
        if lane not in subs_of:
            fails.append("FAIL lane: agent lane %r names no sub-agent (no kind: agent sub-agent has this lane)"
                         % lane)

    for nid in sorted(agent_step):
        owners = subs_of.get(node_lane[nid], [])
        if owners and not any(nid in (s.get("future_state_steps") or []) for s in owners):
            fails.append("FAIL steps: agent step %s in lane %r is not in the future_state_steps of %s"
                         % (nid, node_lane[nid], ", ".join(str(s.get("id")) for s in owners)))

    for s in subs:
        for g in sub_grants(s):
            if g not in table:
                fails.append("FAIL grant-table: sub-agent %s grant %s is not a WG-<n> row of the map's "
                             "Write grants table" % (s.get("id"), g))

    for s in subs:
        own = set(sub_grants(s))
        for nid in s.get("future_state_steps") or []:
            if nid not in by_id:
                continue
            named = wg_in(by_id[nid], STEP_FIELDS)
            for e in edges:
                if e.get("source") == nid or (e.get("target") == nid and e.get("source") not in agent_step):
                    named |= wg_in(e, EDGE_FIELDS)
            for g in sorted(named - own):
                fails.append("FAIL step-grant: %s is named on step %s (or its edges), carried by sub-agent %s, "
                             "but is not in its write_grants" % (g, nid, s.get("id")))

    if len(lanes) >= 2:
        dispatches = {(i.get("seated_in"), i.get("target")) for i in ifaces if i.get("direction") == "dispatch"}
        for e in edges:
            src, dst = e.get("source"), e.get("target")
            if src not in agent_step or dst not in agent_step or node_lane[src] == node_lane[dst]:
                continue
            src_ids = [x.get("id") for x in subs_of.get(node_lane[src], [])]
            dst_ids = [x.get("id") for x in subs_of.get(node_lane[dst], [])]
            if not any((a, b) in dispatches for a in src_ids for b in dst_ids):
                fails.append("FAIL dispatch: edge %s -> %s crosses from %r to %r with no internal-map interface "
                             "of direction dispatch seated_in %s with target %s"
                             % (src, dst, node_lane[src], node_lane[dst], "/".join(map(str, src_ids)) or "?",
                                "/".join(map(str, dst_ids)) or "?"))

    if fails:
        return [], fails
    return ["PASS: agent architecture carries out the future state (%d agent lane(s), %d sub-agent(s); every "
            "agent step, grant and cross-agent edge resolves)" % (len(lanes), len(subs))], []


def main(argv):
    if len(argv) != 2:
        usage("expected one argument")
    out, fails = check(run_root_of(argv[1]))
    for line in out + fails:
        print(line)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
