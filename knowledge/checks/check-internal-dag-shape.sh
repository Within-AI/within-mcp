#!/usr/bin/env python3
# This file is Python despite its .sh name: run it directly or as `python3 <this file>`; `bash <this file>` cannot run it.
"""Internal-map shape check: the internal-DAG stage's mechanical release gate.

Structurally validates an internal-DAG graph object (a `*.graph.json`, or the same object in YAML as an
`agents.yaml`-shape graph). Like the other factory checks, this is a re-verification against a lazy agent
(a structural proof that the graph is well formed and that every agent write or agent-held decision states
its authority), NOT a semantic judge of whether the decomposition is good. Deterministic and
writer-re-derivable: same file in, same verdict out, independent of cwd.

ONE RULE SET FOR EVERY GRAPH. The plugin never refuses to build an agent because of its shape: a script
with no sub-agents, a single agent using tools, several sub-agents with or without a coordinator, flows
that start independently, an agent with granted write authority. `agent.primitive_form` selects nothing
here (it is read only to print a note for `no-agent-work`, whose placement is the remit gate's call).

It FAILS (exit 1, one line per problem on stderr) only where the graph is broken, contradicts itself, or
leaves an agent write or decision without a stated reason:
  * ids: node ids unique, edge ids unique, interface ids unique, and no id shared between a node and an
    interface (edge targets would be ambiguous);
  * every link resolves: each edge's `from` and `to` name a node or an interface;
  * handoffs match any declared count: when `header.declared_handoff_count` is present it must be an
    integer equal to the number of `handoff` edges;
  * authority: every `write` edge (kind `write` or a write `access_kind`), every `write` interface, and
    every node stamped `authoritative: true` that is not a `review_seam` and not owned by a seam-kind
    sub-agent, must carry a non-empty `authority_grant` naming the ratified decision that grants it.
    Message: `AUTHORITY: <agent write | agent-held decision> without a stated authority_grant: <ids>`.
    A check that asks for a stated reason is not a refusal: a granted write passes, with a NOTE.
  * cooperating-agent fields (all four optional; validated only when present, so internal maps without
    them stay valid; whether they are used is the agent-architecture check's call, not this one's):
      - on a `kind: agent` sub-agent, `lane` must be the string `Agent` or `Agent: <name>`;
      - on a `kind: agent` sub-agent, `write_grants` must be a list of strings, each `WG-<n>`;
      - on a `kind: agent` sub-agent, `future_state_steps` must be a list of non-empty strings (the
        future-state node ids that sub-agent carries out);
      - on an interface with `direction: dispatch`, `target` must name an existing sub-agent id.
    Message: `COOPERATING: <sub-agent or interface id> <field> <what is wrong>`.

It prints NOTE lines (stdout, after PASS; never a failure) for: more than one root_agent; a root with no
dispatch edges; handoffs present but no declared count; an interface without `direction`; an
escalation gate without `condition`; an escalation gate without an `authoritative` stamp (treated as
routing only); `primitive_form: no-agent-work`; every granted write or decision.

Structure: `nodes` and `edges` must be present lists (`nodes` may be empty only when `edges` is empty).
`sub_agents` and `interfaces` may be absent or `n/a` (read as empty); present and not a list fails.
Optional cooperating-agent fields, checked only when present: a `kind: agent` sub-agent may carry `lane`
(`Agent` or `Agent: <name>`), `write_grants` (a list of `WG-<n>` strings) and `future_state_steps` (a list
of future-state node id strings); an interface with `direction: dispatch` may carry `target` (the receiving
sub-agent's id, which must exist in `sub_agents`).
Unknown fields and unknown top-level keys (including `reference_use`) are ignored.

Parsing: PyYAML is REQUIRED. yaml.safe_load parses both a `*.graph.json` (JSON is a subset of YAML) and a
YAML graph, so one parse path serves both serializations. If PyYAML is unavailable the check exits 2
(setup error) with a clear message; it never degrades silently.

Usage: check-internal-dag-shape.sh [--verbose] [--primitive-form FORM] GRAPH
       (`--primitive-form` is accepted and ignored, kept for callers.)
Exit: 0 pass (notes allowed); 1 shape failure; 2 setup error (PyYAML absent, missing file, bad usage).
"""
import os
import re
import sys

WRITE_TOKENS = {"write", "writes", "all writes"}
NA_TOKENS = {"n/a", "na", "none", ""}
LANE_RE = re.compile(r"^Agent(: \S.*)?$")
GRANT_RE = re.compile(r"WG-[0-9]+")  # used with fullmatch: "$" would accept a trailing newline
PASS_LINE = ("PASS: internal-DAG shape is valid (ids unique; every link resolves; handoffs match any declared "
             "count; every agent write or decision states its authority grant).")


