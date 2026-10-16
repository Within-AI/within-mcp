"""Self-contained, offline diagram pages: the standalone workflow page and report figures.

`figure(svg, caption)` embeds the SVG inline for inspection reports and the run page (inline_svg():
the XML prolog and doctype dropped, every id and #reference prefixed per diagram so two diagrams on one page do not
share ids; svg_source() undoes it, so a test can hash the shown diagram back to the recorded file). `document(svg, bundle, current)` builds the standalone page: the SVG inlined
in a scrolling frame, with each step's and link's full detail in its native SVG <title>
(the browser shows it on hover), plus decision tables, open items, the current-state link,
handoffs, notes and coverage generated from the graph. Pages carry no script: a CSS-only
checkbox switches between full size and fit-to-width. No I/O besides the template.
"""
import hashlib
import html
import posixpath
import re
from pathlib import Path
from string import Template
import sys
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "checks"))
from workflow_contract import (FUTURE, KNOWN_EDGE_KINDS, KNOWN_LANES, KNOWN_NODE_TYPES, PLUGIN_SHAPE,  # noqa: E402
                               RENDERER_ID, graph_notes, id_list, topology)
from _no_script import assert_no_script  # noqa: E402
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _diagram_viewer import VIEWER_CSS, detail_items, svg_size, viewer as diagram_viewer  # noqa: E402

CSS = """
.diagram-legend{font:12px/1.6 system-ui;color:#64748b;margin:10px 0}
.figure:has(.diagram-viewer){width:min(1900px,calc(100vw - 56px));margin-left:50%;transform:translateX(-50%)}
""" + VIEWER_CSS
FIGURE_LEGEND = ("Bands show who does the work: Analyst, Agent and System, plus any other band the map uses. "
                 "Diamonds are decisions. Coral lines are handoffs; dashed drops are reads and solid drops are "
                 "writes to a system rail. A small i marks a note; the standalone page lists them.")

PAGE_CSS = """
:root{--ink:#0B1E2F;--muted:#4A5560;--faint:#A79E92;--coral:#F56740;--coral-ink:#B23A1E;--access:#2E6E86;
--cream:#FBF6EF;--sky:#CFF3FE;--sun:#FFF5D2;--rose:#FFE0D8;--line:#E7DFD3}
*{box-sizing:border-box}
body{margin:0;background:var(--cream);color:var(--ink);font-family:Inter,-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;font-size:14px;line-height:1.55}
.wrap{max-width:1900px;margin:0 auto;padding:28px 24px 64px}
h1{font-size:26px;font-weight:600;letter-spacing:-0.02em;margin:0 0 6px}
.sub{color:var(--muted);font-size:15px;margin:0 0 18px;max-width:90ch}
h2{font-size:12px;font-weight:600;letter-spacing:0.1em;text-transform:uppercase;color:var(--muted);margin:30px 0 10px;display:flex;align-items:center;gap:9px}
h2::before{content:'';width:8px;height:8px;border-radius:50%;background:var(--coral)}
h3{font-size:14px;font-weight:600;margin:14px 0 6px}
.card{background:#fff;border:1px solid var(--line);border-radius:14px;padding:16px 18px}
.legend{display:flex;flex-wrap:wrap;gap:6px 18px;font-size:12.5px;color:var(--muted);margin:0 0 12px;padding:0;list-style:none}
.legend li{display:inline-flex;align-items:center;gap:7px}
.sw{display:inline-block;width:24px;height:12px;border-radius:3px;border:1px solid rgba(11,30,47,.15)}
.ln{display:inline-block;width:28px;height:0;border-top:2px solid var(--ink)}
.dv-frame svg [data-graph-node-id],.dv-frame svg [data-graph-edge-id]{cursor:help}
.hint{font-size:12px;color:var(--muted);margin:8px 2px 0}
table{width:100%;border-collapse:collapse;font-size:12.5px;margin:6px 0 10px}
th,td{border-bottom:1px solid var(--line);padding:6px 8px;text-align:left;vertical-align:top}
th{font-weight:600;color:var(--ink);font-size:11.5px}
td{color:var(--muted)}
code{font-family:Inter,sans-serif;font-size:.9em;background:rgba(11,30,47,.05);padding:1px 5px;border-radius:3px;color:var(--ink)}
ul.plain{margin:6px 0 0;padding-left:18px}
ul.plain li{margin:3px 0;color:var(--muted)}
.muted{color:var(--muted)}
@media print{.hint{display:none}}
""" + VIEWER_CSS
_PROLOG = re.compile(r"\A﻿?\s*(?:<\?xml[^>]*\?>\s*)?(?:<!DOCTYPE[^>]*>\s*)?", re.I)
# one piece of SVG markup: a comment, CDATA, declaration, end tag or start tag (quoted values may hold ">")
_TAG = re.compile(r"""<(?:!--.*?-->|!\[CDATA\[.*?\]\]>|[!?][^>]*>|/[^>]*>|[A-Za-z](?:[^>"']|"[^"]*"|'[^']*')*>)""", re.S)
_ATTR = re.compile(r"""(\s)([^\s=/>"']+)(\s*=\s*)("[^"]*"|'[^']*'|[^\s"'>]+)""")
_URL_REF = re.compile(r"""(url\(\s*["']?#)([^"')\s]+)""", re.I)


