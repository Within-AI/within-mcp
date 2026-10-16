#!/usr/bin/env python3
"""Agent Design run control: the run folder, its state file and event log, stage prompts and stage checks.

Usage (RUN is the Agent Design run folder; FSD_RUN a finished Future State Design run folder):
  ad.py init    --run RUN --from FSD_RUN [--baseline PINS.json]
                                          new run: copy FSD_RUN into RUN/upstream/ (regular files only; older runs
                                          may hold outputs/agent-design-inputs.json; it is never read or copied),
                                          record every file's sha256, the freeze block and the skill commit; with
                                          --baseline, refuse any freeze pin that differs; then write the page
                                          scaffold through page/build.py (fill_stage(run, "init"))
  ad.py next    --run RUN                 consume pending routes (a finding whose owner is an earlier stage reruns
                                          that owner and invalidates every later stage; a repair agent names that
                                          owner in RUN/repairs/KEY.json {"owner_stage", "reason"}; an owner outside
                                          Agent Design or the spent repair budget (per run) becomes a register item,
                                          a named draft gap), then print the next stage key, or "done". Outputs
                                          changed after finishing (or a changed upstream/ copy) are recorded and
                                          rechecked, and the stages after them marked stale; never a stop
  ad.py repair  --run RUN                 the same as next
  ad.py start   --run RUN --stage KEY [--rerun]
                                          mark the stage running; for an agent or review stage write RUN/prompts/KEY.md
                                          and print its path; --rerun marks every later finished stage stale
  ad.py finish  --run RUN --stage KEY     run the stage's checks, record results, minutes, output hashes and counts,
                                          then call limits.boundary and (all checks passed) page.build.fill_stage,
                                          then re-record the hashes of finished stage outputs the page hook rewrote;
                                          finishing review-b first records the repairs its settled fix round kept
                                          (accept_repairs) and marks the stages after them stale
  ad.py status  --run RUN [--builder]     record a watch event and print the person's status (limits.customer_view);
                                          --builder prints limits.table and the stage table instead
  ad.py resume  --run RUN                 print the stage table, the next stage and the prompt of a running stage

Exit codes: 0 ok; 1 a check did not pass (finish); 2 refused, usage, a missing hook, prompt or include file, or a hook
that raised; 3 state.json changed outside this script (changed stage outputs or upstream/ are recorded, not exit 3).
Only this script writes state.json and events.jsonl; every event that follows a write records the new state.json
sha256. The stage list is stages/index.json. Hooks (loaded by file path): scripts/limits.py and page/build.py.
Paths written into the run folder are relative to it. Stdlib only, Python 3.9.
"""
import argparse, glob, hashlib, importlib.util, json, os, re, shutil, stat, subprocess, sys
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
REPO = os.path.dirname(os.path.dirname(SKILL))
INDEX = os.path.join(SKILL, "stages", "index.json")
LIMITS = os.path.join(SKILL, "scripts", "limits.py")
PAGE = os.path.join(SKILL, "page", "build.py")
SET_INSTRUCTION = os.path.join(SKILL, "stages", "_set", "instruction.md")
EXCLUDED = "outputs/agent-design-inputs.json"   # older runs may hold it (a platform answer); never read or copied
NO_BASELINE = "freeze pins not compared with a baseline"
REVIEW_B = "review-b"
FIX_ROUND = "stages/review-b/fix-round.json"
DONE, DONE_PROBLEMS, FAILED = "done", "done with recorded problems", "failed"   # the three finish states
PROBLEMS = "builder/problems.jsonl"            # builder log of recorded problems, register-readable
CHECK_DEFECTS = "builder/check-defects.jsonl"  # one line per check a fallback agent judged wrong or crashed


