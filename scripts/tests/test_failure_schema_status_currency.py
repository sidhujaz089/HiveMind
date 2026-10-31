#!/usr/bin/env python3
"""t799 -- failure-schema.json must carry a cause class for a standing document's own
currency claim drifting from the artefact it describes.

The schema carries the class. The suite also guards that every class the schema carries is
filed in a form the routing rule can act on, and that no class the schema has carried
disappears. The class list is computed from the file at test time, never stored as a
snapshot -- a stored snapshot turns every future append into a false new regression (t1010).

Run: python3 scripts/tests/test_failure_schema_status_currency.py
"""
import json
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = ROOT / 'workspace' / 'failure-schema.json'

NEW_CLASS = 'status-document-currency-drift'
CAUSE_CATEGORY = 'content'
REQUIRED_FIELDS = ['artifact', 'claimed_as_of', 'newest_change_at', 'evidence']
ARTIFACT_NAMED = 'STATE.md'

# BASE_CLASSES is the FLOOR: the classes that must never disappear from the schema. The
# schema file is the source of truth for the full list -- a class added to the schema is
# accepted (checked for a duplicate name), not rejected by membership of a frozen list, so
# appending a class does not make this suite red.
BASE_CLASSES = frozenset([
    'preempted-by-priority-layer',
    'baseline-refusal-undeclared',
    'remote-disconnected-external-restart',
    'concurrent-dispatch-starvation',
    'harness-edit-application',
    'swallowed-exception-introduced',
    'oracle-blocked-by-its-own-gate',
    'gate-after-acceptance',
    'window-full',
    'check-satisfied-by-dead-path',
    'unclassified',
    'oracle-weak',
    'tests-failed',
    NEW_CLASS,
    'bad-shape-unbound-or-removed',   # in the schema since t1072's code_shape gate; enumerated 2026-10-14 (t799)
])


def _entries():
    data = json.loads(SCHEMA.read_text(encoding='utf-8'))
    assert isinstance(data, dict), 'failure-schema.json did not parse to an object'
    assert isinstance(data.get('failures'), list), 'failures is missing or is not a list'
    return data['failures']


def _matching(cls):
    return [e for e in _entries() if e.get('class') == cls]


def _class_names():
    return set(e.get('class') for e in _entries())


def test_the_new_class_is_filed_exactly_once():
    found = _matching(NEW_CLASS)
    assert len(found) == 1, (
        'expected exactly one failure entry with class %r, found %d'
        % (NEW_CLASS, len(found)))


def test_the_class_is_filed_under_a_category_the_routing_rule_can_act_on():
    found = _matching(NEW_CLASS)
    assert len(found) == 1, 'no %r entry to read a cause_category from' % NEW_CLASS
    category = found[0].get('cause_category')
    assert category == CAUSE_CATEGORY, (
        'cause_category is %r; a standing doc going stale is a %r fault -- the category '
        'whose routing rule says note the reason, front the task and re-scope -- not a '
        'label' % (category, CAUSE_CATEGORY))
    categories = json.loads(SCHEMA.read_text(encoding='utf-8')).get('_cause_categories', {})
    assert category in categories, (
        'cause_category %r is not a key of _cause_categories, so no routing rule acts on '
        'it' % category)
    assert category != 'unclassified', (
        'a new cause class filed unclassified is a misfiling: the routing rule must be named')


def test_the_class_declares_the_fields_a_report_of_it_must_carry():
    found = _matching(NEW_CLASS)
    assert len(found) == 1, 'no %r entry to read required_fields from' % NEW_CLASS
    fields = found[0].get('required_fields')
    assert fields == REQUIRED_FIELDS, (
        'required_fields is %r; expected exactly %r -- the standing doc that drifted, the '
        'date its header claims, the date its content last moved, and the commit that shows '
        'it' % (fields, REQUIRED_FIELDS))


def test_the_class_is_tied_to_the_artefact_that_proved_it_was_needed():
    found = _matching(NEW_CLASS)
    assert len(found) == 1, 'no %r entry to read prose from' % NEW_CLASS
    entry = found[0]
    for key in ('reason', 'evidence', 'defense'):
        text = entry.get(key)
        assert isinstance(text, str) and text.strip(), (
            '%s.%s must be a non-empty string: an entry whose %s is empty is a label, not '
            'a defense' % (NEW_CLASS, key, key))
    joined = ' '.join(str(entry.get(k, '')) for k in ('reason', 'evidence', 'defense'))
    assert ARTIFACT_NAMED in joined, (
        'the entry must name the standing doc the class was born from (%s), so the class is '
        'a filed fact about a real artefact and not a generality' % ARTIFACT_NAMED)


def test_the_append_kept_every_class_the_schema_already_had():
    in_schema = _class_names()
    lost = BASE_CLASSES - in_schema
    assert not lost, 'the append dropped existing failure classes: %s' % sorted(lost)


def test_every_class_in_the_schema_is_filed_in_a_form_the_routing_rule_can_act_on():
    names = [e.get('class') for e in _entries()]
    assert len(names) == len(set(names)), (
        'duplicate failure class in the schema: %s'
        % sorted(c for c in set(names) if names.count(c) > 1))
    assert BASE_CLASSES <= set(names), (
        'schema lost a base failure class: %s'
        % sorted(BASE_CLASSES - set(names)))


if __name__ == '__main__':
    names = sorted(k for k, v in list(globals().items())
                   if k.startswith('test_') and callable(v))
    failed = 0
    for name in names:
        try:
            globals()[name]()
            print('ok   %s' % name)
        except Exception:
            failed += 1
            print('FAIL %s' % name)
            traceback.print_exc()
    print('%d passed, %d failed' % (len(names) - failed, failed))
    sys.exit(1 if failed else 0)
