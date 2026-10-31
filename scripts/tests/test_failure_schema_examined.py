'''t658: examined-count (population size) must be a required report-contract field.

Fail-first oracle. It is RED on main for exactly one reason: the contract file named below has
no top-level report-contract block, so a state report may declare a clean verdict over a
population it examined nothing of, and nothing in the contract forbids it. Every other test in
this suite runs cleanly against the file as it stands today.

It reads the REAL contract file from the repository, never a fixture: a synthetic schema would
encode what we imagine the contract looks like, and a build that wrote a different shape would
pass. Read-only -- it writes no file, opens no socket, spawns nothing.
'''
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCHEMA = REPO / 'workspace' / 'failure-schema.json'

# The field this task makes mandatory, matched by EXACT key so a name like examined_count
# cannot satisfy it.
FIELD = 'examined'

# The top-level keys the live contract carries today, taken from the file itself -- a build
# that rewrites the file and drops one is a regression, not a clean change.
EXISTING_TOP_KEYS = ('_what', '_grows_by', '_cause_categories', 'scope', 'brief', 'failures')

# The seven fields a brief must carry today, by their real names in the file, as distinct
# constants -- not one repeated placeholder string the implementation can satisfy by chance.
BRIEF_REQUIRED_NAMES = ('single_change', 'what_is_wrong', 'done_when', 'frozen_oracle_ref',
                        'related_entries', 'symbols', 'behaviour_oracle')

# The four fields a scope must carry today, same reason.
SCOPE_REQUIRED_NAMES = ('goal_link', 'best_way', 'related', 'oracle')


def load_schema():
    '''Parse the live contract file. Unparseable JSON is a finding, not a skip.'''
    assert SCHEMA.is_file(), 'missing contract file: ' + str(SCHEMA)
    text = SCHEMA.read_text(encoding='utf-8')
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise AssertionError('the contract file is not valid JSON any more: ' + str(exc)) from exc
    assert isinstance(data, dict), 'the contract file must be a JSON object'
    return data


def entry_key(entry):
    '''A required entry reads <name>: <prose> -- the file's own house style. Return <name>.'''
    if not isinstance(entry, str):
        return ''
    return entry.split(':', 1)[0].strip().strip('*').strip()


def required_entries(block):
    '''The block's required list, accepting the spellings the live file uses.'''
    for key in ('required', '_required'):
        value = block.get(key)
        if isinstance(value, list):
            return value
    return []


def entry_text_for(entries, name):
    '''The full prose of the entry whose key is exactly `name`, or the empty string if none.'''
    for entry in entries:
        if entry_key(entry) == name:
            return entry
    return ''


def declares(entries, name):
    '''True when some required entry is named `name`. Tolerates the live file's own style, in
    which a line reads 'what_is_wrong and why' with no colon at all: match the leading name,
    not the whole line, so a correct build that leaves the prose alone cannot fail.'''
    for entry in entries:
        key = entry_key(entry)
        if key == name:
            return True
        if key.startswith(name + ' ') or key.startswith(name + ' and '):
            return True
    return False


def load_contract():
    '''The live contract, with the one structural precondition every test needs: the file
    parses, and its failures list is still the list the schema grew.'''
    data = load_schema()
    assert isinstance(data.get('failures'), list), 'failures[] must survive as the list the schema grew'
    return data


def test_report_contract_block_exists():
    data = load_contract()
    assert 'report-contract' in data, (
        'there is no top-level report-contract key, so a state report has no contract at all; '
        'the top-level keys present are ' + ', '.join(sorted(data))
    )
    assert isinstance(data['report-contract'], dict), (
        'the report-contract block must be a JSON object, not '
        + type(data['report-contract']).__name__
    )


