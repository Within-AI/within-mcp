"""One reading of a register question settled by snapshot evidence."""

def closed_by_snapshot(item):
    applied = item.get("applied_from_snapshot") if isinstance(item, dict) else None
    applied = applied if isinstance(applied, list) else [applied] if isinstance(applied, dict) else []
    return any(isinstance(a, dict) and a.get("kind") == "answer" and a.get("closes") is True for a in applied)


def _values(value):
    return value if isinstance(value, list) else [value] if value is not None else []


def question_groups(items):
    """Lossless view projection, never a rewrite of register evidence.

    IDs identify an issue within a decision/kind. An exact nonempty raw-id set
    also identifies a renamed issue. Overlapping evidence, prose, rule links and
    display headings alone do not identify a question.
    """
    groups = []
    for item in items:
        if not isinstance(item, dict):
            continue
        scope = (item.get("decision"), item.get("kind"))
        raw = set(x for x in _values(item.get("raw_ids")) if isinstance(x, str) and x)
        matches = []
        for group in groups:
            for member in group["members"]:
                other = set(x for x in _values(member.get("raw_ids")) if isinstance(x, str) and x)
                same_id = bool(item.get("id")) and item.get("id") == member.get("id")
                if scope == (member.get("decision"), member.get("kind")) and (same_id or raw and raw == other):
                    matches.append(group)
                    break
        if not matches:
            groups.append({"members": [item]})
        else:
            target = matches[0]
            target["members"].append(item)
            for group in matches[1:]:
                target["members"].extend(group["members"])
                groups.remove(group)
    result = []
    for group in groups:
        members = group["members"]
        merged = dict(members[0])
        for field in ("raw_ids", "dependent_rules", "snapshot_refs", "applied_from_snapshot"):
            values = []
            for member in members:
                for value in _values(member.get(field)):
                    if value not in values:
                        values.append(value)
            if values or any(field in member for member in members):
                merged[field] = values
        group["item"] = merged
        result.append(group)
    return result


def internal_question(item, views=None):
    """Route declared builder-only or purely diagnostic residuals internally.

    Without an explicit builder-only declaration, business decisions, affected
    rules, mixed provenance and ratification stay visible. Missing authority is not evidence that a question is internal.
    """
    views = views if isinstance(views, dict) else {}
    if item.get("id") in views.get("builder", []) and item.get("id") not in views.get("business_owner", []):
        return True
    if item.get("decision") or item.get("dependent_rules") or item.get("kind") != "evidence":
        return False
    raw = item.get("raw_ids") or []
    return (item.get("source") in ("unreadable source", "recorded problem", "check finding")
            and bool(raw) and all(isinstance(r, str) and r.split(":", 1)[0] in
                                 ("unreadable", "recorded-problem", "check-finding") for r in raw))
