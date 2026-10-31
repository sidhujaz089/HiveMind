#!/usr/bin/env python3
"""t464: every implementation brief must state the edit-application protocol.

The defects noted on t464 (t325/t364/t431 supervise/switch; t274/t400/t350/t467
merge/merged, all builder gemma4:26b) are the SAME class the schema already records as
harness-edit-application (workspace/failure-schema.json:71-84): gemma4:26b cannot reliably
APPLY an edit -- indentation mangled on a line-range edit, a file re-emitted unchanged, a
large file truncated mid-emission. The briefs were verified-precise, so the model is burning
attempts against a wall it was never shown. GATE_RULES (scripts/bridge.py:39-44, appended at
scripts/bridge.py:216) already closes the analogous "the model never saw the rule" gap for the
commit gate (t431). This suite pins the sibling: an EDIT_PROTOCOL constant, appended to every
implementation brief, that states the edit-application protocol in plain words.

Red on main because bridge.EDIT_PROTOCOL does not exist yet.
"""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / 'scripts'))

import bridge

PASSED = FAILED = 0

SHOULD_MENTION = ('indent', 'line')

IMPL = {'kind': 'implementation', 'file': 'scripts/widget.py',
        'target': 'scripts/tests/test_widget.py',
        'single_change': 'spin.', 'what_is_wrong': 'nothing spins',
        'done_when': 'test_widget.py exits 0', 'related_entries': ['t464']}

def report(name, got, want):
    global PASSED, FAILED
    ok = got == want
    PASSED += ok
    FAILED += not ok
    print('  ' + ('PASS' if ok else 'FAIL') + '  ' + name +
          ('' if ok else '  (got %r, want %r)' % (got, want)))

def fixture():
    repo = Path(tempfile.mkdtemp(prefix='bridge-edit-protocol-'))
    (repo / 'scripts' / 'tests').mkdir(parents=True)
    (repo / 'scripts' / 'tests' / 'test_widget.py').write_text(
        'import sys\nsys.exit(0)\n', encoding='utf-8')
    (repo / 'scripts' / 'autonomy_loop.sh').write_text(
        '# fixture work list\n'
        'p07-fixture|brief-p07-fixture.txt|scripts/fixture.py|scripts/tests/test_fixture.py\n'
        'while true; do\n    sleep 60\ndone\n', encoding='utf-8')
    return repo

def main():
    ep = getattr(bridge, 'EDIT_PROTOCOL', '')
    report('bridge.EDIT_PROTOCOL is a non-empty string',
           isinstance(ep, str) and len(ep) > 0, True)
    for word in SHOULD_MENTION:
        report('EDIT_PROTOCOL states the ' + word + ' shape',
               word in ep.lower(), True)

    repo = fixture()
    ans = repo / 'answer.md'
    ans.write_text('## t999\n\n```chunks\n' + json.dumps([IMPL]) + '\n```\n', encoding='utf-8')
    rc = bridge.main([str(ans), '--task', 't999', '--repo', str(repo), '--apply'])
    report('a valid block still applies (exit 0)', rc, 0)
    briefs = sorted((repo / 'workspace' / 'briefs').glob('brief-*.txt'))
    brief = briefs[0].read_text(encoding='utf-8') if briefs else ''
    report('exactly one brief is written', len(briefs), 1)
    if ep:
        report('the written brief states EDIT_PROTOCOL', ep in brief, True)
    else:
        report('the written brief states EDIT_PROTOCOL (constant defined)', False, True)

    print('\n%d passed, %d failed' % (PASSED, FAILED))
    return 1 if FAILED else 0

if __name__ == '__main__':
    sys.exit(main())
