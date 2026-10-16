#!/usr/bin/env python3
"""Writes drawn and left-out processes: two parts, fields only.

Usage: check_writes_and_scope.py --run RUN_ROOT --stage KEY [--file PATH] [--part writes|scope|writes,scope]

--part writes (future-state stage). Reads the transformation map's `### Write grants` table
(`RUN_ROOT/stages/transformation-plan/transformation-map.md`) and the future state (`--file`, else
`stages/future-state/future-state.input.json`, else `stages/future-state/future-state.json`). The grant rules are the
plugin's one shared set, `KNOWLEDGE_ROOT/checks/_write_grants.py` (Agent Design runs the same module), on either lane
shape: a `lanes` table, or the `lane` named on each node (`Agent` / `Agent: <name>`, `Analyst`, `System`). A node's
grants are its `write_grant` (one id, or a list when a step carries more than one grant) and any `write_grants` list.
Fails when:
  - writes-system-drawn: a grants row's System is on no future node. A node carries a system through its `systems`
    ids (and those workspaces' `name` / `short_label`), its lane's `label` / `system`, a `system_surface` node's
    `title`, or its `agent` tag. The value `the person` is drawn by any step on a people lane (`Analyst`).
    Names compare with case and spacing ignored. An external lane's `label` draws the system it names on an exact
    match (case aside, outer spaces trimmed); a label matching two workspaces draws nothing and fails as
    `external-label-ambiguous`.
  - writes-node-grant: an agent node that writes (type `write_interaction`, or a non-empty `writes`) carries no grant,
    or a node names a grant that is no row of the table.
  - writes-grant-carried: no future-state node carries a grant the agent writes (Who writes `agent`).
  - writes-steps-drawn: a grants row's Steps (comma-separated, each plain or in one code span; `new` marks a
    future-state addition) is empty, holds an empty item, or names a step that is neither a future-state node id
    nor in any node's `future.from`. Every node carries `future.from` (the current steps it comes from), in a
    single-process run as in a set; when the future state records no `future.from` at all, an unresolved step is
    flagged `writes-steps-no-lineage` instead of failing.
  - writes-before-confirm: a step the agent takes writes mechanically into a system of record (a grant with Who
    writes `agent`, Write kind `mechanical`, System of record `yes` or `unclear`, carried by the step or whose Steps
    resolve to it) with no person confirmation of its own before it, on every path from a start; judged only when the
    map keeps a confirmation step for that write (the grant's Steps include a person step, or `## Splits` names it).
  - writes-grant-fields: a node's `write_grant` and `write_grants` name different grants (both are read; make them
    one).
  - writes-grant-reading: a recorded reading (below) names no grants row or column, or carries no reason.

Recorded readings. The map's Write grants table is an input. Where a row disagrees with the map's own text or with
the flow the evidence supports, draw what the evidence supports, do not edit the map, and record the reading you took
in a `grant_readings` list at the root of the future state, one object per grant and column:
`{"grant": "WG-<n>", "column": "Steps", "table_says": "<the cell as the table holds it>", "read_as": [<future-state
step ids the grant covers>], "rules": [<finding names this reading answers, e.g. "write-before-confirm">],
"reason": "<the map text or evidence that supports the reading>"}`. For a column other than Steps, `read_as` is
the value you read, in words. A Steps reading's `read_as` list replaces the cell, and the rules run on it: what they
still find (a write that bypasses the confirmation) still fails. On a reading with no `read_as` list, a finding on
that grant named in `rules` (the shared names: grant-step-missing, grant-system-undrawn, write-before-confirm,
write-grant-unknown, grant-fields-disagree) becomes a flag (`writes-grant-reading-recorded`), not a failure. Every
reading is listed in the flags and on the future-state page beside the table's value.

--part scope (transformation-plan stage, when a direction exists). Reads `inputs/snapshot.json`
`scope.process_ids` (the processes captured for this run), `inputs/design-direction.json` `process_ids` (the processes
the recommendation covers) and the map's `## Design direction` table. A snapshot process the recommendation leaves out
is recorded by a Design direction row whose Item cell is that process id. Fails (scope-left-out-recorded) when such a
process has no row, or its Followed cell does not start `departed:`. The pinned value is `departed: outside the
recommendation's scope; reshaped as the rest of the set` (the older `departed: outside the recommendation's scope;
kept with people` is still accepted); any other wording after `departed:` is a flag (scope-left-out-wording), never a
failure. A direction covering every snapshot process needs no such row.
The section (the first `## Design direction` heading outside fenced blocks) follows strict table rule v2: each region
holds one table as one unbroken block of lines that each start and end with `|`, header and separator first; any
other line with a pipe outside inline code (`\\|` is not a pipe) fails (scope-direction-table), naming its line
(`section_table`). When such lines leave the section without a usable table header, those findings are the
result (exit 1), not a setup error.

Before any rule runs, one structural boundary (`validate_writes_inputs`, plus the object-root test in `_read_json`)
turns odd input into a setup error: a JSON root that is not an object, a `graph` / `nodes` / `edges` / `lanes` /
`workspaces` collection of the wrong shape, a node field of the wrong type, or a Write grants id used twice.

Exit 0 pass, 1 fail, 2 setup error. Stdout is one JSON object: {"check", "result", "findings", "flags"}.
Stdlib only.
"""
import argparse, json, os, re, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import md_blocks  # noqa: E402  fenced blocks, comments and code spans: one rule for every reader
import knowledge  # noqa: E402  the one lookup for the plugin's knowledge folder

