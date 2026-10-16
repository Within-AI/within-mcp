#!/usr/bin/env python3
"""Future State Design run control: the run folder, its state file and event log, stage prompts and stage checks.

Usage (RUN is the run folder; every command appends to RUN/events.jsonl):
  fsd.py init      --run RUN --snapshot DIR          new run from a frozen Work Brain Snapshot folder (RUN must be absent or empty);
                                                     freeze.pin records the freeze block in state.json
  fsd.py direction --run RUN (--report FILE | --design-prompt FILE | --evidence FILE) [--solution ID] [--folder DIR]
                   [--allow-not-ready]               record the Agent Recommendations direction (before any stage starts):
                                                     --allow-not-ready suppresses the open question when not marked ready; direction is always recorded
                                                     the report by file name in DIR, else the pasted brief, else the evidence
                                                     file; report_source records which. Writes RUN/handover.json
  fsd.py next      --run RUN                         print the next stage key, or "done"; exit 3 if a finished stage's outputs changed
  fsd.py prompt    --run RUN --stage KEY             stage the references and write RUN/prompts/KEY.md; print its path
  fsd.py start     --run RUN --stage KEY [--rerun]   mark the stage running (refuses a done stage unless --rerun, missing inputs, or
                                                     drift reported by freeze.drift); appends the executor.started event
  fsd.py finish    --run RUN --stage KEY             run the stage's checks, record outputs and result; exit 1 if a check fails
  fsd.py repair    --run RUN --stage KEY             after a finish with failing checks: write RUN/prompts/KEY-repair.md for one repair
                                                     agent (only the checks repairable here), mark the stage running again; at
                                                     most one repair per stage. When no failing check is repairable here, the
                                                     repair is skipped and recorded (stage stays done, checks failing)
  fsd.py check     --run RUN --stage KEY             run the stage's checks only (no state change)
  fsd.py status    --run RUN [--builder]             the person's status (plain stage names, wall-clock minutes, one note);
                                                     --builder prints the stage table with checks and work counts
  fsd.py resume    --run RUN [--copied-from DIR]     record a resume in events.jsonl (run-resumed) with the copy-in check: every
                                                     file under DIR (the copy the run was brought in from) compared by sha256
                                                     with RUN, and the files that differ; exit 1 if any differ. Without
                                                     --copied-from the event records copy_check null (no copy on this host)

Stages, in order: set-map (only when the snapshot has 2+ processes and the direction carries no set map), current-state,
transformation-plan, future-state. Only this script writes inputs/set-map.json: at `direction` from the direction's set_map,
or at `finish --stage set-map` once that stage's checks pass; state.json records set_map {source, sha256}. The check set
comes from hooks_checks.for_stage, extra prompt lines from a3_refs.stage, the freeze block and drift from freeze.py.
Reference material is read only through knowledge.py. Stdlib only.

What the person sees: init and every finish print the person's status between two marker lines (plain stage names,
wall-clock minutes since the run started, one note; no check names, ids, exit codes or paths). Builder detail (check
counts, failing checks, renderer notes, progress lines, page notes) goes to RUN/builder/fsd-builder.log. The finish of
the last stage writes the run page RUN/outputs/future-state-design.html (page/run_page.py); a page failure is a
builder-log line and a recorded problem, never a failed finish.
"""
import argparse, glob, hashlib, json, os, re, shutil, subprocess, sys, time
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import knowledge, brief_values, direction as dirmod, check_schema
import freeze, hooks_checks, a3_refs
import md_blocks

STAGES = ["set-map", "current-state", "transformation-plan", "future-state", "ambition-audit"]
LABELS = {"set-map": "Set map", "current-state": "Current state Workflow", "transformation-plan": "Transformation map",
          "future-state": "Future state Workflow", "ambition-audit": "Ambition audit"}
DEPS = {"set-map": ["inputs/snapshot.json"], "current-state": ["inputs/snapshot.json"], "transformation-plan": ["stages/current-state"],
        "future-state": ["stages/current-state", "stages/transformation-plan"],
        "ambition-audit": ["stages/current-state", "stages/transformation-plan", "stages/future-state"]}
NEEDS = {"set-map": [], "current-state": ["set-map"], "transformation-plan": ["current-state"],
         "future-state": ["current-state", "transformation-plan"], "ambition-audit": ["future-state"]}
PRIMARY = {"set-map": "set-map/set-map.json", "current-state": "current-state/current-state.json",
           "transformation-plan": "transformation-plan/transformation-map.md", "future-state": "future-state/future-state.json",
           "ambition-audit": "ambition-audit/ambition-audit.md"}
TARGET_MIN = {"set-map": 4.0, "current-state": 5.5, "transformation-plan": 6.0, "future-state": 5.0,
              "ambition-audit": 5.0}   # Fast Mode's targets (driver/limits.py); set-map's and ambition-audit's are ours
# R-audit, R-rework: the audit runs after future-state; with one or more proposals the first pass is copied to PASS_1 and
# REWORK runs once more with the audit as an extra input, then the audit runs a second time. No third round.
AUDIT = "ambition-audit"
REWORK = ("transformation-plan", "future-state", AUDIT)
PASS_1 = "stages/pass-1"
PASS_1_AUDIT = PASS_1 + "/ambition-audit/ambition-audit.md"
NO_REFERENCE = ("set-map", AUDIT)   # stages with no worked reference: prompt stages none
SET_MAP = "inputs/set-map.json"   # the canonical set map; only this script writes it
REF_STEPS, SIZE_EXPONENT, SIZE_FACTOR_MAX = 25, 0.3, 2.0
COUNTED = {"current-state": "current-state/current-state.json", "future-state": "future-state/future-state.json"}
# P37 row 28. System rails (system_surface) and result boxes (system_result) are system-side, not work; any other
# System-lane type is a system-performed step and counts.
NOT_WORK = ("ai_use", "object_state", "context_neighbour", "message", "start_event", "end_event",
            "system_surface", "system_result")


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha(p):
    return hashlib.sha256(open(p, "rb").read()).hexdigest()


def load_state(run):
    with open(os.path.join(run, "state.json"), encoding="utf-8") as f:
        return json.load(f)


