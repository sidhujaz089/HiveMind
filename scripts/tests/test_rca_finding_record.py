# t1258 oracle: a finding record must not be persisted as 'unresolved' when its RCA report is
# not on disk, and a dead RCA dispatch must record 'rca-failed' with the exit reason ON THE RECORD.
# Run: python3 scripts/tests/test_rca_finding_record.py  (no pytest; exits non-zero on any failure)
# RED ON MAIN for exactly the missing change: rca() lets the asker's exception escape with the
# finding untouched, and write_rca_outcome writes any disposition it is given with no existence
# check on rca_report. Run it before changing anything: tests 1 and 2 must fail for those reasons.
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
import skills                 # patched below: this oracle writes nothing outside its temp dirs
import session_review as sr

# rca() assembles its prompt through skills.with_skills and reads the module constant SKILL.
# Patch both: the prompt is built in memory, and the skill file is this test file, which exists by
# definition and is only ever read. This keeps the oracle off every live-state surface.
skills.with_skills = lambda prompt, role, **kw: (prompt, [])
sr.SKILL = Path(__file__)

FINDING = {
    'key': 'zzt1258key0001',
    'kind': 'unresolved',
    'quote': 'Those invariants remain mine to write',
    'where': 'assistant, late',
    'why_missed': '',
    'disposition': 'unresolved',
    'request': 'req-zzt1258.json',
    'session': 'zzsesh0001',
    'harness': 'claude',
    'found': '2026-10-16T00:00:00Z',
}


def _mint(d, name, **over):
    rec = dict(FINDING)
    rec.update(over)
    p = Path(d) / name
    p.write_text(json.dumps(rec, indent=1) + '\n', encoding='utf-8')
    return p


def _read(p):
    return json.loads(Path(p).read_text(encoding='utf-8'))


def _raiser(*a, **k):
    raise RuntimeError('dispatch died (fake)')


def test_dead_dispatch_records_rca_failed_on_the_record():
    with tempfile.TemporaryDirectory() as d:
        p = _mint(d, 'f1.json')
        raised = False
        exc = None
        try:
            sr.rca(p, asker=_raiser)
        except Exception as e:
            raised = True
            exc = e
        assert raised, ('rca() swallowed the dispatch failure: a dead dispatch must not read as '
                       'success -- write the record AND let the caller see the error')
        assert 'dispatch died (fake)' in str(exc), ('the caller must get the dispatch error itself: %r' % (exc,))
        assert p.is_file(), 'the failure is recorded in place: the finding must not be moved on a failed RCA'
        rec = _read(p)
        assert rec.get('disposition') == 'rca-failed', (
            "a dead RCA dispatch must store disposition 'rca-failed' on the finding record; it is "
            'left as %r -- an untouched record is what made key de1d812a5a5f read as an ordinary '
            "disposition='unresolved' with no report and no completion evidence" % (rec.get('disposition'),))
        assert rec.get('disposition') != 'unresolved', rec
        assert str(rec.get('rca_report') or '').strip() == '', (
            'a failed RCA must not claim a report path: %r' % (rec.get('rca_report'),))
        joined = ' | '.join(str(v) for v in rec.values())
        assert 'RuntimeError' in joined and 'dispatch died (fake)' in joined, (
            'the exit reason must be RECORDED on the record, not only printed: %s' % (joined[:400],))
        assert rec.get('key') == 'zzt1258key0001', 'the record keeps its own key: %s' % (rec,)
        assert rec.get('quote') == FINDING['quote'], 'the record keeps its quote: %s' % (rec,)


def test_unresolved_with_a_missing_report_is_un_writable():
    with tempfile.TemporaryDirectory() as d:
        p = _mint(d, 'f2.json', disposition='deferred', rca_report='')
        before = p.read_text(encoding='utf-8')
        raised = False
        try:
            sr.write_rca_outcome(p, outcome_task='', rca_report='workspace/rca/zz-not-here-0001.md',
                                 disposition='unresolved', completed_at='2026-10-16T00:00:00Z',
                                 tasks_dir=ROOT / 'workspace' / 'tasks')
        except ValueError:
            raised = True
        assert raised, ("persisting disposition='unresolved' with an rca_report that is not on disk "
                        'must be refused at the write -- Rule-5 rung 1: make the orphaned state unstateable')
        assert p.read_text(encoding='utf-8') == before, (
            'a refused write must leave the record byte-identical, not half-updated')


def test_unresolved_with_a_report_on_disk_is_still_writable():
    with tempfile.TemporaryDirectory() as d:
        p = _mint(d, 'f3.json', disposition='deferred')
        rel = 'STATE.md'
        assert (ROOT / rel).is_file(), 'fixture precondition: the repo-root file used as a real report is missing'
        rec = sr.write_rca_outcome(p, outcome_task='', rca_report=rel, disposition='unresolved',
                                   completed_at='2026-10-16T00:00:00Z',
                                   tasks_dir=ROOT / 'workspace' / 'tasks')
        assert rec['disposition'] == 'unresolved', rec
        assert _read(p)['disposition'] == 'unresolved', _read(p)
        assert _read(p)['rca_report'] == rel, _read(p)


def test_rca_failed_is_writable_with_no_report():
    with tempfile.TemporaryDirectory() as d:
        p = _mint(d, 'f4.json', disposition='deferred')
        rec = sr.write_rca_outcome(p, outcome_task='', rca_report='', disposition='rca-failed',
                                   outcome_status='unknown', completed_at='2026-10-16T00:00:00Z',
                                   tasks_dir=ROOT / 'workspace' / 'tasks')
        assert rec['disposition'] == 'rca-failed', rec
        assert _read(p)['disposition'] == 'rca-failed', _read(p)


def _main():
    tests = [test_dead_dispatch_records_rca_failed_on_the_record,
             test_unresolved_with_a_missing_report_is_un_writable,
             test_unresolved_with_a_report_on_disk_is_still_writable,
             test_rca_failed_is_writable_with_no_report]
    failed = 0
    for t in tests:
        try:
            t()
            print('PASS %s' % t.__name__)
        except AssertionError as e:
            failed += 1
            print('FAIL %s: %s' % (t.__name__, e))
    print('%d/%d passed' % (len(tests) - failed, len(tests)))
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(_main())
