#!/usr/bin/env python3
'''Oracle for t1092 -- a named deferral in STATE.md must carry a tNNN.

THE DEFECT (workspace/rca/ac93114085d6.md). A session's final summary said, verbatim:

    Two things to flag honestly: sweep.py and the end-to-end pipeline are still unprobed
    -- round 5's targets that ran out of budget.

and the session closed with no tNNN attached. The deferral lived in prose, invisible to
taskstore.py, jas_replies.py and QUEUE.md, so the next session inherited it as a sentence.

WHAT THIS SUITE PINS, in the order the defect needs it:
  1. the incident sentence IS a finding, and it names sweep.py -- the real item, not a
     placeholder;
  2. the cure silences it: a tNNN in the same paragraph. A check that cannot be silenced
     is --no-verify'd within a week;
  3. every phrasing in the RCA's list, not one idiom;
  4. two deferred items with one id = one unaccounted. A check that stops at the first id
     cannot see the second item;
  5. ordinary prose stays silent -- the decay guard, and the only thing that keeps this
     from becoming the gate everyone routes around;
  6. a finding is a record: reported whole, never cut (never-truncate-a-record);
  7. the check runs at close: Checker.run() reports the deferral and exits 1.

RED ON MAIN: scripts/check_workspace.py has no deferral_findings() and no
Checker.check_deferral_numbers, so HAS_FN and HAS_M are False and every assertion below
reports BAD with that stated reason. Nothing here calls a missing name directly, so the red
is the missing change and never an AttributeError.

Touches no live state: every fixture is a file inside a fresh tempfile.mkdtemp; the
repository is never read. Checker.run's history and ledger writes are stubbed out.
'''
import contextlib
import io
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import check_workspace as cw  # noqa: E402

fails = []

HAS_FN = callable(getattr(cw, 'deferral_findings', None))
HAS_M = callable(getattr(cw.Checker, 'check_deferral_numbers', None))
NO_FN = ('scripts/check_workspace.py defines no deferral_findings() -- this is the change '
         'this scope adds')
NO_M = ('scripts/check_workspace.py defines no Checker.check_deferral_numbers -- this is '
        'the change this scope adds')

INCIDENT = ('Two things to flag honestly: sweep.py and the end-to-end pipeline are '
            'still unprobed -- round 5 targets that ran out of budget.')
WORDS = ', '.join('module%02d.py' % i for i in range(40))

# Every check Checker.run() calls today, read off scripts/check_workspace.py:2336-2346.
# Stubbed so run() exercises the wiring with nothing else reading or writing the tree.
OTHER_CHECKS = [
    'check_git_state', 'check_secrets', 'check_file_hazards', 'check_structure',
    'check_lesson_ids', 'check_tmp_inodes', 'check_enforcement', 'check_deferrals',
    'check_chat_bridge', 'check_drifting_claims', 'check_task_queue',
    'check_served_prose', 'check_review_gate', 'check_warning_clean',
    'check_headroom_waits', 'check_production_failures', 'check_flow_record',
    'check_skill_registry', 'check_enforcement_discipline', 'check_enforcement_ratchet',
    'check_model_registry', 'check_working_set', 'check_manifest_ops',
    'check_runtime_drift', 'check_doc_references',
]

KEYS = sorted(['block', 'items', 'ids', 'unaccounted'])


def check(label, cond, detail=''):
    print('  [%s] %s' % ('ok' if cond else 'BAD', label)
          + ('  :: ' + detail if detail else ''))
    if not cond:
        fails.append(label)


def test_findings():
    if not HAS_FN:
        check(NO_FN, False)
        return
    print('  deferral_findings is present; asserting its behaviour')
    f = cw.deferral_findings(INCIDENT)
    check('the incident sentence is a finding', len(f) == 1, 'got %d' % len(f))
    rec = f[0] if f else {}
    check('keys of a finding record', sorted(rec) == KEYS, str(sorted(rec)))
    check('it names the deferred item the RCA is about', 'sweep.py' in rec.get('block', ''),
          str(rec.get('block', ''))[:70])
    items = rec.get('items', [])
    check('one sentence, one item', len(items) == 1, 'items=%d' % len(items))
    check('with no id in the paragraph, it is unaccounted',
          rec.get('unaccounted') == 1, 'unaccounted=%s' % (rec.get('unaccounted'),))

    print('  the cure: the same paragraph with a tNNN')
    cured = INCIDENT + ' (filed as t1092 so the next session sees it.)'
    got = cw.deferral_findings(cured)
    check('a tNNN in the paragraph silences it', got == [], str(got)[:70])

    print('  every phrasing in the RCA list')
    for s in ('sweep.py is still unprobed.',
              'The pipeline ran out of budget.',
              'Round five targets are still untested.',
              'The probe is left for the next session.',
              'The ledger seam is not yet exercised.',
              'No test covers the restart path.',
              'The probe is deferred to a later session.'):
        n = len(cw.deferral_findings(s))
        check('caught: ' + s, n == 1, 'got %d' % n)

    print('  two distinct items, one id: the second is unaccounted')
    two_one = 'sweep.py is still unprobed. The pipeline ran out of budget. Filed as t1092.'
    f = cw.deferral_findings(two_one)
    check('two deferrals and one id give one finding', len(f) == 1, 'got %d' % len(f))
    rec = f[0] if f else {}
    items = rec.get('items', [])
    check('both deferral sentences are named', len(items) == 2, 'items=%d' % len(items))
    check('the shortfall is counted, not swallowed',
          rec.get('unaccounted') == 1, 'unaccounted=%s' % (rec.get('unaccounted'),))
    check('the id is recorded', rec.get('ids') == ['t1092'], str(rec.get('ids')))

    print('  two distinct items, two ids: silent')
    two_two = 'sweep.py is still unprobed (t1092). The pipeline ran out of budget (t1100).'
    got = cw.deferral_findings(two_two)
    check('two deferrals, two ids: no finding', got == [], str(got)[:70])

    print('  decay guard: ordinary prose must stay silent')
    for s in ('sweep.py is tested by test_sweep.py, which is green.',
              'The pipeline is exercised on every merge; the ledger records six passes.',
              'sweep.py carries t1092 and nothing is deferred.',
              'The sweep module is covered by test_sweep.py; six probes pass.'):
        got = cw.deferral_findings(s)
        check('silent: ' + s, got == [], str(got)[:70])

    print('  a finding is a record: never cut')
    long_item = ('sweep.py, the end-to-end pipeline probe, the ' + WORDS
                 + ' are all still unprobed at close.')
    f = cw.deferral_findings(long_item)
    check('a long deferral is a finding', len(f) == 1, 'got %d' % len(f))
    block = (f[0].get('block', '') if f else '')
    check('reported whole, not shorter than the sentence',
          len(block) >= len(long_item), 'block is %d chars' % len(block))
    check('nothing cut', '...' not in block, 'ellipsis in the block')


