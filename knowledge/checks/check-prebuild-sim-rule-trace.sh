#!/usr/bin/env python3
# This file is Python despite its .sh name: run it directly or as `python3 <this file>`; `bash <this file>` cannot run it.
"""Pre-build simulation rule-trace check (stage 11) -- each coded rule is bound to the written rule it runs.

Plain-language handle: "check the generated rules code has not drifted from the written rules, and no judgment
prompt asks a model to do numbers". One of stage 11's four checks (plan 5.1). It loads the generated rules-code
modules the manifest pins, re-derives every canonical row and split sha with checks/_prebuild_sim.py (the same
function scripts/prebuild-sim-rows.py prints), and scans each function's syntax tree. Semantic drift the tree
cannot see is caught by the independent review (criterion code_fidelity) and by the comparison with the Test
Suite. Run with the run root as the working directory.

Fails (exit 1), each finding naming the rule and the field, on:
  1. a code rule (or an other-marked rule handled as code) without exactly one RULES entry; a RULES entry for an
     unknown rule, a judgment rule, or an other-marked rule handled as judgment or not-run (an other-marked rule
     with no other-marks.yaml entry runs not-run and is skipped here);
  2. a RULES entry whose rule_row_sha256 is not the current row's sha (the rule changed; regenerate its code);
  3. in a rule function: a t(...) literal outside the rule's operative_tokens and the reads of its computes; an
     operative_tokens entry read by neither; a float( call anywhere in a module; an import outside decimal, re,
     datetime, math, fractions; a computed function reading a token outside its declared reads;
  4. a c(...) literal that is no declared computed id, or a p(...) literal that is no open id in parameters.yaml;
  5. a split rule (judgment_split on the row, or in its other-marks.yaml entry when handled as judgment) without
     exactly one SPLITS entry; an entry whose rule_row_sha256, split_sha256, combine_text (character for
     character), identify_output, computes or judge flag differs from the written split; a COMPUTED entry a rule
     lists that does not carry that rule at its current row sha in bound_rows, or a bound_rows entry naming a
     rule that does not list it; a j(...) literal naming another identification; a combine function with an
     arithmetic operator, a numeric literal, or a comparison other than ==, !=, is, is not against True, False,
     None or "needs_more_information"; a computed id in a split's compute with no COMPUTED entry; a combine
     function calling c() with an id that is not a string literal in its rule's effective split compute list (the
     row's judgment_split, or the other-marks.yaml entry's for an other-marked rule handled as judgment), since a
     computed value combine reaches on its own is neither recorded by the compute step nor handed to the next unit;
  6. a recorded prompt of the final run that fails the prompt lint; a prompt the run SENT that is not the file its
     results row pins (judgment.prompt: edited, or deleted, alone or with the whole judgments folder).

Every file it reads through the manifest (the policy, other-marks.yaml, parameters.yaml, every rules-code module,
the final run's results) is read through its pin (checks/_prebuild_sim.py read_pinned): one that is not the pinned
file is refused (exit 2) naming it, and a rules-code module runs only as its pinned bytes. Every prompt the final run
sent is reached through the pin its results row records (pbs.prompt_pins) and linted only as those bytes; the final
run's judgments folder is walked only for the prompts that were never sent (a --no-model run, or a prompt the lint
refused before sending), which have no pin: such a prompt can only add a lint finding.

Usage: check-prebuild-sim-rule-trace.sh MANIFEST
Exit: 0 pass; 1 findings; 2 setup error (a module that cannot load is a setup error, named).
"""
import ast
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _prebuild_sim as pbs  # noqa: E402

ALLOWED_CMP = (True, False, None, "needs_more_information")


def fn_node(tree, name):
    return next((n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == name), None)


def literal_calls(node, names):
    """{accessor: [(literal or None, lineno)]} for calls to the named accessors inside NODE."""
    out = {n: [] for n in names}
    for call in ast.walk(node):
        if isinstance(call, ast.Call) and isinstance(call.func, ast.Name) and call.func.id in names:
            arg = call.args[0] if call.args else None
            lit = arg.value if isinstance(arg, ast.Constant) and isinstance(arg.value, str) else None
            out[call.func.id].append((lit, call.lineno))
    return out


