"""Versioned, stdlib checks for source intake, frozen snapshots and workflow bundles.

Checks prove structure, identity and declared coverage, never semantic truth. Source
shape is selected explicitly with JSON pointers into preserved raw pulls. No network.

Workflow bundles use schema version 2 (see reference/workflow-diagrams.md, Format).
The format never refuses an agent's shape: unfamiliar node types, lanes, link kinds,
treatments, surface kinds, table kinds and issue kinds pass and are drawn generically.
Hard failures exist only where the output would be broken or wrong (a dangling link,
a duplicate id, two statements in one graph that contradict each other, a stale or
missing pin, a value outside the four ratified evidence states) or where a required
foundational input is missing (the future state's link to its current state). Notes
point at unfamiliar or incomplete content; they never make a check fail.
"""
import argparse
import copy
import hashlib
import json
import math
import os
import re
from pathlib import Path, PurePosixPath
import re
import sys
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from _demo_policy import OBSERVATION_LIMIT

SNAPSHOT_VERSION = 1
WORKFLOW_VERSION = 2
STATES = {"Observed", "Assumed", "Contradictory", "needs_more_information"}
ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$")
CURRENT, FUTURE = "current-state", "future-state"
KNOWN_VARIANTS = (CURRENT, FUTURE)
PLUGIN_SHAPE = "workflow-bundle"
RENDERER_ID = "factory-workflow-bands-v3"
A3_RENDERER_ID = "factory-workflow-a3-v1"
RENDERER_IDS = (RENDERER_ID, A3_RENDERER_ID)

# Known values: the renderer draws these specially and the notes compare against their
# usual lanes. No check tests membership in them; anything else is drawn generically.
# Wherever `Agent` is named below, any agent lane (`Agent` or `Agent: <name>`) counts.
KNOWN_LANES = ("Analyst", "Agent", "System")
AGENT_LANE_PREFIX = "Agent: "


def is_agent_lane(lane):
    """True for an agent lane: exactly `Agent`, or `Agent: <name>` with a non-empty name.
    `Agentic`, `Agent:` and `Agent: ` (no name) are not agent lanes."""
    if lane == "Agent":
        return True
    return (isinstance(lane, str) and lane.startswith(AGENT_LANE_PREFIX)
            and bool(lane[len(AGENT_LANE_PREFIX):].strip()))


def lane_kind(lane):
    """The known lane a lane is compared against: `Agent` for every agent lane, else the lane itself."""
    return "Agent" if is_agent_lane(lane) else lane


KNOWN_NODE_TYPES = {
    "read_interaction": ("Analyst",), "write_interaction": ("Analyst",),
    "analyst_reconciliation": ("Analyst",), "analyst_transfer": ("Analyst",),
    "decision": ("Analyst", "Agent"),
    "agent_read": ("Agent",), "agent_reconciliation": ("Agent",), "agent_stage": ("Agent",),
    "system_surface": ("System",), "system_result": ("System",),
}
KNOWN_EDGE_KINDS = ("sequence", "then", "else", "exception", "loop", "handoff", "inbound", "read_access", "write")
KNOWN_TREATMENTS = {"person-kept": ("Analyst",), "agent-taken": ("Agent",),
                    "split": ("Analyst", "Agent"), "system-surface": ("System",)}
KNOWN_SURFACE_KINDS = ("system_of_record", "channel", "review_surface")
KNOWN_TABLE_KINDS = ("consultation", "selection")
INTAKE_ISSUE_KINDS = ("missing", "inaccessible", "contradiction", "policy", "coverage")
KNOWN_GRAPH_ISSUE_KINDS = INTAKE_ISSUE_KINDS + ("open-question",)
BRANCH_KINDS = ("then", "else", "exception")
WORKFLOW_VERSION_MESSAGE = ("unsupported workflow schema_version (expected 2; version-1 bundles "
                            "need re-authoring in the v2 format)")
OTHER_SHAPE_NOTE = "Current state in a different shape; carried items checked by review."

# Systems-coverage tiers. The inventory itself is data-derived; these short lists only relax how a
# cut must be justified. Every system still needs a keep or cut decision in the ledger. An agent
# runtime or incidental UI/utility may be cut with a short reason; everything else is
# "operational" and may not be cut with a throwaway reason.
SYSTEM_RUNTIME = {"claude", "codex", "gpt", "chatgpt", "gemini", "anthropic", "openai", "llm"}
SYSTEM_INCIDENTAL = {"preview", "finder", "macos finder", "google search", "search",
                     "google chrome", "chrome", "safari", "browser", "google calendar",
                     "calendar", "google meet", "zoom", "screenshot", "quicktime"}
SYSTEM_BOILERPLATE = {"", "n/a", "na", "none", "not relevant", "not needed", "irrelevant",
                      "not applicable", "no", "-", "tbd"}


def system_name(name):
    """Display form of a system name: surrounding and repeated whitespace collapsed."""
    return " ".join(str(name).split())


def system_key(name):
    """Comparison key for a system name, so "Gmail", "gmail" and " Gmail " are one system."""
    return system_name(name).casefold()


def reason_key(text):
    """Comparison key for a cut rationale: punctuation and repeated whitespace ignored."""
    return " ".join(re.sub(r"[^\w\s/]", " ", str(text)).split()).casefold()


def system_tier(name):
    key = system_key(name)
    if key in SYSTEM_RUNTIME:
        return "runtime"
    if key in SYSTEM_INCIDENTAL:
        return "incidental"
    return "operational"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def read(path, version=SNAPSHOT_VERSION):
    with open(path, encoding="utf-8") as f:
        value = json.load(f)
    require(isinstance(value, dict), "%s: expected JSON object" % path)
    if version == WORKFLOW_VERSION:
        require(value.get("schema_version") == WORKFLOW_VERSION, WORKFLOW_VERSION_MESSAGE)
    else:
        require(value.get("schema_version") == version, "unsupported or missing schema_version")
    return value


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


REFERENCE_READINGS = []


def reference_readings(clear=False):
    """Return independent evidence records; optionally drain the process-local buffer."""
    records = copy.deepcopy(REFERENCE_READINGS)
    if clear:
        REFERENCE_READINGS.clear()
    return records


def reference(base, ref):
    require(isinstance(ref, dict) and ref.get("path") and ref.get("sha256"), "missing path/SHA reference")
    path = (Path(base).parent / ref["path"]).resolve()
    carrier = Path(base).resolve()
    declared = PurePosixPath(ref["path"])
    alternate = None
    # This is the inspected stage layout, not a general run-root search.
    if (not declared.is_absolute() and declared.parts and declared.parts[0] == "inputs"
            and ".." not in declared.parts and carrier.parent.name in (CURRENT, FUTURE)
            and carrier.parent.parent.name == "stages"):
        run = carrier.parent.parent.parent
        inputs = run / "inputs"
        if inputs.is_dir():
            candidate = (run / declared).resolve()
            if candidate.is_relative_to(inputs.resolve()) and candidate != path:
                alternate = candidate

    def reading(candidate, interpretation):
        actual = sha(candidate) if candidate.is_file() else None
        return {"interpretation": interpretation, "path": str(candidate),
                "status": ("missing" if actual is None else
                           "match" if actual == ref["sha256"] else "stale"),
                "actual_sha256": actual}

    primary = reading(path, "carrier")
    selected = "carrier" if primary["status"] == "match" else None
    if alternate is not None:
        secondary = reading(alternate, "run-root")
        if selected is None and secondary["status"] == "match":
            selected = "run-root"
        record = {"schema": "workflow-reference-reading/1", "carrier": str(carrier),
                  "declared_path": ref["path"], "declared_sha256": ref["sha256"],
                  "candidates": [primary, secondary], "selected": selected,
                  "reason": ("hash-valid carrier takes precedence" if selected == "carrier" else
                             "same-run input matches declared hash" if selected == "run-root" else
                             "neither interpretation matches declared hash")}
        REFERENCE_READINGS.append(record)
        if selected != "carrier" or secondary["status"] != "missing":
            print("REFERENCE_READING " + json.dumps(record), file=sys.stderr)
    if selected == "run-root":
        return alternate
    require(primary["status"] != "missing", "missing referenced file: %s" % path)
    require(primary["status"] == "match", "stale referenced file: %s" % path)
    return path


def rows(value, label, nonempty=False):
    require(isinstance(value, list), "%s must be a list" % label)
    require(not nonempty or bool(value), "%s must not be empty" % label)
    require(all(isinstance(r, dict) for r in value), "%s must contain objects" % label)
    return value


