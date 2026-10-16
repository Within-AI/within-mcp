"""Agent Build: open items and their safe behaviour (U4; pins P2b, P3, C8, G21, G22, H14, H15, I4, I14).

Subcommands:
  build --run R                  writes R/open-items.json from the hand-off, brief.json and briefing gaps
  check --run R --records LABEL  writes R/records/open-items-check-<LABEL>.json (counted)

build exits 1 naming each item without a usable brief mapping; check exits 1
unless every open item's test cases show its safe behaviour in R/outputs/LABEL/.

Fidelity carry-over (0494): a brief loosening or a finding left open at the fidelity fix-round
cap (records/fidelity.json "review_items") becomes an open item `fidelity:<finding id>`, mapped
from brief.json open_items like any other item, so the exit gate sees it. A loosening a later
round fixed is listed as resolved. Builder-authored expected results in the current frozen
version are listed under "review_notes" in open-items.json and in each check record, for the
end-of-run review; they do not change acceptance.

Library: load(run) -> items; safe_path_observed(item, output, brief) -> (bool, reason)
"""
import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _records  # noqa: E402
import _locate  # noqa: E402
from briefing import handoff_context, evidence_only, fact_text

DESIGN = os.path.join("inputs", "agent-design")
HANDOFF = os.path.join(DESIGN, "outputs", "agent-build-inputs.json")
HANDOFF_FIELDS = (
    "open_items", "access_assumptions", "stand_ins", "rules_needing_ratification",
    "critical_cases_failed_or_open", "primitive_form", "identity_runtime_opens",
    "evidence_strength", "upstream_failing_checks", "capability_needs",
    "stage_input_hashes", "outputs", "bundle", "platform_question", "missing_information",
)
LIST_FIELDS = ("open_items", "rules_needing_ratification", "critical_cases_failed_or_open",
               "identity_runtime_opens", "upstream_failing_checks", "missing_information")
NMI = "needs_more_information"
WITHHELD = re.compile(r"^withheld preference: (\S+)")


def load(run):
    """Return the items of R/open-items.json, or [] when it is missing or unreadable."""
    obj, err = _records.load_json(os.path.join(run, "open-items.json"))
    if err or not isinstance(obj, dict) or not isinstance(obj.get("items"), list):
        return []
    return obj["items"]


def _summary(entry):
    if isinstance(entry, dict):
        for key in ("summary", "unknown", "question", "assumption", "text", "why_it_matters", "name"):
            if isinstance(entry.get(key), str) and entry[key]:
                return entry[key]
    return _records.normalize_json(entry)[:200]


def _item(id, field, summary, adjudicator=None):
    return {"id": id, "source_field": field, "summary": summary, "adjudicator": adjudicator or NMI}


def _handoff_items(run):
    """Items from the five hand-off lists; (items, {field: [item ids]})."""
    items, by_field = [], {}
    loc = _locate.locate_design(run)
    if not loc.exists:
        return items, by_field
    handoff, err = _locate.load_handoff(loc.handoff, []) if loc.handoff else (None, "missing: %s" % HANDOFF)
    if not isinstance(handoff, dict):
        handoff = {}
    for field in LIST_FIELDS:
        entries = handoff.get(field)
        if field == "missing_information" and field not in handoff:
            continue  # legacy absence is not an empty known inventory
        if not isinstance(entries, list):
            why = err or ("%s absent" % field if field not in handoff else "%s is not a list" % field)
            items.append(_item("handoff:%s" % field, field, "hand-off %s could not be read (%s); covers the whole design" % (field, why)))
            by_field[field] = ["handoff:%s" % field]
            continue
        ids = []
        for i, e in enumerate(entries):
            d = e if isinstance(e, dict) else {}
            if field == "missing_information":
                id, adj = str(d.get("id") or "missing:%s" % d.get("part", i)), d.get("adjudicator")
            elif field == "rules_needing_ratification" and isinstance(e, str) and e:
                id, adj = "rule:%s" % e, None
            elif field == "open_items":
                id, adj = str(d.get("id", "open_items:%d" % i)), d.get("who_can_answer")
            elif field == "rules_needing_ratification":
                id, adj = "rule:%s" % d.get("rule_id", d.get("id", i)), d.get("adjudicator")
            elif field == "critical_cases_failed_or_open":
                id, adj = "case:%s" % d.get("case_id", d.get("id", i)), d.get("adjudicator")
            elif field == "identity_runtime_opens":
                id, adj = "identity:%s" % d.get("id", i), d.get("adjudicator")
            else:
                id, adj = "upstream:%d" % i, d.get("adjudicator")
            summary = _summary(e)
            if field in ("missing_information", "critical_cases_failed_or_open"):
                summary = "Resolve %s: %s" % (id, fact_text(evidence_only(e)))
            existing = next((item for item in items if item["id"] == id), None)
            if existing is None:
                item = _item(id, field, summary, adj if isinstance(adj, str) else None)
                item["source"] = loc.rel(loc.handoff)
                item["evidence"] = evidence_only(e)
                items.append(item)
            elif summary not in existing["summary"]:
                existing["summary"] += "; " + summary
            ids.append(id)
        by_field[field] = ids
    return items, by_field


