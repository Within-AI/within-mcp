#!/usr/bin/env python3
"""Measured geometry of the A3 renderer's SVG (renderer id `factory-workflow-a3-v1`).

    python3 check-workflow-render-geometry-a3.py --run RUN_ROOT --stage KEY [--file PATH]

Checks every A3 render in RUN_ROOT/stages/KEY (or only PATH, relative to RUN_ROOT when not absolute):
  - canvas: every node box and connector point sits inside the viewBox;
  - overlap: no two node boxes intersect, except a "Sets: ..." chip (`data-role="chip"`) resting on the box
    named by its `data-host` (drawn there by design); a chip on any other box is still an overlap;
  - cut-off: every line of text inside a node box fits that box (width measured with the renderer's own
    font metric), and no text runs off the canvas;
  - line-through-box: no connector segment passes through the interior of a node other than its own ends;
  - binding: when `<stem>.json` sits beside the SVG, `svg_binding` passes against its graph;
  - hidden: a non-drawn element (`data-hidden="replaced"`, a replaced AI use or its edge) is left out of every
    measurement above; it fails when it holds any visible shape (rect, path, polyline, polygon, line, circle,
    ellipse, image, use, or text with content) or carries any other data-hidden value.
Edge labels resting on a node box are reported as flags (they never fail).

Exit 0 pass, 1 fail, 2 setup error; stdout is one JSON object (Pins §8).
"""
import argparse
import json
import os
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "renderer"))
import workflow_contract as wc  # noqa: E402
from render_a3 import MONO, tw  # noqa: E402

CHECK = "workflow-render-geometry-a3"
TEXT_SLACK = 1.0   # px of rounding allowed when text meets a box edge
EDGE_INSET = 0.5   # px a connector may run along a box edge without counting as through it
# Shapes a non-drawn (data-hidden) element may never hold; text counts only when it has content.
HIDDEN_SHAPES = {"rect", "path", "polyline", "polygon", "line", "circle", "ellipse", "image", "use"}


class SetupError(Exception):
    pass


def _tag(el):
    return el.tag.rsplit("}", 1)[-1]


def _intersects(a, b):
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return ax < bx + bw and bx < ax + aw and ay < by + bh and by < ay + ah


def _through(a, b, box):
    """True when segment a-b enters the interior of box (Liang-Barsky clip against the inset box)."""
    x, y, w, h = box
    x0, y0, x1, y1 = x + EDGE_INSET, y + EDGE_INSET, x + w - EDGE_INSET, y + h - EDGE_INSET
    if x1 <= x0 or y1 <= y0:
        return False
    (ax, ay), (bx, by) = a, b
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


def _text_extent(el):
    """(x0, x1, top, bottom) of one SVG <text> line, measured with the renderer's metric."""
    s = "".join(el.itertext())
    size = float(el.get("font-size", "12"))
    weight = el.get("font-weight", "400")
    weight = weight if weight in ("400", "500", "600", "700") else "400"
    mono = el.get("font-family", "") == MONO
    width = tw(s, size, int(weight), mono)
    x, y = float(el.get("x", "0")), float(el.get("y", "0"))
    if el.get("text-anchor") == "middle":
        x0 = x - width / 2
    elif el.get("text-anchor") == "end":
        x0 = x - width
    else:
        x0 = x
    return x0, x0 + width, y - 0.8 * size, y + 0.2 * size, s


