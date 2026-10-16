"""A small standard-library JSON Schema reader for the keywords U2's run and list formats use.

Covers type, enum, const, required, properties, additionalProperties, propertyNames, items, minItems,
minLength, pattern, minimum, maximum, allOf, anyOf, oneOf, if/then/else and $ref (local `#/...` and
file-relative). Returns a list of problems; empty when the document fits.
"""
import json
import os
import re

TYPES = {
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
    "string": lambda v: isinstance(v, str),
    "boolean": lambda v: isinstance(v, bool),
    "null": lambda v: v is None,
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: (isinstance(v, (int, float)) and not isinstance(v, bool))
    or type(v).__name__ == "Decimal",
}


def _load(path, cache):
    path = os.path.normpath(path)
    if path not in cache:
        with open(path, encoding="utf-8") as fh:
            cache[path] = json.load(fh)
    return path, cache[path]


def _resolve(ref, base, cache):
    file_part, _, pointer = ref.partition("#")
    if file_part:
        base, _ = _load(os.path.join(os.path.dirname(base), file_part), cache)
    node = cache[base]
    for part in [p for p in pointer.split("/") if p]:
        node = node[part]
    return node, base


def _check(schema, value, where, base, cache, out):
    if schema is True or schema is None:
        return
    if schema is False:
        out.append("%s: not allowed" % where)
        return
    if "$ref" in schema:
        target, tbase = _resolve(schema["$ref"], base, cache)
        _check(target, value, where, tbase, cache, out)
    t = schema.get("type")
    if t is not None:
        names = t if isinstance(t, list) else [t]
        if not any(TYPES[n](value) for n in names):
            out.append("%s: expected %s" % (where, "/".join(names)))
            return
    if "enum" in schema and value not in schema["enum"]:
        out.append("%s: %r is not one of %r" % (where, value, schema["enum"]))
    if "const" in schema and value != schema["const"]:
        out.append("%s: must be %r" % (where, schema["const"]))
    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0):
            out.append("%s: too short" % where)
        if "pattern" in schema and not re.search(schema["pattern"], value):
            out.append("%s: does not match %s" % (where, schema["pattern"]))
    if TYPES["number"](value):
        if "minimum" in schema and value < schema["minimum"]:
            out.append("%s: below %s" % (where, schema["minimum"]))
        if "maximum" in schema and value > schema["maximum"]:
            out.append("%s: above %s" % (where, schema["maximum"]))
    if isinstance(value, dict):
        for key in schema.get("required", []):
            if key not in value:
                out.append("%s: missing %s" % (where, key))
        props = schema.get("properties", {})
        for key, sub in value.items():
            if "propertyNames" in schema:
                _check(schema["propertyNames"], key, "%s key %r" % (where, key), base, cache, out)
            if key in props:
                _check(props[key], sub, "%s.%s" % (where, key), base, cache, out)
            elif "additionalProperties" in schema:
                _check(schema["additionalProperties"], sub, "%s.%s" % (where, key), base, cache, out)
    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0):
            out.append("%s: fewer than %d items" % (where, schema["minItems"]))
        if "items" in schema:
            for i, sub in enumerate(value):
                _check(schema["items"], sub, "%s[%d]" % (where, i), base, cache, out)
    for sub in schema.get("allOf", []):
        _check(sub, value, where, base, cache, out)
    for key in ("anyOf", "oneOf"):
        if key in schema:
            hits = sum(1 for sub in schema[key] if not _problems(sub, value, where, base, cache))
            if hits == 0 or (key == "oneOf" and hits > 1):
                out.append("%s: does not fit %s" % (where, key))
    if "if" in schema:
        branch = "then" if not _problems(schema["if"], value, where, base, cache) else "else"
        if branch in schema:
            _check(schema[branch], value, where, base, cache, out)


def _problems(schema, value, where, base, cache):
    out = []
    _check(schema, value, where, base, cache, out)
    return out


def problems(schema_path, value):
    """Problems with `value` against the schema file at `schema_path`."""
    cache = {}
    base, schema = _load(schema_path, cache)
    return _problems(schema, value, "$", base, cache)
