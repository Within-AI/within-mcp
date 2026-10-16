#!/usr/bin/env python3
"""Plain-language check of the page-preparation output.

    /usr/bin/python3 skills/agent-design/checks/plain_text.py --run RUN [--out report.json]

Reads RUN/stages/page-prep/page-prep.json against RUN/stages/rules/rules.json, RUN/stages/rules/outcomes.json and
RUN/stages/architecture/agent.yaml (JSON syntax). Exit 0 pass, 1 fail, 2 usage or unreadable input. The report follows
formats/lists/check-report.schema.json; with no --out it is written to RUN/checks/plain_text.json.

Problem kinds (ported from Fast Mode's page/check/plain_check.py; patterns, number words and the lead limit copied from
its text_rules.json `plain`):
  id            rule, case, interface or step id inside text
  field-token   snake_case or dotted field name inside text
  banned        banned term from formats/lists/vocabulary.json `banned`
  retired       retired term from formats/lists/vocabulary.json `retired`
  decode        hash, file name or path, JSON or YAML fragment inside text
  typed-count   a count typed next to a counted noun; any digit or number word in the interpretive paragraph
  length        an interpretive paragraph (the lead) over 60 words
  input-invalid the output parses but fails stages/page-prep/output.schema.json (pins G27); nothing else is computed
  Scanned text: rule words (when, then, example, missing, the plain why, both contradiction sides), frames, part
  titles (parts), the lead and the guide. Zero rules is valid.
  rule-missing, example-missing, frame-missing, label-unknown   coverage against the run's stage files
"""
import json, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
AD = os.path.dirname(HERE)
CHECK_PATH = "skills/agent-design/checks/plain_text.py"
OUTPUT = os.path.join("stages", "page-prep", "page-prep.json")
SCHEMA = os.path.join(AD, "stages", "page-prep", "output.schema.json")

# copied from skills/fast-mode-build/page/check/text_rules.json `plain` and `id_regex` (dev 0bb665d)
ID_REGEXES = [
    r"\bR-[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*\b",
    r"\bI[RCHW]-\d(?:-fm-\d)?(?:\.\w+)?\b",
    r"\b(?:n|s|eg|iss|fx|bm|ag|an)-[a-z0-9]+(?:-[a-z0-9]+)*\b",
    r"\b[A-Z]{1,3}-[A-Z]?\d+\b",
    r"\b(R-[a-z]+(-[a-z]+)+|I[RCHW]-\d\.\w+)\b",
]
FIELD_TOKEN_REGEXES = [r"\b[a-z]+(?:_[a-z0-9]+)+\b", r"\b[A-Za-z0-9-]+\.[a-z_]{3,}\b(?!\s)"]
DECODE_REGEXES = {
    "uuid or hash": r"\b[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\b|\b[0-9a-f]{12,}\b",
    "file name or path": r"\b[\w-]+\.(?:csv|json|ya?ml|md|py|txt|svg|html|xlsx?|jsonl)\b|(?:^|\s)/[\w.-]+/[\w./-]+",
    "json or yaml fragment": r"[{}]|\[\s*\"|\":|^\s*[a-z_]+:\s",
}
COUNT_NOUNS = "steps?|rules?|cases?|points?|interfaces?|questions?|groups?|surfaces?|gates?"
NUMBER_WORDS = ("two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|"
                "eighteen|nineteen|twenty")
LEAD_MAX_WORDS = 60
FALLBACK = ("Without this script, an agent reads page-prep.json against the rules, outcomes and architecture files and "
            "applies the same kinds by hand, listing each problem with its place.")


def vocabulary():
    """The banned and retired term lists from U1's pinned vocabulary file."""
    with open(os.path.join(AD, "formats", "lists", "vocabulary.json"), encoding="utf-8") as f:
        v = json.load(f)
    return v.get("banned", []), v.get("retired", [])


def scan_text(text, where, add, banned, retired):
    for kind, terms in (("banned", banned), ("retired", retired)):
        for t in terms:
            if re.search(t["pattern"], text, re.I):
                add(kind, where, "%s term: %s" % (kind, t["name"]))
    for rx in ID_REGEXES:
        for m in re.finditer(rx, text):
            add("id", where, "id-shaped text %r" % m.group(0))
    for rx in FIELD_TOKEN_REGEXES:
        for m in re.finditer(rx, text):
            add("field-token", where, "field name %r" % m.group(0))
    for label, rx in DECODE_REGEXES.items():
        for m in re.finditer(rx, text, re.M):
            add("decode", where, "%s: %r" % (label, m.group(0).strip()))
    rx = r"\b(\d+|%s)\s+(?:[a-z]+\s+)?(?:%s)\b" % (NUMBER_WORDS, COUNT_NOUNS)
    for m in re.finditer(rx, text, re.I):
        add("typed-count", where, "count typed in text %r" % m.group(0))


