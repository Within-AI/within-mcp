"""Evaluator for the Agent Design expression language (formats/lists/expression-language.md).

Standard library only, Python 3.9-safe. Expressions are JSON trees; nothing is ever run as code.
A missing, null or not-applicable input never becomes zero or False: it raises MissingInput.
"""
import json
import numbers
import os
import sys

ARITH_FNS = {"add": 2, "subtract": 2, "multiply": 2, "divide": 2, "sum": None, "abs": 1, "round": (1, 2)}
COMPARE_OPS = ("==", "!=", "<", "<=", ">", ">=")
KINDS = ("literal", "read", "compare", "and", "or", "not", "in", "is_null", "is_not_applicable", "calc")


class ExprError(Exception):
    pass


class MissingInput(ExprError):
    def __init__(self, token, why):
        self.token = token
        super().__init__("missing input %s (%s)" % (token, why))


class TypeMismatch(ExprError):
    pass


class CalcError(ExprError):
    pass


class Invalid(ExprError):
    pass


def _is_na(value):
    return isinstance(value, dict) and value == {"not_applicable": True}


def _is_num(value):
    return isinstance(value, numbers.Number) and not isinstance(value, bool)


def _is_block(expr):
    return isinstance(expr, dict) and "test" in expr and "kind" not in expr


def _kind(node):
    if not isinstance(node, dict) or "kind" not in node:
        raise Invalid("not an expression node: %r" % (node,))
    kind = node["kind"]
    if kind not in KINDS:
        raise Invalid("unknown node kind: %r" % (kind,))
    return kind


def _read(token, row):
    if token not in row:
        raise MissingInput(token, "absent")
    value = row[token]
    if value is None:
        raise MissingInput(token, "null")
    if _is_na(value):
        raise MissingInput(token, "not applicable")
    return value


def _bool(value, where):
    if not isinstance(value, bool):
        raise TypeMismatch("%s needs a boolean, got %r" % (where, value))
    return value


def _comparable(a, b, op):
    if _is_num(a) and _is_num(b):
        return
    if isinstance(a, str) and isinstance(b, str):
        return
    if isinstance(a, bool) and isinstance(b, bool) and op in ("==", "!="):
        return
    raise TypeMismatch("cannot compare %r with %r using %s" % (a, b, op))


def _compare(op, a, b):
    _comparable(a, b, op)
    if op == "==":
        return a == b
    if op == "!=":
        return a != b
    if isinstance(a, bool) or isinstance(b, bool):
        raise TypeMismatch("cannot order booleans with %s" % op)
    if op == "<":
        return a < b
    if op == "<=":
        return a <= b
    if op == ">":
        return a > b
    return a >= b


def _calc(node, row):
    step = node.get("step")
    if not isinstance(step, str) or not step:
        raise Invalid("calc node needs a non-empty step name")
    fn = node.get("fn")
    if fn not in ARITH_FNS:
        raise Invalid("unknown calc fn %r in step %s" % (fn, step))
    args = node.get("args")
    if not isinstance(args, list):
        raise Invalid("calc step %s needs an args list" % step)
    want = ARITH_FNS[fn]
    n = len(args)
    if (want is None and n < 1) or (isinstance(want, int) and n != want) or (isinstance(want, tuple) and n not in want):
        raise Invalid("calc step %s: %s takes %s arguments, got %d" % (step, fn, want or "one or more", n))
    vals = [_eval(a, row) for a in args]
    for v in vals:
        if not _is_num(v):
            raise TypeMismatch("calc step %s: %r is not a number" % (step, v))
    if fn == "add":
        return vals[0] + vals[1]
    if fn == "subtract":
        return vals[0] - vals[1]
    if fn == "multiply":
        return vals[0] * vals[1]
    if fn == "divide":
        if vals[1] == 0:
            raise CalcError("calc step %s: division by zero" % step)
        return vals[0] / vals[1]
    if fn == "sum":
        return sum(vals)
    if fn == "abs":
        return abs(vals[0])
    places = 0
    if n == 2:
        if not isinstance(vals[1], int) or isinstance(vals[1], bool):
            raise TypeMismatch("calc step %s: round places must be an integer" % step)
        places = vals[1]
    return _round_half_up(vals[0], places)


