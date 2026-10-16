r"""Check that an HTML page the ADK writes carries no JavaScript (FORDEEN-87).

Every page the plugin writes is pure HTML and CSS, so it can be sent by email or Slack without a mail filter
flagging it. The check does not hunt for script; it accepts one strict, known-safe page shape and fails anything
outside it. It reads the page with Python's own HTML parser (no pattern pass over the raw text for script), and
odd input fails rather than passes.

The accepted shape:

  markup       every "<" in the page opens a start tag, an end tag, a comment or the doctype. A stray or malformed
               "<", a "<" inside a tag's own source text (even inside a quoted value), text left unparsed at the
               end and a NUL character all fail.
  elements     no script (start or end tag, anywhere, inline SVG included). No iframe, frame, frameset, object,
               embed, applet, param, base, portal, xmp, plaintext, noembed or noframes. A meta http-equiv must be
               "Content-Security-Policy" (it only restricts the page) or "refresh", with content "<seconds>" or
               "<seconds>; url=<allowed URL>"; any other (set-cookie, ...) fails. No SVG animation (animate,
               set, ...) that targets a link, style, srcdoc or an on* attribute.
  attributes   no attribute repeated on one element; no attribute whose name starts with "on"; no srcdoc,
               whatever it holds (it is never decoded); no attribute value that starts with javascript: or vbscript:;
               every url(...) in an SVG attribute that takes a URL (fill, stroke, marker, marker-start, marker-mid,
               marker-end, clip-path, mask, filter, cursor) and in an SVG animation's to, from, by or values is
               an allowed URL or a same-document #fragment; those values are also read as CSS (image-set(...)
               included, as under URLs below). Text
               attributes (title, aria-*, alt, data-*, value, placeholder, ...) are text: "url(" there is not read.
  URLs         every URL-bearing attribute (href and any *:href, src, srcset entries, action, formaction, data,
               poster, background, cite, longdesc, usemap, manifest, ping, ...) and every CSS url(...), @import
               string and image-set(...) string, in style attributes and <style> blocks, once entities and CSS
               escapes are decoded and spaces and control characters removed, is relative, a fragment, http:,
               https:, mailto: or data:image/(png|jpeg|gif|webp). Anything else fails (javascript:, vbscript:,
               data:text/html, data:image/svg+xml, ...), as do a url( or CSS string that does not close cleanly
               and CSS that names javascript:, vbscript:, expression(, behavior: or -moz-binding outside a string.
               Every string and url(...) inside image-set(...), up to its matching ")", is checked; an escaped
               bracket (\( or \)) is part of a name, not a bracket. Inside image-set(...) the only functions
               allowed are url( and type(, written without escapes: any other (var(, attr(, a custom --function(,
               ...) can substitute a candidate the check cannot see, so it fails. A <style> block is checked both as written (an HTML
               browser does not decode entities there) and with entities decoded (inline SVG does).
               CSS is tokenized as a browser does: a comment is not read inside a string, and text in a string
               (content:"...") is not a URL unless url(, @import or image-set( takes it.
  raw text     the content of style, textarea and title (inline SVG included) holds no "<", even once entities
               are decoded, and no tag or comment. One left open, or written self-closing (<style/>), fails.
  comments     hold no "<" and no ">", and end with a plain "-->" (so <!-->, <!--->, --!> and an unclosed comment
               fail). The only other declaration allowed is <!doctype html>; <!x>, <?x>, <![CDATA[ and "</" not
               followed by a letter fail.

`problems(html)` returns every finding as sorted (line, kind, detail) tuples; `assert_no_script(html, where)` raises
NoScriptError naming them, and renderers call it before writing a page. The command-line check is
check-no-script.py next to the shared copy of this file. Byte-identical copies sit with the run page kit and the
agent-recommendation skill, so a kit or skill copied out of the plugin runs the same check; a test keeps the three
identical. Standard library only, Python 3.9 and later.
"""
import html as _html
import re
from html.parser import HTMLParser

_SPACE = re.compile(r"[\x00-\x20\x7f]+")
_SCHEME = re.compile(r"([a-zA-Z][a-zA-Z0-9+.\-]*):")
_DATA_IMAGE = re.compile(r"data:image/(?:png|jpeg|gif|webp)[;,]", re.I)
_DOCTYPE = re.compile(r"doctype\s+html\s*", re.I)
_SCRIPT_SCHEMES = ("javascript:", "vbscript:", "livescript:")

BANNED = {"iframe", "frame", "frameset", "object", "embed", "applet", "param", "base", "portal", "xmp", "plaintext",
          "noembed", "noframes"}
