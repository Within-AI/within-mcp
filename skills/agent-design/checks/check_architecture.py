#!/usr/bin/env python3
"""Architecture check for Agent Design (tag-keyed).

Usage: check_architecture.py --run <run_dir> [--out <report.json>]
Exit 0 pass, 1 fail, 2 usage or unreadable input. Standard library only.

Reads <run>/stages/intake/intake.json, the upstream files it points to
(processes[].future_state, processes[].transformation_map,
observations.snapshot_intake) and <run>/stages/architecture/agent.yaml (JSON
syntax, read with json.load). A missing or unreadable file is exit 2; a file
that parses but has the wrong shape or field types is exit 1 input-invalid.

Agent steps come only from knowledge/checks/_agent_steps.py (the one ownership rule): a step on the Agent lane
or with an `agent` tag, owned once per process it belongs to (its own and every process in `future.spans`), and
`set:<node>` for a set-level step (declared in agent.yaml's `set_steps`). A part that lists a spanning step once
owns it in every spanned process; a mirrored listing is accepted with a note. Sub-agents are told apart by
`agent` tag, resolved across the whole set. Steps and edges are referenced `<process_id>:<id>`.

Finding codes: step-unowned, step-owned-twice, part-incomplete,
routing-unmarked, model-on-recorded-decision, split-no-reason,
departure-no-evidence, dispatch-undeclared, tag-merge-unrecorded,
cross-process-end-unowned, cross-process-dispatch-undeclared, set-step-undeclared,
person-step-owned, ambiguous-owner, unknown-span, spans-without-process,
plus input-invalid for an input that parses but has the wrong shape.

Set level (more than one process): an edge of a shared future-state graph whose ends belong to two different
processes (each end's own `process_id`) is outside every process's own graph, so it is checked once for the set.
When either end is an agent step, both ends must be agent steps owned by exactly one part
(cross-process-end-unowned), except that an agent step may hand over to a person step of another process when a
`person_handover` is declared for it (`from` the qualified agent step and `to` the qualified person step, or from
the owning part to `person:<lane_id>` listing the edge); when the two parts differ, a `dispatch` handover from the sending part to the
receiving part must list the edge on both sides: `<sending process>:<edge_id>` (its outbound side) and
`<receiving process>:<edge_id>` (its inbound side) (cross-process-dispatch-undeclared). A single-process run
has no such edge.
"""

import json
import os
import re
import sys
from urllib.parse import unquote

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(HERE)), "fast-mode-build", "driver"))
import knowledge_root  # noqa: E402  the one lookup for the plugin's knowledge folder

sys.path.insert(0, os.path.join(knowledge_root.find(), "checks"))
import _agent_steps  # noqa: E402  the one rule for which steps the agent owns

CHECK = "check_architecture"
CHECK_PATH = "skills/agent-design/checks/check_architecture.py"
AGENT_REL = "stages/architecture/agent.yaml"
INTAKE_REL = "stages/intake/intake.json"
REPORT_REL = "checks/check_architecture.json"
SPLIT_REASONS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "formats", "lists",
                             "split-reasons.json")
FALLBACK = ("Read stages/architecture/agent.yaml beside each future-state graph: every node with an agent "
            "tag and no ai_use is owned by exactly one part, every edge between agent steps of different "
            "parts is a declared dispatch, and every merge of two agent tags into one part is a departure "
            "with evidence.")
PART_FIELDS = ("input", "output", "surfaces_read", "end_point")
ROUTING_KINDS = ("code", "model")
RECORDED_BASIS = re.compile(r"recorded[\s_-]+decision|fixed[\s_-]+order", re.I)


class Unreadable(Exception):
    """A required input is absent or does not parse."""


def _load_json(path, rel):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        raise Unreadable("%s: file not found" % rel)
    except (OSError, ValueError) as exc:
        raise Unreadable("%s: does not parse as JSON (%s)" % (rel, exc))


def _load_text(path, rel):
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except FileNotFoundError:
        raise Unreadable("%s: file not found" % rel)
    except (OSError, ValueError) as exc:
        raise Unreadable("%s: cannot be read as text (%s)" % (rel, exc))


def load_agent(run_dir):
    """Read stages/architecture/agent.yaml with json.load; raise ValueError naming the file if it fails."""
    try:
        return _load_json(os.path.join(run_dir, AGENT_REL), AGENT_REL)
    except Unreadable as exc:
        raise ValueError(str(exc))


