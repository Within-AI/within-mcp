#!/usr/bin/env python3
"""Boundaries check (U11).

A boundary is a directed future-state edge whose endpoints differ in `lane_id`, `agent` tag or
owning part (or in side type, an existing AI tool next to an agent). Sides are typed
`agent:<tag>`, existing tool (`ai_use` node, `<process_id>:<node_id>`), `person:<lane_id>`,
system or external. An `asks_returns` edge gives two boundaries, one each way. Agent writes and
system reads recorded only in node fields (`writes`, `write_grant`, `systems`) give implied
boundaries. An implied write names its destination through the Write grants table (the step's grant's System, or
the system a `writes` entry names), never a grant id or a record name: the written records and grants sit apart in
`records` and `grants`, and `destination` holds the typed identity; a destination the table cannot settle is
`unresolved` or `ambiguous` (its ref says so) and recorded as `write-destination-unresolved`, never guessed. A start event's boundary is a trigger. Person-to-system and person-to-person
boundaries are returned with `counted_apart: true` and never required in the stage output.
An end event is a side like any other node: an edge into or out of one in another lane is a
boundary (an untagged end event in its neighbour's own lane is not). Several processes of a set may share one future-state file (nodes carry `process_id`); it
is derived once. Each edge reference is qualified with its own process, taken from its end nodes'
`process_id` (source first), and each node's owning part, existing-tool side ref and implied boundary
ids use its own `process_id` (pins H2, I13); an edge or node with no process of its own (set-level or
missing) uses the first process id that points to the file.

usage: check_boundaries.py --run RUN_DIR [--out REPORT.json]
exit 0 pass, 1 fail (missing or wrong-shaped input, JSON null included, is a recorded problem), 2 usage or
input that will not parse.
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import evidence_ids  # noqa: E402
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                                "fast-mode-build", "driver"))
import knowledge_root  # noqa: E402  the one lookup for the plugin's knowledge folder
sys.path.insert(0, os.path.join(knowledge_root.find(), "checks"))
import _agent_steps as agent_roles
import _write_grants as wg  # noqa: E402  grant ids, `writes` systems, the Write grants table

CHECK = "check_boundaries"
PATH = "skills/agent-design/checks/check_boundaries.py"
FALLBACK = ("Without this script, an agent lists every future-state edge whose ends differ in lane_id, "
            "agent tag or owning part, both ways for asks_returns edges, and confirms each agent-side one "
            "is in stages/boundaries/boundaries.json; gate edges need one meaning and one evidence state.")
REQUIRED = ("id", "edge_ref", "from", "to", "kind", "counted_apart", "evidence_state")
EVIDENCE_STATES = ("Observed", "Assumed", "Contradictory", "needs_more_information")


def _slug(text):
    s = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(text).strip()).strip("-")
    return s or "unnamed"


def _side(node, pid, known=None, lanes=None):
    lane = node.get("lane")
    if agent_roles.is_tool_use(node):
        return {"side": "existing_tool", "ref": _step_ref(node, pid, known)}
    role, _ = agent_roles.accepted_role(node, lanes or {})
    # Events retain their lane side so cross-lane triggers/end events stay explicit.
    event = node.get("type") in ("start_event", "end_event")
    lane_kind, _ = agent_roles.lane_of(node, lanes or {})
    if role == "agent" or (event and lane_kind == "agent"):
        return {"side": "agent", "ref": node.get("agent") or "?"}
    if role == "person" or lane_kind == "people" or lane == "Analyst":
        return {"side": "person", "ref": node.get("lane_id") or node["id"]}
    if lane == "System":
        return {"side": "system", "ref": node["id"]}
    return {"side": "external", "ref": node.get("lane_id") or node["id"]}


def _kind(a, b, node_a, edge):
    if node_a.get("type") == "start_event":
        return "trigger"
    x, y = a["side"], b["side"]
    sysish = ("system", "existing_tool", "external")
    if x == "agent" and y == "agent":
        return "agent_to_agent"
    if x == "agent" and y == "person":
        return "handover_to_person"
    if x == "person" and y == "agent":
        return "back_from_person"
    if x in sysish and y == "agent":
        return "system_read"
    if x == "agent" and y == "existing_tool":
        return "system_read"
    if x == "agent" and y in ("system", "external"):
        return "system_read" if (edge or {}).get("kind") == "read_access" else "agent_write"
    if x == "person" and y == "person":
        return "person_to_person"
    if x == "person" and y == "system":
        return "person_to_system"
    return "not_agent_side"


def _counted_apart(a, b):
    return "agent" not in (a["side"], b["side"])


def is_counted_apart_kind(kind):
    return kind in ("person_to_system", "person_to_person", "not_agent_side")


def _graph_shape(fs):
    """Why a future state cannot be derived from (None when it can): it must be an object whose graph holds a `nodes`
    list of objects each with a string id, an `edges` list of objects each with a string id, source and target, and,
    when `lanes` is given, a list of objects. Nothing is coerced: a missing list, JSON null or a falsey value of
    another type is a finding."""
    g = fs.get("graph", fs) if isinstance(fs, dict) else None
    if not isinstance(g, dict):
        return "the future state is not an object with a graph"
    for k in ("nodes", "edges", "lanes"):
        if k == "lanes" and k not in g:
            continue
        v = g.get(k)
        if not isinstance(v, list):
            return "the graph's %s is %s, not a list" % (k, "missing" if k not in g else type(v).__name__)
        if not all(isinstance(x, dict) for x in v):
            return "the graph's %s holds an entry that is not an object" % k
    if not all(isinstance(n.get("id"), str) and n["id"] for n in g["nodes"]):
        return "the graph's nodes hold an entry with no string id"
    for k in ("id", "source", "target"):
        if not all(isinstance(e.get(k), str) and e[k] for e in g["edges"]):
            return "the graph's edges hold an entry with no string %s" % k
    return None


def _graph(future_state):
    return future_state.get("graph", future_state)


def derive(future_state, process_id=None, parts=None, known=None, grants=None):
    """Every boundary of one future state, as boundary.schema.json objects.

    `parts` optionally maps qualified step references `<process_id>:<node_id>` to part ids. `known`
    optionally lists the run's process ids: a node's own `process_id` outside it counts as none. `grants` maps a
    grant id to its Write grants row (a row whose System is None is conflicting across tables).
    """
    pid = process_id or future_state.get("process_id") or "p"
    parts = parts or {}
    g = _graph(future_state)
    lanes = agent_roles._lane_index(g)
    nodes = {n["id"]: n for n in g.get("nodes") or []}
    out = []
    drawn = set()
    for e in g.get("edges") or []:
        a, b = nodes.get(e.get("source")), nodes.get(e.get("target"))
        if a is None or b is None:
            continue
        drawn.add((a["id"], b["id"]))
        drawn.add((b["id"], a["id"]))
        sa, sb = _side(a, pid, known, lanes), _side(b, pid, known, lanes)
        pa, pb = parts.get(_step_ref(a, pid, known)), parts.get(_step_ref(b, pid, known))
        differs = (a.get("lane_id") != b.get("lane_id") or (a.get("agent") or None) != (b.get("agent") or None)
                   or ((pa or pb) and pa != pb) or sa != sb)
        if differs and _untagged_end(a, b) and a.get("lane_id") == b.get("lane_id"):
            differs = False
        if not differs:
            continue
        ref = "%s:%s" % (_edge_pid(a, b, pid, known), e["id"])
        halves = [(a, b, sa, sb, ref)]
        if e.get("kind") == "asks_returns":
            halves = [(a, b, sa, sb, ref + "#ask"), (b, a, sb, sa, ref + "#return")]
        for x, y, sx, sy, bid in halves:
            out.append({"id": bid, "edge_ref": ref, "from": dict(sx), "to": dict(sy),
                        "kind": _kind(sx, sy, x, e), "counted_apart": _counted_apart(sx, sy),
                        "evidence_state": "needs_more_information",
                        "source": "edge", "gate_edge": _is_gate(x, e)})
    for n in g.get("nodes") or []:
        s = _side(n, pid, known, lanes)
        if s["side"] != "agent":
            continue
        rows = grants.for_step(_step_ref(n, pid, known)) if hasattr(grants, "for_step") else grants
        for t in _write_targets(n, nodes, g, rows):
            if t["dest"]["type"] == "system" and _connected(n["id"], t["ref"], drawn, nodes):
                continue
            ref = "implied:%s->%s" % (_step_ref(n, pid, known), t["ref"])
            person = t["dest"]["type"] == "system" and _person_dest(t, grants, _step_ref(n, pid, known))
            to = {"side": "person" if person else "system", "ref": t["ref"]}
            out.append({"id": ref, "edge_ref": ref, "from": dict(s), "to": to,
                        "kind": "handover_to_person" if person else "agent_write", "counted_apart": False,
                        "evidence_state": "needs_more_information", "source": "node field", "gate_edge": False,
                        "records": t["records"], "grants": t["grants"], "destination": t["dest"]})
        for sysid in n.get("systems") or []:
            if _connected(n["id"], sysid, drawn, nodes):
                continue
            ref = "implied:%s->%s" % (sysid, _step_ref(n, pid, known))
            out.append({"id": ref, "edge_ref": ref, "from": {"side": "system", "ref": sysid}, "to": dict(s),
                        "kind": "system_read", "counted_apart": False, "evidence_state": "needs_more_information",
                        "source": "node field", "gate_edge": False})
    return out


def _system_ref(name, nodes, g):
    """The ref a system name takes: the `system_surface` node titled with it, else the workspace named with it, else
    the name as a slug."""
    key = wg.system_key(name)
    for n in nodes.values():
        if n.get("type") == "system_surface" and wg.system_key(n.get("title") or "") == key:
            return n["id"]
    for w in g.get("workspaces") or []:
        if isinstance(w, dict) and isinstance(w.get("id"), str) and key in (
                wg.system_key(w.get(k) or "") for k in ("name", "short_label")):
            return w["id"]
    return _slug(name)   # the slug keeps the system's key (wg.system_key), so parity still resolves it


def _grant_dest(gid, grants):
    """(identity of the system a grant writes to, or None when the table does not settle it)."""
    row = grants.get(gid)
    if row is None:
        return None
    system = row.get("System")
    if not isinstance(system, str) or not system.strip() or wg.norm(system) in ("n/a", "none", "unknown"):
        return None
    return system.strip()


def _person_dest(t, grants, step):
    """True when a write target goes to the person: through the shared resolver's reading of its grants (a System
    naming a drawn person step or people lane, or `the person`), else the System `the person` itself."""
    res = getattr(grants, "res", None)
    if res is not None and t["grants"]:
        return any(res.person_destination(x, step) for x in t["grants"] if x in res.grants)
    return wg.norm(t["dest"]["system"]) == wg.PERSON_SYSTEM


def _write_targets(n, nodes, g, grants):
    """One target per destination of an agent node's writes: {"ref", "dest", "records", "grants"}.

    A `writes` entry naming its system ("System: what is written", or a `system` key) goes to that system; one
    naming only a record goes to the system of the node's one grant; with several grants it is ambiguous, with no
    known grant unresolved. A node with grants and no `writes` goes to each grant's system."""
    grants = grants or {}
    gids = wg.grant_ids(n)
    entries = []
    for w in wg._entries(n.get("writes")):
        record = w if isinstance(w, str) else (w.get("record") or w.get("work_object"))
        entries.append((record if isinstance(record, str) else None, wg.write_system(w)))
    if not entries:
        entries = [(None, None)] if gids else []
    out, by_ref = [], {}

    def add(ref, dest, record, gs):
        t = by_ref.get(ref)
        if t is None:
            t = by_ref[ref] = {"ref": ref, "dest": dest, "records": [], "grants": []}
            out.append(t)
        if record and record not in t["records"]:
            t["records"].append(record)
        for x in gs:
            if x not in t["grants"]:
                t["grants"].append(x)

    for record, system in entries:
        if system:
            match = [x for x in gids if wg.norm(_grant_dest(x, grants) or "") == wg.norm(system)]
            add(_system_ref(system, nodes, g), wg.identity(wg.SYSTEM, system, system=system, source_ref=system),
                record, match)
            continue
        dests = [(x, _grant_dest(x, grants)) for x in gids]
        known = [(x, d) for x, d in dests if d]
        if record is None:
            for x, d in dests:
                if d:
                    add(_system_ref(d, nodes, g), wg.identity(wg.SYSTEM, d, system=d, source_ref=x), None, [x])
                else:
                    add("unresolved-" + _slug(x), wg.identity(wg.UNRESOLVED, source_ref=x), None, [x])
        elif len(gids) == 1 and known:
            x, d = known[0]
            add(_system_ref(d, nodes, g), wg.identity(wg.SYSTEM, d, system=d, source_ref=x), record, [x])
        elif len(known) > 1:
            # several grants, a record naming no system: the first grant's system is read, the rest kept as
            # candidates with the reason
            cands = [wg.identity(wg.SYSTEM, d, system=d, source_ref=x) for x, d in known]
            x, d = known[0]
            dest = wg.identity(wg.SYSTEM, d, system=d, source_ref=x, candidates=cands)
            dest["reading"] = "record %s names no system; read as %s, the system of the step's first grant %s" % (
                record, d, x)
            add(_system_ref(d, nodes, g), dest, record, gids)
        else:
            add("unresolved-" + _slug(record), wg.identity(wg.UNRESOLVED, source_ref=record), record, gids)
    return out


