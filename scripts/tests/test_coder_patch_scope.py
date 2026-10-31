"""t1268: coder.py's WRITE path must honour the file scope the brief declared.

WHY THIS SUITE EXISTS. projects/local-inference/coder.py:1594 is `def apply_ops(root, ops):` --
TWO parameters. Its per-op guards at coder.py:1609-1618 test only that a path is relative,
untraversed, not secret-named and present on disk, so any path a model puts in an op is written.
Dispatch p547-t1114-roles-pages (2026-10-16) shows the cost: a brief saying 'ONE file:
projects/app-repo/roles_pages.py. Edit ... and nothing else' produced patches to 3 files, all 3
were applied, and the extra edits broke a non-target suite. The allowed list is already in scope
at the dispatch site -- coder.py:2961 passes a.files to fill_paths and coder.py:2965 calls
apply_ops one line later -- so this is one argument, not new plumbing.

RED ON MAIN FOR ONE REASON: apply_ops accepts no allowed-files argument, so every call that
passes one raises `TypeError: apply_ops() takes 2 positional arguments but 3 were given`. The last
three tests pass today ON PURPOSE: they pin the two-argument call, the refusal of a '..' path and
the empty-allowlist case, all of which the change must leave intact.

Shape-free by design: every assertion is on-disk bytes or the (ok, detail) return, so a build
that defines a helper nothing calls passes nothing.
"""
import importlib.util
import sys
import tempfile
import traceback
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
CODER = REPO / 'projects' / 'local-inference' / 'coder.py'

TARGET = "\n".join(['VALUE = 1', 'RESULT = 2', 'TAIL = 3', ''])
OTHER = "\n".join(['VALUE = 9', 'RESULT = 8', 'TAIL = 7', ''])
NEWFILE = "\n".join(['def test_x():', '    assert True', ''])
TARGET_99 = "\n".join(['VALUE = 1', 'RESULT = 99', 'TAIL = 3', ''])
TARGET_11 = "\n".join(['VALUE = 11', 'RESULT = 2', 'TAIL = 3', ''])
OTHER_22 = "\n".join(['VALUE = 22', 'RESULT = 8', 'TAIL = 7', ''])


