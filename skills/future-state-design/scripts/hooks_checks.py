#!/usr/bin/env python3
"""Check-matrix hook (Pins §1, §10, addendum G1): the checks a stage runs, as [(label, argv, needed)].

argv is run as `python3 <argv...>` with the run folder as the working directory (fsd.run_checks); needed lists the
input files the check reads, and an empty list means "no file" (the check is not run). Knowledge checks come from
knowledge.path("checks", run), the root knowledge_root.find resolved (or the one the run recorded); the skill's own
checks sit beside this file. A run is multi-process when its snapshot names two or more processes, and has a direction
when inputs/design-direction.json exists.

  set-map               a3-format --kind set-map, evidence-freeze (no reference record: Pins §1), set-map-movers
  current-state single  prototype set + run-hygiene --part lanes,files,ids,paths + evidence-freeze
  current-state multi   prototype set (render-geometry -> render-geometry-a3) + a3-format --kind graph,
                        a3-current-state, run-hygiene, evidence-freeze
  transformation-plan   prototype set (+ design-direction with a direction) + run-hygiene --part files
                        + writes-and-scope --part scope (with a direction) + evidence-freeze --file <map>
  future-state single   prototype set + run-hygiene --part lanes,files,ids,paths + writes-and-scope --part writes
                        + evidence-freeze
  future-state multi    prototype set (render-geometry -> render-geometry-a3) + a3-format --kind graph,
                        a3-future-ai-frames-lanes, a3-future-context-platform, a3-future-splits-links-frames,
                        run-hygiene, writes-and-scope --part writes, evidence-freeze
                        + audit-response in the rework round (stages/pass-1/ambition-audit/ambition-audit.md exists;
                        check_audit_response.py <run>, beside this file; notes only, NOTES_ONLY)
  ambition-audit        ambition-audit only (check_ambition_audit.py <run>, beside this file); never the map or workflow
                        checks. Its output is notes only (NOTES_ONLY): it never counts as a failing check.
Multi-process run-hygiene passes no --part, so it runs every part, `paths` (no absolute pin paths) included.
REPAIR_SCOPE says which failing findings the stage's one repair can fix (repair_scope classifies a check result),
SETUP_ERROR_EXIT the exit code each check documents as a setup error (never repairable here), and
TIMEOUTS gives the per-check time limit (TIMEOUT_DEFAULT for any check not named). Stdlib only.
"""
import glob, json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import knowledge  # noqa: E402

WORKFLOW = ("systems-coverage", "graph-structure", "coverage-manifest", "open-and-contradiction", "render-binding", "render-geometry")
MAP = "stages/transformation-plan/transformation-map.md"
A3_FUTURE = ("a3-future-ai-frames-lanes", "a3-future-context-platform", "a3-future-splits-links-frames")
AUDIT_FILE = "ambition-audit/ambition-audit.md"
PASS_1_AUDIT = "stages/pass-1/ambition-audit/ambition-audit.md"   # the first audit, kept once the rework starts
NOTES_ONLY = ("ambition-audit", "audit-response")   # check labels whose result is recorded as notes, never as a failing check


def is_multi(run):
    try:
        with open(os.path.join(run, "inputs", "snapshot.json"), encoding="utf-8") as f:
            ids = (json.load(f).get("scope") or {}).get("process_ids") or []
    except (OSError, ValueError, AttributeError):
        return False
    return len(ids) >= 2


def has_direction(run):
    return os.path.isfile(os.path.join(run, "inputs", "design-direction.json"))


def carrier(key, run):
    """The file carrying the reference_use record (the prototype's rule, unchanged)."""
    import fsd  # fsd imports this module; imported here to avoid a load-time cycle
    return fsd.carrier(key, run)