sys.path.insert(0, knowledge.path("checks"))
import _write_grants as W  # noqa: E402  the shared grant rules (Agent Design runs the same module)

CHECK = "writes-and-scope"
LEFT_OUT_STATUS = "departed"
LEFT_OUT_REASON = "outside the recommendation's scope; reshaped as the rest of the set"
LEFT_OUT_REASONS = (LEFT_OUT_REASON, "outside the recommendation's scope; kept with people")   # new value, then the old
GRANTS_HEADER = W.HEADER
DIRECTION_HEADER = ["item", "followed", "how the map carries it"]
MAP_REL = os.path.join("stages", "transformation-plan", "transformation-map.md")


class SetupError(Exception):
    pass


def _cell(s):
    return s.strip().strip("`").strip()


def _table_rows(lines):
    """Pipe-table rows (cells stripped) from consecutive lines, separator rows dropped."""
    rows = []
    for ln in lines:
        ln = ln.strip()
        if not ln.startswith("|"):
            if rows:
                break
            continue
        cells = [_cell(c) for c in ln.strip().strip("|").split("|")]
        if cells and set("".join(cells)) <= set("-: "):
            continue
        rows.append(cells)
    return rows


def _section(text, heading_re):
    """Body lines of the first heading matching `heading_re` outside fenced blocks and comments, up to the next
    heading of level 1 to 3 outside them; lines inside a block read as blank. None when the heading is absent."""
    lines = text.splitlines()
    mask = md_blocks.mask(lines)
    lines = ["" if mask[i] else ln for i, ln in enumerate(lines)]
    start = next((i for i, ln in enumerate(lines) if re.match(heading_re + r"\s*$", ln)), None)
    if start is None:
        return None
    end = next((i for i in range(start + 1, len(lines)) if re.match(r"#{1,3} ", lines[i])), len(lines))
    return lines[start + 1:end]


def _fence_mask(lines):
    """Per line, True when it belongs to a fenced block or an HTML comment (md_blocks.mask); tracked from the start."""
    return md_blocks.mask(lines)


def _has_pipe(ln):
    """A pipe outside code spans; an escaped pipe (`\\|`) is a character, not a pipe (md_blocks.has_pipe)."""
    return md_blocks.has_pipe(ln)


def _is_row(ln):
    t = ln.strip().replace("\\|", "")
    return len(t) > 1 and t.startswith("|") and t.endswith("|")


def _row_cells(ln):
    """Cells of a table row; an escaped pipe stays in its cell as `|`."""
    return [_cell(c).replace("\0", "|") for c in ln.strip().replace("\\|", "\0").strip("|").split("|")]


