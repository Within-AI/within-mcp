"""Agent Build: shared record helpers (pins C1, G7, H1, H2, H7, I1, I2).

Every script that reads or writes a run-folder record imports this module by
putting its own folder first on sys.path. Standard library only, 3.9-safe.

Names:
  load_json(path) -> (obj, err)
  write_record(path, obj)
  sha256_file(path) -> hex; sha256_json(obj) -> hex; normalize_json(obj) -> str
  now_iso() -> "YYYY-MM-DDTHH:MM:SSZ" (honours AB_FIXED_NOW)
  record_header(skill_version, inputs, code_identity) -> dict
  package_hash(dir) -> hex
  write_counted(run, relpath, obj)
  standard_input_hashes(run) -> dict
  entrypoint_in_package(run, command) -> (ok, reason)
  run_from_copy(run, command, case_path, label, timeout=300) -> (output, stderr)
  subprocess_env(run, label=None) -> dict
  SKILL_VERSION; exit codes OK, FAILED, USAGE, NEEDS_PERSON
"""
import datetime
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

SKILL_VERSION = "0.1.0"

OK = 0
FAILED = 1
USAGE = 2
NEEDS_PERSON = 3

STANDARD_INPUTS = ("brief.json", "capabilities.json", "frozen/MANIFEST.json")
INTERPRETERS = ("python", "python3", "node", "bash", "sh", "env")
_PYTHON_VERSIONED = re.compile(r"^python3\..+$")


def load_json(path):
    """Return (obj, None), or (None, reason) for a missing, unreadable or invalid file."""
    if not os.path.exists(path):
        return None, "missing: %s" % path
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except (OSError, UnicodeDecodeError) as err:
        return None, "unreadable: %s (%s)" % (path, err)
    try:
        return json.loads(text), None
    except ValueError as err:
        return None, "invalid JSON: %s (%s)" % (path, err)


