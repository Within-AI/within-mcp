# Run folder layout

Every Agent Build run keeps its files in one run folder, called `R` below. Script arguments and paths inside records are relative to `R`, written with forward slashes, never absolute machine paths. The one exception: `preferences_path` in `R/config.json` may be absolute or relative to `R`.

| Path | What it holds | Written by |
|---|---|---|
| `R/config.json` | run config (engine, build host, blocks) | the session, after the settings conversation |
| `R/brief.json` | the agent-build brief's machine fields; `R/brief.md` is the prose brief | the session |
| `R/inputs/agent-design/` | an Agent Design run folder (`outputs/agent-build-inputs.json`, `stages/test-suite/test-suite.json`); absent = no design | the session (copied in) |
| `R/inputs/work-brain/*.md` | Work Brain notes pulled through the Within MCP | the session |
| `R/inputs/requirements/` | person-ratified source requirements when there is no design | the session |
| `R/inputs/environment.json` | the environment read | `briefing.py --read-env`, or the session |
| `R/inputs/history/<system>.csv` | past cases or extracts, when access exists | the session |
| `R/run-log.jsonl` | run log | `log.py` |
| `R/briefing.md`, `R/what-we-still-need-to-know.json`, `R/capabilities.json` | briefing, gaps, capabilities | `briefing.py` |
| `R/access.json`, `R/handover.json`, `R/open-items.json` | access ledger, hand-over list, open items | `access.py`, `openitems.py` |
| `R/profiles/<system>.json` | data profiles | `profile.py` |
| `R/frozen/v<N>/...` | frozen files of manifest version N; `R/frozen/MANIFEST.json` = current version pointer + all versions | `freeze.py` |
| `R/records/*.json` | counted records, written only by the skill's scripts in the orchestrating session | each recording script (`config.py` writes `records/config-<phase>.json`) |
| `R/outputs/<label>/<case_id>.json` | raw run-this-case outputs (label = early, final, suite-<package12>, live, cold) | `e2e.py`, `run_suite.py`, `live.py`, `cold.py` |
| `R/residuals.json` | residuals | `status.py` |
| `R/package/` | the shipped agent package; `R/package.sha256` its hash | the build units; the session writes the hash |
| `R/units/<uid>/` | launcher pid and log files | `launch.py` |
| `R/reviews/<uid>-r<n>.md` | review results | `review.py` |
| `R/fidelity/round-<n>/` | fidelity rounds | `fidelity.py` |
| `R/logs/gate/` | gate logs | `tools/gate` |

The layout as a block:

```
R/config.json
R/brief.json
R/inputs/agent-design/
R/inputs/work-brain/*.md
R/inputs/requirements/
R/inputs/environment.json
R/inputs/history/<system>.csv
R/run-log.jsonl
R/briefing.md, R/what-we-still-need-to-know.json, R/capabilities.json
R/access.json, R/handover.json, R/open-items.json
R/profiles/<system>.json
R/frozen/v<N>/...
R/frozen/MANIFEST.json
R/records/*.json
R/outputs/<label>/<case_id>.json
R/residuals.json
R/package/
R/package.sha256
R/units/<uid>/
R/reviews/<uid>-r<n>.md
R/fidelity/round-<n>/
R/logs/gate/
```

The brief's paths written `run/X` mean `R/X`. `R/records/**`, `R/frozen/**`, `R/residuals.json` and `R/run-log.jsonl` are written only by the skill's scripts run by the orchestrating session, never by a build unit.
