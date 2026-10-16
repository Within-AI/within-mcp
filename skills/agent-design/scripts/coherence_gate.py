#!/usr/bin/env python3
"""Coherence gate: every stage check plus four cross-stage checks, run before page preparation.

    coherence_gate.py run --run RUN      run the checks, write stages/coherence-gate/report.json, route once
    coherence_gate.py status --run RUN   work out gate state from the check reports on disk

A check that did not run, exited 2, crashed, timed out or left no usable report is unchecked,
never passed. The gate passes only when every check passed. A failure is routed once to its
owning stage (route-<stage>.json); a later failing run writes open items and stops; a run after
that refuses to route again. The gate dispatches nothing. Standard library only.
"""
import importlib.util
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
REPO = os.path.dirname(os.path.dirname(SKILL))
GATE_PATH = "skills/agent-design/scripts/coherence_gate.py"
DIAGRAM_PATH = os.path.join(SKILL, "page", "diagram", "diagram.py")

CHECK_TIME_LIMIT = 300  # seconds per stage check

PASSED, FAILED, UNCHECKED = "passed", "failed", "unchecked"
REPORT_FIELDS = ("check", "path", "run", "exit_code", "result", "problems", "counts", "agent_fallback")
QUALIFIED = re.compile(r"^[^:\s]+:[^:\s]+$")
OUTBOUND_WRITES = ("write", "person_write")
sys.path.insert(0, os.path.join(os.path.dirname(SKILL), "fast-mode-build", "driver"))
import knowledge_root  # noqa: E402  the one lookup for the plugin's knowledge folder
sys.path.insert(0, os.path.join(knowledge_root.find(), "checks"))
import _write_grants as wg  # noqa: E402  the one resolver and write-kinds table


def _fallback(script, examine):
    return ("If %s cannot run, open the run folder and examine %s; write each defect you find as a problem "
            "with the file and id, and treat the check as unchecked until the script runs." % (script, examine))


STAGE_CHECKS = [
    {"name": "check_architecture", "path": "skills/agent-design/checks/check_architecture.py", "stage": "architecture",
     "fallback": _fallback("skills/agent-design/checks/check_architecture.py",
                           "stages/architecture/agent.yaml: parts, owned steps, handovers and departures against the future state")},
    {"name": "check_grants", "path": "skills/agent-design/checks/check_grants.py", "stage": "architecture",
     "fallback": _fallback("skills/agent-design/checks/check_grants.py",
                           "stages/architecture/agent.yaml proposed_writes and part grants against the Write grants table upstream")},
    {"name": "check_boundaries", "path": "skills/agent-design/checks/check_boundaries.py", "stage": "boundaries",
     "fallback": _fallback("skills/agent-design/checks/check_boundaries.py",
                           "stages/boundaries/boundaries.json: one boundary per future-state edge the agent design crosses")},
    {"name": "check_rules", "path": "skills/agent-design/checks/check_rules.py", "stage": "rules",
     "fallback": _fallback("skills/agent-design/checks/check_rules.py",
                           "stages/rules/rules.json: each rule's expression, tokens, configuration values and evidence")},
    {"name": "check_outcomes", "path": "skills/agent-design/checks/check_outcomes.py", "stage": "rules",
     "fallback": _fallback("skills/agent-design/checks/check_outcomes.py",
                           "stages/rules/outcomes.json against the rules: gaps and overlaps per decision, open items skipped")},
    {"name": "check_placement", "path": "skills/agent-design/checks/check_placement.py", "stage": "placement",
     "fallback": _fallback("skills/agent-design/checks/check_placement.py",
                           "stages/placement/placed-rules.json and declarations.json: each rule placed at an owned step, stand-ins declared")},
    {"name": "supply_check", "path": "skills/agent-design/checks/supply_check.py", "stage": "fields",
     "fallback": _fallback("skills/agent-design/checks/supply_check.py",
                           "stages/fields/fields.json: every token a rule reads has a field or declaration supplying it")},
    {"name": "check_cases", "path": "skills/agent-design/checks/check_cases.py", "stage": "test-suite",
     "fallback": _fallback("skills/agent-design/checks/check_cases.py",
                           "stages/test-suite/test-suite.json: cases cover each decision and outcome, inputs complete")},
]

CROSS_CHECKS = [
    {"name": "gate_seating", "stage": "rules",
     "fallback": _fallback(GATE_PATH + " (gate_seating)",
                           "stages/rules/outcomes.json gates against agent.yaml parts' owns_steps, as <process_id>:<node_id>")},
    {"name": "open_item_kind", "stage": "rules",
     "fallback": _fallback(GATE_PATH + " (open_item_kind)",
                           "open-items.json under stages/rules, stages/fields and stages/placement: one kind per open item id")},
    {"name": "grant_parity", "stage": "fields",
     "fallback": _fallback(GATE_PATH + " (grant_parity)",
                           "agent.yaml grants against the fields keyed on each grant id (the field's grant, the boundary's grant ref or the agent step's write_grant), satisfied per write kind; a grant no boundary touches is an open obligation")},
    {"name": "render_faithfulness", "stage": "fields",
     "fallback": _fallback(GATE_PATH + " (render_faithfulness)",
                           "page/diagram/diagram.py diagram_data(run_dir) writes and person_handovers against the write and hand-back fields")},
]


