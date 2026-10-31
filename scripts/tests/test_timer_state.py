'''Oracle for t1037 -- scripts/timer_state.py must separate installed, enabled and fired.

Why this is red today (verified 2026-10-11): `ls scripts/timer_state.py` reports No such
file, so every case below fails at import. The machine shows the same gap: systemctl
--user list-unit-files reports night-queue.timer and triage.timer DISABLED, neither
appears in list-timers, and neither has a symlink under
~/.config/systemd/user/timers.target.wants, while deploy/systemd carries all three
.timer files. Installed, enabled and fired are three states and nothing separates them.

Live state is never touched: every path is a temp dir and every systemctl call goes to
FakeSystemctl below. The task's runtime half (the module printing UNARMED night-queue.timer
and UNARMED triage.timer on the current machine) is asserted here as a PROPERTY, built from
the repo's real timer names -- asserting it against ~/.config would go green or red with
the machine, not with the code.

The seven names the implementation must provide: intended, armed, live_state, rows,
format_rows, record, main. Each test below says which it pins.
'''
import json
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))

FAILS = []

# The population, verified 2026-10-11 with `ls deploy/systemd`.
VERIFIED_TIMERS = ['nas-backup.timer', 'night-queue.timer', 'triage.timer']

# What `systemctl --user show` prints for a running timer and for a disabled one. Real
# shapes: systemd prints n/a for a property that has no value, so a parser that stores the
# string 'n/a' in the record is a bug. live_state must ask for the Unit property so each
# block can be attributed to its unit -- asserted in the first test.
ACTIVE_BLOCK = ('Type=oneshot\n'
                'Unit={name}\n'
                'ActiveState=active\n'
                'UnitFileState=enabled\n'
                'LastTriggerUSec=Sat 2026-10-10 04:33:44 MDT\n'
                'NextElapseUSecRealtime=Sun 2026-10-11 04:36:54 MDT\n')
INACTIVE_BLOCK = ('Type=oneshot\n'
                  'Unit={name}\n'
                  'ActiveState=inactive\n'
                  'UnitFileState=disabled\n'
                  'LastTriggerUSec=n/a\n'
                  'NextElapseUSecRealtime=n/a\n')


class _Proc:
    def __init__(self, stdout):
        self.returncode = 0
        self.stdout = stdout
        self.stderr = ''


class FakeSystemctl:
    '''Answers `systemctl --user show` for a fixed population and REFUSES anything else.

    A stub that served the next scripted reply to any call would let a module that ENABLES
    units, or asks systemd about units outside the repo tree, pass unnoticed. It answers
    only reads of units it was built to answer for.'''

    def __init__(self, answers):
        self.answers = answers
        self.calls = []

    def __call__(self, argv, **kw):
        a = [str(x) for x in argv]
        if a[:2] != ['systemctl', '--user'] or 'show' not in a:
            raise AssertionError('timer_state must READ unit state, never change it: ' + repr(a))
        if not kw.get('timeout'):
            raise AssertionError('every systemctl call needs timeout=: ' + repr(a))
        if kw.get('text') is not True:
            raise AssertionError('systemctl output must be decoded, text=True: ' + repr(a))
        asked = sorted(x for x in a if x.endswith('.timer'))
        if not asked:
            raise AssertionError('asked systemd about no timer: ' + repr(a))
        outside = [x for x in asked if x not in self.answers]
        if outside:
            raise AssertionError('asked systemd about units outside the repo population: ' + repr(outside))
        self.calls.append(a)
        return _Proc(''.join(self.answers[n].format(name=n) for n in asked))


def ck(what, got, want):
    ok = got == want
    print('  [' + ('ok' if ok else 'FAIL') + '] ' + what)
    print('        got  ' + repr(got))
    print('        want ' + repr(want))
    if not ok:
        FAILS.append(what)


def _is_json(line):
    try:
        json.loads(line)
        return True
    except Exception as exc:
        print('        not json (' + type(exc).__name__ + '): ' + repr(line))
        return False


