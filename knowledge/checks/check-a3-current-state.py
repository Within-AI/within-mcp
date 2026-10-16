#!/usr/bin/env python3
"""Current-state A3 checks: the lane standard, the link model and the actors check on a v0.1 current state.

Usage: check-a3-current-state.py --run RUN_ROOT --stage current-state [--file PATH] [--set-map PATH]

Reads `stages/current-state/current-state.input.json` (or `--file`) and `inputs/set-map.json` (or `--set-map`),
both relative to RUN_ROOT unless absolute. Prints one JSON object (Pins §8):
`{"check", "result": "pass"|"fail"|"setup-error", "findings": [{"rule", "where", "detail"}], "flags": [...]}`.
Exit 0 pass, 1 fail, 2 setup error. Flags never fail. Every rule reads fields only; no rule matches words.

Before any rule runs, both inputs are validated against the v0.1 schemas under `formats/` (standard library
only); anything outside that shape is a setup error, never a pass. Some ported rules are therefore also enforced
by the shape gate (layout keys, ai_use mode and check, an Assumed shared-instance link, the AI lane count).

Ported from 0434's A3 check script (lane and link groups; its advisories become flags). Not ported: the metric
group (counts, never pass or fail). Added: the actors check (P37 rows 19, 20, 29).
"""
import argparse
import json
import os
import re
import sys
from collections import Counter

CHECK = "check-a3-current-state"
BAND_LANE = {"people": "Analyst", "ai_and_agents": "Agent", "system_of_record": "System", "external": "External"}
BOX_TYPES = {"read_interaction", "write_interaction"}
# Node types the A3 renderer draws as a work box (task or AI box); pills, events, diamonds, messages and
# context neighbours are not an automation's box.
AUTOMATION_BOX_TYPES = BOX_TYPES | {"ai_use"}
AUTOMATION_BAND = {"rules_automation": "system_of_record", "ai_automation": "ai_and_agents"}
FORMATS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "formats")
SCHEMAS = {"current state": "current-state-graph-v0.1.schema.json", "set map": "set-map-v0.1.schema.json"}


class SetupError(Exception):
    pass


# ---------------- input shape: the v0.1 schemas, read by a standard-library subset of JSON Schema 2020-12.
# A keyword outside the subset is a setup error, so a schema change can never silently loosen the check.
ANNOTATIONS = {"$schema", "$id", "$defs", "title", "description"}
TYPES = {"object": dict, "array": list, "string": str, "boolean": bool, "null": type(None)}


def _is(v, t):
    if t == "integer":
        return isinstance(v, int) and not isinstance(v, bool)
    if t == "number":
        return isinstance(v, (int, float)) and not isinstance(v, bool)
    return isinstance(v, TYPES[t])


def _errors(v, s, root, at):
    """Yield 'where: why' for every way value v breaks schema s."""
    if s is True:
        return
    if s is False:
        yield f"{at}: not allowed"
        return
    unknown = set(s) - ANNOTATIONS - {"type", "$ref", "required", "properties", "additionalProperties", "items",
                                      "const", "enum", "if", "then", "else", "allOf", "anyOf", "not", "pattern",
                                      "minimum", "minItems", "minLength", "maxLength", "contains", "minContains",
                                      "maxContains", "format"}
    if unknown:
        raise SetupError(f"schema keyword(s) {sorted(unknown)} at {at} are not supported by this check")
    if "$ref" in s:
        ref = s["$ref"]
        if not ref.startswith("#/$defs/") or ref[8:] not in root.get("$defs", {}):
            raise SetupError(f"schema $ref {ref!r} does not resolve")
        yield from _errors(v, root["$defs"][ref[8:]], root, at)
    if "type" in s:
        ts = s["type"] if isinstance(s["type"], list) else [s["type"]]
        if not any(_is(v, t) for t in ts):
            yield f"{at}: is {type(v).__name__}, needs {'/'.join(ts)}"
            return
    if "const" in s and not (v == s["const"] and type(v) is type(s["const"])):
        yield f"{at}: {v!r} is not {s['const']!r}"
    if "enum" in s and not any(v == e and type(v) is type(e) for e in s["enum"]):
        yield f"{at}: {v!r} is not one of {s['enum']}"
    if isinstance(v, str):
        if "pattern" in s and not re.search(s["pattern"], v):
            yield f"{at}: {v!r} does not match {s['pattern']}"
        if len(v) < s.get("minLength", 0) or len(v) > s.get("maxLength", len(v)):
            yield f"{at}: length {len(v)} is out of bounds"
        if s.get("format") == "date" and not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", v):
            yield f"{at}: {v!r} is not a date"
    if _is(v, "number") and "minimum" in s and v < s["minimum"]:
        yield f"{at}: {v} is below {s['minimum']}"
    if isinstance(v, dict):
        for k in s.get("required", []):
            if k not in v:
                yield f"{at}: missing {k!r}"
        props = s.get("properties", {})
        for k, x in v.items():
            if k in props:
                yield from _errors(x, props[k], root, f"{at}.{k}")
            elif "additionalProperties" in s:
                yield from _errors(x, s["additionalProperties"], root, f"{at}.{k}")
    if isinstance(v, list):
        if len(v) < s.get("minItems", 0):
            yield f"{at}: {len(v)} items, needs at least {s['minItems']}"
        if "items" in s:
            for i, x in enumerate(v):
                yield from _errors(x, s["items"], root, f"{at}[{i}]")
        if "contains" in s:
            hits = sum(1 for x in v if not any(_errors(x, s["contains"], root, at)))
            if hits < s.get("minContains", 1) or hits > s.get("maxContains", hits):
                yield f"{at}: {hits} items match the required 'contains' shape"
    for sub in s.get("allOf", []):
        yield from _errors(v, sub, root, at)
    if "anyOf" in s and not any(not any(_errors(v, sub, root, at)) for sub in s["anyOf"]):
        yield f"{at}: matches none of the allowed shapes"
    if "not" in s and not any(_errors(v, s["not"], root, at)):
        yield f"{at}: has a shape that is not allowed"
    if "if" in s:
        branch = s.get("then") if not any(_errors(v, s["if"], root, at)) else s.get("else")
        if branch is not None:
            yield from _errors(v, branch, root, at)


