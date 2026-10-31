#!/usr/bin/env python3
'''t952 -- the notifications lane records its own cert probe.

RCA 032a3ac46eb2: the TLS-cert blocker lived in a handover sentence and was re-discovered four
times in five days because nothing recorded the probe at the lane. These tests pin the writer:
notify.py appends one JSON row per probe to workspace/notifications/notify-probes.jsonl, with the
verbatim reply, and shouts at the status reader when the newest row goes stale.

No network, no tailscale, no real keypair: every path is redirected into a temp dir and the
subprocess is faked. The real keypair, subscription and toggle files are only ever read.
'''
import contextlib
import io
import json
import re
import socket
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / 'scripts'))

BLOCKED = '500 Internal Server Error: your Tailscale account does not support getting TLS certs'
ISSUED = 'issued certificate for hive.example.ts.net; expires 2026-11-08T00:00:00Z'
NETERR = 'dial tcp: lookup login.tailscale.com on 127.0.0.53:53: no such host'
FQDN = 'hive.example.ts.net'
FIELDS = ['at', 'bytes', 'cmd', 'exit', 'fqdn', 'lane', 'probe', 'reason', 'verdict']
AT_RE = re.compile(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$')


class Reply:
    '''The shape subprocess.run(..., capture_output=True, text=True) answers with.'''

    def __init__(self, code, out):
        self.returncode = code
        self.stdout = out
        self.stderr = ''


def read_rows(path):
    out = []
    for line in Path(path).read_text(encoding='utf-8').splitlines():
        if line.strip():
            out.append(json.loads(line))
    return out


def main():
    fails = []

    def ck(label, got, want):
        ok = got == want
        print('  [%s] %s -> %s' % ('ok' if ok else 'BAD', label,
                                   got if ok else 'got ' + repr(got)[:160] + ' want ' + repr(want)[:160]))
        if not ok:
            fails.append(label + ': expected ' + repr(want) + ', got ' + repr(got))

    import notify

    probe = getattr(notify, 'cert_probe', None)
    ck('notify.py defines cert_probe', probe is not None, True)
    ck('notify.py names the probes file', str(getattr(notify, 'PROBES', '')),
       str(REPO / 'workspace' / 'notifications' / 'notify-probes.jsonl'))
    if probe is None:
        for x in fails:
            print('FAIL:', x)
        print('test_notify_cert_probe: FAILED')
        return 1

    real_probes = getattr(notify, 'PROBES', None)
    real_run = notify.subprocess.run
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        notify.PROBES = tmp / 'notify-probes.jsonl'
        try:
            # --- cert_probe, with the reply fed in ------------------------------------
            row = notify.cert_probe(FQDN, runner=lambda cmd: (1, BLOCKED))
            ck('the row is returned to its caller', isinstance(row, dict), True)
            assert sorted(row) == FIELDS
            ck('the lane is named at the lane', row['lane'], 'notifications')
            ck('the probe is named', row['probe'], 'tls-cert')
            ck('the reply exit code is recorded verbatim', row['exit'], 1)
            ck('the reply is recorded verbatim', row['reason'], BLOCKED)
            ck('bytes records the length of the reply', row['bytes'], len(BLOCKED))
            ck('the command is recorded so a reader can re-run it',
               all(part in row['cmd'] for part in ('tailscale', 'cert', FQDN)), True)
            ck('at is a UTC timestamp a reader can parse', bool(AT_RE.match(row['at'])), True)

            # --- the three verdicts, from three DISTINCT real replies ------------------
            notify.cert_probe(FQDN, runner=lambda cmd: (0, ISSUED))
            r2 = read_rows(notify.PROBES)[-1]
            ck('exit 0 is verdict cert_issued', r2['verdict'], 'cert_issued')
            ck('and that reply is recorded verbatim', r2['reason'], ISSUED)

            notify.cert_probe(FQDN, runner=lambda cmd: (7, NETERR))
            r3 = read_rows(notify.PROBES)[-1]
            ck('an unclassified failure is verdict error', r3['verdict'], 'error')
            ck('and its reply is recorded, not swallowed', r3['reason'], NETERR)

            def boom(cmd):
                raise FileNotFoundError(2, 'tailscale: command not found')

            r4 = notify.cert_probe(FQDN, runner=boom)
            ck('a runner that raises is recorded, not swallowed', r4['verdict'], 'error')
            ck('the exception is named in the reason', 'tailscale: command not found' in r4['reason'], True)
            ck('a runner that raises records exit -1', r4['exit'], -1)

            # --- never truncate a record ----------------------------------------------
            long_reason = BLOCKED + '\n' + ('filler ' * 500) + 'END-TOKEN-7Q'
            r5 = notify.cert_probe(FQDN, runner=lambda cmd: (1, long_reason))
            ck('a long reply is recorded whole, not cut', r5['reason'], long_reason)
            assert r5['reason'].endswith('END-TOKEN-7Q')

            all_rows = read_rows(notify.PROBES)
            ck('every probe appends a row', len(all_rows), 5)
            ck('appending never rewrites the earlier rows',
               [r['verdict'] for r in all_rows[:3]], ['account_blocked', 'cert_issued', 'error'])
            ck('every row names the probe', sorted({r['probe'] for r in all_rows}), ['tls-cert'])
            assert all(AT_RE.match(r['at']) for r in all_rows)

            # --- the lane owns its own directory ---------------------------------------
            Path(notify.PROBES).unlink()
            notify.PROBES = tmp / 'sub' / 'deep' / 'notify-probes.jsonl'
            notify.cert_probe(FQDN, runner=lambda cmd: (1, BLOCKED))
            ck('the lane directory is created by the writer', notify.PROBES.is_file(), True)
            notify.PROBES = tmp / 'notify-probes.jsonl'

            # --- the default runner: the real command, bounded by a timeout ------------
            calls = []

            def fake_run(cmd, *a, **kw):
                calls.append((list(cmd), kw))
                return Reply(1, BLOCKED)

            notify.subprocess.run = fake_run
            try:
                dr = notify.cert_probe(FQDN)
            finally:
                notify.subprocess.run = real_run
            ck('the default runner runs the cert command with the fqdn',
               [c[0] for c in calls], [['tailscale', 'cert', FQDN]])
            ck('and bounds the call with a timeout',
               bool(calls) and isinstance(calls[0][1].get('timeout'), int) and calls[0][1]['timeout'] > 0, True)
            ck('the reply lands in the reason verbatim', dr['reason'], BLOCKED)
            ck('the default runner records the exit code verbatim', dr['exit'], 1)

            # --- the status path, which is the fixture the RCA asked for ---------------
            argv = list(sys.argv)

            def status():
                out = io.StringIO()
                sys.argv = ['notify.py', '--status']
                try:
                    with contextlib.redirect_stdout(out):
                        notify.main()
                finally:
                    sys.argv = argv
                return out.getvalue()

            # a probe row from five minutes ago: the lane is alive, so nothing is stale
            notify.PROBES.write_text(json.dumps({
                'at': (datetime.now(timezone.utc) - timedelta(minutes=5)).strftime('%Y-%m-%dT%H:%M:%SZ'),
                'lane': 'notifications', 'probe': 'tls-cert', 'fqdn': FQDN,
                'cmd': 'tailscale cert', 'exit': 1, 'bytes': len(BLOCKED),
                'verdict': 'account_blocked', 'reason': BLOCKED}) + '\n', encoding='utf-8')
            calls2 = []

            def ok_run(cmd, *a, **kw):
                calls2.append(list(cmd))
                return Reply(0, ISSUED)

            notify.subprocess.run = ok_run
            try:
                out = status()
            finally:
                notify.subprocess.run = real_run
            ck('the status path runs the cert probe on the machine fqdn', calls2,
               [['tailscale', 'cert', socket.getfqdn()]])
            assert 'tls-cert' in out
            ck('the status path prints the verdict it recorded', 'cert_issued' in out, True)
            ck('a fresh probe does not shout about staleness', 'STALE' in out, False)
            ck('the status path appended a row', len(read_rows(notify.PROBES)), 2)
            assert read_rows(notify.PROBES)[-1]['fqdn'] == socket.getfqdn()

            # stale: the only row predates the window
            notify.PROBES.write_text(json.dumps({
                'at': '2026-01-01T00:00:00Z', 'lane': 'notifications', 'probe': 'tls-cert',
                'fqdn': FQDN, 'cmd': 'tailscale cert', 'exit': 1, 'bytes': len(BLOCKED),
                'verdict': 'account_blocked', 'reason': BLOCKED}) + '\n', encoding='utf-8')
            notify.subprocess.run = ok_run
            try:
                out = status()
            finally:
                notify.subprocess.run = real_run
            ck('a stale lane shouts at the status reader', 'STALE' in out, True)
            assert 'tls-cert' in out
            ck('and the new row is appended as well', len(read_rows(notify.PROBES)), 2)

            # never probed: the file is absent
            Path(notify.PROBES).unlink()
            notify.subprocess.run = ok_run
            try:
                out = status()
            finally:
                notify.subprocess.run = real_run
            ck('a lane that has never been probed says so',
               ('STALE' in out) or ('never' in out.lower()), True)
            ck('and the first probe is recorded too', len(read_rows(notify.PROBES)), 1)
        finally:
            notify.PROBES = real_probes
            notify.subprocess.run = real_run

    # --- the decay computation, pinned with a fixed clock, not wall time -------------
    age = getattr(notify, 'probe_age_days', None)
    ck('notify.py defines probe_age_days', age is not None, True)
    if age is not None:
        now = datetime(2026, 10, 9, 0, 0, 0, tzinfo=timezone.utc)
        ck('a row nine days old reads as nine days old',
           round(age({'at': '2026-09-30T00:00:00Z'}, now=now), 3), 9.0)
        ck('a row with no date reads as stale, not fresh', age({'probe': 'tls-cert'}, now=now) > 1000, True)
        ck('a row with an unreadable date reads as stale, not fresh',
           age({'at': 'last tuesday'}, now=now) > 1000, True)
        assert round(age({'at': '2026-09-30T00:00:00Z'}, now=now), 3) == 9.0
    n = getattr(notify, 'CERT_PROBE_STALE_DAYS', None)
    ck('notify.py declares the staleness window', isinstance(n, (int, float)) and 0 < n <= 30, True)

    for x in fails:
        print('FAIL:', x)
    print('test_notify_cert_probe:', 'FAILED' if fails else 'ok')
    return 1 if fails else 0


if __name__ == '__main__':
    sys.exit(main())
