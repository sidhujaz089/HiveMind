#!/usr/bin/env python3
'''Fail-first oracle for t794: swift_prep.py must hash the PLE pieces it pairs the Swift
checkpoint with, append one row per piece to the integrity ledger, and refuse to build
swift-serve when a piece is not the bytes its pair says it is.

RED ON MAIN for one reason: projects/local-inference/swift_prep.py imports argparse, json, os,
shutil, sys and pathlib and nothing else, so the file cannot hash a tensor, and it defines no
tensor_sha256, ple_integrity, DTYPE_BYTES, PLE_BASE_DIR or PLE_INTEGRITY -- while its docstring
line 28 promises 'The other 4 are checked by tensor hash before serving'. Every test below
fails on a missing name, and main() reaches DST.mkdir having read no byte of the table.

No live state: every checkpoint, table and ledger is a synthetic safetensors file written under
tempfile.TemporaryDirectory. Nothing reads ~/models, nothing writes into the repository.

Run: python3 scripts/tests/test_swift_prep_ple.py
'''
import builtins
import hashlib
import importlib.util
import io
import json
import struct
import sys
import tempfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
MOD = REPO / 'projects' / 'local-inference' / 'swift_prep.py'
FAILURES = []

# The real PLE piece names, measured 2026-10-11 from the safetensors headers of
# ~/models/ple-table-fp8 (131 shards) and ~/models/swift-w4a16-autoround (81 shards): the
# intersection is four pieces, shards 0, 1, 126 and 127 of layer 1. The tensor NAME is the only
# usable key -- the two checkpoints number their shards differently, so file names never match.
PLE0 = 'model.language_model.layers.1.ple.ple_embedding.ngram_embedding.shard_0.weight'
PLE1 = 'model.language_model.layers.1.ple.ple_embedding.ngram_embedding.shard_127.weight'
QWEIGHT = 'model.language_model.layers.1.mlp.experts.0.down_proj.qweight'
PLE_MARK = 'ngram_embedding'
KEYS = {'at', 'tensor', 'swift_sha256', 'base_sha256', 'verdict', 'actor'}
READ_LIMIT = 2 * 1024 * 1024
REAL_IO_OPEN = io.open
REAL_BUILTIN_OPEN = builtins.open


def sha(blob):
    return hashlib.sha256(blob).hexdigest()


def blob_of(seed, n):
    return bytes((i * 7 + seed) % 251 for i in range(n))


def load_module():
    spec = importlib.util.spec_from_file_location('swift_prep_under_test', MOD)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def write_safetensors(path, tensors):
    '''{name: (dtype, shape, blob)} -> a real safetensors file: u64 LE header length, the JSON
    header (data_offsets relative to the start of the data section), then the payload.'''
    header = {}
    payload = b''
    off = 0
    for name, (dtype, shape, blob) in tensors.items():
        header[name] = {'dtype': dtype, 'shape': list(shape),
                        'data_offsets': [off, off + len(blob)]}
        payload += blob
        off += len(blob)
    hb = json.dumps(header).encode('utf-8')
    hb = hb + b' ' * (-len(hb) % 8)
    Path(path).write_bytes(struct.pack('<Q', len(hb)) + hb + payload)


def rows_of(path):
    lines = Path(path).read_text(encoding='utf-8').splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def test_tensor_sha256_hashes_exactly_the_tensor_byte_range():
    m = load_module()
    assert callable(getattr(m, 'tensor_sha256', None)), (
        'swift_prep.py defines no tensor_sha256: its imports include no hashlib, so nothing in '
        'the file can hash a tensor, while its docstring promises the check at line 28')
    f = m.tensor_sha256
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / 'model-00002-of-00081.safetensors'
        a = blob_of(1, 256)
        b = blob_of(2, 100)
        write_safetensors(p, {PLE0: ('F8_E4M3', [4, 64], a), QWEIGHT: ('BF16', [50, 2], b)})
        assert f(p, PLE0) == sha(a), (
            'tensor_sha256 must return sha256 over exactly that tensor byte range: not the file, '
            'not the header, not the whole data section')
        assert f(p, QWEIGHT) == sha(b), (
            'two tensors in one file must hash to two different values: a digest that ignores '
            'data_offsets cannot tell a rewritten piece from its neighbour in the same shard')
        absent = PLE0.replace('.layers.1.', '.layers.9.')
        try:
            f(p, absent)
            raise AssertionError('tensor_sha256 must raise KeyError on a tensor the file does not '
                                'name, so a pruned piece cannot pass as an unchanged one')
        except KeyError:
            pass


