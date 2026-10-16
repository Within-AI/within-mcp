"""Demo's bounded evidence, first-pass grading and three-case evaluation policy."""
import hashlib
import json
from pathlib import Path
import re

OBSERVATION_LIMIT = 5
EVAL_CASE_LIMIT = 3
MAX_COHERENCE_CYCLES = 2  # First pass plus one targeted repair; then stop.
ASSESSMENT_KIND = "demo-self-assessment"
SIGNIFICANT_REASONS = {"access", "scope-change", "authority", "company-control", "coherence-blocker"}
MANIFEST = re.compile(r"<!--\s*CRITERIA-MANIFEST\s*([\s\S]*?)-->")


def is_demo(events):
    return any(e.get("type") == "run.opened" and
               e.get("payload", {}).get("mode", "").lower() == "demo" for e in events)


def needs_human(events, stage):
    """Demo skips routine signoffs unless an exception or explicit control applies."""
    run = next((e for e in events if e.get("type") == "run.opened"), {})
    if run.get("payload", {}).get("human_review_required", False):
        return True  # An explicitly recorded run/company control still applies.
    sub = next((e for e in reversed(events) if e.get("stage") == stage
                and e.get("type") == "executor.submitted"), None)
    if not sub:
        return False
    related = [e for e in events if e.get("stage") == stage
               and e.get("payload", {}).get("submission_event_id") == sub["event_id"]
               and e["payload"].get("artifact_sha256") == sub["payload"]["sha256"]]
    if any(e.get("type") == "human.review_requested"
           and e["payload"].get("reason_category") in SIGNIFICANT_REASONS for e in related):
        return True
    decisions = [e for e in related if e.get("type") in {"human.decision", "human.visual_review"}
                 and (e["type"] == "human.visual_review" or e["payload"].get("gate") in {"acceptance", "current-state-acceptance"})]
    return bool(decisions and decisions[-1]["payload"].get("decision") != "accepted")


def validate_assessor(event, submission):
    """Demo records the existing worker's assessment, never an independent reviewer."""
    actor = event.get("actor", {})
    engine = submission.get("actor", {}).get("engine") if submission else None
    if event["payload"].get("assessment_kind") != ASSESSMENT_KIND:
        raise ValueError("Demo requires an explicitly labeled self-assessment")
    if engine == "script":
        # Deterministic rendering/freezing has no model author. The current host
        # assesses that output; do not falsely attribute semantic scores to code.
        valid = actor.get("role") == "orchestrator" and actor.get("engine") in {"claude", "codex"}
    else:
        valid = actor.get("role") == "executor" and actor.get("engine") == engine and engine in {"claude", "codex"}
    if not valid:
        raise ValueError("Demo assessment must come from the existing executor, or current host for scripted output; no cross-model reviewer")


def coherence_criteria(text):
    match = MANIFEST.search(text)
    if not match:
        raise ValueError("Demo review brief requires a CRITERIA-MANIFEST")
    rows = json.loads(match.group(1))
    if not isinstance(rows, list) or not rows:
        raise ValueError("Demo criteria manifest must be nonempty")
    required = set()
    seen = set()
    for row in rows:
        if (not isinstance(row, dict) or not isinstance(row.get("criterion"), str)
                or not row["criterion"] or type(row.get("coherence_required")) is not bool):
            raise ValueError("Demo criteria need an explicit coherence_required boolean")
        if row["criterion"] in seen:
            raise ValueError("Duplicate Demo criterion")
        seen.add(row["criterion"])
        if row["coherence_required"]:
            if not isinstance(row.get("coherence_reason"), str) or not row["coherence_reason"].strip():
                raise ValueError("Coherence criteria need a concrete coherence_reason")
            required.add(row["criterion"])
    if not required:
        raise ValueError("Demo brief must identify at least one coherence criterion")
    return required


def stage_brief(events, stage, root):
    brief = next((e for e in reversed(events) if e.get("stage") == stage
                  and e.get("type") == "brief.authored"), None)
    if brief is None:
        raise ValueError("Demo review requires an authored brief")
    path = Path(root) / brief["payload"]["brief_path"]
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != brief["payload"]["sha256"]:
        raise ValueError("Demo review brief identity changed")
    return coherence_criteria(data.decode("utf-8"))


