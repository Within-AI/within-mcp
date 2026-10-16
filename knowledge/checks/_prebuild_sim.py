#!/usr/bin/env python3
"""Shared code for stage 11, the pre-build simulation, and for stage 9's code-or-judgment mark.

One module, imported by every piece that needs it, so the pieces can never disagree:
  * the numeric-predicate lexicon, `numeric_predicate_hits(text)`: check-rule-mark.sh (stage 9), the runner's
    prompt lint and the rule-trace check all read it from here;
  * the prompt lint, `lint_prompt(rendered, variant, ...)`: the hard line that no judgment prompt asks a model to
    add, subtract, count, compare two numbers or test a threshold.

Stdlib only in this part (PyYAML is imported lazily by the parts that read YAML).
"""
import re

# --------------------------------------------------------------------------------------------------
# The numeric-predicate lexicon (plan 3.3). A condition or question that matches any entry has, or may have, a
# numeric predicate: a sum, difference, count, threshold, tolerance or comparison of numbers. Words match whole
# words, case-insensitively, including the listed inflections; the operators match as written.

NUMERIC_WORDS = (
    "sum", "sums", "summed", "summing",
    "total", "totals", "totalled", "totaled", "totalling", "totaling",
    "add", "adds", "added", "adding",
    "subtract", "subtracts", "subtracted", "subtracting",
    "difference", "differences",
    "net", "nets", "netted", "netting",
    "balance", "balances", "balanced", "balancing",
    "count", "counts", "counted", "counting",
    "number of",
    "exceeds", "exceed", "exceeded", "exceeding",
    "more than", "less than", "at least", "at most",
    "greater", "fewer",
    "within",
    "tolerance", "tolerances",
    "threshold", "thresholds",
    "equals", "equal", "equalled", "equaled",
    "matches the amount",
    "covers", "cover", "covered", "covering",
    "zero",
    "percent", "percentage",
    "ratio", "ratios",
    "rounded", "rounding",
)
NUMERIC_OPERATORS = ("<=", ">=", "!=", "==", "≠", "<", ">", "=")

_WORD_RE = re.compile(r"(?<![\w-])(%s)(?![\w-])" % "|".join(
    re.escape(w).replace(r"\ ", r"\s+") for w in sorted(NUMERIC_WORDS, key=len, reverse=True)), re.IGNORECASE)
# An operator counts when it stands alone between operands ("a >= b", "x = 0"), never inside an arrow ("->",
# "=>") or a placeholder ("<describe>").
_OP_RE = re.compile(r"(?:^|(?<=\s))(<=|>=|!=|==|≠|<|>|=)(?=\s|\d|-\d)")


def numeric_predicate_hits(text):
    """Every lexicon entry TEXT matches, lower-cased, in order of first appearance, each once."""
    if not isinstance(text, str) or not text:
        return []
    found = []
    for m in _WORD_RE.finditer(text):
        word = re.sub(r"\s+", " ", m.group(1).lower())
        if word not in found:
            found.append(word)
    for m in _OP_RE.finditer(text):
        if m.group(1) not in found:
            found.append(m.group(1))
    return found


def basis_names_every_hit(basis, hits):
    """True when BASIS (a numeric_free_basis) names each flagged word or operator of HITS."""
    if not isinstance(basis, str):
        return False
    low = re.sub(r"\s+", " ", basis.lower())
    return all(h in low for h in hits)


def missing_from_basis(basis, hits):
    low = re.sub(r"\s+", " ", (basis or "").lower())
    return [h for h in hits if h not in low]


def nonspace_len(text):
    return len(re.sub(r"\s", "", text)) if isinstance(text, str) else 0


# --------------------------------------------------------------------------------------------------
# The prompt lint (plan 5.1). Run by the runner on every rendered prompt BEFORE any request is sent, and re-run
# by the rule-trace check over every recorded prompt. A failing prompt is never sent.

VARIANTS = ("single", "identify", "judge")
_SECTION_RE = re.compile(r"(?m)^## ")
_NUMERIC_FIELD_RE = re.compile(r"\b(number|numeric|integer|int|float|decimal|amount_value)\b|\"\s*:\s*-?\d", re.I)
LINT_DEFECT = "judgment prompt would ask the model to compute or compare a number"


def sections(rendered):
    """{heading: body} for every '## ' section of a rendered prompt (heading text without the hashes)."""
    out = {}
    parts = _SECTION_RE.split(rendered or "")
    for part in parts[1:]:
        heading, _, body = part.partition("\n")
        out[heading.strip()] = body.strip()
    return out


def _section(secs, prefix):
    return next((body for heading, body in secs.items() if heading.lower().startswith(prefix.lower())), None)


def question_text(rendered, variant):
    """The question section a lint reads: the rendered condition of a single-step prompt, the identify question or
    the judge question."""
    secs = sections(rendered)
    if variant == "single":
        rule = _section(secs, "The rule") or ""
        m = re.search(r"(?m)^- Condition:\s*(.*)$", rule)
        return m.group(1).strip() if m else ""
    return _section(secs, "The question") or ""


def response_shape(answer):
    """The response shape a prompt's Answer section asks for: its first backticked segment (the whole section when
    none is backticked)."""
    m = re.search(r"`([^`]*)`", answer or "", re.S)
    return m.group(1) if m else (answer or "")


def _norm(text):
    return re.sub(r"\s+", " ", text or "").strip().lower()


def lint_prompt(rendered, variant, split=False, condition=None, numeric_free_basis=None):
    """Every reason RENDERED (a prompt of VARIANT) must not be sent; [] when it may be sent.

    Fails when: the question matches the numeric lexicon (a single-step prompt is exempt only when the rule's
    numeric_free_basis names every flagged word); an identify or judge prompt carries the rule's original
    condition text; the response shape has a numeric field, or (identify, judge) a `holds` field; or a split
    rule is about to be sent as a single request."""
    problems = []
    if variant not in VARIANTS:
        return ["unknown prompt variant %r (expected one of %s)" % (variant, ", ".join(VARIANTS))]
    if split and variant == "single":
        problems.append("a split rule is about to be sent as a single request; it must run identify, compute in "
                        "code, judge, combine in code")
    question = question_text(rendered, variant)
    if not question.strip():
        problems.append("the prompt has no question section to decide")
    hits = numeric_predicate_hits(question)
    if hits:
        if variant == "single" and numeric_free_basis and basis_names_every_hit(numeric_free_basis, hits):
            pass
        else:
            problems.append("the %s question asks for a number or a numeric comparison (it says: %s)"
                            % (variant, ", ".join(hits)))
    if variant in ("identify", "judge") and condition and _norm(condition) and _norm(condition) in _norm(rendered):
        problems.append("the %s prompt carries the rule's original condition, which only code may decide" % variant)
    answer = response_shape(_section(sections(rendered), "Answer") or "")
    if _NUMERIC_FIELD_RE.search(answer):
        problems.append("the response shape has a numeric field; a judgment answer never returns a number")
    if variant in ("identify", "judge") and re.search(r"[\"']holds[\"']", answer):
        problems.append("a split rule's %s response carries a holds field; code combines the steps into holds"
                        % variant)
    return problems


# ==================================================================================================
# Stage 11 shared code (plan 4, 5, 7): file helpers, the policy and its rows, canonical row shas, the rules code
# registry, and the deterministic derivations (walk, comparison, coverage, defects) that the scripts write and
# the checks re-derive. Every derivation here is a pure function of recorded files, so a check that re-derives a
# file byte for byte uses exactly the code that wrote it.

from collections import namedtuple as _namedtuple
import hashlib
import json
import os
import sys

STAGE = "pre-build-simulation"
OUT = "output/pre-build-simulation"
MANIFEST_PATH = OUT + "/pre-build-simulation.yaml"
# stage 11 writes this when an older Test Suite case has no structured expected_outcome; the manifest pins it at
# data.derived_expected_outcomes (null when it does not exist)
DERIVED_EO_PATH = OUT + "/expected-outcomes.derived.yaml"
MARKS = ("code", "judgment")
HANDLINGS = ("code", "judgment", "not-run")
END_STATES = ("escalated", "recommended", "staged", "routed", "no-route", "needs_more_information", "written",
              "handed_off")
HOLDS = (True, False, "needs_more_information", "not_run")
LABELS = ("client_data", "synthetic_data")
BASIS_ORDER = ("assumed", "real-layout", "client-sample")   # weakest first
STATE_BASIS = {"real-sample": "client-sample", "real-layout": "real-layout", "assumed": "assumed"}
NO_MODEL_REASON = "model calls not made in this run"
PROVISIONAL_LABEL = "PROVISIONAL TEST VALUE - NOT A CLIENT VALUE"
RETIRED_REVIEW_KEYS = ("business_owner_review", "reviews", "samples_queued")
DEFAULT_MODEL = "claude-opus-5-5"
DEFAULT_REPEATS = 3
MIN_REASON = 20


class PrebuildError(ValueError):
    """A stage 11 file cannot be read or is malformed (a setup error, exit 2 in the checks)."""


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_text(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def yaml_mod():
    try:
        import yaml
    except ImportError:
        raise PrebuildError("PyYAML is required but not importable (pip install pyyaml)")
    return yaml


def load_yaml(path):
    try:
        with open(path, encoding="utf-8") as f:
            return yaml_mod().safe_load(f)
    except OSError as exc:
        raise PrebuildError("cannot read %s: %s" % (path, exc))
    except Exception as exc:  # noqa: BLE001 - a YAML parse error is a setup error
        if isinstance(exc, PrebuildError):
            raise
        raise PrebuildError("cannot parse %s: %s" % (path, exc))


def load_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError) as exc:
        raise PrebuildError("cannot read %s: %s" % (path, exc))


def dump_yaml(doc):
    """The one YAML serialisation every stage 11 writer and re-deriving check uses (deterministic)."""
    return yaml_mod().safe_dump(doc, sort_keys=True, allow_unicode=True, default_flow_style=False, width=100)


def canon_json(obj):
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def write_text(path, text):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def pin(run_root, rel):
    """{path, sha256} of a run-relative file."""
    return {"path": rel, "sha256": sha256_file(os.path.join(run_root, rel))}


# ---- the decision policy -------------------------------------------------------------------------

def policy_rows(policy):
    """[(rule_set, row)] in policy order."""
    out = []
    for rs in (policy or {}).get("rule_sets") or []:
        for row in rs.get("rules") or []:
            if isinstance(row, dict) and row.get("rule_id"):
                out.append((rs, row))
    return out


def rows_by_id(policy):
    return {row["rule_id"]: row for _rs, row in policy_rows(policy)}


def owner_of(policy):
    return {row["rule_id"]: rs.get("owner") for rs, row in policy_rows(policy)}


def is_other_mark(row):
    return row.get("runs_as") not in MARKS


def open_register(policy):
    return {o.get("id"): o for o in (policy or {}).get("open_register") or [] if isinstance(o, dict)}


def row_open_ids(row):
    stamp = row.get("open_stamp")
    stamps = stamp if isinstance(stamp, list) else [stamp]
    return [s for s in stamps if isinstance(s, str) and s.strip() not in ("", "-", "—")]


def rule_open_of(row, policy):
    """The first `kind: rule` open the row is stamped with, or None (such a rule runs as needs more information)."""
    reg = open_register(policy)
    return next((o for o in row_open_ids(row) if (reg.get(o) or {}).get("kind") == "rule"), None)


def value_opens_of(row, policy):
    reg = open_register(policy)
    return [o for o in row_open_ids(row) if (reg.get(o) or {}).get("kind") == "value"]


def other_marks_index(other_marks):
    return {e.get("rule_id"): e for e in (other_marks or {}).get("rules") or [] if isinstance(e, dict)}


def effective_handling(row, other_entry):
    """How stage 11 runs a row: code, judgment or not-run (an other mark with no entry runs not-run)."""
    if not is_other_mark(row):
        return row["runs_as"]
    if other_entry is None or other_entry.get("handling") not in HANDLINGS:
        return "not-run"
    return other_entry["handling"]


def effective_split(row, other_entry):
    if not is_other_mark(row):
        return row.get("judgment_split")
    return (other_entry or {}).get("judgment_split")


def effective_numeric_free_basis(row, other_entry):
    if not is_other_mark(row):
        return row.get("numeric_free_basis")
    return (other_entry or {}).get("numeric_free_basis")


def canonical_row(row, other_entry=None):
    """The canonical rule row (plan 5.1): the sha input binding generated code to the written rule."""
    doc = {k: row.get(k) for k in ("rule_id", "condition", "action", "operative_tokens", "runs_as",
                                    "computed_inputs")}
    for k in ("judgment_split", "numeric_free_basis"):
        if k in row:
            doc[k] = row[k]
    if other_entry is not None:
        om = {"handling": other_entry.get("handling")}
        for k in ("judgment_split", "numeric_free_basis"):
            if k in other_entry:
                om[k] = other_entry[k]
        doc["other_marks"] = om
    return canon_json(doc)


def row_sha256(row, other_entry=None):
    return sha256_text(canonical_row(row, other_entry))


def split_sha256(split):
    return sha256_text(canon_json(split)) if isinstance(split, dict) else None


def row_shas(policy, other_marks=None):
    """{rule_id: {"rule_row_sha256", "split_sha256"}} for every row (the prebuild-sim-rows.py output)."""
    idx = other_marks_index(other_marks)
    out = {}
    for _rs, row in policy_rows(policy):
        entry = idx.get(row["rule_id"]) if is_other_mark(row) else None
        split = effective_split(row, entry)
        out[row["rule_id"]] = {"rule_row_sha256": row_sha256(row, entry), "split_sha256": split_sha256(split)}
    return out


def action_state(action):
    """The end state a rule's action leads to, from its leading verb (case-insensitive, after stripping): escalate ->
    escalated; recommend -> recommended; draft or stage -> staged; route -> routed; write or enter -> written; hand off
    or handoff -> handed_off; no route, or any other action, -> no-route."""
    a = (action or "").strip().lower()
    if a.startswith("escalate"):
        return "escalated"
    if a.startswith("recommend"):
        return "recommended"
    if a.startswith("draft") or a.startswith("stage"):
        return "staged"
    if a.startswith("route"):
        return "routed"
    if a.startswith("write") or a.startswith("enter"):
        return "written"
    if a.startswith("hand off") or a.startswith("handoff"):
        return "handed_off"
    if a.startswith("no route"):
        return "no-route"
    return "no-route"


# Severity of each end state, most severe highest; end_state_of() keeps the most severe state among the rules that held.
STATE_RANK = {"escalated": 7, "needs_more_information": 6, "written": 5, "handed_off": 4, "staged": 3,
              "recommended": 2, "routed": 1, "no-route": 0}


# ---- the rules code (plan 5.1) -------------------------------------------------------------------

STDLIB_IMPORTS = {"decimal", "re", "datetime", "math", "fractions"}


def load_source_module(path, name, src=None):
    """Load a Python file as a fresh module from its source, never from a bytecode cache (a generated file edited
    within the same second at the same size must never run as its earlier bytes). SRC, when given, is the source
    already read (through a pin) and is what runs."""
    import types
    if src is None:
        with open(path, encoding="utf-8") as f:
            src = f.read()
    mod = types.ModuleType(name)
    mod.__file__ = path
    sys.modules[name] = mod
    try:
        exec(compile(src, path, "exec"), mod.__dict__)
    finally:
        sys.modules.pop(name, None)
    return mod


def load_rules_modules(run_root, paths):
    """Load every generated rules-code module by file path under a private name; returns
    ({rule_id: entry}, {computed_id: entry}, {rule_id: split entry}, {module_path: module}). An entry of PATHS that
    is a {path, sha256} pin (a manifest's rules.code_modules) is read through the pin (read_pinned) and runs only as
    the pinned bytes; a plain path (the runner's run config, before any manifest exists) is read as it is."""
    rules, computed, splits, mods = {}, {}, {}, {}
    for entry_path in paths:
        src = None
        if isinstance(entry_path, dict):
            src = read_pinned(run_root, entry_path, "text", "rules code")
            rel = entry_path["path"]
        else:
            rel = entry_path
        full = os.path.join(run_root, rel)
        name = "_pbs_rules_" + hashlib.sha256(os.path.abspath(full).encode()).hexdigest()[:16]
        try:
            mod = load_source_module(full, name, src)
        except Exception as exc:  # noqa: BLE001 - generated code that cannot load is a named failure
            raise PrebuildError("rules code %s does not load: %s: %s" % (rel, type(exc).__name__, exc))
        mods[rel] = mod
        for table, into in (("RULES", rules), ("COMPUTED", computed), ("SPLITS", splits)):
            for key, entry in (getattr(mod, table, None) or {}).items():
                if key in into:
                    raise PrebuildError("%s %r is defined twice (again in %s)" % (table, key, rel))
                e = dict(entry)
                e["_module"] = rel
                into[key] = e
    return rules, computed, splits, mods


# ---- walk derivation (plan 5.1, "Step by step") ----------------------------------------------------

import re as _re  # noqa: E402

HANDOFF_RE = _re.compile(r"\b(I[HCRW]-[A-Z0-9-]*\d)\b")


