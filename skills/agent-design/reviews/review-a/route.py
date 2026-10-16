#!/usr/bin/env python3
"""Review A: list its inputs and route its verdict to the architecture stage once per architecture revision.

review_inputs(run_dir) -> list[str]  run-folder-relative paths the review may read: the agent design, its check
                                     report, each process's future state and transformation map, the set map (when
                                     the intake names one), the snapshot intake and the observation source files it
                                     lists.
revision(run_dir) -> str             sha256 over agent.yaml, the set map (if any) and the sorted observation source
                                     files with their sha256; any change makes a new revision.
route(run_dir) -> dict               consume stages/review-a/verdict.json, write stages/review-a/findings.json then
                                     routed.json, both keyed to the revision read. A new revision (after an
                                     architecture rerun) is routed again; with no findings nothing is routed
                                     (no route_to). ad.py next consumes findings.json.
CLI: route.py --run RUN  (exit 0 routed, 2 refused).
Standard library only; schemas are U1's, validated by a small subset validator.
"""
import argparse
import hashlib
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.normpath(os.path.join(HERE, "..", "..", "scripts")))
import evidence_ids  # noqa: E402
LISTS = os.path.normpath(os.path.join(HERE, "..", "..", "formats", "lists"))
CHECK_REPORT_SCHEMA = os.path.join(LISTS, "check-report.schema.json")
VERDICT_SCHEMA = os.path.join(LISTS, "verdict.schema.json")

AGENT = "stages/architecture/agent.yaml"
ARCH_REPORT = "checks/check_architecture.json"
INTAKE = "stages/intake/intake.json"
VERDICT = "stages/review-a/verdict.json"
FINDINGS = "stages/review-a/findings.json"
ROUTED = "stages/review-a/routed.json"
READING = "stages/review-a/reading.json"  # the revision a Review A run read, recorded when it starts
ROUTE_TO = "architecture"


class Refused(Exception):
    """Review A cannot run or route; the message says why in plain words."""


# --- minimal JSON Schema subset (the keywords U1's two list schemas use) ---

_TYPES = {
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
    "string": lambda v: isinstance(v, str),
    "integer": lambda v: (isinstance(v, int) and not isinstance(v, bool))
    or (isinstance(v, float) and v.is_integer()),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "null": lambda v: v is None,
}

# Keywords this validator enforces, and keywords that only annotate. Any other
# keyword in a schema is reported as an error, so the gate fails closed when
# U1's schemas grow a keyword this subset does not understand.
_CHECKED = {"type", "const", "enum", "minLength", "pattern", "minItems", "items",
            "required", "properties", "additionalProperties", "if", "then", "allOf"}
_ANNOTATIONS = {"$id", "$schema", "$comment", "title", "description", "version",
                "default", "examples"}