# ---------------------------------------------------------------- helpers

def _load(path):
    with open(path) as f:
        return json.load(f)


def _write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2, sort_keys=True)
        f.write("\n")


def _items(data, key):
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and isinstance(data.get(key), list):
        return data[key]
    raise ValueError("expected a list or an object with a %r list" % key)


def _gate_dir(run_dir):
    return os.path.join(run_dir, "stages", "coherence-gate")


def _problem(defect, where, detail):
    return {"defect": defect, "where": where, "detail": detail}


class Unchecked(Exception):
    pass


def _need(run_dir, rel):
    path = os.path.join(run_dir, rel)
    if not os.path.isfile(path):
        raise Unchecked("missing input %s" % rel)
    try:
        return _load(path)
    except (OSError, ValueError) as e:
        raise Unchecked("unreadable input %s: %s" % (rel, e))


# ---------------------------------------------------------------- stage checks

def run_check(entry, run_dir, out_path):
    """Run one stage check through the pinned entry point. Returns (exit_code or None, note).

    Tests replace this function with a stub that writes a report to out_path."""
    script = os.path.join(REPO, entry["path"])
    if not os.path.isfile(script):
        return None, "not run: %s is absent" % entry["path"]
    try:
        proc = subprocess.run([sys.executable, script, "--run", run_dir, "--out", out_path],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=CHECK_TIME_LIMIT)
    except subprocess.TimeoutExpired:
        return None, "exceeded the %d s time limit" % CHECK_TIME_LIMIT
    return proc.returncode, ""


REL_PATH = re.compile(r"^(?![/~])(?![A-Za-z]:)(?!(.*/)?\.\.(/|$))[^\\]+$")
RESULT_CODE = {"pass": 0, "fail": 1, "error": 2}


def _is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def report_shape_errors(report):
    """Every way a report departs from U1's check-report schema (formats/lists/check-report.schema.json)."""
    if not isinstance(report, dict):
        return ["report is not an object"]
    missing = [k for k in REPORT_FIELDS if k not in report]
    if missing:
        return ["report lacks %s" % ", ".join(missing)]
    errors = []
    for k in ("check", "agent_fallback"):
        if not (isinstance(report[k], str) and report[k]):
            errors.append("%s is not a non-empty string" % k)
    for k in ("path", "run"):
        if not (isinstance(report[k], str) and REL_PATH.match(report[k])):
            errors.append("%s is not a relative path" % k)
    code, result = report["exit_code"], report["result"]
    if not (_is_int(code) and code in (0, 1, 2)):
        errors.append("exit_code %r is not 0, 1 or 2" % (code,))
    if result not in RESULT_CODE:
        errors.append("result %r is not pass, fail or error" % (result,))
    problems = report["problems"]
    if not isinstance(problems, list):
        errors.append("problems is not a list")
    else:
        for i, p in enumerate(problems):
            if not isinstance(p, dict):
                errors.append("problems[%d] is not an object" % i)
                continue
            if not (isinstance(p.get("defect"), str) and p["defect"]):
                errors.append("problems[%d] lacks a non-empty defect" % i)
            for k in ("where", "detail"):
                if not isinstance(p.get(k), str):
                    errors.append("problems[%d] lacks a string %s" % (i, k))
    counts = report["counts"]
    if not isinstance(counts, dict):
        errors.append("counts is not an object")
    elif any(not _is_int(v) for v in counts.values()):
        errors.append("counts holds a value that is not an integer")
    if not errors:
        if code != RESULT_CODE[result]:
            errors.append("report result %r contradicts its exit_code %r" % (result, code))
        elif result == "fail" and not problems:
            errors.append("report result fail names no problems")
    return errors


def judge_report(report_path, exit_code=None):
    """State of one check from its report (and its exit code when it was just run).

    A report outside the full check-report shape is unchecked, never passed or failed."""
    if not os.path.isfile(report_path):
        return UNCHECKED, "left no report", None
    try:
        report = _load(report_path)
    except (OSError, ValueError) as e:
        return UNCHECKED, "report unreadable: %s" % e, None
    errors = report_shape_errors(report)
    if errors:
        return UNCHECKED, "report malformed: %s" % "; ".join(errors), None
    if exit_code is not None and report["exit_code"] != exit_code:
        return UNCHECKED, "report exit_code %r contradicts exit %r" % (report["exit_code"], exit_code), report
    result, code = report["result"], report["exit_code"]
    if result == "pass" and code == 0:
        return PASSED, "", report
    if result == "fail" and code == 1:
        return FAILED, "", report
    return UNCHECKED, "check reported an error (exit 2)", report


