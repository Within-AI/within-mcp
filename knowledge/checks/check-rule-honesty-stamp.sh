#!/usr/bin/env python3
# This file is Python despite its .sh name: run it directly or as `python3 <this file>`; `bash <this file>` cannot run it.
"""Rule honesty-stamp check — the decision-rules stage's per-policy content release gate.

Plain-language: "Is every rule stamped where it should be — a source and an evidence state from the four,
its interface fields listed, its opens placed by kind — with no agent-owned decision that lacks a write grant,
and no default/interim/fallback smuggled in for anything left open?" (Former internal tag: the honesty-stamp half of
the stage's checks. The plain-language question is the handle.)

WHAT THIS CHECK IS — AND IS NOT (the enforcement model, R1 §2.5). It is *told* two explicit arguments: the
decision policy AND the published interface-token index (for each token's upstream `evidence_state` / `open`
metadata). It does exactly one kind of work — STRUCTURE and CLOSED / ENUMERATED SETS: presence of stamps,
placement of opens by kind, closed evidence-state set, id-membership of inherited-open stamps, a closed
forbidden-key set, and the authoritative-needs-a-grant invariant. It NEVER judges whether a stamp is TRULY grounded in its
named Company Brain locus, whether a full-stop is honestly a boundary (vs a smuggled conditional), or whether
prose implies an entitlement without a forbidden key — those are the independent cross-engine reviewer's and
the human sign-off's job. A passing structural check is NEVER, on its own, evidence that a policy is honest.

The structural predicates (a script can decide each without judging truth):

  1. COMMITTED-RULE STAMPING (presence, not truth): every committed rule row carries a non-empty
     `source_ids` AND `company_brain_source`, an `evidence_state` in the CANONICAL FOUR
     {Observed, Assumed, Contradictory, needs_more_information} (the retired fifth `SOP-documented` fails as
     out-of-set), and a non-empty `operative_tokens` list. A missing stamp key or an empty tokens list is a
     FAILURE, not a skip.
  2. OPENS STAMPED, NOT GUESSED (by placement): a rule `evidence_state: needs_more_information` has a matching
     `open_register` entry of kind value, rule or scope (referenced by its `open_stamp`; the one kind list
     shared with the interface design residuals — access is never a stamp target, it is its own Assumed
     entry); every `open_register` `kind: rule` or `kind: scope` open carries a non-empty `follow_on` AND `closure_route`.
  3. INHERITED-OPEN STAMPING (structural): for every operative token a rule binds whose published-token record
     in the passed-in index carries an OPEN (its `open` field is a register reference, not `—`), the binding
     rule must carry a non-empty `open_stamp` AND an `open_register` entry whose id the `open_stamp` names. A
     rule binding an index-open token with no `open_stamp`, or whose `open_stamp` names no present
     `open_register` entry, FAILS. Compared by id membership — never by reading prose.
  4. ACCESS IS STRUCTURAL, NOT INTERPRETED (closed forbidden-key set): access lives ONLY as an `open_register`
     entry with `kind: access` stamped `Assumed`. Any of the keys {access, entitlement, entitlements}, or a
     `kind: access` marker AT ANY DEPTH (not only the row's top level — a nested sub-mapping is caught too),
     appearing INSIDE a `rule_sets[].rules[]` row -> FAIL; an `open_register`
     `kind: access` entry stamped anything but `Assumed` -> FAIL. Whether some rule's prose IMPLIES an
     entitlement without a forbidden key is a SEMANTIC judgment left to the review, never this check.
  5. NO INTERIM/DEFAULT/FALLBACK: a `default` / `interim` / `fallback` key ANYWHERE fails (FM-18).
  6. AUTHORITATIVE ONLY UNDER A WRITE GRANT (design 0399 §5): every `rule_sets[].authoritative`,
     `gates[].authoritative` and `dispatch.authoritative` is either exactly `false` (valid with no grant), or
     exactly `true` AND the same object carries `authority_grant` naming one or more write grant ids
     (`WG-<n>`, a YAML list or a comma-separated string). `true` with no grant, a grant that is not a `WG-n`
     id, or any other value FAILS. A legacy all-false policy still passes. The check validates the policy
     alone: whether the named grant's row in the transformation map's Write grants table allows an
     authoritative decision is the coherence gate's lookup (INV-10), never this check's.
  7. GOVERNANCE STAMPING: each `guardrails` entry and each `escalation_triggers` / `special_cases` entry
     carries a non-empty `evidence_state` (canonical four) + a source / `carried_by`.

Any predicate violated -> exit 1, each reported on its own problem line.

§2.5 NOTE: presence and placement only — whether a stamp is *truly grounded* and whether a full-stop is
honestly a boundary (vs a smuggled conditional) is the review's job.

Parsing: PyYAML is REQUIRED. One `yaml.safe_load` path serves both the policy and the token index. PyYAML
absent -> exit 2 (setup error); it never degrades to a line scan. `actor.engine == script`; writer-re-derivable
and cwd-independent.

Usage: check-rule-honesty-stamp.sh [--verbose] DECISION_POLICY TOKEN_INDEX
Exit: 0 pass; 1 structural assertion failure; 2 setup error (incl. PyYAML absent).
"""
import os
import re
import sys

