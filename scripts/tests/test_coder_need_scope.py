"""t1260: coder.py must refuse to SHOW a model-requested file outside the brief's scope.

Recorded evidence (workspace/tasks/t1260--harness-dispatch-offers-feedback-py-as-editable.md):
dispatch p550-t1115-hub-common, attempts 1 and 4, logged 'asked for 2 file(s), gave 2 new' for a
brief whose text said 'ONE file: projects/app-repo/hub_common.py' and 'Do NOT touch feedback.py in
this chunk'. The model asked (obj['need']) for projects/app-repo/feedback.py, the harness showed it,
the model edited it, and the paired oracle's byte-identity assertion died.

readable() has carried an 'out of target' branch for years: def readable(root, rel, targets=None)
at coder.py:523, refusing at coder.py:541-542 with 'out of target: dispatch is scoped to ...'. The
one call site that serves a MODEL-requested file -- coder.py:2892, 'fp, why = readable(root, rel)'
-- never passes targets. So the missing piece is not a rule; it is a decision on the path between
'the model asked' and 'the file is shown'. This suite pins that decision.

Honest limit, recorded so the next reader is not misled: tests 1-6 are the BEHAVIOUR of the
predicate main() will call. Test 7 is the only assertion that pins the wiring, and it is a
source-shape assertion, because reaching the need loop inside main() requires a model call, which
an oracle may never make. A build that defines in_scope and never calls it passes 1-6 and fails 7.
"""
import importlib.util
import inspect
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
CODER = REPO / 'projects' / 'local-inference' / 'coder.py'

# The recorded pair, taken from the brief the dispatch carried. Two DISTINCT real paths, not one
# repeated placeholder: the whole point is that these are different files. The brief authorized
# hub_common.py and named feedback.py as off-limits.
BRIEF_FILES = ['projects/app-repo/hub_common.py']
OUTSIDE = 'projects/app-repo/feedback.py'


def load_coder():
    """Import coder.py from the path it really lives on -- the pattern
    scripts/tests/test_coder_scope_apply.py uses, so the suite is red for the missing change and
    not for its own import. A top-level side effect arrives as a traceback with a cause."""
    spec = importlib.util.spec_from_file_location('coder_under_test', CODER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_scope_predicate_exists():
    c = load_coder()
    assert hasattr(c, 'in_scope'), (
        'coder.py defines no in_scope, so nothing between a model asking for a file and that '
        'file being shown decides the brief scope (t1260)')


def test_a_file_outside_the_brief_is_refused():
    # The recorded case: the brief named hub_common.py; the model asked for feedback.py.
    c = load_coder()
    assert c.in_scope(OUTSIDE, BRIEF_FILES) is False, (
        OUTSIDE + ' is not one of ' + repr(BRIEF_FILES)
        + ' -- showing it is what broke p550-t1115-hub-common')


def test_the_scoped_file_is_still_served():
    c = load_coder()
    assert c.in_scope(BRIEF_FILES[0], BRIEF_FILES) is True, (
        'the file the brief scoped must be served -- refusing it leaves the model with '
        'nothing to edit')


def test_a_bare_name_naming_the_scoped_file_is_served():
    # A model writes bare file names. Scope enforcement that refuses the bare name of the one
    # scoped file trades the t1260 dispatch for a starved one: the model is told to work from a
    # file it was never shown. fill_paths has carried the same tolerance for op paths since p50
    # (its comment records p50 attempt 5 sending 'coder.py' for
    # projects/local-inference/coder.py and being refused 'no such file').
    c = load_coder()
    assert c.in_scope('hub_common.py', BRIEF_FILES) is True, (
        'a bare file name naming the one scoped file must be served')


def test_a_same_named_file_in_another_directory_is_refused():
    # A basename-only rule passes tests 2-4 and re-opens t1260 through the side door:
    # projects/app-repo/feedback.py and src/feedback.py are different files.
    c = load_coder()
    assert c.in_scope('src/hub_common.py', BRIEF_FILES) is False, (
        'matching the basename of a path is not scope: a same-named file in a different '
        'directory is a different file')


def test_an_empty_ask_is_refused():
    # No scope means the loop asks for nothing; an empty ask must not resolve to the scoped
    # file by falling through a basename comparison against an empty string.
    c = load_coder()
    assert c.in_scope('', BRIEF_FILES) is False, 'an empty ask must be refused'
    assert c.in_scope(None, BRIEF_FILES) is False, 'a missing ask must be refused'
    assert c.in_scope('hub_common.py', []) is False, (
        'a brief that named no files authorises no files')
    assert c.in_scope('hub_common.py', None) is False, (
        'a missing authorisation authorises no files')


def test_the_need_loop_applies_the_scope():
    # The defect is the missing argument at coder.py:2892, not a missing rule. Defining
    # in_scope and never calling it reproduces t1260 exactly, so the call site is asserted.
    # This is the one shape assertion in this file -- see the module docstring.
    c = load_coder()
    src = inspect.getsource(c.main)
    assert 'in_scope(rel' in src, (
        'the need loop in main() must decide scope per requested file (coder.py:2892: '
        'fp, why = readable(root, rel)); a helper nobody calls satisfies this suite and '
        'not the dispatch')
    assert 'refused.append' in src, (
        'an out-of-scope ask must be refused INSIDE the loop and named, so the log says why '
        '(the loop already echoes every refusal with a refused: line) -- never silently dropped')


if __name__ == '__main__':
    _fails = []
    for _name in sorted(globals()):
        _fn = globals()[_name]
        if _name.startswith('test_') and callable(_fn):
            try:
                _fn()
                print('ok   ' + _name)
            except Exception as _e:                 # noqa: BLE001 -- read, not traced
                print('FAIL ' + _name + ': ' + type(_e).__name__ + ': ' + str(_e))
                _fails.append(_name)
    print('%d failure(s)' % len(_fails))
    sys.exit(1 if _fails else 0)
