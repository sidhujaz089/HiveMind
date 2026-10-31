#!/usr/bin/env python3
'''t1012, half one: the version-state recorder reads the app's own line and round-trips a ledger.

The case this exists for (RCA 11c4f849789b): the Claude CLI ran pinned to 2.1.237 from 2026-09-05 to
2026-10-07. Nothing recorded the running version, so "we are pinned" was unfalsifiable between
incidents -- it was proved 30 days later by hand-grepping four rotated app logs. What log_baseline.py
has is a RATE delta (24 h against the 7 days before), which is structurally blind to a condition that
is constant for a month. The fix is a STATE row and an ABSOLUTE verdict: what is installed versus the
highest version the app has named. This suite pins the recorder and the ledger; the wiring into
report() is pinned by scripts/tests/test_toolchain_version_state.py.

Hermetic: every path is under one temp dir made by tempfile.mkdtemp. The real ~/.config/Claude logs,
the real install directory and the real workspace/ledgers/toolchain-versions.jsonl are never read and
never written -- every version_state, read and append call below is handed a temp path.
'''
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
import log_baseline as lb  # noqa: E402

FAILS = []
NOW = time.mktime(time.strptime('2026-10-10 08:07:33', '%Y-%m-%d %H:%M:%S'))
TS = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(NOW))
TODAY = TS[:10]
H = Path(tempfile.mkdtemp())
_n = [0]


def ck(name, cond, detail=''):
    print(f"  [{'ok' if cond else 'BAD'}]  {name}" + ('' if cond else f'  {detail}'))
    if not cond:
        FAILS.append(name)


def install_root(*versions):
    """Mirror the app install directory: a version counts only when its directory has a file in it."""
    _n[0] += 1
    root = H / f'inst{_n[0]}'
    for v in versions:
        (root / v).mkdir(parents=True, exist_ok=True)
        (root / v / 'cli.js').write_text('x\n', encoding='utf-8')
    return root


def logdir(*lines):
    _n[0] += 1
    d = H / f'logs{_n[0]}'
    d.mkdir(parents=True, exist_ok=True)
    if lines:
        (d / 'main.log').write_text(''.join(l + '\n' for l in lines), encoding='utf-8')
    return d


def add_rotated(d, name, *lines):
    """A rotated file: where a 30-day-old pin is found."""
    (d / name).write_text(''.join(l + '\n' for l in lines), encoding='utf-8')


def t1_the_fallback_line_is_read_at_info_level():
    root = install_root('2.1.237')
    d = logdir('2026-10-10 07:00:00 [info] Falling back to installed version: 2.1.237',
               "2026-10-10 07:00:01 [error] EINVAL: invalid argument, open '/home/x/.config/Claude/p1'")
    st = lb.version_state(folder=d, install_root=root, now=NOW)
    assert st['ts'] == TS, st
    assert st['harness'] == 'claude-desktop', st
    assert st['installed_versions'] == ['2.1.237'], st
    assert st['active_version'] == '2.1.237', st
    assert st['highest_version_seen'] == '2.1.237', st
    assert st['verdict'] == 'CURRENT', st
    ck('T5: the row records the [level] tag of the line it read', st['level'] == 'info', st)
    ck('an [info] fallback line is read -- the [error|warn] filter is not applied to it',
       st['active_version'] == '2.1.237', st)
    ck('the row names the file it came from, so a reader can go and look',
       st['source'] == str(d / 'main.log'), st)
    ck('the row is stamped in UTC, to the second, from the now passed in', st['ts'] == TS, st)
    ck('the harness is named, so the ledger separates runtimes', st['harness'] == 'claude-desktop', st)


def t2_the_rca_fixture_is_a_pin():
    """The task fixture, verbatim: installed 2.1.237 with highest seen 2.1.284 yields PINNED."""
    root = install_root('2.1.237')
    d = logdir('2026-10-07 23:56:00 [warn] Falling back to installed version: 2.1.284')
    add_rotated(d, 'main.1.log', '2026-09-05 14:07:59 [warn] Falling back to installed version: 2.1.237')
    st = lb.version_state(folder=d, install_root=root, now=NOW)
    assert st['installed_versions'] == ['2.1.237'], st
    assert st['highest_version_seen'] == '2.1.284', st
    assert st['verdict'] == 'PINNED', st
    ck('(b) installed 2.1.237 against highest seen 2.1.284 is a PINNED verdict', st['verdict'] == 'PINNED', st)
    ck('the newest fallback line is the active version, not the first', st['active_version'] == '2.1.284', st)
    ck('a version named only in a ROTATED log counts -- a 30-day-old pin lives there',
       st['highest_version_seen'] == '2.1.284', st)
    ck('the installed set comes from the install dir, so it can disagree with the logs',
       st['installed_versions'] == ['2.1.237'] and st['highest_version_seen'] == '2.1.284', st)
    ck('the level tag is the level of the newest line, not of the first', st['level'] == 'warn', st)
    ck('source names the file that held the newest line', st['source'] == str(d / 'main.log'), st)


