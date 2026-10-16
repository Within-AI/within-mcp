#!/usr/bin/env python3
"""The "What we still need to know" register (key criterion 22).

  register.py collect --run RUN            residual sources -> stages/register/raw.json
  register.py check --run RUN [--out F]    register.json and verdicts.json against fresh raw items
  register.py views --run RUN              business-owner.md and builder.md from register.json

Standard library only. Exit codes: 0 pass, 1 fail, 2 usage or unreadable input.
"""
import glob
import importlib.util
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
_state_spec = importlib.util.spec_from_file_location("ad_register_state", os.path.join(HERE, "register_state.py"))
_state = importlib.util.module_from_spec(_state_spec)
_state_spec.loader.exec_module(_state)
closed_by_snapshot = _state.closed_by_snapshot
question_groups = _state.question_groups
internal_question = _state.internal_question
AD = os.path.dirname(HERE)
REPO = os.path.dirname(os.path.dirname(AD))
SOURCES = os.path.join(AD, "stages", "register", "sources.json")
TEXT_RULES = os.path.join(REPO, "skills", "fast-mode-build", "page", "check", "text_rules.json")

REG_DIR = os.path.join("stages", "register")
CHECK_PATH = "skills/agent-design/scripts/register.py"

CLASSES = ["Contradictory", "needs_more_information", "Assumed pending ratification", "adjudication"]
KINDS = ["evidence", "ratification", "escalation"]
REGISTER_SOURCES = ["rules open item", "gate residual", "fix residual", "simulation residual",
                    "departure pending sign-off", "missing future-state step", "upstream failing check",
                    "fields open item", "placement open item", "review finding", "check finding",
                    "recorded problem", "unreadable source"]
ROUTE = ["Targeted Companion capture", "Document upload", "FDE Intake"]
ITEM_FIELDS = ["id", "class", "kind", "source", "unknown", "why_it_matters", "agent_meanwhile",
               "who_can_answer", "closure_route", "resolution_evidence", "breaks_if_wrong",
               "dependent_rules", "group", "reader_verdict"]
TEXT_FIELDS = ["unknown", "why_it_matters", "agent_meanwhile", "who_can_answer",
               "resolution_evidence", "breaks_if_wrong"]
OPEN_ITEM_KINDS = ("missing_input", "policy_choice")
VERDICTS = ("ok", "finding", "cannot_judge")
VERDICT_SCHEMA = "formats/lists/verdict.schema.json"
AGENT_SCHEMA = "formats/design/agent.schema.json"


class SourceError(Exception):
    """A listed source is missing, not JSON or not of its expected shape."""


