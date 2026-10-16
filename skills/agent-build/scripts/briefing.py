#!/usr/bin/env python3
"""Agent Build: the briefing (pins P1, P2b, P8, G17-G20, H11, I4).

Reads everything under R/inputs/, R/access.json, R/config.json and the
preferences file named in config.json, and writes:
  R/briefing.md                       the four sections (template: templates/briefing.md)
  R/what-we-still-need-to-know.json   gaps; items with source "briefing" are this script's
  R/capabilities.json                 the nine P8 fields, set level and per process
  R/records/design-location.json      where the Agent Design output was found and each reading taken
  R/records/capability-basis.json     the reason behind uses_model / produces_numbers, and the internal id
                                      behind each person-facing gap line
  R/inputs/environment.json           only with --read-env

Every read is guarded: a damaged input becomes a "could not be read" line and
a gap, never a failure. Appends nothing to the run log (the session appends
briefing.written).

Usage:
  briefing.py --run R [--read-env]

Exit: 0 briefing written; 2 usage error (bad flags, --run missing or not a folder).
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import _records  # noqa: E402
import _locate  # noqa: E402
from _records import OK, USAGE  # noqa: E402

NMI = "needs_more_information"
NMI_PROSE = "needs more information"

# The usual places; the design is found by _locate.locate_design wherever it landed.
HANDOFF_REL = "inputs/agent-design/outputs/agent-build-inputs.json"
SUITE_REL = "inputs/agent-design/stages/test-suite/test-suite.json"
AGENT_REL = "inputs/agent-design/stages/architecture/agent.yaml"
GAPS_NAME = "what-we-still-need-to-know.json"
MAX_DOC_SECTIONS = 12
# Where the design was found and every reading taken (builder record).
DESIGN_RECORD = os.path.join("records", "design-location.json")
# The reading behind uses_model / produces_numbers (capabilities.json keys are fixed), and the internal id
# behind each person-facing gap line (builder record).
BASIS_RECORD = os.path.join("records", "capability-basis.json")

# The 14 hand-off fields of agent-design.handoff/1 and their JSON types.
HANDOFF_FIELDS = (
    ("open_items", list), ("access_assumptions", list), ("stand_ins", list),
    ("rules_needing_ratification", list), ("critical_cases_failed_or_open", list),
    ("primitive_form", dict), ("identity_runtime_opens", list), ("evidence_strength", list),
    ("upstream_failing_checks", list), ("capability_needs", list),
    ("stage_input_hashes", dict), ("outputs", list), ("bundle", dict),
    ("platform_question", str),
)
# The four suite fields read from test-suite.json.
SUITE_FIELDS = (
    ("status", str), ("numeric_floors", (str, dict, list)),
    ("omitted_layers", list), ("cases", list),
)
# Fields Agent Design's hand-off writer adds beyond the table; known, so never "unknown field".
WRITER_FIELDS = ("version", "stage_origins", "stages_without_files", "platform_answer",
                 "status_reasons", "missing_information", "readings", "simulation_coverage")
KNOWN_FIELDS = set(name for name, _ in HANDOFF_FIELDS + SUITE_FIELDS) | set(WRITER_FIELDS)

ENV_TOOLS = ("python3", "git", "curl", "docker", "node")

TOPICS = (
    "Process and deliverable",
    "Target environment and packaging",
    "Which steps need judgement",
    "The review experience",
)

CAPABILITY_FIELDS = (
    "uses_model", "produces_numbers", "writes", "calls_external_systems",
    "ships_as_docker_image", "runtime", "runtime_on_node", "needs_registry", "engine",
)

FALLBACK_TEMPLATE = (
    "# Briefing\n\n## What the design implies\n\n{{implies}}\n\n## What's thin\n\n{{thin}}\n\n"
    "## First questions\n\n{{questions}}\n\n## Carried over from earlier runs\n\n{{carried}}\n"
)


class Gaps(object):
    """Collects briefing gap items with stable ids brief-<field>-<n>."""

    def __init__(self):
        self.items = []
        self.counts = {}

    def add(self, field, summary, scope=None, process=None, adjudicator=None):
        n = self.counts.get(field, 0) + 1
        self.counts[field] = n
        self.items.append({
            "id": "brief-%s-%d" % (field, n),
            "source": "briefing",
            "field": field,
            "scope": scope,
            "process": process,
            "summary": summary,
            "adjudicator": adjudicator,
        })


def prose(value):
    if value == NMI:
        return NMI_PROSE
    if value is True:
        return "yes"
    if value is False:
        return "no"
    if value is None:
        return "none (dropped)"
    if isinstance(value, list):
        return ", ".join(str(v) for v in value) if value else "none"
    return str(value)


def with_basis(value, reading):
    """A capability as one person-facing line: the value and, after it, the reason for it."""
    why = reading.get("basis") if isinstance(reading, dict) else None
    return "%s (%s)" % (prose(value), why) if why else prose(value)


def refer(basis, field, ref, text):
    """Record the internal id behind a person-facing line (builder record only)."""
    if ref is not None:
        basis["references"].append({"field": field, "id": ref, "text": text})


def note_overrides(caps, basis):
    """After brief.json overrides: a value that differs from the derived reading is the person's."""
    for f in ("uses_model", "produces_numbers"):
        entry = basis["capabilities"].get(f)
        if entry is not None and caps["capabilities"].get(f) != entry["value"]:
            basis["capabilities"][f] = {"value": caps["capabilities"][f], "derived": entry["value"],
                                        "basis": "confirmed by the person in the brief (derived: %s)" % entry["basis"]}
    for pid, entry in caps["per_process"].items():
        mine = basis["per_process"].get(pid, {}).get("uses_model")
        if mine is not None and entry.get("uses_model") != mine["value"]:
            basis["per_process"][pid]["uses_model"] = {
                "value": entry["uses_model"], "derived": mine["value"],
                "basis": "confirmed by the person in the brief (derived: %s)" % mine["basis"]}


