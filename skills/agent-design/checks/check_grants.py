#!/usr/bin/env python3
"""Write-grant checks: the 0399 rules R1 to R6 on the Write grants table, grant direction, and writes.

Usage: check_grants.py --run RUN [--out REPORT.json]

Reads `RUN/stages/intake/intake.json`, the upstream files it points to (each process's `transformation_map` and
`future_state`, and `observations.snapshot_intake` for the key-control values under `scope.key_control`), and
`RUN/stages/architecture/agent.yaml` (JSON syntax). Defects:
  R2, R3, R4, R5-enum, R5-duplicate-id, R5-malformed-id, R6   the 0399 table rules (Recipient column added; R1
                          dropped and R2 narrowed to control steps by run 0503, R3's Step-up evidence and R3b are notes)
  key-control-mismatch    a row's Key control differs from the snapshot's value for that process
  person-grant-held       a person-written grant is held by a part
  agent-grant-unheld      an agent-written grant is held by no part
  grant-step-not-owned    a step carrying a held grant is owned by none of the parts holding it, or a holder owns
                          no step carrying the grant (each holder is checked on its own)
  grant-step-missing      no future-state node carries the grant in `write_grant`, or Steps names an unknown id
  grant-system-undrawn    the grant's System is drawn by no future-state lane or node
  proposed-write-mismatch a `proposed_writes` entry names an unknown grant or a step outside the grant's steps
  write-ungranted         an agent step writes into a system and no grant it carries covers that system
  exception-scope         the same, on a step whose only grants carry an exception reason
  write-grant-unknown     a future-state node's `write_grant` names no row of any Write grants table (fail-safe:
                          the recorded write is never dropped silently)
  write-before-confirm    an agent mechanical write into a system of record has no person confirmation of its own,
                          or is reachable from a start avoiding its own confirmations (the every-path rule of PR #43:
                          its own confirmations are found walking back from the write through agent-lane nodes,
                          tier 1, and only when that finds none, through any node, tier 2)
  handoff-write-ungranted a set's hand-off between two processes (an edge whose kind or label is exactly `handoff`
                          or a write marker, from an agent step of one process to a step of another) is covered
                          by no grant the sending step
                          carries that an agent writes and the sending step's owning part holds
  grant-fields-disagree   a future-state node's `write_grant` and `write_grants` name different grants (both read)
  grant-reading-invalid   a recorded reading names no row or column, or carries no reason
  input-invalid           an input parses but has the wrong shape

The future-state rules (grant-step-missing, grant-system-undrawn, write-grant-unknown, write-ungranted,
exception-scope, write-before-confirm, grant-fields-disagree) are the plugin's one shared set,
`KNOWLEDGE_ROOT/checks/_write_grants.py`; Future State Design's writes check runs the same module. Both lane shapes
are read: a lanes table, or the `lane` named on each node (`Agent` / `Agent: <name>`, `Analyst`, `System`).

Upstream problems never fail this check. A defect found on the upstream files alone (the table rules R1 to R6,
key-control-mismatch, and the future-state rules) is an upstream problem: the run continues, and the report lists it
under `upstream_recorded` when a reading answers it, else under `upstream_unresolved` (the status table and "What we
still need to know" carry it). A reading answers it when it names the problem's grant and lists its defect in `rules`
(see _write_grants.py for the reading's fields); readings come from the future state's `grant_readings` (recorded by
Future State Design) and from agent.yaml's `upstream_readings` (recorded by the architecture stage). A reading with
`"status": "unresolved"` keeps its problems under `upstream_unresolved`. Only the stage's own defects fail: grants held
or owned wrongly by the parts, proposed writes, hand-offs, an invalid agent.yaml reading, and input-invalid.
Writes are collected from recorded v0.1 field values only: a node's `writes` and `write_grant`, `type:
write_interaction`, every agent-to-system-of-record edge except one whose `kind` is exactly `read_access` (a read;
no substring, no case folding, `label` does not count), and a system-to-agent edge only when its `kind` or `label`
is exactly a write marker or the agent node records that system. A node's own write records count whatever its
edges say. A system named by workspace id (a real run's lane `system` and node `systems`, and sometimes a grant's
System cell) reads as that id's name in the graph's `workspaces` list; a `writes` entry whose `record` (or
`work_object`) is not in the "System: what" form, and is not itself the name of a system the graph draws, names
what is written, not where, so it counts as a write only on a step carrying no known grant (system "(no system
named)"). In a set, a system drawn anywhere in the shared graph counts as drawn. Steps are qualified
`<process_id>:<node_id>`; a
Steps cell's ids are split on commas and each id has its own backticks stripped ("`c3`, `c2`").
Exit 0 pass, 1 fail, 2 usage or unreadable input. Standard library only.
"""
import argparse
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(HERE)), "fast-mode-build", "driver"))
import knowledge_root  # noqa: E402  the one lookup for the plugin's knowledge folder

