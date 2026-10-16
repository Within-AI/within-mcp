#!/usr/bin/env python3
"""Check a stage file's evidence refs against the frozen snapshot index and the run's evidence addendum.

Usage: check-evidence-freeze.py --run RUN_ROOT --stage KEY [--file PATH]

KEY is set-map, current-state, future-state or transformation-plan. The stage file defaults to
stages/set-map/set-map.json, stages/transformation-plan/transformation-map.md, or stages/<KEY>/<KEY>.input.json
(falling back to the rendered <KEY>.json); --file names another, relative to RUN_ROOT.

A Markdown stage file (.md) is read by its tables (addendum G2). A table counts when its header has an
evidence-state column ("State" or "Evidence state") and an evidence column (naming "Evidence", "Ref" or "Source").
Each row of such a table is a claim: its state is the first word of the state cell, and on a row stamped Observed
its refs are the evidence cell split on commas, semicolons and spaces, with backticks dropped and the empty
markers "-", "—", "none" and "n/a" ignored. Only Observed rows have their refs checked, so the ids a reshape move
cites from the Current state Workflow (stamped Assumed) are not read as snapshot refs. Tables inside code fences
are skipped. Findings name the row as <file>:<line>.

A ref is the `source_id` of an item in any `source_refs[]` or `evidence_refs[]` list (a plain string item is its
own ref). Before any rule runs, every `source_refs` / `evidence_refs` value must be a list whose items are a
non-empty string or an object with a non-empty string `source_id`; any other shape is a setup error. Per Pins §5 a ref resolves to inputs/snapshot.json `sources[].id`, `issues[].id` or
`required_source_ids[]`, or to an `admitted` entry of evidence/addendum.json (Pins §4). Fails:
  ref-unresolved        the ref resolves to neither;
  ref-finer-than-index  the ref names something inside an indexed source (an observation in a collection)
                        and that id is not itself in the index;
  ref-pending-entry     the ref names a `flagged` entry with `resolution: null`;
  ref-not-admitted      the ref names a `flagged` entry the person resolved (it is still not admitted);
  read-hash-changed     a stored read under evidence/reads/ no longer hashes to its entry's sha256;
  read-missing          an entry's stored read is gone.
Flags (never fail):
  late-capture            an addendum entry captured after the freeze cutoff;
  later-process-version   the stage file carries a process version above the freeze pin (Pins §3 drift);
  freeze-block-absent     state.json has no freeze block, so versions were not compared.
Then runs `_evidence_claims.check` (E3b) and adds its findings.

Prints {"check", "result", "findings", "flags"} as JSON (Pins §8). Exit 0 pass, 1 fail, 2 setup error.
"""
import argparse
import datetime
import hashlib
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _evidence_claims  # noqa: E402
import _md_blocks  # noqa: E402  fenced blocks, comments and code spans: one rule for every reader

CHECK = "evidence-freeze"
STAGES = ("set-map", "current-state", "future-state", "transformation-plan")
REF_LISTS = ("source_refs", "evidence_refs")
SEPARATORS = (":", "/", "#")
STATE_HEADERS = ("state", "evidence state")
EVIDENCE_WORDS = ("evidence", "ref", "refs", "source", "sources")
EMPTY_REFS = ("", "-", "—", "–", "none", "n/a")


class SetupError(Exception):
    pass


def load_json(path, what):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except OSError as exc:
        raise SetupError("%s not readable: %s" % (what, exc))
    except ValueError as exc:
        raise SetupError("%s is not JSON: %s" % (what, exc))


def stage_file(run, stage, file_arg):
    if file_arg:
        return os.path.join(run, file_arg)
    if stage not in STAGES:
        raise SetupError("stage %r has no evidence-freeze file; use one of %s or pass --file" % (stage, ", ".join(STAGES)))
    if stage == "set-map":
        return os.path.join(run, "stages", "set-map", "set-map.json")
    if stage == "transformation-plan":
        return os.path.join(run, "stages", stage, "transformation-map.md")
    folder = os.path.join(run, "stages", stage)
    authored = os.path.join(folder, stage + ".input.json")
    return authored if os.path.exists(authored) else os.path.join(folder, stage + ".json")


