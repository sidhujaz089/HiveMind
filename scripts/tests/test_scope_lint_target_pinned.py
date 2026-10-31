#!/usr/bin/env python3
"""t977: a brief must not hand a builder a target suite that pins the text it tells it to
rewrite.

The defect (t941, model swift, 7 attempts at ~1400s each, nine suites red every time): the brief
anchored the builder to the finding message in scripts/scope_lint.py, and its target suite
scripts/tests/test_scope_lint_window.py asserts that same message -- at lines 114-115 it demands
the finding start with the size sentence and contain 'and the brief names no SYMBOLS', in a test
that PASSES on main, in the same suite as the test that demands the finding grow. A builder told
to rewrite the message satisfies the red test and breaks the green one. Verified 2026-10-09 by
running that suite on main: 4/5 passed, exit 1, failing
test_finding_names_real_definitions_from_the_file.

The check pinned here, scope_lint.target_pinned(chunks, repo), compares the string literals a
target suite asserts on with the string literals in the implementation file ON THE LINES THE
BRIEF CITES, and flags the pair. Static: no model, no subprocess, no execution, so it works
under --no-run.
"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
import scope_lint

CHECK = getattr(scope_lint, 'target_pinned', None)
MISSING = 'scope_lint.target_pinned does not exist: the change this oracle exists to pin'
CLASS = 'brief-target-pinned'

# The strings the real pair uses, taken from the artefacts so the fixture cannot drift into a
# shape the check was written for. PINNED is a fragment of the f-string in scope_lint.py that
# builds the brief-window finding; a slice of a message is exactly what a suite pins.
PINNED = 'and the brief names no SYMBOLS'
UNRELATED = 'names 7 separate edit sites in the builder output'
SHORT = 'bytes (> '
PIN_TEST = 'test_finding_keeps_the_size_sentence_it_records_today'
SUITE = 'scripts/tests/test_scope_lint_window.py'
IMPL = 'scripts/scope_lint.py'

# Line numbers inside the fixture module below; the prose of a brief cites them the way a real
# brief cites a real file.
PIN_LINE = 5
SPARE_LINE = 8
SHORT_LINE = 11

IMPL_LINES = [
    '"""a module whose finding message is assembled from fragments, as scope_lint.py does."""',
    '',
    '',
    'def build(name, size):',
    '    return "%s is %d bytes (> 40000) " % (name, size) + ' + repr(PINNED),
    '',
    '',
    'SPARE = ' + repr(UNRELATED),
    '',
    '',
    'TOO_SHORT = ' + repr(SHORT),
]


def _suite(pins):
    """A target suite whose test functions assert on the given literals (None: no string)."""
    lines = ['"""fixture suite: quotes the implementation file, as a real suite does."""', '', '']
    for name, lit in pins:
        lines.append('def %s():' % name)
        lines.append('    ev = 0' if lit is None else "    ev = ''")
        lines.append('    assert len(ev) == 1' if lit is None else '    assert %r == ev' % (lit,))
        lines.append('')
        lines.append('')
    return '\n'.join(lines) + '\n'


def _repo(root, pins):
    """A throwaway repo: the module whose message is assembled from fragments, and the suite
    that quotes it. Everything is written inside a tempfile dir; nothing touches the tree."""
    (root / 'src').mkdir(parents=True)
    (root / 'tests').mkdir(parents=True)
    (root / 'src' / 'mod.py').write_text('\n'.join(IMPL_LINES) + '\n', encoding='utf-8')
    (root / 'tests' / 'test_mod.py').write_text(_suite(pins), encoding='utf-8')
    return 'src/mod.py', 'tests/test_mod.py'


def _chunk(impl, target, cite):
    """An implementation chunk whose prose cites one line of the module, as a real brief does."""
    return {'kind': 'implementation', 'file': impl, 'target': target,
            'single_change': 'rewrite the finding message at %s' % cite,
            'what_is_wrong': 'the message names nothing the builder can copy',
            'done_when': 'python3 tests/test_mod.py exits 0',
            'related_entries': ['implementation']}


def test_finding_names_the_suite_the_test_and_the_pinned_text():
    """A suite that quotes a string on a cited line must be named, with the text and the field
    that carries the preservation -- not a count of 'possible conflicts'."""
    assert CHECK is not None, MISSING
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        impl, target = _repo(root, [(PIN_TEST, PINNED), ('test_other', UNRELATED), ('test_short', SHORT)])
        hits = CHECK([_chunk(impl, target, 'mod.py:%d' % PIN_LINE)], root)
        assert len(hits) == 1, 'expected exactly one finding, got %d: %r' % (len(hits), hits)
        cls, idx, f, ev = hits[0]
        assert cls == CLASS, 'the class must be %r, got %r' % (CLASS, cls)
        assert idx == 0, 'the second field is the chunk index, got %r' % (idx,)
        assert f == target, 'the third field is the target suite the brief names, got %r' % (f,)
        assert PIN_TEST in ev, 'the finding must name the test function that pins the text: %r' % (ev,)
        assert PINNED in ev, 'the finding must quote the pinned literal itself: %r' % (ev,)
        assert 'behaviour_oracle' in ev, 'the finding must name the contract field that carries the preservation: %r' % (ev,)
        assert UNRELATED not in ev, 'the finding quoted a line the brief does not cite: %r' % (ev,)
        assert SHORT not in ev, 'a shared string under the 25-character floor is prose, not a pin: %r' % (ev,)


def test_a_cited_line_the_suite_never_quotes_is_silent():
    """The vacuity guard: a suite that asserts no strings has nothing to pin. A check that
    flagged every implementation chunk would satisfy the positive case and be useless."""
    assert CHECK is not None, MISSING
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        impl, target = _repo(root, [('test_count', None)])
        hits = CHECK([_chunk(impl, target, 'mod.py:%d' % PIN_LINE)], root)
        assert hits == [], 'a suite that asserts no strings has nothing to pin: %r' % (hits,)


def test_a_shared_string_under_the_floor_is_silent():
    """The floor is load-bearing: 'bytes (> ' is shared by the fixture's line 11 and the suite,
    and it is 10 characters, so it must NOT be a finding."""
    assert CHECK is not None, MISSING
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        impl, target = _repo(root, [('test_short', SHORT)])
        hits = CHECK([_chunk(impl, target, 'mod.py:%d' % SHORT_LINE)], root)
        assert hits == [], 'text shorter than 25 characters is ordinary prose: %r' % (hits,)


def test_the_finding_is_scoped_to_the_lines_the_brief_cites():
    """Locality: the suite's other literal is on the module's line 8, and this chunk cites only
    line 8 -- so the finding must quote THAT text, and must not report line 5's."""
    assert CHECK is not None, MISSING
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        impl, target = _repo(root, [('test_other', UNRELATED)])
        hits = CHECK([_chunk(impl, target, 'mod.py:%d' % SPARE_LINE)], root)
        assert len(hits) == 1, 'line 8 shares a string the suite asserts on: %r' % (hits,)
        assert UNRELATED in hits[0][3], 'the finding must quote the text on the cited line: %r' % (hits,)
        assert PINNED not in hits[0][3], 'the finding quoted a line the brief does not cite: %r' % (hits,)


