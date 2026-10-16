#!/usr/bin/env python3
"""Rule checks for the rules stage (U13), and the dropped failure-handling family report (--families).

Usage:
  check_rules.py --run RUN [--out REPORT]            ordinary rule checks; exit 0 pass, 1 fail, 2 usage or unreadable
  check_rules.py --run RUN --families [--out REPORT] family report only; exit 0 whenever it wrote its report

Reads stages/rules/rules.json, stages/rules/outcomes.json, stages/rules/open-items.json,
stages/intake/intake.json and the upstream files the intake points to, and stages/fields/fields.json when present
(for each token's declared type). The four stage outputs are read only through formats/readers.py.
Standard library only. Expressions are evaluated only through scripts/expr.py.
Each decision's value grid is partitioned by the tokens its rules share and streamed; at most GRID_BUDGET points
are evaluated per decision, and an uncovered remainder is reported as grid-partial, never as a pass.
"""
import argparse
import itertools
import json
import os
import re
import sys
from decimal import Decimal

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "scripts"))
import expr  # noqa: E402
import evidence_ids  # noqa: E402  the one observation-id resolver (U1)
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(HERE)), "fast-mode-build", "driver"))
import knowledge_root  # noqa: E402  the one lookup for the plugin's knowledge folder
sys.path.insert(0, os.path.join(knowledge_root.find(), "checks"))
import _agent_steps  # noqa: E402  the one rule for which steps the agent owns
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "formats"))
import readers  # noqa: E402
sys.path.insert(0, HERE)
from check_grants import split_row  # noqa: E402  one cell rule for every Write grants reader

CHECK_PATH = "skills/agent-design/checks/check_rules.py"
OPEN_KINDS = ("missing_input", "policy_choice")
FAMILY_OF_DIRECTION = {
    "outbound_handoff": "handoff",
    "draft": "draft-write",
    "write": "write",
    "person_write": "write",
    "read": "read",
    "inbound_control": "read",
}


GRID_BUDGET = 200000  # evaluated grid points per decision per invocation, after partitioning

Unreadable = readers.Unreadable


# ---------------------------------------------------------------- shared loading

def load_json(run_dir, rel, required=True):
    """Any other run file (intake, upstream, test suite, simulation); the four stage outputs use readers."""
    return readers.read_json(os.path.join(run_dir, rel), rel, required)


def items_of(data, key):
    """A list file may be a bare list or an object holding the list under `key`."""
    if data is None:
        return []
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and isinstance(data.get(key), list):
        return data[key]
    raise Unreadable("expected a list or an object with %s[]" % key)


def load_rules(run_dir):
    """The normalised rules output (tokens as objects) and its shape findings."""
    return readers.load_rules(run_dir)


def load_open_items(run_dir):
    """The normalised open items (`missing` a list of strings) and their shape findings."""
    return readers.load_open_items(run_dir)


def shape_problems(findings):
    return [{"defect": "shape", "where": f["where"], "detail": f["detail"]} for f in findings]


def stamped(item):
    return (isinstance(item, dict) and item.get("evidence_state") == "needs_more_information"
            and item.get("kind") in OPEN_KINDS)


def is_contradictory(rule):
    return rule.get("evidence_state") == "Contradictory"


def has_expression(rule):
    e = rule.get("expression")
    return isinstance(e, dict) and isinstance(e.get("test"), dict)


def report(check, run_rel, problems, counts, fallback, extra=None):
    failing = [p for p in problems if p["defect"] != "NOTE"]
    rep = {
        "check": check,
        "path": CHECK_PATH if check != "check_outcomes" else "skills/agent-design/checks/check_outcomes.py",
        "run": run_rel,
        "exit_code": 1 if failing else 0,
        "result": "fail" if failing else "pass",
        "problems": failing,
        "counts": counts,
        "agent_fallback": fallback,
        "notes": [p["detail"] for p in problems if p["defect"] == "NOTE"],
    }
    if extra:
        rep.update(extra)
    return rep


