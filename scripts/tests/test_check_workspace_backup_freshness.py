#!/usr/bin/env python3
'''t730 oracle: the workspace check's backup gate is wired, and a stale backup cannot print PASS.

WHY. scripts/check_workspace.py reads workspace/backup-runs.jsonl and PASSes when the newest
nas-backup row says verified -- the stamp's AGE is never compared to anything, so a verified row
from a month ago passes forever. scripts/nas_backup.sh's own head comment records the 2026-09-15
episode this repeats: verify-failed for weeks on an intact backup, because nothing required a
FRESH verified row to exist. Measured on the live ledger: newest verified nas-backup row is
2026-10-09T10:41:35Z, over 47 hours old, and the newest nas-backup row overall is prune-failed.

Red on main for ONE reason: no backup_verdict_line(), no Checker.check_backup_freshness, not
registered in run(). Every case prints that reason; nothing here raises.

WHY THE STATUS VOCABULARY IS PINNED TO FOUR WORDS. Checker.result looks the status up in a
colours dict keyed by PASS / WARN / FAIL / SKIP, so any other string raises KeyError and run()
records a crashed check as a FAIL that certifies nothing -- a gate that crashes is not a gate.
And WARN exits 0: a stale backup reported as a warning is exactly the silent pass this task
exists to stop. So UNREADABLE must map to FAIL, and the mapping must land inside that dict.

Offline: no writes, no model, no subprocess, no live state, no process left running.
'''
import inspect
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
try:
    import check_workspace as W
    IMPORT_ERR = None
except Exception as exc:
    W = None
    IMPORT_ERR = 'importing scripts/check_workspace.py raised %s: %s' % (type(exc).__name__, exc)

LIMIT = 36
LEDGER = 'backup-runs.jsonl'
REMEDY = 'nas_backup.sh'
LABEL = 'Backup freshness'
# The four keys of the colours dict in Checker.result, read off the file.
LEGAL = ('PASS', 'WARN', 'FAIL', 'SKIP')

# The live ledger's real numbers: 61 nas-backup rows, newest verified 2026-10-09T10:41:35Z.
PASS_RES = {'verdict': 'PASS', 'n_rows': 61, 'n_unreadable': 0,
            'newest_verified': '2026-10-09T10:41:35Z', 'age_hours': 6.25,
            'limit_hours': LIMIT,
            'detail': '61 nas-backup row(s) examined; newest verified 2026-10-09T10:41:35Z '
                      '(6.25h old); limit 36h'}
FAIL_RES = {'verdict': 'FAIL', 'n_rows': 61, 'n_unreadable': 0,
            'newest_verified': '2026-10-09T10:41:35Z', 'age_hours': 47.7,
            'limit_hours': LIMIT,
            'detail': '61 nas-backup row(s) examined; newest verified 2026-10-09T10:41:35Z '
                      '(47.7h old) is older than the 36h limit; run scripts/nas_backup.sh '
                      'and read what it says'}
UNREAD_RES = {'verdict': 'UNREADABLE', 'n_rows': 0, 'n_unreadable': 1,
              'newest_verified': None, 'age_hours': None, 'limit_hours': LIMIT,
              'detail': 'unparseable line 2 of workspace/backup-runs.jsonl: 1 line(s) of the '
                        'ledger are not a JSON object; run scripts/nas_backup.sh and read '
                        'what it says'}
CASES = (('PASS', PASS_RES), ('FAIL', FAIL_RES), ('UNREADABLE', UNREAD_RES))

ok = 0
fail = 0


def check(label, cond, got=''):
    global ok, fail
    if cond:
        ok += 1
        print('  ok   %s' % label)
    else:
        fail += 1
        print('  FAIL %s%s' % (label, (' -- got ' + str(got)) if got != '' else ''))


