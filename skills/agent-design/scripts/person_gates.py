#!/usr/bin/python3
"""Whether a case for a gate a person decides needs that person's decision: one reading for check_cases and
convert_cases.

A case that states no person decision is read as testing the hand-over to the person, unless its expected result
depends on the decision. It depends on it when any expected outcome or allowed alternative, or any rule assertion
in `holds` (true or false), concerns a rule downstream of the gate:

- `next_step` in an outcome's values (what happens after the person's choice);
- an outcome label that is not one of the gate's own labels and that a downstream rule sets;
- an expected value, named derivation or amount (other than the outcome) that a downstream rule sets, or a
  derivation that reads such a token;
- a `holds` entry naming a downstream rule (a gate id named in `holds` stands for its rules).

A rule is downstream of a gate when it is placed at a step reachable from the gate's step in the run's future-state
graph (strictly after it), when it reads the person's decision token, or when it reads a token a downstream rule
sets. Structure only: steps, edges, tokens and labels, never wording. Missing inputs give an empty reading, never
an exception. Standard library only, Python 3.9.
"""
import json
import os

import compute_expected
import expr

PERSON_TOKENS = ("person.decision",)
OUTCOME = "outcome"
NEXT_STEP = "next_step"
GATE_PREFIX = "gate:"
INTAKE = "stages/intake/intake.json"
RULES = "stages/rules/rules.json"
PLACED = "stages/placement/placed-rules.json"
OUTCOMES = "stages/rules/outcomes.json"


