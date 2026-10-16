"""Embed the simulation result in the Agent Design page (pins C: AD/page/simulation/embed.py).

    render(run_dir) -> str

Reads <run_dir>/simulation/walk.json (U2's simulation.schema.json, written by U20a) and returns one HTML fragment
scoped under #simulation: a sticky bar with the counts, the cases at a glance and one case card per case, each card
opened by :target from its row. Ported from simulation v1's page (simpage_v1.render / step_html) with experiment 08's
two fixes: the narrow layout is keyed to the panel (container query at 900 px or less, never the viewport) and each
:target case has scroll-margin-top larger than the sticky bar, so a jump never hides its heading. Case counts come
from the displayed walk entries; rule coverage comes from the page summary (page/build.py summary()). Cases are also grouped by part (the sub-agent each walked step records) and interface (a
hand-over wherever the next step's part differs, as U20b's interfaces.py derives it). Cases the converter left out of
the walk (simulation/sim-input.json unconvertible[], stamped-open exclusions included) are listed with their reason.
Pure HTML and CSS, no script. Standard library only, Python 3.9+.
"""
import html
import importlib.util
import json
import os
import re

WALK = os.path.join("simulation", "walk.json")
BUILD = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "build.py")
SIM_INPUT = os.path.join("simulation", "sim-input.json")
SUITE = os.path.join("stages", "test-suite", "test-suite.json")
NO_PART = "not given"
INSIDE = "inside the part, no hand-over"
GAP = "This part was not produced in this run."
DROPPED = ("execution_error", "not_executed", "needs_more_information")
STATE_WORDS = {"matches": "Matches", "differs": "Differs", "execution_error": "Could not run",
               "not_executed": "Not run", "needs_more_information": "Needs more information"}
STATE_CLASS = {"matches": "k-good", "differs": "k-esc"}
BAR_PX = 56          # the sticky bar's fixed height
SCROLL_MARGIN_PX = 72  # scroll-margin-top of every :target case; at least BAR_PX

CSS = """
#simulation{container-type:inline-size;--sim-coral:#F56740;--sim-ink:#0B1E2F;--sim-grey:#5E6670;--sim-line:#E7DFD3;--sim-card:#FFFFFF;--sim-bg:#FBF6EF;--sim-sky:#CFF3FE;--sim-rose:#FFE0D8;--sim-sun:#FFF5D2;position:relative;min-width:0}
#simulation *{box-sizing:border-box}
#simulation .sim-bar{position:sticky;top:0;z-index:2;height:%(bar)dpx;overflow:hidden;display:flex;align-items:center;gap:8px 14px;padding:0 12px;background:var(--sim-card);border-bottom:2px solid var(--sim-coral);font-size:.88rem}
#simulation .sim-bar b{white-space:nowrap}
#simulation .sim-bar span{white-space:nowrap;color:var(--sim-grey)}
#simulation h4{font-size:1rem;margin:22px 0 8px}
#simulation .sim-sub{color:var(--sim-grey);margin:0 0 12px;font-size:.9rem}
#simulation .sim-wrap{overflow-x:auto;max-width:100%%}
#simulation table{border-collapse:collapse;width:100%%;background:var(--sim-card)}
#simulation th,#simulation td{text-align:left;padding:7px 10px;border-bottom:1px solid var(--sim-line);vertical-align:top;font-size:.88rem;overflow-wrap:anywhere}
#simulation th{font-size:.76rem;color:var(--sim-grey);font-weight:600}
#simulation code{font:.82rem ui-monospace,Menlo,monospace;overflow-wrap:anywhere}
#simulation .pill{display:inline-block;padding:2px 9px;border-radius:999px;font-size:.78rem;font-weight:600;white-space:nowrap;background:var(--sim-line);color:var(--sim-ink)}
#simulation .k-good{background:var(--sim-sky)}
#simulation .k-esc{background:var(--sim-rose)}
#simulation .k-drop{background:var(--sim-sun)}
#simulation .sim-case{background:var(--sim-card);border:1px solid var(--sim-line);border-radius:12px;padding:14px 16px 10px;margin:0 0 14px;scroll-margin-top:%(margin)dpx}
#simulation .sim-case:target{border-color:var(--sim-coral);box-shadow:0 0 0 3px rgba(245,103,64,.25)}
#simulation .sim-case h5{font-size:1rem;margin:0 0 6px;display:flex;flex-wrap:wrap;gap:6px 10px;align-items:baseline}
#simulation .sim-outcome{margin:0 0 8px;font-size:.9rem}
#simulation .sim-drop{margin:0 0 8px;padding:6px 10px;border:1px dashed var(--sim-grey);border-radius:8px;font-size:.88rem}
#simulation .sim-detail{display:none}
#simulation .sim-case:target .sim-detail{display:grid;grid-template-columns:minmax(380px,1fr) minmax(380px,1fr);gap:14px}
#simulation .sim-open{font-size:.82rem;color:var(--sim-grey)}
#simulation .sim-case:target .sim-open{display:none}
#simulation .sim-miss{color:var(--sim-coral);font-weight:600}
#simulation .sim-steps{grid-column:1/-1;margin:0;padding:0 0 0 22px;font-size:.88rem}
#simulation .sim-steps li{margin:0 0 8px;overflow-wrap:anywhere}
#simulation .sim-stephead{display:flex;flex-wrap:wrap;gap:4px 10px;align-items:baseline}
#simulation .sim-held{font-weight:600}
#simulation .sim-vals{margin:4px 0 0;padding:0 0 0 18px;color:var(--sim-grey)}
@container (max-width: 900px){#simulation .sim-case:target .sim-detail{grid-template-columns:minmax(0,1fr)}#simulation .sim-bar{gap:4px 10px;font-size:.8rem}}
""" % {"bar": BAR_PX, "margin": SCROLL_MARGIN_PX}


