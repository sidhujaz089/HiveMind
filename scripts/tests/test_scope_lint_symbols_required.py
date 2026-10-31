#!/usr/bin/env python3
"""t1072 -- scope_lint must refuse an implementation chunk that names no SYMBOLS.

From t1056's analysis (workspace/analysis/t1056-why-work-lands-on-claude.md): on 2026-10-09 every
one of 22 builds that reached Claude did so for one of two reasons, and one of them was a brief
with no SYMBOLS line. scripts/auto_merge.py refuses to merge a change whose hunks it cannot
localise to a named symbol, so a brief that names nothing is work that can NEVER auto-merge -- it
is delivered to a person by construction. scope_lint is the only place upstream of the bridge
where the scoper can still act on that, and today it says nothing about it.

RED ON MAIN for the stated reason only: scope_lint.static() has no check for a missing SYMBOLS
line, so test_a gets zero findings (the message prints what static() did return). The other cases
assert a rule that does not exist on main and pass vacuously until test_a is fixed.

Behaviour, not shape: a finding must exist for the chunk, point at its chunk index and its file,
and say SYMBOLS is missing. The negatives keep the fix narrow -- naming one symbol clears it, an
append chunk is exempt (the builder is shown the file's tail, not the file), and one chunk naming
symbols does not clear a sibling that names none.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))

import scope_lint  # noqa: E402

PY_FILE = 'scripts/auto_merge.py'      # 11,946 bytes: under the 40 KB window, so ONLY a
                                       # missing-SYMBOLS rule can fire on it
OTHER_PY = 'scripts/scope_lint.py'     # 32,784 bytes: a second real .py file, per-chunk accounting
DOC = 'docs/LESSONS.md'                # 275,589 bytes: appended at its end, no symbols to name
TARGET = 'scripts/tests/test_auto_merge.py'


def _impl(**over):
    c = {
        'kind': 'implementation',
        'file': PY_FILE,
        'target': TARGET,
        'single_change': 'fixture prose for the missing-symbols check',
        'what_is_wrong': 'fixture prose for the missing-symbols check',
        'done_when': 'python3 scripts/tests/test_auto_merge.py exits 0',
    }
    c.update(over)
    return c


def _fields(finding):
    """(class, chunk index, file, evidence) from a finding, in whichever shape static() records."""
    if isinstance(finding, dict):
        return (finding.get('class') or finding.get('kind'),
                finding.get('index', finding.get('i', finding.get('chunk'))),
                finding.get('file'),
                finding.get('evidence') or finding.get('detail'))
    return finding[0], finding[1], finding[2], finding[3]


def _symbol_findings(findings, file=PY_FILE):
    """The findings for one file that say SYMBOLS is missing, matched on the evidence text: a
    class name is a label, 'SYMBOLS is missing' is the behaviour being asked for."""
    out = []
    for f in findings:
        cls, idx, fn, ev = _fields(f)
        if fn == file and 'symbol' in str(ev).lower():
            out.append((cls, idx, fn, ev))
    return out


def test_a_a_chunk_that_names_no_symbols_is_refused():
    findings = scope_lint.static([_impl()], ROOT)
    found = _symbol_findings(findings)
    assert len(found) == 1, (
        'an implementation chunk that names no SYMBOLS must be refused (t1072): auto_merge.py '
        'cannot localise a change it cannot name, so such a brief is work that can never '
        'auto-merge. static() returned ' + repr(findings))
    cls, idx, fn, ev = found[0]
    assert isinstance(cls, str) and cls.strip(), 'the finding needs a class, got ' + repr(cls)
    assert idx == 0, 'the finding must point at the offending chunk, got index ' + repr(idx)
    assert fn == PY_FILE, 'the finding must name the chunk file, got ' + repr(fn)
    assert 'SYMBOLS' in str(ev), 'the evidence must name SYMBOLS so the scoper can act: ' + repr(ev)
    assert PY_FILE in str(ev), 'the evidence must name the file so the fix is actionable: ' + repr(ev)


def test_b_naming_a_symbol_clears_it():
    findings = scope_lint.static([_impl(symbols=['judge'])], ROOT)
    assert _symbol_findings(findings) == [], (
        'the rule must refuse the ABSENCE of symbols, not every chunk: naming one symbol has to '
        'clear it, or every brief into a small file becomes unbuildable: ' + repr(findings))


def test_c_a_non_python_append_chunk_is_exempt():
    findings = scope_lint.static([_impl(file=DOC, append=True)], ROOT)
    assert _symbol_findings(findings, DOC) == [], (
        'a chunk that only appends to a file has no symbols to name -- the builder is shown its '
        'last 80 lines -- so refusing it makes every LESSONS.md chunk unbuildable: ' + repr(findings))


def test_d_an_append_chunk_on_a_python_file_is_exempt_too():
    findings = scope_lint.static([_impl(append=True)], ROOT)
    assert _symbol_findings(findings) == [], (
        'append is the exemption the brief format already uses for show the tail, not the file; '
        'the new rule must not quietly re-open it: ' + repr(findings))


def test_e_one_chunk_naming_symbols_does_not_clear_a_sibling():
    chunks = [_impl(), _impl(file=OTHER_PY, symbols=['static'])]
    found = _symbol_findings(scope_lint.static(chunks, ROOT))
    assert [f[1] for f in found] == [0], (
        'the requirement is per chunk: chunk 1 naming static must not clear chunk 0 naming '
        'nothing. SYMBOLS findings: ' + repr(found))


if __name__ == '__main__':
    _tests = [test_a_a_chunk_that_names_no_symbols_is_refused,
              test_b_naming_a_symbol_clears_it,
              test_c_a_non_python_append_chunk_is_exempt,
              test_d_an_append_chunk_on_a_python_file_is_exempt_too,
              test_e_one_chunk_naming_symbols_does_not_clear_a_sibling]
    _failed = 0
    for _t in _tests:
        try:
            _t()
            print('PASS  ' + _t.__name__)
        except AssertionError as _e:
            _failed += 1
            print('FAIL  ' + _t.__name__ + ': ' + str(_e))
    print(str(len(_tests) - _failed) + '/' + str(len(_tests)) + ' passed')
    sys.exit(1 if _failed else 0)