def _unchecked_report(entry, reason):
    """The report the gate leaves in place of an unsuccessful execution's, so `status` also says unchecked."""
    return {"check": entry["name"], "path": entry["path"], "run": "checks/%s.json" % entry["name"],
            "exit_code": 2, "result": "error",
            "problems": [_problem("unchecked", entry["path"], "written by the coherence gate: %s" % reason)],
            "counts": {}, "agent_fallback": entry["fallback"]}


def _execute_stage_check(entry, run_dir, out):
    try:
        code, note = run_check(entry, run_dir, out)
    except Exception as e:  # the runner itself crashed
        return UNCHECKED, "crashed: %s" % e, None
    if code is None:
        return UNCHECKED, note, None
    if code == 2:
        return UNCHECKED, "exited 2", None
    if code not in (0, 1):
        return UNCHECKED, "crashed (exit %s)" % code, None
    return judge_report(out, code)


def _run_stage_check(entry, run_dir):
    out = os.path.join(run_dir, "checks", entry["name"] + ".json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    if os.path.exists(out):
        os.remove(out)  # a report from an earlier run never stands in for this one
    state, reason, report = _execute_stage_check(entry, run_dir, out)
    if state == UNCHECKED:
        _write(out, _unchecked_report(entry, reason))  # whatever the execution left never passes later
        return state, reason, []
    return state, reason, report["problems"] if state == FAILED else []


# ---------------------------------------------------------------- cross-stage checks

def _agent(run_dir):
    return _need(run_dir, "stages/architecture/agent.yaml")


def _grant_systems(agent):
    out = {}
    for w in agent.get("header", {}).get("proposed_writes", []):
        out[w.get("grant")] = w.get("system")
    return out


def _fields(run_dir):
    try:
        return _items(_need(run_dir, "stages/fields/fields.json"), "fields")
    except ValueError as e:
        raise Unchecked("fields.json: %s" % e)


def _boundaries(run_dir):
    try:
        items = _items(_need(run_dir, "stages/boundaries/boundaries.json"), "boundaries")
    except ValueError as e:
        raise Unchecked("boundaries.json: %s" % e)
    return {b.get("id"): b for b in items}


def _system_titles(run_dir):
    """System node id -> title, from each `system_surface` node of the run's future states (the intake's
    `processes[].future_state`). Boundaries name a system by node id; proposed_writes by that title.
    No intake, or an unreadable future state, gives no titles: the boundary's ref is then compared as it is."""
    out = {}
    try:
        intake = _load(os.path.join(run_dir, "stages", "intake", "intake.json"))
    except (OSError, ValueError):
        return out
    for proc in (intake.get("processes") or []) if isinstance(intake, dict) else []:
        rel = proc.get("future_state") if isinstance(proc, dict) else None
        if not isinstance(rel, str) or not rel or rel.startswith(("/", "~")) or ".." in rel.split("/"):
            continue
        try:
            doc = _load(os.path.join(run_dir, rel))
        except (OSError, ValueError):
            continue
        g = doc.get("graph", doc) if isinstance(doc, dict) else {}
        for n in (g.get("nodes") or []) if isinstance(g, dict) else []:
            if isinstance(n, dict) and n.get("type") == "system_surface" and n.get("id") and n.get("title"):
                out[n["id"]] = n["title"]
    return out


def _field_system(field, boundaries, titles=None):
    """The system a write field lands in: its boundary's to side, when that side is a system, named by the
    system node's title when the future state draws one (the name proposed_writes[].system uses)."""
    b = boundaries.get(field.get("boundary_id"))
    if not b:
        return None
    to = b.get("to") or {}
    if to.get("side") != "system":
        return None
    return (titles or {}).get(to.get("ref"), to.get("ref"))


def check_gate_seating(run_dir):
    outcomes = _need(run_dir, "stages/rules/outcomes.json")
    agent = _agent(run_dir)
    owned = set()
    for part in agent.get("parts", []):
        owned.update(part.get("owns_steps", []))
    problems = []
    for gate in outcomes.get("gates", []):
        step = gate.get("step") or gate.get("id")
        if not (isinstance(step, str) and QUALIFIED.match(step)):
            problems.append(_problem("gate-unseated", "stages/rules/outcomes.json gate %s" % gate.get("id"),
                                     "gate names no qualified step <process_id>:<node_id>"))
        elif step not in owned:
            problems.append(_problem("gate-unseated", "stages/rules/outcomes.json gate %s" % gate.get("id"),
                                     "step %s is owned by no part" % step))
    return problems


OPEN_ITEM_FILES = [("rules", True), ("fields", False), ("placement", False)]


def check_open_item_kind(run_dir):
    kinds = {}
    for stage, required in OPEN_ITEM_FILES:
        rel = "stages/%s/open-items.json" % stage
        if not required and not os.path.isfile(os.path.join(run_dir, rel)):
            continue
        try:
            items = _items(_need(run_dir, rel), "items")
        except ValueError as e:
            raise Unchecked("%s: %s" % (rel, e))
        for item in items:
            kinds.setdefault(item.get("id"), {}).setdefault(item.get("kind"), []).append(stage)
    problems = []
    for item_id in sorted(kinds, key=str):
        if len(kinds[item_id]) > 1:
            detail = "; ".join("%s in %s" % (k, ", ".join(s)) for k, s in sorted(kinds[item_id].items(), key=str))
            problems.append(_problem("open-item-kind-conflict", "open item %s" % item_id, detail))
    return problems


def _run_graphs_and_rows(run_dir):
    """[(process id, future-state doc)] and the Write grants rows of the run's transformation maps (intake's
    `processes[]`). Anything unreadable gives nothing for that process: the gate falls back to proposed_writes."""
    graphs, rows, seen = [], [], set()
    try:
        intake = _load(os.path.join(run_dir, "stages", "intake", "intake.json"))
    except (OSError, ValueError):
        return graphs, rows
    for proc in (intake.get("processes") or []) if isinstance(intake, dict) else []:
        if not isinstance(proc, dict):
            continue
        for key in ("future_state", "transformation_map"):
            rel = proc.get(key)
            if not isinstance(rel, str) or not rel or rel.startswith(("/", "~")) or ".." in rel.split("/"):
                continue
            path = os.path.join(run_dir, rel)
            try:
                if key == "future_state":
                    graphs.append((proc.get("process_id"), wg.with_source(_load(path), rel)))
                elif rel not in seen:
                    seen.add(rel)
                    with open(path, encoding="utf-8") as fh:
                        rows.extend(wg.read_table(fh.read()) or [])
            except (OSError, ValueError, wg.TableError):
                continue
    return graphs, rows


def _edge_ends(graphs):
    """(process id, edge id) -> (source, target) for every drawn edge of the run's future states."""
    out = {}
    for pid, doc in graphs:
        g = doc.get("graph", doc) if isinstance(doc, dict) else {}
        for e in (g.get("edges") or []) if isinstance(g, dict) else []:
            if isinstance(e, dict) and isinstance(e.get("id"), str):
                out.setdefault((pid, e["id"]), (e.get("source"), e.get("target")))
    return out


def _side_step_ref(b, name, ends=None):
    """The qualified step a boundary side names: its own qualified ref, else the end of the drawn edge its
    `edge_ref` names, else the step an `implied:` ref carries for the agent side. None when none says."""
    side = b.get(name) or {}
    ref = side.get("ref") or ""
    if ":" in ref:
        return ref
    edge = b.get("edge_ref") or ""
    if edge.startswith("implied:"):
        parts = [x for x in edge[len("implied:"):].split("->") if ":" in x]
        if side.get("side") == "agent" and parts:
            return parts[0] if name == "from" else parts[-1]
        return None
    if ends and ":" in edge:
        pid, eid = edge.split(":", 1)
        hit = ends.get((pid, eid.split("#", 1)[0]))
        if hit:
            node = hit[0] if name == "from" else hit[1]
            return "%s:%s" % (pid, node) if node else None
    return None


def _field_keys(f, b, res, ends=None):
    """(grant ids the field is keyed on, reading note or None). The key is explicit: the field's own `grant`, or
    the write grant(s) of the agent step it leaves from; never a system name. Where they disagree the most explicit
    form is read (the field's own `grant`, then a grant id the boundary names, then the step's grants) and the note
    says so; a step drawn twice is read from its first drawing. The field always keeps a key when one is named."""
    step = _side_step_ref(b, "from", ends) if b and wg.agent_side(b) == "from" else None
    step_grants = res.step_grants(step) if step else []
    side_ids = [r for r in (((b or {}).get(n) or {}).get("ref") for n in ("to", "from"))
                if isinstance(r, str) and wg.GRANT_ID.match(r)]
    own = f.get("grant") if isinstance(f.get("grant"), str) and f.get("grant").strip() else None
    if own is not None:
        if step_grants and own not in step_grants:
            return [own], "field grant %s read over its step %s's %s (the field names it)" % (
                own, step, ", ".join(step_grants))
        if any(r != own for r in side_ids):
            return [own], "field grant %s read over the boundary's %s (the field names it)" % (
                own, ", ".join(side_ids))
        return [own], None
    if side_ids and step_grants and not set(side_ids) <= set(step_grants):
        return list(side_ids), "the boundary's %s read over its step %s's %s (the boundary names it)" % (
            ", ".join(side_ids), step, ", ".join(step_grants))
    return list(step_grants), None


def _field_grants(f, b, res, ends=None):
    """The grant ids a field is keyed on; see _field_keys."""
    return _field_keys(f, b, res, ends)[0]


def _writers(res, g):
    """Qualified ids of the person steps that write under grant g: the nodes its Steps cell names, directly or
    through `future.from`, each a resolved, unambiguous person step (an agent node, a system surface or a step drawn
    twice with different content never counts)."""
    # every row the tables give g (the person step a row names writes under it, whichever row a step reads)
    refs = set(x for row in (res.conflicts.get(g) or [res.grants.get(g) or {}])
               for x in wg.steps_refs(row.get("Steps") or "") if x and x != "new")
    out = set()
    for (p, nid), n in res.nodes.items():
        fut = n.get("future") if isinstance(n.get("future"), dict) else {}
        if not res.is_person_step("%s:%s" % (p, nid)):
            continue
        if nid in refs or "%s:%s" % (p, nid) in refs or refs & set(wg._as_list(fut.get("from"))):
            out.add("%s:%s" % (p, nid))
    return out


def _kind_allows(kind, direction, person_dest=False):
    """False only when the field would have the agent act beyond the grant's authority: an agent `write` under a
    grant only a person writes, or under a draft into a system (a mechanical write where only a draft is allowed).
    Any other kind mismatch (a `write` field drawn for a hand-off to the person, say) is a drawing detail, read by the
    grant's kind."""
    if direction != "write":
        return True
    if kind == "person_write":
        return False
    return not (kind == "draft" and not person_dest)


def _reaches(side, step, g, kind, res, titles, src=None):
    """True when a boundary side is grant g's own destination for its write kind. A side named by the grant's own
    id reaches its destination whatever side type the boundary carries (the side type is a drawing detail, see
    _side_mismatch); a grant id no row holds reaches nothing."""
    sd, ref = side.get("side"), side.get("ref")
    if isinstance(ref, str) and ref == g:
        return res.resolve(g)[0].get("type") == wg.GRANT
    # src: the agent step the field leaves from; the grant row it reads is the shared reading for that step
    system = res.grant_system(g, src)
    person_dest = res.person_destination(g, src)
    if kind == "person_write":
        return sd == "person" and step is not None and step in _writers(res, g)
    if kind == "draft" and person_dest:
        return sd == "person"
    if sd not in ("system", "external", "agent") or not isinstance(ref, str) or not system:
        return False
    if sd == "agent" and kind != "own_file":
        return False
    key = wg.system_key(system)
    return wg.system_key(titles.get(ref, ref)) == key or wg.system_key(res.system_of(ref) or "") == key


FIELD_KINDS = {"write": "write", "draft": "draft", "person_write": "person_write"}


def _side_mismatch(b, g, kind):
    """A drawing hint when a boundary named by grant g's id carries a side type its kind does not route to."""
    to = (b or {}).get("to") or {}
    info = wg.WRITE_KINDS.get(kind)
    if to.get("ref") != g or info is None or any(to.get("side") in sides for sides, _ in info["routes"]):
        return None
    return "boundary %s names %s on a %s side; a %s grant goes to %s (read as reaching it)" % (
        (b or {}).get("id"), g, to.get("side"), kind.replace("_", " "),
        " or ".join(sorted(set(x for sides, _ in info["routes"] for x in sides))))


def grant_parity(run_dir):
    """(problems, open obligations); see grant_parity_full for the readings taken."""
    problems, obligations, _ = grant_parity_full(run_dir)
    return problems, obligations


def grant_parity_full(run_dir):
    """(problems, open obligations, reading notes). Parity keys on the grant id (the field's `grant`, or the write grant of the
    agent step the field leaves from) and is satisfied per write kind (wg.WRITE_KINDS): an allowed field kind, from
    the agent, on a boundary reaching the grant's own destination. A grant whose destination no boundary touches at
    all is a stamped open obligation, not a failure; a boundary that reaches it with the field missing or wrong is a
    recorded parity problem. A field keyed on a grant whose destination it misses is recorded too. Where the keys
    disagree, the most explicit is read (see _field_keys); a grant whose row gives no readable kind is read by its
    fields' kinds; a boundary named by the grant's own id reaches it whatever its side type. Each such reading is a
    note, never a finding."""
    agent = _agent(run_dir)
    fields, boundaries = _fields(run_dir), _boundaries(run_dir)
    proposed = _grant_systems(agent)
    grants = dict(proposed)
    for part in agent.get("parts", []):
        for g in part.get("grants", []):
            grants.setdefault(g, None)
    graphs, rows = _run_graphs_and_rows(run_dir)
    res = wg.Resolver(graphs, rows, dict((g, s) for g, s in proposed.items() if isinstance(s, str)))
    ends = _edge_ends(graphs)
    titles = _system_titles(run_dir)
    problems, obligations, notes = [], [], [dict(n) for n in res.notes]
    keyed, known = {}, set(grants)
    for f in fields:
        b = boundaries.get(f.get("boundary_id"))
        mine, reading = _field_keys(f, b, res, ends)
        where = "stages/fields/fields.json field %s" % f.get("id")
        if reading:
            notes.append({"code": "grant-key-reading", "where": where, "detail": reading, "note": True})
        for g in mine:
            keyed.setdefault(g, []).append((f, b))
        if f.get("direction") not in ("write", "draft", "person_write"):
            continue
        if not any(g in known for g in mine):
            problems.append(_problem("write-field-without-grant", where,
                                     "the field is keyed on no grant of the agent (no field grant, and its step "
                                     "carries none); a system name is never taken as the key"))
            continue
        hits, outside = [], []
        src = _side_step_ref(b, "from", ends) if b and wg.agent_side(b) == "from" else None
        for g in mine:
            # a grant whose row gives no readable kind is read by the field's own kind
            kind = res.kind(g, src) or FIELD_KINDS.get(f.get("direction"))
            to = (b or {}).get("to") or {}
            if kind and _reaches(to, _side_step_ref(b, "to", ends), g, kind, res, titles, src):
                if not _kind_allows(kind, f.get("direction"), res.person_destination(g, src)):
                    outside.append((g, kind))
                    continue
                hits.append(g)
                hint = _side_mismatch(b, g, kind)
                if hint:
                    notes.append({"code": "boundary-side-reading", "where": where, "detail": hint, "note": True})
        if outside and not hits:
            # kept: the agent would act beyond the authority the grant gives (e.g. writing where only a person may)
            problems.append(_problem("field-outside-grant-authority", where, "a %s field keyed on %s, which grants "
                                     "%s; the agent may not make this write" % (f.get("direction"), ", ".join(
                                         g for g, _ in outside), ", ".join(sorted(set(
                                             k.replace("_", " ") for _, k in outside))))))
            continue
        if b and wg.agent_side(b) == "from" and not hits:
            to = b.get("to") or {}
            problems.append(_problem("field-grant-destination-mismatch", where,
                                     "keyed on %s, but its boundary reaches %s %s, none of their destinations"
                                     % (", ".join(mine), to.get("side"), titles.get(to.get("ref"), to.get("ref")))))
    for g in sorted(grants, key=str):
        where = "agent.yaml grant %s" % g
        if g not in res.grants:
            problems.append(_problem("grant-without-write-field", where,
                                     "grant has no proposed_writes entry or Write grants row naming its system"))
            continue
        kind = res.kind(g)
        if kind is None:
            # no readable kind: the keyed fields' own kinds decide (the most common; a write when none is keyed)
            fk = [FIELD_KINDS[f.get("direction")] for f, _ in keyed.get(g, []) if f.get("direction") in FIELD_KINDS]
            kind = max(sorted(set(fk)), key=fk.count) if fk else "write"
            notes.append({"code": "grant-kind-reading", "where": where, "note": True,
                          "detail": "the Write grants row gives no readable write kind; read as a %s from its "
                                    "fields" % kind.replace("_", " ")})
        good = []
        for f, b in keyed.get(g, []):
            if not (b and wg.agent_side(b) == "from"):
                continue
            src = _side_step_ref(b, "from", ends)
            fk = res.kind(g, src) or kind
            if _by_id_or_route(fk, f, b, g, res.person_destination(g, src)) and _reaches(b.get("to") or {}, _side_step_ref(b, "to", ends), g, fk,
                                                         res, titles, src):
                good.append(f)
        if good:
            continue
        # does any boundary reach the grant's destination at all, keyed or not?
        touched = False
        for b in boundaries.values():
            if not isinstance(b, dict):
                continue
            for name in ("to", "from"):
                other = "from" if name == "to" else "to"
                side = b.get(name) or {}
                # a person destination counts only across from the agent (else every person boundary would)
                if side.get("side") == "person" and (b.get(other) or {}).get("side") != "agent":
                    continue
                if _reaches(side, _side_step_ref(b, name, ends), g, kind, res, titles):
                    touched = True
            if not touched and kind == "person_write" and not _writers(res, g) and \
                    "person" in [(b.get(n) or {}).get("side") for n in ("from", "to")] and wg.agent_side(b):
                touched = True   # the writing person step is not known: a person boundary counts, never ignored
            if touched:
                break
        system = res.grant_system(g)
        dest = "system %s" % system if system else "destination"
        if touched:
            problems.append(_problem("grant-without-write-field", where,
                                     "a boundary reaches the grant's %s but no field keyed on %s satisfies a %s "
                                     "grant (%s)" % (dest, g, kind.replace("_", " "),
                                                     wg.WRITE_KINDS[kind]["satisfied_by"])))
        else:
            obligations.append({"id": "OB-%s" % g, "grant": g, "evidence_state": "needs_more_information",
                                "reason": "no boundary in the design touches the grant's %s; the write stays "
                                          "proposed until the design draws it" % dest,
                                "source": "coherence_gate"})
    return problems, obligations, notes


def _by_id_or_route(kind, f, b, g, person_dest=False):
    """A field can carry grant g: its kind and side type are a route the grant's kind allows, or its boundary is
    named by g's own id (the side type is then a drawing detail) with a field kind the grant's kind allows."""
    to = b.get("to") or {}
    if to.get("ref") == g:
        return wg.data_direction(f.get("direction")) == wg.FROM_AGENT and _kind_allows(
            kind, f.get("direction"), person_dest)
    return wg.satisfies(kind, f.get("direction"), to.get("side"))


def check_grant_parity(run_dir):
    return grant_parity(run_dir)[0]


def _diagram_data(run_dir):
    if not os.path.isfile(DIAGRAM_PATH):
        raise Unchecked("diagram module absent: %s" % DIAGRAM_PATH)
    try:
        spec = importlib.util.spec_from_file_location("ad_diagram_for_gate", DIAGRAM_PATH)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        data = mod.diagram_data(run_dir)
    except Exception as e:
        raise Unchecked("diagram_data could not run: %s: %s" % (type(e).__name__, e))
    if not isinstance(data, dict) or any(not isinstance(data.get(k), list)
                                         for k in ("parts", "person_handovers", "writes")):
        raise Unchecked("diagram_data returned data without parts, person_handovers and writes")
    return data


def _render_res(run_dir, grants):
    """The resolver for one render-faithfulness call, built fresh from the run's files each time (never cached: a
    repeated check after the run's files change must read them again)."""
    graphs, rows = _run_graphs_and_rows(run_dir)
    return wg.Resolver(graphs, rows, dict((g, s) for g, s in grants.items() if isinstance(s, str)))


def check_render_faithfulness(run_dir):
    data = _diagram_data(run_dir)
    agent = _agent(run_dir)
    fields, boundaries = _fields(run_dir), _boundaries(run_dir)
    grants = _grant_systems(agent)
    drawn_grants = set(w.get("grant") for w in data["writes"] if isinstance(w, dict))
    drawn_handovers = set(h.get("boundary_id") for h in data["person_handovers"] if isinstance(h, dict))
    titles = _system_titles(run_dir)
    res = _render_res(run_dir, grants)
    ends = _edge_ends(_run_graphs_and_rows(run_dir)[0])
    problems = []
    for f in fields:
        d = f.get("direction")
        if d in OUTBOUND_WRITES:
            system = _field_system(f, boundaries, titles)
            # the grants the field is keyed on (its own `grant`, or its step's write grant); never a system name
            covering = set(_field_grants(f, boundaries.get(f.get("boundary_id")), res, ends))
            if not covering & drawn_grants:
                problems.append(_problem("write-not-drawn", "stages/fields/fields.json field %s" % f.get("id"),
                                         "no diagram writes[] entry carries a grant for system %s" % system))
        elif d == "back_from_person" and f.get("boundary_id") not in drawn_handovers:
            problems.append(_problem("hand-back-not-drawn", "stages/fields/fields.json field %s" % f.get("id"),
                                     "no diagram person_handovers[] entry carries boundary %s" % f.get("boundary_id")))
    return problems


CROSS_FUNCS = {
    "gate_seating": check_gate_seating,
    "open_item_kind": check_open_item_kind,
    "grant_parity": check_grant_parity,
    "render_faithfulness": check_render_faithfulness,
}


def run_cross_check(entry, run_dir, obligations=None):
    try:
        if entry["name"] == "grant_parity":
            problems, obs, notes = grant_parity_full(run_dir)
            if obligations is not None:
                obligations.extend(obs)
            if notes and not problems:
                return PASSED, "%d reading(s) recorded: %s" % (len(notes), "; ".join(
                    n["detail"] for n in notes[:3]) + ("; ..." if len(notes) > 3 else "")), []
        else:
            problems = CROSS_FUNCS[entry["name"]](run_dir)
    except Unchecked as e:
        return UNCHECKED, str(e), []
    except Exception as e:
        return UNCHECKED, "crashed: %s: %s" % (type(e).__name__, e), []
    return (FAILED if problems else PASSED), "", problems


# ---------------------------------------------------------------- gate

def _row(entry, kind, state, reason, problems):
    return {"name": entry["name"], "kind": kind, "stage": entry["stage"], "state": state, "reason": reason,
            "problems": problems, "agent_fallback": entry["fallback"]}


def overall(rows):
    states = [r["state"] for r in rows]
    if FAILED in states:
        return FAILED
    if UNCHECKED in states:
        return UNCHECKED
    return PASSED


def _routes(run_dir):
    d = _gate_dir(run_dir)
    if not os.path.isdir(d):
        return []
    return sorted(n for n in os.listdir(d) if n.startswith("route-") and n.endswith(".json"))


def _open_item(row, n, problem):
    unknown = problem["detail"] if problem else row["reason"]
    where = problem["where"] if problem else row["name"]
    return {
        "id": "GATE-%d" % n,
        "class": "needs_more_information",
        "kind": "escalation",
        "source": "gate residual",
        "unknown": "%s %s at %s: %s" % (row["name"], row["state"], where, unknown or "no detail"),
        "why_it_matters": "The coherence gate did not pass after one routed fix; the design is not coherent across stages until this is settled.",
        "agent_meanwhile": "The agent stays read-only and recommend-only for anything this item touches.",
        "who_can_answer": "The owner of the %s stage" % row["stage"],
        "closure_route": [],
        "resolution_evidence": "A rerun of the coherence gate in which %s passes." % row["name"],
        "breaks_if_wrong": "Downstream pages and the build hand-off would describe an inconsistent design.",
        "dependent_rules": [],
        "group": {"id": "gate-%s" % row["stage"], "cheapest_item": "GATE-%d" % n},
        "reader_verdict": None,
    }


def _gather(run_dir, obligations=None):
    rows = []
    for e in STAGE_CHECKS:
        rows.append(_row(e, "stage", *_run_stage_check(e, run_dir)))
    for e in CROSS_CHECKS:
        rows.append(_row(e, "cross-stage", *run_cross_check(e, run_dir, obligations)))
    return rows


def cmd_run(run_dir):
    obligations = []
    rows = _gather(run_dir, obligations)
    state = overall(rows)
    gd = _gate_dir(run_dir)
    routing = {"action": "none", "routes": [], "open_items": []}
    bad = [r for r in rows if r["state"] != PASSED]
    if bad:
        existing = _routes(run_dir)
        open_path = os.path.join(gd, "open-items.json")
        if not existing:
            by_stage = {}
            for r in bad:
                by_stage.setdefault(r["stage"], []).append(
                    {"check": r["name"], "state": r["state"], "reason": r["reason"], "problems": r["problems"]})
            for stage in sorted(by_stage):
                name = "route-%s.json" % stage
                _write(os.path.join(gd, name), {"stage": stage, "failures": by_stage[stage]})
                routing["routes"].append(name)
            routing["action"] = "routed"
        elif not os.path.exists(open_path):
            items, n = [], 0
            for r in bad:
                for p in (r["problems"] or [None]):
                    n += 1
                    items.append(_open_item(r, n, p))
            _write(open_path, {"items": items})
            routing["action"] = "open_items"
            routing["open_items"] = [i["id"] for i in items]
        else:
            routing["action"] = "route_refused"
    problems = []
    for r in bad:
        for p in r["problems"]:
            problems.append(_problem(p["defect"], "%s: %s" % (r["name"], p["where"]), p["detail"]))
        if r["state"] == UNCHECKED:
            problems.append(_problem("unchecked", r["name"], r["reason"]))
    if routing["action"] == "route_refused":
        problems.append(_problem("route-refused", "stages/coherence-gate",
                                 "a route and open items already exist; the gate does not route a third time"))
    report = {
        "check": "coherence_gate", "path": GATE_PATH, "run": "stages/coherence-gate/report.json",
        "exit_code": 0 if state == PASSED else 1, "result": "pass" if state == PASSED else "fail",
        "problems": problems,
        "counts": {"passed": sum(r["state"] == PASSED for r in rows),
                   "failed": sum(r["state"] == FAILED for r in rows),
                   "unchecked": sum(r["state"] == UNCHECKED for r in rows)},
        "agent_fallback": "If coherence_gate.py cannot run, run each check below by hand or follow its own fallback, "
                          "and count any check you could not run as unchecked.",
        "state": state, "checks": rows, "routing": routing,
        "open_obligations": obligations,
    }
    _write(os.path.join(gd, "report.json"), report)
    for r in rows:
        print("%-22s %-10s %s" % (r["name"], r["state"], r["reason"]))
        print("    fallback: %s" % r["agent_fallback"])
    print("overall: %s" % state)
    if routing["action"] == "routed":
        print("routed to: %s" % ", ".join(routing["routes"]))
    elif routing["action"] == "open_items":
        print("open items: %s" % ", ".join(routing["open_items"]))
    elif routing["action"] == "route_refused":
        print("route refused: already routed once and open items written")
    return 0 if state == PASSED else 1


def _newest(folder):
    newest = None
    for root, _, files in os.walk(folder):
        for n in files:
            t = os.path.getmtime(os.path.join(root, n))
            newest = t if newest is None or t > newest else newest
    return newest


def cmd_status(run_dir):
    rows = []
    for e in STAGE_CHECKS:
        path = os.path.join(run_dir, "checks", e["name"] + ".json")
        state, reason, report = judge_report(path)
        if os.path.isfile(path):
            newest = _newest(os.path.join(run_dir, "stages", e["stage"]))
            if newest is not None and os.path.getmtime(path) < newest:
                state, reason = UNCHECKED, "report older than the newest file in stages/%s" % e["stage"]
        rows.append(_row(e, "stage", state, reason, []))
    for e in CROSS_CHECKS:
        rows.append(_row(e, "cross-stage", *run_cross_check(e, run_dir)))
    for r in rows:
        print("%-22s %-10s %s" % (r["name"], r["state"], r["reason"]))
    state = overall(rows)
    print("overall: %s" % state)
    return 0 if state == PASSED else 1


USAGE = "usage: coherence_gate.py run|status --run RUN_DIR"


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) != 3 or argv[0] not in ("run", "status") or argv[1] != "--run":
        print(USAGE, file=sys.stderr)
        return 2
    run_dir = os.path.abspath(argv[2])
    if not os.path.isdir(run_dir) or not os.access(run_dir, os.R_OK | os.W_OK | os.X_OK):
        print("unreadable run folder: %s" % argv[2], file=sys.stderr)
        return 2
    return cmd_run(run_dir) if argv[0] == "run" else cmd_status(run_dir)


if __name__ == "__main__":
    sys.exit(main())
