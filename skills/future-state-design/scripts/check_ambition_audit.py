#!/usr/bin/env python3
"""Shape notes for the ambition audit: stages/ambition-audit/ambition-audit.md.

Usage: check_ambition_audit.py RUN_ROOT

Prints one JSON object: {"verdict": bool, "proposals": n, "notes": [...]}. `verdict` is true when a Verdict section
with some text exists; `proposals` counts the readable rows of the Proposals table. Read leniently: any heading level,
case or trailing words ("## Verdict", "### verdict:", "# Proposals (round 1)"), extra columns and missing optional
cells. Columns are found by name; when the header has no Proposal column the first column is read as the proposal.
Every Evidence id not found in inputs/snapshot.json `sources[].id` or `issues[].id` is a note; the proposal still
counts. Readable proposals are always counted, even when other sections are missing (a missing or empty Verdict is a
note with `verdict: false`); only a missing file or a missing Proposals table gives `proposals: 0`, with a note.
A subheading inside a section ("### Changes to make" under "## Proposals") stays part of it. Fenced blocks and HTML
comments are hidden before any section is found (md_blocks, shared with fsd.py's fallback reader), so a commented-out
or quoted template heading or row never counts.
A table with no header and separator is read as | Proposal | What to change | Steps | Evidence | when its first row
looks like a proposal (first cell AP-<n>, or four cells none of which is a column name), with a note; a proposal row
is never taken as the header. A snapshot whose `sources` or `issues` is not a list is a note; the rest still counts.
Verdict lines that carry ids are a note. Notes never fail: exit 0 always, unless RUN_ROOT itself is not a folder (exit 2). Stdlib only; it
never edits anything.
"""
import json, os, re, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import md_blocks  # noqa: E402  fenced blocks and comments: one rule for every reader

AUDIT = os.path.join("stages", "ambition-audit", "ambition-audit.md")
HEADING = re.compile(r"^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$")
SEP_CELL = re.compile(r"^:?-{1,}:?$")
PROPOSAL_ID = re.compile(r"^AP-\d+\b", re.I)
COLUMNS = ["proposal", "what to change", "steps", "evidence"]
ID_IN_TEXT = re.compile(r"\b(?:AP|RM|WG|DD)-\d+\b|\bissue-[a-z0-9-]+\b|\b[a-z0-9-]+:process-[a-z0-9-]+\b", re.I)


def visible_lines(text):
    """The audit's lines with fenced blocks and HTML comments hidden (md_blocks.document_text, the rule every reader of
    an agent-written file uses), so a commented-out or quoted template never stands in for the real sections."""
    return md_blocks.document_text(text).splitlines()


def sections(lines):
    """{'verdict': [lines], 'proposals': [lines]} keyed by the first matching heading of each kind."""
    found, cur, cur_level = {}, None, 0
    for ln in lines:
        m = HEADING.match(ln)
        if m:
            level, title = len(m.group(1)), re.sub(r"[*_`:]", "", m.group(2)).strip().lower()
            kind = next((k for k in ("verdict", "proposals") if title.startswith(k.rstrip("s"))), None)
            if cur and kind is None and level > cur_level:
                continue  # a subheading stays inside its section
            cur = None
            if kind and kind not in found:
                found[kind], cur, cur_level = [], kind, level
            continue
        if cur:
            found[cur].append(ln)
    return found


def cells(line):
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    return [c.strip() for c in s.split("|")]


def is_sep(c):
    return any(c) and all(SEP_CELL.match(x) for x in c if x)


def norm(c):
    return [re.sub(r"[*_`]", "", x).strip().lower() for x in c]


def looks_like_proposal(c, next_is_sep):
    """True when a first table row is a proposal, not a header."""
    if c and PROPOSAL_ID.match(re.sub(r"[*_`]", "", c[0]).strip()):
        return True
    named = any(any(h.startswith(n) for n in COLUMNS) for h in norm(c))
    return not next_is_sep and len(c) == 4 and not named