CANONICAL_FOUR = {"Observed", "Assumed", "Contradictory", "needs_more_information"}
BANNED_INTERIM_KEYS = {"default", "interim", "fallback"}
FORBIDDEN_ACCESS_KEYS = {"access", "entitlement", "entitlements"}
WRITE_GRANT_ID = re.compile(r"^WG-[1-9][0-9]*$")


def die(msg, code):
    sys.stderr.write(msg + "\n")
    sys.exit(code)


def _nonempty(v):
    return isinstance(v, str) and v.strip() != ""


def _nonempty_list(v):
    return isinstance(v, list) and len(v) > 0


def load_one_yaml(path, what):
    if not os.path.isfile(path):
        die("Missing %s: %s" % (what, path), 2)
    text = open(path, "r", encoding="utf-8", errors="replace").read()
    try:
        import yaml
    except ImportError:
        die("PyYAML is required for the honesty-stamp parse but is not importable. Install it "
            "(pip install pyyaml) and re-run; this check does not fall back to a line scan.", 2)
    try:
        return yaml.safe_load(text)
    except Exception as e:
        die("%s is not parseable (JSON or YAML): %s" % (what, e), 1)


def _walk_dicts(obj):
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            for d in _walk_dicts(v):
                yield d
    elif isinstance(obj, list):
        for item in obj:
            for d in _walk_dicts(item):
                yield d


def _walk_keys(obj):
    for node in _walk_dicts(obj):
        for k in node.keys():
            yield k


def index_open_tokens(index):
    """The set of published tokens whose record carries an OPEN (its `open` field is a register reference,
    not `—`/empty)."""
    out = set()
    pubs = index.get("published_tokens") if isinstance(index.get("published_tokens"), list) else []
    for rec in pubs:
        if isinstance(rec, dict) and _nonempty(rec.get("token")):
            openv = rec.get("open")
            if _nonempty(openv) and str(openv).strip() != "—":
                out.add(rec["token"])
    return out


def open_register_ids(policy):
    ids = set()
    for o in (policy.get("open_register") if isinstance(policy.get("open_register"), list) else []):
        if isinstance(o, dict) and _nonempty(o.get("id")):
            ids.add(o["id"])
    return ids


# Open kinds a needs_more_information rule may point at (access is its own Assumed entry, never a stamp target).
STAMP_TARGET_KINDS = ("value", "rule", "scope")


def open_register_by_kind(policy, kind):
    out = []
    for o in (policy.get("open_register") if isinstance(policy.get("open_register"), list) else []):
        if isinstance(o, dict) and o.get("kind") == kind:
            out.append(o)
    return out


def _stamp_refs(open_stamp):
    """A rule's `open_stamp` may be a scalar id or a list; return the set of non-empty ids it names ('—' is
    'no open')."""
    if isinstance(open_stamp, list):
        return {str(x).strip() for x in open_stamp if _nonempty(str(x)) and str(x).strip() != "—"}
    if _nonempty(open_stamp) and str(open_stamp).strip() != "—":
        return {str(open_stamp).strip()}
    return set()


def _grant_ids(v):
    """The ids an `authority_grant` names: a YAML list or a comma-separated string. Returns (ids, bad) where
    `bad` lists every entry that is not a well-formed `WG-<n>` id."""
    if isinstance(v, list):
        raw = [str(x).strip() for x in v]
    elif isinstance(v, str):
        raw = [x.strip() for x in v.split(",")]
    else:
        return [], []
    raw = [x for x in raw if x]
    return [x for x in raw if WRITE_GRANT_ID.match(x)], [x for x in raw if not WRITE_GRANT_ID.match(x)]


