#!/usr/bin/env python3
# This file is Python despite its .sh name: run it directly or as `python3 <this file>`; `bash <this file>` cannot run it.
"""Backbone stage release gate: the Agent-Builder structural validator for a build-spec spine.

Structurally validates a build-spec.yaml against the backbone spine template's required shape. Like the
other factory checks, this is a Mechanism-A re-verification against a lazy agent (a structural proof that
the spine's load-bearing blocks are present, typed, and in-lane), NOT a semantic judge of whether the
filled values are good and NOT a fail-closed guarantee. `actor.engine == script`; the single writer
re-runs it and re-derives the SHA at append time (see run-check.sh / _checks.py), so the recorded result
is re-verified, never trusted from the payload. It is deterministic and writer-re-derivable: same file in,
same verdict out, independent of cwd.

It asserts:
  1. Required top-level blocks present: apiVersion, kind, meta, inputs, outputs, integrations,
     tool_permission_inventory, sub_agent_surface, decision_rules, end_to_end_loop, monitoring, runbook, scope.
  2. Typing of EVERY required block, not just presence:
       * the mapping blocks (meta, tool_permission_inventory, sub_agent_surface, decision_rules,
         end_to_end_loop, monitoring, runbook, scope) are each a mapping;
       * inputs / outputs / integrations are each a non-empty list of mappings;
       * every input carries name + type + source + evidence_state, and its `type` VALUE is one of the
         allowed input types; every output likewise, with its `type` VALUE one of the allowed output types;
       * every integration carries name + access_mode + source + evidence_state.
  3. The write stance (pinned write-grant design, section 3):
       * every integration's access_mode is one of read / write / read-write; a write or read-write
         integration carries a non-empty `write_grants: [WG-n, ...]`;
       * a top-level `write_grants:` block lists the agent-written rows of the transformation map's Write
         grants table, each {id, system, write_kind, exception_reason, access_state, source}: id is WG-<n>
         and unique, write_kind and exception_reason are in their enums, access_state is exactly Assumed;
       * every grant id named in any integration (whatever its access mode) is a well-formed WG-<n> listed in `write_grants`;
       * tool_permission_inventory.must_not carries exactly one of "all writes" (an agent that writes
         nothing; still valid) or "decision-bearing writes to a system of record without a write grant";
       * `write_grants` may be absent only when must_not carries "all writes".
     The check validates the outline alone; it does not load the transformation map. Whether each
     `write_grants` entry equals its register row is the coherence gate's lookup (INV-10).
  4. The sub-agent SURFACE is declared but NOT decomposed: sub_agent_surface.declared is true (false when
     primitive_form.value == script, which has no coordination surface to declare) and names a
     decomposed_by stage; it must NOT carry a decomposition (no sub_agents / handoffs / edges keys) — that
     is the internal-map stage's lane.
  5. No invented decision-rule values past the cap: decision_rules.cap is present and decision_rules.rules
     is EXPLICITLY PRESENT and an empty list (the backbone leaves room; the full policy is the deepen
     stage's file). An absent `rules:` key is a failure, not a pass — the room must be declared, not implied.
  6. The honesty bar: every evidence_state value is one of Observed / Assumed / Contradictory /
     needs_more_information, and any needs_more_information stamp says what would resolve it — a `needs:`
     note for a value open, or a `follow_on:` for a policy/rule/scope residual in the open register.

Parsing: PyYAML is REQUIRED. A regex line scan cannot reliably type-check a YAML mapping, so this check does
a single real structural parse rather than carrying a lower-fidelity fallback that would decide differently
from the parsed path. If PyYAML is unavailable the check exits 2 (setup error) with a clear message; it never
degrades silently. Same file in, same verdict out, independent of cwd (writer-re-derivable).

Usage: check-build-spec-structure.sh BUILD_SPEC_YAML
Exit: 0 pass; 1 structural assertion failure; 2 setup error (incl. PyYAML absent).
"""
import os
import re
import sys