def _gaps(run):
    doc, _ = _records.load_json(os.path.join(run, "what-we-still-need-to-know.json"))
    entries = doc.get("items") if isinstance(doc, dict) else None
    return [g for g in entries if isinstance(g, dict) and g.get("source") == "briefing"] if isinstance(entries, list) else []


def _fidelity_items(run):
    """Open items from the fidelity ledger's end-of-run review items: [(item, still_open)]."""
    led, err = _records.load_json(os.path.join(run, "records", "fidelity.json"))
    if err or not isinstance(led, dict) or not isinstance(led.get("review_items"), list):
        return []
    rounds = led.get("rounds") if isinstance(led.get("rounds"), list) else []
    last = rounds[-1] if rounds and isinstance(rounds[-1], dict) else {}
    live = set()
    if led.get("state") != "clean":
        live = set(f.get("id") for f in last.get("findings") or [] if isinstance(f, dict))
    out, seen = [], {}
    for ri in led["review_items"]:
        if not isinstance(ri, dict) or not isinstance(ri.get("finding"), str):
            continue
        id = "fidelity:%s" % ri["finding"]
        kind = ri.get("kind")
        text = ri.get("text") if isinstance(ri.get("text"), str) else ""
        label = "brief loosening (design's stricter reading kept)" if kind == "loosening" \
            else "fidelity finding left open at the fix-round cap"
        still_open = kind == "open-at-cap" or ri["finding"] in live
        if id in seen:
            item, was_open = out[seen[id]]
            if "%s: " % label not in item["summary"]:
                item["summary"] = "%s; %s: %s" % (item["summary"], label, text)
            out[seen[id]] = (item, was_open or still_open)
            continue
        seen[id] = len(out)
        out.append((_item(id, "fidelity", "%s: %s" % (label, text)), still_open))
    return out


def review_notes(run):
    """Builder-authored expected results in the current frozen version, for the end-of-run review."""
    manifest, err = _records.load_json(os.path.join(run, "frozen", "MANIFEST.json"))
    if err or not isinstance(manifest, dict):
        return []
    cur = [v for v in manifest.get("versions") or []
           if isinstance(v, dict) and v.get("version") == manifest.get("current_version")]
    notes = []
    for f in (cur[0].get("files") or []) if len(cur) == 1 else []:
        if isinstance(f, dict) and f.get("kind") == "expected" and f.get("authored_by") == "builder":
            notes.append({"kind": "builder-authored-expected", "path": f.get("path"),
                          "case_id": f.get("case_id"), "author": f.get("author"),
                          "author_role": f.get("author_role"), "authored_by": "builder",
                          "note": f.get("authorship_note") or "expected result written by the builder"})
    return notes


def _valid_mapping(entry):
    return (isinstance(entry, dict)
            and isinstance(entry.get("affected_processes"), list) and entry["affected_processes"]
            and isinstance(entry.get("test_case_ids"), list) and entry["test_case_ids"]
            and entry.get("safe_behaviour") in ("stop", "escalate"))