RAW_TEXT = {"style", "textarea", "title"}
ANIMATION = {"animate", "set", "animatemotion", "animatetransform", "animatecolor"}
URL_ATTRS = {"href", "src", "action", "formaction", "data", "poster", "background", "cite", "longdesc", "usemap",
             "manifest", "codebase", "classid", "code", "archive", "profile", "icon", "lowsrc", "dynsrc",
             "xml:base", "pluginspage"}
URL_LIST_ATTRS = {"ping", "itemtype"}          # whitespace-separated URLs
SRCSET_ATTRS = {"srcset", "imagesrcset"}
SVG_URL_ATTRS = {"fill", "stroke", "marker", "marker-start", "marker-mid", "marker-end", "clip-path", "mask", "filter",
                 "cursor"}                     # SVG presentation attributes that take url(...)
ANIMATION_VALUE_ATTRS = {"to", "from", "by", "values"}   # an animation sets its target to these, url(...) included
COMMENT_DETAIL = "a comment holding '<' or '>' (or <!-->, <!--->, --!>)"


class NoScriptError(ValueError):
    """A page falls outside the accepted shape, so it may carry script."""


def url_problem(value):
    """None when `value` (entities already decoded) is an allowed URL, else the finding kind."""
    flat = _SPACE.sub("", value or "")
    m = _SCHEME.match(flat)
    if not m:
        return None                                   # relative, or a fragment
    scheme = m.group(1).lower()
    if scheme in ("http", "https", "mailto"):
        return None
    if scheme == "data" and _DATA_IMAGE.match(flat):
        return None
    return "javascript URL" if scheme + ":" in _SCRIPT_SCHEMES else "URL not allowed"


def srcset_urls(value):
    """The URL of each srcset candidate: a run of non-space characters, trailing commas dropped."""
    out, i, n, space = [], 0, len(value), " \t\n\r\f"
    while i < n:
        while i < n and (value[i] == "," or value[i] in space):
            i += 1
        j = i
        while j < n and value[j] not in space:
            j += 1
        url = value[i:j].rstrip(",")
        if url:
            out.append(url)
        if value[i:j].endswith(","):
            i = j
            continue
        while j < n and value[j] != ",":              # the descriptors, up to the next comma
            if value[j] == "(":
                return out + [value[j:]]              # a bracket in a descriptor: odd, checked as a URL so it fails
            j += 1
        i = j + 1
    return out


def _css_escape(m):
    hexa, other = m.group(1), m.group(2)
    if hexa:
        code = int(hexa, 16)
        return "\ufffd" if code == 0 or code > 0x10FFFF or 0xD800 <= code <= 0xDFFF else chr(code)
    return "" if other in "\r\n\f" else other


def css_decode(css):
    """CSS escapes decoded (\\6a -> j, \\: -> :, an escaped newline dropped). Comments are left in place."""
    return re.sub(r"\\(?:([0-9a-fA-F]{1,6})(?:\r\n|[ \t\r\n\f])?|([\s\S]))", _css_escape, css)


_ESCAPE = re.compile(r"\\(?:([0-9a-fA-F]{1,6})(?:\r\n|[ \t\r\n\f])?|([\s\S]))")


