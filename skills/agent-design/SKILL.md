---
name: agent-design
description: Design the agent from a finished Future State Design run. Use when someone says "design the agent", "agent design from the future state", "turn the future state into an agent design", or asks to resume an agent design run. It reads the future state, transformation map and frozen snapshot, and produces the agent's architecture, boundaries, rules, placement, fields, test suite, simulation, register of open items and a shareable page. It does not build or deploy the agent.
metadata:
  module: Agent Design (third of Agent Recommendations, Future State Design, Agent Design, Agent Build)
  status: skeleton from run 0453; stage prompts land with their own units
---

# Agent Design

Takes a finished Future State Design run folder to an agent design shaped like the agents we actually built. Scripts do the record keeping; the agent and review stages are written from their stage prompts.

**Every stage runs here, one after another, in the session that invoked this skill**, the same way on every host (Claude Code, Cowork, Codex). For an agent or review stage, this session reads the stage's prompt, does exactly what it says, writes the files it names and goes back to `ad.py`; then it takes the next stage. The stage files, the checks and the repair records are the same as ever. Read each prompt fresh and work only from the files it names, not from what you remember of earlier stages. No step needs the host to start another session or agent; none of this skill's work is handed to one.

**Two kinds of agent, kept apart.** The *designed agent parts* (the orchestration layer and its sub-agents) are what this skill designs: they live inside the agent being designed, in `RUN/stages/architecture/agent.yaml` and the files after it. *This session* is what runs the skill. A stage prompt that talks about sub-agents or dispatch between parts means the designed agent parts, never this session.

The run's record is `state.json` and `events.jsonl`, both written only by `scripts/ad.py`. Paths below are relative to the repository root unless they start with `RUN/`. Run every script with `python3`. **What the host must offer:** `python3`. Without it, say so plainly and stop.

## 1. Set up the run

1. Propose, rather than ask for, the two folders: FSD_RUN is the newest finished Future State Design run in the workspace folder (every stage done), and RUN is a new folder `agent-design-<date>` beside it (`agent-design-<date>-2`, `-3` and so on when that name exists and is not empty). Name both in plain words and let the person confirm or pick others. Ask whether there is a freeze pins baseline file.
2. Run `skills/agent-design/scripts/ad.py init --run RUN --from FSD_RUN [--baseline PINS.json]`. It copies FSD_RUN into `RUN/upstream/` (older runs may hold `outputs/agent-design-inputs.json`; it is never read or copied) and records the freeze pins; it records unfinished upstream stages and carries their readable output forward, listing uncertainty in What we still need to know. Only input that cannot be read at all stops. Without a baseline, `ad.py status` says `freeze pins not compared with a baseline`; tell the person.
3. Tell the person their page exists: `init` prints one line starting `Your page` with the page's path, `RUN/outputs/agent-design.html`. Give them that line in chat. Where the host can, the page opens in the browser by itself; where it cannot (Cowork, a sandboxed host), the path is how they open it. The page fills in as each stage finishes and reloads itself while the run goes.

## 2. Run the stages

Read only inside this run's folder and the plugin; never list or open folders beside the run.

Loop until `ad.py next --run RUN` prints `done`. Where `ad.py` says to dispatch an agent (a stage, repair, fix or fallback agent), carry out that prompt here in this session.

1. `ad.py next --run RUN` prints the next stage KEY. Outputs of a finished stage, or the `upstream/` copy, that changed are recorded, never a stop: every finished stage from the earliest change on becomes `stale` (all of them for an `upstream/` change) and runs again in order. Only the earliest changed stage re-finishes on its own changed outputs (none when `upstream/` changed): `start` prints `refinish: …`; run `finish` without rerunning the prompt. Every later stage reruns normally, even one that changed too. `start`, with or without `--rerun`, records changes the same way first. Exit 3 means `state.json` itself changed outside `ad.py`: show the message.
2. Read the stage's entry in `skills/agent-design/stages/index.json`.
3. `ad.py start --run RUN --stage KEY`. For an agent or review stage it writes and prints `RUN/prompts/KEY.md` (the set instruction first when the run covers several processes, and the line `Time target: N minutes.` last).
4. Run every `command` line through `ad.py exec --run RUN --stage KEY --line N` (N counts the entry's lines from 1), never directly: `exec` runs the line and records its exit for this revision. Script stage: `exec` each line in order (or `ad.py exec --run RUN --stage KEY` for all of them). Agent or review stage: `exec` the first `before_dispatch` lines (0 when the entry has none), then carry out `RUN/prompts/KEY.md` here in this session, then `exec` the remaining lines (the entry's `note` says which run when).
   - review-a: after the review writes its verdict, `exec` line 1 (`route.py`).
   - review-b: `exec` line 1 (`fix_round.py plan`), then line 2 (`apply-known`); carry out each `RUN/stages/review-b/fix/<stage>.md` in turn, one at a time; then `exec` line 3 (`settle`).
   - register: `exec` line 1 (`register.py collect`), carry out the writer's prompt, then the reader's (`skills/agent-design/stages/register/reader.md`) as a separate read; when the reader reports findings, carry out the repair step in the writer's prompt (the writer applies them, then the reader reads once more); then `exec` line 2 (`register.py views`).
   - A long stage: between steps, about every 10 minutes of work, run `ad.py status --run RUN --if-changed`; post the block it prints for the person, if it prints one.
