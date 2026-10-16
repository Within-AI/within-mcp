"""Stage 12 (sandbox-ship) clearance rule: is the Sandbox stage clearable, from evidence.

Reads the stage's spec (schema sandbox-ship-spec/v1) as plain YAML, its live_run block and package.files[] only.
Two routes clear it:
  A. live evidence: live_run.status is "run", the evidence file exists and matches its sha256, and the package hash
     it records equals the package hash computed now. The evidence file (JSON) states that the container was built
     and started, that the Test Suite ran inside it, and the results.
  B. accepted without a live run: live_run.status is "not_run", open_items lists "live-sandbox-run", and
     live_run.approved_by names a person.
Both routes need the spec's stops-before-production statement. Clearing this stage is never a claim about
production launch: the Production Readiness Gate is a separate decision.

The package hash is the one rule in scripts/_sandbox_ship_build.package_hash (sha256 over the sorted lines
"dest<TAB>file-sha256", each line ending in a newline) for every entry of package.files, the file sha taken from the
entry's src, relative to the run root, plus the built .dockerignore in the package folder and, unless package.build
is given (the package then brings its own Dockerfiles as listed files), the built Dockerfile. A missing built
package is a setup error: build the package first.
"""
import hashlib
import json
import os
import sys

OPEN_ITEM = "live-sandbox-run"
SCHEMA = "sandbox-ship-spec/v1"
NEITHER = ("Passing tests alone clear nothing. To clear this stage, give one of two things: either live evidence "
           "(live_run.status run, an evidence file that matches its sha256 and records this package's hash), or a "
           "not_run status with the open item live-sandbox-run listed and a person named in live_run.approved_by.")


def _shared_builder():
    """Load the builder library by file path, once, under one module name: the package hash has one definition."""
    name = "_sandbox_ship_build_shared"
    if name in sys.modules:
        return sys.modules[name]
    import importlib.util
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts",
                        "_sandbox_ship_build.py")
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


class SetupError(Exception):
    pass


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def load_spec(root, rel):
    path = os.path.join(root, rel)
    if not os.path.isfile(path):
        raise SetupError("the spec %s does not exist" % rel)
    try:
        import yaml
    except ImportError:
        raise SetupError("PyYAML is required but not importable")
    try:
        with open(path, encoding="utf-8") as f:
            doc = yaml.safe_load(f)
    except Exception as exc:  # noqa: BLE001
        raise SetupError("the spec %s cannot be parsed: %s" % (rel, exc))
    if not isinstance(doc, dict):
        raise SetupError("the spec %s is not a mapping" % rel)
    return doc


def package_hash(root, files, package_root=None, package_build=None):
    """(hash, problems): the package hash computed now from package.files. PACKAGE_BUILD is the spec's
    package.build (None for the default stack, whose Dockerfile the builder generates)."""
    entries, problems = [], []
    if not isinstance(files, list) or not files:
        return None, ["package.files lists no files, so there is no package to hash"]
    for i, ent in enumerate(files):
        src = (ent or {}).get("src") if isinstance(ent, dict) else None
        dest = (ent or {}).get("dest") if isinstance(ent, dict) else None
        if not src or not dest:
            problems.append("package.files entry %d needs both src and dest" % (i + 1))
            continue
        p = os.path.join(root, str(src))
        if not os.path.isfile(p):
            problems.append("package file %s is missing" % src)
            continue
        entries.append((dest, sha256_file(p)))
    if problems:
        return None, problems
    build = _shared_builder()
    try:
        gen = build.generated_hashes(os.path.join(root, str(package_root)) if package_root else None, package_build)
    except build.BuildError as exc:
        raise SetupError(str(exc))
    return build.package_hash(entries, gen), []


def _text(v):
    return v.strip() if isinstance(v, str) else ""


