"""Agent Build: run the current frozen suite through the run-this-case command (pins C7, P2, P3, P4, H1, H2, H7, I1).

Library:
  run_case(command, case_path, out_dir, timeout=300) -> P4 output dict, also written to out_dir/<case_id>.json

CLI:
  run_suite.py --run R --command CMD... [--label LABEL]   (default label suite-<package12>)
Writes R/outputs/<label>/<case_id>.json and, through _records.write_counted, R/records/suite-<package12>.json.
Exit: 0 aggregate pass; 1 fail; 3 pending; 2 usage, missing or unreadable package.sha256 or manifest,
entrypoint outside the package (nothing written).
The command is never started here: every case runs through _records.run_from_copy.
Standard library only, 3.9-safe.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _records  # noqa: E402
import compare  # noqa: E402
import log  # noqa: E402

P4_FIELDS = ("case_id", "values", "artifacts", "code_steps", "tool_calls", "model_calls", "rule_results",
             "decisions", "decision_trail", "activity_log", "exit")
EXITS = ("completed", "crashed", "refused")


def _split_out_dir(out_dir):
    """out_dir must be R/outputs/<label>; return (run, label) or raise ValueError."""
    full = os.path.abspath(out_dir)
    label = os.path.basename(full)
    outputs = os.path.dirname(full)
    if not label or os.path.basename(outputs) != "outputs":
        raise ValueError("out_dir must be R/outputs/<label>: %s" % out_dir)
    return os.path.dirname(outputs), label


def _missing_field(output):
    for field in P4_FIELDS:
        if field not in output:
            return field
    if "outcome" not in output:
        if output.get("no_verdict") is not True:
            return "outcome"
        if "no_verdict_reason" not in output:
            return "no_verdict_reason"
    return None


def run_case(command, case_path, out_dir, timeout=300):
    run, label = _split_out_dir(out_dir)
    case_doc, _ = _records.load_json(case_path)
    case_id = case_doc.get("case_id") if isinstance(case_doc, dict) else None
    if not isinstance(case_id, str) or not case_id:
        case_id = os.path.splitext(os.path.basename(case_path))[0]

    def crashed(error):
        return {"case_id": case_id, "exit": "crashed", "error": error}

    try:
        output, _ = _records.run_from_copy(run, command, case_path, label, timeout)
    except (ValueError, RuntimeError) as err:
        output = crashed(str(err))
    if output.get("exit") == "crashed":
        output = crashed(output.get("error") or "crashed")
    else:
        missing = _missing_field(output)
        if missing:
            output = crashed("missing field: %s" % missing)
        elif output.get("exit") not in EXITS:
            output = crashed("exit is not completed|crashed|refused: %r" % (output.get("exit"),))
        elif output.get("case_id") != case_id:
            output = crashed("case_id %r differs from the case file's %r" % (output.get("case_id"), case_id))
    if not os.path.isdir(out_dir):
        os.makedirs(out_dir)
    _records.write_record(os.path.join(out_dir, "%s.json" % case_id), output)
    return output


def _usage(msg):
    sys.stderr.write("run_suite.py: %s\n" % msg)
    return _records.USAGE


HELP = "usage: run_suite.py --run R [--label LABEL] --command CMD... [--label LABEL]\n"


def _parse(argv):
    """Return (run, label, command) or raise ValueError. A trailing `--label X` after the command is the label."""
    run = label = None
    command = None
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--command":
            command = argv[i + 1:]
            break
        if arg in ("--run", "--label") and i + 1 < len(argv):
            if arg == "--run":
                run = argv[i + 1]
            else:
                label = argv[i + 1]
            i += 2
            continue
        raise ValueError("unknown or incomplete argument: %s" % arg)
    if command is not None and len(command) >= 2 and command[-2] == "--label":
        label = command[-1]
        command = command[:-2]
    if not run or not command:
        raise ValueError("--run and --command are required")
    return run, label, command


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--help" in argv[:1] or "-h" in argv[:1]:
        sys.stdout.write(HELP)
        return _records.OK
    try:
        run, label, command = _parse(argv)
    except ValueError as err:
        return _usage(str(err))
    if not os.path.isdir(run):
        return _usage("--run is not a folder")
    package_sha, err = compare.read_package_sha(run)
    if err:
        return _usage(err)
    version_entry, version, err = compare.current_manifest(run)
    if err:
        return _usage(err)
    ok, reason = _records.entrypoint_in_package(run, command)
    if not ok:
        return _usage(reason if reason == "entrypoint outside the package" else
                      "entrypoint outside the package (%s)" % reason)
    try:
        actual = _records.package_hash(os.path.join(run, "package"))
    except ValueError as err:
        return _usage(str(err))
    brief, _ = _records.load_json(os.path.join(run, "brief.json"))
    brief = brief if isinstance(brief, dict) else {}
    package12 = package_sha[:12]
    label = label or "suite-%s" % package12
    out_dir = os.path.join(run, "outputs", label)
    package_changed = actual != package_sha

    outputs = {}
    run_info = {}
    for cid, entry in sorted((k, v) for k, v in compare.manifest_files(version_entry, "case").items()
                             if k is not None):
        case_path = os.path.join(run, "frozen", entry.get("path", ""))
        input_sha = _records.sha256_file(case_path) if os.path.isfile(case_path) else None
        run_info[cid] = {"input_sha256": input_sha}
        if package_changed or input_sha is None:
            continue
        output = run_case(command, case_path, out_dir)
        outputs[cid] = (output, "outputs/%s/%s.json" % (label, output.get("case_id", cid)))

    cases = compare.derive_cases(run, version_entry, outputs, brief, record_cases=run_info)
    for case in cases:
        if run_info[case["case_id"]]["input_sha256"] is None:
            case["state"], case["reason"] = "fail", "case file missing: %s" % case["case_id"]
        elif package_changed:
            case["state"], case["reason"] = "fail", "package changed"
    dispositions = compare.read_dispositions(run)
    cases, stale = compare.apply_dispositions(
        cases, dispositions, compare.current_hashes(run, cases, dispositions, package_sha))
    agg = compare.aggregate(cases, brief)
    if package_changed:
        agg = {"state": "fail", "reasons": ["package changed"], "non_critical_share": None}

    inputs = {"package.sha256": _records.sha256_file(os.path.join(run, "package.sha256"))}
    inputs.update(_records.standard_input_hashes(run))
    record = _records.record_header(_records.SKILL_VERSION, inputs, {"package_sha256": package_sha})
    record.update({
        "verdict": compare.verdict_for(agg["state"]),
        "reasons": list(agg["reasons"]) + ["stale disposition ignored: %s" % d.get("case_id") for d in stale],
        "command": list(command),
        "label": label,
        "manifest_version": version,
        "cases": compare.record_cases(cases),
        "aggregate": agg,
    })
    relpath = "records/suite-%s.json" % package12
    _records.write_counted(run, relpath, record)
    log.append(run, "suite.recorded", {"path": relpath, "label": label, "verdict": record["verdict"]})
    print(json.dumps({"record": relpath, "aggregate": agg}, sort_keys=True))
    return compare.exit_for(agg["state"])


if __name__ == "__main__":
    sys.exit(main())
