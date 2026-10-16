#!/usr/bin/env python3
# This file is Python despite its .sh name: run it directly or as `python3 <this file>`; `bash <this file>` cannot run it.
"""Reference-use record check: the output says which worked reference it followed, and why.

Usage (run with the run root as the working directory):
    check-reference-use-record.sh --stage KEY ARTIFACT

The record lives in the stage's submitted artifact, encoded by file type:
  .json          top-level key `reference_use`
  .yaml / .yml   one line at column 0: `reference_use: {...single-line JSON...}`
  .md            `<!-- REFERENCE-USE {...JSON...} -->`

For a stage whose references are `available` the record must exist and be complete: it lists every
reference id in `considered`, names each followed reference with its level, staged primary path and
sha256 (all four copied exactly), gives a specific reason, and states any divergence with its reason.
Every problem is printed before the check exits. For a stage with no registered reference the check
passes when there is no record and fails when a record is present.

Exit: 0 pass; 1 assertion failure; 2 setup error (unknown stage, missing or unsupported artifact,
registry drift).
"""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import _reference_registry as rr  # noqa: E402


def fail(lines):
    print("FAIL reference-use-record:")
    for line in lines:
        print("  - " + line)
    return 1


def main(argv):
    if len(argv) != 3 or argv[0] != "--stage":
        sys.stderr.write("usage: check-reference-use-record.sh --stage KEY ARTIFACT\n")
        return 2
    stage, artifact = argv[1], argv[2]
    try:
        reg = rr.load()
    except (OSError, ValueError) as exc:
        sys.stderr.write("registry unreadable: %s\n" % exc)
        return 2
    st = rr.state(stage, reg)
    if st is None:
        sys.stderr.write("unknown stage key %r (not in the reference registry)\n" % stage)
        return 2
    if not Path(artifact).is_file():
        sys.stderr.write("missing artifact: %s\n" % artifact)
        return 2
    if rr.carrier_encoding(artifact) is None:
        sys.stderr.write("unsupported carrier extension for %s (use .json, .yaml, .yml or .md)\n" % artifact)
        return 2
    records, problems = rr.extract_records(artifact)
    if problems:
        return fail(problems)
    if len(records) > 1:
        return fail(["the artifact carries %d reference records; exactly one is allowed" % len(records)])
    if st != "available":
        if records:
            return fail(["record cites a reference that is not registered for this stage"])
        print("PASS reference-use-record: no reference registered for this stage; no record expected")
        return 0
    try:
        rr.verify_plugin_files(stage, reg)
    except rr.RegistryStale as exc:
        sys.stderr.write("%s\n" % exc)
        return 2
    if not records:
        return fail(["no reference record in %s (add the reference_use record the packet describes)" % artifact])
    problems = rr.record_problems(records[0], stage, reg, run_root=".")
    if problems:
        return fail(problems)
    followed = ", ".join(item["reference_id"] for item in records[0]["followed"])
    print("PASS reference-use-record: followed %s" % followed)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
