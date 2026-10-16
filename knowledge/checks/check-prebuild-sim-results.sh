#!/usr/bin/env python3
# This file is Python despite its .sh name: run it directly or as `python3 <this file>`; `bash <this file>` cannot run it.
"""Pre-build simulation results check (stage 11) -- the recorded results, comparison, coverage and defects hold.

Plain-language handle: "check the pre-build simulation's results are whole and every derived file matches what
the results say". One of stage 11's four checks (plan 5.2). It re-derives the comparison and the coverage from
the final run's results and the Test Suite's expected outcomes with the same functions that wrote them
(checks/_prebuild_sim.py, through scripts/prebuild-sim-compare.py) and requires byte equality. Run with the run
root as the working directory.

Fails (exit 1), each finding listed, on:
  * the final run's results, the policy, the Test Suite or a rules-code module no longer at the sha the manifest
    binds them to;
  * a Test Suite case with no results row, or a row that neither ran nor states why not (a not_run reason of at
    least 20 non-space characters);
  * comparison.yaml or coverage.yaml that differs from its re-derivation; a coverage statement whose machine block
    is not coverage.yaml, or that says the agent works, is ready, or passed;
  * a defect without an owner or with an unknown class or status; an open `code` defect (the stage owns those and
    fixes them itself); a `dispute` defect still unclassified (the next round reclassifies it as case, rule or
    mismatch);
  * a judgment step whose responses do not cover slots 1..repeats exactly once, whose response ids are not all
    distinct (two slots carrying the same response is a collapsed repeat, never independent evidence), or whose
    response files are not the cache entries for that step's prompt sha and slot;
  * a malformed other-marks.yaml entry (its rule_id not an other-marked rule of the pinned policy, handling not
    code, judgment or not-run, or a basis under 20 non-space characters), named, never a refusal of the rule.
A rule recorded not_run with its reason (an other-marked rule) always passes.
Every file it reads is read through the manifest's pin (checks/_prebuild_sim.py read_pinned; the re-derivation
through config_from_manifest's pins): a pinned file that is not the pinned file is refused (exit 2) naming it,
never read as if it were.

Usage: check-prebuild-sim-results.sh MANIFEST
Exit: 0 pass; 1 findings; 2 setup error.
"""
import importlib.util
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import _prebuild_sim as pbs  # noqa: E402