def test_the_real_pair_t941_shipped_is_caught_on_the_repo_as_it_stands():
    """The fixture that makes a keyword-matching stub fail: t941's real brief against t941's real
    target suite, read from the repository, with no synthetic phrasing left to match."""
    assert CHECK is not None, MISSING
    chunk = {'kind': 'implementation', 'file': IMPL, 'target': SUITE,
             'single_change': 'make the brief-window finding name definitions the builder can copy',
             'what_is_wrong': 'the finding names no SYMBOLS, so a builder handed a 40 KB file has '
                              'nothing to copy; the brief cites lines 264-266 of the file',
             'done_when': 'python3 scripts/tests/test_scope_lint_window.py exits 0',
             'related_entries': ['implementation']}
    hits = CHECK([chunk], ROOT)
    assert len(hits) == 1, 'the t941 pair is the case this check exists for: %r' % (hits,)
    cls, idx, f, ev = hits[0]
    assert cls == CLASS, 'the class must be %r, got %r' % (CLASS, cls)
    assert f == SUITE, 'the finding must name the suite that pins the text, got %r' % (f,)
    assert PINNED in ev, 'the finding must quote the literal the file assembles: %r' % (ev,)


def test_the_check_is_registered_so_its_passed_state_is_recorded():
    """Decay: record() writes one scope_quality event per name in CHECK_NAMES, so a check absent
    from it fires findings and never records that it ran."""
    assert CLASS in scope_lint.CHECK_NAMES, (
        'record() emits one event per name in CHECK_NAMES, so a check not listed there is never '
        'recorded as having run: %r' % (scope_lint.CHECK_NAMES,))


ANSWER = (
    "The scope.\n\n"
    "```chunks\n"
    "[{\"kind\": \"implementation\", \"file\": \"scripts/scope_lint.py\", "
    "\"target\": \"scripts/tests/test_scope_lint_window.py\", "
    "\"single_change\": \"add target_pinned and call it from lint\", "
    "\"what_is_wrong\": \"the brief tells the builder to rewrite a message the target suite pins\", "
    "\"done_when\": \"python3 scripts/tests/test_scope_lint_window.py exits 0\", "
    "\"related_entries\": [\"implementation\"]}]\n"
    "```\n"
)


def test_lint_reports_the_finding_so_a_scope_cannot_ship_the_pair():
    """The value is used: a check that exists, is registered, and is not called by lint() is a
    decoration. This feeds t941-shaped text through the real entry point."""
    assert CHECK is not None, MISSING
    _chunks, findings = scope_lint.lint(ANSWER, ROOT, execute=False)
    hits = [f for f in findings if f[0] == CLASS]
    assert len(hits) == 1, 'lint() must surface the finding for a t941-shaped answer: %r' % (findings,)
    assert SUITE in hits[0][3] and PINNED in hits[0][3], (
        'the finding must name the target suite and the pinned text: %r' % (hits,))


def main():
    tests = sorted((n, f) for n, f in globals().items() if n.startswith('test_') and callable(f))
    failed = []
    for name, fn in tests:
        try:
            fn()
            print('ok   ' + name)
        except AssertionError as e:
            failed.append(name)
            print('FAIL %s: %s' % (name, e))
        except Exception as e:
            failed.append(name)
            print('FAIL %s: %s: %s' % (name, type(e).__name__, e))
    print('%d/%d passed' % (len(tests) - len(failed), len(tests)))
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
