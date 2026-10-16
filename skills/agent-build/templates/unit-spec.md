# Unit spec: <unit id>

One spec per unit, written by `within:agent-build` from the split. Keep every heading in order. Replace every field placeholder before running the gate. Use one absolute path per bullet in Owns and Must not edit, optionally ending in `/**`; keep explanations outside those sections. Replace the default `<worktree>/run` paths with the actual absolute run location when it lives elsewhere. All paths outside Owns remain outside the unit's write scope. A unit never writes counted records; only the skill's scripts write them.

## Unit
<id> — <short name>

## Outcome
<One sentence a stranger could check as true or false.>

## North star
<One line from the brief's Objective.>

## Base
- Repo or folder: <worktree path>
- Branch: <branch>
- Commit: <full hash>

## Owns
- <absolute owned path>

## Must not edit
- <worktree>/run/frozen/**
- <worktree>/run/records/**
- <worktree>/run/residuals.json
- <worktree>/run/run-log.jsonl


## Inputs (read-only)
- <path> — <version or date> — <what it is>

## Acceptance criteria
1. <criterion a stranger could check as true or false>

## Done-check
- Command: <command>
- Working directory: <worktree>
- Expected exit code: 0
- Timeout: <minutes> minutes

Tests use the system temp folder, never `evidence/`.

## Tested against
- Criterion <n>: <real data or stub, and which>

## Time box
- Total: <minutes>
- Progress note at: <minutes>
- Cut order if short of time: <order>

## Permissions
- May write: <paths>
- Tools allowed: <tools>
- Tools denied: <tools>
- Headless flags: one of the two blocks below, by engine.

Anthropic:

```bash
claude -p "$(cat spec.md)" \
  --permission-mode acceptEdits \
  --strict-mcp-config \
  --output-format stream-json --verbose \
  --tools Bash,Read,Write,Edit,Glob,Grep \
  --allowedTools Bash Read Write Edit Glob Grep \
  --disallowedTools "Write(run/**)" "Edit(run/**)" "Bash(rm:*)"
```

The deny rules keep the run folder out of reach: the unit reads it, never writes it.

OpenAI:

```bash
export TMPDIR="<worktree>/.tmp"
codex exec \
  --sandbox workspace-write \
  -c approval_policy="never" \
  --ignore-user-config \
  --json \
  -C <worktree> \
  -c 'sandbox_workspace_write.writable_roots=["<worktree>/.tmp", "<git-dir>", "<common-dir>/objects", "<common-dir>/refs", "<common-dir>/logs"]' \
  "$(cat spec.md)"
```

`TMPDIR` points at a writable temp folder inside the worktree. The git paths the unit needs to commit are extra writable roots, exactly these four: the worktree's own admin folder `<git-dir>` (from `git -C <worktree> rev-parse --absolute-git-dir`), and `<common-dir>/objects`, `<common-dir>/refs` and `<common-dir>/logs` (`<common-dir>` from `git -C <worktree> rev-parse --path-format=absolute --git-common-dir`). Granting the whole `.git` folder is not enough: the sandbox still refuses the commit's lock file. `packed-refs` stays read-only.

## Output and log paths
- Deliverables: <paths>
- Log: <path>

## Checkpoint rule
Commit and write a draft return before any step expected to take more than 10 minutes.

## Review set
- <paths and diffs the reviewer reads>

## Return shape
The return file starts with these six lines, exactly:
status: done | partial | blocked | stopped
commit: <full hash>
done_check_exit: <integer, or not_run>
done_check_log: <path inside the repo, or none>
criteria_met: <numbers, or none>
criteria_not_met: <numbers, or none>

For an external return, use `done_check_log: none` and put the external log path and hash below the six header lines. Name the exact checked product commit.
