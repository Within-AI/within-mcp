#!/usr/bin/env python3
# This file is Python despite its .sh name: run it directly or as `python3 <this file>`; `bash <this file>` cannot run it.
"""Interface-design structure check — the interface-design stage's per-file content release gate.

Plain-language: "Is this ONE interface design well-formed — required sections present, values from the
closed sets, references resolving, opens placed by kind?" (Former internal tag: C2. The plain-language
question is the handle.)

WHAT THIS CHECK IS — AND IS NOT (the enforcement model, R1 §2.5). This check is *told* its single target:
the orchestrator enumerates the design set from the coverage manifest and hands each design to this check
explicitly, as its first positional argument. The check does exactly one kind of work — STRUCTURE and
CLOSED/ENUMERATED SETS: required sections present, values drawn from a closed set, references resolve,
opens placed by kind. It NEVER self-detects "am I a design" (detection is the orchestrator's job, not a
check's — this is why the prior self-detection weakness, residual R4-OP-03, is dissolved: there is no
structural signature for an adversary to drop, because membership is asserted upstream). It NEVER judges
SEMANTIC HONESTY — whether a stamp is truly grounded in the named Brain locus, whether a hunt genuinely
searched, whether prose smuggles a decision rule. That soundness judgment is owned by the independent
cross-engine reviewer and the human sign-off gate ("Sign off the stage"). A passing structural check is
NEVER, on its own, evidence that a design is sound.

SET-LEVEL completeness (every enumerated seam represented by exactly one design XOR one well-formed stamped
open) is a SEPARATE gate — the set-completeness gate (`check-interface-seating-directions.sh`), told the
built map's closed seam surface. This check owns ONE design; it never receives or self-partitions a set.

The structural predicates (a script can decide each without judging truth):

  1. REQUIRED S1-S9 STRUCTURE present and of the declared shape (a MISSING required section, an EMPTY input
     shape where the template requires rows, or a NON-MAPPING committed row is a FAILURE, never a silent
     skip): `interface_id`, `artifact_kind` (the closed value below), and a `direction` in the closed six
     (inbound_control, read, outbound_handoff, draft, write = agent to a system, dispatch = agent to another
     agent). The archetypes are read, outbound_handoff, draft_surface, inbound_control, and the two write-
     stance archetypes `write_surface` (paired with direction `write`) and `dispatch` (paired with direction
     `dispatch`); both fill the flat `fields:` block, like `outbound_handoff`. The archetype value itself is
     not enforced here (it never was); the input-shape rule below is;
     the mapping sections `scope` (S1), `inputs` (S2), `authority_boundary` (S4), `owner_escalation` (S6),
     `referenced_material` (S8); and the non-empty list sections `outputs_back_signals` (S3),
     `failure_modes` (S5), `eval_hooks` (S7), `boundary_membership` (S9). `scope` carries `in`/`out`
     lists; `inputs` must yield at least one field-row (a flat `fields` list, or the two inbound-control
     sub-blocks) — an empty input shape fails. Every committed row in a committed collection (scope claims,
     input field-rows, back-signals, failure modes, boundary-membership records, assumptions, residual) must
     be a MAPPING; a non-mapping row fails rather than being filtered away. Container TYPE is validated
     BEFORE any traversal: `assumptions`, `residual`, and the present `inputs` subcontainers
     (`inputs.fields`, `inputs.control_inputs` and its `invocation`/`decision_state`) must be the declared
     shape — a slot that should be a list but is a mapping/scalar (or vice versa) FAILS rather than being
     read as empty and skipped, so a malformed container cannot bypass the access-as-assumption placement
     check or the opens-split-by-kind check. (Whether each declared section's values are TRUE is the
     review's job; that each is PRESENT and well-SHAPED is this check's.)
  2. CLOSED evidence/access states: every `evidence_state` AND `access_state` value is one of the canonical
     four {Observed, Assumed, Contradictory, needs_more_information}. The retired fifth `SOP-documented`
     state fails as an out-of-set value.
  3. COMMITTED-ELEMENT STAMPING (presence, not truth): every committed element — S2 input fields, inbound-
     control invocation + decision_state rows, scope.in / scope.out claims, S3 back-signals, S4
     authority_boundary, S6 owner_escalation, S9 boundary_membership, failure_modes[] rows, assumptions,
     residual — carries the PRESENCE of a non-empty `evidence_state` AND a non-empty `source`. A missing
     stamp key is a failure, not a skip. (Whether the source truly grounds the stamp is the review's job.)
  4. CLAIM_REF RESOLUTION: every `claim_ref` (on each scope entry and each S2 field-row) is present and
     resolves to an existing `boundary_membership[].id`. Missing or dangling fails.
  5. AUTHORITY BOUNDARY well-formed: `agent_write` is a boolean; `full_stop` is non-empty. The write rule
     (design §4): direction `write` requires `agent_write: true`; `agent_write: true` requires
     `authority_boundary.write_grant: WG-n` (n a positive integer) and a direction from {write,
     outbound_handoff, draft, dispatch}; `agent_write: false` stays valid on every other direction. A
     person-routed system-of-record write has no write interface — the agent's side of it is an
     outbound_handoff to the person. (Whether the grant id exists in the transformation map's Write grants
     table is the coherence gate's lookup, not this check's.)
  6. OPENS SPLIT BY KIND — by PLACEMENT (not by reading prose):
       * access is ONLY under `assumptions[]` — an `access_state` key anywhere else fails;
       * a `needs_more_information` field / back-signal carries a non-empty `needs:` note (a value open);
       * a rule/policy open is a `residual` (`kind: rule`) and a scope open a `residual` (`kind: scope`),
         each with a non-empty `follow_on`; a field / back-signal carrying a `follow_on` fails (a rule open
         smuggled into the field layer); a data-field open stays a stamped `needs_more_information` field;
       * every `residual` is `kind: rule` or `kind: scope`; every `assumptions` entry is stamped `Assumed`;
       * WRITE TARGET: a `write` or `dispatch` design carries a non-empty top-level `target` (for `write` the
         system name, for `dispatch` the receiving sub-agent id), and at least one of its `assumptions[]`
         entries stamped `access_state: Assumed` contains that target in one of its text values
         (case-insensitive substring, whitespace collapsed to single spaces). A missing target or a target
         no Assumed assumption names fails; a write or dispatch design with no Assumed assumption at all
         fails as before, on that line alone.
  7. HUNT-RECORD WELL-FORMEDNESS (the SHAPE of the proof, never whether it is true): if
     `hunt.result ∈ {empty, reconciled}` then `search_scope` and `reconciliation_evidence` are non-empty;
     an `Observed` membership has `hunt_performed: true`, a non-empty `search_scope`, and a cleared result;
     a `contradictory` result is not stamped `Observed` / `Assumed`.
  8. S-GUARD (forbidden keys): no `rule_sets` / `gates` / `dispatch` / `escalation_triggers` / `thresholds`
     / `special_cases` block, and no `default` / `interim` / `fallback` key, anywhere. Condition->outcome
     authoring is the decision-rules stage. (`rule_set_ref` is a reference, not `rule_sets` — allowed.)
  9. FAILURE-MODE FIXTURE SHAPE: each `failure_modes[].fixture` carries a non-empty `synthetic_case`, an
     `expected_kind` of exactly `authority_boundary` (the only permitted value — a closed set), and a
     non-empty `expected`. Whether the `expected` prose reads as a full-stop hand-over vs a smuggled rule
     is a SEMANTIC judgment — it belongs to the review, not here.
 10. ONE NAME FOR THE CHECK DECLARATIONS: S7 is `eval_hooks` and its field-check kind is `field_validator` —
     the exact names the eval stage's check engine (`eval-stage/framework/evals/checks/engine.py`) reads. A
     design that still carries the retired section name or the retired field-check kind name (the two
     spellings in RETIRED_SECTION_KEYS / RETIRED_CHECK_KINDS below) fails here, with a message naming the new
     spelling, so old material is caught at the interface-design stage rather than when the engine finds no
     `eval_hooks` and fails closed three stages later.

Any predicate violated -> exit 1, each reported on its own problem line.

Like the other factory checks this is a Mechanism-A re-verification against a lazy agent, NOT a semantic
judge. `actor.engine == script`; the single writer re-runs it and re-derives the SHA at append time, so the
recorded result is re-verified, never trusted from the payload. Deterministic and writer-re-derivable.

Parsing: PyYAML is REQUIRED. yaml.safe_load parses BOTH a `.yaml` design and a `.graph.json`. If PyYAML is
unavailable the check exits 2 (setup error) with a clear message; it never degrades silently.

Usage: check-interface-design-structure.sh [--verbose] ONE_INTERFACE_DESIGN
Exit: 0 pass; 1 structural assertion failure; 2 setup error (incl. PyYAML absent).
"""
import os
import re
import sys

