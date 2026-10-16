#!/usr/bin/env python3
"""Every Observed claim in one workflow stage cites a source: a stage check, run before the page is written.

Usage: check_observed_cited.py RUN_ROOT --stage current-state|future-state [--snapshot-dir DIR]   (default DIR: RUN_ROOT/inputs)

Reads stages/<stage>/<stage>.input.json and <stage>.json and applies the post-run observed-claims rule (postrun.observed_claims): an
Observed object cites source_refs, each source_id exists in the frozen snapshot (source or step id), its raw pull reads, and a given pointer resolves.
An open issue does not stand in for a source: an element with no source and an open issue is Assumed, never Observed.
Exit 0 every Observed claim is cited; 1 a claim is not, or a stage file is missing or unreadable; 2 the snapshot cannot be read.
Read-only; stdlib only.
"""
import json, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import postrun  # noqa: E402

PROBLEMS = ("no_citation", "unknown_source", "pointer_unresolved", "source_unreadable")


def main(argv):
    if len(argv) < 2 or "--stage" not in argv:
        print(__doc__.strip().splitlines()[2], file=sys.stderr)
        return 2
    run = os.path.abspath(argv[1])
    stage = argv[argv.index("--stage") + 1]
    snap_dir = argv[argv.index("--snapshot-dir") + 1] if "--snapshot-dir" in argv else os.path.join(run, "inputs")
    try:
        res = postrun.observed_claims(run, snap_dir, keys=(stage,))
    except (OSError, ValueError, KeyError) as e:
        print("cannot read the snapshot in %s: %s" % (snap_dir, e))
        return 2
    bad = {k: res[k] for k in PROBLEMS if res.get(k)}
    broken = res["files_missing"] + res.get("unreadable", [])
    print(json.dumps({"stage": stage, "checked": res["checked"], **bad, **({"files_not_checked": broken} if broken else {})}, indent=1))
    if res.get("no_citation"):
        print("FAIL: %d Observed claim(s) cite no source: %s. Cite the snapshot source that shows each one, or stamp it Assumed "
              "and keep its open issue." % (len(res["no_citation"]), ", ".join(res["no_citation"][:12])))
    if bad or broken:
        return 1
    print("PASS: %d Observed claims in %s cite a source" % (res["checked"], stage))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
