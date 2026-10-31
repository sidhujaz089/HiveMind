#!/usr/bin/env python3
'''t1096: --ratchet must not drop a kind=flake ceiling entry on a single passing sweep.

Why (workspace/rca/b57898748f14.md): test_coder_delivers_what_it_verified.py is a t286-class
slow-builder suite -- about 448 s of real work, cut off by the shared cap when the box is
saturated, green solo. Its ceiling entry was removed by python3 scripts/run_tests.py --ratchet
on the strength of ONE sweep in which it happened to pass, so the next saturated sweep reported
it as a NEW failure and the session spent its time on "broke it or flake?". The ceiling records
that status in prose (its _t286_readded key) -- prose --ratchet cannot read -- so the entry is
declared by hand, dropped by the ratchet, and re-declared, forever.

This oracle drives the ratchet decision as DATA: a ceiling dict and sweep-journal rows built in
memory. It writes no file, starts no subprocess, calls no model and touches no live state, so it
finishes in well under a second. A future pass may add a quiet-box load signal; nothing here
depends on one, because the sweep journal carries no load field (stated gap, not a placeholder).

Asserted, in the order a person would check by hand:
  - a kind=flake entry that passed ONE clean sweep is NOT dropped, and it gets a reason that
    quotes that entry OWN removed_only_when text -- the flake entry and the defect entry carry
    DIFFERENT texts, so an implementation that emits one shared generic string fails;
  - two clean-sweep passes on two DIFFERENT trees do drop it, and then it carries no reason;
  - two passes recorded on the SAME tree are one pass, not two: run_tests --resume/--cache
    reports a suite it never ran (run_tests.py line 561 appends reused passes without _tick);
  - a pass inside a sweep that did not finish clean (sweep row ok False) is not evidence a
    flake is fixed;
  - a suite recorded with rc 1 is never counted as a pass;
  - kind=defect is dropped the moment it passes, and an entry carrying no kind at all is too --
    all 226 entries in the ceiling today carry no kind, so an implementation defaulting an
    absent kind to flake silently breaks the ratchet for every existing entry;
  - kind=flake with no removed_only_when text written yet is still protected on one pass;
  - a passing suite that is not in the ceiling is never in the drop set;
  - ratchet_write is what --ratchet writes: the flake is still in allowed_to_fail, the suites
    that passed are gone, every other ceiling key survives (226 entries plus _the_three,
    _what, set), lowered_on carries the date given, and the caller dict is not mutated;
  - the --ratchet block inside main() routes through ratchet_drops and ratchet_write and no
    longer computes allowed_to_fail as sorted(failed | keep) -- the expression at run_tests.py
    line 626 that dropped the flake on one pass. A rule the runner does not call is not a rule.

Red on main for exactly one reason: scripts/run_tests.py defines none of FLAKE_MIN_PASS,
flake_droppable, ratchet_drops, ratchet_write, and its --ratchet block writes sorted(failed | keep).
Every call into the missing API is guarded, so this reports the gap and exits 1 -- it does not
crash, and nothing here imports a module that does not exist.
'''
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
import run_tests as RT

FLAKE = 'test_coder_delivers_what_it_verified.py'
DEFECT = 'test_ollama_page_wired.py'
SIBLING = 'test_scope_lint_evidence.py'
OUTSIDE = 'test_ceiling_owned.py'
FLAKE_RULE = ('comes off when the flake is FIXED (scratch dir cleaned in finally/atexit, suite '
              'serialised or given a timeout that survives contention), not when it happens to '
              'pass')
DEFECT_RULE = 'fixed by the t899 census, kept out of the sandbox for py_vapid'
NEEDED = ('flake_droppable', 'ratchet_drops', 'ratchet_write')
ok = 0
fail = 0


def check(label, cond):
    global ok, fail
    print('  [%s] %s' % ('ok' if cond else 'FAIL', label))
    if cond:
        ok += 1
    else:
        fail += 1


def suite_row(name, rc, tree, secs):
    return {'kind': 'suite', 'tree': tree, 'suite': name, 'rc': rc, 'secs': secs,
            'epoch': 1000.0 + secs, 'at': '2026-10-13T00:00:00Z'}


def sweep_row(tree, clean):
    return {'kind': 'sweep', 'tree': tree, 'ok': clean, 'total': 226, 'passing': 226,
            'epoch': 2000.0, 'at': '2026-10-13T00:10:00Z'}


HAS = all(hasattr(RT, n) for n in NEEDED)
if not HAS:
    print('  scripts/run_tests.py defines none of %s -- every behavioural check below is red '
          'for that reason, which is the change this oracle asks for' % ', '.join(NEEDED))


def ceiling_fixture():
    return {'_what': 'suites allowed to fail',
            'allowed_to_fail': sorted([FLAKE, DEFECT, SIBLING]),
            'kind': {FLAKE: 'flake', DEFECT: 'defect'},
            'removed_only_when': {FLAKE: FLAKE_RULE, DEFECT: DEFECT_RULE},
            '_the_three': {FLAKE: 't286 (t286): slow builder, fails under the 4-at-a-time pool'},
            'set': '2026-10-13',
            'lowered_on': '2026-10-01'}


def drops(passing, rows, ceiling):
    if not HAS:
        return set(), {}
    return RT.ratchet_drops(ceiling, passing, rows)


def write(ceiling, drop, today):
    if not HAS:
        return None
    return RT.ratchet_write(ceiling, drop, today)


