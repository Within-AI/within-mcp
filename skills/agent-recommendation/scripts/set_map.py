#!/usr/bin/env python3
"""Set map v0.1: how a recommended set of processes fits together. Python standard library only; no network.

The set map is the structured form of the links Agent Recommendations finds between processes (work objects, their states,
typed links with evidence strength, context neighbours and open points). It lives at current_state.set_map in the report
data, and a copy scoped to the chosen solution's processes lives at verdict.design_brief.set_map, which Future State Design
reads. current_state.relations[] stays and is a view of the set map: every relations row has exactly one set-map link.
Performers that are not processes (an outside party, a rules bot, an AI automation, other) are listed in actors[] (v0.1);
the v0 key external_parties was renamed to actors and is refused, never mapped.

    python3 -I -B set_map.py check --input report.json
    python3 -I -B set_map.py scope --input report.json --solution <solution id> [--output scoped.json]
    python3 -I -B set_map.py summary --input report.json [--solution <solution id>]
"""
import argparse
import copy
import json
import re
import sys
from pathlib import Path

ID = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$')
DATE = re.compile(r'^\d{4}-\d{2}-\d{2}$')
KINDS = ('handoff', 'variant_entry', 'parent_child', 'lifecycle_continuation', 'decision_gate', 're_entry', 'overlap',
         'analogous_work', 'shared_knowledge', 'shared_team_or_system')
FLOW_KINDS = {'handoff', 'variant_entry', 'parent_child', 'lifecycle_continuation', 'decision_gate', 're_entry'}
LEVELS = ('shared_instances', 'step_state', 'trigger_end_text', 'same_capture_session', 'dependency_field', 'hierarchy_scope',
          'shared_team_or_system')
STATES = ('Observed', 'Assumed', 'Contradictory', 'needs_more_information')
ROLES = ('entry_variant', 'main_step', 'closing_step', 'upkeep', 'gate', 'other')
TOUCHES = ('gate', 'parent', 'continuation', 'upkeep', 'alternative_outcome', 'upstream', 'other')
OBJECT_RELATIONS = ('parent_of', 'child_of', 'creates', 'record_of', 'created_by', 'applied_to')
VERSIONS = ('0', '0.1')
ACTOR_KINDS = ('external_party', 'rules_automation', 'ai_automation', 'other')
RELATION_TYPES = ('analogous_work', 'shared_knowledge', 'observed_handoff', 'proposed_bridge')
REQUIRED = ('set_map_version', 'set_id', 'title', 'read_at', 'processes', 'work_objects', 'object_states', 'links',
            'context_neighbours', 'open_points')
LEVEL_TEXT = {'shared_instances': 'shared cases', 'step_state': 'step states match', 'trigger_end_text': 'trigger and end text',
              'same_capture_session': 'same capture session', 'dependency_field': 'dependency field only',
              'hierarchy_scope': 'hierarchy position only', 'shared_team_or_system': 'shared team or system only'}
KIND_TEXT = {'handoff': 'hands over', 'variant_entry': 'are alternative ways into', 'parent_child': 'is the parent of the work in',
             'lifecycle_continuation': 'is continued by', 'decision_gate': 'decides whether this runs:', 're_entry': 'sends work back to'}


class SetMapError(ValueError):
    pass


def need(ok, message):
    if not ok:
        raise SetMapError(message)


def _obj(v, w):
    need(isinstance(v, dict), f'{w}: object required')
    return v


def _list(v, w, nonempty=False):
    need(isinstance(v, list), f'{w}: list required')
    need(not nonempty or len(v) > 0, f'{w}: nonempty list required')
    return v


def _text(v, w):
    need(isinstance(v, str) and bool(v.strip()), f'{w}: text required')
    return v


def _id(v, w):
    need(isinstance(v, str) and bool(ID.fullmatch(v)), f'{w}: identifier required (letters, digits, then . _ : -; at most 64)')
    return v


def _enum(v, allowed, w):
    need(v in allowed, f'{w}: one of {", ".join(str(a) for a in allowed)} required')
    return v


def _ids(v, w, nonempty=False):
    for i, x in enumerate(_list(v, w, nonempty)):
        _id(x, f'{w}[{i}]')
    return v