def read_fields(path, fields, present, normalise=None, readings=None):
    """Return ({field: value}, {field: reason}, [unknown keys]) for one JSON input. With normalise
    (a _locate.normalise_* function), key names are normalised first and each mapping is recorded
    in readings."""
    values, problems, unknown = {}, {}, []
    if not present or path is None:
        for name, _ in fields:
            problems[name] = "missing"
        return values, problems, unknown
    obj, err = _records.load_json(path)
    if err is not None and err.startswith("missing"):
        for name, _ in fields:
            problems[name] = "missing"
        return values, problems, unknown
    if err is None and normalise is not None and isinstance(obj, dict):
        obj = normalise(obj, readings if readings is not None else [])
    if err is not None or not isinstance(obj, dict):
        for name, _ in fields:
            problems[name] = "unreadable"
        return values, problems, unknown
    for name, kind in fields:
        if name not in obj:
            problems[name] = "missing"
        elif not isinstance(obj[name], kind) or isinstance(obj[name], bool):
            problems[name] = "unreadable"
        else:
            values[name] = obj[name]
    for key in sorted(obj):
        if key not in KNOWN_FIELDS:
            unknown.append(key)
    return values, problems, unknown


def load_config(run, thin, gaps):
    path = os.path.join(run, "config.json")
    if not os.path.exists(path):
        thin.append("- could not be read: config.json (missing)")
        gaps.add("config", "config.json is missing; engine, blocks and processes are not known")
        return {}
    obj, err = _records.load_json(path)
    if err is not None or not isinstance(obj, dict):
        thin.append("- could not be read: config.json (unreadable)")
        gaps.add("config", "config.json could not be read; engine, blocks and processes are not known")
        return {}
    return obj


def cannot_read(thin, gaps, name, field, summary):
    """One "could not be read" line under What's thin and exactly one gap item for it."""
    thin.append("- could not be read: %s (unreadable)" % name)
    gaps.add(field, summary)


def load_agent(run, thin, gaps, loc=None):
    """The design's agent.yaml (Agent Design writes it as JSON; simple YAML is read too, recorded).
    None when absent or unreadable; an unreadable file is a "could not be read" line and a gap."""
    loc = loc or _locate.locate_design(run)
    path = loc.agent
    if path is None or not os.path.lexists(path):
        return None
    obj, err = _locate.read_agent(path, loc.readings)
    if err is not None or not isinstance(obj, dict):
        rel = loc.rel(path)
        cannot_read(thin, gaps, rel, "agent.yaml",
                    "%s could not be read; the design's parts, writes and hand-overs are not known" % rel)
        return None
    return obj


def person_documents(loc, gaps):
    """A person's documents beside or instead of the design, read as design evidence: headed sections and
    bullets summarised structurally, the run-relative path and an excerpt. Lines for What the design implies."""
    if not loc.documents:
        return []
    lines = ["", "### A person's document, read as design evidence"]
    for path in loc.documents:
        rel = loc.rel(path)
        doc = _locate.read_document(path)
        if doc.get("error"):
            lines.append("- %s: could not be read (%s)" % (rel, doc["error"]))
            continue
        headed = [sec for sec in doc["sections"] if sec["heading"]]
        lines.append("- %s: %d headed section(s), %d bullet point(s), %d words; read it with the person, "
                     "the design facts above do not come from it" % (rel, len(headed), doc["bullets"], doc["words"]))
        for sec in doc["sections"][:MAX_DOC_SECTIONS]:
            name = sec["heading"] or "(before the first heading)"
            text = "  - %s: %d bullet point(s)" % (name, sec["bullets"])
            if sec["first"]:
                text += "; first: %s" % sec["first"]
            lines.append(text)
        if len(doc["sections"]) > MAX_DOC_SECTIONS:
            lines.append("  - and %d more section(s)" % (len(doc["sections"]) - MAX_DOC_SECTIONS))
        if doc["excerpt"]:
            lines.append("  - Excerpt: %s" % doc["excerpt"])
        loc.readings.append({"what": rel, "reading": "a person's document, read as design evidence",
                             "why": "a text document beside or instead of the Agent Design output; "
                                    "%d headed section(s), %d bullet point(s)" % (len(headed), doc["bullets"])})
        gaps.add("person-document", "a person's document (%s) was read as design evidence; confirm with the "
                 "person what it settles" % os.path.basename(path), scope="whole design")
    return lines


def load_overrides(run, thin, gaps):
    """brief.json capabilities (G6): person-confirmed values that win over the derived ones."""
    path = os.path.join(run, "brief.json")
    if not os.path.lexists(path):
        return {}
    obj, err = _records.load_json(path)
    if err is not None or not isinstance(obj, dict):
        cannot_read(thin, gaps, "brief.json", "brief.json",
                    "brief.json could not be read; person-confirmed capabilities are not applied")
        return {}
    over = obj.get("capabilities")
    if over is None:
        return {}
    if not isinstance(over, dict):
        cannot_read(thin, gaps, "brief.json capabilities", "brief.json",
                    "brief.json capabilities is not an object; person-confirmed capabilities are not applied")
        return {}
    return over


def apply_overrides(caps, over):
    """Flat brief.json capabilities win at set level and for every process; a set-level
    uses_model reaches a process only when it is false (no model anywhere). per_process
    entries win for their own process."""
    flat = dict((k, v) for k, v in over.items() if k in CAPABILITY_FIELDS)
    caps["capabilities"].update(flat)
    per = over.get("per_process") if isinstance(over.get("per_process"), dict) else {}
    for pid, entry in caps["per_process"].items():
        for k, v in flat.items():
            if k != "uses_model" or v is False:
                entry[k] = v
        mine = per.get(pid)
        if isinstance(mine, dict):
            entry.update((k, v) for k, v in mine.items() if k in CAPABILITY_FIELDS)
    return caps


def process_ids(config, handoff):
    procs = config.get("processes")
    if isinstance(procs, list) and procs and all(isinstance(p, str) and p for p in procs):
        return list(procs)
    out = []
    for entry in handoff.get("evidence_strength", []):
        if isinstance(entry, dict) and isinstance(entry.get("process"), str) and entry["process"] not in out:
            out.append(entry["process"])
    return out


