#!/usr/bin/env python3
"""Freeze cases, expected results and judge prompts into R/frozen/v<N>/, and verify them.

freeze   --run R --cases DIR --expected DIR [--judge-prompts DIR] --author NAME --author-role ROLE
verify   --run R
refreeze --run R --case ID --expected FILE --author NAME --author-role ROLE

A missing, conflicting, invalid or incomplete expectation is frozen as given with state
"pending"; so is one whose amounts disagree with the Agent Design derivation. Nothing is guessed.

Authorship: expected results written in a separate context (the Agent Design suite, a fresh
session or a person) are preferred and recorded as `authored_by: separate-context`. When no
fresh or separate session is available, builder-authored expected results (role `builder` or
`brief-author`) are accepted and recorded as `authored_by: builder` with a note, so the
end-of-run review sees them. freeze verify is unchanged.
"""
import argparse
import decimal
import json
import os
import stat
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _records  # noqa: E402
import _locate  # noqa: E402
import _expected_method  # noqa: E402
import log  # noqa: E402

ROLES = ("agent-design-suite", "fresh-session", "person-ratified", "builder", "brief-author")
BUILDER_ROLES = ("builder", "brief-author")
BUILDER_NOTE = ("written by the builder because no fresh or separate session was available; "
                "accepted and recorded for the end-of-run review")
MANIFEST = os.path.join("frozen", "MANIFEST.json")
MANIFEST_REL = "frozen/MANIFEST.json"
SUITE = os.path.join("inputs", "agent-design", "stages", "test-suite", "test-suite.json")
HANDOFF = os.path.join("inputs", "agent-design", "outputs", "agent-build-inputs.json")
NMI = "needs_more_information"
VERIFY_RECORD = "records/freeze-verify.json"


class InputError(Exception):
    """Malformed input the script cannot classify (exit 2)."""


# ---------- reading inputs ----------

def _json_files(folder):
    return sorted(n for n in os.listdir(folder) if n.endswith(".json") and os.path.isfile(os.path.join(folder, n)))


def _all_files(folder):
    out = []
    for root, dirs, files in os.walk(folder):
        dirs.sort()
        for name in sorted(files):
            full = os.path.join(root, name)
            if os.path.isfile(full):
                out.append(os.path.relpath(full, folder).replace(os.sep, "/"))
    return out


def _read_bytes(path):
    with open(path, "rb") as f:
        return f.read()


def _parse(data):
    """(obj, None) or (None, reason) for bytes that are not valid JSON.

    Fractional numbers parse straight to Decimal so a long decimal is never rounded through float.
    """
    try:
        return json.loads(data.decode("utf-8"), parse_float=decimal.Decimal), None
    except (ValueError, UnicodeDecodeError) as err:
        return None, "not valid JSON (%s)" % err


def _label_set_ids(run):
    brief, err = _records.load_json(os.path.join(run, "brief.json"))
    if err or not isinstance(brief, dict):
        return []
    ids = []
    for ls in brief.get("label_sets") or []:
        if isinstance(ls, dict) and isinstance(ls.get("id"), str):
            ids.append(ls["id"])
    return ids


def _design_paths(run):
    """(suite path, suite run-relative name, hand-off path) as the locator returns them; a path it found
    none for, or refused (outside the run), is None and reads as absent."""
    loc = _locate.locate_design(run)
    name = loc.rel(loc.suite) if loc.suite else SUITE.replace(os.sep, "/")
    return loc.suite, name, loc.handoff


def _suite_cases(run):
    """{case id: suite case} from the Agent Design suite; {} with no design."""
    path, name, _ = _design_paths(run)
    if path is None or not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            suite = _locate.normalise_suite(json.load(f, parse_float=decimal.Decimal), [])  # keys/ids only
    except (OSError, ValueError, UnicodeDecodeError) as err:
        raise InputError("cannot read %s: %s" % (name, err))
    cases = suite.get("cases") if isinstance(suite, dict) else None
    if not isinstance(cases, list):
        raise InputError("%s has no cases list" % name)
    return {c.get("id"): c for c in cases if isinstance(c, dict)}


def _design_identity(run):
    path = _design_paths(run)[2]
    handoff, err = _locate.load_handoff(path, []) if path else (None, "missing")
    if err or not isinstance(handoff, dict) or not isinstance(handoff.get("stage_input_hashes"), dict):
        return NMI
    return handoff["stage_input_hashes"]


# ---------- assessing expectations ----------

