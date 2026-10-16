#!/usr/bin/env python3
"""Typed event-schema + legal-transition validation (F4 — Phase A substrate).

`_eventlog.load_and_validate` does STRUCTURAL validation only (whole-file parse;
event_ids unique, gapless, strictly increasing). This module adds the TYPED layer
that F4 requires: an immutable run header, well-formed event
envelopes, an allowed event-type set, per-type required payload keys and enums, and
legal per-stage state transitions. It is grounded in the canonical taxonomy at
`reference/event-log-schema.md` (v0.2) — not invented here.

Why it exists: without it, the shared validator trusted any parseable, id-sequenced
JSON, so invented or stale serial JSON could influence routing — including a
free-authored `stage.cleared`. Every authorization-changing event
(`review.returned`, `review.reconciled`, `stage.cleared`, `escalated`) must pass this.

The typed layer is enforced in STRICT mode (the fresh-run spec). Callers that route
or project a log (the writer, the loop-controller, the reconciler) pass typed=True;
the legacy structural-only mode is retained for read-only inspection of pre-typed
logs but is not a clearance path.

HONEST LIMIT (mechanism A): this defends against a lazy agent, not a determined one on
the same filesystem who could edit this module or write the log directly. A real trust
boundary needs a harness-owned writer in a separate trust domain (mechanism D), still
absent. The factory stays human-supervised.
"""
import re
import math

from _eventlog import EventLogError, evt_num
import _stageindex

# The writer stamps ts as UTC "YYYY-MM-DDTHH:MM:SSZ"; enforce that shape so a
# hand-authored event cannot carry a junk timestamp that later misleads ordering.
TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

# The anchored-rubric method treats scores 2-3 as a fail, so the clearance pass bar may
# never be set below 4. Bound it (and max_cycles) at run.opened so a caller cannot lower
# the gate by policy (a writer-path clearance weakening the review caught).
PASS_BAR_MIN, PASS_BAR_MAX = 4, 5

# Envelope enums (superset of the schema-doc roles + the roles used by the bundled
# workers brief-drafter/debugger; engines per the schema doc).
ACTOR_ROLES = {
    "orchestrator", "executor", "reviewer", "human", "watcher",
    "brief-drafter", "debugger",
}
ACTOR_ENGINES = {"claude", "codex", "human", "script"}

DISPOSITIONS = {"clear", "remediate", "escalate"}

# Human-gate enums (a retrofit of the existing workflow-diagram half onto the
# six-gate human-input model). Previously the writer key-checked human.decision but
# never enum-checked gate/decision (any string passed). These freeze the allowed
# values so an invented gate/decision cannot slip through as a valid human gate.
#   Six pre-existing values (additive, no removal) + the two new front-gate additions:
#     intake         -> "Confirm what we're building"  (G-A)
#     brief-approval -> "Approve the stage plan"        (G-B)
# The six existing values keep their exact spelling; adding a constraint where none
# existed cannot reject any log that validated before this retrofit.
HUMAN_GATE_VALUES = {
    "boundary", "current-state-acceptance", "future-state-remit",
    "acceptance", "feasibility", "mid-execution-steer",
    "intake", "brief-approval",
    # Front-door gates (R8 ATD front door; amended keystone §3.6 / Part 4):
    "entry-confirm", "advisory-approval",
    # Stage 11, the pre-build simulation (added 2026-09-25): the business owner's review of the samples and
    # outcomes on the simulation page, and a person confirming the provisional test values used for opens.
    "prebuild-simulation-review", "provisional-values",
}
# The decision enum was already canonical in the schema doc; this retrofit only ENFORCES it.
HUMAN_DECISION_VALUES = {"accepted", "rejected", "changes-requested"}
# The visual-review gate ("Check the diagram reads true", G-D) accepts only these two
# dispositions — a render is either accepted or sent back for changes.
VISUAL_REVIEW_DECISION_VALUES = {"accepted", "changes-requested"}

