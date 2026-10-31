#!/usr/bin/env python3
'''t761 -- a FLOW node's worker name is a foreign key to ROLES.json.

Why this exists: a lane is created today by writing a role into
projects/local-inference/models/ROLES.json and code in the dispatcher. Nothing compares a
FLOW.json node's `worker` string with a role key/name, so the chart and the store can disagree in
both directions with no validator to say which is wrong (RCA workspace/rca/16c0fc02b191.md: the
Senior lane, added 2026-09-28, has no chart node).

role_store.check() (scripts/role_store.py, `def check` at line 204) compares role task lists with
FLOW.json task IDS, checks skill kinds and the generated SERVING.json block -- and never reads a
node's worker string.

Everything below is seeded inside a temp dir; the live ROLES.json, FLOW.json and SERVING.json are
never written. RED on main for one reason: check() reports no worker-name problem for any seeded
chart, so every 'is reported' assertion below gets an empty list and fails. E1/E2 pass on main by
design: they are the guard that the check is not a blanket warning.
'''
import json
import sys
import tempfile
from pathlib import Path

sys.path[:0] = [str(Path(__file__).resolve().parents[2] / 'scripts')]
import role_store as rs  # noqa: E402

PASSED = FAILED = 0


def report(name, got, want):
    global PASSED, FAILED
    ok = got == want
    PASSED += 1 if ok else 0
    FAILED += 0 if ok else 1
    print(('PASS' if ok else 'FAIL') + '  ' + name
          + ('' if ok else '  (got ' + repr(got) + ', want ' + repr(want) + ')'))


def store_doc():
    '''The store as the live one is shaped: model roles Senior, Manager, Frontline (key coder)
    and a person role. Keys and names differ on purpose -- the FK must accept both.'''
    return {'roles': {
        'senior': {'name': 'Senior', 'kind': 'model', 'model': 'swift', 'tasks': []},
        'manager': {'name': 'Manager', 'kind': 'model', 'model': 'swift', 'tasks': []},
        'coder': {'name': 'Frontline', 'kind': 'model', 'model': 'swift', 'tasks': []},
        'claude': {'name': 'Claude', 'kind': 'person', 'signature': 'Claude - Code', 'tasks': []},
    }}


def node(tid, worker, kind='role'):
    '''A FLOW task node; the fields check() needs are id, worker and worker_kind.'''
    return {'id': tid, 'name': tid, 'worker': worker, 'worker_kind': kind, 'command': '',
            'queue': '', 'inputs': [], 'outputs': [], 'outcomes': [], 'triggers': {},
            'note': '', 'ends': [], 'status': 'active'}


def check_against(doc, tasks):
    '''role_store.check() against a seeded store/chart/serving inside a temp dir.

    The SERVING.json copy is written from rs.serving_block(doc) so it AGREES with the store:
    every problem string check() then returns is a worker-name problem, nothing else. rs.FLOW is
    repointed for the call and restored in a finally, so no new parameter is needed and no live
    file is written.'''
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        store = tmp / 'ROLES.json'
        flow = tmp / 'FLOW.json'
        serving = tmp / 'SERVING.json'
        store.write_text(json.dumps(doc), encoding='utf-8')
        flow.write_text(json.dumps({'tasks': tasks}), encoding='utf-8')
        serving.write_text(json.dumps({'roles': rs.serving_block(doc)}), encoding='utf-8')
        real = rs.FLOW
        rs.FLOW = flow
        try:
            return rs.check(rs.load(store), serving=serving)
        finally:
            rs.FLOW = real


def named_in(problems, *needles):
    '''The problem strings that mention every needle, case-insensitively.'''
    return [p for p in problems if all(n.lower() in p.lower() for n in needles)]


def test_off_chart_worker_string_is_reported():
    '''The chart names a lane that is no role: 'Ghost' on node console. All three model roles are
    named here, so the only finding must be the off-chart worker.'''
    p = check_against(store_doc(), [node('scoping', 'Manager'), node('preflight', 'Senior'),
                                    node('building', 'Frontline'), node('console', 'Ghost')])
    report('A a worker_kind=role node naming a string that is no role key/name is reported',
           len(p), 1)
    report('A  the finding names that node AND its worker string, and no other node',
           [bool(named_in(p, 'console', 'Ghost')),
            [x for x in p if 'scoping' in x.lower() or 'manager' in x.lower()]],
           [True, []])


def test_model_role_named_by_no_node_is_reported():
    '''The other direction: a model role the chart never names. This is the live finding --
    ROLES.json has an Auditor role and no FLOW node names it.'''
    p = check_against(store_doc(), [node('scoping', 'Manager'), node('building', 'Frontline'),
                                    node('merge_review', 'Claude', kind='person')])
    report('B a model role that no FLOW node names is reported', len(p), 1)
    report('B  the finding names that role (Senior, key senior), and names no other role',
           [bool(named_in(p, 'senior')),
            [x for x in p if 'manager' in x.lower() or 'frontline' in x.lower()]],
           [True, []])


def test_a_role_name_on_a_non_role_node_is_not_a_link_to_it():
    '''The FK is a foreign key, not a substring scan: a SCRIPT node carrying a role's name must
    NOT satisfy the key. The whole point is a lane claimed off-chart, so only worker_kind 'role'
    counts in both directions. Senior stays un-named here.'''
    p = check_against(store_doc(), [node('scoping', 'Manager'), node('building', 'Frontline'),
                                    node('intake', 'Senior', kind='script')])
    report('C a script node carrying a role name is not a chart link to it', len(p), 1)
    report('C  the finding names Senior', bool(named_in(p, 'senior')), True)


def test_two_off_chart_worker_strings_are_reported_separately():
    '''The two strings the live FLOW.json actually carries, so a single repeated placeholder
    cannot satisfy this and one blanket warning fails it: one finding per offending node.'''
    p = check_against(store_doc(), [node('scoping', 'Manager'), node('preflight', 'Senior'),
                                    node('building', 'Frontline'),
                                    node('console', 'any role'),
                                    node('assigned_items', 'the assigned model role')])
    report('D two distinct off-chart worker strings give one finding per node',
           (len(p), len(named_in(p, 'console', 'any role')),
            len(named_in(p, 'assigned_items', 'the assigned model role'))),
           (2, 1, 1))


def test_a_consistent_chart_reports_nothing():
    '''The check must not be a blanket warning, and a role named by its store key passes.'''
    p = check_against(store_doc(), [node('scoping', 'Manager'), node('preflight', 'Senior'),
                                    node('building', 'Frontline'),
                                    node('merge_review', 'Claude', kind='person')])
    report('E1 a chart that names a real role for every role node, and names every model role, is clean',
           p, [])
    q = check_against(store_doc(), [node('scoping', 'manager'), node('preflight', 'senior'),
                                    node('building', 'coder')])
    report('E2 a worker naming a role by its store key is accepted', q, [])


if __name__ == '__main__':
    for fn in (test_off_chart_worker_string_is_reported,
               test_model_role_named_by_no_node_is_reported,
               test_a_role_name_on_a_non_role_node_is_not_a_link_to_it,
               test_two_off_chart_worker_strings_are_reported_separately,
               test_a_consistent_chart_reports_nothing):
        fn()
    print('  ' + str(PASSED) + ' passed, ' + str(FAILED) + ' failed')
    sys.exit(1 if FAILED else 0)
