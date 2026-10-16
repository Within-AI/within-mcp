# Eval hardening: the later sequence

This sequence runs after the agent is built and testable. It is not part of this skill's statuses: the skill's `done` never waits for it, and nothing here changes a record the exit gate reads. The return advises it as the next step.

## Capability suites
Build capability suites from the frozen cases and new cases: the frozen cases stay as they are, and new cases cover what the build learned (edge cases, real documents as access arrives, cases people flagged).

## Regression suites
Turn every passing capability case and every fixed failure into a regression suite that runs on each change to the agent, through the same run-this-case runner.

## Judge calibration
Where judged checks matter, calibrate the judge against people's rulings on a labelled set before any judge verdict counts. Until then a judge stays advisory, as it is in this skill.

## Who rules on it
The people who own the process rule on new cases, on expected results and on whether a calibrated judge may count; the builder never writes the expected results it is tested against.
