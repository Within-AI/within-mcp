#!/usr/bin/python3
"""Supply check: every value a placed rule reads must be supplied at or before the rule's step.

Usage: /usr/bin/python3 skills/agent-design/checks/supply_check.py --run <run_dir> [--out <report.json>]
Exit 0 pass, 1 fail (a missing input file is a recorded `input-missing` problem), 2 usage (no run folder) or input
that will not parse. The report follows formats/lists/check-report.schema.json.

A value is supplied by: an inbound field (direction read, inbound_control or back_from_person) at or before
the rule's step in the same process; the case input (a `case.` token); a declared configuration value; a
declared stand-in (a `stand_in.` token with a `declarations.json` `stand_ins` entry stamped Assumed); or a
value set by a rule at an earlier step of the same process, in the same part, or in another part when an
`outbound_handoff` field carries the token across a declared `dispatch` handover from the setting part to the
reading part (its boundary's edge named in that handover, leaving the setting part at or after the setter's
step and reaching the reading part at or before the reader's step). Fields are typed by their
declared direction, never by id. One problem per (rule, token), reason chosen in this order:
written-by-same-rule, input-field-only-computed, supplied-only-later, order-unknown (the supplier sits in a
process the set map leaves unordered against the reader's), never-supplied. A spanning agent step is owned and
supplied once in every process it spans (U9a's `_agent_steps`); steps in different processes are ordered by the
set map's flow links.

Predicate binding (declared reads against the reads the expression uses) and the honesty stamp (each placed
rule's evidence state, taken from rules.json when the placed rule has none, and an open item for every needs_more_information stamp) run here too. Every rule
expression is first checked with `expr.validate`; a malformed one is `input-invalid` and nothing is computed.
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "scripts"))
import expr as expr_mod  # noqa: E402
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(HERE)), "fast-mode-build", "driver"))
import knowledge_root  # noqa: E402  the one lookup for the plugin's knowledge folder
sys.path.insert(0, os.path.join(knowledge_root.find(), "checks"))
import _write_grants as wg  # noqa: E402  the one resolver of boundary sides and data direction
import _agent_steps  # noqa: E402  U9a: the one rule for agent-owned steps, spanning steps owned once per process

CHECK = "supply_check"
CHECK_PATH = "skills/agent-design/checks/supply_check.py"
REPORT_REL = "checks/supply_check.json"
FALLBACK = "none: a script check; an agent reads the report and routes each problem to the fields stage"

INBOUND = ("read", "inbound_control", "back_from_person")
OUTBOUND = ("write", "person_write", "draft", "outbound_handoff", "dispatch")
BINDING_MESSAGES = ("token read but not declared:", "token declared but not read:")
STATES = ("Observed", "Assumed", "Contradictory", "needs_more_information")
SOURCES = ("rule", "input", "configuration", "stand_in")
FIELD_REQUIRED = ("id", "boundary_id", "token", "type", "direction", "run_time_source", "evidence_state", "signal")

INPUTS = {
    "intake": "stages/intake/intake.json",
    "agent": "stages/architecture/agent.yaml",
    "rules": "stages/rules/rules.json",
    "open_items": "stages/rules/open-items.json",
    "placed": "stages/placement/placed-rules.json",
    "declarations": "stages/placement/declarations.json",
    "boundaries": "stages/boundaries/boundaries.json",
    "fields": "stages/fields/fields.json",
}


class Unreadable(Exception):
    """Input that will not parse (exit 2)."""


class InputMissing(Exception):
    """An input file the run should hold is absent or cannot be opened: a recorded problem (exit 1), not a stop."""


class InputInvalid(Exception):
    pass


def _report(exit_code, problems, counts, notes=()):
    result = {0: "pass", 1: "fail", 2: "error"}[exit_code]
    # notes: the readings taken where input was ambiguous but readable; recorded, never failing
    return {"check": CHECK, "path": CHECK_PATH, "run": REPORT_REL, "exit_code": exit_code, "result": result,
            "problems": problems, "counts": counts, "notes": list(notes), "agent_fallback": FALLBACK}


def _load(run_dir, rel):
    path = os.path.join(run_dir, rel)
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    except (OSError, ValueError) as exc:
        raise InputMissing("%s: %s" % (rel, exc.__class__.__name__))
    try:
        return json.loads(text)
    except ValueError as exc:
        raise Unreadable("%s: %s" % (rel, exc.__class__.__name__))


def _list(doc, rel, *keys):
    if isinstance(doc, list):
        return doc
    if isinstance(doc, dict):
        for k in keys:
            if isinstance(doc.get(k), list):
                return doc[k]
    raise InputInvalid("%s: expected a list or an object with %s" % (rel, " or ".join(keys)))


def _graph(doc):
    g = doc.get("graph", doc) if isinstance(doc, dict) else {}
    nodes = [n.get("id") for n in g.get("nodes") or [] if isinstance(n, dict)]
    edges = [(e.get("source"), e.get("target")) for e in g.get("edges") or [] if isinstance(e, dict)]
    succ = {}
    for a, b in edges:
        succ.setdefault(a, set()).add(b)
    reach = {}
    for n in set(nodes) | set(succ):
        seen, stack = set(), list(succ.get(n, ()))
        while stack:
            m = stack.pop()
            if m not in seen:
                seen.add(m)
                stack.extend(succ.get(m, ()))
        reach[n] = seen
    return reach


def _ends(doc):
    """Node ids and edge ends (edge id -> (source, target)) of one future-state graph."""
    g = doc.get("graph", doc) if isinstance(doc, dict) else {}
    nodes = set(n.get("id") for n in g.get("nodes") or [] if isinstance(n, dict))
    edges = dict((e.get("id"), (e.get("source"), e.get("target"))) for e in g.get("edges") or []
                 if isinstance(e, dict) and e.get("id"))
    return {"nodes": nodes, "edges": edges}


def _split(ref):
    pid, _, node = ref.partition(":")
    return pid, node


class Order(object):
    """Step order across a run.

    Inside one process the future-state graph orders steps. A spanning agent step is one step in every process it
    spans (U9a's `_agent_steps` ownership gives its refs), so its refs are the same step. Steps in different
    processes are ordered by the set map's flow links (`links[]` with `flow` true, every `from` process before every
    `to` process, transitively); processes with no defined order, or caught in a cycle, are incomparable: `unknown`.
    """

    def __init__(self, reach, ownership=None, before=None, qualified_reach=None):
        self.reach = reach
        self.qualified_reach = qualified_reach or {}
        self.ownership = ownership
        self.before = before or {}
        self.cycle_used = set()  # processes in a set-map cycle that an order question actually ran into

    def refs(self, ref):
        info = self.ownership.owned.get(ref) if self.ownership is not None else None
        return list(info.get("refs") or [ref]) if info else [ref]

    def same_step(self, a, b):
        return a == b or b in self.refs(a)

    def _pair(self, a, b):
        """'before' when a is at or before b, else 'unknown' when only incomparable processes relate them, else None."""
        unknown = False
        for x in self.refs(a):
            for y in self.refs(b):
                if y in self.qualified_reach.get(x, ()):
                    return "before"
                (pa, na), (pb, nb) = _split(x), _split(y)
                if pa == pb:
                    if pa in self.reach and (na == nb or nb in self.reach[pa].get(na, set())):
                        return "before"
                elif pb in self.before.get(pa, ()):
                    if pa in self.before.get(pb, ()):
                        # a cycle: the order is unknown, not wrong; the read counts as supplied, noted once
                        self.cycle_used.add(tuple(sorted((pa, pb))))
                    return "before"
                elif pa not in self.before.get(pb, ()):
                    unknown = True
        return "unknown" if unknown else None

    def at_or_before(self, a, b):
        return self.same_step(a, b) or self._pair(a, b) == "before"

    def strictly_before(self, a, b):
        return not self.same_step(a, b) and self.at_or_before(a, b)

    def strictly_after(self, a, b):
        return self.strictly_before(b, a) and not self.at_or_before(a, b)

    def cycle_notes(self):
        """One reading note per two-way pair in the set map's flow links. Never a finding: when reads cross the pair
        they count as supplied (order unknown, not wrong); when none does, the note says no read depends on it."""
        pairs = sorted(set(tuple(sorted((x, y))) for x in self.before for y in self.before[x]
                           if x != y and x in self.before.get(y, ())))
        out = []
        for x, y in pairs:
            if (x, y) in self.cycle_used:
                detail = ("the set map's flow links put %s and %s both before and after each other; reads across "
                          "that pair count as supplied (their order is unknown, not wrong)" % (x, y))
            else:
                detail = "the set map links %s and %s both ways; no read depends on their order" % (x, y)
            out.append({"code": "process-order-cycle", "where": "%s <-> %s" % (x, y), "note": True, "detail": detail})
        return out

    def unknown(self, a, b):
        """True when a and b sit only in processes the set map leaves unordered (never inferred)."""
        if self.same_step(a, b) or self.at_or_before(a, b) or self.at_or_before(b, a):
            return False
        return self._pair(a, b) == "unknown" and self._pair(b, a) == "unknown"


def _self_links(set_map):
    """Processes a set map's flow links link to themselves."""
    out = set()
    links = set_map.get("links") if isinstance(set_map, dict) else None
    for link in links if isinstance(links, list) else []:
        if isinstance(link, dict) and link.get("flow") is True:
            frm, to = link.get("from"), link.get("to")
            frm = [frm] if isinstance(frm, str) else frm if isinstance(frm, list) else []
            to = [to] if isinstance(to, str) else to if isinstance(to, list) else []
            out.update(a for a in frm if isinstance(a, str) and a in to)
    return out


