#!/usr/bin/env python3
"""The process facts a Future State Design brief is filled with. Copied from fast-mode-build/briefs/make_brief.py at dev 61f6a7d
(build() and its helpers, unchanged); the template and the writing live in fsd.py. Stdlib only."""
import glob, hashlib, json, os, re

HERE = os.path.dirname(os.path.abspath(__file__))


def load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def tool_names(step):
    out = []
    for t in step.get("tools") or []:
        name = t if isinstance(t, str) else (t.get("name") or t.get("title") or "")
        if name.strip():
            out.append(name.strip())
    return out


def steps_of(version):
    for task in version.get("tasks") or []:
        for sub in task.get("sub_tasks") or []:
            yield from sub.get("steps") or []


def quoted(names):
    return ", ".join('"%s"' % n for n in names)


def count(n, one, many=None):
    return "%d %s" % (n, one if n == 1 else (many or one + "s"))


def is_empty(v):
    return v in ("", None, [], {}) or (isinstance(v, str) and v.strip().upper() in ("N/A", "NA"))


def context_of(version):
    extra = [x.strip() for x in (version.get("team"), version.get("value_stream")) if x and x.strip()]
    return " (%s)" % ", ".join(extra) if extra else ""


def process_set(inputs, snap, details):
    """The set's processes in the set order, as (resource key, current_version) pairs, and the set note text.
    The order is the snapshot's scope.process_ids; every details file must match one of them exactly once."""
    order = (snap.get("scope") or {}).get("process_ids") or []
    by_key = {}
    for path in details:
        doc = load(path)
        k = doc.get("process_resource_key")
        if not k or k in by_key:
            raise ValueError("cannot tell the processes apart: %s has %s process_resource_key" % (os.path.basename(path), "a repeated" if k else "no"))
        by_key[k] = doc.get("current_version") or {}
    if len(order) != len(set(order)) or sorted(order) != sorted(by_key):
        raise ValueError("cannot tell the order of the %d processes: the snapshot's scope.process_ids %s do not match the details files' keys %s"
                         % (len(details), order, sorted(by_key)))
    note = None
    intake_ref = snap.get("intake") or {}
    intake_path = os.path.join(inputs, intake_ref.get("path") or "snapshot-intake.json")
    ref = (load(intake_path).get("set_note") if os.path.isfile(intake_path) else None)
    if ref:
        path = os.path.join(os.path.dirname(intake_path), ref.get("path") or "")
        if not os.path.isfile(path):
            raise ValueError("the intake records a set note at %s but the file is not there" % path)
        with open(path, "rb") as f:
            raw = f.read()
        if hashlib.sha256(raw).hexdigest() != ref.get("sha256"):
            raise ValueError("the set note %s does not match the sha256 the intake recorded" % path)
        note = raw.decode("utf-8")  # verbatim: no stripping, indentation and blank lines kept
    return [(k, by_key[k]) for k in order], note


def set_section(members, note):
    keys = [k for k, _ in members]
    lines = ["", "", "## The process set",
             "This run designs one or more cooperating agents across %d related processes, in this order. Each is identified by its resource key (names can repeat); `snapshot.json` holds every one of them." % len(members), ""]
    for i, (k, version) in enumerate(members, 1):
        lines.append("%d. **%s**%s, resource key `%s`" % (i, (version.get("name") or "").strip(), context_of(version), k))
    lines += ["",
              "- The Current state Workflow covers the whole set: its `process.process_ids` lists every key above, in this order: %s. The workflow check requires exactly that list." % json.dumps(keys),
              "- Draw one current state across the set, not one picture per process. Where the set note or the evidence shows how the processes connect (for example several ways into a shared next process, or one process's result starting another), draw those connections as flow between the processes' steps: a handoff to the next process, or a decision that chooses between alternatives. If what decides between alternatives is not recorded, say so on that decision (needs more information); do not invent a rule. Keep separate start events only where the triggers really differ. A connection that is not evidenced is marked Assumed, never left out silently.",
              "- Every source id in `snapshot.json` starts with its process key, for example `%s:process-details`, `%s:process-details-steps-1-1`, `%s:observations`. Observed citations use these prefixed ids (`<key>:process-details-steps-T-S`), never a bare `process-details`." % (keys[0], keys[0], keys[0]),
              "", "### Set note (how the processes connect)"]
    if note:
        lines += ["Written at intake by the agent that read every process's pulls; quoted verbatim below. The driver does not copy it into `inputs/`; this section is its only copy for you, and it counts as part of the frozen intake. Use it for how the processes connect (variants, sequence, adjacent processes left out). Facts about any one process still come from `inputs/`; where the note and `inputs/` disagree, stamp it Contradictory.", "", note]
    else:
        lines.append("No set note was given. How these processes connect is needs more information; do not invent links between them.")
    return "\n".join(lines)


