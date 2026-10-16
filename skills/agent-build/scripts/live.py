"""Live run over the current frozen cases, bound to the package hash (U12).

Usage:
  live.py record --run R [--usage-file PATH] [--attest NAME --evidence PATH] --command CMD...

Runs every `kind: case` file of the current manifest version through `run_suite.run_case`
(which runs only from a hashed package copy), compares each output with `compare.compare_case`,
keeps a failed comparison `pending` until a hash-bound disposition, and writes
`R/records/live.json` through `_records.write_counted`.

Exit: 0 pass; 1 a check failed; 2 usage or unreadable input (nothing written); 3 a disagreement
awaits a person's disposition, or `uses_model` is undecidable.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _records  # noqa: E402
import log  # noqa: E402

LABEL = "live"
DISAGREEMENT = "disagreement awaiting disposition"
NMI = "needs_more_information"


def die(msg):
    sys.stderr.write("live.py: %s\n" % msg)
    return _records.USAGE


def split_command(argv):
    if "--command" not in argv:
        return argv, None
    i = argv.index("--command")
    return argv[:i], argv[i + 1:]


def read_package_sha(run):
    path = os.path.join(run, "package.sha256")
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8") as f:
        value = f.readline().strip()
    return value or None


def capability(run, name):
    """The P8 capability: brief.json `capabilities` (person-confirmed) overrides capabilities.json."""
    brief, _ = _records.load_json(os.path.join(run, "brief.json"))
    if isinstance(brief, dict) and isinstance(brief.get("capabilities"), dict):
        if name in brief["capabilities"]:
            return brief["capabilities"][name]
    caps, _ = _records.load_json(os.path.join(run, "capabilities.json"))
    if isinstance(caps, dict) and isinstance(caps.get("capabilities"), dict):
        if name in caps["capabilities"]:
            return caps["capabilities"][name]
    return NMI


def run_relative(run, path):
    """Resolve PATH against R; return (absolute, run-relative) or (absolute, None) when outside R."""
    full = path if os.path.isabs(path) else os.path.join(run, path)
    real = os.path.realpath(full)
    root = os.path.realpath(run)
    if real != root and real.startswith(root.rstrip(os.sep) + os.sep):
        return full, os.path.relpath(real, root).replace(os.sep, "/")
    return full, None


def non_empty_file(path):
    return os.path.isfile(path) and os.path.getsize(path) > 0


def current_files(manifest):
    version = manifest.get("current_version")
    for entry in manifest.get("versions") or []:
        if isinstance(entry, dict) and entry.get("version") == version:
            return version, [f for f in entry.get("files") or [] if isinstance(f, dict)]
    return version, None


def manifest_state(entry):
    if entry is None:
        return {"state": NMI, "pending_reason": "no manifest entry"}
    return {"state": entry.get("state"), "pending_reason": entry.get("pending_reason")}


def tolerance_for(expected, case_doc, brief):
    if isinstance(expected, dict) and expected.get("tolerance") is not None:
        return expected["tolerance"]
    if isinstance(brief, dict) and brief.get("amount_tolerance") is not None:
        return brief["amount_tolerance"]
    if isinstance(case_doc, dict) and case_doc.get("source") == "agent-design-suite":
        return 0.01
    return None


def read_dispositions(run, reasons):
    path = os.path.join(run, "records", "dispositions.jsonl")
    lines = []
    if not os.path.isfile(path):
        return lines
    with open(path, encoding="utf-8") as f:
        for n, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except ValueError:
                obj = None
            if not isinstance(obj, dict):
                reasons.append("dispositions.jsonl line %d is malformed and was ignored" % n)
                continue
            lines.append(obj)
    return lines


def artifact_hashes(run, output):
    """Artifact hashes keyed by run-relative path, as compare.py dispose stores them (integration review 1, E2)."""
    import compare  # noqa: E402  (U10)
    hashes = {}
    artifacts = output.get("artifacts") if isinstance(output, dict) else None
    if isinstance(artifacts, dict):
        for name, rel in sorted(artifacts.items()):
            if not isinstance(rel, str):
                continue
            path = os.path.join(run, rel)
            hashes[compare.artifact_key(run, rel)] = _records.sha256_file(path) if os.path.isfile(path) else None
    return hashes


def usage_evidence(run, args, reasons):
    """Return (usage dict or None, spend_usd)."""
    if args.usage_file is not None:
        full, rel = run_relative(run, args.usage_file)
        if rel is None:
            # Observed usage is a host-written file; it may sit outside the run folder.
            rel = os.path.realpath(full)
        if not non_empty_file(full):
            reasons.append("usage file missing or empty: %s" % rel)
            return None, None
        doc, err = _records.load_json(full)
        if not isinstance(doc, dict):
            reasons.append("usage file is not a JSON object: %s" % rel)
            return None, None
        spend = doc.get("spend_usd")
        if isinstance(spend, bool) or not isinstance(spend, (int, float)):
            spend = None
        return {"source": "observed", "evidence": rel}, spend
    if args.attest is not None or args.evidence is not None:
        if not args.attest or not args.attest.strip():
            reasons.append("attestation without a name")
            return None, None
        if not args.evidence:
            reasons.append("attestation without an evidence file")
            return None, None
        full, rel = run_relative(run, args.evidence)
        if rel is None:
            reasons.append("attestation evidence outside the run folder")
            return None, None
        if not non_empty_file(full):
            reasons.append("attestation evidence missing or empty: %s" % rel)
            return None, None
        return {"source": "attested", "attestor": args.attest.strip(), "evidence": rel}, None
    return None, None


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    head, command = split_command(argv)
    parser = argparse.ArgumentParser(prog="live.py")
    sub = parser.add_subparsers(dest="cmd")
    rec = sub.add_parser("record")
    rec.add_argument("--run", required=True)
    rec.add_argument("--usage-file")
    rec.add_argument("--attest")
    rec.add_argument("--evidence")
    try:
        args = parser.parse_args(head)
    except SystemExit as err:
        return 0 if err.code == 0 else _records.USAGE
    if args.cmd != "record":
        return die("expected the record subcommand")
    if not command:
        return die("--command CMD... is required")
    run = args.run
    if not os.path.isdir(run):
        return die("--run is not a folder")

    recorded = read_package_sha(run)
    if recorded is None:
        return die("package.sha256 missing")
    manifest, err = _records.load_json(os.path.join(run, "frozen", "MANIFEST.json"))
    if not isinstance(manifest, dict):
        return die("frozen/MANIFEST.json missing or unreadable")
    version, files = current_files(manifest)
    if files is None:
        return die("current manifest version not found")
    ok, why = _records.entrypoint_in_package(run, command)
    if not ok:
        return die(why)
    try:
        actual = _records.package_hash(os.path.join(run, "package"))
    except ValueError as err:
        return die(str(err))

    import compare  # noqa: E402  (U10)
    import run_suite  # noqa: E402  (U10)

    reasons = []
    fails = []
    needs_person = []
    if actual != recorded:
        fails.append("package changed")

    brief, _ = _records.load_json(os.path.join(run, "brief.json"))
    if not isinstance(brief, dict):
        brief = {}
    expected_entries = {f.get("case_id"): f for f in files if f.get("kind") == "expected"}
    out_dir = os.path.join(run, "outputs", LABEL)
    if not os.path.isdir(out_dir):
        os.makedirs(out_dir)

    cases = []
    current = {"package_sha256": recorded, "expected_sha256": {}, "artifact_sha256": {}}
    model_calls_total = 0
    for entry in sorted((f for f in files if f.get("kind") == "case"), key=lambda f: str(f.get("case_id"))):
        case_path = os.path.join(run, "frozen", entry["path"])
        case_doc, _ = _records.load_json(case_path)
        case_id = entry.get("case_id")
        exp_entry = expected_entries.get(case_id)
        expected = None
        exp_sha = None
        if exp_entry is not None:
            exp_path = os.path.join(run, "frozen", exp_entry["path"])
            expected, _ = _records.load_json(exp_path)
            exp_sha = _records.sha256_file(exp_path) if os.path.isfile(exp_path) else None
        output = run_suite.run_case(command, case_path, out_dir)
        out_rel = "outputs/%s/%s.json" % (LABEL, case_id)
        _records.write_record(os.path.join(run, out_rel), output)
        if isinstance(output, dict) and isinstance(output.get("model_calls"), int) \
                and not isinstance(output.get("model_calls"), bool):
            model_calls_total += output["model_calls"]
        manifest_arg = {"case": manifest_state(entry), "expected": manifest_state(exp_entry)}
        result = compare.compare_case(case_doc, expected, output,
                                      tolerance_for(expected, case_doc, brief), manifest=manifest_arg, run=run)
        state, reason = result.get("state"), result.get("reason")
        crashed = not isinstance(output, dict) or output.get("exit") == "crashed"
        comparison = {"state": state, "reason": reason}
        if state == "fail" and not crashed:
            state, reason = "pending", DISAGREEMENT
        exp_doc = expected if isinstance(expected, dict) else {}
        cases.append({
            "case_id": case_id,
            "case_sha256": _records.sha256_file(case_path) if os.path.isfile(case_path) else None,
            "input_sha256": _records.sha256_json(case_doc.get("input")) if isinstance(case_doc, dict) else None,
            "output": out_rel,
            "state": state,
            "reason": reason,
            "comparison": comparison,
            "critical": exp_doc.get("critical"),
            "disposition": None,
            "judgement_needed": exp_doc.get("judgement_needed"),
            "exclusion": exp_doc.get("exclusion"),
            "expected_sha256": exp_sha,
            "manifest": manifest_arg,
        })
        current["expected_sha256"][case_id] = exp_sha
        current["artifact_sha256"][case_id] = artifact_hashes(run, output)

    dispositions = read_dispositions(run, reasons)
    cases, stale = compare.apply_dispositions(cases, dispositions, current)
    for item in stale or []:
        name = item.get("case_id") if isinstance(item, dict) else item
        reasons.append("stale disposition ignored: %s" % name)
    for case in cases:
        disp = case.get("disposition") if isinstance(case.get("disposition"), dict) else {}
        if case.get("state") == "pass" and disp.get("disposition") == "accepted" \
                and (case.get("comparison") or {}).get("state") == "fail":
            # Pins I5: accepted never passes a scripted fail; keep it pending and name it stale.
            case["state"], case["reason"], case["disposition"] = "pending", DISAGREEMENT, None
            reasons.append("stale disposition ignored: %s" % case.get("case_id"))
        for key in ("judgement_needed", "exclusion", "expected_sha256", "manifest"):
            case.pop(key, None)
    aggregate = compare.aggregate(cases, brief)

    uses_model = capability(run, "uses_model")
    usage, spend = usage_evidence(run, args, reasons)
    if uses_model is True:
        if model_calls_total <= 0:
            fails.append("design uses a model but model_calls_total is 0")
        if usage is None:
            fails.append("design uses a model but there is no usage evidence")
    elif uses_model is not False:
        needs_person.append("uses_model is undecidable (%s)" % (uses_model if uses_model == NMI else "missing"))

    pending = [c["case_id"] for c in cases if c.get("state") == "pending"]
    if pending:
        needs_person.append("pending cases: %s" % ", ".join(str(p) for p in pending))
    if aggregate.get("state") == "fail":
        fails.append("aggregate fail")

    if fails:
        verdict, code = "fail", _records.FAILED
    elif needs_person:
        verdict, code = "needs_person", _records.NEEDS_PERSON
    elif aggregate.get("state") == "pass":
        verdict, code = "pass", _records.OK
    else:
        verdict, code = "needs_person", _records.NEEDS_PERSON
        needs_person.append("aggregate state %s" % aggregate.get("state"))

    inputs = _records.standard_input_hashes(run)
    inputs["package.sha256"] = _records.sha256_file(os.path.join(run, "package.sha256"))
    disp_path = os.path.join(run, "records", "dispositions.jsonl")
    if os.path.isfile(disp_path):
        inputs["records/dispositions.jsonl"] = _records.sha256_file(disp_path)
    record = _records.record_header(_records.SKILL_VERSION, inputs, {"package_sha256": recorded})
    record.update({
        "verdict": verdict,
        "reasons": fails + needs_person + reasons + list(aggregate.get("reasons") or []),
        "command": command,
        "label": LABEL,
        "manifest_version": version,
        "cases": cases,
        "aggregate": aggregate,
        "model_calls_total": model_calls_total,
        "usage": usage,
        "spend_usd": spend,
    })
    _records.write_counted(run, "records/live.json", record)
    log.append(run, "live.recorded", {"verdict": verdict, "package_sha256": recorded,
                                      "manifest_version": version})
    for line in record["reasons"]:
        sys.stdout.write("%s\n" % line)
    sys.stdout.write("verdict: %s\n" % verdict)
    return code


if __name__ == "__main__":
    sys.exit(main())
