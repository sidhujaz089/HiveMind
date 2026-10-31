#!/usr/bin/env python3
'''t1496: a bundle copy that lands short must be recorded as a LOST-BYTES failure.

A plain cp of the 399 MB bundle over the soft NFS mount failed on close with EIO every run on
2026-10-19; the script now uses `dd ... oflag=direct` into a `.partial` name, compares sha256 with
the source, and mv's only when the hashes match. The mechanism is merged; what pins it is not.
scripts/tests/test_backup_copy_resilience.py exercises backup_corpora.copy_content_only in Python
and never runs the shell script; test_nas_backup_prune_wiring.py and test_nas_backup_kill_decay.py
run the real script for prune ordering and signal death. NOTHING asserts what the ledger SAYS when
the copy lands short -- and the script's own header comment says the ledger is the only surface
error_scan.py reads, so a row that names no bytes reaches no alert at all.

Why red on main, from the file as it is: the detail is built from `cperr`, which is assigned only
from dd's and mv's own output. A dd that exits 0 having written 8.6% of the file -- the exact shape
of the 10-19 truncation, and a shape `git bundle verify` passes -- leaves cperr EMPTY, so the row
reads: could not copy the bundle to the NAS: cp gave no message; NFS ... : 0. The byte counts and
the two sha256s -- the facts that made the 10-19 incident diagnosable, both computed by hand with
stat(1) and sha256sum(1) -- appear nowhere in the row. That is never-truncate-a-record's line about
an error that names a symptom, not a cause.

Every assertion marked CONTROL passes on main and pins behaviour this change must not lose.

No live state: every path is under tempfile.mkdtemp, and sandbox() refuses to run a script that
does not honour NAS_BACKUP_REPO/NAS_BACKUP_DEST, so nothing can reach /mnt/nas. `dd` is replaced by
a PATH shim that logs every invocation, so a suite passing on a script that stopped copying through
dd is impossible. No model call, no network, no fixed /tmp path, timeout= on every subprocess, and
every temp dir is removed in a finally block.
'''
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / 'scripts' / 'nas_backup.sh'
CORPORA = 'ai-workspace-corpora'
SENTINEL = 'EIO-SENTINEL-t1496'

# backup_corpora.py is stubbed: the corpus rsync is not under test and a sandbox has no corpora.
STUB = '\n'.join([
    'import os, pathlib, sys',
    'log = os.environ.get("NAS_BACKUP_TEST_LOG")',
    'if log:',
    '    pathlib.Path(log).open("a").write(" ".join(sys.argv[1:]) + "\\n")',
    'if len(sys.argv) > 1 and sys.argv[1] == "--to":',
    '    d = pathlib.Path(sys.argv[2]) / "ai-workspace-corpora"',
    '    d.mkdir(parents=True, exist_ok=True)',
    '    (d / "corpusfile").write_text("payload\\n")',
    '    (d / "MANIFEST.sha256").write_text("deadbeef  corpusfile\\n")',
    'sys.exit(0)',
]) + '\n'

# A fake dd: logs every invocation, honours if=/of=, a byte-count truncation, a last-byte flip and
# an exit code. It refuses an invocation it was not written to answer, so a copy mechanism that
# stops calling dd cannot pass these tests by default.
DD_SHIM = '\n'.join([
    '#!/usr/bin/env python3',
    'import os, pathlib, sys',
    'args = {}',
    'for a in sys.argv[1:]:',
    '    if "=" in a:',
    '        k, sep, v = a.partition("=")',
    '        args.setdefault(k, v)',
    'log = os.environ.get("NAS_BACKUP_TEST_DD_LOG")',
    'if log:',
    '    pathlib.Path(log).open("a").write(" ".join(sys.argv[1:]) + "\\n")',
    'if "if" not in args or "of" not in args:',
    '    sys.stderr.write("dd shim: not the copy this suite is wired for: %r\\n" % (sys.argv[1:],))',
    '    sys.exit(2)',
    'src = pathlib.Path(args["if"])',
    'dst = pathlib.Path(args["of"])',
    'data = src.read_bytes()',
    'cut = os.environ.get("NAS_BACKUP_TEST_TRUNCATE")',
    'if cut:',
    '    data = data[:int(cut)]',
    'if os.environ.get("NAS_BACKUP_TEST_CORRUPT") == "1" and data:',
    '    data = data[:-1] + bytes([data[-1] ^ 0xFF])',
    'dst.parent.mkdir(parents=True, exist_ok=True)',
    'dst.write_bytes(data)',
    'code = int(os.environ.get("NAS_BACKUP_TEST_DD_EXIT") or 0)',
    'if code:',
    '    sys.stderr.write("dd: of=%s: Input/output error EIO-SENTINEL-t1496\\n" % args["of"])',
    'sys.exit(code)',
]) + '\n'