def index(value, label, nonempty=False):
    value = rows(value, label, nonempty)
    require(all(isinstance(r.get("id"), str) and r["id"].strip() for r in value), "%s requires IDs" % label)
    result = {r["id"]: r for r in value}
    require(len(result) == len(value), "%s has duplicate IDs" % label)
    return result


def pointer(value, path):
    require(isinstance(path, str) and (path == "" or path.startswith("/")), "invalid JSON pointer")
    for part in path.split("/")[1:]:
        key = part.replace("~1", "/").replace("~0", "~")
        value = value[int(key)] if isinstance(value, list) else value[key]
    return value


def issues(value, sources):
    """Intake/snapshot issues: the five intake kinds (snapshot checks are unchanged)."""
    result = index(value, "issues")
    for key, item in result.items():
        require(item.get("kind") in INTAKE_ISSUE_KINDS, "invalid issue kind: " + key)
        require(item.get("status") == "open" and item.get("reason"), "issues must remain explicitly open: " + key)
        refs = item.get("source_ids")
        require(isinstance(refs, list) and set(refs) <= set(sources), "unresolved issue references: " + key)
        if item["kind"] == "contradiction":
            require(len(set(refs)) >= 2, "contradiction needs both source claims: " + key)
    return result


def at(value, ptr, sid, what):
    """Resolve a mapped pointer for one source, naming the source id and pointer when it fails."""
    try:
        return pointer(value, ptr)
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise ValueError("source %s: mapping %s pointer %s did not resolve (%s: %s)" % (sid, what, ptr, type(exc).__name__, exc)) from None


def intake(path):
    doc = read(path)
    require(doc.get("kind") == "snapshot-intake", "expected snapshot-intake")
    scope = doc.get("scope", {})
    require(isinstance(scope, dict) and scope.get("process_ids") and scope.get("cutoff_at"), "scope needs process_ids and cutoff_at")
    from datetime import datetime
    cutoff = datetime.fromisoformat(scope["cutoff_at"].replace("Z", "+00:00"))
    require(cutoff.tzinfo is not None, "cutoff_at needs a timezone")
    process_ids = scope["process_ids"]
    require(isinstance(process_ids, list) and all(isinstance(p, str) and p for p in process_ids), "invalid process_ids")
    sources = index(doc.get("sources"), "sources", True)
    required = doc.get("required_source_ids")
    require(isinstance(required, list) and required and len(set(required)) == len(required) and set(required) <= set(sources), "required source inventory is missing or unresolved")
    opens = issues(doc.get("issues"), sources)
    demo = scope.get("mode") == "demo"
    if demo:
        require(scope.get("observation_limit") == OBSERVATION_LIMIT,
                "Demo intake requires observation_limit: 5")
    observation_counts = {}
    steps, profiles, systems = [], {}, {}
    for sid, source in sources.items():
        require(source.get("process_id") in process_ids, "source outside process scope: " + sid)
        status = source.get("status")
        if demo:
            require(source.get("collection_kind") != "process-history", "Demo does not pull version history")
        require(status in {"included", "missing", "inaccessible", "excluded"}, "invalid source status: " + sid)
        if status != "included":
            require(source.get("reason"), "omission needs rationale: " + sid)
            require(any(sid in i["source_ids"] for i in opens.values()), "omission needs an open issue: " + sid)
            require(sid not in required, "essential source unavailable: " + sid)
            continue
        raw_path = reference(path, source)
        raw = json.loads(raw_path.read_text(encoding="utf-8"))
        require(isinstance(raw, dict), "raw pull must be an object: " + sid)
        mapping = source.get("mapping")
        require(isinstance(mapping, dict), "source requires a mapping object: " + sid)
        # A raw pull that carries its process id is compared through the pointer. A pull
        # that carries none (a collection such as the observations list) is bound by the
        # source's own stated process_id, already checked against the scope above.
        if "process_id" in mapping:
            require(at(raw, mapping["process_id"], sid, "process_id") == source["process_id"], "raw source belongs to another process: " + sid)
        if "timestamp" in mapping:
            captured = datetime.fromisoformat(at(raw, mapping["timestamp"], sid, "timestamp").replace("Z", "+00:00"))
            require(captured.tzinfo is not None and captured <= cutoff, "source after cutoff: " + sid)
        if "items" in mapping:
            items = at(raw, mapping["items"], sid, "items")
            require(isinstance(items, list), "mapped items must be a list")
            require("total" in mapping, "collection mapping needs total to detect incomplete pulls")
            total = at(raw, mapping["total"], sid, "total")
            require(type(total) is int and total >= len(items), "invalid source total")
            sampled = demo and source.get("collection_kind") == "observations"
            if sampled:
                selection = source.get("selection", {})
                require(selection.get("limit") == OBSERVATION_LIMIT and selection.get("offset") == 0
                        and selection.get("order") == "newest-first" and selection.get("recency_evidence"),
                        "Demo observations need a bounded newest-first request and recency evidence")
                require(len(items) == min(total, OBSERVATION_LIMIT), "Demo requires the latest five observations, or all if fewer exist")
                require("item_id" in mapping and "observed_at" in mapping,
                        "Demo observations need ID and timestamp pointers")
                ids = [at(item, mapping["item_id"], sid, "item_id") for item in items]
                require(all(isinstance(i, str) and i for i in ids) and len(set(ids)) == len(ids),
                        "Demo observation IDs must be unique")
                times = [datetime.fromisoformat(at(item, mapping["observed_at"], sid, "observed_at").replace("Z", "+00:00")) for item in items]
                require(all(t.tzinfo is not None and t <= cutoff for t in times), "invalid Demo observation timestamp")
                require(times == sorted(times, reverse=True), "Demo observations must be newest first")
                pid = source["process_id"]
                observation_counts[pid] = observation_counts.get(pid, 0) + len(items)
                require(observation_counts[pid] <= OBSERVATION_LIMIT, "Demo observation cap is per process, not per page/version")
            complete = total == len(items) and ("next" not in mapping or not at(raw, mapping["next"], sid, "next"))
            if not complete:
                require(sampled or sid not in required, "essential source pagination incomplete: " + sid)
                require(any(i["kind"] == "coverage" and sid in i["source_ids"] for i in opens.values()), "partial pull needs coverage issue: " + sid)
        elif "item" in mapping:
            # One observation cited on its own (collection_kind "observation"): an item of its parent collection's own included file,
            # same process, inside the parent's items (so a Demo alias can only name an item of the checked sample), before the cutoff.
            require(source.get("collection_kind") == "observation", "a single-item source must be an observation: " + sid)
            parent = sources.get(source.get("parent"))
            require(isinstance(parent, dict) and parent.get("collection_kind") == "observations" and parent.get("status") == "included"
                    and parent.get("process_id") == source["process_id"] and parent.get("path") == source.get("path")
                    and parent.get("sha256") == source.get("sha256"), "an observation source needs its included collection, same process and file, as parent: " + sid)
            items_ptr = (parent.get("mapping") or {}).get("items")
            require(isinstance(items_ptr, str) and re.fullmatch(re.escape(items_ptr) + r"/\d+", str(mapping["item"])),
                    "an observation source must point at one item of its parent's items: " + sid)
            item = at(raw, mapping["item"], sid, "item")
            require(isinstance(item, dict), "mapped item must be an object: " + sid)
            require(all(isinstance(mapping.get(k), str) and mapping[k].startswith(mapping["item"] + "/") for k in ("item_id", "observed_at")),
                    "an observation source reads its id and time from its own item: " + sid)
            require(sid.endswith("observation:" + str(at(raw, mapping["item_id"], sid, "item_id"))), "an observation source id does not match its item: " + sid)
            observed = datetime.fromisoformat(str(at(raw, mapping["observed_at"], sid, "observed_at")).replace("Z", "+00:00"))
            require(observed.tzinfo is not None and observed <= cutoff, "observation after cutoff: " + sid)
        elif demo and source.get("collection_kind") == "observations":
            require(False, "Demo observation source requires items and total pointers")
        source_steps = []
        if "steps" in mapping:
            source_steps = at(raw, mapping["steps"], sid, "steps")
            require(isinstance(source_steps, list), "steps pointer must resolve to a list")
            require("step_id" in mapping, "steps mapping requires step_id pointer")
            ids = [at(row, mapping["step_id"], sid, "step_id") for row in source_steps]
            require(all(isinstance(k, str) and k for k in ids) and len(set(ids)) == len(ids), "invalid/duplicate source step IDs")
            steps.extend({"id": sid + ":" + k, "source_id": sid, "pointer": mapping["steps"] + "/" + str(i)} for i, k in enumerate(ids))
        if "systems" in mapping:
            src_systems = at(raw, mapping["systems"], sid, "systems")
            require(isinstance(src_systems, list) and all(isinstance(s, str) and s.strip() for s in src_systems),
                    "systems pointer must resolve to a list of system names: " + sid)
            for name in src_systems:
                systems.setdefault(system_key(name), system_name(name))
        if "step_tools" in mapping:
            require(bool(source_steps), "step_tools pointer needs a non-empty steps list: " + sid)
            resolved = 0
            for i, row in enumerate(source_steps):
                try:
                    tools = pointer(row, mapping["step_tools"])
                except (KeyError, IndexError, TypeError):
                    continue  # this step records no tools
                resolved += 1
                # Same rule as the systems pointer: a tool that is not a plain name is an error,
                # never skipped, so no tool can drop out of the inventory unnoticed.
                require(isinstance(tools, list) and all(isinstance(t, str) and t.strip() for t in tools),
                        "step_tools pointer must resolve to a list of system names: %s step %d" % (sid, i))
                for name in tools:
                    systems.setdefault(system_key(name), system_name(name))
            require(resolved > 0, "step_tools pointer resolves on no step: " + sid)
        profiles[sid] = {"fields": sorted(raw), "field_types": {k: type(v).__name__ for k, v in sorted(raw.items())}, "step_count": len(source_steps)}
    return doc, steps, profiles, sorted(systems.values(), key=system_key)