def compare_module():
    path = os.path.join(os.path.dirname(HERE), "scripts/prebuild-sim-compare.py")
    spec = importlib.util.spec_from_file_location("_pbs_compare_for_check", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def check(root, mrel):
    m = pbs.load_manifest(root, mrel)
    p = []
    fr = pbs.final_run(m)
    if fr is None:
        return ["final_run_id %r names no entry in runs[]" % m.get("final_run_id")]
    bound = [("the final run's results", fr["results"]), ("its comparison", fr["comparison"]),
             ("the decision policy", m["inputs"]["decision_policy"]), ("the Test Suite", m["inputs"]["eval_set"])]
    bound += [("rules code %s" % c["path"], c) for c in m["rules"].get("code_modules") or []]
    for what, pin in bound:
        if pbs.pin_reason(root, pin, what) is not None:
            p.append("%s (%s) is not at the sha the manifest binds the results to" % (what, pin["path"]))
    if p:
        return p
    eval_set = pbs.read_pinned(root, m["inputs"]["eval_set"], "yaml", "the pinned Test Suite") or {}
    policy = pbs.read_pinned(root, m["inputs"]["decision_policy"], "yaml", "the pinned decision policy") or {}
    rows = pbs.results_rows(root, m)
    by_case = {r.get("case_id"): r for r in rows}
    for _g, case in pbs.eval_cases(eval_set):
        r = by_case.get(case.get("id"))
        if r is None:
            p.append("Test Suite case %s has no results row" % case.get("id"))
        elif not r.get("units") and pbs.nonspace_len(r.get("not_run_reason")) < pbs.MIN_REASON:
            p.append("case %s neither ran nor states why not (a not_run reason of at least %d non-space characters)"
                     % (case.get("id"), pbs.MIN_REASON))
    # re-derive comparison and coverage
    cfg = pbs.config_from_manifest(m, root)
    try:
        comparison, coverage, _derived = compare_module().derive_all(root, cfg, fr["run_id"])
    except (pbs.PrebuildError, OSError, KeyError) as exc:
        return p + ["the comparison and coverage cannot be re-derived: %s" % exc]
    if pbs.read_pinned(root, fr["comparison"], "text", "the final run's comparison") != pbs.dump_yaml(comparison):
        p.append("comparison.yaml differs from the comparison re-derived from the results and the expected outcomes")
    cov_text = pbs.read_pinned(root, m["coverage"]["data"], "text", "the pinned coverage.yaml")
    if cov_text != pbs.dump_yaml(coverage):
        p.append("coverage.yaml differs from the coverage re-derived from the results")
    stmt = pbs.read_pinned(root, m["coverage"]["statement"], "text", "the pinned coverage statement")
    try:
        block = pbs.statement_block(stmt)
    except Exception as exc:  # noqa: BLE001
        block = None
        p.append("the coverage statement's machine block cannot be read: %s" % exc)
    if block != pbs.yaml_mod().safe_load(cov_text):
        p.append("the coverage statement's machine block (<!-- COVERAGE ... -->) is not coverage.yaml")
    claims = pbs.banned_claims_in(stmt)
    if claims:
        p.append("the coverage statement claims %s; it never says the agent works, is ready, or passed"
                 % ", ".join(repr(c) for c in claims))
    # defects
    defects = pbs.read_pinned(root, m["defects"], "yaml", "the pinned defects.yaml") or {}
    for d in defects.get("defects") or []:
        did = d.get("id")
        if d.get("class") not in pbs.DEFECT_CLASSES:
            p.append("defect %s has class %r" % (did, d.get("class")))
        if d.get("status") not in pbs.DEFECT_STATUSES:
            p.append("defect %s has status %r" % (did, d.get("status")))
        if d.get("class") == "dispute":
            p.append("defect %s is a business owner's dispute still unclassified; the round's routing step must "
                     "reclassify it as case, rule or mismatch, keeping the note verbatim" % did)
        elif not str(d.get("owner_stage") or "").strip():
            p.append("defect %s has no owner (owner_stage)" % did)
        if d.get("class") == "code" and d.get("status") in ("open", "routed", "escalated"):
            p.append("defect %s is an open code defect; the stage owns those and must fix them itself" % did)
    # judgment evidence: independent repeats
    for r in rows:
        for unit in r.get("units") or []:
            for s in unit.get("steps") or []:
                j = s.get("judgment")
                if not j:
                    continue
                where = "case %s rule %s %s step" % (r.get("case_id"), s.get("rule_id"), s.get("kind"))
                k = j.get("repeats")
                slots = [x.get("slot") for x in j.get("responses") or []]
                if not isinstance(k, int) or sorted(slots) != list(range(1, k + 1)):
                    p.append("%s: its responses cover slots %s, not 1..%s exactly once" % (where, slots, k))
                ids = [x.get("response_id") for x in j.get("responses") or []]
                if len(set(ids)) != len(ids) or any(not i for i in ids):
                    p.append("%s: its response ids are not all distinct (a collapsed repeat is never independent "
                             "evidence)" % where)
                psha = (j.get("prompt") or {}).get("sha256")
                for x in j.get("responses") or []:
                    want = "%s/judgment-cache/%s/slot-%s.json" % (pbs.OUT, psha, x.get("slot"))
                    # the cache entry through the response's own pin (its recorded path and sha256)
                    read = pbs.pinned_read(root, x, "json", "the cache entry") if x.get("path") == want else None
                    if read is None or read.state in ("missing", "no-pin"):
                        p.append("%s: slot %s's response is not the cache entry %s" % (where, x.get("slot"), want))
                        continue
                    entry = read.content if read.ok and isinstance(read.content, dict) else {}
                    if (not read.ok or entry.get("prompt_sha256") != psha or entry.get("slot") != x.get("slot")
                            or entry.get("response_id") != x.get("response_id")):
                        p.append("%s: slot %s's cache entry does not match the recorded response" % (where,
                                                                                                  x.get("slot")))
    # other-marks shape
    om_pin = m["rules"].get("other_marks")
    if om_pin:
        om = pbs.read_pinned(root, om_pin, "yaml", "the pinned other-marks.yaml") or {}
        rows_by = pbs.rows_by_id(policy)
        if om.get("schema") != "other-marks/v1":
            p.append("other-marks.yaml must carry schema other-marks/v1")
        for e in om.get("rules") or []:
            rid = (e or {}).get("rule_id")
            row = rows_by.get(rid)
            if row is None or not pbs.is_other_mark(row):
                p.append("other-marks.yaml entry %r is not an other-marked rule of the pinned policy" % rid)
            if (e or {}).get("handling") not in pbs.HANDLINGS:
                p.append("other-marks.yaml entry %r has handling %r (code, judgment or not-run)"
                         % (rid, (e or {}).get("handling")))
            if pbs.nonspace_len((e or {}).get("basis")) < pbs.MIN_REASON:
                p.append("other-marks.yaml entry %r needs a basis of at least %d non-space characters"
                         % (rid, pbs.MIN_REASON))
    return p


def main(argv):
    args = [a for a in argv if not a.startswith("--")]
    if len(args) != 1:
        sys.stderr.write("usage: check-prebuild-sim-results.sh MANIFEST\n")
        return 2
    try:
        problems = check(os.getcwd(), args[0])
    except (pbs.PrebuildError, KeyError, TypeError, ValueError) as exc:
        sys.stderr.write("prebuild-sim-results: %s\n" % exc)
        return 2
    if problems:
        sys.stderr.write("pre-build simulation results check FAILED for %s:\n  - %s\n" % (args[0],
                                                                                      "\n  - ".join(problems)))
        return 1
    print("PASS prebuild-sim-results: every case ran or says why; comparison and coverage re-derive byte for byte; "
          "every defect has an owner; every judgment step carries independent repeats.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