def for_stage(key, run):
    C = knowledge.path("checks", run)
    multi, direction = is_multi(run), has_direction(run)
    g = lambda pat: sorted(glob.glob(os.path.join(run, "stages", pat)))
    k = lambda name, *args: [os.path.join(C, name), "--run", run, "--stage", key] + list(args)
    own = lambda name, *args: [os.path.join(HERE, name), "--run", run, "--stage", key] + list(args)
    out = []

    if key == "ambition-audit":
        return [("ambition-audit", [os.path.join(HERE, "check_ambition_audit.py"), run], g(AUDIT_FILE))]

    if key == "set-map":
        sm = g("set-map/set-map.json")
        return [("a3-format", k("check-a3-format.py", "--kind", "set-map"), sm),
                ("evidence-freeze", k("check-evidence-freeze.py"), sm),
                ("set-map-movers", own("check_set_map_movers.py"), sm)]

    # the prototype set (Pins §10), with render-geometry -> render-geometry-a3 for a multi-process graph stage
    if key in ("current-state", "future-state"):
        b = g(f"{key}/{key}.json")
        for n in WORKFLOW:
            if n == "render-geometry" and multi:
                out.append(("workflow-render-geometry-a3", k("check-workflow-render-geometry-a3.py"), b))
            else:
                out.append((f"workflow-{n}", [os.path.join(C, f"check-workflow-{n}.py")] + b, b))
        if key == "future-state":
            out.append(("future-state-reshape", [os.path.join(C, "check-future-state-reshape.py"), run], b))
    else:
        b = g("transformation-plan/transformation-map.md")
        out.append(("transformation-map-shape", [os.path.join(C, "check-transformation-map-shape.sh")] + b, b))
    cand = carrier(key, run)
    for name, script in (("reference-use-record", "check-reference-use-record.sh"), ("reference-copy-guard", "check-reference-copy-guard.sh")):
        if cand:
            out.append((name, [os.path.join(C, script), "--stage", key, cand[0]], cand))
    if key in ("transformation-plan", "future-state"):
        out.append(("open-ids-in-register", [os.path.join(HERE, "check_open_ids.py"), run], g("current-state/current-state.input.json")))
    if key == "transformation-plan" and direction:
        out.append(("design-direction", [os.path.join(HERE, "check_direction.py"), run], ["inputs/design-direction.json"]))

    # successors and additions
    if key == "transformation-plan":
        out.append(("run-hygiene", own("check_run_hygiene.py", "--part", "files"), b))
        if direction:
            out.append(("writes-and-scope", own("check_writes_and_scope.py", "--part", "scope"), b))
        out.append(("evidence-freeze", k("check-evidence-freeze.py", "--file", MAP), b))
        if os.path.isfile(os.path.join(run, PASS_1_AUDIT)):   # the rework round: its audit response, notes only
            out.append(("audit-response", [os.path.join(HERE, "check_audit_response.py"), run], [PASS_1_AUDIT]))
        return out

    inp = g(f"{key}/{key}.input.json")
    if multi:
        out.append(("a3-format", k("check-a3-format.py", "--kind", "graph"), inp))
        if key == "current-state":
            out.append(("a3-current-state", k("check-a3-current-state.py"), inp))
        else:
            out += [(name, k(f"check-{name}.py"), inp) for name in A3_FUTURE]
        out.append(("run-hygiene", own("check_run_hygiene.py"), b or inp))
    else:
        out.append(("run-hygiene", own("check_run_hygiene.py", "--part", "lanes,files,ids,paths"), b or inp))
    if key == "future-state":
        out.append(("writes-and-scope", own("check_writes_and_scope.py", "--part", "writes"), b or inp))
    out.append(("evidence-freeze", k("check-evidence-freeze.py"), inp or b))
    return out


# ---------------------------------------------------------------- repair scope and time limits

TIMEOUT_DEFAULT = 60
TIMEOUTS = {"reference-copy-guard": 180}   # it scans every evidence file; real runs need more than a minute

