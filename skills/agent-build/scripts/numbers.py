"""Agent Build: numbers ledger coverage over a label's raw outputs (criterion 11; pins P3, G34, H12, I1).

Usage: numbers.py --run R --label LABEL

Every value an output reports must be a ledger name, come from a code step named after the
entry's computed_by, reproduce when numbers.py re-runs that callable on the step's recorded
inputs (from a fresh hashed copy of R/package), and match the frozen expectation. Always writes
R/records/numbers.json. Exit 0 pass, 1 fail, 3 an empty ledger a person has not confirmed,
2 usage or unreadable inputs (nothing written).

Entry point for the exit gate: evaluate(run, label) -> record dict (never writes).
"""
import os
import sys

# This file's pinned name shadows the standard library's `numbers` module whenever scripts/ is
# first on sys.path (decimal and fractions import `numbers`). So that those imports keep
# working, the module first re-exports the standard library's names (Number, Complex, Real,
# Rational, Integral) and imports everything else lazily, inside functions.


def _stdlib_numbers():
    import importlib.util
    path = os.path.join(os.path.dirname(os.__file__), "numbers.py")
    spec = importlib.util.spec_from_file_location("_ab_stdlib_numbers", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_STD = _stdlib_numbers()
for _name in _STD.__all__:
    globals()[_name] = getattr(_STD, _name)
__all__ = list(_STD.__all__)

HERE = os.path.dirname(os.path.abspath(__file__))
RERUN_TIMEOUT = 60
MODULE_RUNNER = (
    "import importlib.util, json, os, sys\n"
    "path, name = sys.argv[1], sys.argv[2]\n"
    "sys.path.insert(0, os.path.dirname(path))\n"
    "spec = importlib.util.spec_from_file_location('ab_numbers_target', path)\n"
    "mod = importlib.util.module_from_spec(spec)\n"
    "spec.loader.exec_module(mod)\n"
    "print(json.dumps(getattr(mod, name)(**json.load(sys.stdin))))\n"
)


class PackageChanged(Exception):
    pass


def _deps():
    """(_records, core), imported on first use (see the note at the top)."""
    if HERE not in sys.path:
        sys.path.insert(0, HERE)
    import _records
    import core
    return _records, core


def step_name(computed_by):
    if not isinstance(computed_by, dict):
        return None
    if isinstance(computed_by.get("command"), list) and computed_by["command"]:
        return " ".join(str(a) for a in computed_by["command"])
    if isinstance(computed_by.get("module"), str) and isinstance(computed_by.get("function"), str):
        return "%s:%s" % (computed_by["module"], computed_by["function"])
    return None


def _package_file(run, rel):
    """R/package/<rel> when it is a regular file inside the package, else None."""
    if not isinstance(rel, str) or not rel:
        return None
    package = os.path.join(run, "package")
    path = rel if os.path.isabs(rel) else os.path.join(package, rel)
    real, root = os.path.realpath(path), os.path.realpath(package)
    if not real.startswith(root.rstrip(os.sep) + os.sep) or not os.path.isfile(real):
        return None
    return real


def command_entrypoint(run, command):
    """Pins H2/I1: (path under R/package, None) for the command's entrypoint, else (None, reason).

    The entrypoint is the first argument, not an interpreter, that names an existing file
    (relative paths resolve against R/package, the re-run's working folder)."""
    _records, _ = _deps()
    package = os.path.join(run, "package")
    for arg in (str(a) for a in command):
        if _records._is_interpreter(arg):
            continue
        path = _package_file(run, arg)
        if path is not None:
            return path, None
        if os.path.isfile(arg if os.path.isabs(arg) else os.path.join(package, arg)):
            return None, "entrypoint outside the package (%s)" % arg
    return None, "entrypoint outside the package (no argument names a file under package/)"


def computed_by_problem(run, computed_by):
    """None when computed_by exists; else a reason."""
    import ast
    import shutil
    if step_name(computed_by) is None:
        return "computed_by is neither {module, function} nor {command: [...]}"
    if "command" in computed_by:
        first = str(computed_by["command"][0])
        if not (_package_file(run, first) or (os.path.isabs(first) and os.path.isfile(first)) or shutil.which(first)):
            return "command %r does not resolve" % first
        return command_entrypoint(run, computed_by["command"])[1]
    path = _package_file(run, computed_by["module"])
    if path is None:
        return "module %s not found under package/" % computed_by["module"]
    try:
        with open(path, encoding="utf-8") as f:
            tree = ast.parse(f.read())
    except (OSError, SyntaxError, UnicodeDecodeError) as err:
        return "module %s unreadable (%s)" % (computed_by["module"], err)
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == computed_by["function"]:
            return None
    return "module %s does not define function %s" % (computed_by["module"], computed_by["function"])


def rerun(run, computed_by, inputs):
    """Pin I1: re-run the callable from a fresh hashed copy of R/package. Returns (value, error)."""
    import json
    import shutil
    import subprocess
    import tempfile
    _records, _ = _deps()
    tmp = tempfile.mkdtemp()
    try:
        copy = os.path.join(tmp, "package")
        shutil.copytree(os.path.join(run, "package"), copy, symlinks=True)
        with open(os.path.join(run, "package.sha256"), encoding="utf-8") as f:
            recorded = f.readline().strip()
        if _records.package_hash(copy) != recorded:
            raise PackageChanged()
        package_root = os.path.realpath(os.path.join(run, "package"))
        if "command" in computed_by:
            argv = [str(a) for a in computed_by["command"]]
            entry, problem = command_entrypoint(run, argv)
            if problem:
                return None, problem
            for i, arg in enumerate(argv):
                if not _records._is_interpreter(arg) and _package_file(run, arg) == entry:
                    argv[i] = os.path.join(copy, os.path.relpath(entry, package_root))
                    break
        else:
            argv = [sys.executable, "-B", "-c", MODULE_RUNNER,
                    os.path.join(copy, computed_by["module"]), computed_by["function"]]
        try:
            proc = subprocess.run(argv, cwd=copy, env=_records.subprocess_env(run),
                                  input=json.dumps(inputs).encode("utf-8"),
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=RERUN_TIMEOUT)
        except subprocess.TimeoutExpired:
            return None, "re-run timed out after %d s" % RERUN_TIMEOUT
        except OSError as err:
            return None, "re-run could not start (%s)" % err
        if proc.returncode != 0:
            return None, "re-run exited %d" % proc.returncode
        try:
            return json.loads(proc.stdout.decode("utf-8")), None
        except (ValueError, UnicodeDecodeError):
            return None, "re-run printed no JSON value"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def same(a, b):
    _, core = _deps()
    da, db = core.to_decimal(a), core.to_decimal(b)
    return da is not None and db is not None and da == db


def matches_expected(ctx, cid, name, value):
    _, core = _deps()
    slot = ctx["frozen"].get(cid)
    if slot is None or slot["expected"] is None:
        return "pending"
    states = core.manifest_states(ctx, cid)
    if states["case"]["state"] == "pending" or states["expected"]["state"] == "pending":
        return "pending"
    # compare.compare_case (C5) decides whether the case is pending (pins H6, I10); the value itself
    # is then matched here, because a whole-case fail can come from the outcome, not the number.
    if core.compare(ctx, cid, ctx["outputs"].get(cid))["state"] == "pending":
        return "pending"
    wanted = [c.get("values", {}).get(name) for c in slot["expected"].get("candidates") or []
              if isinstance(c, dict) and isinstance(c.get("values"), dict) and name in c["values"]]
    if not wanted:
        return "pending"
    tol = core.tolerance(ctx, cid)
    dtol = core.to_decimal(tol) if tol is not None else None
    if tol is not None and (dtol is None or dtol < 0):
        return "pending"  # malformed numeric control (pins H18)
    got = core.to_decimal(value)
    for w in wanted:
        dw = core.to_decimal(w)
        if got is None or dw is None:
            continue
        if (dtol is None and got == dw) or (dtol is not None and abs(got - dw) <= dtol):
            return "pass"
    return "fail"


def produces_numbers(run, brief):
    _records, _ = _deps()
    override = brief.get("capabilities")
    if isinstance(override, dict) and "produces_numbers" in override:
        return override["produces_numbers"]
    caps, err = _records.load_json(os.path.join(run, "capabilities.json"))
    if err or not isinstance(caps, dict) or not isinstance(caps.get("capabilities"), dict):
        return None
    return caps["capabilities"].get("produces_numbers")


def evaluate(run, label):
    """Build the numbers.json record (pins G11). Never writes."""
    _, core = _deps()
    ctx = core.load_run(run, label)
    record = core.header(ctx)
    record["label"] = label  # numbers runs on the suite label and again on live (criterion 11)
    ledger = ctx["brief"].get("numbers")
    if ledger is None:
        ledger = []
    if not isinstance(ledger, list):
        raise core.InputError("brief.json numbers is not a list")
    record["ledger_empty"] = not ledger
    record["entries"] = []
    if core.package_changed(ctx):
        record.update({"verdict": "fail", "reasons": ["package changed"]})
        return record
    fails, person = [], []
    names = set(e.get("name") for e in ledger if isinstance(e, dict))

    for cid in sorted(ctx["outputs"]):
        values = ctx["outputs"][cid].get("values")
        if isinstance(values, dict):
            for key in sorted(values):
                if key not in names:
                    fails.append("%s: number not in ledger: %s" % (cid, key))

    if not ledger:
        produces = produces_numbers(run, ctx["brief"])
        if ctx["brief"].get("no_numbers_confirmed") is not True:
            person.append("empty ledger without no_numbers_confirmed")
        elif produces is None or produces == "needs_more_information":
            person.append("empty ledger: produces_numbers is %s" % ("missing" if produces is None else produces))
        elif produces is True:
            fails.append("empty ledger confirmed, but the briefing found numbers (produces_numbers: true)")
        elif produces is not False:
            person.append("empty ledger: produces_numbers is %r" % (produces,))

    try:
        for entry in ledger:
            if not isinstance(entry, dict):
                fails.append("ledger entry is not an object")
                continue
            record["entries"].append(assess_entry(ctx, entry, fails))
    except PackageChanged:
        record.update({"verdict": "fail", "reasons": ["package changed"]})
        return record

    reasons = fails + person
    verdict = "fail" if fails else ("needs_person" if person else "pass")
    record.update({"verdict": verdict, "reasons": reasons})
    return record


def assess_entry(ctx, entry, fails):
    run = ctx["run"]
    name = entry.get("name")
    computed_by = entry.get("computed_by")
    result = {"name": name, "computed_by": computed_by, "cases": []}
    problem = computed_by_problem(run, computed_by)
    if problem:
        fails.append("%s: %s" % (name, problem))
    listed = entry.get("test_case_ids")
    if not isinstance(listed, list) or not listed:
        fails.append("%s: test_case_ids is empty" % name)
        listed = []
    wanted_step = step_name(computed_by)
    for cid in listed:
        out = ctx["outputs"].get(cid)
        if out is None or out.get("exit") == "crashed":
            fails.append("%s: listed case %s has no usable output" % (name, cid))
        elif name not in (out.get("values") or {}):
            fails.append("%s: listed case %s reports no value" % (name, cid))
    for cid in sorted(ctx["outputs"]):
        out = ctx["outputs"][cid]
        values = out.get("values") if isinstance(out.get("values"), dict) else {}
        if name not in values:
            continue
        value = values[name]
        case = {"case_id": cid, "value": value, "step_found": False, "reproduced": False,
                "matches_expected": "pending"}
        step = None
        for s in out.get("code_steps") or []:
            if isinstance(s, dict) and s.get("name") == wanted_step and same(s.get("output"), value):
                step = s
                break
        if step is None:
            fails.append("%s: %s value %r has no matching code step %s" % (name, cid, value, wanted_step))
        else:
            case["step_found"] = True
            if problem is None:
                inputs = step.get("inputs") if isinstance(step.get("inputs"), dict) else {}
                got, err = rerun(run, computed_by, inputs)
                if err:
                    fails.append("%s: %s re-run failed: %s" % (name, cid, err))
                elif not same(got, value):
                    fails.append("%s: %s recorded inputs give %r, not %r" % (name, cid, got, value))
                else:
                    case["reproduced"] = True
        case["matches_expected"] = matches_expected(ctx, cid, name, value)
        if case["matches_expected"] != "pass":
            fails.append("%s: %s value %r against the frozen expectation: %s"
                         % (name, cid, value, case["matches_expected"]))
        result["cases"].append(case)
    return result


def main(argv=None):
    _, core = _deps()
    return core.cli(argv, "Numbers ledger coverage over a label's outputs.", [],
                    lambda a: evaluate(a.run, a.label), "records/numbers.json")


if __name__ == "__main__":
    sys.exit(main())
