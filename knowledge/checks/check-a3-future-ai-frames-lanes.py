#!/usr/bin/env python3
"""A3 future-state check: current AI uses, process frames and lanes.

Usage: check-a3-future-ai-frames-lanes.py --run RUN_ROOT --stage future-state [--file PATH]

Reads `stages/current-state/current-state.input.json`, `stages/future-state/future-state.input.json` (or `--file`)
and the `## Current AI uses` table of `stages/transformation-plan/transformation-map.md`. It runs only when the
current state's `extensions` lists an A3 lanes id (`a3-lanes-v0.1` or `a3-lanes-v0`); otherwise it passes with a
flag. Every rule keys on fields, never on a word list.

It fails (one finding each) on:
- ai-use-decision: a current `ai_use` node with no `## Current AI uses` row whose Decision is keep, upgrade or replace.
- ai-use-kept: a `keep` row whose use is not a future node with the same id (a node that only lists it in
  `future.from`, such as an agent step absorbing it, does not keep it).
- ai-use-failed-kept: a use with `ai_use.failed: true`, a `keep` row, and a future node carrying it (same id, or
  listing it in `future.from`) that is unchanged (`future.change` is `kept` or absent).
- ai-use-replaced-drawn: a `replace` row whose use is still a future node that the A3 renderer would draw. A
  replaced use may stay in the future graph data only when an agent step lists it in `future.from` with
  `future.change` "assigned": the renderer then leaves it undrawn and lists it in layout.json hidden
  (`render_a3.replaced_ai_uses`, the one shared rule).
- frame: a future work node (type read_interaction, write_interaction, decision, ai_use or message, or any node with
  an `agent` tag) whose `process_id` is missing or is neither a current `process_frames[].process_id` nor `set`.
- lane-dropped: a current lane outside the AI and agents band whose id is not a future lane.
- table-shape: strict table rule v2 in the first `## Current AI uses` section outside fences (each region, the
  heading's and each subsection's, to the next heading of any level): a line holding a pipe outside inline code
  (`\\|` is not a pipe) outside the region's one block of lines that start and end with `|`, a second block, or a
  block without header and separator fails, naming the line; the AI use rules then do not read the table.
- agent-band: a future agent step (an `agent` tag, or a coarse `lane` of `Agent`) whose `lane_id` is not a lane of
  band `ai_and_agents`.

Flags (never fail): a `replace` row whose Future step is not a future node listing the use in `future.from`.

Exit codes: 0 pass, 1 fail, 2 setup error. Stdout is one JSON object:
`{"check", "result", "findings": [{"rule", "where", "detail"}], "flags": [{"rule", "where", "detail"}]}`.
"""
import argparse
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "renderer"))
from render_a3 import replaced_ai_uses  # noqa: E402
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _md_blocks  # noqa: E402  fenced blocks, comments and code spans: one rule for every reader

CHECK = "a3-future-ai-frames-lanes"
A3_EXTENSIONS = ("a3-lanes-v0.1", "a3-lanes-v0")
WORK_TYPES = ("read_interaction", "write_interaction", "decision", "ai_use", "message")
AI_BAND = "ai_and_agents"
SET_FRAME = "set"
DECISIONS = ("keep", "upgrade", "replace")
AI_USES_HEADING = "## Current AI uses"
AI_USES_COLUMNS = ("AI use", "Decision", "Future step")


class SetupError(Exception):
    pass


def emit(result, findings, flags):
    print(json.dumps({"check": CHECK, "result": result, "findings": findings, "flags": flags}, indent=2))
    return {"pass": 0, "fail": 1, "setup-error": 2}[result]


def load_json(path):
    if not os.path.isfile(path):
        raise SetupError(f"missing file: {path}")
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError) as exc:
        raise SetupError(f"cannot read {path}: {exc}")


def cell(text):
    return text.strip().strip("`").strip()


def _section_table(map_text):
    """(table line indexes, problems) of the first `## Current AI uses` section outside fences, or (None, []) when
    it is absent, under strict table rule v2 (see _v2_region): the table is the block of the heading's own region;
    a problem names its 1-based line number."""
    return _v2_section(map_text.splitlines(), AI_USES_HEADING)



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
        m = None if mask[i] else re.match(r"\s{0,3}(#{1,6})(\s|$)", lines[i])
        if m:
            if len(m.group(1)) <= level:
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
    if block and not (len(block) >= 2 and re.fullmatch(r"\|(\s*:?-+:?\s*\|)+", lines[block[1]].strip())):
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


def ai_uses_table_problems(map_text):
    """[(line number, line)] of `## Current AI uses` lines holding a pipe outside the section's one table."""
    return _section_table(map_text)[1]


def ai_use_rows(map_text):
    """Rows of the `## Current AI uses` table as dicts, or None when the section is absent."""
    block, _ = _section_table(map_text)
    if block is None:
        return None
    lines = map_text.splitlines()
    # `\|` is an escaped pipe: kept as `|` inside its cell, never a cell break.
    table = [[cell(c.replace("\0", "|")) for c in lines[i].strip().replace("\\|", "\0").strip("|").split("|")]
             for i in block]
    if not table:
        return []
    header = table[0]
    missing = [c for c in AI_USES_COLUMNS if c not in header]
    if missing:
        raise SetupError(f"{AI_USES_HEADING} table is missing columns: {', '.join(missing)}")
    rows = []
    for raw in table[1:]:
        if all(re.fullmatch(r":?-+:?", c or "-") for c in raw):
            continue
        rows.append({h: (raw[i] if i < len(raw) else "") for i, h in enumerate(header)})
    return rows


