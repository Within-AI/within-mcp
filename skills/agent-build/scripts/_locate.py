"""Agent Build: find the Agent Design output wherever it landed, and read it leniently.

Agent Design's output reaches a Build run in many shapes: the run folder copied as is, one folder
deeper (a folder copied into a folder), only its published outputs/ (with outputs/design/ and a
`bundle.design_files` map), only the shareable copy, camelCase keys, agent.yaml as real YAML, or a
person's own document instead of a design. Every script that reads the design asks this module where
it is, instead of hard-coding inputs/agent-design/... paths.

Names:
  locate_design(run, named=None) -> Design
      .exists       True when design files or a person's documents were found
      .base, .base_rel   the folder searched (inputs/agent-design, or the named path)
      .root, .root_rel   the chosen design root, or None
      .handoff, .suite, .agent, .architecture   absolute paths, or None
      .documents    absolute paths of a person's documents found beside or instead of the design
      .readings     [{"what", "reading", "why"}], one per interpretation taken
      .rel(path)    run-relative path with "/" separators
  normalise_key(key) -> snake_case key
  normalise_handoff(obj, readings) / normalise_suite(obj, readings) / normalise_agent(obj, readings)
  load_handoff(path, readings) -> (obj or None, err or None)   JSON, keys normalised
  read_agent(path, readings) -> (obj or None, err or None)     JSON, else simple YAML
  load_simple_yaml(text) -> value (raises YamlError)
  read_document(path) -> {"sections": [...], "bullets": n, "words": n, "excerpt": str}

Normalisation touches structural key names and identifier fields only (field names, `process_id` read
as `process`, `process/step` read as `process:step`); never free text, and never the keys or values
under an expected result. Stdlib only, 3.9-safe. Nothing here stops a run.
"""
import json
import os
import re
import _md_blocks

HANDOFF_NAME = "agent-build-inputs.json"
SUITE_NAME = "test-suite.json"
AGENT_NAMES = ("agent.yaml", "agent.yml", "agent.json")
DOC_EXTS = (".md", ".markdown", ".txt")

# Where each kind sits relative to a design root, in the order a lookup prefers them.
LAYOUT = {
    "handoff": ("outputs", "", "outputs/shareable", "shareable"),
    "suite": ("stages/test-suite", "outputs/design/test-suite", "design/test-suite",
              "outputs/shareable/design/test-suite", "shareable/design/test-suite", "test-suite", ""),
    "agent": ("stages/architecture", "outputs/design/architecture", "design/architecture",
              "outputs/shareable/design/architecture", "shareable/design/architecture", "architecture", ""),
}
KIND_TEXT = {"handoff": "hand-over", "suite": "test suite", "agent": "agent.yaml"}

HANDOFF_FIELDS = (
    "open_items", "access_assumptions", "stand_ins", "rules_needing_ratification",
    "critical_cases_failed_or_open", "primitive_form", "identity_runtime_opens", "evidence_strength",
    "upstream_failing_checks", "capability_needs", "stage_input_hashes", "outputs", "bundle",
    "platform_question",
)
# Hand-over lists whose entries are records with structural keys (normalised one level down).
HANDOFF_RECORD_LISTS = (
    "open_items", "access_assumptions", "stand_ins", "rules_needing_ratification",
    "critical_cases_failed_or_open", "identity_runtime_opens", "evidence_strength",
    "upstream_failing_checks", "capability_needs",
)
MAX_DEPTH = 8
MAX_JSON_BYTES = 20 * 1024 * 1024
MAX_DOC_BYTES = 1024 * 1024
MAX_DOCS = 10
MAX_CONTENT_FILES = 400


def _reading(readings, what, reading, why):
    entry = {"what": what, "reading": reading, "why": why}
    if entry not in readings:
        readings.append(entry)


# ---------- key normalisation ----------

def normalise_key(key):
    """camelCase, PascalCase, kebab-case and spaced keys to snake_case; other keys unchanged."""
    if not isinstance(key, str):
        return key
    k = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", key)
    k = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", "_", k)
    k = re.sub(r"[\s\-.]+", "_", k.strip()).lower()
    return re.sub(r"_+", "_", k)


