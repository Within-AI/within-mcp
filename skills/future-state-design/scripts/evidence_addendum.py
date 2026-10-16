#!/usr/bin/env python3
"""Evidence addendum (Pins §4): reads added after the freeze, each stored by hash and judged against the run's cutoff.

  evidence_addendum.py add --run R --read FILE --what … --why … --ids ID [ID …] --capture-date ISO
                           --capture-proof session.created_at|observation.timestamp --claim … [--sha256 HEX]
  evidence_addendum.py resolve --run R --id EA-n --choice re-freeze|what-we-still-need-to-know

`add` stores the read's original bytes unchanged at RUN_ROOT/evidence/reads/<sha256>.json and appends an entry to
RUN_ROOT/evidence/addendum.json. A capture dated on or before the cutoff in state.json `freeze.cutoff` is `admitted`;
a later one is `flagged` with `resolution: null` until the person picks with `resolve`. Every refusal exits non-zero
and writes nothing. The orchestrating session calls this on the person's behalf; a stage agent never does.
Stdlib only.
"""
import argparse, hashlib, json, os, re, sys, tempfile
from datetime import datetime, timezone

ADDENDUM = os.path.join("evidence", "addendum.json")
READS = os.path.join("evidence", "reads")
PROOFS = ("session.created_at", "observation.timestamp")
CHOICES = ("re-freeze", "what-we-still-need-to-know")
FIELDS = ("id", "what", "why", "ids", "capture_date", "capture_proof", "read_at", "sha256", "read_file", "claim",
          "status", "resolution", "resolved_at")
ID_RE = re.compile(r"^EA-(\d+)$")
SHA_RE = re.compile(r"^[0-9a-f]{64}$")


class AddendumError(Exception):
    """The request is refused; nothing was written."""


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_when(value, name):
    """An ISO-8601 date-time with a timezone; a date-only or timezone-less value is refused."""
    if not isinstance(value, str) or not value.strip():
        raise AddendumError("%s is missing" % name)
    v = value.strip()
    if "T" not in v and " " not in v:
        raise AddendumError("%s %r is a date without a time and timezone" % (name, value))
    try:
        d = datetime.fromisoformat(v[:-1] + "+00:00" if v.endswith("Z") else v)
    except ValueError:
        raise AddendumError("%s %r is not ISO-8601" % (name, value))
    if d.tzinfo is None or d.utcoffset() is None:
        raise AddendumError("%s %r carries no timezone" % (name, value))
    return d


