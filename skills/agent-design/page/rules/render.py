"""Decision-rules section of the Agent Design page (the ratified rules-page spec), as one HTML fragment.

`render(run_dir) -> str` fills the `rules-page` slot. The fragment carries no script: rows are native <details>,
filtering is radio inputs plus CSS :has(), and every row stays visible where that CSS is not supported. Every count
and ordinal on the page is computed here from the run's files; the fixed strings of this module hold no digits.
Standard library only; runs on the system Python.
"""
import html
import hashlib
from pathlib import Path
import importlib.util
import json
import os
import re
import sys

# Every file the page reads, relative to the run folder.
SOURCES = {
    "intake": "stages/intake/intake.json",
    "agent": "stages/architecture/agent.yaml",  # JSON syntax, read with json.load (pins F5)
    "rules": "stages/rules/rules.json",
    "outcomes": "stages/rules/outcomes.json",
    "placed": "stages/placement/placed-rules.json",
    "page_prep": "stages/page-prep/page-prep.json",
    "register": "stages/register/register.json",
}
OPTIONAL = ("outcomes", "page_prep", "register")
CSS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rules.css")

NMI = "needs_more_information"
EVIDENCE = ["Observed", "Assumed", "Contradictory", NMI]
EV_LABEL = {"Observed": "Observed", "Assumed": "Assumed", "Contradictory": "Contradictory",
            NMI: "needs more information"}
EV_DEF = {"Observed": "Seen in the captured work or a written procedure.",
          "Assumed": "A design choice that waits for ratification.",
          "Contradictory": "The evidence disagrees; the row says how.",
          NMI: "We do not know yet; the rule hands over."}
HEX_ID = re.compile(r"\b[a-f\d]{12}\b")  # the bare process id shape
NOT_PRODUCED = "This part was not produced in this run."
NOT_YET = "Not reached yet: the open questions are written later in the run."


def _register_pending(run_dir):
    """True while the run's register stage has not finished (state.json says so): its section is pending then, not
    missing. With no readable state.json the section reads as not produced, as before."""
    try:
        with open(os.path.join(run_dir, "state.json"), "rb") as f:   # json detects the encoding of bytes
            entry = (json.load(f).get("stages") or {}).get("register") or {}
        return not str(entry.get("status", "pending")).startswith(("done", "failed"))
    except (OSError, ValueError, AttributeError):
        return False
OUTCOME_MIN, OUTCOME_MAX = 2, 6


