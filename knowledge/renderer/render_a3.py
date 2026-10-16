#!/usr/bin/env python3
"""A3 renderer: lays out a current- or future-state graph (a3-lanes-v0 or a3-lanes-v0.1) to the lane standard, deterministically.

Usage: render_a3.py BUNDLE.json OUT_DIR [--stem NAME] [--budget PX]

  --budget PX   default 0: one coherent journey with a single lane stack. Use the page
                size controls or standalone SVG to read detail at native size.
                An explicit positive budget opts into legacy stacked parts ("tiers").

Writes into OUT_DIR:
  NAME.svg          the diagram; the root carries data-renderer="factory-workflow-a3-v1" and every box its
                    detail in an SVG <title> tooltip;
  NAME.html         the page: the SVG inline plus the label list, CSS only (no script, no event attributes);
  NAME.layout.json  columns, tiers, routing, self-check results and hidden: [{id, reason}];
  NAME.json         the input bundle plus the render keys render_workflow.py writes, renderer "factory-workflow-a3-v1".
Exits 1, naming the cause, on a bundle with no AI and agents lane or more than one, an agent step (a node with an
`agent` tag) outside that lane, a cycle not marked kind 'loop', or an output that would overwrite the input bundle.
Agents share the one AI and agents lane and are told apart by their `agent` tag, drawn in the box. An ai_use node
named in an agent step's future.from with change "assigned" is a replaced AI use: it is not drawn, and it and its
edges are listed in layout.json hidden with the reason. Each stays in the SVG as an empty <g data-hidden="replaced">
bound by its graph id (no box, no line), so svg_binding still finds every graph element.
Exits 2 when the self-check finds anything (the files are still written, so the findings can be read).
Python stdlib only. No model calls. No layout positions are read from the input (v0 forbids them).
Ported from the 0435 prototype (9 Oct 2026); its viewer script is not carried over.
"""
import argparse, hashlib, html, json, os, re, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
RENDERER_ID = "factory-workflow-a3-v1"
EXTENSIONS = ("a3-lanes-v0", "a3-lanes-v0.1")
with open(HERE / "a3_inter_widths.json", encoding="utf-8") as _f:
    FW = json.load(_f)

# Within palette (brand system): Midnight anchors, Coral for deliberate moments, Cream/White canvas, tertiary for tints.
MID, CORAL, WHITE, CREAM = "#0B1E2F", "#F56740", "#FFFFFF", "#FBF6EF"
SUN, SKY, ROSE = "#FFF5D2", "#CFF3FE", "#FFE0D8"
CORAL_INK, GREY, LINE, ACCESS = "#B23A1E", "#5E6A75", "#D8CFC2", "#2E6E86"
FONT = "Inter, -apple-system, 'Segoe UI', Helvetica, Arial, sans-serif"
MONO = "ui-monospace, Menlo, monospace"

BANDS = ["people", "ai_and_agents", "system_of_record", "external"]
BAND_NAME = {"people": "PEOPLE", "ai_and_agents": "AI AND AGENTS", "system_of_record": "SYSTEM OF RECORD", "external": "EXTERNAL"}
BAND_FILL = {"people": WHITE, "ai_and_agents": "#EAF9FE", "system_of_record": "#FFFAE6", "external": "#FFF1EC"}
NONFLOW = {"asks_returns", "sets_state", "loop"}  # never set depth (lane standard + formats drafter)

# Geometry (user units = px at 100%)
HEAD_W = 184            # lane header column
BOX_W = 172             # task / AI / context box width
PAD = 10                # inner padding
T_TITLE, T_SUB, T_HEAD, T_LANE = 13, 10.5, 10, 12.5
CH_BASE, TRACK = 30, 10  # vertical channel base width between columns, per-track spacing (parallel runs >= 8 px apart)
SAFE = 1.12               # captions kept clear of lines are sized as a wider fallback font would set them
MIN_SEP = 8              # least gap between two parallel lines of different edges (ports, stubs)
GAP_BASE = 26           # horizontal gap between rows (holds a label pill)
LANE_PAD = 8
TIER_HEAD = 34          # strip above each part
TIER_GAP = 26
COLLAPSED_H = 30


# ---------------------------------------------------------------- text
def tw(s, size, weight=400, mono=False):
    if mono:
        return len(s) * FW["mono"]["w"] * size
    t = FW[str(weight)]
    return sum(t.get(ch, 0.62) for ch in s) * size


def conn_letter(i):
    """Label for the i-th (0-based) connector between parts: A..Z, then AA, AB, .. ZZ, AAA, .. (unbounded)."""
    out = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        out = chr(65 + r) + out
    return out


CONN_R, CONN_FS, CONN_PAD = 11, 11, 2  # connector circle radius, label font size, room kept inside the ring


