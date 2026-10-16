# Interfaces

The pinned interfaces of the Agent Build skill: exit codes, the command table, record shapes, the run-this-case interface, comparison rules, brief and config fields, events, inputs, fixtures, vocabulary and the gate. Paths are relative to the skill folder unless they start with `R/` (the run folder, see `run-folder.md`). Scripts run with `python3 scripts/<name>.py`.

## P1. Exit codes (every script)
- `0` pass; for `briefing.py`, briefing written.
- `1` a check failed: the next step must not count it.
- `2` usage error, unreadable argument, or malformed input the script cannot classify.
- `3` needs a person: a disposition, ruling or name is missing.
- `briefing.py` returns only 0 or 2 (2 only for a usage error: bad flags, `--run` missing or not a folder). Nothing a design contains makes it return 1 or 3.
- Every recording script writes its record **before** exiting, whatever the exit code (except exit 2 on usage errors, which writes nothing). The record's `verdict` field equals `pass | fail | needs_person | unknown` matching exit 0/1/3/2-with-record.

## P2. CLI table
Common to every script: `--run R` (the run folder, see P2a), `--help`. Unknown flags = exit 2. All other paths are relative to `R` unless stated.

### P2a. Run-folder layout (`R`)
```
R/config.json                     run config (P10)
R/brief.json                      the agent-build brief's machine fields (P9); R/brief.md is the prose brief
R/inputs/agent-design/            an Agent Design run folder (outputs/agent-build-inputs.json, stages/test-suite/test-suite.json); absent = no design
R/inputs/work-brain/*.md          Work Brain notes pulled through the Within MCP by the session
R/inputs/requirements/            person-ratified source requirements when there is no design
R/inputs/environment.json         the environment read (written by briefing.py --read-env, or by the session)
R/inputs/history/<system>.csv     past cases or extracts, when access exists
R/run-log.jsonl                   run log 
R/briefing.md, R/what-we-still-need-to-know.json, R/capabilities.json
R/access.json, R/handover.json, R/open-items.json
R/profiles/<system>.json
R/frozen/v<N>/...                 frozen files of manifest version N; R/frozen/MANIFEST.json = current version pointer + all versions
R/records/*.json                  counted records (written only by the skill's scripts in the orchestrating session)
R/outputs/<label>/<case_id>.json  raw run-this-case outputs (label = early | final | suite-<package12> | live | cold)
R/residuals.json
R/package/                        the shipped agent package; R/package.sha256 its hash
R/units/<uid>/                    launcher pid and log files
R/reviews/<uid>-r<n>.md
R/fidelity/round-<n>/
R/logs/gate/                      gate logs
```
The brief's paths written `run/X` mean `R/X`.

