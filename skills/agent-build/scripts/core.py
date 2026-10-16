"""Agent Build: shared core checks over a label's raw outputs (criteria 12, 14; pins P3, H8, H13, I2, I3).

Usage: core.py --run R --label LABEL --source DIR

Writes R/records/core.json with six checks: no-upgrade, activity-log, decision-trail,
no-judge-pass, advisory-judge-valid, no-case-content. Run it on the final suite label and again on
`live`; the record names its label, and the exit gate re-derives core on both. Exit 0 pass, 1 a
check failed, 2 usage or an unreadable brief.json, frozen/MANIFEST.json or package.sha256 (nothing
written).

Entry point for the exit gate: evaluate(run, label, source_dir) -> record dict (never writes).

This file also holds the run-reading helpers stoprules.py and numbers.py import
(load_run, compare, header, finish). Standard library only, 3.9-safe.
"""
import argparse
import json
import os
import re
import sys
from decimal import Decimal, InvalidOperation

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _records  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
SCHEMA_PATH = os.path.join(os.path.dirname(HERE), "references", "judge-verdict.schema.json")
CHECK_NAMES = ("no-upgrade", "activity-log", "decision-trail", "no-judge-pass",
               "advisory-judge-valid", "no-case-content")
LOG_KEYS = ("ts", "case_id", "seq", "event", "actor", "detail")
LOG_EVENTS = ("decision", "tool", "rule", "step", "judge")
VERDICT_EXIT = {"pass": _records.OK, "fail": _records.FAILED, "needs_person": _records.NEEDS_PERSON}


class InputError(Exception):
    """Usage error or an unreadable required input: exit 2, nothing written."""


# ---------------------------------------------------------------- shared run reading

def _read_package_sha(run):
    path = os.path.join(run, "package.sha256")
    try:
        with open(path, encoding="utf-8") as f:
            value = f.readline().strip()
    except (OSError, UnicodeDecodeError) as err:
        raise InputError("unreadable package.sha256 (%s)" % err)
    if not re.fullmatch(r"[0-9a-f]{64}", value):
        raise InputError("unreadable package.sha256 (not a sha256)")
    return value


def load_run(run, label):
    """Read brief, frozen set, raw outputs and package hashes. InputError for exit-2 cases."""
    if not os.path.isdir(run):
        raise InputError("--run is not a folder: %s" % run)
    if not label or "/" in label or label in (".", ".."):
        raise InputError("bad --label")
    brief, err = _records.load_json(os.path.join(run, "brief.json"))
    if err or not isinstance(brief, dict):
        raise InputError("unreadable brief.json (%s)" % (err or "not an object"))
    manifest, err = _records.load_json(os.path.join(run, "frozen", "MANIFEST.json"))
    if err or not isinstance(manifest, dict):
        raise InputError("unreadable frozen/MANIFEST.json (%s)" % (err or "not an object"))
    version = manifest.get("current_version")
    entry = None
    for v in manifest.get("versions") or []:
        if isinstance(v, dict) and v.get("version") == version and not isinstance(version, bool):
            entry = v
    if entry is None or not isinstance(version, int):
        raise InputError("unreadable frozen/MANIFEST.json (no entry for current_version)")
    recorded = _read_package_sha(run)
    try:
        actual = _records.package_hash(os.path.join(run, "package"))
    except ValueError as err:
        raise InputError(str(err))

    frozen = {}
    for f in entry.get("files") or []:
        if not isinstance(f, dict) or f.get("kind") not in ("case", "expected"):
            continue
        cid = f.get("case_id")
        doc, _ = _records.load_json(os.path.join(run, "frozen", str(f.get("path"))))
        slot = frozen.setdefault(cid, {"case": None, "case_entry": None,
                                       "expected": None, "expected_entry": None})
        slot[f["kind"]] = doc if isinstance(doc, dict) else None
        slot[f["kind"] + "_entry"] = f
    frozen = dict((k, v) for k, v in frozen.items() if v["case"] is not None)

    outputs = {}
    out_dir = os.path.join(run, "outputs", label)
    if os.path.isdir(out_dir):
        for name in sorted(os.listdir(out_dir)):
            path = os.path.join(out_dir, name)
            if not name.endswith(".json") or not os.path.isfile(path):
                continue
            doc, err = _records.load_json(path)
            stem = name[:-5]
            if err or not isinstance(doc, dict):
                doc = {"case_id": stem, "exit": "crashed", "error": err or "not an object"}
            outputs[str(doc.get("case_id", stem))] = doc
    return {"run": run, "label": label, "brief": brief, "manifest_version": version,
            "frozen": frozen, "outputs": outputs, "package_recorded": recorded,
            "package_actual": actual}


