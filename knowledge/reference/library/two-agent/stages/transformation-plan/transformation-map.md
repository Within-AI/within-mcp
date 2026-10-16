# Transformation map — service request intake and review (worked two-agent reference)

Invented generic process: a request arrives in a shared inbox, is read, checked against the requester
record, reviewed and approved by a person, entered in the request register and answered. No client detail.

## Reshape moves

| Move | Kind | Current steps | Future steps | Evidence | State | How the outcome is kept |
|---|---|---|---|---|---|---|
| RM-1 | merge | `c-1`, `c-2`, `c-4` | `f-1` | `process-details` (the same request form is opened and read twice before the review) | Assumed | Every field the review sheet needs is still read from the form, once |
| RM-2 | route at entry | new | `f-2` | `iss-routing` (no written rule for which request type goes to which review queue) | Assumed | Every request still reaches the review; only the queue is chosen at entry |
| RM-3 | package handoffs | new | `f-4` | `process-details` (the reviewer receives the form and the record separately) | Assumed | The reviewer still sees the form and the requester record, in one package |
| RM-4 | improve decision quality | new | `f-5` | `process-details` (the approval rests on the reviewer reading the whole form unaided) | Assumed | The person still approves; the check only points at the fields to confirm |

### No reshape

- consolidate repeated reads: after RM-1 the form is read once and the requester record is read once already.
- consolidate exceptions: the capture draws no exception path.
- remove: every remaining step carries work the ending needs.
- reorder: the review has to follow the record lookup, and the entry has to follow the confirmation.

## Outcomes

| Current ending | Future ending | Reason and evidence |
|---|---|---|
| `c-7` | `f-8` | The requester still hears the outcome from a person (`process-details`). |

## 1. Per-node disposition table

| Node/`step_code` | Title | Systems | Disposition | Shape Classification | Sub-Agent Group | Detail/Notes | Fate |
|---|---|---|---|---|---|---|---|
| `c-1` | Open the shared request inbox | Request inbox | agent-taken | script-eligible | n/a | Opening the inbox is folded into the single read (Observed). | merged into RM-1 |
| `c-2` | Read the request form | Request inbox | agent-taken | script-eligible | n/a | The first read of the form (Observed). | merged into RM-1 |
| `c-3` | Look up the requester record | Request register | agent-taken | script-eligible | n/a | A lookup by requester id (Observed). | assigned |
| `c-4` | Re-read the request form to fill the review sheet | Request inbox | agent-taken | script-eligible | n/a | The same read, repeated (Observed). | merged into RM-1 |
| `c-5` | Review and approve the request values | n/a | human-retained | n/a | n/a | The person decides and confirms the values (Observed). | kept |
| `c-6` | Enter the approved request in the register | Request register | human-retained | n/a | n/a | Confirmed by the person, entered by the review agent (Observed). | split by confirm/enter |
| `c-7` | Send the requester the outcome | Email | human-retained | n/a | n/a | An outside send stays with the person (Observed). | kept |

### Tally (reconciles to the current-state node count)

7 rows, 7 current-state step nodes (the three system rails carry no work and have no row).

## 3. New future-state additions beyond the current-state graph

| Future step | Added by |
|---|---|
| `f-2` | RM-2 |
| `f-4` | RM-3 |
| `f-5` | RM-4 |
| `f-7` | confirm/enter |

## 5. Authority boundary

The intake agent reads the form once, routes the request, looks up the requester record and packages both for
review. It dispatches the package to the review agent. The review agent checks the package and hands it to the
person, who confirms the values; the review agent then enters exactly those confirmed values in the request
register. The person sends the outcome to the requester.

**Agent split (the one split in this design).** The work is carried out by two cooperating agents, starting from
one and splitting once for a named reason: term 36(b), a different authority with its own write-grant set. The
intake agent holds only `WG-1`, the handoff that dispatches the package to the review agent. The review agent holds
only `WG-2`, the write of the confirmed values into the request register. Neither agent holds the other's grant, so
the agent that gathers and packages a request never writes to the register.

### Write grants

The `Recipient` column is the one ruled amendment to the write-stance Write grants table.

| Grant | Steps | System | System of record | Key control | Write kind | Who writes | Route | Exception reason | Next rung | Step-up evidence | Access | Recipient |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| WG-1 | new | the review agent | no | No | handoff | agent | n/a | none | n/a | n/a | Assumed | n/a |
| WG-2 | `c-6` | Request register | yes | No | mechanical | agent | api | none | n/a | n/a | Assumed | n/a |
| WG-3 | `c-7` | Email | no | No | notice | person | screen | none | The agent drafts the requester notice | Inherited error cost: a wrong notice reaches an outside party | Assumed | outside |

## 7. What We Still Need to Know

- Which request type goes to which review queue (`iss-routing`): the routing at entry (RM-2) is Assumed.
- Write access to the request register for the review agent (WG-2): FDE Intake.
