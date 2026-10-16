#!/usr/bin/env python3
"""Intake: read a finished Future State Design run folder into the normalised intake.

  intake.py --run RUN [--from FOLDER]      (FOLDER defaults to RUN/upstream)

Writes RUN/stages/intake/intake.json (U1's formats/design/intake.schema.json) and, only when an
observation is dated after the snapshot cutoff, RUN/stages/intake/evidence-addendum.json. Paths in the
intake are relative to RUN and begin `upstream/`; `build` copies no file. The intake carries paths, not
graphs, and writes no list of observation ids: an id is valid when it has the form in
`observations.id_form` and its resource key is in the snapshot intake.

The older single-process layout goes to `intake_single_legacy.read(folder)` (U6); absolute paths read from
upstream files go through `paths.resolve(path, folder)` (U28), except provenance fields that name a folder or
source outside the run by design (PROVENANCE, and state.json snapshot_source). Both are imported at call time.

When RUN/upstream/handover.json exists, each input is located leniently (`locate`): the declared pointer (backslashes
normalised, an absolute path reduced to the same file inside the run, the older key `transformation_map` accepted), then
the usual path, then a search of the run by file name (several candidates: hash match, newest, most complete; the others
named). A pointer that would leave the run is never followed. Each reading is recorded in `upstream_problems` as
{kind: reading, what, reading, why}. Process ids come from any readable list (`intake_single_legacy.process_ids_in`), the
snapshot first; nothing naming a process gives one unnamed process and a What we still need to know item (an
`upstream_failing_checks` entry with check `what-we-still-need-to-know`, which the register collects). Without a record (or
when it will not parse or is not an object) the older inference runs, and when the older readers cannot read the run each
input is located instead; the intake records `layout_source: legacy` plus one note in `upstream_problems`. What `ad.py
init` recorded (RUN/state.json init_readings), the upstream freeze's own readings and any text documents supplied with the
run (design evidence) are carried as readings too. Parseable but malformed upstream (a stage not done, no freeze or
cutoff, a non-object state, a bad observation, node or event line) becomes `upstream_problems` and the intake is written
with what could be read (exit 0). Exit 2, with nothing written, only when nothing in the folder can be read (or, on the
older-layout paths, a file the reader needs will not parse and the lenient locate cannot read the run either). Failing
upstream checks are carried forward and printed, never refused.
The plugin's shared write-grant rules (KNOWLEDGE_ROOT/checks/_write_grants.py) run on the upstream Write grants
table and future state; each problem joins `upstream_failing_checks` as stage `future-state`, check `write-grants`,
result `fail` (or `recorded` when the future state's `grant_readings` answers it, or `unchecked` when the rules could
not read the files). The stage prompts hand them to the stage agents; the run never stops for them.
Standard library only, Python 3.9.
"""
import argparse, hashlib, importlib.util, inspect, json, os, re, sys, tempfile, zipfile
from xml.etree import ElementTree
from datetime import datetime

SCRIPTS = os.path.dirname(os.path.abspath(__file__))
SCHEMA = os.path.join(SCRIPTS, "..", "formats", "design", "intake.schema.json")
PREFIX = "upstream/"
ID_FORM = "<prefix>observation:<resource_key>"
OUT_DIR = os.path.join("stages", "intake")
INTAKE = "intake.json"
ADDENDUM = "evidence-addendum.json"
STATES = ("Observed", "Assumed", "Contradictory", "needs_more_information")
HANDOVER = "handover.json"
CURRENT = "stages/current-state/current-state.json"
FUTURE = "stages/future-state/future-state.json"
TMAP = "stages/transformation-plan/transformation-map.md"
SET_MAP = "inputs/set-map.json"
SNAPSHOT_INTAKE = "inputs/snapshot-intake.json"


class IntakeError(Exception):
    """Malformed or unreadable input; nothing was written."""


class InstallError(IntakeError):
    """A module of the skill itself is missing: the plugin install is broken, not the input; always stops."""


def load_module(name):
    """Import NAME from the skill's scripts folder at call time (a module already loaded is reused)."""
    if name in sys.modules:
        return sys.modules[name]
    path = os.path.join(SCRIPTS, name + ".py")
    if not os.path.isfile(path):
        raise InstallError("%s is missing: the module %s is not installed" % (path, name))
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# node types that are not work: one definition, shared with intake_single_legacy.py and the same as fsd.py's
NOT_WORK = load_module("intake_single_legacy").NOT_WORK
# source kinds that are observations, not sources: the collection and its single observations (C6)
OBSERVATION_KINDS = load_module("intake_single_legacy").OBSERVATION_KINDS
# process ids from any readable list in a parsed document: one definition, shared with intake_single_legacy.py
process_ids_in = load_module("intake_single_legacy").process_ids_in


def read_json(folder, rel):
    path = os.path.join(folder, rel)
    root = os.path.realpath(folder)
    if os.path.lexists(path) and not os.path.realpath(path).startswith(root + os.sep):
        raise IntakeError("%s: the file leads outside the supplied folder, so it is not read" % rel)
    if not os.path.isfile(path):
        raise IntakeError("%s: the file is missing" % rel)
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, UnicodeDecodeError) as e:
        raise IntakeError("%s: the file cannot be read (%s)" % (rel, e))
    except ValueError as e:
        raise IntakeError("%s: the file is not readable JSON (%s)" % (rel, e))


def parse_when(value, where):
    if not isinstance(value, str) or not value.strip():
        raise IntakeError("%s: no timestamp" % where)
    text = value.strip().replace("Z", "+00:00")
    m = re.match(r"^(.*T\d\d:\d\d:\d\d)(\.\d+)?(.*)$", text)
    if m and m.group(2):  # 3.9's fromisoformat takes 3 or 6 fraction digits only
        text = m.group(1) + (m.group(2) + "000000")[:7] + m.group(3)
    try:
        when = datetime.fromisoformat(text)
    except ValueError:
        raise IntakeError("%s: %r is not an ISO 8601 timestamp" % (where, value))
    if when.tzinfo is None:
        raise IntakeError("%s: %r carries no time zone" % (where, value))
    return when


def layout_markers(folder):
    has_set_map = os.path.isfile(os.path.join(folder, SET_MAP))
    has_layout = False
    for root, dirs, files in os.walk(folder):
        dirs[:] = [d for d in dirs if d != "references"]
        if any(f.endswith(".layout.json") for f in files):
            has_layout = True
            break
    refs = os.path.join(folder, "references")
    has_a3 = os.path.isdir(refs) and any(os.path.isdir(os.path.join(refs, d, "a3")) for d in os.listdir(refs))
    return {"inputs/set-map.json": has_set_map, "*.layout.json": has_layout, "references/*/a3": has_a3}


def validate(value, schema, where="intake"):
    """The subset of JSON Schema U1's intake schema uses; returns a list of problems."""
    out = []
    types = {"object": dict, "array": list, "string": str, "integer": int, "null": type(None), "boolean": bool}
    if "oneOf" in schema:
        if sum(1 for s in schema["oneOf"] if not validate(value, s, where)) != 1:
            out.append("%s matches none of its allowed forms" % where)
        return out
    if "const" in schema and value != schema["const"]:
        return ["%s is %r, expected %r" % (where, value, schema["const"])]
    if "enum" in schema and value not in schema["enum"]:
        return ["%s is %r, not one of %s" % (where, value, schema["enum"])]
    if "type" in schema:
        allowed = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
        ok = any(isinstance(value, types[t]) and not (t == "integer" and isinstance(value, bool)) for t in allowed)
        if not ok:
            return ["%s is not of type %s" % (where, "/".join(allowed))]
    if isinstance(value, dict):
        for k in schema.get("required", []):
            if k not in value:
                out.append("%s lacks %s" % (where, k))
        props = schema.get("properties", {})
        for k, v in value.items():
            if k in props:
                out += validate(v, props[k], "%s.%s" % (where, k))
            elif schema.get("additionalProperties") is False:
                out.append("%s carries an unexpected field %s" % (where, k))
    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0):
            out.append("%s has fewer than %d items" % (where, schema["minItems"]))
        if "items" in schema:
            for i, v in enumerate(value):
                out += validate(v, schema["items"], "%s[%d]" % (where, i))
    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0):
            out.append("%s is empty" % where)
        if "pattern" in schema and not re.search(schema["pattern"], value):
            out.append("%s %r does not match the allowed path form" % (where, value))
    if isinstance(value, int) and not isinstance(value, bool) and value < schema.get("minimum", value):
        out.append("%s is below %s" % (where, schema["minimum"]))
    return out


