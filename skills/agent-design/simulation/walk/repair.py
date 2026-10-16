"""The repair guard: a repair may only touch files inside RUN/simulation/, never the simulation input.

Rules, the test suite, the frozen expected values, frozen upstream inputs and every other stage's files
are protected. A relative path is resolved against the run folder (never the working folder); `..` and
symlinks are resolved before the comparison.
"""
import os

SIM_DIR = "simulation"
SIM_INPUT = os.path.join("simulation", "sim-input.json")


class RepairRefused(Exception):
    """The repair would touch a protected file."""


def _resolved(run_dir, path):
    run_real = os.path.realpath(run_dir)
    full = path if os.path.isabs(path) else os.path.join(run_real, path)
    return run_real, os.path.realpath(full)


def guard(run_dir, path):
    """Return None when a repair may write `path`; raise RepairRefused otherwise."""
    run_real, target = _resolved(run_dir, path)
    sim_dir = os.path.join(run_real, SIM_DIR)
    if os.path.commonpath([sim_dir, target]) != sim_dir or target == sim_dir:
        raise RepairRefused("repair refused: %s is outside simulation/" % path)
    if target == os.path.join(run_real, SIM_INPUT):
        raise RepairRefused("repair refused: %s is the simulation input" % path)


def repair(run_dir, path, text, repairs):
    """Write `text` to `path` through the guard and record the attempt in `repairs` (walk.json repairs[])."""
    run_real, target = _resolved(run_dir, path)
    shown = os.path.relpath(target, run_real).replace(os.sep, "/")
    try:
        guard(run_dir, path)
    except RepairRefused as exc:
        repairs.append({"path": shown, "applied": False, "refusal": str(exc)})
        return False
    with open(target, "w", encoding="utf-8") as fh:
        fh.write(text)
    repairs.append({"path": shown, "applied": True, "refusal": None})
    return True
