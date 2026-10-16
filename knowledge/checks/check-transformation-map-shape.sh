#!/usr/bin/env python3
# This file is Python despite its .sh name: run it directly or as `python3 <this file>`; `bash <this file>` cannot run it.
"""Transformation-map shape check: the transformation stage's mechanical release gate.

Structurally validates a transformation-map's per-node disposition table and its
`## Sub-Agent Groups` section (plan v3, `docs/handoffs/0282-factory-plugin-agent-shape-spectrum/
challenge-loop/plan/v3.md`, §A2/§A6) against the six shape invariants below, and computes the
derived spectrum-placement recommendation. Like the other factory checks, this is a Mechanism-A
re-verification against a lazy agent (a structural proof the load-bearing shape invariants hold),
NOT a semantic judge of whether a classification is operationally correct, and NOT a fail-closed
guarantee. `actor.engine == script`; deterministic and re-derivable: same file in, same verdict out.

--- Base-taxonomy agent-taken lookup (versioned constant; A1: taxonomies stay unreconciled — this
    is the adapter, not a fourth taxonomy) ---

AGENT_TAKEN_LOOKUP below is the single declared mapping this check uses to find "this row is
agent-taken work" across the three known base taxonomies. A run's own base-disposition column is
left exactly as-is (A1); this check only needs to recognize which of ITS values means agent-taken.
An unrecognized base-disposition COLUMN (the check cannot even locate a base-disposition column at
all) is a setup error (exit 2). Two further cases are distinguished by KNOWN_TAXONOMY_VALUES
(remediation cycle 2, C2): (i) a located column whose values genuinely belong to one of the three
known taxonomies (recognized by header name, or — failing that — because at least one of its
values matches that taxonomy's full known vocabulary, agent-taken values included) but has zero
rows in the agent-taken set — NOT a setup error, this is the legitimate `no-agent-work` placement
branch (see below); vs (ii) a located column whose values don't recognizably belong to ANY of the
three known taxonomies at all — a setup error (exit 2), since this means the run is using a
fourth, unknown scheme this script cannot yet adapt to.

Invariants asserted (mirrors check-internal-dag-shape.sh's numbered-invariant docstring pattern):

  1. EVERY AGENT-TAKEN ROW IS CLASSIFIED. Every row whose base-disposition value matches
     AGENT_TAKEN_LOOKUP must carry a non-empty `Shape Classification` in
     {script-eligible, judgment-required}. A missing or off-enum value fails. This invariant also
     covers the `Handoff out (target)` / `Handoff out (gating decision)` pair's required-field
     shape (§A2, v3): a Sub-Agent Groups row with target `person-terminal` or a named group and an
     empty/placeholder gating-decision cell fails here. A target value of bare `person` (the
     pre-v3 enum value) also fails here — it is off-enum since the v3 rename to `person-terminal`.

  2. NO SUB-AGENT GROUP ON A SCRIPT-ELIGIBLE ROW. Every row with `Shape Classification ==
     script-eligible` must carry `Sub-Agent Group == n/a`.

  3. DECLARED-VS-ACTUAL GROUP RECONCILIATION. The distinct non-`n/a` `Sub-Agent Group` values used
     in the per-node table must equal (as sets) the `Group ID` values declared in the
     `## Sub-Agent Groups` section — a group used but never declared, or declared but unused, fails.

  4. NO DANGLING HANDOFF TARGET. Every `Handoff out (target)` value that names a Group ID (not
     `person-terminal` or `terminal`) must match a real `Group ID` in the same section.

  5. NO SELF-REFERENTIAL HANDOFF. No row's `Handoff out (target)` may equal its own `Group ID`
     (encodes the ratified loop-back rule — a loop-back to the same unit is not a boundary).

  6. NO PLACEHOLDER BASIS / GATING-DECISION TEXT. Every non-`n/a` `Basis` cell (the Sub-Agent
     Groups section's `Basis` column, and — scoped to agent-taken rows only, a proportionate
     reading; see judgment call in the build return — the per-node table's own detail/notes
     column where one is present) and every required `Handoff out (gating decision)` cell must be
     non-empty and not a banned placeholder ({"TBD", "n/a" where a real basis is required, "",
     "..."}). This is a presence/non-placeholder check only — it does not parse or validate the
     Observed/Assumed/Contradictory/needs_more_information vocabulary itself.

--- Write grants (0399 write-grant design, section 1) ---

The authority boundary (template section 5) carries a `### Write grants` table with exactly the
thirteen columns in WRITE_GRANT_HEADER (0399's twelve plus `Recipient`, see R7 below), one row per distinct write the future state makes. Rules:

  R1. Dropped (0503 R-writes): agents write by default, decision-bearing record writes included.
  R2. Safeguards (0503 R-people; each prevents an action taken outside the agent's authority in a live system).
      With `## Why people stay` (`| Step | Process | Reason | Why |`, Reason `control`, `outside party` or
      `client statement`) these fail and go to the stage's one repair: an agent write whose Steps include a step
      listed with Reason `control` or `outside party`, whatever its write kind (an Exception reason never clears
      it; preparatory agent work, a draft or a packet, belongs on its own step, not the control or outside-party
      step); a per-node row for a
      `control` step that is agent-taken or has Fate `assigned`; an agent notice with Recipient `outside`; an agent
      write whose System is drawn in an external lane of `<map folder>/../current-state/current-state.json`.
      Quiet records (NOTE WHY lines, exit unaffected): a person grant or a person-kept step (Fate `kept`, not
      agent-taken) with no Why people stay row; a Reason outside the three; a `control` reason in a process the
      snapshot does not mark key control `true`. The section is read leniently: heading with or without a section
      number, header cells case-insensitive, extra columns ignored, a missing Why cell a note.
  R3. Who writes `person` needs a Next rung (not `n/a`, not empty). Its Step-up evidence should contain,
      case-insensitive, its Why people stay reason (or any of the three reason words) or one of `reversib`,
      `checkab`, `key control`, `inherited error`; when it holds neither this is a NOTE R3 (exit unaffected).
  R3b. NOTE (exit unaffected): an Exception reason other than `none` whose Step-up evidence fails the keyword test.
      Exception reasons are descriptive only.
  R4. Access is exactly `Assumed` on every row.
  R5. Every enum cell is in its enum; Grant ids are `WG-<n>` (n a positive integer) and unique.
  R6. The section exists, with the exact header and at least one row.
  Structure (reported under R5): the section, from its heading to the next heading, holds one
  contiguous pipe table whose every line starts and ends with `|`; a pipe line outside it (e.g. a
  row without outer pipes split off the table) fails rather than being skipped. Grant and enum
  cells are plain or wholly one code span; Steps items likewise (`WG-`1`` is malformed).
  NOTE (not a failure): an Exception reason other than `none` is printed as a NOTE (descriptive only).

THE RESHAPE SECTIONS INPUT SCHEMA (0400, north-star key criterion 3; pins P2, P4, P5). Every map carries
(in this order, before `## 1. Per-node disposition table`; the check requires presence, not position): `## Reshape moves` with the header exactly
`| Move | Kind | Current steps | Future steps | Evidence | State | How the outcome is kept |` (Move ids
`RM-<n>`; State `Assumed` or `not adopted: <reason>`); `### No reshape` directly under that table, one
bullet `- <kind>: <reason>` per move kind with no move; and `## Outcomes` with the header exactly
`| Current ending | Future ending | Reason and evidence |` (Future ending a future-state end-event id, or
`retired`). The eight move kinds, lower case: route at entry, consolidate repeated reads, package
handoffs, improve decision quality, consolidate exceptions, remove, merge, reorder. The Write grants
table carries `Recipient` as its 13th and last column, after `Access` (the one ruled amendment to 0399's
pinned twelve-column table): `internal` | `outside` | `unclear` on `notice` rows, `n/a` on every other row.
Rules, each failing with its own message:
  RS1. `## Reshape moves`, `### No reshape` and `## Outcomes` each exist; the two tables carry their exact
       headers and every move row has seven cells, a `RM-<n>` id and a State of `Assumed` or
       `not adopted: <reason>`.
  RS2. Every move Kind (and every no-reshape bullet's kind) is one of the eight.
  RS3. Every move's Evidence holds at least one id token (a backticked span, or a comma/space separated
       word; placeholders such as `n/a` do not count) that is not a node id of the disposition table, not
       `RM-<n>` and not `WG-<n>`. When `<map folder>/../../inputs/snapshot.json` exists, at least one token
       must equal a `sources[].id` or `issues[].id` there, or an `issues[].id` of
       `<map folder>/../current-state/current-state.json` when that exists.
  RS4. Every one of the eight kinds has a move row or a no-reshape bullet with a reason.
  RS5. Every Outcomes row has exactly three cells and a non-empty Current ending and Future ending; the
       table has at least one row. The whole section is read up to the next heading (or a `---` rule):
       every non-blank line after the header and separator must be a `| ... |` row, so a row missing its
       outer pipes, or rows after a blank line, fail rather than being skipped. (Coverage of current endings is the future-state reshape check's job.)
  RS6. Every adopted move (State not `not adopted: <reason>`) whose Kind is not `remove` names at least one
       future step: a Future steps cell that is empty, `—`, `-`, `n/a` or `none` (case-insensitive,
       backticks ignored) fails. A `remove` move may name none; its removed steps are checked through coverage.
  R7. The Write grants header ends with `Recipient`; each row's Recipient is in its enum for its Write kind.
  R8. Key control matches Within. When `<map folder>/../../inputs/snapshot.json` exists, a Write grants row
      whose Key control is `No` or `unknown` fails if the snapshot's `scope.key_control` is `true` for a
      process the row covers (a missing value or `needs more information` reads as false). In a single-process run every row covers the one process; a row whose Steps
      are only `new` covers every process of the run; a row naming step ids covers the processes those
      current-state steps trace to, read from the snapshot sources behind each step (the current state's
      node source_refs and coverage), and a `new` entry alongside step ids adds nothing. A step whose
      process cannot be read adds no process to its row: the check prints a NOTE (exit unaffected) naming
      the grant and the step, and the row is still checked against every process its other named steps
      trace to. R8 fails only for a process the row is traced to.
  F1. Floor flag 1 (NOTE, exit unaffected): a `mechanical` row with Who writes `person` while Key control is not
      `Yes`.
  F2. Floor flag 2 (NOTE, exit unaffected): a `notice` row with Who writes `person` and Recipient `internal` whose
      Steps have no Why people stay row (a design direction `must_not` keeping it with people is named in the note).
  F3. Floor flag 3 (NOTE, exit unaffected): a `person` row whose Next rung restates the default (contains,
      case-insensitive, "enter" and "confirmed") is printed as a NOTE for the coherence agent to judge.
  TABLE. Strict tables (rule v2): in the Reshape moves, per-node, Sub-Agent Groups and Write grants sections,
      each region (a heading to the next heading of any level; fenced blocks and comments tracked from the
      document start and ignored) holds at most one contiguous block of lines that start and end with `|`, header and
      separator first. Any other line there holding a pipe outside inline code (`\\|` is not a pipe), a second
      block, or an orphan row without header and separator fails, naming the region and line number. A complete
      table under a subsection heading is valid. A row with every pipe removed reads as prose and is not caught.
  BLOCK. Fenced blocks and HTML comments follow one rule for every map reader (_md_blocks): nothing inside them
      counts. A block left open, a comment that opens after text on its line, and text after a comment's closing
      `-->` fail, naming the line.

Parsing: a real structural parse of the two markdown tables' actual pipe-delimited rows and header
(split on the header-separator row, map header names to column indices, iterate rows) — not a
heuristic scan of narrative prose. Table 1 (the per-node table) is located as the first pipe-table
in the document whose header row contains BOTH "Shape Classification" and "Sub-Agent Group". Table
2 (the Sub-Agent Groups section) is the first pipe-table following a heading line matching
`## Sub-Agent Groups` (case-insensitive). Column identity is resolved by header-name matching
(case-insensitive substring), not fixed column position, so the run's own base-disposition column
name (which varies by taxonomy) does not need to be predicted in advance.

--- Derived spectrum-placement computation (never written back into the transformation-map
    artifact itself — printed only in this script's own report; see plan v3 §A6, §A4's sourcing
    rule for how a downstream consumer reads the REMIT GATE's ratified decision, never this
    script's output directly) ---

  1. ZERO agent-taken rows at all -> PLACEMENT = "no-agent-work". Checked FIRST so a map that
     never establishes any agent-taken row does not vacuously fall through to "script". Not a
     build-eligible placement — surfaced to the remit gate as "no agent work identified — confirm
     this transformation map is complete, or that a no-automation outcome is the intended result".
  2. >=1 agent-taken row; zero non-`n/a` Sub-Agent Group values anywhere; every agent-taken row is
     script-eligible -> PLACEMENT = "script".
  3. exactly one distinct Group ID, and no row's Handoff out (target) names a *different* group
     (only person-terminal/terminal values used) -> PLACEMENT = "single-judgment-surface".
  4. more than one distinct Group ID, and at least one inter-group Handoff out (target)
     -> PLACEMENT = "multi-subagent-with-handoffs".
  5. anything else -> PLACEMENT = "spectrum-intermediate", with a REASON naming the unmatched
     condition (a named, reasoned fallback — never silently defaulted to one of the other four).

Usage: check-transformation-map-shape.sh [--verbose] TRANSFORMATION_MAP.md
Exit: 0 pass; 1 shape assertion failure; 2 setup error (table not found / unparseable).
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _md_blocks  # noqa: E402  fenced blocks, comments and code spans: one rule for every reader

# The dogfooding-6 and arseu-5 taxonomies below were derived from built reference agents whose source
# files are not shipped with the plugin, so their citations name the source by role only.
AGENT_TAKEN_LOOKUP = {
    "canonical-5": {
        "values": ["agent-taken"],
        "source": "reference/workflow-diagram-factory-v1.md:69",
    },
    "dogfooding-6": {
        "values": ["deterministic-code-executed"],
        "source": (
            "an internal build (metrics-pack reference agent): its transformation map"
        ),
    },
    "arseu-5": {
        # Kept exactly as plan v3 §A6 literally specifies (reverted cycle-2 remediation's widening
        # to include "shared review seam" — that widening deviated from the ratified lookup as
        # written; see docs/handoffs/0282-.../scoring-loop's cycle-2 review, C2). The remittance
        # reference agent's calibration mismatch this produces (single-judgment-surface, not
        # multi-subagent-with-handoffs) is a known, flagged residual — see the run's escalation
        # note, not silently absorbed here.
        "values": ["A", "agent evidence/reasoning contribution"],
        "source": (
            "a client build (ERP clearing reference agent): its transformation map"
        ),
        "source2": (
            "a client build (remittance reference agent): its transformation map"
        ),
    },
}

# --- Full (agent-taken + non-agent-taken) known base-taxonomy vocabularies (remediation cycle 2,
#     C2/Fix 2a). Used only to distinguish a genuinely-recognized taxonomy column that happens to
#     have zero agent-taken rows (a legitimate `no-agent-work` placement) from a column whose values
#     don't recognizably belong to ANY of the three known taxonomies at all (a setup error — a
#     fourth, unknown scheme this script cannot yet adapt to). Each taxonomy's non-agent-taken
#     values are cited to where they were actually observed; never invented. ---
KNOWN_TAXONOMY_VALUES = {
    "canonical-5": {
        # The reference doc's own row-4 text names all five values of this taxonomy in full.
        "values": ["retained", "agent-taken", "human-retained", "removed-with-reason", "other"],
        "source": "reference/workflow-diagram-factory-v1.md:69",
    },
    "dogfooding-6": {
        # Only 3 of this taxonomy's 6 slots are exercised by the one real dogfooding run on disk
        # (`deterministic-code-executed` plus its own "unchanged" / "retired" naming for the other
        # two dispositions it uses) — the vocabulary here is built from what is actually observed
        # in that run, not the other 3 hypothetical slots, per this file's own no-invention
        # convention.
        "values": ["unchanged", "deterministic-code-executed", "retired"],
        "source": (
            "an internal build (metrics-pack reference agent): its transformation map"
        ),
    },
    "arseu-5": {
        # The ERP clearing reference agent's node table uses the single-letter tokens (A/R/S/E/U,
        # e.g. compound cells like "A + S + E"); the remittance reference agent's node table spells
        # the same 5-category scheme out in full. Both are the same taxonomy's own legend, quoted
        # where it defines all 5 categories.
        "values": [
            "A", "R", "S", "E", "U",
            "agent evidence/reasoning contribution", "analyst retained action",
            "shared review seam", "escalation/uncertainty seam", "unchanged current-state context",
        ],
        "source": (
            "a client build (ERP clearing reference agent): its transformation map"
        ),
        "source2": (
            "a client build (remittance reference agent): its transformation map"
        ),
    },
}

BANNED_PLACEHOLDERS = {"tbd", "", "...", "n/a"}

SHAPE_CLASSIFICATION_ENUM = {"script-eligible", "judgment-required"}
TERMINAL_TARGETS = {"person-terminal", "terminal"}
OFF_ENUM_LEGACY_TARGET = "person"  # pre-v3 bare "person" value — rejected since the v3 rename


def die(msg, code):
    sys.stderr.write(msg + "\n")
    sys.exit(code)


# ------------------------------------------------------------------------------------------
# Real structural parse of markdown pipe-tables (no PyYAML needed — the artifact is a flat
# table, not a nested graph; see check-internal-dag-shape.sh's own note on why ITS parse needs
# yaml.safe_load, which does not apply to this flat-table format).
# ------------------------------------------------------------------------------------------

SEPARATOR_RE = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*$")


def _split_row(line):
    line = line.strip().replace("\\|", "\0")  # an escaped pipe is a character in its cell, not a cell border
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|"):
        line = line[:-1]
    return [c.strip().replace("\0", "|") for c in line.split("|")]


def find_all_tables(lines):
    """Return a list of tables, each {header: [..], rows: [[..],...], header_line_idx: int}."""
    tables = []
    i = 0
    n = len(lines)
    while i < n:
        if SEPARATOR_RE.match(lines[i]) and i > 0 and "|" in lines[i - 1]:
            header = _split_row(lines[i - 1])
            rows = []
            j = i + 1
            while j < n and lines[j].strip().startswith("|"):
                rows.append(_split_row(lines[j]))
                j += 1
            tables.append({"header": header, "rows": rows, "header_line_idx": i - 1})
            i = j
        else:
            i += 1
    return tables


def col_index(header, *needles):
    """First column whose lowercased header contains ALL needles (case-insensitive substring)."""
    for idx, h in enumerate(header):
        hl = h.lower()
        if all(nd in hl for nd in needles):
            return idx
    return None


def find_per_node_table(tables):
    for t in tables:
        shape_i = col_index(t["header"], "shape classification")
        group_i = col_index(t["header"], "sub-agent group")
        if shape_i is not None and group_i is not None:
            t["shape_i"] = shape_i
            t["group_i"] = group_i
            return t
    return None


# Allows an optional leading section number ("## 2. Sub-Agent Groups") since every real calibration-case
# map numbers its sections — the literal schema heading is "Sub-Agent Groups" wherever it falls.
HEADING_RE = re.compile(r"^\s{0,3}#+\s*(\d+[.)]?\s*)?sub-agent groups\s*$", re.IGNORECASE)


def find_group_table(lines, tables):
    heading_idx = None
    for idx, line in enumerate(lines):
        if HEADING_RE.match(line):
            heading_idx = idx
            break
    if heading_idx is None:
        return None
    for t in tables:
        if t["header_line_idx"] > heading_idx:
            return t
    return None


# Narrowed in remediation cycle 2 (reviewer finding, parser-broadness): the three actual known
# base-taxonomy column header names, as they appear verbatim in the three real calibration-case
# files — "Disposition" (dogfooding), "Category" (ERP clearing reference agent), "Primary
# classification" (remittance reference agent). A loose "any column containing 'classif'" substring match was too broad (it would match any column with
# "classification" anywhere in its name, not just this run's actual base-disposition column).
BASE_DISPOSITION_HEADER_PATTERNS = ("disposition", "category", "primary classification")


def base_disposition_col(header, shape_i, group_i):
    for idx, h in enumerate(header):
        if idx in (shape_i, group_i):
            continue
        hl = h.lower()
        if any(k in hl for k in BASE_DISPOSITION_HEADER_PATTERNS):
            return idx
    return None


def detail_col(header, exclude):
    for idx, h in enumerate(header):
        if idx in exclude:
            continue
        hl = h.lower()
        if "detail" in hl or "note" in hl:
            return idx
    return None


def is_agent_taken(value):
    if value is None:
        return False
    tokens = re.split(r"[^A-Za-z0-9\-/]+", value)
    tokens = [t for t in tokens if t]
    value_norm = value.strip().lower()
    for entry in AGENT_TAKEN_LOOKUP.values():
        for v in entry["values"]:
            if v == "A":
                if "A" in tokens:
                    return True
            elif v.lower() == value_norm:
                return True
    return False


def is_known_taxonomy_value(value):
    """True if `value` recognizably belongs to the full (agent-taken + non-agent-taken)
    vocabulary of ANY of the three known base taxonomies (KNOWN_TAXONOMY_VALUES) — used to tell
    a genuinely-recognized taxonomy with zero agent-taken rows (no-agent-work) apart from an
    unrecognized fourth scheme (setup error). See Fix 2a, remediation cycle 2."""
    if value is None:
        return False
    tokens = re.split(r"[^A-Za-z0-9\-/]+", value)
    tokens = [t for t in tokens if t]
    value_norm = value.strip().lower()
    for entry in KNOWN_TAXONOMY_VALUES.values():
        for v in entry["values"]:
            if len(v) == 1:
                if v in tokens:
                    return True
            elif v.lower() == value_norm:
                return True
    return False


def is_placeholder(text):
    return (text or "").strip().lower() in BANNED_PLACEHOLDERS


def validate_and_place(node_table, group_table, verbose=False):
    problems = []
    header = node_table["header"]
    shape_i = node_table["shape_i"]
    group_i = node_table["group_i"]
    base_i = base_disposition_col(header, shape_i, group_i)
    if base_i is None:
        return None, ["setup: could not locate a base-disposition/category/classification "
                       "column in the per-node table (distinct from Shape Classification / "
                       "Sub-Agent Group)"], "setup"
    det_i = detail_col(header, {shape_i, group_i, base_i})

    node_id_i = 0

    agent_taken_rows = []
    base_values_seen = []
    for row in node_table["rows"]:
        if len(row) <= max(shape_i, group_i, base_i):
            problems.append("malformed per-node row (too few cells): %r" % (row,))
            continue
        base_val = row[base_i]
        base_values_seen.append(base_val)
        if is_agent_taken(base_val):
            agent_taken_rows.append(row)

    # ---- Fix 2a: a recognized column with zero agent-taken rows is the legitimate no-agent-work
    # placement; a column whose values don't recognizably belong to ANY known taxonomy at all is a
    # setup error (exit 2), not a silent no-agent-work pass ----
    if not agent_taken_rows and not any(is_known_taxonomy_value(v) for v in base_values_seen):
        return None, [
            "setup: base-disposition column %r's values do not recognizably belong to any of the "
            "three known base taxonomies (canonical-5 / dogfooding-6 / arseu-5) — this looks "
            "like a fourth, unknown scheme this script cannot yet adapt to" % header[base_i]
        ], "setup"

    if verbose:
        sys.stderr.write("base-disposition column: %r (index %d)\n" % (header[base_i], base_i))
        sys.stderr.write("detail/notes column: %r\n" % (header[det_i] if det_i is not None else None))
        sys.stderr.write("agent-taken rows found: %d\n" % len(agent_taken_rows))

    # ---- invariant 1: every agent-taken row is classified ----
    for row in agent_taken_rows:
        shape_val = row[shape_i].strip()
        if shape_val not in SHAPE_CLASSIFICATION_ENUM:
            problems.append(
                "INV1: agent-taken row %r has Shape Classification %r — must be one of %s"
                % (row[node_id_i], shape_val, sorted(SHAPE_CLASSIFICATION_ENUM)))

    # ---- invariant 2: no sub-agent group on a script-eligible row ----
    for row in node_table["rows"]:
        if len(row) <= max(shape_i, group_i):
            continue
        if row[shape_i].strip() == "script-eligible" and row[group_i].strip().lower() != "n/a":
            problems.append(
                "INV2: script-eligible row %r carries Sub-Agent Group %r — must be n/a"
                % (row[node_id_i], row[group_i]))

    used_groups = set()
    for row in node_table["rows"]:
        if len(row) <= group_i:
            continue
        gv = row[group_i].strip()
        if gv and gv.lower() != "n/a":
            used_groups.add(gv)

    declared_groups = {}
    target_i = handoff_i = gate_i = basis_i = member_i = None
    if group_table is not None:
        gh = group_table["header"]
        gid_i = col_index(gh, "group id")
        member_i = col_index(gh, "member")
        target_i = col_index(gh, "handoff out", "target")
        if target_i is None:
            target_i = col_index(gh, "handoff out")
        gate_i = col_index(gh, "handoff out", "gating")
        basis_i = col_index(gh, "basis")
        if gid_i is None or target_i is None or gate_i is None or basis_i is None:
            problems.append(
                "setup: Sub-Agent Groups table is missing one of the required columns "
                "(Group ID / Handoff out (target) / Handoff out (gating decision) / Basis)")
        else:
            for grow in group_table["rows"]:
                if len(grow) <= max(gid_i, target_i, gate_i, basis_i):
                    problems.append("malformed Sub-Agent Groups row (too few cells): %r" % (grow,))
                    continue
                declared_groups[grow[gid_i].strip()] = grow
    elif used_groups:
        problems.append(
            "setup: per-node table uses Sub-Agent Group value(s) %s but no '## Sub-Agent Groups' "
            "section/table was found" % sorted(used_groups))

    # ---- invariant 3: declared-vs-actual group reconciliation ----
    declared_ids = set(declared_groups.keys())
    if declared_ids != used_groups:
        missing_declared = used_groups - declared_ids
        unused_declared = declared_ids - used_groups
        if missing_declared:
            problems.append(
                "INV3: Sub-Agent Group value(s) used in the per-node table but never declared in "
                "'## Sub-Agent Groups': %s" % sorted(missing_declared))
        if unused_declared:
            problems.append(
                "INV3: Group ID(s) declared in '## Sub-Agent Groups' but never used in the "
                "per-node table: %s" % sorted(unused_declared))

    # ---- invariants 4/5/6 (group-table-scoped) + invariant 1's handoff required-field shape ----
    if declared_groups:
        for gid, grow in declared_groups.items():
            target_val = grow[target_i].strip()
            gate_val = grow[gate_i].strip()
            basis_val = grow[basis_i].strip()

            if target_val == OFF_ENUM_LEGACY_TARGET:
                problems.append(
                    "INV1: group %r Handoff out (target) is bare 'person' — off-enum since the "
                    "v3 rename; must be 'person-terminal' or a named Group ID" % gid)
                target_is_group = False
            else:
                target_is_group = target_val not in TERMINAL_TARGETS

            # Fix 2b: a non-placeholder gating decision is required whenever target is
            # `person-terminal` or a named Group ID — bare `terminal` alone accepts `n/a` (plan v3
            # §A2's `Handoff out (gating decision)` column definition). The prior condition
            # (`target_val in TERMINAL_TARGETS or target_is_group`) was a tautology covering every
            # target value, including bare `terminal`, which is wrong.
            if target_val == "person-terminal" or target_is_group:
                if is_placeholder(gate_val):
                    problems.append(
                        "INV1: group %r Handoff out (target) %r requires a non-placeholder "
                        "Handoff out (gating decision); got %r" % (gid, target_val, gate_val))

            # invariant 4: no dangling handoff target
            if target_is_group and target_val not in declared_ids:
                problems.append(
                    "INV4: group %r Handoff out (target) %r does not match any declared Group ID "
                    "%s" % (gid, target_val, sorted(declared_ids)))

            # invariant 5: no self-referential handoff
            if target_val == gid:
                problems.append(
                    "INV5: group %r names itself as its own Handoff out (target) — a loop-back to "
                    "the same unit is not a boundary" % gid)

            # invariant 6: no placeholder Basis
            if is_placeholder(basis_val):
                problems.append("INV6: group %r Basis cell is empty/placeholder: %r" % (gid, basis_val))

    # invariant 6, per-node detail/notes column, scoped to agent-taken rows only (judgment call —
    # see the build return's note on this scoping)
    if det_i is not None:
        for row in agent_taken_rows:
            if len(row) <= det_i:
                continue
            detail_val = row[det_i].strip()
            if is_placeholder(detail_val):
                problems.append(
                    "INV6: agent-taken row %r has an empty/placeholder detail/notes cell: %r"
                    % (row[node_id_i], detail_val))

    if problems:
        return None, problems, None

    # ---- derived spectrum-placement ----
    if not agent_taken_rows:
        return "no-agent-work", [], None

    all_script_eligible = all(row[shape_i].strip() == "script-eligible" for row in agent_taken_rows)
    if not used_groups and all_script_eligible:
        return "script", [], None

    distinct_groups = declared_ids if declared_groups else used_groups
    inter_group_handoff = False
    if declared_groups:
        for gid, grow in declared_groups.items():
            tv = grow[target_i].strip()
            if tv not in TERMINAL_TARGETS and tv != gid:
                inter_group_handoff = True
                break

    if len(distinct_groups) == 1 and not inter_group_handoff:
        return "single-judgment-surface", [], None
    if len(distinct_groups) > 1 and inter_group_handoff:
        return "multi-subagent-with-handoffs", [], None

    reason = (
        "unmatched pattern: %d distinct group(s), inter-group handoff present=%s — does not "
        "cleanly match script / single-judgment-surface / multi-subagent-with-handoffs"
        % (len(distinct_groups), inter_group_handoff)
    )
    return "spectrum-intermediate", [], reason


WRITE_GRANT_HEADER = ["Grant", "Steps", "System", "System of record", "Key control", "Write kind",
                      "Who writes", "Route", "Exception reason", "Next rung", "Step-up evidence", "Access",
                      "Recipient"]
# Recipient (0400 pin P4) is the one ruled amendment to 0399's pinned twelve-column table.
LEGACY_WRITE_GRANT_HEADER = WRITE_GRANT_HEADER[:-1]
NOTICE_RECIPIENTS = {"internal", "outside", "unclear"}
WRITE_GRANT_ENUMS = {
    "System of record": {"yes", "no", "unclear"},
    "Key control": {"Yes", "No", "unknown"},
    "Write kind": {"notice", "draft", "handoff", "own-file", "mechanical", "decision-bearing"},
    "Who writes": {"agent", "person"},
    "Route": {"api", "mcp", "screen", "n/a"},
    "Exception reason": {"none", "own-working-surface", "reversible-reviewed"},
    "Access": {"Assumed"},
}
STEP_UP_KEYWORDS = ("reversib", "checkab", "key control", "inherited error")
GRANT_ID_RE = re.compile(r"^WG-[1-9][0-9]*$")
WRITE_GRANTS_HEADING_RE = re.compile(r"^\s{0,3}#+\s*(\d+[.)]?\s*)?write grants\s*$", re.IGNORECASE)


ANY_HEADING_RE = re.compile(r"^\s{0,3}#")
CODE_WRAP_RE = re.compile(r"^`([^`]*)`$")
ID_COLUMNS = ("Grant",) + tuple(WRITE_GRANT_ENUMS) + ("Recipient",)


def _bare(cell):
    return cell.replace("`", "").strip()


def _strict_value(cell):
    """A Grant or enum cell is plain text or one code span wrapping the whole value; any other
    backtick use (e.g. WG-`1`) is malformed. Returns the value, or None when malformed."""
    cell = cell.strip()
    m = CODE_WRAP_RE.match(cell)
    if m:
        return m.group(1).strip()
    return None if "`" in cell else cell


def _strict_steps(cell):
    """Steps: comma-separated ids, each plain or wholly in one code span. None when malformed."""
    items = [i.strip() for i in cell.split(",")]
    vals = [_strict_value(i) for i in items]
    return None if any(v is None or v == "" for v in vals) else ", ".join(vals)


def _write_grants_section_table(lines, heading_idx):
    """Strict parse of the region from its heading to the next heading of any level (strict table rule v2;
    fences tracked from the document start, `\\|` is not a pipe). The region holds exactly one pipe table
    (header, separator, rows) as one contiguous block of lines that each start and end with `|`; no other line
    in the region may hold a pipe. Returns (table, problems)."""
    mask = _fence_mask(lines)
    end = _region_end(lines, heading_idx, mask)
    pipe_idx = [i for i in range(heading_idx + 1, end) if not mask[i] and _has_pipe(lines[i])]
    if not pipe_idx:
        return None, []
    problems = []
    start = pipe_idx[0]
    block_end = start
    while block_end < end and not mask[block_end] and _has_pipe(lines[block_end]):
        block_end += 1
    for i in range(start, block_end):
        if not _is_table_row(lines[i]):
            problems.append("R5: Write grants line %d is a table row without both outer pipes: %r"
                            % (i + 1, lines[i].strip()))
    for i in pipe_idx:
        if i >= block_end:
            problems.append("R5: Write grants line %d holds a pipe outside the table (a row split "
                            "off from the table, or a second table): %r" % (i + 1, lines[i].strip()))
    if block_end - start < 2 or not SEPARATOR_RE.match(lines[start + 1]):
        problems.append("R6: Write grants line %d starts a table with no header separator row (e.g. an orphan "
                        "row): %r" % (start + 1, lines[start].strip()))
        return None, problems
    table = {"header": _split_row(lines[start]),
             "rows": [_split_row(lines[i]) for i in range(start + 2, block_end)],
             "header_line_idx": start}
    return table, problems


def _has_step_up_keyword(text):
    tl = text.lower()
    return any(k in tl for k in STEP_UP_KEYWORDS)


def _key_control_reader(map_path):
    """From `<map folder>/../../inputs/snapshot.json`: (process ids Within marks key control true, every process id
    of the run, step id -> set of its processes). None when no snapshot sits beside the run or none is true.

    A current-state step's processes are those of the snapshot sources behind it: its node's source_refs, plus
    the snapshot steps the current state's coverage maps onto it. A source's process is its `process_id`, else
    the `<process id>:` prefix of its id. Only read for a run of several processes."""
    import json
    folder = os.path.dirname(os.path.abspath(map_path))
    try:
        snap = json.load(open(os.path.join(folder, "..", "..", "inputs", "snapshot.json"), encoding="utf-8"))
        kc = snap["scope"]["key_control"]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if not isinstance(kc, dict) or not any(v is True for v in kc.values()):
        return None
    true_ids, every = {p for p, v in kc.items() if v is True}, set(kc)
    step_pids = {}
    if len(every) > 1:
        src = {}
        for s in snap.get("sources") or []:
            if isinstance(s, dict) and s.get("id") is not None:
                pid = s.get("process_id") or str(s["id"]).split(":", 1)[0]
                src[str(s["id"])] = {pid} & every
        snap_steps = {str(s.get("id")): src.get(str(s.get("source_id")), set())
                      for s in snap.get("steps") or [] if isinstance(s, dict)}
        try:
            cs = json.load(open(os.path.join(folder, "..", "current-state", "current-state.json"), encoding="utf-8"))
            nodes, coverage = cs["graph"]["nodes"], cs.get("coverage") or []
        except (OSError, ValueError, KeyError, TypeError):
            nodes, coverage = [], []
        for n in (n for n in nodes if isinstance(n, dict)):
            step_pids[str(n.get("id"))] = set().union(*[src.get(str(r.get("source_id")), set())
                                                        for r in n.get("source_refs") or [] if isinstance(r, dict)])
        for c in coverage:
            for gid in (c.get("graph_ids") or []) if isinstance(c, dict) else []:
                step_pids.setdefault(str(gid), set()).update(snap_steps.get(str(c.get("step_id")), set()))
    return true_ids, every, step_pids


DIRECTION_HEADING_RE = re.compile(r"^\s{0,3}##\s+design direction\s*$", re.IGNORECASE)
DIRECTION_HEADER = ["Item", "Followed", "How the map carries it"]


def _must_not_ids(map_path):
    """Ids of the `must_not` items in `<map folder>/../../inputs/design-direction.json` (read by `items[].kind`),
    or None when the file is missing or unreadable."""
    import json
    folder = os.path.dirname(os.path.abspath(map_path))
    try:
        data = json.load(open(os.path.join(folder, "..", "..", "inputs", "design-direction.json"), encoding="utf-8"))
        items = data["items"]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if not isinstance(items, list):
        return None
    return {str(i["id"]) for i in items
            if isinstance(i, dict) and i.get("kind") == "must_not" and i.get("id") is not None}


def _cites(cell, token):
    """True when `token` appears in `cell` as a whole token (case-sensitive; not inside a longer id)."""
    return re.search(r"(?<![A-Za-z0-9_-])%s(?![A-Za-z0-9_-])" % re.escape(token), cell) is not None


def _direction_keeps_with_people(gid, must_not, lines, grant_cells=()):
    """F2 exception: (item, how) for the first `must_not` item whose `## Design direction` row is Followed `yes`
    and whose carry cell names grant `gid` as a whole token (how = "direction-row"), else the first such item
    the grant row's own cells name as a whole token (how = "grant-row"); or (None, None)."""
    if not must_not or not gid:
        return None, None
    idx, body = _section(lines, DIRECTION_HEADING_RE)
    table = _first_table(body) if idx is not None else None
    if table is None or [_bare(h) for h in table["header"]] != DIRECTION_HEADER:
        return None, None
    followed = [(_bare(raw[0]), raw[2]) for raw in table["rows"]
                if len(raw) == len(DIRECTION_HEADER) and _bare(raw[0]) in must_not
                and _bare(raw[1]).lower() == "yes"]
    for item, carry in followed:
        if _cites(carry, gid):
            return item, "direction-row"
    for item, _carry in followed:
        if any(_cites(cell, item) for cell in grant_cells):
            return item, "grant-row"
    return None, None


