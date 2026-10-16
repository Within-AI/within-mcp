#!/usr/bin/env python3
"""Readability geometry of any diagram SVG the plugin draws (A3, workflow bands, agent architecture).

    python3 check-diagram-readability.py --file PATH [--file PATH ...] [--rules edge-through-box,diagonal,...]

Works on the SVG of render_a3.py, render_workflow.py and render_internal_dag.py from their data attributes only:
  edges  groups with `data-graph-edge-id` or `data-edge-id`; the polyline is `data-points` on a child (each child
         holding one is a line of its own) or else on the group; the ends are `data-source`/`data-target` or
         `data-from`/`data-to`;
  boxes  groups with `data-graph-node-id`, or `data-id` + `data-type`, with `data-bounds` = "x y w h"; an element
         with `data-rail-y` is a rail, not a box;
  text   every visible <text> line (not inside defs, marker, title, desc, style, a `data-hidden` element or a
         display:none / visibility:hidden one). Its extent is measured with the A3 renderer's own Inter metric
         (`render_a3.tw`); when that cannot be imported, with PER_CHAR x font size per character. Letter-spacing,
         text-anchor, tspans and transforms (translate, rotate, scale, matrix) are applied; the line runs from
         0.8 x size above the baseline to 0.2 x size below it.
Rules (each finding names its rule):
  edge-through-box   a segment passes through the interior (BOX_INSET px inset) of a box that is not one of its ends;
  diagonal           a segment that is neither horizontal nor vertical;
  line-through-text  a segment crosses a visible text line (TEXT_INSET px inset), except text belonging to the edge's
                     own label (inside the edge group, or under an element whose `data-label-for` / `data-edge`
                     names the edge);
  text-overlap       two visible text lines overlap by more than OVERLAP px both ways;
  cut-off-label      a visible text line ends with "…" or "..." (its text was truncated);
  raw-id-visible     a visible text line, a tspan run of one, or a whole token of one (delimited by characters that
                     cannot be part of an id) equals an id held in the SVG's own id data attributes (ID_ATTRS).
                     Structural: the ids come from the SVG itself, never from a word list or a pattern.
No rule reads label wording: every decision comes from data attributes and geometry.

Exit 0 pass, 1 fail, 2 setup error; stdout is one JSON object with findings and per-file, per-rule counts.
"""
import argparse
import json
import math
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "renderer"))
try:
    from render_a3 import tw as _a3_tw  # noqa: E402
except Exception:  # pragma: no cover - the renderer ships beside this check; the fallback keeps the check usable
    _a3_tw = None

CHECK = "diagram-readability"
RULES = ("edge-through-box", "diagonal", "line-through-text", "text-overlap", "cut-off-label", "raw-id-visible")
BOX_INSET = 2.0      # px: a connector may run along a box edge without counting as through it
TEXT_INSET = 1.0     # px: a connector grazing a text line's estimated extent does not count
OVERLAP = 3.0        # px: two text lines must overlap by more than this, in x and in y, to count
STRAIGHT_EPS = 0.01  # px: a segment whose dx and dy both exceed this is diagonal
PER_CHAR = 0.56      # fallback width per character, as a fraction of the font size (Inter, mixed case)
DEFAULT_SIZE = 16.0  # SVG default font size
# Data attributes that hold a node, edge, interface, table or process id in any of the three renderers.
ID_ATTRS = ("data-graph-node-id", "data-graph-edge-id", "data-graph-table-id", "data-edge-id", "data-id",
            "data-source", "data-target", "data-from", "data-to", "data-label-for", "data-edge", "data-host",
            "data-process")
# Characters that are never part of an id in these renderers' id data attributes; they delimit tokens.
ID_CHARS = re.compile(r"[A-Za-z0-9_.:#\-]")
NOT_DRAWN = {"defs", "marker", "symbol", "clipPath", "mask", "pattern", "title", "desc", "metadata", "style"}
INHERITED = ("font-size", "font-weight", "font-family", "text-anchor", "letter-spacing")


class SetupError(Exception):
    pass


def _tag(el):
    return el.tag.rsplit("}", 1)[-1]


def _num(value, default=None):
    try:
        return float(str(value).strip().rstrip("px"))
    except (TypeError, ValueError):
        return default


def _points(text):
    vals = [v for v in re.split(r"[\s,]+", (text or "").strip()) if v]
    nums = [_num(v) for v in vals]
    if len(nums) < 4 or len(nums) % 2 or any(n is None for n in nums):
        return None
    return list(zip(nums[0::2], nums[1::2]))