def _write_target(text, nodes):
    """The system a real run's `writes` text ("System: what is written") names: the id of the
    `system_surface` node titled with the text before the first ":", else that text as a slug."""
    name = text.split(":", 1)[0].strip()
    for n in nodes.values():
        if n.get("type") == "system_surface" and " ".join(str(n.get("title") or "").split()).lower() == \
                " ".join(name.split()).lower():
            return n["id"]
    return _slug(name)


def _untagged_end(a, b):
    """True when one end is an end event with no agent tag: it stays in its lane's side."""
    return any(n.get("type") == "end_event" and not n.get("agent") for n in (a, b))


def _own_pid(node, known=None):
    """A node's own process id, or None for a set-level node (`process_id: "set"`), one with none, or
    one naming a process outside `known`."""
    own = node.get("process_id")
    if not own or own == "set" or (known is not None and own not in known):
        return None
    return own


def _step_ref(node, pid, known=None):
    """A node's qualified step reference, under its own `process_id` (shared set graphs); a node with
    no single process of its own uses `pid`."""
    return "%s:%s" % (_own_pid(node, known) or pid, node["id"])


def _edge_pid(a, b, pid, known=None):
    """The process an edge belongs to: the first end node (source, then target) with its own
    process; `pid` when neither end has one. Edges of a shared set graph carry none."""
    for n in (a, b):
        own = _own_pid(n, known)
        if own:
            return own
    return pid