HAS_FN = bool(W) and callable(getattr(W, 'backup_verdict_line', None))
HAS_MTH = bool(W) and callable(getattr(W.Checker, 'check_backup_freshness', None))
NO_FN = 'scripts/check_workspace.py has no backup_verdict_line() -- the change this scope adds'
NO_MTH = 'scripts/check_workspace.py has no Checker.check_backup_freshness -- the change this scope adds'


def _fmt(res):
    return W.backup_verdict_line(res)


def _absent():
    check('check_workspace has backup_verdict_line(res)', False, NO_FN)


def test_the_formatter_exists_and_the_method_exists():
    if IMPORT_ERR:
        check('check_workspace imports cleanly', False, IMPORT_ERR)
    check('check_workspace has backup_verdict_line(res)', HAS_FN, NO_FN)
    check('Checker has the method check_backup_freshness', HAS_MTH, NO_MTH)


def test_a_verified_row_inside_the_window_maps_to_pass():
    if not HAS_FN:
        _absent()
        return
    got = _fmt(PASS_RES)
    check('a fresh verified row maps to status PASS', isinstance(got, tuple) and len(got) == 3
          and got[0] == 'PASS', repr(got))


def test_a_verified_row_outside_the_window_maps_to_fail():
    if not HAS_FN:
        _absent()
        return
    got = _fmt(FAIL_RES)
    check('a 47.7h-old verified row maps to status FAIL',
          isinstance(got, tuple) and len(got) == 3 and got[0] == 'FAIL', repr(got))


def test_an_unreadable_ledger_maps_to_fail_never_pass_never_warn():
    if not HAS_FN:
        _absent()
        return
    got = _fmt(UNREAD_RES)
    check('UNREADABLE maps to status FAIL',
          isinstance(got, tuple) and len(got) == 3 and got[0] == 'FAIL', repr(got))
    check('NEGATIVE: UNREADABLE is never reported as PASS (the silent pass this task stops)',
          got[0] != 'PASS', repr(got[0]))
    check('NEGATIVE: UNREADABLE is never WARN -- WARN exits 0, which is the same silent pass',
          got[0] != 'WARN', repr(got[0]))


def test_every_verdict_maps_into_a_status_the_checker_can_print():
    if not HAS_FN:
        _absent()
        return
    for label, res in CASES:
        got = _fmt(res)
        check('%s maps to a status in Checker.result''s colour keys (PASS/WARN/FAIL/SKIP)'
              % label, isinstance(got, tuple) and len(got) == 3 and got[0] in LEGAL, repr(got))
        check('%s is given a non-empty string label and a non-empty string detail' % label,
              isinstance(got[1], str) and bool(got[1].strip())
              and isinstance(got[2], str) and bool(got[2].strip()), repr(got))


def test_the_three_verdicts_are_distinguishable_by_status():
    if not HAS_FN:
        _absent()
        return
    st = [(_fmt(res)[0] if HAS_FN else None) for _l, res in CASES]
    check('PASS and FAIL produce DIFFERENT statuses (a constant stub cannot satisfy this)',
          st[0] != st[1], st)
    check('the record is a 3-tuple ready to splat into Checker.result(status, name, detail)',
          all(len(_fmt(r)) == 3 for _l, r in CASES),
          [len(_fmt(r)) for _l, r in CASES])


def test_the_detail_carries_the_assessment_verbatim():
    if not HAS_FN:
        _absent()
        return
    for label, res in CASES:
        got = _fmt(res)
        check('%s: the printed detail contains the collector''s whole detail' % label,
              res['detail'] in got[2], repr(got[2]))
        check('%s: it carries the population count 61, not a count of its own' % label,
          '61' in got[2] or label != 'PASS' and True,
          repr(got[2]))
    check('the stale case carries the stamp a person acts on (2026-10-09T10:41:35Z)',
          '2026-10-09T10:41:35Z' in _fmt(FAIL_RES)[2], repr(_fmt(FAIL_RES)[2]))
    check('the stale case carries the limit it was judged against (36)',
          '36' in _fmt(FAIL_RES)[2], repr(_fmt(FAIL_RES)[2]))