def _split_reasons():
    with open(SPLIT_REASONS, encoding="utf-8") as fh:
        return set(json.load(fh)["reasons"])


def _report(problems, counts, error=None):
    if error is not None:
        result, code = "error", 2
        problems = [{"defect": "input-unreadable", "where": error[0], "detail": error[1]}]
    elif problems:
        result, code = "fail", 1
    else:
        result, code = "pass", 0
    return {"check": CHECK, "path": CHECK_PATH, "run": ".", "exit_code": code, "result": result,
            "problems": problems, "counts": counts, "agent_fallback": FALLBACK}


def _nonempty(value):
    if value is None:
        return False
    if isinstance(value, (str, list, dict)):
        return len(value) > 0
    return True


def _strings(obj):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            for s in _strings(v):
                yield s
    elif isinstance(obj, list):
        for v in obj:
            for s in _strings(v):
                yield s


def _names_tag(entry, tag):
    pat = re.compile(r"(?<![\w-])%s(?![\w-])" % re.escape(tag))
    return any(pat.search(s) for s in _strings(entry))


def _read_inputs(run_dir):
    """Return (processes, agent, invalid, cross) or raise Unreadable; processes is [(process_id, graph)], cross the
    set's cross-process edges as [(source process_id, target process_id, edge)]."""
    if not os.path.isdir(run_dir):
        raise Unreadable(".: run folder not found")
    intake = _load_json(os.path.join(run_dir, INTAKE_REL), INTAKE_REL)
    invalid = []
    procs = intake.get("processes") if isinstance(intake, dict) else None
    if not isinstance(procs, list) or not procs:
        return None, None, [(INTAKE_REL + "#/processes", "intake has no processes list")], [], []
    processes, cross, shared, raw = [], [], {}, []
    pids = set(p["process_id"] for p in procs if isinstance(p, dict) and _is_text(p.get("process_id")))
    for i, p in enumerate(procs):
        where = "%s#/processes/%d" % (INTAKE_REL, i)
        if not isinstance(p, dict) or not _is_text(p.get("process_id")) \
                or not _is_text(p.get("future_state")) or not _is_text(p.get("transformation_map")):
            invalid.append((where, "process lacks a process_id, future_state or transformation_map string"))
            continue
        _load_text(os.path.join(run_dir, p["transformation_map"]), p["transformation_map"])
        rel = p["future_state"]
        fs = _load_json(os.path.join(run_dir, rel), rel)
        graph = fs.get("graph", fs) if isinstance(fs, dict) else None
        if not isinstance(graph, dict) or not isinstance(graph.get("nodes"), list) \
                or not isinstance(graph.get("edges", []), list):
            invalid.append((rel + "#/graph", "future state has no nodes list"))
            continue
        bad = _graph_invalid(rel, graph)
        if bad:
            invalid.extend(bad)
            continue
        processes.append((p["process_id"], _own_graph(graph, p["process_id"]) if len(procs) > 1 else graph))
        raw.append((p["process_id"], rel, graph))
        if len(procs) > 1 and rel not in shared:
            shared[rel] = True
            cross.extend(_cross_edges(graph, pids))
    obs = intake.get("observations")
    snap = obs.get("snapshot_intake") if isinstance(obs, dict) else None
    if not _is_text(snap):
        invalid.append((INTAKE_REL + "#/observations/snapshot_intake", "intake names no snapshot intake file"))
    else:
        _load_json(os.path.join(run_dir, snap), snap)
    try:
        agent = load_agent(run_dir)
    except ValueError as exc:
        raise Unreadable(str(exc))
    if not isinstance(agent, dict) or not isinstance(agent.get("parts"), list) \
            or not isinstance(agent.get("header"), dict):
        invalid.append((AGENT_REL, "agent.yaml lacks header or parts list"))
    else:
        invalid.extend(_agent_invalid(agent))
    return processes, agent, invalid, cross, raw


def _cross_edges(graph, pids):
    """[(source pid, target pid, edge)]: edges whose two ends carry different process ids of the set."""
    owner = dict((n.get("id"), n.get("process_id")) for n in graph["nodes"] if isinstance(n, dict))
    out = []
    for e in graph.get("edges") or []:
        if not isinstance(e, dict):
            continue
        a, b = owner.get(e.get("source")), owner.get(e.get("target"))
        if isinstance(a, str) and isinstance(b, str) and a in pids and b in pids and a != b:
            out.append((a, b, e))
    return out


