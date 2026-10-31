'''t1546: a refused chunks block must name the key the chunk actually used.

Evidence read 2026-11-09 (this session, from scripts/scope_lint.py, scripts/bridge.py and
the task file workspace/tasks/t1546--scope-rca-chunks-invalid.md):
  scripts/bridge.py:135-138 -- validate reads only c.get('file'); when it is absent it
  prints 'chunk N: missing file'. It never names a key the chunk DOES carry, so a chunk
  whose file list sits under a renamed key is refused by a message about a key that is not
  in it. The producer sees a complaint about a field it believes it wrote and re-emits the
  same shape: 8 rows in 7 seconds (17:44:20Z to 17:44:27Z) for scope t1522@7a2b9774e17a.
  scripts/scope_lint.py:681-694 -- lint() already runs bridge.parse and bridge.validate and
  turns every error into a chunks-invalid finding, so the class is caught producer-side,
  before any branch exists. Detection is not the gap; the message is.
  python3 scripts/scope_quality.py report -- swift: 137 chunks-invalid rows, first-pass
  yield 9% (56/652 scopes).

Each test asserts the EVIDENCE TEXT a manager is handed. All of them are red on main for one
missing change: scope_lint has no chunk_shape().

Read-only. Writes only under tempfile.mkdtemp and removes them. The one subprocess call
carries a timeout. No model call, no live server, nothing outside a temp dir.
'''
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / 'scripts'))
import scope_lint

LINT = str(REPO / 'scripts' / 'scope_lint.py')
TASK = 't1546'
TARGET = 'scripts/tests/test_scope_lint_chunk_shape.py'
NL = chr(10)
OPEN = '```chunks' + NL
CLOSE = NL + '```' + NL
REQUIRED5 = ('file', 'single_change', 'what_is_wrong', 'done_when', 'related_entries')

# The shape behind this task's defect: a list under a plural key, an id, and none of the
# fields the contract names. See the task file for the rows.
DRIFTED = [
    {'id': 0, 'files': [TARGET], 'kind': 'oracle', 'target': TARGET,
     'steps': [], 'oracle_text': 'x = 1' + NL},
    {'id': 1, 'files': ['scripts/example.py'], 'kind': 'implementation',
     'target': 'scripts/example.py', 'steps': []},
]

# The same content written to the contract's vocabulary. The negative case: it must NOT fire.
CONTRACT = [{
    'kind': 'implementation',
    'file': 'scripts/bridge.py',
    'target': TARGET,
    'single_change': 'add one function that names the key a chunk used',
    'what_is_wrong': 'the refusal names the field a chunk lacks, never the field it has',
    'done_when': 'python3 ' + TARGET + ' exits 0',
    'related_entries': ['t1546'],
    'steps': [{'where': 'validate', 'now': 'def validate(chunks, repo):',
               'do': 'add a pass that names the key the chunk used',
               'expect': 'one tuple per defect', 'if_not': 'stop and report the line'}],
}]


def _answer(chunks):
    # chr(10), never a literal newline: this file's own text must not open or close a
    # chunks fence in the answer that carries it (bridge.parse takes the LAST block).
    return OPEN + json.dumps(chunks, indent=1) + CLOSE


def _lint(answer):
    d = Path(tempfile.mkdtemp(prefix='t1546-'))
    try:
        f = d / 'answer.md'
        f.write_text(answer, encoding='utf-8')
        return subprocess.run([sys.executable, LINT, str(f), '--task', TASK, '--no-run'],
                              cwd=str(REPO), capture_output=True, text=True, timeout=300)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def _findings(chunks):
    return scope_lint.chunk_shape(chunks, scope_lint.REPO)


def _one(i):
    return [f for f in _findings(DRIFTED) if f[1] == i]


def test_1_the_finding_names_the_key_the_chunk_used():
    out = _one(0)
    assert out, ('chunk 0 carries a key named files, and chunk_shape() reported nothing for it')
    ev = ' '.join(f[3] for f in out)
    assert 'files' in ev, 'the finding must name the key the chunk used: ' + repr(out)
    assert 'file' in ev, 'the finding must name the contract field file: ' + repr(out)


def test_2_one_pass_names_every_required_field_the_chunk_lacks():
    ev = ' '.join(f[3] for f in _one(1))
    named = [k for k in REQUIRED5 if k in ev]
    assert len(named) == 5, ('one pass must name every required field the chunk lacks; named '
                             + repr(named) + ' from ' + repr(ev))


def test_3_every_finding_names_its_chunk_and_the_path_it_declares():
    out = _findings(DRIFTED)
    assert out, 'the drifted block produced no findings at all'
    for cls, i, f, ev in out:
        assert cls == 'chunks-invalid', 'use the existing class: ' + repr(cls)
        assert ev.startswith('chunk ' + str(i) + ':'), repr(ev)
        assert f in (TARGET, 'scripts/example.py'), ('the file slot must be the path the chunk '
                                                     'declares: ' + repr(f))


def test_4_a_chunk_written_to_the_contract_is_not_flagged():
    out = _findings(CONTRACT)
    assert out == [], ('a gate that flags a chunk written to the contract gets routed around: '
                       + repr(out))


def test_5_a_file_key_holding_a_list_is_the_same_drift():
    out = _findings([dict(CONTRACT[0], file=['scripts/example.py'])])
    assert len(out) == 1, repr(out)
    assert 'string' in out[0][3].lower(), ('say the contract field is a single path string: '
                                           + repr(out))


def test_6_the_refusal_a_manager_reads_names_the_rename():
    cp = _lint(_answer(DRIFTED))
    assert cp.returncode == 2, ('scope_lint exits 2 on findings; got ' + str(cp.returncode)
                                + ' on ' + cp.stdout)
    lines = [ln for ln in cp.stdout.splitlines() if 'chunk 1' in ln]
    assert lines, 'no stdout line for the implementation chunk: ' + repr(cp.stdout)
    joined = ' '.join(lines)
    assert 'files' in joined, ('the printed refusal must name the key the chunk used: '
                               + repr(joined))


def test_7_a_contract_shaped_answer_is_not_flagged_for_shape():
    cp = _lint(_answer(CONTRACT))
    hits = [ln for ln in cp.stdout.splitlines() if 'files' in ln]
    assert hits == [], ('a chunk written to the contract was flagged by the new check: '
                        + repr(hits) + ' in ' + repr(cp.stdout))


def main():
    tests = [(n, globals()[n]) for n in sorted(k for k in globals() if k.startswith('test_'))]
    assert tests, 'no test functions defined'
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print('PASS ' + name)
        except AssertionError as exc:
            failed += 1
            print('FAIL ' + name + ': ' + str(exc))
        except Exception as exc:
            failed += 1
            print('ERROR ' + name + ': ' + type(exc).__name__ + ': ' + str(exc))
    print(str(len(tests) - failed) + '/' + str(len(tests)) + ' passed')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
