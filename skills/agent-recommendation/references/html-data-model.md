# Reusable HTML report data model

Use `scripts/render_report.py` to populate the shipped template. Do not generate replacement HTML or edit the template per case. The bundled template preserves the user-supplied Within design, layout, components and interactions; all case-specific content comes from JSON. Its original finance example is not a factual or instructional default.

```sh
python3 -I -B scripts/render_report.py --input report.json --output recommendations.html
```

The command writes a standalone HTML and `recommendations.evidence.json`. `--evidence-output PATH` changes the latter filename; `--data` aliases `--input`. The HTML is static HTML and CSS with no script; it names the evidence file in its footer but does not embed it. Deliver the HTML and evidence file together. No server, package install or network is required. The renderer uses Python's standard library. Fields marked with a budget are checked by the renderer; see the Length budgets section of `report-spec.md`.

Use `assets/example-report.json` as a fully populated shape example, **not** as production evidence. It describes synthetic post-call qualification work. The data model is distinct from the discovery ledger: prepare it from the original ledger and confirm that all selected source memberships and typed relationships survive; a renderer cannot detect evidence omitted before it receives the JSON.

## Required JSON fields

Unless explicitly nullable or optional, fields are required. Text must be nonempty; use a precise unknown and its consequence instead of an invented fact. List entries are text unless a structure is shown. IDs use letters, digits, `.`, `_`, `:` and `-`, starting with a letter or digit. Source links, when present, must be absolute HTTP(S) URLs. Files remain attachments, not fabricated public links.

### Root

| Field | Shape and meaning |
|---|---|
| `schema_version` | `"1.0"` |
| `stage` | `"initial_hypotheses"` or `"refined"` |
| `title`, `headline` | Report/browser title and plain-language leading conclusion |
| `provenance_note` | Evidence provenance or illustrative status, shown in the header |
| `source_labels` | Nonempty list of source names shown as header badges |
| `objective` | Object below |
| `current_state` | Object below |
| `recommendations` | Object below |
| `problems` | Ordered hypotheses; zero is valid and uses `empty_reason` |
| `guardrails` | Shared constraints; empty only if none are established |
| `evidence` | `{scope_notes, sources: [{id, label, detail, url?}]}`; source IDs must be unique |
| `empty_reason` | Required text if no hypotheses; otherwise may be `""` |
| `feedback_summary`, `artifact_changes` | Required for `refined`: feedback text and nonempty list of what supplied artifacts changed |
| `stage1_recommendation_id`, `stage2_round`, `our_recommendation`, `round2_basis` | Refined reports only; see Stage 2 verdicts below. Present (even as `null`) on `initial_hypotheses` is rejected |
| `run_folder` | Optional absolute path of the Stage 1 run folder; nonempty single-line text, or absent/`null` when not set |

`objective = {description, north_star, attributes: [{label, value}]}`. Budgets: `description` 35 words, `north_star` 25, each attribute `value` 30. Attributes are extensible; keep the user's labels, and keep full long strings (including line breaks) in the evidence file and details while the visible value stays within budget. At least one must be labeled `Technology landscape` or `Tech landscape`. Include platforms, acceptable forms, access status, human responsibilities and constraints here, not only in prose elsewhere. Unknown or bypassed intake must remain explicit.

`current_state = {description, snapshot, details: [step], coverage, process_index, relations}`. Budgets: `description` 60 words, `snapshot` 30 words; longer material goes in `details`. `details` is nonempty. `snapshot` names evidence scope/date; `coverage` explains the actual reading and source-inspection depth.

- `process_index`: `[{id, title, role, url?}]`; every referenced process must exist here.
- `set_map` (optional; use it whenever a hypothesis spans two or more processes): set map v0, below.
- `relations`: `[{id, type, from_process, to_process, rationale, evidence_refs: [source_id]}]`. Type is exactly `analogous_work`, `shared_knowledge`, `observed_handoff` or `proposed_bridge`. Both endpoints must exist. An observed handoff needs source references; the producer must establish actual object/version continuity, not infer a handoff from shared software. Other types may have no supporting source only when their rationale explicitly explains the hypothesis or gap.

### Set map (`current_state.set_map`)

