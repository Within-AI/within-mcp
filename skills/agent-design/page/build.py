"""Build the Agent Design page as the run goes: one call per finished stage (pins C7).

    fill_stage(run_dir, stage) -> dict or None   fill the stage's slots, then publish the person's page
    start_stage(run_dir, stage) -> dict or None  publish the person's page with the stage marked running
    publish_ended(run_dir, reason=None) -> dict or None   publish the page of a run that stopped early

The page lives in <run_dir>/page/ and is built through the shared library knowledge/page/progressive/progressive.py
(scaffold, fill, final_gate). The sections and slots come from template/slots.json, the stage-to-slot map from
template/stage-slots.json and every reader-visible sentence from template/strings.json; every count is computed here
by summary(), the one place counts are computed. Four slots come from renderers loaded by file path (RENDERERS); an
absent renderer or one that raises leaves its slot reading the gap sentence, and the error goes to
page/warnings.jsonl. At stage "page" the library's final gate and the page checks (PAGE_CHECKS) run and the page
always publishes to <run_dir>/agent-design.html: their problems and any text defects are notes in page/gate.json,
grouped by the stage that owns the words (also written to stages/page/page-route.json for one routed repair), and
pipeline state goes to the builder log (page/builder-log.jsonl), never the page.

A stage's own output file never reaches the person's page: a slot is its rendered section from the boundary where its
renderer can run, and until then reads "Not reached yet". Every call publishes the person's page at
<run_dir>/outputs/agent-design.html through the shared live page (knowledge/page/progressive/live.py): live (reload,
progress strip, running marker) until the page stage, which publishes it final; a later call never turns it back. The
publication record is builder/live-page.json. Publishing never raises; the result dict (with the person's page line)
is returned for the driving script to print. Standard library only, Python 3.9+.
"""
import html
import hashlib
from urllib.parse import quote
import importlib.util
import inspect
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
AD = os.path.dirname(HERE)
REPO = os.path.dirname(os.path.dirname(AD))
TEMPLATE = os.path.join(HERE, "template")
def _knowledge(*parts):
    """A path under the plugin's knowledge folder, found through the one lookup
    (skills/fast-mode-build/driver/knowledge_root.py); "" when no knowledge folder is found."""
    spec = importlib.util.spec_from_file_location(
        "knowledge_root", os.path.join(REPO, "skills", "fast-mode-build", "driver", "knowledge_root.py"))
    lookup = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(lookup)
    try:
        return os.path.join(lookup.find(), *parts)
    except lookup.KnowledgeNotFound:
        return ""


PROGRESSIVE = _knowledge("page", "progressive", "progressive.py")
LIVE = _knowledge("page", "progressive", "live.py")
LIMITS = os.path.join(AD, "scripts", "limits.py")
INDEX = os.path.join(AD, "stages", "index.json")
PUBLISHED = "outputs/agent-design.html"     # the person's page, live from init, final at the page stage
RECORD_DIR = "builder"                      # live-page.json: the publication record
INTERFACE_DETAIL = "builder/interface-detail.md"   # the interface fields with their tokens and boundary ids
LAST_PUBLISHING_STAGE = "page"              # later stages (post-run, handoff) leave the final page as it is

# Slot -> renderer file, each exposing render(run_dir) -> str. Tests replace entries to load stubs.
RENDERERS = {
    "rules-page": os.path.join(HERE, "rules", "render.py"),
    "simulation": os.path.join(HERE, "simulation", "embed.py"),
    "agent-diagram": os.path.join(HERE, "diagram", "diagram.py"),
    "set-panel": os.path.join(HERE, "set-panel", "panel.py"),
}
PAGE_CHECKS = os.path.join(HERE, "diagram", "page_checks.py")
PAGE_CHECKS_NOT_RUN = "page checks not run"
MACHINE_PATH = "<machine path>"
# An absolute path: a slash that starts a token (not inside a word, a relative path or a URL) followed by path text.
_ABS_PATH = re.compile(r"(?<![\w.:/])/[^\s'\"<>()\[\]{},;]+")

INTAKE = "stages/intake/intake.json"
GATE_REPORT = "stages/coherence-gate/report.json"
FIX_ROUND = "stages/review-b/fix-round.json"
PAGE_PREP = "stages/page-prep/page-prep.json"
# U23's page-prep words per slot (stages/page-prep/output.schema.json). U23 writes the lead and the reading
# guide for the orientation; it writes no words meant for the other slots, which then carry no words block.
PREP_WORDS = {"orientation": ("interpretive_paragraph", "guide")}
FIELDS = "stages/fields/fields.json"
RULES = "stages/rules/rules.json"
SUITE = "stages/test-suite/test-suite.json"
EXPECTED = "stages/expected-values/expected.json"
SCREEN_REPORT = "checks/screen_judgments.json"


class Unreadable(Exception):
    """A run file that exists but cannot be read or parsed."""


def _load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _template(name):
    return _load(os.path.join(TEMPLATE, name))


def _s(key, **values):
    text = _template("strings.json")[key]
    return text.format(**values) if values else text


def _word(table, value):
    """An enum value in plain words (strings.json table), so no enum shows as a raw id outside <code>."""
    return _template("strings.json")[table].get(value, value)


def _e(text):
    return html.escape(str(text))


def _p(text, cls=None):
    return '<p%s>%s</p>' % (' class="%s"' % cls if cls else "", _e(text))


def _ul(items):
    return "<ul>\n%s\n</ul>" % "\n".join("<li>%s</li>" % _e(i) for i in items)


def _scrub(text, run_dir):
    """Make diagnostic text free of machine paths: the run folder (as given and resolved) becomes RUN, a path inside
    the repository becomes its repo-relative path, and any other absolute path becomes MACHINE_PATH."""
    text = str(text)
    for p in sorted({run_dir, os.path.abspath(run_dir), os.path.realpath(run_dir)}, key=len, reverse=True):
        if p:
            text = text.replace(p.rstrip(os.sep), "RUN")
    for p in sorted({REPO, os.path.realpath(REPO)}, key=len, reverse=True):
        text = text.replace(p.rstrip(os.sep) + os.sep, "")
    return _ABS_PATH.sub(MACHINE_PATH, text)


def _read_run(run_dir, rel):
    """(None, False) when absent; (data, True) when parsed; raises Unreadable when present but unreadable."""
    path = os.path.join(run_dir, rel)
    if not os.path.exists(path):
        return None, False
    try:
        return _load(path), True
    except (OSError, ValueError, UnicodeDecodeError):
        raise Unreadable(rel)


def _items(data, key):
    """A list stored bare or under key."""
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and isinstance(data.get(key), list):
        return data[key]
    raise Unreadable(key)


def _intake(run_dir):
    data, _ = _read_run(run_dir, INTAKE)
    return data if isinstance(data, dict) else None


