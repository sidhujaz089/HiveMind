#!/usr/bin/env python3
"""Work sent back to scoping is told what its last scope is missing (Jas, 2026-10-21: "the flows that
send work back need to update missing criteria from the scope").

Every flow that returns a task to the scoper -- a bridge or lint refusal, a preflight objection, a
build still red after every attempt, a Manager answer with no chunks block, a withdrawn chunk, a
re-scope after Jas's reply -- ends in one place: manager_prompt.build_prompt(tid) for the next
scoping session. So that is where the gap list goes. build_prompt reads the task's last answer
(workspace/run-logs/scoping-queue/out/<tid>.md), parses its chunks block, and checks each
implementation chunk's steps against the repository AS IT IS NOW with the same check scope_gate
applies (scope_lint.steps_findings, require=True): a chunk with no steps, or a step whose `now` is
no longer the file's text (main moved, or it never was), is listed by chunk and step, with the
instruction to fill it.

Contract:
  - a previous chunk with no steps -> the prompt carries the gap section naming chunk 0 and "no steps";
  - a previous step whose now is stale -> the section names "step 1";
  - previous steps that still hold -> no gap section;
  - no previous answer -> no gap section.

RED ON MAIN for the stated reason only: build_prompt never reads the previous answer's chunks.
"""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))

import manager_prompt  # noqa: E402

HEAD = 'WHAT YOUR LAST SCOPE OF THIS TASK IS MISSING'


def _root(chunks=None):
    d = Path(tempfile.mkdtemp(prefix='scope-lint-gaps-'))
    (d / 'workspace' / 'tasks').mkdir(parents=True)
    (d / 'workspace' / 'tasks' / 't9--fix-alpha.md').write_text('# Fix alpha\n\nstatus: queued\n', encoding='utf-8')
    (d / 'mod.py').write_text('def alpha(a):\n    b = a + 1\n    return b\n', encoding='utf-8')
    (d / 'scripts' / 'tests').mkdir(parents=True)
    (d / 'scripts' / 'tests' / 'test_mod.py').write_text('print(1)\n', encoding='utf-8')
    if chunks is not None:
        out = d / 'workspace' / 'run-logs' / 'scoping-queue' / 'out'
        out.mkdir(parents=True)
        (out / 't9.md').write_text('answer\n\n```chunks\n' + json.dumps(chunks) + '\n```\n', encoding='utf-8')
    return d


def _impl(steps=None):
    c = {'kind': 'implementation', 'file': 'mod.py', 'target': 'scripts/tests/test_mod.py',
         'single_change': 'x', 'what_is_wrong': 'y', 'done_when': 'python3 scripts/tests/test_mod.py exits 0',
         'related_entries': ['t0'], 'symbols': ['alpha']}
    if steps is not None:
        c['steps'] = steps
    return c


def _step(now):
    return {'where': 'alpha', 'now': now, 'do': 'd', 'expect': 'e', 'if_not': 'stop'}


def _gap(prompt):
    return prompt[prompt.index(HEAD):] if HEAD in prompt else None


def test_a_missing_steps_are_listed():
    g = _gap(manager_prompt.build_prompt('t9', _root([_impl()])))
    assert g is not None, 'a previous chunk with no steps must put the gap section in the prompt'
    assert 'chunk 0' in g and 'no steps' in g, 'the section must name chunk 0 and say no steps:\n' + g


def test_b_stale_now_is_listed():
    g = _gap(manager_prompt.build_prompt('t9', _root([_impl([_step('    b = a + 2')])])))
    assert g is not None and 'step 1' in g, 'a stale now must be listed by step:\n' + str(g)


def test_c_steps_that_hold_add_nothing():
    p = manager_prompt.build_prompt('t9', _root([_impl([_step('    b = a + 1')])]))
    assert HEAD not in p, 'steps that still match the file must add no gap section'


def test_d_no_previous_answer_no_section():
    assert HEAD not in manager_prompt.build_prompt('t9', _root()), 'a first scope has no gap section'


if __name__ == '__main__':
    _tests = [test_a_missing_steps_are_listed, test_b_stale_now_is_listed,
              test_c_steps_that_hold_add_nothing, test_d_no_previous_answer_no_section]
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
