#!/usr/bin/env python3
'''t741: every corpus-digest drift gets a durable row on disk.

Run: python3 scripts/tests/test_digests_ledger.py   (pytest is not installed)

WHAT THIS PINS. scripts/digests.py compares the watched chat/ files against
workspace/corpus-digests.json, prints '[DRIFT] ...' and exits 1 -- and records
nothing on disk, so a re-baseline erases the evidence that drift was ever seen.
This suite requires one machine-readable row per drifted watched file, appended to
<repo>/workspace/corpus-drift-log.jsonl, carrying the real prev and new sha256, the
byte count, a ts datetime.fromisoformat can parse, the actor, the session id and
verdict 'pending'.
'''
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]          # scripts/tests -> repo root
sys.path.insert(0, str(ROOT / 'scripts'))
import digests  # noqa: E402  the real module: WATCHED supplies the real paths

MODULE = ROOT / 'scripts' / 'digests.py'
REL_LOG = 'workspace/corpus-drift-log.jsonl'
ACTOR = 'claude-code/opus-5'
SESSION = 'sess-t741-fixture'
NL = chr(10)
BASE0 = 'baseline content 0' + NL
EDIT0 = 'baseline content 0' + NL + 'EDITED BY THE FIXTURE' + NL


def sha_text(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def build_repo(tmp):
    repo = Path(tmp) / 'repo'
    (repo / 'scripts').mkdir(parents=True)
    shutil.copy(str(MODULE), str(repo / 'scripts' / 'digests.py'))
    for i, rel in enumerate(digests.WATCHED):
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text('baseline content %d' % i + NL, encoding='utf-8')
    return repo


def run_digests(repo, args):
    env = dict(os.environ)
    env['WORKSPACE_AGENT'] = ACTOR
    env['DSH_SESSION_ID'] = SESSION
    return subprocess.run([sys.executable, str(repo / 'scripts' / 'digests.py')] + list(args),
                          cwd=str(repo), capture_output=True, text=True,
                          timeout=60, env=env)


def rows_of(repo):
    log = repo / REL_LOG
    if not log.is_file():
        return []
    return [json.loads(ln) for ln in log.read_text(encoding='utf-8').splitlines() if ln.strip()]


def test_drift_writes_one_durable_row_with_exact_fields():
    tmp = tempfile.mkdtemp(prefix='t741-drift-')
    try:
        repo = build_repo(tmp)
        base = run_digests(repo, ['--write'])
        assert base.returncode == 0, 'bare --write must still record: ' + base.stdout + base.stderr
        (repo / digests.WATCHED[0]).write_text(EDIT0, encoding='utf-8')

        r = run_digests(repo, [])
        assert r.returncode == 1, 'a drift run must still exit 1: ' + r.stdout
        assert '[DRIFT]' in r.stdout, 'the printed report must survive: ' + r.stdout

        rows = rows_of(repo)
        assert len(rows) == 1, 'expected exactly 1 drift row, got %d: %r' % (len(rows), rows)
        row = rows[0]
        assert row['file'] == digests.WATCHED[0], repr(row.get('file'))
        assert row['prev_sha'] == sha_text(BASE0), repr(row.get('prev_sha'))
        assert row['sha'] == sha_text(EDIT0), repr(row.get('sha'))
        assert row['prev_sha'] != row['sha'], 'a drift row whose two shas match records nothing'
        assert row['bytes'] == len(EDIT0.encode('utf-8')), repr(row.get('bytes'))
        assert row['verdict'] == 'pending', repr(row.get('verdict'))
        assert row['actor'] == ACTOR, repr(row.get('actor'))
        assert row['session_id'] == SESSION, repr(row.get('session_id'))
        ts = row['ts']
        assert isinstance(ts, str) and ts, repr(row.get('ts'))
        datetime.fromisoformat(ts)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_no_drift_writes_no_row():
    tmp = tempfile.mkdtemp(prefix='t741-clean-')
    try:
        repo = build_repo(tmp)
        assert run_digests(repo, ['--write']).returncode == 0
        r = run_digests(repo, [])
        assert r.returncode == 0, 'an un-drifted run must exit 0: ' + r.stdout
        assert '[PASS]' in r.stdout, r.stdout
        assert not (repo / REL_LOG).exists(), 'a run with no drift must not write a drift row'
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_every_drifted_file_gets_its_own_row():
    tmp = tempfile.mkdtemp(prefix='t741-two-')
    try:
        repo = build_repo(tmp)
        assert run_digests(repo, ['--write']).returncode == 0
        for i in (0, 1):
            (repo / digests.WATCHED[i]).write_text('edited content %d' % i + NL, encoding='utf-8')
        r = run_digests(repo, [])
        assert r.returncode == 1, r.stdout
        rows = rows_of(repo)
        assert len(rows) == 2, 'one row per drifted file, got %d: %r' % (len(rows), rows)
        assert {row['file'] for row in rows} == {digests.WATCHED[0], digests.WATCHED[1]}, \
            'the rows must name the real watched paths: %r' % [row['file'] for row in rows]
        assert len({row['prev_sha'] for row in rows}) == 2, \
            'two drifted files must not share a prev_sha: %r' % [row['prev_sha'] for row in rows]
        assert all(row['verdict'] == 'pending' for row in rows), repr(rows)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_rows_append_across_runs():
    tmp = tempfile.mkdtemp(prefix='t741-append-')
    try:
        repo = build_repo(tmp)
        assert run_digests(repo, ['--write']).returncode == 0
        (repo / digests.WATCHED[0]).write_text(EDIT0, encoding='utf-8')
        assert run_digests(repo, []).returncode == 1
        first = (repo / REL_LOG).read_text(encoding='utf-8')
        rows1 = rows_of(repo)
        assert len(rows1) == 1, repr(rows1)

        (repo / digests.WATCHED[2]).write_text('edited the ledger' + NL, encoding='utf-8')
        assert run_digests(repo, []).returncode == 1
        second = (repo / REL_LOG).read_text(encoding='utf-8')
        assert second.startswith(first), 'the drift ledger is append-only: run one was rewritten'
        rows2 = rows_of(repo)
        assert len(rows2) == 2, 'expected 2 appended rows, got %d: %r' % (len(rows2), rows2)
        assert rows2[0]['file'] == digests.WATCHED[0], repr(rows2)
        assert rows2[1]['file'] == digests.WATCHED[2], repr(rows2)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    tests = [test_drift_writes_one_durable_row_with_exact_fields,
             test_no_drift_writes_no_row,
             test_every_drifted_file_gets_its_own_row,
             test_rows_append_across_runs]
    failed = 0
    for t in tests:
        try:
            t()
            print('PASS %s' % t.__name__)
        except Exception as e:  # noqa: BLE001 -- reported, never swallowed
            failed += 1
            print('FAIL %s: %s: %s' % (t.__name__, type(e).__name__, e))
    print('%d/%d passed' % (len(tests) - failed, len(tests)))
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
