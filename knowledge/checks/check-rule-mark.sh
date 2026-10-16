#!/usr/bin/env python3
# This file is Python despite its .sh name: run it directly or as `python3 <this file>`; `bash <this file>` cannot run it.
"""Rule-mark check -- every decision rule says whether code or judgment decides it, and why.

Plain-language handle: "check every rule is marked code or judgment, with a reason, and that no model is ever
asked to do numbers". It is one of stage 9's checks (Design each part's decision rules). It validates STRUCTURE
and the numeric-predicate lexicon only; whether a mark is RIGHT is the independent review's job (criterion
rule_mark) and the person's at sign-off.

Usage: check-rule-mark.sh [--help] <decision-policy.yaml> <internal-map.graph.json>
  1st argument: the decision policy. 2nd argument: the INTERNAL MAP GRAPH JSON (the built map of the agent's
  inner team, `*.graph.json`), which the contradiction test reads for each anchor step's `runs_on`. Both are
  required; `--help` prints this text.

Fails (exit 1), naming the rule, on:
  * a rule row without `runs_as`;
  * `runs_as` outside {code, judgment} without a `runs_as_note` of at least 20 non-space characters;
  * `runs_as_basis` missing or under 20 non-space characters;
  * a judgment rule without a `computed_inputs` list;
  * a contradiction: a judgment rule every one of whose owning rule set's anchor steps is a graph node with
    `runs_on: code`, unless `runs_as_basis` states why (it says the model step is outside the anchored steps
    because ...; any basis that names the anchored steps and gives a "because" reason passes);
  * a model would be asked to do numbers: a judgment rule with neither `judgment_split` nor a
    `numeric_free_basis` of at least 20 non-space characters; a `judgment_split` whose identify question or
    judge text matches the numeric-predicate lexicon (checks/_prebuild_sim.py numeric_predicate_hits); a
    `compute` entry not in `computed_inputs`; a missing or empty `combine`; an identify step without a question,
    an output id, an output kind in {item-ids, text-spans, category} or a reads list; a `numeric_free_basis` on a
    condition the lexicon flags that does not name each flagged word (a stated reason passes, for example "covers"
    used of a topic, not an amount).
Never fails a rule for being unfamiliar: any other `runs_as` value with its note passes and is counted as other
(stage 11 runs it as code, judgment or not-run with its reason).

Notes (stdout, never a failure): a pure script (no graph node with `runs_on` other than code) whose rules are all
code prints `NOTE all rules run as code; judgment steps: none`; each other-marked rule is listed.
On pass: `PASS rule-mark: <n> code, <m> judgment (<s> split), <k> other`.

PyYAML is required. Exit: 0 pass; 1 a failure (each listed); 2 setup error.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _prebuild_sim as pbs  # noqa: E402

KINDS = ("item-ids", "text-spans", "category")
MIN = 20


def die(msg, code):
    sys.stderr.write(msg + "\n")
    sys.exit(code)


def load(path, kind):
    if not os.path.isfile(path):
        die("missing file: %s" % path, 2)
    try:
        if kind == "json":
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        import yaml
        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f)
    except ImportError:
        die("PyYAML is required but not importable (pip install pyyaml).", 2)
    except ValueError as exc:
        die("cannot parse %s: %s" % (path, exc), 2)


def anchored_on_code_only(rule_set, nodes):
    anchors = rule_set.get("anchor_steps") or []
    found = [nodes[a] for a in anchors if a in nodes]
    return bool(found) and len(found) == len(anchors) and all(n.get("runs_on") == "code" for n in found)


def basis_states_anchor_reason(basis):
    low = (basis or "").lower()
    return "anchor" in low and "because" in low


def check_split(rid, row, split, computed, problems):
    where = "rule %s: judgment_split" % rid
    if not isinstance(split, dict):
        problems.append("%s must be a mapping with identify, compute, judge and combine" % where)
        return
    ident = split.get("identify")
    if not isinstance(ident, dict):
        problems.append("%s.identify is missing (step 1 picks things out, by model)" % where)
        ident = {}
    q = ident.get("question")
    if not isinstance(q, str) or not q.strip():
        problems.append("%s.identify.question is missing" % where)
    else:
        hits = pbs.numeric_predicate_hits(q)
        if hits:
            problems.append("%s.identify.question asks the model for a number or a numeric comparison (it says: %s); "
                            "state only what to pick out, and compute the numbers in code" % (where, ", ".join(hits)))
    if not isinstance(ident.get("output"), str) or not ident.get("output", "").strip():
        problems.append("%s.identify.output (the identification id) is missing" % where)
    if ident.get("output_kind") not in KINDS:
        problems.append("%s.identify.output_kind is %r; it must be one of %s"
                        % (where, ident.get("output_kind"), ", ".join(KINDS)))
    reads = ident.get("reads")
    if not isinstance(reads, list) or not reads:
        problems.append("%s.identify.reads must list the tokens the identification reads" % where)
    compute = split.get("compute")
    if not isinstance(compute, list):
        problems.append("%s.compute must be a list of computed_inputs ids" % where)
        compute = []
    for cid in compute:
        if cid not in computed:
            problems.append("%s.compute entry %r is not in the rule's computed_inputs" % (where, cid))
    judge = split.get("judge")
    if judge is not None:
        if not isinstance(judge, str) or not judge.strip():
            problems.append("%s.judge must be the non-numeric question, or null when nothing is left" % where)
        else:
            hits = pbs.numeric_predicate_hits(judge)
            if hits:
                problems.append("%s.judge asks the model for a number or a numeric comparison (it says: %s); give it "
                                "the computed results as facts and ask only what is left" % (where, ", ".join(hits)))
    combine = split.get("combine")
    if not isinstance(combine, str) or not combine.strip():
        problems.append("%s.combine is missing or empty (one sentence: how code turns the step outputs into holds)"
                        % where)


def main(argv):
    if any(a in ("-h", "--help") for a in argv):
        print(__doc__)
        return 0
    args = [a for a in argv if not a.startswith("--")]
    if len(args) != 2:
        die("usage: check-rule-mark.sh <decision-policy.yaml> <internal-map.graph.json>", 2)
    policy = load(args[0], "yaml")
    graph = load(args[1], "json")
    if not isinstance(policy, dict) or not isinstance(graph, dict):
        die("the policy and the internal map must each be a mapping", 2)
    nodes = {n.get("id"): n for n in graph.get("nodes") or [] if isinstance(n, dict)}
    pure_script = not any(n.get("runs_on") not in (None, "code") for n in nodes.values())
    problems, notes = [], []
    n_code = n_judge = n_split = n_other = 0
    for rs in policy.get("rule_sets") or []:
        for row in rs.get("rules") or []:
            rid = row.get("rule_id", "?")
            mark = row.get("runs_as")
            basis = row.get("runs_as_basis")
            if mark is None:
                problems.append("rule %s has no runs_as (mark it code or judgment, with a runs_as_basis)" % rid)
                continue
            if pbs.nonspace_len(basis) < MIN:
                problems.append("rule %s: runs_as_basis is missing or under %d non-space characters; state why this "
                                "mark, tied to the condition's wording" % (rid, MIN))
            if mark == "code":
                n_code += 1
                continue
            if mark != "judgment":
                n_other += 1
                if pbs.nonspace_len(row.get("runs_as_note")) < MIN:
                    problems.append("rule %s is marked %r, which is neither code nor judgment, and needs a runs_as_note "
                                    "of at least %d non-space characters saying who or what decides it"
                                    % (rid, mark, MIN))
                else:
                    notes.append("NOTE rule %s is marked %r (%s); stage 11 runs it as code, judgment or not-run with "
                                 "its reason" % (rid, mark, row.get("runs_as_note").strip()))
                continue
            n_judge += 1
            computed = row.get("computed_inputs")
            if not isinstance(computed, list):
                problems.append("rule %s is judgment and needs a computed_inputs list (possibly []) naming every "
                                "number it needs, computed in code" % rid)
                computed = []
            if anchored_on_code_only(rs, nodes) and not basis_states_anchor_reason(basis):
                problems.append("rule %s is judgment but every anchor step of its rule set (%s) runs on code; say in "
                                "runs_as_basis why (\"the model step is outside the anchored steps because ...\")"
                                % (rid, ", ".join(rs.get("anchor_steps") or [])))
            split = row.get("judgment_split")
            nfb = row.get("numeric_free_basis")
            if split is not None:
                n_split += 1
                check_split(rid, row, split, computed, problems)
                continue
            if pbs.nonspace_len(nfb) < MIN:
                problems.append("rule %s is judgment with neither judgment_split nor a numeric_free_basis of at least "
                                "%d non-space characters; a model would be left to decide any number in its condition"
                                % (rid, MIN))
                continue
            hits = pbs.numeric_predicate_hits(row.get("condition"))
            missing = pbs.missing_from_basis(nfb, hits)
            if missing:
                problems.append("rule %s: its condition says %s, which may be a numeric test; numeric_free_basis must "
                                "name each of those words and why it is not one, or the rule needs a judgment_split"
                                % (rid, ", ".join(missing)))
    if problems:
        die("rule-mark check FAILED for %s:\n  - %s" % (args[0], "\n  - ".join(problems)), 1)
    for note in notes:
        print(note)
    if pure_script and n_judge == 0 and n_other == 0:
        print("NOTE all rules run as code; judgment steps: none")
    print("PASS rule-mark: %d code, %d judgment (%d split), %d other" % (n_code, n_judge, n_split, n_other))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
