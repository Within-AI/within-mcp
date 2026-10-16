#!/usr/bin/env python3
"""Agent Build: stage-boundary status table and the residuals list (criterion 15; pins P2b, P3, C9, G28).

Usage:
  status.py table --run R --stage NAME
  status.py residual add --run R --id ID --kind blocks-landing|lands-with-a-note --summary TEXT
  status.py residual resolve --run R --id ID --note TEXT

Exit: 0 ok; 2 usage error, duplicate or unknown id, unknown kind (the file is left unchanged).
"""
import argparse
import datetime
import glob
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _records  # noqa: E402
import log  # noqa: E402

KINDS = ("blocks-landing", "lands-with-a-note")


def _residuals_path(run):
    return os.path.join(run, "residuals.json")


def _load_residuals(run):
    path = _residuals_path(run)
    if not os.path.exists(path):
        return {"residuals": []}
    doc, err = _records.load_json(path)
    if not isinstance(doc, dict) or not isinstance(doc.get("residuals"), list):
        raise ValueError("residuals.json %s" % (err or "has no residuals list"))
    return doc


def add_residual(run, id, kind, summary):
    """Add an open residual; ValueError on a duplicate id or an unknown kind (nothing written)."""
    if kind not in KINDS:
        raise ValueError("unknown kind: %s" % kind)
    if not id or not summary:
        raise ValueError("id and summary are required")
    doc = _load_residuals(run)
    if any(isinstance(r, dict) and r.get("id") == id for r in doc["residuals"]):
        raise ValueError("duplicate residual id: %s" % id)
    entry = {"id": id, "kind": kind, "summary": summary, "status": "open", "note": ""}
    doc["residuals"].append(entry)
    _records.write_record(_residuals_path(run), doc)
    return entry


def resolve_residual(run, id, note):
    """Mark a residual resolved with a note, keeping the entry; ValueError on an unknown id."""
    if not note:
        raise ValueError("a note is required")
    doc = _load_residuals(run)
    for r in doc["residuals"]:
        if isinstance(r, dict) and r.get("id") == id:
            r["status"] = "resolved"
            r["note"] = note
            _records.write_record(_residuals_path(run), doc)
            return r
    raise ValueError("unknown residual id: %s" % id)


def _parse(ts):
    try:
        return datetime.datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ")
    except (TypeError, ValueError):
        return None


def table_rows(run):
    """Return the table rows [item, state, minutes, target, spend]."""
    events = log.read(run)
    now = _parse(_records.now_iso())
    units = {}
    order = []
    for e in events:
        pl = e.get("payload") or {}
        uid = pl.get("uid")
        if e.get("type") == "unit.launched" and uid:
            if uid not in units:
                order.append(uid)
            units[uid] = {"launched": e.get("ts"), "exited": None, "exit_code": None,
                          "spend": None, "target": pl.get("estimate_min")}
        elif e.get("type") == "unit.exited" and uid in units:
            units[uid].update({"exited": e.get("ts"), "exit_code": pl.get("exit_code"),
                               "spend": pl.get("spend_usd")})
    rows = []
    for uid in order:
        u = units[uid]
        start = _parse(u["launched"])
        end = _parse(u["exited"]) if u["exited"] else now
        minutes = str(int((end - start).total_seconds() // 60)) if start and end else "unknown"
        state = "running" if u["exited"] is None else "exited %s" % u["exit_code"]
        target = "" if u["target"] is None else str(u["target"])
        spend = "not reported" if u["spend"] is None else "$%.2f" % u["spend"]
        rows.append([uid, state, minutes, target, spend])
    for path in sorted(glob.glob(os.path.join(run, "records", "*.json"))):
        doc, _ = _records.load_json(path)
        state = doc.get("verdict", "no verdict") if isinstance(doc, dict) else "unreadable"
        rows.append(["records/" + os.path.basename(path), str(state), "", "", ""])
    try:
        res = _load_residuals(run)["residuals"]
    except ValueError:
        rows.append(["residuals", "unreadable", "", "", ""])
    else:
        for kind in KINDS:
            n = sum(1 for r in res if isinstance(r, dict) and r.get("status") == "open" and r.get("kind") == kind)
            rows.append(["open residuals (%s)" % kind, str(n), "", "", ""])
    return rows


def render(rows):
    lines = ["| Item | State | Minutes | Target | Spend |", "|---|---|---|---|---|"]
    for r in rows:
        lines.append("| " + " | ".join(c.replace("|", "\\|") for c in r) + " |")
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="status.py", description="Agent Build status and residuals.")
    sub = parser.add_subparsers(dest="command")
    p_table = sub.add_parser("table")
    p_table.add_argument("--run", required=True)
    p_table.add_argument("--stage", required=True)
    p_res = sub.add_parser("residual")
    rsub = p_res.add_subparsers(dest="action")
    p_add = rsub.add_parser("add")
    p_add.add_argument("--run", required=True)
    p_add.add_argument("--id", required=True)
    p_add.add_argument("--kind", required=True)
    p_add.add_argument("--summary", required=True)
    p_resolve = rsub.add_parser("resolve")
    p_resolve.add_argument("--run", required=True)
    p_resolve.add_argument("--id", required=True)
    p_resolve.add_argument("--note", required=True)
    try:
        args = parser.parse_args(argv)
    except SystemExit as e:
        return _records.USAGE if e.code else _records.OK
    if args.command is None or (args.command == "residual" and args.action is None):
        parser.print_usage(sys.stderr)
        return _records.USAGE
    if not os.path.isdir(args.run):
        sys.stderr.write("--run is not a folder: %s\n" % args.run)
        return _records.USAGE
    try:
        if args.command == "table":
            print(render(table_rows(args.run)))
            log.append(args.run, "stage.boundary", {"stage": args.stage})
        elif args.action == "add":
            add_residual(args.run, args.id, args.kind, args.summary)
        else:
            resolve_residual(args.run, args.id, args.note)
    except (ValueError, OSError) as err:
        sys.stderr.write("%s\n" % err)
        return _records.USAGE
    return _records.OK


if __name__ == "__main__":
    sys.exit(main())