def svg_prefix(svg, index=0):
    """The id prefix of one diagram on a page: the sha256 of its exact bytes plus its place among the page's diagrams
    (number_figures() sets it), so it is the same on every render and unique per figure."""
    return "d%s-%d-" % (hashlib.sha256(svg.encode("utf-8")).hexdigest()[:12], index)


def _defined_ids(svg):
    ids = set()
    for tag in _TAG.findall(svg):
        if tag[1:2].isalpha():
            for _sp, name, _eq, value in _ATTR.findall(tag):
                if name == "id":
                    ids.add(value.strip("\"'"))
    return ids


# Attributes whose value is an id reference (one id, or a space-separated list of ids). Any other attribute is text or
# data and is never touched, except that url(#id) is rewritten in `style` and the presentation attributes below.
_ID_LIST_ATTRS = {"aria-labelledby", "aria-describedby", "aria-controls", "aria-owns", "aria-flowto", "aria-details",
                  "aria-errormessage", "aria-activedescendant", "for"}
_URL_ATTRS = {"style", "fill", "stroke", "clip-path", "mask", "filter", "marker", "marker-start", "marker-mid",
              "marker-end", "cursor", "color-profile"}
# SVG animation attributes that may hold url(#id) values (attributeName="fill" values="url(#a);url(#b)").
_URL_ATTRS |= {"values", "from", "to", "by"}
_CSS_ID = re.compile(r"#((?:[-_A-Za-z0-9]|[^\x00-\x7f])+)")


def _css(text, ids, rename, urls):
    """<style> text with url(#id) rewritten in declarations and #id rewritten in selectors (the text before a "{"
    that is not an at-rule prelude). Comments and quoted strings are copied as they are; a #hex colour inside a
    declaration is never a selector, so it is never touched."""
    out, seg, i, n = [], [], 0, len(text)

    def flush(selector):
        piece = "".join(seg)
        del seg[:]
        if selector and not piece.lstrip().startswith("@"):
            piece = _selector_ids(piece, ids, rename)
        out.append(urls(piece))

    while i < n:
        ch = text[i]
        if text.startswith("/*", i):
            j = text.find("*/", i + 2)
            j = n if j < 0 else j + 2
            flush(False)
            out.append(text[i:j])
            i = j
            continue
        if ch in "\"'":
            j = i + 1
            while j < n and text[j] != ch:
                j += 2 if text[j] == "\\" else 1
            seg.append(text[i:j + 1])
            i = j + 1
            continue
        if ch == "{":
            flush(True)
            out.append(ch)
        elif ch in ";}":
            flush(False)
            out.append(ch)
        else:
            seg.append(ch)
        i += 1
    flush(False)
    return "".join(out)


