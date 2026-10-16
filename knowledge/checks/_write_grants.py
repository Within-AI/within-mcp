"""The write-grant rules, one copy for every skill that judges a Write grants table against a future state.

Future State Design (its writes-and-scope check, writes part) and Agent Design (its grants check, and its intake)
both call this module; neither holds a rule of its own for what is judged here. The rules:

  grant-step-missing    every grant an agent writes (Who writes `agent`) is carried by a future-state step (a node
                        naming it in `write_grant` or `write_grants`; a person's write needs no grant field on the
                        person's step), and every id in a grant's Steps cell is a future-state node or sits in some node's
                        `future.from` (an empty Steps cell or item fails too). When a single-process future state (no
                        `lanes` list) carries no `future.from` at all (an older run), an id that is not a node cannot
                        be resolved; that finding carries `no_lineage: true` so a caller can flag it instead of failing.
  write-grant-unknown   every grant a step names is a row of the table (fail-safe: the recorded write is never dropped).
  grant-system-undrawn  the grant's System is drawn by a future-state lane or node. An external lane's label draws
                        the system it names on an exact match (case aside, outer spaces trimmed).
  external-label-ambiguous an external lane's label matches the names of two or more workspaces: the label still
                        draws its name, the first workspace by id is read, and the reading is recorded (never
                        failing).
  write-before-confirm  no agent mechanical write into a system of record (System of record `yes` or `unclear`)
                        whose confirmation the map keeps (0503 R-writes: the grant's Steps include a person step,
                        the split partner of the agent step, or the map's `## Splits` names the grant or a step;
                        values copied unchanged from a named source need none) before that person confirmation,
                        on every path: its own confirmations are found walking back
                        through agent-lane nodes (tier 1), and only when that finds none through any node (tier 2);
                        the write fails when it has none, or is still reachable from a start once they are removed.
                        A step's grants include every grant whose Steps resolve to it (through `future.from`).
  write-ungranted       an agent step writes into a system (its `writes` or `write_grant(s)`; never its `systems`
                        read surfaces or a drawn arrow) and no grant it carries covers that system.
  exception-scope       the same, on a step whose only grants carry an exception reason.
  grant-fields-disagree a node names both `write_grant` and `write_grants` and they hold different grants (the
                        reader takes the union; the disagreement is reported so the stage can make them one).
  grant-reading-invalid a recorded reading (below) that names no row, no Write grants column, or carries no reason.

Both lane shapes are read: a lanes table (`graph.lanes`, each with a `band`; multi-process A3 runs and some
single-process runs), and the lane named on each node (`lane`: `Agent` or `Agent: <name>` is the AI and agents band,
`Analyst` the people band, `System` the system-of-record band, `External` the external band), used when the graph has
no `lanes` list (the same boundary as Future State Design's run-hygiene check).

Recorded readings. A stage that reads the table differently from its cells, because the map's own text or the
evidence says otherwise, records the reading it took instead of editing the map. A reading is an object:

  {"grant": "WG-<n>", "column": "Steps", "table_says": "<the cell as the table holds it>",
   "read_as": ["f-enter"],                      # Steps column: the future-state step ids the grant is read as covering
   "rules": ["write-before-confirm"],           # optional: the findings on this grant the reading answers
   "reason": "<why: the map's text or evidence that supports the reading>",
   "status": "resolved"}                        # optional: "resolved" (default) or "unresolved"

Future State Design records them in the future state's top-level `grant_readings` list; Agent Design's stage agents
in agent.yaml's `upstream_readings`. A Steps reading whose `read_as` is a list replaces the cell when steps are
resolved, and the rules then run on the reading: what they still find on those steps is a real finding. Any other
reading (no `read_as` list) answers the findings on its grant whose rule it names in `rules`: they are recorded, not
failing. Nothing here edits any file. Stdlib only; Python 3.9-safe.

Agents judge, then continue (0494). Cells are read structurally: case, punctuation, articles and column order aside
(columns by header). Where input is ambiguous but readable the Resolver takes one reading by a structural tie-break and
records it in `notes` (a node drawn twice: the first drawing; a grant given different rows: the row whose Steps names
the step, else the first; a reference matching several things: see Resolver.resolve). A grant whose row gives no
readable kind leaves the field's own kind to decide. Every consumer (supply, parity, boundaries, reshape) uses this
one reading. Kept as findings: a missing or unparseable table, a missing file, a grant id no row holds.
"""
import re
import _agent_steps
from collections import deque

import _md_blocks  # noqa: E402  the caller puts this folder on sys.path; fenced blocks and comments never count

HEADER = ["Grant", "Steps", "System", "System of record", "Key control", "Write kind", "Who writes", "Route",
          "Exception reason", "Next rung", "Step-up evidence", "Access", "Recipient"]
GRANT_ID = re.compile(r"^WG-[1-9][0-9]*$")
WRITE_MARKERS = ("write", "write_interaction", "update", "post")
READ_ACCESS = "read_access"   # the one agent-to-system edge kind that is a read (exact value)
CONFIRM_TYPES = ("analyst_reconciliation", "decision", "analyst_input")
CONFIRM_WORDS = re.compile(r"\b(confirm|approve|review|decide|verif)", re.I)
PERSON_SYSTEM = "the person"
KIND_UNRESOLVED = "unresolved"   # a grant whose metadata is present but gives no write kind: direction unknown
UNNAMED = "(no system named)"
NODE_LANE_BANDS = {"agent": "ai_and_agents", "analyst": "people", "system": "system_of_record",
                   "external": "external"}
READING_STATUSES = ("resolved", "unresolved")
RULES = ("grant-step-missing", "write-grant-unknown", "grant-system-undrawn", "write-before-confirm",
         "write-ungranted", "exception-scope", "grant-fields-disagree", "grant-reading-invalid",
         "external-label-ambiguous")


class TableError(Exception):
    """The Write grants table is present but its header or a row has the wrong number of cells."""


class ShapeError(Exception):
    """A future state parses but a collection the rules read has the wrong shape."""


def norm(s):
    return " ".join(str(s).split()).lower()


def system_key(s):
    """A system's identity key: case, spacing and separators (`-`, `_`, `.`, `/`) aside, so `Order-ledger`, the slug
    a derived boundary takes, and `Order ledger`, the name a grant gives, are one system."""
    return " ".join(re.sub(r"[-_./]+", " ", str(s)).split()).lower()


def system_name_from_index(name, index):
    """Exact names precede separator variants; an ambiguous variant grants no new identity."""
    if not isinstance(name, str):
        return None
    hits = index.get(norm(name))
    if hits is None:
        hits = set(s for key, values in index.items() if system_key(key) == system_key(name) for s in values)
    return sorted(hits)[0] if hits and len(hits) == 1 else None


def workspace_names(workspace):
    """Display names when supplied; otherwise the workspace's structural identity."""
    names = [workspace[k] for k in ("name", "short_label")
             if isinstance(workspace.get(k), str) and workspace[k].strip()]
    return names or [workspace["id"]]


def system_index(doc, nodes=None):
    """One alias index for graph checks and grant resolution, including ID-only workspaces."""
    graph = doc.get("graph", doc)
    index = {}
    def add(alias, name):
        if isinstance(alias, str) and alias.strip():
            index.setdefault(norm(alias), set()).add(name)
    workspaces = graph.get("workspaces")
    if workspaces is None:
        workspaces = doc.get("workspaces")
    for workspace in workspaces if isinstance(workspaces, list) else []:
        if isinstance(workspace, dict) and isinstance(workspace.get("id"), str):
            names = workspace_names(workspace)
            for alias in [workspace["id"]] + names:
                add(alias, names[0])
    for lane in graph.get("lanes") if isinstance(graph.get("lanes"), list) else []:
        if isinstance(lane, dict) and lane.get("band") in ("system_of_record", "external"):
            add(lane.get("label"), lane.get("label"))
    nodes = graph.get("nodes") if nodes is None else nodes
    for node in nodes if isinstance(nodes, list) else []:
        if isinstance(node, dict) and node.get("type") == "system_surface":
            title = node.get("title")
            name = title if isinstance(title, str) and title.strip() else node.get("id")
            if isinstance(name, str):
                add(node.get("id"), name)
                add(name, name)
    return index