def write_record(path, obj):
    """Write obj as indented, key-sorted JSON with a trailing newline, atomically."""
    folder = os.path.dirname(os.path.abspath(path))
    data = json.dumps(obj, indent=2, sort_keys=True) + "\n"
    fd, tmp = tempfile.mkstemp(dir=folder, prefix="." + os.path.basename(path) + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def normalize_json(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_json(obj):
    return hashlib.sha256(normalize_json(obj).encode("utf-8")).hexdigest()


def now_iso():
    fixed = os.environ.get("AB_FIXED_NOW")
    if fixed:
        return fixed
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def record_header(skill_version, inputs, code_identity):
    return {
        "produced_at": now_iso(),
        "skill_version": skill_version,
        "input_hashes": dict(inputs),
        "code_identity": dict(code_identity),
    }


def package_hash(dir):
    """sha256 over the sorted lines '<relative path>\\0<sha256 of file>\\n' (pin G7).

    A symlink anywhere under dir raises ValueError.
    """
    lines = []
    for root, dirs, files in os.walk(dir):
        for name in dirs + files:
            full = os.path.join(root, name)
            if os.path.islink(full):
                raise ValueError("symlink in package: %s" % os.path.relpath(full, dir).replace(os.sep, "/"))
        for name in files:
            full = os.path.join(root, name)
            if not os.path.isfile(full):
                continue
            rel = os.path.relpath(full, dir).replace(os.sep, "/")
            lines.append("%s\0%s\n" % (rel, sha256_file(full)))
    lines.sort()
    return hashlib.sha256("".join(lines).encode("utf-8")).hexdigest()


def write_counted(run, relpath, obj):
    """Write R/<relpath> with write_record, then append record.written to the run log (pin H1)."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import log  # sibling module; imported here because log.py imports this one

    path = os.path.join(run, relpath)
    parent = os.path.dirname(os.path.abspath(path))
    if not os.path.isdir(parent):
        os.makedirs(parent)
    write_record(path, obj)
    return log.append(run, "record.written", {"path": relpath, "sha256": sha256_file(path)})


def standard_input_hashes(run):
    """Map each of brief.json, capabilities.json, frozen/MANIFEST.json that exists in R to its sha256."""
    hashes = {}
    for name in STANDARD_INPUTS:
        path = os.path.join(run, name)
        if os.path.isfile(path):
            hashes[name] = sha256_file(path)
    return hashes


def _is_interpreter(arg):
    base = os.path.basename(arg)
    return base in INTERPRETERS or bool(_PYTHON_VERSIONED.match(base))


def _find_entrypoint(run, command):
    """Return (index, resolved path) of the entrypoint argument, or (None, None)."""
    for i, arg in enumerate(command):
        path = arg if os.path.isabs(arg) else os.path.join(run, arg)
        if os.path.isfile(path) and not _is_interpreter(arg):
            return i, path
    return None, None


def _under(path, folder):
    path = os.path.realpath(path)
    folder = os.path.realpath(folder)
    return path == folder or path.startswith(folder.rstrip(os.sep) + os.sep)


def entrypoint_in_package(run, command):
    """Pin H2: the entrypoint must resolve (realpath) under R/package."""
    _, path = _find_entrypoint(run, command)
    if path is None:
        return False, "no entrypoint found"
    if not _under(path, os.path.join(run, "package")):
        return False, "entrypoint outside the package"
    return True, ""


def subprocess_env(run, label=None):
    """The environment every subprocess the skill starts gets (pin I1)."""
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env.pop("PYTHONPATH", None)
    env.pop("AB_RUN_DIR", None)
    env.pop("AB_ACTIVITY_DIR", None)
    run_abs = os.path.abspath(run)
    env["AB_RUN_DIR"] = run_abs
    if label is not None:
        env["AB_ACTIVITY_DIR"] = os.path.join(run_abs, "outputs", label, "activity")
    return env


def _to_run_relative(value, copy, run):
    """Return an R-relative forward-slash path, or None when it lies outside R."""
    if not isinstance(value, str) or not value:
        return None
    path = value if os.path.isabs(value) else os.path.join(copy, value)
    if not _under(path, run):
        return None
    return os.path.relpath(os.path.realpath(path), os.path.realpath(run)).replace(os.sep, "/")


def run_from_copy(run, command, case_path, label, timeout=300):
    """Run a run-this-case command from a hashed copy of R/package (pins I1, I2).

    Raises ValueError (entrypoint) or RuntimeError("package changed").
    Returns (output dict, stderr str).
    """
    ok, reason = entrypoint_in_package(run, command)
    if not ok:
        raise ValueError(reason)
    index, entry = _find_entrypoint(run, command)
    package = os.path.join(run, "package")
    case_doc, _ = load_json(case_path)
    case_id = case_doc.get("case_id") if isinstance(case_doc, dict) else None

    def crashed(error):
        return {"case_id": case_id, "exit": "crashed", "error": error}

    tmp = tempfile.mkdtemp()
    try:
        copy = os.path.join(tmp, "package")
        shutil.copytree(package, copy, symlinks=True)
        recorded = None
        sha_path = os.path.join(run, "package.sha256")
        if os.path.isfile(sha_path):
            with open(sha_path, encoding="utf-8") as f:
                first = f.readline()
            recorded = first.strip()
        if recorded is None or package_hash(copy) != recorded:
            raise RuntimeError("package changed")

        rel = os.path.relpath(os.path.realpath(entry), os.path.realpath(package))
        argv = list(command)
        argv[index] = os.path.join(copy, rel)
        argv.append(os.path.abspath(case_path))
        env = subprocess_env(run, label)
        activity = env["AB_ACTIVITY_DIR"]
        if not os.path.isdir(activity):
            os.makedirs(activity)

        try:
            proc = subprocess.run(argv, cwd=copy, env=env, stdin=subprocess.DEVNULL,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  timeout=timeout)
        except subprocess.TimeoutExpired as err:
            stderr = err.stderr.decode("utf-8", "replace") if err.stderr else ""
            return crashed("timeout after %s seconds" % timeout), stderr
        except OSError as err:
            return crashed("could not start: %s" % err), ""
        stderr = proc.stderr.decode("utf-8", "replace")
        if proc.returncode != 0:
            return crashed("exit code %d" % proc.returncode), stderr
        try:
            output = json.loads(proc.stdout.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as err:
            return crashed("stdout is not exactly one JSON object (%s)" % err), stderr
        if not isinstance(output, dict):
            return crashed("stdout is not exactly one JSON object"), stderr

        outside = False
        if output.get("activity_log") is not None:
            rel_path = _to_run_relative(output["activity_log"], copy, run)
            if rel_path is None:
                outside = True
            else:
                output["activity_log"] = rel_path
        artifacts = output.get("artifacts")
        if isinstance(artifacts, dict):
            for name in sorted(artifacts):
                rel_path = _to_run_relative(artifacts[name], copy, run)
                if rel_path is None:
                    outside = True
                else:
                    artifacts[name] = rel_path
        if outside:
            output["exit"] = "crashed"
            output["error"] = "path outside the run folder"
        return output, stderr
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
