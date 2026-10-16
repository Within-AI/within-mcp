# Discover recurring work

Use this guide during Stage 1 to find recognizable work hypotheses and during Stage 2 to investigate the selected hypothesis. The unit of recommendation is useful work with a recipient and a stopping point. Work Brain process entries organize evidence; they do not automatically define agent boundaries.

## 1. Fix the scope and keep a coverage ledger

Start from the user's objective, north star, constraints, accepted solution types and requested scope. Reuse answers already supplied. Record the workspace, snapshot time, process/version IDs, retrieval route and scope exclusions.

Discover the available MCP capabilities rather than assuming tool names or response schemas. Preserve original responses or durable references in the supporting evidence artifact. Never invent access, process data or observation content.

- Enumerate the requested hierarchy and reconcile process IDs against the resulting inventory. Exhaust pagination where the request requires complete coverage. Search results alone do not establish an exhaustive inventory.
- Give every in-scope process a disposition: reviewed, zero observations, awaiting evidence, inaccessible, or excluded with a reason. Retain draft entries separately where the source distinguishes them.
- Track coverage by stage: listed; details retrieved; observation records enumerated; narratives read; source spans inspected; work units extracted; relationships reviewed. Retrieval is not inspection.
- Record expected and returned counts, pagination completion, truncation, failed requests and unresolved gaps. A count in process metadata may exceed the records returned in one response.
- An exhaustive-narrative request requires reading all available narratives in scope before claiming that census complete. An inventory-only or sampled request permits lighter inspection, with the limit stated explicitly.
- Completion describes coverage of the requested evidence snapshot, not all work performed in the company. Missing or uncaptured work remains possible.

Persist the coverage ledger as work progresses. Resume unfinished stages rather than repeating successful retrievals. If the user changes scope, record the change and reconcile the ledger.

## 2. Extract useful work before proposing a solution

For each inspected narrative or source span, extract the following where supported. Keep unknowns explicit.

| Element | Question |
| --- | --- |
| Trigger and actor | Who does this, after what event, and for whom? |
| Inputs | Which records, files, conversations or prior outputs are used? What are their object and version identities? |
| Transformation | What useful change is made to those inputs? |
| Standard and context | Which schema, playbook, intended behavior, expertise or author-supplied intent governs the result? |
| Output and recipient | What is produced, where does it go, and who uses it? |
| Check and authority | How is it accepted or corrected, and which decision remains with a person? |
| Current assistance | Is this manual work, existing automation, agent setup, an invocation, or maintenance? |
| Recurrence and repetition | Which transformation repeats across independent cases, people or teams? What stays stable, what varies, and how often does it occur? |
| Variation and friction | What differs across cases, what failed, and what required corrective work? |
| Evidence | Which process, observation, session, source span and artifact support each material claim? |

A work-unit record is an analytical extraction, not a distinct business execution. One narrative may describe several work units; several narratives may describe the same event. Keep source memberships many-to-many.

Split a broad process where outputs, recipients or authority differ. Preserve a single useful transformation that appears under several process names. Do not force a merge because two cases share an application, department, agent name or similar wording.

Use intuitive descriptions such as “After a customer call, a salesperson updates the qualification fields from the conversation.” Mask process titles and existing agent labels during initial grouping where practicable, then restore them for traceability. Title masking reduces one source of bias; it does not make already-assisted traces independent validation.

Distinguish setup, execution, accepted output and repeated adoption. A configured schedule does not prove delivered throughput; an agent build record does not prove recurring user demand. Capture the underlying work separately from problems in the current assistance.

## 3. Resolve identity before adding counts

Maintain an identity ledger linking raw observation IDs to sessions/spans, business objects, versions and work units. For candidate pairs, record one of: same event, retry, repeated projection, separate event, or unresolved, with the evidence and reasoning.

