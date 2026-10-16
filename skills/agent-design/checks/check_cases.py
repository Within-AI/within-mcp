"""check_cases: the test suite against brief criterion 14.

Usage: /usr/bin/python3 skills/agent-design/checks/check_cases.py --run <run_dir> [--out <report.json>]
Exit 0 pass, 1 fail, 2 usage or unreadable input. Standard library only, Python 3.9.

Every problem is a recorded finding in the report; only input that will not parse stops (exit 2). The report
also records layer coverage: `coverage: {<layer>: "complete" | "incomplete" | "omitted" | "not_applicable" |
"missing"}` with `coverage_reasons`, and `suite_status` "incomplete" when a layer is covered only by
exclusions. A layer is applicable when the design has a rule or gate in it (handover: a gate, or two or more
parts); an applicable layer with neither cases nor exclusions fails, and declaring it omitted does not cover it.
Only an exclusion that carries a non-empty `reason` and `evidence_state` counts toward its layer's coverage; one
without them is listed in `notes` and covers no layer. A case whose content parses but has the wrong shape is a
recorded `case_malformed` finding and is left out of the rest of the check.
"""
import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import compute_expected  # noqa: E402
import evidence_ids  # noqa: E402
import person_gates as person_gate_reading  # noqa: E402  the one reading of which cases need a person's decision

CHECK = "check_cases"
PATH = "skills/agent-design/checks/check_cases.py"
SUITE = "stages/test-suite/test-suite.json"
OUTCOMES = "stages/rules/outcomes.json"
OPEN_ITEMS = "stages/rules/open-items.json"
AGENT = "stages/architecture/agent.yaml"
BOUNDARIES = "stages/boundaries/boundaries.json"
INTAKE = "stages/intake/intake.json"
RULES = "stages/rules/rules.json"
DIRECTIONS = ("credit", "debit", "needs_more_information")
FALLBACK = ("If this check cannot run, an agent reads stages/test-suite/test-suite.json against "
            "stages/rules/outcomes.json and lists each criterion 14 defect by name; the stage stays unfinished.")

KINDS = ("positive", "must_escalate", "boundary")
LAYERS = ("part", "handover", "end_to_end", "authority")
MISSING_RULE = re.compile(r"^missing rule:\s*\S")


class Unreadable(Exception):
    pass


def _load(run_dir, rel):
    p = os.path.join(run_dir, rel)
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError) as e:
        raise Unreadable("%s: %s" % (rel, e))


def _items(doc, key):
    if isinstance(doc, list):
        return doc
    if isinstance(doc, dict) and isinstance(doc.get(key), list):
        return doc[key]
    raise Unreadable("expected a list or {\"%s\": [...]}" % key)


EXCLUSION_KEYS = {"id", "decision", "kind", "layer", "critical", "description", "exclusion", "reason",
                  "evidence_state"}


def _is_exclusion(case):
    return isinstance(case, dict) and "exclusion" in case


