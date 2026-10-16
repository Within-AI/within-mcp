"""Read a single-process Future State Design run in the older layout into the normalised intake.

The older layout has no inputs/set-map.json, no *.layout.json and no references/*/a3; its graph nodes carry
`agent` tags and no `process_id`, so every node belongs to the one process. `read` only reads; it writes
nothing. Older runs may hold a platform-answers file under outputs/; it is never read. Standard library only,
3.9-safe.
"""

import json
import os
from datetime import datetime

SCHEMA = "agent-design.intake/1"
LAYOUT = "single-legacy"
ID_FORM = "<prefix>observation:<resource_key>"

CURRENT_STATE = "stages/current-state/current-state.json"
TRANSFORMATION_MAP = "stages/transformation-plan/transformation-map.md"
FUTURE_STATE = "stages/future-state/future-state.json"
SNAPSHOT = "inputs/snapshot.json"
SNAPSHOT_INTAKE = "inputs/snapshot-intake.json"
SET_MAP = "inputs/set-map.json"
STATE = "state.json"

# Same as NOT_WORK in skills/future-state-design/scripts/fsd.py: node types that are not work. intake.py imports this one.
NOT_WORK = ("ai_use", "object_state", "context_neighbour", "message", "start_event", "end_event",
            "system_surface", "system_result")
# Source kinds that are observations rather than sources: the collection and its single observations.
OBSERVATION_KINDS = ("observation", "observations")
EVIDENCE_STATES = ("Observed", "Assumed", "Contradictory", "needs_more_information")


class LegacyLayoutError(Exception):
    """The folder is not a readable single-process run in the older layout."""


ID_KEYS = ("process_id", "id", "process_resource_key", "process")


def _ids_of(items):
    """Process ids in a list of strings or of objects naming one under an id key; [] when the list names none."""
    out = []
    for x in items if isinstance(items, list) else []:
        v = x if isinstance(x, str) else next((x.get(k) for k in ID_KEYS if isinstance(x, dict)
                                               and isinstance(x.get(k), str) and x.get(k)), None)
        if isinstance(v, str) and v and v != "set" and v not in out:
            out.append(v)
    return out


def process_ids_in(doc):
    """(ids, field): the process ids a parsed document names and the field they came from, read structurally from
    whichever readable list it holds: scope.process_ids, process_ids, processes[], process.process_ids,
    graph.process_frames[], process_versions[] / freeze.process_versions[], or the work nodes' own process_id.
    ([], None) when it names none. The reserved node value `set` is never a process."""
    if not isinstance(doc, dict):
        return [], None
    scope = doc.get("scope") if isinstance(doc.get("scope"), dict) else {}
    process = doc.get("process") if isinstance(doc.get("process"), dict) else {}
    graph = doc.get("graph") if isinstance(doc.get("graph"), dict) else {}
    freeze = doc.get("freeze") if isinstance(doc.get("freeze"), dict) else {}
    for field, items in (("scope.process_ids", scope.get("process_ids")), ("process_ids", doc.get("process_ids")),
                         ("processes", doc.get("processes")), ("process.process_ids", process.get("process_ids")),
                         ("graph.process_frames", graph.get("process_frames")),
                         ("process_versions", doc.get("process_versions")),
                         ("freeze.process_versions", freeze.get("process_versions")),
                         ("graph.nodes", [n for n in graph.get("nodes") or [] if isinstance(n, dict)
                                          and n.get("type") not in NOT_WORK and isinstance(n.get("process_id"), str)]
                          if isinstance(graph.get("nodes"), list) else None)):
        ids = _ids_of([{"process_id": n["process_id"]} for n in items] if field == "graph.nodes" and items else items)
        if ids:
            return ids, field
    return [], None


def _fail(rel, problem):
    raise LegacyLayoutError("%s: %s" % (rel, problem))


def _load(run_folder, rel):
    path = os.path.join(run_folder, rel)
    root = os.path.realpath(run_folder)
    if os.path.lexists(path) and not os.path.realpath(path).startswith(root + os.sep):
        _fail(rel, "leads outside the supplied folder, so it is not read")
    if not os.path.isfile(path):
        _fail(rel, "file not found")
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except OSError as e:
        _fail(rel, "cannot be read (%s)" % e.strerror)
    except ValueError as e:
        _fail(rel, "is not valid JSON (%s)" % e)


def _need(cond, rel, problem):
    if not cond:
        _fail(rel, problem)


def _when(value, rel, what):
    _need(isinstance(value, str) and value, rel, "%s is missing" % what)
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        _fail(rel, "%s %r is not an ISO 8601 timestamp" % (what, value))


