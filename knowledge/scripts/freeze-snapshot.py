#!/usr/bin/env python3
"""Validate preserved raw pulls and freeze a deterministic snapshot; no LLM call.

Usage: freeze-snapshot.py INTAKE.json OUTPUT.json. Output must not already exist.
Paths in the source inventory remain relative to the hash-bound intake file.
"""
import argparse
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "checks"))
from workflow_contract import SNAPSHOT_VERSION, intake, sha


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("intake")
    parser.add_argument("output")
    args = parser.parse_args()
    try:
        doc, steps, profiles, systems = intake(args.intake)
        result = {"schema_version": SNAPSHOT_VERSION, "kind": "snapshot",
                  "intake": {"path": os.path.relpath(Path(args.intake).resolve(), Path(args.output).resolve().parent), "sha256": sha(args.intake)},
                  "steps": steps, "profiles": profiles, "systems": systems,
                  **{key: doc[key] for key in ("scope", "required_source_ids", "sources", "issues")},
                  # The intake's worked-reference record (SKILL.md Step 1) travels unchanged.
                  **({"reference_use": doc["reference_use"]} if "reference_use" in doc else {})}
        with open(args.output, "x", encoding="utf-8") as f:
            f.write(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
        print("Snapshot frozen: %s (%d sources, %d steps)" % (args.output, len(doc["sources"]), len(steps)))
    except (ValueError, KeyError, TypeError, IndexError, OSError) as exc:
        parser.exit(1, "Snapshot refused: %s\n" % exc)


if __name__ == "__main__":
    main()
