"""Stage 12 (sandbox-ship): the spec's binding to the cleared design and to stage 11's manifest.

The logic behind checks/check-sandbox-ship-spec.sh. Stdlib plus stage 11's own code: every file a pin reaches is
read through stage 11's readers in checks/_prebuild_sim.py (pinned_read, read_pinned, submitted_manifest), never
through a second reader. Each finding is one plain sentence that says what is missing and what to supply.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _prebuild_sim as pbs  # noqa: E402
import _sandbox_ship_client as sc  # noqa: E402


def _shared_builder():
    """Load the builder library by file path, once, under one module name: package.build has one reading."""
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

SCHEMA = "sandbox-ship-spec/v1"
SPEC_PATH = "output/sandbox-ship/spec.yaml"
KEYS = ("schema", "run_id", "client", "design_pins", "stage11_manifest", "systems", "capabilities", "package",
        "open_items", "adapter_risks", "honesty", "stops_before_production", "live_run")
CAPABILITIES = ("agent_execution", "runtime_log", "observability", "persistence", "llm_judge")
BINDINGS = ("real", "stub")
DATA_LABELS = ("client", "synthetic")
DISPOSITIONS = ("carried-forward", "resolved", "handover")
# Wording no field may carry, on top of stage 11's own list of banned claims.
EQUIVALENCE_CLAIMS = ("equivalent to the client environment", "identical to the client environment",
                      "same as the client environment", "matches the client environment",
                      "mirrors the client environment", "mirrors production", "equivalent to production",
                      "identical to production", "production-equivalent", "production equivalent",
                      "the same as production", "is the client environment", "cleared the production readiness gate",
                      "production readiness gate is cleared", "production readiness gate cleared")


def _text(v):
    return isinstance(v, str) and v.strip() != ""


def _pair(v):
    return isinstance(v, dict) and (v.get("path") or v.get("sha256")) is not None


def pin_findings(root, pair, what, kind_words):
    """Findings for one pin read through stage 11's pinned_read: a missing pin, a moved file, a stale pin each say
    so in their own words. KIND_WORDS names the thing to supply."""
    if not isinstance(pair, dict) or not _text(pair.get("path")):
        return ["%s has no pin: give the file's path and its sha256 (%s)" % (what, kind_words)]
    if not _text(pair.get("sha256")):
        return ["%s (%s) has a path but no sha256: add the sha256 of the file as it is now (%s)"
                % (what, pair["path"], kind_words)]
    r = pbs.pinned_read(root, pair, "bytes", what)
    if r.state == "held":
        return []
    if r.state == "missing":
        return ["%s is not where the spec says: no file at %s; it was moved or removed, so give its new path or "
                "restore it" % (what, pair["path"])]
    if r.state == "moved":
        return ["%s at %s is a stale pin: the file changed since the spec pinned it; re-read it and update the "
                "sha256 in the spec" % (what, pair["path"])]
    return ["%s at %s cannot be used: %s" % (what, pair["path"], r.reason)]


def _strings(doc):
    if isinstance(doc, str):
        yield doc
    elif isinstance(doc, dict):
        for v in doc.values():
            yield from _strings(v)
    elif isinstance(doc, list):
        for v in doc:
            yield from _strings(v)


def load_spec(root, rel):
    try:
        doc = pbs.load_yaml(os.path.join(root, rel))
    except pbs.PrebuildError:
        raise
    except OSError as exc:
        raise pbs.PrebuildError("cannot read the spec %s: %s" % (rel, exc))
    if not isinstance(doc, dict):
        raise pbs.PrebuildError("the spec %s is not a mapping; it should be the sandbox-ship-spec/v1 shape" % rel)
    return doc


def stage11(root, spec, events):
    """(findings, manifest or None): the spec's stage 11 manifest pin against the submitted manifest."""
    pair = spec.get("stage11_manifest")
    p = []
    if not isinstance(pair, dict) or not _text(pair.get("path")) or not _text(pair.get("sha256")):
        p.append("the spec pins no stage 11 manifest: give stage11_manifest with the path and sha256 of the "
                 "manifest stage 11 submitted")
    else:
        p.extend(pin_findings(root, pair, "the stage 11 manifest", "the file stage 11 submitted"))
    sub = pbs.submitted_manifest(root, events)
    if not sub.held:
        p.append("stage 11's submitted manifest cannot be used: %s" % sub.reason)
        return p, None
    sha = (sub.submission.get("payload") or {}).get("sha256")
    if isinstance(pair, dict) and _text(pair.get("path")) and (pair.get("path") != sub.rel or pair.get("sha256") != sha):
        if not p:
            p.append("the spec's stage 11 manifest pin (%s) is not the manifest stage 11 submitted (%s at sha256 "
                     "%s); pin the submitted one" % (pair.get("path"), sub.rel, str(sha)[:12]))
    for where, path, pinned, now in pbs.pin_problems(root, sub.manifest, responses=False):
        p.append("stage 11's manifest pins %s (%s), which %s; the design changed after stage 11, so stage 11 has to "
                 "run again" % (path, where, "is missing" if now is None else "has changed since it was pinned"))
    return p, sub.manifest