def _selector_ids(selector, ids, rename):
    """#id in a selector renamed, outside quoted strings and [attribute] brackets; an escaped id is left alone."""
    out, i, n, depth = [], 0, len(selector), 0
    while i < n:
        ch = selector[i]
        if ch in "\"'":
            j = selector.find(ch, i + 1)
            j = n if j < 0 else j + 1
            out.append(selector[i:j])
            i = j
            continue
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth = max(0, depth - 1)
        elif ch == "#" and depth == 0:
            m = _CSS_ID.match(selector, i)
            nxt = selector[m.end():m.end() + 1] if m else ""
            if m and nxt != "\\" and m.group(1) in ids:
                out.append("#" + rename(m.group(1)))
                i = m.end()
                continue
        out.append(ch)
        i += 1
    return "".join(out)


def _rename(svg, rename):
    """SVG markup with RENAME(old id) applied to every reference to an id the SVG defines: the id attribute itself,
    (xlink:)href="#id", the id tokens of id-list attributes (aria-labelledby, aria-describedby, for, ...), url(#id) in
    `style` and presentation attributes, and in <style> text url(#id) and #id selectors. Free text is never touched:
    text nodes, comments, CDATA, aria-label, data-* and every other attribute keep their bytes."""
    ids = _defined_ids(svg)

    def urls(text):
        return _URL_REF.sub(lambda m: m.group(1) + (rename(m.group(2)) if m.group(2) in ids else m.group(2)), text)

    def attr(m):
        space, name, eq, value = m.groups()
        quote = value[0] if value[:1] in "\"'" else ""
        inner = value[1:-1] if quote else value
        key = name.lower()
        if key == "id":
            if inner in ids:
                inner = rename(inner)
        elif key in ("href", "xlink:href"):
            if inner.startswith("#") and inner[1:] in ids:
                inner = "#" + rename(inner[1:])
        elif key in _ID_LIST_ATTRS:
            inner = "".join(rename(t) if t in ids else t for t in re.split(r"(\s+)", inner))
        elif key in _URL_ATTRS:
            inner = urls(inner)
        return space + name + eq + quote + inner + quote

    out, pos, in_style = [], 0, False
    for m in _TAG.finditer(svg):
        text = svg[pos:m.start()]
        out.append(_css(text, ids, rename, urls) if in_style else text)
        tag = m.group()
        if tag[1:2].isalpha():
            out.append(_ATTR.sub(attr, tag))
            in_style = re.match(r"<style\b", tag, re.I) is not None and not tag.endswith("/>")
        else:
            out.append(tag)
            if re.match(r"</style\b", tag, re.I):
                in_style = False
        pos = m.end()
    out.append(svg[pos:])
    return "".join(out)


def inline_svg(svg, prefix=None):
    """SVG text as it is inlined on a page: the XML prolog and doctype dropped, and every id the SVG defines renamed
    with PREFIX (default svg_prefix(svg)) in its id attribute and in every reference that points at it: href="#id"
    and xlink:href="#id", id-list attributes (aria-labelledby, aria-describedby, for, ...), url(#id) in style and
    presentation attributes, and url(#id) and #id selectors in <style> text, single or double quoted. Text nodes and
    text attributes (aria-label, data-*, ...) are never changed.
    Deterministic: a check recomputes it from the recorded file and compares the whole output."""
    prefix = svg_prefix(svg) if prefix is None else prefix
    body = _PROLOG.sub("", svg, count=1)
    out = _rename(body, lambda name: prefix + name)
    if svg_source(out, prefix) != body:
        # the source already names something the renaming would collide with (e.g. "#p-a" beside id "a" for prefix
        # "p-"), so svg_source() could not undo it exactly. Callers pick another place number (free_prefix()).
        raise ValueError("id prefix %r does not round-trip for this SVG" % prefix)
    return out


def _prefix_taken(svg, prefix):
    """True when renaming SVG's ids with PREFIX could not be undone exactly."""
    body = _PROLOG.sub("", svg, count=1)
    return svg_source(_rename(body, lambda name: prefix + name), prefix) != body


def free_prefix(svg, index=0, used=()):
    """svg_prefix(svg, k) for the first place number k >= INDEX that is not in USED and starts no name in SVG."""
    k = index
    while svg_prefix(svg, k) in used or _prefix_taken(svg, svg_prefix(svg, k)):
        k += 1
    return svg_prefix(svg, k)