CANONICAL_FOUR = {"Observed", "Assumed", "Contradictory", "needs_more_information"}
CLEARED_RESULTS = {"empty", "reconciled"}
COMMITTED_DESIGN_KIND = "agent-seam-interface-design"
DIRECTIONS = {"inbound_control", "read", "outbound_handoff", "draft", "write", "dispatch"}
# the directions on which the agent may hold a write (`agent_write: true`) under a write grant.
WRITE_DIRECTIONS = {"write", "outbound_handoff", "draft", "dispatch"}
WRITE_GRANT_RE = re.compile(r"^WG-[1-9][0-9]*$")
# condition->outcome block keys the S-guard forbids (the decision-rules stage owns them).
BANNED_BLOCK_KEYS = {"rule_sets", "gates", "dispatch", "escalation_triggers", "thresholds", "special_cases"}
# interim/default/fallback keys the stage must never author for an open.
BANNED_INTERIM_KEYS = {"default", "interim", "fallback"}
# RETIRED SPELLINGS (predicate 10) — deliberate: these old names are listed ONLY so a design still carrying
# them is REFUSED with a message naming the new spelling. The eval stage's check engine reads `eval_hooks`
# and dispatches `field_validator`; a design written with the old names would build zero checks there.
RETIRED_SECTION_KEYS = {"implied_checks": "eval_hooks"}
RETIRED_CHECK_KINDS = {"payload_shape_validator": "field_validator"}


