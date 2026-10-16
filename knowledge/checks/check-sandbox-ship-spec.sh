#!/usr/bin/env python3
# This file is Python despite its .sh name: run it directly or as `python3 <this file>`; `bash <this file>` cannot run it.
"""Sandbox-ship spec check (stage 12) -- the spec is bound to the cleared design and to stage 11's manifest.

Plain-language handle: "check the spec still describes the design that cleared, every system says how it is
reached, every capability is declared, nothing stage 11 carried was dropped, and no field over-claims". Run with
the run root as the working directory.

Fails (exit 1), each finding listed with the thing to supply, on:
  1. a design file the spec pins that is moved, stale or unpinned, or a stage 11 manifest pin that is missing,
     moved or stale (all read through stage 11's readers in checks/_prebuild_sim.py);
  2. a system with no binding: real needs a grant file (path and sha256, present and matching) and client data;
     stub needs a reason; a real system labelled synthetic is refused;
  3. a capability (agent_execution, runtime_log, observability, persistence, llm_judge) neither given an
     implementation nor marked not applicable with a reason;
  4. an open item or adapter risk stage 11's manifest carries that the spec's lists do not name (with a
     disposition: carried-forward, resolved or handover);
  5. a missing honesty or stops-before-production statement, or any field that claims production readiness or
     equivalence to the client environment.
The event log is required: a run with no events.jsonl is refused (exit 2) naming it.

Usage: check-sandbox-ship-spec.sh [SPEC]     (default output/sandbox-ship/spec.yaml)
Exit: 0 pass; 1 findings; 2 setup error (the spec or the event log cannot be read).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _prebuild_sim as pbs  # noqa: E402
import _sandbox_ship_spec as spec  # noqa: E402


def main(argv):
    args = [a for a in argv if not a.startswith("--")]
    if len(args) > 1:
        sys.stderr.write("usage: check-sandbox-ship-spec.sh [SPEC]\n")
        return 2
    rel = args[0] if args else spec.SPEC_PATH
    try:
        problems = spec.check(os.getcwd(), rel)
    except pbs.PrebuildError as exc:
        sys.stderr.write("sandbox-ship-spec: %s\n" % exc)
        return 2
    if problems:
        sys.stderr.write("sandbox-ship spec check FAILED for %s:\n  - %s\n" % (rel, "\n  - ".join(problems)))
        return 1
    print("PASS sandbox-ship-spec: every design pin holds, every system is bound, every capability is declared, "
          "every open item and adapter risk from stage 11 is carried, and no field over-claims.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
