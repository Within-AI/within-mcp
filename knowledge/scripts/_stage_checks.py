"""One shared contract for early checks, stage clearance and human review ordering."""
import os
import re
import json
import _checks
import _demo_policy
import _reference_registry

# Workflow checks read schema version 2 bundles (checks/workflow_contract.py); only exit codes are read here.
GRAPH_CHECKS = ["workflow-graph-structure", "workflow-coverage-manifest", "workflow-systems-coverage", "workflow-open-and-contradiction"]
RENDER_CHECKS = GRAPH_CHECKS + ["workflow-render-binding", "workflow-render-geometry"]
STAGES = {
    "snapshot": {"input": "snapshot-intake", "checks": ["snapshot-structure"], "human": True},
    "stage-0-frozen-input": {"input": "snapshot-intake", "checks": ["snapshot-structure"], "human": True},
    "current-state": {"input": "snapshot-structure", "checks": RENDER_CHECKS, "human": True, "visual_review": True},
    "stage-2-gate-0-skeleton-graph": {"input": "snapshot-structure", "checks": GRAPH_CHECKS, "human": True},
    "track-a-current-state-workflow": {"input": "workflow-graph-structure", "checks": RENDER_CHECKS, "human": True, "visual_review": True},
    # future-state-reshape skips itself (PASS) on a run with no transformation map or no stages/ folder.
    "future-state": {"input": "workflow-graph-structure", "checks": RENDER_CHECKS + ["future-state-reshape"],
                     "human": True, "visual_review": True},
    "track-b-future-state-graph": {"input": "workflow-graph-structure", "checks": GRAPH_CHECKS, "human": True},
    "track-b-future-state-workflow-render": {"input": "workflow-graph-structure", "checks": RENDER_CHECKS, "human": True, "visual_review": True},
}


def latest(events, stage, kind):
    return next((e for e in reversed(events) if e["stage"] == stage and e["type"] == kind), None)


def check_receipt(events, stage, name, path, digest):
    return next((e for e in reversed(events) if e["stage"] == stage and e["type"] == "check.passed"
                 and e["payload"].get("check") == name and e["payload"].get("artifact_path") == path
                 and e["payload"].get("target_sha256") == digest), None)


def require_checks(events, stage, names, path, digest, root):
    for name in names:
        receipt = check_receipt(events, stage, name, path, digest)
        if receipt is None:
            raise ValueError("missing deterministic preflight %s on %s" % (name, path))
        # Revalidate transitive dependencies, not only the primary bundle hash.
        _checks.reverify_check_passed(receipt, root)


def require_reference_checks(events, stage, path, digest, root):
    """Every reference check the registry requires for this stage has a re-verified check.passed bound
    to this artifact identity AND run with argv starting ["--stage", stage].

    A receipt run for another stage (for example `--stage internal-handoff`, which trivially passes on a
    record-less artifact) never satisfies this stage."""
    for name in _reference_registry.clearance_checks(stage):
        receipts = [e for e in events if e["stage"] == stage and e["type"] == "check.passed"
                    and e["payload"].get("check") == name and e["payload"].get("artifact_path") == path
                    and e["payload"].get("target_sha256") == digest]
        bound = [e for e in receipts if (e["payload"].get("argv") or [])[:2] == ["--stage", stage]]
        if not bound:
            if receipts:
                raise ValueError("reference check %s was run for another stage" % name)
            raise ValueError("missing deterministic preflight %s on %s" % (name, path))
        _checks.reverify_check_passed(bound[-1], root)


def governing_review(events, stage, sub_id):
    """The single governing review event for this submission ID: whichever of a bound
    review.returned or a bound review.reconciled is TEMPORALLY LATEST in the log, not
    "a plain-returned clear, else fall back to any bound reconciled clear" -- the
    latter (and any other split-by-type preference) can let a stale reconciled verdict
    govern even after a later, un-reconciled review.returned for the same submission
    superseded it. THE ONE SHARED SOURCE for this: loop-controller.sh's action
    selection uses this too, so the two can never disagree on which verdict governs
    (a disagreement deadlocks the stage: the controller advises one action while the
    writer's clearance gate enforces a different governing verdict)."""
    by_id = {e.get("event_id"): e for e in events}

    def reconciled_bound(recon):
        refs = recon["payload"].get("reviews") or []
        return bool(refs) and all(
            (r := by_id.get(rid)) is not None and r.get("type") == "review.returned"
            and r.get("stage") == stage and r.get("payload", {}).get("submission_event_id") == sub_id
            for rid in refs)

    candidates = [e for e in events if e["stage"] == stage and (
        (e["type"] == "review.returned" and e["payload"].get("submission_event_id") == sub_id)
        or (e["type"] == "review.reconciled" and reconciled_bound(e)))]
    return candidates[-1] if candidates else None


