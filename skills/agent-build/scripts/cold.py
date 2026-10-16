"""Light cold-run record from an attested report, bound to the package hash (U12).

Usage:
  cold.py record --run R --attestor NAME --evidence PATH --report FILE

Reads the attested report (P3 cold fields plus `package_sha256`), derives the required setup probes
from the capabilities (P8; never from the report) and writes `R/records/cold.json` through
`_records.write_counted` whatever the verdict.

Exit: 0 pass; 1 a check failed; 2 usage error or unreadable report (nothing written);
3 a probe's applicability, or a capability deciding the required set, is needs_more_information.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _records  # noqa: E402
import log  # noqa: E402

NMI = "needs_more_information"
ALWAYS = ("python39", "git", "curl", "entrypoint_start", "config_paths", "write_denial",
          "read_only_zero_denials", "eval_chain")
COLD_FIELDS = ("empty_folder", "files_missing", "install", "probes", "cases", "reviewer_view",
               "reused_state")


def die(msg):
    sys.stderr.write("cold.py: %s\n" % msg)
    return _records.USAGE


def capabilities(run):
    """capabilities.json `capabilities`, overridden field by field by brief.json `capabilities`."""
    caps = {}
    doc, _ = _records.load_json(os.path.join(run, "capabilities.json"))
    if isinstance(doc, dict) and isinstance(doc.get("capabilities"), dict):
        caps.update(doc["capabilities"])
    brief, _ = _records.load_json(os.path.join(run, "brief.json"))
    if isinstance(brief, dict) and isinstance(brief.get("capabilities"), dict):
        caps.update({k: v for k, v in brief["capabilities"].items() if k != "per_process"})
    if "engine" not in caps:
        config, _ = _records.load_json(os.path.join(run, "config.json"))
        if isinstance(config, dict) and config.get("engine") is not None:
            caps["engine"] = config["engine"]
    return caps


def required_probes(caps):
    """Return (sorted required probe names, [undecidable capability names]) under the P8 table."""
    required = list(ALWAYS)
    undecided = []

    def get(name):
        value = caps.get(name, NMI)
        if value == NMI:
            undecided.append(name)
            return None
        return value

    uses_model = get("uses_model")
    if uses_model is True:
        required += ["key_file_credentials", "engine_egress"]
        engine = get("engine")
        if engine == "openai":
            required.append("openai_region")
    if "runtime" not in caps or caps.get("runtime") == NMI:
        undecided.append("runtime")
    elif caps["runtime"] is not None:
        required.append("runtime_pin")
    if get("needs_registry") is True:
        required.append("registry_access")
    external = get("calls_external_systems")
    if isinstance(external, list) and external:
        required.append("external_endpoints")
    if get("runtime_on_node") is True:
        required.append("node_version")
    if get("ships_as_docker_image") is True:
        required.append("image-build")
    return sorted(set(required)), undecided


def current_case_ids(run):
    manifest, _ = _records.load_json(os.path.join(run, "frozen", "MANIFEST.json"))
    if not isinstance(manifest, dict):
        return None, None
    version = manifest.get("current_version")
    for entry in manifest.get("versions") or []:
        if isinstance(entry, dict) and entry.get("version") == version:
            return version, set(f.get("case_id") for f in entry.get("files") or []
                                if isinstance(f, dict) and f.get("kind") == "case")
    return version, None


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(prog="cold.py")
    sub = parser.add_subparsers(dest="cmd")
    rec = sub.add_parser("record")
    rec.add_argument("--run", required=True)
    rec.add_argument("--attestor")
    rec.add_argument("--evidence")
    rec.add_argument("--report", required=True)
    try:
        args = parser.parse_args(argv)
    except SystemExit as err:
        return 0 if err.code == 0 else _records.USAGE
    if args.cmd != "record":
        return die("expected the record subcommand")
    run = args.run
    if not os.path.isdir(run):
        return die("--run is not a folder")
    sha_path = os.path.join(run, "package.sha256")
    if not os.path.isfile(sha_path):
        return die("package.sha256 missing")
    with open(sha_path, encoding="utf-8") as f:
        recorded = f.readline().strip()
    if not recorded:
        return die("package.sha256 empty")
    report_path = args.report if os.path.isabs(args.report) else os.path.join(run, args.report)
    report, err = _records.load_json(report_path)
    if not isinstance(report, dict):
        return die("report unreadable: %s" % (err or "not a JSON object"))
    version, case_ids = current_case_ids(run)
    if case_ids is None:
        return die("frozen/MANIFEST.json missing or without its current version")
    try:
        actual = _records.package_hash(os.path.join(run, "package"))
    except ValueError as err:
        return die(str(err))

    fails = []
    needs_person = []

    attestor = args.attestor.strip() if isinstance(args.attestor, str) else ""
    if not attestor:
        fails.append("missing attestor")
    evidence = args.evidence
    if not evidence:
        fails.append("evidence file missing")
    else:
        full = evidence if os.path.isabs(evidence) else os.path.join(run, evidence)
        if not os.path.isfile(full):
            fails.append("evidence file missing: %s" % evidence)
        elif os.path.getsize(full) == 0:
            fails.append("evidence file empty: %s" % evidence)

    if report.get("empty_folder") is not True:
        fails.append("empty_folder is not true")
    files_missing = report.get("files_missing")
    if not isinstance(files_missing, list):
        fails.append("files_missing is not a list")
    elif files_missing:
        fails.append("files missing: %s" % ", ".join(str(x) for x in files_missing))
    if report.get("install") != "pass":
        fails.append("install did not pass")

    caps = capabilities(run)
    required, undecided = required_probes(caps)
    for name in undecided:
        needs_person.append("capability %s is needs_more_information" % name)
    probes = report.get("probes")
    by_name = {}
    if not isinstance(probes, list):
        fails.append("probes is not a list")
        probes = []
    for probe in probes:
        if not (isinstance(probe, dict) and isinstance(probe.get("name"), str)):
            fails.append("malformed probe entry")
        elif probe["name"] in by_name:
            fails.append("duplicate probe: %s" % probe["name"])
        else:
            by_name[probe["name"]] = probe
    for probe in by_name.values():
        applies = probe.get("applies")
        if applies == NMI:
            needs_person.append("probe applicability needs_more_information: %s" % probe["name"])
        elif applies is True and probe.get("result") != "pass":
            fails.append("probe failed: %s" % probe["name"])
    for name in required:
        probe = by_name.get(name)
        if probe is None:
            fails.append("required probe missing: %s" % name)
        elif probe.get("applies") is not True and probe.get("applies") != NMI:
            fails.append("incorrect applicability: %s" % name)
    if caps.get("ships_as_docker_image") is True:
        image = by_name.get("image-build")
        undecided = image is not None and image.get("applies") == NMI  # already a needs_person reason above
        if not undecided and not (image and image.get("applies") is True and image.get("result") == "pass"):
            fails.append("no image-build probe passing for a Docker agent")

    cases = report.get("cases")
    if not isinstance(cases, list) or not cases:
        fails.append("no case")
        cases = cases if isinstance(cases, list) else []
    for case in cases:
        if not isinstance(case, dict):
            fails.append("malformed case entry")
            continue
        if case.get("case_id") not in case_ids:
            fails.append("case outside the current frozen manifest: %s" % case.get("case_id"))
        if case.get("state") != "pass":
            fails.append("case not pass: %s" % case.get("case_id"))
    view = report.get("reviewer_view")
    if not isinstance(view, str) or not view.strip():
        fails.append("reviewer_view is empty")
    if report.get("reused_state") is True:
        fails.append("reused state")
    elif report.get("reused_state") is not False:
        fails.append("reused_state is missing")
    if report.get("package_sha256") != recorded:
        fails.append("report package_sha256 differs from package.sha256")
    if actual != recorded:
        fails.append("package changed")

    if fails:
        verdict, code = "fail", _records.FAILED
    elif needs_person:
        verdict, code = "needs_person", _records.NEEDS_PERSON
    else:
        verdict, code = "pass", _records.OK

    inputs = _records.standard_input_hashes(run)
    inputs["package.sha256"] = _records.sha256_file(sha_path)
    inputs["report"] = _records.sha256_file(report_path)
    record = _records.record_header(_records.SKILL_VERSION, inputs, {"package_sha256": recorded})
    record.update({
        "verdict": verdict,
        "reasons": fails + needs_person,
        "attestor": attestor or None,
        "evidence": evidence or None,
        "required_probes": required,
        "manifest_version": version,
        "report_package_sha256": report.get("package_sha256"),
    })
    for field in COLD_FIELDS:
        record[field] = report.get(field)
    _records.write_counted(run, "records/cold.json", record)
    log.append(run, "cold.recorded", {"verdict": verdict, "package_sha256": recorded})
    for line in record["reasons"]:
        sys.stdout.write("%s\n" % line)
    sys.stdout.write("verdict: %s\n" % verdict)
    return code


if __name__ == "__main__":
    sys.exit(main())
