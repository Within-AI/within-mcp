"""Deterministic band layout and SVG for workflow graphs (schema version 2). Stdlib only.

Bands show who does the work: Analyst, Agent and System, plus any other lane the graph
uses. Each agent lane (`Agent` or `Agent: <name>`) gets its own band, labelled with the lane
name; agent bands sit together in the agent position. Columns are longest-path depth over the control flow; systems are full-width rails
in the System band that reads and writes drop onto. Every position comes from the graph
and fixed constants; iteration follows graph order only, so the output is byte-stable.
Anything the renderer has not seen before (a node type, lane, link kind, surface kind)
is drawn generically under its own label. No model call, no network.
"""
import html
import json
from pathlib import Path
import sys
import textwrap

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "checks"))
from workflow_contract import (FUTURE, KNOWN_EDGE_KINDS, KNOWN_NODE_TYPES, KNOWN_SURFACE_KINDS,  # noqa: E402
                               KNOWN_VARIANTS, RENDERER_ID, graph_notes, graph_shape, id_list, is_agent_lane,
                               lane_kind, topology)

CORAL, MIDNIGHT, WHITE, CREAM = "#F56740", "#0B1E2F", "#FFFFFF", "#FBF6EF"
SUN, SKY, ROSE = "#FFF5D2", "#CFF3FE", "#FFE0D8"
CORAL_INK, MUTED, FAINT, ACCESS = "#B23A1E", "#4A5560", "#A79E92", "#2E6E86"
FONT = "Inter, -apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif"

X0, PITCH = 230, 290
NODE_W, NODE_H, DIA_HW, DIA_HH, RES_W, RES_H = 186, 122, 54, 46, 176, 54
STACK, RES_STACK = 142, 82
LANE_LEFT, SPINE_W, RAIL_GAP = 96, 36, 33
TRACK, GUTTER_STEP, STUB = 18, 7, 10
# A decision's text (title, reviews, id, tables) sits beside its vertical axis, right-aligned DEC_GAP left of it,
# so every line can leave or enter the diamond at a vertex and fan out on the other side (DEC_FAN apart).
DEC_GAP, DEC_FAN, DEC_WRAP = 8, 11, 14
# The first text baseline under a decision: below the stub row (STUB) its bottom-vertex lines turn along.
DEC_TEXT = 25
GUTTER_TRACKS = (PITCH - NODE_W) // GUTTER_STEP
RAIL_X0 = LANE_LEFT + SPINE_W + 8

TYPE_TAGS = {"read_interaction": "READ", "write_interaction": "WRITE", "analyst_reconciliation": "RECONCILE",
             "analyst_transfer": "TRANSFER", "agent_read": "READ", "agent_reconciliation": "RECOMMEND",
             "agent_stage": "STAGE", "system_result": "SYSTEM RESULT"}
SURFACE_FILL = {"system_of_record": SKY, "channel": ROSE, "review_surface": SUN}
BAND_FILL = {"Analyst": (CREAM, "1"), "Agent": (SKY, "0.35")}


def esc(value):
    return html.escape(str(value), quote=True)


def num(value):
    text = ("%.2f" % value).rstrip("0").rstrip(".")
    return "0" if text in ("-0", "") else text


def cut(text, limit):
    text = str(text)
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def wrap(text, width, maxlines):
    lines = textwrap.wrap(str(text), width, break_long_words=True) or [""]
    if len(lines) > maxlines:
        lines = lines[:maxlines]
        lines[-1] = lines[-1][:width - 1].rstrip() + "…"
    return lines


def derived_short(title, limit=None):
    """The name of a system or workspace: its text up to the first ' (', never cut (LIMIT is ignored; kept for
    callers). A name that does not fit its slot wraps or shrinks where it is drawn instead."""
    return str(title).split(" (")[0].strip()


def chosen_label(short, full):
    """A chosen short label, unless it was cut off (it ends in an ellipsis, the truncation mark); then the full
    name, since a cut-off label hides what it names. With no short label, the name up to its first ' ('."""
    short, full = str(short or "").strip(), str(full or "").strip()
    if not short:
        return derived_short(full)
    if (short.endswith("…") or short.endswith("...")) and full:
        return full
    return short


def short_label(node):
    return chosen_label(node.get("short_label"), node.get("title", ""))


def fit_lines(text, options, room):
    """Wrap at readable size; box_dims reserves the required height before routing."""
    size, width, step = next(o for o in reversed(options) if o[0] >= 11)
    return size, step, textwrap.wrap(str(text), width, break_long_words=True) or [""]


def pretty(value):
    return str(value).replace("_", " ")


def type_tag(node):
    return TYPE_TAGS.get(node["type"]) or pretty(node["type"]).upper()


def display_label(edge, lanes):
    """The drawn text of an edge: display_label, else the defaults of the format."""
    if edge.get("display_label"):
        return str(edge["display_label"])
    kind = edge["kind"]
    defaults = {"then": "yes", "else": "no", "handoff": "handoff", "inbound": "inbound"}
    if kind in defaults:
        return defaults[kind]
    if kind == "write" and is_agent_lane(lanes.get(edge["source"])):
        return "agent writes"
    if kind in ("exception", "loop"):
        return pretty(edge["branch"]) if edge.get("branch") else ""
    if kind not in KNOWN_EDGE_KINDS:
        return pretty(kind)
    return ""


LABEL_MAX = 32   # an edge label longer than this goes to a numbered footnote rather than onto the line


def decision_lines(node, names=None):
    """The text printed beside a decision, in order: (kind, text). Ids are never printed (they live in the
    tooltip and the data attributes); the title and the review line wrap rather than being cut."""
    lines = [("a", "AGENT DECIDES")] if is_agent_lane(node["lane"]) else []
    lines += [("t", ln) for ln in textwrap.wrap(str(node["title"]), DEC_WRAP, break_long_words=True) or [""]]
    if node.get("reviews"):
        # `reviews` names the step under review by id: print that step's title, never the id.
        reviewed = (names or {}).get(node["reviews"], node["reviews"])
        text = "reviews %s · ratify" % reviewed
        lines += [("r", ln) for ln in textwrap.wrap(text, 22, break_long_words=True)]
    return lines


def table_name(table):
    """A decision table's visible name, or "" when it has none (its id is never shown)."""
    return str(table.get("name") or table.get("title") or "").strip()


def decision_text_box(node, centre, names=None):
    """Bounding box (x, y, w, h) of everything printed for a decision: its lines (the AGENT DECIDES line first
    for an Agent-band decision) and its table marks, all right-aligned DEC_GAP left of the diamond's vertical
    axis, below the diamond."""
    x, y = centre
    widths = {"t": 6.9, "r": 5.6, "a": 6.2}
    items = [widths[k] * len(t) for k, t in decision_lines(node, names)]
    tables = id_list(node.get("decision_table")) or []
    items += [14.0 for _ in tables]
    w = max(items) + 4 if items else 0
    top = y + DIA_HH + DEC_TEXT - 11
    bottom = y + DIA_HH + decision_text_depth(node, names)
    return (x - DEC_GAP - w, top, w, bottom - top)


def decision_text_depth(node, names=None):
    """How far below a decision's bottom vertex its printed lines and table marks reach."""
    tables = id_list(node.get("decision_table")) or []
    return DEC_TEXT + 13.5 * (len(decision_lines(node, names)) + len(tables) - 1) + 4


def box_dims(node):
    if node["type"] == "decision":
        return DIA_HW, DIA_HH
    if node["type"] == "system_result":
        return RES_W / 2, max(RES_H, 50 + 13 * len(textwrap.wrap(str(node["title"]), 30))) / 2
    lines = textwrap.wrap(str(node["title"]), 25) or [""]
    extra = (18 if node.get("systems") else 0) + (15 if node.get("evidence_state") in
            ("needs_more_information", "Contradictory") else 0)
    extra += 8 if node.get("stages_to") else 0
    return NODE_W / 2, max(NODE_H, 40 + len(lines) * 13 + extra) / 2


def band_lanes(graph, variant, act_nodes):
    used = []
    for n in act_nodes:
        if n["lane"] != "System" and n["lane"] not in used:
            used.append(n["lane"])
    order = []
    if "Analyst" in used or variant in KNOWN_VARIANTS:
        order.append("Analyst")
    agents = [lane for lane in used if is_agent_lane(lane)]
    if "Agent" in used or (variant == FUTURE and not agents):
        order.append("Agent")
    order += [lane for lane in agents if lane != "Agent"]
    return order + [lane for lane in used if lane != "Analyst" and not is_agent_lane(lane)]