ON_EXIT = {"escalate", "block", "halt"}
# Once one of these is recorded for a stage, the stage is terminal — no further
# same-stage events may be appended (a resubmission after clear must not slip in and
# leave the stage silently advanced).
TERMINAL_TYPES = {"stage.cleared", "stage.blocked", "escalated", "stage.accepted_with_residual"}

# stage.reopened (added 2026-09-25): lifts a stage's terminality so a routed finding can be corrected in the
# owning artifact. `trigger` says why; `finding_ref` (a findings-register or defect id) is required unless a
# person asked for the reopen; `authorized_by` is an accepted human.decision event id, or the literal
# "fast-mode-policy" on a fast-mode factory run.
REOPEN_TRIGGERS = {"coherence-finding", "pre-build-simulation-defect", "person-request"}
FAST_MODE_POLICY = "fast-mode-policy"

# The allowed event-type set + the payload keys each type must carry. Kept aligned to
# what the scripts actually read/write so a legitimate writer-emitted log is never
# rejected (verified against the F7 fixture regen). Extra keys are permitted; these
# are the minimum a well-formed event of each type must have.
REQUIRED_PAYLOAD = {
    "run.opened":               [],          # loop_policy checked separately (F5)
    "snapshot.frozen":          ["source", "path", "sha256"],
    "feasibility.gated":        ["surfaces", "verdict"],
    "scope.shelved":            ["what", "reason"],
    "failure_mode.logged":      ["family", "artifact_vs_machinery"],
    "failure_mode.opened":      [],          # id resolved in append-event.enforce_payload
    "failure_mode.verified":    [],
    "failure_mode.closed":      [],
    "packet.authored":          ["packet_path", "sha256"],
    "brief.authored":           ["brief_path", "sha256", "criteria"],
    "executor.started":         ["packet_path"],
    "executor.aborted":         ["reason", "cycle"],
    "executor.submitted":       ["artifact_path", "sha256", "cycle"],
    "review.started":           ["brief_path", "artifact_path", "artifact_sha256"],
    "review.returned":          ["return_path", "artifact_path", "artifact_sha256",
                                 "disposition", "scores", "cycle", "submission_event_id"],
    "review.aborted":           ["reason"],
    "review.reconciled":        ["reviews", "resolved_disposition"],
    "remediation.authored":     ["cycle", "scoped_to"],
    "artifact.changed_verified": ["cycle", "path", "old_sha256", "new_sha256"],
    # stage.reopened lifts terminality: the stage is terminal iff its latest terminal event is later than
    # its latest stage.reopened (see validate_transition). Canonical stage keys only.
    "stage.reopened":           ["reason", "trigger", "authorized_by"],
    "stage.cleared":            ["cycle", "disposition"],
    "stage.blocked":            ["cycle", "disposition"],
    # stage.accepted_with_residual (G-F "accept-with-open"): a terminal disposition
    # that lands named residuals rather than a full clear. Rides the SAME governing
    # review-clear + required-check floor as stage.cleared (enforced in append-event.sh),
    # distinguished by payload.disposition == "accept-with-open" + a non-empty
    # residuals[], each {id, kind in {value, rule, scope, access}, hydrate_at}.
    "stage.accepted_with_residual": ["cycle", "residuals", "gate_ref"],
    "escalated":                ["stage", "reason", "cycles_used", "max_cycles", "to"],
    "schema.revised":           ["from_version", "to_version"],
    "human.decision":           ["gate", "decision"],
    "check.passed":             ["check", "artifact_path", "target_sha256", "argv"],
    "check.failed":             ["check", "artifact_path", "target_sha256", "argv", "exit_code"],
    "human.review_requested":   ["artifact_path", "artifact_sha256", "submission_event_id"],
    # Typed home for the render gate (G-D). The render was prose-only
    # before; this binds the human accuracy judgment to the exact rendered artifact
    # (artifact_sha256) plus the per-item checklist the BAKED
    # reference/visual-standards-checklist.md reports into. Adding a key here also
    # registers it into ALLOWED_TYPES (derived just below).
    "human.visual_review":      ["stage", "artifact_path", "artifact_sha256",
                                 "checklist", "decision"],
    # --- Front-door layer (R8 ATD front-door integration). Additive; NON-GATING: these
    # authorize nothing and block nothing. The front door instantiates the shipped canonical
    # pipeline and records where a run starts; the sufficiency + heal records ride the one log. ---
    # graph.authored: instantiate the shipped canonical pipeline, seeded at the confirmed entry
    # stage. `stages` carries the per-stage {name, active, seeded_root, deps} encoding the resume
    # read consumes (the R8 encoding of "seeded entry stage as root / upstream deps satisfied").
    "graph.authored":           ["graph", "pipeline_asset", "seeded_entry_stage", "stages"],
    # handoff.internal: the internal-handoff join marker -- the reviewed future-state graph handed
    # to the design half (fresh path), OR a client-brought artifact satisfying the seeded stage's
    # consumed input (enter-with-inputs; provenance == "client-brought").
    "handoff.internal":         ["to_stage", "provenance"],
    # sufficiency.assessed (R11): script-computed coverage of a client-brought artifact against the
    # shipped required-input manifest. NON-GATING; actor.engine == script (writer-re-derived).
    "sufficiency.assessed":     ["entry_stage", "object_kind", "manifest_version",
                                 "artifact_sha256", "results"],
    # The consented advisory auto-heal records (R12). Consent is the preceding human.decision
    # (gate == advisory-approval); these record what was healed and the new artifact version.
    "heal.applied":             ["entry_stage", "source_version", "result_version",
                                 "approved_gap_ids", "proceed"],
    "artifact.version":         ["artifact_path", "version", "supersedes"],
}
ALLOWED_TYPES = set(REQUIRED_PAYLOAD)


