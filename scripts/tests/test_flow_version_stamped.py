#!/usr/bin/env python3
"""t760: every write to FLOW.json carries a citable version, mirrored into FLOW-CHANGELOG.jsonl.

WHY (workspace/rca/* on task t760): a claim about the chart -- 'scope_quality.route runs on every
tick' -- cannot be pinned to the chart in force when the sentence was written. FLOW.json carries no
version, and projects/local-inference/models/FLOW-CHANGELOG.jsonl begins at n=1
(2026-09-30T06:40:51Z), so 'what did the chart say on 2026-09-28' has no answer. scripts/flow_change.py
is the chart's writer: _write() rewrites FLOW.json and appends
{n, at, who, what, task, ops, before, after} to the changelog. Today neither carries a version, so
'flow version N' names nothing.

BEHAVIOUR asserted, not shape: the FILE ON DISK is stamped (not a dict in memory), the changelog row
carries the same number as its `n`, the stamp INCREMENTS across writes (a constant stamp passes
nothing), the chart's content is written through untouched, and the number _write returns is the
number the chart carries -- that return value is the only handle a finding can cite.

Run: python3 scripts/tests/test_flow_version_stamped.py
"""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))

import flow_change  # noqa: E402

CHART = {'_what': 'test chart', 'tasks': [{'id': 'intake', 'name': 'Intake'}], 'links': []}
ROW_KEYS = {'n', 'at', 'who', 'what', 'task', 'ops', 'before', 'after'}


def _sandbox(tmp):
    """Point flow_change at a throwaway chart and changelog.

    FLOW and LOG are module globals read inside _write, so re-binding them moves every
    write off the live repository. No fixed paths: the caller's TemporaryDirectory only.
    """
    flow = Path(tmp) / 'FLOW.json'
    log = Path(tmp) / 'FLOW-CHANGELOG.jsonl'
    flow.write_text(json.dumps(CHART, indent=1) + '\n', encoding='utf-8')
    flow_change.FLOW = flow
    flow_change.LOG = log
    return flow, log


def _rows(log):
    return [json.loads(line) for line in log.read_text(encoding='utf-8').splitlines() if line.strip()]


def test_the_written_chart_carries_a_flow_version():
    with tempfile.TemporaryDirectory() as tmp:
        flow, _log = _sandbox(tmp)
        n = flow_change._write(CHART, {}, 'test', 't760 oracle write')
        data = json.loads(flow.read_text(encoding='utf-8'))
        assert isinstance(data.get('flow_version'), int), (
            'FLOW.json on disk carries no integer flow_version, so a claim about the chart has '
            'nothing to cite. Keys written: %r' % (sorted(data),))
        assert data['flow_version'] == n, (
            'the chart says flow_version %r but _write returned %r -- the returned number is the '
            'handle a finding cites, so the chart and the return must agree'
            % (data.get('flow_version'), n))
        assert data['tasks'] == CHART['tasks'] and data['links'] == CHART['links'], (
            'stamping the version changed the chart content: tasks/links must be written through')


def test_the_changelog_row_is_the_version_index():
    with tempfile.TemporaryDirectory() as tmp:
        _flow, log = _sandbox(tmp)
        n = flow_change._write(CHART, {}, 'test', 't760 oracle write')
        rows = _rows(log)
        assert len(rows) == 1, 'expected exactly one changelog row, got %d' % len(rows)
        row = rows[0]
        missing = ROW_KEYS - set(row)
        assert not missing, 'the changelog row lost existing keys: %s' % sorted(missing)
        assert row.get('flow_version') == row.get('n'), (
            'the row\'s flow_version %r is not its n %r -- the version a finding cites must be '
            'the number the changelog can be searched by' % (row.get('flow_version'), row.get('n')))
        assert row.get('flow_version') == n, (
            'changelog flow_version %r is not the returned handle %r'
            % (row.get('flow_version'), n))


def test_the_version_increments_across_writes():
    with tempfile.TemporaryDirectory() as tmp:
        flow, log = _sandbox(tmp)
        first = flow_change._write(CHART, {}, 'test', 'first')
        second = flow_change._write(CHART, {}, 'test', 'second')
        assert second == first + 1, (
            'flow_version did not advance with the next write: %r then %r -- a stamp that does not '
            'advance pins no moment in the chart\'s history' % (first, second))
        data = json.loads(flow.read_text(encoding='utf-8'))
        assert data.get('flow_version') == second, (
            'the chart on disk still says flow_version %r after a later write made it %r'
            % (data.get('flow_version'), second))
        got = [(r.get('n'), r.get('flow_version')) for r in _rows(log)]
        assert got == [(first, first), (second, second)], (
            'the changelog is not a version index: %r' % (got,))


def _run():
    tests = sorted((n, f) for n, f in globals().items()
                   if n.startswith('test_') and callable(f))
    failed = 0
    for name, fn in tests:
        try:
            fn()
        except Exception as e:
            failed += 1
            print('FAIL %s: %s' % (name, e))
        else:
            print('ok %s' % name)
    print('%d/%d' % (len(tests) - failed, len(tests)))
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(_run())