def build(run):
    """Return (items, unmapped ids, problems)."""
    brief, err = _records.load_json(os.path.join(run, "brief.json"))
    if not isinstance(brief, dict):
        brief = {}
    entries = brief.get("open_items") if isinstance(brief.get("open_items"), list) else []
    mapping = {e["id"]: e for e in entries if isinstance(e, dict) and isinstance(e.get("id"), str)}

    items, by_field = _handoff_items(run)
    index = {i["id"]: i for i in items}

    def add(item):
        items.append(item)
        index[item["id"]] = item
        return item

    for gap in _gaps(run):
        field, summary = gap.get("field"), gap.get("summary") if isinstance(gap.get("summary"), str) else ""
        if field == "preferences":
            m = WITHHELD.match(summary)
            if not m:
                continue
            key = m.group(1).rstrip(",;.)")
            id = "prefs:%s" % key
            if id not in index:
                add(_item(id, "preferences", summary, gap.get("adjudicator")))
            continue
        if field not in HANDOFF_FIELDS:
            continue
        id = "handoff:%s" % field
        covered = [x for x in by_field.get(field, []) if x != id]
        targets = [index[id]] if id in index else ([index[x] for x in covered] if covered else [])
        if not targets:
            targets = [add(_item(id, field, "briefing gap: %s" % summary, gap.get("adjudicator")))]
            by_field.setdefault(field, []).append(id)
        else:
            for t in targets:
                note = "briefing gap: %s" % summary
                if note not in t["summary"]:
                    t["summary"] = "%s; %s" % (t["summary"], note)

    resolved_fidelity = set()
    for item, still_open in _fidelity_items(run):
        if item["id"] in index:
            continue
        add(item)
        if not still_open:
            resolved_fidelity.add(item["id"])

    for e in entries:
        if isinstance(e, dict) and isinstance(e.get("id"), str) and e["id"] not in index:
            add(_item(e["id"], "brief", e.get("summary") if isinstance(e.get("summary"), str) else "brief open item"))

    previous = {i.get("id"): i for i in load(run) if isinstance(i, dict)}
    unmapped, problems = [], []
    for item in items:
        entry = mapping.get(item["id"])
        if item["id"].startswith("prefs:"):
            tests = entry.get("test_case_ids") if isinstance(entry, dict) else None
            procs = entry.get("affected_processes") if isinstance(entry, dict) else None
            item["affected_units"] = ["*"]
            item["affected_processes"] = list(procs) if isinstance(procs, list) and procs else ["*"]
            item["safe_behaviour"] = "escalate"
            item["test_case_ids"] = list(tests) if isinstance(tests, list) else []
            if not item["test_case_ids"]:
                unmapped.append(item["id"])
                problems.append("%s: withheld preference has no test_case_ids in brief.json open_items" % item["id"])
        elif item["id"] in resolved_fidelity and not _valid_mapping(entry):
            # Fixed by a later fidelity round: listed for the record, nothing left to show.
            item["affected_units"] = ["*"]
            item["affected_processes"] = ["*"]
            item["safe_behaviour"] = "stop"
            item["test_case_ids"] = []
        elif _valid_mapping(entry):
            units = entry.get("affected_units")
            item["affected_units"] = list(units) if isinstance(units, list) and units else ["*"]
            item["affected_processes"] = list(entry["affected_processes"])
            item["safe_behaviour"] = entry["safe_behaviour"]
            item["test_case_ids"] = list(entry["test_case_ids"])
        else:
            item["affected_units"] = ["*"]
            item["affected_processes"] = ["*"]
            item["safe_behaviour"] = "stop"
            item["test_case_ids"] = []
            unmapped.append(item["id"])
            why = "no brief.json open_items entry" if entry is None else \
                "brief entry needs non-empty affected_processes and test_case_ids and safe_behaviour stop or escalate"
            problems.append("%s: %s" % (item["id"], why))
        prev = previous.get(item["id"], {})
        item["status"] = prev.get("status") if prev.get("status") in ("open", "resolved") else "open"
        if item["id"] in resolved_fidelity:
            item["status"] = "resolved"
    return items, unmapped, problems


def cmd_build(run):
    items, unmapped, problems = build(run)
    notes = review_notes(run)
    doc = {"items": items, "handoff_context": handoff_context(_locate.locate_design(run))}
    if notes:
        doc["review_notes"] = notes
    _records.write_record(os.path.join(run, "open-items.json"), doc)
    print("open items: %d" % len(items))
    for n in notes:
        print("for the end-of-run review: %s is builder-authored (%s)" % (n["path"], n["note"]))
    for p in problems:
        print("unmapped: %s" % p)
    return _records.FAILED if unmapped else _records.OK


def _is_seq(v):
    return isinstance(v, int) and not isinstance(v, bool)


def safe_path_observed(item, output, brief):
    """Pins G22, H14: was the item's safe behaviour observed, with a sequenced stop event?"""
    if not isinstance(output, dict):
        return False, "missing output"
    if output.get("exit") == "crashed":
        return False, "output crashed"
    behaviour = item.get("safe_behaviour")
    calls = output.get("tool_calls") if isinstance(output.get("tool_calls"), list) else []
    writes = [c for c in calls if isinstance(c, dict) and c.get("kind") == "write"]
    rules = output.get("rule_results") if isinstance(output.get("rule_results"), list) else []
    events = [r for r in rules if isinstance(r, dict) and r.get("effect") == behaviour]
    if behaviour == "stop":
        if output.get("exit") == "refused" and not writes:
            return True, "refused with no write call"
    elif behaviour == "escalate":
        label_sets = brief.get("label_sets") if isinstance(brief, dict) else None
        outcomes = {}
        for ls in label_sets if isinstance(label_sets, list) else []:
            if isinstance(ls, dict) and isinstance(ls.get("escalation_outcomes"), list):
                outcomes[ls.get("id")] = ls["escalation_outcomes"]
        decisions = output.get("decisions") if isinstance(output.get("decisions"), list) else []
        events += [d for d in decisions if isinstance(d, dict)
                   and d.get("after") in outcomes.get(d.get("label_set"), [])]
    else:
        return False, "safe_behaviour %r is not stop or escalate" % behaviour
    if not events:
        return False, "no %s event (rule_results or decisions) in the output" % behaviour
    if any(not _is_seq(e.get("seq")) for e in events):
        return False, "%s event has no integer seq" % behaviour
    first = min(e["seq"] for e in events)
    for w in writes:
        if not _is_seq(w.get("seq")):
            return False, "write call %s has no integer seq" % w.get("name")
        if w["seq"] > first:
            return False, "write call %s (seq %d) after the %s event (seq %d)" % (w.get("name"), w["seq"], behaviour, first)
    return True, "%s event at seq %d with no later write" % (behaviour, first)