def _e(value):
    return html.escape(str(value if value is not None else ""), quote=True)


def _c(value):
    """An id the page shows (case id, rule id, outcome label, token, part): always inside <code>, the only place the
    raw-id check lets an id stand in the simulation slot (orchestrator ruling, 13 Oct 2026)."""
    return "<code>%s</code>" % _e(value)


# the id shapes the page's raw-id check looks for (page/diagram/page_checks.py _RAW_PATTERNS)
_ID_SHAPES = [
    re.compile(r"\b[A-Z]{1,4}-[0-9]+\b"),
    re.compile(r"\b(?=[0-9a-fA-F]*[0-9])(?=[0-9a-fA-F]*[a-fA-F])[0-9a-fA-F]{8,}\b"),
    re.compile(r"\b[a-z0-9]+(?:_[a-z0-9]+)+\b"),
]


def _text(value):
    """Free text from the walk (origin, drop label, error): escaped, with each id-shaped word wrapped in <code>."""
    s = str(value if value is not None else "")
    return "".join(_c(w) if any(p.search(w) for p in _ID_SHAPES) else _e(w) for w in re.split(r"(\s+)", s))


def fmt(v):
    """A value as a reader sees it (simpage_v1.fmt): yes/no, none, lists and maps spelled out."""
    if isinstance(v, bool):
        return "yes" if v else "no"
    if v is None:
        return "none"
    if isinstance(v, list):
        return ", ".join(fmt(x) for x in v) if v else "none"
    if isinstance(v, dict):
        return "; ".join("%s: %s" % (k, fmt(x)) for k, x in v.items()) if v else "none"
    return str(v)


def case_anchor(case_id):
    return "sim-case-" + (re.sub(r"[^A-Za-z0-9_-]+", "-", str(case_id)).strip("-") or "x")


def _pill(state):
    cls = STATE_CLASS.get(state, "k-drop")
    return '<span class="pill %s">%s</span>' % (cls, _e(STATE_WORDS.get(state, state)))


def _values(case):
    rows = []
    for v in case.get("values", []):
        mark = "" if v.get("match") else ' <span class="sim-miss">differs</span>'
        rows.append("<tr><td><code>%s</code></td><td><code>%s</code></td><td><code>%s</code>%s</td></tr>"
                    % (_e(v["token"]), _e(fmt(v.get("expected"))), _e(fmt(v.get("actual"))), mark))
    if not rows:
        return '<div><p class="sim-sub">No values were produced for this case.</p></div>'
    return ('<div class="sim-wrap"><table><tr><th>Value</th><th>Expected</th><th>Walked</th></tr>%s</table></div>'
            % "".join(rows))


def _interfaces(case):
    rows = ["<tr><td><code>%s</code></td><td><code>%s</code></td><td><code>%s</code></td><td><code>%s</code></td></tr>"
            % (_e(i["part"]), _e(i["boundary_id"]), _e(fmt(i.get("input"))), _e(fmt(i.get("output"))))
            for i in case.get("interfaces", [])]
    if not rows:
        return '<div><p class="sim-sub">No interface was reached in this case.</p></div>'
    return ('<div class="sim-wrap"><table><tr><th>Part</th><th>Interface</th><th>In</th><th>Out</th></tr>%s</table></div>'
            % "".join(rows))


