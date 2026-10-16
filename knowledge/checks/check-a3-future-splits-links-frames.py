#!/usr/bin/env python3
"""A3 future-state check: splits, set links and frames (unit F1c, run 0450; PC36 §3).

    python3 check-a3-future-splits-links-frames.py --run RUN_ROOT --stage future-state [--file PATH]

Reads fields only, never words:
- `stages/future-state/future-state.input.json` (or `--file`): the future-state bundle;
- `stages/current-state/current-state.input.json`: the current state it was designed from;
- `inputs/set-map.json`: the canonical set map;
- `stages/transformation-plan/transformation-map.md`: the `## Set links` table.

Fails when:
- split-unresolved: a `future.split_pair` id names no node of the future state;
- split-one-way: the node a `split_pair` names does not list the first node back;
- set-link-missing: a set-map flow link has no `## Set links` row (Link cell = the link id). A link is a
  flow link when its `kind` is a flow kind (set-map v0.1); the optional `flow` field is derived from `kind`;
- frame-dropped: a current `process_frames[].process_id` is not a future frame;
- spans-missing: a cross-process step has no `future.spans`. A step is cross-process when its own
  `process_id` and the `process_id`s of the current nodes in its `future.from` name two or more frames
  (the set-level value `set` and empty values are not frames);
- spans-unresolved: a `future.spans` id is not a future frame.
- table-shape: strict table rule v2 in the first `## Set links` section outside fences (each region, the
  heading's and each subsection's, to the next heading of any level): a line holding a pipe outside inline code
  (`\\|` is not a pipe) outside the region's one block of lines that start and end with `|`, a second block, or
  a block without header and separator fails, naming the line.
Flags (never fail): a cross-process step whose `spans` leaves out a frame it draws from; a `## Set links`
table whose header differs from the pinned one; a `## Set links` row naming no set-map flow link.

Each set-map link must have a non-empty string `id`, a `kind` from the v0.1 enum and, when present, a
boolean `flow` equal to the one derived from `kind`; any other shape is a setup error, never a pass.

Exit 0 pass, 1 fail, 2 setup error. Stdout is one JSON object (Pins §8).
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _md_blocks  # noqa: E402  fenced blocks, comments and code spans: one rule for every reader

CHECK = "a3-future-splits-links-frames"
STAGE = "future-state"
FUTURE_INPUT = os.path.join("stages", "future-state", "future-state.input.json")
CURRENT_INPUT = os.path.join("stages", "current-state", "current-state.input.json")
SET_MAP = os.path.join("inputs", "set-map.json")
MAP = os.path.join("stages", "transformation-plan", "transformation-map.md")
SET_LINKS_HEADING = "## Set links"
SET_LINKS_HEADER = ["Link", "Kind", "Current", "Future"]
SET_FRAME = "set"
FLOW_KINDS = {"handoff", "variant_entry", "parent_child", "lifecycle_continuation", "decision_gate", "re_entry"}
NON_FLOW_KINDS = {"overlap", "analogous_work", "shared_knowledge", "shared_team_or_system"}


class SetupError(Exception):
    pass


def _load_json(path, what):
    if not os.path.isfile(path):
        raise SetupError("%s not found: %s" % (what, path))
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError) as exc:
        raise SetupError("%s is not readable JSON: %s (%s)" % (what, path, exc))


def _graph(bundle, what):
    graph = bundle.get("graph") if isinstance(bundle, dict) else None
    if not isinstance(graph, dict) or not isinstance(graph.get("nodes"), list):
        raise SetupError("%s has no graph.nodes list" % what)
    return graph


def _frames(graph):
    return [f.get("process_id") for f in graph.get("process_frames") or [] if isinstance(f, dict)]


def _cells(line):
    """Cells of a table line; `\\|` is an escaped pipe, kept as `|` inside its cell."""
    text = line.strip().replace("\\|", "\0").strip("|")
    return [c.strip().strip("`").strip().replace("\0", "|") for c in text.split("|")]


def _set_links_section(text):
    """(table line indexes, problems) of the first `## Set links` section outside fences; ([], []) when it is
    absent. Strict table rule v2 (see _v2_region): the table is the block of the heading's own region; a problem
    is (1-based line number, line)."""
    table, problems = _v2_section(text.splitlines(), SET_LINKS_HEADING)
    return table or [], problems



def _v2_regions(lines, heading):
    """Strict table rule v2: (fence mask, [(start, end)] regions) of the first `heading` line outside fences, or
    (mask, None) when absent. Fenced blocks and HTML comments (_md_blocks) are tracked from the document start. The section's regions are the heading's
    own (to the next heading of any level) and one per subsection heading, up to the next heading of the same or
    higher level."""
    mask = _md_blocks.mask(lines)
    head = next((i for i, l in enumerate(lines) if not mask[i] and _md_blocks.is_heading_line(l, heading)), None)
    if head is None:
        return mask, None
    level = len(heading) - len(heading.lstrip("#"))
    heads = [head]
    for i in range(head + 1, len(lines)):
        stripped = lines[i].lstrip(" ")
        hashes = len(stripped) - len(stripped.lstrip("#"))
        if not mask[i] and len(lines[i]) - len(stripped) <= 3 and 1 <= hashes <= 6 \
                and stripped[hashes:hashes + 1] in ("", " ", "\t"):
            if hashes <= level:
                heads.append(i)
                break
            heads.append(i)
    else:
        heads.append(len(lines))
    return mask, [(heads[n] + 1, heads[n + 1]) for n in range(len(heads) - 1)]


def _v2_region(lines, start, end, mask):
    """(block, problem indexes) of one region: its pipe-bearing lines (a pipe outside inline code; `\\|` is not a
    pipe) form at most one contiguous block of lines that start and end with `|`, whose first two lines are a header
    and a separator (`|---|...`). Every other pipe line fails, and so does the first line of a block without them."""
    def has_pipe(line):
        return _md_blocks.has_pipe(line)

    def is_row(line):
        text = line.strip().replace("\\|", "")
        return len(text) > 1 and text.startswith("|") and text.endswith("|")

    blocks, current = [], None
    for i in range(start, end):
        if not mask[i] and is_row(lines[i]):
            if current is None:
                current = []
                blocks.append(current)
            current.append(i)
        else:
            current = None
    block = blocks[0] if blocks else []
    bad = [i for i in range(start, end) if not mask[i] and has_pipe(lines[i]) and i not in block]
    separator = block[1:2] and lines[block[1]].strip()
    if block and not (separator and all(c.strip().strip(":") and set(c.strip().strip(":")) == {"-"}
                                        for c in separator[1:-1].split("|"))):
        bad.append(block[0])
    return block, sorted(bad)


def _v2_section(lines, heading):
    """(table line indexes of the heading's own region, problems [(1-based line number, line)]), or (None, [])
    when the section is absent."""
    mask, regions = _v2_regions(lines, heading)
    if regions is None:
        return None, []
    table, bad = None, []
    for start, end in regions:
        block, problems = _v2_region(lines, start, end, mask)
        if table is None:
            table = [] if block and block[0] in problems else block
        bad += problems
    return table, [(i + 1, lines[i].strip()) for i in sorted(bad)]


def set_links_problems(text):
    """[(line number, line)] of `## Set links` lines holding a pipe outside the section's one table."""
    return _set_links_section(text)[1]


def set_links_table(text):
    """(header cells or None, [row cells]) of the table under `## Set links`."""
    header, rows = None, []
    lines = text.splitlines()
    for i in _set_links_section(text)[0]:
        cells = _cells(lines[i])
        if all(c and set(c) <= set("-: ") for c in cells):
            continue
        if header is None:
            header = cells
        else:
            rows.append(cells)
    return header, rows


def check_splits(nodes, findings):
    by_id = {n.get("id"): n for n in nodes}
    for node in nodes:
        nid = node.get("id")
        future = node.get("future") or {}
        for other in future.get("split_pair") or []:
            target = by_id.get(other)
            if target is None:
                findings.append({"rule": "split-unresolved", "where": nid,
                                 "detail": "split_pair id %r names no node of the future state" % other})
                continue
            back = (target.get("future") or {}).get("split_pair") or []
            if nid not in back:
                findings.append({"rule": "split-one-way", "where": nid,
                                 "detail": "split_pair names %r, whose split_pair does not name %r back"
                                 % (other, nid)})


def _is_flow(link):
    """Flow membership from `kind`; raises SetupError on any link outside the v0.1 shape."""
    if not isinstance(link, dict):
        raise SetupError("set-map link is not an object: %r" % (link,))
    lid, kind = link.get("id"), link.get("kind")
    if not isinstance(lid, str) or not lid:
        raise SetupError("set-map link has no string id: %r" % (link,))
    if not isinstance(kind, str) or kind not in FLOW_KINDS | NON_FLOW_KINDS:
        raise SetupError("set-map link %r has kind %r, outside the set-map v0.1 kinds" % (lid, kind))
    derived = kind in FLOW_KINDS
    if "flow" in link and link["flow"] is not derived:
        raise SetupError("set-map link %r has flow %r, but kind %r derives flow %r" % (lid, link["flow"], kind, derived))
    return derived


def check_set_links(set_map, map_text, findings, flags):
    links = set_map.get("links") if isinstance(set_map, dict) else None
    if not isinstance(links, list):
        raise SetupError("set map has no links list")
    flow_ids = [lk["id"] for lk in links if _is_flow(lk)]
    for number, line in set_links_problems(map_text):
        findings.append({"rule": "table-shape", "where": "%s:%d" % (MAP, number),
                         "detail": "%s line %d holds a pipe outside the section's one table of rows with both "
                         "outer pipes: %r" % (SET_LINKS_HEADING, number, line)})
    header, rows = set_links_table(map_text)
    if header is not None and header != SET_LINKS_HEADER:
        flags.append({"rule": "set-links-header", "where": MAP,
                      "detail": "header is %r, pinned header is %r" % (header, SET_LINKS_HEADER)})
    carried = {row[0] for row in rows if row}
    for lid in flow_ids:
        if lid not in carried:
            findings.append({"rule": "set-link-missing", "where": lid,
                             "detail": "set-map flow link %r has no row in the map's %s table"
                             % (lid, SET_LINKS_HEADING)})
    for lid in sorted(carried - set(flow_ids)):
        flags.append({"rule": "set-link-unknown", "where": lid,
                      "detail": "%s row %r names no set-map flow link" % (SET_LINKS_HEADING, lid)})


def check_frames(current_graph, future_graph, findings, flags):
    future_frames = set(_frames(future_graph))
    for pid in _frames(current_graph):
        if pid not in future_frames:
            findings.append({"rule": "frame-dropped", "where": pid,
                             "detail": "current frame %r is not a frame of the future state" % pid})
    current_pid = {n.get("id"): n.get("process_id") for n in current_graph["nodes"]}
    for node in future_graph["nodes"]:
        nid = node.get("id")
        future = node.get("future") or {}
        drawn = {node.get("process_id")} | {current_pid.get(src) for src in future.get("from") or []}
        drawn = {p for p in drawn if p and p != SET_FRAME}
        spans = future.get("spans") or []
        for pid in spans:
            if pid not in future_frames:
                findings.append({"rule": "spans-unresolved", "where": nid,
                                 "detail": "spans id %r is not a frame of the future state" % pid})
        if len(drawn) < 2:
            continue
        if not spans:
            findings.append({"rule": "spans-missing", "where": nid,
                             "detail": "cross-process step draws on frames %s but has no future.spans"
                             % sorted(drawn)})
        elif drawn - set(spans):
            flags.append({"rule": "spans-incomplete", "where": nid,
                          "detail": "spans leaves out frames %s" % sorted(drawn - set(spans))})


def run(run_root, stage, file_path=None):
    if stage != STAGE:
        raise SetupError("this check runs at stage %r, not %r" % (STAGE, stage))
    if not os.path.isdir(run_root):
        raise SetupError("run folder not found: %s" % run_root)
    future = _load_json(file_path or os.path.join(run_root, FUTURE_INPUT), "future-state bundle")
    current = _load_json(os.path.join(run_root, CURRENT_INPUT), "current-state bundle")
    set_map = _load_json(os.path.join(run_root, SET_MAP), "set map")
    map_path = os.path.join(run_root, MAP)
    if not os.path.isfile(map_path):
        raise SetupError("transformation map not found: %s" % map_path)
    with open(map_path, encoding="utf-8") as fh:
        map_text = fh.read()
    future_graph, current_graph = _graph(future, "future-state bundle"), _graph(current, "current-state bundle")
    findings, flags = [], []
    check_splits(future_graph["nodes"], findings)
    check_set_links(set_map, map_text, findings, flags)
    check_frames(current_graph, future_graph, findings, flags)
    return findings, flags


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--run", required=True)
    parser.add_argument("--stage", required=True)
    parser.add_argument("--file")
    args = parser.parse_args(argv)
    try:
        findings, flags = run(args.run, args.stage, args.file)
    except SetupError as exc:
        print(json.dumps({"check": CHECK, "result": "setup-error",
                          "findings": [{"rule": "setup", "where": args.run, "detail": str(exc)}], "flags": []}))
        return 2
    print(json.dumps({"check": CHECK, "result": "fail" if findings else "pass",
                      "findings": findings, "flags": flags}, indent=2))
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
