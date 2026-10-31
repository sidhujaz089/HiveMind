#!/usr/bin/env python3
"""t789 oracle: a defect row must name its contract; a declared contract needs two distinct ends.

RED ON MAIN for the missing change: scripts/scope_lint.py defines neither CROSS_COMPONENT_CLASS
nor contract_violation(), so `import scope_lint` succeeds and the attribute lookups below raise
AttributeError. The repository's cause categories today (workspace/failure-schema.json lines 4-11)
are content, external, harness-capability and environment-precondition -- there is no category for
'true only at the seam between two components', so L-114's diff baseline vs diff_of(SOURCE_EXT),
L-115's consumers of one implicit contract, and branch_exists() printing a flat 'branch MISSING'
from a lookup that examined only refs/heads are all statable as single-function rows and nothing
refuses them (workspace/rca/430f8f2b7b28.md sections 2 and 5).

Read-only: workspace/failure-schema.json is read, never written; rows are deep-copied.
"""
import copy
import json
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
import scope_lint

SCHEMA = ROOT / 'workspace' / 'failure-schema.json'

# The real seam pairs named in workspace/rca/430f8f2b7b28.md section 2 -- distinct producers,
# distinct consumers, distinct invariants (not one repeated placeholder string).
L114 = {
    'class': 'cross-component-contract',
    'cause_category': 'content',
    'reason': 'the diff baseline recorded *.py and *.md while diff_of walks SOURCE_EXT, so every Kotlin edit was invisible',
    'contract': {
        'producer': 'scripts/coder.py baseline(*.py,*.md)',
        'consumer': 'scripts/coder.py diff_of(SOURCE_EXT)',
        'invariant': 'the baseline enumerates exactly the extensions diff_of walks',
    },
}
BRANCH_WRITER = {
    'class': 'cross-component-contract',
    'cause_category': 'harness-capability',
    'reason': "branch_exists() printed a flat 'branch MISSING' from a lookup that examined only refs/heads",
    'contract': {
        'producer': 'scripts/merge_gate.py branch_exists(refs/heads)',
        'consumer': 'scripts/merge_gate.py callers that ask about a tag',
        'invariant': 'a ref a caller names is looked up in every namespace that can hold it',
    },
}


def test_class_name_is_a_constant_in_code():
    assert scope_lint.CROSS_COMPONENT_CLASS == 'cross-component-contract', scope_lint.CROSS_COMPONENT_CLASS


def test_a_row_with_a_two_sided_contract_is_clean():
    for row in (L114, BRANCH_WRITER):
        assert scope_lint.contract_violation(copy.deepcopy(row)) == '', row['class']


def test_a_row_stated_single_function_is_reported_by_name():
    row = copy.deepcopy(L114)
    del row['contract']
    v = scope_lint.contract_violation(row)
    assert v and 'contract' in v.lower(), v


def test_a_contract_missing_an_end_names_the_key_that_is_missing():
    for key in ('producer', 'consumer', 'invariant'):
        gone = copy.deepcopy(BRANCH_WRITER)
        del gone['contract'][key]
        v = scope_lint.contract_violation(gone)
        assert v and key in v.lower(), (key, v)
        blank = copy.deepcopy(BRANCH_WRITER)
        blank['contract'][key] = '   '
        v2 = scope_lint.contract_violation(blank)
        assert v2 and key in v2.lower(), (key, v2)


def test_two_ends_must_be_distinct():
    row = copy.deepcopy(L114)
    row['contract']['consumer'] = row['contract']['producer']
    v = scope_lint.contract_violation(row)
    assert v and 'two distinct' in v.lower(), v


def test_the_seam_class_cannot_declare_no_contract():
    row = {'class': scope_lint.CROSS_COMPONENT_CLASS, 'cause_category': 'content',
           'reason': 'producer and consumer disagree about the same ref',
           'contract': 'none', 'contract_reason': 'it is a seam'}
    v = scope_lint.contract_violation(row)
    assert v and 'cross-component' in v.lower(), v


def test_none_needs_a_reason_and_a_reason_is_accepted():
    bare = {'class': 'window-full', 'cause_category': 'content',
            'reason': 'one function, one defect', 'contract': 'none'}
    v = scope_lint.contract_violation(bare)
    assert v and 'contract_reason' in v.lower(), v
    stated = dict(bare, contract_reason='true inside one function; no second component reads it')
    assert scope_lint.contract_violation(stated) == '', stated


def test_a_string_contract_that_is_not_none_is_reported():
    row = {'class': 'window-full', 'cause_category': 'content', 'reason': 'r', 'contract': 'see the brief'}
    v = scope_lint.contract_violation(row)
    assert v and 'contract' in v.lower(), v


def test_the_real_failure_rows_are_the_population_the_checker_stops():
    data = json.loads(SCHEMA.read_text(encoding='utf-8'))
    rows = data.get('failures')
    assert isinstance(rows, list) and len(rows) >= 5, f'{SCHEMA}: expected a list of defect rows'
    for row in rows:
        assert isinstance(row, dict) and row.get('class'), row
        bare = copy.deepcopy(row)
        bare.pop('contract', None)
        bare.pop('contract_reason', None)
        v = scope_lint.contract_violation(bare)
        assert v and 'contract' in v.lower(), f"row {row.get('class')!r} stated single-function was not reported"


def _chunk(**over):
    c = {'kind': 'implementation', 'file': 'scripts/scope_lint.py',
         'target': 'scripts/tests/test_failure_schema_contract.py',
         'single_change': 'add the seam cause class and validate a declared contract',
         'what_is_wrong': 'no cause category for a defect true only at a seam',
         'done_when': 'python3 scripts/tests/test_failure_schema_contract.py exits 0'}
    c.update(over)
    return c


def test_static_refuses_a_chunk_whose_contract_is_incomplete():
    out = scope_lint.static([_chunk(contract={'producer': 'scripts/merge_gate.py branch_exists',
                                              'consumer': '',
                                              'invariant': 'a ref a caller names is looked up in every namespace'})], ROOT)
    hits = [o for o in out if o[0] == 'contract-invalid']
    assert len(hits) == 1, out
    assert 'consumer' in hits[0][3].lower(), hits[0]


def test_static_passes_a_complete_contract_and_a_chunk_that_declares_none():
    full = scope_lint.static([_chunk(contract={'producer': 'scripts/merge_gate.py branch_exists(refs/heads)',
                                              'consumer': 'scripts/merge_gate.py callers asking about a tag',
                                              'invariant': 'a ref a caller names is looked up in every namespace'})], ROOT)
    assert [o for o in full if o[0] == 'contract-invalid'] == [], full
    plain = scope_lint.static([_chunk()], ROOT)
    assert [o for o in plain if o[0] == 'contract-invalid'] == [], plain


def _main():
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith('test_') and callable(f)]
    failed = []
    for name, fn in tests:
        try:
            fn()
            print(f'PASS {name}')
        except Exception:
            failed.append(name)
            print(f'FAIL {name}')
            traceback.print_exc()
    print(f'{len(tests) - len(failed)}/{len(tests)} passed')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(_main())
