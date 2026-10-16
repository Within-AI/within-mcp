# Transformation map — ERP open-item clearing, read-only agent in the loop (v1)

Present-to-future disposition of every step in the cleared current-state graph
(`../../current-state/middle/current-state.json`, 23 steps), written against the future-state design it bridges to
(`../../future-state/middle/future-state.json`: a scheduled read-only agent that briefs the analyst, plus an
exception seam the analyst starts by supplying company-code scope). Bounded by the ratified remit: recommend, then
review; the agent reads permitted ERP surfaces and writes nowhere; the analyst decides and performs every write.
This map is the fixed semantic input to the future-state graph; it is not itself a diagram and does not alter the
current-state graph.

---

## Reshape moves

Each move reshapes the current work before the assign pass, cites the evidence it rests on and says how the outcome is kept. Every move is Assumed until the client confirms it.

| Move | Kind | Current steps | Future steps | Evidence | State | How the outcome is kept |
|---|---|---|---|---|---|---|
| RM-1 | consolidate repeated reads | `n-set-params`, `n-exec-test`, `n-export-list`, `n-dec-more-scope` | `n-02`, `n-03` | `WWSNTK-5`, `WWSNTK-7` (variant configuration; the direct read path) | Assumed | Every in-scope variant, geography and G/L is still read and its clearable pairs computed, in one pass instead of one per scope |
| RM-2 | package handoffs | `n-apply-label` | `n-04`, `n-05a`, `n-06`, `n-07`, `n-4esc` | `WWSNTK-1`, `WWSNTK-3` (label mapping; tie-break order) | Assumed | The analyst still decides every label, now from one briefing with evidence per group |
| RM-3 | consolidate exceptions | `n-review-log`, `n-teams-followup` | `n-10in`, `n-10`, `n-11`, `n-12`, `n-13` | `WWSNTK-4`, `WWSNTK-6` (log taxonomy; escalation authority) | Assumed | Every exception still reaches the analyst, packaged one case per exception |

### No reshape

- route at entry: one daily due point starts every run; there is nothing to route.
- improve decision quality: the label decision is bounded by its decision table; its open rows escalate rather than default.
- remove: every step still carries work an ending needs.
- merge: the remaining analyst steps each write a different surface.
- reorder: the update run must follow the label decisions.

## Outcomes

| Current ending | Future ending | Reason and evidence |
|---|---|---|
| `n-end` | `n-corr` | `n-corr` is the future graph's one declared outcome and its only reachable ending: every path from the start `n-01` runs through the posting result `n-09`, which continues into exception handling (`n-10in`), and closes at the separate correction path `n-corr`. Matched pairs are still posted at `n-09` (Observed); the residual rows the current ending documented go to `n-corr` (Assumed). |

---

## 1. Per-node disposition table

The base disposition column uses this run's category letters: **A** agent evidence/reasoning contribution, **R**
analyst retained action, **S** shared review seam, **E** Escalation/uncertainty seam, **U** unchanged current-state
context. A compound cell names the first category as primary.