def assign_tracks(requests):
    """Interval assignment: first track whose occupied spans do not overlap."""
    tracks, result = [], {}
    for key, lo, hi in requests:
        for i, spans in enumerate(tracks):
            if all(hi < a or lo > b for a, b in spans):
                spans.append((lo, hi))
                result[key] = i
                break
        else:
            tracks.append([(lo, hi)])
            result[key] = len(tracks) - 1
    return result, len(tracks)


def simplify(points):
    out = []
    for p in points:
        if out and out[-1] == p:
            continue
        if len(out) >= 2 and (out[-2][0] == out[-1][0] == p[0] or out[-2][1] == out[-1][1] == p[1]):
            # Keep a U-turn: collapse only when the middle point lies between its neighbours.
            a, b = out[-2], out[-1]
            if (a[0] == b[0] == p[0] and min(a[1], p[1]) <= b[1] <= max(a[1], p[1])) or \
               (a[1] == b[1] == p[1] and min(a[0], p[0]) <= b[0] <= max(a[0], p[0])):
                out[-1] = p
                continue
        out.append(p)
    return out


def crosses(a, b, box):
    (ax, ay), (bx, by) = a, b
    x, y, w, h = box
    if ax == bx:
        return x < ax < x + w and max(min(ay, by), y) < min(max(ay, by), y + h)
    if ay == by:
        return y < ay < y + h and max(min(ax, bx), x) < min(max(ax, bx), x + w)
    return False


def intersects(r, box):
    rx, ry, rw, rh = r
    x, y, w, h = box
    return rx < x + w and x < rx + rw and ry < y + h and y < ry + rh


class Scene:
    """Everything the SVG needs, computed from the graph."""


def layout(graph, variant, diagram=None):
    """Positions, bands, rails and orthogonal routes for every element (pure)."""
    nodes, edges, tables, notes = graph_shape(graph, variant, evidence=False, diagram=diagram)
    topo = topology(graph)
    order, depth, route = topo["order"], topo["depth"], topo["route"]
    rail_ids = topo["rails"]
    rails = set(rail_ids)
    act = [nodes[n] for n in topo["act"]]
    lanes = {n["id"]: n["lane"] for n in graph["nodes"]}
    s = Scene()
    s.graph, s.variant, s.nodes, s.edges, s.tables, s.notes = graph, variant, nodes, edges, tables, notes
    s.topo, s.rails = topo, rail_ids
    s.names = {n["id"]: n.get("title", "") for n in graph["nodes"] if n.get("title")}
    s.lanes_list = band_lanes(graph, variant, act)
    system_index = len(s.lanes_list)

    def band_of(nid):
        n = nodes[nid]
        if n["lane"] == "System":
            return ("System", "result" if n["type"] == "system_result" else "step")
        return (n["lane"], "")

    def band_index(nid):
        lane = band_of(nid)[0]
        return system_index if lane == "System" else s.lanes_list.index(lane)

    s.band_of, s.band_index = band_of, band_index
    layout_succ = {}
    for e in topo["layout_edges"]:
        layout_succ.setdefault(e["source"], []).append(e["target"])
    groups = {}
    for n in act:
        groups.setdefault((depth[n["id"]], band_of(n["id"])), []).append(n["id"])
    slot = {}
    for key, members in groups.items():
        members.sort(key=lambda nid: (0 if any(depth[t] == depth[nid] + 1 for t in layout_succ.get(nid, [])) else 1,
                                      order[nid]))
        for i, nid in enumerate(members):
            slot[nid] = (i, len(members))
    s.slot = slot
    stack_of = {}
    for (d, band), members in groups.items():
        stack_of[band] = max(stack_of.get(band, 0), len(members))
    maxdepth = max(depth.values()) if depth else 0
    s.width = X0 + maxdepth * PITCH + NODE_W / 2 + 96
    s.rail_x1 = s.width - 28

    def cx(nid):
        return X0 + depth[nid] * PITCH

    # Edge classes and track zones (depend only on band membership and columns).
    self_loops = {nid for nid in nodes if any(e["source"] == e["target"] == nid for e in edges.values())}
    pair_count, pair_seen = {}, {}
    for e in graph["edges"]:
        if route[e["id"]] in ("step",):
            key = (e["source"], e["target"])
            pair_count[key] = pair_count.get(key, 0) + 1
    plan = {}
    for e in graph["edges"]:
        eid, r = e["id"], route[e["id"]]
        if r != "step":
            plan[eid] = {"class": r}
            continue
        src, dst = e["source"], e["target"]
        key = (src, dst)
        k = pair_seen.get(key, 0)
        pair_seen[key] = k + 1
        n = pair_count[key]
        par = (k - (n - 1) / 2.0) if n > 1 else 0.0
        sb, tb = band_index(src), band_index(dst)
        if eid in topo["returns"] or e["kind"] == "loop":
            plan[eid] = {"class": "return", "zone": "disp", "par": par}
        elif sb == tb and depth[dst] == depth[src] + 1:
            plan[eid] = {"class": "adjacent", "par": par, "n": n}
        elif sb == tb:
            zone = "top" if sb == 0 else ("disp" if sb == system_index else ("corr", sb))
            plan[eid] = {"class": "skip", "zone": zone, "par": par}
        elif tb > sb:
            zone = "disp" if tb == system_index or sb + 1 == system_index else ("corr", sb + 1)
            plan[eid] = {"class": "down", "zone": zone, "par": par}
        else:
            zone = "disp" if sb == system_index else ("corr", sb)
            plan[eid] = {"class": "up", "zone": zone, "par": par}
    s.plan = plan

    spans = None
    for _ in range(6):
        requests = {}
        for e in graph["edges"]:
            p = plan[e["id"]]
            if "zone" not in p:
                continue
            if spans is None:
                xs = [cx(e["source"]), cx(e["target"])]
                lo, hi = min(xs) - 8, max(xs) + 8
            else:
                lo, hi = spans[e["id"]]
            requests.setdefault(p["zone"], []).append((e["id"], lo, hi))
        assignment, counts = {}, {}
        for zone, reqs in requests.items():
            result, count = assign_tracks(reqs)
            assignment.update(result)
            counts[zone] = count
        _geometry(s, counts, stack_of, cx, self_loops)
        new_spans = _route(s, assignment, cx)
        if new_spans == spans:
            break
        spans = new_spans
    _place_labels(s)
    return s


