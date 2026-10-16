# gate — completion gate (level local)

An agent's "done" is a claim. `gate` reads the unit spec (and optionally the unit's RETURN.md),
checks the facts behind the claim (git and a fresh run of the done-check) and reports claimed vs
verified per check. It only reports: it never changes the repo, retries, merges or pushes. It
writes only its own logs.

This is a curated copy of the parallel-build-loop gate. It keeps level local (checks 1–5) unchanged.

## Not shipped

The merge level is not shipped: checks 6–10 (`pushed`, `pull_request`, `ci_checks`, `review_threads`, `review_decision`), the options that fed them and every `gh` call need `gh` and the GitHub API, which a customer build may not have; merge state is checked by people and CI. `--level merge` exits 2 with "merge level not shipped" in the report's warnings and on stderr.

## Usage

```
./gate check <unit-spec.md> [--return <RETURN.md>] [--worktree <path>] [--from <commit>] [--log-root <dir>]
```

- Repo = the spec's "Repo or folder". With `--worktree`, paths under "Repo or folder" (Owns, Must
  not edit, done-check working directory) are mapped to the worktree.
- `--from <commit>`: the commit the orchestrator last moved the unit onto, after a reset, rebase or
  merge. Checks 2–4 are then judged on the unit's own range, `<from>..HEAD`.
- Any other option (including `--level` with a value other than `merge`) is a usage error: exit 2,
  one JSON object with verdict `unknown`.
- stdout: exactly one JSON object with `level` (always `local`), `checked_commit`, `verdict`,
  `checks`, `claims`, `format_errors`, `warnings`. Each check has `name, required, verified
  (true/false/null), verdict, evidence`; evidence holds the command, exit code, commit, source and log path.
- stderr: a Markdown table, one row per check, then the verdict, warnings and claim comparison.

Use `check` and a completed unit spec, including all headings from `templates/unit-spec.md`. Substitute absolute paths and shell-quote paths containing spaces. The bundled gate already uses level local; `--level local` is not a supported option. Keep logs outside the checked worktree. A done-check command such as `false`, with expected exit 0, produces exit 1 and a failed `done_check` entry; a missing `check` subcommand produces exit 2 and verdict `unknown`.

## Log root

Each run writes one folder `<log root>/<run-id>/` (one log per check, the done-check output,
`process-snapshot.txt`, `report.json`). The log root is, in this order:

1. `$GATE_LOG_ROOT` when set (an empty value means the current folder);
2. else `--log-root DIR`;
3. else `./run/logs/gate/` under the current folder.

## The five local checks

1. **committed** — the `git status --porcelain --untracked-files=all` snapshot taken immediately
   before the done-check is empty (ignored files excluded); unreadable = unknown.
2. **right_base** — the spec's base commit (or the `--from` commit) is an ancestor of HEAD. If HEAD
   trails the spec's branch, a warning gives the count; a stale base is not a fail.
3. **in_lane** — every path changed in the range (every commit; both sides of a rename; merges
   diffed against each parent) is under an "Owns" entry and not under a "Must not edit" entry.
4. **must_not_edit** — no changed path is under a "Must not edit" entry (it wins over "Owns").
5. **done_check** — the done-check runs fresh with `/bin/bash -lc` in its working directory, in its
   own process group. The gate waits until every member of the group has exited; a member still
   alive at the timeout is killed and the check fails. Timeout or exit mismatch = fail; failure to
   start or unreadable git state = unknown; HEAD changing during the run = fail. A git status change
   during the run is recorded and warned, not failed.

## Claims (`--return`)

The six header lines are parsed. `commit` matches if it is HEAD, or an ancestor of HEAD where every
later commit changes only top-level `RETURN.md` / `done-check.log`. `done_check_exit` is compared
with the fresh exit code. `status`, `done_check_log` and the criteria lists are shown as unverified
claims. For external logs, set `done_check_log: none` and record their paths and hashes below the headers. Require the commit and done-check exit comparisons to match as well as a passing verdict. Claims do not change the verdict; a malformed header is unknown, exit 2.

## Verdicts and exit codes

- **pass** (exit 0): every required check verified true.
- **fail** (exit 1): any check false.
- **unknown** (exit 2): no check false and at least one could not be read. Unknown never becomes
  pass. A malformed spec, an unresolved placeholder, a missing worktree, a usage error or a crash
  in the gate is also unknown.
- Placeholders: `<...>` counts only in a field value, backticked or not; in criteria and other prose
  it is ordinary text. Path templates: in Outcome and North star only, a backticked `<name>` next to
  a `/` (for example `` `<run>/stages/rules.json` ``) is literal text.
- Owns and Must not edit entries must each be one absolute path, optionally ending in `/**`; any
  other form is a format error. A format error stops only the checks that read that field, and the
  verdict stays unknown while any exists.

Tests: `/usr/bin/python3 tests/test_gate.py` (see `tests/README.md`).
