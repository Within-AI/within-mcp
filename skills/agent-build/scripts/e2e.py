#!/usr/bin/env python3
"""Agent Build: early and final end-to-end records (criterion 9; pins P2b, P3, P4, P8, G23-G26, H1-H3, I1, I2).

Usage:
  e2e.py check-split --split FILE
  e2e.py record --run R --early|--final --command CMD... [--source DIR] [--tools-file PATH]
                [--attest NAME --evidence PATH] [--probes-file PATH]

check-split: 0 an end-to-end unit is in the first build round; 1 it is not; 2 file or block
missing or unreadable.
record: 0 pass; 1 fail; 2 usage or unreadable input (no record); 3 (--final only) a probe's
applicability is needs_more_information. The record is written before exiting 0, 1 or 3. In the
early record a probe whose applicability is needs_more_information is recorded as not applicable
with its reasoning (`applies: false`, `applicability: needs_more_information`) and the run
continues; it never stops the early record or round 2. The final record keeps it undecided.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _records  # noqa: E402
import log  # noqa: E402

NMI = "needs_more_information"

PROBES = (
    "python39", "git", "curl", "entrypoint_start", "config_paths", "key_file_credentials",
    "write_denial", "read_only_zero_denials", "runtime_pin", "engine_egress",
    "external_endpoints", "registry_access", "node_version", "openai_region", "eval_chain",
)
ALWAYS = ("python39", "git", "curl", "entrypoint_start", "config_paths", "write_denial",
          "read_only_zero_denials", "eval_chain")
OBSERVED = ("python39", "git", "curl", "entrypoint_start", "read_only_zero_denials", "eval_chain")
CAP_FIELDS = ("uses_model", "produces_numbers", "writes", "calls_external_systems",
              "ships_as_docker_image", "runtime", "runtime_on_node", "needs_registry", "engine")

P4_REQUIRED = ("case_id", "values", "artifacts", "code_steps", "tool_calls", "model_calls",
               "rule_results", "decisions", "decision_trail", "activity_log", "exit")

_JSON_BLOCK = re.compile(r"^```json[ \t]*\n(.*?)^```[ \t]*$", re.S | re.M)
_FENCE_JSON = re.compile(r"^```json[ \t]*$", re.M)


class Usage(Exception):
    pass


# ---------------------------------------------------------------- check-split

def read_split(path):
    """Return (units, err). err set means exit 2."""
    if not os.path.isfile(path):
        return None, "split file missing: %s" % path
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except (OSError, UnicodeDecodeError) as err:
        return None, "split file unreadable: %s" % err
    if path.lower().endswith(".json"):
        body = text
    else:
        if len(_FENCE_JSON.findall(text)) != 1:
            return None, "split file needs exactly one fenced json block"
        m = _JSON_BLOCK.search(text)
        if not m:
            return None, "split file's json block is not closed"
        body = m.group(1)
    try:
        doc = json.loads(body)
    except ValueError as err:
        return None, "split block is not valid JSON: %s" % err
    units = doc.get("units") if isinstance(doc, dict) else None
    if not isinstance(units, list):
        return None, "split block has no units list"
    for u in units:
        if not isinstance(u, dict) or "id" not in u or "kind" not in u:
            return None, "split unit without id or kind: %r" % (u,)
        r = u.get("round")
        if not isinstance(r, int) or isinstance(r, bool):
            return None, "split unit %s has no integer round" % u.get("id")
    return units, None


def check_split(path):
    units, err = read_split(path)
    if err:
        print(err)
        return _records.USAGE
    e2e = [u for u in units if u.get("kind") == "end-to-end"]
    if not e2e:
        print("refused: no end-to-end unit in the split")
        return _records.FAILED
    first = min(u["round"] for u in units)
    if not any(u["round"] == first for u in e2e):
        print("refused: no end-to-end unit in the first build round (round %d); end-to-end in round %s"
              % (first, ", ".join(str(u["round"]) for u in e2e)))
        return _records.FAILED
    print("ok: end-to-end unit %s in the first build round (round %d)"
          % (", ".join(str(u["id"]) for u in e2e if u["round"] == first), first))
    return _records.OK


# ---------------------------------------------------------------- helpers

def run_path(run, path):
    return path if os.path.isabs(path) else os.path.join(run, path)


def rel_or_none(run, path):
    """Run-relative forward-slash path of a file inside R, else None."""
    if not isinstance(path, str) or not path:
        return None
    full = run_path(run, path)
    real, root = os.path.realpath(full), os.path.realpath(run)
    if not real.startswith(root.rstrip(os.sep) + os.sep):
        return None
    return os.path.relpath(real, root).replace(os.sep, "/")


def nonempty_file_in_run(run, path):
    rel = rel_or_none(run, path)
    if rel is None:
        return False
    full = os.path.join(run, rel)
    return os.path.isfile(full) and os.path.getsize(full) > 0


def load_capabilities(run, brief):
    """Merge R/capabilities.json with brief.json capabilities (brief wins per field).

    Returns (caps, missing_file). A field absent everywhere is needs_more_information.
    """
    doc, err = _records.load_json(os.path.join(run, "capabilities.json"))
    base = {}
    missing = err is not None or not isinstance(doc, dict)
    if not missing:
        base = doc.get("capabilities") if isinstance(doc.get("capabilities"), dict) else {}
    override = brief.get("capabilities") if isinstance(brief.get("capabilities"), dict) else {}
    caps = {}
    for field in CAP_FIELDS:
        if field in override:
            caps[field] = override[field]
        elif field in base:
            caps[field] = base[field]
        else:
            caps[field] = NMI
    return caps, missing


def applicability(name, caps, caps_missing):
    """Return (applies, reason) for one probe (pins P8)."""
    if name in ALWAYS:
        return True, "always applicable"
    if caps_missing:
        return NMI, "capabilities.json missing"

    def undecided(*fields):
        for f in fields:
            if caps.get(f) == NMI:
                return f
        return None

    if name in ("key_file_credentials", "engine_egress"):
        u = undecided("uses_model")
        if u:
            return NMI, "%s is needs_more_information" % u
        return caps["uses_model"] is True, "uses_model is %s" % json.dumps(caps["uses_model"])
    if name == "runtime_pin":
        u = undecided("runtime")
        if u:
            return NMI, "runtime is needs_more_information"
        return caps["runtime"] is not None, "runtime is %s" % json.dumps(caps["runtime"])
    if name == "registry_access":
        u = undecided("needs_registry")
        if u:
            return NMI, "needs_registry is needs_more_information"
        return caps["needs_registry"] is True, "needs_registry is %s" % json.dumps(caps["needs_registry"])
    if name == "external_endpoints":
        u = undecided("calls_external_systems")
        if u:
            return NMI, "calls_external_systems is needs_more_information"
        v = caps["calls_external_systems"]
        return (isinstance(v, list) and len(v) > 0), "calls_external_systems is %s" % json.dumps(v)
    if name == "node_version":
        u = undecided("runtime_on_node")
        if u:
            return NMI, "runtime_on_node is needs_more_information"
        return caps["runtime_on_node"] is True, "runtime_on_node is %s" % json.dumps(caps["runtime_on_node"])
    if name == "openai_region":
        u = undecided("engine", "uses_model")
        if u:
            return NMI, "%s is needs_more_information" % u
        ok = caps["engine"] == "openai" and caps["uses_model"] is True
        return ok, "engine is %s, uses_model is %s" % (json.dumps(caps["engine"]), json.dumps(caps["uses_model"]))
    return NMI, "unknown probe"


def tool_version(name, args):
    path = shutil.which(name)
    if not path:
        return False, "%s not found" % name
    try:
        proc = subprocess.run([path] + args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as err:
        return False, "%s version call failed: %s" % (name, err)
    text = proc.stdout.decode("utf-8", "replace").strip().splitlines()
    first = text[0] if text else ""
    if proc.returncode != 0:
        return False, "%s version call exited %d" % (name, proc.returncode)
    return True, first


def probe_python39():
    ok, detail = tool_version("python3", ["--version"])
    if not ok:
        return "fail", detail
    m = re.search(r"(\d+)\.(\d+)", detail)
    if not m or (int(m.group(1)), int(m.group(2))) < (3, 9):
        return "fail", "python3 below 3.9: %s" % detail
    return "pass", detail


def classify_eval_step(exit_code, results_path):
    """Classify one eval step: "capability-miss" | "crashed" | "pass"."""
    if exit_code == 0:
        return "pass"
    if exit_code == 1 and results_path and os.path.isfile(results_path):
        doc, err = _records.load_json(results_path)
        if err is None and isinstance(doc, dict) and isinstance(doc.get("cases"), list):
            if any(isinstance(c, dict) and c.get("state") == "fail" for c in doc["cases"]):
                return "capability-miss"
    return "crashed"


GRADER = '''import json, sys
output_path, expected_path, results_path = sys.argv[1:4]
with open(output_path, encoding="utf-8") as f:
    output = json.load(f)
with open(expected_path, encoding="utf-8") as f:
    expected = json.load(f)
state = "pass" if output.get("outcome") == expected.get("outcome") else "fail"
with open(results_path, "w", encoding="utf-8") as f:
    json.dump({"cases": [{"case_id": output.get("case_id"), "state": state}]}, f)
sys.exit(0 if state == "pass" else 1)
'''


def probe_eval_chain(run, output, label):
    """Run a stand-in grader as a subprocess against an expectation that must differ."""
    tmp = tempfile.mkdtemp()
    try:
        grader = os.path.join(tmp, "grader.py")
        out_path = os.path.join(tmp, "output.json")
        exp_path = os.path.join(tmp, "expectation.json")
        results = os.path.join(tmp, "results.json")
        with open(grader, "w", encoding="utf-8") as f:
            f.write(GRADER)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(output if isinstance(output, dict) else {}, f)
        with open(exp_path, "w", encoding="utf-8") as f:
            json.dump({"outcome": {"stand-in": "an outcome no case gives"}}, f)
        try:
            proc = subprocess.run([sys.executable, grader, out_path, exp_path, results],
                                  cwd=tmp, env=_records.subprocess_env(run, label),
                                  stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, timeout=60)
            code = proc.returncode
        except (OSError, subprocess.TimeoutExpired):
            code = -1
        present = os.path.isfile(results)
        cls = classify_eval_step(code, results)
        evidence = {"grader_exit": code, "results_file": present, "classification": cls}
        ok = code == 1 and present and cls == "capability-miss"
        return ("pass" if ok else "fail"), evidence
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def p4_problem(output):
    if not isinstance(output, dict):
        return "output is not a JSON object"
    for field in P4_REQUIRED:
        if field not in output:
            return "missing P4 field %s" % field
    if "outcome" not in output and not (output.get("no_verdict") is True and "no_verdict_reason" in output):
        return "missing P4 field outcome (or no_verdict with no_verdict_reason)"
    return p4_type_problem(output)


def _is_int(x):
    return isinstance(x, int) and not isinstance(x, bool)


def _is_number(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def _rows(output, field, keys, seq=True):
    rows = output[field]
    if not isinstance(rows, list):
        return "P4 field %s is not a list" % field
    for i, row in enumerate(rows):
        if not isinstance(row, dict):
            return "P4 field %s[%d] is not an object" % (field, i)
        for key in keys:
            if key not in row:
                return "P4 field %s[%d] has no %s" % (field, i, key)
        if seq and not _is_int(row["seq"]):
            return "P4 field %s[%d].seq is not an integer" % (field, i)
    return None


def p4_type_problem(output):
    """Types of the pinned P4 fields; a malformed value is a crash, never an outcome."""
    if not isinstance(output["case_id"], str) or not output["case_id"]:
        return "P4 field case_id is not a non-empty string"
    if "outcome" in output:
        if not isinstance(output["outcome"], dict):
            return "P4 field outcome is not an object"
    elif not isinstance(output["no_verdict_reason"], str) or not output["no_verdict_reason"]:
        return "P4 field no_verdict_reason is not a non-empty string"
    values = output["values"]
    if not isinstance(values, dict) or not all(_is_number(v) for v in values.values()):
        return "P4 field values is not an object of numbers"
    artifacts = output["artifacts"]
    if not isinstance(artifacts, dict) or not all(isinstance(v, str) and v for v in artifacts.values()):
        return "P4 field artifacts is not an object of paths"
    problem = (_rows(output, "code_steps", ("name", "inputs", "output"), seq=False)
               or _rows(output, "tool_calls", ("seq", "name", "kind", "arguments", "result", "denied"))
               or _rows(output, "rule_results", ("seq", "rule_id", "fired", "effect"))
               or _rows(output, "decisions", ("seq", "actor", "label_set", "before", "after")))
    if problem:
        return problem
    for i, row in enumerate(output["code_steps"]):
        if not isinstance(row["name"], str) or not isinstance(row["inputs"], dict):
            return "P4 field code_steps[%d] has a malformed name or inputs" % i
    for i, row in enumerate(output["tool_calls"]):
        if not isinstance(row["name"], str) or not isinstance(row["denied"], bool):
            return "P4 field tool_calls[%d] has a malformed name or denied" % i
    for i, row in enumerate(output["rule_results"]):
        if not isinstance(row["fired"], bool):
            return "P4 field rule_results[%d].fired is not a boolean" % i
    if not _is_int(output["model_calls"]) or output["model_calls"] < 0:
        return "P4 field model_calls is not a non-negative integer"
    trail = output["decision_trail"]
    if not isinstance(trail, list) or not all(isinstance(s, str) for s in trail):
        return "P4 field decision_trail is not a list of step names"
    if not isinstance(output["activity_log"], str) or not output["activity_log"]:
        return "P4 field activity_log is not a path"
    if output["exit"] not in ("completed", "crashed", "refused"):
        return "P4 field exit is not completed, crashed or refused"
    return None


def run_early(run, command, case_path):
    """Run CMD... <case> with working folder R (pins I1, I2). Returns the output dict."""
    case_doc, _ = _records.load_json(case_path)
    case_id = case_doc.get("case_id") if isinstance(case_doc, dict) else None

    def crashed(error):
        return {"case_id": case_id, "exit": "crashed", "error": error}

    env = _records.subprocess_env(run, "early")
    if not os.path.isdir(env["AB_ACTIVITY_DIR"]):
        os.makedirs(env["AB_ACTIVITY_DIR"])
    try:
        proc = subprocess.run(list(command) + [os.path.abspath(case_path)], cwd=run, env=env,
                              stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, timeout=300)
    except subprocess.TimeoutExpired:
        return crashed("timeout after 300 seconds")
    except OSError as err:
        return crashed("could not start: %s" % err)
    if proc.returncode != 0:
        return crashed("exit code %d" % proc.returncode)
    try:
        output = json.loads(proc.stdout.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as err:
        return crashed("stdout is not exactly one JSON object (%s)" % err)
    if not isinstance(output, dict):
        return crashed("stdout is not exactly one JSON object")
    outside = False
    if output.get("activity_log") is not None:
        rel = _records._to_run_relative(output["activity_log"], run, run)
        if rel is None:
            outside = True
        else:
            output["activity_log"] = rel
    if isinstance(output.get("artifacts"), dict):
        for name in sorted(output["artifacts"]):
            rel = _records._to_run_relative(output["artifacts"][name], run, run)
            if rel is None:
                outside = True
            else:
                output["artifacts"][name] = rel
    if outside:
        output["exit"] = "crashed"
        output["error"] = "path outside the run folder"
    return output


def git_commit(source):
    try:
        proc = subprocess.run(["git", "-C", source, "rev-parse", "HEAD"], stdin=subprocess.DEVNULL,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    sha = proc.stdout.decode("utf-8", "replace").strip()
    if proc.returncode != 0 or not re.fullmatch(r"[0-9a-f]{40,64}", sha):
        return None
    return sha


def check_target(run, brief):
    """Return (target, reasons) per pins P9, G26, I13."""
    target = brief.get("target") if isinstance(brief, dict) else None
    if (not isinstance(target, dict) or not isinstance(target.get("environment"), str)
            or not target.get("environment") or not isinstance(target.get("systems"), list)):
        return target, ["target mismatch: brief.json has no readable target.environment and target.systems"]
    reasons = []
    ledger, err = _records.load_json(os.path.join(run, "access.json"))
    entries = {}
    if err is None and isinstance(ledger, dict) and isinstance(ledger.get("systems"), list):
        for s in ledger["systems"]:
            if isinstance(s, dict) and isinstance(s.get("name"), str):
                entries[s["name"]] = s
    for system in target["systems"]:
        if not isinstance(system, dict):
            reasons.append("target mismatch: unreadable system entry")
            continue
        name, mode = system.get("name"), system.get("mode")
        entry = entries.get(name)
        state = entry.get("state") if entry else None
        if mode == "real":
            if entry is None:
                reasons.append("target mismatch: real system %s has no access.json entry" % name)
            elif state not in ("extract", "read-only", "live"):
                reasons.append("target mismatch: real system %s has access %s" % (name, state))
            elif not nonempty_file_in_run(run, entry.get("evidence")):
                reasons.append("target mismatch: real system %s recorded %s without an existing evidence file"
                               % (name, state))
        elif mode == "stubbed":
            if state in ("extract", "read-only", "live"):
                reasons.append("target mismatch: stubbed system %s recorded %s" % (name, state))
        else:
            reasons.append("target mismatch: system %s has mode %s" % (name, mode))
    return target, reasons


def read_tools(run, args):
    """Return (tools record, reasons)."""
    empty = {"source": None, "attestor": None, "evidence": None, "reachable": []}
    if args.tools_file:
        source, attestor, path = "observed", None, args.tools_file
    elif args.attest is not None or args.evidence is not None:
        source, attestor, path = "attested", args.attest, args.evidence
        if not attestor or not path or not nonempty_file_in_run(run, path):
            empty.update({"source": "attested", "attestor": attestor, "evidence": path})
            return empty, ["attested tools rejected: attestor and an existing non-empty evidence file needed"]
    else:
        return empty, ["reachable tools not observed or attested"]
    doc, err = _records.load_json(run_path(run, path))
    tools = doc.get("tools") if isinstance(doc, dict) else None
    rec = {"source": source, "attestor": attestor, "evidence": rel_or_none(run, path) or path, "reachable": []}
    if err or not isinstance(tools, list):
        return rec, ["reachable tools file unreadable: %s" % (err or "no tools list")]
    for t in tools:
        if not isinstance(t, dict) or t.get("kind") not in ("read", "write") or not isinstance(t.get("name"), str):
            return rec, ["reachable tools file has a malformed entry: %r" % (t,)]
        rec["reachable"].append({"name": t["name"], "kind": t["kind"]})
    return rec, []


def read_probes_file(run, path):
    if not path:
        return {}, None, []
    doc, err = _records.load_json(run_path(run, path))
    if err or not isinstance(doc, dict) or not isinstance(doc.get("probes", {}), dict):
        return {}, None, ["probes file unreadable: %s" % (err or "no probes object")]
    traces = doc.get("traces_arrived")
    if traces not in (True, False, None):
        traces = None
    return doc.get("probes", {}), traces, []


def first_ready_case(run):
    manifest, err = _records.load_json(os.path.join(run, "frozen", "MANIFEST.json"))
    if err:
        raise Usage("frozen manifest unreadable: %s" % err)
    version = manifest.get("current_version") if isinstance(manifest, dict) else None
    entry = None
    for v in (manifest.get("versions") or []) if isinstance(manifest, dict) else []:
        if isinstance(v, dict) and v.get("version") == version:
            entry = v
    if entry is None or not isinstance(entry.get("files"), list):
        raise Usage("frozen manifest has no entry for current_version %r" % (version,))
    files = [f for f in entry["files"] if isinstance(f, dict)]
    cases = sorted((f for f in files if f.get("kind") == "case" and f.get("state") == "ready"),
                   key=lambda f: f.get("path", ""))
    if not cases:
        raise Usage("no ready case in manifest version %r" % (version,))
    case = cases[0]
    expected = None
    for f in files:
        if f.get("kind") == "expected" and f.get("case_id") == case.get("case_id"):
            expected = f
    return version, case, expected


def compare(run, brief, case_entry, expected_entry, case_doc, expected_doc, output):
    """Return the comparison {"state", "reason"} (pins C5, H6, I5, G22)."""
    manifest = {
        "case": {"state": case_entry.get("state"), "pending_reason": case_entry.get("pending_reason")},
        "expected": {"state": expected_entry.get("state") if expected_entry else None,
                     "pending_reason": expected_entry.get("pending_reason") if expected_entry else None},
    }
    case_id = case_entry.get("case_id")
    items, _ = _records.load_json(os.path.join(run, "open-items.json"))
    open_item = None
    for item in (items.get("items") or []) if isinstance(items, dict) else []:
        if (isinstance(item, dict) and item.get("status") == "open"
                and case_id in (item.get("test_case_ids") or [])):
            open_item = item
            break
    pending = [k for k in ("case", "expected") if manifest[k]["state"] != "ready"]
    if pending:
        k = pending[0]
        return {"state": "pending", "reason": "%s manifest entry %s: %s"
                % (k, manifest[k]["state"], manifest[k]["pending_reason"])}
    if open_item is not None:
        try:
            import openitems
        except ImportError:
            return {"state": "pending", "reason": "openitems.py not available"}
        got = openitems.safe_path_observed(open_item, output, brief)
        ok, why = (got if isinstance(got, tuple) else (got, ""))
        if ok is True:
            return {"state": "pass", "reason": "open item %s: safe path observed %s" % (open_item.get("id"), why)}
        return {"state": "fail", "reason": "open item %s: safe path not observed %s" % (open_item.get("id"), why)}
    try:
        import compare as compare_mod
    except ImportError:
        return {"state": "pending", "reason": "compare.py not available"}
    tolerance = expected_doc.get("tolerance") if isinstance(expected_doc, dict) else None
    if tolerance is None:
        tolerance = brief.get("amount_tolerance")
    if tolerance is None and isinstance(case_doc, dict) and case_doc.get("source") == "agent-design-suite":
        tolerance = 0.01
    result = compare_mod.compare_case(case_doc, expected_doc, output, tolerance, manifest=manifest, run=run)
    if not isinstance(result, dict) or result.get("state") not in ("pass", "fail", "pending"):
        return {"state": "pending", "reason": "compare_case returned an unreadable result"}
    return {"state": result["state"], "reason": result.get("reason")}


# ---------------------------------------------------------------- record

def record(args):
    run = args.run
    if not os.path.isdir(run):
        raise Usage("--run is not a folder: %s" % run)
    if not args.command:
        raise Usage("--command needs the run-this-case command")
    label = "early" if args.early else "final"
    if args.early and not args.source:
        raise Usage("--source is required with --early")
    if args.tools_file and (args.attest is not None or args.evidence is not None):
        raise Usage("give --tools-file or --attest/--evidence, not both")

    version, case_entry, expected_entry = first_ready_case(run)
    ok, why = _records.entrypoint_in_package(run, args.command)
    if not ok:
        raise Usage(why if why == "entrypoint outside the package" else "entrypoint outside the package (%s)" % why)

    rel_record = "records/e2e-%s.json" % label
    if args.early:
        existing, err = _records.load_json(os.path.join(run, rel_record))
        if err is None and isinstance(existing, dict):
            mv = existing.get("manifest_version")
            if isinstance(mv, int) and isinstance(version, int) and mv < version:
                print("refused: e2e-early.json exists for manifest version %d below the current %d; "
                      "the early record is never re-created after a re-freeze" % (mv, version))
                return _records.FAILED

    brief, brief_err = _records.load_json(os.path.join(run, "brief.json"))
    if not isinstance(brief, dict):
        brief = {}
    reasons = []
    if brief_err:
        reasons.append("brief.json unreadable: %s" % brief_err)

    case_path = os.path.join(run, "frozen", case_entry["path"])
    case_doc, _ = _records.load_json(case_path)
    expected_doc, expected_path = None, None
    if expected_entry is not None:
        expected_path = os.path.join(run, "frozen", expected_entry["path"])
        expected_doc, _ = _records.load_json(expected_path)
    case_id = case_entry.get("case_id")

    # Code identity
    if args.early:
        sha = git_commit(args.source)
        code_identity = {"commit": sha if sha else NMI}
        if not sha:
            reasons.append("commit needs_more_information: git rev-parse HEAD failed in --source")
    else:
        sha_path = os.path.join(run, "package.sha256")
        pkg = None
        if os.path.isfile(sha_path):
            with open(sha_path, encoding="utf-8") as f:
                pkg = f.readline().strip() or None
        code_identity = {"package_sha256": pkg if pkg else NMI}
        if not pkg:
            reasons.append("package.sha256 missing")

    # Run the case
    output = None
    if args.early:
        output = run_early(run, args.command, case_path)
    elif code_identity["package_sha256"] != NMI:
        try:
            output, _stderr = _records.run_from_copy(run, args.command, case_path, "final")
        except RuntimeError as err:
            reasons.append(str(err))
        except ValueError as err:
            raise Usage(str(err))
    output_rel = None
    if output is not None:
        problem = None if output.get("exit") == "crashed" else p4_problem(output)
        if problem:
            output = {"case_id": case_id, "exit": "crashed", "error": problem}
        out_dir = os.path.join(run, "outputs", label)
        if not os.path.isdir(out_dir):
            os.makedirs(out_dir)
        output_rel = "outputs/%s/%s.json" % (label, case_id)
        _records.write_record(os.path.join(run, output_rel), output)
        if output.get("exit") == "crashed":
            reasons.append("case crashed: %s" % output.get("error"))

    # Comparison
    if output is None:
        comparison = {"state": "fail", "reason": "case not run"}
    else:
        comparison = compare(run, brief, case_entry, expected_entry, case_doc, expected_doc, output)
        if output.get("exit") == "crashed" and comparison["state"] == "pass":
            comparison = {"state": "fail", "reason": "case crashed"}
    if comparison["state"] != "pass":
        reasons.append("comparison %s: %s" % (comparison["state"], comparison["reason"]))

    # Target
    target, target_reasons = check_target(run, brief)
    reasons.extend(target_reasons)

    # Probes
    caps, caps_missing = load_capabilities(run, brief)
    probes_in, traces, probe_errs = read_probes_file(run, args.probes_file)
    reasons.extend(probe_errs)
    tool_calls = output.get("tool_calls") if isinstance(output, dict) else None
    denials = sum(1 for t in tool_calls if isinstance(t, dict) and t.get("denied") is True) \
        if isinstance(tool_calls, list) else 0
    probes = []
    undecidable = False
    for name in PROBES:
        applies, reason = applicability(name, caps, caps_missing)
        result, evidence = "not-run", None
        undecided = args.early and applies == NMI
        if applies == NMI and not args.early:
            undecidable = True
        elif undecided:
            # The agent judges, then continues: an undecidable probe is read as not
            # applicable, with the reasoning kept on the probe and in the run log.
            applies = False
            reason = "not applicable: %s, so the probe is not run; recorded for the end-of-run review" % reason
        elif name in OBSERVED:
            if name == "python39":
                result, evidence = probe_python39()
            elif name in ("git", "curl"):
                ok, evidence = tool_version(name, ["--version"])
                result = "pass" if ok else "fail"
            elif name == "entrypoint_start":
                good = isinstance(output, dict) and output.get("exit") != "crashed"
                result = "pass" if good else "fail"
                evidence = output_rel
            elif name == "read_only_zero_denials":
                if isinstance(tool_calls, list):
                    result = "pass" if denials == 0 else "fail"
                    evidence = "%d denied tool calls" % denials
                else:
                    evidence = "no tool_calls in the case output"
            elif name == "eval_chain":
                result, evidence = probe_eval_chain(run, output, label)
        else:
            entry = probes_in.get(name)
            if isinstance(entry, dict) and entry.get("result") in ("pass", "fail"):
                if entry.get("source") == "attested" and (
                        not entry.get("attestor") or not nonempty_file_in_run(run, entry.get("evidence"))):
                    evidence = "attested result rejected: attestor and an existing non-empty evidence file needed"
                else:
                    result = entry["result"]
                    evidence = entry.get("evidence") if entry.get("evidence") is not None else entry.get("detail")
        if applies is True and result != "pass":
            reasons.append("probe %s %s%s" % (name, "failed" if result == "fail" else "has no result",
                                              ": %s" % evidence if isinstance(evidence, str) and evidence else ""))
        if applies == NMI:
            reasons.append("probe %s applicability needs_more_information: %s" % (name, reason))
        entry = {"name": name, "applies": applies, "reason": reason, "result": result,
                 "evidence": evidence}
        if undecided:
            entry["applicability"] = NMI
        probes.append(entry)

    # Tools and denials
    tools, tool_reasons = read_tools(run, args)
    reasons.extend(tool_reasons)
    host = brief.get("agent_host") if isinstance(brief.get("agent_host"), dict) else {}
    allow = host.get("tool_allow_list") if isinstance(host.get("tool_allow_list"), list) else []
    for t in tools["reachable"]:
        if t["kind"] == "write" and t["name"] not in allow:
            reasons.append("write tool outside the allow-list: %s" % t["name"])
    if denials > 0:
        reasons.append("denials: %d tool calls denied" % denials)

    header = _records.record_header(_records.SKILL_VERSION, {}, code_identity)

    # Timing (early only)
    round2 = None
    if args.early:
        try:
            events = log.read(run)
        except ValueError as err:
            events = []
            reasons.append("run log unreadable: %s" % err)
        for ev in events:
            payload = ev.get("payload") if isinstance(ev, dict) else None
            if ev.get("type") == "round.started" and isinstance(payload, dict) and payload.get("round") == 2:
                round2 = payload.get("first_launch")
                break
        if round2 is not None and header["produced_at"] > round2:
            reasons.append("early record produced at %s, after the second build round's first launch %s"
                           % (header["produced_at"], round2))

    inputs = _records.standard_input_hashes(run)
    inputs["frozen/" + case_entry["path"]] = _records.sha256_file(case_path)
    if expected_path and os.path.isfile(expected_path):
        inputs["frozen/" + expected_entry["path"]] = _records.sha256_file(expected_path)
    header["input_hashes"] = inputs

    if undecidable:
        verdict, code = "needs_person", _records.NEEDS_PERSON
    elif reasons:
        verdict, code = "fail", _records.FAILED
    else:
        verdict, code = "pass", _records.OK

    rec = dict(header)
    rec.update({
        "verdict": verdict, "reasons": reasons, "manifest_version": version,
        "target": target, "case_id": case_id, "output": output_rel, "comparison": comparison,
        "probes": probes, "traces_arrived": traces, "tools": tools, "denials": denials,
        "round2_first_launch": round2,
    })
    _records.write_counted(run, rel_record, rec)
    payload = {"label": label, "path": rel_record, "verdict": verdict, "case_id": case_id,
               "manifest_version": version}
    not_applicable = [p["name"] for p in probes if p.get("applicability") == NMI]
    if not_applicable:
        payload["probes_read_not_applicable"] = not_applicable
    log.append(run, "e2e.recorded", payload)
    print("%s: %s" % (verdict, "; ".join(reasons) if reasons else "all checks passed"))
    return code


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    command = None
    if "--command" in argv:
        i = argv.index("--command")
        command, argv = argv[i + 1:], argv[:i]
    parser = argparse.ArgumentParser(prog="e2e.py", description="Early and final end-to-end records.")
    sub = parser.add_subparsers(dest="sub")
    p_split = sub.add_parser("check-split")
    p_split.add_argument("--split", required=True)
    p_rec = sub.add_parser("record")
    p_rec.add_argument("--run", required=True)
    which = p_rec.add_mutually_exclusive_group(required=True)
    which.add_argument("--early", action="store_true")
    which.add_argument("--final", action="store_true")
    p_rec.add_argument("--source")
    p_rec.add_argument("--tools-file")
    p_rec.add_argument("--attest")
    p_rec.add_argument("--evidence")
    p_rec.add_argument("--probes-file")
    args = parser.parse_args(argv)
    if args.sub == "check-split":
        if command is not None:
            parser.error("--command is not a check-split argument")
        return check_split(args.split)
    if args.sub == "record":
        args.command = command
        try:
            return record(args)
        except Usage as err:
            sys.stderr.write("%s\n" % err)
            print(str(err))
            return _records.USAGE
    parser.print_usage(sys.stderr)
    return _records.USAGE


if __name__ == "__main__":
    sys.exit(main())