def _key_control_problem(label, row, reader):
    """R8 for one grant row whose Key control is not `Yes`: (problem or None, note or None).

    A step whose process cannot be read adds no process to the row; it yields a NOTE instead, so R8 never
    fails on a process the row is not traced to."""
    true_ids, every, step_pids = reader
    steps = [s.strip() for s in row["Steps"].split(",") if s.strip()]
    unread = []
    if len(every) == 1 or all(s == "new" for s in steps):
        covered = set(every)
    else:
        covered = set()
        for s in steps:
            if s != "new":
                covered |= step_pids.get(s) or set()
                unread += [] if step_pids.get(s) else [s]
    note = None
    if unread:
        note = ("NOTE R8: grant %s step(s) %s: key control could not be checked for this step because its "
                "process can't be traced to the snapshot" % (label, ", ".join(unread)))
    hit = sorted(covered & true_ids)
    if not hit:
        return None, note
    return ("R8: grant %s has Key control %r, but Within marks key control true for %s, which the row covers; "
            "write 'Yes'" % (label, row["Key control"], ", ".join(hit))), note


# trailing words or a colon after the name are a heading variant (R-lenient): "### why people stay:", "(kept steps)"
WHY_HEADING_RE = re.compile(r"^\s{0,3}#+\s*(\d+[.)]?\s*)?[*_]*why people stay\b[^\n]*$", re.IGNORECASE)
WHY_REASONS = ("control", "outside party", "client statement")


