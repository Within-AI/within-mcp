---
name: future-state-design
description: Design the future state of one process or a set of related processes from a frozen Work Brain Snapshot — Current state Workflow, then a transformation map (how the work is reshaped and what an agent takes on), then a Future state Workflow — optionally aimed by a recommendation the person chose in Agent Recommendations. Use when someone asks to design the future state, map how a process should work with an agent, turn a recommendation or design prompt into a future-state workflow, or resume a future state design run. It does not design the agent's internals or build anything.
metadata:
  module: Future State Design (second of Agent Recommendations, Future State Design, Agent Design, Agent Build)
  status: prototype from handoff 0423, copied from Fast Mode's first three stages at dev 61f6a7d
---

# Future State Design

Takes a frozen Work Brain Snapshot to three reviewable outputs, each made by one stage that this session runs, one after another:

1. **Current state Workflow** (`current-state`): today's work as the evidence shows it.
2. **Transformation map** (`transformation-plan`): how the work is reshaped, which steps an agent takes, what stays with people, and the agent's write grants.
3. **Future state Workflow** (`future-state`): the work redrawn with the agent in place.

When the snapshot holds two or more processes and the direction carries no set map covering every snapshot process (a map scoped to the recommendation's processes does not count), a **set map** stage (`set-map`) runs first: it writes `stages/set-map/set-map.json` (format v0.1), saying how the processes connect. When its checks pass, `scripts/fsd.py` writes the run's one set map, `inputs/set-map.json`, and records its hash in `state.json`; a set map that comes with the direction and covers the whole snapshot is written there at `direction` instead. Every later stage reads only `inputs/set-map.json`. Stage order: `set-map` (when it runs) → `current-state` → `transformation-plan` → `future-state` → `ambition-audit` (every run). The ambition audit attacks the first design and lists proposals to make it more ambitious; when it lists any, `scripts/fsd.py` keeps the first pass under `stages/pass-1/` and runs `transformation-plan`, `future-state` and the audit once more with the proposals as input. The audit is internal: the person sees the design as in progress until the final map and future state land, and never sees the audit.

**One lane in every future state.** Each Future state Workflow has exactly one AI and agents lane, single-process and multi-process alike: one per run, never one per process or per agent. When there are several agents, each agent node carries the `agent` tag that tells them apart. A future state with an `Agent: <name>` lane, or a second agent lane, fails its checks.

If the person brings a recommendation from Agent Recommendations, the design aims at it, and the transformation map records how it carried each item. The run stops at the Future state Workflow. Agent Design is the next module.

**Every stage runs in this session, one after another, on every host** (Claude Code, Cowork, Codex). For each stage, read its prompt file and do what it says, working only from the files it names in the run folder, as if this conversation held nothing else; then hand back to `scripts/fsd.py finish`. Everything the stages know comes from the run folder. The stage files, the checks and the repair records are the same as they always were. Agents named inside a stage's output (for example the agent groups in the transformation map) are parts of the design, not sessions to start. The run's record is `state.json` (where each stage stands) and `events.jsonl` (what happened, in order), both written only by `scripts/fsd.py`.

**What the host must offer:** `python3`. Host notes (`references/host-notes.md`): in Cowork, copy the inputs into the cloud workspace, and bring the run folder back with a checksum check; in the Codex desktop app, open the skill's folder as the project. Kept open: code by default versus agent fallback.

Paths below are relative to this skill's folder. Run every script with `python3`.

## 1. Set up the run (ask one thing at a time; confirm each answer)

1. **Resuming?** If the person names an existing run folder, go to section 3.
2. **The snapshot.** Ask for the frozen snapshot folder (it holds `snapshot.json`). If they have none, make one: with the Within MCP, save the process details (seven explicit scopes) and observations (`verbosity="full"`, `limit=100`, `offset=0`) for the process key, byte for byte, then run `scripts/make_intake.py --details <file> --observations <file> --process-id <key> --out <snapshot folder>` (several processes: `--process <key> <details> <observations>` once per process). Processes are named by resource key, never by name alone; when a name matches several processes, list them with keys and ask.
3. **A recommendation?** Ask whether this design follows a recommendation from Agent Recommendations. Accept any of:
   - a refined report (`report-refined-<id>.json` in the recommendation run folder) and, optionally, which solution (default: the report's Our recommendation);
   - a Stage 1 report (`report.json`) and the solution id;
   - the design prompt text the person copied from the report: save it verbatim to a file in the run's parent folder, never inside the run folder.
   If the person pasted a design prompt that names a recommendation run folder holding the refined report, prefer the report file.
4. **Where the run lands.** Propose `<workspace>/runs/fsd-<label>` outside any plugin folder, and confirm the exact path. It must be absent or empty.
5. Run `scripts/fsd.py init --run <run> --snapshot <snapshot folder>`, then, with a recommendation, `scripts/fsd.py direction --run <run> --report <file> [--solution <id>]` or `--design-prompt <file>`. The evidence file alone works too (`--evidence <file>`); add `--folder <folder>` for the folder the person's files are in. The report is looked up by its file name in that folder, and a path in the prompt is only where it was written, never resolved; when the report isn't there (or its sha256 differs), the brief pasted in the prompt is used, then the evidence file's report, then its design brief. `direction` records which in `state.json` and `events.jsonl` (`report_source`) and prints one plain line naming the source: post that line to the person unchanged. It stops only when nothing is readable. When no solution is named, it takes the report's own recommendation, else the only or first-ranked solution, and the plain line names it so the person can redirect. A solution not marked ready is recorded under What we still need to know and the run continues; tell the person in their words. Say how many direction items were recorded and whether its processes match the snapshot's. `init` also pins the snapshot's process versions and cutoff (section 5); a missing, duplicate or disagreeing pin or cutoff is normalised and recorded; when cutoffs disagree the earliest is used and the person is asked which is right.

## 2. Run the stages

Read only inside this run's folder and the plugin; never list or open folders beside the run.

Repeat until `scripts/fsd.py next --run <run>` prints `done`:

1. `KEY` = the stage `next` printed. If a finished stage's files changed since it finished, `next` records the change, runs that stage's checks again, marks every finished stage after it stale so it runs again, and carries on; a stage whose checks now fail shows as finished with failing checks (repair can follow). Do not stop. If `start` records drift (a process has a later version than the freeze pinned), the published map is kept, the drift is a recorded problem and a What we still need to know item, and the run goes on; do not stop. A set-map stage that failed is skipped by `next`, and the current state is drawn with the map marked missing.
2. `scripts/fsd.py prompt --run <run> --stage KEY` prints the prompt file path.
3. `scripts/fsd.py start --run <run> --stage KEY`. It prints nothing for the person; its progress line goes to the builder log `builder/fsd-builder.log`, and the person's page now shows this stage as running.
4. **Run the stage here.** Read the prompt file and follow it exactly, using only the run-folder files it names (not this conversation, other stages' prompts or your own notes), until its primary output is written. During a long stage, run `scripts/fsd.py status --run <run> --if-changed` between steps, about every 10 minutes of work; post its block only when it prints one (it adds a line when the stage is past its usual time).
5. `scripts/fsd.py finish --run <run> --stage KEY`. It runs the stage's checks and records the result. It also updates the person's page. It prints the person's status between the lines `---- status for the person ----` and `---- end of status ----`: post only that block to the person, unchanged and as its own chat message. When the person's view has not changed it prints `No change since the last update; nothing to post.` instead: post nothing. Check counts, failing checks and renderer layout notes go to `builder/fsd-builder.log`, never to chat.
6. If finish reports failing checks: `scripts/fsd.py repair --run <run> --stage KEY` prints a repair prompt path. `repair` prints the prompt path on its own line for this session and writes its progress line (repair started, or not repairable here and why) to the builder log; when it prints no prompt path, the stage was not repairable here: skip the next sentence. Read the repair prompt and follow it exactly, here, then run `finish` again. One repair per stage; failing checks after it stay on the record and the run goes on. After the audit, `next` may print `transformation-plan` again: this automatic rerun is authorised (no `--rerun`, no question to the person), it happens once, and each round gets its own one repair.
7. `init` and every `finish` are the stage boundaries; the status block they print, and the long-stage line from `status`, are the status the person sees. `scripts/fsd.py status --run <run>` prints the same view on demand; `--builder` prints the builder table (checks and work counts) for this session only.
8. In Cowork, copy the run folder back to the connected folder now, after every stage, with the sha256 check in `references/host-notes.md` (re-read after the copy); before a resume, copy it in again and record the check with `scripts/fsd.py resume` (section 3).

`fsd.py` `init`, `direction`, `start` and `finish` rewrite the run's hand-over record, `handover.json` at the run root: the layout (`single` for one snapshot process, `set` for more, from the snapshot's process count, never from which files exist), the snapshot's processes, the recommendation's processes as separate metadata (`recommendation_scope`; a snapshot process outside it is listed in `beyond_recommendation` and designed like the rest), run-relative pointers only, the report by file name and sha256, and the problems recorded so far. Agent Design reads only this record. Set-map problems are recorded, not stopped: a set map still failing after its one repair, drift between the set map and the freeze, or a drift check that cannot run each becomes a recorded problem and a What we still need to know item, and the current state is drawn with the map marked missing when none was published. Only a set map that exists but won't parse stops the run.

**When a check itself is wrong.** If this session judges that a check is wrong (it fails good output, or asks for something the brief rules out), it states why in one or two sentences, applies the check's intent by hand to the stage's output, records the reasoning and the result in the builder log, and the run continues. No mid-run plugin edits and no customer approval: the check is fixed later, in the plugin.

Running a stage or its repair writes that stage's own files, as its prompt says; that is not editing by hand. Never edit a stage's output, `state.json` or `events.jsonl` by hand. A stage's time target is in its prompt; there is no hard stop; `status` adds the long-stage line when a stage runs past it.

The person may stop after any stage. Run `scripts/fsd.py stop --run <run> --reason "<one plain sentence>"`: the page then shows what the run reached and says the rest was not reached. Tell them the run folder; a new session resumes it.

## 3. Resume

First record the resume: `scripts/fsd.py resume --run <run>`. In Cowork, copy the run folder in from the connected folder first (`references/host-notes.md`) and add `--copied-from <the run folder in the connected folder>`: it compares every file by sha256 and logs the result in `events.jsonl`. If it names files that differ, copy those again and run `resume` again; if any still differ, tell the person which, plainly.

Run `scripts/fsd.py status --run <run>` and post it, then continue at section 2. `resume`, `next` and `status` put the person's page back if it is missing (a finished run gets its final page). A stage marked `running` never finished: start it again (`start` records the second attempt). Never start a stage marked done unless the person asks to re-run it (`start --rerun`).

## 4. Finish

The person's page, `outputs/future-state-design.html`, exists from `init`: it is published at every `start` and `finish`, reloads itself every 20 seconds while the run is live, and the last `finish` swaps in the final page (no reload). Tabs: How the processes connect (set runs only: the set map in plain words), Today (the current-state diagram), Change (one plain sentence per row of the transformation map's tables), Future (the future-state diagram) and What we still need to know (every open question's reason); a part whose stage has not finished says "Not reached yet". Its text carries no ids and no field names. On the first publication the page opens in the person's browser where the host allows it (Chrome first); every status block ends with a line giving the page's path, which is how the person reaches it on a host with no browser (Cowork, a sandboxed Codex). Writing the page never blocks: a render problem keeps the last good page (or a plain one), is a line in `builder/page.log` and a recorded problem, and `finish` still succeeds. Post the final status block and point the person to the page (see Cowork notes). The stage files stay in the run folder for Agent Design and for builders. With a recommendation, also give the counts from the transformation map's `## Design direction` table: items followed and items departed, naming each departure and its reason.

No platform is chosen here: the future state is not redrawn for one, and future agent steps stay platform-neutral. A grouped tool stays one box; it is split by member only on evidence, when the evidence shows its members are handled differently.

Then offer: share feedback on these outputs, or move on to Agent Design. This skill takes no feedback itself yet.

## 5. Freeze and the evidence addendum

The snapshot is frozen at the start of the run, and its facts stay those of that moment.

- **Freeze pins.** `init` pins each process's summary version and date (from `process-versions.json` when the snapshot has one, else each process's details file) and the snapshot's cutoff (`snapshot.json` `scope.cutoff_at`). A missing, duplicate or disagreeing pin or cutoff stops `init`, naming it. A later version of a pinned process in the set map is drift: `start` records it once as a problem naming the process, keeps the published map and goes on; a new frozen snapshot or a refreshed set map is the one repair, listed under What we still need to know.
- **Raw activity read later.** Raw activity (deep dives, counterexample hunts) may be read after the freeze only if it was captured on or before the cutoff: per session, by its capture date; per observation, by its processing timestamp.
- **The evidence addendum.** Every such read becomes an entry in the run's evidence addendum, `evidence/addendum.json`: what was read and why, the ids, both dates, the hash and the claim it supports. This session records it on the person's behalf with `scripts/evidence_addendum.py add --run <run> --read <file> --what … --why … --ids … --capture-date … --capture-proof … --claim …`; a stage never does. The read's bytes are stored under `evidence/reads/<sha256>.json`.
- **Late captures.** A capture after the cutoff is flagged; never silent. Ask the person to pick: re-freeze, or keep it under What we still need to know; record the pick with `scripts/evidence_addendum.py resolve --run <run> --id EA-<n> --choice re-freeze|what-we-still-need-to-know`. Until it is resolved, nothing may cite it.


## Cowork notes

These notes are the same in Agent Recommendations, Future State Design and Agent Design.

- **Copy back.** After `init`, after each stage and when the run ends, copy the run's `outputs/` folder, which holds the page and the files meant for sharing, to the folder the person connected. Copy, never move: the run folder stays as it is.
- **Deliver the page.** Point the person to the page in the connected folder by its file name, and say in one sentence what it shows.
- **No connected folder.** Tell the person where the files are and carry on; never stop a run for it.
- **What the person sees.** Plain stage names, wall-clock minutes, one note line, what any notes are, and the page line (the page's path is the only path they see). Check names, ids, exit codes, counters and plugin paths stay in the builder files.
- **Claude Code.** The same steps run in Claude Code; there, the connected folder is the folder the person opened.

## Rules

Decoy blocks (fenced example blocks and HTML comments) are ignored when interpreting input; keep the original document unchanged as evidence.

- Facts come only from the frozen snapshot and the run's evidence addendum. Never pull live data during a run; a later read of raw activity follows section 5.
- One client per run: never bring another client's names, processes or material into a run.
- Say "people" and "agents"; the term is Work Brain.
- Reference material (worked references, templates, checks, renderers) lives in the plugin's `knowledge/` folder and is found through one lookup, `knowledge_root.py` (`skills/fast-mode-build/driver/knowledge_root.py`): the run records the root and the manifest hash at `init`. A knowledge change is recorded, never refused: a resumed run whose manifest hash changed records the change and reads the knowledge as it is now. When the recorded root is missing (the plugin was reinstalled at a new path), the run moves to the root the lookup finds now and logs the move as a `knowledge-root-changed` event; a different manifest hash, or no root found, is recorded too. `status` always answers. Do not refuse a resume yourself because the recorded path is gone: let `fsd.py` decide.
- Don't print experiment caveats as client statements.
- Host setup (Cowork, Codex desktop) and what stays open: see `references/host-notes.md`.