def css_tokens(css):
    """(code, strings, urls, odd, brackets): CSS read the way a browser tokenizes it. CODE is the CSS outside strings
    and comments with escapes decoded, each string replaced by \x00<n>\x00 and each unquoted url( value by \x01<n>\x01;
    STRINGS and URLS hold their decoded values; ODD names anything outside the strict shape (a string or url( that
    does not close, a quote or bracket inside an unquoted url, a control placeholder character in the CSS). BRACKETS
    is CODE with each escaped character replaced by \x02, same length: an escaped bracket is part of a name, not a
    bracket, and an escaped name is never read as url or type."""
    code, strings, urls, odd, brackets = [], [], [], [], []
    tail = ""
    i, n = 0, len(css)
    if "\x00" in css or "\x01" in css:
        odd.append("a control character in the CSS")
    while i < n:
        c = css[i]
        if c == "\\":
            m = _ESCAPE.match(css, i)
            ch = _css_escape(m) if m else ""
            i = m.end() if m else i + 1
            code.append(ch)
            brackets.append("\x02" if ch else "")
            tail = (tail + ch)[-24:]
            continue
        if css.startswith("/*", i):
            end = css.find("*/", i + 2)
            i = n if end < 0 else end + 2
            code.append(" ")
            brackets.append(" ")
            tail = (tail + " ")[-24:]
            continue
        if c in "\"'":
            j, val = i + 1, []
            while j < n and css[j] != c and css[j] not in "\r\n\f":
                if css[j] == "\\":
                    m = _ESCAPE.match(css, j)
                    val.append(_css_escape(m))
                    j = m.end()
                else:
                    val.append(css[j])
                    j += 1
            if j >= n or css[j] != c:
                odd.append("a CSS string that does not close")
            strings.append("".join(val))
            mark = "\x00%d\x00" % (len(strings) - 1)
            code.append(mark)
            brackets.append(mark)
            tail = (tail + mark)[-24:]
            i = j + 1
            continue
        if c not in " \t\r\n\f" and re.search(r"url\(\s*\Z", tail, re.I):
            j, val = i, []
            while j < n and css[j] != ")":
                if css[j] == "\\":
                    m = _ESCAPE.match(css, j)
                    val.append(_css_escape(m))
                    j = m.end()
                    continue
                if css[j] in "\"'(" or (css[j] in " \t\r\n\f" and css[j:].lstrip(" \t\r\n\f")[:1] != ")"):
                    odd.append("a quote, bracket or space inside an unquoted url(")
                    break
                val.append(css[j])
                j += 1
            if j >= n:
                odd.append("a url( that does not close")
            urls.append("".join(val).strip())
            mark = "\x01%d\x01" % (len(urls) - 1)
            code.append(mark)
            brackets.append(mark)
            tail = (tail + mark)[-24:]
            i = j
            continue
        code.append(c)
        brackets.append(c)
        tail = (tail + c)[-24:]
        i += 1
    return "".join(code), strings, urls, odd, "".join(brackets)


def css_problems(css):
    """Findings in a style attribute or a <style> block: disallowed URLs and CSS that runs code. Text inside a CSS
    string (content:"...") is text, not a URL, unless url(, @import or image-set( takes it."""
    found = []
    code, strings, urls, odd, brackets = css_tokens(css)
    for what in odd:
        found.append(("URL not allowed", what))
    flat = _SPACE.sub("", code).lower()
    for bad in ("expression(", "behavior:", "-moz-binding") + _SCRIPT_SCHEMES:
        if bad in flat:
            found.append(("javascript URL" if bad in _SCRIPT_SCHEMES else "URL not allowed", "CSS naming %s" % bad))

    def check(url, form):
        kind = url_problem(url)
        if kind:
            found.append((kind, form % _SPACE.sub("", url)[:60]))
    for m in re.finditer(r"url\(", code, re.I):
        rest = code[m.end():]
        q = re.match(r"\s*\x00(\d+)\x00(\s*\))?", rest)
        u = re.match(r"\s*\x01(\d+)\x01(\s*\))?", rest)
        if q:
            check(strings[int(q.group(1))], "url(%s)")
        elif u:
            check(urls[int(u.group(1))], "url(%s)")
        if not (q or u) or not (q or u).group(2):
            found.append(("URL not allowed", "a url( that does not close cleanly"))
    for m in re.finditer(r"@import\s*\x00(\d+)\x00", code, re.I):
        check(strings[int(m.group(1))], "@import %s")
    for m in re.finditer(r"image-set\(", code, re.I):
        if brackets[m.end() - 1] != "(":
            continue                                  # image-set\( is a name, not the function
        depth, j = 1, m.end()                         # every candidate up to the matching ")", url(...) ones included
        while j < len(brackets) and depth:
            depth += {"(": 1, ")": -1}.get(brackets[j], 0)
            j += 1
        if depth:
            found.append(("URL not allowed", "an image-set( that does not close"))
        for f in re.finditer(r"([^\s(),\x00\x01]*)\(", brackets[m.end():j]):
            if f.group(1).lower() not in ("url", "type"):
                found.append(("URL not allowed", "an image-set( holding %s( (only url( and type( are allowed)"
                              % f.group(1).replace("\x02", "\\")[:30]))
        for k in re.findall(r"\x00(\d+)\x00", code[m.end():j]):
            check(strings[int(k)], "image-set(%s)")
    return found


def attribute_url_problems(value):
    """url(...) in an SVG attribute that takes a URL (SVG_URL_ATTRS): each one, CSS escapes decoded, must be an allowed
    URL; a same-document #fragment is allowed. A url( that does not close fails."""
    found = []
    text = css_decode(value)
    for m in re.finditer(r"url\(", text, re.I):
        u = re.match(r"\s*(\"[^\"]*\"|'[^']*'|[^\"'()\s]*)\s*\)", text[m.end():])
        if not u:
            found.append(("URL not allowed", "a url( that does not close cleanly"))
            continue
        url = u.group(1)[1:-1] if u.group(1)[:1] in "\"'" else u.group(1)
        kind = url_problem(url)
        if kind:
            found.append((kind, "url(%s)" % _SPACE.sub("", url)[:60]))
    return found