def cell(s):
    return s.strip().strip("`").strip()


def split_row(line):
    """Raw cells of a pipe-table row, outer pipes dropped. An escaped pipe (`\\|`) and a pipe inside inline code are
    cell text, not cell borders; an escaped pipe reads `|`."""
    s = re.sub(r"`[^`]*`", lambda m: m.group(0).replace("|", "\0"), line.strip().replace("\\|", "\0"))
    return [c.replace("\0", "|") for c in s.strip("|").split("|")]


def read_table(text):
    """Rows (dicts keyed by HEADER) of the first `### Write grants` table outside fenced blocks and comments, or None
    when the section is absent. Columns are read by their header (any order; a known column left out reads empty;
    an extra column is kept under its own name). Raises TableError when the header names no Grant column or a row's
    cell count differs from the header's (wrong-shaped input, recorded by the caller)."""
    lines = text.splitlines()
    masked = _md_blocks.mask(lines)
    lines = ["" if masked[i] else ln for i, ln in enumerate(lines)]
    start = next((i for i, ln in enumerate(lines) if re.match(r"^\s{0,3}### Write grants\s*$", ln)), None)
    if start is None:
        return None
    rows = []
    for ln in lines[start + 1:]:
        if re.match(r"^\s{0,3}#{1,3} ", ln):
            break
        ln = ln.strip()
        if not ln.startswith("|"):
            if rows:
                break
            continue
        cells = [cell(c) for c in split_row(ln)]
        if set("".join(cells)) <= set("-: "):
            continue
        rows.append(cells)
    if not rows:
        raise TableError("Write grants section holds no table")
    cols = [_HEADER_KEYS.get(header_key(c), c) for c in rows[0]]
    if "Grant" not in cols:
        raise TableError("Write grants header names no Grant column")
    out = []
    for r in rows[1:]:
        if len(r) != len(cols):
            raise TableError("Write grants row has %d cells, expected %d" % (len(r), len(cols)))
        row = dict((h, "") for h in HEADER)
        # columns are read by their header, in any order; a known column the table leaves out reads empty
        row.update(zip(cols, r))
        out.append(row)
    return out


def header_key(s):
    """A column header's identity: case, spacing, backticks and separators aside."""
    return system_key(str(s).strip("`"))


_HEADER_KEYS = dict((header_key(h), h) for h in HEADER)


def steps_refs(steps_cell):
    """The ids of a Steps cell, comma-separated, each stripped of spaces and its own backticks ("" for an empty item)."""
    return [cell(s) for s in steps_cell.split(",")]


def _as_list(v):
    if v is None:
        return []
    if isinstance(v, str):
        return [v]
    if isinstance(v, list):
        return [x for x in v if isinstance(x, str)]
    return []


def _entries(v):
    """A node's `writes` as a list of entries: a string is one entry; any other non-list value has none."""
    if isinstance(v, (str, dict)):
        return [v]
    return [x for x in v if isinstance(x, (str, dict))] if isinstance(v, list) else []


def grant_ids(n):
    """The grants a node names: `write_grant` and `write_grants`, each a string or a list of strings, in order."""
    out = []
    for key in ("write_grant", "write_grants"):
        for g in _as_list(n.get(key)):
            if g not in out:
                out.append(g)
    return out


def write_system(w):
    """The system a node's `writes` entry names: a bare system name, or the text before the first ":" of a
    "System: what is written" entry. A dict entry with no `system` names one only in that form of its `record` or
    `work_object`; a plain `{"record": ...}` says what is written, not where, so None."""
    if isinstance(w, dict):
        s = w.get("system")
        if isinstance(s, str) and s.strip():
            w = s
        else:
            text = w.get("record") or w.get("work_object")
            if not isinstance(text, str) or ":" not in text:
                return None
            w = text
    return str(w).split(":", 1)[0].strip()


def qualify(pid, ref):
    return ref if not pid or ":" in ref else "%s:%s" % (pid, ref)


def problem(rule, where, detail, grants=(), **extra):
    p = {"rule": rule, "where": where, "detail": detail, "grants": list(grants)}
    p.update(extra)
    return p


# ---- the future state ----

class SourceGraph(dict):
    """A graph plus its file identity, without adding metadata to the document's fields."""


def with_source(doc, key):
    if not isinstance(doc, dict):
        return doc
    result = SourceGraph(doc)
    result.source_key = key
    return result