def _agent_loader(run_dir):
    """check_architecture.load_agent(run_dir) (U8, pins F6), loaded by file path."""
    path = os.path.join(AD, "checks", "check_architecture.py")
    if not os.path.isfile(path):
        raise SourceError("check_architecture.py (U8) not found; cannot read stages/architecture/agent.yaml")
    spec = importlib.util.spec_from_file_location("check_architecture", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.load_agent(run_dir)


# Replaced in tests by a stub; runtime always uses U8's function.
load_agent = _agent_loader


def _load_sources():
    with open(SOURCES) as f:
        return json.load(f)["sources"]


def _read_json(run_dir, rel):
    path = os.path.join(run_dir, rel)
    if not os.path.isfile(path):
        raise SourceError("source missing: %s" % rel)
    try:
        with open(path) as f:
            return json.load(f)
    except (ValueError, OSError) as e:
        raise SourceError("source not valid JSON: %s (%s)" % (rel, e))


def _shape(cond, rel, what):
    if not cond:
        raise SourceError("source not of its expected shape: %s (%s)" % (rel, what))


# ---- schema validation (standard library; the keyword subset U1's and U2's formats use) ---------

_META = {"$schema", "$id", "$defs", "version", "title", "description", "default", "$comment"}
_TYPES = {"object": dict, "array": list, "string": str, "boolean": bool, "null": type(None)}
_DOCS = {}


def _doc(path):
    path = os.path.normpath(path)
    if path not in _DOCS:
        with open(path) as f:
            _DOCS[path] = json.load(f)
    return path, _DOCS[path]


def _pointer(doc, frag):
    node = doc
    for part in [p for p in frag.lstrip("/").split("/") if p]:
        node = node[part.replace("~1", "/").replace("~0", "~")]
    return node


def _is_type(x, t):
    if t == "integer":
        return isinstance(x, int) and not isinstance(x, bool) or isinstance(x, float) and x.is_integer()
    if t == "number":
        return isinstance(x, (int, float)) and not isinstance(x, bool)
    return isinstance(x, _TYPES[t])


def _errors(x, s, base, at):
    """Errors of instance x against schema s; base is the file s came from (for $ref)."""
    if s is True:
        return []
    if s is False:
        return ["%s: not allowed" % at]
    out = []
    for k in s:
        if k not in _META and k not in _KEYWORDS:
            raise ValueError("schema keyword %s not supported by register.py's validator (%s)" % (k, base))
    if "$ref" in s:
        ref, _, frag = s["$ref"].partition("#")
        rbase, rdoc = _doc(os.path.join(os.path.dirname(base), ref)) if ref else _doc(base)
        out += _errors(x, _pointer(rdoc, frag), rbase, at)
    if "type" in s:
        types = s["type"] if isinstance(s["type"], list) else [s["type"]]
        if not any(_is_type(x, t) for t in types):
            return out + ["%s: expected %s" % (at, " or ".join(types))]
    if "enum" in s and x not in s["enum"]:
        out.append("%s: %r is not one of %s" % (at, x, s["enum"]))
    if "const" in s and x != s["const"]:
        out.append("%s: must be %r" % (at, s["const"]))
    if isinstance(x, str):
        if len(x) < s.get("minLength", 0):
            out.append("%s: shorter than %d" % (at, s["minLength"]))
        if "pattern" in s and not re.search(s["pattern"], x):
            out.append("%s: does not match %s" % (at, s["pattern"]))
    if _is_type(x, "number"):
        if "minimum" in s and x < s["minimum"]:
            out.append("%s: below %s" % (at, s["minimum"]))
        if "maximum" in s and x > s["maximum"]:
            out.append("%s: above %s" % (at, s["maximum"]))
    if isinstance(x, list):
        if len(x) < s.get("minItems", 0):
            out.append("%s: fewer than %d entries" % (at, s["minItems"]))
        if "maxItems" in s and len(x) > s["maxItems"]:
            out.append("%s: more than %d entries" % (at, s["maxItems"]))
        if "items" in s:
            for i, e in enumerate(x):
                out += _errors(e, s["items"], base, "%s[%d]" % (at, i))
    if isinstance(x, dict):
        for r in s.get("required", []):
            if r not in x:
                out.append("%s: misses %s" % (at, r))
        props = s.get("properties", {})
        for k, v in x.items():
            if k in props:
                out += _errors(v, props[k], base, "%s.%s" % (at, k))
            elif "additionalProperties" in s:
                out += _errors(v, s["additionalProperties"], base, "%s.%s" % (at, k))
    for sub in s.get("allOf", []):
        out += _errors(x, sub, base, at)
    if "anyOf" in s and not any(not _errors(x, sub, base, at) for sub in s["anyOf"]):
        out.append("%s: matches none of anyOf" % at)
    if "oneOf" in s and sum(1 for sub in s["oneOf"] if not _errors(x, sub, base, at)) != 1:
        out.append("%s: does not match exactly one of oneOf" % at)
    if "if" in s:
        branch = s.get("then") if not _errors(x, s["if"], base, at) else s.get("else")
        if branch is not None:
            out += _errors(x, branch, base, at)
    return out


_KEYWORDS = {"$ref", "type", "enum", "const", "minLength", "pattern", "minimum", "maximum", "minItems",
             "maxItems", "items", "required", "properties", "additionalProperties", "allOf", "anyOf", "oneOf",
             "if", "then", "else"}


def schema_errors(instance, schema_ref):
    """Validate against `formats/...json[#/pointer]` (relative to skills/agent-design)."""
    path, _, frag = schema_ref.partition("#")
    base, doc = _doc(os.path.join(AD, path))
    return _errors(instance, _pointer(doc, frag), base, "$")


def _validate_source(data, src):
    """A whole listed file against its pinned format (sources.json `schema` without `schema_each`)."""
    if not src.get("schema") or src.get("schema_each"):
        return
    try:
        errs = schema_errors(data, src["schema"])
    except (OSError, ValueError, KeyError) as e:
        raise SourceError("cannot validate %s against %s: %s" % (src["path"], src["schema"], e))
    _shape(not errs, src["path"], "against %s: %s" % (src["schema"], "; ".join(errs[:5])))


def _entry_errors(entry, src):
    """One entry of a `schema_each` source against its pinned format."""
    if not src.get("schema") or not src.get("schema_each"):
        return []
    try:
        return schema_errors(entry, src["schema"])
    except (OSError, ValueError, KeyError) as e:
        return ["cannot validate against %s: %s" % (src["schema"], e)]


def _raw(source, register_source, entry_id, index, text, **extra):
    sid = entry_id if isinstance(entry_id, str) and entry_id else "%s#%d" % (source, index)
    item = {"raw_id": "%s:%s" % (source, sid), "source": source, "source_id": sid,
            "register_source": register_source, "text": text}
    item.update(extra)
    return item


class _BadLine(str):
    """A JSONL line that will not parse, kept as its text so the odd item can show it."""


def _odd(src, rel, index, entry, reason):
    """A recorded item for an entry that does not match its source's item shape (never a stop)."""
    try:
        raw = str(entry) if isinstance(entry, _BadLine) else json.dumps(entry, sort_keys=True, default=str)
    except (TypeError, ValueError):
        raw = repr(entry)
    sid = "%s#%s" % (rel, index)
    return {"raw_id": "unreadable:%s" % sid, "source": "unreadable", "source_id": sid,
            "register_source": "unreadable source", "text": reason, "owner": src.get("stage") or "register",
            "odd": {"path": rel, "index": index, "raw": raw[:500], "reason": reason}}


def _unreadable(src, rel, reason):
    """A recorded item for a source file that is missing after its stage finished or will not be read."""
    sid = rel
    return {"raw_id": "unreadable:%s" % sid, "source": "unreadable", "source_id": sid,
            "register_source": "unreadable source", "text": reason, "owner": src.get("stage") or "register",
            "odd": {"path": rel, "index": None, "raw": "", "reason": reason}}


def _items_list(data, rel, key="items"):
    if isinstance(data, dict):
        _shape(isinstance(data.get(key), list), rel, "%s must be a list" % key)
        data = data[key]
    _shape(isinstance(data, list), rel, "expected a list or {\"%s\": [...]}" % key)
    return data


def _text(e, *keys):
    parts = [str(e[k]) for k in keys if isinstance(e.get(k), (str, int, float)) and str(e.get(k))]
    if not parts:
        for k in ("missing", "unknown"):
            if e.get(k):
                parts.append(json.dumps(e[k]))
    return "; ".join(parts)


def _each(src, rel, entries, fn, out):
    """fn(i, entry) -> list of raw items; an entry fn rejects becomes a recorded odd item; the rest still read."""
    for i, e in enumerate(entries):
        errs = _entry_errors(e, src)
        if errs:
            out.append(_odd(src, rel, i, e, "entry does not match %s: %s" % (src["schema"], "; ".join(errs[:3]))))
            continue
        try:
            if isinstance(e, _BadLine):
                raise SourceError("entry %d (a JSONL line) is not valid JSON" % i)
            if not isinstance(e, dict) and src["shape"] != "rules-not-demonstrated":
                raise SourceError("entry %d is not an object" % i)
            got = fn(i, e)
            if any(not (isinstance(g.get("text"), str) and g["text"].strip()) for g in got):
                raise SourceError("entry %d carries no text the register can show" % i)
            out.extend(got)
        except SourceError as exc:
            out.append(_odd(src, rel, i, e, str(exc)))
        except Exception as exc:  # an odd entry never stops collection
            out.append(_odd(src, rel, i, e, "%s: %s" % (type(exc).__name__, exc)))


OPEN_ITEM_SOURCE = {"rules-open-items": "rules open item", "fields-open-items": "fields open item",
                    "placement-open-items": "placement open item", "gate-residuals": "gate residual",
                    "fix-leftovers": "fix residual"}
GRANT_CHECKS = ("write-grants", "check_grants", "write_grants")


def _read_agent(run_dir, src, rel, out):
    try:
        agent = load_agent(run_dir)
    except SourceError:
        raise
    except Exception as e:  # load_agent's own failures
        raise SourceError("source not readable: %s (%s)" % (rel, e))
    _shape(isinstance(agent, dict), rel, "not an object")
    header = agent.get("header")
    _shape(isinstance(header, dict), rel, "header must be an object")
    deps, writes = agent.get("departures"), header.get("proposed_writes")
    for part, inst in (("departures", deps), ("header.proposed_writes", writes)):
        _shape(isinstance(inst, list), rel, "%s must be a list" % part)
    dsrc = dict(src, schema=AGENT_SCHEMA + "#/properties/departures/items", schema_each="departures")
    wsrc = dict(src, schema=AGENT_SCHEMA + "#/properties/header/properties/proposed_writes/items",
                schema_each="proposed_writes")

    def dep(i, d):
        got = []
        if d.get("sign_off") == "pending business-owner sign-off":
            got.append(_raw("departure-pending-sign-off", "departure pending sign-off", d.get("id"), i,
                            _text(d, "change", "detail")))
        if d.get("missing_future_state_step") is True:
            got.append(_raw("missing-future-state-step", "missing future-state step", d.get("id"), i,
                            _text(d, "change", "detail", "routed_to")))
        return got

    def wr(i, w):
        return [_raw("proposed-write", "departure pending sign-off", w.get("grant"), i,
                     _text(w, "step", "system", "grant"), ratification=True)]

    _each(dsrc, rel + " departures", deps, dep, out)
    _each(wsrc, rel + " header.proposed_writes", writes, wr, out)


def _read_data(run_dir, src, rel, data, out):
    """Raw items of one parsed listed file; a wrong top-level shape raises SourceError, an odd entry is recorded."""
    shape, key = src["shape"], src["key"]
    _validate_source(data, src)
    if shape == "open_items":
        reg = OPEN_ITEM_SOURCE.get(key, "rules open item")

        def one(i, e):
            extra = {}
            if key == "rules-open-items":
                kind = e.get("kind")
                if kind not in OPEN_ITEM_KINDS:
                    raise SourceError("rules open item %s has kind %r, not missing_input or policy_choice"
                                      % (e.get("id") or "%s#%d" % (key, i), kind))
                extra["open_item_kind"] = kind
                extra["question_for_person"] = kind == "policy_choice"
            if isinstance(e.get("decision"), str) and e["decision"]:
                extra["decision"] = e["decision"]
            return [_raw(key, reg, e.get("id"), i, _text(e, "decision", "unknown", "detail"), **extra)]
        _each(src, rel, _items_list(data, rel), one, out)
    elif shape == "verdict_findings":
        def one(i, e):
            if e.get("verdict", "finding") != "finding":
                return []
            return [_raw(key, "review finding", e.get("id"), i, _text(e, "detail", "subject"),
                         owner="architecture")]
        _each(src, rel, _items_list(data, rel), one, out)
    elif shape == "problems":
        _shape(isinstance(data, dict) and isinstance(data.get("problems"), list), rel, "problems must be a list")
        name = os.path.splitext(os.path.basename(rel))[0]

        def one(i, p):
            _shape(isinstance(p.get("defect"), str) and p["defect"], rel, "problem %d names no defect" % i)
            return [_raw("check-finding", "check finding", "%s#%d" % (name, i), i,
                         _text(p, "defect", "where", "detail"), check=name, owner=src.get("stage") or name)]
        _each(src, rel, data["problems"], one, out)
    elif shape == "grants":
        # Pin C3: upstream grant problems come only from the grants check's split. Unresolved ones are
        # questions; recorded ones (a reading answers them) are not.
        _shape(isinstance(data, dict) and "upstream_unresolved" in data, rel,
               "the grants report has no upstream_unresolved split")
        _shape(isinstance(data["upstream_unresolved"], list), rel, "upstream_unresolved must be a list")

        def one(i, p):
            _shape(isinstance(p.get("defect"), str) and p["defect"], rel, "upstream problem %d names no defect" % i)
            return [_raw("upstream-grant-problem", "upstream failing check", None, i,
                         _text(p, "defect", "where", "detail"), defect=p["defect"], owner="upstream")]
        _each(src, rel, data["upstream_unresolved"], one, out)
    elif shape == "jsonl":
        def one(i, rec):
            if rec.get("kind") == "register-source":
                return []  # register.py's own notes; their item is already in raw.json
            return [_raw("recorded-problem", "recorded problem", None, i,
                         _text(rec, "kind", "item", "detail"), owner=rec.get("stage") or "run")]
        _each(src, rel, data, one, out)
    elif shape == "walk":
        _shape(isinstance(data, dict) and isinstance(data.get("cases"), list), rel, "cases must be a list")
        err = data.get("error")
        if err is not None:
            out.append(_raw("simulation-error", "simulation residual", "error", 0, str(err)))

        def case(i, c):
            if c.get("drop_label"):
                return [_raw("simulation-dropped-case", "simulation residual", c.get("case_id"), i,
                             "%s (%s)" % (c["drop_label"], c.get("state")))]
            return []
        _each(src, rel, data["cases"], case, out)
        # Pin I7: summary.rules_not_demonstrated lists the undemonstrated rule ids, one raw item each;
        # a summary with counts only is the older shape and gives one aggregate item.
        summary = data.get("summary") if isinstance(data.get("summary"), dict) else {}
        listed = summary.get("rules_not_demonstrated")
        if "rules_not_demonstrated" in summary:
            _shape(isinstance(listed, list), rel, "summary.rules_not_demonstrated must be a list of rule ids")
            for i, r in enumerate(listed):
                if isinstance(r, str) and r:
                    out.append(_raw("simulation-rule-not-demonstrated", "simulation residual", r, i,
                                    "rule not demonstrated", dependent_rules=[r]))
                else:
                    out.append(_odd(src, rel + " summary.rules_not_demonstrated", i, r, "not a rule id"))
        else:
            total, shown = summary.get("rules_total"), summary.get("rules_demonstrated")
            if isinstance(total, int) and isinstance(shown, int) and shown < total:
                out.append(_raw("simulation-rule-not-demonstrated", "simulation residual", None, 0,
                                "%d of %d rules not demonstrated" % (total - shown, total)))
    elif shape == "report":
        _shape(isinstance(data, dict) and isinstance(data.get("problems"), list), rel, "problems must be a list")

        def prob(i, p):
            return [_raw("simulation-prose-disagreement", "simulation residual", p.get("id"), i,
                         _text(p, "defect", "where", "detail"))]
        _each(src, rel, data["problems"], prob, out)
        # check_read (U20b) passes and lists each reader `finding` or `cannot_judge` in residuals[]
        # (rule_id, verdict, detail): one raw item per entry, keyed by the rule it names.
        residuals = data.get("residuals", [])
        _shape(isinstance(residuals, list), rel, "residuals must be a list")
        seen = set()

        def res(i, r):
            _shape(r.get("verdict") in ("finding", "cannot_judge") and isinstance(r.get("rule_id"), str)
                   and r["rule_id"], rel, "residual %d needs a rule_id and a verdict of finding or cannot_judge" % i)
            rid = r["rule_id"] if r["rule_id"] not in seen else "%s#%d" % (r["rule_id"], i)
            seen.add(r["rule_id"])
            return [_raw("simulation-prose-disagreement", "simulation residual", rid, i,
                         _text(r, "verdict", "detail"), dependent_rules=[r["rule_id"]], read_verdict=r["verdict"])]
        _each(src, rel + " residuals", residuals, res, out)
    elif shape == "screen":
        # judgment screen flags (U20b): they never stop the stage, so each one is a visible residual naming its case
        _shape(isinstance(data, dict) and isinstance(data.get("problems"), list), rel, "problems must be a list")
        _each(src, rel, data["problems"], lambda i, p: [_raw("simulation-judgment-screen", "simulation residual",
                                                             None, i, _text(p, "where", "defect", "detail"))], out)
    elif shape == "intake":
        _shape(isinstance(data, dict) and isinstance(data.get("upstream_failing_checks"), list), rel,
               "upstream_failing_checks must be a list")

        def one(i, c):
            if c.get("check") in GRANT_CHECKS:
                return []  # pin C3: grant problems come from the grants check report's unresolved split only
            return [_raw("upstream-failing-check", "upstream failing check", None, i,
                         _text(c, "stage", "check", "result", "detail"), owner="upstream")]
        _each(src, rel, data["upstream_failing_checks"], one, out)
    elif shape == "rules":
        _shape(isinstance(data, dict) and isinstance(data.get("rules"), list), rel, "rules must be a list")

        def one(i, r):
            got = []
            deps = [r["id"]] if isinstance(r.get("id"), str) and r["id"] else []
            if r.get("status") == "proposed":
                got.append(_raw("proposed-rule", "rules open item", r.get("id"), i, _text(r, "prose"),
                                ratification=True, dependent_rules=deps))
            if r.get("evidence_state") == "Assumed":
                got.append(_raw("assumed-rule", "rules open item", r.get("id"), i, _text(r, "prose"),
                                ratification=True, dependent_rules=deps))
            return got
        _each(src, rel, data["rules"], one, out)
    elif shape == "declarations":
        _shape(isinstance(data, dict) and isinstance(data.get("stand_ins"), list), rel, "stand_ins must be a list")
        _each(src, rel, data["stand_ins"], lambda i, s: [_raw("stand-in", "rules open item", s.get("id") or s.get("token"),
                                                              i, _text(s, "token", "meaning", "detail"),
                                                              ratification=True)], out)
    else:
        raise SourceError("unknown source shape %r in sources.json" % shape)


def _load_file(path, rel, shape):
    try:
        with open(path, encoding="utf-8") as f:
            if shape == "jsonl":
                lines = []
                for n, line in enumerate(f):
                    if line.strip():
                        try:
                            lines.append(json.loads(line))
                        except ValueError:
                            lines.append(_BadLine(line.strip()))
                return lines
            return json.load(f)
    except (ValueError, OSError, UnicodeDecodeError) as e:
        raise SourceError("source not valid JSON: %s (%s)" % (rel, e))


def stage_states(run_dir, required=False, problems=None):
    """{stage key: status} from state.json. Raises StateError if it will not parse, or is missing when required.

    A missing state.json gives None when not required. A parseable state.json without a stages object raises
    StateError, unless a problems list is given: then it is recorded there as a state-shape finding and the
    stages count as unknown (None), so no missing stage output is taken as a finished stage's."""
    path = os.path.join(run_dir, "state.json")
    if not os.path.isfile(path):
        if required:
            raise StateError("state.json is missing")
        return None
    try:
        with open(path, encoding="utf-8") as f:
            st = json.load(f)
    except (ValueError, OSError, UnicodeDecodeError) as e:
        raise StateError("state.json cannot be read (%s)" % e)
    stages = st.get("stages") if isinstance(st, dict) else None
    if not isinstance(stages, dict):
        detail = ("state.json is %s, not an object" % type(st).__name__ if not isinstance(st, dict)
                  else "state.json has no stages object")
        if problems is None:
            raise StateError("state.json cannot be read (%s)" % detail)
        problems.append({"defect": "state-shape", "where": "state.json", "detail": detail})
        return None
    return {k: (v.get("status") if isinstance(v, dict) else v) for k, v in stages.items()}


class StateError(Exception):
    """The run's state.json will not parse: the only input that stops collect."""


DONE_STATES = ("done", "done with recorded problems")


def _paths(run_dir, src):
    if any(ch in src["path"] for ch in "*?["):
        found = sorted(os.path.relpath(p, run_dir) for p in glob.glob(os.path.join(run_dir, src["path"])))
        found = [p.replace(os.sep, "/") for p in found]
        return [p for p in found if p not in src.get("exclude", [])], True
    return [src["path"]], False


def _read_source(run_dir, src, states=None, notes=None):
    """Raw items of one listed source; never raises on a missing, unparsed or odd source (see sources.json)."""
    out = []
    notes = notes if notes is not None else []
    rels, is_glob = _paths(run_dir, src)
    for rel in rels:
        path = os.path.join(run_dir, rel)
        if not os.path.isfile(path):
            stage = src.get("stage")
            ran = states is not None and stage is not None and states.get(stage) in DONE_STATES
            if not ran:
                notes.append({"skipped": rel})
                notes.append({"account": {"key": src["key"], "path": rel, "status": "skipped"}})
                continue  # the stage has not run (or no stage writes it): skipped and counted
            reason = "%s is missing although stage %s finished" % (rel, stage)
            out.append(_unreadable(src, rel, reason))
            notes.append({"recorded": rel, "reason": reason})
            notes.append({"account": {"key": src["key"], "path": rel, "status": "recorded"}})
            continue
        before = len(out)
        status = "read"
        try:
            if src["shape"] == "agent":
                _read_agent(run_dir, src, rel, out)
            else:
                _read_data(run_dir, src, rel, _load_file(path, rel, src["shape"]), out)
        except SourceError as e:
            del out[before:]
            out.append(_unreadable(src, rel, str(e)))
            notes.append({"recorded": rel, "reason": str(e)})
            status = "recorded"
        except Exception as e:  # nothing an earlier stage wrote stops the register
            del out[before:]
            out.append(_unreadable(src, rel, "%s: %s" % (type(e).__name__, e)))
            notes.append({"recorded": rel, "reason": "%s: %s" % (type(e).__name__, e)})
            status = "recorded"
        notes.append({"account": {"key": src["key"], "path": rel, "status": status,
                                  "items": len(out) - before}})
        for it in out[before:]:
            if it.get("odd") and it["odd"]["index"] is not None:
                notes.append({"recorded": "%s#%s" % (rel, it["odd"]["index"]), "reason": it["odd"]["reason"]})
    return out


def build_raw(run_dir, notes=None, state_problems=None, state_required=False):
    states = stage_states(run_dir, state_required, state_problems)
    notes = notes if notes is not None else []
    items, seen = [], {}
    for src in _load_sources():
        for it in _read_source(run_dir, src, states, notes):
            # raw ids stay unique: a repeated id gets the next free #n suffix
            rid = it["raw_id"]
            if rid in seen:
                seen[rid] += 1
                it["raw_id"] = "%s#%d" % (rid, seen[rid])
            else:
                seen[rid] = 1
            items.append(it)
    counts = {"items": len(items), "skipped": sum(1 for n in notes if "skipped" in n),
              "recorded": sum(1 for n in notes if "recorded" in n)}
    return {"schema": "agent-design.register-raw/2", "items": items, "counts": counts,
            "skipped": [n["skipped"] for n in notes if "skipped" in n],
            # one line per listed file (globs expanded): read, skipped (stage not finished) or recorded
            "sources": [n["account"] for n in notes if "account" in n]}


def _builder_note(run_dir, notes):
    """One builder-log line per recorded source problem (builder/problems.jsonl), not repeated on a rerun."""
    recs = [n for n in notes if "recorded" in n]
    if not recs:
        return
    path = os.path.join(run_dir, "builder", "problems.jsonl")
    have = set()
    if os.path.isfile(path):
        try:
            with open(path, encoding="utf-8") as f:
                have = set(line.strip() for line in f)
        except OSError:
            have = set()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            for n in recs:
                line = json.dumps({"kind": "register-source", "stage": "register", "item": n["recorded"],
                                   "detail": "recorded as a register item: %s" % n["reason"]}, sort_keys=True)
                if line not in have:
                    f.write(line + "\n")
                    have.add(line)
    except OSError:
        pass


def cmd_collect(run_dir):
    notes = []
    try:
        raw = build_raw(run_dir, notes)
    except StateError as e:
        print("register collect: %s" % e, file=sys.stderr)
        return 2
    out_dir = os.path.join(run_dir, REG_DIR)
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "raw.json"), "w") as f:
        json.dump(raw, f, indent=2)
        f.write("\n")
    _builder_note(run_dir, notes)
    for n in notes:
        if "recorded" in n:
            print("recorded   %s: %s" % (n["recorded"], n["reason"]))
    print("register collect: %d raw items (%d recorded source problems, %d skipped) -> %s/raw.json"
          % (len(raw["items"]), raw["counts"]["recorded"], raw["counts"]["skipped"], REG_DIR))
    return 0


