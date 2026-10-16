#!/usr/bin/env python3
# This file is Python despite its .sh name: run it directly or as `python3 <this file>`; `bash <this file>` cannot run it.
"""Interface-design SET-COMPLETENESS gate — the interface-design stage's set-level release gate.

Plain-language: "Is every part's boundary covered exactly once, and is no whole direction missing?"
(Former internal tag: C1 · check-interface-seating-directions. The plain-language question is the handle.)

THIS IS THE STAGE'S SET-COMPLETENESS GATE (the enforcement model, R1 §2.5 facet 3, bound to the stage's
clearance per §4.1's `stage.cleared`→required-check-set rule). It fires AT this stage — set-level
completeness is owned here, not deferred to the later cross-stage coherence sink (which reconciles
representations against each other, a different job). It is TOLD the built map's closed seam surface (the
`closed_interface_set` it receives, frozen from "Map the agent's inner team"); it never self-discovers the
seam set by globbing designs off disk. Every seam is enumerated upstream by the orchestrator from the DAG
coverage manifest; this gate proves the produced set covers exactly that enumerated surface. It attests
STRUCTURE and closed sets only — whether each design is honest is the review path's job, never this gate's.

It reads the built map's CLOSED SEAM SURFACE plus the set of interface designs (and any stamped opens) the
stage produced, and proves two SEPARATE coverage facts (per-file content is the SEPARATE per-design
structure check, check-interface-design-structure.sh):

  (a) PER-SEAM COVERAGE. The SEAM is the unit of coverage, NOT the direction. Every seam in the built
      map's closed_interface_set is represented in EXACTLY ONE of two mutually-exclusive ways:
        * EITHER by exactly one interface design bound to that seam id (its `design_ref` now FILLED),
        * OR by exactly one WELL-FORMED stamped-open residual bound to that seam id (a `kind: rule`
          residual with a canonical-four `evidence_state`, a non-empty `source`, a non-empty `follow_on`,
          and a `boundary_extension_note` whose `seam` names that seam; the seam's `design_ref` then
          legitimately stays null). An ill-formed/unstamped residual does NOT count as a representation.
      A seam represented by NEITHER is a silent absence -> fail. A seam represented by BOTH a filled
      design and a stamped open -> fail. Two designs for one seam -> fail. Two stamped opens for one
      seam -> fail. (One design XOR one stamped open, per seam.)

  (b) DIRECTION COVERAGE — a SEPARATE check over the six directions {inbound_control, read,
      outbound_handoff, draft, write, dispatch} (design §4). Two requirements, each counting a seam as
      represented by a design OR a stamped open:
        * `inbound_control` is REQUIRED — a hard gate: at least one inbound_control seam is represented;
        * at least ONE outbound seam is represented, from {outbound_handoff, draft, write, dispatch}.
      `read` is NOT required: an agent fed only through its inbound seam is valid. A `write` seam is the
      agent writing to a system under a write grant; a `dispatch` seam is the agent handing work to another
      agent. A person-routed system-of-record write has no write seam — the agent's side of it is an
      outbound_handoff to the person. This is NEVER "exactly one design per direction" — that would wrongly
      forbid a second same-direction seam. A built map may host multiple seams of one direction (e.g. two
      read seams); each is covered on its own under (a).

  (c) SEATING FROZEN. Every produced design's `seated_in` equals the built map's `seated_in` for that
      seam. Re-seating is refused (the stage FREEZES seating from the built map; it never re-seats).

  (d) CONSUMER-BINDING COVERAGE — additive to (a)/(b)/(c), a SEPARATE gate. For every seam represented by
      a FILLED design (per (a)) and for each of the two paths {happy, escalation}: the path is BOUND iff
      the design's `consumers.<path>.consuming_agent_section` or `consumers.<path>.consuming_substrate_job`
      is non-empty/non-null, or it is OPEN iff exactly one well-formed `consumer_open` residual
      (`kind: consumer`, canonical-four `evidence_state`, non-empty `source`/`follow_on`, a resolvable
      `seam`, `path` in {happy, escalation}) names that (seam, path). Exactly one of bound/open must hold
      — neither is a silent absence (fail, the FM-01 shape); both is ambiguous (fail); more than one
      consumer_open for the same (seam, path) is a duplicate representation (fail). Seams still covered by
      a `kind: rule` stamped-open (no design yet) carry no `consumers` block and are NOT evaluated by gate
      (d) at all — consumer-binding is deferred until that seam's design is produced and re-checked.
      ROLLOUT: a coverage object whose id (the COVERAGE_OBJECT path's basename) appears on the frozen,
      checked-in `consumer-binding-baseline.txt` is likewise not evaluated by gate (d) at all — a one-time
      grandfather-by-id exemption captured at the moment this gate landed (every object produced before the
      patch lacks a `consumers` block by construction and must keep passing, per the extension's own
      backward-compat bar), not a narrowing of what gate (d) checks once it applies. Each baseline line
      pairs the id with a sha256 fingerprint of that object's `designs[]` content AT BASELINE-CAPTURE TIME
      (see `_designs_fingerprint`); the exemption applies only while the CURRENT object's `designs[]`
      fingerprint still matches. The exemption is removed the moment a baseline-listed object's designs
      are next re-touched (its `designs[]` content changes) and re-graded — the fingerprint mismatch is
      what detects that, not membership in the file alone. Never invent a default `consuming_agent_section`
      / `consuming_substrate_job` to make a seam appear bound.

Input file shape (a single object; the built seam surface + the produced design set, off disk — NO live
Company Brain is read):
    closed_interface_set:            # the built map's seam surface (frozen), one entry per seam
      - {id: <seam id>, direction: inbound_control|read|outbound_handoff|draft|write|dispatch,
         seated_in: <owner id>}
    designs:                         # the produced interface designs (one document per seam)
      - {interface_id: <seam id>, direction: <dir>, seated_in: <owner id>, design_ref: <filled|null>, ...,
         consumers: {                # OPTIONAL — per-path named consumer(s), evaluated by gate (d)
           happy: {consuming_agent_section: <id|null>, consuming_substrate_job: <id|null>},
           escalation: {consuming_agent_section: <id|null>, consuming_substrate_job: <id|null>}}}
    stamped_opens:                   # OPTIONAL — residuals that SUBSTITUTE for a seam's design
      - {id: <residual id>, kind: rule,
         boundary_extension_note: {seam: <seam id>, direction: <dir>, note: <why carried open>}, follow_on: ...}
    consumer_opens:                  # OPTIONAL — residuals that SUBSTITUTE for a bound (seam, path)
      - {id: <residual id>, kind: consumer, seam: <seam id>, path: happy|escalation,
         evidence_state: <canonical four>, source: <non-empty>, follow_on: <non-empty>, note: <non-empty>}

Like the other factory checks this is a Mechanism-A re-verification against a lazy agent (a structural
proof that the load-bearing coverage invariants hold), NOT a semantic judge of whether a design is good.
`actor.engine == script`; the single writer re-runs it and re-derives the SHA at append time
(run-check.sh / _checks.py), so the recorded result is re-verified, never trusted from the payload.
Deterministic and writer-re-derivable: same file in, same verdict out, independent of cwd.

Parsing: PyYAML is REQUIRED. yaml.safe_load parses BOTH a `.yaml` coverage object and a `.graph.json`
(JSON is a subset of YAML), so one parse path serves both serializations. A regex line scan cannot
reliably read a nested structure, so this check does a single real structural parse rather than a
lower-fidelity fallback that would decide differently. If PyYAML is unavailable the check exits 2 (setup
error) with a clear message; it never degrades silently.

Usage: check-interface-seating-directions.sh [--verbose] [--help] COVERAGE_OBJECT
Rules it does NOT test (so they are written here, not discovered in a brief): each design names the system it
reads or writes with a top-level `system_id` (the same id wherever that system appears; for a seam that faces
only people, the people's role), never omitted and never the interface id reused. This gate does not read
`system_id`; the stage 11 driver and the stage briefs do. `--help` prints this text.
Exit: 0 pass; 1 coverage assertion failure; 2 setup error (incl. PyYAML absent).
"""
import hashlib
import json
import os
import sys