def _refs(v, w, sources):
    for i, r in enumerate(_list(v, w)):
        rw = f'{w}[{i}]'
        _obj(r, rw)
        _text(r.get('source_id'), rw + '.source_id')
        if sources is not None:
            need(r['source_id'] in sources, f'{rw}.source_id: unknown source {r["source_id"]}')
        _text(r.get('locator'), rw + '.locator')
        need(isinstance(r.get('read_at'), str) and bool(DATE.fullmatch(r['read_at'])), f'{rw}.read_at: date YYYY-MM-DD required')


def renamed(sm, where='set_map'):
    """Refuse the v0 key external_parties: it was renamed to actors in v0.1 and is never mapped silently."""
    need(not (isinstance(sm, dict) and 'external_parties' in sm),
         f'{where}.external_parties: renamed to actors in set map v0.1; list each one in actors[] '
         'with a kind (external_party, rules_automation, ai_automation or other)')


def validate(sm, where='set_map', sources=None):
    """Shape and reference checks for one set map. sources: the report's evidence source ids, or None outside a report."""
    _obj(sm, where)
    renamed(sm, where)
    for k in REQUIRED:
        need(k in sm, f'{where}.{k}: required')
    need(sm['set_map_version'] in VERSIONS, f'{where}.set_map_version: "0" or "0.1" required')
    _id(sm['set_id'], where + '.set_id')
    _text(sm['title'], where + '.title')
    need(isinstance(sm['read_at'], str) and bool(DATE.fullmatch(sm['read_at'])), f'{where}.read_at: date YYYY-MM-DD required')
    procs, leads = {}, []
    for i, p in enumerate(_list(sm['processes'], where + '.processes', True)):
        pw = f'{where}.processes[{i}]'
        _obj(p, pw)
        pid = _id(p.get('process_id'), pw + '.process_id')
        need(pid not in procs, f'{pw}.process_id: duplicate process {pid}')
        procs[pid] = p
        _text(p.get('title'), pw + '.title')
        _enum(p.get('role'), ROLES, pw + '.role')
        for k in ('trigger', 'end'):
            _text(p.get(k), f'{pw}.{k}')
        for k in ('reads_states', 'writes_states'):
            _ids(p.get(k), f'{pw}.{k}')
        if 'lead' in p:
            need(type(p['lead']) is bool, f'{pw}.lead: true or false required')
            if p['lead']:
                leads.append(pid)
        if 'membership_of' in p:
            _ids(p['membership_of'], pw + '.membership_of')
    need(len(leads) <= 1, f'{where}.processes: at most one lead')
    if sm.get('lead_process') is not None:
        need(sm['lead_process'] in procs, f'{where}.lead_process: {sm["lead_process"]} is not in processes')
        need(not leads or leads == [sm['lead_process']], f'{where}.lead_process: disagrees with the process marked lead')
    ids = set()

    def unique(x, w):
        i = _id(x.get('id'), w + '.id')
        need(i not in ids, f'{w}.id: duplicate id {i}')
        ids.add(i)
        return i

    parties = set()
    for i, x in enumerate(_list(sm.get('actors', []), where + '.actors')):
        xw = f'{where}.actors[{i}]'
        _obj(x, xw)
        parties.add(unique(x, xw))
        _text(x.get('name'), xw + '.name')
        kind = _enum(x.get('kind'), ACTOR_KINDS, xw + '.kind')
        if kind == 'other' or 'kind_text' in x:
            _text(x.get('kind_text'), xw + '.kind_text')
        for k in ('system',):
            if k in x:
                need(isinstance(x[k], str), f'{xw}.{k}: text required')
        if 'writes_objects' in x:
            _ids(x['writes_objects'], xw + '.writes_objects')
        if 'evidence_state' in x:
            _enum(x['evidence_state'], STATES, xw + '.evidence_state')
        if 'evidence_refs' in x:
            _refs(x['evidence_refs'], xw + '.evidence_refs', sources)
    objects = {}
    for i, o in enumerate(_list(sm['work_objects'], where + '.work_objects')):
        ow = f'{where}.work_objects[{i}]'
        _obj(o, ow)
        objects[unique(o, ow)] = o
        _text(o.get('name'), ow + '.name')
        _text(o.get('system_of_record'), ow + '.system_of_record')
        _list(o.get('relations'), ow + '.relations')
    for i, o in enumerate(sm['work_objects']):
        for j, r in enumerate(o['relations']):
            rw = f'{where}.work_objects[{i}].relations[{j}]'
            _obj(r, rw)
            _enum(r.get('kind'), OBJECT_RELATIONS, rw + '.kind')
            need(r.get('object') in objects, f'{rw}.object: unknown work object {r.get("object")}')
            if 'evidence_state' in r:
                _enum(r['evidence_state'], STATES, rw + '.evidence_state')
    for i, x in enumerate(sm.get('actors', [])):
        for k, o in enumerate(x.get('writes_objects', [])):
            need(o in objects, f'{where}.actors[{i}].writes_objects[{k}]: unknown work object {o}')
    neighbours = {}
    for i, c in enumerate(_list(sm['context_neighbours'], where + '.context_neighbours')):
        cw = f'{where}.context_neighbours[{i}]'
        _obj(c, cw)
        unique(c, cw)
        pid = _id(c.get('process_id'), cw + '.process_id')
        need(pid not in procs, f'{cw}.process_id: {pid} is an in-set process; a process is a member or a context neighbour, not both')
        need(pid not in neighbours, f'{cw}.process_id: duplicate context neighbour {pid}')
        neighbours[pid] = c
        _text(c.get('title'), cw + '.title')
        _enum(c.get('touches'), TOUCHES, cw + '.touches')
        _ids(c.get('attach_to'), cw + '.attach_to', True)
        _enum(c.get('found_by'), LEVELS, cw + '.found_by')
        _enum(c.get('evidence_state'), STATES, cw + '.evidence_state')
        if 'evidence_refs' in c:
            _refs(c['evidence_refs'], cw + '.evidence_refs', sources)
    actors = set(procs) | set(neighbours) | parties
    states = {}
    for i, s in enumerate(_list(sm['object_states'], where + '.object_states')):
        sw = f'{where}.object_states[{i}]'
        _obj(s, sw)
        states[unique(s, sw)] = s
        need(s.get('object') in objects, f'{sw}.object: unknown work object {s.get("object")}')
        _text(s.get('name'), sw + '.name')
        need(type(s.get('order')) is int and s['order'] >= 1, f'{sw}.order: positive integer required')
        for k, a in enumerate(_ids(s.get('moved_in_by'), sw + '.moved_in_by')):
            need(a in actors, f'{sw}.moved_in_by[{k}]: {a} is not a process, context neighbour or actor')
        _enum(s.get('evidence_state'), STATES, sw + '.evidence_state')
        if 'evidence_refs' in s:
            _refs(s['evidence_refs'], sw + '.evidence_refs', sources)
    links = {}
    for i, l in enumerate(_list(sm['links'], where + '.links')):
        lw = f'{where}.links[{i}]'
        _obj(l, lw)
        links[unique(l, lw)] = l
        kind = _enum(l.get('kind'), KINDS, lw + '.kind')
        if 'flow' in l:
            need(l['flow'] is (kind in FLOW_KINDS), f'{lw}.flow: must be {str(kind in FLOW_KINDS).lower()} for {kind}')
        for end in ('from', 'to'):
            for k, a in enumerate(_ids(l.get(end), f'{lw}.{end}', True)):
                need(a in actors, f'{lw}.{end}[{k}]: {a} is not a process, context neighbour or actor')
        if l.get('object') is not None:
            need(l['object'] in objects, f'{lw}.object: unknown work object {l["object"]}')
        if l.get('state') is not None:
            need(l['state'] in states, f'{lw}.state: unknown object state {l["state"]}')
        if l.get('to_object') is not None:
            need(l['to_object'] in objects, f'{lw}.to_object: unknown work object {l["to_object"]}')
        if l.get('to_state') is not None:
            need(l.get('to_object') is not None, f'{lw}.to_state: needs to_object')
            need(l['to_state'] in states, f'{lw}.to_state: unknown object state {l["to_state"]}')
            need(states[l['to_state']]['object'] == l['to_object'], f'{lw}.to_state: {l["to_state"]} is not a state of {l["to_object"]}')
        if l.get('direction_evidence_state') is not None:
            _enum(l['direction_evidence_state'], STATES, lw + '.direction_evidence_state')
        _enum(l.get('evidence_level'), LEVELS, lw + '.evidence_level')
        est = _enum(l.get('evidence_state'), STATES, lw + '.evidence_state')
        _refs(l.get('evidence_refs'), lw + '.evidence_refs', sources)
        _text(l.get('rationale'), lw + '.rationale')
        if l['evidence_level'] == 'shared_instances':
            need(type(l.get('shared_instance_count')) is int and l['shared_instance_count'] >= 1,
                 f'{lw}.shared_instance_count: a positive count is required at shared_instances')
            need(est != 'Assumed', f'{lw}.evidence_state: a link backed by shared instances is never Assumed')
        if est in ('needs_more_information', 'Contradictory'):
            need(bool(l.get('open_point_ids')), f'{lw}.open_point_ids: a {est} link names its open point')
        if l.get('relation_type') is not None:
            _enum(l['relation_type'], RELATION_TYPES, lw + '.relation_type')
    opens = {}
    for i, o in enumerate(_list(sm['open_points'], where + '.open_points')):
        ow = f'{where}.open_points[{i}]'
        _obj(o, ow)
        opens[unique(o, ow)] = o
        _text(o.get('text'), ow + '.text')
        _enum(o.get('evidence_state'), STATES, ow + '.evidence_state')
        _ids(o.get('about'), ow + '.about', True)
    known = ids | actors
    for pid, p in procs.items():
        for k in ('reads_states', 'writes_states'):
            for x in p[k]:
                need(x in states, f'{where}.processes[{pid}].{k}: unknown object state {x}')
    for c in sm['context_neighbours']:
        for x in c['attach_to']:
            need(x in states or x in actors, f'{where}.context_neighbours[{c["id"]}].attach_to: {x} is not a state or process')
    for o in sm['open_points']:
        for x in o['about']:
            need(x in known, f'{where}.open_points[{o["id"]}].about: unknown id {x}')
    for coll in ('links', 'object_states'):
        for x in sm[coll]:
            for op in _ids(x.get('open_point_ids', []), f'{where}.{coll}[{x["id"]}].open_point_ids'):
                need(op in opens, f'{where}.{coll}[{x["id"]}].open_point_ids: unknown open point {op}')
    if 'scope' in sm:
        sc = _obj(sm['scope'], where + '.scope')
        _id(sc.get('from_set_id'), where + '.scope.from_set_id')
        _id(sc.get('solution_id'), where + '.scope.solution_id')
    for i, c in enumerate(_list(sm.get('checks', []), where + '.checks')):
        _obj(c, f'{where}.checks[{i}]')
        _text(c.get('check'), f'{where}.checks[{i}].check')
        _enum(c.get('result'), ('pass', 'open', 'fail'), f'{where}.checks[{i}].result')
    return sm


