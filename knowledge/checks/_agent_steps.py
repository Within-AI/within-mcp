#!/usr/bin/env python3
"""Which future-state steps the agent owns: the one rule every check asks.

Agent Design's architecture, rules and placement checks and Future State Design's run hygiene all call
`agent_steps(graph, ...)`; none of them decides ownership on its own. Standard library only; Python 3.9.

The rule, read from structural fields only (lanes, tags, process ids, spans), never from words:

- A step is the agent's when it sits on the Agent lane (`lane` "Agent" or "Agent: <name>", or a `lane_id`
  whose lane is the Agent lane or the `ai_and_agents` band) or carries a nonblank `agent` tag.
  Events, object states, context neighbours and system surfaces on the Agent lane are not steps; a tagged node
  counts whatever its type.
- A node with an `ai_use` (or of type `ai_use`) whose `ai_use.mode` is not "agent" is a tool a person uses today:
  never an agent step.
- A person step (a people or role lane, no agent tag) is never owned. A node on a people lane that also carries
  an agent tag is ambiguous: the tag, the more specific signal, decides; it is owned as the agent's step and the
  reading is recorded in `notes` (never failing).
- A step is owned once in its own process and once in every process of its `future.spans`, as
  `<process>:<node>`. An unknown process in spans is a finding and that entry is ignored. Spans given with no
  `process_id` is a finding; the spans are used.
- In a set (more than one process, one shared graph), a step with `process_id` "set" is set-level: it is owned as
  `set:<node>` (plus one id per spanned process). When the caller passes the architecture's declared
  `set_steps`, a set-level step missing from it is a finding; the step is still owned.
- In a single-process graph (a file the intake gives to exactly one process, also inside a multi-process run) a
  node without a process id belongs to the graph's one process, never the set. A node whose explicit process id
  differs from the graph's one process is a recorded finding (`process-conflict`) and is left unowned and unplaced:
  it is placed under neither process and its spans are not applied, so it never stands in for another graph's step.
  Graph membership (the processes the file belongs to) is separate from the run's processes (allowed in spans).
- Node identity is scoped to its process: `n1` in one process and `n1` in another are different steps. A claim on
  one ref expands only to that step's own refs (its process plus its spans).

Findings are dicts {"code", "where", "detail"}; nothing here raises on graph content.
"""

import json

SET = "set"
NON_STEP_TYPES = ("start_event", "end_event", "object_state", "context_neighbour", "system_surface")
AGENT_LANE = "Agent"
AGENT_LANE_PREFIX = "Agent: "
AGENT_BAND = "ai_and_agents"
PEOPLE_BAND = "people"
PEOPLE_LANES = ("Analyst",)


def _text(value):
    return isinstance(value, str) and value.strip() != ""


def is_agent_lane_name(lane):
    """True for the Agent lane name: "Agent" or "Agent: <name>"."""
    if lane == AGENT_LANE:
        return True
    return isinstance(lane, str) and lane.startswith(AGENT_LANE_PREFIX) and bool(lane[len(AGENT_LANE_PREFIX):].strip())


def _lane_index(graph):
    """lane_id -> lane object, for an A3 graph that carries `lanes[]`."""
    out = {}
    for ln in graph.get("lanes") or [] if isinstance(graph, dict) else []:
        if isinstance(ln, dict) and isinstance(ln.get("id"), str):
            out[ln["id"]] = ln
    return out


def lane_of(node, lanes):
    """("agent" | "people" | "other", lane label) for one node."""
    ln = lanes.get(node.get("lane_id")) if isinstance(node.get("lane_id"), str) else None
    if ln is not None:
        if ln.get("band") == AGENT_BAND or is_agent_lane_name(ln.get("lane")):
            return "agent", ln.get("id")
        if ln.get("band") == PEOPLE_BAND or ln.get("lane") in PEOPLE_LANES:
            return "people", ln.get("id")
        return "other", ln.get("id")
    lane = node.get("lane")
    if is_agent_lane_name(lane):
        return "agent", lane
    if lane in PEOPLE_LANES:
        return "people", lane
    return "other", lane