def tolerance(ctx, case_id):
    """P5 order: the case's tolerance, brief amount_tolerance, 0.01 for Agent Design cases, exact."""
    slot = ctx["frozen"].get(case_id) or {}
    expected = slot.get("expected") or {}
    if expected.get("tolerance") is not None:
        return expected["tolerance"]
    if ctx["brief"].get("amount_tolerance") is not None:
        return ctx["brief"]["amount_tolerance"]
    if (slot.get("case") or {}).get("source") == "agent-design-suite":
        return 0.01
    return None


def manifest_states(ctx, case_id):
    slot = ctx["frozen"].get(case_id) or {}

    def one(entry):
        entry = entry or {}
        return {"state": entry.get("state"), "pending_reason": entry.get("pending_reason")}
    return {"case": one(slot.get("case_entry")), "expected": one(slot.get("expected_entry"))}


def compare(ctx, case_id, output):
    """compare.compare_case (C5) for one frozen case against an output."""
    try:
        import compare as compare_mod
    except ImportError:
        raise InputError("compare module not present")
    slot = ctx["frozen"].get(case_id)
    if slot is None:
        return {"case_id": case_id, "state": "fail", "reason": "case not in the frozen set"}
    return compare_mod.compare_case(slot["case"], slot["expected"], output,
                                    tolerance(ctx, case_id), manifest=manifest_states(ctx, case_id),
                                    run=ctx["run"])


def header(ctx, extra=None):
    run = ctx["run"]
    inputs = _records.standard_input_hashes(run)
    inputs["package.sha256"] = _records.sha256_file(os.path.join(run, "package.sha256"))
    inputs.update(extra or {})
    rec = _records.record_header(_records.SKILL_VERSION, inputs,
                                 {"package_sha256": ctx["package_recorded"]})
    rec["manifest_version"] = ctx["manifest_version"]
    return rec


def package_changed(ctx):
    return ctx["package_actual"] != ctx["package_recorded"]


def to_decimal(x):
    if isinstance(x, bool) or not isinstance(x, (int, float, str)):
        return None
    try:
        d = Decimal(str(x))
    except InvalidOperation:
        return None
    return d if d.is_finite() else None


def is_int(x):
    return isinstance(x, int) and not isinstance(x, bool)


def finish(run, relpath, record):
    """Write the counted record (pins H1) and return the P1 exit code."""
    _records.write_counted(run, relpath, record)
    return VERDICT_EXIT.get(record.get("verdict"), _records.USAGE)


def cli(argv, description, extra_args, evaluate_fn, relpath):
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--run", required=True)
    parser.add_argument("--label", required=True)
    for name in extra_args:
        parser.add_argument(name, required=True)
    try:
        args = parser.parse_args(argv)
    except SystemExit as err:
        return _records.USAGE if err.code else _records.OK
    try:
        record = evaluate_fn(args)
    except InputError as err:
        sys.stderr.write("error: %s\n" % err)
        return _records.USAGE
    code = finish(args.run, relpath, record)
    print("%s: %s" % (relpath, record["verdict"]))
    for reason in record.get("reasons", []):
        print("- %s" % reason)
    return code


def executed(output):
    """A completed output or a non-crashed refusal (pins H16, I13)."""
    return isinstance(output, dict) and output.get("exit") in ("completed", "refused")


# ---------------------------------------------------------------- judge verdict validator

