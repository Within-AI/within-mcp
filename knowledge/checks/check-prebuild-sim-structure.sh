#!/usr/bin/env python3
# This file is Python despite its .sh name: run it directly or as `python3 <this file>`; `bash <this file>` cannot run it.
"""Pre-build simulation structure check (stage 11) -- the manifest's shape and every file it pins.

Plain-language handle: "check the pre-build simulation's manifest is whole and every file it pins is still the
file it pinned". One of stage 11's four checks. Structure and closed sets only; whether the data shapes, rules
code and results are right is the other checks' and the independent review's job. Run with the run root as the
working directory.

Fails (exit 1), each finding listed, on:
  * the manifest's shape (pre-build-simulation-manifest.template.yaml): a missing or unknown top-level key (the
    column-0 reference_use record is allowed), a wrong schema, or any of the retired review keys
    business_owner_review, reviews or samples_queued;
  * a pinned file (any {path, sha256} anywhere in the manifest) that is missing or no longer has its pinned sha;
  * data.derived_expected_outcomes missing, null while output/pre-build-simulation/expected-outcomes.derived.yaml
    exists, or pointing at a file that does not exist;
  * design_set not pinning output/coherence/design-set.json at its current sha; run_id or
    gate_submission_event_id not matching the event log;
  * a token a rule reads from a read interface that is in no data-shape record's token_map, or in more than one;
  * a data-shape record whose state disagrees with its evidence (real-sample needs a client-sample entry;
    real-layout a client-layout or company-brain entry; assumed only interface-design entries and an
    adapter_risk), a manifest systems[] state or adapter_risk_id out of step with its record, or an assumed
    record whose adapter risk is not listed in adapter-risks.yaml (or a risk listed for a system that is not
    assumed);
  * a data file without a label, or in the folder another label names (data/client/ holds client_data only,
    data/synthetic/ synthetic_data only);
  * a value open stamped on a rule that runs without a parameter entry labelled "PROVISIONAL TEST VALUE - NOT A
    CLIENT VALUE", or a parameter entry for an open that is not a value open of the policy, or one with no
    confirmed_by;
  * the case-class manifest not covering every Test Suite case (check_corpus_coverage from
    pre-build-simulation-stage/templates/corpus-coverage-check.template.py, over case-classes.yaml's
    case_classes and corpus);
  * a changes-requested review the event log records against an earlier submission (a
    prebuild-simulation-review decision and the record it pins; never a listing of the reviews folder) that the
    manifest does not list in folded_reviews with its current sha, or whose record is missing or not the file its
    decision pins, or whose defects and declaration entries are not in the pinned defects.yaml and declaration.yaml
    as the record says them (the same class, or the class its dispute was reclassified to, the owner's note and
    correction verbatim, the same evidence; a matching id alone is never a fold), or which breaks a record rule
    (its feedback file re-checked against every feedback rule);
  * walk.yaml or case-data.yaml malformed (a walk rule the policy does not carry; a walk override without a
    reason; a case input file the manifest does not pin as data).
Every pinned file this check reads is read through its pin (checks/_prebuild_sim.py read_pinned): one that is not
the pinned file is a finding naming it, never read as if it were. The event log is required (pbs.require_events):
a run with no events.jsonl is refused (exit 2) naming it, never read as an empty log. The manifest's sha is taken
from the same single read as its content.
Never refuses a shape: an unfamiliar mark, a pure script or a policy with no rules passes on structure alone.

Usage: check-prebuild-sim-structure.sh MANIFEST
Exit: 0 pass; 1 findings; 2 setup error (the manifest or the event log cannot be read).
"""
import importlib.util
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _prebuild_sim as pbs  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))


