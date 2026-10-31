#!/usr/bin/env python3
"""t720 -- a script is ON the Flow chart only when a LINK names it, never prose.

WHY (workspace/rca/927ef79c9880.md, rca/64d5b943a4f0.md, rca/f41e02b0a8f5.md).
scripts/flow_coverage.py answers one question -- "is this permanent script on the chart?"
-- and scripts/library.py's registration gate refuses a script it answers False for. Today
on_chart reads every text field of every task, including the free-text `note`, and accepts
ANY helpers[] row without reading its `serves` list. So a script named only in a sentence,
and a helper row that names no task at all, both count as deployed. Registration is
deployment: the gate in front of the whole permanent population accepts a deployment that
says a script exists and never says what it serves.

THE RULE this suite pins: a script is on the chart when a task's worker, command or queue,
a link's mover, a source's mover, a service's unit or name, OR a helpers[] row whose
`serves` names a task/service id that EXISTS on this chart, names it. A script named only
in a task's `note` is NOT on the chart, and a helper row whose `serves` is empty, missing,
or names only ids that are not on this chart links nothing.

RED TODAY, assertion by assertion:
  RED 1   a task's `note` names the script -> on_chart answers True (the note text is joined
          by _text_fields and swept by named for file stems)
  RED 8   a helpers[] row with an empty `serves` -> True (on_chart short-circuits on
          helper_paths, which returns every helper's `script` key and never reads `serves`)
  RED 9   a helper whose `serves` names an id not on the chart -> True, same reason
  RED 11  flow_coverage has no `serves_link` symbol today
  RED 12  nothing resolves a helper's `serves` to the node it names
  RED 13  uncovered() -- the consumer of on_chart -- reports a prose-named script as covered
KEEP 2-7, 10, 14-18 state the behaviour the change must NOT remove: every real link shape
stays ON the chart, and kind 'test' plus scripts/tests/ stay exempt from the census.

NO LIVE STATE: every flow below is a dict written in this file. KEEP 15 opens the real
FLOW.json READ-ONLY and asserts that a script the chart never names is not on it.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import flow_coverage as fc  # noqa: E402

SCRIPT = 'scripts/probe_x.py'
NOT_ON_ANY_CHART = 'scripts/zz_t720_not_a_real_script.py'
TASK = 't720'
SERVICE = 'svc_hub'
PHANTOM = 't999999'

NOTE_ONLY = {'tasks': [{'id': 't700',
                        'note': 'scripts/probe_x.py was added during this work.'}],
             'links': [], 'sources': [], 'services': [], 'helpers': []}
WORKER_LINK = {'tasks': [{'id': TASK, 'worker': SCRIPT}], 'links': [], 'sources': [],
               'services': [], 'helpers': []}
COMMAND_LINK = {'tasks': [{'id': TASK, 'command': 'python3 ' + SCRIPT + ' --json'}],
                'links': [], 'sources': [], 'services': [], 'helpers': []}
QUEUE_LINK = {'tasks': [{'id': TASK, 'queue': 'scripts/probe_x.py --daemon'}],
              'links': [], 'sources': [], 'services': [], 'helpers': []}
MOVER_LINK = {'tasks': [], 'links': [{'id': 'l720', 'mover': SCRIPT}], 'sources': [],
              'services': [], 'helpers': []}
SOURCE_LINK = {'tasks': [], 'links': [], 'sources': [{'id': 's720', 'mover': SCRIPT}],
               'services': [], 'helpers': []}
SERVICE_LINK = {'tasks': [], 'links': [], 'sources': [],
                'services': [{'id': SERVICE, 'name': 'probe_x.py watches the hub'}],
                'helpers': []}

PASSED = 0
FAILED = 0


def check(label, got, want):
    global PASSED, FAILED
    if got == want:
        PASSED += 1
        print('  [PASS] ' + label)
    else:
        FAILED += 1
        print('  [FAIL] ' + label + ' -- expected ' + repr(want) + ', got ' + repr(got))


def helper_flow(serves):
    """A helpers[] row naming SCRIPT, serving exactly the ids in `serves`.

    The chart it is served on declares task t720, task t721 (note only) and service
    svc_hub, and nothing else, so an id that is not one of those is off the chart.
    """
    return {'tasks': [{'id': TASK, 'worker': 'scripts/other.py'},
                      {'id': 't721', 'note': 'mentions probe_x in prose'}],
            'links': [], 'sources': [], 'services': [{'id': SERVICE, 'unit': 'svc.service'}],
            'helpers': [{'script': SCRIPT, 'serves': list(serves)}]}


def test_prose_is_not_a_link():
    check('RED 1 a task note naming the script is NOT a link',
          fc.on_chart(SCRIPT, NOTE_ONLY), False)


def test_task_worker_is_a_link():
    check('KEEP 2 a task whose worker names the script is a link',
          fc.on_chart(SCRIPT, WORKER_LINK), True)


def test_task_command_is_a_link():
    check('KEEP 3 a task whose command runs the script is a link',
          fc.on_chart(SCRIPT, COMMAND_LINK), True)


def test_task_queue_is_a_link():
    check('KEEP 18 a task whose queue names the script is a link',
          fc.on_chart(SCRIPT, QUEUE_LINK), True)


def test_service_name_is_a_link():
    check('KEEP 4 a service whose name names the script is a link',
          fc.on_chart(SCRIPT, SERVICE_LINK), True)


def test_link_mover_is_a_link():
    check('KEEP 5 a link whose mover names the script is a link',
          fc.on_chart(SCRIPT, MOVER_LINK), True)


def test_source_mover_is_a_link():
    check('KEEP 17 a source whose mover names the script is a link',
          fc.on_chart(SCRIPT, SOURCE_LINK), True)


def test_helper_serving_a_real_task_is_a_link():
    check('KEEP 6 a helper whose serves names a real task is a link',
          fc.on_chart(SCRIPT, helper_flow([TASK])), True)


def test_helper_serving_a_real_service_is_a_link():
    check('KEEP 7 a helper whose serves names a real service is a link',
          fc.on_chart(SCRIPT, helper_flow([SERVICE])), True)


def test_helper_with_empty_serves_is_not_a_link():
    check('RED 8 a helper whose serves is empty links nothing',
          fc.on_chart(SCRIPT, helper_flow([])), False)


def test_helper_serving_an_unknown_id_is_not_a_link():
    check('RED 9 a helper whose serves names an id not on the chart links nothing',
          fc.on_chart(SCRIPT, helper_flow([PHANTOM])), False)


def test_a_script_named_nowhere_is_not_a_link():
    check('KEEP 10 a script the chart never names is not on the chart',
          fc.on_chart(NOT_ON_ANY_CHART, WORKER_LINK), False)


def test_serves_link_is_a_function():
    check('RED 11 flow_coverage exposes serves_link',
          callable(getattr(fc, 'serves_link', None)), True)


def test_serves_link_names_the_node_that_serves_the_script():
    fn = getattr(fc, 'serves_link', None)
    if not callable(fn):
        check('RED 12a serves_link answers for a helper that serves a real task',
              None, 'non-empty list')
        return
    evidence = fn(SCRIPT, helper_flow([TASK]))
    check('RED 12b a helper serving a real task yields evidence, as a non-empty list',
          isinstance(evidence, list) and len(evidence) > 0, True)
    check('RED 12c the evidence names the node id it resolved to',
          any('t720' in str(x) for x in evidence), True)
    check('RED 12d a helper serving a phantom id yields NO evidence',
          fn(SCRIPT, helper_flow([PHANTOM])), [])
    check('RED 12e a helper whose serves is empty yields NO evidence',
          fn(SCRIPT, helper_flow([])), [])
    check('RED 12f a script named only in a task note yields NO evidence',
          fn(SCRIPT, NOTE_ONLY), [])


def test_census_applies_the_link_rule():
    two = {SCRIPT: {'kind': 'tool'}, 'scripts/other.py': {'kind': 'tool'}}
    one = {SCRIPT: {'kind': 'tool'}}
    check('RED 13a uncovered() reports both when the chart says only prose',
          fc.uncovered(NOTE_ONLY, two), ['scripts/other.py', SCRIPT])
    check('RED 13b uncovered() clears a script linked by a task worker',
          fc.uncovered(WORKER_LINK, one), [])
    check('RED 13c uncovered() clears a helper whose serves names a real task',
          fc.uncovered(helper_flow([TASK]), one), [])
    check('RED 13d uncovered() clears a helper whose serves names a real service',
          fc.uncovered(helper_flow([SERVICE]), one), [])
    check('RED 13e uncovered() does NOT clear a helper whose serves is empty',
          fc.uncovered(helper_flow([]), one), [SCRIPT])
    check('RED 13f uncovered() does NOT clear a helper serving an id off the chart',
          fc.uncovered(helper_flow([PHANTOM]), one), [SCRIPT])


def test_test_kind_is_still_exempt():
    check('KEEP 14a a kind-test script stays out of the census',
          fc.uncovered(NOTE_ONLY, {SCRIPT: {'kind': 'test'}}), [])
    check('KEEP 14b a scripts/tests path stays out of the census',
          fc.uncovered(NOTE_ONLY, {'scripts/tests/test_probe_x.py': {'kind': 'tool'}}), [])


def test_real_chart_still_rejects_an_unnamed_script():
    path = ROOT / 'projects' / 'local-inference' / 'models' / 'FLOW.json'
    try:
        flow = json.loads(path.read_text(encoding='utf-8'))
    except Exception as e:
        check('KEEP 15 the real FLOW.json is readable',
              type(e).__name__ + ': ' + str(e), 'readable')
        return
    check('KEEP 15 the real chart does not name ' + NOT_ON_ANY_CHART,
          fc.on_chart(NOT_ON_ANY_CHART, flow), False)


def test_supporting_api_survives():
    for name in ('_text_fields', 'named', 'helper_paths', 'on_chart', 'permanent',
                 'uncovered', 'main'):
        check('KEEP 16 ' + name + '() is still callable',
              callable(getattr(fc, name, None)), True)
    check('KEEP 16 EXEMPT_KINDS is unchanged', getattr(fc, 'EXEMPT_KINDS', None), {'test'})


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith('test_') and callable(v)]
    for t in tests:
        try:
            t()
        except Exception as e:
            global FAILED
            FAILED += 1
            print('  [BAD] ' + t.__name__ + ' raised ' + type(e).__name__ + ': ' + str(e))
    print('test_flow_serves_link: ' + str(PASSED) + ' passed, ' + str(FAILED) + ' failed')
    return 1 if FAILED else 0


if __name__ == '__main__':
    sys.exit(main())
