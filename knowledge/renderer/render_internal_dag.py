#!/usr/bin/env python3
"""Internal-DAG renderer: a deterministic view of an authored internal-DAG graph (agent architecture).

The graph object (a `*.graph.json`) is the sole source of meaning. The diagram (HTML + standalone SVG +
self-view PNG), `agents.yaml`, `agents/<sub-agent>.yaml` and `dag-coverage-manifest.md` are all renders
of it; nothing here re-decides meaning. The renderer is generic: every label, id, count, stage and
interface comes from the graph. No client, counterparty, desk or real system name appears in this file.

Usage:  render_internal_dag.py GRAPH.graph.json [OUT_DIR]
Emits (into OUT_DIR, default = the graph's directory), stem = GRAPH basename minus ".graph.json":
  <stem>-diagram.html            page with no script (scrolling frame, hover detail as SVG titles)
  <stem>-diagram.svg             the standalone SVG (the same SVG the page embeds)
  <stem>-diagram.selfview.png    full-diagram raster, when rsvg-convert is available
  dag-coverage-manifest.md       structure checks and coverage (one graph per output folder)
  agents.yaml                    the agent block (and coordinator block when a root exists), rendered
  agents/<sub-agent-id>.yaml     one per band that comes from a sub-agent registry entry not of kind seam

Rule 1 of the plugin: the renderer never refuses an agent because of its shape.
  * The drawing mode comes from the graph's structure, never from a label: a graph with no sub-agents is
    drawn as one flow band; a graph with sub-agents is drawn one band per sub-agent. A root band appears
    when a root_agent node exists, a review band when a review_seam node exists, an interface band when an
    interface is drawn as a rail.
  * Known node types, edge kinds and interface kinds are drawn specially. Anything else is drawn
    generically under its own label (a step box with a muted stripe; a dashed link labelled with its kind;
    a dotted drop to a rail; a rail with a White plate naming its kind) and listed in the legend.
  * A cycle is drawn: the link that closes it becomes a return path with a note.
  * Unfamiliar or incomplete content gets a visible note (an "i" marker on the element, and the
    "Notes on this diagram" section in the page and manifest). Notes never fail.
  * Structural failures are only broken or wrong output: duplicate or missing ids, links to things that do
    not exist, mirror / anchor / membership disagreements, a declared count or scope the graph contradicts,
    an evidence value outside the four states. On a structural failure every output is still written, the
    "Structure checks" table names the failing ids, and the renderer exits 1. Exit 2 only when the input
    cannot be read at all (not JSON, `nodes` / `edges` not lists, a list field that is not a list).
  * Whether an agent write or an agent-held decision is allowed is not the renderer's call (it is the
    internal-DAG shape check's, which asks for a stated `authority_grant`). The renderer draws them
    distinctly and lists them as facts under "What the agent can change".

Layout: x = longest-path depth over the layout links (sequence / then / else / handoff and every unknown
kind between nodes), after a depth-first pass sets aside the links that close cycles; dispatch, inbound,
reentry and access links are excluded from layering. Connectors are routed orthogonally around every box on a
channel grid (Router); each label is wrapped whole and placed in a free slot on its own line (place_labels).
Interface ids are never the visible text: interfaces show their label, the id stays in data attributes and
<title>. Stdlib only; deterministic; zero model calls.
"""
import bisect
import hashlib
import heapq
import html
import json
import os
import re
import shutil
import subprocess
import sys
import textwrap
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _diagram_viewer import VIEWER_CSS, detail_items, svg_size, viewer  # noqa: E402

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "checks"))
from _no_script import assert_no_script  # noqa: E402

STATES = ("Observed", "Assumed", "Contradictory", "needs_more_information")
ATTENTION_STATES = ("Assumed", "needs_more_information", "Contradictory")
NA_TOKENS = ("n/a", "na", "none", "")

KNOWN_NODE_TYPES = ("root_agent", "step", "escalation_gate", "review_seam")
KNOWN_EDGE_KINDS = ("sequence", "then", "else", "handoff", "reentry", "dispatch", "read_access", "inbound", "write")
KNOWN_IFACE_KINDS = ("control", "read", "handoff", "draft_surface", "write")
BASE_LAYOUT_KINDS = ("sequence", "then", "else", "handoff")
KNOWN_COORDINATION = ("state-driven-dispatch", "independent-triggers")
DIRECTION_OF = {"control": "inbound_control", "read": "read", "handoff": "outbound_handoff",
                "draft_surface": "draft", "write": "write"}
WRITE_TOKENS = ("write", "writes", "all writes")

OP_TAG = {"agent_read": "READ", "agent_reconciliation": "RECOMMEND", "agent_stage": "STAGE",
          "escalation_gate": "ROUTE", "review_seam": "ANALYST", "schedule": "TRIGGER",
          "agent_assess": "ASSESS", "agent_consume": "INBOUND", "agent_triage": "TRIAGE"}
TYPE_TAG = {"step": "STEP", "escalation_gate": "GATE", "review_seam": "REVIEW SEAM", "root_agent": "ROOT AGENT"}

# ---------------------------------------------------------------- Within palette (+ derived utility inks)
CORAL = "#F56740"; MID = "#0B1E2F"; WHITE = "#FFFFFF"; CREAM = "#FBF6EF"
SUN = "#FFF5D2"; SKY = "#CFF3FE"; ROSE = "#FFE0D8"
CORAL_INK = "#B23A1E"; MUTED = "#4A5560"; FAINT = "#A79E92"; ACCESS = "#2E6E86"
FONT = "Inter, -apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif"

# ---------------------------------------------------------------- geometry (fixed constants)
X0 = 330; PITCH = 280
NODE_W = 180; NODE_H = 104; HW = NODE_W / 2      # NODE_H is the minimum; the drawn height fits the content
GATE_HW = 72; GATE_HH = 50
SEAM_W = 188; SEAM_H = 92; SHW = SEAM_W / 2       # SEAM_H is the minimum
ROW_GAP = 64                                      # free space between stacked boxes: routing channels + labels
BAND_PAD_TOP = 60; BAND_PAD_BOTTOM = 44
BAND_TOP = 96
ROOT_H = 104
LANE_LEFT = 96; SPINE_W = 36
RAIL_GAP = 30
PLATE_W = 322; PLATE_H = 48; ROOT_SPACING = 360
CHIP_W = 330
# connector routing: orthogonal paths on a channel grid around every box
GRID = 8; CLEAR = 9; STUB = 14
BEND = 40; OVER = 9.0; NEAR = 3.0; CROSS = 10.0
LABEL_PER = 5.0; LABEL_FS = 8.8; LABEL_LH = 11
FRACS = (0.5,) + tuple(v for k in range(1, 11) for v in (0.5 - 0.05 * k, 0.5 + 0.05 * k))

RULES = (
    ("F1", "ids are present and unique"),
    ("F2", "every link has both ends"),
    ("F3", "every named interface, state record, read and draft target exists"),
    ("F4", "every seating and anchor names a real element"),
    ("F5", "seated interfaces agree both ways"),
    ("F6", "each known interface is anchored to its steps"),
    ("F7", "sub-agent members agree with the steps"),
    ("F8", "handoffs match the declared count"),
    ("F9", "declared stage scope and interface set hold"),
    ("F10", "evidence states are one of the four"),
)

NOTE_TEXT = {
    "N-D1": "This coordinator has no dispatch links.",
    "N-D2": "More than one coordinator; each is drawn in the root band.",
    "N-D3": "Sub-agent '{0}' is not in the sub-agent list.",
    "N-D4": "No sub-agent stated for this step.",
    "N-D5": "This sub-agent has no steps.",
    "N-D6": "No handoff to a person downstream of this step.",
    "N-D7": "Drawn as a return path: this link closes a cycle.",
    "N-D8": "Handoff count not declared.",
    "N-D9": "Stage scope not declared; derived from sub-agent order.",
    "N-D10": "Not reachable from a start or trigger.",
    "N-D11": "Not seated in a sub-agent or step.",
    "N-D12": "The header says the agent writes nowhere, but this design has agent writes or decisions.",
}


class Unreadable(Exception):
    pass


def esc(s):
    return html.escape(str(s), quote=True)


def yq(s):
    return '"%s"' % str(s).replace("\\", "\\\\").replace('"', '\\"')


def wrap(text, width=22, maxlines=3):
    lines = textwrap.wrap(str(text), width) or [""]
    if len(lines) > maxlines:
        lines = lines[:maxlines]
        lines[-1] = lines[-1][:width - 1].rstrip() + "…"
    return lines


def wrap_all(text, width):
    """Every word kept: wrapped to lines of at most `width` characters (a longer single word stays whole)."""
    return textwrap.wrap(str(text), width, break_long_words=False, break_on_hyphens=False) or [""]


class Router(object):
    """Orthogonal connector routing on a channel grid. Grid lines run every GRID px plus the exact port
    coordinates; a grid point inside any obstacle (inflated by CLEAR) is closed. A* over (point, heading)
    with a bend cost, a cost for running on top of / right next to an earlier connector and a small cost
    for crossing one. Deterministic: ties break on insertion order."""

    def __init__(self, obstacles, xs, ys, spine_x1):
        self.xs = sorted(set(round(v, 1) for v in xs))
        self.ys = sorted(set(round(v, 1) for v in ys))
        self.xi = {v: k for k, v in enumerate(self.xs)}
        self.yi = {v: k for k, v in enumerate(self.ys)}
        NX, NY = len(self.xs), len(self.ys)
        self.NX, self.NY = NX, NY
        self.closed = bytearray(NX * NY)
        for (x0, y0, x1, y1) in obstacles:
            a = bisect.bisect_right(self.xs, x0 - CLEAR); b = bisect.bisect_left(self.xs, x1 + CLEAR)
            c = bisect.bisect_right(self.ys, y0 - CLEAR); d = bisect.bisect_left(self.ys, y1 + CLEAR)
            for ix in range(a, b):
                base = ix * NY
                for iy in range(c, d):
                    self.closed[base + iy] = 1
        self.spine = [x < spine_x1 for x in self.xs]
        self.hocc = defaultdict(int)   # (ix, iy) -> uses of the unit edge (ix,iy)-(ix+1,iy)
        self.vocc = defaultdict(int)   # (ix, iy) -> uses of the unit edge (ix,iy)-(ix,iy+1)
        self.hnode = set(); self.vnode = set()

    def has(self, p):
        return round(p[0], 1) in self.xi and round(p[1], 1) in self.yi

    def route(self, s, sdir, t, tdir, spine_x=None, limit=600000):
        """Path from grid point s (leaving with heading sdir) to grid point t (arriving with heading tdir).
        Headings: 0 +x, 1 +y, 2 -x, 3 -y. Returns the list of corner points, or None."""
        xs, ys, NY = self.xs, self.ys, self.NY
        sx, sy = self.xi[round(s[0], 1)], self.yi[round(s[1], 1)]
        tx, ty = self.xi[round(t[0], 1)], self.yi[round(t[1], 1)]
        TX, TY = xs[tx], ys[ty]
        DX = (1, 0, -1, 0); DY = (0, 1, 0, -1)
        closed, spine, hocc, vocc, hnode, vnode = self.closed, self.spine, self.hocc, self.vocc, self.hnode, self.vnode
        start = ((sx * NY + sy) << 2) | sdir
        g = {start: 0.0}
        parent = {start: None}
        heap = [(abs(xs[sx] - TX) + abs(ys[sy] - TY), 0.0, 0, start)]
        cnt = 1
        goal = None
        n = 0
        while heap:
            f, gc, _, st = heapq.heappop(heap)
            if st == -1:
                break
            if gc > g.get(st, 1e18):
                continue
            n += 1
            if n > limit:
                return None
            d = st & 3; node = st >> 2; ix, iy = divmod(node, NY)
            if ix == tx and iy == ty:
                tot = gc + (0 if d == tdir else BEND)
                if goal is None or tot < goal[0]:
                    goal = (tot, st)
                    heapq.heappush(heap, (tot, tot, cnt, -1)); cnt += 1
                continue
            for nd in (d, (d + 1) & 3, (d + 3) & 3):
                jx, jy = ix + DX[nd], iy + DY[nd]
                if jx < 0 or jy < 0 or jx >= self.NX or jy >= NY:
                    continue
                if closed[jx * NY + jy] and not (jx == tx and jy == ty):
                    continue
                if spine[jx]:
                    if spine_x is None:
                        continue
                    if nd & 1 and abs(xs[jx] - spine_x) > 0.05:
                        continue
                if nd & 1:
                    k = (ix, min(iy, jy)); ln = abs(ys[jy] - ys[iy])
                    c = ln + OVER * ln * vocc.get(k, 0)
                    for kk in ((ix - 1, k[1]), (ix + 1, k[1])):
                        if 0 <= kk[0] < self.NX and abs(xs[kk[0]] - xs[ix]) < 7 and vocc.get(kk, 0):
                            c += NEAR * ln
                    if (jx, jy) in hnode:
                        c += CROSS
                else:
                    k = (min(ix, jx), iy); ln = abs(xs[jx] - xs[ix])
                    c = ln + OVER * ln * hocc.get(k, 0)
                    for kk in ((k[0], iy - 1), (k[0], iy + 1)):
                        if 0 <= kk[1] < NY and abs(ys[kk[1]] - ys[iy]) < 7 and hocc.get(kk, 0):
                            c += NEAR * ln
                    if (jx, jy) in vnode:
                        c += CROSS
                if nd != d:
                    c += BEND
                ns = ((jx * NY + jy) << 2) | nd
                ng = gc + c
                if ng < g.get(ns, 1e18):
                    g[ns] = ng
                    parent[ns] = st
                    heapq.heappush(heap, (ng + abs(xs[jx] - TX) + abs(ys[jy] - TY), ng, cnt, ns)); cnt += 1
        if goal is None:
            return None
        cells = []
        st = goal[1]
        while st is not None:
            node = st >> 2
            cells.append(divmod(node, NY))
            st = parent[st]
        cells.reverse()
        for (ax, ay), (bx, by) in zip(cells, cells[1:]):
            if ay == by:
                self.hocc[(min(ax, bx), ay)] += 1
                self.hnode.add((ax, ay)); self.hnode.add((bx, by))
            else:
                self.vocc[(ax, min(ay, by))] += 1
                self.vnode.add((ax, ay)); self.vnode.add((bx, by))
        return simplify([(xs[a], ys[b]) for a, b in cells])


def simplify(pts):
    """Drop repeated and collinear interior points of an orthogonal polyline."""
    out = []
    for p in pts:
        p = (round(p[0], 1), round(p[1], 1))
        if out and abs(out[-1][0] - p[0]) < 0.05 and abs(out[-1][1] - p[1]) < 0.05:
            continue
        if len(out) >= 2:
            a, b = out[-2], out[-1]
            if (abs(a[0] - b[0]) < 0.05 and abs(b[0] - p[0]) < 0.05) or (abs(a[1] - b[1]) < 0.05 and abs(b[1] - p[1]) < 0.05):
                out[-1] = p
                continue
        out.append(p)
    return out


def rects_hit(a, b, pad=0.0):
    return a[0] < b[2] + pad and b[0] < a[2] + pad and a[1] < b[3] + pad and b[1] < a[3] + pad


def cut(text, n):
    text = str(text)
    return text if len(text) <= n else text[:n - 1].rstrip() + "…"


def spaced(s):
    return str(s).replace("_", " ")


def first_sentence(text, limit):
    text = " ".join(str(text).split())
    m = re.search(r"\.(\s|$)", text)
    if m:
        text = text[:m.start() + 1]
    return cut(text, limit)


def ordinal(n):
    return {1: "1st", 2: "2nd", 3: "3rd"}.get(n, "%dth" % n)


def as_list(v, name):
    if v is None:
        return []
    if isinstance(v, str) and v.strip().lower() in NA_TOKENS:
        return []
    if not isinstance(v, list):
        raise Unreadable("'%s' must be a list" % name)
    return v


def id_list(v):
    """A field that accepts a string or a list of ids (never parsed as prose)."""
    if v is None:
        return []
    if isinstance(v, list):
        return [str(x) for x in v if x is not None]
    return [str(v)]


def txt(v, default=""):
    if v is None or v == "":
        return default
    if isinstance(v, (list, dict)):
        return json.dumps(v, ensure_ascii=False)
    return str(v)


def is_write_token(v):
    return isinstance(v, str) and v.strip().lower() in WRITE_TOKENS


def has_grant(x):
    g = x.get("authority_grant")
    return isinstance(g, str) and g.strip() != ""