def _round_half_up(value, places):
    from decimal import Decimal, ROUND_HALF_UP
    q = Decimal(1).scaleb(-places)
    result = Decimal(str(value)).quantize(q, rounding=ROUND_HALF_UP)
    return int(result) if places <= 0 else float(result)


def _eval(node, row):
    kind = _kind(node)
    if kind == "literal":
        return node.get("value")
    if kind == "read":
        return _read(node.get("token"), row)
    if kind == "is_null":
        token = node.get("token")
        return token not in row or row[token] is None
    if kind == "is_not_applicable":
        return _is_na(row.get(node.get("token")))
    if kind == "compare":
        op = node.get("op")
        if op not in COMPARE_OPS:
            raise Invalid("unknown comparison operator %r" % (op,))
        return _compare(op, _eval(node.get("left"), row), _eval(node.get("right"), row))
    if kind == "not":
        return not _bool(_eval(node.get("arg"), row), "not")
    if kind in ("and", "or"):
        args = node.get("args")
        if not isinstance(args, list) or len(args) < 2:
            raise Invalid("%s needs two or more args" % kind)
        for a in args:
            v = _bool(_eval(a, row), kind)
            if kind == "and" and not v:
                return False
            if kind == "or" and v:
                return True
        return kind == "and"
    if kind == "in":
        value = _eval(node.get("value_of"), row)
        items = node.get("list")
        if not isinstance(items, list):
            raise Invalid("in needs a list")
        for item in items:
            if (_is_num(value) and isinstance(item, str)) or (isinstance(value, str) and _is_num(item)) \
                    or (isinstance(value, bool) != isinstance(item, bool)):
                raise TypeMismatch("membership of %r in a list holding %r" % (value, item))
        return value in items
    return _calc(node, row)


def evaluate(expr, row):
    """Evaluate a node (returns its value) or a block (returns {"test": bool, "sets": {...}})."""
    if _is_block(expr):
        test = _bool(_eval(expr["test"], row), "test")
        sets = {}
        if test:
            for name, node in (expr.get("sets") or {}).items():
                sets[name] = _eval(node, row)
        return {"test": test, "sets": sets}
    return _eval(expr, row)


def _walk(node, tokens, lits, in_cmp=False):
    """Collect read tokens, and literals anywhere inside a comparison or membership (calc args included)."""
    if not isinstance(node, dict):
        return
    if _is_block(node):
        _walk(node["test"], tokens, lits)
        for sub in (node.get("sets") or {}).values():
            _walk(sub, tokens, lits)
        return
    kind = node.get("kind")
    if kind in ("read", "is_null", "is_not_applicable"):
        tokens.append(node.get("token"))
    elif kind == "literal":
        if in_cmp and "value" in node:
            lits.append(node["value"])
    elif kind == "compare":
        _walk(node.get("left"), tokens, lits, True)
        _walk(node.get("right"), tokens, lits, True)
    elif kind == "in":
        _walk(node.get("value_of"), tokens, lits, True)
        items = node.get("list")
        lits.extend(items if isinstance(items, list) else [])
    elif kind == "not":
        _walk(node.get("arg"), tokens, lits, in_cmp)
    elif kind in ("and", "or", "calc"):
        args = node.get("args")
        for a in args if isinstance(args, list) else []:
            _walk(a, tokens, lits, in_cmp)


def _tokens(expr):
    tokens, lits = [], []
    _walk(expr, tokens, lits)
    return sorted(set(t for t in tokens if isinstance(t, str)))


def reads(expr):
    """Sorted, de-duplicated tokens read in the test and the sets."""
    return _tokens(expr)


def literals(expr):
    """Every literal inside a comparison or membership (operand subtrees and calc args included) and every
    membership list item, in order of first appearance, de-duplicated."""
    tokens, lits = [], []
    _walk(expr, tokens, lits)
    out = []
    for v in lits:
        if not any(type(v) is type(o) and v == o for o in out):
            out.append(v)
    return out


def _all_literals(node, out):
    """Every literal node value and membership list item, anywhere in the expression (test, sets, calc args)."""
    if isinstance(node, dict):
        if _is_block(node):
            _all_literals(node["test"], out)
            for sub in (node.get("sets") or {}).values() if isinstance(node.get("sets"), dict) else []:
                _all_literals(sub, out)
            return
        kind = node.get("kind")
        if kind == "literal" and "value" in node:
            out.append(node["value"])
        if kind == "in" and isinstance(node.get("list"), list):
            out.extend(node["list"])
        for key in ("left", "right", "arg", "value_of"):
            _all_literals(node.get(key), out)
        for a in node.get("args") if isinstance(node.get("args"), list) else []:
            _all_literals(a, out)


