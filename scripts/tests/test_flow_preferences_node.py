'''t1058 -- the Flow chart can address a standing agent instruction.

The finding (workspace/rca/7dd208ad6b42.md): the rule 'edit docs/PREFERENCES.md in the
same turn when Jas corrects a working assumption, or a run reveals something that changes
how the next task is scoped' has been in docs/PREFERENCES.md since 2026-08-26 and exercised
in 20+ later commits, and appears NOWHERE in projects/local-inference/models/FLOW.json
(grep -in preferences over that file: zero hits, re-verified). The chart can address
scripts, services, sources, tasks, helpers and, since t780, procedures -- not prose rules.
With no chart object for the rule, no chart-enforcing detector can fire on its absence and
the off-flow finding has nothing to close against.

This oracle pins the chart half only, by reading the one JSON file:
  * a procedures node for the standing rule, naming the artefact file and its anchor;
  * it carries the rule's own words, so one repeated placeholder string cannot pass;
  * it satisfies the schema scripts/tests/test_flow_procedure_node.py (t780) already
    demands of every procedure: owner, >=2 distinct steps, inputs, outputs, serves, status,
    and no 'script' key smuggled into the helpers lane;
  * it is ATTACHED -- every serves id resolves to a task id in this same file (t780's
    standard: 'a reference that resolves to nothing is the off-flow shape');
  * it is attributable -- installed_by_task names this task;
  * the procedure t780 registered keeps its real steps -- the lane is added to, not rewritten.

Deliberately NOT asserted: that flow_coverage.on_chart('docs/PREFERENCES.md') resolves.
flow_coverage.py:38-41 joins only sources' `mover` text and named() matches .py/.sh stems
only, so no content added to FLOW.json can make that true today. That is RCA item 2, a
change to a different file, and a scope of its own.

Every assertion is scoped to the node this task adds, so the file is red on main for the
missing node and for nothing else. No subprocess, no model, no live state, no writes.

Run: python3 scripts/tests/test_flow_preferences_node.py
Red on main for one reason: FLOW.json's procedures lane has no node for PREFERENCES.md.
'''
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FLOW = ROOT / 'projects' / 'local-inference' / 'models' / 'FLOW.json'

ARTEFACT = 'docs/PREFERENCES.md'
ANCHOR = 'keeping this file current'
# The rule's own words, from the sentence the finding is built on (workspace/rca/
# 7dd208ad6b42.md, quoting the assistant mid-session): 'when you correct a working
# assumption, or a run reveals something that changes how the next task is scoped, edit it
# in the same turn. session_end.py is a backstop, not the mechanism.'
RULE_PHRASES = ('working assumption', 'the next task is scoped', 'same turn', 'backstop')


def _chart():
    return json.loads(FLOW.read_text(encoding='utf-8'))


def _task_ids(chart):
    return {str(t.get('id')) for t in chart.get('tasks', [])}


def _preferences_nodes(chart):
    return [n for n in chart.get('procedures', [])
            if 'preferences' in json.dumps(n).lower()]


def _the_node():
    chart = _chart()
    nodes = _preferences_nodes(chart)
    ids = sorted(str(n.get('id')) for n in chart.get('procedures', []))
    assert nodes, ('FLOW.json has ' + str(len(ids)) + ' procedure(s) -- ' +
                   ', '.join(ids) + ' -- and none of them mentions PREFERENCES. The rule '
                   '"edit docs/PREFERENCES.md in the same turn" has no chart object to '
                   'attach to, which is the defect t1058 closes.')
    assert len(nodes) == 1, ('more than one procedure mentions PREFERENCES: ' +
                             ', '.join(sorted(str(n.get('id')) for n in nodes)) +
                             ' -- the chart is the system of record, so a standing rule '
                             'gets exactly one node.')
    return nodes[0]


def test_a_preferences_procedure_is_registered():
    assert _the_node().get('id'), 'the PREFERENCES procedure has no id'


def test_the_procedure_addresses_the_file_it_keeps_current():
    art = str(_the_node().get('artefact') or '')
    norm = art.replace('#', '/').replace('-', ' ').lower()
    assert 'preferences' in norm, ("the procedure's artefact is " + repr(art) +
                                   ' -- the standing rule lives in ' + ARTEFACT +
                                   ', and a procedure that does not name its file cannot '
                                   'be reached from the file')
    assert ANCHOR in norm, ('artefact ' + repr(art) + ' does not point at the section ' +
                            ANCHOR + " -- the RCA's address is " + ARTEFACT +
                            '#keeping-this-file-current')


