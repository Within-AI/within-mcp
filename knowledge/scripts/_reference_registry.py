#!/usr/bin/env python3
"""The worked-reference registry: the one place that declares which references each stage has.

Stdlib only. Every other part of the reference wiring (staging, the executor and reviewer channels,
the record check, the copy guard, clearance and the dry-run harness) reads the registry through this
module, never by opening `reference/library/registry.json` itself.

A stage is in exactly one of three states:
  * `available`      -- worked references exist; every level is registered with its files and hashes;
  * `not-built-yet`  -- the stage takes references but none has landed yet;
  * `none-by-design` -- the stage never takes a reference (its `reason` says why).

The registry path is fixed to this module's own skill root. No environment variable or argument
changes the registry that enforcement reads (`validate()` alone accepts another registry object, for
tests working on temporary copies).
"""
import hashlib
import json
import os
from pathlib import Path
import re
import sys

SKILL_ROOT = Path(__file__).resolve().parent.parent
REGISTRY_PATH = SKILL_ROOT / "reference/library/registry.json"
LIBRARY_ROOT = "reference/library"
LEVELS = ("simple", "middle", "complex")
STATES = ("available", "not-built-yet", "none-by-design")
ENCODINGS = ("json-key", "yaml-line", "md-comment")

# How a registered file reaches the reviewer is derived from its file name, never declared:
# reviewer_kind(path) is the one place that decides, and every reader calls it.
TEXT_EXT = (".md", ".json", ".yaml", ".yml", ".svg", ".html", ".txt",
            ".py", ".sh", ".conf", ".mjs", ".js", ".jsx", ".css", ".sql", ".toml", ".cfg", ".ini")
IMAGE_EXT = (".png", ".jpg", ".jpeg", ".webp")
# Text files recognised by name rather than extension (Dockerfile, any *.Dockerfile, .dockerignore).
TEXT_NAMES = ("Dockerfile", ".dockerignore")
TEXT_NAME_SUFFIXES = (".Dockerfile",)


def reviewer_kind(path):
    """'text' (inlined in full; must decode as UTF-8), 'image' (attached), or None (refused)."""
    name = os.path.basename(str(path))
    if name in TEXT_NAMES or (name.endswith(TEXT_NAME_SUFFIXES) and name not in TEXT_NAME_SUFFIXES):
        return "text"
    ext = os.path.splitext(name)[1].lower()
    if ext in TEXT_EXT:
        return "text"
    if ext in IMAGE_EXT:
        return "image"
    return None

# The anonymized client placeholder used in references. A real client run has no reason to say it,
# so its appearance in an output signals copying. It is never exempt through any ignore list.
PLACEHOLDER_STRINGS = ("Acme",)

# Every file of the labelled synthetic dry-run fixture carries this marker (tests/dry_run/).
SYNTHETIC_MARKER = "SYNTHETIC DRY-RUN FIXTURE"

# The record encoding each reference-taking stage's submitted artifact takes (rule 7). This map is part
# of the stage lockstep: the change that registers a new stage adds its carrier here (json-key for a JSON
# artifact, yaml-line for YAML, md-comment for Markdown); until it does, validation fails naming the entry.
CARRIER_ENCODING = {
    "snapshot": "json-key",
    "current-state": "json-key",
    "transformation-plan": "md-comment",
    "future-state": "json-key",
    "agent-backbone": "yaml-line",
    "agent-build": "json-key",
    "deepen-interface-design": "yaml-line",
    "deepen-decision-rules": "yaml-line",
    "eval": "yaml-line",
    "pre-build-simulation": "yaml-line",
    "sandbox-ship": "yaml-line",
}

# The files beside a stage's submitted artifact that the copy guard also scans, because the artifact (the carrier)
# pins them. Stage key -> list of JSON pointers (RFC 6901) into the parsed carrier; a `*` segment means every
# element of a list. Every pointer ends in `/path`: it names the path half of a `{path, sha256}` pair, and the
# guard scans the file only when that path resolves inside the run root and the file hashes to the pinned sha256.
# Stage 11 (pre-build simulation): the coverage statement and every per-system data-shape record its manifest pins.
# `resolve_companions` is the only code that reads these pointers; validation refuses an entry whose key is not a
# reference-taking stage in the registry or whose pointer is malformed.
CARRIER_COMPANIONS = {
    "pre-build-simulation": ["/coverage/statement/path", "/systems/*/data_shape/path"],
    "sandbox-ship": ["/live_run/evidence/path"],
}

# A companion pair the carrier may leave null, and the one carrier value that allows it. Stage key -> {companion
# pointer: (JSON pointer to a value in the carrier, the value that permits a null pair)}. Stage 12: the live evidence
# is null only when no live run happened (`live_run.status: not_run`); any other null is a setup error. Read only by
# `resolve_companions`; validation refuses an entry whose pointer is not one of the stage's CARRIER_COMPANIONS.
COMPANION_NULL_WHEN = {
    "sandbox-ship": {"/live_run/evidence/path": ("/live_run/status", "not_run")},
}

# The package a stage's carrier lists file by file, scanned by the copy guard from the built package. Stage key -> one
# JSON pointer (RFC 6901, no `*`) to the carrier's package block `{root, files[] ({src, dest})}`. The files carry no
# hash of their own: the guard scans `<root>/<dest>` for each listed file only when the built package's
# package.lock.json agrees with package_hash() recomputed through the one builder implementation
# (scripts/_sandbox_ship_build.py, loaded by file path as checks/_sandbox_ship_clearance.py loads it), over both the
# sources and the built files. Stage 12 (sandbox-ship): its spec's package block. `resolve_packages` is the only code
# that reads this map; validation refuses an entry whose key is not a reference-taking stage in the registry or whose
# pointer is malformed.
CARRIER_PACKAGES = {
    "sandbox-ship": "/package",
}

ROLE_RE = re.compile(r"^[a-z][a-z0-9-]*$")
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
TEMPLATE_PLACEHOLDER = "{{ REFERENCES.block }}"
WORKED_COLLECTION = "worked_references"


class RegistryStale(Exception):
    """A registered plugin file no longer matches the hash the registry holds."""


class StagingError(Exception):
    """A run's staged copy of a reference cannot be (re)written safely."""


def _sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _canonical_keys():
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import _stageindex
    return [key for _plain, key in _stageindex.CANONICAL_PIPELINE]


def _prepare_stage_brief_stages():
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import _stage_checks
    return set(_stage_checks.STAGES)


# --------------------------------------------------------------------------------------------------
# Loading and lookups


def load(path=REGISTRY_PATH):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def registry_sha256():
    return _sha256_file(REGISTRY_PATH)


def stage_entry(stage, reg=None):
    reg = load() if reg is None else reg
    entry = reg.get("stages", {}).get(stage)
    return entry if isinstance(entry, dict) else None


def state(stage, reg=None):
    """The registry state for a canonical stage key; None for an unknown or legacy key."""
    entry = stage_entry(stage, reg)
    return entry.get("state") if entry else None


def clearance_checks(stage, reg=None):
    """The reference checks the writer requires at review and clearance for this stage."""
    if state(stage, reg) == "available":
        return ["reference-use-record", "reference-copy-guard"]
    return []


def staged_path(stage, level, basename):
    return "references/%s/%s/%s" % (stage, level, basename)


def _level_order(level):
    return LEVELS.index(level) if level in LEVELS else len(LEVELS)


def _references(entry):
    return sorted(entry.get("references") or [], key=lambda r: _level_order(r.get("level")))


def _reference_files(ref):
    """README first (role `readme`), then artifacts in registry order."""
    files = [{"role": "readme", "path": ref["readme"]["path"], "sha256": ref["readme"]["sha256"]}]
    for art in ref.get("artifacts") or []:
        files.append({"role": art["role"], "path": art["path"], "sha256": art["sha256"]})
    return files


def exemplar_set(stage, reg=None):
    """Every reference of an available stage, in level order, with run-relative staged paths."""
    entry = stage_entry(stage, reg)
    if not entry or entry.get("state") != "available":
        return []
    out = []
    for ref in _references(entry):
        files = [{"role": f["role"], "path": staged_path(stage, ref["level"], os.path.basename(f["path"])),
                  "sha256": f["sha256"]} for f in _reference_files(ref)]
        out.append({"reference_id": ref["id"], "level": ref["level"], "files": files})
    return out


def reference_ids(stage, reg=None):
    entry = stage_entry(stage, reg)
    if not entry or entry.get("state") != "available":
        return []
    return [ref["id"] for ref in _references(entry)]


def worked_references(reg=None):
    """The registry's worked references: whole invented run roots that span several stages.

    They live in the top-level `worked_references` collection, keyed by id, each entry in the same shape as a
    stage reference (`id`, `level`, `readme`, `artifacts`), with every file under `reference/library/<id>/`.
    They are never staged into a stage channel; validation and rehash cover them like stage references."""
    reg = load() if reg is None else reg
    coll = reg.get(WORKED_COLLECTION)
    return coll if isinstance(coll, dict) else {}


def worked_reference(ref_id, reg=None):
    entry = worked_references(reg).get(ref_id)
    return entry if isinstance(entry, dict) else None


def primary_artifact(ref):
    return next(a for a in ref.get("artifacts") or [] if a.get("role") == "primary")


