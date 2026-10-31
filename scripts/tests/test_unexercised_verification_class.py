#!/usr/bin/env python3
"""t801 -- failure-schema.json must carry a cause class for a verification that produced a
false green: a claim a writer can make in prose with no field naming the path the run
exercised, the run count, the population it opened, the exit code, or that it completed.

Red on main: workspace/failure-schema.json's failures array has 13 classes and none of them
is 'unexercised-verification' (verified by reading the file end to end: 459 lines, the last
entry 'tests-failed' opens at line 425 and closes at 457, ']' at 458, '}' at 459; a repo-wide
search for the string 'unexercised-verification' hits only briefs, RCAs, tasks, manager-queue
logs and task-sessions, never a file under scripts/). So the sentence this class exists to
make nameable -- 'I said 279 before and after -> 0 new from a single-run test that never
exercised the mutated path' (workspace/rca/25883a460549.md; findings/25883a460549.json:6,8,
covered_by '') -- has nowhere to be filed, and three task-store searches found no task on the
topic.

The field list is the UNION of the three session reviews that produced this task, so ONE
append settles the family and the queued t852 extends this class with fields instead of
minting a near-twin class:
  t801 checklist   mutated_path, metric, command, runs, fail_before, pass_after, exercised_paths
  rca/696139090a04 exit_code, budget_s, budget_source, completed  (a 'timeout 280' run that
      died with exit 143 and printed nothing passes a field list that only records what a run
      exercised)
  rca/6bc783739c45 examined_population, claim_scope (a negative claim over a population the
      writer never opened)
  t852             verification_quote, population, blocked_by, lands_via

Run: python3 scripts/tests/test_unexercised_verification_class.py
"""
import json
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = ROOT / 'workspace' / 'failure-schema.json'

NEW_CLASS = 'unexercised-verification'
CAUSE_CATEGORY = 'content'

# The 17 names the three RCAs and t852 asked for, in the order a reader meets them: what was
# mutated, what was measured, how it was run, how many times, what it showed before and after,
# what the run exercised, whether the command finished and on what budget, what population was
# opened, what the claim covers, and where the verification landed.
REQUIRED_FIELDS = [
    'mutated_path', 'metric', 'command', 'runs', 'fail_before', 'pass_after',
    'exercised_paths', 'exit_code', 'budget_s', 'budget_source', 'completed',
    'examined_population', 'claim_scope', 'verification_quote', 'population',
    'blocked_by', 'lands_via',
]

# The 13 classes on disk today, read off workspace/failure-schema.json this session. The schema
# is append-only (its own _grows_by contract), so a build that drops one is a regression.
EXISTING_CLASSES = [
    'preempted-by-priority-layer', 'baseline-refusal-undeclared',
    'remote-disconnected-external-restart', 'concurrent-dispatch-starvation',
    'harness-edit-application', 'swallowed-exception-introduced',
    'oracle-blocked-by-its-own-gate', 'gate-after-acceptance', 'window-full',
    'check-satisfied-by-dead-path', 'unclassified', 'oracle-weak', 'tests-failed',
]

# Real artefacts, read this session, not invented fixtures.
FINDING_ID = '25883a460549'
CLAIM_PHRASE = '279 before and after'

# The anti-gaming pair. A required-field list made of prose a writer types is satisfied by
# typing -- which is exactly how '279 before and after -> 0 new' passed the check it was
# checked against. Every required field of this class names a fact a command produces, so none
# of these names may appear in it.
PROSE_ONLY_FIELDS = ('notes', 'comment', 'summary', 'text', 'reason', 'evidence', 'defense')


def _entries():
    assert SCHEMA.is_file(), 'missing file: %s' % SCHEMA
    data = json.loads(SCHEMA.read_text(encoding='utf-8'))
    assert isinstance(data, dict), 'failure-schema.json did not parse to one JSON object'
    assert isinstance(data.get('failures'), list), 'the top-level "failures" list is missing'
    return data['failures']


def _matching(cls):
    return [e for e in _entries() if e.get('class') == cls]


def test_the_class_is_filed_exactly_once():
    found = _matching(NEW_CLASS)
    assert len(found) == 1, (
        'expected exactly one failure entry with class %r, found %d -- on main the schema has '
        'no such class, which is the defect this task adds' % (NEW_CLASS, len(found)))


