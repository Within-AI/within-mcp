#!/usr/bin/env python3
"""Agent Design time targets, status table and recovery ladder (hook module, loaded by ad.py).

  target(run_dir, stage)    the stage's target in minutes (scaled for a set), or None for a null target; the
                            status table counts one process until the intake has run (just after init)
  table(run_dir)            the Markdown status table: minutes against target, checks, items, last watch
  boundary(run_dir, stage)  append the table to status.md and print it; walk the recovery ladder for an
                            over-time stage; at the run ceiling write REMAINING.md and raise CeilingReached
  next_rung(run_dir, stage) record and return the stage's next recovery rung
  customer_view(run_dir)    the status the person sees: at most 10 plain rows, wall-clock minutes since the run
                            started, one note line, what the notes are, the unresolved upstream count, a long-stage
                            line and the page line; no check names, ids, counters or exit codes
  customer_post(run_dir)    the same block when the person's view changed since the last post, else None
  builder_view(run_dir)     the builder's status: table() (check names, attempts, counters, recovery steps)

Minutes come only from the stage records in state.json; this module never measures time. It reads
state.json, events.jsonl, stages/intake/intake.json, stages/index.json and config/targets.json, and writes
only status.md, REMAINING.md and logs/recovery.jsonl (customer_post also records builder/status-posts.json). The customer view's wall clock reads event timestamps only. Acting on a rung is the driving session's job.
Standard library only, Python 3.9-safe.
"""
import json
import os
from datetime import datetime, timezone

SKILL = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INDEX = os.path.join(SKILL, "stages", "index.json")
TARGETS = os.path.join(SKILL, "config", "targets.json")

OVER_TIME_FACTOR = 2
CEILING_FACTOR = 1.5
RUNGS = ["repair in place", "retry once", "continue with a warning", "drop only the affected unit", "hard stop"]
DASH = "—"


class CeilingReached(Exception):
    """The run's summed stage minutes reached 1.5 times the summed targets."""


def _load(path, name):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError) as e:
        raise ValueError("%s cannot be read (%s)" % (name, e))


def _index():
    d = _load(INDEX, "stages/index.json")
    try:
        return [(e["key"], e.get("target")) for e in d["stages"]]
    except (KeyError, TypeError) as e:
        raise ValueError("stages/index.json has no readable stages list (%s)" % e)


def _targets():
    d = _load(TARGETS, "config/targets.json")
    try:
        return {k: (float(v["minutes"]), v["kind"]) for k, v in d["targets"].items()}
    except (KeyError, TypeError, ValueError, AttributeError) as e:
        raise ValueError("config/targets.json has no readable targets (%s)" % e)


def _state(run_dir):
    d = _load(os.path.join(run_dir, "state.json"), "state.json")
    if not isinstance(d, dict) or not isinstance(d.get("stages"), dict):
        raise ValueError("state.json has no stages object")
    return d["stages"]


def _processes(run_dir, stage, before_intake=False):
    name = "stages/intake/intake.json"
    if before_intake and not os.path.exists(os.path.join(run_dir, "stages", "intake", "intake.json")):
        return 1   # the status table before the intake has run (just after init): the set factor is not known yet
    try:
        with open(os.path.join(run_dir, "stages", "intake", "intake.json"), encoding="utf-8") as f:
            procs = json.load(f).get("processes")
    except (OSError, ValueError, AttributeError) as e:
        raise ValueError("%s: the set factor needs %s, which cannot be read (%s)" % (stage, name, e))
    if not isinstance(procs, list) or not procs:
        raise ValueError("%s: the set factor needs %s, which lists no process" % (stage, name))
    return len(procs)


def _scaled(run_dir, stage, key, targets, before_intake=False):
    if key not in targets:
        raise ValueError("%s: target key %r is not in config/targets.json" % (stage, key))
    minutes, kind = targets[key]
    if kind == "script":
        return minutes
    return minutes * (1 + 0.5 * (_processes(run_dir, stage, before_intake) - 1))


