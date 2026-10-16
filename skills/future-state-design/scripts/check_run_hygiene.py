#!/usr/bin/env python3
"""Run hygiene: lanes, stage-folder files, internal ids in displayed note text and portable pin paths.

Usage: check_run_hygiene.py --run RUN_ROOT --stage KEY [--file PATH] [--part lanes,files,ids,paths]

Parts (comma-separated; default all four):
  lanes  An empty people lane, or a lane or band the renderer does not know, fails. Single-process: the bands
         renderer always draws the Analyst band, so a graph with no people step there fails. In a future state an
         `Agent: <name>` lane fails and more than one agent lane fails. Which steps are the agent's is never decided
         here: knowledge/checks/_agent_steps.py (the rule Agent Design uses) says so, and an Agent-lane step is the
         agent's with or without a tag. A step it owns from the Agent lane alone, with no `agent` tag, is a
         non-blocking `lanes.agent-tag` note; a people-lane step tagged agent is a `lanes.ambiguous-owner` note.
  files  Every file under stages/KEY/ must be on the stage's allow-list (primaries, rendered files,
         `<stem>.layout.json`, `reshape-check.json`). On a graph stage, the files the renderer writes on a
         re-render into a fresh name, `<stage>-<suffix>.json|.svg|.html|.layout.json` (suffix: lowercase letters,
         digits and hyphens, e.g. `current-state-repaired.svg`), are accepted, each with a `rerender-file-accepted`
         note; any other file still fails.
  ids    A node's or edge's `label`, `display_label` or `notes` must not contain a snapshot source or issue
         id, a node id, `RM-<n>`, `WG-<n>`, `DD-<n>` or a 12-hex process key, anywhere inside the text (also
         when joined to other characters, e.g. `source=src-x_suffix`). A node id shorter than
         WHOLE_TOKEN_BELOW characters is matched as a whole token only (no letter or digit either side), so
         `b1` in a note fails but a word such as `tab1e` does not.
  paths  Every pin path (the `path` of the snapshot, set map, current state and prior workflow pins) in
         each stages/KEY/*.input.json must be relative, never absolute, so the run folder can move. An
         absolute path (`/...`, `~...`, `C:\\...`, `file:...`) fails. A missing pin or a non-string path is not
         this part's concern; no input file, or one that does not parse, is a setup error.

Reads fields only: lane names, bands and ids are structural values, never a word list. The graph is read from
--file, else stages/KEY/KEY.json (rendered bundle), else stages/KEY/KEY.input.json. An A3 input is one whose
graph carries `lanes[]`. Exit 0 pass, 1 fail, 2 setup error; stdout is one JSON object (check, result, findings,
flags, notes); notes never fail the run. Stdlib only.
"""
import argparse, glob, json, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(HERE)), "fast-mode-build", "driver"))
import knowledge_root  # noqa: E402  the one lookup for the plugin's knowledge folder
sys.path.insert(0, os.path.join(knowledge_root.find(), "checks"))
import _agent_steps  # noqa: E402  the one rule for which steps the agent owns (Agent Design asks it too)

CHECK = "run-hygiene"
PARTS = ("lanes", "files", "ids", "paths")
STAGES = ("set-map", "current-state", "transformation-plan", "future-state")
GRAPH_STAGES = ("current-state", "future-state")

# Single-process: the coarse lanes the bands renderer knows (the same KNOWN_LANES as knowledge/checks).
KNOWN_LANES = ("Analyst", "Agent", "System")
PEOPLE_LANE = "Analyst"
AGENT_LANE_PREFIX = "Agent: "
# A3 (format v0.1): the four bands, in order.
BANDS = ("people", "ai_and_agents", "system_of_record", "external")

ALLOW = {
    "set-map": {"set-map.json"},
    "current-state": {"current-state.input.json", "current-state.json", "current-state.svg",
                      "current-state.html", "current-state.layout.json"},
    "transformation-plan": {"transformation-map.md"},
    "future-state": {"future-state.input.json", "future-state.json", "future-state.svg",
                     "future-state.html", "future-state.layout.json", "reshape-check.json"},
}

# The renderer refuses to overwrite and a repair renders into a fresh name: <stage>-<suffix>.<rendered kind>.
RERENDER_KINDS = (".layout.json", ".json", ".svg", ".html")
RERENDER_SUFFIX = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

FIXED_ID_PATTERNS = (("RM-<n>", r"RM-\d+"), ("WG-<n>", r"WG-\d+"), ("DD-<n>", r"DD-\d+"),
                     ("12-hex process key", r"[0-9a-fA-F]{12}"))