def test_the_procedure_carries_the_rule_in_its_own_words():
    node = _the_node()
    parts = [str(node.get('trigger') or ''), str(node.get('note') or '')]
    parts += [str(s) for s in node.get('steps', [])]
    joined = ' | '.join(parts).lower()
    for want in RULE_PHRASES:
        assert want in joined, ('no trigger, step or note of procedure ' +
                                str(node.get('id')) + ' says ' + repr(want) + '. The rule '
                                'as stated in the finding: "when you correct a working '
                                'assumption, or a run reveals something that changes how '
                                'the next task is scoped, edit it in the same turn. '
                                'session_end.py is a backstop, not the mechanism." '
                                'Text on record: ' + joined)


def test_the_procedure_meets_the_schema_t780_demands_of_procedures():
    node = _the_node()
    nid = str(node.get('id'))
    for field in ('id', 'name', 'owner', 'steps', 'inputs', 'outputs', 'serves', 'status'):
        assert field in node, ('procedure ' + nid + ' is missing ' + repr(field) +
                               ' -- scripts/tests/test_flow_procedure_node.py (t780) '
                               'requires it of every procedure, so a node without it is '
                               'invisible to the machinery that reads this lane')
    assert str(node.get('owner') or '').strip(), 'procedure ' + nid + ' has no owner'
    assert str(node.get('status') or '').strip(), 'procedure ' + nid + ' has no status'
    steps = [str(s).strip() for s in node.get('steps', [])]
    assert len(steps) >= 2, ('procedure ' + nid + ' has ' + str(len(steps)) +
                             ' step(s) -- a procedure is multi-step')
    assert all(steps), 'procedure ' + nid + ' has an empty step'
    assert len(set(steps)) == len(steps), ('procedure ' + nid + ' repeats a step string: ' +
                                           str(steps) + ' -- the rule has distinct moments, '
                                           'not one placeholder repeated')
    assert isinstance(node.get('inputs'), list), 'inputs must be a list'
    assert isinstance(node.get('outputs'), list), 'outputs must be a list'
    assert 'script' not in node, ('procedure ' + nid + ' carries a script path -- that is '
                                  'the helpers lane, and a procedure recorded there has no '
                                  'owner and no steps, so the off-flow pass cannot see it')


def test_the_procedure_is_attached_to_the_session_machinery():
    node = _the_node()
    known = _task_ids(_chart())
    serves = [str(s) for s in node.get('serves', [])]
    assert serves, ('procedure ' + str(node.get('id')) + ' serves nothing -- an unattached '
                    'node is exactly the off-flow shape this task closes')
    dead = [s for s in serves if s not in known]
    assert not dead, ('procedure ' + str(node.get('id')) + ' serves ' + ', '.join(dead) +
                      ', which is not a task id in FLOW.json. Task ids in this file: ' +
                      ', '.join(sorted(known)))
    assert any('session' in s for s in serves), ('none of ' + str(serves) + ' names a '
                                                 'session task -- the rule is a mid-session '
                                                 'instruction, so it is attached to the '
                                                 'session machinery, not to intake')


def test_the_procedure_t780_registered_survives_the_addition():
    lane = _chart().get('procedures', [])
    node = next((n for n in lane if str(n.get('id')) == 'proc_env_probe_cycle'), None)
    assert node is not None, ('the procedures lane no longer holds proc_env_probe_cycle '
                              '(installed by t780) -- adding the preferences node must not '
                              'rewrite the lane: ' +
                              ', '.join(str(n.get('id')) for n in lane))
    steps = [str(s) for s in node.get('steps', [])]
    for want in ('probe env3', 'patch env2', 're-clone env3'):
        assert want in steps, ('the env probe cycle lost its step ' + repr(want) +
                               ' -- steps on record: ' + str(steps))


def test_the_registration_is_attributable_to_this_task():
    who = str(_the_node().get('installed_by_task') or '')
    assert '1058' in who, ("installed_by_task is " + repr(who) + " -- t780's standard is "
                           'that a registration records who installed it, so a later sweep '
                           'can tell a real node from a guess')


if __name__ == '__main__':
    tests = [
        test_a_preferences_procedure_is_registered,
        test_the_procedure_addresses_the_file_it_keeps_current,
        test_the_procedure_carries_the_rule_in_its_own_words,
        test_the_procedure_meets_the_schema_t780_demands_of_procedures,
        test_the_procedure_is_attached_to_the_session_machinery,
        test_the_procedure_t780_registered_survives_the_addition,
        test_the_registration_is_attributable_to_this_task,
    ]
    failed = 0
    for fn in tests:
        try:
            fn()
        except AssertionError as exc:
            failed += 1
            print('FAIL ' + fn.__name__ + ': ' + str(exc))
        except Exception as exc:
            failed += 1
            print('ERROR ' + fn.__name__ + ': ' + type(exc).__name__ + ': ' + str(exc))
    print('ran ' + str(len(tests)) + ' test(s), ' + str(failed) + ' failed')
    raise SystemExit(1 if failed else 0)
