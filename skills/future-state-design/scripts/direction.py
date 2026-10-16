#!/usr/bin/env python3
"""Turn an Agent Recommendations output into the run's design direction (inputs/design-direction.json), and render it for the brief.

Three sources, best first:
  1. a refined report (report-refined-<id>.json, Stage 2): the chosen solution's design brief. A report is refined when it says
     so (stage, stage2_round) or when any solution carries verdict.design_brief, whatever its stage marker. The solution is
     --solution (case, spaces and dashes ignored), else the report's Our recommendation, else the only or first-ranked
     solution, the choice recorded and named in the person's plain line. A verdict other than ready to design is recorded
     (readings + What we still need to know) and the run continues.
  2. a Stage 1 report (report.json): the chosen solution's card (what, proposed future state, what the agent does, what people keep,
     assumptions, ignore-if), chosen the same way. Weaker: nobody has confirmed it.
  3. the pasted design prompt (text the Stage 2 report's "Copy design prompt" button gives), or a person's own brief. When its
     first line is the pinned line naming the refined report's path and solution, that report is looked up (by name, then
     by content beside the prompt) and read as source 1. Otherwise its labelled lines (any case or indentation) become items,
     and when anything else is written in it the whole document is also kept verbatim as one person_brief item.
A set map (how the processes link to each other and to the work around them) travels with sources 1 and 2: the brief's scoped
copy (design_brief.set_map), else the report's whole current_state.set_map. It is kept verbatim under "set_map", and
"set_map_scope" says which of the two it is.
Before the direction is written, direction_checks.check(direction, snapshot) runs (see intake). No finding stops the run: a
"corrected" one is applied and recorded (a scoped map with no whole map becomes set_map_evidence and the set-map stage draws
from the snapshot; a dropped handoff is restored from the whole map), a "contradictory" one goes to what_we_still_need_to_know,
an "open-point" one goes to open_points. Every reading taken is recorded in "readings" as {what, reading, why}. Only a source
that cannot be read at all stops, with the true reason.
Each becomes a list of items with ids DD-1, DD-2, ...: the stages report how they carried each one. Stdlib only.
"""
import hashlib, json, os, re, shutil, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import direction_checks  # noqa: E402
import md_blocks  # noqa: E402  fenced blocks and comments never supply design brief lines

KIND_TEXT = {"objective": "Objective", "process": "Process in scope", "future_state": "Proposed future state", "read": "Reads",
             "write": "Writes", "important_error": "Important error", "must_not": "Must not", "still_need_to_know": "What we still need to know",
             "agent_does": "What the agent does", "people_keep": "What people keep", "assumption": "Assumption", "ignore_if": "Ignore if",
             "guardrail": "Guardrail", "person_brief": "The person's own brief (verbatim)",
             "limits": "Limits and authority (as written)"}


SCOPED, WHOLE = "scoped to the design brief", "whole set (not scoped to this solution)"


def _sha(p):
    return hashlib.sha256(open(p, "rb").read()).hexdigest()


def _items(pairs):
    """Pairs (kind, text) become DD-n items; an optional third element is the client source id (0503 R-limits), kept as `source`."""
    out = []
    for p in pairs:
        k, t = p[0], p[1]
        if not (t and str(t).strip()):
            continue
        i = {"id": "DD-%d" % (len(out) + 1), "kind": k, "text": t}
        if len(p) > 2 and isinstance(p[2], str) and p[2].strip():
            i["source"] = p[2].strip()
        out.append(i)
    return out


def _src(x):
    """The optional client source id on a write or must-not item (a `source` string), else None."""
    return x["source"] if isinstance(x, dict) and isinstance(x.get("source"), str) and x["source"].strip() else None


def item_text(i):
    """An item's text as the stages see it: a client-stated item ends with ` (client-stated: <source>)`."""
    return "%s (client-stated: %s)" % (i["text"], i["source"]) if i.get("source") else i["text"]


def _reading(what, reading, why):
    """One recorded reading (0495 shared convention): what was read, the reading taken, and why, in one line each."""
    return {"what": what, "reading": reading, "why": why}


def _pid(p, readings=None):
    """A process entry's id: process_id, else the obvious rename id (recorded), else a bare string entry."""
    if isinstance(p, str):
        return p
    if not isinstance(p, dict):
        return None
    if p.get("process_id"):
        return p["process_id"]
    for k in ("id", "processId", "process"):
        if isinstance(p.get(k), str) and p[k]:
            if readings is not None:
                readings.append(_reading("process key", "read %r as the process id" % k,
                                         "the entry has no process_id; %r is the only id-shaped key" % k))
            return p[k]
    return None


def _txt(x, *keys):
    """A field read leniently: a string as is, a dict by the first present key, anything else as compact JSON."""
    if isinstance(x, str):
        return x
    if isinstance(x, dict):
        return next((str(x[k]) for k in keys if x.get(k) not in (None, "")), json.dumps(x, sort_keys=True))
    return "" if x is None else str(x)


def _brief_pairs(b, readings=None):
    la = b.get("limits_and_authority") or {}
    loose = []
    if not isinstance(la, dict):
        # Not the usual reads/writes/errors/must-not object: kept as written, never dropped.
        loose = [("limits", _txt(x, "text", "detail")) for x in (la if isinstance(la, list) else [la])]
        if readings is not None:
            readings.append(_reading("limits and authority", "kept as written in %d item(s)" % len(loose),
                                     "it is a %s, not the usual object of reads, writes, errors and must-nots"
                                     % ("list" if isinstance(la, list) else "single value")))
        la = {}
    pairs = [("objective", _txt(b.get("objective"), "description", "text"))]
    rd = []
    for p in b.get("processes") or []:
        pid = _pid(p, rd)
        role = p.get("role", "") if isinstance(p, dict) else ""
        pairs.append(("process", "%s: %s%s" % (pid, role, " (lead)" if isinstance(p, dict) and p.get("lead") else "")))
    if b.get("how_processes_connect"):
        pairs.append(("process", "How the processes connect: " + _txt(b["how_processes_connect"])))
    if rd and readings is not None:
        readings.append(rd[0])   # one line for the rename, however many entries use it
    pairs.append(("future_state", _txt(b.get("future_state"), "description", "text")))
    pairs += [("read", "%s: %s" % (r.get("system"), r.get("what")) if isinstance(r, dict) else _txt(r)) for r in la.get("reads") or []]
    pairs += [("write", "%s: %s (who writes: %s)" % (w.get("system"), w.get("what"), w.get("who")) if isinstance(w, dict) else _txt(w),
               _src(w)) for w in la.get("writes") or []]
    pairs += [("important_error", _txt(e, "text")) for e in la.get("important_errors") or []]
    pairs += [("must_not", _txt(m, "text"), _src(m)) for m in la.get("must_not") or []]
    pairs += loose
    pairs += [("still_need_to_know", _txt(x, "text", "detail")) for x in b.get("still_need_to_know") or []]
    return pairs


