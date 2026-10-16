#!/usr/bin/env python3
"""Field profile of a system extract (P2b, P3 `profiles/<system>.json`).

Usage: profile.py --run R --system S --file inputs/history/S.csv [--date-formats F,...]

Reads the CSV (UTF-8, BOM tolerated, first row the header) and writes
R/profiles/S.json. A measure whose denominator is 0 is "needs_more_information".
Exit 0 when written; exit 2 writing nothing on usage errors, an unreadable file,
or a row whose column count differs from the header's.
"""
import argparse
import csv
import io
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _records  # noqa: E402

NMI = "needs_more_information"
DEFAULT_DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%SZ")
TOP_N = 5


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        sys.stderr.write("profile.py: %s\n" % message)
        sys.exit(_records.USAGE)


def _usage(message):
    sys.stderr.write("profile.py: %s\n" % message)
    return _records.USAGE


def _parses(value, formats):
    for fmt in formats:
        try:
            datetime.strptime(value, fmt)
            return True
        except ValueError:
            continue
    return False


def profile_field(name, cells, rows, formats):
    """Measures for one column; cells are the raw strings, one per non-header row."""
    trimmed = [c.strip() for c in cells]
    values = [v for v in trimmed if v != ""]
    empty = len(trimmed) - len(values)
    counts = {}
    for v in values:
        counts[v] = counts.get(v, 0) + 1
    distinct = len(counts)
    top = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:TOP_N]
    if values:
        share = sum(1 for v in values if _parses(v, formats)) / float(len(values))
        ratio = distinct / float(len(values))
    else:
        share = NMI
        ratio = NMI
    return {
        "name": name,
        "distinct_count": distinct,
        "distinct_ratio": ratio,
        "null_rate": (empty / float(rows)) if rows else NMI,
        "top_values": [[v, n] for v, n in top],
        "date_parse_share": share,
        "pinned_clock_hint": isinstance(share, float) and share > 0,
    }


def main(argv=None):
    parser = _Parser(prog="profile.py", allow_abbrev=False,
                     description="Write R/profiles/<system>.json from a CSV extract.")
    parser.add_argument("--run")
    parser.add_argument("--system")
    parser.add_argument("--file")
    parser.add_argument("--date-formats")
    args = parser.parse_args(argv)

    for flag in ("run", "system", "file"):
        if not getattr(args, flag):
            return _usage("--%s is required" % flag)
    if not os.path.isdir(args.run):
        return _usage("--run is not a folder: %s" % args.run)
    if "/" in args.system or "\\" in args.system or args.system in (".", ".."):
        return _usage("--system must be a plain name")
    formats = DEFAULT_DATE_FORMATS
    if args.date_formats is not None:
        formats = tuple(f for f in args.date_formats.split(",") if f)
        if not formats:
            return _usage("--date-formats names no format")

    run = args.run
    path = args.file if os.path.isabs(args.file) else os.path.join(run, args.file)
    if not _records._under(path, run):
        return _usage("file outside the run folder: %s" % args.file)
    try:
        with open(path, "rb") as f:
            raw = f.read()
        text = raw.decode("utf-8-sig")
        table = list(csv.reader(io.StringIO(text, newline="")))
    except (OSError, UnicodeDecodeError, csv.Error) as err:
        return _usage("unreadable file %s (%s)" % (args.file, err))
    if not table:
        return _usage("unreadable file %s (no header row)" % args.file)

    header = table[0]
    body = table[1:]
    for i, row in enumerate(body, start=2):
        if len(row) != len(header):
            return _usage("row %d has %d columns; the header has %d" % (i, len(row), len(header)))

    rows = len(body)
    fields = [profile_field(name, [row[j] for row in body], rows, formats)
              for j, name in enumerate(header)]
    rel = os.path.relpath(os.path.realpath(path), os.path.realpath(run)).replace(os.sep, "/")
    script_sha = _records.sha256_file(os.path.abspath(__file__))
    record = _records.record_header(_records.SKILL_VERSION, {rel: _records.sha256_file(path)},
                                    {"script_sha256": script_sha})
    record.update({"verdict": "pass", "reasons": [], "file": rel, "rows": rows, "fields": fields})
    out_dir = os.path.join(run, "profiles")
    if not os.path.isdir(out_dir):
        os.makedirs(out_dir)
    _records.write_record(os.path.join(out_dir, args.system + ".json"), record)
    return _records.OK


if __name__ == "__main__":
    sys.exit(main())