def _malformed(case):
    """Why an entry is neither a well-formed ordinary case nor a well-formed exclusion (U2's case.schema.json), or None."""
    if not isinstance(case, dict):
        return "entry is not an object"
    for key in ("id", "decision"):
        if not isinstance(case.get(key), str) or not case[key]:
            return "%s is missing or not a non-empty string" % key
    if "kind" in case and case["kind"] not in KINDS:
        return "kind %r is not one of %s" % (case["kind"], ", ".join(KINDS))
    if "layer" in case and case["layer"] not in LAYERS:
        return "layer %r is not one of %s" % (case["layer"], ", ".join(LAYERS))
    if _is_exclusion(case):
        extra = sorted(set(case) - EXCLUSION_KEYS)
        if extra:
            return "exclusion carries fields an exclusion may not have: %s" % ", ".join(extra)
        exc = case["exclusion"]
        if not isinstance(exc, dict):
            return "exclusion is not an object"
        if set(exc) != {"missing", "stamp"}:
            return "exclusion must hold exactly missing and stamp, has: %s" % (", ".join(sorted(exc)) or "nothing")
        if not isinstance(exc["missing"], str) or not exc["missing"]:
            return "exclusion.missing is not a non-empty string"
        if exc["stamp"] != "needs_more_information":
            return "exclusion.stamp is %r, not 'needs_more_information'" % exc["stamp"]
        for key in ("reason", "evidence_state"):
            if key in case and (not isinstance(case[key], str) or not case[key].strip()):
                return "exclusion %s is not a non-empty string" % key
        return None
    for key in ("kind", "layer"):
        if key not in case:
            return "ordinary case has no %s" % key
    if not isinstance(case.get("expected"), dict):
        return "ordinary case has no expected outcome object"
    if not isinstance(case.get("rows"), list):
        return "ordinary case has no rows list"
    for i, row in enumerate(case["rows"]):
        if not isinstance(row, dict) or not isinstance(row.get("values", {}), dict):
            return "rows[%d] is not an object with a values object" % i
    outcomes = [("expected", case["expected"])]
    alts = case.get("allowed_alternatives")
    if alts is not None:
        if not isinstance(alts, list) or not all(isinstance(a, dict) for a in alts):
            return "allowed_alternatives is not a list of objects"
        outcomes += [("allowed_alternatives[%d]" % i, a) for i, a in enumerate(alts)]
    for name, out in outcomes:
        for key in ("values", "derivations", "amount_direction"):
            if out.get(key) is not None and not isinstance(out[key], dict):
                return "%s.%s is not an object" % (name, key)
    for key in ("holds", "judgment_answers"):
        entries = case.get(key)
        if entries is not None and (not isinstance(entries, list) or not all(isinstance(e, dict) for e in entries)):
            return "%s is not a list of objects" % key
    if case.get("interface") is not None and not isinstance(case["interface"], dict):
        return "interface is not an object"
    return None


def _reads(node):
    if not isinstance(node, dict):
        return []
    if compute_expected.is_named(node):
        inputs = node.get("inputs") if isinstance(node.get("inputs"), dict) else {}
        return [v["read"] for v in inputs.values() if isinstance(v, dict) and "read" in v]
    if node.get("kind") == "read":
        return [node.get("token")]
    out = []
    for a in node.get("args") if isinstance(node.get("args"), list) else []:
        out.extend(_reads(a))
    return out


def _outcome_labels(case):
    labels = []
    exp = case.get("expected")
    if isinstance(exp, dict) and exp.get("label"):
        labels.append(exp["label"])
    for alt in case.get("allowed_alternatives") or []:
        if isinstance(alt, dict) and alt.get("label"):
            labels.append(alt["label"])
    return labels


def _load_optional(run_dir, rel):
    if not os.path.isfile(os.path.join(run_dir, rel)):
        return None
    return _load(run_dir, rel)


def _declared_directions(rules_doc):
    """Token name -> amount_direction for every token rules.json declares one on."""
    out = {}
    tokens = rules_doc.get("tokens") if isinstance(rules_doc, dict) else None
    for t in tokens if isinstance(tokens, list) else []:
        if isinstance(t, dict) and isinstance(t.get("amount_direction"), str):
            name = t.get("token") or t.get("name") or t.get("id")
            if isinstance(name, str):
                out[name] = t["amount_direction"]
    return out


def _report(problems, counts, exit_code=None, error=None, extra=None):
    if error is not None:
        result, code = "error", 2
        problems = [{"defect": "unreadable_input", "where": "", "detail": error}]
    elif problems:
        result, code = "fail", 1
    else:
        result, code = "pass", 0
    report = {"check": CHECK, "path": PATH, "run": SUITE, "exit_code": code, "result": result,
              "problems": problems, "counts": counts, "agent_fallback": FALLBACK}
    report.update(extra or {})
    return report


