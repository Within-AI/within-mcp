#!/usr/bin/env python3
"""Review B fix round: one verdict, at most one fix round through the owning stages (rules, placement, fields).

    fix_round.py plan|apply-known|settle --run RUN

Order: plan, then apply-known, then the fixes (the top-level skill carries out each
stages/review-b/fix/<stage>.md), then settle. settle writes in fix-round.json `after`: the file hashes of each owning
stage the round kept, which `ad.py finish --stage review-b` records as the accepted repair. Standard library only;
no session is launched here.

A later Review B pass in the same run (the stage restarted after a later route invalidated it) finds the round already
used: plan, apply-known and settle then exit 0, apply nothing, keep the earlier round's record, add a `rechecks` entry
and record one problem (see check_only). No second set of fixes is applied in a run.
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import uuid
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(os.path.dirname(HERE))
REPO = os.path.dirname(os.path.dirname(SKILL))
sys.path.insert(0, os.path.join(SKILL, "scripts"))
sys.path.insert(0, os.path.join(SKILL, "formats"))
import expr  # noqa: E402
import evidence_ids  # noqa: E402
import readers  # noqa: E402

STAGES = ("rules", "placement", "fields")
CHECKS = {"rules": ["check_rules", "check_outcomes"], "placement": ["check_placement"], "fields": ["supply_check"]}
REPO_GUARDED = ("skills/agent-design/checks", "skills/agent-design/config", "skills/agent-design/formats")
FIXES = "stages/review-b/fixes.json"      # the fixers' per-finding record: [{finding_id, files, summary}]
SCRATCH = "stages/review-b/scratch"       # where the guide sends every check run (--out); settle ignores it
GENERATED = ("checks/",)                  # generated reports in RUN: never a fix, never compared by settle
RB = "stages/review-b"
VERDICT_REQUIRED = ("id", "subject", "verdict", "evidence", "detail", "route_to")
CHECK_TIMEOUT = 600


class FixRoundError(Exception):
    """A refusal with a plain message."""


class VerdictError(FixRoundError):
    """The verdict is unreadable or an item lacks a required field."""


class PlanRefused(FixRoundError):
    """plan may not run (no supply check report, or a round already exists)."""


class StatusError(FixRoundError):
    """apply-known or settle called when fix-round.json is not planned."""


# ---------- small helpers

def _p(run_dir, rel):
    return os.path.join(run_dir, *rel.split("/"))


def _load(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _dump(path, data):
    for rel in ROUTE_FILES:   # a route file ad.py reads is shape-checked where it is written
        tail = os.sep + os.path.join(*rel.split("/"))
        if path.endswith(tail):
            shape = route_shape_problems(rel, data)
            if shape:
                _reject_route(path[:-len(tail)], rel, data, shape)
                return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, sort_keys=False)
        fh.write("\n")


def _sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _files(root):
    out = []
    for base, dirs, names in os.walk(root):
        dirs.sort()
        for n in sorted(names):
            if n.endswith(".pyc") or "__pycache__" in base:
                continue
            out.append(os.path.relpath(os.path.join(base, n), root).replace(os.sep, "/"))
    return out


def _run_files(run_dir):
    return [f for f in _files(run_dir) if not (f == RB or f.startswith(RB + "/") or f.startswith(GENERATED))]


def _repo_files():
    out = []
    for d in REPO_GUARDED:
        root = os.path.join(REPO, *d.split("/"))
        if os.path.isdir(root):
            out.extend(d + "/" + f for f in _files(root))
    return out


def _safe_rel(rel):
    return (isinstance(rel, str) and rel and not rel.startswith(("/", "~")) and "\\" not in rel
            and ".." not in rel.split("/") and not (len(rel) > 1 and rel[1] == ":"))


def _split_ref(ref):
    path, _, pointer = ref.partition("#")
    return path, pointer


def _pointer_parts(pointer):
    if not pointer:
        return []
    if not pointer.startswith("/"):
        raise FixRoundError("JSON pointer must start with '/': " + pointer)
    return [p.replace("~1", "/").replace("~0", "~") for p in pointer[1:].split("/")]


def _resolve(doc, parts):
    cur = doc
    for p in parts:
        if isinstance(cur, list):
            cur = cur[int(p)]
        elif isinstance(cur, dict):
            cur = cur[p]
        else:
            raise KeyError(p)
    return cur


def _leaves(doc):
    if isinstance(doc, dict):
        for v in doc.values():
            yield from _leaves(v)
    elif isinstance(doc, list):
        for v in doc:
            yield from _leaves(v)
    else:
        yield doc


def _same(a, b):
    return type(a) is type(b) and a == b


def _state(run_dir):
    path = _p(run_dir, RB + "/fix-round.json")
    if not os.path.isfile(path):
        raise StatusError("no fix round planned for this run: run `fix_round.py plan --run RUN` first")
    state = _load(path)
    if state.get("status") != "planned":
        raise StatusError("fix round status is %r, not planned: one fix round per run" % state.get("status"))
    return state


# ---------- route shapes: what ad.py's pending_routes reads from the route files Review B writes

ROUTE_FILES = ("stages/review-b/fix-round.json", "stages/review-b/route-placement.json")
WRONG_SHAPE = ".wrong-shape.json"   # where a wrong-shaped route is kept as a record; ad.py never reads it


def _str(v):
    return isinstance(v, str) and v != ""


def route_shape_problems(rel, doc):
    """The fields ad.py reads from a Review B route file, with the types it needs; [] when the shape is right.
    fix-round.json: an object; `open` a list of finding ids (strings); `findings` a list whose objects carry a string
    `id` and, when set, a string `stage` and `owner_stage`. route-placement.json: an object naming stage `placement`
    and a `rules` list."""
    if not isinstance(doc, dict):
        return ["the route is not an object"]
    out = []
    if rel.endswith("route-placement.json"):
        if doc.get("stage") != "placement":
            out.append("stage is not placement")
        if not isinstance(doc.get("rules"), list):
            out.append("rules is not a list")
        return out
    if not isinstance(doc.get("open", []), list):
        out.append("open is not a list")
    else:
        out += ["open[%d] is not a finding id string" % i for i, f in enumerate(doc.get("open") or []) if not _str(f)]
    if doc.get("findings") is not None and not isinstance(doc.get("findings"), list):
        out.append("findings is not a list")
    else:
        for i, f in enumerate(doc.get("findings") or []):
            if not isinstance(f, dict):
                out.append("findings[%d] is not an object" % i)
                continue
            if not _str(f.get("id")):
                out.append("findings[%d].id is not a string" % i)
            for k in ("stage", "owner_stage"):
                if f.get(k) is not None and not isinstance(f.get(k), str):
                    out.append("findings[%d].%s is not a string" % (i, k))
    return out


def _reject_route(run_dir, rel, data, shape):
    """A wrong-shaped route: record one problem in builder/problems.jsonl in ad.py's form, keep the document beside it
    as a record (<name>.wrong-shape.json) and leave no route file for ad.py."""
    path = _p(run_dir, rel)
    if os.path.exists(path):
        os.remove(path)
    keep = path[:-len(".json")] + WRONG_SHAPE
    os.makedirs(os.path.dirname(keep), exist_ok=True)
    with open(keep, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, default=repr)
        fh.write("\n")
    _record_problem(run_dir, "route-shape", rel, "the route %s has the wrong shape (%s); it is not left for ad.py, "
                    "its record is %s" % (rel, "; ".join(shape), os.path.relpath(keep, run_dir).replace(os.sep, "/")))


def _record_problem(run_dir, kind, item, detail):
    _, _, attempts = _stage_start(run_dir)
    rec = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "kind": kind, "item": item,
           "detail": detail, "stage": STAGE_KEY, "revision": attempts}
    plog = _p(run_dir, PROBLEMS)
    os.makedirs(os.path.dirname(plog), exist_ok=True)
    with open(plog, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec) + "\n")


# ---------- a later pass: the run's one fix round is already used

PROBLEMS = "builder/problems.jsonl"       # ad.py's log of recorded problems: {at, kind, item, detail, stage, revision}
STAGE_KEY = "review-b"


def _stage_start(run_dir):
    """(started_at, attempts) of this Review B stage from ad.py's state.json, or (None, None) when unknown."""
    try:
        st = _load(_p(run_dir, "state.json"))
        s = st["stages"][STAGE_KEY]
        started = s["started_at"]
        ts = datetime.fromisoformat(started).timestamp()
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None, None, None
    return started, ts, s.get("attempts")


RECHECK_BEFORE = RB + "/recheck-before"  # the stage folders as a check-only pass found them; settle restores them
USED_FIX = RB + "/fix-used"              # the used round's fix prompts, moved out of fix/ so no fixer is dispatched


def _this_pass(started, attempts):
    return {"stage_started_at": started, "revision": attempts}


def _same_pass(rec, started, attempts):
    return isinstance(rec, dict) and rec.get("stage_started_at") == started and rec.get("revision") == attempts


def _round_path(run_dir):
    """Where this run's fix-round state lives: fix-round.json, else the wrong-shape record kept beside it (the used
    round's state, which ad.py never reads), else fix-round.json (not yet written)."""
    path = _p(run_dir, RB + "/fix-round.json")
    record = _p(run_dir, RB + "/fix-round" + WRONG_SHAPE)
    if not os.path.isfile(path) and os.path.isfile(record):
        return record
    return path


def _open_measure(run_dir, state):
    """How much of the round's work is still open now: findings no fixer has claimed in fix/<stage>.done.json, and the
    problems the owning stages' checks report (a check result with unknown problems counts as one). Progress between
    attempts is a smaller total than the last attempt's; an edit that resolves nothing is not progress."""
    claimed = set()
    stages = [st for st in state.get("stages") or [] if isinstance(st, str) and st in STAGES]
    for stage in stages:
        try:
            got = _load(_p(run_dir, RB + "/fix/%s.done.json" % stage)).get("fixed") or []
        except (OSError, ValueError, AttributeError):
            got = []
        claimed.update(x for x in got if isinstance(x, str))
    fixed = set(state.get("fixed") or [])
    unclaimed = [f.get("id") for f in state.get("findings") or [] if isinstance(f, dict) and "open_reason" not in f
                 and f.get("id") not in claimed and f.get("id") not in fixed]
    problems = 0
    for stage in stages:
        if not os.path.isdir(_p(run_dir, "stages/" + stage)):
            continue
        for found in _stage_problems(_stage_checks(run_dir, stage)).values():
            problems += len(found) if isinstance(found, list) else 1
    return {"unclaimed_findings": len(unclaimed), "check_problems": problems, "open": len(unclaimed) + problems}