def svg_source(inline, prefix):
    """inline_svg() undone, tags only, for a diagram drawn for the page with no recorded file (it has no prolog): the
    bytes it was drawn from, to hash. Text nodes are left as shown, so a changed label changes the hash."""
    return _rename(inline, lambda name: name[len(prefix):] if name.startswith(prefix) else name)


def number_figures(markup):
    """Every diagram in MARKUP with an id prefix of its own: a prefix already used earlier in MARKUP (the same figure
    shown twice) moves to the next unused place number. Deterministic; a page whose prefixes differ is unchanged."""
    used = set(re.findall(r'data-svg-prefix="([^"]*)"', markup))
    seen = set()

    def one(m):
        attrs, inline = m.group(1), m.group(2)
        old = re.search(r'data-svg-prefix="([^"]*)"', attrs).group(1)
        if old not in seen:
            seen.add(old)
            return m.group(0)
        base, k = re.sub(r"\d+-$", "", old), 0
        source = svg_source(inline, old)
        while "%s%d-" % (base, k) in used or _prefix_taken(source, "%s%d-" % (base, k)):
            k += 1
        new = "%s%d-" % (base, k)
        used.add(new)
        seen.add(new)
        swap = lambda name: new + name[len(old):] if name.startswith(old) else name  # noqa: E731
        return '<div class="diagram-svg"%s>%s</div>' % (attrs.replace('data-svg-prefix="%s"' % old,
                                                                         'data-svg-prefix="%s"' % new), _rename(inline, swap))
    return FIGURE_RE.sub(one, markup)


FIGURE_RE = re.compile(r'<div class="diagram-svg"([^>]*)>(<svg\b.*?</svg>\s*)</div>', re.S)


def figure(svg, caption="Workflow diagram", index=0):
    # The SVG inline (a page carries no data:image/svg+xml, FORDEEN-87); data-svg-sha256 names the exact bytes it was
    # drawn from and data-svg-prefix the id prefix. The shared viewer around it (sizes, full window) is CSS only;
    # these pages write no standalone file, so the viewer carries no "open on its own" link.
    digest = hashlib.sha256(svg.encode("utf-8")).hexdigest()
    prefix = free_prefix(svg, index)
    root = ET.fromstring(svg)
    view = root.get("viewBox", "").split()
    native_width = float(view[2]) if len(view) == 4 else 1000
    legend = FIGURE_LEGEND if root.get("data-renderer") == RENDERER_ID else ""
    inner = (f'<div class="diagram-svg" role="img" aria-label="{html.escape(caption, quote=True)}" data-native-width="{native_width:g}" '
             f'data-svg-sha256="{digest}" data-svg-prefix="{prefix}">' + inline_svg(svg, prefix) + '</div>')
    return ('<div class="diagram-viewer" style="--native-width:%gpx">' % native_width +
            diagram_viewer(inner, native_width, caption=None, frame_class="diagram-viewport") +
            '<p class="diagram-legend">' + legend + '</p></div>')



def angle_safe(text):
    """Text for a <title> or <textarea>: "<" written as the single angle quote ‹, since those elements
    may hold no "<" even once entities are decoded (the no-script page shape, FORDEEN-87)."""
    return str(text).replace("<", "\u2039")

def E(value):
    return html.escape(str(value), quote=True)


def cut(text, limit):
    text = str(text)
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def pretty(value):
    return str(value).replace("_", " ")


def _table(headers, rows):
    head = "".join("<th>%s</th>" % E(h) for h in headers)
    body = "".join("<tr>%s</tr>" % "".join("<td>%s</td>" % c for c in row) for row in rows)
    return "<table><thead><tr>%s</tr></thead><tbody>%s</tbody></table>" % (head, body)