How the recommended processes fit together, so the next module reads the links instead of rebuilding them. `scripts/set_map.py check` and the renderer check it; the renderer does not judge whether the evidence is true.

| Field | Shape and meaning |
|---|---|
| `set_map_version`, `set_id`, `title`, `read_at` | `"0"`, an identifier, a plain title, the date the evidence was read (`YYYY-MM-DD`); optional `evidence_cutoff` |
| `lead_process` | Optional; a process below, and the one marked `lead` if any |
| `processes` | Nonempty `[{process_id, title, role, trigger, end, reads_states, writes_states}]`, plus optional `lead`, `key_control`, `role_text`, `membership_of`. `role` is `entry_variant`, `main_step`, `closing_step`, `upkeep`, `gate` or `other`. Every process in any hypothesis's `process_memberships` is here, and every one here is in `process_index`. `membership_of` names hypotheses that list the process |
| `work_objects` | `[{id, name, system_of_record, relations: [{kind, object}]}]`, optional `id_pattern`, `external`; `kind` is `parent_of`, `child_of`, `creates`, `created_by` or `record_of` |
| `object_states` | `[{id, object, name, order, moved_in_by, evidence_state}]`; `order` is the place in the object's life (not a layout position); `moved_in_by` names processes, context neighbours or actors |
| `links` | `[{id, kind, from, to, evidence_level, evidence_state, evidence_refs, rationale}]`, optional `object`, `state`, `shared_instance_count`, `routing_rule`, `relation_id`, `relation_type`, `open_point_ids`, `flow` |
| `context_neighbours` | Processes outside the set that touch it: `[{id, process_id, title, touches, attach_to, found_by, evidence_state}]`; `touches` is `gate`, `parent`, `continuation`, `upkeep`, `alternative_outcome` or `other`; `attach_to` names the states or processes where it touches. A process is a member or a neighbour, never both |
| `actors` | Optional `[{id, name, kind}]`, plus optional `kind_text`, `system`, `writes_objects`, `evidence`: the one list of performers that move a state or write an object but are not processes. `kind` is `external_party` (a vendor, a customer), `rules_automation`, `ai_automation` or `other` (with `kind_text`). This key replaces the earlier `external_parties`; a set map that still carries `external_parties` is refused, never mapped |
| `open_points` | `[{id, text, evidence_state, about}]`, optional `client_text` for "What we still need to know" |
| `checks` | Optional `[{check, result, detail?}]`, result `pass`, `open` or `fail` |

- **Link kinds:** `handoff`, `variant_entry` (several processes produce the same object in the same state), `parent_child`, `lifecycle_continuation`, `decision_gate`, `re_entry`, `overlap`, `analogous_work`, `shared_knowledge`, `shared_team_or_system`. The first six are flow; the last three never are. `from` and `to` are lists, because a variant entry or an overlap joins several processes.
- **Evidence level**, strongest first: `shared_instances`, `step_state`, `trigger_end_text`, `same_capture_session`, `dependency_field`, `hierarchy_scope`, `shared_team_or_system`. A link at `shared_instances` carries `shared_instance_count` (1 or more) and is never `Assumed`. A shared team or system never establishes a flow link on its own.
- **Evidence state** is `Observed`, `Assumed`, `Contradictory` or `needs_more_information`; a link that is `Contradictory` or `needs_more_information` names its open point. Each `evidence_refs` entry is `{source_id, locator, read_at}`, and `source_id` is an `evidence.sources[]` id (add the feedback or the artifact as a source when it is the evidence).
- **`relations[]` is a view of the set map.** Every `relations[]` row is named by exactly one link's `relation_id`, and that link joins the row's two processes. A `handoff` at `shared_instances` is an `observed_handoff` row; any other `handoff` is a `proposed_bridge` row; `analogous_work` and `shared_knowledge` map one to one; a `variant_entry` may be the view of one `analogous_work` row (add one `analogous_work` link per further row). The other kinds set `relation_id` to `null`; the report lists them under the relations.
- **Single process:** a report whose hypotheses each cover one process needs no set map; it renders as before.

`recommendations = {description, assumptions: [text], ignore_if: [text]}`. Assumptions and reject conditions are nonempty and easy to correct. The description explains the common objective, overlap and stage.

### Each work hypothesis in `problems`

