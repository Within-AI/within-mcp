#!/usr/bin/env python3
"""Fidelity loop on an agent build's brief (U16).

round  --run R --brief FILE --inputs DIR...
ruling --run R --finding ID --by NAME --text TEXT
status --run R

Each round runs two read-only sessions (the fidelity check and the cross-walk),
first running one fix session in a fresh process when the last round left
findings. Ledger: R/records/fidelity.json. Round files: R/fidelity/round-<n>/.

Agents judge, then continue: readable input never stops for a person here. A
loosening (the brief reads looser than the design) is carried as an open finding
so the next fix session restores the design's stricter reading, and it is
recorded for the end-of-run review (ledger "review_items" and the run log). At
the fix-round cap the remaining findings become named open items for the
end-of-run review and the run continues (state "continued-at-cap"). A person's
ruling is still accepted at any time and reaches the next fix session.

Exit 0 clean round, or continued at the cap with named open items; 1 findings,
next round due; 2 usage, unreadable brief or inputs, or malformed session output.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _records  # noqa: E402
import log  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
PROMPTS = os.path.join(os.path.dirname(HERE), "prompts")
SESSION_TIMEOUT = 900
FIX_CAP = 3
KINDS = ("loosening", "inversion", "invention", "other")
# "stopped-loosening" and "stopped-cap" are older ledger states, read leniently:
# the first as "open", the second as "continued-at-cap".
STATES = ("clean", "open", "continued-at-cap", "stopped-loosening", "stopped-cap")
VERDICT = {0: "pass", 1: "fail", 2: "unknown"}
CAP_VERDICT = "pass_with_open_items"
STRICTER = "kept the design's stricter reading; the next fix session restores it"
FINDING_RE = re.compile(r"^finding:\s*([^|]*?)\s*\|\s*(.*\S)\s*$")
COUNT_RE = re.compile(r"^findings:\s*(\d+)\s*$")

CHECK_TOOLS = ["--tools", "Read,Glob,Grep", "--allowedTools", "Read", "Glob", "Grep",
               "--disallowedTools", "Bash", "Write", "Edit", "NotebookEdit"]


class Malformed(Exception):
    pass


class Usage(Exception):
    pass


# ---------- argv ----------

def check_argv(engine, prompt, run):
    if engine == "openai":
        return ["codex", "exec", "--skip-git-repo-check", "--sandbox", "read-only",
                "-C", os.path.abspath(run), prompt]
    return ["claude", "-p", prompt, "--strict-mcp-config", "--output-format", "stream-json",
            "--verbose"] + CHECK_TOOLS


def fix_argv(engine, prompt, fix_dir):
    if engine == "openai":
        return ["codex", "exec", "--ignore-user-config", "-c", 'approval_policy="never"',
                "--sandbox", "workspace-write", "-C", os.path.abspath(fix_dir), "--json", prompt]
    return ["claude", "-p", prompt, "--permission-mode", "acceptEdits", "--strict-mcp-config",
            "--output-format", "stream-json", "--verbose", "--tools", "Read,Edit,Write"]


def engines(run):
    """(check engine, fix engine) from R/config.json."""
    cfg, err = _records.load_json(os.path.join(run, "config.json"))
    if err or not isinstance(cfg, dict) or cfg.get("engine") not in ("anthropic", "openai"):
        raise Usage("config.json missing, unreadable or without a valid engine")
    engine = cfg["engine"]
    other = "openai" if engine == "anthropic" else "anthropic"
    return (other if cfg.get("cross_engine_review") is True else engine), engine


# ---------- helpers ----------

def rel(run, path):
    a, r = os.path.abspath(path), os.path.abspath(run)
    if a == r or a.startswith(r + os.sep):
        return os.path.relpath(a, r).replace(os.sep, "/")
    return path.replace(os.sep, "/")


def resolve(run, path):
    return path if os.path.isabs(path) else os.path.join(run, path)


def input_files(run, dirs):
    """Every file under each folder, following linked folders once; any walk error is raised."""
    files = []

    def fail(err):
        raise err

    for d in dirs:
        for root, subdirs, names in os.walk(d, onerror=fail, followlinks=True):
            real = os.path.realpath(root)
            for s in subdirs:
                target = os.path.realpath(os.path.join(root, s))
                if real == target or real.startswith(target + os.sep):
                    raise OSError("inputs folder links back into itself: %s" % rel(run, os.path.join(root, s)))
            subdirs.sort()
            for name in sorted(names):
                files.append(os.path.join(root, name))
    return files


def hashes(run, brief, files):
    out = {rel(run, brief): _records.sha256_file(brief)}
    for f in files:
        out[rel(run, f)] = _records.sha256_file(f)
    return out


def prompt_sections():
    path = os.path.join(PROMPTS, "fidelity-check.md")
    with open(path, encoding="utf-8") as f:
        text = f.read()
    parts = {}
    for title in ("Fidelity check", "Cross-walk"):
        m = re.search(r"^## " + re.escape(title) + r"\n(.*?)(?=^## |\Z)", text, re.S | re.M)
        if not m:
            raise Usage("prompt section missing: ## %s" % title)
        parts[title] = m.group(1).strip()
    return parts


def session_text(stdout):
    """The session's text: the stream-json result when present, else stdout."""
    result = None
    for line in stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if isinstance(obj, dict) and obj.get("type") == "result" and isinstance(obj.get("result"), str):
            result = obj["result"]
    return stdout if result is None else result