def _decision_tables(graph, titles):
    tables = graph.get("decision_tables") or []
    if not tables:
        return ""
    out = ['<h2>Decision tables</h2><div class="card">']
    for t in tables:
        owners = ", ".join("<code>%s</code> %s" % (E(o), E(titles.get(o, ""))) for o in id_list(t["owner"]) or [])
        out.append("<h3><code>%s</code> · %s</h3>" % (E(t["id"]), E(pretty(t["kind"]))))
        out.append('<p class="muted">%s Owner: %s. Evidence: %s.</p>'
                   % (E(t.get("purpose", "")), owners, E(pretty(t.get("evidence_state", "")))))
        columns = t.get("columns") or []
        rows = t.get("rows") or []
        cells = t.get("cells") or []
        if t["kind"] == "consultation":
            grid = {(c.get("row_id"), c.get("column")): c for c in cells}
            body = []
            for r in rows:
                line = ["<code>%s</code> %s" % (E(r["id"]), E(r.get("label", "")))]
                for col in columns:
                    c = grid.get((r["id"], col), {})
                    line.append(E(c.get("outcome", "")) + ("" if c.get("evidence_state") in (None, "Observed")
                                                           else " <em>(%s)</em>" % E(pretty(c["evidence_state"]))))
                body.append(line)
            out.append(_table(["Row"] + [pretty(c) for c in columns], body))
        elif t["kind"] == "selection":
            body = []
            for r in rows:
                attrs = r.get("attributes") or {}
                leads = r.get("next_node")
                leads_to = ("<code>%s</code> %s" % (E(leads), E(titles.get(leads, "")))) if leads else \
                    "needs more information"
                body.append(["<code>%s</code> %s" % (E(r["id"]), E(r.get("label", "")))] +
                            [E(attrs.get(c, "")) for c in columns] +
                            [leads_to, E(pretty(r.get("evidence_state", ""))),
                             E(", ".join(id_list(r.get("issue_ids")) or []))])
            out.append(_table(["Row"] + [pretty(c) for c in columns] + ["Leads to", "Evidence", "Open items"], body))
        else:
            body = []
            for r in rows:
                attrs = r.get("attributes") or {}
                body.append(["<code>%s</code> %s" % (E(r["id"]), E(r.get("label", "")))] +
                            [E(attrs.get(c, "")) for c in columns] + [E(pretty(r.get("evidence_state", "")))])
            out.append(_table(["Row"] + [pretty(c) for c in columns] + ["Evidence"], body))
            if cells:
                out.append(_table(["Row", "Column", "Outcome"],
                                  [[E(c.get("row_id", "")), E(c.get("column", "")), E(c.get("outcome", ""))] for c in cells]))
        rules = t.get("rules") or []
        if rules:
            out.append('<ul class="plain">%s</ul>' % "".join(
                "<li><code>%s</code> %s <span class=\"muted\">(%s)</span></li>"
                % (E(r["id"]), E(r.get("text", "")), E(pretty(r.get("evidence_state", "")))) for r in rules))
        if t.get("treatment_note"):
            out.append('<p class="muted">%s</p>' % E(t["treatment_note"]))
    out.append("</div>")
    return "".join(out)


def _open_items(graph):
    issues = graph.get("issues") or []
    if not issues:
        return ""
    kinds = []
    for i in issues:
        if i["kind"] not in kinds:
            kinds.append(i["kind"])
    if "open-question" in kinds:
        kinds.remove("open-question")
        kinds.insert(0, "open-question")
    out = ['<h2>Open items</h2><div class="card">']
    for kind in kinds:
        heading = "What we still need to know" if kind == "open-question" else pretty(kind).capitalize()
        rows = []
        for i in issues:
            if i["kind"] != kind:
                continue
            bound = ", ".join(id_list(i.get("bound_to")) or [])
            rows.append(["<code>%s</code>" % E(i["id"]), E(i.get("reason", "")), E(bound),
                         E(", ".join(i.get("source_ids") or []))])
        out.append("<h3>%s</h3>" % E(heading))
        out.append(_table(["Item", "Reason", "Bound to", "Sources"], rows))
    out.append("</div>")
    return "".join(out)


