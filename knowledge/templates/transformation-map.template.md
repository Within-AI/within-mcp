<!--
  Transformation-map stage — canonical ARTIFACT template ("Agree the agent's role and authority";
  internal tag: transformation). No template for this artifact existed before this handoff (confirmed by
  search — no template file, no structural check script, nothing under checks/ or scripts/ mentioned it).

  WHAT THIS IS
    The per-node present-to-future disposition table PLUS the two new fields this handoff adds on top of
    it (`Shape Classification`, `Sub-Agent Group`), plus the new `## Sub-Agent Groups` section. This is
    the schema ADDITION, layered orthogonally on whichever base disposition/category taxonomy a given run
    already uses — it is NOT a new base taxonomy and does NOT reconcile the three that already exist in
    production (canonical 5-value `agent-taken` / dogfooding's `deterministic-code-executed` 6th value /
    a client build's own A/R/S/E/U system). A run keeps its own base-disposition column exactly as today; this template
    shows where the two new columns land relative to it and what the new section looks like. See
    `reference/workflow-diagram-factory-v1.md` row 4 for this stage's underlying mandate (classify each
    current-state node as retained / agent-taken / human-retained / removed-with-reason / other; map
    agent-taken activities to a current antecedent or explicit capability, with authority) — this template
    extends that existing per-node disposition, it does not replace it.

  WHAT THIS IS NOT
    NOT a new artifact class and NOT a machine-only sidecar file. The transformation map has always been,
    and remains, prose markdown with pipe-tables — every real run (internal and client builds) is a `.md`
    file, not YAML/JSON. NOT the place a derived spectrum-placement value is stored: the placement
    `checks/check-transformation-map-shape.sh` computes is printed in that script's own report, never
    written back into this artifact as a stored field (Question 2 ruling, plan v3 §A6).

  HOW TO READ THE TOKENS
    {{ CURRENT_STATE.* }}   — filled from the cleared current-state graph this run already has.
    {{ REMIT.* }}           — filled from the ratified remit/authority gate content for this run.
    <describe: ...>         — a prose slot the brief-drafter's / executor's instantiation fills from a
                              named source, exactly as the sibling `build-spec.template.yaml` convention.
    [BASE_DISPOSITION]      — placeholder header name for whichever base taxonomy this run already uses
                              (e.g. `Disposition`, `Category`, `Primary classification`) — NOT invented
                              here; instantiate with the run's own existing column name and values.

  THE HONESTY BAR (binding on every instantiation, same convention as build-spec.template.yaml)
    Every committed `Shape Classification` / `Sub-Agent Group` / `Basis` / `Handoff out (gating decision)`
    value traces to the current-state graph the base-disposition column already cites. The evidence state
    (Observed / Assumed / Contradictory / needs_more_information) is folded into the `Basis` / gating-
    decision / existing detail cell as prose — the convention every one of the three calibration cases
    already uses (the reference agents' Notes columns embed evidence-state language inline; dogfooding's Detail
    column does the same) — never a fourth structured evidence-state column. Invent nothing: an
    unclassifiable row is a setup problem to flag, never a guessed classification.

  PLACEMENT ONLY FOR AGENT-TAKEN ROWS
    `Shape Classification` and `Sub-Agent Group` are populated ONLY for rows whose base-disposition value
    denotes agent-taken work (the 3-entry lookup `checks/check-transformation-map-shape.sh`'s
    `AGENT_TAKEN_LOOKUP` constant uses: `agent-taken` / `deterministic-code-executed` / `A` or
    "agent evidence/reasoning contribution"). Every other row (retained / removed / unchanged / human-
    retained / analyst-retained / escalation-only) carries `n/a` in both new columns.

  RESHAPE FIRST, THEN ASSIGN (0400)
    The map first reshapes the work as named moves that cite client evidence (`## Reshape moves`, `### No
    reshape`), states what happens to every ending today's process reaches (`## Outcomes`), and only then
    assigns each reshaped step in the per-node table (its `Fate` column) and the Write grants table.

  The instantiated artifact must pass `checks/check-transformation-map-shape.sh` (invariants 1-6, the
  reshape rules RS1-RS5, the Write grants rules R2-R7 and R3b and the quiet records in section 5, + the
  derived spectrum-placement report). This template is criteria-less by construction: the grading key
  for this stage's own in-pipeline challenge loop is withheld and is never handed to the proposer or
  opponent.
