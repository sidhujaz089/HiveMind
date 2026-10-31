#!/usr/bin/env python3
'''t1391 -- refuse temp-dir creation outside a namespaced path in tracked code.

The defect this suite exists for (workspace/rca/a7f0b05fd87c.md, C6/C8): a tracked script calls
tempfile.mkdtemp(prefix='tmp') at import time and never tears it down, so /tmp regrows at
~9,600 inodes/day. scripts/sweep.py mops a list of NAMED prefixes, so a name nobody predicted
is invisible -- the blacklist-needs-a-librarian shape. This gate inverts it: the APPROVED
creation paths are enumerated (a prefix= carrying one of NAMESPACES, or a dir= under a
namespaced root) and every other temp creation in tracked code is a finding.

RED ON MAIN: scripts/check_tmp_hygiene.py does not exist on main, so the import below fails.
Run: python3 scripts/tests/test_tmp_hygiene.py
'''
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

NL = chr(10)
REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / 'scripts'))

import check_tmp_hygiene  # noqa: E402  the module this task adds

SCRIPT = REPO / 'scripts' / 'check_tmp_hygiene.py'
Q = chr(39)
BAD_PREFIX = ('import tempfile' + NL + 'd = tempfile.mkdtemp(prefix=' + Q + 'tmp' + Q + ')' + NL)
CLEAN = 'import tempfile' + NL + 'print(tempfile.gettempdir())' + NL


def _mk(call, arg):
    '''One line of source creating a temp entry, on line 2, after an import line.'''
    return 'import tempfile' + NL + 'x = tempfile.' + call + '(' + arg + ')' + NL


def test_namespaces_are_declared():
    ns = check_tmp_hygiene.NAMESPACES
    assert isinstance(ns, (tuple, list)) and len(ns) >= 1, \
        'NAMESPACES must enumerate the approved namespaced roots, got ' + repr(ns)
    for n in ns:
        assert isinstance(n, str) and n.strip() and '/' not in n, \
            'NAMESPACES entries are single path components, got ' + repr(n)


def test_unnamespaced_prefix_is_flagged_with_a_citable_record():
    f = check_tmp_hygiene.check_source(BAD_PREFIX)
    assert len(f) == 1, 'prefix=' + Q + 'tmp' + Q + ' at the bare temp root is the defect; got ' + repr(f)
    got = f[0]
    assert got['line'] == 2, 'a finding must name the line it came from: ' + repr(got)
    assert 'mkdtemp' in got['code'], 'a finding must quote the offending call: ' + repr(got)
    assert str(got['why']).strip(), 'a finding must say what is wrong: ' + repr(got)


def test_default_prefix_is_flagged():
    f = check_tmp_hygiene.check_source('import tempfile' + NL + 'x = tempfile.mkdtemp()' + NL)
    assert len(f) == 1 and f[0]['line'] == 2, \
        'mkdtemp() with no prefix lands at the bare temp root and must be flagged: ' + repr(f)


def test_all_four_creation_call_forms_are_flagged():
    src = ('import tempfile' + NL
           + 'a = tempfile.mkdtemp(prefix=' + Q + 'tmp' + Q + ')' + NL
           + 'b = tempfile.mkstemp(prefix=' + Q + 'bridge' + Q + ')' + NL
           + 'c = tempfile.TemporaryDirectory(prefix=' + Q + 'skill' + Q + ')' + NL
           + 'd = tempfile.NamedTemporaryFile(prefix=' + Q + 'trigger' + Q + ')' + NL)
    lines = sorted(x['line'] for x in check_tmp_hygiene.check_source(src))
    assert lines == [2, 3, 4, 5], \
        'all four creation call forms are checked, one finding per line: ' + repr(lines)


def test_namespaced_root_is_approved():
    ns = check_tmp_hygiene.NAMESPACES[0]
    under = repr(os.path.join(tempfile.gettempdir(), ns) + os.sep)
    assert check_tmp_hygiene.check_source(_mk('mkdtemp', 'dir=' + under)) == [], \
        'a dir= under a declared namespaced root is the approved path and must pass'
    assert check_tmp_hygiene.check_source(_mk('mkdtemp', 'prefix=' + repr(ns))) == [], \
        'a prefix that carries a declared namespace is the approved path and must pass'


def test_a_file_that_creates_nothing_is_clean():
    assert check_tmp_hygiene.check_source(CLEAN) == [], \
        'mentioning tempfile is not creating a temp dir; a gate that flags it is --no-verify-ed'
    assert check_tmp_hygiene.check_source('import os' + NL + 'print(os.getcwd())' + NL) == []


def test_the_gate_is_clean_on_its_own_source():
    f = check_tmp_hygiene.check_file(SCRIPT)
    assert f == [], 'the gate must not flag its own file: ' + repr(f)


def test_cli_refuses_a_creator_and_passes_a_clean_file():
    d = tempfile.mkdtemp()
    try:
        bad = Path(d) / 'bad.py'
        bad.write_text(BAD_PREFIX, encoding='utf-8')
        r = subprocess.run([sys.executable, str(SCRIPT), str(bad)],
                           capture_output=True, text=True, timeout=60)
        assert r.returncode == 1, \
            'a temp-dir creator must exit the gate non-zero: ' + repr((r.returncode, r.stdout, r.stderr))
        assert str(bad) in r.stdout, 'the gate must name the file it refused: ' + repr(r.stdout)
        assert 'mkdtemp' in r.stdout, 'the record must show the offending call: ' + repr(r.stdout)
        good = Path(d) / 'good.py'
        good.write_text(CLEAN, encoding='utf-8')
        r2 = subprocess.run([sys.executable, str(SCRIPT), str(good)],
                            capture_output=True, text=True, timeout=60)
        assert r2.returncode == 0, \
            'a file that creates no temp dir must pass: ' + repr((r2.returncode, r2.stdout, r2.stderr))
    finally:
        shutil.rmtree(d, ignore_errors=True)


def main():
    tests = sorted((n, fn) for n, fn in list(globals().items())
                   if n.startswith('test_') and callable(fn))
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print('ok   ' + name)
        except Exception as exc:
            failed += 1
            print('FAIL ' + name + ': ' + repr(exc))
    print(str(len(tests) - failed) + ' passed, ' + str(failed) + ' failed')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