def _process_order(set_map, pids):
    """(process -> set of processes it precedes, processes in a cycle), from the set map's flow links (transitive).
    Two processes that each precede the other sit in a cycle (a process linked to itself is not one: inside a
    process the graph orders steps). A read crossing a cyclic pair counts as supplied and is noted once (Order)."""
    succ = dict((p, set()) for p in pids)
    cycles = set()
    links = set_map.get("links") if isinstance(set_map, dict) else None
    for link in links if isinstance(links, list) else []:
        if not isinstance(link, dict) or link.get("flow") is not True:
            continue
        frm, to = link.get("from"), link.get("to")
        frm = [frm] if isinstance(frm, str) else frm if isinstance(frm, list) else []
        to = [to] if isinstance(to, str) else to if isinstance(to, list) else []
        for a in frm:
            for b in to:
                if isinstance(a, str) and isinstance(b, str) and a != b:
                    succ.setdefault(a, set()).add(b)
                    succ.setdefault(b, set())
    before = {}
    for p in succ:
        seen, stack = set(), list(succ[p])
        while stack:
            q = stack.pop()
            if q not in seen:
                seen.add(q)
                stack.extend(succ.get(q, ()))
        before[p] = seen
        if p in seen:
            cycles.add(p)
            seen.discard(p)   # a process never precedes itself
    return before, cycles