class Graph(object):
    """One process's future state: a whole single-process graph, or this process's nodes of a set's shared graph
    (in_set). pid is the process id used to qualify step ids ("<pid>:<node>"); None leaves ids bare."""

    def __init__(self, pid, doc, in_set=False):
        self.document = doc
        g = doc.get("graph", doc) if isinstance(doc, dict) else None
        if not isinstance(g, dict):
            raise ShapeError("future state of %s: graph is not an object" % pid)
        for key in ("nodes", "edges", "lanes"):
            v = g.get(key, [])
            if v is None:
                v = []
            if not isinstance(v, list) or not all(isinstance(x, dict) for x in v):
                raise ShapeError("future state of %s: %s is not a list of objects" % (pid, key))
        ws = g.get("workspaces") if g.get("workspaces") is not None else doc.get("workspaces")
        if ws is not None and not isinstance(ws, list):
            raise ShapeError("future state of %s: workspaces is not a list" % pid)
        self.pid = pid
        self.has_lanes = g.get("lanes") is not None   # a lanes list (even empty) is the lanes-table shape
        self.lanes = dict((l.get("id"), l) for l in g.get("lanes") or [])
        self.workspaces = {}
        self.ws_names = {}
        for w in ws or []:
            if isinstance(w, dict) and isinstance(w.get("id"), str):
                names = workspace_names(w)
                if names:
                    self.workspaces[w["id"]] = names[0]
                self.ws_names[w["id"]] = names
        self.nodes = {}
        for n in g.get("nodes") or []:
            if not isinstance(n.get("id"), str):
                raise ShapeError("future state of %s: a node has no string id" % pid)
            if not in_set or n.get("process_id") in (pid, "set", None):
                self.nodes[n["id"]] = n
        self.edges = list(g.get("edges") or [])
        if len(self.nodes) < len(g.get("nodes") or []):
            self.edges = [e for e in self.edges if e.get("source") in self.nodes and e.get("target") in self.nodes]
        self.adj, self.preds = {}, {}
        for e in self.edges:
            self.adj.setdefault(e.get("source"), []).append(e.get("target"))
            self.preds.setdefault(e.get("target"), []).append(e.get("source"))
        # external lane labels: an exact (case aside, trimmed) name; one matching two workspaces is a finding
        self.label_draws, self.label_findings = set(), []
        for lane in self.lanes.values():
            label = lane.get("label")
            if lane.get("band") != "external" or not isinstance(label, str) or not label.strip():
                continue
            key = label.strip().lower()
            # counted by workspace id: two distinct ids sharing one name are two systems, never one
            hits = sorted(wid for wid, names in self.ws_names.items()
                          if names and any(x.strip().lower() == key for x in names))
            if len(hits) > 1:
                # the label still draws the system it names; which workspace it is stays a recorded reading
                self.label_findings.append(problem(
                    "external-label-ambiguous", "lane %s" % lane.get("id"),
                    "external lane label %r matches %d systems (%s); read as %s, the first by id" % (
                        label.strip(), len(hits), ", ".join("%s (%s)" % (self.ws_names[w][0], w) for w in hits),
                        hits[0]),
                    candidates=hits, read_as=hits[0]))
            self.label_draws.add(key)
        self.from_index = {}
        for nid, n in self.nodes.items():
            fut = n.get("future")
            for c in _as_list(fut.get("from")) if isinstance(fut, dict) else []:
                self.from_index.setdefault(c, []).append(nid)

    def has_lineage(self):
        """True when any node records `future.from` as a list, even an empty one (every step new is lineage too)."""
        return any(isinstance(n.get("future"), dict) and isinstance(n["future"].get("from"), list)
                   for n in self.nodes.values())

    def system_name(self, s):
        return system_name_from_index(s, system_index(self.document, list(self.nodes.values()))) or s

    def band(self, n):
        if self.has_lanes:
            return (self.lanes.get(n.get("lane_id")) or {}).get("band")
        name = norm(n.get("lane") or "")
        return NODE_LANE_BANDS.get(name.split(":", 1)[0].strip()) if name else None

    def is_agent(self, n):
        return bool(n.get("agent")) or self.band(n) == "ai_and_agents"

    def is_person(self, n):
        return self.band(n) == "people" and n.get("type") != "system_surface"

    def record_system(self, n):
        """The system a system-of-record node stands for, else None: a `system_surface` node's own title, else its
        lane's system (or label)."""
        if self.band(n) != "system_of_record":
            return None
        if n.get("type") == "system_surface" and n.get("title"):
            return n["title"]
        lane = self.lanes.get(n.get("lane_id")) or {}
        return self.system_name(lane.get("system")) or lane.get("label") or n.get("title")

    def drawn(self):
        out = set()
        for lane in self.lanes.values():
            if lane.get("band") == "system_of_record":
                out.update(norm(self.system_name(lane[k])) for k in ("label", "system") if lane.get(k))
        out.update(self.label_draws)
        for n in self.nodes.values():
            for s in n.get("systems") or []:
                if isinstance(s, str):
                    out.add(norm(s))
                    out.update(norm(x) for x in self.ws_names.get(s, []))
            if n.get("type") == "system_surface" and n.get("title"):
                out.add(norm(n["title"]))
            if n.get("agent"):
                out.add(norm(n["agent"]))
            if self.is_person(n):
                out.add(PERSON_SYSTEM)
        return out

    def starts(self):
        s = [i for i, n in self.nodes.items() if n.get("type") == "start_event"]
        return s or [i for i in self.nodes if not self.preds.get(i)]

    def own_confirmations(self, write):
        """The person steps that confirm this write's values: the one rule, `own_confirmations` below."""
        return own_confirmations(write, self.nodes, self.preds, self.is_agent, self.is_person)

    def reachable_avoiding(self, avoid):
        seen, queue = set(), deque(x for x in self.starts() if x not in avoid)
        while queue:
            x = queue.popleft()
            if x in seen:
                continue
            seen.add(x)
            queue.extend(y for y in self.adj.get(x, []) if y not in avoid and y not in seen)
        return seen

    def resolve(self, ref):
        """The node ids a Steps id stands for in this graph: the node itself, else the nodes whose future.from holds
        it. A qualified id (`<pid>:<node>`) resolves here only when its process is this graph's."""
        if ":" in ref and ref not in self.nodes and ref not in self.from_index:
            p, local = ref.split(":", 1)
            if self.pid and p != self.pid:
                return []
            ref = local
        if ref in self.nodes:
            return [ref]
        return list(self.from_index.get(ref, []))


# ---- the person-confirmation rule (the one copy; Future State Design's reshape check imports it) ----

def own_confirmations(write, nodes, preds, is_agent, is_person):
    """The person steps that confirm a write's values. A confirming step is a person step (not a start or end event)
    of a confirming type, or whose title says confirm, approve, review, decide or verify. Tier 1 walks back from the
    write through agent-lane nodes only (chained writes included); only when that finds none, tier 2 walks back
    through any node. A start event is never passed. Returns a set of node ids (empty when there is none)."""
    def confirming(x):
        n = nodes[x]
        return is_person(n) and n.get("type") not in ("start_event", "end_event") and (
            n.get("type") in CONFIRM_TYPES or bool(CONFIRM_WORDS.search(n.get("title") or "")))

    for passable in (lambda x: is_agent(nodes[x]), lambda x: True):
        found, seen, queue = set(), {write}, deque(preds.get(write, []))
        while queue:
            x = queue.popleft()
            if x in seen or x not in nodes:
                continue
            seen.add(x)
            if confirming(x):
                found.add(x)
            elif passable(x) and nodes[x].get("type") != "start_event":
                queue.extend(preds.get(x, []))
        if found:
            return found
    return set()


# ---- typed identities and the direction data flows ----

PROCESS, STEP, GRANT, SYSTEM, RECORD = "process", "step", "grant", "system", "record"
AMBIGUOUS, UNRESOLVED = "ambiguous", "unresolved"
TO_AGENT, FROM_AGENT, UNKNOWN = "to_agent", "from_agent", "unknown"
INBOUND_FIELDS = ("read", "inbound_control", "back_from_person")
OUTBOUND_FIELDS = ("write", "person_write", "draft", "outbound_handoff", "dispatch")

# Each write kind: the direction its data flows, the routes that can carry it (the destination side types and, for
# each, the field directions allowed), and what satisfies grant parity. Every kind also needs the field keyed on the
# grant (the field's `grant`, or `write_grant(s)` on the agent step the field leaves from), the agent as the source,
# and the boundary reaching the grant's own destination (its system, the person, the person step that writes, or the
# agent's own store); the gate checks those, this table says which field kinds count.
WRITE_KINDS = {
    "write": {"direction": FROM_AGENT, "routes": ((("system", "external"), ("write",)),),
              "satisfied_by": "a write field on a boundary from the agent to the grant's system"},
    "draft": {"direction": FROM_AGENT, "routes": ((("person",), ("draft", "outbound_handoff")),
                                                  (("system", "external"), ("draft",))),
              "satisfied_by": "a draft field to the grant's destination (a person or a system)"},
    "person_write": {"direction": FROM_AGENT, "routes": ((("person",), ("person_write", "outbound_handoff")),),
                     "satisfied_by": "a field the agent supplies to the person step that writes"},
    "own_file": {"direction": FROM_AGENT, "routes": ((("system", "agent"), ("write", "draft")),),
                 "satisfied_by": "a field to the agent's own store the grant names"},
}


def identity(type_, id_=None, process=None, system=None, source_ref=None, candidates=None):
    return {"type": type_, "id": id_, "process": process, "system": system, "source_ref": source_ref,
            "candidates": list(candidates or [])}


ARTICLES = ("the", "a", "an")


def words(s):
    """A cell's words: case, punctuation, separators and articles aside (`The Agent` and `agent` are one word list;
    `hand-off for review` reads `hand off for review`)."""
    return [w for w in re.sub(r"[^a-z0-9]+", " ", str(s or "").lower()).split() if w not in ARTICLES]


def is_agent_writer(who, agent_tags=()):
    """True when a Who writes cell names the agent: the word `agent`, or one of the agent's own tags."""
    w = words(who)
    return "agent" in w or (bool(w) and w in [words(t) for t in agent_tags])


CANONICAL_KINDS = {"own file": "own_file", "own store": "own_file", "draft": "draft", "handoff": "draft"}


def _names(values):
    return [words(v) for v in values if words(v)]


