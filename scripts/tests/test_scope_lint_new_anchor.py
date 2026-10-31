"""t1547 (RCA, class brief-steps): a step whose `where` names a symbol the chunk CREATES is refused with the anchor-the-neighbour message, whichever of the two places the scope marks the symbol as new.

Evidence: scope-quality.jsonl:22999 (2026-10-21T20:38:44Z, task t1529, model swift, prompt custom:cd3d504cc8a8) -- chunk 1 scripts/session_review.py, step 3: where 'rca_finding_view' is a symbol this chunk creates ... anchor the step at the existing neighbour it goes beside. scope_lint caught it (scope_lint.py:655-659) because the scope listed the name under "new"; steps_findings builds `created` from the "new" key ONLY (scope_lint.py:634), while the manager prompt makes the SYMBOLS form `<name> (new)` the marker a scoper is told to write (manager_prompt.py:59-63). A scope that writes the SYMBOLS marker and omits the parallel `new` list gets scope_lint.py:665's message -- "is not a symbol defined in <file> -- for a non-Python file use L<start>-L<end>" -- a line range in text that does not exist yet. Same fact, two encodings, one read.

RED ON MAIN for the stated reason: test_a is red today because steps_findings does not read the SYMBOLS `(new)` marker, and answers with the "is not a symbol defined in" wording. test_b pins the branch that fired on t1529 (no existing suite covers it); test_c pins the clean shape the refusal tells the scoper to write.
"""
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
'''


def _repo():
    d = Path(tempfile.mkdtemp(prefix='scope-lint-anchor-'))
    (d / 'mod.py').write_text(MOD, encoding='utf-8')
    (d / 'scripts' / 'tests').mkdir(parents=True)
    (d / 'scripts' / 'tests' / 'test_mod.py').write_text('print(1)\n', encoding='utf-8')
    return d


def _chunk(symbols, new):
    return {'kind': 'implementation', 'file': 'mod.py', 'target': 'scripts/tests/test_mod.py',
            'single_change': 'add a view over findings', 'what_is_wrong': 'the review has no view',
            'done_when': 'python3 scripts/tests/test_mod.py exits 0', 'related_entries': ['t1547'],
            'symbols': symbols, 'new': new,
            'steps': [{'where': 'finding_view',
                       'now': 'def finding_view(rows):\n    return rows',
                       'do': 'add def finding_view(rows) returning rows',
                       'expect': 'finding_view exists and the target suite passes',
                       'if_not': 'stop and report the line you found'}]}


def _hits(chunks, repo):
    return [f for f in scope_lint.steps_findings(chunks, repo, require=True)
            if f[0] == 'brief-steps']


def test_a_symbols_new_marker_anchors_the_same_as_the_new_list():
    repo = _repo()
    hits = _hits([_chunk(['alpha', 'finding_view (new)'], [])], repo)
    assert len(hits) == 1, 'one brief-steps finding expected, got ' + repr(hits)
    cls, idx, f, ev = hits[0]
    assert cls == 'brief-steps', 'the class must be brief-steps: ' + repr(hits)
    assert idx == 0, 'the finding is on chunk 0: ' + repr(hits)
    assert f == 'mod.py', 'the finding names mod.py: ' + repr(hits)
    assert 'step 1' in ev, 'the evidence must name the step: ' + repr(ev)
    assert 'creates' in ev, (
        'a step anchored at a symbol the SYMBOLS line marks (new) must be told the file has no '
        'text there yet, as a step anchored via the new key is: ' + repr(ev))
    assert 'anchor' in ev, 'the refusal must name the fix, anchor the existing neighbour: ' + repr(ev)
    assert 'is not a symbol defined in' not in ev, (
        'a (new) name must not be reported as a typo for an existing symbol: ' + repr(ev))


def test_b_the_new_list_anchors_too():
    repo = _repo()
    hits = _hits([_chunk(['alpha', 'finding_view (new)'], ['finding_view'])], repo)
    assert len(hits) == 1, 'the shape that fired on t1529 stays refused: ' + repr(hits)
    assert 'creates' in hits[0][3], repr(hits[0][3])
    assert 'anchor' in hits[0][3], repr(hits[0][3])
    assert 'is not a symbol defined in' not in hits[0][3], repr(hits[0][3])


def test_c_anchoring_at_the_existing_neighbour_is_clean():
    repo = _repo()
    ch = _chunk(['alpha', 'finding_view (new)'], ['finding_view'])
    ch['steps'] = [{'where': 'alpha', 'now': '    b = a + 1\n    return b',
                    'do': 'add def finding_view(rows) after alpha, returning rows',
                    'expect': 'finding_view exists and python3 scripts/tests/test_mod.py exits 0',
                    'if_not': 'stop and report the line you found'}]
    got = _hits([ch], repo)
    assert got == [], 'the fix the message asks for must lint clean: ' + repr(got)


if __name__ == '__main__':
    _tests = [test_a_symbols_new_marker_anchors_the_same_as_the_new_list,
              test_b_the_new_list_anchors_too,
              test_c_anchoring_at_the_existing_neighbour_is_clean]
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