def check_schema(doc, layout):
    with open(SCHEMA, encoding="utf-8") as f:
        schema = json.load(f)
    problems = validate(doc, schema)
    if doc.get("layout") != layout:
        problems.append("intake.layout is %r, expected %r" % (doc.get("layout"), layout))
    if problems:
        raise IntakeError("the intake does not validate against intake.schema.json: %s" % "; ".join(problems))


def prefixed(rel):
    rel = rel.replace(os.sep, "/")
    return rel if rel.startswith(PREFIX) else PREFIX + rel


def legacy(folder):
    reader = load_module("intake_single_legacy")
    problems = []
    try:
        takes_problems = "problems" in inspect.signature(reader.read).parameters
        doc = reader.read(folder, problems=problems) if takes_problems else reader.read(folder)
    except getattr(reader, "LegacyLayoutError", ()) as e:
        raise IntakeError(str(e))
    if not isinstance(doc, dict):
        raise IntakeError("the older-layout reader returned no intake object")
    doc = json.loads(json.dumps(doc))
    for p in doc.get("processes") or []:
        if isinstance(p, dict):
            for k in ("current_state", "transformation_map", "future_state"):
                if isinstance(p.get(k), str):
                    p[k] = prefixed(p[k])
    if isinstance(doc.get("set_map"), str):
        doc["set_map"] = prefixed(doc["set_map"])
    obs = doc.get("observations")
    if isinstance(obs, dict) and isinstance(obs.get("snapshot_intake"), str):
        obs["snapshot_intake"] = prefixed(obs["snapshot_intake"])
    doc["upstream_problems"] = [
        reading(p["what"], p["reading"], p["why"]) if p.get("kind") == "reading" and all(k in p for k in ("what", "reading", "why"))
        else {"kind": "legacy-" + str(p.get("kind") or "problem"), "detail": str(p.get("detail") or "")}
        for p in problems if isinstance(p, dict)]
    check_schema(doc, "single-legacy")
    return doc, None


def absolute_paths(value):
    if isinstance(value, str):
        if value.startswith("/") or value.startswith("~"):
            yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from absolute_paths(v)
    elif isinstance(value, list):
        for v in value:
            yield from absolute_paths(v)


# event fields that name a folder or source outside the run by design: provenance, never read, never resolved
PROVENANCE = {"run-init": (("knowledge_root",),),
              "knowledge-root-changed": (("old",), ("new",)),
              "direction-recorded": (("direction_source", "file"), ("direction_source", "via_design_prompt", "file")),
              "run-resumed": (("copy_check", "copied_from"),)}


def without_provenance(event):
    if not isinstance(event, dict) or event.get("event") not in PROVENANCE:
        return event
    event = json.loads(json.dumps(event))
    for field in PROVENANCE[event["event"]]:
        holder = event
        for key in field[:-1]:
            holder = holder.get(key) if isinstance(holder, dict) else None
        if isinstance(holder, dict):
            holder.pop(field[-1], None)
    return event


def check_snapshot_source(folder, source):
    """An older run names the snapshot folder (a string, outside the run): never resolved. A newer run records
    {name, sha256}: the sha256 must be that of inputs/snapshot.json."""
    if not isinstance(source, dict) or not isinstance(source.get("sha256"), str):
        return
    path = os.path.join(folder, "inputs", "snapshot.json")
    if not os.path.isfile(path):
        raise IntakeError("inputs/snapshot.json: the file is missing, so state.json snapshot_source.sha256 cannot be checked")
    with open(path, "rb") as f:
        actual = hashlib.sha256(f.read()).hexdigest()
    if actual != source["sha256"]:
        raise IntakeError("state.json: snapshot_source.sha256 is %s but inputs/snapshot.json has sha256 %s"
                          % (source["sha256"], actual))


def resolve_upstream_paths(folder, state, prob):
    """Snapshot hash and absolute event paths of an older (v0.1) run: a mismatch or a path that names no file in the run is a
    recorded problem through prob(kind, detail); an events.jsonl that won't parse stops."""
    found = []
    try:
        check_snapshot_source(folder, state.get("snapshot_source"))
    except IntakeError as e:
        prob("snapshot-hash", str(e))
    events = os.path.join(folder, "events.jsonl")
    if os.path.isfile(events):
        try:
            with open(events, encoding="utf-8") as f:
                lines = f.read().splitlines()
        except (OSError, UnicodeDecodeError) as e:
            raise IntakeError("events.jsonl: the file cannot be read (%s)" % e)
        for n, line in enumerate(lines, 1):
            if not line.strip():
                continue
            try:
                found += list(absolute_paths(without_provenance(json.loads(line))))
            except ValueError as e:
                prob("events-line-unreadable", "events.jsonl line %d is not readable JSON (%s); the line is skipped" % (n, e))
    if not found:
        return
    paths = load_module("paths")
    for p in found:
        try:
            paths.resolve(p, folder)
        except paths.Unresolved:
            prob("upstream-path-unresolved", "the upstream path %s cannot be found in %s" % (p, folder))


def failing_checks(state):
    out = []
    for stage, rec in (state.get("stages") or {}).items():
        if not isinstance(rec, dict):
            continue
        for c in rec.get("checks") or []:
            if not isinstance(c, dict) or c.get("result") == "pass":
                continue
            out.append({"stage": stage, "check": str(c.get("check")) if c.get("check") is not None else None,
                        "result": c.get("result"), "detail": c.get("detail")})
    for c in out:
        if not c["check"]:
            raise IntakeError("state.json: stage %s records a check with no name" % c["stage"])
    return out


def count_states(graphs, pid, prob=None):
    """Per evidence state, PID's work nodes. With PROB, a node with no process_id or an unknown evidence state is recorded
    through prob(kind, detail) and not counted; without it, it stops."""
    counts = dict((s, 0) for s in STATES)
    for graph, rel in graphs:
        for n in graph["nodes"]:
            if not isinstance(n, dict) or n.get("type") in NOT_WORK:
                continue
            # a work node belongs to the process named by its own process_id, whatever it cites;
            # the reserved value 'set' marks a set-level node, counted under no single process
            owner = n.get("process_id")
            if not isinstance(owner, str) or not owner:
                if prob is None:
                    raise IntakeError("%s: work node %s has no process_id" % (rel, n.get("id")))
                prob("node-no-process", "%s: work node %s has no process_id; not counted" % (rel, n.get("id")))
                continue
            if owner != pid:
                continue
            state = n.get("evidence_state")
            if state not in counts:
                if prob is None:
                    raise IntakeError("%s: node %s has evidence state %r, not one of %s"
                                      % (rel, n.get("id"), state, ", ".join(STATES)))
                prob("unknown-evidence-state", "%s: node %s has evidence state %r, not one of %s; not counted"
                     % (rel, n.get("id"), state, ", ".join(STATES)))
                continue
            counts[state] += 1
    return counts


