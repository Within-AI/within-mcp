#!/usr/bin/env python3
"""Client readiness: clear a shipped package before it leaves the build.

    readiness.py scan --run R
    readiness.py adjudicate --run R --flag ID --basis ordinary-wording|own-source|authored-heading --reason TEXT
                            [--source PATH]
    readiness.py escalate --run R --flag ID --reason TEXT
    readiness.py dispose --run R --flag ID --disposition not-a-client|removed --by NAME

Scans every shareable file (everything under R/package/ plus the hand-off bundle's shareable paths) with the
curated client-name rules and the vocabulary lists, checks that no internal trace file ships, and scans the saved
image when the agent ships as a Docker image. Writes R/records/clearance.json bound to the package hash and
appends clearance.recorded.

Client-name candidates are a guess from the shape of the text, so the agent judges each one; the scan never clears
one itself. The run's sources are the text files under R/inputs/ except the shareable files, the bundle's internal
trace files and any file with the same bytes as a shipped file. Each source's declared client is read structurally:
every client_name or client (string or {name}) at any depth of a JSON source, key-value or front-matter lines
such as `client: X`, and Markdown table rows whose first cell is a client key (`| Client | X |`). Each name flag
carries its provenance: {"run_client", "sources": [{"path", "declared_client"}], "foreign"}, where foreign lists
sources declaring another client, and "declared:<name>" when the candidate contains (as whole words, case and
punctuation folded) another client's name that a source or shipped file declares. Another client's declared name
found anywhere in a shipped file is flagged too (rule client:declared).
  - The agent judges a candidate with `adjudicate` and a one-line reason. Bases: ordinary-wording, authored-heading
    (a heading the build wrote), own-source (with --source naming a listed source that holds the text).
  - The agent hands a candidate to a person with `escalate`.
  - Refused, so a person disposes it with `dispose`: a candidate whose provenance shows a source declaring another
    client, and a company-shaped candidate (organisation suffix or domain) that appears in no source of this run.
    Vocabulary, path and id flags are always disposed by a person.
Adjudications live in R/records/clearance-adjudications.json ({"adjudications": [{"flag", "text", "rule", "path",
"basis", "reason", "source", "by", "date"}]}). Every scan reads that record and re-checks each entry against the
current provenance, so an entry that no longer holds is not counted. The exit gate still needs every flag disposed.

Exit 0 cleared; 1 a scan failed, or a candidate awaits the agent's judgement (verdict needs_adjudication);
3 a candidate only a person may clear (refused or escalated), a missing or conflicting client name, or image
applicability that needs more information; 2 usage or unreadable input, or an adjudication the provenance does not
support (nothing written).
"""

import argparse
import io
import os
import sys
import tarfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _records  # noqa: E402
import _locate  # noqa: E402
import _scan_rules  # noqa: E402
import log  # noqa: E402

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VOCABULARY_FILES = (
    os.path.join(SKILL_DIR, "references", "scan-vocabulary.json"),
    os.path.join(SKILL_DIR, "references", "inputs", "formats", "lists", "vocabulary.json"),
)
HANDOFF = "inputs/agent-design/outputs/agent-build-inputs.json"
DESIGN = "inputs/agent-design"
RECORD = "records/clearance.json"
ADJUDICATIONS = "records/clearance-adjudications.json"
SOURCES = "inputs"
SOURCE_MAX = 8 * 1024 * 1024
IMAGE_TAR = "image/image.tar"
IMAGE_MEMBER_MAX = 1024 * 1024
DISPOSITIONS = ("not-a-client", "removed")
NMI = "needs_more_information"


class Usage(Exception):
    """Exit 2: nothing is written."""


def _rel_files(folder):
    out = []
    for root, _dirs, files in os.walk(folder):
        for name in files:
            out.append(os.path.relpath(os.path.join(root, name), folder).replace(os.sep, "/"))
    return sorted(out)