def grid_literals(expr):
    """The grid-sampling literals: those inside a comparison or membership (the same list as literals)."""
    return literals(expr)


def governed_literals(expr, outcome_labels=()):
    """The evidence-governed literals: every number and string constant anywhere in the expression (comparisons,
    membership lists, calculations, sets, outputs), except a string that is a typed outcome label. Each must be a declared configuration value with observations. De-duplicated, in
    order of first appearance."""
    found = list(literals(expr))
    _all_literals(expr, found)
    labels = set(l for l in outcome_labels if isinstance(l, str))
    out = []
    for v in found:
        if isinstance(v, bool) or v is None or not (_is_num(v) or isinstance(v, str)):
            continue
        if isinstance(v, str) and v in labels:
            continue
        if not any(type(v) is type(o) and v == o for o in out):
            out.append(v)
    return out


def judgment_reads(expr):
    return [t for t in _tokens(expr) if t.startswith("judgment.")]


def stand_in_reads(expr):
    return [t for t in _tokens(expr) if t.startswith("stand_in.")]


def _is_scalar(value, allow_null):
    if value is None:
        return allow_null
    return isinstance(value, (str, bool)) or _is_num(value)


def _check_token(node, where, problems):
    token = node.get("token")
    if not isinstance(token, str) or not token:
        problems.append("%s: %s needs a non-empty string token" % (where, node["kind"]))


def _check_args(node, where, problems, least):
    args = node.get("args")
    if not isinstance(args, list):
        problems.append("%s: %s needs an args list" % (where, node["kind"]))
        return None
    if len(args) < least:
        problems.append("%s: %s needs %d or more args, got %d" % (where, node["kind"], least, len(args)))
    for i, a in enumerate(args):
        _check_node(a, "%s.args[%d]" % (where, i), problems)
    return args


def _check_node(node, where, problems):
    """Every field each node kind requires, with its type, as expression.schema.json defines it."""
    if not isinstance(node, dict) or "kind" not in node:
        problems.append("%s: not an expression node" % where)
        return
    kind = node["kind"]
    if kind not in KINDS:
        problems.append("%s: unknown node kind %r" % (where, kind))
        return
    if kind == "literal":
        if "value" not in node:
            problems.append("%s: literal needs a value" % where)
        elif not _is_scalar(node["value"], allow_null=True):
            problems.append("%s: literal value must be a string, number, boolean or null" % where)
    elif kind in ("read", "is_null", "is_not_applicable"):
        _check_token(node, where, problems)
    elif kind == "compare":
        if node.get("op") not in COMPARE_OPS:
            problems.append("%s: unknown comparison operator %r" % (where, node.get("op")))
        _check_node(node.get("left"), where + ".left", problems)
        _check_node(node.get("right"), where + ".right", problems)
    elif kind == "not":
        _check_node(node.get("arg"), where + ".arg", problems)
    elif kind in ("and", "or"):
        _check_args(node, where, problems, 2)
    elif kind == "in":
        _check_node(node.get("value_of"), where + ".value_of", problems)
        items = node.get("list")
        if not isinstance(items, list):
            problems.append("%s: in needs a list" % where)
        else:
            for i, item in enumerate(items):
                if not _is_scalar(item, allow_null=False):
                    problems.append("%s.list[%d]: list items must be strings, numbers or booleans" % (where, i))
    else:
        step = node.get("step")
        if not isinstance(step, str) or not step:
            problems.append("%s: calc needs a non-empty step name" % where)
        fn = node.get("fn")
        if fn not in ARITH_FNS:
            problems.append("%s: unknown calc fn %r" % (where, fn))
        args = _check_args(node, where, problems, 1)
        if fn in ARITH_FNS and args is not None:
            want, n = ARITH_FNS[fn], len(args)
            if (want is None and n < 1) or (isinstance(want, int) and n != want) or (isinstance(want, tuple) and n not in want):
                problems.append("%s: %s takes %s arguments, got %d" % (where, fn, want or "one or more", n))


