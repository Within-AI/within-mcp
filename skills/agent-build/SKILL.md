---
name: agent-build
description: Build a verified agent from a ratified Agent Design (or person-ratified requirements) inside the target agreed at planning, light on the way in and strict on the way out. Use when someone asks to build the agent, build this design, turn the agent design into code, build an agent from the design, resume an agent build run, or check whether a built agent is done. It does not launch the agent into production; production integration on the customer's stack is handed over.
metadata:
  module: Agent Build (after within:agent-design)
  status: full flow for run 0482 (ten steps, settings to return)
---

# Agent Build

`within:agent-build` takes the output of `within:agent-design` (its run folder copied to `R/inputs/agent-design/`), or person-ratified requirements when there is no design, to a built agent package whose results are checked against frozen expected results inside the target agreed at planning. It is light on the way in: the briefing never refuses a design, whatever its shape, agent host or target. It is strict on the way out: `exit_gate.py` writes `done` or `not done` against the agreed target, and only `done` counts. The skill stops before production launch; production integration on the customer's stack is a hand-over item.

Paths below are relative to this skill's folder. Scripts run with `python3` (`python3 scripts/<name>.py ...`). The run folder `R` is laid out as in `references/run-folder.md`; every command, record and exit code is in `references/interfaces.md`. Exit codes: 0 pass, 1 a check failed, 2 usage or unreadable input, 3 needs a person. Missing information is never filled in: it becomes an open item, "needs more information" or a question for the person.

At a glance, the five checkpoints people ask about:

1. **Settings.** `python3 scripts/config.py validate --run R --phase settings`, then `python3 scripts/prefs.py read --path <preferences_path>` (step 1).
2. **Briefing.** `python3 scripts/briefing.py --run R` (step 2).
3. **Freeze.** `python3 scripts/freeze.py freeze --run R ...` (step 6).
4. **Early end-to-end.** `python3 scripts/e2e.py record --early --run R --source <agent source> --command ...` (step 7).
5. **Exit.** `python3 scripts/exit_gate.py --run R` (step 9).

At every stage boundary, show the person the status table: `python3 scripts/status.py table --run R --stage <step name>`.

## 1. Settings and carried-over preferences

Agree the engine (`anthropic` or `openai`; OpenAI is a preview offer, see `references/engines.md`), the build host and the preferences file with the person, and write `R/config.json`. Then run:

- `python3 scripts/config.py validate --run R --phase settings`. Exit 1 lists the rejection reasons: fix the config with the person. Open items in `R/records/config-settings.json` are raised with the person, not filled in.
- `python3 scripts/prefs.py read --path <preferences_path>`. Carried-over values are offered as defaults, never applied silently; withheld keys (a malformed line) stay withheld until a person fixes the line, and become open items.

When the settings validate, append the event: `python3 scripts/log.py append --run R --type settings.validated --payload '{}'`. Close the step with `python3 scripts/status.py table --run R --stage settings`.

## 2. Briefing