class Stop(Exception):
    """Print the message and exit with the code."""
    def __init__(self, code, msg):
        Exception.__init__(self, msg)
        self.code = code


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha(p):
    with open(p, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def rel(p, run):
    return os.path.relpath(p, run).replace(os.sep, "/")


def load_index():
    with open(INDEX, encoding="utf-8") as f:
        return json.load(f)["stages"]


def stage_entry(key):
    for e in load_index():
        if e["key"] == key:
            return e
    raise Stop(2, "refused: %s is not a stage in stages/index.json" % key)


def keys():
    return [e["key"] for e in load_index()]


# ---------------------------------------------------------------- state and events

def load_state(run):
    with open(os.path.join(run, "state.json"), encoding="utf-8") as f:
        return json.load(f)


def write_state(run, st, rec):
    """Save state.json, then append the event carrying the new state.json sha256."""
    tmp = os.path.join(run, "state.json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f, indent=2)
    os.replace(tmp, os.path.join(run, "state.json"))
    append_event(run, dict(rec, state_sha256=sha(os.path.join(run, "state.json"))))


def append_event(run, rec):
    rec = dict({"at": now(), "source": "ad.py"}, **rec)
    with open(os.path.join(run, "events.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")


def recorded_state_sha(run):
    last = None
    try:
        with open(os.path.join(run, "events.jsonl"), encoding="utf-8") as f:
            for line in f:
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if isinstance(rec, dict) and rec.get("state_sha256"):
                    last = rec["state_sha256"]
    except OSError:
        return None
    return last


def open_run(run):
    """The run's state, after confirming state.json is the one this script last wrote (else exit 3)."""
    p = os.path.join(run, "state.json")
    if not os.path.isfile(p):
        raise Stop(2, "refused: %s has no state.json; run ad.py init first" % run)
    if sha(p) != recorded_state_sha(run):
        raise Stop(3, "refused: state.json was changed outside ad.py (its sha256 differs from the last one recorded "
                      "in events.jsonl); restore state.json, nothing else is run")
    return load_state(run)


def tree_hashes(d):
    out = {}
    for dp, _, fs in os.walk(d):
        for f in fs:
            p = os.path.join(dp, f)
            out[rel(p, d)] = sha(p)
    return dict(sorted(out.items()))


def changed_done(run, st):
    """Finished stages whose recorded output hashes no longer match, in index order."""
    bad = []
    for e in load_index():
        s = st["stages"].get(e["key"]) or {}
        if is_done(s.get("status")) and tree_hashes(os.path.join(run, e["outputs"])) != s.get("outputs", {}):
            bad.append(e["key"])
    return bad


def upstream_changed(run, st):
    return tree_hashes(os.path.join(run, "upstream")) != st.get("upstream", {})


def change_message(run, st):
    """None when nothing changed; else the line naming what changed."""
    parts = []
    if upstream_changed(run, st):
        parts.append("the upstream/ copy changed after init (restore it, or start a new run)")
    bad = changed_done(run, st)
    if bad:
        parts.append("outputs changed after finishing: %s (re-run the earliest with start --stage %s --rerun, "
                     "or restore them)" % (", ".join(bad), bad[0]))
    return "; ".join(parts) or None


def revalidate_changed(run, st, by):
    """Changed bytes are recorded and revalidated, never a stop. Every finished stage from the earliest changed one on
    becomes stale (changed bytes do not prove the stages that read them were refreshed). Only the earliest changed stage,
    and only when upstream/ did not change, is marked to re-finish on the bytes it holds: its next start opens a new
    revision without a dispatch and its finish runs the normal lifecycle (required outputs, checks, fallback, repair,
    routes) before the new hashes are accepted. Every later stage, changed or not, reruns with a normal dispatch. A
    changed upstream/ copy is re-recorded and every stage from the first one reruns. Each change is a problem line and
    one state event. Returns the changed stage keys (the first stage stands for upstream/)."""
    order = keys()
    bad, up = changed_done(run, st), upstream_changed(run, st)
    if not bad and not up:
        return []
    if up:
        st["upstream"] = tree_hashes(os.path.join(run, "upstream"))
        append_line(run, PROBLEMS, {"kind": "upstream-changed", "stage": order[0], "item": "upstream",
                                    "detail": "the upstream/ copy changed after init; recorded, and every stage reruns "
                                              "from %s to read it again" % order[0]})
    first = 0 if up else min(order.index(k) for k in bad)
    refinish = [] if up else [order[first]]
    for k in bad:
        if k in refinish:
            st["stages"][k]["refinish"] = {"at": now(), "revision": st["stages"][k].get("attempts", 0)}
        detail = ("outputs changed after finishing; kept, and re-finished on them (checks, repair) with every later "
                  "stage rerun" if k in refinish else "outputs changed after finishing, after an earlier change; the "
                  "stage reruns")
        append_line(run, PROBLEMS, {"kind": "outputs-changed", "stage": k, "item": k,
                                    "revision": st["stages"][k].get("attempts", 0), "detail": detail})
    stale = []
    for k in order[first:]:
        if k not in refinish:
            st["stages"][k].pop("refinish", None)   # only the earliest change keeps its bytes
        if is_done(st["stages"][k]["status"]) or st["stages"][k]["status"] == FAILED:
            st["stages"][k]["status"] = "stale"
            stale.append(k)
    write_state(run, st, {"event": "outputs-changed", "by": by, "changed": bad, "upstream": up, "stale": stale,
                          "refinish": refinish})
    print("changed    %s: recorded; re-finished on the kept outputs: %s; marked stale: %s"
          % (", ".join((["upstream"] if up else []) + bad), ", ".join(refinish) or "none", ", ".join(stale) or "none"),
          file=sys.stderr)
    return ([order[0]] if up else []) + bad


def next_key(st):
    for k in keys():
        if not is_done((st["stages"].get(k) or {}).get("status")):
            return k
    return "done"


# ---------------------------------------------------------------- hooks

def load_hook(path, name):
    if not os.path.isfile(path):
        raise Stop(2, "refused: hook module missing: %s" % rel(path, REPO))
    d = os.path.dirname(path)
    if d not in sys.path:
        sys.path.insert(0, d)
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except Exception as e:
        raise Stop(2, "%s: %s" % (name, e))
    return mod


def process_count(run):
    p = os.path.join(run, "stages", "intake", "intake.json")
    try:
        with open(p, encoding="utf-8") as f:
            procs = json.load(f).get("processes")
    except (OSError, ValueError, AttributeError) as e:
        raise Stop(2, "refused: stages/intake/intake.json cannot be read (%s); the process count is unknown" % e)
    if not isinstance(procs, list):
        raise Stop(2, "refused: stages/intake/intake.json has no processes list; the process count is unknown")
    return len(procs)


def upstream_grant_block(run, key):
    """The intake's write-grant problems on the upstream files, handed to the stage agent (never a refusal)."""
    try:
        with open(os.path.join(run, "stages", "intake", "intake.json"), encoding="utf-8") as f:
            checks = json.load(f).get("upstream_failing_checks") or []
    except (OSError, ValueError, AttributeError):
        return ""
    found = [c for c in checks if isinstance(c, dict) and c.get("check") == "write-grants"]
    if not found:
        return ""
    lines = ["## Upstream write-grant problems", "",
             "The intake ran the plugin's write-grant rules on the Future State Design output and found these. They "
             "are in the upstream files, which no stage edits. They never stop the run; each one is listed in What "
             "we still need to know.", ""]
    lines += ["- %s: %s" % (c.get("result"), c.get("detail")) for c in found]
    if key == "architecture":
        lines += ["", "Judge each `fail` against the map's own text and the future state, design to what the evidence "
                  "supports, and record the reading you took in agent.yaml's `upstream_readings` list: one object per "
                  "grant and Write grants column, `{\"grant\", \"column\", \"table_says\", \"read_as\" (for Steps: "
                  "the future-state step ids the grant covers), \"rules\" (the problem names it answers, e.g. "
                  "\"write-before-confirm\"), \"reason\", \"status\"}`. Use `\"status\": \"unresolved\"` when the "
                  "evidence cannot settle it. A `recorded` line was already answered upstream; follow that reading."]
    else:
        lines += ["", "Read the architecture stage's `upstream_readings` in agent.yaml and follow the reading it took."]
    return "\n".join(lines) + "\n\n"


def write_prompt(run, e, limits):
    """RUN/prompts/KEY.md: the set include (2+ processes), the stage prompt, the time target line."""
    src = os.path.join(REPO, e["folder"], "prompt.md")
    if not os.path.isfile(src):
        raise Stop(2, "refused: the stage prompt is missing: %s" % rel(src, REPO))
    text = ""
    if process_count(run) > 1:
        if not os.path.isfile(SET_INSTRUCTION):
            raise Stop(2, "refused: this run is a set of processes and the set include is missing: %s"
                          % rel(SET_INSTRUCTION, REPO))
        with open(SET_INSTRUCTION, encoding="utf-8") as f:
            text += f.read().rstrip("\n") + "\n\n"
    with open(src, encoding="utf-8") as f:
        text += f.read().rstrip("\n") + "\n\n"
    try:
        n = limits.target(run, e["key"])
    except Exception as ex:
        raise Stop(2, "limits.target: %s" % ex)
    text += upstream_grant_block(run, e["key"])
    text += repair_block(run, e["key"])
    text += ("Time target: %g minutes.\n" % n) if n is not None else "Time target: not set.\n"
    text = resolve_paths(text, run, e)
    os.makedirs(os.path.join(run, "prompts"), exist_ok=True)
    p = os.path.join(run, "prompts", e["key"] + ".md")
    with open(p, "w", encoding="utf-8") as f:
        f.write(text)
    return p


TEMPLATE_RE = re.compile(r"(?<![\w/.-])((?:skills/agent-design/)?(?:stages|reviews|simulation)/[\w.-]+/)?"
                         r"([\w.-]*template\.(?:json|yaml))")


def resolve_paths(text, run, e):
    """The one substitution helper for stage prompts: `<run>` becomes the run folder, every plugin path
    (skills/agent-design/...) becomes absolute, and every template (stages/<x>/template.json, or a bare template file
    "beside this prompt") is named by its path in the plugin, where templates live; the run holds none."""
    text = text.replace("<run>", run)
    folder = os.path.join(REPO, e["folder"]) if e.get("folder") else None

    def template(m):
        sub, name = m.group(1), m.group(2)
        if sub:
            sub = sub[len("skills/agent-design/"):] if sub.startswith("skills/agent-design/") else sub
            return os.path.join(SKILL, sub, name)
        return os.path.join(folder, name) if folder and os.path.isfile(os.path.join(folder, name)) else m.group(0)
    text = TEMPLATE_RE.sub(template, text)
    text = re.sub(r"(?<![\w/.-])skills/agent-design/", SKILL + "/", text)

    def repo_path(m):
        p = os.path.join(REPO, m.group(0))
        return p if os.path.exists(p) else m.group(0)
    return re.sub(r"(?<![\w/.-])(?:knowledge|skills)/[\w/.-]*[\w]", repo_path, text)


# ---------------------------------------------------------------- routes: every finding reaches its owner
#
# A route is a finding with an owning stage: a failing check whose owner (stages/index.json) is an earlier stage,
# Review A's findings.json, a coherence-gate route-<stage>.json, or Review B's leftovers. `next` consumes them in
# stage order: the earliest owner inside Agent Design is invalidated with every later stage and returned as the
# next stage, with its repair prompt; routes owned by a stage that rerun invalidates are subsumed (their findings
# re-check after the rerun). An owner outside Agent Design ("upstream"), a spent repair budget, or Review B's
# leftovers (one review round per run) become register items. Nothing asks the person which stage to repair.
#
# A stage's repair agent may judge that the cause of a failing check sits in an earlier stage. It then writes
# RUN/repairs/<stage>.json: {"owner_stage": "<an earlier stage key>", "reason": "<one line>"} instead of forcing a fix
# in its own stage. The second finish records that reading on the failing checks (repair_owner, repair_reason); the
# stage ends done with recorded problems and `next` routes those findings to the owner, which reruns, and every later
# stage reruns after it. An owner_stage that is not a known earlier stage is recorded and read as the failing stage
# itself (no route). The repair budget is per run, not per stage: once spent, a remaining finding is recorded as a
# named draft gap (a register item) and the run goes on to its hand-over.

ROUTES = "builder/routes.jsonl"          # one line per consumed route, register-readable
REVIEW_A_FINDINGS = "stages/review-a/findings.json"
GATE_DIR = "stages/coherence-gate"
RB_FIX_ROUND = FIX_ROUND
REPAIR_BUDGET = 2                        # route reruns per run (all stages together), not per stage
REPAIR_OWNERS = "repairs"                # RUN/repairs/<stage>.json: a repair agent's owning-stage reading


RB_PLACEMENT = "stages/review-b/route-placement.json"   # settle's in-round placement route (one review round)
UPSTREAM = "upstream"                    # a finding owned outside Agent Design (Future State Design, the snapshot)


def _read_route(p):
    """A route file's JSON: None when the file is absent; input that will not parse stops the command (exit 2)."""
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as e:
        raise Stop(2, "refused: route file %s does not parse (%s)" % (p, e.__class__.__name__))


def _shape_route(rid, source, path, run, why):
    """A parseable route file of the wrong shape: a recorded problem, never a rerun."""
    return {"id": "%s:%s" % (rid, sha(path)), "source": source, "owner": UPSTREAM, "file": rel(path, run),
            "findings": [{"problem": "route file has the wrong shape: %s" % why}], "register": True,
            "shape_problem": why}


def _list_or_absent(doc, k):
    """A route field that, when present (not missing and not null), must be a list."""
    return doc.get(k) is None or isinstance(doc[k], list)


def _name_or_absent(doc, k):
    """A route field that, when present (not missing and not null), must be a non-empty string."""
    return doc.get(k) is None or (isinstance(doc[k], str) and bool(doc[k]))


def _owner_shape(findings):
    """What is wrong with the findings' owner fields, or None: a present owner_stage (not missing and not null) must be
    a stage name. A wrong-typed owner is a recorded problem; the route's owner is never put in its place."""
    for i, f in enumerate(findings or []):
        if isinstance(f, dict) and not _name_or_absent(f, "owner_stage"):
            return "finding %s has an owner_stage that is not a stage name" % (
                f["id"] if isinstance(f.get("id"), str) and f["id"] else "#%d" % (i + 1))
    return None


def _fix_round_shape(fr):
    """What is wrong with the shape of Review B's fix-round.json, or None. Every present nested field is checked before
    any default applies: a wrong container, finding id or owner is a recorded problem, never dropped or inferred."""
    if not isinstance(fr, dict):
        return "not an object"
    if not _list_or_absent(fr, "open") or not all(isinstance(x, str) and x for x in fr.get("open") or []):
        return "open is not a list of string ids"
    if not _list_or_absent(fr, "findings"):
        return "findings is not a list"
    for f in fr.get("findings") or []:
        if not isinstance(f, dict) or not isinstance(f.get("id"), str) or not f["id"]:
            return "a finding is not an object with a string id"
        if not _name_or_absent(f, "owner_stage") or not _name_or_absent(f, "stage"):
            return "finding %s has an owner_stage or stage that is not a stage name" % f["id"]
    return None


def _split_by_owner(r, order):
    """One route per finding owner: a finding's own owner_stage wins over the route's owner. A finding owned by an
    upstream stage (or any name outside Agent Design) becomes a register item, never an Agent Design rerun."""
    groups = {}
    if r.get("agent_owner"):   # the repair agent's reading wins; a finding's own owner is kept as an alternative
        alt = sorted(set(f["owner_stage"] for f in r["findings"] or [] if isinstance(f, dict)
                         and isinstance(f.get("owner_stage"), str) and f["owner_stage"] != r["owner"]))
        sub = dict(r, alternatives=alt) if alt else dict(r)
        if r["owner"] not in order:
            sub["register"] = True
        return [sub]
    for f in r["findings"] or []:
        own = f.get("owner_stage") if isinstance(f, dict) else None
        if own is not None and not (isinstance(own, str) and own) and not r.get("shape_problem"):
            raise Stop(2, "refused: %s finding owner %r is not a stage name and was not recorded" % (r["source"], own))
        own = own if isinstance(own, str) and own else r["owner"]
        groups.setdefault(own, []).append(f)
    if not groups:
        groups[r["owner"]] = []
    out = []
    for own, fs in groups.items():
        sub = dict(r, owner=own, findings=fs)
        if own != r["owner"]:
            sub["id"] = "%s@%s" % (r["id"], own)
        if own not in order:
            sub["register"] = True
        out.append(sub)
    return out


def pending_routes(run, st):
    """Every unconsumed route, as {id, source, owner, file, findings}, one per finding owner."""
    seen = st.get("routes") or {}
    out = []
    owners = {}
    for e in load_index():
        for c in e.get("checks") or []:
            owners[(e["key"], c["name"])] = c.get("owner") or e["key"]
    order = keys()
    for k in order:
        s = st["stages"].get(k) or {}
        if not is_done(s.get("status")):
            continue
        for c in s.get("checks") or []:
            owner = c.get("repair_owner") or owners.get((k, c.get("name")), k)
            probs = list(c.get("problems")) if isinstance(c.get("problems"), list) else []
            upstream = [f for f in probs if isinstance(f, dict) and f.get("owner_stage")
                        and f.get("owner_stage") != k]
            if c.get("result") == "fail" and (owner != k or upstream):
                rid = "check:%s:%s:%s:%s" % (k, c["name"], s.get("attempts", 0), hashlib.sha256(
                    json.dumps(s.get("outputs") or {}, sort_keys=True).encode()).hexdigest()[:12])
                why = _owner_shape(probs)
                if why:
                    out.append({"id": rid, "source": "check %s (stage %s)" % (c["name"], k), "owner": UPSTREAM,
                                "file": "checks/%s.json" % c["name"], "register": True, "shape_problem": why,
                                "findings": [{"problem": "route has the wrong shape: %s" % why}]})
                    continue
                if owner == k:   # the check's own stage keeps its findings; only the ones naming another owner route
                    probs = upstream
                out.append({"id": rid, "source": "check %s (stage %s)" % (c["name"], k), "owner": owner,
                            "file": "checks/%s.json" % c["name"], "findings": probs,
                            "agent_owner": bool(c.get("repair_owner"))})
    fa = os.path.join(run, REVIEW_A_FINDINGS)
    if is_done((st["stages"].get("review-a") or {}).get("status")):
        doc = _read_route(fa)
        if doc is not None and (not isinstance(doc, dict) or not _list_or_absent(doc, "items")
                                or not _name_or_absent(doc, "route_to") or not _name_or_absent(doc, "revision")):
            out.append(_shape_route("review-a", "Review A", fa, run,
                                    "not an object with an items list and a string route_to and revision"))
        elif doc and doc.get("items") and _owner_shape(doc["items"]):
            out.append(_shape_route("review-a", "Review A", fa, run, _owner_shape(doc["items"])))
        elif doc and doc.get("items"):
            rid = "review-a:%s" % (doc.get("revision") or sha(fa))
            out.append({"id": rid, "source": "Review A", "owner": doc.get("route_to") or "architecture",
                        "file": REVIEW_A_FINDINGS, "findings": doc["items"]})
    gd = os.path.join(run, GATE_DIR)
    if is_done((st["stages"].get("coherence-gate") or {}).get("status")) and os.path.isdir(gd):
        for n in sorted(os.listdir(gd)):
            if n.startswith("route-") and n.endswith(".json"):
                p = os.path.join(gd, n)
                doc = _read_route(p)
                if not isinstance(doc, dict) or not _list_or_absent(doc, "failures") or not _name_or_absent(doc, "stage"):
                    out.append(_shape_route("gate:%s" % n, "coherence gate", p, run,
                                            "not an object with a failures list and a string stage"))
                    continue
                if _owner_shape(doc.get("failures")):
                    out.append(_shape_route("gate:%s" % n, "coherence gate", p, run, _owner_shape(doc["failures"])))
                    continue
                out.append({"id": "gate:%s:%s" % (n, sha(p)), "source": "coherence gate",
                            "owner": doc.get("stage") or UPSTREAM,
                            "file": rel(p, run), "findings": doc.get("failures") or []})
    rp = os.path.join(run, RB_PLACEMENT)
    doc = _read_route(rp)
    if doc is not None:
        if not isinstance(doc, dict) or doc.get("stage") != "placement" or not isinstance(doc.get("rules"), list):
            out.append(_shape_route("review-b-placement", "Review B placement", rp, run,
                                    "not an object naming stage placement and its rules"))
        else:
            out.append({"id": "review-b-placement:%s" % sha(rp), "source": "Review B placement", "owner": "placement",
                        "file": RB_PLACEMENT, "findings": [{"rules": doc["rules"], "findings": doc.get("findings")}],
                        "in_round": True, "prompt": doc.get("prompt")})
    if is_done((st["stages"].get(REVIEW_B) or {}).get("status")):
        fp = os.path.join(run, RB_FIX_ROUND)
        fr = _read_route(fp)
        why = _fix_round_shape(fr) if fr is not None else None
        if why:
            out.append(_shape_route("review-b", "Review B leftover", fp, run, why))
        elif fr and fr.get("open"):
            by = dict((f["id"], f) for f in fr.get("findings") or [])
            for fid in fr["open"]:
                f = by.get(fid) or {"id": fid}
                out.append({"id": "review-b:%s" % fid, "source": "Review B leftover",
                            "owner": f.get("owner_stage") or f.get("stage") or UPSTREAM,
                            "file": RB_FIX_ROUND, "findings": [f], "register": True})
    out = [x for r in out for x in _split_by_owner(r, order)]
    out = [r for r in out if r["id"] not in seen]
    out.sort(key=lambda r: order.index(r["owner"]) if r["owner"] in order else -1)
    return out


def repair_block(run, key):
    """The repair section of a rerun stage's prompt: the routed findings it must answer."""
    try:
        s = load_state(run)["stages"].get(key) or {}
    except (OSError, ValueError):
        return ""
    rp = s.get("repair")
    if not rp or rp.get("done"):
        return ""
    lines = ["## Repair", "", "This stage reruns to repair findings routed to it. Read each source, fix what it names in "
             "this stage's outputs, and leave a finding you cannot fix with evidence as an open item:", ""]
    for r in rp.get("routes") or []:
        lines.append("- %s: `%s` (%d finding(s))" % (r["source"], os.path.join(run, r["file"]), r["count"]))
    return "\n".join(lines) + "\n\n"


def _stage_name(x):
    """A stage name as a key: trimmed, lower case, spaces and underscores read as hyphens."""
    return re.sub(r"[\s_]+", "-", x.strip().lower()) if isinstance(x, str) else None


def read_repair_owner(run, key, since):
    """The repair agent's owning-stage reading for KEY (RUN/repairs/KEY.json), as (owner or None, reading line).
    Never a stop: a missing or older file is no reading; an unreadable file, a missing owner_stage, or a name that is
    not a stage before KEY is recorded and read as KEY itself (owner None)."""
    p = os.path.join(run, REPAIR_OWNERS, key + ".json")
    try:
        if since is not None and os.path.getmtime(p) < since:
            return None, None
        with open(p, encoding="utf-8") as f:
            doc = json.load(f)
    except FileNotFoundError:
        return None, None
    except (OSError, ValueError) as ex:
        return None, "the repair agent's owning-stage file could not be read (%s); read as %s itself" % (
            ex.__class__.__name__, key)
    doc = doc if isinstance(doc, dict) else {}
    raw = doc.get("owner_stage")
    reason = doc.get("reason") if isinstance(doc.get("reason"), str) and doc["reason"].strip() else "no reason given"
    name, order = _stage_name(raw), keys()
    if name == key:
        return None, None
    if name in order and order.index(name) < order.index(key):
        return name, reason.strip()
    return None, "the repair agent named %r as the owning stage, which is not a stage before %s; read as %s itself " \
                 "(%s)" % (raw, key, key, reason.strip())


def consume_routes(run, st):
    """Consume pending routes (earliest owner first). Returns the owner rerun, or None."""
    routes = pending_routes(run, st)
    for k, s in st["stages"].items():   # an in-round placement repair is over once settle consumed its route
        rp = s.get("repair") or {}
        if rp.get("in_round") and not rp.get("done") and not os.path.exists(os.path.join(run, RB_PLACEMENT)):
            rp["done"] = True
    if not routes:
        return None
    order = keys()
    st.setdefault("routes", {})
    rerun, consumed, in_round = None, [], None
    for r in routes:
        owner = r["owner"]
        rec = {"source": r["source"], "owner": owner, "file": r["file"], "count": len(r["findings"]), "at": now(),
               "revision": (st["stages"].get(owner) or {}).get("attempts")}
        if r.get("alternatives"):   # owners the findings named, set aside for the repair agent's reading
            rec["alternatives"] = r["alternatives"]
        if r.get("shape_problem"):
            rec["action"] = "recorded problem (%s)" % r["shape_problem"]
        elif r.get("in_round"):
            # Review B's single round: placement reruns with settle's fix prompt and settle re-judges; no stage is
            # invalidated and no second review round runs
            in_round = r
            rec.update(action="in-round rerun inside %s" % REVIEW_B, prompt=r.get("prompt"))
        elif r.get("register") or owner not in order:
            rec["action"] = "register item"
        elif rerun is not None and owner in order and order.index(owner) >= order.index(rerun):
            rec["action"] = "subsumed by the rerun of %s" % rerun
        elif rerun is not None:
            rec["action"] = "subsumed by the rerun of %s" % rerun
        elif (st.get("route_reruns") or 0) >= REPAIR_BUDGET:
            rec["action"] = ("register item (the run's repair budget is spent; recorded as a draft gap for %s)"
                             % owner)
        else:
            rerun = owner
            rec["action"] = "rerun"
        st["routes"][r["id"]] = rec
        consumed.append(dict(rec, id=r["id"]))
        append_line(run, ROUTES, dict(rec, id=r["id"]))
        if rec["action"].startswith(("register item", "recorded problem")):
            for f in r["findings"] or [{}]:
                fid = f.get("id") if isinstance(f, dict) else None
                append_line(run, PROBLEMS, {"kind": "register-item" if rec["action"].startswith("register") else
                                            "route-shape", "stage": owner, "item": fid or r["source"],
                                            "source": r["source"], "route": r["id"],
                                            "detail": "%s: %s" % (r["file"], json.dumps(f, sort_keys=True)[:400])})
    stale = []
    if rerun is not None:
        s = st["stages"][rerun]
        s["route_reruns"] = (s.get("route_reruns") or 0) + 1
        st["route_reruns"] = (st.get("route_reruns") or 0) + 1
        s["repair"] = {"routes": [{"id": c["id"], "source": c["source"], "file": c["file"], "count": c["count"]}
                                  for c in consumed if c["owner"] == rerun or c["action"].startswith("subsumed")]}
        for k in order[order.index(rerun):]:
            if st["stages"][k]["status"] not in ("pending",):
                st["stages"][k]["status"] = "stale"
                stale.append(k)
    if in_round is not None:
        s = st["stages"]["placement"]
        s["repair"] = {"in_round": REVIEW_B, "prompt": in_round.get("prompt"),
                       "routes": [{"id": in_round["id"], "source": in_round["source"], "file": in_round["file"],
                                   "count": len(in_round["findings"])}]}
    write_state(run, st, {"event": "routes-consumed", "routes": consumed, "rerun": rerun, "stale": stale,
                          "in_round": bool(in_round)})
    if in_round is not None and rerun is None:
        print("repair: placement reruns inside the %s round for the rules a kept fix added: carry out the placement "
              "fix prompt %s (edits stay under stages/placement/), then run fix_round.py settle --run %s again; "
              "settle re-judges and closes the round" % (REVIEW_B, os.path.join(run, in_round.get("prompt") or ""), run),
              file=sys.stderr)
        return "placement"
    reg = [c for c in consumed if c["action"].startswith(("register item", "recorded problem"))]
    for c in consumed:
        if c not in reg:
            print("route      %s -> %s: %s" % (c["source"], c["owner"], c["action"]), file=sys.stderr)
    if reg:
        print("route      %d finding(s) recorded as register items (%s)" % (
            len(reg), ", ".join(sorted(set(c["source"] for c in reg)))), file=sys.stderr)
    return rerun


# ---------------------------------------------------------------- init

def init_reading(notes, what, reading, why, still_need=False):
    """One recorded reading {what, reading, why}; still_need marks one the person is asked about (the intake turns it
    into a What we still need to know item)."""
    rec = {"what": what, "reading": reading, "why": why}
    if still_need:
        rec["still_need"] = True
    notes.append(rec)
    return rec


def read_freeze(doc, what, notes=None):
    """The freeze block {cutoff, process_versions} of DOC (a state or a baseline file). A missing block, a missing cutoff
    or pins under another key are read leniently and recorded in NOTES; without NOTES a missing block still stops."""
    fr = doc.get("freeze") if isinstance(doc, dict) and "freeze" in doc else doc
    if notes is None and (not isinstance(fr, dict) or "cutoff" not in fr
                          or not isinstance(fr.get("process_versions"), list)):
        raise Stop(2, "refused: %s has no freeze block (cutoff and process_versions)" % what)
    if not isinstance(fr, dict):
        init_reading(notes, "freeze", "no freeze block; the cutoff is unknown and no process versions are pinned",
                     "%s carries no freeze block" % what, still_need=True)
        return {"cutoff": "unknown", "process_versions": []}
    cutoff = fr.get("cutoff")
    if not (isinstance(cutoff, str) and cutoff):
        init_reading(notes, "freeze cutoff", "the cutoff is unknown", "%s freeze block has no cutoff" % what,
                     still_need=True)
        cutoff = "unknown"
    pins = fr.get("process_versions")
    if not isinstance(pins, list):
        # pins under another key: the one list whose entries are objects naming a process_id
        found = [(k, v) for k, v in sorted(fr.items()) if isinstance(v, list) and v
                 and all(isinstance(x, dict) and "process_id" in x for x in v)]
        if found:
            init_reading(notes, "freeze pins", "process versions read from freeze.%s" % found[0][0],
                         "%s freeze block has no process_versions list; freeze.%s is the list of pinned processes"
                         % (what, found[0][0]))
            pins = found[0][1]
        else:
            init_reading(notes, "freeze pins", "no process versions are pinned",
                         "%s freeze block holds no list of pinned processes" % what, still_need=True)
            pins = []
    usable = [x for x in pins if isinstance(x, dict) and isinstance(x.get("process_id"), str) and x["process_id"]]
    if len(usable) != len(pins):
        init_reading(notes, "freeze pins", "%d unusable pin(s) set aside; %d kept" % (len(pins) - len(usable), len(usable)),
                     "%s holds pins that are not objects naming a process_id" % what)
    return {"cutoff": cutoff, "process_versions": usable}


def compare_freeze(fr, base):
    """Differences between the upstream freeze block and the baseline, each naming the process and field."""
    diffs = []
    if fr.get("cutoff") != base.get("cutoff"):
        diffs.append("cutoff: upstream %r, baseline %r" % (fr.get("cutoff"), base.get("cutoff")))
    sides = {}
    for side, block in (("upstream", fr), ("baseline", base)):
        seen = {}
        for pv in block.get("process_versions") or []:
            pid = pv.get("process_id") if isinstance(pv, dict) else None
            if pid in seen:
                diffs.append("process %s: listed twice in the %s" % (pid, side))
            seen[pid] = pv
        sides[side] = seen
    for pid in sorted(set(sides["upstream"]) | set(sides["baseline"]), key=str):
        if pid not in sides["baseline"]:
            diffs.append("process %s: missing from the baseline" % pid)
        elif pid not in sides["upstream"]:
            diffs.append("process %s: missing from the upstream freeze" % pid)
        else:
            for field in ("version_no", "created_at"):
                a, b = sides["upstream"][pid].get(field), sides["baseline"][pid].get(field)
                if a != b:
                    diffs.append("process %s: %s upstream %r, baseline %r" % (pid, field, a, b))
    return diffs


def plan_copy(src, notes=None):
    """Run-relative paths of the files to copy. A symbolic link whose target resolves to a file inside SRC is copied
    (its target's content); a link to a folder inside SRC is skipped (its files are copied where they live); a link
    leading outside SRC is skipped, so nothing outside the person's folder is copied. Each link is recorded in NOTES.
    Without NOTES a link still stops. A special file stops."""
    files, root = [], os.path.realpath(src)
    for dp, dns, fns in os.walk(src):
        for n in sorted(dns + fns):
            p = os.path.join(dp, n)
            mode = os.lstat(p).st_mode
            if stat.S_ISLNK(mode):
                if notes is None:
                    raise Stop(2, "refused: FSD_RUN holds a symbolic link: %s" % rel(p, src))
                target = os.path.realpath(p)
                inside = os.path.commonpath([root, target]) == root
                if inside and os.path.isfile(target) and rel(p, src) != EXCLUDED:
                    files.append(rel(p, src))
                    init_reading(notes, "linked file %s" % rel(p, src), "copied the file it points to",
                                 "the link resolves to %s inside the folder" % rel(target, root))
                elif inside:
                    init_reading(notes, "linked folder %s" % rel(p, src), "skipped",
                                 "it points to a folder inside the folder, whose files are copied where they live")
                else:
                    init_reading(notes, "linked file %s" % rel(p, src), "skipped",
                                 "it points outside the supplied folder, so it is not copied")
                continue
            if not (stat.S_ISDIR(mode) or stat.S_ISREG(mode)):
                raise Stop(2, "refused: FSD_RUN holds a special file: %s" % rel(p, src))
            if stat.S_ISREG(mode) and rel(p, src) != EXCLUDED:
                files.append(rel(p, src))
    return sorted(files)


def git_head():
    try:
        p = subprocess.run(["git", "-C", SKILL, "rev-parse", "HEAD"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError, ValueError):
        return "unknown"
    return (p.stdout.strip() if p.returncode == 0 else "") or "unknown"


def upstream_unfinished(up):
    """[(stage, status)] for every FSD stage not done; a done with recorded problems ("done, ...") counts as done."""
    stages = up.get("stages") if isinstance(up, dict) else None
    if not isinstance(stages, dict):
        return []
    out = []
    for k, v in stages.items():
        status = v.get("status") if isinstance(v, dict) else v
        if not (isinstance(status, str) and (status == "done" or status.startswith("done,")
                                             or status.startswith("done with"))):
            out.append((k, status if isinstance(status, str) else "not recorded"))
    return out


def read_upstream_state(src, notes):
    """The upstream state.json as an object ({} when missing, unreadable or not an object; each recorded in NOTES)."""
    sp = os.path.join(src, "state.json")
    root = os.path.realpath(src)
    if os.path.lexists(sp) and os.path.commonpath([root, os.path.realpath(sp)]) != root:
        init_reading(notes, "run state", "read without a run state; the intake reads the files themselves",
                     "state.json leads outside the supplied folder, so it is not read")
        return {}
    if not os.path.isfile(sp):
        init_reading(notes, "run state", "read without a run state; the intake reads the files themselves",
                     "the supplied folder has no state.json")
        return {}
    try:
        with open(sp, encoding="utf-8") as f:
            up = json.load(f)
    except (OSError, UnicodeDecodeError, ValueError) as e:
        init_reading(notes, "run state", "read without a run state; the intake reads the files themselves",
                     "state.json cannot be read (%s)" % e)
        return {}
    if not isinstance(up, dict):
        init_reading(notes, "run state", "read without a run state; the intake reads the files themselves",
                     "state.json is not an object")
        return {}
    return up


def cmd_init(a):
    run, src = os.path.realpath(a.run), os.path.realpath(getattr(a, "from"))
    if os.path.isdir(run) and os.listdir(run):
        raise Stop(2, "refused: RUN is not empty; a new run needs an absent or empty folder")
    if not os.path.exists(src):
        raise Stop(2, "refused: FSD_RUN does not exist: %s" % getattr(a, "from"))
    if os.path.commonpath([run, src]) in (run, src):
        raise Stop(2, "refused: RUN and FSD_RUN are the same folder or one is inside the other")
    notes = []
    single_doc = os.path.isfile(src)
    if single_doc:
        init_reading(notes, "source", "a single document, copied as design evidence for the stages",
                     "FSD_RUN is a file (%s), not a Future State Design run folder" % os.path.basename(src))
        up = {}
    else:
        up = read_upstream_state(src, notes)
    for k, v in upstream_unfinished(up):
        init_reading(notes, "upstream stage %s" % k, "carried as it is; the intake reads what it produced",
                     "the Future State Design run records %s as %s" % (k, v))
    fr = read_freeze({"freeze": up.get("freeze")}, "FSD_RUN state.json", notes) if up else \
        {"cutoff": "unknown", "process_versions": []}
    if not up and not single_doc:
        init_reading(notes, "freeze", "the cutoff is unknown and no process versions are pinned",
                     "there is no readable run state to carry a freeze block", still_need=True)
    baseline = {"compared": False}
    if a.baseline:
        try:
            with open(a.baseline, encoding="utf-8") as f:
                base = read_freeze(json.load(f), "the baseline file", notes)
        except (OSError, UnicodeDecodeError, ValueError) as e:
            base = None
            init_reading(notes, "baseline", "not compared", "the baseline file cannot be read (%s)" % e)
        if base is not None:
            diffs = compare_freeze(fr, base)
            baseline = {"compared": True, "sha256": sha(a.baseline)}
            if diffs:
                baseline["differences"] = diffs
                init_reading(notes, "baseline", "the upstream freeze is used; it differs from the baseline: %s"
                             % "; ".join(diffs), "the design is drawn from the evidence the upstream run froze",
                             still_need=True)
    if not os.path.isfile(PAGE):
        raise Stop(2, "refused: hook module missing: %s; nothing written" % rel(PAGE, REPO))
    files = [os.path.basename(src)] if single_doc else plan_copy(src, notes)
    os.makedirs(os.path.join(run, "upstream"), exist_ok=True)
    for r in files:
        dst = os.path.join(run, "upstream", r)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copyfile(src if single_doc else os.path.join(src, r), dst)
    upstream = tree_hashes(os.path.join(run, "upstream"))
    freeze = {"cutoff": fr["cutoff"], "process_versions": fr["process_versions"]}
    st = {"schema": "agent-design.state/0", "skill": "agent-design", "skill_commit": git_head(), "created_at": now(),
          "upstream_folder": os.path.basename(src), "upstream": upstream, "freeze": freeze, "baseline": baseline,
          "init_readings": notes, "stages": {k: {"status": "pending", "attempts": 0} for k in keys()}}
    write_state(run, st, {"event": "run-init", "files": len(upstream), "excluded": [EXCLUDED],
                          "freeze": freeze, "baseline": baseline, "skill_commit": st["skill_commit"],
                          "init_readings": len(notes)})
    # the page scaffold at launch (key criterion 19): the page hook scaffolds on its first call; "init" is no stage
    # key, so no slot is filled
    # A page problem never stops setup: it is a recorded problem, and the next boundary tries again.
    try:
        load_hook(PAGE, "page_build").fill_stage(run, "init")
        append_event(run, {"event": "page-scaffold", "path": "outputs/agent-design.html"})
    except Exception as ex:
        append_line(run, PROBLEMS, {"kind": "page-hook", "item": "init", "detail": "page.build.fill_stage: %s" % ex})
    print("run ready:", run)
    for n in notes:
        print("reading, recorded: %s: %s (%s)" % (n["what"], n["reading"], n["why"]), file=sys.stderr)
    if not baseline["compared"]:
        print(NO_BASELINE)
    print("next stage:", keys()[0])
    if os.path.isfile(LIMITS):
        show_customer(run, load_hook(LIMITS, "limits"), always=True)
    return 0


# ---------------------------------------------------------------- next, start, finish

def cmd_next(a):
    run = os.path.realpath(a.run)
    st = open_run(run)
    revalidate_changed(run, st, "next")   # changed bytes: recorded and rechecked, never a stop
    rerun = consume_routes(run, st)
    in_round = rerun and (st["stages"].get(rerun, {}).get("repair") or {}).get("in_round")
    nk = rerun if in_round else next_key(st)
    print(nk)
    if nk == "done" and os.path.isfile(LIMITS):
        show_customer(run, load_hook(LIMITS, "limits"))   # final completion is a stage boundary too
    if rerun and not in_round:
        print("repair: %s reruns for the routed findings; start it with start --stage %s, its prompt carries them"
              % (rerun, rerun), file=sys.stderr)
    return 0


def cmd_repair(a):
    """Consume every pending route now and print the stage to rerun (the same work `next` does first)."""
    return cmd_next(a)


def cmd_start(a):
    run, key = os.path.realpath(a.run), a.stage
    st = open_run(run)
    e = stage_entry(key)
    order = keys()
    s = st["stages"][key]
    # changed bytes: recorded and revalidated before anything else, with or without --rerun, never a stop; when that
    # leaves an earlier stage to rerun first, start names it and starts nothing (`next` prints it too)
    if revalidate_changed(run, st, key):
        first = next((k for k in order[:order.index(key)] if not is_done(st["stages"][k]["status"])), None)
        if first is not None:
            print("start: %s reruns first (changed outputs were recorded and the stages that read them marked stale); "
                  "start --stage %s" % (first, first))
            return 1
    kept = []   # changed stages are never re-recorded here: revalidate_changed above re-finishes or reruns them
    if is_done(s["status"]) and not a.rerun:
        raise Stop(2, "refused: %s is already done; use --rerun to run it again" % key)
    missing = [k for k in order[:order.index(key)] if not is_done(st["stages"][k]["status"])]
    if missing:
        raise Stop(2, "refused: %s needs %s done first" % (key, ", ".join(missing)))
    if s.get("refinish") and not a.rerun:
        return refinish_start(run, st, key, e)
    s.pop("refinish", None)
    limits = load_hook(LIMITS, "limits") if e["kind"] in ("agent", "review") else None
    prompt = write_prompt(run, e, limits) if limits else None
    stale = []
    if a.rerun:
        for k in order[order.index(key) + 1:]:
            if is_done(st["stages"][k]["status"]) or st["stages"][k]["status"] == FAILED:
                st["stages"][k]["status"] = "stale"
                stale.append(k)
    if key == "review-a":   # bind the verdict this run writes to the architecture revision it reads
        try:
            load_hook(os.path.join(SKILL, "reviews", "review-a", "route.py"), "review_a_route").record_reading(run)
        except Exception as e:   # recorded, never a stop: route.py then treats the verdict as unbound
            append_line(run, PROBLEMS, {"kind": "review-a-reading", "stage": key, "item": key,
                                        "detail": "could not record the revision Review A reads (%s)" % e})
    s.update({"status": "running", "started_at": now(), "attempts": s.get("attempts", 0) + 1})
    rec = {"event": "stage-start", "stage": key, "attempt": s["attempts"], "rerun": bool(a.rerun), "stale": stale}
    if kept:
        rec["kept_changed"] = kept
    if s.get("repair"):
        rec["repair"] = s["repair"].get("routes")
    if prompt:
        rec["prompt"] = {"path": rel(prompt, run), "sha256": sha(prompt)}
        s["prompt"] = rel(prompt, run)
    write_state(run, st, rec)
    try:   # the person's page shows this stage as running; never a stop
        page = load_hook(PAGE, "page_build")
        if hasattr(page, "start_stage"):
            page.start_stage(run, key)
    except Exception as ex:
        append_line(run, PROBLEMS, {"kind": "page-hook", "item": key, "detail": "page.build.start_stage: %s" % ex})
    print(prompt if prompt else "started %s" % key)
    if stale:
        print("marked stale: %s" % ", ".join(stale))
    return 0


def refinish_start(run, st, key, e):
    """Open a new revision of a stage whose outputs changed after finishing, keeping them: no prompt and no dispatch;
    the stage's start time is set just before its oldest output so finish judges the kept files as this revision's.
    A missing or unreadable output then gets the usual one retry; checks, fallback and repair run as in any finish."""
    s = st["stages"][key]
    rf = s.pop("refinish")
    times = [os.path.getmtime(os.path.join(dp, f)) for dp, _, fs in os.walk(os.path.join(run, e["outputs"]))
             for f in fs]
    t0 = (datetime.fromtimestamp(min(times) - 1, timezone.utc).isoformat() if times else now())
    s.update({"status": "running", "started_at": t0, "attempts": s.get("attempts", 0) + 1})
    write_state(run, st, {"event": "stage-start", "stage": key, "attempt": s["attempts"], "rerun": False,
                          "stale": [], "refinish": rf})
    print("refinish: %s keeps its changed outputs; run finish without rerunning the prompt --run %s --stage %s" % (key, run, key))
    return 0


def preserve_report(run, path):
    """Keep the exact bytes, including malformed reports, before any later writer replaces them."""
    if not os.path.isfile(path):
        return None
    digest = sha(path)
    dst = os.path.join(run, "checks", "history", digest + ".json")
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    if not os.path.exists(dst):
        shutil.copyfile(path, dst)
    return {"path": rel(dst, run), "sha256": digest}


def run_check(run, c):
    out = os.path.join(run, "checks", c["name"] + ".json")
    previous = preserve_report(run, out)
    result = _run_check(run, c)
    evidence = preserve_report(run, out)
    # A missing executable did not produce the old report still at out.
    if result.get("detail") == "check file not found":
        evidence = None
    result["evidence"] = evidence
    append_line(run, "checks/history.jsonl", {"check": c["name"], "previous": previous,
                                               "result": result})
    return result


def _run_check(run, c):
    """pass, fail or unchecked, and the report's counts. Exit code decides; a report must back it. Only the check's
    own report (checks/<name>.json) is archived by run_check then cleared; a kept fallback report (checks/<name>.fallback.json) never is."""
    path = os.path.join(REPO, c["path"])
    out = os.path.join(run, "checks", c["name"] + ".json")
    rec = {"name": c["name"], "path": c["path"], "fallback": c["fallback"]}
    if not os.path.isfile(path):
        return dict(rec, result="unchecked", detail="check file not found")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    if os.path.exists(out):
        os.remove(out)   # a report left by an earlier run never stands in for this one
    try:
        p = subprocess.run([sys.executable, path] + list(c.get("args") or []) + ["--run", run, "--out", out],
                           cwd=REPO, capture_output=True, text=True, timeout=600,
                           env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1", ADK_NO_OPEN="1"))
    except (OSError, subprocess.SubprocessError) as ex:
        return dict(rec, result="unchecked", crashed=True, detail=str(ex))
    try:
        with open(out, encoding="utf-8") as f:
            report = json.load(f)
        if not isinstance(report, dict):
            raise ValueError("not an object")
    except (OSError, ValueError):
        return dict(rec, result="unchecked", crashed=True, exit_code=p.returncode, detail="no readable report")
    rec.update(exit_code=p.returncode, counts=report.get("counts"))
    if p.returncode == 0 and report.get("result") == "pass":
        return dict(rec, result="pass")
    if p.returncode == 1:
        return dict(rec, result="fail", problems=report.get("problems"))
    return dict(rec, result="unchecked", crashed=True,
                detail="exit %d with result %r" % (p.returncode, report.get("result")))


def is_done(status):
    return status in (DONE, DONE_PROBLEMS)


def started_ts(s):
    try:
        return datetime.fromisoformat(s["started_at"]).timestamp()
    except (KeyError, ValueError, TypeError):
        return None


# outputs with a shared reader elsewhere in the plugin: they are readable only if that reader takes them
# (check_architecture.load_agent reads agent.yaml with json.load)
JSON_READ = ("agent.yaml",)


def readable(p):
    """The file exists and parses: JSON for .json and for outputs whose shared reader is JSON (agent.yaml), one JSON
    value a line for .jsonl, non-empty UTF-8 text otherwise."""
    try:
        with open(p, "rb") as f:
            raw = f.read()
        text = raw.decode("utf-8")
        if p.endswith(".json") or os.path.basename(p) in JSON_READ:
            json.loads(text)
        elif p.endswith(".jsonl"):
            for line in text.splitlines():
                if line.strip():
                    json.loads(line)
        return bool(text.strip())
    except (OSError, ValueError):
        return False


def output_gaps(run, e, s):
    """The stage's required outputs that are missing, unreadable or older than the stage's start, one dict each.
    An index entry without required_outputs (an older index) has nothing to judge."""
    gaps = []
    t0 = started_ts(s)
    for pat in e.get("required_outputs") or []:
        hits = sorted(glob.glob(os.path.join(run, pat)))
        hits = [h for h in hits if os.path.isfile(h)]
        if not hits:
            gaps.append({"item": pat, "detail": "missing"})
            continue
        for h in hits:
            if not readable(h):
                gaps.append({"item": rel(h, run), "detail": "unreadable"})
            elif t0 is not None and os.path.getmtime(h) < t0:
                gaps.append({"item": rel(h, run), "detail": "older than the stage start, so not written by it"})
    return gaps


def run_commands(run, lines, show=False):
    """Run command lines from the index in order (RUN stands for the run folder): [{line, exit}]."""
    out = []
    for line in lines:
        parts = line.split()
        args = [run if x == "RUN" else x for x in parts[1:]]
        try:
            p = subprocess.run([sys.executable, os.path.join(REPO, parts[0])] + args, cwd=REPO, capture_output=True,
                               text=True, timeout=1800, env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1", ADK_NO_OPEN="1"))
            code = p.returncode
            if show:
                sys.stdout.write(p.stdout)
                sys.stderr.write(p.stderr)
        except (OSError, subprocess.SubprocessError):
            code = None
        out.append({"line": line, "exit": code})
    return out


def command_round(s, rev):
    """0 before the stage's automatic retry in this revision, 1 after it."""
    return 1 if s.get("retry_rev") == rev else 0


def record_exits(s, rev, done):
    rnd = command_round(s, rev)
    s["command_exits"] = list(s.get("command_exits") or []) + [dict(x, revision=rev, round=rnd) for x in done]


def unrecorded_lines(e, s, rev):
    """The stage's command lines with no exit recorded for this revision (and this side of the retry)."""
    rnd = command_round(s, rev)
    have = set(x.get("line") for x in s.get("command_exits") or []
               if x.get("revision") == rev and x.get("round", 0) == rnd)
    return [line for line in e.get("command") or [] if line not in have]


def append_line(run, relpath, rec):
    p = os.path.join(run, relpath)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps(dict({"at": now()}, **rec)) + "\n")


def read_fallback(run, e, s, path):
    """A fallback agent's report, checked: one check of this stage, this revision, a reason, applied verdicts."""
    names = [c["name"] for c in e["checks"]]
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
    except (OSError, ValueError) as ex:
        raise Stop(2, "refused: the fallback report %s cannot be read (%s); nothing recorded" % (path, ex))
    if not isinstance(doc, dict):
        raise Stop(2, "refused: the fallback report %s is not a JSON object; nothing recorded" % path)
    if doc.get("check") not in names:
        raise Stop(2, "refused: the fallback report names check %r, which is not a check of %s (%s); nothing "
                      "recorded" % (doc.get("check"), e["key"], ", ".join(names) or "no checks"))
    if doc.get("revision") != s.get("attempts", 0):
        raise Stop(2, "refused: the fallback report is for revision %r, but %s is at revision %r; nothing recorded"
                      % (doc.get("revision"), e["key"], s.get("attempts", 0)))
    if not (isinstance(doc.get("why"), str) and doc["why"].strip()):
        raise Stop(2, "refused: the fallback report gives no reason (`why`) the check is wrong or crashed; nothing "
                      "recorded")
    applied = doc.get("applied")
    # a readable verdict is normalised (trimmed, lower case, passed/failed read as pass/fail); each change is recorded
    doc["readings"] = []
    for v in applied if isinstance(applied, list) else []:
        if isinstance(v, dict) and isinstance(v.get("verdict"), str):
            n = v["verdict"].strip().lower()
            n = {"passed": "pass", "failed": "fail"}.get(n, n)
            if n != v["verdict"]:
                doc["readings"].append("verdict %r read as %r for %s" % (v["verdict"], n, v.get("item")))
                v["verdict"] = n
    ok = isinstance(applied, list) and applied and all(
        isinstance(v, dict) and isinstance(v.get("item"), str) and v["item"].strip()
        and v.get("verdict") in ("pass", "fail", "open") and isinstance(v.get("reason"), str) and v["reason"].strip()
        for v in applied)
    if not ok:
        raise Stop(2, "refused: the fallback report only explains; it must also apply the check's intent to the stage "
                      "output by hand: an `applied` list with one {item, verdict (pass, fail or open), reason} per "
                      "item the check would judge. Nothing recorded; %s is not done." % e["key"])
    return doc


def cmd_exec(a):
    """Run the running stage's command lines (all, or the one numbered --line from 1) and record each exit in
    state.json for this revision; finish reads them, and a non-zero exit is never recorded as plain done."""
    run, key = os.path.realpath(a.run), a.stage
    st = open_run(run)
    e = stage_entry(key)
    s = st["stages"][key]
    if s["status"] != "running":
        raise Stop(2, "refused: %s is %s, not running" % (key, s["status"]))
    lines = list(e.get("command") or [])
    if a.line is not None:
        if not 1 <= a.line <= len(lines):
            raise Stop(2, "refused: %s has %d command line(s); --line %d is not one" % (key, len(lines), a.line))
        lines = [lines[a.line - 1]]
    if not lines:
        raise Stop(2, "refused: %s has no command in stages/index.json" % key)
    done = run_commands(run, lines, show=True)
    rev = s.get("attempts", 0)
    record_exits(s, rev, done)
    write_state(run, st, {"event": "stage-command", "stage": key, "revision": rev, "round": command_round(s, rev),
                          "commands": done})
    for x in done:
        print("exit %s  %s" % (x["exit"], x["line"]))
    return 0 if all(x["exit"] == 0 for x in done) else 1


def run_page_hook(page, run, key, problems):
    try:
        page.fill_stage(run, key)
    except Exception as ex:
        problems.append({"kind": "page-hook", "item": key, "detail": "page.build.fill_stage: %s" % ex})


def cmd_finish(a):
    run, key = os.path.realpath(a.run), a.stage
    st = open_run(run)
    e = stage_entry(key)
    s = st["stages"][key]
    if s["status"] != "running":
        raise Stop(2, "refused: %s is %s, not running" % (key, s["status"]))
    for hook in (LIMITS, PAGE):
        if not os.path.isfile(hook):
            raise Stop(2, "refused: hook module missing: %s; nothing recorded" % rel(hook, REPO))
    rev = s.get("attempts", 0)
    reports = [(p, read_fallback(run, e, s, p)) for p in (a.fallback_report or [])]
    seen = [d["check"] for _, d in reports]
    if len(set(seen)) != len(seen):
        raise Stop(2, "refused: two fallback reports name the same check; nothing recorded")
    # accepted fallback reports: kept in the run, never deleted, each a recorded check defect
    fb = dict(s.get("fallbacks") or {}) if s.get("fallbacks_rev") == rev else {}
    by_name = {c["name"]: c for c in e["checks"]}
    for p, doc in reports:
        c = by_name[doc["check"]]
        dst = os.path.join(run, "checks", c["name"] + ".fallback.json")
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        previous_fallback = preserve_report(run, dst)
        original = next((r for r in s.get("checks", []) if r.get("name") == c["name"]), None)
        if original and original.get("result") == "fallback":
            original = (fb.get(c["name"]) or {}).get("original")
        if os.path.realpath(p) != os.path.realpath(dst):
            shutil.copyfile(p, dst)
        fb[c["name"]] = {"path": rel(dst, run), "sha256": sha(dst), "why": doc["why"].strip(),
                         "applied": doc["applied"], "original": original,
                         "evidence": preserve_report(run, dst)}
        line = {"check": c["name"], "check_path": c["path"], "reason": doc["why"].strip(), "stage": key,
                "revision": rev, "report": rel(dst, run), "original": original,
                "evidence": fb[c["name"]]["evidence"], "previous_fallback": previous_fallback}
        if doc.get("readings"):   # the verdict normalisation, recorded beside the kept report
            fb[c["name"]]["readings"] = line["readings"] = doc["readings"]
        append_line(run, CHECK_DEFECTS, line)
    # every command line needs an exit recorded for this revision; finish runs any line that has none (in index
    # order) and records it, so a command that never ran cannot leave the stage done
    todo = unrecorded_lines(e, s, rev)
    if todo:
        done = run_commands(run, todo)
        record_exits(s, rev, done)
        write_state(run, st, {"event": "stage-command", "stage": key, "revision": rev, "round": command_round(s, rev),
                              "by": "finish", "commands": done})
        for x in done:
            print("ran        %s  (exit %s; no exit was recorded for it)" % (x["line"], x["exit"]))
    # the one automatic retry of a command stage reruns its commands before the checks run, so the checks judge the
    # outputs the retry wrote (the repair limit is unchanged)
    gaps = output_gaps(run, e, s) if e.get("command") else []
    if gaps and s.get("retry_rev") != rev:
        s["retry_rev"] = rev
        lines = list(e["command"])
        if e["kind"] == "script":
            retried = run_commands(run, lines)
            record_exits(s, rev, retried)
            write_state(run, st, {"event": "stage-retry", "stage": key, "revision": rev, "gaps": gaps,
                                  "commands": retried})
            for x in retried:
                print("retried    %s  (exit %s)" % (x["line"], x["exit"]))
        else:
            # dispatch order: the lines that run before the dispatch rerun now; the stage agent is dispatched once
            # more; the lines after it run through ad.py exec (finish runs any still unrecorded)
            n = int(e.get("before_dispatch") or 0)
            retried = run_commands(run, lines[:n])
            record_exits(s, rev, retried)
            write_state(run, st, {"event": "retry-requested", "stage": key, "revision": rev, "gaps": gaps,
                                  "commands": retried})
            for x in retried:
                print("retried    %s  (exit %s)" % (x["line"], x["exit"]))
            after = ["ad.py exec --run RUN --stage %s --line %d" % (key, i + 1) for i in range(n, len(lines))]
            print("retry: %s wrote %s. Carry out the %s stage prompt once more in this session with RUN/%s%s, then run finish --stage %s "
                  "again." % (key, "; ".join("%s (%s)" % (g["item"], g["detail"]) for g in gaps), key,
                              s.get("prompt") or "prompts/%s.md" % key,
                              ", then run " + ", then ".join(after) if after else "", key))
            print("%s: not done, one retry requested" % key)
            return 1
    res = []
    for c in e["checks"]:
        if c["name"] in fb:
            res.append({"name": c["name"], "path": c["path"], "fallback": c["fallback"], "result": "fallback",
                        "report": fb[c["name"]]["path"], "sha256": fb[c["name"]]["sha256"],
                        "original": fb[c["name"]].get("original")})
        else:
            res.append(run_check(run, c))
    # a check marked "blocking": false (the judgment screen) flags without changing the stage state
    soft = set(c["name"] for c in e["checks"] if c.get("blocking") is False)
    flagged = [c["name"] for c in res if c["name"] in soft and c["result"] == "fail"]
    hard = [c for c in res if c["name"] not in flagged]
    unchecked = [c for c in hard if c["result"] == "unchecked"]
    failing = [c for c in hard if c["result"] == "fail"]
    mins = None
    try:
        mins = round((datetime.fromisoformat(now()) - datetime.fromisoformat(s["started_at"])).total_seconds() / 60, 2)
    except (KeyError, ValueError, TypeError):
        pass
    s.update({"checks": res, "counts": {c["name"]: c.get("counts") for c in res}, "flagged": flagged,
              "fallbacks": fb, "fallbacks_rev": rev})
    for c in res:
        print("%-10s %s%s" % ("flagged" if c["name"] in flagged else c["result"], c["name"],
                              "" if c["result"] in ("pass", "fallback") else "  (agent fallback: %s)" % c["fallback"]))
    limits = load_hook(LIMITS, "limits")
    ceiling, problems = None, []
    try:
        limits.boundary(run, key)
    except Exception as ex:
        cr = getattr(limits, "CeilingReached", None)
        if isinstance(cr, type) and isinstance(ex, cr):
            ceiling = ex
        else:
            problems.append({"kind": "limits-boundary", "item": key, "detail": "limits.boundary: %s" % ex})
    # required outputs are judged before any check-fallback or repair handling, for every stage: a missing or
    # unreadable output gets its one retry, then the stage is failed (a check that cannot read it never loops)
    gaps = output_gaps(run, e, s)
    page = load_hook(PAGE, "page_build")
    hook_ran = False
    if gaps:
        # the page hook writes some stages' outputs (the page itself), so it runs before a gap is judged final
        hook_ran = True
        run_page_hook(page, run, key, problems)
        gaps = output_gaps(run, e, s)
    if gaps and s.get("retry_rev") != rev:
        s["retry_rev"] = rev
        # a stage without a command (command stages retried before the checks): the stage agent once more
        write_state(run, st, {"event": "retry-requested", "stage": key, "revision": rev, "gaps": gaps})
        print("retry: %s wrote %s. Carry out the %s stage prompt once more in this session with RUN/%s, then run finish --stage %s "
              "again." % (key, "; ".join("%s (%s)" % (g["item"], g["detail"]) for g in gaps), key,
                          s.get("prompt") or "prompts/%s.md" % key, key))
        print("%s: not done, one retry requested" % key)
        if ceiling is not None:
            raise Stop(2, "ceiling reached: %s" % key)
        return 1
    if unchecked and not gaps:
        # no completion state: the skill dispatches each check's fallback agent and calls finish --fallback-report
        write_state(run, st, {"event": "fallback-needed", "stage": key, "revision": rev,
                              "checks": {c["name"]: c["result"] for c in res}})
        for c in unchecked:
            print("fallback needed: %s (%s). Carry out the fallback (%s) in this session; it writes a JSON report with check, "
                  "revision %d, why and applied per-item verdicts; then run finish --stage %s --fallback-report FILE."
                  % (c["name"], c.get("detail", ""), c["fallback"], rev, key))
        print("%s: not done, a check could not run" % key)
        if ceiling is not None:
            raise Stop(2, "ceiling reached: %s" % key)
        return 3 if any(c.get("crashed") for c in unchecked) else 1
    if failing and not gaps and s.get("repair_rev") != rev:
        s["repair_rev"] = rev
        s["repair_requested_at"] = datetime.now(timezone.utc).timestamp()
        write_state(run, st, {"event": "repair-requested", "stage": key, "revision": rev,
                              "checks": {c["name"]: c["result"] for c in res}})
        print("repair: carry out one repair of %s in this session with the failing reports (%s), then run finish --stage %s "
              "again; no start --rerun. When the cause sits in an earlier stage, the repair agent writes %s "
              '{"owner_stage": "<earlier stage>", "reason": "<one line>"} instead of forcing a fix here; that stage '
              "then reruns, with the stages after it" % (key, ", ".join("checks/%s.json" % c["name"] for c in failing),
                                                          key, os.path.join(run, REPAIR_OWNERS, key + ".json")))
        print("%s: not done, one repair requested" % key)
        if ceiling is not None:
            raise Stop(2, "ceiling reached: %s" % key)
        return 1
    held = matching_done(run, st)
    held[key] = tree_hashes(os.path.join(run, e["outputs"]))
    # the page hook runs before the state is written, on the ceiling path too (the ceiling exit stays)
    if not hook_ran:
        run_page_hook(page, run, key, problems)
    for x in s.get("command_exits") or []:
        if x.get("revision") == rev and x.get("exit") != 0:
            problems.append({"kind": "command-exit", "item": x.get("line"), "detail": "%sexited %r" % (
                "retry " if x.get("round") == 1 else "", x.get("exit"))})
    if s.get("retry_rev") == rev:
        problems.append({"kind": "retry", "item": key, "detail": "one automatic retry ran; outputs %s"
                         % ("still missing or unreadable" if gaps else "present after it")})
    # the repair agent's owning-stage reading (a valid one returns the owner and its reason; anything else a note)
    owner, note = (read_repair_owner(run, key, s.get("repair_requested_at")) if failing and s.get("repair_rev") == rev
                   else (None, None))
    if note and not owner:
        problems.append({"kind": "repair-owner-reading", "item": key, "detail": note})
    if owner:
        s["repair_owner"] = {"stage": owner, "reason": note, "revision": rev}
    for c in failing:
        items = c.get("problems") if isinstance(c.get("problems"), list) else []
        if owner:   # routed by `next` to the owning stage (pending_routes reads repair_owner)
            c.update(repair_owner=owner, repair_reason=note)
        problems.append({"kind": "check-failed", "item": c["name"], "detail": "failed after the stage's one repair"
                         + ("; %d problem(s) in checks/%s.json" % (len(items), c["name"]) if items else "")
                         + ("; the repair agent names %s as the owning stage (%s), which reruns next"
                            % (owner, c["repair_reason"]) if owner else "")})
    for name in flagged:   # the judgment screen never stops the run, but its flag is a recorded problem
        c = next(x for x in res if x["name"] == name)
        items = c.get("problems") if isinstance(c.get("problems"), list) else []
        problems.append({"kind": "screen-flag", "item": name, "detail": "flagged, the run carries on"
                         + ("; %d problem(s) in checks/%s.json" % (len(items), name) if items else "")})
    for name, f in sorted(fb.items()):
        problems.append({"kind": "check-defect", "item": name, "detail": "judged by a fallback agent: %s" % f["why"]})
        for v in f["applied"]:
            if v["verdict"] != "pass":
                problems.append({"kind": "fallback-verdict", "item": "%s: %s" % (name, v["item"]),
                                 "detail": "%s: %s" % (v["verdict"], v["reason"])})
    written = stage_written_problems(run, key, rev)   # already in the builder file; counted, not written again
    if gaps:
        status = FAILED
        problems = [dict(g, kind="missing-output") for g in gaps] + problems
    else:
        status = DONE_PROBLEMS if problems or written else DONE
    s.update({"status": status, "ended_at": now(), "minutes": mins, "outputs": held[key],
              "problems": len(problems) + len(written)})
    if s.get("repair") and not s["repair"].get("done"):
        s["repair"]["done"] = rev
    write_state(run, st, {"event": "stage-end", "stage": key, "status": status, "minutes": mins, "revision": rev,
                          "checks": {c["name"]: c["result"] for c in res}, "flagged": flagged,
                          "fallback_reports": dict((n, {"path": f["path"], "sha256": f["sha256"]})
                                                   for n, f in sorted(fb.items())),
                          "problems": len(problems) + len(written)})
    for p in written:
        print("problem    %s %s: %s (recorded by the stage)" % (p.get("kind"), p.get("item"), p.get("detail")))
    for p in problems:
        append_line(run, PROBLEMS, dict(p, stage=key, revision=rev))
        print("problem    %s %s: %s" % (p["kind"], p["item"], p["detail"]))
    print("%s: %s in %s min" % (key, status + (", flagged: " + ", ".join(flagged) if flagged else ""), mins))
    show_customer(run, limits)
    if is_done(status) and key == REVIEW_B:
        accept_repairs(run, st)
    if ceiling is not None:
        raise Stop(2, "ceiling reached: %s" % key)
    if is_done(status):
        rerecord_hook_writes(run, st, key, held)
        return 0
    return 1


def stage_written_problems(run, key, rev):
    """Problem lines the stage's own commands wrote to the builder file for this stage and revision (finish writes its
    own lines only after it reads these, so none of them is counted twice). A line that does not parse is skipped."""
    out = []
    try:
        with open(os.path.join(run, PROBLEMS), encoding="utf-8") as f:
            for line in f:
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if (isinstance(rec, dict) and rec.get("stage") == key and type(rec.get("revision")) is int
                        and rec["revision"] == rev):
                    out.append(rec)
    except OSError:
        pass
    return out


def restore_tree(src, dst):
    """Make dst hold exactly the files of src (the copy taken before a repair)."""
    for dp, _, fs in os.walk(dst):
        for f in fs:
            p = os.path.join(dp, f)
            if not os.path.isfile(os.path.join(src, rel(p, dst))):
                os.remove(p)
    for dp, _, fs in os.walk(src):
        for f in fs:
            p = os.path.join(dp, f)
            q = os.path.join(dst, rel(p, src))
            os.makedirs(os.path.dirname(q), exist_ok=True)
            shutil.copyfile(p, q)


def accept_repairs(run, st):
    """Record Review B's settled repairs to finished owning stages (its one fix round, key criterion 13).
    A changed finished stage is accepted only when stages/review-b/fix-round.json is settled, its copy of the stage
    taken at plan (before/<stage>) matches the recorded outputs, and the stage's files now hash exactly as settle
    wrote them in `after`. Accepted stages get their new hashes and a repaired_by record; every later finished stage
    up to Review B that was not itself repaired is marked stale, as a rerun would. Review B stays done, so no second
    review or fix round runs. Every changed finished stage is checked first: if any one is not exactly the settled
    repair, nothing is accepted or marked stale; every changed stage is restored from its before/<stage> copy where
    that copy matches its record, each is a repair-refused problem line, and Review B ends done with recorded problems.
    Only a stage with no matching copy stays changed; the next `next` or start records and rechecks it (never exit 3)."""
    try:
        with open(os.path.join(run, FIX_ROUND), encoding="utf-8") as f:
            fr = json.load(f)
    except (OSError, ValueError):
        return
    if not isinstance(fr, dict) or fr.get("status") != "settled" or not isinstance(fr.get("after"), dict):
        return
    order = keys()
    by_key = {e["key"]: e for e in load_index()}
    changed = changed_done(run, st)
    outside = [k for k in changed
               if order.index(k) >= order.index(REVIEW_B) or k not in fr["after"]
               or tree_hashes(os.path.join(run, by_key[k]["outputs"])) != fr["after"][k]
               or not os.path.isdir(os.path.join(run, os.path.dirname(FIX_ROUND), "before", k))
               or tree_hashes(os.path.join(run, os.path.dirname(FIX_ROUND), "before", k)) != st["stages"][k].get("outputs", {})]
    if outside:
        # nothing is accepted; every changed stage goes back to its copy taken before the repair, and each is a
        # recorded problem (a stage with no matching copy stays changed, recorded, and the next start refuses it)
        restored, kept = [], []
        for k in changed:
            before = os.path.join(run, os.path.dirname(FIX_ROUND), "before", k)
            ok = os.path.isdir(before) and tree_hashes(before) == st["stages"][k].get("outputs", {})
            if ok:
                restore_tree(before, os.path.join(run, by_key[k]["outputs"]))
                restored.append(k)
            else:
                kept.append(k)
            append_line(run, PROBLEMS, {
                "kind": "repair-refused", "stage": REVIEW_B, "item": k,
                "revision": (st["stages"].get(REVIEW_B) or {}).get("attempts"),
                "detail": "%s changed outside the settled %s fix round; %s" % (
                    k if k in outside else "another stage", REVIEW_B,
                    "restored from the copy taken before the repair" if ok
                    else "no matching copy taken before the repair, so it stays changed")})
        rb = st["stages"].get(REVIEW_B) or {}
        if rb.get("status") == DONE:
            rb["status"] = DONE_PROBLEMS
        rb["problems"] = (rb.get("problems") or 0) + len(changed)
        write_state(run, st, {"event": "review-repair-refused", "review": REVIEW_B, "outside": outside,
                              "restored": restored, "kept_changed": kept})
        print("not accepting %s repairs: %s changed outside the settled fix round; restored: %s%s"
              % (REVIEW_B, ", ".join(outside), ", ".join(restored) or "none",
                 "; still changed (no copy to restore): " + ", ".join(kept) if kept else ""))
        return
    repaired = {}
    for k in changed:
        s = st["stages"][k]
        now_h = tree_hashes(os.path.join(run, by_key[k]["outputs"]))
        files = dict((p, h) for p, h in now_h.items() if s["outputs"].get(p) != h)
        findings = [f.get("id") for f in fr.get("findings") or []
                    if isinstance(f, dict) and f.get("stage") == k and f.get("id") in (fr.get("fixed") or [])]
        s["outputs"] = now_h
        s["repaired_by"] = {"review": REVIEW_B, "at": now(), "files": files, "findings": findings}
        repaired[k] = {"files": files, "findings": findings}
    if not repaired:
        return
    first = min(order.index(k) for k in repaired)
    stale = []
    for k in order[first + 1:order.index(REVIEW_B)]:
        if k not in repaired and is_done(st["stages"][k]["status"]):
            st["stages"][k]["status"] = "stale"
            stale.append(k)
    write_state(run, st, {"event": "review-repair", "review": REVIEW_B, "repaired": repaired, "stale": stale})
    print("accepted %s repairs: %s" % (REVIEW_B, ", ".join(sorted(repaired, key=order.index))))
    if stale:
        print("marked stale: %s" % ", ".join(stale))


def matching_done(run, st):
    """Finished stages whose outputs still match their record: {key: current hashes}."""
    out = {}
    for e in load_index():
        s = st["stages"].get(e["key"]) or {}
        if is_done(s.get("status")):
            h = tree_hashes(os.path.join(run, e["outputs"]))
            if h == s.get("outputs", {}):
                out[e["key"]] = h
    return out


def rerecord_hook_writes(run, st, key, held):
    """After a successful page hook, record the new hashes of the finished stages whose outputs the hook rewrote.
    Only stages that matched their record just before the hook are re-recorded, so a change made outside ad.py
    stays a change; the event names each re-recorded stage."""
    redone = []
    for e in load_index():
        k = e["key"]
        if k in held:
            h = tree_hashes(os.path.join(run, e["outputs"]))
            if h != held[k]:
                st["stages"][k]["outputs"] = h
                redone.append(k)
    if redone:
        write_state(run, st, {"event": "page-fill", "stage": key, "rerecorded": redone})


# ---------------------------------------------------------------- status, resume

def stage_table(st):
    lines = ["| Stage | Status | Minutes | Checks |", "|---|---|---|---|"]
    for k in keys():
        s = st["stages"].get(k) or {}
        ck = s.get("checks") or []
        status = s.get("status", "pending")
        if is_done(status) and s.get("flagged"):
            status += ", flagged: " + ", ".join(s["flagged"])   # done, but not clean
        lines.append("| %s | %s | %s | %s |" % (k, status, "" if s.get("minutes") is None else s["minutes"],
                     "%d/%d pass" % (sum(c["result"] == "pass" for c in ck), len(ck)) if ck else ""))
    return "\n".join(lines)


CUSTOMER_START = "---- status for the person ----"
CUSTOMER_END = "---- end of status ----"


UNCHANGED = "No change since the last update; nothing to post."
STATUS_FALLBACK = "The progress table could not be drawn just now; the run continues."


def show_customer(run, limits, always=False):
    """Print the person's status between two marker lines, only when their view changed since the last post (always at
    init); the session posts only what sits between them. A status that cannot be built is one plain line here and a
    builder-log entry, never a stop."""
    view = getattr(limits, "customer_view", None)
    post = getattr(limits, "customer_post", None)
    if view is None:
        return
    try:
        if post is not None:
            try:
                text = post(run)
            except Exception as ex:   # the changed-only record failed: post the view
                append_line(run, PROBLEMS, {"kind": "customer-status", "item": "status", "detail": "customer_post: %s" % ex})
                text = view(run)
            if text is None and always:
                text = view(run)
            if text is None:
                print(UNCHANGED)
                return
        else:
            text = view(run)
    except Exception as ex:
        append_line(run, PROBLEMS, {"kind": "customer-status", "item": "status", "detail": "customer_view: %s" % ex})
        text = STATUS_FALLBACK
    print(CUSTOMER_START)
    print(text)
    print(CUSTOMER_END)


def state_shape(st):
    """What is wrong with the shape of a parsed state.json, or None."""
    if not isinstance(st, dict) or not isinstance(st.get("stages"), dict):
        return "state.json is not an object with a stages object"
    bad = sorted(k for k, v in st["stages"].items() if not isinstance(v, dict))
    if bad:
        return "state.json stage entries are not objects: %s" % ", ".join(bad)
    return None


def status_page(run, st):
    """Keep the person's page true to the run when status is asked for: a running stage shows as running; a run that
    stopped early (it hit its ceiling, or a stage could not finish and nothing is running) gets the ended view. Never
    raises; a page problem is a recorded problem."""
    try:
        stages = st.get("stages") or {}
        running = [k for k in keys() if (stages.get(k) or {}).get("status") == "running"]
        failed = any(str((v or {}).get("status", "")).startswith("failed") for v in stages.values())
        page = load_hook(PAGE, "page_build")
        stopped = os.path.isfile(os.path.join(run, "REMAINING.md"))   # the ceiling stop wins over a stage left running
        if (stopped or (failed and not running)) and hasattr(page, "publish_ended"):
            page.publish_ended(run, "The run stopped before it finished; the parts it reached are shown.")
        elif running and hasattr(page, "start_stage"):
            page.start_stage(run, running[0])
    except Exception as ex:   # noqa: BLE001 - the page never stops a status call
        append_line(run, PROBLEMS, {"kind": "page-hook", "item": "status", "detail": "page on status: %s" % ex})


def cmd_status(a):
    run = os.path.realpath(a.run)
    try:
        st = open_run(run)
    except ValueError as ex:
        raise Stop(2, "refused: state.json does not parse (%s)" % ex.__class__.__name__)
    limits = load_hook(LIMITS, "limits")
    with open(os.path.join(run, "events.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps({"event": "watch", "at": now(), "state_sha256": sha(os.path.join(run, "state.json"))}) + "\n")
    bad = state_shape(st)
    if bad:   # parseable but unexpected: a recorded problem and a plain message, never a stop
        append_line(run, PROBLEMS, {"kind": "state-shape", "item": "state.json", "detail": bad})
        print(STATUS_FALLBACK if not getattr(a, "builder", False)
              else "The status could not be shown just now: %s. It is recorded in %s." % (bad, PROBLEMS))
        return 0
    if not getattr(a, "builder", False):
        status_page(run, st)
        if getattr(a, "if_changed", False):
            show_customer(run, limits)   # between steps of a long stage: post only when the person's view changed
            return 0
        try:
            print(limits.customer_view(run))
        except Exception as ex:
            append_line(run, PROBLEMS, {"kind": "customer-status", "item": "status", "detail": "customer_view: %s" % ex})
            print(STATUS_FALLBACK)
        return 0
    try:
        table = limits.table(run)
    except Exception as ex:
        append_line(run, PROBLEMS, {"kind": "builder-status", "item": "status", "detail": "limits.table: %s" % ex})
        table = "The limits table could not be built just now; it is recorded in %s." % PROBLEMS
    try:
        body = [stage_table(st)]
        if not (st.get("baseline") or {}).get("compared"):
            body.append(NO_BASELINE)
        msg = change_message(run, st)
        if msg:
            body.append(msg)
    except Exception as ex:   # parseable but unexpected nested state: recorded, a plain message, never a stop
        append_line(run, PROBLEMS, {"kind": "state-shape", "item": "state.json",
                                    "detail": "builder status: %s: %s" % (ex.__class__.__name__, ex)})
        body = ["The stage table could not be shown just now: state.json holds an unexpected value. It is recorded "
                "in %s." % PROBLEMS]
    print(table)
    print()
    print("\n".join(body))
    return 0


def cmd_resume(a):
    run = os.path.realpath(a.run)
    st = open_run(run)
    revalidate_changed(run, st, "resume")   # changed bytes: recorded and revalidated, never a stop
    print(stage_table(st))
    if not (st.get("baseline") or {}).get("compared"):
        print(NO_BASELINE)
    print("next stage: %s" % next_key(st))
    for k in keys():
        s = st["stages"][k]
        if s["status"] == "running" and s.get("prompt"):
            print("running: %s, prompt %s" % (k, os.path.join(run, s["prompt"])))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd")
    sub.required = True
    for name in ("init", "next", "repair", "start", "exec", "finish", "status", "resume"):
        p = sub.add_parser(name)
        p.add_argument("--run", required=True)
        if name in ("start", "exec", "finish"):
            p.add_argument("--stage", required=True)
    sub.choices["init"].add_argument("--from", required=True)
    sub.choices["init"].add_argument("--baseline")
    sub.choices["start"].add_argument("--rerun", action="store_true")
    sub.choices["status"].add_argument("--if-changed", action="store_true",
                                       help="post the person's block only when their view changed (long stages)")
    sub.choices["status"].add_argument("--builder", action="store_true",
                                       help="the builder view: check names, attempts, counters and recovery steps")
    sub.choices["exec"].add_argument("--line", type=int)
    sub.choices["finish"].add_argument("--fallback-report", action="append", dest="fallback_report")
    a = ap.parse_args(argv)
    try:
        return {"init": cmd_init, "next": cmd_next, "repair": cmd_repair, "start": cmd_start, "exec": cmd_exec, "finish": cmd_finish,
                "status": cmd_status, "resume": cmd_resume}[a.cmd](a) or 0
    except Stop as e:
        print(str(e), file=sys.stderr if e.code != 2 or not str(e).startswith("ceiling reached") else sys.stdout)
        return e.code


if __name__ == "__main__":
    sys.exit(main())
