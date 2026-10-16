"""Set panel for the Agent Design page (U30).

render(run_dir) -> str returns the HTML fragment for the page's set-panel slot: each process with its evidence
strength (from the intake), the shared work objects with their states, the links between processes in plain words,
and the coverage table from stages/_set/coverage.py (whose report() writes page/set-coverage.json as it runs).
Every count comes from the page summary (page/build.py summary()), never from the panel's own reading. A missing name, evidence strength or link label shows
"not recorded"; ids never appear in the text (U27's raw-id check). Loaded by file path from page/build.py.
"""
import html
import importlib.util
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(os.path.dirname(HERE))
BUILD = os.path.join(os.path.dirname(HERE), "build.py")
INTAKE = "stages/intake/intake.json"
NOT_RECORDED = "not recorded"

STATE_WORDS = [("Observed", "observed"), ("Assumed", "assumed"), ("Contradictory", "contradictory"),
               ("needs_more_information", "needs more information")]
LINK_WORDS = {
    "handoff": "hands work on to",
    "variant_entry": "is another way into",
    "parent_child": "starts work inside",
    "lifecycle_continuation": "continues the work of",
    "decision_gate": "decides whether work goes on to",
    "re_entry": "sends work back into",
    "overlap": "moves the same work as",
    "analogous_work": "does similar work to",
    "shared_knowledge": "shares knowledge with",
    "shared_team_or_system": "shares a team or system with",
}
SOURCE_WORDS = [("stages/intake/", "the intake"), ("stages/placement/", "the placed rules"),
                ("stages/test-suite/", "the test suite")]
CONTEXT_KINDS = {"analogous_work", "shared_knowledge", "shared_team_or_system"}
# Work moves from `from` to `to` on every flow kind. These kinds read naturally with the destination first:
# "<to> continues the work of <from>".
TO_FIRST_KINDS = {"lifecycle_continuation"}


def _e(text):
    return html.escape(str(text), quote=True)


def _plural(n, one, many):
    return one if n == 1 else many


def _summary(run_dir):
    """The page's one computed summary (page/build.py summary()): every count on the panel comes from there."""
    spec = importlib.util.spec_from_file_location("ad_page_build_for_set_panel", BUILD)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.summary(run_dir)


def _read(run_dir, rel):
    with open(os.path.join(run_dir, rel), encoding="utf-8") as f:
        return json.load(f)


def _text(value):
    return value if isinstance(value, str) and value.strip() else None


def _names(set_map):
    out = {}
    for p in set_map.get("processes") or []:
        if isinstance(p, dict) and isinstance(p.get("process_id"), str):
            out[p["process_id"]] = _text(p.get("title"))
    return out


def _name(names, pid):
    return names.get(pid) or NOT_RECORDED


def _strength(proc, summ):
    """The process's evidence strength as the summary counts it (summary()["process_evidence"])."""
    pid = proc.get("process_id") if isinstance(proc, dict) else None
    es = proc.get("evidence_strength") if isinstance(proc, dict) else None
    counted = (summ.get("process_evidence") or {}).get(pid) if isinstance(pid, str) else None
    if not isinstance(es, dict) or not isinstance(counted, dict):
        return NOT_RECORDED
    parts = []
    for key, one, word in (("observations", "observation", "observations"), ("sources", "source", "sources")):
        n = counted.get(key)
        parts.append("%d %s" % (n, _plural(n, one, word)) if isinstance(n, int) and not isinstance(n, bool) else "%s: %s" % (word, NOT_RECORDED))
    states = counted.get("states")
    if isinstance(states, dict):
        steps = []
        for key, word in STATE_WORDS:
            n = states.get(key)
            steps.append("%s %s" % (n, word) if isinstance(n, int) and not isinstance(n, bool) else "%s %s" % (word, NOT_RECORDED))
        parts.append("steps: " + ", ".join(steps))
    else:
        parts.append("steps: " + NOT_RECORDED)
    return "; ".join(parts)


def _join(names, ids):
    ids = [i for i in ids or [] if isinstance(i, str)]
    return " and ".join(_name(names, i) for i in ids) if ids else NOT_RECORDED


def _link_sentence(link, names, objects, states):
    kind = link.get("kind")
    verb = LINK_WORDS.get(kind)
    left, right = _join(names, link.get("from")), _join(names, link.get("to"))
    if verb is None:
        sentence = "%s and %s are linked (how: %s)" % (left, right, NOT_RECORDED)
    elif kind in TO_FIRST_KINDS:
        sentence = "%s %s %s" % (right, verb, left)
    else:
        sentence = "%s %s %s" % (left, verb, right)
    obj = objects.get(link.get("object")) if link.get("object") else None
    state = states.get(link.get("state")) if link.get("state") else None
    if link.get("object"):
        sentence += ", carrying %s" % (obj or NOT_RECORDED)
        if link.get("state"):
            sentence += " at %s" % (state or NOT_RECORDED)
    if kind in CONTEXT_KINDS:
        sentence += " (context only; no work crosses)"
    return sentence + "."