### P2b. Commands
| Script | Subcommand and arguments | Reads | Writes | Exit |
|---|---|---|---|---|
| `config.py` | `validate --run R --phase settings\|build [--prefs PATH]` | `config.json`, `references/blocks.json`, `references/engines.md` not read | stdout report; `records/config-<phase>.json` | 0 valid; 1 a listed rejection; 2 unreadable config |
| `log.py` | `append --run R --type T --payload JSON`; `read --run R [--type T]` | `run-log.jsonl` | `run-log.jsonl` (append) | 0; 2 bad type/payload |
| `briefing.py` | `--run R [--read-env]` | everything under `inputs/`, `access.json`, prefs via `config.json` | `briefing.md`, `what-we-still-need-to-know.json`, `capabilities.json`, `inputs/environment.json` when `--read-env` | 0; 2 usage |
| `access.py` | `record --run R --system S --state none\|synthetic\|extract\|read-only\|live --evidence PATH`; `assume --run R --system S --note TEXT`; `handover --run R`; `done --run R --item ID --record PATH` | `brief.json`, `inputs/agent-design/outputs/agent-build-inputs.json` | `access.json`, `handover.json` | 0; 1 `done` without a bound run record; 2 |
| `openitems.py` | `build --run R`; `check --run R --records LABEL` | hand-off, `brief.json`, `outputs/LABEL/` | `open-items.json`, `records/open-items-check.json` | 0; 1 unmapped item or safe behaviour not observed; 2 |
| `profile.py` | `--run R --system S --file inputs/history/S.csv [--date-formats F,...]` | the CSV | `profiles/S.json` | 0; 2 unreadable |
| `replay.py` | `--run R --cases DIR --command CMD...` | past case files, `brief.json` rules | `records/replay.json` | 0 always when written (no threshold); 2 |
| `freeze.py` | `freeze --run R --cases DIR --expected DIR [--judge-prompts DIR] --author NAME --author-role ROLE`; `verify --run R`; `refreeze --run R --case ID --expected FILE --author NAME --author-role ROLE` | case files, expected files, hand-off suite | `frozen/v<N>/**`, `frozen/MANIFEST.json`, `records/freeze-verify.json`, log event `freeze.sealed` | 0; 1 builder-authored expected, empty set, verify mismatch; 2 |
| `stoprules.py` | `--run R --label LABEL` | `brief.json`, `outputs/LABEL/`, `frozen/`, optional `records/replay.json` | `records/stoprules.json` | 0; 1; 2 |
| `numbers.py` | `--run R --label LABEL` | `brief.json`, `outputs/LABEL/`, `frozen/`, `capabilities.json` | `records/numbers.json` (always) | 0; 1; 3 empty ledger without `no_numbers_confirmed`; 2 |
| `core.py` | `--run R --label LABEL --source DIR` | `brief.json`, `outputs/LABEL/`, `frozen/`, agent source DIR | `records/core.json` | 0; 1; 2 |
| `run_suite.py` | `--run R --command CMD... [--label LABEL]` (default label `suite-<package12>`) | `frozen/` current version, `package.sha256` | `outputs/LABEL/*.json`, `records/suite-<package12>.json` | 0 aggregate pass; 1 fail; 3 pending; 2 |
| `compare.py` | `--run R --label LABEL`; `dispose --run R --case ID --disposition accepted\|agent-wrong\|expected-wrong --reviewer NAME --note TEXT --artifact PATH...` | `outputs/LABEL/`, `frozen/`, `brief.json` | updates the suite record's per-case states and aggregate; `records/dispositions.jsonl` (append) | 0 pass; 1 fail; 3 pending; 2 |
| `e2e.py` | `check-split --split FILE`; `record --run R --early\|--final --command CMD... [--tools-file PATH] [--attest NAME --evidence PATH]` | `brief.json`, `frozen/`, `config.json`, `run-log.jsonl` | `records/e2e-early.json` or `records/e2e-final.json`, `outputs/early\|final/` | 0; 1; 3 probe applicability undecidable; 2 |
| `live.py` | `record --run R --command CMD... [--usage-file PATH] [--attest NAME --evidence PATH]` | `frozen/`, `package.sha256`, `brief.json` | `records/live.json`, `outputs/live/` | 0; 1; 3 pending disagreement; 2 |
| `cold.py` | `record --run R --attestor NAME --evidence PATH --report FILE` | the attested report (P3 cold shape) | `records/cold.json` | 0; 1; 2 |
| `exit_gate.py` | `--run R` | everything in criterion 15 | `records/exit.json` | 0 `done`; 1 `not done`; 2 |
| `status.py` | `table --run R --stage NAME`; `residual add\|resolve --run R ...` | `run-log.jsonl`, records | stdout; `residuals.json` | 0; 2 |
| `launch.py` | `unit --run R --uid U --worktree ABS --branch B --base C --prompt FILE [--dry-run]` | `config.json` | `units/U/{U}.pid`, `.exit`, `.log` | 0 launched (or printed with `--dry-run`); 2; 3 placement refused |
| `review.py` | `--run R --uid U --path ABS --base C --brief FILE [--round N]` | `config.json` | `reviews/U-rN.md` | 0 verdict parsed; 1 failed review after one relaunch; 2 |
| `fidelity.py` | `round --run R --brief FILE --inputs DIR...`; `ruling --run R --finding ID --by NAME --text TEXT`; `status --run R` | `fidelity/` | `fidelity/round-<n>/`, `records/fidelity.json` | 0 clean round; 1 findings, next round due; 3 loosening or cap; 2 |
| `readiness.py` | `scan --run R`; `dispose --run R --flag ID --disposition D --by NAME` | `package/`, hand-off `bundle`, `brief.json` | `records/clearance.json` | 0 cleared; 1 a scan failed; 3 undisposed flag or missing/conflicting client name; 2 |
| `monitor.py` | `--run R [--once]` | `run-log.jsonl`, `units/` | log events | 0; 2 |
| `integrate.py` | `suite --run R --repo ABS --commit C --command CMD...`; `check-evidence --repo ABS` | repo | `records/integration-suite.json` | 0; 1; 2 |
| `prefs.py` | `read --path P`; `append --path P --key K --value JSON --decided-by NAME --run-id ID --source STOP` | prefs file | prefs file (append only) | 0; 2 |

