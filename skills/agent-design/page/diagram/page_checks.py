"""Static page checks for the FORDEEN-72 items (U27, pins F6 and H16, H17).

    problems(html) -> list[str]

Reads the HTML text only (no browser) and returns one entry per instance found, each beginning with its problem
name: diagonal-edge, edge-through-box, badge-over-title, cut-label, cropped-diagram, wall-of-text,
caption-direction, raw-id.

SVG conventions (the renderer's own, pinned by U27):
  boxes    <rect> with x, y, width, height; a <rect> inside an element of class "badge" is part of the badge, not a box.
  edges    <line>, <polyline> or <path>; a path with any curve command (C, S, Q, T, A) is a diagonal edge.
  badges   elements of class "badge"; a badge's box is its own x/y/width/height, or the union of the rects inside it.
  text     <text> elements carrying font-size and font-weight; outside a badge each is a title and a label. Width is
           the sum of per-character em widths from knowledge/renderer/a3_inter_widths.json for that weight (nearest
           weight key; a character missing from the table takes the widest character of that weight) times the font
           size. The text box spans 0.8 em above the baseline to 0.2 em below it, placed by text-anchor.
  label    a text's box is the smallest non-badge rect holding its anchor point; the label is cut when its width is
           over that rect's width less 6 px padding on each side. Text inside a badge is measured against the badge.
  geometry only translate transforms on <g> are applied; any other transform is cropped-diagram. <defs>, <marker>
           and their contents are skipped (arrow markers excluded). Strokes are excluded. Tolerance 0.5 px.
Standard library only.
"""
import html.parser
import importlib.util
import json
import math
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(HERE))))
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


WIDTHS = _knowledge("renderer", "a3_inter_widths.json")

TOL = 0.5
LABEL_PAD = 6
MIN_SCALE = 0.45
P_WORDS = 60
P_RUN = 3
DIRECTIONS = ("left-to-right", "top-to-bottom")
_DIRECTION_WORDS = re.compile(r"\b(left|right|top|bottom)[\s-]+to[\s-]+(left|right|top|bottom)\b", re.I)
_RAW_PATTERNS = [
    re.compile(r"\b[A-Z]{1,4}-[0-9]+\b"),
    re.compile(r"\b(?=[0-9a-fA-F]*[0-9])(?=[0-9a-fA-F]*[a-fA-F])[0-9a-fA-F]{8,}\b"),
    re.compile(r"\b[a-z0-9]+(?:_[a-z0-9]+)+\b"),
]
CODE_SLOTS = ("appendix", "simulation")  # slots whose <code> text may show raw ids
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
BREAKS = {"h1", "h2", "h3", "h4", "h5", "h6", "table", "ul", "ol", "dl", "details", "figure"}
SKIP_GEOMETRY = {"defs", "marker", "clippath", "mask", "pattern", "symbol", "style", "title", "desc"}
_TRANSLATE = re.compile(r"^\s*translate\(\s*(-?[\d.]+)(?:[\s,]+(-?[\d.]+))?\s*\)\s*$")
_NUM = re.compile(r"-?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")
_ZOOM_OVERFLOW = re.compile(r"\.rp-zoom-view\b[^{]*\{([^}]*)\}", re.S)
_HIDDEN = re.compile(r"overflow(?:-[xy])?\s*:\s*(hidden|clip)", re.I)


# ---- a small tree from the HTML text -------------------------------------------------------------------------

class Node:
    def __init__(self, tag, attrs, parent):
        self.tag, self.attrs, self.parent, self.children = tag, attrs, parent, []

    def elements(self):
        return [c for c in self.children if isinstance(c, Node)]

    def walk(self):
        for c in self.children:
            if isinstance(c, Node):
                yield c
                for d in c.walk():
                    yield d

    def text(self):
        return "".join(c if isinstance(c, str) else c.text() for c in self.children)

    def classes(self):
        return (self.attrs.get("class") or "").split()

    def ancestors(self):
        n = self.parent
        while n is not None:
            yield n
            n = n.parent


