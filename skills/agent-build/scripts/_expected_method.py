"""Expected-value calculation method; no file I/O.

Source: skills/agent-design/scripts/compute_expected.py
Predecessor: a602a8058126c09231028904fae6925143028297
source sha-256: 9a70361241c8c47a6baaad72a2248a517481ca3f6bfd27940b43c9a053a93a55
Copied: ARITY and Unusable through compute_case; constants below from the same source.
Excluded: CLI, file/archive operations and file-specific exceptions.
Refresh this copy with its source; protected knowledge reference pins are independent.
"""
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

DIRECTIONS = ("credit", "debit", "needs_more_information")
NOT_APPLICABLE = "not_applicable"

# U2's pinned arithmetic form: calc, literal and read nodes; calc fns below.
ARITY = {"add": 2, "subtract": 2, "multiply": 2, "divide": 2, "sum": None, "abs": 1, "round": (1, 2)}


class Unusable(Exception):
    """A derivation that cannot give an amount; the message is the exclusion reason."""


def _decimal(value):
    """A decimal string or JSON number straight to Decimal (pin I8); None when it is neither."""
    if isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        d = value
    elif isinstance(value, int):
        d = Decimal(value)
    elif isinstance(value, float):
        d = Decimal(repr(value))
    elif isinstance(value, str):
        try:
            d = Decimal(value.strip())
        except InvalidOperation:
            return None
    else:
        return None
    return d if d.is_finite() else None


def _num(value, where):
    d = _decimal(value)
    if d is None:
        raise Unusable("%s is not a number (%r)" % (where, value))
    return d


def _cell(token, rows):
    row_id, _, column = token.partition(".")
    if not column or row_id not in rows:
        raise Unusable("derivation cites row %r, which the case does not have" % row_id)
    values = rows[row_id]
    if column not in values:
        raise Unusable("derivation cites %s, which the case's rows do not have" % token)
    cell = values[column]
    value = cell.get("value") if isinstance(cell, dict) and "label" in cell else cell
    if value is None:
        raise Unusable("input %s is null (missing input, never treated as zero)" % token)
    if isinstance(value, dict) and value == {"not_applicable": True}:
        raise Unusable("input %s is not applicable" % token)
    return _num(value, "input %s" % token)


def _named_input(name, value, rows):
    if isinstance(value, dict) and set(value) == {"read"} and isinstance(value["read"], str):
        return _cell(value["read"], rows)
    if value is None:
        raise Unusable("input %s is null (missing input, never treated as zero)" % name)
    return _num(value, "input %s" % name)


def _two(vals, name):
    if len(vals) != 2:
        raise Unusable("named derivation %s takes 2 inputs, got %d" % (name, len(vals)))
    return vals


def _round(vals):
    if len(vals) not in (1, 2):
        raise Unusable("named derivation round takes 1 or 2 inputs, got %d" % len(vals))
    places = 0
    if len(vals) == 2:
        if vals[1] != vals[1].to_integral_value():
            raise Unusable("round places must be an integer")
        places = int(vals[1])
    return vals[0].quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)


def _one(vals, name):
    if len(vals) != 1:
        raise Unusable("named derivation %s takes 1 input, got %d" % (name, len(vals)))
    return vals[0]


def _ratio(vals):
    a, b = _two(vals, "ratio")
    if b == 0:
        raise Unusable("named derivation ratio divides by zero")
    return a / b


# Named derivations: inputs are taken in the order the case writes them.
NAMED = {
    "copy": lambda v: _one(v, "copy"),
    "negate": lambda v: -_one(v, "negate"),
    "absolute": lambda v: abs(_one(v, "absolute")),
    "sum": lambda v: sum(v, Decimal(0)),
    "difference": lambda v: _two(v, "difference")[0] - _two(v, "difference")[1],
    "absolute_difference": lambda v: abs(_two(v, "absolute_difference")[0] - _two(v, "absolute_difference")[1]),
    "product": lambda v: _two(v, "product")[0] * _two(v, "product")[1],
    "ratio": _ratio,
    "round": _round,
}


def is_named(node):
    return isinstance(node, dict) and "derive" in node


