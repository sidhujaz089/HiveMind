#!/usr/bin/env python3
'''t802 -- backup_corpora.py must survive a mount drop: stage, retry, rename, record.

Mechanism (workspace/rca/5d889eec288f.md): copy_content_only makes a single shutil.copyfile
pass straight into the final path, so a transport drop at file N of a tree leaves the bundle
without the manifest that makes it a bundle (written once, at the end of do_backup) and with
no record anywhere that the attempt happened. Red on main for exactly that reason; green
after the change the brief asks for. No live state: every write goes under tempfile.mkdtemp.
'''
import errno
import json
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / 'scripts'))
import backup_corpora as bc

PASSED = FAILED = 0
KEYS = {'ts', 'target', 'corpus', 'files', 'bytes', 'stage', 'errno', 'retry_count', 'result'}
ONE = b'first corpus payload'
TWO = bytes(bytearray([0, 1, 2, 3])) + b'second payload'


def check(label, got, want):
    global PASSED, FAILED
    if got == want:
        PASSED += 1
        print('  [ok]   ' + label)
    else:
        FAILED += 1
        print('  [BAD]  ' + label)
        print('         got:  ' + repr(got))
        print('         want: ' + repr(want))


def make_src(tmp):
    src = Path(tmp) / 'src'
    (src / 'sub').mkdir(parents=True)
    (src / 'one.txt').write_bytes(ONE)
    (src / 'sub' / 'two.bin').write_bytes(TWO)
    return src


def read_rows(log):
    out = []
    if not log.is_file():
        return out
    for line in log.read_text(encoding='utf-8').splitlines():
        if line.strip():
            out.append(json.loads(line))
    return out


def test_transient_drop_is_retried_and_the_file_lands(tmp):
    src = make_src(tmp)
    dst = Path(tmp) / 'dst' / 'one.txt'
    bc.ATTEMPT_LOG = Path(tmp) / 'attempts.jsonl'
    real = bc.shutil.copyfile
    calls = []

    def flaky(s, d, *a, **k):
        calls.append(Path(d))
        if len(calls) < 3:
            raise OSError(errno.EIO, 'Input/output error')
        return real(s, d)

    real_sleep = bc.time.sleep
    bc.time.sleep = lambda s: None
    bc.shutil.copyfile = flaky
    try:
        bc.copy_content_only(src / 'one.txt', dst)
    finally:
        bc.shutil.copyfile = real
        bc.time.sleep = real_sleep

    check('the copy lands the whole source bytes',
          (dst.is_file(), dst.read_bytes() if dst.is_file() else None), (True, ONE))
    limit = getattr(bc, 'COPY_RETRY_LIMIT', None)
    check('COPY_RETRY_LIMIT exists and is a small bound (1 < n <= 8)',
          isinstance(limit, int) and 1 < limit <= 8, True)
    check('the copy was attempted three times: retried, then it stopped on success',
          len(calls), 3)
    check('every attempt wrote the .part staging name, never the final name',
          [p.name for p in calls], [dst.name + '.part'] * 3)
    check('no .part file survives a successful copy',
          sorted(p.name for p in dst.parent.glob('*.part')), [])