def _payload(event):
    p = event.get("payload")
    return p if isinstance(p, dict) else {}


def _validate_loop_policy(lp, where):
    """Validate a loop_policy wherever it appears (run header OR a stage-scoped
    override), so a stage event cannot weaken the exit policy untyped."""
    if not isinstance(lp, dict):
        raise EventLogError("Typed: %s loop_policy must be an object {max_cycles, on_exit}." % where)
    mc = lp.get("max_cycles")
    if not isinstance(mc, int) or isinstance(mc, bool) or mc < 1:
        raise EventLogError("Typed: %s loop_policy.max_cycles must be an integer >= 1." % where)
    if lp.get("on_exit", "escalate") not in ON_EXIT:
        raise EventLogError("Typed: %s loop_policy.on_exit %r not in %s."
                            % (where, lp.get("on_exit"), ", ".join(sorted(ON_EXIT))))
    if "pass_bar" in lp:
        pb = lp["pass_bar"]
        if not isinstance(pb, int) or isinstance(pb, bool) or not (PASS_BAR_MIN <= pb <= PASS_BAR_MAX):
            raise EventLogError(
                "Typed: %s loop_policy.pass_bar must be an integer in [%d, %d]; got %r."
                % (where, PASS_BAR_MIN, PASS_BAR_MAX, pb))


