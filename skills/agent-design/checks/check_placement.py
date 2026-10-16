#!/usr/bin/python3
"""Strict placement check (key criterion 11).

Usage: /usr/bin/python3 skills/agent-design/checks/check_placement.py --run <run_dir> [--out <report.json>]
Exit 0 pass, 1 fail (input that parses but has the wrong shape is `input-invalid`, pins G27), 2 usage or
unreadable input. The report follows formats/lists/check-report.schema.json.

Which steps the agent owns comes only from knowledge/checks/_agent_steps.py: a rule placed at
`<process>:<node>` of a step that spans processes is owned when any part lists that step (under any process it
spans); `set:<node>` names a set-level step. `step-not-in-future-state` fails a rule whose node is not a step of
the named process (its own process_id or its future.spans), even when the node exists elsewhere in the file.
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "scripts"))
import evidence_ids  # noqa: E402
import expr  # noqa: E402
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(HERE)), "fast-mode-build", "driver"))
import knowledge_root  # noqa: E402  the one lookup for the plugin's knowledge folder
sys.path.insert(0, os.path.join(knowledge_root.find(), "checks"))
import _agent_steps  # noqa: E402  the one rule for which steps the agent owns

CHECK = "check_placement"
CHECK_PATH = "skills/agent-design/checks/check_placement.py"
TEXT_FIELDS = ("decision", "prose", "expression", "scope")
# `mechanism` is optional (key change v2.2); when present it is judged in full below.
REQUIRED = ("process_id", "future_state_step", "decision", "scenario", "why", "observation_ids")
MECHANISM_KINDS = ("cascade", "bands", "shared_destination")
MECHANISM_KEYS = ("kind", "order", "status")
MECHANISM_WORDS = ("cascade", "bands", "shared destination", "shared_destination")


class Unreadable(Exception):
    pass


class InputInvalid(Exception):
    """Parsed input with the wrong shape (pins G27): exit 1, problem `input-invalid` naming the file and path."""

    def __init__(self, rel, path, detail):
        Exception.__init__(self, "%s %s: %s" % (rel, path, detail))
        self.rel, self.path, self.detail = rel, path, detail


PLACED = "stages/placement/placed-rules.json"


def _need(ok, rel, path, detail):
    if not ok:
        raise InputInvalid(rel, path, detail)


def _text(v):
    return isinstance(v, str) and v != ""


def _entry_type_errors(pr):
    """Wrongly typed fields of one placed entry, per U1's placed-rule schema: [(field, detail)]."""
    errors = []
    for field in ("rule_id", "process_id", "decision", "scenario", "why"):
        if field in pr and not _text(pr[field]):
            errors.append((field, "must be a non-empty string"))
    if "future_state_step" in pr:
        step = pr["future_state_step"]
        if isinstance(step, list):
            if not step or not all(_text(s) for s in step):
                errors.append(("future_state_step", "a list of steps must hold non-empty strings"))
        elif not _text(step):
            errors.append(("future_state_step", "must be a non-empty string (one future-state node id)"))
    if "mechanism" in pr and not isinstance(pr["mechanism"], dict):
        errors.append(("mechanism", "must be an object (kind, order, status: proposed)"))
    if "observation_ids" in pr:
        obs = pr["observation_ids"]
        if not isinstance(obs, list) or not all(_text(o) for o in obs):
            errors.append(("observation_ids", "must be a list of strings (empty is allowed unless the rule is Observed)"))
    if "text_sha256" in pr and not _text(pr["text_sha256"]):
        errors.append(("text_sha256", "must be a non-empty string"))
    return errors


def _load(run_dir, rel):
    path = os.path.join(run_dir, rel)
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError) as e:
        raise Unreadable("%s: %s" % (rel, e))


