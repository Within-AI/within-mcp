"""Agent Build: access ledger and hand-over list (U4; pins P2b, P3, C8, G15, G29).

Subcommands:
  record   --run R --system S --state none|synthetic|extract|read-only|live [--evidence PATH]
  assume   --run R --system S --note TEXT
  handover --run R
  done     --run R --item ID --record PATH

Missing access never refuses: a system in state `none` or a missing
access.json is recorded, never a failure. Only `done` exits 1, when the item
has no run record bound to the system's current access.

Library: load(run) -> ledger
"""
import argparse
import datetime
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _records  # noqa: E402
import _locate  # noqa: E402
import log  # noqa: E402

STATES = ("none", "synthetic", "extract", "read-only", "live")
REAL_STATES = ("extract", "read-only", "live")
HANDOFF = os.path.join("inputs", "agent-design", "outputs", "agent-build-inputs.json")
PRODUCTION_ID = "ho-production-customer-integrates"


class UsageError(Exception):
    pass


def load(run):
    """Return the ledger from R/access.json, or {"systems": []} when it is missing or unreadable."""
    obj, err = _records.load_json(os.path.join(run, "access.json"))
    if err or not isinstance(obj, dict) or not isinstance(obj.get("systems"), list):
        return {"systems": []}
    return obj


def _system(ledger, name):
    for s in ledger["systems"]:
        if s.get("name") == name:
            return s
    return None


def _add_system(ledger, name):
    s = _system(ledger, name)
    if s is None:
        s = {"name": name, "state": "none", "evidence": None, "verified_at": None, "assumptions": []}
        ledger["systems"].append(s)
    return s


def _seed(run):
    """Seed the ledger from the hand-off access_assumptions and brief target.systems."""
    ledger = {"systems": []}
    loc = _locate.locate_design(run)
    handoff, _ = _locate.load_handoff(loc.handoff, []) if loc.handoff else (None, "missing")
    entries = handoff.get("access_assumptions") if isinstance(handoff, dict) else None
    for entry in entries if isinstance(entries, list) else []:
        if not isinstance(entry, dict) or not isinstance(entry.get("system"), str) or not entry["system"]:
            continue
        s = _add_system(ledger, entry["system"])
        if entry.get("assumption") is not None:
            s["assumptions"].append({"note": entry["assumption"], "source": "handoff:%s" % entry.get("id")})
    for target in _target_systems(run):
        _add_system(ledger, target["name"])
    return ledger


def _target_systems(run):
    brief, _ = _records.load_json(os.path.join(run, "brief.json"))
    target = brief.get("target") if isinstance(brief, dict) else None
    systems = target.get("systems") if isinstance(target, dict) else None
    out = []
    for s in systems if isinstance(systems, list) else []:
        if isinstance(s, dict) and isinstance(s.get("name"), str) and s["name"]:
            out.append(s)
    return out


def _ledger(run):
    if os.path.isfile(os.path.join(run, "access.json")):
        obj, err = _records.load_json(os.path.join(run, "access.json"))
        if err or not isinstance(obj, dict) or not isinstance(obj.get("systems"), list):
            raise UsageError("access.json is unreadable: %s" % (err or "no systems list"))
        return obj
    return _seed(run)


def _inside(run, rel):
    """Return (absolute path, run-relative path) when rel resolves inside R, else (None, None)."""
    path = rel if os.path.isabs(rel) else os.path.join(run, rel)
    real, root = os.path.realpath(path), os.path.realpath(run)
    if not real.startswith(root.rstrip(os.sep) + os.sep):
        return None, None
    return real, os.path.relpath(real, root).replace(os.sep, "/")


def cmd_record(run, system, state, evidence):
    ledger = _ledger(run)
    rel = None
    if evidence is not None or state != "none":
        if evidence is None:
            raise UsageError("state %s needs --evidence" % state)
        path, rel = _inside(run, evidence)
        if path is None or not os.path.isfile(path) or os.path.getsize(path) == 0:
            raise UsageError("evidence must be an existing non-empty file inside the run folder: %s" % evidence)
    s = _add_system(ledger, system)
    s["state"] = state
    s["evidence"] = rel
    s["verified_at"] = _records.now_iso()
    _records.write_record(os.path.join(run, "access.json"), ledger)
    log.append(run, "access.recorded", {"system": system, "state": state, "evidence": rel})
    print("recorded %s: %s" % (system, state))
    return _records.OK


def cmd_assume(run, system, note):
    ledger = _ledger(run)
    s = _add_system(ledger, system)
    s["assumptions"].append({"note": note, "source": "person"})
    _records.write_record(os.path.join(run, "access.json"), ledger)
    print("assumption noted for %s (state stays %s)" % (system, s["state"]))
    return _records.OK


def _case_ids(run):
    folder = os.path.join(run, "cases")
    if not os.path.isdir(folder):
        return []
    return sorted(n[:-5] for n in os.listdir(folder) if n.endswith(".json"))


def _rerun_what(run, name, state):
    cases = _case_ids(run)
    which = "cases %s" % ", ".join(cases) if cases else "every case in the frozen suite"
    text = "re-run %s against the real %s when access arrives" % (which, name)
    if state == "synthetic":
        text += "; the test data was synthetic data"
    return text