def conn_shape(letter):
    """(radius, font size) for a connector circle so its label fits inside the ring.

    One letter keeps the original 11 px circle and 11 px label. Longer labels shrink the font (down to 8 px),
    then widen the circle once the font is at its floor."""
    inner = 2 * (CONN_R - CONN_PAD)
    if len(letter) == 1:
        return CONN_R, CONN_FS
    fs = CONN_FS
    while fs > 8 and tw(letter, fs, 700) > inner:
        fs -= 1
    w = tw(letter, fs, 700)
    r = CONN_R if w <= inner else int(-(-w // 2)) + CONN_PAD
    return r, fs


def wrap(s, size, weight, maxw, mono=False):
    """Lines of s that fit maxw, broken between words. A word wider than maxw breaks only just after a hyphen
    ("enrichment-" / "agent"); a part that still does not fit stays whole, so the caller sizes its box to the
    widest line. No word is cut anywhere else."""
    words, lines, cur = str(s).split(), [], ""
    for w in words:
        cand = (cur + " " + w) if cur else w
        if tw(cand, size, weight, mono) <= maxw:
            cur = cand
            continue
        if cur:
            lines.append(cur)
        cur = ""
        for part in re.findall(r"[^-]*-|[^-]+", w):
            if cur and tw(cur + part, size, weight, mono) > maxw:
                lines.append(cur); cur = ""
            cur += part
    if cur:
        lines.append(cur)
    return lines or [""]


def split_words(src, lines):
    """Words of src that lines (as wrap returned them) cut anywhere other than just after a hyphen."""
    toks = [t for ln in lines for t in ln.split()]
    j, bad = 0, []
    for w in str(src).split():
        rest = w
        while j < len(toks) and rest != toks[j] and toks[j].endswith("-") and rest.startswith(toks[j]):
            rest = rest[len(toks[j]):]; j += 1
        if j < len(toks) and rest == toks[j]:
            j += 1
        else:
            bad.append(w); break
    return bad


def wrap_box(b, s, size, weight, maxw, mono=False):
    """wrap(), recording the source and its lines on the box so the self-check can confirm no word was cut."""
    lines = wrap(s, size, weight, maxw, mono)
    b.wraps.append((str(s), lines))
    return lines


_EVENT_ATTR = re.compile(r"(?i)\b(on[a-z]+)(\s*)=")
_JS_URL = re.compile(r"(?i)(javascript)(\s*):")
_CSS_URL = re.compile(r"(?i)(url)\(")
_CSS_ESCAPE = re.compile(r"\\(?:([0-9a-fA-F]{1,6})(?:\r\n|[ \t\r\n\f])?|([\s\S]))")
_CONTROL_SPACE = re.compile(r"[\x00-\x20\x7f]+")
_SCRIPT_SCHEMES = ("java" + "script:", "vb" + "script:", "live" + "script:")  # split: no script URL in this source


def _css_decoded(text):
    """Text with CSS escapes decoded, as the no-script check reads every attribute value (\\75 -> u)."""
    def one(m):
        if m.group(1):
            code = int(m.group(1), 16)
            return "\ufffd" if code == 0 or code > 0x10FFFF or 0xD800 <= code <= 0xDFFF else chr(code)
        return "" if m.group(2) in "\r\n\f" else m.group(2)
    return _CSS_ESCAPE.sub(one, text)


def esc(s):
    """Escape for SVG/HTML text and attributes; also defuse event-attribute and javascript-URL look-alikes in
    bundle text with character references, so no written page carries either pattern even as text. The no-script
    page shape reads every attribute value as possible CSS and as a possible URL, so bundle text (an agent or
    system name) that would read as one is written with an invisible word joiner (U+2060): before the bracket of
    a literal url(, after each backslash when CSS escapes would spell url(, and in front of text that starts with
    a script scheme once spaces are dropped. The renderer's own marker URLs are not escaped."""
    s = _CSS_URL.sub("\\1\u2060(", str(s))
    if "\\" in s and _CSS_URL.search(_css_decoded(s)):
        s = s.replace("\\", "\\\u2060")
    t = html.escape(s, quote=True)
    t = _JS_URL.sub(r"\1\2&#58;", _EVENT_ATTR.sub(r"\1\2&#61;", t))
    return "\u2060" + t if _CONTROL_SPACE.sub("", s).lower().startswith(_SCRIPT_SCHEMES) else t


def title_text(s):
    """Escape text for a <title> (the page tab or an SVG tooltip). The no-script page shape allows no "<" in a
    <title>, even escaped, so "<" is written as the single angle quote \u2039, as the other renderers do."""
    return esc(str(s).replace("<", "\u2039"))


# ---------------------------------------------------------------- boxes
class Box:
    def __init__(self, node):
        self.n, self.id = node, node["id"]
        self.lines = []      # (text, size, weight, colour, mono, x_align) relative layout computed in size()
        self.kind = "task"
        self.badge = 0
        self.ring = False
        self.tips = []
        self.chips = []      # write-only states drawn on this box: (state node, unused)
        self.chip_y = []     # per chip: (top inside the box, height, wrapped lines)
        self.wraps = []      # (source text, wrapped lines) for every wrapped text in the box
        self.agent = None    # the `agent` tag of an agent step


def ws_label(w, sid):
    """A workspace's label: a chosen short_label, else its full name, whether or not it has a group (v0.1).
    The renderer never shortens a name itself; a long name wraps."""
    short = (w.get("short_label") or "").strip()
    return short or w.get("name") or sid


def tool_names(node, ws):
    seen, out = set(), []
    for sid in node.get("systems") or []:
        name = ws_label(ws.get(sid, {"name": sid}), sid)
        if name not in seen:
            seen.add(name); out.append(name)
    return out


def make_box(n, ws):
    b = Box(n)
    t = n.get("type")
    ev = n.get("evidence_state")
    b.dash = "6 4" if ev == "Assumed" else ("2 3" if ev == "needs_more_information" else "")
    b.ring = bool(n.get("captured_after_cutoff"))
    b.tips = [n.get("title", n["id"])]
    detail = n.get("detail")
    if isinstance(detail, str) and detail.strip():
        b.tips.append(detail)
    sub = n.get("subtask") or {}
    if sub.get("step_range"):
        b.tips.append("Steps: " + sub["step_range"])
    if ev:
        b.tips.append("Evidence: " + ev)
    if n.get("evidence_note"):
        b.tips.append(n["evidence_note"])
    if b.ring:
        b.tips.append("Captured after the snapshot cutoff.")
    L = []
    if t == "decision":
        b.kind = "decision"
    elif t == "start_event":
        b.kind = "start"
    elif t == "end_event":
        b.kind = "stop" if (n.get("stop") or {}).get("unresolved") else "end"
    elif t == "object_state":
        b.kind = "state"
    elif t == "ai_use":
        b.kind = "ai"
        a = n["ai_use"]
        b.failed = bool(a.get("failed"))
        b.weak = a.get("evidence_strength") not in (None, "step")
        if b.weak and not b.dash:
            b.dash = "5 4"
        L.append(((a.get("tool", "AI") + " · " + a.get("mode", "")).upper(), T_HEAD, 700, ACCESS, False))
        L.append((n["title"], 12.5, 600, MID, False))
        chk = (a.get("failure") if b.failed and a.get("failure") else a.get("checked_by", ""))
        if chk:
            L.append((("⚠ " if b.failed else "✓ ") + chk, T_SUB, 600 if b.failed else 400, CORAL_INK if b.failed else ACCESS, False))
        b.tips += ["Tool: " + a.get("tool", ""), "Mode: " + a.get("mode", ""), "Checked: " + a.get("checked_by", "")]
        if a.get("failure"):
            b.tips.append("Failure: " + a["failure"])
    elif t == "context_neighbour":
        b.kind = "context"
        nb = n.get("neighbour") or {}
        L.append(("OUTSIDE THE SET · " + str(nb.get("touches", "context")).upper(), T_HEAD, 700, GREY, False))
        L.append((n["title"], 12, 500, MID, False))
        keys = [k for k in [nb.get("process_id", "")] + list(nb.get("also") or []) if k]
        if keys:  # process ids go in the tooltip, never in the box text
            b.tips.append("Process: " + " · ".join(str(k) for k in keys))
    elif t == "message":
        b.kind = "message"
    elif t in ("read_interaction", "write_interaction") or n.get("subtask"):
        b.kind = "task"
        L.append((n["title"], T_TITLE, 600, MID, False))
        tools = tool_names(n, ws)
        if tools:
            L.append(("Tools: " + " · ".join(tools), T_SUB, 400, GREY, False))
        if n.get("writes"):
            L.append(("Writes: " + ", ".join(w.get("record", "") for w in n["writes"]), T_SUB, 600, ACCESS, False))
        pc = n.get("person_check")
        if pc:
            L.append(("Person check: " + pc.get("role", "") + " " + pc.get("what", ""), T_SUB, 600, CORAL_INK, False))
        rec = n.get("recovery") or []
        b.badge = len(rec)
        if rec:
            b.tips.append("Recoveries and exceptions:\n- " + "\n- ".join(r.get("label", r.get("id", "")) for r in rec))
        if tools:
            b.tips.append("Tools: " + ", ".join(tools))
    else:
        b.kind = "generic"
        L.append((n.get("title", n["id"]), 12, 500, MID, False))
        L.append((t or "node", T_SUB, 400, GREY, True))
    if n.get("agent"):  # an agent step of any type: its tag heads the box, as the tool heads an AI use
        b.agent = str(n["agent"])
        L.insert(0, ("AGENT · " + b.agent, T_HEAD, 700, ACCESS, False))
        b.tips.append("Agent: " + b.agent)
    b.spec = L
    size_box(b)
    return b


def size_box(b):
    n = b.n
    inner = BOX_W - 2 * PAD
    b.wraps = []
    if b.kind in ("task", "ai", "context", "generic"):
        out, y, w_ = [], PAD + 2, BOX_W  # the box widens only for a word that cannot break to fit
        for i, (s, sz, wt, col, mono) in enumerate(b.spec):
            mw = inner - (22 if (i == 0 and b.badge) else 0) - (10 if (i == 0 and b.ring) else 0)
            for ln in wrap_box(b, s, sz, wt, mw, mono):
                y += sz * 1.28
                out.append((ln, sz, wt, col, mono, PAD, y - sz * 0.28))
                w_ = max(w_, tw(ln, sz, wt, mono) + BOX_W - mw)
            y += 3 if i == 0 else 1
        b.chip_y = []
        for st, _ in b.chips:
            lines = wrap_box(b, chip_text(st), CHIP_T, 500, inner - 12)
            ch = len(lines) * CHIP_T * 1.28 + 6
            b.chip_y.append((y + 3, ch, lines)); y += ch + 4
            w_ = max([w_] + [tw(l, CHIP_T, 500) + BOX_W - inner + 12 for l in lines])
        b.text = out
        b.w, b.h = w_, max(56, y + PAD - 2)
    elif b.kind == "decision":
        best = None
        for wch in (90, 104, 118, 132, 146):
            lines = wrap(n["title"], 12, 600, wch)
            tw_ = max(tw(l, 12, 600) for l in lines); th = len(lines) * 15
            W, H = 2 * tw_ + 24, 2 * th + 22
            if W <= BOX_W + 8 and (best is None or W * H < best[0]):
                best = (W * H, W, H, lines)
        if best is None:  # a word too wide for every width: the diamond grows to the widest line
            lines = wrap(n["title"], 12, 600, 80); th = len(lines) * 15; mx = max(tw(l, 12, 600) for l in lines)
            best = (0, BOX_W if mx <= 80 else max(BOX_W, 2 * mx + 24), 2 * th + 22, lines)
        _, W, H, lines = best
        b.wraps.append((str(n["title"]), lines))
        rows = [(l, 12, 600, MID) for l in lines]
        if b.agent:  # the agent tag sits above the question; the diamond grows to keep both inside
            tag = wrap_box(b, "AGENT · " + b.agent, T_HEAD, 700, (BOX_W + 8 - 24) / 2)
            rows = [(l, T_HEAD, 700, ACCESS) for l in tag] + rows
            W = max(W, 2 * max(tw(l, s_, w_) for l, s_, w_, _ in rows) + 24)
            H = max(H, 2 * len(rows) * 15 + 22)
        W, H = max(W, 96), max(H, 64)
        b.w, b.h = W, H
        y0 = H / 2 - (len(rows) - 1) * 15 / 2 + 4.2
        b.text = [(l, sz, wt, c, False, "c", y0 + i * 15) for i, (l, sz, wt, c) in enumerate(rows)]
    else:  # start, end, stop, state, message: rounded single block
        icon = {"start": "▸ ", "end": "", "stop": "× ", "state": "", "message": "✉ "}[b.kind]
        sz, wt = 12, 500
        col = CORAL_INK if b.kind == "stop" else MID
        rows = [(l, sz, wt, col) for l in wrap_box(b, icon + n["title"], sz, wt, BOX_W - 28)]
        if b.agent:
            rows = [(l, T_HEAD, 700, ACCESS) for l in wrap_box(b, "AGENT · " + b.agent, T_HEAD, 700, BOX_W - 28)] + rows
        tw_ = max(tw(l, s_, w_) for l, s_, w_, _ in rows)
        b.w = max(110, min(BOX_W, tw_ + 30)) if tw_ <= BOX_W - 28 else tw_ + 30  # wider only for an unbreakable word
        b.h = len(rows) * 15 + 18
        y0 = b.h / 2 - (len(rows) - 1) * 15 / 2 + 4.2
        b.text = [(l, s_, w_, c, False, "c", y0 + i * 15) for i, (l, s_, w_, c) in enumerate(rows)]


CHIP_T = 10.5
BOXED = ("read_interaction", "write_interaction", "ai_use", "context_neighbour")


def chip_text(st):
    tag = (" · AGENT · " + str(st["agent"])) if st.get("agent") else ""  # a chipped state keeps its agent tag
    return "Sets: " + st.get("title", st["id"]) + tag


def outcome_ids(g):
    out = set()
    for o in g.get("outcomes") or []:
        if isinstance(o, dict):
            out |= {o[k] for k in ("id", "node", "node_id", "state") if isinstance(o.get(k), str)}
        elif isinstance(o, str):
            out.add(o)
    return out


def write_only_states(g):
    """{state id: writer id} for the states drawn as a "Sets: ..." chip on their writer's box (v0.1): an
    object_state with exactly one way in, a sets_state edge from one boxed step, and nothing out. It stays a
    pill when another process reads it (any edge out), a handoff passes through it, or it is an end (outcomes
    or an end flag)."""
    nodes = {n["id"]: n for n in g["nodes"]}
    ends = outcome_ids(g)
    chips = {}
    for nid, n in nodes.items():
        if n.get("type") != "object_state" or nid in ends or n.get("terminal") or (n.get("object_state") or {}).get("terminal"):
            continue
        ins = [e for e in g["edges"] if e["target"] == nid]
        outs = [e for e in g["edges"] if e["source"] == nid]
        if outs or len(ins) != 1 or ins[0]["kind"] != "sets_state" or ins[0]["source"] == nid:
            continue
        w = nodes.get(ins[0]["source"])
        if w and (w.get("type") in BOXED or (w.get("subtask") and w.get("type") not in
                                              ("decision", "start_event", "end_event", "object_state", "message"))):
            chips[nid] = w["id"]
    return chips


def replaced_ai_uses(g):
    """{node or edge id: reason} for what is not drawn: each ai_use node named in an agent step's future.from with
    change "assigned" (its work now sits with that agent), and every edge that touches one."""
    nodes = {n["id"]: n for n in g["nodes"]}
    out = {}
    for n in g["nodes"]:
        fut = n.get("future") or {}
        if not n.get("agent") or fut.get("change") != "assigned":
            continue
        for fid in fut.get("from") or []:
            if fid != n["id"] and (nodes.get(fid) or {}).get("type") == "ai_use" and fid not in out:
                out[fid] = "replaced AI use: assigned to agent step %s (agent %s)" % (n["id"], n["agent"])
    for e in g["edges"]:
        for end in (e["source"], e["target"]):
            if end in out and e["id"] not in out:
                out[e["id"]] = "edge of a replaced AI use"
    return out


def lane_sort_key(g, first):
    """Bands fixed. Inside a band, lanes with a v0.1 `order` come first, by order; the rest follow, people
    lanes by first appearance in spine order, other lanes in file order."""
    def key(l):
        auto = first.get(l["id"], 10 ** 6) if l["band"] == "people" else g["lanes"].index(l)
        return (BANDS.index(l["band"]), (0, l["order"], auto) if isinstance(l.get("order"), int) else (1, 0, auto))
    return key


# ---------------------------------------------------------------- layout
def split_groups(g, nodes, hidden):
    """Map each drawn split part to its group's first node (file order). Refuses a split_pair naming a node that is
    missing, hidden or not drawn as a box, since both parts must sit in one column."""
    order_ix = {n["id"]: i for i, n in enumerate(g["nodes"])}
    parent = {}
    def find(x):
        while parent.setdefault(x, x) != x:
            x = parent[x]
        return x
    for n in g["nodes"]:
        pair = (n.get("future") or {}).get("split_pair") or []
        if not pair or n["id"] not in nodes:
            continue
        for o in pair:
            if o not in nodes:
                why = "is hidden" if o in hidden else ("is not drawn as a box" if o in order_ix else "does not exist")
                raise SystemExit("split %s pairs with %s, which %s; both parts of a split sit in one column"
                                 % (n["id"], o, why))
            a, z = sorted((find(n["id"]), find(o)), key=order_ix.get)
            parent[z] = a
    return {x: find(x) for x in parent}


def spanning_steps(g, nodes):
    """Map each drawn node with future.spans to the frame ids it spans (file order kept, repeats dropped).
    Refuses a span naming no process frame."""
    frames = [f["process_id"] for f in g.get("process_frames", [])]
    out = {}
    for nid, n in nodes.items():
        ps = (n.get("future") or {}).get("spans") or []
        if not ps:
            continue
        bad = [p for p in ps if p not in frames]
        if bad:
            raise SystemExit("step %s spans %s, which is not a process frame" % (nid, ", ".join(bad)))
        out[nid] = list(dict.fromkeys(ps))
    return out


def cycle_members(succ, among):
    """{node: cycle number} for the nodes of AMONG that lie on a cycle of SUCC (node -> [successor, ...]): every
    strongly connected component with more than one node, numbered. Nodes that only follow a cycle are left out.
    Iterative Tarjan."""
    index, low, on, stack, out, n = {}, {}, set(), [], {}, 0
    for root in among:
        if root in index:
            continue
        work = [(root, iter(succ.get(root, [])))]
        index[root] = low[root] = n; n += 1; stack.append(root); on.add(root)
        while work:
            v, it = work[-1]
            for w in it:
                if w not in among:
                    continue
                if w not in index:
                    index[w] = low[w] = n; n += 1; stack.append(w); on.add(w)
                    work.append((w, iter(succ.get(w, []))))
                    break
                if w in on:
                    low[v] = min(low[v], index[w])
            else:
                work.pop()
                if work:
                    low[work[-1][0]] = min(low[work[-1][0]], low[v])
                if low[v] == index[v]:
                    comp = []
                    while True:
                        w = stack.pop(); on.discard(w); comp.append(w)
                        if w == v:
                            break
                    if len(comp) > 1:
                        k = len(set(out.values()))
                        out.update((w, k) for w in comp)
    return out


def layout(B, budget):
    g = B["graph"]
    ws = {w["id"]: w for w in g.get("workspaces", [])}
    lanes = {l["id"]: l for l in g["lanes"]}
    notes = []
    for n in g["nodes"]:
        if n.get("lane_id") not in lanes:
            raise SystemExit("node %s has no A3 lane (lane_id)" % n["id"])
    ai_lanes = [l["id"] for l in g["lanes"] if l["band"] == "ai_and_agents"]
    if len(ai_lanes) > 1:
        raise SystemExit("more than one AI and agents lane (%s); there is one, and agents are told apart by their "
                         "agent tag" % ", ".join(ai_lanes))
    for n in g["nodes"]:
        if n.get("agent") and lanes[n["lane_id"]]["band"] != "ai_and_agents":
            raise SystemExit("agent step %s (agent %s) is outside the AI and agents lane" % (n["id"], n["agent"]))
    # Replaced AI uses and their edges leave the drawing; layout.json lists them in hidden with the reason.
    hidden = replaced_ai_uses(g)
    vis = dict(g, nodes=[n for n in g["nodes"] if n["id"] not in hidden],
               edges=[e for e in g["edges"] if e["id"] not in hidden])
    # Write-only states leave the layout: each is drawn as a chip on its writer's box, and its sets_state edge
    # goes to that box. The data keeps both.
    chips = write_only_states(vis)
    marked = any(e.get("spine") is True for e in vis["edges"])  # before chip edges leave, so their marks count
    nodes = {n["id"]: n for n in vis["nodes"] if n["id"] not in chips}
    chip_edges = [e for e in vis["edges"] if e["target"] in chips]
    edges = [e for e in vis["edges"] if e["target"] not in chips]
    out_e, in_e = {}, {}
    for e in edges:
        out_e.setdefault(e["source"], []).append(e); in_e.setdefault(e["target"], []).append(e)
    split_of = split_groups(g, nodes, hidden)
    spans = spanning_steps(g, nodes)

    # 1. Columns: longest path over spine edges. Non-flow kinds never set depth. A context edge sets depth only
    #    when it is the node's only way in (so a context neighbour on the spine still gets its own column).
    def counts(e):
        if marked:  # v0.1: when any edge is marked, only marked edges set columns
            return e.get("spine") is True and e["kind"] not in NONFLOW
        if e["kind"] in NONFLOW:
            return False
        if e.get("context"):
            return not any((not x.get("context")) and x["kind"] not in NONFLOW for x in in_e.get(e["target"], []))
        return True
    spine = [e for e in edges if counts(e)]
    zero = [e for e in edges if e["kind"] in ("asks_returns", "sets_state")]  # same column as the step, never deeper
    # Both parts of a split share one column: columns are computed on split groups, and an edge between two parts
    # of one split sets no depth (it is drawn as a drop inside the column).
    rep_of = {nid: split_of.get(nid, nid) for nid in nodes}
    succ, indeg = {}, {nid: 0 for nid in nodes if rep_of[nid] == nid}
    for e, w in [(e, 1) for e in spine] + [(e, 0) for e in zero]:
        a, z = rep_of[e["source"]], rep_of[e["target"]]
        if a == z:
            continue
        succ.setdefault(a, []).append((z, w)); indeg[z] += 1
    order_ix = {n["id"]: i for i, n in enumerate(g["nodes"])}
    col = {nid: 0 for nid in indeg}
    q = sorted([nid for nid, d in indeg.items() if d == 0], key=order_ix.get)
    seen, deg = [], dict(indeg)
    while q:
        v = q.pop(0); seen.append(v)
        for t, w in succ.get(v, []):
            col[t] = max(col[t], col[v] + w); deg[t] -= 1
            if deg[t] == 0:
                q.append(t)
    if len(seen) != len(indeg):
        on_cycle = cycle_members({a: [z for z, _ in ts] for a, ts in succ.items()}, set(indeg) - set(seen))
        cyc = [n for n in nodes if rep_of[n] in on_cycle]
        tied = [n for n in cyc if n in split_of]
        if tied:
            raise SystemExit("split parts %s lie on one path through %s, so they cannot share a column; a split's "
                             "parts are alternatives side by side, not steps in sequence" % (tied, cyc))
        raise SystemExit("spine has a cycle through %s; mark the return edge kind 'loop'" % cyc)
    # Every flow edge, not only the ones that set columns, must be free of unmarked cycles: a context edge back
    # to a step that has another way in sets no column, but it still closes a loop that must be marked. This holds
    # in both modes: when the spine is marked (v0.1), an unmarked return edge sets no column but still closes a cycle.
    # A flow edge from a node to itself is a cycle too; an edge between two parts of one split is not (skipped below).
    selfs = [e for e in edges if e["kind"] not in NONFLOW and e["source"] == e["target"]]
    if selfs:
        raise SystemExit("sequence has a cycle through %s, closed by edge(s) %s that set no column; mark the "
                         "return edge kind 'loop'" % (sorted({e["source"] for e in selfs}),
                                                      ", ".join(e["id"] for e in selfs)))
    seq_succ, seq_deg = {}, {nid: 0 for nid in indeg}
    for e in edges:
        a, z = rep_of[e["source"]], rep_of[e["target"]]
        if e["kind"] in NONFLOW or a == z:
            continue
        seq_succ.setdefault(a, []).append(z); seq_deg[z] += 1
    q = [nid for nid, d in seq_deg.items() if d == 0]
    done = set(q)
    while q:
        for t in seq_succ.get(q.pop(), []):
            seq_deg[t] -= 1
            if seq_deg[t] == 0:
                done.add(t); q.append(t)
    if len(done) != len(seq_deg):
        on_cycle = cycle_members(seq_succ, set(seq_deg) - done)  # not the nodes that only follow the cycle
        cyc = [n for n in nodes if rep_of[n] in on_cycle]
        closing = [e["id"] for e in edges if e["kind"] not in NONFLOW and e not in spine
                   and rep_of[e["source"]] in on_cycle
                   and on_cycle[rep_of[e["source"]]] == on_cycle.get(rep_of[e["target"]])]
        raise SystemExit("sequence has a cycle through %s, closed by edge(s) %s that set no column; mark the "
                         "return edge kind 'loop'" % (cyc, ", ".join(closing)))
    # A node with nothing coming in sits just left of its first successor.
    for nid in reversed(seen):
        if indeg[nid] == 0 and succ.get(nid):
            col[nid] = max(col[nid], min(col[t] - w for t, w in succ[nid]))
    for nid in nodes:
        col[nid] = col[rep_of[nid]]
    # AI boxes share their person box's column; spread them to a free neighbouring column before stacking.
    # When that spread leaves a spine edge not running left to right, it is redone with a guard: a box moves only
    # to a column that keeps every spine edge in and out of it left to right, else it keeps its column and stacks.
    # Edges between parts of one split set no column.
    sp = [(e["source"], e["target"]) for e in spine if rep_of[e["source"]] != rep_of[e["target"]]]
    sp_in, sp_out = {}, {}
    for a, z in sp:
        sp_out.setdefault(a, []).append(z); sp_in.setdefault(z, []).append(a)
    def keeps_order(nid, c):
        return all(col[x] < c for x in sp_in.get(nid, [])) and all(col[x] > c for x in sp_out.get(nid, []))
    sat_lanes = {l["id"] for l in g["lanes"] if l["band"] == "ai_and_agents"}
    col0 = dict(col)
    for guard in (False, True):
        col.update(col0)
        for lid in sat_lanes:
            # a split part keeps its partner's column; the others spread around the columns it holds
            used = {col[x]: x for x in nodes if nodes[x]["lane_id"] == lid and x in split_of}
            for nid in sorted([x for x in nodes if nodes[x]["lane_id"] == lid and x not in split_of],
                              key=lambda x: (col[x], order_ix[x])):
                for c in (col[nid], col[nid] + 1, col[nid] - 1, col[nid] + 2):
                    if c >= 0 and c not in used and (not guard or keeps_order(nid, c)):
                        col[nid] = c; used[c] = nid; break
        if all(col[a] < col[z] for a, z in sp):
            break
    # 2. Lane order: bands fixed; people lanes by first appearance in spine order; others keep file order.
    first = {}
    for i, nid in enumerate(sorted(nodes, key=lambda x: (col[x], order_ix[x]))):
        first.setdefault(nodes[nid]["lane_id"], i)
    lane_list = sorted(g["lanes"], key=lane_sort_key(g, first))
    if not any(l["band"] == "ai_and_agents" for l in lane_list):
        raise SystemExit("no AI and agents lane (the lane standard requires one)")
    lane_ix = {l["id"]: i for i, l in enumerate(lane_list)}

    all_nodes = {n["id"]: n for n in g["nodes"]}
    boxes = {}
    for nid, n in nodes.items():
        b = make_box(n, ws)
        hosted = [all_nodes[s] for s in sorted(chips, key=order_ix.get) if chips[s] == nid]
        if hosted:
            b.chips = [(st, None) for st in hosted]
            b.tips += [chip_text(st) for st in hosted]
            size_box(b)
        boxes[nid] = b
    ncol = max(col.values()) + 1
    colw = [40] * ncol
    for nid, b in boxes.items():
        colw[col[nid]] = max(colw[col[nid]], b.w)

    # 3. Tiers: split columns into parts that fit the budget, preferring cuts few edges cross and no frame straddles.
    lab_room = {}  # column -> room a label between it and the next column takes beside the channel's tracks
    for e in edges:
        if e.get("display_label") and col[e["target"]] == col[e["source"]] + 1:
            lab_room[col[e["source"]]] = max(lab_room.get(col[e["source"]], 0), tw(e["display_label"], 10.5, 500) + 22)
    def width_of(a, z):  # columns a..z-1
        return (HEAD_W + 16 + sum(colw[a:z]) + (z - a + 1) * (CH_BASE + 2 * TRACK) + 24
                + sum(lab_room.get(c, 0) for c in range(a, z - 1)))
    proc = {nid: nodes[nid].get("process_id") for nid in nodes}
    # A cross-process step belongs to the frames it spans: its own process when that is one of them, else the first.
    for nid, ps in spans.items():
        if proc[nid] not in ps:
            proc[nid] = ps[0]
    def cut_cost(c):  # cut before column c
        cost = 0
        for e in edges:
            a, z = col[e["source"]], col[e["target"]]
            if min(a, z) < c <= max(a, z):
                cost += 1
        for p in {v for v in proc.values() if v}:
            cs = [col[n] for n in nodes if proc[n] == p]
            if min(cs) < c <= max(cs):
                cost += 4
        return cost
    if budget and budget > 0:
        INF = (10 ** 9, 10 ** 9)
        dp = [INF] * (ncol + 1); prev = [0] * (ncol + 1); dp[0] = (0, 0)
        for z in range(1, ncol + 1):
            for a in range(z):
                if dp[a] == INF or width_of(a, z) > budget:
                    continue
                cand = (dp[a][0] + 1, dp[a][1] + (cut_cost(a) if a else 0))
                if cand < dp[z]:
                    dp[z], prev[z] = cand, a
        if dp[ncol] == INF:
            raise SystemExit("a single column is wider than the budget")
        cuts, z = [], ncol
        while z:
            cuts.append((prev[z], z)); z = prev[z]
        tiers = list(reversed(cuts))
    else:
        tiers = [(0, ncol)]
    tier_of = {nid: next(i for i, (a, z) in enumerate(tiers) if a <= col[nid] < z) for nid in nodes}

    # 4. Rows inside each lane, per tier: pack process frames as blocks; free nodes take the first free cell.
    row = {}
    lane_rows = {}  # (tier, lane) -> number of rows
    frames_meta = {f["process_id"]: f for f in g.get("process_frames", [])}
    for ti, (a, z) in enumerate(tiers):
        for l in lane_list:
            members = [nid for nid in nodes if tier_of[nid] == ti and nodes[nid]["lane_id"] == l["id"]]
            occ = []  # occ[r] = set of columns used
            def free(r, cs):
                while len(occ) <= r:
                    occ.append(set())
                return not (occ[r] & cs)
            # The frames a cross-process step spans pack as one block in its lane, so each named frame can be
            # drawn round the step without taking in another frame's rows.
            pack = {}
            def root(p):
                while pack.get(p, p) != p:
                    p = pack[p]
                return p
            for nid in members:
                for q in spans.get(nid, [])[1:]:
                    a_, z_ = root(spans[nid][0]), root(q)
                    if a_ != z_:
                        pack[z_] = a_
            blocks = {}
            for nid in members:
                p = proc[nid]
                if p:
                    blocks.setdefault(root(p), []).append(nid)
            # frames in order of their first column, then file order
            for p, ms in sorted(blocks.items(), key=lambda kv: (min(col[m] for m in kv[1]), min(order_ix[m] for m in kv[1]))):
                lo, hi = min(col[m] for m in ms), max(col[m] for m in ms)
                span = set(range(lo, hi + 1))
                bycol = {}
                for m in sorted(ms, key=lambda m: order_ix[m]):
                    bycol.setdefault(col[m], []).append(m)
                h = max(len(v) for v in bycol.values())
                r0 = 0
                while not all(free(r0 + k, span) for k in range(h)):
                    r0 += 1
                for k in range(h):
                    occ[r0 + k] |= span
                for c, ms_ in bycol.items():
                    for k, m in enumerate(ms_):
                        row[m] = r0 + k
            for nid in sorted([m for m in members if not proc[m]], key=lambda m: (col[m], order_ix[m])):
                # prefer the row of a neighbour already placed in this lane, else the first free one
                pref = [row[x] for e in in_e.get(nid, []) + out_e.get(nid, []) for x in (e["source"], e["target"])
                        if x != nid and x in row and nodes[x]["lane_id"] == l["id"] and tier_of[x] == ti]
                cands = sorted(set(pref)) + list(range(0, len(occ) + 1))
                for r in cands:
                    if free(r, {col[nid]}):
                        occ[r].add(col[nid]); row[nid] = r; break
            lane_rows[(ti, l["id"])] = max([row[m] for m in members], default=-1) + 1
    return dict(g=g, nodes=nodes, edges=edges, boxes=boxes, col=col, row=row, colw=colw, tiers=tiers, tier_of=tier_of,
                lane_list=lane_list, lane_ix=lane_ix, lane_rows=lane_rows, frames_meta=frames_meta, spine=spine,
                proc=proc, notes=notes, ws=ws, chips=chips, chip_edges=chip_edges, all_nodes=all_nodes, hidden=hidden,
                split_of=split_of, spans=spans)


# ---------------------------------------------------------------- routing + geometry
def build(L, budget):
    g, nodes, boxes, col, row = L["g"], L["nodes"], L["boxes"], L["col"], L["row"]
    tiers, tier_of, lane_list = L["tiers"], L["tier_of"], L["lane_list"]
    edges = L["edges"]
    lane_ix = L["lane_ix"]

    # Global row index inside a tier: rows in lane order (collapsed lanes still count as one slot).
    def rowkey(nid):
        return (lane_ix[nodes[nid]["lane_id"]], row[nid])

    # --- abstract routes
    routes = {}
    conn = []  # off-part connectors
    grp = {}
    for e in edges:
        s, t = e["source"], e["target"]
        ts, tt = tier_of[s], tier_of[t]
        cs, ct = col[s], col[t]
        if s == t:
            routes[e["id"]] = {"kind": "self"}
            continue
        if ts != tt:
            key = (s, tt)
            if key not in grp:
                grp[key] = conn_letter(len(grp))
            routes[e["id"]] = {"kind": "split", "letter": grp[key], "group": key}
            conn.append((e["id"], grp[key]))
            continue
        same_row = rowkey(s) == rowkey(t)
        if ct > cs:
            clear = same_row and not any(tier_of[x] == ts and rowkey(x) == rowkey(s) and cs < col[x] < ct for x in nodes)
            if clear:
                routes[e["id"]] = {"kind": "straight"}
            elif ct == cs + 1:
                routes[e["id"]] = {"kind": "jog", "ch": cs}
            else:
                routes[e["id"]] = {"kind": "gap", "ch1": cs, "ch2": ct - 1}
        elif ct == cs:
            # vertical drop when nothing sits between them in this column, else via the channel to the right
            lo, hi = sorted([rowkey(s), rowkey(t)])
            between = any(tier_of[x] == ts and col[x] == cs and lo < rowkey(x) < hi for x in nodes)
            routes[e["id"]] = {"kind": "drop"} if not between else {"kind": "side", "ch": cs}
        else:
            routes[e["id"]] = {"kind": "back", "ch1": cs, "ch2": ct - 1}
    return routes, conn


def render(B, budget, stem):
    L = layout(B, budget)
    routes, conn = build(L, budget)
    g, nodes, boxes, col, row = L["g"], L["nodes"], L["boxes"], L["col"], L["row"]
    tiers, tier_of, lane_list, colw = L["tiers"], L["tier_of"], L["lane_list"], L["colw"]
    edges, lane_ix = L["edges"], L["lane_ix"]
    E = {e["id"]: e for e in edges}

    # ---- geometry per tier (two passes: tracks first, then coordinates)
    # vertical channel c sits right of column c (c = -1 is left of the first column of the tier)
    # horizontal gap is identified by (tier, slot) where slot is the boundary above global row index k
    def rows_of_tier(ti):
        out = []
        for l in lane_list:
            n = L["lane_rows"][(ti, l["id"])]
            out += [(l["id"], r) for r in range(max(n, 1))]
        return out
    tier_rows = {ti: rows_of_tier(ti) for ti in range(len(tiers))}
    def slot(nid):
        return tier_rows[tier_of[nid]].index((nodes[nid]["lane_id"], row[nid]))

    # Choose the horizontal gap for 'gap'/'back'/'split' routes: the boundary next to the target row facing the source.
    vreq, hreq = {}, {}
    def vadd(ti, ch, a, z, key):
        vreq.setdefault((ti, ch), []).append((key, min(a, z), max(a, z)))
    def hadd(ti, gp, a, z, key):
        hreq.setdefault((ti, gp), []).append((key, min(a, z), max(a, z)))
    for eid, r in routes.items():
        e = E[eid]; s, t = e["source"], e["target"]
        ti = tier_of[s]
        if r["kind"] in ("jog", "side"):
            vadd(ti, r["ch"], 2 * slot(s) + 1, 2 * slot(t) + 1, eid)
        elif r["kind"] in ("gap", "back"):
            ss, st = slot(s), slot(t)
            gp = st if ss <= st else st + 1   # boundary above target (source above/same) or below target (source below)
            if r["kind"] == "back":
                gp = max(ss, st) + 1          # run underneath both rows
            r["gp"] = gp
            vadd(ti, r["ch1"], 2 * ss + 1, 2 * gp, eid + ":a")
            vadd(ti, r["ch2"], 2 * gp, 2 * st + 1, eid + ":b")
            hadd(ti, gp, r["ch1"], r["ch2"], eid)
        elif r["kind"] == "split":
            tt = tier_of[t]; ss, st = slot(s), slot(t)
            r["gp_out"], r["gp_in"] = ss + 1, st
            last = tiers[ti][1] - 1; firstc = tiers[tt][0] - 1
            r["ch_out"], r["ch_in"] = col[s], col[t] - 1
            gk = "%s>%d" % r["group"]
            members = [E[x]["target"] for x, rr in routes.items() if rr.get("group") == r["group"]]
            r["gp_in"] = min(slot(m) for m in members)
            vadd(ti, col[s], 2 * ss + 1, 2 * (ss + 1), gk + ":a")
            hadd(ti, ss + 1, col[s], last + 1, gk + ":o")
            vadd(tt, col[t] - 1, 2 * r["gp_in"], 2 * st + 1, eid + ":b")
            hadd(tt, r["gp_in"], firstc - 1, col[t] - 1, gk + ":i")
    def assign(reqs):
        out, n = {}, {}
        for k, items in reqs.items():
            tracks = []
            for key, lo, hi in sorted(items, key=lambda x: (x[1], x[2], x[0])):
                for i, sp in enumerate(tracks):
                    if all(hi < a or lo > b for a, b in sp):
                        sp.append((lo, hi)); out[(k, key)] = i; break
                else:
                    tracks.append([(lo, hi)]); out[(k, key)] = len(tracks) - 1
            n[k] = len(tracks)
        return out, n
    vtrack, vcount = assign(vreq)
    htrack, hcount = assign(hreq)

    need, room = {}, {}  # (tier, channel) -> least width; label room kept left of the channel's tracks
    for eid, r in routes.items():
        e = E[eid]
        lab = e.get("display_label")
        if lab and r["kind"] in ("straight", "jog") and col[e["target"]] == col[e["source"]] + 1:
            k = (tier_of[e["source"]], col[e["source"]])
            # room for the label beside the channel's tracks, not on them: the bundle moves right of a label width
            n_ = vcount.get(k, 0)
            if n_ <= 1 and r["kind"] == "straight":
                need[k] = max(need.get(k, 0), tw(lab, 10.5, 500) + 34)
            else:
                room[k] = max(room.get(k, 0), tw(lab, 10.5, 500) + 14 + 8)
                need[k] = max(need.get(k, 0), room[k] + max(0, n_ - 1) * TRACK + 20)
    n_out, n_in = {}, {}
    for eid, r in routes.items():
        if r["kind"] == "split":
            n_out.setdefault(tier_of[E[eid]["source"]], set()).add(r["group"])
            n_in.setdefault(tier_of[E[eid]["target"]], set()).add(r["group"])
    # ---- x coordinates per tier
    X = {}  # (tier, col) -> (left, right)
    CHX = {}  # (tier, ch) -> (x0, width)
    tier_w = {}
    for ti, (a, z) in enumerate(tiers):
        x = HEAD_W + 8
        for c in range(a - 1, z):
            if c >= a:
                X[(ti, c)] = (x, x + colw[c]); x += colw[c]
            w = CH_BASE + max(0, vcount.get((ti, c), 0) - 1) * TRACK + (TRACK if vcount.get((ti, c), 0) else 0)
            w = max(w, need.get((ti, c), 0))
            if c == z - 1:  # room for off-part connectors on the right, beside the routing tracks
                w = max(w, 46) + (26 * (len(n_out.get(ti, ())) - 1) if n_out.get(ti) else 0)
            if c == a - 1:
                w = max(w, 40) + (26 * (len(n_in.get(ti, ())) - 1) if n_in.get(ti) else 0)
            CHX[(ti, c)] = (x, w); x += w
        tier_w[ti] = x + 8
    W = max(tier_w.values())

    # A box side that many lines leave or enter grows the box, so its ports stay MIN_SEP apart.
    side_n = {}
    for eid, r in routes.items():
        if r["kind"] in ("self", "drop"):
            continue
        e = E[eid]
        for nid, sd in ((e["source"], "right"), (e["target"], "right" if r["kind"] == "side" else "left")):
            side_n[(nid, sd)] = side_n.get((nid, sd), 0) + 1
    for (nid, sd), n_ in sorted(side_n.items()):
        b = boxes[nid]
        if b.kind != "decision" and (n_ - 1) * MIN_SEP + 16 > b.h:
            dh = (n_ - 1) * MIN_SEP + 16 - b.h
            b.h += dh
            if b.kind in ("start", "end", "stop", "state", "message"):  # centred text stays centred
                b.text = [t_[:6] + (t_[6] + dh / 2,) for t_ in b.text]
    # ---- y coordinates per tier
    Y = {}       # (tier, slot) -> (top, bottom) of node row
    GY = {}      # (tier, gap boundary k) -> (y0, height)
    LANE_Y = {}  # (tier, lane) -> (top, bottom)
    TIER_Y = {}
    frame_label_h = {}
    frame_label_off = {}  # (tier, process, lane) -> (caption lines drawn above it in a shared box, lines in that box)
    # frames: label space above the first row a frame occupies in a lane
    def frame_rows(ti):
        out = {}
        for nid in nodes:
            p = L["proc"][nid]
            if p and tier_of[nid] == ti:
                # a cross-process step is drawn inside every frame it spans, in its own lane
                for q in dict.fromkeys([p] + (L.get("spans") or {}).get(nid, [])):
                    out.setdefault((q, nodes[nid]["lane_id"]), []).append(nid)
        return out
    def chx(ti, ch, key):
        x0, w = CHX[(ti, ch)]
        n = vcount.get((ti, ch), 1) or 1
        k = vtrack.get(((ti, ch), key), 0)
        rm = room.get((ti, ch), 0)
        return x0 + rm + (w - rm) / 2 + (k - (n - 1) / 2) * TRACK

    # A drop into the first row of a frame passes that frame's caption strip: a lone drop between two boxes (the
    # only line on those sides) then runs near their right ends, clear of the caption at the frame's left edge.
    drop_n = {}
    for eid, r in routes.items():
        if r["kind"] == "drop":
            e = E[eid]; down = slot(e["target"]) > slot(e["source"])
            for n_, sd in ((e["source"], down), (e["target"], not down)):
                drop_n[(n_, sd)] = drop_n.get((n_, sd), 0) + 1
    def lone_drop_x(eid, fr):
        e = E[eid]; s_, t_ = e["source"], e["target"]
        down = slot(t_) > slot(s_)
        lower = t_ if down else s_
        if (any(boxes[n_].kind == "decision" for n_ in (s_, t_)) or drop_n[(s_, down)] != 1
                or drop_n[(t_, not down)] != 1
                or not any(lower in ms and min(slot(m) for m in ms) == slot(lower) for ms in fr.values())):
            return None
        c0, c1 = X[(tier_of[lower], col[lower])]
        return (c0 + c1) / 2 + min(boxes[s_].w, boxes[t_].w) / 2 - 22

    def caption_blocks(ti, s0, fr):
        """x positions of the vertical runs that pass the caption strip above row slot s0 of tier ti: channel
        tracks reaching the row from the gap above, and drops in a column that cross into the row."""
        xs = []
        for (t_, ch), items in vreq.items():
            if t_ != ti:
                continue
            for key, lo, hi in items:
                if lo <= 2 * s0 < 2 * s0 + 1 <= hi:
                    xs.append((chx(ti, ch, key) - 4, chx(ti, ch, key) + 4))
        for eid, r in routes.items():
            if r["kind"] != "drop":
                continue
            e = E[eid]
            if tier_of[e["source"]] != ti:
                continue
            a_, z_ = sorted((slot(e["source"]), slot(e["target"])))
            if a_ < s0 <= z_:
                xx = lone_drop_x(eid, fr)
                c0, c1 = X[(ti, col[e["source"]])]
                xs.append((xx - 4, xx + 4) if xx is not None else ((c0 + c1) / 2 - 24, (c0 + c1) / 2 + 24))
        return xs
    frame_label_x = {}  # (tier, process, lane) -> left x of the caption, when it moves off the frame's left edge
    y = 12
    FR = {}
    for ti in range(len(tiers)):
        TIER_Y[ti] = [y, None]
        y += TIER_HEAD
        fr = frame_rows(ti); FR[ti] = fr
        top_extra = {}
        # frames of different processes in one lane that share a step (a step spanning both) are drawn over each other;
        # their captions stack, in frame order, instead of being drawn on top of each other
        same_box, comp = {}, {}
        for (p, lid), ms in fr.items():
            hit = [k for k, g in same_box.items() if k[0] == lid and set(ms) & g["steps"]]
            g = {"lane": lid, "steps": set(ms), "procs": [p]}
            for k in hit:
                h = same_box.pop(k)
                g["steps"] |= h["steps"]; g["procs"] = h["procs"] + g["procs"]
            key = (lid, len(comp)); comp[key] = True
            same_box[key] = g
        order = list(fr)
        group_of = {}
        for g in same_box.values():
            procs = sorted(g["procs"], key=lambda q: order.index((q, g["lane"])))
            for q in procs:
                group_of[(q, g["lane"])] = procs
        for (p, lid), ms in fr.items():
            s0 = min(slot(m) for m in ms)
            xs = [X[(ti, col[m])] for m in ms]
            fw = max(x1 for _, x1 in xs) - min(x0 for x0, _ in xs) + 20
            # a group no process frame declares (the set-level steps) is named for what it is, never by its id
            meta = L["frames_meta"].get(p, {"name": SET_LEVEL_NAME})
            first_lane = min(lane_ix[nodes[m]["lane_id"]] for m in fr_all(L, p))
            cont = tier_of[min(fr_all(L, p), key=lambda m: col[m])] != ti
            lab = frame_label(meta, p, cont and lane_ix[lid] == first_lane, lane_ix[lid] != first_lane)
            lines = wrap(lab, 10.5, 600, fw - 16)
            # A caption stays clear of the lines that cross its strip: it keeps the frame's left edge when that is
            # free, else moves (and wraps) into the free stretch of the strip that needs the fewest lines.
            bx0 = min(X[(ti, col[m])][0] + (X[(ti, col[m])][1] - X[(ti, col[m])][0] - boxes[m].w) / 2 for m in ms) - 10
            bx1 = max(X[(ti, col[m])][0] + (X[(ti, col[m])][1] - X[(ti, col[m])][0] + boxes[m].w) / 2 for m in ms) + 10
            blocks = sorted(caption_blocks(ti, s0, fr))
            def blocked(xa, xb):
                return any(b0 < xb and b1 > xa for b0, b1 in blocks)
            lo_x, hi_x = bx0 + 8, bx1 - 8
            # (captions stacked for frames that share a step keep the frame's left edge: they line up as one block)
            if len(group_of[(p, lid)]) == 1 and blocked(lo_x, lo_x + SAFE * max(tw(l, 10.5, 600) for l in lines)):
                free, cur = [], lo_x
                for b0, b1 in blocks:
                    if b0 > cur:
                        free.append((cur, min(b0, hi_x)))
                    cur = max(cur, b1)
                if cur < hi_x:
                    free.append((cur, hi_x))
                best = None
                for f0, f1 in free:
                    if f1 - f0 < 48:
                        continue
                    ls = wrap(lab, 10.5, 600, (f1 - f0 - 4) / SAFE)
                    if SAFE * max(tw(l, 10.5, 600) for l in ls) > f1 - f0 - 2:
                        continue
                    if best is None or len(ls) < len(best[1]):
                        best = (f0 + 2, ls)
                if best is not None:
                    frame_label_x[(ti, p, lid)], lines = best
            frame_label_h[(ti, p, lid)] = lines
        for (p, lid), ms in fr.items():
            s0 = min(slot(m) for m in ms)
            group = group_of[(p, lid)]
            before = sum(len(frame_label_h[(ti, q, lid)]) for q in group[:group.index(p)])
            total = sum(len(frame_label_h[(ti, q, lid)]) for q in group)
            frame_label_off[(ti, p, lid)] = (before, total)
            top_extra[s0] = max(top_extra.get(s0, 0), total * 13.5 + 8)
        rows = tier_rows[ti]
        prev_lane = None
        for k, (lid, r) in enumerate(rows):
            if lid != prev_lane:
                if prev_lane is not None:
                    # a frame reaches 8 px below its lowest box: the lane closes below it, so no frame runs into
                    # the next lane (over a collapsed lane's caption)
                    if any(lid_ == prev_lane for (_, lid_) in fr):
                        y += LANE_PAD + 2
                    y = max(y, LANE_Y[(ti, prev_lane)][0] + lane_header_h(L, ti, prev_lane))
                    LANE_Y[(ti, prev_lane)][1] = y
                LANE_Y[(ti, lid)] = [y, None]
                y += LANE_PAD
            gh = GAP_BASE + max(0, hcount.get((ti, k), 0) - 1) * TRACK if (k > 0 and rows[k - 1][0] == lid) else max(LANE_PAD, hcount.get((ti, k), 0) * TRACK + 6)
            if k > 0 and rows[k - 1][0] == lid:
                GY[(ti, k)] = (y, gh); y += gh
            else:
                # boundary at a lane edge: tracks run just inside the lane top
                GY[(ti, k)] = (y - LANE_PAD + 2, max(LANE_PAD, hcount.get((ti, k), 0) * TRACK + 4))
                y += max(0, hcount.get((ti, k), 0) * TRACK + 4 - LANE_PAD)
            y += top_extra.get(k, 0)
            members = [nid for nid in nodes if tier_of[nid] == ti and nodes[nid]["lane_id"] == lid and row[nid] == r]
            h = max([boxes[m].h for m in members], default=0)
            if not members and L["lane_rows"][(ti, lid)] == 0:
                h = COLLAPSED_H - 2 * LANE_PAD
            Y[(ti, k)] = (y, y + h)
            y += h
            prev_lane = lid
        # bottom boundary
        kb = len(rows)
        gh = max(LANE_PAD, hcount.get((ti, kb), 0) * TRACK + 6)
        GY[(ti, kb)] = (y, gh); y += gh
        y = max(y, LANE_Y[(ti, prev_lane)][0] + lane_header_h(L, ti, prev_lane))
        LANE_Y[(ti, prev_lane)][1] = y
        TIER_Y[ti][1] = y
        y += TIER_GAP
    H = y

    # ---- node positions
    P = {}
    for nid, b in boxes.items():
        ti = tier_of[nid]
        x0, x1 = X[(ti, col[nid])]
        r0, r1 = Y[(ti, slot(nid))]
        cx = (x0 + x1) / 2
        P[nid] = (cx - b.w / 2, r0 + (r1 - r0 - b.h) / 2, b.w, b.h)

    def gy(ti, gp, key):
        y0, h = GY[(ti, gp)]
        n = hcount.get((ti, gp), 1) or 1
        k = htrack.get(((ti, gp), key), 0)
        return y0 + h / 2 + (k - (n - 1) / 2) * TRACK

    # ---- ports: spread edges that share a side
    sides = {}
    def side_of(eid, end):
        r = routes[eid]; k = r["kind"]
        if k == "drop":
            e = E[eid]; down = P[e["target"]][1] > P[e["source"]][1]
            return ("bottom" if down else "top") if end == "s" else ("top" if down else "bottom")
        if k == "side":
            return "right"
        if k == "back":
            return "right" if end == "s" else "left"
        return "right" if end == "s" else "left"
    for eid in routes:
        if routes[eid]["kind"] == "self":
            continue
        e = E[eid]
        for end, nid in (("s", e["source"]), ("t", e["target"])):
            sides.setdefault((nid, side_of(eid, end)), []).append((eid, end))
    port = {}
    for (nid, sd), lst in sides.items():
        x, y_, w, h = P[nid]
        def other(it):
            eid, end = it; e = E[eid]; o = e["target"] if end == "s" else e["source"]
            if tier_of[o] != tier_of[nid]:
                return (1e6 if end == "s" else -1e6)
            ox, oy, ow, oh = P[o]
            return (oy + oh / 2) if sd in ("left", "right") else (ox + ow / 2)
        lst.sort(key=lambda it: (other(it), it[0]))
        n = len(lst)
        kind = boxes[nid].kind
        for i, it in enumerate(lst):
            if sd in ("left", "right"):
                span = min(12 * (n - 1), max(h - 16, min(h - 4, MIN_SEP * (n - 1)))) if n > 1 else 0
                yy = y_ + h / 2 - span / 2 + (span / (n - 1) * i if n > 1 else 0)
                if kind == "decision":
                    yy = y_ + h / 2 + (i - (n - 1) / 2) * MIN_SEP
                    half = w / 2 * (1 - abs(yy - (y_ + h / 2)) / (h / 2))
                    xx = x + w / 2 - half if sd == "left" else x + w / 2 + half
                else:
                    xx = x if sd == "left" else x + w
                port[it] = (xx, yy)
            else:
                span = min(w - 24, 14 * (n - 1)) if n > 1 else 0
                xx = x + w / 2 - span / 2 + (span / (n - 1) * i if n > 1 else 0)
                yy = y_ if sd == "top" else y_ + h
                if kind == "decision":
                    xx = x + w / 2 + (i - (n - 1) / 2) * MIN_SEP
                    off = abs(xx - (x + w / 2)) / (w / 2) * (h / 2)
                    yy = y_ + off if sd == "top" else y_ + h - off
                port[it] = (xx, yy)

    for eid, r in routes.items():
        xx = lone_drop_x(eid, FR[tier_of[E[eid]["source"]]]) if r["kind"] == "drop" else None
        if xx is not None:
            port[(eid, "s")] = (xx, port[(eid, "s")][1]); port[(eid, "t")] = (xx, port[(eid, "t")][1])

    def level_ok(nid, sd, yy):
        """True when the only edge on side sd of box nid may meet it at height yy (inside its straight side)."""
        bx, by, bw, bh = P[nid]
        return boxes[nid].kind != "decision" and len(sides.get((nid, sd), [])) == 1 and by + 8 <= yy <= by + bh - 8

    # ---- polylines
    PTS = {}
    CONN, cpos, cused = [], {}, []
    for eid, r in routes.items():
        e = E[eid]; s, t = e["source"], e["target"]; ti = tier_of[s]
        k = r["kind"]
        if k == "self":
            x, y_, w, h = P[s]
            PTS[eid] = [[(x + w, y_ + h * 0.3), (x + w + 14, y_ + h * 0.3), (x + w + 14, y_ + h * 0.7), (x + w, y_ + h * 0.7)]]
            continue
        a, z = port.get((eid, "s")), port.get((eid, "t"))
        if k == "straight":
            # a short jog between two near rows reads as a second line beside its neighbours: when the target side
            # takes only this edge and the source height lies on that side, the line enters level instead
            tx, ty, tw_, th_ = P[t]
            if (abs(a[1] - z[1]) >= 0.5 and boxes[t].kind != "decision" and len(sides.get((t, "left"), [])) == 1
                    and ty + 8 <= a[1] <= ty + th_ - 8):
                z = (z[0], a[1])
            pts = [a, (z[0], a[1])] if abs(a[1] - z[1]) < 0.5 else [a, ((a[0] + z[0]) / 2, a[1]), ((a[0] + z[0]) / 2, z[1]), z]
            PTS[eid] = [pts]
        elif k == "drop":
            PTS[eid] = [[a, (a[0], (a[1] + z[1]) / 2), (z[0], (a[1] + z[1]) / 2), z] if abs(a[0] - z[0]) > 0.5 else [a, z]]
        elif k in ("jog", "side"):
            xc = chx(ti, r["ch"], eid)
            if k == "jog" and abs(a[1] - z[1]) >= 0.5:
                # between neighbouring columns a line runs level when one end can take the other's height
                if level_ok(t, "left", a[1]):
                    z = (z[0], a[1])
                elif level_ok(s, "right", z[1]):
                    a = (a[0], z[1])
            PTS[eid] = [[a, z]] if abs(a[1] - z[1]) < 0.5 else [[a, (xc, a[1]), (xc, z[1]), z]]
        elif k in ("gap", "back"):
            x1, x2 = chx(ti, r["ch1"], eid + ":a"), chx(ti, r["ch2"], eid + ":b")
            yy = gy(ti, r["gp"], eid)
            PTS[eid] = [[a, (x1, a[1]), (x1, yy), (x2, yy), (x2, z[1]), z]]
        elif k == "split":
            tt = tier_of[t]
            gk = "%s>%d" % r["group"]
            x1 = chx(ti, r["ch_out"], gk + ":a"); yo = gy(ti, r["gp_out"], gk + ":o")
            x2 = chx(tt, r["ch_in"], eid + ":b"); yi = gy(tt, r["gp_in"], gk + ":i")
            if r["group"] not in cpos:
                cr = conn_shape(r["letter"])[0]
                xr = CHX[(ti, tiers[ti][1] - 1)][0] + CHX[(ti, tiers[ti][1] - 1)][1] - (3 + cr)
                xl = CHX[(tt, tiers[tt][0] - 1)][0] + (3 + cr)
                # circles keep 4 px apart across, 2 px apart down (26 / 24 px between centres at the 11 px radius)
                while any(abs(xr - p[0]) < cr + p[2] + 4 and abs(yo - p[1]) < cr + p[2] + 2 for p in cused):
                    xr -= 2 * cr + 4
                while any(abs(xl - p[0]) < cr + p[2] + 4 and abs(yi - p[1]) < cr + p[2] + 2 for p in cused):
                    xl += 2 * cr + 4
                cused += [(xr, yo, cr), (xl, yi, cr)]
                cpos[r["group"]] = (xr, xl, cr)
                CONN.append((eid, r["letter"], (xr, yo), (xl, yi), ti, tt))
            xr, xl, cr = cpos[r["group"]]
            PTS[eid] = [[a, (x1, a[1]), (x1, yo), (xr - cr, yo)], [(xl + cr, yi), (x2, yi), (x2, z[1]), z]]

    separate_stubs(PTS, routes, E, P, boxes)
    return dict(L=L, routes=routes, P=P, PTS=PTS, CONN=CONN, X=X, Y=Y, GY=GY, CHX=CHX, LANE_Y=LANE_Y, TIER_Y=TIER_Y,
                W=W, H=H, FR=FR, frame_label_h=frame_label_h, frame_label_x=frame_label_x, frame_label_off=frame_label_off, tier_rows=tier_rows, slot=slot)


def separate_stubs(PTS, routes, E, P, boxes):
    """Level runs at a box side (the first or last segment of a line, leading to its channel) of two different
    edges that lie closer than MIN_SEP over a shared stretch read as one line. Move one of them up or down along
    its box side, toward the way its line turns, until every such pair is MIN_SEP apart (bounded passes)."""
    def stubs():
        out = []
        for eid, segs in PTS.items():
            if routes[eid]["kind"] == "self":
                continue
            e = E[eid]
            for end, pts, i in (("s", segs[0], 0), ("t", segs[-1], -1)):
                if len(pts) < 3:
                    continue
                p0, p1 = (pts[0], pts[1]) if i == 0 else (pts[-1], pts[-2])
                nxt = pts[2] if i == 0 else pts[-3]
                if abs(p0[1] - p1[1]) < 0.01 and abs(p1[0] - nxt[0]) < 0.01:
                    out.append((eid, end, e["source"] if end == "s" else e["target"], p0[1],
                                min(p0[0], p1[0]), max(p0[0], p1[0]), 1 if nxt[1] > p1[1] else -1))
        return out
    def clash(y, x0, x1, eid, all_):
        return any(o[0] != eid and abs(o[3] - y) < MIN_SEP and min(x1, o[5]) - max(x0, o[4]) > 0.5 for o in all_)
    for _ in range(4 * len(PTS) + 4):  # one move per pass; each move clears a pair for good
        all_ = stubs()
        moved = False
        for a in all_:
            eid, end, nid, y, x0, x1, way = a
            if not clash(y, x0, x1, eid, all_):
                continue
            bx, by, bw, bh = P[nid]
            if boxes[nid].kind == "decision":
                continue
            for d in [way * k for k in range(2, 17)] + [-way * k for k in range(2, 17)]:
                ny = y + d * MIN_SEP / 4
                if by + 4 <= ny <= by + bh - 4 and not clash(ny, x0, x1, eid, all_):
                    pts = PTS[eid][0] if end == "s" else PTS[eid][-1]
                    j, k = (0, 1) if end == "s" else (-1, -2)
                    pts[j] = (pts[j][0], ny); pts[k] = (pts[k][0], ny)
                    moved = True
                    break
            if moved:
                break
        if not moved:
            break


def fr_all(L, p):
    sp = L.get("spans") or {}
    return [nid for nid in L["nodes"] if L["proc"][nid] == p or p in sp.get(nid, [])]


SET_LEVEL_NAME = "Across the processes"


def frame_label(meta, p, cont, other_lane):
    """A frame's caption: the process name ("Unnamed process" when the bundle names none); the id itself stays in the
    frame's <title> and data-process, never in the visible caption."""
    name = str(meta.get("name") or "").strip() or "Unnamed process"
    if other_lane:
        return name + " · same process"
    if cont:
        return name + " · continued"
    s = name
    if meta.get("role"):
        s += " · " + meta["role"]
    if meta.get("tags"):
        s += " · " + meta["tags"]
    return s


def lane_ns(L, lid):
    return next(l for l in L["lane_list"] if l["id"] == lid)


def lane_label_lines(l):
    return wrap(l["label"], T_LANE, 600, HEAD_W - 24)


def lane_role_lines(l):
    """Preserve authored roles under the label, omitting only an exact label duplicate."""
    return [line for role in l.get("roles", []) if role != l["label"]
            for line in wrap(role, 11, 400, HEAD_W - 24)]


def label_block_h(l):
    roles = lane_role_lines(l)
    return 14 + len(lane_label_lines(l)) * 16 + (6 + len(roles) * 14 if roles else 0) + 2 * LANE_PAD + 6


def lane_header_h(L, ti, lid):
    l = lane_ns(L, lid)
    if L["lane_rows"][(ti, lid)] == 0 and not lane_role_lines(l):
        return COLLAPSED_H
    return label_block_h(l)


# ---------------------------------------------------------------- SVG
def diagram_heading(B):
    """Name the actual state; never infer current state from the layout shape."""
    variant = str(B.get("variant") or "").replace("-", " ").strip()
    return variant.capitalize() if variant else str(B.get("title") or "Workflow")


def svg_doc(G, B):
    L, P, PTS = G["L"], G["P"], G["PTS"]
    nodes, boxes, edges = L["nodes"], L["boxes"], L["edges"]
    W, H = G["W"], G["H"]
    out, labels, placed, rects = [], [], [], []
    foot = G.setdefault("footnotes", [])
    # obstacles for label placement
    for nid, (x, y, w, h) in P.items():
        rects.append((x - 3, y - 3, w + 6, h + 6))
    for _, _, (xr, yo), (xl, yi), _, _ in G["CONN"]:
        rects += [(xr - 13, yo - 13, 26, 26), (xl - 13, yi - 13, 26, 26)]
    SEGS = [(eid, sg) for eid, segs in PTS.items() for pts in segs for sg in zip(pts, pts[1:])]
    # tiers and lanes
    for ti, (a, z) in enumerate(L["tiers"]):
        t0, t1 = G["TIER_Y"][ti]
        n = len(L["tiers"])
        title = ("Part %d of %d" % (ti + 1, n)) if n > 1 else diagram_heading(B)
        out.append('<g data-role="tier" data-decoration="tier">')
        out.append(f'<text x="4" y="{t0 + 20}" font-size="13" font-weight="700" fill="{MID}" font-family="{FONT}">{esc(title)}</text>')
        if n > 1:
            # the subtitle starts past the title as the widest fallback font would set it, so the two never touch
            sx = 4 + max(tw(title, 13, 700), len(title) * 13 * 0.66) + 16
            out.append(f'<text x="{sx:.1f}" y="{t0 + 20}" font-size="11" fill="{GREY}" font-family="{FONT}">reads left to right, then continues in the next part; lettered circles join the parts</text>')
        out.append("</g>")
        rects.append((0, t0, W, TIER_HEAD))
        prev_band = None
        for l in L["lane_list"]:
            y0, y1 = G["LANE_Y"][(ti, l["id"])]
            out.append(f'<rect data-role="lane" data-decoration="lane" x="0" y="{y0:.1f}" width="{W}" height="{y1 - y0:.1f}" fill="{BAND_FILL[l["band"]]}"/>')
            sw = 2 if prev_band and prev_band != l["band"] else 1
            out.append(f'<line data-decoration="lane" x1="0" y1="{y0:.1f}" x2="{W}" y2="{y0:.1f}" stroke="{"#B9AE9C" if sw > 1 else "#E6DED2"}" stroke-width="{sw}"/>')
            collapsed = L["lane_rows"][(ti, l["id"])] == 0
            out.append(f'<rect data-role="lane" data-decoration="lane" x="0" y="{y0:.1f}" width="{HEAD_W}" height="{y1 - y0:.1f}" fill="{MID}"/>')
            rects.append((0, y0, HEAD_W, y1 - y0))
            if collapsed:
                lab = BAND_NAME[l["band"]] + " · " + l["label"]
                lab += " · no current AI use found" if (l["band"] == "ai_and_agents" and not any(nodes[n_]["lane_id"] == l["id"] for n_ in nodes)) else " · nothing in this part"
                for (cx_, cy_, fs_, rows_) in collapsed_caption(lab, l, y0, y1, W, SEGS, G["CONN"]):
                    for i, s_ in enumerate(rows_):
                        out.append(f'<text data-decoration="lane" x="{cx_:.1f}" y="{cy_ + i * (fs_ + 1):.1f}" font-size="{fs_}" fill="{GREY}" font-family="{FONT}" font-style="italic">{esc(s_)}</text>')
                    rects.append((cx_ - 4, y0 + 4, max(tw(r_, fs_) for r_ in rows_) + 8, y1 - y0 - 8))
            if collapsed and not lane_role_lines(l):
                out.append(f'<text data-decoration="lane" x="12" y="{(y0 + y1) / 2 + 4:.1f}" font-size="10" font-weight="700" fill="#F9A68F" font-family="{FONT}" letter-spacing="0.4">{esc(BAND_NAME[l["band"]])}</text>')
            else:
                ls = lane_label_lines(l)
                roles = lane_role_lines(l)
                blk = label_block_h(l) - 2 * LANE_PAD - 6
                out.append(f'<g data-lane-header="{esc(l["id"])}">')
                cy = (y0 + y1) / 2 - blk / 2
                out.append(f'<text data-decoration="lane" x="12" y="{cy + 10:.1f}" font-size="10" font-weight="700" fill="#F9A68F" font-family="{FONT}" letter-spacing="0.4">{esc(BAND_NAME[l["band"]])}</text>')
                for i, s in enumerate(ls):
                    out.append(f'<text data-decoration="lane" x="12" y="{cy + 28 + i * 16:.1f}" font-size="{T_LANE}" font-weight="600" fill="{WHITE}" font-family="{FONT}">{esc(s)}</text>')
                for i, s in enumerate(roles):
                    out.append(f'<text data-decoration="lane" data-role="lane-role" x="12" y="{cy + 28 + len(ls) * 16 + 6 + i * 14:.1f}" font-size="11" font-weight="400" fill="{WHITE}" font-family="{FONT}">{esc(s)}</text>')
                out.append("</g>")
            prev_band = l["band"]
    # frames
    stacked = []   # captions of frames that share a box: drawn after every frame, so no later rectangle covers them
    for ti, fr in G["FR"].items():
        for (p, lid), ms in fr.items():
            xs = [P[m] for m in ms]
            x0 = min(b[0] for b in xs) - 10; x1 = max(b[0] + b[2] for b in xs) + 10
            y0 = min(b[1] for b in xs); y1 = max(b[1] + b[3] for b in xs) + 8
            lines = G["frame_label_h"][(ti, p, lid)]
            before, total = G["frame_label_off"][(ti, p, lid)]
            top = y0 - total * 13.5 - 10
            # stretch to the row band so stacked frames align
            G.setdefault("FRAME_RECTS", []).append((p, lid, ti, (x0, top, x1 - x0, y1 - top)))
            sp = [m for m in ms if p in (L.get("spans") or {}).get(m, [])]
            sp_attr = f' data-spanning-steps="{esc(" ".join(sp))}"' if sp else ""
            meta = L["frames_meta"].get(p, {})
            ftip = "Process " + p + ((" · " + str(meta["name"])) if meta.get("name") else "")
            out.append(f'<rect data-role="frame" data-decoration="frame" data-process="{esc(p)}"{sp_attr} x="{x0:.1f}" y="{top:.1f}" width="{x1 - x0:.1f}" height="{y1 - top:.1f}" rx="10" fill="{CREAM}" stroke="#C9BBA5" stroke-width="1.2"><title>{title_text(ftip)}</title></rect>')
            dest = stacked if total > len(lines) else out
            # the caption block keeps clear of every line drawn through its strip (drops included): when the chosen
            # spot is crossed, it slides right to the first spot inside the frame that no line crosses
            cx_ = G["frame_label_x"].get((ti, p, lid), x0 + 8)
            mw_ = SAFE * max(tw(l_, 10.5, 600) for l_ in lines)
            band = (top + 15 + before * 13.5 - 9, top + 15 + (before + len(lines) - 1) * 13.5 + 3)
            def crossed(xa):
                r_ = (xa, band[0], mw_, band[1] - band[0])
                return [sg for _, sg in SEGS if seg_hits(sg, r_, 2)]
            if total == len(lines) and crossed(cx_):
                for xa in sorted({x0 + 8} | {max(sg[0][0], sg[1][0]) + 4 for _, sg in SEGS}):
                    if x0 + 8 <= xa and xa + mw_ <= x1 - 8 and not crossed(xa):
                        cx_ = xa; break
            for i, s in enumerate(lines):
                dest.append(f'<text data-decoration="frame" data-process="{esc(p)}" x="{cx_:.1f}" y="{top + 15 + (before + i) * 13.5:.1f}" font-size="10.5" font-weight="600" font-family="{FONT}" fill="{MID}">{esc(s)}</text>')
            rects.append((x0, top, x1 - x0, total * 13.5 + 8))
    out.extend(stacked)
    # edges
    STY = {"Observed": (MID, ""), "Assumed": (MID, "7 5"), "Contradictory": (CORAL, ""), "needs_more_information": (CORAL, "2 4")}
    for e in edges:
        eid = e["id"]
        segs = PTS.get(eid)
        if not segs:
            continue
        colr, dash = STY.get(e.get("evidence_state"), (MID, ""))
        sw = 2.6 if e["kind"] == "handoff" else 1.4
        if e.get("context"):
            colr = GREY if colr == MID else colr
        if e.get("weak"):
            dash = "2 3"
        start_marker = ""
        if e["kind"] == "asks_returns":
            colr, sw = ACCESS, 1.4
            dash = dash or "4 3"
            start_marker = f' marker-start="url(#ah-{ACCESS[1:]})"'
        if e["kind"] == "sets_state":
            colr, dash, sw = GREY, "1.5 3", 1.3
        if e["kind"] == "loop":
            colr = GREY
        tip = f'{e["kind"]} · {e.get("evidence_state", "")}' + (f' · {e["label"]}' if e.get("label") else "")
        allpts = []
        rk = G["routes"][eid]["kind"]
        route = {"drop": "drop" if P[e["target"]][1] > P[e["source"]][1] else "rise", "back": "return", "self": "self"}.get(rk, "step")
        out.append(f'<g id="edge-{esc(eid)}" data-graph-edge-id="{esc(eid)}" data-source="{esc(e["source"])}" data-target="{esc(e["target"])}" data-kind="{esc(e["kind"])}" data-route="{route}">')
        for i, pts in enumerate(segs):
            end = ' marker-end="url(#ah-%s)"' % colr[1:] if i == len(segs) - 1 else ""
            st = start_marker if i == 0 else ""
            pstr = " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
            allpts.append(pstr)
            out.append(f'<polyline data-points="{pstr}" points="{pstr}" fill="none" stroke="{colr}" stroke-width="{sw}" stroke-dasharray="{dash}" stroke-linejoin="round"{end}{st}><title>{title_text(tip)}</title></polyline>')
        out.append("</g>")
        # label
        text = e.get("display_label") or ""
        if text:
            lw, lh = tw(text, 10.5, 500) + 14, 18
            spot = None
            cands = []
            for pts in segs:
                for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
                    ln = abs(x2 - x1) + abs(y2 - y1)
                    for f in (0.5, 0.35, 0.65, 0.2, 0.8):
                        cands.append((-(ln if abs(y1 - y2) < 0.5 else ln * 0.6), f, x1 + (x2 - x1) * f, y1 + (y2 - y1) * f))
            cands.sort(key=lambda c: (c[0], abs(c[1] - 0.5)))
            more = []
            for pts in segs:
                for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
                    horiz = abs(y1 - y2) < 0.5
                    for f in (0.5, 0.3, 0.7, 0.15, 0.85):
                        mx, my = x1 + (x2 - x1) * f, y1 + (y2 - y1) * f
                        for off in (12, -12, 22, -22, 32, -32):
                            more.append((0, f, mx, my + off) if horiz else (0, f, mx + off + (lw / 2 if off > 0 else -lw / 2), my))
            for pts in segs:  # finer steps along each segment, tried after the usual spots
                for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
                    for f in (0.1, 0.25, 0.4, 0.45, 0.55, 0.6, 0.75, 0.9):
                        more.append((0, f, x1 + (x2 - x1) * f, y1 + (y2 - y1) * f))
            others = [sg for oid, sg in SEGS if oid != eid]
            def clear(r, lines):
                """r keeps off every box, caption and placed label, and (when lines) every other edge's line."""
                return (not any(hit(r, o) for o in rects + placed)
                        and not (lines and any(seg_hits(sg, r) for sg in others)))
            def marker_spot(lines):
                for pts in segs:
                    for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
                        for f in (0.5, 0.3, 0.7, 0.15, 0.85, 0.1, 0.4, 0.6, 0.9):
                            mx, my = x1 + (x2 - x1) * f, y1 + (y2 - y1) * f
                            r = (mx - 9, my - 9, 18, 18)
                            if clear(r, lines):
                                return r
                return None
            # A label never sits on another edge's line: the full label, else a numbered marker, clear of every
            # other line; only when neither fits anywhere does it fall back to keeping clear of boxes alone.
            marker = False
            for lines in (True, False):
                for _, f, mx, my in cands + more:
                    r = (mx - lw / 2, my - lh / 2, lw, lh)
                    if 0 <= r[0] and r[0] + lw <= W and clear(r, lines):
                        spot = r; break
                if spot is None:
                    spot = marker_spot(lines); marker = spot is not None
                if spot is not None:
                    break
            if spot is None:
                G["L"]["notes"].append("no room for the label of %s" % eid); continue
            if marker:
                # numbered marker on the line; the text is listed under the diagram
                num = len(foot) + 1
                placed.append(spot)
                foot.append((num, text, nodes[e["source"]]["title"], nodes[e["target"]]["title"]))
                x, y, w, h = spot
                labels.append(f'<g data-role="edge-label" data-decoration="edge-label" data-edge="{esc(eid)}"><title>{title_text(text)}</title><circle cx="{x + 9:.1f}" cy="{y + 9:.1f}" r="9" fill="{WHITE}" stroke="{colr}" stroke-width="1"/>'
                              f'<text x="{x + 9:.1f}" y="{y + 12.6:.1f}" font-size="10.5" font-weight="700" fill="{colr if colr != GREY else "#4A5560"}" text-anchor="middle" font-family="{FONT}">{num}</text></g>')
                continue
            placed.append(spot)
            x, y, w, h = spot
            labels.append(f'<g data-role="edge-label" data-decoration="edge-label" data-edge="{esc(eid)}"><rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h}" rx="9" fill="{WHITE}" stroke="{colr}" stroke-width="0.8"/>'
                          f'<text x="{x + w / 2:.1f}" y="{y + 12.8:.1f}" font-size="10.5" font-weight="500" fill="{colr if colr != GREY else "#4A5560"}" text-anchor="middle" font-family="{FONT}">{esc(text)}</text></g>')
    # connectors between parts
    for eid, letter, (xr, yo), (xl, yi), ti, tt in G["CONN"]:
        e = E_of(edges, eid)
        tgt, src = nodes[e["target"]]["title"], nodes[e["source"]]["title"]
        cr, fs = conn_shape(letter)
        dy = 4 if fs == CONN_FS else round(fs * 0.36, 1)  # baseline offset that centres the label vertically
        for (cx, cy, txt) in ((xr, yo, f"Continues in part {tt + 1}: {tgt}"), (xl, yi, f"From part {ti + 1}: {src}")):
            out.append(f'<g data-role="connector" data-decoration="connector"><title>{title_text(txt)}</title><circle cx="{cx:.1f}" cy="{cy:.1f}" r="{cr}" fill="{WHITE}" stroke="{MID}" stroke-width="1.6"/>'
                       f'<text x="{cx:.1f}" y="{cy + dy:.1f}" font-size="{fs}" font-weight="700" fill="{MID}" text-anchor="middle" font-family="{FONT}">{letter}</text></g>')
    # nodes
    for nid, b in boxes.items():
        x, y, w, h = P[nid]
        n = b.n
        tip = "\n".join(b.tips)
        g_ = [f'<g id="node-{esc(nid)}" data-graph-node-id="{esc(nid)}" data-lane="{esc(n["lane"])}" data-node-type="{esc(n["type"])}" data-systems="{esc(json.dumps(n.get("systems", [])))}"{agent_attr(b)} data-bounds="{x:.1f} {y:.1f} {w:.1f} {h:.1f}"><title>{title_text(tip)}</title>']
        for t_ in L["g"].get("decision_tables", []):
            own = t_["owner"] if isinstance(t_["owner"], list) else [t_["owner"]]
            if nid in own:
                rows_ = "; ".join(str((r.get("attributes") or {}).get("outcome", r.get("id"))) + " (" + str(r.get("evidence_state", "")) + ")" for r in t_.get("rows", []))
                g_.append(f'<g id="table-{esc(t_["id"])}-{esc(nid)}" data-graph-table-id="{esc(t_["id"])}" data-owner="{esc(nid)}"><title>{title_text(t_.get("purpose", ""))}: {title_text(rows_)}</title></g>')
        k = b.kind
        if k == "decision":
            cx, cy = x + w / 2, y + h / 2
            stroke = CORAL if b.dash == "2 3" else MID
            g_.append(f'<polygon points="{cx},{y:.1f} {x + w:.1f},{cy:.1f} {cx},{y + h:.1f} {x:.1f},{cy:.1f}" fill="{SUN}" stroke="{stroke}" stroke-width="1.3" stroke-dasharray="{b.dash}"/>')
        elif k in ("start", "end", "stop", "state", "message"):
            fill, stroke, sw, rx, dash = WHITE, MID, 1.3, h / 2, b.dash
            if k == "end":
                sw = 3
            if k == "stop":
                stroke, sw = CORAL, 2.4
            if k == "state":
                fill = SUN if L["nodes"][nid]["lane_id"] and lane_band(L, nid) == "system_of_record" else ROSE
            if k == "message":
                rx, dash, stroke = 6, "3 3", GREY
            g_.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" rx="{rx:.1f}" fill="{fill}" stroke="{stroke}" stroke-width="{sw}" stroke-dasharray="{dash}"/>')
        else:
            fill, stroke, sw = WHITE, MID, 1.2
            if k == "ai":
                fill, stroke, sw = "#E6F8FE", (CORAL if b.failed else ACCESS), (2.2 if b.failed else 1.3)
            if b.agent:
                fill, stroke = "#E6F8FE", ACCESS
            if k == "context":
                fill, stroke = "#F3EEE6", GREY
                b.dash = b.dash or "5 4"
            if b.dash == "2 3":
                stroke = CORAL
            g_.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" rx="8" fill="{fill}" stroke="{stroke}" stroke-width="{sw}" stroke-dasharray="{b.dash}"/>')
        for (s, sz, wt, c, mono, ax, yy) in b.text:
            fam = MONO if mono else FONT
            if ax == "c":
                g_.append(f'<text x="{x + w / 2:.1f}" y="{y + yy:.1f}" font-size="{sz}" font-weight="{wt}" fill="{c}" text-anchor="middle" font-family="{fam}">{esc(s)}</text>')
            else:
                g_.append(f'<text x="{x + ax:.1f}" y="{y + yy:.1f}" font-size="{sz}" font-weight="{wt}" fill="{c}" font-family="{fam}">{esc(s)}</text>')
        for (st, _), (cy, ch, lines) in zip(b.chips, b.chip_y):
            g_.append(chip_svg(L, st, nid, x, y + cy, w, ch, lines))
        if b.badge:
            bx, by = x + w - 14, y + 14
            g_.append(f'<g data-role="badge"><circle cx="{bx:.1f}" cy="{by:.1f}" r="9" fill="{CORAL}"/><text x="{bx:.1f}" y="{by + 3.8:.1f}" font-size="10.5" font-weight="700" fill="{WHITE}" text-anchor="middle" font-family="{FONT}">{b.badge}</text></g>')
        if b.ring:
            rx_ = x + w - (34 if b.badge else 12)
            g_.append(f'<circle data-role="badge" cx="{rx_:.1f}" cy="{y + 12:.1f}" r="4" fill="{WHITE}" stroke="{CORAL}" stroke-width="2"/>')
        g_.append("</g>")
        out.append("".join(g_))
    out += labels
    out += hidden_elements(L)
    cols = (MID, CORAL, GREY, ACCESS)
    markers = "".join(f'<marker id="ah-{c[1:]}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="{c}"/></marker>' for c in cols)
    svg = (f'<svg id="dg" xmlns="http://www.w3.org/2000/svg" data-renderer="{RENDERER_ID}" viewBox="0 0 {W:.0f} {H:.0f}" width="{W:.0f}" height="{H:.0f}">'
           f'<defs>{markers}</defs><rect data-decoration="canvas" width="{W:.0f}" height="{H:.0f}" fill="{WHITE}"/>' + "".join(out) + "</svg>")
    return svg


def hidden_elements(L):
    """One empty, non-drawn <g> per hidden node or edge (a replaced AI use and its edges): it carries the graph id
    and the attributes svg_binding reads, plus data-hidden and the reason, and no visible primitive. A hidden edge
    has no route; it carries data-route="step" only because svg_binding requires a known route value."""
    out, hidden = [], L.get("hidden") or {}
    for n in L["g"]["nodes"]:
        if n["id"] in hidden:
            out.append(f'<g id="node-{esc(n["id"])}" data-graph-node-id="{esc(n["id"])}" data-lane="{esc(n["lane"])}" '
                       f'data-node-type="{esc(n["type"])}" data-systems="{esc(json.dumps(n.get("systems", [])))}" '
                       f'data-hidden="replaced" data-hidden-reason="{esc(hidden[n["id"]])}" display="none"/>')
    for e in L["g"]["edges"]:
        if e["id"] in hidden:
            out.append(f'<g id="edge-{esc(e["id"])}" data-graph-edge-id="{esc(e["id"])}" data-source="{esc(e["source"])}" '
                       f'data-target="{esc(e["target"])}" data-kind="{esc(e["kind"])}" data-route="step" '
                       f'data-hidden="replaced" data-hidden-reason="{esc(hidden[e["id"]])}" display="none"/>')
    return out


def agent_attr(b):
    return f' data-agent="{esc(b.agent)}"' if b.agent else ""


def chip_svg(L, st, host, x, y, w, h, lines):
    """A write-only state as a chip inside its writer's box, bound to the state's id, with its sets_state edge
    drawn as a short tick from the box's text to the chip (the edge goes to the host box)."""
    sid = st["id"]
    tips = [st.get("title", sid)] + (["Evidence: " + st["evidence_state"]] if st.get("evidence_state") else [])
    tips.append("Write-only state, set by this step (no other step reads it).")
    if st.get("agent"):
        tips.append("Agent: " + str(st["agent"]))
    ag = f' data-agent="{esc(str(st["agent"]))}"' if st.get("agent") else ""
    x0, cw = x + PAD, w - 2 * PAD
    out = [f'<g id="node-{esc(sid)}" data-graph-node-id="{esc(sid)}" data-role="chip" data-host="{esc(host)}" '
           f'data-lane="{esc(st["lane"])}" data-node-type="{esc(st["type"])}" data-systems="{esc(json.dumps(st.get("systems", [])))}"{ag} '
           f'data-bounds="{x0:.1f} {y:.1f} {cw:.1f} {h:.1f}"><title>{title_text(chr(10).join(tips))}</title>'
           f'<rect x="{x0:.1f}" y="{y:.1f}" width="{cw:.1f}" height="{h:.1f}" rx="{min(h / 2, 9):.1f}" fill="{ROSE}" stroke="{GREY}" stroke-width="0.8"/>']
    for i, ln in enumerate(lines):
        out.append(f'<text x="{x0 + 6:.1f}" y="{y + 3 + (i + 1) * CHIP_T * 1.28 - CHIP_T * 0.28:.1f}" font-size="{CHIP_T}" font-weight="500" fill="{MID}" font-family="{FONT}">{esc(ln)}</text>')
    out.append("</g>")
    for e in L["chip_edges"]:
        if e["target"] == sid:
            tip = f'{e["kind"]} · {e.get("evidence_state", "")} · drawn as the chip on its writer'
            out.append(f'<g id="edge-{esc(e["id"])}" data-graph-edge-id="{esc(e["id"])}" data-source="{esc(e["source"])}" '
                       f'data-target="{esc(sid)}" data-kind="{esc(e["kind"])}" data-route="self" data-role="chip-edge">'
                       f'<polyline points="{x0 - 4:.1f},{y + 3:.1f} {x0 - 4:.1f},{y + h - 3:.1f}" fill="none" stroke="{GREY}" '
                       f'stroke-width="1.3"><title>{title_text(tip)}</title></polyline></g>')
    return "".join(out)


def collapsed_caption(lab, lane, y0, y1, W, SEGS, CONN):
    """[(x, first baseline, font size, lines)] for a collapsed lane's caption, set where no line crosses it: the
    full caption on one line at the first free stretch it fits, else without the band name (the lane head shows
    it), else wrapped onto the lines the lane holds. When no stretch is free it keeps its usual place."""
    ya, yb = y0 + 3, y1 - 3
    blocks = []
    for _, ((x1, y1_), (x2, y2_)) in SEGS:
        if abs(x1 - x2) < 0.01 and min(y1_, y2_) < yb and max(y1_, y2_) > ya:
            blocks.append((x1 - 4, x1 + 4))
        elif abs(y1_ - y2_) < 0.01 and ya < y1_ < yb:
            blocks.append((min(x1, x2) - 4, max(x1, x2) + 4))
    for _, _, (xr, yo), (xl, yi), _, _ in CONN:
        for cx, cy in ((xr, yo), (xl, yi)):
            if abs(cy - (y0 + y1) / 2) < (y1 - y0) / 2 + 13:
                blocks.append((cx - 15, cx + 15))
    free, cur, hi = [], HEAD_W + 8, W - 4
    for b0, b1 in sorted(blocks):
        if b0 > cur:
            free.append((cur, min(b0, hi)))
        cur = max(cur, b1)
    if cur < hi:
        free.append((cur, hi))
    short = lane["label"] + lab[len(BAND_NAME[lane["band"]] + " · " + lane["label"]):]
    mid = (y0 + y1) / 2
    for text in (lab, short):
        for f0, f1 in free:
            if SAFE * tw(text, 11) + 8 <= f1 - f0:
                return [(f0 + 4, mid + 4, 11, [text])]
    room = max(1, int((y1 - y0 - 4) // 11))
    for f0, f1 in free:
        rows = wrap(short, 10, 400, (f1 - f0 - 8) / SAFE)
        if len(rows) <= room and SAFE * max(tw(r, 10) for r in rows) + 8 <= f1 - f0:
            return [(f0 + 4, mid + 4 - (len(rows) - 1) * 11 / 2, 10, rows)]
    return [(HEAD_W + 12, mid + 4, 11, [lab])]


def lane_band(L, nid):
    return lane_ns(L, L["nodes"][nid]["lane_id"])["band"]


def E_of(edges, eid):
    return next(e for e in edges if e["id"] == eid)


def hit(a, b):
    return a[0] < b[0] + b[2] and b[0] < a[0] + a[2] and a[1] < b[1] + b[3] and b[1] < a[1] + a[3]


def seg_hits(sg, r, m=1.0):
    """True when the straight segment sg ((x1, y1), (x2, y2)) passes within m px of the rectangle r (x, y, w, h)."""
    (x1, y1), (x2, y2) = sg
    x0, y0, xa, ya = r[0] - m, r[1] - m, r[0] + r[2] + m, r[1] + r[3] + m
    if abs(y1 - y2) < 0.01:
        return y0 < y1 < ya and min(x1, x2) < xa and max(x1, x2) > x0
    if abs(x1 - x2) < 0.01:
        return x0 < x1 < xa and min(y1, y2) < ya and max(y1, y2) > y0
    for i in range(1, 40):  # a diagonal (none are drawn today): sampled
        t = i / 40
        if x0 < x1 + (x2 - x1) * t < xa and y0 < y1 + (y2 - y1) * t < ya:
            return True
    return False


# ---------------------------------------------------------------- self-check (pure, on our own geometry)
def self_check(G):
    L, P, PTS = G["L"], G["P"], G["PTS"]
    res = {}
    ids = list(P)
    ov = [(a, b) for i, a in enumerate(ids) for b in ids[i + 1:] if hit(P[a], P[b])]
    res["node_overlaps"] = ov
    cross = []
    for eid, segs in PTS.items():
        e = E_of(L["edges"], eid)
        for pts in segs:
            for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
                for nid, (x, y, w, h) in P.items():
                    if nid in (e["source"], e["target"]):
                        continue
                    if x1 == x2 and x < x1 < x + w and max(min(y1, y2), y) < min(max(y1, y2), y + h) - 0.5:
                        cross.append((eid, nid))
                    if abs(y1 - y2) < 0.5 and y < y1 < y + h and max(min(x1, x2), x) < min(max(x1, x2), x + w) - 0.5:
                        cross.append((eid, nid))
    res["edges_through_boxes"] = sorted(set(cross))
    # every A3 lane rule we can check on positions
    lane_order = [l["id"] for l in L["lane_list"]]
    bands = [l["band"] for l in L["lane_list"]]
    res["band_order_ok"] = bands == sorted(bands, key=BANDS.index)
    res["ai_lane_present"] = "ai_and_agents" in bands
    so = L.get("split_of") or {}
    back = [e["id"] for e in L["spine"] if L["col"][e["target"]] <= L["col"][e["source"]]
            and not (e["source"] in so and so.get(e["target"]) == so[e["source"]])]
    res["spine_edges_not_left_to_right"] = back
    # Every node is accounted for exactly once: drawn as a box or pill, drawn as a chip, or listed in hidden.
    hidden = L.get("hidden") or {}
    drawn, chip = set(P), set(L["chips"])
    hid = {i for i in hidden if i in L["all_nodes"]}
    res["node_counts"] = {"drawn": len(drawn), "chip": len(chip), "hidden": len(hid), "total": len(L["all_nodes"])}
    res["nodes_missing"] = [n for n in L["all_nodes"] if n not in drawn | chip | hid]
    res["nodes_drawn"] = not res["nodes_missing"] and len(drawn) + len(chip) + len(hid) == len(L["all_nodes"])
    hid_e = sum(1 for e in L["g"]["edges"] if e["id"] in hidden)
    res["edges_drawn"] = (sum(1 for e in L["edges"] if e["id"] in PTS) + len(L["chip_edges"]) + hid_e
                          == len(L["g"]["edges"]))
    # Both parts of a split in one column: same column index and overlapping x ranges.
    groups = {}
    for nid, r in so.items():
        groups.setdefault(r, []).append(nid)
    res["splits_not_in_one_column"] = sorted(
        sorted(ms) for ms in groups.values()
        if len({L["col"][m] for m in ms}) > 1
        or max(P[m][0] for m in ms) >= min(P[m][0] + P[m][2] for m in ms))
    # A cross-process step intersects every frame it spans and no frame of another process.
    fr = G.get("FRAME_RECTS") or []
    bad = {}
    for nid, ps in (L.get("spans") or {}).items():
        got = {p for p, _, _, r in fr if hit(P[nid], r)}
        missing, extra = sorted(set(ps) - got), sorted(got - set(ps))
        if missing or extra:
            bad[nid] = {"missing": missing, "extra": extra}
    res["spans_not_framed"] = sorted(bad)
    res["span_frame_mismatch"] = bad
    # No drawn box cuts a word across lines except just after a hyphen.
    res["words_split"] = sorted((nid, w) for nid in P if nid in L["boxes"]
                                for src, lines in L["boxes"][nid].wraps for w in split_words(src, lines))
    res["lane_order"] = lane_order
    return res


def findings(chk):
    """Flatten a self-check result into a list of plain findings; empty means the check passed."""
    out = ["boxes overlap: %s and %s" % tuple(p) for p in chk["node_overlaps"]]
    out += ["edge %s runs through box %s" % tuple(p) for p in chk["edges_through_boxes"]]
    out += ["spine edge %s does not run left to right" % e for e in chk["spine_edges_not_left_to_right"]]
    out += ["node %s is not drawn, not a chip and not hidden" % n for n in chk.get("nodes_missing") or []]
    out += ["split parts %s are not in one column" % ", ".join(ms) for ms in chk.get("splits_not_in_one_column") or []]
    out += ["box %s cuts the word %r across lines" % p for p in chk.get("words_split") or []]
    for n, d in sorted((chk.get("span_frame_mismatch") or {}).items()):
        if d["missing"]:
            out.append("cross-process step %s misses the frame(s) it spans: %s" % (n, ", ".join(d["missing"])))
        if d["extra"]:
            out.append("cross-process step %s intersects frame(s) it does not span: %s" % (n, ", ".join(d["extra"])))
    for key, what in (("band_order_ok", "lane bands are out of order"), ("ai_lane_present", "no AI and agents lane"),
                      ("nodes_drawn", "a node was not drawn"), ("edges_drawn", "an edge was not drawn")):
        if not chk[key]:
            out.append(what)
    return out


# ---------------------------------------------------------------- page (CSS only)
# The shared diagram viewer (sizes, full window, open-on-its-own link, steps in words). This renderer runs as a single
# file, so it carries a copy of knowledge/renderer/_diagram_viewer.py's code; test_diagram_viewer_0478 checks it matches.
# ---- viewer code (render_a3.py keeps a byte-identical copy between these markers)
DV_SIZES = (("fit", "Fit width", None), ("50", "50%", 0.5), ("100", "100%", 1.0), ("150", "150%", 1.5), ("200", "200%", 2.0))

VIEWER_CSS = (
    ".dv{margin:8px 0 12px}"
    ".dv-bar{display:flex;flex-wrap:wrap;align-items:center;gap:6px;padding:8px 0;font:600 12px Inter,-apple-system,'Segoe UI',Helvetica,Arial,sans-serif;color:#0B1E2F}"
    ".dv-bar .dv-k{font-weight:500;color:#4A5560;margin-right:2px}"
    ".dv-bar label,.dv-bar a{display:inline-flex;align-items:center;background:#fff;border:1px solid #D9D2C7;border-radius:8px;padding:5px 10px;color:#0B1E2F;cursor:pointer;text-decoration:none}"
    ".dv-bar input{position:fixed;top:0;left:0;width:1px;height:1px;margin:0;opacity:0;pointer-events:none}"
    ".dv-bar label:has(input:checked){border-color:#0B1E2F;background:#FBF6EF}"
    ".dv-bar label:has(input:focus-visible),.dv-bar a:focus-visible{outline:2px solid #F56740;outline-offset:2px}"
    ".dv-bar a{margin-left:auto;font-weight:500}"
    ".dv-bar .dv-x{display:none}"
    ".dv-frame{overflow:auto;max-height:86vh;min-height:240px;background:#fff;border:1px solid #E7DFD3;border-radius:12px}"
    ".dv-frame svg{display:block;max-width:none;height:auto;width:100%}"
    ".dv:has(.dv-full:checked) .dv-bar{position:fixed;top:0;left:0;right:0;z-index:2147483001;margin:0;padding:8px 12px;background:#FBF6EF;border-bottom:1px solid #E7DFD3}"
    ".dv:has(.dv-full:checked) .dv-frame{position:fixed;top:46px;left:0;right:0;bottom:0;max-height:none;z-index:2147483000;border:0;border-radius:0}"
    ".dv:has(.dv-full:checked) .dv-x{display:inline}.dv:has(.dv-full:checked) .dv-o{display:none}"
    ":has(.dv-full:checked){transform:none!important;filter:none!important;perspective:none!important;contain:none!important;will-change:auto!important}"
    ".dv-detail{margin:8px 0 0;font-size:12.5px;color:#4A5560}"
    ".dv-detail summary{cursor:pointer;font-weight:600;color:#0B1E2F}"
    ".dv-detail dl{margin:6px 0 0;display:grid;grid-template-columns:minmax(160px,22%) 1fr;gap:4px 14px}"
    ".dv-detail dt{font-weight:600;color:#0B1E2F}.dv-detail dd{margin:0;white-space:pre-line}"
    + "".join(".dv:has(.dv-z%s:checked) .dv-frame svg{width:calc(var(--dv-w) * %s)}" % (k, s) for k, _l, s in DV_SIZES if s)
    + "@media print{.dv-bar,.dv-detail{display:none}.dv-frame{max-height:none;overflow:visible;border:0}.dv-frame svg{width:100%!important}}"
)

_DV_OPEN = re.compile(r'<g\b[^>]*\bdata-(?:graph-node-id|graph-edge-id|edge-id|id)="([^"]*)"[^>]*>')
_DV_TITLE = re.compile(r"<title>(.*?)</title>", re.S)
_DV_EVENT_ATTR = re.compile(r"(?i)\b(on[a-z]+)(\s*)=")
_DV_JS_URL = re.compile(r"(?i)\b(java|vb|live)(script)(\s*):")


def _dv_text(s):
    """Escape for HTML text and defuse event-attribute and script-URL look-alikes with character references, so the
    page carries neither pattern even as text."""
    t = html.escape(s, quote=True)
    return _DV_JS_URL.sub(r"\1\2\3&#58;", _DV_EVENT_ATTR.sub(r"\1\2&#61;", t))


def detail_items(svg_text):
    """Every step's and link's hover detail, in drawing order: (first line, remaining lines) from each group that
    names a node or an edge and holds its own <title> before its first nested group or its end."""
    out, seen = [], set()
    for m in _DV_OPEN.finditer(svg_text):
        ident = m.group(1)
        if ident in seen:
            continue
        rest = svg_text[m.end():]
        stop = min([i for i in (rest.find("<g"), rest.find("</g>")) if i >= 0] or [len(rest)])
        t = _DV_TITLE.search(rest, 0, stop)
        if not t:
            continue
        text = html.unescape(re.sub(r"<[^>]*>", "", t.group(1))).strip()
        if not text:
            continue
        seen.add(ident)
        first, _, more = text.partition("\n")
        out.append((first.strip(), more.strip()))
    return out


def _dv_relative_href(path):
    """A file path as a relative URL: every character outside [A-Za-z0-9._~/-] percent-encoded, and "./" in front
    unless it already starts with "./" or "../", so no file name can read as a URL scheme (a script or data scheme)."""
    enc = re.sub(r"[^A-Za-z0-9._~/-]", lambda m: "".join("%%%02X" % b for b in m.group(0).encode("utf-8")), str(path))
    return enc if enc.startswith(("./", "../")) else "./" + enc.lstrip("/")


def viewer(svg_markup, width, svg_href=None, detail=None, caption="Diagram", frame_class=""):
    """The viewer around one diagram. `width` is the SVG's drawn width in px (100% = drawn size); `svg_href` is the
    standalone file's path relative to the page, or None when no file is written next to the page; `detail` is
    detail_items() output, or None to leave out the steps-in-words list; `caption` names the frame for assistive technology (None when
    the markup inside already does); `frame_class` adds a class to the frame for a page's own styling."""
    parts = ['<form class="dv" style="--dv-w:%gpx"><div class="dv-bar"><span class="dv-k">Size</span>' % float(width)]
    for size, label, _scale in DV_SIZES:
        parts.append('<label><input type="radio" class="dv-z%s" name="size"%s>%s</label>'
                     % (size, " checked" if size == "fit" else "", label))
    parts.append('<label><input type="checkbox" class="dv-full" name="full"><span class="dv-o">Full window</span>'
                 '<span class="dv-x">Close full window</span></label>')
    if svg_href:
        parts.append('<a class="dv-open" href="%s" target="_blank" rel="noopener">Open the diagram on its own '
                     '(use the browser\'s zoom)</a>' % html.escape(_dv_relative_href(svg_href), quote=True))
    role = (' role="img" aria-label="%s"' % _dv_text(caption)) if caption else ""
    parts.append('</div><div class="dv-frame%s"%s>' % ((" " + frame_class) if frame_class else "", role))
    parts.append(svg_markup)
    parts.append('</div>')
    if detail:
        parts.append('<details class="dv-detail"><summary>Every step and link in words (%d)</summary><dl>' % len(detail))
        for first, more in detail:
            parts.append('<dt>%s</dt><dd>%s</dd>' % (_dv_text(first), _dv_text(more)))
        parts.append('</dl></details>')
    parts.append('</form>')
    return "".join(parts)


def svg_size(svg_text):
    """(width, height) of an SVG from its viewBox, else its width/height attributes, else (1000, 600)."""
    m = re.search(r"<svg\b[^>]*>", svg_text)
    tag = m.group(0) if m else ""
    vb = re.search(r'viewBox="([^"]+)"', tag)
    if vb:
        v = vb.group(1).replace(",", " ").split()
        if len(v) == 4:
            try:
                return float(v[2]), float(v[3])
            except ValueError:
                pass
    w = re.search(r'\swidth="([0-9.]+)', tag)
    h = re.search(r'\sheight="([0-9.]+)', tag)
    return (float(w.group(1)) if w else 1000.0), (float(h.group(1)) if h else 600.0)
# ---- end of viewer code


PAGE_CSS = (f"body{{margin:0;background:{CREAM};color:{MID};font-family:{FONT}}}"
            "header,section{padding:16px 24px}h1{font-size:20px;margin:0 0 4px}"
            f"p.meta{{margin:0;color:{GREY};font-size:12px}}"
            ".canvas{padding:0 24px 16px}"
            f"h2{{font-size:14px;margin:0 0 8px}}ol,ul{{margin:0;padding-left:20px;font-size:13px}}li{{margin:2px 0}}"
            f"code{{font-family:{MONO};font-size:12px}}" + VIEWER_CSS)


def page_doc(svg, B, stem, rep):
    """The HTML page: the SVG inline (its <title> tooltips show on hover), the edge labels that did not fit on
    the diagram, and the renderer's notes. Styling is one <style> block; nothing on the page runs."""
    proc = B.get("process") if isinstance(B.get("process"), dict) else {}
    heading = proc.get("name") or B.get("title") or stem
    variant = str(B.get("variant") or "workflow").replace("-", " ")
    parts = ["<!DOCTYPE html>", '<html lang="en"><head><meta charset="utf-8">',
             '<meta name="viewport" content="width=device-width, initial-scale=1">',
             f'<meta name="generator" content="{RENDERER_ID}">',
             f"<title>{title_text(heading)} · {title_text(variant)}</title><style>{PAGE_CSS}</style></head>",
             f'<body data-renderer="{RENDERER_ID}"><header><h1>{esc(heading)}</h1>',
             f'<p class="meta">{esc(variant.capitalize())} · opens fitted to the width; pick a size or Full window and scroll '
             f'inside the frame, or open the diagram on its own to use the browser\'s zoom · hover a box for its detail, or read '
             f'every step in words under the diagram · renderer <code>{RENDERER_ID}</code></p></header>',
             '<div class="canvas">' + viewer(svg, svg_size(svg)[0], svg_href=stem + ".svg", detail=detail_items(svg),
                                             caption=f"{heading} · {variant}") + '</div>']
    if rep["footnotes"]:
        parts.append("<section><h2>Labels listed by number</h2><ol>")
        parts += [f"<li value=\"{int(n)}\">{esc(text)} <span>({esc(a)} → {esc(z)})</span></li>" for n, text, a, z in rep["footnotes"]]
        parts.append("</ol></section>")
    if rep["hidden"]:
        parts.append("<section><h2>Not drawn</h2><ul>")
        parts += [f"<li>{esc(h['id'])}: {esc(h['reason'])}</li>" for h in rep["hidden"]]
        parts.append("</ul></section>")
    readings = [r for r in B.get("grant_readings") or [] if isinstance(r, dict)]
    if readings:
        said = lambda v: ", ".join(str(x) for x in v) if isinstance(v, list) else str(v if v is not None else "")
        parts.append("<section><h2>Write grant readings</h2><p>Where the transformation map's Write grants table and "
                     "the evidence disagree, the diagram follows the evidence; the map is unchanged.</p><ul>")
        parts += [f"<li>{esc(said(r.get('grant')))} {esc(said(r.get('column')))}: the map's table says "
                  f"{esc(said(r.get('table_says')))}; drawn as {esc(said(r.get('read_as')))}. "
                  f"Why: {esc(said(r.get('reason')))}</li>" for r in readings]
        parts.append("</ul></section>")
    if rep["notes"]:
        parts.append("<section><h2>Renderer notes</h2><ul>")
        parts += [f"<li>{esc(n)}</li>" for n in rep["notes"]]
        parts.append("</ul></section>")
    parts.append("</body></html>\n")
    return "\n".join(parts)


def sha_of(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def out_bundle(B, bundle_path, out, stem):
    """The input bundle plus the render keys render_workflow.py writes (prior_workflow, presentation, render,
    render_html, render_coverage), with pinned paths kept resolvable from OUT_DIR, and the renderer id."""
    B = json.loads(json.dumps(B))
    in_dir = Path(bundle_path).resolve().parent
    B["prior_workflow"] = {"path": os.path.relpath(Path(bundle_path).resolve(), out), "sha256": sha_of(bundle_path)}
    for key in ("snapshot", "current_state"):
        if isinstance(B.get(key), dict) and B[key].get("path"):
            B[key]["path"] = os.path.relpath((in_dir / B[key]["path"]).resolve(), out)
    g = B["graph"]
    B["presentation"] = "diagram"
    B["render"] = {"path": stem + ".svg", "sha256": sha_of(out / (stem + ".svg"))}
    B["render_html"] = {"path": stem + ".html", "sha256": sha_of(out / (stem + ".html"))}
    tables = []
    for t in g.get("decision_tables", []):  # the A3 SVG draws one group per owner; coverage names the first
        own = t["owner"] if isinstance(t["owner"], list) else [t["owner"]]
        tables.append({"graph_id": t["id"], "render_id": "table-%s-%s" % (t["id"], own[0])})
    # Replaced AI uses are not drawn but keep a hidden element (data-hidden) under the same render id.
    B["render_coverage"] = ([{"graph_id": n["id"], "render_id": "node-" + n["id"]} for n in g["nodes"]] +
                            [{"graph_id": e["id"], "render_id": "edge-" + e["id"]} for e in g["edges"]] + tables)
    B["renderer"] = RENDERER_ID
    return B


# ---------------------------------------------------------------- command line
def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("bundle"); ap.add_argument("out_dir")
    ap.add_argument("--stem", default=None); ap.add_argument("--budget", type=int, default=0)
    a = ap.parse_args(argv)
    with open(a.bundle, encoding="utf-8") as f:
        B = json.load(f)
    if not set(EXTENSIONS) & set(B.get("extensions") or []):
        raise SystemExit("bundle declares neither the a3-lanes-v0 nor the a3-lanes-v0.1 extension")
    stem = a.stem or Path(a.bundle).stem
    out = Path(a.out_dir).resolve()
    if (out / (stem + ".json")) == Path(a.bundle).resolve():
        raise SystemExit("output %s.json would overwrite the input bundle; choose another --stem or OUT_DIR" % stem)
    eff = a.budget
    for _ in range(40):  # channel widths are only known after routing; tighten the budget until the canvas fits
        G = render(B, eff, stem)
        if not a.budget or G["W"] <= a.budget:
            break
        eff -= max(16, int(G["W"] - a.budget) // 4)
    svg = svg_doc(G, B)
    chk = self_check(G)
    found = findings(chk)
    out.mkdir(parents=True, exist_ok=True)
    (out / (stem + ".svg")).write_text(svg, encoding="utf-8")
    L = G["L"]
    order = {x["id"]: i for i, x in enumerate(B["graph"]["nodes"] + B["graph"]["edges"])}
    hidden = [{"id": k, "reason": v} for k, v in sorted(L["hidden"].items(), key=lambda kv: order[kv[0]])]
    rep = {"renderer": RENDERER_ID, "hidden": hidden, "canvas": [round(G["W"]), round(G["H"])], "budget": a.budget, "parts": [list(t) for t in L["tiers"]],
           "columns": max(L["col"].values()) + 1, "routes": {k: v["kind"] for k, v in G["routes"].items()},
           "connectors": [c[1] for c in G["CONN"]], "notes": L["notes"],
           "chips": [{"id": s_, "host": h_} for s_, h_ in sorted(L["chips"].items())],
           "footnotes": [list(f) for f in G.get("footnotes") or []],
           "self_check": chk, "findings": found, "model_calls": 0}
    (out / (stem + ".layout.json")).write_text(json.dumps(rep, indent=1), encoding="utf-8")
    (out / (stem + ".html")).write_text(page_doc(svg, B, stem, rep), encoding="utf-8")
    (out / (stem + ".json")).write_text(json.dumps(out_bundle(B, a.bundle, out, stem), indent=2, ensure_ascii=False) + "\n",
                                        encoding="utf-8")
    print(json.dumps({"canvas": rep["canvas"], "parts": rep["parts"], "columns": rep["columns"],
                      "node_overlaps": len(chk["node_overlaps"]), "edges_through_boxes": len(chk["edges_through_boxes"]),
                      "band_order_ok": chk["band_order_ok"], "findings": len(found), "notes": L["notes"]}))
    if found:
        print("self-check findings:\n- " + "\n- ".join(found), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
