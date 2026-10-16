#!/usr/bin/env python3
"""Stage a stage's worked references into the run root and print the block its executor receives.

Usage:
    stage-references.py --run-root DIR --stage KEY [--emit block|brief-block|json|none]
    stage-references.py --plugin-root

For a stage whose references are `available`, every level is copied (never overwritten) to
`DIR/references/<stage>/<level>/<file>` and pinned in `DIR/references/<stage>/staging.json`: the run keeps
the exact bytes it was handed, never a live pointer into the plugin. Re-running is idempotent; a staged
copy that differs from the registry is refused.

--emit block        (default) the executor's "Worked references" block. For a stage whose channel is
                    prepare-stage-briefs it includes the section heading; for a template channel the
                    template already carries the heading, so the output starts at the instruction.
                    A stage with no reference yet gets the fixed "No worked reference exists" text.
--emit brief-block  the review brief's block (the reviewer instruction plus artifact/sha256 pairs);
                    empty unless the stage's references exist.
--emit json         the staged exemplar set.
--emit none         stage only.

--plugin-root       print the full path of this skill folder (the folder holding `checks/`, `templates/`,
                    `scripts/`), derived from this script's own location, and exit. Fill every
                    `{{ PLUGIN_ROOT }}` in a packet or grading-key template with it, so every plugin file the
                    text names resolves from the run folder.

A stage that takes no reference by design prints nothing (a note goes to stderr) and exits 0.

Exit: 0 ok; 1 a staged copy differs from the registry; 2 unknown stage, registry drift or a stale
copy-guard string set.
"""
import argparse
import json
import sys

import _reference_registry as rr


def main(argv):
    if argv == ["--plugin-root"]:
        print(rr.plugin_root())
        return 0
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--run-root", required=True)
    parser.add_argument("--stage", required=True)
    parser.add_argument("--emit", choices=("block", "brief-block", "json", "none"), default="block")
    args = parser.parse_args(argv)
    try:
        reg = rr.load()
    except (OSError, ValueError) as exc:
        sys.stderr.write("registry unreadable: %s\n" % exc)
        return 2
    st = rr.state(args.stage, reg)
    if st is None:
        sys.stderr.write("unknown stage key %r (not in the reference registry)\n" % args.stage)
        return 2
    if st == "none-by-design":
        sys.stderr.write("stage %s takes no worked reference by design: %s\n"
                         % (args.stage, rr.stage_entry(args.stage, reg)["reason"]))
        return 0
    try:
        refs = rr.stage_references(args.run_root, args.stage, reg)
    except rr.RegistryStale as exc:
        sys.stderr.write("%s\n" % exc)
        return 2
    except rr.StagingError as exc:
        sys.stderr.write("%s\n" % exc)
        return 1
    if args.emit == "block":
        channel = rr.stage_entry(args.stage, reg)["channel"]
        sys.stdout.write(rr.render_packet_block(args.stage, with_heading=channel["kind"] == "prepare-stage-briefs",
                                                reg=reg))
    elif args.emit == "brief-block":
        sys.stdout.write(rr.render_brief_block(args.stage, reg))
    elif args.emit == "json":
        print(json.dumps(refs, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