def derive_walk(graph, policy):
    """walk/v1 from the internal map and the policy. One unit per rule set owner, in policy order (the dispatch
    set first); each unit's rules in policy order; exits from the set's terminal handoff, chained to a later unit
    that reads that handoff's tokens, else a person; the dispatch unit's exits follow its guards."""
    rsets = [rs for rs in (policy or {}).get("rule_sets") or [] if isinstance(rs, dict)]
    guards = ((policy or {}).get("dispatch") or {}).get("guards") or []
    guard_ids = {g.get("rule_id") for g in guards}
    dispatch_owner = next((rs.get("owner") for rs in rsets
                           if any(r.get("rule_id") in guard_ids for r in rs.get("rules") or [])), None)
    ordered = ([rs for rs in rsets if rs.get("owner") == dispatch_owner]
               + [rs for rs in rsets if rs.get("owner") != dispatch_owner])
    if not ordered:
        subs = [s.get("id") for s in (graph or {}).get("sub_agents") or [] if isinstance(s, dict)] or ["script"]
        return {"schema": "walk/v1", "entry": {"start": subs[0], "per_case_signal": None, "routes": {}},
                "units": [{"id": s, "order": [], "gates": [], "exits": {}, "exit_when": {}} for s in subs]}
    step_owner = {}
    for rs in ordered:
        for a in rs.get("anchor_steps") or []:
            step_owner.setdefault(a, rs.get("owner"))
    gates_by_owner = {}
    for g in (policy or {}).get("gates") or []:
        gates_by_owner.setdefault(g.get("owner"), []).append(g.get("gate_id"))
    units = []
    for i, rs in enumerate(ordered):
        owner = rs.get("owner")
        exits, when = {}, {}
        if owner == dispatch_owner and guards:
            for g in guards:
                target = step_owner.get(g.get("routes_to")) or g.get("owner") or "end"
                if target == owner:
                    continue   # a guard routing into its own set continues inside the same unit
                key = "route:%s" % g.get("rule_id")
                exits[key] = target
                when[key] = g.get("rule_id")
        if not exits:
            handoffs = HANDOFF_RE.findall(str(rs.get("terminal_handoff") or ""))
            if not handoffs:
                # an unfamiliar handoff name: follow policy order to the next unit (or end after the last)
                key = str(rs.get("terminal_handoff") or "end")
                nxt = next((later.get("owner") for later in ordered[i + 1:] if later.get("owner") != dispatch_owner),
                           None)
                exits[key] = nxt or "end"
            for h in handoffs:
                nxt = next((later.get("owner") for later in ordered[i + 1:]
                            if later.get("owner") != dispatch_owner
                            and any(str(t).startswith(h + ".") for r in later.get("rules") or []
                                    for t in r.get("operative_tokens") or [])), None)
                exits[h] = nxt or ("end" if len(ordered) == 1 else "person")
                if len(handoffs) > 1:
                    carrier = next((r.get("rule_id") for r in rs.get("rules") or []
                                    if any(str(t).startswith(h + ".") for t in r.get("operative_tokens") or [])
                                    and action_state(r.get("action")) in ("staged", "recommended", "escalated")),
                                   None)
                    if carrier:
                        when[h] = carrier
        units.append({"id": owner, "order": [r.get("rule_id") for r in rs.get("rules") or [] if r.get("rule_id")],
                      "gates": sorted(g for g in gates_by_owner.get(owner, []) if g),
                      "exits": exits, "exit_when": when})
    return {"schema": "walk/v1", "entry": {"start": units[0]["id"], "per_case_signal": None, "routes": {}},
            "units": units}


# ---- gate outcomes, rule results and end states from recorded steps -------------------------------

def rule_results(row):
    """{rule_id: (holds, why, provisional)} from a results.jsonl row: the last recorded step of each rule that
    carries a decision (an intermediate identify or compute step of a split rule records holds null), across units
    (a later unit's isolated re-run of a rule wins)."""
    out = {}
    for unit in row.get("units") or []:
        for step in unit.get("steps") or []:
            prov = list(step.get("provisional") or [])
            rid = step.get("rule_id")
            if step.get("holds") is None:
                if rid in out:
                    out[rid] = (out[rid][0], out[rid][1], sorted(set(out[rid][2]) | set(prov)))
                continue
            prior = out.get(rid, (None, None, []))[2]
            out[rid] = (step.get("holds"), step.get("why"), sorted(set(prior) | set(prov)))
    return out


def gate_outcome(gate, results, rows):
    """The generic gate logic, as code: needs_more_information when any implementing rule did not decide; else
    escalated when an implementing escalate rule held; else the state of the first implementing rule that held;
    else no-route. None when no implementing rule was evaluated in the case."""
    impl = [r for r in gate.get("implemented_by") or [] if r in results]
    if not impl:
        return None
    vals = [results[r][0] for r in impl]
    if any(v not in (True, False) for v in vals):
        return "needs_more_information"
    held = [r for r in impl if results[r][0] is True]
    if any(action_state((rows.get(r) or {}).get("action")) == "escalated" for r in held):
        return "escalated"
    if held:
        return action_state((rows.get(held[0]) or {}).get("action"))
    return "no-route"


def end_state_of(results, rows):
    """The case's end state from the rules that held (most severe first); needs_more_information when none held and
    at least one rule could not decide; no-route when nothing held."""
    held = [action_state((rows.get(r) or {}).get("action")) for r, v in results.items() if v[0] is True]
    if held:
        return max(held, key=lambda s: STATE_RANK[s])
    if any(v[0] not in (True, False) for v in results.values()):
        return "needs_more_information"
    return "no-route"


# ---- comparison (plan 5.4) ------------------------------------------------------------------------

def eval_cases(eval_set):
    """[(gate_id, case)] over every declared gate section, in declaration order."""
    out = []
    for g in ((eval_set or {}).get("meta") or {}).get("gates") or []:
        for c in ((eval_set or {}).get(g.get("id")) or {}).get("cases") or []:
            if isinstance(c, dict) and c.get("id"):
                out.append((g.get("id"), c))
    return out


def _norm_value(v):
    from decimal import Decimal, InvalidOperation
    s = str(v).strip()
    try:
        return ("d", Decimal(s.replace(",", "")))
    except (InvalidOperation, ValueError):
        return ("s", s)


def token_value_of(row, token):
    for unit in row.get("units") or []:
        for step in unit.get("steps") or []:
            for rd in step.get("reads") or []:
                if rd.get("token") == token:
                    return True, rd.get("value")
    return False, None


def expected_outcome_of(case, derived=None):
    """(expected outcome, source) for a Test Suite CASE: its own structured expected_outcome ("eval-set"), or where
    it has none the derived expectation for its id in DERIVED ({case_id: outcome}, the cases of
    expected-outcomes.derived.yaml; "derived-at-stage-11", shown as "derived expectation, not confirmed"), else
    (None, "none"). The one rule every reader of a case's expected outcome uses: the runner, the comparison, the
    page and the data-boundary check."""
    expected = case.get("expected_outcome")
    if expected is None and derived:
        found = derived.get(case.get("id"))
        if found:
            return found, "derived-at-stage-11"
        return None, "none"
    return expected, "eval-set" if expected else "none"


def derived_outcomes(run_root, manifest):
    """{case_id: derived expected outcome} read through the manifest's pin data.derived_expected_outcomes ({} when
    the pin is null or absent). A reader of a submitted manifest reads the file through this pin, never by its
    standard path."""
    pin = (manifest.get("data") or {}).get("derived_expected_outcomes")
    if not isinstance(pin, dict) or not pin.get("path"):
        return {}
    # a pinned read: used only when the file still has the sha256 the manifest pins (PinnedReadError otherwise)
    return (read_pinned(run_root, pin, "yaml", "data.derived_expected_outcomes") or {}).get("cases") or {}


def compare_case(case, row, rows, derived=None):
    """One case's comparison record (deterministic). ROW is its results.jsonl row or None."""
    cid = case.get("id")
    expected, source = expected_outcome_of(case, derived)
    rec = {"case_id": cid, "required": bool(case.get("required")), "expectation_source": source,
           "items": [], "critical": False}
    if row is None:
        rec.update(result="not-run", reason="the case was not run in this round")
        return rec
    if row.get("not_run_reason"):
        rec.update(result="not-run", reason=row["not_run_reason"])
        return rec
    if not expected:
        rec.update(result="not-comparable", reason="no expected outcome yet")
        return rec
    results = rule_results(row)
    undecided = sorted(r for r, v in results.items() if v[0] not in (True, False))
    items = []

    def item(name, exp, act, status, reason=None):
        entry = {"item": name, "expected": exp, "actual": act, "status": status}
        if reason:
            entry["reason"] = reason
        items.append(entry)

    end = row.get("end_state")
    # An undecided rule blocks the end state only when it could have changed it: nothing held at all, or the rule's
    # own action is more severe than the end state the decided rules reached.
    blocking = [r for r in undecided if end == "needs_more_information"
                or STATE_RANK[action_state((rows.get(r) or {}).get("action"))] > STATE_RANK.get(end, 0)]
    for key in ("end_state", "at"):
        exp, act = expected.get(key), row.get(key)
        if blocking:
            item(key, exp, act, "not-comparable", "depends on rules that did not decide: %s" % ", ".join(blocking))
        else:
            item(key, exp, act, "agree" if exp == act else "differ")
    for key, want in (("rules_fire", True), ("rules_do_not_fire", False)):
        for rid in expected.get(key) or []:
            got = results.get(rid)
            if got is None:
                item("%s %s" % (key, rid), want, None, "not-comparable", "the rule was not reached on this case")
            elif got[0] not in (True, False):
                item("%s %s" % (key, rid), want, got[0], "not-comparable", str(got[1] or ""))
            else:
                item("%s %s" % (key, rid), want, got[0], "agree" if got[0] is want else "differ")
    for tok, exp in sorted((expected.get("tokens") or {}).items()):
        present, val = token_value_of(row, tok)
        if exp == "present":
            item("token %s" % tok, "present", "present" if present else "absent",
                 "agree" if present else "differ")
        elif exp == "absent":
            item("token %s" % tok, "absent", "present" if present else "absent",
                 "differ" if present else "agree")
        else:
            act = val if present else None
            same = present and _norm_value(exp) == _norm_value(act)
            item("token %s" % tok, str(exp), None if act is None else str(act), "agree" if same else "differ")
    rec["items"] = items
    differ = [i for i in items if i["status"] == "differ"]
    notc = [i for i in items if i["status"] == "not-comparable"]
    if differ:
        rec["result"] = "mismatch"
        rec["reason"] = "differs on: %s" % ", ".join(i["item"] for i in differ)
    elif notc:
        rec["result"] = "not-comparable"
        rec["reason"] = "; ".join(sorted({i.get("reason", "") for i in notc}))
    else:
        rec["result"] = "match"
    if rec["required"] and row.get("end_state") != "escalated" and not blocking:
        rec["critical"] = True
        rec["result"] = "mismatch"
        rec["reason"] = ("a must-escalate case did not end escalated"
                         + ("; " + rec["reason"] if rec.get("reason") and rec["reason"] != "" else ""))
    return rec


def derive_comparison(run_id, eval_set, results_rows, policy, derived=None):
    rows = rows_by_id(policy)
    by_case = {r.get("case_id"): r for r in results_rows if r.get("sample_id") is None}
    cases = []
    ordered = sorted(eval_cases(eval_set), key=lambda gc: (not gc[1].get("required"), gc[1].get("id")))
    for _gid, case in ordered:
        cases.append(compare_case(case, by_case.get(case.get("id")), rows, derived))
    for r in sorted((r for r in results_rows if r.get("sample_id") is not None), key=lambda r: r.get("case_id")):
        cases.append({"case_id": r.get("case_id"), "sample_id": r.get("sample_id"), "required": False,
                      "expectation_source": "none", "items": [], "critical": False,
                      "result": "not-comparable", "reason": "no expected outcome yet"})
    counts = {}
    for c in cases:
        counts[c["result"]] = counts.get(c["result"], 0) + 1
    return {"schema": "prebuild-comparison/v1", "run_id": run_id, "cases": cases, "counts": counts,
            "critical_mismatches": sorted(c["case_id"] for c in cases if c.get("critical"))}


# ---- coverage (plan 5.5) --------------------------------------------------------------------------

def system_of_tokens(data_shapes):
    """{token: (system_id, state)} from every data-shape record."""
    out = {}
    for ds in data_shapes:
        for tm in ds.get("token_map") or []:
            if isinstance(tm, dict) and tm.get("token"):
                out[tm["token"]] = (ds.get("system_id"), ds.get("state"))
    return out


def weakest_basis(bases):
    bases = [b for b in bases if b in BASIS_ORDER]
    return min(bases, key=BASIS_ORDER.index) if bases else "assumed"


def rule_reads(row, computed_reads, other_entry=None):
    """Every token a rule's coverage item rests on: its operative tokens, what its computed inputs read, and, for a
    split judgment, what the identify step reads and what the split's compute ids read. The split is the effective
    one (plan 5.1): the row's own for a code or judgment rule, the other-marks.yaml entry's for an other-marked rule
    handled as judgment (an other-marked rule handled code or not-run has no split step to read)."""
    toks = list(row.get("operative_tokens") or [])
    for cid in row.get("computed_inputs") or []:
        toks.extend(computed_reads.get(cid) or [])
    split = None
    if not is_other_mark(row) or effective_handling(row, other_entry) == "judgment":
        split = effective_split(row, other_entry)
    split = split if isinstance(split, dict) else {}
    toks.extend((split.get("identify") or {}).get("reads") or [])
    for cid in split.get("compute") or []:
        toks.extend(computed_reads.get(cid) or [])
    return sorted(set(toks))


def not_checked_reason(rid, row, handling, policy, uncovered, results_for_rule):
    if rid in uncovered:
        return "the rule itself is still an open question (no test case yet by the Test Suite's own ratified reason)"
    if handling == "not-run":
        who = row.get("runs_as")
        note = (row.get("runs_as_note") or "").strip()
        return "it is decided by %s%s, which this check does not run" % (who, (": " + note) if note else "")
    whys = [str(w or "") for h, w in results_for_rule if h not in (True, False)]
    if any("changed between runs" in w or "either way" in w for w in whys):
        return "its result changed between runs"
    if any(NO_MODEL_REASON in w or "judgment" in w.lower() and "not run" in w.lower() for w in whys):
        return "its judgment step was not run"
    if rule_open_of(row, policy) or any("open" in w for w in whys):
        return "the rule itself is still an open question"
    if whys:
        return "it could not decide on any case: %s" % whys[0]
    return "no test case yet"


def derive_coverage(run_id, policy, eval_set, results_rows, data_shapes, parameters, other_marks, computed_reads):
    rows = rows_by_id(policy)
    idx = other_marks_index(other_marks)
    tok_sys = system_of_tokens(data_shapes)
    uncovered = {u.get("rule_id") for u in (((eval_set or {}).get("meta") or {}).get("uncovered_open_rules") or {})
                 .get("rules") or [] if isinstance(u, dict)}
    per_rule = {}
    prov = {}
    for r in results_rows:
        for rid, (holds, why, provisional) in rule_results(r).items():
            per_rule.setdefault(rid, []).append((holds, why, r.get("case_id")))
            prov.setdefault(rid, set()).update(provisional)
    items = []
    for _rs, row in policy_rows(policy):
        rid = row["rule_id"]
        entry = idx.get(rid) if is_other_mark(row) else None
        handling = effective_handling(row, entry)
        results = per_rule.get(rid, [])
        exercised = sorted({cid for h, _w, cid in results if h in (True, False)})
        reads = rule_reads(row, computed_reads, entry)
        systems = sorted({tok_sys[t][0] for t in reads if t in tok_sys})
        basis = weakest_basis([STATE_BASIS.get(tok_sys[t][1]) for t in reads if t in tok_sys])
        item = {"kind": "rule", "id": rid, "exercised_by": exercised, "data_basis": basis, "systems": systems,
                "provisional": sorted(prov.get(rid, set())), "handling": handling}
        if exercised:
            item["status"] = "checked"
        else:
            item["status"] = "not-checked-yet"
            item["reason"] = not_checked_reason(rid, row, handling, policy, uncovered,
                                                [(h, w) for h, w, _c in results])
        items.append(item)
    # gates
    for g in (policy or {}).get("gates") or []:
        ex = []
        for r in results_rows:
            if gate_outcome(g, rule_results(r), rows) is not None:
                ex.append(r.get("case_id"))
        impl_basis = [i["data_basis"] for i in items if i["id"] in (g.get("implemented_by") or [])]
        item = {"kind": "gate", "id": g.get("gate_id"), "exercised_by": sorted(set(ex)),
                "data_basis": weakest_basis(impl_basis), "provisional": []}
        item["status"] = "checked" if ex else "not-checked-yet"
        if not ex:
            item["reason"] = "no case reached the rules that implement it"
        items.append(item)
    # escalation triggers: exercised when a case reached a state where a carrying rule held
    for n, t in enumerate((policy or {}).get("escalation_triggers") or [], 1):
        carriers = t.get("carried_by") or []
        ex = sorted({r.get("case_id") for r in results_rows
                     if any(rule_results(r).get(c, (None,))[0] is True for c in carriers)})
        tid = t.get("id") or "trigger-%d" % n
        cbasis = [i["data_basis"] for i in items if i["id"] in carriers]
        item = {"kind": "trigger", "id": tid, "name": t.get("trigger"), "exercised_by": ex,
                "data_basis": weakest_basis(cbasis), "provisional": []}
        item["status"] = "checked" if ex else "not-checked-yet"
        if not ex:
            if any(c in uncovered for c in carriers):
                item["reason"] = "the rule itself is still an open question (no test case yet by the Test Suite's own ratified reason)"
            else:
                item["reason"] = "no case reached a state where its carrying rule held"
        items.append(item)
    for g in ((policy or {}).get("dispatch") or {}).get("guards") or []:
        rid = g.get("rule_id")
        ex = sorted({r.get("case_id") for r in results_rows if rule_results(r).get(rid, (None,))[0] in (True, False)})
        item = {"kind": "dispatch", "id": rid, "exercised_by": ex,
                "data_basis": next((i["data_basis"] for i in items if i["id"] == rid), "assumed"), "provisional": []}
        item["status"] = "checked" if ex else "not-checked-yet"
        if not ex:
            item["reason"] = "no case reached the dispatch guard"
        items.append(item)
    totals = {}
    for kind in ("rule", "gate", "trigger", "dispatch"):
        ks = [i for i in items if i["kind"] == kind]
        totals[kind] = {"total": len(ks), "checked": sum(1 for i in ks if i["status"] == "checked"),
                        "real_shaped": sum(1 for i in ks if i["status"] == "checked"
                                           and i["data_basis"] in ("client-sample", "real-layout")),
                        "assumed": sum(1 for i in ks if i["status"] == "checked" and i["data_basis"] == "assumed")}
    values = [{"open_id": p.get("open_id"), "parameter": p.get("parameter"),
               "provisional_value": str(p.get("provisional_value"))}
              for p in (parameters or {}).get("parameters") or [] if isinstance(p, dict)]
    return {"schema": "prebuild-coverage/v1", "run_id": run_id, "items": items, "totals": totals,
            "provisional_values": sorted(values, key=lambda v: str(v["open_id"]))}