def validate_envelope(event, idx):
    """Envelope + type + payload-key + enum checks for a single event."""
    where = "event %s" % (event.get("event_id") or ("position %d" % (idx + 1)))
    t = event.get("type")
    if t not in ALLOWED_TYPES:
        raise EventLogError(
            "Typed: %s has unknown type %r (allowed: %s)."
            % (where, t, ", ".join(sorted(ALLOWED_TYPES))))
    for k in ("event_id", "ts", "run_id", "stage", "type"):
        if not event.get(k):
            raise EventLogError("Typed: %s missing required envelope field %r." % (where, k))
    if not TS_RE.match(str(event.get("ts"))):
        raise EventLogError("Typed: %s ts %r is not a UTC 'YYYY-MM-DDTHH:MM:SSZ' timestamp."
                            % (where, event.get("ts")))
    actor = event.get("actor")
    if not isinstance(actor, dict):
        raise EventLogError("Typed: %s actor must be an object {role, engine}." % where)
    if actor.get("role") not in ACTOR_ROLES:
        raise EventLogError("Typed: %s actor.role %r not in %s."
                            % (where, actor.get("role"), ", ".join(sorted(ACTOR_ROLES))))
    if actor.get("engine") not in ACTOR_ENGINES:
        raise EventLogError("Typed: %s actor.engine %r not in %s."
                            % (where, actor.get("engine"), ", ".join(sorted(ACTOR_ENGINES))))
    p = _payload(event)
    if t == "run.opened":
        if "mode" in p and (not isinstance(p["mode"], str) or p["mode"].lower() not in {"demo", "project", "factory"}):
            raise EventLogError("Typed: mode must be demo, project or factory")
        if "human_review_required" in p and type(p["human_review_required"]) is not bool:
            raise EventLogError("Typed: human_review_required must be boolean")
        if "fast_mode" in p and type(p["fast_mode"]) is not bool:
            raise EventLogError("Typed: fast_mode must be boolean")
    for key in ("duration_ms", "reverification_duration_ms", "writer_validation_duration_ms"):
        if key in p and (type(p[key]) not in (int, float) or not math.isfinite(p[key]) or p[key] < 0):
            raise EventLogError("Typed: invalid nonnegative timing field " + key)
    if "started_at" in p or "finished_at" in p:
        from datetime import datetime
        try:
            start = datetime.fromisoformat(p["started_at"].replace("Z", "+00:00"))
            finish = datetime.fromisoformat(p["finished_at"].replace("Z", "+00:00"))
            if start.tzinfo is None or finish.tzinfo is None or finish < start:
                raise ValueError("invalid timing interval")
        except (KeyError, TypeError, ValueError) as exc:
            raise EventLogError("Typed: invalid started_at/finished_at: %s" % exc)
    for k in REQUIRED_PAYLOAD[t]:
        if k not in p:
            raise EventLogError("Typed: %s (%s) missing required payload.%s." % (where, t, k))

    # Type-specific enums / shapes.
    if t == "run.opened":
        if "loop_policy" not in p:
            raise EventLogError("Typed: %s run.opened must carry payload.loop_policy {max_cycles, on_exit}." % where)
        _validate_loop_policy(p.get("loop_policy"), where + " run.opened")
    # A stage-scoped loop_policy on ANY other event must be typed the same way, so it
    # cannot weaken the exit policy without validation.
    elif "loop_policy" in p:
        _validate_loop_policy(p.get("loop_policy"), where + " stage-scoped")
    if t == "review.returned" and p.get("disposition") not in DISPOSITIONS:
        raise EventLogError("Typed: %s review.returned.disposition %r not in %s."
                            % (where, p.get("disposition"), ", ".join(sorted(DISPOSITIONS))))
    if t == "review.reconciled":
        if p.get("resolved_disposition") not in DISPOSITIONS:
            raise EventLogError("Typed: %s review.reconciled.resolved_disposition %r not in %s."
                                % (where, p.get("resolved_disposition"), ", ".join(sorted(DISPOSITIONS))))
        if not isinstance(p.get("reviews"), list) or not p["reviews"]:
            raise EventLogError("Typed: %s review.reconciled.reviews must be a non-empty list of event_ids." % where)
    if t == "review.returned" and not isinstance(p.get("scores"), list):
        raise EventLogError("Typed: %s review.returned.scores must be a list." % where)
    if t == "check.passed" and actor.get("engine") != "script":
        raise EventLogError("Typed: %s check.passed must have actor.engine == 'script'." % where)
    if t == "sufficiency.assessed" and actor.get("engine") != "script":
        raise EventLogError("Typed: %s sufficiency.assessed must have actor.engine == 'script' "
                            "(script-computed coverage; non-gating)." % where)
    # Enum-value check on the human decision gate (previously key-presence
    # only). Enforces the full eight-value gate enum and the three-value decision enum.
    if t == "human.decision":
        if p.get("gate") not in HUMAN_GATE_VALUES:
            raise EventLogError("Typed: %s human.decision.gate %r not in %s."
                                % (where, p.get("gate"), ", ".join(sorted(HUMAN_GATE_VALUES))))
        if p.get("decision") not in HUMAN_DECISION_VALUES:
            raise EventLogError("Typed: %s human.decision.decision %r not in %s."
                                % (where, p.get("decision"), ", ".join(sorted(HUMAN_DECISION_VALUES))))
    # Shape checks for the typed render gate (G-D). Its decision enum is
    # the two-value {accepted, changes-requested}; its checklist is a list of
    # {item, pass(bool)} objects (the visual-standards checklist items reported into).
    if t == "human.visual_review":
        if p.get("decision") not in VISUAL_REVIEW_DECISION_VALUES:
            raise EventLogError("Typed: %s human.visual_review.decision %r not in %s."
                                % (where, p.get("decision"), ", ".join(sorted(VISUAL_REVIEW_DECISION_VALUES))))
        cl = p.get("checklist")
        if not isinstance(cl, list) or not cl:
            raise EventLogError("Typed: %s human.visual_review.checklist must be a non-empty list." % where)
        for row in cl:
            if not isinstance(row, dict) or "item" not in row or not isinstance(row.get("pass"), bool):
                raise EventLogError(
                    "Typed: %s human.visual_review.checklist items must be objects "
                    "carrying 'item' and a boolean 'pass'; got %r." % (where, row))

    if t == "stage.reopened":
        if p.get("trigger") not in REOPEN_TRIGGERS:
            raise EventLogError("Typed: %s stage.reopened.trigger %r not in %s."
                                % (where, p.get("trigger"), ", ".join(sorted(REOPEN_TRIGGERS))))
        for k in ("reason", "authorized_by"):
            if not isinstance(p.get(k), str) or not p.get(k).strip():
                raise EventLogError("Typed: %s stage.reopened.%s must be a non-empty string." % (where, k))
        if p.get("trigger") != "person-request" and (not isinstance(p.get("finding_ref"), str)
                                                     or not p.get("finding_ref").strip()):
            raise EventLogError(
                "Typed: %s stage.reopened with trigger %r must carry payload.finding_ref (the finding or "
                "defect id it corrects); only a person-request reopen may omit it." % (where, p.get("trigger")))

    # Shape check for the front-door graph.authored stages[] encoding (R8 resolution of
    # R10 Open O-4). The event-log schema documents each stages[] entry as
    # {name, active, seeded_root, deps}; the writer enforces that SHAPE (not merely the
    # top-level key's presence), so a malformed pipeline instantiation is rejected. Exactly
    # one stage carries seeded_root: true (the run's entry root).
    if t == "graph.authored":
        stages = p.get("stages")
        if not isinstance(stages, list) or not stages:
            raise EventLogError("Typed: %s graph.authored.stages must be a non-empty list." % where)
        seeded_roots = 0
        for row in stages:
            if not isinstance(row, dict):
                raise EventLogError(
                    "Typed: %s graph.authored.stages entries must be objects "
                    "{name, active, seeded_root, deps}; got %r." % (where, row))
            for k in ("name", "active", "seeded_root", "deps"):
                if k not in row:
                    raise EventLogError(
                        "Typed: %s graph.authored.stages entry missing %r (each entry is "
                        "{name, active, seeded_root, deps}): %r" % (where, k, row))
            if not isinstance(row.get("active"), bool) or not isinstance(row.get("seeded_root"), bool):
                raise EventLogError(
                    "Typed: %s graph.authored.stages entry 'active'/'seeded_root' must be "
                    "booleans: %r" % (where, row))
            if not isinstance(row.get("deps"), list):
                raise EventLogError(
                    "Typed: %s graph.authored.stages entry 'deps' must be a list: %r" % (where, row))
            if row.get("seeded_root") is True:
                seeded_roots += 1
        if seeded_roots != 1:
            raise EventLogError(
                "Typed: %s graph.authored.stages must carry exactly one seeded_root (got %d)."
                % (where, seeded_roots))
    # Shape check for the terminal accept-with-open disposition (G-F). residuals[] carries the
    # named opens by kind; the disposition itself is enforced in append-event.sh (accept-with-open).
    if t == "stage.accepted_with_residual":
        residuals = p.get("residuals")
        if not isinstance(residuals, list) or not residuals:
            raise EventLogError(
                "Typed: %s stage.accepted_with_residual.residuals must be a non-empty list." % where)
        for row in residuals:
            if (not isinstance(row, dict) or not row.get("id")
                    or row.get("kind") not in ("value", "rule", "scope", "access")
                    or not row.get("hydrate_at")):
                raise EventLogError(
                    "Typed: %s stage.accepted_with_residual residuals must each be "
                    "{id, kind in {value, rule, scope, access}, hydrate_at}; got %r." % (where, row))
        # accept-with-open is HUMAN-GATED at G-F (companion §4.1): the terminal event must
        # carry a gate_ref (a string event-id) to the accepted G-F human.decision that signed
        # it off. Presence + type here; append-event.sh re-derives that it resolves to an
        # ACCEPTED G-F human.decision for the stage.
        if not isinstance(p.get("gate_ref"), str) or not p.get("gate_ref").strip():
            raise EventLogError(
                "Typed: %s stage.accepted_with_residual must carry a non-empty string "
                "payload.gate_ref (the accepted G-F human.decision that signs it off)." % where)


