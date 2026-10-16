#!/usr/bin/env python3
"""Check that HTML pages carry no JavaScript: no <script> tag, no inline event handler, no javascript: URL.

Usage: check-no-script.py PATH [PATH ...]

Each PATH is an .html/.htm file or a folder (searched for .html/.htm files). Paths listed in
no-script-exemptions.json (next to this file, relative to the plugin root, each with a reason) are skipped and
named as skipped. Prints PASS no-script (N pages), or FAIL no-script: <page>: <finding> per finding on stderr.
Exit 0 pass, 1 fail, 2 usage. The rules are in _no_script.py.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from _no_script import problems  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(HERE))
EXEMPTIONS = os.path.join(HERE, "no-script-exemptions.json")


def exemptions():
    """{absolute path: reason} for every exempt file or folder."""
    with open(EXEMPTIONS, encoding="utf-8") as f:
        doc = json.load(f)
    out = {}
    for item in doc["exempt"]:
        if not item.get("reason", "").strip():
            raise ValueError("exemption %s has no reason" % item.get("path"))
        out[os.path.normpath(os.path.join(ROOT, item["path"]))] = item["reason"]
    return out


def exempt_reason(path, exempt):
    path = os.path.normpath(os.path.abspath(path))
    for base, reason in exempt.items():
        if path == base or path.startswith(base + os.sep):
            return reason
    return None


def pages(paths):
    for p in paths:
        if os.path.isdir(p):
            for d, dirs, files in os.walk(p):
                dirs[:] = sorted(x for x in dirs if x not in (".git", "node_modules"))
                for name in sorted(files):
                    if name.lower().endswith((".html", ".htm")):
                        yield os.path.join(d, name)
        else:
            yield p


def main(argv):
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__.strip(), file=sys.stderr)
        return 2
    exempt = exemptions()
    checked, failed = 0, 0
    for page in pages(argv):
        if not os.path.isfile(page):
            print("FAIL no-script: %s: not found" % page, file=sys.stderr)
            failed += 1
            continue
        reason = exempt_reason(page, exempt)
        if reason:
            print("SKIP no-script: %s (%s)" % (page, reason))
            continue
        checked += 1
        with open(page, encoding="utf-8", errors="replace") as f:
            found = problems(f.read())
        for line, kind, detail in found:
            print("FAIL no-script: %s:%d: %s %s" % (page, line, kind, detail), file=sys.stderr)
        failed += bool(found)
    if failed:
        return 1
    print("PASS no-script (%d pages)" % checked)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
