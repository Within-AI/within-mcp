# Engines: launcher flags, reviewer flags and named fallbacks

One engine end to end. `launch.py` builds the unit launcher argument list below for the run's engine; `review.py` and the fidelity check build the read-only reviewer list. The protected run paths are `run/frozen/`, `run/records/`, `run/residuals.json` and `run/run-log.jsonl`; no unit may write them.

## Anthropic

### Unit launcher

- `claude -p` headless, with stdin closed.
- `--permission-mode acceptEdits`
- `--strict-mcp-config`
- `--output-format stream-json --verbose`
- `--tools <tool list>` and the matching allowed-tools list from the unit's spec.
- `--settings <file>` turning on Claude Code's Bash sandbox:
  - `"sandbox": {"enabled": true, "autoAllowBashIfSandboxed": true, "failIfUnavailable": true, "allowUnsandboxedCommands": false}`
  - `sandbox.filesystem.allowWrite` holds the unit temp folder plus the worktree's git admin folder, `<common>/objects`, `<common>/refs` and `<common>/logs` (the same git roots as the OpenAI list), each checked to lie outside the protected run paths.
  - deny rules (`permissions.deny` for Edit and Write) keeping `run/frozen/`, `run/records/`, `run/residuals.json` and `run/run-log.jsonl` unwritable.

The Bash sandbox must be available on the build host. With `failIfUnavailable: true`, a unit without it exits at startup instead of running unconfined; `launch.py` records that exit, and the unit is never retried unsandboxed.

### Read-only reviewer

A fresh Claude session given only `--tools Read,Glob,Grep` (no Bash, Edit or Write). It reads the diff file `review.py` writes first (`git diff <base>..HEAD` and the file list, at `reviews/<U>-r<n>.diff`), then the files it names.

## OpenAI

### Unit launcher

```
codex exec --sandbox workspace-write -c approval_policy="never" --ignore-user-config --json -C <worktree>
```

- `TMPDIR` set to a writable temp folder for the unit.
- Extra writable roots: the worktree's git admin folder, `.git/objects`, `.git/refs` and `.git/logs`, so commits work inside the sandbox.

### Read-only reviewer

`codex exec --sandbox read-only`, a fresh session per review.

## Named fallbacks

- **Phoenix client 0.17.2** (or the otel family locked below 1.45) when a newer tracing client breaks; tracing is never a gate either way.
- **Codex on a data-residency OpenAI project:** point it at the regional host with `-c openai_base_url="<regional API host>"`.
- **`NODE_USE_ENV_PROXY=1`** on Node 24 behind a proxy.
- **Node 22+** for OpenAI runtimes on Node.
- **The OpenAI project region** is a prerequisite: confirm it before the first OpenAI session.