def is_tool_use(node):
    """True when the node is a tool a person uses today (an ai_use whose mode is not "agent")."""
    use = node.get("ai_use")
    agent_mode = isinstance(use, dict) and use.get("mode") == "agent"
    if node.get("type") == "ai_use":
        return not agent_mode
    if use is None or use == "" or use == [] or use == {}:
        return False
    return not agent_mode


def classify(node, lanes):
    """("agent" | "person" | "ambiguous" | "none", reason) for one node."""
    kind, _ = lane_of(node, lanes)
    tagged = _text(node.get("agent"))
    if is_tool_use(node):
        return ("person" if kind == "people" else "none"), "ai_use tool"
    if tagged and kind == "people":
        return "ambiguous", "on a people lane and tagged agent"
    if tagged:
        return "agent", "agent tag"
    if kind == "agent":
        if node.get("type") in NON_STEP_TYPES:
            return "none", "not a step"
        return "agent", "Agent lane"
    if kind == "people":
        return "person", "people lane"
    return "none", "not an agent step"


def accepted_role(node, lanes):
    """Shared role reading after resolving a people-lane/agent-tag ambiguity."""
    kind, reason = classify(node, lanes)
    return ("agent", "agent tag on a people lane") if kind == "ambiguous" else (kind, reason)


class Ownership(object):
    """The result of `agent_steps`.

    owned       qualified ref -> {"node": node id, "agent": tag or "", "home": the node's first ref,
                "refs": every ref of that one step}
    by_node     node id -> [qualified refs], home first, for every agent step
    places      node id -> set of process ids (or "set") the node belongs to, for every node
    person      node ids of person steps
    person_refs qualified refs of person steps
    findings    [{"code", "where", "detail"}]
    """

    def __init__(self):
        self.owned, self.by_node, self.places, self.person, self.findings = {}, {}, {}, set(), []
        self.notes = []  # readings taken where input was ambiguous but readable (never failing)
        self.nodes, self.person_refs = {}, set()
        self.step_nodes = {}  # qualified ref -> node; one graph's bare ids never meet another's here
        self.node_candidates = {}
        self.node_list = []  # every node of every graph once, never merged by bare id

    def node_at(self, ref):
        """The node a qualified ref (`<process>:<node>` or `set:<node>`) names, or None."""
        return self.step_nodes.get(ref)

    def steps(self):
        """Every owned step once: [(home ref, [its refs])], keyed by qualified identity, never the bare id."""
        return [(ref, list(info["refs"])) for ref, info in self.owned.items() if ref == info["home"]]

    def add(self, code, where, detail):
        self.findings.append({"code": code, "where": where, "detail": detail})

    def ref_for(self, process_id, node_id):
        """The node's owned ref valid in process_id (its own `<process>:<node>`, else `set:<node>`), or None."""
        for ref in ("%s:%s" % (process_id, node_id), "%s:%s" % (SET, node_id)):
            if ref in self.owned:
                return ref
        return None

    def is_place(self, process_id, node_id):
        """True when `<process_id>:<node_id>` names a node of the future state in that process (or the set)."""
        return process_id in self.places.get(node_id, ())

    def is_person_ref(self, ref):
        return ref in self.person_refs