def _check(run_dir):
    suite = _load(run_dir, SUITE)
    outcomes = _load(run_dir, OUTCOMES)
    open_items = _items(_load(run_dir, OPEN_ITEMS), "items")
    agent = _load(run_dir, AGENT)
    boundaries = _items(_load(run_dir, BOUNDARIES), "boundaries")
    if not isinstance(suite, dict) or not isinstance(suite.get("cases"), list):
        raise Unreadable("%s: no cases list" % SUITE)

    problems, notes = [], []
    rules_doc = _load_optional(run_dir, RULES)
    directions_declared = _declared_directions(rules_doc)

    def bad(defect, where, detail):
        problems.append({"defect": defect, "where": where, "detail": detail})

    decisions = {d["id"]: list(d.get("labels") or []) for d in outcomes.get("decisions") or []}
    gates = {g["id"]: list(g.get("labels") or []) for g in outcomes.get("gates") or []}
    all_labels = set()
    for labels in list(decisions.values()) + list(gates.values()):
        all_labels.update(labels)
    label_gates = {}
    for gid, labels in gates.items():
        for lab in labels:
            label_gates.setdefault(lab, set()).add(gid)
    open_ids = [str(i.get("id")) for i in open_items if isinstance(i, dict) and i.get("id")]
    parts = agent.get("parts") or []

    cases = []
    for i, c in enumerate(suite["cases"]):
        why = _malformed(c)
        if why:
            cid = c.get("id") if isinstance(c, dict) and isinstance(c.get("id"), str) else "cases[%d]" % i
            bad("case_malformed", cid, "case is malformed and left out of the check: %s" % why)
        else:
            cases.append(c)
    ordinary = [c for c in cases if isinstance(c, dict) and not _is_exclusion(c)]
    exclusions = [c for c in cases if _is_exclusion(c)]

    # (t), (u) suite status
    if suite.get("status") != "simulation-only":
        bad("suite_not_simulation_only", SUITE, "status is %r, not 'simulation-only'" % suite.get("status"))
    if suite.get("numeric_floors") != "open":
        bad("numeric_floor_not_open", SUITE, "numeric_floors is %r, not 'open'" % suite.get("numeric_floors"))

    # (b) exclusions must name an open item or a missing rule
    for c in exclusions:
        missing = str((c.get("exclusion") or {}).get("missing") or "")
        if not (MISSING_RULE.match(missing) or any(oid in missing for oid in open_ids)):
            bad("exclusion_names_nothing", c.get("id", ""),
                "exclusion names no open item from %s and no 'missing rule: ...'" % OPEN_ITEMS)

    # per-case checks
    resolver = None
    for c in ordinary:
        cid = c.get("id", "")
        rows = c.get("rows") or []
        exp = c.get("expected") if isinstance(c.get("expected"), dict) else {}
        if not rows:
            bad("case_empty_rows", cid, "case has no rows")
        exp_values, exp_derivs, _ = compute_expected.outcome_parts(exp)
        if not exp_values and not exp_derivs:
            bad("case_no_expected_values", cid, "case has no expected values and no derivations")
        for name, out in [("expected", exp)] + [("allowed_alternatives[%d]" % i, a)
                                                for i, a in enumerate(c.get("allowed_alternatives") or [])]:
            for key, why in sorted(compute_expected.conflicts(out, compute_expected._rows(c)).items()):
                bad("expected_field_conflict", "%s.%s.%s" % (cid, name, key),
                    "%r is declared both as a value and as a derivation: %s; neither is picked" % (key, why))
        # (v) labels in outcomes.json
        for lab in _outcome_labels(c) + ([c["final_label"]] if c.get("final_label") else []):
            if lab not in all_labels:
                bad("label_not_in_outcomes", cid, "label %r is not in %s" % (lab, OUTCOMES))
        # (l) alternatives carry their own expected values
        for i, alt in enumerate(c.get("allowed_alternatives") or []):
            if not isinstance(alt, dict) or not any(compute_expected.outcome_parts(alt)[:2]):
                bad("alternative_without_values", "%s.allowed_alternatives[%d]" % (cid, i),
                    "allowed alternative outcome has no expected values of its own")
        # (j) handover interface record
        if c.get("layer") == "handover":
            iface = c.get("interface") if isinstance(c.get("interface"), dict) else {}
            for side in ("input", "output"):
                if iface.get(side) in (None, "", {}, []):
                    bad("handover_no_interface_record", cid, "handover case records no %s at its interface" % side)
        # (m) one gate per case
        touched = set()
        iface = c.get("interface") if isinstance(c.get("interface"), dict) else {}
        if iface.get("boundary_id") in gates:
            touched.add(iface["boundary_id"])
        for lab in _outcome_labels(c) + ([c["final_label"]] if c.get("final_label") else []):
            if len(label_gates.get(lab, ())) == 1:
                touched |= label_gates[lab]
        if len(touched) > 1:
            bad("case_pooled_across_gates", cid, "case spans gates %s" % ", ".join(sorted(touched)))
        # rows: (o) decided values, (p) labels, (q) observation ids
        decided = set(exp_values) | set(exp_derivs)
        for alt in c.get("allowed_alternatives") or []:
            if isinstance(alt, dict):
                decided |= set(alt.get("values") or {}) | set(alt.get("derivations") or {})
        row_cols = {}
        for row in rows:
            rid = row.get("id", "") if isinstance(row, dict) else ""
            values = (row.get("values") or {}) if isinstance(row, dict) else {}
            row_cols[rid] = set(values)
            for col, lv in values.items():
                where = "%s.%s.%s" % (cid, rid, col)
                if col in decided:
                    bad("row_holds_decided_value", where, "row holds %r, which the case's decision sets" % col)
                label = lv.get("label") if isinstance(lv, dict) else None
                if label not in ("observed", "synthetic"):
                    bad("value_unlabelled", where, "value carries no observed or synthetic label")
                elif label == "observed":
                    ref = lv.get("source_ref") or ""
                    if resolver is None:
                        resolver = evidence_ids.Resolver(run_dir)
                    ok, why = resolver.resolve(ref)
                    if not ok:
                        bad("observation_unresolved", where,
                            "observation id %r does not resolve: %s" % (ref, why))
        # (r), (s) derivations
        outs = [("expected", exp)] + [("allowed_alternatives[%d]" % i, a)
                                      for i, a in enumerate(c.get("allowed_alternatives") or [])
                                      if isinstance(a, dict)]
        for name, out in outs:
            values, derivs, dirs = compute_expected.outcome_parts(out)
            for key, d in derivs.items():
                if compute_expected.is_named(d) and d.get("derive") not in compute_expected.NAMED:
                    bad("derivation_unknown_name", "%s.%s.%s" % (cid, name, key),
                        "named derivation %r is not one of %s" % (d.get("derive"),
                                                                 ", ".join(sorted(compute_expected.NAMED))))
            # Numeric text is an amount only with an explicit numeric declaration.
            amount_keys = set(derivs) | {k for k, v in values.items()
                if compute_expected._is_amount(v) or
                (k in dirs or k in directions_declared) and compute_expected._decimal(v) is not None}
            for key, direction in dirs.items():
                if direction not in DIRECTIONS:
                    bad("amount_direction_invalid", "%s.%s.%s" % (cid, name, key),
                        "amount_direction %r is not one of %s" % (direction, ", ".join(DIRECTIONS)))
                elif key not in amount_keys:
                    bad("amount_direction_without_amount", "%s.%s.%s" % (cid, name, key),
                        "amount_direction given for %r, which is not an expected amount" % key)
            for key in sorted(amount_keys):
                if key in directions_declared and key not in dirs:
                    bad("amount_direction_missing", "%s.%s.%s" % (cid, name, key),
                        "token %r declares amount_direction %r in %s; the expected amount carries none"
                        % (key, directions_declared[key], RULES))
            for key, d in derivs.items():
                missing_row = False
                unlabelled_input = False
                for tok in _reads(d):
                    rid, _, col = str(tok).partition(".")
                    if rid not in row_cols or col not in row_cols[rid]:
                        missing_row = True
                        bad("derivation_cites_missing_row", "%s.%s.%s" % (cid, name, key),
                            "derivation reads %r, which is not in the case's rows" % tok)
                    elif isinstance(compute_expected._rows(c)[rid][col], dict):
                        unlabelled_input |= compute_expected._rows(c)[rid][col].get("label") not in ("observed", "synthetic")
                try:
                    compute_expected._eval(d, compute_expected._rows(c))
                except compute_expected.Unusable as exc:
                    # Keep the specific existing findings; otherwise report the producer's refusal.
                    unknown = compute_expected.is_named(d) and d.get("derive") not in compute_expected.NAMED
                    if not missing_row and not unknown and not unlabelled_input:
                        bad("derivation_invalid", "%s.%s.%s" % (cid, name, key), str(exc))

    # decision coverage: (a), (e), (f), (g), (n)
    # an escalation condition hands the item to a person: an "escalat" label, or a gate label on a person boundary
    to_person = set()
    for b in boundaries:
        if isinstance(b, dict) and (b.get("to") or {}).get("side") == "person":
            to_person.update(gates.get(b.get("id"), []))
    for did, labels in decisions.items():
        mine = [c for c in ordinary if c.get("decision") == did]
        excl = [c for c in exclusions if c.get("decision") == did]
        if not mine and not excl:
            bad("decision_without_case", did, "decision has no case and no stamped-open exclusion")
            continue
        uncovered = []
        for lab in labels:
            if "escalat" not in lab.lower() and lab not in to_person:
                continue
            if any(c.get("kind") == "must_escalate" and lab in _outcome_labels(c) for c in mine):
                continue
            if any(c.get("kind") in ("must_escalate", None) for c in excl):
                continue
            uncovered.append(lab)
        for kind in KINDS:
            if any(c.get("kind") == kind for c in mine):
                continue
            if any(c.get("kind") in (kind, None) for c in excl):
                continue
            detail = "decision has no %s case and no exclusion" % kind
            if kind == "must_escalate" and uncovered:
                # (n) is folded into (f) here, as (e)-(g) are not repeated under (a)
                detail += "; escalation conditions left uncovered: %s" % "; ".join(uncovered)
                uncovered = []
            bad("decision_missing_%s" % kind, did, detail)
        for lab in uncovered:
            bad("escalation_without_case", "%s: %s" % (did, lab),
                "escalation condition has no must_escalate case and no exclusion")

    # layers: (h), (i); exclusions count as declared, incomplete coverage of their layer
    omitted = {o.get("layer") for o in suite.get("omitted_layers") or [] if isinstance(o, dict)}
    present = {c.get("layer") for c in ordinary}
    excluded_layers = {}
    for c in exclusions:
        if c.get("layer") not in LAYERS:
            continue
        lacking = [k for k in ("reason", "evidence_state") if not c.get(k)]
        if lacking:
            notes.append({"note": "exclusion_not_counted_for_coverage", "where": c.get("id", ""),
                          "detail": "exclusion has no %s, so it does not count toward the %s layer's coverage"
                          % (" and no ".join(lacking), c["layer"])})
            continue
        excluded_layers.setdefault(c["layer"], []).append(c["reason"])
    # a layer applies when the design has a rule or gate in it: rules.json's rules when it lists them, else the
    # decisions in outcomes.json
    listed_rules = rules_doc.get("rules") if isinstance(rules_doc, dict) else None
    has_rules = bool(listed_rules) if isinstance(listed_rules, list) else bool(decisions)
    applicable = {"part": has_rules, "end_to_end": has_rules, "authority": has_rules,
                  "handover": bool(gates) or len(parts) >= 2}
    coverage, coverage_reasons = {}, {}
    for layer in LAYERS:
        if layer in present:
            coverage[layer] = "complete"
        elif layer in excluded_layers:
            coverage[layer] = "incomplete"
            coverage_reasons[layer] = "covered only by exclusions: %s" % "; ".join(
                sorted(set(excluded_layers[layer])))
        elif not applicable[layer]:
            coverage[layer] = "omitted" if layer in omitted else "not_applicable"
            coverage_reasons[layer] = "the design has no rule or gate in this layer"
        else:
            coverage[layer] = "missing"
            if layer == "handover" and layer in omitted and len(parts) >= 2:
                continue  # handover_omission_with_parts below names this one
            bad("layer_missing", layer, "layer applies to this design and has no case and no exclusion%s"
                % (" (declaring it omitted does not cover it)" if layer in omitted else ""))
    if "handover" in omitted and len(parts) >= 2:
        bad("handover_omission_with_parts", "omitted_layers",
            "handover layer declared omitted but the architecture has %d parts" % len(parts))

    # (k) one case per outcome label on every hand-off that reaches a person
    covered = set()
    for c in ordinary:
        covered.update(_outcome_labels(c))
        if c.get("final_label"):
            covered.add(c["final_label"])
    for b in boundaries:
        if not isinstance(b, dict) or (b.get("to") or {}).get("side") != "person":
            continue
        for lab in gates.get(b.get("id"), []):
            if lab not in covered:
                bad("person_handoff_label_without_case", "%s: %s" % (b.get("id"), lab),
                    "hand-off to a person has outcome label with no case")

    # person-decided gates: a gate whose boundary comes from a person. Each case for one declares the person's
    # decision as synthetic data, and each of the gate's labels has a case.
    person_gates = set()
    for b in boundaries:
        if isinstance(b, dict) and (b.get("from") or {}).get("side") == "person" and b.get("id") in gates:
            person_gates.add(b.get("id"))
    decided_labels = {}
    reading = person_gate_reading.Reading(run_dir, rules_doc=rules_doc, outcomes=outcomes)
    for c in ordinary:
        cid = c.get("id", "")
        pd = c.get("person_decision")
        dec = c.get("decision")
        if isinstance(dec, str) and dec.startswith("gate:"):
            dec = dec[len("gate:"):]
        gid = dec if isinstance(dec, str) and dec in gates else None
        if pd is None:
            if gid in person_gates:
                if reading.needs_decision(c, gid):
                    # Damage prevented: the case would assert what the agent does after a decision no one made.
                    bad("person_decision_missing", cid,
                        "case is for gate %s, which a person decides; its expected result depends on a rule "
                        "downstream of the gate, but it states no person decision" % gid)
                else:
                    notes.append({"note": "case_tests_handover", "where": cid,
                                  "detail": "no person decision is stated, so the case is read as testing the "
                                            "hand-over at gate %s" % gid})
            continue
        if not isinstance(pd, dict) or not isinstance(pd.get("gate"), str) or not isinstance(pd.get("decision"), str):
            bad("person_decision_malformed", cid, "person_decision needs a gate and a decision")
            continue
        pg = pd["gate"][len("gate:"):] if pd["gate"].startswith("gate:") else pd["gate"]
        if pg not in gates:
            bad("person_decision_unknown_gate", cid, "person_decision names gate %r, not in %s" % (pg, OUTCOMES))
        elif gid is not None and pg != gid:
            bad("person_decision_missing", cid,
                "case is for gate %s and states a decision for gate %s; no decision for its own gate" % (gid, pg))
        elif pd["decision"] not in gates[pg]:
            bad("person_decision_not_an_outcome", cid,
                "person decision %r is not an outcome of gate %s" % (pd["decision"], pg))
        else:
            decided_labels.setdefault(pg, set()).add(pd["decision"])
    open_gates = {c.get("decision") for c in exclusions}
    for gid in sorted(person_gates):
        if gid in open_gates:
            continue  # a stamped-open exclusion for the gate declares its labels open
        for lab in gates[gid]:
            if lab not in decided_labels.get(gid, set()) and lab not in covered:
                bad("person_decided_label_without_case", "%s: %s" % (gid, lab),
                    "gate a person decides has outcome label with no case")

    counts = {"cases": len(ordinary), "exclusions": len(exclusions), "decisions": len(decisions),
              "problems": len(problems)}
    extra = {"coverage": coverage, "coverage_reasons": coverage_reasons, "notes": notes,
             "suite_status": "incomplete" if "incomplete" in coverage.values() else "complete"}
    return problems, counts, extra