def _current_state(graph, pin):
    kind = pin.get("kind", PLUGIN_SHAPE)
    digest = str(pin.get("sha256", ""))[:12]
    out = ['<h2>Current state</h2><div class="card">',
           "<p>Transforms: <code>%s</code> · %s · sha256 %s</p>" % (E(pin.get("path", "")), E(kind), E(digest))]
    if kind == PLUGIN_SHAPE:
        out.append("<p class=\"muted\">Carried open items and decision tables checked by script.</p>")
    else:
        out.append("<p class=\"muted\">Current state in a different shape; carried items checked by review.</p>")
        reasons = {i["id"]: i.get("reason", "") for i in graph.get("issues") or []}
        items = pin.get("carried_open_items") or []
        if items:
            out.append(_table(["Item", "As stated in the current state", "Where", "Now recorded as"],
                              [["<code>%s</code>" % E(i.get("id", "")), E(i.get("text", "")), E(i.get("source_locator", "")),
                                "<code>%s</code> %s" % (E(i.get("issue_id", "")), E(reasons.get(i.get("issue_id"), "")))]
                               for i in items if isinstance(i, dict)]))
        else:
            out.append("<p>None listed.</p>")
    out.append("</div>")
    return "".join(out)


def _handoffs(graph, titles):
    edges = [e for e in graph.get("edges") or [] if e["kind"] in ("handoff", "inbound")]
    if not edges:
        return ""
    rows = [["<code>%s</code> %s" % (E(e["id"]), E(e["kind"])),
             "<code>%s</code> %s → <code>%s</code> %s" % (E(e["source"]), E(titles.get(e["source"], "")),
                                                        E(e["target"]), E(titles.get(e["target"], ""))),
             E(e.get("label") or e.get("display_label") or "")] for e in edges]
    return '<h2>Handoffs</h2><div class="card">%s</div>' % _table(["Link", "Route", "Label"], rows)


def _notes(notes, titles):
    if not notes:
        return ""
    ordered = [n for n in notes if n["id"]] + [n for n in notes if not n["id"]]
    items = "".join("<li>%s%s</li>" % (("<code>%s</code> %s: " % (E(n["id"]), E(titles.get(n["id"], "")))) if n["id"]
                                       else "Page: ", E(n["text"])) for n in ordered)
    return '<h2>Notes on this diagram</h2><div class="card"><ul class="plain">%s</ul></div>' % items


def _grant_readings(bundle):
    """The future state's recorded readings of the map's Write grants table, each beside the table's own value."""
    rs = [r for r in bundle.get("grant_readings") or [] if isinstance(r, dict)]
    if not rs:
        return ""

    def said(v):
        return ", ".join(str(x) for x in v) if isinstance(v, list) else str(v if v is not None else "")

    rows = [[E(said(r.get("grant"))), E(said(r.get("column"))), E(said(r.get("table_says"))), E(said(r.get("read_as"))),
             E(said(r.get("reason")))] for r in rs]
    return ('<h2>Write grant readings</h2><div class="card"><p>Where the transformation map\'s Write grants table and '
            'the evidence disagree, the diagram follows the evidence; the map is unchanged.</p>%s</div>'
            % _table(["Grant", "Column", "The map's table says", "Drawn as", "Why"], rows))


def _coverage(graph, bundle, s):
    counts = {}
    for n in graph["nodes"]:
        band = "System" if n["type"] == "system_surface" else s.band_of(n["id"])[0]
        counts.setdefault(band, {}).setdefault(n["type"], 0)
        counts[band][n["type"]] += 1
    rows = [[E(band), E(", ".join("%s %d" % (pretty(t), c) for t, c in types.items()))] for band, types in counts.items()]
    kinds = {}
    for e in graph.get("edges") or []:
        kinds[e["kind"]] = kinds.get(e["kind"], 0) + 1
    rows.append(["Links", E(", ".join("%s %d" % (pretty(k), c) for k, c in kinds.items()) or "none")])
    rows.append(["Decision tables", str(len(graph.get("decision_tables") or []))])
    mapping = {}
    for row in bundle.get("coverage") or []:
        if isinstance(row, dict):
            mapping[row.get("mapping")] = mapping.get(row.get("mapping"), 0) + 1
    if mapping:
        rows.append(["Captured steps", E(", ".join("%s %d" % (k, c) for k, c in mapping.items()))])
    return '<h2>Coverage</h2><div class="card">%s</div>' % _table(["Part", "Count"], rows)