def frozen(path):
    doc = read(path)
    require(doc.get("kind") == "snapshot", "expected snapshot")
    upstream = reference(path, doc.get("intake"))
    original, steps, profiles, systems = intake(upstream)
    for key in ("scope", "required_source_ids", "issues"):
        require(doc.get(key) == original.get(key), "snapshot changed intake " + key)
    require(doc.get("steps") == steps, "snapshot step inventory differs from raw evidence")
    require(doc.get("systems", []) == systems, "snapshot systems inventory differs from raw evidence")
    require(doc.get("profiles") == profiles, "snapshot shape profile differs from raw evidence")
    # Resolve source paths relative to intake, never rewrite raw pulls or their hashes.
    require(doc.get("sources") == original["sources"], "snapshot source inventory changed")
    return doc, upstream


class Checked:
    """Result of a workflow check: the bundle and what was derived from it.

    `printed` holds the notes this particular check prints after its PASS line."""

    def __init__(self, **values):
        self.printed = []
        self.__dict__.update(values)


def id_list(value):
    """A field that may hold one id or a list of ids, as a list (None when malformed)."""
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return value
    return None


def nonempty_text(value):
    return isinstance(value, str) and bool(value.strip())


def node_ids(graph):
    return [n.get("id") for n in graph.get("nodes") or [] if isinstance(n, dict)]


def topology(graph):
    """Routing classes, starts, outcomes, return paths and depth columns of a graph.

    Pure and deterministic: iteration follows graph order only. Routing class is chosen
    by an edge's endpoints, never by its kind: an edge whose target is a system rail is
    a drop, one whose source is a rail is a rise, one between two rails is a rail link,
    a self-edge is a self-loop, and everything else is a step link."""
    nodes = [n for n in graph.get("nodes") or [] if isinstance(n, dict)]
    order = {n["id"]: i for i, n in enumerate(nodes)}
    by_id = {n["id"]: n for n in nodes}
    rails = [n["id"] for n in nodes if n.get("type") == "system_surface"]
    rail_set = set(rails)
    act = [n["id"] for n in nodes if n["id"] not in rail_set]
    edges = [e for e in graph.get("edges") or [] if isinstance(e, dict)
             and e.get("source") in by_id and e.get("target") in by_id]
    route = {}
    for e in edges:
        s, t = e["source"], e["target"]
        if s == t:
            route[e["id"]] = "self"
        elif s in rail_set and t in rail_set:
            route[e["id"]] = "rail"
        elif t in rail_set:
            route[e["id"]] = "drop"
        elif s in rail_set:
            route[e["id"]] = "rise"
        else:
            route[e["id"]] = "step"
    steps = [e for e in edges if route[e["id"]] == "step"]
    flow = [e for e in steps if e.get("kind") != "loop"]
    incoming = {e["target"] for e in flow}
    outgoing = {e["source"] for e in flow}
    given_starts = graph.get("starts") if isinstance(graph.get("starts"), list) else []
    starts_derived = not given_starts
    starts = list(given_starts) if given_starts else [n for n in act if n not in incoming]
    open_ends = list(graph.get("open_ends") or [])
    given_outcomes = graph.get("outcomes") if isinstance(graph.get("outcomes"), list) else []
    outcomes = list(given_outcomes) if given_outcomes else [
        n for n in act if n not in outgoing and n not in open_ends]
    # Return paths: depth-first search over step links (loop edges excluded), roots
    # visited as starts then graph order, outgoing edges in graph order. An edge whose
    # target is on the current search stack closes a cycle and is drawn as a return path.
    out = {n: [] for n in act}
    for e in flow:
        out[e["source"]].append(e)
    visited, on_stack, returns = set(), set(), set()
    for root in [s for s in starts if s in out] + act:
        if root in visited:
            continue
        visited.add(root)
        on_stack.add(root)
        stack = [(root, iter(out[root]))]
        while stack:
            node, successors = stack[-1]
            edge = next(successors, None)
            if edge is None:
                stack.pop()
                on_stack.discard(node)
                continue
            target = edge["target"]
            if target in on_stack:
                returns.add(edge["id"])
            elif target not in visited:
                visited.add(target)
                on_stack.add(target)
                stack.append((target, iter(out[target])))
    layout_edges = [e for e in flow if e["id"] not in returns]
    # Longest-path depth over layout edges (Kahn order; ready queue by graph order).
    import heapq
    indeg = {n: 0 for n in act}
    succ = {n: [] for n in act}
    for e in layout_edges:
        indeg[e["target"]] += 1
        succ[e["source"]].append(e["target"])
    depth = {n: 0 for n in act}
    ready = [(order[n], n) for n in act if indeg[n] == 0]
    heapq.heapify(ready)
    while ready:
        _, node = heapq.heappop(ready)
        for target in succ[node]:
            depth[target] = max(depth[target], depth[node] + 1)
            indeg[target] -= 1
            if indeg[target] == 0:
                heapq.heappush(ready, (order[target], target))
    return {"nodes": by_id, "order": order, "rails": rails, "act": act, "edges": edges,
            "route": route, "steps": steps, "starts": starts, "starts_derived": starts_derived,
            "outcomes": outcomes, "outcomes_given": bool(given_outcomes), "open_ends": open_ends,
            "returns": returns, "layout_edges": layout_edges, "depth": depth}