def _agent_step(boundary, direction, ends=None):
    """The qualified agent-side step of a boundary: the side typed `agent` (wg.boundary_direction, the shared rule),
    whichever way the arrow is drawn (a read drawn agent -> system still brings its data to the agent step). None
    when neither or both sides are the agent's: no side is guessed."""
    name, _, _ = wg.boundary_direction(boundary, direction)
    return _side_step(boundary, name, ends) if name else None


def _field_direction(f, b, step, res):
    """(data direction, reading note or None) of one field. The field's own kind decides the direction
    (wg.data_direction); the grants it is keyed on (its own `grant`, or, for an outbound field, the write grant(s)
    of the agent step it leaves from) only add a reading note when their row is unsettled or their kind points the
    other way. A field is never dropped from supply for an unclear grant."""
    base = wg.data_direction(f["direction"])
    notes = [n["detail"] for n in res.step_notes(step)]
    own = f.get("grant")
    if isinstance(own, str) and own.strip():
        keys = [own.strip()]
    elif f["direction"] in OUTBOUND and wg.agent_side(b) == "from":
        keys = res.step_grants(step)
    else:
        keys = []
    for g in keys:
        ident, _ = res.resolve(g)
        if ident.get("type") != wg.GRANT:
            notes.append("keyed on grant %s, which no Write grants row holds; read by the field's own kind" % g)
            continue
        kind = res.direction_kind(g, step)
        if kind is None:
            notes.append("grant %s's row gives no readable write kind; read by the field's own kind" % g)
            continue
        note = wg.direction_note(f["direction"], kind, g)
        if note:
            notes.append(note)
    return base, ("; ".join(notes) if notes else None)