def verify_plugin_files(stage, reg=None, skill_root=SKILL_ROOT):
    """Re-hash every registered plugin file of the stage; raise RegistryStale on any drift."""
    entry = stage_entry(stage, reg)
    if not entry or entry.get("state") != "available":
        return
    hint = "registry stale: run scripts/reference-registry.py rehash --stage %s" % stage
    for ref in _references(entry):
        for f in _reference_files(ref):
            path = Path(skill_root) / f["path"]
            if not path.is_file():
                raise RegistryStale("%s (missing %s)" % (hint, f["path"]))
            if _sha256_file(path) != f["sha256"]:
                raise RegistryStale("%s (%s changed)" % (hint, f["path"]))
    ds = entry.get("distinctive_strings")
    if ds:
        path = Path(skill_root) / ds["path"]
        if not path.is_file() or _sha256_file(path) != ds["sha256"]:
            raise RegistryStale("%s (%s changed)" % (hint, ds["path"]))


# --------------------------------------------------------------------------------------------------
# Validation


def _registered_text_problems(path, rel):
    kind = reviewer_kind(rel)
    if kind is None:
        return ["%s: extension %r cannot reach the reviewer as content (allowed: %s, or a file named %s)"
                % (rel, os.path.splitext(rel)[1].lower(), ", ".join(TEXT_EXT + IMAGE_EXT),
                   ", ".join(TEXT_NAMES + tuple("*" + s for s in TEXT_NAME_SUFFIXES)))]
    if kind == "text":
        try:
            path.read_bytes().decode("utf-8")
        except UnicodeDecodeError:
            return ["%s: a text reference file must decode as UTF-8" % rel]
    return []


def _validate_available(stage, entry, reg, skill_root, problems):
    refs = entry.get("references")
    if not isinstance(refs, list) or not 1 <= len(refs) <= 3:
        problems.append("%s: an available stage needs 1 to 3 references" % stage)
        return
    levels_seen = set()
    roles_seen = set()
    for ref in refs:
        if not isinstance(ref, dict):
            problems.append("%s: each reference must be an object" % stage)
            continue
        level = ref.get("level")
        if level not in LEVELS:
            problems.append("%s: reference level %r is not one of %s" % (stage, level, list(LEVELS)))
            continue
        if level in levels_seen:
            problems.append("%s: duplicate level %r" % (stage, level))
        levels_seen.add(level)
        if ref.get("id") != "%s.%s" % (stage, level):
            problems.append("%s: reference id %r must be %r" % (stage, ref.get("id"), "%s.%s" % (stage, level)))
        prefix = "%s/%s/%s/" % (LIBRARY_ROOT, stage, level)
        readme = ref.get("readme")
        artifacts = ref.get("artifacts")
        if not isinstance(readme, dict) or not isinstance(artifacts, list) or not artifacts:
            problems.append("%s.%s: a reference needs a readme and a non-empty artifacts list" % (stage, level))
            continue
        files = [("readme", readme)] + [(a.get("role") if isinstance(a, dict) else None, a) for a in artifacts]
        if os.path.basename(str(readme.get("path", ""))) != "README.md":
            problems.append("%s.%s: the README basename must be README.md" % (stage, level))
        primaries = [a for a in artifacts if isinstance(a, dict) and a.get("role") == "primary"]
        if len(primaries) != 1:
            problems.append("%s.%s: exactly one artifact must have role primary (found %d)"
                            % (stage, level, len(primaries)))
        basenames = set()
        for role, f in files:
            if not isinstance(f, dict):
                problems.append("%s.%s: each file entry must be an object" % (stage, level))
                continue
            rel = f.get("path")
            if not isinstance(rel, str) or not rel.startswith(prefix) or "/../" in rel or rel.endswith("/"):
                problems.append("%s.%s: path %r is outside %s" % (stage, level, rel, prefix))
                continue
            if role != "readme":
                if not isinstance(role, str) or not ROLE_RE.match(role):
                    problems.append("%s.%s: role %r must match %s" % (stage, level, role, ROLE_RE.pattern))
                else:
                    roles_seen.add(role)
            base = os.path.basename(rel)
            if base in basenames:
                problems.append("%s.%s: two files share the basename %s" % (stage, level, base))
            basenames.add(base)
            path = Path(skill_root) / rel
            if not path.is_file():
                problems.append("%s.%s: registered file %s does not exist" % (stage, level, rel))
                continue
            if f.get("sha256") != _sha256_file(path):
                problems.append("%s.%s: sha256 of %s does not match the file on disk (stale hash; "
                                "run scripts/reference-registry.py rehash --stage %s)" % (stage, level, rel, stage))
            problems.extend("%s.%s: %s" % (stage, level, p) for p in _registered_text_problems(path, rel))
    for role in entry.get("required_roles") or []:
        if role not in roles_seen:
            problems.append("%s: required role %r appears in no reference" % (stage, role))
    ds = entry.get("distinctive_strings")
    expected = "%s/%s/distinctive-strings.json" % (LIBRARY_ROOT, stage)
    if not isinstance(ds, dict) or ds.get("path") != expected:
        problems.append("%s: distinctive_strings must name %s" % (stage, expected))
    else:
        path = Path(skill_root) / ds["path"]
        if not path.is_file():
            problems.append("%s: distinctive strings file %s does not exist" % (stage, ds["path"]))
        elif ds.get("sha256") != _sha256_file(path):
            problems.append("%s: sha256 of %s does not match the file on disk" % (stage, ds["path"]))
        else:
            problems.extend(_string_set_problems(reg, stage, skill_root))




def _registered_files(entry):
    for ref in entry.get("references") or []:
        if not isinstance(ref, dict):
            continue
        if isinstance(ref.get("readme"), dict):
            yield ref["readme"].get("path")
        for art in ref.get("artifacts") or []:
            if isinstance(art, dict):
                yield art.get("path")


def _companion_problems(stages, companions):
    """CARRIER_COMPANIONS rules: each key a reference-taking stage in the registry, each pointer well formed."""
    problems = []
    if not isinstance(companions, dict):
        return ["CARRIER_COMPANIONS must map stage keys to lists of JSON pointers"]
    for key, pointers in companions.items():
        entry = stages.get(key) if isinstance(key, str) else None
        if not isinstance(entry, dict):
            problems.append("CARRIER_COMPANIONS: %r is not a registered stage key" % (key,))
        elif entry.get("state") == "none-by-design":
            problems.append("CARRIER_COMPANIONS: %r takes no worked reference by design, so the copy guard never "
                            "scans it" % key)
        if not isinstance(pointers, list) or not pointers:
            problems.append("CARRIER_COMPANIONS[%r] must be a non-empty list of JSON pointers" % (key,))
            continue
        seen = set()
        for pointer in pointers:
            problem = pointer_problem(pointer)
            if problem:
                problems.append("CARRIER_COMPANIONS[%r]: pointer %r is malformed: %s" % (key, pointer, problem))
            elif pointer in seen:
                problems.append("CARRIER_COMPANIONS[%r]: pointer %r is listed twice" % (key, pointer))
            seen.add(pointer)
    return problems


def _package_problems(stages, packages, companions=None, null_when=None):
    """CARRIER_PACKAGES rules (each key a reference-taking stage in the registry, one well-formed pointer), plus
    COMPANION_NULL_WHEN's (each entry names a companion pointer of its stage and a well-formed carrier pointer)."""
    problems = []
    if not isinstance(packages, dict):
        problems.append("CARRIER_PACKAGES must map stage keys to one JSON pointer each")
        packages = {}
    for key, pointer in packages.items():
        entry = stages.get(key) if isinstance(key, str) else None
        if not isinstance(entry, dict):
            problems.append("CARRIER_PACKAGES: %r is not a registered stage key" % (key,))
        elif entry.get("state") == "none-by-design":
            problems.append("CARRIER_PACKAGES: %r takes no worked reference by design, so the copy guard never "
                            "scans it" % key)
        problem = package_pointer_problem(pointer)
        if problem:
            problems.append("CARRIER_PACKAGES[%r]: pointer %r is malformed: %s" % (key, pointer, problem))
    companions = CARRIER_COMPANIONS if companions is None else companions
    null_when = COMPANION_NULL_WHEN if null_when is None else null_when
    for key, rules in (null_when.items() if isinstance(null_when, dict) else []):
        for pointer, rule in (rules.items() if isinstance(rules, dict) else []):
            if pointer not in (companions.get(key) or []):
                problems.append("COMPANION_NULL_WHEN[%r]: %r is not a companion pointer of that stage" % (key, pointer))
            if (not isinstance(rule, tuple) or len(rule) != 2 or package_pointer_problem(rule[0])
                    or not isinstance(rule[1], str)):
                problems.append("COMPANION_NULL_WHEN[%r][%r] must be (a JSON pointer, the value that permits null)"
                                % (key, pointer))
    return problems