def test_tensor_sha256_reads_the_range_in_chunks():
    m = load_module()
    if not callable(getattr(m, 'tensor_sha256', None)):
        raise AssertionError('swift_prep.py defines no tensor_sha256: see the first test')
    f = m.tensor_sha256
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / 'model-00131-of-00131.safetensors'
        blob = bytes(10 * 1024 * 1024)
        write_safetensors(p, {PLE0: ('F8_E4M3', [4000, 640], blob)})
        sizes = []

        class Recorder:
            def __init__(self, fh):
                self._fh = fh

            def read(self, *args):
                if args:
                    sizes.append(args[0])
                return self._fh.read(*args)

            def __getattr__(self, name):
                return getattr(self._fh, name)

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return self._fh.__exit__(*exc)

        def wrapper(path, mode='rb', *args, **kw):
            return Recorder(REAL_IO_OPEN(path, mode, *args, **kw))

        io.open = wrapper
        builtins.open = wrapper
        try:
            got = f(p, PLE0)
        finally:
            io.open = REAL_IO_OPEN
            builtins.open = REAL_BUILTIN_OPEN
        assert got == sha(blob), 'the streamed hash must equal sha256 over the whole tensor'
        big = [n for n in sizes if isinstance(n, int) and n > READ_LIMIT]
        assert not big, (
            'tensor_sha256 asked for ' + str(max(big)) + ' bytes in one read(): the real pieces '
            'sit inside shards of about 1.7 GB and the CPU+GPU pool Swift shares with the engine '
            'is 121 GiB, so the byte range must be streamed with seek() plus bounded read(n)')


def test_a_matching_pair_records_one_row_per_piece_and_reports_nothing():
    m = load_module()
    assert callable(getattr(m, 'ple_integrity', None)), (
        'swift_prep.py defines no ple_integrity, so nothing appends the {at, tensor, swift_sha256, '
        'base_sha256, verdict, actor} row the RCA says is missing')
    with tempfile.TemporaryDirectory() as td:
        swift = Path(td) / 'swift'
        base = Path(td) / 'base'
        swift.mkdir()
        base.mkdir()
        s0 = blob_of(3, 1004)
        s1 = blob_of(4, 600)
        write_safetensors(swift / 'model-00002-of-00081.safetensors',
                          {PLE0: ('BF16', [1004], s0), QWEIGHT: ('BF16', [500], blob_of(5, 500))})
        write_safetensors(swift / 'model-00131-of-00081.safetensors',
                          {PLE1: ('BF16', [100, 6], s1)})
        write_safetensors(base / 'model-00005-of-00131.safetensors', {PLE0: ('BF16', [1004], s0)})
        write_safetensors(base / 'model-00127-of-00131.safetensors', {PLE1: ('BF16', [100, 6], s1)})
        led = Path(td) / 'ple-integrity.jsonl'
        problems = m.ple_integrity(swift_dir=swift, base_dir=base, ledger_path=led)
        assert problems == [], (
            'when every shared PLE piece hashes the same in both directories the check must report '
            'nothing: a gate that fires on a matching table gets bypassed inside a week. Got '
            + repr(problems))
        rows = rows_of(led)
        names = sorted(r['tensor'] for r in rows)
        assert names == sorted([PLE0, PLE1]), (
            'exactly one row per PLE piece the two directories share, and the non-PLE tensor must '
            'not be recorded: got ' + repr(names))
        assert all(set(r) == KEYS for r in rows), (
            'every row carries exactly the keys ' + repr(sorted(KEYS)) + ': got '
            + repr(sorted(rows[0])))
        assert all(r['verdict'] == 'identical' for r in rows), (
            'equal digests must record verdict identical: got '
            + repr(sorted(r['verdict'] for r in rows)))
        assert all(len(r['swift_sha256']) == 64 and len(r['base_sha256']) == 64 for r in rows), (
            'both digest fields are full sha256 hex: a prefix is a truncated record')
        assert all(r['swift_sha256'] == sha(s0 if r['tensor'] == PLE0 else s1) for r in rows), (
            'the recorded swift digest must be the digest of that tensors bytes, not of the file '
            'that holds it')
        assert all(r['base_sha256'] == r['swift_sha256'] for r in rows), (
            'an identical verdict requires the two recorded digests to be equal')
        assert all(r['actor'] == 'swift_prep' for r in rows), (
            'the row says who measured it: got ' + repr(sorted(r['actor'] for r in rows)))
        assert all(len(r['at']) >= 19 and 'T' in r['at'] for r in rows), (
            'the ledger is the dated measurement the RCA says does not exist: got '
            + repr(sorted(r['at'] for r in rows)))


