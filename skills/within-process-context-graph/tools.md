# Within MCP — PROD Tool Catalog

This is the operational reference for every tool the Within MCP exposes to a
customer's AI agent in production. **All tools here are read-only** — the MCP
never mutates workspace state.

The point of this catalog is not to enumerate APIs — it's to help an agent
**pick the right tool for the goal at hand** when working inside a customer's
Within workspace.

## How agents should think about Within

Within is a process intelligence platform that captures **how work
actually happens** inside a customer's organization (Companion + Interviewer),
organizes it into a living **Process Index — Within's context graph of how
work happens** (Structure), and helps customers improve via Advisor + Signals
(Improve). When an agent calls the Within MCP, it is reading from that living
map.

The agent's job is usually one of:

1. **Understand current state** — what process exists, who runs it, how, with
   what evidence.
2. **Explain or summarize** — answer a customer question grounded in the
   workspace, not generic knowledge.
3. **Trace dependencies / impact** — what feeds this, what is downstream, what
   breaks if it changes.
4. **Surface what the evidence shows** — describe duplication, exceptions, and
   inconsistencies across processes as observed facts, not recommendations.

All these workflows reduce to: **find the right process(es) → fetch detail →
gather evidence → traverse relationships → synthesize**. The tools below are
grouped by where they fit in that flow.

---

## A. Standard MCP entry points

The default entry path. Most ChatGPT / Claude / standard MCP clients will start
here.

| Tool | When to use |
|---|---|
| `search` | First call for almost any process question. Semantic search over the customer's process index. **Iterate** — single queries rarely cover broad topics, and processes use organization-specific names. |
| `fetch` | Pull full details for one process by ID returned from `search` or hierarchy navigation: placement in the index, current version (steps, policies, inputs/outputs), dependencies, history. Fetch dependency IDs to walk relationships. |

---

## B. Process Index — discover & navigate

Use these when `search` results feel sparse, or when you need richer process
metadata than `fetch` returns.

| Tool | When to use |
|---|---|
| `search_processes` | Search processes by name, objective, team, or semantic similarity — ranked keyword + semantic results. Use for an exact phrase or team filter as well as broad topic search. |
| `get_process_hierarchy` | Browse the whole index tree structure (nodes with name, type, children). Use when search misses, or to orient before drilling in. Supports `max_depth` / `root`. |
| `get_hierarchy_node_details` | Inspect a single hierarchy node — parent, children, linked process. |
| `get_process_details` | The rich nested payload: `current_version`, `dependencies`, `hierarchy_node`, optional version history. This is what `fetch` reshapes. Use directly when you need the structured shape, or narrow with `scope` (any of `attributes`, `tasks`, `observations`, `dependencies`, `linked_artifacts`, `policies`, `history`; defaults to `attributes`). |

---

## C. Process change & evidence

Customer asks "what changed?", "what happened?", "why does this run this way?"
Use these to pull the evidence trail behind a process.

| Tool | When to use |
|---|---|
| `get_recent_process_changes` | Workspace-wide version-change feed. "What has been edited recently?" |
| `get_process_observations` | Recorded executions of one process, surfacing the friction signals (deviations / exceptions captured by Companion). `verbosity="summary"` to scan; `verbosity="full"` for the narrative. Optional version filter. |
| `get_observation_citation` | The actual session timeline behind an observation: what the user did, when. The most "primary source" evidence Within has. Only works for your own sessions. |

---

## D. Artifacts — the source evidence

Artifacts are the underlying documents, recordings, and source files behind
processes (BRDs, SOPs, recordings).

| Tool | When to use |
|---|---|
| `search_artifacts` | Hybrid semantic + lexical search across artifact text chunks. Returns short snippets. **Start here** for artifact discovery. |
| `get_artifact_details` | Artifact metadata and relationships. |
| `get_artifact_content` | Artifact text — `mode="preview"` for the first ~15k tokens, or `mode="range"` with `start_line` / `end_line` for numbered lines (~5k-token cap) when you need to cite specific lines. |
| `search_artifact_text` | Within one artifact (by `resource_key`), find matches with surrounding context. |