def _incomplete(expected, label_ids):
    """Reason naming the gap, or None when every candidate covers every label set."""
    if not label_ids:
        return None
    candidates = expected.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        return "incomplete expectation: no candidates"
    for i, cand in enumerate(candidates, 1):
        if not isinstance(cand, dict):
            return "incomplete expectation: candidate %d is not an object" % i
        if cand.get("no_verdict") is True:
            reason = cand.get("no_verdict_reason")
            if not isinstance(reason, str) or not reason.strip():
                return "incomplete expectation: candidate %d has no_verdict true with an empty no_verdict_reason" % i
            continue
        outcome = cand.get("outcome") if isinstance(cand.get("outcome"), dict) else {}
        for ls in label_ids:
            if ls not in outcome:
                return "incomplete expectation: candidate %d has no outcome for label set %s" % (i, ls)
    return None


def _design_disagreement(expected, suite_case):
    """needs_more_information reason, or None when the derivation agrees with every candidate."""
    entry = _expected_method.compute_case(suite_case)
    if "excluded" in entry:
        if "exclusion" in suite_case:
            return "%s: the suite case is an exclusion (%s)" % (NMI, entry["excluded"])
        return "%s: derivation unusable: %s" % (NMI, entry["excluded"])
    candidates = expected.get("candidates") if isinstance(expected.get("candidates"), list) else []
    decision = suite_case.get("decision")
    blocks = [suite_case.get("expected")] + (suite_case.get("allowed_alternatives") or [])
    for idx, block in enumerate(blocks):
        if not isinstance(block, dict) or not isinstance(block.get("values"), dict):
            return "%s: expected values are missing or unreadable" % NMI
        cand = candidates[idx] if idx < len(candidates) else {}
        outcome = cand.get("outcome") or {}
        # Decision-keyed expectations preserve exact output identities and JSON types.
        if decision in outcome:
            if outcome[decision] != block.get("label"):
                return "%s: decision %s label conflicts with suite" % (NMI, decision)
            for name, value in block["values"].items():
                if name in ("outcome", decision) or isinstance(value, (dict, list)):
                    continue
                if value is None or isinstance(value, (str, bool)):
                    if name not in outcome or type(outcome[name]) is not type(value) or outcome[name] != value:
                        return "%s: exact output %s is missing or conflicts with suite" % (NMI, name)
    wanted = [("primary candidate", entry["amounts"])]
    for i, alt in enumerate(entry["alternatives"], 1):
        wanted.append(("alternative candidate %d" % i, alt["amounts"]))
    for idx, (which, amounts) in enumerate(wanted):
        cand = candidates[idx] if idx < len(candidates) and isinstance(candidates[idx], dict) else None
        values = cand.get("values") if cand and isinstance(cand.get("values"), dict) else {}
        for name in sorted(amounts):
            derived = decimal.Decimal(amounts[name])
            if name not in values:
                return "%s: the %s lacks value %s, which the derivation computes as %s" % (NMI, which, name, amounts[name])
            given = _expected_method._decimal(values[name])
            if given is None:
                return "%s: the %s value %s is not a number (%r); the derivation gives %s" % (
                    NMI, which, name, values[name], amounts[name])
            if given != derived:
                return "%s: the %s value %s is %s but the derivation gives %s" % (
                    NMI, which, name, _expected_method._text(given), amounts[name])
    return None


