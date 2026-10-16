"""Agent Design architecture: authored graph through the shared knowledge renderer.

The legacy diagram_data API retains its three-list projection. Graph-less runs show
coarse ownership only; render never synthesizes an internal graph from that projection.
"""
import html
import importlib.util
import json
import os
import re
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
AGENT = "stages/architecture/agent.yaml"
GRAPH = "stages/architecture/internal-dag.graph.json"
BOUNDARIES = "stages/boundaries/boundaries.json"
DIRECTION = "top-to-bottom"

TITLE_SIZE, TITLE_WEIGHT = 14, "600"
BADGE_SIZE, BADGE_WEIGHT = 11, "600"
PAD = 12
MIN_BOX_W, BOX_H = 168, 64
GAP_X, GAP_Y, MARGIN = 40, 72, 24

FRAME_CSS = """<style>
.rp-figure { border:1px solid var(--border, #d9d4cc); border-radius:10px; margin:12px 0; background:var(--white, #fff); }
.rp-zoom-radio { position:fixed; top:0; left:0; opacity:0; width:1px; height:1px; margin:0; pointer-events:none; }
.rp-zoom-bar { display:flex; align-items:center; gap:6px; padding:8px 10px; border-bottom:1px solid var(--border, #d9d4cc); flex-wrap:wrap; }
.rp-zoom-bar label { border:1px solid var(--border, #d9d4cc); background:var(--white, #fff); border-radius:8px; padding:4px 10px; font-size:.8rem; font-weight:600; cursor:pointer; }
.rp-zoom-hint { margin-left:auto; font-size:.75rem; color:var(--grey, #6b6b6b); }
.rp-zoom-read:checked ~ .rp-zoom-bar .rp-zoom-l-read, .rp-zoom-fit:checked ~ .rp-zoom-bar .rp-zoom-l-fit { border-color:#1d2b3a; background:var(--cream, #f7f3ec); }
.rp-zoom-read:focus-visible ~ .rp-zoom-bar .rp-zoom-l-read, .rp-zoom-fit:focus-visible ~ .rp-zoom-bar .rp-zoom-l-fit { outline:2px solid #e8604c; outline-offset:2px; }
.rp-zoom-view { overflow:auto; max-height:86vh; min-height:200px; background:var(--white, #fff); }
.rp-zoom-view svg { display:block; max-width:none; }
.rp-zoom-fit:checked ~ .rp-zoom-view svg { width:100% !important; min-width:0 !important; max-width:100% !important; height:auto !important; max-height:84vh; }
</style>"""

CAPTIONS = {
    "top-to-bottom": "Read from top to bottom: the orchestration layer hands work down to each sub-agent, and the "
                     "bottom row shows the people the agents hand work to or take it back from.",
    "left-to-right": "Read from left to right: the orchestration layer hands work across to each sub-agent, and the "
                     "last column shows the people the agents hand work to or take it back from.",
}


def _load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _boundaries(run_dir):
    path = os.path.join(run_dir, BOUNDARIES)
    if not os.path.isfile(path):
        return []
    data = _load(path)
    items = data.get("boundaries", []) if isinstance(data, dict) else data
    return [b for b in items if isinstance(b, dict)]


def _is_person(end):
    return isinstance(end, str) and end.startswith("person:")


def diagram_data(run_dir):
    """parts, person_handovers (one per person-side boundary of each person_handover, with its boundary_id; None when
    no boundary carries one of the hand-over's edge_refs) and writes (each from header.proposed_writes, with grant)."""
    agent = _load(os.path.join(run_dir, AGENT))
    header = agent.get("header", {})
    parts = [{"id": p["id"], "role": p.get("role"), "routing": (p.get("routing") or {}).get("kind")}
             for p in agent.get("parts", [])]
    writes = [{"step": w.get("step"), "system": w.get("system"), "grant": w.get("grant"), "status": w.get("status")}
              for w in header.get("proposed_writes", [])]
    boundaries = _boundaries(run_dir)
    handovers = []
    for h in header.get("handovers", []):
        if h.get("kind") != "person_handover":
            continue
        person = h["to"] if _is_person(h.get("to")) else h.get("from")
        part = h.get("from") if person == h.get("to") else h.get("to")
        found = False
        for ref in h.get("edge_refs", []):
            for b in boundaries:
                if b.get("edge_ref") != ref:
                    continue
                sides = [(b.get("from") or {}).get("side"), (b.get("to") or {}).get("side")]
                if "person" not in sides:
                    continue
                found = True
                handovers.append({"from": h.get("from"), "to": h.get("to"), "part": part, "person": person,
                                  "edge_ref": ref, "boundary_id": b.get("id")})
        if not found:
            handovers.append({"from": h.get("from"), "to": h.get("to"), "part": part, "person": person,
                              "edge_ref": (h.get("edge_refs") or [None])[0], "boundary_id": None})
    return {"parts": parts, "person_handovers": handovers, "writes": writes}