def load_schema(what):
    path = os.path.join(FORMATS, SCHEMAS[what])
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError) as exc:
        raise SetupError(f"cannot read the {what} schema {path}: {exc}")


def flow_kinds():
    """The set-map link kinds that are flow links, read from the set-map v0.1 schema: every `links[].kind`
    value except those the schema pins to `flow: false`. Never a hand-typed list, so a new kind cannot drift."""
    schema = load_schema("set map")

    def deref(s):
        ref = s.get("$ref") if isinstance(s, dict) else None
        return schema.get("$defs", {}).get(ref[8:], {}) if isinstance(ref, str) and ref.startswith("#/$defs/") else s

    link = deref((((schema.get("properties") or {}).get("links") or {}).get("items")) or {})
    kinds = set(deref((link.get("properties") or {}).get("kind") or {}).get("enum") or [])
    not_flow = set()
    for rule in link.get("allOf") or []:
        if ((rule.get("then") or {}).get("properties") or {}).get("flow", {}).get("const") is False:
            not_flow |= set((((rule.get("if") or {}).get("properties") or {}).get("kind") or {}).get("enum") or [])
    if not kinds or not not_flow or not not_flow <= kinds or kinds == not_flow:
        raise SetupError("cannot read the flow link kinds from the set map schema (links[].kind enum and flow rule)")
    return kinds - not_flow


def validate(doc, what):
    """Raise SetupError unless doc has the strict v0.1 shape of `what` ('current state' or 'set map')."""
    schema = load_schema(what)
    errs = list(_errors(doc, schema, schema, what))
    # Fields the rules read that the schema leaves open.
    if what == "current state" and not errs:
        errs += [f"current state.graph.starts[{i}]: needs a node id string"
                 for i, x in enumerate(doc["graph"].get("starts") or []) if not isinstance(x, str)]
    if errs:
        more = f" (and {len(errs) - 5} more)" if len(errs) > 5 else ""
        raise SetupError(f"{what} is not valid v0.1: " + "; ".join(errs[:5]) + more)


def norm_step(pid, s):
    """'pid:...:st-001' or 'st-001' (with the box's process) -> (pid, 'st-001')."""
    m = re.search(r"(st-\d+)$", s)
    if not m:
        return (pid, s)
    head = s.split(":")[0] if ":" in s else pid
    return (head, m.group(1))


def proc(n):
    return n.get("process_id") or (n.get("subtask") or {}).get("process_id")


def band_of(n, lanes):
    lane = lanes.get(n.get("lane_id"))
    return lane.get("band") if lane else None


