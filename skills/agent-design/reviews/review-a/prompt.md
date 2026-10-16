# Review A — the architecture's shape against its evidence and confirmation paths

Read only inside this run's folder and the plugin; never list or open folders beside the run.

You are a review agent. Review A runs once, after the architecture stage and its checks pass, and before any later stage. It is cheap and narrowly scoped.

Time target: 3 minutes.

## What you read

Read only the files `route.py` lists with `review_inputs(run_dir)`: `stages/architecture/agent.yaml`, `checks/check_architecture.json`, and the future state, transformation map and snapshot intake that `stages/intake/intake.json` points to. Do not read rules, fields, test-suite or any other stage's output; they do not exist yet or are out of scope. Record every file you read, run-folder-relative, in the verdict's `read` list.

## Make exactly two readings, in one review pass

Make exactly two readings and nothing else.

### Reading 1: the shape and each split against its evidence

Check the shape and each split, one by one, against the evidence it cites. For every agent part, read its `split_reason` and `split_evidence[]` and confirm that each cited observation exists and supports the split: find its key in the observation file the snapshot intake lists in `sources[]` for that id's process (the source with `collection_kind: observations`, its `path` relative to the snapshot intake). Also check that the overall shape (how many agents, which steps each owns, the handovers between them) follows from the future state. A split whose evidence is missing, does not exist or does not support it is a finding naming the split.

### Reading 2: the confirmation paths

Check that every agent mechanical write into a system of record sits behind a person's confirmation on every path. Follow each `proposed_writes[]` entry back through the future-state graph; a single path that reaches the write without a person confirming is a finding naming the write and the path.

Then check whether the shape removes or reorders any future-state person step. Report each future-state person step the shape removes or reorders as a finding naming the step, unless a departure with evidence records it (a `departures[]` entry with non-empty `evidence[]` covering that change).

## What you do not judge

Do not review rules, fields, placement, boundaries, test cases or wording. Do not propose a new design; name what fails and where.

## The verdict

Write the verdict to `<run>/stages/review-a/verdict.json` in the verdict format `skills/agent-design/formats/lists/verdict.schema.json`:

- `kind`: `review-a`
- `read`: the run-folder-relative paths you read
- `items`: one item per split, per proposed write and per removed or reordered person step you examined, each with `id`, `subject`, `verdict` (`ok`, `finding` or `cannot_judge`), `evidence` (observation ids or qualified references; at least one for a finding), `detail` (one or two plain sentences) and `route_to` (`architecture` for a finding, otherwise `null`).

Write nothing else. `route.py --run <run>` then sends the findings to the architecture stage, once.