-->

# Transformation map — <describe: the agent's plain-language name> (v1)

Present-to-future disposition of every step in the cleared current-state graph
(`{{ CURRENT_STATE.path }}`, `{{ CURRENT_STATE.node_count }}` nodes), bounded by the ratified
remit/authority gate `{{ REMIT.event_id }}`. This map is the fixed semantic input to the future-state
graph; it is not itself a diagram and does not alter the current-state graph.

---

## Reshape moves

_Required, with exactly this heading and header. One row per move. A move cites client evidence: at least one
snapshot source id or issue id, never step ids alone._

| Move | Kind | Current steps | Future steps | Evidence | State | How the outcome is kept |
|---|---|---|---|---|---|---|
| `RM-1` | <describe: one of the eight move kinds below> | <describe: current-state node ids, comma-separated, or `new`> | <describe: future-state node ids> | <describe: snapshot source or issue ids, e.g. `ISS-3`, with a short reason> | `Assumed` \| `not adopted: <reason>` | <describe: how every outcome this work reaches is still reached> |

**Move kinds** (exactly these eight, lower case): `route at entry` · `consolidate repeated reads` ·
`package handoffs` · `improve decision quality` · `consolidate exceptions` · `remove` · `merge` · `reorder`.
Move ids are `RM-<n>`. State is `Assumed`, or `not adopted: <reason>` for a move the future state does not carry.
`remove` may drop any step the evidence shows adds nothing to the outcome, except a `control` step (see
`## Why people stay`).

### No reshape

_Required. One bullet per move kind that has no move above; nothing is left unsaid._

- <describe: move kind>: <describe: why the evidence supports no move of this kind>

## Outcomes

_Required. One row per ending today's process reaches. Several current endings may share one future ending._

| Current ending | Future ending | Reason and evidence |
|---|---|---|
| <describe: current-state end-event id> | <describe: future-state end-event id> \| `retired` | <describe: why this ending is kept or retired, citing evidence> |

---

## 1. Per-node disposition table

_The `[BASE_DISPOSITION]` column is this run's OWN existing base-taxonomy column — instantiate it with
that run's real header name and values (do not invent a fourth taxonomy). The two right-hand columns are
this handoff's addition._

| Node/`step_code` | Title | Systems | `[BASE_DISPOSITION]` | Shape Classification | Sub-Agent Group | Detail/Notes | Fate |
|---|---|---|---|---|---|---|---|
| <describe: node id> | <describe: title> | <describe: systems touched> | <describe: this run's base-taxonomy value> | `script-eligible` \| `judgment-required` \| `n/a` (non-agent-taken row) | a short group id (e.g. `unit-1`) \| `n/a` | <describe: evidence-grounded detail/notes, folding in the evidence state as prose> | `kept` \| `assigned` \| `merged into RM-<n>` \| `removed by RM-<n>` \| `split by confirm/enter` |

**Fate** (the per-current-step trace): exactly one row per current-state node, giving what happened to it —
`kept`, `assigned`, `merged into RM-<n>`, `removed by RM-<n>`, or `split by confirm/enter` (the assign-pass rule:
the person confirms the values, the agent enters them).

