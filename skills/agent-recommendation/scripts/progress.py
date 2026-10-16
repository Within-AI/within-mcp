#!/usr/bin/env python3
"""The person's live page and status table for an Agent Recommendations run. Never a stop: always exits 0.

    progress.py start  --out <outputs folder> --page <file name> [--connected-folder]
    progress.py stage  --out <outputs folder> --page <file name> --stage <key> --state running|done|done-notes [--note <text>]
    progress.py final  --out <outputs folder> --page <file name> --html <rendered report html>
    progress.py ended  --out <outputs folder> --page <file name> --reason <text>

start publishes a plain "being prepared" page with the stage strip at <outputs>/<page> and prints the status table.
stage records the stage, republishes the live page and prints the table only when the person's view changed; a running
stage past its usual time gets one "still working" line (run it again between steps of a long stage). final swaps in
the rendered report (no reload, no strip). ended shows what the run reached and says it stopped.

Each page file name has its own progress and opens once, so a refinement or round-two report gets its own page.
--connected-folder persists for that page and replaces the managed host path in customer status with the file name in
the person's connected folder. Pass it on start in Cowork; later stage, final and ended calls inherit it.
Builder detail (state, anything that went wrong) goes to <run>/builder/progress when the outputs folder is named
outputs, else to <outputs>/.progress; never to the person. Standard library only, Python 3.9+.
"""
import argparse
import importlib.util
import json
import os
import sys
import tempfile
import time
from datetime import datetime, timezone

sys.dont_write_bytecode = True  # An installed plugin is immutable; local imports must not add __pycache__ files.

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(HERE)), "fast-mode-build", "driver"))
import knowledge_root  # noqa: E402  the one lookup for the plugin's knowledge folder

PROGRESSIVE = os.path.join(knowledge_root.find(), "page", "progressive")


