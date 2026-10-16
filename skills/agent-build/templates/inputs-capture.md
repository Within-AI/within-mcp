# Inputs capture: <process or set name>

The first step of planning: open-ended inputs, captured before any design or split. Dictated by <person> on <date>. The planning interview (`templates/interview.md`) fills the questions; this file holds the answers.

Legend (mark every line):
- **[S]** said: stated by the person who owns the run (quote or close paraphrase).
- **[I]** inferred: filled in by the agent, flagged for confirmation.
- **[O]** open: not yet answered; resolve it or carry it into "What we still need to know".

## Carried over from earlier runs
Values from the customer preferences file, each shown with its source and confirmed or changed by the person; never re-asked blank. A withheld key is an open item.
- [S] <key>: <value> (from run <run_id>, <source>, decided by <name> on <date>) — confirmed / changed to <value>
- [O] <withheld key>: malformed preferences line; actions that depend on it stop or escalate

## Objective
- [S] <what the agent must achieve, in the owner's words>
- [I] <any inference about scope, flagged for confirmation>

## Who uses it and who reviews it
- [S] <who uses the result, and who reviews the agent's work and how>

## What ships
- [S] <deliverable form: app, skill package, kit, spec for a platform team>

## Target
- [S] Environment: <name>
- [S] <system>: real / stubbed — <reason>

## Agent host
- [S] <host, and the tools it lets the agent inherit; read-only unless stated>

## Blocks
- [S] <block>: <option, or dropped with a reason, or added by a person>

## Must not move
Every path or system listed here feeds every unit's Must not edit list and the completion gate.
- [S] <path or system that must not change>

## Not this run
- [S] <idea or scope deliberately left out>

## References
- The briefing: `briefing.md` and `what-we-still-need-to-know.json`
- The Agent Design output: `inputs/agent-design/` (the hand-off `outputs/agent-build-inputs.json`)
- Work Brain notes: `inputs/work-brain/*.md`, cited by file name
- <other path or link> — <date> — <why it matters>

## Unknowns
- [O] <question still open, and who answers it>