def load_markdown(path, what):
    """{"tables": [{"line", "rows": [{"id", "line", "evidence_state", "evidence_refs"?}]}]} from a Markdown file."""
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.read().splitlines()
    except OSError as exc:
        raise SetupError("%s not readable: %s" % (what, exc))
    except ValueError as exc:
        raise SetupError("%s is not UTF-8 text: %s" % (what, exc))
    problems = strict_table_problems(lines)
    if problems:
        raise SetupError("%s has a table line outside its section's one table: %s" % (what, "; ".join(problems)))
    tables, mask, i = [], _fence_mask(lines), 0
    while i < len(lines):
        text = lines[i].strip()
        if not mask[i] and text.startswith("|") and i + 1 < len(lines) and _is_separator(lines[i + 1]):
            header = [_plain(c).lower() for c in _cells(lines[i])]
            state_col, evidence_col = _claim_columns(header)
            j = i + 2
            rows = []
            while j < len(lines) and not mask[j] and lines[j].strip().startswith("|"):
                if state_col is not None:
                    rows.append(_claim_row(_cells(lines[j]), state_col, evidence_col, j + 1))
                j += 1
            if state_col is not None:
                tables.append({"line": i + 1, "rows": rows})
            i = j
            continue
        i += 1
    return {"tables": tables}


HEADING_RE = re.compile(r"^\s{0,3}(#{1,6})(\s|$)")


def _fence_mask(lines):
    """For each line, True when it belongs to a fenced block or an HTML comment (_md_blocks.mask)."""
    return _md_blocks.mask(lines)


def _has_pipe(line):
    return _md_blocks.has_pipe(line)


def _is_table_line(line):
    text = line.strip().replace("\\|", "")
    return len(text) > 1 and text.startswith("|") and text.endswith("|")


def region_table_problems(lines, heading_idx, end, mask, name):
    """Strict table rule v2 for one region (lines heading_idx+1 .. end-1): its pipe-bearing lines form at most
    one contiguous block of lines that start and end with `|`, whose first two lines are a header and a
    separator. Any other pipe-bearing line, and the first line of a block without header and separator, is a
    problem naming the region and its 1-based line number."""
    blocks, current = [], None
    for i in range(heading_idx + 1, end):
        if not mask[i] and _is_table_line(lines[i]):
            if current is None:
                current = []
                blocks.append(current)
            current.append(i)
        else:
            current = None
    block = blocks[0] if blocks else []
    out = [(i + 1, "section %r line %d holds a pipe outside the table's one block of rows with both outer "
            "pipes: %r" % (name, i + 1, lines[i].strip()))
           for i in range(heading_idx + 1, end) if not mask[i] and _has_pipe(lines[i]) and i not in block]
    if block and not (len(block) >= 2 and _is_separator(lines[block[1]])):
        out.append((block[0] + 1, "section %r line %d holds a pipe outside a table: its block has no header and "
                    "separator row (e.g. an orphan row): %r" % (name, block[0] + 1, lines[block[0]].strip())))
    return out


def strict_table_problems(lines):
    """Strict table rule v2 for every region of a Markdown file. A region runs from a heading (any level) to the
    next heading (any level); lines before the first heading form one more region. Fences are tracked from the
    document start and nothing inside a fenced block counts."""
    mask = _fence_mask(lines)
    heads = [i for i, line in enumerate(lines) if not mask[i] and HEADING_RE.match(line)]
    bounds = [(-1, "(before the first heading)", heads[0] if heads else len(lines))]
    bounds += [(idx, lines[idx].strip(), heads[n + 1] if n + 1 < len(heads) else len(lines))
               for n, idx in enumerate(heads)]
    problems = []
    for idx, name, end in bounds:
        problems += region_table_problems(lines, idx, end, mask, name)
    return [problem for _, problem in sorted(problems)]


def _cells(line):
    text = line.strip().replace("\\|", "\0")
    if text.startswith("|"):
        text = text[1:]
    if text.endswith("|"):
        text = text[:-1]
    return [c.strip().replace("\0", "|") for c in text.split("|")]


def _is_separator(line):
    cells = _cells(line)
    return line.strip().startswith("|") and bool(cells) and all(
        c and set(c) <= set("-: ") and "-" in c for c in cells)