def read_why_people_stay(lines):
    """`## Why people stay`, read leniently (R-lenient): the heading with or without a section number or trailing
    words, header cells matched case-insensitively with emphasis marks ignored, extra columns ignored, a missing Why cell a note. Returns (rows, notes); rows is a
    list of {"step", "process", "reason", "why"} (lower-cased reason, backticks stripped), [] when absent."""
    mask = _fence_mask(lines)
    idx = next((i for i, l in enumerate(lines) if WHY_HEADING_RE.match(l) and not mask[i]), None)
    if idx is None:
        return [], []
    end = _region_end(lines, idx, mask)
    block = [i for i in range(idx + 1, end) if not mask[i] and lines[i].strip().startswith("|")]
    notes = []
    if len(block) < 2 or not SEPARATOR_RE.match(lines[block[1]]):
        return [], ["NOTE WHY: the Why people stay section holds no readable table; read as no rows"]
    header = [re.sub(r"[*_]", "", _bare(h)).strip().lower() for h in _split_row(lines[block[0]])]   # **Step** reads as step
    col = {k: header.index(k) for k in ("step", "process", "reason", "why") if k in header}
    if "step" not in col or "reason" not in col:
        return [], ["NOTE WHY: the Why people stay table has no Step or Reason column (header %r); read as no rows"
                    % header]
    rows = []
    for i in block[2:]:
        raw = _split_row(lines[i])
        cell = lambda k: _bare(raw[col[k]]) if k in col and col[k] < len(raw) else ""
        if not cell("step"):
            continue
        row = {"step": cell("step"), "process": cell("process"), "reason": cell("reason").lower(), "why": cell("why")}
        if not row["why"]:
            notes.append("NOTE WHY: step %s has no Why cell in Why people stay (reason %r read as given)"
                         % (row["step"], row["reason"]))
        rows.append(row)
    return rows, notes


