"""Set map v0.1 validator, standard library only (C1b1).

`validate(instance, schema)` returns findings `{"rule", "where", "detail"}`. It first checks the
input's shape (every JSON type the schema declares unconditionally); input outside that shape raises
InputShapeError (setup error, exit 2) before any rule runs. Then two layers:
  1. schema: a small Draft 2020-12 interpreter covering exactly the keywords the set-map v0.1
     schema uses (an unknown keyword is a setup error, so the schema cannot grow past it silently);
  2. stdlib-only rules the schema does not express (STDLIB_ONLY lists each with its reason).
Format is checked by the stdlib-only `format-date` rule, not by the interpreter, because
`jsonschema` treats `format` as an annotation unless a format checker is passed.
"""
import datetime
import re

SCHEMA_REL = "formats/set-map-v0.1.schema.json"

# Keywords that only annotate; they never reject.
ANNOTATIONS = {"$schema", "$id", "title", "description", "$defs", "format", "examples", "default", "$comment"}
ASSERTIONS = {"type", "$ref", "properties", "required", "additionalProperties", "items", "enum", "const",
              "pattern", "minLength", "minItems", "minimum", "allOf", "anyOf", "not", "if", "then", "else"}

STDLIB_ONLY = {
    "format-date": "jsonschema (Draft 2020-12) treats `format: date` as an annotation unless a format "
                   "checker is passed, so it accepts any string there; the stdlib validator requires a real "
                   "calendar date.",
    "link-endpoint-resolves": "a link's `from`/`to` must name a process of the set map or a context "
                              "neighbour; JSON Schema cannot compare values across arrays.",
    "open-point-id-resolves": "a link's `open_point_ids` must name an `open_points[]` id; JSON Schema "
                              "cannot compare values across arrays.",
    "duplicate-id": "ids across work_objects, object_states, links, context_neighbours, open_points and "
                    "actors must be unique; `uniqueItems` cannot compare one field across objects.",
}


class SchemaError(Exception):
    """The schema uses something this interpreter does not cover (setup error, exit 2)."""


class InputShapeError(Exception):
    """The input has a value of the wrong JSON type (setup error, exit 2); `findings` says where."""

    def __init__(self, findings):
        super().__init__(f"{len(findings)} value(s) of the wrong JSON type")
        self.findings = findings


def _type_ok(value, t):
    if t == "object":
        return isinstance(value, dict)
    if t == "array":
        return isinstance(value, list)
    if t == "string":
        return isinstance(value, str)
    if t == "boolean":
        return isinstance(value, bool)
    if t == "null":
        return value is None
    if t == "integer":
        return (isinstance(value, int) and not isinstance(value, bool)) or (isinstance(value, float) and value.is_integer())
    if t == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    raise SchemaError(f"unknown type {t!r}")


def _equal(a, b):
    # JSON equality: booleans are not numbers.
    if isinstance(a, bool) or isinstance(b, bool):
        return isinstance(a, bool) and isinstance(b, bool) and a == b
    return a == b


def _where(path):
    return "/" + "/".join(str(p) for p in path)


class _Interp:
    def __init__(self, root):
        self.root = root

    def resolve(self, ref):
        if not ref.startswith("#/"):
            raise SchemaError(f"only local $ref is supported: {ref}")
        node = self.root
        for part in ref[2:].split("/"):
            part = part.replace("~1", "/").replace("~0", "~")
            if not isinstance(node, dict) or part not in node:
                raise SchemaError(f"unresolvable $ref {ref}")
            node = node[part]
        return node

    def errors(self, schema, value, path):
        if schema is True:
            return []
        if schema is False:
            return [("schema", path, "no value is allowed here")]
        unknown = set(schema) - ANNOTATIONS - ASSERTIONS
        if unknown:
            raise SchemaError(f"unsupported schema keyword(s) {sorted(unknown)} at {_where(path)}")
        out = []
        if "$ref" in schema:
            out += self.errors(self.resolve(schema["$ref"]), value, path)
        if "type" in schema:
            types = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
            if not any(_type_ok(value, t) for t in types):
                return out + [("schema", path, f"expected type {'/'.join(types)}")]
        if "enum" in schema and not any(_equal(value, e) for e in schema["enum"]):
            out.append(("schema", path, f"{value!r} is not one of {schema['enum']}"))
        if "const" in schema and not _equal(value, schema["const"]):
            out.append(("schema", path, f"must be {schema['const']!r}"))
        if isinstance(value, str):
            if "pattern" in schema and not re.search(schema["pattern"], value):
                out.append(("schema", path, f"{value!r} does not match {schema['pattern']}"))
            if "minLength" in schema and len(value) < schema["minLength"]:
                out.append(("schema", path, f"shorter than {schema['minLength']}"))
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if "minimum" in schema and value < schema["minimum"]:
                out.append(("schema", path, f"{value} is below {schema['minimum']}"))
        if isinstance(value, list):
            if "minItems" in schema and len(value) < schema["minItems"]:
                out.append(("schema", path, f"fewer than {schema['minItems']} item(s)"))
            if "items" in schema:
                for i, item in enumerate(value):
                    out += self.errors(schema["items"], item, path + [i])
        if isinstance(value, dict):
            for key in schema.get("required", []):
                if key not in value:
                    out.append(("schema", path, f"missing required key {key!r}"))
            props = schema.get("properties", {})
            for key, sub in props.items():
                if key in value:
                    out += self.errors(sub, value[key], path + [key])
            extra = schema.get("additionalProperties", True)
            if extra is not True:
                for key in value:
                    if key not in props:
                        out += self.errors(extra, value[key], path + [key])
        for sub in schema.get("allOf", []):
            out += self.errors(sub, value, path)
        if "anyOf" in schema and not any(not self.errors(s, value, path) for s in schema["anyOf"]):
            out.append(("schema", path, "matches none of the allowed shapes"))
        if "not" in schema and not self.errors(schema["not"], value, path):
            out.append(("schema", path, "matches a disallowed shape"))
        if "if" in schema:
            branch = "then" if not self.errors(schema["if"], value, path) else "else"
            if branch in schema:
                out += self.errors(schema[branch], value, path)
        return out