def route_a(root, live, pkg_now):
    """Findings for route A (empty list means route A holds)."""
    out = []
    ev = live.get("evidence")
    if not isinstance(ev, dict) or not _text(ev.get("path")) or not _text(ev.get("sha256")):
        return ["live_run.evidence needs a path and a sha256"]
    path = os.path.join(root, ev["path"])
    if not os.path.isfile(path):
        return ["the live evidence file %s does not exist" % ev["path"]]
    now = sha256_file(path)
    if now != ev["sha256"]:
        return ["the live evidence file %s is not the file the spec pins (pinned %s, now %s)"
                % (ev["path"], str(ev["sha256"])[:12], now[:12])]
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
    except Exception as exc:  # noqa: BLE001
        return ["the live evidence file %s is not readable JSON: %s" % (ev["path"], exc)]
    if not isinstance(doc, dict):
        return ["the live evidence file %s must be a JSON object" % ev["path"]]
    for key, what in (("container_built", "the container was built"),
                      ("container_started", "the container was started"),
                      ("test_suite_ran_in_container", "the Test Suite ran inside the container")):
        if doc.get(key) is not True:
            out.append("the live evidence does not state that %s (%s must be true)" % (what, key))
    if not doc.get("results"):
        out.append("the live evidence records no results (results is empty or missing)")
    recorded = doc.get("package_sha256")
    if not _text(recorded):
        out.append("the live evidence does not record the package hash it ran (package_sha256)")
    elif recorded != pkg_now:
        out.append("the package changed after the live evidence was recorded (evidence %s, package now %s); "
                   "run the Sandbox again on this package" % (str(recorded)[:12], str(pkg_now)[:12]))
    if _text(ev.get("package_sha256")) and ev["package_sha256"] != pkg_now:
        out.append("the spec's evidence.package_sha256 %s is not this package's hash %s"
                   % (str(ev["package_sha256"])[:12], str(pkg_now)[:12]))
    return out


def route_b(spec, live):
    out = []
    items = spec.get("open_items")
    listed = [(i.get("id") if isinstance(i, dict) else i) for i in items] if isinstance(items, list) else []
    if OPEN_ITEM not in listed:
        out.append("open_items does not include %s, so a stage without a live run is not accepted" % OPEN_ITEM)
    if not _text(live.get("approved_by")):
        out.append("live_run.approved_by names no person; a stage without a live run needs a person's approval")
    return out


def handover(spec):
    """Per system: what differs between the Sandbox and production."""
    rows = []
    for s in spec.get("systems") or []:
        if not isinstance(s, dict):
            continue
        name = s.get("name", "(unnamed system)")
        if s.get("binding") == "real":
            rows.append("%s: Sandbox uses real access (grant recorded, data labelled %s); confirm the production "
                        "grant matches it" % (name, s.get("data_label", "client")))
        else:
            rows.append("%s: Sandbox uses a stub with %s data (%s); production needs the real system connected"
                        % (name, s.get("data_label", "synthetic"), _text(s.get("reason")) or "no reason stated"))
    return rows


def evaluate(root, spec_rel):
    """(route, findings, handover_rows). route is "A", "B" or None."""
    spec = load_spec(root, spec_rel)
    findings = []
    if spec.get("schema") != SCHEMA:
        findings.append("schema is %r, not %s" % (spec.get("schema"), SCHEMA))
    stops = spec.get("stops_before_production")
    if not (isinstance(stops, dict) and _text(stops.get("statement"))):
        findings.append("the spec has no stops_before_production statement")
    live = spec.get("live_run")
    live = live if isinstance(live, dict) else {}
    pkg = spec.get("package") if isinstance(spec.get("package"), dict) else {}
    status = live.get("status")
    route, route_findings = None, []
    if status == "run":
        pkg_now, pkg_problems = package_hash(root, pkg.get("files"), pkg.get("root"), pkg.get("build"))
        route_findings = pkg_problems or route_a(root, live, pkg_now)
        route = "A"
    elif status == "not_run":
        route_findings = route_b(spec, live)
        route = "B"
    else:
        route_findings = ["live_run.status is %r; it must be run or not_run" % status]
        route_findings.append(NEITHER)
    findings.extend(route_findings)
    return (route if not findings else None), findings, handover(spec)
