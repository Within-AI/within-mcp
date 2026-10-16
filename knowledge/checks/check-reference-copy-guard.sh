#!/usr/bin/env python3
# This file is Python despite its .sh name: run it directly or as `python3 <this file>`; `bash <this file>` cannot run it.
"""Reference copy guard: a worked reference is a shape, never content.

Usage (run with the run root as the working directory):
    check-reference-copy-guard.sh --stage KEY ARTIFACT

Fails an output that carries strings unique to its stage's worked references (ids, amounts, names and
labels listed in reference/library/<stage>/distinctive-strings.json, including the anonymized client
placeholder `Acme`). The output's own reference record is stripped before scanning.

The only exemption is the run's own evidence: a string that also appears in a file the stage's latest
`executor.started` event lists in `payload.inputs` (JSON or YAML, followed through any `{path, sha256}` link that
resolves inside the run root with a matching hash, up to four links deep) is client content, not a
copy. The walk never descends into a `reference_use` record carried by an input: a worked reference
named in a record is never evidence. A worked reference of this stage supplied as run evidence (a file under `references/<stage>/`, or
one whose hash equals a registered file of this stage) fails the guard; earlier stages' references may
be inputs.

The guard targets reference strings only. It never looks for client names in outputs: run outputs are
expected to carry the client's names.

Companions. For a stage listed in CARRIER_COMPANIONS (scripts/_reference_registry.py), the guard also
scans every file the artifact pins at those pointers, with the same strings and the same run-evidence
exemption; a hit names the file and the line, and each companion has its own reference record stripped
if it carries one. Stage 11, the pre-build simulation: its manifest plus the coverage statement and
data-shape records it pins (`/coverage/statement/path`, `/systems/*/data_shape/path`). Stage 12, the
Sandbox/Ship spec: the live-run evidence it pins (`/live_run/evidence/path`); a null evidence names no
companion only when `live_run.status` is `not_run` (COMPANION_NULL_WHEN), any other null is a setup error.

Package files. For a stage listed in CARRIER_PACKAGES (today stage 12, `sandbox-ship`: the spec's
`/package` block), the guard also scans every file `package.files[]` lists, read from the built package
(`<package.root>/<dest>`), with the same strings and the same run-evidence exemption; a hit names the file
and the line. The listed files carry no hash of their own, so they are scanned only when the built
`package.lock.json` agrees with package_hash() recomputed through the one builder implementation
(scripts/_sandbox_ship_build.py), over the listed sources and over the built files. A missing build, a
missing lock, or a lock that disagrees (a source or built file changed after the build) is a setup error
(exit 2) naming what to rebuild, never a silent skip. `resolve_packages` in scripts/_reference_registry.py
is the one resolution.

Every other stage is scanned on its submitted artifact only. The artifact is parsed as its own format
(YAML through the stage 11 checks' loader, checks/_prebuild_sim.py, which needs PyYAML; JSON as JSON).
A companion is scanned only when its pinned path resolves inside the run root and the file hashes to
the pinned sha256. A pointer that reaches nothing, a path escaping the run root, a missing file or a
changed hash is a setup error (exit 2) naming the pointer and path, never a silent skip. The one
legitimate absence is a `*` over an empty list (`systems: []`), which names no companion and passes.

Exit: 0 pass; 1 the output carries a reference's strings (or a same-stage reference was supplied as
evidence); 2 setup error (unknown stage, missing or unsupported artifact, registry or string-set drift, a
companion that cannot be bound, a package that is unbuilt or disagrees with its lock, or the YAML loader a
stage with companions or a package needs is missing).
"""
import hashlib
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import _reference_registry as rr  # noqa: E402
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _sandbox_ship_leak as leak  # noqa: E402  (its binary test is the one the leak scan uses)

MAX_DEPTH = 4


def latest_inputs(stage):
    log = Path("events.jsonl")
    if not log.is_file():
        return []
    inputs = []
    for line in log.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if event.get("stage") == stage and event.get("type") == "executor.started":
            inputs = event.get("payload", {}).get("inputs") or []
    return [i for i in inputs if isinstance(i, dict) and isinstance(i.get("path"), str)]


def inside_root(path):
    root = os.path.realpath(".")
    full = os.path.realpath(path)
    return full == root or full.startswith(root + os.sep)


def links(value):
    """Every {path, sha256} link in VALUE, except inside a reference_use record (at any depth).

    A record names the worked references its author followed; those are shapes, never run evidence,
    so the walk does not descend into one.
    """
    if isinstance(value, dict):
        if isinstance(value.get("path"), str) and isinstance(value.get("sha256"), str):
            yield value["path"], value["sha256"]
        for k, v in value.items():
            if k == rr.RECORD_KEY:
                continue
            yield from links(v)
    elif isinstance(value, list):
        for v in value:
            yield from links(v)