def assess(case_ids, expected_files, label_ids, suite):
    """State per case id: {case_id: (state, reason)} for its expected file (or its absence).

    case_ids: case ids that have a case file. expected_files: {file name: bytes}.
    Returns (by_case, by_file): by_file maps each expected file name to (case_id, state, reason).
    """
    parsed, claims = {}, {}
    for name, data in expected_files.items():
        stem = name[:-len(".json")]
        obj, err = _parse(data)
        parsed[name] = (stem, obj, err)
        claimed = obj.get("case_id") if isinstance(obj, dict) else None
        if isinstance(claimed, str):
            claims.setdefault(claimed, []).append(name)
    by_file = {}
    for name, (stem, obj, err) in parsed.items():
        if err:
            by_file[name] = (stem, "pending", "invalid expectation: %s %s" % (name, err))
            continue
        if not isinstance(obj, dict):
            by_file[name] = (stem, "pending", "invalid expectation: %s is not a JSON object" % name)
            continue
        claimed = obj.get("case_id")
        if claimed != stem:
            by_file[name] = (stem, "pending", "conflicting expectation: %s claims case_id %r" % (name, claimed))
            continue
        others = [n for n in claims.get(stem, []) if n != name]
        if others:
            by_file[name] = (stem, "pending", "conflicting expectation: %s also claim case_id %s" % (", ".join(sorted(others)), stem))
            continue
        own = obj.get("pending_reason")
        reason = own.strip() if isinstance(own, str) and own.strip() else None
        if reason is None:  # the expected file's own pending_reason wins, label sets or none
            decision = suite.get(stem, {}).get("decision")
            candidates = obj.get("candidates") or []
            legacy_label = not label_ids and candidates and all(
                isinstance(c, dict) and isinstance(c.get("outcome"), dict) and "label" in c["outcome"]
                for c in candidates)
            scoped = isinstance(decision, str) and (decision in label_ids or (not label_ids and not legacy_label))
            applicable = [decision] if scoped else label_ids
            reason = _incomplete(obj, applicable)
        if reason is None and stem in suite:
            reason = _design_disagreement(obj, suite[stem])
        if reason is None and stem not in case_ids:
            reason = "no case file for case_id %s" % stem
        by_file[name] = (stem, "pending" if reason else "ready", reason)
    by_case = {}
    for cid in case_ids:
        name = "%s.json" % cid
        if name not in by_file:
            by_case[cid] = ("pending", "missing expectation: expected/%s not supplied" % name)
        else:
            _, state, reason = by_file[name]
            by_case[cid] = (state, reason)
    return by_case, by_file


# ---------- manifest ----------

def _manifest_path(run):
    return os.path.join(run, MANIFEST)


def _seal(version_entry):
    body = {k: v for k, v in version_entry.items() if k != "manifest_sha256"}
    return _records.sha256_json(body)


def current_manifest(run):
    """(manifest, current version number), or (None, None) when absent or unreadable."""
    manifest, err = _records.load_json(_manifest_path(run))
    if err or not isinstance(manifest, dict):
        return None, None
    return manifest, manifest.get("current_version")


def _entry(path, kind, case_id, author, role, now, state, reason, process=None):
    e = {"path": path, "sha256": None, "kind": kind, "case_id": case_id, "author": author,
         "author_role": role, "frozen_at": now, "state": state, "pending_reason": reason}
    if process is not None:
        e["process"] = process
    if kind == "expected":
        if role in BUILDER_ROLES:
            e["authored_by"], e["authorship_note"] = "builder", BUILDER_NOTE
        else:
            e["authored_by"] = "separate-context"
    return e


def _builder_notice(role):
    if role in BUILDER_ROLES:
        print("note: expected results recorded as builder-authored (role %s); a separate-context "
              "author is preferred when one is available" % role)


def _write_frozen(run, rel, data):
    path = os.path.join(run, "frozen", rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)
    os.chmod(path, 0o444)
    return _records.sha256_file(path)


def _provenance(run, version):
    """Retain exact source and method bytes separately from evaluation files."""
    suite, suite_name, handoff = _design_paths(run)
    sources = [("suite.json", suite, suite_name),
               ("handoff.json", handoff, _locate.locate_design(run).rel(handoff) if handoff else None),
               ("expected-method.py", _expected_method.__file__, "_expected_method.py")]
    evidence = []
    for name, source, origin in sources:
        if source and os.path.isfile(source):
            rel = "v%d/provenance/%s" % (version, name)
            evidence.append({"path": rel, "source": origin,
                             "sha256": _write_frozen(run, rel, _read_bytes(source))})
    return evidence


def _seal_and_log(run, manifest, version_entry):
    version_entry["provenance"] = _provenance(run, version_entry["version"])
    version_entry["decisions"] = {cid: c.get("decision") for cid, c in _suite_cases(run).items()}
    version_entry["manifest_sha256"] = _seal(version_entry)
    manifest["versions"].append(version_entry)
    manifest["current_version"] = version_entry["version"]
    _records.write_record(_manifest_path(run), manifest)
    log.append(run, "freeze.sealed", {"version": version_entry["version"],
                                      "manifest_sha256": version_entry["manifest_sha256"]})


def _refuse(reasons):
    for r in reasons:
        print("refused: %s" % r, file=sys.stderr)
    return _records.FAILED


def _usage(msg):
    print("error: %s" % msg, file=sys.stderr)
    return _records.USAGE


# ---------- commands ----------

