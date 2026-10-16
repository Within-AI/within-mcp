"""Curated scan rules for readiness.py (stdlib only).

A curated copy of the candidate rules and never-flag rules of the plugin's sandbox-ship leak scan
(knowledge/checks/_sandbox_ship_leak.py) and its clearance check. Kept: name-shaped strings (two or more
capitalised words), organisation suffixes, absolute paths foreign to the run, foreign workspace or tenant ids;
added: domain names. Never flagged: the current client by exact normalised match, {{ }} placeholders, the
commodity word list, leading sentence words. Dropped: YAML, the sandbox-ship spec, the run-header events reader,
Dockerfile and .dockerignore generation and every container step.

Provenance (0494): a client-name candidate is a guess from the shape of the text, so it is judged on where it came
from, not on another word list. source_index() and trace() find where the exact phrase occurs in this run's sources
and the client each source declares (JSON top-level keys and key-value or front-matter lines); blocked() says when
only a person may clear it (another client's material, or a company-shaped name found in no source of this run);
adjudication_problem() is the one rule for which agent adjudications are accepted. Nothing here clears a candidate.
"""

import hashlib
import json
import os
import re

ORG_SUFFIXES = ("Inc", "Ltd", "LLC", "GmbH", "plc", "Corp", "Bank", "Group")

# Commodity tools and vendors, Within, and the plugin's own vocabulary. Words, matched case-insensitively.
# The source's retired two-word brain term is replaced by "Work Brain".
COMMODITY_WORDS = frozenset(w.lower() for w in """
Within Python Docker Dockerfile GitHub GitLab Bitbucket Slack Linux Ubuntu Debian Alpine macOS Windows Microsoft Google
Amazon Web Services AWS Azure OpenAI Anthropic Claude Codex Postgres PostgreSQL MySQL SQLite Redis Kafka Jira Asana
Notion Linear Salesforce SAP Oracle Snowflake Databricks Excel Outlook Teams Gmail SharePoint Kubernetes Node Java
JavaScript TypeScript YAML JSON HTML CSS Markdown PyYAML Phoenix Chrome Apple Meta Actions Cloud Drive Docs Sheets
Work Brain Snapshot Test Suite Production Readiness Gate Agent Technical Design Sandbox Autonomy Ladder Escalation
Interface Simulation Synthetic Data Design Brief Eval Package Spec Stage Run Client Current Future State Workflow
""".split())

# Words that only open a sentence or a heading; dropped from the front of a name-shaped string.
LEADING_WORDS = frozenset(w.lower() for w in """
The This That These Those A An If When Each Every For See Note Use Read Never Always In On At Of To And Or But With
Without Not No Any All Some Also Then Here There It Its Our Your Their We You They Is Are Was Were Be Do Does
""".split())

# Domains that are never a client: reserved example domains and commodity vendors (first label a commodity word).
EXAMPLE_DOMAINS = frozenset(("example.com", "example.org", "example.net", "localhost"))

PLACEHOLDER_RE = re.compile(r"\{\{[^{}]*\}\}")
_WORD = r"[A-Z][A-Za-z0-9&'-]*"
ORG_RE = re.compile(r"\b(?:%s[ \t]+)+(?:%s)\b" % (_WORD, "|".join(ORG_SUFFIXES)))
NAME_RE = re.compile(r"\b%s(?:[ \t]+%s)+\b" % (_WORD, _WORD))
PATH_RE = re.compile(r"(?<![\w:/.~-])/(?:Users|home|tmp|var|private|mnt|opt|srv)/[^\s'\"`)\]>,;}]+")
ID_KEY_RE = re.compile(r"""\b(workspace|tenant)[_-]?id\b["']?\s*[:=]\s*["']?([A-Za-z0-9][A-Za-z0-9_.-]*)""",
                       re.IGNORECASE)
