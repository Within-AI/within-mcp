#!/usr/bin/env python3
"""Agent Build hand-off: `handoff.py --run RUN`.

Writes RUN/outputs/agent-build-inputs.json (the authoritative hand-off) every time, so the current file always
describes the current run. The design it describes travels with it: the architecture (copies of
stages/architecture/*), the rules (stages/rules/*) and the test suite are copied to RUN/outputs/design/ and named in `bundle.design_files`
by paths relative to the hand-off's own folder. The shareable bundle RUN/outputs/shareable/ (hand-off copy,
page-preparation guide, business-owner view and its own design/ copy) is portable on its own.

Before anything is written, the previous hand-off and its design/ copy are archived to RUN/outputs/versions/
(agent-build-inputs.v<k>.json, design.v<k>/) at the previous version number, or the next free number when that
one is taken; an existing version file never stops the export.

Nothing is withheld. A field missing from a readable stage file is written as empty and listed in
`missing_information`; a part whose input file is missing or unreadable, or a document that does not match
formats/run/handoff.schema.json, is written with what is available and marks the hand-off `status: incomplete`
with `status_reasons`. Readings taken here and those recorded by earlier stages are carried in `readings`.
RUN/handoff/assemble-report.json (a sidecar outside the bundle format) is always written too.
Exit 0 whenever the run folder exists; 2 for usage or a missing run folder. Older runs may hold a platform
answer file under outputs/; it is never read. The script never opens
RUN/outputs/agent-design-inputs.json (the platform answer belongs to Agent Build, so this hand-off poses the
question with an empty answer).

Standard library only, Python 3.9.
"""
import hashlib
import importlib.util
import json
import os
import re
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
AD = os.path.dirname(HERE)

FORMATS = os.path.join(AD, "formats")
HANDOFF_SCHEMA = "run/handoff.schema.json"

HANDOFF = "outputs/agent-build-inputs.json"
SHAREABLE = "outputs/shareable"
VERSIONS = "outputs/versions"
GUIDE = "stages/page-prep/page-prep.json"
OWNER_VIEW = "stages/register/business-owner.md"
DESIGN = "outputs/design"
DESIGN_DIR = "design"   # the design copy's folder name beside each copy of the hand-off
ARCH_SRC = "stages/architecture"
SUITE_SRC = "stages/test-suite/test-suite.json"
RULES_SRC = "stages/rules"
PROBLEMS = "builder/problems.jsonl"
BUNDLE_FILES = ("agent-build-inputs.json", os.path.basename(GUIDE), os.path.basename(OWNER_VIEW))
SHAREABLE_ENTRIES = BUNDLE_FILES + (DESIGN_DIR,)
# Parts written as an empty list when they cannot be read (each one recorded in missing_information).
LIST_PARTS = ["open_items", "access_assumptions", "stand_ins", "rules_needing_ratification",
              "critical_cases_failed_or_open", "identity_runtime_opens", "evidence_strength",
              "upstream_failing_checks", "capability_needs"]

PLATFORM_QUESTION = ("Which platform will run these agents, and does it offer each capability "
                     "need below natively or through its portable fallback?")

# Capability needs and their portable fallbacks (no vendor product named).
FALLBACKS = {
    "pause for a person and resume": (
        "Write the pending item and its full state to a queue table, notify the person, and resume "
        "from the saved state when their answer is recorded."),
    "calculation tool": (
        "Run the named calculation as a plain function in code before or after the model step and "
        "pass only its result to the model."),
    "code routing": (
        "Route with an ordinary conditional in code on the recorded fields; the model never chooses "
        "the branch."),
    "system reads": (
        "Read the surface through a read-only query or export the agent runs before it reasons, "
        "with the result passed in as input."),
}

# Pins-addendum F2: the files each stage writes, as the hand-off reads them.
STAGE_FILES = [
    ("intake", ["stages/intake/intake.json", "stages/intake/evidence-addendum.json"]),
    ("architecture", ["stages/architecture/agent.yaml"]),
    ("check-architecture", []),
    ("review-a", ["stages/review-a/verdict.json", "stages/review-a/findings.json",
                  "stages/review-a/routed.json"]),
    ("boundaries", ["stages/boundaries/boundaries.json"]),
    ("rules", ["stages/rules/rules.json", "stages/rules/outcomes.json",
               "stages/rules/open-items.json"]),
    ("placement", ["stages/placement/placed-rules.json", "stages/placement/declarations.json"]),
    ("fields", ["stages/fields/fields.json"]),
    ("supply-check", []),
    ("review-b", ["stages/review-b/verdict.json", "stages/review-b/fix-round.json",
                  "stages/review-b/open-items.json"]),
    ("test-suite", ["stages/test-suite/test-suite.json"]),
    ("expected-values", ["stages/expected-values/expected.json",
                         "stages/expected-values/expected.sha256",
                         "stages/expected-values/judge-questions.json"]),
    ("simulation", ["simulation/sim-input.json", "simulation/walk.json",
                    "simulation/interfaces.json"]),
    ("coherence-gate", ["stages/coherence-gate/report.json",
                        "stages/coherence-gate/open-items.json"]),
    ("register", ["stages/register/raw.json", "stages/register/register.json",
                  "stages/register/verdicts.json", "stages/register/business-owner.md",
                  "stages/register/builder.md"]),
    ("page-prep", ["stages/page-prep/page-prep.json"]),
    ("post-run", ["stages/post-run/resolutions.json"]),
]

REQUIRED = ["open_items", "access_assumptions", "stand_ins", "rules_needing_ratification",
            "critical_cases_failed_or_open", "primitive_form", "identity_runtime_opens",
            "evidence_strength", "upstream_failing_checks", "capability_needs",
            "stage_input_hashes", "outputs", "bundle", "platform_question"]

SIM_INPUT = "simulation/sim-input.json"
FAILED_OR_OPEN = {"differs", "execution_error", "not_executed", "needs_more_information"}

# An absolute path: POSIX (any root), home-relative (~/) or a Windows drive.
_ABS_START = r"(?:/|~/|[A-Za-z]:[\\/])"
WHOLE_PATH = re.compile(r"^\s*%s[^\n\r\t]*$" % _ABS_START)
# Inside longer text a path is either quoted ("...", '...', `...`, <...>) and may hold spaces,
# or unquoted and ends at whitespace, unless the next words lead on to another "/" segment
# ("/a/Google Drive/b.csv" is one path). A match never starts after a letter, digit, ":", "/"
# or ".", so "and/or", "1/2" and URLs are left alone.
_SEG = r"""[^\s"'`<>]"""
_WORD = r"""[^\s"'`<>/,;]"""
EMBEDDED_PATH = re.compile(
    r"""(?<=["'`<])%s[^"'`<>\n\r]*(?=["'`>])"""
    r"""|(?<![\w:/.~])%s%s*(?:(?:[ ]%s+)+/%s+)*""" % (_ABS_START, _ABS_START, _SEG, _WORD, _SEG))