def _why_by_step(rows):
    """step id -> set of its Why people stay reasons (a Step cell may list several ids)."""
    out = {}
    for r in rows:
        for step in (x.strip() for x in r["step"].split(",")):
            if step:
                out.setdefault(step, set()).add(r["reason"])
    return out


def _snapshot_key_control_true(map_path):
    """Process ids the snapshot beside the run marks key control `true`, or None when there is no readable snapshot.
    A missing value or `needs more information` reads as false (R-people, R8)."""
    import json
    folder = os.path.dirname(os.path.abspath(map_path))
    try:
        snap = json.load(open(os.path.join(folder, "..", "..", "inputs", "snapshot.json"), encoding="utf-8"))
    except (OSError, ValueError):
        return None
    scope = snap.get("scope") if isinstance(snap, dict) else None
    kc = scope.get("key_control") if isinstance(scope, dict) else None
    if isinstance(kc, dict):
        return {str(p) for p, v in kc.items() if v is True}
    return set()


def _external_systems(map_path):
    """Normalised names of the systems drawn in an external lane of the current state beside the run (a lanes-table
    entry with band `external` and every node whose `lane_id` names it, or a node whose lane is `External`): the
    lane's label and system, and each such node's title, system and systems (workspace ids read as their names)."""
    import json
    folder = os.path.dirname(os.path.abspath(map_path))
    try:
        cs = json.load(open(os.path.join(folder, "..", "current-state", "current-state.json"), encoding="utf-8"))
        g = cs["graph"]
    except (OSError, ValueError, KeyError, TypeError):
        return set()
    names = set()
    norm = lambda v: " ".join(str(v).lower().split())
    if not isinstance(g, dict):
        return set()
    lane_ids = set()
    for lane in g.get("lanes") or []:
        if isinstance(lane, dict) and str(lane.get("band", "")).lower() == "external":
            names |= {norm(lane[k]) for k in ("label", "system") if isinstance(lane.get(k), str)}
            lane_ids.add(lane.get("id"))
    ws = {w.get("id"): w.get("name") for w in g.get("workspaces") or [] if isinstance(w, dict)}
    for n in g.get("nodes") or []:
        if not isinstance(n, dict):
            continue
        if str(n.get("lane", "")).strip().lower() == "external" or (n.get("lane_id") in lane_ids - {None}):
            names |= {norm(n[k]) for k in ("title", "system") if isinstance(n.get(k), str)}
            for x in n.get("systems") or []:
                if isinstance(x, str):
                    names.add(norm(x))
                    if isinstance(ws.get(x), str):
                        names.add(norm(ws[x]))
    return names - {""}