def _stage_hashes(run_dir, state):
    """{stage: {file: sha256}} of the round's owning stage folders as they are now (progress between attempts)."""
    out = {}
    for stage in state.get("stages") or []:
        if not isinstance(stage, str) or stage not in STAGES:
            continue
        root = _p(run_dir, "stages/" + stage)
        out[stage] = {f: _sha(os.path.join(root, *f.split("/"))) for f in _files(root)} if os.path.isdir(root) else {}
    return out


def _refresh_rest(run_dir):
    """A resumed round re-baselines the run files outside the stage folders (the restart itself wrote state.json,
    events and prompts; settle must not revert those). The owning stage folders keep the round's original baseline."""
    before = _p(run_dir, RB + "/before")
    mpath = os.path.join(before, "manifest.json")
    try:
        manifest = _load(mpath)
    except (OSError, ValueError):
        return
    if not isinstance(manifest, dict):
        return
    rest = os.path.join(before, "rest")
    if os.path.exists(rest):
        shutil.rmtree(rest)
    manifest["run"] = {}
    for rel in _run_files(run_dir):
        manifest["run"][rel] = _sha(_p(run_dir, rel))
        dst = os.path.join(rest, *rel.split("/"))
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(_p(run_dir, rel), dst)
    _dump(mpath, manifest)


def _restore_prompts(run_dir, state):
    """A resumed round offers its fix prompts again when an earlier check-only pass moved them to fix-used/."""
    fixdir, used = _p(run_dir, RB + "/fix"), _p(run_dir, USED_FIX)
    if os.path.isdir(fixdir) or not os.path.isdir(used):
        return
    shutil.move(used, fixdir)


def check_only(run_dir, record=True):
    """The fix-round state when this Review B pass may only check, else None (a first pass, or the same pass).

    A pass is identified by ad.py's started_at and attempts (revision) for the Review B stage. plan records the pass
    that wrote fix-round.json under `pass`; a later pass (another revision or start) finds the round already used. For
    a fix-round.json without `pass`, the file counts as earlier only when its whole-second mtime is before started_at
    (which ad.py records to the second); a write in the same second keeps the old refusal. The check-only pass applies
    nothing, keeps the earlier round's record under its existing keys, adds a `rechecks` entry, rewrites the file (so it
    is newer than the stage start), moves the used round's fix/ prompts to fix-used/ (so none is dispatched again),
    copies the owning stage folders to recheck-before/ (settle restores them) and records one problem in
    builder/problems.jsonl in ad.py's form. A restarted stage whose round is still `planned` (never settled) resumes it
    with fixes allowed (ruled 0494), judged against the round's before-snapshot; a repeat resume with no less open work
    than the last (unclaimed findings plus stage-check problems) made no progress, so the repair loop ends: the round
    becomes `settled-with-gaps` (never reopened; the owning stages go back to its baseline), this pass checks only, and
    each still-open finding is recorded as a named draft gap (kind `draft-gap`); the run continues. A planned round with no
    before-snapshot cannot be judged, so it checks only as before.

    A fix round whose record had the wrong shape (fix-round.wrong-shape.json, no fix-round.json) counts as used in
    every pass, the one that wrote it included: each pass checks only, keeping its state in that record, so plan,
    apply-known and settle all complete and none applies a fix. `record=False` skips the problem line (plan has just
    recorded the route-shape problem for the same pass)."""
    path = _round_path(run_dir)
    wrong = path.endswith(WRONG_SHAPE)
    if not os.path.isfile(path):
        return None
    started, ts, attempts = _stage_start(run_dir)
    if started is None and not wrong:
        return None
    try:
        state = _load(path)
    except ValueError:
        return None
    if not isinstance(state, dict):
        return None
    rechecks = state.get("rechecks") if isinstance(state.get("rechecks"), list) else []
    if any(_same_pass(r, started, attempts) for r in rechecks):
        _dump(path, state)
        return state
    if wrong:
        pass   # a wrong-shaped round is used in every pass
    elif isinstance(state.get("pass"), dict):
        if _same_pass(state["pass"], started, attempts):
            return None
    elif int(os.path.getmtime(path)) >= ts:
        return None
    found = state.get("status")
    detail = ("this run's one fix round was already used (fix round status %r, written by an earlier Review B pass); "
              "this pass checks only and applies no fixes" % (found,))
    if not wrong and found == "planned" and os.path.isdir(_p(run_dir, RB + "/before")):
        # A round planned by an earlier pass that never settled is unfinished work, not a used round: it resumes with
        # fixes allowed, judged against the same before-snapshot. Repeats are bounded by progress: a resume is allowed
        # when it is the first, or when less work is open than at the last resume (_open_measure).
        resumes = state.get("resumes") if isinstance(state.get("resumes"), list) else []
        measure = _open_measure(run_dir, state)
        last = resumes[-1].get("measure") if resumes and isinstance(resumes[-1], dict) else None
        if not resumes or not isinstance(last, dict) or measure["open"] < last.get("open", 0):
            state["resumes"] = resumes + [dict(_this_pass(started, attempts), measure=measure,
                                               stage_hashes=_stage_hashes(run_dir, state))]
            state["pass"] = _this_pass(started, attempts)
            _dump(path, state)
            _restore_prompts(run_dir, state)
            if record:
                _record_problem(run_dir, "fix-round-resumed", RB + "/fix-round.json",
                                "the fix round an earlier Review B pass planned never settled; this pass resumes it "
                                "with fixes allowed (attempt %d)" % (len(resumes) + 2))
            _refresh_rest(run_dir)
            return None
        gaps = [f.get("id") for f in state.get("findings") or [] if isinstance(f, dict)
                and "open_reason" not in f and f.get("id") not in (state.get("fixed") or [])]
        state["draft_gaps"] = gaps
        # Exhausted: the round is closed with its gaps so no later pass reopens it, and the owning stages go back to
        # the round's baseline (an earlier attempt's edits were never judged, so none is kept).
        state["status"] = "settled-with-gaps"
        state["exhausted"] = dict(_this_pass(started, attempts), measure=measure, last_measure=last)
        before_dir = _p(run_dir, RB + "/before")
        for stage in state.get("stages") or []:
            if isinstance(stage, str) and stage in STAGES and os.path.isdir(os.path.join(before_dir, stage)):
                _restore_stage(run_dir, before_dir, stage)
        detail = ("the unfinished fix round made no progress since its last attempt (%d open, %s before); the repair "
                  "loop ends here, the round is settled with gaps, %d open finding(s) are recorded as draft gaps and "
                  "the run continues; this pass checks only and applies no fixes"
                  % (measure["open"], last.get("open") if isinstance(last, dict) else "unknown", len(gaps)))
        if record:
            subjects = {f.get("id"): f.get("subject") for f in state.get("findings") or [] if isinstance(f, dict)}
            for fid in gaps:
                _record_problem(run_dir, "draft-gap", fid, "Review B finding %s (%s) stays open: the repair made no "
                                "progress between attempts; it is a named gap in this draft" % (fid, subjects.get(fid)))
    if wrong:
        detail = ("this run's one fix round is used: its record had the wrong shape (%s/fix-round%s); this pass checks "
                  "only and applies no fixes" % (RB, WRONG_SHAPE))
    withdrawn = []
    fixdir = _p(run_dir, RB + "/fix")
    if os.path.isdir(fixdir):
        withdrawn = [RB + "/fix/" + n for n in _files(fixdir)]
        used = _p(run_dir, USED_FIX)
        if os.path.exists(used):
            shutil.rmtree(used)
        shutil.move(fixdir, used)
    snap = _p(run_dir, RECHECK_BEFORE)
    if os.path.exists(snap):
        shutil.rmtree(snap)
    os.makedirs(snap)   # a stage folder absent here was absent at the snapshot: settle treats it as empty
    for stage in STAGES:
        if os.path.isdir(_p(run_dir, "stages/" + stage)):
            shutil.copytree(_p(run_dir, "stages/" + stage), os.path.join(snap, stage))
    state["rechecks"] = rechecks + [dict(_this_pass(started, attempts), status_found=found, applied=[],
                                         prompts_withdrawn=withdrawn, detail=detail)]
    _dump(path, state)  # rewritten by this pass; every earlier key is kept as it was
    if not record:
        return state
    rec = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "kind": "fix-round-used",
           "item": RB + "/fix-round.json", "detail": detail, "stage": STAGE_KEY, "revision": attempts}
    plog = _p(run_dir, PROBLEMS)
    os.makedirs(os.path.dirname(plog), exist_ok=True)
    with open(plog, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec) + "\n")
    return state


def _restore_checked(run_dir, state):
    """settle in a check-only pass: put back any edit made to the stage folders since the pass began; record it."""
    snap = _p(run_dir, RECHECK_BEFORE)
    restored = []
    for stage in STAGES:
        before, now = os.path.join(snap, stage), _p(run_dir, "stages/" + stage)
        if not os.path.isdir(snap):
            continue   # no snapshot at all: what the pass found is not known, so nothing is removed
        names = _changed(before, now)   # a folder absent at the snapshot is an empty baseline
        _copy_files(before, now, names)
        restored += ["stages/%s/%s" % (stage, n) for n in names]
        if not os.path.isdir(before) and os.path.isdir(now):
            shutil.rmtree(now)   # the folder itself was created during the pass
    if restored and state.get("rechecks"):
        last = state["rechecks"][-1]
        last["restored"] = sorted(set(last.get("restored") or []) | set(restored))
        _dump(_round_path(run_dir), state)
    return dict(_checked_only(state), restored=restored)


def _checked_only(state):
    return dict(state, check_only=True, applied=[], refused={})


# ---------- checks (tests replace run_check with stub reports)

class CheckResult(str):
    """A check result ('pass', 'fail' or 'error') that also carries the problems the check reported, as
    [{"defect", "where"}]; None when they are not known."""

    def __new__(cls, result, problems=None):
        obj = str.__new__(cls, result)
        obj.problems = None
        if isinstance(problems, list):
            obj.problems = [{"defect": p.get("defect"), "where": p.get("where")} for p in problems
                            if isinstance(p, dict)]
        return obj


def _problems_of(result):
    """The {defect, where} problems a check result carries: [] for a pass, None when not known."""
    if result == "pass":
        return []
    probs = getattr(result, "problems", None)
    return list(probs) if isinstance(probs, list) else None