REQUIRED_TOP = [
    "apiVersion", "kind", "meta", "inputs", "outputs", "integrations",
    "tool_permission_inventory", "sub_agent_surface", "decision_rules",
    "end_to_end_loop", "monitoring", "runbook", "scope",
]
# The required blocks that must each parse to a mapping (inputs/outputs/integrations are lists, handled apart).
REQUIRED_MAPPING_BLOCKS = [
    "meta", "tool_permission_inventory", "sub_agent_surface", "decision_rules",
    "end_to_end_loop", "monitoring", "runbook", "scope",
]
EVIDENCE_STATES = {"Observed", "Assumed", "Contradictory", "needs_more_information"}
# Allowed `type` values, mirroring the build-spec spine template's typed-I/O enums.
INPUT_TYPES = {"string", "number", "boolean", "object", "array", "document", "signal"}
OUTPUT_TYPES = {"string", "number", "boolean", "object", "array", "recommendation", "escalation"}
# The write stance (pinned write-grant design, section 3).
ACCESS_MODES = {"read", "write", "read-write"}
WRITE_MODES = {"write", "read-write"}
WRITE_KINDS = {"notice", "draft", "handoff", "own-file", "mechanical", "decision-bearing"}
EXCEPTION_REASONS = {"none", "own-working-surface", "reversible-reviewed"}
WRITE_GRANT_FIELDS = ("id", "system", "write_kind", "exception_reason", "access_state", "source")
MUST_NOT_READ_ONLY = "all writes"
MUST_NOT_GRANTED = "decision-bearing writes to a system of record without a write grant"
GRANT_ID_RE = re.compile(r"WG-[1-9][0-9]*")  # used with fullmatch: no trailing newline slips through
FORBIDDEN_SURFACE_DECOMP_KEYS = ["sub_agents", "subagents", "handoffs", "edges", "internal_dag", "nodes"]


def die(msg, code):
    sys.stderr.write(msg + "\n")
    sys.exit(code)


# --------------------------------------------------------------------------------------------------
# Validation over a parsed dict (the PyYAML path).
# --------------------------------------------------------------------------------------------------
def validate_parsed(doc):
    problems = []
    if not isinstance(doc, dict):
        return ["top level is not a mapping (expected the build-spec object)"]

    for k in REQUIRED_TOP:
        if k not in doc:
            problems.append("missing required top-level block: %r" % k)

    # 2a. the mapping blocks must each be a mapping (type, not just presence)
    for block in REQUIRED_MAPPING_BLOCKS:
        if block in doc and not isinstance(doc.get(block), dict):
            problems.append("%r must be a mapping (got %s)" % (block, type(doc.get(block)).__name__))

    # 2b. inputs / outputs / integrations must each be a non-empty list
    for block in ("inputs", "outputs", "integrations"):
        v = doc.get(block)
        if block in doc and (not isinstance(v, list) or len(v) == 0):
            problems.append("%r must be a non-empty list" % block)

    # 2c. per-item typing for inputs/outputs: required fields + the `type` VALUE against its enum
    for block, allowed in (("inputs", INPUT_TYPES), ("outputs", OUTPUT_TYPES)):
        v = doc.get(block)
        if isinstance(v, list):
            for idx, item in enumerate(v):
                if not isinstance(item, dict):
                    problems.append("%s[%d] is not a mapping" % (block, idx))
                    continue
                for f in ("name", "type", "source", "evidence_state"):
                    if f not in item:
                        problems.append("%s[%d] missing required field %r" % (block, idx, f))
                t = item.get("type")
                if "type" in item and (not isinstance(t, str) or t not in allowed):
                    problems.append("%s[%d].type = %r is not one of %s"
                                    % (block, idx, t, sorted(allowed)))

    # 2d. per-item typing for integrations: each a mapping with the load-bearing fields
    integ = doc.get("integrations")
    if isinstance(integ, list):
        for idx, item in enumerate(integ):
            if not isinstance(item, dict):
                problems.append("integrations[%d] is not a mapping" % idx)
                continue
            for f in ("name", "access_mode", "source", "evidence_state"):
                if f not in item:
                    problems.append("integrations[%d] missing required field %r" % (idx, f))

    # 3. the write stance: access modes, grant ids, the write_grants block, and must_not
    problems.extend(_check_write_stance(doc))

    # 4. sub-agent surface declared, not decomposed
    sas = doc.get("sub_agent_surface")
    if isinstance(sas, dict):
        primitive_form = doc.get("primitive_form")
        is_script = isinstance(primitive_form, dict) and primitive_form.get("value") == "script"
        if is_script:
            if sas.get("declared") is not False:
                problems.append("sub_agent_surface.declared must be false when primitive_form.value == "
                                "script (a single deterministic execution unit has no coordination surface)")
        elif sas.get("declared") is not True:
            problems.append("sub_agent_surface.declared must be true (the surface must be declared)")
        if not sas.get("decomposed_by"):
            problems.append("sub_agent_surface.decomposed_by must name the downstream internal-map stage")
        for bad in FORBIDDEN_SURFACE_DECOMP_KEYS:
            if bad in sas:
                problems.append("sub_agent_surface must NOT carry a decomposition (found %r) — that is the "
                                "internal-map stage's lane; declare the surface only" % bad)
    elif "sub_agent_surface" in doc:
        problems.append("sub_agent_surface must be a mapping")

    # 5. no invented decision rules past the cap
    dr = doc.get("decision_rules")
    if isinstance(dr, dict):
        if "cap" not in dr:
            problems.append("decision_rules.cap must cite the Agent-Builder cap (e.g. '3-8')")
        if "rules" not in dr:
            problems.append("decision_rules.rules must be EXPLICITLY present as an empty list ([]) — the "
                            "backbone declares the room it leaves; an absent key does not.")
        else:
            rules = dr.get("rules")
            if not isinstance(rules, list):
                problems.append("decision_rules.rules must be a list (an empty one at the backbone)")
            elif rules:
                problems.append("decision_rules.rules must be empty at the backbone (found %d) — the full "
                                "policy is the deepen stage's decision-policy file; no invented rules here"
                                % len(rules))
    elif "decision_rules" in doc:
        problems.append("decision_rules must be a mapping")

    # 6. honesty bar: evidence_state enum + needs_more_information carries needs:
    problems.extend(_walk_evidence(doc, path="$"))
    return problems