DIRECTIONS = ("inbound_control", "read", "outbound_handoff", "draft", "write", "dispatch")
# (b) direction coverage: inbound_control is a hard gate, plus at least one outbound direction. read is optional.
REQUIRED_DIRECTION = "inbound_control"
OUTBOUND_DIRECTIONS = ("outbound_handoff", "draft", "write", "dispatch")
CANONICAL_FOUR = {"Observed", "Assumed", "Contradictory", "needs_more_information"}
PATHS = ("happy", "escalation")
BASELINE_FILENAME = "consumer-binding-baseline.txt"


def die(msg, code):
    sys.stderr.write(msg + "\n")
    sys.exit(code)


def _wellformed_open(o):
    """A stamped-open residual REPRESENTS its seam only if it is a well-formed stamped open: `kind: rule`,
    a canonical-four `evidence_state`, a non-empty `source`, a non-empty `follow_on`, and a
    `boundary_extension_note.seam` binding. An ill-formed one does NOT count — so the seam it names reads
    as an uncovered silent absence (fail) rather than being silently accepted as covered."""
    if not isinstance(o, dict):
        return False
    if o.get("kind") != "rule":
        return False
    if o.get("evidence_state") not in CANONICAL_FOUR:
        return False
    if not _nonempty(o.get("source")):
        return False
    if not _nonempty(o.get("follow_on")):
        return False
    ben = o.get("boundary_extension_note") if isinstance(o.get("boundary_extension_note"), dict) else {}
    if not _nonempty(ben.get("seam")):
        return False
    return True


