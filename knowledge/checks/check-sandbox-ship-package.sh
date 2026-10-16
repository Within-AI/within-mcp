#!/usr/bin/env python3
# This file is Python despite its .sh name: run it directly or as `python3 <this file>`; `bash <this file>` cannot run it.
"""Sandbox-ship package check (stage 12) -- the package on disk is exactly what the spec produces.

Plain-language handle: "check the package is the one the spec describes". Re-derives the package from the spec
(package.files, the templates, the source bytes) and compares it with the package folder: every listed file
present with its source's bytes, nothing unlisted present, and the Dockerfile, .dockerignore and package.lock.json
identical to what the spec and templates produce (so a hand-edited package file, a stray file, or a changed source
fails). When the spec gives package.build, the package's own Dockerfiles and compose file are listed files: they
are checked as the builder checks them, and a generated Dockerfile in the folder counts as an unlisted file. Run
with the run root as the working directory. Docker is not used.

Usage: check-sandbox-ship-package.sh [SPEC]     (default: output/sandbox-ship/spec.yaml)
Exit: 0 pass; 1 findings; 2 setup error (the spec cannot be read, or it is refused as a spec).
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "scripts"))
import _sandbox_ship_build as ssb  # noqa: E402

DEFAULT_SPEC = "output/sandbox-ship/spec.yaml"


def main(argv):
    args = [a for a in argv if not a.startswith("--")]
    if len(args) > 1:
        sys.stderr.write("usage: check-sandbox-ship-package.sh [SPEC]\n")
        return 2
    root = os.getcwd()
    spec_path = args[0] if args else DEFAULT_SPEC
    try:
        spec = ssb.load_spec(os.path.join(root, spec_path))
        problems = ssb.verify(spec, root)
    except ssb.BuildError as exc:
        sys.stderr.write("sandbox-ship-package: %s\n" % exc)
        return 2
    if problems:
        sys.stderr.write("sandbox-ship package check FAILED for %s:\n  - %s\n" % (spec_path, "\n  - ".join(problems)))
        return 1
    print("PASS sandbox-ship-package: the package folder matches the spec; nothing unlisted is in it.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