ID_URL_RE = re.compile(r"/(?:workspaces|tenants)/([A-Za-z0-9][A-Za-z0-9_.-]*)")
# Bare domains (no "@", "://" or "www." before them). Fail-safe: every two-letter top-level label is a candidate
# (that covers every country code without a list), as is every generic label below; in an e-mail address or a URL
# any alphabetic top-level label counts. Only the explicit exclusions in _is_not_domain stay quiet.
BARE_TLDS = ("[a-z]{2}|com|net|org|biz|info|dev|app|xyz|tech|cloud|online|site|ltd|inc|gov|edu|mil|int|shop|store"
             "|agency|logistics|bank|insurance|finance|financial|capital|health|law|legal|consulting|solutions"
             "|services|systems|group|global|world|company|business|industries|energy|media|news|digital|network"
             "|email|studio|design|software|support|management|partners|holdings|ventures|enterprises|international"
             "|marketing|express|delivery|supply|supplies|trade|trading|exchange|money|credit|loans|fund|invest"
             "|properties|realty|travel|aero|asia|africa|london|berlin|paris|nyc|tokyo|museum|coop|pro|name|mobi"
             "|life|live|link|club|top|vip|one|plus|team|work|works|zone|space|website|web|page|blog|wiki|run")
# File-name extensions that collide with a candidate label (a name.md or name.py is a file, not a domain).
FILE_EXTENSIONS = frozenset(("md", "py", "sh", "js", "ts", "rb", "rs", "gz", "xz", "cc", "mk", "ps"))
# A two-label identifier such as r2.hr (one letter, then digits) is an id, not a domain.
ID_LABEL_RE = re.compile(r"^[a-z][0-9]+$", re.IGNORECASE)
_LABELS = r"(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+"
DOMAIN_RE = re.compile(r"(?<![\w.-])(%s(?:%s))(?![\w-])" % (_LABELS, BARE_TLDS), re.IGNORECASE)
LINKED_DOMAIN_RE = re.compile(r"(?:@|://|(?<![\w.-])www\.)(%s[a-z]{2,63})(?![\w-])" % _LABELS, re.IGNORECASE)

# Rules a person may dispose as "not-a-client"; every rule may be disposed as "removed".
# client:declared is a client name some source of the run declares, found anywhere in a shipped file.
CLIENT_NAME_RULES = ("client:organisation", "client:name", "client:domain", "client:declared")
CLIENT_SUFFIXES = frozenset(("inc", "ltd", "llc", "gmbh", "corp", "corporation", "co", "group", "plc", "ag", "sa"))


# Agent adjudication bases (readiness.py adjudicate). "escalated" is the agent handing a candidate to a person.
AGENT_BASES = ("ordinary-wording", "own-source", "authored-heading")
ESCALATED = "escalated"
COMPANY_SHAPED_RULES = ("client:organisation", "client:domain")
# A key-value or front-matter style client declaration in a text source: `client: X`, `Client name = X`,
# `"client_name": "X",`, `**Client:** X`. Read structurally (a key and a value), not from a list of names.
_DECLARATION_RE = re.compile(r"""^[\s>*#+-]*["'*_]*client(?:[ _-]?name)?["'*_]*\s*[:=]\s*["'*_]*(.*?)["'*_,;.]*\s*$""",
                             re.IGNORECASE)
# A Markdown table row whose first cell is a client key: `| Client | X |`.
_TABLE_DECLARATION_RE = re.compile(
    r"""^\s*\|\s*["'*_]*client(?:[ _-]?name)?["'*_]*\s*\|\s*["'*_]*([^|]*?)["'*_]*\s*\|""", re.IGNORECASE)
_TOKEN = re.compile(r"[a-z0-9]+")


def name_tokens(name):
    """Case- and punctuation-folded word tokens of a name, one trailing company suffix dropped."""
    return _TOKEN.findall(normalise_org(name))


def contains_name(text, name):
    """True when NAME's tokens occur in TEXT as a run of whole tokens (case and punctuation folded)."""
    want = name_tokens(name)
    if not want:
        return False
    hay = " %s " % " ".join(_TOKEN.findall(text.lower()))
    return (" %s " % " ".join(want)) in hay


def _fold(text):
    return " ".join(text.lower().split())


def text_declarations(text):
    """Client names a text source declares in key-value or front-matter lines."""
    out = []
    for line in text.splitlines():
        m = _DECLARATION_RE.match(line) or _TABLE_DECLARATION_RE.match(line)
        if m:
            value = " ".join(m.group(1).split())
            if value and value[0] not in "{[" and value.lower() not in ("null", "none", "~"):
                out.append(value)
    return out


