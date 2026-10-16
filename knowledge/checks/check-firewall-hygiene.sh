#!/usr/bin/env python3
# This file is Python despite its .sh name: run it directly or as `python3 <this file>`; `bash <this file>` cannot run it.
"""Firewall-hygiene check — the executor/control separation gate (cross-cutting controls; R1 §2.5).

Plain-language: "Do the grading keys and verdicts live under control/ (never under executor/), do their
filenames stay opaque (no semantic label that leaks what a key grades), and does no executor brief point
at control material?" This is the structural spine of the convention firewall — the executor never sees
its grading key. The orchestrator runs it at run setup and before any executor relay starts; a non-zero
exit blocks the relay. The `check-firewall-hygiene` internal tag is a footnote; the plain-language
question is the handle.

WHAT THIS CHECK IS — AND IS NOT (the enforcement model, R1 §2.5). It is *told* its two targets as explicit
arguments (never globbing a tree to discover them): EXECUTOR_DIR (holds executor briefs) and CONTROL_DIR
(holds grading keys, verdicts, and one manifest). It does exactly one kind of work — STRUCTURE and a
CLOSED SET: directory placement, filename-shape membership against a fixed anchored pattern, a closed
banned-label set, and a literal control-path-reference scan of executor text. Nothing else. It NEVER
makes a semantic-honesty judgment — never whether a rubric is fair, whether a brief is honest, whether the
withheld grading key is well-designed. That is the independent reviewer's and the human sign-off's job. A
passing check is NEVER, on its own, evidence that the firewall is honestly maintained — only that the
directory structure and filenames conform and that no executor brief points at control material.

THE THREE STRUCTURAL TESTS (all closed-set / structural).
  Test A — directory placement. Every file directly under CONTROL_DIR must be a control file: a grading
    key (GRADING_KEY_RE), a verdict (VERDICT_RE), or the manifest (basename == MANIFEST_BASENAME). A file
    under CONTROL_DIR matching none of the three is a violation. And no control-shaped file (grading-key
    or verdict shape) may appear anywhere under EXECUTOR_DIR (recursive) — a grading key misfiled into
    executor space is the primary firewall breach this test catches.
  Test B — opaque-ID filename shape + banned-label absence. Grading-key filenames match ^k-\\d+\\.md$
    (anchored whole); verdict filenames match ^k-\\d+\\.c\\d+\\.json$ (anchored whole). The manifest is the
    single allowed non-`k-` control file, named by the fixed constant MANIFEST_BASENAME (told, not
    inferred). Every filename that adopts the opaque `k-` control-ID prefix must carry NONE of the closed
    banned-label set {node, phase, rubric, grading, review, verdict, WITHHELD} as a substring — the six
    words case-insensitively, WITHHELD as the exact uppercase token (it is a filename-convention flag, not
    an English word). Rationale: the opaque id is the whole point — a semantic label in a control filename
    leaks what the grading key grades, which is exactly what the firewall hides. A non-`k-` filename under
    CONTROL_DIR is caught by Test A's allowed-shape rule instead (it is neither key, verdict, nor
    manifest), so the banned-label sub-test scopes to the `k-` family it protects and each defect is
    isolated to one rule.
  Test C — no executor->control pointer. No file under EXECUTOR_DIR (recursive) may contain a path
    reference into CONTROL_DIR: the basename of CONTROL_DIR used as a path segment (e.g. `control/`), or
    either control-file shape used as a path (.../k-<digits>.md, .../k-<digits>.c<digits>.json). A hit is a
    violation. This is a literal string/path scan, NOT a semantic read of whether the brief "really" leaks
    anything — that limit is deliberate and is the reviewer's job.

MANIFEST_BASENAME. Defaulted to `manifest.json`. A maintainer sets it to the run root's actual control
manifest basename; the check needs the filename only, never grading-key content. It is a fixed constant
(not inferred by scanning) so the gate cannot be steered by a planted file.

VERDICT. All three tests hold and both directories exist and are readable -> exit 0. Any Test A/B/C
failure -> exit 1, and the emitted manifest names each offending path and the test it failed (one line per
offense). A target directory missing or unreadable, wrong argument count, or a CONTROL_DIR with no file
named MANIFEST_BASENAME (a malformed run layout, not a firewall breach) -> exit 2, to stderr, never
conflated with a violation.

Content-only, cwd-independent, no network, no live tool; `actor.engine == script`. No third-party import:
this check reads directory listings and file text under the two named directories only.

Usage: check-firewall-hygiene.sh [--verbose] EXECUTOR_DIR CONTROL_DIR
Exit: 0 pass; 1 executor/control-separation violation; 2 setup error.
"""
import os
import re
import sys

MANIFEST_BASENAME = "manifest.json"

GRADING_KEY_RE = re.compile(r"^k-\d+\.md$")
VERDICT_RE = re.compile(r"^k-\d+\.c\d+\.json$")

# The opaque control-ID prefix the banned-label sub-test protects.
CONTROL_ID_PREFIX = "k-"

# Closed banned-label set. The six lowercase words are matched case-insensitively; WITHHELD is matched as
# the exact uppercase token (a filename-convention flag, not an English word).
BANNED_LABELS_CI = ("node", "phase", "rubric", "grading", "review", "verdict")
BANNED_LABEL_EXACT = "WITHHELD"

# Path-shaped control references for Test C (unanchored — they may sit inside a longer path in brief text).
CONTROL_PATH_SHAPES = (
    re.compile(r"k-\d+\.md\b"),
    re.compile(r"k-\d+\.c\d+\.json\b"),
)


def die(msg, code):
    sys.stderr.write(msg + "\n")
    sys.exit(code)