SET_MAP_OWNS = "the set map owns it; fix it there and run this stage again"
MAP_OWNS = "the transformation map owns it; fix it there and run this stage again"
# (check label, finding rule) -> (why not repairable here, condition). A condition of None means always; "set-map-link"
# means only when the finding's `where` is a link id in the run's inputs/set-map.json.
REPAIR_SCOPE = {
    ("a3-current-state", "set-map-processes-match"): (SET_MAP_OWNS, None),
    ("a3-current-state", "process-has-flow-link"): (SET_MAP_OWNS, None),
    ("a3-current-state", "state-has-one-mover"): (SET_MAP_OWNS, None),
    ("a3-current-state", "variant-entry-rule-or-open-point"): (SET_MAP_OWNS, None),
    ("a3-current-state", "waiting-state-resolved"): (SET_MAP_OWNS, None),
    ("a3-current-state", "shared-instances-not-assumed"): (SET_MAP_OWNS, "set-map-link"),
    ("a3-future-ai-frames-lanes", "ai-use-decision"): (MAP_OWNS, None),
    ("a3-future-ai-frames-lanes", "table-shape"): (MAP_OWNS, None),
    ("a3-future-splits-links-frames", "set-link-missing"): (MAP_OWNS, None),
    ("a3-future-splits-links-frames", "table-shape"): (MAP_OWNS, None),
}
OUTSIDE = {"timeout": "the check ran out of time; the cause is outside the stage's files",
           "setup-error": "the check could not set up; the cause is outside the stage's files"}
# check label -> the exit code its script's docstring documents as a setup error. A failing result with that code is a
# setup error even when the check printed plain text (the copy guard's registry and string-set drift do).
SETUP_ERROR_EXIT = {
    "reference-copy-guard": 2, "reference-use-record": 2, "transformation-map-shape": 2, "evidence-freeze": 2,
    "workflow-render-geometry-a3": 2, "a3-format": 2, "a3-current-state": 2, "a3-future-ai-frames-lanes": 2,
    "a3-future-context-platform": 2, "a3-future-splits-links-frames": 2, "run-hygiene": 2, "writes-and-scope": 2,
    "set-map-movers": 2,
}


def timeout_for(label):
    return TIMEOUTS.get(label, TIMEOUT_DEFAULT)


def set_map_link_ids(run):
    """The link ids in inputs/set-map.json, or None when it is missing or cannot be read (callers fall back to repairable)."""
    try:
        with open(os.path.join(run, "inputs", "set-map.json"), encoding="utf-8") as f:
            links = json.load(f).get("links")
        return {l.get("id") for l in links if isinstance(l, dict)}
    except (OSError, ValueError, AttributeError, TypeError):
        return None


def finding_scope(label, finding, run, links):
    """(repairable, why) for one finding of a failing check."""
    rule = finding.get("rule") if isinstance(finding, dict) else None
    hit = REPAIR_SCOPE.get((label, rule))
    if hit is None:
        return True, None
    why, cond = hit
    if cond == "set-map-link" and (links is None or finding.get("where") not in links):
        return True, None
    return False, "%s: %s" % (rule, why)


# The open-ids check reads the transformation map's open items: in the future-state stage a finding on the map's own
# lines (where begins "transformation-map") is the map's, never the future state's, so it never spends that stage's repair.
MAP_OWNED_OPEN_IDS = ("open-items-shape",)


def repair_scope(label, result, findings, run, check_result=None, stage=None):
    """(repairable, why) for a failing check. result is fsd's result field; check_result is the `result` the check printed
    in its JSON, if any; findings is the parsed findings list, or None when the output was not JSON with findings. A check
    is repairable when any finding is, or when it failed with no parsed findings."""
    if label in SETUP_ERROR_EXIT and result == "fail(rc=%d)" % SETUP_ERROR_EXIT[label]:
        check_result = "setup-error"
    for bad in ("timeout", "setup-error"):
        if bad in (result, check_result):
            return False, "Not repairable here: %s." % OUTSIDE[bad]
    if not findings:
        return True, "Repairable here: the check failed on the stage's own files."
    links = set_map_link_ids(run) if any(isinstance(f, dict) and f.get("rule") == "shared-instances-not-assumed"
                                         for f in findings) else None
    scopes = [(False, "%s: %s" % (f.get("rule"), MAP_OWNS))
              if stage == "future-state" and label == "open-ids-in-register" and isinstance(f, dict)
              and (f.get("rule") in MAP_OWNED_OPEN_IDS or str(f.get("where") or "").startswith("transformation-map"))
              else finding_scope(label, f, run, links) for f in findings]
    if any(ok for ok, _ in scopes):
        return True, "Repairable here: at least one finding can be fixed in the stage's own files."
    whys = sorted({w for _, w in scopes})
    return False, "Not repairable here: %s." % "; ".join(whys)
