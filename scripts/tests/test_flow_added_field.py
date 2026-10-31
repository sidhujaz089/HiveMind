#!/usr/bin/env python3
'''
t1068 oracle: scripts/flow_lint.py accepts an ISO added date on a chart node and treats it as
descriptive, so the endpoint that writes FLOW.json can record when a node entered the chart.

RED ON MAIN FOR THIS REASON, checkable right now:
    grep -c added projects/local-inference/models/FLOW.json   ->   0
In this module, TASK_FIELDS (scripts/flow_lint.py:58) has no added, so _new_task refuses the key at
scripts/flow_lint.py:94, and edit_task refuses it at scripts/flow_lint.py:169. DESCRIPTIVE
(scripts/flow_lint.py:63) lacks it, so stamping a built-in task is reported by lint as a change to
what that task does -- and flow_change applies an edit only when lint reports no error, no new
warning and no consequence (its docstring, and scripts/flow_change.py:108), so a key that is not
descriptive can never be written to the chart at all. That is why this suite asserts the clean-lint
result and not only that the key is accepted.

WHY THIS FILE IS AUTHORED HERE (the reason six answers were refused): it does not exist on disk, and
bridge refuses an implementation chunk whose target neither exists nor is an oracle in the same
block, so the suite ships as the oracle chunk of this block and the flow_lint chunk names it as its
target.

NO LIVE STATE: flow_lint writes nothing (its own docstring says so; the chart is read through
flow_lint.load and only compared byte-for-byte at the end). No subprocess, no urlopen, no model
call, no fixed /tmp path, no process started. No pytest is installed: run it as
python3 scripts/tests/test_flow_added_field.py
'''
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
import flow_lint  # noqa: E402

DATE = '2026-10-12'
NEW_ID = 'epoch_probe_t1068'
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


def doc_of():
    '''The live chart, on a private copy: the data is never a fixture invented here.'''
    return json.loads(json.dumps(flow_lint.load()))


def builtin_id(doc):
    '''The first task that describes a script (model_task falsy) -- the kind for which lint reports
    consequences, so the kind a stamp must be clean on. Read from the chart, not hardcoded.'''
    for t in doc['tasks']:
        if not t.get('model_task'):
            return t['id']
    return doc['tasks'][0]['id']


def node_of(res, tid):
    '''The node in a lint result, tolerating a refused edit (whose after is None) so a refusal on
    main reads as a FAIL and not as a traceback in the builder log.'''
    after = (res.get('after') or {}) if isinstance(res, dict) else {}
    hits = [t for t in (after.get('tasks') or []) if t.get('id') == tid]
    return hits[0] if hits else {}


def test_1_the_endpoint_can_stamp_a_built_in_task():
    '''The load-bearing behaviour, not a shape. Stamping a date on a real built-in task must leave
    lint reporting no errors, no new warnings and no consequences, because flow_change applies an
    edit only when all three lists are empty. A key the gate calls substantive can never be written,
    so a writer that stamps it in silence loses the date on every path -- which is the defect.'''
    doc = doc_of()
    tid = builtin_id(doc)
    real_name = [t for t in doc['tasks'] if t['id'] == tid][0]['name']
    res = flow_lint.lint(doc, [{'op': 'edit_task', 'id': tid, 'set': {'added': DATE}}],
                         layout=False)
    report('stamping a real built-in task reports no errors', res['errors'], [])
    report('  and no new warnings', res['warnings'], [])
    report('  and no consequences, because a date is not a change to what the task does',
           res['consequences'], [])
    node = node_of(res, tid)
    report('  and the node in the returned chart carries the date', node.get('added'), DATE)
    report('  and keeps its own name, not a copy of the date', node.get('name'), real_name)


def test_2_a_new_task_can_carry_its_epoch():
    '''The real add_task path, with the chart's first task id and name, not a constant invented
    here: a new task named on the Flow tab has to be able to carry the date it entered on.'''
    doc = doc_of()
    tid = doc['tasks'][0]['id']
    name = doc['tasks'][0]['name']
    ops = [{'op': 'add_task', 'task': {'id': NEW_ID, 'name': name, 'added': DATE}}]
    try:
        res = flow_lint.apply(doc, ops)
    except flow_lint.EditError as exc:
        report('a task added with an ISO date is accepted by the gate',
               'refused: ' + str(exc), 'accepted')
        return
    nodes = [t for t in res['tasks'] if t['id'] == NEW_ID]
    report('a task added with an ISO date is accepted by the gate', len(nodes), 1)
    node = nodes[0] if nodes else {}
    report('  and carries that date on the node, not only in the request', node.get('added'), DATE)
    report('  and keeps the name it was given, not the date', node.get('name'), name)
    report('  and a new task gains exactly one key beyond what a task has today, added',
           sorted(set(node) - set(flow_lint.TASK_FIELDS)), ['added'])