def declared_clients(doc):
    """Every client identity a JSON document declares, at any depth: client_name, or client as a string or {name}."""
    out = []

    def walk(node):
        if isinstance(node, dict):
            for key, value in node.items():
                if str(key).lower() in ("client_name", "client"):
                    name = value.get("name") if isinstance(value, dict) else value
                    if isinstance(name, str) and name.strip():
                        out.append(" ".join(name.split()))
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(doc)
    return out


def declared_client(doc):
    """The first client identity a JSON document declares, or None."""
    found = declared_clients(doc)
    return found[0] if found else None


def source_declarations(text, doc=None):
    """Every client a source declares: its JSON top level (DOC) and its key-value lines, without repeats."""
    found = []
    for name in (declared_clients(doc) if doc is not None else []) + text_declarations(text):
        if name and name not in found:
            found.append(name)
    return found


def source_index(sources, client):
    """SOURCES: iterable of (path, text, declared names). Returns (path, folded text, declared, foreign) entries;
    a source is foreign when any name it declares is not the run's CLIENT."""
    out = []
    for path, text, declared in sources:
        foreign = [d for d in declared if not is_own_client(d, client)]
        out.append((path, _fold(text), list(declared), bool(foreign)))
    return out


def trace(candidate, index, client, foreign_names=()):
    """Provenance of CANDIDATE: where the exact phrase (case and whitespace folded) occurs, with each source's
    declared client; foreign lists the sources that declare another client, plus "declared:<name>" when the
    candidate contains (as whole words) a client name some source or shipped file declares that is not the run's
    client."""
    phrase = _fold(candidate)
    sources, foreign = [], []
    for path, folded, declared, is_foreign in index:
        if phrase and phrase in folded:
            sources.append({"path": path, "declared_client": declared})
            if is_foreign:
                foreign.append(path)
    for name in foreign_names:
        if not is_own_client(name, client) and contains_name(candidate, name) \
                and "declared:%s" % name not in foreign:
            foreign.append("declared:%s" % name)
    return {"run_client": client, "sources": sources, "foreign": foreign}


def blocked(rule, provenance):
    """Why only a person may clear this candidate, or None when the agent may judge it."""
    if provenance.get("foreign"):
        return "it appears in material declaring another client (%s)" % ", ".join(provenance["foreign"])
    if rule in COMPANY_SHAPED_RULES and not provenance.get("sources"):
        return "it is company-shaped and appears in no source of this run"
    return None


def adjudication_problem(rule, basis, provenance, source=None):
    """None when an agent may clear this candidate on BASIS; otherwise the reason it stays for a person."""
    if rule not in CLIENT_NAME_RULES:
        return "only client-name candidates can be adjudicated by the agent (rule %s)" % rule
    if basis not in AGENT_BASES:
        return "basis %r is not one of %s" % (basis, ", ".join(AGENT_BASES))
    why = blocked(rule, provenance)
    if why:
        return why
    if basis == "own-source":
        paths = [s.get("path") for s in provenance.get("sources") or []]
        if not source:
            return "own-source must name the source it comes from"
        if source not in paths:
            return "the named source %s does not hold the text" % source
    return None


def _is_commodity(words):
    words = [w for w in words if w not in ORG_SUFFIXES]
    return bool(words) and all(w.lower() in COMMODITY_WORDS for w in words)


def normalise_org(name):
    """Lowercase, trim, collapse whitespace, drop one trailing company suffix."""
    words = re.sub(r"[,;]", " ", name.lower()).split()
    if len(words) > 1 and words[-1].rstrip(".") in CLIENT_SUFFIXES:
        words.pop()
    return " ".join(words)


def is_own_client(candidate, client):
    """Exact match only, once both are normalised. Containment either way is not a match."""
    return bool(client) and normalise_org(candidate) == normalise_org(client)


def _is_commodity_domain(domain):
    d = domain.lower()
    if d in EXAMPLE_DOMAINS or any(d.endswith("." + e) for e in EXAMPLE_DOMAINS):
        return True
    labels = d.split(".")
    return len(labels) >= 2 and labels[-2] in COMMODITY_WORDS


def _is_not_domain(token, next_char):
    """The explicit bare-domain exclusions: a file name, a two-label id, or code (a call or an index)."""
    labels = token.lower().split(".")
    if labels[-1] in FILE_EXTENSIONS:
        return True
    if len(labels) == 2 and ID_LABEL_RE.match(labels[0]):
        return True
    return next_char in ("(", "[")


