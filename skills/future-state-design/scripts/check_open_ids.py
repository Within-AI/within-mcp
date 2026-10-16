#!/usr/bin/env python3
"""One open-id register: later stages must reuse the ids of the opens raised in the Current state Workflow.

Usage: check_open_ids.py RUN_ROOT [--threshold 0.6]

Register = `issues[].id` of stages/current-state/current-state.input.json. Other representations scanned:
  - stages/transformation-plan/transformation-map.md  (every `issue-...` token; `- issue-x: text` lines carry a reason)
  - stages/future-state/future-state.input.json       (`issues[].id` and `.reason`)
Reports (a) ids used downstream that are not in the register, and (b) pairs of DIFFERENT ids whose reason texts
overlap (word overlap coefficient >= threshold; short map lines against long register reasons): the same open under two ids. Exit 0 if no pair found, 1 otherwise, 2 if the
register cannot be read. Deterministic; stdlib only. It never edits anything.

The map's What We Still Need to Know section has a fixed format (read_open_items): each open item is a top-level
`- <id>: <reason>` bullet; any shape the format does not allow fails (open-items-shape, naming its line) whenever the
map exists, so the section is never guessed at. Ids inside fenced blocks and HTML comments are never open items, and
their reasons never enter the overlap scan.

Open items closed in the future state (when the future-state file exists and the section's shape passes): the map's
open items are the `- <id>: <reason>` bullets of its What We Still Need to Know section. Each must be kept (same id in the future
state's `graph.issues[]`, read from the graph only, never from a top-level `issues` list) or recorded in its top-level `opens_closed` list ({id, resolution same_as | found_false,
by, reason}). A well-formed same_as rename (old id -> an id kept in graph.issues) declares the
two ids the same open, so that pair is not reported in same_open_under_two_ids. Failures go to `findings` (rule, where, message) and exit 1. A map whose section has no such bullet
is flagged `open-items-not-listed` (older maps keep passing until they list their open items).
"""
import json, os, re, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import md_blocks  # noqa: E402  fenced blocks and comments: one rule for every reader

TOKEN = re.compile(r"\bissue-[a-z0-9][a-z0-9-]*[a-z0-9]\b")
STOP = set("the a an of to and or in on is are for be by with as at that this it not no from per which what whether needs more information".split())


def words(t):
    return {w for w in re.findall(r"[a-z0-9]+", t.lower()) if w not in STOP and len(w) > 2}


def overlap(a, b):
    return len(a & b) / min(len(a), len(b)) if a and b else 0.0


def load_issues(path):
    d = json.load(open(path))
    issues = d.get("issues") or (d.get("graph") or {}).get("issues") or []
    return {i["id"]: (i.get("reason") or "") for i in issues if isinstance(i, dict) and "id" in i}


OPEN_HEAD = re.compile(r"^(#{1,6})\s+.*still need to know", re.I)
OPEN_BULLET = re.compile(r"^[-*]\s+(?:`([A-Za-z0-9][A-Za-z0-9_.-]*)`|([A-Za-z0-9][A-Za-z0-9_.-]*))\s*:\s*\S")
# A bullet shaped like an open item (a first word, however marked up, then a colon) that OPEN_BULLET refuses: an
# empty reason, an id with other characters, doubled or unmatched backticks, bold.
RECORDISH = re.compile(r"^[-*]\s+\S+?\s*:")
INDENTED_OPEN_HEAD = re.compile(r"^ {1,3}#{1,6}\s+.*still need to know", re.I)
HEADING = re.compile(r"^(#{1,6})(\s|$)")
# Lines the section's format does not allow, each with what to write instead. Checked in order, on lines outside
# fenced blocks and comments, after tabs become four spaces.
REJECT = [
    (re.compile(r"^ +([-*+]|\d{1,9}[.)])(\s|$)"), "an indented list item: write each open item as a top-level "
     "`- <id>: <reason>` bullet at the start of the line, and supporting detail as plain indented text"),
    (re.compile(r"^(\+|\d{1,9}[.)])(\s|$)"), "a numbered or `+` list item: write open items as `- <id>: <reason>`"),
    (re.compile(r"^ {0,3}>"), "a quote: write the note as plain text"),
    (re.compile(r"^ {1,3}#"), "an indented heading: start headings at the beginning of the line"),
    (re.compile(r"^ {0,3}(=+|-{1,2})\s*$|^ {1,3}(-{3,}|\*{3,}|_{3,})\s*$"),
     "an underline or indented rule, which Markdown may read as a heading: use `---` at the start of a line"),
    (md_blocks.comment_marks, "a comment sharing its line with text: put the comment on lines of its own"),
]
RESOLUTIONS = ("same_as", "found_false")