def validate(reg, skill_root=SKILL_ROOT, companions=None):
    """Every registry rule; an empty list means the registry is valid.

    COMPANIONS defaults to CARRIER_COMPANIONS (the argument exists for tests only)."""
    problems = []
    if not isinstance(reg, dict):
        return ["registry must be a JSON object"]
    if reg.get("schema_version") != 1:
        problems.append("schema_version must be 1")
    if reg.get("library_root") != LIBRARY_ROOT:
        problems.append("library_root must be %r" % LIBRARY_ROOT)
    if reg.get("levels") != list(LEVELS):
        problems.append("levels must be %r" % list(LEVELS))
    stages = reg.get("stages")
    if not isinstance(stages, dict):
        return problems + ["stages must be an object keyed by canonical stage key"]
    canonical = _canonical_keys()
    for key in canonical:
        if key not in stages:
            problems.append("stage %r is missing from the registry" % key)
    for key in stages:
        if key not in canonical:
            problems.append("stage %r is not a canonical stage key (legacy or unknown)" % key)
    fixture_copy = reg.get("synthetic_fixture_copy")
    if "synthetic_fixture_copy" in reg and fixture_copy is not True:
        problems.append("synthetic_fixture_copy may only be true (and only in a harness plugin copy)")
    brief_stages = _prepare_stage_brief_stages()
    for key, entry in stages.items():
        if not isinstance(entry, dict):
            problems.append("%s: entry must be an object" % key)
            continue
        st = entry.get("state")
        if st not in STATES:
            problems.append("%s: state %r is not one of %s" % (key, st, list(STATES)))
            continue
        if st == "none-by-design":
            if not isinstance(entry.get("reason"), str) or not entry["reason"].strip():
                problems.append("%s: none-by-design needs a non-empty reason" % key)
            for field, empty in (("channel", None), ("record_encoding", None), ("distinctive_strings", None)):
                if entry.get(field) is not empty:
                    problems.append("%s: none-by-design must have %s null" % (key, field))
            for field in ("required_roles", "references"):
                if entry.get(field) != []:
                    problems.append("%s: none-by-design must have %s []" % (key, field))
            continue
        if "reason" in entry:
            problems.append("%s: reason is only for none-by-design stages" % key)
        channel = entry.get("channel")
        if not isinstance(channel, dict):
            problems.append("%s: a reference-taking stage needs a channel" % key)
        elif channel.get("kind") == "prepare-stage-briefs":
            if set(channel) != {"kind"}:
                problems.append("%s: a prepare-stage-briefs channel carries only its kind" % key)
            if key not in brief_stages:
                problems.append("%s: prepare-stage-briefs channel is only for stages in _stage_checks.STAGES" % key)
        elif channel.get("kind") == "template":
            template = channel.get("template")
            if not isinstance(template, str) or not template:
                problems.append("%s: a template channel names its template file" % key)
            elif not (Path(skill_root) / template).is_file():
                problems.append("%s: template channel file %s does not exist" % (key, template))
            elif (Path(skill_root) / template).read_text(encoding="utf-8").count(TEMPLATE_PLACEHOLDER) != 1:
                problems.append("%s: template channel file %s must contain %s exactly once"
                                % (key, template, TEMPLATE_PLACEHOLDER))
        else:
            problems.append("%s: channel kind %r is not prepare-stage-briefs or template" % (key, channel.get("kind")))
        enc = entry.get("record_encoding")
        if enc not in ENCODINGS:
            problems.append("%s: record_encoding %r is not one of %s" % (key, enc, list(ENCODINGS)))
        elif key not in CARRIER_ENCODING:
            problems.append("%s: no carrier registered for this stage; add \"%s\": \"<json-key | yaml-line | "
                            "md-comment>\" (the file type of its submitted artifact) to CARRIER_ENCODING in "
                            "scripts/_reference_registry.py in the same change that registers the stage" % (key, key))
        elif enc != CARRIER_ENCODING[key]:
            problems.append("%s: record_encoding must be %r for this stage's submitted artifact"
                            % (key, CARRIER_ENCODING[key]))
        roles = entry.get("required_roles")
        if not isinstance(roles, list) or not roles or "primary" not in roles:
            problems.append("%s: required_roles must be a non-empty list including primary" % key)
        elif any(not isinstance(r, str) or not ROLE_RE.match(r) for r in roles):
            problems.append("%s: every required role must match %s" % (key, ROLE_RE.pattern))
        if st == "not-built-yet":
            if entry.get("references") != []:
                problems.append("%s: not-built-yet must have references []" % key)
            if entry.get("distinctive_strings") is not None:
                problems.append("%s: not-built-yet must have distinctive_strings null" % key)
            continue
        _validate_available(key, entry, reg, skill_root, problems)
    for key, entry in stages.items():
        if not isinstance(entry, dict) or entry.get("state") != "available":
            continue
        for rel in _registered_files(entry):
            path = Path(skill_root) / str(rel)
            if not path.is_file():
                continue
            marked = SYNTHETIC_MARKER.encode("utf-8") in path.read_bytes()
            if fixture_copy is True and not marked:
                problems.append("%s: %s lacks the synthetic fixture marker in a synthetic_fixture_copy registry"
                                % (key, rel))
            if fixture_copy is not True and marked:
                problems.append("%s: %s carries the synthetic fixture marker; fixture files may never be "
                                "registered outside a harness plugin copy" % (key, rel))
    problems.extend(_companion_problems(stages, CARRIER_COMPANIONS if companions is None else companions))
    problems.extend(_package_problems(stages, CARRIER_PACKAGES))
    problems.extend(_worked_problems(reg, skill_root, fixture_copy))
    problems.extend(_generic_strings_problems(skill_root))
    return problems


def _worked_problems(reg, skill_root, fixture_copy):
    """The worked-reference collection: same entry shape as a stage reference, every file registered and current."""
    if WORKED_COLLECTION not in reg:
        return []
    coll = reg.get(WORKED_COLLECTION)
    if not isinstance(coll, dict):
        return ["%s must be an object keyed by reference id" % WORKED_COLLECTION]
    problems = []
    for ref_id, ref in coll.items():
        where = "%s.%s" % (WORKED_COLLECTION, ref_id)
        if not isinstance(ref, dict):
            problems.append("%s: entry must be an object" % where)
            continue
        if not isinstance(ref_id, str) or not ROLE_RE.match(ref_id) or ref.get("id") != ref_id:
            problems.append("%s: id must equal its key and match %s" % (where, ROLE_RE.pattern))
        if ref_id in reg.get("stages", {}):
            problems.append("%s: a worked reference id may not be a stage key" % where)
        if ref.get("level") not in LEVELS:
            problems.append("%s: level %r is not one of %s" % (where, ref.get("level"), list(LEVELS)))
        readme, artifacts = ref.get("readme"), ref.get("artifacts")
        if not isinstance(readme, dict) or not isinstance(artifacts, list) or not artifacts:
            problems.append("%s: a reference needs a readme and a non-empty artifacts list" % where)
            continue
        if os.path.basename(str(readme.get("path", ""))) != "README.md":
            problems.append("%s: the README basename must be README.md" % where)
        primaries = [a for a in artifacts if isinstance(a, dict) and a.get("role") == "primary"]
        if len(primaries) != 1:
            problems.append("%s: exactly one artifact must have role primary (found %d)" % (where, len(primaries)))
        prefix = "%s/%s/" % (LIBRARY_ROOT, ref_id)
        seen = set()
        for role, f in [("readme", readme)] + [(a.get("role") if isinstance(a, dict) else None, a) for a in artifacts]:
            if not isinstance(f, dict):
                problems.append("%s: each file entry must be an object" % where)
                continue
            rel = f.get("path")
            if not isinstance(rel, str) or not rel.startswith(prefix) or "/../" in rel or rel.endswith("/"):
                problems.append("%s: path %r is outside %s" % (where, rel, prefix))
                continue
            if role != "readme" and (not isinstance(role, str) or not ROLE_RE.match(role)):
                problems.append("%s: role %r must match %s" % (where, role, ROLE_RE.pattern))
            if rel in seen:
                problems.append("%s: %s is registered twice" % (where, rel))
            seen.add(rel)
            path = Path(skill_root) / rel
            if not path.is_file():
                problems.append("%s: registered file %s does not exist" % (where, rel))
                continue
            if f.get("sha256") != _sha256_file(path):
                problems.append("%s: sha256 of %s does not match the file on disk (stale hash; run "
                                "scripts/reference-registry.py rehash --worked %s)" % (where, rel, ref_id))
            problems.extend("%s: %s" % (where, p) for p in _registered_text_problems(path, rel))
            marked = SYNTHETIC_MARKER.encode("utf-8") in path.read_bytes()
            if marked and fixture_copy is not True:
                problems.append("%s: %s carries the synthetic fixture marker" % (where, rel))
        folder = Path(skill_root) / prefix
        if folder.is_dir():
            for p in sorted(folder.rglob("*")):
                rel = p.relative_to(skill_root).as_posix()
                if p.is_file() and "__pycache__" not in p.parts and rel not in seen:
                    problems.append("%s: %s is under the reference folder but not registered" % (where, rel))
    return problems


def _generic_strings_problems(skill_root):
    path = Path(skill_root) / LIBRARY_ROOT / "generic-strings.json"
    if not path.is_file():
        return ["%s/generic-strings.json is missing" % LIBRARY_ROOT]
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        return ["generic-strings.json is not valid JSON: %s" % exc]
    strings = doc.get("strings") if isinstance(doc, dict) else None
    if not isinstance(strings, list) or any(not isinstance(s, str) or not s.strip() for s in strings):
        return ["generic-strings.json strings must be a list of non-empty strings"]
    placeholders = {p.lower() for p in PLACEHOLDER_STRINGS}
    bad = [s for s in strings if s.lower() in placeholders]
    if bad:
        return ["generic-strings.json may not carry the anonymized client placeholder %s: its "
                "appearance in an output signals copying" % ", ".join(sorted(set(bad)))]
    return []


# --------------------------------------------------------------------------------------------------
# Hash maintenance (the only writer of registry hashes)


