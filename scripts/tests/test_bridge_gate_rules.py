#!/usr/bin/env python3
"""t431: the brief's gate rules must name every no-timeout form the commit gate fires on.

The gate (library_checks._no_timeout, scripts/library_checks.py:84-99) refuses any
subprocess.run / subprocess.check_output / urlopen call whose text lacks 'timeout'.
bridge.GATE_RULES is the plain-words copy of that gate that bridge.py appends to every
brief it writes (scripts/bridge.py:35-43, appended at line 199). A form the gate checks
but the brief omits is the t431 defect itself: p58-t323 lost complete implementations
because the brief named a subprocess pattern the gate then refused. This suite pins that
the constant and the written brief agree with the gate on the no-timeout forms.
"""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / 'scripts'))

import bridge

PASSED = FAILED = 0

# The call forms the no-timeout detector fires on (scripts/library_checks.py:87:
# urlopen, subprocess.run, subprocess.check_output). If the gate's scope changes,
# change this list with it. The brief may name MORE forms than the gate (extra caution
# is fine); it may never name fewer.
GATE_FORMS = ('subprocess.run', 'subprocess.check_output', 'urlopen')

IMPL = {'kind': 'implementation', 'file': 'scripts/widget.py',
        'target': 'scripts/tests/test_widget.py',
        'single_change': 'spin.', 'what_is_wrong': 'nothing spins',
        'done_when': 'test_widget.py exits 0', 'related_entries': ['t431']}

def report(name, got, want):
    global PASSED, FAILED
    ok = got == want
    PASSED += ok
    FAILED += not ok
    print('  ' + ('PASS' if ok else 'FAIL') + '  ' + name +
          ('' if ok else '  (got %r, want %r)' % (got, want)))

def fixture():
    repo = Path(tempfile.mkdtemp(prefix='bridge-gate-rules-'))
    (repo / 'scripts' / 'tests').mkdir(parents=True)
    (repo / 'scripts' / 'tests' / 'test_widget.py').write_text(
        'import sys\nsys.exit(0)\n', encoding='utf-8')
    (repo / 'scripts' / 'autonomy_loop.sh').write_text(
        '# fixture work list\n'
        'p07-fixture|brief-p07-fixture.txt|scripts/fixture.py|scripts/tests/test_fixture.py\n'
        'while true; do\n    sleep 60\ndone\n', encoding='utf-8')
    return repo

def main():
    # 1. the constant must state every no-timeout form the gate enforces
    for form in GATE_FORMS:
        report('GATE_RULES names the no-timeout form ' + repr(form),
               form in bridge.GATE_RULES, True)
    report('GATE_RULES demands an explicit timeout=',
           'timeout=' in bridge.GATE_RULES, True)

    # 2. and it must actually land in every brief bridge.py writes, not just be defined
    repo = fixture()
    ans = repo / 'answer.md'
    ans.write_text('## t999\n\n```chunks\n' + json.dumps([IMPL]) + '\n```\n', encoding='utf-8')
    rc = bridge.main([str(ans), '--task', 't999', '--repo', str(repo), '--apply'])
    report('a valid block still applies (exit 0)', rc, 0)
    briefs = sorted((repo / 'workspace' / 'briefs').glob('brief-*.txt'))
    brief = briefs[0].read_text(encoding='utf-8') if briefs else ''
    report('exactly one brief is written', len(briefs), 1)
    for form in GATE_FORMS:
        report('the written brief states the no-timeout form ' + repr(form),
               form in brief, True)
    report('the written brief demands timeout=', 'timeout=' in brief, True)

    print('\n%d passed, %d failed' % (PASSED, FAILED))
    return 1 if FAILED else 0

if __name__ == '__main__':
    sys.exit(main())