def _stage_prior(prior, stage, etype):
    return [e for e in prior if e.get("stage") == stage and e.get("type") == etype]


def latest_terminal(prior, stage):
    """The stage's governing terminal event, or None when the stage is open.

    Terminal iff the latest terminal event is later than the latest stage.reopened. A gate tag
    (scripts/_stageindex.GATE_TAGS) is never terminal."""
    if _stageindex.is_gate(stage):
        return None
    terminal = None
    for e in prior:
        if e.get("stage") != stage:
            continue
        if e.get("type") in TERMINAL_TYPES:
            terminal = e
        elif e.get("type") == "stage.reopened":
            terminal = None
    return terminal


def is_terminal(events, stage):
    return latest_terminal(events, stage) is not None


def _validate_reopen(event, prior, terminal, where):
    stage = event.get("stage")
    p = _payload(event)
    if not _stageindex.describe(stage)["known"]:
        kind = ("a gate tag" if _stageindex.is_gate(stage) else
                "a legacy stage key" if _stageindex.is_legacy(stage) else "not a canonical stage key")
        raise EventLogError(
            "Typed transition: %s stage.reopened for %r is refused — %r is %s; only a canonical "
            "pipeline stage can be reopened." % (where, stage, stage, kind))
    if terminal is None:
        raise EventLogError(
            "Typed transition: %s stage.reopened for stage %r is refused — the stage is not terminal "
            "(reopening an open stage is refused; correct it with a new submission instead)." % (where, stage))
    auth = p.get("authorized_by")
    if auth == FAST_MODE_POLICY:
        opened = prior[0].get("payload", {}) if prior else {}
        if not (str(opened.get("mode", "")).lower() == "factory" and opened.get("fast_mode") is True):
            raise EventLogError(
                "Typed transition: %s stage.reopened authorized by %r is refused — that policy applies "
                "only on a factory run opened with fast_mode: true; a person's accepted human.decision "
                "must authorize this reopen." % (where, FAST_MODE_POLICY))
        return
    decision = next((e for e in prior if e.get("event_id") == auth), None)
    if (decision is None or decision.get("type") != "human.decision"
            or _payload(decision).get("decision") != "accepted"):
        raise EventLogError(
            "Typed transition: %s stage.reopened.authorized_by=%r must name an accepted human.decision "
            "already in the log (or be %r on a fast-mode factory run)." % (where, auth, FAST_MODE_POLICY))