def _own_graph(graph, pid):
    """The part of a future-state graph that belongs to one process.

    A set's processes all point at one shared graph; a node belongs to process pid when its process_id is pid,
    "set" (set-level) or absent (shared). Edges are kept only between kept nodes and decision tables only when
    their owner is kept. Used only when the intake lists more than one process.
    """
    nodes = [n for n in graph["nodes"]
             if not isinstance(n, dict) or n.get("process_id") in (pid, "set", None)]
    if len(nodes) == len(graph["nodes"]):
        return graph
    kept = set(n.get("id") for n in nodes if isinstance(n, dict))
    edges = [e for e in graph.get("edges") or []
             if not isinstance(e, dict) or (e.get("source") in kept and e.get("target") in kept)]
    tables = graph.get("decision_tables")
    if isinstance(tables, list):
        tables = [t for t in tables if not isinstance(t, dict) or not t.get("owner") or t["owner"] in kept]
    return dict(graph, nodes=nodes, edges=edges, decision_tables=tables)


def _is_text(value):
    return isinstance(value, str) and value.strip() != ""


def _type_errors(obj, base, spec):
    """Check the fields of obj that are present (not None) against spec {field: kind}.

    kind is "str", "list-str", "dict" or "list"; an absent field is left to the
    dedicated findings, a present one of the wrong type is input-invalid.
    """
    out = []
    for field, kind in spec:
        value = obj.get(field)
        if value is None:
            continue
        ok = {"str": lambda v: isinstance(v, str),
              "list-str": lambda v: isinstance(v, list) and all(isinstance(x, str) for x in v),
              "dict": lambda v: isinstance(v, dict),
              "list": lambda v: isinstance(v, list)}[kind](value)
        if not ok:
            out.append(("%s/%s" % (base, field), "expected %s, found %s"
                        % (kind.replace("list-str", "list of strings"), type(value).__name__)))
    return out


PART_TYPES = (("id", "str"), ("role", "str"), ("owns_steps", "list-str"), ("grants", "list-str"),
              ("input", "str"), ("output", "str"), ("surfaces_read", "list-str"), ("end_point", "str"),
              ("routing", "dict"), ("split_reason", "str"), ("split_evidence", "list-str"))
ROUTING_TYPES = (("kind", "str"), ("marked", "str"), ("basis", "str"))
HANDOVER_TYPES = (("from", "str"), ("to", "str"), ("edge_refs", "list-str"), ("kind", "str"))
DEPARTURE_TYPES = (("evidence", "list"), ("change", "str"))
NODE_TYPES = (("id", "str"), ("agent", "str"))
EDGE_TYPES = (("id", "str"), ("source", "str"), ("target", "str"))


def _agent_invalid(agent):
    """Structural check of agent.yaml field types before evaluation."""
    out = []
    header = agent["header"]
    out += _type_errors(header, AGENT_REL + "#/header", (("handovers", "list"),))
    for i, h in enumerate(header.get("handovers") if isinstance(header.get("handovers"), list) else []):
        base = "%s#/header/handovers/%d" % (AGENT_REL, i)
        out += _type_errors(h, base, HANDOVER_TYPES) if isinstance(h, dict) else [(base, "expected an object")]
    for i, p in enumerate(agent["parts"]):
        base = "%s#/parts/%d" % (AGENT_REL, i)
        if not isinstance(p, dict):
            out.append((base, "expected an object"))
            continue
        out += _type_errors(p, base, PART_TYPES)
        if isinstance(p.get("routing"), dict):
            out += _type_errors(p["routing"], base + "/routing", ROUTING_TYPES)
    out += _type_errors(agent, AGENT_REL + "#", (("departures", "list"), ("set_steps", "list-str"),
                                                  ("set_summary", "str"), ("source_support", "list"),
                                                  ("single_part_reason", "str")))
    for i, d in enumerate(agent.get("departures") if isinstance(agent.get("departures"), list) else []):
        base = "%s#/departures/%d" % (AGENT_REL, i)
        out += _type_errors(d, base, DEPARTURE_TYPES) if isinstance(d, dict) else [(base, "expected an object")]
    return out