def _eval_named(node, rows):
    name = node.get("derive")
    if name not in NAMED:
        raise Unusable("unknown named derivation %r (known: %s)" % (name, ", ".join(sorted(NAMED))))
    inputs = node.get("inputs")
    if not isinstance(inputs, dict) or not inputs:
        raise Unusable("named derivation %s has no inputs" % name)
    if set(node) - {"derive", "inputs"}:
        raise Unusable("named derivation %s carries fields other than derive and inputs" % name)
    return NAMED[name]([_named_input(k, v, rows) for k, v in inputs.items()])


def _eval(node, rows):
    if not isinstance(node, dict):
        raise Unusable("derivation node is not an object")
    if is_named(node):
        return _eval_named(node, rows)
    kind = node.get("kind")
    if kind == "literal":
        if node.get("value") is None:
            raise Unusable("literal is null (missing value, never treated as zero)")
        return _num(node.get("value"), "literal")
    if kind == "read":
        token = node.get("token")
        if not isinstance(token, str):
            raise Unusable("read node has no token")
        return _cell(token, rows)
    if kind != "calc":
        raise Unusable("unsupported operation %r (outside the pinned arithmetic form)" % (kind,))
    fn = node.get("fn")
    if fn not in ARITY:
        raise Unusable("unsupported operation %r (outside the pinned arithmetic form)" % (fn,))
    args = node.get("args")
    if not isinstance(args, list):
        raise Unusable("calc %s has no args list" % fn)
    want, n = ARITY[fn], len(args)
    if (want is None and n < 1) or (isinstance(want, int) and n != want) or (isinstance(want, tuple) and n not in want):
        raise Unusable("calc %s takes %s arguments, got %d" % (fn, want or "one or more", n))
    vals = [_eval(a, rows) for a in args]
    if fn == "add":
        return vals[0] + vals[1]
    if fn == "subtract":
        return vals[0] - vals[1]
    if fn == "multiply":
        return vals[0] * vals[1]
    if fn == "divide":
        if vals[1] == 0:
            raise Unusable("calc divide by zero")
        return vals[0] / vals[1]
    if fn == "sum":
        return sum(vals, Decimal(0))
    if fn == "abs":
        return abs(vals[0])
    places = 0
    if n == 2:
        if vals[1] != vals[1].to_integral_value():
            raise Unusable("round places must be an integer")
        places = int(vals[1])
    return vals[0].quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)


def _text(d):
    """Plain decimal text, never exponent form."""
    try:
        d = d.normalize()
    except InvalidOperation:
        pass
    text = format(d, "f")
    return "0" if text in ("-0", "") else text


def is_na(value):
    """The not-applicable expected value: the marker object or the text "not_applicable"."""
    return value == NOT_APPLICABLE or (isinstance(value, dict) and value == {"not_applicable": True})


def typed(value):
    """An expected or actual non-number value in its graded form: text with surrounding whitespace removed
    (whitespace-only becomes the empty answer ""), the not-applicable marker as "not_applicable", booleans
    and null as they are."""
    if is_na(value):
        return NOT_APPLICABLE
    if isinstance(value, str):
        return value.strip()
    return value


def _is_amount(value):
    """A JSON number, never a boolean or an identifier that happens to contain digits."""
    return isinstance(value, (int, float, Decimal)) and not isinstance(value, bool) and _decimal(value) is not None


def outcome_parts(outcome):
    """(values, derivations, directions) of one outcome; a named derivation written straight into values counts
    as a derivation. A field declared both in values and in derivations stays in both (see conflicts)."""
    values = dict(outcome.get("values") or {}) if isinstance(outcome.get("values"), dict) else {}
    derivations = dict(outcome.get("derivations") or {}) if isinstance(outcome.get("derivations"), dict) else {}
    for name in list(values):
        if is_named(values[name]) and name not in derivations:
            derivations[name] = values.pop(name)
    directions = outcome.get("amount_direction") if isinstance(outcome.get("amount_direction"), dict) else {}
    return values, derivations, directions