def normalise_keys(obj, readings, where):
    """A copy of dict obj with normalised key names. An exact snake_case key wins over a variant of it;
    every mapping applied is recorded once. Values are untouched."""
    if not isinstance(obj, dict):
        return obj
    out, mapped = {}, []
    for key in obj:
        if normalise_key(key) == key:
            out[key] = obj[key]
    for key in obj:
        new = normalise_key(key)
        if new == key:
            continue
        if new in out:
            _reading(readings, "%s key %s" % (where, key), "kept %s, ignored %s" % (new, key),
                     "both spellings are present; the snake_case one is the declared name")
            continue
        out[new] = obj[key]
        mapped.append("%s -> %s" % (key, new))
    if mapped:
        _reading(readings, "%s key names" % where, "; ".join(mapped),
                 "same field names in another case or separator style")
    return out


def _step_ref(value):
    """`process/step` read as `process:step`; any other value unchanged."""
    if isinstance(value, str) and ":" not in value and value.count("/") == 1:
        left, right = value.split("/", 1)
        if left.strip() and right.strip() and " " not in value:
            return "%s:%s" % (left, right), True
    return value, False


def _process_alias(entry, readings, where):
    """Entries naming their process as process_id are read as `process` too (both kept)."""
    if isinstance(entry, dict) and "process" not in entry and isinstance(entry.get("process_id"), str):
        entry = dict(entry)
        entry["process"] = entry["process_id"]
        _reading(readings, "%s process_id" % where, "read process_id as process",
                 "the entry names its process under process_id")
    return entry


def normalise_handoff(obj, readings):
    if not isinstance(obj, dict):
        return obj
    out = normalise_keys(obj, readings, "hand-over")
    for field in HANDOFF_RECORD_LISTS:
        entries = out.get(field)
        if not isinstance(entries, list):
            continue
        fixed = []
        for e in entries:
            e = normalise_keys(e, readings, "hand-over %s" % field) if isinstance(e, dict) else e
            fixed.append(_process_alias(e, readings, "hand-over %s" % field))
        out[field] = fixed
    for field in ("primitive_form", "bundle"):
        if isinstance(out.get(field), dict):
            out[field] = normalise_keys(out[field], readings, "hand-over %s" % field)
    form = out.get("primitive_form")
    if isinstance(form, dict) and "decision_ref" in form:
        ref, changed = _step_ref(form["decision_ref"])
        if changed:
            form = dict(form)
            form["decision_ref"] = ref
            out["primitive_form"] = form
            _reading(readings, "hand-over step references", "read process/step as process:step",
                     "a step reference uses / between process and step")
    return out


def normalise_suite(obj, readings):
    """Top-level and per-case key names, case process/decision identifiers. Never the expected result's
    own keys or values."""
    if not isinstance(obj, dict):
        return obj
    out = normalise_keys(obj, readings, "test suite")
    cases = out.get("cases")
    if isinstance(cases, list):
        fixed, steps = [], 0
        for c in cases:
            if isinstance(c, dict):
                c = _process_alias(normalise_keys(c, readings, "test suite case"), readings, "test suite case")
                ref, changed = _step_ref(c.get("decision"))
                if changed:
                    c = dict(c)
                    c["decision"] = ref
                    steps += 1
            fixed.append(c)
        out["cases"] = fixed
        if steps:
            _reading(readings, "test suite step references",
                     "read process/step as process:step in %d case(s)" % steps,
                     "a case's decision uses / between process and step")
    return out


def normalise_agent(obj, readings):
    if not isinstance(obj, dict):
        return obj
    out = normalise_keys(obj, readings, "agent.yaml")
    if isinstance(out.get("header"), dict):
        header = normalise_keys(out["header"], readings, "agent.yaml header")
        for field in ("handovers", "proposed_writes"):
            if isinstance(header.get(field), list):
                header[field] = [normalise_keys(h, readings, "agent.yaml %s" % field) if isinstance(h, dict) else h
                                 for h in header[field]]
        out["header"] = header
    parts = out.get("parts")
    if isinstance(parts, list):
        fixed, steps = [], 0
        for p in parts:
            if isinstance(p, dict):
                p = normalise_keys(p, readings, "agent.yaml part")
                if isinstance(p.get("routing"), dict):
                    p["routing"] = normalise_keys(p["routing"], readings, "agent.yaml routing")
                if isinstance(p.get("owns_steps"), list):
                    owns = []
                    for s in p["owns_steps"]:
                        s, changed = _step_ref(s)
                        steps += changed
                        owns.append(s)
                    p["owns_steps"] = owns
            fixed.append(p)
        out["parts"] = fixed
        if steps:
            _reading(readings, "agent.yaml step references",
                     "read process/step as process:step for %d step(s)" % steps,
                     "a part's owned step uses / between process and step")
    return out


# ---------- reading files ----------