def target(run_dir, stage):
    """The stage's target in minutes, scaled by the set factor for agent targets; None for a null target."""
    idx = dict(_index())
    if stage not in idx:
        raise ValueError("%s is not a stage in stages/index.json" % stage)
    if idx[stage] is None:
        return None
    return _scaled(run_dir, stage, idx[stage], _targets())


def _fmt(n):
    return "%g" % round(n, 2)


def _over(minutes, tgt):
    return minutes is not None and tgt is not None and minutes > OVER_TIME_FACTOR * tgt


def _checks_cell(checks):
    if not checks:
        return "0/0"
    passed = sum(1 for c in checks if c.get("result") == "pass")
    unchecked = sum(1 for c in checks if c.get("result") == "unchecked")
    cell = "%d/%d" % (passed, len(checks))
    return cell + (" (unchecked %d)" % unchecked if unchecked else "")


def _items_cell(counts):
    if not counts:
        return DASH
    if not isinstance(counts, dict):
        return str(counts)
    parts = []
    for name, c in counts.items():
        if isinstance(c, dict):
            parts.append("%s: %s" % (name, ", ".join("%s %s" % (k, v) for k, v in c.items()) or DASH))
        else:
            parts.append("%s: %s" % (name, DASH if c is None else c))
    return "; ".join(parts).replace("|", "/")