def parse_findings(text):
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    if not lines:
        raise Malformed("no output")
    m = COUNT_RE.match(lines[-1])
    if not m:
        raise Malformed("missing last line 'findings: <count>'")
    found = []
    for line in lines[:-1]:
        fm = FINDING_RE.match(line)
        if not fm:
            raise Malformed("line outside the output format: %s" % line)
        kind = fm.group(1).strip()
        if kind not in KINDS:
            raise Malformed("unknown finding kind: %s" % kind)
        found.append({"kind": kind, "text": fm.group(2).strip()})
    if int(m.group(1)) != len(found):
        raise Malformed("count %s disagrees with %d finding lines" % (m.group(1), len(found)))
    return found


def run_session(argv, cwd, run):
    try:
        p = subprocess.run(argv, cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, timeout=SESSION_TIMEOUT,
                           env=_records.subprocess_env(run), universal_newlines=True)
    except subprocess.TimeoutExpired:
        raise Malformed("session timed out after %d s" % SESSION_TIMEOUT)
    except OSError as err:
        raise Malformed("session could not start: %s" % err)
    if p.returncode != 0:
        raise Malformed("session crashed with exit %d" % p.returncode)
    return p.stdout, p.stderr


def write_text(path, text):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


# ---------- ledger ----------

def ledger_path(run):
    return os.path.join(run, "records", "fidelity.json")


def load_ledger(run):
    path = ledger_path(run)
    if not os.path.exists(path):
        return None, None
    obj, err = _records.load_json(path)
    if err or not isinstance(obj, dict) or obj.get("state") not in STATES:
        return None, err or "ledger malformed"
    obj.setdefault("rounds", [])
    obj.setdefault("rulings", [])
    obj.setdefault("review_items", [])
    if obj["state"] == "stopped-loosening":
        obj["state"] = "open"
    elif obj["state"] == "stopped-cap":
        obj["state"] = "continued-at-cap"
    return obj, None


def code_identity():
    return {"script_sha256": _records.sha256_file(os.path.abspath(__file__))}


def save(run, ledger, code, reasons, input_hashes=None):
    if input_hashes is None:
        input_hashes = ledger.get("input_hashes", {})
    rec = _records.record_header(_records.SKILL_VERSION, input_hashes, code_identity())
    for key in ("rounds", "state", "rulings"):
        rec[key] = ledger[key]
    rec["review_items"] = ledger.get("review_items", [])
    rec["verdict"] = CAP_VERDICT if code == 0 and ledger["state"] == "continued-at-cap" else VERDICT[code]
    rec["reasons"] = list(reasons)
    os.makedirs(os.path.join(run, "records"), exist_ok=True)
    _records.write_record(ledger_path(run), rec)
    return rec


def ruled_ids(ledger):
    return set(r.get("finding") for r in ledger["rulings"])


def note_loosenings(ledger, n, findings):
    """Record each loosening for the end-of-run review; the stricter reading is kept."""
    out = []
    for f in findings:
        if f["kind"] == "loosening":
            item = {"finding": f["id"], "kind": "loosening", "round": n, "text": f["text"],
                    "handling": STRICTER}
            ledger["review_items"].append(item)
            out.append(item)
    return out


def note_cap(ledger, n, findings):
    """Name every remaining finding as an open item for the end-of-run review."""
    known = set((i.get("finding"), i.get("kind")) for i in ledger["review_items"])
    out = []
    for f in findings:
        if (f["id"], "open-at-cap") in known:
            continue
        item = {"finding": f["id"], "kind": "open-at-cap", "round": n, "text": f["text"],
                "finding_kind": f["kind"],
                "handling": "fix-round cap of %d reached; left open for the end-of-run review" % FIX_CAP}
        ledger["review_items"].append(item)
        out.append(item)
    return out