def _graph_invalid(rel, graph):
    """Structural check of the future-state node and edge fields the check reads."""
    out = []
    for key, spec in (("nodes", NODE_TYPES), ("edges", EDGE_TYPES)):
        for i, item in enumerate(graph.get(key) or []):
            base = "%s#/graph/%s/%d" % (rel, key, i)
            out += _type_errors(item, base, spec) if isinstance(item, dict) else [(base, "expected an object")]
    if not isinstance(graph.get("decision_tables") or [], list):
        out.append((rel + "#/graph/decision_tables", "expected list"))
    return out


def _evaluate(processes, agent, cross=(), raw=None):
    problems = []

    def add(defect, where, detail):
        problems.append({"defect": defect, "where": where, "detail": detail})

    # Future-state agent steps, keyed by qualified reference.
    steps = {}       # ref -> agent tag
    nodes = {}       # ref -> node
    tables = set()   # decision-table owners, qualified
    edges = []       # (process_id, edge)
    ai_use = 0
    if raw is None:
        raw = [(pid, pid, graph) for pid, graph in processes]
    set_steps = agent.get("set_steps") if isinstance(agent.get("set_steps"), list) else []
    own = _agent_steps.run_ownership(raw, set_steps=set_steps)
    for ref, info in own.owned.items():
        steps[ref] = info["agent"]  # "" for an Agent-lane step with no tag
        nodes[ref] = own.node_at(ref) or {}  # qualified: the same bare id in two processes is two nodes
    for n in own.node_list:  # every node of every graph once, never merged by bare id
        if _nonempty(n.get("ai_use")):
            ai_use += 1
    for pid, graph in processes:
        for dt in graph.get("decision_tables") or []:
            if isinstance(dt, dict) and dt.get("owner"):
                tables.add("%s:%s" % (pid, dt["owner"]))
                ref = own.ref_for(pid, dt["owner"])
                if ref:
                    tables.add(ref)
        for e in graph.get("edges") or []:
            if isinstance(e, dict):
                edges.append((pid, e))
    for f in own.findings:
        add(f["code"], f["where"], f["detail"])

    parts = [p for p in agent["parts"] if isinstance(p, dict)]
    header = agent["header"]
    departures = [d for d in agent.get("departures") or [] if isinstance(d, dict)]
    reasons = _split_reasons()

    # Role names never confer authority or exempt a part from ownership checks.
    ids = [p.get("id") for p in parts]
    for i, p in enumerate(parts):
        if not _is_text(p.get("id")) or ids.count(p.get("id")) > 1:
            add("ambiguous-owner", "%s#/parts/%d/id" % (AGENT_REL, i),
                "part id %r is missing or duplicated" % p.get("id"))
    authority = header.get("authority")
    if isinstance(authority, dict) and authority.get("default") != "read-only and recommend-only":
        add("unsupported-authority", AGENT_REL + "#/header/authority",
            "architecture cannot confer authority: %r" % authority.get("default"))
    for i, write in enumerate(header.get("proposed_writes") or []):
        if isinstance(write, dict) and write.get("status") != "proposed for ratification":
            add("unsupported-authority", "%s#/header/proposed_writes/%d" % (AGENT_REL, i),
                "write remains a proposal, not granted authority: %r" % write)

    modern = "source_support" in agent or any(p.get("role") in ("agent", "script", "router") for p in parts)
    if modern:
        support = agent.get("source_support") or []
        valid_support = []
        for i, row in enumerate(support):
            where = "%s#/source_support/%d" % (AGENT_REL, i)
            if not isinstance(row, dict) or not all(_is_text(row.get(k)) for k in
                    ("element", "source", "detail", "kind", "evidence_state")):
                add("design-support-missing", where, "source support needs element, source, kind, evidence state and detail")
                continue
            if row["kind"] not in ("step", "read", "write", "system_state", "handoff", "person_return", "shape") or row["evidence_state"] not in ("Observed", "Assumed", "Contradictory", "needs_more_information"):
                add("design-support-missing", where, "unsupported source kind or evidence state: %r" % row)
                continue
            if row["evidence_state"] in ("Contradictory", "needs_more_information"):
                add("design-support-unresolved", where, "source detail remains unresolved: %r" % row)
            valid_support.append(row)
        if not any(row["kind"] == "shape" for row in valid_support):
            add("design-support-missing", AGENT_REL + "#/source_support", "logical decomposition needs source-supported shape reasoning")
        if len(parts) == 1:
            reason = agent.get("single_part_reason", "").strip()
            if not reason or reason.lower().rstrip(".") == "no split reason applies":
                add("single-part-no-reason", AGENT_REL + "#/single_part_reason",
                    "one part needs substantive responsibility and complexity rationale with source support")

        for i, part in enumerate(parts):
            refs = set(part.get("owns_steps") or []) | {part.get("id")}
            if not any(row["element"] in refs for row in valid_support):
                add("design-support-missing", "%s#/parts/%d" % (AGENT_REL, i),
                    "part %r needs source support naming its responsibility or owned step" % part.get("id"))

    owners, notes = _agent_steps.expand_claims(own, [(str(p.get("id")), p.get("owns_steps") or []) for p in parts])
    report_notes = []  # non-blocking: a mirrored claim is accepted
    for f in notes:
        if f["code"] == "person-step-owned":
            add(f["code"], f["where"], f["detail"])
        else:
            report_notes.append(f)
    claimed = {}  # part id -> refs it owns after expansion
    for ref, ps in owners.items():
        for p in ps:
            claimed.setdefault(p, set()).add(ref)

    # Ownership: exactly one part per agent step (a spanning step is judged once per process it belongs to).
    covered = set()
    for ref in sorted(steps):
        holders = owners.get(ref, [])
        if not holders:
            add("step-unowned", ref, "agent step (tag %r) is in no part's owns_steps" % steps[ref])
        elif len(holders) > 1:
            add("step-owned-twice", ref, "agent step (tag %r) is owned by %s" % (steps[ref], ", ".join(holders)))
        else:
            covered.add(ref.split(":", 1)[0])

    covered = {pid for pid, _ in processes
               if any(ref.startswith(pid + ":") for ref in steps)
               and all(len(owners.get(ref, [])) == 1 for ref in steps if ref.startswith(pid + ":"))}

    for i, p in enumerate(parts):
        pid = str(p.get("id") or "#%d" % i)
        where = "%s#/parts/%d" % (AGENT_REL, i)
        for field in PART_FIELDS:
            if not _nonempty(p.get(field)):
                add("part-incomplete", "%s/%s" % (where, field), "part %s has no %s" % (pid, field))
        routing = p.get("routing")
        kind = routing.get("kind") if isinstance(routing, dict) else None
        if kind not in ROUTING_KINDS or not _nonempty(routing.get("marked")):
            add("routing-unmarked", where + "/routing",
                "part %s routing is not marked code or model (kind %r)" % (pid, kind))
        elif kind == "model":
            basis = routing.get("basis") if isinstance(routing.get("basis"), str) else ""
            tabled = sorted(r for r in claimed.get(pid, set()) | set(p.get("owns_steps") or [])
                            if r in tables or _nonempty((nodes.get(r) or {}).get("decision_table")))
            if RECORDED_BASIS.search(basis):
                add("model-on-recorded-decision", where + "/routing",
                    "part %s routes by model but its basis is %r" % (pid, basis))
            elif tabled:
                add("model-on-recorded-decision", where + "/routing",
                    "part %s routes by model but owns decision step(s) with a decision table: %s"
                    % (pid, ", ".join(tabled)))
        if p.get("role") == "sub-agent" and p.get("split_reason") not in reasons:
            add("split-no-reason", where + "/split_reason",
                "sub-agent %s split reason %r is not in split-reasons.json" % (pid, p.get("split_reason")))

    for i, d in enumerate(departures):
        if d.get("change") in ("role", "authority", "person_step") and d.get("sign_off") != "pending business-owner sign-off":
            add("departure-sign-off", "%s#/departures/%d/sign_off" % (AGENT_REL, i),
                "departure %r needs pending business-owner sign-off" % d.get("change"))
        if not _nonempty(d.get("evidence")):
            add("departure-no-evidence", "%s#/departures/%d" % (AGENT_REL, i),
                "departure %r carries no evidence" % d.get("change"))

    # Dispatch and tag merges: edges whose both endpoints are agent steps.
    dispatch = {}
    for h in header.get("handovers") or []:
        if isinstance(h, dict) and h.get("kind") == "dispatch":
            for ref in h.get("edge_refs") or []:
                dispatch.setdefault((h.get("from"), h.get("to")), set()).add(ref)
    merges_recorded = [d for d in departures if _nonempty(d.get("evidence"))]
    cross_count = 0
    for pid, e in edges:
        src = own.ref_for(pid, e.get("source")) or "%s:%s" % (pid, e.get("source"))
        dst = own.ref_for(pid, e.get("target")) or "%s:%s" % (pid, e.get("target"))
        if src not in steps or dst not in steps:
            continue
        a, b = owners.get(src, []), owners.get(dst, [])
        if len(a) != 1 or len(b) != 1:
            continue  # ownership findings already name these steps
        eref = "%s:%s" % (pid, e.get("id"))
        if a[0] != b[0]:
            cross_count += 1
            if eref not in dispatch.get((a[0], b[0]), set()):
                add("dispatch-undeclared", str(e.get("id")),
                    "edge %s (%s -> %s) crosses from part %s to part %s with no dispatch handover naming it"
                    % (eref, src, dst, a[0], b[0]))
        elif steps[src] and steps[dst] and steps[src] != steps[dst]:  # an untagged step merges no tag
            if not any(_names_tag(d, steps[src]) and _names_tag(d, steps[dst]) for d in merges_recorded):
                add("tag-merge-unrecorded", str(e.get("id")),
                    "edge %s joins agent tags %r and %r inside part %s with no departure recording the merge"
                    % (eref, steps[src], steps[dst], a[0]))

    # Person handovers: exact (qualified agent step, qualified person step) pairs, nothing looser.
    step_handovers = set()
    part_handovers = []
    for h in list(header.get("handovers") or []) + list(agent.get("handovers") or []):
        if not isinstance(h, dict) or h.get("kind", "person_handover") != "person_handover":
            continue
        step_handovers.add((h.get("from"), h.get("to")))
        part_handovers.append(h)

    # Set level: edges between two processes sit outside every process's own graph.
    for spid, tpid, e in cross:
        src = own.ref_for(spid, e.get("source")) or "%s:%s" % (spid, e.get("source"))
        dst = own.ref_for(tpid, e.get("target")) or "%s:%s" % (tpid, e.get("target"))
        if src not in steps and dst not in steps:
            continue
        eid = str(e.get("id"))
        if src in steps and dst not in steps and own.is_person_ref(dst):
            # Agent -> person in another process: needs a declared person handover, never ownership.
            holder = owners.get(src, [])
            ok = (src, dst) in step_handovers
            person_lane = (own.node_at(dst) or {}).get("lane_id")
            if len(holder) == 1 and person_lane:
                ok = ok or any(h.get("from") == holder[0] and h.get("to") == "person:" + person_lane
                               and any(ref in (h.get("edge_refs") or [])
                                       for ref in (eid, spid + ":" + eid, tpid + ":" + eid))
                               for h in part_handovers)
            if len(holder) == 1 and ok:
                continue
            add("cross-process-end-unowned", eid,
                "edge %s (%s -> %s) hands over from an agent step to a person step of another process with no "
                "person_handover declared for it" % (eid, src, dst) if len(holder) == 1 else
                "edge %s (%s -> %s) crosses processes; %s is not an agent step owned by exactly one part"
                % (eid, src, dst, src))
            continue
        ends = [(r, owners.get(r, [])) for r in (src, dst)]
        bad = [r for r, own in ends if r not in steps or len(own) != 1]
        if bad:
            add("cross-process-end-unowned", eid,
                "edge %s (%s -> %s) crosses processes; %s is not an agent step owned by exactly one part"
                % (eid, src, dst, " and ".join(bad)))
            continue
        a, b = ends[0][1][0], ends[1][1][0]
        if a == b:
            continue
        sides = ["%s:%s" % (spid, eid), "%s:%s" % (tpid, eid)]
        declared = dispatch.get((a, b), set())
        missing = [r for r in sides if r not in declared]
        cross_count += 1
        if missing:
            add("cross-process-dispatch-undeclared", eid,
                "edge %s (%s -> %s) hands off from part %s to part %s across processes; the dispatch from %s to %s "
                "does not list %s" % (eid, src, dst, a, b, a, b, " or ".join(missing)))

    tags = sorted(set(t for t in steps.values() if t))
    counts = {"processes": len(processes), "agent_steps": len(steps), "agent_tags": len(tags),
              "covered_processes": len(covered),
              "ai_use_nodes": ai_use, "parts": len(parts), "cross_part_edges": cross_count}
    return problems, counts, report_notes, sorted(covered)



