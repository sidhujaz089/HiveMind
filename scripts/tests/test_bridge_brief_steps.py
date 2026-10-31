#!/usr/bin/env python3
"""The builder's brief carries the scope's steps, in order (Jas, 2026-10-21, plan
docs/dsh/PLAN-2026-10-21.md, A1), and bridge.validate refuses a malformed `steps` field.

A step is {where, now, do, expect, if_not}: where to edit, the file's text there now, the change,
what is true after it, and what to do when it is not. The scoper writes them (scope_lint checks
that `now` is the real text); this is the half that hands them to the builder. A brief without them
is the prose-only brief that left the builder to find its own anchors.

Contract:
  - bridge.brief_text renders a STEPS section with every step numbered in order, each showing
    WHERE, NOW (the quoted lines), DO, EXPECT and IF NOT;
  - a chunk with no steps renders no STEPS section (every older brief is unchanged);
  - bridge.validate refuses steps that are not a non-empty list, more than six, or a step missing
    any of the five fields; well-formed steps add no error.

RED ON MAIN for the stated reason only: brief_text ignores `steps` and validate never reads it.
"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))

import bridge  # noqa: E402

FIELDS = ('where', 'now', 'do', 'expect', 'if_not')


def _repo():
    d = Path(tempfile.mkdtemp(prefix='bridge-wt-steps-'))
    (d / 'mod.py').write_text('def alpha(a):\n    b = a + 1\n    return b\n', encoding='utf-8')
    (d / 'scripts' / 'tests').mkdir(parents=True)
    (d / 'scripts' / 'tests' / 'test_mod.py').write_text('print(1)\n', encoding='utf-8')
    return d


def _steps():
    return [{'where': 'alpha', 'now': '    b = a + 1', 'do': 'add 2 instead of 1',
             'expect': 'alpha(1) == 3', 'if_not': 'stop and report the line you found'},
            {'where': 'L3-L3', 'now': '    return b', 'do': 'return b times ten',
             'expect': 'python3 scripts/tests/test_mod.py exits 0', 'if_not': 'report the failing assertion'}]


def _impl(**over):
    c = {'kind': 'implementation', 'file': 'mod.py', 'target': 'scripts/tests/test_mod.py',
         'single_change': 'change alpha', 'what_is_wrong': 'alpha is wrong',
         'done_when': 'python3 scripts/tests/test_mod.py exits 0', 'related_entries': ['t0'],
         'symbols': ['alpha']}
    c.update(over)
    return c


def test_a_brief_renders_every_step_in_order():
    text = bridge.brief_text('t0', _impl(steps=_steps()), _repo())
    assert 'STEPS' in text, 'the brief must carry a STEPS section:\n' + text
    body = text[text.index('STEPS'):]
    order = ['1.', 'WHERE: alpha', 'NOW:', 'b = a + 1', 'DO: add 2 instead of 1', 'EXPECT: alpha(1) == 3',
             'IF NOT: stop and report the line you found',
             '2.', 'WHERE: L3-L3', 'return b', 'DO: return b times ten',
             'EXPECT: python3 scripts/tests/test_mod.py exits 0', 'IF NOT: report the failing assertion']
    pos = 0
    for s in order:
        i = body.find(s, pos)
        assert i >= 0, f'{s!r} must follow in order in the STEPS section:\n' + body
        pos = i + len(s)


def test_b_no_steps_no_section():
    text = bridge.brief_text('t0', _impl(), _repo())
    assert 'STEPS' not in text, 'a chunk without steps must render no STEPS section:\n' + text


def _step_errors(chunk):
    return [e for e in bridge.validate([chunk], _repo()) if 'step' in e]


def test_c_validate_refuses_malformed_steps():
    assert _step_errors(_impl(steps='do it')), 'steps that are a string must be refused'
    assert _step_errors(_impl(steps=[])), 'an empty steps list must be refused'
    assert _step_errors(_impl(steps=_steps() * 4)), 'more than six steps must be refused'
    for f in FIELDS:
        s = _steps()
        del s[0][f]
        errs = _step_errors(_impl(steps=s))
        assert errs and f in ' '.join(errs), f'a step missing {f!r} must be refused naming it: ' + repr(errs)


def test_d_validate_accepts_well_formed_steps():
    assert _step_errors(_impl(steps=_steps())) == [], 'well-formed steps add no error'
    assert bridge.validate([_impl()], _repo()) == [], 'a chunk without steps is still valid'


if __name__ == '__main__':
    _tests = [test_a_brief_renders_every_step_in_order, test_b_no_steps_no_section,
              test_c_validate_refuses_malformed_steps, test_d_validate_accepts_well_formed_steps]
    _failed = 0
    for _t in _tests:
        try:
            _t()
            print('PASS  ' + _t.__name__)
        except Exception as _e:
            _failed += 1
            print('FAIL  ' + _t.__name__ + ': ' + type(_e).__name__ + ': ' + str(_e)[:400])
    print(str(len(_tests) - _failed) + '/' + str(len(_tests)) + ' passed')
    sys.exit(1 if _failed else 0)