def _norm_id(x):
    """An id compared ignoring case, spaces, dashes and underscores."""
    return re.sub(r"[\s_\-]+", "", str(x or "")).lower()


def _match_id(want, ids, readings, what="solution id"):
    """The id in IDS that WANT names: exact, else the one id equal ignoring case, spaces and dashes (recorded)."""
    if want in ids:
        return want
    hits = [k for k in ids if _norm_id(k) == _norm_id(want)]
    if len(hits) == 1:
        readings.append(_reading(what, "read %r as %s" % (want, hits[0]), "they match ignoring case, spaces and dashes"))
        return hits[0]
    return None


def _ranked(d, sols):
    """Solution ids best-ranked first: problems by their numeric rank (unranked ones after, in listed order), solutions in listed order."""
    probs = [p for p in d.get("problems") or [] if isinstance(p, dict)]
    def key(ip):
        r = ip[1].get("rank")
        return (0, r, ip[0]) if isinstance(r, (int, float)) and not isinstance(r, bool) else (1, 0, ip[0])
    out = []
    for _, p in sorted(enumerate(probs), key=key):
        out += [s["id"] for s in p.get("solutions") or [] if isinstance(s, dict) and s.get("id") in sols and s["id"] not in out]
    return out


def _has_brief(s):
    return isinstance((s.get("verdict") or {}).get("design_brief") if isinstance(s.get("verdict"), dict) else None, dict)


def _choose(d, sols, solution, refined, readings, still, lenient=False):
    """The solution to design for. Named one (lenient id match) -> our_recommendation -> the only candidate -> the first-ranked
    candidate (candidates: solutions with a design brief in a refined report, else all). Every choice not named is recorded."""
    rec = (d.get("our_recommendation") or {}).get("solution_id") if isinstance(d.get("our_recommendation"), dict) else None
    if solution:
        sid = _match_id(solution, list(sols), readings)
        if sid:
            return sid, "named"
        if not lenient:
            raise Unusable("has no solution %r (solutions: %s)" % (solution, ", ".join(sols)))
        readings.append(_reading("solution id", "no solution named %r; chose another" % solution,
                                 "the report's solutions are %s and no other source gave a direction" % ", ".join(sols)))
    if rec:
        sid = _match_id(rec, list(sols), readings)
        if sid:
            readings.append(_reading("solution", "took %s, the report's own recommendation" % sid,
                                     "no solution was named" if not solution else "the named solution is not in the report"))
            return sid, "recommended"
    cands = [k for k in _ranked(d, sols) if not refined or _has_brief(sols[k][1])] or _ranked(d, sols)
    sid = cands[0]
    if len(cands) == 1:
        readings.append(_reading("solution", "took %s, the only solution%s" % (sid, " with a design brief" if refined else ""),
                                 "no solution was named"))
    else:
        readings.append(_reading("solution", "took %s, the first-ranked of %d (others: %s)" % (sid, len(cands), ", ".join(cands[1:])),
                                 "no solution was named and the report recommends none"))
        still.append({"evidence_state": "needs_more_information", "process_id": None,
                      "detail": "the recommendation holds %d solutions and names none; the design follows %s (\"%s\"), the first-ranked. "
                                "Say if another one should be designed instead." % (len(cands), sid, sols[sid][1].get("name") or sid)})
    return sid, ("only" if len(cands) == 1 else "first-ranked")


class Unusable(Exception):
    """A report that parses but gives no direction: no solution at all, or a refined solution with no design brief. In the
    source chain it counts as not found; its reason reaches the person's plain line."""