class _Tree(html.parser.HTMLParser):
    def __init__(self, text):
        super().__init__(convert_charrefs=True)
        self.root = Node("#root", {}, None)
        self.cur = self.root
        self.styles = []
        self.feed(text)
        self.close()

    def handle_starttag(self, tag, attrs):
        node = Node(tag, {k: (v if v is not None else "") for k, v in attrs}, self.cur)
        self.cur.children.append(node)
        if tag not in VOID:
            self.cur = node

    def handle_startendtag(self, tag, attrs):
        self.cur.children.append(Node(tag, {k: (v if v is not None else "") for k, v in attrs}, self.cur))

    def handle_endtag(self, tag):
        n = self.cur
        while n is not None and n.tag != tag:
            n = n.parent
        if n is not None and n.parent is not None:
            self.cur = n.parent

    def handle_data(self, data):
        if self.cur.tag == "style":
            self.styles.append(data)
        self.cur.children.append(data)

    def handle_comment(self, data):
        pass


# ---- text widths ---------------------------------------------------------------------------------------------

_TABLE = None


def _table():
    global _TABLE
    if _TABLE is None:
        with open(WIDTHS, encoding="utf-8") as f:
            _TABLE = json.load(f)
    return _TABLE


def _weight(value):
    value = (value or "400").strip().lower()
    value = {"normal": "400", "bold": "700", "bolder": "700", "lighter": "400"}.get(value, value)
    try:
        w = float(value)
    except ValueError:
        w = 400.0
    keys = [k for k, v in _table().items() if k.isdigit() and isinstance(v, dict)]
    return min(keys, key=lambda k: (abs(float(k) - w), float(k)))


def text_width(text, size, weight):
    row = _table()[_weight(weight)]
    widest = max(row.values())
    return sum(row.get(ch, widest) for ch in text) * size


def _num(value, default=None):
    if value is None:
        return default
    m = _NUM.search(str(value))
    return float(m.group(0)) if m else default


# ---- geometry ------------------------------------------------------------------------------------------------

def _offset(node, svg):
    """Summed translate of the <g> ancestors up to svg; other transforms are reported by _bad_transforms."""
    dx = dy = 0.0
    for n in [node] + list(node.ancestors()):
        if n is svg:
            break
        m = _TRANSLATE.match(n.attrs.get("transform") or "")
        if n.tag == "g" and m:
            dx += float(m.group(1))
            dy += float(m.group(2) or 0)
    return dx, dy


def _bad_transforms(svg):
    """Each element inside and including svg whose transform is not a translate on a <g>, once each."""
    out = []
    for n in [svg] + list(svg.walk()):
        t = n.attrs.get("transform")
        if t and not (n.tag == "g" and _TRANSLATE.match(t)):
            out.append(n)
    return out


def _skipped(node, svg):
    for n in [node] + list(node.ancestors()):
        if n is svg:
            return False
        if n.tag.lower() in SKIP_GEOMETRY:
            return True
    return False


def _in_badge(node, svg):
    for n in [node] + list(node.ancestors()):
        if n is svg:
            return None
        if "badge" in n.classes():
            return n
    return None


def _points(node):
    """The edge's points, and whether it has a curve."""
    a = node.attrs
    if node.tag == "line":
        return [(_num(a.get("x1"), 0), _num(a.get("y1"), 0)), (_num(a.get("x2"), 0), _num(a.get("y2"), 0))], False
    if node.tag == "polyline":
        nums = [float(v) for v in _NUM.findall(a.get("points", ""))]
        return list(zip(nums[0::2], nums[1::2])), False
    d = a.get("d", "")
    if re.search(r"[CcSsQqTtAa]", d):
        return [], True
    pts, x, y, start = [], 0.0, 0.0, (0.0, 0.0)
    for cmd, args in re.findall(r"([MmLlHhVvZz])([^MmLlHhVvZz]*)", d):
        vals = [float(v) for v in _NUM.findall(args)]
        if cmd in "Zz":
            x, y = start
            pts.append((x, y))
            continue
        if cmd in "Hh":
            for v in vals:
                x = v if cmd == "H" else x + v
                pts.append((x, y))
            continue
        if cmd in "Vv":
            for v in vals:
                y = v if cmd == "V" else y + v
                pts.append((x, y))
            continue
        for i, (vx, vy) in enumerate(zip(vals[0::2], vals[1::2])):
            x, y = (vx, vy) if cmd.isupper() else (x + vx, y + vy)
            if cmd in "Mm" and i == 0:
                start = (x, y)
            pts.append((x, y))
    return pts, False


