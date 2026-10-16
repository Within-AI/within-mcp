# Rules stage

Read only inside this run's folder and the plugin; never list or open folders beside the run.

You write the decision rules for this run, as one stage in the session that runs the skill. Time target: 10 minutes.

## Read

- `<run>/stages/intake/intake.json`. It carries paths, not graphs; every path is relative to `<run>` and begins `upstream/`. For each entry in `processes[]`, open the files its `future_state` and `transformation_map` paths name: every decision step, its observations and the documents it cites. Open the snapshot intake that `observations.snapshot_intake` names: its `sources[]` list the documents, and an entry with `prior_art: true` is prior art.
- An item the future state lists in its top-level `opens_closed` is closed, not open: never raise it again. An old `## Target platform question` section in the map and its open item, if present, are ignored: the platform is asked only at the Agent Build hand-off.
- `<run>/stages/architecture/agent.yaml`: the agent's shape (its parts and the steps each owns).
- `skills/agent-design/formats/lists/expression-language.md`: the only language a rule's expression may use.
- The templates beside this prompt: `template.json`, `outcomes.template.json`, `open-items.template.json`.

## Write

Write every rule first, before placement. A rule names its decision and never a step: there is no step field in a rule. Placement is a later stage.

Write three files, each as JSON (never YAML):

1. `<run>/stages/rules/rules.json`, shaped like `template.json`: top-level `tokens` (each operative token, its meaning and its `observation_ids`), `config` (each configuration value, its `id`, `value` and `observation_ids`) and `rules`. Each rule carries `id`, `decision` (the qualified reference `<process_id>:<node_id>`), `prose` (one plain sentence a person can check), `expression` (`reads`, `test` and `sets` in the expression language), `scope` (`run`, or `item` with an `item_key`), `observation_ids` (its sources), `config_values` (the ids of the `config` entries it uses), `ratified_sources` (the snapshot-intake source ids a person ratified that support the rule) and `evidence_state` (one of `Observed`, `Assumed`, `Contradictory`, `needs_more_information` from `formats/lists/evidence-states.json`).
2. `<run>/stages/rules/outcomes.json`, shaped like `outcomes.template.json`: one list of decisions and their typed outcome labels per agent, and one per gate. Each gate is `{"id": <boundary id>, "step": <owned step>, "labels": [...]}`: `id` is the id of the boundary the gate guards in `stages/boundaries/boundaries.json`, and `step` is the qualified step `<process_id>:<node_id>` the gate sits at, a step a part of `agent.yaml` owns. Also write `primitive_form`: `value` is the agent's primitive form in plain words, taken from the agent shape in `agent.yaml` (for example "one agent with two sub-agents"), and `decision_ref` is the `id` of the decision in this file that the form rests on (the decision whose handling makes that shape necessary).
3. `<run>/stages/rules/open-items.json`, shaped like `open-items.template.json`: `{"items": [...]}`, each item with exactly `id`, `decision`, `missing`, `evidence_state: needs_more_information` and `kind`.

## Rules you must follow

- No invented default. Every literal in an expression (each comparison or membership operand and each `calc` constant) is declared in `rules.json` `config` with its observation ids and listed in the rule's `config_values`. Outcome labels in `sets`, ids and scope values are not literals. An unsupported literal value is an open item; this does not exclude a stated condition or a justified proposed control without observations.
- Never treat a document the snapshot marks prior art as a ratified source. Prior art may explain a step; it never supports a rule or a config value.
- List in a rule's `ratified_sources` only sources a person ratified; never list a prior-art source there.
- Set each rule's `evidence_state` from the pinned evidence-state values; when the evidence for the rule conflicts, set `evidence_state: Contradictory`.
- No arithmetic in this prompt and none done by you. Every amount, gap, total or ratio goes through the named calculation step of the expression language (a `calc` node with its `step` name); never write a computed number into a rule.
- No fixed end-state or action vocabulary. Labels are typed per decision and published once in `outcomes.json`; every label a rule sets appears there under its decision. Do not reuse the retired end-state words in `formats/lists/retired-end-states.json`.
- No cap on the number of rules. Write as many rules as the decisions need.
- Write each policy choice as a decision for a person, not a rule. A policy choice goes to `<run>/stages/rules/open-items.json` as an item with `kind: policy_choice`; every other open item carries `kind: missing_input`. No rule carries `policy_choice: true`.
- A decision step with no supportable rule is stamped as an open item naming the missing input in `<run>/stages/rules/open-items.json`, with `kind: missing_input`.
- A token naming a stand-in begins `stand_in.`; a token naming a recorded judgment answer begins `judgment.`.
- `and`/`or` short-circuit left to right, so write the guard first: `is_null(x) or x > 5`, never `x > 5 or is_null(x)`.
- Missing values never become 0 or false: read them through `is_null` or `is_not_applicable`.
- When an open item stamps a token as missing (`evidence_state: needs_more_information`, the token listed in its `missing`), every rule that reads that token guards it first: test `is_null` on it before any read of its value.

