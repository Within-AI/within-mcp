"""Observed claims need evidence: the hook `check-evidence-freeze.py` calls for each stage file.

Any object in the stage file stamped `evidence_state: "Observed"` (a graph node or edge, a decision-table row, a
set-map link, actor or object state, a row of a Markdown map table, which the hook reads as
`{"tables": [{"rows": [...]}]}`) must carry at least one ref in its own `source_refs[]` or
`evidence_refs[]`. Fails:
  observed-without-evidence   the claim has no ref at all.
A ref that is present but does not resolve is the hook's own `ref-unresolved` (or finer, pending, not-admitted)
finding, so it is not reported twice here; an Observed claim passes only when its refs resolve. Ref shapes were
already checked by the hook (a setup error), so every ref here is a non-empty string.
"""

REF_LISTS = ("source_refs", "evidence_refs")
OBSERVED = "observed"


def check(doc, stage, index):
    """[{rule, where, detail}] for each claim stamped Observed with no evidence ref in `doc`.

    `doc` is the parsed stage file, `stage` the stage key and `index` the set of ids a ref may
    resolve to (snapshot index plus admitted addendum entries).
    """
    findings = []
    for where, kind, claim in observed_claims(doc):
        label = "%s %s" % (kind, claim.get("id")) if isinstance(claim.get("id"), str) else kind
        refs = own_refs(claim)
        if not refs:
            findings.append(_finding("observed-without-evidence", where,
                                     "%s is stamped Observed in the %s file but carries no evidence ref" % (label, stage)))
    return findings


def observed_claims(node, where="", parent_key=""):
    """[(where, kind, claim)] for every object stamped Observed anywhere in `node`."""
    found = []
    if isinstance(node, dict):
        state = node.get("evidence_state")
        if isinstance(state, str) and state.strip().lower() == OBSERVED:
            found.append((where or "/", _kind(where, parent_key), node))
        for key, value in node.items():
            if key not in REF_LISTS:
                found.extend(observed_claims(value, "%s/%s" % (where, key), key))
    elif isinstance(node, list):
        for i, value in enumerate(node):
            found.extend(observed_claims(value, "%s/%d" % (where, i), parent_key))
    return found


def own_refs(claim):
    """The refs in the claim's own source_refs[] / evidence_refs[] lists."""
    refs = []
    for key in REF_LISTS:
        value = claim.get(key)
        if isinstance(value, list):
            for item in value:
                ref = item.get("source_id") if isinstance(item, dict) else item
                if isinstance(ref, str) and ref.strip():
                    refs.append(ref)
    return refs


def _kind(where, parent_key):
    """A readable name for the claim, from the list that holds it."""
    if "/graph/" in where + "/":
        names = {"nodes": "graph node", "edges": "graph edge", "rows": "decision-table row"}
        return names.get(parent_key, "graph item")
    names = {"links": "set-map link", "actors": "actor", "object_states": "object state", "rows": "map row",
             "processes": "process", "work_objects": "work object"}
    return names.get(parent_key, "claim")


def _finding(rule, where, detail):
    return {"rule": rule, "where": where, "detail": detail}