def check(cs, sm):
    """Return (findings, flags) for a current-state bundle and its set map (or None).

    Raises SetupError, before any rule runs, when either input is outside the strict v0.1 shape."""
    validate(cs, "current state")
    if sm is not None:
        validate(sm, "set map")
    findings, flags = [], []

    def fail(rule, where, detail=""):
        findings.append({"rule": rule, "where": where, "detail": detail})

    def flag(rule, where, detail=""):
        flags.append({"rule": rule, "where": where, "detail": detail})

    g = cs.get("graph") or {}
    nodes, edges = g.get("nodes") or [], g.get("edges") or []
    byid = {n["id"]: n for n in nodes if "id" in n}
    lanes = {l["id"]: l for l in g.get("lanes") or [] if "id" in l}
    inset = set((cs.get("process") or {}).get("process_ids") or [])

    # ---------------- lane standard
    if not lanes:
        fail("lanes-present", "graph.lanes", "no A3 lanes")
    for n in nodes:
        if n.get("lane_id") not in lanes:
            fail("node-in-a3-lane", n["id"], f"lane_id {n.get('lane_id')!r} is not a lane")
        elif BAND_LANE.get(lanes[n["lane_id"]].get("band")) != n.get("lane"):
            fail("coarse-lane-matches-band", n["id"],
                 f"lane {n.get('lane')!r} in a {lanes[n['lane_id']].get('band')} lane")
    boxes = [n for n in nodes if n.get("type") in BOX_TYPES]
    for n in boxes:
        st = n.get("subtask") or {}
        if not (st.get("step_range") and st.get("step_ids") and proc(n) in inset):
            fail("box-has-steps-and-process", n["id"], "needs subtask.step_range, subtask.step_ids and an in-set process")
    seen, badge = {}, {}
    for n in boxes:
        for s in (n.get("subtask") or {}).get("step_ids") or []:
            k = norm_step(proc(n), s)
            if k in seen and seen[k] != n["id"]:
                fail("one-box-per-subtask", n["id"], f"{k[0]}:{k[1]} also on {seen[k]}")
            seen[k] = n["id"]
    for n in nodes:
        for r in n.get("recovery") or []:
            for s in r.get("step_ids") or []:
                k = norm_step(proc(n), s)
                if k in seen and seen[k] != n["id"]:
                    fail("one-box-per-subtask", n["id"], f"{k[0]}:{k[1]} is a badge here and a box step on {seen[k]}")
                badge[k] = n["id"]
    for n in nodes:
        if n.get("type") == "decision" and band_of(n, lanes) == "ai_and_agents":
            fail("no-decision-in-ai-lane", n["id"], "a current state has no decision in the AI lane")
    ai_l = [l for l in lanes.values() if l.get("band") == "ai_and_agents"]
    ai_n = [n for n in nodes if n.get("type") == "ai_use"]
    if len(ai_l) != 1:
        fail("one-ai-lane", "graph.lanes", f"{len(ai_l)} AI and agents lanes")
    if not ai_n and not any(l.get("none_found") for l in ai_l):
        fail("ai-lane-filled-or-none-found", "graph.lanes", "no AI use and no lane says none_found")
    # asks_returns is two-way: the AI use counts as linked only when the edge's other end is a person's box.
    person_boxes = {n["id"] for n in boxes if band_of(n, lanes) == "people"}
    linked = {y for e in edges if e.get("kind") == "asks_returns"
              for x, y in ((e.get("source"), e.get("target")), (e.get("target"), e.get("source"))) if x in person_boxes}
    for n in ai_n:
        u = n.get("ai_use") or {}
        if not (u.get("mode") and u.get("checked_by")):
            fail("ai-use-mode-and-check", n["id"], "ai_use needs mode and checked_by")
        if n["id"] not in linked:
            fail("ai-use-asks-returns", n["id"], "no asks_returns edge to a person's box")
        if n.get("lane_id") in lanes and band_of(n, lanes) != "ai_and_agents":
            fail("ai-use-in-ai-lane", n["id"], f"sits in a {band_of(n, lanes)} lane")
    for e in edges:
        s, t = byid.get(e.get("source")), byid.get(e.get("target"))
        if not s or not t or e.get("context"):
            continue
        ps, pt = proc(s), proc(t)
        if ps and pt and ps != pt and ps in inset and pt in inset:
            if e.get("kind") != "handoff" or not e.get("evidence_state") or (
                    e.get("evidence_state") == "Observed" and not e.get("source_refs")):
                fail("cross-process-handoff", e.get("id"), f"{ps} -> {pt} skips a state and is no evidenced handoff edge")
    deg = Counter(x for e in edges for x in (e.get("source"), e.get("target")))
    ctx = [n for n in nodes if n.get("type") == "context_neighbour"]
    for n in ctx:
        if not deg[n["id"]]:
            fail("context-neighbour-attached", n["id"], "no edge")
    for e in edges:
        if "context_neighbour" in (byid.get(e.get("source"), {}).get("type"), byid.get(e.get("target"), {}).get("type")) \
                and not e.get("context"):
            fail("context-edge-marked", e.get("id"), "edge to a context neighbour is not marked context")
    sp = [n for n in nodes if n.get("type") == "object_state"]
    for n in sp:
        if n.get("lane_id") in lanes and band_of(n, lanes) not in ("system_of_record", "external"):
            fail("state-pill-lane", n["id"], f"state pill in a {band_of(n, lanes)} lane")
    frames = {f.get("process_id") for f in g.get("process_frames") or []}
    if frames != inset:
        fail("one-frame-per-process", "graph.process_frames", f"differs on {sorted(map(str, inset ^ frames))}")
    for n in nodes:
        if any(k in n for k in ("col", "row", "x", "y")):
            fail("no-layout-positions", n["id"], "carries a layout position")

    if not sm:
        fail("set-map-present", "inputs/set-map.json", "no set map: link model and actors checks cannot run")
        return findings, flags

    # ---------------- link model
    sm_states = {s["id"]: s for s in sm.get("object_states") or []}
    sm_links = {l["id"]: l for l in sm.get("links") or []}
    sm_ops = sm.get("open_points") or []
    about = {x for o in sm_ops for x in o.get("about") or []}
    sm_procs = {p["process_id"] for p in sm.get("processes") or []}
    actors = {a["id"]: a for a in sm.get("actors") or []}
    if sm_procs != inset:
        fail("set-map-processes-match", "set_map.processes", f"differs on {sorted(sm_procs ^ inset)}")
    flow = flow_kinds()
    flowp = {p for l in sm_links.values() if l.get("kind") in flow for p in (l.get("from") or []) + (l.get("to") or [])}
    for p in sorted(sm_procs):
        if p not in flowp and p not in about:
            fail("process-has-flow-link", p, "no flow link and no open point saying why")
    overl = {l.get("state") for l in sm_links.values() if l.get("kind") == "overlap" and l.get("state")}
    varst = {l.get("state") for l in sm_links.values() if l.get("kind") == "variant_entry"}
    for s in sm_states.values():
        if len(s.get("moved_in_by") or []) != 1 and s["id"] not in about | overl | varst:
            fail("state-has-one-mover", s["id"], f"{len(s.get('moved_in_by') or [])} movers, no overlap or variant entry")
    starts = set(g.get("starts") or [])
    decs = [n["id"] for n in nodes if n.get("type") == "decision"]
    for l in sm_links.values():
        if l.get("kind") != "variant_entry":
            continue
        vb = {n["id"] for n in boxes if proc(n) in set(l.get("from") or [])}
        hit = [d for d in decs if len({proc(byid[e["target"]]) for e in edges
                                       if e.get("source") == d and e.get("target") in vb} - {None}) >= 2]
        if len(starts) != 1 or not hit:
            fail("variant-entry-shared-start", l["id"], f"starts={sorted(starts)} deciding diamonds={hit}")
        if not l.get("routing_rule") and l.get("evidence_state") not in ("needs_more_information", "Contradictory") \
                and not l.get("open_point_ids"):
            fail("variant-entry-rule-or-open-point", l["id"], "no routing rule and no open point")
    for l in sm_links.values():
        if l.get("evidence_level") == "shared_instances" and l.get("evidence_state") == "Assumed":
            fail("shared-instances-not-assumed", l["id"], "set-map link backed by shared instances is Assumed")
    for e in edges:
        l = sm_links.get(e.get("link_ref"))
        if l and l.get("evidence_level") == "shared_instances" and e.get("evidence_state") == "Assumed":
            fail("shared-instances-not-assumed", e.get("id"), f"edge on shared-instance link {l['id']} is Assumed")
    cont_from = {l.get("state") for l in sm_links.values() if l.get("kind") in ("lifecycle_continuation", "re_entry")}
    attached = {x for c in sm.get("context_neighbours") or [] for x in c.get("attach_to") or []}
    later = {(s.get("object"), s.get("order")) for s in sm_states.values()}
    for s in sm_states.values():
        if s.get("waiting_on") and not s.get("terminal") and s["id"] not in cont_from | attached | about \
                and not any(o == s.get("object") and (r or 0) > (s.get("order") or 0) for o, r in later):
            fail("waiting-state-resolved", s["id"], "nothing moves the work object on from this wait")
    cns = sm.get("context_neighbours") or []
    drawn = {(n.get("neighbour") or {}).get("set_map_ref") for n in ctx} | {(n.get("neighbour") or {}).get("process_id") for n in ctx}
    for c in cns:
        if c["id"] not in drawn and c.get("process_id") not in drawn:
            fail("context-neighbour-drawn", c["id"], "set-map context neighbour is not drawn")
    ids = set(sm_states) | set(sm_links) | {c["id"] for c in cns} | {o["id"] for o in sm_ops} \
        | {w["id"] for w in sm.get("work_objects") or []} | sm_procs | set(actors)
    refs = [(e.get("id"), e["link_ref"]) for e in edges if e.get("link_ref")] \
        + [(n["id"], (n.get("object_state") or {}).get("state")) for n in sp] \
        + [(n["id"], (n.get("neighbour") or {}).get("set_map_ref")) for n in ctx if (n.get("neighbour") or {}).get("set_map_ref")] \
        + [(w.get("id"), w.get("set_map_ref")) for w in g.get("work_objects") or [] if w.get("set_map_ref")]
    for where, r in refs:
        if r not in ids:
            fail("set-map-refs-resolve", where, f"{r!r} is not in the set map")
    used_states = {(n.get("object_state") or {}).get("state") for n in sp}
    for s in sm_states:
        if s not in used_states:
            flag("state-drawn-as-pill", s, "set-map state not drawn")
    used_links = {e.get("link_ref") for e in edges}
    for lid, l in sm_links.items():
        if l.get("kind") in flow and lid not in used_links:
            flag("flow-link-drawn", lid, "set-map flow link drawn by no edge")

    # ---------------- actors (P37 rows 1, 19, 20, 29)
    for where, a in [(n["id"], n.get("actor")) for n in nodes if n.get("actor")] + \
                    [(l["id"], l.get("actor")) for l in lanes.values() if l.get("actor")]:
        if a not in actors:
            fail("actor-resolves", where, f"actor {a!r} is not in set map actors[]")
    movers = {m for s in sm_states.values() for m in s.get("moved_in_by") or []}
    for aid, a in actors.items():
        kind = a.get("kind")
        if kind in AUTOMATION_BAND and (aid in movers or a.get("writes_objects")):
            drawn_by = [n for n in nodes if n.get("actor") == aid and n.get("type") in AUTOMATION_BOX_TYPES]
            if not drawn_by:
                fail("automation-drawn-as-box", aid, f"{kind} moves the work object and has no box")
            for n in drawn_by:
                if band_of(n, lanes) != AUTOMATION_BAND[kind]:
                    fail("automation-band", n["id"], f"{kind} box sits in a {band_of(n, lanes)} lane")
        if kind == "external_party":
            held = [l for l in lanes.values() if l.get("actor") == aid]
            if not held:
                fail("outside-party-has-lane", aid, "outside party has no lane")
            for l in held:
                if l.get("band") != "external":
                    fail("outside-party-has-lane", l["id"], f"lane for {aid} is in the {l.get('band')} band")
    return findings, flags


