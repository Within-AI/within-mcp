# Deep web research

Use outside evidence to challenge and improve local work hypotheses. Research is complete when it supports a consequential comparison and next test, not when it fills a source quota. External success establishes plausibility; it does not establish local demand, tenant access, output quality or ROI.

## Two research questions

Keep these purposes separate in the evidence ledger:

1. **Comparable work:** what have credible operators or researchers learned about this input, transformation, standard, recipient and failure mode? Which approaches worked, failed, were abandoned or needed human intervention?
2. **Access and existing capabilities:** which source interfaces and native features could support the proposed work, and what does this user's environment actually permit?

Preserve the existing source-research discipline for the second question without treating a product capability page as evidence that the first question has been answered. The legacy source-research guide is an opt-in reference when a legacy assessment is requested; it is not a mandatory route for this workflow.

## 1. Discover available research routes

The planned MCP-accessible index of X API, web and podcast evidence is the preferred entry point **when it is connected and suitable**. Discover real tool names, descriptions and schemas using the environment's available tool-discovery mechanism. Inspect their declared search and source-fetch capabilities before calling them. Do not invent an index endpoint, tool name, parameter or claim of connectivity.

Record available routes, coverage and material gaps: source types, dates, live/cached status, lexical or semantic retrieval, author/date filters, pagination/truncation and original-source retrieval. Query only supported fields. Log actual tool arguments, result IDs and retrieval timestamps; never log API keys, tokens or credential-bearing headers.

If the index is absent, stale or missing a material source, use an available authorized web, podcast or X research route and disclose the substitution. If a requested route is unavailable, complete independent research and state exactly what remains unverified. An unavailable X API cannot be silently replaced by public web search while claiming an authenticated X search. Prior indexed API results, fresh API calls and public post rechecks are distinct provenance categories.

## 2. Form work-shape questions

For each material hypothesis write a search brief:

```text
Local job: actor / trigger / inputs / transformation / standard / output / recipient.
Proposed benefit and necessary assumptions.
Strongest counter-hypothesis and evidence that would support it.
Comparable settings and important local differences.
Decision this research could change.
```

Search for the work, not merely the agent or department name. “Transcript evidence into reviewed CRM fields” retrieves a more useful comparison than “best sales agent.” Use terminology variants and specific failure modes. Search for positive examples, failures, limits and ordinary-software alternatives. Do not let the initial proposed form factor determine which examples are eligible.

Use lexical/entity and semantic retrieval where available. Follow relevant original authors, linked experiments and referenced episodes. Deepen evidence that could change the decision; do not repeatedly broaden a query just to accumulate similar endorsements.

## 3. Read original evidence and its limits

Prioritize primary lab publications, empirical studies, specialist research and first-person operator accounts. Include relevant Anthropic, OpenAI and other expert organizations without treating their authority as independent validation. General vendor marketing and generic consultant advice are not sufficient support. A vendor's detailed account of its own operating workflow can qualify as an operator account; label its self-report and commercial interest.

- Fetch the supporting passage with enough surrounding context to check scope and caveats. A title, snippet, repost or engagement count is a discovery lead, not effectiveness evidence.
- For podcasts retain episode identity, publisher, speaker, publication/event dates, transcript passage and verified timecode or chapter anchor. Distinguish transcript reading, show-note reading and audio/video inspection. Do not invent a timestamp or attribute a show-note paraphrase as a verbatim spoken claim.
- For X retain canonical post ID/URL, author, date, thread/parent identity and the context actually retrieved. Identify original posts versus reposts, quotes and secondary summaries. If the relevant thread is truncated or unavailable, limit the claim accordingly.
- For documents retain canonical URL, title, author/organization, publication/update dates, relevant section or chunk and retrieval date. Separate an older experiment's setting from current product capabilities.
- Record whether the claimed result was a demo, pilot, repeated operational use or a measured experiment. Preserve baseline, sample, denominator, metric definition and limits where given. An omitted denominator remains unknown.
- Deduplicate the underlying claim across posts, podcasts and articles. Multiple retellings are one source lineage unless there is independent evidence.
- Keep excerpts proportionate and within source-use limits. Prefer faithful paraphrases with precise source locators; do not copy complete copyrighted transcripts.

