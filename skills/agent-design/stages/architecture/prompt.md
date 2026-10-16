# Agent Design: architecture stage

Read only inside this run's folder and the plugin; never list or open folders beside the run.

Time target: 10 minutes.

You design the agent for a finished Future State Design run. Author `<run>/stages/architecture/internal-dag.graph.json` as the source of meaning for the internal architecture, and write `<run>/stages/architecture/agent.yaml` as its compatible ownership and lifecycle projection. Write agent.yaml in JSON syntax (valid YAML, read with `json.load`): double-quoted keys and strings, no comments, no YAML-only forms. Start from `skills/agent-design/stages/architecture/template.yaml`, which is in the same JSON syntax, and replace every `<<name>>` placeholder. Its one part is a repeatable row, not a prescribed shape: use as many supported responsibilities as the design needs. Choose `part_role` from `agent`, `script`, `router`, `sub-agent` or `orchestration`; remove unused proposed-write and handover rows, and add split fields only to sub-agents. The file must validate against `skills/agent-design/formats/design/agent.schema.json`.

## What to read

- `<run>/stages/intake/intake.json`: the processes and the paths to each process's `current_state`, `transformation_map` and `future_state` (all under `<run>/upstream/`), and `observations` (the snapshot intake and the observation id form `<prefix>observation:<resource_key>`).
- Each future state: its steps, lanes, edges and `agent` tags.
- Each transformation map: its Next rung and Step-up evidence columns, and its Write grants table (`WG-<n>` ids).
- An item the future state lists in its top-level `opens_closed` is closed, not open: never raise it again. An old `## Target platform question` section in the map and its open item, if present, are ignored: the platform is asked only at the Agent Build hand-off.

Cite only observation ids whose resource key is in the snapshot intake. Use readable procedures, current/future-state nodes and transformation-map sections as source support too; there is no observation quota. With zero observations, leave observation lists empty, mark design assumptions `Assumed` and missing detail `needs_more_information`, and continue the draft. Do not invent evidence or mark a proposed design Observed merely because its source is readable.

## Qualified references

Every step is referenced as `<process_id>:<node_id>`, single-process runs included. Every edge is referenced as `<process_id>:<edge_id>`. This holds in `owns_steps`, `proposed_writes[].step` and `handovers[].edge_refs`.

## Which steps are the agent's

The agent's steps are the future-state steps on the Agent lane or with an `agent` tag (a tool a person uses today, an `ai_use` that is not in agent mode, is not one). The check works this out with the same rule Future State Design uses; you do not decide it per step.