def test_3_a_date_is_descriptive_and_a_status_is_not():
    '''The negative half, and the one that keeps any widening honest. DESCRIPTIVE must gain added
    and must NOT gain anything else: status is how a built-in task is switched off, so changing it
    must stay a consequence. A build that empties DESCRIPTIVE to make the date look clean fails
    here, and so does a build that adds the date to DESCRIPTIVE only, leaving the gate refusing the
    key in edit_task (that half is red in test 1).'''
    doc = doc_of()
    tid = builtin_id(doc)
    real = [t for t in doc['tasks'] if t['id'] == tid][0]
    rename = real['name'] + ' (epoch probe)'
    res = flow_lint.lint(doc, [{'op': 'edit_task', 'id': tid,
                                'set': {'name': rename, 'added': DATE}}], layout=False)
    report('a stamp and a rename on a built-in task report no consequence', res['consequences'], [])
    node = node_of(res, tid)
    report('  and the node carries the date and the new name together, from the same edit',
           [node.get('added'), node.get('name')], [DATE, rename])
    res2 = flow_lint.lint(doc, [{'op': 'edit_task', 'id': tid, 'set': {'status': 'paused'}}],
                          layout=False)
    report('  and switching that real built-in task off is STILL a consequence',
           len(res2['consequences']) > 0, True)


def test_4_a_task_created_without_a_date_keeps_every_default_it_has_today():
    '''The gate must not gain a required key. Callers that name no date today must keep working and
    get the empty string, which DESCRIPTIVE ignores, so a chart whose nodes carry no date keeps
    linting exactly as it does today. The defaults asserted are the ones _new_task writes at
    scripts/flow_lint.py:97-104, read from the code, not invented.'''
    doc = doc_of()
    res = flow_lint.apply(doc, [{'op': 'add_task', 'task': {'id': NEW_ID, 'name': 'Epoch probe'}}])
    nodes = [t for t in res['tasks'] if t['id'] == NEW_ID]
    report('a task added with no date is accepted', len(nodes), 1)
    node = nodes[0] if nodes else {}
    report('  and its date is the empty string a reader can tell from a real one',
           node.get('added'), '')
    report('  and every default _new_task gives today is unchanged',
           [node.get('worker'), node.get('worker_kind'), node.get('queue'), node.get('status'),
            node.get('model_task'), node.get('ends'), node.get('outcomes'), node.get('skills')],
           ['', 'role', '', 'active', True, [], [], None])


def test_5_a_date_the_reader_cannot_parse_is_refused_and_says_what_it_rejected():
    '''The off-flow reader compares this value to a session date, so a value it cannot parse is
    worse than no value: it reads as a date that is not one. The refusal must name the field, the
    rejected value and a valid example, so a person reading the Flow tab learns which word to fix.'''
    doc = doc_of()
    tid = doc['tasks'][0]['id']
    for bad, label in [('soon', 'word'), ('2026-10-12T00:00:00Z', 'timestamp'),
                       ('2026-10-12 ', 'date padded with a space'),
                       ('2026-10-12T00:00', 'date with a time part'),
                       (20261012, 'integer'), (None, 'nothing')]:
        res = flow_lint.lint(doc, [{'op': 'edit_task', 'id': tid, 'set': {'added': bad}}],
                             layout=False)
        report('a ' + label + ' in added is refused', bool(res['errors']), True)
        report('  and the refusal names added, the rejected value and a valid example',
               [any('added' in e and repr(bad) in e and '2026-10-12' in e for e in res['errors'])],
               [True])


def test_6_a_date_that_is_not_on_the_calendar_is_refused():
    '''A stamp must be a real calendar day: fromisoformat accepts the shape 2026-02-30 as an ISO
    string and rejects it as a date, and 2026-1-5 is not the form a reader compares against.'''
    doc = doc_of()
    tid = doc['tasks'][0]['id']
    for bad in ['2026-02-30', '2026-13-01', '2026-1-5', '10-12-2026']:
        res = flow_lint.lint(doc, [{'op': 'edit_task', 'id': tid, 'set': {'added': bad}}],
                             layout=False)
        report('the impossible date ' + bad + ' is refused', bool(res['errors']), True)
        report('  and the refusal names added, the rejected value and a valid example',
               [any('added' in e and repr(bad) in e and '2026-10-12' in e for e in res['errors'])],
               [True])


def test_7_reading_the_chart_writes_nothing_to_it():
    '''Negative, and the one that keeps this suite honest: the gate reads FLOW.json and never writes
    it. A suite that stamped the real chart to make its own assertions green would break every
    reader of the file, and back-filling the existing nodes is a data change Claude makes -- this
    suite must leave the chart byte-identical.'''
    before = CHART.read_bytes()
    doc = doc_of()
    flow_lint.lint(doc, [{'op': 'add_helper', 'helper': {'script': 'scripts/flow_lint.py',
                                                         'serves': [doc['tasks'][0]['id']],
                                                         'what': 'epoch probe helper'}}],
                   layout=False)
    report('reading the chart and linting a probe edit leaves the real file byte-identical',
           CHART.read_bytes(), before)


def main():
    print('flow_lint accepts an ISO added date on a chart node and treats it as descriptive')
    for fn in (test_1_the_endpoint_can_stamp_a_built_in_task,
               test_2_a_new_task_can_carry_its_epoch,
               test_3_a_date_is_descriptive_and_a_status_is_not,
               test_4_a_task_created_without_a_date_keeps_every_default_it_has_today,
               test_5_a_date_the_reader_cannot_parse_is_refused_and_says_what_it_rejected,
               test_6_a_date_that_is_not_on_the_calendar_is_refused,
               test_7_reading_the_chart_writes_nothing_to_it):
        print(fn.__name__)
        fn()
    print('')
    print(str(PASSED) + ' passed, ' + str(FAILED) + ' failed')
    return 1 if FAILED else 0


if __name__ == '__main__':
    sys.exit(main())