def _per_node_rows(tables):
    """[(step id, agent-taken?, Fate)] from the per-node disposition table. Where the table has no Fate column (an
    older map), a row whose disposition is not agent-taken and says it is retained reads as Fate `kept`."""
    t = find_per_node_table(tables)
    if t is None:
        return []
    header = t["header"]
    base_i = base_disposition_col(header, t["shape_i"], t["group_i"])
    fate_i = next((i for i, h in enumerate(header) if _bare(h).lower() == "fate"), None)
    out = []
    for raw in t["rows"]:
        if not raw:
            continue
        get = lambda i: raw[i] if i is not None and i < len(raw) else ""
        taken = is_agent_taken(get(base_i))
        fate = _bare(get(fate_i)).lower()
        if fate_i is None and not taken and "retain" in _bare(get(base_i)).lower():
            fate = "kept"
        out.append((_bare(raw[0]), taken, fate))
    return out


def validate_people_stay(lines, tables, grant_rows, map_path=None):
    """The R-people safeguards and quiet records. Returns (problems, notes).

    Failing (each names the damage: an action taken outside the agent's authority in a live system):
      - an agent taking a step listed with Reason `control` (per-node row agent-taken, or Fate `assigned`);
      - an agent write whose Steps include a `control` step (R2; an exception never clears it);
      - an agent write whose Steps include an `outside party` step;
      - an agent notice with Recipient `outside`;
      - an agent write into a system drawn in an external lane of the current state.
    Quiet (NOTE lines): a person grant or person-kept step with no Why people stay row; a Reason outside the three;
    a `control` reason in a process the snapshot does not mark key control true."""
    rows, notes = read_why_people_stay(lines)
    problems = []
    why = _why_by_step(rows)
    control = {s for s, r in why.items() if "control" in r}
    outside = {s for s, r in why.items() if "outside party" in r}
    for r in rows:
        if r["reason"] not in WHY_REASONS:
            notes.append("NOTE WHY: step %s has Reason %r, not one of %s" % (r["step"], r["reason"], list(WHY_REASONS)))
    kc_true = _snapshot_key_control_true(map_path) if map_path else None
    if kc_true is not None:
        for r in rows:
            if r["reason"] == "control" and r["process"] and r["process"] not in kc_true:
                notes.append("NOTE WHY: step %s has Reason 'control' but the snapshot does not mark process %s key "
                             "control true" % (r["step"], r["process"]))
    for step, taken, fate in _per_node_rows(tables):
        if step in control and (taken or fate == "assigned"):
            problems.append("R2: step %s is a control step (Why people stay) but the map gives it to an agent "
                            "(%s); the agent would take an approval, sign-off or submission outside its authority"
                            % (step, "agent-taken" if taken else "Fate assigned"))
        elif not taken and fate == "kept" and step not in why:
            notes.append("NOTE WHY: step %s is kept with a person and has no Why people stay row" % step)
    idx, body = _section(lines, RESHAPE_HEADING_RE)
    table = _first_table(body) if idx is not None else None
    for raw in (table or {}).get("rows", []) if table and [_bare(h) for h in table["header"]] == MOVE_HEADER else []:
        move = dict(zip(MOVE_HEADER, raw))
        gone = [x for x in (t.strip() for t in _bare(move.get("Current steps", "")).split(",")) if x in control]
        if _bare(move.get("Kind", "")).lower() == "remove" and gone:
            notes.append("NOTE WHY: move %s removes control step(s) %s (R-moves: `remove` never drops a control step)"
                         % (_bare(move["Move"]), ", ".join(gone)))
    external = _external_systems(map_path) if map_path else set()
    for row in grant_rows:
        label = row["Grant"]
        steps = [x.strip() for x in row["Steps"].split(",") if x.strip()]
        if row["Who writes"] == "person":
            missing = [x for x in steps if x != "new" and x not in why]
            if missing:
                notes.append("NOTE WHY: grant %s is written by a person but step(s) %s have no Why people stay row"
                             % (label, ", ".join(missing)))
            if "new" in steps and label not in why:
                notes.append("NOTE WHY: grant %s is new work written by a person and has no Why people stay row"
                             % label)
            continue
        if row["Who writes"] != "agent":
            continue
        # every write kind: preparatory agent work (a draft, a packet) belongs on its own step, not the control step
        hit = [x for x in steps if x in control]
        if hit:
            problems.append("R2: grant %s is an agent write on control step(s) %s (Why people stay); the agent would "
                            "write an approval, sign-off or submission outside its authority (an exception reason "
                            "does not clear this)" % (label, ", ".join(hit)))
        hit = [x for x in steps if x in outside]
        if hit:
            problems.append("R2: grant %s is an agent write on outside-party step(s) %s (Why people stay); the agent "
                            "would send to or write into a party outside the organisation" % (label, ", ".join(hit)))
        if row["Write kind"] == "notice" and row["Recipient"] == "outside":
            problems.append("R2: grant %s is an agent notice with Recipient 'outside'; the agent would send to a party "
                            "outside the organisation" % label)
        if external and " ".join(row["System"].lower().split()) in external:
            problems.append("R2: grant %s is an agent write into %s, a system drawn in an external lane; the agent "
                            "would write into a party outside the organisation" % (label, row["System"]))
    return problems, notes