| Fields | Meaning |
|---|---|
| `id`, `rank` | Unique hypothesis ID and unique positive integer; rank controls selection order |
| `title`, `statement` | Intuitive work title (budget 14 words) and how it works today, including actor and trigger (budget 45) |
| `fit` | Brief hypothesis status, e.g. `Investigate`; not an approval. On a refined report the producer rewrites `fit` and `explore` to suit a report that ends in verdicts: no `Investigate` fit on a refined report whose solutions have verdicts |
| `recover`, `recover_detail` | Short (`recover` budget 25 words) and longer (`recover_detail` budget 40 words, shown on the Potential value card) potential-value descriptions; uncertainty and effort belong here, no invented savings |
| `explore` | What investigation or evidence should come first; replaces the supplied template's build-first subtitle (budget 15 words, shown in the problem rail) |
| `owner`, `systems` | Accountable role and list of involved systems |
| `source_summary`, `source_refs` | Visible source summary and nonempty list of known source IDs |
| `note` | Optional content, required field; may be empty string |
| `details` | Nonempty current-state step descriptions |
| `process_memberships` | Nonempty `[{process_id, role}]`; retain every contributing source, no duplicates |
| `relation_refs` | List of known relation IDs; both endpoints must remain in this hypothesis's memberships |
| `counts` | Structure below |
| `solutions` | Nonempty list of plausible forms; do not manufacture alternatives to meet a quota |
| `avoid` | `[{id, name, reason}]`, or `[]` |

`counts = {raw_observations, read_observations, distinct_events, exceptions, cadence, window, limitations}`. First three are nonnegative integers or `null` for unknown; `read_observations` cannot exceed known raw count. Remaining fields are nonempty strings including definitions and basis; `limitations` budget 40 words. Record proportions are not exception rates; observations are not executions; counts are not annual frequency or impact. The displayed summary preserves these limits.

### Each solution

| Fields | Meaning |
|---|---|
| `id`, `name`, `role` | Stable globally unique solution ID, plain title (`name` budget 12 words), status |
| `what` | Proposed useful help, beneficiary and output; initial architecture is provisional (budget 35 words) |
| `future` | Hypothetical trigger, inputs, action, output, review and stopping point (budget 60 words) |
| `impact` | `high`, `medium`, `low` or `unknown` (displayed as Not assessed) |
| `impact_reasoning` | Objective-relative value and effort basis; explain missing information when unknown (budget 35 words) |
| `agent_does`, `people_keep` | Bounded assistance and retained human responsibility; labels do not require an agent |
| `selection_summary` | Why assistance might fit and the strongest simpler alternative (budget 25 words; longer reasoning in `selection_details`) |
| `assumptions`, `ignore_if`, `challenges` | Nonempty lists; shown in the open card body, not on collapsed cards (the first card of the selected problem is open by default); content is never dropped (budget 20 words per item) |
| `counter_hypothesis` | Concrete alternative explanation for why it would not help |
| `form_factors` | Nonempty list of plausible forms, including one if only one is defensible |
| `selection_details` | Nonempty fuller criteria assessment, alternatives and evidence, expandable |
| `decision_change` | Which finding would alter the choice |
| `source_refs` | Known IDs supporting solution reasoning; may be empty if no additional source is established |
| `information_requests` | Tailored artifact requests below |
| `continuation` | `{destination, status, feedback_request}`; `status` is `permitted` or `unresolved` |

Each `information_requests` entry is:

```json
{
  "artifact": "A linked example of the working spreadsheet and final decision memo",
  "contains": "Same-case inputs, intermediate columns and formulas, final output and the corrections made by its owner. Preserve a shared example ID.",
  "why": "The process narrative does not expose how the owner reaches the decision.",
  "decision_change": "Decide whether assistance should retrieve evidence, draft analysis, or abstain from the judgment.",
  "acceptable_substitute": "A redacted example or a walkthrough of the same case; identify what remains inferred.",
  "timing": "needed_now"
}
```

Budgets: `contains` 25 words, `why` 25 words. Timing is `needed_now` or `later`. Request the smallest useful set, not a default of three. Reuse artifacts already supplied. Initial hypotheses normally include tailored requests. Any solution may use an empty list only with nonempty `evidence_sufficiency` naming the already-supplied materials and explaining why no further artifact is needed for the next decision; do not invent a request just to fill the UI. Fully establishing evidence does not itself authorize implementation.