def _is_number(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def json_equal(a, b):
    """JSON Schema equality: a boolean never equals a number, at any depth."""
    if isinstance(a, bool) or isinstance(b, bool):
        return isinstance(a, bool) and isinstance(b, bool) and a == b
    if _is_number(a) or _is_number(b):
        return _is_number(a) and _is_number(b) and a == b
    if isinstance(a, list) or isinstance(b, list):
        return (isinstance(a, list) and isinstance(b, list) and len(a) == len(b)
                and all(json_equal(x, y) for x, y in zip(a, b)))
    if isinstance(a, dict) or isinstance(b, dict):
        return (isinstance(a, dict) and isinstance(b, dict) and set(a) == set(b)
                and all(json_equal(a[k], b[k]) for k in a))
    return type(a) is type(b) and a == b


def schema_errors(value, schema, where="$"):
    """Return a list of 'where: problem' strings; empty when value is valid."""
    if not isinstance(schema, dict):
        return ["%s: schema is not an object" % where]
    unknown = sorted(k for k in schema if k not in _CHECKED and k not in _ANNOTATIONS)
    if unknown:
        return ["%s: schema keyword %s is not supported by this validator" % (where, ", ".join(unknown))]
    errs = []
    t = schema.get("type")
    if t is not None:
        types = t if isinstance(t, list) else [t]
        if any(x not in _TYPES for x in types):
            return ["%s: schema type %r is not supported" % (where, t)]
        if not any(_TYPES[x](value) for x in types):
            return ["%s: type should be %s" % (where, "/".join(types))]
    if "const" in schema and not json_equal(value, schema["const"]):
        errs.append("%s: const should be %r" % (where, schema["const"]))
    if "enum" in schema and not any(json_equal(value, e) for e in schema["enum"]):
        errs.append("%s: enum should be one of %r" % (where, schema["enum"]))
    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0):
            errs.append("%s: minLength %d" % (where, schema["minLength"]))
        if "pattern" in schema and not re.search(schema["pattern"], value):
            errs.append("%s: pattern not matched" % where)
    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0):
            errs.append("%s: minItems %d" % (where, schema["minItems"]))
        if isinstance(schema.get("items"), dict):
            for i, item in enumerate(value):
                errs += schema_errors(item, schema["items"], "%s[%d]" % (where, i))
    if isinstance(value, dict):
        for key in schema.get("required", []):
            if key not in value:
                errs.append("%s: required %s missing" % (where, key))
        props = schema.get("properties", {})
        for key, sub in props.items():
            if key in value:
                errs += schema_errors(value[key], sub, "%s.%s" % (where, key))
        extra = schema.get("additionalProperties", True)
        for key in value:
            if key in props:
                continue
            if extra is False:
                errs.append("%s: additionalProperties %s" % (where, key))
            elif isinstance(extra, dict):
                errs += schema_errors(value[key], extra, "%s.%s" % (where, key))
    if "if" in schema and not schema_errors(value, schema["if"], where):
        if "then" in schema:
            errs += schema_errors(value, schema["then"], where)
    for sub in schema.get("allOf", []):
        errs += schema_errors(value, sub, where)
    return errs


def _load_json(path, what):
    if not os.path.isfile(path):
        raise Refused("%s is absent" % what)
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (ValueError, OSError) as exc:
        raise Refused("%s cannot be read as JSON (%s)" % (what, exc))