def reconciliation_latest_bound(recon_ev, events, latest_sub_id, stage):
    """True iff a review.reconciled resolves ONLY to review.returned events for this
    stage, each bound to the latest submission EVENT IDENTITY — so a stale/cross-track
    reconciliation cannot serve as a governing clear for a newer submission."""
    by_id = {e.get("event_id"): e for e in events}
    refs = recon_ev.get("payload", {}).get("reviews", []) or []
    if not refs:
        return False
    for rid in refs:
        r = by_id.get(rid)
        if (not r or r.get("type") != "review.returned" or r.get("stage") != stage
                or r.get("payload", {}).get("submission_event_id") != latest_sub_id):
            return False
    return True


def has_governing_clear(events, stage, sub_id):
    """The writer's stage.cleared derivation: a clear review.returned bound to SUB_ID, or a clear
    review.reconciled that reconciles only reviews of SUB_ID. Shared by the writer's clearance and the
    design-coherence gate (scripts/_coherence_gate.py), so the two never derive a clear differently."""
    for e in events:
        if e.get("stage") != stage:
            continue
        ep = e.get("payload", {})
        if (e.get("type") == "review.returned"
                and ep.get("disposition") == "clear"
                and ep.get("submission_event_id") == sub_id):
            return True
        # A reconciliation governs a clear ONLY if it reconciles reviews of the
        # LATEST submission — never a stale cycle-1 reconciliation after a resubmit.
        if (e.get("type") == "review.reconciled"
                and ep.get("resolved_disposition") == "clear"
                and reconciliation_latest_bound(e, events, sub_id, stage)):
            return True
    return False


def governing_disposition(review_event):
    if review_event is None:
        return None
    if review_event["type"] == "review.reconciled":
        return review_event["payload"].get("resolved_disposition")
    return review_event["payload"].get("disposition")


def clear_review(events, stage, submission):
    review = governing_review(events, stage, submission["event_id"])
    return review if governing_disposition(review) == "clear" else None


def needs_human(events, stage):
    if _demo_policy.is_demo(events):
        return _demo_policy.needs_human(events, stage)
    if stage == "agent-build":
        return True  # Existing internal-DAG visual gate remains mandatory.
    if not STAGES.get(stage, {}).get("human"):
        return False
    run = next((e for e in events if e["type"] == "run.opened"), {})
    policy = run.get("payload", {})
    return policy.get("mode", "project").lower() != "factory" or policy.get("human_review_required", False)


def human_accepted(events, stage, submission):
    review = clear_review(events, stage, submission)
    if not review:
        return False
    after = events.index(review)
    if _demo_policy.is_demo(events):
        # An earlier approval cannot answer a newly raised significant exception.
        after = max([after] + [i for i, e in enumerate(events)
                    if e["stage"] == stage and e["type"] == "human.review_requested"
                    and e["payload"].get("submission_event_id") == submission["event_id"]])
    relevant = [e for e in events if e["stage"] == stage and e["type"] in {"human.visual_review", "human.decision"}
                and e["payload"].get("submission_event_id") == submission["event_id"]
                and e["payload"].get("artifact_sha256") == submission["payload"]["sha256"]
                and events.index(e) > after
                and (e["type"] == "human.visual_review" or e["payload"].get("gate") in {"acceptance", "current-state-acceptance"})]
    return bool(relevant and relevant[-1]["payload"].get("decision") == "accepted")


def has_diagram(root, submission):
    with open(os.path.join(root, submission["payload"]["artifact_path"]), encoding="utf-8") as f:
        return json.load(f).get("presentation") == "diagram"