def cmd_handover(run):
    brief, err = _records.load_json(os.path.join(run, "brief.json"))
    if err or not isinstance(brief, dict):
        raise UsageError("brief.json is missing or unreadable: %s" % (err or "not an object"))
    target = brief.get("target")
    if not isinstance(target, dict) or not isinstance(target.get("systems"), list):
        raise UsageError("brief.json target.systems is missing or not a list")
    ledger = _ledger(run)
    if not os.path.isfile(os.path.join(run, "access.json")):
        _records.write_record(os.path.join(run, "access.json"), ledger)

    items = [{"id": PRODUCTION_ID, "system": None, "kind": "customer-integrates",
              "what": "production integration on the customer's stack"}]
    for t in _target_systems(run):
        s = _system(ledger, t["name"])
        state = s.get("state") if s else "none"
        if t.get("mode") == "real" and state in REAL_STATES:
            continue
        items.append({"id": "ho-%s-run-when-access-arrives" % t["name"], "system": t["name"],
                      "kind": "run-when-access-arrives", "what": _rerun_what(run, t["name"], state)})

    old, _ = _records.load_json(os.path.join(run, "handover.json"))
    previous = {}
    if isinstance(old, dict) and isinstance(old.get("items"), list):
        previous = {i.get("id"): i for i in old["items"] if isinstance(i, dict)}
    for item in items:
        prev = previous.get(item["id"], {})
        item["status"] = prev.get("status", "open")
        item["run_record"] = prev.get("run_record")
        item["input_hashes"] = prev.get("input_hashes")
    _records.write_record(os.path.join(run, "handover.json"), {"items": items})
    print("hand-over list: %d item(s)" % len(items))
    return _records.OK


def _parse_iso(text):
    try:
        return datetime.datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ")
    except (TypeError, ValueError):
        return None


def _bare(h):
    return h.split(":", 1)[1] if isinstance(h, str) and h.startswith("sha256:") else h


def cmd_done(run, item_id, record):
    doc, err = _records.load_json(os.path.join(run, "handover.json"))
    if err or not isinstance(doc, dict) or not isinstance(doc.get("items"), list):
        raise UsageError("handover.json is missing or unreadable; run handover first")
    item = next((i for i in doc["items"] if isinstance(i, dict) and i.get("id") == item_id), None)
    if item is None:
        raise UsageError("no hand-over item %s" % item_id)

    reasons = []
    ledger = load(run)
    s = _system(ledger, item.get("system")) if item.get("system") else None
    path, rel = _inside(run, record)
    rec, rec_err = (None, "outside the run folder") if path is None else _records.load_json(path)
    if rec_err or not isinstance(rec, dict):
        reasons.append("run record not readable: %s" % (rec_err or "not an object"))
    if s is None:
        reasons.append("item has no system in the access ledger")
    else:
        if s.get("state") not in REAL_STATES:
            reasons.append("system %s access state is %s" % (s["name"], s.get("state")))
        ev = os.path.join(run, s["evidence"]) if s.get("evidence") else None
        if ev is None or not os.path.isfile(ev):
            reasons.append("system %s has no current evidence file" % s["name"])
        elif isinstance(rec, dict):
            hashes = rec.get("input_hashes")
            values = [_bare(v) for v in hashes.values()] if isinstance(hashes, dict) else []
            if _records.sha256_file(ev) not in values:
                reasons.append("run record is not bound to the current evidence %s" % s["evidence"])
        if isinstance(rec, dict):
            produced, verified = _parse_iso(rec.get("produced_at")), _parse_iso(s.get("verified_at"))
            if produced is None or verified is None:
                reasons.append("produced_at or verified_at missing or not ISO")
            elif produced < verified:
                reasons.append("run record produced before access was recorded")
    if reasons:
        for r in reasons:
            print("not done: %s" % r)
        if (item.get("status"), item.get("run_record"), item.get("input_hashes")) != ("open", None, None):
            item.update({"status": "open", "run_record": None, "input_hashes": None})
            _records.write_record(os.path.join(run, "handover.json"), doc)
        return _records.FAILED
    item["status"] = "done"
    item["run_record"] = rel
    item["input_hashes"] = dict(rec["input_hashes"])
    _records.write_record(os.path.join(run, "handover.json"), doc)
    print("done: %s" % item_id)
    return _records.OK


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        self.print_usage(sys.stderr)
        sys.stderr.write("error: %s\n" % message)
        sys.exit(_records.USAGE)


def main(argv=None):
    p = _Parser(prog="access.py", description="Access ledger and hand-over list.")
    sub = p.add_subparsers(dest="cmd")
    r = sub.add_parser("record")
    r.add_argument("--run", required=True)
    r.add_argument("--system", required=True)
    r.add_argument("--state", required=True, choices=STATES)
    r.add_argument("--evidence")
    a = sub.add_parser("assume")
    a.add_argument("--run", required=True)
    a.add_argument("--system", required=True)
    a.add_argument("--note", required=True)
    h = sub.add_parser("handover")
    h.add_argument("--run", required=True)
    d = sub.add_parser("done")
    d.add_argument("--run", required=True)
    d.add_argument("--item", required=True)
    d.add_argument("--record", required=True)
    for sp in (r, a, h, d):
        sp.error = p.error
    args = p.parse_args(argv)
    if args.cmd is None:
        p.error("a subcommand is required")
    if not os.path.isdir(args.run):
        p.error("--run is not a folder: %s" % args.run)
    try:
        if args.cmd == "record":
            return cmd_record(args.run, args.system, args.state, args.evidence)
        if args.cmd == "assume":
            return cmd_assume(args.run, args.system, args.note)
        if args.cmd == "handover":
            return cmd_handover(args.run)
        return cmd_done(args.run, args.item, args.record)
    except UsageError as err:
        sys.stderr.write("error: %s\n" % err)
        return _records.USAGE


if __name__ == "__main__":
    sys.exit(main())