def test_a_dtype_conversion_is_recorded_as_a_measurement_not_a_refusal():
    m = load_module()
    if not callable(getattr(m, 'ple_integrity', None)):
        raise AssertionError('swift_prep.py defines no ple_integrity: see the matching-pair test')
    widths = getattr(m, 'DTYPE_BYTES', None)
    assert isinstance(widths, dict), (
        'swift_prep.py defines no DTYPE_BYTES, and telling a dtype conversion from a rewrite needs '
        'the safetensors dtype byte widths')
    assert widths.get('BF16') == 2 and widths.get('F8_E4M3') == 1, (
        'DTYPE_BYTES must give BF16 2 bytes and F8_E4M3 1 byte per element: those are the two '
        'dtypes the real box carries. Got ' + repr(widths))
    with tempfile.TemporaryDirectory() as td:
        swift = Path(td) / 'swift'
        base = Path(td) / 'base'
        swift.mkdir()
        base.mkdir()
        bf16 = blob_of(6, 16000)
        fp8 = blob_of(7, 8000)
        write_safetensors(swift / 'model-00002-of-00081.safetensors',
                          {PLE0: ('BF16', [16000], bf16)})
        write_safetensors(base / 'model-00005-of-00131.safetensors',
                          {PLE0: ('F8_E4M3', [8000], fp8)})
        led = Path(td) / 'ple-integrity.jsonl'
        problems = m.ple_integrity(swift_dir=swift, base_dir=base, ledger_path=led)
        assert problems == [], (
            'the real box stores every piece as shape [2500012, 160] in BOTH checkpoints, F8_E4M3 '
            'in the base table and BF16 in Swifts, so the bytes differ at an equal element count. '
            'Refusing that is refusing the table Swift serves today. Got ' + repr(problems))
        rows = rows_of(led)
        assert len(rows) == 1 and rows[0]['verdict'] == 'dtype-converted', (
            'the row must record the measured relationship as dtype-converted: not identical (the '
            'bytes differ) and not mismatch. Got ' + repr(rows))
        assert rows[0]['swift_sha256'] == sha(bf16) and rows[0]['base_sha256'] == sha(fp8), (
            'both sides digests are recorded even when they differ, so the difference is auditable '
            'rather than asserted')
        swift2 = Path(td) / 'swift2'
        base2 = Path(td) / 'base2'
        swift2.mkdir()
        base2.mkdir()
        write_safetensors(swift2 / 'model-00002-of-00081.safetensors',
                          {PLE0: ('BF16', [16000], bf16)})
        write_safetensors(base2 / 'model-00005-of-00131.safetensors',
                          {PLE0: ('F8_E4M3', [16000], blob_of(8, 16000))})
        led2 = Path(td) / 'ple-integrity-2.jsonl'
        problems2 = m.ple_integrity(swift_dir=swift2, base_dir=base2, ledger_path=led2)
        assert problems2 != [], (
            'equal byte lengths at different dtypes are 8000 elements against 16000 elements: that '
            'is a rewrite, not a conversion, and it must be reported. Got ' + repr(problems2))
        rows2 = rows_of(led2)
        assert len(rows2) == 1 and rows2[0]['verdict'] == 'mismatch', (
            'the row must record verdict mismatch when the element counts differ: got ' + repr(rows2))


