# Coverage manifest: the agent's internal DAG

_Agent: `marketing-metrics-pack-refresh` · internal handle: `dag-coverage-manifest`._

**Source of truth:** `internal-dag.graph.json` (sha256 `70ae864860649f7c9226a73692897c324b79c8dff19f5faba56415d3155093de`).

**Policy:** coverage, not count. Every node, link and interface maps to a diagram element or a stated entry below; no silent drops. Derived deterministically by walking the graph (the same script that writes the diagram and `agents.yaml`). Left to right is control-flow order (longest path over `sequence` / `then` / `else` / `handoff` and every unfamiliar link kind between steps; dispatch, inbound, re-entry and access links excluded).

**Structure checks: PASS.**

**No sub-agents and no coordinator: this agent runs as one flow.**

## 1. Tally (reconciles with the graph)

**Nodes: 7 total.** `step` 4 · `escalation_gate` 1 · `review_seam` 2.

**Links: 8 drawn.** `sequence` 2 · `then` 1 · `else` 1 · `handoff` 2 · `read_access` 2.

**Interfaces: 6.** inbound_control 1 · read 2 · outbound_handoff 2 · draft 1.

**Handoffs:** 2 (declared 2).

**Positioned steps:** 7; a root_agent is drawn in the root band, not layered.

## 3. Stage coverage

No stage scope declared and no sub-agent owns stages; there is no stage table for this design.

## 5. Interfaces

| interface | id | kind | direction | seated in | anchor | evidence | design_ref |
|---|---|---|---|---|---|---|---|
| **Run start (what starts a run: a schedule or the owner's request)** | `IC-1` | control | inbound_control | `s-retrieve` | `s-retrieve` | needs_more_information | design pending |
| **Salesforce pipeline influence report (read-only report call)** | `IR-1` | read | read | `s-retrieve` | `s-retrieve` | Observed | design pending |
| **Google Slides, Sheets and Drive: accepted templates and prior-period pack (read-only)** | `IR-2` | read | read | `s-compute` | `s-compute` | Assumed | design pending |
| **Review copies and run log (script to the owner's review process)** | `IH-1` | handoff | outbound_handoff | `s-stage-1-recommend` | `s-stage-1-recommend` | Assumed | design pending |
| **Readiness escalation (script to the owner; unexplained discrepancies to the Performance Investigator)** | `IH-2` | handoff | outbound_handoff | `s-stage-2-escalate` | `s-stage-2-escalate` | Assumed | design pending |
| **Run-created Sheet and Slides review copies and run log (never an original or live file)** | `IW-1` | draft_surface | draft | `s-stage-1-recommend` | `s-stage-1-recommend`, `s-stage-2-escalate` | Assumed | design pending |

## 6. Structure checks

| check | result |
|---|---|
| ids are present and unique | pass |
| every link has both ends | pass |
| every named interface, state record, read and draft target exists | pass |
| every seating and anchor names a real element | pass |
| seated interfaces agree both ways | pass |
| each known interface is anchored to its steps | pass |
| sub-agent members agree with the steps | pass |
| handoffs match the declared count | pass |
| declared stage scope and interface set hold | pass |
| evidence states are one of the four | pass |

## 7. What the agent can change

None: the agent writes nowhere and holds no decision in this design.

## 8. Notes on this diagram

None.

## 9. Open items and standing assumptions

- `report-mapping` [kind: rule / needs_more_information] Whether Qualified Pipeline Generated and Closed-Won Influenced come from the one observed pipeline report or need further reports. → bound to `s-retrieve`, `s-compute`, `eg-1`; follow-on: Deepen each sub-agent; closure: document upload of the report list, or FDE Intake.
- `discrepancy-threshold` [kind: rule / needs_more_information] What makes a computed value an unexplained discrepancy that goes to the Performance Investigator. → bound to `eg-1`; follow-on: Deepen each sub-agent; closure: FDE Intake with the reporting owner.
- `promotion-owner` [kind: value / needs_more_information] Who approves promotion or publication of the reviewed copies. → bound to `rs-1`; follow-on: —; closure: FDE Intake.

| surface | id | access_state | basis |
|---|---|---|---|
| **Salesforce pipeline influence report** | `IR-1` | Assumed | report read access and tool coverage not yet confirmed; stamped Assumed until access verification |
| **Google Slides, Sheets and Drive templates and history** | `IR-2` | Assumed | no live credentials yet; stamped Assumed until access verification |
| **Run-created review copies and run log** | `IW-1` | Assumed | creating and writing copies in the owner's Drive is not yet granted; stamped Assumed until access verification |

**Terminology preflight** (FDE working vocabulary, Notion `3bbed0c6-ff0e-81c2-95ea-e819366aa08f`): banned-term hits in the rendered page, this manifest and the rendered YAML: **none**.

## 10. How this map fits the backbone

Source: `../../agent-backbone/simple/build-spec.yaml (sub_agent_surface, tool_permission_inventory, integrations, decision_rules)` (evidence_state Observed).

- **The declared surface.** The build-spec declares no sub-agent surface (sub_agent_surface.declared: false, primitive_form: script).
- **What this map realizes.** One flow band of code steps; no root_agent, no sub_agents. People's steps (screenshot, share link, distribution) stay outside the agent and are named in the owner's review seam.
- **Evidence-state lift.** Nothing to lift: no surface was declared.

| backbone element | realized in this internal DAG |
|---|---|
| `build_spec.sub_agent_surface` | declared false: realized as the single flow (s-retrieve to s-stage-1-recommend / s-stage-2-escalate) |
| `build_spec.tool_permission_inventory.must_not (all writes)` | no write edge or write interface; the review copies and run log are the draft surface IW-1 |
| `build_spec.integrations (access_mode: read)` | Salesforce as IR-1; Google Slides, Sheets and Drive templates and history as IR-2; access stays in assumptions |
| `build_spec.decision_rules (cap 3-8, room_left_for_deepen_stage)` | rule_set_ref values are references only; the computation and readiness rules are the deepen stage's |

## Coverage assertion

All 7 nodes, 8 drawn links and 6 interfaces map to a diagram element or a stated entry above. Structure checks: **PASS**.