def read_table(lines):
    """(header, rows, headerless) of the pipe table rows in lines, or (None, [], False)."""
    rows = [cells(ln) for ln in lines if ln.strip().startswith("|") or (ln.count("|") >= 2)]
    if not rows:
        return None, [], False
    headerless = looks_like_proposal(rows[0], len(rows) > 1 and is_sep(rows[1]))
    header = COLUMNS if headerless else rows[0]
    body = []
    for c in (rows if headerless else rows[1:]):
        if is_sep(c) or not any(c) or (not headerless and norm(c) == norm(header)):
            continue  # separators, blank rows and a repeated header row
        body.append(c)
    return header, body, headerless


def col(header, name):
    for i, h in enumerate(header):
        if re.sub(r"[*_`]", "", h).strip().lower().startswith(name):
            return i
    return None


def snapshot_ids(run, notes):
    path = os.path.join(run, "inputs", "snapshot.json")
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError) as e:
        notes.append("snapshot unreadable (%s); evidence ids not resolved" % e.__class__.__name__)
        return None
    if not isinstance(d, dict):
        notes.append("snapshot is not a JSON object; evidence ids not resolved")
        return None
    ids = set()
    for key in ("sources", "issues"):
        items = d.get(key)
        if items is None:
            continue
        if not isinstance(items, list):
            notes.append("snapshot %s is not a list; its ids not resolved" % key)
            continue
        for item in items:
            if isinstance(item, dict) and item.get("id") is not None:
                ids.add(str(item["id"]))
    return ids


def check(run):
    notes, result = [], {"verdict": False, "proposals": 0, "notes": None}
    path = os.path.join(run, AUDIT)
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            lines = visible_lines(f.read())
    except OSError:
        notes.append("audit file missing: %s" % AUDIT)
        result["notes"] = notes
        return result
    found = sections(lines)
    verdict = [ln.strip() for ln in found.get("verdict", []) if ln.strip()]
    if "verdict" not in found:
        notes.append("no Verdict section")
    elif not verdict:
        notes.append("Verdict section is empty")
    else:
        result["verdict"] = True
        ids = sorted(set(ID_IN_TEXT.findall(" ".join(verdict))))
        if ids:
            notes.append("Verdict names ids (plain words expected): %s" % ", ".join(ids))
    if "proposals" not in found:
        notes.append("no Proposals section")
        result["notes"] = notes
        return result
    header, body, headerless = read_table(found["proposals"])
    if header is None:
        notes.append("Proposals section has no table")
        result["notes"] = notes
        return result
    if headerless:
        notes.append("Proposals table has no header; rows read as | Proposal | What to change | Steps | Evidence |")
    elif norm(header) != COLUMNS:
        notes.append("Proposals header read leniently: | %s |" % " | ".join(header))
    p_i = col(header, "proposal")
    if p_i is None:
        p_i = 0
        notes.append("no Proposal column; first column read as the proposal")
    e_i = col(header, "evidence")
    if e_i is None:
        notes.append("no Evidence column; evidence ids not resolved")
    known = snapshot_ids(run, notes) if e_i is not None else None
    count = 0
    for c in body:
        name = c[p_i].strip() if p_i < len(c) else ""
        if not name:
            notes.append("row without a proposal id skipped: | %s |" % " | ".join(c))
            continue
        count += 1
        if e_i is None:
            continue
        ev = c[e_i] if e_i < len(c) else ""
        ids = [x for x in (t.strip(" `*_") for t in re.split(r"[,;\s]+", ev)) if x]
        if not ids:
            notes.append("%s: no evidence id" % name)
        elif known is not None:
            missing = [x for x in ids if x not in known]
            if missing:
                notes.append("%s: evidence id not in snapshot: %s" % (name, ", ".join(missing)))
    result["proposals"] = count
    if count == 0:
        notes.append("Proposals table has no rows")
    result["notes"] = notes
    return result


def main(argv):
    if len(argv) != 2:
        print(__doc__.strip().splitlines()[2], file=sys.stderr)
        return 2
    run = os.path.abspath(argv[1])
    if not os.path.isdir(run):
        print(json.dumps({"verdict": False, "proposals": 0, "notes": ["run folder missing: %s" % argv[1]]}))
        return 2
    try:
        out = check(run)
    except Exception as e:  # a reading slip is a note, never a stop
        out = {"verdict": False, "proposals": 0, "notes": ["audit not read (%s)" % e.__class__.__name__]}
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