def test_the_class_is_filed_under_a_category_the_routing_rule_can_act_on():
    found = _matching(NEW_CLASS)
    assert len(found) == 1, 'no %r entry to read a cause_category from' % NEW_CLASS
    category = found[0].get('cause_category')
    assert category == CAUSE_CATEGORY, (
        'cause_category is %r; a verification that produced a false green is a %r fault -- '
        'the category whose routing rule says note the reason, front the task and re-scope'
        % (category, CAUSE_CATEGORY))
    categories = json.loads(SCHEMA.read_text(encoding='utf-8')).get('_cause_categories', {})
    assert category in categories, (
        'cause_category %r is not a key of _cause_categories, so no routing rule acts on it'
        % (category,))
    assert category != 'unclassified', (
        'a new cause class filed under a category with no routing rule is a misfiling: the '
        'class exists precisely so the defect has somewhere to go')


def test_the_class_requires_the_fields_a_verification_claim_must_carry():
    found = _matching(NEW_CLASS)
    assert len(found) == 1, 'no %r entry to read required_fields from' % NEW_CLASS
    fields = found[0].get('required_fields')
    assert isinstance(fields, list), 'required_fields must be a list, got %r' % (fields,)
    assert all(isinstance(x, str) and x.strip() for x in fields), (
        'every required field must be a non-empty string name, got %r' % (fields,))
    assert len(fields) == len(set(fields)), 'required_fields has duplicates: %r' % (fields,)
    missing = [f for f in REQUIRED_FIELDS if f not in fields]
    assert not missing, (
        'required_fields is missing %s. The class exists to make "279 before and after -> 0 '
        'new" unstateable in prose: mutated_path / metric / command / runs / fail_before / '
        'pass_after / exercised_paths name the run; exit_code / budget_s / budget_source / '
        'completed (added by rca/696139090a04 -- a run killed by "timeout 280" that printed '
        'nothing) name that the command finished; examined_population / claim_scope (added by '
        'rca/6bc783739c45) name what was opened and what the claim covers; '
        'verification_quote / population / blocked_by / lands_via (added by t852) name what was '
        'claimed and where it landed. A subset of that set leaves the sentence stateable. The '
        'check is a SUPERSET, not equality, so t852 can extend this class with fields.' % missing)
    prose = [f for f in fields if f in PROSE_ONLY_FIELDS]
    assert not prose, (
        'required_fields carries prose-only fields %s: a class whose required fields are notes '
        'a writer types is satisfied by typing, which is how a single-run test that never '
        'exercised the mutated path passed as verification' % (prose,))


def test_the_class_names_the_finding_it_was_born_from():
    found = _matching(NEW_CLASS)
    assert len(found) == 1, 'no %r entry to read prose from' % NEW_CLASS
    entry = found[0]
    for key in ('reason', 'evidence', 'defense'):
        text = entry.get(key)
        assert isinstance(text, str) and text.strip(), (
            '%s.%s must be a non-empty string: an entry whose %s is empty is a label, not a '
            'defense' % (NEW_CLASS, key, key))
    joined = ' '.join(str(entry.get(k, '')) for k in ('reason', 'evidence', 'defense'))
    assert FINDING_ID in joined, (
        'the entry must name the finding that produced this class (rca/%s), so the class is a '
        'filed fact about a real artefact and not a generality' % FINDING_ID)
    assert CLAIM_PHRASE in joined, (
        'the entry must quote the claim shape the class exists to stop (the phrase %r from '
        'workspace/rca/%s.md); a class that does not name the sentence it was born from will '
        'not be recognised when the next one is written' % (CLAIM_PHRASE, FINDING_ID))


def test_the_class_is_filed_against_the_artefacts_that_proved_it_was_needed():
    found = _matching(NEW_CLASS)
    assert len(found) == 1, 'no %r entry to read instances from' % NEW_CLASS
    instances = found[0].get('instances')
    assert isinstance(instances, list) and len(instances) >= 2, (
        'the class must be filed against at least the two RCAs that asked for it '
        '(rca/25883a460549.md and rca/696139090a04.md); instances is %r' % (instances,))
    seen = []
    for inst in instances:
        for key in ('at', 'chunk', 'evidence'):
            val = inst.get(key)
            assert isinstance(val, str) and val.strip(), (
                'an instance row needs non-empty at / chunk / evidence -- the shape every '
                'class in this schema uses (see the oracle-weak entry, lines 402-417): %r'
                % (inst,))
        seen.append(inst['chunk'])
    assert FINDING_ID in ' '.join(seen), (
        'the instances must name rca/%s -- the finding whose sentence is the defect' % FINDING_ID)


def test_the_append_kept_every_class_the_schema_already_had():
    in_schema = set(e.get('class') for e in _entries())
    lost = sorted(set(EXISTING_CLASSES) - in_schema)
    assert not lost, (
        'the append dropped existing failure classes: %s -- the schema is append-only '
        '(_grows_by), so dropping one is a regression, not a tidy-up' % (lost,))


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
