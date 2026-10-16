#!/usr/bin/env python3
# This file is Python despite its .sh name: run it directly or as `python3 <this file>`; `bash <this file>` cannot run it.
"""Pre-build simulation data-boundary check (stage 11) -- no answers in the data, and the labels are true.

Plain-language handle: "check the case data holds raw records only, and client data is never called synthetic".
One of stage 11's four checks (plan 4.6). Run with the run root as the working directory.

Fails (exit 1), each finding listed, on:
  1. a column or key in a data file that is not a field of its system's data-shape record (a synthetic file cannot
     add a convenient net_balance column). CSV reads its header row; JSON and YAML read the keys of their records
     (a top-level `records` or `rows` list, or a top-level list); a report-export layout reads every header row
     (a row naming at least two of the record's fields), whose every cell must be a field;
  2. a data file or the case-data map that contains the decision policy's sha or path, any rule id, gate id or
     escalation-trigger text, any Test Suite case's `expected` text, or any expected-outcome value string of 6
     or more characters that is not also a value in the client sample (a case's `expected_outcome`, or where it
     has none the derived expectation the manifest pins at data.derived_expected_outcomes);
  3. a generation-input record (generation-inputs.json) that lists the decision policy, the Test Suite, the derived
     expected-outcomes file, its judge rubrics or its calibration set (by path or sha), or a synthetic data file the record does not list among
     its outputs (every invented file comes from the generation sub-step), or no record while synthetic data
     exists;
  4. a client data file whose header calls it synthetic, or that is labelled anything but client data.

Every file it reads through the manifest is read through its pin (checks/_prebuild_sim.py read_pinned / pinned_read):
a pinned data file that is not the pinned file is a finding naming it (never skipped, never read as if it were); a
pinned policy, Test Suite, derived expectation, data-shape record or generation-input record that is not the pinned
file is a refusal (exit 2) naming it.

Usage: check-prebuild-sim-data-boundary.sh MANIFEST
Exit: 0 pass; 1 findings; 2 setup error.
"""
import csv
import io
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _prebuild_sim as pbs  # noqa: E402

EXCLUDED_NAMES = ("calibration-set", "judge-rubrics", "eval-set", "decision-policy")


def header_lines(text):
    return [l for l in text.splitlines() if l.lstrip().startswith("#")]


def columns_of(path, rec, text):
    """(columns, how) of the data file at PATH whose content (read through its pin) is TEXT."""
    ext = os.path.splitext(path)[1].lower()
    fields = {f.get("name") for f in rec.get("fields") or [] if isinstance(f, dict)}
    container = ((rec.get("format") or {}).get("container") or "").lower()
    if ext in (".json", ".yaml", ".yml"):
        try:
            doc = json.loads(text) if ext == ".json" else pbs.yaml_mod().safe_load(text)
        except pbs.PrebuildError:
            raise
        except Exception as exc:  # noqa: BLE001 - a data file that does not parse is named
            raise pbs.PrebuildError("cannot parse %s: %s" % (path, exc))
        records = doc
        if isinstance(doc, dict):
            records = doc.get("records", doc.get("rows"))
            if records is None:
                return sorted(k for k in doc if k != "data_label"), "keys"
        cols = set()
        for r in records or []:
            if isinstance(r, dict):
                cols.update(k for k in r if k != "data_label")
        return sorted(cols), "record keys"
    body = "\n".join(l for l in text.splitlines() if not l.lstrip().startswith("#"))
    rows = [r for r in csv.reader(io.StringIO(body)) if any(c.strip() for c in r)]
    if container == "report-export" or ext not in (".csv",):
        cols = set()
        for r in rows:
            cells = [c.strip() for c in r if c.strip()]
            if sum(1 for c in cells if c in fields) >= 2:
                cols.update(cells)
        return sorted(cols), "header rows"
    return ([c.strip() for c in rows[0] if c.strip()] if rows else []), "header row"