def die(msg, code):
    sys.stderr.write(msg + "\n")
    sys.exit(code)


def _nonempty(v):
    return isinstance(v, str) and v.strip() != ""


def _walk_dicts(obj):
    """Yield every mapping node anywhere in the nested structure."""
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            for d in _walk_dicts(v):
                yield d
    elif isinstance(obj, list):
        for item in obj:
            for d in _walk_dicts(item):
                yield d


def _walk_keys(obj):
    for node in _walk_dicts(obj):
        for k in node.keys():
            yield k


def _input_field_rows(design):
    """Every S2 field-row across the archetype-dependent input shapes (flat `fields` or the two
    inbound-control sub-blocks)."""
    rows = []
    inp = design.get("inputs") if isinstance(design.get("inputs"), dict) else {}
    if isinstance(inp.get("fields"), list):
        rows += [(r, "inputs.fields") for r in inp["fields"] if isinstance(r, dict)]
    ci = inp.get("control_inputs") if isinstance(inp.get("control_inputs"), dict) else {}
    for sub in ("invocation", "decision_state"):
        if isinstance(ci.get(sub), list):
            rows += [(r, "inputs.control_inputs.%s" % sub) for r in ci[sub] if isinstance(r, dict)]
    return rows


def _output_signal_rows(design):
    if isinstance(design.get("outputs_back_signals"), list):
        return [(r, "outputs_back_signals") for r in design["outputs_back_signals"] if isinstance(r, dict)]
    return []


def _open_scan_rows(design):
    """The rows the open-by-kind placement scans inspect: S2 input fields AND S3 back-signals (a value or
    rule open can be smuggled onto an output row too)."""
    return _input_field_rows(design) + _output_signal_rows(design)


def _scope_claim_rows(design):
    rows = []
    scope = design.get("scope") if isinstance(design.get("scope"), dict) else {}
    for side in ("in", "out"):
        if isinstance(scope.get(side), list):
            rows += [(e, "scope.%s claim %r" % (side, e.get("item"))) for e in scope[side] if isinstance(e, dict)]
    return rows