Malformed input is always exit 2 or a recorded `unknown`/`pending`/`needs_more_information`, never a pass. `CMD...` is the run-this-case command given after `--command` as the remaining arguments; the script appends the case file path as its last argument.

## P3. Record shapes
Every record is a JSON object with the header from C1 (`produced_at`, `skill_version`, `input_hashes`, `code_identity`) plus `verdict` (P1) and `reasons: [str]`. `code_identity` is `{"commit": "<sha>"}` for the early end-to-end record and `{"package_sha256": "<hex>"}` for every counted record (final e2e, suite, stoprules, numbers, core, live, cold, clearance). Records also carry `manifest_version` (int) when they read `frozen/`.
- `access.json`: `{"systems": [{"name", "state": "none|synthetic|extract|read-only|live", "evidence": path|null, "verified_at", "assumptions": [{"note", "source"}]}]}`. An assumption never raises `state`.
- `handover.json`: `{"items": [{"id", "system", "kind": "run-when-access-arrives|customer-integrates", "what", "status": "open|done", "run_record": path|null, "input_hashes": {}|null}]}`.
- `open-items.json`: `{"items": [{"id", "source_field", "summary", "affected_units": [], "affected_processes": [], "safe_behaviour": "stop|escalate", "test_case_ids": [], "adjudicator", "status": "open|resolved"}]}`.
- `profiles/<system>.json`: `{"file", "rows", "fields": [{"name", "distinct_count", "distinct_ratio", "null_rate", "top_values": [[value, count]], "date_parse_share", "pinned_clock_hint": bool}]}`; an empty denominator gives `"needs_more_information"` in place of the number.
- `records/replay.json`: `{"rules": [{"rule_id", "fire": n, "no_fire": n, "agree": n, "disagree": n, "needs_more_information": n}]}`.
- `frozen/MANIFEST.json`: `{"current_version": N, "versions": [{"version", "sealed_at", "manifest_sha256", "design_identity": {stage_input_hashes}|"needs_more_information", "files": [{"path": "v<N>/...", "sha256", "kind": "case|expected|judge-prompt", "case_id", "author", "author_role", "frozen_at", "state": "ready|pending", "pending_reason"}]}]}`. `manifest_sha256` = `sha256_json` of the version entry without that field. Earlier versions are never rewritten.
- `records/freeze-verify.json`: `{"manifest_version", "log_hash", "problems": [{"path", "kind": "modified|replaced|deleted|permissions|manifest-edited"}]}`.
- `records/e2e-early.json` / `e2e-final.json`: `{"target": {...}, "case_id", "output": path, "comparison": {"state", "reason"}, "probes": [{"name", "applies": true|false|"needs_more_information", "reason", "result": "pass|fail|not-run", "evidence"}], "traces_arrived": true|false|null, "tools": {"source": "observed|attested", "attestor", "evidence", "reachable": [{"name", "kind": "read|write"}]}, "denials": n, "round2_first_launch": iso|null}`.
- `records/suite-<package12>.json`: `{"command", "label", "manifest_version", "cases": [{"case_id", "case_sha256", "input_sha256", "output": path, "state", "reason", "critical": true|false|null, "disposition": null|{...}}], "aggregate": {"state", "reasons", "non_critical_share"}}`.
- `records/stoprules.json`: `{"rules": [{"rule_id", "fired_cases": [], "not_fired_cases": [], "effect_took_hold": bool, "verdict", "reason"}], "replay": path|null}`.
- `records/numbers.json`: `{"label", "ledger_empty": bool, "entries": [{"name", "computed_by", "cases": [{"case_id", "value", "step_found": bool, "reproduced": bool, "matches_expected": "pass|fail|pending"}]}]}`. Numbers runs on the final suite label and again on `live`; `label` names the run that wrote the record, and the exit gate re-derives numbers on both labels.
- `records/core.json`: `{"label", "checks": [{"name": "no-upgrade|activity-log|no-case-content|decision-trail|no-judge-pass|advisory-judge-valid", "verdict", "findings": []}]}`. Core runs on the final suite label and again on `live`; `label` names the run that wrote the record, and the exit gate re-derives core on both labels.
- `records/live.json`: like the suite record plus `{"model_calls_total", "usage": {"source": "observed|attested", "evidence"}, "spend_usd": number|null}`.
- `records/cold.json`: `{"attestor", "evidence", "empty_folder": bool, "files_missing": [], "install": "pass|fail", "probes": [...], "cases": [{"case_id", "state"}], "reviewer_view": text, "reused_state": bool}`.
- `records/clearance.json`: `{"client_name", "flags": [{"id", "rule", "path", "line", "text", "disposition": null|{"value", "by", "date"}}], "shareable": [], "internal": [], "image_scan": "pass|fail|not-applicable"}`.
- `records/fidelity.json`: `{"rounds": [{"n", "findings": [{"id", "kind": "loosening|inversion|invention|other", "text"}], "fixer_session", "author_session", "clean": bool}], "state": "clean|open|stopped-loosening|stopped-cap", "rulings": []}`.
- `records/integration-suite.json`: `{"commit", "command", "sandboxed": false, "exit", "verdict", "started_at", "ended_at", "evidence_hits": []}`. `review.py --integration` starts only when it has `exit: 0`, `sandboxed: false`, `commit` equal to the reviewed HEAD, `verdict: "pass"` and an empty `evidence_hits`.
- `residuals.json`: `{"residuals": [{"id", "kind": "blocks-landing|lands-with-a-note", "summary", "status": "open|resolved", "note"}]}`.
- `records/config-<phase>.json`: header + `verdict`, `reasons`, `phase`, `preferences_path`, `open_items: [{"id", "summary"}]`.
- `records/exit.json`: `{"status": "done|not done", "reasons": [str], "processes": [{"process", "status", "reasons"}], "package_sha256", "manifest_version", "checks": [{"name", "applies", "reason", "result"}]}`.