Run `python3 scripts/briefing.py --run R` (add `--read-env` to read the build host's tools). It writes `R/briefing.md` (layout in `templates/briefing.md`), `R/what-we-still-need-to-know.json` and `R/capabilities.json`. The briefing never refuses a design: it reads whatever shape arrives (single process or a set, any agent host, a skill package, a pure script, no design at all) and lists what is thin as questions, never as a reason to stop. Walk the person through `R/briefing.md`, starting with "First questions".

Append the event: `python3 scripts/log.py append --run R --type briefing.written --payload '{}'`. Close the step with `python3 scripts/status.py table --run R --stage briefing`.

## 3. Access ledger and real-data offers

For each system the design reads or writes, agree with the person what access exists today and offer the real-data options in `references/real-data-offers.md`. Record every target system in the access ledger:

- `python3 scripts/access.py record --run R --system <name> --state none|synthetic|extract|read-only|live --evidence <file>` for each target system; `python3 scripts/access.py assume --run R --system <name> --note <text>` notes an assumption, which never raises the state.
- `python3 scripts/access.py handover --run R` writes `R/handover.json`: production integration on the customer's stack, plus one run-when-access-arrives item per stubbed system or system without access.
- With an extract in `R/inputs/history/<system>.csv`: `python3 scripts/profile.py --run R --system <name> --file inputs/history/<name>.csv`, and, with past cases, `python3 scripts/replay.py --run R --cases <dir> --command ...` (no threshold; it informs the interview).

The access ledger runs here, before the early end-to-end record in step 7: the planning brief's `target` and the access ledger must agree. A system marked `real` needs an access entry with evidence (a state above `synthetic`); a system marked `stubbed` is never recorded `extract`, `read-only` or `live`. A disagreement is recorded on the early record as a failed check for the exit gate to read; it does not hold back round 2. Close the step with `python3 scripts/status.py table --run R --stage access`.

## 4. Planning interview

Run the interview in `templates/interview.md` with the person: the target (environment and, per system, `real` or `stubbed`), the agent host, the deliverable form, the rules that stop work, the numbers, the label sets and the building blocks (`references/blocks.md`). Each answer the person wants carried to later runs is saved with `python3 scripts/prefs.py append --path <preferences_path> --key <key> --value <json> --decided-by <name> --run-id <run id> --source interview --run R`.

Write the block choices into `R/config.json` and run `python3 scripts/config.py validate --run R --phase build`. Exit 1 lists the rejection reasons (an option for the other engine, a shared block chosen twice); an unknown option id is listed as an open item, and obvious spellings of engine, host and option ids are read and recorded; none of them is about the agent host or the target, which are agreed here, not checked against a list. When it validates, append the event: `python3 scripts/log.py append --run R --type build.validated --payload '{}'`. Close the step with `python3 scripts/status.py table --run R --stage interview`.

## 5. North-star brief

Write the brief from `templates/north-star-brief.md`: the prose `R/brief.md` and the machine fields `R/brief.json`, which carry the agreed `target`, `agent_host`, `open_items`, `version`, `split_version` and `split_path` (the split file written in step 7, for example `split.md`). Check the brief against its inputs with `python3 scripts/fidelity.py round --run R --brief brief.md --inputs <input dirs>` until a round is clean. A loosening keeps the design's stricter reading (the next fix session restores it) and is recorded for the end-of-run review; at the round cap the remaining findings become named open items for the end-of-run review and the step goes on. A person's ruling, when one is given, is recorded with `python3 scripts/fidelity.py ruling ...` and reaches the next fix session.

After the brief is written, run `python3 scripts/openitems.py build --run R`: every open item (hand-off gaps, briefing gaps, withheld preferences, brief-origin items, and brief loosenings or findings the fidelity loop left open, as `fidelity:<finding id>`) gets affected units, a safe behaviour (`stop` or `escalate`) and the test cases that must show it. Close the step with `python3 scripts/status.py table --run R --stage brief`.

## 6. Freeze

Seal the cases and the expected results, preferably written by someone other than the builder (the Agent Design suite, a fresh session or a person): `python3 scripts/freeze.py freeze --run R --cases <dir> --expected <dir> --author <name> --author-role <role>`. When no fresh or separate session is available, builder-authored expected results are accepted and recorded as builder-authored (`authored_by: builder`, with a note) for the end-of-run review; `openitems.py build` lists them under `review_notes`. A replaced expectation goes through `python3 scripts/freeze.py refreeze --run R --case <id> --expected <file> ...`, never an edit.

Right after sealing, run `python3 scripts/freeze.py verify --run R` and keep running it before every counted check; it runs again before the exit gate in step 9. Close the step with `python3 scripts/status.py table --run R --stage freeze`.

## 7. Split and unattended units

Write the split from `templates/split-plan.md` (one fenced json block of units with their `round` and `kind`, one unit of kind `end-to-end` in the first build round) and the unit specs from `templates/unit-spec.md`, then run `python3 scripts/e2e.py check-split --split split.md`.

Launch each unit of round 1 with its round number: `python3 scripts/launch.py unit --run R --uid <U> --repo <abs repo> --worktree <abs worktree> --branch <branch> --base <commit> --prompt <file> --round 1`. Watch the units with `python3 scripts/monitor.py --run R`. Check each finished unit with the gate, `tools/gate/gate check <abs unit spec> --worktree <abs worktree> --return <abs return> --log-root <abs gate logs>`, and review it with `python3 scripts/review.py --run R --uid <U> --path <abs worktree> --base <commit> --brief brief.md --round <n>` (preamble in `prompts/review-preamble.md`).

Substitute absolute paths in the gate command and shell-quote any path containing spaces. Use the instantiated unit spec and the return for that unit. The bundled gate is local-only; do not add `--level local`. Exit 0 means all required checks passed, exit 1 means a check failed, and exit 2 means unknown or invalid input. Read the JSON report and compare the return's commit and done-check exit with the fresh results; a passing verdict alone does not verify the claims. Keep gate logs outside the checked worktree, and unset `GATE_LOG_ROOT` if using `--log-root` (the environment variable takes precedence). This unit check does not replace the final readiness and exit checks in step 9.

When the first build round's end-to-end unit has landed, record the early end-to-end run: `python3 scripts/e2e.py record --early --run R --source <agent source folder> --command <run-this-case command>`. The early record comes before round 2 starts; the access ledger from step 3 must already agree with the target.

Once the early record is written, whatever its verdict, start round 2 (a probe whose applicability needs more information is recorded as not applicable with its reasoning, and the run continues): `python3 scripts/launch.py unit --run R --uid <U> ... --round 2`, and so on for later rounds, each launch passing its round number. Close the step with `python3 scripts/status.py table --run R --stage split`.

## 8. Integration

Merge the units, then run the repo's suite on the integrated commit: `python3 scripts/integrate.py suite --run R --repo <abs repo> --commit <commit> --command ...` and `python3 scripts/integrate.py check-evidence --repo <abs repo>`. Write `R/package.sha256` for the shipped package, then record the final end-to-end run: `python3 scripts/e2e.py record --final --run R --command <run-this-case command>`. Close the step with `python3 scripts/status.py table --run R --stage integration`.

## 9. Way out

Every counted check runs on the hashed package, after `python3 scripts/freeze.py verify --run R` passes:

- `python3 scripts/run_suite.py --run R --command ...` runs the frozen suite; `python3 scripts/compare.py --run R --label <label>` compares, and `python3 scripts/compare.py dispose ...` records a person's disposition.
- `python3 scripts/stoprules.py --run R --label <label>`, `python3 scripts/numbers.py --run R --label <label>` and `python3 scripts/core.py --run R --label <label> --source <dir>`.
- `python3 scripts/live.py record --run R --command ...` for the live run, then `python3 scripts/numbers.py --run R --label live` so every live number also comes from its matching code step, and `python3 scripts/core.py --run R --label live --source <dir>` so core also checks the live outputs (the exit gate re-derives numbers and core on the suite label and on `live`; a failure on either label fails the check), and `python3 scripts/cold.py record --run R --attestor <name> --evidence <file> --report <file>` for the light cold run.
- `python3 scripts/readiness.py scan --run R` for clearance. The scan lists each possible client name with where it occurs and the client each of those sources declares, and waits for your judgement (exit 1): clear it with a one-line reason, `python3 scripts/readiness.py adjudicate --run R --flag <id> --basis ordinary-wording|authored-heading|own-source [--source <path>] --reason "..."` (own-source names the source), or hand it to a person with `python3 scripts/readiness.py escalate --run R --flag <id> --reason "..."`. A name found in a source that declares another client, a company-shaped name found in no source of this run, and vocabulary, path or id flags go to a person (exit 3), who disposes them with `python3 scripts/readiness.py dispose ...`.
- `python3 scripts/openitems.py check --run R --records suite-<package12>` on the final suite label and `python3 scripts/openitems.py check --run R --records live` on the `live` label.

Then run `python3 scripts/freeze.py verify --run R` once more and `python3 scripts/exit_gate.py --run R`. The exit gate writes `done` or `not done` against the agreed target in `R/records/exit.json`; anything but `done` is reported as `not done` with its reasons. Close the step with `python3 scripts/status.py table --run R --stage way-out`.

## 10. Return

Show the final status with `python3 scripts/status.py table --run R --stage return` and list open residuals (`python3 scripts/status.py residual ...`). Write the return from `templates/return.md`: the exit verdict and its reasons, the hand-over list, the open items and the records behind each claim. The skill stops before production launch; the person takes the package from here.
