"""The one diagram viewer every diagram page uses: CSS only, no script (FORDEEN-87).

    viewer(svg_markup, width, svg_href=None, detail=None, caption="Diagram", frame_class="") -> str
    detail_items(svg_text) -> [(first line, rest)]
    svg_size(svg_text) -> (width, height)
    CSS                                                           the viewer's style rules

What the reader gets, on every page that shows a diagram:
  - a frame that opens fitted to its width, so nothing is cropped on arrival;
  - size steps (Fit width, 50%, 100%, 150%, 200%; 100% is the drawn size), scroll inside the frame to pan;
  - a Full window toggle that pins the frame to the whole browser window (untick it to come back);
  - "Open the diagram on its own", a link to the standalone SVG file written next to the page, where the browser's
    own zoom and pan work;
  - optionally, the full detail of every step and link as a list under the diagram, so the text that shows on hover
    is also reachable from the keyboard and by a screen reader.
Each viewer is its own <form> (no action, nothing to submit), so its radios form their own group without any id: two
diagrams on one page never share a control or an id. Every input sits inside its label; the inputs are fixed-position
and invisible, so focusing one never scrolls the page, and the label carries the focus ring. The state is read with
:has(). The SVG markup is passed in unchanged (callers that prefix ids do that first).

render_a3.py keeps a copy of this module's code (it must run as a single file); a test checks the two match.
Standard library only (html, re), Python 3.9+.
"""
import html
import re

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