def rehash(stage, path=REGISTRY_PATH, skill_root=SKILL_ROOT):
    """Recompute every sha256 of one stage from disk and rewrite the registry. Returns missing files."""
    reg = load(path)
    entry = stage_entry(stage, reg)
    if entry is None:
        raise KeyError(stage)
    missing = []
    for ref in entry.get("references") or []:
        for f in [ref["readme"]] + list(ref.get("artifacts") or []):
            p = Path(skill_root) / f["path"]
            if not p.is_file():
                missing.append(f["path"])
            else:
                f["sha256"] = _sha256_file(p)
    ds = entry.get("distinctive_strings")
    if ds:
        p = Path(skill_root) / ds["path"]
        if not p.is_file():
            missing.append(ds["path"])
        else:
            ds["sha256"] = _sha256_file(p)
    if missing:
        return missing
    with open(path, "w", encoding="utf-8") as f:
        f.write(json.dumps(reg, indent=2, ensure_ascii=False) + "\n")
    return []


def rehash_worked(ref_id, path=REGISTRY_PATH, skill_root=SKILL_ROOT):
    """Recompute every sha256 of one worked reference from disk and rewrite the registry. Returns missing files."""
    reg = load(path)
    ref = worked_reference(ref_id, reg)
    if ref is None:
        raise KeyError(ref_id)
    missing = []
    for f in [ref["readme"]] + list(ref.get("artifacts") or []):
        p = Path(skill_root) / f["path"]
        if not p.is_file():
            missing.append(f["path"])
        else:
            f["sha256"] = _sha256_file(p)
    if missing:
        return missing
    with open(path, "w", encoding="utf-8") as f:
        f.write(json.dumps(reg, indent=2, ensure_ascii=False) + "\n")
    return []


# --------------------------------------------------------------------------------------------------
# The record an output carries: which reference it followed, at which level, and why


RECORD_SCHEMA = "reference-use/v1"
RECORD_KEY = "reference_use"
YAML_RECORD_RE = re.compile(r"(?m)^reference_use:[ \t]*(\{.*\})[ \t]*$")
YAML_ANY_RECORD_RE = re.compile(r"(?m)^reference_use:.*$")
MD_RECORD_RE = re.compile(r"<!--\s*REFERENCE-USE\s*(\{.*?\})\s*-->", re.DOTALL)
MD_ANY_RECORD_RE = re.compile(r"<!--\s*REFERENCE-USE")
CARRIER_EXT = {".json": "json-key", ".yaml": "yaml-line", ".yml": "yaml-line", ".md": "md-comment"}


class _Obj(dict):
    duplicates = ()


def _pairs_hook(pairs):
    obj = _Obj(pairs)
    keys = [k for k, _v in pairs]
    obj.duplicates = tuple(sorted({k for k in keys if keys.count(k) > 1}))
    return obj


def carrier_encoding(path):
    """The record encoding a carrier file takes, from its extension; None when unsupported."""
    return CARRIER_EXT.get(os.path.splitext(str(path))[1].lower())


def extract_records(path):
    """Return (records, problems) for the record(s) a carrier file holds."""
    enc = carrier_encoding(path)
    if enc is None:
        raise ValueError("unsupported carrier extension for %s (use .json, .yaml, .yml or .md)" % path)
    text = Path(path).read_text(encoding="utf-8")
    records, problems = [], []
    if enc == "json-key":
        try:
            doc = json.loads(text, object_pairs_hook=_pairs_hook)
        except ValueError as exc:
            return [], ["artifact is not valid JSON: %s" % exc]
        if isinstance(doc, dict):
            if RECORD_KEY in doc.duplicates:
                problems.append("the artifact carries more than one %s key" % RECORD_KEY)
                records.extend([doc[RECORD_KEY]] * 2)
            elif RECORD_KEY in doc:
                records.append(doc[RECORD_KEY])
        return records, problems
    if enc == "yaml-line":
        matched = set()
        for m in YAML_RECORD_RE.finditer(text):
            matched.add(m.start())
            try:
                records.append(json.loads(m.group(1), object_pairs_hook=_pairs_hook))
            except ValueError as exc:
                problems.append("record is not valid JSON: %s" % exc)
        for m in YAML_ANY_RECORD_RE.finditer(text):
            if m.start() not in matched:
                problems.append("record must be a single-line JSON object on one line starting "
                                "`reference_use: ` (line %d)" % (text.count("\n", 0, m.start()) + 1))
        return records, problems
    spans = list(MD_RECORD_RE.finditer(text))
    for m in spans:
        try:
            records.append(json.loads(m.group(1), object_pairs_hook=_pairs_hook))
        except ValueError as exc:
            problems.append("record is not valid JSON: %s" % exc)
    if len(MD_ANY_RECORD_RE.findall(text)) != len(spans):
        problems.append("record must be written as <!-- REFERENCE-USE {...JSON...} -->")
    return records, problems


def strip_records(path):
    """The carrier's text with its record removed (what the copy guard scans)."""
    enc = carrier_encoding(path)
    if enc is None:
        raise ValueError("unsupported carrier extension for %s" % path)
    text = Path(path).read_text(encoding="utf-8")
    if enc == "json-key":
        try:
            doc = json.loads(text)
        except ValueError:
            return text
        if isinstance(doc, dict) and RECORD_KEY in doc:
            del doc[RECORD_KEY]
            return json.dumps(doc, indent=2, ensure_ascii=False)
        return text
    if enc == "yaml-line":
        return re.sub(r"(?m)^reference_use:[ \t]*\{.*\}[ \t]*(?:\n|$)", "", text)
    return MD_RECORD_RE.sub("", text)


def insert_record(text, record, encoding):
    """TEXT with RECORD added in ENCODING (used by the dry-run harness and tests)."""
    if encoding == "json-key":
        doc = json.loads(text)
        doc[RECORD_KEY] = record
        return json.dumps(doc, indent=2, ensure_ascii=False) + "\n"
    line = json.dumps(record, ensure_ascii=True, separators=(", ", ": "))
    if encoding == "yaml-line":
        return text.rstrip("\n") + "\n" + RECORD_KEY + ": " + line + "\n"
    return text.rstrip("\n") + "\n\n<!-- REFERENCE-USE " + json.dumps(record, ensure_ascii=False) + " -->\n"


def _placeholder_problem(value):
    return any(tok in value for tok in ("<", ">", "TODO", "TBD"))


def record_problems(record, stage, reg=None, run_root="."):
    """Every problem with one record for an available stage; empty list when it is valid."""
    reg = load() if reg is None else reg
    entry = stage_entry(stage, reg)
    refs = {ref["id"]: ref for ref in _references(entry)}
    problems = []
    if not isinstance(record, dict):
        return ["record is not a JSON object"]
    for key in ("schema", "stage", "considered", "followed", "reason"):
        if key not in record:
            problems.append("record lacks %s" % key)
    for key in record:
        if key not in ("schema", "stage", "considered", "followed", "reason", "divergences"):
            problems.append("record carries unknown key %s" % key)
    if "schema" in record and record["schema"] != RECORD_SCHEMA:
        problems.append("schema must be %r" % RECORD_SCHEMA)
    if "stage" in record and record["stage"] != stage:
        problems.append("record stage %r is not %r" % (record["stage"], stage))
    considered = record.get("considered")
    ids = list(refs)
    if "considered" in record:
        if (not isinstance(considered, list) or any(not isinstance(c, str) for c in considered)
                or len(set(considered)) != len(considered) or set(considered) != set(ids)):
            problems.append("considered must list every reference id of this stage exactly once (%s)"
                            % ", ".join(ids))
    considered_set = set(considered) if isinstance(considered, list) else set()
    followed = record.get("followed")
    if "followed" in record:
        if not isinstance(followed, list) or not followed:
            problems.append("followed must be a non-empty list")
            followed = []
        elif len(followed) > len(ids):
            problems.append("followed lists %d references but the stage has %d" % (len(followed), len(ids)))
        seen = set()
        for i, item in enumerate(followed):
            if not isinstance(item, dict):
                problems.append("followed[%d] must be an object" % i)
                continue
            field_problems = []
            for key in ("reference_id", "level", "path", "sha256"):
                if key not in item:
                    field_problems.append("followed[%d] lacks %s" % (i, key))
                elif not isinstance(item[key], str) or not item[key].strip():
                    field_problems.append("followed[%d].%s must be a non-empty string" % (i, key))
            for key in item:
                if key not in ("reference_id", "level", "path", "sha256"):
                    field_problems.append("followed[%d] carries unknown key %s" % (i, key))
            if isinstance(item.get("sha256"), str) and item["sha256"] and not SHA_RE.match(item["sha256"]):
                field_problems.append("followed[%d].sha256 must be a 64-character lowercase hex digest" % i)
            problems.extend(field_problems)
            if field_problems:
                continue
            rid = item["reference_id"]
            if rid in seen:
                problems.append("followed lists %s more than once" % rid)
            seen.add(rid)
            if rid not in considered_set:
                problems.append("followed reference %s is not listed in considered" % rid)
            ref = refs.get(rid)
            if ref is None:
                problems.append("followed reference %s is not registered for this stage" % rid)
                continue
            if item["level"] != ref["level"]:
                problems.append("followed[%d].level %r does not match the registry's %r" % (i, item["level"], ref["level"]))
            primary = primary_artifact(ref)
            expected = staged_path(stage, ref["level"], os.path.basename(primary["path"]))
            if item["path"] != expected:
                problems.append("followed[%d].path must be %s" % (i, expected))
            if item["sha256"] != primary["sha256"]:
                problems.append("followed[%d].sha256 does not match the registered primary of %s" % (i, rid))
            staged = Path(run_root) / item["path"]
            if not staged.is_file():
                problems.append("staged reference file %s is missing (run stage-references.py)" % item["path"])
            elif _sha256_file(staged) != primary["sha256"]:
                problems.append("staged reference file %s does not hash to the registered primary" % item["path"])
    if "reason" in record:
        reason = record["reason"]
        if not isinstance(reason, str) or len(re.sub(r"\s", "", reason)) < 20:
            problems.append("reason must say, in at least 20 characters, why that level is the closest shape")
        elif _placeholder_problem(reason):
            problems.append("reason carries a placeholder (<, >, TODO or TBD)")
    divergences = record.get("divergences", [])
    if not isinstance(divergences, list):
        problems.append("divergences must be a list (or [])")
    else:
        for i, d in enumerate(divergences):
            if not isinstance(d, dict):
                problems.append("divergences[%d] must be an object with aspect and reason" % i)
                continue
            for key in ("aspect", "reason"):
                value = d.get(key)
                if not isinstance(value, str) or not value.strip():
                    problems.append("divergences[%d] needs a non-empty %s" % (i, key))
                elif _placeholder_problem(value):
                    problems.append("divergences[%d].%s carries a placeholder (<, >, TODO or TBD)" % (i, key))
    return problems