def from_report(path, solution=None, allow_not_ready=False, doc=None, lenient=False, card_fallback=False):
    """path is the report file; doc, when given, is the report itself (the evidence file's `report`) and path names that file.
    A report is read as refined when it says so (stage, stage2_round) or when any solution carries verdict.design_brief.
    The solution: --solution (case, spaces and dashes ignored), else Our recommendation, else the only or first-ranked one,
    each choice recorded in `readings`. A verdict other than ready to design is recorded and the run continues.
    Raises Unusable when the report has no solution or the refined solution carries no design brief (solution-card text
    never stands in for a missing design brief)."""
    d = doc if doc is not None else json.load(open(path, encoding="utf-8"))
    if not isinstance(d, dict):
        raise Unusable("is not an object")
    readings, still = [], []
    sols = {s["id"]: (p, s) for p in d.get("problems") or [] if isinstance(p, dict)
            for s in p.get("solutions") or [] if isinstance(s, dict) and "id" in s}
    marked = d.get("stage") == "refined" or "stage2_round" in d
    refined = marked or any(_has_brief(s) for _, s in sols.values())
    if refined and not marked:
        readings.append(_reading("report kind", "read as a refined report",
                                 "it carries no refined-stage marker but a solution holds a design brief"))
    src = {"file": os.path.abspath(path), "sha256": _sha(path), "kind": "refined report" if refined else "stage 1 report"}
    if doc is not None:
        src["kind"] = "evidence file report"
    if not sols:
        raise Unusable("holds no solutions")
    sid, how = _choose(d, sols, solution, refined, readings, still, lenient)
    p, s = sols[sid]
    v = s.get("verdict") if isinstance(s.get("verdict"), dict) else {}
    brief_here = isinstance(v.get("design_brief"), dict) and bool(v["design_brief"])
    if refined and not brief_here:
        # Decided per chosen solution: without its own design brief it is read from its card. In the source chain that is
        # the last resort (a pasted brief or an evidence-file brief comes first), so card text never displaces a brief.
        if not card_fallback:
            raise Unusable("solution %s carries no design brief" % sid)
        readings.append(_reading("solution content", "read %s from its solution card" % sid,
                                 "it carries no design brief and no other source gave one"))
        still.append({"evidence_state": "needs_more_information", "process_id": None,
                      "detail": "the chosen solution has no design brief from the recommendation, only its summary card; its "
                                "limits, reads and writes are still to be confirmed."})
        refined = False
        src["kind"] = "stage 1 report" if doc is None else src["kind"]
    if refined:
        if v.get("kind") != "ready_to_design":
            readings.append(_reading("verdict", "designed for %s although its verdict is %s" % (sid, v.get("kind")),
                                     "the person handed it over for design; the verdict is kept on the record"))
            if not allow_not_ready:
                still.append({"evidence_state": "needs_more_information", "process_id": None,
                              "detail": "the recommendation ended %s for this solution, not ready to design%s; the design is a "
                                        "draft until that is settled." % (v.get("kind"), (": " + v["reason"]) if v.get("reason") else "")})
        pairs = _brief_pairs(v["design_brief"], readings)
        set_map, set_map_scope = (v["design_brief"].get("set_map"), SCOPED)
        procs = [_pid(x) for x in v["design_brief"].get("processes") or []]
        src.update({"solution_id": sid, "verdict": v.get("kind"), "stage1_recommendation_id": d.get("stage1_recommendation_id")})
    else:
        pairs = [("objective", _txt(d.get("objective"), "description", "text"))] + \
                [("process", "%s: %s" % (_pid(m), m.get("role", "") if isinstance(m, dict) else "")) for m in p.get("process_memberships") or []] + _card_pairs(s)
        src.update({"solution_id": sid, "verdict": None})
        set_map, set_map_scope = None, None
        procs = []
    pairs += [("guardrail", _txt(g, "text")) for g in d.get("guardrails") or []]
    procs = procs or [_pid(m) for m in p.get("process_memberships") or []]
    if set_map is None and isinstance((d.get("current_state") or {}).get("set_map"), dict) and d["current_state"]["set_map"]:
        set_map, set_map_scope = d["current_state"]["set_map"], WHOLE
    out = {"schema": "future-state-design.direction/0", "source": src, "solution_name": s.get("name"), "process_ids": [x for x in procs if x],
           "items": _items(pairs)}
    if how != "named":
        out["solution_chosen"] = how
    if set_map is not None:
        out["set_map"] = set_map
        out["set_map_scope"] = set_map_scope
    if readings:
        out["readings"] = readings
    if still:
        out["what_we_still_need_to_know"] = still
    if doc is not None:
        out["_report_doc"] = doc   # direction_checks reads the report from here; never written
    return out


def _card_pairs(s):
    pairs = [("future_state", s.get("what")), ("future_state", s.get("future"))]
    for k in ("agent_does", "people_keep"):
        v = s.get(k)
        pairs += [(k, x) for x in (v if isinstance(v, list) else re.split(r";\s*", v or ""))]
    for k, kind in (("assumptions", "assumption"), ("ignore_if", "ignore_if")):
        pairs += [(kind, x if isinstance(x, str) else (x.get("text") or x.get("statement") or json.dumps(x))) for x in s.get(k) or []]
    return pairs


PROMPT_KEYS = [("Objective: ", "objective"), ("Proposed future state: ", "future_state"), ("Reads: ", "read"), ("Writes: ", "write"),
               ("Important errors: ", "important_error"), ("Must not: ", "must_not"), ("What we still need to know: ", "still_need_to_know")]


# The current opener is plain words (a slash opener is not recognised in a fresh Cowork task); the two older forms stay
# accepted for design prompts rendered before it.
FIRST_LINE = re.compile(r"^(?:Use the future state design skill \(within:future-state-design\) for this: design|"
                        r"/within:future-state-design Design|Use the future-state-design skill from the Agent "
                        r"Development Kit to design) from the refined report (.+), solution (\S+)\.$")


def from_design_prompt(path, folder=None):
    """The pasted design prompt. A first line naming a refined report goes through resolve(): the report is looked up by
    file name in FOLDER (when given) and beside the prompt, then the brief pasted below it is used."""
    d, source, line, notes = resolve(design_prompt=path, folder=folder)
    d["_resolved"] = [source, line, notes]   # fsd.py pops it into state.json report_source and the plain line
    return d


LABELS = {pre.rstrip(": ").lower(): kind for pre, kind in PROMPT_KEYS}
LABEL_LINE = re.compile(r"^([A-Za-z][^:]{0,60}):\s*(.*)$")
START_LINE = re.compile(r'Start the (?:agent|future state) design for "(.+?)" \(recommendation (\S+?)\)')


BULLET = re.compile(r"^[-*\u2022]\s+(.*)$")