def _coverage_html(summ, names):
    rep = summ.get("coverage")
    if rep is None:
        e = summ.get("coverage_error") or ""
        missing = [w for rel, w in SOURCE_WORDS if e.startswith(rel)]
        return "<p>Coverage per process: %s (%s cannot be read yet).</p>" % (NOT_RECORDED, _e(missing[0] if missing else "a source"))
    rows = []
    for p in rep["processes"]:
        why = []
        if p["rules"] == 0:
            why.append("no rules")
        if p["cases"] == 0:
            why.append("no cases")
        rows.append("<tr><td>%s</td><td>%d</td><td>%d</td><td>%s</td></tr>"
                    % (_e(_name(names, p["process_id"])), p["rules"], p["cases"],
                       _e("uncovered: " + " and ".join(why) if why else "covered")))
    head = "<tr><th>Process</th><th>Placed rules</th><th>Test cases</th><th>Coverage</th></tr>"
    notes = []
    u = rep["untagged"]
    if u["rules"] or u["cases"]:
        notes.append("<p>%d %s and %d %s no process, so they count for none.</p>"
                     % (u["rules"], _plural(u["rules"], "placed rule", "placed rules"), u["cases"],
                        _plural(u["cases"], "test case names", "test cases name")))
    totals = summ.get("coverage_counts") or {}
    unknown = totals.get("unknown_tags", 0)
    if unknown:
        notes.append("<p>%d %s a process that is not in this set.</p>"
                     % (unknown, _plural(unknown, "process tag names", "process tags name")))
    total = totals.get("processes", 0)
    lead = "<p>%d of %d %s both placed rules and test cases.</p>" % (
        totals.get("covered", 0), total, _plural(total, "process has", "processes have"))
    return "%s\n<table>\n%s\n%s\n</table>\n%s" % (lead, head, "\n".join(rows), "\n".join(notes))


def render(run_dir, summary=None):
    summ = summary if summary is not None else _summary(run_dir)
    intake = _read(run_dir, INTAKE)
    procs = [p for p in intake.get("processes") or [] if isinstance(p, dict)]
    set_map = {}
    if isinstance(intake.get("set_map"), str):
        try:
            set_map = _read(run_dir, intake["set_map"])
        except (OSError, ValueError):
            set_map = {}
    names = _names(set_map if isinstance(set_map, dict) else {})
    objects = {o.get("id"): _text(o.get("name")) for o in set_map.get("work_objects") or [] if isinstance(o, dict)}
    states_by_obj = {}
    state_names = {}
    for s in set_map.get("object_states") or []:
        if isinstance(s, dict):
            state_names[s.get("id")] = _text(s.get("name"))
            states_by_obj.setdefault(s.get("object"), []).append(s)

    out = ["<h3>Processes in this set</h3>",
           "<p>This design covers %d %s as one set.</p>" % (
               summ["processes"], _plural(summ["processes"], "process", "processes")), "<ul>"]
    for p in procs:
        out.append("<li><strong>%s</strong>: evidence %s.</li>" % (_e(_name(names, p.get("process_id"))), _e(_strength(p, summ))))
    out.append("</ul>")

    out.append("<h4>Shared work</h4>")
    if objects:
        out.append("<ul>")
        for oid, oname in objects.items():
            states = sorted(states_by_obj.get(oid, []), key=lambda s: s.get("order") if isinstance(s.get("order"), int) else 0)
            seq = ", then ".join(_text(s.get("name")) or NOT_RECORDED for s in states) or NOT_RECORDED
            n = (summ.get("object_states") or {}).get(oid, 0)
            out.append("<li>%s, which moves through %d %s: %s.</li>" % (_e(oname or NOT_RECORDED), n,
                                                                          _plural(n, "state", "states"), _e(seq)))
        out.append("</ul>")
    else:
        out.append("<p>Shared work object: %s.</p>" % NOT_RECORDED)

    links = [l for l in set_map.get("links") or [] if isinstance(l, dict)]
    out.append("<h4>How the processes meet</h4>")
    if links:
        out.append("<ul>")
        for l in links:
            out.append("<li>%s</li>" % _e(_link_sentence(l, names, objects, state_names)))
        out.append("</ul>")
    else:
        out.append("<p>Links between processes: %s.</p>" % NOT_RECORDED)

    out.append("<h4>Rules and cases per process</h4>")
    out.append(_coverage_html(summ, names))
    return "\n".join(out)