def _plain(cell):
    return cell.replace("`", "").replace("*", "").strip()


def _claim_columns(header):
    """(state column, evidence column) of a table header, or (None, None) when it is not a claims table."""
    state = next((k for k, h in enumerate(header) if h in STATE_HEADERS), None)
    if state is None:
        return None, None
    others = [(k, h) for k, h in enumerate(header) if k != state]
    evidence = next((k for k, h in others if h in EVIDENCE_WORDS), None)
    if evidence is None:
        evidence = next((k for k, h in others if any(w in EVIDENCE_WORDS for w in h.replace("/", " ").split())), None)
    return (state, evidence) if evidence is not None else (None, None)


def _claim_row(cells, state_col, evidence_col, line):
    cell = lambda k: cells[k] if k < len(cells) else ""
    words = _plain(cell(state_col)).split()
    row = {"id": _plain(cell(0)) or "line %d" % line, "line": line, "evidence_state": words[0] if words else ""}
    if row["evidence_state"].lower() == "observed":
        tokens = cell(evidence_col).replace("`", " ").replace(";", " ").replace(",", " ").split()
        row["evidence_refs"] = [t for t in tokens if t.lower() not in EMPTY_REFS]
    return row


def markdown_where(doc, rel, where):
    """`/tables/T/rows/R...` as `<file>:<line>...` for a Markdown stage file."""
    parts = where.split("/")
    if len(parts) >= 5 and parts[1] == "tables" and parts[3] == "rows":
        try:
            row = doc["tables"][int(parts[2])]["rows"][int(parts[4])]
        except (IndexError, ValueError):
            return where
        rest = "/".join(parts[5:])
        return "%s:%d%s" % (rel, row["line"], " " + rest if rest else "")
    return where


def snapshot_index(snapshot):
    """(index ids, {nested id: owning source id}) from snapshot.json, per Pins §5."""
    if not isinstance(snapshot, dict):
        raise SetupError("inputs/snapshot.json is not an object")
    index = set()
    for key in ("sources", "issues"):
        items = snapshot.get(key, [])
        if not isinstance(items, list):
            raise SetupError("snapshot.json %s is not a list" % key)
        for item in items:
            if isinstance(item, dict) and isinstance(item.get("id"), str):
                index.add(item["id"])
    required = snapshot.get("required_source_ids", [])
    if not isinstance(required, list):
        raise SetupError("snapshot.json required_source_ids is not a list")
    index.update(r for r in required if isinstance(r, str))
    nested = {}
    for source in snapshot.get("sources", []):
        if isinstance(source, dict) and isinstance(source.get("id"), str):
            for inner in _ids_below(source, top=True):
                nested.setdefault(inner, source["id"])
    return index, nested


def _ids_below(node, top=False):
    """Every `id` string inside `node`, not counting `node`'s own id."""
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "id" and not top and isinstance(value, str):
                yield value
            elif key != "id":
                yield from _ids_below(value)
    elif isinstance(node, list):
        for value in node:
            yield from _ids_below(value)


def load_addendum(run):
    """Entries of evidence/addendum.json, or [] when the run has none."""
    path = os.path.join(run, "evidence", "addendum.json")
    if not os.path.exists(path):
        return []
    doc = load_json(path, "evidence/addendum.json")
    entries = doc.get("entries") if isinstance(doc, dict) else None
    if not isinstance(entries, list) or not all(isinstance(e, dict) and isinstance(e.get("id"), str) for e in entries):
        raise SetupError("evidence/addendum.json must be {\"entries\": [{\"id\", ...}]}")
    return entries


def ref_shape_errors(node, where=""):
    """Every `source_refs` / `evidence_refs` value anywhere in `node` that is not a list of refs, as messages."""
    errors = []
    if isinstance(node, dict):
        for key, value in node.items():
            here = "%s/%s" % (where, key)
            if key in REF_LISTS:
                if not isinstance(value, list):
                    errors.append("%s is %s, not a list of refs" % (here, type(value).__name__))
                    continue
                for i, item in enumerate(value):
                    ref = item.get("source_id") if isinstance(item, dict) else item
                    if not isinstance(ref, str) or not ref.strip():
                        errors.append("%s/%d is %s, not an id or {\"source_id\": id}" % (here, i, json.dumps(item)))
            else:
                errors.extend(ref_shape_errors(value, here))
    elif isinstance(node, list):
        for i, value in enumerate(node):
            errors.extend(ref_shape_errors(value, "%s/%d" % (where, i)))
    return errors


