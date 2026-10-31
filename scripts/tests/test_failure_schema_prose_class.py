#!/usr/bin/env python3
"""Oracle: workspace/failure-schema.json declares the prose-defect class (t733).

RED ON MAIN for ONE reason: the schema has no class named 'defect-cited-without-disposition'.
Verified on 2026-10-11: grep -n 'disposition' workspace/failure-schema.json returns ZERO lines,
so there is no existing class to extend -- which is the first step this task's checklist asked
for ('grep failure-schema.json for an existing class and extend it rather than mint a second').
The failures key is a LIST of 14 rows keyed by a 'class' field: read from the real file, and
confirmed in scripts/failure_schema.py:83, which looks a row up by r.get('class').

Nothing here fails for any other reason: the file is parsed first (a trailing comma is reported
as 'not valid JSON', never as a KeyError), no module is imported, no temp dir is created, no
subprocess is started, no model is called, nothing is written.

WHY A JSON ENTRY NEEDS A TEST AT ALL. The class was named on 2026-10-01 in
workspace/rca/2bb1091df460.md; its carrying tasks t515/t497-t501 were closed as prose and the
class regrew within hours (workspace/rca/d7f6cd55e4b4.md). The task text itself says the fix is
a schema field, because prose has no next actor. A class that no command can fail on is
advertising. This suite is that command, and it is the entry's decay test: rename it, delete it,
file it under a cause category the schema cannot route, narrow the four dispositions, empty the
vocabulary, or make a disposition that can be written empty, and this goes red.

WHAT EACH ASSERTION BUYS -- the checks a person does by hand, in this order:
  * it is keyed by the exact id the RCA named, and declared EXACTLY ONCE: the appender appends
    instances to the one row for a class and never mints a second, so two rows is the defect.
  * the entry is a record with a real reason and real evidence, not a label.
  * cause_category is one of the keys _cause_categories declares -- the file's own _why says a
    taxonomy with a missing row produces a misfiling, not a gap.
  * required_fields carries 'disposition': that field is what makes a bare citation incomplete.
  * dispositions is EXACTLY the four the RCA named, each carrying its payload, compared by value
    (a substring check passes a broad-enough entry -- the oracle-weak shape in t471/t297/t370).
  * the trigger vocabulary is the state-adjective set the RCAs named, and 'fixed' is not in it:
    'fixed' is a disposition word, so a scanner that greps for it reports the fix as the defect.
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = ROOT / 'workspace' / 'failure-schema.json'
CLASS = 'defect-cited-without-disposition'

# The four dispositions, verbatim from the task's CHANGE line: {task: tNNN} | {covered_by: tNNN}
# | {declined: reason} | {declared_open: boundary + close condition}.
EXPECTED_DISPOSITIONS = {
    'task': ['task'],
    'covered_by': ['task'],
    'declined': ['reason'],
    'declared_open': ['boundary', 'close_condition'],
}

# Seed vocabulary: the state-adjective class named in workspace/rca/0f167ec334ff.md (unmerged,
# undiagnosed, unreviewed, unowned, untriaged, uncommitted, undisposed), the dead-phrase class
# from workspace/rca/1a7761b3791e.md (didn't fire, did not apply, no-op), and the field cases
# quoted on this task: 'unhealthy' (d7f6cd55e4b4), 'uninstalled' (39cc3df02e11), 'is
# unavailable' (31e21653e2e6), 'there is no' (0d7944d8eee3), 'should probably' (5d889eec288f).
EXPECTED_VOCABULARY = {
    'unmerged', 'undiagnosed', 'unreviewed', 'unowned', 'untriaged', 'uncommitted',
    'undisposed', 'unaddressed', 'unhealthy', 'uninstalled', 'not patched',
    'not recorded', 'no collector', 'no mechanism', 'no owner', 'no closure path',
    "didn't fire", 'did not apply', 'no-op', 'should probably', 'is unavailable',
    'there is no', 'left open', 'left alone',
}

FAILURES = []
_CACHE = []


def load_schema():
    """Parse the real schema once. Returns None and records the failure if it does not parse."""
    for entry in _CACHE:
        return entry[0]
    try:
        data = json.loads(SCHEMA.read_text(encoding='utf-8'))
    except Exception as exc:
        _CACHE.append((None, repr(exc)))
        return None
    _CACHE.append((data, None))
    return data


def parse_error():
    load_schema()
    return _CACHE[0][1] if _CACHE else None


def the_row():
    data = load_schema()
    rows = data.get('failures') if isinstance(data, dict) else None
    if not isinstance(rows, list):
        return None
    matches = [r for r in rows if isinstance(r, dict) and r.get('class') == CLASS]
    return matches[0] if matches else None


def check(name, ok, detail=''):
    print('  ' + ('ok   ' if ok else 'FAIL ') + name + ('' if ok else '   ' + str(detail)))
    if not ok:
        FAILURES.append(name)


def test_the_schema_file_parses_as_json():
    err = parse_error()
    check('workspace/failure-schema.json parses as JSON', err is None, err)
    if err is not None:
        return
    data = load_schema()
    check('it has a failures list of class rows', isinstance(data.get('failures'), list),
          type(data.get('failures')).__name__)


def test_the_class_is_declared_exactly_once():
    data = load_schema()
    rows = data.get('failures') if isinstance(data, dict) else None
    if not isinstance(rows, list):
        check('a class named ' + CLASS + ' is declared', False, 'failures is not a list')
        return
    matches = [r for r in rows if isinstance(r, dict) and r.get('class') == CLASS]
    present = ', '.join(sorted(str(r.get('class')) for r in rows if isinstance(r, dict)))
    check('a class named ' + CLASS + ' is declared', len(matches) >= 1, 'present: ' + present)
    check('it is declared exactly once (the appender keys rows by class)', len(matches) == 1,
          'matches: ' + str(len(matches)))


def test_the_entry_is_a_record_not_a_label():
    row = the_row()
    check('the entry is an object, not a string label', isinstance(row, dict), type(row).__name__)
    if not isinstance(row, dict):
        return
    reason = row.get('reason')
    check('it carries a reason that says why, naming prose',
          isinstance(reason, str) and 'prose' in reason.lower() and len(reason.strip()) > 40,
          repr(reason)[:300])
    check('it carries evidence a reader ten scopes away can check',
          isinstance(row.get('evidence'), str) and len(row['evidence'].strip()) > 20,
          repr(row.get('evidence'))[:300])


def test_the_cause_category_is_one_the_schema_can_route():
    data = load_schema()
    table = (data.get('_cause_categories') if isinstance(data, dict) else None) or {}
    cats = set(str(k) for k in table if not str(k).startswith('_'))
    check('_cause_categories declares the routing categories', len(cats) >= 2, sorted(cats))
    row = the_row()
    if not isinstance(row, dict):
        check('the class is filed under one of them', False, 'no class row to file')
        return
    check('the class is filed under one of them', row.get('cause_category') in cats,
          'cause_category=' + repr(row.get('cause_category')) + ' legal=' + repr(sorted(cats)))


def test_the_record_requires_the_disposition_field():
    row = the_row()
    if not isinstance(row, dict):
        check("required_fields carries 'disposition'", False, 'no class row')
        return
    req = row.get('required_fields')
    check("required_fields is a list containing 'disposition'",
          isinstance(req, list) and 'disposition' in req, repr(req)[:200])


def test_the_four_dispositions_are_named_exactly():
    row = the_row()
    if not isinstance(row, dict):
        check('dispositions names the four dispositions', False, 'no class row')
        return
    got = row.get('dispositions')
    check('dispositions is exactly the four the RCA named, with their payloads',
          got == EXPECTED_DISPOSITIONS, repr(got)[:400])
    if isinstance(got, dict):
        check('no disposition can be written empty (every one names a payload field)',
              all(isinstance(v, list) and len(v) >= 1
                  and all(isinstance(x, str) and x.strip() for x in v)
                  for v in got.values()), repr(got)[:400])
        check("'unresolved' is not offered as a disposition", 'unresolved' not in got, sorted(got))


def test_the_trigger_vocabulary_is_the_state_adjective_set():
    row = the_row()
    if not isinstance(row, dict):
        check('trigger_vocabulary is declared', False, 'no class row')
        return
    vocab = row.get('trigger_vocabulary')
    if not isinstance(vocab, list):
        check('trigger_vocabulary is a list of phrases', False, repr(vocab)[:200])
        return
    check('it is the defect-word set the RCAs named', set(vocab) == EXPECTED_VOCABULARY,
          'missing=' + repr(sorted(EXPECTED_VOCABULARY - set(vocab))) +
          ' extra=' + repr(sorted(set(vocab) - EXPECTED_VOCABULARY)))
    check('every trigger is a non-empty lowercase phrase',
          all(isinstance(t, str) and t.strip() and t == t.lower() for t in vocab),
          repr([t for t in vocab if not isinstance(t, str) or t != t.lower()])[:200])
    check("'fixed' is a disposition word, not a defect trigger", 'fixed' not in vocab, sorted(vocab))


def test_the_defense_names_the_command_that_enforces_it():
    row = the_row()
    if not isinstance(row, dict):
        check('the entry names the command that fails on a bare citation', False, 'no class row')
        return
    defense = row.get('defense')
    check('the defense names the scanner by path, so the class has a next actor',
          isinstance(defense, str) and 'failure_schema.py' in defense, repr(defense)[:300])


if __name__ == '__main__':
    tests = [
        test_the_schema_file_parses_as_json,
        test_the_class_is_declared_exactly_once,
        test_the_entry_is_a_record_not_a_label,
        test_the_cause_category_is_one_the_schema_can_route,
        test_the_record_requires_the_disposition_field,
        test_the_four_dispositions_are_named_exactly,
        test_the_trigger_vocabulary_is_the_state_adjective_set,
        test_the_defense_names_the_command_that_enforces_it,
    ]
    for fn in tests:
        print(fn.__name__)
        fn()
    print('failures: ' + str(len(FAILURES)) + ' of ' + str(len(tests)) + ' tests')
    for name in FAILURES:
        print('  - ' + name)
    raise SystemExit(1 if FAILURES else 0)
