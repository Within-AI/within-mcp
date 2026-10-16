"""A run page that fills in as a run goes: scaffold at launch, fill a slot at each stage boundary, gate at the end.

    scaffold(page_dir, slots) -> str      writes <page_dir>/page.html with every slot reading "Not reached yet"
    fill(page_dir, slot, raw_path, html_fragment) -> None
                                          html_fragment None: the raw output at raw_path, escaped in <pre>, first;
                                          a later fill with a fragment replaces it (swapped in whole)
    final_gate(page_dir) -> dict          checks the slot markers on the page as found, marks unreached slots,
                                          writes the page and runs the strict no-script check:
                                          {"ok": bool, "problems": [str]}

`slots` is an ordered list of {"id", "section", "title"}. A section is one left-hand tab (hidden radios, labels and
CSS :checked panels; no script) holding its slots in list order; tabs come in order of first appearance. The caller
supplies every slot, title and raw file; the library fixes only its own names: page.html, page-state.json, slots/.

Every write goes to a temporary file in the same folder and is swapped in with os.replace, and the page is rebuilt
from the saved state each time, so the same state always gives the same bytes. Standard library only, Python 3.9+.
"""
import html
import html.parser
import importlib.util
import json
import os
import re
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PAGE = "page.html"
STATE = "page-state.json"
SLOTS = "slots"
PENDING = "Not reached yet"
GAP = "This part was not produced in this run."
PREPARING = "The plain-language summary is being prepared."
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
MARKER = "data-slot"