# ---- the coverage statement (plan 6) --------------------------------------------------------------

BANNED_CLAIMS = ("the agent works", "agent is ready", "is production-ready", "production ready", "production-ready",
                 "passed", "ready for production", "the agent passed")
COVERAGE_BLOCK_RE = _re.compile(r"<!--\s*COVERAGE\s*(.*?)-->", _re.S)


def coverage_statement(coverage, adapter_risks, defects, agent_name, design_set_sha, date_text, fast_mode=False,
                       samples_requested=True):
    t = coverage["totals"]
    items = coverage["items"]
    rule_items = [i for i in items if i["kind"] == "rule"]
    own = [i for i in rule_items if i["status"] == "checked" and i["data_basis"] == "client-sample"]
    layout = [i for i in rule_items if i["status"] == "checked" and i["data_basis"] == "real-layout"]
    assumed = [i for i in rule_items if i["status"] == "checked" and i["data_basis"] == "assumed"]
    notc = [i for i in rule_items if i["status"] != "checked"]
    lines = ["# What we checked before building the agent", "",
             "%s, %s, design version %s." % (agent_name, date_text, (design_set_sha or "")[:12]), ""]
    if not samples_requested:
        lines += ["Samples were not requested in this run.", ""]
    if not rule_items:
        lines += ["The design has no decision rules to check.", ""]
    lines += ["## Checked on real-shaped data",
              "%d of %d rules, %d of %d gates and %d of %d escalation triggers ran on data from your systems, or on "
              "invented data built to your systems' real layouts." % (
                  t["rule"]["real_shaped"], t["rule"]["total"], t["gate"]["real_shaped"], t["gate"]["total"],
                  t["trigger"]["real_shaped"], t["trigger"]["total"]),
              "- On your own samples: %s" % (", ".join("%s (%d cases)" % (i["id"], len(i["exercised_by"]))
                                                       for i in own) or "none"),
              "- On invented data shaped like your real exports: %s" % (
                  ", ".join("%s (%d cases)" % (i["id"], len(i["exercised_by"])) for i in layout) or "none"),
              "", "## Checked on assumed data shapes"]
    risks = (adapter_risks or {}).get("risks") or []
    # only the systems whose real layout is unknown (each carries an adapter risk), never a system these rules also
    # read whose layout is known
    unseen = {r.get("system_id") for r in risks if isinstance(r, dict)}
    systems = sorted({s for i in assumed for s in i.get("systems") or [] if s in unseen})
    lines.append("%d rules ran only on data shaped like the design expects, because we have not seen the real layout "
                 "of %s. These may need changes when real data arrives:" % (
                     len(assumed), ", ".join(systems) or "any system"))
    lines += ["- %s" % " ".join(str(r.get("what", "")).split()) for r in risks] or ["- no adapter risks recorded"]
    lines += ["", "## Not checked yet",
              "%d rules could not be checked:" % len(notc)]
    lines += ["- %s: %s" % (i["id"], i.get("reason", "")) for i in notc] or ["- none"]
    lines += ["", "## Values used for testing only"]
    lines += ["- %s (%s): %s - not your value; please confirm" % (v["open_id"], v.get("parameter") or "a value",
                                                                  v["provisional_value"])
              for v in coverage.get("provisional_values") or []] or ["- none"]
    lines += ["", "## What we found and fixed"]
    ds = (defects or {}).get("defects") or []
    fixed = [d for d in ds if d.get("status") == "fixed"]
    still = [d for d in ds if d.get("status") not in ("fixed",)]
    lines += ["- %s (%s): %s; fixed by %s" % (d.get("id"), d.get("class"), " ".join(str(d.get("proposed_correction") or
                                                                                    "").split()), d.get("owner_stage"))
              for d in fixed] or ["- nothing fixed yet"]
    lines.append("Still open: %s." % (", ".join("%s (%s, owned by %s)" % (d.get("id"), d.get("class"),
                                                                          d.get("owner_stage") or "to be classified")
                                                for d in still) or "none"))
    lines += ["", "<!-- COVERAGE", dump_yaml(coverage).rstrip("\n"), "-->", ""]
    return "\n".join(lines)


def statement_block(text):
    m = COVERAGE_BLOCK_RE.search(text or "")
    if not m:
        return None
    return yaml_mod().safe_load(m.group(1))


def banned_claims_in(text):
    low = (text or "").lower()
    body = COVERAGE_BLOCK_RE.sub("", low)
    return [b for b in BANNED_CLAIMS if _re.search(r"(?<![\w-])%s(?![\w-])" % _re.escape(b), body)]


# ---- defects (plan 5.6) ---------------------------------------------------------------------------

DEFECT_CLASSES = ("code", "rule", "case", "mismatch", "coverage", "shape", "mark", "dispute")
DEFECT_STATUSES = ("open", "routed", "fixed", "escalated", "accepted")
OWNER_OF_CLASS = {"code": STAGE, "rule": "deepen-decision-rules", "case": "eval", "coverage": "eval",
                  "shape": "deepen-decision-rules", "mark": "deepen-decision-rules"}


def slug(text):
    return _re.sub(r"[^A-Za-z0-9]+", "-", str(text)).strip("-").lower()[:60]


def derived_defects(run_id, comparison, coverage, data_shapes, policy, runner_defects):
    """The defects a round raises by itself, each with a stable id (dispute defects come only from reviews)."""
    out = []
    for d in runner_defects or []:
        out.append(dict(d))
    for c in comparison.get("cases") or []:
        if c.get("result") == "mismatch":
            out.append({"id": "mismatch-%s" % slug(c["case_id"]), "class": "mismatch",
                        "evidence": {"cases": [c["case_id"]], "rules": sorted({i["item"].split(" ", 1)[1]
                                                                               for i in c.get("items") or []
                                                                               if i["status"] == "differ"
                                                                               and i["item"].startswith("rules_")}),
                                     "runs": [run_id]},
                        "owner_stage": "eval",
                        "proposed_correction": "apply the precedence rule: if the policy text is unambiguous the case "
                                               "is wrong (Test Suite); if ambiguous the rule is wrong (decision rules); "
                                               "if a person cannot tell, escalate. %s" % c.get("reason", "")})
    for i in coverage.get("items") or []:
        if i["kind"] in ("rule", "gate", "trigger", "dispatch") and i["status"] != "checked" \
                and (i.get("reason") or "") in ("no test case yet", "no case reached the rules that implement it",
                                                "no case reached a state where its carrying rule held",
                                                "no case reached the dispatch guard"):
            out.append({"id": "coverage-%s-%s" % (i["kind"], slug(i["id"])), "class": "coverage",
                        "evidence": {"cases": [], "rules": [i["id"]] if i["kind"] == "rule" else [], "runs": [run_id]},
                        "owner_stage": "eval",
                        "proposed_correction": "write a Test Suite case that exercises %s %s" % (i["kind"], i["id"])})
    rows = policy_rows(policy)
    for ds in data_shapes:
        if ds.get("state") not in ("real-sample", "real-layout"):
            continue
        for tm in ds.get("token_map") or []:
            frm = tm.get("from") or {}
            if frm.get("absent"):
                readers = sorted(r["rule_id"] for _rs, r in rows if tm.get("token") in (r.get("operative_tokens") or []))
                if readers:
                    out.append({"id": "shape-%s" % slug(tm["token"]), "class": "shape",
                                "evidence": {"cases": [], "rules": readers, "runs": [run_id]},
                                "owner_stage": "deepen-decision-rules",
                                "proposed_correction": "the real layout of %s has no source for %s (%s); change the "
                                                       "rules that read it, or give the interface a real source for it "
                                                       "(interface design)" % (ds.get("system_id"), tm["token"],
                                                                               frm.get("reason", ""))})
    for d in out:
        d.setdefault("status", "open")
        d.setdefault("raised_by", "run")
        d.setdefault("routed_via", None)
    dedup = {}
    for d in out:
        dedup.setdefault(d["id"], d)
    return [dedup[k] for k in sorted(dedup)]


def merge_defects(existing, derived, run_id):
    """Fold a round's derived defects into defects.yaml: new ones open with a history entry; a derived defect that
    this round did not raise again is marked fixed; review-raised and person-edited entries are kept."""
    dup = duplicate_defect_ids(existing)
    if dup:
        # a person's entry is never silently dropped by the merge's dict keyed by id
        raise PrebuildError("; ".join(dup))
    current = {d["id"]: dict(d) for d in (existing or {}).get("defects") or [] if isinstance(d, dict) and d.get("id")}
    raised = {d["id"] for d in derived}
    for d in derived:
        if d["id"] not in current:
            d = dict(d)
            d["history"] = [{"run_id": run_id, "status": "open", "note": "raised by run %s" % run_id}]
            current[d["id"]] = d
        elif current[d["id"]].get("status") == "fixed":
            cur = current[d["id"]]
            cur["status"] = "open"
            cur.setdefault("history", []).append({"run_id": run_id, "status": "open", "note": "raised again"})
    for did, d in current.items():
        if d.get("raised_by") == "run" and did not in raised and d.get("status") in ("open", "routed"):
            d["status"] = "fixed"
            d.setdefault("history", []).append({"run_id": run_id, "status": "fixed",
                                                "note": "not raised again by run %s" % run_id})
    return {"schema": "prebuild-defects/v1", "defects": [current[k] for k in sorted(current)]}


# ---- the manifest ---------------------------------------------------------------------------------

MANIFEST_KEYS = ("schema", "run_id", "design_set", "gate_submission_event_id", "inputs", "systems", "data", "rules",
                 "runs", "final_run_id", "coverage", "defects", "adapter_risks", "folded_reviews",
                 "review_open_item", "opens")
MANIFEST_EXTRA_ALLOWED = ("reference_use",)


def iter_pins(doc, where=""):
    """Every {path, sha256} pair anywhere in DOC, as (where, pair)."""
    if isinstance(doc, dict):
        if isinstance(doc.get("path"), str) and isinstance(doc.get("sha256"), str):
            yield where or "(root)", doc
        for k, v in doc.items():
            if k in ("path", "sha256"):
                continue
            yield from iter_pins(v, "%s.%s" % (where, k) if where else k)
    elif isinstance(doc, list):
        for i, v in enumerate(doc):
            yield from iter_pins(v, "%s[%d]" % (where, i))


def load_manifest(run_root, manifest_rel):
    doc = load_yaml(os.path.join(run_root, manifest_rel))
    if not isinstance(doc, dict):
        raise PrebuildError("%s is not a mapping" % manifest_rel)
    return doc


def load_manifest_with_sha(run_root, manifest_rel):
    """(manifest, sha256) of the manifest named on a command line, both from ONE read of its bytes, so the sha a
    check or the page records is the sha of exactly the content it used (never a second read of the path)."""
    full = os.path.join(run_root, manifest_rel)
    try:
        with open(full, "rb") as f:
            data = f.read()
    except OSError as exc:
        raise PrebuildError("cannot read %s: %s" % (manifest_rel, exc))
    try:
        doc = yaml_mod().safe_load(data.decode("utf-8"))
    except PrebuildError:
        raise
    except Exception as exc:  # noqa: BLE001 - a YAML parse error is a setup error
        raise PrebuildError("cannot parse %s: %s" % (manifest_rel, exc))
    if not isinstance(doc, dict):
        raise PrebuildError("%s is not a mapping" % manifest_rel)
    return doc, hashlib.sha256(data).hexdigest()


EVENT_LOG = "events.jsonl"
NO_EVENT_LOG_MSG = ("the run has no event log (%s is missing): %s learns what exists only from the log, so a missing "
                    "log is refused, never read as an empty one")


def events_of(run_root):
    """The run's events, or None when the run has no event log. A door that decides anything from the log calls
    require_events() instead, which refuses a missing log naming it."""
    path = os.path.join(run_root, EVENT_LOG)
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8") as f:
        return [json.loads(l) for l in f if l.strip()]


def require_events(run_root, who):
    """The run's events; PrebuildError naming events.jsonl when the run has none (WHO: the door asking, in plain
    words). A missing log is never read as an empty log."""
    events = events_of(run_root)
    if events is None:
        raise PrebuildError(NO_EVENT_LOG_MSG % (os.path.join(run_root, EVENT_LOG), who))
    return events


def final_run(manifest):
    return next((r for r in manifest.get("runs") or [] if r.get("run_id") == manifest.get("final_run_id")), None)


def results_rows(run_root, manifest):
    """The final run's results.jsonl rows, read through the manifest's pin (PinnedReadError when the file is not the
    one pinned)."""
    fr = final_run(manifest)
    if fr is None:
        raise PrebuildError("the manifest's final_run_id names no entry in runs[]")
    return read_pinned(run_root, fr.get("results"), "jsonl", "the final run's results")


def config_from_manifest(manifest, run_root):
    """A run config equivalent to the one the manifest was built from (for re-derivation by the checks). Its "pins"
    key carries the manifest's own {path, sha256} pairs, so scripts/prebuild-sim-compare.py derive_all reads every
    input through the manifest's pin (read_pinned), never by path alone."""
    inputs, rules = manifest.get("inputs") or {}, manifest.get("rules") or {}
    fr = final_run(manifest) or {}
    derived = (manifest.get("data") or {}).get("derived_expected_outcomes")
    return {"decision_policy": inputs["decision_policy"]["path"], "eval_set": inputs["eval_set"]["path"],
            "parameters": rules["parameters"]["path"],
            "other_marks": (rules.get("other_marks") or {}).get("path"),
            "rules_code": [m["path"] for m in rules.get("code_modules") or []],
            "data_shape_dir": OUT + "/data-shape",
            # through the manifest's pin, as every other pinned input (null or absent: no derived expectations)
            "derived_expected_outcomes": (derived or {}).get("path") if isinstance(derived, dict) else None,
            "pins": {"decision_policy": inputs["decision_policy"], "eval_set": inputs["eval_set"],
                     "parameters": rules["parameters"], "other_marks": rules.get("other_marks"),
                     "rules_code": list(rules.get("code_modules") or []),
                     "data_shapes": [s["data_shape"] for s in manifest.get("systems") or []
                                     if isinstance(s, dict) and isinstance(s.get("data_shape"), dict)],
                     "derived_expected_outcomes": derived if isinstance(derived, dict) else None,
                     "results": fr.get("results")}}


def data_shapes_of(run_root, manifest):
    """Every data-shape record the manifest's systems[] pin, each read through its pin."""
    return [read_pinned(run_root, s["data_shape"], "yaml", "the data-shape record of %s" % s.get("system_id")) or {}
            for s in manifest.get("systems") or [] if isinstance(s.get("data_shape"), dict)]


def read_interface(token):
    return str(token).split(".", 1)[0].startswith("IR")


# ==================================================================================================
# The business owner's review (plan 6.5, 6.6): the pins, the governing review, the review context, and the four
# written rule lists. Each list has ONE validator here, and every door a file or payload can arrive through calls
# it (scripts/prebuild-sim-review.py --check and --record, the writer's check on a human.decision with gate
# prebuild-simulation-review or provisional-values, the fold-in before the next submission, clearance, the page
# renderer, the structure check). Every door that reads a review record runs validate_record, and so the feedback
# rules on the feedback file it pins (recorded_context() rebuilds the recorder's context from the record's pins):
#   * validate_feedback            pre-build-simulation-stage/templates/prebuild-review-feedback.rules.md
#   * validate_record              the rule block at the head of prebuild-review-record.template.yaml
#   * validate_decision_payload    the rule block at the head of prebuild-review-decision.template.yaml
#   * validate_provisional_confirmation  the rule block at the head of provisional-values-confirmation.template.yaml
# Every finding is "<rule id>: <plain message>"; the rule ids are the ones the build report's rule tables list.
# The writer floor, the review recorder and the page renderer all take their pins from pinned_files() /
# pins_current() and their governing review from governing_review(), and read a submitted manifest only through
# submitted_manifest() (the manifest only while it is still the submitted bytes); no other code re-implements them.
# Every file any of them opens because a pin names it (a manifest pair, a design-set entry, a review record, the
# feedback file, a response file, the parameters a confirmation pins) is read through pinned_read() / read_pinned()
# and used only when it still has the pinned sha256; a submitted manifest is replaced only after next_round_act().
# Which review records, folded reviews and runs exist is read from the event log only (logged_reviews,
# pending_reviews, unlogged_folds, submitted_runs), never from a listing of a folder (see "what exists" below).

REVIEW_GATE = "prebuild-simulation-review"
PROVISIONAL_GATE = "provisional-values"
REVIEWS_DIR = OUT + "/reviews"
FEEDBACK_DIR = "inputs/prebuild-review"
DESIGN_SET = "output/coherence/design-set.json"
DECLARATION = "inputs/data-samples/declaration.yaml"
PARAMETERS = OUT + "/parameters.yaml"
FEEDBACK_SCHEMA = "prebuild-review-feedback/v1"
RECORD_SCHEMA = "prebuild-review-record/v1"
FEEDBACK_KEYS = ("schema", "source", "run_id", "design_set_sha256", "final_run_id", "manifest_sha256", "written_at",
                 "reviewer_role", "cases", "samples", "chat")