def state_fixture(text):
    '''A throwaway dir holding ONE STATE.md. The fixture lives only here, so a method that
    read the repository STATE.md instead cannot pass -- the dead-path guard.'''
    d = Path(tempfile.mkdtemp(prefix='t1092-'))
    try:
        (d / 'STATE.md').write_text(text, encoding='utf-8')
        c = cw.Checker(d, quick=True)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            c.check_deferral_numbers()
        return c, buf.getvalue()
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_method():
    if not HAS_M:
        check(NO_M, False)
        return
    print('  Checker.check_deferral_numbers is present; asserting its reports')
    c, out = state_fixture('## Left open\n\nsweep.py is still unprobed.\n')
    check('a bare deferral in STATE.md FAILS the check', c.failures == 1,
          'failures=%d' % c.failures)
    check('it is a FAIL, not a WARN', 'FAIL' in out, out[:60])
    check('it names the deferred item', 'sweep.py' in out, out[:60])
    check('it names the file it read', 'STATE.md' in out, out[:60])

    c, out = state_fixture('## Left open\n\nsweep.py is still unprobed, filed as t1092.\n')
    check('the same file with a tNNN passes', c.failures == 0 and c.passes == 1,
          'failures=%d passes=%d' % (c.failures, c.passes))
    check('and reports PASS', 'PASS' in out, out[:60])

    c, out = state_fixture('## Status\n\nThe pipeline is exercised on every merge; '
                           'six probes pass (t1092).\n')
    check('prose with no deferral passes', c.failures == 0 and c.passes == 1,
          'failures=%d passes=%d' % (c.failures, c.passes))

    c, out = state_fixture('## Left open\n\nsweep.py is still unprobed. The probe is left '
                           'for the next session. Filed as t1092.\n')
    check('two deferrals, one id, fails', c.failures == 1, 'failures=%d' % c.failures)

    print('  an unreadable STATE.md is never a pass')
    d = Path(tempfile.mkdtemp(prefix='t1092-'))
    try:
        (d / 'STATE.md').mkdir()          # a directory where the check expects a file
        c = cw.Checker(d, quick=True)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            c.check_deferral_numbers()
        out = buf.getvalue()
        check('an unreadable STATE.md is not a pass', c.failures == 1,
              'failures=%d' % c.failures)
        check('and it names the file it could not read', 'STATE.md' in out, out[:60])
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_wired():
    if not HAS_M:
        check(NO_M, False)
        return
    d = Path(tempfile.mkdtemp(prefix='t1092-'))
    try:
        try:
            subprocess.run(['git', 'init', '-q', str(d)], capture_output=True, timeout=60)
        except Exception as e:
            check('git init ran; the wiring case needs a repo', False,
                  '%s: %s' % (type(e).__name__, e))
            return
        (d / 'STATE.md').write_text('## Left open\n\nsweep.py is still unprobed.\n',
                                    encoding='utf-8')
        c = cw.Checker(d, quick=True)
        with contextlib.ExitStack() as stack:
            for n in OTHER_CHECKS + ['_record_history', '_record_tree_state']:
                if hasattr(cw.Checker, n):
                    stack.enter_context(mock.patch.object(cw.Checker, n,
                                                          lambda self, *a: None))
            if hasattr(cw, 'HISTORY'):
                stack.enter_context(mock.patch.object(cw, 'HISTORY',
                                                      d / 'check-history.jsonl'))
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = c.run()
        out = buf.getvalue()
        check('Checker.run() exits 1 on a bare deferral', rc == 1, 'rc=%s' % (rc,))
        check('run() reports the deferred item by name', 'sweep.py' in out, out[-160:])
    finally:
        shutil.rmtree(d, ignore_errors=True)


if __name__ == '__main__':
    print('test_check_workspace_deferral_numbers (t1092): a deferral must carry a tNNN')
    print('  deferral_findings present: %s; check_deferral_numbers present: %s'
          % (HAS_FN, HAS_M))
    test_findings()
    test_method()
    test_wired()
    print()
    if fails:
        print('FAILED: %d assertion(s)' % len(fails))
        for x in fails:
            print('  - ' + x)
        sys.exit(1)
    print('all deferral-number assertions passed')
    sys.exit(0)