class Dag(object):
    # ================================================================= load + normalize
    def __init__(self, g, graph_path):
        if not isinstance(g, dict):
            raise Unreadable("the graph is not a JSON object")
        if not isinstance(g.get("nodes"), list):
            raise Unreadable("'nodes' must be a list")
        if not isinstance(g.get("edges"), list):
            raise Unreadable("'edges' must be a list")
        self.g = g
        self.base = os.path.basename(graph_path)
        self.stem = self.base[:-len(".graph.json")] if self.base.endswith(".graph.json") else os.path.splitext(self.base)[0]
        self.sha = hashlib.sha256(open(graph_path, "rb").read()).hexdigest()
        raw_nodes = g["nodes"]
        raw_edges = g["edges"]
        raw_subs = as_list(g.get("sub_agents"), "sub_agents")
        raw_ifaces = as_list(g.get("interfaces"), "interfaces")
        self.open_register = as_list(g.get("open_register"), "open_register")
        self.assumptions = as_list(g.get("assumptions"), "assumptions")
        for name, lst in (("nodes", raw_nodes), ("edges", raw_edges), ("sub_agents", raw_subs),
                          ("interfaces", raw_ifaces), ("open_register", self.open_register),
                          ("assumptions", self.assumptions)):
            for x in lst:
                if not isinstance(x, dict):
                    raise Unreadable("every entry of '%s' must be an object" % name)
        self.hdr = g.get("header") if isinstance(g.get("header"), dict) else {}
        self.agent = g.get("agent") if isinstance(g.get("agent"), dict) else {}
        self.rec_id = txt(self.agent.get("rec_id"), self.stem)
        self.spec_id = txt(self.agent.get("spec_id"), self.rec_id)
        self.primitive_form = txt(self.agent.get("primitive_form"), "not stated")

        self.fail = defaultdict(list)     # rule -> [ids]
        self.notes = []                   # [(id or "", text)]

        def uniq(lst, what):
            out, seen = [], set()
            for i, x in enumerate(lst):
                xid = x.get("id")
                if xid is None or str(xid) == "":
                    self.fail["F1"].append("%s #%d has no id" % (what, i))
                    continue
                xid = str(xid)
                if xid in seen:
                    self.fail["F1"].append(xid)
                    continue
                seen.add(xid)
                out.append(x)
            return out

        self.nodes = uniq(raw_nodes, "node")
        self.edges = uniq(raw_edges, "edge")
        self.ifaces = uniq(raw_ifaces, "interface")
        self.subs = uniq(raw_subs, "sub-agent")
        self.N = {str(n["id"]): n for n in self.nodes}
        self.I = {str(i["id"]): i for i in self.ifaces}
        self.SA = {str(s["id"]): s for s in self.subs}
        for nid in self.N:
            if nid in self.I:
                self.fail["F1"].append(nid)
        self.node_order = [str(n["id"]) for n in self.nodes]
        self.nidx = {nid: k for k, nid in enumerate(self.node_order)}

        self.all_ids = set(self.N) | set(self.I) | set(self.SA) | {str(e["id"]) for e in self.edges}
        pat = "|".join(re.escape(x) for x in sorted(self.all_ids, key=len, reverse=True) if x)
        self.id_re = re.compile(r"(?<![A-Za-z0-9._:-])(?:%s)(?![A-Za-z0-9._:-])" % pat) if pat else None

        self.ntype = {nid: txt(n.get("type"), "step") for nid, n in self.N.items()}
        self.roots = [nid for nid in self.node_order if self.ntype[nid] == "root_agent"]
        self.seams = [nid for nid in self.node_order if self.ntype[nid] == "review_seam"]
        self.act = [nid for nid in self.node_order if self.ntype[nid] != "root_agent"]
        self.seam_kind_ids = {sid for sid, s in self.SA.items() if txt(s.get("kind"), "agent") == "seam"}

        self.coord_kind = self.hdr.get("coordination_shape_kind")
        if not self.coord_kind:
            self.coord_kind = "state-driven-dispatch" if self.roots else None
        self.coord_kind = txt(self.coord_kind) if self.coord_kind else None

        self._classify_edges()
        self._checks()
        self._modes_and_bands()
        self._layout()
        self._facts()
        self._notes()

    def tw(self, text, per=5.4):
        """Text width estimate. Ids count as a fixed width so geometry never depends on an id literal."""
        t = str(text)
        if self.id_re is not None:
            t = self.id_re.sub("IDxxxxxxxx", t)
        return len(t) * per

    def nsub(self, nid):
        v = self.N[nid].get("sub_agent")
        return None if v is None or str(v) == "" else str(v)

    # ================================================================= edges
    def _classify_edges(self):
        self.E = []          # drawable edges (dicts with computed keys)
        for e in self.edges:
            eid = str(e["id"])
            f = e.get("from"); t = e.get("to")
            f = None if f is None else str(f)
            t = None if t is None else str(t)
            kind = txt(e.get("kind"), "link")
            ok = True
            for end in (f, t):
                if end is None or (end not in self.N and end not in self.I):
                    ok = False
            if not ok:
                self.fail["F2"].append(eid)
                continue
            fn, tn = f in self.N, t in self.N
            if fn and tn:
                if self.ntype[f] == "root_agent" and self.ntype[t] == "root_agent":
                    route = "rootlink"
                elif self.ntype[f] == "root_agent":
                    route = "bus"
                elif self.ntype[t] == "root_agent":
                    route = "toroot"
                elif kind == "inbound":
                    route = "inbound"
                elif kind == "reentry":
                    route = "reentry"
                else:
                    route = "step"
            elif fn and not tn:
                route = "drop"
            elif tn and not fn:
                route = "rise"
            else:
                route = "rail"
            layout = (route == "step" and (kind in BASE_LAYOUT_KINDS or kind not in KNOWN_EDGE_KINDS))
            self.E.append({"e": e, "id": eid, "from": f, "to": t, "kind": kind, "route": route,
                           "layout": layout})
        self.Eid = {x["id"]: x for x in self.E}

    # ================================================================= structure checks (F3-F10)
    def _checks(self):
        N, I, SA = self.N, self.I, self.SA
        for x in self.E:
            e = x["e"]
            if e.get("interface") is not None and str(e.get("interface")) not in I:
                self.fail["F3"].append(x["id"])
            if e.get("reads_state_from") is not None and str(e.get("reads_state_from")) not in N:
                self.fail["F3"].append(x["id"])
        for nid in self.node_order:
            n = N[nid]
            reads = n.get("reads")
            if reads is not None and not isinstance(reads, list):
                reads = [reads]
            if any(str(r) not in I for r in (reads or [])):
                self.fail["F3"].append(nid)
            if n.get("drafts_into") is not None and str(n.get("drafts_into")) not in I:
                self.fail["F3"].append(nid)
        for iid, i in I.items():
            bad = [s for s in id_list(i.get("seated_in")) if s not in SA and s not in N]
            bad += [s for k in ("at_step", "also_drafted_by", "also_emitted_by") for s in id_list(i.get(k)) if s not in N]
            if bad:
                self.fail["F4"].append(iid)
        # F5 mirror
        containers = [(sid, s) for sid, s in SA.items()] + [(nid, N[nid]) for nid in self.node_order]
        for cid, c in containers:
            if "seated_interfaces" not in c:
                continue
            listed = id_list(c.get("seated_interfaces"))
            bad = False
            for iid in listed:
                if iid not in I or cid not in id_list(I[iid].get("seated_in")):
                    bad = True
            for iid, i in I.items():
                if cid in id_list(i.get("seated_in")) and iid not in listed:
                    bad = True
            if bad:
                self.fail["F5"].append(cid)
        # F6 known-kind anchors
        for iid, i in I.items():
            k = txt(i.get("kind"), "interface")
            anchors = [a for a in id_list(i.get("at_step")) if a in N]
            ok = True
            if k == "read":
                for a in anchors:
                    if not any(x["kind"] == "read_access" and x["from"] == a and x["to"] == iid for x in self.E):
                        ok = False
                    if "reads" in N[a]:
                        rl = N[a].get("reads")
                        rl = rl if isinstance(rl, list) else [rl]
                        if iid not in [str(r) for r in rl]:
                            ok = False
            elif k == "handoff":
                anchors += [a for a in id_list(i.get("also_emitted_by")) if a in N]
                for a in anchors:
                    if not any(x["kind"] == "handoff" and x["from"] == a and txt(x["e"].get("interface")) == iid for x in self.E):
                        ok = False
            elif k == "draft_surface":
                anchors += [a for a in id_list(i.get("also_drafted_by")) if a in N]
                for a in anchors:
                    if txt(N[a].get("drafts_into")) != iid:
                        ok = False
            if not ok:
                self.fail["F6"].append(iid)
        # F7 membership
        for sid, s in SA.items():
            if "member_nodes" not in s:
                continue
            declared = set(id_list(s.get("member_nodes")))
            actual = {nid for nid in self.node_order if self.nsub(nid) == sid}
            if declared != actual:
                self.fail["F7"].append(sid)
        # F8 declared handoff count
        self.handoffs = [x for x in self.E if x["kind"] == "handoff"]
        dh = self.hdr.get("declared_handoff_count")
        self.declared_handoffs = dh
        if dh is not None:
            if isinstance(dh, bool) or not isinstance(dh, int):
                self.fail["F8"].append("declared_handoff_count")
            elif dh != len([e for e in self.edges if txt(e.get("kind")) == "handoff"]):
                self.fail["F8"].extend(x["id"] for x in self.handoffs)
                if not self.handoffs:
                    self.fail["F8"].append("declared_handoff_count")
        # F9 declared stage scope / closed interface set
        self.stage_declared = "stage_scope" in self.hdr and self.hdr.get("stage_scope") is not None
        self.closed_declared = self.hdr.get("closed_interface_set") is not None
        if self.closed_declared:
            cs = id_list(self.hdr.get("closed_interface_set"))
            if set(cs) != set(I):
                self.fail["F9"].append("closed_interface_set")
            self.closed_set = cs
        else:
            self.closed_set = list(I)
        # F10 evidence values
        for lst in (self.nodes, self.edges, self.ifaces, self.subs):
            for x in lst:
                v = x.get("evidence_state")
                if v is not None and v not in STATES:
                    self.fail["F10"].append(str(x.get("id")))
        for k in self.hdr:
            if str(k).endswith("_evidence_state") and self.hdr[k] is not None and self.hdr[k] not in STATES:
                self.fail["F10"].append("header." + str(k))
        for k, a in enumerate(self.assumptions):
            v = a.get("access_state")
            if v is not None and v not in STATES:
                self.fail["F10"].append(txt(a.get("surface"), "assumption #%d" % k))

    # ================================================================= drawing mode + bands
    def _modes_and_bands(self):
        registry = [str(s["id"]) for s in self.subs]
        self.single_flow = (not self.subs) and all(
            self.nsub(nid) in (None, "root", "review-seam") for nid in self.node_order)
        positioned = [nid for nid in self.act if self.ntype[nid] != "review_seam"]
        self.bands = []   # dicts: key, kind, label, sub, fill
        if self.roots:
            self.bands.append({"key": "#root", "kind": "root", "label": "ROOT AGENT",
                               "sub": "coordinator" if self.coord_kind != "state-driven-dispatch" else "dispatcher"})
        self.band_of = {}
        self.flow_band_keys = []
        self.ordinals = bool(self.roots) and self.coord_kind == "state-driven-dispatch"
        if self.single_flow:
            lbl = self.hdr.get("flow_label")
            if not lbl:
                lbl = "SCRIPT" if self.primitive_form == "script" else "AGENT"
            self.flow_label = txt(lbl)
            self.bands.append({"key": "#flow", "kind": "flow", "label": self.flow_label.upper(),
                               "sub": "one flow · no sub-agents"})
            self.flow_band_keys.append("#flow")
            for nid in positioned:
                self.band_of[nid] = "#flow"
        else:
            self.flow_label = None
            used_by_positioned = {self.nsub(nid) for nid in positioned}
            k_ord = 0
            for sid in registry:
                s = self.SA[sid]
                kind = txt(s.get("kind"), "agent")
                if kind == "seam" and sid not in used_by_positioned:
                    continue
                sub = "%s sub-agent" % kind
                if self.ordinals and kind != "seam":
                    k_ord += 1
                    sub += " · %s invoked" % ordinal(k_ord)
                self.bands.append({"key": "sa:" + sid, "kind": "agent", "label": txt(s.get("label"), sid).upper(),
                                   "sub": sub, "sid": sid})
                self.flow_band_keys.append("sa:" + sid)
            extra = []
            for nid in positioned:
                v = self.nsub(nid)
                if v is not None and v not in self.SA and v not in extra:
                    extra.append(v)
            for v in extra:
                self.bands.append({"key": "x:" + v, "kind": "extra", "label": v.upper(),
                                   "sub": "not in the sub-agent list", "sid": v})
                self.flow_band_keys.append("x:" + v)
            if any(self.nsub(nid) is None for nid in positioned):
                self.bands.append({"key": "#unassigned", "kind": "unassigned", "label": "UNASSIGNED",
                                   "sub": "no sub-agent stated"})
                self.flow_band_keys.append("#unassigned")
            for nid in positioned:
                v = self.nsub(nid)
                if v is None:
                    self.band_of[nid] = "#unassigned"
                elif v in self.SA:
                    self.band_of[nid] = "sa:" + v
                else:
                    self.band_of[nid] = "x:" + v
        if self.seams:
            self.bands.append({"key": "#review", "kind": "review", "label": "REVIEW SEAM",
                               "sub": "person-owned decisions"})
            for nid in self.seams:
                self.band_of[nid] = "#review"
        self.band_index = {b["key"]: k for k, b in enumerate(self.bands)}
        self.B = {b["key"]: b for b in self.bands}

        # which interfaces are drawn as what
        handoff_ifaces_used = []
        for x in self.handoffs:
            iid = txt(x["e"].get("interface"))
            if iid in self.I and iid not in handoff_ifaces_used:
                handoff_ifaces_used.append(iid)
        self.handoff_chip_edge = {}
        for x in self.handoffs:
            iid = txt(x["e"].get("interface"))
            if iid in self.I and txt(self.I[iid].get("kind")) == "handoff" and iid not in self.handoff_chip_edge:
                self.handoff_chip_edge[iid] = x["id"]
        self.chips = [iid for iid in self.I if txt(self.I[iid].get("kind")) == "control" and self.roots]
        order = {"read": 0, "draft_surface": 1, "write": 2}
        rails = [iid for iid in self.I if iid not in self.chips and iid not in self.handoff_chip_edge]
        self.rails = sorted(rails, key=lambda iid: (order.get(txt(self.I[iid].get("kind"), "interface"), 3),
                                                     list(self.I).index(iid)))

    # ================================================================= layout
    def _layout(self):
        act = self.act
        out_layout = defaultdict(list)
        indeg = {nid: 0 for nid in act}
        for x in self.E:
            if x["layout"]:
                out_layout[x["from"]].append(x)
                indeg[x["to"]] += 1
        starts = []
        for x in self.E:
            if x["route"] == "bus" and x["kind"] == "dispatch" and x["to"] not in starts:
                starts.append(x["to"])
        for nid in act:
            if self.N[nid].get("entry_trigger") and nid not in starts:
                starts.append(nid)
        for nid in act:
            if indeg[nid] == 0 and nid not in starts:
                starts.append(nid)
        for nid in act:
            if nid not in starts:
                starts.append(nid)
        color = {nid: 0 for nid in act}
        self.returns = set()
        for s in starts:
            if color[s]:
                continue
            color[s] = 1
            stack = [(s, 0)]
            while stack:
                u, k = stack[-1]
                outs = out_layout[u]
                if k < len(outs):
                    stack[-1] = (u, k + 1)
                    x = outs[k]
                    v = x["to"]
                    if color[v] == 1:
                        self.returns.add(x["id"])
                    elif color[v] == 0:
                        color[v] = 1
                        stack.append((v, 0))
                else:
                    color[u] = 2
                    stack.pop()
        for x in self.E:
            if x["id"] in self.returns:
                x["route"] = "return"
                x["layout"] = False
        adj = defaultdict(list)
        indeg = {nid: 0 for nid in act}
        for x in self.E:
            if x["layout"]:
                adj[x["from"]].append(x["to"])
                indeg[x["to"]] += 1
        q = [nid for nid in act if indeg[nid] == 0]
        order = []
        while q:
            u = q.pop(0)
            order.append(u)
            for v in adj[u]:
                indeg[v] -= 1
                if indeg[v] == 0:
                    q.append(v)
        self.depth = {nid: 0 for nid in act}
        for u in order:
            for v in adj[u]:
                self.depth[v] = max(self.depth[v], self.depth[u] + 1)
        self.maxdepth = max(self.depth.values()) if self.depth else 0

        # stacking: cells by (depth, band); review seams by the band whose handoff lands on them
        src_band = {}
        for x in self.handoffs:
            if x["to"] in self.seams and x["from"] in self.band_of:
                bi = self.band_index[self.band_of[x["from"]]]
                src_band[x["to"]] = min(src_band.get(x["to"], 10 ** 6), bi)
        groups = defaultdict(list)
        for nid in act:
            groups[(self.depth[nid], self.band_of[nid])].append(nid)
        self.slot = {}
        self.stack_count = defaultdict(int)
        for (d, bk), members in groups.items():
            if bk == "#review":
                members.sort(key=lambda n: (src_band.get(n, 10 ** 6), self.nidx[n]))
            else:
                members.sort(key=lambda n: self.nidx[n])
            for k, nid in enumerate(members):
                self.slot[nid] = (k, len(members))
            self.stack_count[bk] = max(self.stack_count[bk], len(members))

        # root band content (plates + control chips) sizes the root band
        self.chip_geo = []
        chips_bottom = 0
        if self.roots:
            px_last = LANE_LEFT + SPINE_W + 26 + (len(self.roots) - 1) * ROOT_SPACING
            self.chip_x = px_last + PLATE_W + 20
            cursor = 28
            for iid in self.chips:
                i = self.I[iid]
                lines = wrap_all(txt(i.get("label"), iid), 52)
                h = 26 + len(lines) * 11 + 8
                self.chip_geo.append((iid, cursor, h, lines))
                cursor += h + 6
            chips_bottom = cursor - 6 if self.chips else 0

        # box heights fit their content (one height per kind so stacked cells line up)
        self.step_geo = {nid: self.step_layout(nid) for nid in act if self.ntype[nid] not in ("escalation_gate", "review_seam")}
        # Fit an inscribed title rectangle at a fixed readable size; grow the diamond
        # and its row instead of shrinking or clipping the authored title.
        self.gate_hw = 110
        self.gate_hh = max([GATE_HH] + [len(self.gate_text(nid)[0]) * 14 + 24
                                      for nid in act if self.ntype[nid] == "escalation_gate"])
        self.node_h = max([NODE_H, 2 * self.gate_hh + 40] + [v["need"] for v in self.step_geo.values()])
        self.seam_geo = {nid: self.seam_layout(nid) for nid in self.seams}
        self.seam_h = max([SEAM_H] + [v["need"] for v in self.seam_geo.values()])
        y = BAND_TOP
        for b in self.bands:
            if b["kind"] == "root":
                h = max(ROOT_H, chips_bottom + 28)
            else:
                c = self.stack_count.get(b["key"], 1) or 1
                bh = self.seam_h if b["kind"] == "review" else self.node_h
                h = BAND_PAD_TOP + c * bh + (c - 1) * ROW_GAP + BAND_PAD_BOTTOM
            b["top"] = y; b["h"] = h
            y += h
        self.bands_bottom = y

        # root plates
        self.root_pos = {}
        if self.roots:
            top = self.B["#root"]["top"]
            for k, rid in enumerate(self.roots):
                px = LANE_LEFT + SPINE_W + 26 + k * ROOT_SPACING
                py = top + (ROOT_H - PLATE_H) / 2.0
                self.root_pos[rid] = (px, py)

    def cx(self, nid):
        if nid in self.root_pos:
            return self.root_pos[nid][0] + PLATE_W / 2.0
        return X0 + self.depth[nid] * PITCH

    def cy(self, nid):
        if nid in self.root_pos:
            return self.root_pos[nid][1] + PLATE_H / 2.0
        b = self.B[self.band_of[nid]]
        k, c = self.slot[nid]
        bh = self.seam_h if b["kind"] == "review" else self.node_h
        stack = c * bh + (c - 1) * ROW_GAP
        room = b["h"] - BAND_PAD_TOP - BAND_PAD_BOTTOM
        return b["top"] + BAND_PAD_TOP + (room - stack) / 2.0 + bh / 2.0 + k * (bh + ROW_GAP)

    def dims(self, nid):
        if nid in self.root_pos:
            return PLATE_W / 2.0, PLATE_H / 2.0
        t = self.ntype[nid]
        if t == "escalation_gate":
            return self.gate_hw, self.gate_hh
        if t == "review_seam":
            return SHW, self.seam_h / 2.0
        return HW, self.node_h / 2.0

    # ---------------------------------------------------------------- box content (sizes the boxes)
    def iface_label(self, iid):
        i = self.I.get(iid)
        return txt(i.get("label"), iid) if i else iid

    def step_layout(self, nid):
        """Where each piece of a step box sits, top-down, so nothing in the box overlaps: header row (tag, id),
        a badge row (decides, runs-on, handoff), the title, then a bottom row (what it reads / drafts, evidence)."""
        n = self.N[nid]
        ro = self.runs_on(nid)
        badges = []
        if ro == "model":
            badges.append(("runs", "AI STEP", 14 + 7 * 5.2))
        elif ro not in (None, "code"):
            lab = cut(spaced(ro).upper(), 14)
            badges.append(("runs", lab, 14 + len(lab) * 5.2))
        if n.get("emits_handoff"):
            badges.append(("handoff", "→ HANDOFF", 14 + 9 * 5.0))
        left_w = 50 if n.get("authoritative") is True else 0
        rows = []
        cur, used = [], left_w
        for b in badges:
            if cur and used + 6 + b[2] > NODE_W - 24:
                rows.append(cur); cur, used = [], 0
            cur.append(b); used += 6 + b[2]
        if cur or left_w:
            rows.append(cur)
        title = txt(n.get("title"), nid)
        lines = wrap_all(title, 22)
        if len(lines) > 4:
            lines = wrap_all(title, 26)
        first = 40 + len(rows) * 15
        reads = n.get("reads")
        reads = [str(r) for r in (reads if isinstance(reads, list) else ([reads] if reads else []))]
        if reads:
            access = ("reads %d interface%s" % (len(reads), "" if len(reads) == 1 else "s"), ACCESS,
                      [self.iface_label(r) for r in reads], reads)
        elif n.get("drafts_into"):
            d = txt(n.get("drafts_into"))
            access = ("drafts for a person", CORAL_INK, [self.iface_label(d)], [d])
        else:
            access = None
        ev = n.get("evidence_state")
        chip_w = 0
        if ev is not None and ev != "Observed":
            lab = "needs more information" if ev == "needs_more_information" else cut(str(ev), 28)
            chip_w = 14 + len(lab) * 4.7 + (12 if ev not in STATES else 0)
        bottom_rows = 0
        split = False
        if access and chip_w:
            split = 16 + len(access[0]) * 4.6 + 8 + chip_w > NODE_W - 8
            bottom_rows = 2 if split else 1
        elif access or chip_w:
            bottom_rows = 1
        need = first + (len(lines) - 1) * 14 + 6 + bottom_rows * 15 + 6
        return {"rows": rows, "lines": lines, "first": first, "access": access, "split": split,
                "chip": bool(chip_w), "need": need}

    def seam_layout(self, nid):
        n = self.N[nid]
        owner = n.get("owner")
        head = "REVIEW SEAM · %s-owned" % txt(owner) if owner else "REVIEW SEAM"
        head_lines = wrap_all(head, 27)
        lines = wrap_all(txt(n.get("title"), nid), 28)
        first = 16 + (len(head_lines) - 1) * 11 + 18
        need = first + (len(lines) - 1) * 13 + 6 + 18
        return {"head": head_lines, "lines": lines, "first": first, "need": need}

    def port(self, nid, side):
        x = self.cx(nid); y = self.cy(nid); hw, hh = self.dims(nid)
        return {"r": (x + hw, y), "l": (x - hw, y), "t": (x, y - hh), "b": (x, y + hh)}[side]

    # ================================================================= facts: writes, decisions, runs_on
    def _facts(self):
        self.write_edges = [x for x in self.E if x["kind"] == "write" or is_write_token(x["e"].get("access_kind"))]
        self.write_edges_all = [e for e in self.edges if txt(e.get("kind")) == "write" or is_write_token(e.get("access_kind"))]
        self.write_ifaces = [iid for iid, i in self.I.items()
                             if txt(i.get("kind")) == "write" or is_write_token(i.get("access_kind"))]
        self.decisions = [nid for nid in self.node_order
                          if self.N[nid].get("authoritative") is True and self.ntype[nid] != "review_seam"
                          and self.nsub(nid) not in self.seam_kind_ids]
        self.zero_boundary = not (self.write_edges_all or self.write_ifaces or self.decisions)

    def runs_on(self, nid):
        n = self.N[nid]
        r = n.get("runs_on")
        if r is None or r == "":
            a = n.get("is_ai_node")
            if a is True:
                return "model"
            if a is False:
                return "code"
            return None
        return str(r)

    # ================================================================= notes
    def _notes(self):
        notes = []
        # id-bound notes, in graph order: nodes, then edges, then interfaces, then sub-agents; page notes last
        dispatch_from = defaultdict(int)
        for x in self.E:
            if x["kind"] == "dispatch":
                dispatch_from[x["from"]] += 1
        # reachability (N-D10)
        starts = set()
        for x in self.E:
            if x["route"] == "bus":
                starts.add(x["to"])
        indeg = defaultdict(int)
        for x in self.E:
            if x["layout"] or x["route"] == "return":
                indeg[x["to"]] += 1
        for nid in self.act:
            if self.N[nid].get("entry_trigger") or indeg[nid] == 0:
                starts.add(nid)
        walk_kinds = set(BASE_LAYOUT_KINDS) | {"dispatch", "inbound", "reentry"}
        reach = set()
        stack = list(starts)
        while stack:
            u = stack.pop()
            if u in reach:
                continue
            reach.add(u)
            for x in self.E:
                if x["from"] == u and x["to"] in self.N and (
                        x["layout"] or x["route"] == "return" or x["kind"] in walk_kinds):
                    stack.append(x["to"])
        # handoff reachable (N-D6)
        def hands_off(nid):
            seen = set(); st = [nid]
            while st:
                u = st.pop()
                if u in seen:
                    continue
                seen.add(u)
                if self.N[u].get("emits_handoff"):
                    return True
                for x in self.E:
                    if x["from"] == u and x["to"] in self.N and (x["layout"] or x["route"] in ("return", "reentry")):
                        if x["kind"] == "handoff":
                            return True
                        st.append(x["to"])
            return False
        for nid in self.node_order:
            t = self.ntype[nid]
            if t == "root_agent":
                if dispatch_from[nid] == 0:
                    notes.append((nid, NOTE_TEXT["N-D1"]))
                continue
            if t != "review_seam" and not self.single_flow:
                v = self.nsub(nid)
                if v is None:
                    notes.append((nid, NOTE_TEXT["N-D4"]))
                elif v not in self.SA:
                    notes.append((nid, NOTE_TEXT["N-D3"].format(v)))
            if t != "review_seam" and self.seams and not hands_off(nid):
                notes.append((nid, NOTE_TEXT["N-D6"]))
            if nid not in reach:
                notes.append((nid, NOTE_TEXT["N-D10"]))
        for x in self.E:
            if x["route"] == "return":
                notes.append((x["id"], NOTE_TEXT["N-D7"]))
        for iid, i in self.I.items():
            if not id_list(i.get("seated_in")):
                notes.append((iid, NOTE_TEXT["N-D11"]))
        for sid, s in self.SA.items():
            if txt(s.get("kind"), "agent") == "agent" and not any(self.nsub(n) == sid for n in self.node_order):
                notes.append((sid, NOTE_TEXT["N-D5"]))
        if len(self.roots) > 1:
            notes.append(("", NOTE_TEXT["N-D2"]))
        if self.declared_handoffs is None and self.handoffs:
            notes.append(("", NOTE_TEXT["N-D8"]))
        # stage scope
        self.stage_scope = None
        self.stage_derived = False
        if self.stage_declared:
            self.stage_scope = [str(s) for s in id_list(self.hdr.get("stage_scope"))]
        elif not self.single_flow:
            derived = []
            for s in self.subs:
                for st in id_list(s.get("owned_stages")):
                    if st not in derived:
                        derived.append(st)
            if derived:
                self.stage_scope = derived
                self.stage_derived = True
                notes.append(("", NOTE_TEXT["N-D9"]))
        self.stage_cover = {}
        if self.stage_scope is not None:
            for st in self.stage_scope:
                if self.single_flow:
                    self.stage_cover[st] = ["#flow"]
                else:
                    self.stage_cover[st] = [str(s["id"]) for s in self.subs if st in id_list(s.get("owned_stages"))]
            if self.stage_declared and not self.single_flow:
                for st in self.stage_scope:
                    if not self.stage_cover[st]:
                        self.fail["F9"].append("stage " + st)
        boundary = self.hdr.get("zero_agent_action_boundary") or self.hdr.get("zero_system_action_boundary")
        self.boundary_text = txt(boundary) if boundary else ""
        if self.boundary_text and not self.zero_boundary:
            notes.append(("", NOTE_TEXT["N-D12"]))
        self.notes = notes
        self.note_for = defaultdict(list)
        for nid, t in notes:
            if nid:
                self.note_for[nid].append(t)

    # ================================================================= SVG pieces
    def note_marker(self, eid, x, y):
        texts = self.note_for.get(eid)
        if not texts:
            return ""
        return ('<g data-note-for="%s"><title>%s</title><circle cx="%.1f" cy="%.1f" r="7" fill="%s" stroke="%s" stroke-width="1"/>'
                '<text x="%.1f" y="%.1f" fill="%s" font-size="9.5" font-weight="700" font-style="italic" text-anchor="middle">i</text></g>'
                % (esc(eid), esc(angle_safe(" ".join(texts))), x, y, WHITE, MUTED, x, y + 3.4, MUTED))

    def ev_chip(self, x_right, y_bottom, ev, anchor="end"):
        if ev is None or ev == "Observed":
            return ""
        bad = ev not in STATES
        label = "needs more information" if ev == "needs_more_information" else str(ev)
        label = cut(label, 28)
        w = 14 + len(label) * 4.7
        x0 = x_right - w if anchor == "end" else x_right
        fill = ROSE if (bad or ev in ("Contradictory", "needs_more_information")) else SUN
        out = ('<rect x="%.1f" y="%.1f" width="%.1f" height="13" rx="6.5" fill="%s" stroke="%s" stroke-width="0.5"/>'
               '<text x="%.1f" y="%.1f" fill="%s" font-size="7.6" font-weight="700" text-anchor="middle">%s</text>'
               % (x0, y_bottom - 14, w, fill, MID, x0 + w / 2, y_bottom - 4.6, CORAL_INK, esc(label)))
        if bad:
            out += self.info_flag(x0 - 10, y_bottom - 7.5)
        return out

    def info_flag(self, x, y):
        return ('<circle cx="%.1f" cy="%.1f" r="7" fill="%s"/>'
                '<text x="%.1f" y="%.1f" fill="%s" font-size="10" font-weight="700" text-anchor="middle">!</text>'
                % (x, y, CORAL, x, y + 3.4, WHITE))

    def op_tag(self, nid):
        n = self.N[nid]
        op = n.get("agent_op")
        if op:
            return OP_TAG.get(str(op), cut(spaced(op).upper(), 22))
        t = self.ntype[nid]
        return TYPE_TAG.get(t, cut(spaced(t).upper(), 22))

    def edge_label(self, px, py, text, ink=MID, per=5.0, fs=9.0):
        w = 12 + self.tw(text, per)
        return ('<rect x="%.1f" y="%.1f" width="%.1f" height="15" rx="4" fill="%s" stroke="%s" stroke-width="0.5" opacity="0.96"/>'
                '<text x="%.1f" y="%.1f" fill="%s" font-size="%.1f" font-weight="600" text-anchor="middle">%s</text>'
                % (px - w / 2, py - 8.5, w, WHITE, MID, px, py + 2.6, ink, fs, esc(text)))

    def gate_label(self, eid, px, py, text, max_w, per=4.9, fs=8.6):
        """A gate branch label that fits the gap between the gate and its target: one line when it fits,
        else wrapped onto two lines, the second cut with an ellipsis. The full label stays in the graph."""
        room = max(24.0, max_w - 12)
        lines = [text]
        if self.tw(text, per) > room:
            lines, cur = [], ""
            for word in text.split():
                trial = (cur + " " + word).strip()
                if cur and self.tw(trial, per) > room:
                    lines.append(cur)
                    cur = word
                else:
                    cur = trial
            lines.append(cur)
            if len(lines) > 2:
                lines = [lines[0], " ".join(lines[1:])]
            lines = [ln if self.tw(ln, per) <= room else ln[:max(1, int(room / per) - 1)].rstrip() + "…" for ln in lines]
        w = 12 + max(self.tw(ln, per) for ln in lines)
        h = 15 + 11 * (len(lines) - 1)
        top = py - h / 2.0
        out = ['<rect data-label-for="%s" x="%.1f" y="%.1f" width="%.1f" height="%.1f" rx="4" fill="%s" stroke="%s" '
               'stroke-width="0.5" opacity="0.96"/>' % (esc(eid), px - w / 2, top, w, h, WHITE, MID)]
        for i, ln in enumerate(lines):
            out.append('<text x="%.1f" y="%.1f" fill="%s" font-size="%.1f" font-weight="600" text-anchor="middle">%s</text>'
                       % (px, top + 10.6 + 11 * i, ACCESS, fs, esc(ln)))
        return "".join(out)

    def bounds(self, nid):
        x = self.cx(nid); y = self.cy(nid); hw, hh = self.dims(nid)
        return (x - hw, y - hh, 2 * hw, 2 * hh)

    def gate_text(self, nid):
        title = txt(self.N[nid].get("title"), nid)
        return wrap_all(title, 17), 11.0, 14.0

    def render_node(self, nid):
        n = self.N[nid]; t = self.ntype[nid]; x = self.cx(nid); y = self.cy(nid)
        bx, by, bw, bh = self.bounds(nid)
        title = txt(n.get("title"), nid)
        ev = n.get("evidence_state")
        g = ['<g class="node" data-id="%s" data-type="%s" data-bounds="%.1f %.1f %.1f %.1f" tabindex="0" role="listitem" aria-label="%s">'
             % (esc(nid), esc(t), bx, by, bw, bh, esc(title))]
        trig = n.get("entry_trigger")
        if isinstance(trig, dict):
            ttext = cut("▶ %s · %s" % (spaced(txt(trig.get("type"), "trigger")), txt(trig.get("label"))), 48)
            tw = 14 + self.tw(ttext, 5.0)
            g.append('<g class="trigger"><rect x="%.1f" y="%.1f" width="%.1f" height="17" rx="8.5" fill="%s" stroke="%s" stroke-width="1"/>'
                     '<text x="%.1f" y="%.1f" fill="%s" font-size="9" font-weight="600">%s</text></g>'
                     % (bx, by - 20, tw, WHITE, CORAL_INK, bx + 7, by - 8.2, CORAL_INK, esc(ttext)))
        if t == "escalation_gate":
            decides = n.get("authoritative") is True
            pts = "%.1f,%.1f %.1f,%.1f %.1f,%.1f %.1f,%.1f" % (x, y - self.gate_hh, x + self.gate_hw, y, x, y + self.gate_hh, x - self.gate_hw, y)
            if decides:
                g.append('<polygon points="%s" fill="%s" stroke="%s" stroke-width="1.8"/>' % (pts, SUN, CORAL_INK))
            else:
                g.append('<polygon points="%s" fill="%s" stroke="%s" stroke-width="1.6" stroke-dasharray="4 2"/>' % (pts, SUN, ACCESS))
            lines, fs, lh = self.gate_text(nid)
            top = y - (len(lines) - 1) * lh / 2.0 + fs * 0.35
            for k, ln in enumerate(lines):
                g.append('<text x="%.1f" y="%.1f" fill="%s" font-size="%.1f" font-weight="600" text-anchor="middle">%s</text>'
                         % (x, top + k * lh, MID, fs, esc(ln)))
            # the caption sits under the left half, so the bottom corner stays free for a link
            g.append('<text x="%.1f" y="%.1f" fill="%s" font-size="8.5" font-weight="700" letter-spacing="0.06em" text-anchor="end">%s</text>'
                     % (x - 16, y + self.gate_hh + 4, CORAL_INK if decides else ACCESS, "GATE · decides" if decides else "GATE · routes"))
            g.append(self.ev_chip(x - 16, y + self.gate_hh + 32, ev))
            g.append(self.note_marker(nid, x + self.gate_hw * 0.5 + 10, y - self.gate_hh * 0.5 - 8))
            g.append("</g>")
            return "".join(g)
        if t == "review_seam":
            geo = self.seam_geo[nid]
            x0 = x - SHW; y0 = y - self.seam_h / 2.0
            g.append('<rect x="%.1f" y="%.1f" width="%d" height="%.1f" rx="8" fill="%s" stroke="%s" stroke-width="1.6" stroke-dasharray="6 3"/>'
                     % (x0, y0, SEAM_W, self.seam_h, CREAM, CORAL))
            for k, ln in enumerate(geo["head"]):
                g.append('<text x="%.1f" y="%.1f" fill="%s" font-size="8.5" font-weight="700" letter-spacing="0.06em">%s</text>'
                         % (x0 + 11, y0 + 16 + k * 11, CORAL_INK, esc(ln)))
            for k, ln in enumerate(geo["lines"]):
                g.append('<text x="%.1f" y="%.1f" fill="%s" font-size="11" font-weight="500" text-anchor="middle">%s</text>'
                         % (x, y0 + geo["first"] + k * 13, MID, esc(ln)))
            g.append(self.ev_chip(x0 + 8, y0 + self.seam_h - 4, ev, anchor="start"))
            g.append(self.note_marker(nid, x0 + SEAM_W, y0))
            g.append("</g>")
            return "".join(g)
        # step box (known `step` or any unfamiliar type). No box prints its raw id: the id is in the box's <title> and
        # data-id, and the page lists it, so a reader sees names only (0478).
        geo = self.step_geo[nid]
        H = self.node_h
        x0 = x - HW; y0 = y - H / 2.0
        ro = self.runs_on(nid)
        known = t in KNOWN_NODE_TYPES
        if ro == "model":
            stripe = CORAL; sw = 2.2
        elif ro == "code" and self.single_flow:
            stripe = MUTED; sw = 1.5
        else:
            stripe = ACCESS if known else MUTED; sw = 1.5
        decides = n.get("authoritative") is True
        g.append('<rect x="%.1f" y="%.1f" width="%d" height="%.1f" rx="9" fill="%s" stroke="%s" stroke-width="%.1f"/>'
                 % (x0, y0, NODE_W, H, WHITE, CORAL_INK if decides else MID, sw))
        g.append('<path d="M%.1f %.1f q0 -8 8 -8 v%.1f q-8 0 -8 -8 z" fill="%s" fill-opacity="0.9"/>'
                 % (x0 + 1.2, y0 + 9, H - 2, stripe))
        g.append('<text x="%.1f" y="%.1f" fill="%s" font-size="8.5" font-weight="700" letter-spacing="0.06em">%s</text>'
                 % (x0 + 16, y0 + 16, stripe if stripe != MUTED else MUTED, esc(self.op_tag(nid))))
        for r, row in enumerate(geo["rows"]):
            ry = y0 + 22 + r * 15
            if r == 0 and decides:
                g.append('<text x="%.1f" y="%.1f" fill="%s" font-size="8.5" font-weight="700" letter-spacing="0.06em">decides</text>'
                         % (x0 + 16, ry + 9.5, CORAL_INK))
            px1 = x0 + NODE_W - 8
            for kind, lab, w in reversed(row):
                if kind == "runs":
                    fill = CORAL if ro == "model" else WHITE
                    ink = WHITE if ro == "model" else MID
                    g.append('<g class="runs-on" data-runs-on="%s"><rect x="%.1f" y="%.1f" width="%.1f" height="13" rx="6.5" fill="%s" stroke="%s" stroke-width="0.6"/>'
                             '<text x="%.1f" y="%.1f" fill="%s" font-size="7.8" font-weight="700" letter-spacing="0.06em" text-anchor="middle">%s</text></g>'
                             % (esc(ro), px1 - w, ry, w, fill, CORAL, px1 - w / 2, ry + 9.2, ink, esc(lab)))
                else:
                    hid = txt(n.get("emits_handoff"))
                    g.append('<g class="handoff-badge" data-interface="%s"><title>%s</title><rect x="%.1f" y="%.1f" width="%.1f" height="13" rx="6.5" fill="%s" stroke="%s" stroke-width="0.6"/>'
                             '<text x="%.1f" y="%.1f" fill="%s" font-size="7.6" font-weight="700" letter-spacing="0.04em" text-anchor="middle">%s</text></g>'
                             % (esc(hid), esc("hands off to a person: " + self.iface_label(hid)), px1 - w, ry, w, ROSE, CORAL,
                                px1 - w / 2, ry + 9.2, CORAL_INK, esc(lab)))
                px1 -= w + 6
        for k, ln in enumerate(geo["lines"]):
            g.append('<text x="%.1f" y="%.1f" fill="%s" font-size="11.5" font-weight="500" text-anchor="middle">%s</text>'
                     % (x, y0 + geo["first"] + k * 14, MID, esc(ln)))
        chip = self.ev_chip(x0 + NODE_W - 8, y0 + H - 4, ev)
        acc = geo["access"]
        if acc:
            line_y = y0 + H - (23 if geo["split"] else 8)
            g.append('<g class="access-line" data-interfaces="%s"><title>%s</title><text x="%.1f" y="%.1f" fill="%s" font-size="8.2" font-weight="600">%s</text></g>'
                     % (esc(" ".join(acc[3])), esc("; ".join(acc[2])), x0 + 16, line_y, acc[1], esc(acc[0])))
        g.append(chip)
        g.append(self.note_marker(nid, x0 + NODE_W, y0))
        g.append("</g>")
        return "".join(g)

    # ---------------------------------------------------------------- rails
    def rail_label(self, iid):
        i = self.I[iid]
        k = txt(i.get("kind"), "interface")
        lbl = txt(i.get("label"), iid)
        if k in KNOWN_IFACE_KINDS:
            return lbl
        return "%s · %s" % (lbl, spaced(k))

    def render_rail(self, iid):
        i = self.I[iid]; y = self.rail_y[iid]
        k = txt(i.get("kind"), "interface")
        ev = i.get("evidence_state")
        needs = ev == "needs_more_information"
        if k == "draft_surface":
            plate = SUN
        elif k == "write":
            plate = ROSE
        elif k == "read":
            plate = SUN if needs else SKY
        else:
            plate = WHITE
        lw = self.plate_w[iid]
        g = ['<g class="node" data-id="%s" data-type="interface:%s" data-bounds="%.1f %.1f %.1f %.1f" data-rail-y="%.1f" tabindex="0" role="listitem" aria-label="%s, interface">'
             % (esc(iid), esc(k), self.rail_x0, y - 11, lw, 22, y, esc(txt(i.get("label"), iid)))]
        g.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="%s" stroke-width="1.1" stroke-opacity="0.4"/>'
                 % (self.rail_x0, y, self.rail_x1, y, MID))
        g.append('<rect x="%.1f" y="%.1f" width="%.1f" height="22" rx="7" fill="%s" stroke="%s" stroke-width="1"/>'
                 % (self.rail_x0, y - 11, lw, plate, MID))
        g.append('<text x="%.1f" y="%.1f" fill="%s" font-size="10.5" font-weight="600">%s</text>'
                 % (self.rail_x0 + 11, y + 4, MID, esc(self.rail_label(iid))))
        right = self.rail_right_text(iid)
        if right:
            g.append('<text x="%.1f" y="%.1f" fill="%s" font-size="9.2" font-weight="600" text-anchor="end">%s</text>'
                     % (self.rail_x1 - 2, y + 3.6, CORAL_INK, right))
        if ev is not None and ev not in STATES:
            g.append('<text x="%.1f" y="%.1f" fill="%s" font-size="9.2" font-weight="600" text-anchor="end">%s</text>'
                     % (self.rail_x1 - 2, y - 5, CORAL_INK, esc(cut(ev, 40))))
            g.append(self.info_flag(self.rail_x0 + lw + 8, y))
        g.append(self.note_marker(iid, self.rail_x0 + lw + 22, y))
        g.append("</g>")
        return "".join(g)

    # ---------------------------------------------------------------- edges
    @staticmethod
    def d_of(pts):
        return "M" + " L".join("%.1f %.1f" % p for p in pts)

    @staticmethod
    def pts_attr(pts):
        return " ".join("%.1f,%.1f" % p for p in pts)

    def edge_open(self, x, pts):
        # Keep authored relationship meaning and provenance available even when the
        # visible caption uses a display override or an interface's readable name.
        e = x["e"]
        detail = " · ".join(str(e[k]) for k in ("display_label", "label", "condition", "source") if e.get(k))
        state = e.get("evidence_state")
        if isinstance(state, str) and state.strip():
            detail = (detail or spaced(x["kind"])) + " · Evidence: " + state
        return ('<g class="edge" data-edge-id="%s" data-kind="%s" data-from="%s" data-to="%s" data-route="%s" data-points="%s" tabindex="0" role="group" aria-label="%s"><title>%s</title>'
                % (esc(x["id"]), esc(x["kind"]), esc(x["from"]), esc(x["to"]), x["route"], self.pts_attr(pts),
                   esc(detail or spaced(x["kind"])), esc(detail or spaced(x["kind"]))))

    def path(self, pts, col, sw, marker, dash=None):
        return ('<path d="%s" fill="none" stroke="%s" stroke-width="%s"%s marker-end="url(#%s)"/>'
                % (self.d_of(pts), col, sw, (' stroke-dasharray="%s"' % dash) if dash else "", marker))

    def label_of(self, x, default=None):
        e = x["e"]
        if e.get("display_label"):
            return txt(e.get("display_label"))
        if x["route"] in ("reentry", "inbound") and e.get("label"):
            return txt(e.get("label"))
        return default

    def render_edge(self, x):
        r = x["route"]
        fn = getattr(self, "draw_" + r)
        return fn(x)

    def mid_marker(self, x, pts):
        k = len(pts) // 2
        if len(pts) % 2 == 0:
            px = (pts[k - 1][0] + pts[k][0]) / 2.0; py = (pts[k - 1][1] + pts[k][1]) / 2.0
        else:
            px, py = pts[k]
        return self.note_marker(x["id"], px, py - 12)

    # ---- what each link says (full text; wrapped, never cut)
    def label_spec(self, x):
        """(text, ink) for the link's label, or None. Interface ids never become the visible text."""
        r, kind, e = x["route"], x["kind"], x["e"]
        if r == "step":
            if kind in ("then", "else"):
                lbl = self.label_of(x) or (txt(e.get("label")) if e.get("label") else ("yes" if kind == "then" else "no"))
                return lbl, ACCESS
            if kind == "handoff":
                iid = txt(e.get("interface"))
                return (self.label_of(x) or (self.iface_label(iid) if iid else "handoff")), CORAL_INK
            if kind == "dispatch":
                return self.label_of(x) or "dispatch", MID
            if kind == "write":
                return self.label_of(x) or "writes", MID
            if kind not in KNOWN_EDGE_KINDS:
                return self.label_of(x) or spaced(kind), MID
            lbl = self.label_of(x)
            return (lbl, MID) if lbl else None
        if r in ("return", "reentry"):
            lbl = self.label_of(x) or ("re-entry" if r == "reentry" else (spaced(kind) if kind not in KNOWN_EDGE_KINDS else "return"))
            return lbl, ACCESS
        if r == "inbound":
            return self.label_of(x) or "analyst → agent inbound", CORAL_INK
        if r == "drop":
            if x["to"] not in self.rail_y:
                return self.label_of(x) or spaced(kind), MID
            if kind == "read_access":
                lbl = self.label_of(x)
                return (lbl, MID) if lbl else None
            if kind == "write":
                return self.label_of(x) or "writes", CORAL_INK
            return self.label_of(x) or spaced(kind), MID
        if r == "rise":
            return self.label_of(x) or spaced(kind), MID
        if r == "toroot":
            return self.label_of(x) or spaced(kind), MID
        if r == "bus":
            if x["to"] in self.callouts:
                return None
            line = self.bus_text(x)
            rs = x["e"].get("reads_state_from")
            if rs:
                line = (line + " " if line else "") + "(reads state at %s)" % rs
            return (line, CORAL_INK) if line else None
        return None

    # ---- ports: which side of which box each link leaves and enters, spread along the side
    def box_of(self, ref):
        """(x0, y0, x1, y1) of a node, a root plate or a control chip."""
        if ref in self.N:
            bx, by, bw, bh = self.bounds(ref) if ref not in self.root_pos else (
                self.root_pos[ref][0], self.root_pos[ref][1], PLATE_W, PLATE_H)
            return (bx, by, bx + bw, by + bh)
        for cid, cy0, h, _ in self.chip_geo:
            if cid == ref:
                y0 = self.B["#root"]["top"] + cy0
                return (self.chip_x, y0, self.chip_x + CHIP_W, y0 + h)
        return None

    def center_of(self, ref):
        b = self.box_of(ref)
        if b is None:
            return (self.rail_x0, self.rail_y.get(ref, self.bands_bottom))
        return ((b[0] + b[2]) / 2.0, (b[1] + b[3]) / 2.0)

    def side_pair(self, a, b):
        A, B = self.box_of(a), self.box_of(b)
        if A[2] + 10 < B[0]:
            return "r", "l"
        if B[2] + 10 < A[0]:
            return "l", "r"
        if (A[1] + A[3]) <= (B[1] + B[3]):
            return "b", "t"
        return "t", "b"

    def plan_links(self):
        """Every routed link as (edge id, source end, target end); an end is (ref, side) on a box, or a fixed point."""
        self.links = []
        for x in self.E:
            r = x["route"]; a, b = x["from"], x["to"]
            if r in ("step",):
                sa, sb = self.side_pair(a, b)
                self.links.append([x["id"], [a, sa], [b, sb]])
            elif r in ("return", "reentry"):
                ka, ca = self.slot[a]; kb, cb = self.slot[b]
                side = "t" if (self.band_of[a] == self.band_of[b] and ka == 0 and kb == 0) else "b"
                self.links.append([x["id"], [a, side], [b, side]])
            elif r == "inbound":
                sa, _ = self.side_pair(a, b)
                self.links.append([x["id"], [a, sa], [b, "b", "center"]])
            elif r == "toroot":
                self.links.append([x["id"], [a, "t"], [b, "b"]])
            elif r == "bus":
                self.links.append([x["id"], None, [b, "l"]])
            elif r == "drop":
                if b in self.rail_y:
                    self.links.append([x["id"], [a, "b"], ["rail", b]])
                else:
                    self.links.append([x["id"], [a, "b"], [b, "b"]])
            elif r == "rise":
                if a in self.rail_y:
                    self.links.append([x["id"], ["rail", a], [b, "b"]])
                else:
                    self.links.append([x["id"], [a, "b"], [b, "t"]])
        for t, (lx, ly, lw, lh, lines, host) in sorted(self.callouts.items(), key=lambda kv: self.nidx[kv[0]]):
            self.links.append([("callout", t), None, [t, "t"]])   # a tuple: never equal to an authored edge id
        # a gate is a diamond: links meet it only at its four corners; forward links fan out over them
        for nid in self.act:
            if self.ntype[nid] != "escalation_gate":
                continue
            outs = [L for L in self.links if L[1] and L[1][0] == nid and L[1][1] == "r"]
            outs.sort(key=lambda L: (self.center_of(L[2][1] if L[2][0] == "rail" else L[2][0])[1], self.Eid[L[0]]["id"] if L[0] in self.Eid else L[0]))
            order = {1: ["r"], 2: ["t", "b"], 3: ["t", "r", "b"]}.get(len(outs))
            if order is None:
                order = ["t", "r"] + ["b"] * (len(outs) - 2)
            for L, s in zip(outs, order):
                L[1][1] = s
        # spread the ends that share a side of a box
        groups = defaultdict(list)
        for k, L in enumerate(self.links):
            for which in (1, 2):
                end = L[which]
                if end is None or end[0] == "rail":
                    continue
                other = L[3 - which]
                if other is None:
                    oc = (LANE_LEFT, self.center_of(end[0])[1])
                elif other[0] == "rail":
                    oc = (self.rail_x0, self.rail_y[other[1]])
                else:
                    oc = self.center_of(other[0])
                groups[(end[0], end[1])].append((k, which, oc, len(end) > 2))
        self.port_pt = {}
        for (ref, side), members in groups.items():
            box = self.box_of(ref)
            x0, y0, x1, y1 = box
            if ref in self.N and self.ntype.get(ref) == "escalation_gate":
                cx_, cy_ = (x0 + x1) / 2.0, (y0 + y1) / 2.0
                v = {"l": (cx_ - self.gate_hw, cy_), "r": (cx_ + self.gate_hw, cy_), "t": (cx_, cy_ - self.gate_hh), "b": (cx_, cy_ + self.gate_hh)}[side]
                for k, which, oc, fixed in members:
                    self.port_pt[(k, which)] = v
                continue
            horiz = side in ("t", "b")
            lo, hi = (x0, x1) if horiz else (y0, y1)
            fixed = [m for m in members if m[3]]
            free = [m for m in members if not m[3]]
            free.sort(key=lambda m: (m[2][0] if horiz else m[2][1], m[0], m[1]))
            if ref in self.root_pos and side == "b":
                lo = lo + 48          # the dispatch bus leaves the plate's left end
            mid = (lo + hi) / 2.0
            n = len(free)
            slots = n + (1 if fixed else 0)
            if fixed and slots % 2 == 0:
                slots += 1
            span = (hi - lo) - 32
            step = min(22.0, span / max(1, slots - 1)) if slots > 1 else 0
            pos = [mid + (j - (slots - 1) / 2.0) * step for j in range(slots)]
            if fixed:
                pos.remove(pos[(slots - 1) // 2])
                pos = pos[:n]
            for (k, which, oc, fx), p in zip(free, pos):
                self.port_pt[(k, which)] = {"t": (p, y0), "b": (p, y1), "l": (x0, p), "r": (x1, p)}[side]
            for (k, which, oc, fx) in fixed:
                self.port_pt[(k, which)] = {"t": (mid, y0), "b": (mid, y1), "l": (x0, mid), "r": (x1, mid)}[side]
        # rail landings: right of every plate down to the target rail, one x per landing
        used = []
        self.landing = {}
        for k, L in enumerate(self.links):
            for which in (1, 2):
                end = L[which]
                if end is None or end[0] != "rail":
                    continue
                rid = end[1]
                other = self.port_pt.get((k, 3 - which))
                ox = other[0] if other else self.rail_x0
                reach = max(self.rail_x0 + self.plate_w[r] for r in self.rails if self.rail_y[r] <= self.rail_y[rid])
                lx = max(ox, reach + 18)
                while any(abs(lx - u) < 9 for u in used):
                    lx += 10
                used.append(lx)
                self.landing[(k, which)] = (lx, self.rail_y[rid])

    # ---- routing
    def obstacles(self):
        obs = []
        for nid in self.act:
            x0, y0, x1, y1 = self.box_of(nid)
            if self.ntype[nid] == "escalation_gate":
                cx_ = (x0 + x1) / 2.0
                obs.append((cx_ - 16 - 140, y1 - 6, cx_ - 16, y1 + 33))   # caption under the left half
            if isinstance(self.N[nid].get("entry_trigger"), dict):
                y0 -= 21
            obs.append((x0, y0, x1, y1))
        for rid in self.roots:
            obs.append(self.box_of(rid))
            px, py = self.root_pos[rid]
            if any(x["route"] == "bus" and x["from"] == rid for x in self.E):
                obs.append((px + 28, py + PLATE_H + 13, px + 80, py + PLATE_H + 25))
        for cid, cy0, h, _ in self.chip_geo:
            obs.append(self.box_of(cid))
        for t, (lx, ly, lw, lh, lines, host) in self.callouts.items():
            obs.append((lx, ly, lx + lw, ly + lh))
        obs.extend(self.text_obstacles)
        for iid in self.rails:
            y = self.rail_y[iid]
            obs.append((self.rail_x0, y - 11, self.rail_x0 + self.plate_w[iid], y + 11))
        return obs

    def route_all(self):
        obs = self.obstacles()
        self.route_obstacles = obs
        x_lo = LANE_LEFT + SPINE_W + 4; x_hi = self.vb_w - 30
        y_lo = BAND_TOP + 4; y_hi = self.frame_bottom - 4
        xs = [x_lo + k * GRID for k in range(int((x_hi - x_lo) / GRID) + 1)]
        ys = [y_lo + k * GRID for k in range(int((y_hi - y_lo) / GRID) + 1)]
        OUT = {"r": (1, 0, 0), "l": (-1, 0, 2), "b": (0, 1, 1), "t": (0, -1, 3)}
        ends = {}
        for k, L in enumerate(self.links):
            for which in (1, 2):
                end = L[which]
                if end is None:
                    continue
                if end[0] == "rail":
                    p = self.landing[(k, which)]
                    s = (p[0], p[1] - STUB)
                    ends[(k, which)] = (p, s, 3 if which == 1 else 1)
                    continue
                p = self.port_pt[(k, which)]
                ox, oy, d = OUT[end[1]]
                s = (round(p[0] + ox * STUB, 1), round(p[1] + oy * STUB, 1))
                ends[(k, which)] = (p, s, d if which == 1 else (d + 2) % 4)
        self.bus_start = {}
        for x in self.E:
            if x["route"] == "bus":
                px, py = self.root_pos[x["from"]]
                self.bus_start[x["id"]] = [(px + 24, py + PLATE_H), (px + 24, py + PLATE_H + 10),
                                           (self.bus[x["to"]], py + PLATE_H + 10)]
        for v in ends.values():
            xs.append(v[1][0]); ys.append(v[1][1])
        for pts in self.bus_start.values():
            xs.append(pts[2][0]); ys.append(pts[2][1])
        for t, (lx, ly, lw, lh, lines, host) in self.callouts.items():
            xs.append(lx + 24); ys.append(ly + lh + STUB)
        R = Router(obs, xs, ys, LANE_LEFT + SPINE_W)
        self.rpts = {}
        self.route_failures = []
        # route in a fixed order: links between steps first, then handoffs and returns, then the long runs
        rank = {"step": 0, "return": 2, "reentry": 2, "inbound": 3, "toroot": 4, "bus": 5, "drop": 6, "rise": 6}
        def lrank(k):
            eid = self.links[k][0]
            if isinstance(eid, tuple):
                return (5, k)
            x = self.Eid[eid]
            return (rank.get(x["route"], 7) + (1 if x["kind"] == "handoff" else 0), k)
        for k in sorted(range(len(self.links)), key=lrank):
            eid, src, dst = self.links[k]
            pe, se, de = ends[(k, 2)]
            spine_x = None
            if src is None:
                if isinstance(eid, tuple):
                    t = eid[1]
                    lx, ly, lw, lh, lines, host = self.callouts[t]
                    head = [(lx + 24, ly + lh)]
                    s, sd = (lx + 24, ly + lh + STUB), 1
                else:
                    head = self.bus_start[eid]
                    s, sd = head[-1], 1
                    spine_x = head[-1][0]
            else:
                ps, s, sd = ends[(k, 1)]
                head = [ps]
            mid = None
            if R.has(s) and R.has(se):
                mid = R.route(s, sd, se, de, spine_x=spine_x)
            if mid is None:
                self.route_failures.append(eid)
                if abs(s[0] - se[0]) < 0.05 or abs(s[1] - se[1]) < 0.05:
                    mid = [s, se]
                else:
                    mid = [s, (s[0], se[1]), se] if sd in (1, 3) else [s, (se[0], s[1]), se]
            self.rpts[eid] = simplify(head + [s] + mid + [se, pe])

    # ---- labels: each in a free slot on its own line, clear of every box, text and other line
    def label_box(self, text, chars):
        lines = wrap_all(text, chars)
        w = 12 + max(self.tw(ln, LABEL_PER) for ln in lines)
        h = 15 + LABEL_LH * (len(lines) - 1)
        return lines, w, h

    def place_labels(self):
        self.labels = {}
        self.label_misses = []
        self.label_leaders = {}
        self.label_level = {}
        hard = [(a - 2, b - 2, c + 2, d + 2) for (a, b, c, d) in self.route_obstacles]
        for iid in self.rails:
            hard.append((self.rail_x0, self.rail_y[iid] - 0.6, self.rail_x1, self.rail_y[iid] + 0.6))
        segs = {}
        for eid, pts in self.rpts.items():
            segs[eid] = [(min(p[0], q[0]) - 1, min(p[1], q[1]) - 1, max(p[0], q[0]) + 1, max(p[1], q[1]) + 1)
                         for p, q in zip(pts, pts[1:])]
        frame = (LANE_LEFT + SPINE_W + 2, BAND_TOP + 2, self.vb_w - 26, self.frame_bottom - 2)
        placed = []
        labelled = {x["id"] for x in self.E if x["id"] in self.rpts and self.label_spec(x)}
        for x in self.E:
            spec = self.label_spec(x) if x["id"] in self.rpts else None
            if not spec:
                continue
            text, ink = spec
            pts = self.rpts[x["id"]]
            seglist = []
            n = len(pts) - 1
            for j, (p, q) in enumerate(zip(pts, pts[1:])):
                ln = abs(p[0] - q[0]) + abs(p[1] - q[1])
                lo = 6 if j > 0 else STUB
                hi = ln - (STUB + 6 if j == n - 1 else 6)
                if hi - lo < 4:
                    continue
                seglist.append((j, p, q, ln, lo, hi))
            if x["kind"] in ("then", "else"):
                seglist.sort(key=lambda s: s[0])
            elif x["route"] in ("drop", "bus"):
                seglist.sort(key=lambda s: -s[0])
            else:
                seglist.sort(key=lambda s: (-s[3], s[0]))
            others = [r for e2, rs in segs.items() if e2 != x["id"] for r in rs]
            # labelled links paint last, so a label may cover an unlabelled line but never another labelled one
            others_labelled = [r for e2, rs in segs.items() if e2 != x["id"] and e2 in labelled for r in rs]
            best = None
            for level in (0, 1, 2, 3, 4, 5):
                beside = level in (1, 3, 5)
                avoid = others if level < 2 else (others_labelled if level < 4 else ())
                for chars in (30, 22, 16, 11, 40):
                    lines, w, h = self.label_box(text, chars)
                    for j, p, q, ln, lo, hi in seglist:
                        horiz = abs(p[1] - q[1]) < 0.05
                        for f in FRACS:
                            t_ = lo + (hi - lo) * f
                            if horiz:
                                sgn = 1 if q[0] >= p[0] else -1
                                cx_, cy_ = p[0] + sgn * t_, p[1]
                            else:
                                sgn = 1 if q[1] >= p[1] else -1
                                cx_, cy_ = p[0], p[1] + sgn * t_
                            for off in ((-1, 1, -2, 2, -3, 3, -4, 4) if beside else (0,)):
                                if horiz:
                                    c2 = (cx_, cy_ + off * (h / 2.0 + 2))
                                else:
                                    c2 = (cx_ + off * (w / 2.0 + 2), cy_)
                                r = (c2[0] - w / 2.0, c2[1] - h / 2.0, c2[0] + w / 2.0, c2[1] + h / 2.0)
                                if self.label_free(r, frame, hard, placed, avoid):
                                    best = (lines, w, h, c2[0], c2[1])
                                    if abs(off) > 1:
                                        # A displaced caption keeps a visible tether to its own
                                        # connector, so nearby parallel routes cannot steal its meaning.
                                        end = (cx_, c2[1] - (h / 2 if off > 0 else -h / 2)) if horiz else (c2[0] - (w / 2 if off > 0 else -w / 2), cy_)
                                        self.label_leaders[x["id"]] = ((cx_, cy_), end)
                                    break
                            if best:
                                break
                        if best:
                            break
                    if best:
                        break
                if best:
                    break
                if x["route"] == "bus":
                    # a start label may also sit just above its step's top-left corner
                    bx0, by0, bx1, by1 = self.box_of(x["to"])
                    for chars in (30, 22, 16):
                        lines, w, h = self.label_box(text, chars)
                        r = (bx0, by0 - 4 - h, bx0 + w, by0 - 4)
                        if self.label_free(r, frame, hard, placed, avoid):
                            best = (lines, w, h, bx0 + w / 2.0, by0 - 4 - h / 2.0)
                            break
                    if best:
                        break
            self.label_level[x["id"]] = level if best else 9
            if best is None:
                self.label_misses.append(x["id"])
                lines, w, h = self.label_box(text, 22)
                p, q = pts[len(pts) // 2 - 1], pts[len(pts) // 2]
                best = (lines, w, h, (p[0] + q[0]) / 2.0, (p[1] + q[1]) / 2.0)
            lines, w, h, cx_, cy_ = best
            placed.append((cx_ - w / 2.0, cy_ - h / 2.0, cx_ + w / 2.0, cy_ + h / 2.0))
            self.labels[x["id"]] = (lines, w, h, cx_, cy_, ink)

    @staticmethod
    def label_free(r, frame, hard, placed, others):
        if r[0] < frame[0] or r[1] < frame[1] or r[2] > frame[2] or r[3] > frame[3]:
            return False
        if any(rects_hit(r, o) for o in hard):
            return False
        if any(rects_hit(r, o, 3) for o in placed):
            return False
        if any(rects_hit(r, o) for o in others):
            return False
        return True

    def label_svg(self, eid):
        if eid not in self.labels:
            return ""
        lines, w, h, cx_, cy_, ink = self.labels[eid]
        top = cy_ - h / 2.0
        out = ['<rect data-label-for="%s" x="%.1f" y="%.1f" width="%.1f" height="%.1f" rx="4" fill="%s" stroke="%s" '
               'stroke-width="0.5" opacity="0.97"/>' % (esc(eid), cx_ - w / 2.0, top, w, h, WHITE, MID)]
        for i, ln in enumerate(lines):
            out.append('<text x="%.1f" y="%.1f" fill="%s" font-size="%.1f" font-weight="600" text-anchor="middle">%s</text>'
                       % (cx_, top + 10.8 + LABEL_LH * i, ink, LABEL_FS, esc(ln)))
        if eid in self.label_leaders:
            a, b = self.label_leaders[eid]
            out.insert(0, '<path data-caption-leader="%s" d="M %.1f %.1f L %.1f %.1f" fill="none" stroke="%s" stroke-width="0.7"/>'
                       % (esc(eid), a[0], a[1], b[0], b[1], ink))
        return "".join(out)

    def label_marker(self, x):
        if x["id"] in self.labels:
            lines, w, h, cx_, cy_, ink = self.labels[x["id"]]
            return self.note_marker(x["id"], cx_ + w / 2.0 + 9, cy_ - h / 2.0)
        return self.mid_marker(x, self.rpts[x["id"]])

    # ---- drawing
    def draw_step(self, x):
        kind = x["kind"]; e = x["e"]
        pts = self.rpts[x["id"]]
        out = [self.edge_open(x, pts)]
        if kind in ("then", "else"):
            out.append(self.path(pts, MID, "1.5", "arw"))
        elif kind == "handoff":
            out.append(self.path(pts, CORAL, "2.4", "arw-coral"))
        elif kind == "sequence":
            out.append(self.path(pts, MID, "1.5", "arw"))
        elif kind == "dispatch":
            out.append(self.path(pts, CORAL, "1.4", "arw-coral", "3 3"))
        elif kind == "write":
            out.append(self.path(pts, CORAL_INK, "1.5", "arw-ink"))
        elif kind == "read_access":
            out.append(self.path(pts, ACCESS, "1.15", "arw-access", "5 3"))
        else:
            out.append(self.path(pts, MID, "1.3", "arw", "3 3"))
        iid = txt(e.get("interface"))
        if kind == "handoff" and iid and self.handoff_chip_edge.get(iid) == x["id"] and x["id"] in self.labels:
            lines, w, h, cx_, cy_, ink = self.labels[x["id"]]
            out.append('<g class="node iface-chip" data-id="%s" data-type="interface:handoff" tabindex="0" role="listitem" aria-label="%s, handoff interface">%s%s</g>'
                       % (esc(iid), esc(self.iface_label(iid)), self.label_svg(x["id"]),
                          self.note_marker(iid, cx_ + w / 2.0 + 9, cy_ + h / 2.0)))
        else:
            out.append(self.label_svg(x["id"]))
        out.append(self.label_marker(x))
        out.append("</g>")
        return "".join(out)

    def draw_return(self, x, explicit=False):
        pts = self.rpts[x["id"]]
        out = [self.edge_open(x, pts), self.path(pts, ACCESS, "1.5", "arw-access", "6 4"),
               self.label_svg(x["id"]), self.label_marker(x), "</g>"]
        return "".join(out)

    def draw_reentry(self, x):
        return self.draw_return(x, explicit=True)

    def draw_inbound(self, x):
        pts = self.rpts[x["id"]]
        out = [self.edge_open(x, pts), self.path(pts, CORAL, "1.8", "arw-coral", "6 4"),
               self.label_svg(x["id"]), self.label_marker(x), "</g>"]
        return "".join(out)

    def draw_drop(self, x):
        pts = self.rpts[x["id"]]
        kind = x["kind"]
        out = [self.edge_open(x, pts)]
        if x["to"] in self.rail_y and kind == "read_access":
            dash = "2 4" if x["e"].get("evidence_state") == "needs_more_information" else "5 3"
            out.append(self.path(pts, ACCESS, "1.15", "arw-access", dash))
        elif x["to"] in self.rail_y and kind == "write":
            out.append(self.path(pts, CORAL_INK, "1.5", "arw-ink"))
        else:
            out.append(self.path(pts, MID, "1.1", "arw", "2 3"))
        out.append(self.label_svg(x["id"]))
        out.append(self.label_marker(x))
        out.append("</g>")
        return "".join(out)

    def draw_rise(self, x):
        pts = self.rpts[x["id"]]
        out = [self.edge_open(x, pts), self.path(pts, MID, "1.1", "arw", "2 3"),
               self.label_svg(x["id"]), self.label_marker(x), "</g>"]
        return "".join(out)

    def draw_rail(self, x):
        a, b = x["from"], x["to"]
        ya = self.rail_y.get(a, self.bands_bottom); yb = self.rail_y.get(b, self.bands_bottom)
        xx = self.rail_x0 + max(self.plate_w.get(a, 120), self.plate_w.get(b, 120)) + 6
        pts = [(xx, ya), (xx, yb)]
        out = [self.edge_open(x, pts), self.path(pts, MID, "1.1", "arw", "2 3"), self.mid_marker(x, pts), "</g>"]
        return "".join(out)

    def draw_toroot(self, x):
        pts = self.rpts[x["id"]]
        out = [self.edge_open(x, pts), self.path(pts, MID, "1.1", "arw", "3 3"),
               self.label_svg(x["id"]), "</g>"]
        return "".join(out)

    def draw_rootlink(self, x):
        a, b = x["from"], x["to"]
        sx, sy = self.port(a, "b"); tx, ty = self.port(b, "b")
        pts = [(sx, sy), (sx, sy + 8), (tx, sy + 8), (tx, ty)]
        out = [self.edge_open(x, pts), self.path(pts, MID, "1.1", "arw", "3 3"), "</g>"]
        return "".join(out)

    def bus_text(self, x):
        e = x["e"]
        parts = []
        if e.get("trigger_type"):
            parts.append(spaced(txt(e.get("trigger_type"))))
        if e.get("routing_guard"):
            parts.append(txt(e.get("routing_guard")))
        if self.label_of(x):
            parts = [self.label_of(x)]
        if not parts and x["kind"] != "dispatch":
            parts.append(spaced(x["kind"]))
        return " · ".join(parts)

    def bus_lines(self, x, width=60):
        t = self.bus_text(x)
        lines = wrap_all(t, width) if t else []
        rs = x["e"].get("reads_state_from")
        if rs:
            lines.append("(reads state at %s)" % rs)
        return lines

    def plan_buses(self):
        self.bus = {}
        targets = []
        by_target = defaultdict(list)
        for x in self.E:
            if x["route"] == "bus":
                by_target[x["to"]].append(x)
                if x["to"] not in targets:
                    targets.append(x["to"])
        def tkey(t):
            bk = self.band_of.get(t)
            return (self.band_index.get(bk, 99), self.nidx[t])
        targets.sort(key=tkey)
        n = len(targets)
        self.callouts = {}
        for k, t in enumerate(targets):
            busx = LANE_LEFT + 6 + k * (SPINE_W - 12) / float(max(1, n - 1))
            self.bus[t] = round(busx, 1)
            es = by_target[t]
            if len(es) > 1:
                bk = self.band_of[t]
                bidx = self.band_index[bk]
                host = bk
                if bidx > 0 and self.bands[bidx - 1]["kind"] not in ("root",):
                    host = self.bands[bidx - 1]["key"]
                elif bidx + 1 < len(self.bands):
                    host = self.bands[bidx + 1]["key"]
                lines = []
                band = self.B.get(bk, {})
                name = txt(self.SA.get(band.get("sid"), {}).get("label"), band.get("label", "")) if band.get("sid") else band.get("label", "")
                head = ("Routed to %s when:" % name) if self.coord_kind == "state-driven-dispatch" else ("Starts %s:" % name)
                lines.append((head, None))
                for x in es:
                    for j, ln in enumerate(self.bus_lines(x, 64)):
                        lines.append(((u"•  " if j == 0 else "    ") + ln, x["id"]))
                lw = max(self.tw(t_, 4.7) for t_, _ in lines) + 20
                lh = 16 + (len(lines) - 1) * 12 + 8
                lx = self.cx(t) + HW + 60
                hb = self.B[host]
                ly = hb["top"] + max(20, (hb["h"] - lh) / 2.0)
                self.callouts[t] = (lx, ly, lw, lh, lines, host)

    def draw_bus(self, x):
        t = x["to"]
        pts = self.rpts[x["id"]]
        out = [self.edge_open(x, pts)]
        if x["kind"] == "dispatch":
            out.append(self.path(pts, CORAL, "1.4", "arw-coral", "3 3"))
        else:
            out.append(self.path(pts, MID, "1.3", "arw", "3 3"))
        first = [y for y in self.E if y["route"] == "bus" and y["to"] == t][0]["id"] == x["id"]
        if t in self.callouts:
            lx, ly, lw, lh, lines, host = self.callouts[t]
            if first:
                out.append('<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" rx="6" fill="%s" stroke="%s" stroke-width="1" stroke-dasharray="3 2"/>'
                           % (lx, ly, lw, lh, WHITE, CORAL))
                out.append('<text x="%.1f" y="%.1f" fill="%s" font-size="8.2" font-weight="700">%s</text>'
                           % (lx + 8, ly + 13, CORAL_INK, esc(lines[0][0])))
                lead = self.rpts.get(("callout", t))
                if lead:
                    out.append('<path class="callout-lead" data-points="%s" d="%s" fill="none" stroke="%s" stroke-width="1" stroke-dasharray="2 3" marker-end="url(#arw-coral)"/>'
                               % (self.pts_attr(lead), self.d_of(lead), CORAL))
            for k, (t_, owner) in enumerate(lines[1:]):
                if owner == x["id"]:
                    out.append('<text x="%.1f" y="%.1f" fill="%s" font-size="8" font-weight="500">%s</text>'
                               % (lx + 8, ly + 25 + k * 12, MUTED, esc(t_)))
        else:
            out.append(self.label_svg(x["id"]))
        out.append(self.label_marker(x))
        out.append("</g>")
        return "".join(out)

    def render_roots(self):
        out = []
        b = self.B["#root"]
        for rid in self.roots:
            n = self.N[rid]
            px, py = self.root_pos[rid]
            title = "ROOT AGENT · DISPATCHER" if self.coord_kind == "state-driven-dispatch" else "ROOT AGENT"
            if self.coord_kind == "independent-triggers":
                lines = ["Independent triggers · no routing between sub-agents"]
            else:
                lines = [cut(txt(self.hdr.get("router_type"), "not stated"), 60)]
                if self.coord_kind:
                    lines.append(self.coord_kind.replace("-", " "))
            out.append('<g class="node" data-id="%s" data-type="root_agent" data-bounds="%.1f %.1f %d %d" tabindex="0" role="listitem" aria-label="%s, root agent">'
                       % (esc(rid), px, py, PLATE_W, PLATE_H, esc(txt(n.get("title"), rid))))
            out.append('<rect x="%.1f" y="%.1f" width="%d" height="%d" rx="10" fill="%s"%s/>'
                       % (px, py, PLATE_W, PLATE_H, MID, (' stroke="%s" stroke-width="2"' % CORAL) if n.get("authoritative") is True else ""))
            out.append('<text x="%.1f" y="%.1f" fill="%s" font-size="10.5" font-weight="700" letter-spacing="0.06em">%s</text>'
                       % (px + 14, py + (17 if len(lines) > 1 else 19), WHITE, title))
            for k, ln in enumerate(lines):
                out.append('<text x="%.1f" y="%.1f" fill="%s" font-size="8">%s</text>'
                           % (px + 14, py + (31 + k * 11 if len(lines) > 1 else 34), SKY, esc(ln)))
            out.append(self.note_marker(rid, px + PLATE_W, py))
            out.append("</g>")
            buses = [x for x in self.E if x["route"] == "bus" and x["from"] == rid]
            if buses:
                cap = "dispatch" if self.coord_kind == "state-driven-dispatch" else (
                    "triggers" if self.coord_kind == "independent-triggers" else "starts")
                out.append('<text x="%.1f" y="%.1f" fill="%s" font-size="8.4" font-weight="700" letter-spacing="0.04em">%s</text>'
                           % (px + 24 + 6, py + PLATE_H + 22, CORAL_INK, cap))
        for iid, cy0, h, lines in self.chip_geo:
            i = self.I[iid]
            y0 = b["top"] + cy0
            out.append('<g class="node" data-id="%s" data-type="interface:control" data-bounds="%.1f %.1f %d %.1f" tabindex="0" role="listitem" aria-label="%s, inbound control interface">'
                       % (esc(iid), self.chip_x, y0, CHIP_W, h, esc(txt(i.get("label"), iid))))
            out.append('<rect x="%.1f" y="%.1f" width="%d" height="%.1f" rx="10" fill="%s" stroke="%s" stroke-width="1.4" stroke-dasharray="5 3"/>'
                       % (self.chip_x, y0, CHIP_W, h, CREAM, CORAL))
            out.append('<text x="%.1f" y="%.1f" fill="%s" font-size="9.6" font-weight="700">Inbound control</text>'
                       % (self.chip_x + 14, y0 + 17, CORAL_INK))
            for k, ln in enumerate(lines):
                out.append('<text x="%.1f" y="%.1f" fill="%s" font-size="8.4">%s</text>'
                           % (self.chip_x + 14, y0 + 31 + k * 11, MUTED, esc(ln)))
            out.append(self.note_marker(iid, self.chip_x + CHIP_W, y0))
            out.append("</g>")
        return "".join(out)

    # ---------------------------------------------------------------- strip
    def strip_text(self):
        if self.zero_boundary:
            t = "The agent writes nowhere and holds no decision in this design"
            if self.boundary_text:
                t += ". " + first_sentence(self.boundary_text, 160)
            return t, []
        tips = []
        targets = []
        for x in self.write_edges:
            lbl = txt(self.I[x["to"]].get("label"), x["to"]) if x["to"] in self.I else txt(self.N[x["to"]].get("title"), x["to"])
            if lbl not in targets:
                targets.append(lbl)
            tips.append("%s: %s" % (x["id"], txt(x["e"].get("authority_grant"), "no grant stated")))
        for iid in self.write_ifaces:
            lbl = txt(self.I[iid].get("label"), iid)
            if lbl not in targets:
                targets.append(lbl)
            tips.append("%s: %s" % (iid, txt(self.I[iid].get("authority_grant"), "no grant stated")))
        dec = []
        for nid in self.decisions:
            dec.append(txt(self.N[nid].get("title"), nid))
            tips.append("%s: %s" % (nid, txt(self.N[nid].get("authority_grant"), "no grant stated")))
        parts = []
        if targets:
            parts.append("The agent writes to: " + ", ".join(targets))
        if dec:
            parts.append(("decides at: " if parts else "The agent decides at: ") + ", ".join(dec))
        return "; ".join(parts), tips

    # ---------------------------------------------------------------- assemble
    def viewer_html(self):
        """The shared CSS-only viewer around the diagram, linking the standalone SVG written next to the page."""
        svg = self.svg_with_titles()
        return viewer(svg, svg_size(svg)[0], svg_href="%s-diagram.svg" % self.stem,
                      detail=detail_items(svg), caption="Agent architecture diagram")

    def rail_right_text(self, iid):
        i = self.I[iid]
        k = txt(i.get("kind"), "interface")
        if k == "draft_surface":
            return "draft surface · never a system write"
        if k == "write":
            return "agent writes here"
        if i.get("evidence_state") == "needs_more_information":
            return "needs more information"
        return None

    def build_svg(self):
        self.plan_buses()

        # horizontal extent
        maxx = X0 + self.maxdepth * PITCH + HW + 96
        if self.roots:
            maxx = max(maxx, LANE_LEFT + SPINE_W + 26 + (len(self.roots) - 1) * ROOT_SPACING + PLATE_W + 40)
            if self.chips:
                maxx = max(maxx, self.chip_x + CHIP_W + 40)
        for t, (lx, ly, lw, lh, lines, host) in self.callouts.items():
            maxx = max(maxx, lx + lw + 30)
        self.rail_x0 = LANE_LEFT + SPINE_W + 8
        self.plate_w = {}
        for iid in self.rails:
            self.plate_w[iid] = max(120.0, 26 + self.tw(self.rail_label(iid), 6.0))
            maxx = max(maxx, self.rail_x0 + self.plate_w[iid] + 240)
        vb_w = max(900.0, maxx)
        self.rail_x1 = vb_w - 28
        self.vb_w = vb_w
        band_w = vb_w - LANE_LEFT - 24

        # rails
        rail_top = self.bands_bottom + 56        # the first landing stays clear of the band heading
        self.rail_y = {iid: rail_top + k * RAIL_GAP for k, iid in enumerate(self.rails)}
        if self.rails:
            rail_bottom = rail_top + (len(self.rails) - 1) * RAIL_GAP + 26
        else:
            rail_bottom = self.bands_bottom
        self.frame_bottom = rail_bottom

        # the fixed text on the frame (band headings, rail notes) is kept clear of lines and labels
        self.text_obstacles = []
        for b in self.bands:
            label = "%s · %s" % (b["label"], b["sub"]) if b.get("sub") else b["label"]
            x0 = LANE_LEFT + SPINE_W + 10
            self.text_obstacles.append((x0, b["top"] + 5, x0 + len(label) * 7.7, b["top"] + 20))
        if self.rails:
            x0 = LANE_LEFT + SPINE_W + 10
            self.text_obstacles.append((x0, self.bands_bottom + 13, x0 + 28 * 7.7, self.bands_bottom + 28))
        for iid in self.rails:
            rt = self.rail_right_text(iid)
            if rt:
                y = self.rail_y[iid]
                self.text_obstacles.append((self.rail_x1 - 2 - len(rt) * 5.4, y - 7, self.rail_x1, y + 6))

        self.plan_links()
        self.route_all()
        self.place_labels()

        text, tips = self.strip_text()
        max_chars = int((band_w - 40) / 6.0)
        strip_lines = wrap_all(text, max_chars)
        strip_y = rail_bottom + 18
        strip_h = 14 + 14 * len(strip_lines)
        vb_h = strip_y + strip_h + 36
        self.vb_h = vb_h

        s = []
        s.append('<rect x="0" y="0" width="%.0f" height="%.0f" fill="%s"/>' % (vb_w, vb_h, WHITE))
        s.append('<rect x="%d" y="%d" width="%.0f" height="%.0f" fill="%s" stroke="%s" stroke-width="1.5"/>'
                 % (LANE_LEFT, BAND_TOP, band_w, rail_bottom - BAND_TOP + 8, WHITE, MID))
        for b in self.bands:
            fill, fop = (SKY, "0.30") if b["kind"] in ("agent", "flow", "extra", "unassigned") else (CREAM, "1")
            s.append('<rect data-band="%s" data-band-bounds="%d %.1f %.0f %.1f" x="%d" y="%.1f" width="%.0f" height="%.1f" fill="%s" fill-opacity="%s" stroke="%s" stroke-width="1"/>'
                     % (esc(b["key"]), LANE_LEFT, b["top"], band_w, b["h"], LANE_LEFT, b["top"], band_w, b["h"], fill, fop, MID))
            s.append('<rect x="%d" y="%.1f" width="%d" height="%.1f" fill="%s"/>' % (LANE_LEFT, b["top"], SPINE_W, b["h"], MID))
            label = "%s · %s" % (b["label"], b["sub"]) if b.get("sub") else b["label"]
            s.append('<text x="%d" y="%.1f" fill="%s" font-size="11" font-weight="700" letter-spacing="0.08em">%s</text>'
                     % (LANE_LEFT + SPINE_W + 10, b["top"] + 16, MID, esc(label)))
        if self.rails:
            ih = rail_bottom - (self.bands_bottom + 8)
            s.append('<rect data-band="#interfaces" x="%d" y="%.1f" width="%.0f" height="%.1f" fill="%s" stroke="%s" stroke-width="1"/>'
                     % (LANE_LEFT, self.bands_bottom + 8, band_w, ih, WHITE, MID))
            s.append('<rect x="%d" y="%.1f" width="%d" height="%.1f" fill="%s"/>' % (LANE_LEFT, self.bands_bottom + 8, SPINE_W, ih, MID))
            s.append('<text x="%d" y="%.1f" fill="%s" font-size="11" font-weight="700" letter-spacing="0.08em">INTERFACES · access assumed</text>'
                     % (LANE_LEFT + SPINE_W + 10, self.bands_bottom + 24, MID))
        # paint order: access drops, rails, links, root band, nodes
        # labelled links paint after unlabelled ones, so a line never runs over a label's text
        def lay(routes, labelled):
            return "".join(self.render_edge(x) for x in self.E
                           if x["route"] in routes and (x["id"] in self.labels) == labelled)
        LINKS = ("step", "return", "reentry", "inbound", "toroot", "rootlink")
        access = lay(("drop", "rise", "rail"), False)
        rails = "".join(self.render_rail(r) for r in self.rails)
        links = lay(LINKS, False) + lay(("bus",), False)
        buses = lay(("drop", "rise", "rail"), True) + lay(LINKS, True) + lay(("bus",), True)
        roots = self.render_roots() if self.roots else ""
        nodes = "".join(self.render_node(nid) for nid in self.act)
        write_case = not self.zero_boundary
        ink = CORAL_INK if write_case else WHITE
        strip = ('<g class="boundary-strip" data-zero-write="%s"><title>%s</title>'
                 '<rect x="%d" y="%.1f" width="%.0f" height="%d" rx="6" fill="%s"%s/>'
                 % ("false" if write_case else "true", esc(angle_safe("; ".join(tips) if tips else text)),
                    LANE_LEFT, strip_y, band_w, strip_h, ROSE if write_case else MID,
                    (' stroke="%s" stroke-width="1"' % CORAL_INK) if write_case else ""))
        for k, ln in enumerate(strip_lines):
            strip += ('<text x="%d" y="%.1f" fill="%s" font-size="10.5" font-weight="600">%s</text>'
                      % (LANE_LEFT + 14, strip_y + 18 + 14 * k, ink, esc(ln)))
        strip += "</g>"
        defs = ('<defs>'
                '<marker id="arw" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0 0 L10 5 L0 10 z" fill="%s"/></marker>'
                '<marker id="arw-coral" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7.5" markerHeight="7.5" orient="auto-start-reverse"><path d="M0 0 L10 5 L0 10 z" fill="%s"/></marker>'
                '<marker id="arw-access" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0 0 L10 5 L0 10 z" fill="%s"/></marker>'
                '<marker id="arw-ink" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0 0 L10 5 L0 10 z" fill="%s"/></marker>'
                '</defs>' % (MID, CORAL, ACCESS, CORAL_INK))
        inner = defs + "".join(s) + access + rails + links + buses + roots + nodes + strip
        self.svg = ('<svg id="wf" xmlns="http://www.w3.org/2000/svg" width="%.0f" height="%.0f" viewBox="0 0 %.0f %.0f" font-family="%s">%s</svg>'
                    % (vb_w, vb_h, vb_w, vb_h, FONT, inner))
        return self.svg

    # ================================================================= page text pieces
    def headline(self):
        if self.single_flow:
            lbl = self.flow_label.lower()
            lbl = lbl[:1].upper() + lbl[1:]
            n = len([nid for nid in self.act if self.ntype[nid] != "review_seam"])
            head = "%s: one flow of %d steps" % (lbl, n)
            trig = self.trigger_list()
            lede = ("A deterministic view of the authored internal DAG. The agent runs as one flow with no sub-agents%s."
                    % ((", started by " + trig) if trig else ""))
            return head, lede
        n = len([b for b in self.bands if b["kind"] in ("agent", "extra")])
        if not self.roots:
            return ("The agent's inner team: %d sub-agents, started independently" % n,
                    "A deterministic view of the authored internal DAG. Each band is one sub-agent; there is no coordinator between them.")
        if self.coord_kind == "state-driven-dispatch":
            return ("The agent's inner team: %d sub-agents coordinated by a dispatcher" % n,
                    "A deterministic view of the authored internal DAG. Each band is one sub-agent; the root band routes work to them on recorded state.")
        k = self.coord_kind.replace("-", " ")
        return ("The agent's inner team: %d sub-agents, %s" % (n, k),
                "A deterministic view of the authored internal DAG. Each band is one sub-agent; the root band starts them (%s) on recorded state." % k)

    def trigger_list(self):
        out = []
        for nid in self.act:
            t = self.N[nid].get("entry_trigger")
            if isinstance(t, dict):
                s = spaced(txt(t.get("type"), "trigger"))
                if t.get("label"):
                    s += " (%s)" % txt(t.get("label"))
                out.append(s)
        return ", ".join(out)

    def band_name(self, sid):
        s = self.SA.get(sid)
        return txt(s.get("label"), sid) if s else sid

    def structure_rows(self):
        rows = []
        for rid, name in RULES:
            ids = self.fail.get(rid, [])
            rows.append((rid, name, ids))
        return rows

    def change_rows(self):
        rows = []
        for x in self.write_edges:
            rows.append(("agent write", x["id"], txt(self.I[x["to"]].get("label"), x["to"]) if x["to"] in self.I else x["to"],
                         txt(x["e"].get("authority_grant"), "no grant stated")))
        for iid in self.write_ifaces:
            rows.append(("agent write", iid, txt(self.I[iid].get("label"), iid), txt(self.I[iid].get("authority_grant"), "no grant stated")))
        for nid in self.decisions:
            rows.append(("agent-held decision", nid, txt(self.N[nid].get("title"), nid), txt(self.N[nid].get("authority_grant"), "no grant stated")))
        return rows

    def iface_direction(self, i):
        if i.get("direction"):
            return txt(i.get("direction"))
        return DIRECTION_OF.get(txt(i.get("kind")), "not stated")

    def anchors_of(self, i):
        return id_list(i.get("at_step")) + id_list(i.get("also_drafted_by")) + id_list(i.get("also_emitted_by"))

    def tips(self):
        T = {}
        for nid in self.node_order:
            n = self.N[nid]
            trig = n.get("entry_trigger")
            band = self.band_of.get(nid)
            T[nid] = {"title": txt(n.get("title"), nid), "type": self.ntype[nid],
                      "band": (self.B[band]["label"] if band else ("root band" if nid in self.root_pos else "")),
                      "op": txt(n.get("agent_op")), "evidence": txt(n.get("evidence_state"), "not stated"),
                      "source": txt(n.get("source")), "input": txt(n.get("input")), "output": txt(n.get("output")),
                      "rule_set_ref": txt(n.get("rule_set_ref")) or None,
                      "reads": [str(r) for r in (n.get("reads") if isinstance(n.get("reads"), list) else [])],
                      "drafts_into": txt(n.get("drafts_into")) or None, "emits_handoff": txt(n.get("emits_handoff")) or None,
                      "authoritative": n.get("authoritative"), "grant": txt(n.get("authority_grant")),
                      "runs_on": self.runs_on(nid) or "",
                      "trigger": ("%s · %s" % (spaced(txt(trig.get("type"))), txt(trig.get("label")))) if isinstance(trig, dict) else "",
                      "carries_open": id_list(n.get("carries_open")), "note": txt(n.get("note")),
                      "notes": self.note_for.get(nid, [])}
            if nid in self.root_pos:
                T[nid]["op"] = "router: %s" % txt(self.hdr.get("router_type"), "not stated")
                T[nid]["note"] = "coordination: %s · control interfaces: %s" % (
                    txt(self.hdr.get("coordination_shape"), "not stated"), ", ".join(self.chips) or "none")
        for iid, i in self.I.items():
            T[iid] = {"title": txt(i.get("label"), iid),
                      "type": "interface (%s / %s)" % (txt(i.get("kind"), "interface"), self.iface_direction(i)),
                      "band": "seated in %s" % (", ".join(id_list(i.get("seated_in"))) or "not seated"),
                      "op": "at %s" % (", ".join(self.anchors_of(i)) or "—"),
                      "evidence": txt(i.get("evidence_state"), "not stated"), "source": txt(i.get("source")),
                      "note": txt(i.get("note")), "design_ref": txt(i.get("design_ref")),
                      "grant": txt(i.get("authority_grant")), "reads": [], "carries_open": [],
                      "notes": self.note_for.get(iid, [])}
        return T

    @staticmethod
    def tip_text(d):
        """The hover detail of one step or interface, as plain lines for its SVG <title>."""
        lines = [d["type"] + (" · " + d["band"] if d.get("band") else ""), d["title"]]

        def row(k, v):
            if v:
                lines.append("%s: %s" % (k, v))
        row("Op", d.get("op"))
        row("Runs on", d.get("runs_on"))
        row("Started by", d.get("trigger"))
        row("Input", d.get("input"))
        row("Rule set (ref)", d.get("rule_set_ref"))
        row("Output", d.get("output"))
        row("Reads", ", ".join(d.get("reads") or []))
        row("Drafts into", d.get("drafts_into"))
        row("Handoff", d.get("emits_handoff"))
        if d.get("authoritative") is True:
            row("Decides", "yes")
        row("Authority grant", d.get("grant"))
        row("Evidence", d.get("evidence"))
        row("Source", d.get("source"))
        row("Carries open", ", ".join(d.get("carries_open") or []))
        row("Note", d.get("note"))
        row("Design ref", d.get("design_ref"))
        row("Diagram note", " ".join(d.get("notes") or []))
        return "\n".join(lines)

    def svg_with_titles(self):
        """The diagram SVG with every step's and interface's detail in a native <title> (no script)."""
        tips = self.tips()

        def fill(m):
            d = tips.get(html.unescape(m.group(2)))
            if not d:
                return m.group(0)
            return "%s<title>%s</title>" % (m.group(1), html.escape(angle_safe(self.tip_text(d)), quote=False))
        return re.sub(r'(<g class="node[^"]*" data-id="([^"]*)"[^>]*>)', fill, self.svg)

    # ================================================================= HTML
    def html_page(self):
        head, lede = self.headline()
        note = self.g.get("note")
        badge = first_sentence(note, 80) if note else "Agent architecture · internal DAG"
        cobrand = self.hdr.get("client_display_name")
        cob = ('<span class="cobrand">× %s</span>' % esc(cobrand)) if cobrand else ""
        n_disp = len([x for x in self.E if x["kind"] == "dispatch"])
        n_sub = len([b for b in self.bands if b["kind"] in ("agent", "extra")])
        dh = self.declared_handoffs
        hand = "%d handoffs (%s)" % (len(self.handoffs), ("declared %s" % dh) if dh is not None else "count not declared")
        n_fail = sum(len(v) for v in self.fail.values())
        verdict = ('<span class="verdict pass">PASS</span>' if not n_fail else '<span class="verdict fail">FAIL</span>')
        locator = ("<b>Source of truth:</b> <code>%s</code> (sha256 <code>%s…</code>) &nbsp;·&nbsp; %d positioned steps · %d links (%d dispatch) · %s · %d interfaces · %s · %d agent writes · %d agent-held decisions. Structure checks: %s"
                   % (esc(self.base), self.sha[:16], len(self.act), len(self.E), n_disp,
                      ("one flow, no sub-agents" if self.single_flow else "%d sub-agents" % n_sub),
                      len(self.I), hand, len(self.write_edges) + len(self.write_ifaces), len(self.decisions), verdict))
        legend = self.legend()
        sections = []
        if self.notes:
            items = "".join("<li><code>%s</code> %s</li>" % (esc(i) if i else "page", esc(t)) for i, t in self.notes)
            sections.append('<div class="sh"><span class="dot"></span>Notes on this diagram</div><div class="card"><ul class="notes">%s</ul></div>' % items)
        if not self.single_flow:
            sections.append('<div class="sh"><span class="dot"></span>Coordination model</div><div class="card"><p class="note">%s</p></div>'
                            % self.coordination_html())
        if self.stage_scope is not None:
            rows = []
            for st in self.stage_scope:
                owners = self.stage_cover.get(st, [])
                if owners == ["#flow"]:
                    disp = "the one flow"
                else:
                    disp = ", ".join("<b>%s</b> (<code>%s</code>, %s)" % (esc(self.band_name(o)), esc(o), esc(txt(self.SA[o].get("kind"), "agent"))) for o in owners) or '<span class="warn">no owner</span>'
                rows.append("<tr><td>%s</td><td>%s</td></tr>" % (esc(st), disp))
            title = "Stage coverage" + (" (derived from sub-agent order)" if self.stage_derived else "")
            sections.append('<div class="sh"><span class="dot"></span>%s</div><div class="card"><table class="cov"><tr><th>stage</th><th>covered by</th></tr>%s</table></div>'
                            % (title, "".join(rows)))
        elif not self.single_flow:
            sections.append('<div class="sh"><span class="dot"></span>Stage coverage</div><div class="card"><p class="note">No stage scope declared and no sub-agent owns stages; there is no stage table for this design.</p></div>')
        if self.single_flow:
            sections.append('<div class="card" style="margin-top:22px"><p class="note"><b>No sub-agents and no coordinator: this agent runs as one flow.</b></p></div>')
        else:
            rows = []
            for s in self.subs:
                sid = str(s["id"])
                members = [n for n in self.node_order if self.nsub(n) == sid]
                bd = s.get("boundary") if isinstance(s.get("boundary"), dict) else {}
                seam_note = s.get("seam_note")
                seam_detail = ('<p class="note">%s</p>' % esc(seam_note)
                               if isinstance(seam_note, str) and seam_note.strip() else "")
                state = s.get("evidence_state")
                if isinstance(state, str) and state.strip():
                    seam_detail += '<p class="note">Evidence: %s</p>' % esc(state)
                rows.append("<tr><td><b>%s</b>%s</td><td><code>%s</code></td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>"
                            % (esc(txt(s.get("label"), sid)), seam_detail, esc(sid), esc(txt(s.get("kind"), "agent")),
                               esc(", ".join(id_list(s.get("owned_stages")))) or "—",
                               ", ".join("<code>%s</code>" % esc(m) for m in members) or "—",
                               esc(txt(bd.get("terminates_at"), "not stated"))))
            sections.append('<div class="sh"><span class="dot"></span>Sub-agent decomposition</div><div class="card"><table class="cov"><tr><th>sub-agent</th><th>id</th><th>kind</th><th>stages</th><th>member steps</th><th>terminates</th></tr>%s</table></div>'
                            % "".join(rows))
        if self.I:
            rows = []
            for iid in self.I:
                i = self.I[iid]
                rows.append("<tr><td><b>%s</b></td><td><code>%s</code></td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>"
                            % (esc(txt(i.get("label"), iid)), esc(iid), esc(txt(i.get("kind"), "interface")), esc(self.iface_direction(i)),
                               ", ".join("<code>%s</code>" % esc(v) for v in id_list(i.get("seated_in"))) or "not seated",
                               ", ".join("<code>%s</code>" % esc(v) for v in self.anchors_of(i)) or "—",
                               esc(txt(i.get("evidence_state"), "not stated"))))
            sections.append('<div class="sh"><span class="dot"></span>Interfaces · %d</div><div class="card"><table class="cov"><tr><th>interface</th><th>id</th><th>kind</th><th>direction</th><th>seated in</th><th>anchor</th><th>evidence</th></tr>%s</table></div>'
                            % (len(self.I), "".join(rows)))
        else:
            sections.append('<div class="sh"><span class="dot"></span>Interfaces</div><div class="card"><p class="note">No interfaces declared.</p></div>')
        rows = "".join("<tr><td>%s</td><td>%s</td></tr>" % (esc(name), ('<span class="verdict pass">pass</span>' if not ids else '<span class="verdict fail">fail</span> ' + ", ".join("<code>%s</code>" % esc(i) for i in ids)))
                       for rid, name, ids in self.structure_rows())
        sections.append('<div class="sh"><span class="dot"></span>Structure checks</div><div class="card"><table class="cov structure"><tr><th>check</th><th>result</th></tr>%s</table></div>' % rows)
        crow = self.change_rows()
        if crow:
            body = "".join("<tr><td>%s</td><td><code>%s</code></td><td>%s</td><td>%s</td></tr>" % (esc(a), esc(b), esc(c), esc(d)) for a, b, c, d in crow)
            sections.append('<div class="sh"><span class="dot"></span>What the agent can change</div><div class="card"><table class="cov"><tr><th>what</th><th>id</th><th>where</th><th>authority grant</th></tr>%s</table></div>' % body)
        else:
            sections.append('<div class="sh"><span class="dot"></span>What the agent can change</div><div class="card"><p class="note">None: the agent writes nowhere and holds no decision in this design.</p></div>')
        if self.open_register:
            rows = "".join("<tr><td>%s</td><td><code>%s</code></td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>"
                           % (esc(txt(o.get("item"))), esc(txt(o.get("id"))), esc(txt(o.get("kind"))), esc(txt(o.get("state"))),
                              esc(txt(o.get("follow_on"), "—")), ", ".join("<code>%s</code>" % esc(b) for b in id_list(o.get("bound_to"))))
                           for o in self.open_register)
            sections.append('<div class="sh"><span class="dot"></span>Open items carried forward</div><div class="card"><table class="cov"><tr><th>item</th><th>id</th><th>kind</th><th>state</th><th>follow-on</th><th>bound to</th></tr>%s</table></div>' % rows)
        if self.assumptions:
            rows = "".join("<tr><td><b>%s</b></td><td><code>%s</code></td><td>%s</td><td>%s</td></tr>"
                           % (esc(txt(a.get("label"), txt(a.get("surface")))), esc(txt(a.get("surface"))), esc(txt(a.get("access_state"), "not stated")), esc(txt(a.get("basis"))))
                           for a in self.assumptions)
            sections.append('<div class="sh"><span class="dot"></span>Standing assumptions · access</div><div class="card"><table class="cov"><tr><th>surface</th><th>id</th><th>access_state</th><th>basis</th></tr>%s</table></div>' % rows)
        footer = ("Deterministic view over <code>%s</code>. Bands come from the graph's sub-agents (or one flow band when there are none). Left to right is control-flow order."
                  % esc(self.base))
        page = PAGE % {"name": esc(self.rec_id), "wordmark": WORDMARK, "badge": esc(badge), "cobrand": cob,
                       "head": esc(head), "lede": esc(lede), "locator": locator, "legend": legend,
                       "viewer": self.viewer_html(), "viewer_css": VIEWER_CSS, "sections": "".join(sections), "footer": footer}
        return assert_no_script(page, "internal DAG page")

    def coordination_html(self):
        h = self.hdr
        parts = ["<b>Coordination:</b> %s (kind: %s)." % (esc(txt(h.get("coordination_shape"), "not stated")), esc(self.coord_kind or "not stated"))]
        if self.roots:
            parts.append("<b>Router:</b> %s." % esc(txt(h.get("router_type"), "not stated")))
            parts.append("The root band carries %d dispatch links." % len([x for x in self.E if x["kind"] == "dispatch"]))
        parts.append("<b>Judgment model:</b> %s." % esc(txt(h.get("judgment_model"), "not stated")))
        if h.get("routing_state_observability"):
            parts.append("<b>Routing-state observability:</b> %s (evidence: %s)." % (esc(txt(h.get("routing_state_observability"))), esc(txt(h.get("routing_state_evidence_state"), "not stated"))))
        rej = h.get("coordination_shapes_rejected")
        if isinstance(rej, list) and rej:
            parts.append("<b>Shapes considered and rejected:</b> %s." % esc("; ".join(
                "%s (%s)" % (txt(r.get("shape")), txt(r.get("rejection_rationale"))) if isinstance(r, dict) else txt(r) for r in rej)))
        return " ".join(parts)

    def legend(self):
        items = ['<span class="lg"><span class="stripe agent"></span><b>Step</b></span>']
        if any(self.runs_on(n) == "model" for n in self.act):
            items.append('<span class="lg"><span class="aibadge">AI STEP</span> a step that calls a model</span>')
        if self.seams:
            items.append('<span class="lg"><span class="stripe seam"></span><b>Review seam</b> &mdash; a person decides</span>')
        kinds = {x["kind"] for x in self.E}
        if "handoff" in kinds:
            items.append('<span class="lg"><span class="eln handoff"></span><b>handoff</b></span>')
        if "dispatch" in kinds:
            items.append('<span class="lg"><span class="eln handoff" style="border-top-style:dashed"></span><b>dispatch</b></span>')
        if "inbound" in kinds:
            items.append('<span class="lg"><span class="eln handoff" style="border-top-style:dashed;border-top-width:1.8px"></span><b>inbound</b> (person &rarr; agent)</span>')
        if "read_access" in kinds:
            items.append('<span class="lg"><span class="eln access"></span><b>read</b></span>')
        if self.write_edges or self.write_ifaces:
            items.append('<span class="lg"><span class="eln write"></span><b>agent writes</b></span>')
        if any(x["route"] in ("return", "reentry") for x in self.E):
            items.append('<span class="lg"><span class="eln access" style="border-top-style:dashed"></span><b>return path</b></span>')
        if any(self.ntype[n] not in KNOWN_NODE_TYPES for n in self.node_order):
            items.append('<span class="lg"><span class="stripe other"></span>Other step types (drawn under their own label)</span>')
        if any(x["kind"] not in KNOWN_EDGE_KINDS for x in self.E):
            items.append('<span class="lg"><span class="eln other"></span>Other link kinds</span>')
        if any(txt(i.get("kind"), "interface") not in KNOWN_IFACE_KINDS for i in self.I.values()):
            items.append('<span class="lg"><span class="plate other"></span>Other interface kinds</span>')
        out = '<div class="legend">%s</div>' % "".join(items)
        if self.hdr.get("ai_node_policy"):
            out += '<p class="note">%s</p>' % esc(txt(self.hdr.get("ai_node_policy")))
        return out

    # ================================================================= agents.yaml
    def agents_yaml(self):
        ay = ["# RENDERED from %s by render_internal_dag.py. Do NOT hand-edit - re-run the renderer." % self.base,
              "# The graph object is the source of truth; this file, the diagram, and the coverage manifest are all renders of it."]
        zb = "true" if self.zero_boundary else "false"
        if self.roots:
            r = self.roots[0]
            rn = self.N[r]
            ay.append("root_agent:")
            ay.append("  id: %s" % yq(self.rec_id))
            ay.append("  spec_id: %s" % yq(self.spec_id))
            ay.append("  primitive_form: %s" % yq(self.primitive_form))
            ay.append("  coordination_shape: %s" % yq(txt(self.hdr.get("coordination_shape"), "not stated")))
            if self.coord_kind:
                ay.append("  coordination_shape_kind: %s" % yq(self.coord_kind))
            ay.append("  router_type: %s" % yq(txt(self.hdr.get("router_type"), "not stated")))
            if self.hdr.get("routing_state_observability"):
                ay.append("  routing_state_observability: %s" % yq(txt(self.hdr.get("routing_state_observability"))))
            if self.hdr.get("judgment_model"):
                ay.append("  judgment_model: %s" % yq(txt(self.hdr.get("judgment_model"))))
            ay.append("  zero_agent_action_boundary: %s   # computed from the graph: no write edge or interface and no agent-held decision" % zb)
            ay.append("  seated_interfaces:")
            for iid in id_list(rn.get("seated_interfaces")):
                if iid in self.I:
                    i = self.I[iid]
                    ay += ["    - id: %s" % yq(iid), "      kind: %s" % yq(txt(i.get("kind"), "interface")),
                           "      direction: %s" % yq(self.iface_direction(i))]
                    ay += self.target_line(i, "      ")
                    ay += ["      design_ref: %s" % (yq(txt(i.get("design_ref"))) if i.get("design_ref") else "null   # the deepen stage designs it")]
            ay.append("  dispatch_routes:")
            for x in self.E:
                if x["route"] == "bus":
                    e = x["e"]
                    ay.append("    - to: %s" % yq(x["to"]))
                    if e.get("trigger_type"):
                        ay.append("      trigger_type: %s" % yq(txt(e.get("trigger_type"))))
                    ay.append("      guard: %s" % yq(txt(e.get("routing_guard"))))
                    ay.append("      reads_state_from: %s" % (yq(txt(e.get("reads_state_from"))) if e.get("reads_state_from") else "null"))
                    ay.append("      authoritative: %s" % ("true" if e.get("authoritative") is True else "false"))
            if len(self.roots) > 1:
                ay.append("  other_coordinators: [%s]" % ", ".join(yq(x) for x in self.roots[1:]))
            ind = "  "
        else:
            ay.append("agent:")
            ay.append("  rec_id: %s" % yq(self.rec_id))
            ay.append("  spec_id: %s" % yq(self.spec_id))
            ay.append("  primitive_form: %s" % yq(self.primitive_form))
            if self.coord_kind:
                ay.append("  coordination_shape_kind: %s" % yq(self.coord_kind))
            if self.hdr.get("judgment_model"):
                ay.append("  judgment_model: %s" % yq(txt(self.hdr.get("judgment_model"))))
            ay.append("  zero_agent_action_boundary: %s   # computed from the graph: no write edge or interface and no agent-held decision" % zb)
            ay.append("entry_triggers:")
            any_t = False
            for nid in self.act:
                t = self.N[nid].get("entry_trigger")
                if isinstance(t, dict):
                    any_t = True
                    ay += ["  - node: %s" % yq(nid), "    type: %s" % yq(txt(t.get("type"))), "    label: %s" % yq(txt(t.get("label")))]
            if not any_t:
                ay[-1] = "entry_triggers: []"
            ind = ""
        if not self.single_flow:
            ay.append("%ssub_agents:" % ind)
            for b in self.bands:
                if b["kind"] == "agent" and txt(self.SA[b["sid"]].get("kind"), "agent") != "seam":
                    ay += ["%s  - id: %s" % (ind, yq(b["sid"])), "%s    config_path: %s" % (ind, yq("agents/%s.yaml" % b["sid"]))]
                    ay += self.cooperating_fields(self.SA[b["sid"]], ind + "    ")
            disp = [(iid, i) for iid, i in self.I.items()
                    if txt(i.get("direction")) == "dispatch" and txt(i.get("target"))]
            if disp:
                ay.append("%sdispatch_interfaces:   # agent-to-agent dispatch: seated in the sender, target is the receiver" % ind)
                for iid, i in disp:
                    ay += ["%s  - id: %s" % (ind, yq(iid)),
                           "%s    seated_in: [%s]" % (ind, ", ".join(yq(x) for x in id_list(i.get("seated_in")))),
                           "%s    target: %s" % (ind, yq(txt(i.get("target"))))]
            ay.append("%scoverage_seams:   # no-agent-participation stages; no runnable sub-agent config emitted" % ind)
            for s in self.subs:
                if txt(s.get("kind"), "agent") == "seam":
                    ay += ["%s  - id: %s" % (ind, yq(str(s["id"]))),
                           "%s    stages: [%s]" % (ind, ", ".join(yq(x) for x in id_list(s.get("owned_stages")))),
                           "%s    note: %s" % (ind, yq(txt(s.get("seam_note"))))]
        else:
            ay.append("steps:")
            flow = [nid for nid in self.act if self.band_of.get(nid) == "#flow"]
            flow.sort(key=lambda n: (self.depth[n], self.nidx[n]))
            for nid in flow:
                n = self.N[nid]
                reads = n.get("reads") if isinstance(n.get("reads"), list) else []
                ay += ["  - id: %s" % yq(nid), "    type: %s" % yq(self.ntype[nid]), "    title: %s" % yq(txt(n.get("title"), nid)),
                       "    runs_on: %s" % (yq(self.runs_on(nid)) if self.runs_on(nid) else "null"),
                       "    input: %s" % yq(txt(n.get("input"))), "    output: %s" % yq(txt(n.get("output"))),
                       "    reads: [%s]" % ", ".join(yq(r) for r in reads),
                       "    drafts_into: %s" % (yq(txt(n.get("drafts_into"))) if n.get("drafts_into") else "null")]
            ay.append("interfaces:" if self.I else "interfaces: []")
            for iid, i in self.I.items():
                ay += ["  - id: %s" % yq(iid), "    kind: %s" % yq(txt(i.get("kind"), "interface")), "    direction: %s" % yq(self.iface_direction(i))]
                ay += self.target_line(i, "    ")
        return "\n".join(ay) + "\n"

    @staticmethod
    def cooperating_fields(s, ind):
        """The cooperating-agent fields of a `kind: agent` sub-agent (lane, write_grants,
        future_state_steps), each emitted only when the graph states it, so legacy output is unchanged."""
        out = []
        if txt(s.get("kind"), "agent") != "agent":
            return out
        if "lane" in s and txt(s.get("lane")):
            out.append("%slane: %s" % (ind, yq(txt(s.get("lane")))))
        for f in ("write_grants", "future_state_steps"):
            if isinstance(s.get(f), list):
                out.append("%s%s: [%s]" % (ind, f, ", ".join(yq(str(x)) for x in s[f])))
        return out

    @staticmethod
    def target_line(i, ind):
        """`target` of a dispatch interface (the receiving sub-agent id), only when the graph states it."""
        if txt(i.get("direction")) == "dispatch" and txt(i.get("target")):
            return ["%starget: %s" % (ind, yq(txt(i.get("target"))))]
        return []

    def sub_agent_yamls(self):
        out = {}
        if self.single_flow:
            return out
        for b in self.bands:
            if b["kind"] != "agent":
                continue
            s = self.SA[b["sid"]]
            if txt(s.get("kind"), "agent") == "seam":
                continue
            sid = b["sid"]
            bd = s.get("boundary") if isinstance(s.get("boundary"), dict) else {}
            c = ["# RENDERED from %s. Do NOT hand-edit." % self.base, "id: %s" % yq(sid), "label: %s" % yq(txt(s.get("label"), sid)),
                 "kind: %s" % yq(txt(s.get("kind"), "agent")),
                 "owned_stages: [%s]" % ", ".join(yq(x) for x in id_list(s.get("owned_stages"))),
                 "evidence_state: %s" % yq(txt(s.get("evidence_state"), "not stated")), "boundary:",
                 "  inputs: %s" % yq(txt(bd.get("inputs"), "not stated")), "  outputs: %s" % yq(txt(bd.get("outputs"), "not stated")),
                 "  terminates_at: %s" % yq(txt(bd.get("terminates_at"), "not stated")), "member_steps:"]
            members = [m for m in (id_list(s.get("member_nodes")) if "member_nodes" in s else
                                   [n for n in self.node_order if self.nsub(n) == sid]) if m in self.N]
            for m in members:
                n = self.N[m]
                c += ["  - id: %s" % yq(m), "    type: %s" % yq(self.ntype[m]), "    agent_op: %s" % yq(txt(n.get("agent_op"))),
                      "    title: %s" % yq(txt(n.get("title"), m)),
                      "    input: %s" % yq(txt(n.get("input"))),
                      "    rule_set_ref: %s" % (yq(txt(n.get("rule_set_ref"))) if n.get("rule_set_ref") else "null"),
                      "    output: %s" % yq(txt(n.get("output")))]
                if self.runs_on(m):
                    c.append("    runs_on: %s" % yq(self.runs_on(m)))
                if isinstance(n.get("reads"), list) and n.get("reads"):
                    c.append("    reads: [%s]" % ", ".join(yq(r) for r in n["reads"]))
                if n.get("drafts_into"):
                    c.append("    drafts_into: %s" % yq(txt(n.get("drafts_into"))))
                if n.get("emits_handoff"):
                    c.append("    emits_handoff: %s" % yq(txt(n.get("emits_handoff"))))
                if n.get("authoritative") is not None:
                    c.append("    authoritative: %s" % ("true" if n.get("authoritative") is True else "false"))
                if n.get("authority_grant"):
                    c.append("    authority_grant: %s" % yq(txt(n.get("authority_grant"))))
                c += ["    evidence_state: %s" % yq(txt(n.get("evidence_state"), "not stated")),
                      "    carries_open: [%s]" % ", ".join(yq(x) for x in id_list(n.get("carries_open")))]
            c.append("seated_interfaces:")
            for iid in id_list(s.get("seated_interfaces")):
                if iid not in self.I:
                    continue
                i = self.I[iid]
                c += ["  - id: %s" % yq(iid), "    kind: %s" % yq(txt(i.get("kind"), "interface")),
                      "    direction: %s" % yq(self.iface_direction(i))]
                c += self.target_line(i, "    ")
                c += ["    at_step: [%s]" % ", ".join(yq(a) for a in id_list(i.get("at_step"))),
                      "    evidence_state: %s" % yq(txt(i.get("evidence_state"), "not stated")),
                      "    design_ref: %s" % (yq(txt(i.get("design_ref"))) if i.get("design_ref") else "null   # the deepen stage designs it")]
            c.append("carries_open: [%s]" % ", ".join(yq(x) for x in id_list(s.get("carries_open"))))
            out[sid] = "\n".join(c) + "\n"
        return out

    # ================================================================= manifest
    def manifest(self, rendered_text):
        from collections import Counter
        L = []
        n_fail = sum(len(v) for v in self.fail.values())
        L.append("# Coverage manifest: the agent's internal DAG\n")
        L.append("_Agent: `%s` · internal handle: `dag-coverage-manifest`._\n" % self.rec_id)
        L.append("**Source of truth:** `%s` (sha256 `%s`).\n" % (self.base, self.sha))
        L.append("**Policy:** coverage, not count. Every node, link and interface maps to a diagram element or a stated "
                 "entry below; no silent drops. Derived deterministically by walking the graph (the same script that "
                 "writes the diagram and `agents.yaml`). Left to right is control-flow order (longest path over "
                 "`sequence` / `then` / `else` / `handoff` and every unfamiliar link kind between steps; dispatch, "
                 "inbound, re-entry and access links excluded).\n")
        L.append("**Structure checks: %s.**\n" % ("PASS" if not n_fail else "FAIL"))
        if self.single_flow:
            L.append("**No sub-agents and no coordinator: this agent runs as one flow.**\n")
        L.append("## 1. Tally (reconciles with the graph)\n")
        tc = Counter(self.ntype[n] for n in self.node_order)
        kc = Counter(x["kind"] for x in self.E)
        L.append("**Nodes: %d total.** " % len(self.node_order) + " · ".join("`%s` %d" % (k, v) for k, v in tc.items()) + ".\n")
        L.append("**Links: %d drawn.** " % len(self.E) + " · ".join("`%s` %d" % (k, v) for k, v in kc.items()) + ".\n")
        if not self.single_flow:
            sc = Counter(txt(s.get("kind"), "agent") for s in self.subs)
            L.append("**Sub-agents: %d total.** " % len(self.subs) + " · ".join("`%s` %d" % (k, v) for k, v in sc.items()) + ".\n")
        ic = Counter(self.iface_direction(i) for i in self.I.values())
        L.append("**Interfaces: %d.** " % len(self.I) + (" · ".join("%s %d" % (k, v) for k, v in ic.items()) or "No interfaces declared") + ".\n")
        dh = self.declared_handoffs
        L.append("**Handoffs:** %d (%s).\n" % (len(self.handoffs), ("declared %s" % dh) if dh is not None else "count not declared"))
        L.append("**Positioned steps:** %d; a root_agent is drawn in the root band, not layered.\n" % len(self.act))
        if not self.single_flow:
            L.append("## 2. Coordination model\n")
            h = self.hdr
            L.append("- Coordination: %s (kind: %s)." % (txt(h.get("coordination_shape"), "not stated"), self.coord_kind or "not stated"))
            if self.roots:
                L.append("- Router: %s The root band carries %d dispatch links." % (txt(h.get("router_type"), "not stated"),
                                                                                 len([x for x in self.E if x["kind"] == "dispatch"])))
            L.append("- Judgment model: %s." % txt(h.get("judgment_model"), "not stated"))
            L.append("- Coordination evidence: %s; routing-state evidence: %s." % (
                txt(h.get("coordination_shape_evidence_state"), "not stated"), txt(h.get("routing_state_evidence_state"), "not stated")))
            if h.get("routing_state_observability"):
                L.append("- Routing-state observability: %s" % txt(h.get("routing_state_observability")))
            rej = h.get("coordination_shapes_rejected")
            if isinstance(rej, list) and rej:
                L.append("- Shapes considered and rejected: %s." % "; ".join(
                    "%s (%s)" % (txt(r.get("shape")), txt(r.get("rejection_rationale"))) if isinstance(r, dict) else txt(r) for r in rej))
            L.append("")
        L.append("## 3. Stage coverage\n")
        if self.stage_scope is not None:
            if self.stage_derived:
                L.append("_Stage scope derived: not declared in the header; taken from the sub-agents' owned stages in registry order._\n")
            L.append("| stage | covered by |")
            L.append("|---|---|")
            for st in self.stage_scope:
                owners = self.stage_cover.get(st, [])
                if owners == ["#flow"]:
                    disp = "the one flow"
                else:
                    disp = ", ".join("%s (`%s`, %s)" % (self.band_name(o), o, txt(self.SA[o].get("kind"), "agent")) for o in owners) or "**no owner**"
                L.append("| %s | %s |" % (st, disp))
            L.append("")
        else:
            L.append("No stage scope declared and no sub-agent owns stages; there is no stage table for this design.\n")
        if not self.single_flow:
            L.append("## 4. Sub-agent decomposition\n")
            L.append("| sub-agent | id | kind | stages | member steps | seated interfaces | terminates | evidence | carries open |")
            L.append("|---|---|---|---|---|---|---|---|---|")
            for s in self.subs:
                sid = str(s["id"])
                bd = s.get("boundary") if isinstance(s.get("boundary"), dict) else {}
                mem = ", ".join("`%s`" % m for m in self.node_order if self.nsub(m) == sid) or "—"
                L.append("| **%s** | `%s` | %s | %s | %s | %s | %s | %s | %s |" % (
                    txt(s.get("label"), sid), sid, txt(s.get("kind"), "agent"), ", ".join(id_list(s.get("owned_stages"))) or "—",
                    mem, ", ".join("`%s`" % i for i in id_list(s.get("seated_interfaces"))) or "—",
                    txt(bd.get("terminates_at"), "not stated"), txt(s.get("evidence_state"), "not stated"),
                    ", ".join(id_list(s.get("carries_open"))) or "—"))
            L.append("")
        L.append("## 5. Interfaces\n")
        if self.I:
            L.append("| interface | id | kind | direction | seated in | anchor | evidence | design_ref |")
            L.append("|---|---|---|---|---|---|---|---|")
            for iid, i in self.I.items():
                L.append("| **%s** | `%s` | %s | %s | %s | %s | %s | %s |" % (
                    txt(i.get("label"), iid), iid, txt(i.get("kind"), "interface"), self.iface_direction(i),
                    ", ".join("`%s`" % v for v in id_list(i.get("seated_in"))) or "not seated",
                    ", ".join("`%s`" % v for v in self.anchors_of(i)) or "—", txt(i.get("evidence_state"), "not stated"),
                    txt(i.get("design_ref"), "design pending")))
            L.append("")
        else:
            L.append("No interfaces declared.\n")
        L.append("## 6. Structure checks\n")
        L.append("| check | result |")
        L.append("|---|---|")
        for rid, name, ids in self.structure_rows():
            L.append("| %s | %s |" % (name, "pass" if not ids else "FAIL: " + ", ".join("`%s`" % i for i in ids)))
        L.append("")
        L.append("## 7. What the agent can change\n")
        crow = self.change_rows()
        if crow:
            L.append("| what | id | where | authority grant |")
            L.append("|---|---|---|---|")
            for a, b, c, d in crow:
                L.append("| %s | `%s` | %s | %s |" % (a, b, c, d))
            L.append("")
        else:
            L.append("None: the agent writes nowhere and holds no decision in this design.\n")
        L.append("## 8. Notes on this diagram\n")
        if self.notes:
            for i, t in self.notes:
                L.append("- `%s`: %s" % (i or "page", t))
            L.append("")
        else:
            L.append("None.\n")
        L.append("## 9. Open items and standing assumptions\n")
        if self.open_register:
            for o in self.open_register:
                L.append("- `%s` [kind: %s / %s] %s → bound to %s; follow-on: %s; closure: %s." % (
                    txt(o.get("id")), txt(o.get("kind"), "not stated"), txt(o.get("state"), "not stated"), txt(o.get("item")),
                    ", ".join("`%s`" % b for b in id_list(o.get("bound_to"))) or "—", txt(o.get("follow_on"), "—"),
                    txt(o.get("closure"), "—")))
            L.append("")
        else:
            L.append("No open items carried.\n")
        if self.assumptions:
            L.append("| surface | id | access_state | basis |")
            L.append("|---|---|---|---|")
            for a in self.assumptions:
                L.append("| **%s** | `%s` | %s | %s |" % (txt(a.get("label"), txt(a.get("surface"))), txt(a.get("surface")),
                                                      txt(a.get("access_state"), "not stated"), txt(a.get("basis"))))
            L.append("")
        banned = ["contract", "Context Graph", "Process Index", "Agent Audit", "Mode A", "Mode B"]
        scan = rendered_text + "\n".join(L)
        hits = {b: scan.count(b) for b in banned if scan.count(b)}
        hw = len(re.findall(r"\bhumans\b", scan))
        if hw:
            hits["humans"] = hw
        L.append("**Terminology preflight** (FDE working vocabulary, Notion `3bbed0c6-ff0e-81c2-95ea-e819366aa08f`): "
                 "banned-term hits in the rendered page, this manifest and the rendered YAML: **%s**.\n" % ("none" if not hits else hits))
        recon = self.hdr.get("r2_backbone_reconciliation")
        if isinstance(recon, dict):
            L.append("## 10. How this map fits the backbone\n")
            L.append("Source: `%s` (evidence_state %s).\n" % (txt(recon.get("source")), txt(recon.get("evidence_state"), "not stated")))
            L.append("- **The declared surface.** %s" % txt(recon.get("declared_surface")))
            L.append("- **What this map realizes.** %s" % txt(recon.get("this_stage_realizes")))
            L.append("- **Evidence-state lift.** %s" % txt(recon.get("surface_evidence_state_lift")))
            mp = recon.get("mapping")
            if isinstance(mp, dict) and mp:
                L.append("\n| backbone element | realized in this internal DAG |")
                L.append("|---|---|")
                for k, v in mp.items():
                    L.append("| `%s` | %s |" % (k, txt(v)))
            L.append("")
        L.append("## Coverage assertion\n")
        L.append("All %d nodes, %d drawn links and %d interfaces map to a diagram element or a stated entry above. "
                 "Structure checks: **%s**.\n" % (len(self.node_order), len(self.E), len(self.I), "PASS" if not n_fail else "FAIL"))
        return "\n".join(L)


# ===================================================================== page template
WORDMARK = ('<svg class="wm" viewBox="0 0 963 206" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">'
 '<path d="M127.046 14.4375C105.642 14.4375 88.2656 31.8866 88.2656 53.2903C88.2656 74.6939 105.682 92.1432 127.046 92.1432C148.41 92.1432 165.827 74.6939 165.827 53.2903C165.827 31.8866 148.41 14.4375 127.046 14.4375Z" fill="#F56740"/>'
 '<path d="M253.966 127.976C253.966 153.334 247.13 172.944 233.62 186.275C221.493 198.261 204.117 204.621 183.404 204.621C159.477 204.621 140.636 195.285 128.957 177.592L127.003 174.656L125.05 177.592C113.371 195.285 94.5709 204.621 70.6026 204.621C49.8898 204.621 32.5139 198.302 20.3466 186.275C6.83646 172.944 0 153.334 0 127.976V1.14373C14.0799 1.46988 45.8205 4.609 45.8205 16.3504V128.017C45.8205 150.276 56.1566 162.507 74.9568 162.507C85.8219 162.507 104.093 149.408 104.093 119.402V117.364C104.093 116.875 104.256 116.712 104.419 116.589C105.029 116.1 106.372 116.019 107.145 116.222C120.126 119.851 134.125 119.81 147.106 116.141C147.554 116.019 148.693 116.222 149.507 116.793C149.792 116.997 149.914 117.201 149.914 117.282V119.321C149.914 149.327 168.144 162.426 179.05 162.426C197.81 162.426 208.186 150.195 208.186 127.935V16.269C208.186 13.4967 209.244 11.1729 211.401 9.17521C220.761 0.409891 246.357 0.654425 253.966 0.939807V127.935V127.976Z" fill="#F56740"/>'
 '<rect x="282.008" y="2.08984" width="49.2506" height="201.321" rx="6.15633" fill="#F56740"/>'
 '<path d="M358.822 45.1841C358.822 48.5842 361.579 51.3405 364.979 51.3405L397.461 51.3405C399.161 51.3405 400.54 52.7186 400.54 54.4186L400.54 197.255C400.54 200.655 403.296 203.411 406.696 203.411L443.634 203.411C447.034 203.411 449.79 200.655 449.79 197.255L449.79 54.4186C449.79 52.7186 451.168 51.3405 452.868 51.3405L485.351 51.3405C488.752 51.3405 491.508 48.5842 491.508 45.1841L491.508 8.24618C491.508 4.84613 488.752 2.08984 485.351 2.08984L449.79 2.08984L400.54 2.08984L364.979 2.08984C361.579 2.08984 358.822 4.84612 358.822 8.24617L358.822 45.1841Z" fill="#F56740"/>'
 '<path d="M562.165 203.411C565.565 203.411 568.321 200.655 568.321 197.255L568.321 122.138C568.321 120.438 569.699 119.06 571.399 119.06L635.898 119.06C637.598 119.06 638.976 120.438 638.976 122.138L638.976 197.255C638.976 200.655 641.732 203.411 645.132 203.411L682.07 203.411C685.47 203.411 688.227 200.655 688.227 197.255V119.06L688.227 69.8095L688.227 8.24617C688.227 4.84612 685.47 2.08984 682.07 2.08984L645.132 2.08984C641.732 2.08984 638.976 4.84613 638.976 8.24617L638.976 66.7313C638.976 68.4313 637.598 69.8095 635.898 69.8095L571.399 69.8095C569.699 69.8095 568.321 68.4313 568.321 66.7313L568.321 8.24617C568.321 4.84612 565.565 2.08984 562.165 2.08984L525.227 2.08984C521.827 2.08984 519.07 4.84613 519.07 8.24617L519.07 197.255C519.07 200.655 521.827 203.411 525.227 203.411L562.165 203.411Z" fill="#F56740"/>'
 '<rect x="715.789" y="2.08984" width="49.2506" height="201.321" rx="6.15633" fill="#F56740"/>'
 '<path d="M875.221 0.0275631C922.65 -1.19304 961.217 38.2818 961.218 87.4289V197.119C961.218 200.519 958.463 203.275 955.063 203.277L918.144 203.292C914.742 203.294 911.984 200.536 911.984 197.135V87.4504C911.984 66.2655 896.859 49.24 876.414 49.7658C856.675 50.2739 842.236 66.9967 842.235 87.4504V197.138C842.235 197.487 842.204 197.83 842.148 198.163V203.417H792.898V8.25217C792.898 4.85214 795.655 2.09592 799.055 2.09592H835.992C839.285 2.09592 841.973 4.68073 841.973 8.24617V16.5024C852.212 6.55081 866.005 0.263 875.221 0.0275631Z" fill="#F56740"/>'
 '</svg>')


def angle_safe(text):
    """Text for a <title> or <textarea>: "<" written as the single angle quote ‹, since those elements
    may hold no "<" even once entities are decoded (the no-script page shape, FORDEEN-87)."""
    return str(text).replace("<", "\u2039")

PAGE = '''<!doctype html>
<meta charset="utf-8">
<title>Internal DAG · %(name)s</title>
<style>
:root{--canvas:#FFFFFF;--ink:#0B1E2F;--muted:#4A5560;--muted2:#6B6660;--faint:#A79E92;
  --accent:#F56740;--accent-ink:#B23A1E;--access:#2E6E86;--surface:#FBF6EF;--sky:#CFF3FE;--sun:#FFF5D2;--rose:#FFE0D8;
  --border:rgba(11,30,47,0.10);--border-card:#E7DFD3;--hairline:#E7DFD3;
  --sans:'Inter',-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;--col:1120px;
  --sh-card:0 1px 1px rgba(11,30,47,0.04),0 24px 48px -22px rgba(11,30,47,0.12);}
*{box-sizing:border-box}
body{margin:0;font-family:var(--sans);color:var(--ink);font-size:14px;line-height:1.6;background:var(--canvas);-webkit-font-smoothing:antialiased}
h1{margin:0;font-weight:600;letter-spacing:-0.02em;line-height:1.1}
p{margin:0} code{font-family:var(--sans);font-size:0.86em;background:rgba(11,30,47,0.05);padding:1px 5px;border-radius:3px}
.appbar{max-width:var(--col);margin:0 auto;display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;padding:20px 24px 14px;gap:16px}
.brand{display:flex;align-items:center;flex-wrap:wrap;min-width:0;max-width:100%%;gap:13px} .wm{height:23px;width:auto;display:block}
.cobrand{font-size:10px;letter-spacing:0.12em;text-transform:uppercase;color:var(--ink);font-weight:600}
.badge{max-width:100%%;overflow-wrap:anywhere;font-size:10px;letter-spacing:0.12em;text-transform:uppercase;color:var(--muted2);background:#F2EADF;border:1px solid var(--border);border-radius:999px;padding:5px 11px}
.appdiv{border-bottom:1px solid var(--hairline)}
.wrap{max-width:var(--col);margin:0 auto;padding:28px 24px 72px}
.phead h1{font-size:clamp(22px,3vw,32px);margin:8px 0 12px;line-height:1.08}
.phead .lede{color:var(--muted);font-size:15px;max-width:82ch}
.eyebrow{font-size:11px;font-weight:600;letter-spacing:0.12em;text-transform:uppercase;color:var(--muted2);display:flex;align-items:center;gap:9px}
.eyebrow .dot,.sh .dot{width:8px;height:8px;border-radius:50%%;background:var(--accent)}
.locator{background:var(--surface);border:1px solid var(--border-card);border-radius:12px;padding:14px 18px;margin:18px 0 0;font-size:12.5px;color:var(--muted)}
.locator b{color:var(--ink)}
.sh{font-size:10px;letter-spacing:0.12em;text-transform:uppercase;color:var(--muted2);margin:30px 0 12px;display:flex;align-items:center;gap:9px}
.card{background:var(--surface);border:1px solid var(--border-card);border-radius:16px;padding:16px 20px 18px;box-shadow:var(--sh-card)}
.legend{display:flex;flex-wrap:wrap;gap:8px 16px;align-items:center;font-size:12.5px;color:var(--muted);margin:2px 0 12px}
.lg{display:inline-flex;align-items:center;gap:7px}
.stripe{width:6px;height:16px;border-radius:3px} .stripe.agent{background:var(--access)} .stripe.seam{background:var(--accent)} .stripe.other{background:var(--muted)}
.aibadge{font-size:9px;font-weight:700;letter-spacing:0.06em;color:#fff;background:var(--accent);border-radius:999px;padding:1px 7px}
.plate{width:22px;height:12px;border-radius:4px;border:1px solid var(--ink)} .plate.other{background:#fff}
.eln{width:30px;height:0;border-top-width:2.2px;border-top-style:solid;display:inline-block}
.eln.handoff{border-top-color:var(--accent);border-top-width:2.6px}
.eln.access{border-top-color:var(--access);border-top-style:dashed}
.eln.write{border-top-color:var(--accent-ink)}
.eln.other{border-top-color:var(--ink);border-top-style:dashed;border-top-width:1.4px}
%(viewer_css)s
.dv-frame svg .node{cursor:help}
.hint{font-size:10.5px;color:var(--muted2);margin:10px 2px 0}
.note{font-size:13px;color:var(--muted);line-height:1.6} .note b{color:var(--ink)}
ul.notes{margin:0;padding-left:18px;font-size:12.5px;color:var(--muted)}
.warn{color:var(--accent-ink);font-weight:600}
.card:has(> table.cov){max-width:100%%;overflow-x:auto}
table.cov{width:100%%;border-collapse:collapse;font-size:12px;margin-top:6px}
table.cov td,table.cov th{border-bottom:1px solid var(--hairline);padding:5px 8px;color:var(--muted);text-align:left;vertical-align:top}
table.cov th{color:var(--ink);font-weight:600;font-size:11px} table.cov code{background:none;color:var(--ink)}
.verdict{display:inline-block;font-size:11px;font-weight:700;letter-spacing:0.1em;padding:2px 9px;border-radius:999px}
.verdict.pass{background:#DFF3E4;color:#1E6B36;border:1px solid #B7E0C2}
.verdict.fail{background:var(--rose);color:var(--accent-ink);border:1px solid var(--accent)}
footer{margin-top:40px;padding-top:18px;border-top:1px solid var(--hairline);color:var(--muted2);font-size:11.5px;line-height:1.6}
</style>

<div class="appbar">
  <div class="brand" aria-label="within">%(wordmark)s%(cobrand)s<span class="badge" style="margin-left:6px">%(badge)s</span></div>
  <span class="badge">Agent architecture · internal DAG</span>
</div>
<div class="appdiv"></div>

<main class="wrap">
  <div class="phead">
    <p class="eyebrow"><span class="dot"></span>Internal DAG · %(name)s</p>
    <h1>%(head)s</h1>
    <p class="lede">%(lede)s</p>
  </div>
  <div class="locator"><p>%(locator)s</p></div>

  <div class="sh"><span class="dot"></span>The diagram</div>
  <div class="card">
    %(legend)s
    %(viewer)s
    <p class="hint">Opens fitted to the width · pick a size, or Full window, and scroll inside the frame to move around · open the diagram on its own to use the browser's zoom · hover a step for its detail, or read every step in words under the diagram · a small i marks a note</p>
  </div>
  %(sections)s
  <footer>%(footer)s</footer>
</main>

'''


class OutputExists(Exception):
    """An output file is already there and --overwrite was not given (nothing was written)."""


def write_outputs(dag, out_dir, overwrite=False):
    os.makedirs(out_dir, exist_ok=True)
    stem = dag.stem
    out_html = os.path.join(out_dir, "%s-diagram.html" % stem)
    out_svg = os.path.join(out_dir, "%s-diagram.svg" % stem)
    out_png = os.path.join(out_dir, "%s-diagram.selfview.png" % stem)
    out_cov = os.path.join(out_dir, "dag-coverage-manifest.md")
    out_agents = os.path.join(out_dir, "agents.yaml")
    agents_dir = os.path.join(out_dir, "agents")
    dag.build_svg()
    svg = dag.svg_with_titles()
    page = dag.html_page()
    ay = dag.agents_yaml()
    subs = dag.sub_agent_yamls()
    if not overwrite:
        # Same rule as the workflow renderer: refuse to replace existing outputs unless told to.
        targets = [out_html, out_svg, out_png, out_cov, out_agents] + [os.path.join(agents_dir, "%s.yaml" % sid) for sid in subs]
        existing = [p for p in targets if os.path.exists(p)]
        if existing:
            raise OutputExists("output already exists (%s). Re-rendering the same graph in a fix cycle replaces these "
                               "files: add the --overwrite flag to do that. Otherwise render to a new folder"
                               % ", ".join(os.path.basename(p) for p in existing))
    # stale per-sub-agent configs from earlier renders are removed; hand-written files are left alone
    if os.path.isdir(agents_dir):
        for fn in sorted(os.listdir(agents_dir)):
            p = os.path.join(agents_dir, fn)
            if not fn.endswith(".yaml") or fn[:-5] in subs or not os.path.isfile(p):
                continue
            with open(p, encoding="utf-8", errors="replace") as fh:
                first = fh.readline()
            if first.startswith("# RENDERED from"):
                os.remove(p)
    if subs:
        os.makedirs(agents_dir, exist_ok=True)
    assert_no_script(page, out_html)
    with open(out_html, "w", encoding="utf-8") as fh:
        fh.write(page)
    with open(out_svg, "w", encoding="utf-8") as fh:
        fh.write(svg)
    with open(out_agents, "w", encoding="utf-8") as fh:
        fh.write(ay)
    for sid, text in subs.items():
        with open(os.path.join(agents_dir, "%s.yaml" % sid), "w", encoding="utf-8") as fh:
            fh.write(text)
    rendered = page + ay + "".join(subs.values())
    with open(out_cov, "w", encoding="utf-8") as fh:
        fh.write(dag.manifest(rendered))
    rsvg = os.environ.get("RSVG_CONVERT") or shutil.which("rsvg-convert")
    png = "skipped (rsvg-convert not found)"
    if rsvg and os.path.isfile(rsvg):
        try:
            subprocess.run([rsvg, "-o", out_png, "--background-color", "white"], input=svg.encode("utf-8"),
                           check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            png = "ok"
        except Exception as ex:   # the PNG is a convenience raster; its absence is not a failure
            png = "skipped (%s)" % ex
    else:
        sys.stderr.write("WARNING: rsvg-convert not found; the self-view PNG was not written.\n")
    for p in (out_html, out_svg, out_cov, out_agents):
        print("wrote", p)
    for sid in subs:
        print("wrote", os.path.join(agents_dir, "%s.yaml" % sid))
    print("PNG:", out_png, png)


def main(argv):
    overwrite = "--overwrite" in argv
    argv = [a for a in argv if a != "--overwrite"]
    if not argv:
        sys.stderr.write("usage: render_internal_dag.py [--overwrite] GRAPH.graph.json [OUT_DIR]\n"
                         "  Refuses to replace existing outputs unless --overwrite is given.\n")
        return 2
    graph_path = os.path.abspath(argv[0])
    if not os.path.isfile(graph_path):
        sys.stderr.write("Missing graph object: %s\n" % graph_path)
        return 2
    out_dir = os.path.abspath(argv[1]) if len(argv) > 1 else os.path.dirname(graph_path)
    try:
        with open(graph_path, encoding="utf-8") as fh:
            g = json.load(fh)
        dag = Dag(g, graph_path)
    except (ValueError, Unreadable) as ex:
        sys.stderr.write("UNREADABLE: %s (renderer input is a JSON internal-DAG graph; nothing was written)\n" % ex)
        return 2
    try:
        write_outputs(dag, out_dir, overwrite)
    except OutputExists as ex:
        sys.stderr.write("REFUSED: %s (nothing was written)\n" % ex)
        return 2
    mode = "single-flow" if dag.single_flow else "banded"
    print("mode:", mode, "| bands:", ", ".join(b["label"] for b in dag.bands), "| max depth", dag.maxdepth)
    for i, t in dag.notes:
        print("NOTE render: %s: %s" % (i or "page", t))
    failed = False
    for rid, name, ids in dag.structure_rows():
        if ids:
            failed = True
            print("STRUCTURE: %s: %s" % (name, ", ".join(ids)))
    if failed:
        print("Structure checks: FAIL (every output was written)")
        return 1
    print("Structure checks: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
