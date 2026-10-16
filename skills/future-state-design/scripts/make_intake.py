#!/usr/bin/env python3
"""Copied from fast-mode-build/tools/make_intake.py at dev 61f6a7d; only the reference paths changed (read through knowledge.py).

Make a snapshot folder from two saved Within connector responses.

Usage:
    make_intake.py --details DETAILS.json --observations OBSERVATIONS.json \\
        --process-id ID --out SNAPSHOT_FOLDER [--cutoff-at ISO8601]
    make_intake.py --process ID DETAILS.json OBSERVATIONS.json [--process ID2 ...] \\
        [--set-note SET-NOTE.md] --out SNAPSHOT_FOLDER [--cutoff-at ISO8601]

The second form takes a set of related processes, identified by resource key, in the set order. One --process is
the same as the first form. With two or more, each process passes the same checks as a single one (any refusal
refuses the whole set), source and issue ids are prefixed "<id>:", scope.process_ids lists the ids in the set
order, each process's parsed systems attribute is written as get_process_<id>-systems-used.json (the stage runner
copies get_process_*.json into the run's inputs), and the optional set note is copied unchanged as set-note.md and
recorded in the intake's "set_note" with its sha256 (a missing set note is a warning, not an error).

Inputs: the process details response (all scopes) and the observations response (full verbosity),
each saved exactly as the connector returned it. They are copied byte for byte and never edited.

Writes into --out (a new or empty folder):
  get_process_details-<id>-all-scopes.json, get_process_observations-<id>-full.json   the raw responses
  systems-used-attribute.json   the systems_used attribute parsed from its list-string into a list
  snapshot-intake.json          one source per sub-task steps group, the details source, the observations
                                source (mapped by /items and /total so an incomplete pull is caught), the
                                systems source, and one open coverage issue for systems named in the
                                attribute that appear in no step's tools list
  snapshot.json                 frozen with the plugin's freeze script after the intake check passes

scope.key_control holds one entry per id in scope.process_ids: true or false, from the process details' key_control
field (top level, else the key_control attribute's value). Key control is true only when the Work Brain marks it: a raw
true, or the text true/yes in any case (exactly, no padding); anything else, or no such field, maps to false (0503 R-people).

cutoff_at defaults to the moment this command runs (UTC); pass --cutoff-at to set it.
Prints what it made. Exit 0 done; 1 the intake check or freeze refused; 2 setup error.
"""
import argparse
import ast
import hashlib
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import knowledge  # the one switch for where reference material and its scripts live
CHECK_INTAKE = os.path.join(knowledge.path("checks"), "check-snapshot-intake.py")
CHECK_FROZEN = os.path.join(knowledge.path("checks"), "check-snapshot-structure.py")
FREEZE = knowledge.path("freeze_snapshot")
SYSTEMS_NAME = "systems-used-attribute.json"
SET_NOTE_NAME = "set-note.md"


class SetupError(Exception):
    pass


def load(path, what):
    try:
        with open(path, encoding="utf-8") as f:
            value = json.load(f)
    except (OSError, ValueError) as e:
        raise SetupError("cannot read %s %s: %s" % (what, path, e))
    if not isinstance(value, dict):
        raise SetupError("%s %s is not a JSON object" % (what, path))
    return value