def _load(name):
    spec = importlib.util.spec_from_file_location("ar_" + name, os.path.join(PROGRESSIVE, name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


live = _load("live")
status = _load("status")

# key, the person's label, usual minutes
STAGES = [("intake", "Your objective and context", 5),
          ("scope", "Which processes to look at", 5),
          ("inventory", "What the work looks like", 15),
          ("hypotheses", "Where agents could help", 15),
          ("report", "Your recommendations", 5)]
LABELS = {k: label for k, label, _ in STAGES}
TARGETS = {k: m for k, _, m in STAGES}
TITLE = "Agent recommendations: progress"
NOTE = "Each part of the page fills in when its stage finishes."
BEGIN = "---- status for the person ----"
FINISH = "---- end of status ----"
PREPARING = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Your recommendations</title>
<style>body{margin:0;font:16px/1.5 Inter,system-ui,sans-serif;color:#0B1E2F;background:#fff}
main{max-width:760px;margin:40px auto;padding:0 20px}h1{font-size:28px;margin:0 0 12px}</style>
</head>
<body>
<main>
<h1>Your recommendations are being prepared</h1>
<p>This page shows where the run is. When the report is ready it appears here in place of this note.</p>
</main>
</body>
</html>
"""


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def builder_dir(out):
    out = os.path.abspath(out)
    if os.path.basename(out) == "outputs":
        return os.path.join(os.path.dirname(out), "builder", "progress")
    return os.path.join(out, ".progress")


def _write(path, text):
    folder = os.path.dirname(path)
    os.makedirs(folder, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", suffix=".part", dir=folder)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


class Run:
    """One page's progress: the stage states, kept in the builder folder under the page's file name."""

    def __init__(self, out, page, connected_folder=False):
        self.out = os.path.abspath(out)
        self.page = os.path.basename(page)
        self.dest = os.path.join(self.out, self.page)
        self.rec = builder_dir(self.out)
        self.path = os.path.join(self.rec, "progress-%s.json" % self.page)
        try:
            with open(self.path, encoding="utf-8") as f:
                self.data = json.load(f)
            if not isinstance(self.data, dict):
                self.data = {}
        except (OSError, ValueError):
            self.data = {}
        self.data.setdefault("stages", {})
        if connected_folder:
            self.data["connected_folder"] = True

    def note(self, line):
        """One builder line; never shown to the person."""
        try:
            os.makedirs(self.rec, exist_ok=True)
            with open(os.path.join(self.rec, "builder-notes.log"), "a", encoding="utf-8") as f:
                f.write("%s %s: %s\n" % (_now(), self.page, line))
        except OSError:
            pass

    def save(self):
        try:
            _write(self.path, json.dumps(self.data, indent=2, sort_keys=True) + "\n")
        except OSError as exc:
            self.note("progress record not saved (%s)" % exc)

    def set_stage(self, key, state, note=None):
        st = self.data["stages"].setdefault(key, {})
        if state == "running":
            if st.get("state") != "running":
                st["started"] = time.time()
        else:
            st.setdefault("started", time.time())
            st["finished"] = time.time()
        st["state"] = state
        if note:
            st["note"] = " ".join(note.split())
        elif state != "done-notes":
            st.pop("note", None)
        self.data["last"] = key

    def state(self, key):
        return self.data["stages"].get(key, {}).get("state", "pending")

    def progress(self):
        return [(label, self.state(k)) for k, label, _ in STAGES]

    def publish(self, render, mode, reason=None):
        return live.publish(self.dest, render, self.progress(), mode, self.rec, reason=reason, record=self.note)

    def overrun(self):
        for k, label, target in STAGES:
            st = self.data["stages"].get(k, {})
            if st.get("state") == "running" and st.get("started"):
                return status.overrun_line(label, (time.time() - st["started"]) / 60.0, target)
        return None

    def block(self, line, mode="live", reason=None):
        def words(state):
            if mode == "ended" and state in ("pending", "running"):
                return live.ENDED_STATE
            return live.STATES.get(state, live.STATES["pending"])

        rows = [(label, words(self.state(k))) for k, label, _ in STAGES]
        notes = [self.data["stages"][k]["note"] for k, _, _ in STAGES
                 if self.state(k) == "done-notes" and self.data["stages"][k].get("note")]
        last = self.data["stages"].get(self.data.get("last") or "", {})
        if mode == "ended":
            note = live.ENDED_NOTE + (" " + " ".join(reason.split()) if reason else "")
        elif mode == "final":
            note = "Your recommendations are ready on the page."
        elif last.get("note") and last.get("state") != "done-notes":
            note = last["note"]
        else:
            note = NOTE
        if self.data.get("connected_folder"):
            tail = " It fills in as each stage finishes." if mode == "live" else ""
            line = "Your page in the connected folder: %s.%s" % (self.page, tail)
        return status.customer_block(TITLE, rows, note, page_line=line, notes=notes,
                                     overrun=self.overrun() if mode == "live" else None)


def _post(run, block, always=False):
    text = block if always else status.post_if_changed(run.rec, run.page, block)
    if always:
        status.post_if_changed(run.rec, run.page, block)   # record it, so an identical next post is skipped
    if text is None:
        print(status.UNCHANGED)
        return
    print(BEGIN)
    print(text)
    print(FINISH)


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def cmd_start(a):
    run = Run(a.out, a.page, a.connected_folder)
    run.data.setdefault("started_at", _now())
    run.save()
    r = run.publish(lambda: PREPARING, "live")
    _post(run, status.safe(lambda: run.block(r["line"]), record=run.note), always=True)


def cmd_stage(a):
    run = Run(a.out, a.page, a.connected_folder)
    if a.stage not in LABELS or a.state not in ("running", "done", "done-notes"):
        run.note("unknown stage or state (%r, %r); nothing recorded" % (a.stage, a.state))
        print(status.UNCHANGED)
        return
    if run.data.get("final"):   # the report is published: progress no longer moves backwards
        run.note("stage %s %s after the final report; nothing recorded" % (a.stage, a.state))
        print(status.UNCHANGED)
        return
    run.data.pop("ended", None)   # work resumed after an early stop
    run.set_stage(a.stage, a.state, a.note)
    run.save()
    # the page on disk already holds the "being prepared" note or the last good page; refresh its strip
    r = run.publish(lambda: _read(run.dest) if os.path.isfile(run.dest) else PREPARING, "live")
    _post(run, status.safe(lambda: run.block(r["line"]), record=run.note))


def cmd_final(a):
    run = Run(a.out, a.page, a.connected_folder)
    for k, _, _ in STAGES:   # the report is rendered, so every stage before it finished
        if run.state(k) not in ("done", "done-notes"):
            run.set_stage(k, "done")
    run.save()
    already = bool(run.data.get("final"))
    r = run.publish(lambda: _read(a.html), "final")
    ok = r.get("ok") and (not r.get("fallback") or already)   # a retry that kept the published report is still final
    if ok:
        run.data["final"] = _now()
        run.save()
    mode = "final" if ok else "ended"
    _post(run, status.safe(lambda: run.block(r["line"], mode=mode), record=run.note))


def cmd_ended(a):
    run = Run(a.out, a.page, a.connected_folder)
    if run.data.get("final"):   # the report is already published: an early stop no longer applies
        run.note("ended after the final report; nothing changed")
        print(status.UNCHANGED)
        return
    run.data["ended"] = _now()
    run.save()
    r = run.publish(lambda: _read(run.dest) if os.path.isfile(run.dest) else PREPARING, "ended", reason=a.reason)
    _post(run, status.safe(lambda: run.block(r["line"], mode="ended", reason=a.reason), record=run.note))


def parser():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("start", "stage", "final", "ended"):
        s = sub.add_parser(name)
        s.add_argument("--out", required=True, help="the person's outputs folder")
        s.add_argument("--page", required=True, help="the page's file name in that folder")
        s.add_argument("--connected-folder", action="store_true",
                       help="show the page by file name in the connected folder, not by this host's path")
        if name == "stage":
            s.add_argument("--stage", required=True, help=", ".join(LABELS))
            s.add_argument("--state", required=True, help="running, done or done-notes")
            s.add_argument("--note")
        elif name == "final":
            s.add_argument("--html", required=True, help="the rendered report")
        elif name == "ended":
            s.add_argument("--reason", required=True)
    return p


def main(argv=None):
    a = parser().parse_args(argv)
    try:
        {"start": cmd_start, "stage": cmd_stage, "final": cmd_final, "ended": cmd_ended}[a.cmd](a)
    except Exception as exc:  # noqa: BLE001 - progress never stops a run
        try:
            Run(a.out, a.page, a.connected_folder).note(
                "progress %s failed (%s: %s)" % (a.cmd, type(exc).__name__, exc))
        except Exception:  # noqa: BLE001
            pass
        print(BEGIN)
        print(status.FALLBACK)
        print(FINISH)
    return 0


if __name__ == "__main__":
    sys.exit(main())