- A step whose `future.spans` lists other processes is one step that the agent runs for each of those processes. List it once in `owns_steps`, under its own process (`<process_id>:<node_id>`). Do not add a mirrored ref for each spanned process: the check counts the one listing for every process the step spans.
- In a set, a set-level step (`process_id` is `set`, or a step of the set's shared graph with no `process_id`) is listed once as `set:<node_id>`, in the owning part's `owns_steps` and in the top-level `set_steps` list. When the design has set-level steps, also write `set_summary`: a short plain-prose write-up of what the agent does across the processes, which steps span processes and which steps are set-level. Leave both fields out when there are no set-level steps.
- A person step (a people lane, no agent tag) is never in `owns_steps`.
- When an agent step hands work to a person step in another process, declare that person handover: a `person_handover` entry with `from` the qualified agent step, `to` the qualified person step, and `edge_refs` listing the edge on both sides (`<sending process_id>:<edge_id>` and `<receiving process_id>:<edge_id>`). The check matches `from` and `to` exactly against the edge's two steps; a part id or a lane name does not count. Without it the check records the edge as unowned.

## Header

- `purpose` and `metrics`: what the agent is for and how the business will measure it.
- `authority.default` is exactly `read-only and recommend-only`. The agent hands an item back to the person only when it can cite the evidence for doing so; write that sentence in `authority.hand_back`.
- `access.evidence_state` is exactly `Assumed`: no access has been confirmed yet.
- `proposed_writes`: list every proposed agent write for ratification, one entry each, with the qualified `step`, the `system`, its `WG-<n>` grant id from the Write grants table, and `status` exactly `proposed for ratification`. Nothing is granted by this file.
- `handovers`: every dispatch between parts and every hand-over to or from a person is declared in handovers with the future-state edges it covers. Each entry has `from`, `to` (a part `id`, or `person:<lane_id>` for a person lane; for a person handover into another process, the qualified agent step and the qualified person step), `edge_refs` and `kind` (`dispatch` between parts, `person_handover` to or from a person). Every future-state edge between agent steps owned by different parts needs a `dispatch` entry naming that edge. In a set, an edge from one process's step to another process's step is declared on both sides: its `dispatch` entry lists it as `<sending process_id>:<edge_id>` and as `<receiving process_id>:<edge_id>`.
- `autonomy_ladder`: build the autonomy ladder from the transformation map's Next rung and Step-up evidence columns. Cover all eight dimensions, each with `entry_rung`, `next_rung` and `step_up_evidence`:
  1. output to the person (`output_to_person`)
  2. trigger (`trigger`)
  3. drafting boundary (`drafting_boundary`)
  4. writes to systems of record (`writes_to_systems_of_record`)
  5. hand-off writes (`handoff_writes`)
  6. person checkpoint (`person_checkpoint`)
  7. verification without write authority (`verification_without_write_authority`)
  8. entry rung and graduation path (`entry_rung_and_graduation_path`)

## Logical parts

Choose responsibilities from the source. Multi-process designs default to logical decomposition by deterministic work, judgment, routing, authority, surfaces and handoff boundaries. There is no agent-per-process count. A shared responsibility may span several processes; preserve `set:<node>` ownership once. Use `script` for deterministic work, `agent` for judgment, and `router` for selecting a supported path. Use `orchestration` only when coordinating distinct responsibilities is actually required, and `sub-agent` for a delegated responsibility. No mandatory coordinator or model step.

Each part states `id`, `input`, `output`, `surfaces_read`, `end_point`, `owns_steps`, `grants` and `routing`. Every future-state agent step has exactly one owner; each spanned step is listed once. Grants remain proposals, never authority conferred by this file. `routing.kind` is `code` for a fixed sequence or recorded decision and `model` for judgment; `marked` preserves the upstream mark, or says that it is missing; `basis` names the supporting source and explains the choice. Separate deterministic preparation from judgment in the graph even when both belong to one part.

A person step stays outside the parts. Declare both the handoff and any supported return using `person_handover`; name the destination and the condition/data that lets work resume. Missing return routing is an open detail, not an invented loop.

### Split reasons and the single-agent exception

Every `sub-agent` names a `split_reason` from `skills/agent-design/formats/lists/split-reasons.json` and supplies `split_evidence` with actual observation ids or precise run-relative source pointers. For other roles explain the responsibility boundary through `source_support` and `shape`. A source-backed assumption is acceptable; invented observations are not.

A single-part design requires `single_part_reason`. In a multi-process set this is an exception: explain the actual responsibility and complexity that permit one part, citing specific source steps, common inputs, authority, trigger and review boundary. Explain why separate responsibilities would not help. “No split reason applies” alone is insufficient. Remove the field for two or more parts. A single-agent caption says what that agent does and makes no orchestration claim.

## Authored internal graph and source map

Read `knowledge/reference/library/agent-build/simple/internal-dag.graph.json` and the middle/complex examples only to learn the existing Fast Mode format; do not copy their client facts, labels, rules or evidence. Use `schema_version: internal-dag-v1`, `artifact_kind: agent-internal-subagent-dag`, and the existing `agent`, `header`, `interfaces`, `nodes`, `edges`, `sub_agents`, `assumptions` and `open_register` structures where applicable. The graph is authored from this run's sources, never inferred from part names or filled with reference content.

- Give each internal step a stable `id`, a concrete `title`, `type`, `runs_on`, `input`, `output`, `source`, `evidence_state`, and `fs_ref` to its qualified future-state step when one exists. Use `step` for work, `escalation_gate` for routing, `review_seam` for a person boundary and `root_agent` only for supported coordination. Use `runs_on: code` for deterministic operations and `model` for judgment. Keep a missing future-state step in departures rather than silently adding it.
- Use interfaces for reads, draft surfaces, proposed writes and handoffs. Each names the actual system/data, source, evidence state and anchored step (`at_step`/`seated_in`). Step `reads`, `drafts_into`, `emits_handoff` and interface anchors must agree. A draft surface is distinct from a system-of-record write. Keep every proposed write in `header.proposed_writes` with its source grant; no graph field can grant authority.
- Declare edges with a unique `id`, `from`, `to` and `kind` (`sequence`, `then`, `else`, `dispatch`, `handoff`, `reentry`, `read_access`, `inbound` or `write` as supported). For every handoff and person return, state what crosses the boundary, the receiving step and the supported condition. Preserve future-state edge references in the projection. Do not invent a return, trigger or routing state to complete a picture.
- Represent actual source-supported state and its provenance at the node that records it; a dispatch edge may use `reads_state_from` to reference that node. Use read interfaces for external state surfaces. Identify readers, proposed writers and the state used in routing. Unknown access, state or return detail stays in `open_register` with `state: needs_more_information` and `bound_to` element ids. Explain assumptions beside the affected element.
- For multiple parts, `sub_agents[].member_nodes` and each node's `sub_agent` must agree. A single flow omits a root and sub-agent registry. Set `header.flow_label` to the concrete responsibility (or SCRIPT); its caption describes one flow without orchestration. Explain the chosen and rejected shapes in the header, with source and evidence state.
- Populate `agent.yaml.source_support` for every authored internal step, read, write, system state, handoff, person return and shape decision. Each row names the graph element or qualified step, `kind`, a run-relative `source` with exact node/section pointer, `evidence_state`, and `detail` explaining what the source supports and what remains uncertain. This is a semantic crosswalk, not a list of filenames. Knowledge-backed inference stays Assumed; an unsupported detail stays open. Reconcile this map with graph ids, part ownership and projection handovers before finishing.

Readable older runs without the graph remain supported legacy input; describe their available ownership detail honestly and do not fabricate an internal graph from it. Do not add mandatory outputs to the stage index. When rendering the authored graph, use a fresh subdirectory of the architecture stage so derived configuration and diagram files cannot overwrite `agent.yaml`. Renderer findings are diagnostics: preserve them and continue with the readable draft; rendering success alone proves neither source fidelity nor visual acceptance.

## Shape and rejected alternatives

`shape` names the chosen shape and cites its actual observation ids or run-relative source pointers; observation ids may be absent. The corresponding `source_support` rows explain the reasoning. `rejected_alternatives` lists the other shapes you weighed, each with the reason it was rejected.

## Departures

The `departures` key is always present; it is an empty list when the design has no departures.

- A departure from Future State Design's `agent` tags is allowed only with recorded evidence. Each departure row has `evidence` (actual observation ids or precise run-relative source pointers), `change` (what changed: `role`, `authority`, `person_step` or another short name) and, where it applies, `sign_off`.
- A departure that changes a role, authority or a person step is marked exactly "pending business-owner sign-off" in `sign_off`, and becomes a ratification item for "What we still need to know".
- Merging two Future State Design `agent` tags into one part is a departure that needs recorded evidence.
- A future-state step the design needs but the future state lacks is recorded as a missing step for Future State Design: a departure row with `missing_future_state_step: true` and `routed_to: "future-state-design"`. Do not add the step yourself.

When an input is missing or unclear, keep the draft, label the affected detail `needs_more_information` and record any assumption as `Assumed`; never present a guess as source evidence.

## Upstream write-grant problems

The Write grants table and the future state are inputs: judge them, never edit them. When your prompt ends with an "Upstream write-grant problems" list (the intake ran the plugin's shared grant rules on them), design to what the evidence supports and record, for each `fail` line, the reading you took in agent.yaml's `upstream_readings` list: `{"grant": "WG-<n>", "column": "<Write grants column>", "table_says": "<the cell>", "read_as": [<for Steps: the future-state step ids the grant covers>], "rules": [<the problem names it answers, e.g. "write-before-confirm">], "reason": "<the map text or future-state evidence that supports it>", "status": "resolved"}`. When the evidence cannot settle it, say so with `"status": "unresolved"`; it stays in What we still need to know. A problem is never a reason to stop: the check records what you chose and the run continues. Your parts hold and own the grants as you read them.

Write the authored `<run>/stages/architecture/internal-dag.graph.json` and its `<run>/stages/architecture/agent.yaml` projection. Preserve upstream source files and reference bytes.