def validate_write_grants(lines, tables, map_path=None):
    """Rules R1-R8, R3b and floor flags F1-F2 (F3 as a NOTE) on the `### Write grants` table.
    Returns (problems, notes)."""
    notes = []
    key_control = _key_control_reader(map_path) if map_path else None
    must_not = _must_not_ids(map_path) if map_path else None
    mask = _fence_mask(lines)
    heading_idx = next((i for i, l in enumerate(lines) if WRITE_GRANTS_HEADING_RE.match(l) and not mask[i]), None)
    if heading_idx is None:
        return ["R6: no '### Write grants' section found (required in the authority boundary, even "
                "when the agent writes nothing: one row per notice or handoff)"], notes
    table, problems = _write_grants_section_table(lines, heading_idx)
    if table is None:
        return problems or ["R6: '### Write grants' section has no table"], notes
    header = [_bare(h) for h in table["header"]]
    if header == LEGACY_WRITE_GRANT_HEADER:
        return problems + ["R7: Write grants table has no Recipient column; it is the 13th and last column, "
                "after Access (the one ruled amendment to 0399's table): internal | outside | unclear on "
                "notice rows, n/a on every other row"], notes
    if header != WRITE_GRANT_HEADER:
        return problems + ["R6: Write grants table header must be exactly | %s |; got | %s |"
                % (" | ".join(WRITE_GRANT_HEADER), " | ".join(header))], notes
    if not table["rows"]:
        return problems + ["R6: Write grants table has no rows (every agent at least hands its output to a "
                "person)"], notes
    col = {h: i for i, h in enumerate(WRITE_GRANT_HEADER)}
    seen = set()
    good_rows = []
    why = _why_by_step(read_why_people_stay(lines)[0])
    for n, raw in enumerate(table["rows"], start=1):
        if len(raw) != len(WRITE_GRANT_HEADER):
            problems.append("R5: Write grants row %d has %d cells, expected %d: %r"
                            % (n, len(raw), len(WRITE_GRANT_HEADER), raw))
            continue
        row = {h: _bare(raw[i]) for h, i in col.items()}
        bad_fmt = [h for h in ID_COLUMNS if _strict_value(raw[col[h]]) is None]
        if _strict_steps(raw[col["Steps"]]) is None:
            bad_fmt.append("Steps")
        if bad_fmt:
            problems.append("R5: Write grants row %d has malformed inline formatting in %s: %r"
                            % (n, ", ".join(bad_fmt), raw))
            continue
        row.update({h: _strict_value(raw[col[h]]) for h in ID_COLUMNS})
        gid = row["Grant"]
        label = gid or "row %d" % n
        if not GRANT_ID_RE.match(gid):
            problems.append("R5: Write grants row %d has a malformed Grant id %r (expected WG-<n>, n a "
                            "positive integer)" % (n, gid))
        elif gid in seen:
            problems.append("R5: duplicate Grant id %r" % gid)
        seen.add(gid)
        off = False
        for h, allowed in WRITE_GRANT_ENUMS.items():
            if h == "Access":
                continue
            if row[h] not in allowed:
                off = True
                problems.append("R5: grant %s has %s %r; must be one of %s"
                                % (label, h, row[h], sorted(allowed)))
        if row["Access"] != "Assumed":
            problems.append("R4: grant %s has Access %r; Access is always exactly 'Assumed'"
                            % (label, row["Access"]))
        if not row["Steps"] or not row["System"]:
            problems.append("R5: grant %s has an empty Steps or System cell" % label)
        if off:
            continue
        allowed_recipients = NOTICE_RECIPIENTS if row["Write kind"] == "notice" else {"n/a"}
        if row["Recipient"] not in allowed_recipients:
            problems.append("R7: grant %s (Write kind %r) has Recipient %r; must be one of %s"
                            % (label, row["Write kind"], row["Recipient"], sorted(allowed_recipients)))
        if key_control and row["Key control"] != "Yes":
            problem, note = _key_control_problem(label, row, key_control)
            if problem:
                problems.append(problem)
            if note:
                notes.append(note)
        good_rows.append(row)
        person = row["Who writes"] == "person"
        reasons = set().union(*[why.get(x.strip(), set()) for x in row["Steps"].split(",")]) if why else set()
        if person and row["Write kind"] == "mechanical" and row["Key control"] != "Yes":
            notes.append("NOTE F1: grant %s is a mechanical write (values a person already confirmed) kept with "
                         "a person while Key control is %r; the agent can enter confirmed values"
                         % (label, row["Key control"]))
        if person and row["Write kind"] == "notice" and row["Recipient"] == "internal" and not reasons:
            item, how = _direction_keeps_with_people(gid, must_not, lines, raw)
            notes.append("NOTE F2: grant %s is an internal notice sent by a person %s"
                         % (label, "with no Why people stay row" if not item else
                            "kept with people by design direction %s (must not; %s)"
                            % (item, "the map's Design direction row cites it" if how == "direction-row"
                               else "the grant row names it")))
        if person and "enter" in row["Next rung"].lower() and "confirmed" in row["Next rung"].lower():
            notes.append("NOTE F3: grant %s is written by a person but its Next rung restates the default "
                         "(the agent enters values a person confirmed): %r" % (label, row["Next rung"]))
        exception = row["Exception reason"] != "none"
        evidence = row["Step-up evidence"].lower()
        if person:
            if row["Next rung"].lower() in ("", "n/a"):
                problems.append("R3: grant %s is written by a person but has no Next rung" % label)
            if not _has_step_up_keyword(evidence) and not any(r in evidence for r in reasons | set(WHY_REASONS)):
                notes.append("NOTE R3: grant %s is written by a person but its Step-up evidence %r names neither "
                             "its Why people stay reason nor any of %s"
                             % (label, row["Step-up evidence"], list(STEP_UP_KEYWORDS)))
        if exception:
            if not _has_step_up_keyword(evidence):
                notes.append("NOTE R3b: grant %s has Exception reason %r but its Step-up evidence %r names "
                             "none of %s" % (label, row["Exception reason"], row["Step-up evidence"],
                                             list(STEP_UP_KEYWORDS)))
            notes.append("NOTE: grant %s carries Exception reason %r (descriptive only)"
                         % (label, row["Exception reason"]))
    people_problems, people_notes = validate_people_stay(lines, tables, good_rows, map_path)
    return problems + people_problems, notes + people_notes