# ---- check ------------------------------------------------------------------------------

def _item_problems(item, where, groups):
    probs = []

    def p(defect, detail):
        probs.append({"defect": defect, "where": where, "detail": detail})

    if not isinstance(item, dict):
        p("item-not-object", "register item is not an object")
        return probs
    if item.get("kind") == "access" or ("system" in item and "assumption" in item):
        p("access-in-items", "an access assumption belongs in access[], not among the questions")
        return probs
    for field in ITEM_FIELDS:
        if field not in item:
            p("missing-field", "missing field %s" % field)
    if "class" in item and item["class"] not in CLASSES:
        p("class-off-list", "class %r is not one of %s" % (item["class"], ", ".join(CLASSES)))
    if "kind" in item and item["kind"] not in KINDS:
        p("kind-off-list", "kind %r is not one of %s" % (item["kind"], ", ".join(KINDS)))
    if "source" in item and item["source"] not in REGISTER_SOURCES:
        p("source-off-list", "source %r is not a register source" % (item["source"],))
    for field in TEXT_FIELDS:
        if field in item and not (isinstance(item[field], str) and item[field].strip()):
            p("empty-field", "%s must be non-empty text" % field)
    route = item.get("closure_route")
    if "closure_route" in item:
        if not isinstance(route, list):
            p("route-not-list", "closure_route must be a list")
        else:
            off = [s for s in route if s not in ROUTE]
            if off:
                p("route-step-off-list", "closure route steps %r are not on the route" % off)
            else:
                idx = [ROUTE.index(s) for s in route]
                if idx != sorted(set(idx)):
                    p("route-out-of-order", "closure route must run Targeted Companion capture -> "
                                            "Document upload -> FDE Intake, no step twice")
            if item.get("kind") == "escalation" and route:
                p("escalation-as-route", "an escalation has no closure route; it goes to people to decide")
    if "dependent_rules" in item and not (isinstance(item["dependent_rules"], list)
                                          and all(isinstance(r, str) and r for r in item["dependent_rules"])):
        p("dependent-rules-not-list", "dependent_rules must be a list of rule ids")
    if "group" in item:
        g = item["group"]
        if not isinstance(g, dict) or not g.get("id"):
            p("group-no-id", "group must carry an id")
        elif not g.get("cheapest_item"):
            p("group-no-cheapest-item", "group %s names no cheapest item" % g["id"])
        elif g["cheapest_item"] not in groups.get(g["id"], set()):
            p("group-no-cheapest-item", "group %s names %s as cheapest item, which is not in that group"
              % (g["id"], g["cheapest_item"]))
    return probs