def validate_transition(event, prior):
    """Legal-transition prerequisites for a single event against the prior events.

    Deliberately checks PREREQUISITE PRESENCE per stage, not a rigid full ordering
    (stages and parallel tracks interleave in one log). The authorization-critical
    prerequisites are what stop invented/stale routing:
      * review.returned  needs a prior executor.submitted in its stage;
      * review.reconciled needs a prior review.returned in its stage;
      * remediation.authored needs a prior review.returned in its stage;
      * stage.cleared / stage.blocked need a prior review verdict in its stage.
    Tighter identity/hash binding for clearance lives in append-event.sh (F1); this is
    the structural gate beneath it.
    """
    t = event.get("type")
    stage = event.get("stage")
    where = "event %s" % (event.get("event_id") or "?")

    # A stage that already recorded a terminal event accepts no further same-stage
    # events — a resubmission after clear/block/escalate must not slip in — until a
    # stage.reopened lifts it: the stage is terminal iff its latest terminal event is later
    # than its latest stage.reopened. A gate tag is never terminal (its pass is derived and
    # it re-runs whenever a pinned design artifact changes).
    if _stageindex.is_gate(stage) and t in ("stage.cleared", "stage.accepted_with_residual", "stage.blocked"):
        raise EventLogError(
            "Typed transition: %s %s for %r is refused — the design-coherence gate is never cleared as a "
            "stage; its pass is derived from its checks and review (scripts/_coherence_gate.py)." % (where, t, stage))
    terminal = latest_terminal(prior, stage)
    if t == "stage.reopened":
        _validate_reopen(event, prior, terminal, where)
    elif terminal is not None:
        raise EventLogError(
            "Typed transition: %s for stage %r is refused — the stage is already "
            "terminal (%s at %s)." % (where, stage, terminal.get("type"), terminal.get("event_id")))

    if t == "review.returned" and not _stage_prior(prior, stage, "executor.submitted"):
        raise EventLogError(
            "Typed transition: %s review.returned for stage %r has no prior "
            "executor.submitted." % (where, stage))
    if t == "review.reconciled" and not _stage_prior(prior, stage, "review.returned"):
        raise EventLogError(
            "Typed transition: %s review.reconciled for stage %r has no prior "
            "review.returned to reconcile." % (where, stage))
    if t == "remediation.authored" and not _stage_prior(prior, stage, "review.returned"):
        raise EventLogError(
            "Typed transition: %s remediation.authored for stage %r has no prior "
            "review.returned." % (where, stage))
    if t in ("stage.cleared", "stage.blocked", "stage.accepted_with_residual"):
        if not (_stage_prior(prior, stage, "review.returned")
                or _stage_prior(prior, stage, "review.reconciled")):
            raise EventLogError(
                "Typed transition: %s %s for stage %r has no prior review verdict "
                "(review.returned / review.reconciled)." % (where, t, stage))