def _read_text(path):
    """Text of a file, or None when it is not UTF-8 text."""
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError:
        return None
    return _decode(data)


def _decode(data):
    if b"\0" in data:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _name_of(value):
    if isinstance(value, dict):
        value = value.get("name")
    if isinstance(value, str) and value.strip():
        return " ".join(value.split())
    return None


def _safe_rel(p):
    return isinstance(p, str) and p and not os.path.isabs(p) and ".." not in p.replace("\\", "/").split("/")


def design_paths(run):
    """(hand-off, design root) as run-relative paths, as the locator returns them; None when it found none
    or refused it (outside the run), which reads as absent."""
    loc = _locate.locate_design(run)
    return (loc.rel(loc.handoff) if loc.handoff else None), (loc.root_rel if loc.root else None)


def load_inputs(run):
    """Read the required inputs; Usage on anything missing or unreadable."""
    if not os.path.isdir(run):
        raise Usage("--run is not a folder: %s" % run)
    pkg = os.path.join(run, "package")
    if not os.path.isdir(pkg):
        raise Usage("package/ missing or not a folder")
    try:
        with open(os.path.join(run, "package.sha256"), encoding="utf-8") as f:
            recorded = f.read().strip()
    except (OSError, UnicodeDecodeError):
        raise Usage("package.sha256 missing or unreadable")
    if not recorded:
        raise Usage("package.sha256 is empty")
    brief, err = _records.load_json(os.path.join(run, "brief.json"))
    if err or not isinstance(brief, dict):
        raise Usage("brief.json missing or unreadable: %s" % (err or "not an object"))
    try:
        current = _records.package_hash(pkg)
    except (ValueError, OSError) as e:
        raise Usage(str(e))
    handoff = None
    handoff_rel = design_paths(run)[0]
    if handoff_rel and os.path.exists(os.path.join(run, handoff_rel)):
        handoff, err = _records.load_json(os.path.join(run, handoff_rel))
        if err or not isinstance(handoff, dict):
            raise Usage("hand-off unreadable: %s" % (err or "not an object"))
        handoff = _locate.normalise_handoff(handoff, [])
    vocab, err = _scan_rules.load_vocabulary(VOCABULARY_FILES)
    if err:
        raise Usage(err)
    return {"brief": brief, "recorded": recorded, "current": current, "handoff": handoff, "vocab": vocab}


def current_client(brief, handoff):
    """(client_name or None, reason or None)."""
    from_brief = _name_of(brief.get("client_name"))
    from_handoff = None
    if handoff is not None:
        from_handoff = _name_of(handoff.get("client_name")) or _name_of(handoff.get("client"))
    if from_brief and from_handoff and from_brief != from_handoff:
        return None, "client name conflicting: brief says %r, hand-off says %r" % (from_brief, from_handoff)
    name = from_brief or from_handoff
    if not name:
        return None, "client name missing: brief.json has no client_name and the hand-off gives none"
    return name, None


def _content_files(run, full):
    """The non-empty files an internal entry holds (itself, or every file under a folder), each contained in the
    run; empty files carry nothing to leak and match any empty package file."""
    if not full or not _locate.contained(run, full):
        return []
    if os.path.isdir(full):
        paths = [os.path.join(full, p) for p in _rel_files(full)]
    else:
        paths = [full]
    return [p for p in paths if os.path.isfile(p) and _locate.contained(run, p) and os.path.getsize(p) > 0]


