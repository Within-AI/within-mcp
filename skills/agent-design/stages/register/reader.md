# Register reader: check each question against the Work Brain snapshot

Read only inside this run's folder and the plugin; never list or open folders beside the run.

Time target: 5 minutes. This read shares the page-prep target in `config/targets.json`.

You are the snapshot reader for one Agent Design run. Work in this run folder (`<run>`) and do this read yourself, as a read separate from the writing; do not edit `register.json`.

## Read

- `<run>/stages/register/register.json`: every entry of `items[]` is a question. The `access[]` list is not a question; give it no verdict.
- `<run>/upstream/inputs/snapshot.json`: the frozen Work Brain snapshot. Read nothing newer.

## Judge each question

For every question, decide one verdict:

- `ok`: the snapshot does not already answer it (or the answer it holds is already applied, with its entries cited in `snapshot_refs`), and the question, the decision it bears on, who can answer and how it closes are stated plainly enough for a business owner, with no ids, field names or file names in them.
- `finding`: the snapshot already answers it or corrects it and the register has not applied that yet, or the item misstates what is unknown. Cite the snapshot entries in `evidence` (at least one).
- `cannot_judge`: the snapshot is silent or too thin to say either way.

Tell an answer that is absent from the snapshot apart from one that is in the snapshot but not yet taken in. Only the second is a finding about the snapshot. For it, set `family` to `answer-in-snapshot` (or `correction-in-snapshot` when the entry corrects what the question assumes), and give `known_value`: `target` (the question's item id), `value` (what the snapshot states, in plain words) and `source` (the snapshot entry id). Report only what an entry states in so many words; an answer you would have to infer is not in the snapshot.

Never fill a missing value in the register yourself: a verdict says how the question stands against the snapshot, and the writer applies your findings, with citations, in its repair step. After that repair you read the register once more and write `<run>/stages/register/verdicts.json` again.

## Write

Write `<run>/stages/register/verdicts.json` in the verdict format `skills/agent-design/formats/lists/verdict.schema.json`:

- `kind`: `register-read`.
- `read`: the run-folder-relative paths you read, for example `upstream/inputs/snapshot.json` and `stages/register/register.json`.
- `items[]`: one per question, each with `id` (your verdict id), `subject` (the question's item id), `verdict`, `evidence`, `detail` (one plain sentence) and `route_to` (`register`).

Every question gets exactly one verdict, matched by its item id in `subject`.