def v01(folder):
    """The v0.1 set layout. Files that won't parse stop (read_json); a set map with no readable process list stops; every
    other parseable but malformed shape becomes an upstream problem and the intake is written with what could be read."""
    problems = []
    prob = lambda kind, detail: problems.append({"kind": kind, "detail": detail})
    markers = layout_markers(folder)
    for name, present in markers.items():
        if not present:
            prob("v01-marker-missing", "%s: not found; a v0.1 run folder carries inputs/set-map.json, a *.layout.json file "
                 "and references/*/a3" % name)
    state = {}
    if os.path.isfile(os.path.join(folder, "state.json")):
        state = read_json(folder, "state.json")
    else:
        prob("missing-file", "state.json: the file is missing")
    if not isinstance(state, dict):
        prob("state-not-object", "state.json: not an object")
        state = {}
    freeze = state.get("freeze")
    if not isinstance(freeze, dict):
        prob("no-freeze", "state.json: no freeze block; the run was never frozen")
        freeze = {}
    cutoff = None
    if not freeze.get("cutoff"):
        prob("no-cutoff", "state.json: the snapshot has no cutoff (freeze.cutoff is missing)")
    else:
        try:
            cutoff = parse_when(freeze["cutoff"], "state.json freeze.cutoff")
        except IntakeError as e:
            prob("no-cutoff", str(e))
    stages = state.get("stages") if isinstance(state.get("stages"), dict) else {}
    for stage in ("current-state", "transformation-plan", "future-state"):
        status = (stages.get(stage) or {}).get("status") if isinstance(stages.get(stage), dict) else None
        if not (isinstance(status, str) and (status == "done" or status.startswith("done,"))):
            prob("upstream-stage-not-done", "state.json: the %s stage is %r, not done" % (stage, status))
    versions = []
    for v in freeze.get("process_versions") or [] if isinstance(freeze.get("process_versions"), list) else []:
        if not isinstance(v, dict) or not all(k in v for k in ("process_id", "version_no", "created_at")):
            prob("process-version", "state.json: a freeze.process_versions entry lacks process_id, version_no or created_at")
            continue
        versions.append({"process_id": v["process_id"], "version_no": v["version_no"], "created_at": v["created_at"]})
    resolve_upstream_paths(folder, state, prob)

    set_map = read_json(folder, SET_MAP)
    raw = set_map.get("processes") if isinstance(set_map, dict) else None
    order = [p.get("process_id") for p in raw if isinstance(p, dict)] if isinstance(raw, list) else []
    if not order or not all(isinstance(p, str) and p for p in order):
        raise IntakeError("%s: no processes, or a process with no process_id" % SET_MAP)
    if len(set(order)) != len(order):
        prob("set-map-duplicate", "%s: a process is listed more than once; each is read once" % SET_MAP)
        order = list(dict.fromkeys(order))
    graphs, ids_in = [], {}
    for rel in (CURRENT, FUTURE):
        if not os.path.isfile(os.path.join(folder, rel)):
            prob("missing-file", "%s: the file is missing" % rel)
            ids_in[rel] = set()
            continue
        doc = read_json(folder, rel)
        graph = doc.get("graph") if isinstance(doc, dict) else None
        if not isinstance(graph, dict) or not isinstance(graph.get("nodes"), list):
            prob("no-graph", "%s: no graph with nodes" % rel)
            ids_in[rel] = set()
            continue
        ids = set((doc.get("process") or {}).get("process_ids") or []) if isinstance(doc.get("process"), dict) else set()
        ids |= {f.get("process_id") for f in graph.get("process_frames") or [] if isinstance(f, dict)}
        ids_in[rel] = ids
        graphs.append((graph, rel))
    if not os.path.isfile(os.path.join(folder, TMAP)):
        prob("missing-file", "%s: the file is missing" % TMAP)
    for pid in order:
        for rel in (CURRENT, FUTURE):
            if pid not in ids_in[rel]:
                prob("process-no-graph", "%s: the set map names process %s, which has no graph in %s" % (SET_MAP, pid, rel))

    sources = []
    if os.path.isfile(os.path.join(folder, SNAPSHOT_INTAKE)):
        intake = read_json(folder, SNAPSHOT_INTAKE)
        raw = intake.get("sources") if isinstance(intake, dict) else None
        if isinstance(raw, list):
            sources = raw
        else:
            prob("no-sources", "%s: no sources list" % SNAPSHOT_INTAKE)
        stated = ((intake.get("scope") or {}) if isinstance(intake, dict) and isinstance(intake.get("scope"), dict) else {}).get("cutoff_at")
        if stated is not None and cutoff is not None:
            try:
                if parse_when(stated, SNAPSHOT_INTAKE + " scope.cutoff_at") != cutoff:
                    prob("cutoff-disagrees", "%s: scope.cutoff_at %s disagrees with state.json freeze.cutoff %s"
                         % (SNAPSHOT_INTAKE, stated, freeze["cutoff"]))
            except IntakeError as e:
                prob("cutoff-disagrees", str(e))
    else:
        prob("missing-file", "%s: the file is missing" % SNAPSHOT_INTAKE)

    processes, late = [], []
    for pid in order:
        mine = [s for s in sources if isinstance(s, dict) and s.get("process_id") == pid]
        source_count = sum(1 for s in mine if s.get("status") == "included" and s.get("collection_kind") not in OBSERVATION_KINDS)
        collections = [s for s in mine if s.get("collection_kind") == "observations"]
        count = 0
        if len(collections) != 1:
            prob("observations-sources", "%s: process %s has %d observation collections, expected one; its observations are "
                 "not counted" % (SNAPSHOT_INTAKE, pid, len(collections)))
        else:
            coll = collections[0]
            prefix = coll["id"][:-len("observations")] if str(coll.get("id", "")).endswith("observations") else pid + ":"
            obs_rel = "inputs/" + str(coll.get("path"))
            if not os.path.isfile(os.path.join(folder, obs_rel)):
                prob("missing-file", "%s: the file is missing" % obs_rel)
                items = None
            else:
                obs_doc = read_json(folder, obs_rel)
                items = obs_doc.get("items") if isinstance(obs_doc, dict) else None
                if not isinstance(items, list):
                    prob("no-items", "%s: no items list" % obs_rel)
            if isinstance(items, list):
                with open(os.path.join(folder, obs_rel), "rb") as f:
                    digest = hashlib.sha256(f.read()).hexdigest()
                for n, item in enumerate(items):
                    key = item.get("resource_key") if isinstance(item, dict) else None
                    if not isinstance(key, str) or not key:
                        prob("observation-unreadable", "%s: observation %d has no resource_key; not counted" % (obs_rel, n))
                        continue
                    try:
                        when = parse_when(item.get("timestamp"), "%s observation %s" % (obs_rel, key))
                    except IntakeError as e:
                        prob("observation-unreadable", "%s; not counted" % e)
                        continue
                    if cutoff is None or when <= cutoff:
                        count += 1
                    else:
                        late.append({"process_id": pid, "observation_id": prefix + "observation:" + key,
                                     "observed_at": item["timestamp"], "path": prefixed(obs_rel), "sha256": digest})
        counts = count_states(graphs, pid, prob)
        processes.append({
            "process_id": pid,
            "current_state": prefixed(CURRENT),
            "transformation_map": prefixed(TMAP),
            "future_state": prefixed(FUTURE),
            "evidence_strength": {"observation_count": count, "source_count": source_count, "evidence_states": counts},
        })
    try:
        failing = failing_checks(state)
    except IntakeError as e:
        failing = []
        prob("unnamed-check", str(e))
    cutoff_text = freeze.get("cutoff") if isinstance(freeze.get("cutoff"), str) and freeze.get("cutoff") else "unknown"
    seen = set()
    problems = [p for p in problems if (p["kind"], p["detail"]) not in seen and not seen.add((p["kind"], p["detail"]))]
    doc = {
        "schema": "agent-design.intake/1",
        "layout": "v0.1",
        "processes": processes,
        "set_map": prefixed(SET_MAP),
        "snapshot": {"cutoff": cutoff_text, "process_versions": versions},
        "observations": {"snapshot_intake": prefixed(SNAPSHOT_INTAKE), "id_form": ID_FORM},
        "post_freeze_reads": [{"process_id": r["process_id"], "observation_id": r["observation_id"],
                               "observed_at": r["observed_at"], "path": r["path"]} for r in late],
        "upstream_failing_checks": failing,
        "upstream_problems": problems,
    }
    check_schema(doc, "v0.1")
    addendum = None
    if late:
        addendum = {"entries": [{
            "id": "EA-%d" % n, "what": "observation %s" % r["observation_id"],
            "why": "dated after the snapshot cutoff %s" % cutoff_text, "ids": [r["observation_id"]],
            "capture_date": r["observed_at"], "capture_proof": "observation.timestamp", "read_at": None,
            "sha256": r["sha256"], "read_file": r["path"],
            "claim": "observation %s was captured after the freeze" % r["observation_id"],
            "status": "flagged", "resolution": None, "resolved_at": None} for n, r in enumerate(late, 1)]}
    return doc, addendum