5. `ad.py finish --run RUN --stage KEY`. It runs the stage's checks, calls the status table and the page hooks, and only then records the stage. Exit 0: the stage is done (or done with recorded problems); go on. Exit 1: one repair or one retry was asked for, or the stage failed (see section 3). Exit 3 after a check could not run: carry out its fallback (section 3); any other exit 3 means `state.json` changed outside `ad.py` (step 1). A `start` exit 1 names an earlier stage that must rerun first; start that one. Exit 2 with `ceiling reached: KEY`: the run has used its time; post the person's status and one plain sentence saying the run stopped at its time limit (the stages not done are listed for builders in `RUN/REMAINING.md`; do not show that file to the person). Any other exit 2: tell the person in one plain sentence and stop.
   - A stage with a `command` is judged only on exits recorded for its current revision: a non-zero exit is recorded as a problem, never as plain done, and any line with no recorded exit is run by `finish` itself (and recorded) before it judges the stage.

## 3. Checks and their agent fallbacks

`finish` runs every check by script and records `pass`, `fail` or `unchecked`; it never runs a fallback itself. Upstream write-grant problems (found on the Future State Design files alone by the plugin's shared grant rules) never fail a check: the intake lists them, the stage prompts hand them to the stage agents, the architecture stage records the reading it took in agent.yaml's `upstream_readings`, and `check_grants` reports each as recorded or unresolved. Show the person the unresolved ones from the status table; they are in What we still need to know, and the run continues.

A stage finishes in one of three states. **Done**: every required output (the entry's `required_outputs`) is present and readable, and every check passed. **Done with recorded problems**: the required outputs are present, but a check still failed after the stage's one repair, a fallback agent judged a check, a command exited non-zero, or the page hook raised; each problem is written to the builder log `RUN/builder/problems.jsonl`, which the register reads, and `next` moves on. **Failed**: a required output is missing or unreadable after one automatic retry. Only a missing or unreadable required output stops a stage; a failing or broken check never does.

- A `fail`: `finish` asks for one repair. Carry out one repair of that stage here, working from the failing report, then run `finish` again (no `start --rerun`); the second result decides the state. When the repair agent judges the cause sits in an earlier stage, it writes `RUN/repairs/KEY.json` with `{"owner_stage": "<earlier stage>", "reason": "<one line>"}` instead of forcing a fix; its reading wins over any owner the findings name, `finish` records it, and `next` reruns that stage and the stages after it. The repair budget is per run: once spent, a remaining finding is recorded as a named draft gap in What we still need to know and the run continues to its hand-over. Never ask the person which stage to repair.
- Missing outputs: for a script stage with a `command`, `finish` reruns the command lines once by itself, then reruns the checks on what they wrote. For an agent or review stage it reruns any lines that run before the dispatch, then asks you to carry out the stage's prompt once more and `exec` the lines after it; then run `finish` again (it runs any line you did not). That is the stage's one automatic retry.
- A crashed or wrong check (`unchecked`, or a check you judge wrong) is handled by the fallback named beside it, carried out here in this session. It states why the check is wrong or crashed and applies the check's intent by hand to the stage output, writing a JSON report: `check`, `revision` (the stage's attempt number), `why`, and `applied`, a list of `{item, verdict (pass, fail or open), reason}` for the items the check would judge. Record it with `ad.py finish --run RUN --stage KEY --fallback-report FILE` (repeat the flag for several checks). A report that only explains, with no applied verdicts, is refused and the stage stays not done. An accepted report is kept as `RUN/checks/<name>.fallback.json`, its sha256 goes in the stage-end event, the check goes to `RUN/builder/check-defects.jsonl`, and the run continues. Never edit the plugin's files mid-run, and never ask the customer to approve the fallback.

| Stage | Check path | Fallback (carried out in this session) |
|---|---|---|
| check-architecture | `skills/agent-design/checks/check_architecture.py` | an architecture check agent reads `stages/architecture/agent.yaml` against the check's rules |
| check-architecture | `skills/agent-design/checks/check_grants.py` | a grants check agent compares the proposed writes with the upstream Write grants table |
| boundaries | `skills/agent-design/checks/check_boundaries.py` | a boundaries check agent derives the boundaries from the future state and compares them |
| rules | `skills/agent-design/checks/check_rules.py` | a rules check agent validates every rule against the rule format and the declared tokens |
| rules | `skills/agent-design/checks/check_outcomes.py` | an outcomes check agent looks for gaps and overlaps between the rules and the outcomes |
| placement | `skills/agent-design/checks/check_placement.py` | a placement check agent confirms every rule is placed and every stand-in declared |
| supply-check | `skills/agent-design/checks/supply_check.py` | a supply check agent confirms every token a rule reads is supplied |
| supply-check | `skills/agent-design/checks/check_rules.py` (`--families`) | a families check agent lists the rule families the fields imply and the rules omit |
| test-suite | `skills/agent-design/checks/check_cases.py` | a cases check agent validates the test suite against its formats and the rules |
| simulation | `skills/agent-design/simulation/reads/check_read.py` | a read check agent compares the read's verdict with the walk result and the expected values |
| simulation | `skills/agent-design/simulation/reads/screen_judgments.py` | a screening agent reads each frozen judgment answer for invented values |
| coherence-gate | `skills/agent-design/checks/check_coherence_gate.py` | a coherence check agent reads the gate's report, or runs each stage check by hand, and lists every failing or unchecked item |
| register | `skills/agent-design/scripts/register.py` (`check`) | a register check agent confirms every open item reached the register with its source |
| page-prep | `skills/agent-design/checks/plain_text.py` | a plain-language check agent reads the page text for banned words, raw ids and jargon |
| post-run | `skills/agent-design/scripts/postrun.py` | a post-run check agent lists banned words, uncited Observed claims, ids and paths as builder-log notes; it never blocks and never asks |
| post-run | `skills/agent-design/page/rules/check_rules_page.py` (`--page agent-design.html`) | a rules page check agent compares the rules on the page with the rules file |

## 4. What the person sees

At every stage boundary (after `init`, after each `finish`, and when `next` prints `done`) `ad.py` prints the person's status between the lines `---- status for the person ----` and `---- end of status ----`: at most 10 plain rows, the wall-clock minutes since the run started, one note line, what any notes are in plain words, how many items carried in from Future State Design are still open, a line when a stage is running past its usual time, and the page line. Post only that block in chat, unchanged, at each boundary; when `ad.py` prints `No change since the last update; nothing to post.` instead, post nothing. During a long stage, the `ad.py status` you run between steps (section 2) is the only other post. A retry updates its row rather than adding a new post. Everything else `ad.py` prints (check names, attempts, counters, recovery steps, problems) is for this session and the builder files, never for chat.

`ad.py status --run RUN` prints the same view on demand and records a watch in `events.jsonl`; `ad.py status --run RUN --builder` prints the builder view (minutes against each stage's time target, check counts and item counts). To pick up a run after a break, run `ad.py resume --run RUN`: it prints the stage table, the next stage and the prompt of any stage still running.

## 5. Finish

When `next` prints `done`, give the person the page `outputs/agent-design.html` (now final, no longer reloading) and the shareable files in `outputs/`, and the open items in the register. The page always publishes: anything the page checks left over is one quiet note on the page and an entry in the builder log, never a stop. The design is a proposal for people to ratify; nothing is built.

In Cowork, when copying back (see Cowork notes), the person's page is `outputs/agent-design.html`, so copying `outputs/` carries it; the run root keeps a second copy, `agent-design.html`, for older readers, and it need not be copied. Copy, never move.

## Cowork notes

These notes are the same in Agent Recommendations, Future State Design and Agent Design.

- **Copy back.** After `init`, after each stage and when the run ends, copy the run's `outputs/` folder, which holds the page and the files meant for sharing, to the folder the person connected. Copy, never move: the run folder stays as it is.
- **Deliver the page.** Point the person to the page in the connected folder by its file name, and say in one sentence what it shows.
- **No connected folder.** Tell the person where the files are and carry on; never stop a run for it.
- **What the person sees.** Plain stage names, wall-clock minutes, one note line, what any notes are, and the page line (the page's path is the only path they see). Check names, ids, exit codes, counters and plugin paths stay in the builder files.
- **Claude Code.** The same steps run in Claude Code; there, the connected folder is the folder the person opened.

