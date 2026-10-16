#!/usr/bin/env python3
"""Agent Build: the run log (pins C2, P12, H1).

R/run-log.jsonl holds one JSON object per line: {"ts", "type", "payload"}.
Appends are made in "a" mode under an exclusive fcntl.flock. The event
vocabulary is closed: an unknown type, a payload that is not a JSON object, or
a payload missing a pinned key exits 2 and writes nothing.

Curated from the parallel-build-loop log.py (copy of 6 Oct 2026).
  Kept:    one JSON object per line; UTC timestamps; exclusive flock around the
           append; closed vocabulary; exit 2 writes nothing.
  Changed: the event set is the 22 of P12 plus record.written (H1); events
           carry only ts, type and payload; the log lives at R/run-log.jsonl.
  Dropped: event ids, the unit field, resume and status.

Usage:
  log.py append --run R --type T --payload JSON
  log.py read --run R [--type T]

Exit: 0 ok; 2 usage, input or log error.
"""
import argparse
import fcntl
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _records  # noqa: E402

LOG_NAME = "run-log.jsonl"

EVENT_TYPES = (
    "settings.validated", "build.validated", "briefing.written", "prefs.appended",
    "access.recorded", "freeze.sealed", "refreeze.sealed", "round.started",
    "unit.launched", "unit.exited", "unit.alarm", "unit.killrule",
    "review.returned", "e2e.recorded", "suite.recorded", "live.recorded",
    "cold.recorded", "clearance.recorded", "fidelity.round", "integration.suite",
    "exit.written", "stage.boundary", "record.written",
)

# Matched with fullmatch: "$" alone would let a trailing newline through.
_HEX64 = re.compile(r"[0-9a-f]{64}")
_ISO = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ")


def log_path(run):
    return os.path.join(run, LOG_NAME)


def _is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def validate(event_type, payload):
    """Return an error message, or None when the event may be written."""
    if event_type not in EVENT_TYPES:
        return "unknown event type: %s" % event_type
    if not isinstance(payload, dict):
        return "payload must be a JSON object"
    if event_type == "freeze.sealed":
        if not _is_int(payload.get("version")):
            return "freeze.sealed payload needs an integer version"
        sha = payload.get("manifest_sha256")
        if not isinstance(sha, str) or not _HEX64.fullmatch(sha):
            return "freeze.sealed payload needs a 64-hex manifest_sha256"
    if event_type == "round.started":
        if not _is_int(payload.get("round")):
            return "round.started payload needs an integer round"
        first = payload.get("first_launch")
        if not isinstance(first, str) or not _ISO.fullmatch(first):
            return "round.started payload needs first_launch as YYYY-MM-DDTHH:MM:SSZ"
    return None


def append(run, event_type, payload):
    """Append one event to R/run-log.jsonl and return it; ValueError when refused."""
    err = validate(event_type, payload)
    if err:
        raise ValueError(err)
    event = {"ts": _records.now_iso(), "type": event_type, "payload": payload}
    line = json.dumps(event, ensure_ascii=False) + "\n"
    with open(log_path(run), "a", encoding="utf-8") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        try:
            f.write(line)
            f.flush()
            os.fsync(f.fileno())
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    return event


def read(run):
    """Return every event in R/run-log.jsonl ([] when missing); ValueError names a bad line."""
    path = log_path(run)
    if not os.path.isfile(path):
        return []
    events = []
    with open(path, encoding="utf-8") as f:
        for i, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except ValueError as err:
                raise ValueError("run-log line %d is not valid JSON: %s" % (i, err))
            if not isinstance(event, dict):
                raise ValueError("run-log line %d is not a JSON object" % i)
            events.append(event)
    return events


def die(msg):
    sys.stderr.write(msg + "\n")
    sys.exit(_records.USAGE)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="log.py", description="Agent Build run log.")
    sub = parser.add_subparsers(dest="command")
    p_append = sub.add_parser("append", help="append one event")
    p_append.add_argument("--run", required=True)
    p_append.add_argument("--type", required=True)
    p_append.add_argument("--payload", required=True)
    p_read = sub.add_parser("read", help="print events, one JSON object per line")
    p_read.add_argument("--run", required=True)
    p_read.add_argument("--type")
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_usage(sys.stderr)
        return _records.USAGE
    if not os.path.isdir(args.run):
        die("--run is not a folder: %s" % args.run)

    if args.command == "append":
        try:
            payload = json.loads(args.payload)
        except ValueError as err:
            die("--payload is not valid JSON: %s" % err)
        try:
            event = append(args.run, args.type, payload)
        except ValueError as err:
            die(str(err))
        except OSError as err:
            die("could not write the run log: %s" % err)
        print(json.dumps(event, ensure_ascii=False))
        return _records.OK

    try:
        events = read(args.run)
    except (ValueError, OSError, UnicodeDecodeError) as err:
        die(str(err))
    for event in events:
        if args.type is None or event.get("type") == args.type:
            print(json.dumps(event, ensure_ascii=False))
    return _records.OK


if __name__ == "__main__":
    sys.exit(main())
