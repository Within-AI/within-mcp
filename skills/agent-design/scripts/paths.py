#!/usr/bin/env python3
"""Map a path named upstream to the same file inside the run folder, or refuse to name it.

resolve(path, run_folder) -> str returns the file's path relative to run_folder, with `/` separators.
A relative path is checked under run_folder. An absolute path (POSIX `/...`, Windows `C:\\...` or `\\\\...`,
recognised on any machine, split on `/` and `\\`) is matched by its longest trailing part that exists under
run_folder; a match on the file name alone counts only when exactly one file in the run folder has that name.
Anything else raises Unresolved naming the path: no match, a longest match that hits more than one entry, a
match that is a folder, a `..` segment, or a result that lies outside run_folder once symbolic links are followed.
Standard library only.
"""
import os
import re

_WIN_ABS = re.compile(r"^[A-Za-z]:[\\/]")


class Unresolved(Exception):
    """The file cannot be found in the run folder; the message names the path."""


def _is_absolute(path):
    return path.startswith("/") or path.startswith("\\") or bool(_WIN_ABS.match(path))


def _parts(path):
    parts = [p for p in re.split(r"[\\/]+", path) if p not in ("", ".")]
    if parts and re.match(r"^[A-Za-z]:$", parts[0]):
        parts = parts[1:]
    return parts


def _inside(run_real, candidate):
    real = os.path.realpath(candidate)
    return real == run_real or real.startswith(run_real + os.sep)


def _entries(run):
    """Every entry under run as (parts, is_regular_file); symbolic links are listed, never descended."""
    out = []
    for root, dirs, files in os.walk(run, followlinks=False):
        rel_root = os.path.relpath(root, run)
        base = [] if rel_root == "." else rel_root.split(os.sep)
        for d in dirs:
            out.append((base + [d], False))
        for f in files:
            full = os.path.join(root, f)
            out.append((base + [f], os.path.isfile(full)))
    return out


def resolve(path, run_folder):
    if not isinstance(path, str) or not path.strip():
        raise Unresolved("path cannot be resolved in the run folder: %r" % (path,))
    run = os.path.abspath(run_folder)
    run_real = os.path.realpath(run)
    parts = _parts(path)
    if not parts or ".." in parts:
        raise Unresolved("path cannot be resolved in the run folder: %s" % path)

    if not _is_absolute(path):
        candidate = os.path.join(run, *parts)
        if os.path.isfile(candidate) and _inside(run_real, candidate):
            return "/".join(parts)
        raise Unresolved("path not found as a file in the run folder: %s" % path)

    entries = _entries(run)
    for k in range(len(parts), 0, -1):
        tail = parts[-k:]
        matches = [(p, f) for p, f in entries if len(p) >= k and p[-k:] == tail]
        if not matches:
            continue
        if len(matches) > 1:
            raise Unresolved("path matches more than one entry in the run folder: %s" % path)
        rel_parts, is_file = matches[0]
        candidate = os.path.join(run, *rel_parts)
        if not is_file:
            raise Unresolved("path matches a folder, not a file, in the run folder: %s" % path)
        if not _inside(run_real, candidate):
            raise Unresolved("path resolves outside the run folder: %s" % path)
        return "/".join(rel_parts)
    raise Unresolved("path not found in the run folder: %s" % path)