## P4. Run-this-case interface
Command: `<CMD...> <case file>`; it prints exactly one JSON object to stdout (anything else on stdout = `exit: crashed`) and exits 0. Fields as the brief's P4 list: `case_id`, `outcome` (object: label set id → outcome) **or** `no_verdict: true` + `no_verdict_reason`, `values` (name → number), `artifacts` (name → path), `code_steps` ([{name, inputs: {}, output}]), `tool_calls` ([{seq, name, kind, arguments, result, denied}]), `model_calls` (int), `rule_results` ([{seq, rule_id, fired, effect}]), `decisions` ([{seq, actor, label_set, before, after}]), `decision_trail` ([step names]), `activity_log` (path), `exit` (`completed|crashed|refused`).
- `seq` is one integer sequence shared by `tool_calls`, `rule_results` and `decisions`.
- Normalized activity log: JSON lines, each `{"ts", "case_id", "seq", "event": "decision|tool|rule|step|judge", "actor", "detail": {}}`.
- A non-zero exit, a timeout (300 s default), invalid JSON or a missing required field = `exit: crashed` in the stored output, never an outcome.
- Decision trail step names: scripted steps start with `check:`, judge steps with `judge:`, others free.

## P5. Comparison rules
Frozen expected result against run output, per result kind:
- Complete candidates: the primary expectation and each declared `allowed_alternatives` entry are complete candidates. One candidate must satisfy all applicable label sets, values and artifact checks together; fields are never mixed across candidates. Ambiguous or conflicting candidates make the case `pending`.
- Label sets: exact match per set within the chosen candidate.
- Numbers: within the amount tolerance the case or the brief declares; for Agent Design cases with none declared, the Agent Design grader's 0.01; otherwise exact. A suite's `numeric_floors` is never used as an amount tolerance.
- Drafts and documents: the scripted checks the case declares (required fields, sections, values), never prose equality.
- "No verdict": the run output carries `no_verdict: true` and a `no_verdict_reason`, compared with the expected no-verdict and its reason; a missing or conflicting reason keeps the case `pending`.
- Exclusion cases (stamped needs more information upstream): the case stays `pending`, and a critical exclusion keeps the suite incomplete, until its missing information is resolved. Its safe-path behaviour is checked separately, through the open items, and never turns the case into `pass`.