def cmd_check(run, label):
    brief, _ = _records.load_json(os.path.join(run, "brief.json"))
    inputs = _records.standard_input_hashes(run)
    items_path = os.path.join(run, "open-items.json")
    if os.path.isfile(items_path):
        inputs["open-items.json"] = _records.sha256_file(items_path)
    pkg = os.path.join(run, "package.sha256")
    package_sha = NMI
    if os.path.isfile(pkg):
        with open(pkg, encoding="utf-8") as f:
            package_sha = f.readline().strip() or NMI
    record = _records.record_header(_records.SKILL_VERSION, inputs, {"package_sha256": package_sha})
    record.update({"label": label, "items": [], "reasons": []})
    relpath = "records/open-items-check-%s.json" % label

    doc, err = _records.load_json(items_path)
    if err or not isinstance(doc, dict) or not isinstance(doc.get("items"), list):
        record["verdict"] = "unknown"
        record["reasons"].append("open-items.json missing or unreadable; run build first")
        _records.write_counted(run, relpath, record)
        print(record["reasons"][0])
        return _records.USAGE

    notes = review_notes(run)
    if notes:
        record["review_notes"] = notes
    manifest = os.path.join(run, "frozen", "MANIFEST.json")
    if os.path.isfile(manifest):
        record["input_hashes"]["frozen/MANIFEST.json"] = _records.sha256_file(manifest)
    out_dir = os.path.join(run, "outputs", label)
    for item in doc["items"]:
        if not isinstance(item, dict) or item.get("status") != "open":
            continue
        entry = {"id": item.get("id"), "cases": []}
        cases = item.get("test_case_ids") if isinstance(item.get("test_case_ids"), list) else []
        if not cases:
            record["reasons"].append("%s: no test_case_ids" % item.get("id"))
        for case_id in cases:
            path = os.path.join(out_dir, "%s.json" % case_id)
            output, out_err = _records.load_json(path)
            if out_err:
                ok, reason = False, "missing output: outputs/%s/%s.json" % (label, case_id)
            else:
                ok, reason = safe_path_observed(item, output, brief)
            entry["cases"].append({"case_id": case_id, "observed": ok, "reason": reason})
            if not ok:
                record["reasons"].append("%s / %s: %s" % (item.get("id"), case_id, reason))
        record["items"].append(entry)
        hashes = record["input_hashes"]
        for case in entry["cases"]:
            p = os.path.join(out_dir, "%s.json" % case["case_id"])
            if os.path.isfile(p):
                hashes["outputs/%s/%s.json" % (label, case["case_id"])] = _records.sha256_file(p)
    record["verdict"] = "fail" if record["reasons"] else "pass"
    _records.write_counted(run, relpath, record)
    for r in record["reasons"]:
        print("not observed: %s" % r)
    print("verdict: %s" % record["verdict"])
    return _records.FAILED if record["reasons"] else _records.OK


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        self.print_usage(sys.stderr)
        sys.stderr.write("error: %s\n" % message)
        sys.exit(_records.USAGE)


def main(argv=None):
    p = _Parser(prog="openitems.py", description="Open items and their safe behaviour.")
    sub = p.add_subparsers(dest="cmd")
    b = sub.add_parser("build")
    b.add_argument("--run", required=True)
    c = sub.add_parser("check")
    c.add_argument("--run", required=True)
    c.add_argument("--records", required=True, metavar="LABEL")
    for sp in (b, c):
        sp.error = p.error
    args = p.parse_args(argv)
    if args.cmd is None:
        p.error("a subcommand is required")
    if not os.path.isdir(args.run):
        p.error("--run is not a folder: %s" % args.run)
    if args.cmd == "build":
        return cmd_build(args.run)
    if not re.match(r"^[A-Za-z0-9._-]+$", args.records) or args.records in (".", ".."):
        p.error("--records must be a plain label")
    return cmd_check(args.run, args.records)


if __name__ == "__main__":
    sys.exit(main())