def grant_kind_how(row, agent_tags=(), person_names=(), system_names=()):
    """(write kind or None, how it was read) for one grant row. Each cell is first resolved to something the run
    draws (case, punctuation and articles aside):
      Who writes naming one of the agent's tags (or the table's `agent`) is the agent; naming a drawn person step,
      person lane or role (or the table's `person`) is a person write;
      System naming a drawn person step or person lane (or the table's `the person`) is a draft to the person;
      System naming one of the agent's own tags is the agent's own store;
      Write kind `own file` / `own store` / `draft` / `handoff` are the table's own values.
    `how` is "structural" when every deciding cell resolved that way. A cell that resolves to nothing falls back to
    its words (`agent`, `person`, a kind starting `own`, `draft`, `hand off`; a Who writes naming someone else is a
    person) and `how` is "wording", which the caller records as a reading note. None when no cell says who writes
    or what kind: the field's own kind then decides."""
    if not isinstance(row, dict):
        return None, "unreadable"
    tags, persons, systems = _names(agent_tags), _names(person_names), _names(system_names)
    who_w, kind_w, sys_w = words(row.get("Who writes")), words(row.get("Write kind")), words(row.get("System"))
    how = "structural"
    # who writes
    if who_w in tags or who_w == ["agent"]:
        agent = True
    elif who_w in persons or who_w == ["person"]:
        return "person_write", how
    elif who_w:
        agent = "agent" in who_w
        how = "wording"
        if not agent:
            return "person_write", how   # names someone other than the agent: a person's role
    else:
        agent = False
    # what kind, then where
    canon = CANONICAL_KINDS.get(" ".join(kind_w)) or CANONICAL_KINDS.get("".join(kind_w))
    if canon == "own_file":
        return canon, how
    if sys_w and (sys_w in persons or sys_w == ["person"]):
        return "draft", how
    if canon == "draft":
        return canon, how
    if sys_w and sys_w in tags:
        return "own_file", how
    if kind_w[:1] == ["own"]:
        return "own_file", "wording"
    # the word `person` in System counts only when the cell names no system the run draws
    if "draft" in kind_w or "handoff" in "".join(kind_w) or ("person" in sys_w and sys_w not in systems):
        return "draft", "wording"
    if agent:
        return "write", how
    return None, "unreadable"


def grant_kind(row, agent_tags=(), person_names=(), system_names=()):
    """The write kind of one grant (see grant_kind_how); None when no cell says who writes or what kind."""
    return grant_kind_how(row, agent_tags, person_names, system_names)[0]


def reading_note(code, where, detail, **extra):
    """One recorded reading: what was ambiguous, which reading was taken and why. A note never fails a check."""
    n = {"code": code, "where": where, "detail": detail, "note": True}
    n.update(extra)
    return n