def _side_step(boundary, name, ends=None):
    """The qualified step a boundary side names, qualified from its `edge_ref` when the `ref` is bare.

    An agent side whose bare `ref` is the agent tag rather than a step of the process (U11's derivation) takes
    the step from the edge `edge_ref` names in that process's graph (ends, process id -> _ends): the edge's
    target for `to`, its source for `from`.
    """
    side = boundary.get(name) or {}
    ref = side.get("ref") or ""
    if ":" in ref:
        return ref
    edge_ref = boundary.get("edge_ref") or ""
    if side.get("side") == "agent" and ends and ":" in edge_ref and not edge_ref.startswith("implied:"):
        pid, eid = _split(edge_ref)
        graph = ends.get(pid) or {}
        if ref not in graph.get("nodes", ()) and eid in graph.get("edges", {}):
            source, target = graph["edges"][eid]
            step = target if name == "to" else source
            if step:
                return "%s:%s" % (pid, step)
    if edge_ref.startswith("implied:"):
        parts = [p for p in edge_ref[len("implied:"):].split("->") if ":" in p]
        if side.get("side") == "agent" and parts:
            # an implied boundary (a workspace a step names but the graph draws no node for, as single-process runs
            # do) names its agent step qualified in the ref itself; the bare `ref` is only the agent tag (0486)
            return parts[0]
        pid = _split(parts[0])[0] if parts else ""
    else:
        pid = _split(edge_ref)[0]
    return "%s:%s" % (pid, ref) if pid and ref else None


def _decl_tokens(entries, rel):
    out = {}
    for i, e in enumerate(entries):
        if not isinstance(e, dict) or not isinstance(e.get("token"), str):
            raise InputInvalid("%s: entry %d has no token" % (rel, i))
        out[e["token"]] = e
    return out