def design_findings(root, spec, manifest):
    p = []
    pins = spec.get("design_pins")
    if not isinstance(pins, list) or not pins:
        return ["the spec pins no design files: list design_pins with the path and sha256 of every design file the "
                "package is built from"]
    by_path = {}
    for i, pair in enumerate(pins):
        p.extend(pin_findings(root, pair, "design pin %d" % (i + 1), "a file of the cleared design"))
        if isinstance(pair, dict) and _text(pair.get("path")):
            by_path[pair["path"]] = pair
    if manifest is not None:
        want = []
        ds = manifest.get("design_set")
        if isinstance(ds, dict):
            want.append(("the design set", ds))
        for name, pair in sorted((manifest.get("inputs") or {}).items()):
            if isinstance(pair, dict):
                want.append(("design input %s" % name, pair))
        for what, pair in want:
            have = by_path.get(pair.get("path"))
            if have is None:
                p.append("the spec does not pin %s (%s), which stage 11 pins: add it to design_pins" % (what, pair.get("path")))
            elif have.get("sha256") != pair.get("sha256"):
                p.append("the spec pins %s (%s) at a different sha256 than stage 11 pinned: the pin is stale, or "
                         "the design changed after stage 11" % (what, pair.get("path")))
    return p


def system_findings(root, spec, manifest):
    p = []
    systems = spec.get("systems")
    if not isinstance(systems, list):
        return ["the spec has no systems list: give systems (an empty list when the agent reads no system)"]
    named = set()
    for i, s in enumerate(systems):
        if not isinstance(s, dict) or not _text(s.get("name")):
            p.append("system %d has no name: say which system it is" % (i + 1))
            continue
        name = s["name"]
        named.add(name)
        binding, label = s.get("binding"), s.get("data_label")
        if binding not in BINDINGS:
            p.append("system %s has no binding: say whether it is real (with a recorded access grant) or a stub "
                     "(with a reason)" % name)
            continue
        if label not in DATA_LABELS:
            p.append("system %s has no data label: say whether its data is client or synthetic" % name)
        if binding == "real":
            grant = s.get("grant")
            if not isinstance(grant, dict) or not _text(grant.get("path")):
                p.append("system %s is bound to real access but carries no grant file: give the path and sha256 of "
                         "the recorded grant, or bind it as a stub with a reason" % name)
            else:
                p.extend(pin_findings(root, grant, "the access grant of system %s" % name, "the recorded grant"))
            if label == "synthetic":
                p.append("system %s is bound to real access, so its data is client data, never synthetic: label it "
                         "client" % name)
        elif not _text(s.get("reason")):
            p.append("system %s is a stub with no reason: say why it is not bound to real access" % name)
    if manifest is not None:
        for s in manifest.get("systems") or []:
            sid = s.get("system_id") if isinstance(s, dict) else None
            if sid and sid not in named:
                p.append("stage 11 read system %s, but the spec gives it no binding: add it with a real grant or a "
                         "stub and a reason" % sid)
    return p


def capability_findings(spec):
    caps = spec.get("capabilities")
    if not isinstance(caps, dict):
        return ["the spec declares no capabilities: give all five (%s)" % ", ".join(CAPABILITIES)]
    p = []
    for k in CAPABILITIES:
        c = caps.get(k)
        if not isinstance(c, dict):
            p.append("capability %s is not declared: name its implementation, or mark it not applicable with a "
                     "reason" % k)
        elif c.get("not_applicable") is True:
            if not _text(c.get("reason")):
                p.append("capability %s is marked not applicable with no reason: say why it does not apply" % k)
        elif not _text(c.get("implementation")):
            p.append("capability %s names no implementation: name one, or mark it not applicable with a reason" % k)
    return p