def _access_problems(a, where):
    probs = []
    if not isinstance(a, dict):
        return [{"defect": "access-not-object", "where": where, "detail": "access entry is not an object"}]
    for field in ("id", "kind", "system", "assumption", "evidence_state"):
        if field not in a:
            probs.append({"defect": "missing-field", "where": where, "detail": "access entry misses %s" % field})
    if a.get("kind", "access") != "access" or a.get("evidence_state", "Assumed") != "Assumed":
        probs.append({"defect": "access-shape", "where": where,
                      "detail": "access entries carry kind access and evidence_state Assumed"})
    return probs


def _open_problems(o, where):
    """An identity or runtime open: which service identity the agent writes as or where it runs, and who decides."""
    if not isinstance(o, dict):
        return [{"defect": "identity-runtime-open-not-object", "where": where, "detail": "entry is not an object"}]
    probs = []
    for field in ("id", "question", "adjudicator"):
        if not (isinstance(o.get(field), str) and o[field].strip()):
            probs.append({"defect": "no-adjudicator" if field == "adjudicator" else "missing-field",
                          "where": where, "detail": "identity or runtime open misses %s" % field})
    return probs


def verdict_file_problems(v):
    """U1's verdict format (formats/lists/verdict.schema.json), checked in full."""
    try:
        return schema_errors(v, VERDICT_SCHEMA)
    except (OSError, ValueError, KeyError) as e:
        return ["cannot validate against %s: %s" % (VERDICT_SCHEMA, e)]