def t3_installed_versions_sort_numerically():
    """2.1.9 and 2.1.100: a string sort puts 2.1.100 first and calls it the older one."""
    root = install_root('2.1.9', '2.1.100')
    d = logdir('2026-10-10 07:00:00 [warn] Falling back to installed version: 2.1.100')
    st = lb.version_state(folder=d, install_root=root, now=NOW)
    assert st['installed_versions'] == ['2.1.9', '2.1.100'], st
    assert st['verdict'] == 'CURRENT', st
    ck('the installed list is ordered by version number, not as text',
       st['installed_versions'] == ['2.1.9', '2.1.100'], st)


def t4_the_highest_seen_must_be_newer_than_the_newest_installed():
    """2.1.9 installed against 2.1.100 seen: a string comparison says 2.1.9 is newer, so it reads CURRENT."""
    root = install_root('2.1.9')
    d = logdir('2026-10-10 07:00:00 [warn] Falling back to installed version: 2.1.100')
    st = lb.version_state(folder=d, install_root=root, now=NOW)
    assert st['verdict'] == 'PINNED', st
    ck('2.1.9 installed against 2.1.100 seen is a pin, not up to date', st['verdict'] == 'PINNED', st)


def t5_an_empty_version_directory_is_not_installed():
    """2026-09-05: the update downloaded into an EMPTY 2.1.255 and the app stayed on 2.1.237."""
    _n[0] += 1
    root = H / f'inst{_n[0]}'
    (root / '2.1.255').mkdir(parents=True, exist_ok=True)
    (root / '2.1.237').mkdir(parents=True, exist_ok=True)
    (root / '2.1.237' / 'cli.js').write_text('x\n', encoding='utf-8')
    d = logdir('2026-10-10 07:00:00 [warn] Falling back to installed version: 2.1.255')
    st = lb.version_state(folder=d, install_root=root, now=NOW)
    assert st['installed_versions'] == ['2.1.237'], st
    assert st['verdict'] == 'PINNED', st
    ck('a version directory with nothing in it is a failed download, not an installed version',
       st['installed_versions'] == ['2.1.237'], st)


def t6_a_torn_last_line_is_readable_and_said_whole():
    """The reason a ledger exists: a run killed mid-write leaves a half line behind."""
    led = H / 'torn.jsonl'
    lb.append_version_row(led, {'ts': '2026-10-01T00:00:00Z', 'verdict': 'CURRENT'})
    lb.append_version_row(led, {'ts': '2026-10-02T00:00:00Z', 'verdict': 'PINNED'})
    with led.open('a', encoding='utf-8') as fh:
        fh.write('{"ts": "2026-10-05T00:00:00Z", "verdict": "PINN')     # process died mid-write
    rows, bad = lb.read_version_rows(led)
    assert len(rows) == 2, rows
    assert rows[0]['ts'] == '2026-10-01T00:00:00Z', rows
    assert rows[1]['verdict'] == 'PINNED', rows
    assert bad == ['{"ts": "2026-10-05T00:00:00Z", "verdict": "PINN'], bad
    ck('a torn final line does not take the whole ledger down with it', len(rows) == 2, rows)
    ck('the torn fragment is reported WHOLE -- evidence, never dropped, never cut',
       len(bad) == 1 and bad[0].startswith('{"ts": "2026-10-05'), bad)


def t7_a_recorded_value_is_never_cut():
    led = H / 'wide.jsonl'
    src = '/home/jas/.config/Claude/logs/main.log:4021 Falling back to installed version: 2.1.284 ' + 'x' * 5000
    lb.append_version_row(led, {'ts': '2026-10-03T00:00:00Z', 'source': src})
    rows, bad = lb.read_version_rows(led)
    assert rows[0]['source'] == src, rows
    ck('a long source string round-trips byte for byte: no cap on a recorded value',
       len(rows[0]['source']) == len(src), rows)