IDENTITY_PINS = ("run_id", "design_set_sha256", "final_run_id", "manifest_sha256")
CASE_KEYS = ("case_id", "verdict", "note")
SAMPLE_KEYS = ("sample_id", "system_id", "source", "permission", "redaction", "provided_by", "format", "file_name",
               "rows_text", "what_should_happen")
MAX_SAMPLE_BYTES = 2097152
RECORD_KEYS = ("schema", "sequence", "feedback_file", "source", "reviewer_role", "pins", "decision", "cases_accepted",
               "cases_disputed", "samples_added", "defects", "declaration_entries")
PIN_KEYS = IDENTITY_PINS + ("submission_event_id",)
SAMPLE_ADDED_KEYS = ("sample_id", "system_id", "path", "sha256")
DEFECT_ENTRY_KEYS = ("id", "class", "evidence", "owner_stage", "proposed_correction", "status", "raised_by",
                     "routed_via", "history", "from_review_record")
DECLARATION_ENTRY_KEYS = ("system_id", "provided", "files", "provided_by", "permission", "redaction", "notes",
                          "added_via", "source", "feedback_file", "from_review_record")
DECISION_KEYS = ("gate", "decision", "feedback_file", "source") + PIN_KEYS + (
    "review_record", "cases_accepted", "cases_disputed", "samples_added")
PROVISIONAL_KEYS = ("gate", "decision", "rationale", "parameters", "manifest_sha256", "confirmed", "changed")
WRITER_KEYS = ("writer_validation_duration_ms",)   # the writer adds its own timing key to every event
STALE_MSG = ("this feedback was written against an earlier version of the design or its results; open the current "
             "page and redo it")
MOVED_MSG = "the manifest on disk is not the one submitted; it changed after submission, so start a new round"
RECORD_RE = _re.compile(r"^review-(\d{3})\.yaml$")


def latest_submission(events, stage=STAGE):
    return next((e for e in reversed(events or []) if e.get("stage") == stage
                 and e.get("type") == "executor.submitted"), None)


# ---- the submitted manifest: its bytes only while they are still the submitted bytes ----------------------
# The next round writes its manifest at the same path as the submitted one, so "the manifest at the submission's
# artifact_path" is the submitted manifest only until that round replaces it. Every reader of a submitted manifest
# goes through submitted_manifest(), which hands back the manifest only when the file at the path still has the
# sha256 the executor.submitted recorded, and otherwise says plainly that it has been replaced (or is missing).
# No other code compares a submission's sha256 with the file on disk. No copy of a submitted manifest is kept.

NO_SUBMISSION_MSG = "stage 11 has no submitted manifest"
REPLACED_MSG = ("the submitted manifest %s has been replaced since it was submitted: the file at that path no longer "
                "hashes to the sha256 its executor.submitted %s recorded (the next round writes its own manifest at "
                "the same path)")
MISSING_SUBMITTED_MSG = ("the submitted manifest %s is missing: no file is at the path its executor.submitted %s "
                         "recorded")


class Submitted(_namedtuple("Submitted", "submission rel manifest state reason")):
    """SUBMISSION the executor.submitted event (None when there is none), REL its artifact_path, MANIFEST the parsed
    manifest only when STATE is "held"; STATE one of "none", "held", "replaced", "missing", "unreadable"; REASON the
    plain sentence for every state but "held"."""
    __slots__ = ()

    @property
    def held(self):
        return self.state == "held"


def submitted_manifest(run_root, events, submission=None):
    """The submitted manifest of SUBMISSION (default: stage 11's latest executor.submitted), read only while the
    file at its artifact_path still has the sha256 that event recorded. Returns a Submitted; its manifest is None
    unless state is "held"."""
    sub = submission if submission is not None else latest_submission(events)
    if sub is None:
        return Submitted(None, None, None, "none", NO_SUBMISSION_MSG)
    sp = sub.get("payload") or {}
    rel = sp.get("artifact_path")
    # the executor.submitted is the pin: {artifact_path, sha256}, read through the one pinned read
    r = pinned_read(run_root, {"path": rel, "sha256": sp.get("sha256")}, "yaml", "the submitted manifest")
    if r.state in ("missing", "no-pin"):
        return Submitted(sub, rel, None, "missing", MISSING_SUBMITTED_MSG % (rel, sub.get("event_id")))
    if r.state in ("moved", "no-sha"):
        return Submitted(sub, rel, None, "replaced", REPLACED_MSG % (rel, sub.get("event_id")))
    if not r.ok:
        return Submitted(sub, rel, None, "unreadable", "the submitted manifest cannot be read: %s" % r.reason)
    if not isinstance(r.content, dict):
        return Submitted(sub, rel, None, "unreadable", "the submitted manifest cannot be read: %s is not a mapping"
                         % rel)
    return Submitted(sub, rel, r.content, "held", None)


# ---- the next round's first act: when a submitted manifest may be replaced -----------------------------
# A submitted manifest is replaced only after a concrete next-round act, shown by something on disk or in the log:
#   * a run whose id the submitted manifest does not list and whose run files (results.jsonl, comparison.yaml,
#     cost.json) exist under output/pre-build-simulation/runs/<id>/;
#   * a file the submitted manifest pins whose sha256 has changed (pins_current);
#   * at least one unfolded review actually folded in (the caller's pending-fold count, when it folds).
# A request is never an act on its own: a differing, shorter or reordered run list, or a flag, proves nothing.
# next_round_act() is the one place that decides it; scripts/prebuild-sim-manifest.py calls it before writing.

RUN_FILES = ("results.jsonl", "comparison.yaml", "cost.json")
NO_ACT_MSG = ("%s is a submitted manifest; a submitted manifest is never written again (executor.submitted %s). The "
              "next round's manifest replaces it only after a concrete next-round act shown on disk or in the log: a "
              "run it does not list whose run files exist, a change to a file it pins, or --fold with a review record "
              "still to fold. A requested run list or a flag is never an act on its own; none has happened%s")


def next_round_act(run_root, events, pending_folds=None, manifest_rel=MANIFEST_PATH):
    """(submission, act, reason) for writing a manifest at MANIFEST_REL. SUBMISSION is the stage 11
    executor.submitted whose recorded sha256 the file at MANIFEST_REL still has (read through submitted_manifest),
    or None when that file is not a submitted manifest (then act and reason are None: nothing to protect). ACT is a
    plain sentence naming the concrete act that has happened, or None; REASON says why the file may not be replaced
    when ACT is None. PENDING_FOLDS, when given, is a callable returning the review records a fold would fold in
    now; it is asked only when no other act has happened, and a non-empty answer is an act."""
    held = None
    for sub in (e for e in reversed(events or []) if e.get("stage") == STAGE and e.get("type") == "executor.submitted"):
        cand = submitted_manifest(run_root, events, sub)
        if cand.state in ("held", "unreadable") and cand.rel == manifest_rel:
            held = cand
            break
    if held is None:
        return None, None, None
    sub = held.submission
    if not held.held:
        return sub, None, NO_ACT_MSG % (manifest_rel, sub.get("event_id"), " (%s)" % held.reason)
    listed = [r.get("run_id") for r in held.manifest.get("runs") or [] if isinstance(r, dict)]
    new_runs = next_round_runs(run_root, listed)
    if new_runs:
        return sub, "a new run whose run files exist and which the submitted manifest does not list (%s)" \
            % ", ".join(new_runs), None
    changed = pins_current(run_root, manifest_rel, held.manifest)
    if changed:
        return sub, "a file the submitted manifest pins changed (%s)" % ", ".join(changed), None
    if pending_folds is not None:
        folds = pending_folds()
        if folds:
            return sub, "the fold-in folds %d unfolded review record(s) in (%s)" % (
                len(folds), ", ".join(str(f[0]) if isinstance(f, (list, tuple)) else str(f) for f in folds)), None
        return sub, None, NO_ACT_MSG % (manifest_rel, sub.get("event_id"), "; --fold found no unfolded review")
    return sub, None, NO_ACT_MSG % (manifest_rel, sub.get("event_id"), "")


# ---- pins ---------------------------------------------------------------------------------------------

SentPrompt = _namedtuple("SentPrompt", "where pin case_id rule_id variant")
NO_FINAL_RUN_MSG = ("the manifest's final_run_id %r names no entry in runs[], so the files its final run's results "
                    "pin cannot be reached")


def _results_reached(run_root, manifest):
    """(responses, prompts) the final run's results.jsonl rows pin: RESPONSES [(where, {path, sha256})] for every
    judgment response file, PROMPTS [SentPrompt] for every prompt that was sent (a step's judgment.prompt; a prompt
    the lint refused or a --no-model run never sent has no pin). The results are read through their pin
    (results_rows): a results file that is missing, changed or cannot be parsed at its pinned sha raises
    PinnedReadError naming it, and a manifest with no final run raises PrebuildError; neither is ever read as "no
    pins"."""
    if final_run(manifest) is None:
        raise PrebuildError(NO_FINAL_RUN_MSG % (manifest.get("final_run_id"),))
    responses, prompts = [], []
    for r in results_rows(run_root, manifest):
        for unit in r.get("units") or []:
            for s in unit.get("steps") or []:
                j = s.get("judgment") if isinstance(s.get("judgment"), dict) else {}
                for x in j.get("responses") or []:
                    if isinstance(x, dict):
                        responses.append(("results %s %s slot %s" % (r.get("case_id"), s.get("rule_id"),
                                                                     x.get("slot")),
                                          {"path": x.get("path") or "", "sha256": x.get("sha256")}))
                pp = j.get("prompt")
                if isinstance(pp, dict):
                    prompts.append(SentPrompt("results %s %s %s prompt" % (r.get("case_id"), s.get("rule_id"),
                                                                           s.get("kind")),
                                              {"path": pp.get("path") or "", "sha256": pp.get("sha256")},
                                              r.get("case_id"), s.get("rule_id"), s.get("kind")))
    return responses, prompts


def response_pins(run_root, manifest):
    """(where, {path, sha256}) for every judgment response file the final run's results.jsonl rows pin. Raises
    (PinnedReadError naming the results file) when the results cannot be read through their pin; never []."""
    return _results_reached(run_root, manifest)[0]


def prompt_pins(run_root, manifest):
    """[SentPrompt] for every prompt the final run sent, each pinned by its results row (judgment.prompt). Raises
    like response_pins. An unsent prompt has no pin and is not here: the rule-trace check reaches it by the final
    run's judgments folder, and it can only add a lint finding."""
    return _results_reached(run_root, manifest)[1]


def pinned_files(run_root, manifest):
    """Every (where, {path, sha256}) the submitted manifest pins: each pair anywhere in it (iter_pins), plus every
    judgment response file and every sent prompt its final run's results pin. The one list the writer floor, the
    recorder's before/after re-hash, the renderer and the structure check all walk. Raises when the results cannot
    be read through their pin (the files they pin cannot be reached, so they are never taken to be none)."""
    responses, prompts = _results_reached(run_root, manifest)
    return list(iter_pins(manifest)) + responses + [(p.where, p.pin) for p in prompts]


def disk_sha(run_root, rel):
    full = os.path.join(run_root, rel or "")
    return sha256_file(full) if rel and os.path.isfile(full) else None


# ---- pinned reads: a file reached through a pin is used only when it matches the pin ----------------
# Any file stage 11 opens because a pinned record names it (a manifest's {path, sha256} pairs, a design-set entry,
# a review record, the feedback file, a response file, a cache entry, and anything else of that kind) is read
# through pinned_read(): the bytes are read once, hashed, compared with the pin's sha256, and parsed from those same
# bytes only when they match. On a mismatch, a missing file, a read error, or a pin that records no sha256, the
# caller gets a plain reason naming the file and refuses (or, at a read-only door, reports "none"); it never falls
# back to another source. _pin_bytes() is the one comparison of a pin with a file on disk; pinned_read(),
# read_pinned(), pin_reason(), pin_problems() and submitted_manifest() all go through it, and no other stage 11
# code compares a pin's sha256 with disk.

PIN_READ_KINDS = ("yaml", "json", "jsonl", "text", "bytes")
PIN_MOVED_MSG = ("%s %s no longer has the sha256 its pin records (pinned %s, now %s); a file reached through a pin "
                 "is used only when it matches the pin")
PIN_MISSING_MSG = "%s %s is missing: no file is at the path its pin records"
PIN_NO_SHA_MSG = "%s %s is pinned without a sha256, so nothing binds its bytes"
PIN_NO_PATH_MSG = "%s has no pin ({path, sha256}) to read it through"


class PinRead(_namedtuple("PinRead", "ok content reason state path")):
    """One pinned read: OK True only when the file is on disk with exactly the pin's sha256 and parsed; CONTENT the
    parsed content then (else None); REASON the plain sentence otherwise, naming the file; STATE one of "held",
    "no-pin", "no-sha", "missing", "unreadable", "moved", "unparsable"; PATH the pinned path."""
    __slots__ = ()


class PinnedReadError(PrebuildError):
    """A file reached through a pin does not match the pin (or cannot be read); the message names the file."""

    def __init__(self, reason, path=None, state=None):
        PrebuildError.__init__(self, reason)
        self.reason, self.path, self.state = reason, path, state


def _pin_bytes(run_root, pair, what):
    """(state, bytes or None, sha256 on disk or None, reason) for the file PAIR pins: the one comparison of a pin's
    sha256 with the file on disk."""
    label = what or "the pinned file"
    if not isinstance(pair, dict) or not isinstance(pair.get("path"), str) or not pair.get("path"):
        return "no-pin", None, None, PIN_NO_PATH_MSG % label
    rel, sha = pair["path"], pair.get("sha256")
    full = os.path.join(run_root, rel)
    if not os.path.isfile(full):
        return "missing", None, None, PIN_MISSING_MSG % (label, rel)
    try:
        with open(full, "rb") as f:
            data = f.read()
    except OSError as exc:
        return "unreadable", None, None, "%s %s cannot be read: %s" % (label, rel, exc)
    now = hashlib.sha256(data).hexdigest()
    if not isinstance(sha, str) or not sha:
        return "no-sha", None, now, PIN_NO_SHA_MSG % (label, rel)
    if now != sha:
        return "moved", None, now, PIN_MOVED_MSG % (label, rel, sha[:12], now[:12])
    return "held", data, now, None


def pinned_read(run_root, pair, kind="yaml", what=None):
    """Read the file PAIR ({path, sha256}) pins, as KIND (yaml, json, jsonl, text or bytes), only when it is on disk
    with exactly PAIR's sha256. Returns a PinRead; its content is parsed from the bytes that were hashed."""
    if kind not in PIN_READ_KINDS:
        raise ValueError("unknown pinned-read kind %r" % kind)
    rel = pair.get("path") if isinstance(pair, dict) else None
    state, data, _now, reason = _pin_bytes(run_root, pair, what)
    if state != "held":
        return PinRead(False, None, reason, state, rel)
    try:
        if kind == "bytes":
            content = data
        elif kind == "text":
            content = data.decode("utf-8", errors="replace")
        elif kind == "json":
            content = json.loads(data.decode("utf-8"))
        elif kind == "jsonl":
            content = [json.loads(l) for l in data.decode("utf-8").splitlines() if l.strip()]
        else:
            content = yaml_mod().safe_load(data.decode("utf-8"))
    except PrebuildError:
        raise
    except Exception as exc:  # noqa: BLE001 - a file at its pinned sha that does not parse is named, not a crash
        return PinRead(False, None, "%s %s is at its pinned sha256 but cannot be parsed: %s"
                       % (what or "the pinned file", rel, exc), "unparsable", rel)
    return PinRead(True, content, None, "held", rel)


def read_pinned(run_root, pair, kind="yaml", what=None):
    """The content of the file PAIR pins (pinned_read), or PinnedReadError with the reason naming the file."""
    r = pinned_read(run_root, pair, kind, what)
    if not r.ok:
        raise PinnedReadError(r.reason, r.path, r.state)
    return r.content


def pin_reason(run_root, pair, what=None):
    """None when the file PAIR pins is on disk with exactly PAIR's sha256; else the plain reason naming the file (for
    a site that needs the file to be the pinned one without reading its content)."""
    return _pin_bytes(run_root, pair, what)[3]


def pin_problems(run_root, manifest, responses=True):
    """[(where, path, pinned sha256, sha256 on disk or None)] for every pinned file that is missing or changed
    (RESPONSES=False: the manifest's own pairs only, as the structure check reports them). With RESPONSES, every
    pin the submission reaches is re-hashed: the manifest's own pairs, then every judgment response file and every
    sent prompt the final run's results pin. When the results file is itself missing or changed it is reported here
    (the files it pins cannot be reached through it, and that finding already refuses); when it is at its pinned
    sha but cannot be parsed, PinnedReadError is raised naming it (never read as "nothing pinned")."""
    out = []

    def rehash(pins):
        for where, pair in pins:
            state, _data, now, _reason = _pin_bytes(run_root, pair, where)
            if state != "held":
                out.append((where, pair.get("path"), pair.get("sha256"),
                            now if state in ("moved", "no-sha") else None))
    rehash(iter_pins(manifest))
    if responses:
        fr = final_run(manifest)
        results = (fr or {}).get("results") if isinstance(fr, dict) else None
        if not (isinstance(results, dict) and any(o[1] == results.get("path") for o in out)):
            reached, prompts = _results_reached(run_root, manifest)
            rehash(reached + [(p.where, p.pin) for p in prompts])
    return out


