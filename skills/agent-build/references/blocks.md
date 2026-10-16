# Building-block catalogue (offers, not defaults)

The machine-readable copy is `blocks.json` next to this file; the two say the same thing. The planning session offers these blocks; it chooses nothing on its own.

- **Suggested** marks the option the planning session offers first on an engine; **strongly recommended** marks a block the skill argues for but never requires.
- Every block can be swapped for a listed alternative, replaced by a block the person adds, or dropped with a reason.
- There is **no default shape and no default execution layer.** Both are decided at planning, with a reason, as part of the target.
- Two capabilities are required: **an always-on activity log** in every build (`activity_log`, `required: true`), and **calculation in code when the design produces numbers** (`calculation`, `required: false`, `required_when: "numbers exist"`). How each is implemented is still a choice.
- One engine end to end. Every option names its `engine` (`anthropic`, `openai` or `any`); the run config accepts only `any` or the run's engine. The reviewer is the only exception: the other engine's reviewer is allowed only when `cross_engine_review` is true.

## Engines

| Engine | Status | Evidence |
|---|---|---|
| `anthropic` | `offered` | |
| `openai` | `preview` | `status_evidence: null` |

The OpenAI engine ships as `preview`: the config refuses it without `allow_preview_engine: true`. If the OpenAI live re-run (L3) passes, its status becomes `offered`, with `status_evidence` naming the passing L3 record (a path under `live/l3/`); if L3 fails, it stays `preview` and Anthropic is offered.

## Catalogue

| Block id | Block | Option id | Option | Engine | Suggested on | Pin | Evidence status |
|---|---|---|---|---|---|---|---|
| `runtime` | Agent runtime | `claude-agent-sdk` | Claude Agent SDK | anthropic | anthropic | 0.2.152 | Anthropic settled (exp 3); OpenAI provisional (exp 5) |
| | | `mastra` | Mastra with @ai-sdk/openai | openai | openai | 1.75 with @ai-sdk/openai 4.x | |
| | | `openai-agents-sdk` | OpenAI Agents SDK (OpenAI only) | openai | | | |
| | | `plain-sdk` | Plain SDK calls with code around them | any | | | |
| | | `customer-platform` | The customer's platform (skill package) | any | | | |
| | | `none` | None (pure script) | any | | | |
| `execution_layer` | Execution layer (none suggested: chosen at planning as part of the target) | `sandbox-folder` | Sandbox folder or plain process | any | | | findings 1 (Where it runs); ruling 1 |
| | | `docker` | Docker | any | | | |
| | | `customer-platform` | Customer platform | any | | | |
| | | `customer-chat` | Inside the customer's own chat | any | | | |
| `model` | Model (pinned per build) | `claude-sonnet-5-5` | claude-sonnet-5-5 | anthropic | anthropic | | proposed |
| | | `gpt-6.1-sol` | gpt-6.1-sol | openai | openai | | |
| | | `other-same-engine` | Any model the same engine offers | any | | | |
| | | `none` | None | any | | | |
| `tools` | Tools and system reads | `mcp-tool-file` | MCP plus a tool-definition file the build owns, with a fake MCP server as a stub | any | anthropic, openai | | proposed; ruling 1 |
| | | `customer-connectors` | The customer's real connectors, MCPs or APIs where access allows | any | | | |
| | | `direct-api-client` | Direct API client | any | | | |
| | | `upload-export` | Upload or export | any | | | |
| `activity_log` | Activity log (required capability) | `jsonl-log` | Per-case JSON-lines log the agent's own code writes, separate from tracing | any | anthropic, openai | | settled (0464 scoping findings line 55; v0.2 catalogue) |
| | | `native-adapter` | Native format behind an adapter that writes the normalized JSON-lines log | any | | | |
| `calculation` | Calculation (required capability when numbers exist) | `code-function` | Deterministic code function per number, recorded in code_steps | any | anthropic, openai | | settled (shared core; criterion 11) |
| | | `other-code` | Any code implementation that records code_steps | any | | | |
| `persistence` | Persistence | `sqlite` | SQLite | any | anthropic, openai | | proposed |
| | | `file-store` | File store | any | | | |
| | | `customer-store` | The customer platform's store | any | | | |
| | | `none` | None | any | | | |
| `state` | State | `run-record` | Run record the agent's own code writes | any | anthropic, openai | | proposed |
| | | `runtime-sessions` | Runtime sessions | any | | | |
| | | `none` | None | any | | | |
| `tracing` | Tracing (strongly recommended, never a gate) | `phoenix` | Phoenix client, failing loud when switched on | any | anthropic, openai | 0.17.2 | settled pin (exp 3); ruling 8 |
| | | `runtime-own` | The runtime's own tracing | any | openai | | |
| | | `otlp` | Any stack the customer uses (OTLP to their backend, an OTLP file, others) | any | | | |
| | | `none` | None, with a reason | any | | | |
| `scripted_evals` | Scripted evals | `plain-runner` | Plain runner over the frozen cases through run-this-case | any | anthropic, openai | | proposed |
| | | `runtime-scorers` | The runtime's own scorers behind the same runner | any | | | |
| `judge` | Judge (advisory only) | `claude-sonnet-5-5` | claude-sonnet-5-5 | anthropic | anthropic | | settled models (exp 4); ruling 2 |
| | | `gpt-5.6-luna` | gpt-5.6-luna | openai | openai | | |
| | | `none` | None | any | | | |
| `launcher` | Launcher | `claude-p` | claude -p per unit | anthropic | anthropic | | settled (exp 2) |
| | | `codex-exec` | codex exec per unit, with a writable temp folder and git paths | openai | openai | | |
| | | `codex-native-workers` | Native Codex workers (not recommended) | openai | | | |
| `reviewer` | Reviewer | `claude-fresh` | Fresh Claude session, read-only | anthropic | anthropic | | settled (ruling 12 Oct); the other engine's reviewer only under cross_engine_review |
| | | `codex-fresh` | Fresh Codex session, read-only | openai | openai | | |

Tracing is never a gate, and no code in this skill hard-wires Phoenix: when Phoenix is switched on and fails to start, the agent fails loud; when it is off, nothing imports it. A judge's verdict is advisory on every engine and never sets a case state.

## Options a person adds

A person may replace any block's option with one of their own. In `config.json` the block's choice is then written as:

```json
{"choice": "<id>", "added_by": "<name>", "engine": "anthropic|openai|any", "reason": "<text>"}
```

- The choice **validates when `added_by` and `reason` are present and the engine fits** the run's engine (`any`, or the run's own engine).
- **An id outside the catalogue without `added_by` is rejected** (the config check exits 1).
- An added option is recorded in the preferences file like any other planning decision (`source: blocks`).