def check(run_dir):
    try:
        problems, counts, extra = _check(run_dir)
    except (Unreadable, AttributeError, KeyError, TypeError) as e:
        return _report([], {}, error=str(e))
    return _report(problems, counts, extra=extra)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="check_cases.py")
    ap.add_argument("--run", required=True)
    ap.add_argument("--out")
    try:
        args = ap.parse_args(argv)
    except SystemExit:
        return 2
    if not os.path.isdir(args.run):
        report = _report([], {}, error="run folder not found")
    else:
        report = check(args.run)
    out = args.out
    if not out and os.path.isdir(args.run):
        out = os.path.join(args.run, "checks", "%s.json" % CHECK)
    if out:
        try:
            d = os.path.dirname(out)
            if d:
                os.makedirs(d, exist_ok=True)
            with open(out, "w", encoding="utf-8") as f:
                json.dump(report, f, indent=2)
                f.write("\n")
        except OSError:
            pass
    for p in report["problems"]:
        print("%s: %s: %s" % (p["defect"], p["where"], p["detail"]))
    for layer, reason in sorted((report.get("coverage_reasons") or {}).items()):
        print("coverage: %s: %s: %s" % (layer, report["coverage"][layer], reason))
    print("%s: %s" % (CHECK, report["result"]))
    return report["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
