#!/usr/bin/env python3
"""Set-map movers: an object state moved in by two or more actors needs an overlap link, a variant entry or an open
point about it, at the set-map stage (the set-map half of a3-current-state's state-has-one-mover, moved earlier).

Usage: check_set_map_movers.py --run RUN_ROOT --stage set-map

Reads stages/set-map/set-map.json. A state whose `moved_in_by` has two or more entries fails unless a link of kind
`overlap` or `variant_entry` has `state` equal to its id, or an open point's `about` names it. Exit 0 pass, 1 fail,
2 setup error (missing or unreadable file, not JSON, no `object_states`, a state with no `moved_in_by` list, `links` or
`open_points` not a list of objects, a state id or a link's kind or state that is not a string, an `about` that is
not a list of ids) or usage;
stdout is one JSON object on one line. Stdlib only.
"""
import argparse, json, os, sys

CHECK = "set-map-movers"
RULE = "set-map-state-two-movers"
STAGE = "set-map"
REL = os.path.join("stages", "set-map", "set-map.json")
COVERING = ("overlap", "variant_entry")


class SetupError(Exception):
    pass


def load(run):
    path = os.path.join(run, REL)
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
    except OSError as e:
        raise SetupError("cannot read %s: %s" % (REL, e.strerror or e))
    except ValueError as e:
        raise SetupError("%s is not valid JSON: %s" % (REL, e))
    if not isinstance(doc, dict) or not isinstance(doc.get("object_states"), list):
        raise SetupError("%s has no object_states list" % REL)
    for i, s in enumerate(doc["object_states"]):
        if not isinstance(s, dict) or not isinstance(s.get("moved_in_by"), list):
            sid = s.get("id") if isinstance(s, dict) else None
            raise SetupError("%s object_states[%d] (%s) has no moved_in_by list" % (REL, i, sid))
    for key in ("links", "open_points"):
        items = doc.get(key)
        if items is not None and not (isinstance(items, list) and all(isinstance(x, dict) for x in items)):
            raise SetupError("%s %s is not a list of objects" % (REL, key))
    for i, s in enumerate(doc["object_states"]):
        if not isinstance(s.get("id"), str):
            raise SetupError("%s object_states[%d] id is not a string" % (REL, i))
    for i, l in enumerate(doc.get("links") or []):
        if not all(l.get(f) is None or isinstance(l.get(f), str) for f in ("kind", "state")):
            raise SetupError("%s links[%d] kind or state is not a string" % (REL, i))
    for i, o in enumerate(doc.get("open_points") or []):
        about = o.get("about")
        if about is not None and not (isinstance(about, list) and all(isinstance(x, str) for x in about)):
            raise SetupError("%s open_points[%d] about is not a list of ids" % (REL, i))
    return doc


def findings(doc):
    covered = {l.get("state") for l in doc.get("links") or [] if isinstance(l, dict) and l.get("kind") in COVERING}
    about = {x for o in doc.get("open_points") or [] if isinstance(o, dict) for x in o.get("about") or []}
    out = []
    for i, s in enumerate(doc["object_states"]):
        movers = s["moved_in_by"]
        if len(movers) >= 2 and s.get("id") not in covered | about:
            out.append({"rule": RULE, "where": "/object_states/%d" % i, "state": s.get("id"), "moved_in_by": movers,
                        "detail": "%s claim %s; say how they relate: an overlap or variant_entry link with this state, "
                                  "or an open point about it" % (", ".join(map(str, movers)), json.dumps(s.get("id")))})
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(prog="check_set_map_movers.py")
    ap.add_argument("--run", required=True)
    ap.add_argument("--stage", required=True)
    a = ap.parse_args(argv)
    if a.stage != STAGE:
        ap.print_usage(sys.stderr)
        print("check_set_map_movers.py: error: --stage must be set-map, not %r" % a.stage, file=sys.stderr)
        return 2
    try:
        doc = load(a.run)
    except SetupError as e:
        print(json.dumps({"check": CHECK, "result": "error", "file": REL, "error": str(e)}))
        return 2
    try:
        found = findings(doc)
    except (TypeError, AttributeError, ValueError) as e:   # input of a shape load() did not foresee: never a repairable crash
        print(json.dumps({"check": CHECK, "result": "error", "file": REL, "error": "unexpected set map shape: %s" % e}))
        return 2
    print(json.dumps({"check": CHECK, "result": "fail" if found else "pass", "file": REL,
                      "states": len(doc["object_states"]), "findings": found}))
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