def error_report(check, run_rel, detail, fallback):
    return {"check": check, "path": CHECK_PATH if check != "check_outcomes"
            else "skills/agent-design/checks/check_outcomes.py",
            "run": run_rel, "exit_code": 2, "result": "error",
            "problems": [{"defect": "unreadable", "where": "", "detail": detail}],
            "counts": {}, "agent_fallback": fallback, "notes": []}


# ---------------------------------------------------------------- value grid (shared with check_outcomes)

def _literal_values(node, out):
    if not isinstance(node, dict):
        return
    if node.get("kind") == "literal" and "value" in node:
        out.append(node["value"])
    for key in ("left", "right", "arg", "value_of"):
        _literal_values(node.get(key), out)
    for a in node.get("args") or [] if isinstance(node.get("args"), list) else []:
        _literal_values(a, out)


def _token_uses(node, lits, calc_tokens, in_calc=False, bool_tokens=None):
    """Per token: literals compared with it or listed with it, and whether it is read inside a calc.

    A token read directly as a test, or as an operand of not, and or or, is added to bool_tokens.
    """
    if not isinstance(node, dict):
        return
    if bool_tokens is None:
        bool_tokens = set()
    if "test" in node and "kind" not in node:
        _token_uses(node["test"], lits, calc_tokens, False, bool_tokens)
        if isinstance(node["test"], dict) and node["test"].get("kind") == "read":
            bool_tokens.add(node["test"].get("token"))
        for sub in (node.get("sets") or {}).values():
            _token_uses(sub, lits, calc_tokens, False, bool_tokens)
        return
    kind = node.get("kind")
    if kind in ("not", "and", "or"):
        operands = [node.get("arg")] if kind == "not" else (node.get("args") if isinstance(node.get("args"), list) else [])
        for a in operands:
            if isinstance(a, dict) and a.get("kind") == "read":
                bool_tokens.add(a.get("token"))
    if kind in ("read", "is_null", "is_not_applicable"):
        lits.setdefault(node.get("token"), [])
        if in_calc:
            calc_tokens.add(node.get("token"))
        return
    if kind in ("compare", "in"):
        toks = expr.reads(node)
        found = []
        _literal_values(node, found)
        if kind == "in" and isinstance(node.get("list"), list):
            found.extend(node["list"])
        for t in toks:
            lits.setdefault(t, []).extend(found)
    if kind == "calc":
        in_calc = True
    for key in ("left", "right", "arg", "value_of"):
        _token_uses(node.get(key), lits, calc_tokens, in_calc, bool_tokens)
    for a in node.get("args") or [] if isinstance(node.get("args"), list) else []:
        _token_uses(a, lits, calc_tokens, in_calc, bool_tokens)


def _step(value):
    if isinstance(value, int):
        return 1
    exp = Decimal(repr(value)).as_tuple().exponent
    return Decimal(1).scaleb(min(exp, 0))


def _num_values(v):
    if isinstance(v, int):
        return [v - 1, v, v + 1]
    d, s = Decimal(repr(v)), _step(v)
    return [float(d - s), v, float(d + s)]


def token_values(literals, read_in_calc, boolean=False):
    vals = []

    def add(v):
        if not any(type(v) is type(o) and v == o for o in vals):
            vals.append(v)

    nums = [v for v in literals if expr._is_num(v)]
    strs = [v for v in literals if isinstance(v, str)]
    bools = [v for v in literals if isinstance(v, bool)]
    for n in nums:
        for v in _num_values(n):
            add(v)
    for s in strs:
        add(s)
    if strs:
        other = "none-of-the-listed"
        while other in strs:
            other += "-x"
        add(other)
    for b in bools:
        add(b)
        add(not b)
    if not vals and boolean:
        add(True)
        add(False)
    if not vals:
        add(1 if read_in_calc else "sample")
    vals.append(None)
    return vals


NUMBER_TYPES = ("number", "numeric", "integer", "int", "decimal", "float", "amount")


def grid_columns(expressions, types=None):
    """(tokens, columns): every token the expressions read, sorted, and the values sampled for each."""
    lits, calc_tokens, bool_tokens = {}, set(), set()
    for e in expressions:
        _token_uses(e, lits, calc_tokens, False, bool_tokens)
    types = types or {}
    tokens = sorted(t for t in lits if isinstance(t, str))
    columns = [token_values(lits[t], t in calc_tokens or str(types.get(t) or "").lower() in NUMBER_TYPES,
                            t in bool_tokens or str(types.get(t) or "").lower() in ("boolean", "bool"))
               for t in tokens]
    return tokens, columns


