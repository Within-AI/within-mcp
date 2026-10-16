#!/usr/bin/env python3
# This file is Python despite its .sh name: run it directly or as `python3 <this file>`; `bash <this file>` cannot run it.
"""Cross-client scan (stage 12, sandbox-ship) -- flag strings in the package that may name another client.

Plain-language handle: "check nothing in the package names or points at a client other than this run's". Runs in
every mode; it reads no mode setting. Run with the run root as the working directory. The rules live in
checks/_sandbox_ship_leak.py.

Usage: check-sandbox-ship-leak-scan.sh --spec SPEC [--resolution FILE]
  SPEC        the sandbox-ship-spec/v1 file (client, package.files[])
  --resolution  the leak-resolution/v1 record (default output/sandbox-ship/leak-resolution.yaml)
Exit: 0 pass (no flags, or every flag resolved against the current package); 1 unresolved or stale flags;
2 setup error (spec, package file or resolution record cannot be read).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _sandbox_ship_leak as leak  # noqa: E402


def parse(argv):
    spec = resolution = None
    it = iter(argv)
    for a in it:
        if a == "--spec":
            spec = next(it, None)
        elif a == "--resolution":
            resolution = next(it, None)
        else:
            return None, None
    return spec, resolution


def main(argv):
    spec_path, resolution = parse(argv)
    if not spec_path:
        sys.stderr.write("usage: check-sandbox-ship-leak-scan.sh --spec SPEC [--resolution FILE]\n")
        return 2
    root = os.getcwd()
    resolution = resolution or leak.DEFAULT_RESOLUTION
    try:
        spec = leak.read_spec(spec_path)
        spec["client"] = leak.snapshot_client(root, spec)
        flags, notes = leak.scan_package(root, spec)
        pkg_hash = leak.package_sha256(root, spec["files"], spec["package_root"], spec["build"])
        entries = leak.load_resolutions(resolution if os.path.isabs(resolution) else os.path.join(root, resolution))
    except leak.LeakSetupError as exc:
        sys.stderr.write("sandbox-ship-leak-scan: %s\n" % exc)
        return 2
    for n in notes:
        print("note: %s" % n)
    problems = leak.unresolved(flags, entries, pkg_hash, resolution)
    if problems:
        sys.stderr.write("cross-client scan BLOCKED (package %s):\n  - %s\n" % (pkg_hash[:12], "\n  - ".join(problems)))
        return 1
    print("PASS sandbox-ship-leak-scan: %d flag(s), all resolved against package %s." % (len(flags), pkg_hash[:12]))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