def _under_root(p, root):
    if not root:
        return False
    real_root = os.path.realpath(root)
    rp = os.path.realpath(p)
    return (rp == real_root or rp.startswith(real_root + os.sep) or p == root
            or p.startswith(root.rstrip("/") + "/"))


def scan_client(text, client=None, run_root=None, own_ids=()):
    """Client-name candidates in TEXT as a list of (line_no, rule, match)."""
    found = []
    for lineno, raw in enumerate(text.splitlines(), 1):
        line = PLACEHOLDER_RE.sub(" ", raw)
        taken = []
        for m in ORG_RE.finditer(line):
            taken.append(m.span())
            words = m.group(0).split()
            while len(words) > 1 and words[0].lower() in LEADING_WORDS:
                words.pop(0)
            cand = " ".join(words)
            if _is_commodity(words) or is_own_client(cand, client) or len(words) < 2:
                continue
            found.append((lineno, "client:organisation", cand))
        for m in NAME_RE.finditer(line):
            if any(s <= m.start() < e for s, e in taken):
                continue
            words = m.group(0).split()
            while words and words[0].lower() in LEADING_WORDS:
                words.pop(0)
            if len(words) < 2 or _is_commodity(words):
                continue
            cand = " ".join(words)
            if is_own_client(cand, client):
                continue
            found.append((lineno, "client:name", cand))
        for m in PATH_RE.finditer(line):
            p = m.group(0).rstrip(".:")
            if _under_root(p, run_root):
                continue
            found.append((lineno, "client:path", p))
        for m in ID_KEY_RE.finditer(line):
            if m.group(2) not in own_ids:
                found.append((lineno, "client:id", m.group(2)))
        for m in ID_URL_RE.finditer(line):
            if m.group(1) not in own_ids:
                found.append((lineno, "client:id", m.group(1)))
        # No domain is exempt for carrying the current client's name: ownership is not provable from the text,
        # so a person disposes it. One flag per domain per line, whichever pattern found it.
        domains = []
        for rx in (LINKED_DOMAIN_RE, DOMAIN_RE):
            for m in rx.finditer(line):
                d = m.group(1)
                if rx is DOMAIN_RE and _is_not_domain(d, line[m.end():m.end() + 1]):
                    continue
                if d.lower() not in [x.lower() for x in domains] and not _is_commodity_domain(d):
                    domains.append(d)
        found.extend((lineno, "client:domain", d) for d in domains)
    return found


def client_candidates(text, client=None, run_root=None, own_ids=()):
    """Pinned entry point (G12): list of (line_no, match)."""
    return [(lineno, match) for lineno, _rule, match in scan_client(text, client, run_root, own_ids)]


def load_vocabulary(paths):
    """Compile the banned (and retired) entries of each vocabulary file.

    Returns (entries, error): entries is a list of (name, compiled pattern); error names the first file that is
    missing, unreadable or malformed (entries is then None).
    """
    entries = []
    for path in paths:
        try:
            with open(path, encoding="utf-8") as f:
                doc = json.load(f)
        except (OSError, ValueError) as err:
            return None, "vocabulary file unreadable: %s (%s)" % (os.path.basename(path), err)
        if not isinstance(doc, dict) or not isinstance(doc.get("banned"), list):
            return None, "vocabulary file malformed: %s" % os.path.basename(path)
        for key in ("banned", "retired"):
            for item in doc.get(key) or []:
                if not isinstance(item, dict) or not item.get("name") or not item.get("pattern"):
                    return None, "vocabulary entry malformed in %s" % os.path.basename(path)
                try:
                    entries.append((item["name"], re.compile(item["pattern"], re.IGNORECASE)))
                except re.error as err:
                    return None, "vocabulary pattern invalid in %s: %s" % (os.path.basename(path), err)
    return entries, None


def vocabulary_hits(text, entries):
    """Vocabulary hits in TEXT as a list of (line_no, rule, match), rule = vocabulary:<name>."""
    found = []
    for lineno, line in enumerate(text.splitlines(), 1):
        for name, rx in entries:
            for m in rx.finditer(line):
                found.append((lineno, "vocabulary:%s" % name, m.group(0)))
    return found


def flag_id(rule, path, text):
    """First 12 hex of sha256 of rule<TAB>path<TAB>text."""
    return hashlib.sha256(("%s\t%s\t%s" % (rule, path, text)).encode("utf-8")).hexdigest()[:12]