def accepted_scores(scores, required, pass_bar=4):
    return [s["criterion"] for s in scores
            if s["criterion"] not in required and s["score"] < pass_bar]


def disposition(scores, required, raw_disposition, pass_bar=4):
    if raw_disposition == "escalate":
        return "escalate"  # Structurally unreviewable is not a coherent result.
    return ("remediate" if any(s["criterion"] in required and s["score"] < pass_bar
                               for s in scores) else "clear")


def first_review(events, stage):
    return next((e for e in events if e.get("stage") == stage and
                 e.get("type") == "review.returned"), None)


def preserve_first_scores(events, stage, scores, required):
    first = first_review(events, stage)
    if first:
        original = [s for s in first["payload"]["scores"] if s["criterion"] not in required]
        current = [s for s in scores if s["criterion"] not in required]
        if sorted(original, key=lambda s: s["criterion"]) != sorted(current, key=lambda s: s["criterion"]):
            raise ValueError("Demo repair must preserve first-pass advisory scores and evidence verbatim")


def eval_cases(doc):
    """Validate Demo sample size without weakening ID/grounding checks elsewhere."""
    meta = doc.get("meta") or {}
    if meta.get("mode") != "demo":
        raise ValueError("Demo eval set requires meta.mode: demo")
    if not isinstance(meta.get("demo_limitations"), str) or not meta["demo_limitations"].strip():
        raise ValueError("Demo eval set must explain untested coverage in meta.demo_limitations")
    gates = [g["id"] for g in meta.get("gates", [])]
    cases = []
    for key, block in doc.items():
        if isinstance(block, dict) and "cases" in block:
            if key not in gates or not isinstance(block["cases"], list):
                raise ValueError("Demo cases must belong to a declared gate")
            cases.extend(block["cases"])
    if len(cases) != EVAL_CASE_LIMIT:
        raise ValueError("Demo requires exactly three eval cases in total")
    ids = [case.get("id") for case in cases]
    if not all(isinstance(cid, str) and cid for cid in ids) or len(set(ids)) != len(ids):
        raise ValueError("Demo eval case IDs must be present and unique")
    return cases


def calibration_cases(doc, calibration):
    ids = {case["id"] for case in eval_cases(doc)}
    mocks = calibration.get("mocks", [])
    if len(mocks) != EVAL_CASE_LIMIT or {m.get("case_id") for m in mocks} != ids:
        raise ValueError("Demo evaluator checks must reuse the same three cases, once each")


