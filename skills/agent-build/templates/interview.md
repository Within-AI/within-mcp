# Planning interview: <process or set name>

The planning session runs this interview after the briefing (`briefing.md`, `what-we-still-need-to-know.json`). Ask one topic at a time, mark each answer said, inferred or open (as in `inputs-capture.md`), and write each decision down when it is made. Nothing here refuses a design for its shape: a gap becomes an open item and the build goes on.

## Carried over from earlier runs

Ask this section before every question below. Read the customer preferences file with `prefs.py read --path P`.

| Key | Carried-over value | Source (run id, stop, decided by, date) | Confirm or change |
|---|---|---|---|
| <key> | <value> | <run_id>, <source>, <decided_by>, <date> | <confirmed / changed to ...> |

- Show each carried-over value with its source and ask the person to confirm or change it. Never re-ask a carried-over value blank.
- A changed value is appended as a new decision (see "Writing decisions down"); the old line stays.
A malformed preferences line is set aside and recorded; every valid preference is kept and the newest valid value per key wins. A key is withheld (raised as an open item while the interview continues) only when no valid line exists for it. `all_withheld` means the preferences file could not be read at all.

## The six late-learning questions

Past builds learned these late (findings 3 and 4.3). Ask each one explicitly.

1. **Who reviews the work and how.** Who looks at the agent's output, on what surface, and how often (findings 3, 4.3). Feeds `review_surface`.
2. **What ships.** The deliverable's form: an app, a skill package, a kit, a spec for a platform team (findings 3, 4.3). Feeds `deliverable_form`.
3. **Which steps need judgement and why.** Every other step is code (findings 3, 4.3).
4. **What the host may inherit.** Tools listed explicitly, read-only by default; a missing allow-list means no tools (findings 3, 4.3). Feeds `agent_host.tool_allow_list` and `agent_host.writes_allowed`.
5. **Whether test data needs a pinned clock.** Frozen cases that depend on today's date need a fixed clock (findings 3, 4.3).
6. **Where the single source of the rules lives.** One place for rules that appear in several files (findings 3, 4.3). Feeds `rule_source_of_truth`.

## The four first-question topics

The briefing hands these over as the first questions to ask (findings 4.1).

1. **Process and deliverable.** Which process or processes, and what they produce (findings 4.1).
2. **Target environment and packaging.** Where the agent runs and how it is packaged (findings 4.1).
3. **Which steps need judgement.** Confirm against the design's steps (findings 4.1).
4. **The review experience.** What the person reviewing sees and does (findings 4.1).

**The agent-host question.** Ask the hand-off's `platform_question` here and nowhere earlier: which host runs the agent and what it lets the agent do (findings 4.1). Feeds `agent_host {kind, name}`.

## Agree the target

Agree, per system, whether the build uses it for real or stubbed, and the environment it runs in. This fills the brief's `target` (P9: `target {environment, systems[{name, mode, reason}]}`).

Environment name: <environment>

| name | mode (`real` or `stubbed`) | reason |
|---|---|---|
| <system> | <real / stubbed> | <why> |

A system without access is `stubbed` with a reason; its checks become items that need more information, never a block.

## Offer the blocks

Walk through the blocks in `references/blocks.md` with the person. Each block is an offer: swappable for a listed alternative, droppable with a reason, or replaceable by a block the person adds (`added_by`, `reason`, `engine`). There is no default shape and no default execution layer; both are chosen here, with a reason, as part of the target. The activity log is always required; calculation in code is required when numbers exist.

## Writing decisions down

Every decision at a planning stop is appended to the preferences file:

```
prefs.py append --path P --key K --value JSON --decided-by NAME --run-id ID --source interview|target|blocks --run R
```

With `--run R` the append also logs `prefs.appended`. A decision changing scope gives the brief and the split a new version together (`version` and `split_version` move as one).