def _evaluate(run_dir):
    docs = {k: _load(run_dir, rel) for k, rel in INPUTS.items()}
    problems, notes = [], []

    def problem(defect, where, detail):
        problems.append({"defect": defect, "where": where, "detail": detail})

    def note(code, where, detail):
        notes.append({"code": code, "where": where, "detail": detail, "note": True})

    # Future-state graphs per process.
    reach, ends, graphs, rows, tables_seen = {}, {}, [], [], set()
    for proc in _list(docs["intake"], INPUTS["intake"], "processes"):
        pid, fs = proc.get("process_id"), proc.get("future_state")
        if not pid or not fs:
            raise InputInvalid("%s: a process lacks process_id or future_state" % INPUTS["intake"])
        fs_doc = _load(run_dir, fs)
        reach[pid] = _graph(fs_doc)
        ends[pid] = _ends(fs_doc)
        graphs.append((pid, fs, fs_doc))
        tm = proc.get("transformation_map")
        if isinstance(tm, str) and tm.strip() and tm not in tables_seen:
            tables_seen.add(tm)
            try:
                with open(os.path.join(run_dir, tm), encoding="utf-8") as fh:
                    rows.extend(wg.read_table(fh.read()) or [])
            except OSError:
                problem("input-missing", tm, "the transformation map is not in the run; its Write grants are "
                                             "unknown, so a field keyed on a grant has an unknown direction")
            except wg.TableError as exc:
                problem("write-grants-table-unreadable", tm, str(exc))

    agent = docs["agent"]
    if not isinstance(agent, dict) or not isinstance(agent.get("parts"), list):
        raise InputInvalid("%s: parts missing" % INPUTS["agent"])
    res = wg.Resolver([(pid, wg.with_source(doc, path)) for pid, path, doc in graphs], rows)
    set_steps = agent.get("set_steps") if isinstance(agent.get("set_steps"), list) else None
    ownership = _agent_steps.run_ownership(graphs, set_steps=set_steps)
    owner = {}
    for part in agent["parts"]:
        for ref in part.get("owns_steps") or []:
            owner[ref] = part.get("id")
    # A claim on a spanning step covers every process it spans (U9a), so no mirrored listing is needed.
    claims, _ = _agent_steps.expand_claims(ownership, [(p.get("id"), p.get("owns_steps") or [])
                                                      for p in agent["parts"]])
    for ref, part_ids in claims.items():
        owner.setdefault(ref, part_ids[0])

    # Cross-process order from the set map's flow links; a read crossing a cyclic pair is supplied; each pair is noted once.
    before, cycles, self_links = {}, set(), set()
    sm_rel = docs["intake"].get("set_map") if isinstance(docs["intake"], dict) else None
    if isinstance(sm_rel, str) and sm_rel.strip():
        if os.path.isfile(os.path.join(run_dir, sm_rel)):
            set_map = _load(run_dir, sm_rel)
            before, cycles = _process_order(set_map, list(reach))
            self_links = _self_links(set_map)   # noqa: F841  kept for readers; a self-link is not a cycle
        elif len(reach) > 1:
            problem("set-map-missing", sm_rel, "the intake names a set map that is not in the run; steps in "
                                               "different processes are not ordered")
    # Real edges involving a set step order that step without inventing a
    # process-owned copy. File membership keeps anonymous same-name nodes apart.
    qualified_nodes, qualified_edges = [], []
    for doc, pids, default in _agent_steps.shared_groups(graphs):
        local = _agent_steps.agent_steps(doc, list(reach), default_process=default,
                                        graph_processes=pids)
        by_id = {}
        for ref, node in local.step_nodes.items():
            if ownership.node_at(ref) != node:
                continue
            info = ownership.owned.get(ref)
            home = info["home"] if info else ref
            by_id.setdefault(node["id"], home)
            qualified_nodes.append({"id": home})
        graph = doc.get("graph", doc)
        for edge in graph.get("edges") or []:
            a, b = by_id.get(edge.get("source")), by_id.get(edge.get("target"))
            if a and b and (a.startswith("set:") or b.startswith("set:")
                            or _split(a)[0] == _split(b)[0]):
                qualified_edges.append({"source": a, "target": b})
        if any(ref.startswith("set:") for ref in local.step_nodes):
            ends.setdefault("set", _ends(doc))
    qualified_reach = _graph({"nodes": qualified_nodes, "edges": qualified_edges})
    for pid in reach:
        # The shared file's foreign and set nodes are context, not local aliases.
        local_ids = {ref.split(":", 1)[1] for ref in ownership.step_nodes
                     if ref.startswith(pid + ":")}
        reach[pid] = {nid: targets & local_ids for nid, targets in reach[pid].items()
                      if nid in local_ids}
    order = Order(reach, ownership, before, qualified_reach)
    dispatches = {}
    for h in (agent.get("header") or {}).get("handovers") or []:
        if h.get("kind") == "dispatch":
            dispatches.setdefault((h.get("from"), h.get("to")), set()).update(h.get("edge_refs") or [])

    rules = {r.get("id"): r for r in _list(docs["rules"], INPUTS["rules"], "rules")}
    open_decisions = set(i.get("decision") for i in _list(docs["open_items"], INPUTS["open_items"], "items", "open_items"))
    placed = _list(docs["placed"], INPUTS["placed"], "placed_rules", "rules")
    decl = docs["declarations"] if isinstance(docs["declarations"], dict) else {}
    if not isinstance(decl.get("stand_ins"), list) or not isinstance(decl.get("configuration"), list):
        raise InputInvalid("%s: stand_ins and configuration lists required" % INPUTS["declarations"])
    stand_ins = _decl_tokens(decl["stand_ins"], INPUTS["declarations"])
    config = _decl_tokens(decl["configuration"], INPUTS["declarations"])
    boundaries = {b.get("id"): b for b in _list(docs["boundaries"], INPUTS["boundaries"], "boundaries")}
    fields = _list(docs["fields"], INPUTS["fields"], "fields")

    for token, entry in sorted(stand_ins.items()):
        if entry.get("evidence_state") != "Assumed":
            problem("stand-in-not-assumed", "%s:%s" % (INPUTS["declarations"], token),
                    "a declared stand-in must be stamped Assumed")

    # Placed rules with their expressions.
    prs = []
    placed_ids = set()
    for pr in placed:
        rid = pr.get("rule_id")
        if rid not in rules or not pr.get("process_id") or not pr.get("future_state_step"):
            raise InputInvalid("%s: placed rule %r has no rule in %s or no step" % (INPUTS["placed"], rid, INPUTS["rules"]))
        expression = rules[rid].get("expression")
        if not isinstance(expression, dict) or not isinstance(expression.get("reads"), list):
            raise InputInvalid("%s: rule %r has no expression block with reads" % (INPUTS["rules"], rid))
        # U1's validator; its declared-against-used findings are reported below as predicate binding.
        malformed = [m for m in expr_mod.validate(expression) if not m.startswith(BINDING_MESSAGES)]
        if malformed:
            raise InputInvalid("%s: rule %r expression is malformed: %s" % (INPUTS["rules"], rid, "; ".join(malformed)))
        ref = wg.qualify(pr["process_id"], pr["future_state_step"])
        if ref not in owner:
            problem("step-unowned", rid, "no agent.yaml part owns %s" % ref)
        prs.append({"id": rid, "decision": pr.get("decision"), "ref": ref, "part": owner.get(ref),
                    "state": pr["evidence_state"] if "evidence_state" in pr else rules[rid].get("evidence_state"),
                    "expr": expression,
                    "used": expr_mod.reads(expression), "declared": expression.get("reads"),
                    "sets": sorted((expression.get("sets") or {}).keys())})
        placed_ids.add(rid)

    # Fields, typed by declared direction.
    inbound, handoffs = {}, {}
    for i, f in enumerate(fields):
        missing = [k for k in FIELD_REQUIRED if k not in f]
        if missing or f.get("direction") not in INBOUND + OUTBOUND or f.get("run_time_source") not in SOURCES:
            problem("input-invalid", "%s[%d]" % (INPUTS["fields"], i),
                    "missing %s or bad direction / run_time_source" % ",".join(missing))
            continue
        b = boundaries.get(f["boundary_id"])
        if b and wg.boundary_direction(b, f["direction"])[0] is None:
            problem("agent-side-unknown", "%s:%s" % (INPUTS["fields"], f["id"]),
                    "boundary %s has no side typed agent; the field is seated at no step" % f["boundary_id"])
            continue
        step = _agent_step(b, f["direction"], ends) if b else None
        if not step:
            problem("input-invalid", "%s:%s" % (INPUTS["fields"], f["id"]), "boundary %s not found or has no agent step" % f["boundary_id"])
            continue
        direction, why = _field_direction(f, b, step, res)
        if why:
            note("direction-reading", "%s:%s" % (INPUTS["fields"], f["id"]), why)
        src = f["run_time_source"]
        if src == "rule" and f.get("rule_id") not in placed_ids:
            problem("rule-id-unplaced", f["id"], "run_time_source rule must name a placed rule in rule_id")
        if (src == "stand_in" or f["token"].startswith("stand_in.")) and f["token"] not in stand_ins:
            problem("stand-in-undeclared", f["id"],
                    "token %s is a stand-in or alias with no declarations.json stand_ins entry" % f["token"])
        if src == "configuration" and f["token"] not in config:
            problem("configuration-undeclared", f["id"],
                    "token %s is a configuration value with no declarations.json configuration entry" % f["token"])
        if direction == wg.TO_AGENT and f["direction"] in INBOUND:
            inbound.setdefault(f["token"], []).append((f, step))
        elif f["direction"] == "outbound_handoff":
            handoffs.setdefault(f["token"], []).append(
                {"edge": b.get("edge_ref"), "from": step, "to": _side_step(b, "to", ends)})

    setters = {}
    for r in prs:
        for t in r["sets"]:
            setters.setdefault(t, []).append(r)

    counts = {"placed_rules": len(prs), "fields": len(fields), "reads_checked": 0, "supplied": 0}
    for r in prs:
        # Predicate binding.
        for t in sorted(set(r["used"]) - set(r["declared"])):
            problem("binding-undeclared-read", "%s:%s" % (r["id"], t), "the expression reads a token not in its declared reads")
        for t in sorted(set(r["declared"]) - set(r["used"])):
            problem("binding-unused-read", "%s:%s" % (r["id"], t), "a declared read the expression never uses")
        # Honesty stamp.
        if r["state"] not in STATES:
            problem("honesty-stamp-invalid", r["id"], "evidence state %r is not one of the four states" % (r["state"],))
        elif r["state"] == "needs_more_information" and r["decision"] not in open_decisions:
            problem("honesty-stamp-no-open-item", r["id"], "stamped needs_more_information with no open item for its decision")
        # Supply.
        for t in r["used"]:
            counts["reads_checked"] += 1
            reason = _reason(r, t, order, inbound, setters, handoffs, dispatches, stand_ins, config, owner)
            if reason:
                problem(reason, "%s:%s" % (r["id"], t), "rule at %s reads %s" % (r["ref"], t))
            else:
                counts["supplied"] += 1
    notes.extend(order.cycle_notes())
    notes.extend(dict(n) for n in res.notes)
    return problems, counts, notes