def cap_reasons(ledger):
    items = [i for i in ledger["review_items"] if i.get("kind") == "open-at-cap"]
    reasons = ["open item %s for the end-of-run review: %s" % (i["finding"], i["text"]) for i in items]
    return reasons or ["fix-round cap of %d reached; no open items" % FIX_CAP]


def open_findings(ledger):
    return ledger["rounds"][-1]["findings"] if ledger["rounds"] else []


def log_round(run, n, clean, findings, state, review_items=None):
    payload = {"n": n, "clean": clean, "findings": findings, "state": state}
    if review_items:
        payload["review_items"] = review_items
    log.append(run, "fidelity.round", payload)


def finish(run, ledger, code, reasons, n, clean, count, input_hashes=None, review_items=None):
    save(run, ledger, code, reasons, input_hashes)
    log_round(run, n, clean, count, ledger["state"], review_items)
    for r in reasons:
        print(r)
    print("state: %s" % ledger["state"])
    return code


# ---------- commands ----------

def read_round_inputs(run, args):
    """(brief, files, hashes); raises Usage when the brief or an inputs folder is unreadable."""
    brief = resolve(run, args.brief)
    if not os.path.isfile(brief) or not os.access(brief, os.R_OK):
        raise Usage("brief unreadable: %s" % args.brief)
    dirs = [resolve(run, d) for d in args.inputs]
    for d, given in zip(dirs, args.inputs):
        if not os.path.isdir(d) or not os.access(d, os.R_OK | os.X_OK):
            raise Usage("inputs folder unreadable: %s" % given)
    try:
        files = input_files(run, dirs)
        now_hashes = hashes(run, brief, files)
        with open(brief, encoding="utf-8") as f:
            f.read()
    except (OSError, UnicodeDecodeError) as err:
        raise Usage("brief or inputs unreadable: %s" % err)
    return brief, files, now_hashes


def write_failed_round(rdir, reason, fixed):
    """Fill in every round file the failed round did not produce, saying why."""
    names = ["check.md", "crosswalk.md"] + (["brief-after.md", "fix.log"] if fixed else [])
    for name in names:
        path = os.path.join(rdir, name)
        if not os.path.exists(path):
            write_text(path, "not run or not produced: %s\n" % reason)
    _records.write_record(os.path.join(rdir, "findings.json"),
                          {"findings": None, "verdict": "unknown", "reason": reason})


def number_findings(n, found):
    return [{"id": "r%d-%d" % (n, k), "kind": f["kind"], "text": f["text"]} for k, f in enumerate(found, 1)]


def failed_round(run, ledger, rdir, n, found, prior, fixer, author, reason, now_hashes):
    """A session failed after the round already did something durable: keep it.

    A completed fix (the brief was replaced) or a loosening reported by a
    well-formed session is entered as a round that is not clean, carrying the
    prior findings, so the fix counts toward the cap and the loosening is kept
    as an open finding (recorded for the end-of-run review). With neither, the
    ledger is left as it was. Exit 2 (verdict unknown).
    """
    findings = number_findings(n, found)
    loose = [f for f in findings if f["kind"] == "loosening"]
    if fixer is None and not loose:
        return finish(run, ledger, 2, [reason], n, False, 0, now_hashes)
    findings += prior
    _records.write_record(os.path.join(rdir, "findings.json"),
                          {"findings": findings, "verdict": "unknown", "reason": reason})
    ledger["rounds"].append({"n": n, "findings": findings, "fixer_session": fixer,
                             "author_session": author, "clean": False})
    reasons = [reason]
    items = note_loosenings(ledger, n, loose)
    ledger["state"] = "open"
    reasons += ["loosening %s recorded for the end-of-run review (%s): %s" % (f["id"], STRICTER, f["text"])
                for f in loose]
    if fixer is not None:
        reasons.append("fix by %s recorded; open findings carried: %s"
                       % (fixer, ", ".join(f["id"] for f in prior) or "none"))
    return finish(run, ledger, 2, reasons, n, False, len(findings), now_hashes, items)


