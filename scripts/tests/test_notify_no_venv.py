#!/usr/bin/env python3
'''t1455: scripts/tests/test_notify.py must exit 0 when py_vapid cannot be imported.

Runs TWO copies of the suite (scripts/notify.py + scripts/tests/test_notify.py,
copied out of the real repo into temp trees) under a fresh interpreter whose
PYTHONPATH forces the py_vapid import one way or the other, so the result does
not depend on what the machine happens to have installed:

  TREE A  a py_vapid.py that raises ImportError on import. This is the coder
          sandbox: a repo copy with no .venv, where the suite's own re-exec
          guard (test_notify.py:23-30) no-ops because notify.VENV does not
          exist, and _real_sender's 'from py_vapid import Vapid' (notify.py:191)
          dies at its call site, test_notify.py:146.
  TREE B  working stubs of py_vapid and pywebpush. The module IS importable, so
          the suite must take the real signing path and print 'signing ok' --
          it must NOT print the skip. This is what stops the fix becoming an
          unconditional print.

RED ON MAIN for the stated reason: the construction at test_notify.py:146 sits
OUTSIDE the suite's try at :148, so tree A's child raises ModuleNotFoundError,
exits 1, prints a traceback, and prints neither 'signing skipped' nor its own
'test_notify: ok' line. Reproduced on this box 2026-10-18 with a copy of the two
files in a temp dir with no .venv, run by system python3: exit 1, that traceback,
last ok line printed 'a 500 neither sends nor drops'.

No live state: the child writes nothing in the repo (test_notify.py:49-53
redirects PREFS/SUBS/VAPID into a temp dir itself and restores them). No model
call, no network, no process left running: NOTIFY_TEST_REEXEC=1 is set in the
child's env so the re-exec guard cannot fire with or without a .venv, and every
subprocess.run carries timeout=.
'''
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SUITE = 'scripts/tests/test_notify.py'
SKIP = 'signing skipped: py_vapid absent'
TIMEOUT = 180

NO_VAPID = 'raise ImportError("no module named py_vapid")'

STUB_VAPID = '''class Vapid:
    @staticmethod
    def from_pem(key):
        return Vapid()
'''

STUB_WEBPUSH = '''class WebPushException(Exception):
    pass


class _Response:
    status_code = 201


def webpush(**kw):
    return _Response()
'''


def _tree(root, name, mods):
    d = Path(root) / name
    (d / 'scripts' / 'tests').mkdir(parents=True)
    shutil.copy(REPO / 'scripts' / 'notify.py', d / 'scripts' / 'notify.py')
    shutil.copy(REPO / SUITE, d / 'scripts' / 'tests' / 'test_notify.py')
    site = d / 'site'
    site.mkdir()
    for fname, body in mods:
        (site / fname).write_text(body, encoding='utf-8')
    return d, site


def _dec(v):
    return v.decode('utf-8', 'replace') if isinstance(v, bytes) else (v or '')


def _run(tree, site):
    env = os.environ.copy()
    prev = env.get('PYTHONPATH')
    env['PYTHONPATH'] = str(site) + ((os.pathsep + prev) if prev else '')
    env['NOTIFY_TEST_REEXEC'] = '1'
    cmd = [sys.executable, str(tree / 'scripts' / 'tests' / 'test_notify.py')]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT, env=env)
    except subprocess.TimeoutExpired as e:
        return 124, 'TIMEOUT after %s s: %s\n%s%s' % (TIMEOUT, e, _dec(e.stdout), _dec(e.stderr))
    return p.returncode, _dec(p.stdout) + '\n' + _dec(p.stderr)


def main():
    fails = []

    def ck(label, cond, detail=''):
        print('  [%s]    %s%s' % ('ok' if cond else 'BAD', label,
                                  (' -> ' + detail) if detail else ''))
        if not cond:
            fails.append(label)

    for rel in ('scripts/notify.py', SUITE):
        if not (REPO / rel).is_file():
            print('FAIL: ' + rel + ' is not on disk at ' + str(REPO / rel))
            fails.append(rel + ' exists')

    if fails:
        for x in fails:
            print('FAIL:', x)
        print('test_notify_no_venv: FAILED')
        return 1

    with tempfile.TemporaryDirectory() as d:
        a, site_a = _tree(d, 'no-venv', [('py_vapid.py', NO_VAPID)])
        b, site_b = _tree(d, 'stubbed', [('py_vapid.py', STUB_VAPID),
                                         ('pywebpush.py', STUB_WEBPUSH)])
        rc_a, out_a = _run(a, site_a)
        rc_b, out_b = _run(b, site_b)
        print('--- tree A (py_vapid unimportable): child output, whole ---')
        print(out_a)
        print('--- tree B (py_vapid + pywebpush stubbed): child output, whole ---')
        print(out_b)
        ck('no-venv: test_notify.py exits 0', rc_a == 0, 'exit code ' + str(rc_a))
        ck('no-venv: it prints the named skipped state', SKIP in out_a)
        ck('no-venv: it does not claim it signed', 'signing ok' not in out_a)
        ck('no-venv: no traceback escapes the suite', 'Traceback' not in out_a)
        ck('no-venv: it ran to its own green line', 'test_notify: ok' in out_a)
        ck('no-venv: no check inside the suite failed', 'FAIL:' not in out_a)
        ck('stubbed: test_notify.py exits 0', rc_b == 0, 'exit code ' + str(rc_b))
        ck('stubbed: with py_vapid importable it signs for real', 'signing ok' in out_b)
        ck('stubbed: it does not print the skip', SKIP not in out_b)
        ck('stubbed: it ran to its own green line', 'test_notify: ok' in out_b)

    for x in fails:
        print('FAIL:', x)
    print('test_notify_no_venv:', 'FAILED' if fails else 'ok')
    return 1 if fails else 0


if __name__ == '__main__':
    sys.exit(main())