def placed_rule_list(doc):
    """placed-rules.json as a list of placed rules (a list, `placed_rules` or `rules`): scripts/expr.py's one reader."""
    mod = sys.modules.get("expr")
    if not hasattr(mod, "placed_rule_list"):
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                            "scripts", "expr.py")
        spec = importlib.util.spec_from_file_location("expr", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    return mod.placed_rule_list(doc)


# The register's item fields (stages/register/register.json) -> the names this page reads.
REGISTER_KEYS = {"unknown": "what", "why_it_matters": "why", "who_can_answer": "who", "agent_meanwhile": "agent_today",
                 "dependent_rules": "rules"}


def _register_state():
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "scripts", "register_state.py")
    spec = importlib.util.spec_from_file_location("ad_register_state", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_closed = _register_state().closed_by_snapshot


def register_item(item):
    """A register item with the page's names filled from the register's own fields where the page's are absent."""
    out = dict(item)
    for src, dst in REGISTER_KEYS.items():
        if out.get(dst) in (None, "", []) and item.get(src) not in (None, "", []):
            out[dst] = item[src]
    return out


class SourceMissing(Exception):
    """A required source file is absent or not JSON."""


def esc(text):
    return html.escape("" if text is None else str(text), quote=True)


def slug(text):
    return re.sub(r"[^a-z\d]+", "-", str(text).lower()).strip("-") or "x"


def heading(level, text, attrs=""):
    return "<h%d%s>%s</h%d>" % (level, attrs, text, level)


def read_json(run_dir, rel):
    path = os.path.join(run_dir, rel)
    if not os.path.isfile(path):
        return None
    with open(path, "rb") as f:  # json detects the Unicode encoding of bytes
        return json.loads(f.read())


def contained_source(run_dir, path):
    """Optional snapshot reads are confined to the run, including symlink resolution."""
    try:
        root = Path(run_dir).resolve()
        target = (root / path).resolve()
        relative = target.relative_to(root).as_posix()
        with target.open("rb") as stream:
            return json.load(stream), relative
    except (OSError, ValueError, TypeError):
        return None, None


def source_associations(run_dir, intake, rows, names):
    """Join explicit locators only; identity never changes a rule's evidence state."""
    snapshot, snapshot_rel = contained_source(run_dir, (intake.get("observations") or {}).get("snapshot_intake"))
    observations, policies, seen = {}, {}, set()
    def policy_names(value, pointer=""):
        if isinstance(value, dict):
            for key, child in value.items():
                ptr = pointer + "/" + str(key).replace("~", "~%s" % 0).replace("/", "~%s" % 1)
                if key in ("name", "title") and "/policies/" in ptr and isinstance(child, str):
                    yield child, ptr
                yield from policy_names(child, ptr)
        elif isinstance(value, list):
            for index, child in enumerate(value):
                yield from policy_names(child, pointer + "/" + str(index))
    for source in (snapshot or {}).get("sources", []) if isinstance(snapshot, dict) else []:
        if not isinstance(source, dict) or not isinstance(source.get("path"), str):
            continue
        pid = source.get("process_id")
        doc, rel = contained_source(run_dir, str(Path(snapshot_rel).parent / source["path"]))
        if doc is None or (pid, rel) in seen:
            continue
        seen.add((pid, rel))
        if source.get("collection_kind") == "observations":
            items = doc.get("items", []) if isinstance(doc, dict) else doc
            for index, item in enumerate(items if isinstance(items, list) else []):
                if not isinstance(item, dict) or not item.get("resource_key"):
                    continue
                pointer = str(pid) + ":observation:" + str(item["resource_key"])
                title = item.get("title") or item.get("name")
                timestamp = item.get("timestamp")
                process = names.get(pid)
                if process in ("the process",) or (process or "").startswith("process "):
                    process = None
                label = title or "Captured session — %s — %s" % (process or "Process name not recorded", timestamp or "Time not recorded")
                observations.setdefault((pid, pointer), []).append({"label": label, "file": rel,
                    "json_pointer": ("/items/" if isinstance(doc, dict) else "/") + str(index),
                    "pointer": pointer, "timestamp": timestamp, "descriptive": not bool(title)})
        elif "process-details" in str(source.get("id", "")):
            for title, pointer in policy_names(doc):
                policies.setdefault((pid, title), []).append({"label": title, "file": rel, "json_pointer": pointer})
    catalog = {}
    for row in rows:
        row["source_origin"] = "stages/rules/rules.json#/rules/" + str(row["source_index"])
        row["sources"] = []
        raw = row["rule"]
        titles = re.findall(r"policy ['\"]([^'\"]+)['\"]", raw.get("prose", ""), re.I)
        pointers = list(dict.fromkeys(x for obj in (raw, row["placed"], row["prep"] or {}) for x in obj.get("observation_ids", []) or []))
        requests = [("policy", title, policies.get((row["pid"], title), [])) for title in titles]
        requests += [("observation", pointer, observations.get((row["pid"], pointer), [])) for pointer in pointers]
        for kind, identity, matches in requests:
            if len(matches) != 1:
                row["sources"].append({"label": identity, "unavailable": True, "kind": kind})
                continue
            source = dict(matches[0], kind=kind)
            key = json.dumps([row["pid"], kind, source["file"], source["json_pointer"]])
            source["anchor"] = "rule-source-" + hashlib.sha256(key.encode()).hexdigest()
            catalog[source["anchor"]] = source
            row["sources"].append(source)
        # Keep unparseable attribution in authored wording without inventing a target.
        row["source_prose"] = raw.get("prose") if re.search(r"\bpolicy\b", raw.get("prose", ""), re.I) else None
    return list(catalog.values())


def source_list(m):
    out = []
    for source in m.get("source_catalog", []):
        out.append('<li id="%s" data-source-pointer="%s"><b>%s</b>%s<p>Source: %s · %s</p>%s</li>' % (
            source["anchor"], esc(source.get("pointer", "")), esc(source["label"]),
            " (descriptive label)" if source.get("descriptive") else "",
            esc(source["file"]), esc(source["json_pointer"]),
            '<p>Recorded time: %s</p>' % esc(source["timestamp"]) if source.get("timestamp") else ""))
    return '<details class="rule-sources"><summary>Sources associated with rules</summary><ul>%s</ul></details>' % "".join(out) if out else ""


def accepted_state(rule_state, placed_state):
    """Show placement's accepted, more cautious reading; never promote certainty for display."""
    certainty = {"Observed": 2, "Assumed": 1, "Contradictory": 1, "needs_more_information": 0}
    if placed_state in certainty and rule_state in certainty and certainty[placed_state] < certainty[rule_state]:
        return placed_state
    return rule_state or ""


def declared_outcome(rule, placement, outcomes):
    """Read only the decision's declared output; matching text in other fields is not an outcome."""
    decision = placement.get("decision") or rule.get("decision")
    declarations = [d for d in outcomes.get("decisions") or [] if d.get("id") == decision]
    declaration = declarations[0] if len(declarations) == 1 else {}
    output = declaration.get("output") or declaration.get("output_token") or "outcome"
    if isinstance(output, dict):
        output = output.get("token") or output.get("name")
    if not isinstance(output, str):
        return None
    value = ((rule.get("expression") or {}).get("sets") or {}).get(output)
    if not isinstance(value, dict) or value.get("kind", "literal") != "literal":
        return None
    return value.get("value")


def load(run_dir, prep_hook=None):
    """Read every source and join them into one model. Raises SourceMissing for an absent required file.

    `prep_hook`, when given, wraps the page-prep data before anything reads it (the checker uses it to record every
    page-prep field the page is built from)."""
    data = {}
    for key, rel in SOURCES.items():
        try:
            data[key] = read_json(run_dir, rel)
        except ValueError as exc:
            raise SourceMissing("%s is not JSON: %s" % (rel, exc))
        if data[key] is None and key not in OPTIONAL:
            raise SourceMissing("%s is missing" % rel)
    intake, agent = data["intake"], data["agent"]
    prep = data["page_prep"] or {}
    if prep_hook is not None:
        prep = prep_hook(prep)
    pids = [p["process_id"] for p in intake["processes"]]
    graphs, graph_files = {}, {}
    for p in intake["processes"]:
        path = os.path.realpath(os.path.join(run_dir, p["future_state"]))
        if path not in graph_files:
            graph_files[path] = {"doc": read_json(run_dir, p["future_state"]) or {}, "pids": []}
        graph_files[path]["pids"].append(p["process_id"])
        graphs[p["process_id"]] = graph_files[path]["doc"]
    steps, step_by_ref, aliases = [], {}, []
    for source in graph_files.values():
        members = source["pids"]
        for n in (source["doc"].get("graph") or {}).get("nodes") or []:
            pid = n.get("process_id") or (members[0] if len(members) == 1 else "set")
            ref = "%s:%s" % (pid, n["id"])
            if ref in step_by_ref:
                continue
            step = {"pid": pid, "id": n["id"], "ref": ref, "title": n.get("title") or n["id"],
                    "lane": n.get("lane") or "", "anchor": "step-%s-%s" % (slug(pid), slug(n["id"]))}
            steps.append(step)
            step_by_ref[ref] = step
            for span in (n.get("future") or {}).get("spans") or []:
                if span in pids:
                    aliases.append(("%s:%s" % (span, n["id"]), step))
    for ref, step in aliases:
        step_by_ref.setdefault(ref, step)
    parts = agent["parts"]
    owner_of = {}
    for part in parts:
        for ref in part.get("owns_steps") or []:
            canonical = (step_by_ref.get(ref) or {}).get("ref", ref)
            owner_of.setdefault(canonical, part["id"])
    titles = prep.get("parts") or {}
    part_title = {p["id"]: titles.get(p["id"]) or p.get("title") or p.get("name") or p["id"].replace("-", " ").replace("_", " ").capitalize() for p in parts}
    rules_by_id = {}
    for r in data["rules"]["rules"]:
        rules_by_id.setdefault(r["id"], r)  # a duplicate id keeps its first entry, as page/build.py summary() does
    placed = placed_rule_list(data["placed"]) or []
    items = None if data["register"] is None else [register_item(i) for i in data["register"].get("items") or []
                                                   if not _closed(i)]
    waits = {}
    for it in items or []:
        for rid in it.get("rules") or []:
            waits.setdefault(rid, []).append(it)
    prep_rules = prep.get("rules") or {}
    order = {p["id"]: i for i, p in enumerate(parts)}
    step_order = {s["ref"]: i for i, s in enumerate(steps)}
    placements = {}
    for p in placed:
        placements.setdefault(p["rule_id"], []).append(p)
    rows = []
    for rid in dict.fromkeys(list(rules_by_id) + list(placements)):
        rule = rules_by_id.get(rid) or {}
        locations = placements.get(rid, [])
        p = locations[0] if locations else {}
        pid = p.get("process_id") or rule.get("process_id") or ""
        ref = "%s:%s" % (pid, p.get("future_state_step", ""))
        step = step_by_ref.get(ref)
        if step:
            ref = step["ref"]
        prepared = prep_rules.get(rid)
        gaps = []
        if rid not in rules_by_id:
            gaps.append("source_missing")
        if not locations:
            gaps.append("placement_missing")
        elif not step:
            gaps.append("step_missing")
        if not owner_of.get(ref):
            gaps.append("owner_missing")
        if not prepared:
            gaps.append("prep_missing")
        if not p.get("mechanism"):
            gaps.append("mechanism_missing")
        ev = rule.get("evidence_state")
        for location in locations:
            ev = accepted_state(ev, location.get("evidence_state"))
        rows.append({
            "source_index": next((i for i, source in enumerate(data["rules"]["rules"]) if source is rule), None),
            "id": rid, "rule": rule, "placed": p, "placements": locations, "gaps": gaps,
            "pid": pid, "ref": ref, "step": step, "owner": owner_of.get(ref),
            "decision": p.get("decision") or rule.get("decision") or "",
            "outcome": declared_outcome(rule, p, data["outcomes"] or {}),
            "ev": ev or "", "prep": prepared,
            "obs": list(p.get("observation_ids") or rule.get("observation_ids") or []), "mech": p.get("mechanism"),
            "special": bool(p.get("special_case")), "waits": waits.get(rid, []),
        })
    rows.sort(key=lambda r: (r["special"], order.get(r["owner"], len(order)), step_order.get(r["ref"], len(steps))))
    names = process_names(run_dir, intake, graphs, pids)
    catalog = source_associations(run_dir, intake, rows, names)
    return {"source_catalog": catalog, "data": data, "intake": intake, "agent": agent, "prep": prep, "has_prep": data["page_prep"] is not None,
            "pids": pids, "graphs": graphs, "steps": steps, "step_by_ref": step_by_ref, "parts": parts,
            "part_title": part_title, "owner_of": owner_of, "rows": rows, "items": items, "multi": len(pids) > 1,
            "inventory": {"source_ids": list(rules_by_id), "represented_ids": [r["id"] for r in rows],
                          "source_count": len(rules_by_id), "represented_count": len(rows),
                          "placement_only_ids": [rid for rid in placements if rid not in rules_by_id],
                          "unplaced_ids": [rid for rid in rules_by_id if rid not in placements]},
            "process_name": names}


def process_names(run_dir, intake, graphs, pids):
    """Each process's name: its set-map title when the run has a set map (a shared set file's own `process.name`
    names the whole set), else its future state's `process.name`, else its id."""
    titles = {}
    if intake.get("set_map"):
        try:
            set_map = read_json(run_dir, intake["set_map"]) or {}
        except ValueError:
            set_map = {}
        listed = set_map.get("processes") if isinstance(set_map, dict) else None
        for p in listed or []:
            if isinstance(p, dict) and p.get("process_id") in pids and p.get("title"):
                titles[p["process_id"]] = p["title"]
    out = {}
    for n, pid in enumerate(pids, 1):
        named = graphs[pid].get("process") or {}
        name = titles.get(pid) or named.get("name")
        if not isinstance(name, str) or not name.strip() or name.strip() == pid or HEX_ID.search(name):
            name = "process %d" % n if len(pids) > 1 else "the process"  # never the bare process id
        out[pid] = name
    return out


def terminal_outcomes(m):
    """Distinct outcome labels the agent can end on, in the order outcomes.json first lists them."""
    out = []
    for d in ((m["data"]["outcomes"] or {}).get("decisions") or []):
        for label in d.get("labels") or []:
            if label not in out:
                out.append(label)
    return out


def outcome_label(m, label):
    o = (m["prep"].get("outcomes") or {}).get(label) or {}
    return o.get("label") or str(label).replace("_", " ")


def decision_name(m, dec):
    """The decision as a reader sees it. Placement may name a decision by its Future state step ref
    (`<process>:<step>`); the page then shows that step's plain name. Anchors and filters keep the raw name."""
    step = m["step_by_ref"].get(dec)
    if step:
        return step["title"]
    for decision in (m["data"]["outcomes"] or {}).get("decisions") or []:
        if decision.get("id") == dec or decision.get("decision") == dec:
            return decision.get("title") or decision.get("name") or dec
    return dec


def fill(text, values):
    class Safe(dict):
        def __missing__(self, key):
            return "{" + key + "}"
    return str(text).format_map(Safe(values))


def count(n, filt, bd="", href="", label=""):
    attrs = ' class="count" data-count="%d" data-f="%s"' % (n, esc(filt))
    if bd:
        attrs += ' data-bd="%s"' % esc(bd)
    inner = '<span class="num">%d</span>%s' % (n, label)
    if href:
        return '<a href="#%s"%s>%s</a>' % (esc(href), attrs, inner)
    return "<span%s>%s</span>" % (attrs, inner)


def pl(n, one, many):
    """The word for n things: singular for exactly one, plural otherwise."""
    return one if n == 1 else many


def matches(row, **f):
    for k, v in f.items():
        if k == "owner" and row["owner"] != v:
            return False
        if k == "ev" and row["ev"] != v:
            return False
        if k == "outcome" and row["outcome"] != v:
            return False
        if k == "runs" and row["runs"] != v:
            return False
        if k == "wait" and not row["waits"]:
            return False
        if k == "proc" and row["pid"] != v:
            return False
        if k == "dec" and slug(row["decision"]) != v:
            return False
        if k == "step" and (row["step"] or {}).get("anchor") != v:
            return False
    return True


def ftext(f):
    return "&".join("%s=%s" % kv for kv in sorted(f.items())) or "all"


def n_of(m, **f):
    """A breakdown count from the page summary (page/build.py summary(), over the placed rows de-duplicated)."""
    s = m.get("summary")
    if s and "count" in s:
        return s["count"](**f)
    return sum(1 for r in m["rows"] if matches(r, **f))


def page_summary(run_dir, rows=None):
    """The page's one computed summary (page/build.py summary()), given the placed rows and the filter test so it
    also gives every breakdown, or None when it cannot be loaded; no count on the rules page is computed twice."""
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "build.py")
    try:
        spec = importlib.util.spec_from_file_location("ad_page_build_for_rules", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.summary(run_dir, rows=rows, match=matches)
    except Exception:  # noqa: BLE001 - the rules page still renders from its own rows
        return None


def s_count(m, key, fallback, ev=None):
    """A total from the page summary; the row count when the summary is absent."""
    s = m.get("summary")
    if not s:
        return fallback
    return s["rules_shown"].get(ev, fallback) if ev else s.get(key, fallback)


def summary(m):
    rows, parts = m["rows"], m["parts"]
    owners = [p["id"] for p in parts if any(r["owner"] == p["id"] for r in rows)]
    nsteps = len({r["ref"] for r in rows})
    if m.get("summary") and "distinct" in m["summary"]:
        nsteps = m["summary"]["distinct"]("ref")
    total = s_count(m, "rules", len(rows))
    nowners = s_count(m, "owners", len(owners))
    out = ['<section class="rp-summary" id="rp-summary">', heading(2, "Summary")]
    nprocs = s_count(m, "processes", len(m["pids"]))
    if m["multi"]:
        lead = "%s %s across %s %s, %s %s and %s %s; %s apply across processes." % (
            count(total, "all", href="rp-rules"), pl(total, "rule", "rules"), '<span class="n">%d</span>' % nowners,
            pl(nowners, "sub-agent", "sub-agents"), '<span class="n">%d</span>' % nprocs,
            pl(nprocs, "process", "processes"), '<span class="n">%d</span>' % nsteps, pl(nsteps, "step", "steps"),
            count(n_of(m, proc="set"), "cross"))
    else:
        lead = "%s %s across %s %s and %s %s." % (
            count(total, "all", href="rp-rules"), pl(total, "rule", "rules"), '<span class="n">%d</span>' % nowners,
            pl(nowners, "sub-agent", "sub-agents"), '<span class="n">%d</span>' % nsteps, pl(nsteps, "step", "steps"))
    out.append('<p class="lead">%s</p>' % lead)
    shown = {ev: s_count(m, None, n_of(m, ev=ev), ev=ev) for ev in EVIDENCE}
    values = {"total": total, "sessions": s_count(m, "sessions", len({o for r in rows for o in r["obs"]})),
              "waiting": n_of(m, wait=True), "observed": shown["Observed"], "assumed": shown["Assumed"],
              "contradictory": shown["Contradictory"], "needs_more_information": shown[NMI]}
    if m["prep"].get("interpretive") or m["prep"].get("interpretive_paragraph"):
        out.append(heading(3, "Reading the numbers"))
        out.append('<p class="interpretive">%s</p>' % esc(fill(m["prep"].get("interpretive") or m["prep"]["interpretive_paragraph"], values)))
    if m["prep"].get("boundary"):
        out.append(heading(3, "The boundary that holds for every rule"))
        out.append('<p class="boundary">%s</p>' % esc(fill(m["prep"]["boundary"], values)))
    out.append('<div class="tiles">')
    for ev in EVIDENCE:
        out.append('<label class="tile" for="fe-%s">%s<span class="t">%s</span><span class="d">%s</span></label>' % (
            slug(ev), count(shown[ev], ftext({"ev": ev}), bd="ev"), EV_LABEL[ev], EV_DEF[ev]))
    out.append("</div>")
    recorded = (m.get("summary") or {}).get("rules_shown", {}).get("recorded", 0)
    if recorded:
        out.append('<p class="quiet"><span class="n">%d</span> %s recorded as seen in the work, but this run has no '
                   'observations behind them yet.</p>' % (recorded, "rule is" if recorded == 1 else "rules are"))
    # The three short facts are one list, so the summary never runs into a wall of paragraphs.
    code, model = n_of(m, runs="code"), n_of(m, runs="model")
    out.append('<ul class="quiet"><li>%s %s as fixed logic and %s %s on the agent\'s judgment.</li>' % (
        count(code, ftext({"runs": "code"}), bd="runs"), pl(code, "rule runs", "rules run"),
        count(model, ftext({"runs": "model"}), bd="runs"), pl(model, "rests", "rest")))
    out.append('<li><a href="#rp-open">%s %s on something we still need to know</a>.</li>'
               % (count(values["waiting"], ftext({"wait": "yes"})), pl(values["waiting"], "rule waits", "rules wait")))
    seen = s_count(m, "observed_sessions", len({o for r in rows if r["ev"] == "Observed" for o in r["obs"]}))
    out.append('<li>Observed rules rest on <span class="n">%d</span> captured %s.</li></ul>'
               % (seen, pl(seen, "session", "sessions")))
    out.append('<table class="bd">%s<thead><tr><th>Sub-agent</th>%s<th>Total</th></tr></thead><tbody>' % (
        "<caption>Rules by sub-agent and evidence state</caption>", "".join("<th>%s</th>" % EV_LABEL[e] for e in EVIDENCE)))
    for o in owners:
        cells = "".join("<td>%s</td>" % count(n_of(m, owner=o, ev=e), ftext({"owner": o, "ev": e})) for e in EVIDENCE)
        out.append('<tr><th><a href="#part-%s">%s</a></th>%s<td>%s</td></tr>' % (
            esc(slug(o)), esc(m["part_title"][o]), cells, count(n_of(m, owner=o), ftext({"owner": o}), bd="owner")))
    out.append("</tbody></table>")
    if m["multi"]:
        out.append('<table class="bd"><caption>Rules by process</caption><tbody>')
        for pid in m["pids"]:
            out.append('<tr><th>%s</th><td>%s</td></tr>' % (
                esc(m["process_name"][pid]), count(n_of(m, proc=pid), ftext({"proc": pid}), bd="proc")))
        out.append('<tr><th>Cross-process</th><td>%s</td></tr></tbody></table>' % count(n_of(m, proc="set"), "cross", bd="proc"))
    out.append("</section>")
    return "".join(out)


def process_section(m):
    rows = m["rows"]
    out = ['<section class="rp-process" id="rp-process">', heading(2, "How the work flows"), '<ol class="flow">']
    for s in m["steps"]:
        owner = m["owner_of"].get(s["ref"])
        here = [r for r in rows if r["ref"] == s["ref"]]
        tag = esc(m["part_title"][owner]) if owner else "People"
        links = []
        if owner and here:
            links.append('<a href="#part-%s">%s</a>' % (esc(slug(owner)), esc(m["part_title"][owner])))
        for dec in dict.fromkeys(r["decision"] for r in here if not r["special"]):
            links.append('<a href="#%s">%s</a>' % (esc(dec_anchor(s, dec)), esc(decision_name(m, dec))))
        # The lane, the step and the note are separated in the text itself, so no two of them run together.
        cross = "" if owner else ' · <span class="cross">People decide this step; the agent may not.</span>'
        ptag = ""
        if m["multi"]:
            # Shared orientation is not a distinct process in the flow inventory.
            process_name = m["process_name"].get(s["pid"])
            ptag = ' <span class="%s">%s</span>' % (
                "ptag" if process_name is not None else "shared-scope",
                esc(process_name if process_name is not None else "Across processes"))
        out.append('<li id="%s" class="step"><span class="lane">%s:</span>%s %s%s%s</li>' % (
            esc(s["anchor"]), tag, ptag, esc(s["title"]), cross,
            ('<div class="here">' + " · ".join(links) + "</div>") if links else ""))
    out.append("</ol></section>")
    return "".join(out)


def dec_anchor(step, dec):
    return "dec-%s-%s" % (step["anchor"][len("step-"):], slug(dec))


def outcome_table(m):
    labels = terminal_outcomes(m)
    if not (OUTCOME_MIN <= len(labels) <= OUTCOME_MAX):
        return ""
    prep = m["prep"].get("outcomes") or {}
    dimensions = [("when", "When it applies", "When it applies is not recorded."),
                  ("next", "What the reviewing person does next", "The next action is not recorded.")]
    dimensions = [(key, title, note) for key, title, note in dimensions
                  if any((prep.get(label) or {}).get(key) for label in labels)]
    out = ['<section class="rp-outcomes" id="rp-outcomes">', heading(2, "What the agent can produce"),
           '<table class="oc"><thead><tr><th scope="col">Outcome</th>' +
           "".join('<th scope="col" id="outcome-col-%s">%s</th>' % (key, title)
                   for key, title, _ in dimensions) +
           '<th scope="col">Rules</th></tr></thead><tbody>']
    for index, label in enumerate(labels):
        o = prep.get(label) or {}
        first = next((r for r in m["rows"] if r["outcome"] == label and r["step"] and not r["special"]), None)
        href = dec_anchor(first["step"], first["decision"]) if first else ""
        out.append('<tr data-outcome="%s"><th scope="row" id="outcome-row-%s">%s</th>' % (
            esc(label), index, esc(outcome_label(m, label))))
        for key, _, _ in dimensions:
            value = o.get(key)
            out.append('<td headers="outcome-row-%s outcome-col-%s"%s>%s</td>' % (
                index, key, '' if value else ' aria-describedby="outcome-missing-%s"' % key,
                esc(value) if value else '<a href="#outcome-missing-%s">Not recorded</a>' % key))
        out.append('<td>%s</td></tr>' % count(n_of(m, outcome=label), ftext({"outcome": label}), href=href))
    out.append("</tbody></table>")
    for key, _, note in dimensions:
        missing = [(i, label) for i, label in enumerate(labels) if not (prep.get(label) or {}).get(key)]
        if missing:
            out.append('<p class="outcome-missing" id="outcome-missing-%s">%s Affected outcomes: %s.</p>' % (
                key, note, ", ".join('<a href="#outcome-row-%s">%s</a>' % (i, esc(outcome_label(m, label)))
                                    for i, label in missing)))
    out.append("</section>")
    return "".join(out)


def basis(m, r):
    ex = (r["prep"] or {}).get("example")
    if r["obs"]:
        total = sum((p.get("evidence_strength") or {}).get("observation_count", 0) for p in m["intake"]["processes"] if p["process_id"] == r["pid"])
        total = ((m.get("summary") or {}).get("process_observations") or {}).get(r["pid"], total)
        line = 'Basis: seen in <span class="n">%d</span> of <span class="n">%d</span> %s.' % (
            len(set(r["obs"])), total, pl(total, "session", "sessions"))
        if total < len(set(r["obs"])):
            line = 'Basis: cites <span class="n">%d</span> captured %s.' % (
                len(set(r["obs"])), pl(len(set(r["obs"])), "session", "sessions"))
        if ex:
            line += ' <span class="f-example">%s</span>' % esc(ex)
        return '<span class="f-basis">%s</span>' % line
    line = ("Evidence basis is not recorded in placement." if not r["rule"] or not r.get("placements", [r["placed"]])
            else "No captured observations are cited for this rule.")
    if ex:
        line += ' <span class="f-example">%s</span>' % esc(ex)
    return '<span class="f-basis">%s</span>' % line



def contradiction_sides(c):
    """Both sides of a contradiction: page-prep writes side_a and side_b (output.schema.json); the older a/b shape
    with `says` is still read."""
    if not isinstance(c, dict):
        return "", ""
    def side(new, old):
        v = c.get(new)
        if isinstance(v, str) and v.strip():
            return v
        v = c.get(old)
        return v.get("says") if isinstance(v, dict) and isinstance(v.get("says"), str) else (v if isinstance(v, str) else "")
    return tuple(v if v.strip() else "" for v in (side("side_a", "a"), side("side_b", "b")))


GAP_TEXT = {
    "source_missing": "The source rule is missing.",
    "placement_missing": "Placement is not recorded.",
    "step_missing": "The placed step is not available.",
    "owner_missing": "Ownership is not recorded.",
    "prep_missing": "Showing source wording; page wording is not recorded.",
    "mechanism_missing": "No mechanism is recorded.",
}


def shared_rule_notes(m):
    """Visible page-level facts, with explicit rule links and row descriptions.

    Group only identical wording in the same source field. Distinct authored
    reasons and examples remain attached to their own rules.
    """
    candidates = {}
    has_scenario = bool(m["rows"])
    has_why = any((r["prep"] or {}).get("why") or r["placed"].get("why") for r in m["rows"])
    for r in m["rows"]:
        prep = r["prep"] or {}
        values = {}  # Evidence counts and examples remain local to each rule.
        why = prep.get("why") or r["placed"].get("why")
        if has_why:
            values["why"] = '<b>Why</b> ' + esc(why or "The reason is not recorded.")
        if prep.get("why") and r["placed"].get("why") and prep["why"] != r["placed"]["why"]:
            values["placement-why"] = '<b>Placement rationale</b> ' + esc(r["placed"]["why"])
        if has_scenario:
            scenario = prep.get("scenario") or r["placed"].get("scenario")
            values["scenario"] = esc("needs more information" if scenario == NMI else scenario or "Scenario is not recorded.")
        for gap, wording in GAP_TEXT.items():
            if gap in r.get("gaps", []) and (gap != "mechanism_missing" or any(x["mech"] for x in m["rows"])):
                values[gap] = esc(wording)
        if any((x["prep"] or {}).get("boundary") for x in m["rows"]):
            values["boundary"] = '<b>Boundary</b> ' + esc(prep.get("boundary") or "Boundary is not recorded.")
        for field, value in values.items():
            candidates.setdefault((field, value), []).append(r["id"])
    m["shared_notes"] = {}
    out = []
    for (field, value), ids in candidates.items():
        if len(ids) < 2:
            continue
        anchor = "rule-note-%d" % (len(out) + 1)
        for rid in ids:
            m["shared_notes"].setdefault(rid, {})[field] = anchor
        links = ", ".join('<a href="#rule-%s">%s</a>' % (esc(slug(rid)), '<span data-rule-label="%s">Rule</span>' % esc(rid)) for rid in ids)
        out.append('<p class="shared-rule-note" id="%s" data-field="%s">%s <span class="note-rules">Applies to: %s.</span></p>' % (anchor, field, value, links))
    return '<div class="shared-rule-notes">%s</div>' % "".join(out) if out else ""


def mechanism_identity(r, peers=()):
    """A kind or ordinal alone does not identify a shared mechanism."""
    mech = r["mech"]
    scope = (r["owner"], r["pid"], r["ref"], r["decision"])
    for key in ("id", "mechanism_id"):
        if mech.get(key):
            return (mech.get("kind"), key, str(mech[key]), scope)
    order = mech.get("order")
    if isinstance(order, list) and order and all(isinstance(x, str) for x in order) and r["id"] in order and len(set(order)) == len(order):
        return (mech.get("kind"), "members", tuple(order), scope)
    # The schema/template encode a local sequence with integer ordinals, not
    # a mechanism id. Only a complete, unique 1..N sequence in one authored
    # owner/process/step/decision boundary is sufficient grouping evidence.
    boundary = lambda x: (x["owner"], x["pid"], x["ref"], x["decision"], x["mech"].get("kind"))
    candidates = [x for x in peers if x.get("mech") and boundary(x) == boundary(r)]
    orders = [x["mech"].get("order") for x in candidates]
    if (len(orders) > 1 and all(type(n) is int for n in orders)
            and sorted(orders) == list(range(1, len(orders) + 1))):
        return (mech.get("kind"), "ordinals", boundary(r))
    return (mech.get("kind"), "rule", r["id"])


def row_html(m, r, number, mech_n=None, grouped=False):
    prep = r["prep"] or {}
    shared = m.get("shared_notes", {}).get(r["id"], {})
    raw = not prep
    when = prep.get("when") or r["rule"].get("prose") or "The condition is not recorded."
    then = prep.get("then") or (outcome_label(m, r["outcome"]) if r["outcome"] is not None
                                               else "Outcome is not recorded.")
    scen = prep.get("scenario") or r["placed"].get("scenario") or "Scenario is not recorded."
    scen = "needs more information" if scen == NMI else scen
    step = r["step"]
    step_html = ('<a class="f-step" href="#%s">%s</a>' % (esc(step["anchor"]), esc(step["title"])) if step
                 else ('<span class="f-step">%s</span>' % esc(r["placed"]["future_state_step"])
                       if r["placed"].get("future_state_step") else ""))
    attrs = ' class="rule" id="rule-%s" data-rid="%s" data-owner="%s" data-ev="%s" data-outcome="%s" data-runs="%s" data-dec="%s" data-step="%s"' % (
        esc(slug(r["id"])), esc(r["id"]), esc(r["owner"] or ""), esc(r["ev"]), esc(r["outcome"] or ""), esc(r["runs"]),
        esc(slug(r["decision"])), esc((step or {}).get("anchor", "")))
    if shared:
        attrs += ' aria-describedby="%s"' % " ".join(dict.fromkeys(shared.values()))
    if m["multi"]:
        attrs += ' data-proc="%s"' % esc(r["pid"])
    if r["mech"]:
        attrs += ' data-mechanism-status="%s"' % esc(r["mech"].get("status") or "")
    if mech_n is not None:
        attrs += ' data-mech-step="%d"' % mech_n
    head = '<span class="rn">Rule %d</span>' % number
    if mech_n is not None:
        head += ' <span class="ms">Step %d</span>' % mech_n
    context = ['<span class="f-owner">%s</span>' % esc(m["part_title"][r["owner"]]) if r["owner"] else "",
               step_html, '<span class="f-decision">%s</span>' % esc(decision_name(m, r["decision"])) if r["decision"] else ""]
    parts = [head,
             ('<span class="f-scenario">%s</span>' % esc(scen)) if "scenario" not in shared else "",
             '<span class="f-when%s">When %s</span>' % (" raw" if raw else "", esc(when)),
             '<span class="f-then%s">Then %s</span>' % (" raw" if raw else "", esc(then)),
             '<span class="chip-ev">%s</span>' % EV_LABEL.get(r["ev"], esc(r["ev"])), "" if "basis" in shared else basis(m, r)]
    for gap, wording in GAP_TEXT.items():
        if (gap in r.get("gaps", []) and gap not in shared
                and (gap != "mechanism_missing" or any(x["mech"] for x in m["rows"]))):
            parts.append('<span class="f-gaps" data-gap="%s">%s</span>' % (gap, esc(wording)))
    if r["ev"] == NMI and prep.get("missing"):
        parts.append('<span class="f-missing">What is missing: %s</span>' % esc(prep["missing"]))
    c = prep.get("contradiction") or {}
    side_a, side_b = contradiction_sides(c)
    if r["ev"] == "Contradictory" or side_a or side_b:
        parts.append('<span class="dis">The disagreement: %s<span class="side">%s</span><span class="side">%s</span></span>' % (
                esc(c.get("what") + " " if isinstance(c, dict) and isinstance(c.get("what"), str) else ""), esc(side_a or "First side is not recorded."), esc(side_b or "Second side is not recorded.")))
    if m["multi"]:
        parts.append('<span class="ptag">%s</span>' % esc(m["process_name"].get(r["pid"], "Across processes" if r["pid"] == "set" else "Process not recorded")))
    more = []
    if r["ev"] != NMI and prep.get("missing"):
        more.append('<p class="f-missing">What is missing: %s</p>' % esc(prep["missing"]))
    for source in r.get("sources", []):
        if source.get("unavailable"):
            # a source with no resolvable plain name is left out of the person's page (RULES.md 7, north star 4:
            # no internal ids on customer pages; Souradip, 15 Oct); the pointer stays in the builder's rules record
            continue
        else:
            more.append('<p class="f-source">Associated source: <a href="#%s" data-source-pointer="%s">%s</a></p>' % (
                source["anchor"], esc(source.get("pointer", "")), esc(source["label"])))
    if r.get("source_prose"):
        more.append('<p class="f-source-prose">Authored source wording: %s <span class="source-origin">(%s/prose)</span></p>' % (esc(r["source_prose"]), esc(r["source_origin"])))
        if not any(s["kind"] == "policy" for s in r.get("sources", [])):
            more.append('<p class="f-source-unavailable">Source link unavailable: policy attribution could not be resolved.</p>')
    if grouped:
        more.append('<template class="rule-context">%s</template>' % " · ".join(v for v in context if v))
    else:
        parts[1:1] = [v for v in context if v]
    if "why" not in shared and any((x["prep"] or {}).get("why") or x["placed"].get("why") for x in m["rows"]):
        more.append('<p class="f-why"><b>Why</b> %s</p>' % esc(prep.get("why") or r["placed"].get("why") or "The reason is not recorded."))
    if r["mech"] and r["mech"].get("status"):
        more.append('<p class="f-mechanism-status">Mechanism: %s.</p>' % esc(r["mech"]["status"]))
    if "boundary" not in shared and any((x["prep"] or {}).get("boundary") for x in m["rows"]):
        more.append('<p class="f-boundary"><b>Boundary</b> %s</p>' % esc(prep.get("boundary") or "Boundary is not recorded."))
    if "placement-why" not in shared and r["placed"].get("why") and prep.get("why") and r["placed"]["why"] != prep["why"]:
        more.append('<p class="f-placement-why"><b>Placement rationale</b> %s</p>' % esc(r["placed"]["why"]))
    if len(r.get("placements", [])) > 1:
        more.append("<p>This rule has additional placements:</p><ul>")
        for location in r["placements"][1:]:
            ref = "%s:%s" % (location.get("process_id", ""), location.get("future_state_step", ""))
            step = m["step_by_ref"].get(ref) or {}
            details = [step.get("title") or "Step not available", location.get("scenario"), location.get("why")]
            if location.get("mechanism"):
                mechanism = location["mechanism"]
                label = {"cascade": "Sequence", "bands": "Range", "shared_destination": "Shared destination"}.get(
                    mechanism.get("kind"), "Mechanism recorded")
                position = mech_key(mechanism.get("order"), r["id"])
                details.append(label + (" · Step %d" % position if position else ""))
            more.append("<li>%s</li>" % " · ".join(esc(v) for v in details if v))
        more.append("</ul>")
    if r["waits"]:  # a list, so a rule waiting on several items is not a run of paragraphs
        more.append('<ul class="f-waits">%s</ul>' % "".join(
            '<li class="f-wait">Waiting on: <a href="#item-%s">%s</a></li>' % (esc(slug(it["id"])), esc(it.get("what")))
            for it in r["waits"]))
    return "<details%s><summary>%s</summary><section class=\"more\">%s</section></details>" % (
        attrs, " · ".join(p for p in parts if p), "".join(more))


def filters(m):
    """Radio groups for sub-agent, evidence state and (multi-process) process, with the CSS that filters rows."""
    owners = [p["id"] for p in m["parts"] if any(r["owner"] == p["id"] for r in m["rows"])]
    groups = [("fo", "owner", "Sub-agent", [(o, m["part_title"][o]) for o in owners]),
              ("fe", "ev", "Evidence", [(e, EV_LABEL[e]) for e in EVIDENCE])]
    if m["multi"]:
        groups.append(("fp", "proc", "Process", [(p, m["process_name"].get(p, "Across processes")) for p in dict.fromkeys(r["pid"] for r in m["rows"] if r["pid"])]))
    html_out, css = [], []
    for pre, attr, title, opts in groups:
        html_out.append('<fieldset class="flt"><legend>%s</legend>' % title)
        html_out.append('<input type="radio" name="%s" id="%s-all" checked><label for="%s-all">All</label>' % (pre, pre, pre))
        for val, label in opts:
            i = "%s-%s" % (pre, slug(val))
            html_out.append('<input type="radio" name="%s" id="%s"><label for="%s">%s</label>' % (pre, i, i, esc(label)))
            css.append('.rp:has(#%s:checked) details.rule:not([data-%s="%s"]){display:none}' % (i, attr, esc(val)))
        html_out.append("</fieldset>")
    return "".join(html_out), "".join(css)


def frame_of(m, part):
    """The part's frame in page-prep's plain words (frames, keyed by part id) where page-prep wrote one; the
    architecture's own input, output, reads and end point otherwise. The architecture text may name outcome labels
    and tokens; page-prep's frame is the checked plain-language version a reader sees."""
    frame = (m["prep"].get("frames") or {}).get(part["id"])
    return frame if isinstance(frame, dict) else part


def rules_section(m):
    rows = [r for r in m["rows"] if not r["special"]]
    out = ['<section class="rp-rules" id="rp-rules">', heading(2, "Rules")]
    out.append(shared_rule_notes(m))
    number = [0]

    def nxt():
        number[0] += 1
        return number[0]
    owners = [p for p in m["parts"] if any(r["owner"] == p["id"] and r["step"] for r in rows)]
    frame_keys = {"Input": "input", "Output": "output", "Reads": "surfaces_read", "Ends": "end_point"}
    recorded_frames = {p["id"]: frame_of(m, p) for p in owners}

    def frame_recorded(frame, key):
        # An authored empty list means no reads, not missing authority.
        return bool(frame.get(key)) or (key == "surfaces_read" and frame.get(key) == [])

    frame_notes = {p["id"]: [] for p in owners}
    for label, key in frame_keys.items():
        if not any(frame_recorded(f, key) for f in recorded_frames.values()):
            continue
        missing = [p for p in owners if not frame_recorded(recorded_frames[p["id"]], key)]
        if missing:
            note_id = "frame-missing-" + key
            out.append('<p class="frame-missing" id="%s">Not recorded: %s. Affected parts: %s.</p>' % (
                note_id, label.lower(), ", ".join('<a href="#part-%s">%s</a>' % (
                    esc(slug(p["id"])), esc(m["part_title"][p["id"]])) for p in missing)))
            for p in missing:
                frame_notes[p["id"]].append(note_id)
    for part in owners:
        prow = [r for r in rows if r["owner"] == part["id"] and r["step"]]
        first = next((m["step_by_ref"][s] for s in part.get("owns_steps") or [] if s in m["step_by_ref"]), None)
        out.append('<section class="part" data-group="owner" id="part-%s">' % esc(slug(part["id"])))
        out.append(heading(3, esc(m["part_title"][part["id"]])))
        frame = frame_of(m, part)
        fields = [("Input", frame.get("input")), ("Output", frame.get("output")),
                  ("Reads", "No reads recorded (authored empty list)." if frame.get("surfaces_read") == []
                   else ", ".join(frame.get("surfaces_read") or [])), ("Ends", frame.get("end_point"))]
        out.append('<div class="frame"%s>' % (
            ' aria-describedby="%s"' % " ".join(frame_notes[part["id"]]) if frame_notes[part["id"]] else ""))
        if first:
            out.append('<a class="sitsat" href="#%s">Where it sits in the process</a>' % esc(first["anchor"]))
        out.append('<dl class="frame-fields">%s</dl>' % "".join(
            "<dt>%s</dt><dd>%s</dd>" % (label, esc(value)) for label, value in fields if value))
        if frame.get("future_state_steps"):
            out.append('<p class="frame-position">%s</p>' % esc(" · ".join(frame["future_state_steps"])))
        out.append('</div>')
        decs = list(dict.fromkeys(r["decision"] for r in prow))
        labels = list(dict.fromkeys(r["outcome"] for r in prow if r["outcome"]))
        out.append('<table class="dxo"><thead><tr><th>Decision</th>%s</tr></thead><tbody>' % "".join(
            "<th>%s</th>" % esc(outcome_label(m, l)) for l in labels))
        for d in decs:
            out.append("<tr><th>%s</th>%s</tr>" % (esc(decision_name(m, d)), "".join(
                "<td>%s</td>" % count(n_of(m, owner=part["id"], dec=slug(d), outcome=l),
                                      ftext({"owner": part["id"], "dec": slug(d), "outcome": l})) for l in labels)))
        out.append("</tbody></table>")
        steps_here = [s for s in m["steps"] if any(r["ref"] == s["ref"] for r in prow)]
        for s in steps_here:
            srow = [r for r in prow if r["ref"] == s["ref"]]
            out.append('<section class="stepgrp" data-group="step">')
            out.append(heading(4, '<a href="#%s">%s</a>' % (esc(s["anchor"]), esc(s["title"]))))
            for d in dict.fromkeys(r["decision"] for r in srow):
                drow = [r for r in srow if r["decision"] == d]
                labs = list(dict.fromkeys(outcome_label(m, r["outcome"]) for r in drow if r["outcome"]))
                out.append('<section class="decgrp" data-group="dec" id="%s">' % esc(dec_anchor(s, d)))
                here = {"owner": part["id"], "dec": slug(d), "step": s["anchor"]}
                n_here = n_of(m, **here)
                out.append('<p class="q">%s <a class="tostep" href="#%s">On this step</a> · %s %s · Can end: %s</p>' % (
                    esc(decision_name(m, d)), esc(s["anchor"]), count(n_here, ftext(here)), pl(n_here, "rule", "rules"),
                    esc(", ".join(labs))))
                mechs = {}
                for r in drow:
                    if r["mech"]:
                        mechs.setdefault(mechanism_identity(r, drow), []).append(r)
                done = set()
                for r in drow:
                    if r["id"] in done:
                        continue
                    if r["mech"]:
                        kind = r["mech"]["kind"]
                        members = mechs[mechanism_identity(r, drow)]
                        known = [g for g in members if mech_key(g["mech"].get("order"), g["id"]) > 0]
                        group = sorted(known, key=lambda g: mech_key(g["mech"].get("order"), g["id"]))
                        group += [g for g in members if g not in known]
                        positions = [mech_key(g["mech"].get("order"), g["id"]) for g in group]
                        ordered = (len(group) > 1 and len(known) == len(group)
                                   and len(set(positions)) == len(positions))
                        if not ordered:
                            group = members
                        tag = "ol" if ordered else "ul"
                        label = {"cascade": "Sequence", "bands": "Range", "shared_destination": "Shared destination"}.get(kind, "Mechanism")
                        if len(group) == 1:
                            label = "Single-rule mechanism · " + ("Recorded position %d." % positions[0] if positions[0] > 0 else "Order is not recorded.")
                        else:
                            label += " · Rules in their recorded sequence" if ordered else " · Order is not recorded for all rules, or grouping is unresolved."
                        out.append('<div class="mech" data-mech="%s" data-order="%s"><p class="mk">%s</p><%s>' % (
                            esc(kind), "recorded" if ordered else "missing", esc(label), tag))
                        for g in group:
                            position = mech_key(g["mech"].get("order"), g["id"])
                            body = row_html(m, g, nxt(), position if position > 0 else None, grouped=True)
                            out.append('<li value="%d">%s</li>' % (position, body) if ordered else "<li>%s</li>" % body)
                            done.add(g["id"])
                        out.append("</%s></div>" % tag)
                    else:
                        out.append(row_html(m, r, nxt(), grouped=True))
                        done.add(r["id"])
                out.append("</section>")
            out.append("</section>")
        out.append("</section>")
    unresolved = [r for r in rows if not r["owner"] or not r["step"]]
    if unresolved:
        out.append('<section class="unplaced">' + heading(3, "Rules with unresolved placement"))
        out.extend(row_html(m, r, nxt()) for r in unresolved)
        out.append("</section>")
    out.append("</section>")
    m["numbered"] = number
    return "".join(out)


def mech_key(order, rule_id=None):
    """A mechanism row's position, from one: an int, a list starting with an int, or the placement prompt's list of
    rule ids in sequence (position = index of this rule's id plus one)."""
    if isinstance(order, list) and rule_id in order:
        return order.index(rule_id) + 1
    if isinstance(order, int):
        return order
    if isinstance(order, list) and order and isinstance(order[0], int):
        return order[0]
    try:
        return int(order)
    except (TypeError, ValueError):
        return 0


def guardrails(m):
    g = m["prep"].get("guardrails") or []
    t = m["prep"].get("thresholds") or []
    out = ['<section class="rp-guard" id="rp-guardrails">', heading(2, "Guardrails")]
    if not g and not t:
        out.append("<p>No guardrails were recorded in this run.</p>")
    else:
        out.append('<ul class="never">')
        for x in g:
            links = " ".join('<a href="#rule-%s">%s</a>' % (esc(slug(rid)), "Rule %d" % rule_number(m, rid))
                             for rid in x.get("rules") or [] if rule_number(m, rid))
            unknown = [rid for rid in x.get("rules") or [] if not rule_number(m, rid)]
            if unknown:
                links += ' <span class="guardrail-unresolved">Affected rule unavailable (%d unresolved reference%s).</span>' % (len(unknown), "s" if len(unknown) != 1 else "")
            if not x.get("rules"):
                links += ' <span class="guardrail-unresolved">Affected rules are not recorded.</span>'
            out.append("<li>%s <span class=\"why\">%s</span> %s</li>" % (esc(x.get("text") or "Guardrail text is not recorded."), esc(x.get("reason") or "Reason is not recorded."), links))
        out.append("</ul>")
        if t:
            out.append(heading(3, "Tunable thresholds") + "<ul>")
            for x in t:
                out.append("<li>%s: %s</li>" % (esc(x.get("text")), esc(x.get("basis") or EV_LABEL[NMI])))
            out.append("</ul>")
    out.append("</section>")
    return "".join(out)


def rule_number(m, rid):
    return m.get("numbers", {}).get(rid)


def special_section(m):
    rows = [r for r in m["rows"] if r["special"]]
    out = ['<section class="rp-special" id="rp-special">', heading(2, "Special cases")]
    if not rows:
        out.append("<p>No special cases in this run.</p>")
    for r in rows:
        m["numbered"][0] += 1
        out.append(row_html(m, r, m["numbered"][0]))
    out.append("</section>")
    return "".join(out)


def open_section(m):
    out = ['<section class="rp-open" id="rp-open">', heading(2, "What we still need to know")]
    if m["items"] is None:
        out.append("<p>%s</p></section>" % (NOT_YET if _register_pending(m.get("run_dir", "")) else NOT_PRODUCED))
        return "".join(out)
    main = [i for i in m["items"] if i.get("kind") != "access"]
    access = [i for i in m["items"] if i.get("kind") == "access"]

    behaviors = {}
    for i in m["items"]:
        if isinstance(i.get("agent_today"), str) and i["agent_today"]:
            behaviors.setdefault(i["agent_today"], []).append(i)
    behavior_refs = {}
    for text, items in behaviors.items():
        if len(items) < 2:
            continue
        anchor = "current-behavior-%d" % (len(behavior_refs) + 1)
        links = ['<a href="#item-%s">%s</a>' % (esc(slug(i["id"])), esc(i.get("what"))) for i in items]
        rule_ids = dict.fromkeys(rid for i in items for rid in i.get("rules") or [])
        links += ['<a href="#rule-%s">%s</a>' % (esc(slug(rid)), esc(rid)) for rid in rule_ids if rule_number(m, rid)]
        out.append('<div class="shared-current-behavior" id="%s"><p>What the agent does today: <span class="f-today">%s</span></p><p>Applies to: %s</p></div>' % (anchor, esc(text), " · ".join(links)))
        for i in items:
            behavior_refs[i["id"]] = anchor

    def item(i):
        links = " ".join('<a href="#rule-%s">%s</a>' % (esc(slug(rid)), "Rule %d" % rule_number(m, rid))
                         for rid in i.get("rules") or [] if rule_number(m, rid))
        today = ('<dt>What the agent does today</dt><dd class="f-today">%s</dd>' % esc(i["agent_today"])) if i.get("agent_today") else ""
        if i["id"] in behavior_refs:
            today = '<dt>Current behavior</dt><dd><a href="#%s">Shared current behavior</a></dd>' % behavior_refs[i["id"]]
        # The question, then its facts as a definition list (one paragraph per fact made a wall of text).
        return ('<div class="item" id="item-%s" data-oid="%s" data-kind="%s"><p class="w">%s</p><dl>'
                "<dt>Why it matters</dt><dd>%s</dd>%s<dt>Who can answer</dt><dd>%s</dd>"
                "<dt>Rules that depend on it</dt><dd>%s</dd></dl></div>") % (
            esc(slug(i["id"])), esc(i["id"]), esc(i.get("kind") or ""), esc(i.get("what")), esc(i.get("why")),
            today, esc(i.get("who")), links)
    if main:
        out.append(heading(3, "Questions for the customer"))
        out.extend(item(i) for i in main)
    if access:
        out.append(heading(3, "Access the agent needs before it can be deployed"))
        out.extend(item(i) for i in access)
    if not m["items"]:
        out.append("<p>Nothing is waiting in this run.</p>")
    out.append("</section>")
    return "".join(out)


def evidence_section(m):
    cited = {o for r in m["rows"] for o in r["obs"]}
    docs = []
    intake_rel = m["intake"]["observations"]["snapshot_intake"]
    snap, snapshot_rel = contained_source(m["run_dir"], intake_rel)
    snap = snap if isinstance(snap, dict) else {}
    for s in snap.get("sources") or []:
        if s.get("title") and s.get("collection_kind") != "observations":
            if isinstance(s["title"], str):
                _, safe_rel = contained_source(m["run_dir"], str(Path(snapshot_rel).parent / s.get("path", "")))
                docs.append(dict(s, path=safe_rel or "Source link unavailable"))
    total = s_count(m, "observations", sum((p.get("evidence_strength") or {}).get("observation_count", 0) for p in m["intake"]["processes"]))
    cites = s_count(m, "sessions", len(cited))
    out = ['<section class="rp-evidence" id="rp-evidence">', heading(2, "Evidence base"),
           '<p>The rules cite <span class="n">%d</span> captured %s out of <span class="n">%d</span> in the Work Brain.</p>'
           % (cites, pl(cites, "session", "sessions"), total)]
    if docs:
        out.append("<p>Written sources:</p><ul>%s</ul>" % "".join('<li data-source-id="%s" data-source-path="%s">%s</li>' % (esc(d.get("id") or ""), esc(d.get("path") or ""), esc(d["title"])) for d in docs))
    out.append("</section>")
    return "".join(out)


def render(run_dir, prep_hook=None):
    """Return the rules-page fragment for the run at `run_dir`."""
    m = load(run_dir, prep_hook)
    m["run_dir"] = run_dir
    by_id = {p["id"]: p for p in m["parts"]}
    for r in m["rows"]:
        part = by_id.get(r["owner"]) or {}
        r["runs"] = "model" if (part.get("routing") or {}).get("kind") == "model" else "code"
    m["summary"] = page_summary(run_dir, m["rows"])
    # Number rules in page order before any section links to them.
    m["numbered"] = [0]
    rules_html = rules_section(m)
    special_html = special_section(m)
    m["numbers"] = {}
    for mt in re.finditer(r'data-rid="([^"]*)"[^>]*>.*?<span class="rn">Rule (\d+)</span>', rules_html + special_html):
        m["numbers"][html.unescape(mt.group(1))] = int(mt.group(2))
    rules_html = re.sub(r'<span data-rule-label="([^"]+)">Rule</span>',
                        lambda match: "Rule %d" % rule_number(m, html.unescape(match.group(1))), rules_html)
    controls, css = filters(m)
    with open(CSS_FILE, "rb") as f:
        base_css = f.read().decode()
    contents = [("rp-summary", "Summary"), ("rp-process", "How the work flows")]
    table = outcome_table(m)
    if table:
        contents.append(("rp-outcomes", "What the agent can produce"))
    contents += [("rp-rules", "Rules"), ("rp-guardrails", "Guardrails"), ("rp-special", "Special cases"),
                 ("rp-open", "What we still need to know"), ("rp-evidence", "Evidence base")]
    toc = '<nav class="toc"><ol>%s</ol></nav>' % "".join('<li><a href="#%s">%s</a></li>' % c for c in contents)
    body = [summary(m), process_section(m), table, '<div class="controls">%s</div>' % controls, rules_html,
            guardrails(m), special_html, open_section(m), evidence_section(m), source_list(m)]
    return '<div class="rp" id="rules-page"><style>%s%s</style>%s%s</div>' % (base_css, css, toc, "".join(body))