DISPLAYED = ("label", "display_label", "notes")
WHOLE_TOKEN_BELOW = 4
PINS = ("snapshot", "set_map", "current_state", "prior_workflow")
DRIVE_PATH = re.compile(r"^[A-Za-z]:[\\/]")


class SetupError(Exception):
    pass


def is_agent_lane(lane):
    if lane == "Agent":
        return True
    return isinstance(lane, str) and lane.startswith(AGENT_LANE_PREFIX) and bool(lane[len(AGENT_LANE_PREFIX):].strip())


def is_agent_mode(n):
    # An ai_use whose ai_use.mode is "agent" is an agent; mode "assistant" is a tool a person uses.
    use = n.get("ai_use")
    return isinstance(use, dict) and use.get("mode") == "agent"


def lane_kind(lane):
    return "Agent" if is_agent_lane(lane) else lane


def finding(rule, where, detail):
    return {"rule": rule, "where": where, "detail": detail}


def load_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError) as e:
        raise SetupError("cannot read %s: %s" % (path, e))


def graph_file(run, stage, given):
    if given:
        if not os.path.isfile(given):
            raise SetupError("no such file: %s" % given)
        return given
    for name in (stage + ".json", stage + ".input.json"):
        p = os.path.join(run, "stages", stage, name)
        if os.path.isfile(p):
            return p
    raise SetupError("stage %s has neither %s.json nor %s.input.json" % (stage, stage, stage))


def load_graph(run, stage, given, findings=None):
    """(bundle, graph); graph is None when the file parses but carries no graph.nodes[] (a recorded finding).

    A missing or unparseable file raises SetupError (exit 2); a wrong shape or variant is a finding."""
    findings = [] if findings is None else findings
    if stage not in GRAPH_STAGES:
        raise SetupError("parts lanes and ids need a graph stage (%s), not %s" % (", ".join(GRAPH_STAGES), stage))
    path = graph_file(run, stage, given)
    bundle = load_json(path)
    rel = os.path.relpath(path, run)
    graph = bundle.get("graph") if isinstance(bundle, dict) else None
    if not isinstance(graph, dict) or not isinstance(graph.get("nodes"), list):
        findings.append(finding("graph-shape", rel, "the file carries no graph.nodes[]; lanes and ids are not checked"))
        return bundle, None
    variant = bundle.get("variant")
    if variant is not None and variant != stage:
        findings.append(finding("graph-variant", rel, "variant %r but the stage is %s" % (variant, stage)))
    return bundle, graph


def nodes_of(graph):
    return [n for n in graph["nodes"] if isinstance(n, dict)]