_TRAIL = ".,;:!?)]}"

# Schema keywords handoff.py checks itself (standard library only); any other keyword in the
# schema files stops the run rather than passing unchecked.
SCHEMA_ANNOTATIONS = {"$schema", "$id", "$comment", "$defs", "version", "title", "description"}
SCHEMA_KEYWORDS = {"$ref", "type", "required", "properties", "additionalProperties", "items",
                   "minItems", "maxItems", "enum", "const", "minLength", "maxLength", "pattern",
                   "anyOf", "if", "then", "else"}


REPORT = "handoff/assemble-report.json"


class Stop(Exception):
    """A named problem. Blocking (the default): an input file missing or unreadable, a wrong type or a broken
    reference; the hand-off is still written, marked incomplete. Not blocking: a field absent from a readable file,
    written as empty and recorded as missing information."""

    def __init__(self, message, blocking=True):
        Exception.__init__(self, message)
        self.blocking = blocking


def _inside(run, path):
    """True when path, every link resolved, lies inside the run folder."""
    root = os.path.realpath(run)
    real = os.path.realpath(path)
    return real == root or real.startswith(root + os.sep)


def _contained(run, rel):
    """Containment before any read: a run file that resolves outside the run folder is never opened."""
    if not _inside(run, os.path.join(run, rel)):
        raise Stop("input resolves outside the run folder, not read: %s" % rel)


def _read_bytes(run, rel):
    _contained(run, rel)
    with open(os.path.join(run, rel), "rb") as f:
        return f.read()


def _json(run, rel):
    path = os.path.join(run, rel)
    if not os.path.isfile(path):
        raise Stop("missing input: %s" % rel)
    _contained(run, rel)
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except ValueError as e:
        raise Stop("unreadable input: %s (%s)" % (rel, e))


def _need(doc, key, rel, kind=list):
    if not isinstance(doc, dict):
        raise Stop("wrong type: %s is not an object" % rel)
    if key not in doc:
        raise Stop("missing field: %s in %s" % (key, rel), blocking=False)
    if not isinstance(doc[key], kind):
        raise Stop("wrong type: %s in %s" % (key, rel))
    return doc[key]