SCHEMA_KEYWORDS = ("type", "required", "additionalProperties", "properties", "enum", "pattern")
_TYPES = {
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
    "string": lambda v: isinstance(v, str),
    "integer": is_int,
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "null": lambda v: v is None,
}


class SchemaError(Exception):
    """The schema itself uses a keyword or value this validator does not hold."""


def check_schema(schema, where="$"):
    """Walk the whole schema; SchemaError for an unknown keyword or value (never ignored)."""
    if not isinstance(schema, dict):
        raise SchemaError("schema at %s is not an object" % where)
    for key in schema:
        if key not in SCHEMA_KEYWORDS:
            raise SchemaError("unknown schema keyword %r at %s" % (key, where))
    if "type" in schema and schema["type"] not in _TYPES:
        raise SchemaError("unknown type %r at %s" % (schema["type"], where))
    for name, child in (schema.get("properties") or {}).items():
        check_schema(child, "%s.properties.%s" % (where, name))
    extra = schema.get("additionalProperties", True)
    if isinstance(extra, dict):
        check_schema(extra, "%s.additionalProperties" % where)
    elif not isinstance(extra, bool):
        raise SchemaError("additionalProperties at %s is neither boolean nor schema" % where)


def validate_schema(value, schema, where="$"):
    """Return a list of problems; SchemaError for an unknown keyword anywhere in the schema."""
    check_schema(schema)
    return _validate(value, schema, where)


def _validate(value, schema, where):
    problems = []
    if "type" in schema:
        if not _TYPES[schema["type"]](value):
            return ["%s is not of type %s" % (where, schema["type"])]
    if "enum" in schema and value not in schema["enum"]:
        problems.append("%s is not one of %s" % (where, schema["enum"]))
    if "pattern" in schema and isinstance(value, str) and not re.search(schema["pattern"], value):
        problems.append("%s does not match %s" % (where, schema["pattern"]))
    if isinstance(value, dict):
        for key in schema.get("required", []):
            if key not in value:
                problems.append("%s is missing %r" % (where, key))
        props = schema.get("properties", {})
        extra = schema.get("additionalProperties", True)
        for key in sorted(value):
            child = "%s.%s" % (where, key)
            if key in props:
                problems.extend(_validate(value[key], props[key], child))
            elif extra is False:
                problems.append("%s is not allowed" % child)
            elif isinstance(extra, dict):
                problems.extend(_validate(value[key], extra, child))
    return problems


def load_schema():
    schema, err = _records.load_json(SCHEMA_PATH)
    if err:
        raise InputError("unreadable judge-verdict schema (%s)" % err)
    return schema


def reduce_disposition(scores):
    mean = Decimal(sum(scores)) / Decimal(len(scores))
    if mean >= 4:
        return "agree"
    if mean <= 2:
        return "disagree"
    return "unsure"


def judge_line_problems(run, detail, schema):
    """Pins H8, I3: schema, ranges, reducer and artifact hash for one detail.valid line."""
    verdict = detail.get("verdict")
    try:
        problems = validate_schema(verdict, schema, "verdict")
    except SchemaError as err:
        return ["schema error: %s" % err]
    if problems:
        return problems
    scores = verdict["scores"]
    if not scores:
        return ["scores is empty"]
    for name in sorted(scores):
        s = scores[name]
        if not is_int(s) or s < 0 or s > 5:
            problems.append("score %r = %r is not an integer from 0 to 5" % (name, s))
    if problems:
        return problems
    recomputed = reduce_disposition(list(scores.values()))
    if recomputed != verdict["disposition"]:
        problems.append("stated disposition %s but the reducer gives %s"
                        % (verdict["disposition"], recomputed))
    artifact = detail.get("artifact")
    path = resolve_in_run(run, artifact)
    if path is None or not os.path.isfile(path):
        problems.append("artifact %r not found in the run folder" % (artifact,))
    elif _records.sha256_file(path) != verdict["artifact_sha256"]:
        problems.append("artifact_sha256 does not match %s" % artifact)
    return problems


# ---------------------------------------------------------------- the six checks