def step_html(step):
    """One walked step (simpage_v1.step_html, ported to U20a's step rows): the rule, whether its test held, the values
    read with their origin and the values written."""
    head = ['<li class="sim-step"><div class="sim-stephead">']
    if step.get("rule_id"):
        head.append("<span>Rule <code>%s</code></span>" % _e(step["rule_id"]))
    if "test_held" in step:
        head.append('<span class="sim-held">%s</span>' % ("holds" if step["test_held"] else "does not hold"))
    where = [_c(step["part"])] if step.get("part") else []
    if step.get("item_key"):
        where.append("item " + _c(step["item_key"]))
    if where:
        head.append('<span class="sim-sub">%s</span>' % ", ".join(where))
    head.append("</div>")
    det = []
    reads = step.get("reads") or []
    if reads:
        det.append("<li><b>Read:</b> " + "; ".join(
            "<code>%s</code> <code>%s</code> (%s)" % (_e(r.get("token")), _e(fmt(r.get("value"))), _text(r.get("origin", "")))
            for r in reads) + "</li>")
    writes = step.get("writes") or {}
    if writes:
        det.append("<li><b>Wrote:</b> " + "; ".join(
            "<code>%s</code> <code>%s</code>" % (_e(k), _e(fmt(v))) for k, v in writes.items()) + "</li>")
    if det:
        head.append('<ul class="sim-vals">%s</ul>' % "".join(det))
    head.append("</li>")
    return "".join(head)


def _steps(case):
    steps = case.get("steps") or []
    if not steps:
        return ""
    return '<ol class="sim-steps">%s</ol>' % "".join(step_html(s) for s in steps)


def case_html(case):
    state = case["state"]
    out = ['<section class="sim-case" id="%s"><h5>%s %s</h5>' % (case_anchor(case["case_id"]), _c(case["case_id"]),
                                                                _pill(state))]
    if state in DROPPED:
        out.append('<p class="sim-drop"><b>Dropped from the walk:</b> %s</p>' % _text(case.get("drop_label", "")))
    out.append('<p class="sim-outcome">Expected outcome: %s. Walked outcome: %s.</p>'
               % (_c(case.get("outcome_expected")), _c(fmt(case.get("outcome_actual")))))
    if case.get("reason"):
        out.append('<p class="sim-sub">%s</p>' % _text(case["reason"]))
    out.append('<p class="sim-open">Open this case from the table above to see its steps, values and interfaces.</p>')
    out.append('<div class="sim-detail">%s%s%s</div></section>' % (_steps(case), _values(case), _interfaces(case)))
    return "".join(out)


def _case_parts(case):
    """[(part, interface)] for one case: a hand-over wherever the next step's part differs (both sides listed), else
    the part's own steps."""
    parts = [s.get("part") or NO_PART for s in case.get("steps") or []]
    keys, crossed = [], set()
    for a, b in zip(parts, parts[1:]):
        if a != b:
            iface = "%s->%s" % (a, b)
            crossed.update((a, b))
            keys += [(a, iface), (b, iface)]
    keys += [(p, INSIDE) for p in parts if p not in crossed]
    out = []
    for k in keys:
        if k not in out:
            out.append(k)
    return out


def by_part_and_interface(cases):
    """{(part, interface): [case ids]} in first-seen order."""
    groups = {}
    for c in cases:
        for key in _case_parts(c):
            ids = groups.setdefault(key, [])
            if c["case_id"] not in ids:
                ids.append(c["case_id"])
    return groups


def _grouping(cases):
    groups = by_part_and_interface(cases)
    if not groups:
        return '<h4>Cases by part and interface</h4><p class="sim-sub">No walked step recorded a part.</p>'
    rows = ['<tr><td>%s</td><td>%s</td><td>%s</td></tr>'
            % (_c(part), _e(iface) if iface == INSIDE else _c(iface),
               ", ".join('<a href="#%s">%s</a>' % (case_anchor(i), _c(i)) for i in ids))
            for (part, iface), ids in groups.items()]
    return ('<h4>Cases by part and interface</h4><div class="sim-wrap"><table>'
            '<tr><th>Part</th><th>Interface</th><th>Cases</th></tr>%s</table></div>' % "".join(rows))