def _geometry(s, counts, stack_of, cx, self_loops):
    graph, nodes, depth = s.graph, s.nodes, s.topo["depth"]
    node_h = max([NODE_H] + [2 * box_dims(n)[1] for n in graph["nodes"]
                              if n["type"] not in ("decision", "system_surface", "system_result")])
    res_h = max([RES_H] + [2 * box_dims(n)[1] for n in graph["nodes"] if n["type"] == "system_result"])
    stack, res_stack = max(STACK, node_h + 20), max(RES_STACK, res_h + 28)
    s.counts = counts
    first_top = 36 + counts.get("top", 0) * TRACK
    s.first_top = first_top
    bands, y = [], first_top
    s.pitch = {}
    for i, lane in enumerate(s.lanes_list):
        c = max(1, stack_of.get((lane, ""), 1))
        members = [n for n in graph["nodes"] if n["lane"] == lane and n["type"] != "system_surface"]
        deco = any(n["type"] == "decision" or n["id"] in self_loops or n.get("annotation") for n in members)
        # A decision's text hangs below its diamond: the gap under its row grows so the text clears the row
        # below, and the band grows so the lowest row's text stays inside it.
        below = [0.0] * c
        for n in members:
            if n["type"] == "decision" and n["id"] in s.slot:
                r = s.slot[n["id"]][0]
                below[r] = max(below[r], DIA_HH + decision_text_depth(n, s.names))
        offsets = [0.0]
        for r in range(c - 1):
            offsets.append(offsets[-1] + max(stack, below[r] + node_h / 2 + 8))
        s.pitch[lane] = offsets
        span = offsets[-1]
        depth_below = max(below)
        if i == 0:
            h = max(296, span + node_h + 174)
        else:
            y += 18 + counts.get(("corr", i), 0) * TRACK
            h = span + node_h + 46 + (50 if deco else 0)
        if depth_below:
            h = max(h, span + 2 * (depth_below + 10))
        bands.append({"lane": lane, "top": y, "height": h})
        y += h
    s.bands = bands
    s.disp_top = y
    system_top = y + 24 + counts.get("disp", 0) * 40
    s.system_top = system_top
    step_rows = stack_of.get(("System", "step"), 0)
    result_rows = stack_of.get(("System", "result"), 0)
    s.step_rows, s.result_rows = step_rows, result_rows
    last_bottom = None
    if step_rows:
        last_bottom = system_top + 46 + (step_rows - 1) * stack + node_h
    if result_rows:
        base = system_top + (46 if step_rows else 20) + step_rows * stack + res_h / 2
        last_bottom = base + (result_rows - 1) * res_stack + res_h // 2
    else:
        base = None
    rail_top = last_bottom + 53 if last_bottom is not None else system_top + 46
    # Rail order: not Assumed first, then Assumed; each by first use as a drop target
    # or rise source in graph.edges order, then graph order.
    first_use = {}
    for e in graph["edges"]:
        r = s.topo["route"][e["id"]]
        rail = e["target"] if r == "drop" else e["source"] if r == "rise" else None
        if rail is not None and rail not in first_use:
            first_use[rail] = len(first_use)
    orders = s.topo["order"]
    ranked = sorted(s.rails, key=lambda rid: (1 if nodes[rid].get("access_state") == "Assumed" else 0,
                                              0 if rid in first_use else 1,
                                              first_use.get(rid, 0), orders[rid]))
    s.rail_order = ranked
    s.rail_y = {rid: rail_top + i * RAIL_GAP for i, rid in enumerate(ranked)}
    if ranked:
        system_bottom = rail_top + (len(ranked) - 1) * RAIL_GAP + 30
    elif last_bottom is not None:
        system_bottom = last_bottom + 30
    else:
        system_bottom = system_top + 76
    s.system_bottom = system_bottom
    s.height = system_bottom + 96
    centre = {}
    band_top = {b["lane"]: b for b in bands}
    for n in graph["nodes"]:
        nid = n["id"]
        if n["type"] == "system_surface":
            continue
        i, c = s.slot[nid]
        lane, row = s.band_of(nid)
        if lane == "System":
            if row == "step":
                centre[nid] = (cx(nid), system_top + 46 + node_h // 2 + i * stack)
            else:
                centre[nid] = (cx(nid), base + i * res_stack)
        else:
            b = band_top[lane]
            offsets = s.pitch[lane]
            mid = (offsets[c - 1] if c - 1 < len(offsets) else 0) / 2.0
            centre[nid] = (cx(nid), b["top"] + b["height"] / 2 + offsets[i] - mid)
    s.centre = centre
    boxes = {}
    for nid, (x, y0) in centre.items():
        hw, hh = box_dims(nodes[nid])
        boxes[nid] = (x - hw, y0 - hh, 2 * hw, 2 * hh)
    s.text_boxes = {nid: decision_text_box(nodes[nid], centre[nid], s.names) for nid in centre
                    if nodes[nid]["type"] == "decision"}
    s.plate_w = {}
    for rid in s.rails:
        label = short_label(nodes[rid])
        w = int(max(78, 24 + len(label) * 7.2) + 0.999)
        s.plate_w[rid] = w
        boxes[rid] = (RAIL_X0, s.rail_y[rid] - 12, w, 24)
    s.boxes = boxes


def _zone_y(s, zone, track):
    if zone == "top":
        return s.first_top - TRACK * (track + 1)
    if zone == "disp":
        return s.disp_top + 32 + track * 40
    index = zone[1]
    above = s.bands[index - 1]
    return above["top"] + above["height"] + 18 + track * TRACK


def _gutter(s, d, used):
    if d <= 0:
        d = 1
    k = used.get(d, 0)
    used[d] = k + 1
    k %= GUTTER_TRACKS
    offset = ((k + 1) // 2) * GUTTER_STEP * (1 if k % 2 else -1)
    return X0 + d * PITCH - PITCH / 2 + offset


def _column(x):
    return int(round((x - X0) / float(PITCH)))


def _detour(s, points, ends, gutter_for, skip):
    """Obstacle rule: a vertical run through another node moves into a column gutter."""
    pts = simplify(points)
    changed = True
    guard = 0
    while changed and guard < 8:
        changed = False
        guard += 1
        for i in range(len(pts) - 1):
            a, b = pts[i], pts[i + 1]
            if a[0] != b[0] or a[1] == b[1]:
                continue
            if not any(crosses(a, b, box) for nid, box in s.boxes.items() if nid not in skip):
                continue
            g = gutter_for(a[0])
            step = 1 if b[1] > a[1] else -1
            new = pts[:i]
            if i == 0 and ends[0]:
                new += [a, (a[0], a[1] + step * STUB), (g, a[1] + step * STUB)]
            else:
                new += [(g, a[1])]
            if i + 1 == len(pts) - 1 and ends[1]:
                new += [(g, b[1] - step * STUB), (b[0], b[1] - step * STUB), b]
            else:
                new += [(g, b[1])]
            new += pts[i + 2:]
            pts = simplify(new)
            changed = True
            break
    return pts


def _route(s, assignment, cx):
    graph, nodes, plan, centre, boxes = s.graph, s.nodes, s.plan, s.centre, s.boxes
    s.routes = {}
    spans = {}
    used = {}
    drop_groups = {}
    for e in graph["edges"]:
        r = s.topo["route"][e["id"]]
        if r in ("drop", "rise"):
            node = e["source"] if r == "drop" else e["target"]
            group = e["kind"] if e["kind"] in ("read_access", "write") else "other"
            drop_groups.setdefault((node, r, group), []).append(e["id"])
    rail_links = [e["id"] for e in graph["edges"] if s.topo["route"][e["id"]] == "rail"]

    fan = {}

    def is_decision(nid):
        return nodes[nid]["type"] == "decision"

    def fan_x(nid, side):
        """A decision's lines leave and enter at its top or bottom vertex and fan out to the right of its axis
        (its text sits to the left): the k-th line through that vertex runs at x + k * DEC_FAN."""
        k = fan.get((nid, side), 0)
        fan[(nid, side)] = k + 1
        return centre[nid][0] + k * DEC_FAN

    def port(nid, side, dx=0):
        x, y = centre[nid]
        hw, hh = box_dims(nodes[nid])
        if is_decision(nid):
            dx = 0
        dx = max(-(hw - 8), min(hw - 8, dx))
        return {"top": (x + dx, y - hh), "bottom": (x + dx, y + hh),
                "left": (x - hw, y), "right": (x + hw, y)}[side]

    def vertex_lead(nid, side, lane_x):
        """Points from a decision's vertex to the start of its own vertical lane (a stub, then across)."""
        x, y = centre[nid]
        vy = y + DIA_HH if side == "bottom" else y - DIA_HH
        sy = vy + (STUB if side == "bottom" else -STUB)
        return [(x, vy), (x, sy), (lane_x, sy)]

    def zone_below(nid, zone):
        b = s.band_index(nid)
        position = 0 if zone == "top" else len(s.lanes_list) if zone == "disp" else zone[1]
        return b < position

    for e in graph["edges"]:
        eid, src, dst = e["id"], e["source"], e["target"]
        p = plan[eid]
        cls = p["class"]
        own = {src, dst}
        if cls == "self":
            x, y = centre[src]
            top = boxes[src][1]
            pts = [(x - 20, top), (x - 20, top - 28), (x + 20, top - 28), (x + 20, top)]
            s.routes[eid] = ("self", pts)
            continue
        if cls in ("drop", "rise"):
            node, rail = (src, dst) if cls == "drop" else (dst, src)
            group = e["kind"] if e["kind"] in ("read_access", "write") else "other"
            members = drop_groups[(node, cls, group)]
            k = members.index(eid)
            spread = (k - (len(members) - 1) / 2.0) * 11
            base = {"read_access": -40, "write": 32}.get(group, 4)
            x0 = centre[node][0] + base + spread
            hw = box_dims(nodes[node])[0]
            x0 = max(centre[node][0] - hw + 4, min(centre[node][0] + hw - 4, x0))
            bottom = boxes[node][1] + boxes[node][3]
            ry = s.rail_y[rail]
            if is_decision(node):
                lead = vertex_lead(node, "bottom", fan_x(node, "bottom"))
                pts = lead + [(lead[-1][0], ry)]
            else:
                pts = [(x0, bottom), (x0, ry)]
            d = s.topo["depth"][node]

            def gutter_for(x, d=d, group=group):
                return _gutter(s, d if group == "read_access" else d + 1, used)

            pts = _detour(s, pts, (True, False), gutter_for, own | set(s.rails))
            last = pts[-1]
            pts = pts + [(last[0] + (6 if last[0] < centre[node][0] else -6), ry)]
            if cls == "rise":
                pts = list(reversed(pts))
            s.routes[eid] = (cls, pts)
            continue
        if cls == "rail":
            k = rail_links.index(eid)
            x = s.rail_x1 - 20 - k * 12
            s.routes[eid] = ("rail", [(x, s.rail_y[src]), (x, s.rail_y[dst])])
            continue
        par = p.get("par", 0.0)
        if cls == "adjacent":
            sy, ty = centre[src][1], centre[dst][1]
            off = par * 36
            shw, shh = box_dims(nodes[src])
            thw, thh = box_dims(nodes[dst])
            off = max(-(min(shh, thh) - 6), min(min(shh, thh) - 6, off))
            a = (centre[src][0] + shw, sy + off)
            b = (centre[dst][0] - thw, ty + off)
            if sy == ty:
                pts = [a, b]
            else:
                mid = (a[0] + b[0]) / 2.0 + par * 8
                pts = [a, (mid, a[1]), (mid, b[1]), b]
            # a decision is left and entered at its side vertex; a parallel offset jogs after a stub
            if is_decision(src) and off:
                pts = [(a[0], sy), (a[0] + STUB, sy), (a[0] + STUB, a[1])] + pts[1:]
            if is_decision(dst) and off:
                pts = pts[:-1] + [(b[0] - STUB, b[1]), (b[0] - STUB, ty), (b[0], ty)]
            route_name = "step"
        else:
            zone = p["zone"]
            ty_track = _zone_y(s, zone, assignment[eid])
            dx_par = par * 14
            if cls == "return":
                sdx, tdx = -64 + dx_par, 46 + dx_par
            elif cls == "up":
                sdx, tdx = 30 + dx_par, dx_par
            else:
                sdx, tdx = dx_par, dx_par
            sside = "bottom" if zone_below(src, zone) else "top"
            tside = "bottom" if zone_below(dst, zone) else "top"
            a = port(src, sside, sdx)
            b = port(dst, tside, tdx)
            head = vertex_lead(src, sside, fan_x(src, sside)) if is_decision(src) else [a]
            tail = list(reversed(vertex_lead(dst, tside, fan_x(dst, tside)))) if is_decision(dst) else [b]
            pts = head + [(head[-1][0], ty_track), (tail[0][0], ty_track)] + tail
            route_name = "return" if cls == "return" else "step"
        if cls != "adjacent" or len(pts) > 2:
            def gutter_for(x):
                return _gutter(s, _column(x), used)

            pts = _detour(s, pts, (True, True), gutter_for, own)
        pts = simplify(pts)
        s.routes[eid] = (route_name, pts)
        if "zone" in p:
            ty_track = _zone_y(s, p["zone"], assignment[eid])
            xs = [x for (x, y) in pts if y == ty_track] or [pts[0][0], pts[-1][0]]
            spans[eid] = (min(xs) - 8, max(xs) + 8)
    return spans


def _place_labels(s):
    s.labels, s.footnotes = [], []
    pills = []
    lanes = {n["id"]: n["lane"] for n in s.graph["nodes"]}
    boxes = list(s.boxes.values()) + list(s.text_boxes.values())

    def sibling_segments(edge):
        for other in s.graph["edges"]:
            if other is not edge and other["source"] == edge["source"] and other["id"] in s.routes:
                opts = s.routes[other["id"]][1]
                for seg in zip(opts, opts[1:]):
                    yield seg

    for e in s.graph["edges"]:
        text = display_label(e, lanes)
        if not text:
            continue
        _, pts = s.routes[e["id"]]
        w = 12 + len(text) * 5.6
        segments = [(a, b) for a, b in zip(pts, pts[1:])]
        # A label never sits on a stretch that another link from the same step also runs along
        # (two exits of a decision leaving by one port): there it could name either branch.
        own = [seg for seg in segments if not any(_overlaps(seg, other) for other in sibling_segments(e))]
        segments = own or segments
        horizontal = [(a, b) for a, b in segments if a[1] == b[1] and a[0] != b[0]]
        chosen = next((seg for seg in horizontal if abs(seg[1][0] - seg[0][0]) >= w + 8), None)
        if chosen is None:
            pool = horizontal or segments
            chosen = max(pool, key=lambda seg: abs(seg[1][0] - seg[0][0]) + abs(seg[1][1] - seg[0][1]))
        (ax, ay), (bx, by) = chosen
        others = [seg for eid, (_, opts) in s.routes.items() if eid != e["id"] for seg in zip(opts, opts[1:])]

        def clear(rect, strict):
            inside = rect[0] >= 0 and rect[1] >= 0 and rect[0] + rect[2] <= s.width and rect[1] + rect[3] <= s.height
            if not inside or any(intersects(rect, b) for b in boxes) or any(intersects(rect, q) for q in pills):
                return False
            return not strict or not any(_seg_in_rect(a, b, rect) for a, b in others)

        placed = None
        if len(text) <= LABEL_MAX:
            # First a spot no other link runs through, on the chosen stretch or another long-enough horizontal
            # one; then (as before) any spot on the chosen stretch clear of boxes and labels.
            roomy = [seg for seg in horizontal if seg != chosen and abs(seg[1][0] - seg[0][0]) >= w + 8]
            roomy += [seg for seg in segments if seg[0][0] == seg[1][0] and abs(seg[1][1] - seg[0][1]) >= 40]
            for strict, pool in ((True, [chosen] + roomy), (False, [chosen])):
                for (sx0, sy0), (sx1, sy1) in pool:
                    vertical = sx0 == sx1
                    if not vertical:
                        shifts = [(0, 0), (0, -14), (0, 14)]
                    elif ((sx0, sy0), (sx1, sy1)) == chosen:
                        shifts = [(0, 0), (-14 * (w / 28.0 + 1), 0), (14 * (w / 28.0 + 1), 0)]
                    else:   # another link's vertical stretch: the pill sits on it, or hugs it on either side
                        shifts = [(0, 0), (-(w / 2 + 4), 0), (w / 2 + 4, 0)]
                    for f in (0.5, 0.25, 0.75, 0.1, 0.9):
                        for dx, dy in shifts:
                            px = sx0 + (sx1 - sx0) * f + dx
                            py = sy0 + (sy1 - sy0) * f + dy
                            rect = (px - w / 2, py - 8.5, w, 17)
                            if clear(rect, strict):
                                placed = (px, py, rect)
                                break
                        if placed:
                            break
                    if placed:
                        break
                if placed:
                    break
        if placed:
            pills.append(placed[2])
            s.labels.append((e, text, placed))
        else:
            # The footnote number sits on the link where it is clear of boxes, decision text, labels and
            # other links; else at the middle of the chosen stretch.
            spot = None
            for strict in (True, False):
                for a, b in [chosen] + segments:
                    for f in (0.5, 0.3, 0.7, 0.15, 0.85):
                        fx, fy = a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f
                        rect = (fx - 8, fy - 12, 16, 11)
                        if clear(rect, strict):
                            spot = (fx, fy)
                            break
                    if spot:
                        break
                if spot:
                    break
            spot = spot or ((ax + bx) / 2.0, (ay + by) / 2.0)
            pills.append((spot[0] - 8, spot[1] - 12, 16, 11))
            s.footnotes.append((e, text, spot, len(s.footnotes) + 1))


def _seg_in_rect(a, b, rect):
    """True when the axis-aligned segment a-b passes through the interior of RECT (x, y, w, h)."""
    x, y, w, h = rect
    (ax, ay), (bx, by) = a, b
    if ax == bx:
        return x < ax < x + w and min(ay, by) < y + h and max(ay, by) > y
    if ay == by:
        return y < ay < y + h and min(ax, bx) < x + w and max(ax, bx) > x
    return False


def _overlaps(a, b):
    """True when two axis-aligned segments run along each other for some length."""
    (ax0, ay0), (ax1, ay1) = a
    (bx0, by0), (bx1, by1) = b
    if ay0 == ay1 == by0 == by1:
        return min(max(ax0, ax1), max(bx0, bx1)) - max(min(ax0, ax1), min(bx0, bx1)) > 0
    if ax0 == ax1 == bx0 == bx1:
        return min(max(ay0, ay1), max(by0, by1)) - max(min(ay0, ay1), min(by0, by1)) > 0
    return False


def _defs():
    def marker(mid, path, size="7"):
        return ('<marker id="%s" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="%s" markerHeight="%s" '
                'orient="auto-start-reverse">%s</marker>' % (mid, size, size, path))
    return ("<defs>" +
            marker("arw", '<path d="M0 0 L10 5 L0 10 z" fill="%s"/>' % MIDNIGHT) +
            marker("arw-coral", '<path d="M0 0 L10 5 L0 10 z" fill="%s"/>' % CORAL, "7.5") +
            marker("arw-access", '<path d="M0 0 L10 5 L0 10 z" fill="%s"/>' % ACCESS) +
            marker("arw-open", '<path d="M1 1 L9 5 L1 9" fill="none" stroke="%s" stroke-width="1.4"/>' % MIDNIGHT) +
            marker("arw-ink", '<path d="M0 0 L10 5 L0 10 z" fill="%s"/>' % CORAL_INK) +
            "</defs>")


def _note_marker(gid, x, y, texts):
    return ('<g data-note-for="%s"><title>%s</title><circle cx="%s" cy="%s" r="7" fill="%s" stroke="%s" stroke-width="1"/>'
            '<text x="%s" y="%s" fill="%s" font-size="9" font-weight="700" text-anchor="middle">i</text></g>'
            % (esc(gid), esc(angle_safe(" ".join(texts))), num(x), num(y), WHITE, MUTED, num(x), num(y + 3.2), MUTED))


def _glyph(lane, x, y):
    lane = lane_kind(lane)
    if lane == "Agent":
        return ('<rect x="%s" y="%s" width="13" height="11" rx="2.5" fill="none" stroke="%s" stroke-width="1.4"/>'
                '<circle cx="%s" cy="%s" r="2.1" fill="%s"/>'
                % (num(x + 7), num(y + 11), ACCESS, num(x + 13.5), num(y + 8), ACCESS))
    if lane == "Analyst":
        return ('<circle cx="%s" cy="%s" r="3.4" fill="%s"/>'
                '<path d="M%s %s Q%s %s %s %s" stroke="%s" stroke-width="1.5" fill="none"/>'
                % (num(x + 13), num(y + 14), CORAL, num(x + 7), num(y + 25), num(x + 13), num(y + 19),
                   num(x + 19), num(y + 25), CORAL))
    return ""


def _chips(s, node, cx, top, bottom, inner_w):
    items = []
    for ref in dict.fromkeys(node.get("systems") or []):
        if ref in s.nodes:
            rail = s.nodes[ref]
            kind = rail.get("surface_kind")
            items.append((short_label(rail), SURFACE_FILL.get(kind, WHITE), kind not in SURFACE_FILL))
        else:
            ws = next((w for w in s.graph.get("workspaces", []) if w["id"] == ref), {"id": ref})
            items.append((chosen_label(ws.get("short_label"), ws.get("name", "")) or "workspace", SUN, False))
    if not items:
        return ""
    chip_h, gap, vgap = 15, 4, 3

    def split(label):
        """A name too long for one line at 7.5px or more goes on two lines, broken at the word nearest the middle."""
        if 9 + len(label) * 5.7 * (7.5 / 9.0) <= inner_w:
            return [label]
        words = label.split(" ")
        best = None
        for k in range(1, len(words)):
            pair = [" ".join(words[:k]), " ".join(words[k:])]
            if best is None or max(len(p) for p in pair) < max(len(p) for p in best):
                best = pair
        return best or [label]

    def width(lines):
        return min(inner_w, max(24, 9 + max(len(ln) for ln in lines) * 5.7))

    def font(lines):
        """9px, or smaller so that a long name fits a chip as wide as the node (a name is never cut)."""
        longest = max(len(ln) for ln in lines)
        full = 9 + longest * 5.7
        return 9.0 if full <= inner_w else max(5.0, 9.0 * (inner_w - 9) / (longest * 5.7))

    # One-line names first, so a long two-line name never pushes the short ones into the "+N" count.
    items = [it for it in items if len(split(it[0])) == 1] + [it for it in items if len(split(it[0])) > 1]
    units_free = max(1, int((bottom - top + vgap) // (chip_h + vgap)))
    rows, current, used, i = [], [], 0.0, 0   # a row is (units, chips); a two-line chip is a row of 2 units

    def units_used():
        return sum(u for u, _ in rows)

    while i < len(items):
        label, fill, outlined = items[i]
        lines = split(label)
        w = width(lines)
        if len(lines) > 1:
            if current:
                rows.append((1, current))
                current, used = [], 0.0
            if units_used() + 2 > units_free:
                break
            rows.append((2, [(lines, w, fill, outlined)]))
            i += 1
            continue
        add = w if not current else w + gap
        if current and used + add > inner_w:
            rows.append((1, current))
            current, used = [], 0.0
            if units_used() >= units_free:
                break
            continue
        if not current and units_used() >= units_free:
            break
        current.append((lines, w, fill, outlined))
        used += add
        i += 1
    if current and units_used() < units_free:
        rows.append((1, current))
    overflow = len(items) - sum(len(r) for _, r in rows)
    if overflow > 0:
        if not rows or rows[-1][0] == 2:
            if rows and units_used() >= units_free:
                rows.pop()
                overflow += 1
            rows.append((1, []))
        last = rows[-1][1]
        plus = "+%d" % overflow
        while last and sum(w for _, w, _, _ in last) + gap * len(last) + width([plus]) > inner_w:
            last.pop()
            overflow += 1
            plus = "+%d" % overflow
        last.append(([plus], width([plus]), WHITE, False))
    total_h = sum(u * chip_h + (u - 1) * vgap for u, _ in rows) + (len(rows) - 1) * vgap
    y = bottom - total_h
    out = []
    for units, row in rows:
        h = units * chip_h + (units - 1) * vgap
        total = sum(w for _, w, _, _ in row) + gap * (len(row) - 1)
        x = cx - total / 2.0
        for lines, w, fill, outlined in row:
            stroke = ('stroke="%s" stroke-width="0.8"' % MIDNIGHT) if outlined else \
                     ('stroke="%s" stroke-width="0.6" stroke-opacity="0.5"' % MIDNIGHT)
            out.append('<rect x="%s" y="%s" width="%s" height="%s" rx="7.5" fill="%s" %s/>'
                       % (num(x), num(y), num(w), num(h), fill, stroke))
            size = font(lines)
            first = y + h / 2.0 - (len(lines) - 1) * (size + 2) / 2.0 + size * 0.35
            for k, ln in enumerate(lines):
                out.append('<text x="%s" y="%s" fill="%s" font-size="%s" text-anchor="middle">%s</text>'
                           % (num(x + w / 2.0), num(first + k * (size + 2)), MIDNIGHT, num(size), esc(ln)))
            x += w + gap
        y += h + vgap
    return "".join(out)



def angle_safe(text):
    """Text for a <title> or <textarea>: "<" written as the single angle quote ‹, since those elements
    may hold no "<" even once entities are decoded (the no-script page shape, FORDEEN-87)."""
    return str(text).replace("<", "\u2039")

def _tooltip(parts):
    return "<title>%s</title>" % esc(angle_safe(" | ".join(p for p in parts if p)))


def render_svg(graph, variant, diagram=None):
    """The canonical band SVG of GRAPH. Raises ValueError only on a hard failure."""
    diagram = diagram if isinstance(diagram, dict) else {}
    s = layout(graph, variant, diagram)
    nodes, boxes = s.nodes, s.boxes
    notes_by = {}
    for n in s.notes:
        if n["id"]:
            notes_by.setdefault(n["id"], []).append(n["text"])
    band_labels = diagram.get("band_labels") if isinstance(diagram.get("band_labels"), dict) else {}
    lanes = {n["id"]: n["lane"] for n in graph["nodes"]}
    band_w = s.width - LANE_LEFT - 24
    out = ['<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %s %s" width="%s" height="%s" '
           'data-renderer="%s" font-family="%s">' % (num(s.width), num(s.height), num(s.width), num(s.height),
                                                    RENDERER_ID, esc(FONT)),
           _defs(),
           '<g data-decoration="background"><rect x="0" y="0" width="%s" height="%s" fill="%s"/></g>'
           % (num(s.width), num(s.height), WHITE)]
    # Bands
    deco = ['<g data-decoration="bands">',
            '<rect x="%s" y="%s" width="%s" height="%s" fill="%s" stroke="%s" stroke-width="1.5"/>'
            % (LANE_LEFT, num(s.first_top), num(band_w), num(s.system_bottom - s.first_top), WHITE, MIDNIGHT)]

    def spine(top, h, label, label_y=None, room=None):
        """The band's dark spine and its rotated name: one line at 14px when it fits ROOM px (the band height
        less a margin by default), else two lines at 11px, else two lines at the size that fits. Never cut."""
        cy = top + h / 2.0 if label_y is None else label_y
        x = LANE_LEFT + SPINE_W / 2.0
        room = (h - 20) if room is None else room
        per = 0.72   # width of one capital per font px, letter-spacing included
        lines, size = [label], 14.0
        if len(label) * per * size > room:
            words = label.split(" ")
            best = None
            for i in range(1, len(words)):
                a, b = " ".join(words[:i]), " ".join(words[i:])
                if best is None or max(len(a), len(b)) < max(len(best[0]), len(best[1])):
                    best = (a, b)
            lines = list(best) if best else [label]
            size = 11.0
            longest = max(len(ln) for ln in lines)
            if longest * per * size > room:
                size = max(6.0, room / (longest * per))
                if len(lines) == 1:
                    size = min(size, 14.0)
        out = '<rect x="%s" y="%s" width="%s" height="%s" fill="%s"/>' % (LANE_LEFT, num(top), SPINE_W, num(h), MIDNIGHT)
        step = size + 1.5
        first = x + 0.3 * size - step * (len(lines) - 1) / 2.0
        for i, ln in enumerate(lines):
            lx = first + i * step
            out += ('<text x="%s" y="%s" fill="%s" font-size="%s" font-weight="600" text-anchor="middle" '
                    'letter-spacing="0.1em" transform="rotate(-90 %s %s)">%s</text>'
                    % (num(lx), num(cy), WHITE, num(size), num(lx), num(cy), esc(ln)))
        return out

    for b in s.bands:
        fill, opacity = BAND_FILL.get(lane_kind(b["lane"]), (WHITE, "1"))
        label = str(band_labels.get(b["lane"]) or b["lane"]).upper()
        deco.append('<rect x="%s" y="%s" width="%s" height="%s" fill="%s" fill-opacity="%s" stroke="%s" stroke-width="1"/>'
                    % (LANE_LEFT, num(b["top"]), num(band_w), num(b["height"]), fill, opacity, MIDNIGHT))
        deco.append(spine(b["top"], b["height"], label))
    sys_h = s.system_bottom - s.system_top
    deco.append('<rect x="%s" y="%s" width="%s" height="%s" fill="%s" stroke="%s" stroke-width="1"/>'
                % (LANE_LEFT, num(s.system_top), num(band_w), num(sys_h), WHITE, MIDNIGHT))
    assumed = [rid for rid in s.rail_order if nodes[rid].get("access_state") == "Assumed"]
    sys_label = str(band_labels.get("System") or "System").upper()
    label_y = None
    if assumed:
        top = s.rail_y[assumed[0]] - 12
        bottom = s.rail_y[assumed[-1]] + 12
        mid = (top + bottom) / 2.0
        half = len(sys_label) * 5 + 8
        if abs((s.system_top + sys_h / 2.0) - mid) < half + 50:
            label_y = max(s.system_top + half, top - 50 - half) if top - s.system_top > 2 * half + 50 else None
    deco.append(spine(s.system_top, sys_h, sys_label, label_y))
    if assumed:
        x = LANE_LEFT + SPINE_W + 3
        deco.append('<path d="M%s %s H%s V%s H%s" fill="none" stroke="%s" stroke-width="1.4"/>'
                    % (num(x + 4), num(top), num(x), num(bottom), num(x + 4), ACCESS))
        lx = LANE_LEFT + SPINE_W / 2.0
        mid = max(s.system_top + 46, min(s.system_bottom - 46, mid))
        deco.append('<text x="%s" y="%s" fill="%s" font-size="8.4" font-weight="600" text-anchor="middle" '
                    'letter-spacing="0.06em" transform="rotate(-90 %s %s)">ACCESS ASSUMED</text>'
                    % (num(lx + 12), num(mid + 3), SKY, num(lx + 12), num(mid + 3)))
    deco.append("</g>")
    out.extend(deco)

    def edge_group(e, cls, pts, body):
        gid = e["id"]
        attrs = ('id="edge-%s" data-graph-edge-id="%s" data-kind="%s" data-source="%s" data-target="%s" '
                 'data-route="%s" data-points="%s"' % (esc(gid), esc(gid), esc(e["kind"]), esc(e["source"]),
                                                       esc(e["target"]), cls,
                                                       " ".join("%s,%s" % (num(x), num(y)) for x, y in pts)))
        parts = [e.get("label") or display_label(e, lanes) or pretty(e["kind"]), pretty(e["kind"]),
                 "evidence: " + pretty(e["evidence_state"])]
        if e.get("condition"):
            parts.append("condition: " + e["condition"])
        if e.get("issue_id"):
            parts.append("issue: " + e["issue_id"])
        parts += notes_by.get(gid, [])
        extra = ""
        foot = next((f for f in s.footnotes if f[0]["id"] == gid), None)
        if foot:
            (fx, fy), n = foot[2], foot[3]
            extra += ('<text x="%s" y="%s" fill="%s" font-size="8" text-anchor="middle" data-footnote="%d">[%d]</text>'
                      % (num(fx), num(fy - 3), FAINT, n, n))
        if gid in notes_by:
            segs = list(zip(pts, pts[1:]))
            (ax, ay), (bx, by) = max(segs, key=lambda sg: abs(sg[1][0] - sg[0][0]) + abs(sg[1][1] - sg[0][1]))
            extra += _note_marker(gid, (ax + bx) / 2.0, (ay + by) / 2.0 - 12, notes_by[gid])
        return '<g %s>%s%s%s</g>' % (attrs, _tooltip(parts), body, extra)

    def polyline(pts, stroke, width, dash, marker):
        dash_attr = ' stroke-dasharray="%s"' % dash if dash else ""
        return ('<polyline points="%s" fill="none" stroke="%s" stroke-width="%s"%s marker-end="url(#%s)"/>'
                % (" ".join("%s,%s" % (num(x), num(y)) for x, y in pts), stroke, width, dash_attr, marker))

    def band_lane(nid):
        return s.band_of(nid)[0]

    def style(e, cls):
        kind = e["kind"]
        if cls == "self":
            return MIDNIGHT, "1.5", "4 3", "arw"
        if cls in ("drop", "rise", "rail"):
            node = e["source"] if cls == "drop" else e["target"]
            agent = cls != "rail" and is_agent_lane(band_lane(node))
            if kind == "read_access":
                dash = "2 4" if e["evidence_state"] == "needs_more_information" else "5 3"
                return (ACCESS, "1.25", dash, "arw-access") if agent else (MIDNIGHT, "1.25", dash, "arw-open")
            if kind == "write":
                return (CORAL_INK, "1.6", "", "arw-ink") if agent else (MIDNIGHT, "1.4", "", "arw")
            return MIDNIGHT, "1.25", "2 3", "arw-open"
        if cls == "return" or kind == "loop":
            if is_agent_lane(band_lane(e["target"])):
                return ACCESS, "1.5", "6 4", "arw-access"
            return MIDNIGHT, "1.5", "6 4", "arw"
        if kind in ("sequence", "then", "else"):
            return MIDNIGHT, "1.5", "", "arw"
        if kind == "exception":
            return MIDNIGHT, "1.5", "7 4", "arw"
        if kind == "handoff":
            return CORAL, "2.2", "", "arw-coral"
        if kind == "inbound":
            return CORAL, "1.8", "6 4", "arw-coral"
        if kind == "read_access":
            return MIDNIGHT, "1.25", "5 3", "arw-open"
        if kind == "write":
            if is_agent_lane(band_lane(e["source"])):
                return CORAL_INK, "1.6", "", "arw-ink"
            return MIDNIGHT, "1.4", "", "arw"
        return MIDNIGHT, "1.5", "3 3", "arw"

    access_layer, step_layer = [], []
    for e in graph["edges"]:
        cls, pts = s.routes[e["id"]]
        stroke, width, dash, marker = style(e, cls)
        group = edge_group(e, cls, pts, polyline(pts, stroke, width, dash, marker))
        (access_layer if cls in ("drop", "rise", "rail") else step_layer).append(group)
    out.extend(access_layer)
    # Rails (drawn after drops so drops pass beneath the plates).
    for rid in s.rail_order:
        n = nodes[rid]
        y = s.rail_y[rid]
        x, py, w, h = boxes[rid]
        label = short_label(n)
        needs = "needs_more_information" in (n.get("evidence_state"), n.get("access_state"))
        kind = n.get("surface_kind")
        parts = [n["title"], "System rail", "evidence: " + pretty(n["evidence_state"])]
        if n.get("access_state"):
            parts.append("access: " + pretty(n["access_state"]))
        if kind:
            parts.append("kind: " + pretty(kind))
        if n.get("issue_id"):
            parts.append("issue: " + n["issue_id"])
        if isinstance(n.get("detail"), str) and n["detail"]:
            parts.append(n["detail"])
        parts += notes_by.get(rid, [])
        body = ['<line x1="%s" y1="%s" x2="%s" y2="%s" stroke="%s" stroke-width="1.1" stroke-opacity="0.45"/>'
                % (RAIL_X0, num(y), num(s.rail_x1), num(y), MIDNIGHT),
                '<rect x="%s" y="%s" width="%s" height="24" rx="7" fill="%s" stroke="%s" stroke-width="1"/>'
                % (num(x), num(py), num(w), SUN if needs else WHITE if kind not in SURFACE_FILL and kind else SKY, MIDNIGHT),
                '<rect x="%s" y="%s" width="9" height="10" fill="none" stroke="%s" stroke-width="1.2"/>'
                % (num(x + 7), num(y - 6), MIDNIGHT),
                '<text x="%s" y="%s" fill="%s" font-size="11.5" font-weight="600">%s</text>'
                % (num(x + 22), num(y + 4), MIDNIGHT, esc(label))]
        marker_x = x + w + 15
        if needs:
            body.append('<circle cx="%s" cy="%s" r="8.5" fill="%s"/><text x="%s" y="%s" fill="%s" font-size="11" '
                        'font-weight="700" text-anchor="middle">!</text>'
                        % (num(x + w + 12), num(y), CORAL, num(x + w + 12), num(y + 3.4), WHITE))
            body.append('<text x="%s" y="%s" fill="%s" font-size="9.4" font-weight="600">needs more information</text>'
                        % (num(x + w + 26), num(y + 3.6), CORAL_INK))
            marker_x = x + w + 150
        if rid in notes_by:
            body.append(_note_marker(rid, marker_x, y, notes_by[rid]))
        out.append('<g id="node-%s" data-graph-node-id="%s" data-lane="%s" data-node-type="%s" data-systems="%s" '
                   'data-bounds="%s %s %s %s" data-rail-y="%s" data-rail-x0="%s" data-rail-x1="%s">%s%s</g>'
                   % (esc(rid), esc(rid), esc(n["lane"]), esc(n["type"]), esc(json.dumps(n.get("systems", []))),
                      num(x), num(py), num(w), num(h), num(y), RAIL_X0, num(s.rail_x1), _tooltip(parts), "".join(body)))
    out.extend(step_layer)
    # Act nodes
    s.table_anchor = {}
    open_ends = set(graph.get("open_ends") or [])
    workspace_names = {w["id"]: w.get("name", w["id"]) for w in graph.get("workspaces", [])}
    for n in graph["nodes"]:
        nid = n["id"]
        if n["type"] == "system_surface":
            continue
        x, y = s.centre[nid]
        bx, by, bw, bh = boxes[nid]
        band = band_lane(nid)
        parts = [n["title"], band + " band" if band == n["lane"] else "%s band (lane %s)" % (band, n["lane"]),
                 "evidence: " + pretty(n["evidence_state"])]
        if n.get("treatment"):
            parts.append("ownership: " + str(n["treatment"]))
        if n.get("issue_id"):
            parts.append("issue: " + n["issue_id"])
        if isinstance(n.get("detail"), str) and n["detail"]:
            parts.append(n["detail"])
        parts += notes_by.get(nid, [])
        body = []
        if n["type"] == "decision":
            body.append('<polygon points="%s,%s %s,%s %s,%s %s,%s" fill="%s" stroke="%s" stroke-width="1.5"/>'
                        % (num(x), num(y - DIA_HH), num(x + DIA_HW), num(y), num(x), num(y + DIA_HH), num(x - DIA_HW),
                           num(y), SUN, MIDNIGHT))
            body.append('<path d="M%s %s L%s %s M%s %s L%s %s" stroke="%s" stroke-width="1.6" fill="none"/>'
                        % (num(x - 9), num(y - 9), num(x + 9), num(y + 9), num(x - 9), num(y + 9), num(x + 9),
                           num(y - 9), MIDNIGHT))
            tx = x - DEC_GAP
            lines = decision_lines(n, s.names) + [("d", t) for t in id_list(n.get("decision_table")) or []]
            yy = y + DIA_HH + DEC_TEXT
            for kind, text in lines:
                if kind == "a":
                    body.append('<text x="%s" y="%s" fill="%s" font-size="8.6" font-weight="700" text-anchor="end" '
                                'letter-spacing="0.06em">%s</text>' % (num(tx), num(yy), CORAL_INK, esc(text)))
                elif kind == "t":
                    body.append('<text x="%s" y="%s" fill="%s" font-size="11.5" font-weight="600" text-anchor="end">%s</text>'
                                % (num(tx), num(yy), MIDNIGHT, esc(text)))
                elif kind == "r":
                    body.append('<text x="%s" y="%s" fill="%s" font-size="8.8" font-weight="600" text-anchor="end">%s</text>'
                                % (num(tx), num(yy), CORAL_INK, esc(text)))
                yy += 13.5
            s.table_anchor[nid] = yy - 13.5 * len(id_list(n.get("decision_table")) or [])
            if nid in notes_by:
                body.append(_note_marker(nid, x - DIA_HW - 10, y - 10, notes_by[nid]))
        elif n["type"] == "system_result" and band == "System":
            contra = n["evidence_state"] == "Contradictory"
            is_open = nid in open_ends
            stroke = CORAL if is_open else CORAL_INK if contra else MIDNIGHT
            body.append('<rect x="%s" y="%s" width="%s" height="%s" rx="8" fill="%s" stroke="%s" stroke-width="1.4" '
                        'stroke-dasharray="5 3"/>' % (num(bx), num(by), num(bw), num(bh), ROSE if contra else "#F4F1EC", stroke))
            body.append('<text x="%s" y="%s" fill="%s" font-size="8.4" font-weight="700" letter-spacing="0.06em">%s</text>'
                        % (num(bx + 12), num(by + 16), CORAL_INK if contra else MUTED,
                           "RESULT · ≠ CONTRADICTORY" if contra else "SYSTEM RESULT"))
            room = 13 if is_open else 26
            size, step, lines = fit_lines(n["title"], ((11, 30, 13), (9.5, 35, 11.5), (8.5, 40, 10)), room)
            for i, ln in enumerate(lines):
                body.append('<text x="%s" y="%s" fill="%s" font-size="%s" font-weight="500" text-anchor="middle">%s</text>'
                            % (num(x), num(by + 33 + i * step), MIDNIGHT, num(size), esc(ln)))
            if is_open:   # under the last title line (the title shrinks to one line when it can)
                body.append('<text x="%s" y="%s" fill="%s" font-size="8.8" font-weight="600" text-anchor="middle">'
                            'closure: needs more information</text>'
                            % (num(x), num(max(by + 47, by + 33 + (len(lines) - 1) * step + 12)), CORAL_INK))
            if nid in notes_by:
                body.append(_note_marker(nid, bx - 4, by - 4, notes_by[nid]))
        else:
            stripe = CORAL if band == "Analyst" else ACCESS if is_agent_lane(band) else MUTED
            tag_colour = CORAL_INK if band == "Analyst" else ACCESS if is_agent_lane(band) else MUTED
            body.append('<rect x="%s" y="%s" width="%s" height="%s" rx="9" ry="9" fill="%s" stroke="%s" stroke-width="1.5"/>'
                        % (num(bx), num(by), num(bw), num(bh), WHITE, MIDNIGHT))
            body.append('<path d="M%s %s q0 -8 8 -8 v%s q-8 0 -8 -8 z" fill="%s"/>'
                        % (num(bx + 1.2), num(by + 9), num(bh - 2), stripe))
            glyph = _glyph(band, bx + 8, by)
            body.append(glyph)
            tag_x = bx + 34 if glyph else bx + 16
            tag = type_tag(n) if n["type"] != "system_result" else "SYSTEM RESULT"
            body.append('<text x="%s" y="%s" fill="%s" font-size="8.5" font-weight="700" letter-spacing="0.06em">%s</text>'
                        % (num(tag_x), num(by + 17), tag_colour, esc(tag)))
            if n["type"] == "agent_stage" and n.get("stages_to"):
                pill = "→ " + str(n["stages_to"])
                pw = min(bw - 16, 12 + len(pill) * 5.0)
                pill_size = 8 if pw >= 12 + len(pill) * 5.0 else max(5.0, 8 * (pw - 12) / (len(pill) * 5.0))
                px1 = bx + bw - 8
                body.append('<rect x="%s" y="%s" width="%s" height="12" rx="6" fill="%s" stroke="%s" stroke-width="0.6"/>'
                            % (num(px1 - pw), num(by + 20), num(pw), SKY, ACCESS))
                body.append('<text x="%s" y="%s" fill="%s" font-size="%s" font-weight="600" text-anchor="middle">%s</text>'
                            % (num(px1 - pw / 2.0), num(by + 28.6), ACCESS, num(pill_size), esc(pill)))
            state = n["evidence_state"]
            sub = "[needs more information]" if state == "needs_more_information" else \
                  "[Contradictory]" if state == "Contradictory" else ""
            nsys = len(dict.fromkeys(n.get("systems") or []))
            ly = by + (46 if n["type"] == "agent_stage" and n.get("stages_to") else 38)
            # The box reserves readable title height plus chips and evidence before routing.
            room = (by + bh - 8) - (ly - 10) - (18 if nsys else 0) - (15 if sub else 0)
            size, step, lines = fit_lines(n["title"], ((12.3, 22, 14.5), (11, 25, 13), (10, 28, 12), (9, 31, 11),
                                                       (8, 35, 9.8)), room)
            for i, ln in enumerate(lines):
                body.append('<text x="%s" y="%s" fill="%s" font-size="%s" font-weight="500" text-anchor="middle">%s</text>'
                            % (num(x), num(ly + i * step), MIDNIGHT, num(size), esc(ln)))
            chip_top = ly + len(lines) * step + 2
            if sub:
                body.append('<text x="%s" y="%s" fill="%s" font-size="9.4" font-weight="600" text-anchor="middle">%s</text>'
                            % (num(x), num(chip_top + 9), CORAL_INK, sub))
                chip_top += 15
                if state == "Contradictory":
                    body.append('<circle cx="%s" cy="%s" r="8.5" fill="%s"/><text x="%s" y="%s" fill="%s" font-size="11" '
                                'font-weight="700" text-anchor="middle">≠</text>'
                                % (num(bx + bw - 4), num(by + bh - 4), CORAL_INK, num(bx + bw - 4), num(by + bh - 0.4), WHITE))
            body.append(_chips(s, n, x, chip_top, by + bh - 8, bw - 18))
            if n.get("annotation"):
                ann_lines = textwrap.wrap(str(n["annotation"]), 70, break_long_words=True) or [""]
                aw = 12 + max(len(a) for a in ann_lines) * 5.0
                ay = by + bh + 14
                body.append('<rect x="%s" y="%s" width="%s" height="%s" rx="5" fill="%s" opacity="0.94"/>'
                            % (num(x - aw / 2.0), num(ay - 10), num(aw), num(16 + 11 * (len(ann_lines) - 1)), WHITE))
                for i, a in enumerate(ann_lines):
                    body.append('<text x="%s" y="%s" fill="%s" font-size="9" font-weight="600" text-anchor="middle">%s</text>'
                                % (num(x), num(ay + 2 + 11 * i), CORAL_INK, esc(a)))
            if nid in notes_by:
                body.append(_note_marker(nid, bx - 4, by - 4, notes_by[nid]))
        systems_attr = esc(json.dumps(n.get("systems", [])))
        out.append('<g id="node-%s" data-graph-node-id="%s" data-lane="%s" data-node-type="%s" data-systems="%s" '
                   'data-bounds="%s %s %s %s">%s%s</g>'
                   % (esc(nid), esc(nid), esc(n["lane"]), esc(n["type"]), systems_attr,
                      num(bx), num(by), num(bw), num(bh), _tooltip(parts), "".join(body)))
    # Decision-table links under each owner
    for t in graph.get("decision_tables", []):
        for k, owner in enumerate(id_list(t["owner"])):
            x, y = s.centre[owner]
            bx, by, bw, bh = boxes[owner]
            if nodes[owner]["type"] == "decision":
                ty = s.table_anchor.get(owner, by + bh + 40)
                refs = id_list(nodes[owner].get("decision_table")) or []
                ty += 13.5 * refs.index(t["id"]) if t["id"] in refs else 0
            else:
                refs = id_list(nodes[owner].get("decision_table")) or []
                ty = by + bh + 12 + 11 * (refs.index(t["id"]) if t["id"] in refs else 0)
                if nodes[owner].get("annotation"):
                    ann_lines = textwrap.wrap(str(nodes[owner]["annotation"]), 70, break_long_words=True) or [""]
                    ty += 18 + 11 * (len(ann_lines) - 1)
            rid = "table-%s" % t["id"] if k == 0 else "table-%s--%s" % (t["id"], owner)
            decision = nodes[owner]["type"] == "decision"
            # A table is shown by its name when it has one; else by a small table mark. Its id stays in the
            # tooltip and the data attributes, never as visible text.
            name = table_name(t)
            ax = x - DEC_GAP if decision else x
            if name:
                mark = ('<text x="%s" y="%s" fill="%s" font-size="8.5" font-weight="600" text-anchor="%s">table · %s</text>'
                        % (num(ax), num(ty), MUTED, "end" if decision else "middle", esc(name)))
            else:
                gx = ax - 12 if decision else ax - 6
                gy = ty - 8
                mark = ('<rect x="%s" y="%s" width="12" height="9" rx="1" fill="%s" stroke="%s" stroke-width="0.9"/>'
                        '<path d="M%s %s H%s M%s %s V%s" stroke="%s" stroke-width="0.7" fill="none"/>'
                        % (num(gx), num(gy), WHITE, MUTED, num(gx), num(gy + 3), num(gx + 12), num(gx + 4), num(gy),
                           num(gy + 9), MUTED))
            out.append('<g id="%s" data-graph-table-id="%s" data-owner="%s">%s%s</g>'
                       % (esc(rid), esc(t["id"]), esc(owner), _tooltip([t.get("purpose", ""), "decision table " + t["id"]]),
                          mark))
    # Labels (top layer)
    if s.labels:
        out.append('<g data-decoration="edge-labels">')
        for e, text, (px, py, rect) in s.labels:
            cls = s.routes[e["id"]][0]
            colour = CORAL_INK if e["kind"] in ("exception", "handoff", "inbound") else \
                ACCESS if cls in ("drop", "rise") and is_agent_lane(band_lane(e["source"] if cls == "drop" else e["target"])) \
                and e["kind"] == "read_access" else MIDNIGHT
            out.append('<g data-label-for="%s"><rect data-label-for="%s" x="%s" y="%s" width="%s" height="17" rx="4" '
                       'fill="%s" stroke="%s" stroke-width="0.5" opacity="0.97"/>'
                       '<text x="%s" y="%s" fill="%s" font-size="10.4" font-weight="600" text-anchor="middle">%s</text></g>'
                       % (esc(e["id"]), esc(e["id"]), num(rect[0]), num(rect[1]), num(rect[2]), WHITE, MIDNIGHT,
                          num(px), num(py + 3.2), colour, esc(text)))
        out.append("</g>")
    out.append("</svg>")
    return "\n".join(out) + "\n"


def tips(graph, notes=()):
    """Tooltip data for the standalone page, per node and per edge id."""
    names = {n["id"]: n.get("title", n["id"]) for n in graph["nodes"] if n.get("type") == "system_surface"}
    names.update({w["id"]: w.get("name", w["id"]) for w in graph.get("workspaces", [])})
    by_id = {}
    for n in notes:
        if n["id"]:
            by_id.setdefault(n["id"], []).append(n["text"])
    result = {}
    for n in graph["nodes"]:
        result[n["id"]] = {"id": n["id"], "type": n["type"], "lane": n["lane"], "title": n["title"],
                           "performer": n.get("performer", ""), "detail": n.get("detail", ""),
                           "facts": [{"label": str(f.get("label", "")), "text": str(f.get("text", ""))}
                                     for f in n.get("facts") or [] if isinstance(f, dict)],
                           "systems": [names.get(ref, ref) for ref in dict.fromkeys(n.get("systems") or [])],
                           "evidence_state": n["evidence_state"], "condition": n.get("condition", ""),
                           "decision_table": id_list(n.get("decision_table")) or [],
                           "treatment": str(n.get("treatment", "")), "notes": by_id.get(n["id"], [])}
    for e in graph["edges"]:
        result[e["id"]] = {"id": e["id"], "kind": e["kind"], "label": e.get("label", ""),
                           "condition": e.get("condition", ""), "notes": by_id.get(e["id"], [])}
    return result


def page_notes(graph, variant, current=None, pin=None, diagram=None):
    return graph_notes(graph, variant, current, pin, diagram)