The renderer generates the copy/paste prompt on Stage 1 (`initial_hypotheses`) reports only, from the selected ID, objective, full attributes, source scope, tailored requests and actual output filenames. Its first line is `Use the agent recommendation skill (within:agent-recommendation) for this.` for every destination (plain words, never a leading "/"; the renderer refuses a copy-box prompt that starts with "/"). A blank line follows. When `run_folder` is set, the next line is `Continue the Stage 1 run in <run_folder>: reuse its evidence and add to its decision log. If that folder isn't available, use the attached files.` The prompt asks for feedback and reconstructing the work before specification. It never claims files are already attached. No free-form prompt override exists that can silently turn the continuation into build authorization. The step-1 label (`destinationLabel`) is "Continue in this session, or open Claude Code, then paste this prompt" for a Claude Code destination, "Continue in this session, or open Codex, then paste this prompt" for a Codex destination, "Continue in this session, or open <destination>, then paste this prompt" for another permitted tool, and "First confirm an allowed chat destination: <destination>" when unresolved, so the page asks the user to identify an allowed destination.

## Stage 2 verdicts (refined reports)

On `stage == "refined"` every solution ends with a verdict and the report ends with one Our recommendation line; the Stage 1 continuation prompt is not generated. On `initial_hypotheses` none of these fields may be present (presence includes an explicit `null`), and the HTML data and evidence file are unchanged.

General rules: "text" is a JSON string that is nonblank after trimming; "list of text" is an array whose every entry is text; booleans are exactly `true`/`false`; integers are JSON integers (`true` and `1.0` fail). Every container is type-checked before it is read, and malformed input raises `ReportDataError`, never a default. Errors name the path: root fields as `stage2_round`, `our_recommendation.solution_id`, `round2_basis.item_brought`; verdict fields as `problems[<problem id>].solutions[<solution id>].verdict.<field path>`. Unknown keys inside the new objects are preserved inert in the evidence file; `prompt`, `design_prompt` and `round2_prompt` inside `verdict` are rejected (prompts are always generated).

### Root fields

| Field | Shape and meaning |
|---|---|
| `stage1_recommendation_id` | Required: the id of the Stage 1 recommendation the person picked, the same in round 1 and round 2. It must be a valid identifier (starts with a letter or digit, then letters, digits, `.`, `_`, `:` or `-`), because it becomes part of the file names below. It must not contain `-r2` in any case, which marks round 2 in those names; this keeps every recommendation's and round's files distinct, except ids that differ only in case, which the producer checks before saving |
| `stage2_round` | Required: integer `1` or `2`. One round by default; a second only when a blocked item was brought |
| `our_recommendation` | Required: `{solution_id, text}`. `text` is text (budget 35 words). `solution_id` names a solution in this report whose verdict is not `dont_pursue`, or is `null` — allowed only when no solution has another verdict (including a zero-problem report with `empty_reason`) |
| `round2_basis` | Required when `stage2_round` is `2`, rejected otherwise: `{blocked_solution_id, item_brought}`, both text. `blocked_solution_id` names the round-1 solution whose blocking item the person brought; it need not exist in this report. Round 1 is saved as `report-refined-<recommendation id>.json`, `recommendations-refined-<recommendation id>.html` and `recommendations-refined-<recommendation id>.evidence.json`; round 2 as `report-refined-<recommendation id>-r2-<solution id>.json`, `recommendations-refined-<recommendation id>-r2-<solution id>.html` and `recommendations-refined-<recommendation id>-r2-<solution id>.evidence.json`, where the recommendation id is `stage1_recommendation_id` and the solution id is `round2_basis.blocked_solution_id`; neither round writes to the Stage 1 files, and round 2 never writes to the round-1 files |

### Per-solution `verdict`

| Field | `ready_to_design` | `dont_pursue` | `blocked` |
|---|---|---|---|
| `kind` | required | required | required |
| `reason` (text, budget 35 words; for `dont_pursue` names the simpler route when one exists) | required | required | required |
| `do_first` (solution id) | optional | rejected | optional |
| `blocking_item` `{question, owner}` (text, text; owner may be a role) | rejected | rejected | required |
| `readiness` | required | rejected | rejected |
| `design_brief` | required | rejected | rejected |