def _placed_rule_list(doc):
    """expr.placed_rule_list: the one reader of placed-rules.json (a list, `placed_rules` or `rules`)."""
    mod = sys.modules.get("expr")
    if not hasattr(mod, "placed_rule_list"):
        spec = importlib.util.spec_from_file_location("expr", os.path.join(AD, "scripts", "expr.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    return mod.placed_rule_list(doc)


def _load_agent(run):
    """check_architecture.load_agent(run_dir) (U8, pins F6)."""
    mod = sys.modules.get("check_architecture")
    if mod is None:
        path = os.path.join(AD, "checks", "check_architecture.py")
        if not os.path.isfile(path):
            raise Stop("missing reader: checks/check_architecture.py (load_agent)")
        spec = importlib.util.spec_from_file_location("check_architecture", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    _contained(run, "stages/architecture/agent.yaml")
    try:
        return mod.load_agent(run)
    except Stop:
        raise
    except Exception as e:  # the reader's own failure is a named stop
        raise Stop("unreadable input: stages/architecture/agent.yaml (%s)" % e)


def _primitive_form(run):
    outcomes = _json(run, "stages/rules/outcomes.json")
    if "primitive_form" not in outcomes:
        raise Stop("missing field: primitive_form in stages/rules/outcomes.json")
    pf = outcomes["primitive_form"]
    if not isinstance(pf, dict) or not isinstance(pf.get("value"), str) or not pf["value"].strip():
        raise Stop("primitive_form is empty")
    ref = pf.get("decision_ref")
    if not isinstance(ref, str) or not ref.strip():
        raise Stop("primitive_form has no decision reference")
    ids = {d.get("id") for d in _need(outcomes, "decisions", "stages/rules/outcomes.json")
           if isinstance(d, dict)}
    if ref not in ids:
        raise Stop("primitive_form decision reference %s is not a decision in "
                   "stages/rules/outcomes.json" % ref)
    return {"value": pf["value"], "decision_ref": ref}


def _capability_needs(run, agent, note):
    """Each part's capability needs. A part without an id is left out and noted; absent handovers or placed
    rules read as none, noted."""
    rel = "stages/architecture/agent.yaml"
    parts = _need(agent, "parts", rel)
    header = agent.get("header") if isinstance(agent.get("header"), dict) else {}
    handovers = header.get("handovers")
    if not isinstance(handovers, list):
        note("capability_needs", "missing field: header.handovers in %s; read as none" % rel, blocking=False,
             written_as="pauses for a person not derived")
        handovers = []
    try:
        placed = _placed_rule_list(_json(run, "stages/placement/placed-rules.json"))
    except Stop as e:
        note("capability_needs", "%s; calculation needs not derived" % e, written_as="calculation needs left out")
        placed = []
    if placed is None:
        note("capability_needs", "missing field: rules in stages/placement/placed-rules.json; calculation needs "
             "not derived", blocking=False, written_as="calculation needs left out")
        placed = []
    person_ends = set()
    for h in handovers:
        if isinstance(h, dict) and h.get("kind") == "person_handover":
            person_ends.update([h.get("from"), h.get("to")])
    out = []
    for part in parts:
        pid = part.get("id") if isinstance(part, dict) else None
        if not isinstance(pid, str) or not pid:
            note("capability_needs", "missing field: id in a part of %s" % rel, written_as="that part left out")
            continue
        steps = set(part.get("owns_steps") or [])
        needs = []
        if pid in person_ends:
            needs.append({"need": "pause for a person and resume"})
        calcs = sorted({r["calculation"] for r in placed
                        if isinstance(r, dict) and isinstance(r.get("calculation"), str) and r["calculation"].strip()
                        and "%s:%s" % (r.get("process_id"), r.get("future_state_step")) in steps})
        if calcs:
            needs.append({"need": "calculation tool", "calculations": calcs})
        routing = part.get("routing") if isinstance(part.get("routing"), dict) else {}
        if routing.get("kind") == "code" and str(routing.get("marked") or "").strip():
            needs.append({"need": "code routing", "marked": routing["marked"]})
        for surface in part.get("surfaces_read") or []:
            needs.append({"need": "system reads", "surface": surface})
        for n in needs:
            n["portable_fallback"] = FALLBACKS[n["need"]]
        out.append({"part": pid, "needs": needs})
    return out


def _stage_hashes(run, outside=None):
    """Hash each stage's files; a file resolving outside the run folder is never read (listed in `outside`)."""
    hashes, files = {}, {}
    outside = [] if outside is None else outside
    for stage, rels in STAGE_FILES:
        present = sorted(r for r in rels if os.path.isfile(os.path.join(run, r)))
        if stage == "coherence-gate":
            folder = os.path.join(run, "stages", "coherence-gate")
            if os.path.isdir(folder) and _inside(run, folder):
                present = sorted(set(present) | {
                    "stages/coherence-gate/" + n for n in os.listdir(folder)
                    if n.startswith("route-") and n.endswith(".json")})
        outside.extend(r for r in present if not _inside(run, os.path.join(run, r)))
        present = [r for r in present if _inside(run, os.path.join(run, r))]
        files[stage] = present
        if not present:
            hashes[stage] = None
            continue
        h = hashlib.sha256()
        for rel in present:
            h.update(rel.encode("utf-8") + b"\0" + _read_bytes(run, rel) + b"\0")
        hashes[stage] = "sha256:" + h.hexdigest()
    return hashes, files


def _rewrite_path(raw, roots):
    """One absolute path: run-relative when inside RUN, otherwise its file name."""
    p = raw.strip()
    if p.startswith("~"):
        p = os.path.expanduser(p)
    windows = re.match(r"^[A-Za-z]:[\\/]", p) is not None
    p = p.replace("\\", "/")
    if not windows:
        p = os.path.normpath(p)
        for root in roots:
            if p == root:
                return "."
            if p.startswith(root + "/"):
                return p[len(root) + 1:]
    return p.rstrip("/").rsplit("/", 1)[-1] or "."


def _relativise(value, run):
    """Rewrite machine-specific absolute paths: run-relative inside RUN, else the file name.

    A string whose whole value is an absolute path is rewritten as a whole, spaces included;
    paths inside longer text are found by EMBEDDED_PATH."""
    roots = sorted({os.path.normpath(os.path.abspath(run)), os.path.realpath(run)},
                   key=len, reverse=True)

    def fix(m):
        raw = m.group(0)
        tail = ""
        while raw and raw[-1] in _TRAIL and len(raw) > 1:
            raw, tail = raw[:-1], raw[-1] + tail
        return _rewrite_path(raw, roots) + tail

    if isinstance(value, str):
        if WHOLE_PATH.match(value):
            lead = value[:len(value) - len(value.lstrip())]
            rest = value[len(lead):]
            body = rest.rstrip()
            return lead + _rewrite_path(body, roots) + rest[len(body):]
        return EMBEDDED_PATH.sub(fix, value)
    if isinstance(value, list):
        return [_relativise(v, run) for v in value]
    if isinstance(value, dict):
        return {k: _relativise(v, run) for k, v in value.items()}
    return value


def _critical_left_out(run, suite, walked, cases=(), note=None):
    """Critical suite cases without completed execution, each listed open with its reason, never walked or guessed: a stamped
    exclusion keeps its stamp and names what is missing; any other case carries the converter's reasons from
    simulation/sim-input.json unconvertible[] (read when present)."""
    reasons = {}
    if os.path.isfile(os.path.join(run, SIM_INPUT)):
        try:
            exclusions = _need(_json(run, SIM_INPUT), "unconvertible", SIM_INPUT)
            for e in exclusions:
                if not isinstance(e, dict):
                    raise Stop("wrong type: unconvertible entry in " + SIM_INPUT)
                why = e.get("reason") or ""
                reasons.setdefault(e.get("case_id"), []).append("%s (%s)" % (why, e["item"]) if e.get("item") else why)
        except (Stop, OSError, UnicodeDecodeError) as exc:
            if note is None:
                raise
            note("critical_cases_failed_or_open", str(exc), blocking=False,
                 written_as="known execution and critical cases retained; converter reasons unavailable")
    for entry in cases:
        if not isinstance(entry, dict):
            continue
        cid = entry.get("case_id")
        reason = entry.get("drop_label") or entry.get("reason")
        if reason:
            reasons.setdefault(cid, []).append(str(reason))
        elif cid not in walked:
            reasons.setdefault(cid, []).append("listed in simulation/walk.json with state %s; no completed execution"
                                               % entry.get("state", "missing"))
    out = []
    for c in suite:
        cid = c.get("case_id") or c.get("id")
        if c.get("critical") is not True or cid in walked:
            continue
        exclusion = c.get("exclusion")
        if isinstance(exclusion, dict):
            out.append({"case_id": cid, "state": "needs_more_information", "walked": False,
                        "reason": "stamped %s exclusion, not walked; missing: %s" % (
                            exclusion.get("stamp", "needs_more_information"), exclusion.get("missing", "not named"))})
        else:
            why = "; ".join(reasons.get(cid) or ["no entry in simulation/walk.json"])
            entries = [e for e in cases if isinstance(e, dict) and e.get("case_id") == cid]
            state = entries[0].get("state") if len(entries) == 1 else None
            out.append({"case_id": cid, "state": state if state in FAILED_OR_OPEN else "not_executed",
                        "walked": False, "reason": "not executed: %s" % why})
    return out


def assemble(run):
    """Build the hand-off document part by part. A part that cannot be built is written as its empty value (lists)
    or left out (primitive_form) and listed, with why, under the document's private `_missing` key (popped by
    run_handoff): `blocking` entries make the hand-off incomplete, the others are recorded missing information."""
    missing = []

    def note(name, reason, blocking=True, written_as=None):
        missing.append({"part": name, "reason": reason, "blocking": bool(blocking),
                        "written_as": written_as or ("empty list" if name in LIST_PARTS else "left out")})

    def part(names, fn):
        try:
            return fn()
        except Stop as e:
            for n in names:
                note(n, str(e), e.blocking or n not in LIST_PARTS)
        except Exception as e:  # an odd input never stops the hand-off
            for n in names:
                note(n, "%s: %s" % (type(e).__name__, e))
        return None

    doc = {}

    def from_intake():
        intake = _json(run, "stages/intake/intake.json")
        part(["upstream_failing_checks"], lambda: doc.__setitem__("upstream_failing_checks", _need(
            intake, "upstream_failing_checks", "stages/intake/intake.json")))
        strength = []
        for p in _need(intake, "processes", "stages/intake/intake.json"):
            pid = p.get("process_id") if isinstance(p, dict) else None
            got = p.get("evidence_strength") if isinstance(p, dict) else None
            if not isinstance(got, dict):
                note("evidence_strength", "missing field: evidence_strength for process %s in "
                     "stages/intake/intake.json processes" % (pid or "?"), blocking=False,
                     written_as="process listed without its evidence counts")
                got = {}
            strength.append(dict(got, process=pid))
        doc["evidence_strength"] = strength
    part(["upstream_failing_checks", "evidence_strength"], from_intake)

    items = []

    def from_register():
        register = _json(run, "stages/register/register.json")
        got = _need(register, "items", "stages/register/register.json")
        doc["open_items"] = got
        items.extend(i for i in got if isinstance(i, dict))
    part(["open_items"], from_register)
    part(["access_assumptions"], lambda: doc.__setitem__(
        "access_assumptions", _need(_json(run, "stages/register/register.json"), "access",
                                    "stages/register/register.json")))

    def opens():
        got = _need(_json(run, "stages/register/register.json"), "identity_runtime_opens",
                    "stages/register/register.json")
        kept = []
        for o in got:
            if isinstance(o, dict) and "adjudicator" in o and not (
                    isinstance(o["adjudicator"], str) and o["adjudicator"].strip()):
                o = {k: v for k, v in o.items() if k != "adjudicator"}  # unnamed reads as absent
            kept.append(o)
        doc["identity_runtime_opens"] = kept
        for o in kept:
            if not isinstance(o, dict) or "adjudicator" not in o:
                note("identity_runtime_opens", "identity or runtime open %s has no named adjudicator"
                     % (o.get("id", "?") if isinstance(o, dict) else "?"), blocking=False,
                     written_as="kept; adjudicator still to be named")
    part(["identity_runtime_opens"], opens)

    part(["stand_ins"], lambda: doc.__setitem__("stand_ins", _need(
        _json(run, "stages/placement/declarations.json"), "stand_ins", "stages/placement/declarations.json")))

    def ratify():
        rules = _need(_json(run, "stages/rules/rules.json"), "rules", "stages/rules/rules.json")
        doc["rules_needing_ratification"] = sorted(
            {r["id"] for r in rules if isinstance(r, dict) and r.get("evidence_state") == "Assumed pending ratification"
             and isinstance(r.get("id"), str) and r["id"]}
            | {d for i in items if i.get("class") == "Assumed pending ratification"
               for d in (i.get("dependent_rules") or []) if isinstance(d, str) and d})
    part(["rules_needing_ratification"], ratify)

    def critical():
        walk_failure = None
        try:
            walk = _json(run, "simulation/walk.json")
            cases = _need(walk, "cases", "simulation/walk.json")
        except (Stop, OSError, UnicodeDecodeError) as exc:
            # Missing simulation evidence is blocking even when a missing optional
            # field elsewhere would not be. Preserve the original source diagnostic.
            walk_failure = str(exc) if isinstance(exc, Stop) else "%s: %s" % (type(exc).__name__, exc)
            note("critical_cases_failed_or_open", walk_failure, blocking=True,
                 written_as="known critical suite cases retained as not executed where available")
            walk, cases = {"error": walk_failure}, []
        try:
            suite = _need(_json(run, SUITE_SRC), "cases", SUITE_SRC)
            if any(not isinstance(c, dict) or not isinstance(c.get("case_id") or c.get("id"), str)
                   or not (c.get("case_id") or c.get("id")) for c in suite):
                raise Stop("wrong type: case identity in " + SUITE_SRC)
        except (Stop, OSError, UnicodeDecodeError) as exc:
            diagnostic = str(exc)
            note("simulation_coverage", diagnostic, blocking=True,
                 written_as="unknown suite coverage; available walk evidence retained without suite membership")
            doc["simulation_coverage"] = {
                "status": "unknown", "successful": False, "suite_cases": None,
                "executed_cases": None, "source": "simulation/walk.json",
                "suite_source": SUITE_SRC, "error": diagnostic,
                "walk_evidence": [{k: c[k] for k in ("case_id", "state", "critical", "executed", "walked",
                                                    "reason", "drop_label") if k in c}
                                  for c in cases if isinstance(c, dict)]}
            # Only the walk's explicit critical attribution is available here.
            # An ID alone does not establish membership in an unavailable suite.
            doc["critical_cases_failed_or_open"] = [
                dict({k: c[k] for k in ("case_id", "state", "walked", "reason", "drop_label") if k in c},
                     source="simulation/walk.json", critical_source="simulation/walk.json")
                for c in cases if isinstance(c, dict) and c.get("critical") is True
                and c.get("case_id") and c.get("state") in FAILED_OR_OPEN]
            return
        crit_ids = {c.get("case_id") or c.get("id") for c in suite if c.get("critical") is True}
        # A listed ID is not evidence of execution. Only terminal comparison states
        # count; duplicate/conflicting entries cannot establish a case result.
        by_id = {}
        for c in cases:
            if isinstance(c, dict) and c.get("case_id"):
                by_id.setdefault(c["case_id"], []).append(c)
        suite_ids = {c.get("case_id") or c.get("id") for c in suite}
        suite_ids.discard(None)
        executed = {cid for cid, entries in by_id.items() if cid in suite_ids
                    and len(entries) == 1 and entries[0].get("state") in ("matches", "differs")
                    and entries[0].get("executed") is not False
                    and entries[0].get("walked") is not False}
        matched = {cid for cid in executed if by_id[cid][0]["state"] == "matches"}
        differing = executed - matched
        unexecuted = suite_ids - executed
        # The coverage contains no expected answers, actual values or interface payloads.
        nonexecution = []
        for cid in sorted(unexecuted):
            entries = by_id.get(cid, [])
            why = [str(c.get("drop_label") or c.get("reason") or
                       "listed with state %s; no completed execution" % c.get("state", "missing"))
                   for c in entries]
            if len(entries) > 1:
                why.insert(0, "multiple walk entries; result is ambiguous")
            nonexecution.append({"case_id": cid, "reason": "; ".join(why) or
                                 walk_failure or "no entry in simulation/walk.json"})
        coverage = {
            "status": "not_executed" if not executed else "partial" if unexecuted else "complete",
            "successful": bool(suite_ids) and matched == suite_ids and not walk.get("error"),
            "suite_cases": len(suite_ids), "listed_cases": len(suite_ids & set(by_id)),
            "executed_cases": len(executed), "matches": len(matched), "differs": len(differing),
            "not_executed": len(unexecuted), "nonexecution": nonexecution,
            "unlisted_case_ids": sorted(set(by_id) - suite_ids),
            "source": "simulation/walk.json", "error": walk.get("error"),
        }
        doc["simulation_coverage"] = coverage
        if not executed or unexecuted or walk.get("error"):
            note("simulation_coverage", "%d of %d suite cases executed; %s%s" % (
                len(executed), len(suite_ids), coverage["status"],
                "; " + str(walk["error"]) if walk.get("error") else ""),
                written_as="available results and nonexecution reasons retained")
        crit = [{"case_id": cid, "state": "differs"} for cid in sorted(differing & crit_ids)]
        if walk_failure:
            crit += [{"case_id": cid, "state": "not_executed", "walked": False,
                      "reason": "not executed: %s" % walk_failure} for cid in sorted(crit_ids)]
        else:
            crit += _critical_left_out(run, suite, executed, cases, note)
        doc["critical_cases_failed_or_open"] = crit
    part(["critical_cases_failed_or_open"], critical)

    part(["primitive_form"], lambda: doc.__setitem__("primitive_form", _primitive_form(run)))
    part(["capability_needs"], lambda: doc.__setitem__("capability_needs",
                                                       _capability_needs(run, _load_agent(run), note)))

    for name in LIST_PARTS:
        doc.setdefault(name, [])

    outside = []
    hashes, files = _stage_hashes(run, outside)
    for rel in outside:
        note("stage_input_hashes", "input resolves outside the run folder, not read: %s" % rel,
             written_as="left out of the stage hashes")
    doc.update({
        "stage_input_hashes": {k: v for k, v in hashes.items() if v is not None},
        "stages_without_files": [k for k, v in hashes.items() if v is None],
        "outputs": [],
        "bundle": {
            "shareable": [SHAREABLE + "/agent-build-inputs.json",
                          SHAREABLE + "/" + os.path.basename(GUIDE),
                          SHAREABLE + "/" + os.path.basename(OWNER_VIEW)],
            "internal_trace": ["events.jsonl", "state.json", "prompts/", "logs/", "checks/",
                               "stages/review-a/verdict.json", "stages/review-b/verdict.json",
                               "stages/register/verdicts.json"],
        },
        "platform_question": PLATFORM_QUESTION,
        "platform_answer": "",
    })
    for rel, label in ((GUIDE, "client-ready guide"), (OWNER_VIEW, "business-owner view")):
        if not os.path.isfile(os.path.join(run, rel)):
            note("bundle", "missing %s: %s" % (label, rel), blocking=False, written_as="left out of shareable/")
    ordered = {k: doc[k] for k in REQUIRED if k in doc}
    ordered.update({k: v for k, v in doc.items() if k not in ordered})
    ordered["_missing"] = missing
    return ordered, files


class _Schemas(object):
    """The schema files under formats/, read at run time; $ref resolved by file and pointer."""

    def __init__(self, root):
        self.root, self.docs = root, {}

    def load(self, rel):
        rel = os.path.normpath(rel).replace(os.sep, "/")
        if rel.startswith("../") or os.path.isabs(rel):
            raise Stop("schema reference outside formats/: %s" % rel)
        if rel not in self.docs:
            path = os.path.join(self.root, rel)
            if not os.path.isfile(path):
                raise Stop("missing schema: formats/%s" % rel)
            try:
                with open(path, encoding="utf-8") as f:
                    self.docs[rel] = json.load(f)
            except ValueError as e:
                raise Stop("unreadable schema: formats/%s (%s)" % (rel, e))
        return self.docs[rel]

    def resolve(self, ref, rel):
        if not isinstance(ref, str):
            raise Stop("unsupported $ref in formats/%s" % rel)
        target, _, frag = ref.partition("#")
        if target:
            if "://" in target:
                raise Stop("unsupported $ref %s in formats/%s (only file references)" % (ref, rel))
            rel = os.path.normpath(os.path.join(os.path.dirname(rel), target)).replace(os.sep, "/")
        node = self.load(rel)
        if frag:
            if not frag.startswith("/"):
                raise Stop("unsupported $ref %s in formats/%s (only JSON pointers)" % (ref, rel))
            for tok in frag[1:].split("/"):
                tok = tok.replace("~1", "/").replace("~0", "~")
                if isinstance(node, dict) and tok in node:
                    node = node[tok]
                elif isinstance(node, list) and tok.isdigit() and int(tok) < len(node):
                    node = node[int(tok)]
                else:
                    raise Stop("schema reference not found: %s in formats/%s" % (ref, rel))
        return node, rel

    def scan(self, schema, rel, seen):
        """Stop on any keyword this checker does not support, in every reachable schema."""
        if isinstance(schema, bool):
            return
        if not isinstance(schema, dict):
            raise Stop("malformed schema in formats/%s" % rel)
        if (rel, id(schema)) in seen:
            return
        seen.add((rel, id(schema)))
        for k in schema:
            if k not in SCHEMA_KEYWORDS and k not in SCHEMA_ANNOTATIONS:
                raise Stop("unsupported schema keyword %s in formats/%s" % (k, rel))
        for key in ("properties", "$defs"):
            for sub in (schema.get(key) or {}).values():
                self.scan(sub, rel, seen)
        for key in ("items", "if", "then", "else", "additionalProperties"):
            if key in schema:
                self.scan(schema[key], rel, seen)
        for sub in schema.get("anyOf") or []:
            self.scan(sub, rel, seen)
        if "$ref" in schema:
            sub, srel = self.resolve(schema["$ref"], rel)
            self.scan(sub, srel, seen)
        if "pattern" in schema:
            try:
                re.compile(schema["pattern"])
            except (re.error, TypeError):
                raise Stop("unsupported schema pattern in formats/%s" % rel)


def _is_type(v, t):
    if t == "object":
        return isinstance(v, dict)
    if t == "array":
        return isinstance(v, list)
    if t == "string":
        return isinstance(v, str)
    if t == "boolean":
        return isinstance(v, bool)
    if t == "null":
        return v is None
    if t == "integer":
        return (isinstance(v, int) and not isinstance(v, bool)) or (
            isinstance(v, float) and v.is_integer())
    if t == "number":
        return isinstance(v, (int, float)) and not isinstance(v, bool)
    raise Stop("unsupported schema type %s" % t)


def _same(a, b):
    """JSON equality (true is not 1)."""
    if isinstance(a, bool) or isinstance(b, bool):
        return isinstance(a, bool) and isinstance(b, bool) and a == b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return a == b
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(_same(x, y) for x, y in zip(a, b))
    if isinstance(a, dict) and isinstance(b, dict):
        return set(a) == set(b) and all(_same(a[k], b[k]) for k in a)
    return type(a) is type(b) and a == b


def _check(schemas, value, schema, rel, path, errs):
    where = "/".join(str(p) for p in path) or "(top level)"
    if schema is True:
        return
    if schema is False:
        errs.append("%s: not allowed" % where)
        return
    if "$ref" in schema:
        sub, srel = schemas.resolve(schema["$ref"], rel)
        _check(schemas, value, sub, srel, path, errs)
    if "type" in schema:
        types = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
        if not any(_is_type(value, t) for t in types):
            errs.append("%s: expected %s, found %s" % (where, " or ".join(types),
                                                       type(value).__name__))
            return
    if "enum" in schema and not any(_same(value, e) for e in schema["enum"]):
        errs.append("%s: %s is not one of the allowed values" % (where, json.dumps(value)))
    if "const" in schema and not _same(value, schema["const"]):
        errs.append("%s: must be %s" % (where, json.dumps(schema["const"])))
    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0):
            errs.append("%s: shorter than %d characters" % (where, schema["minLength"]))
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            errs.append("%s: longer than %d characters" % (where, schema["maxLength"]))
        if "pattern" in schema and not re.search(schema["pattern"], value):
            errs.append("%s: %s does not match the required pattern" % (where, json.dumps(value)))
    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0):
            errs.append("%s: fewer than %d entries" % (where, schema["minItems"]))
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            errs.append("%s: more than %d entries" % (where, schema["maxItems"]))
        if "items" in schema:
            for i, v in enumerate(value):
                _check(schemas, v, schema["items"], rel, path + [i], errs)
    if isinstance(value, dict):
        for k in schema.get("required", []):
            if k not in value:
                errs.append("%s: missing required property '%s'" % (where, k))
        props = schema.get("properties") or {}
        for k, v in value.items():
            if k in props:
                _check(schemas, v, props[k], rel, path + [k], errs)
            elif "additionalProperties" in schema:
                _check(schemas, v, schema["additionalProperties"], rel, path + [k], errs)
    if "anyOf" in schema:
        tries = []
        for sub in schema["anyOf"]:
            e = []
            _check(schemas, value, sub, rel, path, e)
            if not e:
                break
            tries.append(e)
        else:
            closest = min(tries, key=len) if tries else []
            errs.append("%s: matches none of the allowed shapes (%s)" % (where, "; ".join(closest)))
    if "if" in schema:
        e = []
        _check(schemas, value, schema["if"], rel, path, e)
        branch = "then" if not e else "else"
        if branch in schema:
            _check(schemas, value, schema[branch], rel, path, errs)


def validate(doc, formats=None):
    """Pre-write check of the whole hand-off against formats/run/handoff.schema.json and the
    schemas it references; returns a list of problems. Raises Stop when the schema files are
    missing, unreadable or use a keyword this checker does not support."""
    schemas = _Schemas(formats or FORMATS)
    root = schemas.load(HANDOFF_SCHEMA)
    schemas.scan(root, HANDOFF_SCHEMA, set())
    errs = []
    _check(schemas, doc, root, HANDOFF_SCHEMA, [], errs)
    return ["hand-off does not match formats/%s at %s" % (HANDOFF_SCHEMA, e) for e in errs]


def _write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, path)