def check_lanes(bundle, graph, future, notes=None):
    out, flags = [], []
    notes = [] if notes is None else notes
    nodes = nodes_of(graph)
    lanes = graph.get("lanes")
    if lanes is not None:  # A3 input
        if not isinstance(lanes, list):
            return [finding("lanes.unknown-band", "graph.lanes", "lanes is not a list")], flags
        used = {n.get("lane_id") for n in nodes}
        agent_lanes = []
        for i, ln in enumerate(lanes):
            ln = ln if isinstance(ln, dict) else {}
            where = "graph.lanes[%d] %s" % (i, ln.get("id"))
            band = ln.get("band")
            if band not in BANDS:
                out.append(finding("lanes.unknown-band", where, "band %r is not one of %s" % (band, ", ".join(BANDS))))
            if band == "people" and ln.get("id") not in used:
                out.append(finding("lanes.empty-people-lane", where, "people lane has no step"))
            if band == "ai_and_agents" or is_agent_lane(ln.get("lane")):
                agent_lanes.append(ln.get("id"))
            if future and isinstance(ln.get("lane"), str) and ln["lane"].startswith(AGENT_LANE_PREFIX):
                out.append(finding("lanes.named-agent-lane", where,
                                   "future state lane %r: agents share the one Agent lane, told apart by the agent tag" % ln["lane"]))
        known_ids = {ln.get("id") for ln in lanes if isinstance(ln, dict)}
        for n in nodes:
            if n.get("lane_id") not in known_ids:
                out.append(finding("lanes.unknown-lane", "node %s" % n.get("id"),
                                   "lane_id %r is not in graph.lanes[]" % n.get("lane_id")))
        agent_ids = set(agent_lanes)
        is_agent_node = lambda n: n.get("lane_id") in agent_ids or is_agent_lane(n.get("lane"))
    else:  # single-process bundle on the bands renderer
        agent_lanes = []
        for n in nodes:
            lane = n.get("lane")
            if lane_kind(lane) not in KNOWN_LANES:
                out.append(finding("lanes.unknown-lane", "node %s" % n.get("id"),
                                   "lane %r is not one of %s" % (lane, ", ".join(KNOWN_LANES))))
            if is_agent_lane(lane) and lane not in agent_lanes:
                agent_lanes.append(lane)
        # The bands renderer always draws the Analyst band on a current or future state, and only steps that are
        # not system surfaces sit in a band; so a drawn people band with none of those steps is empty.
        stepped = {n.get("lane") for n in nodes if n.get("type") != "system_surface"}
        if PEOPLE_LANE not in stepped:
            out.append(finding("lanes.empty-people-lane", "band %s" % PEOPLE_LANE,
                               "the diagram draws the %s band but no people step sits in it" % PEOPLE_LANE))
        if future:
            for lane in agent_lanes:
                if lane.startswith(AGENT_LANE_PREFIX):
                    out.append(finding("lanes.named-agent-lane", "lane %s" % lane,
                                       "future state lane %r: agents share the one Agent lane, told apart by the agent tag" % lane))
        is_agent_node = lambda n: is_agent_lane(n.get("lane"))
    if future:
        if len(agent_lanes) > 1:
            out.append(finding("lanes.agent-lanes", "graph", "future state has %d agent lanes (%s); exactly one is allowed"
                               % (len(agent_lanes), ", ".join(map(str, agent_lanes)))))
        # Ownership is the shared rule's; an owned step without a tag is a note, never a failure.
        own = _agent_steps.agent_steps(graph, default_process="(graph)")
        for nid, refs in own.by_node.items():
            n = own.nodes.get(nid) or {}
            if not (isinstance(n.get("agent"), str) and n["agent"].strip()):
                notes.append(finding("lanes.agent-tag", "node %s" % nid,
                                     "agent step (Agent lane) carries no agent tag; it is still the agent's step, "
                                     "and naming its agent helps Agent Design tell agents apart"))
        for f in list(own.findings) + list(getattr(own, "notes", [])):
            if f["code"] == "ambiguous-owner":
                notes.append(finding("lanes.ambiguous-owner", f["where"], f["detail"]))
    return out, flags


def rerender_name(stage, rel):
    """True when rel is a file the renderer writes on a re-render of graph stage `stage` into a fresh name."""
    if stage not in GRAPH_STAGES or os.sep in rel or "/" in rel:
        return False
    prefixes = set(GRAPH_STAGES) & ({stage} | {a.split(".")[0] for a in ALLOW.get(stage, ())})
    for prefix in prefixes:
        if not rel.startswith(prefix + "-"):
            continue
        rest = rel[len(prefix) + 1:]
        for kind in RERENDER_KINDS:
            if rest.endswith(kind) and RERENDER_SUFFIX.match(rest[:-len(kind)]):
                return True
    return False


def check_files(run, stage, notes=None):
    folder = os.path.join(run, "stages", stage)
    if not os.path.isdir(folder):
        raise SetupError("no stage folder: %s" % folder)
    notes = [] if notes is None else notes
    out = []
    for root, dirs, files in os.walk(folder):
        dirs.sort()
        for name in sorted(files):
            rel = os.path.relpath(os.path.join(root, name), folder)
            if rel not in ALLOW[stage] and rerender_name(stage, rel):
                notes.append(finding("rerender-file-accepted", "stages/%s/%s" % (stage, rel.replace(os.sep, "/")),
                                     "a re-render of the %s graph into a fresh name; accepted" % stage))
            elif rel not in ALLOW[stage]:
                out.append(finding("files.allow-list", "stages/%s/%s" % (stage, rel.replace(os.sep, "/")),
                                   "not on the %s stage allow-list" % stage))
    return out


def snapshot_ids(run):
    p = os.path.join(run, "inputs", "snapshot.json")
    if not os.path.isfile(p):
        raise SetupError("no inputs/snapshot.json in the run")
    snap = load_json(p)
    ids = set()
    for key, kind in (("sources", "snapshot source id"), ("issues", "snapshot issue id")):
        for item in snap.get(key) or [] if isinstance(snap, dict) else []:
            if isinstance(item, dict) and isinstance(item.get("id"), str) and item["id"]:
                ids.add((kind, item["id"]))
    return ids