MOVE_HEADER = ["Move", "Kind", "Current steps", "Future steps", "Evidence", "State", "How the outcome is kept"]
OUTCOME_HEADER = ["Current ending", "Future ending", "Reason and evidence"]
MOVE_KINDS = ("route at entry", "consolidate repeated reads", "package handoffs", "improve decision quality",
              "consolidate exceptions", "remove", "merge", "reorder")
MOVE_ID_RE = re.compile(r"^RM-[1-9][0-9]*$")
ANY_GRANT_OR_MOVE_RE = re.compile(r"^(RM|WG)-[0-9]+$")
NOT_ADOPTED_RE = re.compile(r"^not adopted:\s*\S")
EMPTY_FUTURE_STEPS = {"", "\u2014", "-", "n/a", "none"}
EVIDENCE_PLACEHOLDERS = {"", "n/a", "na", "tbd", "none", "-", "...", "\u2014"}
RESHAPE_HEADING_RE = re.compile(r"^\s{0,3}##\s+reshape moves\s*$", re.IGNORECASE)
NO_RESHAPE_HEADING_RE = re.compile(r"^\s{0,3}###\s+no reshape\s*$", re.IGNORECASE)
OUTCOMES_HEADING_RE = re.compile(r"^\s{0,3}##\s+outcomes\s*$", re.IGNORECASE)


def _section(lines, heading_re):
    """(heading index, body lines up to the next heading), or (None, None) when the heading is absent."""
    idx = next((i for i, l in enumerate(lines) if heading_re.match(l)), None)
    if idx is None:
        return None, None
    end = next((i for i in range(idx + 1, len(lines)) if ANY_HEADING_RE.match(lines[i])), len(lines))
    return idx, lines[idx + 1:end]


def _first_table(body):
    tables = find_all_tables(body)
    return tables[0] if tables else None


HRULE_RE = re.compile(r"^\s{0,3}([-*_])(\s*\1){2,}\s*$")


def _outcomes_stray_lines(body):
    """Non-blank lines after the Outcomes header and separator that are not well-formed table rows.

    A row must start and end with a pipe. A blank line ends the table; any later non-blank line before the
    next heading (or a `---` rule) is stray, so rows split off by a blank line are never silently skipped."""
    sep = next((i for i, l in enumerate(body) if SEPARATOR_RE.match(l) and i > 0 and "|" in body[i - 1]), None)
    if sep is None:
        return []
    stray, ended = [], False
    for line in body[sep + 1:]:
        text = line.strip()
        if HRULE_RE.match(line):
            break
        if not text:
            ended = True
            continue
        if ended or not (text.startswith("|") and text.endswith("|") and len(text) > 1):
            stray.append(line)
    return stray


def _evidence_tokens(cell):
    """Backticked spans, plus the comma/space separated words of the cell (pin P5)."""
    tokens = [t.strip() for t in re.findall(r"`([^`]*)`", cell)]
    tokens += re.split(r"[\s,]+", cell.replace("`", " "))
    out = []
    for t in tokens:
        t = t.strip().strip(".;:()[]{}\"'")
        if t.lower() not in EVIDENCE_PLACEHOLDERS and t not in out:
            out.append(t)
    return out


