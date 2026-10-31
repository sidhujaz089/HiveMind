#!/usr/bin/env python3
# t1374: a brief on a LARGE file must carry verbatim source from that file.
# Evidence: workspace/briefs/brief-p564-t800-check-workspace.txt:4 cites check_workspace.py:1486
# and pastes none of it; :14 admits the author never read the file. Every existing scope_lint
# check passed (brief-anchor accepts a line that exists; brief-window accepts a SYMBOLS line).
# The builder edited a 151 KB file it could not see and broke six suites.
# Run: python3 scripts/tests/test_scope_lint_no_source.py
import sys
import shutil
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / 'scripts'))
import scope_lint

CLASS = 'brief-no-source'
REL = 'scripts/gate.py'
REL2 = 'scripts/small.py'
SIG = 'def check_flow_record(repo, settings_dir=None, flow=None):'
DOC = 'findings = collect_flow_findings(repo, settings_dir, flow)'
BODY1 = 'chart = json.loads(chart_path.read_text(encoding=UTF8))'
BODY2 = 'for task in chart.get(TASKS_KEY, []):   # the census is the whole point'
PAD = '# sustained by the workspace gate; a census nobody runs is a written instruction'

SRC = ['import json',
       'import re',
       'MANAGER_ROLES = (manager, frontline, supervisor)',
       '',
       'def collect_flow_findings(repo, settings_dir, flow):',
       '    return []',
       '',
       SIG,
       DOC,
       BODY1,
       BODY2,
       '        findings.append(note)',
       '    return findings',
       '',
       PAD]


def _repo_with_big_file(root):
    # 700 pad lines of 72 chars: the file is over scope_lint.WINDOW_BYTES (40000, scope_lint.py:58)
    d = Path(root) / 'scripts'
    d.mkdir(parents=True, exist_ok=True)
    (Path(root) / REL).write_text(chr(10).join(SRC + [PAD] * 700) + chr(10), encoding='utf-8')
    (Path(root) / REL2).write_text(chr(10).join(SRC) + chr(10), encoding='utf-8')


def _chunk(prose_lines, rel=REL):
    return {'kind': 'implementation',
            'file': rel,
            'single_change': chr(10).join(prose_lines),
            'what_is_wrong': 'no gate reads the census today, so a tracked hook can be repointed green',
            'done_when': 'python3 scripts/tests/test_x.py exits 0',
            'symbols': ['collect_flow_findings', 'check_flow_record'],
            'behaviour_oracle': 'every finding the check produces today is produced unchanged'}


def _only(chunks, root):
    # static() emits every class; this suite judges only its own, so checks below line 370
    # (not read this session) cannot make it red for a reason that is not this change.
    return [f for f in scope_lint.static(chunks, Path(root)) if f[0] == CLASS]


def test_prose_only_brief_on_a_large_file_is_a_finding():
    # the p564 shape, transcribed: names the function, cites a line, shows no source
    tmp = tempfile.mkdtemp(prefix='t1374a-')
    try:
        _repo_with_big_file(tmp)
        prose = ['Add ONE module-level function to scripts/gate.py and call it from inside',
                 'check_flow_record (its def is at the census neighbour, cited at scripts/gate.py:1486).',
                 'Return a list of strings, one finding per script path the chart does not name.']
        found = _only([_chunk(prose)], tmp)
        assert len(found) == 1, found
        assert found[0][0] == CLASS, found[0]
        assert found[0][2] == REL, found[0]
        assert 'verbatim' in found[0][3], found[0]
        assert 'WINDOW_BYTES' in found[0][3], found[0]
        assert len(found[0][3]) > 40, found[0]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_a_brief_that_pastes_the_real_lines_is_not_a_finding():
    tmp = tempfile.mkdtemp(prefix='t1374b-')
    try:
        _repo_with_big_file(tmp)
        prose = [SIG, DOC, BODY1, 'Append every census finding to the list the function returns.']
        assert _only([_chunk(prose)], tmp) == [], 'a brief that shows source was refused'
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_two_lines_of_source_are_still_not_enough():
    tmp = tempfile.mkdtemp(prefix='t1374c-')
    try:
        _repo_with_big_file(tmp)
        prose = [SIG, DOC, 'and everything else about the return contract is described in words here.']
        assert len(_only([_chunk(prose)], tmp)) == 1, 'the floor is not enforced at three lines'
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_short_fragments_do_not_count_as_source():
    tmp = tempfile.mkdtemp(prefix='t1374d-')
    try:
        _repo_with_big_file(tmp)
        prose = ['import json'] * 8
        assert len(_only([_chunk(prose)], tmp)) == 1, 'fragment spam satisfied the check'
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_a_small_file_is_out_of_scope_for_the_check():
    tmp = tempfile.mkdtemp(prefix='t1374e-')
    try:
        _repo_with_big_file(tmp)
        prose = ['Add a guard to the small module: describe it entirely in prose, no excerpt.']
        assert _only([_chunk(prose, REL2)], tmp) == [], 'the gate fired on a file under WINDOW_BYTES'
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_a_missing_file_is_reported_as_absent_not_as_no_source():
    tmp = tempfile.mkdtemp(prefix='t1374f-')
    try:
        _repo_with_big_file(tmp)
        prose = ['Add a function to a file that is not in the repository at all today.']
        assert _only([_chunk(prose, 'scripts/absent.py')], tmp) == [], 'a zero-length read must not masquerade as a source gap'
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _main():
    tests = [(k, v) for k, v in sorted(globals().items()) if k.startswith('test_') and callable(v)]
    fails = 0
    for name, fn in tests:
        try:
            fn()
            print('PASS ' + name)
        except AssertionError as exc:
            fails += 1
            print('FAIL ' + name + ': ' + str(exc))
    print('t1374 brief-no-source: ' + str(len(tests) - fails) + '/' + str(len(tests)) + ' passed')
    return 1 if fails else 0


if __name__ == '__main__':
    sys.exit(_main())