# ---- drawing -------------------------------------------------------------------------------------------------

_WIDTHS = None


def _knowledge(*parts):
    """A path under the plugin's knowledge folder, found through the one lookup
    (skills/fast-mode-build/driver/knowledge_root.py); "" when no knowledge folder is found."""
    spec = importlib.util.spec_from_file_location(
        "knowledge_root", os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(HERE)))), "skills", "fast-mode-build", "driver", "knowledge_root.py"))
    lookup = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(lookup)
    try:
        return os.path.join(lookup.find(), *parts)
    except lookup.KnowledgeNotFound:
        return ""


def _width(text, size, weight):
    global _WIDTHS
    if _WIDTHS is None:
        _WIDTHS = _load(_knowledge("renderer", "a3_inter_widths.json"))
    row = _WIDTHS[weight]
    widest = max(row.values())
    return sum(row.get(ch, widest) for ch in text) * size


def plain(ref):
    """A reader's name for a part id or person:<lane_id>: words, first letter capital."""
    text = ref.split(":", 1)[1] if _is_person(ref) else ref
    text = re.sub(r"[_\-.]+", " ", text).strip() or ref
    return text[:1].upper() + text[1:]


def _e(text):
    return html.escape(str(text))


def _box(x, y, w, title, badge, cls):
    bw = _width(badge, BADGE_SIZE, BADGE_WEIGHT) + 2 * 6 + 2
    return ('<g class="%s"><rect x="%g" y="%g" width="%g" height="%g" rx="8" fill="#fff" stroke="#1d2b3a"/>'
            '<g class="badge"><rect x="%g" y="%g" width="%g" height="16" rx="8" fill="#f7f3ec"/>'
            '<text x="%g" y="%g" font-size="%d" font-weight="%s" text-anchor="middle" fill="#1d2b3a">%s</text></g>'
            '<text x="%g" y="%g" font-size="%d" font-weight="%s" text-anchor="middle" fill="#1d2b3a">%s</text></g>'
            % (cls, x, y, w, BOX_H, x + 8, y + 6, bw, x + 8 + bw / 2, y + 18, BADGE_SIZE, BADGE_WEIGHT, _e(badge),
               x + w / 2, y + 48, TITLE_SIZE, TITLE_WEIGHT, _e(title)))


def _box_w(title, badge):
    need = max(_width(title, TITLE_SIZE, TITLE_WEIGHT) + 2 * PAD + 4, _width(badge, BADGE_SIZE, BADGE_WEIGHT) + 32)
    return max(MIN_BOX_W, round(need + 0.5))


def _edge(points, back=False):
    pts = " ".join("%g,%g" % p for p in points)
    marker = 'marker-start="url(#ad-arrow-back)"' if back else 'marker-end="url(#ad-arrow)"'
    return '<polyline points="%s" fill="none" stroke="#1d2b3a" stroke-width="1.5" %s/>' % (pts, marker)


def _row(items, y, x0=MARGIN):
    """[(ref, title, badge)] -> {ref: (x, y, w)} laid out left to right."""
    out, x = {}, x0
    for ref, title, badge in items:
        w = _box_w(title, badge)
        out[ref] = (x, y, w)
        x += w + GAP_X
    return out


