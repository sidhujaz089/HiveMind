"""t1512: scope_gate splits a brief-scatter chunk before the first lint call.

Red on main: scope_gate has no split_scatter, so every test below raises AttributeError. The
11-symbol chunk is t450's shape: 11 named symbols in one file overflow the builder's 16k output
cap, and MAX_EDIT_SITES is 6, so 11 sites must become 6 + 5. The gate never blocks the loop.
"""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
import bridge as B       # noqa: E402
import scope_gate as G   # noqa: E402
import scope_lint as L   # noqa: E402

PASSED = FAILED = 0
FILE = 'scripts/scope_lint.py'
TARGET = 'scripts/tests/test_scope_lint.py'
OPEN = '```chunks'
CLOSE = '```'
ELEVEN = ['main', 'record', 'CHECK_NAMES', 'run_oracles', '_repo_modules', '_run_in_group',
          'static', 'contract_violation', 'standing_rule_findings', 'live_state_findings',
          'skill_evidence_findings']
ORDERED = ['skill_evidence_findings', 'live_state_findings', 'standing_rule_findings',
           'contract_violation', 'static', '_run_in_group', '_repo_modules', 'run_oracles',
           'CHECK_NAMES', 'record', 'main']


def report(name, got, want):
    global PASSED, FAILED
    ok = got == want
    PASSED += 1 if ok else 0
    FAILED += 0 if ok else 1
    print(f'  {"PASS" if ok else "FAIL"}  {name}' + ('' if ok else f'  got {got!r} want {want!r}'))


def impl(symbols):
    return {'kind': 'implementation', 'file': FILE, 'target': TARGET,
            'single_change': 'scatter measured by symbol count in ' + FILE,
            'what_is_wrong': 'one brief names more than ' + str(L.MAX_EDIT_SITES) + ' sites',
            'related_entries': [], 'symbols': list(symbols), 'new': [],
            'done_when': 'python3 ' + TARGET + ' exits 0',
            'behaviour_oracle': 'python3 ' + TARGET + ' exits 0'}


def answer(chunks):
    return 'prose above' + '\n' + OPEN + '\n' + json.dumps(chunks) + '\n' + CLOSE + '\n'


def tmp_answer(text):
    ans = Path(tempfile.mkdtemp(prefix='scope-gate-split-')) / 'answer.md'
    ans.write_text(text, encoding='utf-8')
    return ans


def test_split_scatter_rewrites_the_block_and_leaves_the_prose():
    ans = tmp_answer(answer([impl(ELEVEN)]))
    report('an 11-site chunk gains one chunk', G.split_scatter(ans, 't1512'), 1)
    chunks, errors = B.parse(ans.read_text(encoding='utf-8'))
    report('the rewritten block is 2 chunks', len(chunks), 2)
    report('each rewritten chunk is within the site cap',
           [len(c['symbols']) for c in chunks], [6, 5])
    report('the split is by file order, not by brief order',
           [c['symbols'] for c in chunks], [ORDERED[:6], ORDERED[6:]])
    report('the rewritten block parses clean', errors, [])
    report('the prose outside the fence is untouched',
           ans.read_text(encoding='utf-8').startswith('prose above' + '\n' + OPEN), True)
    report('a second call adds nothing', G.split_scatter(ans, 't1512'), 0)


def test_a_chunk_within_the_cap_is_left_byte_identical():
    text = answer([impl(ORDERED[:6])])
    ans = tmp_answer(text)
    report('a 6-site chunk gains no chunk', G.split_scatter(ans, 't1512'), 0)
    report('and the file is byte-identical', ans.read_text(encoding='utf-8'), text)
    plain = tmp_answer('no chunks block here')
    report('an answer with no chunks block gains nothing', G.split_scatter(plain, 't1512'), 0)
    report('and that file is unchanged', plain.read_text(encoding='utf-8'), 'no chunks block here')


def test_gate_carries_the_split_before_the_lint():
    src = (ROOT / 'scripts' / 'scope_gate.py').read_text(encoding='utf-8')
    at = src.index('def gate(') if 'def gate(' in src else -1
    report('split_scatter is defined and called', src.count('split_scatter('), 2)
    report('the call sits inside the gate function body', at >= 0 and 'split_scatter(' in src[at:], True)


def run(name, fn):
    try:
        fn()
    except Exception as e:                     # noqa: BLE001
        report(name, type(e).__name__, 'no exception')


if __name__ == '__main__':
    for name in sorted(dir()):
        if name.startswith('test_'):
            run(name, globals()[name])
    print(f'{PASSED} passed, {FAILED} failed')
    sys.exit(1 if FAILED else 0)
