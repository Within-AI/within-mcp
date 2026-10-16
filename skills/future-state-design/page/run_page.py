#!/usr/bin/env python3
"""The Future State Design run page: one page in RUN/outputs/ with four tabs, built on knowledge/page/progressive/.

  build(run) -> dict     write RUN/outputs/future-state-design.html; return {"page": path or None, "notes": [str]}
  visible_text(html)     the text a reader sees (tags and style removed), for the id checks in the tests

Tabs: Today and Future use the shared diagram viewer. Change associates authored future metadata with
current predecessor identities and exact move declarations, retains map narrative and accessible source provenance.
Missing and ambiguous references stay readable unknowns. The page work folder is RUN/builder/page/.
Standard library only, Python 3.9-safe.
"""
import html
import importlib.util
import json
import os
import re
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(HERE)), "fast-mode-build", "driver"))
import knowledge_root  # noqa: E402  the one lookup for the plugin's knowledge folder
PROGRESSIVE = os.path.join(knowledge_root.find(), "page", "progressive", "progressive.py")
PAGE_NAME = "future-state-design.html"
SLOTS = [{"id": "today", "section": "Today", "title": "How the work runs today"},
         {"id": "change", "section": "Change", "title": "What changes"},
         {"id": "future", "section": "Future", "title": "How the work would run"},
         {"id": "open", "section": "What we still need to know", "title": "Open questions"}]
HEX12 = re.compile(r"\b[0-9a-f]{12}\b(?::[A-Za-z0-9_.:-]+)?")
CODE_ID = re.compile(r"\b[A-Z]{1,4}-\d+[a-z]?\b")
PREFIX_ID = re.compile(r"\b(?:tp|iss|oq|lk|ag|ai|dec|end|x|wg|rm|dd)-[a-z0-9][a-z0-9_-]*\b")
TICKED = re.compile(r"`([^`]*)`")
FIELD = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)+$")
SET_SLOT = {"id": "set", "section": "How the processes connect", "title": "How the processes connect"}
STAGE_OF = {"today": "current-state", "change": "transformation-plan", "future": "future-state", "set": "set-map"}


TEXT_KEYS = ("title", "label", "name", "text", "reason", "summary", "description", "client_text", "role_text")


def field_tokens(*docs):
    """Every snake_case key, and every snake_case string value that is not display text (the same title, label and
    name fields the id cleaner reads titles from), in the given parsed JSON documents."""
    out = set()

    def walk(o):
        if isinstance(o, dict):
            for k, v in o.items():
                if isinstance(k, str) and FIELD.match(k):
                    out.add(k)
                if k in TEXT_KEYS:
                    continue
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
        elif isinstance(o, str) and FIELD.match(o):
            out.add(o)
    for d in docs:
        walk(d)
    return out


def _stage_reached(st, stage):
    """True once the stage has finished (done or failed); a stage not in this run counts as reached."""
    stages = (st or {}).get("stages") or {}
    if not stages:
        return True   # no state to judge by (an older run): render what is on disk
    if stage not in stages:
        return True
    status = str((stages.get(stage) or {}).get("status", "pending"))
    return status.startswith(("done", "failed"))


def _set_map_html(run, cleaner):
    """How the processes connect: the set map's processes and the links between them, in plain words."""
    smap = _load(os.path.join(run, "inputs", "set-map.json"))
    if not isinstance(smap, dict):
        return None
    titles = {}
    items = []
    for p in smap.get("processes") or []:
        if not isinstance(p, dict):
            continue
        t = cleaner.clean(p.get("title") or "")
        if p.get("process_id"):
            titles[p["process_id"]] = t
        role = cleaner.clean(p.get("role_text") or "")
        if t:
            items.append("<li><strong>%s</strong>%s</li>" % (html.escape(t), (": " + html.escape(role)) if role else ""))
    objects = {o.get("id"): cleaner.clean(o.get("name") or "") for o in smap.get("work_objects") or [] if isinstance(o, dict)}
    links = []
    for lk in smap.get("links") or []:
        if not isinstance(lk, dict):
            continue
        src = ", ".join(t for t in (titles.get(x, "") for x in lk.get("from") or []) if t)
        dst = ", ".join(t for t in (titles.get(x, "") for x in lk.get("to") or []) if t)
        if not (src and dst):
            continue
        what = objects.get(lk.get("object")) or ""
        kind = cleaner.clean(str(lk.get("kind") or "passes work")).replace("_", " ").replace("-", " ")
        links.append("<li>%s → %s (%s%s)</li>" % (html.escape(src), html.escape(dst), html.escape(kind),
                                                  (", " + html.escape(what)) if what else ""))
    if not items and not links:
        return None
    out = []
    title = cleaner.clean(smap.get("title") or "")
    if title:
        out.append("<p>%s</p>" % html.escape(title))
    if items:
        out.append("<h4>The processes</h4>\n<ul>\n%s\n</ul>" % "\n".join(items))
    if links:
        out.append("<h4>How work moves between them</h4>\n<ul>\n%s\n</ul>" % "\n".join(links))
    return "\n".join(out)