def graph_notes(graph, variant, current=None, pin=None, diagram=None):
    """Notes on unfamiliar or incomplete content, in graph order (nodes, edges, then
    page-level notes). Pure: `pin` is the bundle's current_state object as written and
    `current` the pinned plugin-shaped current-state graph when the caller loaded it."""
    topo = topology(graph)
    nodes, route = topo["nodes"], topo["route"]
    rails = set(topo["rails"])
    future = variant == FUTURE
    notes = []

    def note(gid, text):
        notes.append({"id": gid, "text": text})

    touched = {e["target"] for e in topo["edges"] if route[e["id"]] == "drop"}
    touched |= {e["source"] for e in topo["edges"] if route[e["id"]] == "rise"}
    reach, pending = set(), [s for s in topo["starts"] if s in nodes]
    step_out = {}
    for e in topo["steps"]:
        step_out.setdefault(e["source"], []).append(e["target"])
    while pending:
        nid = pending.pop()
        if nid in reach:
            continue
        reach.add(nid)
        pending.extend(step_out.get(nid, []))
    onward = {e["source"] for e in topo["steps"]}
    terminal_ok = set(graph.get("outcomes") or []) | set(topo["open_ends"])
    for n in graph.get("nodes") or []:
        nid, ntype, lane = n["id"], n.get("type"), n.get("lane")
        treatment = n.get("treatment")
        if ntype == "system_surface":
            if lane != "System":
                note(nid, "System rails are always drawn in the System band.")
        elif ntype in KNOWN_NODE_TYPES and lane_kind(lane) not in KNOWN_NODE_TYPES[ntype]:
            note(nid, "%s steps usually sit in the %s band; drawn where the graph puts it."
                 % (ntype, " or ".join(KNOWN_NODE_TYPES[ntype])))
        if variant == CURRENT and is_agent_lane(lane) and nid not in rails:
            note(nid, "Agent-performed step in a current-state map.")
        if future and ntype not in ("system_surface", "system_result") and not treatment:
            note(nid, "Ownership not stated.")
        if future and treatment in KNOWN_TREATMENTS and lane_kind(lane) not in KNOWN_TREATMENTS[treatment]:
            note(nid, "Ownership '%s' is unusual in the %s band." % (treatment, lane))
        if nid in rails:
            if future and "access_state" not in n:
                note(nid, "Access state not stated.")
            if nid not in touched:
                note(nid, "No step reads from or writes to this system.")
        else:
            if nid not in reach:
                note(nid, "Not reachable from a start.")
            if topo["outcomes_given"] and nid not in onward and nid not in terminal_ok:
                note(nid, "No onward link and not listed as an outcome or open end; treated as an outcome.")
    seen_access = set()
    for e in topo["edges"]:
        eid, kind = e["id"], e.get("kind")
        if kind in ("read_access", "write") and route[eid] == "step":
            note(eid, "%s link drawn between steps: its target is not a system rail." % kind)
        if route[eid] == "drop":
            key = (kind, e["source"], e["target"])
            if key in seen_access:
                note(eid, "More than one %s link between this step and this system." % kind)
            seen_access.add(key)
        if eid in topo["returns"]:
            note(eid, "Drawn as a return path: this link closes a cycle.")
    if topo["starts_derived"]:
        note("", "Starts not listed; steps with no incoming link are treated as starts.")
    if future and isinstance(pin, dict) and pin.get("kind", PLUGIN_SHAPE) != PLUGIN_SHAPE:
        note("", OTHER_SHAPE_NOTE)
    if variant not in KNOWN_VARIANTS:
        note("", "Variant '%s' is not current-state or future-state; general rules applied." % variant)
    band_labels = (diagram or {}).get("band_labels") or {}
    lanes = {n.get("lane") for n in graph.get("nodes") or []}
    if rails:
        lanes.add("System")
    for key in band_labels if isinstance(band_labels, dict) else []:
        if key not in lanes:
            note("", "Band label for '%s' ignored: no step uses that band." % key)
    if current is not None:
        notes.extend(carry_check(current, graph)[1])
    if future and not any(is_agent_lane(n.get("lane")) and n["id"] not in rails for n in graph.get("nodes") or []):
        note("", "No Agent-band step in this future-state map.")
    return notes


def graph_issues(value):
    """Graph issues: any non-empty kind (known: the intake kinds and open-question)."""
    result = {}
    for i, item in enumerate(rows(value, "issues")):
        key = item.get("id")
        require(isinstance(key, str) and key, "missing field id: issues[%d]" % i)
        for field in ("kind", "status", "reason", "source_ids"):
            require(field in item, "missing field %s: %s" % (field, key))
        require(nonempty_text(item.get("kind")), "missing field kind: " + key)
        require(item.get("status") == "open" and nonempty_text(item.get("reason")),
                "issues must remain explicitly open: " + key)
        require(isinstance(item.get("source_ids"), list), "unresolved issue references: " + key)
        result[key] = item
    return result


def issue_equal(original, carried, replacements=None):
    """Only binding identity may differ; history and all extension fields stay exact."""
    if original == carried:
        return True
    if not isinstance(carried, dict):
        return False
    if {k: v for k, v in original.items() if k != "bound_to"} != {
            k: v for k, v in carried.items() if k != "bound_to"}:
        return False
    old, new = id_list(original.get("bound_to")), id_list(carried.get("bound_to"))
    relation = replacements or {}
    return (old is not None and new is not None and len(old) == len(new)
            and all(a == b or relation.get(a) == b for a, b in zip(old, new)))


def supported_replacements(current, future, sources, map_text=""):
    """Resolve one-to-one, source-bound node replacements, never arbitrary descendants.

    Unchanged responsibility is compared as data. Changed responsibility requires the
    authored disposition AND an adopted, source-cited reshape move. This checks the
    declared evidence relationship, not the truth of natural-language design claims.
    The caller supplies only the hash-checked current graph and frozen sources.
    """
    if current is None:
        return {}
    active = {n["id"]: n for n in future.get("nodes", [])}
    originals = {n["id"]: n for n in current.get("nodes", [])}
    # Ignore examples/comments: only actual markdown table records supply authority.
    clean = re.sub(r"<!--.*?-->", "", map_text, flags=re.S)
    clean = re.sub(r"^```.*?^```[^\n]*", "", clean, flags=re.M | re.S)
    dispositions, moves = [], []
    table = None
    for line in clean.splitlines():
        if not line.strip().startswith("|"):
            table = None
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        header = [c.replace("`", "") for c in cells]
        if header == ["Node/step_code", "Title", "Systems", "Category", "Shape Classification",
                      "Sub-Agent Group", "Detail/Notes", "Fate"]:
            table = dispositions
        elif header == ["Move", "Kind", "Current steps", "Future steps", "Evidence", "State", "How the outcome is kept"]:
            table = moves
        elif table is not None and not all(re.fullmatch(r"[-: ]+", c) for c in cells):
            table.append(cells)
    relation = {}
    for old, source in originals.items():
        if old in active:
            continue
        candidates = [n for n in active.values() if old in (n.get("future", {}).get("from") or [])]
        if len(candidates) != 1:
            continue
        node = candidates[0]
        lineage = node.get("future", {})
        if lineage.get("from") != [old]:
            continue
        refs = source.get("source_refs")
        if not refs or node.get("source_refs") != refs:
            continue
        pids = {sources.get(r.get("source_id"), {}).get("process_id") for r in refs}
        if None in pids or len(pids) != 1:
            continue
        pid = next(iter(pids))
        if any(n.get("process_id", pid) != pid for n in (source, node)):
            continue
        ignored = {"id", "future", "treatment", "issue_id"}
        same = ({k: v for k, v in source.items() if k not in ignored} ==
                {k: v for k, v in node.items() if k not in ignored})
        explicit = False
        declared = [r for r in dispositions if len(r) == 8 and r[0].strip("`") == old
                        and r[1] == source.get("title")
                        and re.findall(r"Future element: `([^`]+)`", r[6]) == [node["id"]]
                        and r[7] == lineage.get("change")]
        if len(declared) == 1 and node.get("systems", []) == source.get("systems", []):
            for move in lineage.get("moves", []):
                matches = [r for r in moves if len(r) == 7 and r[0].strip("`") == move]
                if len(matches) != 1:
                    continue
                r = matches[0]
                cited = re.findall(r"`([^`]+)`", r[4])
                if (re.findall(r"`([^`]+)`", r[2]) == [old]
                        and node["id"] in re.findall(r"`([^`]+)`", r[3])
                        and set(x["source_id"] for x in refs) <= set(cited)
                        and all(x in sources for x in cited)
                        and r[5] in STATES and nonempty_text(r[6])
                        and move in declared[0][6]):
                    explicit = True
        if same or explicit:
            relation[old] = node["id"]
    return relation


