#!/usr/bin/env python3
"""Oracle: scripts/failure_schema.py scans prose for a defect cited with no disposition (t733).

RED ON MAIN for ONE reason: the module has no DEFAULT_VOCABULARY, no defect_vocabulary(), no
find_undispositioned() and no main(). Verified by reading scripts/failure_schema.py (241 lines)
on 2026-10-11: it is the schema APPENDER -- classify(), append(), record_failure(), validate_row()
and a __main__ that prints classify(stdin). The import below is a sys.path insert of scripts/,
which works on main, so nothing here is red for a reason other than the missing change.

WHY THIS IS THE DURABLE HALF. The class was named on 2026-10-01 in workspace/rca/2bb1091df460.md:5
and the finding that named it was quoted verbatim in the RCA this task comes from: 'the only
container up is open-webui (unhealthy, 40 hours).' -- a live defect cited as evidence for another
claim, in prose, with nothing on disk that makes that sentence incomplete. Twenty-five RCAs on
this task name the same shape. Prose has no next actor, so the citation is a legal terminating act.

THE CONTRACT PINNED HERE, and why a person checks it by hand:
  * a line carrying a defect word and no disposition is reported, with the WHOLE line as the
    quote (never a prefix -- never-truncate-a-record), its 1-based number, and the word that fired.
  * the unit is the LINE, because that is how a summary bullet is written and read.
  * a disposition must be one of the four the schema names. 'unresolved' is NOT one of them:
    scripts/tests/test_session_review_undispositioned.py records that the field inside the
    finding file has been saying 'unresolved' for nine days, so counting it ratifies the defect
    this task exists to make illegal.
  * a task number is 't' plus two to four digits -- 'see t7' is not a disposition.
  * 'declined' must carry a reason and 'declared_open' must carry its close condition. A bare
    marker is the bare citation wearing the word.
  * the vocabulary is READ FROM THE SCHEMA at run time, not baked into the scanner. That is the
    sustainment: the word list lives in the artefact that records the class, so a future scope
    that adds a defect word changes a data file, and a schema that loses the entry stops the
    scanner from inventing its own list in private.
  * a missing input file is named on stderr and exits 2. Silence is not a verdict.
No model call, no subprocess, no network, no fixed /tmp path (tempfile.mkdtemp only), every temp
dir removed in a finally, and the live workspace tree is never written.
"""
import contextlib
import io
import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))

try:
    import failure_schema as fs
    _IMPORT = None
except BaseException as exc:  # noqa: BLE001 -- a missing module is reported, never a traceback
    fs = None
    _IMPORT = repr(exc)

FAILURES = []

# The same set the schema entry is pinned to by scripts/tests/test_failure_schema_prose_class.py.
# Both suites pin this literal on purpose: the schema entry and the module fallback must agree,
# and the only way to keep a cross-file agreement testable while the two halves are built
# separately is to state the shared set in the tests.
EXPECTED_VOCABULARY = {
    'unmerged', 'undiagnosed', 'unreviewed', 'unowned', 'untriaged', 'uncommitted',
    'undisposed', 'unaddressed', 'unhealthy', 'uninstalled', 'not patched',
    'not recorded', 'no collector', 'no mechanism', 'no owner', 'no closure path',
    "didn't fire", 'did not apply', 'no-op', 'should probably', 'is unavailable',
    'there is no', 'left open', 'left alone',
}

# The verbatim finding that minted this class (workspace/rca/d7f6cd55e4b4.md).
UNHEALTHY = 'the only container up is open-webui (unhealthy, 40 hours).'
DETECTOR = "the detector didn't fire -- covered_by t613."


def check(name, ok, detail=''):
    print('  ' + ('ok   ' if ok else 'FAIL ') + name + ('' if ok else '   ' + str(detail)))
    if not ok:
        FAILURES.append(name)


def ready():
    check('scripts/failure_schema.py imports', fs is not None, _IMPORT)
    return fs is not None