def canon(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _placed_list(data):
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and isinstance(data.get("placed_rules"), list):
        return data["placed_rules"]
    raise InputInvalid(PLACED, "$", "expected a list of placed rules")


def _config_name(entry):
    if isinstance(entry, str):
        return entry
    if isinstance(entry, dict):
        for key in ("token", "name", "id"):
            if isinstance(entry.get(key), str):
                return entry[key]
    return None


def _step_ids(graph):
    """Node ids of a future-state file: graph.nodes[].id, or top-level nodes[].id."""
    nodes = []
    if isinstance(graph, dict):
        g = graph.get("graph") if isinstance(graph.get("graph"), dict) else graph
        nodes = g.get("nodes") or []
    return {n.get("id") for n in nodes if isinstance(n, dict) and isinstance(n.get("id"), str)}


def _mechanism_words(text):
    low = text.lower() if isinstance(text, str) else ""
    return [w for w in MECHANISM_WORDS if w in low]


# How certain each evidence state is: Observed is established by evidence; Assumed and Contradictory are
# both unestablished readings; needs_more_information claims nothing.
CERTAINTY = {"Observed": 2, "Assumed": 1, "Contradictory": 1, "needs_more_information": 0}


def state_move(rule_state, placed_state):
    """'same', 'more-cautious', 'more-certain', or None when either state is absent or unrecognised (the
    shared citation rule reports an unrecognised state)."""
    if rule_state not in CERTAINTY or placed_state not in CERTAINTY:
        return None
    a, b = CERTAINTY[rule_state], CERTAINTY[placed_state]
    return "same" if a == b else ("more-cautious" if b < a else "more-certain")


def _resolving(resolver, ids):
    return any(resolver.resolve(oid)[0] for oid in ids or [] if isinstance(oid, str))


def _report(run_dir, problems, counts, error=None, invalid=None, notes=None):
    if invalid is not None:
        result, code = "fail", 1
        problems = [{"defect": "input-invalid", "where": "%s %s" % (invalid.rel, invalid.path),
                     "detail": invalid.detail}]
    elif error is not None:
        result, code = "error", 2
        problems = [{"defect": "unreadable-input", "where": "", "detail": error}]
    elif problems:
        result, code = "fail", 1
    else:
        result, code = "pass", 0
    return {
        "check": CHECK,
        "path": CHECK_PATH,
        "run": ".",
        "exit_code": code,
        "result": result,
        "problems": problems,
        "counts": counts,
        "agent_fallback": "none: deterministic check, no agent judgment",
        "notes": notes or [],
    }


def _judge(run_dir):
    intake = _load(run_dir, "stages/intake/intake.json")
    rules_doc = _load(run_dir, "stages/rules/rules.json")
    placed = _placed_list(_load(run_dir, "stages/placement/placed-rules.json"))
    decls = _load(run_dir, "stages/placement/declarations.json")
    agent = _load(run_dir, "stages/architecture/agent.yaml")

    i_rel, r_rel = "stages/intake/intake.json", "stages/rules/rules.json"
    d_rel, a_rel = "stages/placement/declarations.json", "stages/architecture/agent.yaml"
    _need(isinstance(intake, dict), i_rel, "$", "must be an object")
    _need(isinstance(intake.get("processes"), list), i_rel, "$.processes", "must be a list")
    for i, p in enumerate(intake["processes"]):
        _need(isinstance(p, dict) and _text(p.get("process_id")) and _text(p.get("future_state")),
              i_rel, "$.processes[%d]" % i, "must be an object with string process_id and future_state")
    obs = intake.get("observations")
    _need(isinstance(obs, dict) and _text(obs.get("snapshot_intake")), i_rel, "$.observations.snapshot_intake",
          "must be a string path")
    _need(isinstance(rules_doc, dict) and isinstance(rules_doc.get("rules"), list), r_rel, "$.rules",
          "must be a list")
    _need(isinstance(rules_doc.get("config", []), list), r_rel, "$.config", "must be a list")
    _need(isinstance(decls, dict), d_rel, "$", "must be an object")
    for key in ("stand_ins", "configuration"):
        _need(isinstance(decls.get(key, []), list), d_rel, "$." + key, "must be a list")
    _need(isinstance(agent, dict) and isinstance(agent.get("parts"), list), a_rel, "$.parts", "must be a list")
    for i, part in enumerate(agent["parts"]):
        _need(isinstance(part, dict) and isinstance(part.get("owns_steps", []), list),
              a_rel, "$.parts[%d].owns_steps" % i, "must be a list")

    processes = {p["process_id"]: p for p in intake["processes"]}
    loaded = {pid: _load(run_dir, p["future_state"]) for pid, p in processes.items()}
    steps_by_process = {pid: _step_ids(doc) for pid, doc in loaded.items()}
    ownership = _agent_steps.run_ownership([(pid, processes[pid]["future_state"], doc)
                                            for pid, doc in loaded.items()])
    resolver = evidence_ids.Resolver(run_dir)
    listed = [(str(part.get("id")), [s for s in part.get("owns_steps") or [] if isinstance(s, str)])
              for part in agent["parts"]]
    claims, claim_notes = _agent_steps.expand_claims(ownership, listed)
    owned = set(claims)  # only what the shared rule owns; a raw owns_steps ref never makes a step owned
    ownership_findings = [f for f in claim_notes if f["code"] == "person-step-owned"]
    ambiguous = {f["where"].split(" ", 1)[-1]: f for f in ownership.findings if f["code"] == "ambiguous-owner"}
    for part_id, refs in listed:
        for ref in refs:
            if ref in ownership.owned or ownership.is_person_ref(ref):
                continue
            nid = ref.partition(":")[2]
            if nid in ambiguous:
                ownership_findings.append({"code": "ambiguous-owner", "where": ref, "detail": ambiguous[nid]["detail"]})
            elif ownership.is_place(ref.partition(":")[0], nid):
                ownership_findings.append({"code": "claim-not-agent-step", "where": ref,
                                           "detail": "part %s lists a step that is not the agent's; it is not owned"
                                                     % part_id})

    sources = {r.get("id"): r for r in rules_doc.get("rules") or [] if isinstance(r, dict)}
    config_names = {n for n in (_config_name(c) for c in rules_doc.get("config") or []) if n}
    stand_ins = {d.get("token"): d for d in decls.get("stand_ins") or [] if isinstance(d, dict)}
    configured = {n for n in (_config_name(c) for c in decls.get("configuration") or []) if n}

    problems = []
    notes = []

    def add(defect, where, detail):
        problems.append({"defect": defect, "where": where, "detail": detail})

    for f in ownership_findings:
        add(f["code"], f["where"], f["detail"])
    placements = {}
    for i, pr in enumerate(placed):
        if not isinstance(pr, dict):
            add("input-invalid", "%s $[%d]" % (PLACED, i), "a placed rule must be an object")
            continue
        rid = pr.get("rule_id")
        where = "placed[%d] %s" % (i, rid)
        for field in REQUIRED:
            if field not in pr:
                add("missing-field", where, "placed rule is missing `%s`" % field)
        bad = dict(_entry_type_errors(pr))
        for field, detail in bad.items():
            add("input-invalid", "%s $[%d].%s" % (PLACED, i, field), "`%s` %s" % (field, detail))
        # Only an entry naming a usable step counts as a placement (criterion 3(c)); a list of named steps
        # counts too, so it is judged as placed more than once (criterion 3(d)).
        if "rule_id" not in bad and "future_state_step" in pr and "future_state_step" not in bad:
            placements.setdefault(rid, []).append(i)
        src = sources.get(rid)
        if src is None:
            add("placed-rule-unknown", where, "rule_id %r has no rule in stages/rules/rules.json" % rid)
        else:
            for field in TEXT_FIELDS:
                if canon(pr.get(field)) != canon(src.get(field)):
                    add("rule-text-changed", where, "`%s` is not byte-identical to rules.json" % field)
                    if field == "prose":
                        added = [w for w in _mechanism_words(pr.get("prose"))
                                 if w not in _mechanism_words(src.get("prose"))]
                        if added:
                            add("mechanism-in-rule-text", where,
                                "prose names mechanism %s; proposals belong in `mechanism`" % ", ".join(added))
        mech = pr.get("mechanism")
        if "mechanism" not in bad and isinstance(mech, dict):
            if mech.get("status") != "proposed":
                add("mechanism-not-proposed", where, "mechanism must carry status: proposed")
            if mech.get("kind") not in MECHANISM_KINDS:
                add("mechanism-invalid", where, "mechanism kind %r is not one of %s"
                    % (mech.get("kind"), ", ".join(MECHANISM_KINDS)))
            order = mech.get("order")
            integral = isinstance(order, float) and order.is_integer()  # schema "integer" includes 1.0
            if isinstance(order, bool) or not (integral or isinstance(order, (int, list, str))):
                add("mechanism-invalid", where, "mechanism order must be an integer, a list or a string")
            extra = sorted(k for k in mech if k not in MECHANISM_KEYS)
            if extra:
                add("mechanism-invalid", where, "mechanism carries unknown keys %s" % ", ".join(extra))

        pid = pr.get("process_id")
        step = pr.get("future_state_step")
        if "future_state_step" in bad or "process_id" in bad:
            pass  # nothing is computed on an invalid field (pins G27)
        elif isinstance(step, list):
            add("rule-placed-twice", where, "future_state_step is a list; place each rule at exactly one step")
        elif "process_id" in pr and pid not in processes and pid != _agent_steps.SET:
            add("unknown-process", where, "process_id %r is not among the intake's processes" % pid)
        elif isinstance(step, str) and (pid in processes or pid == _agent_steps.SET):
            ref = "%s:%s" % (pid, step)
            if pid == _agent_steps.SET:
                in_place = ownership.is_place(pid, step)
            else:
                in_place = step in steps_by_process[pid] and (step not in ownership.places
                                                              or ownership.is_place(pid, step))
            if not in_place:
                add("step-not-in-future-state", where, "%s is not a step of the process's future state" % ref)
            elif ref not in owned:  # a step that does not exist is named once, above
                add("step-not-owned", where, "%s is not an agent step any part of agent.yaml owns" % ref)

        if "observation_ids" not in bad and "observation_ids" in pr:
            # The evidence state is read, never guessed: the placed rule's own, else its rule's in rules.json.
            state = pr.get("evidence_state")
            src_state = src.get("evidence_state") if isinstance(src, dict) else None
            listed = evidence_ids.list_problems(resolver, pr["observation_ids"],
                                                state if state is not None else src_state)
            moved = state_move(src_state, state)
            if moved == "more-cautious":
                # A more cautious judgement is accepted; the agent's explanation is recorded when it gave one.
                note = pr.get("evidence_note")
                notes.append({"where": where, "detail": "evidence state moved from %s to %s%s" % (
                    src_state, state, (": " + note) if isinstance(note, str) and note.strip() else "")})
            elif moved == "more-certain" and not _resolving(resolver, pr["observation_ids"]):
                # Damage prevented: an unsupported claim presented as more certain than its rule's evidence.
                add("evidence-state-raised", where, "placed evidence state %r is more certain than the rule's %r "
                    "and cites no observation id that resolves" % (state, src_state))
            for kind, detail in listed:
                add("unknown-observation-id" if kind == "invalid" else "rule-evidence", where, detail)

        expression = pr.get("expression")
        try:
            read_tokens = expr.reads(expression)
            stand_in_tokens = expr.stand_in_reads(expression)
        except Exception as e:  # malformed expression is already a rule-text defect or an invalid rule
            add("expression-unreadable", where, str(e))
            read_tokens, stand_in_tokens = [], []
        for tok in stand_in_tokens:
            decl = stand_ins.get(tok)
            if decl is None:
                add("stand-in-undeclared", where, "%s has no entry in declarations.json stand_ins" % tok)
            elif decl.get("evidence_state") != "Assumed":
                add("stand-in-not-assumed", where, "%s is declared but not stamped Assumed" % tok)
        for tok in read_tokens:
            if tok in config_names and tok not in configured:
                add("config-undeclared", where, "%s has no entry in declarations.json configuration" % tok)

    for rid, idx in placements.items():
        if len(idx) > 1:
            add("rule-placed-twice", "rule %s" % rid, "placed at %d entries %s" % (len(idx), idx))
    for rid in sources:
        if rid not in placements:
            add("rule-not-placed", "rule %s" % rid, "rule in rules.json is placed at zero steps")

    counts = {"rules": len(sources), "placed": len(placed), "stand_ins": len(stand_ins),
              "configuration": len(configured)}
    return problems, counts, notes


def check(run_dir):
    try:
        problems, counts, notes = _judge(run_dir)
    except Unreadable as e:
        return _report(run_dir, [], {}, error=str(e))
    except InputInvalid as e:
        return _report(run_dir, [], {}, invalid=e)
    except (AttributeError, TypeError) as e:
        return _report(run_dir, [], {}, invalid=InputInvalid("(input)", "$", "wrong shape: %s" % e))
    return _report(run_dir, problems, counts, notes=notes)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--run", required=True)
    parser.add_argument("--out")
    try:
        args = parser.parse_args(argv)
    except SystemExit:
        return 2
    report = check(args.run)
    out = args.out or os.path.join(args.run, "checks", CHECK + ".json")
    try:
        os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
        with open(out, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
            f.write("\n")
    except OSError as e:
        sys.stderr.write("cannot write report: %s\n" % e)
        return 2
    for p in report["problems"]:
        sys.stderr.write("%s: %s: %s\n" % (p["defect"], p["where"], p["detail"]))
    return report["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