def read_open_items(text):
    """(open item ids, shape problems) of the map's What We Still Need to Know section: the first heading outside
    fenced blocks and comments whose title ends in "still need to know", to the next heading of the same or a higher
    level (subsections stay in it).

    The section has a fixed format, checked line by line and never interpreted: an open item is a top-level
    `- <id>: <reason>` bullet at the start of a line (`*` also accepted). Also allowed: other top-level bullets,
    plain text (indented or not), blank lines, `---` rules, deeper headings, table rows, and fenced blocks and HTML
    comments that start at the beginning of a line (md_blocks; their content never counts). Any other shape (an
    indented or numbered list item, a quote, an indented heading or fence, an underline, a comment sharing its line
    with text, a bullet shaped like an open item that is not a well-formed one) is a problem naming its 1-based line,
    as is any block problem anywhere in the map (md_blocks.problems), an indented What We Still Need to Know heading,
    or a second such section at any level. Problems are [(line, message)]. A bullet whose text before its first colon
    has a space (prose, or several ids on one bullet) is prose: no shape rule can tell the two apart, so each open item
    needs its own bullet, as the stage instructions say."""
    lines = text.expandtabs(4).splitlines()
    hidden = md_blocks.mask(lines)
    problems = list(md_blocks.problems(lines))
    ids, level, done = [], None, False
    starts = {start: (end, kind) for start, end, kind, _closed in md_blocks.blocks(lines)}
    for i, ln in enumerate(lines):
        if i in starts and level is not None and not done:
            end, kind = starts[i]
            if ln[:1] == " ":
                problems.append((i + 1, "an indented %s: start it at the beginning of the line"
                                 % ("fenced block" if kind == "fence" else "comment")))
        if hidden[i]:
            continue
        h = HEADING.match(ln)
        if INDENTED_OPEN_HEAD.match(ln):
            problems.append((i + 1, "an indented What We Still Need to Know heading: start it at the beginning of "
                             "the line"))
            continue
        if level is None or done:
            m = OPEN_HEAD.match(ln)
            if m and done:
                problems.append((i + 1, "a second What We Still Need to Know section: keep one"))
            elif m:
                level = len(m.group(1))
            continue
        if h:
            if OPEN_HEAD.match(ln):
                problems.append((i + 1, "a second What We Still Need to Know section: keep one"))
            if len(h.group(1)) <= level:
                done = True
            continue
        why = next((w for pat, w in REJECT if (pat(ln) if callable(pat) else pat.search(ln))), None)
        if why:
            problems.append((i + 1, why))
            continue
        m = OPEN_BULLET.match(ln)
        if m:
            ids.append(m.group(1) or m.group(2))
        elif RECORDISH.match(ln):
            problems.append((i + 1, "a malformed open item: write `- <id>: <reason>` with an id of letters, digits, "
                             "`_`, `.` and `-` (in matched backticks or none) and a reason after the colon"))
    return ids, sorted(problems)


def map_open_items(text):
    """The open item ids of the map's What We Still Need to Know section, in order (read_open_items)."""
    return read_open_items(text)[0]


def closed_findings(opens, future):
    """Every map open item is kept in graph.issues or recorded in opens_closed; opens_closed entries are shape-checked."""
    f = lambda rule, where, msg: {"rule": rule, "where": where, "message": msg}
    out, seen = [], set()
    for i in opens:
        if i in seen:
            out.append(f("open-duplicate", i, f"the map lists open item {i} more than once"))
        seen.add(i)
    kept, bad = load_graph_issues(future)
    if bad:
        out.append(f("graph-issues-malformed", "graph.issues", bad))
    closed = future.get("opens_closed", [])
    if not isinstance(closed, list):
        return out + [f("opens-closed-malformed", "opens_closed", "opens_closed is not a list")]
    closed_ids = set()
    for n, e in enumerate(closed):
        where = f"opens_closed[{n}]"
        if not isinstance(e, dict) or not isinstance(e.get("id"), str) or not e["id"]:
            out.append(f("opens-closed-malformed", where, "entry is not an object with a string id")); continue
        i, res, by, why = e["id"], e.get("resolution"), e.get("by"), e.get("reason")
        where = f"opens_closed[{n}] {i}"
        if i in closed_ids:
            out.append(f("open-closed-duplicate", where, f"{i} is listed more than once in opens_closed"))
        closed_ids.add(i)
        if i not in seen:
            out.append(f("open-closed-unknown", where, f"{i} is not an open item in the map's What We Still Need to Know"))
        if res not in RESOLUTIONS:
            out.append(f("open-closed-bad-resolution", where, f"resolution must be same_as or found_false, not {res!r}"))
        if not isinstance(why, str) or not why.strip():
            out.append(f("open-closed-no-reason", where, f"{i} has no reason"))
        if i in kept and res in RESOLUTIONS:
            out.append(f("open-closed-but-kept", where, f"{i} is recorded as {res} but is still in graph.issues"))
        if res == "same_as" and not (isinstance(by, str) and by in kept):
            out.append(f("same-as-target-missing", where, f"same_as needs by naming an id in graph.issues, not {by!r}"))
        if res == "found_false" and not (isinstance(by, str) and by.strip() or isinstance(by, list) and by
                                         and all(isinstance(b, str) and b.strip() for b in by)):
            out.append(f("open-closed-no-by", where, f"found_false needs by naming the evidence ids or file"))
    for i in sorted(seen):
        if i not in kept and i not in closed_ids:
            out.append(f("open-dropped", i, f"open item {i} from the map is neither in graph.issues nor in opens_closed"))
    return out


