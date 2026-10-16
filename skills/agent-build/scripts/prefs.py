#!/usr/bin/env python3
"""Agent Build: the customer preferences file (pins C4, P11, G14, H11).

One JSON object per line: {"key", "value", "decided_by", "date", "run_id",
"source"}. The file is append only: `append` opens it in "a" mode, writes one
line, flushes and fsyncs; nothing here ever rewrites it.

`read` returns the latest value per key (the last valid line for that key in
file order). A malformed line is quarantined: listed in `problems` with its line
number and the reading taken, never guessed, and never allowed to erase a valid
line. A key keeps its newest valid value; a key named only by malformed lines is
withheld. `all_withheld` is true only when the file cannot be read at all.
`validate_entry` is the only place the entry rules live.

Usage:
  prefs.py read --path P
  prefs.py append --path P --key K --value JSON --decided-by NAME --run-id ID
                  --source STOP [--run R]

Exit: 0 ok; 2 usage error, invalid --value or a refused entry (nothing written).
"""
import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _records  # noqa: E402

FIELDS = ("key", "value", "decided_by", "date", "run_id", "source")
SOURCES = ("settings", "interview", "target", "blocks", "brief", "freeze",
           "disposition", "other")

# Credential patterns: none may appear in a key or a value.
CREDENTIAL_PATTERNS = (
    ("private key block", re.compile(r"-----BEGIN")),
    ("AWS access key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("secret key", re.compile(r"sk-[A-Za-z0-9]{20,}")),
    ("Slack token", re.compile(r"xox[abp]-")),
    ("GitHub token", re.compile(r"ghp_[A-Za-z0-9]{36}")),
    ("names a password", re.compile(r"password", re.IGNORECASE)),
    ("names a secret", re.compile(r"secret", re.IGNORECASE)),
    ("names an api_key", re.compile(r"api_key", re.IGNORECASE)),
    ("names a token=", re.compile(r"token=", re.IGNORECASE)),
)

_KEY_IN_TEXT = re.compile(r'"key"\s*:\s*"([^"]+)"')


def credential_hits(text):
    """Return the names of the credential patterns found in text."""
    return [name for name, pattern in CREDENTIAL_PATTERNS if pattern.search(text)]


def _is_empty(v):
    return v is None or (isinstance(v, str) and v == "")


def _reject_constant(name):
    raise ValueError("%s is not valid JSON" % name)


def _finite_float(text):
    number = float(text)
    if number in (float("inf"), float("-inf")):
        raise ValueError("%s is not a finite number" % text)
    return number


def loads_strict(text):
    """Parse strict JSON: NaN, Infinity and numbers that overflow to infinity are refused."""
    return json.loads(text, parse_constant=_reject_constant, parse_float=_finite_float)


def _strict_line(obj):
    """Serialise one entry as strict JSON in UTF-8; ValueError when it cannot be."""
    return (json.dumps(obj, ensure_ascii=False, sort_keys=True, allow_nan=False)
            + "\n").encode("utf-8")


def validate_entry(obj):
    """Return the problems with one entry; an empty list means valid."""
    if not isinstance(obj, dict):
        return ["not a JSON object"]
    problems = []
    for field in FIELDS:
        if field not in obj:
            problems.append("missing %s" % field)
        elif _is_empty(obj[field]):
            problems.append("empty %s" % field)
        elif field != "value" and not isinstance(obj[field], str):
            problems.append("%s is not a string" % field)
    source = obj.get("source")
    if isinstance(source, str) and source and source not in SOURCES:
        problems.append("source %r is not one of %s" % (source, "|".join(SOURCES)))
    key = obj.get("key")
    if isinstance(key, str):
        for hit in credential_hits(key):
            problems.append("key matches a credential pattern (%s)" % hit)
    try:
        _strict_line(obj)
    except (TypeError, ValueError) as err:
        problems.append("not strict JSON in UTF-8 (%s)" % err)
        return problems
    if "value" in obj and not _is_empty(obj["value"]):
        value = obj["value"]
        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        for hit in credential_hits(text):
            problems.append("value matches a credential pattern (%s)" % hit)
    return problems


def _recover_key(line, obj, parsed):
    """Return the key a malformed line names, or None when it cannot be identified."""
    if parsed:
        if isinstance(obj, dict) and isinstance(obj.get("key"), str) and obj["key"]:
            return obj["key"]
        return None
    found = _KEY_IN_TEXT.findall(line)
    if len(found) == 1:
        return found[0]
    return None


def read(path):
    """Read the preferences file; a missing file reads as empty and creates nothing."""
    result = {"values": {}, "withheld": [], "all_withheld": False, "problems": []}
    if not os.path.exists(path):
        return result
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except OSError as err:
        # Unreadable: no key can be identified, so nothing is released.
        result["all_withheld"] = True
        result["problems"].append("preferences file unreadable: %s" % err)
        return result
    values = {}
    bad = []  # (line number, problems, key or None): quarantined lines
    for number, raw_line in enumerate(raw.split(b"\n"), 1):
        # The replaced text serves only key recovery; a line that is not
        # strict UTF-8 is malformed, never released.
        line = raw_line.decode("utf-8", errors="replace").strip()
        if not line:
            continue
        parsed = True
        obj = None
        try:
            obj = loads_strict(raw_line.decode("utf-8").strip())
            problems = validate_entry(obj)
        except UnicodeDecodeError as err:
            parsed = False
            problems = ["not valid UTF-8 (%s)" % err]
        except ValueError as err:
            parsed = False
            problems = ["not valid JSON (%s)" % err]
        if not problems:
            values[obj["key"]] = {
                "value": obj["value"], "source": obj["source"],
                "decided_by": obj["decided_by"], "date": obj["date"],
                "run_id": obj["run_id"], "_line": number,
            }
            continue
        bad.append((number, problems, _recover_key(line, obj, parsed)))
    # A malformed line is quarantined: recorded, never guessed, and never allowed to erase a valid line. Each key
    # keeps its newest valid value; a key named only by malformed lines is withheld.
    withheld = []
    for number, problems, key in bad:
        text = "line %d: %s; set aside" % (number, "; ".join(problems))
        if key is None:
            text += " (it names no key it can be tied to; every valid preference is kept)"
        elif key in values:
            text += "; key %r keeps its newest valid value (line %d)" % (key, values[key]["_line"])
        else:
            text += "; key %r withheld (no valid value for it)" % key
            if key not in withheld:
                withheld.append(key)
        result["problems"].append(text)
    result["withheld"] = withheld
    result["values"] = dict((k, dict((f, v[f]) for f in v if f != "_line")) for k, v in values.items())
    return result


def append(path, key, value, decided_by, run_id, source, date=None, run=None):
    """Append one decision; ValueError (file untouched) when the entry is refused."""
    entry = {
        "key": key, "value": value, "decided_by": decided_by,
        "date": date if date is not None else _records.now_iso()[:10],
        "run_id": run_id, "source": source,
    }
    problems = validate_entry(entry)
    if problems:
        raise ValueError("preference refused: " + "; ".join(problems))
    line = _strict_line(entry)
    parent = os.path.dirname(os.path.abspath(path))
    if not os.path.isdir(parent):
        os.makedirs(parent)
    with open(path, "ab") as f:
        # A last line without its newline keeps its bytes; the new entry
        # starts on a line of its own.
        if f.tell() > 0:
            with open(path, "rb") as existing:
                existing.seek(-1, os.SEEK_END)
                if existing.read(1) != b"\n":
                    line = b"\n" + line
        f.write(line)
        f.flush()
        os.fsync(f.fileno())
    if run is not None:
        import log
        log.append(run, "prefs.appended", {"key": key, "source": source})
    return entry


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        self.print_usage(sys.stderr)
        sys.stderr.write("%s: error: %s\n" % (self.prog, message))
        sys.exit(_records.USAGE)


def main(argv=None):
    parser = _Parser(prog="prefs.py", description="Agent Build customer preferences file.")
    sub = parser.add_subparsers(dest="command", parser_class=_Parser)
    p_read = sub.add_parser("read", help="print the latest value per key as one JSON object")
    p_read.add_argument("--path", required=True)
    p_append = sub.add_parser("append", help="append one decision")
    p_append.add_argument("--path", required=True)
    p_append.add_argument("--key", required=True)
    p_append.add_argument("--value", required=True, help="the value, as JSON")
    p_append.add_argument("--decided-by", required=True)
    p_append.add_argument("--run-id", required=True)
    p_append.add_argument("--source", required=True)
    p_append.add_argument("--run")
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_usage(sys.stderr)
        return _records.USAGE
    if args.command == "read":
        print(json.dumps(read(args.path), ensure_ascii=False, sort_keys=True))
        return _records.OK
    try:
        value = loads_strict(args.value)
    except ValueError as err:
        sys.stderr.write("--value is not valid JSON: %s\n" % err)
        return _records.USAGE
    if args.run is not None and not os.path.isdir(args.run):
        sys.stderr.write("--run is not a folder: %s\n" % args.run)
        return _records.USAGE
    try:
        append(args.path, args.key, value, args.decided_by, args.run_id, args.source,
               run=args.run)
    except ValueError as err:
        sys.stderr.write("%s\n" % err)
        return _records.USAGE
    return _records.OK


if __name__ == "__main__":
    sys.exit(main())