def _legend(graph, s, notes):
    items = []
    bands = [b["lane"] for b in s.bands] + ["System"]
    fills = {"Analyst": "var(--cream)", "Agent": "rgba(207,243,254,.35)"}
    for band in bands:
        items.append('<li><span class="sw" style="background:%s"></span>%s band</li>' % (fills.get(band, "#fff"), E(band)))
    other_bands = [b for b in bands if b not in KNOWN_LANES]
    if other_bands:
        items.append("<li>Other bands: %s</li>" % E(", ".join(other_bands)))
    kinds = []
    for e in graph.get("edges") or []:
        if e["kind"] not in kinds:
            kinds.append(e["kind"])
    styles = {"sequence": "border-top:2px solid var(--ink)", "then": "border-top:2px solid var(--ink)",
              "else": "border-top:2px solid var(--ink)", "exception": "border-top:2px dashed var(--ink)",
              "loop": "border-top:2px dashed var(--muted)", "handoff": "border-top:3px solid var(--coral)",
              "inbound": "border-top:2px dashed var(--coral)", "read_access": "border-top:2px dashed var(--access)",
              "write": "border-top:2px solid var(--ink)"}
    words = {"sequence": "sequence", "then": "decision branch", "else": "decision branch", "exception": "exception",
             "loop": "loop back", "handoff": "handoff", "inbound": "inbound (a person hands work to the agent)",
             "read_access": "read from a system", "write": "write to a system (dark coral when the agent writes)"}
    shown = set()
    for k in kinds:
        if k in styles and words[k] not in shown:
            shown.add(words[k])
            items.append('<li><span class="ln" style="%s"></span>%s</li>' % (styles[k], E(words[k])))
    other_kinds = [k for k in kinds if k not in KNOWN_EDGE_KINDS]
    if other_kinds:
        items.append('<li><span class="ln" style="border-top:2px dotted var(--ink)"></span>Other link kinds: %s</li>'
                     % E(", ".join(pretty(k) for k in other_kinds)))
    if s.topo["returns"]:
        items.append('<li><span class="ln" style="border-top:2px dashed var(--ink)"></span>return path (closes a cycle)</li>')
    types = []
    for n in graph["nodes"]:
        if n["type"] not in KNOWN_NODE_TYPES and n["type"] not in types:
            types.append(n["type"])
    if types:
        items.append("<li>Other step types: %s</li>" % E(", ".join(types)))
    states = {n.get("evidence_state") for n in graph["nodes"]}
    if "needs_more_information" in states:
        items.append("<li>[needs more information] marks a step whose evidence is incomplete</li>")
    if "Contradictory" in states:
        items.append("<li>≠ marks a Contradictory step</li>")
    kinds_present = {s.nodes[r].get("surface_kind") for r in s.rails}
    chip = {"system_of_record": ("var(--sky)", "system of record"), "channel": ("var(--rose)", "channel"),
            "review_surface": ("var(--sun)", "review surface")}
    for k, (fill, word) in chip.items():
        if k in kinds_present:
            items.append('<li><span class="sw" style="background:%s"></span>%s chip</li>' % (fill, word))
    if graph.get("workspaces"):
        items.append('<li><span class="sw" style="background:var(--sun)"></span>workspace chip</li>')
    if any(s.nodes[r].get("access_state") == "Assumed" for r in s.rails):
        items.append("<li>ACCESS ASSUMED bracket: rails whose access is assumed, not observed</li>")
    if notes:
        items.append("<li>i marks a note on this diagram (listed below)</li>")
    for e, text, _, n in s.footnotes:
        items.append("<li>Edge labels moved off the diagram: [%d] %s (<code>%s</code>)</li>" % (n, E(text), E(e["id"])))
    return '<ul class="legend">%s</ul>' % "".join(items)


_GROUP = re.compile(r'(<g [^>]*?data-graph-(?:node|edge)-id="([^"]*)"[^>]*>)<title>(.*?)</title>', re.S)


