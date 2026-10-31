#!/usr/bin/env python3
'''
t1068 oracle: scripts/flow_change.py stamps an ISO added date on every FLOW.json node at its own
write endpoint, and the changelog row records the document that was stamped.

RED ON MAIN FOR THIS REASON, checkable right now:
    grep -c added projects/local-inference/models/FLOW.json   ->   0
The write endpoint is _write (def at scripts/flow_change.py:108): answer, add_helper and revert all
call it, it serialises the caller's after document at scripts/flow_change.py:113, replaces the file
at 114 and appends the changelog row at 116. Nothing there stamps a date, so no change to the chart
has ever recorded when a node entered it, and the off-flow reader (workspace/rca/912572409c92.md)
compares a session against a chart that carries no dates at all.

WHY THIS FILE IS AUTHORED HERE (the reason six answers were refused): it does not exist on disk, and
bridge refuses an implementation chunk whose target neither exists nor is an oracle in the same
block, so the suite ships as the oracle chunk of this block and the flow_change chunk names it as
its target.

NO LIVE STATE: _write is redirected into a tempfile directory at call time -- the same redirect this
module documents for LANE_LOG at scripts/flow_change.py:42-43 and honours because _write and entries
read FLOW and LOG as module globals. The real chart is read, never written: the last test asserts it
comes back byte-identical. No subprocess, no urlopen, no model call, no fixed /tmp path, no process
started. No pytest is installed: run it as python3 scripts/tests/test_flow_epoch_stamp.py
'''
import copy
import json
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
import flow_change  # noqa: E402
import flow_lint  # noqa: E402

DATE = '2026-10-12'
COLLECTIONS = ('tasks', 'sources', 'services', 'procedures', 'helpers')
CHART = ROOT / 'projects' / 'local-inference' / 'models' / 'FLOW.json'

PASSED = 0
FAILED = 0


def report(name, got, want):
    global PASSED, FAILED
    ok = got == want
    PASSED += 1 if ok else 0
    FAILED += 0 if ok else 1
    print('  ' + ('PASS' if ok else 'FAIL') + '  ' + name)
    if not ok:
        print('        got  ' + repr(got))
        print('        want ' + repr(want))


def key_of(node, collection):
    '''The identity the chart uses: id on every node kind, script on a helper. flow_lint._helper keys
    a helper by script, so a build that stamps by id alone loses all 198 helpers.'''
    return node.get('script') if collection == 'helpers' else node.get('id')


def real_doc():
    '''The chart read through the gate, on a private copy: the data is never a fixture.'''
    return json.loads(json.dumps(flow_lint.load()))


def stamp(doc, today=DATE):
    '''Red today as a FAIL and not a traceback: flow_change has no stamp_added on main.'''
    try:
        return flow_change.stamp_added(doc, today)
    except AttributeError:
        report('flow_change.stamp_added(doc, today) is callable',
               'AttributeError: the module has no stamp_added', 'a callable')
        return None


def test_1_every_node_gets_the_date():
    '''The chart on disk, read through the gate: every node in every node collection carries the
    date, the stamp reports the count it applied, and the value is an ISO date.'''
    doc = real_doc()
    total = sum(len(doc.get(k, [])) for k in COLLECTIONS)
    report('the chart has nodes to date (a chart with zero nodes proves nothing)', total > 0, True)
    stamped = stamp(doc)
    report('stamp_added returns how many nodes it stamped, the chart read through the gate',
           stamped, total)
    report('  and every node in every node collection carries the date',
           [(k, sum(1 for n in doc.get(k, []) if n.get('added') == DATE)) for k in COLLECTIONS],
           [(k, len(doc.get(k, []))) for k in COLLECTIONS])
    report('  and the value stamped is a plain ISO date, not a timestamp',
           sorted({n.get('added') for k in COLLECTIONS for n in doc.get(k, [])}), [DATE])


def test_2_stamping_is_idempotent_and_keeps_a_real_date():
    '''A stamp must be applied once. A node that already carries a parseable date keeps exactly that
    date, so re-stamping the chart stamps nothing -- and a value no reader can parse is replaced, not
    trusted, because a stamp of not-a-date makes the off-flow comparison silently unreadable.'''
    doc = real_doc()
    total = sum(len(doc.get(k, [])) for k in COLLECTIONS)
    stamp(doc)
    report('stamping the same document a second time stamps nothing', stamp(doc), 0)
    report('  and no node lost the date it was given',
           sum(1 for k in COLLECTIONS for n in doc.get(k, []) if n.get('added') == DATE), total)
    keep = real_doc()
    keep['tasks'][0]['added'] = '2026-01-05'
    stamp(keep, today=DATE)
    report('a node carrying a real date keeps exactly that date, not today',
           keep['tasks'][0].get('added'), '2026-01-05')
    repair = real_doc()
    repair['tasks'][0]['added'] = 'not-a-date'
    stamp(repair, today=DATE)
    report('a stamp no reader can parse is replaced by a real date',
           repair['tasks'][0].get('added'), DATE)