def graph_shape(graph, variant, limits=None, evidence=True, diagram=None, replacements=None):
    """Every hard rule that needs no snapshot, plus notes. Pure; raises ValueError."""
    require(isinstance(graph, dict), "graph must be an object")
    node_list = graph.get("nodes")
    require(isinstance(node_list, list) and bool(node_list), "nodes must be a nonempty list")
    edge_list = graph.get("edges", [])
    require(isinstance(edge_list, list), "edges must be a list")
    for key in ("workspaces", "decision_tables", "issues", "starts", "outcomes", "open_ends"):
        require(isinstance(graph.get(key, []), list), "%s must be a list" % key)
    seen = {}

    def claim(item, label, fields):
        require(isinstance(item, dict), "%s must contain objects" % label)
        gid = item.get("id")
        require(gid is not None and gid != "", "missing field id: %s" % label)
        require(isinstance(gid, str) and ID_PATTERN.match(gid), "invalid graph id: %s" % gid)
        require(gid not in seen, "duplicate graph id: " + gid)
        seen[gid] = item
        for field in fields:
            value = item.get(field)
            if field == "source_refs":
                require(isinstance(value, list), "missing field source_refs: " + gid)
            elif field in ("evidence_state", "owner", "rows"):
                require(field in item, "missing field %s: %s" % (field, gid))
            else:
                require(nonempty_text(value), "missing field %s: %s" % (field, gid))
        return gid

    evidence_fields = ("evidence_state", "source_refs")
    nodes = {}
    for i, n in enumerate(node_list):
        nodes[claim(n, "nodes[%d]" % i, ("type", "title", "lane") + evidence_fields)] = n
    edges = {}
    for i, e in enumerate(edge_list):
        edges[claim(e, "edges[%d]" % i, ("kind", "source", "target") + evidence_fields)] = e
    workspaces = {}
    for i, w in enumerate(graph.get("workspaces", [])):
        workspaces[claim(w, "workspaces[%d]" % i, ())] = w
    tables, parts = {}, []
    for i, t in enumerate(graph.get("decision_tables", [])):
        tid = claim(t, "decision_tables[%d]" % i, ("kind", "owner", "rows") + evidence_fields)
        tables[tid] = t
        require(isinstance(t.get("rows"), list), "missing field rows: " + tid)
        for j, r in enumerate(t["rows"]):
            parts.append((claim(r, "%s.rows[%d]" % (tid, j), evidence_fields), r))
        cells = t.get("cells", [])
        require(isinstance(cells, list), "unresolved table reference: " + tid)
        for j, c in enumerate(cells):
            require(isinstance(c, dict), "unresolved table reference: " + tid)
            label = "%s/%s/%s" % (tid, c.get("row_id"), c.get("column"))
            if "id" in c:
                label = claim(c, label, ())
            require("evidence_state" in c, "missing field evidence_state: " + label)
            require(isinstance(c.get("source_refs"), list), "missing field source_refs: " + label)
            parts.append((label, c))
        rules = t.get("rules", [])
        require(isinstance(rules, list), "unresolved table reference: " + tid)
        for j, r in enumerate(rules):
            parts.append((claim(r, "%s.rules[%d]" % (tid, j), evidence_fields), r))
    opens = graph_issues(graph.get("issues", []))
    for key in opens:
        require(isinstance(key, str) and ID_PATTERN.match(key), "invalid graph id: %s" % key)
        require(key not in seen, "duplicate graph id: " + key)
        seen[key] = opens[key]

    # Evidence values and links (ratified vocabulary; exact links).
    elements = [(k, v, "issue_id") for k, v in list(nodes.items()) + list(edges.items())]
    elements += [(k, v, "issue_ids") for k, v in tables.items()] + [(k, v, "issue_ids") for k, v in parts]
    for key, row, field in elements:
        require(row.get("evidence_state") in STATES, "invalid evidence state: " + key)
        require(all(isinstance(ref, dict) for ref in row["source_refs"]), "unknown source reference: " + key)
        if field == "issue_ids":
            linked = id_list(row.get("issue_ids"))
            require(linked is not None and all(i in opens for i in linked), "unresolved table reference: " + key)
        else:
            linked = [row["issue_id"]] if row.get("issue_id") is not None else []
            require(all(i in opens for i in linked), "unresolved issue references: " + key)
        if evidence:
            require(bool(row["source_refs"]) or bool(linked), "unsourced element requires open issue: " + key)
            # An open issue does not stand in for a source: an element with no source is Assumed, never Observed.
            require(row["evidence_state"] != "Observed" or bool(row["source_refs"]), "Observed element requires a source: " + key)
            if row["evidence_state"] == "Contradictory":
                require(any(opens[i]["kind"] == "contradiction" for i in linked), "contradiction requires open issue: " + key)
            if row["evidence_state"] == "needs_more_information":
                require(bool(linked), "needs_more_information requires an open issue: " + key)
    if evidence:
        for key, item in opens.items():
            if item["kind"] == "contradiction":
                require(len(set(item["source_ids"])) >= 2, "contradiction needs both source claims: " + key)
    for key, item in opens.items():
        bound = id_list(item.get("bound_to"))
        relation = replacements or {}
        if not evidence and variant == FUTURE:
            # Drawing is not evidence validation: the public readers already verified
            # the pinned relation. Resolve only unique historical display anchors.
            relation = dict(relation)
            for b in bound or []:
                matches = [n["id"] for n in nodes.values() if n.get("future", {}).get("from") == [b]]
                if len(matches) == 1:
                    relation[b] = matches[0]
        require(bound is not None and all(b in seen or (variant == FUTURE and relation.get(b) in nodes)
                                         for b in bound), "unresolved issue references: " + key)

    # Links between elements.
    rail_ids = {k for k, n in nodes.items() if n["type"] == "system_surface"}
    for key, n in nodes.items():
        if n["type"] == "system_surface" and "access_state" in n:
            require(n["access_state"] in STATES, "invalid access_state: " + key)
        systems = n.get("systems", [])
        require(isinstance(systems, list), "unknown system reference %s: %s" % (systems, key))
        for ref in systems:
            require(ref in rail_ids or ref in workspaces, "unknown system reference %s: %s" % (ref, key))
        if n.get("reviews") is not None:
            require(n["reviews"] in nodes, "unknown review target: " + key)
    for key, e in edges.items():
        require(e["source"] in nodes and e["target"] in nodes, "dangling edge: " + key)
    for field in ("starts", "outcomes", "open_ends"):
        for ref in graph.get(field, []):
            require(ref in nodes, "unknown start/outcome/open end: %s" % ref)
    require(not set(graph.get("outcomes", [])) & set(graph.get("open_ends", [])), "outcomes and open_ends overlap")
    # A node's systems and the edges between it and system rails say the same thing.
    connected = {k: set() for k in nodes}
    for e in edges.values():
        if e["target"] in rail_ids and e["source"] not in rail_ids:
            connected[e["source"]].add(e["target"])
        elif e["source"] in rail_ids and e["target"] not in rail_ids:
            connected[e["target"]].add(e["source"])
    for key, n in nodes.items():
        if key in rail_ids:
            continue
        declared = {s for s in n.get("systems", []) if s in rail_ids}
        for rail in sorted(connected[key] - declared):
            raise ValueError("systems and access edges disagree: %s/%s" % (key, rail))
        if n["type"] != "system_result":
            for rail in [s for s in n.get("systems", []) if s in rail_ids and s not in connected[key]]:
                raise ValueError("systems and access edges disagree: %s/%s" % (key, rail))

    # Decision tables: the table and the drawn branches never disagree.
    naming = {t: [] for t in tables}
    for key, n in nodes.items():
        refs = id_list(n.get("decision_table"))
        require(refs is not None, "unknown decision table: %s" % key)
        if n["type"] == "decision":
            require(bool(refs), "decision requires decision_table: " + key)
        for ref in refs:
            require(ref in tables, "unknown decision table: %s" % ref)
            naming[ref].append(key)
    step_out, self_out = {}, {}
    for e in edges.values():
        if e["source"] not in rail_ids and e["target"] not in rail_ids:
            # A self-loop leaves its owner too: a selection row may name it. It is kept out of
            # the branch-coverage rule below, which reads step links between two steps only.
            (self_out if e["source"] == e["target"] else step_out).setdefault(e["source"], []).append(e)
    for tid, t in tables.items():
        owners = id_list(t.get("owner"))
        require(owners and all(o in nodes for o in owners) and set(owners) == set(naming[tid]),
                "decision table owner mismatch: " + tid)
        row_ids = [r["id"] for r in t["rows"]]
        for r in t["rows"]:
            require(r.get("next_node") is None or r["next_node"] in nodes, "unresolved table reference: " + r["id"])
            require(r.get("next_edge") is None or r["next_edge"] in edges, "unresolved table reference: " + r["id"])
        for c in t.get("cells", []):
            require(c.get("row_id") in row_ids, "unresolved table reference: %s" % tid)
        columns = t.get("columns", [])
        require(isinstance(columns, list), "unresolved table reference: " + tid)
        if t["kind"] == "consultation":
            grid = [(c.get("row_id"), c.get("column")) for c in t.get("cells", [])]
            expected = [(r, c) for r in row_ids for c in columns]
            require(bool(columns) and len(set(columns)) == len(columns) and len(grid) == len(set(grid))
                    and set(grid) == set(expected)
                    and all(r.get("next_node") is None and r.get("next_edge") is None for r in t["rows"]),
                    "consultation grid incomplete: " + tid)
        elif t["kind"] == "selection":
            require(not t.get("cells"), "selection table cannot hold cells: " + tid)
            outgoing = {e["id"]: e for o in owners for e in step_out.get(o, []) + self_out.get(o, [])}
            for r in t["rows"]:
                attributes = r.get("attributes", {})
                require(isinstance(attributes, dict) and set(attributes) <= set(columns),
                        "selection row attribute not in columns: " + r["id"])
                if r.get("next_edge") is not None:
                    edge = outgoing.get(r["next_edge"])
                    require(edge is not None and r.get("next_node") == edge["target"],
                            "selection row edge is not an outgoing edge of an owner: " + r["id"])
                if r.get("next_node") is None and r.get("next_edge") is None:
                    require(r.get("evidence_state") == "needs_more_information" and bool(id_list(r.get("issue_ids"))),
                            "open selection row needs needs_more_information and an issue: " + r["id"])
            for o in owners:
                if nodes[o]["type"] != "decision":
                    continue
                for e in step_out.get(o, []):
                    if e["kind"] in BRANCH_KINDS:
                        require([r.get("next_edge") for r in t["rows"]].count(e["id"]) == 1,
                                "decision branch missing from table: " + e["id"])
    if isinstance(limits, dict):
        topo = topology(graph)
        counts = {"max_nodes": len(topo["act"]), "max_edges": len(topo["steps"])}
        for key, count in counts.items():
            if key in limits:
                require(type(limits[key]) is int and limits[key] > 0 and count <= limits[key], "exceeds agreed " + key)
    return nodes, edges, tables, graph_notes(graph, variant, diagram=diagram)