def _committed_elements(design):
    """(node, label) for every committed element class the honesty bar STAMPS — each must carry the
    presence of a non-empty evidence_state and source (a missing key is a failure, not a skip)."""
    out = []
    for row, where in _input_field_rows(design):
        out.append((row, "%s field %r" % (where, row.get("name") or row.get("token"))))
    out += _scope_claim_rows(design)
    for s, _ in _output_signal_rows(design):
        out.append((s, "outputs_back_signals %r" % (s.get("name") or s.get("token"))))
    if isinstance(design.get("authority_boundary"), dict):
        out.append((design["authority_boundary"], "authority_boundary (S4)"))
    if isinstance(design.get("owner_escalation"), dict):
        out.append((design["owner_escalation"], "owner_escalation (S6)"))
    for bm in (design.get("boundary_membership") if isinstance(design.get("boundary_membership"), list) else []):
        if isinstance(bm, dict):
            out.append((bm, "boundary_membership %r" % bm.get("id")))
    for fm in (design.get("failure_modes") if isinstance(design.get("failure_modes"), list) else []):
        if isinstance(fm, dict):
            out.append((fm, "failure_modes %r" % fm.get("id")))
    for a in (design.get("assumptions") if isinstance(design.get("assumptions"), list) else []):
        if isinstance(a, dict):
            out.append((a, "assumptions[surface=%r]" % a.get("surface")))
    for r in (design.get("residual") if isinstance(design.get("residual"), list) else []):
        if isinstance(r, dict):
            out.append((r, "residual %r" % r.get("id")))
    return out


# The declared S1-S9 sections and the top-level shape each must have.
# The one residual-kind list (shared with the decision-rules open register and the templates): a policy/rule open
# is `rule`, a scope open is `scope`; a data-field open is a stamped field and access is an `assumptions` entry.
RESIDUAL_KINDS = ("rule", "scope")
MAPPING_SECTIONS = (("scope", "S1"), ("inputs", "S2"), ("authority_boundary", "S4"),
                    ("owner_escalation", "S6"), ("referenced_material", "S8"))
LIST_SECTIONS = (("outputs_back_signals", "S3"), ("failure_modes", "S5"),
                 ("eval_hooks", "S7"), ("boundary_membership", "S9"))


def _committed_collections(design):
    """(path, list) for every collection whose entries are COMMITTED rows and must each be a MAPPING (a
    non-mapping row is a failure, never a silent skip). Only well-typed containers are descended — a
    mis-typed container is reported by the section-shape pass, not here."""
    out = []
    scope = design.get("scope")
    if isinstance(scope, dict):
        for side in ("in", "out"):
            if isinstance(scope.get(side), list):
                out.append(("scope.%s" % side, scope[side]))
    inp = design.get("inputs")
    if isinstance(inp, dict):
        if isinstance(inp.get("fields"), list):
            out.append(("inputs.fields", inp["fields"]))
        ci = inp.get("control_inputs")
        if isinstance(ci, dict):
            for sub in ("invocation", "decision_state"):
                if isinstance(ci.get(sub), list):
                    out.append(("inputs.control_inputs.%s" % sub, ci[sub]))
    for key in ("outputs_back_signals", "failure_modes", "boundary_membership",
                "eval_hooks", "assumptions", "residual"):
        if isinstance(design.get(key), list):
            out.append((key, design[key]))
    return out


def _norm_ws(text):
    return " ".join(str(text).split()).lower()


def _string_values(obj):
    if isinstance(obj, str):
        return [obj]
    if isinstance(obj, dict):
        return [s for v in obj.values() for s in _string_values(v)]
    if isinstance(obj, list):
        return [s for v in obj for s in _string_values(v)]
    return []


