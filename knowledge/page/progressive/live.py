"""The person's run page from the start of a run to its end: published, kept current, opened once, never a stop.

    publish(dest, render, progress, mode, record_dir, reason=None, record=None, restart=False) -> dict
        Publish the page at dest (the person's outputs folder). render() returns the page HTML (or None); progress is
        [(label, state)] with state pending | running | done | done-notes | unavailable; mode is live | ended | final.
          live   the page plus a 20-second reload, an "in progress" line and a progress strip with "Running now";
          ended  no reload; the run stopped before it finished, and every part not reached says so;
          final  the page as rendered, no reload and no strip.
        A render that raises or returns nothing keeps the last good page (its live parts swapped for the true state),
        else writes a plain page that is just the progress strip. A final page is kept unless the caller explicitly starts a new revision with restart=True.
        Returns {"path", "ok", "fallback", "opened", "line"}; never raises. Opens the page on its first publication.
    open_once(dest, record_dir) -> bool
        Open the page for the person once: the local Chrome app on macOS, then the system opener. Never raises and
        never waits long. Skipped when ADK_NO_OPEN is set (tests, replays), when there is no `open`, or off macOS with
        no display (Cowork, a sandboxed host): the printed page line is then the way to the page.
    page_line(dest, opened=None) -> str
        The one line the person always gets: where the page is.

record_dir holds live-page.json (one entry per page file name: mode, opened, open result, last publication). record,
when given, is called with one builder line for anything that went wrong. Standard library only, Python 3.9+.
"""
import html
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone

RECORD = "live-page.json"
REFRESH_SECONDS = 20
NO_OPEN_ENV = "ADK_NO_OPEN"
LIVE_START = "<!-- live-page:start -->"
LIVE_END = "<!-- live-page:end -->"
HEAD_START = "<!-- live-page:head -->"
HEAD_END = "<!-- live-page:head-end -->"
FINAL_MARK = "<!-- live-page:final -->"
STATES = {"pending": "Not reached yet", "running": "Running now", "done": "Done", "done-notes": "Done, with notes",
          "unavailable": "Could not finish"}
ENDED_STATE = "Not reached: the run stopped before this part"
LIVE_NOTE = ("Run in progress. Each part fills in when its stage finishes; this page reloads itself every %d seconds."
             % REFRESH_SECONDS)
ENDED_NOTE = "This run stopped before it finished. The parts it reached are below; the rest say so."
STRIP_CSS = (".lp-strip{font:14px/1.45 Inter,system-ui,sans-serif;background:#FBF6EF;color:#0B1E2F;padding:12px 20px;"
             "border-bottom:1px solid #e8e0d4}.lp-strip p{margin:0 0 8px}.lp-strip ol{margin:0;padding:0;list-style:none;"
             "display:flex;flex-wrap:wrap;gap:6px 14px}.lp-strip li{white-space:nowrap}.lp-running{color:#F56740;"
             "font-weight:600}.lp-pending,.lp-ended{opacity:.65}")


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _note(record, line):
    if record is not None:
        try:
            record(line)
        except Exception:  # noqa: BLE001 - a broken recorder never stops the page
            pass