def section_table(text, heading):
    """Strict table rule v2 for the first `heading` line outside fenced blocks (fences tracked from the document
    start): (table, problems), or (None, []) when the section is absent. Each region of the section (the heading's
    own, to the next heading of any level, and one per subsection up to the next heading of the same or higher
    level) holds at most one contiguous block of lines that start and end with `|`, whose first two lines are a
    header and a separator; any other pipe-bearing line, and the first line of a block without them, is a problem
    (1-based line number, line). The table is the block of the heading's own region: its row cells, separator
    dropped ([] when it has no header and separator)."""
    lines = text.splitlines()
    mask = _fence_mask(lines)
    level = len(heading) - len(heading.lstrip("#"))
    start = next((i for i, ln in enumerate(lines) if not mask[i] and md_blocks.is_heading_line(ln, heading)), None)
    if start is None:
        return None, []
    heads = [start]
    for i in range(start + 1, len(lines)):
        m = None if mask[i] else re.match(r"\s{0,3}(#{1,6})(\s|$)", lines[i])
        if m:
            heads.append(i)
            if len(m.group(1)) <= level:
                break
    else:
        heads.append(len(lines))
    table, bad = None, []
    for n in range(len(heads) - 1):
        blocks, cur = [], None
        for i in range(heads[n] + 1, heads[n + 1]):
            if not mask[i] and _is_row(lines[i]):
                if cur is None:
                    cur = []
                    blocks.append(cur)
                cur.append(i)
            else:
                cur = None
        block = blocks[0] if blocks else []
        bad += [i for i in range(heads[n] + 1, heads[n + 1]) if not mask[i] and _has_pipe(lines[i]) and i not in block]
        whole = len(block) >= 2 and re.fullmatch(r"\|(\s*:?-+:?\s*\|)+", lines[block[1]].strip())
        if block and not whole:
            bad.append(block[0])
        if table is None:
            table = [_row_cells(lines[i]) for i in block[:1] + block[2:]] if whole else []
    return table, [(i + 1, lines[i].strip()) for i in sorted(bad)]


def stray_pipe_lines(text, heading):
    """[(1-based line number, line)] of pipe-bearing lines in the section `heading` (e.g. "## Design direction")
    outside its one table, under strict table rule v2 (`section_table`). Empty when the section is absent."""
    return section_table(text, heading)[1]


def _read_json(path, what):
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError) as e:
        raise SetupError("cannot read %s %s: %s" % (what, path, e))
    if not isinstance(doc, dict):
        raise SetupError("%s %s: the JSON root is a %s, not an object" % (what, path, type(doc).__name__))
    return doc


def _read_map(run):
    path = os.path.join(run, MAP_REL)
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except OSError as e:
        raise SetupError("cannot read the transformation map %s: %s" % (path, e))


def read_table(text):
    """The rows of the map's Write grants table (dicts keyed by GRANTS_HEADER), else a setup error."""
    try:
        rows = W.read_table(text)
    except W.TableError as e:
        raise SetupError(str(e))
    if rows is None:
        raise SetupError("no '### Write grants' section in the transformation map")
    return rows


def read_grants(text):
    """[(grant id, system, [steps])] from the map's `### Write grants` table, in table order. Steps are the
    comma-separated items of the Steps cell, each stripped of whitespace and backticks (an empty item stays "")."""
    return [(r["Grant"], r["System"], W.steps_refs(r["Steps"])) for r in read_table(text)]


def _future_path(run, file_arg):
    if file_arg:
        return file_arg if os.path.isabs(file_arg) else os.path.join(run, file_arg)
    for name in ("future-state.input.json", "future-state.json"):
        p = os.path.join(run, "stages", "future-state", name)
        if os.path.isfile(p):
            return p
    raise SetupError("no future state under %s" % os.path.join(run, "stages", "future-state"))


def _objects(value, what, required=False):
    """A list of JSON objects (absent and not required: empty), else a setup error."""
    if value is None and not required:
        return []
    if not isinstance(value, list) or not all(isinstance(x, dict) for x in value):
        raise SetupError("the future state's %s is not a list of objects" % what)
    return value


def _ids(value):
    return isinstance(value, str) or (isinstance(value, list) and all(isinstance(x, str) for x in value))