---

## E. Workspace navigation

| Tool | When to use |
|---|---|
| `list_accessible_workspaces` | List the customer's workspaces and which one is currently active. |

The connection is scoped to a single workspace. Switching workspace is an
auth-level action (reconnect via OAuth, or use a key generated in the other
workspace), not something the agent does at runtime.

---

## F. Sessions

| Tool | When to use |
|---|---|
| `list_sessions` | Recent workspace sessions as supporting evidence — the processes each updated, its duration, and who created it. |

---

## Tool-selection patterns by goal

**"Tell me about process X"** → `search` → `fetch`. If sparse: `get_process_hierarchy` to browse, then `fetch` the right leaf.

**"What is the evidence for Y?"** → `fetch` (process) → look at policies in current_version + observations → `get_observation_citation` for the actual session that produced an observation.

**"What changed recently?"** → `get_recent_process_changes` → drill into specific processes via `fetch`, then `get_process_observations` for the deviation detail.

**"What depends on Z?" / "What is the impact?"** → `search` for Z → `fetch` + `get_process_details` to read its `dependencies` field → `fetch` each upstream and downstream process to walk the chain. For impact-critical answers, also pull observations on the dependents.

**"Help me understand our P2P value stream"** → `get_process_hierarchy` (root: P2P node) → for each leaf: `get_process_details` → read steps, dependencies, and observed exception patterns. Describe the current state with evidence.

---

## Operating principles for agents

1. **Iterate on search.** Single queries miss. Always be willing to refine 2–4
   times before falling back to hierarchy browsing.
2. **Stay grounded.** Cite Within evidence when available (process IDs,
   artifact IDs, observation timestamps). Do not invent facts the workspace
   does not support.
3. **Separate observed from inferred.** "Observed: X happens at step 3."
   "Inferred: this is likely a duplication of Y based on similar steps in Z."
4. **Call out gaps.** If the workspace does not have evidence, say so plainly.
   That gap is itself useful information for the customer.
5. **Do not expose internal IDs unless needed for a follow-up.** Resource keys
   are for tool calls, not user-facing prose.
6. **Read-only.** Every PROD tool is read-only; the MCP never mutates
   workspace state.

---

## Worked end-to-end scenarios

Each scenario starts with the **outcome** the agent is trying to achieve, then
shows how to compose tools to get there. Use these as recipes — adapt the
exact sequence to the customer's specific question.

### Scenario 1 — Help a process performer do their job (the customer's actual way)

**Outcome:** "Help me do this task the way my team actually does it, not the
way ChatGPT thinks it's done."

A process performer (AP lead, ops manager, recruiter, analyst) has asked
their agent to do real work — vendor reconciliation, invoice approval, ERP
migration prep, audit walkthrough. Pull the customer's existing process
before improvising.

