#!/usr/bin/python3
"""Observation ids: the one parser and resolver the boundaries, placement and cases checks use.

An observation id has the form the run's intake declares in `observations.id_form`
(`<prefix>observation:<resource_key>`). The prefix is empty for a single process whose observation
collection is called `observations`, and `<process_id>:` in a set whose collection is called
`<process_id>:observations`. The key is everything after the first `observation:`.

`parse(oid)` gives `(prefix or None, key)`, or None when the id is malformed (no `observation:`, an empty
key, whitespace anywhere, or a prefix that is only `:`).

`Resolver(run_dir).resolve(oid)` gives `(True, where)` or `(False, reason)`. It reads the snapshot intake
path from `stages/intake/intake.json`, then the observation files that snapshot intake lists in `sources[]`
(paths relative to the snapshot intake's folder), each loaded once per resolver. A source with
`collection_kind: observations` is an observation collection owned by its `process_id`; its prefix is its id
without the trailing `observations` (or `<process_id>:` when the id does not end so). With collections
declared, a prefixed id must name a collection and its key must be in that collection's file; an unprefixed
id must be in exactly one collection (two or more is ambiguous). A snapshot intake that declares no
collection (an older shape) is read through its listed source files only, each owned by its `process_id`: a
prefixed id must name a declared process (`<process_id>:`, from the run intake or the sources) and its key must
be in a file that process owns; a key found only in a file with no owner is a finding (ownership cannot be
established). Keys written inline in the snapshot intake itself never count.

A missing, unreadable or malformed listed source, or one whose path leaves the snapshot folder, makes the
ids that would need it unresolved, never a crash; so is an unprefixed key found once whose uniqueness cannot
be proven because another process's listed source is unreadable. `list_problems(resolver, ids, state)` holds the shared rule
for a citation list: empty is allowed for every evidence state except Observed; an Observed item needs at
least one id that resolves; a missing or unrecognised state is always a problem, whatever the ids.
Standard library only, Python 3.9.
"""
import json
import os

ID_FORM = "<prefix>observation:<resource_key>"
MARK = "observation:"
STATES = ("Observed", "Assumed", "Contradictory", "needs_more_information")
INTAKE = "stages/intake/intake.json"


def parse(oid):
    """(prefix or None, key) for a well-formed id, else None."""
    if not isinstance(oid, str) or MARK not in oid:
        return None
    if any(c.isspace() for c in oid):
        return None
    prefix, key = oid.split(MARK, 1)
    if not key or prefix in (":",):
        return None
    return (prefix or None, key)


def _file_keys(doc):
    """The observation keys of one loaded file: `items[].resource_key` (the intake's reading), else
    `observations[].resource_key`; a file with neither list is searched for `resource_key` anywhere."""
    if isinstance(doc, dict):
        for name in ("items", "observations"):
            if isinstance(doc.get(name), list):
                return {i["resource_key"] for i in doc[name]
                        if isinstance(i, dict) and isinstance(i.get("resource_key"), str)}
    return _keys_in(doc, set())


def _keys_in(doc, out):
    """Every `resource_key` string anywhere in one loaded document."""
    if isinstance(doc, dict):
        for k, v in doc.items():
            if k == "resource_key" and isinstance(v, str):
                out.add(v)
            else:
                _keys_in(v, out)
    elif isinstance(doc, list):
        for v in doc:
            _keys_in(v, out)
    return out