def test_examined_is_a_required_field():
    data = load_contract()
    block = data.get('report-contract')
    assert isinstance(block, dict), 'report-contract block missing -- see test_report_contract_block_exists'
    entries = required_entries(block)
    assert entries, 'report-contract declares no required list, so it requires nothing'
    keys = [entry_key(e) for e in entries]
    assert FIELD in keys, (
        'the field ' + repr(FIELD) + ' is not required by the report contract; the fields it '
        'declares are ' + ', '.join(keys)
    )


def test_zero_case_is_a_distinct_state():
    '''The entry must name the zero case. A clean verdict over an empty population is a
    distinct state, not a flat clean over N -- that distinction is the whole reason the
    field exists, so an entry that only says "population size" is the shape this task is
    here to remove.'''
    data = load_contract()
    block = data.get('report-contract')
    assert isinstance(block, dict), 'report-contract block missing'
    text = entry_text_for(required_entries(block), FIELD)
    assert text, 'no required entry is named exactly ' + repr(FIELD)
    assert len(text) > len(FIELD) + 10, 'the field entry is a bare stub: ' + repr(text)
    low = text.lower()
    assert '0' in text, 'the ' + repr(FIELD) + ' entry never states the zero case: ' + repr(text)
    assert 'empty' in low, 'the ' + repr(FIELD) + ' entry never says what a 0 population means: ' + repr(text)


def test_block_carries_its_reason():
    '''A contract field with no stated reason is advice, and the next dispatch may legally
    ignore advice. Say why it exists.'''
    data = load_contract()
    block = data.get('report-contract')
    assert isinstance(block, dict), 'report-contract block missing'
    why = block.get('_why') if '_why' in block else block.get('why')
    assert isinstance(why, str) and len(why.strip()) >= 40, (
        'report-contract must say in prose why the field is required, in a why or _why key '
        'of at least 40 characters; got ' + repr(why)
    )


def test_field_is_not_buried_in_failures():
    '''NEGATIVE. Appending a failures[] entry whose class is the field name would satisfy any
    check that greps the whole file, and would enforce nothing on a future report. It is not
    the deliverable: the deliverable is a required field on the report contract.'''
    data = load_contract()
    classes = [str(row.get('class', '')) for row in data['failures'] if isinstance(row, dict)]
    assert FIELD not in classes, (
        'the field ' + repr(FIELD) + ' was added as a failures[] entry; the deliverable is a '
        'required field on the report contract, not a logged failure'
    )


def test_existing_contract_survives():
    '''This file is a LIVE contract: the scoper prompt reads scope.required and the brief
    prompt reads brief.required. A build that rewrites the file and drops a field they read
    is a regression, so every existing name is re-checked by its real name.'''
    data = load_contract()
    missing = [k for k in EXISTING_TOP_KEYS if k not in data]
    assert not missing, 'rewriting the contract dropped top-level keys: ' + ', '.join(missing)
    scope = data.get('scope')
    assert isinstance(scope, dict) and isinstance(scope.get('required'), list), 'scope.required was lost'
    lost_scope = [n for n in SCOPE_REQUIRED_NAMES if not declares(scope['required'], n)]
    assert not lost_scope, 'scope.required lost fields: ' + ', '.join(lost_scope)
    brief = data.get('brief')
    assert isinstance(brief, dict) and isinstance(brief.get('required'), list), 'brief.required was lost'
    lost = [n for n in BRIEF_REQUIRED_NAMES if not declares(brief['required'], n)]
    assert not lost, (
        'brief.required lost fields, so every future brief is under-specified: '
        + ', '.join(lost)
    )


def main():
    tests = [(name, fn) for name, fn in sorted(globals().items())
             if name.startswith('test_') and callable(fn)]
    assert tests, 'no test functions defined -- a suite with no tests proves nothing'
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print('PASS ' + name)
        except AssertionError as exc:
            failed += 1
            print('FAIL ' + name + ': ' + str(exc))
        except Exception as exc:
            failed += 1
            print('ERROR ' + name + ': ' + type(exc).__name__ + ': ' + str(exc))
    print(str(len(tests) - failed) + '/' + str(len(tests)) + ' passed')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