def test_the_record_names_the_ledger_it_examined():
    if not HAS_FN:
        _absent()
        return
    for label, res in CASES:
        check('%s: the record names %s so the reader knows what was read' % (label, LEDGER),
              LEDGER in _fmt(res)[2], repr(_fmt(res)[2]))


def test_the_record_names_the_command_that_clears_it():
    if not HAS_FN:
        _absent()
        return
    check('FAIL names the command that clears it (scripts/nas_backup.sh)',
          REMEDY in _fmt(FAIL_RES)[2], repr(_fmt(FAIL_RES)[2]))
    check('UNREADABLE names the command that clears it',
          REMEDY in _fmt(UNREAD_RES)[2], repr(_fmt(UNREAD_RES)[2]))
    check('NEGATIVE: a PASS record does NOT print the remedy -- no alarm when it verified',
          REMEDY not in _fmt(PASS_RES)[2], repr(_fmt(PASS_RES)[2]))


def test_the_unreadable_record_says_why_it_could_not_be_read():
    if not HAS_FN:
        _absent()
        return
    det = _fmt(UNREAD_RES)[2]
    check('the unprintable record says unparseable, not a bare status',
          'unparseable' in det.lower(), repr(det))
    check('the unprintable record gives the line number (line 2)', 'line 2' in det, repr(det))


def test_the_name_is_a_greppable_label_and_is_stable():
    if not HAS_FN:
        _absent()
        return
    names = [_fmt(res)[1] for _l, res in CASES]
    check('the label is one string every verdict shares, so the record is greppable',
          len(set(names)) == 1 and isinstance(names[0], str) and bool(names[0].strip()), names)
    check('the label contains the word backup (grep-able from session_end output)',
          'backup' in names[0].lower(), names)


def test_the_check_is_registered_to_run():
    if not HAS_MTH:
        check('Checker has the method check_backup_freshness', False, NO_MTH)
        return
    src = inspect.getsource(W.Checker.run)
    check('run() calls self.check_backup_freshness -- a check nobody calls proves nothing',
          'self.check_backup_freshness' in src,
          'check_backup_freshness absent from Checker.run')


def test_the_registered_check_reads_the_ledger_through_the_shared_collector():
    if not HAS_MTH:
        check('Checker has the method check_backup_freshness', False, NO_MTH)
        return
    src = inspect.getsource(W.Checker.check_backup_freshness)
    check('the new check reads %s -- the same file the existing gate reads' % LEDGER,
          LEDGER in src, repr(src[:160]))
    check('the new check goes through the shared collector (error_scan.backup_freshness), '
          'not a second copy of the parsing', 'backup_freshness' in src, repr(src[:160]))


def test_the_registry_kept_every_check_that_was_there():
    if not HAS_MTH:
        check('Checker has the method check_backup_freshness', False, NO_MTH)
        return
    src = inspect.getsource(W.Checker.run)
    for kept in ('self.check_git_state', 'self.check_secrets', 'self.check_doc_references'):
        check('run() still registers %s -- adding a gate must not drop a gate' % kept,
              kept in src, 'absent from Checker.run')
    check('the registry grew by exactly the new gate (24 -> 25 registered checks)',
          src.count('self.check_') == 25, src.count('self.check_'))


TESTS = [v for k, v in sorted(globals().items()) if k.startswith('test_') and callable(v)]


def gate(t):
    global fail
    try:
        t()
    except Exception as exc:
        fail += 1
        print('  FAIL %s raised %s: %s -- an oracle reports, it does not crash'
              % (t.__name__, type(exc).__name__, exc))


if __name__ == '__main__':
    print('t730 backup freshness: %d test(s) over scripts/check_workspace.py' % len(TESTS))
    for t in TESTS:
        print(t.__name__)
        gate(t)
    print('')
    print('%d passed, %d failed' % (ok, fail))
    sys.exit(1 if fail else 0)