class Resolver(object):
    """One resolver for every reference a write check meets: a grant id, a workspace id or name, a system node id or
    title, a written record, a step (bare or `<process>:<node>`), or a process id. Built from the run's graphs
    [(process id, future-state doc)] and its Write grants rows (each a dict with at least Grant and System)."""

    def __init__(self, graphs, rows=(), proposed=None):
        self.processes = []
        self.grants = {}
        self.conflicts = {}   # grant id -> [rows] when the tables give one grant different rows (first is read)
        self.findings = []    # malformed collections, recorded
        self.notes = []       # the readings taken where input was ambiguous but readable (never failing)
        self.steps = {}       # bare node id -> [(process, node)]
        self.nodes = {}       # (process, node id) -> node
        self.systems = {}     # normalised name or id -> set of system names
        self.records = {}     # normalised record text -> [(process, node id)]
        self.node_conflicts = {}  # (process, node id) -> [nodes] when one id is drawn twice with different content
        self.span_home = {}   # (spanned process, node id) -> (home process, node id) for a spanning step's alias
        self.agent_tags = set()
        self.person_steps = set()  # (process, node id) of steps a person does: a people-band lane, no agent tag
        self.person_names = set()  # what a cell may name for a person: person step ids and titles, people lanes
        def objs(v, what, pid):
            if v is None:
                return []
            if not isinstance(v, list):
                self.findings.append({"code": "shape-invalid", "where": "%s %s" % (pid, what),
                                      "detail": "%s is not a list; it is read as empty" % what})
                return []
            return [x for x in v if isinstance(x, dict)]

        for item in graphs or ():
            pid, doc = item if isinstance(item, (list, tuple)) and len(item) == 2 else (None, None)
            if pid is not None and pid not in self.processes:
                self.processes.append(pid)
            g = doc.get("graph", doc) if isinstance(doc, dict) else None
            if not isinstance(g, dict):
                self.findings.append({"code": "shape-invalid", "where": "%s future state" % pid,
                                      "detail": "the future state is not an object with a graph; it is skipped"})
                continue
            ws = g.get("workspaces") if g.get("workspaces") is not None else (doc.get("workspaces")
                                                                              if isinstance(doc, dict) else None)
            objs(ws, "workspaces", pid)  # retain the reading note for a malformed collection
            for key, names in system_index(doc).items():
                self.systems.setdefault(key, set()).update(names)
            for lane in objs(g.get("lanes"), "lanes", pid):
                if lane.get("band") == "people":
                    self.person_names.update(x for x in (lane.get("id"), lane.get("label"), lane.get("lane"))
                                             if isinstance(x, str) and x.strip())
            has_lanes = g.get("lanes") is not None
            lane_bands = dict((l.get("id"), l.get("band")) for l in g.get("lanes") or [] if isinstance(l, dict)) \
                if isinstance(g.get("lanes"), list) else {}
            for n in objs(g.get("nodes"), "nodes", pid):
                if not isinstance(n.get("id"), str):
                    continue
                if has_lanes:
                    band = lane_bands.get(n.get("lane_id"))
                else:
                    lane = norm(n.get("lane") or "") if isinstance(n.get("lane"), str) else ""
                    band = NODE_LANE_BANDS.get(lane.split(":", 1)[0].strip()) if lane else None
                is_person = band == "people" and not n.get("agent") and n.get("type") != "system_surface"
                # Step placement, spans and collisions are read once below by the ownership reader.
                if is_person:
                    self.person_names.update(x for x in (n["id"], n.get("title"))
                                             if isinstance(x, str) and x.strip())
                if isinstance(n.get("agent"), str) and n["agent"].strip():
                    self.agent_tags.add(n["agent"].strip())
        valid = [(pid, getattr(doc, "source_key", _agent_steps.graph_key(doc)), doc) for pid, doc in graphs or ()
                 if isinstance(doc, dict) and isinstance(doc.get("graph", doc), dict)
                 and isinstance(doc.get("graph", doc).get("nodes", []), list)]
        ownership = _agent_steps.run_ownership(valid)
        self.findings.extend(ownership.findings)
        self.notes.extend(ownership.notes)
        self.nodes = {tuple(ref.split(":", 1)): n for ref, n in ownership.step_nodes.items()}
        self.person_steps = {tuple(ref.split(":", 1)) for ref in ownership.person_refs}
        self.person_names.update(ownership.person_refs)
        self.node_conflicts = {tuple(ref.split(":", 1)): nodes
                               for ref, nodes in ownership.node_candidates.items()}
        for (place, nid), n in self.nodes.items():
            # Records use the same accepted placement (including legitimate spans) as steps.
            for write in _entries(n.get("writes")):
                text = write.get("record") or write.get("work_object") if isinstance(write, dict) else write
                if isinstance(text, str) and text.strip():
                    entries = self.records.setdefault(norm(text), [])
                    if (place, nid) not in entries:
                        entries.append((place, nid))
            own = n.get("process_id")
            if own and own != place and (own, nid) in self.nodes and self.nodes[(own, nid)] == n:
                self.span_home[(place, nid)] = (own, nid)
            else:
                self.steps.setdefault(nid, []).append((place, nid))
        for r in rows or ():
            if not isinstance(r, dict) or not isinstance(r.get("Grant"), str):
                self.findings.append({"code": "shape-invalid", "where": "Write grants",
                                      "detail": "a row is not an object with a Grant id; it is skipped"})
                continue
            gid = r["Grant"]
            if gid in self.conflicts:
                self.conflicts[gid].append(dict(r))
                for f in self.notes:
                    if f.get("code") == "grant-reading" and f.get("where") == gid:
                        f["candidates"] = [dict(x) for x in self.conflicts[gid]]
            elif gid in self.grants and any(norm(self.grants[gid].get(k) or "") != norm(r.get(k) or "")
                                            for k in set(self.grants[gid]) | set(r)):
                # the first row is read; a step looks up the row whose Steps cell names it (grant_row)
                self.conflicts[gid] = [self.grants[gid], dict(r)]
                self.notes.append(reading_note(
                    "grant-reading", gid, "the Write grants tables give %s different rows; each step reads the row "
                    "whose Steps names it; equally specific rows retain a system-of-record control, else the first row" % gid,
                    candidates=[dict(x) for x in self.conflicts[gid]]))
            else:
                self.grants.setdefault(gid, dict(r))
        for gid, system in (proposed or {}).items():
            if isinstance(gid, str) and gid not in self.grants and gid not in self.conflicts and \
                    isinstance(system, str):
                self.grants[gid] = {"Grant": gid, "System": system, "Who writes": "agent"}

    def system_of(self, name):
        """Exact identity first; separator variants only when they name one drawn system."""
        return system_name_from_index(name, self.systems)

    def grant_row(self, gid, step=None):
        """Prefer the step's lineage row; retain a confirmation control when equally specific rows disagree.

        This selects an existing row, never invents authority or stops readable input. All alternatives are
        kept in the grant-reading note; unrelated rows do not override a more specific lineage match.
        """
        rows = self.conflicts.get(gid)
        if not rows:
            return self.grants.get(gid)
        names = set()
        if isinstance(step, str):
            p, _, nid = step.rpartition(":")
            n = self.nodes.get((p or None, nid)) or self.nodes.get(("set", nid))
            fut = n.get("future") if isinstance(n, dict) and isinstance(n.get("future"), dict) else {}
            names = set([step, nid] + _as_list(fut.get("from")))
        matches = [r for r in rows if names & set(steps_refs(r.get("Steps") or ""))]
        pool = matches or rows
        # Equal specificity must not silently erase the requirement for person confirmation.
        controlled = [r for r in pool if words(r.get("System of record"))[:1] in (["yes"], ["unclear"])]
        return (controlled or pool)[0]

    def grant_system(self, gid, step=None):
        row = self.grant_row(gid, step) or {}
        s = row.get("System")
        return (self.system_of(s) or s) if isinstance(s, str) else None

    def kind(self, gid, step=None):
        return grant_kind(self.grant_row(gid, step), self.agent_tags, self.person_names, self._system_names())

    def kind_how(self, gid, step=None):
        return grant_kind_how(self.grant_row(gid, step), self.agent_tags, self.person_names, self._system_names())

    def _system_names(self):
        """Every name and id of a system the run draws (workspaces, system lanes, system surfaces)."""
        out = set(self.systems)
        for names in self.systems.values():
            out.update(names)
        return out

    def person_destination(self, gid, step=None):
        """True when grant gid's destination is the person. The System cell is resolved first: a drawn person step
        or people lane is the person; a system the run draws (or the agent's own tag) is not, whatever its words.
        Only a cell that resolves to nothing falls back: the word `person`, or a draft to a name nothing draws (a
        person's role, such as a reviewer's title)."""
        row = self.grant_row(gid, step) or {}
        system = row.get("System")
        if not isinstance(system, str) or not system.strip():
            return False
        w = words(system)
        if w in _names(self.person_names):
            return True
        if norm(system) in self.systems or w in _names(self._system_names()) or w in _names(self.agent_tags):
            return False
        return "person" in w or self.kind(gid, step) == "draft"

    def direction_kind(self, gid, step=None):
        """The grant kind to hand data_direction: None when no metadata is known for the grant, or when its row
        gives no readable write kind; either way the field's own kind decides (see kind_reading)."""
        if gid not in self.grants:
            return None
        return self.kind(gid, step)

    def kind_reading(self, gid, step=None):
        """A reading note when grant gid's row gives no readable write kind (the field's own kind decides), else
        None."""
        if gid not in self.grants:
            return None
        kind, how = self.kind_how(gid, step)
        if kind is None:
            return reading_note("grant-kind-reading", gid, "the Write grants row of %s gives no readable write kind; "
                                "each field keyed on it is read by its own kind" % gid)
        if how == "wording":
            return reading_note("grant-kind-reading", gid, "the Write grants row of %s names nothing the run draws in "
                                "a deciding cell; read by its words as a %s" % (gid, kind.replace("_", " ")))
        return None

    def resolve(self, ref, process=None, grant=None):
        """(identity, findings) for one reference. An explicit form decides first (a `WG-<n>` grant id, a
        `<process>:<node>` step id). A reference matching several candidates is read as one, by a structural
        tie-break: the candidate in the reference's own process, then the system the step's grant names (`grant`),
        then the first in a fixed order (step, system, record, process); the identity carries its `candidates` and a
        `reading` saying why. Only a reference matching nothing is `unresolved` with a finding."""
        findings = []
        if not isinstance(ref, str) or not ref.strip():
            return identity(UNRESOLVED, source_ref=ref), [{"code": "ref-unresolved", "where": repr(ref),
                                                           "detail": "an empty reference names nothing"}]
        ref = ref.strip()
        if GRANT_ID.match(ref):
            if ref in self.grants:
                return identity(GRANT, ref, system=self.grant_system(ref), source_ref=ref), findings
            return identity(UNRESOLVED, source_ref=ref), [{"code": "grant-unknown", "where": ref,
                                                          "detail": "no Write grants row holds %s" % ref}]
        if ":" in ref:
            p, local = ref.split(":", 1)
            if (p, local) in self.nodes:
                return identity(STEP, ref, process=p, source_ref=ref), findings
            if p in self.processes or p == "set":
                return identity(UNRESOLVED, source_ref=ref), [{"code": "step-unresolved", "where": ref,
                                                              "detail": "process %s draws no node %s" % (p, local)}]
        cands = []
        if ref in self.processes:
            cands.append(identity(PROCESS, ref, process=ref, source_ref=ref))
        steps = self.steps.get(ref, [])
        if process is not None:
            local = [s for s in steps if s[0] in (process, "set")]
            if steps and not local:
                findings.append(reading_note("process-reading", ref,
                                             "no step in the requested process; read the uniquely named step elsewhere"))
            steps = local or steps
        for p, nid in steps:
            n = self.nodes[(p, nid)]
            if n.get("type") == "system_surface":
                continue
            cands.append(identity(STEP, "%s:%s" % (p, nid), process=p, source_ref=ref))
        system = self.system_of(ref)
        systems = {system} if system else self.systems.get(norm(ref))
        if systems:
            for s in sorted(systems):
                cands.append(identity(SYSTEM, s, system=s, source_ref=ref))
        if norm(ref) in self.records:
            # one candidate per process that writes the record: a record written in two processes is two records
            # until something says otherwise, never collapsed into one
            places = sorted(set(p for p, _ in self.records[norm(ref)]))
            if process is not None and process in places:
                places = [process]
            for p in places:
                cands.append(identity(RECORD, "%s:%s" % (p, ref) if len(places) > 1 else ref, process=p,
                                      source_ref=ref))
        if len(cands) == 1:
            return cands[0], findings
        if not cands:
            return identity(UNRESOLVED, source_ref=ref), [{"code": "ref-unresolved", "where": ref,
                                                          "detail": "matches no grant, system, step or record"}]
        return self._pick(ref, cands, process, grant), findings

    def _pick(self, ref, cands, process=None, grant=None):
        """The one candidate read for an ambiguous reference (see resolve), with its alternatives and why."""
        pool, why = list(cands), "the first candidate in a fixed order (step, system, record, process)"
        own = [c for c in pool if process is not None and c.get("process") == process]
        if own and len(own) < len(pool):
            pool, why = own, "the candidate in its own process %s" % process
        dest = self.grant_system(grant) if isinstance(grant, str) else None
        by_grant = [c for c in pool if dest and c["type"] == SYSTEM and norm(c["id"]) == norm(dest)]
        if by_grant and len(by_grant) < len(pool):
            pool, why = by_grant, "the system its step's grant %s names" % grant
        rank = {STEP: 0, SYSTEM: 1, RECORD: 2, PROCESS: 3}
        chosen = dict(sorted(pool, key=lambda c: rank.get(c["type"], 9))[0])
        chosen["candidates"] = [dict(c) for c in cands]
        chosen["reading"] = "%s matches %s; read as %s %s: %s" % (
            ref, ", ".join("%s %s" % (c["type"], c["id"]) for c in cands), chosen["type"], chosen["id"], why)
        return chosen

    def _conflict_key(self, p, nid):
        """The key under node_conflicts that holds (p, nid)'s other drawings: its own, or its spanning home's; else
        None."""
        if (p, nid) in self.node_conflicts:
            return (p, nid)
        home = self.span_home.get((p, nid))
        return home if home in self.node_conflicts else None

    def step_conflict(self, step_ref):
        """Kept for callers: a step drawn twice is read from its first drawing (a note in `notes`), so nothing here
        ever stops a step being read. Always empty."""
        return []

    def step_notes(self, step_ref):
        """The reading notes of a qualified step id drawn twice with different content (empty when it is one)."""
        if not isinstance(step_ref, str) or ":" not in step_ref:
            return []
        key = self._conflict_key(*step_ref.split(":", 1))
        return [n for n in self.notes if key and n.get("where") == "%s:%s" % key]

    def step_grants(self, step_ref):
        """The grants a step names in `write_grant` / `write_grants` (a qualified step id); a step drawn twice is
        read from its first drawing."""
        if not isinstance(step_ref, str) or ":" not in step_ref:
            return []
        n = self.nodes.get(tuple(step_ref.split(":", 1)))
        return grant_ids(n) if n else []

    def is_person_step(self, step_ref):
        """True for a resolved qualified step a person does (a people-band lane, no agent tag)."""
        if not isinstance(step_ref, str) or ":" not in step_ref:
            return False
        return tuple(step_ref.split(":", 1)) in self.person_steps