def parse_structured(text):
    """TEXT parsed as JSON, else as YAML through the stage 11 checks' loader (checks/_prebuild_sim.py); None when
    it is neither (or the YAML loader is unavailable), so the file is scanned as text only."""
    try:
        return json.loads(text)
    except ValueError:
        pass
    try:
        import _prebuild_sim as pbs
        return pbs.yaml_mod().safe_load(text)
    except Exception:  # noqa: BLE001 - not YAML, or no loader: a plain-text evidence file
        return None


def evidence_files(inputs):
    """[(run-relative path, text)] for the recorded inputs and the files they link to."""
    seen, out = set(), []
    queue = [(Path(i["path"]), i.get("sha256"), 0, True) for i in inputs]
    while queue:
        path, sha, depth, direct = queue.pop(0)
        if not inside_root(path) or not path.is_file():
            continue
        real = os.path.realpath(path)
        if real in seen:
            continue
        if sha is not None and rr._sha256_file(path) != sha:
            continue
        seen.add(real)
        rel = os.path.relpath(real, os.path.realpath("."))
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        out.append((rel, text))
        if depth >= MAX_DEPTH:
            continue
        doc = parse_structured(text)
        if doc is None:
            continue
        for link_path, link_sha in links(doc):
            if os.path.isabs(link_path):
                continue
            for base in (path.parent, Path(".")):
                candidate = base / link_path
                if inside_root(candidate) and candidate.is_file() and rr._sha256_file(candidate) == link_sha:
                    queue.append((candidate, link_sha, depth + 1, False))
                    break
    return out


class SetupError(Exception):
    """A companion or package file cannot be read or bound, or the carrier cannot be parsed (exit 2)."""


def parse_carrier(artifact):
    """The submitted artifact parsed as its own format: YAML through the stage 11 checks' loader, JSON as JSON."""
    enc = rr.carrier_encoding(artifact)
    if enc == "json-key":
        try:
            return json.loads(Path(artifact).read_text(encoding="utf-8"))
        except (OSError, ValueError, UnicodeDecodeError) as exc:
            raise SetupError("cannot parse %s as JSON to find its companions: %s" % (artifact, exc))
    if enc == "yaml-line":
        try:
            import _prebuild_sim as pbs
        except ImportError as exc:
            raise SetupError("missing dependency: the YAML loader checks/_prebuild_sim.py cannot be imported (%s); "
                             "a stage whose artifact pins companion files needs it" % exc)
        try:
            return pbs.load_yaml(artifact)
        except pbs.PrebuildError as exc:
            if "PyYAML" in str(exc):
                raise SetupError("missing dependency: %s; a stage whose artifact pins companion files needs it" % exc)
            raise SetupError("cannot parse %s to find its companions: %s" % (artifact, exc))
    raise SetupError("%s cannot carry companion pointers (a stage with companions submits JSON or YAML)" % artifact)


def companion_texts(stage, artifact):
    """([(run-relative path, text to scan)], text package file count, binary package files skipped) for every companion the artifact pins and every
    package file it lists (taken from the built package), each bound or SetupError."""
    carrier = parse_carrier(artifact)
    try:
        companions = rr.resolve_companions(stage, carrier, ".")
    except rr.CompanionError as exc:
        raise SetupError("companion not bound: %s" % exc)
    try:
        packaged = rr.resolve_packages(stage, carrier, ".")
    except rr.PackageError as exc:
        raise SetupError("package not bound: %s" % exc)
    except OSError as exc:
        raise SetupError("package file not readable: %s" % exc)
    out, seen, skipped_binary = [], {os.path.realpath(artifact)}, 0
    for comp in packaged:
        if comp["file"] in seen:
            continue
        seen.add(comp["file"])
        try:
            text = leak.read_text_or_none(comp["file"])
        except OSError as exc:
            raise SetupError("package file not readable: %s (entry %s): %s" % (comp["path"], comp["at"], exc))
        if text is None:
            skipped_binary += 1  # binary, skipped from the text scan the way the leak scan skips it
            continue
        out.append((os.path.relpath(comp["file"], os.path.realpath(".")), text))
    n_packaged = len(out)
    for comp in companions:
        if comp["file"] in seen:
            continue
        seen.add(comp["file"])
        try:
            if rr.carrier_encoding(comp["file"]):
                text = rr.strip_records(comp["file"])
            else:
                text = Path(comp["file"]).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise SetupError("companion not readable: companion pointer %s (at %s), path %s: %s"
                             % (comp["pointer"], comp["at"], comp["path"], exc))
        out.append((os.path.relpath(comp["file"], os.path.realpath(".")), text))
    return out, n_packaged, skipped_binary


