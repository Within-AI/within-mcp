#!/usr/bin/env python3
"""Extract the input requirements of the checks that run on a stage, mechanically, from the check scripts' own
docstrings (so the prompt block cannot drift from the checks). Started as fast-mode-build/tools/extract_check_schema.py
at dev 61f6a7d; the check list now comes from hooks_checks.for_stage (Pins §10), paths through knowledge.py.

Usage:
  check_schema.py --run RUN --stage KEY          # print the prompt block
  check_schema.py --run RUN --report             # every stage: one row per listed check, extracted or not

Rule: every check for_stage lists gets one block, quoted verbatim from the docstring of the script it runs (argv[0]).
When a docstring has paragraphs that name an input shape (a heading 'THE ... INPUT' / 'SCHEMA', or the words
'machine block', 'top-level', 'Invariants asserted', 'Args:') only those are quoted; otherwise the whole docstring is.
A wrapper with no docstring of its own (`from MODULE import main` ... `main("NAME")`) is followed into MODULE: its
module docstring and the docstring of the function MODULE's CHECKS maps NAME to are quoted. A check whose script is
missing or carries no docstring gets no block and is reported; nothing is written for it by hand. On every stage but
set-map, a quoted paragraph that names the set-map stage's own file is left out (Pins §1: later prompts name only
inputs/set-map.json). Stdlib only.
"""
import argparse, ast, os, re, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import knowledge  # noqa: E402
import hooks_checks  # noqa: E402

SKILL = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STAGES = ("set-map", "current-state", "transformation-plan", "future-state")
SET_MAP_STAGE_FILE = "stages/set-map"   # named only in the set-map stage's own prompt (Pins §1)
MARKERS = re.compile(r"(THE [A-Z -]*(INPUT|SCHEMA)|machine block|top-level|Invariants asserted|Args:)", re.I)
# Requirements a check enforces in code but not in its docstring: the validator's own message strings that
# mention the field are quoted verbatim (derived from the code, never hand-written). label -> (knowledge path, needle)
CODE_MESSAGES = {"reference-use-record": ("reference_registry", "divergences[")}


def parse(path):
    try:
        with open(path, encoding="utf-8") as f:
            return ast.parse(f.read())
    except (OSError, SyntaxError, ValueError):
        return None


def docstring(path):
    tree = parse(path)
    return (ast.get_docstring(tree) or "") if tree else ""


def paragraphs(doc):
    return [p.strip() for p in re.split(r"\n\s*\n", doc) if p.strip()]


def schema_paragraphs(doc):
    """Marker paragraphs, each followed by the numbered / indented paragraphs that continue it (an invariant list)."""
    ps, out, i = paragraphs(doc), [], 0
    while i < len(ps):
        if MARKERS.search(ps[i]):
            chunk = [ps[i]]
            while i + 1 < len(ps) and re.match(r"(\d+\.|[-*] )", ps[i + 1]):
                i += 1; chunk.append(ps[i])
            out.append("\n\n".join(chunk))
        i += 1
    return out


def quoted(doc, stage=None):
    """The marker paragraphs of a docstring, or the whole docstring when it has none; on a stage other than set-map,
    paragraphs naming the set-map stage's file are left out."""
    ps = schema_paragraphs(doc) or paragraphs(doc)
    if stage != "set-map":
        ps = [p for p in ps if SET_MAP_STAGE_FILE not in p]
    return ps


def wrapper_target(path):
    """(module path, check name) for a `from MODULE import main` + `main("NAME")` wrapper, else None."""
    tree = parse(path)
    if not tree:
        return None
    mod = name = None
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom) and n.module and any(a.name == "main" for a in n.names):
            mod = n.module
        if (isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "main" and n.args
                and isinstance(n.args[0], ast.Constant) and isinstance(n.args[0].value, str)):
            name = n.args[0].value
    if not mod:
        return None
    return os.path.join(os.path.dirname(path), mod.replace(".", os.sep) + ".py"), name