## Stated conditions, open values and amount direction

- A condition the process definition (its process details, policies or tasks) states as a trigger for a category, branch or treatment becomes a rule with `evidence_state: Assumed`. Its expression reads a yes/no input token for that condition, named by the condition (for example `<entity>.is_<condition>`), declared in `tokens`, and tested in the expression language's boolean form: the token read directly as the `test`, or as an operand of `and`, `or` or `not`, never compared with a `true` or `false` literal. Never drop such a condition to an open item merely because no observation supports it; the statement is its support. Cite the process-definition statement by its location in the process details (the policy, task or section that states the condition) in the rule's `prose` and the token's `meaning`. The rule and the token list in `observation_ids` only observation ids the snapshot holds for that statement; when the workspace has no observations, `observation_ids` is an empty list. Never invent an observation id, and never drop the rule for lack of one.
- Leave the yes/no input's value open for the customer to confirm, and the same for any rate, threshold or tolerance the condition needs that no observation supports: never invent the value. Read each such value as an input token, name it in an open item (`kind: missing_input`, `evidence_state: needs_more_information`, the token in its `missing`), and guard it with `is_null` first in every rule that reads it. This does not relax "No invented default": no literal enters an expression without a declared configuration value with observations.
- Write the categories as separate rules, one per stated condition, so each can be tested on its own; never fold several conditions into one rule.
- A leftover amount that no stated condition explains is escalated by its own rule; never assign it to a category.
- Every amount-bearing token declares `amount_direction`: `credit` or `debit` when the inputs state which way the amount moves (for example a ledger recorded net of reversals against a report recorded gross), and `needs_more_information` when they do not, with an open item (`kind: missing_input`) naming the token. Never infer a direction from the sign of a number.

## Grounding and proposed controls

Use actual observations and procedure statements when present. Cite their precise locations in rule prose and token meanings; `observation_ids` contains only existing observation identities, and `ratified_sources` only actual ratifications. A procedure without an observation is support for an Assumed rule, never a reason to invent an observation. With neither a statement nor a defensible reason, record the missing input. Evidence states describe support, not a quota to fill.

Consider completeness, duplicate detection, reconciliation, exception routing and people handback where the process makes them useful. These are candidate control families, not required rules for every process. A justified domain proposal may become an Assumed draft rule with empty observation ids: state the specific failure it addresses, the available inputs to check, and why that check belongs in this process. Keep unknown parameters as guarded input tokens with missing-input items. Propose a non-destructive check or draft routing recommendation; domain reasoning never supplies permission to write, approve, delete or commit a transaction. An unsupported permission remains a business decision for a person.

For conflicting evidence, retain both supported statements and their source locations, mark the affected rule Contradictory, and explain the supported reading used for the draft. Preserve existing source rule identities, exceptions and order; never silently replace a source rule with the preferred account. Keep an unresolved business choice in open-items and continue the readable draft without representing either side as ratified. Do not turn conflicts into a new runtime refusal.

Distinguish questions about business policy or authority from builder implementation questions. Use existing policy_choice and missing_input kinds; do not add a third kind. Technical wiring questions stay in internal explanations and do not become invented customer policy choices. Register changes belong to the register stage.

## Finish

Check each expression with `skills/agent-design/scripts/expr.py` `validate` (it must return no problems) before you stop.