def strength_text(entry):
    """Evidence strength as the hand-off records it: a `strength` word, or the writer's counts (pin J1)."""
    if isinstance(entry.get("strength"), str) and entry["strength"]:
        return entry["strength"]
    obs, src, states = entry.get("observation_count"), entry.get("source_count"), entry.get("evidence_states")
    if not isinstance(obs, int) or isinstance(obs, bool) or not isinstance(src, int) or isinstance(src, bool):
        return None
    text = "%d observations from %d sources" % (obs, src)
    if isinstance(states, dict) and states:
        text += " (%s)" % ", ".join("%s %s" % (NMI_PROSE if k == NMI else k, states[k]) for k in sorted(states))
    return text


def _step_process(step):
    return step.split(":", 1)[0] if isinstance(step, str) and ":" in step else None


# The fixed step classes (the transformation map's closed set). A classification is read only from a structural
# record: a part's routing.marked holding exactly one class for every step the part owns, or a {step: class} map.
# A mark written as prose is never searched for words; it reads as unclassified, with the reason recorded.
STEP_CLASSES = {"script-eligible": "script", "judgment-required": "judgement", "judgement-required": "judgement",
                "code": "script", "model": "judgement"}
EXPR_KINDS = ("literal", "read", "compare", "and", "or", "not", "in", "is_null", "is_not_applicable", "calc")
COMPARE_OPS = ("==", "!=", "<", "<=", ">", ">=")
ORDERING_OPS = ("<", "<=", ">", ">=")
RULES_LAYOUT = ("stages/rules", "outputs/design/rules", "design/rules", "outputs/shareable/design/rules",
                "shareable/design/rules", "rules")


def _class_of(value):
    return STEP_CLASSES.get(value.strip().lower()) if isinstance(value, str) else None


def step_classes(part, pid=None):
    """({step: class or None} for the steps the part owns (only process pid's when given), prose flag).
    Unreadable ownership (owns_steps missing, not a list, or holding a non-string) is reported as the step key
    None with class None, so coverage is never complete."""
    owned = part.get("owns_steps")
    readable = isinstance(owned, list) and owned and all(isinstance(x, str) and x for x in owned)
    steps = list(owned) if readable else []
    if pid is not None:
        steps = [x for x in steps if _step_process(x) == pid]
    routing = part.get("routing") if isinstance(part.get("routing"), dict) else {}
    mark = routing.get("marked")
    whole = _class_of(mark)
    out, prose_mark = {}, False
    for step in steps:
        if whole is not None:
            out[step] = whole
        elif isinstance(mark, dict):
            out[step] = _class_of(mark.get(step))
        else:
            out[step] = None
    if whole is None and isinstance(mark, str) and mark.strip():
        prose_mark = True
    if not readable:
        out[None] = None
    return out, prose_mark


def _cases_list(suite):
    cases = suite.get("cases") if isinstance(suite, dict) else None
    return [c for c in cases if isinstance(c, dict)] if isinstance(cases, list) else None


def uses_model_reading(parts, cases=None, needs=None, pid=None):
    """(value, basis): True on structural evidence of a judgement step (a model-routed part, a step classed
    judgement-required in a structural record, test cases carrying judgement answers); False only when every part
    is code-routed and every step it owns is classed script-eligible; otherwise needs_more_information."""
    parts = [p for p in parts if isinstance(p, dict)] if isinstance(parts, list) else []
    classes, prose_parts = {}, []
    for part in parts:
        routing = part.get("routing") if isinstance(part.get("routing"), dict) else {}
        if routing.get("kind") == "model":
            return True, "part %s is routed to a model" % part.get("id")
        mine, prose_mark = step_classes(part, pid)
        classes.update(mine)
        if prose_mark:
            prose_parts.append(str(part.get("id")))
        if "judgement" in mine.values():
            return True, "part %s has a step classed judgement-required" % part.get("id")
    for need in needs or []:
        if isinstance(need, dict) and _class_of(need.get("marked")) == "judgement":
            return True, "a capability need classes a step judgement-required"
    judged = [c for c in cases or [] if isinstance(c.get("judgment_answers"), list) and c["judgment_answers"]]
    if judged:
        return True, "%d test case(s) carry judgement answers for a step" % len(judged)
    if not parts:
        return NMI, "no parts of the agent were read, and nothing else in the design shows a judgement step"
    kinds = [(p.get("routing") or {}).get("kind") if isinstance(p.get("routing"), dict) else None for p in parts]
    odd = [str(k) for k in kinds if k not in ("code", "model")]
    if odd:
        return NMI, "a part's routing is not code or model (%s)" % ", ".join(sorted(set(odd)))
    if not classes:
        return NMI, "no step owned by the parts could be read, so it is not shown that every step is script-eligible"
    unclassified = [x for x, c in classes.items() if c is None]
    if None in classes:
        return NMI, ("a part's owned steps could not be read, so it is not shown that every step is "
                     "script-eligible")
    if unclassified:
        why = ("every part is code-routed, but code routing sets the order, not whether a step inside needs "
               "judgement; %d of %d step(s) carry no single classification" % (len(unclassified), len(classes)))
        if prose_parts:
            why += " (the mark on %s is prose, read it with the person)" % ", ".join(prose_parts)
        return NMI, why
    return False, "every part is code-routed and every step is classed script-eligible"


def uses_model(parts):
    return uses_model_reading(parts)[0]