def cmd_round(args):
    run = args.run
    ledger, err = load_ledger(run)
    if err:
        raise Usage("ledger unreadable: %s" % err)
    if ledger is None:
        ledger = {"rounds": [], "state": "open", "rulings": [], "review_items": []}
    else:
        prev, _ = _records.load_json(ledger_path(run))
        ledger["input_hashes"] = prev.get("input_hashes", {})
    last_n = ledger["rounds"][-1]["n"] if ledger["rounds"] else 0
    last_count = len(open_findings(ledger))
    try:
        brief, files, now_hashes = read_round_inputs(run, args)
        check_engine, fix_engine = engines(run)
    except Usage as u:
        sys.stderr.write("fidelity.py: %s\n" % u)
        return finish(run, ledger, 2, [str(u)], last_n + 1, False, 0)

    if ledger["state"] == "continued-at-cap":
        note_cap(ledger, last_n, open_findings(ledger))
        return finish(run, ledger, 0, cap_reasons(ledger), last_n, False, last_count)
    if ledger["state"] == "clean":
        if now_hashes == ledger.get("input_hashes"):
            return finish(run, ledger, 0, ["clean round %d reused: brief and inputs unchanged" % last_n],
                          last_n, True, 0)
        ledger["state"] = "open"

    prior = open_findings(ledger)
    fix_rounds = sum(1 for r in ledger["rounds"] if r.get("fixer_session"))
    if prior and fix_rounds >= FIX_CAP:
        ledger["state"] = "continued-at-cap"
        items = note_cap(ledger, last_n, prior)
        return finish(run, ledger, 0, cap_reasons(ledger), last_n, False, last_count, None, items)

    sections = prompt_sections()
    n = last_n + 1
    rdir = os.path.join(run, "fidelity", "round-%d" % n)
    if os.path.isdir(rdir):
        shutil.rmtree(rdir)
    os.makedirs(rdir)
    author = os.environ.get("AB_SESSION_ID") or "orchestrating-session"
    fixer = None
    reasons = []

    if prior:
        fixer = "fixer-" + uuid.uuid4().hex
        used = set(r.get("fixer_session") for r in ledger["rounds"]) | {author}
        while fixer in used:
            fixer = "fixer-" + uuid.uuid4().hex
        fix_dir = os.path.join(rdir, "fix")
        os.makedirs(fix_dir)
        copy = os.path.join(fix_dir, os.path.basename(brief))
        shutil.copyfile(brief, os.path.join(rdir, "brief-before.md"))
        shutil.copyfile(brief, copy)
        with open(os.path.join(PROMPTS, "fidelity-fix.md"), encoding="utf-8") as f:
            fix_prompt = f.read().rstrip() + "\n\n## Brief copy\n\n%s\n\n## Open findings\n\n" % os.path.basename(brief)
        fix_prompt += "\n".join("- %s (%s): %s" % (f["id"], f["kind"], f["text"]) for f in prior) + "\n"
        if any(f["kind"] == "loosening" for f in prior):
            fix_prompt += ("\nFor each loosening, keep the design's stricter reading: bring the brief back "
                           "to what the design says.\n")
        ids = set(f["id"] for f in prior)
        rulings = [r for r in ledger["rulings"] if r.get("finding") in ids]
        if rulings:
            fix_prompt += "\n## Rulings\n\n" + "\n".join(
                "- %s, ruled by %s on %s: %s" % (r["finding"], r["by"], r["date"], r["text"]) for r in rulings) + "\n"
        try:
            out, errtext = run_session(fix_argv(fix_engine, fix_prompt, fix_dir), fix_dir, run)
            write_text(os.path.join(rdir, "fix.log"), "session: %s\n%s\n%s" % (fixer, out, errtext))
            shutil.copyfile(copy, os.path.join(rdir, "brief-after.md"))
            shutil.copyfile(copy, brief)
        except Malformed as m:
            write_text(os.path.join(rdir, "fix.log"), "session: %s\nfailed: %s\n" % (fixer, m))
            write_failed_round(rdir, "fix session failed: %s; the brief was not replaced" % m, True)
            return finish(run, ledger, 2, ["fix session failed: %s" % m], n, False, 0, now_hashes)
        now_hashes = hashes(run, brief, files)

    with open(brief, encoding="utf-8") as f:
        brief_text = f.read()
    listing = "\n".join("- " + rel(run, p) for p in files) or "- (none)"
    found = []
    for title, outname in (("Fidelity check", "check.md"), ("Cross-walk", "crosswalk.md")):
        prompt = "%s\n\n## Brief (%s)\n\n%s\n\n## Input files\n\n%s\n" % (
            sections[title], rel(run, brief), brief_text, listing)
        try:
            out, _ = run_session(check_argv(check_engine, prompt, run), run, run)
            text = session_text(out)
            write_text(os.path.join(rdir, outname), text)
            found.extend(parse_findings(text))
        except Malformed as m:
            if not os.path.exists(os.path.join(rdir, outname)):
                write_text(os.path.join(rdir, outname), "malformed: %s\n" % m)
            reason = "%s session output malformed: %s" % (title.lower(), m)
            write_failed_round(rdir, reason, fixer is not None)
            return failed_round(run, ledger, rdir, n, found, prior, fixer, author, reason, now_hashes)

    findings = number_findings(n, found)
    _records.write_record(os.path.join(rdir, "findings.json"), {"findings": findings})
    ledger["rounds"].append({"n": n, "findings": findings, "fixer_session": fixer,
                             "author_session": author, "clean": not findings})
    fix_rounds = sum(1 for r in ledger["rounds"] if r.get("fixer_session"))
    items = note_loosenings(ledger, n, findings)
    reasons += ["loosening %s recorded for the end-of-run review (%s): %s" % (i["finding"], STRICTER, i["text"])
                for i in items]
    if not findings:
        ledger["state"], code = "clean", 0
        reasons.append("round %d clean" % n)
    elif fix_rounds >= FIX_CAP:
        ledger["state"], code = "continued-at-cap", 0
        items = items + note_cap(ledger, n, findings)
        reasons += cap_reasons(ledger)
    else:
        ledger["state"], code = "open", 1
        reasons += ["finding %s (%s): %s" % (f["id"], f["kind"], f["text"]) for f in findings]
    return finish(run, ledger, code, reasons, n, not findings, len(findings), now_hashes, items)