def grid_size(expressions, types=None):
    size = 1
    for column in grid_columns(expressions, types)[1]:
        size *= len(column)
    return size


def grid(expressions, types=None):
    """Rows, streamed: the product, over every token the expressions read, of that token's values.

    A token with no literal samples booleans when its declared type (types, token -> type) is boolean or the
    expressions use it as a boolean, and a number when it is read inside a calc or its type is a number.
    """
    tokens, columns = grid_columns(expressions, types)
    for combo in itertools.product(*columns):
        yield {t: v for t, v in zip(tokens, combo) if v is not None}


def partitions(rules):
    """The rules of one decision split into groups that share no read token (each group's grid is independent)."""
    parent = {}

    def find(x):
        while parent.setdefault(x, x) != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    keyed = []
    for i, rule in enumerate(rules):
        toks = [t for t in expr.reads(rule["expression"]) if isinstance(t, str)] or ["#rule%d" % i]
        for t in toks[1:]:
            parent[find(t)] = find(toks[0])
        keyed.append((toks[0], rule))
    groups = {}
    for key, rule in keyed:
        groups.setdefault(find(key), []).append(rule)
    return [groups[k] for k in sorted(groups)]


def token_types(run_dir, findings=None):
    """Declared type per token from stages/fields/fields.json; empty when the file is absent.

    A present file that will not parse raises Unreadable; its shape findings are added to `findings`."""
    fields, found = readers.load_fields(run_dir)
    if findings is not None:
        findings.extend(found)
    return {f.get("token"): f.get("type") for f in fields if isinstance(f.get("token"), str)}


def _calc_compared(node, out):
    """Tokens read on the other side of a comparison with a calc result, e.g. a tolerance against abs(a - b)."""
    if isinstance(node, dict):
        if node.get("kind") == "compare":
            for mine, other in (("left", "right"), ("right", "left")):
                side = node.get(other)
                if isinstance(side, dict) and side.get("kind") == "calc" and isinstance(node.get(mine), dict):
                    out.update(t for t in expr.reads(node[mine]) if isinstance(t, str))
        for v in node.values():
            _calc_compared(v, out)
    elif isinstance(node, list):
        for v in node:
            _calc_compared(v, out)
    return out


def run_types(run_dir, rules, findings=None):
    """token_types, plus number for a token the rules compare with a calc result and the fields file leaves untyped.

    The fields stage runs after rules, so at the rules stage the comparison is the only sign of a number.
    """
    types = token_types(run_dir, findings)
    for rule in rules:
        if isinstance(rule, dict):
            for t in _calc_compared(rule.get("expression"), set()):
                if not types.get(t):
                    types[t] = "number"
    return types


# ---------------------------------------------------------------- upstream reads

def load_text(run_dir, rel):
    path = os.path.join(run_dir, rel)
    if not os.path.isfile(path):
        raise Unreadable("missing %s" % rel)
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except (OSError, ValueError) as exc:
        raise Unreadable("cannot read %s: %s" % (rel, exc))


def read_upstream(run_dir):
    """Intake plus every upstream file it points to; a missing or unparseable one raises Unreadable naming it.

    Input that parses but has the wrong shape is recorded in `up["problems"]` (input-invalid), never raised."""
    intake = load_json(run_dir, "stages/intake/intake.json")
    problems = []
    if not isinstance(intake, dict):
        problems.append({"defect": "input-invalid", "where": "stages/intake/intake.json",
                         "detail": "intake.json is not an object; no upstream process is read"})
        intake = {}
    elif not isinstance(intake.get("processes", []), list):
        problems.append({"defect": "input-invalid", "where": "stages/intake/intake.json $.processes",
                         "detail": "processes is not a list; no upstream process is read"})
        intake = dict(intake, processes=[])
    up = {"intake": intake, "future_states": {}, "future_files": {}, "maps": {}, "snapshot": None,
          "problems": problems}
    for proc in intake.get("processes") or []:
        if not isinstance(proc, dict):
            continue
        if proc.get("future_state"):
            up["future_states"][proc.get("process_id")] = load_json(run_dir, proc["future_state"])
            up["future_files"][proc.get("process_id")] = proc["future_state"]
        if proc.get("transformation_map"):
            up["maps"][proc.get("process_id")] = load_text(run_dir, proc["transformation_map"])
    obs = intake.get("observations")
    rel = obs.get("snapshot_intake") if isinstance(obs, dict) else None
    if rel:
        up["snapshot"] = load_json(run_dir, rel)
    return up