def _progressive():
    spec = importlib.util.spec_from_file_location("fsd_progressive", PROGRESSIVE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _walk_ids(obj, ids, labels):
    if isinstance(obj, dict):
        i = obj.get("id")
        if isinstance(i, str) and i:
            ids.add(i)
            t = obj.get("title") or obj.get("label") or obj.get("name")
            if isinstance(t, str) and t.strip() and i not in labels:
                labels[i] = t.strip()
        for v in obj.values():
            _walk_ids(v, ids, labels)
    elif isinstance(obj, list):
        for v in obj:
            _walk_ids(v, ids, labels)


def id_like(token):
    """An id that could be mistaken for a word only when it carries a digit, hyphen, underscore or colon."""
    return len(token) >= 2 and bool(re.search(r"[0-9_:-]", token))


class Cleaner(object):
    """Rewrites text so no id shows; keeps a list of what it changed. Ids with a digit, hyphen, underscore or colon are
    removed wherever they stand; plain-word ids (such as a node called start) are words too, so they are removed only
    where they stand as an id: in backticks, as a whole label or cell, or as an item of a list after a colon, comma,
    semicolon or bracket and before a comma, semicolon, full stop, bracket or the end."""

    def __init__(self, ids, labels):
        self.ids = set(i for i in ids if id_like(i))
        self.words = set(i for i in ids if i and not id_like(i) and re.match(r"^[A-Za-z]+$", i))
        self.changes = []
        self.fields = None
        pat = "|".join(re.escape(i) for i in sorted(self.ids, key=len, reverse=True))
        self.bare = re.compile(r"(?<![A-Za-z0-9])(?:%s)(?![A-Za-z0-9])" % pat) if pat else None
        wpat = "|".join(re.escape(i) for i in sorted(self.words, key=len, reverse=True))
        self.word_slot = re.compile(r"(^|[:,;(]\s*)(%s)(?=\s*(?:[,;.)]|$))" % wpat, re.M) if wpat else None
        self.labels = {}
        for k, v in labels.items():
            if self._dirty(v):
                continue
            v = self._strip(v)   # a title must not bring an id back in
            if v:
                self.labels[k] = v

    def _strip(self, text):
        """Remove every id from a title, with no replacement (so a replacement never adds an id)."""
        text = HEX12.sub("", text)
        if self.bare is not None:
            text = self.bare.sub("", text)
        if self.word_slot is not None:
            text = self.word_slot.sub(lambda m: m.group(1), text)
        text = PREFIX_ID.sub("", text)
        return re.sub(r"\s{2,}", " ", text).strip(" ,;")

    def _dirty(self, text):
        return bool(HEX12.search(text) or CODE_ID.search(text))

    def _swap(self, token):
        t = token.strip()
        if t in self.labels:
            self.changes.append("replaced %s with its title" % t)
            return self.labels[t]
        self.changes.append("description unavailable for %s" % t)
        return "Description unavailable"

    def clean(self, text):
        text = str(text)
        text = TICKED.sub(lambda m: self._swap(m.group(1)) if (m.group(1).strip() in self.ids or HEX12.search(m.group(1))
                                                               or m.group(1).strip() in self.words
                                                               or CODE_ID.search(m.group(1)) or id_like(m.group(1).strip())
                                                               and " " not in m.group(1).strip()) else m.group(1), text)
        text = HEX12.sub(lambda m: self._swap(m.group(0)), text)
        if self.bare is not None:
            text = self.bare.sub(lambda m: self._swap(m.group(0)), text)
        if self.word_slot is not None:
            text = self.word_slot.sub(lambda m: m.group(1) + self._swap(m.group(2)), text)
        text = CODE_ID.sub(lambda m: self._swap(m.group(0)), text)
        text = PREFIX_ID.sub(lambda m: self._swap(m.group(0)), text)
        text = re.sub(r"\(\s*[,;]*\s*\)", "", text)
        text = re.sub(r"\s+([,;.)])", r"\1", text)
        text = re.sub(r"([,;])(\s*[,;])+", r"\1", text)
        text = re.sub(r"\(\s*[,;]\s*", "(", text)
        text = re.sub(r"\s{2,}", " ", text)
        return text.strip(" ,;")

    def fields_as_words(self, text):
        """Field names and enum values from the run's data, as plain words: used only where those values are what the
        text shows (diagram hover text and the change table's cells), never on titles or other prose."""
        return self.fields.sub(lambda m: m.group(0).replace("_", " "), text) if self.fields is not None else text

    def set_fields(self, tokens):
        """Field names and enum values taken from the run's own data (snake_case, such as needs_more_information or
        decision_gate) read as plain words wherever they appear; other text is left alone."""
        tokens = sorted(set(t for t in tokens if FIELD.match(t)), key=len, reverse=True)
        self.fields = re.compile(r"(?<![A-Za-z0-9_])(?:%s)(?![A-Za-z0-9_])" % "|".join(map(re.escape, tokens))) if tokens else None


def _tables(md):
    """[(heading, header cells, [row cells])] for every Markdown table, under the nearest heading."""
    out, heading, lines = [], "", md.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if line.startswith("#"):
            heading = line.lstrip("#").strip()
        if line.startswith("|") and i + 1 < len(lines) and re.match(r"^\|[\s:|-]+\|?$", lines[i + 1].strip()):
            head = [c.strip() for c in line.strip("|").split("|")]
            rows, i = [], i + 2
            while i < len(lines) and lines[i].strip().startswith("|"):
                rows.append([c.strip() for c in lines[i].strip().strip("|").split("|")])
                i += 1
            out.append((heading, head, rows))
            continue
        i += 1
    return out


def _sentence(head, cells, cleaner):
    parts = []
    for h, c in zip(head, cells):
        h2, c2 = cleaner.clean(h), cleaner.fields_as_words(cleaner.clean(c))
        if c2 and c2 not in ("-", "—"):
            parts.append("%s: %s" % (h2, c2) if h2 else c2)
    if not parts:
        return ""
    s = "; ".join(parts)
    return s if s.endswith(".") else s + "."


def _source_detail(value):
    return '<details class="fsd-source"><summary>Source provenance</summary><pre>%s</pre></details>' % html.escape(str(value))


def _nodes(doc):
    return [n for n in ((doc or {}).get("graph") or {}).get("nodes", []) if isinstance(n, dict)]


def _scope(node):
    return str(node.get("process_id") or "")


def _refs(value):
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _named(node):
    return str(node.get("title") or node.get("label") or node.get("name") or "Step name unavailable")


def _resolve(nodes, ref, scope):
    # Exact identity within the same process; never substitute a future title for a predecessor.
    bare = not isinstance(ref, dict) or "process_id" not in ref
    if isinstance(ref, dict):
        scope = str(ref.get("process_id") or scope)
        ref = ref.get("id") or ref.get("node_id")
    candidates = [(i, n) for i, n in enumerate(nodes) if n.get("id") == ref]
    scoped = [(i, n) for i, n in candidates if _scope(n) == scope]
    # A bare reference may name a shared run/set node, but never another member.
    # Keep all matches so duplicate identities remain visibly ambiguous.
    if not scoped and bare:
        scoped = [(i, n) for i, n in candidates if not _scope(n)]
    return scoped


def _map_text(md):
    # Comments and fenced examples are evidence, not authored move declarations.
    text = re.sub(r"<!--.*?-->", "", md, flags=re.S)
    return re.sub(r"(?ms)^\s*(`{3,}|~{3,}).*?^\s*\1\s*$", "", text)


def _move_rows(md):
    moves = {}
    for heading, headers, rows in _tables(_map_text(md)):
        keys = [h.strip().lower() for h in headers]
        if "move" not in keys or "kind" not in keys:
            continue
        for cells in rows:
            row = dict(zip(keys, cells))
            ident = row.get("move", "").strip(" `")
            moves.setdefault(ident, []).append(row)
    return moves


def _lineage(cur, fut, md, smap):
    current, future = _nodes(cur), _nodes(fut)
    title_cleaner = Cleaner({n.get("id") for n in current + future if n.get("id")}, {})
    def display(n):
        return title_cleaner._strip(_named(n)) or "Step name unavailable"
    moves = _move_rows(md)
    processes = {}
    for p in (smap or {}).get("processes", []):
        if isinstance(p, dict):
            processes.setdefault(str(p.get("process_id") or ""), []).append(p.get("title") or p.get("name"))
    def process_name(scope):
        names = processes.get(scope, [])
        if len(names) == 1 and names[0]:
            return str(names[0])
        if scope == "set":
            return str((smap or {}).get("title") or "Whole process set")
        if not scope:
            return str(((fut or cur or {}).get("process") or {}).get("name") or "Run scope")
        return "Process name unavailable"
    def reference(ref, scope, nodes, kind):
        matches = _resolve(nodes, ref, scope)
        if len(matches) != 1:
            return '<p class="lineage-unknown">%s description unavailable (%s).</p>%s' % (
                kind, "ambiguous reference" if matches else "unresolved reference", _source_detail(ref))
        i, n = matches[0]
        label = display(n)
        return '<p class="lineage-reference" data-source-index="%d">%s — %s</p>%s' % (
            i, html.escape(label), html.escape(process_name(_scope(n))),
            _source_detail(json.dumps({"reference": ref, "process_id": _scope(n), "node_index": i}, ensure_ascii=False)))
    out = ['<h4>Step-by-step changes</h4>']
    for i, n in enumerate(future):
        meta = n.get("future") if isinstance(n.get("future"), dict) else {}
        prior, move_refs = _refs(meta.get("from")), _refs(meta.get("moves"))
        change = str(meta.get("change") or "Change classification unavailable")
        out.append('<article class="fsd-lineage" id="change-node-%d" data-future-index="%d"><h5>%s — %s</h5><p class="lineage-classification">%s</p>' % (
            i, i, html.escape(display(n)), html.escape(process_name(_scope(n))), html.escape(change)))
        out.append('<div class="lineage-priors"><h6>Current predecessors</h6>')
        if prior:
            if change.lower() == "new":
                out.append('<p>Uncertainty: marked new but current predecessors are also supplied.</p>')
            for ref in prior:
                out.append(reference(ref, _scope(n), current, "Prior step"))
        else:
            out.append('<p>%s</p>' % ("New step; no current predecessor supplied." if change.lower() == "new" else "Prior step description unavailable; no predecessor supplied."))
        out.append('</div><div class="lineage-moves"><h6>Reshape moves</h6>')
        for ref in move_refs:
            rows = moves.get(str(ref), [])
            if len(rows) != 1:
                out.append('<p>Move description unavailable (%s).</p>%s' % ("ambiguous reference" if rows else "unresolved reference", _source_detail(ref)))
                continue
            row = rows[0]
            declared = row.get("future steps")
            if declared:
                declared_refs = re.findall(r"`([^`]+)`", declared) or [v.strip() for v in declared.split(",")]
                targets = [match for ref in declared_refs for match in _resolve(future, ref, _scope(n))]
                if not any(index == i for index, _ in targets):
                    out.append('<p>Uncertainty: the move declaration does not identify this future step among its future steps.</p>')
            out.append('<section class="lineage-move"><h6>%s</h6>' % html.escape(row.get("kind") or "Move name unavailable"))
            for key, value in row.items():
                if key in ("move", "kind"):
                    continue
                if key in ("current steps", "future steps"):
                    out.append('<p><strong>%s</strong></p>' % html.escape(key.capitalize()))
                    refs = re.findall(r"`([^`]+)`", value) or [v.strip() for v in value.split(",") if v.strip()]
                    for item in refs:
                        out.append(reference(item, _scope(n), current if key == "current steps" else future, key[:-1].capitalize()))
                else:
                    out.append('<p><strong>%s:</strong> %s</p>' % (html.escape(key.capitalize()), html.escape(value)))
            out.append(_source_detail(json.dumps(row, ensure_ascii=False)) + '</section>')
        if not move_refs:
            out.append('<p>No reshape move reference supplied.</p>')
        out.append('</div>' + _source_detail(json.dumps({"pointer": "/graph/nodes/%d" % i, "id": n.get("id"), "title": _named(n), "future": meta}, ensure_ascii=False)) + '</article>')
    return "\n".join(out) if future else ""


def _viewer(svg, slot, stage):
    spec = importlib.util.spec_from_file_location("fsd_shared_viewer", os.path.join(knowledge_root.find(), "renderer", "_diagram_viewer.py"))
    viewer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(viewer)
    # Prefix DOM ids and all local references, leaving source identity data attributes intact.
    ids = re.findall(r"(?<![\w-])id=[\"']([^\"']+)[\"']", svg)
    for ident in sorted(set(ids), key=len, reverse=True):
        escaped = re.escape(ident)
        svg = re.sub(r"((?<![\w-])id=[\"'])" + escaped + r"([\"'])", lambda m: m[1] + slot + "-" + ident + m[2], svg)
        svg = re.sub(r"(url\([\"']?#|(?:href|xlink:href)=[\"']#)" + escaped + r"(?=[\"')])", lambda m: m[1] + slot + "-" + ident, svg)
    def aria(m):
        return m[1] + " ".join(slot + "-" + x if x in ids else x for x in m[2].split()) + m[3]
    svg = re.sub(r'(aria-(?:labelledby|describedby)=["\'])([^"\']+)(["\'])', aria, svg)
    detail = viewer.detail_items(svg)
    if not detail:
        texts = re.findall(r'<(?:text|title)\b[^>]*>(.*?)</(?:text|title)>', svg, re.S)
        detail = [(html.unescape(re.sub('<[^>]*>', '', t)), '') for t in texts if t.strip()]
    return '<style>' + viewer.VIEWER_CSS + '</style>' + viewer.viewer(
        svg, viewer.svg_size(svg)[0], '../stages/%s/%s.svg' % (stage, stage), detail,
        caption="Current state" if slot == "today" else "Future state")


def _svg(path, cleaner):
    try:
        with open(path, encoding="utf-8") as f:
            svg = f.read()
    except OSError:
        return None
    svg = re.sub(r"<\?xml[^>]*\?>", "", svg)
    svg = re.sub(r"<!DOCTYPE[^>]*>", "", svg)
    # Structured renderer output already owns its labels and detail. Expanding a small
    # identity caption into a long node title changes layout and can obscure authored text.
    if re.search(r'\bdata-(?:graph-node-id|graph-edge-id|edge-id|id)=["\']', svg):
        return svg.strip()

    def fix(m):
        inner = html.unescape(m.group(2))
        new = cleaner.clean(inner)
        return m.group(1) + html.escape(new, quote=False) + m.group(3)
    svg = re.sub(r"(<(?:text|tspan|title)\b[^>]*>)([^<]*)(</(?:text|tspan|title)>)", fix, svg)
    svg = re.sub(r"(<title\b[^>]*>)([^<]*)(</title>)",
                 lambda m: m.group(1) + html.escape(cleaner.fields_as_words(html.unescape(m.group(2))), quote=False) + m.group(3), svg)
    svg = re.sub(r"(<(?:text|tspan|title)\b[^>]*>)([^<]*)(<)", fix, svg)
    return svg.strip()


OPEN_HEADING = re.compile(r"still need to know|open question", re.I)


def _map_questions(md):
    """Every item (list item or table row) under a transformation-map heading about open questions."""
    items, level, head_row = [], None, False
    for line in md.splitlines():
        t = line.strip()
        m = re.match(r"^(#+)\s*(.*)$", t)
        if m:
            if level is not None and len(m.group(1)) <= level:
                level = None
            if OPEN_HEADING.search(m.group(2)):
                level, head_row = len(m.group(1)), True
            continue
        if level is None:
            continue
        if t == "---":
            level = None
            continue
        b = re.match(r"^(?:[-*+]|\d+[.)])\s+(.*)$", t)
        if b:
            items.append(b.group(1))
        elif t.startswith("|"):
            if re.match(r"^\|[\s:|-]+\|?$", t):
                continue
            if head_row:
                head_row = False
                continue
            items.append(" ".join(c.strip() for c in t.strip("|").split("|")))
    return items


def _open_reasons(run, docs, st, cleaner, md=""):
    seen, out = set(), []
    for q in _map_questions(md or ""):
        q = re.sub(r"^\s*`?[A-Za-z]+-[A-Za-z0-9_-]+`?\s*[:|]\s*", "", q)   # the question's own id label
        r = cleaner.clean(q)
        if r and r not in seen:
            seen.add(r)
            out.append(r[0].upper() + r[1:])
    for d in docs:
        g = (d or {}).get("graph") or {}
        for iss in g.get("issues") or []:
            if isinstance(iss, dict) and iss.get("status", "open") == "open" and isinstance(iss.get("reason"), str):
                r = cleaner.clean(iss["reason"])
                if r and r not in seen:
                    seen.add(r)
                    out.append(r)
    for item in (st or {}).get("what_we_still_need_to_know") or []:
        t = item.get("text") if isinstance(item, dict) else item
        if isinstance(t, str):
            r = cleaner.clean(t)
            if r and r not in seen:
                seen.add(r)
                out.append(r)
    return out


def visible_text(page):
    page = re.sub(r'(?s)<details class="fsd-source">.*?</details>', " ", page)
    t = re.sub(r"(?is)<(style|script)\b.*?</\1>", " ", page)
    t = re.sub(r"(?s)<!--.*?-->", " ", t)
    t = re.sub(r"<[^>]+>", " ", t)
    return html.unescape(t)


def render(run, st=None):
    """The page HTML and the builder notes, from what the run has so far: a part whose stage has not finished reads
    "Not reached yet"; a finished stage with nothing to show says so. Writes only RUN/builder/page/."""
    notes = []
    st = st if st is not None else (_load(os.path.join(run, "state.json")) or {})
    cur = _load(os.path.join(run, "stages", "current-state", "current-state.json"))
    fut = _load(os.path.join(run, "stages", "future-state", "future-state.json"))
    ids, labels = set(), {}
    for d in (cur, fut):
        _walk_ids(d, ids, labels)
    cleaner = Cleaner(ids, labels)
    cleaner.set_fields(field_tokens(cur, fut, _load(os.path.join(run, "inputs", "snapshot.json")),
                                    _load(os.path.join(run, "inputs", "set-map.json"))))
    title = "Future State Design"
    proc = (fut or cur or {}).get("process")
    if isinstance(proc, dict) and isinstance(proc.get("name"), str):
        title = cleaner.clean(proc["name"]) or title
    pp = _progressive()
    work = os.path.join(run, "builder", "page")
    if os.path.isdir(work):
        shutil.rmtree(work)
    is_set = (st.get("layout") == "set") or os.path.isfile(os.path.join(run, "inputs", "set-map.json"))
    slots = ([SET_SLOT] if is_set else []) + SLOTS
    pp.scaffold(work, slots)
    reached = {slot: _stage_reached(st, stage) for slot, stage in STAGE_OF.items()}
    if is_set:
        body = _set_map_html(run, cleaner)
        if body:
            pp.fill(work, "set", None, body)
        elif reached["set"] or "set-map" not in (st.get("stages") or {}):
            notes.append("set map missing or unreadable; its tab says so")
            pp.fill(work, "set", None, "<p>How the processes connect could not be shown for this run.</p>")
    for slot, stage in (("today", "current-state"), ("future", "future-state")):
        if not reached[slot]:
            continue   # pending: the library's "Not reached yet"
        state_ids, state_labels = set(), {}
        _walk_ids(cur if slot == "today" else fut, state_ids, state_labels)
        state_cleaner = Cleaner(state_ids, state_labels)
        state_cleaner.set_fields(field_tokens(cur if slot == "today" else fut))
        svg = _svg(os.path.join(run, "stages", stage, stage + ".svg"), state_cleaner)
        if svg is None:
            notes.append("%s diagram missing; its tab says so" % stage)
            body = "<p>This diagram was not produced in this run.</p>"
        else:
            body = '<p>%s</p>\n<div class="fsd-diagram">%s</div>' % (html.escape(((cur if slot == 'today' else fut) or {}).get('process', {}).get('name') or title), _viewer(svg, slot, stage))
        pp.fill(work, slot, None, body)
    md = ""
    if reached["change"]:
        try:
            with open(os.path.join(run, "stages", "transformation-plan", "transformation-map.md"), encoding="utf-8") as f:
                md = f.read()
        except OSError:
            notes.append("the transformation map could not be read; the Change tab says so")
        parts = [_lineage(cur, fut, md, _load(os.path.join(run, "inputs", "set-map.json")))]
        # Render every authored section in order; table cells with explicit state headings
        # use that state's labels. Ambiguous unqualified identities remain unknown.
        state_cleaners = []
        for doc in (cur, fut):
            state_ids, state_labels = set(), {}
            _walk_ids(doc, state_ids, state_labels)
            c = Cleaner(state_ids, state_labels)
            c.set_fields(field_tokens(doc))
            state_cleaners.append(c)
        ambiguous = {k for k in state_cleaners[0].labels.keys() & state_cleaners[1].labels.keys()
                     if state_cleaners[0].labels[k] != state_cleaners[1].labels[k]}
        summary_cleaner = Cleaner(ids, {k: v for k, v in labels.items() if k not in ambiguous})
        summary_cleaner.set_fields(field_tokens(cur, fut))
        lines = _map_text(md).splitlines()
        i = 0
        while i < len(lines):
            line = lines[i].strip()
            if line.startswith("|") and i + 1 < len(lines) and re.match(r"^\|[\s:|-]+\|?$", lines[i + 1].strip()):
                headers = [c.strip() for c in line.strip("|").split("|")]
                i += 2
                parts.append("<ul>")
                while i < len(lines) and lines[i].strip().startswith("|"):
                    cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                    sentences = []
                    for header, cell in zip(headers, cells):
                        c = state_cleaners[0] if "current" in header.lower() else state_cleaners[1] if "future" in header.lower() else summary_cleaner
                        sentences.append(_sentence([header], [cell], c))
                    parts.append("<li>%s</li>" % html.escape(" ".join(filter(None, sentences))))
                    i += 1
                parts.append("</ul>")
                continue
            if line.startswith("#"):
                parts.append("<h4>%s</h4>" % html.escape(summary_cleaner.clean(line.lstrip("# "))))
            elif line and line != "---":
                parts.append("<p>%s</p>" % html.escape(summary_cleaner.clean(line)))
            i += 1
        cleaner.changes.extend(summary_cleaner.changes)
        for c in state_cleaners:
            cleaner.changes.extend(c.changes)
        if md:
            parts.append(_source_detail(md))
        pp.fill(work, "change", None, "\n".join(parts) or "<p>The transformation map had no tables to summarise.</p>")
    reasons = _open_reasons(run, (cur, fut), st, cleaner, md)
    finished = all(reached[k] for k in ("today", "change", "future"))
    if reasons:
        pp.fill(work, "open", None, "<ul>\n%s\n</ul>" % "\n".join("<li>%s</li>" % html.escape(r) for r in reasons))
    elif finished:
        pp.fill(work, "open", None, "<p>Nothing is open.</p>")
    if finished:
        gate = pp.final_gate(work)
        for p in gate.get("problems") or []:
            notes.append("page gate: %s" % p)
    with open(os.path.join(work, "page.html"), encoding="utf-8") as f:
        text = f.read()
    text = text.replace("</head>", '<style>.fsd-lineage{border-top:1px solid #ddd;padding:12px 0;overflow-wrap:anywhere}.fsd-lineage h5{font-size:16px;margin:8px 0}.fsd-lineage h6{font-size:14px;margin:8px 0}.fsd-source pre{white-space:pre-wrap;overflow-wrap:anywhere}.dv-detail dl{overflow-wrap:anywhere}@media(max-width:600px){.pp-shell{grid-template-columns:minmax(0,1fr);padding:8px 12px 32px;gap:8px}.pp-tabs{position:static;flex-direction:row;flex-wrap:wrap;padding-top:0}.pp-slot{padding:12px;overflow-wrap:anywhere}.pp-main{min-width:0}.dv-detail dl{display:block}.dv-detail dd{margin-bottom:12px}}</style></head>')
    text = text.replace("<title>Run page</title>", "<title>%s</title>" % html.escape(title), 1)
    for c in sorted(set(cleaner.changes)):
        notes.append("page text: %s" % c)
    return text, notes


def build(run):
    """Render and write RUN/outputs/future-state-design.html directly (no live parts); fsd.py publishes through the
    shared live page instead."""
    text, notes = render(run)
    os.makedirs(os.path.join(run, "outputs"), exist_ok=True)
    dest = os.path.join(run, "outputs", PAGE_NAME)
    with open(dest, "w", encoding="utf-8") as f:
        f.write(text)
    return {"page": dest, "notes": notes}


if __name__ == "__main__":
    import sys
    r = build(os.path.abspath(sys.argv[1]))
    print(r["page"])