def save_state(run, st):
    tmp = os.path.join(run, "state.json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f, indent=2)
    os.replace(tmp, os.path.join(run, "state.json"))


def event(run, rec):
    rec = dict({"at": now(), "source": "fsd.py"}, **rec)
    with open(os.path.join(run, "events.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")


def tree_hashes(d):
    out = {}
    for dp, _, fs in os.walk(d):
        for f in fs:
            p = os.path.join(dp, f)
            out[os.path.relpath(p, d)] = sha(p)
    return dict(sorted(out.items()))


def steps_count(run):
    try:
        return len(json.load(open(os.path.join(run, "inputs", "snapshot.json")))["steps"])
    except Exception:
        return 0


def target(run, key):
    f = min(SIZE_FACTOR_MAX, max(1.0, (max(0, steps_count(run)) / float(REF_STEPS)) ** SIZE_EXPONENT))
    return round(TARGET_MIN[key] * f, 2)


def process_ids(run):
    try:
        return (json.load(open(os.path.join(run, "inputs", "snapshot.json"))).get("scope") or {}).get("process_ids") or []
    except Exception:
        return []


def renderer_command(run, key, kroot):
    """The diagram stages' render command for this run: the coherent A3 for 2+ snapshot processes, bands for one."""
    if len(process_ids(run)) >= 2:
        return f"python3 {kroot}/renderer/render_a3.py {key}.input.json . --stem {key} --budget 0"
    return f"python3 {kroot}/renderer/render_workflow.py {key}.input.json {key}.json {key}.svg"


def map_covers_snapshot(run, set_map):
    """True when a carried set map holds every snapshot process as a member (processes[]); a process kept only as a context
    neighbour of a scoped copy is not mapped."""
    if not isinstance(set_map, dict):
        return False
    named = {x.get("process_id") for x in set_map.get("processes") or [] if isinstance(x, dict)}
    return set(process_ids(run)) <= named


def wants_set_map_stage(run, direction):
    """Pins §1: the set-map stage runs when the snapshot has 2+ processes and the direction carries no set map that covers
    every snapshot process (a map scoped to fewer processes than the snapshot never stands in for the whole set)."""
    return len(process_ids(run)) >= 2 and not map_covers_snapshot(run, (direction or {}).get("set_map"))


# ---------------------------------------------------------------- the hand-over record

HANDOVER = "handover.json"


def snapshot_processes(run):
    """The snapshot's process keys. Zero processes or an unreadable process list is unreadable input: stop."""
    try:
        ids = (json.load(open(os.path.join(run, "inputs", "snapshot.json"), encoding="utf-8")).get("scope") or {}).get("process_ids")
    except (OSError, ValueError, AttributeError) as e:
        raise SystemExit("refused: the snapshot's process list cannot be read (%s)" % type(e).__name__)
    if not isinstance(ids, list) or not ids or not all(isinstance(x, str) and x for x in ids):
        raise SystemExit("refused: the snapshot names no readable processes")
    return list(ids)


BUILDER_LOG = "builder/fsd-builder.log"
CUSTOMER_START = "---- status for the person ----"
CUSTOMER_END = "---- end of status ----"
PLAIN = {"set-map": "How the processes connect", "current-state": "How the work runs today",
         "transformation-plan": "What changes", "future-state": "How the work would run", AUDIT: "Checking the design"}
RUN_PAGE = os.path.join(SKILL, "page", "run_page.py")


def builder(run, line):
    """One builder-log line (the builder view); never printed to the person."""
    p = os.path.join(run, BUILDER_LOG)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "a", encoding="utf-8") as f:
        f.write(f"{now()} {line}\n")


def _plain_status(status, started):
    status = str(status)
    if status == "stale":
        return "Awaiting rerun"
    if status == "pending":
        return "Not started"
    if status.startswith("failed"):
        return "Could not finish"
    if status == "done":
        return "Done"
    if status.startswith("done"):
        return "Done, with notes"
    return "In progress" if started else "Not started"


def wall_minutes(run, st, until=None):
    """Wall-clock minutes from the first event (else state.started_at) to now, or to the last event once every stage
    has finished; None when no start is recorded."""
    try:
        with open(os.path.join(run, "events.jsonl"), encoding="utf-8") as f:
            evs = [json.loads(l) for l in f if l.strip()]
    except (OSError, ValueError):
        evs = []
    stamps = []
    for e in evs:
        if isinstance(e, dict) and (e.get("backfill") or e.get("event") in ("run-resumed", "run-stopped")):
            continue   # bookkeeping after the work (a page backfill, a resume) is not run time
        try:
            t = datetime.fromisoformat(str(e.get("at")).replace("Z", "+00:00"))
        except (ValueError, TypeError, AttributeError):
            continue
        stamps.append(t if t.tzinfo else t.replace(tzinfo=timezone.utc))
    if stamps:
        start = stamps[0]
    else:
        try:
            start = datetime.fromisoformat(str(st.get("started_at")).replace("Z", "+00:00"))
        except (ValueError, TypeError):
            return None
    end = None
    keys = stage_keys(st)
    if stamps and keys and all(str(st["stages"][k]["status"]).startswith(("done", "failed")) for k in keys):
        end = stamps[-1]
    if end is None and until:
        try:
            end = datetime.fromisoformat(str(until).replace("Z", "+00:00"))
            end = end if end.tzinfo else end.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            end = None
    end = end or datetime.now(timezone.utc)
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    return max(0, int(round((end - start).total_seconds() / 60.0)))


PAGE_NAME = "future-state-design.html"
LIVE_STATE = {"Awaiting rerun": "pending", "Not started": "pending", "In progress": "running", "Done": "done", "Done, with notes": "done-notes",
              "Could not finish": "unavailable"}


def _shared(name):
    """The shared live page and status helper in the plugin's knowledge folder (knowledge/page/progressive/)."""
    import importlib.util
    path = os.path.join(knowledge.root(), "page", "progressive", name + ".py")
    spec = importlib.util.spec_from_file_location("fsd_shared_" + name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def page_log(run, line):
    """Page notes (renderer notes, live refreshes): RUN/builder/page.log, so the stage log keeps its stage lines."""
    p = os.path.join(run, "builder", "page.log")
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "a", encoding="utf-8") as f:
        f.write(f"{now()} {line}\n")


def page_path(run):
    return os.path.join(run, "outputs", PAGE_NAME)


def _rows(st):
    return [(PLAIN.get(k, LABELS.get(k, k)), _plain_status(st["stages"][k].get("status", "pending"),
                                                           st["stages"][k].get("attempts"))) for k in stage_keys(st)]


def _process_names(run):
    """Process key to name, from the set map and the snapshot's process details files; never raises."""
    names = {}
    try:
        for pid in (json.load(open(os.path.join(run, "inputs", "snapshot.json"), encoding="utf-8")).get("scope") or {}).get("process_ids") or []:
            if isinstance(pid, str) and pid:
                names[pid] = pid   # a key with no known name is removed from the note
    except Exception:   # noqa: BLE001
        pass
    try:
        smap = json.load(open(os.path.join(run, SET_MAP), encoding="utf-8"))
        for p in smap.get("processes") or []:
            if isinstance(p, dict) and isinstance(p.get("process_id"), str) and isinstance(p.get("title"), str):
                names[p["process_id"]] = p["title"].strip()
    except Exception:   # noqa: BLE001
        pass
    for path in glob.glob(os.path.join(run, "inputs", "get_process_*.json")):
        try:
            d = json.load(open(path, encoding="utf-8"))
        except Exception:   # noqa: BLE001
            continue
        d = d.get("process", d) if isinstance(d, dict) else {}
        pid = d.get("process_id") or d.get("id") or d.get("key") if isinstance(d, dict) else None
        nm = d.get("name") or d.get("title") if isinstance(d, dict) else None
        if isinstance(pid, str) and isinstance(nm, str) and nm.strip() and names.get(pid, pid) == pid:
            names[pid] = nm.strip()
    return names


def _plain_note(run, text):
    """A recorded note in the person's words: process keys become process names, other ids are removed."""
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("fsd_run_page_notes", RUN_PAGE)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        names = _process_names(run)
        return mod.Cleaner(set(names), {k: v for k, v in names.items() if v != k}).clean(text)
    except Exception:   # noqa: BLE001 - without the page cleaner, process keys are still taken out
        names = _process_names(run)
        for k, v in names.items():
            text = text.replace(k, v if v != k else "")
        return re.sub(r"\s+([,;.)])", r"\1", re.sub(r"\b[0-9a-f]{12}\b", "", text)).strip()


def _notes(st, run=None):
    """What the "Done, with notes" notes are: the run's own plain still-need-to-know items, ids replaced or removed."""
    out = []
    details = set(str(p.get("detail")) for p in st.get("problems") or [] if isinstance(p, dict))
    for item in st.get("what_we_still_need_to_know") or []:
        t = item.get("text") if isinstance(item, dict) else item
        if isinstance(t, str) and t in details:
            continue   # a problem recorded with no plain wording: its diagnostic text stays in the builder view
        if isinstance(t, str) and t.strip():
            t = _plain_note(run, t.strip()) if run else t.strip()
            if t:
                out.append(t)
    for k in stage_keys(st):
        s = st["stages"][k]
        if str(s.get("status", "")).startswith("done, checks failing"):
            out.append("%s finished with checks that did not all pass; the details are in the builder notes."
                       % PLAIN.get(k, LABELS.get(k, k)))
    return out


def _overrun(run, st):
    for k in stage_keys(st):
        s = st["stages"][k]
        if s.get("status") == "running" and s.get("started_at"):
            try:
                t0 = datetime.fromisoformat(str(s["started_at"]).replace("Z", "+00:00"))
                t0 = t0 if t0.tzinfo else t0.replace(tzinfo=timezone.utc)
                mins = (datetime.now(timezone.utc) - t0).total_seconds() / 60.0
            except (ValueError, TypeError):
                return None
            return _shared("status").overrun_line(PLAIN.get(k, LABELS.get(k, k)), mins, target(run, k))
    return None


def customer_view(run, st):
    """The person's status: one row per stage in plain words, the note line, what the notes are and the page line."""
    st = person_state(st)
    stopped = st.get("stopped") if isinstance(st.get("stopped"), dict) else None
    rows = [(label, "Stopped" if stopped and g == "In progress" else g) for label, g in _rows(st)]
    nxt = next((label for label, g in rows if not g.startswith("Done")), None)
    mins = wall_minutes(run, st, until=stopped.get("at") if stopped else None)
    took = "Start time not recorded" if mins is None else f"{mins} minute{'' if mins == 1 else 's'} since the run started"
    active = next((label for label, state in rows if state == "In progress"), None)
    note = (f"{took}. Now working on: {active}." if active else f"{took}. Next: {nxt}." if nxt else
            f"Finished: {took.replace('since the run started', 'from start to finish')}.")
    page = page_path(run)
    line = None
    if os.path.isfile(page):
        try:
            line = _shared("live").page_line(page, None, "live" if nxt and not stopped else "final")
        except Exception:   # noqa: BLE001 - the table never fails on the page line
            line = "Your page: %s" % page
    elif any(p.get("kind") == "run-page" for p in st.get("problems") or [] if isinstance(p, dict)):
        line = "Your page could not be written this time; the diagrams and map are in the run folder."
    try:
        overrun = None if stopped else _overrun(run, st)
    except Exception:   # noqa: BLE001
        overrun = None
    if stopped and nxt:
        note = f"{took.replace('since the run started', 'before it stopped')}. The run stopped before: {nxt}."
    return _shared("status").customer_block("Future State Design progress", rows, note, page_line=line,
                                            notes=_notes(st, run), overrun=overrun)


def show_customer(run, st, always=False):
    """Post the person's status at a boundary: only when their view changed (always at init)."""
    sh = _shared("status")
    text = sh.safe(lambda: customer_view(run, st), record=lambda l: builder(run, l))
    # the whole open list counts as a change, not only the notes the table shows
    seen = text + "\n" + json.dumps([st.get("what_we_still_need_to_know") or [],
                                      {k: st["stages"][k].get("outputs") for k in stage_keys(st)}], sort_keys=True, default=str)
    block = sh.post_if_changed(os.path.join(run, "builder"), "fsd-status", seen)
    block = text if (always or block is not None) else None
    if block is None:
        print(sh.UNCHANGED)
        return
    print(CUSTOMER_START)
    print(block)
    print(CUSTOMER_END)


def publish_page(run, st, mode="live", reason=None, backfill=False):
    """Publish the person's page through the shared live page: from init, at every stage start and finish, final at
    the end, ended on a stop. A render failure keeps the last good page and is a recorded problem; never raises."""
    rec = lambda l: page_log(run, l)   # noqa: E731
    failed = []
    pst = person_state(st)   # R-view: the page shows the map and future state only once the design is final

    def render():
        try:
            import importlib.util
            spec = importlib.util.spec_from_file_location("fsd_run_page", RUN_PAGE)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            text, notes = mod.render(run, pst)
        except Exception as e:   # noqa: BLE001 - the page never blocks the run
            failed.append(e)
            raise
        for n in notes or []:
            page_log(run, "run page: " + n)
        return text
    progress = [(label, LIVE_STATE.get(g, "pending")) for label, g in _rows(pst)]
    try:
        res = _shared("live").publish(page_path(run), render, progress, mode, os.path.join(run, "builder"),
                                      reason=reason, record=rec,
                                      restart=any(v.get("status") in ("running", "stale")
                                                  for v in pst.get("stages", {}).values() if isinstance(v, dict)))
    except Exception as e:   # noqa: BLE001
        res, failed = {"ok": False}, failed or [e]
    if failed:
        e = failed[0]
        builder(run, f"run page: not written ({type(e).__name__}: {e})")
        if not any(p.get("kind") == "run-page" for p in st.get("problems") or [] if isinstance(p, dict)):
            record_problem(run, st, "run-page", "the run page could not be written (%s)" % type(e).__name__,
                           "The run page could not be built this time; the diagrams and map are in the run folder.")
            save_state(run, st)
    if res.get("ok"):
        page_log(run, "run page: %s, written to outputs/%s" % (mode, PAGE_NAME))
        if mode != "live":
            builder(run, "run page: written to outputs/%s" % PAGE_NAME)
    if res.get("ok") and mode != "live":   # the event log records the published end state, not every live refresh
        event(run, {"event": "run-page", "mode": mode, "path": "outputs/" + PAGE_NAME, "fallback": bool(res.get("fallback")),
                    "opened": bool(res.get("opened")), "backfill": backfill})
    return res


def build_run_page(run, st):
    """The final page (kept for callers of the earlier name)."""
    res = publish_page(run, st, "final")
    return page_path(run) if res.get("ok") else None


def finished(st):
    keys = stage_keys(st)
    return bool(keys) and all(str(st["stages"][k]["status"]).startswith(("done", "failed")) for k in keys)


def backfill_page(run, st):
    """A finished run with no final page, or an unfinished one with no page, gets one now (resume, next, status)."""
    rec = {}
    try:
        with open(os.path.join(run, "builder", "live-page.json"), encoding="utf-8") as f:
            rec = (json.load(f) or {}).get(PAGE_NAME) or {}
    except (OSError, ValueError, AttributeError):
        rec = {}
    if finished(st):
        if not os.path.isfile(page_path(run)) or rec.get("mode") not in ("final",):
            publish_page(run, st, "final", backfill=True)
    elif isinstance(st.get("stopped"), dict):
        if not os.path.isfile(page_path(run)) or rec.get("mode") != "ended":
            publish_page(run, st, "ended", reason=st["stopped"].get("reason"), backfill=True)
    elif not os.path.isfile(page_path(run)):
        publish_page(run, st, "live")


def record_problem(run, st, kind, detail, still_need=None):
    """A recorded problem: state.json problems[], a What we still need to know item, and an event; the run continues."""
    st.setdefault("problems", []).append({"at": now(), "kind": kind, "detail": detail})
    st.setdefault("what_we_still_need_to_know", []).append({"kind": kind, "text": still_need or detail})
    event(run, {"event": "problem-recorded", "kind": kind, "detail": detail})


# ---------------------------------------------------------------- the ambition audit and its one rework (R-audit, R-rework)

AUDIT_SKIPPED = "failed, skipped: no future state"


def wants_audit(st):
    """The audit runs in every run init registers it in (R-audit). An older run folder whose state.json has no audit
    stage is left alone and read as it was."""
    return AUDIT in st.get("stages", {}) and "future-state" in st.get("stages", {})


def ensure_audit_stage(run, st):
    """A future-state that failed gets no audit: the audit stage is marked skipped (so `next` and the final page pass it)
    and the ambition state records why. A future-state rerun that finishes puts the audit back to pending. True when it
    changed the state."""
    if not wants_audit(st):
        return False
    fs, s = str(st["stages"]["future-state"].get("status", "")), st["stages"][AUDIT]
    a = st.get("ambition") if isinstance(st.get("ambition"), dict) else None
    if fs.startswith("failed") and s.get("status", "pending") == "pending" and a is None:
        s["status"] = AUDIT_SKIPPED
        st["ambition"] = {"round": 1, "proposals": 0, "rework": "skipped: no future state"}
        event(run, {"event": "ambition-recorded", "ambition": st["ambition"]})
    elif fs.startswith("done") and s.get("status") == AUDIT_SKIPPED:
        s["status"] = "pending"
        st.pop("ambition", None)
        event(run, {"event": "stage-reopened", "stage": AUDIT, "why": "future-state finished"})
    else:
        return False
    save_state(run, st)
    return True


def ambition_final(st):
    """True once the design the person sees is the final one: the audit finished with no rework, the rework's second audit
    finished, or the audit was skipped. An older run with no audit stage is always final."""
    a = st.get("ambition") if isinstance(st.get("ambition"), dict) else None
    if a:
        return a.get("rework") != "running"
    return AUDIT not in st.get("stages", {})


def person_state(st):
    """The state the person's status and page are built from (R-view): the audit stage and its problems left out, and
    while the design is not final every transformation-plan and future-state that has started reads as running, so
    neither the map nor the future state shows as ready before the last round. The real state is never changed."""
    if ambition_final(st) and AUDIT not in st.get("stages", {}):
        return st
    import copy
    pst = copy.deepcopy(st)
    pst["stages"] = {k: v for k, v in pst["stages"].items() if k != AUDIT}
    internal = lambda i: isinstance(i, dict) and str(i.get("kind", "")).startswith("ambition")   # noqa: E731
    pst["what_we_still_need_to_know"] = [i for i in pst.get("what_we_still_need_to_know") or [] if not internal(i)]
    pst["problems"] = [p for p in pst.get("problems") or [] if not internal(p)]
    if not ambition_final(st):
        for k in ("transformation-plan", "future-state"):
            s = pst["stages"].get(k)
            if isinstance(s, dict) and s.get("status", "pending") != "pending":
                s.update({"status": "running", "attempts": max(1, s.get("attempts") or 0)})
                s.pop("started_at", None)   # no long-stage line for a stage shown as running only while the design is checked
    return pst


def audit_proposals(run, checks):
    """(count, how) for the audit just finished: the audit check's `proposals` (an integer or a list), else the readable
    AP rows of the audit file's Proposals table; (0, None) when neither can be read."""
    for c in checks or []:
        n = c.get("proposals")
        if isinstance(n, bool):
            continue
        if isinstance(n, int) and n >= 0:
            return n, "check"
        if isinstance(n, list):
            return len(n), "check"
    try:
        with open(os.path.join(run, "stages", PRIMARY[AUDIT]), encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError:
        return 0, None
    return read_proposals(text)


def read_proposals(text):
    """(count, "file") for the Proposals table of an audit file, or (0, None) when its proposals cannot be read. Fenced
    code blocks and HTML comments are left out first, so a commented-out or quoted example never counts. A table with
    its header and separator and no data rows is a valid zero; a heading with no readable table, or a table none of
    whose rows names an AP-<n> proposal, cannot be read."""
    text = md_blocks.document_text(text)   # the same masked text check_ambition_audit.py reads
    m = re.search(r"(?im)^#+\s*[^\n]*proposals[^\n]*$", text)
    if not m:
        return 0, None
    body = re.split(r"(?m)^#+\s", text[m.end():], 1)[0]
    lines = [l.strip() for l in body.splitlines() if l.strip().startswith("|")]
    sep = next((i for i, l in enumerate(lines) if re.fullmatch(r"[|:\-\s]+", l) and "-" in l), None)
    if not sep or "proposal" not in lines[sep - 1].lower():
        return 0, None   # no table with a header to read
    data = [l for l in lines[sep + 1:] if l.strip("| \t")]
    rows = [l for l in data if re.match(r"\|\s*`?AP-\d+", l)]
    if data and not rows:
        return 0, None
    return len(rows), "file"


def ambition_step(run, st):
    """Carry the audit and rework forward from what state.json records, one persisted step at a time (archive written ->
    stages reset -> round 2 running -> second audit finished), so a resume or a re-run of `next` never copies pass 1
    twice, never repeats the rework and never starts a third round. Returns True when it changed the state."""
    if AUDIT not in st.get("stages", {}):
        return False
    a = st.get("ambition") if isinstance(st.get("ambition"), dict) else None
    s = st["stages"][AUDIT]
    status = str(s.get("status", "pending"))
    finished_now = status.startswith(("done", "failed"))
    if a and a.get("rework") == "running":
        if not finished_now:
            return False
        if status.startswith("failed"):
            record_problem(run, st, "ambition-audit-second-missing", "ambition audit: the second audit wrote no "
                           "ambition-audit.md; the rework stands as it is")
        a["rework"] = "done"
        st.setdefault("ambition_progress", {}).setdefault("steps", []).append("second-audit-finished")
        save_state(run, st)
        event(run, {"event": "ambition-rework-done", "proposals_second_audit": s.get("proposals")})
        builder(run, "ambition audit: second audit finished (%s proposals); rework done" % s.get("proposals"))
        return True
    if a:
        return False   # none, done or skipped: no further round
    prog = st.get("ambition_progress") if isinstance(st.get("ambition_progress"), dict) else None
    if prog is None:
        if not finished_now:
            return False
        if status.startswith("failed"):
            st["ambition"] = {"round": 1, "proposals": 0, "rework": "skipped: no audit"}
            record_problem(run, st, "ambition-audit-missing", "ambition audit: no ambition-audit.md after its attempt; "
                           "no rework, the first design stands")
            save_state(run, st)
            event(run, {"event": "ambition-recorded", "ambition": st["ambition"]})
            builder(run, "ambition audit: no audit file; rework skipped")
            return True
        n = s.get("proposals") if isinstance(s.get("proposals"), int) else 0
        if n < 1:
            st["ambition"] = {"round": 1, "proposals": 0, "rework": "none"}
            save_state(run, st)
            event(run, {"event": "ambition-recorded", "ambition": st["ambition"]})
            builder(run, "ambition audit: no proposals; no rework")
            return True
        prog = st["ambition_progress"] = {"proposals": n, "steps": []}
        save_state(run, st)
    steps = prog.setdefault("steps", [])
    if "archive-written" not in steps:
        for k in REWORK:
            src, dest = os.path.join(run, "stages", k), os.path.join(run, PASS_1, k)
            if os.path.isdir(dest) or not os.path.isdir(src):
                continue   # written once, never overwritten
            tmp = dest + ".tmp"
            shutil.rmtree(tmp, ignore_errors=True)
            shutil.copytree(src, tmp)
            os.replace(tmp, dest)
        steps.append("archive-written")
        save_state(run, st)
        event(run, {"event": "ambition-pass-1-kept", "path": PASS_1, "stages": list(REWORK)})
    if "stages-reset" not in steps:
        for k in REWORK:
            r = st["stages"].get(k)
            if not isinstance(r, dict):
                continue
            r.setdefault("rounds", []).append({k2: r.get(k2) for k2 in ("status", "attempts", "repairs", "minutes", "ended_at")
                                               if k2 in r})
            r["status"], r["repairs"] = "stale", 0   # runs again, with its own one repair
            r.pop("repair", None)
        steps.append("stages-reset")
        save_state(run, st)
    st["ambition"] = {"round": 2, "proposals": prog.get("proposals", 1), "rework": "running"}
    steps.append("round-2-running")
    save_state(run, st)
    event(run, {"event": "ambition-rework-started", "ambition": st["ambition"]})
    builder(run, "ambition audit: %s proposals; first pass kept under %s; transformation-plan, future-state and "
            "ambition-audit run once more" % (st["ambition"]["proposals"], PASS_1))
    return True


def write_handover(run, st):
    """RUN/handover.json: the declared layout, the snapshot's processes, the recommendation's scope as metadata, run-relative
    pointers only, the report by name and sha256, and any absolute path as provenance (never resolved)."""
    procs = st.get("processes") or []
    scope = st.get("recommendation_scope") or []
    rels = {"snapshot": "inputs/snapshot.json", "snapshot_intake": "inputs/snapshot-intake.json", "set_map": SET_MAP,
            "design_direction": "inputs/design-direction.json", "state": "state.json", "events": "events.jsonl"}
    rels.update({k.replace("-", "_"): "stages/" + v for k, v in PRIMARY.items() if k not in ("set-map", AUDIT)})
    pointers = {k: v for k, v in rels.items() if os.path.isfile(os.path.join(run, v))}
    report = None
    if st.get("report_source"):
        # the report itself as the recommendation named it; source_used names the brief actually used
        ref = st.get("report") or {}
        report = {"name": ref.get("name"), "sha256": ref.get("sha256"), "source_used": st["report_source"]}
    rec = {"schema": "fde-factory.handover/0", "layout": st.get("layout"), "processes": procs,
           "recommendation_scope": scope,
           # snapshot processes outside the recommendation's scope are designed like the rest of the set, not context
           "beyond_recommendation": [{"process": p, "reason": "outside the recommendation's scope; designed like the rest of the set"}
                                     for p in procs if scope and p not in scope],
           "context_processes": [],
           "pointers": pointers, "report": report,
           "provenance": {"knowledge_root": st.get(knowledge.ROOT_KEY)},
           "problems": st.get("problems") or [],
           "written_by": "future-state-design/fsd.py", "written_at": now()}
    with open(os.path.join(run, HANDOVER + ".tmp"), "w", encoding="utf-8") as f:
        json.dump(rec, f, indent=2)
    os.replace(os.path.join(run, HANDOVER + ".tmp"), os.path.join(run, HANDOVER))


def stage_keys(st):
    """This run's stages, in order."""
    return [k for k in STAGES if k in st["stages"]]


def set_map_record(run, st, source):
    st["set_map"] = {"source": source, "sha256": sha(os.path.join(run, SET_MAP))}


def git_head():
    """The skill's git commit, or `unknown` when there is no git or git fails; never raises."""
    try:
        p = subprocess.run(["git", "-C", SKILL, "rev-parse", "HEAD"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError, ValueError):
        return "unknown"
    return (p.stdout.strip() if p.returncode == 0 else "") or "unknown"


def record_root_change(run, old, new):
    """K3: the recorded knowledge root is gone and find() resolved a root with the same manifest hash: log the move."""
    st = load_state(run)
    st[knowledge.ROOT_KEY] = new
    st.setdefault("knowledge_root_changes", []).append(
        {"at": now(), "old": old, "new": new, knowledge.HASH_KEY: st.get(knowledge.HASH_KEY)})
    save_state(run, st)
    event(run, {"event": "knowledge-root-changed", "old": old, "new": new,
                knowledge.HASH_KEY: st.get(knowledge.HASH_KEY)})


# ---------------------------------------------------------------- brief

def write_brief(run, st):
    text = open(os.path.join(SKILL, "stages", "brief.template.md"), encoding="utf-8").read()
    for key in stage_keys(st):
        p = os.path.join(SKILL, "stages", key + ".md")
        if key in NO_REFERENCE and not os.path.isfile(p):
            continue   # the set-map and ambition-audit stage texts land with their own units
        text += open(p, encoding="utf-8").read()
    values = brief_values.build(os.path.join(run, "inputs"))
    values["DIRECTION"] = dirmod.brief_section(run)
    left = sorted({m for m in re.findall(r"\{\{([A-Z_]+)\}\}", text) if m not in values})
    if left:
        raise SystemExit("setup error: unfilled blanks in the brief: %s" % left)
    text = re.sub(r"\{\{([A-Z_]+)\}\}", lambda m: values[m.group(1)], text)
    with open(os.path.join(run, "BRIEF.md"), "w", encoding="utf-8") as f:
        f.write(text)
    st["brief_sha256"] = sha(os.path.join(run, "BRIEF.md"))


# ---------------------------------------------------------------- counts

def work_counts(doc):
    """P37 row 28: steps count only work (every node type outside NOT_WORK); ai_use boxes are counted apart as current AI
    uses; object states, messages, context neighbours, start and end, system rails and result boxes are not counted.
    People come from a work node's lane_id and that lane's band in graph.lanes[], never from the coarse `lane` string:
    with no banded lanes the people count is None (not counted), and a work node whose lane_id does not resolve to a
    banded lane is counted as unplaced."""
    g = doc.get("graph") if isinstance(doc.get("graph"), dict) else doc
    nodes = [n for n in g.get("nodes") or [] if isinstance(n, dict)]
    bands = {l["id"]: l.get("band") for l in g.get("lanes") or [] if isinstance(l, dict) and l.get("id") and l.get("band")}
    work = [n for n in nodes if n.get("type") not in NOT_WORK]
    placed = [n for n in work if n.get("lane_id") in bands]
    people = [n for n in placed if bands[n["lane_id"]] == "people"]
    return {"steps": len(work), "ai_uses": sum(n.get("type") == "ai_use" for n in nodes),
            "agent_steps": sum(bands[n["lane_id"]] == "ai_and_agents" for n in placed) if bands else None,
            "people_steps": len(people) if bands else None,
            "people_lanes": len({n["lane_id"] for n in people}) if bands else None,
            "unplaced_steps": len(work) - len(placed) if bands else None}


def stage_counts(run, key):
    """The work counts of a stage's graph, or None when the stage has no graph to count or it cannot be read."""
    if key not in COUNTED:
        return None
    try:
        with open(os.path.join(run, "stages", COUNTED[key]), encoding="utf-8") as f:
            doc = json.load(f)
    except (OSError, ValueError):
        return None
    return work_counts(doc) if isinstance(doc, dict) else None


def counts_line(c):
    if c is None:
        return ""
    if c["people_steps"] is None:
        people = "people not counted (no lanes with a band)"
    else:
        people = "%d people steps in %d people lanes, %d agent steps" % (c["people_steps"], c["people_lanes"], c["agent_steps"])
        if c["unplaced_steps"]:
            people += ", %d steps with no banded lane" % c["unplaced_steps"]
    return "%d work steps (%s); %d current AI uses counted apart" % (c["steps"], people, c["ai_uses"])


# ---------------------------------------------------------------- commands

def cmd_init(a):
    run = os.path.abspath(a.run)
    if os.path.isdir(run) and os.listdir(run):
        raise SystemExit("refused: %s is not empty; a new run needs an absent or empty folder" % run)
    snap = os.path.abspath(a.snapshot)
    if not os.path.isfile(os.path.join(snap, "snapshot.json")):
        raise SystemExit("refused: %s holds no frozen snapshot.json" % snap)
    try:
        frozen = freeze.pin(snap)
    except freeze.FreezeError as e:
        raise SystemExit("refused: the snapshot cannot be pinned: %s" % e)
    kroot = knowledge.root()
    khash = knowledge.knowledge_root.manifest_sha256(kroot)
    if not khash:
        raise SystemExit("refused: the knowledge folder at %s has no readable manifest hash" % kroot)
    for d in ("inputs", "stages", "logs", "prompts", "references"):
        os.makedirs(os.path.join(run, d), exist_ok=True)
    names = ["snapshot.json", "snapshot-intake.json", "systems-used-attribute.json", "set-note.md"] + \
            [os.path.basename(p) for p in glob.glob(os.path.join(snap, "get_process_*.json"))]
    inputs = {}
    for n in names:
        src = os.path.join(snap, n)
        if os.path.isfile(src):
            shutil.copy2(src, os.path.join(run, "inputs", n))
            inputs[n] = sha(os.path.join(run, "inputs", n))
    t0 = now()
    st = {"schema": "future-state-design.state/0", "skill": "future-state-design", "skill_commit": git_head(),
          "created_at": t0, "started_at": t0, "snapshot_source": {"name": os.path.basename(snap), "sha256": sha(os.path.join(snap, "snapshot.json"))},
          "inputs": inputs,
          knowledge.ROOT_KEY: kroot, knowledge.HASH_KEY: khash,
          "freeze": frozen, "set_map": None, "direction": None, "stages": {}}
    procs = snapshot_processes(run)
    st.update({"layout": "single" if len(procs) == 1 else "set", "processes": procs, "recommendation_scope": [],
               "report_source": None, "problems": [], "what_we_still_need_to_know": []})
    keys = [k for k in STAGES if k != "set-map" or wants_set_map_stage(run, None)]
    st["stages"] = {k: {"status": "pending", "attempts": 0} for k in keys}
    write_brief(run, st)
    save_state(run, st)
    write_handover(run, st)
    event(run, {"event": "run-init", "inputs": inputs, knowledge.ROOT_KEY: kroot, knowledge.HASH_KEY: khash,
                "skill_commit": st["skill_commit"]})
    builder(run, f"run ready: {run}")
    builder(run, f"next stage: {keys[0]}")
    publish_page(run, st, "live")
    show_customer(run, st, always=True)


def cmd_direction(a):
    run = os.path.abspath(a.run)
    st = load_state(run)
    if any(s["status"] != "pending" for s in st["stages"].values()):
        raise SystemExit("refused: a stage has already started; the direction is set before the first stage")
    evidence, folder = getattr(a, "evidence", None), getattr(a, "folder", None)
    if not (a.report or a.design_prompt or evidence):
        raise SystemExit("give --report, --design-prompt or --evidence")
    if a.design_prompt and not a.report and not evidence:
        # the prompt's report is looked up by name in --folder too (from_design_prompt goes through resolve)
        d = dirmod.from_design_prompt(a.design_prompt, folder=folder)
        source, line, notes = d.pop("_resolved", None) or ["pasted brief", "I used %s." % dirmod.SOURCE_WORDS["pasted brief"], []]
    else:
        report = a.report
        if report and not a.design_prompt and not evidence and os.path.isfile(report):
            report = os.path.abspath(report)   # the report the person named on the command line
        d, source, line, notes = dirmod.resolve(report, a.design_prompt, evidence, a.solution, a.allow_not_ready, folder)
    st["report"] = d.pop("report_ref", None)
    scope = snapshot_processes(run)
    st.setdefault("layout", "single" if len(scope) == 1 else "set")
    st.setdefault("processes", scope)
    d["process_check"] = dirmod.process_check(d, scope) if d.get("items") else "direction names no process"
    d["report_source"] = source
    st["report_source"] = source
    st["recommendation_scope"] = [p for p in d.get("process_ids") or [] if p]
    for n in notes:
        st.setdefault("report_lookup_notes", []).append(n)
    for w in d.get("what_we_still_need_to_know") or []:
        if source == "none":
            record_problem(run, st, "direction-not-chosen", w.get("detail"))
    outside = [p for p in scope if st["recommendation_scope"] and p not in st["recommendation_scope"]]
    if outside:
        st["context_processes"] = [{"process": p, "reason": "outside the recommendation", "role": "context"} for p in outside]
    dirmod.keep_source_document(d, run)   # a person's original document, unchanged, as evidence in inputs/
    dirmod.portable(d)   # no machine path in the record: the source files become {name, sha256}
    with open(os.path.join(run, "inputs", "design-direction.json"), "w", encoding="utf-8") as f:
        json.dump(d, f, indent=2)
    sm = os.path.join(run, SET_MAP)
    if d.get("set_map") and (len(scope) < 2 or map_covers_snapshot(run, d["set_map"])):
        with open(sm, "w", encoding="utf-8") as f:
            json.dump(d["set_map"], f, indent=2)
        set_map_record(run, st, "direction")
    elif os.path.isfile(sm) and (st.get("set_map") or {}).get("source") == "direction":
        os.remove(sm)   # a direction recorded again without a set map withdraws the earlier one
        st["set_map"] = None
    if wants_set_map_stage(run, d):
        st["stages"] = dict({"set-map": {"status": "pending", "attempts": 0}}, **{k: v for k, v in st["stages"].items() if k != "set-map"})
    else:
        st["stages"].pop("set-map", None)
    st["direction"] = {"file": "inputs/design-direction.json", "sha256": sha(os.path.join(run, "inputs", "design-direction.json")),
                       "source": d["source"], "items": len(d["items"]), "process_check": d["process_check"]}
    write_brief(run, st)
    save_state(run, st)
    write_handover(run, st)
    event(run, {"event": "direction-recorded", "direction_source": d["source"], "report_source": source, "items": len(d["items"]),
                "process_check": d["process_check"], "set_map": st.get("set_map")})
    print(line)
    print("direction recorded: %d items from %s (%s)" % (len(d["items"]), d["source"]["kind"], d["process_check"]))


def changed_done(run, st):
    bad = []
    for k in stage_keys(st):
        s = st["stages"][k]
        if s["status"].startswith("done") and s.get("outputs"):
            now_h = tree_hashes(os.path.join(run, "stages", k))
            if now_h != s["outputs"]:
                bad.append(k)
    return bad


def revalidate_changed(run, st, keys):
    """A finished stage whose files changed is recorded and revalidated, never a stop: its checks run again on the files
    as they are now, the new hashes and checks are recorded, and the stage is `done` when they pass or `done, checks
    failing` when not (repair can follow). Every finished stage after the earliest changed one is marked `stale`, so
    `next` runs it again on the changed input (start needs no --rerun for a stale stage); the change is a recorded
    problem."""
    for k in keys:
        s = st["stages"][k]
        was = s.get("outputs") or {}
        now_h = tree_hashes(os.path.join(run, "stages", k))
        files = sorted(set(was) ^ set(now_h) | {f for f in set(was) & set(now_h) if was[f] != now_h[f]})
        res = run_checks(k, run)
        failing = [c["check"] for c in res if c["result"] not in ("pass", "notes")]   # the finish rule
        status = "done, checks failing" if failing else "done"
        s.setdefault("revalidations", []).append({"at": now(), "files": files[:50], "status_before": s["status"],
                                                  "status_after": status, "failing": failing})
        s.update({"status": status, "checks": res, "outputs": now_h})
        record_problem(run, st, "outputs-changed-after-finish",
                       "%s: %d file(s) changed after it finished (%s); its checks ran again: %s" % (
                           k, len(files), ", ".join(files[:5]) or "none listed",
                           "failing: " + ", ".join(failing) if failing else "all pass"),
                       "%s changed after it finished and was checked again%s." % (
                           LABELS.get(k, k), "; some checks now fail" if failing else ""))
        event(run, {"event": "outputs-revalidated", "stage": k, "files": files[:50], "status": status, "failing": failing})
        builder(run, "%s: outputs changed after finishing (%s); revalidated: %s" % (
            k, ", ".join(files[:5]), status + (" (" + ", ".join(failing) + ")" if failing else "")))
    order = stage_keys(st)
    first = min(order.index(k) for k in keys)
    later = [k for k in order[first + 1:] if st["stages"][k]["status"].startswith("done")]
    for k in later:
        st["stages"][k]["status"] = "stale"
    if later:
        event(run, {"event": "stages-marked-stale", "after": order[first], "stages": later})
        builder(run, "marked stale after %s changed: %s" % (order[first], ", ".join(later)))
    save_state(run, st)


def cmd_next(a):
    run = os.path.abspath(a.run)
    st = load_state(run)
    bad = changed_done(run, st)
    if bad:
        revalidate_changed(run, st, bad)   # recorded and revalidated; the loop goes on
    if ensure_audit_stage(run, st) | ambition_step(run, st):   # a step an interrupted finish left half-done
        publish_page(run, st, "final" if finished(st) else "live")
    backfill_page(run, st)
    for k in stage_keys(st):
        status = st["stages"][k]["status"]
        if k == "set-map" and status.startswith("failed"):
            continue   # a recorded set-map failure: the run goes on with the map marked missing (current-state start records it)
        if k == AUDIT and status.startswith("failed"):
            continue   # no audit file: recorded, no rework, the run goes on
        if not status.startswith("done"):
            print(k)
            return 0
    print("done")
    return 0


def stage_inputs(run, st, key):
    deps = list(DEPS[key])
    if key == "current-state" and (st.get("set_map") or "set-map" in st["stages"]):
        deps.append(SET_MAP)   # never the set-map stage folder: the canonical copy only
    if key in ("transformation-plan", "future-state") and rework_round(st):
        deps.append(PASS_1_AUDIT)   # the rework reads the first audit's proposals
    return deps


def rework_round(st):
    a = st.get("ambition") if isinstance(st.get("ambition"), dict) else {}
    return a.get("round") == 2


def require_stage(st, key):
    if key not in st["stages"]:
        raise SystemExit("refused: %s is not a stage of this run (stages: %s)" % (key, ", ".join(stage_keys(st))))


def audit_instructions(run):
    """The audit's stage text, added to its prompt when the run's brief does not carry it (a brief written before
    stages/ambition-audit.md was present, or an older run). Empty when the brief has it or the file is absent."""
    p = os.path.join(SKILL, "stages", AUDIT + ".md")
    if not os.path.isfile(p):
        return ""
    with open(p, encoding="utf-8") as f:
        text = f.read()
    try:
        with open(os.path.join(run, "BRIEF.md"), encoding="utf-8") as f:
            if text.strip() in f.read():
                return ""
    except OSError:
        pass
    return "\n\nYour stage instructions (stages/ambition-audit.md) follow; they are not in BRIEF.md.\n\n" + text


def cmd_prompt(a):
    run, key = os.path.abspath(a.run), a.stage
    st = load_state(run)
    require_stage(st, key)
    kroot = knowledge.root(run)
    stage_dir = os.path.join(run, "stages", key)
    os.makedirs(stage_dir, exist_ok=True)
    prompt = (f"You are the '{key}' stage agent. Read {run}/BRIEF.md first and follow it exactly.\n"
              f"STAGE={key}\nTarget: about {target(run, key):g} minutes. Aim to be done inside it.\n"
              f"KNOWLEDGE_ROOT={kroot}\nRUN_ROOT={run}\nSKILL_ROOT={SKILL}\nOUTPUT_FOLDER={stage_dir}\n"
              f"You may read and run the scripts under {SKILL}/scripts/ that your stage text names (they are the checks it runs).\n"
              f"Read {run}/state.json to see which earlier stages finished. "
              f"Inputs for your stage: {', '.join(stage_inputs(run, st, key))}. Your log: {run}/logs/agent-{key}.md")
    if key in ("current-state", "future-state"):
        n = len(process_ids(run))
        prompt += (f"\nRenderer for this run ({n} snapshot process{'es' if n != 1 else ''}), run inside {stage_dir}: "
                   f"{renderer_command(run, key, kroot)}")
    if key in NO_REFERENCE:
        # Pins §1: no worked reference is registered for set-map or the audit, so no reference block and no reference record.
        sp = subprocess.CompletedProcess([], 0, "", "")
    else:
        sp = subprocess.run([sys.executable, knowledge.path("stage_references", run), "--run-root", run, "--stage", key, "--emit", "block"],
                            capture_output=True, text=True, env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
    block = sp.stdout
    if sp.returncode != 0:
        raise SystemExit("reference staging failed (%d): %s" % (sp.returncode, sp.stderr[-400:]))
    if block and "REFERENCE-SET" not in block:
        prompt += "\n\nNote for this stage: " + block.strip()
    elif block:
        prompt += ("\n\nThe worked-reference block for this stage follows. The reference copies it names are staged under "
                   + run + "/references/. Read those copies (they are the references the brief means), and add "
                   "the reference_use record it describes to your primary output.\n\n" + block)
    if key == AUDIT:
        prompt += audit_instructions(run)
    sblock, _ = check_schema.extract(run, key)
    if sblock:
        prompt += "\n\n" + sblock
    refs = a3_refs.stage(key, run)
    if refs:
        prompt += "\n\n" + "\n".join(refs)
    p = os.path.join(run, "prompts", key + ".md")
    with open(p, "w", encoding="utf-8") as f:
        f.write(prompt + "\n\n" + "Read only inside this run's folder and the plugin; never list or open folders beside the run." + "\n")
    event(run, {"event": "prompt-written", "stage": key, "sha256": sha(p), "chars": len(prompt)})
    print(p)


def progress_line(st, key, what, so_far=True):
    """The one line the host posts at each stage start and end: `FSD i/N <stage>: <what>`, then the passing checks of
    every finished stage (its latest checks) when so_far. The audit is internal (R-view): it is left out of N and of the
    checks so far, and its own lines read N/N."""
    keys = [k for k in stage_keys(st) if k != AUDIT]
    line = f"FSD {keys.index(key) + 1 if key in keys else len(keys)}/{len(keys)} {key}: {what}"
    if so_far:
        ck = [c for k in keys if st["stages"][k]["status"].startswith("done") for c in st["stages"][k].get("checks") or []]
        line += f" Checks so far: {sum(c['result'] == 'pass' for c in ck)}/{len(ck)} passing."
    return line


def cmd_start(a):
    run, key = os.path.abspath(a.run), a.stage
    st = load_state(run)
    require_stage(st, key)
    s = st["stages"][key]
    if s["status"].startswith("done") and not a.rerun:
        raise SystemExit("refused: %s is already %s; use --rerun to run it again" % (key, s["status"]))
    tried = lambda n: st["stages"][n]["status"].startswith("done") or (n == "set-map" and st["stages"][n]["status"].startswith("failed"))
    missing = [n for n in NEEDS[key] if n in st["stages"] and not tried(n)]
    if missing:
        raise SystemExit("refused: %s needs %s first" % (key, ", ".join(missing)))
    sm = os.path.join(run, SET_MAP)
    smap = None
    if os.path.isfile(sm):
        try:
            smap = json.load(open(sm, encoding="utf-8"))
        except (OSError, ValueError, UnicodeDecodeError) as e:   # the one stop: a map that exists but won't parse
            raise SystemExit("refused: %s cannot start: inputs/set-map.json cannot be read (%s)" % (key, type(e).__name__))
    elif key == "current-state" and "set-map" in st["stages"] and not (st.get("set_map") or {}).get("missing"):
        st["set_map"] = {"missing": True, "source": None}
        record_problem(run, st, "set-map-missing", "the set map was not published (its stage did not pass its checks), so the "
                       "current state is drawn with the map marked missing",
                       "How the processes link to each other is still open: the set map did not pass its checks.")
    try:
        drift = freeze.drift(st, smap)
    except freeze.FreezeError as e:
        drift = None
        record_problem(run, st, "set-map-freeze-unchecked", "the set map could not be compared with the freeze: %s" % e)
    if drift:
        named = [str(d.get("process_id", d)) if isinstance(d, dict) else str(d) for d in drift]
        if not any(p.get("kind") == "set-map-freeze-drift" for p in st.get("problems") or []):
            record_problem(run, st, "set-map-freeze-drift", "the set map names later process versions than the freeze pinned for %s; "
                           "the published map is kept" % ", ".join(named),
                           "The set map was drawn from later process versions than the snapshot for %s: re-freeze the snapshot "
                           "or refresh the set map (one repair)." % ", ".join(named))
    if s["status"] == "running":
        event(run, {"event": "start-again", "stage": key, "note": "the previous attempt never finished"})
    s.update({"status": "running", "started_at": now(), "attempts": s.get("attempts", 0) + 1})
    st.pop("stopped", None)   # work resumed: the page goes live again
    save_state(run, st)
    write_handover(run, st)
    event(run, {"event": "stage-start", "stage": key, "attempt": s["attempts"], "target_min": target(run, key)})
    with open(os.path.join(run, "events.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps({"type": "executor.started", "stage": key, "at": now(),
                            "payload": {"inputs": executor_inputs(run, st, key)}}) + "\n")
    publish_page(run, st, "live")
    builder(run, "started " + key)
    builder(run, progress_line(st, key, f"started (attempt {s['attempts']})."))


def executor_inputs(run, st, key):
    """Run-relative paths (with hashes, the form the copy guard reads) of every inputs/ file and every done stage's primary."""
    rels = sorted(os.path.relpath(os.path.join(dp, f), run) for dp, _, fs in os.walk(os.path.join(run, "inputs")) for f in fs)
    rels += ["stages/" + PRIMARY[k] for k in stage_keys(st)
             if k != key and st["stages"][k]["status"].startswith("done") and os.path.isfile(os.path.join(run, "stages", PRIMARY[k]))]
    if key in ("transformation-plan", "future-state") and rework_round(st) and os.path.isfile(os.path.join(run, PASS_1_AUDIT)):
        rels.append(PASS_1_AUDIT)
    return [{"path": r, "sha256": sha(os.path.join(run, r))} for r in rels]


def carrier(key, run):
    """The file that carries the reference_use record (Fast Mode's rule): the primary output if it does, else another output."""
    marks = ("reference_use", "REFERENCE-USE")
    def has(p):
        try:
            return any(m in open(p, encoding="utf-8", errors="replace").read() for m in marks)
        except OSError:
            return False
    prim = [p for p in glob.glob(os.path.join(run, "stages", PRIMARY[key])) if os.path.isfile(p)]
    others = sorted(f for f in glob.glob(os.path.join(run, "stages", key, "**", "*"), recursive=True)
                    if os.path.isfile(f) and os.path.splitext(f)[1] in (".json", ".yaml", ".yml", ".md") and f not in prim)
    for p in prim + others:
        if has(p):
            return [p]
    return prim[:1]


def checks_for(key, run):
    C = knowledge.path("checks", run)
    g = lambda pat: sorted(glob.glob(os.path.join(run, "stages", pat)))
    out = []
    if key in ("current-state", "future-state"):
        b = g(f"{key}/{key}.json")
        for n in ("systems-coverage", "graph-structure", "coverage-manifest", "open-and-contradiction", "render-binding", "render-geometry"):
            out.append((f"workflow-{n}", [f"{C}/check-workflow-{n}.py"] + b, b))
        if key == "future-state":
            out.append(("future-state-reshape", [f"{C}/check-future-state-reshape.py", run], b))
    else:
        b = g("transformation-plan/transformation-map.md")
        out.append(("transformation-map-shape", [f"{C}/check-transformation-map-shape.sh"] + b, b))
    cand = carrier(key, run)
    for name, script in (("reference-use-record", "check-reference-use-record.sh"), ("reference-copy-guard", "check-reference-copy-guard.sh")):
        if cand:
            out.append((name, [os.path.join(C, script), "--stage", key, cand[0]], cand))
    if key in ("transformation-plan", "future-state"):
        reg = g("current-state/current-state.input.json")
        out.append(("open-ids-in-register", [os.path.join(HERE, "check_open_ids.py"), run], reg))
    if key == "transformation-plan" and os.path.isfile(os.path.join(run, "inputs", "design-direction.json")):
        out.append(("design-direction", [os.path.join(HERE, "check_direction.py"), run], ["inputs/design-direction.json"]))
    return out


def parse_findings(stdout):
    """(findings, check_result) when the whole stripped stdout is one JSON object with a `findings` list, else (None, None).
    A finding that is not an object or has no `rule` is kept as {rule: None, where: None}."""
    try:
        doc = json.loads(stdout.strip())
    except ValueError:
        return None, None
    if not isinstance(doc, dict) or not isinstance(doc.get("findings"), list):
        return None, None
    out = [{"rule": f.get("rule"), "where": f.get("where")} if isinstance(f, dict) and f.get("rule") is not None
           else {"rule": None, "where": None} for f in doc["findings"]]
    return out, doc.get("result") if isinstance(doc.get("result"), str) else None


def run_relative(text, run):
    """text with the run folder's absolute path (and its resolved form) plus the following / removed, so a check's detail
    names files inside the run run-relative; a bare run path becomes ".". Paths outside the run stay as they are."""
    for r in sorted({run, os.path.realpath(run)}, key=len, reverse=True):
        text = re.sub(re.escape(r) + r"(?:/|(?![^\s'\"`|:;,)\]]))", lambda m: "" if m.group(0).endswith("/") else ".", text)
    return text


def run_checks(key, run):
    res = []
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    for label, argv, needed in hooks_checks.for_stage(key, run):
        if not needed:
            res.append({"check": label, "result": "notes" if label in hooks_checks.NOTES_ONLY else "no-file",
                        "detail": "input file not present"}); continue
        limit, findings, printed = hooks_checks.timeout_for(label), None, None
        try:
            p = subprocess.run([sys.executable] + argv, cwd=run, capture_output=True, text=True, timeout=limit, env=env)
            tail = (p.stdout + p.stderr).strip().splitlines()
            c = {"check": label, "result": "pass" if p.returncode == 0 else f"fail(rc={p.returncode})", "detail": run_relative(" | ".join(tail[-3:]), run)[:400]}
            findings, printed = parse_findings(p.stdout)
            if label in hooks_checks.NOTES_ONLY:
                try:
                    doc = json.loads(p.stdout.strip())
                except ValueError:
                    doc = None
                if isinstance(doc, dict) and "proposals" in doc:
                    c["proposals"] = doc["proposals"]
                if isinstance(doc, dict) and isinstance(doc.get("notes"), list) and label == "audit-response":
                    c["notes"] = [str(n) for n in doc["notes"][:50]]
        except subprocess.TimeoutExpired:
            c = {"check": label, "result": "timeout", "detail": f"check exceeded {limit}s"}
        if label in hooks_checks.NOTES_ONLY and c["result"] != "pass":
            c["raw_result"], c["result"] = c["result"], "notes"   # its output is notes only, never a failing check
            res.append(c)
            continue
        if findings is not None:   # classified below on the raw findings; stored with run-relative `where`
            c["findings"] = [dict(f, where=run_relative(f["where"], run)) if isinstance(f.get("where"), str) else f
                             for f in findings[:50]]
        if c["result"] not in ("pass", "no-file"):
            c["repairable"], c["why"] = hooks_checks.repair_scope(label, c["result"], findings, run, printed, stage=key)
        res.append(c)
    return res


def cmd_check(a):
    res = run_checks(a.stage, os.path.abspath(a.run))
    for c in res:
        print(f"{c['result']:<12} {c['check']}  {c['detail'][:200] if c['result'] != 'pass' else ''}")
    return 1 if any(c["result"] not in ("pass", "notes") for c in res) else 0


def cmd_finish(a):
    run, key = os.path.abspath(a.run), a.stage
    st = load_state(run)
    require_stage(st, key)
    s = st["stages"][key]
    if s["status"] != "running":
        raise SystemExit("refused: %s is %s, not running" % (key, s["status"]))
    res = run_checks(key, run)
    failing = [c["check"] for c in res if c["result"] not in ("pass", "notes")]
    prim = os.path.isfile(os.path.join(run, "stages", PRIMARY[key]))
    mins = None
    try:
        mins = round((datetime.fromisoformat(now()) - datetime.fromisoformat(s["started_at"])).total_seconds() / 60, 2)
    except Exception:
        pass
    status = "done" if prim and not failing else ("done, checks failing" if prim else "failed, primary output missing")
    s.update({"status": status, "ended_at": now(), "minutes": mins, "checks": res, "outputs": tree_hashes(os.path.join(run, "stages", key))})
    counts = stage_counts(run, key)
    if counts is not None:
        s["counts"] = counts
    # The renderer's own layout findings: a note, never a failing check (ruling 2).
    lp, note = os.path.join(run, "stages", key, key + ".layout.json"), None
    s["layout_findings"] = 0
    if os.path.isfile(lp):
        try:
            found = json.load(open(lp, encoding="utf-8")).get("findings")
            if not isinstance(found, list):
                raise ValueError("findings is not a list")
        except (ValueError, AttributeError, OSError):
            s["layout_findings"], note = None, f"{key}: renderer layout notes: unreadable layout.json"
        else:
            s["layout_findings"] = len(found)
            if found:
                msgs = [" ".join(str(f.get("message", f) if isinstance(f, dict) else f).splitlines()) for f in found[:3]]
                note = f"{key}: renderer layout notes: {len(found)} (" + "; ".join(m if len(m) <= 80 else m[:79] + "…" for m in msgs) + ")"
    if key == "set-map" and status == "done":
        shutil.copyfile(os.path.join(run, "stages", PRIMARY[key]), os.path.join(run, SET_MAP))
        set_map_record(run, st, "set-map stage")
    elif key == "set-map" and (s.get("repairs", 0) >= 1 or not prim):
        record_problem(run, st, "set-map-failed", "the set map still fails its checks after its one repair (%s); no map was "
                       "published" % (", ".join(failing) or "primary output missing"),
                       "How the processes link to each other is still open: the set map did not pass its checks.")
    if key == AUDIT and prim:
        n, how = audit_proposals(run, res)
        s["proposals"] = n
        if how is None:
            record_problem(run, st, "ambition-audit-unreadable", "ambition audit: its proposals could not be read; counted as 0")
    save_state(run, st)
    if key == "future-state":
        ensure_audit_stage(run, st)   # a failed future-state: the audit is skipped; a rerun that finishes reopens it
    if key == AUDIT:
        ambition_step(run, st)   # records the ambition state and, with proposals, starts the one rework
    write_handover(run, st)
    # every finish publishes the person's page; the run's last finish publishes the final page (never fails the finish)
    publish_page(run, st, "final" if finished(st) else "live")
    event(run, {"event": "stage-end", "stage": key, "status": status, "minutes": mins,
                "checks_pass": len(res) - len(failing), "checks_total": len(res), "failing": failing, "counts": counts})
    builder(run, f"{key}: {status} in {mins} min; checks {len(res) - len(failing)}/{len(res)}" + (f"; failing: {', '.join(failing)}" if failing else ""))
    if counts is not None:
        builder(run, f"{key}: {counts_line(counts)}")
    if note:
        builder(run, note)
    for c in res:   # the rework's audit response: a quiet record for the builder log, never a failing check
        if c.get("check") == "audit-response":
            for n in c.get("notes") or []:
                builder(run, f"{key}: {n}")
    builder(run, progress_line(st, key, f"{status}, {'?' if mins is None else mins} min. Stage checks {len(res) - len(failing)}/{len(res)}"
                               + (f" (failing: {', '.join(failing)})" if failing else "") + "."))
    show_customer(run, st)
    return 1 if failing or not prim else 0


# The map's Write grants table is an input to the future-state stage: a write-grant finding that comes from the table
# disagreeing with the evidence is answered by a recorded reading in the stage's own output, never a map edit.
GRANT_READING_NOTE = ("A writes-and-scope finding that comes from the transformation map's Write grants table disagreeing "
                      "with the map's own text or the evidence is not fixed by editing the map: draw what the evidence "
                      "supports and record the reading you took in the `grant_readings` list at the root of "
                      "future-state.input.json (grant, column, table_says, read_as, rules, reason), then render again.\n")


def cmd_repair(a):
    run, key = os.path.abspath(a.run), a.stage
    st = load_state(run)
    require_stage(st, key)
    s = st["stages"][key]
    if s["status"] != "done, checks failing":
        raise SystemExit("refused: %s is %s; repair follows a finish with failing checks" % (key, s["status"]))
    if s.get("repairs", 0) >= 1:
        raise SystemExit("refused: %s already had its one repair; the failing checks stay on the record" % key)
    bad = [c for c in s.get("checks") or [] if c["result"] not in ("pass", "no-file", "notes")]   # failing: not pass, not no-file
    fixable = [c for c in bad if c.get("repairable", True)]   # a check recorded before repair scope counts as repairable
    leave = [c for c in bad if not c.get("repairable", True)]
    if not fixable:   # nothing to hand a repair agent, including no failing check at all (e.g. only no-file results)
        reasons = [{"check": c["check"], "why": c.get("why", "")} for c in leave]
        s["repair"] = {"skipped": "not repairable here", "reasons": reasons}
        save_state(run, st)
        event(run, {"event": "repair-skipped", "stage": key, "reasons": reasons})
        why = "; ".join(f"{r['check']}: {r['why']}" for r in reasons) or "no failing checks"
        builder(run, f"{key}: not repairable here; {why}")
        builder(run, progress_line(st, key, f"not repairable here ({why})", False))
        return 0
    stage_dir = os.path.join(run, "stages", key)
    prompt = (f"You are the in-stage repair agent for the '{key}' stage. Read {run}/BRIEF.md (its rules apply). "
              f"Target: about 4 minutes. Aim to be done inside it.\n"
              f"KNOWLEDGE_ROOT={knowledge.root(run)}\nRUN_ROOT={run}\nSKILL_ROOT={SKILL}\nOUTPUT_FOLDER={stage_dir}\n"
              f"You may read and run the scripts under {SKILL}/scripts/ that your stage text names (they are the checks it runs).\n"
              "Your stage's scripted checks failed. Fix ONLY what makes them fail, and ONLY by editing files inside your stage's output folder. "
              "Re-run the failing check yourself if you can, and stop when it passes. Do not touch any other stage, the inputs, state.json or events.jsonl. "
              f"Write {run}/logs/inrepair-{key}.md with one line per failed check: 'fixed' or 'unfixed', and why.\n"
              + (GRANT_READING_NOTE if key == "future-state" else "")
              + "\nFailed checks:\n" + json.dumps(fixable, indent=1))
    if leave:
        prompt += ("\n\nAlso failing: %s; leave these; they are not fixable in this stage." % ", ".join(c["check"] for c in leave))
    p = os.path.join(run, "prompts", key + "-repair.md")
    with open(p, "w", encoding="utf-8") as f:
        f.write(prompt + "\n\n" + "Read only inside this run's folder and the plugin; never list or open folders beside the run." + "\n")
    s.update({"status": "running", "repairs": s.get("repairs", 0) + 1, "started_at": now()})
    save_state(run, st)
    event(run, {"event": "repair-start", "stage": key, "failing": [c["check"] for c in fixable],
                "not_repairable": [c["check"] for c in leave]})
    print(p)
    builder(run, progress_line(st, key, "repair started for " + ", ".join(c["check"] for c in fixable) + ".", False))


def copy_check(run, src):
    """The copy-in check, before anything is written: every file under src compared by sha256 with the same file in the run.
    A file absent from the run counts as differing. events.jsonl matches when the copy's log is a byte prefix of the run's
    and every line after it is a run-resumed event, so the retry after a failed check is not failed by that check's own
    record."""
    src = os.path.abspath(src)
    if not os.path.isdir(src):
        raise SystemExit("refused: --copied-from is not a folder: %s" % src)
    want = tree_hashes(src)
    differed = [rel for rel, h in want.items()
                if not os.path.isfile(os.path.join(run, rel)) or sha(os.path.join(run, rel)) != h]
    if "events.jsonl" in differed and resume_records_only(os.path.join(src, "events.jsonl"),
                                                          os.path.join(run, "events.jsonl")):
        differed.remove("events.jsonl")
    return {"copied_from": src, "files_checked": len(want), "differed": differed}


def resume_records_only(src_log, run_log):
    """True when run_log is src_log plus whole lines that are each a run-resumed event written by this script."""
    try:
        with open(src_log, "rb") as f:
            head = f.read()
        with open(run_log, "rb") as f:
            have = f.read()
    except OSError:
        return False
    if not have.startswith(head) or (head and not head.endswith(b"\n")):
        return False
    for line in have[len(head):].splitlines():
        try:
            rec = json.loads(line)
        except ValueError:
            return False
        if not (isinstance(rec, dict) and rec.get("event") == "run-resumed" and rec.get("source") == "fsd.py"):
            return False
    return True


def cmd_resume(a):
    run = os.path.abspath(a.run)
    check = a.copy_check
    event(run, {"event": "run-resumed", "copy_check": check})
    try:
        st = load_state(run)
        if ensure_audit_stage(run, st) | ambition_step(run, st):   # an audit or rework step an interruption left half-done
            publish_page(run, st, "final" if finished(st) else "live")
        backfill_page(run, st)
    except Exception as e:   # noqa: BLE001 - the page never blocks a resume
        builder(run, "run page: not backfilled on resume (%s)" % type(e).__name__)
    if check is None:
        print("FSD resume recorded: no copy-in check (the run was not copied in on this host).")
        return 0
    if check["differed"]:
        print("FSD resume recorded: %d files checked, %d differ from the copy in %s:" % (
            check["files_checked"], len(check["differed"]), check["copied_from"]))
        for rel in check["differed"]:
            print("  " + rel)
        return 1
    print("FSD resume recorded: %d files checked, all match the copy in %s." % (check["files_checked"], check["copied_from"]))
    return 0


def cmd_status(a):
    run = os.path.abspath(a.run)
    st = load_state(run)
    if not getattr(a, "builder", False):
        backfill_page(run, st)
        if getattr(a, "if_changed", False):
            show_customer(run, st)   # between steps of a long stage: post only when the person's view changed
            return 0
        sh = _shared("status")
        print(sh.safe(lambda: customer_view(run, st), record=lambda l: builder(run, l)))
        return 0
    print("| Stage | Status | Minutes | Checks | Counts |\n|---|---|---|---|---|")
    for k in stage_keys(st):
        s = st["stages"][k]
        ck = s.get("checks") or []
        c = stage_counts(run, k) if s["status"].startswith("done") else None
        print(f"| {LABELS[k]} | {s['status']} | {s.get('minutes') or ''} | " + (f"{sum(c['result'] == 'pass' for c in ck)}/{len(ck)}" if ck else "")
              + f" | {counts_line(c)} |")
    noted = [k for k in stage_keys(st) if (st["stages"][k].get("layout_findings") or 0) > 0]
    if noted:
        print()
    for k in noted:
        print(f"Note: {LABELS[k]} has {st['stages'][k]['layout_findings']} renderer layout notes (arrows not running left to right); "
              f"see {k}.layout.json.")
    if st.get("direction"):
        print("\nDirection: %s, %d items" % (st["direction"]["source"]["kind"], st["direction"]["items"]))
    return 0


def cmd_stop(a):
    """The person stopped the run (or the session must end early): publish the page's ended view and say so."""
    run = os.path.abspath(a.run)
    st = load_state(run)
    reason = (a.reason or "").strip() or None
    if not finished(st):
        st["stopped"] = {"at": now(), "reason": reason}
        save_state(run, st)
    event(run, {"event": "run-stopped", "reason": reason})
    builder(run, "run stopped" + (": " + reason if reason else ""))
    publish_page(run, st, "final" if finished(st) else "ended", reason=reason)
    show_customer(run, st, always=True)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("init", "direction", "next", "prompt", "start", "finish", "repair", "check", "status", "resume", "stop"):
        p = sub.add_parser(name)
        p.add_argument("--run", required=True)
        if name in ("prompt", "start", "finish", "repair", "check"):
            p.add_argument("--stage", required=True, choices=STAGES)
    sub.choices["init"].add_argument("--snapshot", required=True)
    sub.choices["start"].add_argument("--rerun", action="store_true")
    sub.choices["resume"].add_argument("--copied-from")
    sub.choices["status"].add_argument("--builder", action="store_true")
    sub.choices["status"].add_argument("--if-changed", action="store_true")
    sub.choices["stop"].add_argument("--reason")
    d = sub.choices["direction"]
    d.add_argument("--report"); d.add_argument("--solution"); d.add_argument("--design-prompt"); d.add_argument("--allow-not-ready", action="store_true")
    d.add_argument("--evidence"); d.add_argument("--folder")
    a = ap.parse_args(argv)
    if a.cmd == "resume":
        # Compare the copy before the knowledge check below can write to state.json. A copy that differs is recorded
        # and returned at once: the check below may refuse a run whose state.json did not copy, and the result must
        # still reach the event log so the person can copy again.
        if not os.path.isdir(os.path.abspath(a.run)):
            raise SystemExit("refused: the run folder does not exist: %s" % os.path.abspath(a.run))
        a.copy_check = copy_check(os.path.abspath(a.run), a.copied_from) if a.copied_from else None
        if a.copy_check and a.copy_check["differed"]:
            return cmd_resume(a)
    if a.cmd != "init":
        # A resumed run reads only the knowledge it started with; refuse before anything is written.
        run = os.path.abspath(a.run)
        try:
            old = knowledge.recorded(run)[0]
            new = knowledge.verify(run)
        except knowledge.KnowledgeChanged as e:
            raise SystemExit(str(e))
        if new != old:
            record_root_change(run, old, new)
    return {"init": cmd_init, "direction": cmd_direction, "next": cmd_next, "prompt": cmd_prompt, "start": cmd_start,
            "finish": cmd_finish, "repair": cmd_repair, "check": cmd_check, "status": cmd_status,
            "resume": cmd_resume, "stop": cmd_stop}[a.cmd](a) or 0


if __name__ == "__main__":
    sys.exit(main())