def _is_number(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _flat_needs(entries):
    """capability_needs entries, flattened: nested per part ({"part", "needs": [...]}) or flat legacy."""
    out = []
    for entry in entries if isinstance(entries, list) else []:
        if isinstance(entry, dict) and isinstance(entry.get("needs"), list):
            out.extend(entry["needs"])
        else:
            out.append(entry)
    return out


def _needs_for(entries, part_ids=None):
    """Flattened needs, only those of the named parts when part_ids is given (flat legacy needs always count)."""
    out = []
    for entry in entries if isinstance(entries, list) else []:
        if isinstance(entry, dict) and isinstance(entry.get("needs"), list):
            if part_ids is None or entry.get("part") in part_ids:
                out.extend(entry["needs"])
        else:
            out.append(entry)
    return out


def _row_types(cases):
    """{token: set of "number" / "other"} from the values the suite's rows give each token."""
    types = {}
    for c in cases or []:
        for row in c.get("rows") if isinstance(c.get("rows"), list) else []:
            values = row.get("values") if isinstance(row, dict) else None
            for token, cell in (values.items() if isinstance(values, dict) else []):
                v = cell.get("value") if isinstance(cell, dict) else cell
                if v is None:
                    continue
                types.setdefault(token, set()).add("number" if _is_number(v) else "other")
    return types


class _Scan(object):
    """One pass over the rules' typed expressions: the first quantity found, unrecognised content, and comparisons
    or outputs whose type nothing shows."""

    def __init__(self, types):
        self.types = types
        self.quantity = None
        self.unrecognised = 0
        self.unsettled = 0

    def value(self, node):
        """"number", "other" or None (type not shown) for one expression node; records what it finds."""
        if not isinstance(node, dict) or node.get("kind") not in EXPR_KINDS:
            self.unrecognised += 1
            return None
        kind = node["kind"]
        if kind == "literal":
            if "value" not in node:
                self.unrecognised += 1
                return None
            return "number" if _is_number(node["value"]) else "other"
        if kind == "read":
            seen = self.types.get(node.get("token"), set())
            return "number" if "number" in seen else "other" if seen == {"other"} else None
        if kind == "calc":
            self.quantity = self.quantity or "a rule calculates an amount"
            return "number"
        if kind in ("is_null", "is_not_applicable"):
            if not isinstance(node.get("token"), str):
                self.unrecognised += 1
            return "other"
        if kind == "compare":
            op = node.get("op")
            if op not in COMPARE_OPS:
                self.unrecognised += 1
                return "other"
            sides = [self.value(node.get("left")), self.value(node.get("right"))]
            if op in ORDERING_OPS:
                self.quantity = self.quantity or "a rule compares quantities (%s)" % op
            elif "number" in sides:
                self.quantity = self.quantity or "a rule compares numbers"
            elif "other" not in sides:
                self.unsettled += 1
            return "other"
        if kind == "not":
            self.value(node.get("arg"))
            return "other"
        if kind in ("and", "or"):
            args = node.get("args")
            if not isinstance(args, list) or len(args) < 2:
                self.unrecognised += 1
                return "other"
            for a in args:
                self.value(a)
            return "other"
        # in: membership of a value in a literal list
        items = node.get("list")
        side = self.value(node.get("value_of"))
        if not isinstance(items, list):
            self.unrecognised += 1
        elif any(_is_number(i) for i in items) or side == "number":
            self.quantity = self.quantity or "a rule checks a number against a list"
        return "other"

    def rule(self, rule):
        expr = rule.get("expression") if isinstance(rule, dict) else None
        if not isinstance(expr, dict) or not isinstance(expr.get("test"), dict) \
                or not isinstance(expr.get("sets", {}), dict):
            self.unrecognised += 1
            return
        self.value(expr["test"])
        for node in (expr.get("sets") or {}).values():
            out = self.value(node)
            if out == "number":
                self.quantity = self.quantity or "a rule sets a number as its result"
            elif out is None:
                self.unsettled += 1


def produces_numbers_reading(handoff, suite, rules, cases_filter=None):
    """(value, basis): True when any capability need, expectation or rule computes, compares or sets a quantity;
    False only when the rules are read, every rule's test and outputs are recognised expressions, and none yields
    or compares a quantity; otherwise needs_more_information."""
    for need in _flat_needs(handoff.get("capability_needs", [])):
        # only the need's own name (the `need` field, or a bare string need) says what it is; descriptive fields
        # such as `surface` or `portable_fallback` never count
        name = need if isinstance(need, str) else need.get("need") if isinstance(need, dict) else None
        if isinstance(name, str) and name.strip().lower() == "calculation tool":
            return True, "the design asks for a calculation tool"
    cases = _cases_list(suite)
    if cases is not None and cases_filter is not None:
        cases = [c for c in cases if cases_filter(c)]
    for c in cases or []:
        outcomes = [c.get("expected")] + (c.get("allowed_alternatives")
                                          if isinstance(c.get("allowed_alternatives"), list) else [])
        for outcome in outcomes:
            if not isinstance(outcome, dict):
                continue
            if outcome.get("derivations"):
                return True, "a test case expects a derived amount"
            values = outcome.get("values")
            if isinstance(values, dict) and any(_is_number(v) for v in values.values()):
                return True, "a test case expects a number"
    if rules is None:
        if cases is None:
            return NMI, "neither the test cases nor the design's rules could be read"
        return NMI, ("the test cases expect labels only and the design's rules were not with the hand-over; "
                     "label-only cases do not show that no quantity is produced")
    scan = _Scan(_row_types(cases))
    for r in rules:
        scan.rule(r)
    if scan.quantity:
        return True, scan.quantity
    if not rules:
        return NMI, "the design's rules list is empty, so it is not shown that no quantity is produced"
    if scan.unrecognised:
        return NMI, "%d part(s) of the rules could not be read as expressions" % scan.unrecognised
    if scan.unsettled:
        return NMI, ("%d rule comparison(s) or result(s) use values whose type nothing shows, so whether they are "
                     "amounts is not known" % scan.unsettled)
    if cases is None:
        return NMI, "the test cases could not be read"
    return False, "every rule was read and none computes, compares or sets a quantity"


def produces_numbers(handoff, suite, rules=None):
    return produces_numbers_reading(handoff, suite, rules)[0]


def load_rules(loc):
    """(rules, path, set_aside): the design's rules list from the design root (stages/rules/rules.json or a
    published copy). Every candidate is checked to resolve inside the run before it is opened; one that resolves
    outside is set aside and named in set_aside."""
    set_aside = []
    if loc is None or not loc.root:
        return None, None, set_aside
    top = getattr(loc, "run")
    for rel in RULES_LAYOUT:
        path = os.path.join(loc.root, rel, "rules.json")
        if not os.path.lexists(path):
            continue
        if not _locate.contained(top, path):
            set_aside.append(path)
            continue
        if not os.path.isfile(path):
            continue
        obj, err = _records.load_json(path)
        rules = obj.get("rules") if isinstance(obj, dict) else obj if isinstance(obj, list) else None
        if err is None and isinstance(rules, list):
            # every entry is kept: one that is not a rule object is unrecognised content for the reading
            return list(rules), path, set_aside
    return None, None, set_aside


def capabilities(config, handoff, suite, agent, processes, rules=None, basis=None):
    """The P8 fields, set level and per process. With basis (a dict), the reading behind uses_model and
    produces_numbers is filled in: {"capabilities": {field: {value, basis}}, "per_process": {pid: {...}}}."""
    basis = basis if basis is not None else {}
    basis.setdefault("capabilities", {})
    basis.setdefault("per_process", {})
    caps = {}
    engine = config.get("engine")
    caps["engine"] = engine if isinstance(engine, str) and engine else NMI
    parts = agent.get("parts") if agent else None
    header = agent.get("header") if agent else None
    cases = _cases_list(suite)
    needs = _flat_needs(handoff.get("capability_needs", []))
    value, why = uses_model_reading(parts, cases, needs)
    caps["uses_model"] = value
    basis["capabilities"]["uses_model"] = {"value": value, "basis": why}
    value, why = produces_numbers_reading(handoff, suite, rules)
    caps["produces_numbers"] = value
    basis["capabilities"]["produces_numbers"] = {"value": value, "basis": why}
    writes = header.get("proposed_writes") if isinstance(header, dict) else None
    caps["writes"] = (len(writes) > 0) if isinstance(writes, list) else NMI
    assumptions = handoff.get("access_assumptions")
    if isinstance(assumptions, list):
        systems = []
        for a in assumptions:
            if isinstance(a, dict) and isinstance(a.get("system"), str) and a["system"] not in systems:
                systems.append(a["system"])
        # an access assumption without a readable system name is missing, never "no systems"
        unnamed = [a for a in assumptions if not (isinstance(a, dict) and isinstance(a.get("system"), str) and a["system"])]
        caps["calls_external_systems"] = NMI if unnamed else systems
    else:
        caps["calls_external_systems"] = NMI
    blocks = config.get("blocks") if isinstance(config.get("blocks"), dict) else {}
    layer = blocks.get("execution_layer")
    if isinstance(layer, dict) and layer.get("dropped") is True:
        caps["ships_as_docker_image"] = False
    elif isinstance(layer, dict) and isinstance(layer.get("choice"), str):
        caps["ships_as_docker_image"] = layer["choice"] == "docker"
    else:
        caps["ships_as_docker_image"] = NMI
    runtime = blocks.get("runtime")
    dropped = isinstance(runtime, dict) and runtime.get("dropped") is True
    if dropped:
        caps["runtime"] = None
    elif isinstance(runtime, dict) and isinstance(runtime.get("choice"), str):
        caps["runtime"] = runtime["choice"]
    else:
        caps["runtime"] = NMI
    caps["runtime_on_node"] = False if dropped else NMI
    caps["needs_registry"] = False if dropped else NMI

    per_process = {}
    for pid in processes:
        entry = dict(caps)
        my_cases = [c for c in cases or [] if _step_process(c.get("decision")) == pid]
        if isinstance(parts, list):
            mine = [p for p in parts if isinstance(p, dict) and isinstance(p.get("owns_steps"), list)
                    and any(_step_process(s) == pid for s in p["owns_steps"])]
            ids = set(p.get("id") for p in mine)
            my_needs = _needs_for(handoff.get("capability_needs", []), ids)
            value, why = uses_model_reading(mine, my_cases, my_needs, pid)
        else:
            value, why = uses_model_reading(None, my_cases)
        entry["uses_model"] = value
        basis["per_process"][pid] = {"uses_model": {"value": value, "basis": why}}
        per_process[pid] = entry
    return {"capabilities": caps, "per_process": per_process}


def result_kinds(suite, pid=None):
    cases = suite.get("cases")
    if not isinstance(cases, list):
        return None
    kinds = []
    for case in cases:
        if not isinstance(case, dict):
            continue
        if pid is not None and _step_process(case.get("decision")) != pid:
            continue
        expected = case.get("expected")
        label = expected.get("label") if isinstance(expected, dict) else None
        if isinstance(label, str) and label not in kinds:
            kinds.append(label)
        if "exclusion" in case and "escalate" not in kinds:
            kinds.append("escalate")
    return kinds or None


def people_touchpoints(agent):
    header = agent.get("header") if agent else None
    handovers = header.get("handovers") if isinstance(header, dict) else None
    if not isinstance(handovers, list):
        return None
    out = []
    for h in handovers:
        if isinstance(h, dict) and h.get("kind") == "person_handover":
            where = "%s to %s" % (h.get("from"), h.get("to"))
            if where not in out:
                out.append(where)
    return out if out else ["no person hand-over in the design"]


def read_environment(run):
    tools = {}
    for name in ENV_TOOLS:
        found = shutil.which(name)
        version = None
        if found:
            try:
                proc = subprocess.run([found, "--version"], stdin=subprocess.DEVNULL,
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)
                text = (proc.stdout or proc.stderr).decode("utf-8", "replace").strip()
                version = text.splitlines()[0].strip() if text else None
            except (OSError, subprocess.SubprocessError):
                version = None
        tools[name] = {"found": bool(found), "version": version}
    folder = os.path.join(run, "inputs")
    if not os.path.isdir(folder):
        os.makedirs(folder)
    _records.write_record(os.path.join(folder, "environment.json"),
                          {"read_at": _records.now_iso(), "tools": tools})


def carried_over(run, config, gaps):
    path = config.get("preferences_path")
    if not isinstance(path, str) or not path:
        return ["- none"]
    full = path if os.path.isabs(path) else os.path.join(run, path)
    if not os.path.exists(full):
        return ["- none"]
    try:
        import prefs
        result = prefs.read(full)
        values = result["values"]
        withheld = list(result["withheld"])
        all_withheld = bool(result["all_withheld"])
        if not isinstance(values, dict):
            raise ValueError("values is not an object")
    except Exception as err:  # any failure of the reader is a gap, never a traceback
        gaps.add("preferences", "withheld preference: * (preferences file could not be read: %s)"
                 % type(err).__name__, scope="whole design")
        return ["- could not be read: preferences (unreadable)",
                "every carried-over value is withheld until the person resolves the malformed line"]
    if all_withheld:
        gaps.add("preferences", "withheld preference: * (a malformed line names no key)", scope="whole design")
        return ["every carried-over value is withheld until the person resolves the malformed line"]
    lines = []
    for key in sorted(values):
        v = values[key] if isinstance(values[key], dict) else {}
        lines.append("- %s: %s (source: %s, decided by %s, %s, run %s)" % (
            key, json.dumps(v.get("value"), sort_keys=True), v.get("source"), v.get("decided_by"),
            v.get("date"), v.get("run_id")))
    for key in withheld:
        lines.append("- %s: withheld (malformed line; needs a person)" % key)
        gaps.add("preferences", "withheld preference: %s (malformed line; needs a person)" % key)
    problems = result.get("problems") if isinstance(result.get("problems"), list) else []
    set_aside = len(problems) - len(withheld)
    if set_aside > 0:
        lines.append("- %d line(s) in the preferences file could not be read and were set aside; every readable "
                     "preference is kept and each keeps its newest readable value" % set_aside)
    return lines or ["- none"]



# These are handoff facts, not runnable evaluation answers. Keep absent keys absent.
CONTEXT_FIELDS = ("status", "status_reasons", "missing_information", "readings",
                  "simulation_coverage", "critical_cases_failed_or_open",
                  "stages_without_files", "stage_input_hashes", "stage_origins", "outputs", "bundle")


def handoff_context(loc):
    """Read the located handoff without changing normalization or readiness policy."""
    obj, err = _locate.load_handoff(loc.handoff, []) if loc.handoff else (None, "missing")
    context = {"source": loc.rel(loc.handoff) if loc.handoff else None}
    if err or not isinstance(obj, dict):
        context["read_error"] = err or "handoff is not an object"
        return context
    for key in CONTEXT_FIELDS:
        if key in obj:
            context[key] = evidence_only(obj[key])
    return context


def evidence_only(value):
    """Exclude evaluation answer payloads even when nested in carried diagnostics."""
    if isinstance(value, dict):
        return {k: evidence_only(v) for k, v in value.items()
                if k not in ("expected", "actual", "expected_result", "actual_result", "expected_answer", "actual_answer")}
    if isinstance(value, list):
        return [evidence_only(v) for v in value]
    return value


def fact_text(value):
    if value is None:
        return "unknown (null)"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, dict):
        return "; ".join("%s: %s" % (k, "none reported" if k == "error" and v is None else fact_text(v))
                         for k, v in value.items()) or "none recorded"
    if isinstance(value, list):
        return " | ".join(fact_text(v) for v in value) or "none recorded"
    return str(value)


