"""Cross-client scan for stage 12 (sandbox-ship): find strings in the package that may name another client.

Another client's name or context inside a client's deliverable is a serious error, so this scan runs in every mode
and reads no mode setting. No list of other clients is supplied; the scan finds candidates itself and a person
resolves each one.

What it reads (plain YAML, sandbox-ship-spec/v1): `client` (a name, or a mapping with `name`), and `package.files[]`
({src, dest}), each src relative to the run root. Optional: `workspace_id` / `tenant_id` (top level or inside
`client`), the run's own ids.

What it flags (candidates, not verdicts):
  * name-shaped strings: two or more capitalised words in a row;
  * organisation-shaped strings: capitalised words ending in Inc, Ltd, LLC, GmbH, Corp, Bank or Group;
  * paths and ids foreign to the run: an absolute path (under /Users, /home, /tmp, /var, /private, /mnt, /opt,
    /srv) outside the run root, and a workspace or tenant id that is not the run's own.

What it never flags:
  * the run's own client (a candidate that is, or sits inside, the spec's client name);
  * placeholders: anything wrapped in double braces, for example {{ some_name }};
  * commodity tools and vendors, Within (the plugin's maker) and the plugin's own vocabulary: COMMODITY_WORDS below,
    a candidate is skipped when every word of it (after any organisation suffix) is in that list;
  * a capitalised sentence opener: leading words in LEADING_WORDS are dropped before a name is judged.

Resolution record (leak-resolution/v1): one entry per flag, {string, file, decision, decided_by, package_sha256},
decision `not-a-client` or `removed`. The package hash is sha256 over the sorted lines "dest<TAB>file-sha256", each
line ended by a newline, one per entry of package.files plus the built .dockerignore and, unless package.build is
given (the package then brings its own Dockerfiles as listed files), the built Dockerfile (dests .dockerignore and
Dockerfile).
The current client is taken from the run header (run.opened.client), and the spec's client.name must match it.
"""
import os
import re
import sys

LEAK_SPEC_SCHEMA = "sandbox-ship-spec/v1"
RESOLUTION_SCHEMA = "leak-resolution/v1"
DECISIONS = ("not-a-client", "removed")
DEFAULT_RESOLUTION = "output/sandbox-ship/leak-resolution.yaml"

ORG_SUFFIXES = ("Inc", "Ltd", "LLC", "GmbH", "Corp", "Bank", "Group")

# Commodity tools and vendors, Within, and the plugin's own vocabulary. Words, matched case-insensitively.
COMMODITY_WORDS = frozenset(w.lower() for w in """
Within Python Docker Dockerfile GitHub GitLab Bitbucket Slack Linux Ubuntu Debian Alpine macOS Windows Microsoft Google
Amazon Web Services AWS Azure OpenAI Anthropic Claude Codex Postgres PostgreSQL MySQL SQLite Redis Kafka Jira Asana
Notion Linear Salesforce SAP Oracle Snowflake Databricks Excel Outlook Teams Gmail SharePoint Kubernetes Node Java
JavaScript TypeScript YAML JSON HTML CSS Markdown PyYAML Phoenix Chrome Apple Meta Actions Cloud Drive Docs Sheets
Company Brain Snapshot Test Suite Production Readiness Gate Agent Technical Design Sandbox Autonomy Ladder Escalation
Interface Simulation Synthetic Data Design Brief Eval Package Spec Stage Run Client Current Future State Workflow
""".split())

# Words that only open a sentence or a heading; dropped from the front of a name-shaped string.
LEADING_WORDS = frozenset(w.lower() for w in """
The This That These Those A An If When Each Every For See Note Use Read Never Always In On At Of To And Or But With
Without Not No Any All Some Also Then Here There It Its Our Your Their We You They Is Are Was Were Be Do Does
""".split())

PLACEHOLDER_RE = re.compile(r"\{\{[^{}]*\}\}")
_WORD = r"[A-Z][A-Za-z0-9&'-]*"
ORG_RE = re.compile(r"\b(?:%s[ \t]+)+(?:%s)\b" % (_WORD, "|".join(ORG_SUFFIXES)))
NAME_RE = re.compile(r"\b%s(?:[ \t]+%s)+\b" % (_WORD, _WORD))
PATH_RE = re.compile(r"(?<![\w:/.~-])/(?:Users|home|tmp|var|private|mnt|opt|srv)/[^\s'\"`)\]>,;}]+")
ID_KEY_RE = re.compile(r"""\b(workspace|tenant)[_-]?id\b["']?\s*[:=]\s*["']?([A-Za-z0-9][A-Za-z0-9_.-]*)""",
                       re.IGNORECASE)
ID_URL_RE = re.compile(r"/(?:workspaces|tenants)/([A-Za-z0-9][A-Za-z0-9_.-]*)")


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


class LeakSetupError(Exception):
    """The scan cannot run (exit 2): a spec, a package file or a resolution record that cannot be read."""