def check_lead(text, where, add):
    """The interpretive paragraph is the lead: at most 60 words, no digit and no number word."""
    if len(text.split()) > LEAD_MAX_WORDS:
        add("length", where, "interpretive paragraph is over %d words" % LEAD_MAX_WORDS)
    for m in re.finditer(r"\d+|\b(?:%s)\b" % NUMBER_WORDS, text, re.I):
        add("typed-count", where, "number %r in the interpretive paragraph" % m.group(0))


def validate(node, schema, path, root, errs):
    """Standard-library check of the JSON Schema subset output.schema.json uses; appends (path, detail) to errs."""
    if "$ref" in schema:
        target = root
        for k in schema["$ref"].lstrip("#/").split("/"):
            target = target[k]
        validate(node, target, path, root, errs)
    t = schema.get("type")
    kinds = {"object": dict, "array": list, "string": str}
    if t in kinds and not isinstance(node, kinds[t]):
        errs.append((path, "must be of type %s" % t))
        return
    if "const" in schema and not (node == schema["const"] and type(node) is type(schema["const"])):
        errs.append((path, "must be %s" % json.dumps(schema["const"])))
    if isinstance(node, str):
        if len(node) < schema.get("minLength", 0):
            errs.append((path, "must be at least %d characters" % schema["minLength"]))
        if "pattern" in schema and not re.search(schema["pattern"], node):
            errs.append((path, "must match %s" % schema["pattern"]))
    if isinstance(node, list):
        if len(node) < schema.get("minItems", 0):
            errs.append((path, "needs at least %d item(s)" % schema["minItems"]))
        if "items" in schema:
            for i, v in enumerate(node):
                validate(v, schema["items"], "%s[%d]" % (path, i), root, errs)
    if isinstance(node, dict):
        if len(node) < schema.get("minProperties", 0):
            errs.append((path, "needs at least %d entry(ies)" % schema["minProperties"]))
        for k in schema.get("required", []):
            if k not in node:
                errs.append((path, "missing required %r" % k))
        props, extra = schema.get("properties", {}), schema.get("additionalProperties", True)
        for k, v in node.items():
            sub = "%s.%s" % (path, k)
            if k in props:
                validate(v, props[k], sub, root, errs)
            elif extra is False:
                errs.append((sub, "unexpected key %r" % k))
            elif isinstance(extra, dict):
                validate(v, extra, sub, root, errs)
    if "oneOf" in schema:
        ok = 0
        for alt in schema["oneOf"]:
            e = []
            validate(node, alt, path, root, e)
            ok += not e
        if ok != 1:
            errs.append((path, "must match exactly one allowed form (matched %d)" % ok))
    if "if" in schema:
        e = []
        validate(node, schema["if"], path, root, e)
        if not e and "then" in schema:
            validate(node, schema["then"], path, root, errs)


def reader_texts(out):
    """(where, text) for every value in a page-prep output a reader sees: rule words (with the plain why and both sides
    of a contradiction), frames, part titles, the lead and the guide. Tolerates a partial output."""
    texts = []
    rules = out.get("rules") if isinstance(out.get("rules"), dict) else {}
    for rid, e in rules.items():
        if not isinstance(e, dict):
            continue
        for k in ("when", "then", "missing", "example", "why"):
            if isinstance(e.get(k), str):
                texts.append(("rules.%s.%s" % (rid, k), e[k]))
        c = e.get("contradiction")
        if isinstance(c, dict):
            for k in ("side_a", "side_b"):
                if isinstance(c.get(k), str):
                    texts.append(("rules.%s.contradiction.%s" % (rid, k), c[k]))
    frames = out.get("frames") if isinstance(out.get("frames"), dict) else {}
    for pid, fr in frames.items():
        if not isinstance(fr, dict):
            continue
        for k in ("input", "output", "end_point"):
            if isinstance(fr.get(k), str):
                texts.append(("frames.%s.%s" % (pid, k), fr[k]))
        for k in ("surfaces_read", "future_state_steps"):
            for i, v in enumerate(fr.get(k) or []):
                if isinstance(v, str):
                    texts.append(("frames.%s.%s[%d]" % (pid, k, i), v))
    parts = out.get("parts") if isinstance(out.get("parts"), dict) else {}
    for pid, title in parts.items():
        if isinstance(title, str):
            texts.append(("parts.%s" % pid, title))
    for k in ("interpretive_paragraph", "guide"):
        if isinstance(out.get(k), str):
            texts.append((k, out[k]))
    return texts