def handoff_lines(context):
    lines = ["", "### Agent Design handoff", "- Source: %s" % (context["source"] or "not found"),
             "- Handoff status: %s" % fact_text(context.get("status", "unknown (absent)"))]
    if "read_error" in context:
        lines.append("- Handoff could not be read: %s" % context["read_error"])
    coverage = context.get("simulation_coverage")
    lines.append("- Simulation coverage: %s" % (
        fact_text(coverage) if isinstance(coverage, dict) else "unknown (coverage absent or unreadable)"))
    for key in ("suite_cases", "executed_cases", "successful"):
        if not isinstance(coverage, dict) or key not in coverage:
            lines.append("- Simulation %s: unknown (absent)" % key)
    lines.append("- Available files do not establish simulation execution or success.")
    for key in CONTEXT_FIELDS:
        if key in ("status", "simulation_coverage", "stage_input_hashes", "bundle"):
            continue
        if key in context:
            value = context[key]
            if isinstance(value, list) and value:
                lines.extend("- %s: %s" % (key, fact_text(entry)) for entry in value)
            else:
                lines.append("- %s: %s" % (key, fact_text(value)))
    lines.append("- Stage input hashes and bundle bookkeeping: see the source handoff above; "
                 "these identify carried evidence, not completion.")
    return lines