def pins_current(run_root, manifest_path, manifest=None):
    """Every file the manifest at MANIFEST_PATH pins (plan 6.6) re-hashed against disk: the run-relative paths
    that are missing or changed, each once, in pin order ([] when every pin holds). Every pin the submission reaches
    is walked (pin_problems): the manifest's own pairs, and every judgment response file and sent prompt its final
    run's results pin; results that cannot be parsed at their pinned sha raise PinnedReadError naming them. MANIFEST,
    when given, is that manifest already read through its own pin (submitted_manifest), so the pins walked are the
    submitted bytes'."""
    out = []
    doc = manifest if manifest is not None else load_manifest(run_root, manifest_path)
    for _where, path, _pinned, _now in pin_problems(run_root, doc):
        if path not in out:
            out.append(path)
    return out


# ---- the governing review ------------------------------------------------------------------------------

def review_decisions(events):
    return [e for e in events or [] if e.get("type") == "human.decision"
            and (e.get("payload") or {}).get("gate") == REVIEW_GATE]


def _record_whole(p, run_root, events):
    """(ok, why) for the review record a decision payload pins: on disk at its pinned sha, meeting every rule of
    its list, with the decision's pins."""
    rec = p.get("review_record") or {}
    rel = rec.get("path") if isinstance(rec, dict) else None
    r = pinned_read(run_root, rec, "yaml", "its review record")
    if not r.ok:
        return False, "its review record %s is not on disk at the pinned sha (%s)" % (rel, r.reason)
    doc = r.content
    problems = validate_record(doc, rel, run_root, events)
    if problems:
        return False, "its review record breaks its rules (%s)" % "; ".join(problems)
    if any((doc.get("pins") or {}).get(k) != p.get(k) for k in PIN_KEYS):
        return False, "its review record's pins are not the decision's"
    return True, None


def governing_review(events, run_root, terminal):
    """The business owner's review that governs stage 11's latest submission for TERMINAL (stage.cleared or
    stage.accepted_with_residual): {"kind": "accepted" | "folded-changes" | "open-item" | "none", "event",
    "reason"} (plan 6.6). The submitted manifest is read through submitted_manifest(): once the file at its path has
    been replaced (or is missing), the kind is "none" with that reason; a replaced file is never read as if it were
    the submitted one."""
    held = submitted_manifest(run_root, events)
    if not held.held:
        return {"kind": "none", "event": None, "reason": held.reason}
    try:
        # the latest decision, resolved through the one lookup every other reader uses (logged_reviews): a log that
        # pins one record path twice, at the same bytes or different ones, is refused there, never read as either
        governing = latest_logged_review(events)
    except SupersededPinError as exc:
        return {"kind": "none", "event": None, "reason": str(exc)}
    sub, sp, manifest = held.submission, held.submission.get("payload") or {}, held.manifest
    ds_sha = (manifest.get("design_set") or {}).get("sha256")
    latest = governing.event if governing is not None else None

    def pins_match(p):
        return (p.get("run_id") == manifest.get("run_id") and p.get("design_set_sha256") == ds_sha
                and p.get("final_run_id") == manifest.get("final_run_id"))

    if latest is not None:
        p = latest.get("payload") or {}
        if p.get("decision") == "accepted":
            if p.get("submission_event_id") != sub.get("event_id") or p.get("manifest_sha256") != sp.get("sha256"):
                return {"kind": "none", "event": latest,
                        "reason": "the business owner accepted an earlier submission; the results changed after "
                                  "they accepted them, so they need to review the current page"}
            if not pins_match(p):
                return {"kind": "none", "event": latest,
                        "reason": "the accepted review is pinned to another run, design set or final run than the "
                                  "submitted manifest"}
            ok, why = _record_whole(p, run_root, events)
            if not ok:
                return {"kind": "none", "event": latest, "reason": "the accepted review is not whole: %s" % why}
            return {"kind": "accepted", "event": latest, "reason": None}
        if p.get("decision") == "changes-requested" and terminal == "stage.accepted_with_residual":
            rec = governing.pin
            # the folded entry must name the same decision the fold-in recorded (review_event_id): the decision
            # the one lookup gives for this record
            folded = [f for f in manifest.get("folded_reviews") or [] if isinstance(f, dict)
                      and (f.get("record") or {}).get("path") == rec.get("path")
                      and (f.get("record") or {}).get("sha256") == rec.get("sha256")
                      and f.get("review_event_id") == latest.get("event_id")]
            ok, why = _record_whole(p, run_root, events)
            if folded and ok and pins_match(p):
                missing = unfolded_items(run_root, manifest, rec, latest.get("event_id"))
                if not missing:
                    return {"kind": "folded-changes", "event": latest, "reason": None}
                return {"kind": "none", "event": latest,
                        "reason": "the changes-requested review's items are not all folded in: %s"
                                  % ", ".join(missing)}
            if not (folded and ok):
                reason = why or ("its record is not listed in the manifest's folded_reviews at its current sha with "
                                 "review_event_id %s, the decision that logged it" % latest.get("event_id"))
            else:
                reason = "it is pinned to another run, design set or final run than the manifest"
            if not manifest.get("review_open_item"):
                return {"kind": "none", "event": latest,
                        "reason": "the changes-requested review is not folded into the latest submission: %s"
                                  % reason}
    if manifest.get("review_open_item"):
        return {"kind": "open-item", "event": None, "reason": None}
    if latest is None:
        return {"kind": "none", "event": None,
                "reason": "the business owner has not reviewed the pre-build simulation's page"}
    return {"kind": "none", "event": latest,
            "reason": "the latest review requested changes; another round must fold them in and be reviewed"}


def record_id(rel):
    m = RECORD_RE.match(os.path.basename(rel or ""))
    return "review-%s" % m.group(1) if m else None


# ---- what counts as folded: the entry must be what the pinned review record says, never a matching id alone ------
# A review's defect counts as folded into defects.yaml only when an entry there carries the record's defect as the
# record says it: the same id and from_review_record, the same class (or, for a dispute, the class the next round's
# routing step reclassified it to, with that step's history entry), the owner's note / correction verbatim
# (proposed_correction), the same evidence and raised_by, and, when the decision is known, from_review_event_id
# naming that decision. A declaration entry counts only when it is the record's entry exactly. An entry with a
# matching id (or the same from_review_record and files) that differs is a mismatch: the fold-in refuses it, and
# the structure check and clearance report the item unfolded, naming the difference.

RECLASSIFIED_FROM_DISPUTE = ("case", "rule", "mismatch")


def folded_defect_problem(rec_defect, entry, rid, event_id=None):
    """None when ENTRY (a defects.yaml entry) is the review record's defect REC_DEFECT as folded; else the plain
    difference, naming the defect."""
    did = rec_defect.get("id")
    diffs = []
    if not isinstance(entry, dict):
        return "defect %s is not in defects.yaml" % did
    if entry.get("from_review_record") != rid:
        diffs.append("from_review_record %r, not %r" % (entry.get("from_review_record"), rid))
    cls, want = entry.get("class"), rec_defect.get("class")
    if cls != want:
        routed = (want == "dispute" and cls in RECLASSIFIED_FROM_DISPUTE and any(
            isinstance(h, dict) and str(h.get("note") or "").startswith("reclassified from dispute as %s" % cls)
            for h in entry.get("history") or []))
        if not routed:
            diffs.append("class %r, not the record's %r" % (cls, want))
    for key, words in (("proposed_correction", "the owner's note / correction"), ("evidence", "evidence"),
                       ("raised_by", "raised_by")):
        if entry.get(key) != rec_defect.get(key):
            diffs.append("%s %r, not the record's %r" % (words, entry.get(key), rec_defect.get(key)))
    if event_id is not None and entry.get("from_review_event_id") != event_id:
        diffs.append("from_review_event_id %r, not the decision %r" % (entry.get("from_review_event_id"), event_id))
    if diffs:
        return ("defect %s is in defects.yaml but is not what review %s records (%s); a matching id alone is never a "
                "fold" % (did, rid, "; ".join(diffs)))
    return None


def folded_entry_problem(rec_entry, decl, rid):
    """None when the declaration DECL carries the review record's declaration entry REC_ENTRY exactly; else the
    plain reason (missing, or present with the same from_review_record and files but different)."""
    files = tuple(f.get("path") for f in rec_entry.get("files") or [] if isinstance(f, dict))
    same = [s for s in (decl or {}).get("systems") or [] if isinstance(s, dict)
            and s.get("from_review_record") == rid and tuple(f.get("path") for f in s.get("files") or []
                                                             if isinstance(f, dict)) == files]
    if not same:
        return "declaration entry for %s (review %s) is not in the declaration" % (rec_entry.get("system_id"), rid)
    if not any(s == rec_entry for s in same):
        return ("declaration entry for %s is in the declaration but is not what review %s records; a matching "
                "from_review_record and file alone is never a fold" % (rec_entry.get("system_id"), rid))
    return None


DUPLICATE_DEFECT_MSG = ("%s holds defect id %s more than once; each defect id appears once, so a second entry is "
                        "never read as the same defect and never left unchecked")


def duplicate_defect_ids(defects, where="defects.yaml"):
    """A plain refusal for every defect id the defects document DEFECTS ({defects: [...]}) holds more than once, naming
    the id ([] when every id is unique). The one check: the fold's match (fold_problems), the fold-in, the round's
    defect merge, the routing step and the structure check each refuse a defects file with a duplicated id through
    it, so no dict keyed by defect id silently drops an entry."""
    seen, dup = set(), []
    for d in (defects or {}).get("defects") or [] if isinstance(defects, dict) else []:
        did = d.get("id") if isinstance(d, dict) else None
        if did in seen and did not in dup:
            dup.append(did)
        seen.add(did)
    return [DUPLICATE_DEFECT_MSG % (where, did) for did in dup]


def fold_problems(record, rid, defects, decl, event_id=None):
    """Every defect and declaration entry of the review RECORD (read through its pin) that DEFECTS (defects.yaml)
    and DECL (the declaration) do not carry as the record says ([] when all are folded in). A defects file that
    holds a defect id more than once is refused whole (duplicate_defect_ids), naming the id: which entry would be
    the fold cannot be decided, and the second would never be checked."""
    dup = duplicate_defect_ids(defects)
    if dup:
        return dup
    have = {d.get("id"): d for d in (defects or {}).get("defects") or [] if isinstance(d, dict)}
    out = []
    for d in (record or {}).get("defects") or []:
        if not isinstance(d, dict):
            continue
        entry = have.get(d.get("id"))
        out.append("defect %s is not in defects.yaml" % d.get("id") if entry is None
                   else folded_defect_problem(d, entry, rid, event_id))
    for e in (record or {}).get("declaration_entries") or []:
        if isinstance(e, dict):
            out.append(folded_entry_problem(e, decl, rid))
    return [p for p in out if p]


def unproven_folds(run_root, events, manifest):
    """[(entry, reason)] for every entry of MANIFEST's folded_reviews (a manifest read through its pin) that is not
    shown to be folded: its record, read through the entry's pin, must be a review the log names (logged_reviews),
    and every defect and declaration entry it records must be in the manifest's pinned defects.yaml and declaration
    as the record says (fold_problems). A folded_reviews entry is a claim, never proof on its own; the fold-in and
    the builder treat an unproven one as still pending ([] when every entry is shown)."""
    logged = {lr.rel: lr for lr in logged_reviews(events)}
    out = []
    try:
        defects = read_pinned(run_root, manifest.get("defects"), "yaml", "the pinned defects.yaml") or {}
        decl_pin = (manifest.get("inputs") or {}).get("data_declaration")
        decl = (read_pinned(run_root, decl_pin, "yaml", "the pinned data declaration") or {}) if decl_pin else {}
    except PinnedReadError as exc:
        return [(f, exc.reason) for f in manifest.get("folded_reviews") or [] if isinstance(f, dict)]
    for f in manifest.get("folded_reviews") or []:
        if not isinstance(f, dict):
            continue
        pair = f.get("record") if isinstance(f.get("record"), dict) else {}
        lr = logged.get(pair.get("path"))
        if lr is None or lr.pin.get("sha256") != pair.get("sha256"):
            out.append((f, "folded_reviews lists %s, which no review decision in the log pins at that sha256"
                        % pair.get("path")))
            continue
        read = pinned_read(run_root, pair, "yaml", "the folded review record")
        if not read.ok:
            out.append((f, read.reason))
            continue
        problems = fold_problems(read.content if isinstance(read.content, dict) else {},
                                 record_id(pair.get("path")), defects, decl, lr.event.get("event_id"))
        if problems:
            out.append((f, "; ".join(problems)))
    return out


def unfolded_items(run_root, manifest, record_pin, event_id=None):
    """Every defect and declaration entry of the review record RECORD_PIN ({path, sha256}, the decision's pin) names
    that the manifest's pinned defects.yaml and declaration do not carry as the record says (fold_problems: a
    matching id alone is never a fold) ([] when all are folded in). Each of the three files is read through its pin;
    one that is not the pinned file is reported as the reason (naming it), never read as if it were."""
    rid = record_id((record_pin or {}).get("path") if isinstance(record_pin, dict) else None)
    try:
        doc = read_pinned(run_root, record_pin, "yaml", "the review record") or {}
        defects = read_pinned(run_root, manifest.get("defects"), "yaml", "the pinned defects.yaml") or {}
        decl_pin = (manifest.get("inputs") or {}).get("data_declaration")
        decl = (read_pinned(run_root, decl_pin, "yaml", "the pinned data declaration") or {}) if decl_pin else {}
    except PinnedReadError as exc:
        return [exc.reason]
    return fold_problems(doc, rid, defects, decl, event_id)


# ---- the review context: what "current" means for a feedback file --------------------------------------

def shown_cases(run_root, manifest):
    """The case ids the page shows for MANIFEST (and so the ids a feedback file must name exactly once): every Test
    Suite case of the pinned eval set, then every case the final run ran for a sample the business owner added."""
    eval_set = read_pinned(run_root, manifest["inputs"]["eval_set"], "yaml", "the pinned Test Suite") or {}
    ids = [c.get("id") for _g, c in eval_cases(eval_set)]
    # the final run's results through their pin: a results file that is not the pinned one raises PinnedReadError
    # (never a silent fall back to the Test Suite cases alone); a manifest with no final run has no sample cases
    if final_run(manifest) is not None:
        for r in results_rows(run_root, manifest):
            if r.get("sample_id") is not None and r.get("case_id") not in ids:
                ids.append(r.get("case_id"))
    return ids


# The feedback rules that read the submitted manifest's own bytes: F7 its pinned design set, F8 its final_run_id,
# F29 its systems, F24 and F25 the cases its page showed. When that file has been replaced (or is missing),
# review_context() cannot judge them against anything: F0 refuses the file whole, naming the replacement, and these
# are left out rather than judged against the next round's file, the design set as it lies on disk, or nothing.
SUBMITTED_BYTES_RULES = ("F7", "F8", "F29", "F24", "F25")


def review_context(run_root, events):
    """The current values a feedback file is checked against (the one derivation the recorder and the writer's
    decision door share). Raises PrebuildError when stage 11 has no submission. The submitted manifest is read
    through submitted_manifest(): when the file at its path is no longer the submitted one, manifest_moved is set
    (F0 refuses the file, naming manifest_reason), the manifest is None, and SUBMITTED_BYTES_RULES are listed in
    not_rechecked, so nothing is read from the replaced file.
    The current design set (F7) is the one the submission pins (the held manifest's design_set), never the file as
    it lies on disk: design_set_sha256 is that pin's sha256, and when the pinned design set no longer holds on disk
    (the gate re-ran) design_set_reason names it and F7 refuses."""
    held = submitted_manifest(run_root, events)
    if held.state == "none":
        raise PrebuildError("stage 11 has no submitted manifest; a review is recorded against a submission")
    sub, sp, manifest = held.submission, held.submission.get("payload") or {}, held.manifest
    opened = next((e for e in events or [] if e.get("type") == "run.opened"), {})
    ctx = {"run_id": opened.get("run_id"), "design_set_sha256": None, "design_set_reason": None,
           "final_run_id": None, "manifest_sha256": sp.get("sha256"), "manifest_moved": not held.held,
           "manifest_reason": held.reason, "submission_event_id": sub.get("event_id"), "systems": [], "shown": [],
           "manifest_rel": held.rel, "manifest": manifest, "not_rechecked": []}
    if held.held:
        ds_pin = manifest.get("design_set")
        ctx.update(design_set_sha256=(ds_pin or {}).get("sha256") if isinstance(ds_pin, dict) else None,
                   design_set_reason=pin_reason(run_root, ds_pin, "the submission's pinned design set"))
        ctx.update(final_run_id=manifest.get("final_run_id"),
                   systems=[s.get("system_id") for s in manifest.get("systems") or [] if isinstance(s, dict)],
                   shown=shown_cases(run_root, manifest))
    else:
        ctx["not_rechecked"] = list(SUBMITTED_BYTES_RULES)
    return ctx


# The feedback rules a later door cannot re-check on a record's own pins once the round's files have moved on: F0
# asks whether the manifest on disk was the submitted one, and F29 which systems it names, so both need the
# submitted manifest's bytes; F24 and F25 ask which cases its page showed, so they also need its pinned Test Suite
# and final-run results. For these the later door verifies instead that the feedback file is byte-identical to the
# one the recorded decision pins (the writer ran every feedback rule on that exact file before appending it).
MANIFEST_RULES = ("F0", "F29")
SHOWN_RULES = ("F24", "F25")


def decision_for_record(events, rel):
    """The prebuild-simulation-review human.decision that logged the pin of the review record at REL (None when
    there is none), through logged_reviews, the one lookup. A path is pinned by one decision only: a log in which a
    later decision pins it again (at the same sha256 or another) is refused there (SupersededPinError), never read
    as naming either decision."""
    return next((lr.event for lr in logged_reviews(events) if lr.rel == rel), None)