class StepRows(dict):
    """The Write grants rows by grant id as the shared Resolver reads them (the first row of each grant), with
    `for_step(step)` giving the rows one step reads (the row whose Steps names it, else the first). Every consumer
    that looks a grant up by id uses this, so they all take one reading."""

    def __init__(self, res):
        dict.__init__(self, res.grants)
        self.res = res

    def for_step(self, step):
        return dict((gid, self.res.grant_row(gid, step)) for gid in self.res.grants)


def step_rows(rows, graphs=()):
    """StepRows from table rows alone (grant ids with backticks or spaces read the same)."""
    clean = [dict(r, Grant=cell(r["Grant"])) for r in rows or () if isinstance(r, dict) and
             isinstance(r.get("Grant"), str) and cell(r["Grant"])]
    out = StepRows(Resolver(list(graphs), clean))
    out.rows = clean   # kept so a caller can rebuild the reading once it has the graphs
    return out


def agent_side(boundary):
    """Which side of a boundary is the agent's ("from" or "to"), from the typed sides; None when neither or both."""
    if not isinstance(boundary, dict):
        return None
    sides = [k for k in ("from", "to") if isinstance(boundary.get(k), dict) and boundary[k].get("side") == "agent"]
    return sides[0] if len(sides) == 1 else None


def data_direction(field_direction, grant_kind_name=None):
    """The direction data actually flows on a boundary, from the field's kind and the grant's metadata, never from
    the drawn arrow: `to_agent` for an inbound field (a read drawn agent -> system still brings data to the agent),
    `from_agent` for an outbound one. The field's own kind decides: a grant kind that disagrees with it, or one the
    table does not settle, is a reading to record (direction_note), never a field that supplies nothing. Only a
    field kind that is neither inbound nor outbound gives `unknown`."""
    if field_direction in INBOUND_FIELDS:
        return TO_AGENT
    if field_direction in OUTBOUND_FIELDS:
        return FROM_AGENT
    return UNKNOWN


def direction_note(field_direction, grant_kind_name, gid=None):
    """A one-line reading when the grant's kind and the field's kind point different ways (the field's kind is
    read), else None."""
    base = data_direction(field_direction)
    if base == UNKNOWN or grant_kind_name is None:
        return None
    info = WRITE_KINDS.get(grant_kind_name)
    if info is not None and info["direction"] == base:
        return None
    return "a %s field keyed on grant %s (kind %s): read by the field's own kind, data flows %s" % (
        field_direction, gid or "?", grant_kind_name, base)


def boundary_direction(boundary, field_direction, grant_kind_name=None):
    """(agent side "from"/"to" or None, data direction, findings) for one field on one boundary: the one place a
    check asks which side is the agent's and which way data flows. On an agent-to-agent boundary (both sides typed
    `agent`) the field kind names the end: `from` for an outbound field, `to` for an inbound one. No side typed
    `agent`, or two with a field kind that is neither, is `agent-side-unknown`; a field kind that is neither inbound
    nor outbound is `direction-unknown`. A grant kind that disagrees with the field kind is read by the field's kind
    (see direction_note)."""
    findings = []
    side = agent_side(boundary)
    if side is None and isinstance(boundary, dict) and all(
            isinstance(boundary.get(k), dict) and boundary[k].get("side") == "agent" for k in ("from", "to")):
        side = {TO_AGENT: "to", FROM_AGENT: "from"}.get(data_direction(field_direction))
    if side is None:
        findings.append({"code": "agent-side-unknown", "where": str((boundary or {}).get("id")
                                                                     if isinstance(boundary, dict) else boundary),
                         "detail": "neither or both sides of the boundary are typed agent; no agent step is taken"})
        return None, UNKNOWN, findings
    direction = data_direction(field_direction, grant_kind_name)
    if direction == UNKNOWN:
        findings.append({"code": "direction-unknown", "where": str(boundary.get("id")),
                         "detail": "field kind %r and grant kind %r give no data direction" % (
                             field_direction, grant_kind_name)})
    return side, direction, findings


def satisfies(kind_name, field_direction, to_side):
    """True when a field of this direction, reaching a boundary side of this type, can carry a grant of this write
    kind (the caller also checks the key, the agent source and the grant's own destination)."""
    info = WRITE_KINDS.get(kind_name)
    if info is None or data_direction(field_direction) != FROM_AGENT:
        return False
    return any(to_side in sides and field_direction in fields for sides, fields in info["routes"])


# ---- recorded readings ----

def readings(value, where, rows_by_id):
    """(readings, problems) from a `grant_readings` / `upstream_readings` value. A reading must name a row of the
    table, a Write grants column and a non-empty reason; one that does not is a grant-reading-invalid problem and is
    not applied. A Steps reading's `read_as` must be a list of ids to replace the cell."""
    if value is None:
        return [], []
    if not isinstance(value, list):
        return [], [problem("grant-reading-invalid", where, "the readings are not a list")]
    good, bad = [], []
    for i, r in enumerate(value):
        at = "%s[%d]" % (where, i)
        if not isinstance(r, dict):
            bad.append(problem("grant-reading-invalid", at, "a reading is not an object"))
            continue
        gid, col, reason = r.get("grant"), r.get("column"), r.get("reason")
        why = None
        if not isinstance(gid, str) or gid not in rows_by_id:
            why = "names %r, which is no row of the Write grants table" % (gid,)
        elif col not in HEADER:
            why = "column %r is not a Write grants column" % (col,)
        elif not isinstance(reason, str) or not reason.strip():
            why = "carries no reason"
        elif r.get("status", "resolved") not in READING_STATUSES:
            why = "status %r is not resolved or unresolved" % (r.get("status"),)
        elif r.get("rules") is not None and not (isinstance(r["rules"], list)
                                                 and all(isinstance(x, str) for x in r["rules"])):
            why = "rules is not a list of finding names"
        elif col == "Steps" and r.get("read_as") is not None and not (
                isinstance(r["read_as"], list) and all(isinstance(x, str) and x.strip() for x in r["read_as"])):
            why = "a Steps reading's read_as is not a list of step ids"
        if why:
            bad.append(problem("grant-reading-invalid", at, "reading %s" % why, [gid] if isinstance(gid, str) else []))
            continue
        good.append(dict(r, status=r.get("status", "resolved"), rules=list(r.get("rules") or [])))
    return good, bad