def _taken_versions(run):
    """Version numbers already used in outputs/versions (hand-off files and design folders)."""
    folder = os.path.join(run, VERSIONS)
    taken = set()
    if os.path.isdir(folder):
        for name in os.listdir(folder):
            m = re.match(r"^(?:agent-build-inputs\.v(\d+)\.json|design\.v(\d+)|shareable-set-aside\.v(\d+))$", name)
            if m:
                taken.add(int(next(g for g in m.groups() if g)))
    return taken


def _previous(run, reading):
    """(bytes, parsed dict or None, its version or None) of the current hand-off, or (None, None, None)."""
    path = os.path.join(run, HANDOFF)
    if not os.path.isfile(path):
        return None, None, None
    if not _inside(run, path):
        reading("earlier hand-off", "not read or archived; replaced by the current one",
                "%s resolves outside the run folder" % HANDOFF)
        return None, None, None
    raw = _read_bytes(run, HANDOFF)
    try:
        prev = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        reading("earlier hand-off", "archived as found, stage origins all read as regenerated",
                "%s could not be read as JSON" % HANDOFF)
        return raw, None, None
    if not isinstance(prev, dict):
        reading("earlier hand-off", "archived as found, stage origins all read as regenerated",
                "%s is not an object" % HANDOFF)
        return raw, None, None
    v = prev.get("version")
    if isinstance(v, bool) or not isinstance(v, int) or v < 1:
        v = None
    return raw, prev, v