def test_a_rewritten_piece_is_reported_with_both_digests_and_a_mismatch_row():
    m = load_module()
    if not callable(getattr(m, 'ple_integrity', None)):
        raise AssertionError('swift_prep.py defines no ple_integrity: see the matching-pair test')
    with tempfile.TemporaryDirectory() as td:
        swift = Path(td) / 'swift'
        base = Path(td) / 'base'
        swift.mkdir()
        base.mkdir()
        s0 = blob_of(9, 600)
        moved = bytearray(s0)
        moved[7] = (moved[7] + 1) % 251
        write_safetensors(swift / 'model-00002-of-00081.safetensors', {PLE0: ('BF16', [600], s0)})
        write_safetensors(base / 'model-00005-of-00131.safetensors',
                          {PLE0: ('BF16', [600], bytes(moved))})
        led = Path(td) / 'ple-integrity.jsonl'
        problems = m.ple_integrity(swift_dir=swift, base_dir=base, ledger_path=led)
        assert len(problems) == 1, (
            'one differing piece must produce exactly one problem: got ' + repr(problems))
        assert PLE0 in problems[0], (
            'the problem must name the tensor by its full name, which is the only key the two '
            'checkpoints share: got ' + repr(problems))
        assert sha(s0) in problems[0] and sha(bytes(moved)) in problems[0], (
            'the problem must carry both full digests, so the next reader can re-derive it')
        rows = rows_of(led)
        assert len(rows) == 1 and rows[0]['verdict'] == 'mismatch', (
            'the ledger records what was measured, not what was hoped: got ' + repr(rows))
        assert rows[0]['swift_sha256'] == sha(s0) and rows[0]['base_sha256'] == sha(bytes(moved)), (
            'the row must carry the two digests the problem quotes')


def test_a_pruned_piece_is_reported_and_named():
    m = load_module()
    if not callable(getattr(m, 'ple_integrity', None)):
        raise AssertionError('swift_prep.py defines no ple_integrity: see the matching-pair test')
    with tempfile.TemporaryDirectory() as td:
        swift = Path(td) / 'swift'
        base = Path(td) / 'base'
        swift.mkdir()
        base.mkdir()
        s0 = blob_of(10, 512)
        write_safetensors(swift / 'model-00002-of-00081.safetensors', {PLE0: ('BF16', [512], s0)})
        write_safetensors(base / 'model-00005-of-00131.safetensors', {PLE0: ('BF16', [512], s0)})
        led = Path(td) / 'ple-integrity.jsonl'
        assert m.ple_integrity(swift_dir=swift, base_dir=base, ledger_path=led) == [], (
            'baseline: the matching pair must report nothing')
        (base / 'model-00005-of-00131.safetensors').unlink()
        led2 = Path(td) / 'ple-integrity-2.jsonl'
        problems2 = m.ple_integrity(swift_dir=swift, base_dir=base, ledger_path=led2)
        assert PLE0 in ' '.join(problems2), (
            'a piece the table no longer holds must be reported by name, not passed over in '
            'silence: a re-pulled table that quietly lost a piece is exactly the defect: got '
            + repr(problems2))
        assert rows_of(led2) == [], (
            'a piece missing on one side has no pair digest to record, so it is reported and not '
            'written as a row: got ' + repr(rows_of(led2)))