def _layout(run_folder):
    _need(not os.path.exists(os.path.join(run_folder, SET_MAP)), SET_MAP,
          "present, so this is not the older single-process layout")
    for dirpath, dirnames, filenames in os.walk(run_folder):
        rel_dir = os.path.relpath(dirpath, run_folder).replace(os.sep, "/")
        if rel_dir == "outputs" or rel_dir.startswith("outputs/"):
            dirnames[:] = []
            continue
        for name in filenames:
            if name.endswith(".layout.json"):
                _fail(rel_dir + "/" + name, "a layout file, so this is not the older single-process layout")
        parts = rel_dir.split("/")
        if len(parts) == 3 and parts[0] == "references" and parts[2] == "a3":
            _fail(rel_dir, "an a3 reference folder, so this is not the older single-process layout")


def _failing_checks(state):
    failing = []
    stages = state.get("stages")
    _need(isinstance(stages, dict), STATE, "has no stages table")
    for stage, record in stages.items():
        checks = record.get("checks") if isinstance(record, dict) else None
        for entry in checks if isinstance(checks, list) else []:
            if not isinstance(entry, dict) or entry.get("result") == "pass":
                continue
            failing.append({"stage": stage, "check": str(entry.get("check") or "(unnamed)"),
                            "result": entry.get("result"), "detail": entry.get("detail")})
    return failing


def _soft(problems, cond, rel, problem, kind):
    """With PROBLEMS (a list), a failed parseable-shape check is recorded there and False returned; without it, it stops."""
    if cond:
        return True
    if problems is None:
        _fail(rel, problem)
    problems.append({"kind": kind, "detail": "%s: %s" % (rel, problem)})
    return False


def _observation_count(run_folder, intake, cutoff, problems=None):
    sources = [s for s in intake.get("sources", []) if isinstance(s, dict)
               and s.get("collection_kind") == "observations" and s.get("status") == "included"]
    if not _soft(problems, len(sources) == 1, SNAPSHOT_INTAKE, "needs exactly one included observations source, found %d; "
                 "observations are not counted" % len(sources), "observations-sources"):
        return 0
    src = sources[0]
    rel = "inputs/" + str(src.get("path"))
    mapping = src.get("mapping") or {}
    items_key = str(mapping.get("items", "/items")).lstrip("/")
    when_key = str(mapping.get("observed_at", "/timestamp")).lstrip("/")
    data = _load(run_folder, rel)
    items = data.get(items_key) if isinstance(data, dict) else None
    if not _soft(problems, isinstance(items, list), rel, "has no %s list" % items_key, "no-items"):
        return 0
    count = 0
    for n, item in enumerate(items):
        if not _soft(problems, isinstance(item, dict), rel, "item %d is not an object; not counted" % n, "observation-unreadable"):
            continue
        try:
            when = _when(item.get(when_key), rel, "item %d %s" % (n, when_key))
        except LegacyLayoutError as e:
            if problems is None:
                raise
            problems.append({"kind": "observation-unreadable", "detail": "%s; not counted" % e})
            continue
        if cutoff is None or when <= cutoff:
            count += 1
    return count


def _evidence_states(graphs, problems=None):
    counts = dict((k, 0) for k in EVIDENCE_STATES)
    for rel, doc in graphs:
        graph = doc.get("graph") if isinstance(doc, dict) else None
        nodes = graph.get("nodes") if isinstance(graph, dict) else None
        if not _soft(problems, isinstance(nodes, list), rel, "has no graph.nodes list", "no-graph"):
            continue
        for node in nodes:
            if not _soft(problems, isinstance(node, dict), rel, "a node is not an object; not counted", "node-unreadable"):
                continue
            if node.get("type") in NOT_WORK:
                continue
            state = node.get("evidence_state")
            if not _soft(problems, state in counts, rel, "node %s has evidence_state %r, not one of %s; not counted"
                         % (node.get("id"), state, ", ".join(EVIDENCE_STATES)), "unknown-evidence-state"):
                continue
            counts[state] += 1
    return counts