def load_coverage_fn():
    path = os.path.join(os.path.dirname(HERE), "pre-build-simulation-stage/templates/corpus-coverage-check.template.py")
    spec = importlib.util.spec_from_file_location("_pbs_corpus_coverage", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.check_corpus_coverage


def check(root, mrel):
    m, msha = pbs.load_manifest_with_sha(root, mrel)
    events = pbs.require_events(root, "the structure check (run_id, the gate's submission, the logged reviews)")
    p = []
    missing = [k for k in pbs.MANIFEST_KEYS if k not in m]
    unknown = [k for k in m if k not in pbs.MANIFEST_KEYS and k not in pbs.MANIFEST_EXTRA_ALLOWED]
    retired = [k for k in pbs.RETIRED_REVIEW_KEYS if k in m]
    if retired:
        p.append("the manifest carries the retired review key(s) %s; a review is recorded outside the manifest, "
                 "never in it" % ", ".join(retired))
    unknown = [k for k in unknown if k not in retired]
    if missing:
        p.append("the manifest lacks %s" % ", ".join(missing))
    if unknown:
        p.append("the manifest carries unknown key(s) %s" % ", ".join(unknown))
    if m.get("schema") != "pre-build-simulation/v1":
        p.append("schema is %r, not pre-build-simulation/v1" % m.get("schema"))
    if missing:
        return p
    for where, path, pinned, now in pbs.pin_problems(root, m, responses=False):
        if now is None:
            p.append("%s pins %s, which is missing" % (where, path))
        else:
            p.append("%s pins %s at sha256 %s, but the file changed (now %s)" % (where, path, pinned[:12], now[:12]))
    p.extend(derived_outcome_findings(root, m))
    ds = m.get("design_set") or {}
    if ds.get("path") != "output/coherence/design-set.json":
        p.append("design_set must pin output/coherence/design-set.json")
    elif pbs.pin_reason(root, ds, "design_set") is not None:
        p.append("design_set does not pin the design-coherence gate's current design set; the design changed after "
                 "this round, so start a new round")
    opened = next((e for e in events if e.get("type") == "run.opened"), {})
    if m.get("run_id") != opened.get("run_id"):
        p.append("run_id %r is not the run's run.opened run_id %r" % (m.get("run_id"), opened.get("run_id")))
    gate = next((e for e in reversed(events) if e.get("stage") == "design-coherence"
                 and e.get("type") == "executor.submitted"), None)
    if (gate or {}).get("event_id") != m.get("gate_submission_event_id"):
        p.append("gate_submission_event_id %r is not the gate's current submission %r"
                 % (m.get("gate_submission_event_id"), (gate or {}).get("event_id")))
    try:
        policy = pbs.read_pinned(root, m["inputs"]["decision_policy"], "yaml", "the pinned decision policy") or {}
        eval_set = pbs.read_pinned(root, m["inputs"]["eval_set"], "yaml", "the pinned Test Suite") or {}
    except (pbs.PrebuildError, KeyError, TypeError) as exc:
        return p + ["the pinned policy or Test Suite cannot be read: %s" % exc]
    # data-shape records
    records = {}
    for s in m.get("systems") or []:
        try:
            rec = pbs.read_pinned(root, s["data_shape"], "yaml", "its data-shape record") or {}
        except (pbs.PrebuildError, KeyError, TypeError) as exc:
            p.append("systems[] entry %r: its data-shape record cannot be read: %s" % (s.get("system_id"), exc))
            continue
        sid = s.get("system_id")
        records[sid] = rec
        if rec.get("schema") != "data-shape/v1" or rec.get("system_id") != sid:
            p.append("data-shape record for %s must carry schema data-shape/v1 and system_id %s" % (sid, sid))
        kinds = {e.get("kind") for e in rec.get("evidence") or [] if isinstance(e, dict)}
        state = rec.get("state")
        if state == "real-sample" and "client-sample" not in kinds:
            p.append("data-shape %s is real-sample but has no client-sample evidence" % sid)
        elif state == "real-layout" and not kinds & {"client-layout", "company-brain"}:
            p.append("data-shape %s is real-layout but has no client-layout or company-brain evidence" % sid)
        elif state == "assumed":
            if kinds - {"interface-design"}:
                p.append("data-shape %s is assumed but carries %s evidence; its state should say what is known"
                         % (sid, ", ".join(sorted(kinds - {"interface-design"}))))
            if not isinstance(rec.get("adapter_risk"), dict):
                p.append("data-shape %s is assumed and must carry an adapter_risk" % sid)
        elif state not in ("real-sample", "real-layout", "assumed"):
            p.append("data-shape %s has state %r" % (sid, state))
        if s.get("state") != state:
            p.append("systems[] states %s is %r, but its record says %r" % (sid, s.get("state"), state))
        want_risk = (rec.get("adapter_risk") or {}).get("id") if state == "assumed" else None
        if s.get("adapter_risk_id") != want_risk:
            p.append("systems[] adapter_risk_id for %s must be %r (set exactly when the record is assumed)"
                     % (sid, want_risk))
        for f in rec.get("fields") or []:
            if isinstance(f, dict) and f.get("evidence_state") not in ("Observed", "Assumed"):
                p.append("data-shape %s field %r has evidence_state %r" % (sid, f.get("name"), f.get("evidence_state")))
    try:
        risks = pbs.read_pinned(root, m["adapter_risks"], "yaml", "the pinned adapter-risks.yaml") or {}
    except (pbs.PrebuildError, KeyError, TypeError) as exc:
        risks = {}
        p.append("adapter-risks.yaml cannot be read: %s" % exc)
    if risks.get("schema") != "adapter-risks/v1":
        p.append("adapter-risks.yaml must carry schema adapter-risks/v1")
    listed = {r.get("id"): r for r in risks.get("risks") or [] if isinstance(r, dict)}
    for sid, rec in records.items():
        rid = (rec.get("adapter_risk") or {}).get("id")
        if rec.get("state") == "assumed" and rid not in listed:
            p.append("the adapter risk of assumed system %s (%r) is not listed in adapter-risks.yaml" % (sid, rid))
    for rid, r in listed.items():
        rec = records.get(r.get("system_id")) or {}
        if rec.get("state") != "assumed":
            p.append("adapter-risks.yaml lists %s for %s, which is not an assumed system" % (rid, r.get("system_id")))
    # every token a rule reads from a read interface is mapped exactly once
    mapped = {}
    for sid, rec in records.items():
        for tm in rec.get("token_map") or []:
            if isinstance(tm, dict) and tm.get("token"):
                mapped.setdefault(tm["token"], []).append(sid)
    read_ifaces = {r for rec in records.values() for r in rec.get("reads") or []}
    for _rs, row in pbs.policy_rows(policy):
        for tok in row.get("operative_tokens") or []:
            iface = str(tok).split(".", 1)[0]
            if not (pbs.read_interface(tok) or iface in read_ifaces):
                continue
            where = mapped.get(tok, [])
            if not where:
                p.append("token %s (read by %s) is in no data-shape record's token_map" % (tok, row["rule_id"]))
            elif len(where) > 1:
                p.append("token %s is mapped in more than one data-shape record (%s)" % (tok, ", ".join(where)))
    # labels and folders
    for label_dir, want in (("client", "client_data"), ("synthetic", "synthetic_data")):
        for f in (m.get("data") or {}).get(label_dir) or []:
            if not f.get("path", "").startswith("%s/data/%s/" % (pbs.OUT, label_dir)):
                p.append("data file %s is listed as %s but is not under %s/data/%s/" % (f.get("path"), label_dir,
                                                                                      pbs.OUT, label_dir))
            read = pbs.pinned_read(root, f, "text", "data file")
            if not read.ok:
                # not the pinned file: its label is not read (the pin finding above names it too)
                p.append("the label of %s is not read: %s" % (f.get("path"), read.reason))
            else:
                got = data_label(f.get("path", ""), read.content)
                if got is None:
                    p.append("data file %s carries no data_label" % f.get("path"))
                elif got != want:
                    p.append("data file %s is labelled %s but sits in data/%s/ (%s only)" % (f.get("path"), got,
                                                                                           label_dir, want))
    # provisional values
    try:
        params = pbs.read_pinned(root, m["rules"]["parameters"], "yaml", "the pinned parameters.yaml") or {}
        other = (pbs.read_pinned(root, m["rules"]["other_marks"], "yaml", "the pinned other-marks.yaml")
                 if m["rules"].get("other_marks") else None)
    except (pbs.PrebuildError, KeyError, TypeError) as exc:
        return p + ["parameters.yaml or other-marks.yaml cannot be read: %s" % exc]
    reg = pbs.open_register(policy)
    entries = {e.get("open_id"): e for e in params.get("parameters") or [] if isinstance(e, dict)}
    idx = pbs.other_marks_index(other)
    for _rs, row in pbs.policy_rows(policy):
        handling = pbs.effective_handling(row, idx.get(row["rule_id"]) if pbs.is_other_mark(row) else None)
        if handling == "not-run" or pbs.rule_open_of(row, policy):
            continue
        for oid in pbs.value_opens_of(row, policy):
            e = entries.get(oid)
            if e is None:
                p.append("rule %s runs with the value open %s, which has no provisional parameter entry"
                         % (row["rule_id"], oid))
            elif e.get("label") != pbs.PROVISIONAL_LABEL:
                p.append("parameter %s must be labelled %r" % (oid, pbs.PROVISIONAL_LABEL))
    for oid, e in entries.items():
        if (reg.get(oid) or {}).get("kind") != "value":
            p.append("parameter %s names no value open of the pinned policy (a rule open is never given a value)"
                     % oid)
        if not str(e.get("confirmed_by") or "").strip():
            p.append("parameter %s has no confirmed_by (a human.decision event id or fast-mode-policy)" % oid)
    # case classes cover every case
    try:
        cc = pbs.read_pinned(root, m["data"]["case_classes"], "yaml", "the pinned case-classes.yaml") or {}
        covered, gaps = load_coverage_fn()(cc.get("corpus") or {"classes": []},
                                            {"case_classes": cc.get("case_classes") or []})
    except (pbs.PrebuildError, KeyError, TypeError) as exc:
        return p + ["case-classes.yaml cannot be read: %s" % exc]
    class_ids = {c.get("id") for c in cc.get("case_classes") or [] if isinstance(c, dict)}
    for _g, case in pbs.eval_cases(eval_set):
        if case.get("id") not in class_ids:
            p.append("Test Suite case %s has no case class in case-classes.yaml" % case.get("id"))
    if not covered:
        p.append("the case-class manifest is not covered: %s" % "; ".join(gaps))
    # walk and case data
    try:
        walk = pbs.read_pinned(root, m["rules"]["walk"], "yaml", "the pinned walk.yaml") or {}
        case_data = pbs.read_pinned(root, m["data"]["case_data"], "yaml", "the pinned case-data.yaml") or {}
    except (pbs.PrebuildError, KeyError, TypeError) as exc:
        return p + ["walk.yaml or case-data.yaml cannot be read: %s" % exc]
    rows = pbs.rows_by_id(policy)
    if walk.get("schema") != "walk/v1":
        p.append("walk.yaml must carry schema walk/v1")
    for u in walk.get("units") or []:
        for rid in u.get("order") or []:
            if rid not in rows:
                p.append("walk unit %s names rule %s, which the policy does not carry" % (u.get("id"), rid))
    for o in walk.get("overrides") or []:
        if pbs.nonspace_len((o or {}).get("reason")) < pbs.MIN_REASON:
            p.append("walk override %r states no reason of at least %d non-space characters"
                     % ((o or {}).get("what"), pbs.MIN_REASON))
    if case_data.get("schema") != "case-data/v1":
        p.append("case-data.yaml must carry schema case-data/v1")
    data_paths = {f.get("path") for k in ("client", "synthetic") for f in (m.get("data") or {}).get(k) or []}
    for c in case_data.get("cases") or []:
        for unit, spec in ((c or {}).get("units") or {}).items():
            for inp in (spec or {}).get("inputs") or []:
                if inp.get("file") not in data_paths:
                    p.append("case %s unit %s reads %s, which the manifest does not pin as data"
                             % (c.get("case_id"), unit, inp.get("file")))
    p.extend(fold_findings(root, m, msha, events))
    return p


def derived_outcome_findings(root, m):
    """data.derived_expected_outcomes: {path, sha256} when the derived expected outcomes exist, else null."""
    data = m.get("data") if isinstance(m.get("data"), dict) else {}
    exists = os.path.isfile(os.path.join(root, pbs.DERIVED_EO_PATH))
    if "derived_expected_outcomes" not in data:
        return ["data.derived_expected_outcomes is missing; it must pin %s when that file exists, and be null when "
                "it does not" % pbs.DERIVED_EO_PATH]
    pin = data["derived_expected_outcomes"]
    if pin is None:
        if exists:
            return ["data.derived_expected_outcomes is null, but %s exists; the manifest must pin it"
                    % pbs.DERIVED_EO_PATH]
        return []
    if not (isinstance(pin, dict) and isinstance(pin.get("path"), str) and isinstance(pin.get("sha256"), str)):
        return ["data.derived_expected_outcomes must be {path, sha256} or null"]
    if not os.path.isfile(os.path.join(root, pin["path"])):
        return ["data.derived_expected_outcomes points at %s, which does not exist" % pin["path"]]
    return []


def data_label(path, text):
    """The data_label of the data file at PATH, from TEXT (its content, read through its pin)."""
    import json
    import re
    ext = os.path.splitext(path)[1].lower()
    try:
        if ext == ".json":
            doc = json.loads(text)
            return doc.get("data_label") if isinstance(doc, dict) else None
        if ext in (".yaml", ".yml"):
            doc = pbs.yaml_mod().safe_load(text)
            return doc.get("data_label") if isinstance(doc, dict) else None
    except pbs.PrebuildError:
        raise
    except Exception:  # noqa: BLE001 - a data file that does not parse carries no readable label
        return None
    for line in text.splitlines():
        mm = re.match(r"^\s*#\s*data_label:\s*(\S+)", line)
        if mm:
            return mm.group(1)
        if line.strip() and not line.lstrip().startswith("#"):
            break
    return None


def fold_findings(root, m, msha, events):
    """Every changes-requested review the event log records against an earlier submission must be folded into this
    one, and must meet every record rule (pbs.validate_record, which re-checks its feedback file against every
    feedback rule in the context rebuilt from the record's own pins). Which reviews exist is read from the log only
    (pbs.logged_reviews: each prebuild-simulation-review decision and the record it pins), never from a listing of
    the reviews folder: a logged review whose record is missing or changed is a finding naming it, and a file no
    decision names is ignored. Each record is read through its decision's pin before anything in it is used. A
    folded item counts only when it is what the record says (pbs.fold_problems): a matching id alone never does.
    EVENTS is the run's log (required by the caller; a missing log is refused, never read as empty)."""
    out = []
    # a list keyed by path or id below never drops a second entry silently: folded_reviews naming one record twice,
    # or a pinned defects.yaml holding one defect id twice, is a finding naming it
    paths = [(f.get("record") or {}).get("path") for f in m.get("folded_reviews") or [] if isinstance(f, dict)]
    for rel in sorted({x for x in paths if paths.count(x) > 1}, key=str):
        out.append("folded_reviews lists review record %s more than once; each folded review is listed once, and a "
                   "second entry is never left unchecked" % rel)
    dread = pbs.pinned_read(root, m.get("defects"), "yaml", "the pinned defects.yaml")
    if dread.ok:   # a defects.yaml that is not the pinned file is named by the pin findings
        out.extend(pbs.duplicate_defect_ids(dread.content, "the pinned defects.yaml"))
    logged = [lr for lr in pbs.logged_reviews(events) if lr.decision == "changes-requested"
              and (lr.event.get("payload") or {}).get("manifest_sha256") != msha]
    if not logged:
        return out
    folded = {(f.get("record") or {}).get("path"): f for f in m.get("folded_reviews") or [] if isinstance(f, dict)}
    try:
        defects = pbs.read_pinned(root, m["defects"], "yaml", "the pinned defects.yaml") or {}
        decl_pin = (m.get("inputs") or {}).get("data_declaration")
        decl = pbs.read_pinned(root, decl_pin, "yaml", "the pinned data declaration") if decl_pin else {}
    except (pbs.PrebuildError, KeyError, TypeError) as exc:
        return ["defects.yaml or the declaration cannot be read for the fold check: %s" % exc]
    for lr in logged:
        rel = lr.rel
        read = pbs.read_logged_review(root, lr)
        if not read.ok:
            out.append("the changes-requested review %s, which the event log records (decision %s), cannot be "
                       "checked: %s" % (rel, lr.event.get("event_id"), read.reason))
        else:
            for problem in pbs.validate_record(read.content, rel, root, events):
                out.append("review record %s breaks its written rules: %s" % (rel, problem))
        f = folded.get(rel)
        rid = pbs.record_id(rel)
        if f is None:
            out.append("the changes-requested review %s was recorded against an earlier submission and is not "
                       "folded into this manifest (folded_reviews)" % rel)
            continue
        # what is compared with the pinned defects.yaml and declaration is the record read through the manifest's
        # folded_reviews pin, never the file as it lies on disk
        read = pbs.pinned_read(root, f.get("record"), "yaml", "the folded review record")
        if not read.ok:
            out.append("folded_reviews pins %s at a sha that is not the record's current sha (%s)" % (rel, read.reason))
            continue
        rec = read.content if isinstance(read.content, dict) else {}
        for problem in pbs.fold_problems(rec, rid, defects, decl, lr.event.get("event_id")):
            out.append("review %s is not folded in as recorded: %s (the pinned defects.yaml and declaration, "
                       "from_review_record %s)" % (rel, problem, rid))
    return out


def main(argv):
    args = [a for a in argv if not a.startswith("--")]
    if len(args) != 1:
        sys.stderr.write("usage: check-prebuild-sim-structure.sh MANIFEST\n")
        return 2
    root = os.getcwd()
    try:
        problems = check(root, args[0])
    except pbs.PrebuildError as exc:
        sys.stderr.write("prebuild-sim-structure: %s\n" % exc)
        return 2
    if problems:
        sys.stderr.write("pre-build simulation structure check FAILED for %s:\n  - %s\n" % (args[0],
                                                                                        "\n  - ".join(problems)))
        return 1
    print("PASS prebuild-sim-structure: the manifest is whole; every pinned file is the file it pinned; every data "
          "shape, label, provisional value and case class is in place.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