def expected_relation_type(link):
    """The current_state.relations[] type a set-map link maps to (set map v0, 'How it relates'), or None."""
    k = link['kind']
    if k == 'handoff':
        return 'observed_handoff' if link['evidence_level'] == 'shared_instances' else 'proposed_bridge'
    if k in ('analogous_work', 'shared_knowledge'):
        return k
    if k == 'variant_entry':
        return 'analogous_work'
    return None


def validate_in_report(d, where='current_state.set_map'):
    """current_state.set_map against the rest of the report: processes, relations[] and process memberships."""
    sm = d['current_state']['set_map']
    sources = {s['id'] for s in d['evidence']['sources']}
    validate(sm, where, sources)
    index = {p['id'] for p in d['current_state']['process_index']}
    procs = {p['process_id']: p for p in sm['processes']}
    for pid in procs:
        need(pid in index, f'{where}.processes: {pid} is not in current_state.process_index')
    problems = {p['id']: p for p in d['problems']}
    for p in d['problems']:
        for m in p['process_memberships']:
            need(m['process_id'] in procs, f'{where}.processes: {m["process_id"]} (a member of {p["id"]}) is missing')
    for pid, p in procs.items():
        for h in p.get('membership_of', []):
            # A refined report carries the Stage 1 set map forward, so membership_of may name a Stage 1 hypothesis this
            # report no longer holds. A hypothesis that is in this report must list the process.
            need(d['stage'] == 'refined' or h in problems, f'{where}.processes[{pid}].membership_of: unknown hypothesis {h}')
            if h in problems:
                need(pid in {m['process_id'] for m in problems[h]['process_memberships']},
                     f'{where}.processes[{pid}].membership_of: {h} does not list {pid} in its process_memberships')
    rels = {r['id']: r for r in d['current_state']['relations']}
    used = {}
    for l in sm['links']:
        rid = l.get('relation_id')
        if rid is None:
            continue
        lw = f'{where}.links[{l["id"]}].relation_id'
        need(rid in rels, f'{lw}: unknown relation {rid}')
        need(rid not in used, f'{lw}: relation {rid} is already the view of link {used.get(rid)}')
        used[rid] = l['id']
        r = rels[rid]
        want = expected_relation_type(l)
        need(want is not None, f'{lw}: a {l["kind"]} link has no relations[] row; set relation_id to null')
        need(r['type'] == want, f'{lw}: a {l["kind"]} link at {l["evidence_level"]} is a {want} row, but {rid} is {r["type"]}')
        if l.get('relation_type') is not None:
            need(l['relation_type'] == r['type'], f'{where}.links[{l["id"]}].relation_type: disagrees with {rid} ({r["type"]})')
        ends = set(l['from']) | set(l['to'])
        need({r['from_process'], r['to_process']} <= ends, f'{lw}: {rid} joins {r["from_process"]} and {r["to_process"]}, which this link does not')
    for rid in rels:
        need(rid in used, f'{where}.links: relations[] row {rid} has no set-map link (every row is a view of one link)')
    return sm