**Shape Classification anchor** (per the ratified script-eligibility test — `design-log.md`, "Decisions
ratified this session"): `script-eligible` if the node's work is a string match, a lookup, or a basic
arithmetic operation, fully specified by what the current-state graph shows the person doing there.
`judgment-required` for anything else — fuzzy matching, weighing conflicting evidence, or discretion the
documentation can't fully specify. Present only for agent-taken rows; `n/a` for every other row.

**Sub-Agent Group anchor**: `n/a` for every `script-eligible` row (a script has no judgment unit to belong
to) and for every non-agent-taken row. A group id only for `judgment-required` rows that cluster per the
ratified sub-agent boundary rule below.

### Tally (reconciles to the current-state node count)

<describe: a per-base-disposition-value tally, exactly as the calibration-case maps already do, counted
programmatically against the graph's node count, not by eye>.

---

## 2. Sub-Agent Groups

_Placed directly after the per-node table, before any existing "New future-state additions" /
"Judgment-model statement" section — mirroring where the dogfooding map's own judgment-model statement
already sits. One row per group id used in the per-node table above; every group id used there must appear
here exactly once, and every group id here must be used at least once above (no orphaned or unused group)._

| Group ID | Member nodes | Handoff in | Handoff out (target) | Handoff out (gating decision) | Basis |
|---|---|---|---|---|---|
| <describe: a short group id> | <describe: the judgment-required node/step ids in this unit, comma-separated> | `entry` \| `person` \| a named upstream Group ID | `person-terminal` \| a named downstream Group ID \| `terminal` | required whenever target is `person-terminal` or a named group; `n/a` only when target is `terminal` — name the specific decision that gates the next step | evidence citation grounding why this cluster is one unit, per the current-state graph — never assumed |

**Sub-agent boundary rule** (ratified, `design-log.md`, "Decisions ratified this session"): a new boundary
exists only where a run of agent work (one LLM call or several chained with deterministic gates) ends in a
handoff to a person **whose decision gates a different, subsequent unit** of agent work. A person sending
work back to the *same* unit for rework is not a boundary — this is a write-back interaction at the
interface, not a second sub-agent, and is never listed as a `Handoff out (target)` row.

`person-terminal` means this unit's work ends with a person and no further declared agent unit follows —
a genuine end point. If this unit's work in fact hands to a person whose decision gates a different,
subsequent agent unit, the target MUST name that unit's `Group ID` directly — never a bare `person` value
(off-schema; disambiguated in this handoff specifically to prevent a bare `person` standing in for both
"this ends here" and "this continues into another unit").

---

## 3. New future-state additions beyond the current-state graph (if any)

_The additions list: one row per planned future step that has no current node. Added by is the move id or the
assign-pass rule. Counted separately and never reconciled against the current-state node count above._

| Future step | Added by |
|---|---|
| <describe: future-state node id> | `RM-<n>` \| `confirm/enter` |

---

## 4. Judgment-model statement

<describe: whether any LLM/judgment step exists in this map at all, and if so, what it is — grounded in
the per-node table above, never asserted independently of it>.

---

## 5. Authority boundary

<describe: what the agent may write to / act on, and what stays retained by a person, from the ratified
remit/authority gate content>.

### Write grants

_Required, with exactly this heading and header. One row per distinct write the future state makes (a step may
share a grant with others). A design with no system writes still has the table, holding one row per notice or
handoff: every agent at least hands its output to a person. Every other artifact references a write by its Grant id._

| Grant | Steps | System | System of record | Key control | Write kind | Who writes | Route | Exception reason | Next rung | Step-up evidence | Access | Recipient |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `WG-1` | <describe: current-state node ids, comma-separated, or `new`> | <describe: system name as in the snapshot, or `the person` / an agent unit id> | `yes` \| `no` \| `unclear` | `Yes` \| `No` \| `unknown` | `notice` \| `draft` \| `handoff` \| `own-file` \| `mechanical` \| `decision-bearing` | `agent` \| `person` | `api` \| `mcp` \| `screen` \| `n/a` | `none` \| `own-working-surface` \| `reversible-reviewed` | <describe: the next autonomy-ladder step, or `n/a`> | <describe: the basis, naming reversibility, checkability, key control or inherited error cost; or `n/a`> | `Assumed` | `internal` \| `outside` \| `unclear` (notice rows) \| `n/a` |

**Column guide.**
- **Grant**: `WG-<n>`, n a positive integer, unique within the map (gaps allowed); ids are global across a process set.
- **Steps**: current-state node ids, comma-separated, or `new` for a future-state addition.
- **System**: the system name as in the snapshot, or `the person` / an agent unit id for handoffs and notices.
- **System of record**: `yes` · `no` · `unclear` (the authoritative store; `unclear` is treated exactly as `yes`).
- **Key control**: `Yes` · `No` · `unknown`, from the step's process `key_control` attribute: `Yes` only when the
  snapshot marks it `true`; a process with no mark is `No` (an older `unknown` is read as `No`, nothing listed for it).
- **Write kind**: `notice` (report or notify the person) · `draft` · `handoff` · `own-file` (the agent's own working
  files) · `mechanical` (values nobody chose: confirmed by a person, or copied unchanged from a named source) ·
  `decision-bearing` (a value, submission or status change the writer chose). The agent makes both kinds by default.
- **Who writes**: `agent` · `person`.
- **Route**: `api` · `mcp` · `screen` (operating screens or a browser) · `n/a`.
- **Exception reason**: `none` · `own-working-surface` · `reversible-reviewed`; descriptive only, never needed for the
  agent to write.
- **Next rung**: free text, or `n/a`; required when Who writes is `person`.
- **Step-up evidence**: free text naming at least one of reversibility, checkability, key control, inherited error
  cost, or the row's reason from `## Why people stay` (`control`, `outside party`, `client statement`); or `n/a`.
  Required when Who writes is `person`, or when Exception reason is not `none`.
- **Access**: always `Assumed`; nothing else is valid.
- **Recipient**: `internal` · `outside` · `unclear` on `notice` rows (who receives the notice), `n/a` on every other
  row. This 13th column is the one ruled amendment to 0399's pinned Write grants table.

**Rules** (`checks/check-transformation-map-shape.sh` reports each; only the two safeguards go to the stage's repair,
and nothing here stops a run):
- Safeguard. An agent never takes a `control` step (the approval, sign-off or submission itself) and never writes on
  a `control` or `outside party` step; a notice with Recipient `outside` is a person's.
- R1. Retired: a `decision-bearing` record write by the agent needs no Exception reason; agents write by default.
- R2. In a process marked Key control `Yes`, the record writes carrying a `control` step are a person's.
- R3. Who writes `person` needs a Next rung (not `n/a`, not empty) and Step-up evidence naming its basis: one of
  reversibility, checkability, key control, inherited error cost, or the row's `## Why people stay` reason.
- R3b. An Exception reason other than `none` states its basis in Step-up evidence (a note, never a fail).
- R4. Access is exactly `Assumed` on every row.
- R5. Every enum cell is in its enum; Grant ids are unique and well-formed.
- R6. This section exists.
- R7. The table carries the `Recipient` column, each value in its enum for the row's Write kind.
- A person row with no `## Why people stay` reason, a `mechanical` or internal `notice` row kept with a person, and a
  `person` row whose Next rung restates the default are quiet records for the run record and the ambition audit.

List each row with System of record `unclear` or an Exception reason other than `none` in section 7, What We Still
Need to Know.

## Why people stay

_Optional; include it whenever a person keeps a step. One row per current or future step kept with a person. A person
stays only for one of three reasons; every other step is the agents' work._

| Step | Process | Reason | Why |
|---|---|---|---|
| <describe: current- or future-state step id> | <describe: process id> | `control` \| `outside party` \| `client statement` | <describe: the evidence; for `client statement`, the snapshot source id stating the rule or reason a person must do it> |

- `control`: in a process the Work Brain marks key control (`true`), the approval, sign-off or submission itself and
  every record write carrying it.
- `outside party`: anything sent to or written into a party outside the organisation, any write kind.
- `client statement`: the snapshot or a client-quoted direction item states a rule or reason that a person must do it
  (a policy, a requirement, a stated must-not), not merely that a person does it today; Why cites the source id.

Where a step's reason is unclear, take the best-supported reading, say why in Why, and continue.

---

## 6. Retained human controls

<describe: what a person still does in the future state, citing the specific unchanged/retained rows
above>.

---

## 7. What We Still Need to Know

<describe: unresolved evidence gaps this stage surfaced, each named as a gap-closure route (document
upload / capture / FDE intake) — never a silently resolved guess>.

---
_Built by the executor role, from the cleared current-state graph and the ratified remit/authority gate
content only, per `templates/transformation-map-brief.template.md`. No review brief, rubric, or grading
document was read to produce this artifact._
