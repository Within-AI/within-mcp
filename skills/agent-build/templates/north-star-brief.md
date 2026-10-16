# North star brief: <agent name>

Brief version: <n>. Written at planning by `within:agent-build` from the Agent Design hand-off and the planning conversation. The person who owns the build ratifies it before any unit starts.

## Objective
<One paragraph: the agent being built, for which process or processes, and what "done" means for it.>

## Judged by
<Who decides the build is done (the person who owns it) and on what: the exit checks in the Acceptance key below.>

## Deadline, budget, landing
- Deadline: <date and time>
- Budget: <model spend ceiling and time box>
- Landing: <where the built agent lands: branch, package, folder>

## Must not move
- <Each fixed point: inputs, formats, decisions already ratified.>

## Not this run
- <Each thing deliberately left out.> The skill stops before production launch.

## Environment and permissions
<Where units run, what they may write, which tools they may use. Units never write counted records.>

## Target environment
Environment name: <name, e.g. customer sandbox>

| System | Mode | Reason |
|---|---|---|
| <system name> | `real` or `stubbed` | <why: access granted, access pending, out of scope> |

Every system the agent touches has one row. A `stubbed` row lands on the hand-over list in the return.

## Rules
| id | stops_work | source |
|---|---|---|
| <rule id> | <`true`/`false`, confirmed by the person> | <document, section or decision the rule comes from> |

`stops_work` is set only when the person confirms it. Every rule that stops work gets a fire test and a no-fire test.

## Numbers ledger
| name | computed_by | test case ids |
|---|---|---|
| <number name> | <command or module and function in the agent package> | <case ids that check it> |

Every number the agent reports is computed by code, never by a model. If the agent reports no numbers, the person confirms that (`no_numbers_confirmed`).

## Agent host and tool allow-list
- Host: <kind and name, e.g. code runtime, Claude Agent SDK, skill package>
- Tool allow-list: <each tool the agent may call>
- Writes allowed: <yes/no, and where>

A missing allow-list means read-only with no tools, never everything the host offers.

## Deliverable form
<What the build hands over: a package, a skill, a script, a service.>

## Review surface
<Where people review the agent's output: a queue, a sheet, a pull request, an inbox.>

## Single source of the rules
<The one place the rules live; the agent reads them from there and nowhere else.>

## Open items
| id | affected units | affected processes | safe path | test case ids |
|---|---|---|---|---|
| <open item id> | <unit ids or `*`> | <process ids or `*`> | `stop` or `escalate` | <case ids> |

Each open item carries its safe path: the agent stops or escalates until the item is closed. Missing information is never filled in.

## Offers
- Real-data offer: <run the frozen cases on real, cleared data when the customer offers it>
- Real-connector offer: <swap a stubbed system for its real connector when access arrives>
- Shadow-mode offer: <run the agent beside people on live work, output reviewed, nothing acted on>

## Input design carried forward
<The Agent Design hand-off: processes, blocks chosen, test suite, decisions. Cite each source file.>

## Acceptance key
Standing critical criteria (every build):
- C1. Fidelity to the evidence: the agent does what the cited evidence says, nothing more.
- C2. Safeguards enforced in the mechanism: every safeguard is enforced in code or configuration, not only in a prompt.

Exit checks, standing criteria (every build):
- X1. Frozen expected results the builder cannot edit.
- X2. An early end-to-end run on the agreed target.
- X3. Fire and no-fire tests for every rule that stops work.
- X4. Every number computed by code.
- X5. At least one live run with real model calls where the design uses a model.
- X6. The light cold run.
- X7. Client clearance.

Build-specific criteria:
- <n>. <criterion a stranger could check as true or false>

## Known exemptions
| check | alternative | ratified by |
|---|---|---|
| <exit check> | <what is done instead> | <person, date> |

## Key versioning
A scope change gives the brief and the split a new version together: `version` and `split_version` stay equal.

## Machine fields
The fenced block below is written to `R/brief.json`. Keys and defaults are fixed; fill the values at planning.

```json
{
  "target": {
    "environment": "<environment name>",
    "systems": [
      {"name": "<system name>", "mode": "stubbed", "reason": "<why>"}
    ]
  },
  "rules": [
    {"id": "<rule id>", "stops_work": false, "source": "<source>"}
  ],
  "numbers": [
    {"name": "<number name>", "computed_by": {"module": "agent/numbers.py", "function": "<name>"}, "test_case_ids": []}
  ],
  "agent_host": {
    "kind": "<host kind>",
    "name": "<host name>",
    "tool_allow_list": [],
    "writes_allowed": false
  },
  "deliverable_form": "<deliverable form>",
  "review_surface": "<review surface>",
  "rule_source_of_truth": "<single source of the rules>",
  "no_numbers_confirmed": false,
  "all_critical_confirmed": false,
  "non_critical_floor": null,
  "label_sets": [],
  "rule_literals": [],
  "exemptions": [],
  "open_items": [
    {"id": "<open item id>", "affected_units": ["*"], "affected_processes": ["*"], "safe_behaviour": "escalate", "test_case_ids": []}
  ],
  "client_name": "Northwind Supply",
  "amount_tolerance": 0,
  "capabilities": {},
  "processes": ["<process id>"],
  "version": 1,
  "split_version": 1,
  "split_path": "split-plan.md"
}
```

`split_path` is relative to the run folder; the exit gate reads the split's fenced JSON block for round boundaries. `amount_tolerance` and `capabilities` are optional.