def _touch(link, inside_from):
    if inside_from:
        return 'continuation'
    return {'decision_gate': 'gate', 'parent_child': 'parent', 're_entry': 'upkeep'}.get(link['kind'], 'other')


def scope(sm, process_ids, lead=None, solution_id=None):
    """A copy of the set map scoped to the given processes. Every link that touches one of them is kept; a set process on its
    far end becomes a context neighbour, so a handoff target is never dropped. Deterministic; the producer may then edit it."""
    renamed(sm)
    keep = list(dict.fromkeys(process_ids))
    procs = {p['process_id']: p for p in sm['processes']}
    need(all(x in procs for x in keep), 'scope: every process must be in the set map: ' + ', '.join(x for x in keep if x not in procs))
    out = {k: copy.deepcopy(v) for k, v in sm.items() if k not in ('processes', 'links', 'context_neighbours', 'object_states',
                                                                  'work_objects', 'open_points', 'checks')}
    out['processes'] = []
    for x in keep:
        p = copy.deepcopy(procs[x])
        if lead is not None:
            p['lead'] = (x == lead)
        out['processes'].append(p)
    out['lead_process'] = lead if lead is not None else next((x for x in keep if procs[x].get('lead')), None)
    if out['lead_process'] is None:
        out.pop('lead_process')
    if solution_id is not None:
        out['scope'] = {'from_set_id': sm['set_id'], 'solution_id': solution_id}
    links = [copy.deepcopy(l) for l in sm['links'] if set(l['from'] + l['to']) & set(keep)]
    out['links'] = links
    rank = {k: i for i, k in enumerate(LEVELS)}
    labelling = sorted(links, key=lambda l: (l['kind'] not in FLOW_KINDS, rank[l['evidence_level']]))
    neighbours = {c['process_id']: copy.deepcopy(c) for c in sm['context_neighbours']}
    parties = {x['id'] for x in sm.get('actors', [])}
    actors = set(keep)
    for l in labelling:
        for end in ('from', 'to'):
            for a in l[end]:
                if a in keep:
                    continue
                actors.add(a)
                if a in parties:
                    continue
                if a not in neighbours and a in procs:
                    p = procs[a]
                    neighbours[a] = {'id': 'cn-' + a, 'process_id': a, 'title': p['title'],
                                     'touches': _touch(l, l['kind'] in FLOW_KINDS and end == 'to' and bool(set(l['from']) & set(keep))),
                                     'attach_to': [l['state']] if l.get('state') else list(dict.fromkeys(x for x in l['from'] + l['to'] if x in keep)) or [keep[0]],
                                     'summary': 'In the recommended set but outside this design: ' + (p.get('role_text') or p['role']),
                                     'found_by': l['evidence_level'], 'evidence_state': l['evidence_state'],
                                     'evidence_refs': copy.deepcopy(l['evidence_refs'])}
    state_ids = {l['state'] for l in links if l.get('state')}
    for x in keep:
        state_ids |= set(procs[x]['reads_states']) | set(procs[x]['writes_states'])
    for c in sm['context_neighbours']:
        if set(c['attach_to']) & (set(keep) | state_ids):
            actors.add(c['process_id'])
    state_ids |= {l['to_state'] for l in links if l.get('to_state')}
    states = [copy.deepcopy(s) for s in sm['object_states'] if s['id'] in state_ids]
    for s in states:
        actors |= set(s['moved_in_by'])
    out['context_neighbours'] = []
    for a in sorted(actors - set(keep) - parties):
        if a in neighbours:
            c = neighbours[a]
            c['attach_to'] = list(dict.fromkeys(x for x in c['attach_to'] if x in state_ids or x in keep)) or \
                ([x for x in state_ids if a in next(s for s in states if s['id'] == x)['moved_in_by']][:1] or [keep[0]])
            out['context_neighbours'].append(c)
        elif a in procs:
            p = procs[a]
            out['context_neighbours'].append({'id': 'cn-' + a, 'process_id': a, 'title': p['title'], 'touches': 'other',
                                              'attach_to': [s['id'] for s in states if a in s['moved_in_by']][:1] or [keep[0]],
                                              'summary': 'In the recommended set but outside this design.',
                                              'found_by': 'step_state', 'evidence_state': 'Observed'})
    if sm.get('actors'):
        used = actors & parties
        out['actors'] = [copy.deepcopy(x) for x in sm['actors'] if x['id'] in used]
    obj_ids = {s['object'] for s in states} | {l[k] for l in links for k in ('object', 'to_object') if l.get(k)} | \
        {o for x in out.get('actors', []) for o in x.get('writes_objects', [])}
    grow = True
    while grow:
        before = len(obj_ids)
        for o in sm['work_objects']:
            if o['id'] in obj_ids:
                obj_ids |= {r['object'] for r in o['relations']}
        grow = len(obj_ids) != before
    out['work_objects'] = [copy.deepcopy(o) for o in sm['work_objects'] if o['id'] in obj_ids]
    out['object_states'] = states
    kept_ids = {x['id'] for k in ('links', 'object_states', 'work_objects', 'context_neighbours') for x in out[k]} | \
        {c['process_id'] for c in out['context_neighbours']} | set(keep) | {x['id'] for x in out.get('actors', [])}
    named = {op for k in ('links', 'object_states') for x in out[k] for op in x.get('open_point_ids', [])}
    out['open_points'] = []
    for o in sm['open_points']:
        core = set(keep) | {x['id'] for k in ('links', 'object_states', 'work_objects') for x in out[k]}
        if o['id'] in named or set(o['about']) & core:
            o = copy.deepcopy(o)
            o['about'] = [x for x in o['about'] if x in kept_ids] or [keep[0]]
            out['open_points'].append(o)
    return out


