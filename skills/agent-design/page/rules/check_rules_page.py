#!/usr/bin/env python3
"""The acceptance checks of the 0425/0444 rules-page spec (section 6) on a rendered decision-rules page.

The spec's check 14 (cross-client names) is not run here: that scan is pre-ship, over the plugin only, never inside
a customer run. Cited observation ids are resolved by scripts/evidence_ids.py, the one resolver every check uses.

Usage: check_rules_page.py --run <run_dir> --page <html> [--out <report.json>]
Exit 0 pass, 1 fail, 2 usage or unreadable input. The report follows U1's check-report schema; each problem's
`defect` is `check-NN-<name>`. Every check runs and reports whatever an earlier check found.
Standard library only, Python 3.9.
"""
import argparse
import hashlib
import importlib.util
import json
import os
import re
import sys
from html.parser import HTMLParser

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", "..", "..", ".."))
CHECK_PATH = "skills/agent-design/page/rules/check_rules_page.py"
VOCAB = os.path.join(REPO, "skills", "agent-design", "formats", "lists", "vocabulary.json")
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
FILTER_KEYS = {"all", "cross", "owner", "ev", "outcome", "runs", "wait", "proc", "dec", "step"}


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# The one parser and resolver for observation ids (both `observation:<key>` and `<pid>:observation:<key>`).
evidence_ids = _load("ad_evidence_ids_for_rules_page",
                     os.path.join(REPO, "skills", "agent-design", "scripts", "evidence_ids.py"))


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


NO_SCRIPT = _knowledge("checks", "_no_script.py")
render = _load("u25_rules_render", os.path.join(HERE, "render.py"))


class Node:
    def __init__(self, tag, attrs, parent):
        self.tag, self.attrs, self.parent, self.children = tag, dict(attrs), parent, []
        self.attr_list = list(attrs)

    def cls(self):
        return (self.attrs.get("class") or "").split()

    def text(self):
        out = []
        for c in self.children:
            if isinstance(c, str):
                out.append(c)
            elif c.tag not in ("script", "style", "title", "template"):
                out.append(" " + c.text() + " ")
        return "".join(out)

    def reachable_text(self, stop=None):
        if not reachable(self, stop=stop):
            return ""
        return " ".join(c if isinstance(c, str) else c.reachable_text(stop=stop) for c in self.children
                        if not isinstance(c, Node) or c.tag not in ("script", "style", "title", "template"))

    def primary_text(self):
        # Provenance disclosures remain available as evidence, but are not primary prose.
        if self.tag in ("script", "style", "title", "template") or set(self.cls()) & {
                "f-source-unavailable", "f-source-prose", "rule-sources"}:
            return ""
        return " ".join(x if isinstance(x, str) else x.primary_text() for x in self.children)

    def walk(self):
        for c in self.children:
            if isinstance(c, Node):
                yield c
                yield from c.walk()

    def find(self, klass):
        return [n for n in self.walk() if klass in n.cls()]

    def ancestors(self):
        n = self.parent
        while n is not None:
            yield n
            n = n.parent