1. `list_accessible_workspaces` — confirm the agent is in the right
   workspace (if not, the user must reconnect to switch — the agent can't).
2. `search` — query the task in the user's words ("vendor invoice
   reconciliation"). Iterate 2–4 times with refined queries — single queries
   rarely cover broad topics.
3. If still sparse: `get_process_hierarchy` — orient on the value
   stream (P2P, O2C, close cycle, etc.) and find the right leaf.
4. `fetch` — pull the matched process. Read steps, policies, inputs,
   outputs, and dependencies.
5. `get_process_details` — go deeper if `fetch`'s shape is not enough
   (e.g., need the full version metadata or all dependency IDs to walk).
6. `get_process_observations` — exception patterns observed for this
   process across versions; surface new edge cases the user should know about.
7. `get_observation_citation` — drill into the most recent or
   most relevant exception session for primary-source evidence of how the
   process actually runs in practice.
8. Optional: read the `dependencies` field already in `get_process_details`,
   and `fetch` an upstream or downstream process if the user's task touches
   the boundary (e.g., "what should I check before I close this out?").
9. Synthesize: "Here's how your team does this, here's the deviation
   pattern from last quarter, here's what to watch for."

### Scenario 2 — Give a manager a state-of-the-team report

**Outcome:** "Give me a state-of-the-team report on the processes we own —
what's running, what's changing, what's deviating, what needs my attention."

A team manager is checking in on the processes their team owns. The agent
should produce a concise, evidence-backed brief that the manager can act on.

1. `list_accessible_workspaces` — confirm workspace.
2. `get_process_hierarchy` (root: their team's value stream node) —
   pull the team's part of the index.
3. `get_recent_process_changes` — what's been edited recently across the
   workspace, filtered to processes under the team's tree.
4. For each high-volume or recently-changed process:
   - `get_process_details` — current state, dependencies, version label.
   - `get_process_observations` (`verbosity="summary"`) — recent
     observation summary and emerging deviation patterns.
5. Spot-check the top 2–3 emerging deviations:
   `get_observation_citation` — drill into the actual session
   for primary-source detail.
6. Synthesize: "Your team owns N processes. M changed in the last 30
   days. K are showing deviation patterns worth your attention. Here are
   the top 3 with evidence."

### Scenario 3 — Understand a process or value stream in depth

**Outcome:** "Help me understand process X (or value stream Y) — how it runs
today, what feeds it, what depends on it, and what the evidence shows."

The customer wants deep, evidence-grounded understanding of a target. Describe
the current state with its supporting evidence.

1. `list_accessible_workspaces` — confirm workspace.
2. `search` → `fetch` (or directly `fetch` if the ID is known) — pull
   the target process.
3. `get_process_details` — full nested payload: current version, all
   dependencies, hierarchy node, version history.
4. `get_hierarchy_node_details` — context within the index
   (parent value stream, sibling processes that may share patterns).
5. `get_recent_process_changes` (filtered to this process if possible)
   — version evolution. What has the team been changing? In which
   direction?
6. `get_process_observations` — all observations across versions. Look
   for repeated deviation patterns, exception types, manual workarounds.
7. For 3–5 representative observations:
   `get_observation_citation` — primary-source detail of how
   the process runs in practice (not how it's documented).
8. **Walk the dependency graph in the index.** The `dependencies` field
   already in `get_process_details` lists upstream and downstream
   processes by ID. For each, `fetch` + `get_process_details`. Read
   their steps, their own dependencies, and their recent observations.
   Repeat for as many hops as the question needs.
   - **Upstream:** what feeds this, and the quality of those inputs.
   - **Downstream:** what would be affected if it changed.
   - For the most-connected dependencies, drill one level deeper:
     `get_process_observations` and `get_observation_citation`.
9. **Cross-check sibling processes.** From the
   `get_hierarchy_node_details` payload returned earlier,
   `fetch` two or three sibling processes under the same value stream
   and note where they share patterns, controls, or systems.
10. Synthesize a current-state readout:
    - **Current state:** how the process actually runs (cite
      observations).
    - **Connections:** upstream feeders + downstream dependents.
    - **Observed patterns:** deviations, manual steps, duplication signals,
      with evidence.
    - **Gaps:** what evidence is missing or thin.

### What these scenarios share

- **Iteration is normal.** Every scenario uses 2–4 search refinements before
  drilling. Don't bail after one query.
- **Composition is the value.** No single tool answers a real question. The
  agent's job is to chain reads — search → process → version → observation
  → activity timeline → dependencies → sibling processes — until it has
  enough to answer with confidence.
- **Always ground synthesis.** Cite process IDs and observation timestamps.
  Never invent.
- **Parallelize wide scans.** When understanding a whole value stream, multiple
  agents can fan out across the hierarchy. The MCP is stateless per call —
  there's no penalty for parallel reads.