def _yaml():
    try:
        import yaml
    except ImportError:
        raise LeakSetupError("PyYAML is required but not importable (pip install pyyaml)")
    return yaml


def load_yaml_file(path, what):
    try:
        with open(path, encoding="utf-8") as f:
            return _yaml().safe_load(f)
    except OSError as exc:
        raise LeakSetupError("%s cannot be read (%s): %s" % (what, path, exc))
    except Exception as exc:  # noqa: BLE001 - any parse failure is a setup problem, named
        raise LeakSetupError("%s is not valid YAML (%s): %s" % (what, path, exc))


def read_spec(spec_path):
    spec = load_yaml_file(spec_path, "the spec")
    if not isinstance(spec, dict):
        raise LeakSetupError("the spec %s is not a YAML mapping" % spec_path)
    client = spec.get("client")
    name = client.get("name") if isinstance(client, dict) else client
    if not isinstance(name, str) or not name.strip():
        raise LeakSetupError("the spec has no client name (client, or client.name); the scan needs it to know "
                             "whose name is not a leak")
    pkg = spec.get("package")
    files = pkg.get("files") if isinstance(pkg, dict) else None
    if not isinstance(files, list) or not files:
        raise LeakSetupError("the spec lists no package.files; there is nothing to scan")
    entries = []
    for i, f in enumerate(files):
        if not (isinstance(f, dict) and isinstance(f.get("src"), str) and isinstance(f.get("dest"), str)):
            raise LeakSetupError("package.files[%d] must be {src, dest}" % i)
        entries.append({"src": f["src"], "dest": f["dest"]})
    own_ids = set()
    for holder in (spec, client if isinstance(client, dict) else {}):
        for k in ("workspace_id", "tenant_id"):
            if holder.get(k):
                own_ids.add(str(holder[k]))
    return {"client": name.strip(), "files": entries, "own_ids": own_ids,
            "package_root": pkg.get("root") if isinstance(pkg.get("root"), str) else None,
            "build": pkg.get("build"),
            "snapshot": client.get("snapshot") if isinstance(client, dict) else None}


def snapshot_client(root, spec):
    """The current client, taken from the run header and checked against the spec's client.name.

    The spec must still pin a snapshot, read through stage 11's pinned reader; the snapshot need not name the client,
    but if it holds a top-level `client` that differs from the header, that is refused. Refuses (LeakSetupError) when
    the run has no event log, the header records no client, or the names differ.
    """
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import _prebuild_sim as pbs
    import _sandbox_ship_client as sc
    if not isinstance(spec.get("snapshot"), dict):
        raise LeakSetupError("the spec pins no client snapshot (client.snapshot with path and sha256)")
    try:
        doc = pbs.read_pinned(root, spec["snapshot"], "yaml", "the client snapshot")
        events = sc.events_for(root, "the cross-client scan (the current client from the run header)")
    except Exception as exc:  # noqa: BLE001 - any unreadable pin or log is a setup problem, named
        raise LeakSetupError("%s" % exc)
    try:
        return sc.current_client(events, spec["client"], doc)
    except sc.ClientError as exc:
        raise LeakSetupError(str(exc))


def resolve_src(root, src):
    if os.path.isabs(src):
        raise LeakSetupError("package file %s is an absolute path; package files are relative to the run root" % src)
    full = os.path.realpath(os.path.join(root, src))
    real_root = os.path.realpath(root)
    if full != real_root and not full.startswith(real_root + os.sep):
        raise LeakSetupError("package file %s lies outside the run root" % src)
    if not os.path.isfile(full):
        raise LeakSetupError("package file %s does not exist" % src)
    return full


def package_sha256(root, files, package_root=None, package_build=None):
    """The package hash (rule: scripts/_sandbox_ship_build.package_hash), from package.files entries {src, dest}.
    PACKAGE_BUILD is the spec's package.build (None for the default stack, whose Dockerfile the builder generates)."""
    build = _shared_builder()
    entries = [(f["dest"], build.sha256_file(resolve_src(root, f["src"]))) for f in files]
    if not package_root:
        raise LeakSetupError("the spec has no package.root; build the package first so the scan can hash it")
    try:
        gen = build.generated_hashes(os.path.join(root, package_root), package_build)
    except build.BuildError as exc:
        raise LeakSetupError(str(exc))
    return build.package_hash(entries, gen)


def read_text_or_none(full):
    """The file's text, or None when it is binary."""
    with open(full, "rb") as f:
        data = f.read()
    if b"\x00" in data[:8192]:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _is_commodity(words):
    words = [w for w in words if w not in ORG_SUFFIXES]
    return bool(words) and all(w.lower() in COMMODITY_WORDS for w in words)


CLIENT_SUFFIXES = frozenset(("inc", "ltd", "llc", "gmbh", "corp", "corporation", "co", "group", "plc", "ag", "sa"))


def _normalise_org(name):
    """Lowercase, trim, collapse whitespace, drop one trailing company suffix."""
    words = re.sub(r"[,;]", " ", name.lower()).split()
    if len(words) > 1 and words[-1].rstrip(".") in CLIENT_SUFFIXES:
        words.pop()
    return " ".join(words)


