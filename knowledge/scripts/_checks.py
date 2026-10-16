#!/usr/bin/env python3
"""Check registry + append-time re-verification (mechanism A, item 1).

An earlier version of the machinery trusted a `check.passed` event's self-declared
`actor.engine == "script"` string as proof a check ran. It is not proof: the same
agent doing the work can hand-author that event. This module makes the check event
UNFORGEABLE against a lazy agent by having the SINGLE WRITER re-derive the result at
append time instead of trusting the payload:

  1. It recomputes the target artifact's SHA-256 from disk and refuses on mismatch
     (a `check.passed` cannot claim a status for an artifact that is not on disk with
     that identity).
  2. It RE-RUNS the named check against the artifact and refuses unless the check
     itself exits 0 (the payload's asserted result is ignored — re-derived, not read).

So a hand-authored `check.passed` is accepted only if the named check genuinely
passes on the real on-disk artifact right now — i.e. only if it is TRUE. A forged
SHA is refused at step 1; a forged pass (correct SHA, artifact that does not pass the
check) is refused at step 2. `actor.engine` is recorded, no longer trusted.

HONEST LIMIT (mechanism A): this defends against a lazy agent taking the shortcut, not
against a determined one on the same filesystem, who could edit a check script or
write the log directly, bypassing this writer. A real trust boundary needs the writer
in a separate trust domain (mechanism D, the harness-owned writer / launcher). The
factory stays human-supervised until D exists.

Only artifact-bound checks belong here. `check-reviewer-independence.sh` is keyed on
the log + stage (not a single artifact SHA), so it is a clear-time review-integrity
check, not an authoring gate, and is deliberately absent.
"""
import hashlib
import os
import subprocess
import sys
import time


class CheckError(Exception):
    """Raised when a check.passed event cannot be re-verified."""


_HERE = os.path.dirname(os.path.abspath(__file__))

# check name -> the script that re-runs it. The check's FIRST positional arg is the
# primary artifact whose SHA-256 is pinned as payload.target_sha256.
CHECK_REGISTRY = {
    **{name: "../checks/check-" + name + ".py" for name in (
        "snapshot-intake", "snapshot-structure", "workflow-graph-structure",
        "workflow-coverage-manifest", "workflow-systems-coverage",
        "workflow-open-and-contradiction",
        "workflow-render-binding", "workflow-render-geometry")},
    "review-brief-review-set": "check-review-brief-review-set.sh",
    "brief-rubric":            "check-brief-rubric.sh",
    "brief-scope-coherence":   "check-brief-scope-coherence.sh",
    "packet-authoring":        "check-packet-authoring.sh",
    # --- ATD design-half stage checks (R8). Paths are relative to this scripts/ dir
    # (script_path joins against _HERE). Registering them here makes each stage's
    # check.passed writer-re-verifiable, which is what STAGE_CLEARED_CHECKS binds to.
    "build-spec-structure":        "../checks/check-build-spec-structure.sh",
    "internal-dag-shape":          "../checks/check-internal-dag-shape.sh",
    # Fast Mode reshape (0400): the future state's reshape, outcome and write-path check, and the cooperating-agent
    # id lookup. Each takes one path (a run root, or a file under it) and skips itself when its inputs are absent.
    "future-state-reshape":        "../checks/check-future-state-reshape.py",
    "agent-architecture":          "../checks/check-agent-architecture.py",
    "interface-design-structure":  "../checks/check-interface-design-structure.sh",
    "interface-seating-directions":"../checks/check-interface-seating-directions.sh",
    "predicate-binding":           "../checks/check-predicate-binding.sh",
    "rule-honesty-stamp":          "../checks/check-rule-honesty-stamp.sh",
    "hunt-coverage":               "../checks/check-hunt-coverage.sh",
    "coherence-matrix-closure":    "../checks/check-coherence-matrix-closure.sh",
    "predicate-binding-recheck":   "../checks/check-predicate-binding-recheck.sh",
    # The design-coherence gate's third check (added 2026-09-25): every file the gate's design set pins is
    # still the pinned bytes. Its artifact is the design set, output/coherence/design-set.json.
    "design-set-current":          "../checks/check-design-set-current.sh",
    # --- Stage 11, the pre-build simulation, and stage 9's code-or-judgment mark (registered 2026-09-25).
    # Their scripts ship with the plugin; in an installation where one is missing, a check.passed for it cannot
    # be recorded and the writer refuses the stages that require it, naming the missing script.
    "prebuild-sim-structure":      "../checks/check-prebuild-sim-structure.sh",
    "prebuild-sim-data-boundary":  "../checks/check-prebuild-sim-data-boundary.sh",
    "prebuild-sim-rule-trace":     "../checks/check-prebuild-sim-rule-trace.sh",
    "prebuild-sim-results":        "../checks/check-prebuild-sim-results.sh",
    "sandbox-ship-clearance":      "../checks/check-sandbox-ship-clearance.sh",
    "rule-mark":                   "../checks/check-rule-mark.sh",
    "case-coverage":               "../eval-stage/checks/check-case-coverage.sh",
    "case-honesty-stamp":          "../eval-stage/checks/check-case-honesty-stamp.sh",
    "judge-instrument-smoke":      "../eval-stage/checks/check-judge-instrument-smoke.sh",
    # --- Worked-reference wiring. The record check and copy guard take ["--stage", KEY, artifact];
    # the packet check takes ["--stage", KEY, packet]. Each runs with the run root as cwd.
    "reference-use-record":        "../checks/check-reference-use-record.sh",
    "reference-copy-guard":        "../checks/check-reference-copy-guard.sh",
    "packet-references":           "check-packet-references.sh",
    "sandbox-ship-spec":           "../checks/check-sandbox-ship-spec.sh",
    "sandbox-ship-package":        "../checks/check-sandbox-ship-package.sh",
    "sandbox-ship-leak-scan":      "../checks/check-sandbox-ship-leak-scan.sh",
}


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def script_path(check):
    name = CHECK_REGISTRY.get(check)
    if not name:
        return None
    return os.path.join(_HERE, name)


