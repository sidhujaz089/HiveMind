#!/usr/bin/env python3
'''t798 -- writing .claude/settings.json cannot install a hook or permission nobody registered.

RED on main for the missing change: scripts/hooks/guard_task_writes.py is already registered as
the PreToolUse hook on Write|Edit (.claude/settings.json line 28), and its only path test is the
TASKFILE regex at guard_task_writes.py:48, which matches workspace/tasks/t<digits>.md. Line 57
returns 0 for every path that is not a task file, so a settings.json write that installs a
permanent hook passes today with nothing registered on the chart. That is RCA 133a40978321: the
context_watch UserPromptSubmit hook went live on 2026-09-22 and was found by a session review on
2026-10-07, fifteen days later.

The behaviour belongs in the EXISTING hook script, not a new one, for two reasons that both matter
mechanically: .claude/settings.json already routes Write and Edit through it, so the rule is live
the moment the code lands with NO settings.json edit of its own (which is how the guard escapes
the self-referential bootstrap the RCA's control nodes exist to break), and coder.py can only edit
a file that exists.

Assertions are bidirectional on purpose: a build that blocks EVERY settings write passes the
refusal cases and fails tests 1, 3, 5, 6 and 7 here. The cheap exemption -- naming a task id -- is
asserted as hard as the rule.

Built on the REAL .claude/settings.json read from disk, with two DISTINCT constants per kind (the
live deny pattern and a different new one; the live context_watch command and a new guard command),
so a broad implementation cannot satisfy one case by matching the other. Reads the tracked file;
writes only inside a TemporaryDirectory.
'''
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HOOK = ROOT / 'scripts' / 'hooks' / 'guard_task_writes.py'
SETTINGS = ROOT / '.claude' / 'settings.json'
TIMEOUT = 30

CTX_WATCH = 'python3 "$CLAUDE_PROJECT_DIR/scripts/context_watch.py" --hook'
NEW_GUARD = 'python3 "$CLAUDE_PROJECT_DIR/scripts/hooks/guard_settings_writes.py"'
NEW_WATCH = 'python3 "$CLAUDE_PROJECT_DIR/scripts/hooks/unregistered_watch.py"'
DENY_LIVE = 'mcp__ff5d2144-4ece-468d-809e-ce1da6c27f1c__share_file'
DENY_NEW = 'mcp__ff5d2144-4ece-468d-809e-ce1da6c27f1c__delete_file'

failures = []


def check(cond, msg):
    if not cond:
        failures.append(msg)


def disk_settings():
    return json.loads(SETTINGS.read_text(encoding='utf-8'))


def run(payload, flow_json=None):
    '''Run the hook the way the harness does: the payload arrives on stdin, the exit code is the verdict.'''
    env = dict(os.environ)
    if flow_json is not None:
        env['FLOW_JSON'] = str(flow_json)
    return subprocess.run([sys.executable, str(HOOK)], input=json.dumps(payload),
                          capture_output=True, text=True, timeout=TIMEOUT, cwd=str(ROOT), env=env)


def write_payload(data, path):
    return {'tool_name': 'Write',
            'tool_input': {'file_path': str(path),
                           'content': json.dumps(data, indent=2, ensure_ascii=False)}}


def with_new_hook(data, command):
    data.setdefault('hooks', {})['Stop'] = [
        {'hooks': [{'type': 'command', 'command': command}]}]
    return data


def verdict(result, expect, label, needles=()):
    check(result.returncode == expect,
          '%s: expected exit %d, got %r. stdout=%r stderr=%r'
          % (label, expect, result.returncode, result.stdout, result.stderr))
    if expect == 2:
        text = (result.stdout or '') + (result.stderr or '')
        check(text.strip() != '',
              '%s: a refusal must say why it refused, and said nothing' % label)
        for needle in needles:
            check(needle in text,
                  '%s: the refusal does not name %r. stdout+stderr: %r' % (label, needle, text))


def test_the_strings_this_suite_uses_are_the_ones_in_the_real_file():
    data = disk_settings()
    live = [hook.get('command')
            for entries in (data.get('hooks') or {}).values()
            for entry in entries if isinstance(entry, dict)
            for hook in (entry.get('hooks') or [])]
    check(CTX_WATCH in live,
          'precondition: the tracked .claude/settings.json does not register context_watch with the '
          'command string this suite is written against. Live commands: %r' % (live,))
    check(DENY_LIVE in (data.get('permissions') or {}).get('deny', []),
          'precondition: the tracked .claude/settings.json does not carry the deny pattern %r that '
          'this suite uses as its live constant' % (DENY_LIVE,))


def test_a_hook_added_with_nothing_registered_is_refused():
    verdict(run(write_payload(with_new_hook(disk_settings(), NEW_GUARD), SETTINGS)), 2,
            'a Stop hook added with no task id and no control node',
            needles=('guard_settings_writes.py', 'settings.json', '# t', 'FLOW.json'))