| Node/`step_code` | Title | Systems | Category | Shape Classification | Sub-Agent Group | Detail/Notes | Fate |
|---|---|---|---|---|---|---|---|
| `n-start` | Daily cash-application clearing due | none | U | n/a | n/a | The daily due point becomes the agent's scheduled start. Future element: `n-01`. The cadence itself needs more information. | kept |
| `n-open-clearing` | Open Automatic Clearing from the ERP home | ERP (clearing) | R | n/a | n/a | The analyst still opens the clearing program for the update run. The test-run opening is superseded by the agent's direct read. Future elements: `n-08` (update run), `n-02` (read). | kept |
| `n-load-variant` | Load saved clearing variant; confirm company code | ERP (clearing) | R | n/a | n/a | Retained for the update run. Variant configuration stays open (WWSNTK-5). Future element: `n-08`. | kept |
| `n-set-params` | Set posting parameters, Test Run checked | ERP (clearing) | A + R | script-eligible | n/a | The test-run selection (clearing date, company-code range, G/L accounts) becomes the agent's read across variant, geography and G/L: a lookup, script-eligible. The analyst still sets the update-run parameters. Future elements: `n-02`, `n-08`. Observed step; agent read Assumed. | assigned |
| `n-exec-test` | Test run returns detail list and posting-period warnings | ERP (clearing) | U | n/a | n/a | System return; in the future it is the agent's read of the open-item tables itself. Future elements: `ra-02-oi`, `sys-erp-oi`, `n-02`. Read entitlement needs more information (WWSNTK-7). | kept |
| `n-dec-period` | Detail list review: clearable pairs, open period? | ERP (clearing) | A | script-eligible | n/a | Which pairs are clearable is a computation over the read (net balance by Assignment): script-eligible. Rows blocked by a closed period surface later as the exception category for blocked periods. Future element: `n-03`. | assigned |
| `n-export-list` | Export detail list to Excel | ERP (clearing), Excel | A | script-eligible | n/a | Superseded: the agent reads the open items directly, so no export is needed. Future element: `n-02`. | merged into RM-1 |
| `n-filter-pairs` | Isolate paired positive/negative amounts by assignment | Excel | A | script-eligible | n/a | Grouping candidate rows by Assignment, keeping the whole group and its provenance: string match and arithmetic, script-eligible. Future element: `n-03`. | assigned |
| `n-load-line-items` | Load matching line items in the ERP line-item app | ERP line-item app | R | n/a | n/a | The analyst loads the approved groups' items to apply labels. Future elements: `n-08`, `sys-erp-line-items`. | kept |
| `n-open-batch-edit` | Select rows; open the batch edit dialog | ERP line-item app | R | n/a | n/a | Retained write. Future elements: `n-08`, `w-08-erp`, `sys-erp-line-items`. | kept |
| `n-apply-label` | Select and apply the standard Item Text label for the group | ERP (clearing), Sticky Notes | A + S | judgment-required | unit-briefing | The core seam. The agent evaluates each Assignment group against the label evidence and the client-ratified CANCELLATION first-pass policy, recommends only where evidence supports the group, and escalates the rest (weighing whether evidence is sufficient is judgment, and the label mapping is unestablished, WWSNTK-1 and WWSNTK-3). The analyst reviews every recommendation and applies the label. Future elements: `n-04`, `n-05a`, `n-06`, `n-07`, `n-4esc`. | assigned |
| `n-confirm-changed` | "Items changed" toast returned | ERP (clearing) | U | n/a | n/a | System return, folded into the posting result. Future element: `n-09`. | kept |
| `n-rerun-update` | Uncheck Test Run; execute the update run | ERP (clearing) | R | n/a | n/a | Clearing-run authority stays with the analyst. Future elements: `n-08`, `w-08-erp`. | kept |
| `n-confirm-cleared` | Update-run output and Additional Log | ERP (clearing) | U | n/a | n/a | System result. Future element: `n-09`. Additional Log taxonomy open (WWSNTK-4). | kept |
| `n-validate-crosscheck` | Validate postings; archive report; cross-check outside the ERP | Acrobat, report archive, local note | R | n/a | n/a | Retained by the analyst and not redrawn in the future state (it sits outside the agent's two seams). Carries the Contradictory report-save observation and the open client-entity note authority (WWSNTK-2). See What we still need to know. | kept |
| `n-dec-exceptions` | Do exception rows persist? | ERP (clearing) | R | n/a | n/a | When exceptions remain, the analyst supplies the affected company-code scope, which starts the exception seam. Future element: `n-10in`. | kept |
| `n-review-log` | Open Additional Log; review and filter exception rows | ERP (clearing), Excel | A + E | judgment-required | unit-exceptions | The agent reads the Additional Log for the supplied scope, triages each exception into an evidence-state category and packages one case per exception. Linking rows only where the evidence supports it is judgment; the taxonomy is open (WWSNTK-4) and a full log-to-item join needs more information. Future elements: `n-10`, `sys-erp-addlog`, `ra-10-addlog`, `n-11`, `n-12`, `n-13`. | assigned |
| `n-update-exceptions` | Apply the corrective label for the next pass | ERP line-item app, Sticky Notes | R + E | n/a | n/a | Re-cleaning stays with the analyst: the future state has no exception-to-label recommendation. The analyst decides per exception and re-cleans through the line-item app. Future elements: `n-14`, `n-08`. | kept |
| `n-teams-followup` | Escalate the closed-period blocker to a colleague | Microsoft Teams | R + E | n/a | n/a | Escalation of residue stays analyst-owned: the agent packages the case, the analyst routes it to a separate correction path. Future elements: `n-13`, `n-14`, `n-corr`. Receiving owner needs more information (WWSNTK-6). | kept |
| `n-dec-allocation` | Did the run resolve a credit-note allocation request? | ERP (clearing), Outlook | R | n/a | n/a | Retained and not redrawn in the future state (outside the agent's seams). | kept |
| `n-confirm-allocation` | Verify line item; draft the Outlook confirmation | ERP line-item app, Outlook | R | n/a | n/a | Retained write and communication; not redrawn in the future state. | kept |
| `n-dec-more-scope` | More company codes or variants this run? | ERP (clearing) | A | script-eligible | n/a | The per-scope loop is absorbed: the agent computes the clearable universe across every in-scope variant, geography and G/L in one pass. Future element: `n-03`. | merged into RM-1 |
| `n-end` | Update run posted; residual rows documented | none | U | n/a | n/a | Terminal context; in the future the run passes the posting result (`n-09`) and ends at the separate correction path `n-corr`. | kept |

### Tally (reconciles to the current-state node count)

- A (primary): 7 — `n-set-params`, `n-dec-period`, `n-export-list`, `n-filter-pairs`, `n-apply-label`, `n-review-log`, `n-dec-more-scope`
- R (primary): 11 — `n-open-clearing`, `n-load-variant`, `n-load-line-items`, `n-open-batch-edit`, `n-rerun-update`, `n-validate-crosscheck`, `n-dec-exceptions`, `n-update-exceptions`, `n-teams-followup`, `n-dec-allocation`, `n-confirm-allocation`
- U: 5 — `n-start`, `n-exec-test`, `n-confirm-changed`, `n-confirm-cleared`, `n-end`
- Total 23, counted by script against the current-state graph's 23 steps.

Decision table `dm-signal-label` (owner `n-apply-label`): A + E. Carried into the future state with owner `n-04`; the
agent's evaluation is bounded by it, and its open rows (WWSNTK-1, WWSNTK-3, WWSNTK-4) force an escalation rather
than a default label.

---

## 2. Sub-Agent Groups

| Group ID | Member nodes | Handoff in | Handoff out (target) | Handoff out (gating decision) | Basis |
|---|---|---|---|---|---|
| unit-briefing | `n-apply-label` | entry | unit-exceptions | The analyst's per-group decision on the briefing (`n-07`) and the update run it leads to (`n-08`, `n-09`); exception handling starts only when the analyst supplies the affected company-code scope (`n-10in`) | The label judgment is one unit: the current graph shows it as the analyst's single judgment core fed by the grouped pairs (Observed), and the future state hands its briefing to the analyst in one handoff |
| unit-exceptions | `n-review-log` | unit-briefing | person-terminal | The analyst's per-exception decision (`n-14`): re-clean through the line-item app, or route the residue to the separate correction path. A re-clean returns to the same unit on the next pass, which is not a new boundary | The current graph shows exception review as one reconciliation step on the Additional Log (Observed); the future state packages it into one escalation to the analyst |

Placement: two judgment units joined by a person's decision (multi-subagent-with-handoffs). The script-eligible rows
(read, grouping, clearable-universe computation) belong to no group.

---

## 3. New future-state additions beyond the current-state graph (if any)

| Future step | Added by |
|---|---|
| `n-04` | RM-2 |
| `n-05a` | RM-2 |
| `n-06` | RM-2 |
| `n-4esc` | RM-2 |
| `n-10in` | RM-3 |

- **Confidence computation and briefing** (`n-04` to `n-06`): a ranked table and CSV of clearable groups with
  confidence, escalations and batch provenance. Introduced by the design; no captured step.
- **Unsupported-group review** (`n-4esc`): an unsupported group is escalated to the analyst instead of defaulting a
  label. Introduced by the design.
- **Analyst scope input** (`n-10in`): the one person-to-agent input; whether a direct read still needs it is open
  (WWSNTK-8).

---

## 4. Judgment-model statement

Two judgment units exist, both recommend-then-review: the label evaluation for Assignment groups (`unit-briefing`)
and exception triage with case packaging (`unit-exceptions`). Neither decides: the analyst validates or overrides
every recommendation and decides every exception. Insufficient evidence always escalates and never defaults a
label.

---

## 5. Authority boundary

The agent reads the ERP open-item tables and the Additional Log surface, read-only (production entitlement needs
more information, WWSNTK-7), and writes nowhere: no ERP, line-item app, spreadsheet or message write. Its outputs
leave its band only as the briefing handoff and the escalation cases. Every label, save and update run is the
analyst's.

### Write grants

| Grant | Steps | System | System of record | Key control | Write kind | Who writes | Route | Exception reason | Next rung | Step-up evidence | Access | Recipient |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| WG-1 | `n-apply-label`, new | the person | no | unknown | handoff | agent | n/a | none | n/a | n/a | Assumed | n/a |
| WG-2 | `n-review-log`, `n-teams-followup` | the person | no | unknown | handoff | agent | n/a | none | n/a | n/a | Assumed | n/a |
| WG-3 | `n-load-line-items`, `n-open-batch-edit`, `n-apply-label` | ERP line-item app | yes | unknown | decision-bearing | person | screen | none | The agent pre-fills each label from the briefing row for the analyst to accept in the batch edit | Checkability: each label traces to a briefing row the analyst confirmed; reversibility of an Item Text edit needs more information | Assumed | n/a |
| WG-4 | `n-rerun-update` | ERP (clearing) | yes | unknown | decision-bearing | person | screen | none | None proposed: the update run posts clearings, and its inherited error cost is not yet known | Inherited error cost: a wrong clearing flows into the ledger; reversibility of a posted clearing needs more information | Assumed | n/a |
| WG-5 | `n-update-exceptions` | ERP line-item app | yes | unknown | decision-bearing | person | screen | none | The agent drafts the corrective label per exception for the analyst to confirm | Checkability: the corrective label is checked on the next clearing pass; key control status needs more information | Assumed | n/a |
| WG-6 | `n-teams-followup` | Microsoft Teams | no | unknown | handoff | person | screen | none | The agent drafts the escalation message from the packaged case for the analyst to send | Checkability: the message carries the packaged case the analyst already reviewed; reversibility: a Teams message can be edited or deleted | Assumed | n/a |
| WG-7 | `n-confirm-allocation` | Outlook | no | unknown | notice | person | screen | none | The agent drafts the Outlook confirmation from the verified line item for the analyst to send | Checkability: the confirmation quotes the verified line item; inherited error cost: a wrong confirmation misleads the requester, and reversibility of a sent email is low | Assumed | unclear |

The `Recipient` column is the one ruled amendment to the twelve-column Write grants table: `internal`, `outside` or `unclear` on `notice` rows, `n/a` on every other row.

Every row is Assumed: write access and route are never confirmed by this map. Key control is `unknown` for every
step because the evidence carries no key-control marking; it is treated as `No` and listed in What We Still Need to
Know. The agent writes nowhere in a system: its only writes are the two handoffs to the analyst.

---

## 6. Retained controls for people

The analyst still opens the clearing program and loads the variant for the update run (`n-open-clearing`,
`n-load-variant`), loads and edits line items and runs the update (`n-load-line-items`, `n-open-batch-edit`,
`n-rerun-update`), decides per group and per exception, re-cleans (`n-update-exceptions`), routes residue and
escalates to colleagues (`n-teams-followup`), validates postings outside the ERP (`n-validate-crosscheck`) and
handles allocation confirmations (`n-dec-allocation`, `n-confirm-allocation`).

---

## 7. What We Still Need to Know

- WWSNTK-1 label vocabulary and mapping; WWSNTK-3 tie-break order; WWSNTK-4 Additional Log taxonomy: document upload
  of the label list and log catalogue, then FDE Intake.
- WWSNTK-2 client-entity note authority; WWSNTK-5 variant configuration; WWSNTK-6 posting and escalation authority:
  FDE Intake.
- WWSNTK-7 production read path and entitlement: FDE Intake with the ERP owner.
- WWSNTK-8 whether the direct read still needs the analyst's scope; WWSNTK-9 whether rejected or deferred groups
  resurface in the next briefing: targeted enquiry.
- Validation, report archiving and allocation confirmation are not drawn in the future state: confirm they stay as
  they are (targeted enquiry).
- The six cross-cutting patterns the older frozen source cited (CP-1 to CP-6) have no item of their own in the
  Company Brain pull; they are open in the current state as evidence issues, left for a decision.
- Whether the allocation requester is inside the company (`WG-7`, Recipient `unclear`): if so, the notice is internal and
  the floor gives it to the agent. Route: targeted enquiry.
- Key control status of every step (each Write grants row carries `unknown`), and write access and route for each grant (all Assumed): FDE Intake.

---
_Built by the executor role from the cleared current-state graph and the ratified remit/authority gate content only,
per `templates/transformation-map-brief.template.md`. No review brief, rubric, or grading document was read to
produce this artifact._
