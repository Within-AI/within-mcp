"""Current-state / future-state graph v0.1 validator, standard library only (C1b2).

`validate(instance, schema)` returns findings `{"rule", "where", "detail"}`, the same way as the set-map
validator (C1b1), whose interpreter and input-shape check it reuses. Input outside the schema's shape
raises InputShapeError (setup error, exit 2) before any rule runs. Then two layers:
  1. schema: the set-map Draft 2020-12 interpreter, extended with the keywords only the graph schema
     uses (`maxLength`, `contains`, `minContains`, `maxContains`); an unknown keyword is a setup error;
  2. stdlib-only rules the schema does not express (STDLIB_ONLY lists each with its reason).
"""
from _a3_format_setmap import (  # noqa: F401 (the dispatcher catches these through the set-map module)
    ANNOTATIONS, ASSERTIONS, InputShapeError, SchemaError, _Interp, _shape_errors, _where,
)

SCHEMA_REL = "formats/current-state-graph-v0.1.schema.json"

EXTRA = {"maxLength", "contains", "minContains", "maxContains"}

WORK_TYPES = {"read_interaction", "write_interaction", "decision", "ai_use", "message"}

STDLIB_ONLY = {
    "duplicate-node-id": "node ids must be unique; `uniqueItems` cannot compare one field across objects.",
    "node-lane-resolves": "a node's `lane_id` must name a lane of the same graph; JSON Schema cannot compare "
                          "values across arrays.",
    "edge-endpoint-resolves": "an edge's `source`/`target` must name a node of the same graph; JSON Schema "
                              "cannot compare values across arrays.",
    "work-node-frame-resolves": "a work node's `process_id` must name a `process_frames[]` entry (or `set`); "
                                "JSON Schema cannot compare values across arrays.",
    "ai-use-frame-agrees": "an AI use's `ai_use.process_id`, when both are given, must equal the node's `process_id`; "
                           "JSON Schema cannot compare two values of one instance.",
}


class NotBuilt(Exception):
    """Kept so the dispatcher's `except _a3_format_graph.NotBuilt` still resolves; never raised now."""


class _GraphInterp(_Interp):
    def errors(self, schema, value, path):
        if not isinstance(schema, dict):
            return super().errors(schema, value, path)
        unknown = set(schema) - ANNOTATIONS - ASSERTIONS - EXTRA
        if unknown:
            raise SchemaError(f"unsupported schema keyword(s) {sorted(unknown)} at {_where(path)}")
        out = super().errors({k: v for k, v in schema.items() if k not in EXTRA}, value, path)
        if isinstance(value, str) and "maxLength" in schema and len(value) > schema["maxLength"]:
            out.append(("schema", path, f"longer than {schema['maxLength']} characters"))
        if isinstance(value, list) and "contains" in schema:
            n = sum(1 for i, item in enumerate(value) if not self.errors(schema["contains"], item, path + [i]))
            lo, hi = schema.get("minContains", 1), schema.get("maxContains")
            if n < lo:
                out.append(("schema", path, f"{n} item(s) match `contains`, fewer than {lo}"))
            if hi is not None and n > hi:
                out.append(("schema", path, f"{n} item(s) match `contains`, more than {hi}"))
        elif ("minContains" in schema or "maxContains" in schema) and "contains" not in schema:
            raise SchemaError(f"minContains/maxContains without contains at {_where(path)}")
        return out


def _stdlib_rules(inst):
    out = []
    g = inst.get("graph") if isinstance(inst, dict) else None
    if not isinstance(g, dict):
        return out

    def items(key):
        v = g.get(key)
        return [(i, x) for i, x in enumerate(v) if isinstance(x, dict)] if isinstance(v, list) else []

    lanes = {l.get("id") for _, l in items("lanes")}
    frames = {f.get("process_id") for _, f in items("process_frames")} | {"set"}
    seen = {}
    for i, n in items("nodes"):
        nid = n.get("id")
        if isinstance(nid, str):
            if nid in seen:
                out.append(("duplicate-node-id", ["graph", "nodes", i, "id"], f"{nid!r} is also node {seen[nid]}"))
            else:
                seen[nid] = i
        if "lane_id" in n and n["lane_id"] not in lanes:
            out.append(("node-lane-resolves", ["graph", "nodes", i, "lane_id"], f"{n['lane_id']!r} is not a lane of this graph"))
        if n.get("type") in WORK_TYPES and "process_id" in n and n["process_id"] not in frames:
            out.append(("work-node-frame-resolves", ["graph", "nodes", i, "process_id"],
                        f"{n['process_id']!r} is not a process frame of this graph"))
        ai = n.get("ai_use")
        if (n.get("type") == "ai_use" and isinstance(ai, dict) and "process_id" in ai and "process_id" in n
                and ai["process_id"] != n["process_id"]):
            out.append(("ai-use-frame-agrees", ["graph", "nodes", i, "ai_use", "process_id"],
                        f"{ai['process_id']!r} differs from the node's process_id {n.get('process_id')!r}"))
    for i, e in items("edges"):
        for end in ("source", "target"):
            if end in e and e[end] not in seen:
                out.append(("edge-endpoint-resolves", ["graph", "edges", i, end], f"{e[end]!r} is not a node of this graph"))
    return out


def validate(instance, schema):
    """Return findings for a graph instance; raises SchemaError on an unsupported schema and
    InputShapeError on input of the wrong shape (no rule runs on it)."""
    interp = _GraphInterp(schema)
    schema_raw = interp.errors(schema, instance, [])  # type-guarded; raises SchemaError first
    shape = _shape_errors(interp, schema, instance, [])
    if shape:
        raise InputShapeError([{"rule": r, "where": _where(p), "detail": d} for r, p, d in shape])
    raw = schema_raw + _stdlib_rules(instance)
    return [{"rule": rule, "where": _where(path), "detail": detail} for rule, path, detail in raw]