def split_files(run, handoff, reasons, readings=None):
    """(shareable, internal) as run-relative paths; appends failing reasons."""
    shareable = ["package/" + p for p in _rel_files(os.path.join(run, "package"))]
    internal = []
    forms = {}  # internal rel -> the package-relative paths it could ship under
    readable = {}  # internal rel -> its file as the locator resolved it (contained), for the hash comparison
    located = _locate.locate_design(run)
    design_rel = located.root_rel  # the one accepted design location for every consumer
    design = os.path.join(run, design_rel) if design_rel else None
    bundle = handoff.get("bundle") if isinstance(handoff, dict) else None
    if design is None:
        pass
    elif isinstance(bundle, dict):
        prefix = _locate.bundle_prefix(located, bundle)
        for key, target in (("shareable", shareable), ("internal_trace", internal)):
            for p in bundle.get(key) or []:
                if not _safe_rel(p):
                    reasons.append("bundle path outside the design folder: %r" % (p,))
                    continue
                full = _locate.bundle_file(located, p, prefix)
                declared = p.replace("\\", "/")
                rel = located.rel(full) if full else design_rel + "/" + declared
                if full is None and key == "shareable":
                    reasons.append("shareable file missing: %s" % rel)
                    continue
                if key == "shareable" and not os.path.isfile(full):
                    reasons.append("shareable file missing: %s" % rel)
                    continue
                # A declared internal entry is never dropped: one outside the copied folder stays listed as declared.
                rel = rel + "/" if p.endswith("/") and not rel.endswith("/") else rel
                target.append(rel)
                if key == "internal_trace":
                    stripped = declared[len(prefix):] if prefix and declared.startswith(prefix) else declared
                    forms.setdefault(rel, set()).update((declared, stripped, rel[len(design_rel) + 1:]))
                    readable[rel] = full or _locate.bundle_file(located, p)
    elif os.path.isdir(design):
        internal = [design_rel + "/" + p for p in _rel_files(design)
                    if _locate.contained(run, os.path.join(design, p))]
    pkg = os.path.join(run, "package")
    pkg_hashes = set(_records.sha256_file(os.path.join(pkg, p)) for p in _rel_files(pkg))
    # Bytes the hand-over declares shareable are never internal content, wherever else a copy sits.
    pkg_hashes -= set(_records.sha256_file(os.path.join(run, rel)) for rel in shareable
                      if not rel.startswith("package/") and os.path.isfile(os.path.join(run, rel)))
    for rel in internal:
        inners = forms.get(rel) or {rel[len(design_rel) + 1:]}
        full = readable.get(rel) if rel in readable else os.path.join(run, rel)
        same_hash = bool(full) and any(_records.sha256_file(f) in pkg_hashes for f in _content_files(run, full))
        inners = [i for i in inners if _safe_rel(i)]  # written form only: a package link still counts
        if any(os.path.isfile(os.path.join(pkg, inner)) for inner in inners) or same_hash or rel in shareable:
            reasons.append("internal file in shareable output: %s" % rel)
    if readings is not None:
        readings.extend(located.readings)
    return shareable, internal


def _scan_text(text, path, client, run, vocab, found):
    for lineno, rule, match in _scan_rules.scan_client(text, client, os.path.abspath(run)):
        found.append((rule, path, lineno, match))
    for lineno, rule, match in _scan_rules.vocabulary_hits(text, vocab):
        found.append((rule, path, lineno, match))


def docker_applies(run, brief):
    caps = brief.get("capabilities")
    if isinstance(caps, dict) and "ships_as_docker_image" in caps:
        value = caps["ships_as_docker_image"]
    else:
        doc, err = _records.load_json(os.path.join(run, "capabilities.json"))
        inner = doc.get("capabilities") if isinstance(doc, dict) else None
        value = inner.get("ships_as_docker_image", NMI) if isinstance(inner, dict) else NMI
    if value is True or value is False:
        return value
    return NMI