def _schema(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _inside(run_dir, rel, what):
    """Resolve rel inside run_dir; refuse when it escapes or does not exist."""
    if not isinstance(rel, str) or not rel or os.path.isabs(rel):
        raise Refused("%s is not a run-folder-relative path: %r" % (what, rel))
    root = os.path.realpath(run_dir)
    full = os.path.realpath(os.path.join(root, rel))
    if full != root and not full.startswith(root + os.sep):
        raise Refused("%s resolves outside the run folder: %s" % (what, rel))
    if not os.path.isfile(full):
        raise Refused("%s is absent: %s" % (what, rel))
    return os.path.relpath(full, root).replace(os.sep, "/")


def require_architecture_pass(run_dir):
    """Refuse unless checks/check_architecture.json exists, parses, fits U1's schema and passed."""
    what = "the architecture check report %s" % ARCH_REPORT
    report = _load_json(os.path.join(run_dir, ARCH_REPORT), what)
    errs = schema_errors(report, _schema(CHECK_REPORT_SCHEMA))
    if errs:
        raise Refused("%s fails the check-report schema: %s" % (what, "; ".join(errs[:5])))
    if report.get("result") != "pass":
        raise Refused("%s says result %s, not pass; run the architecture checks first"
                      % (what, report.get("result")))
    return report


def review_inputs(run_dir):
    """Exactly the files Review A reads: the agent design, its check report, and the
    future state, transformation map and snapshot intake the intake points to."""
    require_architecture_pass(run_dir)
    intake = _load_json(os.path.join(run_dir, INTAKE), "the intake %s" % INTAKE)
    out = [_inside(run_dir, AGENT, "the architecture output"),
           _inside(run_dir, ARCH_REPORT, "the architecture check report")]
    processes = intake.get("processes") if isinstance(intake, dict) else None
    if not isinstance(processes, list) or not processes:
        raise Refused("the intake lists no processes")
    for i, proc in enumerate(processes):
        if not isinstance(proc, dict):
            raise Refused("intake process %d is not an object" % (i + 1))
        for key in ("future_state", "transformation_map"):
            if key not in proc:
                raise Refused("intake process %d has no %s path" % (i + 1, key))
            out.append(_inside(run_dir, proc[key], "process %d %s" % (i + 1, key)))
    if isinstance(intake.get("set_map"), str):
        out.append(_inside(run_dir, intake["set_map"], "the set map"))
    obs = intake.get("observations")
    if not isinstance(obs, dict) or "snapshot_intake" not in obs:
        raise Refused("the intake has no observations.snapshot_intake path")
    out.append(_inside(run_dir, obs["snapshot_intake"], "observations.snapshot_intake"))
    out += observation_sources(run_dir)
    seen = []
    for rel in out:
        if rel not in seen:
            seen.append(rel)
    return seen


def observation_sources(run_dir):
    """Run-relative paths of the observation source files the snapshot intake lists (U1's resolver reads the list);
    a listed file that is missing or outside the run is left out (the resolver records why)."""
    res = evidence_ids.Resolver(run_dir)
    res._load()
    root = os.path.realpath(run_dir)
    snap = None
    try:
        with open(os.path.join(run_dir, INTAKE), encoding="utf-8") as fh:
            snap = json.load(fh)["observations"]["snapshot_intake"]
    except (OSError, ValueError, KeyError, TypeError):
        return []
    base = os.path.dirname(os.path.join(root, snap))
    out = []
    for c in res.collections:
        p = c.get("path")
        if not isinstance(p, str) or c.get("problem"):
            continue
        full = os.path.realpath(os.path.join(base, p))
        if full.startswith(root + os.sep) and os.path.isfile(full):
            r = os.path.relpath(full, root).replace(os.sep, "/")
            if r not in out:
                out.append(r)
    return sorted(out)


def _file_sha(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def revision(run_dir):
    """The architecture revision Review A reads: agent.yaml, the set map and the observation sources."""
    h = hashlib.sha256()
    parts = [AGENT]
    try:
        with open(os.path.join(run_dir, INTAKE), encoding="utf-8") as fh:
            sm = json.load(fh).get("set_map")
        if isinstance(sm, str) and os.path.isfile(os.path.join(run_dir, sm)):
            parts.append(sm)
    except (OSError, ValueError, AttributeError):
        pass
    parts += observation_sources(run_dir)
    for rel in parts:
        p = os.path.join(run_dir, rel)
        h.update(("%s\0%s\n" % (rel, _file_sha(p) if os.path.isfile(p) else "absent")).encode("utf-8"))
    return h.hexdigest()


def verdict_revision(run_dir, verdict):
    """The architecture revision the verdict was produced from: the verdict's own `revision`, else the one recorded in
    stages/review-a/reading.json when the review started (when the verdict was written after it), else None."""
    if isinstance(verdict.get("revision"), str) and verdict["revision"]:
        return verdict["revision"]
    rp, vp = os.path.join(run_dir, READING), os.path.join(run_dir, VERDICT)
    try:
        with open(rp, encoding="utf-8") as fh:
            reading = json.load(fh)
    except (OSError, ValueError):
        return None
    if isinstance(reading, dict) and isinstance(reading.get("revision"), str) \
            and os.path.getmtime(vp) >= os.path.getmtime(rp):
        return reading["revision"]
    return None


def record_reading(run_dir):
    """Record the revision a Review A run starts on (ad.py calls this when the review-a stage starts)."""
    _write_json(os.path.join(run_dir, READING), {"revision": revision(run_dir)})


def _write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, sort_keys=True)
        fh.write("\n")
    os.replace(tmp, path)


def route(run_dir):
    """Route Review A's findings to the architecture stage, once per architecture revision."""
    rev = revision(run_dir)
    prior = None
    if os.path.exists(os.path.join(run_dir, ROUTED)):
        try:
            with open(os.path.join(run_dir, ROUTED), encoding="utf-8") as fh:
                prior = json.load(fh)
        except (OSError, ValueError):
            prior = None
    require_architecture_pass(run_dir)
    verdict = _load_json(os.path.join(run_dir, VERDICT), "the Review A verdict %s" % VERDICT)
    errs = schema_errors(verdict, _schema(VERDICT_SCHEMA))
    if errs:
        raise Refused("the Review A verdict fails the verdict schema: %s" % "; ".join(errs[:5]))
    vsha = _file_sha(os.path.join(run_dir, VERDICT))
    read = verdict_revision(run_dir, verdict)
    findings = [item for item in verdict["items"] if item.get("verdict") == "finding"]
    doc = {"source": VERDICT, "revision": rev, "items": findings}
    routed = {"revision": rev, "findings": FINDINGS, "verdict": VERDICT, "verdict_sha256": vsha,
              "finding_count": len(findings), "item_count": len(verdict["items"])}
    if findings:
        doc["route_to"] = ROUTE_TO
        routed["route_to"] = ROUTE_TO
    # a verdict is bound to the revision it read: its own `revision`, else the one recorded when the review started.
    # A verdict with neither (the run's first verdict too), or one read on another revision, is never stamped with the
    # current revision: it is recorded as needing a new review and nothing is routed. The binding is checked before an
    # earlier route at this revision is returned, so a new unbound verdict never inherits an earlier bound route.
    stale = None
    if read is not None and read != rev:
        stale = "the verdict read architecture revision %s, not the current %s" % (read[:12], rev[:12])
    elif read is None:
        stale = ("the verdict names no revision it read and no reading record (%s) binds it, so the revision it read "
                 "is not known" % READING)
    if not stale and isinstance(prior, dict) and prior.get("revision") == rev \
            and prior.get("status") != "needs a new review":
        # routed once for this architecture revision: nothing is routed again (no refusal, the run goes on)
        return dict(prior, note="already routed once for this architecture revision; not routed again")
    history = list((prior or {}).get("history") or []) if isinstance(prior, dict) else []
    if isinstance(prior, dict) and prior.get("revision") and prior.get("revision") != rev:
        history.append({k: prior.get(k) for k in ("revision", "finding_count", "route_to", "status") if k in prior})
    if history:
        routed["history"] = history
    if stale:
        out = {"revision": rev, "verdict": VERDICT, "verdict_sha256": vsha, "status": "needs a new review",
               "reason": stale, "finding_count": 0}
        if history:
            out["history"] = history
        stale_findings = os.path.join(run_dir, FINDINGS)
        if os.path.exists(stale_findings):
            os.remove(stale_findings)
        _write_json(os.path.join(run_dir, ROUTED), out)
        return out
    routed["verdict_revision"] = rev
    shape = route_shape_problems(doc)
    if shape:
        # never leave a wrong-shaped route for ad.py: record the problem in ad.py's form and route nothing
        _record_problem(run_dir, "route-shape", FINDINGS, "the Review A route has the wrong shape: %s; it is not "
                        "written" % "; ".join(shape))
        if os.path.exists(os.path.join(run_dir, FINDINGS)):
            os.remove(os.path.join(run_dir, FINDINGS))
        out = {"revision": rev, "verdict": VERDICT, "verdict_sha256": vsha, "status": "route has the wrong shape",
               "reason": "; ".join(shape), "finding_count": 0}
        _write_json(os.path.join(run_dir, ROUTED), out)
        return out
    _write_json(os.path.join(run_dir, FINDINGS), doc)
    _write_json(os.path.join(run_dir, ROUTED), routed)
    return routed


PROBLEMS = "builder/problems.jsonl"  # ad.py's log of recorded problems: {at, kind, item, detail, stage, revision}


def route_shape_problems(doc):
    """What ad.py would trip on in stages/review-a/findings.json (pending_routes and _split_by_owner read it): an
    object with an `items` list, a string `route_to` when set, and per item a string `owner_stage` when set."""
    if not isinstance(doc, dict):
        return ["the route is not an object"]
    out = []
    if not isinstance(doc.get("items", []), list):
        out.append("items is not a list")
    if doc.get("route_to") and not isinstance(doc.get("route_to"), str):
        out.append("route_to is not a string")
    if doc.get("revision") is not None and not isinstance(doc.get("revision"), str):
        out.append("revision is not a string")
    for i, it in enumerate(doc.get("items") if isinstance(doc.get("items"), list) else []):
        if isinstance(it, dict) and it.get("owner_stage") is not None and not isinstance(it.get("owner_stage"), str):
            out.append("items[%d].owner_stage is not a string" % i)
    return out


def _record_problem(run_dir, kind, item, detail):
    import datetime
    rec = {"at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"), "kind": kind,
           "item": item, "detail": detail, "stage": "review-a", "revision": None}
    try:
        with open(os.path.join(run_dir, "state.json"), encoding="utf-8") as fh:
            rec["revision"] = json.load(fh)["stages"]["review-a"].get("attempts")
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        pass
    path = os.path.join(run_dir, PROBLEMS)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec) + "\n")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--run", required=True, help="Agent Design run folder")
    args = ap.parse_args(argv)
    try:
        result = route(args.run)
    except Refused as exc:
        print("refused: %s" % exc, file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