OBS_ID = re.compile(r"^(.*)observation:(.+)$")


def _resource_keys(data, out):
    """Every value of a `resource_key` field anywhere in the snapshot intake."""
    if isinstance(data, dict):
        for k, v in data.items():
            if k == "resource_key" and isinstance(v, str):
                out.add(v)
            else:
                _resource_keys(v, out)
    elif isinstance(data, list):
        for v in data:
            _resource_keys(v, out)
    return out


def observation_keys(upstream):
    """The observation resource keys the snapshot intake holds (the ids an observation id resolves to)."""
    return _resource_keys(upstream.get("snapshot"), set())


def resolving_observations(entry, resolver):
    """The entry's observation ids that resolve through scripts/evidence_ids.py (U1's one resolver). An id that does
    not resolve never counts, also when the snapshot intake exposes no resource keys; malformed entries (null,
    non-string, wrong form, unknown key) never count."""
    out = []
    for oid in entry.get("observation_ids") or []:
        if isinstance(oid, str) and resolver.resolve(oid)[0]:
            out.append(oid)
    return out


def future_state_decisions(upstream):
    """Agent-owned decision nodes of the future states (pins I3), one qualified reference per node (its home)."""
    return [refs[0] for refs in future_state_decision_refs(upstream)]


def future_state_ownership(upstream):
    """The run's one Ownership over every future-state file (knowledge/checks/_agent_steps.py)."""
    procs = [(pid, upstream.get("future_files", {}).get(pid, pid), data)
             for pid, data in upstream["future_states"].items()]
    return _agent_steps.run_ownership(procs)


def future_state_decision_refs(upstream, own=None):
    """Agent-owned decision nodes of the future states: [[qualified refs of one node]], home ref first.

    Which steps are the agent's comes only from knowledge/checks/_agent_steps.py (Agent lane or agent tag; a step
    spanning processes is owned once per process), so an Agent-lane decision with no tag counts and a tagged
    decision outside the Agent lane counts."""
    if own is None:
        own = future_state_ownership(upstream)
    out = []
    for home, refs in own.steps():  # qualified identity: the same bare id in two processes is two steps
        ntype = str((own.node_at(home) or {}).get("type") or "").lower()
        if "decision" in ntype or "gateway" in ntype:
            out.append(refs)
    return out


def prior_art_sources(upstream):
    """Snapshot-intake sources[] entries with prior_art: true (pins I1)."""
    data = upstream["snapshot"]
    marked = set()
    for src in (data.get("sources") if isinstance(data, dict) else None) or []:
        if isinstance(src, dict) and src.get("prior_art") is True:
            marked.add(src.get("id"))
    return marked


def write_grant_rows(run_dir, intake):
    """Rows of every Write grants table the intake's transformation maps hold, keyed by Grant id."""
    rows = {}
    for proc in intake.get("processes") or []:
        rel = proc.get("transformation_map")
        if not rel or not os.path.isfile(os.path.join(run_dir, rel)):
            continue
        with open(os.path.join(run_dir, rel), encoding="utf-8") as fh:
            lines = fh.read().splitlines()
        header = None
        for line in lines:
            cells = [c.strip().strip("`").strip() for c in split_row(line)]
            if not line.strip().startswith("|"):
                header = None if header and not line.strip() else header
                continue
            if "Grant" in cells and "Recipient" in cells:
                header = cells
                continue
            if header and len(cells) == len(header) and not set(line.replace("|", "").strip()) <= set("-: "):
                row = dict(zip(header, cells))
                rows[row["Grant"]] = row
    return rows