def scan_image(run, client, vocab, found):
    """Scan R/image/image.tar (docker save format); returns a failing reason or None."""
    path = os.path.join(run, IMAGE_TAR)
    if not os.path.isfile(path):
        return "image tar missing: %s" % IMAGE_TAR
    try:
        with tarfile.open(path, "r:*") as outer:
            for layer in outer.getmembers():
                if not layer.isfile():
                    continue
                data = outer.extractfile(layer).read()
                try:
                    inner = tarfile.open(fileobj=io.BytesIO(data), mode="r:*")
                except tarfile.TarError:
                    continue  # manifest.json, config blobs: not a layer
                with inner:
                    for member in inner.getmembers():
                        if not member.isfile() or member.size > IMAGE_MEMBER_MAX:
                            continue
                        text = _decode(inner.extractfile(member).read())
                        if text is None:
                            continue
                        _scan_text(text, "image:%s/%s" % (layer.name, member.name), client, run, vocab, found)
    except (tarfile.TarError, OSError) as e:
        return "image tar unreadable: %s" % e
    return None


def _declarations(rel, full, text):
    doc = None
    if rel.endswith(".json"):
        doc, _ = _records.load_json(full)
    return _scan_rules.source_declarations(text, doc)


def run_sources(run, shareable, handoff, internal=()):
    """(path, text, declared clients) for every text file under R/inputs/ that is a source (see the module help).
    `internal` is split_files' internal list (located paths; a trailing / marks a folder). It counts only when the
    hand-off declares a bundle: without one, split_files lists all design material, which stays a source."""
    skip = set(shareable)
    folders = ()
    bundle = handoff.get("bundle") if isinstance(handoff, dict) else None
    if isinstance(bundle, dict):
        skip.update(DESIGN + "/" + p.replace("\\", "/") for p in bundle.get("internal_trace") or [] if _safe_rel(p))
        # A folder is one declared with a trailing / or one that is a folder on disk.
        folders = tuple(p if p.endswith("/") else p + "/" for p in internal
                        if p.endswith("/") or os.path.isdir(os.path.join(run, p)))
        skip.update(p for p in internal if not p.endswith("/"))
    shipped = set()
    for rel in shareable:
        full = os.path.join(run, rel)
        if os.path.isfile(full):
            shipped.add(_records.sha256_file(full))
    out = []
    for rel in [SOURCES + "/" + p for p in _rel_files(os.path.join(run, SOURCES))]:
        full = os.path.join(run, rel)
        if rel in skip or rel.startswith(folders):
            continue
        try:
            if os.path.getsize(full) > SOURCE_MAX:
                continue
        except OSError:
            continue
        if _records.sha256_file(full) in shipped:
            continue
        text = _read_text(full)
        if text is None:
            continue
        out.append((rel, text, _declarations(rel, full, text)))
    return out


def shipped_declarations(run, shareable):
    """Client names the shipped files themselves declare."""
    names = []
    for rel in shareable:
        full = os.path.join(run, rel)
        text = _read_text(full)
        if text is not None:
            names.extend(_declarations(rel, full, text))
    return names


def load_adjudications(run):
    """(entries by flag id, problems): the agent's adjudications record, read leniently."""
    doc, err = _records.load_json(os.path.join(run, ADJUDICATIONS))
    if err and not os.path.exists(os.path.join(run, ADJUDICATIONS)):
        return {}, []
    entries = doc.get("adjudications") if isinstance(doc, dict) else doc
    if not isinstance(entries, list):
        return {}, ["adjudications record unreadable, ignored: %s" % (err or "no adjudications list")]
    out = {}
    for entry in entries:
        if isinstance(entry, dict) and isinstance(entry.get("flag"), str):
            out[entry["flag"]] = entry
    return out, []


def _adjudication_disposition(entry):
    return {"value": "not-a-client", "by": "agent", "basis": entry.get("basis"), "reason": entry.get("reason"),
            "source": entry.get("source"), "adjudicated": True, "date": entry.get("date")}


def _earlier_dispositions(run, package_sha256):
    doc, err = _records.load_json(os.path.join(run, RECORD))
    if err or not isinstance(doc, dict):
        return {}
    ident = doc.get("code_identity") or {}
    if ident.get("package_sha256") != package_sha256:
        return {}
    out = {}
    for fl in doc.get("flags") or []:
        disp = fl.get("disposition") if isinstance(fl, dict) else None
        # Agent adjudications are re-read from their own record and re-checked on every scan, never carried.
        if disp is not None and not (isinstance(disp, dict) and disp.get("adjudicated") is True):
            out[fl.get("id")] = disp
    return out