def _wellformed_consumer_open(o, seams):
    """A `consumer_open` residual REPRESENTS an unbound (seam, path) pair only if it is a well-formed
    consumer open: `kind: consumer`, a canonical-four `evidence_state`, a non-empty `source`, a non-empty
    `follow_on`, a `seam` that resolves to a seam in the closed set, and a `path` in {"happy",
    "escalation"}. An ill-formed one does NOT count — so the (seam, path) it names reads as a silent
    absence (fail) rather than being silently accepted as covered. Mirrors `_wellformed_open` for the
    seam-level stamped-open family; `kind: consumer` distinguishes this residual family from `kind: rule`
    design-opens."""
    if not isinstance(o, dict):
        return False
    if o.get("kind") != "consumer":
        return False
    if o.get("evidence_state") not in CANONICAL_FOUR:
        return False
    if not _nonempty(o.get("source")):
        return False
    if not _nonempty(o.get("follow_on")):
        return False
    sid = o.get("seam")
    if not _nonempty(sid) or sid not in seams:
        return False
    if o.get("path") not in PATHS:
        return False
    return True


def _bound_path(consumers, path):
    """A path is BOUND iff at least one of its two named-consumer fields (`consuming_agent_section`,
    `consuming_substrate_job`) is non-empty/non-null. Both may be filled (not forced to choose); neither
    filled is NOT bound. Never invent a default value here — an absent block is simply not bound, never
    silently treated as covered."""
    if not isinstance(consumers, dict):
        return False
    block = consumers.get(path)
    if not isinstance(block, dict):
        return False
    return _nonempty(block.get("consuming_agent_section")) or _nonempty(block.get("consuming_substrate_job"))