def resolve_in_run(run, rel):
    """Pin I2: a run-relative path resolved against R; None when absolute or outside R."""
    if not isinstance(rel, str) or not rel or os.path.isabs(rel):
        return None
    root = os.path.realpath(run)
    path = os.path.realpath(os.path.join(root, rel))
    if path != root and not path.startswith(root.rstrip(os.sep) + os.sep):
        return None
    return path


def _sorted_decisions(output):
    decisions = [d for d in output.get("decisions") or [] if isinstance(d, dict)]
    return sorted(decisions, key=lambda d: d.get("seq") if is_int(d.get("seq")) else -1)


def check_no_upgrade(ctx):
    escalation = {}
    for ls in ctx["brief"].get("label_sets") or []:
        if isinstance(ls, dict):
            escalation[ls.get("id")] = list(ls.get("escalation_outcomes") or [])
    findings = []
    for cid in sorted(ctx["outputs"]):
        out = ctx["outputs"][cid]
        if not executed(out):
            continue
        state = {}
        for d in _sorted_decisions(out):
            ls = d.get("label_set")
            current = state.get(ls)
            if (d.get("actor") == "model" and current is not None and current[0] == "code"
                    and d.get("after") != current[1]
                    and d.get("after") not in escalation.get(ls, [])):
                findings.append("%s: model decision at seq %s changes %s from %r (set by code) to %r"
                                % (cid, d.get("seq"), ls, current[1], d.get("after")))
            # A decision that leaves the outcome unchanged keeps whoever set it (code provenance).
            if current is None or d.get("after") != current[1]:
                state[ls] = (d.get("actor"), d.get("after"))
    return findings


def read_activity_log(ctx, cid, out):
    """Return (lines, problem)."""
    path = resolve_in_run(ctx["run"], out.get("activity_log"))
    if path is None:
        return None, "%s: activity_log %r is not a path inside the run folder" % (cid, out.get("activity_log"))
    if not os.path.isfile(path):
        return None, "%s: no activity log file at %s" % (cid, out.get("activity_log"))
    lines = []
    with open(path, encoding="utf-8", errors="replace") as f:
        for n, raw in enumerate(f, 1):
            if not raw.strip():
                continue
            try:
                obj = json.loads(raw)
            except ValueError:
                return None, "%s: activity log line %d is not JSON" % (cid, n)
            if not isinstance(obj, dict):
                return None, "%s: activity log line %d is not an object" % (cid, n)
            lines.append((n, obj))
    return lines, None


def check_activity_log(ctx):
    findings = []
    for cid in sorted(ctx["outputs"]):
        out = ctx["outputs"][cid]
        if not executed(out):
            continue
        lines, problem = read_activity_log(ctx, cid, out)
        if problem:
            findings.append(problem)
            continue
        decision_lines = []
        for n, obj in lines:
            missing = [k for k in LOG_KEYS if k not in obj]
            if missing:
                findings.append("%s: activity log line %d lacks %s" % (cid, n, ", ".join(missing)))
                continue
            if obj["event"] not in LOG_EVENTS:
                findings.append("%s: activity log line %d has event %r" % (cid, n, obj["event"]))
            if obj["event"] == "decision":
                decision_lines.append((n, obj))
        # Every decision line must account for exactly one decision with its seq: a matching line
        # never hides a conflicting or repeated line at the same seq.
        decisions = [d for d in out.get("decisions") or [] if isinstance(d, dict)]
        remaining = list(decisions)
        conflicted = []
        for n, obj in decision_lines:
            detail = obj["detail"] if isinstance(obj["detail"], dict) else {}
            same_seq = [d for d in decisions if d.get("seq") == obj["seq"]]
            match = None
            for d in remaining:
                if (d.get("seq") == obj["seq"] and obj["actor"] == d.get("actor")
                        and detail.get("label_set") == d.get("label_set")
                        and detail.get("before") == d.get("before") and detail.get("after") == d.get("after")):
                    match = d
                    break
            if match is not None:
                remaining.remove(match)
            elif not same_seq:
                findings.append("%s: activity log line %d logs a decision not in the output" % (cid, n))
            elif any(d in remaining for d in same_seq):
                conflicted.extend(d for d in same_seq if d in remaining and d not in conflicted)
                findings.append("%s: decision at seq %s logged with another actor, label set or outcome (line %d)"
                                % (cid, obj["seq"], n))
            else:
                findings.append("%s: decision at seq %s logged again or with another actor, label set or "
                                "outcome (line %d)" % (cid, obj["seq"], n))
        for d in remaining:
            if d not in conflicted:
                findings.append("%s: decision at seq %s has no decision line in the activity log" % (cid, d.get("seq")))
    return findings