sys.path.insert(0, os.path.join(knowledge_root.find(), "checks"))
import _agent_steps
import _write_grants as W  # noqa: E402  the shared grant rules (Future State Design runs the same module)

CHECK = "check_grants"
CHECK_PATH = "skills/agent-design/checks/check_grants.py"
REPORT_REL = os.path.join("checks", "check_grants.json")
INTAKE_REL = os.path.join("stages", "intake", "intake.json")
AGENT_REL = os.path.join("stages", "architecture", "agent.yaml")

HEADER = W.HEADER
ENUMS = {
    "System of record": ("yes", "no", "unclear"),
    "Key control": ("Yes", "No", "unknown"),
    "Write kind": ("notice", "draft", "handoff", "own-file", "mechanical", "decision-bearing"),
    "Who writes": ("agent", "person"),
    "Route": ("api", "mcp", "screen", "n/a"),
    "Exception reason": ("none", "own-working-surface", "reversible-reviewed"),
}
RECIPIENTS = ("internal", "outside", "unclear")
GRANT_ID = W.GRANT_ID
STEP_UP_WORDS = ("reversib", "checkab", "key control", "inherited error")
PEOPLE_REASONS = ("control", "outside party", "client statement")  # the map's `## Why people stay` Reason values
HANDOFF_KINDS = W.WRITE_MARKERS + ("handoff",)  # exact edge kinds that carry a write across a process boundary
FSD_READINGS = "future-state grant_readings"
AD_READINGS = "agent.yaml upstream_readings"


class InputError(Exception):
    """A required input is absent or not readable JSON (exit 2)."""


class Invalid(Exception):
    """An input parses but has the wrong shape (exit 1, input-invalid)."""


split_row = W.split_row
_cell = W.cell
_qualify = W.qualify


def _read_text(run, rel):
    path = os.path.join(run, rel)
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except (OSError, UnicodeDecodeError) as e:
        raise InputError("cannot read %s: %s" % (rel, e.__class__.__name__))


def _read_json(run, rel):
    try:
        doc = json.loads(_read_text(run, rel))
    except ValueError:
        raise InputError("%s is not JSON" % rel)
    if not isinstance(doc, dict):
        raise Invalid("%s: the JSON root is not an object" % rel)
    return doc


def _rel(path):
    if not isinstance(path, str) or not path or path.startswith(("/", "~")) or "\\" in path \
            or ".." in path.split("/"):
        raise Invalid("intake path %r is not run-folder-relative" % (path,))
    return path


def read_table(text):
    """Rows (dicts keyed by HEADER) of the `### Write grants` table, or None when the section is absent."""
    try:
        return W.read_table(text)
    except W.TableError as e:
        raise Invalid(str(e))


def _has_step_up(text):
    t = (text or "").lower()
    return t not in ("", "n/a") and any(w in t for w in STEP_UP_WORDS + PEOPLE_REASONS)


