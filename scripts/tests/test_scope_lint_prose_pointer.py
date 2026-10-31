# t713: scope_lint must name the pointer shape when an oracle's text is prose.
# Five scopes wrote a pointer into oracle_text instead of the oracle file: t658
# (see the fenced python block in the scope body above), t771 and t1011
# (the full file above, verbatim), t726 (SEE CHUNK 1 ABOVE), t735 (PASTE THE FULL
# ORACLE TEXT ABOVE VERBATIM). ast.parse already calls every one of them a syntax
# error at line 1, and the send-back the Manager receives says only 'does not
# parse' -- which the next re-scope answers the same way. This suite pins the shape
# word, and pins that a real oracle with a genuine typo keeps its existing
# evidence, so the new check labels the pointer and not every broken file.
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))

import scope_lint

FILE = 'scripts/tests/test_scope_lint_prose_pointer.py'
NL = chr(10)
Q = chr(34)
S = chr(39)

POINT = '<the full file above, verbatim>' + NL
TYPED = 'import sys' + NL + 'x = ' + S + 'unclosed' + NL
FIRST_LINE_TYPO = ('print(' + Q + 'a' + Q + NL + NL.join('x = ' + str(i) for i in range(12)) + NL)
GOOD = ('import sys' + NL + NL + NL + 'def test_ok():' + NL +
        '    assert sys.version_info[0] >= 3' + NL + NL + NL +
        'if __name__ == ' + S + '__main__' + S + ':' + NL + '    test_ok()' + NL)


def _crash(findings):
    return [f for f in findings if f[0] == 'oracle-crash']


def _oracle(text):
    return {'kind': 'oracle', 'file': FILE, 'oracle_text': text}


def test_prose_pointer_is_named_as_prose():
    hits = _crash(scope_lint.static([_oracle(POINT)], ROOT))
    assert len(hits) == 1, 'expected one oracle-crash finding, got ' + repr(hits)
    cls, idx, name, ev = hits[0]
    assert cls == 'oracle-crash', 'the class must stay oracle-crash: ' + str(cls)
    assert idx == 0, 'the finding must carry the chunk index: ' + repr(idx)
    assert name == FILE, 'the finding must name the oracle file: ' + str(name)
    assert 'prose' in ev.lower(), 'the evidence must say the oracle_text is prose: ' + ev
    assert 'oracle_text' in ev, 'the evidence must name the field to fix: ' + ev
    assert 'the full file above' in ev, 'the evidence must quote what the answer wrote: ' + ev


def test_a_later_line_typo_keeps_the_existing_evidence():
    hits = _crash(scope_lint.static([_oracle(TYPED)], ROOT))
    assert len(hits) == 1, 'a broken string literal is one oracle-crash: ' + repr(hits)
    ev = hits[0][3]
    assert 'does not parse' in ev, 'the existing evidence must survive: ' + ev
    assert 'prose' not in ev.lower(), 'a real oracle must not be relabelled as prose: ' + ev
    assert 'line 2' in ev, 'the line of the real typo must be reported: ' + ev


def test_a_first_line_typo_in_a_real_file_is_not_prose():
    hits = _crash(scope_lint.static([_oracle(FIRST_LINE_TYPO)], ROOT))
    assert len(hits) == 1, 'the first-line typo is one oracle-crash: ' + repr(hits)
    ev = hits[0][3]
    assert 'prose' not in ev.lower(), 'thirteen lines of code is not a pointer: ' + ev
    assert 'does not parse' in ev, 'it keeps the parse evidence: ' + ev


def test_a_valid_oracle_is_not_a_finding():
    findings = scope_lint.static([_oracle(GOOD)], ROOT)
    assert _crash(findings) == [], 'a valid oracle must produce nothing: ' + repr(findings)


if __name__ == '__main__':
    fails = []
    for fn in (test_prose_pointer_is_named_as_prose, test_a_later_line_typo_keeps_the_existing_evidence,
               test_a_first_line_typo_in_a_real_file_is_not_prose, test_a_valid_oracle_is_not_a_finding):
        try:
            fn()
            print('PASS ' + fn.__name__)
        except AssertionError as exc:
            fails.append(fn.__name__)
            print('FAIL ' + fn.__name__ + ': ' + str(exc))
    print(str(4 - len(fails)) + ' of 4 passed')
    sys.exit(1 if fails else 0)