def _bounds(text):
    vals = [_num(v) for v in re.split(r"[\s,]+", (text or "").strip()) if v]
    if len(vals) != 4 or any(v is None for v in vals) or vals[2] <= 0 or vals[3] <= 0:
        return None
    return tuple(vals)


# ------------------------------------------------------------------ transforms
def _mul(a, b):
    a0, a1, a2, a3, a4, a5 = a
    b0, b1, b2, b3, b4, b5 = b
    return (a0 * b0 + a2 * b1, a1 * b0 + a3 * b1, a0 * b2 + a2 * b3, a1 * b2 + a3 * b3,
            a0 * b4 + a2 * b5 + a4, a1 * b4 + a3 * b5 + a5)


IDENTITY = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


def _transform(text):
    """Affine matrix (a, b, c, d, e, f) of an SVG transform attribute; None when it cannot be read."""
    m = IDENTITY
    if not text:
        return m
    for name, args in re.findall(r"(\w+)\s*\(([^)]*)\)", text):
        v = [_num(x) for x in re.split(r"[\s,]+", args.strip()) if x]
        if any(x is None for x in v):
            return None
        if name == "translate" and v:
            t = (1, 0, 0, 1, v[0], v[1] if len(v) > 1 else 0)
        elif name == "scale" and v:
            t = (v[0], 0, 0, v[1] if len(v) > 1 else v[0], 0, 0)
        elif name == "rotate" and v:
            r = math.radians(v[0])
            c, s = math.cos(r), math.sin(r)
            t = (c, s, -s, c, 0, 0)
            if len(v) == 3:
                t = _mul(_mul((1, 0, 0, 1, v[1], v[2]), t), (1, 0, 0, 1, -v[1], -v[2]))
        elif name == "matrix" and len(v) == 6:
            t = tuple(v)
        else:
            return None
        m = _mul(m, t)
    return m


def _apply(m, box):
    x0, y0, x1, y1 = box
    a, b, c, d, e, f = m
    pts = [(a * x + c * y + e, b * x + d * y + f) for x, y in ((x0, y0), (x1, y0), (x0, y1), (x1, y1))]
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return min(xs), min(ys), max(xs), max(ys)


# ------------------------------------------------------------------ geometry
def _through(a, b, box, inset):
    """True when segment a-b enters the interior of box (x0, y0, x1, y1) shrunk by inset (Liang-Barsky clip)."""
    x0, y0, x1, y1 = box[0] + inset, box[1] + inset, box[2] - inset, box[3] - inset
    if x1 <= x0 or y1 <= y0:
        return False
    (ax, ay), (bx, by) = a, b
    if max(ax, bx) <= x0 or min(ax, bx) >= x1 or max(ay, by) <= y0 or min(ay, by) >= y1:
        return False
    dx, dy = bx - ax, by - ay
    lo, hi = 0.0, 1.0
    for p, q in ((-dx, ax - x0), (dx, x1 - ax), (-dy, ay - y0), (dy, y1 - ay)):
        if p == 0:
            if q <= 0:
                return False
            continue
        t = q / p
        if p < 0:
            lo = max(lo, t)
        else:
            hi = min(hi, t)
        if lo >= hi:
            return False
    return True


def text_width(s, size, weight="400", family="", spacing=0.0):
    """Width of a run of text: the A3 renderer's Inter metric, else PER_CHAR per character; plus letter-spacing."""
    w = str(weight).strip().lower()
    w = {"normal": "400", "bold": "700", "bolder": "700", "lighter": "400"}.get(w, w)
    wn = _num(w, 400.0)
    weight = min((400, 500, 600, 700), key=lambda k: abs(k - wn))
    mono = "monospace" in family
    if _a3_tw is not None:
        width = _a3_tw(s, size, weight, mono)
    else:
        width = len(s) * size * PER_CHAR
    return width + spacing * len(s)


def _spacing(value, size):
    if not value or value == "normal":
        return 0.0
    value = str(value).strip()
    if value.endswith("em"):
        return (_num(value[:-2], 0.0) or 0.0) * size
    return _num(value, 0.0) or 0.0


