#!/usr/bin/env python3
'''t1114 oracle: an ON/OFF switch per role, recorded in the role store.

Jas, 2026-10-13: an on/off switch for each role; a role turned off finishes the task it is on and
then stops, and takes no new work. An OFF role keeps its model, device, tasks and lanes -- kept,
not deleted. The store is the only source of truth (t1101), so the switch lives in role_store,
where every reader sees it.

This file touches NO live state: the four write paths role_store uses (MODELS, STORE, SERVING,
LOG) are repointed into a temp dir, and HIVEMIND_NO_ROLE_SUITES=1 stops save launching
role_suites.py, which is a subprocess.
'''
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
os.environ['HIVEMIND_NO_ROLE_SUITES'] = '1'
import role_store as rs

WHO = 't1114 oracle'
DOC = {'roles': {
    'coder': {'name': 'Frontline', 'kind': 'model', 'model': 'swift', 'what': 'builds',
              'tasks': [{'task': 'building', 'interrupt': False, 'teamwork': False}],
              'tools': {}, 'attributes': {}, 'lanes': 4},
    'manager': {'name': 'Manager', 'kind': 'model', 'model': 'swift', 'what': 'scopes',
                'tasks': [{'task': 'building', 'interrupt': False, 'teamwork': False}],
                'tools': {}, 'attributes': {}, 'lanes': 1},
    'jas': {'name': 'Jas', 'kind': 'person', 'tasks': [{'task': 'building'}]}}}
FAILS = []
MISSING = [n for n in ('enabled', 'set_enabled') if not hasattr(rs, n)]


def check(what, cond):
    print(('  ok   ' if cond else '  FAIL ') + what)
    if not cond:
        FAILS.append(what)


class TempStore:
    '''role_store writes FOUR module-level paths: MODELS (locked() drops .roles.lock in it),
    STORE, SERVING and LOG. Repoint all four so nothing in the repo moves, and restore them.'''

    def __enter__(self):
        self.dir = Path(tempfile.mkdtemp(prefix='t1114-'))
        self.saved = (rs.MODELS, rs.STORE, rs.SERVING, rs.LOG)
        rs.MODELS = self.dir
        rs.STORE = self.dir / 'ROLES.json'
        rs.SERVING = self.dir / 'SERVING.json'
        rs.LOG = self.dir / 'ROLES-CHANGELOG.jsonl'
        rs._write_json(rs.STORE, json.loads(json.dumps(DOC)))
        rs._write_json(rs.SERVING, {'roles': {}, 'production_lanes': []})
        return self

    def __exit__(self, *exc):
        rs.MODELS, rs.STORE, rs.SERVING, rs.LOG = self.saved
        return False

    def store(self):
        return json.loads(rs.STORE.read_text(encoding='utf-8'))

    def serving(self):
        return json.loads(rs.SERVING.read_text(encoding='utf-8'))

    def rows(self):
        return [json.loads(x) for x in rs.LOG.read_text(encoding='utf-8').splitlines() if x.strip()]


def test_missing_api():
    for n in MISSING:
        check('role_store.%s() exists: the change this oracle asks for' % n, False)


def test_the_read_helper():
    if MISSING:
        return
    d = json.loads(json.dumps(DOC))
    check('no enabled key means ON: the field is not in ROLES.json today, so a missing key must '
          'not silence every role at once', rs.enabled(d, 'coder') is True)
    d['roles']['coder']['enabled'] = True
    check('enabled true reads as True', rs.enabled(d, 'coder') is True)
    d['roles']['coder']['enabled'] = False
    check('enabled false reads as False', rs.enabled(d, 'coder') is False)
    d['roles']['coder']['enabled'] = 'true'
    check('the string true reads as True, a form post is a string', rs.enabled(d, 'coder') is True)
    d['roles']['coder']['enabled'] = 'false'
    check('the string false reads as False, not as the truthy string it is', rs.enabled(d, 'coder') is False)
    check('a role key that is not in the store is OFF, never defaulted to on', rs.enabled(d, 'ghost') is False)
    check('a person role is readable too', rs.enabled(d, 'jas') is True)


def test_building_roles_skips_an_off_role():
    d = json.loads(json.dumps(DOC))
    d['roles']['coder']['enabled'] = False
    br = rs.building_roles(d)
    check('building_roles drops a role switched off', 'coder' not in br)
    check('building_roles keeps the role that is on', 'manager' in br)
    check('building_roles never returns a person role', 'jas' not in br)


def test_set_enabled_keeps_the_role_and_logs_it():
    with TempStore() as t:
        if MISSING:
            return
        n = rs.set_enabled('coder', False, WHO)
        before = json.loads(json.dumps(DOC))['roles']['coder']
        after = t.store()['roles']['coder']
        check('set_enabled writes enabled False', after.get('enabled') is False)
        check('an off role keeps its model', after.get('model') == before.get('model') == 'swift')
        check('an off role keeps its task list', after.get('tasks') == before['tasks'])
        check('an off role keeps its lanes', after.get('lanes') == before['lanes'] == 4)
        check('an off role is not deleted', 'coder' in t.store()['roles'])
        check('set_enabled returns the changelog number', n == len(t.rows()) == 1)
        check('the changelog row names the role', any('Frontline' in r['what'] for r in t.rows()))
        check('the changelog row says off', any('off' in r['what'].lower() for r in t.rows()))
        check('an off role is still in the generated SERVING roles block: kept, not deleted',
              'coder' in t.serving()['roles'])
        check('the generated SERVING production_lanes drops the off role',
              t.serving()['production_lanes'] == ['manager'])
        rs.set_enabled('coder', 'false', WHO)
        check('a form post string false lands in the store as the bool False',
              t.store()['roles']['coder'].get('enabled') is False)
        rs.set_enabled('coder', 'on', WHO)
        check('a form post string on lands as the bool True',
              t.store()['roles']['coder'].get('enabled') is True)
        rs.set_enabled('coder', True, WHO)
        check('turning a role on writes enabled True', t.store()['roles']['coder'].get('enabled') is True)
        check('every role in the store is still there', set(t.store()['roles']) == set(DOC['roles']))
        check('one changelog row per switch', len(t.rows()) == 4)
        check('the other role fields are byte-identical', t.store()['roles']['manager'] == DOC['roles']['manager'])


def test_set_enabled_rejects_an_unknown_role():
    if MISSING:
        return
    with TempStore() as t:
        try:
            rs.set_enabled('ghost', True, WHO)
            check('set_enabled on an unknown role raises KeyError', False)
        except KeyError:
            check('set_enabled on an unknown role raises KeyError', True)
        check('a rejected switch wrote no changelog row', t.rows() == [])
        check('a rejected switch wrote nothing to the store', 'ghost' not in t.store()['roles'])


if __name__ == '__main__':
    for _name in sorted(globals()):
        if _name.startswith('test_'):
            globals()[_name]()
    print('\n  %d failure(s)' % len(FAILS))
    sys.exit(1 if FAILS else 0)