def _load(run_dir, rel):
    try:
        with open(os.path.join(run_dir, rel), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _bare(gid):
    return gid[len(GATE_PREFIX):] if isinstance(gid, str) and gid.startswith(GATE_PREFIX) else gid


def _sets(node, tokens, labels):
    """Every token a rule sets and every literal outcome label it sets, anywhere in the expression."""
    if isinstance(node, dict):
        sets = node.get("sets")
        if isinstance(sets, dict):
            for tok, val in sets.items():
                tokens.add(tok)
                if tok == OUTCOME and isinstance(val, dict) and val.get("kind") == "literal" \
                        and isinstance(val.get("value"), str):
                    labels.add(val["value"])
        for v in node.values():
            _sets(v, tokens, labels)
    elif isinstance(node, list):
        for v in node:
            _sets(v, tokens, labels)


def _reads(expression):
    try:
        return set(expr.reads(expression))
    except Exception:  # an unreadable expression reads nothing here; its own check reports it
        return set()


def _derivation_reads(node, out):
    """Every row reference a derivation reads, in both forms compute_expected evaluates: a named derivation's
    input `{"read": "<row>.<token>"}` and an arithmetic node `{"kind": "read", "token": "<row>.<token>"}`."""
    if isinstance(node, dict):
        if isinstance(node.get("read"), str):
            out.add(node["read"])
        if node.get("kind") == "read" and isinstance(node.get("token"), str):
            out.add(node["token"])
        for v in node.values():
            _derivation_reads(v, out)
    elif isinstance(node, list):
        for v in node:
            _derivation_reads(v, out)


class Reading(object):
    """Downstream rules per gate for one run. Pass loaded documents to skip reading them from the run folder."""

    def __init__(self, run_dir=None, rules_doc=None, placed=None, outcomes=None):
        load = (lambda rel: _load(run_dir, rel)) if run_dir else (lambda rel: None)
        rules_doc = rules_doc if rules_doc is not None else load(RULES)
        placed = placed if placed is not None else expr.placed_rule_list(load(PLACED))
        outcomes = outcomes if outcomes is not None else load(OUTCOMES)
        rules = rules_doc.get("rules") if isinstance(rules_doc, dict) else rules_doc
        self.reads, self.sets, self.labels, self.steps = {}, {}, {}, {}
        for r in rules if isinstance(rules, list) else []:
            if not isinstance(r, dict) or not isinstance(r.get("id"), str):
                continue
            tokens, labels = set(), set()
            _sets(r.get("expression"), tokens, labels)
            self.reads[r["id"]], self.sets[r["id"]], self.labels[r["id"]] = _reads(r.get("expression")), tokens, labels
            self.steps.setdefault(r["id"], set())
        for p in placed if isinstance(placed, list) else []:
            if isinstance(p, dict) and isinstance(p.get("rule_id"), str) and isinstance(p.get("future_state_step"), str):
                self.steps.setdefault(p["rule_id"], set()).add(
                    "%s:%s" % (p.get("process_id") or "", p["future_state_step"]))
        self.gate_labels, self.gate_step, self.gate_rules = {}, {}, {}
        for g in (outcomes or {}).get("gates") or [] if isinstance(outcomes, dict) else []:
            if not isinstance(g, dict) or not isinstance(g.get("id"), str):
                continue
            gid = g["id"]
            self.gate_labels[gid] = set(x for x in g.get("labels") or [] if isinstance(x, str))
            if isinstance(g.get("step"), str):
                self.gate_step[gid] = g["step"]
            listed = g.get("rules")
            self.gate_rules[gid] = [x for x in listed if isinstance(x, str)] if isinstance(listed, list) and listed \
                else [p.get("rule_id") for p in placed or [] if isinstance(p, dict) and p.get("decision") == gid]
        self.edges = self._graph(run_dir, load)
        self._down = {}

    @staticmethod
    def _graph(run_dir, load):
        """Step ref -> set of next step refs, over every process's future-state graph the intake lists."""
        out = {}
        intake = load(INTAKE)
        for pr in (intake or {}).get("processes") or [] if isinstance(intake, dict) else []:
            if not isinstance(pr, dict) or not isinstance(pr.get("future_state"), str):
                continue
            fs = load(pr["future_state"])
            g = fs.get("graph", fs) if isinstance(fs, dict) else None
            if not isinstance(g, dict):
                continue
            pid = pr.get("process_id") or ""
            nodes = {n.get("id"): n for n in g.get("nodes") or [] if isinstance(n, dict)}

            def ref(nid):
                n = nodes.get(nid) or {}
                return "%s:%s" % (n.get("process_id") or pid, nid)
            for e in g.get("edges") or []:
                if isinstance(e, dict) and isinstance(e.get("source"), str) and isinstance(e.get("target"), str):
                    out.setdefault(ref(e["source"]), set()).add(ref(e["target"]))
        return out

    def _after(self, step):
        """Step refs strictly after `step` (a bare node id matches that node in any process)."""
        starts = [s for s in self.edges if s == step or s.split(":", 1)[-1] == step]
        seen, todo = set(), [t for s in starts for t in self.edges.get(s, ())]
        while todo:
            s = todo.pop()
            if s not in seen:
                seen.add(s)
                todo.extend(self.edges.get(s, ()))
        return seen - set(starts)

    def downstream(self, gid):
        gid = _bare(gid)
        if gid in self._down:
            return self._down[gid]
        after = self._after(self.gate_step[gid]) if gid in self.gate_step else set()
        after_bare = {s.split(":", 1)[-1] for s in after}
        down = set()
        for rid in self.reads:
            placed_after = any(s in after or (s.startswith(":") and s[1:] in after_bare) for s in self.steps[rid])
            if placed_after or self.reads[rid] & set(PERSON_TOKENS):
                down.add(rid)
        changed = True
        while changed:  # a rule reading a token a downstream rule sets is downstream too
            flowing = set().union(*[self.sets[r] - {OUTCOME} for r in down]) if down else set()
            more = {r for r in self.reads if r not in down and self.reads[r] & flowing}
            changed = bool(more)
            down |= more
        self._down[gid] = down
        return down

    def gate_step_of(self, gid):
        return self.gate_step.get(_bare(gid))

    def leads_to_gate(self, gid):
        """Rule ids placed at the gate's step or at a step the graph leads from to the gate's step."""
        gid = _bare(gid)
        step = self.gate_step.get(gid)
        if step is None:
            return set()
        bare = step.split(":", 1)[-1]
        out = set()
        for rid, steps in self.steps.items():
            for s in steps:
                if s == step or s.split(":", 1)[-1] == bare and s.startswith(":"):
                    out.add(rid)
                elif step in self._after(s) or (":" not in step and bare in {a.split(":", 1)[-1] for a in self._after(s)}):
                    out.add(rid)
        return out

    def needs_decision(self, case, gid):
        """True when the case's expected result depends on the person's decision at gate `gid`."""
        gid = _bare(gid)
        down = self.downstream(gid)
        own = self.gate_labels.get(gid, set())
        outcomes = [case.get("expected")] + list(case.get("allowed_alternatives") or [])
        for o in outcomes:
            if not isinstance(o, dict):
                continue
            values = o.get("values") if isinstance(o.get("values"), dict) else {}
            if NEXT_STEP in values:
                return True
            label = o.get("label")
            if isinstance(label, str) and label not in own and any(label in self.labels[r] for r in down):
                return True
            # compute_expected's own reading: a named derivation written into values counts as a derivation
            plain, derivations, _ = compute_expected.outcome_parts(o)
            tokens = set(values) | set(plain) | set(derivations)
            if isinstance(o.get("amounts"), dict):
                tokens.update(o["amounts"])
            reads = set()
            _derivation_reads(derivations, reads)
            set_down = set().union(*[self.sets[r] for r in down]) - {OUTCOME} if down else set()
            if (tokens - {OUTCOME, NEXT_STEP}) & set_down:
                return True
            if any(ref == tok or ref.endswith("." + tok) for ref in reads for tok in set_down):
                return True
        for h in case.get("holds") or []:
            rid = h.get("rule_id") if isinstance(h, dict) else None
            named = self.gate_rules.get(_bare(rid), [rid]) if isinstance(rid, str) else []
            if any(r in down for r in named):
                return True
        return False