def _valid_disposition(disp, rule):
    """A disposition counts only with an allowed value for the rule and a named person."""
    if not isinstance(disp, dict) or disp.get("value") not in DISPOSITIONS:
        return False
    if disp["value"] == "not-a-client" and rule not in _scan_rules.CLIENT_NAME_RULES:
        return False
    return isinstance(disp.get("by"), str) and bool(disp["by"].strip())


def scan(run, extra=None):
    """Run every scan, write the record and the event; return the exit code. extra = {flag id: disposition}."""
    inp = load_inputs(run)
    brief, handoff, vocab = inp["brief"], inp["handoff"], inp["vocab"]
    failed, needs = [], []
    if inp["current"] != inp["recorded"]:
        failed.append("package changed")
    client, client_reason = current_client(brief, handoff)
    if client_reason:
        needs.append(client_reason)
    readings = []
    shareable, internal = split_files(run, handoff, failed, readings)

    found = []
    for rel in shareable:
        text = _read_text(os.path.join(run, rel))
        if text is not None:
            _scan_text(text, rel, client, run, vocab, found)

    applies = docker_applies(run, brief)
    if applies is True:
        image_failed = scan_image(run, client, vocab, found)
        if image_failed:
            failed.append(image_failed)
    elif applies == NMI:
        needs.append("image scan applicability needs more information")

    carried = {}
    if inp["current"] == inp["recorded"]:
        carried = _earlier_dispositions(run, inp["recorded"])
    carried.update(extra or {})
    sources = run_sources(run, shareable, handoff, internal)
    index = _scan_rules.source_index(sources, client)
    foreign_names = [d for _p, _t, declared in sources for d in declared]
    foreign_names += shipped_declarations(run, shareable)
    foreign_names = [n for n in dict.fromkeys(foreign_names) if not _scan_rules.is_own_client(n, client)]
    # Another client's declared name anywhere in a shipped file, whatever its shape, is a candidate too.
    for rel in shareable:
        text = _read_text(os.path.join(run, rel))
        if text is None:
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            for name in foreign_names:
                if _scan_rules.contains_name(line, name):
                    found.append(("client:declared", rel, lineno, name))
    adjudicated, adj_problems = load_adjudications(run)
    needs.extend(adj_problems)
    awaiting, escalated = [], []
    flags, seen = [], set()
    for rule, path, lineno, text in found:
        fid = _scan_rules.flag_id(rule, path, text)
        if fid in seen:
            continue
        seen.add(fid)
        disp = carried.get(fid)
        if disp is not None and not _valid_disposition(disp, rule):
            needs.append("invalid disposition ignored: %s (%s in %s)" % (fid, text, path))
            disp = None
        flag = {"id": fid, "rule": rule, "path": path, "line": lineno, "text": text, "disposition": disp}
        if rule in _scan_rules.CLIENT_NAME_RULES:
            prov = _scan_rules.trace(text, index, client, foreign_names)
            flag["provenance"] = prov
            entry = adjudicated.get(fid)
            if disp is None and entry is not None:
                if entry.get("basis") == _scan_rules.ESCALATED:
                    escalated.append(flag)
                    flag["escalation"] = {"reason": entry.get("reason"), "date": entry.get("date")}
                else:
                    problem = _scan_rules.adjudication_problem(rule, entry.get("basis"), prov, entry.get("source"))
                    if problem is None and not (isinstance(entry.get("reason"), str) and entry["reason"].strip()):
                        problem = "no reason given"
                    if problem is None:
                        disp = _adjudication_disposition(entry)
                    else:
                        needs.append("adjudication not accepted: %s (%s in %s): %s" % (fid, text, path, problem))
            if disp is None and flag not in escalated:
                why = _scan_rules.blocked(rule, prov)
                if why:
                    flag["person_reason"] = why
                else:
                    awaiting.append(flag)
            flag["disposition"] = disp
        flags.append(flag)
        if disp and disp.get("value") == "removed":
            failed.append("flag marked removed is still present: %s (%s in %s)" % (fid, text, path))
    undisposed = [f for f in flags if not f["disposition"]]
    for_person = [f for f in undisposed if f not in awaiting]
    needs_agent = []
    if awaiting:
        needs_agent.append("%d candidate(s) await the agent's judgement (readiness.py adjudicate or escalate): %s"
                           % (len(awaiting), ", ".join("%s %r" % (f["id"], f["text"]) for f in awaiting)))
    for f in for_person:
        if f.get("person_reason"):
            needs.append("for a person: %s %r: %s" % (f["id"], f["text"], f["person_reason"]))
        elif f in escalated:
            needs.append("for a person: %s %r: escalated by the agent: %s" % (f["id"], f["text"],
                                                                              f["escalation"]["reason"]))
    if for_person:
        needs.append("%d flag(s) not disposed" % len(for_person))

    if applies is True:
        image_flags = [f for f in undisposed if f["path"].startswith("image:")]
        image_scan = "fail" if (image_failed or image_flags) else "pass"
    elif applies is False:
        image_scan = "not-applicable"
    else:
        image_scan = NMI

    if failed:
        code, verdict = _records.FAILED, "fail"
    elif needs_agent:
        code, verdict = _records.FAILED, "needs_adjudication"
    elif needs:
        code, verdict = _records.NEEDS_PERSON, "needs_person"
    else:
        code, verdict = _records.OK, "pass"

    inputs = _records.standard_input_hashes(run)
    inputs["package.sha256"] = _records.sha256_file(os.path.join(run, "package.sha256"))
    if handoff is not None:
        handoff_rel = design_paths(run)[0]
        inputs[handoff_rel] = _records.sha256_file(os.path.join(run, handoff_rel))
    record = _records.record_header(_records.SKILL_VERSION, inputs, {"package_sha256": inp["recorded"]})
    record.update({
        "verdict": verdict,
        "reasons": failed + needs_agent + needs,
        "client_name": client,
        "flags": flags,
        "shareable": shareable,
        "internal": internal,
        "readings": readings,
        "image_scan": image_scan,
    })
    _records.write_counted(run, RECORD, record)
    log.append(run, "clearance.recorded", {"verdict": verdict, "package_sha256": inp["recorded"],
                                           "flags": len(flags), "undisposed": len(undisposed)})
    for reason in failed + needs_agent + needs:
        print(reason)
    print("clearance: %s (%d flag(s), %d undisposed)" % (verdict, len(flags), len(undisposed)))
    return code


