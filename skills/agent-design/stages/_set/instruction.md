# This run is a set of processes

Read only inside this run's folder and the plugin; never list or open folders beside the run.

This run is a set: the intake lists more than one process, and you design for all of them at once. Read this before the stage instructions below; it applies to every stage.

## Work from the set map and the link model

Work from the intake's set map and the link model around the shared work object. The intake (`stages/intake/intake.json`) gives you:

- `processes`: one entry per process, each with its `process_id`, its `current_state`, `transformation_map` and `future_state` paths, and its `evidence_strength` (observation count, source count and evidence states). Weigh a process with thin evidence as thin; never fill its gaps from a better-evidenced process.
- `set_map`: the path of the set map (under `upstream/`). Open it. It holds the `work_objects`, their `object_states`, the `links` between processes and the `open_points`.

## Name the shared work object

Name the shared work object and its states. Before you design anything, write down which work object (or objects) the processes share, which system of record holds it, and the ordered states it moves through, as the set map's `object_states` records them. Every process in the set moves that object from one state to another; say which process moves it into which state.

## Treat every link between processes as a boundary

Treat each flow, overlap and variant-entry link as a boundary between processes. The set map's links carry one of these kinds:

- flow links: `handoff`, `variant_entry`, `parent_child`, `lifecycle_continuation`, `decision_gate`, `re_entry`;
- `overlap`: two processes move the same object into the same state;
- context links, not flows: `analogous_work`, `shared_knowledge`, `shared_team_or_system`.

A flow, overlap or variant-entry link is a place where work crosses from one process to another. Design it as a boundary: what crosses (the object and its state), who or what hands it over, and what the receiving side checks. An overlap needs one owner for the state change or a stated rule for who moves it. A variant entry needs its routing rule, or an open point when the set map records none. Context links inform the design but carry no work across.

## Keep one design for the set

Keep one design for the set, never one disconnected design per process. Agents, rules, fields and cases are designed once across the set: a part may own steps in several processes, the same policy may hold in several processes, and the processes meet at the links above. Never write a separate, self-contained design per process with nothing joining them.

## Tag everything with its process

Tag every rule, placed rule and case with its `process_id`. Use the `process_id` exactly as the intake lists it. A placed rule carries it in its `process_id` field; a rule and a case carry it as the first part of their qualified `decision` (`<process_id>:<node_id>`), since their formats hold no separate field. A rule is placed at exactly one step. When the same policy needs a seat in several processes, author a distinct rule for each process at the rules stage, each with its own id and its own qualified `decision`, before placement; each of those rules is then placed once, naming its own `process_id`. Steps and edges stay qualified as `<process_id>:<node_id>` and `<process_id>:<edge_id>`. Anything left untagged is counted as covering no process, and a process with no rules or no cases is reported as uncovered.
