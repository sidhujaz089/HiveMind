"""t1512: scope_lint splits a brief-scatter chunk mechanically, by site count only.

Red on main: scope_lint has no split_sites and no split_scattered, so every test below raises
AttributeError. The 11-symbol chunk is t450's shape; MAX_EDIT_SITES is 6, so 11 sites must
become 6 + 5. Every piece keeps the whole prose, file, target and done_when: a piece without
context is t372's failure mode. No model call, no judgement about which changes go together.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
import bridge as B       # noqa: E402
import scope_lint as L   # noqa: E402

PASSED = FAILED = 0
FILE = 'scripts/scope_lint.py'
TARGET = 'scripts/tests/test_scope_lint.py'
ELEVEN = ['main', 'record', 'CHECK_NAMES', 'run_oracles', '_repo_modules', '_run_in_group',
          'static', 'contract_violation', 'standing_rule_findings', 'live_state_findings',
          'skill_evidence_findings']
ORDERED = ['skill_evidence_findings', 'live_state_findings', 'standing_rule_findings',
           'contract_violation', 'static', '_run_in_group', '_repo_modules', 'run_oracles',
           'CHECK_NAMES', 'record', 'main']


def _def_lines(path):
    """{top-level name: its definition line} in the LIVE file. The lines were constants, and
    scope_lint.py kept growing under them (main 621 -> 724 by 2026-10-28), so the oracle went
    unsatisfiable without anything being wrong with a build (Claude, from t1512's objection)."""
    import ast
    tree = ast.parse((ROOT / FILE).read_text(encoding='utf-8'))
    out = {}
    for n in tree.body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out[n.name] = n.lineno
        elif isinstance(n, ast.Assign):
            for t in n.targets:
                if isinstance(t, ast.Name):
                    out[t.id] = n.lineno
    return out


LINE = _def_lines(FILE)
LINES1 = [LINE[s] for s in ORDERED[:6]]
LINES2 = [LINE[s] for s in ORDERED[6:]]
MID = (LINE['static'] + LINE['main']) // 2      # a citation strictly between the two symbols


def report(name, got, want):
    global PASSED, FAILED
    ok = got == want
    PASSED += 1 if ok else 0
    FAILED += 0 if ok else 1
    print(f'  {"PASS" if ok else "FAIL"}  {name}' + ('' if ok else f'  got {got!r} want {want!r}'))


def impl(symbols, cites=()):
    prose = 'scatter measured by symbol count plus cited lines in ' + FILE
    for n in cites:
        prose += ' at scope_lint.py:' + str(n)
    return {'kind': 'implementation', 'file': FILE, 'target': TARGET, 'single_change': prose,
            'what_is_wrong': 'one brief names more than ' + str(L.MAX_EDIT_SITES) + ' sites',
            'related_entries': [], 'symbols': list(symbols), 'new': [],
            'done_when': 'python3 ' + TARGET + ' exits 0',
            'behaviour_oracle': 'python3 ' + TARGET + ' exits 0'}


def test_sites_are_walked_in_file_order_not_brief_order():
    report('split_sites reorders the brief list by definition line',
           L.split_sites(impl(ELEVEN), ROOT), ORDERED)
    report('a cited line is a site, merged into file order',
           L.split_sites(impl(['static', 'main'], cites=[12, MID]), ROOT),
           [12, 'static', MID, 'main'])
    report('a symbol at the same line as a citation is counted once',
           len(L.split_sites(impl(['static', 'main'], cites=[LINE['static'], LINE['main']]), ROOT)), 2)


def test_eleven_sites_become_six_and_five():
    out = L.split_scattered([impl(ELEVEN)], ROOT)
    report('11 sites make 2 chunks', len(out), 2)
    report('the first chunk carries the first 6 sites in file order',
           [c['symbols'] for c in out], [ORDERED[:6], ORDERED[6:]])
    report('every site appears in exactly one chunk',
           sorted([s for c in out for s in c['symbols']]), sorted(ELEVEN))
    report('no site is in both chunks',
           sorted(set(out[0]['symbols']) & set(out[1]['symbols'])), [])
    report('each chunk is within the site cap', [len(c['symbols']) for c in out], [6, 5])
    report('each piece names the lines it is to make',
           [c['edit_lines'] for c in out], [LINES1, LINES2])
    report('each piece names the family it came from',
           [c['split'] for c in out], ['1 of 2', '2 of 2'])


def test_context_is_carried_into_every_piece():
    c = impl(ELEVEN)
    out = L.split_scattered([c], ROOT)
    keys = ['file', 'target', 'done_when', 'single_change', 'what_is_wrong', 'behaviour_oracle']
    report('every piece carries the whole context of its family',
           [[x[k] for k in keys] for x in out], [[c[k] for k in keys] for _ in out])
    report('a split block is lint clean', L.static(out, ROOT), [])
    report('a split block passes bridge.validate', B.validate(out, ROOT), [])
    text = 'prose above' + '\n' + '```chunks' + '\n' + json.dumps(out) + '\n' + '```' + '\n'
    report('the split block round-trips through bridge.parse',
           (B.parse(text)[0], B.parse(text)[1]), (out, []))


def test_a_chunk_at_the_cap_is_left_alone_and_a_split_leaves_no_stray_fragment():
    c6 = impl(ORDERED[:6])
    report('a 6-site chunk is not touched', L.split_scattered([c6, impl(['lint'])], ROOT),
           [c6, impl(['lint'])])
    report('7 sites split into 6 and 1',
           [len(x['symbols']) for x in L.split_scattered([impl(ORDERED[:7])], ROOT)], [6, 1])
    report('12 sites split into 6 and 6, no stray fragment',
           [len(x['symbols']) for x in L.split_scattered([impl(ORDERED + ['_shape_only'])], ROOT)],
           [6, 6])


def test_sites_in_two_files_are_not_this_splits_business():
    c = impl(['static', 'main'])
    c['single_change'] = ('sites in the other file at bridge.py:10 and bridge.py:20 and '
                          'bridge.py:30 and bridge.py:40 and bridge.py:50 and bridge.py:60 and '
                          'bridge.py:70 and bridge.py:80')
    report('a chunk whose cited lines are in another file is not split',
           L.split_scattered([c], ROOT), [c])


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