# ---------------------------------------------------------------- rule checks

def _same(a, b):
    if expr._is_num(a) and expr._is_num(b):
        return a == b
    return type(a) is type(b) and a == b


def decision_groups(rules):
    """Rules with a valid expression, grouped by decision; each group's grid is the decision's grid."""
    groups = {}
    for rule in rules:
        if has_expression(rule) and not expr.validate(rule["expression"]):
            groups.setdefault(rule.get("decision"), []).append(rule)
    return groups


def _not_executable(e, rows=None):
    """Why the expression is not executable: invalid, or it raises on a row of the grid (default: its own grid,
    at most GRID_BUDGET rows)."""
    problems = expr.validate(e)
    if problems:
        return "invalid expression: %s" % "; ".join(problems)
    for row in (itertools.islice(grid([e]), GRID_BUDGET) if rows is None else rows):
        try:
            expr.evaluate(e, row)
        except expr.ExprError as exc:
            return "raises %s (%s) on row %s" % (type(exc).__name__, exc, json.dumps(row, sort_keys=True))
    return None


def run_decision_grid(group, types, budget=GRID_BUDGET):
    """Evaluate each non-contradictory rule of one decision on its partition's streamed grid, stopping after
    `budget` points in all. Returns ({rule id(): why it raises}, coverage {evaluated, total, partial}).

    The partitions share no token, so a combination of the decision's whole grid is covered when each of its
    partition rows was evaluated: `evaluated` is the product of the rows evaluated per partition, `total` the
    product of the partition sizes, and `partial` is true whenever any combination is left uncovered."""
    why, points, evaluated, total = {}, 0, 1, 1
    parts = partitions(group)
    for part in parts:
        total *= grid_size([r["expression"] for r in part], types)
    for part in parts:
        live = [r for r in part if not is_contradictory(r)]
        rows = grid([r["expression"] for r in part], types)
        done = 0
        for row in rows:
            if points >= budget:
                break
            points += 1
            done += 1
            for rule in live:
                if id(rule) in why:
                    continue
                try:
                    expr.evaluate(rule["expression"], row)
                except expr.ExprError as exc:
                    why[id(rule)] = "raises %s (%s) on row %s" % (type(exc).__name__, exc,
                                                                 json.dumps(row, sort_keys=True))
        evaluated *= done
    return why, {"evaluated": evaluated, "total": total, "partial": evaluated < total}