# ---------------------------------------------------------------- the declared hand-over record (U4) and the lenient locate

CONVENTIONAL = {"snapshot": "inputs/snapshot.json", "snapshot_intake": SNAPSHOT_INTAKE, "state": "state.json",
                "current_state": CURRENT, "transformation_plan": TMAP, "future_state": FUTURE, "set_map": SET_MAP}
WHAT = {"snapshot": "the snapshot", "snapshot_intake": "snapshot intake", "state": "the run state (state.json)",
        "current_state": "current state", "transformation_plan": "transformation map", "future_state": "future state",
        "set_map": "the set map"}
OLDER_KEYS = {"transformation_plan": ("transformation_map",)}   # pointer keys older hand-over records used
DOC_EXT = (".md", ".markdown", ".txt", ".text", ".rst")
UNNAMED = "unnamed-process"
REPAIRED = "stages/intake/set-map.json"   # the repaired set map, run-relative (the scoped original stays upstream)
REPAIRED_KEY = "_repaired_set_map"
DOC_TEXT_DIR = "stages/intake/documents"   # extracted text of Word documents, run-relative
DOC_TEXTS_KEY = "_document_texts"
STILL_NEED = "what-we-still-need-to-know"


def reading(what, read_as, why):
    """A recorded reading: which reading the intake took and why, kept in upstream_problems."""
    return {"kind": "reading", "detail": "%s: %s (%s)" % (what, read_as, why), "what": what, "reading": read_as, "why": why}


def still_need(text):
    """A What we still need to know item, carried as an upstream check the register collects."""
    return {"stage": "intake", "check": STILL_NEED, "result": "recorded", "detail": text}


def inside(folder, rel):
    root = os.path.realpath(folder)
    return os.path.realpath(os.path.join(folder, rel)).startswith(root + os.sep)


def safe_pointer(folder, rel):
    """True when rel is a run-relative path that stays inside the folder: no absolute path, no `..`, no symlink escaping."""
    if not isinstance(rel, str) or not rel or rel.startswith(("/", "~", "\\")) or re.match(r"^[A-Za-z]:", rel):
        return False
    if ".." in rel.replace("\\", "/").split("/"):
        return False
    return inside(folder, rel)


def normalise_pointer(folder, raw):
    """(rel, note, refused): the declared pointer as a run-relative path to a file inside the folder. Backslashes become
    slashes; an absolute path is reduced to its longest tail naming a file inside the run. refused is True when the
    pointer could not be followed without leaving the run (or names nothing inside it)."""
    if not isinstance(raw, str) or not raw.strip():
        return None, "the pointer is empty or not text", True
    text = raw.strip().replace("\\", "/")
    drive = re.match(r"^[A-Za-z]:", text)
    absolute = text.startswith(("/", "~")) or bool(drive)
    parts = [p for p in (text[2:] if drive else text).split("/") if p not in ("", ".", "~")]
    if absolute:
        for i in range(len(parts)):
            tail = parts[i:]
            rel = "/".join(tail)
            if ".." not in tail and os.path.isfile(os.path.join(folder, rel)) and inside(folder, rel):
                return rel, "the pointer was the absolute path %s; the same file inside the run was read" % raw, False
        return None, "the pointer is an absolute path naming no file inside the run", True
    if ".." in parts:
        return None, "the pointer leaves the run", True
    rel = "/".join(parts)
    if not rel or not inside(folder, rel):
        return None, "the pointer resolves outside the run", True
    if not os.path.isfile(os.path.join(folder, rel)):
        return None, "the declared file %s is missing" % rel, False
    return rel, ("the pointer %s was read with its separators normalised" % raw if rel != raw else None), False


def search(folder, name):
    """Run-relative paths of the files named NAME inside the folder (links leading outside are left out)."""
    out = []
    for dp, dns, fns in os.walk(folder):
        dns.sort()
        for n in sorted(fns):
            if n == name:
                rel = os.path.relpath(os.path.join(dp, n), folder).replace(os.sep, "/")
                if inside(folder, rel):
                    out.append(rel)
    return out


