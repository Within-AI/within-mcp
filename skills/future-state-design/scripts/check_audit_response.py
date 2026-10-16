#!/usr/bin/env python3
"""Coverage notes for the rework's audit response: stages/transformation-plan/audit-response.md (R-rework, R-lenient).

Usage: check_audit_response.py RUN_ROOT

Runs only once the rework round has started (stages/pass-1/ambition-audit/ambition-audit.md exists; hooks_checks
registers it for transformation-plan then). Prints one JSON object: {"expected": [ids], "answered": [ids], "notes": [...]}.
`expected` is the readable proposal ids of the first audit, read the way check_ambition_audit.py reads them. The response
is read leniently: any pipe table whose header has a Proposal column, any heading or none, extra columns, column-name
variants ("Taken?", "**Taken**"), missing optional cells; fenced blocks and HTML comments are hidden first (md_blocks).
A missing response file, a proposal with no row, a row for an id the audit does not list, and a Taken value other than
yes or no are each a note for the builder log. Notes only: it never fails a stage, never asks for a repair and never
stops a run; exit 0 always, unless RUN_ROOT itself is not a folder (exit 2). The response stays outside the map and the
person's page. Stdlib only; it never edits anything.
"""
import json, os, re, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import check_ambition_audit as audit  # noqa: E402  one lenient reading of the audit's Proposals table

PASS_1_AUDIT = os.path.join("stages", "pass-1", "ambition-audit", "ambition-audit.md")
RESPONSE = os.path.join("stages", "transformation-plan", "audit-response.md")


def ident(cell):
    return re.sub(r"[*_`]", "", cell or "").strip().upper()


def audit_ids(text):
    """The readable proposal ids of an audit file's Proposals table, in order."""
    found = audit.sections(audit.visible_lines(text))
    if "proposals" not in found:
        return []
    header, body, _headerless = audit.read_table(found["proposals"])
    if header is None:
        return []
    p_i = audit.col(header, "proposal")
    p_i = 0 if p_i is None else p_i
    out = []
    for c in body:
        name = ident(c[p_i]) if p_i < len(c) else ""
        if name and name not in out:
            out.append(name)
    return out


def response_rows(text, notes):
    """[(id, taken)] of every response row: the pipe tables whose header names a Proposal column."""
    rows, table = [], []
    for ln in audit.visible_lines(text) + [""]:
        if audit.md_blocks.has_pipe(ln) and ln.strip():
            table.append(audit.cells(ln))
            continue
        if table:
            rows += table_rows(table, notes)
        table = []
    return rows


def table_rows(table, notes):
    header = table[0]
    p_i = audit.col(header, "proposal")
    if p_i is None:
        return []
    t_i = audit.col(header, "taken")
    if t_i is None:
        notes.append("audit response: no Taken column; read as | %s |" % " | ".join(header))
    elif audit.norm(header)[:3] != ["proposal", "taken", "how or why not"]:
        notes.append("audit response header read leniently: | %s |" % " | ".join(header))
    out = []
    for c in table[1:]:
        if audit.is_sep(c) or not any(c):
            continue
        name = ident(c[p_i]) if p_i < len(c) else ""
        if not name:
            continue
        taken = c[t_i] if t_i is not None and t_i < len(c) else ""
        out.append((name, re.sub(r"[*_`.!]", "", taken).strip().lower()))
    return out


def check(run):
    notes = []
    result = {"expected": [], "answered": [], "notes": notes}
    try:
        with open(os.path.join(run, PASS_1_AUDIT), encoding="utf-8", errors="replace") as f:
            expected = audit_ids(f.read())
    except OSError:
        notes.append("no first audit at %s; audit response not compared" % PASS_1_AUDIT.replace(os.sep, "/"))
        return result
    result["expected"] = expected
    try:
        with open(os.path.join(run, RESPONSE), encoding="utf-8", errors="replace") as f:
            rows = response_rows(f.read(), notes)
    except OSError:
        notes.append("audit response missing: %s" % RESPONSE.replace(os.sep, "/"))
        return result
    seen = {}
    for name, taken in rows:
        seen.setdefault(name, taken)
        if taken not in ("yes", "no"):
            notes.append("audit response: %s Taken is %r, not yes or no" % (name, taken))
        if name not in expected:
            notes.append("audit response: %s is not a proposal of the first audit" % name)
    result["answered"] = list(seen)
    for name in expected:
        if name not in seen:
            notes.append("audit response: no row for %s" % name)
    return result


def main(argv):
    if len(argv) != 2:
        print(__doc__.strip().splitlines()[2], file=sys.stderr)
        return 2
    run = os.path.abspath(argv[1])
    if not os.path.isdir(run):
        print(json.dumps({"expected": [], "answered": [], "notes": ["run folder missing: %s" % argv[1]]}))
        return 2
    try:
        out = check(run)
    except Exception as e:  # a reading slip is a note, never a stop
        out = {"expected": [], "answered": [], "notes": ["audit response not read (%s)" % e.__class__.__name__]}
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