def _carriers(setter, reader, handoffs, order, owner, dispatches):
    """Handoffs that carry a value from the setter's part to the reader's part across a declared dispatch.

    The handoff's boundary must be an edge the dispatch handover names, its `from` step owned by the setting
    part and at or after the setter's step, and its `to` step owned by the reading part.
    """
    edges = dispatches.get((setter["part"], reader["part"]), set())
    return [h for h in handoffs
            if h["edge"] in edges and h["to"]
            and owner.get(h["from"]) == setter["part"] and owner.get(h["to"]) == reader["part"]
            and order.at_or_before(setter["ref"], h["from"])]


def _reason(r, t, order, inbound, setters, handoffs, dispatches, stand_ins, config, owner):
    if t in r["sets"]:
        return "written-by-same-rule"
    others = [s for s in setters.get(t, []) if s is not r]
    if others and any(f["run_time_source"] == "input" for f, _ in inbound.get(t, [])):
        return "input-field-only-computed"
    if t.startswith("case.") or t in config:
        return None
    if t.startswith("stand_in."):
        return None if t in stand_ins and stand_ins[t].get("evidence_state") == "Assumed" else "never-supplied"
    if any(order.at_or_before(step, r["ref"]) for _, step in inbound.get(t, [])):
        return None
    carried_later = []
    for s in others:
        if order.strictly_before(s["ref"], r["ref"]):
            if s["part"] == r["part"]:
                return None
            for h in _carriers(s, r, handoffs.get(t, []), order, owner, dispatches):
                if order.at_or_before(h["to"], r["ref"]):
                    return None
                carried_later.append(h["to"])
    later = [step for _, step in inbound.get(t, [])] + [s["ref"] for s in others] + carried_later
    if any(order.strictly_after(step, r["ref"]) for step in later):
        return "supplied-only-later"
    if any(order.unknown(step, r["ref"]) for step in later):
        return "order-unknown"
    return "never-supplied"