def _pasted_brief(path, text, visible=None):
    """A pasted design prompt or a person's own brief. Labelled lines (Objective:, Reads:, ... any case, any indentation)
    become items; visible prose is also kept as one person_brief item. Decoys never supply direction; the original
    document is retained separately as evidence. None only for a blank document or a bare skill opener."""
    if not text.strip():
        return None
    pairs, procs, in_procs, readings, indented = [], [], False, [], False
    visible = md_blocks.document_text(text) if visible is None else visible
    lines = visible.splitlines()
    first_n = next((n for n, ln in enumerate(lines) if ln.strip()), -1)
    for n, raw in enumerate(lines):
        ln = raw.strip()
        if not ln:
            continue
        indented = indented or raw[:1].isspace()
        if n == first_n and FIRST_LINE.match(ln):
            continue
        if ln.lower() == "processes:":
            in_procs = True; continue
        bm = BULLET.match(ln)
        if in_procs and bm:
            m = re.match(r"(\S+)", bm.group(1))
            procs.append(m.group(1)); pairs.append(("process", bm.group(1))); continue
        in_procs = False
        lm = LABEL_LINE.match(ln)
        kind = LABELS.get(re.sub(r"\s+", " ", lm.group(1).strip().lower())) if lm else None
        if kind:
            body = lm.group(2).strip()
            parts = [body] if kind in ("objective", "future_state") else [x.strip() for x in body.split("; ") if x.strip() and x.strip() != "none"]
            pairs += [(kind, x) for x in parts]
    if visible.strip() and not pairs and not [ln for n, ln in enumerate(lines) if ln.strip() and not (n == first_n and FIRST_LINE.match(ln.strip()))]:
        return None   # only the pinned opener line: nothing a person wrote to carry
    if indented:
        readings.append(_reading("pasted text", "read its lines without their indentation", "the lines were indented"))
    # The document's visible text (the shared md_blocks rule: fenced blocks and HTML comments never supply brief text) always
    # travels verbatim beside what was extracted; the original, fences and comments included, is kept unchanged as evidence
    # (fsd.py copies it into the run: keep_source_document) and the item points at it.
    brief_text = visible.strip("\n")
    if brief_text.strip():
        pairs.append(("person_brief", brief_text))
        readings.append(_reading("pasted text", "kept the document's visible text as one item besides %d labelled line item(s)"
                                 % (len(pairs) - 1), "nothing visible in it is dropped, labelled or not"))
    if visible != "\n".join(text.splitlines()):
        readings.append(_reading("pasted text", "left fenced blocks and comments out of the brief text",
                                 "they are examples or notes by the shared rule; they stay in the kept original document"))
    m = START_LINE.search(visible)
    d = {"schema": "future-state-design.direction/0",
         "source": {"file": os.path.abspath(path), "sha256": _sha(path), "kind": "design prompt", "solution_id": m.group(2) if m else None},
         "solution_name": m.group(1) if m else None, "process_ids": procs, "items": _items(pairs)}
    for i in d["items"]:
        if i["kind"] == "person_brief":
            i["source"] = "person's document"
            i["source_document"] = os.path.basename(path)
    if not brief_text.strip():
        d["what_we_still_need_to_know"] = [{"evidence_state": "needs_more_information", "process_id": None,
            "detail": "The supplied document contains only examples or comments. What should this design achieve?"}]
    if readings:
        d["readings"] = readings
    d["_source_document"] = os.path.abspath(path)   # keep_source_document copies it into the run; never written
    return d


def keep_source_document(d, run):
    """Copy the person's original document (fences and comments included) into RUN/inputs unchanged, and point the
    person_brief item at the copy (run-relative). No-op for directions without one. Returns the run-relative path or None."""
    src = d.pop("_source_document", None)
    if not src or not os.path.isfile(src):
        return None
    rel = "inputs/source-document" + (os.path.splitext(src)[1] or ".txt")
    shutil.copyfile(src, os.path.join(run, *rel.split("/")))
    for i in d.get("items") or []:
        if isinstance(i, dict) and i.get("kind") == "person_brief":
            i["source_document"] = rel
    return rel


# ---------------------------------------------------------------- the report source chain (hand-over record, U4)

SOURCE_WORDS = {"report file": "the refined report file",
                "pasted brief": "the design brief pasted in your prompt",
                "evidence report": "the report saved inside the evidence file",
                "evidence design brief": "the design brief saved inside the evidence file",
                "none": "no design direction"}
PLAIN_WHY = [("with the recorded sha256", "the report file in this folder isn't the one the recommendation wrote"),
             ("could not be read", "the report file couldn't be read"),
             ("holds no solutions", "the report file holds no solutions"),
             ("carries no design brief", "the solution in the report file has no design brief"),
             ("has no solution", "the solution named isn't in the report file"),
             ("is not an object", "the report file isn't shaped like a report"),
             ("no usable design brief", "the report file holds no design brief I could use"),
             ("wasn't in this folder", "the report file wasn't in this folder"),
             ("no report file was named", "no report file was named")]


def plain_why(why):
    """The reason in words for the person: no path, file name, flag or id."""
    return next((w for k, w in PLAIN_WHY if k in (why or "")), "the report file wasn't usable")


SHA_LINE = re.compile(r"\(sha256 ([0-9a-f]{64})\)")


MAX_SCAN = 400   # files the content search looks at, at most


def _roots(paths):
    """The supplied roots: the folders given (or the supplied files' folders), existing, deduplicated by real path."""
    out, seen = [], set()
    for p in paths:
        if p and os.path.isdir(p) and os.path.realpath(p) not in seen:
            seen.add(os.path.realpath(p))
            out.append(os.path.abspath(p))
    return out


def _inside(p, roots):
    """True when p's real path (symbolic links resolved) is a file inside one of the supplied roots."""
    rp = os.path.realpath(p)
    return os.path.isfile(rp) and any(_under(rp, r) for r in roots)


def _under(rp, root):
    r = os.path.realpath(root)
    return rp == r or rp.startswith(r.rstrip(os.sep) + os.sep)