def _load_ids(path, keys):
    import json
    try:
        data = json.load(open(path, encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    ids = set()
    if not isinstance(data, dict):
        return ids
    for key in keys:
        for item in data.get(key) or []:
            if isinstance(item, dict) and item.get("id") is not None:
                ids.add(str(item["id"]))
    return ids


def _known_evidence_ids(map_path):
    """Snapshot source/issue ids and current-state issue ids, or None when no snapshot sits beside the run."""
    folder = os.path.dirname(os.path.abspath(map_path))
    snapshot = os.path.join(folder, "..", "..", "inputs", "snapshot.json")
    if not os.path.isfile(snapshot):
        return None
    ids = _load_ids(snapshot, ("sources", "issues"))
    current = os.path.join(folder, "..", "current-state", "current-state.json")
    if os.path.isfile(current):
        ids |= _load_ids(current, ("issues",))
    return ids


def validate_reshape(lines, node_table, map_path):
    """Rules RS1-RS6 on `## Reshape moves`, `### No reshape` and `## Outcomes`. Returns problems."""
    problems = []
    node_ids = {_bare(r[0]) for r in node_table["rows"] if r}
    known_ids = _known_evidence_ids(map_path)
    covered = set()
    moves_read = False  # RS4 is judged only when both the moves table and the no-reshape list were read

    idx, body = _section(lines, RESHAPE_HEADING_RE)
    if idx is None:
        problems.append("RS1: no '## Reshape moves' section found (required before the per-node disposition "
                        "table, header | %s |)" % " | ".join(MOVE_HEADER))
    else:
        table = _first_table(body)
        header = [_bare(h) for h in table["header"]] if table else None
        if header != MOVE_HEADER:
            problems.append("RS1: '## Reshape moves' table header must be exactly | %s |; got %s"
                            % (" | ".join(MOVE_HEADER), "| %s |" % " | ".join(header) if header else "no table"))
        else:
            moves_read = True
            for n, raw in enumerate(table["rows"], start=1):
                if len(raw) != len(MOVE_HEADER):
                    problems.append("RS1: Reshape moves row %d has %d cells, expected %d: %r"
                                    % (n, len(raw), len(MOVE_HEADER), raw))
                    continue
                row = dict(zip(MOVE_HEADER, raw))
                move = _bare(row["Move"])
                label = move or "row %d" % n
                if not MOVE_ID_RE.match(move):
                    problems.append("RS1: Reshape moves row %d has a malformed Move id %r (expected RM-<n>)"
                                    % (n, move))
                state = _bare(row["State"])
                if state != "Assumed" and not NOT_ADOPTED_RE.match(state):
                    problems.append("RS1: move %s has State %r; must be 'Assumed' or 'not adopted: <reason>'"
                                    % (label, state))
                kind = _bare(row["Kind"])
                if (not NOT_ADOPTED_RE.match(state) and kind != "remove"
                        and _bare(row["Future steps"]).lower() in EMPTY_FUTURE_STEPS):
                    problems.append("RS6: move %s is adopted but names no Future steps (%r); name the future-state "
                                    "step ids it produces or keeps, or mark it 'not adopted: <reason>'"
                                    % (label, row["Future steps"].strip()))
                if kind not in MOVE_KINDS:
                    problems.append("RS2: move %s has Kind %r; must be one of the eight move kinds: %s"
                                    % (label, kind, ", ".join(MOVE_KINDS)))
                else:
                    covered.add(kind)
                tokens = [t for t in _evidence_tokens(row["Evidence"])
                          if t not in node_ids and not ANY_GRANT_OR_MOVE_RE.match(t)]
                if not tokens:
                    problems.append("RS3: move %s has Evidence %r holding no snapshot or issue id (step ids, "
                                    "RM-<n> and WG-<n> alone do not count)" % (label, row["Evidence"]))
                elif known_ids is not None and not any(t in known_ids for t in tokens):
                    problems.append("RS3: move %s has Evidence %r citing no source or issue id of the run's "
                                    "snapshot or current state" % (label, row["Evidence"]))

    idx, body = _section(lines, NO_RESHAPE_HEADING_RE)
    if idx is None:
        problems.append("RS1: no '### No reshape' section found (directly under the Reshape moves table, one "
                        "'- <kind>: <reason>' bullet per move kind with no move)")
    else:
        for line in body:
            m = re.match(r"^\s*[-*]\s+(.*)$", line)
            if not m:
                continue
            kind, sep, reason = m.group(1).partition(":")
            kind = _bare(kind).lower()
            if kind not in MOVE_KINDS:
                problems.append("RS2: no-reshape line %r names kind %r; must be one of the eight move kinds: %s"
                                % (line.strip(), kind, ", ".join(MOVE_KINDS)))
            elif not sep or not reason.strip():
                problems.append("RS4: no-reshape line for kind %r gives no reason (write '- %s: <reason>')"
                                % (kind, kind))
            else:
                covered.add(kind)
    if idx is not None and moves_read:
        for kind in MOVE_KINDS:
            if kind not in covered:
                problems.append("RS4: move kind %r has neither a Reshape moves row nor a no-reshape line" % kind)

    idx, body = _section(lines, OUTCOMES_HEADING_RE)
    if idx is None:
        problems.append("RS1: no '## Outcomes' section found (header | %s |)" % " | ".join(OUTCOME_HEADER))
    else:
        table = _first_table(body)
        header = [_bare(h) for h in table["header"]] if table else None
        stray = _outcomes_stray_lines(body) if header == OUTCOME_HEADER else []
        for line in stray:
            problems.append("RS5: Outcomes section has a malformed row (every line after the table header up to "
                            "the next heading must be a | ... | row with no blank line inside the table): %r"
                            % line.strip())
        if header != OUTCOME_HEADER:
            problems.append("RS1: '## Outcomes' table header must be exactly | %s |; got %s"
                            % (" | ".join(OUTCOME_HEADER), "| %s |" % " | ".join(header) if header else "no table"))
        elif not table["rows"]:
            problems.append("RS5: Outcomes table has no rows (one row per current-state ending)")
        else:
            for n, raw in enumerate(table["rows"], start=1):
                if len(raw) != len(OUTCOME_HEADER):
                    problems.append("RS5: Outcomes row %d has %d cells, expected %d: %r"
                                    % (n, len(raw), len(OUTCOME_HEADER), raw))
                elif not _bare(raw[0]) or not _bare(raw[1]):
                    problems.append("RS5: Outcomes row %d has an empty Current ending or Future ending (Future "
                                    "ending is a future-state end-event id, or 'retired'): %r" % (n, raw))
    return problems


HEADING_LEVEL_RE = re.compile(r"^\s{0,3}(#{1,6})(\s|$)")
TABLE_SEPARATOR_RE = re.compile(r"^\|(\s*:?-+:?\s*\|)+$")


def _fence_mask(lines):
    """Per line, True when it belongs to a fenced block or an HTML comment (_md_blocks.mask); tracked from the start."""
    return _md_blocks.mask(lines)


def _has_pipe(line):
    """A pipe outside code spans; an escaped pipe (`\\|`) is a character, not a pipe (_md_blocks.has_pipe)."""
    return _md_blocks.has_pipe(line)


def _is_table_row(line):
    text = line.strip().replace("\\|", "")
    return len(text) > 1 and text.startswith("|") and text.endswith("|")


def _region_end(lines, heading_idx, mask):
    """A region runs from a heading (or the document start, heading_idx -1) to the next heading of any level."""
    return next((i for i in range(heading_idx + 1, len(lines)) if not mask[i] and HEADING_LEVEL_RE.match(lines[i])),
                len(lines))


def _section_regions(lines, heading_idx, mask):
    """Heading indexes of the regions in the section at heading_idx: its own region and each subsection's,
    up to the next heading of the same or higher level (-1 alone: the region before the first heading)."""
    if heading_idx < 0:
        return [-1]
    level = len(HEADING_LEVEL_RE.match(lines[heading_idx]).group(1))
    out = [heading_idx]
    for i in range(heading_idx + 1, len(lines)):
        h = HEADING_LEVEL_RE.match(lines[i]) if not mask[i] else None
        if h:
            if len(h.group(1)) <= level:
                break
            out.append(i)
    return out


def region_table_problems(lines, heading_idx, mask):
    """Strict table rule v2 for one region: its pipe-bearing lines form at most one contiguous block of lines
    that start and end with `|`, whose first two lines are a header and a separator. Returns [(index, why)]."""
    end = _region_end(lines, heading_idx, mask)
    blocks, current = [], None
    for i in range(heading_idx + 1, end):
        if not mask[i] and _is_table_row(lines[i]):
            if current is None:
                current = []
                blocks.append(current)
            current.append(i)
        else:
            current = None
    block = blocks[0] if blocks else []
    out = [(i, "holds a pipe outside the region's one table (a row missing an outer pipe, a row after a blank "
               "line, a second table, or a header or separator without outer pipes)")
           for i in range(heading_idx + 1, end) if not mask[i] and _has_pipe(lines[i]) and i not in block]
    if block and not (len(block) >= 2 and TABLE_SEPARATOR_RE.match(lines[block[1]].strip())):
        out.append((block[0], "holds a pipe outside a table: its block has no header and separator row "
                              "(e.g. an orphan row)"))
    return sorted(out)


def validate_strict_tables(lines, node_table, write_grants_idx=None):
    """TABLE rule (v2) on every region of the Reshape moves, per-node, Sub-Agent Groups and Write grants
    sections (the Write grants region itself is read by R5/R6). Returns problems."""
    mask = _fence_mask(lines)
    hdr = node_table["header_line_idx"]
    heads = [next((i for i, l in enumerate(lines) if RESHAPE_HEADING_RE.match(l) and not mask[i]), None),
             next((i for i in range(hdr, -1, -1) if ANY_HEADING_RE.match(lines[i]) and not mask[i]), None),
             next((i for i, l in enumerate(lines) if HEADING_RE.match(l) and not mask[i]), None),
             write_grants_idx]
    if heads[1] is None:
        heads[1] = -1  # per-node table with no heading above it: its region runs from the start to the next heading
    regions = set()
    for idx in heads:
        if idx is not None:
            regions.update(_section_regions(lines, idx, mask))
    regions.discard(write_grants_idx)
    problems = []
    for idx in sorted(regions):
        name = lines[idx].strip() if idx >= 0 else "(start of document, no heading above the per-node table)"
        problems += ["TABLE: '%s' line %d %s: %r" % (name, i + 1, why, lines[i].strip())
                     for i, why in region_table_problems(lines, idx, mask)]
    return problems


def main(argv):
    verbose = False
    rest = []
    for a in argv:
        if a in ("--verbose", "-v"):
            verbose = True
        else:
            rest.append(a)
    if len(rest) != 1:
        die("usage: check-transformation-map-shape.sh [--verbose] TRANSFORMATION_MAP.md", 2)
    path = rest[0]
    if not os.path.isfile(path):
        die("Missing transformation map: %s" % path, 2)
    text = open(path, "r", encoding="utf-8", errors="replace").read()
    # Strict table rule v2: nothing inside a fenced block or an HTML comment counts, so an example there never
    # stands in for the real section. Those lines (fence and comment lines included) are read as blank; line
    # numbers stay the same.
    lines = text.splitlines()
    block_problems = ["BLOCK: line %d holds %s" % (n, why) for n, why in _md_blocks.problems(lines)]
    mask = _fence_mask(lines)
    lines = ["" if mask[i] else line for i, line in enumerate(lines)]

    tables = find_all_tables(lines)
    if not tables:
        die("setup: no markdown pipe-table found in %s" % path, 2)

    node_table = find_per_node_table(tables)
    if node_table is None:
        die("setup: no per-node table found with both a 'Shape Classification' and a "
            "'Sub-Agent Group' column in %s" % path, 2)

    group_table = find_group_table(lines, tables)

    placement, problems, reason_or_setup = validate_and_place(node_table, group_table, verbose)

    if placement is None and reason_or_setup == "setup":
        die("setup error for %s:\n  - %s" % (path, "\n  - ".join(problems)), 2)

    reshape_problems = validate_reshape(lines, node_table, path)
    grant_problems, grant_notes = validate_write_grants(lines, tables, path)
    mask = _fence_mask(lines)
    grants_idx = next((i for i, l in enumerate(lines) if WRITE_GRANTS_HEADING_RE.match(l) and not mask[i]), None)
    problems = (block_problems + list(problems) + reshape_problems
                + validate_strict_tables(lines, node_table, grants_idx) + grant_problems)
    for note in grant_notes:
        print(note)

    if problems:
        die("transformation-map shape check FAILED for %s:\n  - %s"
            % (path, "\n  - ".join(problems)), 1)

    print("PASS: transformation-map shape is valid (agent-taken rows classified; no group on "
          "script-eligible rows; group declarations reconcile; no dangling/self-referential "
          "handoff; no placeholder basis/gating-decision text).")
    if placement == "spectrum-intermediate":
        print("PLACEMENT spectrum-intermediate (REASON: %s)" % reason_or_setup)
    else:
        print("PLACEMENT %s" % placement)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