def enforce(event, events, root):
    stage, kind, p = event["stage"], event["type"], event["payload"]
    rule = STAGES.get(stage)
    if not rule:
        return
    if kind == "executor.started":
        brief = latest(events, stage, "brief.authored")
        packet = latest(events, stage, "packet.authored")
        if not brief or not packet:
            raise ValueError("author packet and rubric before generation")
        for authored, key in ((brief, "brief_path"), (packet, "packet_path")):
            if _checks.sha256_of(os.path.join(root, authored["payload"][key])) != authored["payload"]["sha256"]:
                raise ValueError("authored input changed on disk")
        if p.get("packet_path") != packet["payload"]["packet_path"]:
            raise ValueError("executor did not select the authored packet")
        brief_text = open(os.path.join(root, brief["payload"]["brief_path"]), encoding="utf-8").read()
        if not p.get("output_path") or not re.search(r"(?m)^output_path: " + re.escape(p["output_path"]) + r"\s*\nidentity_from: executor\.submitted\s*$", brief_text):
            raise ValueError("pre-authored brief must bind the planned output_path to executor.submitted")
        inputs = p.get("inputs")
        if not isinstance(inputs, list) or len(inputs) != 1:
            raise ValueError("generation requires one explicitly bound primary input")
        ref = inputs[0]
        require_checks(events, stage, [rule["input"]], ref["path"], ref["sha256"], root)
    if kind == "executor.submitted":
        started = latest(events, stage, "executor.started")
        if not started:
            raise ValueError("submission requires a preflight-gated executor.started")
        if p.get("artifact_path") != started["payload"]["output_path"] or p.get("cycle") != started["payload"].get("cycle"):
            raise ValueError("submission path/cycle differs from executor.started")
        # Bind generated output back to the actual input checked for this attempt.
        artifact = os.path.join(root, p["artifact_path"])
        if _checks.sha256_of(artifact) != p["sha256"]:
            raise ValueError("submission SHA differs from disk")
        doc = json.load(open(artifact, encoding="utf-8"))
        key = "intake" if rule["input"] == "snapshot-intake" else "snapshot" if rule["input"] == "snapshot-structure" else "prior_workflow"
        ref = started["payload"]["inputs"][0]
        linked = doc.get(key, {})
        if linked.get("sha256") != ref["sha256"] or os.path.realpath(os.path.join(os.path.dirname(artifact), linked.get("path", ""))) != os.path.realpath(os.path.join(root, ref["path"])):
            raise ValueError("generated output does not bind its checked input via " + key)
        if stage in {"track-a-current-state-workflow", "track-b-future-state-workflow-render"}:
            original = json.load(open(os.path.join(root, ref["path"]), encoding="utf-8"))
            if doc.get("graph") != original.get("graph"):
                raise ValueError("rendering changed the approved graph")
    if kind in {"review.started", "review.returned", "stage.cleared", "stage.accepted_with_residual", "human.visual_review", "human.decision"}:
        if kind == "human.decision" and p.get("gate") not in {"acceptance", "current-state-acceptance"}:
            return
        sub = latest(events, stage, "executor.submitted")
        if not sub:
            raise ValueError("review requires a submission")
        require_reference_checks(events, stage, sub["payload"]["artifact_path"], sub["payload"]["sha256"], root)
        require_checks(events, stage, rule["checks"], sub["payload"]["artifact_path"], sub["payload"]["sha256"], root)
        run = next((e for e in events if e["type"] == "run.opened"), {})
        if rule.get("visual_review") and run.get("payload", {}).get("mode", "project").lower() != "factory" and not has_diagram(root, sub):
            raise ValueError("Project/Demo workflow output requires a diagram")
        if kind in {"human.visual_review", "human.decision"}:
            if event.get("actor", {}).get("engine") != "human" or not clear_review(events, stage, sub):
                raise ValueError("final human review must follow a clear LLM review")
            if p.get("submission_event_id") != sub["event_id"] or p.get("artifact_sha256") != sub["payload"]["sha256"]:
                raise ValueError("human review must bind the latest submission ID and SHA")
            if kind == "human.visual_review":
                checklist = p.get("checklist", [])
                if {r.get("item") for r in checklist} != {"V1", "V2", "V3", "V4", "V5", "V6"} or len(checklist) != 6:
                    raise ValueError("visual review requires exactly V1–V6")
                if p.get("decision") == "accepted" and not all(r.get("pass") is True for r in checklist):
                    raise ValueError("accepted visual review contains a failed item")
        if kind in {"stage.cleared", "stage.accepted_with_residual"} and needs_human(events, stage) and not human_accepted(events, stage, sub):
            raise ValueError("final human acceptance is required after LLM review")