def _is_gate(node, edge):
    return node.get("type") == "decision" or edge.get("kind") == "gate"


def _connected(nid, sysid, drawn, nodes):
    """True when a drawn edge (either way) links the node directly to that system."""
    return (nid, sysid) in drawn


# ---------------------------------------------------------------- check

def _report(run_dir, problems, counts, error=None, notes=()):
    if error:
        result, code = "error", 2
    else:
        result, code = ("fail", 1) if problems else ("pass", 0)
    # notes: readings taken where input was ambiguous but readable; recorded, never failing
    return {"check": CHECK, "path": PATH, "run": ".", "exit_code": code, "result": result,
            "problems": problems, "counts": counts, "notes": list(notes), "agent_fallback": FALLBACK}


def _dest_key(ref, grants, graphs):
    """The identity of a boundary's destination, whatever id spelling names it: a grant id reads as its System, a
    workspace id as its name, a system-surface node id as its title, a slug as its words; a destination naming no
    drawn system but the person (the word `person`) is the person."""
    name = ref
    if isinstance(ref, str) and wg.GRANT_ID.match(ref) and isinstance((grants.get(ref) or {}).get("System"), str):
        name = grants[ref]["System"]
    else:
        for _, fs in graphs:
            g = _graph(fs) if isinstance(fs, dict) else {}
            for w in (g.get("workspaces") or (fs.get("workspaces") if isinstance(fs, dict) else None) or []):
                if isinstance(w, dict) and w.get("id") == ref:
                    name = w.get("name") or w.get("short_label") or ref
            for n in g.get("nodes") or []:
                if isinstance(n, dict) and n.get("id") == ref and n.get("type") == "system_surface" and n.get("title"):
                    name = n["title"]
    key = wg.system_key(name)
    # a drawn system first: a workspace named by its name or short label is one system (its id), a system surface
    # its title; only a name nothing draws may read as the person by its words
    for _, fs in graphs:
        g = _graph(fs) if isinstance(fs, dict) else {}
        for w in (g.get("workspaces") or (fs.get("workspaces") if isinstance(fs, dict) else None) or []):
            if isinstance(w, dict) and key in (wg.system_key(w.get(k) or "") for k in ("name", "short_label")):
                return "ws:" + str(w.get("id"))
        for n in g.get("nodes") or []:
            if isinstance(n, dict) and n.get("type") == "system_surface" and wg.system_key(n.get("title") or "") == key:
                return key
    if "person" in wg.words(name):
        return "person"
    return key