def _rect(node, dx, dy):
    a = node.attrs
    x, y = _num(a.get("x"), 0) + dx, _num(a.get("y"), 0) + dy
    return (x, y, x + _num(a.get("width"), 0), y + _num(a.get("height"), 0))


def _overlap(a, b):
    return min(a[2], b[2]) - max(a[0], b[0]) > TOL and min(a[3], b[3]) - max(a[1], b[1]) > TOL


def _on_box(p, box):
    return (box[0] - TOL <= p[0] <= box[2] + TOL) and (box[1] - TOL <= p[1] <= box[3] + TOL)


def _segment_meets(p, q, box):
    """A horizontal or vertical segment meeting a box, edge included."""
    x0, x1 = sorted((p[0], q[0]))
    y0, y1 = sorted((p[1], q[1]))
    return x0 <= box[2] and x1 >= box[0] and y0 <= box[3] and y1 >= box[1]


def _svg_problems(svg, graph=False):
    out = []
    a = svg.attrs
    vb = [float(v) for v in _NUM.findall(a.get("viewbox", a.get("viewBox", "")))]
    if len(vb) != 4 or not all(math.isfinite(v) for v in vb) or vb[2] <= 0 or vb[3] <= 0:
        out.append("cropped-diagram: an SVG has no usable viewBox")
        vb = None
    width = a.get("width")
    if vb is not None:
        if width is None or "%" in width or _num(width) is None:
            out.append("cropped-diagram: an SVG has no pixel width, so its default scale is unknown")
        elif _num(width) / vb[2] < MIN_SCALE - 1e-9:
            out.append("cropped-diagram: default scale %.2f is below %.2f" % (_num(width) / vb[2], MIN_SCALE))
    boxes, edges, badges, texts = [], [], {}, []
    for n in svg.walk():
        if _skipped(n, svg) or (graph and not _drawn(n, svg)):
            continue
        dx, dy = _offset(n, svg)
        badge = _in_badge(n, svg)
        if badge is n and n.tag != "rect":
            badges.setdefault(id(n), [n, None])
        if n.tag == "rect":
            r = _rect(n, dx, dy)
            if badge is not None:
                cur = badges.setdefault(id(badge), [badge, None])
                cur[1] = r if cur[1] is None else (min(cur[1][0], r[0]), min(cur[1][1], r[1]),
                                                   max(cur[1][2], r[2]), max(cur[1][3], r[3]))
            else:
                boxes.append(r)
        elif n.tag in ("line", "polyline", "path") and not graph:
            pts, curved = _points(n)
            edges.append(([(x + dx, y + dy) for x, y in pts], curved))
        elif n.tag == "text":
            size = _num(n.attrs.get("font-size"), 16.0)
            w = text_width(n.text().strip(), size, n.attrs.get("font-weight"))
            x, y = _num(n.attrs.get("x"), 0) + dx, _num(n.attrs.get("y"), 0) + dy
            anchor = n.attrs.get("text-anchor", "start")
            left = x - w / 2 if anchor == "middle" else (x - w if anchor == "end" else x)
            texts.append(((left, y - 0.8 * size, left + w, y + 0.2 * size), (x, y), w, n.text().strip(), badge))
    for n in _bad_transforms(svg):
        out.append("cropped-diagram: transform %r is not a translate on a <g>" % n.attrs.get("transform"))
    badge_boxes = [b for _, b in badges.values() if b is not None]
    for n, b in badges.values():
        if b is None and n.attrs.get("width") is not None:
            badge_boxes.append(_rect(n, *_offset(n, svg)))
    # diagonal edges and edges through boxes
    for pts, curved in edges:
        if curved:
            out.append("diagonal-edge: a path with a curve")
            continue
        for p, q in zip(pts, pts[1:]):
            if abs(p[0] - q[0]) > TOL and abs(p[1] - q[1]) > TOL:
                out.append("diagonal-edge: segment (%g,%g)-(%g,%g)" % (p + q))
        if len(pts) < 2:
            continue
        ends = [b for b in boxes if _on_box(pts[0], b) or _on_box(pts[-1], b)]
        for b in boxes:
            if b in ends:
                continue
            if any(_segment_meets(p, q, b) for p, q in zip(pts, pts[1:])):
                out.append("edge-through-box: an edge meets the box at (%g,%g)" % (b[0], b[1]))
    # badges over titles, cut labels
    for box, anchor, w, text, badge in texts:
        if badge is None:
            for bb in badge_boxes:
                if _overlap(box, bb):
                    out.append("badge-over-title: a badge covers %r" % text)
            holders = [b for b in boxes if _on_box(anchor, b)]
        else:
            holders = [b for b in badge_boxes if _on_box(anchor, b)]
        if holders:
            holder = min(holders, key=lambda b: (b[2] - b[0]) * (b[3] - b[1]))
            if w > (holder[2] - holder[0]) - 2 * LABEL_PAD + TOL:
                out.append("cut-label: %r needs %.1f px in a box of %.1f px" % (text, w + 2 * LABEL_PAD, holder[2] - holder[0]))
    # elements outside the viewBox
    if vb is not None:
        frame = (vb[0], vb[1], vb[0] + vb[2], vb[1] + vb[3])
        extents = boxes + badge_boxes + [t[0] for t in texts]
        extents += [(p[0], p[1], p[0], p[1]) for pts, _ in edges for p in pts]
        for e in extents:
            if e[0] < frame[0] - TOL or e[1] < frame[1] - TOL or e[2] > frame[2] + TOL or e[3] > frame[3] + TOL:
                out.append("cropped-diagram: an element at (%g,%g)-(%g,%g) is outside the viewBox" % e)
    return out