def svg(data):
    parts = data["parts"]
    # The producer requires an orchestration role even for one-part designs.
    # Explicit code-only routing describes a script, not agent orchestration.
    scripted = bool(parts) and all(p.get("routing") == "code" for p in parts)
    orchestration = len(parts) > 1
    orch = [p for p in parts if p["role"] == "orchestration" and orchestration]
    subs = [p for p in parts if p not in orch]
    def badge(part):
        if scripted or (len(parts) == 1 and part.get("role") == "orchestration"):
            return {"code": "Script", "model": "Agent"}.get(part.get("routing"), "Role not stated")
        return plain(part.get("role") or "Role not stated")
    people = []
    for h in data["person_handovers"]:
        if h["person"] not in people:
            people.append(h["person"])
    y1 = MARGIN
    y2 = y1 + (BOX_H + GAP_Y if orch else 0)
    y3 = y2 + BOX_H + GAP_Y
    row1 = _row([(p["id"], plain(p["id"]), badge(p)) for p in orch], y1)
    row2 = _row([(p["id"], plain(p["id"]), badge(p)) for p in subs], y2)
    row3 = _row([(r, plain(r), "Person") for r in people], y3)
    where = dict(row1, **row2)
    right = max([x + w for x, _, w in list(row1.values()) + list(row2.values()) + list(row3.values())] + [MARGIN])
    lane_x = right + GAP_X / 2  # the clear column right of every box, for edges leaving the first row
    bus1, bus2 = y2 - GAP_Y / 2, y3 - GAP_Y / 2
    edges = []
    if orch:
        ox, oy, ow = row1[orch[0]["id"]]
        for p in subs:
            sx, sy, sw = row2[p["id"]]
            edges.append(_edge([(ox + ow / 2, oy + BOX_H), (ox + ow / 2, bus1), (sx + sw / 2, bus1), (sx + sw / 2, sy)]))
    for i, h in enumerate(data["person_handovers"]):
        if h["part"] not in where:
            continue
        px, py, pw = where[h["part"]]
        tx, ty, tw = row3[h["person"]]
        back = h["from"] == h["person"]
        off = min(pw / 2 - 12, 10 + 6 * i)
        start = (px + pw / 2 + off, py + BOX_H)
        if h["part"] in row1:
            pts = [start, (start[0], bus1 - 6), (lane_x, bus1 - 6), (lane_x, bus2), (tx + tw / 2, bus2), (tx + tw / 2, ty)]
        else:
            pts = [start, (start[0], bus2 + 6), (tx + tw / 2, bus2 + 6), (tx + tw / 2, ty)]
        edges.append(_edge(pts, back))
    width = max(right, lane_x) + MARGIN
    height = (y3 + BOX_H if people else y2 + BOX_H) + MARGIN
    boxes = [_box(x, y, w, plain(r), "Script" if scripted else "Orchestration", "ad-orchestration") for r, (x, y, w) in row1.items()]
    roles = {p["id"]: badge(p) for p in subs}
    boxes += [_box(x, y, w, plain(r), roles[r], "ad-sub-agent") for r, (x, y, w) in row2.items()]
    boxes += [_box(x, y, w, plain(r), "Person", "ad-person") for r, (x, y, w) in row3.items()]
    defs = ('<defs><marker id="ad-arrow" viewBox="0 0 10 10" refX="10" refY="5" markerWidth="7" markerHeight="7" '
            'orient="auto"><path d="M0,0 L10,5 L0,10 Z" fill="#1d2b3a"/></marker>'
            '<marker id="ad-arrow-back" viewBox="0 0 10 10" refX="0" refY="5" markerWidth="7" markerHeight="7" '
            'orient="auto"><path d="M10,0 L0,5 L10,10 Z" fill="#1d2b3a"/></marker></defs>')
    return ('<svg xmlns="http://www.w3.org/2000/svg" class="ad-agent-diagram" data-direction="%s" width="%g" '
            'height="%g" viewBox="0 0 %g %g" role="img" aria-label="Agent diagram">%s%s%s</svg>'
            % (DIRECTION, width, height, width, height, defs, "".join(edges), "".join(boxes)))


def figure(svg_text, direction=DIRECTION, key=None, caption=None):
    """PR #51's frame (figure_frames): two radios, Readable size (default) and Fit all, a view that scrolls."""
    key = key or "ad-" + uuid.uuid4().hex
    svg_text = _namespace_svg(svg_text, key)
    return ('<figure class="ad-figure"><div class="rp-figure">'
            '<input type="radio" class="rp-zoom-radio rp-zoom-read" name="{k}" id="{k}-read" checked>'
            '<input type="radio" class="rp-zoom-radio rp-zoom-fit" name="{k}" id="{k}-fit">'
            '<div class="rp-zoom-bar"><label class="rp-zoom-l-read" for="{k}-read">Readable size</label>'
            '<label class="rp-zoom-l-fit" for="{k}-fit">Fit all</label>'
            '<span class="rp-zoom-hint">Opens at a readable size. Scroll inside the frame to pan. Fit all shows the '
            'whole diagram.</span></div>'
            '<div class="rp-zoom-view">{svg}</div></div>'
            '<figcaption>{cap}</figcaption></figure>').format(k=key, svg=svg_text, cap=_e(caption if caption is not None else CAPTIONS[direction]))