def build(run, read_env):
    gaps = Gaps()
    implies, thin = [], []
    config = load_config(run, thin, gaps)

    loc = _locate.locate_design(run)
    design_present = loc.exists
    handoff, h_problems, h_unknown = read_fields(loc.handoff, HANDOFF_FIELDS, design_present,
                                                 _locate.normalise_handoff, loc.readings)
    suite, s_problems, s_unknown = read_fields(loc.suite, SUITE_FIELDS, design_present,
                                               _locate.normalise_suite, loc.readings)
    agent = load_agent(run, thin, gaps, loc) if design_present else None
    processes = process_ids(config, handoff)
    rules, rules_path, outside = load_rules(loc) if design_present else (None, None, [])
    for path in outside:
        loc.readings.append({"what": "rules", "reading": "set aside %s" % loc.rel(path),
                             "why": "it resolves outside the run folder, so it was not opened"})
    if rules_path and os.path.dirname(rules_path) != os.path.join(loc.root, "stages", "rules"):
        loc.readings.append({"what": "rules", "reading": "read %s" % loc.rel(rules_path),
                             "why": "the design's rules are not at the usual place"})
    context = handoff_context(loc)
    basis = {"capabilities": {}, "per_process": {}, "references": [], "handoff_context": context}
    caps = capabilities(config, handoff, suite, agent, processes, rules, basis)
    if design_present:  # with no design, criterion 7 keeps every design-derived field needs_more_information
        apply_overrides(caps, load_overrides(run, thin, gaps))
        note_overrides(caps, basis)
    c = caps["capabilities"]

    # What the design implies
    if not design_present:
        implies.append("No Agent Design output found")
        gaps.add("agent-design", "No Agent Design output found under inputs/agent-design/; "
                 "the design facts below need more information", scope="whole design")
    implies.extend(handoff_lines(context))
    kinds = result_kinds(suite)
    implies.append("Result kinds: %s" % (", ".join(kinds) if kinds else NMI_PROSE))
    implies.append("Number of exits: %s" % (len(kinds) if kinds else NMI_PROSE))
    touch = people_touchpoints(agent)
    implies.append("Where people touch it: %s" % ("; ".join(touch) if touch else NMI_PROSE))
    implies.append("Writes: %s" % prose(c["writes"]))
    if len(processes) > 1:
        implies.append("Processes: a set of %d (%s)" % (len(processes), ", ".join(processes)))
    elif processes:
        implies.append("Processes: single process (%s)" % processes[0])
    else:
        implies.append("Processes: %s" % NMI_PROSE)
    form = handoff.get("primitive_form", {}).get("value") if "primitive_form" in handoff else None
    host = c["runtime"]
    host_text = NMI_PROSE if host == NMI else ("runtime dropped" if host is None else "runtime block %s" % host)
    if isinstance(form, str):
        host_text += "; design form: %s" % form
    implies.append("Likely agent host: %s" % host_text)
    implies.append("Uses a model: %s" % with_basis(c["uses_model"], basis["capabilities"]["uses_model"]))
    implies.append("Produces numbers: %s" % with_basis(c["produces_numbers"],
                                                       basis["capabilities"]["produces_numbers"]))
    if len(processes) > 1:
        for pid in processes:
            pk = result_kinds(suite, pid)
            implies.append("")
            implies.append("### Process: %s" % pid)
            implies.append("- Result kinds: %s" % (", ".join(pk) if pk else NMI_PROSE))
            implies.append("- Uses a model: %s" % with_basis(caps["per_process"][pid]["uses_model"],
                                                             basis["per_process"][pid]["uses_model"]))
    implies.extend(person_documents(loc, gaps))

    notes_dir = os.path.join(run, "inputs", "work-brain")
    try:
        notes = sorted(n for n in os.listdir(notes_dir) if n.endswith(".md")) if os.path.isdir(notes_dir) else []
    except OSError:
        notes = []
        cannot_read(thin, gaps, "inputs/work-brain", "work-brain", "inputs/work-brain could not be listed")
    implies.append("")
    implies.append("### Work Brain notes")
    implies.extend(["- inputs/work-brain/%s (cited, not fetched)" % n for n in notes] or ["- none"])

    access_path = os.path.join(run, "access.json")
    if os.path.exists(access_path):
        ledger, err = _records.load_json(access_path)
        systems = ledger.get("systems") if isinstance(ledger, dict) else None
        implies.append("")
        implies.append("### Access")
        if err is not None or not isinstance(systems, list):
            cannot_read(thin, gaps, "access.json", "access.json",
                        "access.json could not be read; the access state of each system is not known")
        else:
            for s in systems:
                if not isinstance(s, dict):
                    continue
                notes_text = [a.get("note") for a in s.get("assumptions", []) if isinstance(a, dict)] \
                    if isinstance(s.get("assumptions"), list) else []
                line = "- %s: state %s" % (s.get("name"), s.get("state"))
                if notes_text:
                    line += "; assumptions: %s" % "; ".join(str(n) for n in notes_text)
                implies.append(line)

    if read_env:
        try:
            read_environment(run)
        except (OSError, ValueError) as err:
            thin.append("- could not write the environment read (%s)" % type(err).__name__)
    env_path = os.path.join(run, "inputs", "environment.json")
    if os.path.exists(env_path):
        env, err = _records.load_json(env_path)
        implies.append("")
        implies.append("### Environment read")
        tools = env.get("tools") if isinstance(env, dict) else None
        if err is not None or not isinstance(tools, dict):
            implies.append("- inputs/environment.json could not be read")
            cannot_read(thin, gaps, "inputs/environment.json", "environment",
                        "inputs/environment.json could not be read; the target environment is not known")
        else:
            implies.append("- from inputs/environment.json, read at %s" % env.get("read_at"))
            for name in sorted(tools):
                t = tools[name] if isinstance(tools[name], dict) else {}
                implies.append("- %s: %s" % (name, (t.get("version") or "found, version not known")
                                             if t.get("found") else "not found"))
    else:
        thin.append("- environment not read")

    # What's thin: fields that could not be used
    for name, _ in HANDOFF_FIELDS + SUITE_FIELDS:
        reason = h_problems.get(name) or s_problems.get(name)
        if not reason:
            continue
        thin.append("- could not be read: %s (%s)" % (name, reason))
        if name == "open_items":
            gaps.add(name, "open items could not be read (%s); treat the whole design as open" % reason,
                     scope="whole design")
        elif name == "stand_ins":
            gaps.add(name, "stand-ins could not be read (%s): which systems are stubbed?" % reason)
        else:
            gaps.add(name, "hand-off field %s could not be read (%s)" % (name, reason))
    for key in sorted(set(h_unknown + s_unknown)):
        thin.append("- could not be read: %s (unknown field)" % key)
        gaps.add(key, "unknown field %s in the design hand-off" % key)

    # What's thin: safe defaults and design gaps
    for entry in handoff.get("open_items", []):
        if isinstance(entry, dict):
            text = entry.get("unknown") or entry.get("summary")
            thin.append("- open item to settle: %s" % (text if text else "an open item with no wording in the hand-over"))
            refer(basis, "open_items", entry.get("id"), text)
    for i, entry in enumerate(handoff.get("identity_runtime_opens", [])):
        q = entry.get("question") if isinstance(entry, dict) else entry
        adj = entry.get("adjudicator") if isinstance(entry, dict) else None
        adj = adj if isinstance(adj, str) and adj else NMI
        thin.append("- identity or runtime open: %s (adjudicator: %s)" % (q, prose(adj)))
        gaps.add("identity_runtime_opens", "identity or runtime open: %s" % q, adjudicator=adj)
    rule_text = dict((r.get("id"), r.get("prose")) for r in rules or []
                     if isinstance(r, dict) and isinstance(r.get("prose"), str))
    case_text = dict((k.get("id"), k.get("description")) for k in _cases_list(suite) or []
                     if isinstance(k.get("description"), str))
    for entry in handoff.get("rules_needing_ratification", []):
        rid = entry.get("id") or entry.get("rule_id") if isinstance(entry, dict) else entry
        own = next((entry.get(k) for k in ("prose", "text", "summary", "rule") if isinstance(entry, dict)
                    and isinstance(entry.get(k), str) and entry[k].strip()), None)
        wording = own or rule_text.get(rid)
        summary = ("a rule needs ratification before the build relies on it: %s" % wording.strip() if wording
                   else "a rule the design lists needs ratification; its wording is not in the hand-over")
        thin.append("- %s" % summary)
        gaps.add("rules_needing_ratification", summary,
                 process=entry.get("process") if isinstance(entry, dict) else None)
        refer(basis, "rules_needing_ratification", rid, summary)
    for entry in handoff.get("critical_cases_failed_or_open", []):
        cid = entry.get("id") or entry.get("case_id") if isinstance(entry, dict) else entry
        own = next((entry.get(k) for k in ("description", "summary", "text") if isinstance(entry, dict)
                    and isinstance(entry.get(k), str) and entry[k].strip()), None)
        wording = own or case_text.get(cid)
        summary = ("a critical test case failed or is open: %s" % wording.strip() if wording
                   else "a critical test case failed or is open; its wording is not in the hand-over")
        thin.append("- %s" % summary)
        gaps.add("critical_cases_failed_or_open", summary,
                 process=entry.get("process") if isinstance(entry, dict) else None)
        refer(basis, "critical_cases_failed_or_open", cid, summary)
    if suite.get("numeric_floors") == "open":
        thin.append("- the suite floor is open (numeric_floors: open)")
        gaps.add("numeric_floors", "the suite floor is open: a person must ratify a floor or confirm none")
    for entry in suite.get("omitted_layers", []):
        layer = entry.get("layer") if isinstance(entry, dict) else entry
        why = entry.get("reason") if isinstance(entry, dict) else None
        thin.append("- omitted layer: %s%s" % (layer, " (%s)" % why if why else ""))
    for entry in handoff.get("upstream_failing_checks", []):
        thin.append("- upstream failing check: %s" % json.dumps(entry, sort_keys=True))
    if "status" in suite:
        thin.append("- suite status: %s" % suite["status"])

    strengths = {}
    for entry in handoff.get("evidence_strength", []):
        if isinstance(entry, dict) and isinstance(entry.get("process"), str):
            strengths[entry["process"]] = strength_text(entry)
    if len(processes) > 1:
        for pid in processes:
            thin.append("")
            thin.append("### Process: %s" % pid)
            thin.append("- evidence strength: %s" % (strengths.get(pid) or NMI_PROSE))
            for g in gaps.items:
                if g["process"] == pid:
                    thin.append("- %s" % g["summary"])
    else:
        for pid in sorted(strengths):
            thin.append("- evidence strength: %s: %s" % (pid, strengths[pid] or NMI_PROSE))

    questions = ["- %s" % t for t in TOPICS]
    if isinstance(handoff.get("platform_question"), str) and handoff["platform_question"].strip():
        questions.append("- Platform question from the design: %s" % handoff["platform_question"].strip())

    if loc.readings:
        thin.append("")
        thin.append("### How the design was read")
        for r in loc.readings:
            thin.append("- %s: %s (%s)" % (r["what"], r["reading"], r["why"]))

    carried = carried_over(run, config, gaps)
    return implies, thin, questions, carried, gaps, caps, loc, basis