# ---- page-level checks ---------------------------------------------------------------------------------------

def _frames_hidden(tree):
    for css in tree.styles:
        for body in _ZOOM_OVERFLOW.findall(css):
            if _HIDDEN.search(body):
                return True
    for n in tree.root.walk():
        if "rp-zoom-view" in n.classes() and _HIDDEN.search(n.attrs.get("style", "")):
            return True
    return False


def _walls(tree):
    out = []
    for n in [tree.root] + list(tree.root.walk()):
        run = 0
        for c in n.elements():
            if c.tag == "p":
                words = len(c.text().split())
                if words > P_WORDS:
                    out.append("wall-of-text: a paragraph of %d words" % words)
                run += 1
                if run == P_RUN + 1:
                    out.append("wall-of-text: more than %d paragraphs in a row with no heading, table, list or details" % P_RUN)
            elif c.tag in BREAKS:
                run = 0
    return out


def _captions(tree):
    out = []
    for fig in tree.root.walk():
        if fig.tag != "figure":
            continue
        svgs = [n for n in fig.walk() if n.tag == "svg"]
        if not svgs:
            continue
        direction = svgs[0].attrs.get("data-direction")
        if direction not in DIRECTIONS:
            out.append("caption-direction: a figure's SVG has no valid data-direction")
            continue
        for cap in (n for n in fig.walk() if n.tag == "figcaption"):
            for m in _DIRECTION_WORDS.finditer(cap.text()):
                named = "%s-to-%s" % (m.group(1).lower(), m.group(2).lower())
                if named != direction:
                    out.append("caption-direction: the caption says %r but the diagram runs %s" % (m.group(0), direction))
    return out


def _visible(tree):
    """(text, exempt) for each text node outside script and style; <code> text inside the appendix or the simulation
    slot is exempt (pins H 17, extended to the simulation slot by the orchestrator ruling of 13 Oct 2026)."""
    out = []

    def go(node, in_code, in_slot):
        for c in node.children:
            if isinstance(c, str):
                out.append((c, in_code and in_slot))
            elif c.tag not in ("script", "style"):
                go(c, in_code or c.tag == "code", in_slot or c.attrs.get("data-slot") in CODE_SLOTS)

    go(tree.root, False, False)
    return out


def _raw_ids(tree):
    out = []
    for text, exempt in _visible(tree):
        if exempt:
            continue
        for pat in _RAW_PATTERNS:
            for m in pat.finditer(text):
                out.append("raw-id: %r" % m.group(0))
    return out


def _drawn(node, svg):
    """Exclude non-rendered evidence, including SVG titles and hidden groups."""
    for item in [node] + list(node.ancestors()):
        style = item.attrs.get("style", "").replace(" ", "").lower()
        if (item.tag in SKIP_GEOMETRY or "hidden" in item.attrs or
                item.attrs.get("display") == "none" or "display:none" in style or
                item.attrs.get("visibility") == "hidden" or "visibility:hidden" in style):
            return False
        if item is svg:
            return True
    return False