# --------------------------------------------------------------------------------------------------
# Copy-guard strings: candidates, filters, the stored set and its staleness


KINDS = ("id", "amount", "number", "name", "label")
CANDIDATE_RES = [
    ("id", re.compile(r"(?<![A-Za-z0-9])[A-Z][A-Z0-9]{1,9}(?:[-_][A-Z0-9]{1,12})+(?![A-Za-z0-9])")),
    ("id", re.compile(r"(?<![A-Za-z0-9])[A-Z]{2,6}\d{2,8}(?![A-Za-z0-9])")),
    ("amount", re.compile(r"(?<![\d.,])\d{1,3}(?:,\d{3})+(?:\.\d{1,4})?(?![\d,])")),
    ("amount", re.compile(r"(?<![\d.,])\d{3,}\.\d{2}(?!\d)")),
    ("number", re.compile(r"(?<![\d.,-])\d{6,}(?!\d)")),
    ("name", re.compile(r"(?<![A-Za-z])[A-Z][a-z]+(?:[ -][A-Z][a-z]+){1,4}(?![A-Za-z])")),
]
LABEL_TEXT_RE = re.compile(r':\s*"([^"\n]{12,120})"')
HASH_RUN_RE = re.compile(r"[0-9a-f]{32,}")
YEAR_RE = re.compile(r"^(?:19\d\d|20\d\d)$")
GENERIC_CASE_ID_RE = re.compile(r"^(?:POS|NEG)-\d+(?:@[\w-]+)?$")
CORPUS_EXT = (".md", ".py", ".sh", ".yaml", ".yml", ".json", ".template", ".html", ".mjs", ".cjs")
CORPUS_EXCLUDE = ("reference/library/", "tests/", "checks/fixtures/", "eval-stage/checks/fixtures/",
                  "reference/agent-build-example/", "reference/inspection-report-examples/",
                  "reference/example/", "renderer/vendor/", "MANIFEST.json")
_CORPUS_CACHE = {}


def generic_corpus(skill_root=SKILL_ROOT):
    """Lower-cased text of every plugin file where plugin vocabulary lives (filter F3)."""
    key = str(Path(skill_root).resolve())
    if key not in _CORPUS_CACHE:
        parts = []
        root = Path(skill_root)
        for path in sorted(root.rglob("*")):
            if not path.is_file() or not path.name.endswith(CORPUS_EXT) or "__pycache__" in path.parts:
                continue
            rel = path.relative_to(root).as_posix()
            if rel.startswith(CORPUS_EXCLUDE):
                continue
            parts.append(path.read_text(encoding="utf-8", errors="replace").lower())
        _CORPUS_CACHE[key] = "\n".join(parts)
    return _CORPUS_CACHE[key]


def generic_strings(skill_root=SKILL_ROOT):
    doc = json.loads((Path(skill_root) / LIBRARY_ROOT / "generic-strings.json").read_text(encoding="utf-8"))
    return [s for s in doc.get("strings", []) if isinstance(s, str)]


def _words(text):
    return len(text.split())


def _json_labels(value):
    if isinstance(value, dict):
        for v in value.values():
            yield from _json_labels(v)
    elif isinstance(value, list):
        for v in value:
            yield from _json_labels(v)
    elif isinstance(value, str) and 3 <= _words(value) <= 12:
        yield value


def _file_candidates(path, text):
    """[(text, kind)] raw candidates of one file, hash-run overlaps removed."""
    hashes = [m.span() for m in HASH_RUN_RE.finditer(text)]

    def in_hash(span):
        return any(span[0] < h[1] and h[0] < span[1] for h in hashes)

    out = []
    for kind, rx in CANDIDATE_RES:
        for m in rx.finditer(text):
            if not in_hash(m.span()):
                out.append((m.group(0), kind))
    ext = os.path.splitext(str(path))[1].lower()
    if ext == ".json":
        try:
            labels = list(_json_labels(json.loads(text)))
        except ValueError:
            labels = []
        out.extend((v, "label") for v in labels if not HASH_RUN_RE.search(v))
    elif ext in (".yaml", ".yml", ".md"):
        for m in LABEL_TEXT_RE.finditer(text):
            if 3 <= _words(m.group(1)) <= 12 and not in_hash(m.span(1)):
                out.append((m.group(1), "label"))
    return out


def _filter_reason(text, kind, generic, corpus):
    if len(text) < 4:
        return "F1 shorter than 4 characters"
    if text.lower() in generic:
        return "F2 listed in reference/library/generic-strings.json"
    if text.lower() in corpus:
        return "F3 occurs in the plugin's generic corpus"
    if kind == "number" and (YEAR_RE.match(text) or len(text) < 6):
        return "F4 a calendar year or fewer than 6 digits"
    if GENERIC_CASE_ID_RE.match(text):
        return "F5 a generic test-case id (POS-n / NEG-n), the scheme the eval templates instruct"
    return None


def _stage_text_files(entry):
    """(reference id, plugin path) for every README and text artifact of every reference."""
    for ref in _references(entry):
        for f in _reference_files(ref):
            if reviewer_kind(f["path"]) == "text":
                yield ref["id"], f["path"]


def _scan_text(path):
    try:
        return strip_records(path) if carrier_encoding(path) else Path(path).read_text(encoding="utf-8")
    except (OSError, ValueError, UnicodeDecodeError):
        return ""


def extract_candidates(stage, reg=None, skill_root=SKILL_ROOT, explain=None):
    """{text: {"kind", "sources"}} for every candidate surviving F1-F4, plus the placeholder rule.

    EXPLAIN, when a list, receives (text, kind, source, filter reason) for every removed candidate."""
    reg = load() if reg is None else reg
    entry = stage_entry(stage, reg)
    generic = {g.lower() for g in generic_strings(skill_root)}
    corpus = generic_corpus(skill_root)
    placeholders = {p.lower(): p for p in PLACEHOLDER_STRINGS}
    found = {}
    for rid, rel in _stage_text_files(entry):
        text = _scan_text(Path(skill_root) / rel)
        for cand, kind in _file_candidates(rel, text):
            reason = _filter_reason(cand, kind, generic, corpus)
            if reason and cand not in placeholders.values():
                if explain is not None:
                    explain.append((cand, kind, rid, reason))
                continue
            slot = found.setdefault(cand, {"kind": kind, "sources": set()})
            if KINDS.index(kind) < KINDS.index(slot["kind"]):
                slot["kind"] = kind
            slot["sources"].add(rid)
        for low, literal in placeholders.items():
            if re.search(r"(?<![A-Za-z0-9])" + re.escape(low) + r"(?![A-Za-z0-9])", text, re.IGNORECASE):
                found.setdefault(literal, {"kind": "name", "sources": set()})["sources"].add(rid)
    order = {ref["id"]: i for i, ref in enumerate(_references(entry))}
    for slot in found.values():
        slot["sources"] = sorted(slot["sources"], key=lambda r: order.get(r, 99))
    return found


def generated_from(stage, reg=None, skill_root=SKILL_ROOT):
    """{reference id: {plugin path: sha256 of the file on disk now}} for the stage's text files."""
    reg = load() if reg is None else reg
    out = {}
    for rid, rel in _stage_text_files(stage_entry(stage, reg)):
        out.setdefault(rid, {})[rel] = _sha256_file(Path(skill_root) / rel)
    return out


def _registered_hashes(stage, reg):
    out = {}
    for ref in _references(stage_entry(stage, reg)):
        out[ref["id"]] = {f["path"]: f["sha256"] for f in _reference_files(ref)
                          if reviewer_kind(f["path"]) == "text"}
    return out


def _read_string_set(stage, reg, skill_root):
    ds = stage_entry(stage, reg).get("distinctive_strings") or {}
    return json.loads((Path(skill_root) / ds["path"]).read_text(encoding="utf-8"))


def _s1_problems(doc, stage, reg):
    problems = []
    expected = _registered_hashes(stage, reg)
    got = doc.get("generated_from") if isinstance(doc.get("generated_from"), dict) else {}
    if set(got) != set(expected):
        problems.append("copy-guard strings stale: generated_from covers %s but the stage registers %s"
                        % (sorted(got), sorted(expected)))
    for rid, files in expected.items():
        have = got.get(rid) if isinstance(got.get(rid), dict) else {}
        for rel, sha in files.items():
            if have.get(rel) != sha:
                problems.append("copy-guard strings stale: %s changed since the strings were generated" % rel)
        for rel in have:
            if rel not in files:
                problems.append("copy-guard strings stale: %s is no longer registered" % rel)
    return problems