def _coder():
    """Import coder.py from the path it really lives on, the way t989's suite does. Its entry
    point is guarded by __main__, so importing runs module constants only."""
    if not CODER.is_file():
        raise AssertionError('coder.py is not at ' + str(CODER))
    spec = importlib.util.spec_from_file_location('coder_under_test', CODER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _mk(root):
    (root / 'pkg').mkdir(parents=True)
    (root / 'pkg' / 'target.py').write_text(TARGET, encoding='utf-8')
    (root / 'pkg' / 'other.py').write_text(OTHER, encoding='utf-8')


def _read(root, rel):
    return (root / rel).read_text(encoding='utf-8')


def _red(e):
    return AssertionError('apply_ops accepts no allowed-files argument: ' + repr(e))


def test_an_out_of_scope_patch_writes_nothing():
    """The defect: one op in scope, one out. A scope check INSIDE the write loop passes the
    refusal and leaves the in-scope op applied -- ops are sorted by -from at coder.py:1607, so
    the in-scope op is written first. The last assertion is what forces the check above the loop,
    and it is apply_ops's own all-or-nothing promise (docstring, coder.py:1595)."""
    mod = _coder()
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _mk(root)
        ops = [{'path': 'pkg/target.py', 'from': 2, 'to': 2, 'new': 'RESULT = 99'},
               {'path': 'pkg/other.py', 'from': 2, 'to': 2, 'new': 'RESULT = 88'}]
        try:
            ok, detail = mod.apply_ops(root, ops, ['pkg/target.py'])
        except TypeError as e:
            raise _red(e)
        assert ok is False, 'a patch naming a file outside the allowed set must be refused, got ' + repr((ok, detail))
        assert 'pkg/other.py' in str(detail), 'the refusal must name the out-of-scope path, got ' + repr(detail)
        assert 'scope' in str(detail).lower(), 'the reason must be the brief scope, got ' + repr(detail)
        assert _read(root, 'pkg/other.py') == OTHER, 'the out-of-scope file must be byte-identical, got ' + repr(_read(root, 'pkg/other.py'))
        assert _read(root, 'pkg/target.py') == TARGET, 'all-or-nothing: the in-scope op of a refused patch must not be applied either, got ' + repr(_read(root, 'pkg/target.py'))


def test_an_in_scope_patch_is_still_applied():
    """Anti-veto. A gate that refuses everything passes case 1 and fails here: a one-file brief
    whose op names that file is the dispatch this task exists to serve."""
    mod = _coder()
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _mk(root)
        try:
            ok, detail = mod.apply_ops(root, [{'path': 'pkg/target.py', 'from': 2, 'to': 2, 'new': 'RESULT = 99'}], ['pkg/target.py'])
        except TypeError as e:
            raise _red(e)
        assert ok is True, 'an op against an allowed file must be applied, got ' + repr((ok, detail))
        assert _read(root, 'pkg/target.py') == TARGET_99, 'the allowed edit must land, got ' + repr(_read(root, 'pkg/target.py'))
        assert _read(root, 'pkg/other.py') == OTHER, 'the gate must not touch a file the patch never named, got ' + repr(_read(root, 'pkg/other.py'))


def test_two_allowed_files_each_get_their_own_op():
    """DISTINCT values per file: a build that ignores op['path'] and writes one target passes the
    single-file cases and is caught only here."""
    mod = _coder()
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _mk(root)
        ops = [{'path': 'pkg/target.py', 'from': 1, 'to': 1, 'new': 'VALUE = 11'},
               {'path': 'pkg/other.py', 'from': 1, 'to': 1, 'new': 'VALUE = 22'}]
        try:
            ok, detail = mod.apply_ops(root, ops, ['pkg/target.py', 'pkg/other.py'])
        except TypeError as e:
            raise _red(e)
        assert ok is True, 'a patch whose paths are all allowed must be applied, got ' + repr((ok, detail))
        assert _read(root, 'pkg/target.py') == TARGET_11, 'target.py must carry its own op, got ' + repr(_read(root, 'pkg/target.py'))
        assert _read(root, 'pkg/other.py') == OTHER_22, 'other.py must carry its own op, got ' + repr(_read(root, 'pkg/other.py'))


def test_a_path_outside_the_scope_is_refused_as_scope_and_writes_nothing():
    """The reason is the contract. Today 'pkg/added.py' is refused as 'no such file', so a check
    placed after the existence test at coder.py:1615-1616 keeps that wording and the model is
    told a path problem -- which is how t989's frozen-oracle breach got filed as context pressure. The
    first half is an EXISTING file outside the scope (the frozen-test-file shape): today the op
    applies, which is the breach."""
    mod = _coder()
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _mk(root)
        (root / 'pkg' / 'new_test.py').write_text(NEWFILE, encoding='utf-8')
        try:
            ok, detail = mod.apply_ops(root, [{'path': 'pkg/new_test.py', 'from': 1, 'to': 2, 'new': 'def test_x():'}], ['pkg/target.py'])
        except TypeError as e:
            raise _red(e)
        assert ok is False, 'an op against a file the brief excluded must be refused, got ' + repr((ok, detail))
        assert 'pkg/new_test.py' in str(detail), 'the refusal must name the path, got ' + repr(detail)
        assert 'scope' in str(detail).lower(), 'the reason must be the brief scope, got ' + repr(detail)
        assert _read(root, 'pkg/new_test.py') == NEWFILE, 'the excluded file must be byte-identical, got ' + repr(_read(root, 'pkg/new_test.py'))
        try:
            ok2, detail2 = mod.apply_ops(root, [{'path': 'pkg/added.py', 'from': 1, 'to': 1, 'new': 'VALUE = 5'}], ['pkg/target.py'])
        except TypeError as e:
            raise _red(e)
        assert ok2 is False, 'a new path outside the scope must be refused, got ' + repr((ok2, detail2))
        assert 'scope' in str(detail2).lower(), 'it must be a scope refusal, got ' + repr(detail2)
        assert 'no such file' not in str(detail2).lower(), 'the scope check must run before the existence check, got ' + repr(detail2)
        assert not (root / 'pkg' / 'added.py').exists(), 'the excluded path must not be created on disk'


def test_a_traversal_path_is_refused_and_the_sibling_untouched():
    """Green today (coder.py:1610 refuses '..'). Pinned so a scope check that compares by prefix,
    not by the full repo-relative path, cannot pass."""
    mod = _coder()
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / 'outside.py').write_text('VALUE = 1\n', encoding='utf-8')
        ops = [{'path': '../outside.py', 'from': 1, 'to': 1, 'new': 'VALUE = 2'}]
        ok, detail = mod.apply_ops(root, ops, ['outside.py'])
        assert ok is False, "'../outside.py' must never be written, got " + repr((ok, detail))
        assert _read(root, 'outside.py') == 'VALUE = 1\n', 'the sibling file must be untouched, got ' + repr(_read(root, 'outside.py'))


def test_a_two_argument_call_is_unchanged():
    """Back-compat. Every existing two-argument caller must behave exactly as today, including
    write_branch's own apply_ops at coder.py:2207, scripts/local-inference/chatui.py:961 ->
    coder.write_branch, and scripts/tests/test_coder_commit_timeout.py:111."""
    mod = _coder()
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _mk(root)
        ok, detail = mod.apply_ops(root, [{'path': 'pkg/target.py', 'from': 2, 'to': 2, 'new': 'RESULT = 99'}])
        assert ok is True, 'no allowlist must mean no gate, got ' + repr((ok, detail))
        assert _read(root, 'pkg/target.py') == TARGET_99, 'the two-argument call must still write, got ' + repr(_read(root, 'pkg/target.py'))


def test_an_empty_allowed_list_is_not_a_gate():
    """`--files` is `nargs='*' default=None` (coder.py:2421), so a dispatch can arrive with []. An
    empty list means the brief declared no scope, not 'refuse everything'."""
    mod = _coder()
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _mk(root)
        ok, detail = mod.apply_ops(root, [{'path': 'pkg/target.py', 'from': 2, 'to': 2, 'new': 'RESULT = 99'}], [])
        assert ok is True, 'an empty allowed list must not refuse a patch, got ' + repr((ok, detail))
        assert _read(root, 'pkg/target.py') == TARGET_99, 'an empty allowed list must not block the edit, got ' + repr(_read(root, 'pkg/target.py'))


if __name__ == '__main__':
    _fns = [v for k, v in sorted(globals().items())
            if k.startswith('test_') and callable(v)]
    if not _fns:
        print('no test functions collected')
        sys.exit(1)
    _fails = []
    for _fn in _fns:
        try:
            _fn()
            print('ok   ' + _fn.__name__)
        except Exception as _e:
            _fails.append(_fn.__name__)
            print('FAIL ' + _fn.__name__ + ': ' + repr(_e))
            traceback.print_exc()
    print('\n%d/%d passed' % (len(_fns) - len(_fails), len(_fns)))
    sys.exit(1 if _fails else 0)