def read_people_stay(text, notes=None):
    """{(process, step)} of the map's `## Why people stay` rows whose Reason is `control` (process "" when the row
    names none), or None when the map has no such section. Read leniently: any heading level, a section number
    and trailing words (W.WHY_HEADING, the knowledge readers' heading), columns by header in any order with emphasis
    marks ignored (`**Step**` reads as step), extra columns ignored, missing cells empty; each reading taken goes to
    notes. Fenced blocks and comments never count, as in W.read_table."""
    lines = text.splitlines()
    masked = W._md_blocks.mask(lines)
    lines = ["" if masked[i] else ln for i, ln in enumerate(lines)]
    start = next((i for i, ln in enumerate(lines) if W.WHY_HEADING.match(ln)), None)
    if start is None:
        return None
    level = len(re.match(r"^\s*(#+)", lines[start]).group(1))
    if notes is not None and lines[start].strip() != "## Why people stay":
        notes.append("NOTE Why people stay: heading %r read as the section" % lines[start].strip())
    rows = []
    for ln in lines[start + 1:]:
        m = re.match(r"^\s{0,3}(#+)\s", ln)
        if m and len(m.group(1)) <= level:
            break
        ln = ln.strip()
        if not ln.startswith("|"):
            if rows:
                break
            continue
        cells = [_cell(c) for c in split_row(ln)]
        if set("".join(cells)) <= set("-: "):
            continue
        rows.append(cells)
    out = set()
    if not rows:
        if notes is not None:
            notes.append("NOTE Why people stay: the section holds no table, read as listing no control step")
        return out
    cols = [re.sub(r"[*_]", "", c).strip().lower() for c in rows[0]]
    if "step" not in cols or "reason" not in cols:
        if notes is not None:
            notes.append("NOTE Why people stay: the header names no Step or Reason column, read as listing no "
                         "control step")
        return out
    for r in rows[1:]:
        r = r + [""] * (len(cols) - len(r))
        row = dict(zip(cols, r))
        reason = row.get("reason", "").strip().lower()
        if reason not in PEOPLE_REASONS and notes is not None:
            notes.append("NOTE Why people stay: Step %r has Reason %r, not one of %s" % (
                row.get("step", ""), row.get("reason", ""), ", ".join(PEOPLE_REASONS)))
        if reason != "control":
            continue
        for ref in W.steps_refs(row.get("step", "")):
            if ref:
                pid, _, sid = ref.rpartition(":")
                out.add((pid or row.get("process", "").strip(), sid))
    return out


def _is_control_step(steps_cell, control, pids, set_pids=None):
    """True when a grant's Steps cell names a step the map lists with Reason `control`. A `set:` step (a set-shared
    step) may be listed as `set:<id>`, without a process, or under a process whose drawing holds that very shared
    step (`set_pids`: {shared step id: the processes referencing the drawing that holds it}); never under a process
    drawn elsewhere."""
    for ref in W.steps_refs(steps_cell or ""):
        pid, _, sid = ref.rpartition(":")
        if pid == "set":
            cands = ["set"] + list((set_pids or {}).get(sid, ()))
        else:
            cands = [pid] if pid else list(pids) + [""]
        for p in cands:
            if (p, sid) in control or ("", sid) in control:
                return True
    return False


def rule_problems(rows, where, problems, notes, control=None, pids=(), set_pids=None):
    """R2 to R5 on one table's rows. `control` is read_people_stay's result for the table's map (None: no section);
    `pids` are the processes the table serves (an unqualified Steps id may name a step of any of them); `set_pids`
    maps each set-shared step id to the processes whose drawing holds it."""
    seen = set()
    for r in rows:
        gid = r["Grant"]
        at = "%s %s" % (where, gid)
        if not GRANT_ID.match(gid):
            problems.append(_pg(gid, "R5-malformed-id", at, "Grant id %r is not WG-<n>" % gid))
        if gid in seen:
            problems.append(_pg(gid, "R5-duplicate-id", at, "Grant id %s is used by more than one row" % gid))
        seen.add(gid)
        for col, allowed in ENUMS.items():
            if r[col] not in allowed:
                problems.append(_pg(gid, "R5-enum", at, "%s %r is not one of %s" % (col, r[col], ", ".join(allowed))))
        recip_ok = r["Recipient"] in RECIPIENTS if r["Write kind"] == "notice" else r["Recipient"] == "n/a"
        if not recip_ok:
            problems.append(_pg(gid, "R5-enum", at, "Recipient %r does not fit Write kind %r" % (r["Recipient"],
                                                                                          r["Write kind"])))
        sor = r["System of record"] in ("yes", "unclear")
        agent = r["Who writes"] == "agent"
        exc = r["Exception reason"] != "none"
        needs = False
        if control is not None:
            # exceptions are descriptive only: they never waive an agent write on a control step
            if sor and agent and _is_control_step(r["Steps"], control, pids, set_pids):
                needs = True
                problems.append(_pg(gid, "R2", at, "agent write to a system of record on a step Why people stay "
                                    "lists with Reason control"))
        elif sor and agent and r["Key control"] == "Yes" and r["Write kind"] == "decision-bearing":
            # a map without Why people stay is read leniently: the old R2 wording for these rows, as a note
            needs = True
            if not exc:
                notes.append("NOTE %s: R2 key-control agent write to a system of record has no exception (the map "
                             "has no Why people stay section)" % at)
        if r["Who writes"] == "person":
            if r["Next rung"].lower() in ("", "n/a"):
                problems.append(_pg(gid, "R3", at, "person row has no Next rung"))
            if not _has_step_up(r["Step-up evidence"]):
                notes.append("NOTE %s: R3 person row's Step-up evidence names no reversibility, checkability, key "
                             "control, inherited error, control, outside party or client statement" % at)
        if exc and r["Exception reason"] in ENUMS["Exception reason"]:
            if not _has_step_up(r["Step-up evidence"]):
                notes.append("NOTE %s: R3b exception row's Step-up evidence states no qualifying basis" % at)
            if not needs:
                notes.append("NOTE %s: Exception reason %s on a row that needs none" % (at, r["Exception reason"]))
        if r["Access"] != "Assumed":
            problems.append(_pg(gid, "R4", at, "Access %r is not Assumed" % r["Access"]))


