#!/usr/bin/env python3
"""A3 future-state check: carried context and platform-neutral agent steps (PC36 §3, Pins §8).

    python3 check-a3-future-context-platform.py --run RUN_ROOT --stage future-state [--file PATH] [--current PATH]

Reads, relative to RUN_ROOT unless given:
  future   stages/future-state/future-state.input.json   (--file)
  current  stages/current-state/current-state.input.json (--current)
  direction inputs/design-direction.json (optional; its `process_ids`)

Rules (fields only, no word lists):
  context-dropped   a current `object_state` or `context_neighbour` node has no future node of the same
                    type carrying it (same id, or its id in `future.from`). A dropped `context_neighbour`
                    passes only when the future state draws its `neighbour.process_id` as a process frame
                    and the design direction's `process_ids` brings that process in scope.
  platform-chosen   a node in an `ai_and_agents` lane carries a non-null `platform` or `runtime` key at
                    node level or inside `future`.

Every field the rules read is shape-checked first (`validate`); a malformed input is a setup error.
Exit 0 pass, 1 fail, 2 setup error. Stdout is one JSON object: {check, result, findings, flags}.
"""
import argparse
import json
import os
import sys

CHECK = "check-a3-future-context-platform"
CARRIED_TYPES = ("object_state", "context_neighbour")
PLATFORM_KEYS = ("platform", "runtime")


def _load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _graph(bundle, name):
    g = bundle.get("graph") if isinstance(bundle, dict) else None
    if not isinstance(g, dict) or not isinstance(g.get("nodes"), list):
        raise ValueError("%s has no graph.nodes list" % name)
    return g


def _is_id(v):
    return isinstance(v, str) and v != ""


def _ids_list(v):
    return isinstance(v, list) and all(_is_id(x) for x in v)


def validate(current, future, direction):
    """Check the shape of every field the rules read; anything outside it is a setup error, never a pass."""
    if not isinstance(current, dict) or not isinstance(future, dict):
        raise ValueError("the current and future inputs must each be a JSON object")
    for name, bundle in (("current state", current), ("future state", future)):
        for i, n in enumerate(_graph(bundle, name)["nodes"]):
            if not isinstance(n, dict) or not _is_id(n.get("id")) or not _is_id(n.get("type")):
                raise ValueError("%s graph.nodes[%d] is not an object with string id and type" % (name, i))
    g = _graph(future, "future state")
    lanes = g.get("lanes")
    if not isinstance(lanes, list) or not all(isinstance(l, dict) and _is_id(l.get("id")) and _is_id(l.get("band"))
                                              for l in lanes):
        raise ValueError("future state graph.lanes must be a list of objects with string id and band")
    lane_ids = {l["id"] for l in lanes}
    frames = g.get("process_frames", [])
    if not isinstance(frames, list) or not all(isinstance(f, dict) and _is_id(f.get("process_id")) for f in frames):
        raise ValueError("future state graph.process_frames must be a list of objects with a string process_id")
    for n in g["nodes"]:
        if n.get("lane_id") not in lane_ids:
            raise ValueError("future node %s has a lane_id that is not a declared lane" % n["id"])
        fut = n.get("future")
        if not isinstance(fut, dict) or not _ids_list(fut.get("from")):
            raise ValueError("future node %s needs a `future` object whose `from` is a list of ids" % n["id"])
    for n in _graph(current, "current state")["nodes"]:
        if n["type"] == "context_neighbour":
            nb = n.get("neighbour")
            if not isinstance(nb, dict) or not _is_id(nb.get("process_id")):
                raise ValueError("current context neighbour %s needs a `neighbour` object with a string process_id" % n["id"])
    if direction is not None and (not isinstance(direction, dict) or not _ids_list(direction.get("process_ids"))):
        raise ValueError("design-direction.json must be an object whose `process_ids` is a list of ids")


def check_context(current, future, direction):
    findings, flags = [], []
    fut_nodes = [n for n in _graph(future, "future state")["nodes"] if isinstance(n, dict)]
    carried = {}
    for n in fut_nodes:
        ids = [n.get("id")] + list(((n.get("future") or {}).get("from")) or [])
        for i in ids:
            carried.setdefault((n.get("type"), i), n.get("id"))
    frames = {f.get("process_id") for f in _graph(future, "future state").get("process_frames") or [] if isinstance(f, dict)}
    in_scope = set((direction or {}).get("process_ids") or [])
    for n in _graph(current, "current state")["nodes"]:
        if not isinstance(n, dict) or n.get("type") not in CARRIED_TYPES:
            continue
        nid, kind = n.get("id"), n.get("type")
        if (kind, nid) in carried:
            continue
        if kind == "context_neighbour":
            pid = (n.get("neighbour") or {}).get("process_id")
            if pid and pid in frames and pid in in_scope:
                flags.append({"rule": "context-promoted", "where": nid,
                              "detail": "context neighbour %s is drawn as frame %s; the design direction brings it in scope" % (nid, pid)})
                continue
            if pid and pid in frames:
                findings.append({"rule": "context-dropped", "where": nid,
                                 "detail": "context neighbour %s became frame %s, but the design direction does not bring %s in scope" % (nid, pid, pid)})
                continue
        findings.append({"rule": "context-dropped", "where": nid,
                         "detail": "current %s %s is missing from the future state" % (kind, nid)})
    return findings, flags


def check_platform(future):
    findings = []
    g = _graph(future, "future state")
    agent_lanes = {l.get("id") for l in g.get("lanes") or [] if isinstance(l, dict) and l.get("band") == "ai_and_agents"}
    for n in g["nodes"]:
        if not isinstance(n, dict) or n.get("lane_id") not in agent_lanes:
            continue
        for where, holder in (("node", n), ("future", n.get("future"))):
            if not isinstance(holder, dict):
                continue
            for key in PLATFORM_KEYS:
                if holder.get(key) is not None:
                    findings.append({"rule": "platform-chosen", "where": n.get("id"),
                                     "detail": "agent-lane node %s carries %s `%s` (agent steps stay platform-neutral)"
                                               % (n.get("id"), "node-level" if where == "node" else "future.", key)})
    return findings


def run(args):
    root = args.run
    fut_path = args.file or os.path.join(root, "stages", "future-state", "future-state.input.json")
    cur_path = args.current or os.path.join(root, "stages", "current-state", "current-state.input.json")
    dir_path = os.path.join(root, "inputs", "design-direction.json")
    if args.stage != "future-state":
        raise ValueError("this check runs at stage future-state, not %r" % args.stage)
    future, current = _load(fut_path), _load(cur_path)
    if not isinstance(future, dict) or future.get("variant") != "future-state":
        raise ValueError("%s is not a future-state bundle" % fut_path)
    direction = _load(dir_path) if os.path.isfile(dir_path) else None
    validate(current, future, direction)
    findings, flags = check_context(current, future, direction)
    findings += check_platform(future)
    return findings, flags


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--run", required=True)
    ap.add_argument("--stage", required=True)
    ap.add_argument("--file")
    ap.add_argument("--current")
    args = ap.parse_args(argv)
    try:
        findings, flags = run(args)
    except (OSError, ValueError, AttributeError) as e:
        print(json.dumps({"check": CHECK, "result": "setup-error",
                          "findings": [{"rule": "setup", "where": "", "detail": str(e)}], "flags": []}, indent=1))
        return 2
    result = "fail" if findings else "pass"
    print(json.dumps({"check": CHECK, "result": result, "findings": findings, "flags": flags}, indent=1))
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