Suite aggregate, decided in this order (the first that holds wins):
1. `fail`: a critical case fails, or the non-critical pass share is below the ratified floor (computed only when nothing is pending).
2. `pending`: any case is pending, any case lacks a criticality mark, non-critical cases exist and the floor is open (`brief.non_critical_floor` unset or unratified), or an all-critical suite is unconfirmed (`all_critical_confirmed`); no share is computed while pending. A confirmed all-critical suite needs no floor.
3. `pass`: every critical case passes and, when non-critical cases exist, their share meets the ratified floor.

Only an aggregate `pass` counts toward `done`.

Per-case state is `pass`, `fail` or `pending`. A missing, conflicting or unreadable expectation makes the case `pending` for a person, never `pass`. A person's disposition is recorded against the case's frozen hash, the package hash and the reviewed artifact hashes, with the reviewer named and an evidence note:
- "agent result accepted" turns the case into `pass`, but only for a case that is pending because its correctness needs judgement and no scripted check decides it, with every prerequisite resolved. It never overrides a scripted `fail`, and it never clears an exclusion case or a missing expectation: those first need their missing information resolved and a frozen expectation from a non-builder. An advisory judge verdict never produces this disposition.
- "agent wrong" turns the case into `fail`.
- "expected result wrong" means a re-freeze by a non-builder author under a new manifest version. Earlier manifest versions are kept, never overwritten: the early end-to-end record stays checked against the version it ran on, and the final end-to-end record and every counted correctness record use the current version.

Concrete choices:
- Frozen expected file per case (`kind: expected`): `{"case_id", "candidates": [{"outcome": {...}|null, "no_verdict": bool, "no_verdict_reason": str|null, "values": {}, "artifact_checks": [{"artifact", "required_fields": [], "sections": [], "values": {}}]}], "tolerance": number|null, "critical": bool|null, "exclusion": bool, "judgement_needed": bool}`. The first candidate is the primary expectation; the rest are `allowed_alternatives`.
- Tolerance order: the case's `tolerance`, else `brief.json` `amount_tolerance`, else 0.01 for cases whose source is an Agent Design suite, else exact. Numbers compare with `decimal.Decimal(str(x))`.
- `numeric_floors` is never read as a tolerance. Suite floor: `brief.json` `non_critical_floor.value` (share 0–1), counted only when `ratified_by` and `date` are present.
- Dispositions are appended to `records/dispositions.jsonl` as `{"case_id", "case_sha256", "package_sha256", "artifact_sha256": {}, "disposition", "reviewer", "note", "date"}`. A disposition whose hashes differ from the current ones is ignored (stale) and reported.
- "expected-wrong" never edits a frozen file: it records the disposition and the case stays `pending` until `freeze.py refreeze` writes a new manifest version.

## P6. Who writes the evidence
`R/records/**`, `R/frozen/**`, `R/residuals.json` and `R/run-log.jsonl` are written only by the skill's scripts run by the orchestrating session. `launch.py` keeps them out of every unit's writable paths (P2, criterion 18). `exit_gate.py` re-derives comparison, coverage and core verdicts by calling `compare.compare_case`/`aggregate`, `stoprules`, `numbers` and `core` logic on the raw outputs under `R/outputs/`, ignoring the `verdict` fields stored in those records except to report a disagreement between stored and re-derived.

## P7. Observed versus attested
An attested item is `{"source": "attested", "attestor": "<name>", "evidence": "<run-relative path to an existing non-empty file>"}`. Missing attestor, missing or empty evidence file = rejected (exit 1 for the recording script, and the gate treats the item as missing).

## P8. Applicability
By capability, never by shape name. Applicability is decided from the briefing's capability fields and the agreed target:

| Check | Applies when |
|---|---|
| Freeze | always |
| Early and final end-to-end | always |
| Stop-rule coverage | any rule has `stops_work: true` |
| Numbers record | always written; an empty ledger passes only with `no_numbers_confirmed` and a no-numbers briefing |
| Real model calls in the live run | the design uses a model (`uses_model`) |
| Live run, light cold run, clearance | always |
| Image build and image leak scan | the agent ships as a Docker image (`ships_as_docker_image`) |
| Python 3.9+, git, curl on the build host | always |
| Entrypoint start, config paths | always (a skill package's entrypoint is its documented local run) |
| Runtime at its pin | a runtime block is chosen (`runtime` not null) |
| Registry access at install | installation needs a package registry (`needs_registry`; the endpoints are pinned per chosen block) |
| Key-file credentials in a fresh home folder, egress to the engine's API host | the design uses a model (`uses_model`; model calls only) |
| Credentials and egress for each agreed external endpoint | the agent calls an external system (`calls_external_systems`; only the endpoints and credentials agreed at planning) |
| Node version | the runtime runs on Node (`runtime_on_node`) |
| OpenAI project region | the engine is OpenAI and the design uses a model (`engine`, `uses_model`) |
| Write-denial and read-only probes, zero denials | always |
| Eval-chain exit code | always |

Capability fields come from `R/capabilities.json` written by `briefing.py` (`{"capabilities": {...}, "per_process": {pid: {...}}}`): `uses_model`, `produces_numbers`, `writes`, `calls_external_systems` (list of endpoint names), `ships_as_docker_image`, `runtime` (block id or null), `runtime_on_node`, `needs_registry`, `engine` (from config). Each is `true|false|"needs_more_information"`; the brief (`brief.json` `capabilities`) may override with a person-confirmed value. A probe whose capability is `needs_more_information` is recorded `applies: "needs_more_information"` and the exit gate writes `not done` for it.

`traces_arrived` is information only, never a gate: the end-to-end record carries it for the reader and no check reads it as a pass or a fail. Any other exemption needs a person-ratified alternative check recorded in the brief (`exemptions[{check, alternative, ratified_by}]`); a reason alone is not enough, and the exit gate counts an exemption without `alternative` and `ratified_by` as a run reason, not as exempted.

## P9. Brief fields (`R/brief.json`)
Exactly the brief's P9 list, as JSON keys: `target {environment, systems[{name, mode: "real|stubbed", reason}]}`, `rules[{id, stops_work, source}]`, `numbers[{name, computed_by, test_case_ids}]` (`computed_by` is either `{"command": [...]}` reading JSON inputs on stdin and printing the value, or `{"module": "path/to/file.py", "function": "name"}` relative to the agent package), `agent_host {kind, name, tool_allow_list, writes_allowed}`, `deliverable_form`, `review_surface`, `rule_source_of_truth`, `no_numbers_confirmed`, `all_critical_confirmed`, `non_critical_floor {value, ratified_by, date}|null`, `label_sets[{id, escalation_outcomes}]`, `rule_literals[{value, rule_id, confirmed_by}]`, `exemptions[{check, alternative, ratified_by}]`; plus `client_name`, `amount_tolerance` (optional), `capabilities` (optional overrides, P8), `processes` (list of process ids for a set; one entry for single), `version` (brief version, int) and `split_version` (int, equal to `version` after any scope change: criterion 24). A missing `agent_host.tool_allow_list` means `[]` and `writes_allowed: false`.

## P10. Run config (`R/config.json`)
`{"engine": "anthropic|openai", "build_host": "claude-code|codex", "cross_engine_review": bool, "allow_preview_engine": bool, "preferences_path": str|null, "blocks": {"<block id>": {"choice": "<option id>"} | {"dropped": true, "reason": str}}, "processes": [str], "shared_blocks": {"<block id>": [process ids]}, "run_id": str}`. Block ids are the `id`s in `blocks.json` : `runtime, execution_layer, model, tools, activity_log, calculation, persistence, state, tracing, scripted_evals, judge, launcher, reviewer`. Each option in `blocks.json` names its `engine` (`anthropic|openai|any`). `--phase settings` reads only `engine`, `build_host`, `allow_preview_engine`, `preferences_path` (and the OpenAI status from `blocks.json` `engines.openai.status`). A shared block chosen twice = a block id present both in `shared_blocks` and in a per-process `blocks` override `process_blocks.<pid>.<block>`.

## P11. Customer preferences file
JSON lines, each `{"key", "value", "decided_by", "date", "run_id", "source"}`. `source` is one of `settings | interview | target | blocks | brief | freeze | disposition | other`. Append only (open in `"a"` mode, one line per write, `fsync`). Malformed line: not valid JSON, or a missing/empty field. If a malformed line has a recoverable string `key`, that key is withheld; if no key can be identified, `all_withheld: true`.

## P12. Run-log events
Each line `{"ts", "type", "payload"}`. Types: `settings.validated`, `build.validated`, `briefing.written`, `prefs.appended`, `access.recorded`, `freeze.sealed` (payload `{"version", "manifest_sha256"}`: the one `freeze.py verify` trusts — the last for each version), `refreeze.sealed`, `round.started` (payload `{"round": n, "first_launch": iso}`), `unit.launched`, `unit.exited`, `unit.alarm`, `unit.killrule`, `review.returned`, `e2e.recorded`, `suite.recorded`, `live.recorded`, `cold.recorded`, `clearance.recorded`, `fidelity.round`, `integration.suite`, `exit.written`, `stage.boundary`, and `record.written` (payload `{"path", "sha256"}`, appended by every script that writes a counted record). `e2e.py` reads the first `round.started` with `round: 2` as "the second build round's first launch". The exit gate reads `freeze.sealed`, `round.started` and `exit.written` only.

## P13. Agent Design inputs
- Pinned copies in `references/inputs/` of every file under `skills/agent-design/formats/**` from **`36159b83`** (`dev` after PR #64 merged; byte-identical to `a8078468`, the head of `int/0453-agent-design` at dispatch; its `formats/` tree is identical to `d67c91b9`). Layout mirrors the source (`references/inputs/formats/design/...`). `references/inputs/SOURCE.json` = `{"repo": "klarityinc/within-fde-factory-plugin", "ref": "int/0453-agent-design", "commit": "a8078468…full sha", "path": "skills/agent-design/formats"}`.
- The equality test compares each copy byte for byte with `git show <commit>:<path>` when the commit is available in the local clone, and otherwise against `references/inputs/SHA256SUMS` (sha256 per file, written by U1 from the source), so it runs in CI without the branch.
- Work Brain notes: `R/inputs/work-brain/*.md`; `briefing.py` lists and cites them by file name and never calls the MCP.

### Pinned inputs on disk
`references/inputs/formats/**` holds the 21 Agent Design format files byte-equal to commit `36159b833b757c7ef7b4444f7763434f0d73ebb7` (`dev`); `references/inputs/SOURCE.json` is `{"repo": "klarityinc/within-fde-factory-plugin", "ref": "dev", "commit": "36159b833b757c7ef7b4444f7763434f0d73ebb7", "path": "skills/agent-design/formats"}`; `references/inputs/SHA256SUMS` has one line per file, `<sha256 hex>  formats/<relative path>`, sorted by path.

## P14. Fixtures 
- `tests/fixtures/agent-design/single/` — scrubbed copy of `skills/agent-design/tests/fixtures/recorded-single/` at `a8078468`, completed with `outputs/agent-build-inputs.json` (built from `skills/agent-design/tests/u2/fixtures/pass/handoff.json` structure) and `stages/test-suite/test-suite.json`; organisation renamed to Northwind Supply.
- `tests/fixtures/agent-design/set/` — scrubbed copy of `skills/agent-design/tests/u25/fixtures/set/` at `a8078468`, completed the same way, two processes, one shared agent; organisation Bluebird Logistics.
- A structure test asserts the scrubbed copies keep the source's file list and JSON key structure (values may differ only where scrubbed).
- `tests/fixtures/shapes/<kind>/` — one minimal run folder `R` per kind, each with `config.json`, `brief.json`, `inputs/`, a frozen-ready `cases/` and `expected/`, and `package/agent/run_case.py` (the run-this-case stub, inside the package: H2). Kinds (folder names): `single-label`, `multi-label-marker`, `no-verdict`, `numbers`, `drafts`, `one-writer`, `pure-script`, `skill-package`, `set-shared-agent`, `no-design`, `handoff-gaps`, `sandbox-stubbed`, `complete`, `all-critical`, `open-disagreement`, `open-residual`, `prefs-earlier-run`. `complete` is a code-runtime agent that must end `done` once every record is produced; `open-disagreement` and `open-residual` must end `not done`.
- Each stub agent is deterministic Python, stdlib only, reads the case file and prints a P4 output.

## P15. Vocabulary, scan inputs, shipped inventory
- The term lists are data files: `references/scan-vocabulary.json` = `{"banned": [{"name", "pattern"}]}` (the FDE list of banned terms and their patterns), and `references/inputs/formats/lists/vocabulary.json` (banned and retired terms from the pinned Agent Design formats). The scans load both. The terms themselves are written only in those two files, never in prose or code.
- Prose and code say "people" and "agents".
- Shipped inventory: every tracked file under the skill folder except `tests/**`. These are the files the portability scan and the vocabulary scan cover. Exempt from the vocabulary scan: exactly those two data files.

## P16. Curated completion gate
Source: the parallel build loop's gate tool, copied read-only into `tools/gate/` as `gate.py` + `gate` (shell wrapper `#!/bin/bash`, `exec python3 "$(dirname "$0")/gate.py" "$@"`) + `README.md` + `tests/test_gate.py` (+ `tests/README.md`).
- **Kept**: level `local` checks 1–5 (`committed`, `right_base`, `in_lane`, `must_not_edit`, `done_check`), `--from`, `--worktree`, the claims reader (`--return`), the placeholder and path-template rules, verdict logic (pass/fail/unknown), the process-group supervision of the done-check.
- **Dropped** (they need `gh` and the GitHub API, which a customer build may not have; merge state is checked by people and CI): level `merge` and checks 6–10 (`pushed`, `pull_request`, `ci_checks`, `review_threads`, `review_decision`), `--level`, `--remote`, `--repo`, `--branch`, `gh_stub.py`, the GraphQL query. Any `--level` value is exit 2; `--level merge` prints "merge level not shipped".
- **Log root**: `$GATE_LOG_ROOT` if set, else `--log-root DIR`, else `./run/logs/gate/` under the current folder. No machine path appears in the copy.
- Mutation fixtures that write evidence folders are not copied; the kept tests pass on the copy, with log paths under each test's temp folder.

## Shared helpers (C1–C9)
Consumers test against their own stub with this exact name and signature.
1. **`scripts/_records.py` :**
   - `load_json(path) -> (obj, err)`: returns `(None, "<reason>")` on missing, unreadable or invalid JSON; never raises for those.
   - `write_record(path, obj)`: adds nothing; writes JSON with `indent=2`, `sort_keys=True`, trailing newline, via a temp file and `os.replace`.
   - `sha256_file(path) -> str` (hex); `sha256_json(obj) -> str` (hex of the normalized form below).
   - `normalize_json(obj) -> str`: `json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)`.
   - `now_iso() -> str`: UTC, `YYYY-MM-DDTHH:MM:SSZ`; honours env `AB_FIXED_NOW` when set (tests pin the clock with it).
   - `record_header(skill_version, inputs: dict[name -> sha256], code_identity: dict) -> dict`: returns `{"produced_at", "skill_version", "input_hashes", "code_identity"}` (P3).
   - `SKILL_VERSION = "0.1.0"`.
   - Exit-code constants `OK=0, FAILED=1, USAGE=2, NEEDS_PERSON=3` (P1).
2. **`scripts/log.py` **: `append(run, event_type, payload: dict) -> dict` and CLI `log.py append --run R --type T --payload JSON`; `read(run) -> list[dict]`; events in E12.
3. **`scripts/config.py` **: `load(run) -> (config, errors)`; CLI in P2.
4. **`scripts/prefs.py` (U23)**: `read(path) -> {"values": {key: {"value", "source", "decided_by", "date", "run_id"}}, "withheld": [key...], "all_withheld": bool, "problems": [str]}`; `append(path, key, value, decided_by, run_id, source, date=None)`.
5. **`scripts/compare.py` (U10)**: `compare_case(case_frozen: dict, expected: dict | None, output: dict | None, tolerance) -> {"case_id", "state": "pass"|"fail"|"pending", "reason"}`; `aggregate(cases: list, brief: dict) -> {"state", "reasons": [...], "non_critical_share": float | None}`.
6. **`scripts/freeze.py` (U8)**: `verify(run) -> (ok: bool, problems: list[str])`; `current_manifest(run) -> (manifest, version)`.
7. **`scripts/run_suite.py` (U10)**: `run_case(command: list[str], case_path, out_dir, timeout=300) -> output dict` (validates the run-this-case output shape of P4; a crash or invalid output yields `{"case_id", "exit": "crashed", "error"}`).
8. **`scripts/access.py` (U4)**: `load(run) -> ledger`; **`scripts/openitems.py` (U4)**: `load(run) -> items`.
9. **`scripts/status.py` (U13)**: `add_residual(run, id, kind, summary)`, `resolve_residual(run, id, note)`.
10. Script names are exactly those in the split's "Scripts and the files they write" table. No other unit creates a file with the same name.

## Missing inputs and the needs-more-information state (E7–E8)
7. Missing is never zero; a missing or conflicting input is never filled in or converted to force a match. It becomes `needs more information` / `pending` / an open item, as the criterion says.
8. "needs more information" is the literal state string `"needs_more_information"` in JSON and "needs more information" in prose.
