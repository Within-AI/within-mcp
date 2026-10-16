#!/usr/bin/env python3
"""Intake checks on the design direction, run by direction.py before design-direction.json is written.

check(direction, snapshot) -> list of findings, each {"severity": "corrected" | "contradictory" | "open-point", "rule": str,
"process_id": str | None, "detail": str}, plus "fix" on a corrected finding (pins addendum B; 0495: no finding stops a run,
so there is no refuse severity). Only a direction that carries a set map is checked:
  - corrected "scoped-no-whole-map": the set map is the brief's scoped copy and the source report (re-read from
    direction["source"]["file"], same sha256) has no current_state.set_map to compare it with, or cannot be read. Fix
    {"kind": "keep-scoped-as-evidence"}: the scoped copy is kept as evidence, not as the whole map, and the set-map stage
    draws the map from the snapshot.
  - corrected "scoped-drops-handoff-target": the whole map has a handoff link from a process the scoped copy keeps, and the
    scoped copy drops that link or its target (the target is neither a kept process nor a context neighbour). Fix
    {"kind": "add-link", "link": <the whole map's link>, "add_neighbours": [<the target's entry from the whole map>]}.
  - contradictory "set-map-process-not-in-snapshot": a process in the carried set map's processes[] that the snapshot lacks.
  - contradictory "snapshot-process-not-in-set-map": a snapshot process that neither the carried map (processes or context
    neighbours) nor the whole map knows. A direction naming a subset of the snapshot's processes is not a mismatch: the
    snapshot's other processes are looked up in the whole map, not only in the direction's scoped copy.
  - open-point "link-feedback": feedback in the report (feedback_summary) that touches a link, by naming the link's id or two
    processes the link joins (each by its id, or by its title or name as a whole phrase, any case). It is recorded as an open
    point and never merged into the set map.
  - open-point "link-feedback-unread": the report could not be read again, so its feedback was not checked.
  - open-point "snapshot-names-no-process": the snapshot names no process, so nothing was compared (missing is not a match).
Never edits the direction or the set map itself: direction.intake applies each fix and records it. Stdlib only.
"""
import hashlib, json, os, re

SCOPED = "scoped to the design brief"


def _f(severity, rule, process_id, detail, fix=None):
    out = {"severity": severity, "rule": rule, "process_id": process_id, "detail": detail}
    if fix is not None:
        out["fix"] = fix
    return out


def _entry(whole, pid):
    """The whole map's entry for a process (member or context neighbour), as a context neighbour of the scoped copy."""
    for x in (whole.get("processes") or []) + (whole.get("context_neighbours") or []):
        if isinstance(x, dict) and x.get("process_id") == pid:
            return dict(x)
    return {"process_id": pid}


def _ends(link, end):
    v = link.get(end)
    return [x for x in (v if isinstance(v, list) else [v]) if x]


def _pids(items):
    return [x.get("process_id") for x in items or [] if isinstance(x, dict) and x.get("process_id")]


def _report(direction):
    """The source report, re-read and checked against the sha256 recorded at intake; (report, None) or (None, reason)."""
    if isinstance(direction.get("_report_doc"), dict):   # the report came inside an evidence file: no file to re-read
        return direction["_report_doc"], None
    src = direction.get("source") or {}
    path = src.get("file")
    if not path or not os.path.isfile(path):
        return None, "the source report %s cannot be found" % (path or "(none recorded)")
    with open(path, "rb") as f:
        raw = f.read()
    if src.get("sha256") and hashlib.sha256(raw).hexdigest() != src["sha256"]:
        return None, "the source report %s changed since the direction was read (sha256 differs)" % path
    try:
        return json.loads(raw.decode("utf-8")), None
    except ValueError as e:
        return None, "the source report %s is not readable JSON (%s)" % (path, e)


def _handoff_findings(whole, scoped):
    keep = set(_pids(scoped.get("processes")))
    known = keep | set(_pids(scoped.get("context_neighbours")))
    have_ids = {l.get("id") for l in scoped.get("links") or [] if l.get("id")}
    # Review E3: a handoff survives only as a handoff with its original source-target pair; an unchanged id is no proof.
    have_pairs = {(f, t) for l in scoped.get("links") or [] if l.get("kind") == "handoff"
                  for f in _ends(l, "from") for t in _ends(l, "to")}
    out, added = [], set()
    for l in whole.get("links") or []:
        if l.get("kind") != "handoff":
            continue
        for src in (x for x in _ends(l, "from") if x in keep):
            for tgt in _ends(l, "to"):
                kept = (src, tgt) in have_pairs
                if tgt in known and kept:
                    continue
                what = "the link %s" % l["id"] if l.get("id") else "the handoff link"
                if kept:
                    how = "keeps the link but not %s as a process or context neighbour" % tgt
                elif l.get("id") in have_ids:
                    how = "keeps the id %s but no handoff from %s to %s any more" % (l["id"], src, tgt)
                else:
                    how = "drops the link"
                add = [] if tgt in known or tgt in added else [_entry(whole, tgt)]
                added.update(n["process_id"] for n in add)
                out.append(_f("corrected", "scoped-drops-handoff-target", src,
                              "the brief's scoped set map drops the handoff from %s to %s (%s in the whole map): it %s. "
                              "The scoped copy is corrected from the whole map." % (src, tgt, what, how),
                              {"kind": "add-link", "link": dict(l), "add_neighbours": add}))
    return out