def _shape_errors(interp, schema, value, path):
    # The structure: $ref, type, properties, additionalProperties, items, allOf, and anyOf as a union
    # (the value must have the shape of at least one branch). not / if-then-else are rules, not shape.
    if not isinstance(schema, dict):
        return []
    out = []
    if "$ref" in schema:
        out += _shape_errors(interp, interp.resolve(schema["$ref"]), value, path)
    if "type" in schema:
        types = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
        if not any(_type_ok(value, t) for t in types):
            return out + [("input-shape", path, f"expected JSON type {'/'.join(types)}, got {type(value).__name__}")]
    if isinstance(value, list) and "items" in schema:
        for i, item in enumerate(value):
            out += _shape_errors(interp, schema["items"], item, path + [i])
    if isinstance(value, dict):
        props = schema.get("properties", {})
        for key, sub in props.items():
            if key in value:
                out += _shape_errors(interp, sub, value[key], path + [key])
        extra = schema.get("additionalProperties", True)
        if isinstance(extra, dict):
            for key in value:
                if key not in props:
                    out += _shape_errors(interp, extra, value[key], path + [key])
    for sub in schema.get("allOf", []):
        out += _shape_errors(interp, sub, value, path)
    if "anyOf" in schema and all(_shape_errors(interp, sub, value, path) for sub in schema["anyOf"]):
        out.append(("input-shape", path, f"no allowed JSON type matches a {type(value).__name__}"))
    return out


def _date_ok(s):
    if not isinstance(s, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", s):
        return False
    try:
        datetime.date.fromisoformat(s)
    except ValueError:
        return False
    return True


def _stdlib_rules(inst):
    out = []
    if not isinstance(inst, dict):
        return out

    def items(key):
        v = inst.get(key)
        return [x for x in v if isinstance(x, dict)] if isinstance(v, list) else []

    if "read_at" in inst and isinstance(inst["read_at"], str) and not _date_ok(inst["read_at"]):
        out.append(("format-date", ["read_at"], f"{inst['read_at']!r} is not a calendar date"))
    for key in ("object_states", "links", "context_neighbours", "actors"):
        for i, x in enumerate(items(key)):
            refs = x.get("evidence_refs")
            for j, r in enumerate(refs if isinstance(refs, list) else []):
                if isinstance(r, dict) and isinstance(r.get("read_at"), str) and not _date_ok(r["read_at"]):
                    out.append(("format-date", [key, i, "evidence_refs", j, "read_at"],
                                f"{r['read_at']!r} is not a calendar date"))

    seen = {}
    for key in ("work_objects", "object_states", "links", "context_neighbours", "open_points", "actors"):
        for i, x in enumerate(items(key)):
            xid = x.get("id")
            if isinstance(xid, str):
                if xid in seen:
                    out.append(("duplicate-id", [key, i, "id"], f"{xid!r} is also used at {_where(seen[xid])}"))
                else:
                    seen[xid] = [key, i, "id"]

    procs = {p.get("process_id") for p in items("processes")}
    procs |= {c.get("process_id") for c in items("context_neighbours") if c.get("process_id")}
    ops = {o.get("id") for o in items("open_points")}
    for i, link in enumerate(items("links")):
        for end in ("from", "to"):
            ends = link.get(end)
            for j, pid in enumerate(ends if isinstance(ends, list) else []):
                if pid not in procs:
                    out.append(("link-endpoint-resolves", ["links", i, end, j],
                                f"{pid!r} is not a process or context neighbour of this set map"))
        opids = link.get("open_point_ids")
        for j, oid in enumerate(opids if isinstance(opids, list) else []):
            if oid not in ops:
                out.append(("open-point-id-resolves", ["links", i, "open_point_ids", j],
                            f"{oid!r} is not an open point of this set map"))
    return out


def validate(instance, schema):
    """Return findings for a set map instance; raises SchemaError on an unsupported schema and
    InputShapeError on input of the wrong shape (no rule runs on it)."""
    interp = _Interp(schema)
    schema_raw = interp.errors(schema, instance, [])  # type-guarded; raises SchemaError first
    shape = _shape_errors(interp, schema, instance, [])
    if shape:
        raise InputShapeError([{"rule": r, "where": _where(p), "detail": d} for r, p, d in shape])
    raw = schema_raw + _stdlib_rules(instance)
    return [{"rule": rule, "where": _where(path), "detail": detail} for rule, path, detail in raw]