def _tip_lines(d, shown):
    """The detail lines of tip D, after the one-line summary SHOWN the SVG already carries.
    Notes and an edge condition already in the summary are not repeated."""
    lines = []

    def add(label, value, skip_if_shown=None):
        value = str(value or "").strip()
        if value and not (skip_if_shown and skip_if_shown in shown):
            lines.append("%s: %s" % (label, value))

    if "type" in d:
        add("Step", "%s · %s" % (d["id"], pretty(d["type"])))
    else:
        add("Link", "%s · %s" % (d["id"], pretty(d.get("kind", ""))))
    add("Band", d.get("lane"))
    add("Performer", d.get("performer"))
    add("Ownership", d.get("treatment"))
    add("Evidence", pretty(d.get("evidence_state") or ""))
    add("Condition", d.get("condition"), "condition: %s" % d.get("condition"))
    add("Systems", " · ".join(d.get("systems") or []))
    add("Decision table", ", ".join(d.get("decision_table") or []))
    add("Detail", d.get("detail"))
    for f in d.get("facts") or []:
        add(f.get("label") or "Fact", f.get("text"))
    for n in d.get("notes") or []:
        add("Note", n, n)
    return lines


def with_titles(svg, tip_data):
    """SVG with each step's and link's <title> extended by the rest of its detail from TIP_DATA."""
    def fill(m):
        d = tip_data.get(html.unescape(m.group(2)))
        if not d:
            return m.group(0)
        shown = html.unescape(m.group(3))
        text = "\n".join([shown] + _tip_lines(d, shown))
        return "%s<title>%s</title>" % (m.group(1), html.escape(angle_safe(text), quote=False))
    return _GROUP.sub(fill, svg)


def _recorded_svg_href(bundle):
    """The standalone SVG's path relative to the page, from the bundle's own render records (both paths are relative
    to the bundle); None when the bundle does not record both."""
    render, page = bundle.get("render"), bundle.get("render_html")
    if not (isinstance(render, dict) and isinstance(page, dict) and render.get("path") and page.get("path")):
        return None
    return posixpath.relpath(str(render["path"]).replace("\\", "/"), posixpath.dirname(str(page["path"]).replace("\\", "/")) or ".")


def document(svg, bundle, current=None, svg_href=None):
    """The standalone page for BUNDLE (a workflow bundle or a graph document). SVG_HREF is the standalone SVG file's
    path relative to the page, linked from the viewer; when None it comes from the bundle's render records, and no
    link is drawn when those are absent."""
    from _workflow_bands import layout, tips
    graph = bundle["graph"]
    variant = bundle.get("variant")
    diagram = bundle.get("diagram") if isinstance(bundle.get("diagram"), dict) else {}
    pin = bundle.get("current_state") if variant == FUTURE and isinstance(bundle.get("current_state"), dict) else None
    notes = graph_notes(graph, variant, current, pin, diagram)
    s = layout(graph, variant, diagram)
    titles = {n["id"]: n.get("title", "") for n in graph["nodes"]}
    process = bundle.get("process") if isinstance(bundle.get("process"), dict) else {}
    title = cut(diagram.get("title") or process.get("name") or "Workflow", 120)
    subtitle = cut(diagram.get("subtitle") or "", 240)
    sections = [_decision_tables(graph, titles), _open_items(graph)]
    if variant == FUTURE and pin is not None:
        sections.append(_current_state(graph, pin))
    sections += [_grant_readings(bundle), _handoffs(graph, titles), _notes(notes, titles), _coverage(graph, bundle, s)]
    if svg_href is None:
        svg_href = _recorded_svg_href(bundle)
    shown = with_titles(svg.strip(), tips(graph, notes))
    viewer = (diagram_viewer(shown, svg_size(shown)[0], svg_href=svg_href, detail=detail_items(shown),
                             caption=title) +
              '<p class="hint">Opens fitted to the width · pick a size, or Full window, and scroll inside the frame to '
              'move around' + (' · open the diagram on its own to use the browser\'s zoom' if svg_href else '') +
              ' · hover a step or link for its detail, or read every step in words under the diagram</p>')
    template = Template((Path(__file__).resolve().parents[1] / "templates/workflow-diagram.html").read_text(encoding="utf-8"))
    page = template.substitute(title=E(angle_safe(title)), subtitle=E(subtitle), legend=_legend(graph, s, notes), viewer=viewer,
                               sections="".join(sections), notes="".join("<li>%s</li>" % E(n) for n in diagram.get("notes") or []),
                               viewer_css=PAGE_CSS)
    return assert_no_script(page, "workflow diagram page")