def carry_check(current_graph, future_graph, replacements=None):
    """Compare a plugin-shaped current-state graph with the future graph it pins.

    Failures: every current issue not carried unchanged. Notes: current decision tables
    absent from the future graph (N-W17) or carried with different content (N-W18)."""
    failures, notes = [], []
    carried = {i.get("id"): i for i in future_graph.get("issues") or [] if isinstance(i, dict)}
    for issue in current_graph.get("issues") or []:
        if not issue_equal(issue, carried.get(issue.get("id")), replacements):
            failures.append("open/contradictory issue changed or dropped: %s" % issue.get("id"))
    tables = {t.get("id"): t for t in future_graph.get("decision_tables") or [] if isinstance(t, dict)}
    for table in current_graph.get("decision_tables") or []:
        tid = table.get("id")
        if tid not in tables:
            notes.append({"id": "", "text": "Decision table %s from the current state is not in this map." % tid})
        elif any(table.get(k) != tables[tid].get(k) for k in ("kind", "columns", "rows", "cells", "rules")):
            notes.append({"id": "", "text": "Decision table %s differs from the current state." % tid})
    return failures, notes


def current_state(path, bundle):
    """Validate the future state's required link to its current state (either shape).

    Returns (pin, current_graph): the loaded graph for a plugin-shaped pin, else None."""
    pin = bundle.get("current_state")
    require(isinstance(pin, dict), "future-state requires current_state: pin the current state this "
            "future state transforms (see workflow-diagrams.md, Format)")
    if "kind" in pin:
        require(nonempty_text(pin["kind"]), "current_state kind must be a non-empty string")
    kind = pin.get("kind", PLUGIN_SHAPE)
    pinned = reference(path, pin)
    if kind == PLUGIN_SHAPE:
        prefix = ("current_state must be a current-state bundle (for a current state in another shape, "
                  "set current_state.kind and list carried_open_items)")
        try:
            doc = json.loads(pinned.read_text(encoding="utf-8"))
            require(isinstance(doc, dict), "expected JSON object")
            require(doc.get("schema_version") == WORKFLOW_VERSION, "schema_version is not 2")
            require(doc.get("kind") == "workflow", "kind is not workflow")
            require(doc.get("variant") == CURRENT, "variant is not current-state")
            graph_shape(doc.get("graph"), CURRENT)
        except (ValueError, KeyError, TypeError, IndexError, AttributeError, UnicodeDecodeError) as exc:
            raise ValueError("%s: %s" % (prefix, exc))
        mine = reference(path, bundle.get("snapshot"))
        try:
            theirs = reference(pinned, doc.get("snapshot"))
        except ValueError:
            theirs = None
        require(theirs == mine, "current_state snapshot differs")
        require((doc.get("process") or {}).get("process_ids") == (bundle.get("process") or {}).get("process_ids"),
                "current_state process scope differs")
        return pin, doc["graph"]
    items = pin.get("carried_open_items")
    require(isinstance(items, list), "current_state in another shape must list carried_open_items "
            "(use [] only if the pinned file leaves nothing open)")
    opens = {i.get("id"): i for i in (bundle.get("graph") or {}).get("issues") or [] if isinstance(i, dict)}
    seen = set()
    for i, item in enumerate(items):
        label = item.get("id") if isinstance(item, dict) and isinstance(item.get("id"), str) else "#%d" % i
        require(isinstance(item, dict) and isinstance(item.get("id"), str) and ID_PATTERN.match(item["id"])
                and item["id"] not in seen, "invalid carried item id: %s" % label)
        seen.add(item["id"])
        require(nonempty_text(item.get("text")) and nonempty_text(item.get("source_locator")),
                "carried item needs text and source_locator: " + item["id"])
        issue = opens.get(item.get("issue_id"))
        require(issue is not None and issue.get("status") == "open", "carried item needs an open issue: " + item["id"])
    return pin, None


def resolve_evidence(elements, sources, intake_path):
    raw_sources = {}
    for key, row in elements:
        for ref in row.get("source_refs", []):
            require(isinstance(ref, dict) and ref.get("source_id") in sources, "unknown source reference: " + key)
            sid = ref["source_id"]
            if sid not in raw_sources:
                raw_sources[sid] = json.loads(reference(intake_path, sources[sid]).read_text(encoding="utf-8"))
            pointer(raw_sources[sid], ref.get("pointer"))


def graph(path):
    """Workflow bundle structure: pins, process, graph rules, exact evidence links (an Observed element
    cites a source; an open issue does not stand in for one) and, for a future state, the required link
    to its current state."""
    bundle = read(path, WORKFLOW_VERSION)
    require(bundle.get("kind") == "workflow", "expected workflow bundle")
    require(nonempty_text(bundle.get("variant")), "missing field variant: bundle")
    variant = bundle["variant"]
    require(bundle.get("presentation") in ("diagram", "none"), "presentation must be diagram or none")
    snapshot_path = reference(path, bundle.get("snapshot"))
    snapshot, intake_path = frozen(snapshot_path)
    process = bundle.get("process")
    require(isinstance(process, dict) and process.get("process_ids") == snapshot["scope"]["process_ids"],
            "process ids differ from snapshot")
    g = bundle.get("graph")
    diagram = bundle.get("diagram") if isinstance(bundle.get("diagram"), dict) else {}
    pin, current, replacements = None, None, {}
    included = {s["id"]: s for s in snapshot["sources"] if s["status"] == "included"}
    if variant == FUTURE:
        pin, current = current_state(path, bundle)
        # The map is the existing sibling artifact, not a search or invented pin.
        map_path = Path(path).resolve().parent.parent / "transformation-plan" / "transformation-map.md"
        map_text = map_path.read_text(encoding="utf-8") if map_path.is_file() else ""
        replacements = supported_replacements(current, g, included, map_text)
    nodes, edges, tables, notes = graph_shape(g, variant, snapshot["scope"].get("limits"), True, diagram, replacements)
    elements = list(nodes.items()) + list(edges.items()) + list(tables.items())
    for t in tables.values():
        elements += [(r["id"], r) for r in t["rows"]]
        elements += [("%s/%s/%s" % (t["id"], c.get("row_id"), c.get("column")), c) for c in t.get("cells", [])]
        elements += [(r["id"], r) for r in t.get("rules", [])]
    included = {s["id"]: s for s in snapshot["sources"] if s["status"] == "included"}
    resolve_evidence(elements, included, intake_path)
    known_sources = {s["id"] for s in snapshot["sources"]}
    for issue in g.get("issues", []):
        require(set(issue["source_ids"]) <= known_sources, "unresolved issue references: " + issue["id"])
    result = Checked(bundle=bundle, snapshot=snapshot, variant=variant, nodes=nodes, edges=edges,
                     tables=tables, notes=notes, pin=pin, current=current, replacements=replacements)
    result.printed = notes
    return result


def coverage(path):
    """Every captured step is covered exactly once by resolvable graph elements."""
    checked = graph(path)
    bundle, snapshot = checked.bundle, checked.snapshot
    expected = {s["id"] for s in snapshot["steps"]}
    # A source's "steps" pointer is optional (see the intake mapping), so a snapshot can
    # legitimately have zero steps -- only require a nonempty coverage list when there is
    # at least one source step to cover.
    entries = rows(bundle.get("coverage"), "coverage", bool(expected))
    actual = [r.get("step_id") for r in entries]
    require(len(actual) == len(set(actual)) and set(actual) == expected, "source step coverage missing/duplicated/extra")
    targets_known = set(checked.nodes) | set(checked.edges) | set(checked.tables)
    for t in checked.tables.values():
        targets_known |= {r["id"] for r in t["rows"]}
    open_ids = {i["id"] for i in bundle["graph"].get("issues", [])}
    for row in entries:
        targets = row.get("graph_ids", [])
        require(isinstance(targets, list) and len(set(targets)) == len(targets) and set(targets) <= targets_known,
                "coverage targets do not resolve")
        require(row.get("mapping") in {"mapped", "summary", "open"}, "invalid coverage mapping")
        require(bool(targets) or row.get("mapping") != "mapped", "mapped step has no target")
        if row["mapping"] != "mapped":
            require(row.get("rationale"), "summary/open requires explicit rationale")
        if row["mapping"] == "open":
            require(row.get("issue_id") in open_ids, "open coverage requires issue")
    checked.printed = []
    return checked


