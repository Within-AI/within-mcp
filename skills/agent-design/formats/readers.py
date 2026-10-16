"""One loader per rules-stage and fields-stage output (stages/rules/rules.json, open-items.json, outcomes.json and
stages/fields/fields.json).

Each loader returns (data, findings): the data normalised to one shape, and a list of shape findings, each
{"defect": "shape", "where": ..., "detail": ...}. A shape problem is a finding, never an exception: an unusable
top level gives empty data plus a finding, and an unusable nested value is dropped from the data with a finding.
Only a file that does not parse as JSON, or is absent where the caller says it is required, raises Unreadable.
Nothing is ever filled in: an absent optional key stays absent.

Standard library only, Python 3.9-safe.
"""
import json
import os

RULES = "stages/rules/rules.json"
OPEN_ITEMS = "stages/rules/open-items.json"
OUTCOMES = "stages/rules/outcomes.json"
FIELDS = "stages/fields/fields.json"

AMOUNT_DIRECTIONS = ("credit", "debit", "needs_more_information")
AMOUNT_WORDS = ("amount", "currency", "money")
FIELD_KEYS = ("id", "boundary_id", "token", "type", "direction", "run_time_source", "rule_id", "stand_in",
              "evidence_state", "signal")


class Unreadable(Exception):
    pass


def _finding(where, detail):
    return {"defect": "shape", "where": where, "detail": detail}


def read_json(path, rel=None, required=True):
    """The parsed file; None when it is absent and not required. An input that will not parse raises `Unreadable`."""
    rel = rel or path
    if not os.path.isfile(path):
        if required:
            raise Unreadable("missing %s" % rel)
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError) as exc:
        raise Unreadable("cannot read %s: %s" % (rel, exc))


def _run_file(run_dir, rel, required):
    return read_json(os.path.join(run_dir, rel), rel, required)


def _list_of(data, key, where, findings):
    """A list file may be a bare list or an object holding the list under `key`; anything else is a finding."""
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and isinstance(data.get(key), list):
        return data[key]
    findings.append(_finding(where, "expected a list or an object with %s[]" % key))
    return []


def _dicts(items, where, findings):
    out = []
    for i, item in enumerate(items):
        if isinstance(item, dict):
            out.append(item)
        else:
            findings.append(_finding("%s[%d]" % (where, i), "not an object; dropped"))
    return out


def _strings(value, where, findings):
    """A string or a list of strings, as a list of strings; other entries are dropped with a finding."""
    if isinstance(value, str):
        return [value]
    if not isinstance(value, list):
        findings.append(_finding(where, "expected a string or a list of strings; dropped"))
        return []
    out = []
    for i, v in enumerate(value):
        if isinstance(v, str):
            out.append(v)
        else:
            findings.append(_finding("%s[%d]" % (where, i), "not a string; dropped"))
    return out


def _is_id(value):
    """An id or reference a checker may use as a key: a non-empty string or an integer (not a boolean)."""
    return (isinstance(value, str) and value != "") or (isinstance(value, int) and not isinstance(value, bool))


def _drop_bad_ids(entries, keys, where, findings, label):
    """Entries whose named keys, where present, are usable ids; any other entry is dropped with a finding."""
    out = []
    for i, entry in entries:
        bad = [k for k in keys if k in entry and entry[k] is not None and not _is_id(entry[k])]
        if bad:
            findings.append(_finding("%s[%d]" % (where, i), "%s %s is not a string or integer id; %s dropped"
                                     % (label, " and ".join(bad), label)))
            continue
        out.append((i, entry))
    return out


def _string_list_key(entry, key, where, findings):
    """entry[key], where present, as a list of strings (a string becomes a one-item list); bad items dropped."""
    if key in entry and entry[key] is not None:
        entry[key] = _strings(entry[key], "%s.%s" % (where, key), findings)


def _expression_problem(e):
    """Why a rule's expression is unusable to every checker, or None. Deeper problems are expr.validate's."""
    if not isinstance(e, dict):
        return None  # has_expression is false: the checkers report no-expression
    if "test" in e and "kind" not in e:
        if not isinstance(e["test"], dict):
            return "test is not an object"
        if "sets" in e and e["sets"] is not None and not isinstance(e["sets"], dict):
            return "sets is not an object"
        if "reads" in e and e["reads"] is not None and not isinstance(e["reads"], list):
            return "reads is not a list"
        if isinstance(e.get("reads"), list) and not all(isinstance(r, str) for r in e["reads"]):
            return "reads holds a value that is not a string"
    return _node_problem(e if "kind" in e else e.get("test"), 0) or next(
        (p for p in (_node_problem(n, 0) for n in (e.get("sets") or {}).values()) if p), None)


