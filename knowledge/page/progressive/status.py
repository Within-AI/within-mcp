"""The person's status table, shared by Agent Recommendations, Future State Design and Agent Design.

    customer_block(title, rows, note, page_line=None, notes=None, upstream_open=None, overrun=None) -> str
        A plain table (stage, status), the note line, what any "Done, with notes" notes are, the count of problems
        carried in from the previous skill that are still open, a long-stage line and the page line.
    post_if_changed(record_dir, name, block) -> str or None
        The block when the person's view changed since the last post under that name, else None (post nothing).
    overrun_line(label, minutes, target) -> str or None
        One short line when a running stage is past its time target.
    safe(fn, fallback=FALLBACK, record=None) -> str
        fn()'s text, or a plain fallback; any exception detail goes to record (the builder view), never to the person.

The builder view (check names, ids, file paths, exit codes) stays with each skill. Standard library only, Python 3.9+.
"""
import hashlib
import json
import os
import tempfile

NOTES_SHOWN = 5
FALLBACK = "The progress table could not be drawn just now; the run continues."
UNCHANGED = "No change since the last update; nothing to post."
RECORD = "status-posts.json"


def _cell(text):
    return " ".join(str(text).split()).replace("|", "/")


def customer_block(title, rows, note, page_line=None, notes=None, upstream_open=None, overrun=None):
    lines = ["**%s**" % title, "", "| Stage | Status |", "|---|---|"]
    lines += ["| %s | %s |" % (_cell(label), _cell(state)) for label, state in rows]
    lines += ["", note.strip()]
    if overrun:
        lines += ["", overrun]
    notes = [n for n in (" ".join(str(x).split()) for x in (notes or [])) if n]
    seen, uniq = set(), []
    for n in notes:
        if n not in seen:
            seen.add(n)
            uniq.append(n)
    if uniq:
        lines += ["", "What the notes are:"]
        lines += ["- %s" % n for n in uniq[:NOTES_SHOWN]]
        if len(uniq) > NOTES_SHOWN:
            lines.append("- and %d more, listed on the page under What we still need to know." % (len(uniq) - NOTES_SHOWN))
    if upstream_open:
        lines += ["", "Still open from the earlier step: %d item%s, listed on the page under What we still need to know."
                  % (upstream_open, "" if upstream_open == 1 else "s")]
    if page_line:
        lines += ["", page_line]
    return "\n".join(lines)


def _write(path, text):
    folder = os.path.dirname(path) or "."
    os.makedirs(folder, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", suffix=".part", dir=folder)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


def post_if_changed(record_dir, name, block):
    """The block if the person's view changed since the last post under name; None when it is the same."""
    digest = hashlib.sha256(block.encode("utf-8")).hexdigest()
    path = os.path.join(record_dir, RECORD)
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            data = {}
    except (OSError, ValueError):
        data = {}
    if data.get(name) == digest:
        return None
    data[name] = digest
    try:
        _write(path, json.dumps(data, indent=2, sort_keys=True) + "\n")
    except OSError:
        pass   # an unrecorded post is posted again next time; never a stop
    return block


def overrun_line(label, minutes, target):
    try:
        m, t = float(minutes), float(target)
    except (TypeError, ValueError):
        return None
    if t <= 0 or m <= t:
        return None
    return "Still working on %s: %d minutes so far (it usually takes about %d)." % (label, int(m), int(round(t)))


def safe(fn, fallback=FALLBACK, record=None):
    try:
        text = fn()
        return text if isinstance(text, str) and text.strip() else fallback
    except Exception as exc:  # noqa: BLE001 - the person gets plain words; the detail goes to the builder view
        if record is not None:
            try:
                record("status: %s: %s" % (type(exc).__name__, exc))
            except Exception:  # noqa: BLE001
                pass
        return fallback