def test_every_attempt_appends_a_row_with_the_real_values(tmp):
    src = make_src(tmp)
    dst1 = Path(tmp) / 'dst' / 'one.txt'
    dst2 = Path(tmp) / 'dst' / 'sub' / 'two.bin'
    log = Path(tmp) / 'attempts.jsonl'
    check('the module declares ATTEMPT_LOG as the sink t730 reads',
          hasattr(bc, 'ATTEMPT_LOG'), True)
    check('the default path it declares ends in backup-attempts.jsonl',
          str(getattr(bc, 'ATTEMPT_LOG', '')).endswith('backup-attempts.jsonl'), True)
    bc.ATTEMPT_LOG = log
    real = bc.shutil.copyfile
    seen = []

    def flaky(s, d, *a, **k):
        seen.append(1)
        if len(seen) == 1:
            raise OSError(errno.EIO, 'Input/output error')
        return real(s, d)

    real_sleep = bc.time.sleep
    bc.time.sleep = lambda s: None
    bc.shutil.copyfile = flaky
    try:
        bc.copy_content_only(src / 'one.txt', dst1)
        bc.copy_content_only(src / 'sub' / 'two.bin', dst2)
    finally:
        bc.shutil.copyfile = real
        bc.time.sleep = real_sleep

    check('the second corpus file is a DISTINCT payload, not a repeat of the first',
          (src / 'sub' / 'two.bin').read_bytes(), TWO)
    check('the nested file landed with its own bytes',
          (dst2.is_file(), dst2.read_bytes() if dst2.is_file() else None), (True, TWO))
    rows = read_rows(log)
    check('one row PER ATTEMPT: a retry, a success, a second success = 3 rows', len(rows), 3)
    if len(rows) == 3:
        check('every row carries the t730 schema keys', all(set(r) >= KEYS for r in rows), True)
        check('the failed row records result retry', rows[0].get('result'), 'retry')
        check('the failed row records the errno it hit, not zero', rows[0].get('errno'), errno.EIO)
        check('the failed row records stage copy', rows[0].get('stage'), 'copy')
        check('the failed row is attempt 0 of its own file', rows[0].get('retry_count'), 0)
        check('a retry row records zero bytes written', rows[0].get('bytes'), 0)
        check('the recovering row records result ok', rows[1].get('result'), 'ok')
        check('the recovering row is attempt 1', rows[1].get('retry_count'), 1)
        check('the ok row names the FINAL destination, never the .part name',
              rows[1].get('target'), str(dst1))
        check('the ok row counts one file', rows[1].get('files'), 1)
        check('the ok row records the real source file size',
              rows[1].get('bytes'), (src / 'one.txt').stat().st_size)
        check('the ok row closes the attempt with errno 0', rows[1].get('errno'), 0)
        check('the second file gets its own row naming its own destination',
              (rows[2].get('result'), rows[2].get('target')), ('ok', str(dst2)))
        check('the second file starts a fresh attempt count', rows[2].get('retry_count'), 0)
        check('the two ok rows carry the two DIFFERENT byte counts, not one placeholder',
              (rows[1].get('bytes'), rows[2].get('bytes')),
              ((src / 'one.txt').stat().st_size, (src / 'sub' / 'two.bin').stat().st_size))
        check('every row stamps a non-empty ts string',
              all(isinstance(r.get('ts'), str) and r.get('ts') for r in rows), True)
        check('every row carries a corpus string (an empty one is legal at this layer)',
              all(isinstance(r.get('corpus'), str) for r in rows), True)


def test_a_permanent_drop_gives_up_boundedly_and_leaves_no_file(tmp):
    src = make_src(tmp)
    dst = Path(tmp) / 'dst' / 'one.txt'
    log = Path(tmp) / 'attempts.jsonl'
    bc.ATTEMPT_LOG = log
    real = bc.shutil.copyfile
    calls = []
    delays = []

    def dead(s, d, *a, **k):
        calls.append(Path(d))
        Path(d).parent.mkdir(parents=True, exist_ok=True)
        Path(d).write_bytes(b'partial')
        raise OSError(errno.EIO, 'Input/output error')

    real_sleep = bc.time.sleep
    bc.time.sleep = lambda s: delays.append(s)
    bc.shutil.copyfile = dead
    raised = None
    try:
        bc.copy_content_only(src / 'one.txt', dst)
    except OSError as e:
        raised = e.errno
    finally:
        bc.shutil.copyfile = real
        bc.time.sleep = real_sleep

    check('a permanent drop still raises to the caller: the failure is not swallowed',
          raised, errno.EIO)
    limit = getattr(bc, 'COPY_RETRY_LIMIT', None)
    check('COPY_RETRY_LIMIT exists and is a small bound (1 < n <= 8)',
          isinstance(limit, int) and 1 < limit <= 8, True)
    check('the giving-up copy attempted exactly COPY_RETRY_LIMIT times',
          len(calls), limit if isinstance(limit, int) else 3)
    check('the backoff is bounded, so a flaky mount cannot stall the whole backup',
          sum(delays) <= 5.0, True)
    check('no half-written file is left at the final path', dst.is_file(), False)
    check('no .part file is left behind by the failure',
          sorted(p.name for p in dst.parent.glob('*.part')), [])
    check('the giving-up copy left no file of its own anywhere under dst',
          [p.name for p in dst.parent.rglob('*') if p.is_file()], [])
    check('every attempt was recorded: rows equal attempts', len(read_rows(log)), len(calls))


