#!/usr/bin/env python3
"""Post-run steps, run by the driver (never by agents). Read-only over the run; writes logs/postrun.json and logs/stage-table.md.

Usage: postrun.py RUN_ROOT --snapshot-dir DIR [--name-scan TOOL --names LIST [--allowlist FILE]]

  1. vocabulary scan   banned terms in the deliverables: stages/, simulation/, gate/ and the page inputs in page/. The stage agents' own
                       logs (logs/agent-*.md) and the plugin's own *.template.md copies inside work folders are skipped: their audit lines list the banned words on purpose. A hit inside a JSON/YAML
                       key name is marked key-name; a key name in ALLOWED_KEY_NAMES is not reported at all.
  2. observed claims   every Observed-stamped object in a workflow JSON (the stage's input AND the delivered JSON the page reads) cites source_refs whose source_id exists in the frozen
                       snapshot (source or step id) and whose pointer, when given, resolves in that raw pull
  3. name scan         the cross-client name scanner over stages/ and logs/agent-*; hits whose term is part of one of the
                       target's own system names (snapshot inventory) are listed as target-own; every other hit is a flag for a
                       person. Matched text of flags is NOT stored (redacted to first letter + length): run folders are committed.
  4. stage table       minutes, cost, checks per stage from logs/driver.jsonl
Stdlib only.
"""
import json, os, re, subprocess, sys

BANNED = [("contract", re.compile(r"contract", re.I)), ("Context Graph", re.compile(r"context\s+graph", re.I)),
          ("Process Index", re.compile(r"process\s+index", re.I)), ("human", re.compile(r"(?<![A-Za-z])humans?(?![A-Za-z])", re.I)),
          ("Agent Audit", re.compile(r"agent\s+audit", re.I))]
TEXT = {".md", ".yaml", ".yml", ".json", ".txt", ".html", ".svg"}
SCAN_DIRS = ("stages", "simulation", "gate", "page")   # deliverables and page inputs; logs/ is never scanned
# Exact key names a plugin schema requires the agents to write even though they contain a banned word. A hit on such a key name
# is not reported. Empty today: no plugin template or check requires a field name with a banned word. Add a name here only with a
# reference to the schema that demands it; values and prose are never allowed.
ALLOWED_KEY_NAMES = frozenset()


def text_files(run):
    for base in (os.path.join(run, d) for d in SCAN_DIRS):
        for dp, _, fs in os.walk(base):
            for f in fs:
                p = os.path.join(dp, f)
                rel = os.path.relpath(p, run)
                own_rules = any(t in rel for t in ("page-prep", "page_prep", "page-build", "page-screens", "RUNPAGE-RULES", "PLAIN-FILES", "text_rules"))   # the page kit's rule files and prompts list the banned words on purpose
                if os.path.splitext(f)[1] in TEXT and not own_rules and not f.endswith(".template.md") and not f.startswith("raw-") and not f.startswith("prompt-") and f not in ("driver.jsonl",):
                    yield p, rel


def vocab_scan(run):
    hits = []
    for p, rel in text_files(run):
        for n, ln in enumerate(open(p, encoding="utf-8", errors="replace"), 1):
            for name, rx in BANNED:
                for m in rx.finditer(ln):
                    key = bool(re.match(r"""\s*["']?[\w.-]*""" + re.escape(m.group(0)) + r"""[\w.-]*["']?\s*:""", ln, re.I))
                    if key:
                        km = re.match(r"""\s*["']?([\w.-]*)["']?\s*:""", ln)
                        if km and km.group(1) in ALLOWED_KEY_NAMES:
                            continue
                    hits.append({"file": rel, "line": n, "term": name, "kind": "key-name" if key else "prose"})
    return hits


def resolve_pointer(doc, ptr):
    cur = doc
    for part in [x for x in ptr.split("/") if x != ""]:
        part = part.replace("~1", "/").replace("~0", "~")
        try:
            cur = cur[int(part)] if isinstance(cur, list) else cur[part]
        except (KeyError, IndexError, ValueError, TypeError):
            return False
    return True


def observed_claims(run, snap_dir, keys=("current-state", "future-state")):
    snap = json.load(open(os.path.join(snap_dir, "snapshot.json")))
    sources = {s["id"]: s for s in snap.get("sources", [])}
    step_ids = {s["id"] for s in snap.get("steps", [])}
    raw = {}
    out = {"checked": 0, "no_citation": [], "unknown_source": [], "pointer_unresolved": [], "source_unreadable": [], "files_read": [], "files_missing": []}
    seen = set()

    def once(k, v):
        if v not in out[k]:
            out[k].append(v)

    def raw_doc(sid):
        if sid not in raw:
            try:
                raw[sid] = json.load(open(os.path.join(snap_dir, sources[sid]["path"])))
            except (KeyError, OSError, ValueError):
                raw[sid] = None
        return raw[sid]

    def walk(o, where):
        if isinstance(o, dict):
            if o.get("evidence_state") == "Observed":
                # The input and the delivered JSON usually carry the same object: it is checked, and its problems listed, once.
                ident = f"{where}:{o.get('id', '?')}"
                if ident not in seen:
                    seen.add(ident)
                    out["checked"] += 1
                refs = o.get("source_refs") or []
                if not refs:
                    once("no_citation", ident)
                for r in refs:
                    sid = r.get("source_id") if isinstance(r, dict) else None
                    if sid not in sources and sid not in step_ids:
                        once("unknown_source", f"{ident}->{sid}")
                    elif r.get("pointer"):
                        base = sid if sid in sources else next((s["source_id"] for s in snap["steps"] if s["id"] == sid), None)
                        doc = raw_doc(base) if base else None
                        if doc is None:
                            once("source_unreadable", f"{ident}->{sid}")
                        elif not resolve_pointer(doc, r["pointer"]):
                            once("pointer_unresolved", f"{ident}->{sid}{r['pointer']}")
            for v in o.values():
                walk(v, where)
        elif isinstance(o, list):
            for v in o:
                walk(v, where)

    # The stage input and the delivered (rendered) JSON the page reads. A missing or unreadable file is listed: it was not covered.
    for key in keys:
        for name in (f"{key}.input.json", f"{key}.json"):
            rel = f"stages/{key}/{name}"
            p = os.path.join(run, rel)
            try:
                doc = json.load(open(p))
            except OSError:
                out["files_missing"].append(rel)
                continue
            except ValueError as e:
                out.setdefault("unreadable", []).append(f"{rel}: {e}")
                continue
            out["files_read"].append(rel)
            walk(doc, key)
    return out