# ------------------------------------------------------------------ the check
def _collect(root):
    parent = {c: p for p in root.iter() for c in p}
    hidden, owners_of, matrix_of = set(), {}, {}

    def chain(el):
        out = []
        while el is not None:
            out.append(el)
            el = parent.get(el)
        return out

    for el in root.iter():
        p = parent.get(el)
        style = el.get("style", "")
        if (p is not None and id(p) in hidden) or _tag(el) in NOT_DRAWN or el.get("data-hidden") is not None \
                or el.get("display") == "none" or el.get("visibility") == "hidden" \
                or re.search(r"display\s*:\s*none|visibility\s*:\s*hidden", style):
            hidden.add(id(el))
    return parent, hidden, chain


def _attr(chain, name, default=None):
    for el in chain:
        v = el.get(name)
        if v is not None:
            return v
    return default


def svg_findings(svg_text, rules=RULES, where="render"):
    """Findings for one diagram SVG (pure). Raises SetupError when it is not parseable SVG."""
    try:
        root = ET.fromstring(svg_text)
    except ET.ParseError as exc:
        raise SetupError("not parseable SVG: %s" % exc)
    if _tag(root) != "svg":
        raise SetupError("not an SVG document (root <%s>)" % _tag(root))
    rules = set(rules)
    findings = []

    def fail(rule, detail):
        findings.append({"rule": rule, "where": where, "detail": detail})

    parent, hidden, chain = _collect(root)

    ids = set()
    for el in root.iter():
        for name in ID_ATTRS:
            v = el.get(name)
            if v:
                ids.add(v)

    boxes = []
    for el in root.iter():
        nid = el.get("data-graph-node-id") or (el.get("data-id") if el.get("data-type") else None)
        if not nid or id(el) in hidden or el.get("data-rail-y") is not None:
            continue
        b = _bounds(el.get("data-bounds"))
        if b is not None:
            boxes.append((nid, (b[0], b[1], b[0] + b[2], b[1] + b[3])))

    edges = []
    for el in root.iter():
        eid = el.get("data-graph-edge-id") or el.get("data-edge-id")
        if not eid or id(el) in hidden:
            continue
        ends = {el.get(k) for k in ("data-source", "data-target", "data-from", "data-to")} - {None}
        holders = [c for c in el.iter() if c is not el and c.get("data-points") and id(c) not in hidden]
        lines = [_points(c.get("data-points")) for c in holders] or [_points(el.get("data-points"))]
        lines = [ln for ln in lines if ln]
        edges.append((eid, ends, lines))

    texts = []   # (box, string, owner edge ids, runs)
    for el in root.iter():
        if _tag(el) != "text" or id(el) in hidden:
            continue
        ch = chain(el)
        size = _num(_attr(ch, "font-size"), DEFAULT_SIZE)
        runs = []

        def run(s, attrs_chain):
            if s:
                runs.append((s, attrs_chain))
        run(el.text, ch)
        for c in el:
            if _tag(c) == "tspan" and id(c) not in hidden:
                run("".join(c.itertext()), chain(c))
            run(c.tail, ch)
        s = "".join(r[0] for r in runs)
        if not s.strip():
            continue
        width = 0.0
        for rs, rc in runs:
            rsize = _num(_attr(rc, "font-size"), size)
            width += text_width(rs, rsize, _attr(rc, "font-weight", "400"), _attr(rc, "font-family", ""),
                                _spacing(_attr(rc, "letter-spacing"), rsize))
        x, y = _num(el.get("x"), 0.0), _num(el.get("y"), 0.0)
        anchor = _attr(ch, "text-anchor", "start")
        x0 = x - width / 2 if anchor == "middle" else x - width if anchor == "end" else x
        box = (x0, y - 0.8 * size, x0 + width, y + 0.2 * size)
        m, readable = IDENTITY, True
        for anc in reversed(ch):
            t = _transform(anc.get("transform"))
            if t is None:
                readable = False
                break
            m = _mul(m, t)
        if not readable:
            continue   # an unreadable transform: its place is unknown, so it is left out rather than guessed
        box = _apply(m, box)
        owners = set()
        for anc in ch:
            for k in ("data-graph-edge-id", "data-edge-id", "data-label-for", "data-edge"):
                if anc.get(k):
                    owners.add(anc.get(k))
        texts.append((box, s, owners, runs))

    def short(s):
        s = " ".join(s.split())
        return s if len(s) <= 60 else s[:57] + "..."

    if "edge-through-box" in rules or "diagonal" in rules or "line-through-text" in rules:
        for eid, ends, lines in edges:
            crossed, over = set(), set()
            for pts in lines:
                for a, b in zip(pts, pts[1:]):
                    if a == b:
                        continue
                    if "diagonal" in rules and abs(a[0] - b[0]) > STRAIGHT_EPS and abs(a[1] - b[1]) > STRAIGHT_EPS:
                        fail("diagonal", "edge %s has a diagonal segment %g,%g -> %g,%g" % (eid, a[0], a[1], b[0], b[1]))
                    if "edge-through-box" in rules:
                        for nid, box in boxes:
                            if nid not in ends and nid not in crossed and _through(a, b, box, BOX_INSET):
                                crossed.add(nid)
                                fail("edge-through-box", "edge %s passes through box %s" % (eid, nid))
                    if "line-through-text" in rules:
                        for k, (tbox, s, owners, _runs) in enumerate(texts):
                            if k in over or eid in owners:
                                continue
                            if _through(a, b, tbox, TEXT_INSET):
                                over.add(k)
                                fail("line-through-text", "edge %s crosses text %r at %g,%g"
                                     % (eid, short(s), tbox[0], tbox[3]))

    if "text-overlap" in rules:
        order = sorted(range(len(texts)), key=lambda k: texts[k][0][0])
        for i, k in enumerate(order):
            a = texts[k][0]
            for j in order[i + 1:]:
                b = texts[j][0]
                if b[0] >= a[2] - OVERLAP:
                    break
                ix = min(a[2], b[2]) - max(a[0], b[0])
                iy = min(a[3], b[3]) - max(a[1], b[1])
                if ix > OVERLAP and iy > OVERLAP:
                    fail("text-overlap", "text %r overlaps text %r at %g,%g"
                         % (short(texts[k][1]), short(texts[j][1]), max(a[0], b[0]), max(a[1], b[1])))

    if "cut-off-label" in rules:
        for box, s, _o, _r in texts:
            t = s.strip()
            if t.endswith("…") or t.endswith("..."):
                fail("cut-off-label", "truncated text %r at %g,%g" % (short(s), box[0], box[3]))

    if "raw-id-visible" in rules and ids:
        for box, s, _o, runs in texts:
            shown = set()
            for piece in [s] + [r[0] for r in runs]:
                if piece.strip() in ids:
                    shown.add(piece.strip())
            for tok in _tokens(s):
                if tok in ids:
                    shown.add(tok)
            for v in sorted(shown):
                fail("raw-id-visible", "id %r is visible in text %r at %g,%g" % (v, short(s), box[0], box[3]))
    return findings


