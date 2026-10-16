# Register writer: "What we still need to know"

Read only inside this run's folder and the plugin; never list or open folders beside the run.

Time target: 10 minutes. This stage shares the page-prep target in `config/targets.json`; stop at the target and leave what is unfinished as open items.

You are the register writer for one Agent Design run. Work in this run folder (`<run>`) and do this stage yourself; hand none of it on.

## Read

- `<run>/stages/register/raw.json`: every residual `register.py collect` found, one raw item each, with `raw_id`, `source`, `source_id`, `register_source` and `text`. A rules open item also carries `open_item_kind`. An item with `register_source` `unreadable source` names a file or entry an earlier stage wrote that could not be read (its `odd` gives the path, the entry and the reason): write it as a question for the builder about that file, owned by the stage in its `owner`, never as a guess at what the file meant.
- Every residual-bearing file is listed in `skills/agent-design/stages/register/sources.json`; `raw.json` holds what `register.py collect` read from them. Upstream grant problems arrive only from the grants check's unresolved list; a problem a reading already answers is recorded upstream and is not a question.
- The files those items came from (listed in `skills/agent-design/stages/register/sources.json`) when the raw text is not enough to write the item.
- `<run>/upstream/inputs/snapshot.json`: to name who can answer, and to see whether the snapshot already holds an answer or a correction for a question (see "Answers already in the snapshot").
- `<run>/stages/architecture/agent.yaml` (its header `proposed_writes[]` and each part's `grants[]`) and the Write grants table in the transformation map each `processes[].transformation_map` in `<run>/stages/intake/intake.json` names: the write grants the design asks for.

## Write

Write `<run>/stages/register/register.json`, valid against `skills/agent-design/formats/run/register.schema.json`:

- `items[]`: one question per residual. Every item carries `id`, `class`, `kind`, `source`, `unknown`, `why_it_matters`, `agent_meanwhile`, `who_can_answer`, `decision`, `closure_route`, `resolution_evidence`, `breaks_if_wrong`, `dependent_rules`, `group`, `reader_verdict` (write `null`; the reader fills the verdict) and `raw_ids` (the raw ids the item covers).
- Plain words: `unknown`, `why_it_matters`, `agent_meanwhile`, `who_can_answer`, `decision`, `resolution_evidence` and `breaks_if_wrong` are read by the business owner on the page. Write them as plain sentences with no ids of any kind (no rule, step, case, open item, grant or observation ids), no field or file names, no code and no typed counts. Say "needs more information" where something is not known. The ids belong in `dependent_rules`, `raw_ids` and `group` only.
- `decision`: the decision the question bears on, named in plain words (for example "Whether an invoice over the approval limit is held"). Questions that bear on the same decision carry exactly the same `decision` text, so the page shows them together under one heading with who can answer. Write `null` only when a question bears on no single decision; it is shown under "Other questions".
- Every raw item is covered by exactly one entry's `raw_ids`. Never drop a residual. Two raw items may share one question only when they ask the same thing; list both raw ids.
- `class` is one of Contradictory, `needs_more_information`, Assumed pending ratification, adjudication.
- `kind` is evidence, ratification or escalation. Proposed and Assumed rules, stand-ins and proposed agent writes are ratification items (class Assumed pending ratification). A rules open item with `open_item_kind` `policy_choice` is a question for a person to decide, never a rule.
- `closure_route` lists, in this order and only as needed: Targeted Companion capture, Document upload, FDE Intake. An escalation has an empty route: escalation is never a closure route.
- `group`: items that one answer would close share a group `id`; `cheapest_item` names the item in that group that is cheapest to close.
- `access[]`: access assumptions (`kind: access`, `system`, `assumption`, `evidence_state: Assumed`) go here, never among the questions.
- `views`: `business_owner` and `builder`, each the list of item ids in reading order.
- `identity_runtime_opens[]`: the open questions about which service identity the agent writes as and where the agent runs. Write one entry for each such question the design's write grants and the `access[]` entries leave unanswered (for example, which service identity writes each proposed write, or which environment the agent runs in). Each entry carries `id` (`IR-1`, `IR-2`, ...), `question` (one plain sentence) and `adjudicator` (the role who can answer, never a person's name). Write `[]` only when every write grant already names its identity and the place the agent runs is settled.

Never fill a missing answer. An unknown stays an item: do not guess a value, a threshold, a person's decision or a source, and do not mark anything resolved because it seems likely.

## Answers already in the snapshot

Tell two cases apart for every question:

- **Absent from the snapshot.** No snapshot entry states the answer. The question is written as above; nothing is filled.
- **In the snapshot, not yet taken in.** A snapshot entry states the answer, or corrects what the question assumes, in so many words, and the earlier stages did not take it in. Apply it; do not leave the question as if nothing were known:
  - Keep the entry (its `raw_ids` still cover the raw items), and say in `resolution_evidence`, in plain words, what the snapshot records.
  - Cite the snapshot entries it rests on in `snapshot_refs` (their ids, as the snapshot writes them); the plain-words fields still carry no ids.
  - Record what was applied in `applied_from_snapshot`: `kind` (`answer` or `correction`) and `summary` (one plain sentence).
  - Keep what still needs a person: when the snapshot's answer is a business decision or rests on an assumption, the entry stays a ratification item (class Assumed pending ratification, kind ratification) and `unknown` asks for that confirmation, not for the answer again. Only an answer the snapshot states as settled fact closes the question; then `unknown` says what is left to confirm, if anything (when nothing is, say so in `unknown` in one plain sentence and add `"closes": true` to `applied_from_snapshot`: the entry is then closed, kept for its coverage and citations, and the page no longer lists it as an open question), and `agent_meanwhile` says the agent uses the recorded answer.
  - An answer you would have to infer, combine or round from several entries is not in the snapshot: the question stays absent-style, and `resolution_evidence` names the entries that come closest.

## Repair after the reader

The reader (`reader.md`) reads the register against the snapshot after you. When `<run>/stages/register/verdicts.json` holds `finding` verdicts:

1. For each finding that the snapshot answers or corrects a question, apply it as in "Answers already in the snapshot", citing the reader's `evidence` entries in `snapshot_refs`. For a finding that an item misstates what is unknown, rewrite the item in plain words. A finding you judge unsupported by the cited entries is left as it is, with one plain sentence saying why in `reader_repairs`.
2. List each finding you handled in `reader_repairs[]`: `verdict` (the reader's verdict id), `item` (the question's id) and `action` (`applied`, `rewritten` or `left`, with `reason`).
3. Then the reader reads the register once more and writes `<run>/stages/register/verdicts.json` again. There is no further round: what the second read still finds stays a recorded finding, and the run continues.

Write `who_can_answer` as a role (for example "accounts payable lead"), never a person's name or contact details. Name the role a customer would recognise; never an internal route, stage or tool.

## Finish

Run `/usr/bin/python3 skills/agent-design/scripts/register.py check --run <run>` after the reader has written its verdicts; fix what it names in `register.json` and run it again.