def _copy_tree_files(src, dst, run, reading, prefix="", seen=None):
    """Copy every file under src to dst, byte for byte. Any entry (or src
    itself) that resolves outside the run folder is skipped with a reading. Return the copied relative paths."""
    seen = set() if seen is None else seen
    real = os.path.realpath(src)
    if real in seen:
        return []
    seen.add(real)
    copied = []
    for name in sorted(os.listdir(src)):
        path = os.path.join(src, name)
        if name == "__pycache__" or name.endswith(".tmp"):
            continue
        rel = prefix + name
        if not _inside(run, path):
            reading("design file %s" % rel, "not copied",
                    "it resolves outside the run folder, so it may hold another workspace's content")
            continue
        if os.path.isdir(path):
            copied += _copy_tree_files(path, os.path.join(dst, name), run, reading, rel + "/", seen)
            continue
        if not os.path.isfile(path):
            continue
        with open(path, "rb") as f:
            data = f.read()
        _write(os.path.join(dst, name), data)
        copied.append(rel)
    return copied


def _swap_in(staged, final, note, label):
    """Replace final with the freshly built staged tree (never refreshed in place). True on success."""
    try:
        if os.path.lexists(final):
            if os.path.isdir(final) and not os.path.islink(final):
                shutil.rmtree(final)
            else:
                os.remove(final)
        if os.path.isdir(staged):
            os.replace(staged, final)
        return True
    except OSError as e:
        note("bundle", "could not replace the %s (%s)" % (label, e), written_as="%s left out" % label)
        shutil.rmtree(staged, ignore_errors=True)
        return False