def sha256_file(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def write_atomic(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".tmp-")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def cutoff(run):
    p = os.path.join(run, "state.json")
    if not os.path.isfile(p):
        raise AddendumError("%s holds no state.json; the run has no cutoff" % run)
    try:
        with open(p, encoding="utf-8") as f:
            st = json.load(f)
    except ValueError as e:
        raise AddendumError("state.json is not readable JSON: %s" % e)
    return parse_when((st.get("freeze") or {}).get("cutoff"), "the run's cutoff (state.json freeze.cutoff)")


def load(run):
    p = os.path.join(run, ADDENDUM)
    if not os.path.exists(p):
        return {"entries": []}
    try:
        with open(p, encoding="utf-8") as f:
            doc = json.load(f)
    except ValueError as e:
        raise AddendumError("%s is not readable JSON: %s" % (ADDENDUM, e))
    if not isinstance(doc, dict) or not isinstance(doc.get("entries"), list):
        raise AddendumError("%s has no entries list" % ADDENDUM)
    seen = [e.get("id") for e in doc["entries"] if isinstance(e, dict)]
    dup = sorted({i for i in seen if seen.count(i) > 1}, key=str)
    if dup:
        raise AddendumError("%s carries id(s) %s more than once" % (ADDENDUM, ", ".join(map(str, dup))))
    return doc


def save(run, doc):
    write_atomic(os.path.join(run, ADDENDUM), (json.dumps(doc, indent=2, ensure_ascii=False) + "\n").encode("utf-8"))


def add(run, read, what, why, ids, capture_date, capture_proof, claim, expected_sha256=None, at=None):
    """Store READ and append its entry; returns the entry. Raises AddendumError, having written nothing."""
    run = os.path.abspath(run)
    given = {"what": what, "why": why, "capture_date": capture_date, "capture_proof": capture_proof, "claim": claim}
    missing = [k for k, v in given.items() if not isinstance(v, str) or not v.strip()]
    ids = [part.strip() for given_ids in (ids or []) for part in given_ids.split(",") if part.strip()]
    if not ids:
        missing.append("ids")
    if not read:
        missing.append("read")
    if missing:
        raise AddendumError("missing field(s): %s" % ", ".join(missing))
    if capture_proof not in PROOFS:
        raise AddendumError("capture proof %r is not one of %s" % (capture_proof, ", ".join(PROOFS)))
    captured = parse_when(capture_date, "capture date")
    cut = cutoff(run)
    if not os.path.isfile(read):
        raise AddendumError("no read file at %s" % read)
    with open(read, "rb") as f:
        data = f.read()
    try:
        json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as e:
        raise AddendumError("the read %s is not JSON: %s" % (read, e))
    digest = hashlib.sha256(data).hexdigest()
    if expected_sha256 is not None and expected_sha256.strip().lower() != digest:
        raise AddendumError("the read's sha256 %s does not match the given %s" % (digest, expected_sha256))
    doc = load(run)
    entries = doc["entries"]
    if any(isinstance(e, dict) and e.get("sha256") == digest for e in entries):
        raise AddendumError("a read with sha256 %s is already in the addendum" % digest)
    rel = "evidence/reads/%s.json" % digest
    stored = os.path.join(run, READS, digest + ".json")
    if os.path.exists(stored) and sha256_file(stored) != digest:
        raise AddendumError("the stored read %s does not match its sha256; it was changed" % rel)
    nums = [int(m.group(1)) for e in entries if isinstance(e, dict) for m in [ID_RE.match(str(e.get("id", "")))] if m]
    new_id = "EA-%d" % (max(nums, default=0) + 1)
    entry = {"id": new_id, "what": what.strip(), "why": why.strip(), "ids": ids, "capture_date": capture_date.strip(),
             "capture_proof": capture_proof, "read_at": at or now(), "sha256": digest, "read_file": rel,
             "claim": claim.strip(), "status": "admitted" if captured <= cut else "flagged",
             "resolution": None, "resolved_at": None}
    if not os.path.exists(stored):
        write_atomic(stored, data)
    if sha256_file(stored) != digest:
        raise AddendumError("the stored read %s does not match its sha256 after writing" % rel)
    entries.append(entry)
    save(run, doc)
    return entry


def resolve(run, entry_id, choice, at=None):
    """Record the person's pick for a flagged entry; returns the entry. Raises AddendumError, having written nothing."""
    run = os.path.abspath(run)
    if choice not in CHOICES:
        raise AddendumError("choice %r is not one of %s" % (choice, ", ".join(CHOICES)))
    if not os.path.exists(os.path.join(run, ADDENDUM)):
        raise AddendumError("%s has no %s" % (run, ADDENDUM))
    doc = load(run)
    hits = [e for e in doc["entries"] if isinstance(e, dict) and e.get("id") == entry_id]
    if len(hits) != 1:
        raise AddendumError("%d entries carry id %s; expected one" % (len(hits), entry_id))
    entry = hits[0]
    if entry.get("status") != "flagged":
        raise AddendumError("%s is %s, not flagged; there is nothing to resolve" % (entry_id, entry.get("status")))
    if entry.get("resolution") is not None:
        raise AddendumError("%s is already resolved as %s" % (entry_id, entry["resolution"]))
    entry["resolution"] = choice
    entry["resolved_at"] = at or now()
    save(run, doc)
    return entry


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("add", help="store a read and add its entry")
    a.add_argument("--run", required=True)
    a.add_argument("--read")
    a.add_argument("--what")
    a.add_argument("--why")
    a.add_argument("--ids", nargs="*", default=[])
    a.add_argument("--capture-date")
    a.add_argument("--capture-proof")
    a.add_argument("--claim")
    a.add_argument("--sha256", help="refuse unless the read hashes to this value")
    r = sub.add_parser("resolve", help="record the person's pick for a flagged entry")
    r.add_argument("--run", required=True)
    r.add_argument("--id", required=True)
    r.add_argument("--choice", required=True)
    args = ap.parse_args(argv)
    try:
        if args.cmd == "add":
            e = add(args.run, args.read, args.what, args.why, args.ids, args.capture_date, args.capture_proof,
                    args.claim, expected_sha256=args.sha256)
        else:
            e = resolve(args.run, args.id, args.choice)
    except AddendumError as err:
        print("refused: %s" % err, file=sys.stderr)
        return 1
    print(json.dumps(e, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