- `kind` is exactly `ready_to_design`, `dont_pursue` or `blocked`. When `stage2_round` is `2`, no solution may be `blocked`.
- `do_first` names a different solution in this report whose verdict is not `dont_pursue`; chains must not form a cycle. It is advisory ordering only.
- `readiness = {permissions, intent, context, output_checks}`, each `{status, basis}`; `status` is exactly `"met"` and `basis` is text.
- A `ready_to_design` solution has no `information_requests` entry with `timing: "needed_now"`. A `blocked` solution has at most one: any needed-now request it carries is its one deciding item, and other gaps go in its `information_requests` with `timing: "later"`. Two or more needed-now requests on a blocked solution raise `ReportDataError` at `problems[<problem id>].solutions[<solution id>].verdict.kind`.
- `continuation` stays required; on refined reports its `destination` and `status` choose the verdict prompt's first line and label.

### Design brief (`verdict.design_brief`, ready to design only)

The report shows it in five groups: Objective, Processes, Proposed future state, Limits and authority, What we still need to know.

| Field | Shape and meaning |
|---|---|
| `objective` | Text: the objective, how success is judged and its owner (named person or role) |
| `processes` | Nonempty list of `{process_id, role, lead}`: `process_id` text, unique in the brief, present in `current_state.process_index` and in the parent problem's `process_memberships`; `role` text; `lead` boolean; exactly one `lead: true` |
| `how_processes_connect` | Optional text, allowed only with two or more processes; when present it must be text. With a `set_map`, leave it out: the report and the design prompt use a summary written from the set map's flow links |
| `set_map` | Required when `current_state.set_map` is present, rejected otherwise: the set map scoped to this brief. Its `processes` are exactly the brief's processes, its lead is the brief's lead, and it keeps every link in `current_state.set_map` that touches one of them, with each far end as a process or context neighbour. Make it with `scripts/set_map.py scope`. Allowed with one process, so a single-process design still sees where its work comes from and goes |
| `future_state` | Text: the proposed future state |
| `limits_and_authority` | `{reads, writes, important_errors, must_not}`: `reads` list of `{system, what}`; `writes` list of `{system, what, who}`, plus an optional `source`, with `who` exactly `agent` or `person`; `important_errors` nonempty list of text; `must_not` list whose items are text or `{text, source}`. `reads`, `writes`, `must_not` may be empty but must be present. See Client source ids below |
| `still_need_to_know` | List of text; may be empty; must be present |

Agents read, decide, write and send by default. `future_state` covers all the work in the named processes, not only the recommended agent's slice, and may use several agents, a shared reader or router over a common system, deterministic scripts for mechanical steps and orchestration where required.

#### Client source ids (additive, optional)

A write with `who: person` or a `must_not` item appears only when the client's evidence says so, and then carries that evidence's source id; key control and outside parties are the other reasons a person stays.

- `writes[]` items take an optional `source`: text, the id of a source in `evidence.sources` that states the rule.
- `must_not[]` items are text (as in older reports, still valid) or `{text, source}` with `text` nonempty and `source` optional.
- A client-sourced item renders with ` (client-stated: <source>)` after it, in the report HTML and in the design prompt text. An item without `source`, or with a blank or non-text `source`, renders exactly as before; a report that never uses the field renders unchanged and is never refused for lacking it.
- The view model's `brief.writes[]` items gain `source` only when set; `brief.mustNot` stays a list of text, with the marker appended to client-sourced items.
- Future State Design reads the same field name (`source`) from either must-not form.

### Generated prompts

