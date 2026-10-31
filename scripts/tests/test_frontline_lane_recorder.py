#!/usr/bin/env python3
"""t1340 -- every dispatch the loop finishes records WHICH LANE it ran on, and a Frontline lane
with no dispatch for 72 h is a finding.

RCA workspace/rca/3f1108274896.md: "check builds reach the Frontline again -- nobody has seen
that happen" is a claim with no recorder. Verified 2026-10-17 by parsing every row of the dispatch
ledger: 5544 finished rows, 0 carrying a lane, and 0 carrying a role -- the role is written on the
started row (scripts/dispatch_loop.py:2400) and terminal_row (scripts/dispatch_loop.py:97) drops
it. RCA Rule 4 case 2 therefore applies: the writer comes first.

This suite owns all of its data. The routing store's shape is a literal it deep-copies and writes
into a temp directory of its own, so a Models-tab edit cannot turn it red. It reads no live store,
no ledger, opens no socket, starts no process, calls no model, and writes nothing outside its temp
dir. The `at` format is the one scripts/dispatch_loop.py:93 now() writes.

Run: python3 scripts/tests/test_frontline_lane_recorder.py
"""
import copy
import json
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))

import dispatch_loop  # noqa: E402  the module this suite extends
import task_routing   # noqa: E402  the routing shape the lane vocabulary comes from

# The routing store's SHAPE, written here as data this file owns -- not read off disk. Role keys and
# task names are the ones the store uses, so 'coder' (the Frontline role: the Models tab gives it
# Building, gemma4:26b runs it) is the lane name the ledger records.
STORE = {
    'roles': {
        'manager': {'kind': 'model', 'model': 'qwen3.8:27b-mtp-bf16',
                    'tasks': [{'task': 'assigned_items'}, {'task': 'supervision'},
                              {'task': 'building'}]},
        'auditor': {'kind': 'model', 'model': 'qwen3.8:27b-mtp-q4_K_M-56k',
                    'tasks': [{'task': 'assigned_items'}, {'task': 'building'}]},
        'coder': {'kind': 'model', 'model': 'gemma4:26b', 'tasks': [{'task': 'building'}]},
        'senior': {'kind': 'model', 'endpoint': 'http://127.0.0.1:11435',
                   'tasks': [{'task': 'preflight'}]},
    },
}
AT = '2026-10-17T12:00:00Z'
FRONTLINE = 'coder'
FMT = '%Y-%m-%dT%H:%M:%SZ'


def ago(hours):
    """An `at` string `hours` before AT, in the ledger's own format."""
    t = datetime.strptime(AT, FMT) - timedelta(hours=hours)
    return t.strftime(FMT)


def finished(lane, hours_ago=1):
    return {'event': 'finished', 'kind': 'production', 'lane': lane,
            'key': 'p1-t1340-' + str(lane), 'at': ago(hours_ago)}


def started(**over):
    row = {'kind': 'production', 'chunk': 'p1-t1340', 'model': 'gemma4:26b',
           'key': 'p1-t1340', 'pid': 4242, 'brief': 'brief-p1-t1340.txt',
           'brief_sha256': 'abc123', 'role': FRONTLINE}
    row.update(over)
    return row


def terminal(**over):
    return dispatch_loop.terminal_row(started(**over), exit_code=0, seconds=12,
                                      result_path='dispatch-records/dispatch/x.log', at=AT)


def test_the_routing_fixture_routes_a_build_to_a_role_at_all():
    # Passes today; it is not the red reason. It asserts the data the lane vocabulary comes from is
    # a routing store task_routing.owner() can actually read, and that the Frontline role key --
    # 'coder', the store's Building role on gemma4:26b -- is on the list the recorder mirrors.
    d = copy.deepcopy(STORE)
    got = task_routing.owner(d, 'production')
    assert got == 'manager', 'expected the first owner, got ' + repr(got)
    assert 'building' in [t['task'] for t in d['roles']['coder']['tasks']]


def test_the_routing_fixture_is_a_copy_this_suite_owns():
    with tempfile.TemporaryDirectory(prefix='t1340-') as d:
        p = Path(d) / 'routing.json'
        p.write_text(json.dumps(STORE), encoding='utf-8')
        loaded = json.loads(p.read_text(encoding='utf-8'))
        assert loaded == STORE, 'the fixture did not survive a json round trip'
        stripped = copy.deepcopy(STORE)
        stripped['roles']['manager']['tasks'] = []
        stripped['roles']['auditor']['tasks'] = []
        assert task_routing.owner(stripped, 'production') == 'coder', \
            'owner() did not fall to the Frontline: ' + repr(task_routing.owner(stripped, 'production'))
        assert 'building' in [t['task'] for t in STORE['roles']['coder']['tasks']], \
            'the fixture is shared state, not a copy'


def test_a_finished_dispatch_records_the_lane_it_started_on():
    row = terminal()
    assert 'lane' in row, 'terminal row records no lane; keys are ' + str(sorted(row))
    assert row['lane'] == FRONTLINE, 'lane is ' + repr(row.get('lane')) + ', expected coder'