def die(msg, code):
    sys.stderr.write(msg + "\n")
    sys.exit(code)


def _is_write(val):
    return isinstance(val, str) and val.strip().lower() in WRITE_TOKENS


def _is_na(val):
    if val is None:
        return True
    return isinstance(val, str) and val.strip().lower() in NA_TOKENS


def _grant(x):
    g = x.get("authority_grant")
    return g.strip() if isinstance(g, str) and g.strip() else None


def validate(doc):
    """Return (problems, notes)."""
    problems, notes = [], []
    if not isinstance(doc, dict):
        return ["top level is not a mapping (expected the internal-DAG graph object)"], notes

    nodes, edges = doc.get("nodes"), doc.get("edges")
    for name, v in (("nodes", nodes), ("edges", edges)):
        if not isinstance(v, list):
            problems.append("%r must be a present list" % name)
    lists = {}
    for name in ("sub_agents", "interfaces"):
        v = doc.get(name)
        if _is_na(v):
            lists[name] = []
        elif isinstance(v, list):
            lists[name] = v
        else:
            problems.append("%r must be a list when present (absent or 'n/a' means none)" % name)
            lists[name] = []
    if problems:
        return problems, notes
    if not nodes and edges:
        problems.append("'nodes' is empty but 'edges' is not")
        return problems, notes

    subs, ifaces = lists["sub_agents"], lists["interfaces"]
    nodes = [n for n in nodes if isinstance(n, dict)]
    edges = [e for e in edges if isinstance(e, dict)]
    ifaces = [i for i in ifaces if isinstance(i, dict)]
    subs = [s for s in subs if isinstance(s, dict)]
    header = doc.get("header") if isinstance(doc.get("header"), dict) else {}
    agent = doc.get("agent") if isinstance(doc.get("agent"), dict) else {}

    # ---- ids ----
    def dups(ids):
        seen, out = set(), []
        for i in ids:
            if i in seen and i not in out:
                out.append(i)
            seen.add(i)
        return out
    node_ids = [n.get("id") for n in nodes]
    edge_ids = [e.get("id") for e in edges]
    iface_ids = [i.get("id") for i in ifaces]
    for what, ids in (("node", node_ids), ("edge", edge_ids), ("interface", iface_ids)):
        if any(i is None or str(i) == "" for i in ids):
            problems.append("IDS: a %s has no id" % what)
        d = dups(ids)
        if d:
            problems.append("IDS: %s ids are not unique: %s" % (what, d))
    shared = [i for i in node_ids if i is not None and i in set(iface_ids)]
    if shared:
        problems.append("IDS: ids shared between a node and an interface: %s" % shared)

    # ---- every link resolves ----
    targets = set(i for i in node_ids if i is not None) | set(i for i in iface_ids if i is not None)
    dangling = [(e.get("id"), e.get("from"), e.get("to")) for e in edges
                if e.get("from") not in targets or e.get("to") not in targets]
    if dangling:
        problems.append("LINKS: edge(s) whose from/to is not a node or interface id: %s" % dangling)

    # ---- declared handoff count ----
    handoffs = [e for e in edges if e.get("kind") == "handoff"]
    declared = header.get("declared_handoff_count")
    if declared is not None:
        if isinstance(declared, bool) or not isinstance(declared, int):
            problems.append("HANDOFF-COUNT: header.declared_handoff_count must be an integer (got %r)" % (declared,))
        elif len(handoffs) != declared:
            problems.append("HANDOFF-COUNT mismatch: declared %d handoffs, graph has %d (%s)"
                            % (declared, len(handoffs), [e.get("id") for e in handoffs]))
    elif handoffs:
        notes.append("handoff count not declared (%d handoff edges)" % len(handoffs))

    # ---- authority: every agent write or agent-held decision states its grant ----
    seam_subs = {s.get("id") for s in subs if s.get("kind") == "seam" and s.get("id")}
    writes_missing, dec_missing = [], []
    for e in edges:
        if e.get("kind") == "write" or _is_write(e.get("access_kind")):
            g = _grant(e)
            if g:
                notes.append("agent write granted: %s (%s)" % (e.get("id"), g))
            else:
                writes_missing.append(e.get("id"))
    for i in ifaces:
        if i.get("kind") == "write" or _is_write(i.get("access_kind")):
            g = _grant(i)
            if g:
                notes.append("agent write granted: %s (%s)" % (i.get("id"), g))
            else:
                writes_missing.append(i.get("id"))
    for n in nodes:
        if n.get("authoritative") is True and n.get("type") != "review_seam" \
                and not (n.get("sub_agent") and n.get("sub_agent") in seam_subs):
            g = _grant(n)
            if g:
                notes.append("agent-held decision granted: %s (%s)" % (n.get("id"), g))
            else:
                dec_missing.append(n.get("id"))
    if writes_missing:
        problems.append("AUTHORITY: agent write without a stated authority_grant: %s" % ", ".join(str(x) for x in writes_missing))
    if dec_missing:
        problems.append("AUTHORITY: agent-held decision without a stated authority_grant: %s" % ", ".join(str(x) for x in dec_missing))

    # ---- cooperating-agent fields (optional; validated only when present) ----
    sub_ids = {str(s.get("id")) for s in subs if s.get("id") is not None}
    for s in subs:
        if s.get("kind") != "agent":
            continue
        sid = s.get("id")
        if "lane" in s:
            lane = s.get("lane")
            if not (isinstance(lane, str) and LANE_RE.match(lane.strip()) and lane == lane.strip()):
                problems.append("COOPERATING: sub-agent %s lane must be 'Agent' or 'Agent: <name>' (got %r)" % (sid, lane))
        for f in ("write_grants", "future_state_steps"):
            if f not in s:
                continue
            v = s.get(f)
            if not isinstance(v, list) or not all(isinstance(x, str) and x.strip() for x in v):
                problems.append("COOPERATING: sub-agent %s %s must be a list of id strings (got %r)" % (sid, f, v))
            elif f == "write_grants":
                bad = [x for x in v if not GRANT_RE.fullmatch(x)]
                if bad:
                    problems.append("COOPERATING: sub-agent %s write_grants entries must be WG-<n>: %s" % (sid, bad))
    for i in ifaces:
        if i.get("direction") == "dispatch" and "target" in i:
            t = i.get("target")
            if not isinstance(t, str) or t not in sub_ids:
                problems.append("COOPERATING: interface %s target names no sub-agent id (got %r)" % (i.get("id"), t))

    # ---- notes (rule 1 openings) ----
    roots = [n for n in nodes if n.get("type") == "root_agent"]
    if len(roots) > 1:
        notes.append("more than one root_agent: %s" % [n.get("id") for n in roots])
    for r in roots:
        if not any(e.get("kind") == "dispatch" and e.get("from") == r.get("id") for e in edges):
            notes.append("root_agent %s has no dispatch edges" % r.get("id"))
    for i in ifaces:
        if not i.get("direction"):
            notes.append("interface %s has no direction" % i.get("id"))
    for n in nodes:
        if n.get("type") == "escalation_gate":
            if not n.get("condition"):
                notes.append("escalation gate %s cites no condition" % n.get("id"))
            if "authoritative" not in n:
                notes.append("escalation gate %s: authority not stamped; treated as routing only" % n.get("id"))
    if agent.get("primitive_form") == "no-agent-work":
        notes.append("primitive_form says no-agent-work; placement is decided at the remit gate")
    return problems, notes


