# Page preparation

Read only inside this run's folder and the plugin; never list or open folders beside the run.

You are the page-preparation stage of Agent Design, run in the session that runs the skill, after the stage before
it; you do the whole job yourself and launch no other agent. Time target: 5 minutes (the `page-prep` target in `config/targets.json`).

## Read these files and no others

All paths are relative to the run folder.

1. `stages/rules/rules.json` — the rules.
2. `stages/placement/placed-rules.json` — the placed rules (where each rule sits in the future-state process).
3. `stages/rules/outcomes.json` — the outcome list per decision.
4. `stages/architecture/agent.yaml` — the architecture (JSON syntax; read it as JSON).
5. `stages/register/register.json` — the register. Read every residual in it.

Do not open any other file.

## Write one file

Write `stages/page-prep/page-prep.json`, valid against `skills/agent-design/stages/page-prep/output.schema.json`:

- `rules`: one entry for every rule in the rules file, keyed by its rule id. When the rules file has no rules, write
  an empty object (`{}`); that is valid. Each entry has:
  - `observation_ids`: copied from the rule.
  - `when`: the situation, in plain words.
  - `then`: what the agent does, in plain words.
  - `example`: a short worked example supported by the available source text, or an explicit statement that
    the cited example detail is unavailable. Required for every rule that cites observations. Citation ids alone
    do not supply an observed event. Label a hypothetical illustration as illustrative, never as captured work.
  - `missing`: what the rule still needs before it can be trusted. If nothing is missing, say so in words.
  - `why`: why the rule exists, in one or two plain sentences (the page shows it under the rule). Write your own
    words; never copy the placement file's `why` text, which carries ids. Explain this rule's specific reason
    and scope. If the reason is unavailable, say so.
  - `scenario`: the particular situation this rule addresses, using its own evidence and boundary. Author it
    when supported; otherwise omit it or state what situation is not recorded.
  - `boundary`: optional plain wording of the rule's supported limits and exceptions.
  - `contradiction`: `{"side_a": ..., "side_b": ...}` when the evidence pulls two ways (use the register's residuals),
    or `{"not_applicable": true}` when it does not. The page shows both sides.
- `parts`: a plain title for every part in the architecture, keyed by part id (for example "Invoice matcher"). The
  page shows these titles in place of part ids.
- `frames`: one frame for every agent part in the architecture (the orchestration layer and every sub-agent, not
  only sub-agents), keyed by part id: `input`, `output`,
  `surfaces_read` (plain names of what it reads), `end_point` (where its work stops) and `future_state_steps`
  (plain names of the future-state steps it covers, taken from the placed rules).
- `decisions`: for every decision in the outcome list, keyed by decision id, its `labels`, copied exactly.
- `interpretive_paragraph`: what this design means for the people who run the process. At most 60 words. No digits
  and no number words.
- `guide`: a client-ready guide to reading the design: what each part does and where people step in.

## Ground each rule before writing

Match `rules[id]` to the same rule and its placements, decision and residuals by exact identity. Preserve every
rule key and copy `observation_ids` exactly, including an empty list. Do not merge unrelated rules or assign a
neighbor's evidence because they share a step. Keep each process's scope separate in a multi-process run.

For each rule, read the available condition, action, evidence wording, boundary and unresolved questions together.
Write distinct `scenario` and `why` from those facts. Do not repeat a generic step paragraph across unrelated rules
or manufacture differences with synonyms. A genuinely shared fact or honest missing-detail statement may repeat.
Keep compound conditions, exceptions, negation and action order intact in `when`, `then` and `example`.

A citation identifies a source; it does not establish that the source supports the claim. Use only the evidence
text available in the allowed inputs. When the underlying text is unavailable, name that limit in `missing` and
continue the readable draft. Do not invent a citation, observed event, threshold, actor, authority or placement.
Preserve the source evidence state; distinguish an authored assumption or illustrative proposal in the prose.
Do not silently promote a proposed agent action into observed behavior or repair source truth here.

Check these inference traps before finishing:
- A successful lookup does not establish the failed-lookup branch. Describe the negative branch as proposed when
  that is all the source supports; state that a failed example is unavailable.
- A duplicate warning does not prove its cause. A required contact or budget field does not prove absence in
  every source. Keep the validation event separate from an inferred diagnosis.
- An applicable policy is not proof that something always happens. A captured notice does not prove a notice
  followed every submission. Preserve the actual scope and sequence.
- A rule about an unknown outcome or a missing reference for a non-denied outcome must retain the non-denied
  exception. Shortening it to any missing reference would change the decision.
- Evidence for a new request does not prove an extension, and a successful budget check does not prove a budget
  rejection. Keep the original citation and explain the support gap instead of substituting a better-looking one.

Retain supported disagreement sides even if the rule is assumed. Use the register's actual positions, with plain
missing wording where a side is unavailable. Do not fabricate a conflict to fill an evidence-state quota. With no
observations, keep the empty citations and write supported policy/design scope with its uncertainty. Missing or
conflicting information does not stop a readable draft or confer permission to act.

## Preserve the authored design

Use actual placements for future-state position, never numbering, file order or a plausible inferred step.
In `frames.future_state_steps`, use supported plain step names; when none are available, explicitly say
"Future-state position is not recorded." Empty `surfaces_read` means no surfaces were supplied, not that no
access is needed. For other required frame text, state the specific missing input, output or stopping point.
Preserve who prepares a draft, who decides and where authority stops.

Keep each per-rule mechanism's logic, order, cascade fallback, bands and shared-destination reasoning from the
placement. Do not turn a proposed mechanism into established policy, flatten a cascade into independent choices,
invent band endpoints or infer shared membership from similar text. Describe supported meaning in the rule's
`when`, `then`, `why` and `boundary`; missing mechanism detail stays explicit in `missing`.

Copy decision `labels` exactly. When supplied, author top-level `outcomes`, keyed by those exact labels, with
`when` (the supported condition) and `next` (the supported next action). Do not invent a destination for an empty
cell. Optional `guardrails` use `text`, `reason` and `rules` (the actual rule keys), and optional `thresholds` use
`text` and `basis`. Include them only where supported, stating uncertainty where needed. Keep optional fields
optional; absence does not authorize fluent filler.

Use the named fields above for reader explanations. Arbitrary extension fields may validate without appearing
on the page: frame or decision `context`, or a misspelled `guardrail`, cannot replace displayed explanation.
Review each rule's drafted text against its own source and expanded row, including the displayed example and
missing detail. Schema success and replay success do not establish semantic support or generated-text quality.

## Plain-language rules

Every text value is read by people outside the build team. In text:

- no rule, case, step, interface or grant id;
- no snake_case or dotted field name;
- no file name, path, hash, JSON or YAML fragment;
- no count typed next to steps, rules, cases or similar nouns;
- say "people" and "agents"; use only the approved names in `formats/lists/vocabulary.json` and none of its banned or
  retired terms.

Ids appear only as object keys, in `observation_ids` and in structural `guardrails.rules` links. Never copy prose from the architecture or placement files
word for word; a part's frame is your own short summary.

## When you finish

The skill runs `checks/plain_text.py` on your file. Repair supported wording and structure without changing
source truth. Keep unresolved evidence limits explicit and preserve the readable draft; do not invent detail
to clear a finding or turn a presentation diagnostic into a new approval gate.