def check_decision_trail(ctx):
    findings = []
    for cid in sorted(ctx["outputs"]):
        out = ctx["outputs"][cid]
        if not executed(out):
            continue
        trail = out.get("decision_trail")
        if not isinstance(trail, list) or not trail:
            findings.append("%s: decision_trail is empty or missing" % cid)
            continue
        steps = [s if isinstance(s, str) else "" for s in trail]
        judges = [i for i, s in enumerate(steps) if s.startswith("judge:")]
        if not judges:
            continue
        first = judges[0]
        if not any(s.startswith("check:") for s in steps[:first]):
            findings.append("%s: no check: step before the first judge: step" % cid)
        if any(s.startswith("check:") for s in steps[first + 1:]):
            findings.append("%s: a check: step comes after the first judge: step" % cid)
    return findings


def find_suite_record(run, label):
    folder = os.path.join(run, "records")
    if not os.path.isdir(folder):
        return None
    for name in sorted(os.listdir(folder)):
        if not name.endswith(".json"):
            continue
        doc, err = _records.load_json(os.path.join(folder, name))
        if not err and isinstance(doc, dict) and doc.get("label") == label and isinstance(doc.get("cases"), list):
            return doc
    return None


def check_no_judge_pass(ctx):
    suite = find_suite_record(ctx["run"], ctx["label"])
    if suite is None:
        return ["no record under records/ with label %s" % ctx["label"]]
    states = {}
    for c in suite["cases"]:
        if isinstance(c, dict):
            states[c.get("case_id")] = c
    findings = []
    for cid in sorted(ctx["outputs"]):
        out = ctx["outputs"][cid]
        if not executed(out):
            continue
        final = {}
        for d in _sorted_decisions(out):
            final[d.get("label_set")] = d.get("actor")
        judged = sorted(str(ls) for ls, actor in final.items() if actor == "judge")
        if not judged:
            continue
        case = states.get(cid) or {}
        disposition = case.get("disposition")
        accepted = isinstance(disposition, dict) and disposition.get("disposition") == "accepted"
        if case.get("state") == "pass" and not accepted:
            findings.append("%s: final outcome on %s comes from a judge and the case shows pass with no accepted disposition"
                            % (cid, ", ".join(judged)))
    return findings


def check_advisory_judge(ctx):
    schema = load_schema()
    findings = []
    for cid in sorted(ctx["outputs"]):
        out = ctx["outputs"][cid]
        if not executed(out):
            continue
        lines, problem = read_activity_log(ctx, cid, out)
        if problem:
            continue  # reported by activity-log
        for n, obj in lines:
            detail = obj.get("detail")
            if obj.get("event") != "judge" or not isinstance(detail, dict) or detail.get("valid") is not True:
                continue
            for p in judge_line_problems(ctx["run"], detail, schema):
                findings.append("%s: judge line %d marked valid: %s" % (cid, n, p))
    return findings


def _expected_literals(expected):
    out = []
    for cand in expected.get("candidates") or []:
        if not isinstance(cand, dict):
            continue
        vals = list((cand.get("values") or {}).values())
        for chk in cand.get("artifact_checks") or []:
            if isinstance(chk, dict):
                vals.extend((chk.get("values") or {}).values())
        out.extend(vals)
    return out


def _outcome_names(expected):
    names = set()
    for cand in expected.get("candidates") or []:
        if isinstance(cand, dict) and isinstance(cand.get("outcome"), dict):
            names.update(str(v) for v in cand["outcome"].values())
    return names