def _tokens(s):
    """Maximal runs of id characters (ID_CHARS), with trailing sentence punctuation (. :) trimmed."""
    out, cur = [], []
    for ch in s:
        if ID_CHARS.match(ch):
            cur.append(ch)
        else:
            if cur:
                out.append("".join(cur))
            cur = []
    if cur:
        out.append("".join(cur))
    return [t.rstrip(".:") or t for t in out]


def counts(findings):
    out = {}
    for f in findings:
        per = out.setdefault(f["where"], {r: 0 for r in RULES})
        per[f["rule"]] = per.get(f["rule"], 0) + 1
    return out


def run_check(files, rules=RULES):
    findings, per_file = [], {}
    for name in files:
        path = Path(name)
        if not path.is_file():
            raise SetupError("no such file: %s" % path)
        found = svg_findings(path.read_text(encoding="utf-8"), rules, str(path))
        per_file[str(path)] = {r: sum(1 for f in found if f["rule"] == r) for r in RULES if r in rules}
        findings += found
    return findings, per_file


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--file", action="append", required=True, help="an SVG to check (repeatable)")
    ap.add_argument("--rules", help="comma-separated subset of: " + ", ".join(RULES))
    a = ap.parse_args(argv)
    rules = RULES
    try:
        if a.rules:
            rules = tuple(r.strip() for r in a.rules.split(",") if r.strip())
            unknown = [r for r in rules if r not in RULES]
            if unknown or not rules:
                raise SetupError("unknown rule(s): %s (known: %s)" % (", ".join(unknown) or "none", ", ".join(RULES)))
        findings, per_file = run_check(a.file, rules)
    except (SetupError, OSError, UnicodeDecodeError) as exc:
        print(json.dumps({"check": CHECK, "result": "setup-error",
                          "findings": [{"rule": "setup", "where": ",".join(a.file), "detail": str(exc)}],
                          "counts": {}}))
        return 2
    result = "fail" if findings else "pass"
    print(json.dumps({"check": CHECK, "result": result, "rules": list(rules), "counts": per_file,
                      "findings": findings}, indent=1))
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
