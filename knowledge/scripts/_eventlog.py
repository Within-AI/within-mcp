#!/usr/bin/env python3
"""Shared event-log validation for the factory scripts (single source of truth).

Extracted from append-event.sh so the appender, the loop-controller, and the
derived-view renderer all apply the SAME structural validation before they act on a
log. The rule (from the event-log schema): the whole file parses as JSON, every
event carries an evt-NNNN id, ids are unique and strictly increasing from evt-0001
with no gaps. A log that merely parses per-line is NOT trusted — an id gap, a
duplicate, or an out-of-order id is a structural defect that must block routing and
projection, not just appending.

Usage from a caller:

    import _eventlog
    try:
        events = _eventlog.load_and_validate(log_path)
    except _eventlog.EventLogError as e:
        die(str(e), 2)

`require_exists` controls whether a missing file is an empty log (append: ok) or an
error (route/project: nothing to act on).
"""
import json
import os


class EventLogError(Exception):
    """Raised when a log is unreadable or structurally invalid."""


def evt_num(event_id):
    try:
        return int(str(event_id).split("evt-")[-1])
    except Exception:
        return None


def load_and_validate(log_path, require_exists=False, typed=False):
    """Return the list of event dicts, or raise EventLogError.

    Structural validation (always): whole-file JSON parse; every event_id present +
    well formed; ids unique; ids strictly increasing from evt-0001 with no gaps.

    Typed validation (when `typed=True`, the fresh-run spec): additionally enforce the
    typed event-schema + legal transitions (immutable run header, envelopes, allowed
    types, per-type payloads, per-stage prerequisites) via `_schema.validate_typed`.
    Routers/projectors/reconcilers pass typed=True so invented or stale serial JSON
    cannot influence routing; read-only inspection of a pre-typed log may omit it.
    """
    if not os.path.isfile(log_path):
        if require_exists:
            raise EventLogError("Missing log: %s" % log_path)
        return []
    events = []
    with open(log_path, "r", encoding="utf-8") as f:
        for lineno, raw in enumerate(f, 1):
            raw = raw.strip()
            if not raw:
                continue
            try:
                obj = json.loads(raw)
            except json.JSONDecodeError as e:
                raise EventLogError(
                    "Corrupt log: line %d does not parse as JSON (%s)" % (lineno, e))
            if not isinstance(obj, dict):
                raise EventLogError(
                    "Corrupt log: line %d is a JSON %s, not an event object."
                    % (lineno, type(obj).__name__))
            events.append(obj)
    nums = [evt_num(e.get("event_id")) for e in events]
    if any(n is None for n in nums):
        raise EventLogError("Corrupt log: an event_id is missing or malformed.")
    if len(set(nums)) != len(nums):
        raise EventLogError("Corrupt log: duplicate event_id present.")
    for idx, n in enumerate(nums):
        if n != idx + 1:
            raise EventLogError(
                "Corrupt log: event_id not strictly increasing from evt-0001 "
                "(position %d holds evt-%04d)." % (idx + 1, n))
    if typed:
        import _schema  # local import: _schema imports names from this module
        _schema.validate_typed(events)
    return events
