"""Agent Build: run config validation (pins P1, P2b, P10, G3, G4, H5, I11).

  config.py validate --run R --phase settings|build [--prefs PATH]

Writes R/records/config-<phase>.json before exiting (except on a usage error).
Exit 0 valid, 1 a listed rejection, 2 usage error or unreadable input.

Spellings are read leniently: engine, build_host and block option ids are compared after folding case and
treating spaces, underscores and hyphens alike ("Anthropic", "claude code", "claude_code", "OpenAI", "Codex").
When a value is read that way, config.json is rewritten with the canonical id (so every later script reads the
same value) and the reading is listed under "readings" in the record. An option id that matches no option in
blocks.json and carries no person-added fields is an open item, not a rejection.
Standard library only, 3.9-safe.
"""
import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _records  # noqa: E402

ENGINES = ("anthropic", "openai")
BUILD_HOSTS = ("claude-code", "codex")
OPTION_ENGINES = ("anthropic", "openai", "any")
BLOCK_IDS = (
    "runtime", "execution_layer", "model", "tools", "activity_log", "calculation",
    "persistence", "state", "tracing", "scripted_evals", "judge", "launcher", "reviewer",
)
# Keys this script knows; anything else in config.json is listed as an open item.
KNOWN_KEYS = (
    "engine", "build_host", "cross_engine_review", "allow_preview_engine",
    "preferences_path", "blocks", "processes", "shared_blocks", "process_blocks",
    "run_id", "agent_host", "target",
)


def canonical(value):
    """Comparison key with case and separators folded: ' Claude_Code ' and 'open ai' -> 'claudecode', 'openai'."""
    if not isinstance(value, str):
        return value
    return re.sub(r"[\s_-]+", "", value.lower())


def read_as(value, ids):
    """The id in IDS that VALUE names once case and separators are folded, else None."""
    folded = canonical(value)
    for known in ids:
        if canonical(known) == folded:
            return known
    return None


def normalise(config, blocks_doc):
    """Rewrite obvious spellings to canonical ids in place; return the list of readings taken."""
    readings = []

    def take(holder, key, ids, where):
        value = holder.get(key)
        if not isinstance(value, str) or value in ids:
            return
        known = read_as(value, ids)
        if known is not None:
            holder[key] = known
            readings.append({"key": where, "given": value, "read_as": known})

    take(config, "engine", ENGINES, "engine")
    take(config, "build_host", BUILD_HOSTS, "build_host")
    options = block_options(blocks_doc) if isinstance(blocks_doc, dict) else {}
    sections = [("blocks", config.get("blocks"))]
    process_blocks = config.get("process_blocks")
    if isinstance(process_blocks, dict):
        sections += [("process_blocks.%s" % pid, process_blocks[pid]) for pid in sorted(process_blocks)]
    for prefix, blocks in sections:
        if not isinstance(blocks, dict):
            continue
        for block_id in sorted(blocks):
            spec = blocks[block_id]
            if isinstance(spec, dict) and block_id in options:
                take(spec, "choice", tuple(options[block_id]), "%s.%s.choice" % (prefix, block_id))
    return readings


def blocks_path():
    skill = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(skill, "references", "blocks.json")


def load(run):
    """Return (config, errors): errors is a list of reasons; config is None when unusable."""
    config, err = _records.load_json(os.path.join(run, "config.json"))
    if err:
        return None, ["config.json " + err]
    if not isinstance(config, dict):
        return None, ["config.json is not a JSON object"]
    return config, []


def openai_status(blocks_doc):
    """'offered' only when blocks.json says offered with non-empty status_evidence; else 'preview'."""
    if not isinstance(blocks_doc, dict):
        return "preview"
    engines = blocks_doc.get("engines")
    openai = engines.get("openai") if isinstance(engines, dict) else None
    if not isinstance(openai, dict):
        return "preview"
    evidence = openai.get("status_evidence")
    if openai.get("status") == "offered" and isinstance(evidence, str) and evidence.strip():
        return "offered"
    return "preview"


def check_settings(config, blocks_doc, prefs_override, run):
    reasons, items = [], []
    engine = config.get("engine")
    if "engine" not in config:
        reasons.append("engine: missing")
    elif not isinstance(engine, str):
        reasons.append("engine: not a string (%r)" % (engine,))
    elif engine not in ENGINES:
        reasons.append("engine: unsupported %r (anthropic or openai)" % engine)
    host = config.get("build_host")
    if not isinstance(host, str) or host not in BUILD_HOSTS:
        reasons.append("build_host: %r is not claude-code or codex" % (host,))
    if engine == "openai" and openai_status(blocks_doc) == "preview" \
            and config.get("allow_preview_engine") is not True:
        reasons.append("engine: openai is preview and allow_preview_engine is not true")

    prefs = prefs_override if prefs_override is not None else config.get("preferences_path")
    if prefs is not None and not isinstance(prefs, str):
        items.append({"id": "config:preferences_path",
                      "summary": "preferences_path is not a string: %r" % (prefs,)})
    elif isinstance(prefs, str):
        full = prefs if os.path.isabs(prefs) else os.path.join(run, prefs)
        if not os.path.isfile(full):
            items.append({"id": "config:preferences-file",
                          "summary": "preferences file not found: %s" % prefs})
    for key in sorted(config):
        if key not in KNOWN_KEYS:
            items.append({"id": "config:unknown-key:%s" % key,
                          "summary": "unknown config key %r" % key})
    return reasons, items, prefs