def _load(p):
    try:
        if os.path.getsize(p) > 64 * 1024 * 1024:
            return None
        return json.load(open(p, encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return None


def _report_shaped(d, solution=None):
    """True when d is a recommendation report (problems[].solutions[]), holding SOLUTION when one is named."""
    if not isinstance(d, dict) or not isinstance(d.get("problems"), list):
        return False
    ids = [s.get("id") for p_ in d["problems"] if isinstance(p_, dict) for s in p_.get("solutions") or [] if isinstance(s, dict)]
    return bool(ids) and (not solution or any(_norm_id(i) == _norm_id(solution) for i in ids))


def find_reports(name, roots, want_sha=None, solution=None):
    """Candidate report files, best first, or ([], why). Only files whose real path lies inside a supplied root are ever
    opened or hashed (a symbolic link pointing outside is skipped). Tiers: 0 the declared path (relative with its folders
    under each root, or absolute when it lies inside a root); 1 the declared file name in each root; 2 a bounded content
    search of the roots and the declared folders for report-shaped JSON holding the solution. Identical copies count once.
    Order: a recorded sha256 match first (hashes rank, they never exclude), then tier, then holding the solution, then
    newest. Each candidate: {"path", "tier", "sha", "sha_ok" (None when no hash was recorded), "holds"}."""
    decl = str(name or "").replace("\\", "/").strip()
    base = os.path.basename(decl)
    if not base:
        return [], "no report file was named"
    found, seen_real = [], set()

    def add(p, tier, d=None):
        if not _inside(p, roots):
            return
        r = os.path.realpath(p)
        if r in seen_real:
            return
        d = d if d is not None else _load(r)
        if d is None:
            return
        seen_real.add(r)
        found.append({"path": os.path.abspath(p), "tier": tier, "doc": d})

    rel = decl.lstrip("/") if not os.path.isabs(decl) else None
    sub = [os.path.join(r, *rel.split("/")) for r in roots] if rel and "/" in rel else []
    if os.path.isabs(decl):
        add(decl, 0)
    for q in sub:
        add(q, 0)
    for r in roots:
        add(os.path.join(r, base), 1)
    budget = MAX_SCAN
    for f in list(roots) + [os.path.dirname(q) for q in sub]:
        rf = os.path.realpath(f)
        if not os.path.isdir(rf) or not any(_under(rf, r) for r in roots):
            continue
        for n in sorted(os.listdir(rf)):
            q = os.path.join(f, n)
            if budget <= 0:
                break
            if n.lower().endswith(".json") and _inside(q, roots):
                budget -= 1
                d = _load(os.path.realpath(q))
                if _report_shaped(d, solution):
                    add(q, 2, d)
    if not found:
        return [], "the report file %s wasn't in this folder" % base
    for c in found:
        c["sha"] = _sha(os.path.realpath(c["path"]))
        c["sha_ok"] = (c["sha"] == want_sha) if want_sha else None
        c["holds"] = _report_shaped(c.pop("doc"), solution)
    found.sort(key=lambda c: (c["sha_ok"] is False, c["tier"], not c["holds"], -os.path.getmtime(c["path"]), c["path"]))
    out, shas = [], set()
    for c in found:
        if c["sha"] not in shas:
            shas.add(c["sha"])
            out.append(c)
    return out, None


def _usable(d):
    return bool(d and [i for i in d.get("items") or [] if i.get("kind") != "guardrail"])


def _verdict_brief(ev_path, sid, brief):
    readings = []
    d = {"schema": "future-state-design.direction/0",
         "source": {"file": os.path.abspath(ev_path), "sha256": _sha(ev_path), "kind": "evidence file design brief", "solution_id": sid},
         "solution_name": None, "process_ids": [x for x in (_pid(p) for p in brief.get("processes") or []) if x],
         "items": _items(_brief_pairs(brief, readings))}
    if readings:
        d["readings"] = readings
    if brief.get("set_map") is not None:
        d["set_map"], d["set_map_scope"] = brief["set_map"], SCOPED
    return d


def _merge(d, readings, still=()):
    if readings:
        d["readings"] = list(readings) + list(d.get("readings") or [])
    for w in still:
        d.setdefault("what_we_still_need_to_know", []).append(w)
    return d


def resolve(report=None, design_prompt=None, evidence=None, solution=None, allow_not_ready=False, folder=None):
    """The direction from the best usable source: the report file (looked up by name, then by content, in the folder given and
    beside the prompt, evidence file or report), then the brief pasted in the design prompt, then the evidence file's `report`,
    then its verdicts[<solution id>].design_brief. Returns (direction, source, plain line for the person, notes). Stops only
    when nothing is readable, and then says why."""
    # Only the folders supplied (--folder, and the folders of the files given) are searched; never the working directory.
    roots = _roots([folder] + [os.path.dirname(os.path.abspath(p)) for p in (design_prompt, evidence, report) if p])
    notes, name, want_sha, text, readings = [], None, None, None, []
    if report:
        name = report
    if design_prompt:
        text = open(design_prompt, encoding="utf-8-sig").read()
        visible = md_blocks.document_text(text)
        first = next((ln.strip() for ln in visible.splitlines() if ln.strip()), "")
        m = FIRST_LINE.match(first)
        if m:
            name, solution = name or m.group(1).strip(), solution or m.group(2)
        m2 = SHA_LINE.search(visible)
        want_sha = m2.group(1) if m2 else None
    ev, rep, ev_sid, ev_still = None, None, None, []
    if evidence:
        try:
            ev = json.load(open(evidence, encoding="utf-8"))
        except (OSError, ValueError, UnicodeDecodeError) as e:
            notes.append("the evidence file could not be read (%s)" % type(e).__name__)
            ev = None
        ref = (ev or {}).get("report_ref") if isinstance(ev, dict) else None
        if isinstance(ref, dict):
            name, want_sha = name or ref.get("name"), want_sha or ref.get("sha256")
    why, late = None, []
    if name:
        if report and os.path.isabs(report) and os.path.isfile(report) and not design_prompt:
            cands = [{"path": report, "tier": 0, "sha_ok": None}]   # a report the person named on the command line
        else:
            cands, why = find_reports(name, roots, want_sha, solution)
        tried = 0
        for c in cands:
            if c.get("sha_ok") is False:   # ranked last: tried only after the brief pasted with that hash and the evidence file
                late.append(c)
                continue
            tried += 1
            d, why = _try_report(c["path"], solution, allow_not_ready)
            if _usable(d):
                return _report_found(d, c, cands, readings, design_prompt, solution, base_name=name), "report file", \
                    "I used %s%s." % (SOURCE_WORDS["report file"], _chosen(d, solution)), notes
            late.append(c)
        if cands and not tried:
            why = "no file named %s with the recorded sha256" % os.path.basename(str(name).replace("\\", "/"))
        notes.append(why)
    else:
        why = "no report file was named"
    # the report as the recommendation recorded it, never the fallback artifact's own name or hash
    report_ref = {"name": os.path.basename(str(name).replace("\\", "/")), "sha256": want_sha} if name else None
    if text is not None:
        d = _pasted_brief(design_prompt, text, visible)
        if _usable(d) or (d and d.get("what_we_still_need_to_know")):
            still = []
            if name:
                d["source"]["report_named"] = os.path.basename(str(name).replace("\\", "/"))
                still.append({"evidence_state": "needs_more_information", "process_id": None,
                              "detail": "the recommendation's report wasn't available, so what only it carries (the set map of how "
                                        "the processes link, and any detail the prompt leaves out) was not read; for several "
                                        "processes the set map is drawn from the snapshot instead."})
            d["report_ref"] = report_ref
            if not name and any(i["kind"] == "person_brief" for i in d["items"]):
                line = "I used your own brief as the design direction and kept all of it for the stages to read."
            else:
                line = "I used %s, because %s." % (SOURCE_WORDS["pasted brief"], plain_why(why))
            return _merge(d, readings, still), "pasted brief", line, notes
        notes.append("the pasted prompt was empty")
    if isinstance(ev, dict):
        rep = ev.get("report") if isinstance(ev.get("report"), dict) else None
        verdicts = ev.get("verdicts") if isinstance(ev.get("verdicts"), dict) else {}
        sid = solution or ((rep or {}).get("our_recommendation") or {}).get("solution_id")
        if sid and verdicts and sid not in verdicts:
            sid = _match_id(sid, list(verdicts), readings) or sid
        still = []
        if not sid and verdicts and rep is None:
            withbrief = [k for k, v in verdicts.items() if isinstance(v, dict) and isinstance(v.get("design_brief"), dict)] or list(verdicts)
            sid = withbrief[0]
            if len(withbrief) == 1:
                readings.append(_reading("solution", "took %s, the only one in the evidence file" % sid, "no solution was named"))
            else:
                readings.append(_reading("solution", "took %s, the first listed of %d (others: %s)" % (sid, len(withbrief), ", ".join(withbrief[1:])),
                                         "the evidence file holds several solutions and names none"))
                still.append({"evidence_state": "needs_more_information", "process_id": None,
                              "detail": "the evidence file holds %d solutions and names none; the design follows the first listed. "
                                        "Say if another one should be designed instead." % len(withbrief)})
        ev_sid, ev_still = sid, still
        if rep is not None:
            try:
                d = from_report(evidence, sid, allow_not_ready, doc=rep)
            except Unusable as e:
                d = None
                notes.append("the evidence file's report gave no direction: it %s" % e)
            except (KeyError, TypeError, AttributeError):
                d = None
                notes.append("the evidence file's report gave no direction")
            if _usable(d):
                d["report_ref"] = report_ref
                return _merge(d, readings, still), "evidence report", "I used %s%s, because %s." % (
                    SOURCE_WORDS["evidence report"], _chosen(d, solution), plain_why(why)), notes
        brief = (verdicts.get(sid) or {}).get("design_brief") if sid and isinstance(verdicts.get(sid), dict) else None
        if isinstance(brief, dict):
            d = _verdict_brief(evidence, sid, brief)
            if _usable(d):
                d["report_ref"] = report_ref
                line = "I used %s, because %s." % (SOURCE_WORDS["evidence design brief"], plain_why(why))
                if still:
                    line = line[:-1] + "; it holds several solutions and names none, so I took the first listed (say if you meant another)."
                return _merge(d, readings, still), "evidence design brief", line, notes
        notes.append("the evidence file held no usable design brief")
    # Last resort, before stopping on readable input: a report whose hash differs from the recorded one, the named solution's
    # best-supported stand-in when it isn't in the report, or the chosen solution's card when it has no design brief.
    for c in late:
        d, _ = _try_report(c["path"], solution, allow_not_ready, last_resort=True)
        if _usable(d):
            if c.get("sha_ok") is False:
                readings.append(_reading("report file", "used %s although its sha256 differs from the recorded one"
                                         % os.path.basename(c["path"]), "no other source gave a direction"))
            return _report_found(d, c, None, readings, design_prompt, solution, base_name=name), "report file", \
                "I used %s%s." % (SOURCE_WORDS["report file"], _chosen(d, solution)), notes
    if rep is not None:   # the evidence file's report, read leniently: a stand-in solution or the chosen one's card
        try:
            d = from_report(evidence, ev_sid, allow_not_ready, doc=rep, lenient=bool(ev_sid), card_fallback=True)
        except (Unusable, KeyError, TypeError, AttributeError):
            d = None
        if _usable(d):
            d["report_ref"] = {"name": os.path.basename(str(name).replace("\\", "/")), "sha256": want_sha} if name else None
            return _merge(d, readings, ev_still), "evidence report", "I used %s%s, because %s." % (
                SOURCE_WORDS["evidence report"], _chosen(d, ev_sid), plain_why(why)), notes
    raise SystemExit("refused: no design direction could be read: " + "; ".join(n for n in notes if n) +
                     ". Give the refined report, the design prompt with its brief, or the evidence file.")


def _try_report(path, solution, allow_not_ready, last_resort=False):
    """(direction or None, why). last_resort lets a missing named solution and a brief-less solution be read leniently."""
    try:
        return from_report(path, solution, allow_not_ready, lenient=last_resort and bool(solution), card_fallback=last_resort), None
    except Unusable as e:
        return None, "the report file %s" % e
    except (ValueError, OSError, UnicodeDecodeError) as e:
        return None, "the report file could not be read (%s)" % type(e).__name__
    except (KeyError, TypeError, AttributeError) as e:
        return None, "the report file had no usable design brief (%s)" % type(e).__name__


def _report_found(d, c, cands, readings, design_prompt, solution, base_name=None):
    path = c["path"]
    d["report_ref"] = {"name": os.path.basename(path), "sha256": _sha(path)}
    if design_prompt:
        d["source"]["via_design_prompt"] = {"file": os.path.abspath(design_prompt), "sha256": _sha(design_prompt)}
    declared = os.path.basename(str(base_name or "").replace("\\", "/"))
    if c.get("tier") == 2 or (declared and os.path.basename(path) != declared):
        readings.append(_reading("report file", "used %s" % os.path.basename(path),
                                 "no usable file was named %s; this report in the folder given holds the solution" % declared))
    others = [x["path"] for x in cands or [] if x is not c]
    if others:
        readings.append(_reading("report file", "used %s" % os.path.basename(path),
                                 "%d candidates; it ranked first (recorded hash, declared place, holds the solution, newest); "
                                 "others: %s" % (len(others) + 1, ", ".join(os.path.basename(x) for x in others))))
    return _merge(d, readings)


def _chosen(d, named):
    """The plain-line tail when the solution was chosen rather than named as given: its name, so the person can redirect."""
    how = d.get("solution_chosen")
    if not how:
        return ""
    nm = d.get("solution_name") or "the recommended solution"
    why = "the solution named isn't in the report" if named else "no solution was named"
    if how == "recommended":
        return ", designing for \"%s\", the solution the report recommends%s" % (nm, "" if not named else ", because the solution named isn't in the report")
    if how == "first-ranked":
        return ", designing for \"%s\", the first-ranked solution, because %s (say if you meant another)" % (nm, why)
    return ", designing for \"%s\" because %s" % (nm, why)


def _wwsnk(d, detail, process_id=None, state="needs_more_information"):
    d.setdefault("what_we_still_need_to_know", []).append({"evidence_state": state, "process_id": process_id, "detail": detail})


def _apply(d, f):
    """Carry out one "corrected" finding's fix on the direction, recording the reading and a What we still need to know item."""
    fix = f.get("fix") or {}
    sm = d.get("set_map") if isinstance(d.get("set_map"), dict) else None
    if fix.get("kind") == "keep-scoped-as-evidence" and sm is not None:
        d["set_map_evidence"] = {"scope": d.get("set_map_scope"), "set_map": d.pop("set_map"),
                                 "why": "no whole set map came with the recommendation to check this scoped copy against"}
        d.pop("set_map_scope", None)
        d.setdefault("readings", []).append(_reading(
            "set map", "kept the brief's scoped set map as evidence only; the set-map stage draws the map from the snapshot",
            "there is no whole set map to check the scoped copy against"))
        _wwsnk(d, "the recommendation carried only a scoped copy of the set map, with no whole map to check it against; the map is "
                  "drawn from the snapshot and the scoped copy is kept as evidence. Confirm how the processes link.")
    elif fix.get("kind") == "add-link" and sm is not None:
        link = fix.get("link") or {}
        links = [l for l in sm.get("links") or [] if not (link.get("id") and l.get("id") == link.get("id"))]
        sm["links"] = links + [link]
        for n in fix.get("add_neighbours") or []:
            sm.setdefault("context_neighbours", []).append(n)
        d.setdefault("readings", []).append(_reading(
            "set map", "scoped copy corrected from the whole map: restored the handoff %s" % (link.get("id") or "link"),
            "the scoped copy dropped a handoff the whole map records from a process it keeps"))
        _wwsnk(d, "the recommendation's scoped set map left out a handoff from %s that the whole map records; it was restored from "
                  "the whole map. Confirm the handoff." % f.get("process_id"), f.get("process_id"))
    else:
        d.setdefault("open_points", []).append({"process_id": f.get("process_id"), "detail": f.get("detail")})


def intake(d, snapshot):
    """Run direction_checks.check(d, snapshot) and act on its findings (pins addendum B, as ruled in 0495: no finding stops the
    run). "corrected" findings carry a fix that is applied here and recorded (readings + What we still need to know);
    "contradictory" findings join what_we_still_need_to_know[] with evidence_state Contradictory; "open-point" findings, and
    any finding of a severity not known here, join open_points[] (never the set map). Returns the findings."""
    findings = direction_checks.check(d, snapshot) or []
    for f in findings:
        if f.get("severity") == "corrected":
            _apply(d, f)
        elif f.get("severity") == "contradictory":
            _wwsnk(d, f.get("detail"), f.get("process_id"), "Contradictory")
        else:
            d.setdefault("open_points", []).append({"process_id": f.get("process_id"), "detail": f.get("detail")})
    return findings


def _id_variant(pid, scope):
    """The snapshot id a direction id names, when they differ only by case/space/dash or a `<kind>:` prefix; else None."""
    hits = [x for x in scope if _norm_id(x) == _norm_id(pid) or _norm_id(str(pid).rsplit(":", 1)[-1]) == _norm_id(x)]
    return hits[0] if len(hits) == 1 else None


def process_check(d, scope):
    """'match' when the direction names exactly the snapshot's processes; otherwise say how they differ (the run continues; stages see it).
    fsd.py calls this just before it writes design-direction.json, so the intake checks run here first. scope is the snapshot's
    process ids, or the snapshot.json dict itself (then the checks see all of it). A direction id that names a snapshot id
    with a different case, dashes or a `process:` style prefix is read as that id (recorded)."""
    snapshot = scope if isinstance(scope, dict) else {"scope": {"process_ids": list(scope)}}
    scope = (snapshot.get("scope") or {}).get("process_ids") or []
    ids = []
    for p in d.get("process_ids") or []:
        v = p if p in scope else _id_variant(p, scope)
        if v and v != p:
            d.setdefault("readings", []).append(_reading("process id", "read %r as the snapshot process %s" % (p, v),
                                                          "they name the same process apart from case, dashes or a prefix"))
        ids.append(v or p)
    if ids:
        d["process_ids"] = ids
    elif d.get("items"):
        text = "\n".join(str(i.get("text") or "") for i in d["items"] if isinstance(i, dict))
        named = [x for x in scope if re.search(r"(?<![\w-])%s(?![\w-])" % re.escape(str(x)), text)]
        if named:
            d["process_ids"] = named
            d.setdefault("readings", []).append(_reading("process", "took %s as the process(es) in scope" % ", ".join(named),
                                                          "the direction lists no process but its text names these snapshot processes"))
    intake(d, snapshot)
    want, have = set(d.get("process_ids") or []), set(scope)
    if not want:
        return "direction names no process"
    if want == have:
        return "match"
    return "differs: direction %s, snapshot %s" % (sorted(want), sorted(have))


def portable(d):
    """The record fsd.py writes (portable run records, 0466 X1): each source file (source, and source.via_design_prompt when
    present) becomes {name: basename, sha256} beside its other fields, with no "file" key and no absolute path, so a run folder
    copied elsewhere carries no machine path. The direction read here keeps "file" until then: the intake checks re-read the
    report from it. Accepts a source already in the {name, sha256} form. Returns d, changed in place."""
    def one(src):
        if isinstance(src, dict) and "file" in src:
            f = src.pop("file")
            src["name"] = os.path.basename(f) if isinstance(f, str) else f
        return src
    d.pop("_report_doc", None)
    d.pop("_source_document", None)
    src = one(d.get("source"))
    if isinstance(src, dict) and src.get("via_design_prompt"):
        one(src["via_design_prompt"])
    return d


def brief_section(run):
    p = os.path.join(run, "inputs", "design-direction.json")
    if not os.path.isfile(p):
        return ""
    d = json.load(open(p, encoding="utf-8"))
    src = d["source"]
    lines = ["", "", "## Design direction (from Agent Recommendations)",
             "Before this design started, the person chose a direction in Agent Recommendations (%s%s, `inputs/design-direction.json`). "
             "It is the **target** the design aims at, not evidence about the process: facts still come only from `inputs/`, and the "
             "direction never turns an Assumed fact into an Observed one." % (src["kind"], ", solution %s" % src.get("solution_id") if src.get("solution_id") else ""),
             ""]
    if d.get("process_check") and d["process_check"] != "match":
        lines += ["**Scope note:** the direction's processes and this snapshot's processes differ (%s). Design for the snapshot's processes; "
                  "record every direction item that depends on a missing process as departed, with that reason." % d["process_check"], ""]
    if d.get("set_map"):
        sm = d["set_map"]
        lines += ["**Set map:** `inputs/design-direction.json` → `set_map` (%s): %d processes, %d links, %d context neighbours, %d open points. "
                  "It records how the processes link to each other and to the work around them, with each link's evidence. Read it before "
                  "drawing the current state; refresh it against `inputs/`, and treat a link the snapshot contradicts as an open, not a fact."
                  % (d.get("set_map_scope"), len(sm.get("processes") or []), len(sm.get("links") or []), len(sm.get("context_neighbours") or []),
                     len(sm.get("open_points") or [])), ""]
    if d.get("set_map_evidence"):
        lines += ["**Set map kept as evidence only:** `inputs/design-direction.json` → `set_map_evidence`. %s, so it is not the "
                  "set map: the set-map stage draws the map from the snapshot, and may use this copy as evidence." % (
                      str(d["set_map_evidence"].get("why") or "it could not be checked").capitalize()), ""]
    if d.get("readings"):
        lines += ["**How the direction was read** (each reading the intake took, and why):"]
        lines += ["- %s: %s (%s)" % (r.get("what"), r.get("reading"), r.get("why")) for r in d["readings"] if isinstance(r, dict)]
        lines += [""]
    if d.get("what_we_still_need_to_know"):
        lines += ["**What we still need to know from the direction** (carry each into the stage's What we still need to know):"]
        lines += ["- %s" % w.get("detail") for w in d["what_we_still_need_to_know"] if isinstance(w, dict) and w.get("detail")]
        lines += [""]
    lines += ["| Item | Kind | Direction |", "|---|---|---|"]
    lines += ["| %s | %s | %s |" % (i["id"], KIND_TEXT.get(i["kind"], i["kind"]), item_text(i).replace("|", "/").replace("\n", " ")) for i in d["items"]]
    lines += ["",
              "How the stages use it:",
              "- **current-state:** ignore the direction. The Current state Workflow describes today's work only.",
              "- **transformation-plan and future-state:** the direction is the floor, not the ceiling. Its processes, objective, reads and "
              "proposed agent are the minimum the design delivers. Reshape every process in `inputs/snapshot.json` `scope.process_ids`, "
              "whether or not the direction names it; context neighbours stay context unless the direction brings them in. Work outside the "
              "recommended agent may go to further agents where the evidence supports it. Carry every \"What we still need to know\" item "
              "into the stage's What we still need to know (reuse an existing open id when it is the same open).",
              "- **Limits in the direction.** A \"Must not\" or a write whose who writes is a person binds only when it is marked "
              "client-quoted (its text ends with `(client-stated: <source id>)`) or follows from key control or an outside party. Any other "
              "limit is a proposal: the design may depart from it, writing `departed: <reason>` in the Design direction table, and lists it "
              "under What we still need to know. Where a marker's source id is not among the snapshot's sources, read the item as unmarked and "
              "say so in one line; where it is unclear whether a limit binds, take the best-supported reading, say why and continue.",
              "- **Depart only for a reason** (the evidence contradicts the item, the item cannot be carried in this process, or it is an "
              "unmarked limit the evidence gives the agent). Never depart for convenience.",
              "- **transformation-plan records it.** Add a section `## Design direction` directly before `## Reshape moves`, with this table header exactly:",
              "  `| Item | Followed | How the map carries it |`",
              "  One row per item above, in order. Item: the `DD-n` id. Followed: `yes`, or `departed: <reason from the evidence>`. How the map "
              "carries it: the steps, write grants, moves or opens that carry it (ids)."]
    return "\n".join(lines)