def steps_readings(rs):
    """{grant: [step ids]} from the Steps readings that carry a read_as list."""
    return dict((r["grant"], list(r["read_as"])) for r in rs
                if r.get("column") == "Steps" and isinstance(r.get("read_as"), list))


def match(p, rs):
    """The reading that answers problem p (same grant, its rule in the reading's `rules`), or None. A Steps reading
    with a `read_as` list answers nothing this way: it already replaced the cell, and what the rules still find on
    the steps it names is a real finding (a bypass of the confirmation stays a failure)."""
    for r in rs:
        if r.get("column") == "Steps" and isinstance(r.get("read_as"), list):
            continue
        if r["grant"] in p.get("grants", []) and p["rule"] in r["rules"]:
            return r
    return None


# ---- the rules ----

def table_problems(members, rows, read_steps=None):
    """Carriers, Steps resolution and drawn systems for one table shared by `members` (one Graph per process).
    Returns (problems, grants {id: row}, steps_of {id: set of qualified step ids}, rgraphs {id: [Graph]},
    carriers {id: {pid: [node ids]}})."""
    read_steps = read_steps or {}
    problems, grants, steps_of, rgraphs, carriers_of = [], {}, {}, {}, {}
    drawn = set()
    for g in members:
        drawn |= g.drawn()
    for r in rows:
        gid = r["Grant"]
        grants.setdefault(gid, r)
        raw = read_steps.get(gid)
        refs = list(raw) if raw is not None else steps_refs(r["Steps"])
        carriers = dict((g.pid, [nid for nid, n in g.nodes.items() if gid in grant_ids(n)]) for g in members)
        carriers_of[gid] = carriers
        hits = dict((g.pid, dict((ref, g.resolve(ref)) for ref in refs if ref and ref != "new")) for g in members)
        rg = [g for g in members if carriers[g.pid] or any(hits[g.pid].values())] or members[:1]
        rgraphs[gid] = rg
        at = "%s %s" % (rg[0].pid, gid) if rg[0].pid else gid
        if is_agent_writer(r["Who writes"]) and not any(carriers.values()):
            problems.append(problem("grant-step-missing", at, "no future-state node carries %s in write_grant or "
                                    "write_grants" % gid, [gid]))
        if any(not ref for ref in refs):
            problems.append(problem("grant-step-missing", at, "%s has an empty Steps cell or item" % gid, [gid]))
        for ref in refs:
            if ref and ref != "new" and not any(hits[g.pid][ref] for g in members):
                # only an older single-process future state (no lanes list) may lack future.from altogether
                lineage = any(g.has_lineage() or g.has_lanes for g in members)
                problems.append(problem(
                    "grant-step-missing", at, "%s: Steps names %s, found neither in the future state nor in any "
                    "future node's future.from%s" % (gid, qualify(rg[0].pid, ref),
                                                     "" if lineage else " (this future state records no future.from)"),
                    [gid], no_lineage=not lineage))
        for g in rg:
            resolved = set(carriers[g.pid])
            for ids in hits[g.pid].values():
                resolved.update(ids)
            steps_of.setdefault(gid, set()).update(qualify(g.pid, x) for x in resolved)
        if not any(norm(g.system_name(r["System"])) in drawn for g in rg):
            problems.append(problem("grant-system-undrawn", at, "System %r is drawn by no future-state lane or node"
                                    % r["System"], [gid]))
    return problems, grants, steps_of, rgraphs, carriers_of


def node_problems(g, grants):
    """write-grant-unknown and grant-fields-disagree on one graph's nodes."""
    out = []
    for i, n in g.nodes.items():
        for gid in grant_ids(n):
            if gid not in grants:
                out.append(problem("write-grant-unknown", qualify(g.pid, i), "the step names %s, which no row of the "
                                   "Write grants table holds" % gid, [gid]))
        one, many = _as_list(n.get("write_grant")), _as_list(n.get("write_grants"))
        if n.get("write_grant") is not None and n.get("write_grants") is not None and set(one) != set(many):
            out.append(problem("grant-fields-disagree", qualify(g.pid, i), "write_grant names %s but write_grants "
                               "names %s; both are read" % (", ".join(one) or "nothing", ", ".join(many) or "nothing"),
                               sorted(set(one) ^ set(many))))
    return out


def _rows_for(g, nid, grants, sr=None):
    """The rows one step reads: the shared reading (StepRows.for_step on its qualified id) when given, else the
    table's first row per grant."""
    if sr is None:
        return grants
    rows = sr.for_step(qualify(g.pid, nid))
    return dict((gid, rows.get(gid) or grants[gid]) for gid in grants)


def collect_writes(g, grants, sr=None):
    """[(node id, system)] from the node fields that name a write target: `writes` and `write_grant(s)`."""
    out = []
    drawn = g.drawn()
    for i, n in g.nodes.items():
        if not g.is_agent(n):
            continue
        rows = _rows_for(g, i, grants, sr)
        own = [gid for gid in grant_ids(n) if gid in grants]
        for s in _entries(n.get("writes")):
            system = write_system(s)
            if system is None and isinstance(s, dict):
                text = s.get("record") or s.get("work_object")
                if isinstance(text, str) and norm(g.system_name(text.strip())) in drawn:
                    system = text.strip()
            if system is not None:
                out.append((i, g.system_name(system)))
            elif not own:
                out.append((i, UNNAMED))
        for gid in own:
            out.append((i, g.system_name(rows[gid]["System"])))
        # a node's `systems` are the surfaces it reads or works in, and a drawn arrow is not a write field: only
        # `writes` and `write_grant(s)` are write targets (C5, PA-2, U9b ruling)
    seen, uniq = set(), []
    for i, s in out:
        if (i, norm(s)) not in seen:
            seen.add((i, norm(s)))
            uniq.append((i, s))
    return uniq


def _covers(g, grants, x, system):
    """A grant covers a write into `system` when its System names that system (a workspace id read as its name)."""
    return norm(g.system_name(grants[x]["System"])) == norm(g.system_name(system))


def _has_exception(row):
    """True when a grant's Exception reason cell holds a reason (not empty, `none` or `n/a`, case aside)."""
    w = words(row.get("Exception reason"))
    return bool(w) and w not in (["none"], ["n", "a"])


SPLITS_HEADING = re.compile(r"^\s{0,3}#+\s*(\d+[.)]?\s*)?splits\b.*$", re.I)
ANY_HEADING = re.compile(r"^\s{0,3}#")


def split_refs(map_text):
    """The ids (step and grant ids) the map's `## Splits` section names (heading with or without a section number);
    an empty set when there is none. A write the section names keeps its confirmation step. Fenced blocks and
    comments never count."""
    out, inside = set(), False
    lines = (map_text or "").splitlines()
    mask = _md_blocks.mask(lines)
    for line, masked in zip(lines, mask):
        if masked:
            continue
        if ANY_HEADING.match(line):
            inside = bool(SPLITS_HEADING.match(line))
            continue
        if inside:
            out.update(re.findall(r"[A-Za-z0-9][A-Za-z0-9_.:\-]*", line.replace("`", " ")))
    return out


WHY_HEADING = re.compile(r"^\s{0,3}#+\s*(\d+[.)]?\s*)?[*_]*why people stay\b[^\n]*$", re.I)   # trailing words: a variant