def _publish_design(run, note, reading):
    """Build stages/architecture/*, stages/rules/* and the test suite into a fresh tree and swap it in as outputs/design/; return
    bundle.design_files (paths relative to outputs/, the hand-off's folder). A failing component is noted, the
    rest still published."""
    final = os.path.join(run, DESIGN)
    dest = final + ".new"
    shutil.rmtree(dest, ignore_errors=True)
    files = {}
    arch = os.path.join(run, ARCH_SRC)
    arch_files = []
    if os.path.isdir(arch) and not _inside(run, arch):
        reading("architecture folder", "not copied",
                "%s resolves outside the run folder, so it may hold another workspace's content" % ARCH_SRC)
        note("bundle", "architecture folder resolves outside the run: %s" % ARCH_SRC,
             written_as="design_files without architecture")
    elif os.path.isdir(arch):
        try:
            arch_files = _copy_tree_files(arch, os.path.join(dest, "architecture"), run, reading)
        except OSError as e:
            note("bundle", "could not publish the architecture copy (%s)" % e,
                 written_as="design_files without architecture")
            arch_files = None
    if arch_files:
        files["architecture_files"] = ["%s/architecture/%s" % (DESIGN_DIR, r) for r in arch_files]
        if "agent.yaml" in arch_files:
            files["architecture"] = "%s/architecture/agent.yaml" % DESIGN_DIR
        else:
            note("bundle", "missing input: %s/agent.yaml; the other architecture files were copied" % ARCH_SRC,
                 written_as="design_files without architecture")
    elif arch_files == [] and not (os.path.isdir(arch) and not _inside(run, arch)):
        note("bundle", "missing input: %s/ (no architecture files to copy)" % ARCH_SRC,
             written_as="design_files without architecture")
    rules_dir = os.path.join(run, RULES_SRC)
    if os.path.isdir(rules_dir) and not _inside(run, rules_dir):
        reading("rules folder", "not copied",
                "%s resolves outside the run folder, so it may hold another workspace's content" % RULES_SRC)
        note("bundle", "rules folder resolves outside the run: %s" % RULES_SRC, blocking=False,
             written_as="design_files without rules")
    else:
        rule_files = []
        if os.path.isdir(rules_dir):
            try:
                rule_files = _copy_tree_files(rules_dir, os.path.join(dest, "rules"), run, reading)
            except OSError as e:
                note("bundle", "could not publish the rules copy (%s)" % e, written_as="design_files without rules")
                rule_files = None
        if rule_files:
            files["rules_files"] = ["%s/rules/%s" % (DESIGN_DIR, r) for r in rule_files]
        if rule_files is not None and "rules.json" in (rule_files or []):
            files["rules"] = "%s/rules/rules.json" % DESIGN_DIR
        elif rule_files is not None:
            note("bundle", "missing input: %s/rules.json" % RULES_SRC, blocking=False,
                 written_as="design_files without rules")
    suite = os.path.join(run, SUITE_SRC)
    if os.path.isfile(suite) and not _inside(run, suite):
        reading("test suite", "not copied",
                "%s resolves outside the run folder, so it may hold another workspace's content" % SUITE_SRC)
        note("bundle", "test suite resolves outside the run: %s" % SUITE_SRC, written_as="design_files without test_suite")
    elif os.path.isfile(suite):
        try:
            _write(os.path.join(dest, "test-suite", "test-suite.json"), _read_bytes(run, SUITE_SRC))
            files["test_suite"] = "%s/test-suite/test-suite.json" % DESIGN_DIR
        except OSError as e:
            note("bundle", "could not publish the test suite copy (%s)" % e, written_as="design_files without test_suite")
    else:
        note("bundle", "missing input: %s" % SUITE_SRC, written_as="design_files without test_suite")
    if not _swap_in(dest, final, note, "design copy"):
        return {}
    return files