def svg_findings(svg_text, graph=None, where="render"):
    """Findings and flags for one A3 SVG (pure). Raises SetupError when the SVG is not an A3 render."""
    findings, flags = [], []

    def fail(rule, detail):
        findings.append({"rule": rule, "where": where, "detail": detail})

    try:
        root = ET.fromstring(svg_text)
    except ET.ParseError as exc:
        raise SetupError("not parseable SVG: %s" % exc)
    if root.get("data-renderer") != wc.A3_RENDERER_ID:
        raise SetupError("not an A3 render (data-renderer=%r)" % root.get("data-renderer"))
    view = wc._floats(root.get("viewBox", ""), 4)
    if view is None or view[2] <= 0 or view[3] <= 0:
        raise SetupError("invalid canvas")
    vx, vy, vw, vh = view

    def inside(x, y):
        return vx - TEXT_SLACK <= x <= vx + vw + TEXT_SLACK and vy - TEXT_SLACK <= y <= vy + vh + TEXT_SLACK

    if graph is not None:
        try:
            wc.svg_binding(svg_text, graph)
        except (ValueError, KeyError, TypeError) as exc:
            fail("binding", str(exc))

    # Non-drawn elements (a replaced AI use and its edges) have no geometry and are left out of the measurements
    # below. The marking is never a way round them: any other data-hidden value, or a marked element holding a
    # visible shape, fails.
    hidden = set()
    for el in root.iter():
        mark = el.get("data-hidden")
        if mark is None:
            continue
        hidden.update(id(p) for p in el.iter())
        gid = el.get("data-graph-node-id") or el.get("data-graph-edge-id") or el.get("id")
        if mark != "replaced":
            fail("hidden-drawn", "unknown data-hidden value %r on %s (only \"replaced\" is allowed)" % (mark, gid))
        shapes = sorted({_tag(p) for p in el.iter() if _tag(p) in HIDDEN_SHAPES
                         or (_tag(p) in ("text", "tspan") and "".join(p.itertext()).strip())})
        if shapes:
            fail("hidden-drawn", "non-drawn element %s holds visible shapes: %s" % (gid, ", ".join(shapes)))

    boxes, groups, hosts = {}, {}, {}
    for el in root.iter():
        nid = el.get("data-graph-node-id")
        if not nid or id(el) in hidden:
            continue
        box = wc._floats(el.get("data-bounds"), 4)
        if box is None or box[2] <= 0 or box[3] <= 0:
            fail("node-bounds", "invalid node bounds: " + nid)
            continue
        x, y, w, h = box
        if not (inside(x, y) and inside(x + w, y + h)):
            fail("canvas", "node outside canvas: " + nid)
        boxes[nid], groups[nid] = tuple(box), el
        if el.get("data-role") == "chip" and el.get("data-host"):
            hosts[nid] = el.get("data-host")

    ordered = list(boxes.items())
    for i, (nid, a) in enumerate(ordered):
        for other, b in ordered[i + 1:]:
            if hosts.get(nid) == other or hosts.get(other) == nid:
                continue  # a chip on its own host box is drawn there by design
            if _intersects(a, b):
                fail("overlap", "overlapping node boxes: %s/%s" % (nid, other))

    # Cut-off text: node text must fit its own box; any text must stay on the canvas.
    in_node = set()
    for nid, g in groups.items():
        x, y, w, h = boxes[nid]
        stack = [g]
        while stack:
            el = stack.pop()
            if el is not g and (el.get("data-role") == "badge" or el.get("data-graph-table-id")):
                continue
            if _tag(el) == "text":
                in_node.add(id(el))
                x0, x1, top, bottom, s = _text_extent(el)
                if x0 < x - TEXT_SLACK or x1 > x + w + TEXT_SLACK or top < y - TEXT_SLACK or bottom > y + h + TEXT_SLACK:
                    fail("cut-off", "text runs past its box: %s: %r" % (nid, s))
                continue
            stack.extend(list(el))
    for el in root.iter():
        if _tag(el) != "text" or id(el) in in_node or id(el) in hidden:
            continue
        x0, x1, top, bottom, s = _text_extent(el)
        if not (inside(x0, top) and inside(x1, bottom)):
            fail("cut-off", "text runs off the canvas: %r" % s)

    for el in root.iter():
        eid = el.get("data-graph-edge-id")
        if not eid or id(el) in hidden:
            continue
        ends = {el.get("data-source"), el.get("data-target")}
        lines = [p for p in el.iter() if _tag(p) == "polyline"]
        if not lines:
            fail("connector", "connector has no line: " + eid)
        for line in lines:
            numbers = [wc._floats(p, 2) for p in (line.get("data-points") or line.get("points") or "").split()]
            if len(numbers) < 2 or not all(numbers):
                fail("connector", "invalid connector points: " + eid)
                continue
            points = [tuple(p) for p in numbers]
            if not all(inside(px, py) for px, py in points):
                fail("canvas", "connector outside canvas: " + eid)
            crossed = set()
            for a, b in zip(points, points[1:]):
                for nid, box in boxes.items():
                    if nid not in ends and nid not in crossed and _through(a, b, box):
                        crossed.add(nid)
                        fail("line-through-box", "connector passes through a node: %s/%s" % (eid, nid))

    for el in root.iter():
        if el.get("data-role") != "edge-label":
            continue
        rect = next((r for r in el.iter() if _tag(r) == "rect"), None)
        pill = rect is not None and wc._floats(" ".join(str(rect.get(k)) for k in ("x", "y", "width", "height")), 4)
        if not pill:
            continue
        for nid, box in boxes.items():
            if _intersects(pill, box):
                flags.append({"rule": "edge-label-on-node", "where": where,
                              "detail": "edge label %s rests on node %s" % (el.get("data-edge"), nid)})
    return findings, flags


def targets(run, stage, file):
    if file:
        path = Path(file) if os.path.isabs(file) else Path(run) / file
        if not path.is_file():
            raise SetupError("no such file: %s" % path)
        return [path]
    folder = Path(run) / "stages" / stage
    if not folder.is_dir():
        raise SetupError("no stage folder: %s" % folder)
    found = []
    for path in sorted(folder.glob("*.svg")):
        try:
            if ET.parse(path).getroot().get("data-renderer") == wc.A3_RENDERER_ID:
                found.append(path)
        except ET.ParseError:
            continue
    if not found:
        raise SetupError("no A3 render in %s" % folder)
    return found


def run_check(run, stage, file=None):
    findings, flags = [], []
    for path in targets(run, stage, file):
        bundle = path.with_suffix(".json")
        graph = None
        if bundle.is_file():
            try:
                graph = json.loads(bundle.read_text(encoding="utf-8")).get("graph")
            except ValueError as exc:
                raise SetupError("unreadable bundle %s: %s" % (bundle, exc))
        try:
            where = str(path.relative_to(run))
        except ValueError:
            where = str(path)
        f, g = svg_findings(path.read_text(encoding="utf-8"), graph, where)
        findings += f
        flags += g
    return findings, flags


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--run", required=True)
    ap.add_argument("--stage", required=True)
    ap.add_argument("--file")
    a = ap.parse_args(argv)
    try:
        findings, flags = run_check(a.run, a.stage, a.file)
    except (SetupError, OSError) as exc:
        print(json.dumps({"check": CHECK, "result": "setup-error",
                          "findings": [{"rule": "setup", "where": a.run, "detail": str(exc)}], "flags": []}))
        return 2
    result = "fail" if findings else "pass"
    print(json.dumps({"check": CHECK, "result": result, "findings": findings, "flags": flags}, indent=1))
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