def _namespace_svg(markup, prefix):
    """Scope SVG definitions as well as viewer controls when a page embeds twice."""
    ids = re.findall(r'(?<![\w-])id="([^"]+)"', markup)
    for ident in ids:
        new = prefix + "-" + ident
        markup = markup.replace('id="' + ident + '"', 'id="' + new + '"')
        markup = markup.replace('url(#' + ident + ')', 'url(#' + new + ')')
        markup = markup.replace('href="#' + ident + '"', 'href="#' + new + '"')
    return markup


def _shared_renderer():
    path = _knowledge("renderer", "render_internal_dag.py")
    spec = importlib.util.spec_from_file_location("ad_internal_dag", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _projection_findings(graph, agent):
    """Compare explicit future-state ownership only; never guess from titles."""
    owners = {}
    for part in agent.get("parts", []):
        for ref in part.get("owns_steps", []):
            owners.setdefault(ref, []).append(part["id"])
    findings = []
    parts = {p["id"] for p in agent.get("parts", [])}
    for node in graph.get("nodes", []):
        ref, owner = node.get("fs_ref"), node.get("sub_agent")
        if owner and owner not in parts:
            findings.append("Graph owner %s is absent from the ownership projection." % owner)
        if ref and node.get("type") != "review_seam":
            matches = owners.get(ref, [])
            if len(matches) != 1 or (owner and owner not in matches):
                findings.append("Ownership differs for %s: graph %s; projection %s." %
                                (ref, owner or "not stated", ", ".join(matches) or "not stated"))
    return findings


def _graph_render(run_dir):
    path = os.path.join(run_dir, GRAPH)
    graph = _load(path)
    shared = _shared_renderer()
    try:
        dag = shared.Dag(graph, path)
    except shared.Unreadable as exc:
        raise ValueError(str(exc)) from exc
    dag.build_svg()
    drawing = _namespace_svg(dag.svg_with_titles(), "ad-" + uuid.uuid4().hex)
    out = ['<style>' + shared.VIEWER_CSS + '</style>',
           shared.viewer(drawing, shared.svg_size(drawing)[0],
                         detail=shared.detail_items(drawing), caption="Authored agent architecture")]
    agent = _load(os.path.join(run_dir, AGENT))
    # Preserve explicit labels and ownership even when the shared drawing abbreviates
    # them. These are source records, not an inferred execution or authority model.
    out.append('<details class="ad-architecture-source"><summary>Architecture source detail</summary>')
    for key in ("nodes", "edges", "interfaces", "sub_agents", "assumptions", "open_register"):
        out.append('<h4>%s</h4><dl>' % _e(plain(key)))
        for row in graph.get(key, []):
            title = row.get("title") or row.get("label") or row.get("id") or plain(key)
            out.append('<dt>%s</dt><dd><pre>%s</pre></dd>' %
                       (_e(title), _e(json.dumps(row, ensure_ascii=False, indent=2))))
        out.append('</dl>')
    out.append('<h4>Responsibilities and ownership</h4><pre>%s</pre></details>' %
               _e(json.dumps(agent.get("parts", []), ensure_ascii=False, indent=2)))
    findings = ["%s: %s" % (name, ", ".join(ids))
                for _, name, ids in dag.structure_rows() if ids]
    findings += ["%s: %s" % (ident or "Diagram", note) for ident, note in dag.notes]
    findings += _projection_findings(graph, agent)
    out.append('<details class="ad-architecture-findings"><summary>Architecture findings (%d)</summary><ul>%s</ul></details>' %
               (len(findings), "".join('<li>%s</li>' % _e(f) for f in findings)))
    return out


def render(run_dir):
    data = diagram_data(run_dir)
    out = [FRAME_CSS]
    graph_path = os.path.join(run_dir, GRAPH)
    if os.path.isfile(graph_path):
        try:
            out.extend(_graph_render(run_dir))
        except (ValueError, OSError, KeyError, TypeError) as exc:
            # Unreadable graph detail does not erase the available ownership draft.
            out.append('<p>Authored architecture could not be rendered: %s</p>' % _e(exc))
            out.extend(_legacy_render(data))
    else:
        out.extend(_legacy_render(data))
    if data["writes"]:
        items = "".join("<li>%s</li>" % _e("A proposed write to %s, waiting for a person to ratify it." % w["system"])
                        for w in data["writes"])
        out.append("<h4>Proposed writes</h4><ul>%s</ul>" % items)
    return "\n".join(out)


def _legacy_render(data):
    caption = ("Read from top to bottom: available parts and people handoffs. "
               "This coarse ownership view does not specify internal execution order.")
    return [figure(svg(data), caption=caption),
            '<p>Internal step detail is unavailable: no readable authored architecture graph. '
            'System reads, states and return conditions are shown only where recorded in the source.</p>']