def block_options(blocks_doc):
    """Map block id -> {option id: engine} from blocks.json."""
    found = {}
    for block in blocks_doc.get("blocks") or []:
        if isinstance(block, dict) and isinstance(block.get("id"), str):
            opts = {}
            for opt in block.get("options") or []:
                if isinstance(opt, dict) and isinstance(opt.get("id"), str):
                    opts[opt["id"]] = opt.get("engine")
            found[block["id"]] = opts
    return found


def hash_if_read(hashes, name, path):
    """Hash a file's bytes before any parsing, so a readable but invalid file is still recorded."""
    try:
        hashes[name] = _records.sha256_file(path)
    except OSError:
        pass


def numbers_exist(run):
    """True, False, or None when it cannot be decided; also the files read."""
    read = {}
    hash_if_read(read, "brief.json", os.path.join(run, "brief.json"))
    hash_if_read(read, "capabilities.json", os.path.join(run, "capabilities.json"))
    brief, _ = _records.load_json(os.path.join(run, "brief.json"))
    caps, _ = _records.load_json(os.path.join(run, "capabilities.json"))
    if isinstance(brief, dict) and isinstance(brief.get("numbers"), list) and brief["numbers"]:
        return True, read
    produces = None
    if isinstance(caps, dict) and isinstance(caps.get("capabilities"), dict):
        produces = caps["capabilities"].get("produces_numbers")
    if produces is True:
        return True, read
    if produces is False:
        return False, read
    return None, read


def engine_fits(block_id, option_engine, engine, cross):
    if option_engine == "any" or option_engine == engine:
        return True
    return block_id == "reviewer" and option_engine in ENGINES and cross is True


def check_block(where, block_id, spec, options, engine, cross):
    """Return (reasons, items, state) for one block spec; state is chosen|dropped|open."""
    if isinstance(spec, dict) and spec.get("dropped") is True:
        reason = spec.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            return ["%s: dropped without a reason" % where], [], "dropped"
        return [], [], "dropped"
    choice = spec.get("choice") if isinstance(spec, dict) else None
    if not isinstance(choice, str) or not choice:
        return [], [{"id": "block:%s" % where,
                     "summary": "%s is neither chosen nor dropped" % where}], "open"
    known = options.get(block_id, {})
    if choice in known:
        opt_engine = known[choice]
        if not engine_fits(block_id, opt_engine, engine, cross):
            if block_id == "reviewer":
                return ["%s: reviewer %r is on engine %r while cross_engine_review is not true"
                        % (where, choice, opt_engine)], [], "chosen"
            return ["%s: option %r is for engine %r, config engine is %r"
                    % (where, choice, opt_engine, engine)], [], "chosen"
        return [], [], "chosen"
    person_keys = ("added_by", "engine", "reason")
    if not any(k in spec for k in person_keys):
        return [], [{"id": "block:%s:unknown-option" % where,
                     "summary": "%s: option id %r is not in blocks.json and not person-added; "
                                "confirm the option or add added_by, engine and reason" % (where, choice)}], "open"
    reasons = []
    added_by = spec.get("added_by")
    if not isinstance(added_by, str) or not added_by.strip():
        reasons.append("%s: person-added choice %r has no added_by" % (where, choice))
    reason = spec.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        reasons.append("%s: person-added choice %r has no reason" % (where, choice))
    added_engine = spec.get("engine")
    if added_engine not in OPTION_ENGINES:
        reasons.append("%s: person-added choice %r has engine %r (anthropic, openai or any)"
                       % (where, choice, added_engine))
    elif not engine_fits(block_id, added_engine, engine, cross):
        reasons.append("%s: person-added choice %r is for engine %r, config engine is %r"
                       % (where, choice, added_engine, engine))
    return reasons, [], "chosen"


def required_check(where, block_id, state, numbers):
    """Return (reasons, items) for the required-block rules on one effective selection."""
    if block_id == "activity_log" and state in ("absent", "dropped"):
        return ["%s: activity_log is %s; it is required" % (where, state)], []
    if block_id == "calculation" and state in ("absent", "dropped") and numbers is True:
        return ["%s: calculation is %s while numbers exist" % (where, state)], []
    if state == "absent":
        return [], [{"id": "block:%s" % where,
                     "summary": "%s is neither chosen nor dropped" % where}]
    return [], []