def load(path, what):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError) as exc:
        raise SetupError(f"cannot read {what} {path}: {exc}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--run", required=True)
    ap.add_argument("--stage", required=True)
    ap.add_argument("--file")
    ap.add_argument("--set-map")
    a = ap.parse_args(argv)
    out = {"check": CHECK, "result": "setup-error", "findings": [], "flags": []}
    try:
        if a.stage != "current-state":
            raise SetupError(f"stage {a.stage!r}: this check reads the current-state stage only")
        if not os.path.isdir(a.run):
            raise SetupError(f"run root {a.run} is not a folder")
        path = os.path.join(a.run, a.file or "stages/current-state/current-state.input.json")
        cs = load(path, "current state")
        if not isinstance(cs, dict) or cs.get("variant") != "current-state":
            raise SetupError(f"{path} is not a current state (variant {cs.get('variant') if isinstance(cs, dict) else None!r})")
        sm_path = os.path.join(a.run, a.set_map or "inputs/set-map.json")
        sm = load(sm_path, "set map") if os.path.isfile(sm_path) else None
        out["findings"], out["flags"] = check(cs, sm)
    except Exception as exc:  # a shape the validation missed is a setup error too, never a pass
        detail = str(exc) if isinstance(exc, SetupError) else f"input the rules cannot read: {type(exc).__name__}: {exc}"
        out.update(result="setup-error", findings=[{"rule": "setup", "where": a.run, "detail": detail}], flags=[])
        print(json.dumps(out, indent=1))
        return 2
    out["result"] = "fail" if out["findings"] else "pass"
    print(json.dumps(out, indent=1))
    return 1 if out["findings"] else 0


if __name__ == "__main__":
    sys.exit(main())