def merge_gaps(run, gaps):
    """Keep every item whose source is not briefing; replace this script's items."""
    path = os.path.join(run, GAPS_NAME)
    kept, problem = [], None
    if os.path.exists(path):
        obj, err = _records.load_json(path)
        if err is None and isinstance(obj, dict) and isinstance(obj.get("items"), list):
            kept = [i for i in obj["items"] if not (isinstance(i, dict) and i.get("source") == "briefing")]
        else:
            problem = "- could not be read: %s (unreadable; rewritten with this run's items only)" % GAPS_NAME
            gaps.add("gaps-file", "%s could not be read; items from other sources in it were lost" % GAPS_NAME)
    return {"items": kept + gaps.items}, problem


def render(implies, thin, questions, carried):
    try:
        with open(os.path.join(HERE, "..", "templates", "briefing.md"), encoding="utf-8") as f:
            template = f.read()
    except (OSError, UnicodeDecodeError):
        template = FALLBACK_TEMPLATE
    for token, lines in (("{{implies}}", implies), ("{{thin}}", thin or ["- nothing found thin"]),
                         ("{{questions}}", questions), ("{{carried}}", carried)):
        template = template.replace(token, "\n".join(lines))
    return template


def main(argv=None):
    parser = argparse.ArgumentParser(description="Write the Agent Build briefing for a run folder.")
    parser.add_argument("--run", required=True, help="the run folder R")
    parser.add_argument("--read-env", action="store_true", help="also write R/inputs/environment.json")
    args = parser.parse_args(argv)
    run = args.run
    if not os.path.isdir(run):
        sys.stderr.write("briefing.py: --run is not a folder: %s\n" % run)
        return USAGE

    implies, thin, questions, carried, gaps, caps, loc, basis = build(run, args.read_env)
    doc, problem = merge_gaps(run, gaps)
    if problem:
        thin.append(problem)
    try:
        _records.write_record(os.path.join(run, GAPS_NAME), doc)
        _records.write_record(os.path.join(run, "capabilities.json"), caps)
        os.makedirs(os.path.join(run, os.path.dirname(DESIGN_RECORD)), exist_ok=True)
        _records.write_record(os.path.join(run, DESIGN_RECORD), loc.as_record())
        _records.write_record(os.path.join(run, BASIS_RECORD), basis)
        with open(os.path.join(run, "briefing.md"), "w", encoding="utf-8") as f:
            f.write(render(implies, thin, questions, carried))
    except OSError as err:
        sys.stderr.write("briefing.py: cannot write into the run folder: %s\n" % err)
        return USAGE
    print("briefing written: briefing.md, %s, capabilities.json (%d gaps)" % (GAPS_NAME, len(gaps.items)))
    return OK


if __name__ == "__main__":
    sys.exit(main())
