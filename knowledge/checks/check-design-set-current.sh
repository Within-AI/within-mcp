#!/usr/bin/env python3
# This file is Python despite its .sh name: run it directly or as `python3 <this file>`; `bash <this file>` cannot run it.
"""Design-set currency check — one of the design-coherence gate's three checks.

Plain-language: "Is every design artifact the gate pinned still exactly the file it pinned?"
The `check-design-set-current` internal tag is a footnote; the plain-language question is the handle.

The design-coherence gate closes stage 10 and is only valid while every design artifact it pinned is unchanged
on disk. Its design set (`output/coherence/design-set.json`, written by scripts/coherence-design-set.py) pins
each file by run-relative path and sha256. This check re-hashes every pinned file. It judges nothing about the
design itself; it only says whether the pinned files are still the pinned bytes. The writer re-runs it when a
stage that requires the gate clears or starts, so a design change after the gate passed makes the gate stale.

Run with the run root as the working directory (the writer re-verifies checks from the run root).

Usage: check-design-set-current.sh DESIGN_SET
Exit: 0 every pinned file exists with its pinned sha; 1 one or more changed or missing files (each listed);
      2 setup error (bad args, missing or malformed design set).
"""
import hashlib
import json
import os
import sys


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def die(msg, code):
    sys.stderr.write(msg + "\n")
    sys.exit(code)


def main(argv):
    if len(argv) != 1:
        die("usage: check-design-set-current.sh DESIGN_SET", 2)
    path = argv[0]
    if not os.path.isfile(path):
        die("Missing design set: %s" % path, 2)
    try:
        doc = json.load(open(path, encoding="utf-8"))
    except (OSError, ValueError) as exc:
        die("design set %s is not valid JSON: %s" % (path, exc), 2)
    if not isinstance(doc, dict) or doc.get("schema") != "design-set/v1" or not isinstance(doc.get("stages"), dict):
        die("design set %s is not a design-set/v1 document ({schema: design-set/v1, stages: {...}})" % path, 2)
    pinned, changed = 0, []
    for key in sorted(doc["stages"]):
        entry = doc["stages"][key]
        if not isinstance(entry, dict) or not isinstance(entry.get("files"), list):
            die("design set stage %r carries no files list" % key, 2)
        for f in entry["files"]:
            if not isinstance(f, dict) or not f.get("path") or not f.get("sha256"):
                die("design set stage %r has a file entry without path and sha256: %r" % (key, f), 2)
            pinned += 1
            if os.path.isabs(f["path"]):
                changed.append("%s: %s is an absolute path; design-set paths are run-relative" % (key, f["path"]))
            elif not os.path.isfile(f["path"]):
                changed.append("%s: %s is missing (pinned %s)" % (key, f["path"], f["sha256"][:12]))
            elif sha256_of(f["path"]) != f["sha256"]:
                changed.append("%s: %s changed (pinned %s, now %s)"
                               % (key, f["path"], f["sha256"][:12], sha256_of(f["path"])[:12]))
    if changed:
        die("design set is not current — %d of %d pinned files changed:\n  - %s"
            % (len(changed), pinned, "\n  - ".join(changed)), 1)
    print("PASS design-set-current: %d pinned files across %d stages are unchanged" % (pinned, len(doc["stages"])))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