def _new_adjudication(flag, basis, reason, source=None):
    return {"flag": flag["id"], "text": flag["text"], "rule": flag["rule"], "path": flag["path"],
            "basis": basis, "reason": " ".join(reason.split()), "source": source, "by": "agent",
            "date": _records.now_iso()}


def _save_adjudications(run, new_entries):
    """Add or replace entries (by flag id) in the adjudications record."""
    existing, _ = load_adjudications(run)
    for entry in new_entries:
        existing[entry["flag"]] = entry
    folder = os.path.join(run, "records")
    if not os.path.isdir(folder):
        os.makedirs(folder)
    _records.write_record(os.path.join(run, ADJUDICATIONS),
                          {"adjudications": [existing[k] for k in sorted(existing)]})


def _flag_for_agent(run, flag, reason):
    if not reason.strip():
        raise Usage("--reason must say why, in one line")
    load_inputs(run)
    doc, err = _records.load_json(os.path.join(run, RECORD))
    if err or not isinstance(doc, dict):
        raise Usage("no clearance record to judge against; run scan first")
    match = [f for f in doc.get("flags") or [] if isinstance(f, dict) and f.get("id") == flag]
    if not match:
        raise Usage("unknown flag id: %s" % flag)
    if not isinstance(match[0].get("provenance"), dict):
        raise Usage("flag %s is not a client-name candidate (rule %s): a person disposes it"
                    % (flag, match[0].get("rule")))
    return match[0]