def displayed_texts(item):
    for field in DISPLAYED:
        v = item.get(field)
        if isinstance(v, str):
            yield field, v
        elif isinstance(v, list):
            for i, t in enumerate(v):
                if isinstance(t, str):
                    yield "%s[%d]" % (field, i), t


def check_ids(run, graph):
    nodes = nodes_of(graph)
    named = sorted(snapshot_ids(run))
    patterns = [(kind, re.compile(re.escape(v))) for kind, v in named]
    for n in nodes:
        nid = n.get("id")
        if isinstance(nid, str) and nid:
            rx = re.escape(nid) if len(nid) >= WHOLE_TOKEN_BELOW else r"(?<![A-Za-z0-9])%s(?![A-Za-z0-9])" % re.escape(nid)
            patterns.append(("node id", re.compile(rx)))
    patterns += [(kind, re.compile(rx)) for kind, rx in FIXED_ID_PATTERNS]
    out = []
    items = [("node", n) for n in nodes] + [("edge", e) for e in graph.get("edges") or [] if isinstance(e, dict)]
    for what, item in items:
        for field, text in displayed_texts(item):
            for kind, rx in patterns:
                m = rx.search(text)
                if m:
                    out.append(finding("ids.displayed-note", "%s %s %s" % (what, item.get("id"), field),
                                       "displayed text carries a %s: %s" % (kind, m.group(0))))
    return out


def is_absolute(p):
    return os.path.isabs(p) or p.startswith("~") or bool(DRIVE_PATH.match(p)) or p.startswith("file:")


def check_paths(run, stage):
    if stage not in GRAPH_STAGES:
        raise SetupError("part paths needs a graph stage (%s), not %s" % (", ".join(GRAPH_STAGES), stage))
    files = sorted(glob.glob(os.path.join(run, "stages", stage, "*.input.json")))
    if not files:
        raise SetupError("stage %s has no *.input.json" % stage)
    out = []
    for path in files:
        bundle = load_json(path)
        if not isinstance(bundle, dict):
            out.append(finding("input-shape", "stages/%s/%s" % (stage, os.path.basename(path)),
                               "the file is not a JSON object; its pins are not checked"))
            continue
        for key in PINS:
            pin = bundle.get(key)
            p = pin.get("path") if isinstance(pin, dict) else None
            if isinstance(p, str) and is_absolute(p):
                out.append(finding("pin-path-absolute", "stages/%s/%s %s.path" % (stage, os.path.basename(path), key),
                                   "%s.path is absolute (%s); write it relative, never absolute" % (key, p)))
    return out


def run_check(run, stage, given, parts, notes=None):
    findings, flags = [], []
    notes = [] if notes is None else notes
    if not os.path.isdir(run):
        raise SetupError("no run folder: %s" % run)
    if stage not in STAGES:
        raise SetupError("unknown stage %r" % stage)
    graph = None
    if "lanes" in parts or "ids" in parts:
        bundle, graph = load_graph(run, stage, given, findings)
        if graph is None:
            parts = [p for p in parts if p not in ("lanes", "ids")]
    if "lanes" in parts:
        f, fl = check_lanes(bundle, graph, stage == "future-state", notes)
        findings += f
        flags += fl
    if "files" in parts:
        findings += check_files(run, stage, notes)
    if "ids" in parts:
        findings += check_ids(run, graph)
    if "paths" in parts:
        findings += check_paths(run, stage)
    return findings, flags


def main(argv=None):
    ap = argparse.ArgumentParser(prog="check_run_hygiene.py")
    ap.add_argument("--run", required=True)
    ap.add_argument("--stage", required=True)
    ap.add_argument("--file")
    ap.add_argument("--part", default=",".join(PARTS))
    a = ap.parse_args(argv)
    parts = [p.strip() for p in a.part.split(",") if p.strip()]
    try:
        bad = [p for p in parts if p not in PARTS]
        if bad or not parts:
            raise SetupError("unknown part(s) %s; use %s" % (", ".join(bad) or "(none)", "|".join(PARTS)))
        notes = []
        findings, flags = run_check(a.run, a.stage, a.file, parts, notes)
    except SetupError as e:
        print(json.dumps({"check": CHECK, "result": "setup-error", "findings": [finding("setup", a.run, str(e))], "flags": [],
                          "notes": []}))
        return 2
    print(json.dumps({"check": CHECK, "result": "fail" if findings else "pass", "findings": findings, "flags": flags,
                      "notes": notes}, indent=1))
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