def check_scoped(full, scoped, brief_processes, where):
    """The brief's scoped set map covers the brief's processes and keeps every link that touches them."""
    want = [x['process_id'] for x in brief_processes]
    got = [p['process_id'] for p in scoped['processes']]
    need(sorted(want) == sorted(got), f'{where}.processes: must be exactly the brief\'s processes ({", ".join(want)}), not {", ".join(got)}')
    lead = next(x['process_id'] for x in brief_processes if x['lead'])
    need(scoped.get('lead_process', lead) == lead, f'{where}.lead_process: must be the brief\'s lead {lead}')
    have = {l['id'] for l in scoped['links']}
    for l in full['links']:
        if set(l['from'] + l['to']) & set(want):
            need(l['id'] in have, f'{where}.links: {l["id"]} ({l["kind"]}) touches a brief process and must be kept, '
                                  'with its far end as a process or context neighbour')
    return scoped


def _name(sm, a):
    for p in sm['processes']:
        if p['process_id'] == a:
            return p['title']
    for c in sm['context_neighbours']:
        if c['process_id'] == a:
            return c['title'] + ' (context)'
    for x in sm.get('actors', []):
        if x['id'] == a:
            return x['name']
    return a


def summary(sm):
    """Plain sentences from the flow links, for how_processes_connect and the design prompt. Generated, never edited."""
    renamed(sm)
    states = {s['id']: s['name'] for s in sm['object_states']}
    objects = {o['id']: o['name'] for o in sm['work_objects']}
    order = {k: i for i, k in enumerate(('decision_gate', 'parent_child', 'variant_entry', 'handoff', 'lifecycle_continuation', 're_entry'))}
    parts = []
    flow = [l for l in sm['links'] if l['kind'] in FLOW_KINDS]
    disputed = [l for l in flow if l['evidence_state'] == 'Contradictory']

    def at(l):
        if not l.get('object'):
            return ''
        return ' the ' + objects[l['object']] + (' at "' + states[l['state']] + '"' if l.get('state') else '')

    def ev(l, count=True):
        e = l['evidence_state'] if l['evidence_state'] != 'needs_more_information' else 'needs more information'
        n = l.get('shared_instance_count')
        return e + ', ' + LEVEL_TEXT[l['evidence_level']] + (f': {n}' if count and n and l['evidence_level'] == 'shared_instances' else '')

    def names(ids):
        n = [_name(sm, a) for a in ids]
        return n[0] if len(n) == 1 else ', '.join(n[:-1]) + ' and ' + n[-1]

    groups = {}
    for l in sorted((l for l in flow if l not in disputed), key=lambda l: order[l['kind']]):
        key = (l['kind'], tuple(l['to']), l.get('object'), l.get('state'), l['evidence_state']) if l['kind'] == 'handoff' else (l['id'],)
        groups.setdefault(key, []).append(l)
    for ls in groups.values():
        l = ls[0]
        if l['kind'] == 'handoff':
            frm = names([a for x in ls for a in x['from']])
            counts = [x.get('shared_instance_count') for x in ls]
            e = ev(l, False) + (': ' + ', '.join(str(c) for c in counts) if all(counts) and l['evidence_level'] == 'shared_instances' else '')
            verb = 'hand over' if len(ls) > 1 else KIND_TEXT['handoff']
            parts.append(f'{frm} {verb}{at(l) or " the output"} to {names(l["to"])} ({e}).')
        elif l['kind'] == 'variant_entry':
            parts.append(f'{names(l["from"])} {KIND_TEXT["variant_entry"]}{at(l) or " the same work"}, then {names(l["to"])} ({ev(l)}).')
        else:
            verb = KIND_TEXT[l['kind']]
            if len(l['from']) > 1:
                verb = {'decides whether this runs:': 'decide whether this runs:', 'is the parent of the work in': 'are the parents of the work in',
                        'is continued by': 'are continued by', 'sends work back to': 'send work back to'}.get(verb, verb)
            parts.append(f'{names(l["from"])} {verb} {names(l["to"])} ({ev(l)}).')
    for l in (l for l in sm['links'] if l['kind'] == 'overlap'):
        who = l['from'] + [a for a in l['to'] if a not in l['from']]
        parts.append(f'{names(who)} overlap: {"both" if len(who) == 2 else "all"} do the same work on{at(l) or " the same object"}; '
                     f'the boundary is open ({ev(l)}).')
    if not parts:
        return ''
    if disputed:
        parts.append(f'{len(disputed)} disputed link' + ('s are' if len(disputed) != 1 else ' is') + ' left out here.')
    n = len(sm['open_points'])
    if n:
        parts.append(f'{n} open point' + ('s' if n != 1 else '') + ' in the set map.')
    return ' '.join(parts)