def _repo_tree(base):
    '''A root whose deploy/systemd holds the real tracked unit files (read-only from the
    repo; falls back to a minimal timer body if the tree is not present).'''
    root = base / 'repo'
    dep = root / 'deploy' / 'systemd'
    dep.mkdir(parents=True)
    for name in VERIFIED_TIMERS:
        src = ROOT / 'deploy' / 'systemd' / name
        body = src.read_text() if src.is_file() else '[Timer]\nOnCalendar=*-*-* 02:00:00\n'
        (dep / name).write_text(body)
    return root


def _wants(base, names):
    unit_dir = base / 'user'
    unit_dir.mkdir(parents=True, exist_ok=True)
    wants = base / 'timers.target.wants'
    wants.mkdir(parents=True, exist_ok=True)
    for name in names:
        (unit_dir / name).write_text('[Timer]\nOnCalendar=*-*-* 02:00:00\n')
        (wants / name).symlink_to(unit_dir / name)
    return wants


def _answers(active):
    return {n: (ACTIVE_BLOCK if n in active else INACTIVE_BLOCK) for n in VERIFIED_TIMERS}


LIVE = {
    'nas-backup.timer': {'active': 'active', 'unit_file_state': 'enabled',
                         'last_fired': 'Sat 2026-10-10 04:33:44 MDT',
                         'next_fire': 'Sun 2026-10-11 04:36:54 MDT'},
    'night-queue.timer': {'active': 'inactive', 'unit_file_state': 'disabled',
                          'last_fired': None, 'next_fire': None},
    'triage.timer': {'active': 'inactive', 'unit_file_state': 'disabled',
                     'last_fired': None, 'next_fire': None},
}

ROWS = [
    {'at': '2026-10-11T05:12:07Z', 'unit': 'night-queue.timer', 'repo_present': True,
     'enabled': False, 'last_fired': None, 'next_fire': None},
    {'at': '2026-10-11T05:12:07Z', 'unit': 'triage.timer', 'repo_present': True,
     'enabled': False, 'last_fired': None, 'next_fire': None},
    {'at': '2026-10-11T05:12:07Z', 'unit': 'nas-backup.timer', 'repo_present': True,
     'enabled': True, 'last_fired': 'Sat 2026-10-10 04:33:44 MDT',
     'next_fire': 'Sun 2026-10-11 04:36:54 MDT'},
]


def test_intended_is_the_repo_timer_tree(TS):
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        dep = root / 'deploy' / 'systemd'
        dep.mkdir(parents=True)
        (dep / 'night-queue.timer').write_text('[Timer]\nOnCalendar=*-*-* 02:00:00\n')
        (dep / 'triage.timer').write_text('[Timer]\nOnCalendar=*-*-* 03:45:00\n')
        (dep / 'extra.timer').write_text('[Timer]\nOnCalendar=*-*-* 04:30:00\n')
        (dep / 'night-queue.service').write_text('[Service]\n')
        (dep / 'notes.md').write_text('not a unit\n')
        ck('intended is the sorted .timer population; .service and .md excluded',
           TS.intended(root=root), ['extra.timer', 'night-queue.timer', 'triage.timer'])
        ck('an empty tree is an empty population, not a crash',
           TS.intended(root=root / 'no-such-dir'), [])
    listed = sorted(p.name for p in (ROOT / 'deploy' / 'systemd').glob('*.timer'))
    ck('the repo population is exactly the .timer files in deploy/systemd',
       TS.intended(), listed)
    for name in VERIFIED_TIMERS:
        ck('the tracked timer is in the intended set: ' + name, name in TS.intended(), True)


def test_armed_is_the_wants_symlink_tree(TS):
    with tempfile.TemporaryDirectory() as d:
        base = Path(d)
        unit_dir = base / 'user'
        unit_dir.mkdir()
        wants = base / 'timers.target.wants'
        wants.mkdir()
        target = unit_dir / 'night-queue.timer'
        target.write_text('[Timer]\n')
        (wants / 'night-queue.timer').symlink_to(target)
        (wants / 'ghost.timer').symlink_to(unit_dir / 'no-such-timer.timer')
        (wants / 'plain-file.timer').write_text('[Timer]\n')
        ck('armed is every symlink in timers.target.wants, a dangling one included',
           sorted(TS.armed(wants_dir=wants)), ['ghost.timer', 'night-queue.timer'])
        ck('a plain file in the wants dir is not an armed unit',
           'plain-file.timer' in TS.armed(wants_dir=wants), False)
        ck('a wants dir that does not exist is empty, not a crash',
           sorted(TS.armed(wants_dir=base / 'nope')), [])