# ---- the check ----

def _p(defect, where, detail):
    return {"defect": defect, "where": where, "detail": detail}


def _pg(gid, defect, where, detail):
    """A table problem on one grant (the grant lets a recorded reading answer it)."""
    return dict(_p(defect, where, detail), grants=[gid])


def _report(code, problems, counts, notes, recorded=(), unresolved=()):
    result = {0: "pass", 1: "fail", 2: "error"}[code]
    return {"check": CHECK, "path": CHECK_PATH, "run": ".", "exit_code": code, "result": result,
            "problems": problems, "counts": counts, "agent_fallback": "none: a script check",
            "notes": notes, "upstream_recorded": list(recorded), "upstream_unresolved": list(unresolved)}


def _expected_kc(value):
    return "Yes" if value is True else "No" if value is False else "unknown"


def _from_shared(p):
    return {"defect": p["rule"], "where": p["where"], "detail": p["detail"], "grants": p.get("grants", [])}


class ContextGraph(W.Graph):
    """A drawing's full graph, for confirmation evidence. A qualified Steps id resolves by the node it names in the
    run's file-aware ownership reader (`nw-ord:p1` on a set drawing is that drawing's p1 when the reader places it
    there), so a confirmation spelled with another process's prefix is still found on the drawing that holds it."""

    def __init__(self, pid, doc, ownership):
        super().__init__(pid, doc)
        self.ownership = ownership

    def resolve(self, ref):
        if ":" in ref:
            n = self.ownership.node_at(ref)
            local = ref.split(":", 1)[1]
            if n is not None and self.nodes.get(local) == n:
                return [local]
        return super().resolve(ref)


class ScopedGraph(W.Graph):
    """Evaluate each drawing once, retaining the full graph for confirmation paths.

    Ownership comes from the same file-aware reader as architecture. Context nodes
    can establish a confirmation or draw a system without becoming grant carriers
    in every process that happens to reference the file.
    """

    def __init__(self, pid, doc, ownership):
        super().__init__(pid, doc)
        self.context = ContextGraph(pid, doc, ownership)
        self.ownership = ownership
        self.nodes = {nid: n for nid, n in self.nodes.items()
                      if ownership.node_at(W.qualify(pid, nid)) == n
                      and (W.qualify(pid, nid) not in ownership.owned
                           or ownership.owned[W.qualify(pid, nid)]["home"] == W.qualify(pid, nid))}
        self.from_index = {}
        for nid, n in self.nodes.items():
            future = n.get("future") if isinstance(n.get("future"), dict) else {}
            for ref in W._as_list(future.get("from")):
                self.from_index.setdefault(ref, []).append(nid)

    def resolve(self, ref):
        if ":" in ref:
            info = self.ownership.owned.get(ref)
            if info and info["home"].split(":", 1)[0] == self.pid:
                ref = info["home"]
        return super().resolve(ref)

    def drawn(self):
        return self.context.drawn()

    def own_confirmations(self, write):
        return self.context.own_confirmations(write)

    def reachable_avoiding(self, avoid):
        return self.context.reachable_avoiding(avoid)