def function_doc(module_path, check):
    """Docstring of the function the module's CHECKS dict maps `check` to ('' when not found)."""
    tree = parse(module_path)
    if not tree or not check:
        return ""
    fn = None
    for n in tree.body:
        if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "CHECKS" for t in n.targets) \
                and isinstance(n.value, ast.Dict):
            for k, v in zip(n.value.keys, n.value.values):
                if isinstance(k, ast.Constant) and k.value == check and isinstance(v, ast.Name):
                    fn = v.id
    for n in tree.body:
        if isinstance(n, ast.FunctionDef) and n.name == fn:
            return ast.get_docstring(n) or ""
    return ""


def code_messages(path, needle):
    """String literals in `path` that contain `needle` (the validator's own rejection messages)."""
    tree = parse(path)
    seen = []
    for n in (ast.walk(tree) if tree else []):
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and needle in n.value and n.value not in seen:
            seen.append(n.value)
    return seen


def shown(path, run):
    """A script path as the prompt names it: relative to the knowledge root or to the skill folder."""
    for base, tag in ((knowledge.root(run), ""), (SKILL, "skill:")):
        rel = os.path.relpath(path, base)
        if not rel.startswith(".."):
            return tag + rel
    return path


def block_for(label, script, run, stage=None):
    """(block text or '', report row) for one listed check."""
    rel = shown(script, run)
    if not os.path.isfile(script):
        return "", (label, rel, "MISSING", "check script not present")
    sources, doc = [], docstring(script)
    if doc:
        sources.append((rel, quoted(doc, stage)))
    else:
        target = wrapper_target(script)
        if target and os.path.isfile(target[0]):
            mdoc, fdoc = docstring(target[0]), function_doc(*target)
            parts = quoted(mdoc, stage) + quoted(fdoc, stage)
            if parts:
                sources.append((shown(target[0], run) + (f" (check `{target[1]}`)" if fdoc else ""), parts))
    if not sources:
        return "", (label, rel, "NOT-EXTRACTABLE", "no quotable docstring in the script or the module it wraps")
    text = f"#### `{label}`: `{rel}`\n\n" + "\n\n".join(
        (f"From `{src}`:\n\n" if src != rel else "") + "\n\n".join(ps) for src, ps in sources)
    n = sum(len(ps) for _, ps in sources)
    return text, (label, rel, "extracted", f"{n} paragraph(s)")


def extract(run, stage):
    """Return (block_text, report_rows) for the checks hooks_checks.for_stage lists. Row: (label, path, status, note)."""
    rows, parts, done = [], [], set()
    for label, argv, _needed in hooks_checks.for_stage(stage, run):
        key = (label, argv[0])
        if key in done:
            continue
        done.add(key)
        text, row = block_for(label, argv[0], run, stage)
        rows.append(row)
        if text:
            parts.append(text)
        if label in CODE_MESSAGES:
            src, needle = CODE_MESSAGES[label]
            p = knowledge.path(src, run)
            msgs = code_messages(p, needle)
            if msgs:
                rel = os.path.relpath(p, knowledge.root(run))
                rows.append((label + " (validator messages)", rel, "extracted-from-code", f"{len(msgs)} message(s) mentioning {needle}"))
                parts.append(f"#### Rejection messages in `{rel}` that mention `{needle}`\n\n" + "\n".join("- " + m for m in msgs))
    block = ""
    if parts:
        block = ("### Machine-readable requirements of the checks that will run on your output\n\n"
                 "Below, quoted verbatim from each check script's own docstring, is the input shape that check reads. "
                 "Your output must satisfy it exactly (key names, list vs. text, integer vs. string). "
                 "These checks run after your stage.\n\n" + "\n\n".join(parts) + "\n")
    return block, rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    ap.add_argument("--stage", default="")
    ap.add_argument("--report", action="store_true")
    a = ap.parse_args()
    run = os.path.abspath(a.run)
    if a.report:
        bad = 0
        for st in STAGES:
            _, rows = extract(run, st)
            for label, rel, status, note in rows:
                print(f"{st}\t{label}\t{status}\t{note}")
                bad += not status.startswith("extracted")
        return 1 if bad else 0
    block, rows = extract(run, a.stage)
    sys.stdout.write(block)
    for label, rel, status, note in rows:
        if not status.startswith("extracted"):
            print(f"[not extractable] {label}: {note}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