def _known_dest(ref, grants, graphs):
    """True when a destination spelling names a grant, a workspace or a system surface the run draws."""
    if isinstance(ref, str) and wg.GRANT_ID.match(ref):
        return ref in grants
    key = wg.system_key(ref)
    for _, fs in graphs:
        g = _graph(fs) if isinstance(fs, dict) else {}
        for w in (g.get("workspaces") or (fs.get("workspaces") if isinstance(fs, dict) else None) or []):
            if isinstance(w, dict) and key in [wg.system_key(w.get(k) or "") for k in ("id", "name", "short_label")]:
                return True
        for n in g.get("nodes") or []:
            if isinstance(n, dict) and n.get("type") == "system_surface" and key in (
                    wg.system_key(n.get("id") or ""), wg.system_key(n.get("title") or "")):
                return True
    return False


def _identities(b, grants, graphs, derived=False):
    """A boundary's identities: for an implied write (`implied:<step>-><destination>`) its source step with each
    spelling of its destination (the resolved system, the grant ids it writes under, the records it writes, since
    an earlier derivation named the destination by grant id or record); for an implied read the system and the
    step; else its id. Id and ref spelling are presentation."""
    bid = b.get("id") if isinstance(b, dict) else None
    if not isinstance(bid, str) or not bid.startswith("implied:") or "->" not in bid:
        return set([bid])
    src, _, dst = bid[len("implied:"):].partition("->")
    if ":" not in src:   # a system read (`implied:<system>-><step>`): the system is the source
        return set([("read", _dest_key(src, grants, graphs), dst)])
    res = getattr(grants, "res", None)
    if hasattr(grants, "for_step"):
        grants = grants.for_step(src)
    keys = set([_dest_key(dst, grants, graphs), wg.system_key(dst)])
    # a destination named by a grant the shared resolver reads as going to the person is the person
    named = [x for x in [dst] + (list(b.get("grants") or []) if derived else []) if isinstance(x, str)
             and wg.GRANT_ID.match(x)]
    if res is not None and any(x in res.grants and res.person_destination(x, src) for x in named):
        keys.add("person")
    # the kind: a hand-over to the person (its side is the person, its kind says so, or its destination resolves to
    # the person) is never the same boundary as a write into a system
    to = b.get("to") if isinstance(b.get("to"), dict) else {}
    person = to.get("side") == "person" or b.get("kind") == "handover_to_person" or "person" in keys
    kind = "person" if person else "write"
    if not derived:   # a stage boundary is known by its own destination spelling only
        if not person and not _known_dest(dst, grants, graphs):
            # a destination named by something only the step's writes know (an earlier derivation's record name):
            # its kind is whatever the derived boundary with that spelling says
            return set((k2, src, k) for k in keys for k2 in ("person", "write"))
        return set((kind, src, k) for k in keys)
    for gid in b.get("grants") or []:
        keys.add(_dest_key(gid, grants, graphs))
        keys.add(wg.system_key(gid))
    for rec in b.get("records") or []:
        keys.add(wg.system_key(_slug(rec)))
    dest = b.get("destination") if isinstance(b.get("destination"), dict) else {}
    if isinstance(dest.get("system"), str):
        keys.add(_dest_key(dest["system"], grants, graphs))
    return set((kind, src, k) for k in keys)