def _node_box(node, svg):
    # Use drawn shape coordinates, not the renderer's cached data-bounds.
    for shape in node.walk():
        if shape.parent is not node or not _drawn(shape, svg):
            continue
        dx, dy = _offset(shape, svg)
        if shape.tag == "rect":
            return _rect(shape, dx, dy)
        if shape.tag == "polygon":
            nums = [float(v) for v in _NUM.findall(shape.attrs.get("points", ""))]
            pts = list(zip(nums[::2], nums[1::2]))
            if pts:
                return (min(x for x, y in pts) + dx, min(y for x, y in pts) + dy,
                        max(x for x, y in pts) + dx, max(y for x, y in pts) + dy)
    return None



def _on_rail(point, node, svg):
    """Attachment uses only the finite drawn direct-child rail segment."""
    if not node.attrs.get("data-type", "").startswith("interface:"):
        return False
    for line in node.elements():
        if line.tag != "line" or not _drawn(line, svg):
            continue
        values = [line.attrs.get(k, "0") for k in ("x1", "y1", "x2", "y2")]
        try:
            x1, y1, x2, y2 = map(float, values)
        except ValueError:
            continue
        dx, dy = _offset(line, svg)
        if not all(math.isfinite(v) for v in (x1, y1, x2, y2, dx, dy, *point)):
            continue
        a, b = (x1 + dx, y1 + dy), (x2 + dx, y2 + dy)
        vx, vy = b[0] - a[0], b[1] - a[1]
        length = math.hypot(vx, vy)
        if not length:
            continue
        along = ((point[0] - a[0]) * vx + (point[1] - a[1]) * vy) / length
        across = abs((point[0] - a[0]) * vy - (point[1] - a[1]) * vx) / length
        if -TOL <= along <= length + TOL and across <= TOL:
            return True
    return False


def _own_interface_label(node, edge, svg):
    return (node.attrs.get("data-type", "").startswith("interface:") and
            edge in node.ancestors() and
            any(n.tag == "rect" and n.attrs.get("data-label-for") == edge.attrs.get("data-edge-id")
                and _drawn(n, svg) for n in node.elements()))

