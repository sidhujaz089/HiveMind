"""t1228: the standing-process census records the box clock, so a file's local mtime can be
 cited
against a transcript's UTC timestamps without an inference (RCA cd2b5984b941, section 4 row 5, T7).

RED ON MAIN: scripts/process_registry.py defines no box_clock, its snapshot() returns only the keys
cron and units, and its module docstring never mentions a clock. No live crontab, systemctl,
timedatectl, model call or process is touched: every subprocess goes through an injected fake run
(each call carries timeout=), the zone is read from that fake (a SECOND fake zone must change the
answer, so a hard-coded return cannot pass), the offset is computed from the box's own stdlib
clock, and every write goes under tempfile.mkdtemp().
"""
import json
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import process_registry as P

ok = fail = 0


def check(name, cond):
    global ok, fail
    print('  [' + ('ok' if cond else 'FAIL') + ']   ' + name)
    ok += bool(cond)
    fail += not cond


class _Res:
    def __init__(self, stdout):
        self.stdout = stdout


def _fake(zone):
    """A stub that REFUSES traffic it was not written to answer."""
    def run(cmd, **kw):
        assert 'timeout' in kw, 'subprocess call with no timeout=: %r' % (cmd,)
        c = list(cmd)
        if c[:2] == ['crontab', '-l']:
            return _Res('*/5 * * * * python3 scripts/heartbeat.py\n')
        if c[:3] == ['systemctl', '--user', 'list-unit-files']:
            return _Res('scoping-queue@manager.service enabled enabled\n')
        if c[:2] == ['systemctl', 'list-unit-files']:
            return _Res('')
        if c[:2] == ['timedatectl', 'show']:
            assert 'Timezone' in c, 'timedatectl asked for something else: %r' % (c,)
            return _Res('Timezone=' + zone + '\n')
        raise AssertionError('unexpected request: %r' % (c,))
    return run


WANT = int(time.localtime().tm_gmtoff) // 60      # the box's own offset, computed here

clock = getattr(P, 'box_clock', None)
check('box_clock exists on the module', callable(clock))
for _n in ('  the zone comes from the injected timedatectl answer, not a stored value',
           '  a second, different zone is reported as that zone (computed, not cached)',
           '  box_clock returns exactly the keys timezone + utc_offset_minutes',
           '  utc_offset_minutes is the int the box clock implies, in minutes',
           '  and it is a sane minute value, not seconds and not a truncated slice'):
    if callable(clock):
        km = P.box_clock(run=_fake('Asia/Kathmandu'))
        ed = P.box_clock(run=_fake('America/Edmonton'))
        check('  the zone comes from the injected timedatectl answer, not a stored value',
              km.get('timezone') == 'Asia/Kathmandu')
        check('  a second, different zone is reported as that zone (computed, not cached)',
              ed.get('timezone') == 'America/Edmonton')
        check('  box_clock returns exactly the keys timezone + utc_offset_minutes',
              set(km) == {'timezone', 'utc_offset_minutes'})
        check('  utc_offset_minutes is the int the box clock implies, in minutes',
              isinstance(ed.get('utc_offset_minutes'), int)
              and ed.get('utc_offset_minutes') == WANT)
        check('  and it is a sane minute value, not seconds and not a truncated slice',
              -720 <= int(ed.get('utc_offset_minutes')) <= 840)
        break
    check(_n, False)

snap = P.snapshot(run=_fake('America/Edmonton'))
check('snapshot() carries a clock key', 'clock' in snap)
check('  and it is the shape box_clock returns',
      isinstance(snap.get('clock'), dict)
      and {'timezone', 'utc_offset_minutes'} <= set(snap.get('clock', {})))
check('  cron and units are recorded exactly as today (the census still works)',
      snap.get('cron') == ['*/5 * * * * python3 scripts/heartbeat.py']
      and 'user:scoping-queue@manager.service' in snap.get('units', {}))

shifted = dict(snap, clock={'timezone': 'America/Edmonton', 'utc_offset_minutes': WANT + 60})
try:
    _row = P.record(shifted, prev=snap, flow_text='"heartbeat" "manager-queue"', write=False)
    check('a clock-only difference writes NO change row (a DST change must not spam the census)',
          _row is None)
except TypeError as e:
    check('record() takes the census row unchanged (signature problem: %s)' % e, False)

_tmp = Path(tempfile.mkdtemp())
_keep = (getattr(P, 'SNAP', None), getattr(P, 'LEDGER', None))
try:
    if _keep[0] is not None:
        P.SNAP = _tmp / 'process-registry.json'
    if _keep[1] is not None:
        P.LEDGER = _tmp / 'process-changes.jsonl'
    P.record(snap, prev=None, flow_text='"heartbeat" "manager-queue"', write=True)
    _written = json.loads(Path(P.SNAP).read_text(encoding='utf-8'))
    check('the census file written to disk carries the clock (so local mtimes become citable)',
          _written.get('clock', {}).get('timezone') == 'America/Edmonton')
    check('  and keeps cron and units alongside it',
          _written.get('cron') == ['*/5 * * * * python3 scripts/heartbeat.py'])
finally:
    if _keep[0] is not None:
        P.SNAP = _keep[0]
    if _keep[1] is not None:
        P.LEDGER = _keep[1]

check('the module docstring says the census records the box clock',
      'clock' in (P.__doc__ or '').lower())

print('%d passed, %d failed' % (ok, fail))
sys.exit(1 if fail else 0)