class Resolver(object):
    """Resolves observation ids for one run; loads the snapshot intake and each listed source once."""

    def __init__(self, run_dir):
        self.run_dir = run_dir
        self._loaded = False
        self.problem = None        # a reason every id is unresolved (no intake, unsupported id form, ...)
        self.collections = []      # [{"prefix", "process_id", "path", "keys" or None, "problem"}]
        self.owned = False         # True when the snapshot intake declares observation collections
        self.processes = set()     # declared process ids (run intake processes and source owners)
        self.loads = {}            # path -> number of times read (once each)

    # ------------------------------------------------------------ loading

    def _read(self, path):
        self.loads[path] = self.loads.get(path, 0) + 1
        with open(path, encoding="utf-8") as f:
            return json.load(f)

    def _load(self):
        if self._loaded:
            return
        self._loaded = True
        try:
            intake = self._read(os.path.join(self.run_dir, INTAKE))
        except (OSError, ValueError) as e:
            self.problem = "%s is unreadable: %s" % (INTAKE, e)
            return
        obs = intake.get("observations") if isinstance(intake, dict) else None
        rel = obs.get("snapshot_intake") if isinstance(obs, dict) else None
        if not isinstance(rel, str) or not rel:
            self.problem = "%s has no observations.snapshot_intake" % INTAKE
            return
        procs = intake.get("processes")
        for pr in procs if isinstance(procs, list) else []:
            if isinstance(pr, dict) and isinstance(pr.get("process_id"), str) and pr["process_id"]:
                self.processes.add(pr["process_id"])
        form = obs.get("id_form")
        if form is not None and form != ID_FORM:
            self.problem = "%s declares id_form %r; only %r is supported" % (INTAKE, form, ID_FORM)
            return
        snap_path = os.path.normpath(os.path.join(self.run_dir, rel))
        try:
            snap = self._read(snap_path)
        except (OSError, ValueError) as e:
            self.problem = "snapshot intake %s is unreadable: %s" % (rel, e)
            return
        if not isinstance(snap, dict):
            self.problem = "snapshot intake %s is not an object" % rel
            return
        base = os.path.dirname(snap_path)
        sources = snap.get("sources")
        sources = [s for s in sources if isinstance(s, dict)] if isinstance(sources, list) else []
        declared = [s for s in sources if s.get("collection_kind") == "observations"]
        self.owned = bool(declared)
        listed = declared if declared else [s for s in sources if s.get("path")]
        files = {}
        for s in listed:
            sid = s.get("id") if isinstance(s.get("id"), str) else ""
            pid = s.get("process_id") if isinstance(s.get("process_id"), str) and s.get("process_id") else None
            if pid:
                self.processes.add(pid)
            if sid.endswith("observations"):
                prefix = sid[:-len("observations")]
            else:
                prefix = (pid + ":") if pid else ""
            entry = {"prefix": prefix, "process_id": pid, "path": s.get("path"), "keys": None, "problem": None}
            p = s.get("path")
            if not isinstance(p, str) or not p:
                entry["problem"] = "source %r lists no path" % (sid or "?")
            else:
                full = os.path.normpath(os.path.join(base, p))
                if os.path.isabs(p) or os.path.commonpath([base, full]) != base:
                    entry["problem"] = "source path %r is outside the snapshot folder; refused" % p
                elif full in files:
                    entry["keys"], entry["problem"] = files[full]
                else:
                    try:
                        keys = _file_keys(self._read(full))
                        files[full] = (keys, None)
                    except (OSError, ValueError) as e:
                        files[full] = (None, "source %s is missing or unreadable: %s" % (p, e))
                    entry["keys"], entry["problem"] = files[full]
            self.collections.append(entry)

    # ------------------------------------------------------------ resolving

    def resolve(self, oid):
        """(True, where) when the id resolves, else (False, reason)."""
        parsed = parse(oid)
        if parsed is None:
            return False, "malformed observation id %r (form %s)" % (oid, ID_FORM)
        self._load()
        if self.problem:
            return False, self.problem
        prefix, key = parsed
        if prefix is None:
            return self._find_unprefixed(oid, key)
        if self.owned:
            mine = [c for c in self.collections if c["prefix"] == prefix]
            if not mine:
                return False, "prefix %r names no observation collection in the snapshot intake" % prefix
            return self._find(oid, key, mine, "the collection for %r" % prefix)
        pid = prefix[:-1] if prefix.endswith(":") else None
        if pid not in self.processes:
            return False, "prefix %r names no declared process or observation collection" % prefix
        mine = [c for c in self.collections if c["process_id"] == pid]
        found = self._find(oid, key, mine, "the sources of process %s" % pid)
        if found[0]:
            return found
        unowned = [c for c in self.collections if c["process_id"] is None]
        if [c for c in unowned if c["keys"] is not None and key in c["keys"]]:
            return False, "%r: key %s is only in a source with no process_id; ownership by %s cannot be " \
                          "established" % (oid, key, pid)
        if [c for c in unowned if c["problem"]]:
            return False, "%r is unresolved: ownership cannot be established (%s)" % (
                oid, "; ".join(c["problem"] for c in unowned if c["problem"]))
        return found

    def _find_unprefixed(self, oid, key):
        pool = self.collections
        hits = [c for c in pool if c["keys"] is not None and key in c["keys"]]
        owners = {c["process_id"] for c in hits}
        if len(owners - {None}) > 1:
            return False, "%r is ambiguous: key %s is in the sources of processes %s" % (
                oid, key, ", ".join(sorted(str(o) for o in owners - {None})))
        if not hits:
            return self._find(oid, key, pool, "any listed source")
        if len(self.processes) > 1:
            # another process's unreadable source could hold the same key: uniqueness is unproven
            owner = next(iter(owners)) if len(owners) == 1 else None
            other = [c["problem"] for c in pool if c["problem"] and (owner is None or c["process_id"] != owner)]
            if other:
                return False, "%r is unresolved: key %s cannot be shown unique because %s" % (
                    oid, key, "; ".join(other))
        return True, hits[0]["path"]

    def _find(self, oid, key, pool, label):
        hits = [c for c in pool if c["keys"] is not None and key in c["keys"]]
        if hits:
            return True, hits[0]["path"]
        broken = [c["problem"] for c in pool if c["problem"]]
        if broken:
            return False, "%r is unresolved: %s" % (oid, "; ".join(broken))
        return False, "key %s of %r is in no observation file of %s" % (key, oid, label)


def list_problems(resolver, ids, state):
    """Problems for one item's citation list: [(kind, detail)], kind 'invalid' or 'evidence'.

    Each id that does not resolve is 'invalid'. A missing or unrecognised state is always 'evidence', whether
    or not an id resolves. An Observed item with no id that resolves is 'evidence'. An empty list passes for
    Assumed, Contradictory and needs_more_information.
    """
    out = []
    good = 0
    for oid in ids or []:
        ok, why = resolver.resolve(oid)
        if ok:
            good += 1
        else:
            out.append(("invalid", why))
    if state is None:
        out.append(("evidence", "no evidence state is recorded; a missing state is a finding"))
    elif state not in STATES:
        out.append(("evidence", "evidence state %r is not one of %s" % (state, ", ".join(STATES))))
    elif state == "Observed" and not good:
        out.append(("evidence", "an Observed item needs at least one observation id that resolves"))
    return out