def _string_set_problems(reg, stage, skill_root, rules=("list", "S1", "S2", "S3")):
    """The stored copy-guard string set's list rules (5.2) and staleness rules S1-S3 (5.5)."""
    try:
        doc = _read_string_set(stage, reg, skill_root)
    except (OSError, ValueError) as exc:
        return ["%s: distinctive strings file is unreadable: %s" % (stage, exc)]
    if not isinstance(doc, dict):
        return ["%s: distinctive strings file must be a JSON object" % stage]
    problems = []
    ids = set(reference_ids(stage, reg))
    strings = doc.get("strings") if isinstance(doc.get("strings"), list) else None
    dropped = doc.get("dropped", []) if isinstance(doc.get("dropped", []), list) else None
    placeholders = set(PLACEHOLDER_STRINGS)
    candidates = None
    if "list" in rules:
        if doc.get("schema_version") != 1 or doc.get("stage") != stage:
            problems.append("%s: distinctive strings need schema_version 1 and stage %r" % (stage, stage))
        if strings is None or dropped is None:
            return problems + ["%s: distinctive strings need strings and dropped lists" % stage]
        if not strings and not (isinstance(doc.get("empty_reason"), str) and doc["empty_reason"].strip()):
            problems.append("%s: an empty strings list needs a non-empty empty_reason" % stage)
        texts = {}
        for where, entries in (("strings", strings), ("dropped", dropped)):
            for e in entries:
                if not isinstance(e, dict) or not isinstance(e.get("text"), str) or not e["text"]:
                    problems.append("%s: every %s entry needs a text" % (stage, where))
                    continue
                t = e["text"]
                if e.get("kind") not in KINDS:
                    problems.append("%s: %s entry %r has kind %r (use one of %s)" % (stage, where, t, e.get("kind"), list(KINDS)))
                srcs = e.get("sources")
                if not isinstance(srcs, list) or not srcs or any(s not in ids for s in srcs):
                    problems.append("%s: %s entry %r needs sources registered for this stage" % (stage, where, t))
                if where == "dropped" and not (isinstance(e.get("reason"), str) and e["reason"].strip()):
                    problems.append("%s: dropped entry %r needs a reason" % (stage, t))
                if t in texts and texts[t] != where:
                    problems.append("%s: %r is in both strings and dropped" % (stage, t))
                texts[t] = where
                if t not in placeholders:
                    if len(t) < 4:
                        problems.append("%s: distinctive string %r is shorter than 4 characters" % (stage, t))
                    if t.lower() in generic_corpus(skill_root):
                        problems.append("%s: distinctive string %r occurs in the plugin's generic corpus" % (stage, t))
        for t in placeholders:
            if any(isinstance(e, dict) and e.get("text") == t for e in dropped):
                problems.append("%s: the anonymized client placeholder %s may never be dropped: its appearance "
                                "in an output signals copying" % (stage, t))
        candidates = extract_candidates(stage, reg, skill_root)
        for t in placeholders:
            if t in candidates and not any(isinstance(e, dict) and e.get("text") == t for e in strings):
                problems.append("%s: the anonymized client placeholder %s occurs in this stage's references and "
                                "must be listed in strings" % (stage, t))
        for e in strings + dropped:
            if not isinstance(e, dict) or not isinstance(e.get("text"), str):
                continue
            if not any(e["text"] in _scan_text(Path(skill_root) / rel)
                       for rid, rel in _stage_text_files(stage_entry(stage, reg)) if rid in (e.get("sources") or [])):
                problems.append("%s: distinctive string %r does not occur in any artifact of its sources" % (stage, e["text"]))
    if "S1" in rules:
        problems.extend("%s: %s" % (stage, p) for p in _s1_problems(doc, stage, reg))
    if strings is None or dropped is None:
        return problems
    if "S2" in rules or "S3" in rules:
        candidates = candidates if candidates is not None else extract_candidates(stage, reg, skill_root)
        listed = {e["text"]: e for e in strings + dropped if isinstance(e, dict) and isinstance(e.get("text"), str)}
    if "S2" in rules:
        for t, slot in sorted(candidates.items()):
            if t not in listed:
                problems.append("%s: copy-guard strings stale: new candidate %s in %s is neither listed nor dropped"
                                % (stage, t, ", ".join(slot["sources"])))
        for t in sorted(listed):
            if t not in candidates:
                problems.append("%s: copy-guard strings stale: %s is no longer a candidate" % (stage, t))
    if "S3" in rules:
        for t, e in sorted(listed.items()):
            for src in e.get("sources") or []:
                if src not in ids:
                    problems.append("%s: copy-guard strings stale: %s lists source %s, which is not registered"
                                    % (stage, t, src))
                elif t in candidates and src not in candidates[t]["sources"]:
                    problems.append("%s: copy-guard strings stale: %s lists source %s, whose files no longer "
                                    "contain it" % (stage, t, src))
    return problems


def string_set_problems(stage):
    """Staleness rules S1-S3 for the shipped registry's string set of STAGE."""
    reg = load()
    if state(stage, reg) != "available":
        return []
    return _string_set_problems(reg, stage, SKILL_ROOT, rules=("S1", "S2", "S3"))


def string_set_sources_current(stage, reg=None):
    """S1 only (cheap hash comparison), used at run time by staging and the copy guard."""
    reg = load() if reg is None else reg
    if state(stage, reg) != "available":
        return True
    try:
        return not _s1_problems(_read_string_set(stage, reg, SKILL_ROOT), stage, reg)
    except (OSError, ValueError):
        return False


def active_strings(stage, reg=None):
    reg = load() if reg is None else reg
    if state(stage, reg) != "available":
        return []
    return [e for e in _read_string_set(stage, reg, SKILL_ROOT).get("strings", []) if isinstance(e, dict)]


# --------------------------------------------------------------------------------------------------
# Carrier companions: the pinned files the copy guard scans beside the artifact (CARRIER_COMPANIONS)


class CompanionError(Exception):
    """A companion the carrier names cannot be bound (a setup error: the copy guard exits 2).

    POINTER is the registered pointer, AT the concrete location in the carrier, PATH the pinned path (None when the
    pointer resolved to no {path, sha256} pair at all)."""

    def __init__(self, pointer, at, path, why):
        self.pointer, self.at, self.path, self.why = pointer, at, path, why
        where = "companion pointer %s (at %s)" % (pointer, at)
        if path is not None:
            where += ", path %s" % path
        super().__init__("%s: %s" % (where, why))


def pointer_problem(pointer):
    """Why POINTER is not a well-formed companion pointer; None when it is."""
    if not isinstance(pointer, str) or not pointer.startswith("/"):
        return "a JSON pointer is a string starting with /"
    segments = pointer[1:].split("/")
    if len(segments) < 2:
        return "it must name the path of a {path, sha256} pair inside the carrier (at least two segments)"
    if any(seg == "" for seg in segments):
        return "empty segment"
    if any(re.search(r"~(?![01])", seg) for seg in segments):
        return "~ must be escaped as ~0 (and / as ~1)"
    if any("*" in seg and seg != "*" for seg in segments):
        return "* must be a whole segment"
    if segments[-1] != "path":
        return "the last segment must be path (the path half of a {path, sha256} pair)"
    return None


def _unescape(segment):
    return segment.replace("~1", "/").replace("~0", "~")


def _inside(root, path):
    root = os.path.realpath(root)
    full = os.path.realpath(path)
    return full == root or full.startswith(root + os.sep)


def resolve_companions(stage, carrier, run_root="."):
    """[{pointer, at, path, file}] for every companion STAGE's CARRIER_COMPANIONS entry names in CARRIER.

    CARRIER is the parsed submitted artifact (a dict/list from its own format's parser). Each pointer is walked
    segment by segment; a `*` segment fans out over every element of a list. The value the walk reaches must be the
    `path` of a `{path, sha256}` pair whose path resolves inside RUN_ROOT to a file hashing to the pinned sha256.
    `file` is that file's absolute path, `path` the pinned path, `at` the concrete location (e.g. /systems/1/...).

    The one legitimate absence: a `*` over an empty list fans out to nothing, so that pointer names no companion and
    passes. Every other failure to reach a bound file raises CompanionError: a missing key or index, a null or
    non-container where the walk must continue, a `*` over a non-list, a value that is not a {path, sha256} pair, a
    path escaping the run root, a missing file, or a hash that differs from the pinned one."""
    out = []
    for pointer in CARRIER_COMPANIONS.get(stage, []):
        segments = pointer[1:].split("/")
        nodes = [("", carrier)]
        for depth, seg in enumerate(segments[:-1]):
            nxt = []
            for at, node in nodes:
                if seg == "*":
                    if not isinstance(node, list):
                        raise CompanionError(pointer, at or "/", None, "the carrier has no list here to expand *")
                    nxt.extend(("%s/%d" % (at, i), item) for i, item in enumerate(node))
                    continue
                key = _unescape(seg)
                here = "%s/%s" % (at, seg)
                if isinstance(node, dict) and key in node:
                    nxt.append((here, node[key]))
                elif isinstance(node, list) and key.isdigit() and int(key) < len(node):
                    nxt.append((here, node[int(key)]))
                elif (depth == len(segments) - 2 and pointer in COMPANION_NULL_WHEN.get(stage, {})
                      and isinstance(node, dict)):
                    # The pair's own key is absent on a pointer that may be null: the same as a present null.
                    nxt.append((here, None))
                else:
                    raise CompanionError(pointer, here, None, "resolves to nothing in the carrier")
            nodes = nxt
        for at, pair in nodes:
            here = at + "/path"
            if pair is None and pointer in COMPANION_NULL_WHEN.get(stage, {}):
                gate, allowed = COMPANION_NULL_WHEN[stage][pointer]
                found, value = _walk(carrier, gate)
                if found and value == allowed:
                    continue
                raise CompanionError(pointer, here, None, "the pair is null, which only %s: %s permits (found %s)"
                                     % (gate, allowed, json.dumps(value) if found else "nothing there"))
            if not isinstance(pair, dict) or not isinstance(pair.get("path"), str) or not pair["path"]:
                raise CompanionError(pointer, here, None, "resolves to nothing in the carrier (no {path, sha256} pair)")
            path = pair["path"]
            sha = pair.get("sha256")
            if not isinstance(sha, str) or not SHA_RE.match(sha):
                raise CompanionError(pointer, here, path, "the pair carries no pinned sha256")
            full = os.path.join(run_root, path)
            if not _inside(run_root, full):
                raise CompanionError(pointer, here, path, "the path escapes the run root")
            if not os.path.isfile(full):
                raise CompanionError(pointer, here, path, "the file is missing")
            if _sha256_file(full) != sha:
                raise CompanionError(pointer, here, path, "the file's sha256 differs from the pinned one "
                                                          "(changed after pinning)")
            out.append({"pointer": pointer, "at": here, "path": path, "file": os.path.realpath(full)})
    return out