def _node_problem(node, depth):
    """A nested value no expression walker can hold: a token that is not a string, an args or list that is not
    a list, an object or list where a scalar belongs, or nesting past 200 levels. A node that is not an object
    is left to expr.validate, which reports it."""
    if depth > 200:
        return "the expression nests deeper than 200 levels"
    if not isinstance(node, dict):
        return None  # expr.validate reports it
    if "token" in node and not isinstance(node["token"], (str, type(None))):
        return "a token is not a string"
    if "args" in node and not isinstance(node["args"], list):
        return "args is not a list"
    if "list" in node and not isinstance(node["list"], list):
        return "list is not a list"
    if "list" in node and any(isinstance(v, (dict, list)) for v in node["list"]):
        return "a membership list holds an object or a list"
    if "value" in node and isinstance(node["value"], (dict, list)):
        return "a literal value is an object or a list"
    for key in ("left", "right", "arg", "value_of"):
        if key in node:
            p = _node_problem(node[key], depth + 1)
            if p:
                return p
    for a in node.get("args") or []:
        p = _node_problem(a, depth + 1)
        if p:
            return p
    return None


# ---------------------------------------------------------------- rules output

def is_amount_token(token):
    """A token whose declared kind, type or unit is an amount (currency)."""
    for key in ("kind", "type", "unit"):
        if str(token.get(key) or "").strip().lower() in AMOUNT_WORDS:
            return True
    return False


def normalise_token(entry, where, findings):
    """A token name string, or an object with a non-empty `token`, as one object shape (every key kept)."""
    if isinstance(entry, str) and entry:
        return {"token": entry}
    if not isinstance(entry, dict) or not isinstance(entry.get("token"), str) or not entry["token"]:
        findings.append(_finding(where, "a token is a name string or an object with a non-empty token; dropped"))
        return None
    token = dict(entry)
    if "amount_direction" in token:
        value = token["amount_direction"]
        if value not in AMOUNT_DIRECTIONS:
            findings.append(_finding(where + ".amount_direction", "%s is not one of %s; dropped"
                                     % (json.dumps(value), ", ".join(AMOUNT_DIRECTIONS))))
            del token["amount_direction"]
        elif not is_amount_token(token):
            findings.append(_finding(where + ".amount_direction",
                                     "amount_direction on token %s, which declares no amount kind, type or unit; "
                                     "dropped" % token["token"]))
            del token["amount_direction"]
    return token


def parse_rules(data, where=RULES):
    """Normalise a parsed rules output to {"tokens": [obj], "config": [obj], "rules": [obj]} plus findings.

    Every other top-level key is kept as given."""
    findings = []
    if not isinstance(data, dict):
        findings.append(_finding(where, "the rules output is not an object"))
        return {"tokens": [], "config": [], "rules": []}, findings
    out = dict(data)
    tokens, seen = [], set()
    raw = data.get("tokens", [])
    if not isinstance(raw, list):
        findings.append(_finding(where + " tokens", "tokens is not a list"))
        raw = []
    for i, entry in enumerate(raw):
        token = normalise_token(entry, "%s tokens[%d]" % (where, i), findings)
        if token is None:
            continue
        if token["token"] in seen:
            findings.append(_finding("%s tokens[%d]" % (where, i),
                                     "duplicate token %s; the first occurrence is kept" % token["token"]))
            continue
        seen.add(token["token"])
        tokens.append(token)
    out["tokens"] = tokens
    config = data.get("config", [])
    if not isinstance(config, list):
        findings.append(_finding(where + " config", "config is not a list"))
        config = []
    out["config"] = []
    config = [(i, c) for i, c in enumerate(config) if isinstance(c, dict) or findings.append(
        _finding("%s config[%d]" % (where, i), "not an object; dropped"))]
    for i, entry in _drop_bad_ids(config, ("id", "name"), where + " config", findings, "config value"):
        entry = dict(entry)
        if isinstance(entry.get("value"), (dict, list)):
            findings.append(_finding("%s config[%d].value" % (where, i), "a configuration value is an object or a "
                                     "list; the value is dropped"))
            del entry["value"]
        _string_list_key(entry, "observation_ids", "%s config[%d]" % (where, i), findings)
        out["config"].append(entry)
    rules = data.get("rules")
    if not isinstance(rules, list):
        findings.append(_finding(where + " rules", "the rules output needs a rules list"))
        rules = []
    out["rules"] = []
    rules = [(i, r) for i, r in enumerate(rules) if isinstance(r, dict) or findings.append(
        _finding("%s rules[%d]" % (where, i), "not an object; dropped"))]
    for i, rule in _drop_bad_ids(rules, ("id", "decision"), where + " rules", findings, "rule"):
        rule = dict(rule)
        at = "%s rules[%d]" % (where, i)
        for key in ("observation_ids", "config_values", "ratified_sources"):
            _string_list_key(rule, key, at, findings)
        if "expression" in rule and rule["expression"] is not None:
            problem = _expression_problem(rule["expression"])
            if problem:
                findings.append(_finding(at + ".expression", "%s; the expression is dropped" % problem))
                del rule["expression"]
        out["rules"].append(rule)
    return out, findings