def renames(opens, future):
    """{frozenset((old id, new id))} for each opens_closed `same_as` rename of a map open item to an id kept in
    graph.issues: the pair is declared the same open, so its reason overlap is not reported as two ids."""
    kept, _ = load_graph_issues(future)
    closed = future.get("opens_closed")
    out = set()
    for e in closed if isinstance(closed, list) else []:
        if (isinstance(e, dict) and isinstance(e.get("id"), str) and e.get("resolution") == "same_as" and e["id"] in opens
                and e.get("id") not in kept and isinstance(e.get("by"), str) and e["by"] in kept):
            out.add(frozenset((e["id"], e["by"])))
    return out


def load_graph_issues(d):
    """The kept ids: `graph.issues[].id` only. A top-level `issues` list (a stale copy of the register) never counts."""
    g = d.get("graph")
    issues = g.get("issues", []) if isinstance(g, dict) else []
    if not isinstance(issues, list):
        return set(), "graph.issues is not a list"
    return {i["id"] for i in issues if isinstance(i, dict) and isinstance(i.get("id"), str)}, None


def main(argv):
    """check_open_ids.py --run RUN [--threshold T]; the positional RUN stays accepted as an alias."""
    usage = "usage: check_open_ids.py --run RUN [--threshold T]"
    value = lambda flag: argv[argv.index(flag) + 1] if argv.index(flag) + 1 < len(argv) and not argv[argv.index(flag) + 1].startswith("--") else None
    if "--run" in argv:
        run = value("--run")
    elif len(argv) > 1 and not argv[1].startswith("--"):
        run = argv[1]
    else:
        run = None
    if not run:
        print(usage); return 2
    try:
        thr = float(value("--threshold")) if "--threshold" in argv else 0.6
    except (TypeError, ValueError):
        print(usage); return 2
    S = lambda *p: os.path.join(run, "stages", *p)
    try:
        register = load_issues(S("current-state", "current-state.input.json"))
    except (OSError, ValueError) as e:
        print(f"cannot read the register: {e}"); return 2
    reps = {}
    mp = S("transformation-plan", "transformation-map.md")
    opens, shape = [], []
    if os.path.isfile(mp):
        text, ids = open(mp).read(), {}
        opens, shape = read_open_items(text)
        for t in TOKEN.findall(text):
            ids.setdefault(t, "")
        lines = text.splitlines()
        hidden = md_blocks.mask(lines)
        for ln in (ln for n, ln in enumerate(lines) if not hidden[n]):   # a reason in an example never counts
            m = re.match(r"\s*[-*]\s*`?(issue-[a-z0-9-]*[a-z0-9])`?\s*:\s*(.+)", ln)
            if m:
                ids[m.group(1)] = m.group(2)
        reps["transformation-map"] = ids
    fp = S("future-state", "future-state.input.json")
    findings = [{"rule": "open-items-shape", "where": "transformation-map.md line %d" % n, "message": msg}
                for n, msg in shape]
    flags, renamed = [], set()
    if os.path.isfile(fp):
        reps["future-state"] = load_issues(fp)
        if not opens and not shape:
            flags.append({"flag": "open-items-not-listed", "where": "transformation-map",
                          "message": "the map lists no `- <id>: <reason>` open items, so closed opens were not checked"})
        elif opens and not shape:
            fdoc = json.load(open(fp))
            findings += closed_findings(opens, fdoc)
            renamed = renames(opens, fdoc)
    new, pairs = {}, []
    for rep, ids in reps.items():
        n = sorted(i for i in ids if i not in register)
        if n:
            new[rep] = n
    universe = [("current-state", i, r) for i, r in register.items()]
    for rep, ids in reps.items():
        universe += [(rep, i, r) for i, r in ids.items() if i not in register]
    for x in range(len(universe)):
        for y in range(x + 1, len(universe)):
            (r1, i1, t1), (r2, i2, t2) = universe[x], universe[y]
            if i1 != i2 and r1 != r2 and frozenset((i1, i2)) not in renamed:
                s = overlap(words(t1), words(t2))
                if s >= thr:
                    pairs.append({"a": f"{r1}:{i1}", "b": f"{r2}:{i2}", "overlap": round(s, 2)})
    out = {"register_size": len(register), "ids_not_in_register": new, "same_open_under_two_ids": pairs,
           "findings": findings, "flags": flags}
    print(json.dumps(out, indent=2))
    return 1 if pairs or findings else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