def test_adding_the_lane_keeps_every_field_a_finished_row_already_carries():
    # The lane is ADDED. failures()/futile()/parked()/lane_status.py read the fields pinned here.
    row = terminal()
    want = {'event': 'finished', 'kind': 'production', 'chunk': 'p1-t1340', 'model': 'gemma4:26b',
            'brief': 'brief-p1-t1340.txt', 'brief_sha256': 'abc123', 'key': 'p1-t1340',
            'pid': 4242, 'exit': 0, 'seconds': 12, 'accepted': True, 'yielded': True,
            'at': AT, 'result_path': 'dispatch-records/dispatch/x.log'}
    for key in sorted(want):
        assert key in row, 'adding the lane dropped field ' + key + ' from ' + str(sorted(row))
        assert row[key] == want[key], 'field ' + key + ' changed to ' + repr(row.get(key))
    assert json.loads(json.dumps(row)) == row, 'a lane value the ledger cannot store'


def test_the_lane_is_per_row_not_one_constant_string():
    a = terminal()
    b = terminal(role='auditor')
    assert b['lane'] == 'auditor', 'role auditor recorded lane ' + repr(b.get('lane'))
    assert a['lane'] != b['lane'], 'every role recorded the same lane: ' + repr(a.get('lane'))


def test_a_row_that_recorded_no_role_records_no_lane():
    # An honest empty, not an invented one. The store's owner of a build is manager today -- not the
    # Frontline -- so substituting the store would report coder work that never happened.
    assert terminal(role=None)['lane'] is None, \
        'a role-less row was given a lane: ' + repr(terminal(role=None).get('lane'))
    assert terminal(role='')['lane'] is None, \
        'an empty role became a lane: ' + repr(terminal(role='').get('lane'))


def test_the_frontline_lane_is_the_coder_role_key():
    assert dispatch_loop.FRONTLINE_LANE == FRONTLINE, \
        'FRONTLINE_LANE is ' + repr(getattr(dispatch_loop, 'FRONTLINE_LANE', 'MISSING'))


def test_a_recent_frontline_build_keeps_the_decay_silent():
    rows = [finished(FRONTLINE, 1)]
    assert dispatch_loop.frontline_gap_hours(rows, at=AT) == 1.0
    assert dispatch_loop.frontline_decay_findings(rows, at=AT) == []


def test_a_frontline_gap_past_the_window_is_a_finding():
    rows = [finished(FRONTLINE, 100)]
    got = dispatch_loop.frontline_gap_hours(rows, at=AT)
    assert got == 100.0, 'gap computed as ' + repr(got) + ', expected 100.0'
    found = dispatch_loop.frontline_decay_findings(rows, at=AT)
    assert len(found) == 1, 'expected one finding, got ' + repr(found)
    assert '72' in found[0], 'the finding does not name the window: ' + repr(found[0])
    assert FRONTLINE in found[0], 'the finding does not name the lane: ' + repr(found[0])


def test_the_window_is_seventy_two_hours():
    assert dispatch_loop.frontline_decay_findings([finished(FRONTLINE, 71)], at=AT) == []
    assert len(dispatch_loop.frontline_decay_findings([finished(FRONTLINE, 73)], at=AT)) == 1


def test_a_quiet_frontline_is_a_finding_while_other_lanes_are_busy():
    rows = [finished('auditor', 0.5), finished('manager', 0.25),
            {'event': 'tick', 'at': ago(0.5), 'running': 3, 'queued_n': 224}]
    assert dispatch_loop.frontline_gap_hours(rows, at=AT) is None, \
        'a busy non-Frontline lane was counted as Frontline'
    found = dispatch_loop.frontline_decay_findings(rows, at=AT)
    assert len(found) == 1, 'an auditor row silenced the recorder: ' + repr(found)
    assert FRONTLINE in found[0], 'the finding does not name the lane: ' + repr(found[0])


def test_the_newest_frontline_row_sets_the_gap_not_the_first_one():
    rows = [finished(FRONTLINE, 500), finished(FRONTLINE, 2), finished(FRONTLINE, 400)]
    assert dispatch_loop.frontline_gap_hours(rows, at=AT) == 2.0, \
        'the gap came from a stale row: ' + repr(dispatch_loop.frontline_gap_hours(rows, at=AT))


def test_a_started_row_is_not_a_finished_one():
    rows = [{'event': 'started', 'kind': 'production', 'lane': FRONTLINE, 'at': ago(1)}]
    assert dispatch_loop.frontline_gap_hours(rows, at=AT) is None, \
        'an unfinished dispatch counted as a completed one'


def test_disabled_builds_are_not_blamed_for_a_quiet_lane():
    rows = [finished(FRONTLINE, 5000)]
    assert dispatch_loop.frontline_decay_findings(rows, at=AT, builds_enabled=False) == []
    assert dispatch_loop.frontline_gap_hours(rows, at=AT) == 5000.0, \
        'disabling the check also broke the measurement it reports on'


def test_an_unparseable_at_is_skipped_not_counted_as_recent():
    rows = [{'event': 'finished', 'kind': 'production', 'lane': FRONTLINE, 'at': 'not-a-date'},
            {'event': 'finished', 'kind': 'production', 'lane': FRONTLINE},
            finished(FRONTLINE, 100)]
    assert dispatch_loop.frontline_gap_hours(rows, at=AT) == 100.0, \
        'a malformed at read as recent: ' + repr(dispatch_loop.frontline_gap_hours(rows, at=AT))
    assert len(dispatch_loop.frontline_decay_findings(rows, at=AT)) == 1


def main():
    tests = [(k, v) for k, v in sorted(globals().items())
             if k.startswith('test_') and callable(v)]
    failed = []
    for name, fn in tests:
        try:
            fn()
            print('PASS ' + name)
        except Exception as e:
            failed.append(name)
            print('FAIL ' + name + ': ' + type(e).__name__ + ': ' + str(e))
    print(str(len(tests) - len(failed)) + '/' + str(len(tests)) + ' passed')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
