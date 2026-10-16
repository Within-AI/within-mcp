# Transformation map — Bank remittance enrichment and posting (v1)

Present-to-future disposition of every step in the cleared current-state graph
(`../../current-state/complex/current-state.json`, 19 steps), bounded by the ratified remit: recommend, then review.
The agent assembles the permitted evidence, applies only explicitly approved matching logic and returns an
evidence-backed draft or an Escalation; the analyst reviews, holds every authoritative decision and performs every
system action. Access to every surface is assumed for this design, never a verified entitlement. This map is the
fixed semantic input to the future-state graph (`../../future-state/complex/future-state.json`); it is not itself a
diagram and does not alter the current-state graph.

---

## Reshape moves

Each move reshapes the current work before the assign pass, cites the evidence it rests on and says how the outcome is kept. Every move is Assumed until the client confirms it.

| Move | Kind | Current steps | Future steps | Evidence | State | How the outcome is kept |
|---|---|---|---|---|---|---|
| RM-1 | package handoffs | `n-006`, `n-007` | `n-006`, `n-007`, `n-agent-stage-1` | `wwsntk-2`, `wwsntk-3` (source precedence; correlation key) | Assumed | Identity and supporting evidence are still checked for the row, then handed to the analyst as one review packet |
| RM-2 | improve decision quality | `n-010`, `n-012` | `n-agent-balance`, `n-010`, `n-012-agent`, `n-012-analyst` | `wwsntk-4`, `wwsntk-7` (loop-stop threshold; finance-charge tolerance) | Assumed | The analyst still decides the balance and disposes of every remaining difference, now from a drafted assessment or proposal |

### No reshape

- route at entry: the analyst's row selection is the only entry; there is nothing to route.
- consolidate repeated reads: each source is read once per row already.
- consolidate exceptions: the two off-spine dispositions end differently and stay separate.
- remove: every step still carries work an ending needs.
- merge: the analyst's writes each change a different record state.
- reorder: save, completion and verification must run in their current order.

## Outcomes

| Current ending | Future ending | Reason and evidence |
|---|---|---|
| `n-017` | `n-017` | The tracker row is still classified by the analyst (Observed). |
| `n-019` | `n-019` | An unidentified item is still left in its status; closure stays open (`wwsntk-5`). |
| `n-020` | `n-020` | The inquiry is still drafted for outreach (Observed); the send is out of scope. |

---

## 1. Per-node disposition table

The base disposition column uses this run's five-way classification: agent evidence/reasoning contribution, analyst
retained action, shared review seam, Escalation/uncertainty seam, unchanged current-state context.

