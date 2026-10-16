### ambition-audit — Attack the first design for ambition
- Reads: `inputs/snapshot.json`, the Current state Workflow (`stages/current-state/`), the transformation map (`stages/transformation-plan/transformation-map.md`), the Future state Workflow (`stages/future-state/`) and the design direction (`inputs/design-direction.json`, when the run has one). Read nothing else: no reference, no template, no file outside this run's folder.
- Reference: none. This stage has no worked reference.
- Target: about 5 minutes. Aim to be done inside it.
- Produce `stages/ambition-audit/ambition-audit.md`, and nothing else in the stage folder.
- **You are an adversarial auditor, not a reviewer.** Assume the first design copied today's work and only changed who does it, and try to prove it. Do not praise, score or grade the design, and do not fix it yourself: find where it could have been more ambitious and propose the concrete change. The recommendation is the floor, not the ceiling: the design should give agents all the work the evidence supports, with a person kept only for a stated reason. Your proposals feed one rework pass of the transformation map and the future state.
- **Attack lines.** Try every one of these against the map and the future state, citing what you find:
  - Repeated reads of the same data: the same data read again before anything has changed it.
  - A person copying data between systems.
  - A person-kept step with no Why people stay reason.
  - An untouched process: a snapshot process whose steps the design leaves as they are today.
  - A hand-off that could be packaged or removed.
  - A mechanical step left to a person.
  - A missing router over a shared system: work arriving through one shared system that nobody classifies and sends down the right path at entry.
  - A move kind listed under No reshape without a real reason.
- **Never add client facts.** Use only what the run folder already says. Never invent volumes, rules, systems, steps, people or timings; a proposal that needs a fact the evidence does not hold says so in What to change and stays a proposal. Never name the platform an agent runs on. Keep a person on only three kinds of step, and propose nothing that takes them away: a control step, which exists only in a process the snapshot marks key control (key control is Yes only when the snapshot marks it true; otherwise No), and is the approval, sign-off or submission itself and the record writes that carry it; anything sent to or written into a party outside the organisation; and a step the snapshot or a client-quoted direction item states a person must do, not merely that a person does it today. Outside a key-control process an approval, sign-off or submission is not protected: propose removing it or giving it to an agent when the evidence allows. Attack everything else.
- Write the file with exactly these two sections, in this order:
  ```
  ## Verdict

  <two or three plain lines>

  ## Proposals

  | Proposal | What to change | Steps | Evidence |
  |---|---|---|---|
  ```
  - **Verdict:** two or three plain lines saying whether the first design is a real redesign or a near-copy of today's work, and why. Plain words only: no ids of any kind (no step, move, grant, source or issue ids) and no table.
  - **Proposals:** one row per proposal, with the header exactly `| Proposal | What to change | Steps | Evidence |`. Proposal: `AP-<n>`, numbered from 1. What to change: the change in plain words, naming the attack line it answers. Steps: the current-state or future-state step ids the change touches, comma-separated. Evidence: ids from `inputs/snapshot.json` (`sources[].id`, `issues[].id`), comma-separated; a step id is not evidence. When no attack line finds anything, keep the header and the separator with no rows, and say why in the Verdict.
- Write the file early with both headings and grow it; a partial file on disk beats none.
- Checks: `scripts/check_ambition_audit.py RUN_ROOT` reads the file and prints its shape and any evidence id it cannot find in the snapshot. Its findings are notes; they never fail the stage. Fix what it notes where the evidence allows.

- Paths: a path that begins `checks/`, `renderer/`, `reference/` or `templates/` is under `KNOWLEDGE_ROOT`; a path that begins `scripts/` is under `SKILL_ROOT` (your prompt names it), and you may read and run the scripts this stage names there with `python3`; every other path is under `RUN_ROOT`.

Read only inside this run's folder and the plugin; never list or open folders beside the run.