def test_live_state_maps_each_unit_by_name(TS):
    fake = FakeSystemctl({'night-queue.timer': INACTIVE_BLOCK, 'nas-backup.timer': ACTIVE_BLOCK})
    got = TS.live_state(['night-queue.timer', 'nas-backup.timer'], runner=fake)
    ck('live_state keys its rows by unit name', sorted(got), ['nas-backup.timer', 'night-queue.timer'])
    ck('a live row carries these four fields',
       sorted(got['night-queue.timer']), sorted(['active', 'last_fired', 'next_fire', 'unit_file_state']))
    ck('ActiveState parsed for a disabled timer', got['night-queue.timer']['active'], 'inactive')
    ck('UnitFileState parsed into unit_file_state', got['night-queue.timer']['unit_file_state'], 'disabled')
    ck('a timer systemd has never fired is None, not the string n/a and not an empty string',
       got['night-queue.timer']['last_fired'], None)
    ck('a timer with no next elapse is None, not the string n/a',
       got['night-queue.timer']['next_fire'], None)
    ck('ActiveState parsed for the running timer', got['nas-backup.timer']['active'], 'active')
    ck('LastTriggerUSec parsed into last_fired', got['nas-backup.timer']['last_fired'],
       'Sat 2026-10-10 04:33:44 MDT')
    ck('NextElapseUSecRealtime parsed into next_fire', got['nas-backup.timer']['next_fire'],
       'Sun 2026-10-11 04:36:54 MDT')
    ck('live_state asks systemd for the Unit property so each row can be attributed',
       'Unit' in [str(x) for x in fake.calls[0]], True)


def test_rows_carry_six_fields_for_every_intended_timer(TS):
    rs = TS.rows(VERIFIED_TIMERS, {'nas-backup.timer'}, LIVE)
    by = {r['unit']: r for r in rs}
    ck('one row per intended timer', sorted(by), sorted(VERIFIED_TIMERS))
    ck('row keys', sorted(rs[0]),
       sorted(['at', 'unit', 'repo_present', 'enabled', 'last_fired', 'next_fire']))
    ck('the armed timer is enabled', by['nas-backup.timer']['enabled'], True)
    ck('a repo timer with no wants symlink is not enabled', by['night-queue.timer']['enabled'], False)
    ck('repo_present is true for a tracked timer', by['triage.timer']['repo_present'], True)
    ck('the live next fire is carried through', by['nas-backup.timer']['next_fire'],
       'Sun 2026-10-11 04:36:54 MDT')
    ck('the live last fire is carried through', by['nas-backup.timer']['last_fired'],
       'Sat 2026-10-10 04:33:44 MDT')
    ck('a timer systemd has not fired records None', by['triage.timer']['last_fired'], None)
    ck('an armed unit outside the repo tree gets no row',
       'ghost.timer' in [r['unit'] for r in TS.rows(VERIFIED_TIMERS, {'ghost.timer'}, LIVE)], False)


def test_format_reports_each_unarmed_timer_by_name(TS):
    out = TS.format_rows(ROWS)
    ck('one line per row', len(out), 3)
    ck('the un-armed night-queue timer is named UNARMED',
       any('UNARMED night-queue.timer' in s for s in out), True)
    ck('the un-armed triage timer is named UNARMED',
       any('UNARMED triage.timer' in s for s in out), True)
    ck('the armed timer is reported on an ARMED line',
       any(s.split()[0] == 'ARMED' and 'nas-backup.timer' in s for s in out), True)
    ck('an armed timer is never reported UNARMED',
       any('UNARMED nas-backup.timer' in s for s in out), False)