def validate(design):
    P = []
    if not isinstance(design, dict):
        return ["the interface design is not a mapping (expected one interface-design document)"]

    # ---- 1. required S1-S9 structure present and well-shaped ----
    #   REQUIRE the declared structure: a missing required section, an empty input shape where the template
    #   requires rows, or a non-mapping committed row is a FAILURE — never a silent skip.
    if not _nonempty(design.get("interface_id")):
        P.append("(1) required `interface_id` is missing/empty")
    if design.get("artifact_kind") != COMMITTED_DESIGN_KIND:
        P.append("(1) `artifact_kind` is %r — a committed interface design must carry exactly %r (a closed "
                 "value)" % (design.get("artifact_kind"), COMMITTED_DESIGN_KIND))
    if design.get("direction") not in DIRECTIONS:
        P.append("(1) `direction` is %r — must be one of the closed six %s"
                 % (design.get("direction"), sorted(DIRECTIONS)))
    # (1a) mapping sections present and a mapping.
    for key, s in MAPPING_SECTIONS:
        if key not in design:
            P.append("(1) required section `%s` (%s) is missing — the declared S1-S9 structure is required, "
                     "not optional" % (key, s))
        elif not isinstance(design.get(key), dict):
            P.append("(1) required section `%s` (%s) must be a mapping, got %s"
                     % (key, s, type(design.get(key)).__name__))
    # (1b) list sections present, a list, non-empty, each row a mapping.
    for key, s in LIST_SECTIONS:
        v = design.get(key)
        if key not in design:
            P.append("(1) required section `%s` (%s) is missing — the declared S1-S9 structure is required, "
                     "not optional" % (key, s))
        elif not isinstance(v, list):
            P.append("(1) required section `%s` (%s) must be a list, got %s" % (key, s, type(v).__name__))
        elif not v:
            P.append("(1) required section `%s` (%s) is an empty list — the template requires at least one "
                     "row here" % (key, s))
    # (1c) `scope` (S1) carries `in`/`out` lists.
    scope = design.get("scope")
    if isinstance(scope, dict):
        for side in ("in", "out"):
            if side not in scope:
                P.append("(1) `scope.%s` (S1) is missing — scope states both its in and its out claims" % side)
            elif not isinstance(scope.get(side), list):
                P.append("(1) `scope.%s` (S1) must be a list, got %s" % (side, type(scope.get(side)).__name__))
    # (1d) `inputs` (S2) must yield at least one field-row (empty input shape fails).
    if isinstance(design.get("inputs"), dict):
        if not _input_field_rows(design):
            P.append("(1) `inputs` (S2) carries no field-row (empty input shape) — fill a non-empty `fields` "
                     "list, or the two inbound-control sub-blocks `invocation` + `decision_state`")
    # (1e) every committed row in a committed collection must be a MAPPING (a non-mapping row fails).
    for path, lst in _committed_collections(design):
        for i, row in enumerate(lst):
            if not isinstance(row, dict):
                P.append("(1) `%s` row %d is not a mapping — a committed row must be a mapping (%s), never "
                         "skipped" % (path, i, type(row).__name__))
    # (1f) CONTAINER TYPE/SHAPE validated BEFORE any traversal that would silently skip a mis-typed
    #   container. A container that should be a LIST but is a mapping/scalar (or should be a MAPPING but is
    #   a list/scalar) is a FAILURE here — never traversed-as-empty. This closes the bypass where a
    #   malformed `assumptions` container slips past the access-as-assumption PLACEMENT check (§6), or a
    #   malformed `residual` container slips past the OPENS-SPLIT-BY-KIND check (§6): both of those walk
    #   `design.get(...) if isinstance(...list)`, so a mapping/scalar in that slot was previously read as
    #   empty and skipped in silence. Type/shape only — no truth judgment.
    def _require_container_type(container, path, expected):
        ok = isinstance(container, dict) if expected == "mapping" else isinstance(container, list)
        if not ok:
            P.append("(1) container `%s` must be a %s, got %s — a mis-typed container is a FAILURE, never "
                     "traversed-as-empty (unchecked it would bypass the access-as-assumption placement / "
                     "opens-split-by-kind checks)" % (path, expected, type(container).__name__))
        return ok
    for _key in ("assumptions", "residual"):
        if _key in design:
            _require_container_type(design.get(_key), _key, "list")
    _inp = design.get("inputs")
    if isinstance(_inp, dict):
        if "fields" in _inp:
            _require_container_type(_inp.get("fields"), "inputs.fields", "list")
        if "control_inputs" in _inp:
            _ci = _inp.get("control_inputs")
            if _require_container_type(_ci, "inputs.control_inputs", "mapping"):
                for _sub in ("invocation", "decision_state"):
                    if _sub in _ci:
                        _require_container_type(_ci.get(_sub), "inputs.control_inputs.%s" % _sub, "list")

    # ---- index the boundary_membership records ----
    bm_list = design.get("boundary_membership") if isinstance(design.get("boundary_membership"), list) else []
    bm_by_id = {}
    for bm in bm_list:
        if isinstance(bm, dict) and _nonempty(bm.get("id")):
            bm_by_id[bm["id"]] = bm

    # ---- 2. closed evidence/access states ----
    for node in _walk_dicts(design):
        if "evidence_state" in node and node.get("evidence_state") not in CANONICAL_FOUR:
            P.append("(2) illegal evidence_state %r — only the canonical four are allowed "
                     "{Observed, Assumed, Contradictory, needs_more_information} (the fifth `SOP-documented` "
                     "state is retired)" % (node.get("evidence_state"),))
        if "access_state" in node and node.get("access_state") not in CANONICAL_FOUR:
            P.append("(2) illegal access_state %r — access is stamped from the canonical four (Assumed)"
                     % (node.get("access_state"),))

    # ---- 3. committed-element stamping (presence of evidence_state AND source) ----
    for node, label in _committed_elements(design):
        if not _nonempty(node.get("evidence_state")):
            P.append("(3) committed element %s carries no non-empty `evidence_state` — a missing stamp key "
                     "is a failure, not a skip" % label)
        if not _nonempty(node.get("source")):
            P.append("(3) committed element %s carries no non-empty `source`" % label)

    # ---- 4. claim_ref resolution (scope entries + S2 fields) ----
    ref_bearers = []
    scope = design.get("scope") if isinstance(design.get("scope"), dict) else {}
    for side in ("in", "out"):
        if isinstance(scope.get(side), list):
            ref_bearers += [(e, "scope.%s" % side) for e in scope[side] if isinstance(e, dict)]
    ref_bearers += _input_field_rows(design)
    for entry, where in ref_bearers:
        cr = entry.get("claim_ref")
        if not _nonempty(cr):
            P.append("(4) a membership-bearing entry in %s carries no claim_ref (%r) — every scope entry "
                     "and S2 field-row keys its boundary-membership claim"
                     % (where, entry.get("name") or entry.get("item")))
        elif cr not in bm_by_id:
            P.append("(4) claim_ref %r in %s is DANGLING — it resolves to no boundary_membership[].id"
                     % (cr, where))

    # ---- 5. authority boundary well-formed ----
    ab = design.get("authority_boundary")
    if isinstance(ab, dict):
        aw = ab.get("agent_write")
        direction = design.get("direction")
        if not isinstance(aw, bool):
            P.append("(5) authority_boundary.agent_write is %r — must be a boolean (`true` only under a write "
                     "grant, else `false`); a missing or non-boolean value fails" % (aw,))
        elif aw:
            grant = ab.get("write_grant")
            if not (isinstance(grant, str) and WRITE_GRANT_RE.match(grant.strip())):
                P.append("(5) authority_boundary.agent_write is true but `write_grant` is %r — an agent write "
                         "names its grant from the transformation map's Write grants table as `WG-n`" % (grant,))
            if direction not in WRITE_DIRECTIONS:
                P.append("(5) authority_boundary.agent_write is true on direction %r — an agent write is only "
                         "valid on %s" % (direction, sorted(WRITE_DIRECTIONS)))
        if direction == "write" and aw is not True:
            P.append("(5) direction `write` requires authority_boundary.agent_write: true (with a `write_grant`); "
                     "got %r — a person-routed write is an outbound_handoff to the person, not a write "
                     "interface" % (aw,))
        if not _nonempty(ab.get("full_stop")):
            P.append("(5) authority_boundary.full_stop is empty/absent — the S4 boundary names the decision "
                     "the part may NOT take (the full stop)")

    # ---- 6. opens split by kind (by PLACEMENT) ----
    assumption_ids = set()
    for a in (design.get("assumptions") if isinstance(design.get("assumptions"), list) else []):
        if isinstance(a, dict):
            assumption_ids.add(id(a))
    for node in _walk_dicts(design):
        if "access_state" in node and id(node) not in assumption_ids:
            loc = node.get("token") or node.get("name") or node.get("id") or node.get("item") or "an element"
            P.append("(6) `access_state` appears outside the `assumptions:` layer (on %r) — access is a "
                     "standing `assumptions:` entry, never a field, back-signal, scope claim, or residual"
                     % loc)
    for row, where in _open_scan_rows(design):
        if row.get("evidence_state") == "needs_more_information" and not _nonempty(row.get("needs")):
            P.append("(6) row %r in %s is stamped needs_more_information but carries no `needs:` note (a "
                     "value open must say what would resolve it)" % (row.get("name") or row.get("token"), where))
        if "follow_on" in row:
            P.append("(6) row %r in %s carries a `follow_on` — a rule/policy open is a residual (kind: rule) "
                     "with a follow_on, never a field or back-signal" % (row.get("name") or row.get("token"), where))
    for r in (design.get("residual") if isinstance(design.get("residual"), list) else []):
        if not isinstance(r, dict):
            continue
        if r.get("kind") not in RESIDUAL_KINDS:
            P.append("(6) residual %r has kind %r — the honesty tail carries rule and scope opens "
                     "(kind: %s); a data-field open is a stamped needs_more_information field and access is an "
                     "`assumptions` entry" % (r.get("id"), r.get("kind"), " | ".join(RESIDUAL_KINDS)))
        elif not _nonempty(r.get("follow_on")):
            P.append("(6) residual %r is kind: %s but carries no `follow_on` — a residual names "
                     "its downstream carrier (the decision-rules stage)" % (r.get("id"), r.get("kind")))
    for a in (design.get("assumptions") if isinstance(design.get("assumptions"), list) else []):
        if isinstance(a, dict) and a.get("access_state") not in (None, "Assumed"):
            P.append("(6) assumptions entry %r access_state is %r — access is stamped `Assumed` only"
                     % (a.get("surface"), a.get("access_state")))
        if isinstance(a, dict) and a.get("evidence_state") not in (None, "Assumed"):
            P.append("(6) assumptions entry %r is stamped %r — an access assumption is `Assumed` only"
                     % (a.get("surface"), a.get("evidence_state")))
    if design.get("direction") in ("write", "dispatch"):
        stamped = [a for a in (design.get("assumptions") if isinstance(design.get("assumptions"), list) else [])
                   if isinstance(a, dict) and a.get("access_state") == "Assumed"]
        if not stamped:
            P.append("(6) direction %r carries no `assumptions[]` entry with `access_state: Assumed` — a write "
                     "or dispatch design states its access to the target system as an Assumed assumption, the "
                     "same as a read surface (design §4)" % design.get("direction"))
        target = design.get("target")
        if not (isinstance(target, str) and target.strip()):
            P.append("(6) direction %r carries no `target` — a write names the system it writes to, a dispatch "
                     "names the receiving sub-agent id" % design.get("direction"))
        elif stamped:
            want = _norm_ws(target)
            if not any(want in _norm_ws(" ".join(_string_values(a))) for a in stamped):
                P.append("(6) target %r is named in no `assumptions[]` entry stamped `access_state: Assumed` — "
                         "the access assumption states its access to the target by name" % target)

    # ---- 7. hunt-record well-formedness (shape of the proof, never its truth) ----
    for bm in bm_list:
        if not isinstance(bm, dict):
            continue
        hunt = bm.get("hunt") if isinstance(bm.get("hunt"), dict) else {}
        result = hunt.get("result")
        es = bm.get("evidence_state")
        if es == "Observed" and not (hunt.get("hunt_performed") is True
                                     and _nonempty(hunt.get("search_scope"))
                                     and result in CLEARED_RESULTS):
            P.append("(7) boundary_membership %r is stamped Observed without a CLEARED hunt record (need "
                     "hunt_performed: true, a non-empty search_scope, and result in {empty, reconciled})"
                     % bm.get("id"))
        if result in CLEARED_RESULTS and not (_nonempty(hunt.get("search_scope"))
                                              and _nonempty(hunt.get("reconciliation_evidence"))):
            P.append("(7) boundary_membership %r has a cleared hunt (result: %s) but is missing a non-empty "
                     "search_scope and/or reconciliation_evidence — a cleared record records BOTH the scope "
                     "it covered AND its reconciliation evidence" % (bm.get("id"), result))
        if result == "contradictory" and es not in ("Contradictory", "needs_more_information"):
            P.append("(7) boundary_membership %r hunt result is `contradictory` but evidence_state is %r — "
                     "an unreconciled contradiction must be `Contradictory` or `needs_more_information`, "
                     "never Observed or Assumed" % (bm.get("id"), es))

    # ---- 8. S-guard (forbidden keys) ----
    keys = set(_walk_keys(design))
    banned_blocks = sorted(keys & BANNED_BLOCK_KEYS)
    if banned_blocks:
        P.append("(8) S-guard: condition->outcome block(s) present %s — no rule_sets/gates/dispatch/"
                 "escalation_triggers/thresholds/special_cases here (that is the decision-rules stage). "
                 "`rule_set_ref` stays a reference only." % banned_blocks)
    banned_interim = sorted(keys & BANNED_INTERIM_KEYS)
    if banned_interim:
        P.append("(8) S-guard: interim/default/fallback key(s) present %s — the stage authors no interim, "
                 "default, or fallback for anything stamped open" % banned_interim)

    # ---- 9. failure-mode fixture shape (structure + closed set only) ----
    for fm in (design.get("failure_modes") if isinstance(design.get("failure_modes"), list) else []):
        if not isinstance(fm, dict):
            continue
        fx = fm.get("fixture") if isinstance(fm.get("fixture"), dict) else {}
        if fx.get("expected_kind") != "authority_boundary":
            P.append("(9) failure_mode %r fixture expected_kind is %r — must be exactly `authority_boundary` "
                     "(the only permitted value)" % (fm.get("id"), fx.get("expected_kind")))
        if not _nonempty(fx.get("synthetic_case")):
            P.append("(9) failure_mode %r fixture carries no non-empty `synthetic_case`" % fm.get("id"))
        if not _nonempty(fx.get("expected")):
            P.append("(9) failure_mode %r fixture `expected` is empty/absent — every fixture states its "
                     "full-stop hand-over outcome (whether that prose reads true is the review's job, not "
                     "this check's)" % fm.get("id"))

    # ---- 10. one name for the check declarations (retired spellings refused, new spelling named) ----
    for old_key in sorted(keys & set(RETIRED_SECTION_KEYS)):
        P.append("(10) retired section name `%s` — the check declarations (S7) are `%s`, the name the eval "
                 "stage's check engine reads; rename the section (under the old name the engine builds no "
                 "checks and fails closed)" % (old_key, RETIRED_SECTION_KEYS[old_key]))
    for node in _walk_dicts(design):
        k = node.get("kind")
        if isinstance(k, str) and k in RETIRED_CHECK_KINDS:
            P.append("(10) retired check kind `%s` (target %r) — the field check is `kind: %s`, the kind the "
                     "eval stage's check engine dispatches; rename it" % (k, node.get("target"),
                                                                       RETIRED_CHECK_KINDS[k]))

    return P