def _norm(x):
    return x.strip().lower() if isinstance(x, str) else None


def _check_write_stance(doc):
    out = []
    # write_grants block: optional, but when present a list of well-formed entries
    granted = set()
    wg_present = "write_grants" in doc and doc.get("write_grants") is not None
    wg = doc.get("write_grants")
    if wg_present:
        if not isinstance(wg, list):
            out.append("'write_grants' must be a list of grant entries (got %s)" % type(wg).__name__)
        else:
            for idx, g in enumerate(wg):
                if not isinstance(g, dict):
                    out.append("write_grants[%d] is not a mapping" % idx)
                    continue
                for f in WRITE_GRANT_FIELDS:
                    if f not in g or g.get(f) in (None, ""):
                        out.append("write_grants[%d] missing required field %r" % (idx, f))
                gid = g.get("id")
                if "id" in g and gid is not None:
                    if not isinstance(gid, str) or not GRANT_ID_RE.fullmatch(gid):
                        out.append("write_grants[%d].id = %r is not a grant id of the form WG-<n>" % (idx, gid))
                    elif gid in granted:
                        out.append("write_grants[%d].id = %r is listed more than once" % (idx, gid))
                    else:
                        granted.add(gid)
                wk = g.get("write_kind")
                if "write_kind" in g and wk is not None and wk not in WRITE_KINDS:
                    out.append("write_grants[%d].write_kind = %r is not one of %s" % (idx, wk, sorted(WRITE_KINDS)))
                er = g.get("exception_reason")
                if "exception_reason" in g and er is not None and er not in EXCEPTION_REASONS:
                    out.append("write_grants[%d].exception_reason = %r is not one of %s"
                               % (idx, er, sorted(EXCEPTION_REASONS)))
                acc = g.get("access_state")
                if "access_state" in g and acc is not None and acc != "Assumed":
                    out.append("write_grants[%d].access_state = %r must be exactly 'Assumed' (write access is "
                               "always Assumed, never confirmed by the design)" % (idx, acc))

    # integrations: access_mode enum; write modes carry grant ids listed in write_grants
    integ = doc.get("integrations")
    if isinstance(integ, list):
        for idx, item in enumerate(integ):
            if not isinstance(item, dict) or "access_mode" not in item:
                continue
            mode = item.get("access_mode")
            if mode not in ACCESS_MODES:
                out.append("integrations[%d].access_mode = %r is not one of %s" % (idx, mode, sorted(ACCESS_MODES)))
                continue
            ids = item.get("write_grants")
            if mode in WRITE_MODES and (not isinstance(ids, list) or not ids):
                out.append("integrations[%d] has access_mode %r but no grant ids: a write integration carries "
                           "write_grants: [WG-n, ...]" % (idx, mode))
                continue
            if ids is None:
                continue
            # any supplied grant list is validated, whatever the access mode (a read integration may not keep
            # a stale grant reference)
            if not isinstance(ids, list):
                out.append("integrations[%d].write_grants must be a list of grant ids (got %s)"
                           % (idx, type(ids).__name__))
                continue
            for gid in ids:
                if not isinstance(gid, str) or not GRANT_ID_RE.fullmatch(gid):
                    out.append("integrations[%d].write_grants has %r, not a grant id of the form WG-<n>" % (idx, gid))
                elif gid not in granted:
                    out.append("integrations[%d] names grant %s, which is not listed in write_grants" % (idx, gid))

    # must_not: exactly one of the two stance items; write_grants absent only with "all writes"
    tpi = doc.get("tool_permission_inventory")
    if isinstance(tpi, dict):
        mn = tpi.get("must_not")
        items = {_norm(x) for x in mn} if isinstance(mn, list) else set()
        has_ro = MUST_NOT_READ_ONLY in items
        has_granted = MUST_NOT_GRANTED in items
        if not has_ro and not has_granted:
            out.append("tool_permission_inventory.must_not must carry exactly one of %r (an agent that writes "
                       "nothing) or %r" % (MUST_NOT_READ_ONLY, MUST_NOT_GRANTED))
        elif has_ro and has_granted:
            out.append("tool_permission_inventory.must_not carries both %r and %r; it must carry exactly one"
                       % (MUST_NOT_READ_ONLY, MUST_NOT_GRANTED))
        if not wg_present and not has_ro:
            out.append("'write_grants' is absent but tool_permission_inventory.must_not does not carry %r: "
                       "only an agent that writes nothing may leave out write_grants" % MUST_NOT_READ_ONLY)
    elif "tool_permission_inventory" in doc:
        out.append("tool_permission_inventory must be a mapping with a must_not list")
    return out