def cmd_freeze(args):
    run = args.run
    if not os.path.isdir(run):
        return _usage("--run %s is not a folder" % run)
    if os.path.exists(_manifest_path(run)):
        return _usage("%s already exists; use refreeze" % MANIFEST_REL)
    for flag, folder in (("--cases", args.cases), ("--expected", args.expected), ("--judge-prompts", args.judge_prompts)):
        if folder is not None and not os.path.isdir(folder):
            return _usage("%s %s is not a folder" % (flag, folder))
    reasons = []
    case_names = _json_files(args.cases)
    if not case_names:
        reasons.append("the cases folder has no case files: 0 of 0 never passes")
    if reasons:
        return _refuse(reasons)
    try:
        suite = _suite_cases(run)
    except InputError as err:
        return _usage(str(err))
    cases = {}
    for name in case_names:
        data = _read_bytes(os.path.join(args.cases, name))
        obj, err = _parse(data)
        cases[name] = (data, obj if isinstance(obj, dict) else None, err)
    case_ids, seen = [], {}
    for name, (data, obj, err) in cases.items():
        cid = obj.get("case_id") if obj else None
        if isinstance(cid, str):
            seen.setdefault(cid, []).append(name)
    for cid in seen:
        case_ids.append(cid)
    expected_files = {n: _read_bytes(os.path.join(args.expected, n)) for n in _json_files(args.expected)}
    by_case, by_file = assess(case_ids, expected_files, _label_set_ids(run), suite)

    now = _records.now_iso()
    files = []
    for name, (data, obj, err) in sorted(cases.items()):
        cid = obj.get("case_id") if obj else None
        if not isinstance(cid, str):
            state, reason = "pending", "case file %s has no case_id (%s)" % (name, err or "field missing")
            cid = None
        elif len(seen[cid]) > 1:
            state, reason = "pending", "conflicting case files claim case_id %s: %s" % (cid, ", ".join(seen[cid]))
        else:
            state, reason = by_case[cid]
        e = _entry("v1/cases/%s" % name, "case", cid, args.author, args.author_role, now, state, reason,
                   obj.get("process") if obj else None)
        e["sha256"] = _write_frozen(run, e["path"], data)
        files.append(e)
    for name, data in sorted(expected_files.items()):
        cid, state, reason = by_file[name]
        e = _entry("v1/expected/%s" % name, "expected", cid, args.author, args.author_role, now, state, reason)
        e["sha256"] = _write_frozen(run, e["path"], data)
        files.append(e)
    if args.judge_prompts:
        for rel in _all_files(args.judge_prompts):
            e = _entry("v1/judge-prompts/%s" % rel, "judge-prompt", None, args.author, args.author_role, now,
                       "ready", None)
            e["sha256"] = _write_frozen(run, e["path"], _read_bytes(os.path.join(args.judge_prompts, rel)))
            files.append(e)
    version_entry = {"version": 1, "sealed_at": now, "design_identity": _design_identity(run), "files": files}
    _seal_and_log(run, {"current_version": 1, "versions": []}, version_entry)
    pending = [f["path"] for f in files if f["state"] == "pending"]
    print("frozen v1: %d files, %d pending, manifest_sha256 %s" % (len(files), len(pending), version_entry["manifest_sha256"]))
    for f in files:
        if f["state"] == "pending":
            print("pending: %s: %s" % (f["path"], f["pending_reason"]))
    _builder_notice(args.author_role)
    return _records.OK


