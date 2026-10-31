'''t718: scope_lint's oracle-weak check is escaped by a raise in a helper.

Fail-first oracle for the fix in scripts/scope_lint.py, function _shape_only. It is RED on
main for exactly one reason, the missing change: _shape_only() returns the verdict "not
weak" the moment it sees any ast.Raise node anywhere in the tree (scope_lint.py:96-97), so
an oracle whose every assert is a shape/presence form clears the check the instant it
carries a raise inside a helper that pins nothing. The t658 scope (model swift) was flagged
by this check at 2026-10-07T11:32:49Z (workspace/tasks/t718--scope-rca-oracle-weak.md:28);
the answer that replaced it clears the check with a raise inside a JSON-decode helper and no
behavioural assertion in any test body (workspace/run-logs/manager-queue/out/t658.md:53-62).

Each test asserts the value _shape_only RETURNS for a source that differs from its neighbour
only in WHERE the failure-raising statement sits: a value consumed here, not a shape. The
last test asserts the same through scope_lint.static(), so the fix is wired into the check
and not only into the helper.

Read-only: no file writes, no sockets, no subprocess, no model call, no temp dirs, no live
state. Run as `python3 scripts/tests/test_scope_lint_shape_escape.py` (pytest is not used).
'''
import ast
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / 'scripts'))

import scope_lint


# Every assert is a shape/presence form; the only failure-raising statement sits in a
# module-level helper, whose body the suite's runner never reaches. This is the escape:
# main says "not weak"; the fix must say "weak".
RAISE_IN_A_HELPER = '''
import json


def load(text):
    try:
        return json.loads(text)
    except ValueError as exc:
        raise AssertionError('not json: ' + str(exc))


def test_shape():
    assert isinstance(load('{}'), dict)
    assert 'examined' in {'examined': 1}
    assert len('abc') == 3


if __name__ == '__main__':
    test_shape()
'''

# A raise inside a test body is a real failure path: it must stay NOT weak.
RAISE_IN_A_TEST_BODY = '''
def ok():
    return True


def test_value():
    if not ok():
        raise AssertionError('the value was never used')
    assert isinstance({}, dict)


if __name__ == '__main__':
    test_value()
'''

# A module-level raise runs when the suite is run: a real failure path.
MODULE_LEVEL_RAISE = '''
def test_shape():
    assert isinstance([], list)


raise SystemExit('nothing to check')


if __name__ == '__main__':
    test_shape()
'''

# Real value assertions in the test body: never weak, before or after the fix.
VALUE_ASSERTIONS = '''
import json


def load(text):
    try:
        return json.loads(text)
    except ValueError as exc:
        raise AssertionError('not json: ' + str(exc))


def test_values_are_consumed():
    data = load('{"examined": 0}')
    assert data['examined'] == 0, data
    missing = [k for k in ('scope', 'brief') if k not in data]
    assert not missing, missing


if __name__ == '__main__':
    test_values_are_consumed()
'''

# No pin at all: the existing weak shape, which the check already catches.
NO_PIN_SHAPE_ONLY = '''
def test_shape():
    assert isinstance({}, dict)
    assert 'examined' in {'examined': 1}
    assert len([1, 2]) == 2


if __name__ == '__main__':
    test_shape()
'''

# A bare assert_called() proves a call happened, not what it did: weak today.
BARE_ASSERT_CALLED = '''
from unittest.mock import Mock


def test_a_call():
    m = Mock()
    m(1)
    m.assert_called()


if __name__ == '__main__':
    test_a_call()
'''

UNPARSEABLE = 'def test_x(:\n    pass\n'

FIXTURES = ('RAISE_IN_A_HELPER', 'RAISE_IN_A_TEST_BODY', 'MODULE_LEVEL_RAISE',
            'VALUE_ASSERTIONS', 'NO_PIN_SHAPE_ONLY', 'BARE_ASSERT_CALLED')


def test_the_fixture_sources_parse_except_the_one_that_must_not():
    for name in FIXTURES:
        ast.parse(globals()[name])
    try:
        ast.parse(UNPARSEABLE)
    except SyntaxError:
        return
    raise AssertionError('UNPARSEABLE parses: the SyntaxError guard below is vacuous')


def test_shape_only_stays_weak_with_no_pin():
    assert scope_lint._shape_only(NO_PIN_SHAPE_ONLY) is True, (
        'an oracle with only shape/presence asserts and no pin must stay weak; got '
        + repr(scope_lint._shape_only(NO_PIN_SHAPE_ONLY)))


def test_bare_assert_called_is_still_weak():
    assert scope_lint._shape_only(BARE_ASSERT_CALLED) is True, (
        'a bare assert_called() must not count as a pinned behaviour')


def test_module_level_raise_is_a_pin():
    assert scope_lint._shape_only(MODULE_LEVEL_RAISE) is False, (
        'a module-level raise runs when the suite runs; it is a real failure path')


def test_raise_inside_a_test_body_is_a_pin():
    assert scope_lint._shape_only(RAISE_IN_A_TEST_BODY) is False, (
        'a raise inside a test function is a real failure path')


def test_value_assertions_are_never_weak():
    assert scope_lint._shape_only(VALUE_ASSERTIONS) is False, (
        'a source with real value assertions must never be flagged weak')


def test_unparseable_source_is_not_weak():
    assert scope_lint._shape_only(UNPARSEABLE) is False, (
        'a source that does not parse is oracle-crash, not oracle-weak')


def test_raise_in_a_helper_is_not_a_behaviour_pin():
    assert scope_lint._shape_only(RAISE_IN_A_HELPER) is True, (
        'THE FIX: a raise in a module-level helper is a token, not a pinned behaviour; '
        'every assert in this source is isinstance(...), a string-constant `in`, or '
        'len(x) == n -- so the oracle pins no behaviour and the check must say so. Today '
        '_shape_only returns False on the first ast.Raise node (scope_lint.py:96-97).')


def test_lint_flags_the_escape_as_a_finding():
    chunks = [{'kind': 'oracle',
               'file': 'scripts/tests/test_failure_schema_examined.py',
               'oracle_text': RAISE_IN_A_HELPER}]
    findings = scope_lint.static(chunks, scope_lint.REPO)
    weak = [f for f in findings if f[0] == 'oracle-weak']
    assert len(weak) == 1, (
        'static() must report exactly one oracle-weak finding for the escape source; got '
        + repr(findings))
    assert weak[0][1] == 0, 'the finding must point at chunk 0, got ' + repr(weak[0][1])
    assert weak[0][2] == 'scripts/tests/test_failure_schema_examined.py', (
        'the finding must name the oracle file, got ' + repr(weak[0][2]))


def main():
    tests = [(name, fn) for name, fn in sorted(globals().items())
             if name.startswith('test_') and callable(fn)]
    assert tests, 'no test functions defined -- a suite with no tests proves nothing'
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print('PASS ' + name)
        except AssertionError as exc:
            failed += 1
            print('FAIL ' + name + ': ' + str(exc))
        except Exception as exc:
            failed += 1
            print('ERROR ' + name + ': ' + type(exc).__name__ + ': ' + str(exc))
    print(str(len(tests) - failed) + '/' + str(len(tests)) + ' passed')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