def _report(result, problems, counts):
    code = {"pass": 0, "fail": 1, "error": 2}[result]
    return {"check": "register", "path": CHECK_PATH, "run": ".", "exit_code": code, "result": result,
            "problems": problems, "counts": counts,
            "agent_fallback": "Read stages/register/register.json against stages/register/raw.json by hand: "
                              "every residual kept, every field present, every question with a reader verdict."}


REGISTER_FIELDS = (("items", list), ("access", list), ("views", dict), ("identity_runtime_opens", list))


def register_shape_problems(reg, reg_rel):
    """Shape problems of a parseable register.json: not an object, a required key missing or of the wrong type."""
    if not isinstance(reg, dict):
        return [{"defect": "register-shape", "where": reg_rel,
                 "detail": "register is %s, not an object" % type(reg).__name__}]
    probs = []
    for field, t in REGISTER_FIELDS:
        if field not in reg:
            probs.append({"defect": "register-shape", "where": reg_rel, "detail": "register misses %s" % field})
        elif not isinstance(reg[field], t):
            probs.append({"defect": "register-shape", "where": reg_rel,
                          "detail": "%s is %s, not %s" % (field, type(reg[field]).__name__, t.__name__)})
    return probs


def _output_note(run_dir, problems, kind="register-shape"):
    """One builder-log line (builder/problems.jsonl) per recorded register shape or output problem, not repeated."""
    lines = [json.dumps({"kind": kind, "stage": "register", "item": p["where"], "detail": p["detail"]},
                        sort_keys=True)
             for p in problems if p["defect"] == kind]
    if not lines:
        return
    path = os.path.join(run_dir, "builder", "problems.jsonl")
    have = set()
    try:
        if os.path.isfile(path):
            with open(path, encoding="utf-8") as f:
                have = set(line.strip() for line in f)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            for line in lines:
                if line not in have:
                    f.write(line + "\n")
                    have.add(line)
    except OSError:
        pass


