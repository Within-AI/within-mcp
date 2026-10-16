#!/usr/bin/env python3
"""Canonical stage ordinals + plain names -- a stdlib-only projection, no PyYAML dependency.

The authored source of truth is assets/shipped-canonical-pipeline.v1.yaml. helpers/front_door.py
carries its own stdlib projection (CANONICAL_PIPELINE) for the same reason this module does: the
front door and the scripts/ derived-view renderers must not require PyYAML just to name a stage.
KEEP ALL THREE IN LOCKSTEP -- tests/test_stage_index_consistency.py asserts this module's list,
front_door.py's CANONICAL_PIPELINE, and the YAML asset all agree, so drift between them fails a test
rather than surfacing as a silent mis-numbering (the exact defect class a real factory run surfaced:
an orchestrator hand-tagged three events with three different ad hoc stage strings in one run before
this module existed).

Usage (as a library, from another scripts/ module):
    import _stageindex
    info = _stageindex.describe("agent-backbone")
    # -> {"ordinal": 6, "total": 12, "key": "agent-backbone",
    #     "plain_name": "Build the agent's build-spec spine", "known": True, "gate": False}
    _stageindex.describe("stage-0a")
    # -> {"ordinal": None, "total": 12, "key": "stage-0a", "plain_name": None, "known": False, "gate": False}
    _stageindex.describe("design-coherence")   # a gate tag: no ordinal, never terminal
    # -> {..., "known": False, "gate": True, "closes": "eval"}

Usage (as a CLI, for a quick lookup or to print the banner used at a stage transition):
    _stageindex.py --describe KEY
    _stageindex.py --banner KEY [--from PRIOR_KEY] [--next-action TEXT]
    _stageindex.py --list
"""
import sys

# Ordered (plain_name, key). Order is the pipeline's linear spine. Mirrors
# assets/shipped-canonical-pipeline.v1.yaml and helpers/front_door.py's CANONICAL_PIPELINE exactly.
# Plain names below match FACTORY_CHARTER.md's "Constraints - the fixed twelve stages" table
# verbatim (2026-09-18 charter alignment). Where the shipped wording previously carried extra
# descriptive text beyond the charter's canonical name, that text is preserved as a trailing
# comment rather than folded back into the name itself:
#   agent-backbone           formerly "Build the backbone - the agent's build-spec spine"
#   deepen-interface-design  formerly "...boundary (what crosses it, in and out)"
#   deepen-decision-rules    formerly "...decision rules (rules-as-code)"
#   pre-build-simulation  added 2026-09-25: coherence became a gate at the end of stage 10 (eval); stage 11 is the pre-build simulation.
CANONICAL_PIPELINE = [
    ("Freeze the Company Brain Snapshot", "snapshot"),
    ("Draw the current state of the work", "current-state"),
    ("Agree the agent's role and authority", "transformation-plan"),
    ("Draw the future state of the work", "future-state"),
    ("Hand the reviewed future-state graph to the design half", "internal-handoff"),
    ("Build the agent's build-spec spine", "agent-backbone"),
    ("Map the agent's inner team", "agent-build"),
    ("Design each part's boundary", "deepen-interface-design"),
    ("Design each part's decision rules", "deepen-decision-rules"),
    ("Build the test suite and check the whole design fits together", "eval"),
    ("Run the design's rules on sample data before build", "pre-build-simulation"),
    ("Build the agent and run it in a Sandbox", "sandbox-ship"),
]

# Keys that were canonical once and may still appear in older event logs and seeded starts. They are never
# legal for new events. describe() reports them as known=False with legacy_of set, so a banner and the front
# door can say plainly what became of them. A key that is still in CANONICAL_PIPELINE resolves as canonical
# first (describe() checks the pipeline before this table).
LEGACY_KEYS = {
    "coherence-loop": {"became": "design-coherence",
                       "plain": "the design-coherence gate at the end of stage 10"},
}
# Gate tags: event-log `stage` values that are not pipeline stages. A gate has no ordinal and never takes a
# terminal event; its pass is derived (scripts/_coherence_gate.py). It is legal in the `stage` field.
# A gate tag may carry its remediation-loop cap in `loop_policy` (read by scripts/_loop_policy.py): the
# design-coherence gate's cap of five is one diagnostic pass + up to four correction cycles.
GATE_TAGS = {"design-coherence": {"plain": "Check the whole design tells the same story", "closes": "eval",
                                  "loop_policy": {"max_cycles": 5, "on_exit": "escalate"}}}

TOTAL = len(CANONICAL_PIPELINE)
_BY_KEY = {key: (i + 1, plain) for i, (plain, key) in enumerate(CANONICAL_PIPELINE)}