def check(root, mrel):
    m = pbs.load_manifest(root, mrel)
    p = []
    policy_pin = m["inputs"]["decision_policy"]
    policy = pbs.read_pinned(root, policy_pin, "yaml", "the pinned decision policy") or {}
    eval_pin = m["inputs"]["eval_set"]
    eval_set = pbs.read_pinned(root, eval_pin, "yaml", "the pinned Test Suite") or {}
    derived = pbs.derived_outcomes(root, m)
    records = {}
    for s in m.get("systems") or []:
        records[s.get("system_id")] = pbs.read_pinned(root, s["data_shape"], "yaml", "the data-shape record of %s"
                                                      % s.get("system_id")) or {}
    data = m.get("data") or {}
    client_files = data.get("client") or []
    synth_files = data.get("synthetic") or []
    # every data file and the case-data map, each read once through its pin; one that is not the pinned file is a
    # finding naming it and is not scanned
    texts = {}
    for f in client_files + synth_files + [data["case_data"]]:
        read = pbs.pinned_read(root, f, "text", "data file" if f is not data["case_data"] else "the case-data map")
        if read.ok:
            texts[f["path"]] = read.content
        else:
            p.append("%s is not checked: %s" % (f.get("path"), read.reason))
    client_text = "\n".join(texts[f["path"]] for f in client_files if f["path"] in texts)
    # 1. columns are fields
    for f in client_files + synth_files:
        path = os.path.join(root, f["path"])
        rec = records.get(f.get("system_id"))
        if rec is None:
            p.append("data file %s names system %r, which has no data-shape record" % (f["path"], f.get("system_id")))
            continue
        if f["path"] not in texts:
            continue
        fields = {x.get("name") for x in rec.get("fields") or [] if isinstance(x, dict)}
        try:
            cols, how = columns_of(path, rec, texts[f["path"]])
        except pbs.PrebuildError as exc:
            p.append("data file %s cannot be read: %s" % (f["path"], exc))
            continue
        extra = [c for c in cols if c not in fields]
        if extra:
            p.append("data file %s has %s %s that are not fields of %s's data-shape record (raw records only; no "
                     "answers in the data)" % (f["path"], how, ", ".join(extra), f.get("system_id")))
    # 2. no answers in the data
    forbidden = [(policy_pin["sha256"], "the decision policy's sha"), (policy_pin["path"], "the decision policy's path")]
    for _rs, row in pbs.policy_rows(policy):
        forbidden.append((row["rule_id"], "rule id %s" % row["rule_id"]))
    for g in policy.get("gates") or []:
        if g.get("gate_id"):
            forbidden.append((g["gate_id"], "gate id %s" % g["gate_id"]))
    for t in policy.get("escalation_triggers") or []:
        if isinstance(t.get("trigger"), str) and len(t["trigger"]) >= 6:
            forbidden.append((t["trigger"], "escalation-trigger text"))
    for _g, case in pbs.eval_cases(eval_set):
        exp = case.get("expected")
        if isinstance(exp, str) and exp.strip():
            forbidden.append((" ".join(exp.split()), "case %s's expected text" % case.get("id")))
        eo = pbs.expected_outcome_of(case, derived)[0] or {}
        vals = [eo.get("end_state"), eo.get("at")] + [v for v in (eo.get("tokens") or {}).values()
                                                      if v not in ("present", "absent")]
        for v in vals:
            if isinstance(v, str) and len(v) >= 6 and v not in client_text:
                forbidden.append((v, "case %s's expected outcome value %r" % (case.get("id"), v)))
    scan = [f["path"] for f in client_files + synth_files] + [data["case_data"]["path"]]
    for rel in scan:
        if rel not in texts:
            continue
        text = texts[rel]
        body = re.sub(r"(?m)^\s*#?\s*\"?data_label\"?\s*:.*$", "", text)
        flat = " ".join(body.split())
        for needle, what in forbidden:
            n = " ".join(str(needle).split())
            if not n:
                continue
            pat = r"(?<![\w-])%s(?![\w-])" % re.escape(n)
            if re.search(pat, flat):
                p.append("%s contains %s; data holds raw records only, never an answer" % (rel, what))
    # 3. the generation input record
    gen_pin = data.get("generation_inputs")
    gen = pbs.read_pinned(root, gen_pin, "json", "the pinned generation-input record") if gen_pin else None
    if synth_files and not gen:
        p.append("synthetic data exists but there is no generation-input record")
    if gen is not None:
        excluded_shas = {policy_pin["sha256"], eval_pin["sha256"]}
        excluded_paths = {policy_pin["path"], eval_pin["path"]}
        # the derived expected-outcomes file is an answer source for older suites, held out like the Test Suite
        derived_pin = data.get("derived_expected_outcomes")
        if isinstance(derived_pin, dict) and derived_pin.get("path"):
            excluded_paths.add(derived_pin["path"])
            if derived_pin.get("sha256"):
                excluded_shas.add(derived_pin["sha256"])
        for f in gen.get("files") or []:
            path = (f or {}).get("path", "")
            base = os.path.basename(path)
            if path in excluded_paths or (f or {}).get("sha256") in excluded_shas or \
                    any(base.startswith(x) for x in EXCLUDED_NAMES):
                p.append("the generation sub-step was given %s; it may never see the decision policy, the expected "
                         "outcomes, the judge rubrics or the calibration set" % path)
        outputs = set(gen.get("outputs") or [])
        for f in synth_files:
            if f["path"] not in outputs:
                p.append("synthetic data file %s is not among the generation sub-step's recorded outputs" % f["path"])
    # 4. client data is never called synthetic
    for f in client_files:
        if f["path"] not in texts:
            continue
        head = "\n".join(header_lines(texts[f["path"]])[:20]).lower()
        if "synthetic" in head:
            p.append("client data file %s calls itself synthetic in its header; client data (including a redacted "
                     "copy) is never synthetic" % f["path"])
    return p


def main(argv):
    args = [a for a in argv if not a.startswith("--")]
    if len(args) != 1:
        sys.stderr.write("usage: check-prebuild-sim-data-boundary.sh MANIFEST\n")
        return 2
    try:
        problems = check(os.getcwd(), args[0])
    except (pbs.PrebuildError, KeyError, TypeError) as exc:
        sys.stderr.write("prebuild-sim-data-boundary: %s\n" % exc)
        return 2
    if problems:
        sys.stderr.write("pre-build simulation data-boundary check FAILED for %s:\n  - %s\n" % (args[0],
                                                                                            "\n  - ".join(problems)))
        return 1
    print("PASS prebuild-sim-data-boundary: raw records only; every label true; generation never saw the policy "
          "or the expectations.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