def t8_today_row_is_keyed_on_the_run_date():
    led = H / 'ledger.jsonl'
    rows, bad = lb.read_version_rows(led)
    assert rows == [] and bad == [], (rows, bad)
    assert lb.today_row(led, TODAY) is None, 'a ledger with no row for today must read as missing'
    lb.append_version_row(led, {'ts': TS, 'verdict': 'PINNED'})
    rows, bad = lb.read_version_rows(led)
    assert rows == [{'ts': TS, 'verdict': 'PINNED'}], rows
    assert bad == [], bad
    ck('append then read returns the row whole, keys and all', rows == [{'ts': TS, 'verdict': 'PINNED'}], rows)
    ck('the row for today is found after the write', lb.today_row(led, TODAY)['verdict'] == 'PINNED', rows)
    ck('a date with no row is not answered with the newest row', lb.today_row(led, '1999-01-01') is None, rows)


def t9_no_data_is_unknown_and_never_current():
    d = logdir()
    root = H / 'inst-empty'
    root.mkdir(parents=True, exist_ok=True)
    st = lb.version_state(folder=d, install_root=root, now=NOW)
    assert st['verdict'] == 'UNKNOWN', st
    assert st['active_version'] is None, st
    assert st['highest_version_seen'] is None, st
    assert st['installed_versions'] == [], st
    ck('a machine whose version cannot be determined SAYS so', st['verdict'] == 'UNKNOWN', st)


def t10_installed_but_no_log_line_is_not_claimed_current():
    """An install directory alone does not prove what is RUNNING: the verdict must stay UNKNOWN."""
    root = install_root('2.1.237')
    st = lb.version_state(folder=logdir(), install_root=root, now=NOW)
    assert st['verdict'] == 'UNKNOWN', st
    assert st['installed_versions'] == ['2.1.237'], st
    ck('no fallback line means no claim of CURRENT, even with a version installed', st['verdict'] == 'UNKNOWN', st)


def t11_the_ledger_is_the_path_the_task_names():
    assert lb.DEFAULT_LEDGER.name == 'toolchain-versions.jsonl', lb.DEFAULT_LEDGER
    assert lb.DEFAULT_LEDGER.parent.name == 'ledgers', lb.DEFAULT_LEDGER
    assert lb.DEFAULT_LEDGER.parents[1].name == 'workspace', lb.DEFAULT_LEDGER
    ck('the default ledger is the repo workspace/ledgers/toolchain-versions.jsonl',
       ROOT in lb.DEFAULT_LEDGER.parents, str(lb.DEFAULT_LEDGER))


TESTS = [
    ('the fallback line is read at [info] level and its level tag is recorded',
     t1_the_fallback_line_is_read_at_info_level),
    ('installed 2.1.237 with highest seen 2.1.284 is a PINNED verdict', t2_the_rca_fixture_is_a_pin),
    ('installed versions sort numerically, not as text', t3_installed_versions_sort_numerically),
    ('the highest version seen must be compared numerically against the installed',
     t4_the_highest_seen_must_be_newer_than_the_newest_installed),
    ('an empty version directory is a failed download, not an installed version',
     t5_an_empty_version_directory_is_not_installed),
    ('a torn last line is readable and the fragment is said whole', t6_a_torn_last_line_is_readable_and_said_whole),
    ('a recorded value is never truncated', t7_a_recorded_value_is_never_cut),
    ('today row is missing before the write and keyed on the run date', t8_today_row_is_keyed_on_the_run_date),
    ('no data reads as UNKNOWN, never as CURRENT', t9_no_data_is_unknown_and_never_current),
    ('installed versions with no fallback line are UNKNOWN, not CURRENT',
     t10_installed_but_no_log_line_is_not_claimed_current),
    ('the default ledger is workspace/ledgers/toolchain-versions.jsonl', t11_the_ledger_is_the_path_the_task_names),
]


def run(name, fn):
    try:
        fn()
    except Exception as e:          # a missing function is ONE named failure, not a stack trace
        ck(name, False, f'{type(e).__name__}: {e}')


if __name__ == "__main__":
    print('test_toolchain_version_ledger:')
    for name, fn in TESTS:
        run(name, fn)
    print('test_toolchain_version_ledger:', 'FAILED' if FAILS else 'ok')
    sys.exit(1 if FAILS else 0)
