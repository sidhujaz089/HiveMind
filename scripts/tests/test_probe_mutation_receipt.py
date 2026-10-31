#!/usr/bin/env python3
'''Fail-first oracle for t1242: every probe verdict paired with a mutation-coverage receipt.

WHY THIS FILE EXISTS. workspace/probe-runs.jsonl holds 42 rows. 15 of them report an EMPTY
findings list, and 0 of the 42 carry a machine-checkable mutation count -- the keys present
across all 42 rows are at, model, rounds, findings, gate, transcript. The counts gate()
computes survive only as prose inside the human-readable 'gate' string. So 'no findings' is a
NAME, not an IDENTITY: from the record alone it is indistinguishable from 'the probe did not
run at all', which is the L-113 class this task names.

The fix is a RECEIPT in the record: every row probe.py writes carries mutations_total,
mutations_caught, mutations_missed, mutations_skipped and baseline. This suite pins it where
the row is BUILT, and it pins the honesty property: a count the code cannot recover is None,
never 0 -- a 0 is a claim that mutations ran and were caught.

RED on main for one reason: probe.mutation_receipt and probe.run_row do not exist. Verified
this session: 'import probe' succeeds, hasattr(probe, 'mutation_receipt') and
hasattr(probe, 'run_row') are both False, and importing writes nothing to the run log (md5 of
workspace/probe-runs.jsonl identical before and after). Read WHICH assertions fail: they must
be those two AttributeErrors (tests 1-5) plus the wiring assertion (test 6). An import failure,
a syntax error, or a count compared against a signature the brief does not define is a bug in
THIS file, not the missing change.

Pure: no model, no server, no subprocess, no urlopen, no writes. Rows are built in memory, so
there are no temp files and no /tmp paths.

Run: python3 scripts/tests/test_probe_mutation_receipt.py
'''
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
# probe.py imports jsonx, settings and loudcap at module level, before its own sys.path
# inserts, so BOTH candidate directories go on the path first -- the idiom proven working in
# scripts/tests/test_probe_extract.py:23-26 (that suite is green today, exit 0). An import
# failure must then mean something real about probe.py, never about a path this oracle forgot.
for _d in (ROOT / 'scripts', ROOT / 'projects' / 'local-inference'):
    _p = str(_d)
    if _p not in sys.path:
        sys.path.insert(0, _p)

import probe  # noqa: E402

RECEIPT_KEYS = {'mutations_total', 'mutations_caught', 'mutations_missed',
                'mutations_skipped', 'baseline'}

FAILS = []


def check(label, got, want):
    if got == want:
        print(f'  [ok]   {label}: {got!r}')
    else:
        FAILS.append(label)
        print(f'  [BAD]  {label}: expected {want!r}, got {got!r}')


# Report strings copied VERBATIM from projects/local-inference/probe.py gate(), with the real
# line number of the line that emits each:
#   :485  f"{caught} mutations caught, 0 missed, registry {before}"   the OK verdict
#   :478  f"{skipped} mutation(s) SKIPPED ... ({caught} caught, registry {before})"
#   :467  f"{missed} mutation(s) MISSED"                              today's MISSED
#   :463  "mutation registry did not report a result"                 no verdict at all
# The MISSED constant below is the FORM THIS TASK ASKS FOR at :467 -- carrying the counts its
# sibling reports already carry. An implementation that adds the two functions and leaves :467
# as it is today parses mutations_caught as None and fails test 3. Distinct numbers throughout
# (registry 119 against baseline 96; 82 caught of 85; 88 of 90) so a build that writes one
# number into both fields cannot pass. The invariant total == caught + missed + skipped holds
# in every case: 119 = 119+0+0, 85 = 82+3+0, 90 = 88+0+2.
CLEAN = '119 mutations caught, 0 missed, registry 119'
MISSED = '3 mutation(s) MISSED (82 caught, registry 85)'
SKIPPED = ('2 mutation(s) SKIPPED \u2014 their anchors no longer match, so those properties '
           'are no longer proven. A skipped mutation is not a passing one. Re-anchor them to '
           'the patched code or say why the property no longer applies. (88 caught, '
           'registry 90)')
NO_REPORT = 'mutation registry did not report a result'


def test_receipt_parses_a_clean_verdict():
    # EXACT dict equality: keys AND values. Key-presence checks and substring checks are
    # exactly where a receipt goes blind (oracle-weak, t471/t297/t370).
    got = probe.mutation_receipt(CLEAN, 96)
    check('clean verdict -> exact receipt',
          got, {'mutations_total': 119, 'mutations_caught': 119, 'mutations_missed': 0,
                'mutations_skipped': 0, 'baseline': 96})
    check('baseline is the argument, not a copy of the registry count',
          got.get('baseline'), 96)