def adjudicate(run, flag, basis, reason, source=None):
    if basis not in _scan_rules.AGENT_BASES:
        raise Usage("--basis must be one of %s" % ", ".join(_scan_rules.AGENT_BASES))
    fl = _flag_for_agent(run, flag, reason)
    problem = _scan_rules.adjudication_problem(fl.get("rule"), basis, fl["provenance"], source)
    if problem:
        raise Usage("cannot adjudicate %s: %s; a person disposes it" % (flag, problem))
    _save_adjudications(run, [_new_adjudication(fl, basis, reason, source)])
    return scan(run)


def escalate(run, flag, reason):
    fl = _flag_for_agent(run, flag, reason)
    _save_adjudications(run, [_new_adjudication(fl, _scan_rules.ESCALATED, reason)])
    return scan(run)


def dispose(run, flag, disposition, by):
    if disposition not in DISPOSITIONS:
        raise Usage("--disposition must be one of %s" % ", ".join(DISPOSITIONS))
    if not by.strip():
        raise Usage("--by must name the person")
    inp = load_inputs(run)
    doc, err = _records.load_json(os.path.join(run, RECORD))
    if err or not isinstance(doc, dict):
        raise Usage("no clearance record to dispose against; run scan first")
    pkg = (doc.get("code_identity") or {}).get("package_sha256")
    if pkg != inp["current"] or pkg != inp["recorded"]:
        raise Usage("clearance record is for another package; run scan again")
    match = [f for f in doc.get("flags") or [] if isinstance(f, dict) and f.get("id") == flag]
    if not match:
        raise Usage("unknown flag id: %s" % flag)
    if disposition == "not-a-client" and match[0].get("rule") not in _scan_rules.CLIENT_NAME_RULES:
        raise Usage("not-a-client applies only to client-name rules, not %s" % match[0].get("rule"))
    return scan(run, {flag: {"value": disposition, "by": by.strip(), "date": _records.now_iso()}})


def main(argv=None):
    parser = argparse.ArgumentParser(prog="readiness.py", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd")
    p_scan = sub.add_parser("scan")
    p_scan.add_argument("--run", required=True)
    p_adj = sub.add_parser("adjudicate", help="the agent clears a client-name candidate with a reason")
    p_adj.add_argument("--run", required=True)
    p_adj.add_argument("--flag", required=True)
    p_adj.add_argument("--basis", required=True)
    p_adj.add_argument("--reason", required=True)
    p_adj.add_argument("--source")
    p_esc = sub.add_parser("escalate", help="the agent hands a client-name candidate to a person")
    p_esc.add_argument("--run", required=True)
    p_esc.add_argument("--flag", required=True)
    p_esc.add_argument("--reason", required=True)
    p_disp = sub.add_parser("dispose")
    p_disp.add_argument("--run", required=True)
    p_disp.add_argument("--flag", required=True)
    p_disp.add_argument("--disposition", required=True)
    p_disp.add_argument("--by", required=True)
    args = parser.parse_args(argv)
    if not args.cmd:
        parser.print_usage(sys.stderr)
        return _records.USAGE
    try:
        if args.cmd == "scan":
            return scan(args.run)
        if args.cmd == "adjudicate":
            return adjudicate(args.run, args.flag, args.basis, args.reason, args.source)
        if args.cmd == "escalate":
            return escalate(args.run, args.flag, args.reason)
        return dispose(args.run, args.flag, args.disposition, args.by)
    except Usage as e:
        sys.stderr.write("readiness.py: %s\n" % e)
        return _records.USAGE


if __name__ == "__main__":
    sys.exit(main())