def agent_steps(graph, process_ids=None, default_process=None, set_steps=None, graph_processes=None):
    """Return an Ownership for one future-state graph.

    graph            the graph object (`nodes`, optional `lanes`), or a bundle carrying `graph`
    process_ids      every process id of the run (a list); more than one makes the graph a set
    default_process  the process a node without `process_id` belongs to; required for a single-process graph,
                     None for a set's shared graph (such nodes are set-level)
    set_steps        the architecture's declared `set_steps` (a list of `set:<node>`), or None to skip that check
    graph_processes  the processes this graph belongs to (default: process_ids); exactly one makes it a
                     single-process graph whose explicit other process ids are conflicts
    """
    res = Ownership()
    if isinstance(graph, dict) and isinstance(graph.get("graph"), dict):
        graph = graph["graph"]
    if not isinstance(graph, dict):
        return res
    pids = [p for p in (process_ids or []) if _text(p)]
    known = set(pids)
    members = set(p for p in (pids if graph_processes is None else graph_processes) if _text(p))
    is_set = len(members) > 1
    if not is_set and default_process is None and len(members) == 1:
        default_process = sorted(members)[0]
    declared = None if set_steps is None else set(s for s in set_steps if isinstance(s, str))
    lanes = _lane_index(graph)
    for node in graph.get("nodes") or []:
        if not isinstance(node, dict) or not _text(node.get("id")):
            continue
        nid = node["id"]
        res.nodes.setdefault(nid, node)
        res.node_list.append(node)
        where = "node %s" % nid
        own = node.get("process_id")
        future = node.get("future") if isinstance(node.get("future"), dict) else {}
        spans = future.get("spans") if isinstance(future.get("spans"), list) else []
        places = []
        # a conflict needs a graph known to belong to one process; with no process list the default is only a
        # label and the node's own explicit process id is used as written
        if _text(own) and own != SET and len(members) == 1 and default_process is not None and own != default_process:
            res.add("process-conflict", where, "process_id %r differs from the graph's one process %r (the intake "
                                               "lists this file as %r's); the node is left unowned and unplaced, "
                                               "never filled with either process" % (own, default_process,
                                                                                     default_process))
            res.places[nid] = set()
            continue
        elif _text(own) and own != SET:
            if known and own not in known:
                res.add("unknown-process", where, "process_id %r is not a process of the run" % own)
            places.append(own)
        elif own == SET or own is None:
            if own == SET or default_process is None:
                if own is None and spans:
                    res.add("spans-without-process", where,
                            "the node lists future.spans but no process_id; it is set-level and the spans are used")
                places.append(SET)
            else:
                places.append(default_process)
        for sp in spans:
            if not _text(sp) or (known and sp not in known):
                res.add("unknown-span", where, "future.spans names %r, not a process of the run; ignored" % (sp,))
                continue
            if sp not in places:
                places.append(sp)
        if not places and default_process is not None:
            places.append(default_process)
        home_ref = "%s:%s" % (places[0], nid) if places else None
        if home_ref in res.step_nodes:
            first = res.step_nodes[home_ref]
            if first != node:
                res.node_candidates.setdefault(home_ref, [first]).append(node)
                res.notes.append({"code": "node-reading", "where": home_ref, "note": True,
                                  "detail": "the graph draws this step twice; the first drawing is read",
                                  "candidates": res.node_candidates[home_ref]})
            continue
        res.places[nid] = set(places)
        for p in places:
            res.step_nodes["%s:%s" % (p, nid)] = node
        kind, reason = classify(node, lanes)
        if kind == "person":
            res.person.add(nid)
            res.person_refs.update("%s:%s" % (p, nid) for p in places)
            continue
        if kind == "ambiguous":
            # the agent tag is the more specific signal: the step is read as the agent's, and the reading noted
            res.notes.append({"code": "ambiguous-owner", "where": where, "note": True,
                              "detail": "%s: read as the agent's step (its agent tag names it)" % reason})
            kind, reason = accepted_role(node, lanes)
        if kind != "agent":
            continue
        refs = ["%s:%s" % (p, nid) for p in places]
        if not refs:
            continue
        tag = node.get("agent").strip() if _text(node.get("agent")) else ""
        res.by_node[nid] = refs
        for ref in refs:
            res.owned[ref] = {"node": nid, "agent": tag, "home": refs[0], "reason": reason, "refs": list(refs)}
        if SET in places and declared is not None and "%s:%s" % (SET, nid) not in declared:
            res.add("set-step-undeclared", "%s:%s" % (SET, nid),
                    "set-level agent step is not listed in the architecture's set_steps; it is still owned")
    return res


def shared_groups(processes):
    """Group a run's processes by future-state file.

    processes is [(process_id, file key, graph)]; returns [(graph, [process ids], default_process)]. Processes
    that share one file form one set graph (default None); a process with its own file is that file's default.
    """
    order, groups = [], {}
    for pid, key, graph in processes:
        if key not in groups:
            groups[key] = (graph, [])
            order.append(key)
        groups[key][1].append(pid)
    out = []
    for key in order:
        graph, pids = groups[key]
        out.append((graph, pids, pids[0] if len(pids) == 1 else None))
    return out