def _designs_fingerprint(designs):
    """Deterministic content fingerprint of a coverage object's `designs[]`, used to detect a
    baseline-listed object's retouch (plan §7: 'the exemption is removed the moment a baseline-listed
    object's designs[] content changes and it is re-graded'). A canonical (sort_keys) JSON dump of the
    raw `designs` value, hashed with sha256 — same list in, same fingerprint out, independent of key
    ordering or cwd. Not a semantic diff; any change to designs[]'s content (a field edited, added,
    removed) changes the fingerprint, which is exactly the retouch signal this needs."""
    if not isinstance(designs, list):
        designs = []
    canon = json.dumps(designs, sort_keys=True, default=str)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def _load_baseline(baseline_path):
    """Parses `consumer-binding-baseline.txt` into {basename: baseline_designs_hash}. Each non-comment,
    non-blank line is `<basename> <sha256 fingerprint of designs[] at baseline-capture time>` — two
    whitespace-separated tokens. A malformed line (not exactly two tokens) is skipped rather than
    crashing the check; it simply cannot exempt anything."""
    entries = {}
    with open(baseline_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split()
            if len(parts) != 2:
                continue
            entries[parts[0]] = parts[1]
    return entries


def _on_baseline(object_id, designs):
    """True iff `object_id` (matched by basename, so the check stays cwd-independent like the rest of
    it) appears on the frozen, checked-in `consumer-binding-baseline.txt` captured at the moment gate
    (d) landed (plan §7) AND the object's CURRENT `designs[]` content still fingerprint-matches what was
    captured at baseline time. A one-time grandfather-by-id rollout exemption from gate (d) — not a
    narrowing of what gate (d) checks once it applies, and not a recurring/regenerated list. Per plan
    §7, the exemption is removed the moment a baseline-listed object's `designs[]` content changes and
    it is re-graded: a basename match with a mismatched fingerprint means the object was re-touched
    since baseline capture, so this returns False and gate (d) applies to it in full, exactly like a
    newly created object — it does NOT come off the file; the file stays a frozen record of what was
    captured, and the fingerprint comparison (not membership alone) is what implements the retouch."""
    baseline_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), BASELINE_FILENAME)
    if not os.path.isfile(baseline_path):
        return False
    basename = os.path.basename(object_id)
    entries = _load_baseline(baseline_path)
    baseline_hash = entries.get(basename)
    if baseline_hash is None:
        return False
    return baseline_hash == _designs_fingerprint(designs)


def _filled(v):
    """A design_ref counts as filled only if it is a non-empty, non-null pointer."""
    if v is None:
        return False
    if isinstance(v, str) and v.strip().lower() in ("", "null", "none", "~"):
        return False
    return True


def _nonempty(v):
    return isinstance(v, str) and v.strip() != ""