def _check(run):
    problems, notes = [], []
    intake = _read_json(run, INTAKE_REL)
    procs = intake.get("processes")
    obs = intake.get("observations")
    if not isinstance(procs, list) or not procs or not isinstance(obs, dict):
        raise Invalid("intake.json has no processes list or observations object")
    snap = _read_json(run, _rel(obs.get("snapshot_intake")))
    kc_values = (snap.get("scope") or {}).get("key_control") or {}
    agent = _read_json(run, AGENT_REL)
    parts = agent.get("parts")
    header = agent.get("header")
    if not isinstance(parts, list) or not isinstance(header, dict):
        raise Invalid("agent.yaml has no parts list or header object")
    held, owned = {}, {}
    for p in parts:
        if not isinstance(p, dict) or not isinstance(p.get("id"), str):
            raise Invalid("agent.yaml: a part has no id")
        for gid in p.get("grants") or []:
            held.setdefault(gid, []).append(p["id"])
        owned[p["id"]] = set(p.get("owns_steps") or [])
    ad_readings = agent.get("upstream_readings")

    grants, steps_of, graphs, tables, missing_table, writes = {}, {}, [], 0, False, 0
    upstream, valid_readings = [], []
    # A set's processes share one future-state graph and one Write grants table: each process gets its own nodes,
    # and a shared table's rows are checked under the processes whose nodes they name or that carry the grant.
    shared, texts, docs = {}, {}, {}
    pids = set(p["process_id"] for p in procs if isinstance(p, dict) and isinstance(p.get("process_id"), str))
    source_graphs = []
    for proc in procs:
        if not isinstance(proc, dict) or not isinstance(proc.get("process_id"), str):
            raise Invalid("intake.json: a process has no process_id")
        path = _rel(proc.get("future_state"))
        source_graphs.append((proc["process_id"], path, _read_json(run, path)))
    ownership = _agent_steps.run_ownership(source_graphs, agent.get("set_steps"))
    claims, _ = _agent_steps.expand_claims(
        ownership, [(p["id"], p.get("owns_steps") or []) for p in parts])
    for ref, holders in claims.items():
        for holder in holders:
            owned[holder].add(ref)
    notes.extend(ownership.notes)
    cross, seen_fs = [], set()  # a set's edges between two processes, outside every process's own graph
    for proc in procs:
        if not isinstance(proc, dict) or not isinstance(proc.get("process_id"), str):
            raise Invalid("intake.json: a process has no process_id")
        pid = proc["process_id"]
        fs_rel = _rel(proc.get("future_state"))
        fs_doc = _read_json(run, fs_rel)
        try:
            g = ScopedGraph(pid, W.with_source(fs_doc, fs_rel), ownership)
        except W.ShapeError as e:
            raise Invalid(str(e))
        if len(procs) > 1 and fs_rel not in seen_fs:
            seen_fs.add(fs_rel)
            cross.extend(_cross_edges(fs_doc, pids))
        tm = _rel(proc.get("transformation_map"))
        if tm not in texts:
            texts[tm] = _read_text(run, tm)
        if read_table(texts[tm]) is None:
            # with no table there is nothing to compare grants or writes against: R6 alone is reported
            upstream.append(_p("R6", pid, "the transformation map has no Write grants table"))
            missing_table = True
            continue
        graphs.append(g)
        shared.setdefault(tm, []).append(g)
        docs.setdefault(tm, fs_doc)
    for tm, members in shared.items():
        seen_sources = set()
        for member in list(members):
            key = member.document.source_key
            if key in seen_sources:
                continue
            seen_sources.add(key)
            set_graph = ScopedGraph("set", member.document, ownership)
            if set_graph.nodes:
                # Carrier identity is set-owned; evidence still belongs to real
                # processes referencing this exact source drawing.
                set_graph.source_pids = tuple(dict.fromkeys(
                    pid for pid, path, _ in source_graphs if path == key))
                members.append(set_graph)
        rows = read_table(texts[tm])
        tables += sum(g.pid != "set" for g in members)
        table_upstream = []
        # the processes the table serves: a set graph stands for the real processes that reference its drawing
        table_pids = list(dict.fromkeys(pid for g in members for pid in getattr(g, "source_pids", (g.pid,))
                                        if pid != "set"))
        set_pids = {}   # each shared step id -> the processes referencing the drawing that holds it
        for g in members:
            if g.pid == "set":
                for sid in list(g.nodes) + list(g.from_index):
                    set_pids.setdefault(sid, []).extend(p for p in g.source_pids if p not in set_pids[sid])
        rule_problems(rows, members[0].pid, table_upstream, notes, read_people_stay(texts[tm], notes), table_pids,
                      set_pids)
        sources = [(FSD_READINGS, docs[tm].get("grant_readings")), (AD_READINGS, _for_table(ad_readings, rows, tm,
                                                                                         shared, texts))]
        res = W.evaluate(members, rows, sources)
        writes += res["writes"]
        valid_readings += res["readings"]
        for gid, r in res["grants"].items():
            grants.setdefault(gid, r)
        for gid, steps in res["steps_of"].items():
            expanded = set(steps)
            for ref in steps:
                expanded.update(ownership.owned.get(ref, {}).get("refs", []))
            steps_of.setdefault(gid, set()).update(expanded)
        for p in res["problems"]:
            if p["rule"] == "grant-reading-invalid":
                (problems if p["where"].startswith(AD_READINGS) else table_upstream).append(_from_shared(p))
            else:
                table_upstream.append(_from_shared(p))
        recorded_shared = [dict(_from_shared(p), reading=p["reading"]) for p in res["recorded"]]
        for r in rows:
            gid = r["Grant"]
            rgraphs = res["rgraphs"][gid]
            at = "%s %s" % (rgraphs[0].pid, gid)
            evidence_pids = dict.fromkeys(
                pid for g in rgraphs for pid in getattr(g, "source_pids", (g.pid,)))
            for pid in evidence_pids:
                exp = _expected_kc(kc_values.get(pid))
                if r["Key control"] in ENUMS["Key control"] and r["Key control"] != exp:
                    table_upstream.append(_pg(gid, "key-control-mismatch", "%s %s" % (pid, gid), "Key control %s, "
                                              "the snapshot gives %s" % (r["Key control"], exp)))
            holders = held.get(gid, [])
            if r["Who writes"] == "person" and holders:
                problems.append(_p("person-grant-held", at, "person-written grant held by %s" % ", ".join(holders)))
            if r["Who writes"] == "agent":
                if not holders:
                    problems.append(_p("agent-grant-unheld", at, "agent-written grant held by no part"))
                carriers = res["carriers"][gid]
                qcarriers = [_qualify(g.pid, nid) for g in rgraphs for nid in carriers[g.pid]]
                for q in qcarriers:
                    if not any(q in owned[h] for h in holders) and holders:
                        problems.append(_p("grant-step-not-owned", "%s %s" % (q.split(":", 1)[0], gid),
                                           "%s carries %s but %s does not own it" % (q, gid, ", ".join(holders))))
                for h in holders:
                    if qcarriers and not any(q in owned[h] for q in qcarriers):
                        problems.append(_p("grant-step-not-owned", at, "%s holds %s but owns no step carrying it "
                                           "(%s)" % (h, gid, ", ".join(qcarriers))))
        for p in table_upstream:
            r = W.match({"rule": p["defect"], "grants": p.get("grants", [])}, res["readings"])
            if r is None:
                upstream.append(p)
            else:
                recorded_shared.append(dict(p, reading=dict((k, r.get(k)) for k in ("grant", "column", "reason",
                                                                                    "status"))))
        upstream += recorded_shared
    for w in [] if missing_table else header.get("proposed_writes") or []:
        gid = w.get("grant") if isinstance(w, dict) else None
        if gid not in grants or w.get("step") not in steps_of.get(gid, set()):
            problems.append(_p("proposed-write-mismatch", str(gid), "proposed write %r names an unknown grant or "
                               "a step outside the grant's steps" % (w,)))
    if not missing_table:
        handoff_problems(cross, dict((g.pid, g) for g in graphs), grants, steps_of, held, owned, problems)
    for r in valid_readings:
        if r.get("column") == "Steps" and isinstance(r.get("read_as"), list):
            # a Steps reading replaced the cell before the rules ran: it is listed even when no problem is left
            upstream.append(dict(_p("grant-reading", r["grant"], "Steps read as %s, not as the table's %r: %s" % (
                ", ".join(r["read_as"]), r.get("table_says"), r["reason"])),
                reading=dict((k, r.get(k)) for k in ("grant", "column", "reason", "status"))))
    recorded = [_clean(p) for p in upstream if p.get("reading") and p["reading"].get("status") != "unresolved"]
    unresolved = [_clean(p) for p in upstream if not (p.get("reading") and p["reading"].get("status") != "unresolved")]
    problems = [_clean(p) for p in problems]
    counts = {"processes": len(procs), "tables": tables, "grants": len(grants), "writes": writes,
              "handoffs": len(cross), "problems": len(problems), "upstream_recorded": len(recorded),
              "upstream_unresolved": len(unresolved), "readings": len(valid_readings)}
    return problems, counts, notes, recorded, unresolved