def check(run_dir):
    run_rel = "checks/check_rules.json"
    fallback = ("Read stages/rules/rules.json and confirm each rule has an expression reading only declared tokens, "
                "uses only declared configuration values, evaluates, cites no prior art as ratified, and that every "
                "decision has a rule or a stamped open item.")
    try:
        rules_doc, rule_findings = load_rules(run_dir)
        outcomes, outcome_findings = readers.load_outcomes(run_dir)
        open_items, item_findings = load_open_items(run_dir)
        upstream = read_upstream(run_dir)
        ownership = future_state_ownership(upstream)
        fs_decisions = future_state_decision_refs(upstream, ownership)
        prior_art = prior_art_sources(upstream)
        field_findings = []
        types = run_types(run_dir, rules_doc["rules"], field_findings)
    except Unreadable as exc:
        return error_report("check_rules", run_rel, str(exc), fallback)

    problems = shape_problems(rule_findings + outcome_findings + item_findings + field_findings)
    problems = list(upstream.get("problems") or []) + problems
    # ownership findings (process-conflict and the others) stay visible: a decision left unowned is never silent
    problems += [{"defect": f["code"], "where": f["where"], "detail": f["detail"]} for f in ownership.findings]
    known_obs = evidence_ids.Resolver(run_dir)
    tokens = set(readers.token_names(rules_doc))
    labels = readers.outcome_labels(outcomes)
    config = {}
    for entry in rules_doc.get("config") or []:
        if isinstance(entry, dict):
            for key in ("id", "name"):
                if entry.get(key) is not None:
                    config[entry[key]] = entry
    for entry in rules_doc.get("config") or []:
        for oid in entry.get("observation_ids") or []:
            if not resolving_observations({"observation_ids": [oid]}, known_obs):
                problems.append({"defect": "unresolved-observation",
                                 "where": "rules.json config %s" % entry.get("id", entry.get("name")),
                                 "detail": "observation id %s does not resolve; it cannot support the value"
                                           % json.dumps(oid)})
    rules = rules_doc["rules"]
    raises, coverage = {}, {}
    for d, group in decision_groups(rules).items():
        why, cov = run_decision_grid(group, types)
        raises.update(why)
        coverage[str(d)] = cov
        if cov["partial"]:
            problems.append({"defect": "grid-partial", "where": "decision %s" % d,
                             "detail": "%d of %d combinations covered (grid budget %d per decision); the rest "
                                       "are unchecked, so this decision is not passed" % (cov["evaluated"],
                                                                                        cov["total"], GRID_BUDGET),
                             "coverage": cov})

    for rule in rules:
        rid = str(rule.get("id"))
        where = "rules.json rule %s" % rid
        if rule.get("policy_choice") is True:
            problems.append({"defect": "policy-as-rule", "where": where,
                             "detail": "a policy choice belongs to a person: move it to open-items.json, kind policy_choice"})
        for src in (rule.get("ratified_sources") or []):
            if src in prior_art:
                problems.append({"defect": "prior-art-ratified", "where": where,
                                 "detail": "cites source %s as ratified; the snapshot intake marks it prior art" % src})
        if not has_expression(rule):
            problems.append({"defect": "no-expression", "where": where, "detail": "the rule has no expression"})
            continue
        e = rule["expression"]
        for t in expr.reads(e):
            if t not in tokens:
                problems.append({"defect": "undeclared-read", "where": where,
                                 "detail": "reads %s, not in the declared tokens" % t})
        allowed = []
        for name in rule.get("config_values") or []:
            entry = config.get(name)
            if entry and "value" in entry and resolving_observations(entry, known_obs):
                allowed.append(entry["value"])
        for lit in expr.governed_literals(e, labels):
            if not any(_same(lit, v) for v in allowed):
                problems.append({"defect": "invented-default", "where": where,
                                 "detail": "literal %s is not a declared configuration value with observations"
                                           % json.dumps(lit)})
        if not is_contradictory(rule):
            why = _not_executable(e, []) or raises.get(id(rule))
            if why:
                problems.append({"defect": "not-executable", "where": where, "detail": why})

    decided = set(r.get("decision") for r in rules)
    opened = set(i.get("decision") for i in open_items if stamped(i))
    wanted = [d.get("id") for d in (outcomes.get("decisions") or []) if isinstance(d, dict)]
    for refs in fs_decisions:
        # a decision spanning processes is covered by a rule or open item at any of its qualified refs
        if not any(r in wanted or r in decided or r in opened for r in refs):
            wanted.append(refs[0])
    for did in wanted:
        if did not in decided and did not in opened:
            problems.append({"defect": "decision-uncovered", "where": "decision %s" % did,
                             "detail": "no rule and no stamped open item for this decision"})

    counts = {"rules": len(rules), "decisions": len(wanted), "problems": len(problems)}
    return report("check_rules", run_rel, problems, counts, fallback, {"grid_coverage": coverage})


# ---------------------------------------------------------------- dropped families

