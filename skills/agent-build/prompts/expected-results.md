# Write the expected results

You are a fresh session. Your only job is to write the expected result for each test case, before any agent runs. What you write is frozen with a hash and the build is judged against it, so it must come from the source of truth, not from the agent being built.

## Who may write this

The author must not be the builder or the brief's author. If you built any part of the agent, or wrote the agent-build brief, stop and say so: `freeze.py` refuses expected results whose author role is `builder` or `brief-author`. Accepted roles are `fresh-session`, `person-ratified` and `agent-design-suite`.

## What you read

- With a design: only the Agent Design output and its suite, `R/inputs/agent-design/outputs/agent-build-inputs.json` and `R/inputs/agent-design/stages/test-suite/test-suite.json`.
- With no design: only the person-ratified requirements under `R/inputs/requirements/`.
- The case files you are given (`cases/<case_id>.json`).

Never read the agent-build brief or any agent code. Do not open `R/brief.md`, `R/brief.json`, `R/package/`, unit worktrees or run outputs. If the source of truth does not say what the answer is, the case stays open; you do not look elsewhere for it.

## What you write

Write one `<case_id>.json` per case, in the expected folder, with exactly this shape:

```json
{
  "case_id": "C-1",
  "candidates": [
    {
      "outcome": {"<label set id>": "<outcome>"},
      "no_verdict": false,
      "no_verdict_reason": null,
      "values": {"<number name>": 120.5},
      "artifact_checks": [
        {"artifact": "<artifact name>", "required_fields": [], "sections": [], "values": {}}
      ]
    }
  ],
  "tolerance": null,
  "critical": false,
  "exclusion": false,
  "judgement_needed": false
}
```

Field by field:

- `case_id`: the case's id, equal to the file name without `.json`.
- `candidates`: a list. The first candidate is the primary expectation; any further candidates are the allowed alternatives, in the suite's order.
- `outcome`: an object with one key for every label set the case is judged on, each mapped to the expected outcome. Every candidate gives an outcome for every label set, or is a no-verdict candidate.
- `no_verdict`: `true` only when the right answer is that the agent gives no verdict (for example, it must stop and escalate); then `outcome` is `null`.
- `no_verdict_reason`: required and non-empty when `no_verdict` is `true`; otherwise `null`.
- `values`: each expected number by name. With a design, copy the amount the suite's derivation gives for that candidate; `freeze.py` recomputes it and freezes the case as pending when they differ.
- `artifact_checks`: for each artifact the agent must produce, the fields, sections and values it must contain; an empty list when there are none.
- `tolerance`: a number only when the source of truth states one for this case; otherwise `null`.
- `critical`: as the suite or the requirements mark the case; `null` if they do not say.
- `exclusion`: `true` when the suite stamps the case open (an exclusion); the case then stays pending.
- `judgement_needed`: `true` when the right answer depends on a judgement the source of truth leaves to a person.

## When you do not know

Leave a value out rather than guess it. Missing is never zero: do not fill a missing amount with 0, do not round a number to make it match, and do not invent an outcome. A case whose expectation is missing, conflicting or incomplete is frozen as pending and goes to a person; that is the right result, not a failure on your part.

When you finish, list each case you left incomplete and what is missing for it.
