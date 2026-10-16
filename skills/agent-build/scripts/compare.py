"""Agent Build: comparison rules, suite aggregate and dispositions (pins C5, P5, G35, H6, H18, I5, I10).

Library:
  compare_case(case_frozen, expected, output, tolerance, manifest=None, run=None) -> {"case_id", "state", "reason"}
  resolve_tolerance(expected, brief, case_frozen) -> number or None (exact)
  aggregate(cases, brief) -> {"state", "reasons", "non_critical_share"}
  apply_dispositions(cases, dispositions, current) -> (cases, stale)
  derive_cases(run, version_entry, outputs, brief, record_cases=None) -> list of case dicts

CLI:
  compare.py --run R --label LABEL
  compare.py dispose --run R --case ID --disposition accepted|agent-wrong|expected-wrong
             --reviewer NAME --note TEXT --artifact PATH...
Exit: 0 pass; 1 fail (or a refused disposition); 3 pending; 2 usage or unreadable input.
Standard library only, 3.9-safe.
"""
import argparse
import copy
import decimal
import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _records  # noqa: E402

DISPOSITIONS = ("accepted", "agent-wrong", "expected-wrong")
JUDGEMENT_REASON = "needs judgement"
MALFORMED = "malformed numeric control"
DISPOSITIONS_FILE = "records/dispositions.jsonl"


def _result(case_id, state, reason):
    return {"case_id": case_id, "state": state, "reason": reason}