def content_patterns(ctx):
    """Return [(description, compiled regex or literal, kind)] for the no-case-content scan."""
    exempt = set()
    for slot in ctx["frozen"].values():
        if slot["expected"]:
            exempt |= _outcome_names(slot["expected"])
    for lit in ctx["brief"].get("rule_literals") or []:
        if isinstance(lit, dict) and lit.get("value") is not None:
            exempt.add(str(lit["value"]))
    patterns = []
    for cid in sorted(ctx["frozen"]):
        slot = ctx["frozen"][cid]
        patterns.append(("case id %s" % cid,
                         re.compile(r"(?<![A-Za-z0-9_\-])" + re.escape(str(cid)) + r"(?![A-Za-z0-9_\-])")))
        patterns.append(("frozen case payload %s" % cid, _records.normalize_json(slot["case"])))
        if slot["expected"]:
            patterns.append(("frozen expected payload %s" % cid, _records.normalize_json(slot["expected"])))
            for v in _expected_literals(slot["expected"]):
                if isinstance(v, str) and len(v) >= 6 and v not in exempt:
                    patterns.append(("expected string %r of %s" % (v, cid),
                                     re.compile("([\"'])" + re.escape(v) + r"\1")))
                elif not isinstance(v, bool) and isinstance(v, (int, float)) and str(v) not in exempt:
                    lit = json.dumps(v)
                    patterns.append(("expected number %s of %s" % (lit, cid),
                                     re.compile(r"(?<![A-Za-z0-9_.])" + re.escape(lit) + r"(?![A-Za-z0-9_.])")))
    return patterns


def check_no_case_content(ctx, source_dir):
    patterns = content_patterns(ctx)
    findings = []
    for root, dirs, files in os.walk(source_dir):
        dirs[:] = sorted(d for d in dirs if d != ".git")
        for name in sorted(files):
            path = os.path.join(root, name)
            if os.path.islink(path) or not os.path.isfile(path):
                continue
            rel = os.path.relpath(path, source_dir).replace(os.sep, "/")
            with open(path, encoding="utf-8", errors="ignore") as f:
                text = f.read()
            lines = text.split("\n")
            for desc, pat in patterns:
                if isinstance(pat, str):
                    at = text.find(pat)
                    if at >= 0:
                        findings.append("%s:%d holds %s" % (rel, text.count("\n", 0, at) + 1, desc))
                    continue
                for n, line in enumerate(lines, 1):
                    if pat.search(line):
                        findings.append("%s:%d holds %s" % (rel, n, desc))
    return findings


def evaluate(run, label, source_dir):
    """Build the core.json record (pins G11). Never writes."""
    if not source_dir or not os.path.isdir(source_dir):
        raise InputError("--source is not a folder: %s" % source_dir)
    ctx = load_run(run, label)
    record = header(ctx)
    record["label"] = label  # core runs on the suite label and again on live (criterion 12)
    if package_changed(ctx):
        record.update({"verdict": "fail", "reasons": ["package changed"], "checks": []})
        return record
    results = [
        ("no-upgrade", check_no_upgrade(ctx)),
        ("activity-log", check_activity_log(ctx)),
        ("decision-trail", check_decision_trail(ctx)),
        ("no-judge-pass", check_no_judge_pass(ctx)),
        ("advisory-judge-valid", check_advisory_judge(ctx)),
        ("no-case-content", check_no_case_content(ctx, source_dir)),
    ]
    checks = []
    reasons = []
    for name, findings in results:
        checks.append({"name": name, "verdict": "fail" if findings else "pass", "findings": findings})
        reasons.extend("%s: %s" % (name, f) for f in findings)
    record.update({"checks": checks, "verdict": "fail" if reasons else "pass", "reasons": reasons})
    return record


def main(argv=None):
    return cli(argv, "Shared core checks over a label's outputs.", ["--source"],
               lambda a: evaluate(a.run, a.label, a.source), "records/core.json")


if __name__ == "__main__":
    sys.exit(main())