def validate(expr):
    """Problems with an expression; empty when valid. Checks every set, even ones a false test would skip."""
    problems = []
    if _is_block(expr):
        for key in sorted(set(expr) - {"reads", "test", "sets"}):
            problems.append("block: unknown field %s" % key)
        _check_node(expr["test"], "test", problems)
        sets = expr.get("sets")
        if not isinstance(sets, dict):
            problems.append("sets: must be an object")
            sets = {}
        for name, node in sets.items():
            _check_node(node, "sets.%s" % name, problems)
        declared = expr.get("reads")
        if not isinstance(declared, list):
            problems.append("reads: must be a list")
            declared = []
        for i, d in enumerate(declared):
            if not isinstance(d, str) or not d:
                problems.append("reads[%d]: must be a non-empty string" % i)
        declared = [d for d in declared if isinstance(d, str)]
        used = set(_tokens(expr))
        for t in sorted((set(declared) - used) & set(sets)):
            problems.append("reads lists %s, an output the block produces; reads holds consumed inputs only" % t)
        for t in sorted(used - set(declared)):
            problems.append("token read but not declared: %s" % t)
        for t in sorted(set(declared) - used - set(sets)):
            problems.append("token declared but not read: %s" % t)
    else:
        _check_node(expr, "expr", problems)
    return problems


def placed_rule_list(doc):
    """The placed rules in stages/placement/placed-rules.json, read by every script that lists them.

    The placement prompt writes a JSON list; {"placed_rules": [...]} and {"rules": [...]} are read the same way.
    Anything else gives None and the caller names its own error."""
    if isinstance(doc, list):
        return doc
    if isinstance(doc, dict):
        for key in ("placed_rules", "rules"):
            if isinstance(doc.get(key), list):
                return doc[key]
    return None


# ---------------------------------------------------------------- command line

def _rules_of(doc):
    """(where, expression) for a rules document, a list of rules, or a single expression."""
    if isinstance(doc, dict) and isinstance(doc.get("rules"), list) or isinstance(doc, list):
        rules = doc["rules"] if isinstance(doc, dict) else doc
        out = []
        for i, rule in enumerate(rules):
            if isinstance(rule, dict) and "expression" in rule:
                out.append(("rule %s" % rule.get("id", "[%d]" % i), rule["expression"]))
            else:
                out.append(("rule [%d]" % i, rule))
        return out
    return [("expression", doc)]


def validate_file(path):
    """(problems, count): every problem with the expressions in a JSON file, each 'where: problem'.

    A rules document's reads are also checked against its declared tokens (a token string or an object with
    `token`, read through formats/readers.py). Raises ValueError or OSError when the file will not parse."""
    with open(path, encoding="utf-8") as fh:
        doc = json.load(fh)
    declared = None
    if isinstance(doc, dict) and isinstance(doc.get("rules"), list) and "tokens" in doc:
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "formats"))
        import readers
        norm, findings = readers.parse_rules(doc, os.path.basename(path))
        declared = set(readers.token_names(norm))
        problems = ["%s: %s" % (f["where"], f["detail"]) for f in findings]
    else:
        problems = []
    pairs = _rules_of(doc)
    for where, e in pairs:
        if not isinstance(e, dict):
            problems.append("%s: not an expression" % where)
            continue
        problems.extend("%s: %s" % (where, p) for p in validate(e))
        if declared is not None:
            problems.extend("%s: reads %s, not in the declared tokens" % (where, t)
                            for t in reads(e) if t not in declared)
    return problems, len(pairs)


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(
        prog="expr.py", description="Agent Design expression language tools.")
    sub = parser.add_subparsers(dest="command")
    v = sub.add_parser("validate", help="validate the expression(s) in a JSON file: one expression block or node, "
                                        "a rules document ({tokens, config, rules}) or a list of rules")
    v.add_argument("file")
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 2
    if args.command != "validate":
        parser.print_help()
        return 2
    try:
        problems, count = validate_file(args.file)
    except (OSError, ValueError) as exc:
        print("expr validate: unreadable %s: %s" % (args.file, exc))
        return 2
    for p in problems:
        print("INVALID %s" % p)
    print("expr validate: %s: %d expression(s), %d problem(s), %s"
          % (args.file, count, len(problems), "invalid" if problems else "valid"))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
