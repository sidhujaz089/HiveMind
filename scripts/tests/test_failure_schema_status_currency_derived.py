import json
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SUITE = ROOT / 'scripts' / 'tests' / 'test_failure_schema_status_currency.py'
SCHEMA = ROOT / 'workspace' / 'failure-schema.json'
TIMEOUT_S = 120

# The one line in the suite that resolves its schema. The oracle repoints this at a temp JSON,
# so the copy must really read the temp file. Asserting it occurs exactly once keeps a future
# edit that moves it loud, not silent.
SCHEMA_LINE = "SCHEMA = ROOT / 'workspace' / 'failure-schema.json'"

# The class t799 built this suite to witness. Removing it from a schema copy must make the copy
# red, which proves the copy reads the temp JSON and the green in the other cases is not the real
# schema leaking through.
WITNESSED_CLASS = 'status-document-currency-drift'

# A class the schema carries today; dropping it is a defect the suite must still refuse.
EXISTING_CLASS = 'tests-failed'

# A class that exists nowhere in the real schema, appended to a copy to prove the expectation
# moves with the file. Its shape mirrors a real entry (cause_category, reason, evidence, defense).
PROBE_CLASS = 't1010-append-probe-class'
PROBE_ENTRY = {
    'class': PROBE_CLASS,
    'cause_category': 'content',
    'reason': 'appended by t1010 to prove the suite computes its class list from the schema',
    'evidence': 'workspace/failure-schema.json gained a class the test file never heard of',
    'defense': 'the suite reads the schema at test time, so a correct append cannot make it red',
}


def _suite_source():
    assert SUITE.is_file(), 'the suite under test is missing: %s' % SUITE
    text = SUITE.read_text(encoding='utf-8')
    n = text.count(SCHEMA_LINE)
    assert n == 1, ('expected exactly one occurrence of %r in the suite under test, found %d: '
                    'repointing that line at a temp schema is the whole mechanism of this oracle, '
                    'so if the suite resolves its schema path some other way, update SCHEMA_LINE '
                    'here -- do not loosen this assertion' % (SCHEMA_LINE, n))
    return text


def _schema_data():
    return json.loads(SCHEMA.read_text(encoding='utf-8'))


def _class_names(schema_obj):
    return [e.get('class') for e in schema_obj['failures']]


def _written_suite(tmp, schema_obj):
    schema_copy = Path(tmp) / 'failure-schema.json'
    schema_copy.write_text(json.dumps(schema_obj, indent=2, ensure_ascii=False) + '\n',
                           encoding='utf-8')
    source = _suite_source()
    patched = source.replace(SCHEMA_LINE, 'SCHEMA = Path(r%r)' % str(schema_copy))
    assert patched != source, 'the copied suite schema line was not repointed'
    suite_path = Path(tmp) / 'suite_under_test.py'
    suite_path.write_text(patched, encoding='utf-8')
    return suite_path


def _run(path):
    try:
        proc = subprocess.run([sys.executable, str(path)], capture_output=True, text=True,
                              timeout=TIMEOUT_S, cwd=str(Path(path).parent))
    except subprocess.TimeoutExpired as exc:
        raise AssertionError('the suite did not return within %ds: python3 %s'
                             % (TIMEOUT_S, path)) from exc
    return proc.returncode, proc.stdout + proc.stderr


def test_the_suite_goes_green_when_a_class_is_appended():
    '''RED ON MAIN: the suite keeps a frozen copy of the schema class list and asserts the schema
    EQUALS it, so a correct append of a 16th class -- the thing scripts/failure_schema.py
    append() does -- makes the suite red. Reproduced 2026-10-15: 5 passed, 1 failed, failing test
    test_every_class_in_the_schema_is_enumerated_here. That is the defect t1010 files.'''
    data = _schema_data()
    assert isinstance(data.get('failures'), list), 'the schema carries no failures list to append to'
    assert PROBE_CLASS not in _class_names(data), ('the real schema already carries %r; pick a '
                                                   'class name it does not have' % PROBE_CLASS)
    data['failures'].append(dict(PROBE_ENTRY))
    with tempfile.TemporaryDirectory() as tmp:
        code, out = _run(_written_suite(tmp, data))
    assert code == 0, ('the suite went RED when a 16th class was appended to a copy of the schema. '
                       'The expectation is stored as a frozen list instead of computed from the '
                       'file, so every future append to workspace/failure-schema.json is counted as '
                       'a new sandbox regression. Suite output:\n%s' % out)
    assert '6 passed, 0 failed' in out, ('expected 6 passed, 0 failed from the rewritten suite: the '
                                         'four t799 assertions by name, the anti-drop floor, and one '
                                         'test that every schema class is filed in a form the routing '
                                         'rule can act on. Deleting assertions to pass this oracle is '
                                         'the refusal this assertion exists for. Suite output:\n%s' % out)


def test_the_suite_still_goes_red_when_a_class_is_dropped():
    '''Anti-drop: a class the schema has carried must still be refused if it disappears. Passes on
    main and after the fix, so the rewrite cannot be to delete the enumeration tests.'''
    data = _schema_data()
    kept = [e for e in data['failures'] if e.get('class') != EXISTING_CLASS]
    assert len(kept) == len(data['failures']) - 1, ('the schema no longer carries %r; name a class '
                                                     'it does carry' % EXISTING_CLASS)
    data['failures'] = kept
    with tempfile.TemporaryDirectory() as tmp:
        code, out = _run(_written_suite(tmp, data))
    assert code != 0, ('the suite stayed GREEN with %r removed from the schema copy: the anti-drop '
                       'guarantee is gone. Suite output:\n%s' % (EXISTING_CLASS, out))


def test_the_repointed_copy_actually_reads_the_temp_schema():
    '''Guard against a vacuous green: with the witnessed class removed from the temp schema the
    copy must go red, proving the repoint wires it to the temp JSON and not the real schema.'''
    data = _schema_data()
    kept = [e for e in data['failures'] if e.get('class') != WITNESSED_CLASS]
    assert len(kept) == len(data['failures']) - 1, 'the schema no longer carries %r' % WITNESSED_CLASS
    data['failures'] = kept
    with tempfile.TemporaryDirectory() as tmp:
        code, out = _run(_written_suite(tmp, data))
    assert code != 0, ('the copied suite stayed green with %r removed from its schema, so it is not '
                       'reading the temp schema and the green in the other cases would prove '
                       'nothing. Suite output:\n%s' % (WITNESSED_CLASS, out))


def test_the_real_suite_is_green_against_the_real_schema():
    '''t799 survives the rewrite: the suite on disk, unmodified, passes against the real schema.
    6 passed, 0 failed on main; must keep passing, so every t799 assertion is kept by name.'''
    code, out = _run(SUITE)
    assert code == 0, 'the real suite is red against the real schema:\n%s' % out
    assert '6 passed, 0 failed' in out, ('expected 6 passed, 0 failed from the real suite; the '
                                         'rewrite must keep all of the t799 assertions by name. '
                                         'Suite output:\n%s' % out)


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