def test_record_appends_one_json_row_per_timer(TS):
    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / 'timer-state.jsonl'
        out.write_text('{"at": "sentinel", "unit": "pre-existing"}\n')
        TS.record(out, ROWS)
        lines = out.read_text().splitlines()
        ck('record appends, it does not overwrite', len(lines), 4)
        ck('the pre-existing line survives, first', json.loads(lines[0]),
           {'at': 'sentinel', 'unit': 'pre-existing'})
        ck('every appended row is valid json', all(_is_json(l) for l in lines[1:]), True)
        ck('an appended row carries the six fields',
           sorted(json.loads(lines[1])),
           sorted(['at', 'unit', 'repo_present', 'enabled', 'last_fired', 'next_fire']))
        TS.record(out, ROWS)
        ck('record is append-only across runs', len(out.read_text().splitlines()), 7)


def test_main_reports_drift_when_a_repo_timer_is_not_armed(TS):
    with tempfile.TemporaryDirectory() as d:
        base = Path(d)
        root = _repo_tree(base)
        wants = _wants(base, ['nas-backup.timer'])
        out = base / 'timer-state.jsonl'
        rc = TS.main(root=root, wants_dir=wants, out=out, runner=FakeSystemctl(_answers(['nas-backup.timer'])))
        ck('main exits 1 when a tracked timer is not armed', rc, 1)
        got = [json.loads(l) for l in out.read_text().splitlines()]
        ck('main writes one row per tracked timer', sorted(r['unit'] for r in got), sorted(VERIFIED_TIMERS))
        ck('the un-armed timer is recorded enabled false',
           [r['enabled'] for r in got if r['unit'] == 'night-queue.timer'], [False])
        ck('the armed timer is recorded enabled true',
           [r['enabled'] for r in got if r['unit'] == 'nas-backup.timer'], [True])
        ck('the record keeps the live fire time, not a stand-in',
           [r['next_fire'] for r in got if r['unit'] == 'nas-backup.timer'],
           ['Sun 2026-10-11 04:36:54 MDT'])
        ck('at is the UTC ISO stamp night_queue.sh writes',
           sorted(bool(re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z', r['at'])) for r in got),
           [True])
        ck('one census writes one stamp', len({r['at'] for r in got}), 1)
        ck('main wrote only the record file into the tree it was given',
           sorted(p.name for p in base.iterdir()),
           sorted(['repo', 'timers.target.wants', 'timer-state.jsonl', 'user']))


def test_main_exits_zero_when_every_tracked_timer_is_armed(TS):
    with tempfile.TemporaryDirectory() as d:
        base = Path(d)
        root = _repo_tree(base)
        wants = _wants(base, VERIFIED_TIMERS)
        out = base / 'timer-state.jsonl'
        rc = TS.main(root=root, wants_dir=wants, out=out, runner=FakeSystemctl(_answers(VERIFIED_TIMERS)))
        ck('main exits 0 when every tracked timer is armed', rc, 0)
        got = [json.loads(l) for l in out.read_text().splitlines()]
        ck('all rows recorded enabled true', sorted({r['enabled'] for r in got}), [True])
        ck('the armed census records the next fire for every timer',
           sorted({r['next_fire'] for r in got}), ['Sun 2026-10-11 04:36:54 MDT'])


def _load():
    try:
        import timer_state
        return timer_state
    except Exception as exc:
        ck('scripts/timer_state.py is importable', type(exc).__name__ + ': ' + str(exc), 'importable')
        return None


def _run():
    TS = _load()
    tests = sorted((v for k, v in globals().items() if k.startswith('test_') and callable(v)),
                   key=lambda f: f.__name__)
    for f in tests:
        print('-- ' + f.__name__)
        try:
            f(TS)
        except Exception as exc:
            FAILS.append(f.__name__ + ': ' + type(exc).__name__ + ': ' + str(exc))
            print('  [FAIL] ' + f.__name__ + ' raised ' + type(exc).__name__ + ': ' + str(exc))
    if FAILS:
        print('test_timer_state: FAILED (' + str(len(FAILS)) + ')')
        for f in FAILS:
            print('  - ' + f)
        return 1
    print('test_timer_state: ok')
    return 0


if __name__ == '__main__':
    sys.exit(_run())