def token_names(rules_doc):
    return [t["token"] for t in rules_doc.get("tokens") or []]


def load_rules(run_dir, required=True):
    data = _run_file(run_dir, RULES, required)
    if data is None:
        return {"tokens": [], "config": [], "rules": []}, [_finding(RULES, "absent")]
    return parse_rules(data)


# ---------------------------------------------------------------- open items

def parse_open_items(data, where=OPEN_ITEMS):
    """Normalise to a list of items, each item's `missing` a list of strings (a string becomes a one-item list)."""
    findings = []
    items = _dicts(_list_of(data, "items", where, findings), where + " items", findings)
    out = []
    for i, item in _drop_bad_ids(list(enumerate(items)), ("id", "decision"), where + " items", findings,
                                 "open item"):
        item = dict(item)
        if "missing" in item:
            item["missing"] = _strings(item["missing"], "%s items[%d].missing" % (where, i), findings)
        out.append(item)
    return out, findings


def load_open_items(run_dir, required=True):
    data = _run_file(run_dir, OPEN_ITEMS, required)
    if data is None:
        return [], [_finding(OPEN_ITEMS, "absent")]
    return parse_open_items(data)


# ---------------------------------------------------------------- outcomes

def parse_outcomes(data, where=OUTCOMES):
    """Normalise to an object whose decisions and gates are lists of objects with string-list labels.

    primitive_form and every other key are kept as given (check_outcomes judges primitive_form itself)."""
    findings = []
    if not isinstance(data, dict):
        findings.append(_finding(where, "the outcomes output is not an object"))
        return {"decisions": [], "gates": []}, findings
    out = dict(data)
    for group in ("decisions", "gates"):
        raw = data.get(group, [])
        if not isinstance(raw, list):
            findings.append(_finding("%s %s" % (where, group), "%s is not a list" % group))
            raw = []
        entries = []
        good = [(i, e) for i, e in enumerate(raw) if isinstance(e, dict) or findings.append(
            _finding("%s %s[%d]" % (where, group, i), "not an object; dropped"))]
        for i, entry in _drop_bad_ids(good, ("id",), "%s %s" % (where, group), findings, group[:-1]):
            entry = dict(entry)
            entry["labels"] = _strings(entry.get("labels", []), "%s %s[%d].labels" % (where, group, i), findings)
            entries.append(entry)
        out[group] = entries
    return out, findings


def load_outcomes(run_dir, required=True):
    data = _run_file(run_dir, OUTCOMES, required)
    if data is None:
        return {"decisions": [], "gates": []}, [_finding(OUTCOMES, "absent")]
    return parse_outcomes(data)


def outcome_labels(outcomes):
    """Every typed outcome label the outcomes output lists, for decisions and gates."""
    labels = set()
    for group in ("decisions", "gates"):
        for entry in outcomes.get(group) or []:
            labels.update(entry.get("labels") or [])
    return labels


# ---------------------------------------------------------------- fields

FIELD_STRING_KEYS = ("id", "boundary_id", "token", "type", "direction", "run_time_source", "rule_id",
                     "evidence_state", "signal")


def parse_fields(data, where=FIELDS):
    """Normalise to a list of field rows; a key field.schema.json does not define is kept and ignored.

    fields.json is {"fields": [...]} or a bare list; one row object (no `fields` key, as the stage template
    shows it) is read as a one-row list. A row whose id or token is not a string is dropped; any other
    string-typed key holding a non-string is dropped from its row. Each with a finding."""
    findings = []
    if isinstance(data, dict) and "fields" not in data:
        data = [data]
    rows = _dicts(_list_of(data, "fields", where, findings), where + " fields", findings)
    out = []
    for i, row in enumerate(rows):
        at = "%s fields[%d]" % (where, i)
        if not all(isinstance(row.get(k), str) for k in ("id", "token") if k in row):
            findings.append(_finding(at, "id or token is not a string; row dropped"))
            continue
        row = dict(row)
        # Extra descriptive keys (a name, notes, a source) are kept and ignored: no check reads them, so they
        # are never a shape finding.
        for key in FIELD_STRING_KEYS:
            if key in row and not isinstance(row[key], str):
                findings.append(_finding("%s.%s" % (at, key), "not a string; dropped"))
                del row[key]
        out.append(row)
    return out, findings


def load_fields(run_dir, required=False):
    data = _run_file(run_dir, FIELDS, required)
    if data is None:
        return [], []
    return parse_fields(data)