def _for_table(value, rows, tm, shared, texts):
    """The agent.yaml readings for one table: those naming one of its grants, plus (on the first table only) those
    naming no grant of any table, so an invalid reading is reported once and a valid one never fails elsewhere."""
    if not isinstance(value, list):
        return value
    ids = set(r["Grant"] for r in rows)
    every = set()
    for other in shared:
        every |= set(r["Grant"] for r in read_table(texts[other]) or [])
    first = tm == next(iter(shared))
    named = lambda r: r.get("grant") if isinstance(r, dict) and isinstance(r.get("grant"), str) else None
    return [r for r in value if named(r) in ids or (first and named(r) not in every)]


def _clean(p):
    out = _p(p["defect"], p["where"], p["detail"])
    if p.get("reading"):
        out["reading"] = p["reading"]
    return out


def _cross_edges(doc, pids):
    """[(source pid, target pid, edge)]: edges of a shared graph whose two ends carry different process ids."""
    g = doc.get("graph", doc)
    owner = dict((n.get("id"), n.get("process_id")) for n in g.get("nodes") or [] if isinstance(n, dict))
    out = []
    for e in g.get("edges") or []:
        if isinstance(e, dict):
            a, b = owner.get(e.get("source")), owner.get(e.get("target"))
            if isinstance(a, str) and isinstance(b, str) and a in pids and b in pids and a != b:
                out.append((a, b, e))
    return out