def _is_own_client(candidate, client):
    """Exact match only: the candidate is the client's name once both are normalised. Containment either way is not."""
    return _normalise_org(candidate) == _normalise_org(client)


def model_assisted_candidates(text, path):
    """Hook for a later model-assisted pass; returns no candidates today and is never called with a network."""
    return []


def scan_text(text, path, client, root, own_ids):
    """Candidates in TEXT as a list of (string, kind, line_number). PATH is only for the hook."""
    found = []
    for lineno, raw in enumerate(text.splitlines(), 1):
        line = PLACEHOLDER_RE.sub(" ", raw)
        taken = []
        for m in ORG_RE.finditer(line):
            taken.append(m.span())
            words = m.group(0).split()
            if _is_commodity(words) or _is_own_client(m.group(0), client):
                continue
            found.append((" ".join(words), "organisation", lineno))
        for m in NAME_RE.finditer(line):
            if any(s <= m.start() < e for s, e in taken):
                continue
            words = m.group(0).split()
            while words and words[0].lower() in LEADING_WORDS:
                words.pop(0)
            if len(words) < 2 or _is_commodity(words):
                continue
            cand = " ".join(words)
            if _is_own_client(cand, client):
                continue
            found.append((cand, "name", lineno))
        real_root = os.path.realpath(root)
        for m in PATH_RE.finditer(line):
            p = m.group(0).rstrip(".:")
            rp = os.path.realpath(p)
            if rp == real_root or rp.startswith(real_root + os.sep) or p == root or p.startswith(root.rstrip("/") + "/"):
                continue
            found.append((p, "path", lineno))
        for m in ID_KEY_RE.finditer(line):
            if m.group(2) not in own_ids:
                found.append((m.group(2), "id", lineno))
        for m in ID_URL_RE.finditer(line):
            if m.group(1) not in own_ids:
                found.append((m.group(1), "id", lineno))
    for cand in model_assisted_candidates(text, path):
        found.append(cand)
    return found


def scan_package(root, spec):
    """(flags, notes). A flag is {string, kind, file (the dest), lines}, one per (string, file)."""
    flags = {}
    notes = []
    for f in spec["files"]:
        text = read_text_or_none(resolve_src(root, f["src"]))
        if text is None:
            notes.append("skipped %s: not a text file" % f["dest"])
            continue
        for string, kind, lineno in scan_text(text, f["dest"], spec["client"], root, spec["own_ids"]):
            key = (string, f["dest"])
            if key in flags:
                flags[key]["lines"].append(lineno)
            else:
                flags[key] = {"string": string, "kind": kind, "file": f["dest"], "lines": [lineno]}
    return sorted(flags.values(), key=lambda x: (x["file"], x["string"])), notes


def load_resolutions(path):
    """The entries of the resolution record, or None when the file does not exist."""
    if not os.path.isfile(path):
        return None
    doc = load_yaml_file(path, "the resolution record")
    if not isinstance(doc, dict) or doc.get("schema") != RESOLUTION_SCHEMA:
        raise LeakSetupError("the resolution record %s must be a mapping with schema %s" % (path, RESOLUTION_SCHEMA))
    entries = doc.get("resolutions") or []
    if not isinstance(entries, list):
        raise LeakSetupError("the resolution record %s: resolutions must be a list" % path)
    return [e for e in entries if isinstance(e, dict)]


def unresolved(flags, entries, pkg_hash, resolution_path):
    """A list of plain-language findings; empty when every flag is resolved against this package."""
    if not flags:
        return []
    if entries is None:
        return (["%d flag(s) found and no resolution record exists at %s; a person must record a decision for each"
                 % (len(flags), resolution_path)]
                + ["unresolved: %r in %s (%s, line %s)" % (f["string"], f["file"], f["kind"], f["lines"][0])
                   for f in flags])
    by_key = {(str(e.get("string")), str(e.get("file"))): e for e in entries}
    out = []
    for fl in flags:
        e = by_key.get((fl["string"], fl["file"]))
        where = "%r in %s (%s, line %s)" % (fl["string"], fl["file"], fl["kind"], fl["lines"][0])
        if e is None:
            out.append("unresolved: %s has no decision" % where)
        elif e.get("decision") not in DECISIONS:
            out.append("unresolved: %s has decision %r; use one of %s" % (where, e.get("decision"),
                                                                        " or ".join(DECISIONS)))
        elif not str(e.get("decided_by") or "").strip():
            out.append("unresolved: %s has no decided_by; say who decided" % where)
        elif e.get("package_sha256") != pkg_hash:
            out.append("stale: %s was decided against package %s, but the package is now %s; decide again"
                       % (where, str(e.get("package_sha256"))[:12], pkg_hash[:12]))
        elif e.get("decision") == "removed":
            out.append("unresolved: %s is recorded as removed, but it is still in the file" % where)
    return out