def _load_json_file(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.loads(f.read()), None
    except (OSError, UnicodeDecodeError) as err:
        return None, "unreadable (%s)" % type(err).__name__
    except ValueError:
        return None, "invalid JSON"


def load_handoff(path, readings):
    obj, err = _load_json_file(path)
    if err:
        return None, err
    return normalise_handoff(obj, readings), None


def load_suite(path, readings):
    obj, err = _load_json_file(path)
    if err:
        return None, err
    return normalise_suite(obj, readings), None


def read_agent(path, readings):
    """agent.yaml as JSON (how Agent Design writes it), else as simple YAML; the reading is recorded."""
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except (OSError, UnicodeDecodeError) as err:
        return None, "unreadable (%s)" % type(err).__name__
    try:
        obj = json.loads(text)
    except ValueError:
        try:
            obj = load_simple_yaml(text)
        except YamlError as err:
            return None, "neither JSON nor simple YAML (%s)" % err
        _reading(readings, "agent.yaml format", "read as YAML",
                 "the file is not JSON but parses as simple YAML")
    if not isinstance(obj, dict):
        return None, "not a mapping"
    return normalise_agent(obj, readings), None


def read_document(path):
    """A person's document summarised structurally: headed sections with their bullet counts, an excerpt."""
    try:
        if os.path.getsize(path) > MAX_DOC_BYTES:
            return {"sections": [], "bullets": 0, "words": 0, "excerpt": "", "error": "too large to read"}
        with open(path, encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError as err:
        return {"sections": [], "bullets": 0, "words": 0, "excerpt": "", "error": type(err).__name__}
    text = _md_blocks.document_text(text)
    sections, bullets, current = [], 0, None
    body = []
    for line in text.splitlines():
        s = line.strip()
        m = re.match(r"^(#{1,6})\s+(.*)$", s)
        if m:
            current = {"heading": m.group(2).strip(), "level": len(m.group(1)), "bullets": 0, "first": None}
            sections.append(current)
            continue
        if re.match(r"^([-*+]|\d+[.)])\s+\S", s):
            bullets += 1
            item = re.sub(r"^([-*+]|\d+[.)])\s+", "", s)
            if current is None:
                current = {"heading": None, "level": 0, "bullets": 0, "first": None}
                sections.append(current)
            current["bullets"] += 1
            if current["first"] is None:
                current["first"] = item[:160]
        if s:
            body.append(s)
    excerpt = " ".join(body)
    return {"sections": sections, "bullets": bullets, "words": len(text.split()),
            "excerpt": excerpt[:600] + ("..." if len(excerpt) > 600 else "")}


# ---------- locating ----------

class Design(object):
    def __init__(self, run, base):
        self.run = run
        self.base = base
        self.exists = os.path.isdir(base)
        self.root = None
        self.handoff = self.suite = self.agent = self.architecture = None
        self.documents = []
        self.readings = []

    def rel(self, path):
        if path is None:
            return None
        return os.path.relpath(path, self.run).replace(os.sep, "/")

    @property
    def base_rel(self):
        return self.rel(self.base)

    @property
    def root_rel(self):
        return self.rel(self.root)

    def as_record(self):
        return {"root": self.root_rel, "handoff": self.rel(self.handoff), "suite": self.rel(self.suite),
                "agent": self.rel(self.agent), "documents": [self.rel(d) for d in self.documents],
                "readings": list(self.readings)}


def _walk(base):
    """(dirpath, filenames) under base, depth-limited, no hidden folders, no archived versions."""
    base_depth = base.rstrip(os.sep).count(os.sep)
    for dirpath, dirnames, filenames in os.walk(base):
        depth = dirpath.rstrip(os.sep).count(os.sep) - base_depth
        keep = []
        for d in sorted(dirnames):
            if d.startswith(".") or d == "node_modules":
                continue
            if d == "versions" and os.path.basename(dirpath) == "outputs":
                continue  # archived earlier hand-overs, never the current one
            if os.path.islink(os.path.join(dirpath, d)):
                continue
            keep.append(d)
        dirnames[:] = keep if depth < MAX_DEPTH else []
        yield dirpath, sorted(filenames)


def _root_for(base, path, kind):
    """(design root, layout rank) for a file of this kind: strip the layout folders it sits in (the
    longest matching layout wins; the rank is the lookup preference of that layout)."""
    folder = os.path.dirname(path)
    rel = os.path.relpath(folder, base).replace(os.sep, "/")
    rel = "" if rel == "." else rel
    layouts = LAYOUT[kind]
    for suffix in sorted((s for s in layouts if s), key=len, reverse=True):
        if rel == suffix:
            return base, layouts.index(suffix)
        if rel.endswith("/" + suffix):
            return os.path.join(base, rel[:-len(suffix) - 1]), layouts.index(suffix)
    return folder, layouts.index("") if "" in layouts else len(layouts)


def _json_kind(path):
    """Kind of a JSON file judged by its content, for files not named the usual way."""
    try:
        if os.path.getsize(path) > MAX_JSON_BYTES:
            return None
    except OSError:
        return None
    obj, err = _load_json_file(path)
    if err or not isinstance(obj, dict):
        return None
    keys = set(normalise_key(k) for k in obj)
    if len(keys & set(HANDOFF_FIELDS)) >= 4:
        return "handoff"
    cases = obj.get("cases")
    if isinstance(cases, list) and cases and all(isinstance(c, dict) for c in cases) \
            and any("expected" in c for c in cases) and all("id" in c or "expected" in c for c in cases):
        return "suite"
    if isinstance(obj.get("parts"), list) and isinstance(obj.get("header"), dict):
        return "agent"
    return None


def _scan(base, allowed=None):
    """{kind: [path]} by file name, then (for kinds not found by name) by content; plus text documents."""
    found = {"handoff": [], "suite": [], "agent": []}
    jsons, docs = [], []
    for dirpath, filenames in _walk(base):
        for name in filenames:
            full = os.path.join(dirpath, name)
            if os.path.islink(full) or (allowed is not None and not allowed(full)):
                continue
            low = name.lower()
            if low == HANDOFF_NAME:
                found["handoff"].append(full)
            elif low == SUITE_NAME:
                found["suite"].append(full)
            elif low in AGENT_NAMES:
                found["agent"].append(full)
            elif low.endswith(".json"):
                jsons.append(full)
            elif low.endswith(DOC_EXTS):
                docs.append(full)
    # Content candidates are classified for every JSON file, whatever was found by name elsewhere: a
    # stale named file in one folder must not hide the current design's renamed file in another.
    by_content = {"handoff": [], "suite": [], "agent": []}
    for path in jsons[:MAX_CONTENT_FILES]:
        kind = _json_kind(path)
        if kind:
            by_content[kind].append(path)
    return found, by_content, docs


def _within(path, folder):
    """True when path, symbolic links resolved, is folder or inside it (folder resolved too)."""
    real, top = os.path.realpath(path), os.path.realpath(folder)
    try:
        return os.path.commonpath([real, top]) == top
    except ValueError:
        return False


def contained(run, path, input_folders=()):
    """True when path resolves (symbolic links followed) inside the run folder or a given input folder.
    Consumers use it for any path they derive from the design themselves (e.g. bundle entries)."""
    tops = [run] + [f for f in input_folders or () if f]
    return path is not None and any(_within(path, t) for t in tops)


def bundle_prefix(design, bundle):
    """The portion of a run-relative manifest path removed when its bundle was copied.

    The manifest's own shareable entry is the anchor, not a filename search. Try the
    original layout first, then its containing folders, and require the candidate to
    be this handoff (or its byte-identical published copy). One anchor applies to the
    whole bundle; missing files cannot independently switch layouts.
    """
    if not design.root or not design.handoff:
        return ""
    anchors = [p.replace("\\", "/") for p in bundle.get("shareable") or []
               if isinstance(p, str) and os.path.basename(p.replace("\\", "/")) == HANDOFF_NAME
               and not os.path.isabs(p) and ".." not in p.replace("\\", "/").split("/")]
    try:
        if os.path.getsize(design.handoff) > MAX_JSON_BYTES:
            return ""
        with open(design.handoff, "rb") as f:
            evidence = f.read()
        for anchor in anchors:
            parts = anchor.split("/")
            for count in range(len(parts)):
                target = os.path.join(design.root, *parts[count:])
                if not _within(target, design.root) or not contained(design.run, target):
                    continue
                if not os.path.isfile(target) or os.path.getsize(target) > MAX_JSON_BYTES:
                    continue
                with open(target, "rb") as f:
                    same = f.read() == evidence
                if same:
                    prefix = "/".join(parts[:count])
                    if prefix:
                        _reading(design.readings, "bundle paths", "read relative to the copied hand-over",
                                 "the manifest's own entry anchors the copied layout; removed %s/" % prefix)
                    return prefix + "/" if prefix else ""
    except OSError:
        pass
    return ""


def bundle_file(design, path, prefix=""):
    """Resolve one manifest entry under the selected design, never outside it or the run."""
    if not design.root or not isinstance(path, str):
        return None
    path = path.replace("\\", "/")
    if os.path.isabs(path) or ".." in path.split("/"):
        return None
    if prefix:
        if not path.startswith(prefix):
            return None  # an internal trace outside the copied portable folder is not present
        path = path[len(prefix):]
    target = os.path.normpath(os.path.join(design.root, path))
    if not _within(target, design.root) or not contained(design.run, target):
        return None
    return target


def _pointer_targets(design, handoff_path, readings, allowed, search):
    """{kind: path} from the hand-over's bundle.design_files, paths relative to the hand-over's folder."""
    obj, err = load_handoff(handoff_path, [])
    if err or not isinstance(obj, dict) or not isinstance(obj.get("bundle"), dict):
        return {}
    files = obj["bundle"].get("design_files")
    if isinstance(files, dict):
        values = []
        for v in files.values():
            values.extend(v if isinstance(v, list) else [v])
    elif isinstance(files, list):
        values = files
    else:
        return {}
    here = os.path.dirname(handoff_path)
    out = {}
    for v in values:
        if not isinstance(v, str) or not v.strip():
            continue
        p = v.replace("\\", "/")
        if os.path.isabs(p):
            continue
        target = os.path.normpath(os.path.join(here, p))
        if not os.path.exists(target) and design.root:
            alt = os.path.normpath(os.path.join(design.root, p))
            if os.path.exists(alt):
                _reading(readings, "hand-over design_files %s" % v, "resolved against the design root",
                         "the path does not exist beside the hand-over but does under the design root")
                target = alt
        if not os.path.exists(target) or not allowed(target) or not _within(target, search):
            if os.path.lexists(target):
                _reading(readings, "hand-over design_files %s" % v, "not read",
                         "it resolves outside the design folder")
            continue
        candidates = []
        if os.path.isdir(target):
            for dirpath, filenames in _walk(target):
                candidates.extend(os.path.join(dirpath, n) for n in filenames)
        else:
            candidates = [target]
        for c in candidates:
            if os.path.islink(c) or not allowed(c) or not _within(c, search):
                continue
            low = os.path.basename(c).lower()
            kind = "suite" if low == SUITE_NAME else "agent" if low in AGENT_NAMES else \
                (_json_kind(c) if low.endswith(".json") else None)
            if kind in ("suite", "agent") and kind not in out:
                out[kind] = c
    return out


def locate_design(run, named=None, input_folders=()):
    """Find the Agent Design output for run R: under inputs/agent-design at any depth, or under a named
    path, or (when inputs/agent-design has no usable design) anywhere under inputs/. Never raises on content.

    Containment: every folder searched and every path returned resolves (symbolic links followed) inside
    the run folder, or inside a folder explicitly given in input_folders; anything else is not read and
    the refusal is recorded."""
    run = os.path.abspath(run)
    tops_allowed = [os.path.realpath(run)] + [os.path.realpath(f) for f in input_folders or () if f]

    def allowed(path):
        return any(_within(path, top) for top in tops_allowed)

    if named:
        base = named if os.path.isabs(named) else os.path.join(run, named)
    else:
        base = os.path.join(run, "inputs", "agent-design")
    d = Design(run, os.path.abspath(base))
    readings = d.readings
    if d.exists and not allowed(d.base):
        _reading(readings, "design location", "%s not read" % (named or d.base_rel),
                 "it resolves outside the run folder and was not given as an input folder")
        d.exists = False
        return d
    search = d.base
    if not d.exists and not named:
        inputs = os.path.join(run, "inputs")
        if not os.path.isdir(inputs) or not allowed(inputs):
            return d
        search = inputs
    elif not d.exists:
        return d

    found, by_content, docs = _scan(search, allowed)
    if search == d.base and not named and not any(found.values()) and not any(by_content.values()) and not docs:
        inputs = os.path.join(run, "inputs")
        if os.path.isdir(inputs) and allowed(inputs):
            search = inputs
            found, by_content, docs = _scan(search, allowed)
    d.exists = False
    if search != d.base:
        # Outside the usual folder, only design files count, never unrelated notes.
        docs = []
        if not any(found.values()) and not any(by_content.values()):
            return d

    # Group the files by the design root each implies; a file found by its content ranks after any file
    # found by its usual name in the same root.
    roots = {}
    for source, table in ((0, found), (1, by_content)):
        for kind, paths in table.items():
            for p in paths:
                root, rank = _root_for(search, p, kind)
                roots.setdefault(root, {}).setdefault(kind, []).append((source, rank, p.count(os.sep), p))
    if not roots:
        d.documents = [p for p in docs if allowed(p)][:MAX_DOCS]
        d.exists = bool(d.documents)
        if docs:
            _reading(readings, "design root", "none found; %d document(s) read as design evidence" % len(d.documents),
                     "no hand-over, test suite or agent.yaml under %s" % d.base_rel)
        return d

    # Drop roots nested inside another root (copies inside a design run, e.g. review snapshots).
    tops = [r for r in roots if not any(o != r and _within(r, o) and len(roots[o]) >= len(roots[r])
                                       for o in roots)]

    def completeness(r):
        kinds = set(roots[r])
        return (len(kinds), "handoff" in kinds, -r.count(os.sep))

    tops.sort(key=lambda r: (completeness(r), r), reverse=True)
    d.root = tops[0]
    if search != d.base:
        _reading(readings, "design location", "found the design at %s" % d.root_rel,
                 "inputs/agent-design has no usable design; the design was found under inputs/")
    if len(tops) > 1:
        other = tops[1]
        _reading(readings, "design root", "chose %s over %s" % (d.root_rel, d.rel(other)),
                 "it holds %d of the three design files (%s) against %d" % (
                     len(roots[d.root]), ", ".join(KIND_TEXT[k] for k in sorted(roots[d.root])),
                     len(roots[other])))

    chosen = roots[d.root]
    picks = {}
    for kind in ("handoff", "suite", "agent"):
        entries = sorted(chosen.get(kind, []))
        if entries:
            picks[kind] = entries[0][3]
            if entries[0][0] == 1:
                _reading(readings, "%s file" % KIND_TEXT[kind],
                         "took %s as the %s" % (d.rel(picks[kind]), KIND_TEXT[kind]),
                         "no file in the design has the usual name; this one has the %s's structure" % KIND_TEXT[kind])
        else:
            # Something sits at the usual place but is not a readable file (e.g. a folder): name it, so the
            # reader reports it as unreadable rather than missing.
            usual = os.path.join(d.root, LAYOUT[kind][0], HANDOFF_NAME if kind == "handoff" else
                                 SUITE_NAME if kind == "suite" else AGENT_NAMES[0])
            if os.path.lexists(usual) and allowed(usual):
                picks[kind] = usual
    d.handoff = picks.get("handoff")
    pointed = _pointer_targets(d, d.handoff, readings, allowed, search) if d.handoff else {}
    for kind in ("suite", "agent"):
        if kind in pointed:
            if picks.get(kind) != pointed[kind]:
                _reading(readings, KIND_TEXT[kind], "read %s" % d.rel(pointed[kind]),
                         "the hand-over's design_files points to it")
            picks[kind] = pointed[kind]
    for kind in ("handoff", "suite", "agent"):  # last guard: nothing returned resolves outside
        if picks.get(kind) and not allowed(picks[kind]):
            del picks[kind]
    d.handoff, d.suite, d.agent = picks.get("handoff"), picks.get("suite"), picks.get("agent")
    d.exists = bool(d.handoff or d.suite or d.agent)
    d.architecture = os.path.dirname(d.agent) if d.agent else None

    usual = (d.root == d.base and d.handoff and d.rel(d.handoff) == "%s/outputs/%s" % (d.base_rel, HANDOFF_NAME))
    have = [KIND_TEXT[k] for k in ("handoff", "suite", "agent") if picks.get(k)]
    lacking = [KIND_TEXT[k] for k in ("handoff", "suite", "agent") if not picks.get(k)]
    why = "holds the %s" % ", ".join(have)
    if lacking:
        why += "; no %s found" % ", ".join(lacking)
    if not usual or lacking:
        for kind in ("handoff", "suite", "agent"):
            if picks.get(kind) and kind not in pointed:
                why += "; %s at %s" % (KIND_TEXT[kind], d.rel(picks[kind]))
    _reading(readings, "design root", d.root_rel, why)

    # A person's documents: text documents outside the design root, plus the root's own top-level ones
    # when the root is not an Agent Design run folder (which keeps its own notes there).
    run_folder = os.path.exists(os.path.join(d.root, "state.json"))
    mine = []
    for p in docs:
        if not _within(p, d.root):
            mine.append(p)
        elif os.path.dirname(p) == d.root and not run_folder:
            mine.append(p)
    d.documents = [p for p in mine if allowed(p)][:MAX_DOCS]
    if d.documents:
        _reading(readings, "a person's document", "%d document(s) read as design evidence" % len(d.documents),
                 "text documents beside the design are evidence to read with the person")
    return d


# ---------- simple YAML ----------

class YamlError(ValueError):
    pass


_INT = re.compile(r"^[-+]?(0|[1-9][0-9_]*)$")
_FLOAT = re.compile(r"^[-+]?(\d[\d_]*)?\.\d+([eE][-+]?\d+)?$|^[-+]?\d[\d_]*[eE][-+]?\d+$")


def _plain(s):
    if s in ("~", "null", "Null", "NULL", ""):
        return None
    if s in ("true", "True", "TRUE"):
        return True
    if s in ("false", "False", "FALSE"):
        return False
    if _INT.match(s):
        return int(s.replace("_", ""))
    if _FLOAT.match(s):
        return float(s.replace("_", ""))
    return s


def _strip_comment(s):
    """Drop an inline ` #` comment outside quotes."""
    quote = None
    for i, ch in enumerate(s):
        if quote:
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
        elif ch == "#" and (i == 0 or s[i - 1] in " \t"):
            return s[:i].rstrip()
    return s


def _quoted(s):
    if s[0] == '"':
        try:
            return json.loads(s)
        except ValueError:
            raise YamlError("bad double-quoted string")
    if len(s) < 2 or s[-1] != "'":
        raise YamlError("unterminated single-quoted string")
    return s[1:-1].replace("''", "'")


def _flow(s, i):
    """Parse a flow value starting at s[i]; return (value, next index)."""
    while i < len(s) and s[i] == " ":
        i += 1
    if i >= len(s):
        raise YamlError("unterminated flow value")
    ch = s[i]
    if ch in "[{":
        close = "]" if ch == "[" else "}"
        out = [] if ch == "[" else {}
        i += 1
        while True:
            while i < len(s) and s[i] in " ,":
                i += 1
            if i >= len(s):
                raise YamlError("unterminated flow collection")
            if s[i] == close:
                return out, i + 1
            start = i
            if ch == "[":
                v, i = _flow(s, i)
                while i < len(s) and s[i] == " ":
                    i += 1
                if i < len(s) and s[i] == ":" and not isinstance(v, (list, dict)):
                    pair, i = _flow(s, i + 1)  # `[kind: model]`: a single-pair mapping in a sequence
                    v = {v: pair}
                out.append(v)
            else:
                k, i = _flow_scalar(s, i, key=True)
                while i < len(s) and s[i] == " ":
                    i += 1
                if i < len(s) and s[i] == ":":
                    v, i = _flow(s, i + 1)
                else:
                    v = None
                out[k] = v
            while i < len(s) and s[i] == " ":
                i += 1
            if i <= start or (i < len(s) and s[i] not in ("," + close)):
                raise YamlError("unexpected character in a flow collection")
    return _flow_scalar(s, i)


def _flow_scalar(s, i, key=False):
    if s[i] in "\"'":
        q = s[i]
        j = i + 1
        while j < len(s):
            if s[j] == q:
                if q == "'" and j + 1 < len(s) and s[j + 1] == "'":
                    j += 2
                    continue
                if q == '"' and s[j - 1] == "\\":
                    j += 1
                    continue
                break
            j += 1
        return _quoted(s[i:j + 1]), j + 1
    stops = ",]}:" if key else ",]}"
    j = i
    while j < len(s) and s[j] not in stops:
        if not key and s[j] == ":" and (j + 1 == len(s) or s[j + 1] in " ,]}"):
            break
        j += 1
    return _plain(s[i:j].strip()), j


def _scalar(s):
    s = s.strip()
    if not s:
        return None
    if s[0] in "\"'":
        return _quoted(s)
    if s[0] in "[{":
        v, i = _flow(s, 0)
        if s[i:].strip():
            raise YamlError("text after a flow value")
        return v
    return _plain(_strip_comment(s))


def _split_key(content):
    """(key, rest) for a `key: value` line, else None."""
    if content.startswith(("- ", "[", "{")) or content == "-":
        return None
    if content[0] in "\"'":
        q = content[0]
        j = content.find(q, 1)
        while q == "'" and j != -1 and j + 1 < len(content) and content[j + 1] == "'":
            j = content.find(q, j + 2)
        if j == -1:
            return None
        key = _quoted(content[:j + 1])
        rest = content[j + 1:].lstrip()
        if not rest.startswith(":"):
            return None
        return key, _strip_comment(rest[1:].strip())
    m = re.match(r"^([^#][^:]*?)\s*:(\s+|$)(.*)$", content)
    if not m:
        return None
    return m.group(1).strip(), _strip_comment(m.group(3).strip())


class _Yaml(object):
    def __init__(self, text):
        self.lines = text.splitlines()  # original lines: block scalars keep their trailing spaces
        self.i = 0
        self.seen = set()

    def skip(self):
        while self.i < len(self.lines):
            line = self.lines[self.i]
            s = line.strip()
            if not s or s.startswith("#") or s in ("---", "..."):
                self.i += 1
                continue
            lead = line[:len(line) - len(line.lstrip())]
            if "\t" in lead:
                raise YamlError("tab indentation at line %d" % (self.i + 1))
            return True
        return False

    def progress(self):
        """Every loop turn must consume input; a turn that starts where the last one did is an error."""
        key = (self.i, self.lines[self.i])
        if key in self.seen:
            raise YamlError("no progress at line %d" % (self.i + 1))
        self.seen.add(key)

    def indent(self):
        line = self.lines[self.i]
        return len(line) - len(line.lstrip(" "))

    def is_seq(self):
        s = self.lines[self.i].strip()
        return s == "-" or s.startswith("- ")

    def parse(self):
        if not self.skip():
            return None
        value = self.block(self.indent())
        if self.skip():
            raise YamlError("unexpected indentation at line %d" % (self.i + 1))
        return value

    def block(self, ind):
        content = self.lines[self.i].strip()
        if self.is_seq():
            return self.seq(ind)
        if _split_key(content) is not None:
            return self.mapping(ind)
        self.i += 1
        return self.continued(_scalar(content), content, ind - 1)

    def continued(self, value, raw, ind):
        """Multi-line plain scalar: following lines indented deeper than ind join with spaces."""
        if not isinstance(value, str) or raw[:1] in "\"'[{":
            return value
        parts = [value]
        while self.skip() and self.indent() > ind and not self.is_seq() \
                and _split_key(self.lines[self.i].strip()) is None:
            parts.append(_strip_comment(self.lines[self.i].strip()))
            self.i += 1
        return " ".join(parts)

    def seq(self, ind):
        out = []
        while self.skip() and self.indent() == ind and self.is_seq():
            self.progress()
            content = self.lines[self.i].strip()
            rest = content[1:].lstrip()
            if not rest:
                self.i += 1
                out.append(self.block(self.indent()) if self.skip() and self.indent() > ind else None)
                continue
            col = ind + len(content) - len(rest)
            self.lines[self.i] = " " * col + rest
            out.append(self.block(col))
        return out

    def mapping(self, ind):
        out = {}
        while self.skip() and self.indent() == ind:
            self.progress()
            content = self.lines[self.i].strip()
            kv = _split_key(content)
            if kv is None:
                raise YamlError("expected a key at line %d" % (self.i + 1))
            key, rest = kv
            self.i += 1
            if rest == "":
                if self.skip() and (self.indent() > ind or (self.indent() == ind and self.is_seq())):
                    out[key] = self.block(self.indent())
                else:
                    out[key] = None
            elif re.match(r"^[|>][-+]?$", rest):
                out[key] = self.block_scalar(rest, ind)
            else:
                out[key] = self.continued(_scalar(rest), rest, ind)
        if self.skip() and self.indent() > ind:
            raise YamlError("unexpected indentation at line %d" % (self.i + 1))
        return out

    def block_scalar(self, indicator, ind):
        raw, block_ind = [], None
        while self.i < len(self.lines):
            line = self.lines[self.i]
            if line.strip() == "":
                raw.append("")
                self.i += 1
                continue
            cur = len(line) - len(line.lstrip(" "))
            if cur <= ind:
                break
            if block_ind is None:
                block_ind = cur
            raw.append(line[min(block_ind, cur):])
            self.i += 1
        while raw and raw[-1] == "" and not indicator.endswith("+"):
            raw.pop()
        text = "\n".join(raw) if indicator[0] == "|" else _fold(raw)
        return text if indicator.endswith("-") else text + "\n"


def _fold(lines):
    """Folded (`>`) block scalar per YAML: a break between two ordinary lines becomes a space, blank lines
    become line breaks, and breaks around more-indented lines are kept as they are."""
    out, prev, blanks = "", None, 0
    for line in lines:
        if not line.strip():
            blanks += 1
            continue
        more = line[:1] in (" ", "\t")
        if prev is None:
            out += "\n" * blanks
        elif prev == "normal" and not more:
            out += " " if blanks == 0 else "\n" * blanks
        else:
            out += "\n" * (blanks + 1)
        out += line
        prev, blanks = ("more" if more else "normal"), 0
    return out + "\n" * blanks


def load_simple_yaml(text):
    """Parse block-style YAML (mappings, sequences, scalars, flow collections, block scalars)."""
    return _Yaml(text).parse()