def enforce_event(event, events, root):
    """Writer guard: no repeat grading, optional-score repair or hidden eval expansion."""
    kind, stage, p = event["type"], event["stage"], event["payload"]
    demo = is_demo(events)
    if kind == "check.passed" and p.get("check") in {"case-coverage", "case-honesty-stamp", "judge-instrument-smoke"}:
        import yaml
        doc = yaml.safe_load((Path(root) / p["artifact_path"]).read_text(encoding="utf-8"))
        sampled = (doc.get("meta") or {}).get("mode") == "demo"
        if sampled != demo:
            raise ValueError("Eval-set mode must match the actual run mode")
        if demo:
            eval_cases(doc)
            if p.get("check") == "judge-instrument-smoke":
                argv = p.get("argv", [])
                if "--dry-run" not in argv or "--eval-set" not in argv or "--calibration" not in argv:
                    raise ValueError("Demo smoke check requires --dry-run and the explicit three-case eval/calibration files")
                if argv[argv.index("--eval-set") + 1] != p["artifact_path"]:
                    raise ValueError("Demo smoke check must use its bound eval set")
                cal_path = Path(root) / argv[argv.index("--calibration") + 1]
                calibration_cases(doc, yaml.safe_load(cal_path.read_text(encoding="utf-8")))
    if kind == "executor.started" and stage == "snapshot":
        inputs = p.get("inputs") or []
        if len(inputs) == 1:
            doc = json.loads((Path(root) / inputs[0]["path"]).read_text(encoding="utf-8"))
            if (doc.get("scope", {}).get("mode") == "demo") != demo:
                raise ValueError("Snapshot intake mode must match the actual run mode")
    if not demo:
        if p.get("assessment_kind") == ASSESSMENT_KIND:
            raise ValueError("Demo self-assessment cannot replace Project or Factory independent review")
        return
    if kind in {"human.decision", "human.visual_review"}:
        if event.get("actor", {}).get("role") != "human" or event.get("actor", {}).get("engine") != "human":
            raise ValueError("Demo automatic progress must not fabricate human acceptance")
    if kind == "human.review_requested":
        if p.get("reason_category") not in SIGNIFICANT_REASONS or not isinstance(p.get("reason"), str) or not p["reason"].strip():
            raise ValueError("Demo pauses only for a significant exception; include reason_category and a concrete reason")
        sub = next((e for e in reversed(events) if e.get("stage") == stage
                    and e.get("type") == "executor.submitted"), None)
        if (not sub or p.get("submission_event_id") != sub["event_id"]
                or p.get("artifact_sha256") != sub["payload"]["sha256"]
                or p.get("artifact_path") != sub["payload"]["artifact_path"]):
            raise ValueError("Demo human exception must bind the current submitted artifact")
    if kind == "review.started":
        raise ValueError("Demo does not launch a separate reviewer; record the executor's self-assessment")
    if kind == "review.reconciled":
        raise ValueError("Demo uses one self-assessment, not independent review reconciliation")
    if kind == "check.passed" and p.get("check") == "reviewer-independence":
        raise ValueError("Demo has no independent review; do not record an independence pass")
    if kind == "brief.authored":
        required = coherence_criteria((Path(root) / p["brief_path"]).read_text(encoding="utf-8"))
        if first_review(events, stage):
            previous = stage_brief(events, stage, root)
            if required != previous:
                raise ValueError("Demo cannot reclassify coherence criteria after grading")
    if kind in {"review.started", "review.returned"}:
        sub = next((e for e in reversed(events) if e.get("stage") == stage and
                    e.get("type") == "executor.submitted"), None)
        validate_assessor(event, sub)
        if sub and any(e.get("type") == "review.returned" and e.get("stage") == stage
                       and e["payload"].get("submission_event_id") == sub["event_id"] for e in events):
            raise ValueError("Demo accepts one grading pass per submission; no repeated grading")
    if kind == "remediation.authored":
        if any(e.get("stage") == stage and e.get("type") == "remediation.authored" for e in events):
            raise ValueError("Demo permits one coherence repair, not a graded remediation loop")
        required = stage_brief(events, stage, root)
        review = next((e for e in reversed(events) if e.get("stage") == stage and
                       e.get("type") == "review.returned"), None)
        bar = events[0]["payload"].get("loop_policy", {}).get("pass_bar", 4)
        failed = {s["criterion"] for s in review["payload"]["scores"]
                  if s["criterion"] in required and s["score"] < bar} if review else set()
        scope = p.get("scoped_to")
        if (p.get("reason") != "coherence-only" or not isinstance(scope, list)
                or not scope or not set(scope) <= failed):
            raise ValueError("Demo repair must target only failed coherence criteria with reason: coherence-only")
        if p.get("cycle", 0) != 2:
            raise ValueError("Demo allows only one targeted coherence repair")
    if kind in {"executor.started", "executor.submitted"}:
        cycle = p.get("cycle", 1)
        if type(cycle) is not int or not 1 <= cycle <= MAX_COHERENCE_CYCLES:
            raise ValueError("Demo allows a first pass and at most one coherence repair")
        submissions = [e for e in events if e.get("stage") == stage and e.get("type") == "executor.submitted"]
        if len(submissions) >= MAX_COHERENCE_CYCLES:
            raise ValueError("Demo coherence repair budget is exhausted")
        if cycle == 2 or first_review(events, stage):
            repair = next((e for e in reversed(events) if e.get("stage") == stage and
                           e.get("type") == "remediation.authored"), None)
            if not first_review(events, stage) or not repair or cycle != 2 or repair["payload"].get("cycle") != cycle:
                raise ValueError("Demo cannot regenerate graded work without a coherence repair")
        if kind == "executor.started":
            if not p.get("output_path") or (Path(root) / p["output_path"]).exists():
                raise ValueError("Demo generation needs a new output_path; preserve earlier artifacts")
    if kind == "executor.started" and stage == "snapshot":
        inputs = p.get("inputs") or []
        if len(inputs) != 1:
            raise ValueError("Demo snapshot needs its bounded intake input")
