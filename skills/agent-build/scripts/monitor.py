#!/usr/bin/env python3
"""Agent Build: the unit monitor (pins P2b, P12, G28).

Reads R/run-log.jsonl through log.read and R/units/<U>/, and appends through
log.append:
  unit.exited   {uid, attempt, exit_code, spend_usd|null} once per uid and attempt,
                when R/units/<U>/<U>.exit holds "<exit code> <UTC time>";
  unit.alarm    {uid, estimate_min, elapsed_min} once per uid, when now minus the
                uid's first launch exceeds 1.5 x its first estimate_min;
  unit.killrule {uid, attempts: 3, exit_codes} once per uid, after three
                non-zero exits.

Curated from the parallel-build-loop monitor (copy of 8 Oct 2026).
  Kept:    the 1.5x alarm from the first launch; the stream-json cost reader
           (total_cost_usd of the last "type": "result" line); one pass per
           300 s while a launched agent is alive.
  Changed: event names are those of P12; exits come from <U>.exit files; the
           estimate comes from unit.launched; a missing cost is null, never 0;
           a uid with no estimate gets no alarm and a printed note.
  Added:   the kill rule (three failed attempts).
  Dropped: spend.recorded and alarm.raised events.

Usage:
  monitor.py --run R [--once]

Exit: 0 ok; 2 usage error, R not a folder or an unreadable run log.
"""
import argparse
import datetime
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _records  # noqa: E402
import log  # noqa: E402

INTERVAL_S = 300
ALARM_FACTOR = 1.5
KILL_AFTER = 3


def parse_iso(text):
    return datetime.datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=datetime.timezone.utc)


def is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def is_number(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def alive(pid):
    if not is_int(pid) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def result_cost(path):
    """total_cost_usd of the last stream-json line with type "result"; None when absent."""
    cost = None
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                if isinstance(ev, dict) and ev.get("type") == "result" \
                        and is_number(ev.get("total_cost_usd")):
                    cost = ev["total_cost_usd"]
    except OSError:
        pass
    return cost


def read_exit(path):
    """(exit_code, None) from "<exit code> <UTC time>", or (None, reason)."""
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read().strip()
    except OSError as err:
        return None, "unreadable: %s" % err
    parts = text.split()
    if len(parts) != 2:
        return None, "expected '<exit code> <UTC time>', got %r" % text
    try:
        code = int(parts[0])
        parse_iso(parts[1])
    except ValueError:
        return None, "expected '<exit code> <UTC time>', got %r" % text
    return code, None


def unit_pids(run, uid):
    path = os.path.join(run, "units", uid, "%s.pid" % uid)
    try:
        with open(path, encoding="utf-8") as f:
            return [int(tok) for tok in f.read().split() if tok.strip().isdigit()]
    except OSError:
        return []


def summarise(events):
    launches, exited, alarmed, killed = {}, {}, set(), set()
    for ev in events:
        t, p = ev.get("type"), ev.get("payload")
        if not isinstance(p, dict):
            continue
        uid = p.get("uid")
        if not isinstance(uid, str):
            continue
        if t == "unit.launched":
            launches.setdefault(uid, []).append((ev.get("ts"), p))
        elif t == "unit.exited":
            exited.setdefault(uid, []).append(p)
        elif t == "unit.alarm":
            alarmed.add(uid)
        elif t == "unit.killrule":
            killed.add(uid)
    return launches, exited, alarmed, killed


def record_exits(run, launches, exited):
    for uid in sorted(launches):
        attempt = launches[uid][-1][1].get("attempt")
        if any(p.get("attempt") == attempt for p in exited.get(uid, [])):
            continue
        unit_dir = os.path.join(run, "units", uid)
        exit_path = os.path.join(unit_dir, "%s.exit" % uid)
        if not os.path.isfile(exit_path):
            continue
        code, err = read_exit(exit_path)
        if err:
            print("monitor: %s: malformed exit file, nothing recorded (%s)" % (uid, err))
            continue
        payload = {"uid": uid, "attempt": attempt, "exit_code": code,
                   "spend_usd": result_cost(os.path.join(unit_dir, "%s.log" % uid))}
        log.append(run, "unit.exited", payload)
        exited.setdefault(uid, []).append(payload)
        print("monitor: %s attempt %s exited %d" % (uid, attempt, code))


def raise_alarms(run, launches, exited, alarmed, now):
    for uid in sorted(launches):
        if uid in alarmed:
            continue
        latest = launches[uid][-1][1].get("attempt")
        if any(p.get("attempt") == latest and p.get("exit_code") == 0
               for p in exited.get(uid, [])):
            continue
        estimates = [p.get("estimate_min") for _, p in launches[uid]
                     if is_number(p.get("estimate_min"))]
        if not estimates:
            print("no estimate for %s" % uid)
            continue
        estimate = estimates[0]
        try:
            first = parse_iso(launches[uid][0][0])
        except (TypeError, ValueError):
            print("monitor: %s: first unit.launched has no readable ts" % uid)
            continue
        elapsed = (now - first).total_seconds() / 60.0
        if elapsed > ALARM_FACTOR * estimate:
            log.append(run, "unit.alarm", {"uid": uid, "estimate_min": estimate,
                                           "elapsed_min": round(elapsed, 1)})
            alarmed.add(uid)
            print("monitor: %s past 1.5x its estimate (%.1f of %s min)" % (uid, elapsed, estimate))


def apply_kill_rule(run, exited, killed):
    for uid in sorted(exited):
        if uid in killed:
            continue
        failed = [p.get("exit_code") for p in exited[uid]
                  if is_int(p.get("exit_code")) and p.get("exit_code") != 0]
        if len(failed) >= KILL_AFTER:
            log.append(run, "unit.killrule", {"uid": uid, "attempts": KILL_AFTER,
                                              "exit_codes": failed[:KILL_AFTER]})
            killed.add(uid)
            print("monitor: %s failed %d attempts: kill rule" % (uid, KILL_AFTER))


def tick(run):
    """One pass; returns True while any launched agent's pid is alive."""
    now = parse_iso(_records.now_iso())
    launches, exited, alarmed, killed = summarise(log.read(run))
    record_exits(run, launches, exited)
    raise_alarms(run, launches, exited, alarmed, now)
    apply_kill_rule(run, exited, killed)
    return any(alive(pid) for uid in launches for pid in unit_pids(run, uid))


def main(argv=None):
    parser = argparse.ArgumentParser(prog="monitor.py", description="Agent Build unit monitor.")
    parser.add_argument("--run", required=True)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args(argv)
    if not os.path.isdir(args.run):
        sys.stderr.write("--run is not a folder: %s\n" % args.run)
        return _records.USAGE
    run_log = os.path.join(args.run, "run-log.jsonl")
    if not os.path.isfile(run_log) or not os.access(run_log, os.R_OK):
        sys.stderr.write("monitor: run log missing or unreadable: %s\n" % run_log)
        return _records.USAGE
    while True:
        try:
            any_alive = tick(args.run)
        except (ValueError, OSError) as err:
            sys.stderr.write("monitor: %s\n" % err)
            return _records.USAGE
        if args.once or not any_alive:
            return _records.OK
        time.sleep(INTERVAL_S)


if __name__ == "__main__":
    sys.exit(main())