class Tree(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node("root", [], None)
        self.cur = self.root

    def handle_starttag(self, tag, attrs):
        n = Node(tag, attrs, self.cur)
        self.cur.children.append(n)
        if tag not in VOID:
            self.cur = n

    def handle_startendtag(self, tag, attrs):
        self.cur.children.append(Node(tag, attrs, self.cur))

    def handle_endtag(self, tag):
        n = self.cur
        while n is not None and n.tag != tag:
            n = n.parent
        if n is not None and n.parent is not None:
            self.cur = n.parent

    def handle_data(self, d):
        self.cur.children.append(d)


def read_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def read_text(path):
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read()


def rule_sha(rule):
    """SHA-256 of the rule's prose bytes, as the placement prompt defines `text_sha256` (pins I12)."""
    return hashlib.sha256(str(rule.get("prose") or "").encode("utf-8")).hexdigest()


def parse_f(s):
    f = {}
    for p in (s or "").split("&"):
        if "=" in p:
            k, v = p.split("=", 1)
            f[k] = v
        elif p:
            f[p] = ""
    return f


class Ctx:
    def __init__(self, run_dir, page_html):
        self.run_dir = run_dir
        self.html = page_html
        self.m = render.load(run_dir)
        tree = Tree()
        tree.feed(page_html)
        tree.close()
        self.root = tree.root
        self.nodes = list(self.root.walk())
        self.rows = [n for n in self.nodes if n.tag == "details" and "rule" in n.cls()]
        self.ids = {n.attrs["id"] for n in self.nodes if n.attrs.get("id")}
        self.visible = self.root.text()
        # The id-shaped scan reads the rules section only (pins I11): given the whole Agent Design
        # page, the other sections (appendix, simulation) are not this checker's to judge.
        # The fragment's own element first; the page builder's slot around it holds the builder's text too.
        scope = next((n for n in self.nodes if n.attrs.get("id") == "rules-page"),
                     next((n for n in self.nodes if n.attrs.get("data-slot") == "rules-page"), None))
        self.rules_visible = (self.root if scope is None else scope).primary_text()
        self.rules = self.m["data"]["rules"]["rules"]
        self.placed = render.placed_rule_list(self.m["data"]["placed"]) or []
        self.resolver = evidence_ids.Resolver(run_dir)

    def shared_text(self, row, klass):
        field = {"f-scenario": "scenario", "f-why": "why"}.get(klass)
        if not field:
            return ""
        refs = (row.attrs.get("aria-describedby") or "").split()
        notes = [n for n in self.nodes if n.attrs.get("id") in refs
                 and "shared-rule-note" in n.cls() and n.attrs.get("data-field") == field
                 and reachable(n) and any(a.tag == "a" and a.attrs.get("href") == "#" + row.attrs.get("id", "")
                                         for a in n.walk())]
        return " ".join(" ".join(c if isinstance(c, str) else c.reachable_text()
                                  for c in n.children if not isinstance(c, Node) or "note-rules" not in c.cls())
                        for n in notes).strip()

    def row_text(self, row, klass, summary_only=False):
        scope = row
        if summary_only:
            scope = next((c for c in row.children if isinstance(c, Node) and c.tag == "summary"), None)
            if scope is None:
                return ""
        text = " ".join(n.reachable_text(stop=row).strip() for n in scope.find(klass)
                        if reachable(n, stop=row)).strip()
        return text or self.shared_text(row, klass)

    def row_matches(self, row, f):
        a = row.attrs
        for k, v in f.items():
            if k == "all":
                continue
            if k == "cross":
                if len(row.find("ptag")) < 2:
                    return False
            elif k == "wait":
                if not row.find("f-wait"):
                    return False
            elif k == "dec":
                if a.get("data-dec") != v:
                    return False
            elif a.get("data-" + k) != v:
                return False
        return True


# The stage that owns the words each check judges, so a finding routes to one plain-words repair. Checks on the
# register's items belong to the register; everything else on the page is built from page-prep's words.
OWNER = {22: "register"}


def P(num, name, where, detail):
    return {"defect": "check-%02d-%s" % (num, name), "where": where, "detail": detail,
            "owner_stage": OWNER.get(num, "page-prep")}


# The customer's own period labels (M1, Q3, H2, FY24, and pairs such as M2/M3) are their words, not typed counts or ids.
PERIOD_LABEL = re.compile(r"\b(?:FY|[A-Z])\d{1,4}\b")


def check_01(c):
    """Each rule exactly once."""
    out = []
    rids = [r.attrs.get("data-rid") for r in c.rows]
    ids = [r["id"] for r in c.rules]
    for i in sorted({x for x in rids if rids.count(x) > 1}):
        out.append(P(1, "rule-once", "page", "rule %s is a row %d times" % (i, rids.count(i))))
    for i in ids:
        if i not in rids:
            out.append(P(1, "rule-once", "page", "rule %s has no row" % i))
    for i in sorted({x for x in rids if x not in ids}):
        out.append(P(1, "rule-once", "page", "row %s has no rule in rules.json" % i))
    return out


LEAD_COUNTS = [("rules", r"(\d+)\s+rules\b"), ("sub-agents", r"(\d+)\s+sub-agents\b"),
               ("processes", r"(\d+)\s+processes\b"), ("steps", r"(\d+)\s+steps\b"),
               ("cross-process rules", r"(\d+)\s+apply across processes\b")]


def _lead_actual(c):
    """What each lead count describes, counted from the page's own elements (rows count as rows, never deduplicated)."""
    flow = [n for n in c.nodes if n.tag == "li" and "step" in n.cls()]
    return {"rules": len(c.rows),
            "sub-agents": len({r.attrs.get("data-owner") for r in c.rows if r.attrs.get("data-owner")}),
            "processes": len({t.text().strip() for s in flow for t in s.find("ptag")}) if c.m["multi"] else None,
            "steps": len({r.attrs.get("data-step") for r in c.rows if r.attrs.get("data-step")}),
            "cross-process rules": sum(1 for r in c.rows if len(r.find("ptag")) >= 2)}


def _prose_counts(c):
    """The interpretive and boundary paragraphs must read as their page-prep text filled with counts taken from the
    page's own rows, so a stale or wrong figure in the narrative fails."""
    out = []
    obs = {p["rule_id"]: p.get("observation_ids") or [] for p in c.placed}
    values = {"total": len(c.rows),
              "sessions": len({o for r in c.rows for o in obs.get(r.attrs.get("data-rid"), [])}),
              "waiting": sum(1 for r in c.rows if c.row_matches(r, {"wait": "yes"}))}
    for ev in render.EVIDENCE:
        values[ev.lower()] = sum(1 for r in c.rows if c.row_matches(r, {"ev": ev}))
    for klass in ("interpretive", "boundary"):
        shown = [n for n in c.nodes if n.tag == "p" and klass in n.cls()]
        template = c.m["prep"].get(klass)
        if not template:
            continue
        want = re.sub(r"\s+", " ", render.fill(template, values)).strip()
        for n in shown:
            got = re.sub(r"\s+", " ", n.text()).strip()
            if got != want:
                out.append(P(2, "counts", "summary " + klass, "says %r, rows give %r" % (
                    " ".join(re.findall(r"\d+", got)), " ".join(re.findall(r"\d+", want)))))
    return out


def check_02(c):
    """Every summary count equals the number of rows it describes; breakdowns sum to the total."""
    out = []
    total = len(c.rows)
    leads = [n for n in c.nodes if n.tag == "p" and "lead" in n.cls()]
    if len(leads) != 1:
        out.append(P(2, "counts", "summary", "the summary has %d lead sentences, not one" % len(leads)))
    actual = _lead_actual(c)
    for lead in leads:
        text = re.sub(r"\s+", " ", lead.text())
        used = []
        for name, pat in LEAD_COUNTS:
            for mt in re.finditer(pat, text):
                used.append(mt.span(1))
                if actual[name] is None or int(mt.group(1)) != actual[name]:
                    out.append(P(2, "counts", "summary lead", "%s: says %s, page has %s" % (name, mt.group(1), actual[name])))
        for mt in re.finditer(r"\d+", text):
            if mt.span() not in used:
                out.append(P(2, "counts", "summary lead", "count %s describes nothing the check can count" % mt.group()))
    out += _prose_counts(c)
    sums = {}
    for n in c.nodes:
        if "data-count" not in n.attrs:
            continue
        f = parse_f(n.attrs.get("data-f"))
        want = sum(1 for r in c.rows if c.row_matches(r, f))
        shown = re.sub(r"\D", "", " ".join(x.text() for x in n.find("num")))
        if n.attrs.get("data-count") != str(want) or shown != str(want):
            out.append(P(2, "counts", n.attrs.get("data-f") or "", "says %s, shows %s, rows %d" % (
                n.attrs.get("data-count"), shown, want)))
        if n.attrs.get("data-bd"):
            try:
                sums[n.attrs["data-bd"]] = sums.get(n.attrs["data-bd"], 0) + int(n.attrs.get("data-count"))
            except ValueError:
                out.append(P(2, "counts", n.attrs["data-bd"], "count is not a number"))
    for bd, v in sorted(sums.items()):
        if v != total:
            out.append(P(2, "counts", bd, "breakdown sums to %d, total %d" % (v, total)))
    return out


def reachable(node, stop=None):
    """Static reachability; closed native details remain reachable. CSS needs a browser."""
    for n in [node] + list(node.ancestors()):
        if n is stop:
            break
        if n.tag == "template" or "hidden" in n.attrs or n.attrs.get("aria-hidden") == "true":
            return False
        if HIDE.search(n.attrs.get("style") or ""):
            return False
    return True


def group_context(row, kind):
    group = next((n for n in row.ancestors() if n.attrs.get("data-group") == kind), None)
    if group is None:
        return ""
    tag = {"owner": "h3", "step": "h4", "dec": "p"}[kind]
    return " ".join(n.text().strip() for n in group.children if isinstance(n, Node)
                    and n.tag == tag and (kind != "dec" or "q" in n.cls()) and reachable(n))


def check_03(c):
    """Required recorded fields, from the row or its owning visible group."""
    out = []
    model = {r["id"]: r for r in c.m["rows"]}
    for r in c.rows:
        src = model.get(r.attrs.get("data-rid"), {})
        for klass, label in (("f-owner", "owner"), ("f-step", "step"), ("f-decision", "decision"),
                             ("f-scenario", "scenario"), ("f-when", "When"), ("f-then", "Then"),
                             ("chip-ev", "evidence chip")):
            t = c.row_text(r, klass, summary_only=True)
            if label in ("owner", "step", "decision"):
                if not src.get(label):
                    continue
                t = t or group_context(r, {"owner": "owner", "step": "step", "decision": "dec"}[label])
            if label == "scenario" and not ((src.get("prep") or {}).get("scenario") or (src.get("placed") or {}).get("scenario")):
                continue
            if klass in ("f-when", "f-then"):
                t = re.sub(r"^(When|Then)\b", "", t).strip()
            if not t:
                out.append(P(3, "required-fields", r.attrs.get("data-rid", "?"), "%s missing" % label))
    return out


def check_04(c):
    """Each rule's step is a Future state Workflow node inside its set's anchor steps."""
    out = []
    owned = {s for p in c.m["parts"] for s in p.get("owns_steps") or []}
    for p in c.placed:
        g = (c.m["graphs"].get(p["process_id"]) or {}).get("graph") or {}
        nodes = {n["id"] for n in g.get("nodes") or []}
        anchors = set(g["anchor_steps"]) if "anchor_steps" in g else owned
        ref = "%s:%s" % (p["process_id"], p["future_state_step"])
        if p["future_state_step"] not in nodes:
            out.append(P(4, "valid-step", p["rule_id"], "step is not a Future state Workflow node"))
        elif ref not in anchors:
            out.append(P(4, "valid-step", p["rule_id"], "step is outside the anchor steps"))
    # The step each row shows: its link must resolve to a node inside the anchor steps and agree with the placement.
    by_anchor = {s["anchor"]: s for s in c.m["steps"]}
    placed = {p["rule_id"]: p for p in c.placed}
    for r in c.rows:
        rid = r.attrs.get("data-rid", "?")
        links = [a for a in r.find("f-step") if a.tag == "a"]
        if len(links) != 1:
            out.append(P(4, "valid-step", rid, "row shows %d step links, not one" % len(links)))
            continue
        step = by_anchor.get((links[0].attrs.get("href") or "")[1:])
        if step is None:
            out.append(P(4, "valid-step", rid, "row's step link resolves to no Future state Workflow node"))
            continue
        g = (c.m["graphs"].get(step["pid"]) or {}).get("graph") or {}
        anchors = set(g["anchor_steps"]) if "anchor_steps" in g else owned
        if step["ref"] not in anchors:
            out.append(P(4, "valid-step", rid, "row's step is outside the anchor steps"))
        if links[0].text().strip() != step["title"]:
            out.append(P(4, "valid-step", rid, "row's step name differs from the node it links to"))
        p = placed.get(rid)
        if p is not None and (p["process_id"], p["future_state_step"]) != (step["pid"], step["id"]):
            out.append(P(4, "valid-step", rid, "row's step differs from the rule's placement"))
    # The step each step group is headed by: same tests, and every row in the group shows that step.
    for n in c.nodes:
        if n.attrs.get("data-group") != "step":
            continue
        heads = [a for h in n.children if isinstance(h, Node) and h.tag in ("h3", "h4", "h5")
                 for a in h.walk() if a.tag == "a"]
        if len(heads) != 1:
            out.append(P(4, "valid-step", "step group", "step group heading has %d step links, not one" % len(heads)))
            continue
        href = (heads[0].attrs.get("href") or "")[1:]
        step = by_anchor.get(href)
        if step is None:
            out.append(P(4, "valid-step", href, "step group heading resolves to no Future state Workflow node"))
            continue
        g = (c.m["graphs"].get(step["pid"]) or {}).get("graph") or {}
        anchors = set(g["anchor_steps"]) if "anchor_steps" in g else owned
        if step["ref"] not in anchors:
            out.append(P(4, "valid-step", href, "step group heading is outside the anchor steps"))
        if heads[0].text().strip() != step["title"]:
            out.append(P(4, "valid-step", href, "step group heading names a different step from its link"))
        for r in (x for x in n.walk() if x.tag == "details" and "rule" in x.cls()):
            if [a.attrs.get("href") for a in r.find("f-step") if a.tag == "a"] != ["#" + href]:
                out.append(P(4, "valid-step", r.attrs.get("data-rid", "?"), "row sits under a different step heading"))
    return out


def check_05(c):
    """Every decision group holds a rule; more than eight decisions per owner is a warning."""
    out = []
    for n in c.nodes:
        if n.attrs.get("data-group") == "dec" and not [x for x in n.walk() if x.tag == "details" and "rule" in x.cls()]:
            out.append(P(5, "decisions-group", n.attrs.get("id", "?"), "decision group holds no rule"))
    for p in c.placed:
        if not (p.get("decision") or "").strip():
            out.append(P(5, "decisions-group", p["rule_id"], "no decision name"))
    per = {}
    for r in c.m["rows"]:
        per.setdefault(r["owner"], set()).add(r["decision"])
    c.warnings = ["%s has %d decision names" % (o, len(v)) for o, v in per.items() if len(v) > 8]
    return out


def check_06(c):
    """Multi-process: every rule names a process of the design, and every row shows its process tag."""
    if not c.m["multi"]:
        return []
    out = []
    names = set(c.m["process_name"].values())
    for p in c.placed:
        if p.get("process_id") not in c.m["pids"]:
            out.append(P(6, "process-stated", p["rule_id"], "process is not in the design"))
    for r in c.rows:
        tags = [t.text().strip() for t in r.find("ptag")]
        if not tags:
            out.append(P(6, "process-stated", r.attrs.get("data-rid", "?"), "row shows no process tag"))
        for t in tags:
            if t not in names:
                out.append(P(6, "process-stated", r.attrs.get("data-rid", "?"), "process tag names no process"))
    return out


def check_07(c):
    """Observed rules have a basis; every cited observation is an individual observation in the snapshot."""
    out = []
    placed = {p["rule_id"]: p for p in c.placed}
    for rule in c.rules:
        cited = list(rule.get("observation_ids") or []) + list((placed.get(rule["id"]) or {}).get("observation_ids") or [])
        valid = []
        for o in cited:
            if c.resolver.resolve(o)[0]:
                valid.append(o)
            else:
                out.append(P(7, "observed-basis", rule["id"], "cited observation is not in the frozen snapshot"))
        if rule.get("evidence_state") == "Observed" and not valid:
            out.append(P(7, "observed-basis", rule["id"], "Observed rule cites no observation"))
    return out


def check_08(c):
    """Waiting-on stamps resolve to What we still need to know, and every item links back to a rule."""
    out = []
    items = {n.attrs.get("id"): n for n in c.nodes if n.attrs.get("data-oid")}
    rule_ids = {r.attrs.get("id") for r in c.rows}
    for r in c.rows:
        for w in r.find("f-wait"):
            for a in (x for x in w.walk() if x.tag == "a"):
                if (a.attrs.get("href") or "")[1:] not in items:
                    out.append(P(8, "gaps-both-ways", r.attrs.get("data-rid", "?"), "waiting-on stamp has no item"))
    for iid, n in items.items():
        links = [a for a in n.walk() if a.tag == "a" and (a.attrs.get("href") or "")[1:] in rule_ids]
        if not links:
            out.append(P(8, "gaps-both-ways", n.attrs.get("data-oid"), "item links to no rule"))
    return out


def check_09(c):
    """Why present on every rule."""
    out = []
    for r in c.rows:
        t = re.sub(r"^Why\b", "", c.row_text(r, "f-why")).strip()
        if not t:
            out.append(P(9, "why-present", r.attrs.get("data-rid", "?"), "no Why"))
    return out


ID_PATTERNS = [("hex run", r"\b(?=[0-9a-f]*\d)(?=[0-9a-f]*[a-f])[0-9a-f]{8,}\b"),
               ("prefix:suffix token", r"\b[A-Za-z][A-Za-z0-9-]*:[A-Za-z0-9][A-Za-z0-9_-]*\b"),
               ("snake_case name", r"\b[a-z0-9]+_[a-z0-9_]+\b"),
               ("rule-id pattern", r"\b[A-Z]{1,6}(?:-[A-Z]{1,6})*-[A-Z]*\d+[A-Za-z0-9]*\b")]


def check_10(c):
    """No id-shaped primary text."""
    out = []
    for label, pat in ID_PATTERNS:
        for hit in sorted(set(re.findall(pat, c.rules_visible))):
            out.append(P(10, "id-shaped-text", "visible text", "%s: %s" % (label, hit)))
    known = [r["id"] for r in c.rules] + [i.get("id") for i in (c.m["items"] or []) if i.get("id")]
    for i in sorted(set(known)):
        if re.search(r"(?<![\w.-])" + re.escape(i) + r"(?![\w-])", c.rules_visible):
            out.append(P(10, "id-shaped-text", "visible text", "internal id shown: %s" % i))
    return out


def check_11(c):
    """No retired or banned terms; chips use the four labels."""
    out = []
    vocab = read_json(VOCAB)
    for entry in (vocab.get("banned") or []) + (vocab.get("retired") or []):
        if re.search(entry["pattern"], c.visible, re.I):
            out.append(P(11, "vocabulary", "visible text", "banned or retired term: %s" % entry["name"]))
    allowed = set(render.EV_LABEL.values())
    for n in c.nodes:
        if "chip-ev" in n.cls() and n.text().strip() not in allowed:
            out.append(P(11, "vocabulary", "chip", "chip label outside the four"))
    return out


def check_12(c):
    """Every anchor and count link resolves."""
    out = []
    for n in c.nodes:
        href = n.attrs.get("href") or ""
        if n.tag == "a" and href.startswith("#") and href[1:] not in c.ids:
            out.append(P(12, "links-work", href, "anchor does not resolve"))
        if "data-count" in n.attrs:
            bad = [k for k in parse_f(n.attrs.get("data-f")) if k not in FILTER_KEYS]
            if bad:
                out.append(P(12, "links-work", n.attrs.get("data-f"), "count filter key unknown"))
    return out


def check_13(c):
    """A single-process design renders without process tags, filter or breakdown."""
    if c.m["multi"]:
        return []
    out = []
    if any("ptag" in n.cls() for n in c.nodes):
        out.append(P(13, "single-no-process-tags", "page", "process tag on a single-process page"))
    if any("data-proc" in n.attrs for n in c.nodes) or any((n.attrs.get("id") or "").startswith("fp-") for n in c.nodes):
        out.append(P(13, "single-no-process-tags", "page", "process filter or data on a single-process page"))
    if "Rules by process" in c.visible:
        out.append(P(13, "single-no-process-tags", "page", "process breakdown on a single-process page"))
    return out


def check_15(c):
    """Mechanisms are whole: each rule once, step order 1..k, and the place pass changed no rule."""
    out = []
    by_id = {r["id"]: r for r in c.rules}
    groups = {}
    owner_of = c.m["owner_of"]
    for p in c.placed:
        mech = p.get("mechanism")
        ref = "%s:%s" % (p["process_id"], p["future_state_step"])
        if mech:
            groups.setdefault((owner_of.get(ref), ref, p.get("decision"), mech.get("kind")), []).append(p)
        rule = by_id.get(p["rule_id"])
        if rule is None or rule_sha(rule) != p.get("text_sha256"):
            out.append(P(15, "mechanism-whole", p["rule_id"], "placed rule does not match the written rule"))
    for key, ps in groups.items():
        orders = sorted(render.mech_key(p["mechanism"].get("order"), p["rule_id"]) for p in ps)
        if orders != list(range(1, len(ps) + 1)):
            out.append(P(15, "mechanism-whole", str(key[3]), "mechanism step order is %s" % orders))
    page_seen = []
    for n in c.nodes:
        if n.attrs.get("data-mech") is None:
            continue
        mrows = [x for x in n.walk() if x.tag == "details" and "rule" in x.cls()]
        steps = []
        for x in mrows:
            page_seen.append(x.attrs.get("data-rid"))
            shown = re.sub(r"\D", "", " ".join(s.text() for s in x.find("ms")))
            steps.append(x.attrs.get("data-mech-step"))
            if shown != x.attrs.get("data-mech-step"):
                out.append(P(15, "mechanism-whole", x.attrs.get("data-rid", "?"), "shown step differs from its order"))
        if steps != [str(k) for k in range(1, len(steps) + 1)]:
            out.append(P(15, "mechanism-whole", n.attrs.get("data-mech"), "page step order is %s" % steps))
    want = sorted(p["rule_id"] for ps in groups.values() for p in ps)
    if sorted(x for x in page_seen if x) != want:
        out.append(P(15, "mechanism-whole", "page", "mechanism rules on the page differ from the placed mechanisms"))
    return out


def check_16(c):
    """No script element and no inline event handler."""
    out = []
    for n in c.nodes:
        if n.tag == "script":
            out.append(P(16, "no-script", "page", "script element"))
        for k, _v in n.attr_list:
            if k.lower().startswith("on"):
                out.append(P(16, "no-script", n.tag, "inline event handler %s" % k))
    if os.path.isfile(NO_SCRIPT):
        ns = _load("u25_no_script", NO_SCRIPT)
        for line, kind, detail in ns.problems("<!doctype html><html><head><title>Rules</title></head><body>%s</body></html>" % c.html):
            out.append(P(16, "no-script", "line %d" % line, "%s: %s" % (kind, detail)))
    else:
        out.append(P(16, "no-script", "knowledge/checks/_no_script.py", "no-script checker missing"))
    return out


HIDE = re.compile(r"display\s*:\s*none|visibility\s*:\s*(hidden|collapse)|content-visibility\s*:\s*hidden|"
                  r"opacity\s*:\s*0(\.0*)?\s*(;|$)|height\s*:\s*0|font-size\s*:\s*0", re.I)


def check_17(c):
    """With the filtering CSS removed, every rule row is visible."""
    out = []
    for r in c.rows:
        for n in [r] + list(r.ancestors()):
            if "hidden" in n.attrs or n.attrs.get("aria-hidden") == "true":
                out.append(P(17, "filters-degrade", r.attrs.get("data-rid", "?"), "hidden by a %s attribute" % n.tag))
            if HIDE.search(n.attrs.get("style") or ""):
                out.append(P(17, "filters-degrade", r.attrs.get("data-rid", "?"), "hidden by an inline style on %s" % n.tag))
            if n is not r and n.tag == "details" and "open" not in n.attrs:
                out.append(P(17, "filters-degrade", r.attrs.get("data-rid", "?"), "inside a closed details element"))
    return out


class _Seen(dict):
    """Page-prep data that records every string the renderer reads from it, with the path it was read at."""

    def __init__(self, data, path, log):
        super().__init__(data)
        self._path, self._log = path, log

    def _wrap(self, key, value):
        return _seen(value, self._path + (key,), self._log)

    def __getitem__(self, key):
        return self._wrap(key, dict.__getitem__(self, key))

    def get(self, key, default=None):
        return self._wrap(key, dict.__getitem__(self, key)) if key in self else default

    def values(self):
        return [self[k] for k in self.keys()]

    def items(self):
        return [(k, self[k]) for k in self.keys()]


def _seen(value, path, log):
    if isinstance(value, dict):
        return _Seen(value, path, log)
    if isinstance(value, list):
        return [_seen(v, path + (i,), log) for i, v in enumerate(value)]
    if isinstance(value, str):
        log.append((path, value))
    return value


def _prep_prose(c):
    """Every page-prep string the renderer reads while building this page, except examples (spec section 1: an
    example carries its figures) and rule ids listed as references (the page shows those as rule numbers)."""
    log = []
    render.render(c.run_dir, prep_hook=lambda prep: _seen(prep, (), log))
    rule_ids = {r["id"] for r in c.rules}
    seen = set()
    for path, text in log:
        keys = [k for k in path if isinstance(k, str)]
        if (keys and keys[-1] == "example") or (len(keys) >= 2 and keys[-1] == "rules" and text in rule_ids) or (
                keys and keys[-1] == "observation_ids"):
            continue
        where = " ".join(str(k) for k in path)
        if (where, text) not in seen:
            seen.add((where, text))
            yield where, text


def check_18(c):
    """No typed digits in page-prep prose outside renderer placeholders (examples carry their figures)."""
    out = []
    for where, text in _prep_prose(c):
        if re.search(r"\d", PERIOD_LABEL.sub("", re.sub(r"\{[A-Za-z_]+\}", "", text))):
            out.append(P(18, "no-typed-digits", where, "a digit is typed into page-prep prose"))
    return out


def check_19(c):
    """Sub-agent sections and decision groups link to a step anchor; every sub-agent states its position."""
    out = []
    steps = {s["anchor"] for s in c.m["steps"]}
    refs = set(c.m["step_by_ref"])
    for n in c.nodes:
        if n.attrs.get("data-group") == "owner":
            if not [a for a in n.find("sitsat") if (a.attrs.get("href") or "")[1:] in steps]:
                out.append(P(19, "tied-to-future-state", n.attrs.get("id", "?"), "sub-agent section links to no step"))
        if n.attrs.get("data-group") == "dec":
            if not [a for a in n.find("tostep") if (a.attrs.get("href") or "")[1:] in steps]:
                out.append(P(19, "tied-to-future-state", n.attrs.get("id", "?"), "decision group links to no step"))
    for p in c.m["parts"]:
        if p.get("role") == "sub-agent" and not [s for s in p.get("owns_steps") or [] if s in refs]:
            out.append(P(19, "tied-to-future-state", p["id"], "sub-agent has no future-state position"))
    return out


def check_20(c):
    """Every rule with cited observations shows an example."""
    out = []
    cited = {p["rule_id"] for p in c.placed if p.get("observation_ids")}
    for r in c.rows:
        if r.attrs.get("data-rid") in cited and not c.row_text(r, "f-example"):
            out.append(P(20, "example-present", r.attrs.get("data-rid"), "no example"))
    return out


def check_21(c):
    """Every needs-more-information row shows What is missing while collapsed."""
    out = []
    for r in c.rows:
        if r.attrs.get("data-ev") == render.NMI and not c.row_text(r, "f-missing", summary_only=True).startswith("What is missing"):
            out.append(P(21, "missing-present", r.attrs.get("data-rid", "?"), "no What is missing"))
    return out


def check_22(c):
    """Every proposed or Assumed rule has a ratification item; every item says what the agent does today."""
    out = []
    rat = [n for n in c.nodes if n.attrs.get("data-oid") and n.attrs.get("data-kind") == "ratification"]
    linked = {(a.attrs.get("href") or "")[1:] for n in rat for a in n.walk() if a.tag == "a"}
    for r in c.m["rows"]:
        if r["ev"] == "Assumed" or (r["mech"] or {}).get("status") == "proposed":
            if "rule-" + render.slug(r["id"]) not in linked:
                out.append(P(22, "ratification-listed", r["id"], "needs ratification but no item lists it"))
    # an item whose current behavior is shared with other items links to that one shared statement (RULES.md 25's
    # grouping; R2a renders it once as .shared-current-behavior) and still says what the agent does today through it
    shared = {n.attrs.get("id"): n for n in c.nodes if "shared-current-behavior" in n.cls()}
    for n in c.nodes:
        if n.attrs.get("data-oid"):
            t = " ".join(x.text() for x in n.find("f-today"))
            for a in n.walk():
                href = a.attrs.get("href") or ""
                target = shared.get(href[1:]) if a.tag == "a" and href.startswith("#") else None
                if target is not None:
                    t += " " + " ".join(x.text() for x in target.find("f-today"))
            if not re.sub(r"^\s*What the agent does today:", "", t).strip():
                out.append(P(22, "ratification-listed", n.attrs["data-oid"], "item does not say what the agent does today"))
    return out


def content_diagnostics(c):
    """Source-associated evidence, separate from the preserved 21-check API.

    Exact text retention tests preservation, never whether the author's claim is true.
    Repeated prose is a review prompt, never permission to rewrite the source.
    """
    out = []
    def finding(kind, rid, source, output, detail):
        out.append({"defect": "rules-" + kind, "where": rid, "detail": detail,
                    "source_evidence": source, "output_evidence": output,
                    "owner_stage": "page-prep", "disposition": "review-and-continue"})
    rows = {r.attrs.get("data-rid"): r for r in c.rows}
    repetitions = {}
    for src in c.m["rows"]:
        rid = src["id"]
        row = rows.get(rid)
        prep, placed = src.get("prep") or {}, src.get("placed") or {}
        if row is None:
            finding("source-row-missing", rid, src.get("rule"), None, "Source rule has no associated row; catalog mentions cannot substitute.")
            continue
        fields = [("scenario", prep.get("scenario") or placed.get("scenario"), "f-scenario"),
                  ("why", prep.get("why") or placed.get("why"), "f-why"),
                  ("missing", prep.get("missing"), "f-missing")]
        sides = render.contradiction_sides(prep.get("contradiction") or {})
        fields += [("contradiction-side-%d" % i, value, "side") for i, value in enumerate(sides, 1)]
        for name, source, klass in fields:
            if not source:
                continue
            shown = c.row_text(row, klass)
            norm = lambda t: " ".join(("needs more information" if t == render.NMI else str(t)).split())
            # Additions are not omissions. Ordered source tokens must remain in
            # the associated field; this does not certify added words as true.
            output_tokens = iter(norm(shown).split())
            retained = all(any(token == candidate for candidate in output_tokens)
                           for token in norm(source).split())
            if not retained:
                finding("supplied-content-missing", rid, {name: source}, shown,
                        "Supplied %s is not reachable in its row or a two-way associated shared note." % name)
            if name in ("scenario", "why") and shown and not c.shared_text(row, klass):
                repetitions.setdefault((name, norm(shown)), []).append(rid)
        for kind, expected in (("owner", c.m["part_title"].get(src.get("owner"))),
                               ("step", (src.get("step") or {}).get("title")),
                               ("dec", render.decision_name(c.m, src["decision"]) if src.get("decision") else None)):
            if not expected or not any(n.attrs.get("data-group") == kind for n in row.ancestors()):
                continue
            shown = group_context(row, kind)
            if expected not in shown:
                finding("group-context", rid, {kind: expected}, shown, "Owning visible group context is missing or mismatched.")
        if not reachable(row):
            finding("hidden-row", rid, src.get("rule"), row.attrs, "Source row is inert or explicitly hidden.")
    for (name, text), ids in repetitions.items():
        if len(ids) > 1:
            finding("repeated-prose", ", ".join(ids), {"rule_ids": ids, "field": name}, text,
                    "Identical visible prose across distinct rules; review specificity without deleting evidence.")
    return out


# The cross-client name scan (once check 14) is not run inside a customer run: it is a pre-ship check over the
# plugin only. Checks keep their spec numbers, so 14 is skipped.
NUMBERS = [i for i in range(1, 23) if i != 14]
CHECKS = [globals()["check_%02d" % i] for i in NUMBERS]


# RULES.md numbering differs from the historical checker numbering. These are
# coverage declarations, not semantic or visual pass claims. Partial predicates
# explicitly retain the additional witness needed to judge the whole rule.
RULES_COVERAGE = [
    (1, "Recorded dimensions only", [3, 13], "Source/page comparison of all optional dimensions"),
    (2, "Mixed missing fields grouped once", [3], "Source/page comparison of mixed-absence grouping"),
    (3, "Aligned numeric tables", [], "Browser column/header alignment at 1440/900/390"),
    (4, "Compact ordered summary", [2], "Human review of summary order and compactness"),
    (5, "Real-page visual review", [], "Human inspection of real single/set screenshots"),
    (6, "No repeated group fields", [3], "Human inspection of expanded group context"),
    (7, "No internal codes in primary prose", [10], "Human distinction between meaningful evidence and internal codes"),
    (8, "Authored plain boundary", [], "Source/page comparison of boundary wording and authority"),
    (9, "Plain captured example", [20], "Source-grounded human comparison of example and capture"),
    (10, "Visible contradiction and supported sides", [], "Source evidence and browser side-panel/session inspection"),
    (11, "No repeated scenario or why", [], "Human specificity/overlap judgment; never scrub source"),
    (12, "Only owner/process/evidence filters", [13], "Browser filter inventory and expected-row interaction"),
    (13, "Waiting-on plain question", [8, 11], "Human review of question wording"),
    (14, "Expanded rows without overlap", [], "Browser all-expanded overlap at 1440/900/390 plus human review"),
    (15, "No empty scenario column", [3], "Browser per-row layout where scenario absent"),
    (16, "HTML/CSS only", [16], ""),
    (17, "CSS filters and all-visible fallback", [17], "Browser filter interaction and unsupported-selector fallback"),
    (18, "Non-destructive mechanisms", [1, 15], "Source comparison of authored grouping and order"),
    (19, "Single tab and derived summary numbers", [2, 18], "Human summary review; typed digits are diagnostic only"),
    (20, "Invocation order and future-state links", [4, 19], "Source comparison of invocation order and process view"),
    (21, "Authored future-state position", [4, 19], "Source authority review; never infer missing position"),
    (22, "Frames and derived outcomes", [2], "Source/page comparison of frames and terminal actions"),
    (23, "Evidence counts and grounded example", [7, 20], "Source-grounding and client-isolation review"),
    (24, "Reasoned guardrails and plain evidence names", [10, 12], "Source/page comparison of guardrail reasons and rule links"),
    (25, "Grouped asks and current behavior, no approval banner", [22], "Human review of grouping, access separation and approval language"),
    (26, "Plain missing-info collapsed row", [21], "Source/page review of plain missing information and waiting link"),
]


def coverage_matrix():
    content_rules = {1, 2, 6, 10, 11, 15, 18, 26}
    return [{"rule": number, "requirement": title,
             "predicate": ["check_%02d" % i for i in checks] +
                          (["content_diagnostics"] if number in content_rules else []),
             "required_witness": witness,
             "witness_status": "unverified" if witness else "not-required",
             "scope": "Diagnostic coverage only; consult raw findings and external witnesses."}
            for number, title, checks, witness in RULES_COVERAGE]


def report(result, code, problems, counts):
    # Findings are notes for the owning stage: the page is already published and stays published whatever they say.
    notes = {}
    for p in problems:
        notes.setdefault(p.get("owner_stage", "page-prep"), []).append(p)
    return {"check": "check_rules_page", "path": CHECK_PATH, "run": ".", "exit_code": code, "result": result,
            "problems": problems, "counts": counts, "notes_by_owner": notes, "page_stays_published": True,
            "agent_fallback": "Read the page against the checks of the rules-page spec section 6 by hand (not check 14)."}


def run(run_dir, page_path):
    try:
        with open(page_path, encoding="utf-8") as f:
            page = f.read()
        c = Ctx(run_dir, page)
    except (OSError, ValueError, KeyError, TypeError, render.SourceMissing) as exc:
        return report("error", 2, [{"defect": "unreadable-input", "where": os.path.basename(str(page_path)),
                                    "detail": "%s: %s" % (type(exc).__name__, exc)}], {})
    c.warnings = []
    problems, counts = [], {}
    for i, fn in zip(NUMBERS, CHECKS):
        try:
            found = fn(c)
        except Exception as exc:  # noqa: BLE001 - a crashing check fails and the others still run
            found = [P(i, "check-crashed", fn.__name__, "%s: %s" % (type(exc).__name__, exc))]
        counts["check_%02d_problems" % i] = len(found)
        problems.extend(found)
    counts["checks_run"] = len(CHECKS)
    counts["checks_failed"] = sum(1 for i in NUMBERS if counts["check_%02d_problems" % i])
    counts["warnings"] = len(c.warnings)
    counts["rows"] = len(c.rows)
    # Preserve inspectable source/output evidence for inventory and mechanism
    # failures as well as the source-associated prose diagnostics below.
    for problem in problems:
        if problem["defect"].startswith("check-02-"):
            problem["source_evidence"] = {"rule_ids": [r["id"] for r in c.rules]}
            problem["output_evidence"] = {"row_ids": [r.attrs.get("data-rid") for r in c.rows],
                "counts": [{"count": n.attrs.get("data-count"), "filter": n.attrs.get("data-f"),
                            "text": n.text().strip()} for n in c.nodes if "data-count" in n.attrs]}
        if problem["defect"].startswith("check-15-"):
            problem["source_evidence"] = [{"rule_id": p.get("rule_id"), "mechanism": p.get("mechanism"),
                "text_sha256": p.get("text_sha256")} for p in c.placed]
            problem["output_evidence"] = [{"mechanism": n.attrs.get("data-mech"),
                "rows": [{"id": r.attrs.get("data-rid"), "step": r.attrs.get("data-mech-step")}
                         for r in n.walk() if "rule" in r.cls()]} for n in c.nodes if "data-mech" in n.attrs]
    extra = content_diagnostics(c)
    problems.extend(extra)
    counts["content_diagnostics"] = len(extra)
    rep = report("fail" if problems else "pass", 1 if problems else 0, problems, counts)
    rep["rules_coverage"] = coverage_matrix()
    rep["source_inventory"] = [{"id": r["id"], "prose_sha256": rule_sha(r)} for r in c.rules]
    rep["rendered_inventory"] = [r.attrs.get("data-rid") for r in c.rows]
    rep["input_sha256"] = {"page": hashlib.sha256(page.encode("utf-8")).hexdigest()}
    rep["fallback_records"] = [{"original_failed_evidence": p,
        "supported_reading": "Retain supplied source and rendered draft; this finding does not establish semantic falsity.",
        "reason": "Diagnostic only; presentation heuristics cannot justify deleting evidence or refusing readable input.",
        "action": "continue-with-finding"} for p in problems]
    rep["browser_evidence"] = {"status": "unverified", "required_widths": [1440, 900, 390],
        "checks": ["numeric alignment", "expanded overlap", "filter interaction", "all-visible fallback"],
        "reason": "Static predicates do not measure browser geometry or replace human visual judgment."}
    return rep


def check(run_dir, page="agent-design.html"):
    """Same report as the command; `page` is relative to the run folder unless absolute."""
    return run(run_dir, page if os.path.isabs(page) else os.path.join(run_dir, page))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--run", required=True)
    ap.add_argument("--page", required=True)
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    if not os.path.isdir(a.run):
        rep = report("error", 2, [{"defect": "unreadable-input", "where": "run", "detail": "run folder not found"}], {})
    else:
        # ad.py runs checks from the repo root with `--page agent-design.html`: a relative page is read
        # inside the run folder; one that is only found from the working folder still works as given.
        page = a.page
        if not os.path.isabs(page) and (os.path.isfile(os.path.join(a.run, page)) or not os.path.isfile(page)):
            page = os.path.join(a.run, page)
        rep = run(a.run, page)
    out = a.out or (os.path.join(a.run, "checks", "check_rules_page.json") if os.path.isdir(a.run) else None)
    try:
        if out is None:
            raise OSError("no writable report path")
        os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
        with open(out, "w", encoding="utf-8") as f:
            json.dump(rep, f, indent=2)
            f.write("\n")
    except OSError:
        pass
    for p in rep["problems"]:
        print("%s  %s: %s" % (p["defect"], p["where"], p["detail"]))
    print("result: %s" % rep["result"])
    return rep["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