def _last_watch(run_dir):
    p = os.path.join(run_dir, "events.jsonl")
    if not os.path.isfile(p):
        return "no watch yet"
    times = []
    try:
        with open(p, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    ev = json.loads(line)
                    if ev.get("event") == "watch":
                        times.append(datetime.fromisoformat(str(ev["at"]).replace("Z", "+00:00")))
    except (OSError, ValueError, KeyError, AttributeError) as e:
        raise ValueError("events.jsonl cannot be read (%s)" % e)
    if not times:
        return "no watch yet"
    if len(times) == 1:
        return "first watch"
    return _fmt((times[-1] - times[-2]).total_seconds() / 60)


def _compute(run_dir):
    """Everything the table, the ladder and the ceiling need; reads only, raises ValueError on bad input."""
    stages = _state(run_dir)
    idx = _index()
    targets = _targets()
    rows = []
    for key, tkey in idx:
        s = stages.get(key) or {}
        tgt = None if tkey is None else _scaled(run_dir, key, tkey, targets, True)
        rows.append({"stage": key, "status": s.get("status", "pending"), "minutes": s.get("minutes"),
                     "target": tgt, "checks": s.get("checks") or [], "counts": s.get("counts")})
    used = sum(r["minutes"] for r in rows if isinstance(r["minutes"], (int, float)))
    total = sum(_scaled(run_dir, k, k, targets, True) for k in targets)
    return rows, used, total, total * CEILING_FACTOR


def _render(rows, used, total, ceiling, watch):
    lines = ["| Stage | Status | Minutes | Target | Over time | Checks | Items |",
             "|---|---|---|---|---|---|---|"]
    for r in rows:
        m = r["minutes"] if isinstance(r["minutes"], (int, float)) else None
        lines.append("| %s | %s | %s | %s | %s | %s | %s |" % (
            r["stage"], r["status"], DASH if m is None else _fmt(m), DASH if r["target"] is None else _fmt(r["target"]),
            "over time" if _over(m, r["target"]) else "", _checks_cell(r["checks"]), _items_cell(r["counts"])))
    open_up = sum(int(((r["counts"] or {}).get("check_grants") or {}).get("upstream_unresolved") or 0)
                  for r in rows if isinstance(r["counts"], dict) and isinstance(r["counts"].get("check_grants"), dict))
    rec_up = sum(int(((r["counts"] or {}).get("check_grants") or {}).get("upstream_recorded") or 0)
                 for r in rows if isinstance(r["counts"], dict) and isinstance(r["counts"].get("check_grants"), dict))
    if open_up or rec_up:
        lines += ["", "**Upstream write-grant problems: %d unresolved, %d recorded** (the run continues; each is in "
                  "What we still need to know)" % (open_up, rec_up)]
    lines += ["", "Run: %s of %s minutes (ceiling %s)" % (_fmt(used), _fmt(total), _fmt(ceiling)),
              "Minutes since the last watch: %s" % watch]
    return "\n".join(lines)


def table(run_dir):
    """The Markdown status table for the run; writes nothing."""
    rows, used, total, ceiling = _compute(run_dir)
    return _render(rows, used, total, ceiling, _last_watch(run_dir))


def _append(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(text)


def _rung_records(run_dir, stage):
    p = os.path.join(run_dir, "logs", "recovery.jsonl")
    if not os.path.isfile(p):
        return 0
    n = 0
    try:
        with open(p, encoding="utf-8") as f:
            for line in f:
                if line.strip() and json.loads(line).get("stage") == stage:
                    n += 1
    except (OSError, ValueError, AttributeError) as e:
        raise ValueError("logs/recovery.jsonl cannot be read (%s)" % e)
    return n


def next_rung(run_dir, stage):
    """Record and return the stage's next rung: the ladder in order, then hard stop for good."""
    rung = RUNGS[min(_rung_records(run_dir, stage), len(RUNGS) - 1)]
    m = (_state(run_dir).get(stage) or {}).get("minutes")
    rec = {"stage": stage, "minutes": m if isinstance(m, (int, float)) else None,
           "target": target(run_dir, stage), "rung": rung}
    _append(os.path.join(run_dir, "logs", "recovery.jsonl"), json.dumps(rec) + "\n")
    return rung


# The person's view groups the stages into at most 10 plain rows; a stage not listed here joins "Final checks".
GROUPS = [("Reading your inputs", ["intake"]),
          ("Agent shape", ["architecture", "check-architecture", "review-a"]),
          ("Boundaries", ["boundaries"]),
          ("Rules", ["rules", "placement"]),
          ("Fields", ["fields", "supply-check", "review-b"]),
          ("Test cases", ["test-suite", "expected-values"]),
          ("Simulation", ["simulation"]),
          ("Final checks", ["coherence-gate", "register"]),
          ("Your page", ["page-prep", "page", "post-run"]),
          ("Hand-over", ["handoff"])]
MAX_ROWS = 10


def _ts(value):
    try:
        t = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def _events(run_dir):
    out = []
    p = os.path.join(run_dir, "events.jsonl")
    try:
        with open(p, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    try:
                        ev = json.loads(line)
                    except ValueError:
                        continue   # an unreadable line is left to the builder view
                    if isinstance(ev, dict):
                        out.append(ev)
    except OSError:
        pass
    return out


def _group_status(statuses):
    if all(s == "pending" for s in statuses):
        return "Not started"
    if any(str(s).startswith("failed") for s in statuses):
        return "Could not finish"
    if all(str(s).startswith("done") for s in statuses):
        return "Done, with notes" if any(s != "done" for s in statuses) else "Done"
    return "In progress"


def wall_minutes(run_dir, state=None, now=None):
    """Wall-clock minutes since the run started (the first event, else state.started_at); for a finished run, up to
    its last event other than a status watch; None when no start is recorded."""
    evs = _events(run_dir)
    start = None
    for ev in evs:
        start = _ts(ev.get("at"))
        if start:
            break
    state = state if state is not None else _load(os.path.join(run_dir, "state.json"), "state.json")
    if start is None:
        start = _ts(state.get("started_at"))
    if start is None:
        return None
    stages = state.get("stages") or {}
    finished = bool(stages) and all(str((v or {}).get("status", "pending")).startswith(("done", "failed"))
                                    for v in stages.values())
    end = None
    if finished:
        for ev in reversed(evs):
            if ev.get("event") != "watch" and _ts(ev.get("at")):
                end = _ts(ev.get("at"))
                break
    if end is None:
        end = now or datetime.now(timezone.utc)
    return max(0, int(round((end - start).total_seconds() / 60.0)))


REPO = os.path.dirname(os.path.dirname(SKILL))
PAGE = "outputs/agent-design.html"
PROBLEMS = "builder/problems.jsonl"
POST_NAME = "agent-design-status"
# What a recorded problem means for the person, by its kind; {row} is the row of the stage it was recorded at. Any
# other kind gets the general sentence. Never the problem's own detail, which can hold ids, check names or paths.
NOTE_WORDS = {
    "check-failed": "{row}: a check still found something after one repair; it is kept for review.",
    "screen-flag": "{row}: some wording was flagged for a second look.",
    "check-defect": "{row}: one check could not run as written, so it was judged by hand.",
    "fallback-verdict": "{row}: an item judged by hand was left open.",
    "missing-output": "{row}: part of this step was not written.",
    "retry": "{row}: this step needed a second try.",
    "command-exit": "{row}: a step reported an error and the run carried on.",
    "outputs-changed": "{row}: some files were changed after the step finished and are kept as they are.",
    "page-hook": "Your page could not be updated after {row_lower}; it catches up at the next step.",
    "customer-status": "The progress table could not be drawn once; the run carried on.",
    "builder-status": "The progress record could not be drawn once; the run carried on.",
    "state-shape": "The progress record held something unexpected; the run carried on.",
    "limits-boundary": "The time record could not be updated once; the run carried on.",
}
NOTE_GENERAL = "{row}: a note was recorded for the people looking after the run."


def _shared(name):
    """A module from the plugin's knowledge folder (knowledge/page/progressive/<name>.py), found through the one
    lookup (skills/fast-mode-build/driver/knowledge_root.py)."""
    import importlib.util

    def load(mod_name, path):
        spec = importlib.util.spec_from_file_location(mod_name, path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    lookup = load("knowledge_root", os.path.join(REPO, "skills", "fast-mode-build", "driver", "knowledge_root.py"))
    return load("ad_shared_" + name, os.path.join(lookup.find(), "page", "progressive", name + ".py"))


def _groups():
    order = [k for k, _ in _index()]
    known = set(k for _, ks in GROUPS for k in ks)
    groups = [(name, [k for k in ks if k in order]) for name, ks in GROUPS]
    extra = [k for k in order if k not in known]
    groups = [(n, ks + (extra if n == "Final checks" else [])) for n, ks in groups]
    return [(n, ks) for n, ks in groups if ks][:MAX_ROWS]


def _problems(run_dir):
    out = []
    try:
        with open(os.path.join(run_dir, PROBLEMS), encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    try:
                        p = json.loads(line)
                    except ValueError:
                        continue   # an unreadable line is left to the builder view
                    if isinstance(p, dict):
                        out.append(p)
    except OSError:
        pass
    return out


def notes(run_dir, groups=None):
    """What the notes are, in plain words: one sentence per recorded problem kind and row, drawn from the problems
    the run recorded (builder/problems.jsonl), never their detail text."""
    groups = groups if groups is not None else _groups()
    row_of = dict((k, n) for n, ks in groups for k in ks)
    out = []
    for p in _problems(run_dir):
        row = row_of.get(p.get("stage")) or "The run"
        words = NOTE_WORDS.get(p.get("kind"), NOTE_GENERAL)
        line = words.format(row=row, row_lower=row[:1].lower() + row[1:])
        if line not in out:
            out.append(line)
    return out


def upstream_open(stages):
    """The unresolved upstream write-grant problems the checks counted (the builder table's upstream line)."""
    n = 0
    for s in stages.values():
        counts = s.get("counts") if isinstance(s, dict) else None
        grants = counts.get("check_grants") if isinstance(counts, dict) else None
        if isinstance(grants, dict):
            try:
                n += int(grants.get("upstream_unresolved") or 0)
            except (TypeError, ValueError):
                continue
    return n


def _overrun(run_dir, stages, groups, now):
    """The long-stage line when the running stage is past its time target, else None."""
    for name, ks in groups:
        for k in ks:
            s = stages.get(k) or {}
            if not isinstance(s, dict) or s.get("status") != "running":
                continue
            started = _ts(s.get("started_at"))
            try:
                tgt = target(run_dir, k)
            except ValueError:
                tgt = None
            if started is None or tgt is None:
                return None
            minutes = ((now or datetime.now(timezone.utc)) - started).total_seconds() / 60.0
            try:
                return _shared("status").overrun_line(name, minutes, tgt)
            except Exception:  # noqa: BLE001 - no long-stage line rather than no status
                return None
    return None


def _page_line(run_dir):
    """The person's page line, only when the page exists."""
    dest = os.path.join(run_dir, PAGE)
    if not os.path.isfile(dest):
        return None
    try:
        with open(os.path.join(run_dir, "builder", "live-page.json"), encoding="utf-8") as f:
            entry = json.load(f).get(os.path.basename(PAGE)) or {}
    except (OSError, ValueError, AttributeError):
        entry = {}
    try:
        return _shared("live").page_line(dest, entry.get("opened"), entry.get("mode") or "live")
    except Exception:  # noqa: BLE001 - the path alone still tells the person where the page is
        return "Your page: %s (open it in any browser)." % dest


def _view(run_dir, now=None, clock=True):
    state = _load(os.path.join(run_dir, "state.json"), "state.json")
    stages = state.get("stages") if isinstance(state, dict) else None
    if not isinstance(stages, dict):
        raise ValueError("state.json has no stages object")
    groups = _groups()
    seen = []
    any_notes = False
    for name, ks in groups:
        sts = [str((stages.get(k) or {}).get("status", "pending")) for k in ks]
        g = _group_status(sts)
        any_notes = any_notes or g in ("Done, with notes", "Could not finish")
        seen.append((name, g))
    failed = [n for n, g in seen if g == "Could not finish"]
    active = ([n for n, g in seen if g == "In progress"] + [n for n, g in seen if g == "Not started"] + [None])[0]
    mins = wall_minutes(run_dir, state, now) if clock else None
    if not clock:
        took = "Since the run started"
    elif mins is None:
        took = "Start time not recorded"
    else:
        took = "%d minute%s since the run started" % (mins, "" if mins == 1 else "s")
    if active is None:
        note = ("Finished: %s." % took.replace("since the run started", "from start to finish")) if clock else "Finished."
    else:
        note = "%s. Now working on: %s." % (took, active)
    if failed:
        note += " Could not finish: %s." % ", ".join(failed)
    if any_notes:
        note += " Anything left open is listed on the page under What we still need to know."
    procs = 1
    try:
        procs = _processes(run_dir, "status", True)
    except ValueError:
        pass
    if procs > 1:
        note += " The design covers %d processes." % procs
    page = _page_line(run_dir)
    listed = notes(run_dir, groups)
    up = upstream_open(stages)
    over = _overrun(run_dir, stages, groups, now)
    try:
        status = _shared("status")
    except Exception:  # noqa: BLE001 - without the shared formatter, the same rows in the same plain table
        lines = ["**Agent Design progress**", "", "| Stage | Status |", "|---|---|"]
        lines += ["| %s | %s |" % (n, g) for n, g in seen] + ["", note]
        lines += [x for x in (over, page) if x]
        return "\n".join(lines)
    return status.customer_block("Agent Design progress", seen, note, page_line=page, notes=listed,
                                 upstream_open=up, overrun=over)


def customer_view(run_dir, now=None):
    """The status the person sees; reads only. The plain rows and the note line, then what any notes are (from the
    run's recorded problems, in plain words), the unresolved upstream count, a long-stage line when the running
    stage is past its target, and the page line once the page exists."""
    return _view(run_dir, now)


def customer_post(run_dir, now=None):
    """The person's status block when it changed since the last post, else None (post nothing). The comparison
    leaves out the minutes clock, which moves on every call; the record is builder/status-posts.json."""
    block = _view(run_dir, now)
    stable = _view(run_dir, now, clock=False)
    try:   # every stage's status and every recorded problem count as a change, not only what the table shows
        state = _load(os.path.join(run_dir, "state.json"), "state.json")
        probs = []
        p = os.path.join(run_dir, "builder", "problems.jsonl")
        if os.path.isfile(p):
            with open(p, encoding="utf-8") as f:
                probs = [l for l in f if l.strip()]
        stable += "\n" + json.dumps([{k: (v or {}).get("status") if isinstance(v, dict) else v
                                       for k, v in (state.get("stages") or {}).items()}, len(probs)], sort_keys=True)
    except Exception:  # noqa: BLE001 - the plain view alone is compared
        pass
    try:
        changed = _shared("status").post_if_changed(os.path.join(run_dir, "builder"), POST_NAME, stable)
    except Exception:  # noqa: BLE001 - with no record of the last post, post it
        return block
    return None if changed is None else block


def builder_view(run_dir):
    """The builder's status table: check names, attempts, counters and recovery steps."""
    return table(run_dir)


def boundary(run_dir, stage):
    """Builder table to status.md, then any recovery record. Time targets guide; they never stop the run. Only a
    limit the person set (state.json `constraints.time_limit_minutes`) writes REMAINING.md and raises
    CeilingReached. Past 1.5 times the summed targets with no such limit, a note is recorded and the run goes on.
    Prints nothing: the driving script prints customer_view to the person once the stage is recorded."""
    rows, used, total, ceiling = _compute(run_dir)
    if stage not in [r["stage"] for r in rows]:
        raise ValueError("%s is not a stage in stages/index.json" % stage)
    text = _render(rows, used, total, ceiling, _last_watch(run_dir))
    _append(os.path.join(run_dir, "status.md"), "\n## After %s\n\n%s\n" % (stage, text))
    row = [r for r in rows if r["stage"] == stage][0]
    m = row["minutes"] if isinstance(row["minutes"], (int, float)) else None
    if _over(m, row["target"]):
        rung = next_rung(run_dir, stage)
        _append(os.path.join(run_dir, "status.md"), "\n%s is over time (%s of %s minutes): recovery rung %s\n"
                % (stage, _fmt(m), _fmt(row["target"]), rung))
    # An explicit limit from the person's run constraints; anything unreadable there is no limit.
    limit = None
    try:
        with open(os.path.join(run_dir, "state.json"), encoding="utf-8") as f:
            constraints = (json.load(f) or {}).get("constraints")
        raw = constraints.get("time_limit_minutes") if isinstance(constraints, dict) else None
        if raw is not None and not isinstance(raw, bool):
            limit = float(raw)
            limit = limit if limit > 0 else None
    except (OSError, ValueError, TypeError, AttributeError):
        limit = None
    if limit is None:
        if used >= ceiling:
            _append(os.path.join(run_dir, "status.md"),
                    "\nThe run has used %s minutes, past 1.5 times its summed targets (%s minutes). Recorded; the run "
                    "continues because no time limit was set in the run constraints.\n" % (_fmt(used), _fmt(ceiling)))
        return
    if used >= limit:
        lines = ["# Remaining stages", "",
                 "The run stopped at %s minutes, at or past the time limit of %s minutes set in its run constraints."
                 % (_fmt(used), _fmt(limit)), "", "| Stage | Status | Target |", "|---|---|---|"]
        for r in rows:
            if not str(r["status"]).startswith("done"):
                lines.append("| %s | %s | %s |" % (r["stage"], r["status"],
                                                   "none" if r["target"] is None else _fmt(r["target"])))
        with open(os.path.join(run_dir, "REMAINING.md"), "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        raise CeilingReached("the run has used %s minutes, at or past the time limit of %s minutes set in its run "
                             "constraints; the stages not yet done are listed in REMAINING.md"
                             % (_fmt(used), _fmt(limit)))