def check_build(config, blocks_doc, run):
    reasons, items = [], []
    options = block_options(blocks_doc)
    engine = config.get("engine")
    cross = config.get("cross_engine_review")
    blocks = config.get("blocks")
    if not isinstance(blocks, dict):
        blocks = {}
    numbers, read = numbers_exist(run)
    if numbers is None:
        items.append({"id": "block:calculation-numbers",
                      "summary": "whether numbers exist cannot be decided (needs more information); "
                                 "calculation stays unresolved"})
    for block_id in BLOCK_IDS:
        where = "blocks.%s" % block_id
        if block_id not in blocks:
            state = "absent"
        else:
            r, i, state = check_block(where, block_id, blocks[block_id], options, engine, cross)
            reasons.extend(r)
            items.extend(i)
        r, i = required_check(where, block_id, state, numbers)
        reasons.extend(r)
        items.extend(i)
    for key in sorted(blocks):
        if key not in BLOCK_IDS:
            items.append({"id": "block:unknown:%s" % key, "summary": "unknown block id %r" % key})

    process_blocks = config.get("process_blocks")
    if not isinstance(process_blocks, dict):
        process_blocks = {}
    for pid in sorted(process_blocks):
        overrides = process_blocks[pid]
        if not isinstance(overrides, dict):
            continue
        for block_id in sorted(overrides):
            if block_id in BLOCK_IDS:
                where = "process_blocks.%s.%s" % (pid, block_id)
                r, i, state = check_block(where, block_id, overrides[block_id], options, engine, cross)
                reasons.extend(r)
                items.extend(i)
                if state == "dropped":
                    r, i = required_check(where, block_id, state, numbers)
                    reasons.extend(r)
                    items.extend(i)
    shared = config.get("shared_blocks")
    if isinstance(shared, dict):
        for block_id in sorted(shared):
            for pid in sorted(process_blocks):
                overrides = process_blocks[pid]
                if isinstance(overrides, dict) and block_id in overrides:
                    reasons.append("shared_blocks.%s: shared block chosen twice "
                                   "(also in process_blocks.%s.%s)" % (block_id, pid, block_id))
    return reasons, items, read


def write_and_exit(run, phase, verdict, reasons, items, prefs, hashes, readings=()):
    record = _records.record_header(_records.SKILL_VERSION, hashes, {})
    record.update({"verdict": verdict, "reasons": reasons, "phase": phase,
                   "preferences_path": prefs, "open_items": items, "readings": list(readings)})
    folder = os.path.join(run, "records")
    if not os.path.isdir(folder):
        os.makedirs(folder)
    _records.write_record(os.path.join(folder, "config-%s.json" % phase), record)
    print("config %s: %s" % (phase, verdict))
    for reading in readings:
        print("  read %s %r as %r" % (reading["key"], reading["given"], reading["read_as"]))
    for reason in reasons:
        print("  rejected: " + reason)
    for item in items:
        print("  open item: %s (%s)" % (item["id"], item["summary"]))
    return {"pass": _records.OK, "fail": _records.FAILED}.get(verdict, _records.USAGE)


def validate(run, phase, prefs_override):
    hashes = {}
    config, errors = load(run)
    try:
        if os.path.isfile(os.path.join(run, "config.json")):
            hashes["config.json"] = _records.sha256_file(os.path.join(run, "config.json"))
    except OSError as exc:
        config, errors = None, ["config.json unreadable: %s" % exc.strerror]
    if config is None:
        return write_and_exit(run, phase, "unknown", errors, [], prefs_override, hashes)
    bpath = blocks_path()
    hash_if_read(hashes, "blocks.json", bpath)
    blocks_doc, berr = _records.load_json(bpath)
    readings = normalise(config, blocks_doc)
    if readings:
        # Self-repair: write the canonical ids back so every later script reads the same value.
        _records.write_record(os.path.join(run, "config.json"), config)
        hashes["config.json"] = _records.sha256_file(os.path.join(run, "config.json"))
    reasons, items, prefs = check_settings(config, blocks_doc, prefs_override, run)
    if phase == "build":
        if berr is not None or not isinstance(blocks_doc, dict) \
                or not isinstance(blocks_doc.get("blocks"), list):
            why = berr.split(":")[0] if berr else "not a blocks object"
            return write_and_exit(run, phase, "unknown",
                                  ["references/blocks.json: %s" % why],
                                  items, prefs, hashes, readings)
        r, i, read = check_build(config, blocks_doc, run)
        reasons.extend(r)
        items.extend(i)
        hashes.update(read)
    return write_and_exit(run, phase, "fail" if reasons else "pass", reasons, items, prefs, hashes, readings)


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        sys.stderr.write("config.py: %s\n" % message)
        sys.exit(_records.USAGE)


def main(argv=None):
    parser = _Parser(prog="config.py", description="Validate the run config.")
    sub = parser.add_subparsers(dest="command")
    v = sub.add_parser("validate", help="validate R/config.json")
    v.add_argument("--run", required=True)
    v.add_argument("--phase", required=True, choices=("settings", "build"))
    v.add_argument("--prefs")
    args = parser.parse_args(argv)
    if args.command != "validate":
        parser.error("a subcommand is required: validate")
    if not os.path.isdir(args.run):
        parser.error("--run is not a folder: %s" % args.run)
    return validate(args.run, args.phase, args.prefs)


if __name__ == "__main__":
    sys.exit(main())