def key_control_lines(snap, members):
    """One brief line per process, in the given order: its key control from scope.key_control (pin P1). Only a `true` mark
    reads as key control; an older snapshot's "needs more information", a missing value or any other value reads as
    `false` (0503 R-people)."""
    kc = (snap.get("scope") or {}).get("key_control")
    kc = kc if isinstance(kc, dict) else {}
    lines = []
    for key, version in members:
        v = kc.get(key) if key else None
        value = "true" if v is True else "false"
        name = (version.get("name") or "").strip()
        lines.append("  - Process %s%s: key control `%s`." % ("**%s**" % name if name else "", " (resource key `%s`)" % key if key else "", value))
    return "\n".join(lines)


def build(inputs):
    snap = load(os.path.join(inputs, "snapshot.json"))
    details = sorted(glob.glob(os.path.join(inputs, "get_process_details-*.json")))
    if not details:
        raise ValueError("no get_process_details-*.json beside the snapshot")
    scoped = (snap.get("scope") or {}).get("process_ids") or []
    if len(details) == 1 and len(scoped) <= 1:
        version = load(details[0]).get("current_version") or {}
        name = (version.get("name") or "").strip()
        if not name:
            raise ValueError("the process details carry no process name")
        scope, intro = "one real process", "The process is **%s**%s. Everything known about it" % (name, context_of(version))
        versions, process_set_text = [version], ""
        key = scoped[0] if scoped else load(details[0]).get("process_resource_key")
        members = [(key, version)]
        legacy = {"PROCESS_NAME": name, "PROCESS_CONTEXT": context_of(version)}  # blanks older custom templates still use
    else:  # a set of processes: name every one, never pick one silently
        members, note = process_set(inputs, snap, details)
        unnamed = [k for k, v in members if not (v.get("name") or "").strip()]
        if unnamed:
            raise ValueError("the process details for %s carry no process name" % ", ".join(unnamed))
        scope = "a set of %d related real processes" % len(members)
        intro = "The processes are the set of %d named under \"The process set\" below. Everything known about them" % len(members)
        versions = [v for _, v in members]
        process_set_text = set_section(members, note)
        legacy = {}  # a single-process template's {{PROCESS_NAME}} stays unfilled for a set, so it fails loudly

    steps = [step for v in versions for step in steps_of(v)]
    items = [i for p in sorted(glob.glob(os.path.join(inputs, "get_process_observations-*.json"))) for i in load(p).get("items") or []]
    empty = sorted({k for i in items for k, v in (i.get("attributes") or {}).items() if is_empty(v)})
    evidence = "The frozen evidence holds %s, %s and %s." % (count(len(steps), "step"), count(sum(len(v.get("policies") or []) for v in versions), "policy", "policies"), count(len(items), "observation"))
    if empty:
        evidence += " These observation attribute fields are empty or \"N/A\": %s." % ", ".join(empty)

    inventory = [s if isinstance(s, str) else (s.get("name") or "") for s in snap.get("systems") or []]
    inventory = [s.strip() for s in inventory if s and s.strip()]
    used = {t.lower() for s in steps for t in tool_names(s)}
    unused = [s for s in inventory if s.lower() not in used]
    systems = "The snapshot's systems inventory holds %d names: %s." % (len(inventory), quoted(inventory))
    if unused:
        systems += " %s %s in no step's tools list." % (quoted(unused), "appears" if len(unused) == 1 else "appear")
    systems += " Where two names may be one system, say what the evidence shows and stamp the rest needs more information."
    return {"PROCESS_SCOPE": scope, "PROCESS_INTRO": intro, "EVIDENCE_DESCRIPTION": evidence, "SYSTEMS_INVENTORY": systems, "PROCESS_SET": process_set_text,
            "KEY_CONTROL": key_control_lines(snap, members), **legacy}