def git(repo, *args):
    return subprocess.run(['git', '-C', str(repo)] + list(args), capture_output=True,
                          text=True, timeout=120)


def sandbox(base):
    """A repo with one commit and a destination dir, both under the caller's temp dir."""
    text = SCRIPT.read_text(encoding='utf-8')
    assert 'NAS_BACKUP_REPO' in text, 'the script ignores NAS_BACKUP_REPO, so a sandboxed run could touch the live repo'
    assert 'NAS_BACKUP_DEST' in text, 'the script ignores NAS_BACKUP_DEST, so a sandboxed run could touch the live /mnt/nas'
    repo = base / 'repo'
    dest = base / 'dest'
    (repo / 'scripts').mkdir(parents=True)
    (repo / 'workspace' / 'mirror').mkdir(parents=True)
    dest.mkdir(parents=True)
    assert git(repo, 'init', '-q').returncode == 0, 'git init failed in the sandbox'
    (repo / 'note.txt').write_text('v1\n', encoding='utf-8')
    assert git(repo, 'add', 'note.txt').returncode == 0, 'git add failed in the sandbox'
    r = git(repo, '-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-qm', 'c1')
    assert r.returncode == 0, 'git commit failed in the sandbox: %s' % r.stderr
    (repo / 'scripts' / 'backup_corpora.py').write_text(STUB, encoding='utf-8')
    bindir = base / 'bin'
    bindir.mkdir()
    shim = bindir / 'dd'
    shim.write_text(DD_SHIM, encoding='utf-8')
    os.chmod(str(shim), 0o755)
    return repo, dest


def env_for(base, repo, dest, extra=None):
    env = dict(os.environ)
    env['NAS_BACKUP_REPO'] = str(repo)
    env['NAS_BACKUP_DEST'] = str(dest)
    env['NAS_BACKUP_TEST_LOG'] = str(base / 'argv.log')
    env['NAS_BACKUP_TEST_DD_LOG'] = str(base / 'dd.log')
    env['PATH'] = str(base / 'bin') + os.pathsep + env.get('PATH', '')
    for k, v in (extra or {}).items():
        env[k] = str(v)
    return env


def run_script(base, repo, dest, extra=None):
    return subprocess.run(['bash', str(SCRIPT)], env=env_for(base, repo, dest, extra),
                          cwd=str(repo), capture_output=True, text=True, timeout=180)


def rows(ledger):
    if not ledger.exists():
        return []
    return [json.loads(l) for l in ledger.read_text(encoding='utf-8').splitlines() if l.strip()]


def source_bundle(repo):
    found = sorted((repo / 'workspace' / 'mirror').glob('*.bundle'))
    assert len(found) == 1, 'expected exactly one source bundle, found %r' % ([str(p) for p in found],)
    return found[0]


def dest_bundle(dest):
    found = sorted((dest / CORPORA).glob('*.bundle'))
    assert len(found) <= 1, 'more than one bundle in the sandbox destination: %r' % ([str(p) for p in found],)
    return found[0] if found else None


def partials(dest):
    # The staged name starts with a dot, so glob('*.partial') would not see it: list the dir.
    d = dest / CORPORA
    if not d.is_dir():
        return []
    return sorted(p.name for p in d.iterdir() if p.name.endswith('.partial'))


def dd_invocations(base):
    log = base / 'dd.log'
    if not log.is_file():
        return []
    return [l for l in log.read_text(encoding='utf-8').splitlines() if l.strip()]


