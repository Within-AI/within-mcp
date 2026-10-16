"""One rule for the parts of an agent-written Markdown file that no reader may take as content: fenced code blocks
and HTML comments. Every check that finds a section, a table or an open item in such a file reads through mask(), so
an example inside a fence or a comment never stands in for the real thing.

- A fence opens on a line indented at most three spaces that starts with three or more backticks or tildes (a
  backtick fence whose info string holds a backtick is not a fence). It closes only on a line indented at most three
  spaces holding nothing but the same character, at least as many times; any other fence line inside is content.
- Comment marks are read left to right outside code spans: `<!--` opens a comment and the next `-->` closes it. A
  line that leaves a comment open starts a comment block, which runs to the line that closes it; a line starting
  with `<!--` whose comments all close on it is a one-line comment block. A comment opened and closed after text on
  its line is inline and masks nothing.
- Every line of a block is masked, the opening and closing lines included. A block left open masks to the end.
- problems() names each line no format allows: a block left open, and a line of a comment block holding text
  outside its comments (before, between or after them), which mask() hides. A check that gates a whole file reports
  them.

Also: strip_code_spans() and has_pipe() for the pipe test of the strict table rule (a code span closes only on a
backtick run of the same length; an escaped `\\|` is a character, not a pipe), and is_heading_line() for finding a
named heading (indented at most three spaces).

Three byte-identical copies (also agent-build/scripts/_md_blocks.py), held equal by tests/knowledge/test_md_blocks_0471.py: knowledge/checks/_md_blocks.py
(for the knowledge checks) and skills/future-state-design/scripts/md_blocks.py (for the skill's own checks). Stdlib
only; Python 3.9-safe.
"""
import re

FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
COMMENT_LINE = re.compile(r"^ {0,3}<!--")


def code_spans(text):
    """[(start, end)] of the code spans in `text`: a run of n backticks to the next run of exactly n backticks; a
    run with no such closer is literal text."""
    spans, i, n = [], 0, len(text)
    while i < n:
        if text[i] != "`":
            i += 1
            continue
        j = i
        while j < n and text[j] == "`":
            j += 1
        k, close = j, None
        while k < n:
            if text[k] == "`":
                m = k
                while m < n and text[m] == "`":
                    m += 1
                if m - k == j - i:
                    close = m
                    break
                k = m
            else:
                k += 1
        if close is None:
            i = j
        else:
            spans.append((i, close))
            i = close
    return spans


def comment_marks(text, inside=False):
    """[(position, mark)] of the comment marks in `text` outside code spans, left to right, starting inside a comment
    when `inside`: `<!--` opens a comment (outside one) and `-->` closes it (inside one)."""
    spans, out, i = code_spans(text), [], 0
    while i < len(text):
        span = next((b for a, b in spans if a <= i < b), None)
        if span is not None:
            i = span
        elif not inside and text.startswith("<!--", i):
            out.append((i, "<!--"))
            inside, i = True, i + 4
        elif inside and text.startswith("-->", i):
            out.append((i, "-->"))
            inside, i = False, i + 3
        else:
            i += 1
    return out


def ends_inside(text, inside=False):
    """True when `text` ends inside a comment (starting inside one when `inside`)."""
    marks = comment_marks(text, inside)
    return marks[-1][1] == "<!--" if marks else inside


def blocks(lines):
    """[(start, end, kind, closed)] of the fenced blocks ("fence") and comment blocks ("comment"), in order; `end` is
    the index after the closing line (len(lines) when the block is left open)."""
    out, i, n = [], 0, len(lines)
    while i < n:
        line = lines[i]
        f = FENCE.match(line)
        if f and not (f.group(1)[0] == "`" and "`" in f.group(2)):
            mark, j = f.group(1), i + 1
            while j < n:
                c = FENCE.match(lines[j])
                if c and c.group(1)[0] == mark[0] and len(c.group(1)) >= len(mark) and not c.group(2).strip():
                    break
                j += 1
            out.append((i, min(j + 1, n), "fence", j < n))
            i = j + 1
            continue
        if ends_inside(line):
            j = i + 1
            while j < n and ends_inside(lines[j], inside=True):
                j += 1
            out.append((i, min(j + 1, n), "comment", j < n))
            i = j + 1
            continue
        if COMMENT_LINE.match(line) and comment_marks(line):
            out.append((i, i + 1, "comment", True))
        i += 1
    return out


def mask(lines):
    """Per line, True when it belongs to a fenced block or a comment block (opening and closing lines included)."""
    out = [False] * len(lines)
    for start, end, _kind, _closed in blocks(lines):
        for i in range(start, end):
            out[i] = True
    return out


def unclosed(lines):
    """[(1-based line, kind)] of each fenced block or comment block left open at the end of the file."""
    return [(start + 1, kind) for start, _end, kind, closed in blocks(lines) if not closed]


def visible_text(text, inside=False):
    """The text of `text` outside comments (starting inside one when `inside`), code spans kept as written."""
    out, at = [], 0
    for pos, mark in comment_marks(text, inside):
        if mark == "<!--":
            out.append(text[at:pos])
        at = pos + len(mark)
    if not ends_inside(text, inside):
        out.append(text[at:])
    return "".join(out)


def problems(lines):
    """[(1-based line, message)] of the block lines no format allows: a block left open, and a line of a comment block
    that holds text outside its comments (before, between or after them)."""
    out = []
    for start, end, kind, closed in blocks(lines):
        if not closed:
            out.append((start + 1, "a %s that is never closed, so the rest of the file is hidden"
                        % ("fenced block" if kind == "fence" else "comment")))
            continue
        if kind != "comment":
            continue
        inside = False
        for i in range(start, end):
            if visible_text(lines[i], inside).strip():
                out.append((i + 1, "text on a comment line, outside the comment: put the comment on lines of its "
                                   "own"))
            inside = ends_inside(lines[i], inside)
    return out


def strip_code_spans(text):
    """`text` without its code spans (code_spans)."""
    out, i = [], 0
    for a, b in code_spans(text):
        out.append(text[i:a])
        i = b
    return "".join(out) + text[i:]


def has_pipe(line):
    """A pipe outside code spans; an escaped pipe (`\\|`) is a character, not a pipe."""
    return "|" in strip_code_spans(line.replace("\\|", ""))


def is_heading_line(line, heading):
    """True when `line` is the heading `heading` (e.g. "## Design direction"), indented at most three spaces."""
    return len(line) - len(line.lstrip(" ")) <= 3 and line.strip() == heading


def document_text(text):
    """Visible document interpretation for metadata, sections, counts and excerpts.

    Block examples/comments follow mask(); inline comments follow visible_text().
    Callers retain the original document separately as evidence.
    """
    lines = text.splitlines()
    hidden = mask(lines)
    return "\n".join(visible_text(line) for n, line in enumerate(lines) if not hidden[n])