def _walk(doc, pointer):
    """(found, value) at POINTER (no `*`) in DOC."""
    node = doc
    for seg in pointer[1:].split("/"):
        key = _unescape(seg)
        if isinstance(node, dict) and key in node:
            node = node[key]
        elif isinstance(node, list) and key.isdigit() and int(key) < len(node):
            node = node[int(key)]
        else:
            return False, None
    return True, node


# Carrier packages: the built package files the copy guard scans beside the artifact (CARRIER_PACKAGES)

PACKAGE_BUILDER = "_sandbox_ship_build_shared"
REBUILD = "rebuild the package with scripts/sandbox-ship-build.py --run-root <run root> --spec <spec>"


class PackageError(Exception):
    """The package the carrier lists cannot be bound to a matching build (a setup error: the copy guard exits 2)."""


def package_pointer_problem(pointer):
    """Why POINTER is not a well-formed package pointer (one block, no `*`); None when it is."""
    if not isinstance(pointer, str) or not pointer.startswith("/") or pointer == "/":
        return "a JSON pointer is a string starting with / that names at least one segment"
    segments = pointer[1:].split("/")
    if any(seg == "" for seg in segments):
        return "empty segment"
    if any(re.search(r"~(?![01])", seg) for seg in segments):
        return "~ must be escaped as ~0 (and / as ~1)"
    if any("*" in seg for seg in segments):
        return "a package pointer names one block: no * segment"
    return None