def graph_key(doc):
    """Fallback identity for in-memory readers. File-backed callers should keep their file keys."""
    return json.dumps(doc, sort_keys=True, default=str)


def run_ownership(processes, set_steps=None):
    """Read graph membership once; choose a local drawing over an incoming span on collisions.

    Other collisions use stable source/contents order. Every choice is recorded, never a new stop.
    The selected node, person mark and expanded ownership refs always describe the same drawing.
    """
    all_pids = [p for p, _, _ in processes]
    merged = Ownership()
    candidates = {}
    within_graph = {}
    for graph, pids, default in shared_groups(processes):
        one = agent_steps(graph, all_pids, default_process=default, set_steps=set_steps, graph_processes=pids)
        merged.findings.extend(one.findings)
        merged.notes.extend(one.notes)
        within_graph.update(one.node_candidates)
        merged.node_list.extend(one.node_list)
        for ref, node in one.step_nodes.items():
            info = one.owned.get(ref)
            own = node.get("process_id")
            home = "%s:%s" % (own if own else default if default is not None else SET, node["id"])
            candidates.setdefault(ref, []).append((home, node, info, ref in one.person_refs))
    chosen = {}
    merged.node_candidates = dict(within_graph)
    for ref, options in sorted(candidates.items()):
        options.sort(key=lambda x: (x[0] != ref, str(x[0]), graph_key(x[1])))
        home, node, info, person = chosen[ref] = options[0]
        unique = []
        for _, other, _, _ in options:
            if other not in unique:
                unique.append(other)
        if len(unique) > 1:
            merged.node_candidates[ref] = unique
            merged.notes.append({"code": "node-reading", "where": ref, "note": True,
                                 "detail": "different steps name this reference; read the local drawing before "
                                           "incoming spans, then stable home and content order",
                                 "candidates": unique})
        merged.step_nodes[ref] = node
        merged.nodes.setdefault(node["id"], node)
        merged.places.setdefault(node["id"], set()).add(ref.split(":", 1)[0])
        if person:
            merged.person.add(node["id"])
            merged.person_refs.add(ref)
    for ref, (home, node, info, person) in chosen.items():
        if info is None:
            continue
        refs = [r for r in info["refs"] if r in chosen and chosen[r][0] == home and chosen[r][1] == node]
        if not refs:
            continue
        actual_home = home if home in refs else refs[0]
        merged.owned[ref] = dict(info, home=actual_home, refs=refs)
        merged.by_node.setdefault(node["id"], [])
        merged.by_node[node["id"]].extend(r for r in refs if r not in merged.by_node[node["id"]])
    return merged


def expand_claims(ownership, parts):
    """Each part's owned refs, with every claim on a spanning step expanded to all its refs.

    parts is [(part id, [refs])]. Returns (claims, notes): claims maps owned ref -> [part ids] (each part once);
    notes are findings: `mirrored-claim` (a part listed one step under more than one process, accepted) and
    `person-step-owned` (a part claims a person step)."""
    claims, notes = {}, []
    for part_id, refs in parts:
        seen_nodes = {}
        for ref in refs or []:
            if not isinstance(ref, str):
                continue
            info = ownership.owned.get(ref)
            if info is None:
                if ownership.is_person_ref(ref):
                    notes.append({"code": "person-step-owned", "where": ref,
                                  "detail": "part %s lists a person step in owns_steps; a person step is never "
                                            "owned" % part_id})
                continue
            step = info["home"]  # one step's identity: its own first ref, never the bare node id
            if step in seen_nodes and seen_nodes[step] != ref:
                notes.append({"code": "mirrored-claim", "where": ref,
                              "detail": "part %s lists step %s more than once (also %s); one listing covers every "
                                        "process it spans" % (part_id, info["node"], seen_nodes[step])})
                continue
            seen_nodes[step] = ref
            for r in info.get("refs") or [ref]:
                lst = claims.setdefault(r, [])
                if part_id not in lst:
                    lst.append(part_id)
    return claims, notes
