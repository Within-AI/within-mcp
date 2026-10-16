#!/usr/bin/env python3
"""Freeze hook (Pins §3): the snapshot's pinned cutoff and process versions, and drift against a set map.

pin(snapshot_dir) builds {frozen_at, cutoff, process_versions} from the snapshot folder:
- cutoff is snapshot.json scope.cutoff_at, ISO-8601 with a timezone, read leniently: a top-level cutoff_at, a one-element
  list or identical duplicates are one cutoff; differing cutoffs take the earliest; no cutoff freezes at the moment of
  pinning. Each such reading is recorded in the block's `readings` list ({what, reading, why, still_need when the person
  is asked}); the block carries `readings` only when there is one;
- versions come from process-versions.json processes[] when present, else from each
  get_process_details-<id>-all-scopes.json current_version; when both are present they must agree;
- every process in scope.process_ids needs exactly one pin, with an ISO-8601 version date (created_at).
Anything else raises FreezeError naming the cause. drift(state, set_map) lists the set map's process_versions[]
entries whose version_no is higher than the pin. Stdlib only.
"""
import glob
import json
import os
from datetime import datetime, timezone

VERSIONS = "process-versions.json"
DETAILS_GLOB = "get_process_details-*-all-scopes.json"


class FreezeError(Exception):
    """The snapshot folder cannot be pinned, or a set map cannot be compared with the pins."""


def _pairs(pairs):
    """object_pairs_hook keeping repeated keys visible: a repeated key becomes a _Dup list of its values."""
    out = {}
    for k, v in pairs:
        if k in out:
            prev = out[k]
            out[k] = _Dup(prev + [v]) if isinstance(prev, _Dup) else _Dup([prev, v])
        else:
            out[k] = v
    return out


class _Dup(list):
    """Values of a key that appears more than once in one JSON object."""


def _load(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f, object_pairs_hook=_pairs)
    except (OSError, ValueError) as e:
        raise FreezeError("cannot read %s: %s" % (os.path.basename(path), e))


def _iso(value, what):
    if not isinstance(value, str) or not value:
        raise FreezeError("%s is not an ISO-8601 date-time: %r" % (what, value))
    try:
        datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
    except ValueError:
        raise FreezeError("%s is not an ISO-8601 date-time: %r" % (what, value))
    return value


def _when(value):
    return datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)


def _cutoff(snap, readings, frozen_at):
    """The one cutoff, read leniently: scope.cutoff_at, else a top-level cutoff_at; a one-element list is its value; identical
    duplicates are one cutoff; differing cutoffs take the earliest (no evidence beyond any declared freeze); no cutoff at all
    freezes at FROZEN_AT, the moment of pinning (as make_intake.py does by default). Each such reading joins READINGS as
    {what, reading, why[, still_need]}. A cutoff value that is not an ISO-8601 date-time with a timezone still raises."""
    scope = snap.get("scope") if isinstance(snap.get("scope"), dict) else {}
    found = []
    for holder, where in ((scope, "scope.cutoff_at"), (snap, "cutoff_at")):
        if "cutoff_at" in holder:
            v = holder["cutoff_at"]
            found += [(where, x) for x in v] if isinstance(v, list) else [(where, v)]
    if not found:
        readings.append({"what": "snapshot cutoff", "reading": "frozen at the moment of pinning, %s" % frozen_at,
                         "why": "snapshot.json names no cutoff (scope.cutoff_at)", "still_need": True})
        return frozen_at
    for where, value in found:
        _iso(value, "snapshot.json %s" % where)
        if _when(value).tzinfo is None:
            raise FreezeError("snapshot.json %s has no timezone: %r" % (where, value))
    distinct = sorted(set(v for _, v in found), key=_when)
    value = distinct[0]
    places = ", ".join("%s=%r" % f for f in found)
    if len(distinct) > 1:
        readings.append({"what": "snapshot cutoff", "reading": "the earliest cutoff, %s" % value,
                         "why": "snapshot.json names %d different cutoffs (%s); the earliest keeps the design to evidence "
                                "inside every declared freeze" % (len(distinct), places), "still_need": True})
    elif len(found) > 1:
        readings.append({"what": "snapshot cutoff", "reading": value,
                         "why": "snapshot.json repeats the same cutoff %d times (%s)" % (len(found), places)})
    elif found[0][0] != "scope.cutoff_at":
        readings.append({"what": "snapshot cutoff", "reading": value,
                         "why": "snapshot.json names its cutoff at %s instead of scope.cutoff_at" % found[0][0]})
    elif isinstance(scope.get("cutoff_at"), list):
        readings.append({"what": "snapshot cutoff", "reading": value,
                         "why": "snapshot.json scope.cutoff_at is a list holding one cutoff"})
    return value


def _version_no(v, what):
    if isinstance(v, bool) or not isinstance(v, int):
        raise FreezeError("%s version_no is missing or not a whole number: %r" % (what, v))
    return v


