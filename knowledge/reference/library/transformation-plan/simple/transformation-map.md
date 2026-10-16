# Transformation map — Weekly metrics pack refresh (v1)

Present-to-future disposition of every step in the cleared current-state graph
(`../../current-state/simple/current-state.json`, 18 steps, every one a person's step in the Analyst band), bounded
by the ratified remit/authority gate for this agent: no model or judgment step anywhere; a pure deterministic
script that writes only to review copies it creates. This map is the fixed semantic input to the future-state graph
(`../../future-state/simple/future-state.json`); it is not itself a diagram and does not alter the current-state
graph.

---

## Reshape moves

Each move reshapes the current work before the assign pass, cites the evidence it rests on and says how the outcome is kept. Every move is Assumed until the client confirms it.

| Move | Kind | Current steps | Future steps | Evidence | State | How the outcome is kept |
|---|---|---|---|---|---|---|
| RM-1 | remove | `n-step-1-2-3`, `n-step-2-1-1`, `n-step-2-1-2`, `n-step-2-1-3`, `n-step-2-1-4`, `n-step-2-1-5`, `n-step-2-1-6` | `n-step-1-2-1` | `remit-gate` (the ratified remit/authority gate: the accepted template already carries the labelled placeholders) | Assumed | The pack still shows the three metrics, labelled, in the accepted layout; the script writes them into the template's placeholders |
| RM-2 | reorder | `n-step-1-1-2`, `n-step-1-2-2` | `n-run-start`, `n-step-1-1-2`, `n-step-1-2-2` | `decision-2026-09-24` (the re-sequencing decision) | Assumed | The screenshot is still captured and inserted, now once the review copies exist |

### No reshape

- route at entry: one kind of run enters the work; there is nothing to route.
- consolidate repeated reads: the pipeline report is read once already.
- package handoffs: the only handoff is the review copies to a person.
- improve decision quality: no decision step exists in this work.
- consolidate exceptions: no exception path is drawn in the current state.
- merge: the remaining steps each touch a different surface.

## Outcomes

| Current ending | Future ending | Reason and evidence |
|---|---|---|
| `n-step-3-1-5` | `n-step-3-1-5` | The pack is still distributed to the team and the message tidied; the terminal step is the same in both states (Observed). |

---

## 1. Per-node disposition table

The base disposition column uses this run's own taxonomy (`unchanged`, `deterministic-code-executed`, `retired`).

| Node/`step_code` | Title | Systems | Disposition | Shape Classification | Sub-Agent Group | Detail/Notes | Fate |
|---|---|---|---|---|---|---|---|
| `n-step-1-1-1` | Navigate to marketing dashboard | Salesforce | unchanged | n/a | n/a | Stays with a person: this is the brand-awareness dashboard, outside the three-metric scope. Future element: `n-step-1-1-1`. Observed. | kept |
| `n-step-1-1-2` | Capture dashboard screenshot | Salesforce | unchanged | n/a | n/a | Stays with a person, on the reading that the screenshot is of the brand-awareness dashboard. Needs more information: if it also covers pipeline data, that part would be re-disposed later (What we still need to know, b). Future element: `n-step-1-1-2`. | kept |
| `n-step-1-1-3` | Navigate to pipeline dashboard | Salesforce | deterministic-code-executed | script-eligible | n/a | The script retrieves the pipeline influence report through a read-only report call and takes the exact value before rounding. A lookup: script-eligible. Whether the other two metrics come from the same report needs more information (a). Future element: `n-step-1-1-3`. Observed step; the script is Assumed. | assigned |
| `n-step-1-2-1` | Open Weekly Marketing Metrics presentation | Google Slides | deterministic-code-executed | script-eligible | n/a | The script copies the accepted Slides template for the period and writes the computed values into its pre-defined placeholders (reads the template, writes only the run's copy). A fill-in of known values: script-eligible. It replaces the manual placeholder and formatting work of the seven retired rows. Future element: `n-step-1-2-1`. | assigned |
| `n-step-1-2-2` | Insert dashboard screenshot | Google Slides | unchanged | n/a | n/a | Stays with a person (same reading and flag as `n-step-1-1-2`). In the future state the people's sequence picks up here once the review copies exist. The captured contradiction on this step (a static screenshot treated as live data) is carried. Future element: `n-step-1-2-2`. | kept |
| `n-step-1-2-3` | Add metric placeholders | Google Slides | retired | n/a | n/a | No longer performed: the accepted template already carries the placeholders the script writes into. | removed by RM-1 |
| `n-step-2-1-1` | Select data visualizations | Google Slides | retired | n/a | n/a | No longer performed: values are written as text into placeholders, so there are no visualization images to select. | removed by RM-1 |
| `n-step-2-1-2` | Apply horizontal distribution | Google Slides | retired | n/a | n/a | No longer performed: no images to distribute. | removed by RM-1 |
| `n-step-2-1-3` | Center elements on page | Google Slides | retired | n/a | n/a | No longer performed: no images to centre. | removed by RM-1 |
| `n-step-2-1-4` | Create visualization labels | Google Slides | retired | n/a | n/a | No longer performed: the template's placeholders are already labelled. | removed by RM-1 |
| `n-step-2-1-5` | Format and duplicate labels | Google Slides | retired | n/a | n/a | No longer performed: no hand-built labels to format or duplicate. | removed by RM-1 |
| `n-step-2-1-6` | Align labels with visualizations | Google Slides | retired | n/a | n/a | No longer performed: no separately placed image and label pairs. | removed by RM-1 |
| `n-step-2-1-7` | Copy presentation share link | Google Slides | unchanged | n/a | n/a | Stays with a person: the script's authority ends at creating review copies; sharing stays in the existing review process. Future element: `n-step-2-1-7`. | kept |
| `n-step-3-1-1` | Navigate to team channel | Slack | unchanged | n/a | n/a | Stays with a person: no new Slack integration. Future element: `n-step-3-1-1`. | kept |
| `n-step-3-1-2` | Compose distribution message | Slack | unchanged | n/a | n/a | Stays with a person (as `n-step-3-1-1`). Future element: `n-step-3-1-2`. | kept |
| `n-step-3-1-3` | Include presentation link | Slack | unchanged | n/a | n/a | Stays with a person. Future element: `n-step-3-1-3`. | kept |
| `n-step-3-1-4` | Send message to team | Slack | unchanged | n/a | n/a | Stays with a person. Future element: `n-step-3-1-4`. | kept |
| `n-step-3-1-5` | Clean up message formatting | Slack | unchanged | n/a | n/a | Stays with a person; the terminal step in both states. Future element: `n-step-3-1-5`. | kept |

### Tally (reconciles to the current-state node count)

- unchanged: 9 — `n-step-1-1-1`, `n-step-1-1-2`, `n-step-1-2-2`, `n-step-2-1-7`, `n-step-3-1-1` to `n-step-3-1-5`
- deterministic-code-executed: 2 — `n-step-1-1-3`, `n-step-1-2-1`
- retired: 7 — `n-step-1-2-3`, `n-step-2-1-1` to `n-step-2-1-6`
- Total 18, counted by script against the current-state graph's 18 steps.

---

## 2. Sub-Agent Groups

None. Every agent-taken row is script-eligible, so there is no judgment unit and no group (placement: script).

| Group ID | Member nodes | Handoff in | Handoff out (target) | Handoff out (gating decision) | Basis |
|---|---|---|---|---|---|

---

## 3. New future-state additions beyond the current-state graph (if any)

| Future step | Added by |
|---|---|
| `n-run-start` | RM-2 |
| `n-sheet-write` | RM-1 |
| `n-run-log` | RM-1 |

Additive items with no current-state step; they are not dispositions and are not in the tally.

1. **The script's own start** (`n-run-start`). The script's four steps get their own clear start, and the people's
   screenshot and distribution steps run as their own sequence that picks up once the review copies exist
   (re-sequenced by the 2026-09-24 decision). What starts a run, a schedule or a person, is not in the evidence:
   needs more information, never invented.
2. **Sheet review copy** (`n-sheet-write`). No current step touches a Sheet; the script creates a fresh review copy
   and writes the computed values into it. Assumed (ratified target, not observed).
3. **Run log and reconciliation record** (`n-run-log`). For each value: definition, period, source and run time,
   destination and read-back result, stored beside the review copies. Assumed.

---

## 4. Judgment-model statement

No model or judgment step exists in this map. Every current step is unchanged, deterministic-code-executed or
retired, and none of the three additions introduces a judgment step. The "agent recommends, person ratifies" pattern
does not apply to this version.

---

## 5. Authority boundary

The script reads the pipeline report (read-only) and the accepted Slides template, and writes only to the Sheet and
Slides review copies it creates for a run and to its run log. It never writes to an original or live file and never
publishes or distributes. None of these touchpoints has live credentials yet.

| Current step | System | Future role | Touchpoint |
|---|---|---|---|
| `n-step-1-1-3` | Salesforce | script | report retrieval, read-only |
| `n-step-1-2-1` | Google Slides | script | template read; write to the run's own copy only |
| (addition) | Google Sheets | script | write to the run's own copy only |
| (addition) | Run log | script | write |
| every unchanged step | Salesforce, Google Slides, Slack | person | none (no agent access requested) |

### Write grants

| Grant | Steps | System | System of record | Key control | Write kind | Who writes | Route | Exception reason | Next rung | Step-up evidence | Access | Recipient |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| WG-1 | `n-step-1-2-1`, new | Google Sheets (the run's review copy) | no | unknown | own-file | agent | api | none | n/a | n/a | Assumed | n/a |
| WG-2 | `n-step-1-2-1` | Google Slides (the run's review copy) | no | unknown | own-file | agent | api | none | n/a | n/a | Assumed | n/a |
| WG-3 | new | Run log | no | unknown | own-file | agent | api | none | n/a | n/a | Assumed | n/a |
| WG-4 | `n-step-1-2-2` | the person | no | unknown | handoff | agent | n/a | none | n/a | n/a | Assumed | n/a |
| WG-5 | `n-step-2-1-7`, `n-step-3-1-1`, `n-step-3-1-2`, `n-step-3-1-3`, `n-step-3-1-4`, `n-step-3-1-5` | Slack | no | unknown | notice | person | screen | none | The script posts the accepted pack link to the team channel once a person has approved the copy | Reversibility: a channel post can be edited or deleted; checkability: the link is the reviewed copy, read back by the run log | Assumed | unclear |
| WG-6 | `n-step-1-2-2` | Google Slides | no | unknown | draft | person | screen | none | The script captures the dashboard screenshot and places it in the run's review copy for the person to check | Checkability: the person compares the screenshot with the live dashboard before approving the copy; reversibility: an image in the review copy can be replaced | Assumed | n/a |

The `Recipient` column is the one ruled amendment to the twelve-column Write grants table: `internal`, `outside` or `unclear` on `notice` rows, `n/a` on every other row.

Every row is Assumed: write access and route are never confirmed by this map. Key control is `unknown` for every
step because the evidence carries no key-control marking; it is treated as `No` and listed in What We Still Need to
Know. The script writes only its own review copies and log (`own-file`); distribution stays with a person.

---

## 6. Retained controls for people

A person still, in the future state:

- reviews the brand-awareness dashboard and captures and inserts its screenshot (`n-step-1-1-1`, `n-step-1-1-2`,
  `n-step-1-2-2`), out of the three-metric scope;
- copies the share link and owns distribution in Slack end to end (`n-step-2-1-7`, `n-step-3-1-1` to `n-step-3-1-5`);
- checks values, exceptions and layout in the review copies before promoting them, and authorizes each run;
- on rejection, corrects the source or mapping and asks for a new run (the script never self-corrects).

---

## 7. What We Still Need to Know

- **(a) Report mapping for two of the three metrics**: whether they come from the one observed pipeline report or
  need further reports. Route: document upload of the report list, or FDE Intake.
- **(b) Screenshot scope at `n-step-1-1-2`**: brand-awareness only, or pipeline data too. Route: targeted Companion
  capture.
- **(c) Template identity**: the Sheet and Slides template ids, ranges and placeholder bindings. Route: FDE Intake.
- **(d) Promotion ownership**: who approves promotion or publication of the reviewed copies. Route: FDE Intake.
- **(e) Validation schedule**: the test periods and pass bar for the two owner-reviewed live periods. Route: FDE
  Intake.
- **(f) What starts a run**: a schedule or a person. Route: targeted enquiry with the business owner.
- **(g) Who reads the team channel** (`WG-5`, Recipient `unclear`): the team only, or also people outside the
  company. If the team only, the notice is internal and the floor gives it to the agent. Route: targeted enquiry.
- Key control status of every step (each Write grants row carries `unknown`), and write access and route for each grant (all Assumed): FDE Intake.

---
_Built by the executor role from the cleared current-state graph and the ratified remit/authority gate content only,
per `templates/transformation-map-brief.template.md`. No review brief, rubric, or grading document was read to
produce this artifact._
