#!/usr/bin/env python3
"""Render a validated workflow bundle as a stable band SVG with explicit graph bindings.

Usage: render_workflow.py INPUT.json OUTPUT.json OUTPUT.svg [--html OUTPUT.html]
Writes the SVG and a standalone offline HTML page (no script: the diagram scrolls in a frame,
hover detail is native SVG titles; decision tables, open items, notes). The output bundle pins both and its input. Python stdlib
only: no Node.js, no network, no model call. Nothing is refused for its shape; only the
hard failures of the workflow checks stop a render, before anything is written.

A re-render replaces the active outputs: each file is written to a temporary sibling first, any
previous render is copied into `render-archive/<output folder name>/<stem>-<timestamp>/` beside the output
folder (never inside it, so a stage folder keeps only its own files), then each
temporary file is moved over its target with os.replace (atomic on one file system).
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "checks"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from workflow_contract import FUTURE, coverage, graph_notes, preserved_issues, sha  # noqa: E402
from _workflow_bands import render_svg  # noqa: E402
from _diagram_html import document  # noqa: E402
from _no_script import assert_no_script  # noqa: E402

ARCHIVE_DIR = "render-archive"


def _write_temp(target, text):
    """Write text next to target under a temporary name; return the temporary path."""
    temp = target.with_name(".%s.%d.tmp" % (target.name, os.getpid()))
    with temp.open("w", encoding="utf-8") as f:
        f.write(text)
    return temp


def _archive_previous(targets):
    """Copy any existing outputs into one sibling archive folder; return that folder or None."""
    existing = [p for p in targets if p.exists()]
    if not existing:
        return None
    stamp = time.strftime("%Y%m%dT%H%M%S", time.gmtime())
    # Beside the output folder, never inside it: a stage folder keeps only its allowed files (run hygiene).
    out_dir = existing[0].parent
    base = out_dir.parent / ARCHIVE_DIR / out_dir.name
    folder = base / ("%s-%s" % (targets[0].stem, stamp))
    n = 1
    while folder.exists():
        n += 1
        folder = base / ("%s-%s-%d" % (targets[0].stem, stamp, n))
    folder.mkdir(parents=True)
    for p in existing:
        shutil.copy2(str(p), str(folder / p.name))
    return folder


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input")
    parser.add_argument("output")
    parser.add_argument("svg")
    parser.add_argument("--html", dest="html_path", help="Defaults to OUTPUT.svg with .html suffix")
    args = parser.parse_args()
    started = time.perf_counter()
    try:
        checked = coverage(args.input)
        preserved_issues(args.input)
        bundle = checked.bundle
        out_dir = Path(args.output).resolve().parent
        in_dir = Path(args.input).resolve().parent
        bundle["prior_workflow"] = {"path": os.path.relpath(Path(args.input).resolve(), out_dir), "sha256": sha(args.input)}
        html_path = Path(args.html_path) if args.html_path else Path(args.svg).with_suffix(".html")
        targets = [Path(args.output).resolve(), Path(args.svg).resolve(), html_path.resolve()]
        if len(set(targets)) != 3:
            raise ValueError("the bundle, SVG and HTML outputs must be three different paths")
        # Pins stay resolvable from the output bundle's own folder.
        for key in ("snapshot", "current_state"):
            if isinstance(bundle.get(key), dict) and bundle[key].get("path"):
                bundle[key]["path"] = os.path.relpath((in_dir / bundle[key]["path"]).resolve(), out_dir)
        variant = checked.variant
        diagram = bundle.get("diagram") if isinstance(bundle.get("diagram"), dict) else {}
        svg = render_svg(bundle["graph"], variant, diagram)
        html_doc = assert_no_script(document(svg, bundle, checked.current, svg_href=os.path.relpath(Path(args.svg).resolve(), html_path.resolve().parent)), "workflow diagram page")
        pin = bundle.get("current_state") if variant == FUTURE else None
        notes = graph_notes(bundle["graph"], variant, checked.current, pin, diagram)
        for target in targets:
            target.parent.mkdir(parents=True, exist_ok=True)
        out_path, svg_path = targets[0], targets[1]
        temps = {}
        try:
            temps[svg_path] = _write_temp(svg_path, svg)
            temps[html_path.resolve()] = _write_temp(html_path.resolve(), html_doc)
            bundle["presentation"] = "diagram"
            bundle["render"] = {"path": os.path.relpath(svg_path, out_dir), "sha256": sha(temps[svg_path])}
            bundle["render_html"] = {"path": os.path.relpath(html_path.resolve(), out_dir),
                                     "sha256": sha(temps[html_path.resolve()])}
            bundle["render_coverage"] = ([{"graph_id": n["id"], "render_id": "node-" + n["id"]} for n in bundle["graph"]["nodes"]] +
                                         [{"graph_id": e["id"], "render_id": "edge-" + e["id"]} for e in bundle["graph"]["edges"]] +
                                         [{"graph_id": t["id"], "render_id": "table-" + t["id"]}
                                          for t in bundle["graph"].get("decision_tables", [])])
            temps[out_path] = _write_temp(out_path, json.dumps(bundle, indent=2, ensure_ascii=False) + "\n")
            archived = _archive_previous(targets)
            # The bundle goes last so it never points at a render that is not yet in place.
            for target in (svg_path, html_path.resolve(), out_path):
                os.replace(str(temps.pop(target)), str(target))
        finally:
            for temp in temps.values():
                try:
                    temp.unlink()
                except OSError:
                    pass
        if archived:
            print("render: previous render archived in %s" % archived)
        for note in notes:
            print("NOTE render: %s: %s" % (note["id"] or "page", note["text"]))
        print(json.dumps({"svg": str(args.svg), "html": str(html_path), "layout": "bands-v3", "model_calls": 0,
                          "duration_ms": round((time.perf_counter() - started) * 1000, 3), "notes": len(notes),
                          "archived": str(archived) if archived else None}))
    except (ValueError, KeyError, TypeError, IndexError, OSError) as exc:
        parser.exit(1, "Render refused: %s\n" % exc)


if __name__ == "__main__":
    main()
