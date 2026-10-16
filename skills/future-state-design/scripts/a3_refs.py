#!/usr/bin/env python3
"""A3 reference hook (Pins §1): stage the v0.1 formats a multi-process stage follows, and name them in its prompt.

stage(key, run) -> list[str]. For a multi-process run (hooks_checks.is_multi: the snapshot names two or more
processes) it copies the two v0.1 schemas and the three fictional examples from the knowledge root's
checks/formats/ into RUN_ROOT/references/<key>/a3/ and returns their run-relative paths, in FILES order; fsd.py
adds them to the stage prompt. A single-process run gets [] and nothing is written. A missing source file raises
A3RefsError (nothing is filled in). Copies are byte-for-byte; an existing copy is replaced. Stdlib only.
"""
import os, shutil, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import knowledge  # noqa: E402
import hooks_checks  # noqa: E402

STAGES = ("set-map", "current-state", "transformation-plan", "future-state")
FILES = ("set-map-v0.1.schema.json", "current-state-graph-v0.1.schema.json",
         "examples/set-map.example.json", "examples/current-state.example.json", "examples/future-state.example.json")


class A3RefsError(Exception):
    """A v0.1 schema or example the stage needs is missing from the knowledge root."""


def source(run):
    return os.path.join(knowledge.path("checks", run), "formats")


def stage(key, run):
    if key not in STAGES or not hooks_checks.is_multi(run):
        return []
    src = source(run)
    missing = [f for f in FILES if not os.path.isfile(os.path.join(src, f))]
    if missing:
        raise A3RefsError("refused: A3 formats missing under %s: %s" % (src, ", ".join(missing)))
    out = []
    for f in FILES:
        rel = os.path.join("references", key, "a3", f)
        dest = os.path.join(run, rel)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        shutil.copyfile(os.path.join(src, f), dest)
        out.append(rel)
    return out