def authority_problem(obj, label):
    """Predicate 6 for one rule set, gate or dispatch object; None when it holds."""
    auth = obj.get("authoritative")
    if auth is False:
        return None
    if auth is not True:
        return ("(6) %s `authoritative` is %r — must be exactly `false`, or `true` with an `authority_grant` "
                "naming a write grant" % (label, auth))
    ids, bad = _grant_ids(obj.get("authority_grant"))
    if bad:
        return ("(6) %s is `authoritative: true` but its `authority_grant` names %s — each entry must be a "
                "write grant id `WG-<n>`" % (label, bad))
    if not ids:
        return ("(6) %s is `authoritative: true` with no `authority_grant` — an agent-owned decision needs "
                "the write grant id(s) (`WG-<n>`) it rests on, or `authoritative: false`" % label)
    return None


def validate(policy, index):
    P = []
    if not isinstance(policy, dict):
        return ["the decision policy is not a mapping (expected one decision-policy document)"]

    idx_open = index_open_tokens(index)
    reg_ids = open_register_ids(policy)

    # ---- 1 & 3 & 4(rules): iterate every committed rule row ----
    rule_sets = policy.get("rule_sets") if isinstance(policy.get("rule_sets"), list) else []
    for rs in rule_sets:
        if not isinstance(rs, dict):
            P.append("(1) a `rule_sets` entry is not a mapping")
            continue
        # (6) authoritative only under a write grant
        prob = authority_problem(rs, "rule_set %r" % (rs.get("rule_set_ref") or rs.get("owner"),))
        if prob:
            P.append(prob)
        for rule in (rs.get("rules") if isinstance(rs.get("rules"), list) else []):
            if not isinstance(rule, dict):
                P.append("(1) a `rules` row in set %r is not a mapping" % (rs.get("rule_set_ref"),))
                continue
            rid = rule.get("rule_id")
            # (1) stamping presence
            if not (_nonempty_list(rule.get("source_ids")) or _nonempty(rule.get("source_ids"))):
                P.append("(1) rule %r carries no non-empty `source_ids`" % rid)
            if not _nonempty(rule.get("company_brain_source")):
                P.append("(1) rule %r carries no non-empty `company_brain_source`" % rid)
            es = rule.get("evidence_state")
            if es not in CANONICAL_FOUR:
                P.append("(1) rule %r `evidence_state` is %r — only the canonical four are allowed "
                         "{Observed, Assumed, Contradictory, needs_more_information} (the fifth "
                         "`SOP-documented` state is retired)" % (rid, es))
            if not _nonempty_list(rule.get("operative_tokens")):
                P.append("(1) rule %r carries no non-empty `operative_tokens` list — the binding surface is "
                         "required on every committed rule" % rid)
            # (4) forbidden access keys INSIDE a rule row
            bad = sorted(set(_walk_keys(rule)) & FORBIDDEN_ACCESS_KEYS)
            if bad:
                P.append("(4) rule %r carries forbidden access key(s) %s — access is an `open_register` "
                         "`kind: access` entry only, never inside a rule row" % (rid, bad))
            if any(node.get("kind") == "access" for node in _walk_dicts(rule)):
                P.append("(4) rule %r carries a `kind: access` marker at some depth inside a rule row — access "
                         "lives only in `open_register`, never nested anywhere in a rule row" % rid)
            # (2) open placement: a needs_more_information rule has a matching value / rule / scope register entry
            stamps = _stamp_refs(rule.get("open_stamp"))
            if es == "needs_more_information":
                open_ids = {o.get("id") for k in STAMP_TARGET_KINDS for o in open_register_by_kind(policy, k)}
                if not (stamps & open_ids):
                    P.append("(2) rule %r is stamped needs_more_information but no `open_register` "
                             "entry of kind %s is referenced by its `open_stamp` %r"
                             % (rid, " / ".join(STAMP_TARGET_KINDS), sorted(stamps)))
            # (3) inherited-open stamping: any operative token that is index-open forces an open_stamp naming
            #     a present register entry
            for t in (rule.get("operative_tokens") if isinstance(rule.get("operative_tokens"), list) else []):
                if t in idx_open:
                    if not stamps:
                        P.append("(3) rule %r binds index-open token %r but carries no `open_stamp` — an "
                                 "inherited open must be stamped" % (rid, t))
                    elif not (stamps & reg_ids):
                        P.append("(3) rule %r binds index-open token %r; its `open_stamp` %r names no present "
                                 "`open_register` entry" % (rid, t, sorted(stamps)))

    # ---- 2 (rule opens) + 4 (access-open stamping): open_register split by kind ----
    for o in (policy.get("open_register") if isinstance(policy.get("open_register"), list) else []):
        if not isinstance(o, dict):
            P.append("(2) an `open_register` entry is not a mapping")
            continue
        kind = o.get("kind")
        if kind in ("rule", "scope"):
            if not _nonempty(o.get("follow_on")):
                P.append("(2) open_register `kind: %s` entry %r carries no non-empty `follow_on` — a "
                         "rule/policy or scope open names the route that resolves it" % (kind, o.get("id")))
            if not _nonempty(o.get("closure_route")):
                P.append("(2) open_register `kind: %s` entry %r carries no non-empty `closure_route`"
                         % (kind, o.get("id")))
        elif kind == "access":
            if o.get("state") != "Assumed":
                P.append("(4) open_register `kind: access` entry %r `state` is %r — access is stamped "
                         "`Assumed` only" % (o.get("id"), o.get("state")))

    # ---- 5 no interim/default/fallback ANYWHERE ----
    banned = sorted(set(_walk_keys(policy)) & BANNED_INTERIM_KEYS)
    if banned:
        P.append("(5) interim/default/fallback key(s) present %s — the stage authors no interim, default, or "
                 "fallback for anything stamped open (FM-18)" % banned)

    # ---- 6 authoritative only under a write grant, on gates + dispatch ----
    for g in (policy.get("gates") if isinstance(policy.get("gates"), list) else []):
        if isinstance(g, dict):
            prob = authority_problem(g, "gate %r" % (g.get("gate_id"),))
            if prob:
                P.append(prob)
    disp = policy.get("dispatch")
    if isinstance(disp, dict):
        prob = authority_problem(disp, "`dispatch`")
        if prob:
            P.append(prob)

    # ---- 7 governance stamping ----
    for gr in (policy.get("guardrails") if isinstance(policy.get("guardrails"), list) else []):
        if not isinstance(gr, dict):
            continue
        if gr.get("evidence_state") not in CANONICAL_FOUR:
            P.append("(7) guardrail %r `evidence_state` is %r — must be one of the canonical four"
                     % (gr.get("id"), gr.get("evidence_state")))
        if not (_nonempty(gr.get("source")) or _nonempty_list(gr.get("source"))):
            P.append("(7) guardrail %r carries no non-empty `source`" % gr.get("id"))
    for tr in (policy.get("escalation_triggers") if isinstance(policy.get("escalation_triggers"), list) else []):
        if not isinstance(tr, dict):
            continue
        if tr.get("evidence_state") not in CANONICAL_FOUR:
            P.append("(7) escalation_trigger %r `evidence_state` is %r — must be one of the canonical four"
                     % (tr.get("trigger"), tr.get("evidence_state")))
        if not _nonempty_list(tr.get("carried_by")):
            P.append("(7) escalation_trigger %r carries no non-empty `carried_by`" % tr.get("trigger"))
    for sc in (policy.get("special_cases") if isinstance(policy.get("special_cases"), list) else []):
        if not isinstance(sc, dict):
            continue
        if sc.get("evidence_state") not in CANONICAL_FOUR:
            P.append("(7) special_case %r `evidence_state` is %r — must be one of the canonical four"
                     % (sc.get("case"), sc.get("evidence_state")))
        if not _nonempty_list(sc.get("carried_by")):
            P.append("(7) special_case %r carries no non-empty `carried_by`" % sc.get("case"))

    return P


def main(argv):
    verbose = False
    rest = []
    for a in argv:
        if a in ("--verbose", "-v"):
            verbose = True
        else:
            rest.append(a)
    if len(rest) != 2:
        die("usage: check-rule-honesty-stamp.sh [--verbose] DECISION_POLICY TOKEN_INDEX", 2)
    policy_path, index_path = rest
    policy = load_one_yaml(policy_path, "decision policy")
    index = load_one_yaml(index_path, "token index")
    if not isinstance(index, dict):
        die("token index is not a mapping", 2)

    problems = validate(policy, index)
    if verbose:
        sys.stderr.write("parse engine: pyyaml safe_load (serves both the policy and the token index)\n")
    if problems:
        die("rule honesty-stamp check FAILED for %s:\n  - %s" % (policy_path, "\n  - ".join(problems)), 1)
    print("PASS: every rule sourced + evidence-stamped from the canonical four with a non-empty "
          "operative_tokens list; opens placed by kind; inherited opens stamped; access only as an Assumed "
          "kind:access register entry; no default/interim/fallback; authoritative only under a WG-n write grant; "
          "governance entries stamped. (Structure only — semantic honesty is the review's job.)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