- Prefer corroborated business-event or object/version identity, session continuity, ordering and narrative context. No single field is universally sufficient.
- A content hash or matching description is a duplication cue, not proof of event identity. Two real events can have identical text; one event can have different summaries.
- Keep batches and their member items distinct. Count at the unit relevant to the proposed benefit, and say which unit it is.
- Retain retries as evidence of friction even when they belong to one business event. Do not add them as independent demand.
- When combining processes, deduplicate repeated observation IDs and supported shared episodes. Preserve each process's contribution and original counts.
- Do not assume independence where identity cannot be resolved. Report the independent-event count as unknown, or a supported range with its derivation.

## 4. Search across processes and validate relationships

Start with candidate transformations, not a preselected agent architecture. Search the whole authorized scope, including other teams, using input/output terms, object IDs, artifact names, versions, standards, corrections and acceptance signals. Semantic similarity can nominate relationships; it cannot validate them.

| Relationship type | Required basis | What it supports |
| --- | --- | --- |
| `analogous_work` | Different cases share a transformation, useful output and compatible standard; material domain differences are retained. | A reusable capability across separate jobs. It is not one end-to-end execution. |
| `shared_knowledge` | Several bounded requests rely on the same maintained authoritative corpus or playbook. | A shared retrieval capability with explicit invocation boundaries. It is not authority over an entire domain. |
| `observed_handoff` | A specific producer output becomes a consumer input, supported by corroborated object/version identity, ordering and narrative or source continuity. | A locally observed workflow connection, preserving owners and acceptance boundaries. |
| `proposed_bridge` | A connection could be useful, but transfer, identity or continuity has not been established. | A question or experiment. It must not be presented as an observed workflow. |

For each edge retain its ID, type, source and target work units/processes, evidence references, material differences, limitations and any unresolved identity question. A rejected link should retain its reason so later passes do not quietly recreate it.

Investigate in both directions:

1. **Forward:** where is this output consumed, by whom, and what useful next step follows?
2. **Backward:** what sources, standards and checks produced an accepted artifact?
3. **Across:** which other cases repeat the same transformation or depend on the same knowledge?
4. **Against:** which apparently similar cases have incompatible standards, different recipients or no actual transfer?

Revise the boundary as evidence arrives. Split false groupings, downgrade unsupported handoffs to proposed bridges and merge duplicate hypotheses. One connected graph may contain several incompatible jobs; one broad knowledge skill may serve many bounded jobs. Neither breadth nor connectedness decides the solution boundary.