def main(argv):
    verbose = False
    rest = []
    for a in argv:
        if a in ("-h", "--help"):
            print(__doc__)
            return 0
        if a in ("--verbose", "-v"):
            verbose = True
        else:
            rest.append(a)
    if len(rest) != 1:
        die("usage: check-interface-design-structure.sh [--verbose] [--help] ONE_INTERFACE_DESIGN", 2)
    path = rest[0]
    if not os.path.isfile(path):
        die("Missing interface design: %s" % path, 2)
    text = open(path, "r", encoding="utf-8", errors="replace").read()

    try:
        import yaml
    except ImportError:
        die("PyYAML is required for the interface-design structural parse but is not importable. Install "
            "it (pip install pyyaml) and re-run; this check does not fall back to a line scan.", 2)
    try:
        doc = yaml.safe_load(text)
    except Exception as e:
        die("interface design is not parseable (JSON or YAML): %s" % e, 1)

    if isinstance(doc, dict) and isinstance(doc.get("designs"), list):
        die("this check takes exactly ONE interface design, not a `designs:` set — the orchestrator "
            "enumerates the set and hands each design in explicitly; set-level completeness is the "
            "set-completeness gate (check-interface-seating-directions.sh), a separate check.", 2)

    problems = validate(doc)
    if verbose:
        sys.stderr.write("parse engine: pyyaml safe_load (serves both a .yaml design and a .graph.json)\n")
    if problems:
        die("interface-design structure check FAILED for %s:\n  - %s" % (path, "\n  - ".join(problems)), 1)
    print("PASS: required sections present; evidence/access states in the canonical four with a source "
          "each; claim_refs resolve; authority boundary with a full stop and any agent write under a write grant; opens split by kind by "
          "placement; hunt records well-formed; no forbidden condition->outcome block; failure-mode "
          "fixtures authority-boundary-shaped; check declarations under their one name (`eval_hooks`, "
          "`field_validator`). (Structure only — semantic honesty is the review's job.)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