def run():
    global ok, fail
    ceiling = ceiling_fixture()
    passing = set([FLAKE, DEFECT, SIBLING])
    one = [suite_row(FLAKE, 0, 'tree-a', 448.0),
           suite_row(DEFECT, 0, 'tree-a', 12.0),
           suite_row(SIBLING, 0, 'tree-a', 9.0),
           sweep_row('tree-a', True)]

    drop, reasons = drops(passing, one, ceiling)
    check('ONE clean-sweep pass does not drop a kind=flake entry', FLAKE not in drop)
    check('  ...while the suites that genuinely pass are dropped',
          drop == set([DEFECT, SIBLING]))
    check('the kept entry gets a reason a person can act on',
          isinstance(reasons.get(FLAKE), str) and bool(reasons.get(FLAKE)))
    check('  the reason quotes THIS entry removed_only_when text in full',
          isinstance(reasons.get(FLAKE), str) and FLAKE_RULE in reasons.get(FLAKE, ''))
    check('  and does not carry the other entry text',
          isinstance(reasons.get(FLAKE), str) and DEFECT_RULE not in reasons.get(FLAKE, ''))
    check('  exactly the kept entry has a reason, no dropped suite does',
          set(reasons) == set([FLAKE]))

    two = one + [suite_row(FLAKE, 0, 'tree-b', 450.0), sweep_row('tree-b', True)]
    drop2, reasons2 = drops(passing, two, ceiling)
    check('two clean-sweep passes on two DIFFERENT trees do drop it', FLAKE in drop2)
    check('  and once it is droppable it carries no reason', FLAKE not in reasons2)

    same = one + [suite_row(FLAKE, 0, 'tree-a', 451.0)]
    drop3, _r3 = drops(passing, same, ceiling)
    check('two passes recorded on the SAME tree are one pass, not two', FLAKE not in drop3)

    dirty = one + [suite_row(FLAKE, 0, 'tree-b', 452.0), sweep_row('tree-b', False)]
    drop4, _r4 = drops(passing, dirty, ceiling)
    check('a pass inside a sweep that did not finish clean does not count', FLAKE not in drop4)

    red = [suite_row(FLAKE, 1, 'tree-a', 301.9), sweep_row('tree-a', False)]
    drop5, _r5 = drops(passing, red, ceiling)
    check('a suite recorded with rc 1 is never counted as a pass', FLAKE not in drop5)

    d_drop, _dr = drops(set([DEFECT]), one, ceiling)
    check('kind=defect is dropped the moment it passes: the ratchet still ratchets',
          d_drop == set([DEFECT]))

    plain = {'allowed_to_fail': sorted([DEFECT]), 'kind': {}, 'removed_only_when': {}}
    p_drop, _pr = drops(set([DEFECT]), one, plain)
    check('an entry with NO kind is dropped on one pass, exactly as today -- all 226 entries in '
          'the ceiling today carry no kind', p_drop == set([DEFECT]))

    nokind = {'allowed_to_fail': sorted([FLAKE]), 'kind': {}, 'removed_only_when': {}}
    n_drop, _nr = drops(passing, one, nokind)
    check('kind=flake with no removed_only_when text written yet is protected on one pass',
          FLAKE not in n_drop)

    outside = {'allowed_to_fail': sorted([DEFECT]), 'kind': {}}
    o_drop, _or_ = drops(set([DEFECT, OUTSIDE]), one, outside)
    check('a passing suite that is not in the ceiling is never in the drop set',
          o_drop == set([DEFECT]))

    check('the passes a flake needs is 2: the two consecutive greens the RCA names',
          getattr(RT, 'FLAKE_MIN_PASS', None) == 2)

    written = write(ceiling_fixture(), drop, '2026-10-13')
    check('what --ratchet writes keeps the flake in allowed_to_fail',
          isinstance(written, dict) and written.get('allowed_to_fail') == [FLAKE])
    check('  and the suites that passed are gone from it',
          isinstance(written, dict) and DEFECT not in written['allowed_to_fail']
          and SIBLING not in written['allowed_to_fail'])
    check('  and every other key of the ceiling survives (_what, set, _the_three, kind, '
          'removed_only_when)',
          isinstance(written, dict) and all(k in written for k in ('_what', 'set', '_the_three',
                                                                   'kind', 'removed_only_when')))
    check('  and lowered_on carries the date it was given, not a hardcoded one',
          isinstance(written, dict) and written.get('lowered_on') == '2026-10-13')
    check('  and the caller ceiling dict is not mutated by writing it',
          ceiling['allowed_to_fail'] == sorted([FLAKE, DEFECT, SIBLING])
          and DEFECT in ceiling['allowed_to_fail'])

    written2 = write(ceiling_fixture(), drop2, '2026-10-13')
    check('  once the flake is droppable, --ratchet removes it',
          isinstance(written2, dict) and written2.get('allowed_to_fail') == [])

    src = ''
    try:
        src = Path(RT.__file__).read_text(encoding='utf-8')
    except OSError as e:
        print('  [FAIL] scripts/run_tests.py is unreadable: %s' % e)
        fail += 1
    start = src.find('if a.ratchet:')
    end = src.find('new = failed - allowed', start)
    block = src[start:end] if start >= 0 and end > start else ''
    check('the --ratchet block in main() routes through ratchet_drops', 'ratchet_drops(' in block)
    check('  and through ratchet_write', 'ratchet_write(' in block)
    check('  and no longer computes allowed_to_fail as sorted(failed | keep), the expression '
          'that dropped the flake on one pass', 'sorted(failed | keep)' not in block)

    print('')
    print('%d passed, %d failed' % (ok, fail))
    return 1 if fail else 0


if __name__ == '__main__':
    sys.exit(run())