def describe(key):
    """Return {ordinal, total, key, plain_name, known} for a stage key.

    A key not in the canonical list (a legacy tag such as "stage-0a", or a free-text stage an
    orchestrator invented) returns known=False with ordinal/plain_name as None, rather than raising
    or guessing a position -- an unrecognized stage tag is a fact to surface, never to paper over.
    """
    hit = _BY_KEY.get(key)
    if hit is not None:
        ordinal, plain = hit
        return {"ordinal": ordinal, "total": TOTAL, "key": key, "plain_name": plain, "known": True,
                "gate": False}
    if key in GATE_TAGS:
        return {"ordinal": None, "total": TOTAL, "key": key, "plain_name": GATE_TAGS[key]["plain"],
                "known": False, "gate": True, "closes": GATE_TAGS[key]["closes"]}
    info = {"ordinal": None, "total": TOTAL, "key": key, "plain_name": None, "known": False, "gate": False}
    if key in LEGACY_KEYS:
        info["legacy_of"] = LEGACY_KEYS[key]["became"]
    return info


def is_gate(key):
    """True for a gate tag (a `stage` value that is not a pipeline stage and never takes a terminal event)."""
    return key in GATE_TAGS and key not in _BY_KEY


def gate_loop_policy(key):
    """The loop policy a gate tag carries ({max_cycles, on_exit}), or None. A legacy key (`coherence-loop`)
    resolves through the gate it became."""
    if key in _BY_KEY:
        return None
    key = LEGACY_KEYS.get(key, {}).get("became", key)
    lp = GATE_TAGS.get(key, {}).get("loop_policy")
    return dict(lp) if lp else None


def is_legacy(key):
    """True for a key that was canonical once and is no longer in the pipeline."""
    return key in LEGACY_KEYS and key not in _BY_KEY


def slug(key):
    """The <NN>-<key> filename stem a per-stage artifact (e.g. an inspection HTML) is named with.

    Unknown keys still get a stem (NN falls back to "00") so a legacy-tagged stage does not crash
    artifact naming -- it is just visibly out of sequence, which is itself useful information.
    """
    info = describe(key)
    ordinal = info["ordinal"] if info["known"] else 0
    return "%02d-%s" % (ordinal, key)


def format_banner(key, from_key=None, next_action=None, extra_lines=None):
    """The boxed stage banner printed at a transition (see SKILL.md 'Stage banner' convention).

    Renders even for an unknown key (labelled UNREGISTERED STAGE TAG) so a drift between what the
    orchestrator writes to the log and the canonical list is visible in the banner itself, not just
    in a later audit.
    """
    info = describe(key)
    lines = []
    if info["known"]:
        head = "STAGE %d of %d -- %s" % (info["ordinal"], info["total"], info["plain_name"])
    elif info.get("gate"):
        closes = describe(info["closes"])
        head = "GATE (closes stage %s) -- %s" % (closes["ordinal"], info["plain_name"])
    elif info.get("legacy_of"):
        head = "LEGACY STAGE TAG -- %r is now %s" % (key, LEGACY_KEYS[key]["plain"])
    else:
        head = "UNREGISTERED STAGE TAG -- %r is not in the canonical %d-stage pipeline" % (key, TOTAL)
    lines.append(head)
    if from_key:
        prior = describe(from_key)
        if prior["known"]:
            lines.append("(was: stage %d, %s)" % (prior["ordinal"], from_key))
        else:
            lines.append("(was: %r, unregistered)" % from_key)
    if next_action:
        lines.append("next action: %s" % next_action)
    for extra in (extra_lines or []):
        lines.append(extra)
    width = max(len(l) for l in lines) + 4
    rule = "-" * width
    body = "\n".join("  %s" % l for l in lines)
    return "%s\n%s\n%s" % (rule, body, rule)


def main(argv):
    if not argv:
        sys.stderr.write("usage: _stageindex.py --describe KEY | --banner KEY [--from KEY] "
                          "[--next-action TEXT] | --list\n")
        return 2
    if argv[0] == "--list":
        for i, (plain, key) in enumerate(CANONICAL_PIPELINE, start=1):
            print("%2d/%d  %-24s %s" % (i, TOTAL, key, plain))
        return 0
    if argv[0] == "--describe" and len(argv) >= 2:
        info = describe(argv[1])
        for k in ("key", "known", "ordinal", "total", "plain_name"):
            print("%s: %s" % (k, info[k]))
        return 0
    if argv[0] == "--banner" and len(argv) >= 2:
        key = argv[1]
        from_key = next_action = None
        i = 2
        while i < len(argv):
            if argv[i] == "--from":
                i += 1; from_key = argv[i]
            elif argv[i] == "--next-action":
                i += 1; next_action = argv[i]
            i += 1
        print(format_banner(key, from_key=from_key, next_action=next_action))
        return 0
    sys.stderr.write("usage: _stageindex.py --describe KEY | --banner KEY [--from KEY] "
                      "[--next-action TEXT] | --list\n")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