def systems_coverage(path):
    """Every system in the frozen inventory is accounted for: mapped onto a node, or cut with a
    real reason. Operational systems (not runtime/incidental) may not be cut with a throwaway
    reason. Structure + closed set only — whether a keep/cut call was WISE is the review's job.
    The inventory is data-derived (intake systems); names match regardless of case and spacing.
    In the v2 format a node's `systems` holds rail or workspace ids, so a system is on the graph when
    some node's `systems` names a system rail (matched by its `title` or id) or a workspace (matched by
    its `name` or id) that carries the system's name."""
    checked = graph(path)
    bundle, snapshot, nodes = checked.bundle, checked.snapshot, checked.nodes
    expected = {system_key(s): s for s in snapshot.get("systems", [])}
    # Backward-compatible: a bundle with no derived systems needs no ledger (empty == empty).
    entries = rows(bundle.get("systems_coverage", []), "systems_coverage")
    require(all(isinstance(r.get("system"), str) and r["system"].strip() for r in entries),
            "every systems_coverage row needs a system name")
    keys = [system_key(r["system"]) for r in entries]
    repeated = sorted({system_name(r["system"]) for r, k in zip(entries, keys) if keys.count(k) > 1})
    require(not repeated, "systems_coverage lists a system more than once: " + ", ".join(repeated))
    missing = sorted((expected[k] for k in set(expected) - set(keys)), key=system_key)
    require(not missing, "systems with no keep or cut decision in systems_coverage: " + ", ".join(missing))
    extra = sorted(system_name(r["system"]) for r, k in zip(entries, keys) if k not in expected)
    require(not extra, "systems_coverage names systems outside the snapshot inventory: " + ", ".join(extra))
    names = {}  # rail / workspace id -> the system names it carries
    for gid, node in nodes.items():
        if node.get("type") == "system_surface":
            names[gid] = {system_key(gid), system_key(node.get("title", ""))}
    for w in bundle["graph"].get("workspaces", []):
        names[w["id"]] = {system_key(w["id"]), system_key(w.get("name", ""))}
    on_nodes = {key for node in nodes.values() for ref in node.get("systems", [])
                for key in names.get(ref, {system_key(ref)})}
    for row, key in zip(entries, keys):
        name = system_name(row["system"])
        treatment = row.get("treatment")
        require(treatment in {"mapped", "cut"}, "invalid systems treatment: " + name)
        if treatment == "mapped":
            require(key in on_nodes, "mapped system is not present on any node: " + name)
        else:
            require(key not in on_nodes, "system marked cut is still present on a node: " + name)
            reason = row.get("rationale")
            require(isinstance(reason, str) and reason.strip(), "cut system needs a rationale: " + name)
            if system_tier(name) == "operational":
                require(reason_key(reason) not in SYSTEM_BOILERPLATE,
                        "operational system cut with a throwaway reason: " + name)
    checked.printed = []
    return checked


def preserved_issues(path):
    """Known gaps never vanish: issues carried from the snapshot, prior_workflow and a
    plugin-shaped current state stay unchanged; decision-table carry is noted."""
    checked = graph(path)
    bundle = checked.bundle
    previous = list(checked.snapshot["issues"])
    if bundle.get("prior_workflow"):
        prior = read(reference(path, bundle["prior_workflow"]), WORKFLOW_VERSION)
        previous += prior["graph"].get("issues", [])
    carried = {i["id"]: i for i in bundle["graph"].get("issues", [])}
    for issue in previous:
        require(issue_equal(issue, carried.get(issue["id"]), checked.replacements),
                "open/contradictory issue changed or dropped: " + issue["id"])
    printed = []
    if checked.variant == FUTURE:
        if checked.current is not None:
            failures, printed = carry_check(checked.current, bundle["graph"], checked.replacements)
            require(not failures, failures[0] if failures else "")
        else:
            printed = [{"id": "", "text": OTHER_SHAPE_NOTE}]
    checked.printed = printed
    return checked


SVG_NS = "{http://www.w3.org/2000/svg}"
VISIBLE = {"rect", "text", "path", "polyline", "polygon", "line", "circle"}
ROUTES = {"step", "drop", "rise", "rail", "return", "self"}


def parse_svg(svg_text):
    root = ET.fromstring(svg_text)
    require(root.tag == SVG_NS + "svg", "render must be standalone SVG")
    ids = [e.get("id") for e in root.iter() if e.get("id")]
    require(len(ids) == len(set(ids)), "duplicate render IDs")
    return root


def svg_binding(svg_text, graph, render_coverage=None):
    """Every node, edge and decision table of the graph is drawn exactly once, bound by
    its id and carrying the graph's own lane, type, systems, kind and endpoints."""
    root = parse_svg(svg_text)
    require(root.get("data-renderer") in RENDERER_IDS, "render is not from a known renderer")
    nodes = {n["id"]: n for n in graph["nodes"]}
    edges = {e["id"]: e for e in graph.get("edges", [])}
    tables = {t["id"]: t for t in graph.get("decision_tables", [])}
    expected = set(nodes) | set(edges) | set(tables)
    if render_coverage is not None:
        mapping = rows(render_coverage, "render_coverage", True)
        require(len(mapping) == len(expected) and {r.get("graph_id") for r in mapping} == expected,
                "render coverage must account for every graph element exactly once")
    elements = {e.get("id"): e for e in root.iter() if e.get("id")}
    bound, table_groups = {}, {}
    for element in root.iter():
        for attr, source in (("data-graph-node-id", nodes), ("data-graph-edge-id", edges)):
            gid = element.get(attr)
            if gid is not None:
                require(gid in source and gid not in bound, "unknown/duplicate rendered graph element: " + gid)
                require(element.get("id"), "graph element lacks render ID")
                bound[gid] = element
        tid = element.get("data-graph-table-id")
        if tid is not None:
            require(tid in tables and element.get("id"), "unknown/duplicate rendered graph element: " + tid)
            table_groups.setdefault(tid, []).append(element)
    for gid in set(nodes) | set(edges):
        require(gid in bound, "graph/render binding missing: " + gid)
    for tid in tables:
        require(tid in table_groups, "graph/render binding missing: " + tid)
    if render_coverage is not None:
        for row in render_coverage:
            gid, rid = row["graph_id"], row.get("render_id")
            element = elements.get(rid)
            owner = bound.get(gid) if gid not in tables else next(
                (g for g in table_groups.get(gid, []) if g.get("id") == rid), None)
            require(element is not None and element is owner, "graph/render binding missing: " + gid)
    for gid, node in nodes.items():
        element = bound[gid]
        require(element.get("data-lane") == node["lane"], "render lane differs from graph: " + gid)
        require(element.get("data-node-type") == node["type"], "render node type differs from graph: " + gid)
        try:
            systems = json.loads(element.get("data-systems", "null"))
        except ValueError:
            systems = None
        require(systems == node.get("systems", []), "render systems differ from graph: " + gid)
    for gid, edge in edges.items():
        element = bound[gid]
        require(element.get("data-source") == edge["source"] and element.get("data-target") == edge["target"],
                "render edge endpoints differ from graph: " + gid)
        require(element.get("data-kind") == edge["kind"], "render edge kind differs from graph: " + gid)
        require(element.get("data-route") in ROUTES, "render edge endpoints differ from graph: " + gid)
    for tid, table in tables.items():
        owners = id_list(table["owner"])
        drawn = [g.get("data-owner") for g in table_groups[tid]]
        require(sorted(drawn) == sorted(owners), "render table owner differs from graph: " + tid)

    # Every visible primitive is owned by a graph element or labelled decoration.
    def walk(el, owner=False):
        owner = owner or bool(el.get("data-graph-node-id") or el.get("data-graph-edge-id")
                              or el.get("data-graph-table-id") or el.get("data-decoration"))
        tag = el.tag.rsplit("}", 1)[-1]
        if tag in {"defs", "style", "title", "desc"}:
            return
        if tag in VISIBLE:
            require(owner, "unmapped visible render element: " + tag)
        for child in el:
            walk(child, owner)
    walk(root)
    return root


