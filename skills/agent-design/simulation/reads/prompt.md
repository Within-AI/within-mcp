# Simulation read: does each rule's expression say what its prose says?

You are the independent reader for the simulation stage of an Agent Design run. The top-level skill dispatches you once, after the walk and `prepare_read.py` have run. Do not launch another agent or any command-line model; do the read yourself.

Time target: finish within the stage's `simulation` time target (the run's status table shows it); this is a short read, one pass over the rules.

## What you read

Read only `<run>/simulation/reads/input/rules.json`. It holds, per rule, its `id`, its `prose` (the When/Then text) and its structured `expression` (the expression language in `skills/agent-design/formats/lists/expression-language.md`). Do not open any other run file: no case, no expected value, no walk result and no other stage's file. You judge the expression against the prose alone.

## What you decide

Give one finding per rule: the expression says what the prose says, or how they differ.

For each rule, compare the When part of the prose with the expression's test, and the Then part with what the expression sets. Look for a different threshold or comparison, a missing or extra condition, `and` written where the prose means `or` (or the reverse), a value set that the prose does not mention, and a value the prose names that the expression never sets.

## What you write

Write `<run>/simulation/reads/encoding-verdict.json`, valid against `skills/agent-design/formats/lists/verdict.schema.json`:

- `kind`: `encoding-read`
- `read`: `["simulation/reads/input/rules.json"]`
- `items`: exactly one item per rule, with
  - `id`: `read-` followed by the rule id
  - `subject`: the rule id, exactly as in the input
  - `verdict`: `ok` when the expression says what the prose says; `finding` when they differ; `cannot_judge` when the prose is too vague to compare
  - `evidence`: for a finding, at least one entry quoting the prose words and the expression part that disagree
  - `detail`: one plain sentence for people: either that the expression matches the prose, or how they differ
  - `route_to`: `rules` for a finding, otherwise null

Write nothing else. `check_read.py` then checks that every rule has a verdict and lists each finding as a simulation residual by rule id.