def build_bundle(base):
    """The bytes of a sandbox bundle: the script makes it itself, so the size is measured, not guessed."""
    repo, dest = sandbox(base)
    r = run_script(base, repo, dest)
    assert r.returncode == 0, 'the build run failed: %s %s' % (r.stdout, r.stderr)
    return source_bundle(repo).read_bytes()


def split_of(data):
    """(bytes_kept, sha256_of_those_bytes) for a real prefix of the bundle such that the byte count
    appears in NEITHER hash. The row is asserted to carry both hashes plus both byte counts; if a
    count's digits occurred inside a digest, asserting the count in the detail would prove nothing."""
    size = len(data)
    src_sha = hashlib.sha256(data).hexdigest()
    for d in range(2, 10):
        keep = size // d
        if keep <= 0:
            continue
        keep_sha = hashlib.sha256(data[:keep]).hexdigest()
        if str(keep) not in src_sha and str(keep) not in keep_sha:
            return keep, keep_sha
    return 0, ''


def test_a_successful_copy_lands_the_source_bytes_and_is_recorded_verified():
    base = Path(tempfile.mkdtemp(prefix='t1496_ok_'))
    try:
        repo, dest = sandbox(base)
        r = run_script(base, repo, dest)
        assert r.returncode == 0, 'the sandboxed happy path failed: %s %s' % (r.stdout, r.stderr)
        assert dd_invocations(base), 'the copy was never attempted through dd'
        out = dest_bundle(dest)
        assert out is not None, 'no bundle landed in the destination'
        assert hashlib.sha256(out.read_bytes()).hexdigest() == hashlib.sha256(source_bundle(repo).read_bytes()).hexdigest(), 'the copy is not byte-identical to the source bundle'
        assert not partials(dest), 'a .partial file was left on the destination'
        ledger = rows(repo / 'workspace' / 'backup-runs.jsonl')
        assert ledger, 'the happy path wrote no ledger row'
        assert ledger[-1].get('status') == 'verified', ledger[-1]
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_a_short_copy_records_the_bytes_and_hashes_it_lost():
    base = Path(tempfile.mkdtemp(prefix='t1496_short_'))
    try:
        probe = base / 'probe'
        probe.mkdir()
        data = build_bundle(probe)
        assert len(data) > 512, 'fixture premise: the sandbox bundle is only %d bytes, too small to truncate' % len(data)
        kept, kept_sha = split_of(data)
        assert kept, 'fixture premise: every split of a %d-byte bundle gave a byte count whose digits appear inside a sha256' % len(data)
        run = base / 'run'
        repo, dest = sandbox(run)
        r = run_script(run, repo, dest, {'NAS_BACKUP_TEST_TRUNCATE': kept})
        calls = dd_invocations(run)
        assert calls, 'the copy was never attempted through dd, so this is not the mechanism under test'
        assert 'of=' in calls[0], 'the logged dd invocation names no destination: %r' % (calls[0],)
        assert calls[0].split('of=')[1].split()[0].endswith('.partial'), 'CONTROL: the copy stages into a .partial name, it never lands on the final one: %r' % (calls[0],)
        assert r.returncode != 0, 'a bundle that landed short must fail the night'
        ledger = rows(repo / 'workspace' / 'backup-runs.jsonl')
        assert ledger, 'a truncated copy wrote no ledger row at all'
        row = ledger[-1]
        assert row.get('job') == 'nas-backup', row
        assert row.get('status') == 'copy-failed', 'a short copy was recorded as %r: a truncated bundle is a copy failure, not a success: %r' % (row.get('status'), row)
        detail = str(row.get('detail', ''))
        assert detail.strip(), 'the row names no cause at all'
        assert str(kept) in detail, 'the record does not say how many bytes landed: the copy wrote %d of %d and the detail says %r' % (kept, len(data), detail)
        assert hashlib.sha256(data).hexdigest() in detail, 'the record does not name the source sha256, so a reader cannot match it against the bundle: %r' % detail
        assert kept_sha in detail, 'the record does not name the destination sha256, so a reader cannot see which bytes arrived: %r' % detail
        assert not partials(dest), 'a .partial file was left on the destination'
        assert dest_bundle(dest) is None, 'CONTROL: a truncated bundle must never be left at the final name'
        assert not any(x.get('status') == 'verified' for x in ledger), 'a truncated bundle must never be recorded verified: %r' % (ledger,)
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_a_same_length_corruption_is_not_verified_and_names_both_hashes():
    base = Path(tempfile.mkdtemp(prefix='t1496_corrupt_'))
    try:
        repo, dest = sandbox(base)
        r = run_script(base, repo, dest, {'NAS_BACKUP_TEST_CORRUPT': '1'})
        assert dd_invocations(base), 'the copy was never attempted through dd'
        assert r.returncode != 0, 'a copy whose bytes differ from the source must fail the night'
        data = source_bundle(repo).read_bytes()
        flipped = data[:-1] + bytes([data[-1] ^ 0xFF])
        assert len(flipped) == len(data), 'fixture premise: a same-length corruption is the case a size comparison cannot see'
        src_sha = hashlib.sha256(data).hexdigest()
        dst_sha = hashlib.sha256(flipped).hexdigest()
        assert src_sha != dst_sha, 'fixture premise: the corruption did not change the bytes'
        ledger = rows(repo / 'workspace' / 'backup-runs.jsonl')
        assert ledger, 'the corrupted copy wrote no ledger row'
        assert ledger[-1].get('status') == 'copy-failed', ledger[-1]
        detail = str(ledger[-1].get('detail', ''))
        assert src_sha in detail, 'the record does not name the source sha256: %r' % detail
        assert dst_sha in detail, 'the record does not name the destination sha256, so a reader cannot see that the bytes and not the length changed: %r' % detail
        assert not partials(dest), 'a .partial file was left on the destination'
        assert dest_bundle(dest) is None, 'CONTROL: a bundle whose bytes differ must never reach the final name'
        assert not any(x.get('status') == 'verified' for x in ledger), 'a corrupted copy must never be recorded verified: %r' % (ledger,)
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_a_transport_error_keeps_dd_words_and_says_how_many_bytes_are_missing():
    base = Path(tempfile.mkdtemp(prefix='t1496_eio_'))
    try:
        repo, dest = sandbox(base)
        r = run_script(base, repo, dest, {'NAS_BACKUP_TEST_DD_EXIT': '1'})
        assert dd_invocations(base), 'the copy was never attempted through dd'
        assert r.returncode != 0, 'a copy that dies on EIO must fail the night'
        ledger = rows(repo / 'workspace' / 'backup-runs.jsonl')
        assert ledger, 'the EIO copy wrote no ledger row'
        row = ledger[-1]
        assert row.get('status') == 'copy-failed', row
        detail = str(row.get('detail', ''))
        assert SENTINEL in detail, 'CONTROL: the transport error is carried into the record verbatim, not replaced by a code: %r' % detail
        src_sha = hashlib.sha256(source_bundle(repo).read_bytes()).hexdigest()
        assert src_sha in detail, 'the record of a failed copy does not name the source sha256, so it cannot be matched against the bundle on the NAS: %r' % detail
        assert not partials(dest), 'a .partial file was left on the destination'
        assert dest_bundle(dest) is None, 'CONTROL: a bundle that failed to copy must not appear at the final name'
        assert not any(x.get('status') == 'verified' for x in ledger), 'a dead transport must never be recorded verified: %r' % (ledger,)
    finally:
        shutil.rmtree(base, ignore_errors=True)


TESTS = [
    test_a_successful_copy_lands_the_source_bytes_and_is_recorded_verified,
    test_a_short_copy_records_the_bytes_and_hashes_it_lost,
    test_a_same_length_corruption_is_not_verified_and_names_both_hashes,
    test_a_transport_error_keeps_dd_words_and_says_how_many_bytes_are_missing,
]

if __name__ == '__main__':
    failed = 0
    for t in TESTS:
        try:
            t()
            print('PASS %s' % t.__name__)
        except AssertionError as exc:
            failed += 1
            print('FAIL %s: %s' % (t.__name__, exc))
    print('%d of %d passed' % (len(TESTS) - failed, len(TESTS)))
    sys.exit(1 if failed else 0)