Classify each claim as **guidance, demo, operator-reported use, observed artifact/outcome, measured experiment or independent replication**. These labels describe evidence type, not a universal ranking; relevance and methodological limits still matter. Cite counterevidence with the same care as success stories.

## 4. Maintain a research ledger

Each decision-relevant source or claim needs:

| Field group | Required record |
| --- | --- |
| Retrieval | Actual route/tool, query arguments, run/result IDs where returned, retrieval time, live/cached status and coverage limits. |
| Identity | Stable source/claim ID, canonical original URL, title, author/speaker, organization, source type and episode/thread/parent identity where applicable. |
| Passage | Relevant excerpt or paraphrase, surrounding context, section/timecode/thread locator, and what was actually read or inspected. |
| Dates | Publication date, event date if different, update/version and index/fetch date; unknowns remain explicit. |
| Provenance | Original/secondary status, claim lineage, independent corroboration if any, and X API collection/run ID if actually available. |
| Claim | Work performed, result, evidence class, setting, sample/baseline/metric where given, limitations and counterevidence. |
| Consequence | Linked local hypothesis ID, applicability comparison, changed decision or test, and remaining uncertainty. |

Retain inaccessible/deleted/truncated status and unsuccessful queries when they bound a material claim. “Not found in this query/index scope” is the supported conclusion from an empty search; “nobody has done this” is not.

## 5. Compare external evidence with local work

For each important analogue or counterexample create a compact applicability matrix:

| Dimension | External case | Local evidence | Difference / consequence |
| --- | --- | --- | --- |
| Inputs and transformation | What was consumed and changed? | Which inspected local work supports the match? | Is it the same useful job or only a similar domain? |
| Standard and context | What made the result acceptable? | Is the rubric/corpus owned, available and current? | Missing standards or versions may block transfer. |
| Recipient and workflow | Who used the result and at what point? | Who needs the local result? | A generated artifact without a recipient may add work. |
| Authority and access | Which tools, permissions and human decisions mattered? | What is permitted and tested here? | External access does not establish local readiness. |
| Variation and demand | What scope, cases and scale were evaluated? | What independent events and variation are supported? | Do not transplant repetition or coverage claims. |
| Verification and error cost | How were omissions/errors found and handled? | Who can check, at what effort and consequence? | Cheap production may create expensive review. |
| Outcome and effort | What improved, with what measurement limits? | What outcome and total effort can be tested here? | Report plausibility separately from expected or measured value. |

Conclude with the explicit consequence: **retain, narrow, split, change form, repair existing assistance, defer or reject**, with reasoning. Identify the smallest test that separates the hypothesis from its strongest counter-hypothesis. If a source changes neither the hypothesis nor the test, leave it out of the main argument rather than adding decorative citations.

## 6. Preserve access and native-feature research

Retain raw source-system names and their canonical aliases. Resolve aliases before combining findings; similar branding does not establish the same tenant, edition or system. Carry each source's role in the work and read/write requirements into the comparison.

Check current primary product documentation for changeable capability/interface claims. Record feature and interface availability, relevant editions/entitlements, authentication/grants, read versus write scope, export routes and existing native capabilities that could remove the need for a custom agent. Distinguish documentation-supported possibility, user-reported configuration and actually tested tenant access. Preserve tests' time and scope; a successful read does not prove a write or broader entity access.

Do not infer approval from technical availability, a platform preference or someone else's deployment. Unknown or prohibited access must remain visible. Prefer a viable approved alternative where one exists; otherwise frame the dependency as a specific information request. Do not claim that official documentation or an API listing establishes local integration reliability.

## 7. Report and stop proportionately

For each material recommendation return a concise external-to-local comparison, the strongest relevant counterexample, the changed hypothesis/approach and the resulting test. Put fuller passages, provenance and limitations in the report's expandable evidence; preserve the essential limitation in the visible reasoning.

Initial hypotheses need enough research to expose plausible alternatives and uncertainties, not an exhaustive deployment design. After user feedback, deepen the selected hypothesis where new information changes applicability. Stop when the material comparison and next test are supportable, additional retrieval is unlikely to change them, or a specific unavailable source prevents resolution. Record the gap and continue unaffected work. There is no fixed number of sources, podcasts or X posts to collect.