class _Shape(HTMLParser):
    # The same parse on every Python: only script and style are read as raw text (later versions read more elements
    # as raw text, and textarea and title as escapable raw text); textarea and title are checked by hand below.
    CDATA_CONTENT_ELEMENTS = ("script", "style")
    RCDATA_CONTENT_ELEMENTS = ()

    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.found = []
        self.raw = None            # [tag, line, pieces] while inside style, textarea or title

    def add(self, kind, detail, line=None):
        self.found.append((line or self.getpos()[0], kind, detail))

    # --- tags ---
    def _tag(self, tag, attrs):
        tag = tag.lower()
        source = self.get_starttag_text() or ""
        if "<" in source[1:]:
            self.add("markup", "a '<' inside the <%s> tag" % tag)
        if self.raw is not None:
            self.add("raw text", "<%s> inside <%s>" % (tag, self.raw[0]))
        if tag == "script":
            self.add("script tag", "<script>")
        elif tag in BANNED:
            self.add("element not allowed", "<%s>" % tag)
        values = {}
        for name, value in attrs:
            name, value = (name or "").lower(), value or ""
            if name in values:
                self.add("markup", "<%s> repeats the attribute %s" % (tag, name))
                continue
            values[name] = value
            if name.startswith("on"):
                self.add("event handler", "<%s %s=...>" % (tag, name))
            elif name == "srcdoc":
                self.add("srcdoc", "<%s srcdoc=...>" % tag)
            elif name == "style":
                for kind, detail in css_problems(value):
                    self.add(kind, "<%s style> %s" % (tag, detail))
            elif name in URL_ATTRS or name.endswith(":href"):
                self._url(tag, name, value)
            elif name in SRCSET_ATTRS:
                for url in srcset_urls(value):
                    self._url(tag, name, url)
            elif name in URL_LIST_ATTRS:
                for url in value.split():
                    self._url(tag, name, url)
            elif _SPACE.sub("", value).lower().startswith(_SCRIPT_SCHEMES):
                self.add("javascript URL", "<%s %s=...>" % (tag, name))
            if name in SVG_URL_ATTRS or (tag in ANIMATION and name in ANIMATION_VALUE_ATTRS):
                for kind, detail in sorted(set(attribute_url_problems(value) + css_problems(value))):
                    self.add(kind, "<%s %s> %s" % (tag, name, detail))
        if tag == "meta" and "http-equiv" in values:
            self._meta(values)
        if tag in ANIMATION:
            target = values.get("attributename", "").strip().lower()
            if (not target or target.startswith("on") or target in URL_ATTRS or target.endswith("href")
                    or target in SRCSET_ATTRS or target in ("style", "srcdoc")):
                self.add("element not allowed", "<%s attributeName=%r>" % (tag, target))
        return tag

    def _url(self, tag, name, value):
        kind = url_problem(value)
        if kind:
            self.add(kind, "<%s %s=%s>" % (tag, name, _SPACE.sub("", value)[:60]))

    def _meta(self, values):
        equiv = values["http-equiv"].strip().lower()
        if equiv == "content-security-policy":
            return                                    # a CSP only restricts what the page may load or run
        if equiv != "refresh":
            self.add("element not allowed", "<meta http-equiv=%r>" % values["http-equiv"][:40])
            return
        m = re.fullmatch(r"\s*\d+(?:\.\d*)?\s*(?:[;,]\s*(?:url\s*=\s*)?(.*))?", values.get("content", ""), re.I | re.S)
        if not m:
            self.add("element not allowed", "<meta http-equiv=refresh> with odd content")
            return
        url = (m.group(1) or "").strip().strip("'\"")
        kind = url_problem(url)
        if kind:
            self.add(kind, "<meta http-equiv=refresh url=%s>" % _SPACE.sub("", url)[:60])

    def handle_starttag(self, tag, attrs):
        tag = self._tag(tag, attrs)
        if tag in RAW_TEXT and self.raw is None:
            self.raw = [tag, self.getpos()[0], []]

    def handle_startendtag(self, tag, attrs):
        tag = self._tag(tag, attrs)
        if tag in RAW_TEXT:
            self.add("raw text", "<%s/> written self-closing (an HTML browser opens it as raw text)" % tag)

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag == "script":
            self.add("script tag", "</script>")
        if self.raw is not None:
            if tag == self.raw[0]:
                self._close_raw()
            else:
                self.add("raw text", "</%s> inside <%s>" % (tag, self.raw[0]))

    def _close_raw(self):
        tag, line, pieces = self.raw
        self.raw = None
        decoded = _html.unescape("".join(pieces))    # raw source: entities arrive as &name; and &#n;
        if "<" in decoded:                            # style, textarea, title: no "<", even once decoded
            self.add("raw text", "a '<' inside <%s>" % tag, line)
        if tag == "style":                            # as written (HTML) and decoded (inline SVG): both must pass
            for kind, detail in sorted(set(css_problems("".join(pieces)) + css_problems(decoded))):
                self.add(kind, "<style> %s" % detail, line)

    # --- text ---
    def handle_data(self, data):
        if self.raw is not None:
            self.raw[2].append(data)
        elif "<" in data:
            stray = data[data.index("<"):]
            if re.match(r"<\s*/?\s*script", stray, re.I):
                self.add("script tag", "<script> the parser read as text")
            else:
                self.add("markup", "stray or malformed %r" % stray[:30])

    def handle_entityref(self, name):
        if self.raw is not None:
            self.raw[2].append("&%s;" % name)

    def handle_charref(self, name):
        if self.raw is not None:
            self.raw[2].append("&#%s;" % name)

    # --- declarations and comments ---
    def handle_comment(self, data):
        if "<" in data or ">" in data:
            self.add("comment", COMMENT_DETAIL)
        if self.raw is not None:
            self.add("raw text", "a comment inside <%s>" % self.raw[0])

    def handle_decl(self, decl):
        if self.raw is not None:
            self.add("raw text", "a declaration inside <%s>" % self.raw[0])
        if not _DOCTYPE.fullmatch(decl):
            self.add("declaration", "<!%s>" % decl[:40])

    def unknown_decl(self, data):
        self.add("declaration", "<![%s" % data[:40])

    def handle_pi(self, data):
        self.add("declaration", "<?%s" % data[:40])


