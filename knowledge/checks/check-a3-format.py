#!/usr/bin/env python3
"""Validate an A3 format instance against its v0.1 schema, standard library only (Pins §8).

    python3 check-a3-format.py --run RUN_ROOT --stage KEY --kind set-map|graph [--file PATH]

Without --file, the set map read is `stages/set-map/set-map.json` at stage `set-map` and
`inputs/set-map.json` at any other stage; the graph read is `stages/<stage>/<stage>.input.json`.
A relative --file is resolved against RUN_ROOT. Exit 0 pass, 1 fail, 2 setup error (including an
input whose values have the wrong JSON type, checked before any rule runs); stdout is
one JSON object {"check", "result", "findings", "flags"}.
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import _a3_format_graph  # noqa: E402
import _a3_format_setmap  # noqa: E402

CHECK = "a3-format"
SCHEMAS = {
    "set-map": os.path.join(HERE, "formats", "set-map-v0.1.schema.json"),
    "graph": os.path.join(HERE, "formats", "current-state-graph-v0.1.schema.json"),
}


def _emit(result, findings=(), flags=()):
    print(json.dumps({"check": CHECK, "result": result, "findings": list(findings), "flags": list(flags)}, indent=2))
    return {"pass": 0, "fail": 1, "setup-error": 2}[result]


def _setup(rule, where, detail):
    return _emit("setup-error", [{"rule": rule, "where": where, "detail": detail}])


def _default_file(kind, stage):
    if kind == "set-map":
        return os.path.join("stages", "set-map", "set-map.json") if stage == "set-map" else os.path.join("inputs", "set-map.json")
    return os.path.join("stages", stage, f"{stage}.input.json")


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise SystemExit(_setup("usage", "argv", message))


def main(argv=None):
    p = _Parser(description=__doc__.splitlines()[0])
    p.add_argument("--run", required=True)
    p.add_argument("--stage", required=True)
    p.add_argument("--kind", required=True, choices=sorted(SCHEMAS))
    p.add_argument("--file")
    p.add_argument("--part")
    a = p.parse_args(argv)

    if not os.path.isdir(a.run):
        return _setup("run-root", a.run, "RUN_ROOT is not a folder")
    rel = a.file or _default_file(a.kind, a.stage)
    path = rel if os.path.isabs(rel) else os.path.join(a.run, rel)
    try:
        with open(SCHEMAS[a.kind], encoding="utf-8") as f:
            schema = json.load(f)
    except (OSError, ValueError) as e:
        return _setup("schema", SCHEMAS[a.kind], f"cannot read the schema: {e}")
    try:
        with open(path, encoding="utf-8") as f:
            instance = json.load(f)
    except OSError as e:
        return _setup("input", rel, f"cannot read the input: {e}")
    except ValueError as e:
        return _emit("fail", [{"rule": "json", "where": rel, "detail": f"not valid JSON: {e}"}])

    module = _a3_format_setmap if a.kind == "set-map" else _a3_format_graph
    try:
        findings = module.validate(instance, schema)
    except _a3_format_setmap.SchemaError as e:
        return _setup("schema", SCHEMAS[a.kind], str(e))
    except _a3_format_setmap.InputShapeError as e:
        for f in e.findings:
            f["where"] = f"{rel}#{f['where']}"
        return _emit("setup-error", e.findings)
    except _a3_format_graph.NotBuilt as e:
        return _setup("not-built", a.kind, str(e))
    for f in findings:
        f["where"] = f"{rel}#{f['where']}"
    return _emit("fail" if findings else "pass", findings)


if __name__ == "__main__":
    raise SystemExit(main())