def file_sha(folder, rel):
    with open(os.path.join(folder, rel), "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def parses(folder, rel):
    if not rel.endswith(".json"):
        return True
    try:
        read_json(folder, rel)
        return True
    except IntakeError:
        return False


def locate(folder, key, raw, prob, record, declared=True, prefer_sha=None, claimed=None, optional=False):
    """The run-relative path of KEY's input, or None: the declared pointer RAW (normalised), then the conventional path,
    then a search of the run by file name. Several search candidates: the best-supported (hash matching the declaration,
    then newest, then largest) with the others named. Every reading other than a clean declared pointer is recorded; a
    pointer that would leave the run is never followed (pointer-refused); nothing found is input-unavailable."""
    what, conventional = WHAT[key], CONVENTIONAL[key]
    why = None
    if raw is not None:
        rel, note, refused = normalise_pointer(folder, raw)
        if rel and not parses(folder, rel):
            prob("unreadable-file", "%s: the declared %s will not parse; looking for a readable one" % (rel, what))
            rel, note = None, "the declared file %s will not parse" % rel
        if rel:
            if note:
                record(reading(what, "read %s" % rel, note))
            return rel
        if refused:
            prob("pointer-refused", "the hand-over pointer %s is not a run-relative path inside the run (%s), so it was "
                 "not followed" % (key, note))
        why = note
    elif declared:
        why = "the hand-over record declares no pointer for it"
    if os.path.isfile(os.path.join(folder, conventional)) and inside(folder, conventional) and parses(folder, conventional):
        if why:
            record(reading(what, "read the usual path %s" % conventional, why))
        return conventional
    names = [os.path.basename(conventional)]
    if isinstance(raw, str) and raw.strip():
        tail = raw.strip().replace("\\", "/").rstrip("/").split("/")[-1]
        if tail and tail not in names:
            names.append(tail)
    cands = []
    for n in names:
        cands += [r for r in search(folder, n) if r not in cands and parses(folder, r)]
    by_content = False
    if not cands and key in KINDS:
        others = set(v for k, v in CONVENTIONAL.items() if k != key)
        cands = [r for r in json_files(folder) if r not in (claimed or ()) and r not in others
                 and classify(folder, r) == KINDS[key]]
        by_content = bool(cands)
    if not cands:
        if not (optional and raw is None):
            prob("input-unavailable", "%s could not be found in the run (%s; nothing at %s and no file named %s), so it "
                 "is unavailable" % (what, why or "no pointer", conventional, " or ".join(names)))
        return None

    def support(r):
        p = os.path.join(folder, r)
        return (prefer_sha is not None and file_sha(folder, r) == prefer_sha, os.path.getmtime(p), os.path.getsize(p))

    cands.sort(key=support, reverse=True)
    best, others = cands[0], cands[1:]
    found = "a search of the run by content found it" if by_content else "a search of the run found it"
    basis = found if not others else \
        "a search of the run found %d files; this one is the best supported (%s); also found: %s" % (
            len(cands), "its hash matches the declaration" if support(best)[0] else "newest, then most complete",
            ", ".join(others))
    record(reading(what, "read %s" % best, "%s; %s" % (why or "nothing at the usual path %s" % conventional, basis)))
    return best


# structural kinds of a readable JSON file, for finding a renamed input by its content
KINDS = {"future_state": "future-graph", "current_state": "current-graph", "snapshot": "snapshot",
         "snapshot_intake": "snapshot-intake", "set_map": "set-map", "state": "run-state"}


def json_files(folder):
    """Run-relative paths of every .json file inside the folder (links leading outside left out)."""
    out = []
    for dp, dns, fns in os.walk(folder):
        dns.sort()
        for n in sorted(fns):
            if n.endswith(".json"):
                rel = os.path.relpath(os.path.join(dp, n), folder).replace(os.sep, "/")
                if inside(folder, rel):
                    out.append(rel)
    return out


def classify(folder, rel):
    """The structural kind of a readable JSON file, or None: a graph document (graph.nodes) is a future state when it
    carries a current_state reference, else a current state; scope.process_ids or scope.cutoff_at marks a snapshot;
    a sources list a snapshot intake; processes plus links a set map; a stages object a run state."""
    try:
        d = read_json(folder, rel)
    except IntakeError:
        return None
    if not isinstance(d, dict):
        return None
    g = d.get("graph")
    if isinstance(g, dict) and isinstance(g.get("nodes"), list):
        return "future-graph" if "current_state" in d else "current-graph"
    sc = d.get("scope")
    if isinstance(sc, dict) and ("process_ids" in sc or "cutoff_at" in sc) and not isinstance(d.get("sources"), list):
        return "snapshot"
    if isinstance(d.get("sources"), list):
        return "snapshot-intake"
    if isinstance(d.get("processes"), list) and isinstance(d.get("links"), list):
        return "set-map"
    if isinstance(d.get("stages"), dict):
        return "run-state"
    return None


def nested_set_maps(doc, path=""):
    """(path, map) for every object inside DOC holding a processes list and a links list."""
    if isinstance(doc, dict):
        if isinstance(doc.get("processes"), list) and isinstance(doc.get("links"), list):
            yield path or "(top)", doc
        for k, v in doc.items():
            yield from nested_set_maps(v, (path + "." if path else "") + str(k))
    elif isinstance(doc, list):
        for i, v in enumerate(doc):
            yield from nested_set_maps(v, "%s[%d]" % (path, i))


def link_key(link):
    return link.get("id") if isinstance(link, dict) and isinstance(link.get("id"), str) else json.dumps(link, sort_keys=True)


def repair_set_map(folder, sm_rel, set_map, ids):
    """(repaired map or None, readings, still-need items) for a scoped set map: missing processes and links are recovered
    from a whole map of the same set found anywhere in the run (it names every process the scoped map names and none
    outside the run's processes; the one covering most processes wins); without one, the processes missing from
    the map are added from the snapshot's list with the gap recorded. The scoped original stays as evidence."""
    sm_ids = process_ids_in(set_map)[0]
    links = [l for l in set_map.get("links") or [] if l is not None] if isinstance(set_map.get("links"), list) else []
    have = set(link_key(l) for l in links)
    best = None
    for rel in json_files(folder):
        if rel == sm_rel:
            continue
        try:
            doc = read_json(folder, rel)
        except IntakeError:
            continue
        for where, m in nested_set_maps(doc):
            m_ids = process_ids_in(m)[0]
            # the same set: it holds every process the scoped map names and none outside the run's processes
            if not m_ids or not set(sm_ids) <= set(m_ids) or not set(m_ids) <= set(ids):
                continue
            extra = [l for l in m["links"] if l is not None and link_key(l) not in have]
            cover = len(set(m_ids) & set(ids))
            if (set(m_ids) - set(sm_ids) or extra) and (best is None or (cover, len(m["links"])) > best[0]):
                best = ((cover, len(m["links"])), rel, where, m, extra)
    missing = [p for p in ids if p not in sm_ids]
    if best is None and not missing:
        return None, [], []
    repaired = json.loads(json.dumps(set_map))
    procs = repaired["processes"] if isinstance(repaired.get("processes"), list) else []
    readings, needs = [], []
    if best is not None:
        _, rel, where, whole, extra = best
        for p in whole["processes"]:
            pid = process_ids_in({"processes": [p]})[0]
            if pid and pid[0] not in sm_ids:
                procs.append(p)
        repaired["links"] = links + extra
        repaired["repaired_from"] = {"scoped": prefixed(sm_rel), "whole": "%s at %s" % (prefixed(rel), where)}
        readings.append(reading("the set map", "%d link(s) and %d process(es) recovered from the whole map in %s (%s); the "
                                "scoped map kept as evidence" % (len(extra), len(procs) - len(set_map.get("processes") or []),
                                                                 prefixed(rel), where),
                                "the set map %s covers only part of the set" % prefixed(sm_rel)))
        missing = [p for p in ids if p not in process_ids_in({"processes": procs})[0]]
    if missing:
        procs += [{"process_id": p} for p in missing]
        repaired.setdefault("repaired_from", {"scoped": prefixed(sm_rel), "whole": None})
        readings.append(reading("the set map", "%s added from the snapshot's process list, with no links; the scoped map "
                                "kept as evidence" % ", ".join(missing), "no whole set map in the run covers them"))
        needs.append(still_need("How %s connect%s to the rest of the set: the set map available covers only part of it, "
                                "so those links are not drawn yet." % (", ".join(missing), "s" if len(missing) == 1 else "")))
    repaired["processes"] = procs
    return repaired, readings, needs


def soft_json(folder, rel, prob):
    """The parsed file, or None with a recorded problem when it will not parse."""
    try:
        return read_json(folder, rel)
    except IntakeError as e:
        prob("unreadable-file", "%s; it is unavailable" % e)
        return None


def freeze_versions(freeze, prob):
    """Process version pins from the freeze block's process_versions, or from another list of objects naming a process."""
    raw = freeze.get("process_versions")
    if not isinstance(raw, list):
        raw = next((v for k, v in sorted(freeze.items()) if isinstance(v, list) and v
                    and all(isinstance(x, dict) and "process_id" in x for x in v)), [])
    out = []
    for v in raw:
        if isinstance(v, dict) and isinstance(v.get("process_id"), str) and v["process_id"] \
                and isinstance(v.get("version_no"), int) and not isinstance(v.get("version_no"), bool) \
                and v["version_no"] >= 0 and isinstance(v.get("created_at"), str):
            out.append({"process_id": v["process_id"], "version_no": v["version_no"], "created_at": v["created_at"]})
        else:
            prob("process-version", "state.json: a freeze pin lacks a process_id, a whole-number version_no or a "
                 "created_at; it is not carried")
    return out


DOCX = ".docx"
DOCX_LIMIT = 20 * 1024 * 1024   # bytes of word/document.xml read at most
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def docx_text(path):
    """The paragraphs of a Word document as plain text (stdlib zip and XML readers), or None when it cannot be read."""
    try:
        with zipfile.ZipFile(path) as z:
            info = z.getinfo("word/document.xml")
            if info.file_size > DOCX_LIMIT:
                return None
            root = ElementTree.fromstring(z.read(info))
    except (OSError, KeyError, zipfile.BadZipFile, ElementTree.ParseError, ValueError, RuntimeError):
        return None
    paras = ["".join(t.text or "" for t in p.iter(W + "t")) for p in root.iter(W + "p")]
    return "\n".join(x for x in paras if x.strip())


GENERATED_DIRS = ("stages", "prompts", "logs", "references", "page", "builder", "outputs", "checks", "simulation")
GENERATED_FILES = ("BRIEF.md",)   # Future State Design's own brief (fsd.py write_brief)


def documents(folder, everywhere=True):
    """Run-relative paths of a person's text documents anywhere in the folder, leaving out the plugin's own generated
    files: those under the folders a skill writes (GENERATED_DIRS), its brief, and the snapshot files the run state lists
    as copied inputs."""
    copied = set()
    try:
        st = read_json(folder, "state.json")
        if isinstance(st, dict) and isinstance(st.get("inputs"), dict):
            copied = set("inputs/" + k for k in st["inputs"])
    except IntakeError:
        pass
    out = []
    for dp, dns, fns in os.walk(folder):
        dns.sort()
        rel_dir = os.path.relpath(dp, folder).replace(os.sep, "/")
        if rel_dir == ".":
            dns[:] = [d for d in dns if d not in GENERATED_DIRS]
        for n in sorted(fns):
            rel = n if rel_dir == "." else rel_dir + "/" + n
            if not (n.lower().endswith(DOC_EXT + (DOCX,)) and rel not in GENERATED_FILES and rel not in copied
                    and inside(folder, rel)):
                continue
            if n.lower().endswith(DOCX) and docx_text(os.path.join(folder, rel)) is None:
                continue
            out.append(rel)
    return out


def declared(folder, ho, generic=False):
    """The intake from a declared hand-over record (or, GENERIC, from no record: every input located). Each input is
    located leniently (locate); process ids come from any readable list, the snapshot first; parseable but malformed
    upstream becomes upstream_problems. Raises IntakeError only when nothing in the folder can be read."""
    problems, readings, needs = [], [], []
    prob = lambda kind, detail: problems.append({"kind": kind, "detail": detail})
    record = readings.append
    raw_ptrs = ho.get("pointers") if isinstance(ho.get("pointers"), dict) else {}
    ptrs = dict(raw_ptrs)
    for key, older in OLDER_KEYS.items():
        for o in older:
            if key not in ptrs and o in ptrs:
                ptrs[key] = ptrs[o]
                record(reading(WHAT[key], "the pointer under %s was read as %s" % (o, key),
                               "an older hand-over record names it %s" % o))
    parsed = [HANDOVER] if ho else []

    claimed = []

    def find(key, prefer_sha=None, optional=False):
        rel = locate(folder, key, ptrs.get(key), prob, record, declared=not generic and not optional,
                     prefer_sha=prefer_sha, claimed=claimed, optional=optional)
        if rel:
            claimed.append(rel)
        return rel

    def load(rel):
        doc = soft_json(folder, rel, prob) if rel else None
        if doc is not None:
            parsed.append(rel)
        return doc

    state_rel = find("state")
    state = load(state_rel) if state_rel else None
    if state_rel and state is not None and not isinstance(state, dict):
        prob("state-not-object", "%s: not an object; the run state is unavailable" % state_rel)
    state = state if isinstance(state, dict) else {}
    src = state.get("snapshot_source")
    src_sha = src.get("sha256") if isinstance(src, dict) and isinstance(src.get("sha256"), str) else None
    snap_rel = find("snapshot", prefer_sha=src_sha)
    snap = load(snap_rel) if snap_rel else None
    if src_sha and snap_rel and file_sha(folder, snap_rel) != src_sha:
        prob("snapshot-hash", "state.json: snapshot_source.sha256 is %s but %s has sha256 %s"
             % (src_sha, snap_rel, file_sha(folder, snap_rel)))
    cur_rel, tm_rel, fut_rel = find("current_state"), find("transformation_plan"), find("future_state")
    graphs, graph_docs = [], []
    for rel in (cur_rel, fut_rel):
        doc = load(rel) if rel else None
        g = doc.get("graph") if isinstance(doc, dict) else None
        if isinstance(g, dict) and isinstance(g.get("nodes"), list):
            graphs.append((g, rel))
            graph_docs.append((rel, doc))
        elif rel and doc is not None:
            prob("no-graph", "%s: no graph with nodes" % rel)
    sm_raw = ptrs.get("set_map")
    sm_rel = find("set_map", optional=sm_raw is None)
    set_map = load(sm_rel) if sm_rel else None

    # process ids: the snapshot first, then any other readable list
    ids, source = [], None
    sources = [("the snapshot", snap)] + ([("the hand-over record", ho)] if ho else []) + \
        [("the set map", set_map), ("the run state", state)] + [(rel, d) for rel, d in graph_docs]
    for label, doc in sources:
        got, field = process_ids_in(doc)
        if got:
            ids, source = got, (label, field)
            break
    if source and source != ("the snapshot", "scope.process_ids"):
        record(reading("process ids", "read from %s (%s)" % source,
                       "the snapshot names no scope.process_ids list" if snap is not None else "no readable snapshot"))
    if not ids:
        for rel in json_files(folder):
            try:
                doc = read_json(folder, rel)
            except IntakeError:
                continue
            parsed.append(rel)
            got, field = process_ids_in(doc)
            if got:
                ids = got
                record(reading("process ids", "read from %s (%s)" % (rel, field),
                               "none of the usual inputs names a process; this readable file does"))
                break
    if not ids:
        docs = documents(folder, True)
        if not parsed and not docs:
            raise IntakeError("nothing in %s could be read: no file parses and no design document is there" % folder)
        ids = [UNNAMED]
        record(reading("process ids", "one unnamed process", "no readable file names a process"))
        needs.append(still_need("Which process this design is for: nothing supplied names one, so it is read as one "
                                "unnamed process."))
    layout = "single" if len(ids) == 1 else "set"
    if ho and ho.get("layout") is not None and ho.get("layout") != layout:
        prob("layout-disagrees", "the hand-over record declares layout %r but %d process(es) were read; the count of "
             "processes read wins (%s)" % (ho.get("layout"), len(ids), layout))
    repaired = None
    sm_ids = process_ids_in(set_map)[0] if isinstance(set_map, dict) else []
    foreign = [p for p in sm_ids if p not in ids]
    if foreign:
        shared = [p for p in sm_ids if p in ids]
        if not shared or layout != "set":
            record(reading("the set map", "Contradictory: kept as evidence only, not used as this run's set map",
                           "%s names %s, %s of which %s this run's process%s (%s)" % (
                               prefixed(sm_rel), ", ".join(sm_ids), "none" if not shared else "only " + ", ".join(shared),
                               "is" if len(ids) == 1 else "are", "" if len(ids) == 1 else "es", ", ".join(ids))))
            needs.append(still_need("The set map supplied with the run names other processes (%s) than the ones this "
                                    "design covers (%s): which set map belongs to this work?"
                                    % (", ".join(foreign), ", ".join(ids))))
            set_map, sm_rel = None, None
        else:
            # keep this run's processes and the links among them; the original stays as evidence
            def within(link):
                ends = []
                for k in ("from", "to"):
                    v = link.get(k) if isinstance(link, dict) else None
                    ends += v if isinstance(v, list) else [v] if isinstance(v, str) else []
                return all(e in ids for e in ends)
            trimmed = json.loads(json.dumps(set_map))
            trimmed["processes"] = [p for p in set_map.get("processes") or []
                                    if process_ids_in({"processes": [p]})[0][:1] and process_ids_in({"processes": [p]})[0][0] in ids]
            trimmed["links"] = [l for l in set_map.get("links") or [] if within(l)] if isinstance(set_map.get("links"), list) else []
            trimmed["repaired_from"] = {"scoped": prefixed(sm_rel), "whole": None}
            record(reading("the set map", "Contradictory: %s set aside; the map was trimmed to this run's processes and "
                           "the links among them; the original kept as evidence" % ", ".join(foreign),
                           "%s names processes this run has no snapshot entry for" % prefixed(sm_rel)))
            needs.append(still_need("The set map names processes outside this run (%s); they were left out of the "
                                    "design's set map." % ", ".join(foreign)))
            repaired = trimmed
            got_map, got, more = repair_set_map(folder, sm_rel, trimmed, ids)
            if got_map is not None:
                got_map["repaired_from"]["scoped"] = prefixed(sm_rel)
                repaired = got_map
            readings.extend(got)
            needs.extend(more)
    elif isinstance(set_map, dict) and layout == "set":
        repaired, got, more = repair_set_map(folder, sm_rel, set_map, ids)
        readings.extend(got)
        needs.extend(more)
    rscope = ho.get("recommendation_scope") if ho else None
    scope = [x for x in rscope if isinstance(x, str) and x] if isinstance(rscope, list) else []

    freeze = state.get("freeze") if isinstance(state.get("freeze"), dict) else {}
    cutoff_text = freeze.get("cutoff")
    snap_cut = ((snap.get("scope") or {}) if isinstance(snap, dict) and isinstance(snap.get("scope"), dict) else {}).get(
        "cutoff_at")
    if not (isinstance(cutoff_text, str) and cutoff_text) and isinstance(snap_cut, str) and snap_cut:
        record(reading("snapshot cutoff", "read the snapshot's cutoff %s" % snap_cut, "the run state records no freeze cutoff"))
        cutoff_text = snap_cut
    try:
        cutoff = parse_when(cutoff_text, "state.json freeze.cutoff")
    except IntakeError as e:
        cutoff = None
        prob("no-cutoff", str(e))
    stages = state.get("stages") if isinstance(state.get("stages"), dict) else {}
    if state:
        for stage in ("current-state", "transformation-plan", "future-state"):
            status = (stages.get(stage) or {}).get("status") if isinstance(stages.get(stage), dict) else None
            if not (isinstance(status, str) and (status == "done" or status.startswith("done,"))):
                prob("upstream-stage-not-done", "the %s stage is %r, not done" % (stage, status))
    versions = freeze_versions(freeze, prob)
    for p in state.get("problems") if isinstance(state.get("problems"), list) else []:
        if isinstance(p, dict):
            prob("upstream-" + str(p.get("kind") or "problem"), str(p.get("detail") or ""))
    sources_list = []
    si_rel = find("snapshot_intake")
    if si_rel:
        si = load(si_rel)
        raw = si.get("sources") if isinstance(si, dict) else None
        if si is not None and not isinstance(raw, list):
            prob("no-sources", "%s: no sources list; no observations or sources are counted" % si_rel)
        sources_list = [x for x in raw or [] if isinstance(x, dict)] if isinstance(raw, list) else []
    processes, late = [], []
    if len(ids) > 1:
        for g, rel in graphs:
            for n in g["nodes"]:
                if isinstance(n, dict) and n.get("type") not in NOT_WORK and not n.get("process_id"):
                    prob("work-node-without-process", "%s: work node %s names no process; counted under no process"
                         % (rel, n.get("id")))
    for pid in ids:
        counts = dict((x, 0) for x in STATES)
        for g, rel in graphs:
            for n in g["nodes"]:
                if not isinstance(n, dict) or n.get("type") in NOT_WORK:
                    continue
                owner = n.get("process_id") or (pid if len(ids) == 1 else None)
                if len(ids) == 1 and pid == UNNAMED:
                    owner = pid
                if owner != pid:
                    continue
                if n.get("evidence_state") in counts:
                    counts[n["evidence_state"]] += 1
                else:
                    prob("unknown-evidence-state", "%s: node %s has evidence state %r, not one of %s; not counted"
                         % (rel, n.get("id"), n.get("evidence_state"), ", ".join(STATES)))
        mine = [x for x in sources_list if x.get("process_id") == pid or (len(ids) == 1 and not x.get("process_id"))]
        source_count = sum(1 for x in mine if x.get("status") == "included" and x.get("collection_kind") not in OBSERVATION_KINDS)
        colls = [x for x in mine if x.get("collection_kind") == "observations" and x.get("status", "included") == "included"]
        count = 0
        if len(colls) != 1:
            if si_rel:
                prob("observations-sources", "process %s has %d observations sources, expected one; its observations "
                     "are not counted" % (pid, len(colls)))
        else:
            obs_rel = "inputs/" + str(colls[0].get("path"))
            if not safe_pointer(folder, obs_rel) or not os.path.isfile(os.path.join(folder, obs_rel)):
                prob("missing-file", "%s: the observations file is missing" % obs_rel)
            else:
                obs_doc = soft_json(folder, obs_rel, prob)
                items = obs_doc.get("items") if isinstance(obs_doc, dict) else None
                if obs_doc is not None and not isinstance(items, list):
                    prob("no-items", "%s: no items list; its observations are not counted" % obs_rel)
                digest = file_sha(folder, obs_rel)
                prefix = colls[0]["id"][:-len("observations")] if str(colls[0].get("id", "")).endswith("observations") else pid + ":"
                for item in items if isinstance(items, list) else []:
                    key = item.get("resource_key") if isinstance(item, dict) else None
                    try:
                        when = parse_when(item.get("timestamp"), "%s observation %s" % (obs_rel, key))
                    except (IntakeError, AttributeError) as e:
                        prob("observation-unreadable", str(e)); continue
                    if cutoff is None or when <= cutoff:
                        count += 1
                    else:
                        late.append({"process_id": pid, "observation_id": prefix + "observation:" + str(key),
                                     "observed_at": item["timestamp"], "path": prefixed(obs_rel), "sha256": digest})
        rec = {"process_id": pid, "current_state": prefixed(cur_rel) if cur_rel else None,
               "transformation_map": prefixed(tm_rel) if tm_rel else None, "future_state": prefixed(fut_rel) if fut_rel else None,
               "evidence_strength": {"observation_count": count, "source_count": source_count, "evidence_states": counts}}
        if scope:
            rec["role"] = "design" if pid in scope else "context"
            if pid not in scope:
                rec["reason"] = "outside the recommendation"
        processes.append(rec)
    try:
        failing = failing_checks(state)
    except IntakeError as e:
        failing = []
        prob("unnamed-check", str(e))
    if layout == "set" and not sm_rel:
        prob("set-map-missing", "the run is a set but carries no readable set map")
    doc = {"schema": "agent-design.intake/1", "layout": layout, "layout_source": "legacy" if generic else "declared",
           "processes": processes,
           "set_map": REPAIRED if repaired is not None else (prefixed(sm_rel) if sm_rel and set_map is not None else None),
           "snapshot": {"cutoff": cutoff_text if isinstance(cutoff_text, str) and cutoff_text else "unknown",
                        "process_versions": versions},
           "observations": {"snapshot_intake": prefixed(si_rel) if si_rel else None, "id_form": ID_FORM},
           "post_freeze_reads": [{k: r[k] for k in ("process_id", "observation_id", "observed_at", "path")} for r in late],
           "upstream_failing_checks": failing + needs, "recommendation_scope": scope,
           "upstream_problems": readings + problems}
    check_schema(doc, layout)
    if repaired is not None:
        doc[REPAIRED_KEY] = repaired   # written by build() beside the intake; never part of intake.json
    addendum = None
    if late:
        addendum = {"entries": [{
            "id": "EA-%d" % n, "what": "observation %s" % r["observation_id"],
            "why": "dated after the snapshot cutoff %s" % cutoff_text, "ids": [r["observation_id"]],
            "capture_date": r["observed_at"], "capture_proof": "observation.timestamp", "read_at": None,
            "sha256": r["sha256"], "read_file": r["path"],
            "claim": "observation %s was captured after the freeze" % r["observation_id"],
            "status": "flagged", "resolution": None, "resolved_at": None} for n, r in enumerate(late, 1)]}
    return doc, addendum


def _shared_rules():
    """The plugin's shared write-grant rules module, through the one knowledge lookup."""
    driver = os.path.join(os.path.dirname(os.path.dirname(SCRIPTS)), "fast-mode-build", "driver")
    if driver not in sys.path:
        sys.path.insert(0, driver)
    import knowledge_root  # noqa: E402
    checks = os.path.join(knowledge_root.find(), "checks")
    if checks not in sys.path:
        sys.path.insert(0, checks)
    import _write_grants  # noqa: E402
    return _write_grants


def grant_problems(fsd_folder, doc):
    """upstream_failing_checks entries for the shared write-grant rules on the upstream map and future state."""
    def local(rel):
        return os.path.join(fsd_folder, rel[len(PREFIX):] if rel.startswith(PREFIX) else rel)

    entry = lambda result, detail: {"stage": "future-state", "check": "write-grants", "result": result,
                                    "detail": detail}
    try:
        W = _shared_rules()
        procs = [p for p in doc.get("processes") or [] if isinstance(p, dict)]
        groups, docs = {}, {}
        for p in procs:
            if not (p.get("future_state") and p.get("transformation_map") and os.path.isfile(local(p["future_state"])) and os.path.isfile(local(p["transformation_map"]))):
                continue   # an older run without a map or future state: nothing to compare
            with open(local(p["future_state"]), encoding="utf-8") as f:
                fs = json.load(f)
            groups.setdefault(p["transformation_map"], []).append(W.Graph(p["process_id"], fs, in_set=len(procs) > 1))
            docs.setdefault(p["transformation_map"], fs)
        out = []
        for tm, members in groups.items():
            with open(local(tm), encoding="utf-8") as f:
                rows = W.read_table(f.read())
            if rows is None:
                continue   # no Write grants table: an older run; nothing to compare
            res = W.evaluate(members, rows, [("future-state grant_readings", docs[tm].get("grant_readings"))])
            out += [entry("fail", "%s at %s: %s" % (p["rule"], p["where"], p["detail"])) for p in res["problems"]]
            out += [entry("recorded", "%s at %s: %s (the future state recorded its reading of %s %s: %s)"
                          % (p["rule"], p["where"], p["detail"], p["reading"]["grant"], p["reading"]["column"],
                             p["reading"]["reason"])) for p in res["recorded"]]
        return out
    except Exception as e:   # the rules inform; a file they cannot read never stops the intake
        return [entry("unchecked", "the write-grant rules could not read the upstream files (%s: %s)"
                      % (type(e).__name__, e))]


def upstream_readings(fsd_folder):
    """Readings the upstream run recorded about its own freeze (state.json freeze.readings): (readings, still-need items)."""
    try:
        st = read_json(fsd_folder, "state.json")
    except IntakeError:
        return [], []
    fr = st.get("freeze") if isinstance(st, dict) and isinstance(st.get("freeze"), dict) else {}
    out, needs = [], []
    for r in fr.get("readings") if isinstance(fr.get("readings"), list) else []:
        if isinstance(r, dict) and all(isinstance(r.get(k), str) for k in ("what", "reading", "why")):
            out.append(reading("upstream " + r["what"], r["reading"], r["why"]))
            if r.get("still_need"):
                needs.append(still_need("%s: %s (%s)." % (r["what"].capitalize(), r["reading"], r["why"])))
    return out, needs


def assemble(fsd_folder):
    """Every check, nothing written; returns (intake, addendum or None)."""
    if not os.path.isdir(fsd_folder):
        raise IntakeError("%s: no such folder" % fsd_folder)
    ho, note = None, None
    if os.path.exists(os.path.join(fsd_folder, HANDOVER)):
        try:
            ho = read_json(fsd_folder, HANDOVER)
        except IntakeError as e:
            ho, note = None, {"kind": "handover-unreadable", "detail": "%s; the run was read without it (legacy)" % e}
        if ho is not None and not isinstance(ho, dict):
            note = {"kind": "handover-not-object", "detail": "%s is not an object, so it was set aside and the layout was "
                    "inferred from the older run's files (legacy)" % HANDOVER}
            ho = None
    if ho is not None:
        doc, addendum = declared(fsd_folder, ho)
    else:
        try:
            if not any(layout_markers(fsd_folder).values()):   # legacy: no hand-over record, today's inference
                doc, addendum = legacy(fsd_folder)
            else:
                doc, addendum = v01(fsd_folder)
                gap = v01_gap(fsd_folder, doc)
                if gap:
                    doc, addendum = declared(fsd_folder, {}, generic=True)
                    doc["upstream_problems"] = [reading("layout", "each input was located in the run and read", gap)] + \
                        doc["upstream_problems"]
        except InstallError:
            raise
        except IntakeError as e:
            # the older readers need their exact files; locate each input instead and read what is there
            doc, addendum = declared(fsd_folder, {}, generic=True)
            doc["upstream_problems"] = [reading("layout", "each input was located in the run and read",
                                                "the older-layout reader could not read it: %s" % e)] + doc["upstream_problems"]
        doc["layout_source"] = "legacy"
        doc["upstream_problems"] = [note or {"kind": "legacy-layout", "detail": "no hand-over record upstream; the layout was "
                                             "inferred from the older run's files (legacy)"}] + list(doc.get("upstream_problems") or [])
    fr_readings, fr_needs = upstream_readings(fsd_folder)
    docs = documents(fsd_folder)
    if docs:
        fr_readings.append(reading("design documents", "available as design evidence for the stages: %s"
                                   % ", ".join(prefixed(d) for d in docs), "text documents supplied with the run"))
    texts = {}
    for d in docs:
        if d.lower().endswith(DOCX):
            out = "%s/%s.txt" % (DOC_TEXT_DIR, d.replace("/", "__"))
            texts[out] = docx_text(os.path.join(fsd_folder, d))
            fr_readings.append(reading("design document %s" % prefixed(d), "its text extracted to %s; the original kept"
                                       % out, "a Word document is read through its text"))
    if texts:
        doc[DOC_TEXTS_KEY] = texts
    doc["upstream_problems"] = list(doc["upstream_problems"]) + fr_readings
    doc["upstream_failing_checks"] = list(doc["upstream_failing_checks"]) + fr_needs + grant_problems(fsd_folder, doc)
    return doc, addendum


def v01_gap(folder, doc):
    """Why an older-layout set read needs process recovery or set-map repair, or None: the snapshot names processes the
    set map lacks, or a whole map of the same set elsewhere in the run holds links the set map lacks."""
    try:
        snap_ids = process_ids_in(read_json(folder, "inputs/snapshot.json"))[0]
        set_map = read_json(folder, SET_MAP)
    except IntakeError:
        return None
    have = [p["process_id"] for p in doc.get("processes") or []]
    missing = [p for p in snap_ids if p not in have]
    if missing:
        return "the set map leaves out %s, which the snapshot names" % ", ".join(missing)
    extra = [p for p in have if snap_ids and p not in snap_ids]
    if extra:
        return "the set map names %s, which the snapshot does not" % ", ".join(extra)
    if isinstance(set_map, dict) and repair_set_map(folder, SET_MAP, set_map, have)[0] is not None:
        return "a whole map of the same set in the run holds links the set map lacks"
    return None


def init_readings(run_dir):
    """What `ad.py init` recorded (RUN/state.json init_readings): (readings, still-need items)."""
    try:
        st = read_json(run_dir, "state.json")
    except IntakeError as e:
        if os.path.lexists(os.path.join(run_dir, "state.json")):
            return [reading("run state", "what init recorded is not carried", str(e))], []
        return [], []
    out, needs = [], []
    for r in st.get("init_readings") if isinstance(st, dict) and isinstance(st.get("init_readings"), list) else []:
        if isinstance(r, dict) and all(isinstance(r.get(k), str) for k in ("what", "reading", "why")):
            out.append(reading(r["what"], r["reading"], r["why"]))
            if r.get("still_need"):
                needs.append(still_need("%s: %s (%s)." % (r["what"].capitalize(), r["reading"], r["why"])))
    return out, needs


def write_atomic(path, doc):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".intake-")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(json.dumps(doc, indent=2, ensure_ascii=False) + "\n")
    os.replace(tmp, path)


