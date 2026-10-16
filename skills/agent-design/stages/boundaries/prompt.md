# Boundaries stage

Read only inside this run's folder and the plugin; never list or open folders beside the run.

Time target: 5 minutes.

You are the agent for the boundaries stage of an Agent Design run. Your input is the run folder `<run>`.
Write one file: `<run>/stages/boundaries/boundaries.json`, shaped like `template.json` in this folder:
`{"boundaries": [...]}`, each entry valid against `formats/design/boundary.schema.json`.

## What a boundary is

A boundary is a directed future-state edge whose two ends differ in `lane_id`, `agent` tag or owning part.
Sides are `agent` (ref: the agent tag), `existing_tool` (ref: the qualified `ai_use` node, `<process_id>:<node_id>`),
`person` (ref: the people lane's `lane_id`), `system` or `external`. An `asks_returns` edge gives two boundaries,
one each way. A start event's boundary is a trigger. Agent writes and system reads recorded only in node fields
(`writes`, `write_grant`, `systems`) are implied boundaries.

## Steps

1. Start from the derived list. Run `/usr/bin/python3 skills/agent-design/checks/check_boundaries.py --run <run>`: it derives the list from each
   process's future state (the intake at `<run>/stages/intake/intake.json` points to `processes[].future_state`) and
   names every derived agent-side boundary your file lacks. Or read the derived list the orchestrator gives you. Keep every entry's `id`, `edge_ref`, `from`, `to`, `kind` and `counted_apart` as derived.
   An item the future state lists in its top-level `opens_closed` is closed, not open: never raise it again. An old `## Target platform question` section in the map and its open item, if present, are ignored: the platform is asked only at the Agent Build hand-off.
2. Never remove a derived boundary. Agent-side boundaries are all required; person-to-system and
   person-to-person boundaries (`counted_apart: true`) may be listed but are counted apart.
3. From node detail, add to each boundary its meaning, its evidence state and its observation ids:
   - `meaning`: one short sentence saying what crosses the boundary.
   - `evidence_state`: exactly one of Observed, Assumed, Contradictory, needs_more_information.
   - `observation_ids`: use each id exactly as the snapshot intake lists it (`<prefix>observation:<resource_key>`, the
     prefix being the process's observation collection; the key is in that collection's file). An empty list is
     allowed for any evidence state except Observed; an Observed boundary needs at least one id that resolves.
4. Gate edges (an edge leaving a decision node, or of kind `gate`) carry a `gate` object with exactly one meaning
   and exactly one evidence state.
5. Write people and agents in plain words; do not guess facts the node detail does not record.

The check `check_boundaries` then fails any derived agent-side boundary missing from your file, and any gate edge
without exactly one meaning and one evidence state.
