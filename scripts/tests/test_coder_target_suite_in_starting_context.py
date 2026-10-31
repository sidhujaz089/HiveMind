"""t999: coder.py must SHOW the model its own target suite.

Dispatch p332-t768-scope-lint (2026-10-09): the model asked for the target suite on
attempts 2, 4 and 6 and coder.py refused each time with 'no room for more of it beside
the prompt. Work from what you already have'. The brief described the oracle's
assertions in prose, so the model had to satisfy a file it was never allowed to read.

Red on main for the stated reason: coder.py has no target_context, and run() never
places the target suite in the model's starting context -- it is available only on
request, and a request can be refused.

Run: python3 scripts/tests/test_coder_target_suite_in_starting_context.py
"""
import importlib.util
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CODER = ROOT / 'projects' / 'local-inference' / 'coder.py'
TARGET_REL = 'tests/test_demo.py'
ASSERT_LINE = 'assert RATING == 42, RATING'
BIG = 4000

_IMPORT_FAILED = None
try:
    _spec = importlib.util.spec_from_file_location('coder_under_test', CODER)
    coder = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(coder)
except Exception as _e:
    coder = None
    _IMPORT_FAILED = '%s: %s' % (type(_e).__name__, _e)


def _coder():
    if coder is None:
        raise AssertionError('coder.py did not import -- ' + _IMPORT_FAILED)
    return coder


def _mk(root, lines):
    (root / 'tests').mkdir(parents=True, exist_ok=True)
    (root / TARGET_REL).write_text('\n'.join(lines) + '\n', encoding='utf-8')


def _small():
    return ['"""fixture for t999."""', 'RATING = 7', '', ASSERT_LINE]


def test_target_context_exists():
    c = _coder()
    assert hasattr(c, 'target_context'), (
        'coder.py has no target_context: the target suite source never reaches the '
        'model starting context, so a refused file request leaves it coding blind '
        '(p332-t768, attempts 2/4/6)')


def test_shows_the_target_real_assertions():
    c = _coder()
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _mk(root, _small())
        block, budget = c.target_context(root, TARGET_REL, budget=200000, window=262144)
        assert ASSERT_LINE in block, (
            'the block does not carry the target assertion line: prose about the '
            'assertions is what starved p332-t768, not a missing fact')
        assert TARGET_REL in block, 'the block does not name the file it is showing'
        assert 0 < budget <= 200000, 'budget must stay real and positive: %r' % (budget,)
        assert budget <= c.first_budget(len(block), 262144), (
            'the reply budget must never exceed what the window leaves beside the block')


def test_a_reply_still_fits_beside_the_target():
    c = _coder()
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _mk(root, _small())
        block, budget = c.target_context(root, TARGET_REL, budget=200000, window=262144)
        fits, est = c.prompt_fits(len(block), min(budget, c.FILE_REPLY_RESERVE), 262144)
        assert fits, ('a %s-token reply no longer fits beside the target block: ~%s tokens'
                     % (c.FILE_REPLY_RESERVE, est))


def test_a_target_that_cannot_fit_is_refused_loudly():
    c = _coder()
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _mk(root, _small())
        block, budget = c.target_context(root, TARGET_REL, budget=200000, window=4000)
        assert block.strip(), ('a target that cannot fit must be refused LOUDLY, not with '
                               'an empty block: a model shown nothing concludes the suite '
                               'has no assertions')
        assert TARGET_REL in block, 'the refusal must name the file it did not show'
        assert 'not shown' in block, 'the refusal must say it was NOT shown'
        assert budget == 200000, 'a refusal must not consume the reply budget'


def test_a_long_target_announces_its_own_cut():
    c = _coder()
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _mk(root, ['VALUE_%d = %d' % (i, i) for i in range(BIG)])
        block, budget = c.target_context(root, TARGET_REL, budget=200000, window=120000)
        assert len(block.splitlines()) < BIG, (
            'a %d-line target was shown whole: the numbered cap is not being applied' % BIG)
        assert 'TRUNCATED' in block, (
            'the target was cut silently: a model shown half a suite concludes the '
            'missing assertions do not exist')
        assert 0 < budget < 200000, (
            'the budget must shrink by the room the block used: %r' % (budget,))


def test_the_starting_context_shows_the_target_itself():
    _coder()
    src = CODER.read_text(encoding='utf-8')
    head = 'STARTING CONTEXT'
    tail = 'convo, tries = base, []'
    assert head in src and tail in src, (
        'the starting-context assembly moved; locate it before wiring the call')
    i, j = src.index(head), src.index(tail)
    assert 0 <= i < j, ('the starting-context assembly does not precede `convo, tries = '
                       'base, []`: locate it before wiring the call')
    assert 'target_context(' in src[i:j], (
        'run() never calls target_context while building the starting context, so the '
        'target is still only available on request and a refusal hides it')


def _main():
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith('test_') and callable(f)]
    bad = []
    for name, fn in tests:
        try:
            fn()
            print('PASS %s' % name)
        except Exception as e:
            bad.append(name)
            print('FAIL %s: %s: %s' % (name, type(e).__name__, e))
    print('%d/%d passed' % (len(tests) - len(bad), len(tests)))
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(_main())
