"""The one lookup for the plugin's shared knowledge folder (<plugin>/knowledge).

Every skill that reads the reference library, checks, templates or stage folders finds them through find() and
nothing else; tests/knowledge/test_knowledge_lookup_guard.py fails the build when code outside knowledge/ builds the
path itself. The folder is the one that holds MANIFEST.json, tried in this order:

  1. the root a run recorded at its start (logs/driver.jsonl), so a resumed or re-run run reads the knowledge it began with;
  2. <skill base>/../../knowledge, the plugin this file ships in;
  3. $CLAUDE_PLUGIN_ROOT/knowledge;
  4. an installed copy of this plugin found under the plugin folders (newest first).

manifest_sha256() names the knowledge build a run used; Cowork runs show a blank plugin commit, so the run records this.
"""
import glob
import json
import os

KNOWLEDGE_REL = "knowledge"
MANIFEST = "MANIFEST.json"
PLUGIN_NAME = "within"
SKILL_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # <plugin>/skills/fast-mode-build
PLUGIN_FOLDERS = ("~/.claude/plugins", "~/Library/Application Support/Claude")


class KnowledgeNotFound(Exception):
    pass


def _is_root(path):
    return isinstance(path, str) and bool(path) and os.path.isfile(os.path.join(path, MANIFEST))


START_EVENTS = ("v4-start", "run-start")


def recorded_root(run_dir):
    """The knowledge root a run recorded in its start line, or None. Only a start line counts, the root must still hold
    a manifest, and when the line recorded a manifest hash the folder must still carry that hash (the same build)."""
    log = os.path.join(run_dir, "logs", "driver.jsonl") if run_dir else ""
    if not os.path.isfile(log):
        return None
    with open(log, encoding="utf-8", errors="replace") as f:
        for line in f:
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if not isinstance(rec, dict) or rec.get("event") not in START_EVENTS or "knowledge_root" not in rec:
                continue
            root, want = rec.get("knowledge_root"), rec.get("knowledge_manifest_sha256")
            if not _is_root(root):
                return None
            if want and manifest_sha256(root) != want:
                return None
            return root
    return None


# Where installed copies of a plugin sit below a plugin folder, at most this many folders down
# (e.g. ~/.claude/plugins/cache/<marketplace>/<plugin>/<version>/knowledge). Fixed-depth patterns: no recursion
# into unrelated trees and no symlink loops.
INSTALL_DEPTH = 6


def _installed_roots():
    """Knowledge folders of installed copies of this plugin, newest first."""
    found = set()
    for base in PLUGIN_FOLDERS:
        base = os.path.expanduser(base)
        if not os.path.isdir(base):
            continue
        for depth in range(1, INSTALL_DEPTH + 1):
            pattern = os.path.join(base, *(["*"] * depth), KNOWLEDGE_REL, MANIFEST)
            for manifest in glob.glob(pattern):
                root = os.path.dirname(manifest)
                plugin_json = os.path.join(os.path.dirname(root), ".claude-plugin", "plugin.json")
                try:
                    with open(plugin_json, encoding="utf-8") as f:
                        meta = json.load(f)
                except (OSError, ValueError):
                    continue
                if isinstance(meta, dict) and meta.get("name") == PLUGIN_NAME:
                    found.add(root)
    return sorted(found, key=os.path.getmtime, reverse=True)


def candidates(run_dir=None):
    """Every place find() looks, in order (the installed copies are searched only when the first three miss)."""
    plugin_env = os.environ.get("CLAUDE_PLUGIN_ROOT", "")
    return [recorded_root(run_dir),
            os.path.normpath(os.path.join(SKILL_BASE, "..", "..", KNOWLEDGE_REL)),
            os.path.join(plugin_env, KNOWLEDGE_REL) if plugin_env else None]


def find(run_dir=None, plugin=None):
    """The knowledge root. PLUGIN is an internal test hook: a stand-in plugin tree, or a knowledge folder itself."""
    if plugin:
        root = os.path.abspath(plugin)
        if os.path.isdir(os.path.join(root, KNOWLEDGE_REL)):
            return os.path.join(root, KNOWLEDGE_REL)
        if os.path.basename(root) == KNOWLEDGE_REL and os.path.isdir(root):
            return root
        raise KnowledgeNotFound("%r is not a plugin tree (no knowledge folder)" % (plugin,))
    tried = [c for c in candidates(run_dir) if c]
    for root in tried:
        if _is_root(root):
            return os.path.abspath(root)
    for root in _installed_roots():
        return root
    raise KnowledgeNotFound("the plugin's knowledge folder was not found (looked in %s and the installed plugin folders)"
                            % ", ".join(tried))


def no_script_check(run_dir=None):
    """assert_no_script from <knowledge>/checks/_no_script.py: pages the plugin writes carry no script (FORDEEN-87).

    The knowledge root find(run_dir) picks may be an older build a run recorded at its start, from before the check
    existed; that root is passed over for the plugin's own tree (this file's plugin), then $CLAUDE_PLUGIN_ROOT. It never
    passes a page unchecked: raises KnowledgeNotFound when no knowledge folder holds the check."""
    import importlib.util
    first = find(run_dir)
    tried = []
    for root in [first] + [c for c in candidates()[1:] if c]:
        path = os.path.join(root, "checks", "_no_script.py")
        tried.append(path)
        if os.path.isfile(path):
            break
    else:
        raise KnowledgeNotFound("the no-script check was not found (looked for %s)" % ", ".join(tried))
    spec = importlib.util.spec_from_file_location("_no_script", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.assert_no_script


def manifest_sha256(root):
    """The manifest hash of the knowledge folder at ROOT, or None when it has no readable manifest."""
    try:
        with open(os.path.join(root, MANIFEST), encoding="utf-8") as f:
            doc = json.load(f)
    except (OSError, ValueError, TypeError):
        return None
    return doc.get("manifest_sha256") if isinstance(doc, dict) else None