def _walk_evidence(node, path):
    out = []
    if isinstance(node, dict):
        es = node.get("evidence_state")
        if es is not None and es not in EVIDENCE_STATES:
            out.append("%s.evidence_state = %r is not one of %s"
                       % (path, es, sorted(EVIDENCE_STATES)))
        if es == "needs_more_information" and not (node.get("needs") or node.get("follow_on")):
            out.append("%s is stamped needs_more_information but says nothing about what would resolve it "
                       "(a value open needs a `needs:` note; a policy/rule/scope residual needs a "
                       "`follow_on:`)" % path)
        for k, v in node.items():
            out.extend(_walk_evidence(v, "%s.%s" % (path, k)))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            out.extend(_walk_evidence(v, "%s[%d]" % (path, i)))
    return out


def main(argv):
    verbose = False
    rest = []
    for a in argv:
        if a in ("--verbose", "-v"):
            verbose = True
        else:
            rest.append(a)
    if len(rest) != 1:
        die("usage: check-build-spec-structure.sh [--verbose] BUILD_SPEC_YAML", 2)
    spec = rest[0]
    if not os.path.isfile(spec):
        die("Missing build-spec: %s" % spec, 2)
    text = open(spec, "r", encoding="utf-8", errors="replace").read()

    # PyYAML is a required dependency: a single real structural parse, never a lower-fidelity fallback that
    # would decide differently. Absent PyYAML is a setup error (exit 2), not a silent degrade.
    try:
        import yaml
    except ImportError:
        die("PyYAML is required for the build-spec structural parse but is not importable. Install it "
            "(pip install pyyaml) and re-run; this check does not fall back to a line scan.", 2)
    try:
        doc = yaml.safe_load(text)
    except Exception as e:
        die("build-spec is not parseable YAML: %s" % e, 1)
    problems = validate_parsed(doc)

    if verbose:
        sys.stderr.write("parse engine: pyyaml (required; single structural parse)\n")

    if problems:
        die("build-spec structural check FAILED for %s:\n  - %s" % (spec, "\n  - ".join(problems)), 1)
    print("PASS: build-spec spine is structurally valid (blocks present + typed; write stance holds: write "
          "integrations carry listed write grants, grants Assumed, must_not carries one stance item; "
          "sub-agent surface declared not decomposed; decision-rule room left; honesty stamps well-formed).")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
