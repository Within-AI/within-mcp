#!/usr/bin/env python3
"""Where Future State Design reads its reference material: the plugin's one lookup, and nothing else.

Usage: knowledge.py [--run RUN_DIR]     print the root and every path, and whether each exists

The root comes from find() in skills/fast-mode-build/driver/knowledge_root.py (imported, never copied). A new run
records that root and its manifest hash in state.json; a resumed run reads the root it recorded, after verify() has
checked the root and its manifest hash. A change is recorded (state.json `knowledge_changes` and an event), never
refused: a changed manifest hash is recorded and the run reads the knowledge as it is now; when the recorded root is
gone, the run moves to the root find() resolves now (key v1.2, K3) and fsd.py logs a `knowledge-root-changed` event. Every other script in this skill gets
its paths from path(name); none names a reference folder itself. Exit 0 when every path exists, 1 otherwise. Stdlib only.
"""
import argparse, json, os, sys

SKILL = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(SKILL), "fast-mode-build", "driver"))
import knowledge_root  # noqa: E402  the one lookup

# Each thing the stages read, by its place under the root.
PATHS = {
    "library": "reference/library",
    "workflow_method": "reference/workflow-diagrams.md",
    "factory_method": "reference/agent-technical-design-factory.md",
    "templates": "templates",
    "checks": "checks",
    "renderer": "renderer",
    "stage_references": "scripts/stage-references.py",
    "freeze_snapshot": "scripts/freeze-snapshot.py",
    "reference_registry": "scripts/_reference_registry.py",
}
ROOT_KEY, HASH_KEY = "knowledge_root", "knowledge_manifest_sha256"   # in state.json and the run-init event


class KnowledgeChanged(Exception):
    """The run's state.json cannot be read (kept for callers; knowledge changes themselves are recorded, not raised)."""


def recorded(run):
    """(root, manifest hash) the run recorded at init, either None when absent."""
    try:
        with open(os.path.join(run, "state.json"), encoding="utf-8") as f:
            st = json.load(f)
    except (OSError, ValueError) as e:
        raise KnowledgeChanged("refused: cannot read the run's state.json (%s)" % e)
    return st.get(ROOT_KEY), st.get(HASH_KEY)


def _record_change(run, kind, detail, updates):
    """Record a knowledge change in state.json (`knowledge_changes`, plus any field updates) and events.jsonl, once per
    distinct change; print one note line to stderr. Never refuses: the run carries on with the knowledge it finds."""
    p = os.path.join(run, "state.json")
    try:
        with open(p, encoding="utf-8") as f:
            st = json.load(f)
    except (OSError, ValueError):
        return
    entry = dict({"kind": kind, "detail": detail}, **{k: v for k, v in updates.items()})
    changes = st.setdefault("knowledge_changes", [])
    last = changes[-1] if changes else None
    if isinstance(last, dict) and {k: v for k, v in last.items() if k != "at"} == entry:
        return
    from datetime import datetime, timezone
    at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    changes.append(dict({"at": at}, **entry))
    st.update(updates)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f, indent=2)
    os.replace(tmp, p)
    with open(os.path.join(run, "events.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps(dict({"at": at, "source": "knowledge.py", "event": kind, "detail": detail}, **updates)) + "\n")
    sys.stderr.write("note: %s; recorded, the run continues\n" % detail)


def verify(run):
    """The root a resumed run reads. A knowledge change is recorded, never refused: a changed manifest hash is recorded
    and the run reads the root as it is now; a recorded root that is gone moves to the root find() resolves now (fsd.py
    logs the move), recording any manifest-hash difference; nothing recorded adopts the root found now. Raises
    KnowledgeChanged only when the run's state.json cannot be read."""
    root, want = recorded(run)
    if not root or not want:
        try:
            now = knowledge_root.find()
        except knowledge_root.KnowledgeNotFound:
            return root
        have = knowledge_root.manifest_sha256(now)
        _record_change(run, "knowledge-recorded-late", "the run had no recorded knowledge root or manifest hash; "
                       "it now reads %s" % now, {ROOT_KEY: root or now, HASH_KEY: want or have})
        return root or now
    if not os.path.isdir(root):
        # K3: the same knowledge may have moved (a new install path); a different manifest is recorded, not refused.
        try:
            now = knowledge_root.find()
        except knowledge_root.KnowledgeNotFound:
            _record_change(run, "knowledge-root-missing", "the recorded knowledge root no longer exists (%s) and no "
                           "other knowledge root is found now" % root, {})
            return root
        have = knowledge_root.manifest_sha256(now)
        if have != want:
            _record_change(run, "knowledge-manifest-changed", "the recorded knowledge root no longer exists (%s); the root "
                           "found now (%s) has a different manifest hash (recorded %s, now %s)" % (root, now, want, have),
                           {HASH_KEY: have})
        return now
    have = knowledge_root.manifest_sha256(root)
    if have != want:
        _record_change(run, "knowledge-manifest-changed", "the knowledge manifest hash changed since the run started "
                       "(recorded %s, now %s) at %s" % (want, have, root), {HASH_KEY: have})
    return root


def root(run=None):
    """A run's recorded root once state.json exists, else the root find() resolves."""
    if run and os.path.isfile(os.path.join(run, "state.json")):
        r, _ = recorded(run)
        if r:
            return r
    return knowledge_root.find()


def path(name, run=None):
    return os.path.join(root(run), PATHS[name])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", default=None)
    a = ap.parse_args()
    r = root(a.run)
    print("root:", r)
    print("manifest_sha256:", knowledge_root.manifest_sha256(r))
    bad = 0
    for name in PATHS:
        p = path(name, a.run)
        ok = os.path.exists(p)
        bad += not ok
        print(f"  {name}: {p} {'ok' if ok else 'MISSING'}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