def main(argv):
    if len(argv) != 3 or argv[0] != "--stage":
        sys.stderr.write("usage: check-reference-copy-guard.sh --stage KEY ARTIFACT\n")
        return 2
    stage, artifact = argv[1], argv[2]
    try:
        reg = rr.load()
    except (OSError, ValueError) as exc:
        sys.stderr.write("registry unreadable: %s\n" % exc)
        return 2
    st = rr.state(stage, reg)
    if st is None:
        sys.stderr.write("unknown stage key %r (not in the reference registry)\n" % stage)
        return 2
    if not Path(artifact).is_file():
        sys.stderr.write("missing artifact: %s\n" % artifact)
        return 2
    if rr.carrier_encoding(artifact) is None:
        sys.stderr.write("unsupported carrier extension for %s (use .json, .yaml, .yml or .md)\n" % artifact)
        return 2
    if st != "available":
        print("PASS reference-copy-guard: no worked references registered for this stage")
        return 0
    try:
        rr.verify_plugin_files(stage, reg)
    except rr.RegistryStale as exc:
        sys.stderr.write("%s\n" % exc)
        return 2
    if not rr.string_set_sources_current(stage, reg):
        sys.stderr.write("copy-guard strings stale: regenerate with reference-fingerprints.py --write, then rehash\n")
        return 2
    registered_shas = {f["sha256"] for ref in rr.stage_entry(stage, reg)["references"]
                       for f in rr._reference_files(ref)}
    evidence = evidence_files(latest_inputs(stage))
    for rel, _text in evidence:
        if Path(rel).parts[:2] == ("references", stage) or rr._sha256_file(rel) in registered_shas:
            print("FAIL reference-copy-guard: a worked reference of this stage was supplied as run evidence (%s)" % rel)
            return 1
    for item in latest_inputs(stage):
        if Path(item["path"]).parts[:2] == ("references", stage) or item.get("sha256") in registered_shas:
            print("FAIL reference-copy-guard: a worked reference of this stage was supplied as run evidence (%s)"
                  % item["path"])
            return 1
    with_companions = stage in rr.CARRIER_COMPANIONS or stage in rr.CARRIER_PACKAGES
    scanned, n_packaged, n_binary = [(artifact, rr.strip_records(artifact))], 0, 0
    if with_companions:
        try:
            extra, n_packaged, n_binary = companion_texts(stage, artifact)
            scanned += extra
        except SetupError as exc:
            sys.stderr.write("%s\n" % exc)
            return 2
    strings = rr.active_strings(stage, reg)
    # Each text is collapsed once per run, not once per string; byte-identical evidence is searched once.
    ev_collapsed, ev_seen = [], set()
    for _rel, ev_text in evidence:
        digest = hashlib.sha256(ev_text.encode("utf-8")).hexdigest()
        if digest not in ev_seen:
            ev_seen.add(digest)
            ev_collapsed.append(rr.collapse(ev_text))
    scanned_collapsed = [(name, rr.collapse(text)) for name, text in scanned]
    exempt, hits = 0, []
    for entry in strings:
        if any(rr.find_in_collapsed(entry, collapsed, lines) for collapsed, lines in ev_collapsed):
            exempt += 1
            continue
        for name, (collapsed, lines) in scanned_collapsed:
            line = rr.find_in_collapsed(entry, collapsed, lines)
            if line is not None:
                hits.append((entry, name, line))
    if hits:
        print("FAIL reference-copy-guard: output carries %d string(s) unique to this stage's worked references:"
              % len({id(entry) for entry, _name, _line in hits}))
        for entry, name, line in hits:
            where = ("at line %d of %s" % (line, name)) if with_companions else ("at line %d" % line)
            print('  "%s" (%s, from %s) %s' % (entry["text"], entry.get("kind"),
                                              ", ".join(entry.get("sources") or []), where))
        return 1
    if with_companions:
        if stage in rr.CARRIER_PACKAGES:
            print("PASS reference-copy-guard (%d strings checked in %d files: the artifact, %d package file(s) and "
                  "%d companion(s); %d exempt as run evidence; %d binary package file(s) skipped)"
                  % (len(strings), len(scanned), n_packaged, len(scanned) - 1 - n_packaged, exempt, n_binary))
            return 0
        print("PASS reference-copy-guard (%d strings checked in %d files: the artifact and %d companion(s); "
              "%d exempt as run evidence)" % (len(strings), len(scanned), len(scanned) - 1, exempt))
        return 0
    print("PASS reference-copy-guard (%d strings checked, %d exempt as run evidence)" % (len(strings), exempt))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