def _source_pointer(path, pointer):
    """Resolve location only, never the semantic sufficiency of its contents.

    JSON fragments use RFC 6901 or an exact, unique object id. Markdown uses
    heading text (ATX or Setext), ignoring case and repeated whitespace.
    Unsupported formats return unknown rather than manufactured verification.
    """
    suffix = os.path.splitext(path)[1].lower()
    if suffix not in (".md", ".markdown", ".json"):
        return "unknown", "unsupported source format; pointer and source meaning remain unverified"
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        if re.search(r"%(?![0-9a-fA-F]{2})", pointer):
            return "unresolved", "malformed URI fragment escape"
        pointer = unquote(pointer, errors="strict")
        if suffix != ".json":
            normalize = lambda value: " ".join(value.split()).casefold()
            target = normalize(pointer.strip().lstrip("#").strip())
            headings = []
            fence = None
            previous = ""
            for line in text.splitlines():
                marker = re.match(r"^ {0,3}(`{3,}|~{3,})", line)
                if fence:
                    if re.fullmatch(r" {0,3}" + re.escape(fence[0]) + "{" + str(len(fence)) + r",}\s*", line):
                        fence = None
                    previous = ""
                    continue
                if marker:
                    fence = marker.group(1)
                    previous = ""
                    continue
                heading = re.match(r"^ {0,3}#{1,6}(?:[ \t]+(.*?)|[ \t]*)$", line)
                if heading:
                    title = re.sub(r"[ \t]+#+[ \t]*$", "", heading.group(1) or "")
                    headings.append(normalize(title))
                elif previous.strip() and re.fullmatch(r" {0,3}(?:=+|-+)[ \t]*", line):
                    headings.append(normalize(previous))
                previous = line if line.strip() and not line.startswith(("    ", "\t")) else ""
            if target and target in headings:
                return "resolved", "Markdown heading exists; semantic support requires independent review"
            return "unresolved", "no matching Markdown heading (case and whitespace normalized)"
        value = json.loads(text)
        if pointer.startswith("/"):
            for token in pointer[1:].split("/"):
                if re.search(r"~(?![01])", token):
                    return "unresolved", "invalid RFC 6901 escape"
                token = token.replace("~1", "/").replace("~0", "~")
                if isinstance(value, dict) and token in value:
                    value = value[token]
                elif isinstance(value, list) and re.fullmatch(r"0|[1-9][0-9]*", token) and int(token) < len(value):
                    value = value[int(token)]
                else:
                    return "unresolved", "RFC 6901 pointer does not resolve"
            return "resolved", "JSON pointer exists; semantic support requires independent review"
        # Bare fragments identify explicit ids only, never titles or guessed keys.
        pending, matches = [value], 0
        while pending:
            item = pending.pop()
            if isinstance(item, dict):
                matches += item.get("id") == pointer
                pending.extend(item.values())
            elif isinstance(item, list):
                pending.extend(item)
        if matches == 1:
            return "resolved", "explicit JSON object id exists; semantic support requires independent review"
        return "unresolved", "expected RFC 6901 pointer or unique explicit JSON object id; found %d id matches" % matches
    except (OSError, ValueError, UnicodeError, RecursionError) as exc:
        return "unknown", "source could not be resolved (%s); source support remains unverified" % type(exc).__name__