def build(fsd_folder, run_dir):
    """Read FSD_FOLDER, write RUN_DIR/stages/intake/; returns the intake. Raises IntakeError, having written nothing."""
    doc, addendum = assemble(fsd_folder)
    repaired = doc.pop(REPAIRED_KEY, None)
    texts = doc.pop(DOC_TEXTS_KEY, None) or {}
    got, needs = init_readings(run_dir)
    if got or needs:
        doc["upstream_problems"] = got + list(doc["upstream_problems"])
        doc["upstream_failing_checks"] = list(doc["upstream_failing_checks"]) + needs
        check_schema(doc, doc["layout"])
    out = os.path.join(run_dir, OUT_DIR)
    write_atomic(os.path.join(out, INTAKE), doc)
    if repaired is not None:
        write_atomic(os.path.join(run_dir, REPAIRED), repaired)
    for rel, text in texts.items():
        os.makedirs(os.path.dirname(os.path.join(run_dir, rel)), exist_ok=True)
        with open(os.path.join(run_dir, rel), "w", encoding="utf-8") as f:
            f.write(text + "\n")
    stale = os.path.join(out, ADDENDUM)
    if addendum is not None:
        write_atomic(stale, addendum)
    elif os.path.exists(stale):
        os.remove(stale)
    return doc


def main(argv=None):
    ap = argparse.ArgumentParser(description="Read a finished Future State Design run into the intake.")
    ap.add_argument("--run", required=True, help="the Agent Design run folder")
    ap.add_argument("--from", dest="source", help="the Future State Design run folder (default RUN/upstream)")
    a = ap.parse_args(argv)
    source = a.source or os.path.join(a.run, "upstream")
    try:
        doc = build(source, a.run)
    except IntakeError as e:
        print("intake stopped: %s" % e, file=sys.stderr)
        return 2
    print("intake: layout %s, %d process(es), written to %s" % (doc["layout"], len(doc["processes"]),
                                                               os.path.join(OUT_DIR, INTAKE)))
    for p in doc.get("upstream_problems") or []:
        print("upstream problem, recorded: %s" % p["kind"])
    for c in doc["upstream_failing_checks"]:
        print("upstream check failing, carried forward: stage %s, check %s, result %s"
              % (c["stage"], c["check"], c["result"]))
    if doc["post_freeze_reads"]:
        print("post-freeze reads: %d, recorded in %s" % (len(doc["post_freeze_reads"]), os.path.join(OUT_DIR, ADDENDUM)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
