# t668: docs/LESSONS.md must carry the 'never pipe a long-running job through tail' rule.
#
# Reads the lesson store (no subprocess, no live state) and asserts the NEW lesson is a
# distinct, complete entry: the right subject, an ENFORCEMENT line pointing at the real
# check (check_workspace.py), citations to both neighbours (L-105, L-054), and the
# interactive-shell caveat. Red on main because no lesson newer than L-153 carries it.
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _entries(text):
    # Every lesson begins with a line starting '**L-' followed by a 3-digit number.
    blocks = []
    for part in text.split('\n**L-'):
        head = part[0:3]
        if len(head) == 3 and head.isdigit():
            blocks.append((int(head), part))
    return blocks


def test_store_has_lessons():
    text = (ROOT / 'docs' / 'LESSONS.md').read_text(encoding='utf-8')
    assert len(_entries(text)) >= 2, 'docs/LESSONS.md should hold multiple L-### lessons'


def test_tail_lesson_is_new_and_distinct():
    text = (ROOT / 'docs' / 'LESSONS.md').read_text(encoding='utf-8')
    tails = [(n, b) for (n, b) in _entries(text)
             if ('long-running' in b.lower() and 'tail' in b.lower())]
    assert tails, 'no lesson about piping a long-running job through tail'
    num, block = max(tails)
    assert num > 153, 'the tail lesson must be newer than L-153 (the current newest)'
    assert num not in (54, 105), 'the tail lesson must be its own entry, not L-054 or L-105'


def test_tail_lesson_carries_enforcement_and_citations():
    text = (ROOT / 'docs' / 'LESSONS.md').read_text(encoding='utf-8')
    tails = [(n, b) for (n, b) in _entries(text)
             if ('long-running' in b.lower() and 'tail' in b.lower())]
    assert tails
    _, block = max(tails)
    low = block.lower()
    assert 'enforcement' in low, 'the lesson must carry an ENFORCEMENT line'
    assert 'check_workspace' in low, 'ENFORCEMENT must point at the L-054 check (check_workspace.py)'
    assert 'l-105' in low, 'must cite the process-liveness neighbour L-105'
    assert 'l-054' in low, 'must cite the exit-code neighbour L-054'
    assert 'interactive' in low, 'must keep the honest caveat that interactive shell is unguardable'


if __name__ == '__main__':
    import sys
    failures = 0
    for name in ('test_store_has_lessons',
                 'test_tail_lesson_is_new_and_distinct',
                 'test_tail_lesson_carries_enforcement_and_citations'):
        fn = globals()[name]
        try:
            fn()
            print('PASS ' + name)
        except AssertionError as exc:
            failures += 1
            print('FAIL ' + name + ': ' + str(exc))
    if failures:
        print(str(failures) + ' test(s) failed')
        sys.exit(1)
    print('all tests passed')