def _design_rels(design_files):
    rels = list(design_files.get("architecture_files") or []) + list(design_files.get("rules_files") or [])
    if design_files.get("test_suite"):
        rels.append(design_files["test_suite"])
    return rels


def _earlier_readings(run):
    """Readings recorded by earlier stages: `readings` / `upstream_readings` lists in stage files and builder
    problem records carrying a `reading`, each with its source."""
    out = []

    def take(entry, source):
        if isinstance(entry, dict) and entry.get("reading") not in (None, ""):
            out.append({"what": str(entry.get("what") or entry.get("item") or entry.get("kind") or "reading"),
                        "reading": entry["reading"] if isinstance(entry["reading"], str)
                        else json.dumps(entry["reading"], ensure_ascii=False),
                        "why": str(entry.get("why") or entry.get("detail") or ""), "source": source})

    for _, rels in STAGE_FILES:
        for rel in rels:
            if (not rel.endswith((".json", ".yaml")) or not os.path.isfile(os.path.join(run, rel))
                    or not _inside(run, os.path.join(run, rel))):
                continue
            try:
                with open(os.path.join(run, rel), encoding="utf-8") as f:
                    data = json.load(f)
            except (ValueError, OSError, UnicodeDecodeError):
                continue
            if isinstance(data, dict):
                for key in ("readings", "upstream_readings"):
                    if isinstance(data.get(key), list):
                        for e in data[key]:
                            take(e, rel)
    path = os.path.join(run, PROBLEMS)
    if os.path.isfile(path) and _inside(run, path):
        try:
            with open(path, encoding="utf-8") as f:
                lines = f.read().splitlines()
        except (OSError, UnicodeDecodeError):
            lines = []
        for line in lines:
            try:
                take(json.loads(line), PROBLEMS)
            except ValueError:
                continue
    return out


def _clear_shareable(run, n, reading, note):
    """Keep shareable/ holding only the bundle: anything else is moved aside to outputs/versions/ and recorded,
    so internal trace is never shared and the bundle is still refreshed. A failing move is noted."""
    share = os.path.join(run, SHAREABLE)
    if not os.path.lexists(share):
        return
    aside = os.path.join(run, VERSIONS, "shareable-set-aside.v%d" % n)

    def move(path, name, what, why):
        try:
            os.makedirs(aside, exist_ok=True)
            os.replace(path, os.path.join(aside, name))
            reading(what, "moved aside to %s/shareable-set-aside.v%d/%s" % (VERSIONS, n, name), why)
        except OSError as e:
            note(SHAREABLE, "could not move aside %s (%s)" % (name, e), written_as="left in place")

    if not os.path.isdir(share) or os.path.islink(share):
        move(share, "shareable", "shareable folder", "%s was not a plain folder" % SHAREABLE)
        return
    for name in sorted(os.listdir(share)):
        path = os.path.join(share, name)
        expected = name in SHAREABLE_ENTRIES and not os.path.islink(path) and (
            os.path.isdir(path) if name == DESIGN_DIR else os.path.isfile(path))
        if not expected:
            move(path, name, "unexpected entry in shareable folder: %s" % name,
                 "the shareable folder holds only %s" % ", ".join(SHAREABLE_ENTRIES))