def check(run_dir):
    state_problems = []
    try:
        raw = build_raw(run_dir, state_problems=state_problems, state_required=True)
    except StateError as e:
        return _report("error", [{"defect": "state-unreadable", "where": "state.json", "detail": str(e)}], {})
    _output_note(run_dir, state_problems, kind="state-shape")
    reg_rel = os.path.join(REG_DIR, "register.json")
    try:
        reg = _read_json(run_dir, reg_rel)
    except SourceError as e:
        return _report("fail", state_problems + [{"defect": "register-unreadable", "where": reg_rel,
                                                  "detail": str(e)}], {"raw_items": len(raw["items"])})
    # Missing keys keep their missing-field defect and identity_runtime_opens its own; the rest is register-shape.
    problems = state_problems + [p for p in register_shape_problems(reg, reg_rel)
                                 if not p["detail"].startswith(("register misses", "identity_runtime_opens is"))]
    if not isinstance(reg, dict):
        _output_note(run_dir, problems)
        return _report("fail", problems, {"raw_items": len(raw["items"])})
    for field in ("items", "access", "views", "identity_runtime_opens"):
        if field not in reg:
            problems.append({"defect": "missing-field", "where": reg_rel, "detail": "register misses %s" % field})
    items = reg.get("items") if isinstance(reg.get("items"), list) else []
    access = reg.get("access") if isinstance(reg.get("access"), list) else []
    groups = {}
    for it in items:
        if isinstance(it, dict) and isinstance(it.get("group"), dict) and it["group"].get("id"):
            groups.setdefault(it["group"]["id"], set()).add(it.get("id"))
    for i, it in enumerate(items):
        problems += _item_problems(it, "items[%d] %s" % (i, it.get("id", "") if isinstance(it, dict) else ""), groups)
    for i, a in enumerate(access):
        problems += _access_problems(a, "access[%d]" % i)
    opens = reg.get("identity_runtime_opens")
    if "identity_runtime_opens" in reg and not isinstance(opens, list):
        problems.append({"defect": "identity-runtime-opens-not-list", "where": reg_rel,
                         "detail": "identity_runtime_opens must be a list"})
    for i, o in enumerate(opens if isinstance(opens, list) else []):
        problems += _open_problems(o, "identity_runtime_opens[%d]" % i)
    _output_note(run_dir, [dict(p, defect="register-shape") for p in problems if p["where"] == reg_rel])
    covered = set()
    for entry in items + access:
        if isinstance(entry, dict) and isinstance(entry.get("raw_ids"), list):
            covered.update(entry["raw_ids"])
    for r in raw["items"]:
        if r["raw_id"] not in covered:
            problems.append({"defect": "dropped-residual", "where": r["raw_id"],
                             "detail": "raw item %s from %s is in no register entry's raw_ids"
                                       % (r["raw_id"], r["source"])})
    # Reader verdicts: one per question (items[], not access[]), matched by subject = item id.
    v_rel = os.path.join(REG_DIR, "verdicts.json")
    verdict_by_item = {}
    try:
        verdicts = _read_json(run_dir, v_rel)
        errs = verdict_file_problems(verdicts)
        if errs:
            problems.append({"defect": "verdict-file-invalid", "where": v_rel, "detail": "; ".join(errs)})
        else:
            for v in verdicts["items"]:
                verdict_by_item[v["subject"]] = v["verdict"]
    except SourceError as e:
        problems.append({"defect": "verdict-file-invalid", "where": v_rel, "detail": str(e)})
        verdicts = None
    if verdicts is not None and not any(p["defect"] == "verdict-file-invalid" for p in problems):
        for it in items:
            if isinstance(it, dict) and it.get("id") not in verdict_by_item:
                problems.append({"defect": "missing-verdict", "where": str(it.get("id")),
                                 "detail": "question %s has no reader verdict" % it.get("id")})
    counts = {"raw_items": len(raw["items"]), "questions": len(items), "access": len(access),
              "verdicts": len(verdict_by_item)}
    for value in VERDICTS:
        counts["verdict_" + value] = sum(1 for v in verdict_by_item.values() if v == value)
    return _report("fail" if problems else "pass", problems, counts)


def cmd_check(run_dir, out):
    report = check(run_dir)
    path = out or os.path.join(run_dir, "checks", "register.json")
    try:
        d = os.path.dirname(path)
        if d:
            os.makedirs(d, exist_ok=True)
        with open(path, "w") as f:
            json.dump(report, f, indent=2)
            f.write("\n")
    except OSError:
        pass
    for p in report["problems"]:
        print("%s: %s: %s" % (p["defect"], p["where"], p["detail"]))
    print("register check: %s" % report["result"])
    return report["exit_code"]


# ---- views ------------------------------------------------------------------------------

def _rule_ids(run_dir):
    path = os.path.join(run_dir, "stages", "rules", "rules.json")
    try:
        with open(path) as f:
            return [r["id"] for r in json.load(f).get("rules", []) if isinstance(r, dict) and r.get("id")]
    except (OSError, ValueError, AttributeError):
        return []


def _field_regexes():
    with open(TEXT_RULES) as f:
        return [re.compile(p) for p in json.load(f)["plain"]["field_token_regexes"]]


def plain(text, rule_ids, regexes):
    """Business-owner wording: no rule ids, no field names."""
    # Longest id first, so an id that is only the start of a longer known id never replaces part of it.
    # A hyphenated word around the id ("R-2-based", "pre-R-2") is prose and the id goes; a hyphen
    # then a digit ("R-2-1") may be a longer id and is left alone.
    for rid in sorted(rule_ids, key=len, reverse=True):
        rx = re.compile(r"(?<![A-Za-z0-9_])(?<![0-9]-)%s(?![A-Za-z0-9_])(?!-[0-9])" % re.escape(rid))
        text = rx.sub(lambda m: "rule" if m.string[m.start() - 1:m.start()] == "-"
                      or m.string[m.end():m.end() + 1] == "-" else "a rule", text)
    for rx in regexes:
        text = rx.sub("a recorded value", text)
    return text


def _route_words(item):
    route = item.get("closure_route") or []
    if item.get("kind") == "escalation" or not route:
        return "people decide it; no capture or upload closes it"
    return " then ".join(route)


OTHER = "Other questions"