def test_the_ledger_is_appended_not_rewritten():
    m = load_module()
    if not callable(getattr(m, 'ple_integrity', None)):
        raise AssertionError('swift_prep.py defines no ple_integrity: see the matching-pair test')
    with tempfile.TemporaryDirectory() as td:
        swift = Path(td) / 'swift'
        base = Path(td) / 'base'
        swift.mkdir()
        base.mkdir()
        s0 = blob_of(11, 400)
        write_safetensors(swift / 'model-00002-of-00081.safetensors', {PLE0: ('BF16', [400], s0)})
        write_safetensors(base / 'model-00005-of-00131.safetensors', {PLE0: ('BF16', [400], s0)})
        led = Path(td) / 'ple-integrity.jsonl'
        assert m.ple_integrity(swift_dir=swift, base_dir=base, ledger_path=led) == []
        first = rows_of(led)
        assert len(first) == 1, 'one row for one piece: got ' + repr(first)
        changed = bytearray(s0)
        changed[0] = (changed[0] + 1) % 251
        write_safetensors(base / 'model-00005-of-00131.safetensors',
                          {PLE0: ('BF16', [400], bytes(changed))})
        assert len(m.ple_integrity(swift_dir=swift, base_dir=base, ledger_path=led)) == 1
        rows = rows_of(led)
        assert len(rows) == 2, (
            'the ledger is the history of measurements: a second run appends a row, it does not '
            'replace the file. Got ' + repr(rows))
        assert rows[0] == first[0], (
            'the earlier measurement must survive the later one, keys and digests and timestamp '
            'included')
        assert rows[1]['verdict'] == 'mismatch', (
            'the second run measured a moved byte and must say so: got ' + repr(rows[1]))


def _valid_checkpoint(td):
    '''A Swift-shaped checkpoint, small enough to build inside a temp dir, plus its base table.'''
    swift = Path(td) / 'swift-w4a16-autoround'
    base = Path(td) / 'ple-table-fp8'
    swift.mkdir()
    base.mkdir()
    (swift / 'DONE').write_text('')
    cfg = {'quantization_config': {'quant_method': 'auto-round',
                                   'packing_format': 'auto_round:auto_gptq',
                                   'bits': 4, 'group_size': 128, 'sym': True}}
    (swift / 'config.json').write_text(json.dumps(cfg))
    idx = {'metadata': {'total_size': 2048},
           'weight_map': {QWEIGHT: 'model-00001-of-00002.safetensors',
                          PLE0: 'model-00002-of-00002.safetensors'}}
    (swift / 'model.safetensors.index.json').write_text(json.dumps(idx))
    write_safetensors(swift / 'model-00001-of-00002.safetensors',
                      {QWEIGHT: ('BF16', [16], blob_of(12, 32))})
    s0 = blob_of(13, 2048)
    write_safetensors(swift / 'model-00002-of-00002.safetensors', {PLE0: ('BF16', [1024], s0)})
    write_safetensors(base / 'model-00005-of-00131.safetensors', {PLE0: ('BF16', [1024], s0)})
    return swift, base, s0


def _run_main(m, swift, dst, base, led):
    out, err = io.StringIO(), io.StringIO()
    src_saved, dst_saved = m.SRC, m.DST
    base_saved = getattr(m, 'PLE_BASE_DIR', None)
    led_saved = getattr(m, 'PLE_INTEGRITY', None)
    argv_saved = list(sys.argv)
    m.SRC, m.DST = swift, dst
    m.PLE_BASE_DIR, m.PLE_INTEGRITY = base, led
    sys.argv = ['swift_prep.py']
    try:
        with redirect_stdout(out), redirect_stderr(err):
            rc = m.main()
    finally:
        m.SRC, m.DST = src_saved, dst_saved
        m.PLE_BASE_DIR, m.PLE_INTEGRITY = base_saved, led_saved
        sys.argv = argv_saved
    return rc, out.getvalue() + err.getvalue()


