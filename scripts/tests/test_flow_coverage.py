#!/usr/bin/env python3
# t797 oracle: the flow-coverage census writer for permanent TEST SUITES.
# Red on main because scripts/flow_coverage.py has none of these yet:
# CLOSING_STATUSES, suite_population, suite_owner, census, write_census.
# The missing writer (t797 data-collection) is the red reason, not a bug here.
# Run: python3 scripts/tests/test_flow_coverage.py
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import flow_coverage as fc  # exists on main; stdlib-only, so the import is safe

LIB = {'scripts': {
    'scripts/tests/test_alpha.py': {'kind': 'test'},
    'scripts/tests/test_flow_coverage.py': {'kind': 'test'},
    'scripts/helper.py': {'kind': 'worker'},
}}
FLOW_DONE_TASK = {
    'tasks': [{'id': 't301', 'status': 'done', 'worker': '',
               'command': 'python3 scripts/tests/test_alpha.py', 'queue': '', 'note': ''}],
    'helpers': [],
}
FLOW_ACTIVE_TASK = {
    'tasks': [{'id': 't302', 'status': 'active', 'worker': '',
               'command': 'python3 scripts/tests/test_alpha.py', 'queue': '', 'note': ''}],
    'helpers': [],
}
FLOW_SERVICE_HELPER = {
    'tasks': [{'id': 't_fit', 'status': 'active', 'worker': '',
               'command': 'python3 scripts/tests/test_flow_coverage.py', 'queue': '', 'note': ''}],
    'services': [{'id': 'svc_hub', 'unit': 'svc_hub.unit', 'name': 'svc_hub'}],
    'helpers': [{'script': 'scripts/tests/test_flow_coverage.py', 'serves': ['svc_hub'],
                 'what': 'the census writer', 'runs': 'on-demand'}],
}


def test_census_api_present():
    missing = [n for n in ('CLOSING_STATUSES', 'suite_population', 'suite_owner',
                           'census', 'write_census') if not hasattr(fc, n)]
    assert not missing, 't797 census writer missing from scripts/flow_coverage.py: ' + ', '.join(missing)


def test_suite_population_selects_test_suites_not_workers():
    got = fc.suite_population(library=LIB)
    assert got == ['scripts/tests/test_alpha.py', 'scripts/tests/test_flow_coverage.py'], got
    assert 'scripts/helper.py' not in got, 'a non-test worker must not be a suite'


def test_suite_owner_resolves_node_and_status():
    assert fc.suite_owner('scripts/tests/test_alpha.py', FLOW_DONE_TASK) == ('t301', 'done')
    assert fc.suite_owner('scripts/tests/test_alpha.py', FLOW_ACTIVE_TASK) == ('t302', 'active')
    node, status = fc.suite_owner('scripts/tests/test_flow_coverage.py', FLOW_SERVICE_HELPER)
    assert node in ('svc_hub', 't_fit'), node
    assert status in ('', 'active'), status
    node, status = fc.suite_owner('scripts/tests/test_alpha.py', {'tasks': [], 'helpers': []})
    assert node in (None, '') and status in (None, ''), (node, status)


def test_census_row_schema_and_stamp():
    rows = fc.census(flow=FLOW_ACTIVE_TASK, library=LIB, stamp='2026-10-01T00:00:00Z')
    assert {r['path'] for r in rows} == {'scripts/tests/test_alpha.py', 'scripts/tests/test_flow_coverage.py'}
    for r in rows:
        assert set(r) == {'path', 'flow_node', 'owner_task', 'owner_status', 'examined_at'}, set(r)
        assert r['examined_at'] == '2026-10-01T00:00:00Z', r['examined_at']
    by_path = {r['path']: r for r in rows}
    assert by_path['scripts/tests/test_alpha.py']['owner_status'] == 'active'
    assert by_path['scripts/tests/test_flow_coverage.py']['owner_status'] in ('', 'active')


def test_unlinked_flags_the_done_task_suite():
    closing = fc.CLOSING_STATUSES
    def unlinked(flow):
        return [r['path'] for r in fc.census(flow=flow, library=LIB)
                if (r['owner_status'] or '') in closing]
    assert 'scripts/tests/test_alpha.py' in unlinked(FLOW_DONE_TASK)
    assert 'scripts/tests/test_flow_coverage.py' not in unlinked(FLOW_DONE_TASK)
    assert 'scripts/tests/test_alpha.py' not in unlinked(FLOW_ACTIVE_TASK)


def test_write_census_appends_and_grows():
    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / 'workspace' / 'flow-coverage.jsonl'
        fc.write_census(fc.census(flow=FLOW_DONE_TASK, library=LIB, stamp='2026-10-01T00:00:00Z'), out)
        fc.write_census(fc.census(flow=FLOW_ACTIVE_TASK, library=LIB, stamp='2026-10-02T00:00:00Z'), out)
        lines = [json.loads(x) for x in out.read_text(encoding='utf-8').splitlines() if x.strip()]
        assert len(lines) == 4, len(lines)
        assert {l['examined_at'] for l in lines} == {'2026-10-01T00:00:00Z', '2026-10-02T00:00:00Z'}
        assert any(l['owner_status'] == 'done' for l in lines), 'a done-task row must be recorded'


def _run():
    tests = [v for k, v in sorted(globals().items()) if k.startswith('test_') and callable(v)]
    failed = 0
    for t in tests:
        try:
            t()
        except Exception as exc:
            failed += 1
            print('FAIL ' + t.__name__ + ': ' + str(exc))
        else:
            print('PASS ' + t.__name__)
    print(str(len(tests) - failed) + '/' + str(len(tests)) + ' oracle tests passed')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(_run())