def _load(run_dir, rel):
    p = os.path.join(run_dir, rel)
    with open(p) as f:
        return json.load(f) if rel.endswith(".json") or rel.endswith(".yaml") else f.read()


def _parts(run_dir):
    p = os.path.join(run_dir, "stages", "architecture", "agent.yaml")
    if not os.path.exists(p):
        return {}
    try:
        with open(p) as f:
            doc = json.load(f)
    except (OSError, ValueError):
        return {}
    own = {}
    for part in doc.get("parts") or []:
        for step in part.get("owns_steps") or []:
            own[step] = part.get("id")
    return own


def _count(value):
    if value is None or value == "":
        return 0
    if isinstance(value, list):
        return len([v for v in value if v not in (None, "")])
    return 1


def check(run_dir):
    counts = {"derived": 0, "agent_boundaries": 0, "person_to_system": 0, "person_to_person": 0,
              "other_counted_apart": 0, "stage_boundaries": 0, "missing": 0, "gate_edges": 0}
    try:
        intake = _load(run_dir, "stages/intake/intake.json")
        procs = intake["processes"]
        graphs, resolver_graphs, seen = [], [], set()
        tables = []
        for pr in procs:
            tables.append((pr["transformation_map"], _load(run_dir, pr["transformation_map"])))
            graph = (pr["process_id"], wg.with_source(_load(run_dir, pr["future_state"]), pr["future_state"]))
            resolver_graphs.append(graph)
            if pr["future_state"] in seen:
                continue
            seen.add(pr["future_state"])
            graphs.append(graph)
        intake["observations"]["snapshot_intake"]
        stage = _load(run_dir, "stages/boundaries/boundaries.json")
    except ValueError as exc:
        # only input that will not parse stops the check (exit 2)
        return _report(run_dir, [{"defect": "input-unreadable", "where": ".", "detail": str(exc)}], counts, error=True)
    except OSError as exc:
        return _report(run_dir, [{"defect": "input-missing", "where": ".", "detail": str(exc)}], counts)
    except (KeyError, TypeError, AttributeError) as exc:
        return _report(run_dir, [{"defect": "input-invalid", "where": ".",
                                  "detail": "wrong-shaped input (a missing key, or null or the wrong type where an "
                                            "object or list is needed): %s" % exc}], counts)

    problems, notes = [], []
    resolver = evidence_ids.Resolver(run_dir)
    parts = _parts(run_dir)
    seen_tables, all_rows = set(), []
    for rel, text in tables:
        if rel in seen_tables:
            continue
        seen_tables.add(rel)
        try:
            all_rows.extend(wg.read_table(text) or [])
        except wg.TableError as exc:
            problems.append({"defect": "write-grants-table-unreadable", "where": rel, "detail": str(exc)})
            continue
    # the one shared reading (wg.Resolver): where tables give a grant different rows, a step reads the row whose
    # Steps names it, else the first; the reading is a note
    grants = wg.StepRows(wg.Resolver(resolver_graphs, all_rows))
    notes.extend(dict(n) for n in grants.res.notes if n.get("code") == "grant-reading")
    derived = []
    for pid, fs in graphs:
        shape = _graph_shape(fs)
        if shape:
            problems.append({"defect": "input-invalid", "where": "future state of %s" % pid,
                             "detail": "%s; no boundary is derived from it" % shape})
            continue
        derived.extend(derive(fs, pid, parts, {pr["process_id"] for pr in procs}, grants))
    counts["derived"] = len(derived)
    for b in derived:
        dest = b.get("destination") or {}
        if dest.get("reading"):
            notes.append({"code": "write-destination-reading", "where": b["id"], "detail": dest["reading"],
                          "note": True})
        if dest.get("type") in (wg.UNRESOLVED, wg.AMBIGUOUS):
            problems.append({"defect": "write-destination-unresolved", "where": b["id"],
                             "detail": "the Write grants table does not settle where %s writes (%s, grants %s); "
                                       "needs more information" % (b["from"]["ref"], dest.get("type"),
                                                                   ", ".join(b.get("grants") or []) or "none")})
    for b in derived:
        if b["kind"] == "person_to_system":
            counts["person_to_system"] += 1
        elif b["kind"] == "person_to_person":
            counts["person_to_person"] += 1
        elif b["counted_apart"]:
            counts["other_counted_apart"] += 1
        else:
            counts["agent_boundaries"] += 1

    items = stage.get("boundaries") if isinstance(stage, dict) else None
    if not isinstance(items, list):
        problems.append({"defect": "input-invalid", "where": "stages/boundaries/boundaries.json#/boundaries",
                         "detail": "boundaries list missing"})
        return _report(run_dir, problems, counts)
    counts["stage_boundaries"] = len(items)
    by_id = {}
    for i, it in enumerate(items):
        where = "stages/boundaries/boundaries.json#/boundaries/%d" % i
        if not isinstance(it, dict):
            problems.append({"defect": "input-invalid", "where": where, "detail": "not an object"})
            continue
        for k in REQUIRED:
            if k not in it:
                problems.append({"defect": "input-invalid", "where": where + "/" + k, "detail": "required field missing"})
        ids = it.get("observation_ids")
        if ids is not None and not isinstance(ids, list):
            problems.append({"defect": "input-invalid", "where": where + "/observation_ids", "detail": "must be a list"})
            ids = []
        for kind, detail in evidence_ids.list_problems(resolver, ids, it.get("evidence_state")):
            defect = "observation-id-invalid" if kind == "invalid" else "boundary-evidence"
            problems.append({"defect": defect, "where": where, "detail": detail})
        by_id.setdefault(it.get("id"), []).append(it)

    identities = set()
    for entries in by_id.values():
        for it in entries:
            identities |= _identities(it, grants, graphs)
    for b in derived:
        if b["counted_apart"]:
            continue
        if b["id"] not in by_id and _identities(b, grants, graphs, derived=True) & identities:
            notes.append({"code": "boundary-identity-reading", "where": b["id"], "note": True,
                          "detail": "matched to the stage boundary with the same step and destination under "
                                    "another id spelling"})
            continue
        if b["id"] not in by_id:
            counts["missing"] += 1
            problems.append({"defect": "boundary-missing", "where": b["id"],
                             "detail": "%s boundary %s:%s -> %s:%s not in the stage output" % (
                                 b["kind"], b["from"]["side"], b["from"]["ref"], b["to"]["side"], b["to"]["ref"])})

    gate_ids = {b["id"] for b in derived if b.get("gate_edge")}
    for bid, entries in by_id.items():
        if bid not in gate_ids and not any(e.get("kind") == "gate" or "gate" in e for e in entries):
            continue
        counts["gate_edges"] += 1
        meanings, states = [], []
        for e in entries:
            gate = e.get("gate") if isinstance(e.get("gate"), dict) else {}
            meanings.append(gate.get("meaning"))
            states.append(gate.get("evidence_state"))
        n_meaning = sum(_count(m) for m in meanings)
        n_state = sum(_count(s) for s in states)
        if n_meaning != 1:
            problems.append({"defect": "gate-edge-meaning", "where": str(bid),
                             "detail": "gate edge has %d meanings; exactly one is required" % n_meaning})
        if n_state != 1:
            problems.append({"defect": "gate-edge-evidence", "where": str(bid),
                             "detail": "gate edge has %d evidence states; exactly one is required" % n_state})
        else:
            st = [s for s in states if s][0]
            st = st[0] if isinstance(st, list) else st
            if st not in EVIDENCE_STATES:
                problems.append({"defect": "gate-edge-evidence", "where": str(bid),
                                 "detail": "evidence state %r is not one of %s" % (st, ", ".join(EVIDENCE_STATES))})
    return _report(run_dir, problems, counts, notes=notes)


def main(argv):
    args = list(argv)
    if args[:1] in (["--help"], ["-h"]):
        print("usage: check_boundaries.py --run RUN_DIR [--out REPORT.json]")
        return 0
    run, out = None, None
    try:
        while args:
            a = args.pop(0)
            if a == "--run":
                run = args.pop(0)
            elif a == "--out":
                out = args.pop(0)
            else:
                raise IndexError(a)
    except IndexError:
        run = None
    if not run:
        print("usage: check_boundaries.py --run RUN_DIR [--out REPORT.json]", file=sys.stderr)
        return 2
    rep = check(run)
    target = out or os.path.join(run, "checks", CHECK + ".json")
    try:
        os.makedirs(os.path.dirname(os.path.abspath(target)), exist_ok=True)
        with open(target, "w") as f:
            json.dump(rep, f, indent=1)
    except OSError:
        pass
    print("%s: %s (%d problems)" % (CHECK, rep["result"], len(rep["problems"])))
    for p in rep["problems"]:
        print("  %s %s: %s" % (p["defect"], p["where"], p["detail"]))
    return rep["exit_code"]


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