def run_check(run_dir, name):
    """Run one check and return 'pass', 'fail' or 'error' (exit 2, no report or timeout count as not passing), as a
    CheckResult carrying the report's problems."""
    script = os.path.join(SKILL, "checks", name + ".py")
    # Each invocation owns a fresh path. Never reuse stale reports or delete failed evidence.
    folder = os.path.join(run_dir, "checks", "review-b", name, uuid.uuid4().hex)
    os.makedirs(folder, exist_ok=True)
    out = os.path.join(folder, "report.json")
    record = {"check": name, "report": os.path.relpath(out, run_dir)}
    try:
        proc = subprocess.run([sys.executable, script, "--run", run_dir, "--out", out],
                              capture_output=True, text=True, timeout=CHECK_TIMEOUT,
                              env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1", ADK_NO_OPEN="1"))
        record.update(exit_code=proc.returncode, stdout=proc.stdout, stderr=proc.stderr)
        report = _load(out)
        probs = report.get("problems") if isinstance(report, dict) else None
        if proc.returncode == 0 and isinstance(report, dict) and report.get("result") == "pass":
            result = CheckResult("pass", [])
        else:
            result = CheckResult("fail" if proc.returncode == 1 else "error",
                                 probs if proc.returncode == 1 else None)
    except (subprocess.TimeoutExpired, OSError, ValueError) as exc:
        record["error"] = str(exc)
        result = CheckResult("error")
    record["result"] = str(result)
    if os.path.isfile(out):
        with open(out, "rb") as fh:
            record["sha256"] = hashlib.sha256(fh.read()).hexdigest()
    with open(os.path.join(folder, "invocation.json"), "w", encoding="utf-8") as fh:
        json.dump(record, fh, indent=2)
    return result


def _stage_checks(run_dir, stage):
    return {name: run_check(run_dir, name) for name in CHECKS[stage]}


def _stage_problems(results):
    """{check: [{defect, where}] or None} for a stage's check results (kept in fix-round.json)."""
    return {name: _problems_of(r) for name, r in results.items()}


# ---------- verdict

def read_verdict(run_dir):
    path = _p(run_dir, RB + "/verdict.json")
    try:
        verdict = _load(path)
    except (OSError, ValueError) as exc:
        raise VerdictError("verdict unreadable: %s/verdict.json (%s)" % (RB, exc.__class__.__name__))
    if not isinstance(verdict, dict):
        raise VerdictError("verdict is not a JSON object: %s/verdict.json" % RB)
    for key in ("kind", "read", "items"):
        if key not in verdict:
            raise VerdictError("verdict lacks required field %r" % key)
    if not isinstance(verdict["items"], list):
        raise VerdictError("verdict field 'items' is not a list")
    for n, item in enumerate(verdict["items"]):
        if not isinstance(item, dict):
            raise VerdictError("verdict item %d is not an object" % n)
        for key in VERDICT_REQUIRED:
            if key not in item:
                raise VerdictError("verdict item %d (%s) lacks required field %r" % (n, item.get("id", "?"), key))
        if item["verdict"] not in ("ok", "finding", "cannot_judge"):
            raise VerdictError("verdict item %s has verdict %r" % (item["id"], item["verdict"]))
        kv = item.get("known_value")
        if kv is not None:
            if not isinstance(kv, dict) or any(k not in kv for k in ("target", "value", "source")):
                raise VerdictError("verdict item %s: known_value needs target, value and source" % item["id"])
    return verdict


# ---------- the 0412 guide

def guide(stage, run_dir="<run>"):
    scratch = os.path.join(run_dir, *SCRATCH.split("/"))
    lines = ["python3 %s --run %s --out %s" % (os.path.join(SKILL, "checks", n + ".py"), run_dir,
                                                os.path.join(scratch, n + ".json")) for n in CHECKS[stage]]
    text = ["## The checks that decide whether your fix is kept",
            "After you finish, settle judges each finding's fix on its own: it runs this stage's own checks with your "
            "fix in place. A fix that makes a check that passed before fail, or that the guards below refuse, is undone "
            "alone and its finding goes to a person as an open item; the other fixes stay. Fixes that edit the same "
            "file are judged together. Run these from the repo root before you finish and fix anything they report "
            "(each writes its report to the scratch folder, never to the run's checks folder):", ""]
    text += ["    " + ln for ln in lines]
    text += ["",
             "Produced-field convention: a rule's expression `reads` lists only the tokens its test reads. A value the rule works "
             "out goes in the expression's `sets`, never in `reads`.",
             "Only a rule closes a finding on an output-side field: on a write, handoff, draft or dispatch field the agent "
             "produces the value, so extending the field list can not close it.",
             "Only a rule already setting a token may close such a finding, with one exception: a rule restoring a lost "
             "rule family may set a new token when the frozen evidence records that token and the rule cites "
             "observation ids that resolve. Settle refuses any other fix that adds a rule setting a token no earlier "
             "rule set, a rule reading a token no earlier rule read, an alias or a stand-in. Leave a finding you can "
             "not close that way open.",
             "An added, changed or moved rule may read a value only where it is already available at the rule's placed step "
             "(stages/placement/placed-rules.json): a read interface at or before that step, the case input, or an output a "
             "rule writes at an earlier step. A read of a value that arrives only later is refused as an early read. A "
             "rule you add has no placed step yet: settle sends it to the placement stage inside this round.",
             "Do not edit anything under `skills/agent-design/checks/`, `skills/agent-design/config/` or "
             "`skills/agent-design/formats/`; such an edit is undone and refuses the fixes. Edits outside "
             "`%s/stages/%s/` are reverted." % (run_dir, stage)]
    return "\n".join(text)


def _fix_prompt(stage, findings, run_dir="<run>"):
    out = ["# Review B fix: %s" % stage, "",
           "You fix the %s stage of the Agent Design run at `%s`. Edit only files under `%s/stages/%s/`."
           % (stage, run_dir, run_dir, stage),
           "", "## Findings to fix", ""]
    for f in findings:
        fam = (" (family: %s)" % f["family"]) if f.get("family") else ""
        out.append("- `%s`%s: %s. %s" % (f["id"], fam, f["subject"], f["detail"]))
    out += ["", guide(stage, run_dir), "",
            "## When you finish",
            "Add one entry per finding you fixed to `%s/%s`, a JSON list shared by every fix in this round (keep the "
            "entries already there): `{\"finding_id\", \"files\" (the run-relative files the fix changed), "
            "\"summary\"}`." % (run_dir, FIXES),
            "Also write `%s/stages/review-b/fix/%s.done.json` as `{\"fixed\": [ids]}`, listing only the finding ids "
            "you fixed." % (run_dir, stage),
            ""]
    return "Read only inside this run's folder and the plugin; never list or open folders beside the run.\n\n" + "\n".join(out)


def _placement_prompt(rules, run_dir):
    return "Read only inside this run's folder and the plugin; never list or open folders beside the run.\n\n" + "\n".join([
        "# Review B fix: placement", "",
        "A kept Review B rules fix added rules that have no placed step yet: %s. Place each on the future-state step "
        "it decides in `%s/stages/placement/placed-rules.json` (and declare any stand-in it needs in "
        "`declarations.json`), following the placement stage prompt `%s`. Edit only files under "
        "`%s/stages/placement/`; change no other placement." % (", ".join(rules), run_dir,
                                                               os.path.join(SKILL, "stages", "placement", "prompt.md"),
                                                               run_dir),
        "", "Then run settle again: `fix_round.py settle --run RUN` (through `ad.py exec --stage review-b --line 3`).",
        ""])


# ---------- plan

def plan(run_dir):
    run_dir = os.path.abspath(run_dir)
    used = check_only(run_dir)
    if used is not None:
        return _checked_only(used)
    if os.path.exists(_p(run_dir, RB + "/fix-round" + WRONG_SHAPE)):
        raise PlanRefused("this run's fix round was already used; its record had the wrong shape (stages/review-b/"
                          "fix-round%s): one fix round per run" % WRONG_SHAPE)
    if os.path.exists(_p(run_dir, RB + "/fix-round.json")):
        try:
            existing = _load(_p(run_dir, RB + "/fix-round.json"))
        except (OSError, ValueError):
            existing = None
        started, _, attempts = _stage_start(run_dir)
        if (isinstance(existing, dict) and existing.get("status") == "planned" and existing.get("resumes")
                and _same_pass(existing["resumes"][-1], started, attempts)):
            return dict(existing, resumed=True)   # the unfinished round, resumed by this pass (see check_only)
        raise PlanRefused("a fix round already exists for this run (stages/review-b/fix-round.json): one fix round per run")
    if not os.path.isfile(_p(run_dir, "checks/supply_check.json")):
        raise PlanRefused("Review B runs after the supply check: checks/supply_check.json is absent")
    verdict = read_verdict(run_dir)
    findings, owning, unroutable = [], [], []
    for item in verdict["items"]:
        if item["verdict"] != "finding":
            continue
        stage = item["route_to"]
        rec = {"id": item["id"], "stage": stage, "family": item.get("family"), "subject": item["subject"],
               "detail": item["detail"], "known": "known_value" in item}
        if "problem" in item:
            rec["problem"] = item["problem"]  # the check problem the finding carries: {defect, where[, check]}
        if stage not in STAGES:
            rec["open_reason"] = "routed to %r, which is not rules, placement or fields" % (stage,)
        elif not os.path.isdir(_p(run_dir, "stages/" + stage)):
            rec["open_reason"] = "owning stage folder stages/%s is missing" % stage
        elif stage not in owning:
            owning.append(stage)
        if "open_reason" in rec:
            unroutable.append(rec["id"])
        findings.append(rec)
    before = _p(run_dir, RB + "/before")
    if os.path.exists(before):
        shutil.rmtree(before)
    for stage in owning:
        shutil.copytree(_p(run_dir, "stages/" + stage), os.path.join(before, stage))
    manifest = {"run": {}, "repo": {}}
    for rel in _run_files(run_dir):
        manifest["run"][rel] = _sha(_p(run_dir, rel))
        dst = os.path.join(before, "rest", *rel.split("/"))
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(_p(run_dir, rel), dst)
    for rel in _repo_files():
        manifest["repo"][rel] = _sha(os.path.join(REPO, *rel.split("/")))
        dst = os.path.join(before, "repo", *rel.split("/"))
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(os.path.join(REPO, *rel.split("/")), dst)
    _dump(os.path.join(before, "manifest.json"), manifest)
    prompts = []
    fixdir = _p(run_dir, RB + "/fix")
    if os.path.exists(fixdir):
        shutil.rmtree(fixdir)
    for stage in owning:
        dispatched = [f for f in findings if f["stage"] == stage and not f["known"] and "open_reason" not in f]
        if dispatched:
            path = os.path.join(fixdir, stage + ".md")
            os.makedirs(fixdir, exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(_fix_prompt(stage, dispatched, run_dir))
            prompts.append(RB + "/fix/" + stage + ".md")
    checks_before = {stage: _stage_checks(run_dir, stage) for stage in owning}
    problems_before = {stage: _stage_problems(checks_before[stage]) for stage in owning}
    state = {"status": "planned", "findings_in": len(findings), "stages": owning, "findings": findings,
             "unroutable": unroutable, "prompts": prompts, "checks_before": checks_before,
             "problems_before": problems_before,
             "known_applied": [], "known_refused": {}, "fixed": [], "open": []}
    started, _, attempts = _stage_start(run_dir)
    if started is not None:
        state["pass"] = _this_pass(started, attempts)  # the Review B pass that used the round (see check_only)
    _dump(_p(run_dir, RB + "/fix-round.json"), state)  # written last: a failed plan leaves none
    if _round_path(run_dir).endswith(WRONG_SHAPE):
        # the state had the wrong shape, so it was kept as a record and no route is left: this pass's round is used
        # without a fix; it goes on check-only (no fixer is dispatched, settle restores the stage folders)
        return _checked_only(check_only(run_dir, record=False))
    return state


# ---------- apply-known

def _holds(run_dir, source, value):
    path, pointer = _split_ref(source)
    if not _safe_rel(path) or not os.path.isfile(_p(run_dir, path)):
        return False
    try:
        doc = _load(_p(run_dir, path))
        if pointer:
            return _same(_resolve(doc, _pointer_parts(pointer)), value)
    except (ValueError, KeyError, IndexError, FixRoundError):
        return False
    return any(_same(leaf, value) for leaf in _leaves(doc))


def apply_known(run_dir):
    run_dir = os.path.abspath(run_dir)
    used = check_only(run_dir)
    if used is not None:
        return _checked_only(used)
    state = _state(run_dir)
    items = {i["id"]: i for i in read_verdict(run_dir)["items"]}
    known = [f for f in state["findings"] if f["known"] and "open_reason" not in f]
    by_target = {}
    for f in known:
        by_target.setdefault(items[f["id"]]["known_value"]["target"], []).append(f["id"])
    applied, refused = [], {}
    for f in known:
        kv = items[f["id"]]["known_value"]
        tpath, tptr = _split_ref(kv["target"])
        if len(by_target[kv["target"]]) > 1:
            refused[f["id"]] = "two known values for the same target %s" % kv["target"]
        elif not (_safe_rel(tpath) and tpath.startswith("stages/%s/" % f["stage"])):
            refused[f["id"]] = "target %s lies outside the owning stage folder stages/%s/" % (kv["target"], f["stage"])
        elif not _holds(run_dir, kv["source"], kv["value"]):
            refused[f["id"]] = "the cited source %s does not hold the value" % kv["source"]
        elif not os.path.isfile(_p(run_dir, tpath)):
            refused[f["id"]] = "target %s needs an existing file" % kv["target"]
        elif not tptr:  # no pointer: the value replaces the whole target file
            _dump(_p(run_dir, tpath), kv["value"])
            applied.append(f["id"])
        else:
            try:
                doc = _load(_p(run_dir, tpath))
                parts = _pointer_parts(tptr)
                if not parts:
                    raise KeyError(tptr)
                parent = _resolve(doc, parts[:-1])
                if isinstance(parent, list):
                    parent[int(parts[-1])] = kv["value"]
                else:
                    parent[parts[-1]] = kv["value"]
            except (ValueError, KeyError, IndexError, TypeError, FixRoundError):
                refused[f["id"]] = "target %s does not resolve" % kv["target"]
                continue
            _dump(_p(run_dir, tpath), doc)
            applied.append(f["id"])
    state["known_applied"], state["known_refused"] = applied, refused
    _dump(_p(run_dir, RB + "/fix-round.json"), state)
    return {"applied": applied, "refused": refused}


# ---------- settle guards

def _rules_doc(path):
    """The rules output through U2's loader (token objects and strings alike); empty when it will not parse."""
    try:
        data = readers.read_json(path, required=False)
    except readers.Unreadable:
        data = None
    if data is None:
        return {"tokens": [], "config": [], "rules": []}
    return readers.parse_rules(data)[0]


def _rules(path):
    return {r.get("id"): r for r in _rules_doc(path)["rules"] if isinstance(r, dict)}


def _sets_reads(rule):
    e = rule.get("expression")
    sets = sorted((e.get("sets") or {}).keys()) if isinstance(e, dict) and isinstance(e.get("sets"), dict) else []
    try:
        reads = expr.reads(e)
    except Exception:  # an unreadable expression reads nothing we can trust
        reads = []
    return sets, reads


def _stand_in_fields(path):
    """The fields in a fields.json that take their value from a stand-in, keyed by token (or id when tokenless)."""
    try:
        doc = _load(path)
    except (OSError, ValueError):
        return []
    out = []
    for f in (doc.get("fields") or []) if isinstance(doc, dict) and isinstance(doc.get("fields"), list) else []:
        if not isinstance(f, dict):
            continue
        tok = f.get("token")
        if (f.get("run_time_source") == "stand_in" or f.get("stand_in")
                or (isinstance(tok, str) and tok.startswith("stand_in."))):
            out.append(tok if isinstance(tok, str) else json.dumps(f.get("id", f), sort_keys=True))
    return out


def _records_with_key(doc, key, out):
    """Every object in one loaded observation file whose resource_key is `key`."""
    if isinstance(doc, dict):
        if doc.get("resource_key") == key:
            out.append(doc)
        for v in doc.values():
            _records_with_key(v, key, out)
    elif isinstance(doc, list):
        for v in doc:
            _records_with_key(v, key, out)
    return out


def _token_in(text, tok):
    """The exact token in text: no letter, digit, underscore or dot-joined part extends it on either side."""
    pat = r"(?<![\w.])" + re.escape(tok) + r"(?![\w]|\.\w)"
    return re.search(pat, text) is not None


def _token_forms(tok):
    """The token itself, and its last dotted part read as words (`period.is_m1` -> `is m1`): structural, no word list."""
    forms = [tok]
    tail = tok.rsplit(".", 1)[-1].replace("_", " ").strip()
    if tail and tail != tok and len(tail) > 2:
        forms.append(tail)
    return forms


def _mentions(text, tok):
    low = text.lower()
    return any(_token_in(low, f.lower()) for f in _token_forms(tok))


def _process_definition_texts(run_dir):
    """The text of every source file the snapshot intake includes (process definitions, steps, attributes), except
    observation collections."""
    try:
        snap = _load(_p(run_dir, evidence_ids.INTAKE))["observations"]["snapshot_intake"]
        intake_path = os.path.normpath(os.path.join(run_dir, snap))
        intake = _load(intake_path)
    except (OSError, ValueError, KeyError, TypeError):
        return []
    base, seen, out = os.path.dirname(intake_path), set(), []
    for src in intake.get("sources") or [] if isinstance(intake, dict) else []:
        if not isinstance(src, dict) or not isinstance(src.get("path"), str) or src.get("status") == "excluded":
            continue
        if src.get("collection_kind") == "observations":
            continue   # observations count as evidence when the rule cites them (see _evidence_backed)
        path = os.path.normpath(os.path.join(base, src["path"]))
        if path in seen or not os.path.isfile(path):
            continue
        seen.add(path)
        try:
            out.append(json.dumps(_load(path), sort_keys=True))
        except (OSError, ValueError):
            continue
    return out


def _evidence_texts(run_dir, rule):
    """The evidence a changed rule can lean on: the observation records it cites (every cited id must resolve, U1's
    resolver; a citation that does not resolve leaves the rule with no evidence at all) and the snapshot's process
    definitions. Each is one JSON text."""
    if not run_dir:
        return []
    ids = rule.get("observation_ids")
    cited = []
    if isinstance(ids, list) and ids:
        res = evidence_ids.Resolver(run_dir)
        try:   # source paths the resolver gives are relative to the snapshot intake's folder
            snap = _load(_p(run_dir, evidence_ids.INTAKE))["observations"]["snapshot_intake"]
            base = os.path.dirname(os.path.normpath(os.path.join(run_dir, snap)))
        except (OSError, ValueError, KeyError, TypeError):
            return []
        for oid in ids:
            if not isinstance(oid, str):
                return []
            found, where = res.resolve(oid)
            if not found:
                return []
            key = (evidence_ids.parse(oid) or (None, None))[1]
            path = os.path.normpath(os.path.join(base, where))
            try:
                cited += _records_with_key(_load(path), key, [])
            except (OSError, ValueError, TypeError):
                return []
    return [json.dumps(r, sort_keys=True) for r in cited] + _process_definition_texts(run_dir)


def _evidence_backed(run_dir, rule, tok):
    """The evidence names the token (token boundaries, or its last part read as words), in a cited observation or a
    process definition. A name supports reading the value; it never supports a new constant (see _value_in)."""
    if not isinstance(tok, str) or not tok:
        return False
    return any(_mentions(text, tok) for text in _evidence_texts(run_dir, rule))


def _value_in(texts, value):
    """A constant appears in the evidence: a number as a whole number, a string as itself or read as words. Booleans
    and nulls are never shown by text (they appear in any JSON), so only the first draft can supply them."""
    if isinstance(value, bool) or value is None:
        return False
    if isinstance(value, (int, float)):
        forms = [repr(value)] + ([str(int(value))] if float(value).is_integer() else [])
        # sign and decimal boundaries: 3 is not the 3 inside -3, 0.3 or 3.5
        pats = [r"(?<![\w.\-+])" + re.escape(f) + r"(?![\w]|\.\d)" for f in forms]
        return any(re.search(pt, t) for t in texts for pt in pats)
    if isinstance(value, str) and value.strip():
        return any(_mentions(t, value) for t in texts)
    return False


_SENTENCE = re.compile(r"(?<=[.!?])\s+|\n+")


def _units(doc, out):
    """The evidence units of one loaded document: each sentence (or line) of each string field, and each scalar field
    read with its key (`retry_count: 3` -> `retry count: 3`), so a key that names an output pairs with its own value.
    Two different fields of one record never form one unit. A constant counts for an output only when it sits in the
    same unit as the output's name."""
    if isinstance(doc, dict):
        for k, v in doc.items():
            if isinstance(k, str) and (isinstance(v, (int, float)) and not isinstance(v, bool)
                                       or isinstance(v, str) and len([x for x in _SENTENCE.split(v.strip()) if x]) <= 1):
                out.append("%s: %s" % (k.replace("_", " "), v if isinstance(v, str) else json.dumps(v)))
            _units(v, out)
    elif isinstance(doc, list):
        for v in doc:
            _units(v, out)
    elif isinstance(doc, str):
        out.extend(x for x in _SENTENCE.split(doc) if x.strip())
    return out


def _condition_numbers(node, out):
    """(number, compared tokens) for each numeric constant in a condition: a comparison's side against the tokens read
    on the other side, a membership list against the tested value's tokens."""
    if not isinstance(node, dict):
        return out
    kind = node.get("kind")
    if kind == "compare":
        sides = (node.get("left"), node.get("right"))
        for i, side in enumerate(sides):
            other = _node_reads(sides[1 - i]) or set()
            for v in _node_literals(side) or []:
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    out.append((v, sorted(other | (_node_reads(side) or set()))))
    elif kind == "in":
        toks = sorted(_node_reads(node.get("value_of")) or set())
        for v in node.get("list") if isinstance(node.get("list"), list) else []:
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                out.append((v, toks))
    for key in ("arg", "value_of"):
        if kind != "in" or key != "value_of":
            _condition_numbers(node.get(key), out)
    for a in node.get("args") if isinstance(node.get("args"), list) else []:
        _condition_numbers(a, out)
    return out


def _paired(units, tok, value):
    """The output's name and the constant appear together in one evidence unit."""
    return any(_mentions(u, tok) and _value_in([u], value) for u in units)


def _canon(node):
    return json.dumps(node, sort_keys=True)


def _node_reads(node):
    tokens = []
    try:
        expr._walk(node, tokens, [])
    except Exception:   # an unreadable node reads nothing we can trust
        return None
    return set(t for t in tokens if isinstance(t, str))


def _node_literals(node):
    out = []
    try:
        expr._all_literals(node, out)
    except Exception:
        return None
    return out


def _has_literal(pool, value):
    return any(type(value) is type(v) and value == v for v in pool)


def _constants_ok(node, tok, existing_literals, units, numbers_only=False):
    """Every constant in node is one the first draft used for this same output (existing_literals), or sits beside the
    output's name in one evidence unit.
    numbers_only (an existing output whose calculation changed): only numeric constants are held to this, since a new
    number is the invented-value damage; a changed outcome label on an existing output is judged by the outcome checks."""
    lits = _node_literals(node)
    if lits is None:
        return False
    if numbers_only:
        lits = [v for v in lits if isinstance(v, (int, float)) and not isinstance(v, bool)]
    return all(_has_literal(existing_literals, v) or _paired(units, tok, v) for v in lits)


def _set_supported(node, tok, existing_nodes, existing_literals, supported, units):
    """A new output is supported when its calculation is one the first draft already had; or when it reads at least
    one value, every value it reads is supported and every constant in it is one the first draft used or the evidence
    shows beside the output's name; or when it is a constant the evidence shows beside the output's name (same
    sentence or record). A name alone, or a value elsewhere, never licenses a new constant: an invented number."""
    if not isinstance(node, dict):
        return False
    if _canon(node) in existing_nodes:
        return True
    reads, lits = _node_reads(node), _node_literals(node)
    if reads is None or lits is None:
        return False
    if reads:
        return _constants_ok(node, tok, existing_literals, units) and all(supported(t) for t in reads)
    return bool(lits) and all(_paired(units, tok, v) for v in lits)


def _derivation_problems(before_stage, after_stage, run_dir=None):
    """Derivation, alias and stand-in guard: what a fix added that no earlier rule had."""
    problems = []
    before_rules = _rules(os.path.join(before_stage, "rules.json"))
    after_rules = _rules(os.path.join(after_stage, "rules.json"))
    old_sets, old_reads = set(), set()
    for r in before_rules.values():
        s, rd = _sets_reads(r)
        old_sets.update(s)
        old_reads.update(rd)
    old_tokens = set(readers.token_names(_rules_doc(os.path.join(before_stage, "rules.json"))))
    # The repaired dependency graph is judged against what the first draft already supported: its calculations,
    # constants (rule literals and config values) and tokens, plus the evidence (cited observations and process
    # definitions). Ruled 0494: a new output from an existing calculation and existing inputs is a supported repair.
    # A first-draft constant supports a changed or new rule only where the first draft used it for the same output
    # (set_lits[token]) or against the same compared token (cmp_lits[token]); reused elsewhere (a threshold as a
    # multiplier) it needs evidence beside the name like any new constant.
    existing_nodes, set_lits, cmp_lits = set(), {}, {}
    for r in before_rules.values():
        e = r.get("expression") if isinstance(r.get("expression"), dict) else {}
        for tok, node in (e.get("sets") or {}).items() if isinstance(e.get("sets"), dict) else []:
            existing_nodes.add(_canon(node))
            set_lits.setdefault(tok, []).extend(_node_literals(node) or [])
        for value, toks in _condition_numbers(e.get("test"), []):
            for t in toks:
                cmp_lits.setdefault(t, []).append(value)
    base_known = old_reads | old_tokens | old_sets
    text_cache, name_cache = {}, {}

    unit_cache = {}

    def texts_of(rule):
        k = _canon(rule.get("observation_ids"))
        if k not in text_cache:
            text_cache[k] = _evidence_texts(run_dir, rule)
        return text_cache[k]

    def units_of(rule):
        k = _canon(rule.get("observation_ids"))
        if k not in unit_cache:
            out = []
            for t in texts_of(rule):
                try:
                    _units(json.loads(t), out)
                except ValueError:
                    continue
            unit_cache[k] = out
        return unit_cache[k]

    def evidenced(rule, tok):
        k = (_canon(rule.get("observation_ids")), tok)
        if k not in name_cache:
            name_cache[k] = isinstance(tok, str) and any(_mentions(t, tok) for t in texts_of(rule))
        return name_cache[k]

    changed = [(rid, rule) for rid, rule in after_rules.items()
               if not (rid in before_rules and before_rules[rid] == rule)]

    def judge(known):
        """{(rid, tok): supported} for every new output, given the supported tokens `known`."""
        out = {}
        for rid, rule in changed:
            sets, _ = _sets_reads(rule)
            e0 = rule.get("expression") if isinstance(rule.get("expression"), dict) else {}
            nodes = e0.get("sets") if isinstance(e0.get("sets"), dict) else {}

            def supported(tok, rule=rule):
                return not tok.startswith("stand_in.") and (tok in known or evidenced(rule, tok))
            before_nodes = {}
            if rid in before_rules:
                eb = before_rules[rid].get("expression") if isinstance(before_rules[rid].get("expression"), dict) else {}
                before_nodes = eb.get("sets") if isinstance(eb.get("sets"), dict) else {}
            for tok in sets:
                node = nodes.get(tok)
                if tok not in old_sets:
                    out[(rid, tok)] = _set_supported(node, tok, existing_nodes, set_lits.get(tok, []), supported,
                                                     units_of(rule))
                elif _canon(node) != _canon(before_nodes.get(tok)) and _canon(node) not in existing_nodes:
                    # an existing output whose calculation changed: its constants are checked the same way
                    out[(rid, tok)] = _constants_ok(node, tok, set_lits.get(tok, []), units_of(rule), numbers_only=True)
        return out

    # The repaired graph is judged as one: an accepted repaired producer supports repaired consumers, whatever order
    # the rules are written in (iterate to a fixpoint; acceptance only grows, so this ends).
    known = set(base_known)
    while True:
        verdicts = judge(known)
        grown = known | {tok for (_, tok), ok in verdicts.items() if ok}
        if grown == known:
            break
        known = grown

    for rid, rule in changed:
        sets, reads = _sets_reads(rule)
        # a changed condition's numbers: a first-draft constant, or paired with a compared token's name in one unit
        ec = rule.get("expression") if isinstance(rule.get("expression"), dict) else {}
        eb = before_rules.get(rid, {}).get("expression") if isinstance(before_rules.get(rid), dict) else None
        tb = eb.get("test") if isinstance(eb, dict) else None
        if _canon(ec.get("test")) != _canon(tb):
            for value, toks in _condition_numbers(ec.get("test"), []):
                if any(_has_literal(cmp_lits.get(t, []), value) or _paired(units_of(rule), t, value) for t in toks):
                    continue
                problems.append("derivation: rule %s compares %s with %s, a number neither the first draft nor the "
                                "evidence beside its name supports" % (rid, " and ".join(toks) or "a constant",
                                                                     json.dumps(value)))
        for tok in sets:
            if tok not in old_sets and not verdicts.get((rid, tok)):
                problems.append("derivation: rule %s sets %s, which no earlier rule set and no evidence or existing "
                                "calculation supports" % (rid, tok))
            elif tok in old_sets and verdicts.get((rid, tok)) is False:
                problems.append("derivation: rule %s sets %s, with a number neither the first draft nor the "
                                "evidence beside its name supports" % (rid, tok))
        e = rule.get("expression") if isinstance(rule.get("expression"), dict) else {}
        for tok, node in (e.get("sets") or {}).items() if isinstance(e.get("sets"), dict) else []:
            if isinstance(node, dict) and node.get("kind") == "read":
                problems.append("alias: rule %s sets %s as a bare copy of %s" % (rid, tok, node.get("token")))
        for tok in reads:
            if tok.startswith("stand_in."):
                if tok not in old_reads:
                    problems.append("stand-in: rule %s reads %s, which no earlier rule read" % (rid, tok))
            elif tok not in known and not evidenced(rule, tok):
                problems.append("early read: rule %s reads %s, which no earlier rule read or set and no evidence "
                                "supports" % (rid, tok))
    for name in ("declarations.json",):
        try:
            b = _load(os.path.join(before_stage, name)).get("stand_ins") or []
        except (OSError, ValueError, AttributeError):
            b = []
        try:
            a = _load(os.path.join(after_stage, name)).get("stand_ins") or []
        except (OSError, ValueError, AttributeError):
            a = []
        for entry in a:
            if entry not in b:
                problems.append("stand-in: %s gained stand-in %s" % (name, json.dumps(entry, sort_keys=True)))
    for name in ("fields.json",):
        b = set(_stand_in_fields(os.path.join(before_stage, name)))
        for key in _stand_in_fields(os.path.join(after_stage, name)):
            if key not in b:
                problems.append("stand-in: %s gained stand-in field %s" % (name, key))
    for name in _files(after_stage) if os.path.isdir(after_stage) else []:
        if not name.endswith(".json"):
            continue
        try:
            text_after = json.dumps(_load(os.path.join(after_stage, name)))
        except (OSError, ValueError):
            continue
        try:
            text_before = json.dumps(_load(os.path.join(before_stage, name)))
        except (OSError, ValueError):
            text_before = ""
        for key in ('"alias"', '"aliases"', '"alias_of"'):
            if text_after.count(key) > text_before.count(key):
                problems.append("alias: %s gained an %s entry" % (name, key.strip('"')))
    return problems


# ---------- early-read guard: step order (experiment 01's rule)
#
# Ordering source: each process's future-state graph, opened through stages/intake/intake.json
# (processes[].process_id, processes[].future_state); step A is at or before step B when A == B or B is
# reachable from A along that graph's edges. Steps in different processes are never ordered here (strict, H15).
# Steps on one loop (a retry or rework edge) reach each other both ways, so neither is ordered before the other:
# the first visit to the earlier step still comes before the value returned at the later one.

INBOUND = ("read", "inbound_control", "back_from_person")
AGENT_SIDES = ("agent", "existing_tool")


def _maybe(path):
    try:
        return _load(path)
    except (OSError, ValueError):
        return None


def _listed(doc, *keys):
    if isinstance(doc, list):
        return [x for x in doc if isinstance(x, dict)]
    if isinstance(doc, dict):
        for k in keys:
            if isinstance(doc.get(k), list):
                return [x for x in doc[k] if isinstance(x, dict)]
    return []


def _graphs(run_dir):
    """process_id -> {"next": {node: set(nodes)}, "edges": {edge_id: (from, to)}}."""
    out = {}
    for proc in _listed(_maybe(_p(run_dir, "stages/intake/intake.json")), "processes"):
        pid, rel = proc.get("process_id"), proc.get("future_state")
        doc = _maybe(_p(run_dir, rel)) if _safe_rel(rel) else None
        if not isinstance(pid, str) or not isinstance(doc, dict):
            continue
        graph = doc.get("graph") if isinstance(doc.get("graph"), dict) else doc
        nxt, edges = {}, {}
        for e in _listed(graph, "edges"):
            a, b = e.get("from", e.get("source")), e.get("to", e.get("target"))
            if isinstance(a, str) and isinstance(b, str):
                nxt.setdefault(a, set()).add(b)
                if isinstance(e.get("id"), str):
                    edges[e["id"]] = (a, b)
        out[pid] = {"next": nxt, "edges": edges}
    return out


def _path(nxt, na, nb):
    seen, todo = set(), [na]
    while todo:
        for m in nxt.get(todo.pop(), ()):
            if m == nb:
                return True
            if m not in seen:
                seen.add(m)
                todo.append(m)
    return False


def _reaches(graphs, a, b):
    """True when qualified step a lies strictly before qualified step b; None when that can not be established."""
    pa, _, na = a.partition(":")
    pb, _, nb = b.partition(":")
    if pa != pb or pa not in graphs:
        return None
    nxt = graphs[pa]["next"]
    if not _path(nxt, na, nb):
        return False
    return None if _path(nxt, nb, na) else True  # on one loop: neither step is strictly first


def _placed_steps(placement_dir):
    out = {}
    for pr in _listed(_maybe(os.path.join(placement_dir, "placed-rules.json")), "placed_rules", "placed", "rules"):
        rid = pr.get("rule_id", pr.get("rule"))
        if isinstance(rid, str) and isinstance(pr.get("process_id"), str) and isinstance(pr.get("future_state_step"), str):
            out.setdefault(rid, set()).add(pr["process_id"] + ":" + pr["future_state_step"])
    return out


def _boundary_step(boundary, graphs):
    """The agent-side step a boundary meets, or None."""
    ref = boundary.get("edge_ref")
    if not isinstance(ref, str):
        return None
    if ref.startswith("implied:"):
        for side in ref[len("implied:"):].split("->"):
            if ":" in side:
                return side
        return None
    pid, _, eid = ref.partition(":")
    edge = graphs.get(pid, {}).get("edges", {}).get(eid)
    if not edge:
        return None
    if (boundary.get("to") or {}).get("side") in AGENT_SIDES:
        return pid + ":" + edge[1]
    if (boundary.get("from") or {}).get("side") in AGENT_SIDES:
        return pid + ":" + edge[0]
    return None


def _config_names(rules_doc, placement_dir):
    names = set()
    for c in _listed(rules_doc, "config"):
        if isinstance(c.get("name"), str):
            names.add(c["name"])
    for c in _listed(_maybe(os.path.join(placement_dir, "declarations.json")), "configuration"):
        for k in ("name", "token"):
            if isinstance(c.get(k), str):
                names.add(c[k])
    return names


def _early_read_problems(run_dir, before_dir, owning, unplaced=None):
    """Stage -> problems: an added or changed rule (or a moved placement) reading a value not yet available at its step."""
    def stage_dir(stage, when):
        return os.path.join(before_dir, stage) if when == "before" and stage in owning else _p(run_dir, "stages/" + stage)
    rules_after_doc = _maybe(os.path.join(stage_dir("rules", "after"), "rules.json"))
    rules_before = _rules(os.path.join(stage_dir("rules", "before"), "rules.json"))
    rules_after = _rules(os.path.join(stage_dir("rules", "after"), "rules.json"))
    steps_before = _placed_steps(stage_dir("placement", "before"))
    steps_after = _placed_steps(stage_dir("placement", "after"))
    graphs = _graphs(run_dir)
    config = _config_names(rules_after_doc, stage_dir("placement", "after"))
    # where each value becomes available: (step, kind) pairs
    supply = {}
    boundaries = {b.get("id"): b for b in _listed(_maybe(_p(run_dir, "stages/boundaries/boundaries.json")), "boundaries")}
    for fld in _listed(_maybe(_p(run_dir, "stages/fields/fields.json")), "fields"):
        if fld.get("direction") in INBOUND and isinstance(fld.get("token"), str):
            step = _boundary_step(boundaries.get(fld.get("boundary_id")) or {}, graphs)
            supply.setdefault(fld["token"], []).append((step, "read", None))
    for rid, rule in rules_after.items():
        for tok in _sets_reads(rule)[0]:
            for step in steps_after.get(rid, {None}):
                supply.setdefault(tok, []).append((step, "rule", rid))
    found = {}
    for rid, rule in rules_after.items():
        new_rule = rid not in rules_before or rules_before[rid].get("expression") != rule.get("expression")
        moved = steps_after.get(rid, set()) != steps_before.get(rid, set())
        if not (new_rule or moved):
            continue
        stage = "rules" if new_rule and "rules" in owning else "placement" if "placement" in owning else "rules"
        reads = _sets_reads(rule)[1]
        if not steps_after.get(rid):
            if unplaced is not None and rid not in rules_before:
                unplaced.append(rid)   # an added rule: placed by the placement rerun inside this round
                continue
            if reads:
                found.setdefault(stage, []).append(
                    "early read: rule %s has no placed step in placed-rules.json, so when %s become available "
                    "can not be established" % (rid, ", ".join(reads)))
            continue
        for at in sorted(steps_after[rid]):
            for tok in reads:
                if tok.startswith(("stand_in.", "judgment.")) or tok in config:
                    continue  # stand-ins have their own guard; judgments are made at the step; configuration is fixed
                ok, later, unknown = False, [], []
                for step, kind, by in supply.get(tok, []):
                    if kind == "rule" and by == rid:
                        continue  # written by the same rule that reads it
                    if step is None:
                        unknown.append(by or "a read interface")
                        continue
                    before = _reaches(graphs, step, at)
                    if (kind == "read" and step == at) or before:
                        ok = True
                        break
                    if _reaches(graphs, at, step) or (kind == "rule" and step == at):
                        later.append(step)
                    else:
                        unknown.append(step)
                if ok:
                    continue
                if later:
                    why = "it becomes available only at the later step %s" % ", ".join(sorted(set(later)))
                elif unknown:
                    why = "the step that supplies it (%s) can not be ordered before %s" % (", ".join(sorted(set(unknown))), at)
                else:
                    why = "no read interface, case input or earlier rule supplies it"
                found.setdefault(stage, []).append("early read: rule %s at %s reads %s, but %s" % (rid, at, tok, why))
    return found


def _restore_stage(run_dir, before_dir, stage):
    dst = _p(run_dir, "stages/" + stage)
    if os.path.exists(dst):
        shutil.rmtree(dst)
    shutil.copytree(os.path.join(before_dir, stage), dst)


def _open_item(finding, reason):
    fid = finding["id"]
    fam = finding.get("family")
    return {
        "id": "RB-" + fid,
        "class": "needs_more_information",
        "kind": "evidence",
        "source": "fix residual",
        "unknown": "Review B finding %s%s is not closed: %s. %s" % (
            fid, (" (family %s)" % fam) if fam else "", finding["subject"], reason),
        "why_it_matters": finding.get("detail") or finding["subject"],
        "agent_meanwhile": "The agent does not act on this part of the design and hands the item to a person.",
        "who_can_answer": "The process owner",
        "closure_route": ["Targeted Companion capture", "Document upload"],
        "resolution_evidence": "A recorded source that states the missing rule or value for finding %s." % fid,
        "breaks_if_wrong": "The %s stage would carry a rule or value the evidence does not support." % (finding.get("stage") or "owning"),
        "dependent_rules": [],
        "group": {"id": fam or fid, "cheapest_item": "RB-" + fid},
        "reader_verdict": None,
    }


def _fix_entries(run_dir, findings):
    """The validated fixes.json entries, or None when the fixers wrote none (each stage is then one fix); plus notes
    on entries left out. An unparseable fixes.json claims nothing (every change is reverted) and is noted."""
    path = _p(run_dir, FIXES)
    if not os.path.isfile(path):
        return None, []
    try:
        doc = _load(path)
    except (OSError, ValueError) as exc:
        return [], ["%s does not parse (%s); no fix is claimed, so every change is reverted" % (FIXES, exc.__class__.__name__)]
    raw = doc.get("fixes") if isinstance(doc, dict) else doc
    out, notes = [], []
    for n, e in enumerate(raw if isinstance(raw, list) else []):
        fid = e.get("finding_id") if isinstance(e, dict) else None
        files = e.get("files") if isinstance(e, dict) else None
        if fid not in findings:
            notes.append("%s entry %d names no finding of this round (%r); left out" % (FIXES, n, fid))
        elif not isinstance(files, list) or not files or not all(_safe_rel(f) for f in files):
            notes.append("%s entry %d (%s) lists no usable run-relative files; left out" % (FIXES, n, fid))
        else:
            out.append({"finding_id": fid, "files": sorted(set(files)), "summary": e.get("summary")})
    if not isinstance(raw, list):
        notes.append("%s is not a list of fixes; no fix is claimed" % FIXES)
    return out, notes


def _groups(entries):
    """Fixes that share a file are judged together: [{"findings": [...], "files": [...]}] in first-seen order."""
    groups = []
    for e in entries:
        hit = [g for g in groups if set(g["files"]) & set(e["files"])]
        g = {"findings": [e["finding_id"]], "files": list(e["files"])}
        for h in hit:
            g["findings"] = h["findings"] + [f for f in g["findings"] if f not in h["findings"]]
            g["files"] = sorted(set(h["files"]) | set(g["files"]))
            groups.remove(h)
        groups.append(g)
    return groups


def _changed(a, b):
    names = set(_files(a) if os.path.isdir(a) else []) | set(_files(b) if os.path.isdir(b) else [])
    out = []
    for n in sorted(names):
        pa, pb = os.path.join(a, *n.split("/")), os.path.join(b, *n.split("/"))
        if (_sha(pa) if os.path.isfile(pa) else None) != (_sha(pb) if os.path.isfile(pb) else None):
            out.append(n)
    return out


def _copy_files(src_dir, dst_dir, names):
    for n in names:
        src, dst = os.path.join(src_dir, *n.split("/")), os.path.join(dst_dir, *n.split("/"))
        if os.path.isfile(src):
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(src, dst)
        elif os.path.exists(dst):
            os.remove(dst)


def _worse(state, stage, after):
    return [n for n, r in state["checks_before"].get(stage, {}).items() if r == "pass" and after.get(n) != "pass"]


def settle(run_dir, before_dir):
    run_dir = os.path.abspath(run_dir)
    before_dir = os.path.abspath(before_dir)
    used = check_only(run_dir)
    if used is not None:
        return _restore_checked(run_dir, used)
    path = _p(run_dir, RB + "/fix-round.json")
    if os.path.isfile(path):
        current = _load(path)
        if isinstance(current, dict) and current.get("status") == "placement_pending":
            return _settle_placement(run_dir, before_dir, current)
    state = _state(run_dir)
    manifest = _load(os.path.join(before_dir, "manifest.json"))
    owning = list(state["stages"])
    by_id = {f["id"]: f for f in state["findings"]}
    refused = {}       # stage -> reason
    reverted = []
    # 1. check code and settings: the plugin's checks, config and formats files only (generated reports in RUN are
    # never compared)
    check_edits = []
    now_repo = set(_repo_files())
    for rel in sorted(set(manifest["repo"]) | now_repo):
        cur = os.path.join(REPO, *rel.split("/"))
        if manifest["repo"].get(rel) != (_sha(cur) if os.path.isfile(cur) else None):
            check_edits.append(rel)
            if rel in manifest["repo"]:
                os.makedirs(os.path.dirname(cur), exist_ok=True)  # the fix may have deleted the folder
                shutil.copy2(os.path.join(before_dir, "repo", *rel.split("/")), cur)
            else:
                os.remove(cur)
    if check_edits:
        for stage in owning:
            refused[stage] = "a fix edited check code or settings (%s)" % ", ".join(check_edits)
    # 2. edits outside the owning folders: revert from before/rest/ or delete (generated reports are left alone)
    inside = tuple("stages/%s/" % s for s in owning)
    now_run = set(_run_files(run_dir))
    for rel in sorted(set(manifest["run"]) | now_run):
        if rel.startswith(inside) or rel.startswith(GENERATED):
            continue
        cur = _p(run_dir, rel)
        if manifest["run"].get(rel) == (_sha(cur) if os.path.isfile(cur) else None):
            continue
        reverted.append(rel)
        if rel in manifest["run"]:
            os.makedirs(os.path.dirname(cur), exist_ok=True)
            shutil.copy2(os.path.join(before_dir, "rest", *rel.split("/")), cur)
        else:
            os.remove(cur)
    for stage in refused:
        _restore_stage(run_dir, before_dir, stage)
    # 3. each finding's fix on its own (fixes sharing a file together), applied one at a time on the stage as it was
    # before the round: kept when the guards and the stage's checks accept it, else reverted alone with its reason
    entries, notes = _fix_entries(run_dir, by_id)
    fixes, verdicts, unplaced = [], {}, {}
    fixed_copy = os.path.join(run_dir, *RB.split("/"), "fixed")
    if os.path.exists(fixed_copy):
        shutil.rmtree(fixed_copy)
    work = []
    for stage in owning:   # every fixed stage is copied aside and restored first, so each fix is judged alone
        if stage in refused:
            continue
        sdir, bdir = _p(run_dir, "stages/" + stage), os.path.join(before_dir, stage)
        changed = _changed(bdir, sdir)
        if changed:
            shutil.copytree(sdir, os.path.join(fixed_copy, stage))
            _restore_stage(run_dir, before_dir, stage)
            work.append((stage, changed))
    worked = [w[0] for w in work]
    for stage in owning:   # a stage the fixers left unchanged: a claimed fix changed nothing a check could refuse
        if stage in refused or stage in worked:
            continue
        if entries is None:
            try:
                claimed = _load(_p(run_dir, RB + "/fix/%s.done.json" % stage)).get("fixed") or []
            except (OSError, ValueError, AttributeError):
                claimed = []
        else:
            claimed = [e["finding_id"] for e in entries if by_id[e["finding_id"]]["stage"] == stage]
        for fid in claimed:
            if fid in by_id:
                verdicts[fid] = "the claimed fix changed nothing under stages/%s/, so the finding is still there" % stage
    for stage, changed in work:
        sdir, bdir = _p(run_dir, "stages/" + stage), os.path.join(before_dir, stage)
        snap = os.path.join(fixed_copy, stage)
        prefix = "stages/%s/" % stage
        if entries is None:
            try:
                done = _load(_p(run_dir, RB + "/fix/%s.done.json" % stage)).get("fixed") or []
            except (OSError, ValueError, AttributeError):
                done = []
            groups = [{"findings": [f for f in done if f in by_id], "files": [prefix + n for n in changed]}]
        else:
            groups = _groups([e for e in entries if by_id[e["finding_id"]]["stage"] == stage])
            claimed = set(f[len(prefix):] for g in groups for f in g["files"] if f.startswith(prefix))
            for n in changed:
                if n not in claimed:
                    reverted.append(prefix + n)
                    notes.append("%s%s changed but no fix in %s claims it; reverted" % (prefix, n, FIXES))
        for g in groups:
            names = [f[len(prefix):] for f in g["files"] if f.startswith(prefix)]
            outside = [f for f in g["files"] if not f.startswith(prefix)]
            _copy_files(snap, sdir, names)
            problems = ["edits outside %s (%s) are not part of a %s fix" % (prefix, ", ".join(outside), stage)] \
                if outside else []
            if not any(n in changed for n in names):
                problems.append("the claimed fix changed none of its files (%s), so the finding is still there"
                                % ", ".join(g["files"]))
            problems += _derivation_problems(bdir, sdir, run_dir)
            unpl = []
            early = _early_read_problems(run_dir, before_dir, owning, unpl)
            for st_ in sorted(early):
                problems += [p for p in early[st_] if p not in problems]
            worse = _worse(state, stage, _stage_checks(run_dir, stage))
            if worse:
                problems.append("check %s passed before the fix and does not pass after it" % ", ".join(worse))
            rec = {"stage": stage, "findings": g["findings"], "files": g["files"]}
            if problems:
                _copy_files(bdir, sdir, names)
                why = "fix refused: " + "; ".join(problems)
                if len(g["findings"]) > 1:
                    why += " (fixes sharing a file are judged together: %s)" % ", ".join(g["findings"])
                rec.update(result="reverted", reason=why)
                for fid in g["findings"]:
                    verdicts[fid] = why
            else:
                rec["result"] = "kept"
                for fid in g["findings"]:
                    verdicts[fid] = "kept"
                for rid in unpl:
                    if rid not in unplaced:
                        unplaced[rid] = list(g["findings"])
                if unpl:
                    rec["needs_placement"] = sorted(set(unpl))
            fixes.append(rec)
    undone = {}
    for stage in owning:   # a stage none of whose fixes was kept reads as refused (a guard) or undone (a check)
        mine = [r for r in fixes if r["stage"] == stage]
        if mine and all(r["result"] == "reverted" for r in mine):
            reasons = [r["reason"] for r in mine]
            if all(" passed before the fix and does not pass after it" in x and "; " not in x for x in reasons):
                undone[stage] = "fix undone: " + "; ".join(x[len("fix refused: "):] for x in reasons)
            else:
                refused[stage] = "; ".join(reasons)
    state.update({"refused": refused, "undone": undone, "reverted": reverted, "check_edits": check_edits,
                  "fixes": fixes, "fix_notes": notes, "verdicts": verdicts})
    # 4. rules a kept fix added have no placed step: placement reruns through the route inside this round
    if unplaced:
        return _route_placement(run_dir, before_dir, state, unplaced)
    return _close_round(run_dir, before_dir, state)


def _route_placement(run_dir, before_dir, state, unplaced):
    rules = sorted(unplaced)
    if not os.path.isdir(os.path.join(before_dir, "placement")) and os.path.isdir(_p(run_dir, "stages/placement")):
        shutil.copytree(_p(run_dir, "stages/placement"), os.path.join(before_dir, "placement"))
    # placement's checks as they stand before the rerun (the added rules unplaced): the rerun may not make a check
    # that passes now fail; the early-read guard then shows each added rule placed with its reads available
    state["checks_before"]["placement_rerun"] = _stage_checks(run_dir, "placement")
    findings = sorted(set(f for v in unplaced.values() for f in v))
    for fid in findings:
        state["verdicts"][fid] = "pending placement"
    fixdir = _p(run_dir, RB + "/fix")
    os.makedirs(fixdir, exist_ok=True)
    with open(os.path.join(fixdir, "placement.md"), "w", encoding="utf-8") as fh:
        fh.write(_placement_prompt(rules, run_dir))
    route = {"stage": "placement", "source": "review-b settle", "rules": rules, "findings": findings,
             "prompt": RB + "/fix/placement.md"}
    _dump(_p(run_dir, RB + "/route-placement.json"), route)
    state["prompts"] = list(state.get("prompts") or []) + [RB + "/fix/placement.md"]
    state["placement_rerun"] = {"rules": rules, "findings": findings, "route": RB + "/route-placement.json",
                                "result": "pending"}
    state["status"] = "placement_pending"
    _dump(_p(run_dir, RB + "/fix-round.json"), state)
    return state


def _settle_placement(run_dir, before_dir, state):
    """The second settle of the round: judge the placement rerun for the rules a kept fix added."""
    pr = state["placement_rerun"]
    owning = list(state["stages"]) + (["placement"] if "placement" not in state["stages"] else [])
    unpl = []
    early = _early_read_problems(run_dir, before_dir, owning, unpl)
    problems = ["rule %s is still not placed" % r for r in pr["rules"] if r in unpl]
    for st_ in sorted(early):
        problems += [p for p in early[st_] if any(("rule %s " % r) in p for r in pr["rules"])]
    if "placement" not in state["stages"]:
        problems += _derivation_problems(os.path.join(before_dir, "placement"), _p(run_dir, "stages/placement"), run_dir)
    after = _stage_checks(run_dir, "placement")
    worse = [n for n, r in (state["checks_before"].get("placement_rerun") or {}).items()
             if r == "pass" and after.get(n) != "pass"]
    if worse:
        problems.append("check %s passed before the placement rerun and does not pass after it" % ", ".join(worse))
    state["placement_rerun"]["checks_after"] = after
    if problems:
        _restore_stage(run_dir, before_dir, "placement")
        why = "the rules fix is kept, but the placement rerun failed: " + "; ".join(problems)
        pr.update(result="failed", reason=why)
        for fid in pr["findings"]:
            state["verdicts"][fid] = why
        state.setdefault("recorded_problems", []).append({"kind": "placement-rerun", "detail": why})
    else:
        pr["result"] = "placed"
        for fid in pr["findings"]:
            state["verdicts"][fid] = "kept"
        if "placement" not in state["stages"]:
            state["stages"].append("placement")
    try:
        os.remove(_p(run_dir, RB + "/route-placement.json"))
    except OSError:
        pass
    pr["consumed"] = True
    return _close_round(run_dir, before_dir, state)


def _finding_gone(run_dir, before_dir, state, fid, fresh):
    """Settle's own check, rerun on the settled revision: None when the finding is gone, else why it is not. The
    finding's kept fix must be present in the settled outputs (a claimed file differs from the copy taken at plan)
    and the owning stage's checks, run fresh now, must pass every check that passed before the round."""
    by_id = dict((f.get("id"), f) for f in state.get("findings") or [] if isinstance(f, dict))
    recs = [r for r in state.get("fixes") or [] if fid in r.get("findings", []) and r.get("result") == "kept"]
    if not recs:
        return "no kept fix names this finding"
    for r in recs:
        stage, prefix = r["stage"], "stages/%s/" % r["stage"]
        here = []
        for f in r["files"]:
            if not f.startswith(prefix):
                continue
            n = f[len(prefix):]
            a, b = _p(run_dir, f), os.path.join(before_dir, stage, *n.split("/"))
            if (_sha(a) if os.path.isfile(a) else None) != (_sha(b) if os.path.isfile(b) else None):
                here.append(f)
        if not here:
            return "the kept fix (%s) is not in the settled outputs" % ", ".join(r["files"])
        if stage not in fresh:
            fresh[stage] = _stage_checks(run_dir, stage)
        worse = _worse(state, stage, fresh[stage])
        if worse:
            return "check %s passed before the round and does not pass on the settled revision" % ", ".join(worse)
        still = _still_reported(state, stage, by_id.get(fid) or {}, fresh[stage])
        if still:
            return still
    return None


def _key(p):
    return (p.get("defect"), p.get("where")) if isinstance(p, dict) else None


def _still_reported(state, stage, finding, results):
    """Why the finding is still there on the fresh check of its owning stage, else None. A finding is its id plus the
    check problem it carries (defect and where): that problem must be absent from every fresh report of the stage's
    checks, the check it names included. A finding that carries no problem carries the problems its stage's checks
    reported before the round; each must be gone. A check that could not run, or a failing check whose problems are
    not known, keeps the finding open."""
    names = list(CHECKS.get(stage, []))
    for n in names:
        if results.get(n) not in ("pass", "fail"):
            return "check %s could not run on the settled revision" % n
    carried = finding.get("problem")
    if carried is not None:
        if not isinstance(carried, dict) or not isinstance(carried.get("defect"), str) \
                or not isinstance(carried.get("where"), str):
            return "the check problem the finding carries is not a {defect, where} object"
        if isinstance(carried.get("check"), str) and carried["check"] not in names:
            return "the finding names check %s, which is not a check of stage %s" % (carried["check"], stage)
        # a named check never narrows the comparison: the carried defect and location must be gone from every check
        # of the owning stage, since more than one check can report the same problem
        wanted = {n: [_key(carried)] for n in names}
    else:
        before = (state.get("problems_before") or {}).get(stage) or {}
        wanted = {}
        for n in names:
            was = state["checks_before"].get(stage, {}).get(n)
            if was == "pass" or was is None:
                continue
            probs = before.get(n)
            if probs is None:
                if results.get(n) != "pass":
                    return "check %s failed before the round and still does not pass (its problems are not known)" % n
                continue
            wanted[n] = [_key(p) for p in probs]
    for n, keys in wanted.items():
        now = _problems_of(results.get(n))
        if now is None:
            return "check %s does not pass and its problems are not known" % n
        now_keys = set(_key(p) for p in now)
        left = [k for k in keys if k in now_keys]
        if left:
            return "check %s still reports %s" % (n, ", ".join("%s at %s" % k for k in left))
    return None


def _close_round(run_dir, before_dir, state):
    fixed, items = [], []
    refused = state.get("refused") or {}
    verdicts = state.get("verdicts") or {}
    fresh = {}
    closure = {}
    # two findings sharing an id cannot be told apart by the fixes, verdicts and closures keyed on it: both stay open
    # and the ambiguity is recorded, never settled by guessing which one a fix answered
    seen_ids = [f.get("id") for f in state["findings"]]
    dup = sorted(set(i for i in seen_ids if seen_ids.count(i) > 1), key=str)
    for fid in dup:
        why = "%d findings share the id %s, so a fix or check cannot be matched to one of them" % (
            seen_ids.count(fid), fid)
        closure[fid] = why
        rec = {"kind": "duplicate-finding-id", "detail": why}
        if rec not in (state.get("recorded_problems") or []):   # a second settle of the round records it once
            state.setdefault("recorded_problems", []).append(rec)
            _record_problem(run_dir, "duplicate-finding-id", fid, why)
    for f in state["findings"]:
        stage = f["stage"]
        reason = f.get("open_reason") or refused.get(stage) or (state.get("undone") or {}).get(stage)
        if f.get("id") in dup:
            reason = "the finding is kept open: " + closure[f["id"]]
        if reason is None:
            if f["known"]:
                if f["id"] in state["known_applied"]:
                    # an applied value closes the finding only when it is gone on a fresh check of its owning stage
                    if stage not in fresh:
                        fresh[stage] = _stage_checks(run_dir, stage)
                    worse = _worse(state, stage, fresh[stage])
                    why = ("check %s passed before the round and does not pass on the settled revision"
                           % ", ".join(worse)) if worse else _still_reported(state, stage, f, fresh[stage])
                    closure[f["id"]] = why or "gone on the settled revision"
                    if why is None:
                        fixed.append(f["id"])
                        continue
                    reason = "the known value was applied, but the finding is not gone on a fresh check: " + why
                else:
                    reason = state["known_refused"].get(f["id"], "known value not applied")
            elif verdicts.get(f["id"]) == "kept":
                why = _finding_gone(run_dir, before_dir, state, f["id"], fresh)
                closure[f["id"]] = why or "gone on the settled revision"
                if why is None:
                    fixed.append(f["id"])
                    continue
                reason = "the fix was kept, but the finding is not gone on a fresh check: " + why
            else:
                reason = verdicts.get(f["id"]) or "the fix left it open"
        items.append(_open_item(f, reason))
    _dump(_p(run_dir, RB + "/open-items.json"), {"items": items})
    # what the round kept: each changed owning stage's file hashes, so ad.py records exactly these as the accepted
    # repair and still refuses any other change to a finished stage
    after = {}
    for stage in state["stages"]:
        if stage in refused:
            continue
        sdir = _p(run_dir, "stages/" + stage)
        bdir = os.path.join(before_dir, stage)
        if os.path.isdir(sdir) and os.path.isdir(bdir) and _changed(bdir, sdir):
            after[stage] = {rel: _sha(os.path.join(sdir, *rel.split("/"))) for rel in _files(sdir)}
    state.update({"fixed": fixed, "open": [i["id"][3:] for i in items], "after": after, "closure": closure,
                  "checks_settled": fresh})
    state["status"] = "settled"  # set last
    _dump(_p(run_dir, RB + "/fix-round.json"), state)
    return state


def main(argv=None):
    ap = argparse.ArgumentParser(description="Review B fix round")
    ap.add_argument("command", choices=("plan", "apply-known", "settle"))
    ap.add_argument("--run", required=True)
    a = ap.parse_args(argv)
    try:
        if a.command == "plan":
            out = plan(a.run)
        elif a.command == "apply-known":
            out = apply_known(a.run)
        else:
            out = settle(a.run, os.path.join(a.run, *RB.split("/"), "before"))
    except FixRoundError as exc:
        print("refused: %s" % exc, file=sys.stderr)
        return 1
    print(json.dumps({k: out.get(k) for k in ("status", "findings_in", "fixed", "open", "applied", "refused",
                                              "placement_rerun", "check_only", "restored", "resumed", "draft_gaps")
                      if k in out}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
