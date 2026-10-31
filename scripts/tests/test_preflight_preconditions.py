#!/usr/bin/env python3
r'''t1230 -- a brief's claims about the tree are checked before the brief reaches the model.

THE SPECIMEN (workspace/briefs/brief-p577-t735-session-review.txt:12, quoted in
workspace/tasks/t1230--harness-dispatch-does-not-verify-preconditions-b.md:19):

    MUST STAY GREEN, and do not edit any of them:
    scripts/tests/test_session_review_verification_claims.py (it is green on main
    today; its length assertion is relaxed to membership by a precondition commit
    outside your file - if it fails, your DISPOSITIONS edit dropped or renamed a
    value, do not touch the suite)

That relaxation is not in the tree. scripts/tests/test_session_review_verification_claims.py:221
reads  assert len(mod.DISPOSITIONS) == 5  -- that is the line, in the test named
test_the_vocabulary_is_added_not_replaced. The change t735's brief demands adds a SIXTH value to
DISPOSITIONS in scripts/session_review.py, so the named suite turns RED by the only edit the
brief permits. The model added the sixth value (correct), the sibling suite went red (correct),
the brief forbids touching the suite, and the dispatch failed twice on identical grounds. No
model action satisfies that brief: it asserts a precondition of the working tree that the
working tree does not meet, and nothing in the dispatch path reads it.

scripts/preflight_brief.py today checks that every named path EXISTS (main's no-model early
return), shows the target's imports (target_deps) and the files the brief names (brief_refs, via
REF_PATH). It never RUNS anything, so "suite X is green at build time" is taken as a fact.
This suite pins the check that was missing: a brief naming a sibling suite that is red on this
tree is stopped at the gate, in words, before a model dispatch spends its minutes finding out.

Pure: tempfile only, no model call, no live server, no subprocess left running. The two fixture
suites have DISTINCT bodies on purpose -- a check that cannot tell a green suite from a red one
is the vacuous-pass shape (t471, t297, t370; RCA t613).

Run: python3 scripts/tests/test_preflight_preconditions.py   (pytest is not installed)
'''
import importlib.util
import inspect
import re
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / 'scripts' / 'preflight_brief.py'

FN = 'precondition_findings'
CONSTS = ('PRECONDITION_SUITES_CAP', 'PRECONDITION_TIMEOUT_S')

# Two real, distinct bodies. RED prints a marker the finding must carry whole.
RED = "#!/usr/bin/env python3\nprint('RED-SUITE-MARKER')\nraise SystemExit(1)\n"
GREEN = "#!/usr/bin/env python3\nraise SystemExit(0)\n"


