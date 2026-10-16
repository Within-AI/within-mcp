#!/usr/bin/env python3
# This file is Python despite its .sh name: run it directly or as `python3 <this file>`; `bash <this file>` cannot run it.
"""Sandbox-ship clearance check (stage 12) -- is the stage clearable, from evidence.

Plain-language handle: "check the Sandbox stage has live evidence for this exact package, or a person-approved
open item saying the live run is still to do". Run with the run root as the working directory.

Clearable (exit 0) by either route:
  A. live_run.status run, the evidence file matches its sha256, and it records the package hash computed now
     (sha256 over the sorted lines "dest<TAB>file-sha256" of package.files) and states the container was built and
     started, the Test Suite ran inside it, and the results;
  B. live_run.status not_run, open_items includes live-sandbox-run, and live_run.approved_by names a person.
Both routes need the spec's stops_before_production statement. Clearing this stage says nothing about production
launch; that is the Production Readiness Gate's decision.

Usage: check-sandbox-ship-clearance.sh --spec SPEC
Exit: 0 clearable; 1 not clearable; 2 setup error (the spec cannot be read).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _sandbox_ship_clearance as clr  # noqa: E402


def main(argv):
    spec = None
    if len(argv) == 2 and argv[0] == "--spec":
        spec = argv[1]
    elif len(argv) == 1 and argv[0].startswith("--spec="):
        spec = argv[0].split("=", 1)[1]
    if not spec:
        sys.stderr.write("usage: check-sandbox-ship-clearance.sh --spec SPEC\n")
        return 2
    try:
        route, findings, rows = clr.evaluate(os.getcwd(), spec)
    except clr.SetupError as exc:
        sys.stderr.write("sandbox-ship-clearance: %s\n" % exc)
        return 2
    if findings:
        sys.stderr.write("sandbox-ship clearance check NOT CLEARABLE for %s:\n  - %s\n"
                         % (spec, "\n  - ".join(findings)))
        if not any(f == clr.NEITHER for f in findings) and route is None:
            sys.stderr.write("  Passing tests alone clear nothing.\n")
        return 1
    how = ("live evidence bound to this package's hash" if route == "A"
           else "accepted without a live run: open item live-sandbox-run approved by a person")
    print("CLEARABLE sandbox-ship (%s). This stage stops before production launch; the Production Readiness Gate "
          "is a separate decision." % how)
    if rows:
        print("Handover, per system (Sandbox versus production):")
        for r in rows:
            print("  - " + r)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