def _brief_of(d, sid):
    for p in d['problems']:
        for s in p['solutions']:
            if s['id'] == sid:
                return p, s
    raise SetMapError(f'unknown solution {sid}')


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('command', choices=('check', 'scope', 'summary'))
    ap.add_argument('--input', required=True, type=Path)
    ap.add_argument('--solution')
    ap.add_argument('--output', type=Path)
    a = ap.parse_args()
    try:
        d = json.loads(a.input.read_text(encoding='utf-8'))
        sm = d['current_state'].get('set_map') if isinstance(d.get('current_state'), dict) else None
        need(sm is not None, 'current_state.set_map: absent')
        if a.command == 'check':
            validate_in_report(d)
            print('set map: PASS')
        elif a.command == 'scope':
            need(a.solution, 'scope needs --solution')
            p, s = _brief_of(d, a.solution)
            b = ((s.get('verdict') or {}).get('design_brief') or {})
            procs = [x['process_id'] for x in b.get('processes', [])] or [m['process_id'] for m in p['process_memberships']]
            lead = next((x['process_id'] for x in b.get('processes', []) if x.get('lead')), None)
            out = scope(sm, procs, lead, a.solution)
            validate(out, 'scoped set map')
            text = json.dumps(out, ensure_ascii=False, indent=2) + '\n'
            if a.output:
                a.output.write_text(text, encoding='utf-8')
                print(a.output)
            else:
                sys.stdout.write(text)
        else:
            if a.solution:
                _, s = _brief_of(d, a.solution)
                sm = s['verdict']['design_brief']['set_map']
            print(summary(sm))
    except (SetMapError, KeyError, OSError, json.JSONDecodeError) as e:
        ap.exit(2, f'Set map not accepted: {e}\n')


if __name__ == '__main__':
    main()