def cmd_ruling(args):
    run = args.run
    ledger, err = load_ledger(run)
    if ledger is None:
        raise Usage("no ledger: %s" % (err or "records/fidelity.json missing"))
    prev, _ = _records.load_json(ledger_path(run))
    ledger["input_hashes"] = prev.get("input_hashes", {})
    known = set(f["id"] for r in ledger["rounds"] for f in r["findings"])
    if args.finding not in known:
        raise Usage("unknown finding id: %s" % args.finding)
    ledger["rulings"].append({"finding": args.finding, "by": args.by, "text": args.text,
                              "date": _records.now_iso()})
    if ledger["state"] == "continued-at-cap":
        code, reasons = 0, ["ruling recorded for the end-of-run review; the run continues with its open items"]
    elif ledger["state"] == "clean":
        code, reasons = 0, ["ruling recorded; the ledger stays clean"]
    else:
        code, reasons = 1, ["ruling recorded; next round due"]
    save(run, ledger, code, reasons)
    for r in reasons:
        print(r)
    return code


def cmd_status(args):
    ledger, err = load_ledger(args.run)
    if ledger is None:
        print("no ledger: %s" % (err or "records/fidelity.json missing"))
        return 2
    print("state: %s" % ledger["state"])
    print("rounds: %d" % len(ledger["rounds"]))
    open_ = [] if ledger["state"] == "clean" else open_findings(ledger)
    ruled = ruled_ids(ledger)
    for f in open_:
        print("open: %s (%s)%s: %s" % (f["id"], f["kind"], " ruled" if f["id"] in ruled else "", f["text"]))
    for i in ledger.get("review_items", []):
        print("for the end-of-run review: %s (%s): %s" % (i.get("finding"), i.get("kind"), i.get("text")))
    return {"clean": 0, "open": 1, "continued-at-cap": 0}.get(ledger["state"], 1)


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise Usage(message)


def build_parser():
    p = Parser(prog="fidelity.py", description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd")
    r = sub.add_parser("round", help="run one fidelity round")
    r.add_argument("--run", required=True)
    r.add_argument("--brief", required=True)
    r.add_argument("--inputs", required=True, nargs="+")
    g = sub.add_parser("ruling", help="record a person's ruling on a finding")
    g.add_argument("--run", required=True)
    g.add_argument("--finding", required=True)
    g.add_argument("--by", required=True)
    g.add_argument("--text", required=True)
    s = sub.add_parser("status", help="print the ledger state")
    s.add_argument("--run", required=True)
    for sp in (r, g, s):
        sp.error = p.error
    return p


def main(argv=None):
    try:
        args = build_parser().parse_args(argv)
        if not args.cmd:
            raise Usage("a command is required: round, ruling or status")
        if not os.path.isdir(args.run):
            raise Usage("--run is not a folder: %s" % args.run)
        return {"round": cmd_round, "ruling": cmd_ruling, "status": cmd_status}[args.cmd](args)
    except Usage as u:
        sys.stderr.write("fidelity.py: %s\n" % u)
        return 2


if __name__ == "__main__":
    sys.exit(main())