def test_zero_finding_verdict_carries_a_full_receipt():
    # THE REASON THE MODULE EXISTS: findings=[] must be distinguishable from 'no probe ran'.
    transcript = [{'n': 1, 'kind': 'run', 'cmd': 'pwd', 'out': '/x'},
                  {'n': 2, 'kind': 'finding', 'finding': {'what': 'x'}}]
    row = probe.run_row('2026-08-31T05:09:12Z', 'qwen3.8:27b-mtp-q4_K_M', 60, [],
                        CLEAN, transcript, 96)
    check('a no-findings row carries all five receipt keys', RECEIPT_KEYS.issubset(row), True)
    check('no-findings row: mutations_total is the registry count, not 0',
          row.get('mutations_total'), 119)
    check('no-findings row: every mutation that ran was caught',
          row.get('mutations_caught') == row.get('mutations_total'), True)
    check('no-findings row: mutations_missed is 0', row.get('mutations_missed'), 0)
    check('no-findings row: mutations_skipped is 0', row.get('mutations_skipped'), 0)
    check('no-findings row: baseline is the launch baseline', row.get('baseline'), 96)
    check('an empty findings list is preserved, never turned into a count',
          row.get('findings'), [])
    # The record this change is ADDED to must survive it (never-truncate-a-record).
    check('row keeps at', row.get('at'), '2026-08-31T05:09:12Z')
    check('row keeps model', row.get('model'), 'qwen3.8:27b-mtp-q4_K_M')
    check('row keeps rounds', row.get('rounds'), 60)
    check('row keeps the gate verdict string whole', row.get('gate'), CLEAN)
    check('row keeps the whole transcript, not a prefix of it',
          row.get('transcript'), transcript)


def test_a_failed_verdict_receipt_keeps_what_ran():
    # A MISSED verdict is the case today's code records WORST: probe.py:467 emits only the
    # missed count, dropping the caught/registry counts the OK and SKIPPED paths carry.
    got = probe.mutation_receipt(MISSED, 96)
    check('MISSED verdict: registry count is the total', got.get('mutations_total'), 85)
    check('MISSED verdict: caught is recorded, not dropped', got.get('mutations_caught'), 82)
    check('MISSED verdict: missed is recorded', got.get('mutations_missed'), 3)
    check('MISSED verdict: nothing was skipped', got.get('mutations_skipped'), 0)
    check('MISSED verdict: baseline survives', got.get('baseline'), 96)


def test_a_skipped_verdict_receipt_names_the_skips():
    # SKIPPED is the shape probe.py:468-481 says most loudly: an unproven property, not a
    # passing one. A receipt that cannot say how many were skipped is the hole this task
    # exists to close -- a clean-looking total over a gap in coverage.
    got = probe.mutation_receipt(SKIPPED, 96)
    check('SKIPPED verdict: skips are recorded', got.get('mutations_skipped'), 2)
    check('SKIPPED verdict: registry count is the total', got.get('mutations_total'), 90)
    check('SKIPPED verdict: caught is recorded', got.get('mutations_caught'), 88)
    check('SKIPPED verdict: baseline survives', got.get('baseline'), 96)


def test_an_unparseable_verdict_says_none_not_zero():
    # THE NEGATIVE ASSERTION. A 0 here makes a probe that never ran look clean -- the exact
    # shape this task exists to stop. A build that defaults unknown counts to 0 is red.
    got = probe.mutation_receipt(NO_REPORT, 90)
    check('unparseable verdict: mutations_total is None, not 0',
          got.get('mutations_total'), None)
    check('unparseable verdict: mutations_caught is None, not 0',
          got.get('mutations_caught'), None)
    check('unparseable verdict: mutations_missed is None, not 0',
          got.get('mutations_missed'), None)
    check('unparseable verdict: mutations_skipped is None, not 0',
          got.get('mutations_skipped'), None)
    check('unparseable verdict: baseline is still recorded', got.get('baseline'), 90)
    check('receipt keys are present even when the counts are unknown',
          RECEIPT_KEYS.issubset(got), True)
    check('no count field is 0 for a verdict that reported nothing',
          any(got.get(k) == 0 for k in ('mutations_total', 'mutations_caught',
                                         'mutations_missed', 'mutations_skipped')), False)


def test_the_run_log_write_is_wired_to_run_row():
    # Support assertion, and the only source-shape assertion here. Tests 1-5 prove the two
    # functions do the right thing; NOT ONE of them can prove the writer CALLS them -- and a
    # receipt nothing writes is this task's own defect: a correct function with no caller
    # (check-satisfied-by-dead-path). Reading a tracked source file is what
    # scripts/tests/test_probe_extract.py already does (its '[ok] greedy regex call removed
    # from probe.py' line proves it).
    src = (ROOT / 'projects' / 'local-inference' / 'probe.py').read_text(encoding='utf-8')
    i = src.find('LOG.open(')
    check('probe.py still appends to the run log (the existing writer is not removed)',
          i > 0, True)
    if i > 0:
        check('the run-log append builds its row with run_row, so the receipt reaches the file',
              'run_row(' in src[i:i + 400], True)


TESTS = (test_receipt_parses_a_clean_verdict,
         test_zero_finding_verdict_carries_a_full_receipt,
         test_a_failed_verdict_receipt_keeps_what_ran,
         test_a_skipped_verdict_receipt_names_the_skips,
         test_an_unparseable_verdict_says_none_not_zero,
         test_the_run_log_write_is_wired_to_run_row)


def main():
    print('t1242 mutation-coverage receipt oracle (probe.py)')
    for t in TESTS:
        try:
            t()
        except Exception as e:
            FAILS.append(f'{t.__name__} raised {type(e).__name__}: {e}')
            print(f'  [BAD]  {t.__name__} raised {type(e).__name__}: {e}')
    if FAILS:
        print(f'\n{len(FAILS)} assertion(s) failed -- probe verdicts carry no mutation receipt')
        return 1
    print('\nall probe verdicts carry a machine-checkable mutation receipt')
    return 0


if __name__ == '__main__':
    sys.exit(main())