def _package_builder():
    """The stage 12 builder library, loaded by file path under one module name, exactly as
    checks/_sandbox_ship_clearance.py loads it: the package hash has one definition."""
    if PACKAGE_BUILDER in sys.modules:
        return sys.modules[PACKAGE_BUILDER]
    import importlib.util
    path = os.path.join(str(SKILL_ROOT), "scripts", "_sandbox_ship_build.py")
    spec = importlib.util.spec_from_file_location(PACKAGE_BUILDER, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[PACKAGE_BUILDER] = mod
    spec.loader.exec_module(mod)
    return mod


def resolve_packages(stage, carrier, run_root="."):
    """[{pointer, at, path, file}] for every file of the package STAGE's CARRIER_PACKAGES entry names in CARRIER,
    taken from the built package (`file` is `<root>/<dest>`, absolute; `at` the files[] entry; `path` run-relative).

    The files are returned only when the built package.lock.json's package_sha256 equals package_hash() recomputed
    through the one builder implementation twice: over the sources the carrier lists, and over the built files. A
    package block that is missing or malformed, a source or built file that is missing or outside its folder, a
    missing build or lock, or a lock that disagrees (a source or built file changed after the build) raises
    PackageError naming what to rebuild. A stage with no CARRIER_PACKAGES entry has no package files."""
    pointer = CARRIER_PACKAGES.get(stage)
    if pointer is None:
        return []
    found, block = _walk(carrier, pointer)
    if not found or not isinstance(block, dict):
        raise PackageError("package pointer %s resolves to nothing in the carrier" % pointer)
    root, files = block.get("root"), block.get("files")
    if not isinstance(root, str) or not root or not isinstance(files, list) or not files:
        raise PackageError("package pointer %s: the block needs a root and a non-empty files list" % pointer)
    package_root = os.path.join(run_root, root)
    if os.path.isabs(root) or not _inside(run_root, package_root):
        raise PackageError("package pointer %s: root %s escapes the run root" % (pointer, root))
    build = _package_builder()
    lock_path = os.path.join(package_root, build.LOCK_NAME)
    if not os.path.isfile(lock_path):
        raise PackageError("the package at %s is not built (no %s); %s" % (root, build.LOCK_NAME, REBUILD))
    try:
        with open(lock_path, encoding="utf-8") as f:
            locked = json.load(f).get("package_sha256")
    except (OSError, ValueError, AttributeError) as exc:
        raise PackageError("%s/%s is not readable (%s); %s" % (root, build.LOCK_NAME, exc, REBUILD))
    sources, built, out = [], [], []
    for i, item in enumerate(files):
        at = "%s/files/%d" % (pointer, i)
        if not isinstance(item, dict) or not isinstance(item.get("src"), str) or not isinstance(item.get("dest"), str):
            raise PackageError("package entry %s needs both src and dest" % at)
        src = os.path.join(run_root, item["src"])
        dest = build.normalize_dest(item["dest"])
        full = os.path.join(package_root, dest)
        if os.path.isabs(item["src"]) or not _inside(run_root, src) or not os.path.isfile(src):
            raise PackageError("package entry %s: source %s is missing or outside the run root" % (at, item["src"]))
        if os.path.isabs(item["dest"]) or not _inside(package_root, full) or not os.path.isfile(full):
            raise PackageError("package entry %s: %s is missing from the built package at %s; %s"
                               % (at, dest, root, REBUILD))
        sources.append((item["dest"], _sha256_file(src)))
        built.append((item["dest"], _sha256_file(full)))
        out.append({"pointer": pointer, "at": at, "path": os.path.join(root, dest), "file": os.path.realpath(full)})
    try:
        generated = build.generated_hashes(package_root, block.get("build"))
    except build.BuildError as exc:
        raise PackageError("%s (%s)" % (exc, REBUILD))
    now = build.package_hash(sources, generated)
    if now != locked:
        raise PackageError("a package source changed after the build: %s/%s records %s, the sources hash to %s now; %s"
                           % (root, build.LOCK_NAME, locked, now, REBUILD))
    if build.package_hash(built, generated) != locked:
        raise PackageError("a built package file at %s differs from its lock (edited after the build); %s"
                           % (root, REBUILD))
    return out


# --------------------------------------------------------------------------------------------------
# Copy-guard matching


def _collapse(text):
    """(collapsed text, original line number per collapsed character)."""
    out, lines, line, prev_space = [], [], 1, False
    for ch in text:
        if ch.isspace():
            if not prev_space:
                out.append(" ")
                lines.append(line)
            prev_space = True
        else:
            out.append(ch)
            lines.append(line)
            prev_space = False
        if ch == "\n":
            line += 1
    return "".join(out), lines


def string_pattern(entry):
    text = " ".join(entry["text"].split())
    forms = [text]
    if entry.get("kind") == "amount" and "," in text:
        forms.append(text.replace(",", ""))
    body = "|".join(re.escape(f) for f in forms)
    flags = re.IGNORECASE if entry.get("kind") in ("name", "label") else 0
    return re.compile(r"(?<![A-Za-z0-9])(?:" + body + r")(?![A-Za-z0-9])", flags)


def collapse(text):
    """TEXT collapsed once for any number of find_in_collapsed calls: (collapsed text, line per character)."""
    return _collapse(text)


def find_in_collapsed(entry, collapsed, lines):
    """Line number (1-based) of the first match of ENTRY in a text already collapsed by collapse(), or None."""
    m = string_pattern(entry).search(collapsed)
    return lines[m.start()] if m else None


def find_string(entry, text):
    """Line number (1-based) of the first match of ENTRY in TEXT, or None."""
    collapsed, lines = _collapse(text)
    return find_in_collapsed(entry, collapsed, lines)


# --------------------------------------------------------------------------------------------------
# Handing references to executors and reviewers


HEADING = "## Worked references (shape exemplars, not sources)"
RECORD_HEADING = "### Record which reference you followed"
EXECUTOR_INSTRUCTION = (
    "The files below are finished outputs of this stage from other, anonymized engagements, one for each "
    "complexity level the plugin holds. Read every level. Follow the one closest in shape to what you are "
    "building: its structure, sections, level of detail, and how it carries evidence and open questions. "
    "Never copy its content. Names, identifiers, amounts, steps, systems and labels must come from this "
    "engagement's inputs; a script fails any output that carries strings unique to a reference. Where this "
    "engagement genuinely needs a different shape, depart from the reference and state each departure and its "
    "reason in the record.")
EXECUTOR_FIRST_SENTENCE = EXECUTOR_INSTRUCTION.split(". ")[0] + "."
NO_REFERENCE_YET_TEXT = ("No worked reference exists for this stage yet. Build from the inputs and instructions "
                         "above. Do not add a reference record to your output.")
REVIEWER_INSTRUCTION = (
    "WORKED REFERENCES — SHAPE EXEMPLARS, NOT SOURCES. The files below are finished outputs of this same stage "
    "from other, anonymized engagements, one per complexity level. They show the expected shape: structure, "
    "sections, level of detail, and how evidence and open questions are carried. They are not evidence about "
    "this client. Judge the artifact's shape against the reference or references its `reference_use` record "
    "says it followed, under the `reference_shape` criterion. Never lower any score because the artifact's "
    "content (names, steps, systems, rules, amounts, counts) differs from a reference: that content comes from "
    "this client's evidence. Never raise a score because content matches a reference. The record may list "
    "deliberate divergences from the followed reference's shape, each with a reason. Accept a stated "
    "divergence when its reason is specific and holds against this client's evidence or this stage's "
    "template. Treat an unstated structural departure, or a divergence whose reason does not hold, as a shape "
    "finding. If the artifact reproduces reference-specific content (names, identifiers, amounts or labels "
    "unique to a reference), report it as a finding.")
REVIEWER_FIRST_SENTENCE = "WORKED REFERENCES — SHAPE EXEMPLARS, NOT SOURCES."
REFERENCE_SHAPE_CRITERION = {
    "criterion": "reference_shape", "name": "Follows the chosen worked reference's shape", "critical": False,
    "anchors": {
        "0": "No reference record, or the output ignores the shape of every supplied reference.",
        "1": "A record is present but the output's structure matches none of the supplied references.",
        "2": "Follows the chosen reference in places; major structural elements of it are missing with no stated divergence.",
        "3": "Follows the chosen reference's overall structure, but one or more structural elements differ without a stated reason, or another supplied level is clearly the closer fit.",
        "4": "Follows the chosen reference's structure; every structural difference is stated in the record with a reason that holds.",
        "5": "As 4, and the choice reason and divergence reasons are specific to this engagement's evidence. Content differences never lower this score.",
    },
}
REFERENCE_SET_RE = re.compile(r"<!--\s*REFERENCE-SET\s*(.*?)\s*-->", re.DOTALL)


PLUGIN_ROOT_PLACEHOLDER = "{{ PLUGIN_ROOT }}"


def plugin_root():
    """The absolute path of this skill folder (the one holding checks/, templates/, scripts/).

    Derived from this file's own location at render time, so a packet names plugin files by a path that
    resolves from any working directory; never hard-coded to a machine, never read from the environment."""
    return str(SKILL_ROOT)


def plugin_path(rel):
    """REL (a plugin-relative path such as checks/check-reference-use-record.sh) as a full path."""
    return str(SKILL_ROOT / rel)


def fill_plugin_root(text):
    """TEXT with every {{ PLUGIN_ROOT }} placeholder replaced by plugin_root()."""
    return text.replace(PLUGIN_ROOT_PLACEHOLDER, plugin_root())


def example_record(stage, reg=None):
    refs = exemplar_set(stage, reg)
    first = refs[0]
    primary = next(f for f in first["files"] if f["role"] == "primary")
    return {"schema": RECORD_SCHEMA, "stage": stage, "considered": [r["reference_id"] for r in refs],
            "followed": [{"reference_id": first["reference_id"], "level": first["level"],
                          "path": primary["path"], "sha256": primary["sha256"]}],
            "reason": "<why this level is closest in shape>", "divergences": []}


def record_instruction(stage, reg=None):
    entry = stage_entry(stage, reg)
    enc = entry["record_encoding"]
    rec = example_record(stage, reg)
    if enc == "json-key":
        head = "Add one top-level key `reference_use` to your output JSON with this shape:"
        example = "```json\n" + json.dumps({RECORD_KEY: rec}, indent=2, ensure_ascii=False) + "\n```"
    elif enc == "yaml-line":
        head = ("Add one line at column 0 of your output YAML, `reference_use: ` followed by the record as a "
                "single-line JSON object:")
        example = "```yaml\nreference_use: " + json.dumps(rec, ensure_ascii=True, separators=(", ", ": ")) + "\n```"
    else:
        head = "Add one HTML comment to your output Markdown, `<!-- REFERENCE-USE ` + the record as JSON + ` -->`:"
        example = "```markdown\n<!-- REFERENCE-USE " + json.dumps(rec, ensure_ascii=False) + " -->\n```"
    ids = ", ".join(r["reference_id"] for r in exemplar_set(stage, reg))
    tail = ("`considered` lists every reference id above (%s). `followed` names the one you followed (more than "
            "one only if you genuinely combined them), each entry carrying all four of its `reference_id`, "
            "`level`, primary `path` and `sha256` copied exactly as listed, and each id also in `considered`. "
            "`reason` says, in a sentence about this engagement, why that level is the closest shape. "
            "`divergences` lists each deliberate departure from its shape with its reason, or is `[]`. Before "
            "returning, run `%s --stage %s <your output>` and `%s --stage %s <your output>` from the run "
            "folder (the checks by their full path in the plugin, your output by its run-folder path); both "
            "must exit 0." % (ids, plugin_path("checks/check-reference-use-record.sh"), stage,
                              plugin_path("checks/check-reference-copy-guard.sh"), stage))
    return "%s\n\n%s\n\n%s" % (head, example, tail)


def render_packet_block(stage, with_heading=True, reg=None):
    """The executor's Worked references section (the template form omits only the heading line)."""
    st = state(stage, reg)
    if st == "not-built-yet":
        body = NO_REFERENCE_YET_TEXT + "\n"
    elif st == "available":
        refs = exemplar_set(stage, reg)
        lines = [EXECUTOR_INSTRUCTION, "", "<!-- REFERENCE-SET", json.dumps(refs, indent=2), "-->", ""]
        for ref in refs:
            lines.append("- **%s** (`%s`)" % (ref["level"], ref["reference_id"]))
            for f in ref["files"]:
                label = "README" if f["role"] == "readme" else f["role"]
                lines.append("  - %s: `%s` · sha256 `%s`" % (label, f["path"], f["sha256"]))
        lines += ["", RECORD_HEADING, "", record_instruction(stage, reg)]
        body = "\n".join(lines) + "\n"
    else:
        return ""
    return (HEADING + "\n\n" + body) if with_heading else body


def render_brief_block(stage, reg=None):
    """The review brief's Worked references section; empty unless the stage's references exist."""
    if state(stage, reg) != "available":
        return ""
    lines = [HEADING, "", REVIEWER_INSTRUCTION, ""]
    for ref in exemplar_set(stage, reg):
        for f in ref["files"]:
            lines += ["- artifact: %s" % f["path"], "  sha256: %s" % f["sha256"]]
    return "\n".join(lines) + "\n"


def stage_references(run_root, stage, reg=None):
    """Copy every level of an available stage into RUN_ROOT/references/<stage>/ and pin it.

    Returns exemplar_set(stage) ([] for not-built-yet / none-by-design). Raises KeyError for an unknown
    stage, RegistryStale on plugin drift or a stale string set, StagingError when a staged copy differs."""
    reg = load() if reg is None else reg
    st = state(stage, reg)
    if st is None:
        raise KeyError(stage)
    if st != "available":
        return []
    verify_plugin_files(stage, reg)
    if not string_set_sources_current(stage, reg):
        raise RegistryStale("copy-guard strings stale: regenerate with reference-fingerprints.py --write, "
                            "then run scripts/reference-registry.py rehash --stage %s" % stage)
    root = Path(run_root)
    plan = []
    for ref in _references(stage_entry(stage, reg)):
        for f in _reference_files(ref):
            dest = root / staged_path(stage, ref["level"], os.path.basename(f["path"]))
            if dest.exists() and _sha256_file(dest) != f["sha256"]:
                raise StagingError("staged copy %s differs from the registry; a run keeps its pinned references "
                                   "- start a new run or remove the staged folder deliberately"
                                   % dest.relative_to(root))
            plan.append((SKILL_ROOT / f["path"], dest))
    refs = exemplar_set(stage, reg)
    staging = {"schema_version": 1, "stage": stage, "registry_sha256": registry_sha256(), "references": refs}
    staging_path = root / "references" / stage / "staging.json"
    staging_text = json.dumps(staging, indent=2) + "\n"
    if staging_path.exists() and not _same_staging(staging_path, staging):
        raise StagingError("references/%s/staging.json differs from the registry; a run keeps its pinned "
                           "references - start a new run or remove the staged folder deliberately" % stage)
    for src, dest in plan:
        if not dest.exists():
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(src.read_bytes())
    if not staging_path.exists():
        staging_path.write_text(staging_text, encoding="utf-8")
    return refs


def _same_staging(path, staging):
    """An existing staging.json pins the same references (the registry's own hash may move on)."""
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return (isinstance(doc, dict) and doc.get("stage") == staging["stage"]
            and same_exemplars(doc.get("references"), staging["references"]))


def packet_reference_set(text):
    """[parsed REFERENCE-SET blocks] found in a packet's text (values None when a block is not JSON)."""
    out = []
    for m in REFERENCE_SET_RE.finditer(text):
        try:
            out.append(json.loads(m.group(1)))
        except ValueError:
            out.append(None)
    return out


def same_exemplars(a, b):
    """Order-insensitive comparison of two exemplar sets (over references and over files)."""
    def norm(refs):
        if not isinstance(refs, list):
            return None
        try:
            return sorted((r["reference_id"], r["level"], tuple(sorted((f["role"], f["path"], f["sha256"])
                                                                       for f in r["files"])))
                          for r in refs)
        except (KeyError, TypeError):
            return None
    return norm(a) is not None and norm(a) == norm(b)