def run_handoff(run):
    """Write the current hand-off (always), its design copy and the shareable bundle; return the assemble report
    (written to RUN/handoff/)."""
    run = os.path.abspath(run)
    if not os.path.isdir(run):
        raise Stop("run folder not found: %s" % os.path.basename(run))
    written, missing, readings = [], [], []

    def note(part_name, reason, blocking=True, written_as="left out"):
        missing.append({"part": part_name, "reason": reason, "blocking": bool(blocking), "written_as": written_as})

    def reading(what, read_as, why):
        readings.append({"what": what, "reading": read_as, "why": why, "source": "handoff"})

    try:
        doc, files = assemble(run)
        missing.extend(doc.pop("_missing", []) or [])
    except Exception as e:  # assemble guards each part; this is the last net
        doc, files = {k: [] for k in LIST_PARTS}, {}
        note(HANDOFF, "assembly failed (%s: %s)" % (type(e).__name__, e))
        doc.update({"stage_input_hashes": {}, "outputs": [], "platform_question": PLATFORM_QUESTION,
                    "platform_answer": "", "bundle": {"shareable": [], "internal_trace": []}})

    # Version: the earlier hand-off is archived at its own number, or the next free one.
    prev_raw, previous, prev_v = _previous(run, reading)
    taken = _taken_versions(run)
    archive_n = None
    if prev_raw is not None or os.path.isdir(os.path.join(run, DESIGN)):
        if prev_v is not None and prev_v not in taken:
            archive_n = prev_v
        else:
            archive_n = max(taken | {prev_v or 0}) + 1
            why = ("the earlier hand-off has no usable version number" if prev_v is None
                   else "version %d is already taken in %s" % (prev_v, VERSIONS))
            if prev_raw is None:
                why = "an earlier design copy was found without its hand-off"
            reading("earlier hand-off version", "archived as version %d" % archive_n, why)
        n = archive_n + 1
    else:
        n = max(taken | {0}) + 1
        if taken:
            reading("hand-off version", "version %d" % n,
                    "no current hand-off, so numbering continues after %s" % VERSIONS)

    # Archive first, then write. A filesystem failure is noted and the current hand-off is still written.
    if prev_raw is not None:
        try:
            _write(os.path.join(run, VERSIONS, "agent-build-inputs.v%d.json" % archive_n), prev_raw)
            written.append("%s/agent-build-inputs.v%d.json" % (VERSIONS, archive_n))
        except OSError as e:
            note(HANDOFF, "could not archive the earlier hand-off (%s)" % e, written_as="earlier hand-off replaced")
    if os.path.isdir(os.path.join(run, DESIGN)):
        try:
            target = os.path.join(run, VERSIONS, "design.v%d" % archive_n)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            os.replace(os.path.join(run, DESIGN), target)
            written.append("%s/design.v%d/" % (VERSIONS, archive_n))
        except OSError as e:
            note("bundle", "could not archive the earlier design copy (%s)" % e,
                 written_as="earlier design copy replaced by a fresh one")

    design_files = _publish_design(run, note, reading)
    if design_files:
        written.append(DESIGN + "/")

    prev_hashes = (previous or {}).get("stage_input_hashes")
    if previous is not None and not isinstance(prev_hashes, dict):
        reading("earlier hand-off stage hashes", "every stage read as regenerated",
                "the earlier hand-off's stage_input_hashes is not an object")
    if not isinstance(prev_hashes, dict):
        prev_hashes = {}
    doc["version"] = n
    origins = {}
    for stage, _ in STAGE_FILES:
        h = (doc.get("stage_input_hashes") or {}).get(stage)
        origins[stage] = ("inherited" if previous is not None and h is not None
                          and prev_hashes.get(stage) == h else "regenerated")
    doc["stage_origins"] = origins
    doc["outputs"] = [{"path": rel, "stage": stage, "origin": origins[stage], "version": str(n)}
                      for stage, _ in STAGE_FILES for rel in files.get(stage, [])]
    bundle = doc.setdefault("bundle", {"shareable": [], "internal_trace": []})
    bundle["design_files"] = design_files
    bundle["shareable"] = list(bundle.get("shareable") or []) + [
        "%s/%s" % (SHAREABLE, r) for r in _design_rels(design_files)]

    _clear_shareable(run, n, reading, note)
    share = os.path.join(run, SHAREABLE)

    def set_aside(target, why):
        """Move a shareable copy that no longer matches this run out of the bundle, recorded."""
        path = os.path.join(run, target)
        if not os.path.lexists(path):
            return
        aside = os.path.join(run, VERSIONS, "shareable-set-aside.v%d" % n)
        try:
            os.makedirs(aside, exist_ok=True)
            os.replace(path, os.path.join(aside, os.path.basename(target)))
            reading("older shareable copy %s" % os.path.basename(target),
                    "moved aside to %s/shareable-set-aside.v%d" % (VERSIONS, n), why)
        except OSError as e:
            note("bundle", "could not move aside the older %s (%s)" % (target, e), written_as="older copy left")

    # Presentation copies and the shareable design copy first, so their outcomes are in the hand-off.
    for rel, label in ((GUIDE, "client-ready guide"), (OWNER_VIEW, "business-owner view")):
        target = SHAREABLE + "/" + os.path.basename(rel)
        src = os.path.join(run, rel)
        if not os.path.isfile(src):
            set_aside(target, "its source %s is missing" % rel)
            continue
        if not _inside(run, src):
            set_aside(target, "its source resolves outside the run folder")
            note("bundle", "%s resolves outside the run: %s" % (label, rel), blocking=False,
                 written_as="left out of shareable/")
            continue
        try:
            body = _relativise(_read_bytes(run, rel).decode("utf-8"), run).encode("utf-8")
            _write(os.path.join(share, os.path.basename(rel)), body)
            written.append(target)
        except (OSError, UnicodeDecodeError) as e:
            set_aside(target, "its source %s could not be read" % rel)
            note("bundle", "cannot read %s: %s (%s)" % (label, rel, e), blocking=False,
                 written_as="left out of shareable/")
    share_design = os.path.join(share, DESIGN_DIR)
    staged = share_design + ".new"
    try:
        shutil.rmtree(staged, ignore_errors=True)
        if design_files and os.path.isdir(os.path.join(run, DESIGN)):
            _copy_tree_files(os.path.join(run, DESIGN), staged, run, reading)
    except OSError as e:
        note("bundle", "could not publish the shareable design copy (%s)" % e,
             written_as="shareable/ without design/")
        shutil.rmtree(staged, ignore_errors=True)
    if _swap_in(staged, share_design, note, "shareable design copy") and os.path.isdir(share_design):
        written.append(SHAREABLE + "/" + DESIGN_DIR + "/")

    def finish(d):
        d["readings"] = _earlier_readings(run) + readings
        d["missing_information"] = [{"part": m["part"], "reason": m["reason"], "written_as": m["written_as"]}
                                    for m in missing]
        reasons = ["%s: %s" % (m["part"], m["reason"]) for m in missing if m["blocking"]]
        d["status"] = "incomplete" if reasons else "complete"
        d["status_reasons"] = reasons
        return _relativise(d, run)

    # Incomplete drafts still name missing required fields. The schema permits
    # partial documents, so relying on its complete-only requirements hides gaps.
    for name in REQUIRED:
        if name not in doc and not any(m["part"] == name for m in missing):
            note(name, "missing required hand-off field: %s" % name)
    doc = finish(doc)
    try:
        problems = validate(doc)
    except Stop as e:
        problems = ["the hand-off could not be checked against formats/%s: %s" % (HANDOFF_SCHEMA, e)]
    if problems:
        for p in problems:
            note(HANDOFF, p)
        doc = finish(doc)
    data = (json.dumps(doc, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    _write(os.path.join(run, HANDOFF), data)
    written.append(HANDOFF)
    _write(os.path.join(share, "agent-build-inputs.json"), data)
    written.append(SHAREABLE + "/agent-build-inputs.json")

    report = {"schema": "agent-design.handoff-assemble-report/1",
              "complete": doc["status"] == "complete" and not missing, "status": doc["status"],
              "version": n, "written": written,
              "missing": [{"part": m["part"], "reason": m["reason"], "written_as": m["written_as"],
                           "blocking": m["blocking"]} for m in missing],
              "readings": doc["readings"]}
    _write(os.path.join(run, REPORT), (json.dumps(report, indent=2, ensure_ascii=False) + "\n").encode("utf-8"))
    return report


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 2 or argv[0] != "--run":
        print("usage: handoff.py --run RUN", file=sys.stderr)
        return 2
    try:
        report = run_handoff(argv[1])
    except Stop as e:
        print("handoff: %s" % e, file=sys.stderr)
        return 2
    for m in report["missing"]:
        print("handoff: %s: %s: %s (written as %s)" % (
            "incomplete" if m["blocking"] else "missing information", m["part"], m["reason"], m["written_as"]),
            file=sys.stderr)
    for r in report["readings"]:
        if r.get("source") == "handoff":
            print("handoff: reading: %s: %s (%s)" % (r["what"], r["reading"], r["why"]), file=sys.stderr)
    print("handoff: wrote %s version %d, status %s" % (HANDOFF, report["version"], report["status"]))
    print("handoff: %d part(s) written, %d note(s); see %s" % (len(report["written"]), len(report["missing"]),
                                                               REPORT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