def test_main_refuses_to_build_the_moment_a_piece_differs():
    m = load_module()
    assert callable(getattr(m, 'ple_integrity', None)), (
        'swift_prep.py defines no ple_integrity: see the matching-pair test')
    assert hasattr(m, 'PLE_BASE_DIR') and hasattr(m, 'PLE_INTEGRITY'), (
        'main() must consult module-level PLE_BASE_DIR and PLE_INTEGRITY, so the table it gates on '
        'can be pointed at a fixture instead of ~/models')
    with tempfile.TemporaryDirectory() as td:
        swift, base, s0 = _valid_checkpoint(td)
        dst = Path(td) / 'swift-serve'
        led = Path(td) / 'ple-integrity.jsonl'
        bad = bytearray(s0)
        bad[3] = (bad[3] + 1) % 251
        write_safetensors(base / 'model-00005-of-00131.safetensors',
                          {PLE0: ('BF16', [1024], bytes(bad))})
        rc, text = _run_main(m, swift, dst, base, led)
        assert rc not in (0, None), (
            'main() must not report success when a PLE piece is not the bytes its pair has: rc='
            + repr(rc))
        assert not dst.exists(), (
            'main() must refuse BEFORE it creates ' + str(dst) + ': a built swift-serve is a model '
            'that loads and serves, and the point of the gate is that a corrupted table never gets '
            'that far')
        assert PLE0 in text, 'the refusal must name the piece it refused on: got ' + repr(text)
        assert sha(s0) in text and sha(bytes(bad)) in text, (
            'the refusal must print both full digests, so the next reader can re-derive the '
            'difference: got ' + repr(text))
        rows = rows_of(led)
        assert len(rows) == 1 and rows[0]['verdict'] == 'mismatch', (
            'the refusal is only evidence if it is recorded: got ' + repr(rows))


def test_main_still_builds_the_serve_directory_when_the_pieces_match():
    m = load_module()
    if not callable(getattr(m, 'ple_integrity', None)):
        raise AssertionError('swift_prep.py defines no ple_integrity: see the refusal test')
    with tempfile.TemporaryDirectory() as td:
        swift, base, s0 = _valid_checkpoint(td)
        dst = Path(td) / 'swift-serve'
        led = Path(td) / 'ple-integrity.jsonl'
        rc, text = _run_main(m, swift, dst, base, led)
        assert rc == 0, (
            'a checkpoint whose PLE pieces match must still build, exactly as it does today: rc='
            + repr(rc) + ' output=' + repr(text))
        assert (dst / 'config.json').is_file(), (
            'the build must still write config.json: output=' + repr(text))
        cfg = json.loads((dst / 'config.json').read_text(encoding='utf-8'))
        assert cfg['quantization_config']['quant_method'] == 'gptq', (
            'the auto-round to gptq rewrite must survive: got '
            + repr(cfg.get('quantization_config')))
        idx = json.loads((dst / 'model.safetensors.index.json').read_text(encoding='utf-8'))
        assert all(PLE_MARK not in k for k in idx['weight_map']), (
            'the PLE entries must still be dropped from the index: got '
            + repr(sorted(idx['weight_map'])))
        assert QWEIGHT in idx['weight_map'], (
            'the non-PLE tensor must still be in the index: got ' + repr(sorted(idx['weight_map'])))
        assert (dst / 'model-00002-of-00002.safetensors').is_file(), (
            'the shards must still be linked into the serve directory: output=' + repr(text))
        rows = rows_of(led)
        assert len(rows) == 1 and rows[0]['verdict'] == 'identical', (
            'a clean build records the measurement it made: got ' + repr(rows))


TESTS = [test_tensor_sha256_hashes_exactly_the_tensor_byte_range,
         test_tensor_sha256_reads_the_range_in_chunks,
         test_a_matching_pair_records_one_row_per_piece_and_reports_nothing,
         test_a_dtype_conversion_is_recorded_as_a_measurement_not_a_refusal,
         test_a_rewritten_piece_is_reported_with_both_digests_and_a_mismatch_row,
         test_a_pruned_piece_is_reported_and_named,
         test_the_ledger_is_appended_not_rewritten,
         test_main_refuses_to_build_the_moment_a_piece_differs,
         test_main_still_builds_the_serve_directory_when_the_pieces_match]


def run(fn):
    try:
        fn()
        print('ok   ' + fn.__name__)
    except AssertionError as exc:
        FAILURES.append(fn.__name__ + ': ' + str(exc))
        print('FAIL ' + fn.__name__ + ': ' + str(exc))
    except Exception as exc:
        FAILURES.append(fn.__name__ + ': ' + type(exc).__name__ + ': ' + str(exc))
        print('FAIL ' + fn.__name__ + ': ' + type(exc).__name__ + ': ' + str(exc))


if __name__ == '__main__':
    for _fn in TESTS:
        run(_fn)
    print('')
    print('PASS' if not FAILURES else str(len(FAILURES)) + ' failure(s)')
    raise SystemExit(1 if FAILURES else 0)