def _from_versions_file(path):
    """{process_id: [pin, ...]} from process-versions.json processes[]."""
    doc = _load(path)
    rows = doc.get("processes") if isinstance(doc, dict) else None
    if not isinstance(rows, list):
        raise FreezeError("%s has no processes[] list" % VERSIONS)
    pins = {}
    for i, row in enumerate(rows):
        pid = row.get("process_resource_key") if isinstance(row, dict) else None
        if not isinstance(pid, str) or not pid:
            raise FreezeError("%s processes[%d] has no process_resource_key" % (VERSIONS, i))
        pins.setdefault(pid, []).append({"process_id": pid,
                                         "version_no": _version_no(row.get("version_no"), "%s %s" % (VERSIONS, pid)),
                                         "created_at": row.get("created_at"), "source": VERSIONS})
    return pins


def _from_details(snapshot_dir):
    """{process_id: [pin, ...]} from get_process_details-<id>-all-scopes.json current_version."""
    pins = {}
    for path in sorted(glob.glob(os.path.join(snapshot_dir, DETAILS_GLOB))):
        name = os.path.basename(path)
        doc = _load(path)
        if not isinstance(doc, dict):
            raise FreezeError("%s is not a JSON object" % name)
        pid = doc.get("process_resource_key") or name[len("get_process_details-"):-len("-all-scopes.json")]
        cv = doc.get("current_version")
        if not isinstance(cv, dict):
            raise FreezeError("%s has no current_version" % name)
        pins.setdefault(pid, []).append({"process_id": pid, "version_no": _version_no(cv.get("version_no"), name),
                                         "created_at": cv.get("created_at"), "source": name})
    return pins


def _one(pins, pid, where):
    got = pins.get(pid) or []
    if not got:
        raise FreezeError("process %s has no pin in %s" % (pid, where))
    if len(got) > 1:
        raise FreezeError("process %s has %d pins in %s: %s"
                          % (pid, len(got), where, ", ".join("version_no %s" % p["version_no"] for p in got)))
    return got[0]


def _dated(chosen, pid, also=None):
    """CHOSEN once its process version date is a valid ISO-8601 date-time; else FreezeError naming the fields read."""
    looked = chosen["source"] + (" and " + also if also else "")
    if chosen["created_at"] is None:
        raise FreezeError("process %s has no version date: no created_at in %s, so the freeze cannot pin it"
                          % (pid, looked))
    _iso(chosen["created_at"], "process %s version date (created_at in %s)" % (pid, looked))
    return chosen


def pin(snapshot_dir):
    """The freeze block {frozen_at, cutoff, process_versions} for SNAPSHOT_DIR; raises FreezeError naming the cause."""
    p = os.path.join(snapshot_dir, "snapshot.json")
    if not os.path.isfile(p):
        raise FreezeError("no snapshot.json in %s" % snapshot_dir)
    snap = _load(p)
    if not isinstance(snap, dict):
        raise FreezeError("snapshot.json is not a JSON object")
    frozen_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    readings = []
    cutoff = _cutoff(snap, readings, frozen_at)
    ids = (snap.get("scope") if isinstance(snap.get("scope"), dict) else {}).get("process_ids")
    if not isinstance(ids, list) or not ids or not all(isinstance(i, str) and i for i in ids):
        raise FreezeError("snapshot.json scope.process_ids is missing or empty")
    if len(set(ids)) != len(ids):
        raise FreezeError("snapshot.json scope.process_ids names a process twice")
    details = _from_details(snapshot_dir)
    vpath = os.path.join(snapshot_dir, VERSIONS)
    versions = _from_versions_file(vpath) if os.path.isfile(vpath) else None
    out = []
    for pid in ids:
        if versions is None:
            out.append(_dated(_one(details, pid, DETAILS_GLOB), pid))
            continue
        chosen = dict(_one(versions, pid, VERSIONS))
        if pid in details:
            other = _one(details, pid, DETAILS_GLOB)
            if other["version_no"] != chosen["version_no"]:
                raise FreezeError("process %s: %s pins version_no %s but %s pins %s"
                                  % (pid, VERSIONS, chosen["version_no"], other["source"], other["version_no"]))
            if chosen["created_at"] is not None and other["created_at"] is not None \
                    and chosen["created_at"] != other["created_at"]:
                raise FreezeError("process %s: %s says created_at %s but %s says %s"
                                  % (pid, VERSIONS, chosen["created_at"], other["source"], other["created_at"]))
            if chosen["created_at"] is None:
                chosen["created_at"] = other["created_at"]
        out.append(_dated(chosen, pid, other["source"] if pid in details else None))
    block = {"frozen_at": frozen_at, "cutoff": cutoff, "process_versions": out}
    if readings:
        block["readings"] = readings
    return block


def drift(state, set_map):
    """One entry {process_id, pinned, set_map} per set-map process_versions[] entry later than the pin; [] when none.

    No set map, or one without process_versions, is no drift. A set-map entry for a process the freeze did not pin,
    or without a whole-number version_no, cannot be compared and raises FreezeError naming it."""
    if not set_map or not set_map.get("process_versions"):
        return []
    block = (state or {}).get("freeze") or {}
    pinned = {p.get("process_id"): p.get("version_no") for p in block.get("process_versions") or []}
    out = []
    for i, entry in enumerate(set_map["process_versions"]):
        pid = entry.get("process_id") if isinstance(entry, dict) else None
        if pid not in pinned:
            raise FreezeError("set map process_versions[%d] names %s, which the freeze did not pin" % (i, pid))
        v = _version_no(entry.get("version_no"), "set map process %s" % pid)
        if v > pinned[pid]:
            out.append({"process_id": pid, "pinned": pinned[pid], "set_map": v})
    return out