def claims_affected(oc):
    """Distinct Observed objects with at least one citation problem (one object can have several problem entries)."""
    return len({x.split("->")[0] for k in ("no_citation", "unknown_source", "pointer_unresolved", "source_unreadable") for x in oc.get(k, [])})


def name_scan(run, snap_dir, tool, names, allowlist):
    snap = json.load(open(os.path.join(snap_dir, "snapshot.json")))
    own = [str(s).lower() for s in snap.get("systems", [])]
    cmd = ["python3", tool, "--names", names] + (["--allowlist", allowlist] if allowlist else []) + \
          [os.path.join(run, "stages")] + [os.path.join(run, "logs", f) for f in os.listdir(os.path.join(run, "logs")) if f.startswith("agent-")]
    p = subprocess.run(cmd, capture_output=True, text=True)
    own_hits, flags = {}, []
    for ln in p.stdout.splitlines():
        m = re.match(r"^(.*?):(\d+): ([^:]+): ", ln)
        if not m:
            continue
        path, line, term = m.group(1), int(m.group(2)), m.group(3)
        rel = os.path.relpath(path, run) if os.path.isabs(path) else path
        if any(term.lower() in s for s in own):
            own_hits[term] = own_hits.get(term, 0) + 1
        else:
            flags.append({"file": rel, "line": line, "term_redacted": f"{term[0]}…({len(term)})"})
    return {"command": "python3 <client-name-scan.py> --names <client-name-list.txt>" + (" --allowlist <client-name-allowlist.txt>" if allowlist else "") +
            " <run>/stages <run>/logs/agent-*", "returncode": p.returncode, "target_own_terms": own_hits, "flags_for_person": flags}


def stage_table(run):
    rows, cost = [], 0.0
    for ln in open(os.path.join(run, "logs", "driver.jsonl")):
        d = json.loads(ln)
        if d.get("event") == "phase-end":
            rows.append(f"| phase: {d.get('phase')} | {'error' if d.get('error') else 'ok'} | {d.get('minutes')} | - | - |")
        if d.get("event") == "end" and (d.get("stage") or d.get("step")):
            d.setdefault("stage", d.get("step"))
            ck = d.get("checks") or []
            ok = sum(1 for c in ck if c["result"] == "pass")
            rows.append(f"| {d['stage']} | {d.get('outcome')} | {d.get('minutes')} | {d.get('cost_usd')} | {ok}/{len(ck)} |")
            cost += d.get("cost_usd") or 0
        if d.get("event") == "checks-rerun" and d.get("stage"):   # the stage's checks run again on the delivered files; these replace the row above
            ck = d.get("checks") or []
            ok = sum(1 for c in ck if c.get("result") == "pass")
            rows.append(f"| {d['stage']} (rechecked after the {d.get('after')}) | - | - | - | {ok}/{len(ck)} |")
    txt = "| Stage | Outcome | Minutes | Cost USD | Checks passing |\n|---|---|---|---|---|\n" + "\n".join(rows) + f"\n\nTotal cost USD: {cost:.2f}\n"
    open(os.path.join(run, "logs", "stage-table.md"), "w").write(txt)
    return txt


def main(argv):
    run = os.path.abspath(argv[1])
    g = lambda flag: argv[argv.index(flag) + 1] if flag in argv else None
    snap_dir = g("--snapshot-dir")
    res = {"vocabulary": vocab_scan(run), "observed_claims": observed_claims(run, snap_dir)}
    if g("--name-scan"):
        res["name_scan"] = name_scan(run, snap_dir, g("--name-scan"), g("--names"), g("--allowlist"))
    else:
        res["name_scan"] = "not run (no --name-scan given)"
    stage_table(run)
    json.dump(res, open(os.path.join(run, "logs", "postrun.json"), "w"), indent=1)
    print(json.dumps({"vocab_prose_hits": sum(h["kind"] == "prose" for h in res["vocabulary"]),
                      "vocab_key_name_hits": sum(h["kind"] == "key-name" for h in res["vocabulary"]),
                      "observed_checked": res["observed_claims"]["checked"],
                      "observed_problems": sum(len(res["observed_claims"].get(k, [])) for k in ("no_citation", "unknown_source", "pointer_unresolved", "source_unreadable")),
                      "observed_claims_affected": claims_affected(res["observed_claims"]),
                      "observed_files_not_covered": res["observed_claims"]["files_missing"] + [u.split(": ")[0] for u in res["observed_claims"].get("unreadable", [])],
                      "name_scan": (res["name_scan"] if isinstance(res["name_scan"], str) else
                                    {"rc": res["name_scan"]["returncode"], "target_own": res["name_scan"]["target_own_terms"],
                                     "flags": len(res["name_scan"]["flags_for_person"])})}))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