**Set map.** When a hypothesis spans two or more processes, record how they fit together as `current_state.set_map` ([data model](html-data-model.md#set-map-current_stateset_map)). Read the set as stages and variants in the life of shared work objects, not as peers side by side:

- **Work objects and states:** each object the set creates, hands over or waits on, the states it passes through, and which process, context neighbour or actor moves it into each state.
- **Links beyond the four relation types:** several processes producing the same object in the same state are a `variant_entry` (record the routing rule, or an open point when none is recorded); a later process taking the object over after a wait is a `lifecycle_continuation`; a parent object waiting on a child is `parent_child`; an upstream check deciding whether the set runs is a `decision_gate`; a later state restarting earlier work is `re_entry`; two processes doing the same work on the same object is an `overlap`, left open rather than resolved.
- **Evidence strength:** shared object instance identifiers in both processes' observations are strongest, then a step writing the state another's trigger reads, then trigger and end text, the same capture session, the dependency field (an undirected hint; check its direction against the flow), and hierarchy position. A shared team or system is context only.
- **Context neighbours:** processes outside the set that touch it (an upstream gate, a parent flow, the continuation that resolves a wait, upkeep), each attached where it touches. Name them by resource key when the evidence gives one.
- **Actors:** performers that move a state or write an object but are not processes (an outside party such as a vendor or a customer, a rules automation, an AI automation), recorded once in `actors` with their kind.
- **Open points:** each link or state the evidence does not settle, with its evidence state.

Preserve process memberships and typed relationships through every export. The HTML can summarize the current state, but its supporting artifact must retain the full relevant ledger. Verify the exported candidate against source memberships before rendering; a correct renderer cannot restore relationships omitted upstream.

## 5. Describe repetition and coverage honestly

Keep these quantities separate for each hypothesis and each contributing process:

- Raw observation records available and records retrieved.
- Full narratives actually read.
- Unique source spans actually inspected, with their definition and references.
- Supported distinct business events, or unknown; retries, batches and repeated projections.
- Capture time window, completeness and known selection mechanism.
- Confirmed performers, or unknown. Contributors to documentation are not necessarily performers.
- Variation coverage and friction-bearing records, with their definitions.

Raw observation counts can prioritize dense evidence for inspection and flag thin support. They do not establish independent recurrence, organizational frequency, representative variation or agent suitability. A single case may reveal a promising work shape; many records may repeat one episode.

Describe cadence only to the level supported: “after a prospect meeting” or “a weekly forum is recorded” may be justified when “happens 52 times a year across the company” is not. Label source-generated frequency, volume and time attributes as such until corroborated. A configured trigger is not an observed run.

Estimate an observed event rate only when distinct eligible events and an adequate time window are established. State the denominator and capture limitations. Organization-wide extrapolation additionally needs coverage and performer information. Do not multiply raw records by guessed minutes to create savings.

Keep confidence separate in work shape, recurrence, variation coverage, output-quality evidence, access and net value. Do not treat a capture-depth band as proof of any of the others.

### What counts as an exception or friction?

A friction-bearing record reports a blocker, retry, unexpected result or corrective work. Routine approval, ordinary iteration, a defect that is simply the input to debugging, or an uncaptured outcome is not automatically a process failure.

Separate model interpretation problems, missing context, integration failures, normal case variation and human decisions. Label counts as record counts; do not turn them into failure rates without distinct eligible events and outcome ascertainment. If only a targeted subset was coded, report that subset or a conservative minimum, not an exhaustive exception count.

### Test the observation-count proxy

Treat two links as separate hypotheses: records predict independent recurrence; recurrence predicts useful assistance after costs. Neither follows automatically from the other.

1. Define the business-event unit, eligible population, capture window, coverage and actors.
2. Reconcile records to independent events without using suitability labels.
3. Have reviewers assess work shape while masked to counts where practicable.
4. Compare raw counts with actual occurrences, then with independently assessed fit and measured assistance outcomes when trials exist.
5. Hold out whole episodes or families to prevent duplicate leakage; report uncertainty and capture differences.

Purposive examples can disprove a direct equivalence or expose confounders. They do not estimate population correlation, establish zero correlation or justify a learned ranking weight. Keep this evaluation distinct from routine recommendation production.

## 6. Deepen adaptively and stop with a clear disposition

Maintain separate queues for promising hypotheses, underrepresented or uncertain work, and negative/boundary controls. High count is one inspection cue, alongside objective relevance, potential value, missing context and ambiguity. Do not inspect only current favorites.

For each material uncertainty, request the smallest useful evidence: a distinct case, difficult attempt, reviewer correction, upstream source, accepted downstream artifact, governing standard or proof of a handoff. Explain which decision it would change. Reuse evidence already available and do not ask for an arbitrary number of artifacts.

Include controls such as fixed transfers, high-count coordination, integration repair, setup-only traces, missing intent and apparently similar work with different standards. These are comparisons to challenge the method, not a fixed quota or a presumption that such work cannot benefit from assistance.

Record each loop as: question → retrieval or owner request → inspected evidence → changed boundary/confidence → remaining decision. Use bounded retries for unavailable sources. Exhausted retries are not evidence inspected.

Stop a candidate investigation when its useful boundary, material alternatives and next test are supportable; when another read is unlikely to change the decision; or when a specific inaccessible fact blocks progress. Candidate stopping does not cancel an explicitly requested exhaustive coverage obligation.

Return an explicit disposition: hypothesis awaiting feedback; investigate a named gap; reuse or repair existing help; compare plausible assistance; retain human work; or reject with a reason. Stage 1 ends with the mandatory user-feedback and artifact request defined by the root skill. Do not turn a well-documented hypothesis into an approved design without that feedback.