def test_3_stamping_changes_no_node_and_no_link():
    '''The negative half, and the one a rewrite-that-stamps passes: stamping must add, drop, rename
    or reorder no node, must touch no link, and must change no top-level key of the chart. A build
    that stamps by rebuilding the chart into a new shape passes its own suite and breaks every reader
    of the file -- including the 198 helpers, which are keyed by script and not by id.'''
    doc = real_doc()
    keys_before = sorted(doc)
    ids_before = {k: [key_of(n, k) for n in doc.get(k, [])] for k in COLLECTIONS}
    links_before = copy.deepcopy(doc.get('links', []))
    stamp(doc)
    report('stamping changes the top-level keys of the chart in no way', sorted(doc), keys_before)
    report('  and adds, drops, renames or reorders no node',
           {k: [key_of(n, k) for n in doc.get(k, [])] for k in COLLECTIONS}, ids_before)
    report('  and touches no link: a link is an edge, not a node',
           doc.get('links', []), links_before)
    report('  and adds no key to any link',
           [sorted(l) for l in doc.get('links', [])], [sorted(l) for l in links_before])


def test_4_the_default_date_is_today_in_iso_form():
    '''No caller passes a date: answer, add_helper and revert call the endpoint with none. The
    default must be today in the ISO form the off-flow reader compares against a session date.'''
    doc = real_doc()
    fn = getattr(flow_change, 'stamp_added', None)
    if fn is None:
        report('stamp_added(doc) works with no date passed',
               'AttributeError: the module has no stamp_added', 'callable with one argument')
        return
    try:
        fn(doc)
    except TypeError as exc:
        report('stamp_added(doc) works with no date passed', 'TypeError: ' + str(exc),
               'callable with one argument')
        return
    today = time.strftime('%Y-%m-%d', time.gmtime())
    report('  and the default date is today, in ISO form, on every node',
           sorted({n.get('added') for k in COLLECTIONS for n in doc.get(k, [])}), [today])


def test_5_the_write_endpoint_stamps():
    '''The reason the change belongs in the endpoint and not in a caller: the file _write puts on
    disk carries the date, and the changelog row records the stamped document, not the argument the
    caller handed in. FLOW and LOG are redirected into a temp directory, so nothing under projects
    is written by this suite.'''
    saved_flow, saved_log = flow_change.FLOW, flow_change.LOG
    try:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            flow_change.FLOW, flow_change.LOG = tmp / 'FLOW.json', tmp / 'FLOW-CHANGELOG.jsonl'
            doc = real_doc()
            total = sum(len(doc.get(k, [])) for k in COLLECTIONS)
            report('the chart read through the gate has nodes to date', total > 0, True)
            try:
                n = flow_change._write(doc, real_doc(), 't1068', 'epoch stamp probe', ops=[])
            except TypeError as exc:
                report('_write accepts a today keyword', 'TypeError: ' + str(exc),
                       'a trailing keyword argument today')
                return
            written = json.loads((tmp / 'FLOW.json').read_text(encoding='utf-8'))
            report('_write stamps every node in the file it writes',
                   sum(1 for k in COLLECTIONS for x in written.get(k, [])
                       if x.get('added') == DATE), total)
            rows = [json.loads(line) for line in
                    (tmp / 'FLOW-CHANGELOG.jsonl').read_text(encoding='utf-8').splitlines()
                    if line.strip()]
            report('  and it returns the changelog number of the row it appended', n, 1)
            report('  and the row records the STAMPED document, not the pre-stamp argument',
                   sum(1 for k in COLLECTIONS
                       for x in (rows[-1].get('after') or {}).get(k, [])
                       if x.get('added') == DATE), total)
            report('  and the row keeps every field it writes today',
                   [k in rows[-1] for k in ('n', 'at', 'who', 'what', 'task', 'ops', 'before',
                                            'after')],
                   [True] * 8)
            report('  and the row keeps the un-stamped document it was given as before',
                   sum(1 for k in COLLECTIONS
                       for x in (rows[-1].get('before') or {}).get(k, [])
                       if x.get('added') == DATE), 0)
    finally:
        flow_change.FLOW, flow_change.LOG = saved_flow, saved_log


def test_6_the_real_chart_is_untouched():
    '''Negative, and the one that keeps this suite honest: the chart on disk is read, never written.
    Back-filling the existing 269 nodes is the data half of the task, not this suite's job -- a suite
    that demanded it could not be greened by editing scripts/flow_change.py alone, which is the rule
    for a chunk.'''
    before = CHART.read_bytes()
    doc = real_doc()
    stamp(doc)
    report('stamping a private copy writes nothing to the real chart', CHART.read_bytes(), before)


def main():
    print('flow_change stamps an ISO added date on every chart node at its own write endpoint')
    for fn in (test_1_every_node_gets_the_date,
               test_2_stamping_is_idempotent_and_keeps_a_real_date,
               test_3_stamping_changes_no_node_and_no_link,
               test_4_the_default_date_is_today_in_iso_form,
               test_5_the_write_endpoint_stamps,
               test_6_the_real_chart_is_untouched):
        print(fn.__name__)
        fn()
    print('')
    print(str(PASSED) + ' passed, ' + str(FAILED) + ' failed')
    return 1 if FAILED else 0


if __name__ == '__main__':
    sys.exit(main())
