#!/usr/bin/env python3
"""Scopes as granular as a plan: every implementation chunk carries `steps`, and each step's `now`
is the file's real text at its `where` (Jas, 2026-10-21, plan docs/dsh/PLAN-2026-10-21.md, A1).

Jas, on the DSH plan's Do/Expect/If steps: "Are the scopes this detailed? ... if we can provide a
schema this granular that could prevent the amount of reworks." They were not: a chunk carried
`single_change` and `what_is_wrong` as prose, with no quoted code at the edit site. The largest
scope defects since 10-12 are exactly what that leaves open -- brief-scatter 145 of 536 (27%),
brief-unsatisfiable 118 (22%), and brief-unanchored (t502).

The contract tested here, in scope_lint.steps_findings(chunks, repo, require):
  - with require=True an implementation chunk with no steps is a 'brief-steps' finding; with
    require=False (the default every older caller gets) it is not -- the requirement reaches live
    scopers through scope_gate, which passes --require-steps;
  - a step whose `now` is not in the file at its `where` (whitespace-normalised) is a finding: the
    one mechanical proof that the scoper read the file it is scoping;
  - `where` may be a SYMBOLS name or L<start>-L<end>; a symbol not on the chunk's SYMBOLS line is
    a finding; more than scope_lint.MAX_EDIT_SITES steps is a finding;
  - a good chunk has no finding.
And scope_lint's CLI takes --require-steps, and scope_gate.lint passes it.

RED ON MAIN for the stated reason only: scope_lint has no steps_findings and no --require-steps.
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))

import scope_lint  # noqa: E402

MOD = '''X = 1


def alpha(a):
    b = a + 1
    return b


def beta():
    return 2
'''


def _repo():
    d = Path(tempfile.mkdtemp(prefix='scope-lint-steps-'))
    (d / 'mod.py').write_text(MOD, encoding='utf-8')
    (d / 'scripts' / 'tests').mkdir(parents=True)
    (d / 'scripts' / 'tests' / 'test_mod.py').write_text('print(1)\n', encoding='utf-8')
    return d


def _step(**over):
    s = {'where': 'alpha', 'now': '    b = a + 1\n    return b',
         'do': 'return b doubled', 'expect': 'alpha(1) == 4', 'if_not': 'stop and report the line you found'}
    s.update(over)
    return s


def _impl(steps=None, **over):
    c = {'kind': 'implementation', 'file': 'mod.py', 'target': 'scripts/tests/test_mod.py',
         'single_change': 'x', 'what_is_wrong': 'y', 'done_when': 'python3 scripts/tests/test_mod.py exits 0',
         'related_entries': ['t0'], 'symbols': ['alpha']}
    if steps is not None:
        c['steps'] = steps
    c.update(over)
    return c


def _steps_hits(findings):
    return [f for f in findings if f[0] == 'brief-steps']


def test_a_missing_steps_is_refused_only_when_required():
    repo = _repo()
    hits = _steps_hits(scope_lint.steps_findings([_impl()], repo, require=True))
    assert len(hits) == 1 and hits[0][1] == 0 and hits[0][2] == 'mod.py', (
        'a chunk with no steps must be one brief-steps finding on chunk 0, mod.py, when required: ' + repr(hits))
    assert 'steps' in hits[0][3], 'the evidence must say steps are missing: ' + repr(hits[0][3])
    assert _steps_hits(scope_lint.steps_findings([_impl()], repo)) == [], (
        'without require, a chunk with no steps is not a finding (older callers stay as they are)')


def test_b_a_good_chunk_is_clean():
    repo = _repo()
    got = scope_lint.steps_findings([_impl([_step()])], repo, require=True)
    assert got == [], 'a step whose now is in alpha must be clean: ' + repr(got)


def test_c_now_must_be_in_the_file_at_where():
    repo = _repo()
    hits = _steps_hits(scope_lint.steps_findings([_impl([_step(now='    b = a + 2')])], repo))
    assert len(hits) == 1, 'a now that is not alpha\'s text must be a finding: ' + repr(hits)
    assert 'step 1' in hits[0][3], 'the evidence must name the step: ' + repr(hits[0][3])


def test_d_whitespace_is_normalised():
    repo = _repo()
    got = scope_lint.steps_findings([_impl([_step(now='b = a + 1\n\n  return   b')])], repo)
    assert _steps_hits(got) == [], 'indentation and blank lines must not matter: ' + repr(got)


def test_e_where_outside_symbols_is_refused():
    repo = _repo()
    hits = _steps_hits(scope_lint.steps_findings([_impl([_step(where='beta', now='return 2')])], repo))
    assert len(hits) == 1 and 'beta' in hits[0][3], (
        'a where naming a symbol not on the SYMBOLS line must be a finding naming it: ' + repr(hits))


def test_f_line_ranges_anchor_too():
    repo = _repo()
    ok = scope_lint.steps_findings([_impl([_step(where='L5-L6', now='b = a + 1')])], repo)
    assert _steps_hits(ok) == [], 'L5-L6 holds b = a + 1: ' + repr(ok)
    bad = _steps_hits(scope_lint.steps_findings([_impl([_step(where='L10-L11', now='b = a + 1')])], repo))
    assert len(bad) == 1, 'L10-L11 is beta, not b = a + 1: ' + repr(bad)


def test_g_too_many_steps_is_refused():
    repo = _repo()
    n = scope_lint.MAX_EDIT_SITES + 1
    hits = _steps_hits(scope_lint.steps_findings([_impl([_step() for _ in range(n)])], repo))
    assert len(hits) == 1 and str(scope_lint.MAX_EDIT_SITES) in hits[0][3], (
        f'{n} steps must be one finding naming the limit: ' + repr(hits))


def test_h_cli_flag_and_scope_gate_pass_it():
    repo = _repo()
    ans = repo / 'answer.md'
    ans.write_text('x\n```chunks\n' + json.dumps([_impl()]) + '\n```\n', encoding='utf-8')
    cmd = [sys.executable, str(ROOT / 'scripts' / 'scope_lint.py'), str(ans), '--task', 't0',
           '--no-run', '--repo', str(repo)]
    with_flag = subprocess.run(cmd + ['--require-steps'], capture_output=True, text=True, timeout=300)
    without = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    assert 'brief-steps' in with_flag.stdout, 'the CLI must take --require-steps: ' + with_flag.stdout + with_flag.stderr
    assert 'brief-steps' not in without.stdout, 'without the flag no brief-steps finding: ' + without.stdout
    import scope_gate
    seen = []

    class _R:
        returncode, stdout, stderr = 0, '', ''

    real = scope_gate.subprocess.run
    scope_gate.subprocess.run = lambda cmd, **kw: seen.append(cmd) or _R()
    try:
        scope_gate.lint(ans, 't0')
    finally:
        scope_gate.subprocess.run = real
    assert seen and '--require-steps' in seen[0], 'scope_gate.lint must pass --require-steps: ' + repr(seen)


if __name__ == '__main__':
    _tests = [test_a_missing_steps_is_refused_only_when_required, test_b_a_good_chunk_is_clean,
              test_c_now_must_be_in_the_file_at_where, test_d_whitespace_is_normalised,
              test_e_where_outside_symbols_is_refused, test_f_line_ranges_anchor_too,
              test_g_too_many_steps_is_refused, test_h_cli_flag_and_scope_gate_pass_it]
    _failed = 0
    for _t in _tests:
        try:
            _t()
            print('PASS  ' + _t.__name__)
        except Exception as _e:
            _failed += 1
            print('FAIL  ' + _t.__name__ + ': ' + type(_e).__name__ + ': ' + str(_e))
    print(str(len(_tests) - _failed) + '/' + str(len(_tests)) + ' passed')
    sys.exit(1 if _failed else 0)