def _sentences(text):
    return [s.strip() for s in re.split(r"(?<=[.!?;])\s+|\n+", text or "") if s.strip()]


def _names(sentence, token, fold=False):
    pat = r"(?<![\w-])%s(?![\w-])" % r"\s+".join(re.escape(w) for w in token.split())
    return re.search(pat, sentence, re.I if fold else 0) is not None


def _labels(maps):
    """process_id -> titles and names that people use for it in feedback, from processes and context neighbours."""
    out = {}
    for m in maps:
        for x in (m.get("processes") or []) + (m.get("context_neighbours") or []):
            if isinstance(x, dict) and x.get("process_id"):
                out.setdefault(x["process_id"], set()).update(
                    v.strip() for v in (x.get("title"), x.get("name")) if isinstance(v, str) and v.strip())
    return out


def _mentions(sentence, pid, labels):
    return _names(sentence, pid) or any(_names(sentence, t, fold=True) for t in labels.get(pid, ()))


def _feedback_findings(report, maps):
    fb = report.get("feedback_summary")
    texts = [fb] if isinstance(fb, str) else [x for x in fb or [] if isinstance(x, str)]
    links, seen, out, labels = [], set(), [], _labels(maps)
    for m in maps:
        for l in m.get("links") or []:
            key = l.get("id") or (tuple(_ends(l, "from")), tuple(_ends(l, "to")))
            if key not in seen:
                seen.add(key)
                links.append(l)
    for s in (s for t in texts for s in _sentences(t)):
        for l in links:
            frm, to = _ends(l, "from"), _ends(l, "to")
            by_id = bool(l.get("id")) and _names(s, l["id"])
            by_ends = any(_mentions(s, a, labels) for a in frm) and any(_mentions(s, b, labels) for b in to)
            if by_id or by_ends:
                out.append(_f("open-point", "link-feedback", (frm or [None])[0],
                              "feedback in the report touches the link %s (%s to %s): \"%s\". Recorded as an open point, "
                              "not merged into the set map; confirm it against the evidence."
                              % (l.get("id") or "without an id", ", ".join(frm), ", ".join(to), s)))
    return out


def check(direction, snapshot):
    sm = (direction or {}).get("set_map")
    if not isinstance(sm, dict):
        return []
    findings = []
    report, why = _report(direction)
    whole = ((report or {}).get("current_state") or {}).get("set_map")
    if direction.get("set_map_scope") == SCOPED:
        if not isinstance(whole, dict):
            findings.append(_f("corrected", "scoped-no-whole-map", None,
                               "the design brief carries a scoped set map but there is no whole map to check it against (%s). "
                               "The scoped copy is kept as evidence and the set map is drawn from the snapshot."
                               % (why or "the report has no current_state.set_map"), {"kind": "keep-scoped-as-evidence"}))
        else:
            findings += _handoff_findings(whole, sm)
    else:
        whole = sm
    snap = [x for x in ((snapshot or {}).get("scope") or {}).get("process_ids") or [] if x]
    if snap:
        members = _pids(sm.get("processes"))
        known = set(members) | set(_pids(sm.get("context_neighbours"))) | \
            (set(_pids(whole.get("processes"))) | set(_pids(whole.get("context_neighbours"))) if isinstance(whole, dict) else set())
        for p in members:
            if p not in snap:
                findings.append(_f("contradictory", "set-map-process-not-in-snapshot", p,
                                   "the set map names process %s, which the snapshot does not hold; links touching it are not "
                                   "Observed until the evidence settles it." % p))
        unchecked = direction.get("set_map_scope") == SCOPED and not isinstance(whole, dict)   # kept as evidence only
        for p in ([] if unchecked else snap):
            if p not in known:
                findings.append(_f("contradictory", "snapshot-process-not-in-set-map", p,
                                   "the snapshot holds process %s, which the set map does not name; the map's links around it "
                                   "are not Observed until the evidence settles it." % p))
    else:
        findings.append(_f("open-point", "snapshot-names-no-process", None,
                           "the snapshot names no process, so the set map's processes were not compared with it."))
    if report is not None:
        findings += _feedback_findings(report, [m for m in (sm, whole) if isinstance(m, dict)])
    elif direction.get("set_map_scope") != SCOPED:
        findings.append(_f("open-point", "link-feedback-unread", None,
                           "link feedback in the report was not checked: %s." % why))
    return findings
