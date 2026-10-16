#!/usr/bin/env python3
"""Run the least-cost cycle: input preflight, generation, output preflight.

run-stage.py --run-root DIR --stage snapshot --input intake.json
  --artifact snapshot.json --packet packet.md --engine script --cycle 1 -- COMMAND ...

The generator executes in DIR. Project/Factory review uses invoke-reviewer.sh;
Demo records the current worker's self-assessment with record-demo-assessment.py.
The input bundle is explicitly bound, never guessed from a folder or from prose.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import _checks
import _eventlog
import _reference_registry
import _stage_checks
import _timing
import _demo_policy

HERE = Path(__file__).resolve().parent


def append(root, event):
    result = subprocess.run([sys.executable, str(HERE / "append-event.sh"), "--log", str(root / "events.jsonl"), "--event", json.dumps(event)], capture_output=True, text=True)
    if result.returncode:
        raise ValueError(result.stderr.strip())


def run_checks(root, stage, run_id, names, artifact, cycle=1):
    env = dict(os.environ, FACTORY_STAGE=stage, FACTORY_RUN_ID=run_id, FACTORY_CYCLE=str(cycle))
    for check in names:
        command = [sys.executable, str(HERE / "run-check.sh"), "--check", check, "--artifact", artifact, "--append", "--log", "events.jsonl"]
        result = subprocess.run(command, cwd=root, env=env)
        if result.returncode:
            raise ValueError("preflight stopped at " + check)


def run_reference_checks(root, stage, run_id, artifact, cycle=1):
    """The worked-reference checks the registry requires for this stage, before its own checks."""
    env = dict(os.environ, FACTORY_STAGE=stage, FACTORY_RUN_ID=run_id, FACTORY_CYCLE=str(cycle))
    for check in _reference_registry.clearance_checks(stage):
        command = [sys.executable, str(HERE / "run-check.sh"), "--check", check, "--artifact", artifact,
                   "--append", "--log", "events.jsonl", "--", "--stage", stage, artifact]
        result = subprocess.run(command, cwd=root, env=env)
        if result.returncode:
            raise ValueError("preflight stopped at " + check)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-root", required=True)
    parser.add_argument("--stage", required=True, choices=sorted(_stage_checks.STAGES))
    parser.add_argument("--input", required=True)
    parser.add_argument("--artifact", required=True)
    parser.add_argument("--packet", required=True)
    parser.add_argument("--engine", choices=["script", "claude", "codex"], required=True)
    parser.add_argument("--cycle", type=int, default=1)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    try:
        root = Path(args.run_root).resolve()
        events = _eventlog.load_and_validate(str(root / "events.jsonl"), require_exists=True, typed=True)
        if args.engine == "codex" and not _demo_policy.is_demo(events):
            raise ValueError("Codex generation in this launcher is available only in Demo")
        run_id = events[0]["run_id"]
        packet = _stage_checks.latest(events, args.stage, "packet.authored")
        brief = _stage_checks.latest(events, args.stage, "brief.authored")
        if not packet or not brief or packet["payload"]["packet_path"] != args.packet or _checks.sha256_of(root / args.packet) != packet["payload"]["sha256"]:
            raise ValueError("requires the authored packet and paired review brief before generation")
        command = args.command[1:] if args.command[:1] == ["--"] else args.command
        if not command or args.cycle < 1:
            raise ValueError("requires a generator command and positive cycle")
        if (root / args.artifact).exists():
            raise ValueError("generation output exists; choose a new artifact path")
        rule = _stage_checks.STAGES[args.stage]
        run_checks(root, args.stage, run_id, [rule["input"]], args.input, args.cycle)
        actor = {"role": "executor", "engine": args.engine}
        base = {"run_id": run_id, "stage": args.stage, "actor": actor}
        append(root, {**base, "type": "executor.started", "payload": {"packet_path": args.packet, "cycle": args.cycle, "output_path": args.artifact,
                       "inputs": [{"path": args.input, "sha256": _checks.sha256_of(root / args.input)}]}})
        clock = _timing.start()
        try:
            result = subprocess.run(command, cwd=root)
        except OSError as exc:
            append(root, {**base, "type": "executor.aborted", "payload": {"reason": str(exc), "cycle": args.cycle, **_timing.finish(clock)}})
            raise
        timing = _timing.finish(clock)
        if result.returncode or not (root / args.artifact).is_file():
            append(root, {**base, "type": "executor.aborted", "payload": {"reason": "generator exit %d or missing artifact" % result.returncode, "cycle": args.cycle, **timing}})
            raise ValueError("generation failed")
        try:
            append(root, {**base, "type": "executor.submitted", "payload": {"artifact_path": args.artifact,
                           "sha256": _checks.sha256_of(root / args.artifact), "cycle": args.cycle, **timing}})
        except ValueError as exc:
            append(root, {**base, "type": "executor.aborted", "payload": {"reason": str(exc), "cycle": args.cycle, **timing}})
            raise
        run_reference_checks(root, args.stage, run_id, args.artifact, args.cycle)
        run_checks(root, args.stage, run_id, rule["checks"], args.artifact, args.cycle)
        print(("Ready for Demo self-assessment: " if _demo_policy.is_demo(events)
               else "Ready for LLM review: ") + args.stage)
    except (ValueError, KeyError, OSError, _eventlog.EventLogError) as exc:
        parser.exit(1, str(exc) + "\n")


if __name__ == "__main__":
    main()
