#!/usr/bin/env python3
'''t798 -- FLOW.json carries a `controls` census, and every live harness control is on it.

RED on main for the missing change: FLOW.json has no `controls` key, so a hook installed by
editing .claude/settings.json has no chart object to be registered against (RCA 133a40978321,
cause C1). The context_watch UserPromptSubmit hook is the specimen: it went live on 2026-09-22
and a session review found it on 2026-10-07, fifteen days later.

Everything is COMPUTED at test time from the two real files on disk. Nothing is stored in this
file, so the census cannot rot into a list of yesterday's defects: a hook added to
.claude/settings.json without a node fails here, and a node whose hook has been removed fails
here too (the decay test). Reads only; writes nothing.
'''
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FLOW = ROOT / 'projects' / 'local-inference' / 'models' / 'FLOW.json'
SETTINGS = ROOT / '.claude' / 'settings.json'

REQUIRED = ('id', 'file', 'key', 'registered_by_task', 'owner', 'serves', 'matches', 'behaviour')
CONFIG_SURFACES = ('.claude/settings.json', '.claude/settings.local.json')
TASK_ID = re.compile(r'^t\d+$')
NODE_ID = re.compile(r'^ctl_[a-z0-9_]+$')

failures = []


def check(cond, msg):
    if not cond:
        failures.append(msg)


def load(path):
    return json.loads(path.read_text(encoding='utf-8'))


def controls(flow):
    section = flow.get('controls')
    return section if isinstance(section, list) else []


def chart_ids(flow):
    ids = set()
    for key in ('tasks', 'services'):
        for row in flow.get(key) or []:
            if isinstance(row, dict) and row.get('id'):
                ids.add(str(row['id']))
    return ids


def live_controls(settings):
    '''Every control the harness enforces, as (where, exact string), read from the tracked file.'''
    out = []
    hooks = settings.get('hooks')
    if isinstance(hooks, dict):
        for event, entries in hooks.items():
            for entry in entries if isinstance(entries, list) else []:
                if not isinstance(entry, dict):
                    continue
                for hook in entry.get('hooks') or []:
                    if isinstance(hook, dict) and isinstance(hook.get('command'), str):
                        out.append(('hooks.' + str(event), hook['command']))
    perms = settings.get('permissions')
    if isinstance(perms, dict):
        for kind in ('allow', 'deny', 'ask'):
            for pattern in perms.get(kind) or []:
                if isinstance(pattern, str):
                    out.append(('permissions.' + kind, pattern))
    return out


def test_the_controls_census_exists_and_is_not_empty():
    flow = load(FLOW)
    check('controls' in flow,
          'FLOW.json has no `controls` key: a hook installed by editing .claude/settings.json has '
          'nothing to register against (RCA 133a40978321 C1).')
    check(controls(flow) != [],
          'FLOW.json `controls` is present but empty: an empty population passes a census that is '
          'supposed to prove nothing is unregistered (t723).')


def test_every_control_node_carries_the_fields_a_person_needs():
    for node in controls(load(FLOW)):
        if not isinstance(node, dict):
            failures.append('control entry is not an object: %r' % (node,))
            continue
        label = str(node.get('id') or '<no id>')
        for field in REQUIRED:
            value = node.get(field)
            if field == 'serves':
                check(isinstance(value, list) and len(value) > 0,
                      'control %s: `serves` must be a non-empty list of chart ids, got %r'
                      % (label, value))
            else:
                check(isinstance(value, str) and value.strip() != '',
                      'control %s: field `%s` must be a non-empty string, got %r'
                      % (label, field, value))
        check(NODE_ID.match(str(node.get('id') or '')) is not None,
              'control id %r does not match ^ctl_[a-z0-9_]+$' % (node.get('id'),))
        check(TASK_ID.match(str(node.get('registered_by_task') or '')) is not None,
              'control %s: registered_by_task %r is not a task id shaped like t798, so it cannot '
              'be looked up in the task store' % (label, node.get('registered_by_task')))
        check(str(node.get('key') or '').startswith(('hooks', 'permissions')),
              'control %s: key %r must locate the entry inside the config file, e.g. '
              'hooks.UserPromptSubmit[0].hooks[0].command' % (label, node.get('key')))
        check(any(str(node.get('file') or '').endswith(s) for s in CONFIG_SURFACES),
              'control %s: file %r is not a harness config surface (%s)'
              % (label, node.get('file'), ', '.join(CONFIG_SURFACES)))


def test_control_ids_are_unique():
    seen = set()
    for node in controls(load(FLOW)):
        ident = str(node.get('id'))
        check(ident not in seen, 'control id %r appears more than once in FLOW.json' % (ident,))
        seen.add(ident)


def test_serves_names_ids_that_exist_on_the_chart():
    flow = load(FLOW)
    known = chart_ids(flow)
    check(len(known) > 0, 'FLOW.json exposes no task or service id for a control node to serve')
    for node in controls(flow):
        if not isinstance(node, dict):
            continue
        for served in node.get('serves') or []:
            check(str(served) in known,
                  'control %s: serves %r is not an id in FLOW.json tasks/services, so the node '
                  'floats off the chart' % (node.get('id'), served))


def test_every_live_hook_and_permission_is_registered():
    live = live_controls(load(SETTINGS))
    check(len(live) > 0,
          'no hook or permission strings found in the tracked .claude/settings.json -- the census '
          'would be vacuous')
    registered = set()
    for node in controls(load(FLOW)):
        if isinstance(node, dict) and isinstance(node.get('matches'), str):
            registered.add(node['matches'])
    for where, value in live:
        check(value in registered,
              'LIVE CONTROL NOT REGISTERED: %s sets %r in .claude/settings.json and no FLOW.json '
              'control node names that exact string in its `matches` field, so the harness enforces '
              'it and the chart does not know it exists (this is RCA 133a40978321).' % (where, value))


def test_every_registered_node_names_a_live_control():
    live = set(value for _where, value in live_controls(load(SETTINGS)))
    for node in controls(load(FLOW)):
        if not isinstance(node, dict):
            continue
        check(node.get('matches') in live,
              'DECAY: control %s names %r, which is not a live command or permission pattern in '
              '.claude/settings.json -- the node outlived the thing it registers' %
              (node.get('id'), node.get('matches')))


if __name__ == '__main__':
    tests = sorted((n, f) for n, f in list(globals().items())
                   if n.startswith('test_') and callable(f))
    for name, fn in tests:
        try:
            fn()
            print('ok   ' + name)
        except Exception as exc:
            failures.append('%s raised %s: %s' % (name, type(exc).__name__, exc))
            print('ERROR ' + name + ': ' + type(exc).__name__ + ': ' + str(exc))
    for line in failures:
        print('FAIL: ' + line)
    print('%d check(s) failed' % len(failures))
    raise SystemExit(1 if failures else 0)