def why_people_stay(map_text):
    """The map's `## Why people stay` rows, read leniently (0503 R-lenient): the heading with or without a section
    number or trailing words, header cells matched case-insensitively with emphasis marks ignored, extra columns ignored, a missing Why cell a note. Fenced blocks
    and comments never count. Returns (rows, notes): rows are {"step", "process", "reason", "why"} (reason
    lower-cased, backticks stripped); notes are plain strings recording the reading taken."""
    lines = (map_text or "").splitlines()
    mask = _md_blocks.mask(lines)
    start = next((i for i, l in enumerate(lines) if WHY_HEADING.match(l) and not mask[i]), None)
    if start is None:
        return [], []
    block = []
    for i in range(start + 1, len(lines)):
        if mask[i]:
            continue
        if ANY_HEADING.match(lines[i]):
            break
        if lines[i].strip().startswith("|"):
            block.append(lines[i])
    split = lambda ln: [cell(c) for c in ln.strip().strip("|").split("|")]
    if len(block) < 2:
        return [], ["the Why people stay section holds no readable table; read as no rows"]
    header = [re.sub(r"[*_]", "", c).strip().lower() for c in split(block[0])]   # **Step** reads as step
    col = dict((k, header.index(k)) for k in ("step", "process", "reason", "why") if k in header)
    if "step" not in col or "reason" not in col:
        return [], ["the Why people stay table has no Step or Reason column; read as no rows"]
    rows, notes = [], []
    for ln in block[2:]:
        raw = split(ln)
        get = lambda k: raw[col[k]] if k in col and col[k] < len(raw) else ""
        if not get("step"):
            continue
        r = {"step": get("step"), "process": get("process"), "reason": get("reason").lower(), "why": get("why")}
        if not r["why"]:
            notes.append("step %s has no Why cell in Why people stay" % r["step"])
        rows.append(r)
    return rows, notes


def control_steps(rows):
    """The step ids listed in Why people stay with Reason `control` (a Step cell may list several)."""
    return set(x.replace("`", "").strip() for r in rows if r["reason"] == "control" for x in r["step"].split(",")
               if x.replace("`", "").strip())


def keeps_confirmation(g, gid, steps_of, splits=(), raw_steps=""):
    """True when the map keeps a person confirmation step for grant `gid`'s write (0503 R-writes): the grant's Steps
    resolve to a person step of `g` (the split partner of the agent step: the person confirms, the agent enters), or
    the map's `## Splits` names the grant or one of its Steps ids. Otherwise the agent writes with no confirmation:
    a mechanical write of values copied unchanged from a named source needs none.

    The grant's own Steps cell (`raw_steps`, as the table says it) is resolved here too, so a recorded Steps reading
    that narrows the grant never hides the confirmation the map keeps."""
    refs = [r for r in steps_refs(raw_steps or "") if r and r != "new"]
    if gid in splits or any(ref in splits for ref in refs):
        return True
    if any(g.is_person(g.nodes[n]) for ref in refs for n in g.resolve(ref) if n in g.nodes):
        return True
    for q in steps_of.get(gid, ()):
        local = q[len(g.pid) + 1:] if g.pid and q.startswith(g.pid + ":") else q
        if local in splits or (local in g.nodes and g.is_person(g.nodes[local])) or any(
                g.is_person(g.nodes[n]) for n in g.resolve(q) if n in g.nodes):
            return True
    return False


def write_problems(g, grants, steps_of, sr=None, splits=()):
    """write-ungranted, exception-scope and write-before-confirm on one graph; returns (problems, write count).
    sr: the shared reading (StepRows); each step is judged by the rows it reads (where the tables give a grant
    different rows, the row whose Steps names the step, else the first)."""
    out = []
    writes = collect_writes(g, grants, sr)
    all_grants = grants
    for nid, system in writes:
        grants = _rows_for(g, nid, all_grants, sr)
        q = qualify(g.pid, nid)
        named = [x for x in grant_ids(g.nodes[nid]) if x in grants]
        mine = [x for x in grants if q in steps_of.get(x, set()) or x in named]
        cover = [x for x in mine if _covers(g, grants, x, system)]
        at = "%s -> %s" % (q, system)
        if not cover:
            if mine and all(_has_exception(grants[x]) for x in mine):
                out.append(problem("exception-scope", at, "the step's only grants carry an exception reason and none "
                                   "covers this system", mine))
            else:
                out.append(problem("write-ungranted", at, "agent write with no grant covering this system", mine))
            continue
        checked = [x for x in cover if is_agent_writer(grants[x]["Who writes"])
                   and "mechanical" in words(grants[x]["Write kind"])
                   and words(grants[x]["System of record"])[:1] in (["yes"], ["unclear"])]
        if not checked:
            continue
        exempt = named and all(_has_exception(grants[x]) for x in named) and any(
            _covers(g, grants, x, system) for x in named)
        if exempt:
            continue
        # a confirmation the future state draws before the write is judged however the map carries the split (two
        # grants, Steps or `## Splits`); a write with neither needs no confirmation (0503 rows 12 and 17)
        confirms = g.own_confirmations(nid)
        # a scoped graph (Agent Design's set ownership) judges retention on its full drawing, as own_confirmations does
        whole = getattr(g, "context", g)
        if not confirms and not any(keeps_confirmation(whole, x, steps_of, splits, grants[x].get("Steps"))
                                    for x in checked):
            continue
        if not confirms:
            out.append(problem("write-before-confirm", at, "mechanical write (%s) has no person confirmation of its "
                               "own" % ", ".join(checked), checked))
        elif nid in g.reachable_avoiding(confirms):
            out.append(problem("write-before-confirm", at, "mechanical write (%s) reachable from a start avoiding its "
                               "own confirmations %s" % (", ".join(checked), ", ".join(sorted(confirms))), checked))
    return out, len(writes)


def evaluate(members, rows, sources=(), splits=()):
    """Every rule of this module for one table and the graphs that share it, with the recorded readings applied.
    sources is [(where, readings value)], e.g. [("future-state grant_readings", [...])]. splits is the set of ids the
    map's `## Splits` section names (`split_refs`); write-before-confirm judges a write whose confirmation the map
    keeps (`keeps_confirmation`) or whose confirmation the future state draws (`own_confirmations`).

    Returns a dict: problems (failing), recorded (answered by a reading: each problem gains `reading`), readings
    (the valid readings), grants, steps_of, rgraphs, carriers, writes (count)."""
    by_id = dict((r["Grant"], r) for r in rows)
    rs, bad = [], []
    for where, value in sources:
        good, wrong = readings(value, where, by_id)
        rs += good
        bad += wrong
    problems, grants, steps_of, rgraphs, carriers = table_problems(members, rows, steps_readings(rs))
    # the shared reading (same Resolver and tie-break as every other check) decides which row a step reads
    sr = StepRows(Resolver([(g.pid, g.document) for g in members], rows))
    count = 0
    for g in members:
        problems += node_problems(g, grants)
        wp, n = write_problems(g, grants, steps_of, sr, splits)
        problems += wp
        count += n
    failing, answered = list(bad), []
    for note in sr.res.notes:
        answered.append(dict(note, rule=note.get("code", "identity-reading"),
                             reading={"status": "resolved", "reason": note["detail"]}))
    seen_labels = set()
    for g in members:
        for f in g.label_findings:
            if f["where"] not in seen_labels:   # a set's members share one lanes list: one reading per lane
                seen_labels.add(f["where"])
                answered.append(dict(f, reading={"grant": None, "column": None, "status": "resolved",
                                                 "reason": "the label names one system; read as %s" % f["read_as"]}))
    for p in problems:
        r = match(p, rs)
        if r is None:
            failing.append(p)
        else:
            answered.append(dict(p, reading=dict((k, r.get(k)) for k in ("grant", "column", "reason", "status"))))
    return {"problems": failing, "recorded": answered, "readings": rs, "grants": grants, "steps_of": steps_of,
            "rgraphs": rgraphs, "carriers": carriers, "writes": count}