def validate_writes_inputs(grant_rows, doc):
    """The one structural boundary for --part writes; returns ({grant id: system}, graph, nodes)."""
    grants = {}
    for gid, system, _steps in grant_rows:
        if not gid:
            raise SetupError("a Write grants row has an empty Grant id")
        if gid in grants:
            raise SetupError("Write grants id %r is used by more than one row" % gid)
        grants[gid] = system
    if not isinstance(doc, dict):
        raise SetupError("the future state's JSON root is a %s, not an object" % type(doc).__name__)
    if "graph" in doc and not isinstance(doc["graph"], dict):
        raise SetupError("the future state's graph is not an object")
    g = doc.get("graph", doc)
    nodes = _objects(g.get("nodes"), "nodes", required=True)
    _objects(g.get("edges"), "edges")
    _objects(g.get("lanes"), "lanes")
    _objects(g.get("workspaces") or doc.get("workspaces"), "workspaces")
    for n in nodes:
        for key in ("id", "lane_id", "lane", "agent", "title"):
            if n.get(key) is not None and not isinstance(n[key], str):
                raise SetupError("node %r: %s is not a string" % (n.get("id"), key))
        for key in ("write_grant", "write_grants"):
            if n.get(key) is not None and not _ids(n[key]):
                raise SetupError("node %r: %s is not a grant id or a list of grant ids" % (n.get("id"), key))
        sys_ids = n.get("systems")
        if sys_ids is not None and not (isinstance(sys_ids, list) and all(isinstance(x, str) for x in sys_ids)):
            raise SetupError("node %r: systems is not a list of ids" % n.get("id"))
        fut = n.get("future")
        if fut is not None and not (isinstance(fut, dict) and (fut.get("from") is None or (
                isinstance(fut["from"], list) and all(isinstance(x, str) for x in fut["from"])))):
            raise SetupError("future-state node %r: future.from is not a list of ids" % n.get("id"))
    return grants, g, nodes


# shared finding -> this check's rule name
RULE_NAMES = {"grant-system-undrawn": "writes-system-drawn", "write-grant-unknown": "writes-node-grant",
              "write-before-confirm": "writes-before-confirm", "grant-fields-disagree": "writes-grant-fields",
              "grant-reading-invalid": "writes-grant-reading"}
NOT_JUDGED_HERE = ("write-ungranted", "exception-scope")   # Agent Design's, against the agent's own parts


def _rule(p):
    if p["rule"] == "grant-step-missing":
        return "writes-grant-carried" if p["detail"].startswith("no future-state node carries") else "writes-steps-drawn"
    return RULE_NAMES.get(p["rule"], p["rule"])


def _where(p):
    """The finding's place: the grant id for a table finding, the node id for a node finding."""
    if p["rule"] in ("grant-step-missing", "grant-system-undrawn"):
        return p["grants"][0] if p["grants"] else p["where"]
    return p["where"]


def check_writes(run, file_arg):
    map_text = _read_map(run)
    rows = read_table(map_text)
    grant_rows = [(r["Grant"], r["System"], W.steps_refs(r["Steps"])) for r in rows]
    doc = _read_json(_future_path(run, file_arg), "future state")
    grants, g, nodes = validate_writes_inputs(grant_rows, doc)
    try:
        graph = W.Graph(None, doc)
    except W.ShapeError as e:
        raise SetupError(str(e))
    res = W.evaluate([graph], rows, [("grant_readings", doc.get("grant_readings"))], W.split_refs(map_text))
    findings, flags = [], []
    for n in nodes:
        writes = n.get("type") == "write_interaction" or bool(n.get("writes"))
        if graph.is_agent(n) and writes and not W.grant_ids(n):
            findings.append({"rule": "writes-node-grant", "where": n.get("id"),
                             "detail": "agent node writes but carries no write_grant"})
    for p in res["problems"]:
        if p["rule"] in NOT_JUDGED_HERE:
            continue
        if p.get("no_lineage"):
            flags.append({"rule": "writes-steps-no-lineage", "where": _where(p), "detail": p["detail"]})
            continue
        findings.append({"rule": _rule(p), "where": _where(p), "detail": p["detail"]})
    for p in res["recorded"]:
        if p["rule"] in NOT_JUDGED_HERE:
            continue
        flags.append({"rule": "writes-grant-reading-recorded", "where": _where(p),
                      "detail": "%s: %s (recorded reading of %s %s: %s)" % (_rule(p), p["detail"], p["reading"]["grant"],
                                                                            p["reading"]["column"],
                                                                            p["reading"]["reason"])})
    for r in res["readings"]:
        flags.append({"rule": "writes-grant-reading", "where": r["grant"],
                      "detail": "%s read as %s, not as the table's %r: %s" % (
                          r["column"], json.dumps(r.get("read_as")), r.get("table_says"), r["reason"])})
    return findings, flags


def _followed_ok(cell):
    """'ok' for a pinned value (the new one or the old one), 'other' for any other wording starting `departed:`
    (a note: the process is still recorded as left out), None when the cell does not start `departed:`."""
    status, sep, reason = cell.strip().strip("*` ").partition(":")
    if not sep or status.strip().lower() != LEFT_OUT_STATUS:
        return None
    return "ok" if reason.strip().strip("*` ") in LEFT_OUT_REASONS else "other"