class _Markers(html.parser.HTMLParser):
    """Every data-slot attribute value on a real element, in page order (names lowercased, unquoted values read)."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.found = []

    def handle_starttag(self, tag, attrs):
        self.found.extend(value or "" for name, value in attrs if name == MARKER)

    handle_startendtag = handle_starttag


def _markers(text):
    parser = _Markers()
    parser.feed(text)
    parser.close()
    return parser.found


def _write(path, text):
    """Write text to a temporary file beside path, then swap it in whole."""
    folder = os.path.dirname(path)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", suffix=".part", dir=folder)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def _write_json(path, data):
    _write(path, json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n")


def _sections(slots):
    """Validate the slot list; return [(section, [slot, ...]), ...] in order of first appearance."""
    if not isinstance(slots, list) or not slots:
        raise ValueError("slots must be a non-empty list of {id, section, title}")
    order, seen_ids, closed = [], set(), set()
    for s in slots:
        if not isinstance(s, dict) or any(not isinstance(s.get(k), str) or not s.get(k) for k in ("id", "section", "title")):
            raise ValueError("each slot needs non-empty string id, section and title: %r" % (s,))
        if not _ID.match(s["id"]):
            raise ValueError("slot id %r: use letters, digits, '.', '_' or '-'" % s["id"])
        if s["id"] in seen_ids:
            raise ValueError("slot %r is listed twice" % s["id"])
        seen_ids.add(s["id"])
        if order and order[-1][0] == s["section"]:
            order[-1][1].append(s)
            continue
        if s["section"] in closed:
            raise ValueError("section %r: its slots are not next to each other in the list" % s["section"])
        if order:
            closed.add(order[-1][0])
        order.append((s["section"], [s]))
    return order


def _load_state(page_dir):
    path = os.path.join(page_dir, STATE)
    if not os.path.isfile(path):
        raise FileNotFoundError("no %s in %s: run scaffold first" % (STATE, page_dir))
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _slot_body(page_dir, slot_id, gated):
    path = os.path.join(page_dir, SLOTS, slot_id + ".json")
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)["body"]
    if gated:
        return '<p class="pp-gap">%s</p>' % html.escape(GAP)
    return '<p class="pp-pending">%s</p>' % html.escape(PENDING)


def _render(page_dir, state):
    """The whole page from the saved state; the same state gives the same bytes."""
    with open(os.path.join(HERE, "shell.html"), encoding="utf-8") as f:
        shell = f.read()
    with open(os.path.join(HERE, "shell.css"), encoding="utf-8") as f:
        css = f.read().rstrip("\n")
    sections = _sections(state["slots"])
    radios, labels, panels, tab_css = [], [], [], []
    for n, (name, members) in enumerate(sections, 1):
        tid = "pp-t-%d" % n
        radios.append('<input type="radio" name="pp-tab" id="%s" class="pp-tab-radio"%s>'
                      % (tid, " checked" if n == 1 else ""))
        labels.append('<label for="%s">%s</label>' % (tid, html.escape(name)))
        parts = ['<section class="pp-panel" id="pp-panel-%d">' % n, "<h2>%s</h2>" % html.escape(name)]
        for s in members:
            parts.append('<div class="pp-slot" data-slot="%s">\n<h3>%s</h3>\n%s\n</div>'
                         % (s["id"], html.escape(s["title"]), _slot_body(page_dir, s["id"], state.get("gated", False))))
        parts.append("</section>")
        panels.append("\n".join(parts))
        tab_css.append("#%s:checked ~ .pp-main #pp-panel-%d { display:block; }" % (tid, n))
        tab_css.append("#%s:checked ~ .pp-tabs label[for=%s] { background:var(--white); border-left-color:var(--coral); "
                       "font-weight:600; box-shadow:var(--shadow-card); }" % (tid, tid))
        tab_css.append("#%s:focus-visible ~ .pp-tabs label[for=%s] { outline:2px solid var(--coral); outline-offset:2px; }"
                       % (tid, tid))
    out = shell
    for key, value in (("{{CSS}}", css), ("{{TAB_CSS}}", "\n".join(tab_css)), ("{{RADIOS}}", "\n".join(radios)),
                       ("{{LABELS}}", "\n".join(labels)), ("{{PANELS}}", "\n".join(panels))):
        out = out.replace(key, value)
    return out


def _rebuild(page_dir, state):
    path = os.path.join(page_dir, PAGE)
    _write(path, _render(page_dir, state))
    return path


def scaffold(page_dir, slots):
    """Write <page_dir>/page.html with every slot reading "Not reached yet"; return its path."""
    _sections(slots)
    os.makedirs(os.path.join(page_dir, SLOTS), exist_ok=True)
    for name in sorted(os.listdir(os.path.join(page_dir, SLOTS))):
        if name.endswith(".json"):
            os.unlink(os.path.join(page_dir, SLOTS, name))
    state = {"slots": [{"id": s["id"], "section": s["section"], "title": s["title"]} for s in slots], "gated": False}
    _write_json(os.path.join(page_dir, STATE), state)
    return _rebuild(page_dir, state)


def fill(page_dir, slot, raw_path, html_fragment):
    """Fill one slot: the raw output first (html_fragment None), or the plain-words fragment, swapped in whole."""
    state = _load_state(page_dir)
    if slot not in [s["id"] for s in state["slots"]]:
        raise ValueError("slot %r was not declared by the scaffold" % (slot,))
    if html_fragment is None:
        if not raw_path or not os.path.isfile(raw_path):
            raise FileNotFoundError("raw output file not found: %s" % (raw_path,))
        with open(raw_path, encoding="utf-8", errors="replace") as f:
            raw = f.read()
        body = '<pre class="pp-raw">%s</pre>\n<p class="pp-note">%s</p>' % (html.escape(raw), html.escape(PREPARING))
        kind = "raw"
    else:
        body = str(html_fragment)
        kind = "plain"
    _write_json(os.path.join(page_dir, SLOTS, slot + ".json"), {"body": body, "kind": kind})
    _rebuild(page_dir, state)


def _no_script():
    path = os.path.join(HERE, "..", "..", "checks", "_no_script.py")
    spec = importlib.util.spec_from_file_location("_progressive_no_script", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def final_gate(page_dir):
    """Check the page as found, mark every unreached slot, write the page and run the strict no-script check."""
    found = []
    state = _load_state(page_dir)
    ids = [s["id"] for s in state["slots"]]
    page_path = os.path.join(page_dir, PAGE)
    try:
        with open(page_path, encoding="utf-8") as f:
            text = f.read()
    except OSError as exc:
        return {"ok": False, "problems": ["page.html could not be read: %s" % type(exc).__name__]}
    markers = _markers(text)   # attributes only: marker-shaped prose, comments and style text do not count
    for i in ids:
        n = markers.count(i)
        if n == 0:
            found.append("slot marker data-slot=%r is missing from page.html" % i)
        elif n > 1:
            found.append("slot marker data-slot=%r appears %d times in page.html" % (i, n))
    for m in sorted(set(markers) - set(ids)):
        found.append("slot marker data-slot=%r was not declared by the scaffold" % m)
    seen = [m for m in markers if m in ids and markers.count(m) == 1]
    if seen != [i for i in ids if i in seen]:
        found.append("slot markers are out of scaffold order in page.html: %s" % ", ".join(seen))
    state["gated"] = True
    _write_json(os.path.join(page_dir, STATE), state)
    if found:
        # The page on disk is kept as found (a lost marker is reported, never restored); only unreached text changes.
        pending = '<p class="pp-pending">%s</p>' % html.escape(PENDING)
        out = text.replace(pending, '<p class="pp-gap">%s</p>' % html.escape(GAP))
        _write(page_path, out)
    else:
        out = _render(page_dir, state)
        _write(page_path, out)
    for line, kind, detail in _no_script().problems(out):
        found.append("no-script check, line %s: %s: %s" % (line, kind, detail))
    return {"ok": not found, "problems": found}