def refs_in(node, where=""):
    """[(where, ref)] for every ref in a source_refs[] / evidence_refs[] list anywhere in `node`."""
    found = []
    if isinstance(node, dict):
        for key, value in node.items():
            here = "%s/%s" % (where, key)
            if key in REF_LISTS:
                for i, item in enumerate(value):
                    ref = item.get("source_id") if isinstance(item, dict) else item
                    found.append(("%s/%d" % (here, i), ref))
            else:
                found.extend(refs_in(value, here))
    elif isinstance(node, list):
        for i, value in enumerate(node):
            found.extend(refs_in(value, "%s/%d" % (where, i)))
    return found


def check_refs(doc, index, nested, entries):
    findings = []
    by_id = {e["id"]: e for e in entries}
    for where, ref in refs_in(doc):
        if ref in index:
            continue
        elif ref in by_id:
            entry = by_id[ref]
            if entry.get("status") == "admitted":
                continue
            if entry.get("status") == "flagged" and entry.get("resolution") is None:
                findings.append(finding("ref-pending-entry", where,
                                        "%s is flagged and waiting on the person's choice (resolution: null)" % ref))
            else:
                findings.append(finding("ref-not-admitted", where, "%s is %s (resolution %s), not admitted" % (
                    ref, entry.get("status"), json.dumps(entry.get("resolution")))))
        elif ref in nested or any(ref.startswith(i + sep) for i in index for sep in SEPARATORS):
            owner = nested.get(ref) or next(i for i in sorted(index, key=len, reverse=True)
                                             if any(ref.startswith(i + sep) for sep in SEPARATORS))
            findings.append(finding("ref-finer-than-index", where,
                                    "%s is inside source %s and is not itself in the snapshot index" % (ref, owner)))
        else:
            findings.append(finding("ref-unresolved", where,
                                    "%s is in neither the snapshot index nor an admitted addendum entry" % ref))
    return findings


def check_reads(run, entries):
    findings = []
    for entry in entries:
        where = "evidence/addendum.json#%s" % entry["id"]
        rel = entry.get("read_file")
        if not isinstance(rel, str) or not rel:
            findings.append(finding("read-missing", where, "entry has no read_file"))
            continue
        path = os.path.join(run, rel)
        try:
            with open(path, "rb") as f:
                actual = hashlib.sha256(f.read()).hexdigest()
        except OSError:
            findings.append(finding("read-missing", where, "stored read %s is missing" % rel))
            continue
        if actual != entry.get("sha256"):
            findings.append(finding("read-hash-changed", where, "stored read %s hashes to %s, entry says %s" % (
                rel, actual, entry.get("sha256"))))
    return findings


def parse_when(value):
    """(datetime or date, has_time) for an ISO-8601 value, or None."""
    if not isinstance(value, str):
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        if len(text) == 10:
            return datetime.date.fromisoformat(text), False
        stamp = datetime.datetime.fromisoformat(text)
        return (stamp if stamp.tzinfo else stamp.replace(tzinfo=datetime.timezone.utc)), True
    except ValueError:
        return None


def after(capture, cutoff):
    """True when `capture` is later than `cutoff`; a date-only side compares by date."""
    (c, c_time), (k, k_time) = capture, cutoff
    if c_time and k_time:
        return c > k
    return (c.date() if c_time else c) > (k.date() if k_time else k)


def freeze_block(run):
    path = os.path.join(run, "state.json")
    if not os.path.exists(path):
        return None
    state = load_json(path, "state.json")
    block = state.get("freeze") if isinstance(state, dict) else None
    return block if isinstance(block, dict) else None