def handoff_problems(cross, by_pid, grants, steps_of, held, owned, problems):
    """A hand-off from an agent step of one process into another is a write: some grant the sending step carries
    (write_grant or Steps), written by an agent, must be held by a part owning the sending step."""
    for spid, tpid, e in cross:
        g = by_pid.get(spid)
        src = g.nodes.get(e.get("source")) if g else None
        if not src or not g.is_agent(src) or (e.get("kind") not in HANDOFF_KINDS
                                              and e.get("label") not in HANDOFF_KINDS):
            continue
        q = _qualify(spid, src["id"])
        owners = [p for p, steps in owned.items() if q in steps]
        carried = set(x for x in W.grant_ids(src) if x in grants) | set(
            x for x in grants if q in steps_of.get(x, set()))
        ok = [x for x in carried if grants[x]["Who writes"] == "agent" and set(held.get(x, [])) & set(owners)]
        if not ok:
            problems.append(_p("handoff-write-ungranted", "%s -> %s:%s" % (q, tpid, e.get("target")),
                               "hand-off %s into process %s is covered by no agent grant the sending step carries "
                               "and its part holds (carried: %s)" % (e.get("id"), tpid,
                                                                     ", ".join(sorted(carried)) or "none")))


def check(run_dir):
    """The report for run_dir (never raises)."""
    try:
        problems, counts, notes, recorded, unresolved = _check(run_dir)
    except InputError as e:
        return _report(2, [_p("input-unreadable", "", str(e))], {}, [])
    except Invalid as e:
        return _report(1, [_p("input-invalid", "", str(e))], {}, [])
    return _report(1 if problems else 0, problems, counts, notes, recorded, unresolved)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Write-grant checks")
    ap.add_argument("--run", required=True)
    ap.add_argument("--out")
    args = ap.parse_args(argv)
    report = check(args.run)
    out = args.out or os.path.join(args.run, REPORT_REL)
    try:
        d = os.path.dirname(out)
        if d:
            os.makedirs(d, exist_ok=True)
        with open(out, "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=2)
    except OSError as e:
        sys.stderr.write("cannot write the report: %s\n" % e)
    for p in report["problems"]:
        print("%s %s: %s" % (p["defect"], p["where"], p["detail"]))
    for label, key in (("upstream, recorded", "upstream_recorded"), ("upstream, unresolved", "upstream_unresolved")):
        for p in report.get(key) or []:
            print("%s (%s) %s: %s" % (p["defect"], label, p["where"], p["detail"]))
    for n in report["notes"]:
        print(n)
    print(report["result"])
    return report["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