def run_checks(current, future, map_text):
    findings, flags = [], []

    def fail(rule, where, detail):
        findings.append({"rule": rule, "where": where, "detail": detail})

    cur_graph, fut_graph = current.get("graph") or {}, future.get("graph") or {}
    cur_nodes = cur_graph.get("nodes") or []
    fut_nodes = fut_graph.get("nodes") or []
    fut_by_id = {n.get("id"): n for n in fut_nodes}
    # Uses the renderer leaves undrawn (assigned to an agent step); the rule and its reasons live in render_a3.
    not_drawn = replaced_ai_uses({"nodes": [n for n in fut_nodes if n.get("id") is not None],
                                  "edges": [e for e in fut_graph.get("edges") or []
                                            if e.get("id") is not None and "source" in e and "target" in e]})
    taken_from = {}
    for n in fut_nodes:
        for src in (n.get("future") or {}).get("from") or []:
            taken_from.setdefault(src, []).append(n)

    # Current AI uses
    uses = [n for n in cur_nodes if n.get("type") == "ai_use"]
    table_problems = ai_uses_table_problems(map_text)
    for number, text in table_problems:
        fail("table-shape", f"transformation-map.md:{number}",
             f"{AI_USES_HEADING} line {number} holds a pipe outside the section's one table of rows with "
             f"both outer pipes: {text!r}")
    if table_problems:
        uses = []
    rows = ai_use_rows(map_text) if uses else []
    by_use = {}
    for row in rows or []:
        by_use.setdefault(row.get("AI use", ""), []).append(row)
    for use in uses:
        uid = use.get("id")
        where = f"current node {uid}"
        decisions = [r.get("Decision", "").lower() for r in by_use.get(uid, [])]
        decisions = [d for d in decisions if d in DECISIONS]
        if rows is None:
            fail("ai-use-decision", where, f"the map has no {AI_USES_HEADING} section")
            continue
        if not decisions:
            fail("ai-use-decision", where, f"no {AI_USES_HEADING} row with keep, upgrade or replace")
            continue
        if "keep" in decisions:
            if uid not in fut_by_id:
                fail("ai-use-kept", where, "kept, but no future node has this id")
            carriers = ([fut_by_id[uid]] if uid in fut_by_id else []) + taken_from.get(uid, [])
            if (use.get("ai_use") or {}).get("failed") is True \
                    and any((n.get("future") or {}).get("change", "kept") == "kept" for n in carriers):
                fail("ai-use-failed-kept", where, "failed today, kept, and its future node is unchanged")
        if "replace" in decisions:
            if uid in fut_by_id and uid not in not_drawn:
                fail("ai-use-replaced-drawn", where, "replaced, but still drawn as a future node")
            for row in by_use.get(uid, []):
                if row.get("Decision", "").lower() != "replace":
                    continue
                step = fut_by_id.get(row.get("Future step", ""))
                if step is None or uid not in ((step.get("future") or {}).get("from") or []):
                    flags.append({"rule": "ai-use-replaced-step", "where": where,
                                  "detail": "the row's Future step is not a future node listing this use in future.from"})

    # Frames
    frames = {f.get("process_id") for f in cur_graph.get("process_frames") or []}
    for n in fut_nodes:
        if n.get("type") not in WORK_TYPES and n.get("agent") is None:
            continue
        pid = n.get("process_id")
        if pid != SET_FRAME and pid not in frames:
            fail("frame", f"future node {n.get('id')}", f"process_id {pid!r} is not a current process frame")

    # Lanes
    fut_lanes = {l.get("id"): l for l in fut_graph.get("lanes") or []}
    for lane in cur_graph.get("lanes") or []:
        if lane.get("band") != AI_BAND and lane.get("id") not in fut_lanes:
            fail("lane-dropped", f"current lane {lane.get('id')}",
                 f"{lane.get('band')} lane is missing from the future state")
    for n in fut_nodes:
        if n.get("agent") is None and (n.get("lane") or "").strip() != "Agent":
            continue
        lane = fut_lanes.get(n.get("lane_id"))
        if lane is None or lane.get("band") != AI_BAND:
            band = lane.get("band") if lane else "no lane"
            fail("agent-band", f"future node {n.get('id')}",
                 f"agent step sits in lane {n.get('lane_id')!r} ({band}), outside the {AI_BAND} band")
    return findings, flags


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--run", required=True)
    parser.add_argument("--stage", required=True)
    parser.add_argument("--file")
    args = parser.parse_args(argv)
    try:
        if args.stage != "future-state":
            raise SetupError(f"stage {args.stage!r}: this check runs on future-state only")
        stages = os.path.join(args.run, "stages")
        current = load_json(os.path.join(stages, "current-state", "current-state.input.json"))
        if not any(e in (current.get("extensions") or []) for e in A3_EXTENSIONS):
            return emit("pass", [], [{"rule": "not-a3", "where": "current-state.input.json",
                                      "detail": "no A3 lanes extension: checks skipped"}])
        future = load_json(args.file or os.path.join(stages, "future-state", "future-state.input.json"))
        map_path = os.path.join(stages, "transformation-plan", "transformation-map.md")
        if not os.path.isfile(map_path):
            raise SetupError(f"missing file: {map_path}")
        with open(map_path, encoding="utf-8") as f:
            map_text = f.read()
        findings, flags = run_checks(current, future, map_text)
    except SetupError as exc:
        return emit("setup-error", [{"rule": "setup", "where": args.run, "detail": str(exc)}], [])
    return emit("fail" if findings else "pass", findings, flags)


if __name__ == "__main__":
    sys.exit(main())