def validate(doc, object_id):
    problems = []
    if not isinstance(doc, dict):
        return ["top level is not a mapping (expected the seam-surface + design-set coverage object)"]

    cis = doc.get("closed_interface_set")
    designs = doc.get("designs", [])
    opens = doc.get("stamped_opens", [])

    # ---- light structural sanity so the coverage rules compute over a well-formed object ----
    if not isinstance(cis, list) or not cis:
        return ["`closed_interface_set` must be a present, non-empty list (the built map's seam surface)"]
    if not isinstance(designs, list):
        problems.append("`designs` must be a list")
        designs = []
    if not isinstance(opens, list):
        problems.append("`stamped_opens` must be a list (or absent)")
        opens = []

    seams = {}
    for s in cis:
        if not isinstance(s, dict) or not _nonempty(s.get("id")):
            problems.append("a closed_interface_set entry lacks an `id`: %r" % (s,))
            continue
        sid = s["id"]
        if sid in seams:
            problems.append("duplicate seam id in closed_interface_set: %r" % sid)
        if s.get("direction") not in DIRECTIONS:
            problems.append("seam %r has an unknown/absent direction %r (allowed: %s)"
                            % (sid, s.get("direction"), list(DIRECTIONS)))
        seams[sid] = s
    if problems:
        return problems  # cannot compute coverage over a malformed seam surface

    # ---- index the produced representations by the seam they bind to ----
    designs_by_seam = {}
    for d in designs:
        if not isinstance(d, dict):
            problems.append("a `designs` entry is not a mapping: %r" % (d,))
            continue
        sid = d.get("interface_id")
        if not _nonempty(sid):
            problems.append("a design lacks an `interface_id` binding it to a seam: %r"
                            % (d.get("name") or d))
            continue
        if sid not in seams:
            problems.append("design %r binds to seam id %r that is not in the built closed_interface_set "
                            "(a dangling design binding)" % (d.get("name") or sid, sid))
            continue
        # a design REPRESENTS its seam only when its design_ref is FILLED (a null ref does not cover it)
        if _filled(d.get("design_ref")):
            designs_by_seam.setdefault(sid, []).append(d)

    opens_by_seam = {}
    for o in opens:
        if not isinstance(o, dict):
            problems.append("a `stamped_opens` entry is not a mapping: %r" % (o,))
            continue
        ben = o.get("boundary_extension_note") if isinstance(o.get("boundary_extension_note"), dict) else {}
        sid = ben.get("seam")
        if not _nonempty(sid):
            problems.append("a stamped-open residual %r carries no boundary_extension_note.seam binding it "
                            "to a seam" % (o.get("id") or o))
            continue
        if sid not in seams:
            problems.append("stamped-open residual %r names seam %r not in the built closed_interface_set"
                            % (o.get("id") or sid, sid))
            continue
        # a stamped open counts as a seam's representation ONLY if it is a WELL-FORMED stamped open; an
        # ill-formed one (missing kind/stamp/follow_on) does not count, so its seam reads as an uncovered
        # silent absence under per-seam coverage (a) below.
        if _wellformed_open(o):
            opens_by_seam.setdefault(sid, []).append(o)

    # ---- (a) per-seam coverage: exactly one representation (design XOR stamped open) per seam ----
    for sid, s in seams.items():
        n_designs = len(designs_by_seam.get(sid, []))
        n_opens = len(opens_by_seam.get(sid, []))
        total = n_designs + n_opens
        if total == 0:
            problems.append("PER-SEAM COVERAGE: seam %r (%s) is represented by NEITHER a filled design nor "
                            "a WELL-FORMED stamped-open residual — a silent absence (a `design_ref: null` "
                            "with no well-formed stamped open bound to it; an ill-formed open does not "
                            "count)" % (sid, s.get("direction")))
        elif total > 1:
            problems.append("PER-SEAM COVERAGE: seam %r (%s) is represented %d times (designs=%d, "
                            "stamped_opens=%d) — exactly one representation is required (one design XOR one "
                            "stamped open; two designs, two opens, or a design+open all fail)"
                            % (sid, s.get("direction"), total, n_designs, n_opens))

    # ---- (b) direction coverage — SEPARATE: inbound_control required, plus >= 1 outbound direction ----
    def _represented(direction):
        return any(((sid in designs_by_seam) or (sid in opens_by_seam))
                   for sid, s in seams.items() if s.get("direction") == direction)
    if not _represented(REQUIRED_DIRECTION):
        problems.append("DIRECTION COVERAGE: direction %r has ZERO seams represented (no design and no "
                        "stamped open of that direction) — inbound_control is required on every set. (This is "
                        "NOT 'one design per direction'; multiple same-direction seams are each covered "
                        "under (a).)" % REQUIRED_DIRECTION)
    if not any(_represented(d) for d in OUTBOUND_DIRECTIONS):
        problems.append("DIRECTION COVERAGE: no outbound seam is represented — at least one of %s must have "
                        "a design or a stamped open (read is not required, but the agent must hand, draft, "
                        "write or dispatch something)" % list(OUTBOUND_DIRECTIONS))

    # ---- (c) seating frozen: every produced design's seated_in matches the built map ----
    for sid, dlist in designs_by_seam.items():
        frozen = seams[sid].get("seated_in")
        for d in dlist:
            if d.get("seated_in") != frozen:
                problems.append("SEATING FROZEN: design for seam %r is seated_in %r but the built map "
                                "froze %r — re-seating is refused (the stage freezes seating, never "
                                "re-seats it)" % (sid, d.get("seated_in"), frozen))

    # ---- (d) CONSUMER-BINDING COVERAGE — SEPARATE gate, additive to (a)/(b)/(c) ----
    # Operates only over designs_by_seam keys (seams with a filled design); a stamped-open seam has no
    # `consumers` block to evaluate and that absence is not itself a failure (see docstring). Skipped
    # entirely for a coverage object on the frozen baseline-exemption list (rollout, plan §7).
    if not _on_baseline(object_id, designs):
        consumer_opens = doc.get("consumer_opens", [])
        if not isinstance(consumer_opens, list):
            problems.append("`consumer_opens` must be a list (or absent)")
            consumer_opens = []

        opens_by_seam_path = {}
        for co in consumer_opens:
            if not isinstance(co, dict):
                problems.append("a `consumer_opens` entry is not a mapping: %r" % (co,))
                continue
            # a consumer_open counts as a (seam, path)'s representation ONLY if well-formed; an ill-formed
            # one does not count, so the (seam, path) it names reads as a silent absence below.
            if _wellformed_consumer_open(co, seams):
                key = (co.get("seam"), co.get("path"))
                opens_by_seam_path.setdefault(key, []).append(co)

        for sid, dlist in designs_by_seam.items():
            for d in dlist:
                consumers = d.get("consumers") if isinstance(d.get("consumers"), dict) else {}
                for path in PATHS:
                    bound = _bound_path(consumers, path)
                    n_open = len(opens_by_seam_path.get((sid, path), []))
                    if n_open > 1:
                        problems.append("CONSUMER-BINDING COVERAGE: seam %r has %d consumer_open "
                                        "residuals for the %r path — exactly one representation is "
                                        "required (bound XOR one consumer_open)" % (sid, n_open, path))
                    elif bound and n_open == 1:
                        problems.append("CONSUMER-BINDING COVERAGE: seam %r is both bound and carries a "
                                        "consumer_open on the %r path — ambiguous; a bound consumer needs "
                                        "no open, an open path needs no invented consumer" % (sid, path))
                    elif not bound and n_open == 0:
                        problems.append("CONSUMER-BINDING COVERAGE: seam %r has no bound consumer and no "
                                        "consumer_open on the %r path — a silent absence" % (sid, path))
                    # else: exactly one of bound / well-formed consumer_open is true -> represented.

    return problems


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
        die("usage: check-interface-seating-directions.sh [--verbose] COVERAGE_OBJECT", 2)
    path = rest[0]
    if not os.path.isfile(path):
        die("Missing coverage object: %s" % path, 2)
    text = open(path, "r", encoding="utf-8", errors="replace").read()

    try:
        import yaml
    except ImportError:
        die("PyYAML is required for the seam-surface structural parse but is not importable. Install it "
            "(pip install pyyaml) and re-run; this check does not fall back to a line scan.", 2)
    try:
        doc = yaml.safe_load(text)
    except Exception as e:
        die("coverage object is not parseable (JSON or YAML): %s" % e, 1)

    problems = validate(doc, path)
    if verbose:
        sys.stderr.write("parse engine: pyyaml safe_load (serves both a .yaml coverage object and a .graph.json)\n")
    if problems:
        die("interface-seating coverage check FAILED for %s:\n  - %s" % (path, "\n  - ".join(problems)), 1)
    if _on_baseline(path, doc.get("designs", []) if isinstance(doc, dict) else []):
        gate_d_note = ("gate (d) CONSUMER-BINDING COVERAGE skipped under the baseline exemption — this "
                       "object's id is on the frozen consumer-binding-baseline.txt rollout list")
    else:
        gate_d_note = ("every designed seam is consumer-bound on both the happy and escalation path, or "
                       "carries a well-formed consumer_open")
    print("PASS: every seam represented exactly once (design XOR stamped open); inbound_control and >= 1 "
          "outbound direction represented; seating frozen from the built map; %s." % gate_d_note)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