| Node/`step_code` | Title | Systems | Primary classification | Shape Classification | Sub-Agent Group | Detail/Notes | Fate |
|---|---|---|---|---|---|---|---|
| `n-001` | Open Assigned Transaction Queue | Bank portal | unchanged current-state context | n/a | n/a | Queue set-up stays at the analyst's desk. Future element: `n-001`. Observed. | kept |
| `n-002` | Reconcile Against Assignment Workbook | Excel, Bank portal | unchanged current-state context | n/a | n/a | The analyst establishes the working set; the workbook is the Analyst-lane workspace the agent reads context from. Future element: `n-002`. | kept |
| `n-003` | Select Working Row Block | Bank portal | analyst retained action | n/a | n/a | The analyst's row selection is the agent's input. Future element: `n-003`. | kept |
| `n-004` | Assign Batch To Processor | Bank portal, queue export | analyst retained action | n/a | n/a | Assignment and the export stay with the analyst. Future element: `n-004`. | kept |
| `n-005` | Open Transaction Details | Bank portal | agent evidence/reasoning contribution | script-eligible | n/a | The agent reads the transaction-detail fields for the selected row (payment amount, invoice allocation, difference, wire fields): a lookup, script-eligible. Future element: `n-005`. | assigned |
| `n-006` | Confirm Invoice In Acme | Invoicing app, Company records app, Collections workbench, ERP (receipts) | agent evidence/reasoning contribution | judgment-required | unit-a | Cross-source invoice identity: weighing sources whose precedence and correlation key are unestablished (wwsntk-1, wwsntk-2, wwsntk-3) is judgment; where they would decide the outcome the agent escalates. Future element: `n-006`. | assigned |
| `n-007` | Cross-Check Supporting Evidence | Bank statement report, Outlook, Excel, PDF editor, UiPath | agent evidence/reasoning contribution | judgment-required | unit-a | Corroborating supporting evidence under the same open precedence and correlation (wwsntk-2, wwsntk-3): judgment. Staged into one review packet. Future elements: `n-007`, `n-agent-stage-1`. | assigned |
| `n-008` | Check Bulk Upload Needed | none | shared review seam | n/a | n/a | The agent proposes upload or manual entry inside its review packet; the analyst decides. The Sep 3 design replaces the open method-selection rule with a proposed policy pending client validation (exact 1:1 high, exact 2:1 or 3:1 low, anything else escalated), so the method rule is no longer carried here as an open item. Future element: `n-008`. | kept |
| `n-009` | Upload Remittance Template | Excel, Bank portal | analyst retained action | n/a | n/a | Loading staged rows stays with the analyst; the agent may draft them. Future element: `n-009`. | kept |
| `n-010` | Check Allocation Balanced | none | agent evidence/reasoning contribution | script-eligible | n/a | Whether the proposed allocation balances is a reading of the difference value: arithmetic, script-eligible. The agent stages a draft balance assessment; the analyst decides. Future elements: `n-agent-balance`, `n-010`. | assigned |
| `n-011` | Enter Invoice Rows | Bank portal | analyst retained action | n/a | n/a | Grid writes stay with the analyst; the agent may draft the rows. Future element: `n-011`. | kept |
| `n-012` | Reconcile Remaining Difference | Bank portal, Invoicing app, Bank statement report, Outlook, Company records app | agent evidence/reasoning contribution | judgment-required | unit-c | Split in the future state: the agent proposes a reconciliation path where evidence and approved logic permit, otherwise escalates with a packet and reason; the analyst reviews, corrects and disposes. Also an Escalation/uncertainty seam: the threshold, eligibility boundary and tolerance are unestablished (wwsntk-4, wwsntk-5, wwsntk-7) and completion without zero difference is Contradictory (wwsntk-6). Future elements: `n-012-agent`, `n-012-analyst`. | assigned |
| `n-013` | Save Remittance Update | Bank portal | analyst retained action | n/a | n/a | Save stays with the analyst. Future element: `n-013`. | kept |
| `n-014` | Confirm Mark Complete | Bank portal | analyst retained action | n/a | n/a | Completion stays with the analyst. Future element: `n-014`. | kept |
| `n-015` | Record Enrichment Complete (0 open advices) | Bank portal | unchanged current-state context | n/a | n/a | System-produced terminal state; its Contradictory completion evidence is preserved, never turned into a rule. Future element: `n-015`. | kept |
| `n-016` | Verify Completed Record | Bank portal | analyst retained action | n/a | n/a | The verification read and the two workflow-status writes stay with the analyst. Future element: `n-016`. | kept |
| `n-017` | Classify Assignment Tracker Row | Excel | analyst retained action | n/a | n/a | Classification stays with the analyst; its taxonomy authority is open (gap 9). Future element: `n-017`. | kept |
| `n-019` | Item Left In Reviewed / Unidentified Status | Bank portal | Escalation/uncertainty seam | n/a | n/a | Off-spine disposition; closure and eligibility unestablished (wwsntk-5). Future element: `n-019`. | kept |
| `n-020` | Remittance Inquiry Drafted | Bank portal | Escalation/uncertainty seam | n/a | n/a | Off-spine disposition; outreach stays with the analyst and the send is out of scope. Future element: `n-020`. | kept |

### Tally (reconciles to the current-state node count)