def render_binding(path):
    """The pinned SVG is bound to the graph element by element."""
    checked = graph(path)
    bundle = checked.bundle
    checked.printed = []
    if bundle.get("presentation") == "none":
        require(not bundle.get("render") and not bundle.get("render_html") and not bundle.get("render_coverage"),
                "no-presentation bundle carries a render")
        return checked
    render = reference(path, bundle.get("render"))
    svg_binding(render.read_text(encoding="utf-8"), bundle["graph"], bundle.get("render_coverage"))
    return checked


def _floats(value, count=None):
    try:
        numbers = [float(v) for v in str(value).replace(",", " ").split()]
    except ValueError:
        return None
    if count is not None and len(numbers) != count:
        return None
    return numbers if all(math.isfinite(v) for v in numbers) else None


def _on_box(point, box):
    px, py = point
    x, y, w, h = box
    eps = 0.01
    on_vertical = (abs(px - x) < eps or abs(px - (x + w)) < eps) and y - eps <= py <= y + h + eps
    on_horizontal = (abs(py - y) < eps or abs(py - (y + h)) < eps) and x - eps <= px <= x + w + eps
    return on_vertical or on_horizontal


def _crosses(a, b, box):
    (ax, ay), (bx, by) = a, b
    x, y, w, h = box
    if ax == bx:
        return x < ax < x + w and max(min(ay, by), y) < min(max(ay, by), y + h)
    return y < ay < y + h and max(min(ax, bx), x) < min(max(ax, bx), x + w)


def _intersects(r, box):
    rx, ry, rw, rh = r
    x, y, w, h = box
    return rx < x + w and x < rx + rw and ry < y + h and y < ry + rh


def svg_geometry(svg_text, graph, variant, diagram=None):
    """Measured geometry of the band renderer's SVG against its graph (pure).

    Node boxes inside the canvas and apart; orthogonal routes inside the canvas that
    avoid other nodes, start and end on their own endpoints, drops that land on their
    rails, and edge labels that sit off every node."""
    root = svg_binding(svg_text, graph)
    view = _floats(root.get("viewBox", ""), 4)
    require(view is not None and view[2] > 0 and view[3] > 0, "invalid canvas")
    require(root.get("data-renderer") == RENDERER_ID, "geometry requires the standard renderer")
    vx, vy, vw, vh = view

    def inside(x, y):
        return vx <= x <= vx + vw and vy <= y <= vy + vh

    boxes, rails = {}, {}
    for el in root.iter():
        nid = el.get("data-graph-node-id")
        if not nid:
            continue
        box = _floats(el.get("data-bounds"), 4)
        require(box is not None and box[2] > 0 and box[3] > 0, "invalid node bounds: " + nid)
        x, y, w, h = box
        require(inside(x, y) and inside(x + w, y + h), "node outside canvas: " + nid)
        boxes[nid] = tuple(box)
        if el.get("data-rail-y") is not None:
            line = _floats("%s %s %s" % (el.get("data-rail-y"), el.get("data-rail-x0"), el.get("data-rail-x1")), 3)
            require(line is not None, "invalid rail line: " + nid)
            rails[nid] = line
    ordered = list(boxes.items())
    for i, (nid, a) in enumerate(ordered):
        for other, b in ordered[i + 1:]:
            require(not _intersects(a, b), "overlapping node boxes: %s/%s" % (nid, other))
    for el in root.iter():
        eid = el.get("data-graph-edge-id")
        if not eid:
            continue
        numbers = [_floats(p, 2) for p in (el.get("data-points") or "").split()]
        require(len(numbers) >= 2 and all(numbers), "invalid connector points: " + eid)
        points = [tuple(p) for p in numbers]
        for x, y in points:
            require(inside(x, y), "connector outside canvas: " + eid)
        for a, b in zip(points, points[1:]):
            require(a[0] == b[0] or a[1] == b[1], "connector must be orthogonal: " + eid)
        route, source, target = el.get("data-route"), el.get("data-source"), el.get("data-target")
        if route in ("step", "return", "drop", "rise"):
            ends = {source, target}
            for a, b in zip(points, points[1:]):
                for nid, box in boxes.items():
                    if nid in ends or (route in ("drop", "rise") and nid in rails):
                        continue
                    require(not _crosses(a, b, box), "connector crosses a node interior: %s/%s" % (eid, nid))
        if route in ("step", "return"):
            require(_on_box(points[0], boxes[source]) and _on_box(points[-1], boxes[target]),
                    "connector misses visible target: " + eid)
        elif route == "self":
            x, y, w, h = boxes[source]
            for px, py in (points[0], points[-1]):
                require(abs(py - y) < 0.01 and x <= px <= x + w, "connector misses visible target: " + eid)
        elif route == "drop":
            ry, x0, x1 = rails[target]
            require(_on_box(points[0], boxes[source]), "connector misses visible target: " + eid)
            require(abs(points[-1][1] - ry) < 0.01 and x0 <= points[-1][0] <= x1, "drop does not land on its rail: " + eid)
        elif route == "rise":
            ry, x0, x1 = rails[source]
            require(abs(points[0][1] - ry) < 0.01 and x0 <= points[0][0] <= x1, "rise does not leave its rail: " + eid)
            require(_on_box(points[-1], boxes[target]), "connector misses visible target: " + eid)
        elif route == "rail":
            for point, rail in ((points[0], source), (points[-1], target)):
                ry, x0, x1 = rails[rail]
                require(abs(point[1] - ry) < 0.01 and x0 <= point[0] <= x1, "rail link does not join its rails: " + eid)
    for el in root.iter():
        eid = el.get("data-label-for")
        if eid is None or el.tag.rsplit("}", 1)[-1] != "rect":
            continue
        pill = _floats(" ".join(str(el.get(k)) for k in ("x", "y", "width", "height")), 4)
        require(pill is not None, "invalid edge label: " + eid)
        for nid, box in boxes.items():
            require(not _intersects(pill, box), "edge label overlaps a node: " + eid)
    return root


def _renderer():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "renderer"))
    from _workflow_bands import render_svg
    from _diagram_html import document
    return render_svg, document


def geometry(path):
    """Measured geometry plus canonical equality: the SVG and standalone HTML are exactly
    what the standard renderer produces from the pinned graph."""
    checked = render_binding(path)
    bundle = checked.bundle
    if bundle.get("presentation") == "none":
        return checked
    svg = reference(path, bundle["render"]).read_text(encoding="utf-8")
    diagram = bundle.get("diagram") if isinstance(bundle.get("diagram"), dict) else {}
    svg_geometry(svg, bundle["graph"], checked.variant, diagram)
    # Canonical render equality prevents declared geometry metadata from lying about
    # visible paths/text, CSS visibility or transforms. Glyph extents are deliberately
    # outside this stdlib geometric floor and remain a visual check by a person.
    render_svg, document = _renderer()
    expected = render_svg(bundle["graph"], checked.variant, diagram)
    require(svg == expected, "render differs from canonical graph projection")
    require(bundle.get("render_html"), "band render requires its standalone HTML artifact")
    html_path = reference(path, bundle["render_html"])
    page = html_path.read_text(encoding="utf-8")
    svg_href = os.path.relpath(reference(path, bundle["render"]), html_path.parent)   # the viewer's link to the SVG file
    require(page == document(expected, bundle, checked.current, svg_href=svg_href),
            "standalone HTML differs from its embedded diagram")
    return checked


CHECKS = {"snapshot-intake": intake, "snapshot-structure": frozen,
          "workflow-graph-structure": graph, "workflow-coverage-manifest": coverage,
          "workflow-systems-coverage": systems_coverage,
          "workflow-open-and-contradiction": preserved_issues,
          "workflow-render-binding": render_binding, "workflow-render-geometry": geometry}


def main(check):
    parser = argparse.ArgumentParser(description=CHECKS[check].__doc__)
    parser.add_argument("artifact")
    args = parser.parse_args()
    try:
        result = CHECKS[check](args.artifact)
    except (ValueError, KeyError, TypeError, IndexError, OSError, ET.ParseError) as exc:
        print("FAIL %s: %s" % (check, exc), file=sys.stderr)
        return 1
    print("PASS " + check)
    for note in getattr(result, "printed", []):
        print("NOTE %s: %s: %s" % (check, note["id"] or "page", note["text"]))
    return 0