def _readable_dir(path):
    return os.path.isdir(path) and os.access(path, os.R_OK | os.X_OK)


def list_direct_files(path):
    out = []
    for name in sorted(os.listdir(path)):
        full = os.path.join(path, name)
        if os.path.isfile(full):
            out.append(name)
    return out


def walk_files(path):
    out = []
    for root, _dirs, files in os.walk(path):
        for name in sorted(files):
            out.append(os.path.join(root, name))
    return sorted(out)


def is_control_shape(name):
    return bool(GRADING_KEY_RE.match(name) or VERDICT_RE.match(name))


def banned_labels_in(name):
    hits = []
    low = name.lower()
    for w in BANNED_LABELS_CI:
        if w in low:
            hits.append(w)
    if BANNED_LABEL_EXACT in name:
        hits.append(BANNED_LABEL_EXACT)
    return hits


def read_text(path):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError as e:
        die("cannot read executor file %s: %s" % (path, e), 2)


def main(argv):
    verbose = False
    rest = []
    for a in argv:
        if a in ("--verbose", "-v"):
            verbose = True
        else:
            rest.append(a)
    if len(rest) != 2:
        die("usage: check-firewall-hygiene.sh [--verbose] EXECUTOR_DIR CONTROL_DIR", 2)
    executor_dir, control_dir = rest

    # --- setup gate (exit 2 for a malformed run layout, never conflated with a violation) ---
    if not _readable_dir(executor_dir):
        die("EXECUTOR_DIR missing or unreadable: %s (setup defect, not a firewall breach)" % executor_dir, 2)
    if not _readable_dir(control_dir):
        die("CONTROL_DIR missing or unreadable: %s (setup defect, not a firewall breach)" % control_dir, 2)

    control_files = list_direct_files(control_dir)
    if MANIFEST_BASENAME not in control_files:
        die("CONTROL_DIR has no manifest named %r: %s (malformed run layout -> setup defect, not a "
            "firewall breach)" % (MANIFEST_BASENAME, control_dir), 2)

    control_basename = os.path.basename(os.path.normpath(control_dir))
    executor_files = walk_files(executor_dir)

    if verbose:
        sys.stderr.write("firewall-hygiene scan (structure + closed set only):\n")
        sys.stderr.write("  EXECUTOR_DIR=%s (%d files)\n" % (executor_dir, len(executor_files)))
        sys.stderr.write("  CONTROL_DIR=%s (%d direct files); manifest=%r; control segment=%r\n"
                         % (control_dir, len(control_files), MANIFEST_BASENAME, control_basename + "/"))

    offenses = []

    # --- Test A + Test B over CONTROL_DIR direct files ---
    # A `k-` file that carries a banned label is reported as the banned-label offense and NOT also as a
    # bad-shape offense: an anchored control shape has no room for an extra segment, so a banned label
    # always breaks the shape too — reporting only the banned label gives the precise reason and isolates
    # each defect to one rule. A non-`k-` file (never the manifest here) is a bad-shape offense.
    for name in control_files:
        if name == MANIFEST_BASENAME:
            continue
        if name.startswith(CONTROL_ID_PREFIX):
            labels = banned_labels_in(name)
            if labels:
                offenses.append("Test B    %s -> control filename carries banned semantic label(s) %s "
                                "(the opaque `k-` id must not leak what the key grades)"
                                % (os.path.join(control_dir, name), ", ".join(repr(l) for l in labels)))
                continue
        if not is_control_shape(name):
            offenses.append("Test A/B  %s -> not an allowed control shape (expected a grading key "
                            "^k-<digits>.md$, a verdict ^k-<digits>.c<digits>.json$, or the manifest %r)"
                            % (os.path.join(control_dir, name), MANIFEST_BASENAME))

    # --- Test A over EXECUTOR_DIR: no control-shaped file may live in executor space ---
    for full in executor_files:
        base = os.path.basename(full)
        if is_control_shape(base):
            offenses.append("Test A    %s -> control-shaped file (grading key / verdict) misfiled under "
                            "EXECUTOR_DIR (the primary firewall breach)" % full)

    # --- Test C over EXECUTOR_DIR text: no executor brief may point at control material ---
    seg = control_basename + "/"
    for full in executor_files:
        # a control-shaped file already flagged in Test A is not double-scanned for a pointer to itself
        text = read_text(full)
        hit_reasons = []
        if seg in text:
            hit_reasons.append("control-directory segment %r" % seg)
        for rx in CONTROL_PATH_SHAPES:
            m = rx.search(text)
            if m:
                hit_reasons.append("control-file path shape %r" % m.group(0))
        if hit_reasons:
            offenses.append("Test C    %s -> executor brief points at control material (%s)"
                            % (full, "; ".join(hit_reasons)))

    print("firewall-hygiene manifest (generated by check-firewall-hygiene.sh — do not hand-edit):")
    print("  EXECUTOR_DIR=%s | CONTROL_DIR=%s | %d control files | %d executor files | %d offense(s)"
          % (executor_dir, control_dir, len(control_files), len(executor_files), len(offenses)))
    if offenses:
        die("firewall-hygiene FAILED — executor/control separation broken:\n  - %s\n  Structure + closed "
            "set only; a pass would attest layout, never that the firewall is honestly maintained "
            "(that is the independent review + human sign-off's job)." % "\n  - ".join(offenses), 1)
    print("PASS: control files carry opaque-ID filenames (no semantic labels), grading keys/verdicts live "
          "under control/ (never executor/), and no executor brief points at control material. (Structure "
          "+ closed set only — honesty is the review's job.)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