def latest_logged_review(events):
    """The LoggedReview whose decision is the latest prebuild-simulation-review decision in the log (None when the
    log holds none), through logged_reviews, the one lookup: governing_review() reads "the latest decision" here, so
    clearance resolves the decision that governs a review record exactly as the fold-in, unproven_folds() and the
    structure check do (a log that pins one path twice is refused there, never read as either decision)."""
    order = {id(e): i for i, e in enumerate(events or [])}
    logged = logged_reviews(events)
    return max(logged, key=lambda lr: order.get(id(lr.event), -1)) if logged else None


# ---- what exists: the log says, never a look at the disk ------------------------------------------------
# Stage 11 learns what exists only from the event log, and reaches every file only through a pin the log recorded:
#   * review records: each prebuild-simulation-review human.decision pins its record ({path, sha256}); the record
#     pins its feedback file and its sample files. logged_reviews() is the one answer to "which review records
#     exist"; read_logged_review() reads one through its decision's pin, and its content is used only after the pin
#     holds. pending_reviews() is the one answer to "which changes-requested records must the next submission still
#     fold in", and unlogged_folds() to "does a folded_reviews list name only records the log names". A record the
#     log names that is missing or changed is a refusal (or "none" at a read-only door), naming the file.
#   * runs: the runs a submitted round has are the runs its submitted manifest lists (submitted_runs, through
#     submitted_manifest); a run it lists is never dropped (runs_left_out).
# A file on disk that the log does not name is never evidence of anything: stray_review_files() lists the reviews
# folder only so a door can report such files. The one other look at the disk is next_round_runs(): the next
# round's own new runs, which nothing in the log names until that round's submission pins them.

LoggedReview = _namedtuple("LoggedReview", "rel pin decision event")
LOGGED_RECORD_WHAT = "the review record that decision %s logs,"


class SupersededPinError(PrebuildError):
    """Two review decisions in the log pin the same review-record path at different sha256s: a logged pin is never
    silently superseded, so every door that asks the log which records exist refuses, naming both decisions."""


SUPERSEDED_MSG = ("review record %s is pinned by decision %s at sha256 %s and again by the later decision %s at %s "
                  "sha256 %s; a review record is written once and never edited afterwards, and a logged pin is never "
                  "superseded: no later decision may pin that path again, at the same bytes or different ones")


def logged_pin_conflict(events, pair):
    """The earlier prebuild-simulation-review decision in EVENTS that already pins PAIR's path ({path, sha256}), at
    any sha256, or None (decision_for_record, the one lookup). The writer's decision door refuses a decision that
    pins a logged record path again with it (D6); logged_reviews() refuses a log that already holds such a pair."""
    if not isinstance(pair, dict):
        return None
    return decision_for_record(events, pair.get("path"))


def logged_reviews(events):
    """[LoggedReview(rel, pin, decision, event)]: one per review-record path the log's prebuild-simulation-review
    human.decisions pin (every stage, so late feedback recorded at run level counts), sorted by path (review-<nnn>,
    oldest first). PIN is the decision's review_record {path, sha256}; DECISION its payload's decision (the writer
    checked it equals the record's, D7). This is the one lookup of the decision that governs a review record
    (decision_for_record, latest_logged_review and every reader go through it). A path is pinned by one decision
    only: a later decision pinning a logged path again, at the same sha256 or a different one, is never read as
    adding nothing or as replacing the earlier one: SupersededPinError, naming the path and both decisions (the
    writer's decision door refuses such a decision before it is appended, D6)."""
    first = {}
    for e in review_decisions(events):
        pair = (e.get("payload") or {}).get("review_record")
        if not (isinstance(pair, dict) and isinstance(pair.get("path"), str) and pair.get("path")):
            continue
        prior = first.get(pair["path"])
        if prior is None:
            first[pair["path"]] = e
            continue
        psha = prior["payload"]["review_record"].get("sha256")
        raise SupersededPinError(SUPERSEDED_MSG % (pair["path"], prior.get("event_id"), str(psha)[:12],
                                                   e.get("event_id"),
                                                   "the same" if psha == pair.get("sha256") else "a different",
                                                   str(pair.get("sha256"))[:12]))
    return [LoggedReview(rel, dict(ev["payload"]["review_record"]), ev["payload"].get("decision"), ev)
            for rel, ev in sorted(first.items())]


def read_logged_review(run_root, lr):
    """The review record LR (a LoggedReview) names, read through its decision's pin (a PinRead): its content only
    when the file is on disk with exactly the sha256 the decision recorded; otherwise the reason, naming the file."""
    return pinned_read(run_root, lr.pin, "yaml", LOGGED_RECORD_WHAT % lr.event.get("event_id"))


def pending_reviews(run_root, events, folded):
    """[(LoggedReview, PinRead)] for every logged changes-requested review whose record pin ({path, sha256}) the
    folded_reviews list FOLDED does not already carry: what the next submission must still fold in, each read
    through its decision's pin. A missing or changed record comes back with a PinRead that is not ok, never
    dropped."""
    done = {((f.get("record") or {}).get("path"), (f.get("record") or {}).get("sha256"))
            for f in folded or [] if isinstance(f, dict) and isinstance(f.get("record"), dict)}
    return [(lr, read_logged_review(run_root, lr)) for lr in logged_reviews(events)
            if lr.decision == "changes-requested" and (lr.rel, lr.pin.get("sha256")) not in done]


def unlogged_folds(events, folded):
    """The entries of the folded_reviews list FOLDED that the log does not name: a record pin ({path, sha256}) and
    review_event_id that are not a logged changes-requested decision's review_record and event id. [] when every
    entry is one the log names. Each is a plain sentence naming the entry."""
    logged = {(lr.rel, lr.pin.get("sha256"), lr.event.get("event_id")) for lr in logged_reviews(events)
              if lr.decision == "changes-requested"}
    out = []
    for f in folded or []:
        rec = (f or {}).get("record") if isinstance(f, dict) else None
        key = ((rec or {}).get("path"), (rec or {}).get("sha256"), (f or {}).get("review_event_id")) \
            if isinstance(rec, dict) else (None, None, None)
        if key not in logged:
            out.append("folded_reviews entry %s (review_event_id %s) is not a changes-requested review the log "
                       "records at that sha256; only a review the log names is ever folded"
                       % (key[0] or f, key[2]))
    return out


def stray_review_files(run_root, events):
    """Files under the reviews folder that no logged decision names, run-relative, sorted. Listed only so a door
    can report them; a stray file is never evidence of anything (no fold, no page entry, no clearance effect)."""
    rdir = os.path.join(run_root, REVIEWS_DIR)
    if not os.path.isdir(rdir):
        return []
    named = {lr.rel for lr in logged_reviews(events)}
    return sorted(rel for rel in ("%s/%s" % (REVIEWS_DIR, n) for n in os.listdir(rdir)) if rel not in named)


STRAY_NOTE = ("%s is in the reviews folder but no review decision in the event log names it; it is ignored (a file "
              "the log does not name is never evidence of anything)")


def submitted_runs(run_root, events):
    """The run ids stage 11's latest submitted manifest lists, in its order, read through submitted_manifest(); None
    when stage 11 has no submission or the file at its path is no longer the submitted one."""
    held = submitted_manifest(run_root, events)
    if not held.held:
        return None
    return [r.get("run_id") for r in held.manifest.get("runs") or [] if isinstance(r, dict)]


def runs_left_out(run_root, events, runs):
    """The run ids the latest submitted manifest lists (submitted_runs) that RUNS leaves out: a run the log records
    is never dropped from the next round's manifest ([] when none is, or when nothing is held)."""
    recorded = submitted_runs(run_root, events) or []
    return [r for r in recorded if r not in (runs or [])]


def next_round_runs(run_root, listed):
    """The next round's own new runs: folders under runs/ that LISTED does not name and whose run files (RUN_FILES)
    all exist, sorted. Nothing in the log names a run until the submission whose manifest pins it, so this is the
    one look at the runs folder in stage 11: it only finds what the next round has made (next_round_act, the
    builder's default run list); a run it finds is evidence of nothing until a submitted manifest pins it and the
    four checks and the reviews bind those bytes."""
    rdir = os.path.join(run_root, OUT, "runs")
    if not os.path.isdir(rdir):
        return []
    return sorted(n for n in os.listdir(rdir) if n not in (listed or [])
                  and all(os.path.isfile(os.path.join(rdir, n, f)) for f in RUN_FILES))


def recorded_context(run_root, events, pins):
    """The review context rebuilt from a review record's own PINS (run_id, design_set_sha256, final_run_id,
    manifest_sha256, submission_event_id): the values the recorder checked the feedback file against, so every
    later door (the fold-in, clearance, the renderer, the structure check) runs the same feedback rules and gets
    the same answer. The submitted manifest is read from the submission the pins name, through
    submitted_manifest(), and only when it is still the submitted file at the pinned sha; when it is not, the rules
    that need it are listed in not_rechecked (MANIFEST_RULES, SHOWN_RULES) and manifest_reason says why."""
    ctx = {k: pins.get(k) for k in PIN_KEYS}
    ctx.update(manifest_moved=False, systems=[], shown=[], not_rechecked=[], recorded=True)
    sub = next((e for e in events or [] if e.get("event_id") == pins.get("submission_event_id")
                and e.get("type") == "executor.submitted" and e.get("stage") == STAGE), None)
    if sub is None:
        ctx.update(not_rechecked=list(MANIFEST_RULES + SHOWN_RULES),
                   manifest_reason="the pins name no stage 11 executor.submitted")
        return ctx
    held = submitted_manifest(run_root, events, sub)
    if not held.held or (sub.get("payload") or {}).get("sha256") != pins.get("manifest_sha256"):
        ctx.update(not_rechecked=list(MANIFEST_RULES + SHOWN_RULES),
                   manifest_reason=held.reason or "the pinned manifest_sha256 is not the named submission's")
        return ctx
    mrel, manifest = held.rel, held.manifest
    ctx["manifest_rel"] = mrel
    ctx["systems"] = [s.get("system_id") for s in manifest.get("systems") or [] if isinstance(s, dict)]
    try:
        ctx["shown"] = shown_cases(run_root, manifest)
    except PinnedReadError as exc:
        # the pinned Test Suite or final-run results moved on: F24/F25 fall to the byte-identity check, and the
        # reason names the file
        ctx.update(not_rechecked=list(SHOWN_RULES), manifest_reason=exc.reason)
    except (PrebuildError, KeyError, TypeError) as exc:
        ctx.update(not_rechecked=list(SHOWN_RULES), manifest_reason="the shown cases cannot be read: %s" % exc)
    return ctx


# ---- rule list 1: the feedback file (prebuild-review-feedback.rules.md) --------------------------------

def _str_nonempty(v):
    return isinstance(v, str) and v != ""


def _trim_nonempty(v):
    return isinstance(v, str) and v.strip() != ""


def _has_word(v):
    return isinstance(v, str) and _re.search(r"\S", v) is not None


def _keys_problem(entry, keys):
    missing = [k for k in keys if k not in entry]
    extra = [k for k in entry if k not in keys]
    parts = (["missing: %s" % ", ".join(missing)] if missing else []) + (["not allowed: %s" % ", ".join(
        str(k) for k in extra)] if extra else [])
    return "; ".join(parts)


def validate_feedback(doc, ctx):
    """Every rule of prebuild-review-feedback.rules.md for the parsed feedback file DOC against CTX (review_context).
    Returns ["F<n>: message", ...]; [] when the file meets every rule. A file with any finding is refused whole.
    CTX is review_context() at the recording doors, or recorded_context() at every later door; a rule the latter
    lists in not_rechecked is left to that door's byte-identity check (validate_record), never skipped silently."""
    out = []
    skip = set(ctx.get("not_rechecked") or ())
    if ctx.get("manifest_moved") and "F0" not in skip:
        out.append("F0: %s" % MOVED_MSG + (" (%s)" % ctx["manifest_reason"] if ctx.get("manifest_reason") else ""))
    if not isinstance(doc, dict):
        return out + ["F1: the feedback file must be one JSON object"]
    missing = [k for k in FEEDBACK_KEYS if k not in doc]
    if missing:
        out.append("F2: the top-level key(s) %s are missing; the file carries exactly the eleven keys"
                   % ", ".join(missing))
    extra = [k for k in doc if k not in FEEDBACK_KEYS]
    if extra:
        out.append("F3: the top-level key(s) %s are not allowed; the file carries exactly the eleven keys"
                   % ", ".join(str(k) for k in extra))
    if "schema" in doc and doc.get("schema") != FEEDBACK_SCHEMA:
        out.append("F4: schema must be exactly %s, not %r" % (FEEDBACK_SCHEMA, doc.get("schema")))
    if "source" in doc and doc.get("source") not in ("page", "chat"):
        out.append("F5: source must be page or chat, not %r" % (doc.get("source"),))
    for rule, key in zip(("F6", "F7", "F8", "F9"), IDENTITY_PINS):
        if rule in skip:
            continue
        differs = key in doc and (not isinstance(doc.get(key), str) or doc.get(key) != ctx.get(key))
        # F7's current value is the design set the submission pins; when that pinned file no longer holds on disk,
        # the design changed after submission and the file is stale whatever it carries (one finding either way)
        moved = ctx.get("design_set_reason") if rule == "F7" else None
        if differs:
            out.append("%s: %s (the identity pin %s differs: the file says %r, the current value is %r)%s"
                       % (rule, STALE_MSG, key, doc.get(key), ctx.get(key), (" (%s)" % moved) if moved else ""))
        elif moved:
            out.append("%s: %s (the design set the submission pins has changed since: %s)" % (rule, STALE_MSG, moved))
    if "written_at" in doc and not isinstance(doc.get("written_at"), str):
        out.append("F10: written_at must be a string")
    if "reviewer_role" in doc and not _trim_nonempty(doc.get("reviewer_role")):
        out.append("F11: reviewer_role must be a non-empty role")
    cases = doc.get("cases")
    if "cases" in doc and not isinstance(cases, list):
        out.append("F12: cases must be a list")
    samples = doc.get("samples")
    if "samples" in doc and not isinstance(samples, list):
        out.append("F13: samples must be a list (possibly empty)")
    if "chat" in doc:
        chat = doc.get("chat")
        if doc.get("source") == "page" and chat is not None:
            out.append("F14: chat must be null when source is page")
        if doc.get("source") == "chat":
            if not isinstance(chat, dict) or list(chat) != ["turns"]:
                out.append("F15: when source is chat, chat must be an object with exactly one key, turns")
            elif not isinstance(chat.get("turns"), list) or not chat["turns"] or \
                    not all(_trim_nonempty(t) for t in chat["turns"]):
                out.append("F16: chat.turns must be a non-empty list of the person's words, each non-empty")
    if isinstance(cases, list):
        shown = list(ctx.get("shown") or [])
        if not cases:
            out.append("F17: cases is empty; it must name every case shown on the submitted page exactly once")
        seen = []
        for i, c in enumerate(cases):
            if not isinstance(c, dict):
                out.append("F18: cases[%d] must be an object with exactly case_id, verdict and note" % i)
                continue
            problem = _keys_problem(c, CASE_KEYS)
            if problem:
                out.append("F18: cases[%d] must carry exactly case_id, verdict and note (%s)" % (i, problem))
            cid = c.get("case_id")
            if not _str_nonempty(cid):
                out.append("F19: cases[%d].case_id must be a non-empty string" % i)
                continue
            if "verdict" in c and c.get("verdict") not in ("accept", "dispute"):
                out.append("F20: case %s has verdict %r; it must be accept or dispute" % (cid, c.get("verdict")))
            if "note" in c and not isinstance(c.get("note"), str):
                out.append("F21: case %s's note must be a string" % cid)
            elif c.get("verdict") == "dispute" and not _has_word(c.get("note")):
                out.append("F22: case %s is disputed without a note; a dispute needs at least one word" % cid)
            if cid in seen:
                out.append("F23: case %s is named more than once" % cid)
            elif cid not in shown and "F24" not in skip:
                out.append("F24: case %s is not shown on the submitted page" % cid)
            seen.append(cid)
        left_out = [s for s in shown if s not in seen]
        if cases and left_out and "F25" not in skip:
            out.append("F25: the file leaves out shown case(s) %s; it must name every shown case exactly once"
                       % ", ".join(left_out))
    if isinstance(samples, list):
        ids = []
        systems = list(ctx.get("systems") or [])
        for i, s in enumerate(samples):
            if not isinstance(s, dict):
                out.append("F26: samples[%d] must be an object with exactly the ten sample keys" % i)
                continue
            problem = _keys_problem(s, SAMPLE_KEYS)
            if problem:
                out.append("F26: samples[%d] must carry exactly the ten sample keys (%s)" % (i, problem))
            sid = s.get("sample_id")
            where = "sample %s" % sid if _str_nonempty(sid) else "samples[%d]" % i
            if "sample_id" in s and not _str_nonempty(sid):
                out.append("F27: samples[%d].sample_id must be a non-empty string" % i)
            elif _str_nonempty(sid) and sid in ids:
                out.append("F28: sample_id %s is used more than once" % sid)
            ids.append(sid)
            if "system_id" in s and s.get("system_id") not in systems and "F29" not in skip:
                out.append("F29: %s names system %r, which is not one of the design's systems (%s)"
                           % (where, s.get("system_id"), ", ".join(str(x) for x in systems)))
            for rule, key in (("F30", "source"), ("F31", "permission"), ("F32", "redaction"),
                              ("F33", "provided_by")):
                if key in s and not _trim_nonempty(s.get(key)):
                    out.append("%s: %s must say %s (a non-empty answer)" % (rule, where, key))
            if "format" in s and s.get("format") not in ("csv", "pasted-rows"):
                out.append("F34: %s has format %r; it must be csv or pasted-rows" % (where, s.get("format")))
            if "file_name" in s:
                if s.get("format") == "csv" and not isinstance(s.get("file_name"), str):
                    out.append("F35: %s is csv, so file_name must be the picked file's name" % where)
                if s.get("format") == "pasted-rows" and s.get("file_name") is not None:
                    out.append("F36: %s is pasted-rows, so file_name must be null" % where)
            if "rows_text" in s:
                rt = s.get("rows_text")
                if not _trim_nonempty(rt):
                    out.append("F37: %s has no rows (rows_text must be non-empty)" % where)
                elif len(rt.encode("utf-8")) > MAX_SAMPLE_BYTES:
                    out.append("F38: %s is over 2 MB; send a larger file directly to the run's inputs" % where)
            if "what_should_happen" in s and s.get("what_should_happen") is not None and \
                    not isinstance(s.get("what_should_happen"), str):
                out.append("F39: %s's what_should_happen must be a string or null" % where)
    return out