def validate_new_event(event, prior, idx=None):
    """Validate one about-to-be-appended event against the already-validated prior."""
    validate_envelope(event, idx if idx is not None else len(prior))
    # run.opened may appear only as the first event, and its run_id anchors the run.
    if event.get("type") == "run.opened":
        if prior:
            raise EventLogError("Typed: run.opened may appear only as the first event.")
    else:
        if not prior:
            raise EventLogError("Typed: the first event must be run.opened.")
        run_id = prior[0].get("run_id")
        if event.get("run_id") != run_id:
            raise EventLogError(
                "Typed: event %s run_id %r != the run header's %r (immutable run id)."
                % (event.get("event_id") or "?", event.get("run_id"), run_id))
    validate_transition(event, prior)


def validate_typed(events):
    """Validate a whole (already structurally-valid) event list in strict typed mode.

    Raises EventLogError on the first violation. An empty list is vacuously valid
    (an empty log has nothing to route).
    """
    if not events:
        return events
    if events[0].get("type") != "run.opened":
        raise EventLogError("Typed: the first event must be run.opened.")
    run_opened = [e for e in events if e.get("type") == "run.opened"]
    if len(run_opened) != 1:
        raise EventLogError("Typed: exactly one run.opened is allowed (found %d)." % len(run_opened))
    for i in range(len(events)):
        validate_new_event(events[i], events[:i], idx=i)
    return events