def combine_problems(node):
    out = []
    for n in ast.walk(node):
        if isinstance(n, ast.BinOp) and isinstance(n.op, (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Mod,
                                                          ast.FloorDiv, ast.Pow)):
            out.append("an arithmetic operator (line %d)" % n.lineno)
        elif isinstance(n, ast.AugAssign):
            out.append("an arithmetic assignment (line %d)" % n.lineno)
        elif isinstance(n, ast.Constant) and isinstance(n.value, (int, float)) and not isinstance(n.value, bool):
            out.append("a numeric literal %r (line %d)" % (n.value, n.lineno))
        elif isinstance(n, ast.Compare):
            for op, right in zip(n.ops, n.comparators):
                ok_op = isinstance(op, (ast.Eq, ast.NotEq, ast.Is, ast.IsNot))
                ok_right = isinstance(right, ast.Constant) and right.value in ALLOWED_CMP \
                    and not (isinstance(right.value, int) and not isinstance(right.value, bool))
                if not (ok_op and ok_right):
                    out.append("a comparison between values other than True, False, None or "
                               "\"needs_more_information\" (line %d)" % n.lineno)
    return out


def check(root, mrel):
    m = pbs.load_manifest(root, mrel)
    rules_pin = m["rules"]
    # every pinned file through its pin (pbs.read_pinned): one that is not the pinned file is refused, naming it
    policy = pbs.read_pinned(root, m["inputs"]["decision_policy"], "yaml", "the pinned decision policy") or {}
    other = pbs.read_pinned(root, rules_pin["other_marks"], "yaml", "the pinned other-marks.yaml") \
        if rules_pin.get("other_marks") else None
    params = pbs.read_pinned(root, rules_pin["parameters"], "yaml", "the pinned parameters.yaml") or {}
    code_pins = list(rules_pin.get("code_modules") or [])
    paths = [c["path"] for c in code_pins]
    sources = {c["path"]: pbs.read_pinned(root, c, "text", "rules code") for c in code_pins}
    rules, computed, splits, mods = pbs.load_rules_modules(root, code_pins)
    idx = pbs.other_marks_index(other)
    shas = pbs.row_shas(policy, other)
    rows = pbs.rows_by_id(policy)
    open_ids = {p.get("open_id") for p in params.get("parameters") or [] if isinstance(p, dict)}
    trees = {}
    p = []
    for rel in paths:
        tree = ast.parse(sources[rel])
        trees[rel] = tree
        for n in ast.walk(tree):
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "float":
                p.append("%s calls float( on line %d; arithmetic uses Decimal from the token's string form" % (rel,
                                                                                                             n.lineno))
            if isinstance(n, (ast.Import, ast.ImportFrom)):
                names = [a.name for a in n.names] if isinstance(n, ast.Import) else [n.module or ""]
                for name in names:
                    if name.split(".")[0] not in pbs.STDLIB_IMPORTS:
                        p.append("%s imports %s on line %d; rules code imports only %s" % (
                            rel, name, n.lineno, ", ".join(sorted(pbs.STDLIB_IMPORTS))))

    def node_of(entry, key):
        fn = entry.get(key)
        return fn_node(trees.get(entry["_module"]), getattr(fn, "__name__", "")) if fn else None

    def lists(rid):
        row = rows.get(rid) or {}
        entry = idx.get(rid) if pbs.is_other_mark(row) else None
        split = pbs.effective_split(row, entry) or {}
        return set(row.get("computed_inputs") or []) | set((rules.get(rid) or {}).get("computes") or []) \
            | set(split.get("compute") or [])

    # computed functions read only their declared tokens
    for cid, entry in computed.items():
        node = node_of(entry, "fn")
        if node is None:
            p.append("COMPUTED %s has no function in %s" % (cid, entry["_module"]))
            continue
        calls = literal_calls(node, ("t", "c", "p", "j"))
        for lit, line in calls["t"]:
            if lit is None:
                p.append("COMPUTED %s reads a token by a non-literal name (line %d)" % (cid, line))
            elif lit not in (entry.get("reads") or []):
                p.append("COMPUTED %s reads %s, which is not in its declared reads" % (cid, lit))
        for lit, line in calls["c"]:
            if lit not in computed:
                p.append("COMPUTED %s reads c(%r), which is no declared computed id" % (cid, lit))
        for lit, line in calls["p"]:
            if lit not in open_ids:
                p.append("COMPUTED %s reads p(%r), which is no open id in parameters.yaml" % (cid, lit))
        for lit, line in calls["j"]:
            if lit not in (entry.get("identified") or []):
                p.append("COMPUTED %s reads j(%r), which it does not declare in identified" % (cid, lit))
        bound = entry.get("bound_rows") or {}
        for rid, sha in bound.items():
            if rid not in rows or cid not in lists(rid):
                p.append("COMPUTED %s's bound_rows names %s, which does not list it" % (cid, rid))
            elif sha != shas[rid]["rule_row_sha256"]:
                p.append("COMPUTED %s is bound to an earlier row of %s (rule_row_sha256); regenerate it" % (cid, rid))
    # 1-4: code rules
    for _rs, row in pbs.policy_rows(policy):
        rid = row["rule_id"]
        entry_om = idx.get(rid) if pbs.is_other_mark(row) else None
        handling = pbs.effective_handling(row, entry_om)
        if pbs.is_other_mark(row) and entry_om is None:
            continue
        if handling == "code":
            e = rules.get(rid)
            if e is None:
                p.append("code rule %s has no RULES entry" % rid)
                continue
            if e.get("rule_row_sha256") != shas[rid]["rule_row_sha256"]:
                p.append("the code for %s was generated from an earlier row (rule_row_sha256); regenerate it" % rid)
            node = node_of(e, "fn")
            if node is None:
                p.append("RULES %s has no function in %s" % (rid, e["_module"]))
                continue
            calls = literal_calls(node, ("t", "c", "p", "j"))
            comp_reads = set()
            for cid in e.get("computes") or []:
                comp_reads.update((computed.get(cid) or {}).get("reads") or [])
            allowed = set(row.get("operative_tokens") or []) | comp_reads
            read = set()
            for lit, line in calls["t"]:
                if lit is None:
                    p.append("the code for %s reads a token by a non-literal name (line %d)" % (rid, line))
                elif lit not in allowed:
                    p.append("the code for %s reads %s, which is not in its operative_tokens (the coded rule reads a "
                             "different signal)" % (rid, lit))
                else:
                    read.add(lit)
            for tok in row.get("operative_tokens") or []:
                if tok not in read and tok not in comp_reads:
                    p.append("the code for %s never reads its operative token %s" % (rid, tok))
            for lit, line in calls["c"]:
                if lit not in computed:
                    p.append("the code for %s reads c(%r), which is no declared computed id" % (rid, lit))
            for lit, line in calls["p"]:
                if lit not in open_ids:
                    p.append("the code for %s reads p(%r), which is no open id in parameters.yaml" % (rid, lit))
            for cid in e.get("computes") or []:
                ce = computed.get(cid)
                if ce is None:
                    p.append("the code for %s computes %s, which has no COMPUTED entry" % (rid, cid))
                elif (ce.get("bound_rows") or {}).get(rid) != shas[rid]["rule_row_sha256"]:
                    p.append("COMPUTED %s does not carry %s at its current row sha in bound_rows" % (cid, rid))
        elif rid in rules:
            p.append("RULES carries %s, which runs as %s, not code" % (rid, handling))
        split = pbs.effective_split(row, entry_om)
        if handling == "judgment" and split:
            s = splits.get(rid)
            if s is None:
                p.append("split rule %s has no SPLITS entry" % rid)
                continue
            ident = split.get("identify") or {}
            for field, want in (("rule_row_sha256", shas[rid]["rule_row_sha256"]),
                                ("split_sha256", shas[rid]["split_sha256"]),
                                ("combine_text", split.get("combine")),
                                ("identify_output", ident.get("output")),
                                ("computes", list(split.get("compute") or [])),
                                ("judge", split.get("judge") is not None)):
                if s.get(field) != want:
                    p.append("the split for %s was generated from an earlier instruction (%s differs); regenerate "
                             "its code" % (rid, field))
            for cid in split.get("compute") or []:
                ce = computed.get(cid)
                if ce is None:
                    p.append("split %s computes %s, which has no COMPUTED entry" % (rid, cid))
                elif (ce.get("bound_rows") or {}).get(rid) != shas[rid]["rule_row_sha256"]:
                    p.append("COMPUTED %s does not carry %s at its current row sha in bound_rows" % (cid, rid))
                else:
                    for lit, _line in literal_calls(node_of(ce, "fn"), ("j",))["j"]:
                        if lit != ident.get("output"):
                            p.append("COMPUTED %s (for %s) reads j(%r), not the rule's identification %r"
                                     % (cid, rid, lit, ident.get("output")))
            node = node_of(s, "combine")
            if node is None:
                p.append("SPLITS %s has no combine function" % rid)
            else:
                for lit, _line in literal_calls(node, ("j",))["j"]:
                    if lit != ident.get("output"):
                        p.append("the combine for %s reads j(%r), which is another rule's identification" % (rid, lit))
                compute = list(split.get("compute") or [])
                for lit, line in literal_calls(node, ("c",))["c"]:
                    if lit is None:
                        p.append("the combine for %s calls c() with an id that is not written out as text (line %d); "
                                 "call c() only with a computed id from the split's compute list, written as a "
                                 "string" % (rid, line))
                    elif lit not in compute:
                        p.append("the combine for %s calls c(%r), which is not in its split's compute list; add %s "
                                 "to the split's compute, or stop calling it" % (rid, lit, lit))
                for prob in combine_problems(node):
                    p.append("the combine for %s contains %s; combine only reads computed true/false values" % (rid,
                                                                                                             prob))
        elif rid in splits:
            p.append("SPLITS carries %s, which is not a split judgment rule" % rid)
    for rid in rules:
        if rid not in rows:
            p.append("RULES names %s, which the policy does not carry" % rid)
    for rid in splits:
        if rid not in rows:
            p.append("SPLITS names %s, which the policy does not carry" % rid)
    # 6. the prompt lint over every recorded prompt of the final run
    fr = pbs.final_run(m)
    if fr:
        def lint(rel, text, rid, variant, how):
            row = rows.get(rid) or {}
            entry_om = idx.get(rid) if pbs.is_other_mark(row) else None
            for prob in pbs.lint_prompt(text, variant, split=bool(pbs.effective_split(row, entry_om)),
                                        condition=row.get("condition"),
                                        numeric_free_basis=pbs.effective_numeric_free_basis(row, entry_om)):
                p.append("recorded prompt %s%s fails the prompt lint: %s" % (rel, how, prob))
        # every SENT prompt through the pin its results row records (the results read through their own pin); one
        # that is not the pinned file (edited, deleted, or its judgments folder gone) is a finding naming it
        sent = set()
        for sp in pbs.prompt_pins(root, m):
            sent.add(sp.pin["path"])
            read = pbs.pinned_read(root, sp.pin, "text", "the prompt the run sent")
            if not read.ok:
                p.append("recorded prompt %s is not the prompt the final run sent (%s, case %s rule %s): %s"
                         % (sp.pin["path"], sp.where, sp.case_id, sp.rule_id, read.reason))
                continue
            lint(sp.pin["path"], read.content, sp.rule_id, sp.variant, "")
        # the folder only for the prompts that were never sent (no pin): each can only add a lint finding
        jdir = os.path.join(root, pbs.OUT, "runs", fr["run_id"], "judgments")
        by_slug = {pbs.slug(r): r for r in rows}
        if os.path.isdir(jdir):
            for dirpath, _dirs, files in os.walk(jdir):
                for name in sorted(files):
                    if not name.endswith(".prompt.md"):
                        continue
                    rel = os.path.relpath(os.path.join(dirpath, name), root).replace(os.sep, "/")
                    if rel in sent:
                        continue
                    stem = name[:-len(".prompt.md")]
                    rslug, _, variant = stem.rpartition("-")
                    with open(os.path.join(dirpath, name), encoding="utf-8") as f:
                        text = f.read()
                    lint(rel, text, by_slug.get(rslug), variant, " (never sent, so no results row pins it)")
    return p


def main(argv):
    args = [a for a in argv if not a.startswith("--")]
    if len(args) != 1:
        sys.stderr.write("usage: check-prebuild-sim-rule-trace.sh MANIFEST\n")
        return 2
    try:
        problems = check(os.getcwd(), args[0])
    except (pbs.PrebuildError, KeyError, TypeError, SyntaxError) as exc:
        sys.stderr.write("prebuild-sim-rule-trace: %s\n" % exc)
        return 2
    if problems:
        sys.stderr.write("pre-build simulation rule-trace check FAILED for %s:\n  - %s\n" % (args[0],
                                                                                         "\n  - ".join(problems)))
        return 1
    print("PASS prebuild-sim-rule-trace: every coded rule and split is bound to its current written row; every "
          "recorded prompt passes the lint.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