def feedback_items(doc):
    """The file's items, one line each, so a refusal never loses what the reviewer wrote."""
    lines = []
    if isinstance(doc, dict):
        for c in doc.get("cases") or [] if isinstance(doc.get("cases"), list) else []:
            if isinstance(c, dict):
                note = c.get("note")
                lines.append("case %s: %s%s" % (c.get("case_id"), c.get("verdict"),
                                                (" (note: %s)" % note) if isinstance(note, str) and note else ""))
        for s in doc.get("samples") or [] if isinstance(doc.get("samples"), list) else []:
            if isinstance(s, dict):
                lines.append("sample %s for %s (%d characters of rows)" % (s.get("sample_id"), s.get("system_id"),
                                                                           len(str(s.get("rows_text") or ""))))
    return lines


# ---- rule list 2: the review record (prebuild-review-record.template.yaml) -----------------------------

def sample_file_path(final_run_id, sample):
    ext = "csv" if sample.get("format") == "csv" else "txt"
    return "inputs/data-samples/%s/review-%s-%s.%s" % (sample.get("system_id"), final_run_id, sample.get("sample_id"),
                                                       ext)


def expected_record(feedback, feedback_pin, pins, sequence, sample_pins):
    """The review record the recorder writes for FEEDBACK (plan 6.6): the single source of the record's content.
    SAMPLE_PINS maps each sample_id to the {path, sha256} of the sample file written for it."""
    rid = "review-%03d" % sequence
    accepted = [c["case_id"] for c in feedback["cases"] if c["verdict"] == "accept"]
    disputed = [{"case_id": c["case_id"], "note": c["note"]} for c in feedback["cases"] if c["verdict"] == "dispute"]
    samples_added = [{"sample_id": s["sample_id"], "system_id": s["system_id"],
                      "path": sample_pins[s["sample_id"]]["path"], "sha256": sample_pins[s["sample_id"]]["sha256"]}
                     for s in feedback["samples"]]
    defects = []
    for d in disputed:
        defects.append({"id": "dispute-%s-%s" % (rid, slug(d["case_id"])), "class": "dispute",
                        "evidence": {"cases": [d["case_id"]], "rules": [], "runs": [pins["final_run_id"]]},
                        "owner_stage": None, "proposed_correction": d["note"], "status": "open",
                        "raised_by": "business-owner", "routed_via": None, "history": [],
                        "from_review_record": rid})
    for s in feedback["samples"]:
        defects.append({"id": "coverage-%s-%s" % (rid, slug(s["sample_id"])), "class": "coverage",
                        "evidence": {"cases": [], "rules": [], "runs": [pins["final_run_id"]]},
                        "owner_stage": "eval",
                        "proposed_correction": "write a Test Suite case for the business owner's sample %s (%s); no "
                                               "pinned case describes it" % (s["sample_id"], s["system_id"]),
                        "status": "open", "raised_by": "business-owner", "routed_via": None, "history": [],
                        "from_review_record": rid})
    entries = [{"system_id": s["system_id"], "provided": "real-sample", "files": [dict(sample_pins[s["sample_id"]])],
                "provided_by": s["provided_by"], "permission": s["permission"], "redaction": s["redaction"],
                "notes": s.get("what_should_happen") or "", "added_via": "prebuild-simulation-review",
                "source": s["source"], "feedback_file": dict(feedback_pin), "from_review_record": rid}
               for s in feedback["samples"]]
    return {"schema": RECORD_SCHEMA, "sequence": sequence, "feedback_file": dict(feedback_pin),
            "source": feedback["source"], "reviewer_role": feedback["reviewer_role"], "pins": dict(pins),
            "decision": "accepted" if not disputed and not feedback["samples"] else "changes-requested",
            "cases_accepted": accepted, "cases_disputed": disputed, "samples_added": samples_added,
            "defects": defects, "declaration_entries": entries}


def _pin_pair(v):
    return isinstance(v, dict) and set(v) == {"path", "sha256"} and isinstance(v.get("path"), str)


def validate_record(doc, rel, run_root, events, ctx=None):
    """Every rule of the rule block at the head of prebuild-review-record.template.yaml for the record at REL.
    Every door re-checks the pinned feedback file against every feedback rule (R7, validate_feedback). With CTX
    (review_context, while the review is being recorded: the recorder and the writer's decision door) it is checked
    against the current values and the submission must be the latest. Without it (every later door: the fold-in,
    clearance, the renderer, the structure check) the context is rebuilt from the record's own pins
    (recorded_context), so the check is the one the recorder ran; a rule that cannot be re-checked on those pins
    (MANIFEST_RULES, SHOWN_RULES, once the submitted manifest or its Test Suite has moved on) is replaced by
    checking that the feedback file and the record are byte-identical to the ones the recorded decision pins.
    Returns ["R<n>: ..."]."""
    out = []
    live = ctx is not None
    if not isinstance(doc, dict):
        return ["R1: the review record must be a mapping"]
    missing = [k for k in RECORD_KEYS if k not in doc]
    if missing:
        out.append("R1: the field(s) %s are missing" % ", ".join(missing))
    extra = [k for k in doc if k not in RECORD_KEYS]
    if extra:
        out.append("R2: the field(s) %s are not allowed" % ", ".join(str(k) for k in extra))
    if "schema" in doc and doc.get("schema") != RECORD_SCHEMA:
        out.append("R3: schema must be exactly %s" % RECORD_SCHEMA)
    m = RECORD_RE.match(os.path.basename(rel or ""))
    seq = doc.get("sequence")
    if "sequence" in doc and (not isinstance(seq, int) or isinstance(seq, bool) or seq < 1 or not m
                              or int(m.group(1)) != seq):
        out.append("R4: sequence must be an integer of at least 1 equal to the file name's number (%s)"
                   % os.path.basename(rel or ""))
    rid = record_id(rel) or ("review-%03d" % seq if isinstance(seq, int) and not isinstance(seq, bool) else None)
    ff = doc.get("feedback_file")
    fb = {}
    ff_actual = None
    if "feedback_file" in doc:
        read = pinned_read(run_root, ff, "json", "the feedback file") if _pin_pair(ff) else None
        if not _pin_pair(ff) or not ff["path"].startswith(FEEDBACK_DIR + "/"):
            out.append("R5: feedback_file must be {path, sha256} with path under %s/" % FEEDBACK_DIR)
        elif read.state not in ("held", "unparsable"):
            out.append("R6: feedback_file %s is not on disk at its pinned sha (%s)" % (ff["path"], read.reason))
        else:
            # the file is the pinned one: a declaration entry's feedback_file must be this same pin
            ff_actual = {"path": ff["path"], "sha256": ff["sha256"]}
            fb = read.content if read.ok else None
            if not isinstance(fb, dict):
                out.append("R7: the feedback file %s is not one JSON object, so it cannot have passed its rules"
                           % ff["path"])
                fb = {}
            else:
                rpins = doc.get("pins")
                fctx = ctx if live else (recorded_context(run_root, events, rpins) if isinstance(rpins, dict)
                                         and set(rpins) == set(PIN_KEYS) else None)
                # fctx is None only when the pins break R10, which refuses the record below
                problems = validate_feedback(fb, fctx) if fctx is not None else []
                if problems:
                    out.append("R7: the feedback file %s does not pass every feedback rule (%s)"
                               % (ff["path"], "; ".join(problems)))
                elif fctx is not None and fctx.get("not_rechecked"):
                    dec = decision_for_record(events, rel)
                    dp = (dec or {}).get("payload") or {}
                    if dec is None or dp.get("feedback_file") != {"path": ff["path"], "sha256": ff["sha256"]} or \
                            pin_reason(run_root, dp.get("review_record"), "the review record") is not None:
                        out.append("R7: the feedback file %s cannot be re-checked against %s on the record's own "
                                   "pins (%s), and it and the record are not byte-identical to the ones the "
                                   "recorded decision pins"
                                   % (ff["path"], ", ".join(fctx["not_rechecked"]), fctx.get("manifest_reason")
                                      or "the submitted manifest's Test Suite or results have changed since"))
    if "source" in doc and (doc.get("source") not in ("page", "chat") or (fb and doc.get("source") != fb.get("source"))):
        out.append("R8: source must be page or chat and equal the feedback file's source")
    if "reviewer_role" in doc and (not _trim_nonempty(doc.get("reviewer_role"))
                                   or (fb and doc.get("reviewer_role") != fb.get("reviewer_role"))):
        out.append("R9: reviewer_role must be non-empty and equal the feedback file's reviewer_role")
    pins = doc.get("pins")
    if "pins" in doc:
        if not isinstance(pins, dict) or set(pins) != set(PIN_KEYS):
            out.append("R10: pins must be exactly {%s}" % ", ".join(PIN_KEYS))
            pins = {}
        else:
            if fb and any(pins.get(k) != fb.get(k) for k in IDENTITY_PINS):
                out.append("R11: pins' run_id, design_set_sha256, final_run_id and manifest_sha256 must equal the "
                           "feedback file's four identity pins")
            sub = next((e for e in events or [] if e.get("event_id") == pins.get("submission_event_id")), None)
            latest = submitted_manifest(run_root, events) if live else None
            if sub is None or sub.get("type") != "executor.submitted" or sub.get("stage") != STAGE or \
                    (sub.get("payload") or {}).get("sha256") != pins.get("manifest_sha256") or \
                    (live and (latest.submission or {}).get("event_id") != sub.get("event_id")):
                out.append("R12: pins.submission_event_id must name the latest executor.submitted for %s when the "
                           "review is recorded, whose sha256 equals pins.manifest_sha256" % STAGE)
            elif live and not latest.held:
                # a review is recorded against the submitted manifest; once the file at its path is no longer
                # that manifest, the submission it names cannot be reviewed
                out.append("R12: the review is recorded against the latest executor.submitted for %s, but %s"
                           % (STAGE, latest.reason))
    else:
        pins = {}
    fcases = [c for c in fb.get("cases") or [] if isinstance(c, dict)] if isinstance(fb.get("cases"), list) else []
    fsamples = [s for s in fb.get("samples") or [] if isinstance(s, dict)] if isinstance(fb.get("samples"),
                                                                                         list) else []
    accepted = [c.get("case_id") for c in fcases if c.get("verdict") == "accept"]
    disputed = [{"case_id": c.get("case_id"), "note": c.get("note")} for c in fcases if c.get("verdict") == "dispute"]
    if "decision" in doc:
        want = "accepted" if not disputed and not fsamples else "changes-requested"
        if doc.get("decision") not in ("accepted", "changes-requested") or (fb and doc.get("decision") != want):
            out.append("R13: decision must be accepted exactly when every case is accepted and no sample was added, "
                       "else changes-requested")
    ca = doc.get("cases_accepted")
    if "cases_accepted" in doc:
        if not isinstance(ca, list) or (fb and [x for x in accepted if x in ca] != ca):
            out.append("R14: cases_accepted must be case ids the feedback file accepted, in the file's order")
    cd = doc.get("cases_disputed")
    if "cases_disputed" in doc:
        if not isinstance(cd, list) or (fb and [x for x in disputed if x in cd] != cd):
            out.append("R15: cases_disputed must be disputed cases, each {case_id, note} with the note verbatim "
                       "from the feedback file, in the file's order")
    if fb and isinstance(ca, list) and isinstance(cd, list):
        named = list(ca) + [d.get("case_id") for d in cd if isinstance(d, dict)]
        if sorted(map(str, named)) != sorted(str(c.get("case_id")) for c in fcases) or len(set(map(str, named))) \
                != len(named):
            out.append("R16: cases_accepted and cases_disputed together must name every case the feedback file "
                       "names, each exactly once")
    final_run_id = pins.get("final_run_id") or fb.get("final_run_id")
    by_sid = {s.get("sample_id"): s for s in fsamples}
    sa = doc.get("samples_added")
    if "samples_added" in doc:
        if not isinstance(sa, list):
            out.append("R17: samples_added must be a list")
        else:
            for s in sa:
                fs = by_sid.get((s or {}).get("sample_id")) if isinstance(s, dict) else None
                want = sample_file_path(final_run_id, fs) if fs else None
                if not isinstance(s, dict) or set(s) != set(SAMPLE_ADDED_KEYS) or (fb and fs is None) or \
                        (fs and (s.get("system_id") != fs.get("system_id") or s.get("path") != want)) or \
                        pin_reason(run_root, s, "the added sample") is not None:
                    out.append("R17: samples_added entry %r must be {sample_id, system_id, path, sha256} for a "
                               "feedback sample, path the sample file the recorder wrote (%s), sha256 equal to that "
                               "file on disk" % ((s or {}).get("sample_id") if isinstance(s, dict) else s, want))
            if fb and sorted(str((s or {}).get("sample_id")) for s in sa if isinstance(s, dict)) != \
                    sorted(str(x) for x in by_sid):
                out.append("R18: samples_added must carry exactly one entry per feedback sample ([] when none)")
    defects = doc.get("defects")
    if "defects" in doc:
        if not isinstance(defects, list):
            out.append("R19: defects must be a list")
        else:
            disp = [d for d in defects if isinstance(d, dict) and d.get("class") == "dispute"]
            cov = [d for d in defects if isinstance(d, dict) and d.get("class") == "coverage"]
            disp_cases = sorted(str(c) for d in disp for c in ((d.get("evidence") or {}).get("cases") or []))
            if fb and (len(defects) != len(disp) + len(cov) or disp_cases != sorted(str(x["case_id"]) for x in disputed)
                       or len(cov) != len(fsamples)):
                out.append("R19: defects must be one dispute defect per disputed case and one coverage defect per "
                           "added sample, nothing else")
            for d in defects:
                ev = (d or {}).get("evidence") if isinstance(d, dict) else None
                if not isinstance(d, dict) or set(d) != set(DEFECT_ENTRY_KEYS) or not isinstance(ev, dict) or \
                        set(ev) != {"cases", "rules", "runs"} or not all(isinstance(ev[k], list) for k in ev) or \
                        d.get("status") != "open" or d.get("raised_by") != "business-owner" or \
                        d.get("routed_via") is not None or d.get("history") != [] or d.get("from_review_record") != rid:
                    out.append("R20: defect %r must be a full defects.yaml entry (id, class, evidence {cases, rules, "
                               "runs}, owner_stage, proposed_correction, status open, raised_by business-owner, "
                               "routed_via null, history []) with from_review_record %s"
                               % ((d or {}).get("id") if isinstance(d, dict) else d, rid))
            notes = {str(x["case_id"]): x["note"] for x in disputed}
            for d in disp:
                cases = (d.get("evidence") or {}).get("cases") or []
                if fb and (len(cases) != 1 or d.get("proposed_correction") != notes.get(str(cases[0]))):
                    out.append("R21: dispute defect %r must carry the owner's note verbatim in proposed_correction"
                               % d.get("id"))
    entries = doc.get("declaration_entries")
    if "declaration_entries" in doc:
        if not isinstance(entries, list):
            out.append("R22: declaration_entries must be a list")
        else:
            if fb and len(entries) != len(fsamples):
                out.append("R22: declaration_entries must carry one entry per added sample")
            for i, e in enumerate(entries):
                fs = fsamples[i] if i < len(fsamples) else None
                files = (e or {}).get("files") if isinstance(e, dict) else None
                want = sample_file_path(final_run_id, fs) if fs else None
                ok = (isinstance(e, dict) and set(e) == set(DECLARATION_ENTRY_KEYS)
                      and e.get("added_via") == "prebuild-simulation-review" and e.get("provided") == "real-sample"
                      and e.get("from_review_record") == rid
                      and (ff_actual is None or e.get("feedback_file") == ff_actual)
                      and isinstance(e.get("notes"), str) and isinstance(files, list) and len(files) == 1
                      and _pin_pair(files[0]) and pin_reason(run_root, files[0], "the declared sample") is None)
                if ok and fs is not None:
                    ok = (e.get("system_id") == fs.get("system_id") and e.get("source") == fs.get("source")
                          and e.get("permission") == fs.get("permission") and e.get("redaction") == fs.get("redaction")
                          and e.get("provided_by") == fs.get("provided_by") and files[0]["path"] == want
                          and e.get("notes") == (fs.get("what_should_happen") or ""))
                if not ok:
                    out.append("R23: declaration entry %d must be the review-added shape (system_id, provided "
                               "real-sample, files [the sample file at its sha], provided_by, permission, redaction, "
                               "notes, added_via prebuild-simulation-review, source, feedback_file, "
                               "from_review_record %s), labelled client data" % (i, rid))
    return out


# ---- rule list 3: the review decision's payload (prebuild-review-decision.template.yaml) ---------------

def _without_writer_keys(payload):
    p = dict(payload or {})
    for k in WRITER_KEYS:
        p.pop(k, None)
    return p


