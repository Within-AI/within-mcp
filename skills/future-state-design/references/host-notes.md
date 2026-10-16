# Host notes

Every host must offer `python3` (see `SKILL.md`). The stages run one after another in the session that started the skill, on every host; no host needs to start other sessions or agents. These notes cover what two hosts need beyond that, from the Cowork and Codex desktop smoke tests.

## The person's page on every host

`fsd.py` publishes `outputs/future-state-design.html` from `init` and tries once to open it (Chrome first, then the system opener). Where nothing can open it (Cowork has no browser on the workspace; a sandboxed Codex may refuse the opener), it is recorded in `builder/live-page.json` and the run continues; the page line at the end of every status block gives the path. In Cowork, copy `outputs/` to the connected folder after `init` and after every stage, so the page the person opens is current.

## Cowork

- **Bring the inputs in.** Copy the inputs (the frozen snapshot folder, and any recommendation report or design prompt file) from the connected folder into the cloud workspace before `init`. The run reads only the copies.
- **Bring the run back after every stage.** After each stage's `finish` (and after `repair`, `direction` and `platform`), copy the run folder back to the connected folder with a sha256 check: copy, then re-read every copied file and compare its sha256 with the workspace original, because a copy made straight after a write can deliver a stale version. If any file differs, copy it again and re-check; say plainly which files still differ, if any. The connected folder then always holds the run as of the last stage, so a stopped session loses nothing.
- **Copy it in again before a resume.** A new session starts from the connected folder: copy the run folder from it into the cloud workspace before any other `fsd.py` command on the run, then run `scripts/fsd.py resume --run <run> --copied-from <the run folder in the connected folder>`. It does the sha256 check (every file in the connected copy against the workspace copy) and records the result in the run's `events.jsonl`; when it names files that differ, copy those again and run it again.
- **Leave only the run folder in the person's folder.** Copy file by file where the host allows it. If the copy needs helper files (an archive, a checksum list), keep them in one hidden folder, `.fsd-copy/`, beside the run folder, never loose next to it, and delete it once the copy is verified.
- **The knowledge hash comes from `python3 scripts/knowledge.py`, never from hashing MANIFEST.json's bytes.** For an install-integrity check, run `python3 scripts/knowledge.py` from the skill's folder and read its `manifest_sha256:` line; that is the value a run records in `state.json` (`knowledge_manifest_sha256`). Do not hash MANIFEST.json's bytes: that is a different number.

## Codex desktop

- **Open the skill's folder as the project.** The app lists the skill only when its folder is the open project. A session rooted elsewhere did not list it, and its writes needed escalations. Setup notes for customers should say to open the project folder.
- **Read the knowledge hash the same way.** For an install-integrity check, run `python3 scripts/knowledge.py` in the open project folder and take its `manifest_sha256:` line; it is the only way to get the hash, and it matches what the run's `state.json` records. Do not hash MANIFEST.json's bytes: that is a different number.
- **The evidence addendum and snapshot building need the Within MCP, which does not work on Codex desktop until FORDEEN-91 is fixed.**

## Kept open

- Code by default versus agent fallback.

## What to say to the person

Don't print experiment caveats as client statements. The smoke tests behind these notes were runs on test material; their caveats are about the host, never about the person's processes.