def scan(lines, **kw):
    if fs is None:
        return None
    fn = getattr(fs, 'find_undispositioned', None)
    if not callable(fn):
        return None
    return fn(lines, **kw)


def call_main(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = fs.main(argv)
    return rc, out.getvalue(), err.getvalue()


def test_the_module_exposes_the_scanner_api():
    if not ready():
        return
    for name in ('DEFAULT_VOCABULARY', 'defect_vocabulary', 'find_undispositioned', 'main'):
        check('failure_schema.' + name + ' exists', hasattr(fs, name),
              'names present: ' + repr([n for n in dir(fs) if not n.startswith('_')])[:600])


def test_a_defect_cited_in_prose_with_no_disposition_is_a_finding():
    if not ready():
        return
    res = scan([UNHEALTHY])
    if res is None:
        check('a defect cited in prose with no disposition is reported', False,
              'no callable find_undispositioned()')
        return
    check('a defect cited in prose with no disposition is reported', len(res) == 1, repr(res)[:400])
    if not res:
        return
    row = res[0]
    check('the row names the vocabulary word that fired', row.get('trigger') == 'unhealthy', repr(row)[:300])
    check('the row says there is no disposition on the line', row.get('disposition') is None, repr(row)[:300])
    check('the quote is the WHOLE line, never a slice of it', row.get('quote') == UNHEALTHY,
          repr(row.get('quote'))[:400])
    check('the row is 1-based so a reader can open the file at it', row.get('line') == 1, repr(row)[:300])
    check('no disposition is invented for an empty input', scan([]) == [], repr(scan([]))[:200])


def test_the_state_adjective_class_is_recognised():
    if not ready():
        return
    res = scan(['14 branches unmerged.'])
    if res is None:
        check('a bare state adjective is a defect', False, 'no callable find_undispositioned()')
        return
    check('a bare state adjective is a defect (rca 0f167ec334ff)', len(res) == 1, repr(res)[:400])
    if res:
        check('and it names the adjective', res[0].get('trigger') == 'unmerged', repr(res[0])[:300])


def test_the_same_sentence_with_a_task_number_is_not_a_finding():
    if not ready():
        return
    res = scan([UNHEALTHY + ', see t731'])
    if res is None:
        check('a citation carrying tNNN is not a finding', False, 'no callable find_undispositioned()')
        return
    check('a citation carrying tNNN is not a finding', res == [], repr(res)[:400])


def test_a_closed_task_can_be_the_disposition():
    if not ready():
        return
    res = scan([DETECTOR])
    if res is None:
        check('covered_by a closed task dispositions the sentence', False, 'no find_undispositioned()')
        return
    check('covered_by a closed task dispositions the sentence', res == [], repr(res)[:400])


def test_a_short_token_is_not_a_task_number():
    if not ready():
        return
    res = scan(['the open-webui container is unhealthy -- see t7'])
    if res is None:
        check('tNNN means two to four digits: see t7 is not a disposition', False,
              'no find_undispositioned()')
        return
    check('tNNN means two to four digits: see t7 is not a disposition', len(res) == 1, repr(res)[:400])


def test_unresolved_is_not_a_disposition():
    if not ready():
        return
    res = scan(['the open-webui container is unhealthy -- disposition: unresolved'])
    if res is None:
        check('unresolved is not a disposition', False, 'no find_undispositioned()')
        return
    check('unresolved is not a disposition -- it is the word that has been lying for days',
          len(res) == 1, repr(res)[:400])


def test_declined_must_carry_a_reason():
    if not ready():
        return
    ok = scan(['the command checker is unavailable -- declined: out of scope for this session'])
    empty = scan(['the command checker is unavailable -- declined:'])
    if ok is None or empty is None:
        check('declined with a reason dispositions the line', False, 'no find_undispositioned()')
        check('declined with an empty payload is not a disposition', False, 'no find_undispositioned()')
        return
    check('declined with a reason dispositions the line', ok == [], repr(ok)[:400])
    check('declined with an empty payload is not a disposition', len(empty) == 1, repr(empty)[:400])


def test_declared_open_must_carry_its_close_condition():
    if not ready():
        return
    ok = scan(['open-webui is uninstalled -- declared_open: no collector records container health; '
               'close condition: scripts/check_containers.py prints a row for it'])
    bad = scan(['open-webui is uninstalled -- declared_open: no collector records container health'])
    if ok is None or bad is None:
        check('declared_open with its close condition dispositions the line', False,
              'no find_undispositioned()')
        check('declared_open with no close condition is not a disposition', False,
              'no find_undispositioned()')
        return
    check('declared_open with its close condition dispositions the line', ok == [], repr(ok)[:400])
    check('declared_open with no close condition is not a disposition', len(bad) == 1, repr(bad)[:400])


def test_ordinary_report_prose_is_quiet():
    if not ready():
        return
    lines = [
        'The suite is green: 39 passed, 0 failed.',
        'Fixed the sys.path in the oracle and committed it.',
        'Merged p33 into main.',
        'Rebased the branch onto main and pushed.',
    ]
    res = scan(lines)
    if res is None:
        check('ordinary report language produces no finding', False, 'no find_undispositioned()')
        return
    check('ordinary report language produces no finding', res == [], repr(res)[:500])


def test_the_vocabulary_comes_from_the_schema_and_not_from_the_scanner():
    if not ready():
        return
    dv = getattr(fs, 'defect_vocabulary', None)
    if not callable(dv) or not callable(getattr(fs, 'find_undispositioned', None)):
        check('defect_vocabulary(path) reads the schema row', False, 'no defect_vocabulary()')
        check('a word the schema adds becomes a trigger', False, 'no defect_vocabulary()')
        return
    tmp = tempfile.mkdtemp(prefix='t733-vocab-')
    try:
        row = {'class': 'defect-cited-without-disposition',
               'trigger_vocabulary': ['frobnicated', 'unmerged']}
        path = Path(tmp) / 'failure-schema.json'
        path.write_text(json.dumps({'failures': [row]}, indent=2) + '\n', encoding='utf-8')
        vocab = dv(path)
        check('defect_vocabulary(path) returns the schema row, not the default',
              vocab is not None and set(vocab) == {'frobnicated', 'unmerged'}, repr(vocab)[:300])
        res = scan(['the widget is frobnicated', 'the container is unhealthy'], schema_path=path)
        check('a word the schema adds fires, and a word the schema drops stops firing',
              len(res) == 1 and res[0].get('trigger') == 'frobnicated', repr(res)[:400])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_the_module_default_is_the_documented_set():
    if not ready():
        return
    default = getattr(fs, 'DEFAULT_VOCABULARY', None)
    check('DEFAULT_VOCABULARY is exactly the set the RCAs named',
          default is not None and set(default) == EXPECTED_VOCABULARY,
          'missing=' + repr(sorted(EXPECTED_VOCABULARY - set(default or []))) +
          ' extra=' + repr(sorted(set(default or []) - EXPECTED_VOCABULARY))[:500])


def test_the_cli_reports_undispositioned_defects_in_a_file():
    if not ready():
        return
    if not callable(getattr(fs, 'main', None)):
        check('the CLI exits 1 when a defect is cited undispositioned', False, 'no main()')
        return
    tmp = tempfile.mkdtemp(prefix='t733-cli-')
    try:
        f = Path(tmp) / 'summary.md'
        f.write_text(UNHEALTHY + '\n'
                     '14 branches unmerged -- filed t733.\n'
                     + DETECTOR + '\n', encoding='utf-8')
        rc, out, err = call_main(['--input', str(f)])
        rows = [r for r in out.splitlines() if r.strip()]
        check('the CLI exits 1 when a defect is cited undispositioned', rc == 1,
              'rc=' + repr(rc) + ' stdout=' + repr(out)[:300])
        check('it prints exactly one row: the two dispositioned lines are silent',
              len(rows) == 1, repr(rows)[:500])
        if rows:
            check('the row names the file and the line number',
                  str(f) in rows[0] and ':1:' in rows[0], rows[0][:400])
            check('the row carries the whole record, uncut', '40 hours' in rows[0], rows[0][:400])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_a_clean_file_is_quiet_and_exits_zero():
    if not ready():
        return
    if not callable(getattr(fs, 'main', None)):
        check('a session that names no defect exits 0 with nothing on stdout', False, 'no main()')
        return
    tmp = tempfile.mkdtemp(prefix='t733-clean-')
    try:
        f = Path(tmp) / 'summary.md'
        f.write_text('Merged p33. The suite is green: 39 passed, 0 failed.\n'
                     'Fixed the sys.path in the oracle.\n', encoding='utf-8')
        rc, out, err = call_main(['--input', str(f)])
        check('a session that names no defect exits 0 with nothing on stdout',
              rc == 0 and out.strip() == '', 'rc=' + repr(rc) + ' stdout=' + repr(out)[:300])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_a_long_record_is_printed_whole_never_sliced():
    if not ready():
        return
    if not callable(getattr(fs, 'main', None)):
        check('a long record is printed whole, never sliced', False, 'no main()')
        return
    tmp = tempfile.mkdtemp(prefix='t733-long-')
    try:
        filler = 'the chart ' * 60
        f = Path(tmp) / 'long.md'
        f.write_text(filler + 'is unavailable\n', encoding='utf-8')
        rc, out, err = call_main(['--input', str(f)])
        check('a long record is printed whole, never sliced',
              rc == 1 and filler in out and 'is unavailable' in out,
              'rc=' + repr(rc) + ' len=' + str(len(out)) + ' head=' + repr(out[:200]))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_a_missing_input_file_is_named_and_not_a_silent_pass():
    if not ready():
        return
    if not callable(getattr(fs, 'main', None)):
        check('a missing input file is named on stderr and exits 2', False, 'no main()')
        return
    tmp = tempfile.mkdtemp(prefix='t733-missing-')
    try:
        missing = Path(tmp) / 'not-there.md'
        rc, out, err = call_main(['--input', str(missing)])
        check('a missing input file is named on stderr and exits 2', rc == 2,
              'rc=' + repr(rc) + ' stderr=' + repr(err)[:300])
        check('the message names the path it could not read', 'not-there.md' in err, repr(err)[:300])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_no_input_argument_is_not_a_verdict():
    if not ready():
        return
    if not callable(getattr(fs, 'main', None)):
        check('running with no --input says so and exits 2', False, 'no main()')
        return
    rc, out, err = call_main([])
    check('running with no --input says so and exits 2', rc == 2,
          'rc=' + repr(rc) + ' stdout=' + repr(out)[:200])
    check('and it prints a usage line naming --input',
          'usage' in err.lower() and '--input' in err, repr(err)[:300])


if __name__ == '__main__':
    tests = [
        test_the_module_exposes_the_scanner_api,
        test_a_defect_cited_in_prose_with_no_disposition_is_a_finding,
        test_the_state_adjective_class_is_recognised,
        test_the_same_sentence_with_a_task_number_is_not_a_finding,
        test_a_closed_task_can_be_the_disposition,
        test_a_short_token_is_not_a_task_number,
        test_unresolved_is_not_a_disposition,
        test_declined_must_carry_a_reason,
        test_declared_open_must_carry_its_close_condition,
        test_ordinary_report_prose_is_quiet,
        test_the_vocabulary_comes_from_the_schema_and_not_from_the_scanner,
        test_the_module_default_is_the_documented_set,
        test_the_cli_reports_undispositioned_defects_in_a_file,
        test_a_clean_file_is_quiet_and_exits_zero,
        test_a_long_record_is_printed_whole_never_sliced,
        test_a_missing_input_file_is_named_and_not_a_silent_pass,
        test_no_input_argument_is_not_a_verdict,
    ]
    for fn in tests:
        print(fn.__name__)
        fn()
    print('failures: ' + str(len(FAILURES)) + ' of ' + str(len(tests)) + ' tests')
    for name in FAILURES:
        print('  - ' + name)
    raise SystemExit(1 if FAILURES else 0)