def sha256(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def key(name):
    return " ".join(str(name).split()).casefold()


def parse_systems(value):
    """The attribute arrives as a Python list-string such as "['A', 'B']"; a plain comma list is also accepted."""
    if isinstance(value, list):
        names = value
    elif isinstance(value, str) and value.strip().startswith("["):
        try:
            names = ast.literal_eval(value.strip())
        except (ValueError, SyntaxError) as e:
            raise SetupError("the systems_used attribute is not a readable list (%s): %r" % (e, value[:80]))
    elif isinstance(value, str):
        names = value.split(",")
    else:
        raise SetupError("the systems_used attribute has no usable value")
    if not isinstance(names, list):
        raise SetupError("the systems_used attribute did not parse to a list")
    out = []
    for n in names:
        if not isinstance(n, str):
            raise SetupError("the systems_used attribute holds a non-text entry: %r" % (n,))
        n = " ".join(n.split())
        if n:
            out.append(n)
    return out


NEEDS_INFO = "needs more information"


def key_control_of(details):
    """The process's key control, read from the raw process details: True only when the Work Brain marks it true, else
    False (no mark, or a mark that is not true, reads as No; 0503 R-people)."""
    if "key_control" in details:
        raw = details["key_control"]
    else:
        attrs = [a for a in details.get("attributes") or [] if isinstance(a, dict) and a.get("key") == "key_control"]
        raw = attrs[0].get("value") if attrs else None
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        word = raw.casefold()
        if word in ("true", "yes"):
            return True
    return False


def step_groups(details):
    """(task number, sub-task number, pointer, steps) for every sub-task that holds steps; numbers are 1-based."""
    version = details.get("current_version")
    if not isinstance(version, dict):
        raise SetupError("the process details carry no current_version")
    for ti, task in enumerate(version.get("tasks") or []):
        for si, sub in enumerate(task.get("sub_tasks") or []):
            steps = sub.get("steps") or []
            if steps:
                yield ti + 1, si + 1, "/current_version/tasks/%d/sub_tasks/%d/steps" % (ti, si), steps


def tools_of(step):
    return [t for t in (step.get("tools") or []) if isinstance(t, str) and t.strip()]


def place(src, dest):
    """Copy a raw response byte for byte; skip when it is already the destination file."""
    if os.path.exists(dest) and os.path.samefile(src, dest):
        return
    shutil.copyfile(src, dest)


def systems_entry(details, process_id, details_name, out):
    """The systems_used attribute parsed into a list, with where it came from; None when the process has no such attribute."""
    attrs = [a for a in details.get("attributes") or [] if isinstance(a, dict) and a.get("key") == "systems_used"]
    if not attrs:
        return None
    names = parse_systems(attrs[0].get("value"))
    idx = details["attributes"].index(attrs[0])
    return {"process_resource_key": process_id,
            "derived_from": {"file": details_name, "pointer": "/attributes/%d/value" % idx, "sha256": sha256(os.path.join(out, details_name))},
            "note": "Deterministic parse of the systems_used attribute string into a list; names unchanged. Not a raw pull.",
            "systems_used": names}


def build(details, observations, process_id, cutoff_at, details_name, observations_name, out, prefix="", systems_name=SYSTEMS_NAME):
    """One process's intake. A set of processes calls this once per process with prefix "<process id>:" (source
    and issue ids must be unique across the set) and its own systems_name."""
    if details.get("process_resource_key") != process_id:
        raise SetupError("the process details are for %r, not %r" % (details.get("process_resource_key"), process_id))
    items, total = observations.get("items"), observations.get("total")
    if not isinstance(items, list) or type(total) is not int:
        raise SetupError("the observations response needs an 'items' list and an integer 'total'")
    if total != len(items):
        raise SetupError("the observations pull is incomplete: %d items saved of %d reported; pull the rest and save the full response" % (len(items), total))

    def source(sid, name, mapping, **extra):
        path = os.path.join(out, name)
        return {"id": sid, "process_id": process_id, "status": "included", "path": name, "sha256": sha256(path), **extra, "mapping": mapping}

    sources, required, used_tools = [], [], {}
    sources.append(source(prefix + "process-details", details_name, {"process_id": "/process_resource_key"}))
    required.append(prefix + "process-details")
    for t, s, ptr, steps in step_groups(details):
        sid = prefix + "process-details-steps-%d-%d" % (t, s)
        sources.append(source(sid, details_name, {"process_id": "/process_resource_key", "steps": ptr, "step_id": "/code", "step_tools": "/tools"}))
        required.append(sid)
        for step in steps:
            for tool in tools_of(step):
                used_tools.setdefault(key(tool), tool)
    if len(sources) == 1:
        raise SetupError("the process details hold no steps in any sub-task")
    sources.append(source(prefix + "observations", observations_name, {"items": "/items", "total": "/total", "item_id": "/resource_key", "observed_at": "/timestamp"}, collection_kind="observations"))
    required.append(prefix + "observations")

    issues = []
    systems_file = systems_entry(details, process_id, details_name, out)
    if systems_file:
        names = systems_file["systems_used"]
        with open(os.path.join(out, systems_name), "w", encoding="utf-8") as f:
            json.dump(systems_file, f, indent=2, ensure_ascii=False)
            f.write("\n")
        sources.append(source(prefix + "systems-used-attribute", systems_name, {"process_id": "/process_resource_key", "systems": "/systems_used"}))
        required.append(prefix + "systems-used-attribute")
        unused = [n for n in names if key(n) not in used_tools]
        if unused:
            issues.append({"id": prefix + "issue-systems-not-in-steps", "kind": "coverage", "status": "open", "source_ids": [prefix + "systems-used-attribute"],
                           "reason": "The process-level systems_used attribute names %s but no step's tools list does." % ", ".join('"%s"' % n for n in unused)})
    return {"schema_version": 1, "kind": "snapshot-intake",
            "scope": {"process_ids": [process_id], "cutoff_at": cutoff_at, "key_control": {process_id: key_control_of(details)}},
            "required_source_ids": required, "sources": sources, "issues": issues}


def systems_name(process_id):
    """A set's per-process systems file; the get_process_ prefix makes the stage runner copy it into the run's inputs."""
    return "get_process_%s-systems-used.json" % process_id


def undo(out, created, written):
    """Remove what this invocation wrote, so a refusal leaves no half-made folder."""
    if created:
        shutil.rmtree(out, ignore_errors=True)
    else:
        for path in written:
            if os.path.exists(path):
                os.remove(path)


def run(cmd):
    r = subprocess.run([sys.executable] + cmd, capture_output=True, text=True)
    return r.returncode, (r.stdout + r.stderr).strip()


def merge(intakes, cutoff_at):
    """One intake for a set: every process's sources, required ids and issues, in the set order."""
    out = {"schema_version": 1, "kind": "snapshot-intake",
           "scope": {"process_ids": [], "cutoff_at": cutoff_at, "key_control": {}}, "required_source_ids": [], "sources": [], "issues": []}
    for one in intakes:
        out["scope"]["process_ids"] += one["scope"]["process_ids"]
        out["scope"]["key_control"].update(one["scope"]["key_control"])
        for k in ("required_source_ids", "sources", "issues"):
            out[k] += one[k]
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--details", help="saved process details response (all scopes)")
    ap.add_argument("--observations", help="saved observations response (full verbosity)")
    ap.add_argument("--process-id")
    ap.add_argument("--process", nargs=3, action="append", metavar=("PROCESS_ID", "DETAILS", "OBSERVATIONS"),
                    help="one process of a set; repeat once per process, in the set order (instead of --details/--observations/--process-id)")
    ap.add_argument("--set-note", default=None, help="Markdown note on how the processes in the set connect; copied unchanged")
    ap.add_argument("--out", required=True, help="snapshot folder to write (new or empty)")
    ap.add_argument("--cutoff-at", default=None, help="ISO-8601 with timezone; default is the moment of running (UTC)")
    a = ap.parse_args(argv)
    single = (a.details, a.observations, a.process_id)
    if a.process:
        if any(single):
            ap.error("--process cannot be combined with --details, --observations or --process-id")
        processes = [tuple(p) for p in a.process]
    elif all(single):
        processes = [(a.process_id, a.details, a.observations)]
    else:
        ap.error("give --details, --observations and --process-id, or one or more --process PROCESS_ID DETAILS OBSERVATIONS")
    is_set = len(processes) > 1
    created, copied = False, []
    try:
        cutoff = a.cutoff_at or datetime.now(timezone.utc).isoformat(timespec="seconds")
        try:
            if datetime.fromisoformat(cutoff.replace("Z", "+00:00")).tzinfo is None:
                raise ValueError("no timezone")
        except ValueError as e:
            raise SetupError("--cutoff-at must be ISO-8601 with a timezone (%s)" % e)
        ids = [p[0] for p in processes]
        repeated = sorted({i for i in ids if ids.count(i) > 1})
        if repeated:
            raise SetupError("process id repeated in the set: %s; give each process once" % ", ".join(repeated))
        if is_set and any(not i or os.sep in i or i.startswith(".") for i in ids):
            raise SetupError("a process id cannot be empty, start with '.' or hold '%s' (it names the raw files)" % os.sep)
        if a.set_note and not is_set:
            raise SetupError("--set-note is for a set of two or more processes")
        if a.set_note:
            try:
                with open(a.set_note, encoding="utf-8") as f:
                    if not f.read().strip():
                        raise SetupError("the set note %s is empty" % a.set_note)
            except (OSError, ValueError) as e:
                raise SetupError("cannot read set note %s: %s" % (a.set_note, e))
        loaded = []
        for pid, details_path, observations_path in processes:
            loaded.append((pid, details_path, observations_path, load(details_path, "details"), load(observations_path, "observations"),
                           "get_process_details-%s-all-scopes.json" % pid, "get_process_observations-%s-full.json" % pid))
        for f in ("snapshot-intake.json", "snapshot.json", SYSTEMS_NAME) + tuple(systems_name(i) for i in ids if is_set) + ((SET_NOTE_NAME,) if a.set_note else ()):
            if os.path.exists(os.path.join(a.out, f)):
                raise SetupError("%s already exists in %s; use a new folder" % (f, a.out))
        # Validate before writing anything, so a refusal leaves no half-made folder.
        created = not os.path.isdir(a.out)
        os.makedirs(a.out, exist_ok=True)
        for pid, details_path, observations_path, _, _, details_name, observations_name in loaded:
            for src, name in ((details_path, details_name), (observations_path, observations_name)):
                dest = os.path.join(a.out, name)
                if not os.path.exists(dest):
                    copied.append(dest)
                place(src, dest)
        if not is_set:
            pid, _, _, details, observations, details_name, observations_name = loaded[0]
            intake = build(details, observations, pid, cutoff, details_name, observations_name, a.out)
        else:
            # Each process then passes the same checks as a single process (build); any refusal removes everything written.
            copied += [os.path.join(a.out, systems_name(i)) for i in ids]
            intake = merge([build(details, observations, pid, cutoff, details_name, observations_name, a.out, prefix=pid + ":", systems_name=systems_name(pid))
                            for pid, _, _, details, observations, details_name, observations_name in loaded], cutoff)
            if a.set_note:
                dest = os.path.join(a.out, SET_NOTE_NAME)
                copied.append(dest)
                shutil.copyfile(a.set_note, dest)
                intake["set_note"] = {"path": SET_NOTE_NAME, "sha256": sha256(dest), "process_ids": ids,
                                      "note": "Free-prose note on how the processes in the set connect, copied unchanged. Not a raw pull."}
    except SetupError as e:
        print("setup error: %s" % e, file=sys.stderr)
        undo(a.out, created, copied)
        return 2
    intake_path = os.path.join(a.out, "snapshot-intake.json")
    with open(intake_path, "w", encoding="utf-8") as f:
        json.dump(intake, f, indent=2, ensure_ascii=False)
        f.write("\n")
    snap_path = os.path.join(a.out, "snapshot.json")
    for label, cmd in (("intake check", [CHECK_INTAKE, intake_path]), ("freeze", [FREEZE, intake_path, snap_path]), ("frozen check", [CHECK_FROZEN, snap_path])):
        code, text = run(cmd)
        if code:
            print("%s refused: %s" % (label, text), file=sys.stderr)
            # Everything this run wrote goes too: the raw copies, systems files, set note, intake and snapshot.
            undo(a.out, created, copied + [intake_path, snap_path, os.path.join(a.out, SYSTEMS_NAME)])
            return 1
    groups = [s for s in intake["sources"] if "steps" in s["mapping"]]
    print("Snapshot folder made: %s" % a.out)
    if is_set:
        print("  processes: %d, in this order: %s" % (len(loaded), ", ".join(ids)))
    print("  sources: %d (%d step groups, details, observations, systems)" % (len(intake["sources"]), len(groups)))
    for _, _, _, _, observations, _, _ in loaded:
        print("  observations: %d of %d" % (len(observations["items"]), observations["total"]))
    print("  open issues: %d" % len(intake["issues"]))
    print("  key control: %s" % ", ".join("%s %s" % (i, json.dumps(v)) for i, v in intake["scope"]["key_control"].items()))
    print("  cutoff_at: %s" % cutoff)
    systems_files = [n for n in ([systems_name(i) for i in ids] if is_set else [SYSTEMS_NAME]) if os.path.exists(os.path.join(a.out, n))]
    print("  wrote: snapshot-intake.json, snapshot.json%s%s" % ("".join(", " + n for n in systems_files), ", " + SET_NOTE_NAME if is_set and a.set_note else ""))
    if is_set:
        print("  set note: %s" % (SET_NOTE_NAME if a.set_note else "none"))
    print("  checks: intake PASS, frozen PASS")
    if is_set and not a.set_note:
        print("warning: no --set-note given for a set of %d processes; the brief will name the processes but say nothing about how they connect" % len(loaded), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