def _is_number(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def _finite(x):
    return _is_number(x) and math.isfinite(x)


def _dec(x):
    return decimal.Decimal(str(x))


def _within(out, exp, tol):
    if not _is_number(out) or not math.isfinite(out):
        return False
    return abs(_dec(out) - _dec(exp)) <= _dec(tol)


def _norm_label(v):
    if isinstance(v, list):
        return sorted(json.dumps(x, sort_keys=True) for x in v)
    return v


def _same_label(got, exp):
    """Type-aware equality of one label-set value: true/false only equal the same boolean, null only null, a number
    never equals a boolean (so false never matches 0 and true never matches 1)."""
    got, exp = _norm_label(got), _norm_label(exp)
    if isinstance(got, bool) or isinstance(exp, bool):
        return isinstance(got, bool) and isinstance(exp, bool) and got is exp
    if got is None or exp is None:
        return got is None and exp is None
    return got == exp


def _candidate_value_maps(candidate):
    maps = []
    if isinstance(candidate.get("values"), dict):
        maps.append(candidate["values"])
    for check in candidate.get("artifact_checks") or []:
        if isinstance(check, dict) and isinstance(check.get("values"), dict):
            maps.append(check["values"])
    return maps


def _load_artifact(output, name, run):
    """Return (text, parsed JSON or None), or (None, None) when the output names no such file."""
    artifacts = output.get("artifacts") if isinstance(output.get("artifacts"), dict) else {}
    path = artifacts.get(name)
    if not isinstance(path, str) or not os.path.isfile(_artifact_path(path, run)):
        return None, None
    with open(_artifact_path(path, run), encoding="utf-8", errors="replace") as f:
        text = f.read()
    try:
        doc = json.loads(text)
    except ValueError:
        doc = None
    return text, doc


def _numeric_controls_ok(tolerance, candidates, output, run=None):
    """Pin H18: tolerance finite and >= 0; compared expected and output values (artifacts too) finite."""
    if tolerance is not None and (not _finite(tolerance) or tolerance < 0):
        return False
    out_values = output.get("values") if isinstance(output, dict) else None
    for cand in candidates:
        if not isinstance(cand, dict):
            continue
        for values in _candidate_value_maps(cand):
            for name, exp in values.items():
                if not _finite(exp):
                    return False
        if isinstance(cand.get("values"), dict) and isinstance(out_values, dict):
            for name in cand["values"]:
                got = out_values.get(name)
                if _is_number(got) and not math.isfinite(got):
                    return False
        for check in cand.get("artifact_checks") or []:
            if not isinstance(output, dict) or not isinstance(check, dict) \
                    or not isinstance(check.get("values"), dict):
                continue
            _, doc = _load_artifact(output, check.get("artifact"), run)
            if not isinstance(doc, dict):
                continue
            for name in check["values"]:
                got = doc.get(name)
                if _is_number(got) and not math.isfinite(got):
                    return False
    return True


def _artifact_path(path, run):
    if os.path.isabs(path):
        return path
    return os.path.join(run or os.getcwd(), path)


def _check_artifact(check, output, tol, run):
    """Return None when the artifact check holds, else the reason it does not."""
    name = check.get("artifact")
    text, doc = _load_artifact(output, name, run)
    if text is None:
        return "artifact %s missing" % name
    for field in check.get("required_fields") or []:
        if not isinstance(doc, dict) or doc.get(field) in (None, "", [], {}):
            return "artifact %s lacks field %s" % (name, field)
    headings = set()
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            headings.add(stripped.lstrip("#").strip())
    for section in check.get("sections") or []:
        if section not in headings:
            return "artifact %s lacks section %s" % (name, section)
    for vname, exp in (check.get("values") or {}).items():
        got = doc.get(vname) if isinstance(doc, dict) else None
        if not _within(got, exp, tol):
            return "artifact %s value %s outside tolerance" % (name, vname)
    return None


def _candidate_holds(cand, output, tol, run):
    """Return None when every part of the candidate holds together, else the first miss."""
    if cand.get("no_verdict") is True:
        if output.get("no_verdict") is not True:
            return "expected no verdict, output gave one"
        if output.get("no_verdict_reason") != cand.get("no_verdict_reason"):
            return "no-verdict reason differs"
    else:
        if output.get("no_verdict") is True:
            return "output gave no verdict"
        got = output.get("outcome")
        if not isinstance(got, dict):
            return "output has no outcome"
        exp = cand["outcome"]
        if set(got) != set(exp):
            return "label sets differ: expected %s, got %s" % (sorted(exp), sorted(got))
        for label_set in sorted(exp):
            if not _same_label(got[label_set], exp[label_set]):
                return "label set %s: expected %r, got %r" % (label_set, exp[label_set], got[label_set])
    out_values = output.get("values") if isinstance(output.get("values"), dict) else {}
    for name in sorted(cand.get("values") or {}):
        if not _within(out_values.get(name), cand["values"][name], tol):
            return "value %s outside tolerance" % name
    for check in cand.get("artifact_checks") or []:
        miss = _check_artifact(check, output, tol, run)
        if miss:
            return miss
    return None


def compare_case(case_frozen, expected, output, tolerance, manifest=None, run=None):
    """Apply P5 to one case. `run` resolves run-relative artifact paths (default: working folder)."""
    case_id = (case_frozen or {}).get("case_id")
    if manifest:
        for key in ("case", "expected"):
            entry = manifest.get(key) or {}
            if entry.get("state") == "pending":
                return _result(case_id, "pending", "%s pending in the manifest: %s"
                               % (key, entry.get("pending_reason")))
    candidates = expected.get("candidates") if isinstance(expected, dict) else None
    if not _numeric_controls_ok(tolerance, candidates or [], output, run):
        return _result(case_id, "pending", MALFORMED)
    if not isinstance(candidates, list) or not candidates:
        return _result(case_id, "pending", "missing expectation")
    for i, cand in enumerate(candidates):
        if not isinstance(cand, dict):
            return _result(case_id, "pending", "conflicting expectation: candidate %d" % i)
        has_outcome = isinstance(cand.get("outcome"), dict)
        no_verdict = cand.get("no_verdict") is True
        if has_outcome == no_verdict:
            return _result(case_id, "pending", "conflicting expectation: candidate %d" % i)
        if has_outcome and not cand["outcome"]:
            return _result(case_id, "pending", "incomplete expectation")
        if no_verdict and not cand.get("no_verdict_reason"):
            return _result(case_id, "pending", "no-verdict reason missing in expectation")
    if expected.get("exclusion") is True:
        return _result(case_id, "pending", "exclusion")
    if not isinstance(output, dict):
        return _result(case_id, "fail", "missing output")
    if output.get("exit") == "crashed":
        return _result(case_id, "fail", "crashed: %s" % output.get("error"))
    tol = tolerance if tolerance is not None else 0
    misses = []
    for cand in candidates:
        miss = _candidate_holds(cand, output, tol, run)
        if miss is None:
            if expected.get("judgement_needed") is True:
                return _result(case_id, "pending", JUDGEMENT_REASON)
            return _result(case_id, "pass", "candidate matched")
        misses.append(miss)
    if output.get("no_verdict") is True and not output.get("no_verdict_reason") and \
            any(c.get("no_verdict") is True for c in candidates):
        return _result(case_id, "pending", "no-verdict reason missing in output")
    return _result(case_id, "fail", "no candidate satisfied: " + "; ".join(misses))


def resolve_tolerance(expected, brief, case_frozen):
    """Case tolerance, else brief amount_tolerance, else 0.01 for an Agent Design suite case, else None (exact)."""
    if isinstance(expected, dict) and expected.get("tolerance") is not None:
        return expected["tolerance"]
    if isinstance(brief, dict) and brief.get("amount_tolerance") is not None:
        return brief["amount_tolerance"]
    if isinstance(case_frozen, dict) and case_frozen.get("source") == "agent-design-suite":
        return 0.01
    return None


def aggregate(cases, brief):
    brief = brief if isinstance(brief, dict) else {}
    floor = brief.get("non_critical_floor")
    floor_value = floor.get("value") if isinstance(floor, dict) else None
    if floor_value is not None and (not _finite(floor_value) or not 0 <= floor_value <= 1):
        return {"state": "pending", "reasons": [MALFORMED + ": non_critical_floor.value"],
                "non_critical_share": None}
    if not cases:
        return {"state": "fail", "reasons": ["0 of 0 cases: empty suite"], "non_critical_share": None}
    floor_ratified = (floor_value is not None and bool(floor.get("ratified_by")) and bool(floor.get("date")))
    critical_fails = [c["case_id"] for c in cases if c.get("critical") is True and c.get("state") == "fail"]
    pending = [c["case_id"] for c in cases if c.get("state") == "pending"]
    unknown_crit = [c["case_id"] for c in cases if c.get("critical") is None]
    non_critical = [c for c in cases if c.get("critical") is False]
    share = None
    if non_critical:
        passed = len([c for c in non_critical if c.get("state") == "pass"])
        share = float(passed) / len(non_critical)
    reasons = []
    fail = False
    if critical_fails:
        fail = True
        reasons.append("critical case failed: %s" % ", ".join(critical_fails))
    if not pending and non_critical and floor_ratified and share < floor_value:
        fail = True
        reasons.append("non-critical pass share %.4f below floor %s" % (share, floor_value))
    if fail:
        return {"state": "fail", "reasons": reasons, "non_critical_share": share}
    if pending:
        reasons.append("pending cases: %s" % ", ".join(pending))
    if unknown_crit:
        reasons.append("criticality missing: %s" % ", ".join(unknown_crit))
    if non_critical and not floor_ratified:
        reasons.append("non-critical floor unset or not ratified")
    if not non_critical and not unknown_crit and brief.get("all_critical_confirmed") is not True:
        reasons.append("every case is critical and all_critical_confirmed is not true")
    if reasons:
        return {"state": "pending", "reasons": reasons, "non_critical_share": None}
    passed = len([c for c in cases if c.get("state") == "pass"])
    return {"state": "pass", "reasons": ["%d of %d cases pass" % (passed, len(cases))],
            "non_critical_share": share}


def _hashes_current(d, case, current):
    cid = case["case_id"]
    if d.get("case_sha256") != case.get("case_sha256"):
        return False
    if d.get("expected_sha256") != (current.get("expected_sha256") or {}).get(cid):
        return False
    if d.get("package_sha256") != current.get("package_sha256"):
        return False
    named = d.get("artifact_sha256")
    if not isinstance(named, dict):
        return False
    now = (current.get("artifact_sha256") or {}).get(cid) or {}
    for name, sha in named.items():
        if sha is None or now.get(name) != sha:
            return False
    return True


def _acceptance_eligible(case, current):
    manifest = case.get("manifest") or {}
    return (case.get("state") == "pending" and case.get("reason") == JUDGEMENT_REASON
            and case.get("judgement_needed") is True and case.get("exclusion") is not True
            and (manifest.get("case") or {}).get("state") == "ready"
            and (manifest.get("expected") or {}).get("state") == "ready"
            and case.get("expected_sha256") is not None
            and case.get("expected_sha256") == (current.get("expected_sha256") or {}).get(case["case_id"]))


def apply_dispositions(cases, dispositions, current):
    """Pin I5. Returns (cases with the latest current disposition applied, stale dispositions)."""
    out = copy.deepcopy(cases)
    stale = []
    by_id = {}
    for d in dispositions or []:
        if isinstance(d, dict):
            by_id.setdefault(d.get("case_id"), []).append(d)
    known = set(c["case_id"] for c in out)
    for cid, ds in by_id.items():
        if cid not in known:
            stale.extend(ds)
    for case in out:
        applied = None
        for d in by_id.get(case["case_id"], []):
            if not _hashes_current(d, case, current):
                stale.append(d)
            elif d.get("disposition") == "accepted" and not _acceptance_eligible(case, current):
                stale.append(d)
            elif d.get("disposition") in DISPOSITIONS:
                applied = d
            else:
                stale.append(d)
        if applied is None:
            continue
        kind = applied["disposition"]
        if kind == "accepted":
            case["state"], case["reason"] = "pass", "accepted by %s" % applied.get("reviewer")
        elif kind == "agent-wrong":
            case["state"], case["reason"] = "fail", "agent-wrong per %s" % applied.get("reviewer")
        else:
            case["state"] = "pending"
            case["reason"] = "expected-wrong per %s: pending a new manifest version" % applied.get("reviewer")
        case["disposition"] = applied
    return out, stale


# ---------------------------------------------------------------- run-folder helpers

def current_manifest(run):
    """Return (version entry, version int, error)."""
    doc, err = _records.load_json(os.path.join(run, "frozen", "MANIFEST.json"))
    if err:
        return None, None, err
    if not isinstance(doc, dict):
        return None, None, "manifest is not an object"
    version = doc.get("current_version")
    for entry in doc.get("versions") or []:
        if isinstance(entry, dict) and entry.get("version") == version and isinstance(entry.get("files"), list):
            return entry, version, None
    return None, None, "manifest has no current version entry"


def manifest_files(version_entry, kind):
    return dict((f.get("case_id"), f) for f in version_entry["files"]
                if isinstance(f, dict) and f.get("kind") == kind)


def manifest_object(case_entry, expected_entry):
    def part(e):
        if not e:
            return {"state": "pending", "pending_reason": "missing from the manifest"}
        return {"state": e.get("state"), "pending_reason": e.get("pending_reason")}
    return {"case": part(case_entry), "expected": part(expected_entry)}


def read_dispositions(run):
    path = os.path.join(run, DISPOSITIONS_FILE)
    found = []
    if not os.path.isfile(path):
        return found
    with open(path, encoding="utf-8") as f:
        for line in f:
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if isinstance(d, dict):
                found.append(d)
    return found


def read_package_sha(run):
    """Return (hex, error) for R/package.sha256."""
    path = os.path.join(run, "package.sha256")
    try:
        with open(path, encoding="utf-8") as f:
            value = f.readline().strip()
    except (OSError, UnicodeDecodeError) as err:
        return None, "package.sha256 missing or unreadable (%s)" % err
    if len(value) != 64 or any(ch not in "0123456789abcdef" for ch in value):
        return None, "package.sha256 is not a sha256 hex"
    return value, None


def artifact_key(run, path):
    """The run-relative key a disposition stores an artifact hash under (forward slashes)."""
    full = path if os.path.isabs(path) else os.path.join(run, path)
    return os.path.relpath(os.path.realpath(full), os.path.realpath(run)).replace(os.sep, "/")


def current_hashes(run, cases, dispositions, package_sha):
    """The `current` object of apply_dispositions, artifact hashes taken from the files dispositions name."""
    current = {"package_sha256": package_sha, "expected_sha256": {}, "artifact_sha256": {}}
    for case in cases:
        cid = case["case_id"]
        current["expected_sha256"][cid] = case.get("expected_file_sha256")
        names = {}
        for d in dispositions:
            if d.get("case_id") == cid and isinstance(d.get("artifact_sha256"), dict):
                for name in d["artifact_sha256"]:
                    path = os.path.join(run, name)
                    names[name] = _records.sha256_file(path) if os.path.isfile(path) else None
        current["artifact_sha256"][cid] = names
    return current


def derive_cases(run, version_entry, outputs, brief, record_cases=None):
    """Compare every frozen case. `outputs`: {case_id: (output dict or None, run-relative path or None)}.

    `record_cases`: {case_id: {"input_sha256"}} from the run; an input hash that differs from the
    manifest's case hash fails the case. Returns full case dicts (record fields plus the I5 extras).
    """
    case_files = manifest_files(version_entry, "case")
    expected_files = manifest_files(version_entry, "expected")
    record_cases = record_cases or {}
    cases = []
    for cid in sorted(k for k in case_files if k is not None):
        entry = case_files[cid]
        exp_entry = expected_files.get(cid)
        case_doc, _ = _records.load_json(os.path.join(run, "frozen", entry.get("path", "")))
        expected = None
        expected_file_sha = None
        if exp_entry:
            exp_path = os.path.join(run, "frozen", exp_entry.get("path", ""))
            expected, _ = _records.load_json(exp_path)
            try:  # unreadable: no current hash, so no disposition can be current for it
                expected_file_sha = _records.sha256_file(exp_path)
            except OSError:
                expected_file_sha = None
        if not isinstance(expected, dict):
            expected = None
        output, out_path = outputs.get(cid, (None, None))
        manifest = manifest_object(entry, exp_entry)
        tol = resolve_tolerance(expected, brief, case_doc)
        result = compare_case(case_doc if isinstance(case_doc, dict) else {"case_id": cid},
                              expected, output, tol, manifest=manifest, run=run)
        input_sha = (record_cases.get(cid) or {}).get("input_sha256")
        if input_sha is not None and input_sha != entry.get("sha256"):
            result = _result(cid, "fail", "input changed: %s" % entry.get("path"))
        cases.append({
            "case_id": cid,
            "case_sha256": entry.get("sha256"),
            "input_sha256": input_sha,
            "output": out_path,
            "state": result["state"],
            "reason": result["reason"],
            "critical": expected.get("critical") if expected else None,
            "disposition": None,
            "judgement_needed": bool(expected and expected.get("judgement_needed") is True),
            "exclusion": bool(expected and expected.get("exclusion") is True),
            "expected_sha256": exp_entry.get("sha256") if exp_entry else None,
            "expected_file_sha256": expected_file_sha,
            "manifest": manifest,
        })
    return cases


RECORD_CASE_KEYS = ("case_id", "case_sha256", "input_sha256", "output", "state", "reason",
                    "critical", "disposition")


def record_cases(cases):
    return [dict((k, c.get(k)) for k in RECORD_CASE_KEYS) for c in cases]


def verdict_for(state):
    return {"pass": "pass", "fail": "fail", "pending": "needs_person"}[state]


def exit_for(state):
    return {"pass": _records.OK, "fail": _records.FAILED, "pending": _records.NEEDS_PERSON}[state]


# ---------------------------------------------------------------- CLI

def _usage(msg):
    sys.stderr.write("compare.py: %s\n" % msg)
    return _records.USAGE


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        sys.stderr.write("compare.py: %s\n" % message)
        sys.exit(_records.USAGE)


def _relabel(run, label):
    if not os.path.isdir(run):
        return _usage("--run is not a folder")
    records_dir = os.path.join(run, "records")
    target = None
    for name in sorted(os.listdir(records_dir)) if os.path.isdir(records_dir) else []:
        if not name.endswith(".json"):
            continue
        doc, err = _records.load_json(os.path.join(records_dir, name))
        if not err and isinstance(doc, dict) and doc.get("label") == label and isinstance(doc.get("cases"), list):
            target = (name, doc)
    if target is None:
        return _usage("no record under records/ has label %s" % label)
    name, record = target
    version_entry, version, err = current_manifest(run)
    if err:
        return _usage(err)
    package_sha, err = read_package_sha(run)
    if err:
        return _usage(err)
    brief, _ = _records.load_json(os.path.join(run, "brief.json"))
    brief = brief if isinstance(brief, dict) else {}

    frozen_ids = set(k for k in manifest_files(version_entry, "case") if k is not None)
    outputs = {}
    unknown = []
    out_dir = os.path.join(run, "outputs", label)
    for fname in sorted(os.listdir(out_dir)) if os.path.isdir(out_dir) else []:
        if not fname.endswith(".json"):
            continue
        doc, err = _records.load_json(os.path.join(out_dir, fname))
        cid = doc.get("case_id") if isinstance(doc, dict) else None
        if cid not in frozen_ids:
            unknown.append(str(cid if cid is not None else fname))
            continue
        outputs[cid] = (doc, "outputs/%s/%s" % (label, fname))
    stored = dict((c.get("case_id"), c) for c in record["cases"] if isinstance(c, dict))
    cases = derive_cases(run, version_entry, outputs, brief, record_cases=stored)
    dispositions = read_dispositions(run)
    cases, stale = apply_dispositions(cases, dispositions, current_hashes(run, cases, dispositions, package_sha))
    agg = aggregate(cases, brief)
    if unknown:
        agg = {"state": "fail", "reasons": ["output for a case id not in the frozen set: %s" % ", ".join(unknown)]
               + agg["reasons"], "non_critical_share": agg["non_critical_share"]}
    record["cases"] = record_cases(cases)
    record["aggregate"] = agg
    record["manifest_version"] = version
    record["verdict"] = verdict_for(agg["state"])
    record["reasons"] = list(agg["reasons"]) + ["stale disposition ignored: %s" % d.get("case_id") for d in stale]
    record["produced_at"] = _records.now_iso()
    hashes = dict(record.get("input_hashes") or {})
    hashes.update(_records.standard_input_hashes(run))
    record["input_hashes"] = hashes
    _records.write_counted(run, "records/" + name, record)
    print(json.dumps({"record": "records/" + name, "aggregate": agg}, sort_keys=True))
    return exit_for(agg["state"])


def _dispose(args):
    run = args.run
    if not os.path.isdir(run):
        return _usage("--run is not a folder")
    if not args.reviewer or not args.reviewer.strip():
        return _usage("--reviewer is required")
    if not args.note or not args.note.strip():
        return _usage("--note is required")
    if not args.artifact:
        return _usage("--artifact is required")
    for path in args.artifact:
        full = path if os.path.isabs(path) else os.path.join(run, path)
        if not os.path.isfile(full):
            return _usage("artifact file missing: %s" % path)
    reviewer = args.reviewer.strip()
    if reviewer == "judge" or reviewer.startswith("judge:"):
        sys.stderr.write("compare.py: a judge cannot record a disposition\n")
        return _records.FAILED
    version_entry, _, err = current_manifest(run)
    if err:
        return _usage(err)
    package_sha, err = read_package_sha(run)
    if err:
        return _usage(err)
    if args.case not in manifest_files(version_entry, "case"):
        return _usage("unknown case id: %s" % args.case)
    brief, _ = _records.load_json(os.path.join(run, "brief.json"))
    brief = brief if isinstance(brief, dict) else {}
    package12 = package_sha[:12]
    record, err = _records.load_json(os.path.join(run, "records", "suite-%s.json" % package12))
    outputs = {}
    stored = {}
    if not err and isinstance(record, dict):
        for c in record.get("cases") or []:
            if isinstance(c, dict) and c.get("case_id") == args.case:
                stored[args.case] = c
                if c.get("output"):
                    doc, _ = _records.load_json(os.path.join(run, c["output"]))
                    outputs[args.case] = (doc, c["output"])
    cases = derive_cases(run, version_entry, outputs, brief, record_cases=stored)
    case = [c for c in cases if c["case_id"] == args.case][0]
    if args.disposition == "accepted":
        current = {"expected_sha256": {case["case_id"]: case["expected_file_sha256"]}}
        if not _acceptance_eligible(case, current):
            sys.stderr.write("compare.py: accepted refused: case %s is %s (%s), not pending for judgement only\n"
                             % (case["case_id"], case["state"], case["reason"]))
            return _records.FAILED
    artifact_sha = {}
    for path in args.artifact:
        full = path if os.path.isabs(path) else os.path.join(run, path)
        artifact_sha[artifact_key(run, path)] = _records.sha256_file(full)
    entry = {
        "case_id": case["case_id"],
        "case_sha256": case["case_sha256"],
        "expected_sha256": case["expected_sha256"],
        "package_sha256": package_sha,
        "artifact_sha256": artifact_sha,
        "disposition": args.disposition,
        "reviewer": reviewer,
        "note": args.note,
        "date": _records.now_iso(),
    }
    records_dir = os.path.join(run, "records")
    if not os.path.isdir(records_dir):
        os.makedirs(records_dir)
    with open(os.path.join(run, DISPOSITIONS_FILE), "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, sort_keys=True) + "\n")
        f.flush()
        os.fsync(f.fileno())
    print(json.dumps(entry, sort_keys=True))
    return _records.OK


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "dispose":
        p = _Parser(prog="compare.py dispose")
        p.add_argument("--run", required=True)
        p.add_argument("--case", required=True)
        p.add_argument("--disposition", required=True, choices=DISPOSITIONS)
        p.add_argument("--reviewer")
        p.add_argument("--note")
        p.add_argument("--artifact", nargs="+")
        return _dispose(p.parse_args(argv[1:]))
    p = _Parser(prog="compare.py")
    p.add_argument("--run", required=True)
    p.add_argument("--label", required=True)
    args = p.parse_args(argv)
    return _relabel(args.run, args.label)


if __name__ == "__main__":
    sys.exit(main())