def _plain_check():
    """checks/plain_text.py (the page's id check), loaded by file path; None when it is absent."""
    path = os.path.join(AD, "checks", "plain_text.py")
    if not os.path.isfile(path):
        return None
    spec = importlib.util.spec_from_file_location("plain_text_for_register", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _scrub(text, rule_ids, regexes, pt):
    """plain() then any id-, field- or path-shaped text the page's check would reject, replaced by plain words."""
    text = plain(text, rule_ids, regexes)
    if pt is not None:
        for rx in pt.ID_REGEXES:
            text = re.sub(rx, "a recorded item", text)
        for rx in pt.FIELD_TOKEN_REGEXES:
            text = re.sub(rx, "a recorded value", text)
        for rx in pt.DECODE_REGEXES.values():
            text = re.sub(rx, " ", text, flags=re.M)
        text = re.sub(r"\b(\d+|%s)(\s+(?:[a-z]+\s+)?(?:%s)\b)" % (pt.NUMBER_WORDS, pt.COUNT_NOUNS),
                      lambda m: "some" + m.group(2), text, flags=re.I)
    return re.sub(r"[ \t]+", " ", text).strip()


def decision_groups(items):
    """[(decision, [items])] in first-seen order; no decision goes under "Other questions" (last).

    An item id met twice with a different decision keeps its first decision; the conflict is returned."""
    order, groups, first, conflicts = [], {}, {}, []
    for it in items:
        if not isinstance(it, dict):
            continue
        d = it.get("decision")
        if isinstance(d, list):
            vals = [x for x in d if isinstance(x, str) and x.strip()]
            if len(set(vals)) > 1:
                conflicts.append({"item": it.get("id"), "kept": vals[0], "dropped": vals[1:]})
            d = vals[0] if vals else None
        d = d.strip() if isinstance(d, str) and d.strip() else None
        iid = it.get("id")
        if iid in first and first[iid] != d:
            conflicts.append({"item": iid, "kept": first[iid], "dropped": [d]})
            d = first[iid]
        first.setdefault(iid, d)
        key = d or OTHER
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(it)
    if OTHER in order:
        order.remove(OTHER)
        order.append(OTHER)
    return [(k, groups[k]) for k in order], conflicts


TEXT_LIST_FIELDS = {"business-owner.md": ("closure_route",),
                    "builder.md": ("raw_ids", "dependent_rules", "closure_route")}


def view_input_problems(reg, reg_rel, name):
    """Item fields one view joins or reads into, checked before it renders: a list of text, or a group object."""
    probs = []
    for i, it in enumerate(reg["items"]):
        for field in TEXT_LIST_FIELDS[name]:
            v = it.get(field)
            if field in it and not (isinstance(v, list) and all(isinstance(x, str) for x in v)):
                probs.append({"defect": "register-shape", "where": reg_rel,
                              "detail": "items[%d].%s is %s, not a list of text" % (i, field, type(v).__name__)})
        if name == "builder.md" and it.get("group") is not None and not isinstance(it["group"], dict):
            probs.append({"defect": "register-shape", "where": reg_rel,
                          "detail": "items[%d].group is %s, not an object" % (i, type(it["group"]).__name__)})
    return probs


def render_owner_view(run_dir, reg):
    rule_ids = _rule_ids(run_dir)
    rx = _field_regexes()
    pt = _plain_check()
    items, access = reg.get("items", []), reg.get("access", [])
    projected = []
    for group in question_groups(items):
        it = dict(group["item"])
        if closed_by_snapshot(it):
            continue
        visible = [m for m in group["members"] if not internal_question(m, reg.get("views"))]
        if not visible:
            continue
        # Retain differing source wording rather than choosing a new interpretation.
        for field in TEXT_FIELDS:
            values = []
            for member in visible:
                value = member.get(field)
                value = value.strip() if isinstance(value, str) else ""
                if not value:
                    value = ("Who can answer and their authority: needs more information"
                             if field == "who_can_answer" else "needs more information")
                if value not in values:
                    values.append(value)
            it[field] = " / ".join(values)
        routes = [route for member in visible for route in (member.get("closure_route") or [])]
        it["closure_route"] = [route for route in ROUTE if route in routes]
        projected.append(it)
    items = projected
    groups, _ = decision_groups(items)
    bo = ["# What we still need to know", "",
          "Questions for people, grouped by the decision each one bears on. Each says what is unknown, "
          "who can answer it and how it closes.", ""]
    n = 0
    for decision, members in groups:
        bo.append("## %s" % _scrub(decision, rule_ids, rx, pt))
        bo.append("")
        roles = []
        for it in members:
            r = _scrub(str(it.get("who_can_answer", "")), rule_ids, rx, pt)
            if r and r not in roles:
                roles.append(r)
        bo.append("Who can answer: %s" % ("; ".join(roles) or "needs more information"))
        bo.append("")
        for it in members:
            n += 1
            bo.append("### Question %d" % n)
            bo.append("")
            for label, field in (("What we do not know", "unknown"), ("Why it matters", "why_it_matters"),
                                 ("What the agent does meanwhile", "agent_meanwhile"),
                                 ("Who can answer", "who_can_answer"),
                                 ("What breaks if we guess wrong", "breaks_if_wrong")):
                bo.append("- %s: %s" % (label, _scrub(str(it.get(field, "")), rule_ids, rx, pt)))
            bo.append("- How it closes: %s" % _route_words(it))
            bo.append("")
    if not items:
        bo += ["Nothing open.", ""]
    bo += ["## Access we are assuming", ""]
    for a in access:
        bo.append("- %s: %s" % (_scrub(str(a.get("system", "")), rule_ids, rx, pt),
                                _scrub(str(a.get("assumption", "")), rule_ids, rx, pt)))
    if not access:
        bo.append("- None recorded.")
    return "\n".join(bo) + "\n"


def render_builder_view(reg, verdicts):
    items, access = reg.get("items", []), reg.get("access", [])
    _, conflicts = decision_groups(items)
    bd = ["# Register: builder view", "", "%d items, %d access entries." % (len(items), len(access)), ""]
    for it in items:
        bd.append("## %s" % it.get("id"))
        bd.append("")
        bd.append("- class: %s" % it.get("class"))
        bd.append("- kind: %s" % it.get("kind"))
        bd.append("- source: %s (raw: %s)" % (it.get("source"), ", ".join(it.get("raw_ids", [])) or "none"))
        bd.append("- dependent rules: %s" % (", ".join(it.get("dependent_rules", [])) or "none"))
        bd.append("- closure route: %s" % (" -> ".join(it.get("closure_route", [])) or "none (escalation)"))
        bd.append("- group: %s (cheapest item %s)" % ((it.get("group") or {}).get("id"),
                                                       (it.get("group") or {}).get("cheapest_item")))
        bd.append("- decision: %s" % (it.get("decision") or OTHER))
        bd.append("- reader verdict: %s" % verdicts.get(it.get("id"), "none"))
        bd.append("- unknown: %s" % it.get("unknown"))
        bd.append("- routing: %s" % ("closed by snapshot" if closed_by_snapshot(it) else
                                   "internal" if internal_question(it, reg.get("views")) else "business"))
        for field in ("resolution_evidence", "snapshot_refs", "applied_from_snapshot"):
            if field in it:
                bd.append("- %s: %s" % (field, json.dumps(it[field], ensure_ascii=False)))
        bd.append("")
    bd += ["## Question inventory", ""]
    for group in question_groups(items):
        it = group["item"]
        bd.append("- %s: source questions %s; raw: %s; dependent rules: %s; status: %s" % (
            it.get("id"), ", ".join(str(m.get("id")) for m in group["members"]),
            ", ".join(it.get("raw_ids", [])) or "none",
            ", ".join(it.get("dependent_rules", [])) or "none",
            "closed by snapshot" if closed_by_snapshot(it) else
            "internal" if all(internal_question(m, reg.get("views")) for m in group["members"]) else "business"))
    for c in conflicts:
        bd.append("- decision conflict: %s keeps %r; dropped %r" % (c["item"], c["kept"], c["dropped"]))
    bd += ["## Access list", ""]
    for a in access:
        bd.append("- %s: %s: %s (%s)" % (a.get("id"), a.get("system"), a.get("assumption"), a.get("evidence_state")))
    if not access:
        bd.append("- None recorded.")
    return "\n".join(bd) + "\n"


def render_views(run_dir, reg, verdicts):
    return render_owner_view(run_dir, reg), render_builder_view(reg, verdicts)


VIEWS_DEFECTS = ("register-output", "state-shape", "register-unvalidated")


def _views_report(run_dir, problems):
    """Puts views problems into the register check report (checks/register.json), keeping its other findings.

    A rerun replaces the views' earlier entries; any recorded problem makes the report fail. Views never makes the
    report pass: only `register.py check` sets pass, after validating the register. When views clears its own
    findings from a report that was not passing, the report stays failing with a register-unvalidated finding."""
    path = os.path.join(run_dir, "checks", "register.json")
    old = None
    if os.path.isfile(path):
        try:
            with open(path, encoding="utf-8") as f:
                old = json.load(f)
        except (ValueError, OSError, UnicodeDecodeError):
            old = None
    if not (isinstance(old, dict) and isinstance(old.get("problems"), list)):
        if not problems:
            return
        old = _report("fail", [], {})
    kept = [p for p in old["problems"] if not (isinstance(p, dict) and p.get("defect") in VIEWS_DEFECTS)]
    if not problems and len(kept) == len(old["problems"]):
        return
    if not kept and not problems and old.get("result") != "pass":
        problems = [{"defect": "register-unvalidated", "where": os.path.join("checks", "register.json"),
                     "detail": "register.py views cleared its findings, but views does not validate the register; "
                               "run register.py check for a passing report"}]
        if len(old["problems"]) == 1 and old["problems"][0] == problems[0]:
            return
    if old.get("result") == "error":
        result = "error"
    else:   # pass stays pass only when check wrote it and views adds nothing; views never promotes to pass
        result = "fail" if kept or problems or old.get("result") != "pass" else "pass"
    old.update(problems=kept + problems, result=result, exit_code={"pass": 0, "fail": 1, "error": 2}[result])
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            json.dump(old, f, indent=2)
            f.write("\n")
    except OSError:
        pass


def _views_input_problems(reg, reg_rel):
    """What stops the views being rendered from a readable register.json: its shape, or a non-object entry."""
    probs = [p for p in register_shape_problems(reg, reg_rel)
             if not p["detail"].startswith(("register misses views", "register misses identity_runtime_opens",
                                            "views is", "identity_runtime_opens is"))]
    if probs:
        return probs
    for field in ("items", "access"):
        for i, e in enumerate(reg[field]):
            if not isinstance(e, dict):
                probs.append({"defect": "register-shape", "where": reg_rel,
                              "detail": "%s[%d] is %s, not an object" % (field, i, type(e).__name__)})
    return probs


def cmd_views(run_dir):
    state_problems = []
    try:
        stage_states(run_dir, required=True, problems=state_problems)
    except StateError as e:
        print("register views: %s" % e, file=sys.stderr)
        return 2
    reg_rel = os.path.join(REG_DIR, "register.json")
    v_rel = os.path.join(REG_DIR, "verdicts.json")
    problems = list(state_problems)
    reg, loaded = None, False
    try:
        reg = _read_json(run_dir, reg_rel)
        loaded = True
    except SourceError as e:
        problems.append({"defect": "register-output", "where": reg_rel, "detail": str(e)})
    if loaded:
        shape = _views_input_problems(reg, reg_rel)
        if shape:
            problems += [{"defect": "register-output", "where": p["where"], "detail": p["detail"]} for p in shape]
            loaded = False
    verdicts = {}
    verdicts_ok = True
    if os.path.isfile(os.path.join(run_dir, v_rel)):
        try:
            v = _read_json(run_dir, v_rel)
            errs = verdict_file_problems(v)
        except SourceError as e:
            errs = [str(e)]
        if errs:
            verdicts_ok = False
            problems.append({"defect": "register-output", "where": v_rel, "detail": "; ".join(errs)})
        else:
            verdicts = {x["subject"]: x["verdict"] for x in v["items"]}
    written = []
    for name in ("business-owner.md", "builder.md"):
        # Each view on its own: one that cannot be produced is recorded and its existing file left as it was.
        why = None
        if not loaded:
            why = "its inputs cannot be read"
        elif name == "builder.md" and not verdicts_ok:
            why = "verdicts.json cannot be read"
        else:
            bad = view_input_problems(reg, reg_rel, name)
            if bad:
                why = "; ".join(p["detail"] for p in bad)
            else:
                try:
                    text = render_owner_view(run_dir, reg) if name == "business-owner.md" \
                        else render_builder_view(reg, verdicts)
                    os.makedirs(os.path.join(run_dir, REG_DIR), exist_ok=True)
                    with open(os.path.join(run_dir, REG_DIR, name), "w") as f:
                        f.write(text)
                    written.append(name)
                except (TypeError, AttributeError, ValueError, KeyError, OSError, re.error) as e:
                    why = "it cannot be rendered (%s: %s)" % (type(e).__name__, e)
        if why:
            problems.append({"defect": "register-output", "where": os.path.join(REG_DIR, name),
                             "detail": "view not rewritten: %s; any existing file left as it was" % why})
    _output_note(run_dir, problems, kind="register-output")
    _output_note(run_dir, problems, kind="state-shape")
    _views_report(run_dir, problems)
    for p in problems:
        print("recorded   %s: %s" % (p["where"], p["detail"]))
    print("register views: %d questions -> %s/%s" % (len(reg["items"]) if loaded else 0, REG_DIR,
                                                    ", ".join(written) or "no view written"))
    return 1 if problems else 0


USAGE = "usage: register.py collect|check|views --run RUN [--out report.json]"


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if args[:1] in (["--help"], ["-h"]) or args[1:2] in (["--help"], ["-h"]) and args[0] in ("collect", "check", "views"):
        print(USAGE)
        return 0
    if not args or args[0] not in ("collect", "check", "views"):
        print(USAGE, file=sys.stderr)
        return 2
    cmd, rest = args[0], args[1:]
    opts = {}
    while rest:
        flag = rest.pop(0)
        if flag not in ("--run", "--out") or not rest or (flag == "--out" and cmd != "check"):
            print(USAGE, file=sys.stderr)
            return 2
        opts[flag] = rest.pop(0)
    run_dir = opts.get("--run")
    if not run_dir:
        print(USAGE, file=sys.stderr)
        return 2
    if not os.path.isdir(run_dir):
        if cmd == "check" and opts.get("--out"):
            report = _report("error", [{"defect": "run-unreadable", "where": ".", "detail": "run folder not found"}], {})
            with open(opts["--out"], "w") as f:
                json.dump(report, f, indent=2)
        print("register %s: run folder not found" % cmd, file=sys.stderr)
        return 2
    if cmd == "collect":
        return cmd_collect(run_dir)
    if cmd == "check":
        return cmd_check(run_dir, opts.get("--out"))
    return cmd_views(run_dir)


if __name__ == "__main__":
    sys.exit(main())