- agent evidence/reasoning contribution: 5 — `n-005`, `n-006`, `n-007`, `n-010`, `n-012`
- analyst retained action: 8 — `n-003`, `n-004`, `n-009`, `n-011`, `n-013`, `n-014`, `n-016`, `n-017`
- shared review seam: 1 — `n-008`
- Escalation/uncertainty seam: 2 — `n-019`, `n-020`
- unchanged current-state context: 3 — `n-001`, `n-002`, `n-015`
- Total 19, counted by script against the current-state graph's 19 steps.

### Edges and decision tables

Every current edge is preserved with its condition. The six exits of `n-012` (`e-014`, `e-019`, `e-020`, `e-021`
and the self-loop `e-022`) leave the analyst's half of the split step in the future state; the agent's re-proposal
is an explicit re-entry (`e-023`). The four decision tables are carried: `dm-source-state-invoice-identity` and
`dm-source-state-supporting-evidence` bound the agent's evidence assembly (owners `n-006`, `n-007`);
`dm-allocation-method` stays with the analyst's decision at `n-008`; `dm-correction-unresolved` is owned by both
halves of `n-012`.

---

## 2. Sub-Agent Groups

Derived from the future state's handoff seams. Three seams hand agent output to a person: the review packet into
`n-008`, the draft balance into `n-010`, and the reconciliation proposal into `n-012-analyst`. The balance seam
carries a script-eligible assessment, so it forms no judgment unit.

| Group ID | Member nodes | Handoff in | Handoff out (target) | Handoff out (gating decision) | Basis |
|---|---|---|---|---|---|
| unit-a | `n-006`, `n-007` | person | unit-c | The analyst's method decision at `n-008` (upload or manual entry), then the balance decision at `n-010`: an unbalanced allocation, or any manual entry, leads into the reconciliation unit | The current graph runs identity confirmation straight into supporting-evidence cross-checking on the same selected row, both governed by the source-state tables (Observed); the future state stages them as one review packet |
| unit-c | `n-012` | unit-a | person-terminal | The analyst's disposition of the remaining difference at `n-012-analyst` (resolved to zero, completed with a documented residual, left unidentified, or inquiry drafted). Asking the agent to re-propose returns to the same unit, which is not a new boundary | The current graph shows reconciliation of the remaining difference as one step with its own correction table and loop (Observed) |

Placement: two judgment units joined by people's decisions (multi-subagent-with-handoffs).

---

## 3. New future-state additions beyond the current-state graph (if any)

| Future step | Added by |
|---|---|
| `n-agent-stage-1` | RM-1 |
| `n-agent-balance` | RM-2 |

- **Review packet** (`n-agent-stage-1`): stages the agent's identity and evidence work, with a method proposal, for
  the analyst at `n-008`.
- **Draft balance assessment** (`n-agent-balance`): makes the balanced or unbalanced reading an explicit draft for
  the analyst at `n-010`.
- **Re-proposal re-entry** (`e-023`): the analyst can ask the agent to re-propose; it returns through the same
  handoff.

---

## 4. Judgment-model statement

Two judgment units, both recommend-then-review: cross-source identity and evidence assembly (`unit-a`) and
remaining-difference reconciliation (`unit-c`). Transaction reading and the balance reading are script-eligible. No
decision is taken by the agent; it never silently turns an Escalation into a recommendation.

---

## 5. Authority boundary

The agent reads the permitted surfaces (bank portal detail and grid, invoicing app, company records app, collections
workbench, ERP receipts view, bank statement report, Outlook advices, UiPath output) under assumed access, and returns
draft output only, to dedicated draft columns or a linked evidence packet. It never clears, writes, saves, completes,
applies, uploads, assigns, classifies, sends or escalates in any system, and never overwrites source columns,
analyst decisions or status fields.

### Write grants