def _load():
    spec = importlib.util.spec_from_file_location('preflight_brief', MODULE_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _tree(tmp, bodies):
    (tmp / 'scripts' / 'tests').mkdir(parents=True, exist_ok=True)
    for name, body in bodies.items():
        (tmp / 'scripts' / 'tests' / (name + '.py')).write_text(body)
    return tmp


def _brief(names):
    return ('ONE file: scripts/session_review.py, add one DISPOSITIONS value.\n\n'
            'MUST STAY GREEN, and do not edit any of them: '
            + ', '.join('scripts/tests/%s.py' % n for n in names))


def test_the_check_and_its_bound_are_named():
    mod = _load()
    gap = [n for n in (FN,) + CONSTS if not hasattr(mod, n)]
    assert not gap, (
        'scripts/preflight_brief.py defines none of: ' + ', '.join(gap)
        + ' -- nothing in the dispatch path reads the claims a brief makes about files'
        ' outside its own change set, which is how brief-p577-t735-session-review.txt'
        ' asked for a "precondition commit outside your file" the tree does not have'
        ' (workspace/tasks/t1230--harness-dispatch-does-not-verify-preconditions-b.md)')
    assert isinstance(mod.PRECONDITION_SUITES_CAP, int) and mod.PRECONDITION_SUITES_CAP >= 1
    assert mod.PRECONDITION_TIMEOUT_S > 0


def test_a_named_sibling_suite_that_is_red_stops_the_brief():
    mod = _load()
    with tempfile.TemporaryDirectory() as d:
        tmp = _tree(Path(d), {'test_sib': RED})
        out = mod.precondition_findings(_brief(['test_sib']),
                                        files=['scripts/session_review.py'],
                                        target='scripts/tests/test_target.py',
                                        repo=tmp)
    assert len(out) == 1, out
    assert out[0].startswith('PRECONDITION NOT MET: scripts/tests/test_sib.py exits 1'), out
    assert 'RED-SUITE-MARKER' in out[0], (
        out[0] + ' -- a stop that does not carry what the suite printed is a record'
        ' nobody can act on (never-truncate-a-record)')


def test_a_green_sibling_named_by_the_brief_is_not_a_finding():
    mod = _load()
    with tempfile.TemporaryDirectory() as d:
        tmp = _tree(Path(d), {'test_sib': GREEN})
        out = mod.precondition_findings(_brief(['test_sib']), files=[], target=None, repo=tmp)
    assert out == [], out


def test_the_target_and_the_editable_files_are_never_checked():
    # The target suite is red BY DESIGN -- a dispatch exists to turn it green -- and a file
    # the builder will edit is red today and green after. Checking either stops every
    # dispatch, which is a gate that gets bypassed within a week (planning-for-sustainment).
    mod = _load()
    with tempfile.TemporaryDirectory() as d:
        tmp = _tree(Path(d), {'test_sib': RED, 'test_mine': RED})
        out = mod.precondition_findings(_brief(['test_sib', 'test_mine']),
                                        files=['scripts/tests/test_mine.py'],
                                        target='scripts/tests/test_sib.py',
                                        repo=tmp)
    assert out == [], out


def test_a_path_that_is_not_on_disk_is_not_reported_as_red():
    # main() already reports a named path that does not exist. Reporting it again as a red
    # suite would invent a cause for a stop whose real cause is a missing file.
    mod = _load()
    with tempfile.TemporaryDirectory() as d:
        tmp = _tree(Path(d), {})
        out = mod.precondition_findings(_brief(['test_absent']), files=[], target=None, repo=tmp)
    assert out == [], out


def test_a_suite_named_twice_is_reported_once():
    mod = _load()
    with tempfile.TemporaryDirectory() as d:
        tmp = _tree(Path(d), {'test_sib': RED})
        out = mod.precondition_findings(_brief(['test_sib', 'test_sib']),
                                        files=[], target=None, repo=tmp)
    assert len(out) == 1, out


def test_the_bound_reports_what_it_did_not_run():
    # A check skipped for budget reasons is named, never silently dropped: a silent gap in a
    # record reads as a pass (never-truncate-a-record; the unknown bucket of
    # planning-for-sustainment's inversion).
    mod = _load()
    cap = mod.PRECONDITION_SUITES_CAP
    names = ['test_s%d' % i for i in range(cap + 1)]
    with tempfile.TemporaryDirectory() as d:
        tmp = _tree(Path(d), dict((n, RED) for n in names))
        out = mod.precondition_findings(_brief(names), files=[], target=None, repo=tmp)
    assert len(out) == cap + 1, out
    assert sum(1 for f in out if f.startswith('PRECONDITION NOT MET:')) == cap, out
    assert sum(1 for f in out if f.startswith('PRECONDITION NOT CHECKED:')) == 1, out
    assert out[-1].startswith('PRECONDITION NOT CHECKED: scripts/tests/%s.py' % names[-1]), out


def test_every_suite_it_names_is_run_under_a_timeout():
    mod = _load()
    src = inspect.getsource(getattr(mod, FN))
    sites = [m.start() for m in re.finditer(r'subprocess\.run\(', src)]
    assert sites, ('the check must RUN the suites a brief names; asserting about them in'
                   ' prose is what failed on 2026-10-16 (brief p577-t735, attempts 2 and 3)')
    for pos in sites:
        assert 'timeout=' in src[pos:pos + 400], (
            'subprocess.run without timeout= breaks the rule every oracle is held to'
            ' (oracle-gate class)')


TESTS = (
    test_the_check_and_its_bound_are_named,
    test_a_named_sibling_suite_that_is_red_stops_the_brief,
    test_a_green_sibling_named_by_the_brief_is_not_a_finding,
    test_the_target_and_the_editable_files_are_never_checked,
    test_a_path_that_is_not_on_disk_is_not_reported_as_red,
    test_a_suite_named_twice_is_reported_once,
    test_the_bound_reports_what_it_did_not_run,
    test_every_suite_it_names_is_run_under_a_timeout,
)

if __name__ == '__main__':
    failed = 0
    for fn in TESTS:
        try:
            fn()
            print('ok   %s' % fn.__name__)
        except Exception as exc:                                # a crash is a failure, not a pass
            failed += 1
            print('FAIL %s: %s: %s' % (fn.__name__, type(exc).__name__, exc))
    print('\n%d/%d passed' % (len(TESTS) - failed, len(TESTS)))
    raise SystemExit(1 if failed else 0)