def _line(text, pos):
    return text.count("\n", 0, pos) + 1


def _source_problems(text):
    """Comments and declarations, read from the source itself: a parser may end a malformed comment where a browser
    does not. This pass only ever adds findings."""
    found = []
    if "\x00" in text:
        found.append((_line(text, text.index("\x00")), "markup", "a NUL character"))
    for m in re.finditer(r"<[!?/]", text):
        start, lead = m.start(), m.group()
        if lead == "</":
            if not re.match(r"</[a-zA-Z]", text[start:start + 3]):
                found.append((_line(text, start), "declaration", "'</' not followed by a letter"))
        elif lead == "<?":
            found.append((_line(text, start), "declaration", "<?...>"))
        elif text.startswith("<!--", start):
            end = text.find("-->", start + 4)
            if end < 0:
                found.append((_line(text, start), "comment", "a comment that does not close with -->"))
                continue
            body = text[start + 4:end]
            if "<" in body or ">" in body:
                found.append((_line(text, start), "comment", COMMENT_DETAIL))
        elif not re.match(r"<!doctype\s+html\s*>", text[start:start + 40], re.I):
            found.append((_line(text, start), "declaration", "<!%s" % text[start + 2:start + 20]))
    return found


def problems(text):
    """Every way `text` falls outside the accepted shape, as sorted (line, kind, detail) tuples."""
    shape = _Shape()
    left = ""
    try:
        shape.feed(text)
        # Read what the parser could not consume before close(): newer parsers drop an unfinished tag at the end.
        left = shape.rawdata
        shape.close()
    except Exception as exc:  # noqa: BLE001 - the parser gave up: odd input fails
        shape.found.append((shape.getpos()[0], "markup", "the parser stopped: %s" % type(exc).__name__))
    if shape.raw is not None:
        shape.found.append((shape.raw[1], "raw text", "<%s> that does not close" % shape.raw[0]))
    if shape.cdata_elem or shape.rawdata or left.strip():
        shape.found.append((shape.getpos()[0], "markup", "text left unparsed at the end of the page"))
    return sorted(set(shape.found + _source_problems(text)))


def assert_no_script(text, where="page"):
    """Raise NoScriptError when `text` falls outside the accepted shape; return `text` unchanged otherwise."""
    found = problems(text)
    if found:
        listed = "; ".join("line %d: %s %s" % f for f in found[:10])
        more = "" if len(found) <= 10 else " (and %d more)" % (len(found) - 10)
        raise NoScriptError("%s carries JavaScript or falls outside the no-script page shape, which pages must not: %s%s"
                            % (where, listed, more))
    return text