def read(run_folder, problems=None):
    """Return the normalised intake (U1's intake.schema.json, layout single-legacy) for an older-layout run. With PROBLEMS (a
    list), parseable but malformed shapes (a stage not done, no freeze block or cutoff, a bad version entry, a bad
    observation or node) are recorded there and the intake is returned with what could be read; files that are missing or
    won't parse, and an unreadable process list, still stop."""
    soft = lambda cond, rel, problem, kind: _soft(problems, cond, rel, problem, kind)
    run_folder = os.path.abspath(run_folder)
    _need(os.path.isdir(run_folder), run_folder, "not a folder")
    _layout(run_folder)

    state = _load(run_folder, STATE) if soft(os.path.isfile(os.path.join(run_folder, STATE)), STATE, "file not found",
                                             "missing-file") else {}
    if not soft(isinstance(state, dict), STATE, "is not a JSON object", "state-not-object"):
        state = {}
    stages = state.get("stages") if isinstance(state.get("stages"), dict) else {}
    for stage in ("current-state", "transformation-plan", "future-state"):
        status = (stages.get(stage) or {}).get("status") if isinstance(stages.get(stage), dict) else None
        # `done, checks failing` is done too: its failing checks are carried forward, as in the v0.1 reader
        soft(isinstance(status, str) and (status == "done" or status.startswith("done,")), STATE,
             "stage %s is %r, not done" % (stage, status), "upstream-stage-not-done")

    snapshot = _load(run_folder, SNAPSHOT)
    ids, field = process_ids_in(snapshot)
    _need(ids, SNAPSHOT, "scope.process_ids is missing or empty, and no other list in it names a process")
    if field != "scope.process_ids" and problems is not None:
        why = "the snapshot has no scope.process_ids list"
        problems.append({"kind": "reading", "what": "process ids", "reading": "read from the snapshot's %s" % field,
                         "why": why, "detail": "process ids: read from the snapshot's %s (%s)" % (field, why)})
    _need(len(ids) == 1, SNAPSHOT, "scope.process_ids holds %d process ids; the older layout holds one" % len(ids))
    process_id = ids[0]
    _need(isinstance(process_id, str) and process_id, SNAPSHOT, "scope.process_ids[0] is not a non-empty string")

    freeze = state.get("freeze")
    if not soft(isinstance(freeze, dict), STATE, "has no freeze block", "no-freeze"):
        freeze = {}
    cutoff_text = freeze.get("cutoff")
    try:
        cutoff = _when(cutoff_text, STATE, "freeze.cutoff")
    except LegacyLayoutError as e:
        if problems is None:
            raise
        problems.append({"kind": "no-cutoff", "detail": str(e)})
        cutoff, cutoff_text = None, "unknown"
    versions = freeze.get("process_versions")
    if not soft(isinstance(versions, list), STATE, "freeze.process_versions is missing", "process-version"):
        versions = []
    process_versions = []
    for v in versions:
        if not soft(isinstance(v, dict) and all(k in v for k in ("process_id", "version_no", "created_at")), STATE,
                    "a freeze.process_versions entry lacks process_id, version_no or created_at", "process-version"):
            continue
        process_versions.append({"process_id": v["process_id"], "version_no": v["version_no"],
                                 "created_at": v["created_at"]})

    present = {rel: soft(os.path.isfile(os.path.join(run_folder, rel)), rel, "file not found", "missing-file")
               for rel in (CURRENT_STATE, TRANSFORMATION_MAP, FUTURE_STATE, SNAPSHOT_INTAKE)}
    graphs = [(rel, _load(run_folder, rel)) for rel in (CURRENT_STATE, FUTURE_STATE) if present[rel]]

    intake = _load(run_folder, SNAPSHOT_INTAKE) if present[SNAPSHOT_INTAKE] else {"sources": []}
    if not soft(isinstance(intake, dict) and isinstance(intake.get("sources"), list), SNAPSHOT_INTAKE, "has no sources list",
                "no-sources"):
        intake = {"sources": []}
    source_count = sum(1 for s in intake["sources"] if isinstance(s, dict) and s.get("status") == "included"
                       and s.get("process_id") == process_id and s.get("collection_kind") not in OBSERVATION_KINDS)

    return {
        "schema": SCHEMA,
        "layout": LAYOUT,
        "processes": [{
            "process_id": process_id,
            "current_state": CURRENT_STATE,
            "transformation_map": TRANSFORMATION_MAP,
            "future_state": FUTURE_STATE,
            "evidence_strength": {
                "observation_count": _observation_count(run_folder, intake, cutoff, problems),
                "source_count": source_count,
                "evidence_states": _evidence_states(graphs, problems),
            },
        }],
        "set_map": None,
        "snapshot": {"cutoff": cutoff_text, "process_versions": process_versions},
        "observations": {"snapshot_intake": SNAPSHOT_INTAKE, "id_form": ID_FORM},
        "post_freeze_reads": [],
        "upstream_failing_checks": _failing_checks(state) if isinstance(state.get("stages"), dict) or problems is None else [],
    }
