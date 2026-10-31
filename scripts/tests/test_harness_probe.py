'''scripts/tests/test_harness_probe.py -- oracle for scripts/harness_probe.py (t140).

The task deliverable is a MEASUREMENT: a list of specific mechanisms with a verdict each,
tested where testing is cheap. A record is trustworthy on three points, each pinned below:
a run that produced nothing must not look like a pass; a long output is recorded whole,
byte for byte; the verdict vocabulary is a closed allowlist -- anything outside it is a
finding, never a pass.

Run: python3 scripts/tests/test_harness_probe.py
pytest is not installed; this file is its own runner. Every child process is bounded by
the timeout the caller passes in, and the module writes only where it is told to.
'''
import json
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import harness_probe  # noqa: E402 -- the module is the missing change; that absence IS the red

# two distinct real constants: one repeated placeholder string cannot satisfy both
STDOUT_MARK = 'EGRESS_PROBE_MARKER_alpha'
STDOUT_MARK_2 = 'EGRESS_PROBE_MARKER_beta'


def _read_records(path):
    text = Path(path).read_text(encoding='utf-8')
    assert text.endswith('\n'), 'the record file must end with a newline'
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def _probe(out, code, timeout=60, harness='dsh'):
    return harness_probe.probe(harness=harness, command=[sys.executable, '-c', code],
                               out_path=out, timeout=timeout)


def test_appends_one_whole_record_per_run():
    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / 'measurements.jsonl'
        payload = '%s\n%s\n' % (STDOUT_MARK, STDOUT_MARK_2)
        rec = _probe(out, 'import sys; sys.stdout.write(%r)' % payload)
        recs = _read_records(out)
        assert len(recs) == 1, 'expected exactly 1 record, got %d' % len(recs)
        assert recs[0]['stdout'] == payload, (
            'stdout must be recorded whole, not a prefix: stored=%r payload=%r'
            % (recs[0]['stdout'], payload))
        assert rec['verdict'] == 'PASS', rec
        assert recs[0]['harness'] == 'dsh', recs[0]
        assert recs[0]['returncode'] == 0, recs[0]


def test_long_output_is_not_truncated():
    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / 'measurements.jsonl'
        payload = STDOUT_MARK * 2000   # 25 chars x 2000 = 50,000
        _probe(out, 'import sys; sys.stdout.write(%r)' % payload)
        stored = _read_records(out)[0]['stdout']
        assert len(stored) == len(payload), (
            'stdout was truncated: recorded %d of %d chars' % (len(stored), len(payload)))
        assert stored == payload, 'stdout must survive the round trip byte for byte'
        assert '[TRUNCATED' not in stored, 'no truncation marker belongs in a record'


def test_failure_is_recorded_as_failure_not_silence():
    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / 'measurements.jsonl'
        rec = _probe(out, 'import sys; sys.stderr.write(%r); sys.exit(7)' % STDOUT_MARK_2)
        stored = _read_records(out)[0]
        assert rec['verdict'] == 'FAIL', rec
        assert stored['returncode'] == 7, stored
        assert stored['stderr'] == STDOUT_MARK_2, stored
        assert stored['ok'] is False, stored
        assert rec['verdict'] != 'PASS', 'a failed run must never read as a pass'


def test_silent_zero_exit_is_not_a_pass():
    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / 'measurements.jsonl'
        rec = _probe(out, 'pass')
        stored = _read_records(out)[0]
        assert rec['verdict'] == 'NO-RESULT', rec
        assert stored['ok'] is False, stored
        assert (stored.get('reason') or '').strip(), (
            'a NO-RESULT record must say why it is empty, got %r' % (stored.get('reason'),))
        assert rec['verdict'] != 'PASS', rec


def test_timeout_is_named_and_not_waited_out():
    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / 'measurements.jsonl'
        started = time.monotonic()
        rec = _probe(out, 'import time; time.sleep(30)', timeout=1)
        elapsed = time.monotonic() - started
        stored = _read_records(out)[0]
        assert rec['verdict'] == 'TIMEOUT', rec
        assert stored['timeout'] == 1, stored
        assert elapsed < 10, 'the probe blocked %0.1fs for a 1s budget' % elapsed
        assert rec['verdict'] != 'PASS', rec


def test_verdict_vocabulary_is_a_closed_allowlist():
    assert set(harness_probe.VERDICTS) == {'PASS', 'FAIL', 'TIMEOUT', 'NO-RESULT'}
    for verdict in ('FAIL', 'TIMEOUT', 'NO-RESULT'):
        assert harness_probe.is_measured({'verdict': verdict}) is False, verdict
    assert harness_probe.is_measured({'verdict': 'PASS'}) is True
    assert harness_probe.is_measured({'verdict': 'length'}) is False, (
        'a verdict nobody predicted is a finding, not a healthy record')
    assert harness_probe.is_measured({}) is False, 'a record with no verdict is not healthy'


def test_writes_only_where_it_is_told():
    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / 'sub' / 'measurements.jsonl'
        out.parent.mkdir()
        _probe(out, 'print(%r)' % STDOUT_MARK)
        names = sorted(p.name for p in Path(d).rglob('*'))
        assert names == ['measurements.jsonl', 'sub'], names


def test_appends_across_runs_in_order():
    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / 'measurements.jsonl'
        _probe(out, 'print(%r)' % STDOUT_MARK)
        _probe(out, 'print(%r)' % STDOUT_MARK_2, harness='vllm-isolated')
        recs = _read_records(out)
        assert [r['harness'] for r in recs] == ['dsh', 'vllm-isolated'], recs
        assert recs[0]['stdout'] == STDOUT_MARK + '\n', recs[0]
        assert recs[1]['stdout'] == STDOUT_MARK_2 + '\n', recs[1]


if __name__ == '__main__':
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith('test_') and callable(fn):
            try:
                fn()
                print('PASS %s' % name)
            except Exception as exc:  # noqa: BLE001 -- report the cause, never swallow it
                failed += 1
                print('FAIL %s: %s: %s' % (name, type(exc).__name__, exc))
    print('%d failure(s)' % failed)
    sys.exit(1 if failed else 0)