def late_capture_flags(entries, cutoff_value):
    flags = []
    cutoff = parse_when(cutoff_value)
    for entry in entries:
        where = "evidence/addendum.json#%s" % entry["id"]
        capture = parse_when(entry.get("capture_date"))
        if cutoff is None or capture is None:
            flags.append(finding("late-capture", where, "capture %s or cutoff %s is not an ISO-8601 date; "
                                 "could not compare" % (json.dumps(entry.get("capture_date")), json.dumps(cutoff_value))))
        elif after(capture, cutoff):
            flags.append(finding("late-capture", where, "captured %s, after the cutoff %s" % (
                entry.get("capture_date"), cutoff_value)))
    return flags


def stage_versions(doc):
    """[(where, process_id, version_no)] the stage file carries: a set map's process_versions[] or a bundle's
    snapshot.process_versions[]."""
    out = []
    for where, lst in (("/process_versions", doc.get("process_versions")),
                       ("/snapshot/process_versions", (doc.get("snapshot") or {}).get("process_versions")
                        if isinstance(doc.get("snapshot"), dict) else None)):
        if isinstance(lst, list):
            for i, item in enumerate(lst):
                if isinstance(item, dict):
                    out.append(("%s/%d" % (where, i), item.get("process_id"), item.get("version_no")))
    return out


def later_version_flags(doc, block):
    pins = {}
    for item in block.get("process_versions") or []:
        if isinstance(item, dict):
            pins[item.get("process_id")] = item.get("version_no")
    flags = []
    for where, pid, version in stage_versions(doc):
        pin = pins.get(pid)
        if isinstance(pin, int) and isinstance(version, int) and version > pin:
            flags.append(finding("later-process-version", where,
                                 "process %s is at version %d, later than the pinned %d; re-freeze" % (pid, version, pin)))
    return flags


def finding(rule, where, detail):
    return {"rule": rule, "where": where, "detail": detail}


def run_check(run, stage, file_arg=None):
    """(exit code, report dict)."""
    try:
        if not os.path.isdir(run):
            raise SetupError("run folder %s does not exist" % run)
        snapshot = load_json(os.path.join(run, "inputs", "snapshot.json"), "inputs/snapshot.json")
        index, nested = snapshot_index(snapshot)
        entries = load_addendum(run)
        path = stage_file(run, stage, file_arg)
        markdown = path.lower().endswith(".md")
        doc = (load_markdown if markdown else load_json)(path, os.path.relpath(path, run))
        if not isinstance(doc, dict):
            raise SetupError("%s is not an object" % os.path.relpath(path, run))
        shape = ref_shape_errors(doc)
        if shape:
            raise SetupError("%s has malformed refs: %s" % (os.path.relpath(path, run), "; ".join(shape)))
        block = freeze_block(run)
    except SetupError as exc:
        return 2, {"check": CHECK, "result": "setup-error",
                   "findings": [finding("setup", run, str(exc))], "flags": []}
    findings = check_refs(doc, index, nested, entries) + check_reads(run, entries)
    admitted = {e["id"] for e in entries if e.get("status") == "admitted"}
    findings += _evidence_claims.check(doc, stage, index | admitted)
    if markdown:
        rel = os.path.relpath(path, run)
        for item in findings:
            item["where"] = markdown_where(doc, rel, item["where"])
    flags = []
    cutoff = (block or {}).get("cutoff") or (snapshot.get("scope") or {}).get("cutoff_at")
    flags += late_capture_flags(entries, cutoff)
    if block is None:
        flags.append(finding("freeze-block-absent", "state.json",
                             "no freeze block; process versions were not compared with the pins"))
    else:
        flags += later_version_flags(doc, block)
    result = "fail" if findings else "pass"
    return (1 if findings else 0), {"check": CHECK, "result": result, "findings": findings, "flags": flags}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--run", required=True)
    parser.add_argument("--stage", required=True)
    parser.add_argument("--file")
    try:
        args = parser.parse_args(argv)
    except SystemExit:
        print(json.dumps({"check": CHECK, "result": "setup-error",
                          "findings": [finding("setup", "argv", "usage: --run RUN_ROOT --stage KEY [--file PATH]")],
                          "flags": []}))
        return 2
    code, report = run_check(args.run, args.stage, args.file)
    print(json.dumps(report, indent=1))
    return code


if __name__ == "__main__":
    sys.exit(main())