def check_scope(run):
    dpath = os.path.join(run, "inputs", "design-direction.json")
    if not os.path.isfile(dpath):
        return [], [{"rule": "scope-no-direction", "where": "inputs/design-direction.json",
                     "detail": "no direction recorded; nothing to check"}]
    d = _read_json(dpath, "design direction")
    snap = _read_json(os.path.join(run, "inputs", "snapshot.json"), "snapshot")
    scope = snap.get("scope")
    design = scope.get("process_ids") if isinstance(scope, dict) else None
    if not isinstance(design, list) or not design:
        raise SetupError("inputs/snapshot.json has no scope.process_ids")
    wanted = d.get("process_ids")
    if not isinstance(wanted, list) or not isinstance(d.get("items"), list):
        raise SetupError("design-direction.json lacks process_ids or items")
    if not all(isinstance(i, dict) for i in d["items"]) or not all(isinstance(p, str) for p in wanted):
        raise SetupError("design-direction.json items are not all objects, or process_ids not all strings")
    text = _read_map(run)
    rows, problems = section_table(text, "## Design direction")
    if rows is None:
        raise SetupError("no '## Design direction' section in the transformation map")
    findings = [{"rule": "scope-direction-table", "where": "transformation-map.md line %d" % n,
                 "detail": "Design direction line %d holds a pipe outside the table (a row without both outer pipes, "
                           "a row after a blank line, or a second table): %r" % (n, ln)}
                for n, ln in problems]
    if not rows or [c.lower() for c in rows[0][:3]] != DIRECTION_HEADER:
        if findings:
            return findings, []
        raise SetupError("Design direction table header not found")
    followed = {r[0].strip("*` "): r[1] for r in rows[1:] if len(r) >= 2}
    notes = []
    for pid in design:
        if pid in wanted:
            continue
        cell = followed.get(pid)
        if cell is None:
            findings.append({"rule": "scope-left-out-recorded", "where": pid,
                             "detail": "%s is left out by the recommendation and has no Design direction row" % pid})
        else:
            ok = _followed_ok(cell)
            if ok is None:
                findings.append({"rule": "scope-left-out-recorded", "where": pid,
                                 "detail": "%s is left out by the recommendation; Followed is %r, which does not "
                                           "start %r (expected %r)" % (pid, cell, LEFT_OUT_STATUS + ":",
                                                                       "%s: %s" % (LEFT_OUT_STATUS, LEFT_OUT_REASON))})
            elif ok == "other":
                notes.append({"rule": "scope-left-out-wording", "where": pid,
                              "detail": "NOTE: %s is recorded as departed with other wording %r (the pinned value is "
                                        "%r); read as left out" % (pid, cell, "%s: %s" % (LEFT_OUT_STATUS,
                                                                                            LEFT_OUT_REASON))})
    return findings, notes


def run_check(argv):
    ap = argparse.ArgumentParser(prog="check_writes_and_scope.py")
    ap.add_argument("--run", required=True)
    ap.add_argument("--stage", required=True)
    ap.add_argument("--file")
    ap.add_argument("--kind")
    ap.add_argument("--part", default="writes,scope")
    a = ap.parse_args(argv)
    findings, flags = [], []
    try:
        parts = [p.strip() for p in a.part.split(",") if p.strip()]
        bad = [p for p in parts if p not in ("writes", "scope")]
        if bad or not parts:
            raise SetupError("unknown --part %r (writes, scope)" % a.part)
        if not os.path.isdir(a.run):
            raise SetupError("no run folder %s" % a.run)
        if "writes" in parts:
            f, fl = check_writes(a.run, a.file)
            findings += f
            flags += fl
        if "scope" in parts:
            f, fl = check_scope(a.run)
            findings += f
            flags += fl
    except SetupError as e:
        out = {"check": CHECK, "result": "setup-error", "findings": [{"rule": "setup", "where": a.run, "detail": str(e)}],
               "flags": flags}
        return 2, out
    out = {"check": CHECK, "result": "fail" if findings else "pass", "findings": findings, "flags": flags}
    return (1 if findings else 0), out


def main(argv=None):
    code, out = run_check(sys.argv[1:] if argv is None else argv)
    print(json.dumps(out, indent=1))
    return code


if __name__ == "__main__":
    sys.exit(main())