def test_a_hook_whose_command_names_a_task_id_is_allowed():
    verdict(run(write_payload(with_new_hook(disk_settings(), NEW_GUARD + '  # t798'), SETTINGS)),
            0, 'a Stop hook whose command carries the registration token # t798')


def test_a_hook_naming_a_registered_flow_control_node_is_allowed():
    with tempfile.TemporaryDirectory() as td:
        chart = Path(td) / 'FLOW.json'
        chart.write_text(json.dumps({
            'tasks': [{'id': 'intake', 'file': 'scripts/scope_queue.py', 'key': 'intake'}],
            'controls': [{'id': 'ctl_probe_guard',
                          'file': '.claude/settings.json',
                          'key': 'hooks.Stop[0].hooks[0].command',
                          'registered_by_task': 't798',
                          'owner': 'Harness',
                          'serves': ['intake'],
                          'matches': 'python3 x/probe_guard.py',
                          'behaviour': 'probe findings into context'}]},
            ensure_ascii=False), encoding='utf-8')
        verdict(run(write_payload(with_new_hook(disk_settings(),
                                                'python3 x/probe_guard.py  # ctl_probe_guard'),
                                 SETTINGS), flow_json=chart),
                0, 'a Stop hook naming a control node that FLOW.json registers -- this is the '
                   'settings-to-FLOW link the RCA asked for')


def test_a_permission_pattern_added_with_nothing_registered_is_refused():
    data = disk_settings()
    perms = data.setdefault('permissions', {})
    perms['deny'] = list(perms.get('deny') or []) + [DENY_NEW]
    verdict(run(write_payload(data, SETTINGS)), 2,
            'a permission pattern added with no task id and no control node',
            needles=(DENY_NEW, 'settings.json', '# t'))


def test_the_unchanged_file_is_allowed():
    verdict(run(write_payload(disk_settings(), SETTINGS)), 0,
            'settings.json rewritten with nothing added to it -- a gate that fires here fires on '
            'every session and will be bypassed')


def test_hooks_shaped_content_in_an_unrelated_file_is_allowed():
    verdict(run(write_payload(with_new_hook(disk_settings(), NEW_WATCH),
                              ROOT / 'workspace' / 'notes.md')), 0,
            'hooks-shaped JSON written to a file that is not a harness config surface')


def test_an_edit_that_adds_an_unregistered_hook_is_refused():
    text = SETTINGS.read_text(encoding='utf-8')
    anchor = '"hooks": {'
    check(anchor in text,
          'precondition: .claude/settings.json does not contain the anchor %r that this test edits. '
          'The file reads: %r' % (anchor, text))
    nl = chr(10)
    esc = NEW_WATCH.replace('"', chr(92) + '"')
    block = (nl + '    "Stop": [' + nl + '      {' + nl + '        "hooks": [' + nl +
             '          {' + nl + '            "type": "command",' + nl +
             '            "command": "' + esc + '"' + nl +
             '          }' + nl + '        ]' + nl + '      }' + nl + '    ],' + nl + '    ')
    new_string = anchor + block
    try:
        json.loads(text.replace(anchor, new_string, 1))
    except Exception as exc:
        failures.append('the Edit payload this test builds is not valid JSON, so it proves nothing: %r'
                        % (exc,))
        return
    verdict(run({'tool_name': 'Edit',
                 'tool_input': {'file_path': str(SETTINGS), 'old_string': anchor,
                                'new_string': new_string}}), 2,
            'an Edit that adds an unregistered Stop hook',
            needles=('unregistered_watch.py', 'settings.json', '# t'))


def test_unparseable_settings_content_is_refused_with_a_cause():
    result = run({'tool_name': 'Write',
                  'tool_input': {'file_path': str(SETTINGS), 'content': '{ not json at all'}})
    check(result.returncode == 2,
          'unparseable settings.json content must be refused -- a file that cannot be parsed cannot '
          'be censused, so letting it through is the silent failure: exit %r, stdout=%r stderr=%r'
          % (result.returncode, result.stdout, result.stderr))
    text = (result.stdout or '') + (result.stderr or '')
    check('JSON' in text,
          'a refusal of unparseable content must name JSON as the cause, not a code: %r' % text)
    check('settings.json' in text,
          'a refusal of unparseable content must name the file: %r' % text)


def test_a_payload_the_hook_cannot_parse_does_not_block_the_session():
    result = subprocess.run([sys.executable, str(HOOK)], input='not json at all',
                            capture_output=True, text=True, timeout=TIMEOUT, cwd=str(ROOT))
    check(result.returncode == 0,
          'a payload that is not JSON must not block the session -- the rule at '
          'guard_task_writes.py:55 must survive this change: exit %r, stderr=%r'
          % (result.returncode, result.stderr))


def test_the_existing_task_file_guard_still_refuses():
    verdict(run({'tool_name': 'Write',
                 'tool_input': {'file_path': str(ROOT / 'workspace' / 'tasks' / 't999-scope-probe.md'),
                                'content': '# t999 probe'}}), 2,
            'a hand-written task file: guard_task_writes.py already refuses this and must keep '
            'refusing it after the settings rule is added',
            needles=('taskstore.py',))


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