def _warn(run_dir, slot, source, exc):
    page_dir = os.path.join(run_dir, "page")
    os.makedirs(page_dir, exist_ok=True)
    entry = {"slot": slot, "source": source, "error": _scrub("%s: %s" % (type(exc).__name__, exc), run_dir)}
    with open(os.path.join(page_dir, "warnings.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, sort_keys=True) + "\n")


def _gap(live=False):
    """A part that could not be drawn: at the end it was not produced; mid-run it is drawn again at page-prep."""
    return '<p class="pp-gap">%s</p>' % _e(_s("gap_live" if live else "gap"))


BUILDER_LOG = "page/builder-log.jsonl"
REGISTER = "stages/register/register.json"
ROUTE = "stages/page/page-route.json"
QUIET_NOTE = "Some wording is still being tidied."
_HEX_ID = re.compile(r"\b[0-9a-f]{12}\b")
# Pipeline state never shows on the page: a sentence naming one of these is removed from the visible text.
PIPELINE_WORDS = re.compile(r"coherence gate|review b\b|fix round|\bcheck_\w*|exit (?:code|status)|attempt count|"
                            r"\battempts? \d+|\bre-?run (?:the )?(?:stage|run|page|build|check)s?\b|"
                            r"\brecovery (?:step|instruction)s?\b|\bretry the\b", re.I)


def _log(run_dir, where, message):
    """One line in the builder log (page/builder-log.jsonl): pipeline state and repairs the reader never sees."""
    page_dir = os.path.join(run_dir, "page")
    os.makedirs(page_dir, exist_ok=True)
    entry = {"where": where, "message": _scrub(message, run_dir)}
    with open(os.path.join(run_dir, BUILDER_LOG), "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, sort_keys=True) + "\n")


def _plural(n, one, many):
    return one if n == 1 else many


def _plain_title(value):
    """A readable title: retain its words, expand separators, and reject a bare process id."""
    if not isinstance(value, str) or not value.strip():
        return None
    if _HEX_ID.fullmatch(value.strip()):
        return None
    return " ".join(value.replace("_", " ").split())


def _set_titles(run_dir, intake):
    rel = intake.get("set_map") if isinstance(intake, dict) else None
    if not isinstance(rel, str) or not rel:
        return {}
    try:
        data, _ = _read_run(run_dir, rel)
    except Unreadable:
        return {}
    listed = data.get("processes") if isinstance(data, dict) else None
    return {p.get("process_id"): p.get("title") for p in listed or [] if isinstance(p, dict)}


def _process_name(run_dir, proc, index=1, multi=False):
    """The process as the reader knows it: its set-map title, else the name its own current or future drawing
    records, else "the process" ("process N" in a set). Never the bare process id."""
    try:
        intake = _intake(run_dir)
    except Unreadable:
        intake = None
    pid = proc.get("process_id") if isinstance(proc, dict) else None
    candidates = [proc.get("title") if isinstance(proc, dict) else None, _set_titles(run_dir, intake).get(pid)]
    for key in ("current_state", "future_state"):
        candidates.append(_drawing_name(run_dir, proc, key, own_only=multi))
    for c in candidates:
        t = _plain_title(c)
        if t:
            return t
    return "process %d" % index if multi else "the process"


def _drawing_name(run_dir, proc, key, own_only=False):
    rel = proc.get(key) if isinstance(proc, dict) else None
    if not isinstance(rel, str) or not rel.endswith(".json"):
        return None
    try:
        data, _ = _read_run(run_dir, rel)
    except Unreadable:
        return None
    named = data.get("process") if isinstance(data, dict) else None
    if not isinstance(named, dict):
        return None
    ids = named.get("process_ids")
    if own_only and isinstance(ids, list) and len(ids) > 1:
        return None  # a shared set file's name names the whole set
    return named.get("name")


def _by_id(items, where, conflicts):
    """Items de-duplicated by id; an id listed twice with different content is a recorded conflict."""
    out, seen = [], {}
    for i, item in enumerate(items if isinstance(items, list) else []):
        key = item.get("id") if isinstance(item, dict) else None
        if key is None:
            key = "#%d" % i
        if key in seen:
            if seen[key] != item:
                conflicts.append("%s: id %s listed twice with different content" % (where, key))
            continue
        seen[key] = item
        out.append(item)
    return out


def _list_of(run_dir, rel, key, conflicts):
    try:
        data, present = _read_run(run_dir, rel)
    except Unreadable:
        conflicts.append("%s could not be read" % rel)
        return None
    if not present:
        return None
    try:
        return _items(data, key)
    except Unreadable:
        conflicts.append("%s has no %s list" % (rel, key))
        return None


def summary(run_dir, rows=None, match=None):
    """Every count on the page, computed once from the stage outputs and de-duplicated by id: processes, rules,
    rules by evidence state, observations, test cases, expected values and open questions. Conflicts between the
    files are recorded in the builder log, never reconciled silently. Given the rules page's placed rows and its
    filter test (rules/render.py), it also gives the rules page's breakdowns ("count", "sessions",
    "observed_sessions") over those rows de-duplicated by rule, process and step, so no fragment counts on its own.
    "rules_shown" is what the page shows by evidence state: in a run with no observations, rules marked Observed are
    shown as recorded ("recorded") with an Observed count of 0, and the conflict is logged."""
    conflicts = []
    try:
        intake = _intake(run_dir) or {}
    except Unreadable:
        intake = {}
        conflicts.append("%s could not be read" % INTAKE)
    procs = intake.get("processes") if isinstance(intake.get("processes"), list) else []
    placed = rows
    rows, seen, evidence = [], set(), {}
    multi = len(procs) > 1
    for n, proc in enumerate(procs, 1):
        pid = proc.get("process_id") if isinstance(proc, dict) else None
        if pid in seen and pid is not None:
            conflicts.append("intake lists a process twice")
            continue
        seen.add(pid)
        ev = proc.get("evidence_strength") if isinstance(proc, dict) else None
        ev = ev if isinstance(ev, dict) else {}
        obs = ev.get("observation_count") if isinstance(ev.get("observation_count"), int) else 0
        src = ev.get("source_count") if isinstance(ev.get("source_count"), int) else 0
        rows.append({"name": _process_name(run_dir, proc, len(rows) + 1, multi), "observations": obs, "sources": src,
                     "process_id": pid})
        evidence[pid] = {"observations": _count_or_none(ev.get("observation_count")),
                         "sources": _count_or_none(ev.get("source_count")),
                         "states": {k: _count_or_none(v) for k, v in ev["evidence_states"].items()}
                         if isinstance(ev.get("evidence_states"), dict) else None}
    rules = _by_id(_list_of(run_dir, RULES, "rules", conflicts) or [], "rules", conflicts)
    source_ids = [r.get("id") for r in rules if isinstance(r, dict)]
    rule_reader = _module("ad_rules_inventory", os.path.join(HERE, "rules", "render.py"))
    try:
        placement_doc, _ = _read_run(run_dir, "stages/placement/placed-rules.json")
        locations = rule_reader.placed_rule_list(placement_doc) or []
    except (Unreadable, ValueError, TypeError):
        locations = []
        conflicts.append("rule placements could not be read")
    inventory = {r.get("id"): dict(r) for r in rules if isinstance(r, dict)}
    for location in locations:
        rid = location.get("rule_id")
        if rid not in inventory:
            inventory[rid] = {"id": rid}
            conflicts.append("a represented rule has no source rule")
        item = inventory[rid]
        item["evidence_state"] = rule_reader.accepted_state(item.get("evidence_state"), location.get("evidence_state"))
    rules = list(inventory.values())
    states = {k: 0 for k in ("Observed", "Assumed", "Contradictory", "needs_more_information")}
    for r in rules:
        st = r.get("evidence_state") if isinstance(r, dict) else None
        if isinstance(st, str) and st in states:
            states[st] += 1
        else:
            conflicts.append("a rule records no known evidence state")
    observations = sum(r["observations"] for r in rows)
    shown = dict(states, recorded=0)
    if observations == 0 and states["Observed"]:
        conflicts.append(NO_OBS)
        shown["recorded"], shown["Observed"] = states["Observed"], 0
    cases = _by_id(_list_of(run_dir, SUITE, "cases", conflicts) or [], "test cases", conflicts)
    expected = _list_of(run_dir, EXPECTED, "cases", conflicts)
    questions = open_questions(run_dir, conflicts)
    walk_cases, walk_states = _walk_counts(run_dir, conflicts)
    coverage, coverage_error = _coverage_counts(run_dir) if multi else (None, None)
    out = {"source_rule_ids": source_ids, "represented_rule_ids": list(inventory),
           "source_rule_gaps": [rid for rid in inventory if rid not in source_ids],
           "processes": len(rows), "process_rows": rows, "rules": len(rules), "rules_by_state": states,
           "observations": observations, "cases": len(cases),
           "expected": None if expected is None else len(_by_id(expected, "expected values", conflicts)),
           "open_questions": None if questions is None else len(questions), "conflicts": conflicts,
           "rules_shown": shown, "process_observations": {r["process_id"]: r["observations"] for r in rows},
           "walk_cases": walk_cases, "walk_states": walk_states, "coverage": coverage,
           "coverage_error": coverage_error, "object_states": _object_state_counts(run_dir, intake),
           "process_evidence": evidence, "coverage_counts": _coverage_totals(coverage),
           "walk_rules": _walk_rules(run_dir)}
    if placed is not None:
        uniq, keys = [], set()
        for r in placed:
            key = (r.get("id"), r.get("pid"), r.get("ref"))
            if key in keys:
                conflicts.append("a rule is placed twice at the same step")
                continue
            keys.add(key)
            if observations == 0 and r.get("ev") == "Observed":
                # The breakdowns count what the page shows: with no observations, an Observed rule counts as
                # recorded, so no per-agent or per-process cell contradicts the Observed total of 0. This holds even
                # when the placed row disagrees with the rule list (a duplicate rule id, say).
                if not shown["recorded"] and NO_OBS not in conflicts:
                    conflicts.append(NO_OBS)
                r = dict(r, ev="recorded")
            uniq.append(r)
        test = match or (lambda r, **f: True)
        out["count"] = lambda **f: len({r.get("id") for r in uniq if test(r, **f)})
        out["distinct"] = lambda key: len({r.get(key) for r in uniq})
        out["sessions"] = len({o for r in uniq for o in r.get("obs") or []})
        out["observed_sessions"] = len({o for r in uniq if r.get("ev") == "Observed" for o in r.get("obs") or []})
        out["owners"] = len({r.get("owner") for r in uniq if r.get("owner") is not None})
    return out


NO_OBS = "rules marked Observed in a run with no observations; shown as recorded"


def _count_or_none(value):
    """A recorded count, or None (shown as "not recorded") when the file records no whole number."""
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _coverage_totals(coverage):
    """The set coverage's own totals: processes listed, processes with both rules and cases, unknown process tags."""
    if not isinstance(coverage, dict):
        return None
    total = len(coverage.get("processes") or [])
    return {"processes": total, "covered": total - len((coverage.get("uncovered") or {}).get("processes") or []),
            "unknown_tags": len(coverage.get("unknown_process_ids") or [])}


def _walk_rules(run_dir):
    """The walk's own {"reached", "total"} decision-rule counts, or None when the walk records no whole numbers."""
    try:
        data, present = _read_run(run_dir, WALK)
    except Unreadable:
        return None
    s = data.get("summary") if present and isinstance(data, dict) else None
    if not isinstance(s, dict):
        return None
    reached, total = _count_or_none(s.get("rules_reached")), _count_or_none(s.get("rules_total"))
    return None if reached is None or total is None else {"reached": reached, "total": total}


WALK = "simulation/walk.json"
WALK_DROPPED = ("execution_error", "not_executed", "needs_more_information")
COVERAGE = os.path.join(AD, "stages", "_set", "coverage.py")


def _walk_counts(run_dir, conflicts):
    """The simulation's counts: (cases walked, {"matches", "differs", "dropped"}), de-duplicated by case id, or
    (None, None) when there is no readable walk."""
    try:
        data, present = _read_run(run_dir, WALK)
    except Unreadable:
        conflicts.append("%s could not be read" % WALK)
        return None, None
    if not present:
        return None, None
    try:
        cases = _by_id([dict(c, id=c.get("case_id") if isinstance(c.get("case_id"), (str, int)) else None)
                        if isinstance(c, dict) else c for c in _items(data, "cases")],
                       "walked cases", conflicts)
    except Unreadable:
        conflicts.append("%s has no cases list" % WALK)
        return None, None
    states = {"matches": 0, "differs": 0, "dropped": 0}
    for c in cases:
        st = c.get("state") if isinstance(c, dict) else None
        if st in ("matches", "differs"):
            states[st] += 1
        elif st in WALK_DROPPED:
            states["dropped"] += 1
        else:
            conflicts.append("a walked case records no known state")
    return len(cases), states


def _coverage_counts(run_dir):
    """The set's coverage per process (stages/_set/coverage.py report()), or (None, the reason) when a source
    cannot be read."""
    try:
        return _module("ad_set_coverage_for_summary", COVERAGE).report(run_dir), None
    except ValueError as exc:
        return None, str(exc)


def _object_state_counts(run_dir, intake):
    """{shared work object id: how many states it moves through}, from the set map the intake names."""
    rel = intake.get("set_map") if isinstance(intake, dict) else None
    if not isinstance(rel, str):
        return {}
    try:
        data, present = _read_run(run_dir, rel)
    except Unreadable:
        return {}
    out = {}
    for st in (data.get("object_states") if present and isinstance(data, dict) else None) or []:
        if isinstance(st, dict):
            try:
                out[st.get("object")] = out.get(st.get("object"), 0) + 1
            except TypeError:  # an unhashable object id names no object
                continue
    return out


# ---- what we still need to know -------------------------------------------------------------------------------

_Q_TEXT = ("unknown", "question", "what")
_Q_WHO = ("who_can_answer", "adjudicator", "who")


def _first(item, keys):
    for k in keys:
        v = item.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None


_closed_by_snapshot = _module("ad_register_state", os.path.join(AD, "scripts", "register_state.py")).closed_by_snapshot


def open_questions(run_dir, conflicts=None):
    """The register's business questions, de-duplicated by id: [{"id", "text", "who", "decision"}], or None when
    there is no readable register. A question in the business owner's view is shown; with no such view, every item
    that asks something is shown."""
    conflicts = [] if conflicts is None else conflicts
    try:
        data, present = _read_run(run_dir, REGISTER)
    except Unreadable:
        conflicts.append("%s could not be read" % REGISTER)
        return None
    if not present:
        return None
    # A readable register of the wrong shape is a structural problem, never an empty register.
    if not isinstance(data, dict):
        conflicts.append("%s is not an object" % REGISTER)
        return None
    if not isinstance(data.get("items", []), list) or not isinstance(data.get("identity_runtime_opens", []), list) \
            or ("items" not in data and "identity_runtime_opens" not in data):
        conflicts.append("%s has no items list" % REGISTER)
        return None
    items = data.get("items") or []
    if any(not isinstance(i, dict) for i in items):
        conflicts.append("%s lists an item that is not an object" % REGISTER)
        return None
    items = items + [i for i in data.get("identity_runtime_opens") or [] if isinstance(i, dict)]
    views = data.get("views") if isinstance(data.get("views"), dict) else {}
    wanted = views.get("business_owner")
    wanted = set(wanted) if isinstance(wanted, list) and wanted else None
    out = []
    for item in _by_id(items, "register", conflicts):
        if not isinstance(item, dict):
            continue
        if wanted is not None and item.get("id") not in wanted:
            continue
        if _closed_by_snapshot(item):
            continue   # the snapshot settled it and nothing is left to confirm: not an open question
        text = _first(item, _Q_TEXT)
        if not text:
            continue
        group = item.get("group")
        decision = None
        if isinstance(group, dict):
            decision = _first(group, ("decision", "title", "name"))
        elif isinstance(item.get("decision"), str):
            decision = item["decision"]
        out.append({"id": item.get("id"), "text": text, "who": _first(item, _Q_WHO),
                    "decision": _plain_title(decision)})
    return out


def questions_html(run_dir):
    problems = []
    qs = open_questions(run_dir, problems)
    if qs is None:
        _log(run_dir, "questions", "register output absent, unreadable or malformed%s"
             % ("".join("; " + p for p in problems)))
        return _p(_s("questions_none_recorded"))
    if not qs:
        return _p(_s("questions_none_open"))
    groups, order = {}, []
    for q in qs:
        key = q["decision"] or _s("questions_other")
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(q)
    if _s("questions_other") in order:
        order.remove(_s("questions_other"))
        order.append(_s("questions_other"))
    out = [_p(_s("questions_intro"))]
    for key in order:
        out.append('<h4 data-ad-source="true">%s</h4>' % _e(key))
        lines = []
        for q in groups[key]:
            who = ' <span class="ad-who">%s</span>' % _e(_s("questions_who", who=q["who"])) if q["who"] else ""
            lines.append('<li data-ad-source="true">%s%s</li>' % (_e(q["text"]), who))
        out.append("<ul>\n%s\n</ul>" % "\n".join(lines))
    return "\n".join(out)


# ---- orientation ----------------------------------------------------------------------------------------------

def _orientation_processes(run_dir):
    out = ["<h4>%s</h4>" % _e(_s("h_processes"))]
    summ = summary(run_dir)
    if summ["processes"] == 0:
        return out + [_p(_s("intake_missing"))]
    out.append(_p(_s("process_count", count=summ["processes"], noun=_plural(summ["processes"], "process", "processes"))))
    lines = []
    for proc in summ["process_rows"]:
        lines.append(_s("process_line", process=proc["name"], observations=proc["observations"],
                        obs_noun=_plural(proc["observations"], "observation", "observations"),
                        sources=proc["sources"], src_noun=_plural(proc["sources"], "source", "sources")))
    out.append(_ul(lines))
    shown = summ["rules_shown"]
    out.append(_p(_s("rule_count", count=summ["rules"], noun=_plural(summ["rules"], "decision rule", "decision rules"),
                     observed=shown["Observed"], assumed=shown["Assumed"], contradictory=shown["Contradictory"],
                     nmi=shown["needs_more_information"])))
    if summ["source_rule_gaps"]:
        out.append(_p(_s("rules_source_gaps", count=len(summ["source_rule_gaps"]),
                         noun=_plural(len(summ["source_rule_gaps"]), "entry", "entries"))))
    if shown["recorded"]:
        out.append(_p(_s("rules_recorded", count=shown["recorded"],
                         noun=_plural(shown["recorded"], "rule is", "rules are"))))
    return out


def _orientation_upstream(run_dir):
    out = ["<h4>%s</h4>" % _e(_s("h_upstream"))]
    try:
        intake = _intake(run_dir)
    except Unreadable:
        intake = None
    if intake is None:
        return out + [_p(_s("could_not_read", path=INTAKE))]
    if "upstream_failing_checks" not in intake:
        return out + [_p(_s("upstream_not_recorded"))]
    checks = intake["upstream_failing_checks"]
    if not isinstance(checks, list):
        return out + [_p(_s("could_not_read", path=INTAKE))]
    if not checks:
        return out + [_p(_s("upstream_none"))]
    lines = []
    for c in checks:
        if isinstance(c, dict):
            lines.append(_s("upstream_line", check=c.get("check", "?"), stage=c.get("stage", "?")))
        else:
            lines.append(str(c))
    return out + [_p(_s("upstream_count", count=len(checks),
                                     noun=_plural(len(checks), "upstream check", "upstream checks"))), _ul(lines)]


def _gate_item(item):
    if isinstance(item, dict):
        for key in ("name", "check", "defect", "id", "message"):
            if item.get(key):
                return str(item[key])
        return json.dumps(item, sort_keys=True)
    return str(item)


def _orientation_gate(run_dir):
    out = ["<h4>%s</h4>" % _e(_s("h_gate"))]
    try:
        report, present = _read_run(run_dir, GATE_REPORT)
    except Unreadable:
        return out + [_p(_s("could_not_read", path=GATE_REPORT))]
    if not present:
        return out + [_p(_s("gate_not_run"))]
    if not isinstance(report, dict):
        return out + [_p(_s("could_not_read", path=GATE_REPORT))]
    result = str(report.get("result", "")).lower()
    failed = report.get("problems") or report.get("items") or report.get("failures") or []
    if not isinstance(failed, list):
        return out + [_p(_s("could_not_read", path=GATE_REPORT))]
    if result in ("pass", "passed") or report.get("ok") is True or report.get("passed") is True:
        if not failed:
            return out + [_p(_s("gate_passed"))]
    if result in ("fail", "failed", "error") or report.get("ok") is False or report.get("passed") is False or failed:
        return out + [_p(_s("gate_failed", count=len(failed), noun=_plural(len(failed), "item", "items"))), _ul([_gate_item(i) for i in failed])]
    return out + [_p(_s("gate_unknown"))]


def _orientation_fix(run_dir):
    out = ["<h4>%s</h4>" % _e(_s("h_fix"))]
    try:
        data, present = _read_run(run_dir, FIX_ROUND)
    except Unreadable:
        return out + [_p(_s("could_not_read", path=FIX_ROUND))]
    if not present:
        return out + [_p(_s("fix_not_run"))]
    if not isinstance(data, dict):
        return out + [_p(_s("could_not_read", path=FIX_ROUND))]
    status = data.get("status")
    if status == "planned":
        return out + [_p(_s("fix_planned"))]
    if status == "settled":
        left = data.get("open", [])
        if isinstance(left, list):
            n = len(left)
        elif isinstance(left, int) and not isinstance(left, bool):
            n = left
        else:
            return out + [_p(_s("could_not_read", path=FIX_ROUND))]
        return out + [_p(_s("fix_settled_clean") if n == 0 else _s("fix_settled_open", count=n, noun=_plural(n, "item", "items")))]
    return out + [_p(_s("fix_unknown"))]


def _words(run_dir, slot, live=False):
    """page-prep's plain words for one slot: a string or a list of paragraphs under the slot id. Mid-run (live),
    before page-prep has written them, the words are pending, never "not produced"."""
    try:
        data, present = _read_run(run_dir, PAGE_PREP)
    except Unreadable:
        return [_p(_s("could_not_read", path=PAGE_PREP))]
    if not present and live:
        return [_p(_s("words_pending"))] if PREP_WORDS.get(slot) or slot in ("rules-page", "evaluation") else []
    if not present or not isinstance(data, dict):
        return [_p(_s("words_missing"))]
    value = data.get(slot)
    if value is None and isinstance(data.get("slots"), dict):
        value = data["slots"].get(slot)
    if value is None and "interpretive_paragraph" in data:  # U23's shape
        texts = [data.get(k) for k in PREP_WORDS.get(slot, ())]
        if not texts:
            return []
        if all(isinstance(t, str) and t.strip() for t in texts):
            # The lead is one short paragraph; the guide reads as a list of its sentences, so no paragraph
            # grows into a wall of text.
            return [_p(texts[0])] + [_ul(re.split(r"(?<=[.!?])\s+", t.strip())) for t in texts[1:]]
    if isinstance(value, str) and value.strip():
        return [_p(value)]
    if isinstance(value, list) and value and all(isinstance(v, str) for v in value):
        return [_p(v) for v in value]
    return [_p(_s("words_missing"))]


def _words_block(run_dir, slot, live=False):
    words = _words(run_dir, slot, live)
    return ["<h4>%s</h4>" % _e(_s("h_words"))] + words if words else []


def orientation_html(run_dir, live=False):
    """The plain words and the processes. Pipeline state (upstream checks, the coherence gate, the review fix round)
    never shows on the page: those lines go to the builder log."""
    parts = _words_block(run_dir, "orientation", live)
    parts.extend(_orientation_processes(run_dir))
    for build in (_orientation_upstream, _orientation_gate, _orientation_fix):
        try:
            lines = [re.sub(r"<[^>]+>", " ", x) for x in build(run_dir)[1:]]
            _log(run_dir, "orientation", " ".join(" ".join(lines).split()))
        except Exception as exc:
            _log(run_dir, "orientation", "%s: %s" % (type(exc).__name__, exc))
    return "\n".join(parts)


# ---- current, future, evaluation, appendix --------------------------------------------------------------------

_SVG_START = re.compile(r"<svg\b", re.I)
_SVG_ID = re.compile(r'\bid="([^"]+)"')


def _svg_markup(text, key):
    """The SVG element alone (no XML prolog or doctype), its ids prefixed with key so two embedded drawings never
    share an id, and any script removed."""
    m = _SVG_START.search(text)
    if not m:
        return None
    end = text.lower().rfind("</svg>")
    if end < m.start():
        return None
    svg = text[m.start():end + len("</svg>")]
    svg = re.sub(r"<script\b.*?</script>", "", svg, flags=re.S | re.I)
    ids = set(re.findall(r"(?<![\w-])id=[\"']([^\"']+)[\"']", svg))
    for ident in sorted(ids, key=len, reverse=True):
        escaped = re.escape(ident)
        svg = re.sub(r"((?<![\w-])id=[\"'])" + escaped + r"([\"'])", lambda m: m[1] + key + "-" + ident + m[2], svg)
        svg = re.sub(r"(url\([\"']?#|(?:href|xlink:href)=[\"']#)" + escaped + r"(?=[\"')])", lambda m: m[1] + key + "-" + ident, svg)
    def aria(m):
        return m[1] + " ".join(key + "-" + x if x in ids else x for x in m[2].split()) + m[3]
    svg = re.sub(r"(aria-(?:labelledby|describedby)=[\"'])([^\"']+)([\"'])", aria, svg)
    return svg


def _svg_path(proc, key):
    """The drawing Future State Design wrote beside its JSON: <name>.svg next to <name>.json."""
    rel = proc.get(key) if isinstance(proc, dict) else None
    if not isinstance(rel, str) or not rel:
        return None
    base, _ = os.path.splitext(rel)
    return base + ".svg"


def _zoom_frame(svg, key):
    """The page's zoom frame (diagram.py's frame shape): Readable size by default, Fit all, a view that scrolls."""
    return ('<div class="ad-figure"><div class="rp-figure">'
            '<input type="radio" class="rp-zoom-radio rp-zoom-read" name="{k}" id="{k}-read" checked>'
            '<input type="radio" class="rp-zoom-radio rp-zoom-fit" name="{k}" id="{k}-fit">'
            '<div class="rp-zoom-bar"><label class="rp-zoom-l-read" for="{k}-read">Readable size</label>'
            '<label class="rp-zoom-l-fit" for="{k}-fit">Fit all</label>'
            '<span class="rp-zoom-hint">Scroll inside the frame to pan. Fit all shows the whole drawing.</span></div>'
            '<div class="rp-zoom-view">{svg}</div></div></div>').format(k=key, svg=svg)


def _upstream_drawings(run_dir):
    """Read copied drawing evidence only; this is not intake or a completion signal."""
    root = os.path.realpath(os.path.join(run_dir, "upstream"))
    if not os.path.isdir(root) or os.path.islink(os.path.join(run_dir, "upstream")):
        return []

    def safe(base, rel):
        if not isinstance(rel, str) or not rel or os.path.isabs(rel):
            return None
        path = os.path.realpath(os.path.join(base, rel))
        return path if path.startswith(root + os.sep) else None

    def read(path):
        if not path:
            return {}
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError, UnicodeDecodeError):
            return {}

    # Conventional single/set roots, including copied legacy per-process folders.
    bases = {root}
    for folder, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = sorted(d for d in dirs if not os.path.islink(os.path.join(folder, d)))
        if "handover.json" in files:
            bases.add(folder)
        if os.path.basename(folder) in ("current-state", "future-state") and os.path.basename(os.path.dirname(folder)) == "stages":
            bases.add(os.path.dirname(os.path.dirname(folder)))
    out = []
    for base in sorted(bases):
        handover = read(safe(base, "handover.json"))
        pointers = handover.get("pointers")
        pointers = pointers if isinstance(pointers, dict) else {}
        sm = read(safe(base, pointers.get("set_map", "inputs/set-map.json")))
        names = {p.get("process_id"): p.get("title") for p in sm.get("processes", []) if isinstance(p, dict)} if isinstance(sm.get("processes", []), list) else {}
        paths, ids = {}, []
        for key, stage in (("current_state", "current-state"), ("future_state", "future-state")):
            rel = pointers.get(key, "stages/%s/%s.json" % (stage, stage))
            path = safe(base, rel)
            svg = safe(base, os.path.splitext(rel)[0] + ".svg") if isinstance(rel, str) else None
            if path and (os.path.isfile(path) or (svg and os.path.isfile(svg))):
                paths[key] = os.path.relpath(path, os.path.realpath(run_dir))
                named = read(path).get("process", {})
                if isinstance(named, dict) and isinstance(named.get("process_ids"), list):
                    ids.extend(x for x in named["process_ids"] if isinstance(x, str))
        if not paths:
            continue
        declared = handover.get("processes")
        if isinstance(declared, list):
            ids = [x for x in declared if isinstance(x, str)] or ids
        ids = list(dict.fromkeys(ids or list(names)))
        for pid in ids or [None]:
            proc = dict(paths)
            if pid is not None:
                proc["process_id"] = pid
            if names.get(pid):
                proc["title"] = names[pid]
            out.append(proc)
    return out


def _graph_html(run_dir, key):
    """Group supported source identity/content, retaining every referencing process name."""
    pending = not os.path.exists(os.path.join(run_dir, INTAKE))
    try:
        intake = _intake(run_dir)
        procs = intake["processes"]
        if not isinstance(procs, list) or not all(isinstance(p, dict) for p in procs):
            raise TypeError("processes")
    except (Unreadable, KeyError, TypeError):
        procs = _upstream_drawings(run_dir)
        _log(run_dir, key, "intake not yet read" if pending else "intake unreadable; using copied drawing evidence")
        if not procs:
            return _p("Intake has not been read yet. No drawing is available in the copied upstream evidence." if pending else
                      "Intake could not be read. No drawing is available in the copied upstream evidence.")
    multi = len(procs) > 1
    word = _s("drawing_current") if key == "current_state" else _s("drawing_future")
    groups, identities = [], {}
    for n, proc in enumerate(procs, 1):
        name = _process_name(run_dir, proc, n, multi)
        rel = _svg_path(proc, key)
        raw = None
        if rel:
            try:
                path = os.path.join(run_dir, rel)
                if rel.startswith("upstream/") and not os.path.realpath(path).startswith(os.path.realpath(os.path.join(run_dir, "upstream")) + os.sep):
                    raise OSError("drawing escapes copied upstream")
                with open(path, encoding="utf-8") as f:
                    raw = f.read()
                if _svg_markup(raw, "probe") is None:
                    raw = None
            except (OSError, UnicodeDecodeError):
                pass
        identity = None
        if raw is not None:
            # Equal titles, basenames or process IDs alone never establish graph equality.
            try:
                source, _ = _read_run(run_dir, proc[key])
                content = json.dumps(source, sort_keys=True, ensure_ascii=False)
            except Unreadable:
                content = ""
            identity = hashlib.sha256((content + "\0" + raw).encode("utf-8")).hexdigest()
        if identity is not None and identity in identities:
            groups[identities[identity]][0].append(name)
        else:
            if identity is not None:
                identities[identity] = len(groups)
            groups.append(([name], rel, raw))
    out = [_frame_css()]
    for n, (names, rel, raw) in enumerate(groups, 1):
        if multi:
            for name in dict.fromkeys(names):
                out.append("<h4>%s</h4>" % _e(name[:1].upper() + name[1:]))
        if raw is None:
            _log(run_dir, key, "drawing not found or unreadable: %s" % (rel or "no path in intake"))
            out.append(_p(_s("drawing_missing", which=word, process=names[0])))
            continue
        ident = "ad-%s-%d" % ("cur" if key == "current_state" else "fut", n)
        svg = _svg_markup(raw, ident)
        out.append('<div class="ad-source-drawing">')
        out.append(_zoom_frame(svg, ident))
        href = quote(os.path.relpath(os.path.join(run_dir, rel), run_dir), safe="/.")
        out.append('<a class="ad-native" href="./%s" target="_blank" rel="noopener">Open the diagram on its own</a>' % _e(href))
        viewer = _module("ad_upstream_detail", _knowledge("renderer", "_diagram_viewer.py"))
        detail = viewer.detail_items(raw)
        if not detail:
            detail = [(html.unescape(re.sub(r"<[^>]*>", "", t)), "") for t in
                      re.findall(r"<(?:text|title)\b[^>]*>(.*?)</(?:text|title)>", raw, re.S) if t.strip()]
        if detail:
            out.append('<details class="ad-source-detail"><summary>Every step and link in words</summary><dl>')
            out.extend('<dt>%s</dt><dd>%s</dd>' % (_e(first), _e(more)) for first, more in detail)
            out.append('</dl></details>')
        out.append('</div>')
    return "\n".join(out)


def _frame_css():
    try:
        return _module("ad_page_diagram_css", os.path.join(HERE, "diagram", "diagram.py")).FRAME_CSS
    except Exception:
        return ""


def current_html(run_dir):
    return _graph_html(run_dir, "current_state")


def future_html(run_dir):
    return _graph_html(run_dir, "future_state")


def evaluation_html(run_dir, live=False):
    parts = _words_block(run_dir, "evaluation", live)
    summ = summary(run_dir)
    parts.append('<p class="ad-eval-counts">%s</p>' % _e(_s(
        "eval_counts", rules=summ["rules"], rule_noun=_plural(summ["rules"], "decision rule was", "decision rules were"),
        cases=summ["cases"], case_noun=_plural(summ["cases"], "test case", "test cases"))))
    if summ["expected"] is not None:
        parts.append(_p(_s("eval_expected", count=summ["expected"], noun=_plural(summ["expected"], "case", "cases"))))
    return "\n".join(parts)


def _field_words(token):
    """A field token as plain words: its separators become spaces ("invoice_total" -> "invoice total")."""
    return " ".join(re.sub(r"[_.:/\\-]+", " ", str(token)).split())


def _interface_detail(run_dir, fields):
    """The builder's copy of the interface detail: every field with its token and boundary id (builder view only)."""
    lines = ["# Interface detail (builder view)", "",
             "The person's page shows these fields in plain words; the tokens and boundary ids are kept here.", "",
             "| Field | Kind | Direction | Evidence state | Signal | Boundary |", "|---|---|---|---|---|---|"]
    for f in fields:
        lines.append("| %s |" % " | ".join(str(f.get(k, "")).replace("|", "/") for k in
                                           ("token", "type", "direction", "evidence_state", "signal", "boundary_id")))
    path = os.path.join(run_dir, INTERFACE_DETAIL)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    _write_text(path, "\n".join(lines) + "\n")


def appendix_html(run_dir):
    """The interface fields in plain words. The tokens and boundary ids go to the builder view
    (builder/interface-detail.md), never the person's page."""
    try:
        fields = _items(_read_run(run_dir, FIELDS)[0], "fields")
    except Unreadable:
        return _p(_s("could_not_read", path=FIELDS))
    if not fields:
        return _p(_s("fields_none"))
    head = "".join("<th>%s</th>" % _e(_s(k)) for k in
                   ("col_field", "col_kind", "col_direction", "col_confirmation", "col_signal"))
    rows = []
    for f in fields:
        if not isinstance(f, dict):
            return _p(_s("could_not_read", path=FIELDS))
        rows.append("<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>"
                    % (_e(_field_words(f.get("token", ""))), _e(f.get("type", "")),
                       _e(_word("direction_words", f.get("direction", ""))),
                       _e(_word("confirmation_words", f.get("evidence_state", ""))), _e(f.get("signal", ""))))
    try:
        _interface_detail(run_dir, fields)
    except OSError as exc:
        _log(run_dir, "appendix", "interface detail not written: %s" % type(exc).__name__)
    return "%s\n<table>\n<tr>%s</tr>\n%s\n</table>" % (_p(_s("fields_intro", count=len(fields),
                                                                            noun=_plural(len(fields), "interface field", "interface fields"))), head, "\n".join(rows))


# Fragments that print counts take them from summary(), never from their own rows.
SUMMARY_FED = ("simulation", "set-panel")


def _renderer(run_dir, slot, live=False):
    path = RENDERERS[slot]
    if not os.path.isfile(path):
        return _gap(live)
    try:
        mod = _module("ad_page_" + slot.replace("-", "_"), path)
        if slot in SUMMARY_FED and "summary" in inspect.signature(mod.render).parameters:
            return str(mod.render(run_dir, summary=summary(run_dir)))
        return str(mod.render(run_dir))
    except Exception as exc:  # one failing renderer never takes the page down
        _warn(run_dir, slot, os.path.relpath(path, REPO) if path.startswith(REPO) else os.path.basename(path), exc)
        return _gap(live)


def _rules_page_html(run_dir, live=False):
    return "\n".join(_words_block(run_dir, "rules-page", live) + [_renderer(run_dir, "rules-page", live)])


def _built(run_dir, slot, live=False):
    """One slot's rendered section. live: mid-run, before page-prep, where words not yet written read as pending."""
    builders = {"orientation": orientation_html, "current": current_html, "future": future_html,
                "evaluation": evaluation_html, "appendix": appendix_html, "rules-page": _rules_page_html,
                "questions": questions_html}
    takes_live = ("orientation", "evaluation", "rules-page")
    if slot in builders:
        try:
            return builders[slot](run_dir, live) if slot in takes_live else builders[slot](run_dir)
        except Exception as exc:  # a built section failing shows the gap, never the whole page
            _warn(run_dir, slot, "build.py", exc)
            return _gap(live)
    if slot == "simulation":
        _screen_flags(run_dir)
        return _renderer(run_dir, slot, live)
    return _renderer(run_dir, slot, live)


def _screen_flags(run_dir):
    """The judgment screen's flags (U20b) are check state: they go to the builder log, never the page."""
    try:
        report, present = _read_run(run_dir, SCREEN_REPORT)
    except Unreadable:
        _log(run_dir, "simulation", "%s could not be read" % SCREEN_REPORT)
        return
    problems = report.get("problems") if isinstance(report, dict) else None
    if not present or not isinstance(problems, list):
        return
    for p in problems:
        _log(run_dir, "simulation", "judgment screen flag: %s" % (json.dumps(p, sort_keys=True)
                                                                  if isinstance(p, dict) else p))


# ---- the stage hook -------------------------------------------------------------------------------------------

def _lib():
    return _module("progressive_for_agent_design", PROGRESSIVE)


def _page_dir(run_dir):
    return os.path.join(run_dir, "page")


def _numbered(template):
    """The slots with each section numbered in tab order ("1. Orientation" ...); the numbers are computed here, so
    the template holds no digits."""
    order = []
    for s in template["slots"]:
        if s["section"] not in order:
            order.append(s["section"])
    return [dict(s, section="%d. %s" % (order.index(s["section"]) + 1, s["section"]),
                 title=_s("evaluation_title") if s["id"] == "evaluation" else s["title"])
            for s in template["slots"]]


def _ensure(lib, run_dir):
    page_dir = _page_dir(run_dir)
    if not os.path.isfile(os.path.join(page_dir, "page-state.json")):
        lib.scaffold(page_dir, _numbered(_template("slots.json")))
    else:
        state_path = os.path.join(page_dir, "page-state.json")
        with open(state_path, encoding="utf-8") as f:
            state = json.load(f)
        changed = False
        for slot in state["slots"]:
            if slot["id"] == "evaluation" and slot["title"] == "How the design was tested":
                slot["title"] = _s("evaluation_title")
                changed = True
        if changed:
            _write_text(state_path, json.dumps(state, indent=2) + "\n")
    return page_dir


def _plain_text_mod():
    return _module("ad_plain_text_for_page", os.path.join(AD, "checks", "plain_text.py"))


# Source text kinds that reach the page as defects; a displayed paragraph over this many words is a length defect
# (page_checks.P_WORDS).
SOURCE_KINDS = ("id", "field-token", "decode")
PARA_WORDS = 60
_NOT_PROSE = re.compile(r"(^|_)(id|ids|ref|refs|status|kind|type|severity|anchor|token)$")


def _register_texts(reg):
    """Every string the register writes that the page can display (items and identity_runtime_opens, any depth),
    with where it sits; id, status and kind fields are not prose."""
    out = []

    def walk(node, where, key):
        if isinstance(node, str):
            if node.strip() and not (key and _NOT_PROSE.search(key)):
                out.append((where, node))
        elif isinstance(node, dict):
            for k in sorted(node):
                walk(node[k], "%s.%s" % (where, k), k)
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, "%s[%d]" % (where, i), key)
    for top in ("items", "identity_runtime_opens"):
        walk(reg.get(top), top, None)
    return out


def _text_findings(run_dir):
    """Text defects in every displayed piece of prose page-prep and the register wrote (ids, field names, codes,
    over-long paragraphs, pipeline-state words), each named with the stage that owns the words, so the driver can
    route one plain-words repair to that owner. Returns (findings, sources): sources maps owner -> its prose."""
    findings, sources = [], {"page-prep": [], "register": []}
    try:
        pt = _plain_text_mod()
    except Exception as exc:
        _log(run_dir, "page", "plain-words scan not loaded: %s" % type(exc).__name__)
        pt = None

    def scan(owner, where, text):
        sources[owner].append(text)
        found = []
        if pt is not None:
            pt.scan_text(text, where, lambda kind, w, detail: found.append((kind, w, detail)), [], [])
        for kind, w, detail in found:
            if kind in SOURCE_KINDS:
                findings.append({"owner_stage": owner, "kind": kind, "where": w, "detail": _scrub(detail, run_dir)})
        if len(text.split()) > PARA_WORDS:
            findings.append({"owner_stage": owner, "kind": "length", "where": where,
                             "detail": "paragraph is over %d words" % PARA_WORDS})
        for m in PIPELINE_WORDS.finditer(text):
            findings.append({"owner_stage": owner, "kind": "pipeline-state", "where": where,
                             "detail": "pipeline-state words %r" % m.group(0)})

    try:
        prep, present = _read_run(run_dir, PAGE_PREP)
    except Unreadable:
        prep, present = None, False
        findings.append({"owner_stage": "page-prep", "kind": "unreadable", "where": PAGE_PREP,
                         "detail": "page-prep output could not be read"})
    if present and isinstance(prep, dict) and pt is not None:
        for where, text in pt.reader_texts(prep):
            scan("page-prep", where, text)
    try:
        reg, present = _read_run(run_dir, REGISTER)
    except Unreadable:
        reg, present = None, False
        findings.append({"owner_stage": "register", "kind": "unreadable", "where": REGISTER,
                         "detail": "register output could not be read"})
    if present and isinstance(reg, dict):
        for where, text in _register_texts(reg):
            scan("register", where, text)
    elif present:
        findings.append({"owner_stage": "register", "kind": "structure", "where": REGISTER,
                         "detail": "register output is not an object"})
    return findings, sources


def _page_findings(run_dir, gate, checks, sources):
    """Final-gate and page-check problems as notes. A problem quoting text that only one stage wrote belongs to that
    stage; with no attributable source it belongs to page-prep."""
    out = []
    for source, problems in (("final-gate", gate["problems"]), ("page-checks", checks["problems"])):
        for p in problems:
            kind = p.split(":", 1)[0] if ":" in p else source
            owner = "page-prep"
            for quoted in re.findall(r"'([^']{2,})'|\"([^\"]{2,})\"", p):
                q = quoted[0] or quoted[1]
                in_reg = any(q in t for t in sources["register"])
                in_prep = any(q in t for t in sources["page-prep"])
                if in_reg and not in_prep:
                    owner = "register"
                    break
            out.append({"owner_stage": owner, "kind": kind, "where": source, "detail": p})
    return out


def _tidy_visible(text, run_dir):
    """Remove raw ids and process-id hex from visible text (text nodes outside <style>, <script> and the slots whose
    <code> may show ids), keeping the sentence's plain words. Ids inside attributes stay. Returns (html, removed)."""
    checks = _module("ad_page_checks_patterns", PAGE_CHECKS) if os.path.isfile(PAGE_CHECKS) else None
    patterns = list(getattr(checks, "_RAW_PATTERNS", [])) + [_HEX_ID]
    code_slots = getattr(checks, "CODE_SLOTS", ("appendix", "simulation"))
    names = {}
    try:
        intake = _intake(run_dir) or {}
        procs = intake.get("processes") or []
        for index, proc in enumerate(procs, 1):
            if proc.get("process_id"):
                names[proc["process_id"]] = _process_name(run_dir, proc, index, len(procs) > 1)
        agent, _ = _read_run(run_dir, "stages/architecture/agent.yaml")
        for part in (agent or {}).get("parts") or []:
            title = _plain_title(part.get("title") or part.get("name"))
            if part.get("id") and title:
                names[part["id"]] = title
    except (Unreadable, AttributeError, TypeError):
        pass
    removed = []
    out, pos = [], 0
    preserved = {}
    stack = []  # (tag, exempt)
    tag_re = re.compile(r"<!--.*?-->|<(/?)([A-Za-z][A-Za-z0-9-]*)([^>]*)>", re.S)
    for m in tag_re.finditer(text):
        chunk = text[pos:m.start()]
        exempt = any(e is True for _, e in stack)
        hidden = any(n in ("style", "script", "svg") or e == "source" for n, e in stack)
        source = any(e == "source-copy" for _, e in stack)
        raw_chunk = chunk
        chunk = html.unescape(chunk)  # entities decoded, so R&#45;12 is seen as R-12
        if chunk and not hidden:
            for identity, label in names.items():
                chunk = re.sub(r"(?<![\w-])" + re.escape(identity) + r"(?![\w-])", lambda m: label, chunk)
            if source:
                # Resolve known names, but retain unresolved references and business facts.
                raw_chunk = html.escape(chunk, quote=False)
            def drop(mm):
                removed.append(mm.group(0))
                return ""
            # a process id never shows, not even in the slots whose code may show other ids
            chunk = chunk if source else re.sub(r"\b[0-9a-f]{12}\b:?", drop, chunk)
            if not source and PIPELINE_WORDS.search(chunk):
                pieces = re.split(r"(?<=[.!?])(\s+)", chunk)
                chunk = "".join(drop(re.match(r"[\s\S]*", x)) if PIPELINE_WORDS.search(x) else x for x in pieces)
        if chunk and not exempt and not hidden and not source:
            def cut(mm):
                token = mm.group(0)
                removed.append(token)
                return token.replace("_", " ") if "_" in token else ""
            new = chunk
            for pat in patterns:
                new = pat.sub(cut, new)
            if new != chunk:
                new = re.sub(r"[ \t]{2,}", " ", new)
                new = re.sub(r"\(\s*\)|\[\s*\]", "", new)
                new = re.sub(r"\s+([,.;:])", r"\1", new)
            chunk = new
        rendered = raw_chunk if chunk == html.unescape(raw_chunk) else html.escape(chunk, quote=False)
        if source or any(e == "source" for _, e in stack):
            # Restore only after both authored-control cleanup passes have finished.
            token = "\x00AD_SOURCE_%d\x00" % len(preserved)
            preserved[token] = rendered
            rendered = token
        out.append(rendered)
        out.append(m.group(0))
        pos = m.end()
        if m.group(2):
            name = m.group(2).lower()
            if m.group(1):
                for i in range(len(stack) - 1, -1, -1):
                    if stack[i][0] == name:
                        del stack[i:]
                        break
            elif not m.group(3).rstrip().endswith("/") and name not in (
                    "br", "hr", "img", "input", "meta", "link", "col", "wbr", "source", "area", "base", "path",
                    "line", "rect", "circle", "polyline", "polygon", "ellipse", "use", "stop"):
                attrs = m.group(3)
                in_code_slot = any(e == "slot" for _, e in stack)
                slot = re.search(r'data-slot="([^"]*)"', attrs)
                if 'data-ad-source="true"' in attrs:
                    mark = "source-copy"
                elif 'class="ad-source-drawing"' in attrs:
                    mark = "source"
                elif name in ("style", "script"):
                    mark = True
                elif slot and slot.group(1) in code_slots:
                    mark = "slot"
                elif name in ("code", "pre") and in_code_slot:
                    mark = True
                else:
                    mark = False
                stack.append((name, mark))
    out.append(text[pos:])
    result = "".join(out)
    # Attribute values and class names that carry a pipeline word are renamed the same way everywhere, so filters
    # that match one attribute against another still agree.
    renamed = len(re.findall(r"check_", result))
    if renamed:
        removed.append("check_ (renamed %d times inside attributes)" % renamed)
        result = result.replace("check_", "check-")
    # Pipeline state is banned everywhere in the HTML, attributes and the title included: what the sentence pass left
    # (an attribute value, a word inside markup) is removed the same way wherever it sits.

    def gone(mm):
        removed.append("pipeline-state words %r" % mm.group(0))
        return ""
    result = PIPELINE_WORDS.sub(gone, result)
    for token, original in preserved.items():
        result = result.replace(token, original)
    return result, removed


def _title_of(run_dir):
    try:
        intake = _intake(run_dir) or {}
    except Unreadable:
        intake = {}
    procs = intake.get("processes") if isinstance(intake.get("processes"), list) else []
    if len(procs) == 1:
        name = _process_name(run_dir, procs[0], 1, False)
        if name != "the process":
            return _s("page_title_one", process=name)
    return _s("page_title")


def _write_text(path, text):
    tmp = path + ".part"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    os.replace(tmp, path)


def _final(lib, run_dir):
    """The page always publishes. Final-gate, page-check and text problems become notes in page/gate.json, the builder
    log and the repair route (stages/page/page-route.json, grouped by the stage that owns the words); remaining raw
    ids are removed from the visible text and the page shows one quiet line. Raises only when there is no page."""
    page_dir = _page_dir(run_dir)
    try:
        gate = lib.final_gate(page_dir)
        gate = {"ok": bool(gate.get("ok")), "problems": [_scrub(p, run_dir) for p in gate.get("problems", [])]}
    except Exception as exc:
        gate = {"ok": False, "problems": [_scrub("final gate raised %s: %s" % (type(exc).__name__, exc), run_dir)]}
    published = os.path.join(run_dir, "agent-design.html")
    try:
        with open(os.path.join(page_dir, "page.html"), encoding="utf-8") as f:
            text = f.read()
    except (OSError, UnicodeDecodeError) as exc:
        out = {"final_gate": gate, "published": False, "kept_previous": os.path.exists(published),
               "failure": _scrub("page.html could not be read: %s" % type(exc).__name__, run_dir)}
        _write_text(os.path.join(page_dir, "gate.json"), json.dumps(out, indent=2, sort_keys=True) + "\n")
        _log(run_dir, "page", out["failure"])
        raise RuntimeError("Agent Design page could not be produced: " + out["failure"])
    if os.path.isfile(PAGE_CHECKS):
        try:
            found = [_scrub(p, run_dir) for p in _module("ad_page_checks", PAGE_CHECKS).problems(text)]
            checks = {"ran": True, "ok": not found, "problems": found}
        except Exception as exc:
            checks = {"ran": True, "ok": False,
                      "problems": [_scrub("page checks raised %s: %s" % (type(exc).__name__, exc), run_dir)]}
    else:
        checks = {"ran": False, "ok": False, "problems": [PAGE_CHECKS_NOT_RUN]}
    findings, sources = _text_findings(run_dir)
    findings += _page_findings(run_dir, gate, checks, sources)
    # The title goes in before the tidy, so it is cleaned like any visible text.
    text = re.sub(r"<title>.*?</title>", "<title>%s</title>" % _e(_title_of(run_dir)), text, count=1, flags=re.S)
    try:
        text, removed = _tidy_visible(text, run_dir)
    except Exception as exc:
        removed = []
        _log(run_dir, "page", "visible-text tidy failed: %s" % type(exc).__name__)
    for r in removed:
        _log(run_dir, "page", "removed from visible text: %s" % r)
    clean = gate["ok"] and checks["ok"] and not findings and not removed
    # One quiet note at most, however many times the page is finalized.
    text = re.sub(r'\n?<p class="ad-quiet-note">.*?</p>', "", text, flags=re.S)
    if not clean:
        note = '<p class="ad-quiet-note">%s</p>' % _e(QUIET_NOTE)
        m = re.search(r'(<section class="pp-panel"[^>]*>\s*<h2>.*?</h2>)', text, re.S)
        text = text[:m.end()] + "\n" + note + text[m.end():] if m else text.replace("</body>", note + "\n</body>", 1)
    _write_text(os.path.join(page_dir, "page.html"), text)
    _write_text(published, text)
    summ = summary(run_dir)
    for c in summ["conflicts"]:
        _log(run_dir, "summary", c)
    by_owner = {"page-prep": [f for f in findings if f["owner_stage"] == "page-prep"],
                "register": [f for f in findings if f["owner_stage"] == "register"]}
    out = {"final_gate": gate, "page_checks": checks, "clean": clean, "published": True, "notes": findings,
           "by_owner": by_owner, "removed_from_visible_text": len(removed), "summary_conflicts": summ["conflicts"]}
    _write_text(os.path.join(page_dir, "gate.json"), json.dumps(out, indent=2, sort_keys=True) + "\n")
    route_dir = os.path.join(run_dir, "stages", "page")
    os.makedirs(route_dir, exist_ok=True)
    _write_text(os.path.join(run_dir, ROUTE), json.dumps({"findings": findings}, indent=2, sort_keys=True) + "\n")
    for f in findings:
        _log(run_dir, "page", "note for %s: %s %s %s" % (f["owner_stage"], f["kind"], f["where"], f["detail"]))


# ---- the person's page, published as the run goes ---------------------------------------------------------------

def _stage_order():
    try:
        return [e["key"] for e in _load(INDEX)["stages"] if isinstance(e, dict) and isinstance(e.get("key"), str)]
    except (OSError, ValueError, KeyError, TypeError):
        return []


def _groups(order):
    """The person's rows: limits.GROUPS (the status table's grouping), a stage it does not list joining Final checks."""
    try:
        groups = list(_module("ad_limits_for_page", LIMITS).GROUPS)
    except Exception:  # noqa: BLE001 - with no grouping, one row per stage
        groups = [(k, [k]) for k in order]
    known = set(k for _, ks in groups for k in ks)
    extra = [k for k in order if k not in known]
    out = [(n, [k for k in ks if k in order or not order] + (extra if n == "Final checks" else [])) for n, ks in groups]
    return [(n, ks) for n, ks in out if ks]


def progress(run_dir, finished=None, starting=None):
    """[(row label, state)] for the page's progress strip, from state.json. finished: the stage whose finish is
    being recorded (the hook runs before ad.py writes it), counted as done; starting: the stage just started. When
    no stage is running, the first row not yet done is the one running now (the session goes straight to it)."""
    try:
        stages = _load(os.path.join(run_dir, "state.json")).get("stages")
    except (OSError, ValueError, AttributeError):
        stages = None
    stages = stages if isinstance(stages, dict) else {}
    order = _stage_order() or sorted(stages)
    status = {k: str((stages.get(k) or {}).get("status", "pending")) if isinstance(stages.get(k), dict) else "pending"
              for k in order}
    if finished in status and not status[finished].startswith(("done", "failed")):
        status[finished] = "done"
    if starting in status:
        status[starting] = "running"
    rows = []
    for name, ks in _groups(order):
        sts = [status.get(k, "pending") for k in ks]
        if any(s.startswith("failed") for s in sts):
            state = "unavailable"
        elif any(s == "running" for s in sts):
            state = "running"
        elif all(s.startswith("done") for s in sts):
            state = "done-notes" if any(s != "done" for s in sts) else "done"
        elif any(s.startswith("done") for s in sts):
            state = "running"
        else:
            state = "pending"
        rows.append((name, state))
    if not any(s == "running" for _, s in rows):
        for n, (name, state) in enumerate(rows):
            if state == "pending":
                rows[n] = (name, "running")
                break
    return rows


def _live_html(run_dir):
    """The working page for the person: page/page.html with its title and the same visible-text tidy the final page
    gets (no raw ids or pipeline words), so nothing internal shows while the run goes."""
    with open(os.path.join(_page_dir(run_dir), "page.html"), encoding="utf-8") as f:
        text = f.read()
    text = re.sub(r"<title>.*?</title>", "<title>%s</title>" % _e(_title_of(run_dir)), text, count=1, flags=re.S)
    text, removed = _tidy_visible(text, run_dir)
    for r in removed:
        _log(run_dir, "live-page", "removed from visible text: %s" % r)
    return _output_links(text)


def _output_links(text):
    """Slots/root copy use run-relative links; the published page lives in outputs/."""
    return text.replace('class="ad-native" href="./', 'class="ad-native" href="../')


def _final_html(run_dir):
    with open(os.path.join(_page_dir(run_dir), "page.html"), encoding="utf-8") as f:
        return _output_links(f.read())


def _publish(run_dir, mode, finished=None, starting=None, reason=None):
    """Publish the person's page through the shared live page; never raises. Returns live.publish's result, or None
    when the live page module cannot be loaded (recorded in the builder log)."""
    try:
        if not LIVE or not os.path.isfile(LIVE):
            raise FileNotFoundError("the shared live page module was not found")
        live = _module("ad_live_page", LIVE)
        render = (lambda: _final_html(run_dir)) if mode == "final" else (lambda: _live_html(run_dir))
        return live.publish(os.path.join(run_dir, PUBLISHED), render, progress(run_dir, finished, starting), mode,
                            os.path.join(run_dir, RECORD_DIR), reason=reason,
                            record=lambda line: _log(run_dir, "live-page", line), restart=starting is not None)
    except Exception as exc:  # noqa: BLE001 - the person's page never stops a run
        try:
            _log(run_dir, "live-page", "not published: %s: %s" % (type(exc).__name__, exc))
        except Exception:  # noqa: BLE001
            pass
        return None


def _publishes(stage):
    """Every stage up to the page stage publishes; later ones (post-run, handoff) leave the final page alone."""
    order = _stage_order()
    if stage not in order or LAST_PUBLISHING_STAGE not in order:
        return True
    return order.index(stage) <= order.index(LAST_PUBLISHING_STAGE)


def _early_drawings(lib, run_dir):
    for slot in _template("stage-slots.json").get("early_built", []):
        lib.fill(_page_dir(run_dir), slot, None, _built(run_dir, slot, True))


def start_stage(run_dir, stage):
    """Publish the person's page with stage running (for the driving script's stage start); never raises."""
    try:
        lib = _lib()
        _ensure(lib, run_dir)
        if _publishes(stage):   # post-run and handoff leave the final page alone
            _early_drawings(lib, run_dir)
    except Exception as exc:  # noqa: BLE001 - a scaffold failure falls back to live.publish's plain page
        _log(run_dir, "live-page", "scaffold at start: %s: %s" % (type(exc).__name__, exc))
    return _publish(run_dir, "live", starting=stage) if _publishes(stage) else None


def publish_ended(run_dir, reason=None):
    """Publish the page of a run that stopped before it finished: no reload, unreached parts say so; never raises."""
    return _publish(run_dir, "ended", reason=reason)


def fill_stage(run_dir, stage):
    """Fill the slots stage maps to (template/stage-slots.json) with their rendered sections, then publish the
    person's page (live; final at the page stage, after the final gate). Returns the publication result."""
    lib = _lib()
    _ensure(lib, run_dir)
    if _publishes(stage):
        _early_drawings(lib, run_dir)
    entry = _template("stage-slots.json")["stages"].get(stage)
    if entry is None:
        return _publish(run_dir, "live", finished=stage) if _publishes(stage) else None
    multi = summary(run_dir)["processes"] > 1
    live = stage != "page-prep" and not entry.get("final_gate")
    for slot in entry.get("built", []) + entry.get("set_only", []):
        if slot == "set-panel" and not multi:
            # One process is not a set: the panel's set wording would read "1 processes".
            lib.fill(_page_dir(run_dir), slot, None, _p(_s("single_process")))
            continue
        lib.fill(_page_dir(run_dir), slot, None, _built(run_dir, slot, live))
    if multi:
        for slot in entry.get("refresh_set_only", []):
            lib.fill(_page_dir(run_dir), slot, None, _renderer(run_dir, slot, live))
    if entry.get("final_gate"):
        try:
            _final(lib, run_dir)
        except Exception:
            _publish(run_dir, "final")   # no readable page: the last good page is kept and shown as ended
            raise
        return _publish(run_dir, "final")
    return _publish(run_dir, "live", finished=stage) if _publishes(stage) else None