- Ready to design: a design prompt whose first line is `Use the future state design skill (within:future-state-design) for this: design from the refined report <path>, solution <solution id>.` for every destination The path is the report JSON passed to `--input` (rendered in code without one: `<run_folder>/report-refined-<recommendation id>.json`, or the bare file name without a run folder). It names the processes (one, or several as resource keys with the lead), passes the connection text (`how_processes_connect`, else the set map summary with two or more processes) and `run_folder` only when set, says where the set map is and how many links, context neighbours and open points it has when there is one, points at `verdicts["<solution id>"].design_brief` in the evidence file for when the report file is not available, lists the brief with `none` for empty lists, and ends by saying it starts a future state design only and authorizes no building, deployment, new access or writes. Select-and-copy box headed `Copy design prompt`.
- Blocked, round 1 only: a round-2 prompt with the Stage 1 first line and Stage 1 step-1 label above, the open question and owner, and a placeholder for what the person brings; it ends with ready to design or don't pursue. After the attach line it tells the producer to save the round as `report-refined-<recommendation id>-r2-<solution id>.json`, `recommendations-refined-<recommendation id>-r2-<solution id>.html` and `recommendations-refined-<recommendation id>-r2-<solution id>.evidence.json` (this report's `stage1_recommendation_id` and the blocked solution's id) and never to write to the Stage 1 report files or to the current report's HTML and evidence files. Select-and-copy box headed `Copy round 2 prompt`.
- Don't pursue, and any solution in round 2 that is not ready: no prompt.

### Evidence file on refined reports

- `report` holds the full input (including `stage2_round`, `our_recommendation`, `round2_basis`; there are no root copies).
- `continuation_prompts` is `{}`.
- `verdicts` maps every solution id to its full verdict object; `design_brief` is copied verbatim as structured JSON.
- `verdict_prompts` maps only solutions that have a prompt (ready; blocked in round 1) to the prompt text.

### View-model keys (refined only)

`populate()` adds a top-level `ourRecommendation: {solutionName, text}` (`solutionName` is `""` when `solution_id` is `null`) and, per solution, `verdict: {kind, label, reason, doFirstName, blockedQuestion, blockedOwner, prompt, promptLabel, copyLabel, requests, brief}`, and sets the solution's `prompt` to `""`. Labels are `Ready to design`, `Don't pursue`, `Blocked`; `copyLabel` is `Copy design prompt`, `Copy round 2 prompt` or `""`; the page shows it at the start of the select-and-copy hint under the prompt. `requests` is the same formatted request list Stage 1 shows (`[]` for don't pursue). `brief` is `null` unless ready, else `{objective, processes: [{id, title, role, lead}], connect, futureState, reads: [{system, what}], writes: [{system, what, who}], importantErrors, mustNot, stillNeed}`. Absent values are `""`, `[]` or `null`. On Stage 1 nothing is added.

## Preservation, security and interaction

- The page is static HTML and CSS: `render_report.py` writes every section, verdict and prompt in Python, and refuses to write a page that carries a script tag, an inline event handler or a `javascript:` URL (`assert_no_script`). Every report string is HTML-escaped, never inserted as raw HTML. A single placeholder substitution prevents data containing placeholder names from altering the shell.
- No script, runtime or font is embedded or fetched; a Content Security Policy blocks scripts and network connections. The original design's three font files were absent from the supplied export, so its existing system-font fallback is retained. No font has been downloaded or substituted from the web.
- Interactions are pure CSS and stay local to the open page: problem selection (radio buttons), expandable details and approach cards (`<details>`), Read full current state (a checkbox), Pursue this / Not a fit for us / Bring back / Hide direction (one radio group per approach). Each prompt sits in a read-only box with a short select-and-copy hint (`Copy investigation prompt: ...`). Selections are not saved and send no feedback. The continuation prompt carries the selected ID; the user supplies corrections in the new chat.
- The investigation prompt, Attach list and tailored evidence list sit in the Direction panel that Pursue this reveals in an expanded solution. A collapsed solution card shows only its ID, name, `what`, impact pill and impact note; critical assumptions, the counter-hypothesis, reject conditions and challenges are shown in the open card body, and the first card of the selected problem is open by default. Their content is never dropped.
- The exact input report, sources, process memberships and typed relations remain in the evidence artifact delivered next to the page. Verify against the original discovery ledger before rendering and check the rendered HTML after every content run. The renderer validates structure and references; it does not certify evidence truth, causal claims, access permissions, or appropriate research.

Stage 1 continuation labels use the destination word for word: for every permitted destination, "Continue in this session, or open <destination>, then paste this prompt". Never choose a tool from words inside the destination. Unresolved destinations keep the confirmation label.