def cmd_refreeze(args):
    run = args.run
    if not os.path.isdir(run):
        return _usage("--run %s is not a folder" % run)
    manifest, version = current_manifest(run)
    if manifest is None or not isinstance(version, int):
        return _usage("no readable %s; freeze first" % MANIFEST_REL)
    current = [v for v in manifest.get("versions") or [] if v.get("version") == version]
    if len(current) != 1:
        return _usage("%s has no entry for current_version %r" % (MANIFEST_REL, version))
    current = current[0]
    case_entries = [f for f in current["files"] if f["kind"] == "case"]
    if args.case not in [f.get("case_id") for f in case_entries]:
        return _usage("unknown case id %s in version %d" % (args.case, version))
    if not os.path.isfile(args.expected):
        return _usage("--expected %s is not a file" % args.expected)
    try:
        suite = _suite_cases(run)
    except InputError as err:
        return _usage(str(err))
    ok, problems = verify(run)
    if not ok:
        return _refuse(["prior freeze evidence failed verification"] + problems)
    new_v = version + 1
    prefix_old, prefix_new = "v%d/" % version, "v%d/" % new_v
    target = "%sexpected/%s.json" % (prefix_old, args.case)
    new_data = _read_bytes(args.expected)
    # The named case's state is recomputed from the files the new version will hold.
    expected_files = {}
    for f in current["files"]:
        if f["kind"] == "expected" and f["path"] != target:
            expected_files[os.path.basename(f["path"])] = _read_bytes(os.path.join(run, "frozen", f["path"]))
    expected_files["%s.json" % args.case] = new_data
    case_ids = sorted({f["case_id"] for f in case_entries if isinstance(f.get("case_id"), str)})
    by_case, by_file = assess(case_ids, expected_files, _label_set_ids(run), suite)

    # A changed suite must not certify the previous runtime input as current.
    historical_decisions = {}
    for prior in manifest["versions"]:
        historical_decisions.update(prior.get("decisions", {}))
    original_suite = {}
    for evidence in manifest["versions"][0].get("provenance", []):
        if evidence["path"].endswith("/suite.json"):
            doc, err = _parse(_read_bytes(os.path.join(run, "frozen", evidence["path"])))
            doc = _locate.normalise_suite(doc, [])
            original_suite = {c.get("id"): c for c in doc.get("cases", []) if isinstance(c, dict)}
    stale = {}
    for f in case_entries:
        cid = f.get("case_id")
        if cid in historical_decisions and cid not in suite:
            stale[cid] = "needs_more_information: prior suite case is absent or renamed"
        elif cid in suite:
            if cid in original_suite and original_suite[cid].get("rows") != suite[cid].get("rows"):
                stale[cid] = "needs_more_information: suite source rows changed; frozen case input retained"
            elif cid in historical_decisions and historical_decisions[cid] != suite[cid].get("decision"):
                stale[cid] = "needs_more_information: suite decision changed; frozen case input retained"
    now = _records.now_iso()
    files = []
    for f in current["files"]:
        if f["path"] == target:
            continue
        e = dict(f, path=prefix_new + f["path"][len(prefix_old):])
        if e["kind"] == "case" and e.get("case_id") in by_case:
            same = [c for c in case_entries if c.get("case_id") == e.get("case_id")]
            if len(same) > 1:
                e["state"], e["pending_reason"] = "pending", "conflicting case files claim case_id %s" % args.case
            else:
                e["state"], e["pending_reason"] = by_case[e["case_id"]]
        if e["kind"] == "expected":
            _, e["state"], e["pending_reason"] = by_file[os.path.basename(e["path"])]
        if e.get("case_id") in stale:
            e["state"], e["pending_reason"] = "pending", stale[e["case_id"]]
        _write_frozen(run, e["path"], _read_bytes(os.path.join(run, "frozen", f["path"])))
        files.append(e)
    cid, state, reason = by_file["%s.json" % args.case]
    if args.case in stale:
        state, reason = "pending", stale[args.case]
    e = _entry("%sexpected/%s.json" % (prefix_new, args.case), "expected", cid, args.author, args.author_role, now,
               state, reason)
    e["sha256"] = _write_frozen(run, e["path"], new_data)
    files.append(e)
    files.sort(key=lambda x: (x["path"].split("/")[1], x["path"]))
    version_entry = {"version": new_v, "sealed_at": now, "design_identity": _design_identity(run), "files": files}
    _seal_and_log(run, manifest, version_entry)
    log.append(run, "refreeze.sealed", {"version": new_v, "case_id": args.case, "previous_version": version})
    print("frozen v%d (case %s: %s%s), manifest_sha256 %s" % (
        new_v, args.case, state, ": " + reason if reason else "", version_entry["manifest_sha256"]))
    _builder_notice(args.author_role)
    return _records.OK


def _sealed_by_version(run):
    sealed = {}
    try:
        events = log.read(run)
    except ValueError:
        return None
    for ev in events:
        if ev.get("type") == "freeze.sealed" and isinstance(ev.get("payload"), dict):
            sealed[ev["payload"].get("version")] = ev["payload"].get("manifest_sha256")
    return sealed