def is_text(v):
    return isinstance(v, str) and v.strip() != ""


def load(run, rel):
    with open(os.path.join(run, rel), encoding="utf-8") as f:
        return json.load(f)


def report(result, problems, counts):
    code = {"pass": 0, "fail": 1, "error": 2}[result]
    return {"check": "plain_text", "path": CHECK_PATH, "run": OUTPUT, "exit_code": code, "result": result,
            "problems": problems, "counts": counts, "agent_fallback": FALLBACK}


def check(run_dir):
    problems, seen = [], set()

    def add(defect, where, detail):
        key = (defect, where, detail)
        if key not in seen:
            seen.add(key)
            problems.append({"defect": defect, "where": where, "detail": detail})
    try:
        out = load(run_dir, OUTPUT)
        rules = load(run_dir, os.path.join("stages", "rules", "rules.json"))["rules"]
        outcomes = load(run_dir, os.path.join("stages", "rules", "outcomes.json"))
        agent = load(run_dir, os.path.join("stages", "architecture", "agent.yaml"))
        banned, retired = vocabulary()
    except (OSError, ValueError, KeyError, TypeError) as e:
        return report("error", [{"defect": "unreadable", "where": "", "detail": str(e)}], {})
    try:
        with open(SCHEMA, encoding="utf-8") as f:
            schema = json.load(f)
    except (OSError, ValueError) as e:
        return report("error", [{"defect": "unreadable", "where": "", "detail": str(e)}], {})
    errs = []
    validate(out, schema, "$", schema, errs)
    if errs:
        return report("fail", [{"defect": "input-invalid", "where": "%s %s" % (OUTPUT, w), "detail": d} for w, d in errs],
                      {"problems": len(errs)})

    texts = []   # (where, text) a reader sees
    entries = out["rules"]
    for r in rules:
        rid = r.get("id")
        e = entries.get(rid)
        if not isinstance(e, dict):
            add("rule-missing", "rules", "rule in the rules file has no entry: %s" % rid)
            continue
        if r.get("observation_ids") and not is_text(e.get("example")):
            add("example-missing", "rules.%s.example" % rid, "rule cites observations but has no example: %s" % rid)

    frames = out["frames"]
    for p in agent.get("parts", []):
        if p.get("role") == "sub-agent" and not isinstance(frames.get(p.get("id")), dict):
            add("frame-missing", "frames", "sub-agent in the architecture has no frame: %s" % p.get("id"))

    known = {d.get("id"): set(d.get("labels", [])) for d in outcomes.get("decisions", [])}
    decisions = out["decisions"]
    for did, d in decisions.items():
        for lab in d["labels"]:
            if lab not in known.get(did, set()):
                add("label-unknown", "decisions.%s.labels" % did, "label not in outcomes.json for this decision: %s" % lab)

    lead = out["interpretive_paragraph"]
    texts += reader_texts(out)
    check_lead(lead, "interpretive_paragraph", add)

    for where, t in texts:
        scan_text(t, where, add, banned, retired)
    counts = {"rules": len(rules), "entries": len(entries), "frames": len(frames), "decisions": len(decisions),
              "problems": len(problems)}
    return report("fail" if problems else "pass", problems, counts)


def main(argv):
    usage = "usage: plain_text.py --run RUN [--out report.json]\n"
    if argv[:1] in (["--help"], ["-h"]):
        sys.stdout.write(usage)
        return 0
    opts, i = {}, 0
    while i < len(argv):
        if argv[i] in ("--run", "--out") and i + 1 < len(argv):
            opts[argv[i]] = argv[i + 1]
            i += 2
        else:
            sys.stderr.write(usage)
            return 2
    if "--run" not in opts:
        sys.stderr.write(usage)
        return 2
    run = opts["--run"]
    rep = check(run)
    out = opts.get("--out") or os.path.join(run, "checks", "plain_text.json")
    try:
        os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
        with open(out, "w", encoding="utf-8") as f:
            json.dump(rep, f, indent=1)
    except OSError as e:
        sys.stderr.write("cannot write report: %s\n" % e)
        return 2
    for p in rep["problems"]:
        print("[%s] %s: %s" % (p["defect"], p["where"], p["detail"]))
    print(rep["result"])
    return rep["exit_code"]


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
