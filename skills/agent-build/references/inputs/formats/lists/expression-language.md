# Expression language (`agent-design.expression/1`)

A rule's `expression` is a JSON block checked by `expression.schema.json` and evaluated by `skills/agent-design/scripts/expr.py`. Nothing is ever run as code.

## Block

```json
{"reads": ["invoice.amount"], "test": {...node...}, "sets": {"outcome": {...node...}}}
```

- `reads` is the block's read set: every input the expression consumes, wherever it is consumed (`test`, `sets`, `calc` arguments, `is_null` and `is_not_applicable` included). The names a block produces (the keys of `sets`) are outputs, not reads, and never belong in `reads`. This is the only definition of a read set; `validate` enforces it, reporting a token read but not declared, a token declared but not read, and a produced output name listed in `reads`.
- `test` must evaluate to a boolean. When it is true each entry of `sets` is evaluated; when it is false the result is `{"test": false, "sets": {}}`.
- Each set is evaluated against the original row: one set never reads another set's output.

## Rows and tokens

A row is a flat object. A read token is an exact key of the row: a dot is part of the name, never a path (`invoice.amount` is the key `"invoice.amount"`). Tokens beginning `judgment.` name a recorded judgment answer (`expr.judgment_reads`); tokens beginning `stand_in.` name a declared stand-in (`expr.stand_in_reads`).

## Missing values

A token absent from the row, a null value and the not-applicable marker `{"not_applicable": true}` each raise `expr.MissingInput` naming the token, wherever evaluation reaches the read. They never become 0 or false. Only `is_null` and `is_not_applicable` look at such values without raising.

## Node kinds

| kind | fields | example | value |
|---|---|---|---|
| `literal` | `value` | `{"kind": "literal", "value": 500}` | 500 |
| `read` | `token` | `{"kind": "read", "token": "invoice.amount"}` | the row's value |
| `compare` | `op` (`==`, `!=`, `<`, `<=`, `>`, `>=`), `left`, `right` | `{"kind": "compare", "op": ">", "left": {"kind": "read", "token": "invoice.amount"}, "right": {"kind": "literal", "value": 500}}` | true for an amount of 600 |
| `and` | `args` (two or more) | `{"kind": "and", "args": [A, B]}` | true when every operand is true |
| `or` | `args` (two or more) | `{"kind": "or", "args": [A, B]}` | true when some operand is true |
| `not` | `arg` | `{"kind": "not", "arg": A}` | the negation |
| `in` | `value_of`, `list` | `{"kind": "in", "value_of": {"kind": "read", "token": "currency"}, "list": ["EUR", "USD"]}` | true for `"USD"` |
| `is_null` | `token` | `{"kind": "is_null", "token": "po.number"}` | true when absent or null, false otherwise (the marker included) |
| `is_not_applicable` | `token` | `{"kind": "is_not_applicable", "token": "po.number"}` | true only for `{"not_applicable": true}` |
| `calc` | `step`, `fn`, `args` | `{"kind": "calc", "step": "gap", "fn": "subtract", "args": [{"kind": "read", "token": "invoice.amount"}, {"kind": "read", "token": "po.amount"}]}` | invoice minus order |

## Types

- Every evaluated operand of `test`, `and`, `or` and `not` must be a boolean; anything else raises `expr.TypeMismatch`. There is no truthiness.
- Comparing a string with a number raises `expr.TypeMismatch` in every comparison and in membership; nothing is converted. A boolean never counts as a number. Ordering (`<`, `<=`, `>`, `>=`) works on two numbers or two strings.

## Short-circuit

`and` and `or` evaluate left to right and stop early: `and` at the first false operand, `or` at the first true one. Later operands are never evaluated, so a guard protects what follows it:

- `is_null(x) or x > 5` with `x` null returns true without raising.
- `x > 5 or is_null(x)` with `x` null raises `expr.MissingInput` for `x`.

## Arithmetic

Arithmetic appears only inside a `calc` node, which carries a non-empty `step` name (used in error messages) and one `fn`:

| fn | arguments | result |
|---|---|---|
| `add` | exactly 2 | a + b |
| `subtract` | exactly 2 | a − b |
| `multiply` | exactly 2 | a × b |
| `divide` | exactly 2 | a ÷ b; a zero divisor raises `expr.CalcError` naming the step |
| `sum` | 1 or more | the total |
| `abs` | exactly 1 | the absolute value |
| `round` | 1 or 2 (value, places) | rounded half away from zero (half-up on the decimal digits: 2.5 → 3, −2.5 → −3, 2.345 at 2 places → 2.35); with no places, or 0 places, the result is an integer |

Every argument must be a number (not a boolean). A wrong argument count, an unknown `fn` or an unknown node kind raises `expr.Invalid`.

## Helper functions

- `reads(expr)`: sorted, de-duplicated tokens read in `test` and `sets` (including `is_null` and `is_not_applicable` tokens).
- `literals(expr)`: every literal anywhere inside a comparison or membership (its operand subtrees, `calc` arguments included) and every membership list item, in order of first appearance, de-duplicated (for a value grid). Example: `compare(calc add(read amount, 5)) > 10` gives `[5, 10]`. A literal outside any comparison or membership (a `sets` value, a bare `calc`) is not collected. These are the grid-sampling literals (`grid_literals` is the same list).
- `governed_literals(expr, outcome_labels)`: the evidence-governed literals. Every grid-sampling literal, plus every number and string constant anywhere else in the expression (calculations, `sets`, outputs), except a string that is one of the typed outcome labels passed in. `check_rules` flags each one that is not a declared configuration value with observations as `invented-default`. Example: a `sets` calculation `add(read amount, 999)` gives `[999]`.
- `validate(expr)`: problems, empty when valid. It checks every field each node kind requires, with its type, as `expression.schema.json` defines it (a literal with no `value` is a problem), the block's own fields (`reads` a list of non-empty strings, `sets` an object, no other field), the read tokens against `reads`, and every set even when a false test would skip it.

## Command line

`python3 skills/agent-design/scripts/expr.py validate <file.json>` validates the expressions in a JSON file: one expression block or node, a list of rules, or a rules document (`tokens`, `config`, `rules`, whose reads are also checked against its declared tokens). It prints each problem on its own line starting `INVALID`, then one summary line, and exits 0 when every expression is valid, 1 when any is invalid and 2 when the file will not parse. `--help` lists the commands.