def _write(path, text):
    """Write text to a temporary file beside path, then swap it in whole."""
    folder = os.path.dirname(path) or "."
    os.makedirs(folder, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", suffix=".part", dir=folder)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def _load_record(record_dir):
    try:
        with open(os.path.join(record_dir, RECORD), encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_record(record_dir, data):
    _write(os.path.join(record_dir, RECORD), json.dumps(data, indent=2, sort_keys=True) + "\n")


_OPENED = {}   # this process's own memory of opens, so a record that cannot be written never opens a page twice


def _entry(record_dir, dest):
    e = _load_record(record_dir).get(os.path.basename(dest))
    return e if isinstance(e, dict) else {}


def _update_entry(record_dir, dest, record=None, **fields):
    """Record fields for this page; a record that cannot be written is a builder line, never a failure."""
    try:
        data = _load_record(record_dir)
        e = data.get(os.path.basename(dest))
        if not isinstance(e, dict):
            e = data[os.path.basename(dest)] = {}
        e.update(fields)
        _save_record(record_dir, data)
    except Exception as exc:  # noqa: BLE001
        _note(record, "run page: record not written (%s)" % type(exc).__name__)


def _is_final(dest, entry):
    """A published final page: by the record, or by the final mark in the page itself (when the record is lost)."""
    if not os.path.isfile(dest):
        return False
    if entry.get("mode") == "final":
        return True
    try:
        with open(dest, encoding="utf-8") as f:
            return FINAL_MARK in f.read()
    except (OSError, UnicodeDecodeError):
        return False


def strip_live(text):
    """The page without its live parts (reload, in-progress line, progress strip)."""
    text = re.sub(re.escape(HEAD_START) + r".*?" + re.escape(HEAD_END), "", text, flags=re.S)
    return re.sub(re.escape(LIVE_START) + r".*?" + re.escape(LIVE_END), "", text, flags=re.S)


def _strip_html(progress, mode, reason):
    items = []
    for label, state in progress or []:
        state = state if state in STATES else "pending"
        if mode == "ended" and state in ("pending", "running"):
            word, cls = ENDED_STATE, "lp-ended"
        else:
            word, cls = STATES[state], "lp-" + state
        items.append('<li class="%s"><strong>%s</strong>: %s</li>' % (cls, html.escape(str(label)), html.escape(word)))
    note = LIVE_NOTE if mode == "live" else ENDED_NOTE
    if mode == "ended" and reason:
        note += " " + str(reason).strip()
    return ('%s\n<div class="lp-strip" role="status"><p>%s</p><ol>%s</ol></div>\n%s'
            % (LIVE_START, html.escape(note), "".join(items), LIVE_END))


def _head_html(mode):
    parts = ["<style>%s</style>" % STRIP_CSS]
    if mode == "live":
        parts.insert(0, '<meta http-equiv="refresh" content="%d" id="lp-live-reload">' % REFRESH_SECONDS)
    return "%s\n%s\n%s" % (HEAD_START, "\n".join(parts), HEAD_END)


def _fallback_page(title="Run page"):
    return ('<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n<title>%s</title>\n</head>\n<body>\n'
            '<main><p>The page is being prepared. The progress so far is shown above.</p></main>\n</body>\n</html>\n'
            % html.escape(title))


def _decorate(page, progress, mode, reason):
    page = strip_live(page).replace(FINAL_MARK, "")
    if mode == "final":
        return page.rstrip("\n") + "\n" + FINAL_MARK + "\n"
    head = _head_html(mode)
    strip = _strip_html(progress, mode, reason)
    if re.search(r"</head>", page, re.I):
        page = re.sub(r"</head>", lambda m: head + "\n" + m.group(0), page, count=1, flags=re.I)
    else:
        page = head + "\n" + page
    m = re.search(r"<body\b[^>]*>", page, re.I)
    if m:
        page = page[:m.end()] + "\n" + strip + page[m.end():]
    else:
        page = strip + "\n" + page
    return page


def publish(dest, render, progress, mode, record_dir, reason=None, record=None, restart=False):
    """Publish the person's page; see the module docstring. Never raises."""
    out = {"path": dest, "ok": False, "fallback": False, "opened": False, "line": page_line(dest)}
    if mode not in ("live", "ended", "final"):
        mode = "live"
    try:
        entry = _entry(record_dir, dest)
        final_on_disk = _is_final(dest, entry)
        if mode in ("live", "ended") and final_on_disk and not restart:
            out.update(ok=True, opened=entry.get("opened") is True,
                       line=page_line(dest, entry.get("opened") is True, "final"))   # a final page is never turned back into a live one
            return out
        page = None
        try:
            page = render() if callable(render) else render
            if page is not None and not isinstance(page, str):
                page = str(page)
            if page is not None and not page.strip():
                page = None
        except Exception as exc:  # noqa: BLE001 - a render failure keeps the last good page
            _note(record, "run page: render failed (%s: %s); kept the last good page" % (type(exc).__name__, exc))
            page = None
        if page is None and final_on_disk and not restart:
            out.update(ok=True, fallback=True, opened=entry.get("opened") is True,
                       line=page_line(dest, entry.get("opened") is True, "final"))   # keep the good final page as it is
            return out
        if page is None:
            out["fallback"] = True
            last = None
            if os.path.isfile(dest):
                try:
                    with open(dest, encoding="utf-8") as f:
                        last = f.read()
                except (OSError, UnicodeDecodeError):
                    last = None
            if last is not None and mode == "live" and entry.get("mode") == "live":
                # the live page on disk already reloads; refresh only its progress strip
                page = last
            elif last is not None:
                page = last
            else:
                page = _fallback_page()
            if mode == "final":
                mode = "ended"   # a final view with no rendered page is shown as an ended one, never a blank final
                reason = reason or "The final page could not be prepared; what the run reached is shown."
        text = _decorate(page, progress, mode, reason)
        _write(dest, text)
        out["ok"] = True
        _update_entry(record_dir, dest, record=record, mode=mode, published_at=_now(), fallback=out["fallback"])
    except Exception as exc:  # noqa: BLE001 - the page never stops a run
        _note(record, "run page: not published (%s: %s)" % (type(exc).__name__, exc))
        return out
    out["opened"] = open_once(dest, record_dir, record=record)
    out["line"] = page_line(dest, out["opened"], mode)
    return out


def _opener_disabled():
    return bool(os.environ.get(NO_OPEN_ENV))


def open_page(path):
    """Fast Mode's opener (skills/fast-mode-build/driver/run_v4.py open_page): Chrome on macOS, then `open`.
    Returns (opened, how or reason). Never raises."""
    try:
        if _opener_disabled():
            return False, "opening is turned off on this host"
        if not shutil.which("open"):
            return False, "no system opener on this host"
        if sys.platform != "darwin" and not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
            return False, "no display on this host"
        tries = ([["open", "-a", "Google Chrome", path]] if sys.platform == "darwin" else []) + [["open", path]]
        for cmd in tries:
            r = subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               timeout=10)
            if r.returncode == 0:
                return True, " ".join(cmd[:3]) if len(cmd) > 2 else "open"
        return False, "the opener returned an error"
    except Exception as exc:  # noqa: BLE001
        return False, "the opener failed (%s)" % type(exc).__name__


def open_once(dest, record_dir, record=None):
    """Open the page the first time it is published; later calls return whether it was opened. Never raises."""
    try:
        key = os.path.abspath(dest)
        entry = _entry(record_dir, dest)
        if key in _OPENED:
            if entry.get("open_tried") is not True:   # the record could not be written before: write it now
                _update_entry(record_dir, dest, record=record, open_tried=True, opened=_OPENED[key])
            return _OPENED[key]
        if entry.get("open_tried") is True:
            return entry.get("opened") is True
        opened, how = open_page(dest)
        _OPENED[key] = opened
        _update_entry(record_dir, dest, record=record, open_tried=True, opened=opened, open_result=how)
        if not opened:
            _note(record, "run page: not opened (%s); the person has the path" % how)
        return opened
    except Exception as exc:  # noqa: BLE001
        _note(record, "run page: open check failed (%s)" % type(exc).__name__)
        return False


def page_line(dest, opened=None, mode="live"):
    tail = " It fills in as each stage finishes." if mode == "live" else ""
    if opened:
        return "Your page is open in your browser: %s.%s" % (dest, tail)
    return "Your page: %s (open it in any browser).%s" % (dest, tail)