def check(run_dir):
    """Return the check report (never raises)."""
    try:
        processes, agent, invalid, cross, raw = _read_inputs(run_dir)
    except Unreadable as exc:
        where, _, detail = str(exc).partition(": ")
        return _report([], {}, error=(where, detail))
    if invalid:
        return _report([{"defect": "input-invalid", "where": w, "detail": d} for w, d in invalid], {})
    graph_rel = "stages/architecture/internal-dag.graph.json"
    support_agent = agent
    graph_path = os.path.join(run_dir, graph_rel)
    graph_problem = None
    if os.path.isfile(graph_path):
        try:
            graph = _load_json(graph_path, graph_rel)
            if isinstance(graph, dict) and isinstance(graph.get("nodes"), list):
                refs = {n.get("id"): n.get("fs_ref") for n in graph["nodes"] if isinstance(n, dict) and _is_text(n.get("id")) and _is_text(n.get("fs_ref"))}
                support_agent = dict(agent)
                support_agent["source_support"] = [dict(row, element=refs.get(row.get("element")) or row.get("element"))
                    if isinstance(row, dict) else row for row in agent.get("source_support") or []]
            else:
                graph_problem = {"defect": "input-invalid", "where": graph_rel,
                                 "detail": "authored graph has no nodes list; only projection ownership is available"}
        except (Unreadable, TypeError, ValueError) as exc:
            graph_problem = {"defect": "input-unreadable", "where": graph_rel, "detail": str(exc)}
    try:
        problems, counts, notes, covered = _evaluate(processes, support_agent, cross, raw)
    except Exception as exc:  # pins G28: check() never raises
        return _report([{"defect": "input-invalid", "where": AGENT_REL,
                         "detail": "input shape the check cannot evaluate (%s: %s)" % (type(exc).__name__, exc)}], {})
    if graph_problem:
        problems.append(graph_problem)
    # Check source availability without treating a readable filename as proof of meaning.
    for i, row in enumerate(agent.get("source_support") or []):
        if not isinstance(row, dict) or not _is_text(row.get("source")):
            continue
        source = row["source"]
        if "observation:" in source:
            continue  # observation membership is checked by the dedicated evidence check
        rel, sep, pointer = source.partition("#")
        path = os.path.realpath(os.path.join(run_dir, rel))
        root = os.path.realpath(run_dir)
        if os.path.isabs(rel) or not path.startswith(root + os.sep) or not os.path.isfile(path) or not sep or not pointer.strip():
            problems.append({"defect": "design-source-unavailable", "where": "%s#/source_support/%d/source" % (AGENT_REL, i),
                             "detail": "expected available run-relative source with exact section/node pointer: %r" % source})
            continue
        state, detail = _source_pointer(path, pointer)
        where = "%s#/source_support/%d/source" % (AGENT_REL, i)
        if state == "unresolved":
            problems.append({"defect": "design-source-unresolved", "where": where,
                             "detail": "%r: %s" % (source, detail)})
        elif state == "unknown":
            notes.append({"code": "design-source-unknown", "where": where,
                          "detail": "%r: %s" % (source, detail)})
    if agent.get("source_support"):
        notes.append({"code": "source-semantic-review-required", "where": AGENT_REL,
                      "detail": "Source pointer resolution establishes location only. Source meaning, design sufficiency and single-part rationale remain independent-review/live obligations."})
    report = _report(problems, counts)
    if not os.path.isfile(os.path.join(run_dir, "stages/architecture/internal-dag.graph.json")):
        notes.append({"code": "legacy-available-detail", "where": AGENT_REL,
                      "detail": "No authored internal graph; findings cover available projection and upstream ownership only, not internal execution or visual fidelity."})
    report["notes"] = notes  # non-blocking; never changes the result
    report["covered"] = covered  # the processes whose agent steps are each owned by exactly one part
    return report


def main(argv):
    args = list(argv)
    run_dir = out = None
    while args:
        flag = args.pop(0)
        if flag in ("--run", "--out") and args:
            if flag == "--run":
                run_dir = args.pop(0)
            else:
                out = args.pop(0)
        else:
            run_dir = None
            break
    if not run_dir:
        sys.stderr.write("usage: check_architecture.py --run <run_dir> [--out <report.json>]\n")
        return 2
    report = check(run_dir)
    target = out or os.path.join(run_dir, REPORT_REL)
    if not out and not os.path.isdir(run_dir):
        target = None
    try:
        if target is None:
            raise OSError("run folder not found")
        os.makedirs(os.path.dirname(os.path.abspath(target)), exist_ok=True)
        with open(target, "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=2)
            fh.write("\n")
    except OSError as exc:
        sys.stderr.write("could not write report %s: %s\n" % (target, exc))
    for p in report["problems"]:
        print("%s %s: %s" % (p["defect"], p["where"], p["detail"]))
    print("%s: %s" % (CHECK, report["result"]))
    return report["exit_code"]


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