def families(run_dir):
    run_rel = "checks/dropped-families.json"
    fallback = ("List each field with signal failure or state whose token no rule reads, and name its family "
                "from the field's direction.")
    if not os.path.isdir(run_dir):
        return error_report("dropped-families", run_rel, "run folder not found", fallback)
    try:
        rules = load_rules(run_dir)[0]["rules"]
        fields_path = os.path.join(run_dir, "stages", "fields")
        fields, field_findings = [], []
        if os.path.isdir(fields_path):
            fields, field_findings = readers.load_fields(run_dir, required=True)
    except Unreadable as exc:
        return error_report("dropped-families", run_rel, str(exc), fallback)

    carried = set()
    for rule in rules:
        if has_expression(rule):
            carried.update(expr.reads(rule["expression"]))

    notes = []
    grant_cache = {}

    def notice_grant(field):
        """The grant whose row's Recipient is not n/a holding this field's write, matched through system and boundary."""
        if "loaded" not in grant_cache:
            grant_cache["loaded"] = True
            try:
                boundaries = items_of(load_json(run_dir, "stages/boundaries/boundaries.json", False), "boundaries")
                agent = load_json(run_dir, "stages/architecture/agent.yaml", False) or {}
                intake = load_json(run_dir, "stages/intake/intake.json", False) or {}
                grant_cache["boundaries"] = {b.get("id"): b for b in boundaries if isinstance(b, dict)}
                header = agent.get("header") if isinstance(agent, dict) else None
                grant_cache["writes"] = (header.get("proposed_writes") if isinstance(header, dict) else None) or []
                grant_cache["rows"] = write_grant_rows(run_dir, intake)
            except (Unreadable, OSError) as exc:
                notes.append({"defect": "NOTE", "where": "", "detail": "NOTE: notice family not derivable: %s" % exc})
                grant_cache.update({"boundaries": {}, "writes": [], "rows": {}})
        boundary = grant_cache["boundaries"].get(field.get("boundary_id")) or {}
        to = boundary.get("to") or {}
        if to.get("side") != "system":
            return None
        for pw in grant_cache["writes"]:
            if isinstance(pw, dict) and pw.get("system") == to.get("ref"):
                row = grant_cache["rows"].get(pw.get("grant"))
                if row and row.get("Recipient", "n/a") != "n/a":
                    return pw.get("grant")
        return None

    dropped = []
    for field in fields:
        if not isinstance(field, dict) or field.get("signal") not in ("failure", "state"):
            continue
        family = FAMILY_OF_DIRECTION.get(field.get("direction"))
        if family is None:
            continue
        grant = None
        if family == "write":
            grant = notice_grant(field)
            if grant:
                family = "notice"
        if field.get("token") in carried:
            continue
        entry = {"family": family, "field_id": field.get("id"), "token": field.get("token"),
                 "boundary_id": field.get("boundary_id"), "direction": field.get("direction"),
                 "signal": field.get("signal")}
        if grant:
            entry["grant"] = grant
        dropped.append(entry)

    counts = {"fields": len(fields), "dropped": len(dropped)}
    rep = report("dropped-families", run_rel, shape_problems(field_findings) + notes, counts, fallback,
                 {"dropped_families": dropped})
    return rep


# ---------------------------------------------------------------- command line

def write_report(rep, out):
    try:
        os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
        with open(out, "w", encoding="utf-8") as fh:
            json.dump(rep, fh, indent=2, sort_keys=True)
            fh.write("\n")
        return True
    except OSError as exc:
        print("cannot write report %s: %s" % (out, exc), file=sys.stderr)
        return False


def print_report(rep):
    print("%s: %s" % (rep["check"], rep["result"]))
    for p in rep["problems"]:
        print("  %s  %s  %s" % (p["defect"], p["where"], p["detail"]))
    for n in rep.get("notes") or []:
        print("  " + n)
    for d in rep.get("dropped_families") or []:
        print("  dropped %s family: field %s (%s)" % (d["family"], d["field_id"], d["token"]))


def main(argv=None):
    parser = argparse.ArgumentParser(description="Agent Design rule checks")
    parser.add_argument("--run", required=True)
    parser.add_argument("--out")
    parser.add_argument("--families", action="store_true")
    try:
        args = parser.parse_args(argv)
    except SystemExit:
        return 2
    if args.families:
        rep = families(args.run)
        out = args.out or os.path.join(args.run, "checks", "dropped-families.json")
        if rep["exit_code"] == 2:
            print_report(rep)
            return 2
        if not write_report(rep, out):
            return 2
        print_report(rep)
        return 0
    if not os.path.isdir(args.run):
        print("run folder not found: %s" % args.run, file=sys.stderr)
        return 2
    rep = check(args.run)
    out = args.out or os.path.join(args.run, "checks", "check_rules.json")
    if not write_report(rep, out):
        return 2
    print_report(rep)
    return rep["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
