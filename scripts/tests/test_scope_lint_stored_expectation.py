# t755: the scope lint must refuse a scope whose oracle pins a count the
# fixture it writes itself cannot produce (class oracle-red-reason, shape
# 'fixture drift').
#
# The fixture shapes are copied from the real defect:
#   workspace/scope-defects/done/8cdc9aaf986f.json
#   (t719, swift, scope 73d2ca5d766f): the oracle's last test asserts 6
#   PROMISED/FOR-JAS rows; the fixture it writes yields 4, so the suite is
#   red for its own arithmetic and no legal implementation edit can green it.
#
# Run: python3 scripts/tests/test_scope_lint_stored_expectation.py
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LINT = ROOT / 'scripts' / 'scope_lint.py'
PHRASE = 'stored expectation'
CLASS_TOKEN = 'oracle-red-reason'

# Same 4-row fixture in both; the bad scope stores the expected count as 6,
# the good one computes it from the fixture it writes.
BAD_ORACLE = '\n'.join([
    'import tempfile',
    'from pathlib import Path',
    "ROWS = ['FOR-JAS: a', 'FOR-JAS: b', 'PROMISED: c', 'FOR-JAS: d']",
    'def test_the_filter_cuts_the_noise():',
    '    with tempfile.TemporaryDirectory() as d:',
    "        Path(d).joinpath('s01.json').write_text('')",
    "        count = sum(1 for r in ROWS if r.startswith('FOR-JAS'))",
    "        assert count == 6, 'fixture drift'",
])
GOOD_ORACLE = '\n'.join([
    'import tempfile',
    'from pathlib import Path',
    "ROWS = ['FOR-JAS: a', 'FOR-JAS: b', 'PROMISED: c', 'FOR-JAS: d']",
    'def test_the_filter_cuts_the_noise():',
    '    with tempfile.TemporaryDirectory() as d:',
    "        Path(d).joinpath('s01.json').write_text('')",
    "        count = sum(1 for r in ROWS if r.startswith('FOR-JAS'))",
    "        assert count == len(ROWS) - 1, 'fixture drift'",
])


def scope_text(oracle_src):
    chunks = [
        {'kind': 'oracle',
         'file': 'scripts/tests/test_the_filter_cuts_the_noise.py',
         'oracle_text': oracle_src,
         'red_because': 'scripts/session_review.py has no resolved-in-session filter'},
        {'kind': 'implementation',
         'file': 'scripts/session_review.py',
         'target': 'scripts/tests/test_the_filter_cuts_the_noise.py',
         'single_change': 'add the resolved-in-session filter to scripts/session_review.py',
         'what_is_wrong': 'promises answered in the next turns are flagged MISSED',
         'done_when': 'python3 scripts/tests/test_the_filter_cuts_the_noise.py exits 0',
         'related_entries': ['oracle-red-reason']},
    ]
    return '## Chunks\n\n```chunks\n' + json.dumps(chunks, indent=1) + '\n```\n'


def run_lint(directory, name, body):
    path = Path(directory) / name
    path.write_text(body, encoding='utf-8')
    return subprocess.run([sys.executable, str(LINT), str(path), '--task', 't999'],
                          capture_output=True, text=True, timeout=120, cwd=str(ROOT))


def test_flags_a_stored_expectation():
    assert LINT.is_file(), 'scripts/scope_lint.py missing'
    with tempfile.TemporaryDirectory() as d:
        r = run_lint(d, 'bad-scope.md', scope_text(BAD_ORACLE))
        lines = [ln for ln in r.stdout.splitlines() if PHRASE in ln]
        assert len(lines) == 1, 'expected 1 finding, got %d: %r' % (len(lines), r.stdout)
        assert CLASS_TOKEN in lines[0], 'finding must carry the class: ' + lines[0]
        assert 'test_the_filter_cuts_the_noise.py' in lines[0], 'finding must name the chunk: ' + lines[0]
        assert r.returncode != 0, 'a scope carrying it must be refused: rc=%s out=%r' % (r.returncode, r.stdout)


def test_lets_a_computed_expectation_through():
    assert LINT.is_file(), 'scripts/scope_lint.py missing'
    with tempfile.TemporaryDirectory() as d:
        r = run_lint(d, 'good-scope.md', scope_text(GOOD_ORACLE))
        assert 'Traceback' not in r.stderr, 'lint crashed: ' + r.stderr
        lines = [ln for ln in r.stdout.splitlines() if PHRASE in ln]
        assert lines == [], 'computed expectation flagged: %r' % (lines,)


if __name__ == '__main__':
    tests = [v for k, v in sorted(globals().items()) if k.startswith('test_')]
    failed = 0
    for t in tests:
        try:
            t()
            print('PASS', t.__name__)
        except AssertionError as e:
            failed += 1
            print('FAIL %s: %s' % (t.__name__, e))
    print('%d/%d passed' % (len(tests) - failed, len(tests)))
    sys.exit(1 if failed else 0)