def _check(run):
    """(manifest version, [(kind, path)], [reasons])."""
    problems = []
    if not os.path.exists(_manifest_path(run)):
        return None, problems, ["no %s: nothing is frozen" % MANIFEST_REL]
    manifest, err = _records.load_json(_manifest_path(run))
    if err or not isinstance(manifest, dict) or not isinstance(manifest.get("versions"), list):
        return None, [("manifest-edited", MANIFEST_REL)], ["%s unreadable: %s" % (MANIFEST_REL, err or "wrong shape")]
    reasons = []
    sealed = _sealed_by_version(run)
    if sealed is None:
        sealed = {}
        reasons.append("run log unreadable")
    current = manifest.get("current_version")
    highest = max([v for v in sealed if isinstance(v, int)], default=None)
    if current != highest:
        problems.append(("manifest-edited", MANIFEST_REL))
        reasons.append("current_version %r is not the highest sealed version %r" % (current, highest))
    listed = [v.get("version") for v in manifest["versions"] if isinstance(v, dict)]
    if highest is None:
        problems.append(("manifest-edited", MANIFEST_REL))
        reasons.append("the run log has no freeze.sealed event")
    for n in sorted(v for v in sealed if isinstance(v, int)):
        if listed.count(n) != 1:
            problems.append(("manifest-edited", "%s#v%s" % (MANIFEST_REL, n)))
            reasons.append("sealed version %s has %d manifest entries, not exactly 1" % (n, listed.count(n)))
    for v in manifest["versions"]:
        if not isinstance(v, dict) or not isinstance(v.get("files"), list):
            problems.append(("manifest-edited", MANIFEST_REL))
            reasons.append("a version entry has the wrong shape")
            continue
        n = v.get("version")
        where = "%s#v%s" % (MANIFEST_REL, n)
        if v.get("manifest_sha256") != _seal(v):
            problems.append(("manifest-edited", where))
            reasons.append("version %s manifest_sha256 does not match its recomputation" % n)
        elif sealed.get(n) != v.get("manifest_sha256"):
            problems.append(("manifest-edited", where))
            reasons.append("version %s manifest_sha256 differs from the last freeze.sealed event" % n)
        for f in v["files"] + v.get("provenance", []):
            rel = f.get("path") if isinstance(f, dict) else None
            if not isinstance(rel, str) or rel.startswith("/") or ".." in rel.split("/"):
                problems.append(("manifest-edited", where))
                reasons.append("version %s lists a bad path %r" % (n, rel))
                continue
            shown = "frozen/" + rel
            path = os.path.join(run, "frozen", rel)
            if os.path.islink(path):
                problems.append(("replaced", shown))
            elif not os.path.exists(path):
                problems.append(("deleted", shown))
            elif not os.path.isfile(path):
                problems.append(("replaced", shown))
            elif _records.sha256_file(path) != f.get("sha256"):
                problems.append(("modified", shown))
            elif os.stat(path).st_mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH):
                problems.append(("permissions", shown))
    for kind, path in problems:
        if kind not in ("manifest-edited",):
            reasons.append("%s: %s" % (kind, path))
    return current if isinstance(current, int) else None, problems, reasons


def verify(run):
    """(ok, ["<kind>: <path>", ...]); ok is False with no manifest."""
    version, problems, reasons = _check(run)
    ok = version is not None and not problems and not reasons
    return ok, ["%s: %s" % p for p in problems]


def cmd_verify(args):
    run = args.run
    if not os.path.isdir(run):
        return _usage("--run %s is not a folder" % run)
    version, problems, reasons = _check(run)
    ok = version is not None and not problems and not reasons
    logp = log.log_path(run)
    record = _records.record_header(_records.SKILL_VERSION, _records.standard_input_hashes(run), {})
    record.update({
        "verdict": "pass" if ok else "fail",
        "reasons": reasons,
        "manifest_version": version,
        "log_hash": _records.sha256_file(logp) if os.path.isfile(logp) else None,
        "problems": [{"path": p, "kind": k} for k, p in problems],
    })
    _records.write_counted(run, VERIFY_RECORD, record)
    for r in reasons:
        print(r)
    print("verify: %s" % record["verdict"])
    return _records.OK if ok else _records.FAILED


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("freeze")
    f.add_argument("--run", required=True)
    f.add_argument("--cases", required=True)
    f.add_argument("--expected", required=True)
    f.add_argument("--judge-prompts")
    f.add_argument("--author", required=True)
    f.add_argument("--author-role", required=True, choices=ROLES)
    v = sub.add_parser("verify")
    v.add_argument("--run", required=True)
    r = sub.add_parser("refreeze")
    r.add_argument("--run", required=True)
    r.add_argument("--case", required=True)
    r.add_argument("--expected", required=True)
    r.add_argument("--author", required=True)
    r.add_argument("--author-role", required=True, choices=ROLES)
    args = ap.parse_args(argv)
    if args.cmd == "freeze":
        return cmd_freeze(args)
    if args.cmd == "refreeze":
        return cmd_refreeze(args)
    return cmd_verify(args)


if __name__ == "__main__":
    sys.exit(main())