| Grant | Steps | System | System of record | Key control | Write kind | Who writes | Route | Exception reason | Next rung | Step-up evidence | Access | Recipient |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| WG-1 | `n-005`, `n-006`, `n-007`, `n-008` | the person | no | unknown | handoff | agent | n/a | none | n/a | n/a | Assumed | n/a |
| WG-2 | `n-009`, `n-010`, `n-011` | Bank portal (dedicated draft columns) or linked evidence packet | unclear | unknown | draft | agent | screen | none | n/a | n/a | Assumed | n/a |
| WG-3 | `n-012` | the person | no | unknown | handoff | agent | n/a | none | n/a | n/a | Assumed | n/a |
| WG-4 | `n-009`, `n-011`, `n-013` | Bank portal | yes | unknown | decision-bearing | person | screen | none | The agent stages the invoice rows from the balanced draft for the analyst to accept in the grid | Checkability: each row is checked against the balanced draft allocation; reversibility of a saved remittance needs more information | Assumed | n/a |
| WG-5 | `n-014`, `n-016` | Bank portal | yes | unknown | decision-bearing | person | screen | none | None proposed: completion is a status change with downstream effect | Inherited error cost: a wrongly completed record leaves the queue; reversibility of completion needs more information | Assumed | n/a |
| WG-6 | `n-017` | Excel (assignment tracker) | unclear | unknown | decision-bearing | person | screen | none | The agent drafts the tracker classification for the analyst once the taxonomy has an owner | Checkability: blocked until the classification taxonomy is settled (gap 9) | Assumed | n/a |
| WG-7 | `n-004` | Bank portal (batch assignment) | yes | unknown | decision-bearing | person | screen | none | None proposed: assignment routes work to a processor and stays with the analyst | Inherited error cost: a wrongly assigned batch reaches the wrong processor; reversibility of a reassignment needs more information | Assumed | n/a |
| WG-8 | `n-004` | Offline CSV file (assigned-queue export for review) | no | unknown | own-file | person | screen | none | The agent drafts the offline export file from the assigned batch for the analyst to review | Checkability: the offline file is checked row for row against the assigned batch; inherited error cost: the file is a review copy outside any system of record | Assumed | n/a |

The `Recipient` column is the one ruled amendment to the twelve-column Write grants table: `internal`, `outside` or `unclear` on `notice` rows, `n/a` on every other row.

Every row is Assumed: write access and route are never confirmed by this map. Key control is `unknown` for every
step because the evidence carries no key-control marking; it is treated as `No` and listed in What We Still Need to
Know. The agent's writes are handoffs and drafts only; every system-of-record write stays with the analyst. The
queue export (`WG-8`) is the analyst's own transfer of the assigned batch to an offline CSV file for review: no
system of record, nothing sent to anyone.

---

## 6. Retained controls for people

The analyst keeps queue set-up and row selection (`n-001` to `n-004`), every decision (`n-008`, `n-010`, the
disposition at `n-012`), every write, save, completion and status change (`n-009`, `n-011`, `n-013`, `n-014`,
`n-016`), tracker classification (`n-017`) and all outreach (`n-020`).

---

## 7. What We Still Need to Know

- wwsntk-1 the ERP receipts consultation trigger; wwsntk-2 source precedence on conflict; wwsntk-3 the cross-surface
  correlation key: FDE Intake, then document upload of the source rules.
- wwsntk-4 the confidence and loop-stop threshold; wwsntk-5 the eligibility boundary; wwsntk-7 the finance-charge
  tolerance: FDE Intake (design inputs the client sets).
- wwsntk-6 completion without zero difference (Contradictory, preserved): targeted enquiry.
- Gap 9 tracker classification authority; gap 10 the hand-off to the downstream process: targeted enquiry.
- The required threshold and ranking calibration, access verification per surface, and the full remit decision for
  people: FDE Intake.
- Key control status of every step (each Write grants row carries `unknown`), and write access and route for each grant (all Assumed): FDE Intake.

---
_Built by the executor role from the cleared current-state graph and the ratified remit/authority gate content only,
per `templates/transformation-map-brief.template.md`. No review brief, rubric, or grading document was read to
produce this artifact._