def validate_decision_payload(payload, actor, run_root, events):
    """Every rule of the rule block at the head of prebuild-review-decision.template.yaml for a human.decision
    PAYLOAD (and its ACTOR). The writer's own timing key is not part of the shape. Returns ["D<n>: ..."]."""
    p = _without_writer_keys(payload)
    out = []
    if (actor or {}).get("role") != "human" or (actor or {}).get("engine") != "human":
        out.append("D0: the review decision is a person's: actor {role: human, engine: human}")
    missing = [k for k in DECISION_KEYS if k not in p]
    if missing:
        out.append("D1: the payload field(s) %s are missing" % ", ".join(missing))
    extra = [k for k in p if k not in DECISION_KEYS]
    if extra:
        out.append("D2: the payload field(s) %s are not allowed" % ", ".join(str(k) for k in extra))
    if p.get("gate") != REVIEW_GATE:
        out.append("D3: gate must be exactly %s" % REVIEW_GATE)
    if "decision" in p:
        own = ("accepted" if p.get("cases_disputed") == [] and p.get("samples_added") == []
               else "changes-requested")
        if p.get("decision") not in ("accepted", "changes-requested") or p.get("decision") != own:
            out.append("D4: decision must be accepted when every case is accepted and no sample was added, else "
                       "changes-requested")
    rr = p.get("review_record")
    rec = {}
    if "review_record" in p:
        if not _pin_pair(rr) or not _re.match(r"^%s/review-\d{3}\.yaml$" % _re.escape(REVIEWS_DIR), rr["path"]):
            out.append("D5: review_record must be {path, sha256} with path %s/review-<nnn>.yaml" % REVIEWS_DIR)
        else:
            read = pinned_read(run_root, rr, "yaml", "the review record")
            try:
                earlier, superseded = logged_pin_conflict(events, rr), None
            except SupersededPinError as exc:
                earlier, superseded = None, str(exc)
            if read.state not in ("held", "unparsable"):
                out.append("D6: review_record %s is not on disk at its pinned sha (%s)" % (rr["path"], read.reason))
            elif superseded is not None:
                out.append("D6: the event log already pins a review record twice, so no decision is appended: %s"
                           % superseded)
            elif earlier is not None:
                # the record's "written once ... never edited afterwards": a logged pin is never superseded, so no
                # later decision may pin a path an earlier decision logged, at the same bytes or different ones
                esha = str(earlier["payload"]["review_record"].get("sha256"))
                out.append("D6: review_record %s is already pinned by the earlier decision %s at sha256 %s; a review "
                           "record is written once and never edited afterwards, and a logged pin is never "
                           "superseded, so no later decision may pin that path again, at the same bytes or different "
                           "ones (this decision pins %s sha256 %s; a new review is recorded at a new path)"
                           % (rr["path"], earlier.get("event_id"), esha[:12],
                              "the same" if esha == rr.get("sha256") else "a different", str(rr.get("sha256"))[:12]))
            else:
                rec = (read.content or {}) if read.ok else {}
                rec = rec if isinstance(rec, dict) else {}
    if rec:
        if "decision" in p and p.get("decision") != rec.get("decision"):
            out.append("D7: decision must equal the review record's decision")
        if "feedback_file" in p and p.get("feedback_file") != rec.get("feedback_file"):
            out.append("D8: feedback_file must equal the review record's feedback_file")
        if "source" in p and (p.get("source") not in ("page", "chat") or p.get("source") != rec.get("source")):
            out.append("D9: source must be page or chat and equal the review record's source")
        rpins = rec.get("pins") if isinstance(rec.get("pins"), dict) else {}
        diff = [k for k in PIN_KEYS if k in p and p.get(k) != rpins.get(k)]
        if diff:
            out.append("D10: %s must equal the review record's pins" % ", ".join(diff))
        for rule, key in (("D12", "cases_accepted"), ("D13", "cases_disputed"), ("D14", "samples_added")):
            if key in p and p.get(key) != rec.get(key):
                out.append("%s: %s must equal the review record's %s" % (rule, key, key))
    elif "source" in p and p.get("source") not in ("page", "chat"):
        out.append("D9: source must be page or chat and equal the review record's source")
    if "submission_event_id" in p or "manifest_sha256" in p:
        sub = next((e for e in events or [] if e.get("event_id") == p.get("submission_event_id")), None)
        if sub is None or sub.get("stage") != STAGE or sub.get("type") != "executor.submitted" or \
                (sub.get("payload") or {}).get("sha256") != p.get("manifest_sha256"):
            out.append("D11: manifest_sha256 must be the sha256 of the %s executor.submitted named by "
                       "submission_event_id" % STAGE)
    return out


# ---- rule list 4: a provisional-value confirmation (provisional-values-confirmation.template.yaml) -----

def design_set_pin(events):
    """The pin that binds the design set before stage 11's first submission: {path: DESIGN_SET, sha256} from the
    design-coherence gate's latest design-set-current check.passed (the writer re-ran that check on exactly those
    bytes when it recorded it), or None when the gate has recorded none."""
    # the design-set-current check's artifact is always the design set (scripts/_checks.py); its argv names it
    receipt = next((e for e in reversed(events or []) if e.get("stage") == "design-coherence"
                    and e.get("type") == "check.passed" and (e.get("payload") or {}).get("check") == "design-set-current"
                    and DESIGN_SET in ((e.get("payload") or {}).get("argv") or [])), None)
    if receipt is None:
        return None
    return {"path": DESIGN_SET, "sha256": (receipt.get("payload") or {}).get("target_sha256")}


def pinned_policy_for_confirmation(run_root, events, with_reason=False):
    """The decision policy a confirmation's open ids are read against: the latest submitted manifest's pinned
    policy (inputs.decision_policy), else (before the first submission) the design set's decision-policy entry, the
    design set itself bound by design_set_pin(). Every file is read through its pin (pinned_read) and used only when
    it still has the pinned sha256; there is no fall back from one source to another.
    Returns the policy, or None when it cannot be read as pinned; WITH_REASON=True returns (policy, reason), where
    the reason names the file. When stage 11 has a submission whose manifest has been replaced or is missing
    (submitted_manifest), the result is (None, None): P11 refuses the confirmation, and its open ids are not judged
    against the replaced file's policy or any other."""
    held = submitted_manifest(run_root, events)
    if held.state in ("replaced", "missing"):
        policy, reason = None, None
    elif held.state == "unreadable":
        policy, reason = None, held.reason
    elif held.held:
        pair = (held.manifest.get("inputs") or {}).get("decision_policy")
        r = pinned_read(run_root, pair, "yaml", "the submitted manifest's pinned decision policy")
        policy, reason = ((r.content or {}), None) if r.ok else (None, r.reason)
    else:
        policy, reason = _design_set_policy(run_root, events)
    return (policy, reason) if with_reason else policy


def _design_set_policy(run_root, events):
    """(policy, None) from the design set's decision-policy entry, both read through their pins; (None, reason)."""
    ds_pin = design_set_pin(events)
    if ds_pin is None:
        return None, ("nothing binds the design set %s before stage 11's first submission: the design-coherence "
                      "gate has recorded no design-set-current check.passed for it" % DESIGN_SET)
    r = pinned_read(run_root, ds_pin, "json", "the design set")
    if not r.ok:
        return None, r.reason
    entry = ((r.content or {}).get("stages") or {}).get("deepen-decision-rules") or {}
    f = next((x for x in entry.get("files") or [] if isinstance(x, dict) and x.get("role") == "decision-policy"), None)
    if f is None:
        return None, "the design set %s pins no decision policy (stage deepen-decision-rules, role decision-policy)" \
            % DESIGN_SET
    p = pinned_read(run_root, f, "yaml", "the design set's pinned decision policy")
    return ((p.content or {}), None) if p.ok else (None, p.reason)


def validate_provisional_confirmation(payload, actor, run_root, events):
    """Every rule of the rule block at the head of provisional-values-confirmation.template.yaml for a
    human.decision PAYLOAD (and its ACTOR). Returns ["P<n>: message", ...]."""
    p = _without_writer_keys(payload)
    out = []
    if (actor or {}).get("role") != "human" or (actor or {}).get("engine") != "human":
        out.append("P0: the confirmation is a person's: actor {role: human, engine: human}")
    missing = [k for k in PROVISIONAL_KEYS if k not in p]
    if missing:
        out.append("P1: the payload field(s) %s are missing" % ", ".join(missing))
    extra = [k for k in p if k not in PROVISIONAL_KEYS]
    if extra:
        out.append("P2: the payload field(s) %s are not allowed" % ", ".join(str(k) for k in extra))
    if p.get("gate") != PROVISIONAL_GATE:
        out.append("P3: gate must be exactly %s" % PROVISIONAL_GATE)
    if "decision" in p and p.get("decision") != "accepted":
        out.append("P4: decision must be exactly accepted (a person who does not accept a value states a changed "
                   "value instead)")
    if "rationale" in p and not isinstance(p.get("rationale"), str):
        out.append("P5: rationale must be a string")
    prm = p.get("parameters")
    # parameters.yaml is read only through the confirmation's own pin; while that pin is broken (P6, P7) the
    # confirmation is refused whole and its open ids are not judged against any parameters file
    params_doc = None
    pin_holds = False
    if "parameters" in p:
        if not _pin_pair(prm) or prm["path"] != PARAMETERS:
            out.append("P6: parameters must be {path, sha256} with path %s" % PARAMETERS)
        else:
            read = pinned_read(run_root, prm, "yaml", "parameters")
            if not read.ok:
                out.append("P7: parameters is not on disk at its pinned sha (%s)" % read.reason)
            else:
                pin_holds = True
                params_doc = read.content if isinstance(read.content, dict) else {}
    held = submitted_manifest(run_root, events)
    sub = held.submission
    submitted_params = ((held.manifest.get("rules") or {}).get("parameters") or {}).get("sha256") if held.held \
        else None
    msha = p.get("manifest_sha256")
    if "manifest_sha256" in p:
        if sub is None and msha is not None:
            out.append("P8: manifest_sha256 must be null when recorded before stage 11's first submission")
        elif sub is not None and msha is not None and msha != (sub.get("payload") or {}).get("sha256"):
            out.append("P9: manifest_sha256 must be the sha256 of stage 11's latest executor.submitted")
        elif sub is not None and msha is None:
            # the strict reading (the design owner's ruling): once stage 11 has any executor.submitted, a
            # confirmation carries the latest one's sha256, also during a later round before that round submits
            out.append("P10: stage 11 already has an executor.submitted, so it is recorded after submission and "
                       "manifest_sha256 must be the latest one's sha256; null is accepted only before the stage's "
                       "first submission")
        if sub is not None and msha is not None and pin_holds and held.held and \
                prm.get("sha256") != submitted_params:
            out.append("P11: recorded after submission, parameters.sha256 must be the submitted manifest's pinned "
                       "parameters sha")
    if sub is not None and held.state in ("replaced", "missing"):
        # the design owner's ruling (2026-09-28): the submitted parameters pin lives in the submitted manifest's
        # bytes; once the next round has replaced that file there is nothing to confirm against, so refuse
        out.append("P11: the submitted manifest has been replaced by the next round's (%s), so the submitted "
                   "manifest's pinned parameters sha cannot be read; confirm values before the next round runs, or "
                   "after it submits" % held.reason)
    elif sub is not None and held.state == "unreadable":
        out.append("P11: the submitted manifest cannot be read as submitted (%s), so its pinned parameters sha "
                   "cannot be read" % held.reason)
    policy, policy_reason = pinned_policy_for_confirmation(run_root, events, with_reason=True)
    # None: either the submitted manifest is gone (P11 above refuses the confirmation), or the pinned policy is not
    # the pinned file (the reason, naming it, refuses under P13 / P16 below); its open ids are not judged
    value_opens = None if policy is None else {o.get("id") for o in policy.get("open_register") or []
                                               if isinstance(o, dict) and o.get("kind") == "value"}
    entries = None if params_doc is None else {e.get("open_id") for e in (params_doc.get("parameters") or [])
                                               if isinstance(e, dict)}
    confirmed, changed = p.get("confirmed"), p.get("changed")
    if policy_reason and isinstance(confirmed, list) and confirmed:
        out.append("P13: each confirmed id must be a value open of the pinned decision policy, but that policy is "
                   "used only as pinned: %s" % policy_reason)
    if policy_reason and isinstance(changed, list) and changed:
        out.append("P16: each changed id must be a value open of the pinned decision policy, but that policy is used "
                   "only as pinned: %s" % policy_reason)
    if "confirmed" in p:
        if not isinstance(confirmed, list):
            out.append("P12: confirmed must be a list of open ids")
        else:
            for oid in confirmed:
                if value_opens is not None and (oid not in value_opens or (entries is not None
                                                                           and oid not in entries)):
                    out.append("P13: confirmed open %r is not a value open of the pinned policy with a parameters "
                               "entry" % (oid,))
            if len(set(map(str, confirmed))) != len(confirmed):
                out.append("P14: confirmed names an open id more than once")
    conf = confirmed if isinstance(confirmed, list) else []
    if "changed" in p:
        if not isinstance(changed, list):
            out.append("P15: changed must be a list of {open_id, value}")
        else:
            seen = []
            for ch in changed:
                if not isinstance(ch, dict) or set(ch) != {"open_id", "value"}:
                    out.append("P15: each changed entry must be exactly {open_id, value}")
                    continue
                oid = ch.get("open_id")
                if (value_opens is not None and (oid not in value_opens or (entries is not None
                                                                            and oid not in entries))) or oid in seen:
                    out.append("P16: changed open %r must be a value open of the pinned policy with a parameters "
                               "entry, named once" % (oid,))
                if oid in conf:
                    out.append("P17: open %r is both confirmed and changed" % (oid,))
                if not _trim_nonempty(ch.get("value")):
                    out.append("P18: changed open %r needs a non-empty value (a string)" % (oid,))
                seen.append(oid)
    if isinstance(confirmed, list) and isinstance(changed, list) and not confirmed and not changed:
        out.append("P19: at least one of confirmed and changed must be non-empty")
    return out


# ---- the design-coherence gate's escalated residuals (plan 6.3's open-items row, from manifest.opens) ----

FINDINGS_BLOCK_RE = _re.compile(r"<!--\s*FINDINGS-REGISTER\s*(.*?)-->", _re.S)


def _reverify_receipt(run_root, receipt):
    """Re-run a check.passed receipt now through the writer's own re-verification (scripts/_checks.py
    reverify_check_passed: the artifact re-hashed against target_sha256 and the check re-run on its argv); the
    reason it no longer holds, or None."""
    import importlib.util
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "_checks.py")
    if not os.path.isfile(path):
        return "scripts/_checks.py, which re-runs a recorded check, is not installed"
    spec = importlib.util.spec_from_file_location("_pbs_checks_reverify", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    try:
        mod.reverify_check_passed(json.loads(json.dumps(receipt)), base_dir=run_root)
    except mod.CheckError as exc:
        return str(exc)
    return None


def gate_residuals(run_root, events):
    """The design-coherence gate's escalated residuals, verbatim: each machine-block entry with status escalated in
    the findings register the gate's current closure check ran against (the coherence-matrix-closure check.passed
    bound to the gate's latest submission names it as its second argument). [] when the gate has none, or has no
    closure receipt naming a register (nothing is read then).
    The receipt records no sha256 for the register (only the matrix's, as target_sha256), so no pin binds the
    register's bytes. Before reading it, the receipt is re-verified now (the matrix re-hashed against the recorded
    sha and the closure check re-run on the register as it is): the register read is one that closes the recorded
    matrix. A register the receipt names that is missing, or a receipt that no longer holds, is refused
    (PrebuildError naming the file), never skipped."""
    gate = latest_submission(events, "design-coherence")
    if gate is None:
        return []
    gsha = (gate.get("payload") or {}).get("sha256")
    receipt = next((e for e in reversed(events or []) if e.get("stage") == "design-coherence"
                    and e.get("type") == "check.passed"
                    and (e.get("payload") or {}).get("check") == "coherence-matrix-closure"
                    and (e.get("payload") or {}).get("target_sha256") == gsha), None)
    argv = ((receipt or {}).get("payload") or {}).get("argv") or []
    if len(argv) < 2:
        return []
    reg = os.path.join(run_root, argv[1])
    if not os.path.isfile(reg):
        raise PrebuildError("the design-coherence gate's findings register %s, named by its closure check.passed %s, "
                            "is missing; its escalated residuals cannot be carried into opens"
                            % (argv[1], receipt.get("event_id")))
    # the register's bytes are read ONCE, before the receipt is re-verified; after the re-run the file is re-hashed
    # and must still be those bytes, and the residuals are parsed from exactly the bytes the re-run checked
    with open(reg, "rb") as f:
        checked = f.read()
    why = _reverify_receipt(run_root, receipt)
    if why:
        raise PrebuildError("the design-coherence gate's findings register %s is not bound to the gate's recorded "
                            "closure check (check.passed %s no longer holds when re-run: %s); its escalated residuals "
                            "are carried into opens only from a register that closes the recorded matrix"
                            % (argv[1], receipt.get("event_id"), why))
    try:
        with open(reg, "rb") as f:
            after = f.read()
    except OSError as exc:
        raise PrebuildError("the design-coherence gate's findings register %s cannot be read again after its closure "
                            "check was re-run: %s" % (argv[1], exc))
    if after != checked:
        raise PrebuildError("the design-coherence gate's findings register %s changed while its closure check.passed "
                            "%s was being re-run; its residuals are carried only from the bytes that check ran on"
                            % (argv[1], receipt.get("event_id")))
    m = FINDINGS_BLOCK_RE.search(checked.decode("utf-8", errors="replace"))
    if not m:
        return []
    try:
        doc = yaml_mod().safe_load(m.group(1)) or {}
    except Exception as exc:  # noqa: BLE001 - a malformed block is a setup error
        raise PrebuildError("the gate's findings register %s cannot be read: %s" % (argv[1], exc))
    return [{"id": f.get("finding_id"), "kind": "gate-residual", "source": argv[1], "residual": f}
            for f in (doc.get("findings") or []) if isinstance(f, dict) and f.get("status") == "escalated"]