def check(run_dir):
    try:
        if not os.path.isdir(run_dir):
            raise Unreadable("run folder not found")
        problems, counts, notes = _evaluate(run_dir)
    except Unreadable as exc:
        return _report(2, [{"defect": "unreadable-input", "where": str(exc).split(":")[0], "detail": str(exc)}], {})
    except InputMissing as exc:
        return _report(1, [{"defect": "input-missing", "where": str(exc).split(":")[0], "detail": str(exc)}], {})
    except InputInvalid as exc:
        return _report(1, [{"defect": "input-invalid", "where": str(exc).split(":")[0], "detail": str(exc)}], {})
    except (AttributeError, KeyError, TypeError) as exc:
        return _report(1, [{"defect": "input-invalid", "where": "stages", "detail": "unexpected shape: %s" % exc}], {})
    return _report(1 if problems else 0, problems, counts, notes)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Agent Design supply check")
    ap.add_argument("--run", required=True)
    ap.add_argument("--out")
    args = ap.parse_args(argv)
    report = check(args.run)
    out = args.out or os.path.join(args.run, REPORT_REL)
    try:
        os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
        with open(out, "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=2)
            fh.write("\n")
    except OSError:
        pass
    for p in report["problems"]:
        print("%s %s: %s" % (p["defect"], p["where"], p["detail"]))
    print("%s: %s" % (CHECK, report["result"]))
    return report["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