def _carried(spec, key, wanted, what):
    p = []
    listed = spec.get(key)
    have = {}
    if isinstance(listed, list):
        for e in listed:
            if isinstance(e, dict) and _text(e.get("id")):
                have[e["id"]] = e
    elif listed is not None:
        p.append("the spec's %s must be a list" % key)
    for ident in wanted:
        e = have.get(ident)
        if e is None:
            p.append("stage 11 carries %s %s, which the spec does not list: add it to %s as carried-forward, "
                     "resolved or handover" % (what, ident, key))
        elif e.get("disposition") not in DISPOSITIONS:
            p.append("%s %s in the spec has no disposition: say carried-forward, resolved or handover" % (what, ident))
    return p


def carried_findings(root, spec, manifest):
    if manifest is None:
        return []
    p = []
    opens = [o.get("id") for o in manifest.get("opens") or [] if isinstance(o, dict) and o.get("id")]
    ro = manifest.get("review_open_item")
    if isinstance(ro, dict) and ro.get("id"):
        opens.append(ro["id"])
    p.extend(_carried(spec, "open_items", opens, "open item"))
    risks = []
    pair = manifest.get("adapter_risks")
    if pair is not None:
        try:
            doc = pbs.read_pinned(root, pair, "yaml", "stage 11's adapter risks") or {}
        except pbs.PrebuildError as exc:
            return p + ["stage 11's adapter risks cannot be read, so none can be carried: %s" % exc]
        risks = [r.get("id") for r in doc.get("risks") or [] if isinstance(r, dict) and r.get("id")]
    p.extend(_carried(spec, "adapter_risks", risks, "adapter risk"))
    return p


def package_build_findings(spec):
    """The optional package.build: its shape, and every Dockerfile and compose file it names is a package.files
    destination. Absent or null means the generated default Dockerfile and needs nothing."""
    pkg = spec.get("package")
    if not isinstance(pkg, dict):
        return []
    return _shared_builder().build_block_findings(pkg)


def statement_findings(spec):
    p = []
    for key, what in (("honesty", "honesty statement (what the Sandbox is and is not)"),
                      ("stops_before_production", "stops-before-production statement")):
        blk = spec.get(key)
        if not isinstance(blk, dict) or not _text(blk.get("statement")):
            p.append("the spec has no %s: write it under %s.statement" % (what, key))
    for text in _strings(spec):
        low = text.lower()
        hits = pbs.banned_claims_in(text) + [c for c in EQUIVALENCE_CLAIMS if c in low]
        for h in hits:
            p.append("the spec says %r; the Sandbox does not claim production readiness or equivalence to the "
                     "client environment, so reword that field" % h)
    return p


def check(root, rel=SPEC_PATH):
    spec = load_spec(root, rel)
    events = pbs.require_events(root, "the sandbox-ship spec check (the run id and stage 11's submission)")
    p = []
    missing = [k for k in KEYS if k not in spec]
    if missing:
        p.append("the spec is missing %s: fill them in from the template" % ", ".join(missing))
    if spec.get("schema") != SCHEMA:
        p.append("schema is %r, not %s" % (spec.get("schema"), SCHEMA))
    opened = next((e for e in events if e.get("type") == "run.opened"), {})
    if spec.get("run_id") != opened.get("run_id"):
        p.append("run_id %r is not the run's own run id %r" % (spec.get("run_id"), opened.get("run_id")))
    client = spec.get("client")
    if not isinstance(client, dict) or not _text(client.get("name")):
        p.append("the spec names no client: give client.name as the run header records it")
    else:
        snap = pin_findings(root, client.get("snapshot"), "the client snapshot", "the frozen snapshot of the client")
        p.extend(snap)
        if not snap:
            doc = pbs.read_pinned(root, client["snapshot"], "yaml", "the client snapshot")
            try:
                sc.current_client(events, client["name"], doc)
            except sc.ClientError as exc:
                p.append(str(exc))
    s11, manifest = stage11(root, spec, events)
    p.extend(s11)
    p.extend(design_findings(root, spec, manifest))
    p.extend(system_findings(root, spec, manifest))
    p.extend(capability_findings(spec))
    p.extend(carried_findings(root, spec, manifest))
    p.extend(package_build_findings(spec))
    p.extend(statement_findings(spec))
    return p