def _read_json(run_dir, rel):
    try:
        with open(os.path.join(run_dir, rel), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _left_out(run_dir):
    """Cases the converter could not put into the walk, with each reason; a stamped exclusion names what is missing."""
    sim = _read_json(run_dir, SIM_INPUT) or {}
    entries = sim.get("unconvertible") or []
    if not entries:
        return ""
    suite = _read_json(run_dir, SUITE) or {}
    by_id = {c.get("id"): c for c in suite.get("cases") or [] if isinstance(c, dict)}
    reasons = {}
    for e in entries:
        why = e.get("reason") or ""
        if e.get("item"):
            why = "%s (%s)" % (why, e["item"])
        reasons.setdefault(e.get("case_id"), []).append(why)
    rows = []
    for cid, whys in reasons.items():
        case = by_id.get(cid) or {}
        missing = (case.get("exclusion") or {}).get("missing")
        if missing:
            whys = whys + ["missing: %s" % missing]
        rows.append("<tr><td>%s</td><td>%s</td><td>%s</td></tr>"
                    % (_c(cid), "critical" if case.get("critical") is True else "", _text("; ".join(whys))))
    return ('<h4>Cases left out of the walk</h4><p class="sim-sub">These cases were not walked and are not guessed; '
            'each stays open.</p><div class="sim-wrap"><table><tr><th>Case</th><th>Critical</th><th>Why</th></tr>'
            '%s</table></div>' % "".join(rows))


def _wrap(body):
    return '<style>%s</style>\n<div id="simulation">\n%s\n</div>' % (CSS.strip(), body)


def _plural(n, one, many):
    return one if n == 1 else many


def _summary(run_dir):
    """The page's one computed summary (page/build.py summary()): the walk's counts come from there."""
    spec = importlib.util.spec_from_file_location("ad_page_build_for_simulation", BUILD)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.summary(run_dir)


def render(run_dir, summary=None):
    path = os.path.join(run_dir, WALK)
    if not os.path.isfile(path):
        return '<p class="pp-gap">%s</p>' % _e(GAP)
    with open(path, encoding="utf-8") as f:
        walk = json.load(f)
    cases = walk["cases"]
    s = summary if summary is not None else _summary(run_dir)
    # Count the rows we actually display, even when a caller supplies a stale summary.
    n = len(cases)
    counts = {state: sum(c["state"] == state for c in cases) for state in STATE_WORDS}
    states = dict(counts, dropped=sum(counts[state] for state in DROPPED))
    bar = ('<div class="sim-bar"><b>Simulation</b><span>%d %s in walk</span><span>%d match</span><span>%d differ</span>'
           '<span>%d dropped</span></div>' % (n, _plural(n, "case", "cases"), states["matches"], states["differs"],
                                             states["dropped"]))
    parts = [bar]
    in_walk = {c["case_id"] for c in cases}
    sim = _read_json(run_dir, SIM_INPUT) or {}
    left_out_ids = {c.get("case_id") for c in sim.get("unconvertible") or []}
    additional = len(left_out_ids - in_walk)
    if additional:
        parts.append('<p class="sim-sub">Total cases: %d; %d in the walk; '
                     '%d additional cases left out of the walk.</p>' % (n + additional, n, additional))
    compared = counts["matches"] + counts["differs"]
    parts.append('<p class="sim-sub">%d compared; %d could not run; %d not run; '
                 '%d need more information.</p>' % (
                     compared, counts["execution_error"], counts["not_executed"],
                     counts["needs_more_information"]))
    if not cases:
        parts.append('<p class="sim-drop">No cases were executed. No simulation result is available.</p>')
    elif not compared:
        parts.append('<p class="sim-drop">No cases completed comparison. This simulation has no successful result.</p>')
    elif states["dropped"] or additional:
        parts.append('<p class="sim-drop">Partial simulation: some cases have no completed comparison.</p>')
    if walk.get("error"):
        parts.append('<p class="sim-drop"><b>The walk stopped:</b> %s</p>' % _text(walk["error"]))
    reached = s.get("walk_rules")
    if reached:
        parts.append('<p class="sim-sub">The walk reached %d of %d %s.</p>' % (
            reached["reached"], reached["total"], _plural(reached["total"], "decision rule", "decision rules")))
    parts.append('<h4>Cases at a glance</h4><div class="sim-wrap"><table>'
                 '<tr><th>Case</th><th>Result</th><th>Expected outcome</th><th>Walked outcome</th></tr>')
    for c in cases:
        parts.append('<tr><td><a href="#%s">%s</a></td><td>%s</td><td>%s</td><td>%s</td></tr>'
                     % (case_anchor(c["case_id"]), _c(c["case_id"]), _pill(c["state"]), _c(c.get("outcome_expected")),
                        _c(fmt(c.get("outcome_actual")))))
    parts.append('</table></div>')
    parts.append(_grouping(cases))
    left_out = _left_out(run_dir)
    if left_out:
        parts.append(left_out)
    parts.append('<h4>Cases one by one</h4>')
    parts.extend(case_html(c) for c in cases)
    return _wrap("\n".join(parts))