def main(argv):
    verbose = False
    rest = []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in ("--verbose", "-v"):
            verbose = True
        elif a == "--primitive-form":
            i += 1
            if i >= len(argv):
                die("--primitive-form requires a value", 2)
        else:
            rest.append(a)
        i += 1
    if len(rest) != 1:
        die("usage: check-internal-dag-shape.sh [--verbose] [--primitive-form FORM] GRAPH", 2)
    graph = rest[0]
    if not os.path.isfile(graph):
        die("Missing graph object: %s" % graph, 2)
    text = open(graph, "r", encoding="utf-8", errors="replace").read()
    try:
        import yaml
    except ImportError:
        die("PyYAML is required for the internal-DAG structural parse but is not importable. Install it "
            "(pip install pyyaml) and re-run; this check does not fall back to a line scan.", 2)
    try:
        doc = yaml.safe_load(text)
    except Exception as e:
        die("graph is not parseable (JSON or YAML): %s" % e, 1)
    if verbose:
        sys.stderr.write("parse engine: pyyaml safe_load (serves both *.graph.json and agents.yaml-shape)\n")
    problems, notes = validate(doc)
    if problems:
        die("internal-DAG shape check FAILED for %s:\n  - %s" % (graph, "\n  - ".join(problems)), 1)
    print(PASS_LINE)
    for n in notes:
        print("NOTE: %s" % n)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