def reverify_check_passed(event, base_dir=None):
    """Re-derive a check.passed's status. Raise CheckError if it does not hold.

    Expects payload {check, artifact_path, target_sha256, argv}. `argv` is the exact
    argument vector the check was run with (must include artifact_path). We recompute
    the artifact SHA and re-run the check; we trust neither the claimed SHA nor any
    claimed result in the payload.

    `base_dir` (the run root) is where run-relative payload paths resolve. The logged
    event keeps its portable relative paths; re-verification opens the artifact relative
    to base_dir and re-runs the check with cwd=base_dir, so the result does NOT depend
    on the caller's working directory (a check launched from any cwd re-verifies the
    same files). Absolute paths are honoured as-is.
    """
    p = event.get("payload", {}) or {}
    check = p.get("check")
    artifact_path = p.get("artifact_path")
    target_sha = p.get("target_sha256")
    argv = p.get("argv")

    if check not in CHECK_REGISTRY:
        raise CheckError(
            "check.passed names check=%r, which is not a re-verifiable artifact "
            "check %s." % (check, sorted(CHECK_REGISTRY)))
    if not artifact_path:
        raise CheckError("check.passed must carry payload.artifact_path (the "
                         "artifact the check ran against).")
    if not target_sha:
        raise CheckError("check.passed must carry payload.target_sha256.")
    if not isinstance(argv, list) or not argv:
        raise CheckError("check.passed must carry payload.argv (the exact args the "
                         "check was invoked with), so the writer can re-run it.")
    if artifact_path not in argv:
        raise CheckError("check.passed payload.artifact_path %r must appear in "
                         "payload.argv." % artifact_path)
    if CHECK_REGISTRY[check].endswith(".py") and argv != [artifact_path]:
        raise CheckError("workflow/snapshot checks take exactly their bound artifact")

    def resolve(path):
        if os.path.isabs(path) or not base_dir:
            return path
        return os.path.join(base_dir, path)

    artifact_abs = resolve(artifact_path)
    if not os.path.isfile(artifact_abs):
        raise CheckError("check.passed target artifact is not on disk: %s" % artifact_abs)

    disk_sha = sha256_of(artifact_abs)
    if disk_sha != target_sha:
        raise CheckError(
            "check.passed SHA-256 mismatch for %s: payload.target_sha256=%s but the "
            "on-disk artifact hashes to %s (forged or stale identity)."
            % (artifact_abs, target_sha, disk_sha))

    script = script_path(check)
    started = time.perf_counter()
    try:
        proc = subprocess.run(
            [sys.executable, script] + [str(a) for a in argv],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            cwd=(base_dir or None))
    except OSError as e:
        raise CheckError("Could not re-run check %r (%s): %s" % (check, script, e))
    if proc.returncode != 0:
        detail = (proc.stderr or b"").decode("utf-8", "replace").strip()
        raise CheckError(
            "check.passed refused: re-running %s exited %d (not a pass). %s"
            % (check, proc.returncode, detail))
    # Writer-owned measurement, distinct from the caller's initial check duration.
    p["reverification_duration_ms"] = round((time.perf_counter() - started) * 1000, 3)
    return disk_sha