def conflicts(outcome, rows):
    """{field: why} for each field one outcome declares both as a value and as a derivation and the two do not
    agree: a non-number value beside a derivation, or a number the derivation does not give exactly. A recorded
    finding, never resolved by picking one. A number the derivation gives exactly is the same declaration twice."""
    if not isinstance(outcome, dict):
        return {}
    values, derivations, _ = outcome_parts(outcome)
    out = {}
    for name in sorted(set(values) & set(derivations)):
        value = values[name]
        if _decimal(value) is None:
            out[name] = "the value %r and a derivation are both declared" % (value,)
            continue
        try:
            derived = _eval(derivations[name], rows)
        except Unusable:
            continue  # the derivation's own failure is reported where it is computed
        if derived != _decimal(value):
            out[name] = "the value %s and the derivation's %s disagree" % (value, _text(derived))
    return out


def _rows(case):
    rows = {}
    for row in case.get("rows") or [] if isinstance(case, dict) else []:
        if isinstance(row, dict):
            rows[row.get("id")] = row.get("values") if isinstance(row.get("values"), dict) else {}
    return rows


def _amounts(outcome, rows, which):
    """{"amounts", "values", "directions"} of one outcome: every typed expected value kept."""
    if not isinstance(outcome, dict):
        raise Unusable("%s outcome is missing" % which)
    if outcome.get("derivations") is not None and not isinstance(outcome.get("derivations"), dict):
        raise Unusable("%s derivations is not an object" % which)
    values, derivations, directions = outcome_parts(outcome)
    both = conflicts(outcome, rows)
    if both:
        raise Unusable("%s declares %s both as a value and as a derivation (%s); neither is picked"
                       % (which, ", ".join(repr(n) for n in sorted(both)), "; ".join(both[n] for n in sorted(both))))
    amounts, kept = {}, {}
    for name in sorted(derivations):
        try:
            amounts[name] = _eval(derivations[name], rows)
        except Unusable as exc:
            raise Unusable("%s amount %r: %s" % (which, name, exc))
    for name in sorted(values):
        if name in derivations:
            continue  # the same number declared twice; conflicts() has checked they agree
        value = values[name]
        if _is_amount(value):
            amounts[name] = _decimal(value)
        elif name in directions and value is not None and not is_na(value):
            # An authored amount direction declares numeric semantics for legacy text.
            amounts[name] = _num(value, "%s amount %r" % (which, name))
        elif value is None or isinstance(value, (bool, str)) or is_na(value):
            kept[name] = typed(value)
        else:
            raise Unusable("%s value %r is not a number, boolean, text, null or not applicable" % (which, name))
    dirs = {}
    for name in sorted(directions):
        if directions[name] not in DIRECTIONS:
            raise Unusable("%s amount_direction of %r is %r, not one of %s"
                           % (which, name, directions[name], ", ".join(DIRECTIONS)))
        dirs[name] = directions[name]
    out = {"amounts": amounts}
    if derivations:
        out["derivations"] = derivations
    if kept:
        out["values"] = kept
    if dirs:
        out["directions"] = dirs
    return out


def compute_case(case):
    """One frozen entry: {"id", ..., "label", "amounts", "values", "directions", "alternatives"}, or
    {"id", ..., "excluded": reason}. A person-decided case keeps its `person_decision`."""
    head = {"id": case.get("id"), "decision": case.get("decision"), "critical": bool(case.get("critical", False))}
    if "exclusion" in case:
        head["excluded"] = "stamped open in the suite: %s" % (case["exclusion"] or {}).get("missing", "")
        head["stamp"] = "needs_more_information"
        return head
    rows = {}
    for row in case.get("rows") or []:
        rows[row.get("id")] = row.get("values") or {}
    try:
        expected = case.get("expected")
        entry = dict(head, label=(expected or {}).get("label"), alternatives=[])
        entry.update(_amounts(expected, rows, "expected"))
        if isinstance(case.get("person_decision"), dict):
            entry["person_decision"] = case["person_decision"]
        for i, alt in enumerate(case.get("allowed_alternatives") or []):
            frozen_alt = {"label": (alt or {}).get("label") if isinstance(alt, dict) else None}
            frozen_alt.update(_amounts(alt, rows, "alternative %d" % (i + 1)))
            entry["alternatives"].append(frozen_alt)
        return entry
    except Unusable as exc:
        head["excluded"] = str(exc)
        head["stamp"] = "needs_more_information"
        return head