def test_a_non_transport_error_is_never_retried(tmp):
    src = make_src(tmp)
    dst = Path(tmp) / 'dst' / 'one.txt'
    bc.ATTEMPT_LOG = Path(tmp) / 'attempts.jsonl'
    real = bc.shutil.copyfile
    calls = []

    def denied(s, d, *a, **k):
        calls.append(1)
        raise OSError(errno.EACCES, 'Permission denied')

    real_sleep = bc.time.sleep
    bc.time.sleep = lambda s: None
    bc.shutil.copyfile = denied
    raised = None
    try:
        bc.copy_content_only(src / 'one.txt', dst)
    except OSError as e:
        raised = e.errno
    finally:
        bc.shutil.copyfile = real
        bc.time.sleep = real_sleep

    check('a content-level error (EACCES) reaches the caller as an OSError', raised, errno.EACCES)
    check('a content-level error is attempted ONCE: it is never retried', len(calls), 1)
    check('the transport set is exactly the errnos a flaky mount produces',
          getattr(bc, 'TRANSPORT_ERRNOS', None),
          frozenset((errno.EIO, errno.ENOTCONN, errno.ESTALE, errno.ETIMEDOUT,
                     errno.ECONNRESET, errno.ENOTRECOVERABLE)))


def test_a_tree_copy_survives_a_drop_on_its_first_file(tmp):
    src = make_src(tmp)
    dst = Path(tmp) / 'dst'
    bc.ATTEMPT_LOG = Path(tmp) / 'attempts.jsonl'
    real = bc.shutil.copyfile
    calls = []

    def flaky(s, d, *a, **k):
        calls.append(Path(s).name)
        if Path(s).name == 'one.txt' and calls.count('one.txt') == 1:
            raise OSError(errno.ENOTCONN, 'Transport endpoint is not connected')
        return real(s, d)

    real_sleep = bc.time.sleep
    bc.time.sleep = lambda s: None
    bc.shutil.copyfile = flaky
    completed = True
    try:
        bc.copy_tree_content(src, dst)
    except OSError:
        completed = False
    finally:
        bc.shutil.copyfile = real
        bc.time.sleep = real_sleep

    check('the tree copy completed: a transport drop on one file did not abort it',
          completed, True)
    check('the retried file landed with its bytes',
          (dst / 'one.txt').read_bytes() if (dst / 'one.txt').is_file() else None, ONE)
    check('its sibling landed with its own distinct bytes',
          (dst / 'sub' / 'two.bin').read_bytes() if (dst / 'sub' / 'two.bin').is_file() else None, TWO)
    check('the drop was retried on the file that hit it',
          [n for n in calls if n == 'one.txt'], ['one.txt', 'one.txt'])


def test_a_metadata_failure_stays_cosmetic(tmp):
    src = make_src(tmp)
    dst = Path(tmp) / 'dst' / 'one.txt'
    bc.ATTEMPT_LOG = Path(tmp) / 'attempts.jsonl'
    real_stat = bc.shutil.copystat
    bc.shutil.copystat = lambda *a, **k: (_ for _ in ()).throw(
        OSError(95, 'Operation not supported'))
    completed = True
    try:
        bc.copy_content_only(src / 'one.txt', dst)
    except OSError:
        completed = False
    finally:
        bc.shutil.copystat = real_stat

    check('a copy completes even when copystat always answers Errno 95', completed, True)
    check('the bytes landed anyway',
          (dst.is_file(), dst.read_bytes() if dst.is_file() else None), (True, ONE))


def test_the_default_log_is_a_repo_path_and_this_suite_writes_nothing_to_it(tmp):
    default = Path(getattr(bc, 'ATTEMPT_LOG',
                           REPO / 'workspace' / 'run-logs' / 'backup-attempts.jsonl'))
    before = default.stat().st_size if default.is_file() else None
    check('the default attempt log is named backup-attempts.jsonl', default.name,
          'backup-attempts.jsonl')
    check('running this suite created nothing at the default path',
          (default.stat().st_size if default.is_file() else None), before)


TESTS = [
    test_transient_drop_is_retried_and_the_file_lands,
    test_every_attempt_appends_a_row_with_the_real_values,
    test_a_permanent_drop_gives_up_boundedly_and_leaves_no_file,
    test_a_non_transport_error_is_never_retried,
    test_a_tree_copy_survives_a_drop_on_its_first_file,
    test_a_metadata_failure_stays_cosmetic,
    test_the_default_log_is_a_repo_path_and_this_suite_writes_nothing_to_it,
]


def run():
    global FAILED
    for t in TESTS:
        print()
        print(t.__name__ + ':')
        tmp = Path(tempfile.mkdtemp())
        try:
            t(tmp)
        except Exception as e:
            FAILED += 1
            print('  [BAD]  ' + t.__name__ + ' raised ' + type(e).__name__ + ': ' + str(e))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    print()
    print(str(PASSED) + ' passed, ' + str(FAILED) + ' failed')
    return 1 if FAILED else 0


if __name__ == '__main__':
    sys.exit(run())