def _graph_findings(svg, source_graph=None):
    out = []
    nodes = {n.attrs["data-id"]: n for n in svg.walk()
             if "node" in n.classes() and "data-id" in n.attrs and _drawn(n, svg)}
    edges = {n.attrs["data-edge-id"]: n for n in svg.walk()
             if "edge" in n.classes() and "data-edge-id" in n.attrs and _drawn(n, svg)}
    boxes = {key: _node_box(n, svg) for key, n in nodes.items()}
    boxes = {key: box for key, box in boxes.items() if box is not None}
    for i, (key, box) in enumerate(boxes.items()):
        for other, other_box in list(boxes.items())[i + 1:]:
            if _overlap(box, other_box):
                out.append("node-overlap: SVG nodes %r %s and %r %s" % (key, box, other, other_box))
    for n in _bad_transforms(svg):
        out.append("cropped-diagram: transform %r is not a translate on a <g>" % n.attrs.get("transform"))
    vb = [float(v) for v in _NUM.findall(svg.attrs.get("viewbox", ""))]
    if len(vb) == 4:
        for key, box in boxes.items():
            if box[0] < vb[0] - TOL or box[1] < vb[1] - TOL or box[2] > vb[0] + vb[2] + TOL or box[3] > vb[1] + vb[3] + TOL:
                out.append("cropped-diagram: SVG node %r %s lies outside viewBox %s" % (key, box, vb))
    for key, edge in edges.items():
        paths = [n for n in edge.walk() if n.tag in ("path", "polyline", "line") and _drawn(n, svg)]
        if not paths:
            out.append("missing-edge: SVG edge %r has no drawn path" % key)
        for path in paths:
            points, curved = _points(path)
            dx, dy = _offset(path, svg)
            points = [(x + dx, y + dy) for x, y in points]
            if curved:
                out.append("diagonal-edge: SVG edge %r has a curved path" % key)
            for a, b in zip(points, points[1:]):
                if abs(a[0] - b[0]) > TOL and abs(a[1] - b[1]) > TOL:
                    out.append("diagonal-edge: SVG edge %r segment %s to %s" % (key, a, b))
            for target, box in boxes.items():
                if (target not in (edge.attrs.get("data-from"), edge.attrs.get("data-to"))
                        and not _own_interface_label(nodes[target], edge, svg)):
                    if any(_segment_meets(a, b, box) for a, b in zip(points, points[1:])):
                        out.append("edge-through-box: SVG edge %r meets node %r %s" % (key, target, box))
            if len(points) < 2:
                continue
            for end, point in (("from", points[0]), ("to", points[-1])):
                target = edge.attrs.get("data-" + end)
                box = boxes.get(target)
                if box is not None and not (_on_box(point, box) or _on_rail(point, nodes[target], svg)):
                    out.append("edge-endpoint: SVG edge %r %s point %s misses node %r %s" %
                               (key, end, point, target, box))
    if source_graph is not None:
        for row in source_graph.get("nodes", []):
            key = str(row.get("id", ""))
            node = nodes.get(key)
            if node is None or key not in boxes:
                out.append("missing-step: source nodes[%r] title %r has no drawn SVG node" % (key, row.get("title")))
                continue
            title = " ".join(str(row.get("title", "")).split())
            visible = " ".join(" ".join(n.text().split()) for n in node.walk()
                               if n.tag == "text" and _drawn(n, svg))
            if title and title not in visible:
                out.append("changed-title: source nodes[%r].title %r; SVG text %r" % (key, title, visible))
        for row in source_graph.get("edges", []):
            key = str(row.get("id", ""))
            edge = edges.get(key)
            if edge is None:
                out.append("missing-edge: source edges[%r] %r -> %r has no drawn SVG edge" %
                           (key, row.get("from"), row.get("to")))
                continue
            for end in ("from", "to"):
                expected, actual = row.get(end), edge.attrs.get("data-" + end)
                if expected != actual or actual not in nodes:
                    out.append("edge-endpoint: source edges[%r].%s=%r; SVG data-%s=%r" %
                               (key, end, expected, end, actual))
    return out


def notes(html_text, source_graph=None, original_findings=()):
    """Repair/record/continue findings; never a publication or runtime refusal."""
    return [{"owner_stage": "page-prep", "kind": p.split(":", 1)[0], "detail": p}
            for p in problems(html_text, source_graph, original_findings)]


def problems(html_text, source_graph=None, original_findings=()):
    """Check actual drawings. Optional graph is authored source, never inferred.

    Pass prior problems as original_findings when checking a repaired/fallback page;
    they remain verbatim evidence even if the fallback no longer exposes the defect.
    Without source context only available geometry can be checked.
    """
    tree = _Tree(html_text)
    out = list(original_findings)
    if _frames_hidden(tree):
        out.append("cropped-diagram: the diagram frame hides its overflow")
    graphs = []
    for svg in (n for n in tree.root.walk() if n.tag == "svg"):
        if any("node" in n.classes() and "data-id" in n.attrs for n in svg.walk()):
            graphs.append(svg)
            out.extend(_graph_findings(svg, source_graph))
            out.extend(_svg_problems(svg, graph=True))
        else:
            out.extend(_svg_problems(svg))
    if source_graph is not None and not graphs:
        # An appendix containing the source cannot stand in for a drawing.
        out.extend(_graph_findings(Node("svg", {}, None), source_graph))
    out.extend(_walls(tree))
    out.extend(_captions(tree))
    out.extend(_raw_ids(tree))
    return out


def inventory(html_text, source_graph=None):
    """Available-detail status is separate from diagnostic acceptance."""
    tree = _Tree(html_text)
    svgs = [n for n in tree.root.walk() if n.tag == "svg"]
    return {"detail": "authored-graph" if source_graph is not None else "available-detail-only",
            "source_nodes": [n.get("id") for n in source_graph.get("nodes", [])] if source_graph is not None else None,
            "drawn_nodes": [n.attrs["data-id"] for svg in svgs for n in svg.walk()
                            if "node" in n.classes() and "data-id" in n.attrs and _drawn(n, svg)],
            "missing_detail_disclosed": "Internal step detail is unavailable" in tree.root.text(),
            "findings": problems(html_text, source_graph)}
