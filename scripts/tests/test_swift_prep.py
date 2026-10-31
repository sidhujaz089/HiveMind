#!/usr/bin/env python3
"""Fail-first oracle for t794: swift_prep.py must hash the PLE table pieces it pairs the Swift
checkpoint with, append one row per piece to workspace/ple-integrity.jsonl, and refuse to build
when a piece's bytes are not what the pair says they should be.

RED ON MAIN for one reason and one only: the file hashes nothing. It imports argparse, json, os,
shutil, sys and pathlib -- no hashlib -- while its docstring (line 28) promises "The other 4 are
checked by tensor hash before serving". So tensor_sha256, ple_integrity, PLE_BASE_DIR and
PLE_INTEGRITY are all absent, and main() creates the serve directory without reading a byte of
the table it is paired with.

No live state: every checkpoint, table and ledger is a synthetic safetensors file written under
tempfile.TemporaryDirectory. Nothing reads ~/models and nothing writes into the repository.

Run: python3 scripts/tests/test_swift_prep.py
"""
import hashlib
import importlib.util
import io
import json
import os
import struct
import sys
import tempfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
MOD = REPO / "projects" / "local-inference" / "swift_prep.py"
FAILURES = []

# The four pieces swift_prep pairs with, measured 2026-10-11 by reading every safetensors header
# of ~/models/ple-table-fp8 (33 shards, -of-00131) and of ~/models/swift-w4a16-autoround
# (-of-00081): 129 ngram names on the base side, 4 on Swift's side, and the intersection is
# exactly these four. The tensor NAME is the only usable key -- the file names never match.
PLE0 = "model.language_model.layers.1.ple.ple_embedding.ngram_embedding.shard_0.weight"
PLE1 = "model.language_model.layers.1.ple.ple_embedding.ngram_embedding.shard_127.weight"
QWEIGHT = "model.language_model.layers.1.mlp.experts.0.down_proj.qweight"
KEYS = {"at", "tensor", "swift_sha256", "base_sha256", "verdict", "actor"}


def check(cond, msg):
    if not cond:
        FAILURES.append(msg)
        print("FAIL: " + msg)
    return cond


def load_module():
    spec = importlib.util.spec_from_file_location("swift_prep_under_test", MOD)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def write_safetensors(path, tensors):
    # {name: (dtype, bytes)} -> a real safetensors file: u64 LE header length, the JSON header
    # (data_offsets relative to the start of the data section), then the concatenated payload.
    header, payload, off = {}, b"", 0
    for name, (dtype, blob) in tensors.items():
        header[name] = {"dtype": dtype, "shape": [len(blob)],
                        "data_offsets": [off, off + len(blob)]}
        payload += blob
        off += len(blob)
    hb = json.dumps(header).encode()
    hb += b" " * (-len(hb) % 8)
    Path(path).write_bytes(struct.pack("<Q", len(hb)) + hb + payload)


def sha(blob):
    return hashlib.sha256(blob).hexdigest()


def want(m, name):
    f = getattr(m, name, None)
    if f is None:
        check(False, f"{MOD.name} defines no {name}: the file imports no hashlib, so nothing in "
                     f"it can hash a tensor. The docstring promise is prose with no implementing "
                     f"check.")
    return f


def rows_of(path):
    return [json.loads(line) for line in Path(path).read_text(encoding='utf-8').splitlines()
            if line.strip()]


def test_the_integrity_paths_are_module_constants():
    m = load_module()
    for name in ("PLE_BASE_DIR", "PLE_INTEGRITY"):
        check(getattr(m, name, None) is not None,
              f"swift_prep.py defines no {name} constant: main() must read the base table and the "
              f"ledger from module globals, so a test (and an operator) can point them at a "
              f"fixture instead of a live model directory.")
    if getattr(m, "PLE_INTEGRITY", None) is not None:
        check(Path(m.PLE_INTEGRITY).name == "ple-integrity.jsonl",
              f"the ledger must be the file the RCA names; got {m.PLE_INTEGRITY}")
        check(Path(m.PLE_INTEGRITY).parent.name == "workspace",
              f"the ledger must live under the repository's workspace/; got {m.PLE_INTEGRITY}")
    if getattr(m, "PLE_BASE_DIR", None) is not None:
        check(Path(m.PLE_BASE_DIR).name == "ple-table-fp8",
              f"PLE_BASE_DIR must be the base FP8 table the recipe mounts read-only; "
              f"got {m.PLE_BASE_DIR}")


def test_tensor_sha256_hashes_exactly_the_tensor_bytes():
    m = load_module()
    f = want(m, "tensor_sha256")
    if f is None:
        return
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "model-00005-of-00131.safetensors"
        a, b = bytes(range(256)) * 40, bytes(range(100))
        write_safetensors(p, {PLE0: ("F8_E4M3", a), QWEIGHT: ("BF16", b)})
        check(f(p, PLE0) == sha(a),
              "tensor_sha256 must return sha256 of exactly that tensor's byte range -- not of the "
              "file, not the header, not the whole data section")
        check(f(p, QWEIGHT) == sha(b),
              "two tensors in one file must not hash to the same value: a digest that ignores "
              "data_offsets cannot tell a rewritten piece from its neighbour")
        try:
            f(p, "model.language_model.layers.9.ple.ple_embedding.ngram_embedding.shard_9.weight")
            check(False, "tensor_sha256 must raise KeyError on a tensor the file does not name, so "
                         "a pruned piece cannot pass as an unchanged one")
        except KeyError:
            pass


def test_tensor_sha256_streams_the_byte_range():
    m = load_module()
    f = want(m, "tensor_sha256")
    if f is None:
        return
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "model-00005-of-00131.safetensors"
        blob = b"\x01" * (10 * 1024 * 1024)
        write_safetensors(p, {PLE0: ("F8_E4M3", blob)})
        orig = Path.read_bytes

        def no_slurp(self):
            if self.stat().st_size > 8 * 1024 * 1024:
                raise AssertionError("read the whole file into memory")
            return orig(self)

        Path.read_bytes = no_slurp
        try:
            got = f(p, PLE0)
        except AssertionError as e:
            check(False, f"tensor_sha256 must stream (open/seek/read in ~1 MiB chunks, header "
                         f"included): the real pieces are 400,001,920 bytes inside 1.6 GB shards "
                         f"and the 121 GiB pool Swift shares with the engine has no room for a "
                         f"slurp ({e})")
            got = None
        finally:
            Path.read_bytes = orig
        check(got == sha(blob), "the streamed hash must equal sha256 over the whole tensor")


def test_a_matching_table_records_one_row_per_piece_and_reports_nothing():
    m = load_module()
    f = want(m, "ple_integrity")
    if f is None:
        return
    with tempfile.TemporaryDirectory() as td:
        swift, base = Path(td) / "swift", Path(td) / "base"
        swift.mkdir()
        base.mkdir()
        s0, s1 = bytes(range(251)) * 4, bytes(range(199)) * 3
        write_safetensors(swift / "model-00002-of-00081.safetensors",
                          {PLE0: ("BF16", s0), QWEIGHT: ("BF16", s0), PLE1: ("BF16", s1)})
        write_safetensors(base / "model-00005-of-00131.safetensors", {PLE0: ("F8_E4M3", s0)})
        write_safetensors(base / "model-00006-of-00131.safetensors", {PLE1: ("F8_E4M3", s1)})
        led = Path(td) / "ple-integrity.jsonl"
        problems = f(swift_dir=swift, base_dir=base, ledger_path=led)
        check(problems == [], f"when every shared PLE piece hashes the same in both directories "
                              f"the check must report nothing: a gate that fires on a matching "
                              f"table is bypassed within a week. Got {problems!r}")
        rows = rows_of(led)
        check(len(rows) == 2, f"exactly one ledger row per PLE piece shared by the two "
                              f"directories (2 names here); got {len(rows)} row(s): {rows!r}")
        check({r["tensor"] for r in rows} == {PLE0, PLE1},
              f"the rows must name the PLE pieces by their full tensor name, and the non-PLE "
              f"tensor must not be recorded; got {sorted(r['tensor'] for r in rows)}")
        check(all(set(r) == KEYS for r in rows),
              f"every row carries exactly the keys the RCA names, {sorted(KEYS)}; "
              f"got {sorted(rows[0]) if rows else 'no rows'}")
        check(all(r["verdict"] == "identical" for r in rows),
              f"equal digests must record verdict 'identical'; "
              f"got {sorted(r['verdict'] for r in rows)}")
        check(all(len(r["swift_sha256"]) == 64 and len(r["base_sha256"]) == 64 for r in rows),
              "both digest fields are full sha256 hex -- a prefix is a truncated record")
        check(all(r["swift_sha256"] == (sha(s0) if r["tensor"] == PLE0 else sha(s1))
                  for r in rows),
              "the recorded swift digest must be the digest of THAT tensor's bytes")
        check(len({r["swift_sha256"] for r in rows}) == 2,
              "two different tensors must produce two different digests; equal values mean the "
              "hash is not keyed to the tensor")
        check(all(r["actor"] == "swift_prep" for r in rows),
              f"the row must say who measured it; got {sorted(r['actor'] for r in rows)}")
        check(all(len(r["at"]) >= 19 and "T" in r["at"] for r in rows),
              f"the ledger is the dated measurement the RCA's dead end D1 says is missing; "
              f"got {sorted(r['at'] for r in rows)}")


def test_the_verdict_records_the_measured_dtype_conversion_as_a_measurement_not_a_refusal():
    # Measured 2026-10-11: all four pieces are shape [2500012, 160] in BOTH checkpoints, F8_E4M3
    # (400,001,920 bytes) in the base table and BF16 (800,003,840 bytes) in Swift's. They are NOT
    # byte-identical. A check that demanded byte-identity would refuse the table Swift serves
    # from today -- so the verdict distinguishes a dtype conversion from a corruption, and both
    # digests are recorded either way.
    m = load_module()
    f = want(m, "ple_integrity")
    if f is None:
        return
    with tempfile.TemporaryDirectory() as td:
        swift, base = Path(td) / "swift", Path(td) / "base"
        swift.mkdir()
        base.mkdir()
        bf16, fp8 = bytes(8000), bytes(4000)
        write_safetensors(swift / "model-00002-of-00081.safetensors", {PLE0: ("BF16", bf16)})
        write_safetensors(base / "model-00005-of-00131.safetensors", {PLE0: ("F8_E4M3", fp8)})
        led = Path(td) / "ple-integrity.jsonl"
        problems = f(swift_dir=swift, base_dir=base, ledger_path=led)
        check(problems == [], f"a dtype conversion at the same element count is a MEASUREMENT, not "
                              f"corruption: refusing on it would break Swift, which serves today "
                              f"and starts at boot. Got {problems!r}")
        rows = rows_of(led)
        check(len(rows) == 1 and rows[0]["verdict"] == "dtype-converted",
              f"the row must record the measured relationship as verdict 'dtype-converted', "
              f"not 'identical' (they are not byte-identical) and not 'mismatch'; "
              f"got {rows!r}")
        check(rows[0]["swift_sha256"] == sha(bf16) and rows[0]["base_sha256"] == sha(fp8),
              "both sides' digests are recorded even when they differ, so the difference is "
              "auditable rather than asserted")


def test_a_rewritten_piece_is_refused_and_names_the_tensor_and_both_digests():
    m = load_module()
    f = want(m, "ple_integrity")
    if f is None:
        return
    with tempfile.TemporaryDirectory() as td:
        swift, base = Path(td) / "swift", Path(td) / "base"
        swift.mkdir()
        base.mkdir()
        s0 = bytes(range(251)) * 4
        moved = bytearray(s0)
        moved[7] = 7                      # one byte moved in a 400 MB tensor
        s1 = bytes(range(199)) * 3
        write_safetensors(swift / "model-00002-of-00081.safetensors",
                          {PLE0: ("F8_E4M3", s0), PLE1: ("F8_E4M3", s1)})
        write_safetensors(base / "model-00005-of-00131.safetensors", {PLE0: ("F8_E4M3", bytes(moved))})
        write_safetensors(base / "model-00006-of-00131.safetensors", {PLE1: ("F8_E4M3", s1)})
        led = Path(td) / "ple-integrity.jsonl"
        problems = f(swift_dir=swift, base_dir=base, ledger_path=led)
        joined = "\n".join(problems)
        check(bool(problems), "a PLE piece whose bytes differ from its pair must produce a "
                              "problem: swift_prep builds the serve directory that is paired with "
                              "this table, so a silent pass hands the engine a corrupted model")
        check(PLE0 in joined, f"the refusal must name the tensor whose bytes differ; got {problems!r}")
        check(PLE1 not in joined, f"the piece whose bytes match ({PLE1}) must NOT be named: a "
                                  f"blanket message names nothing actionable")
        check(sha(s0) in joined, "the refusal must quote the digest computed from the checkpoint")
        check(sha(bytes(moved)) in joined,
              "the refusal must quote the digest read from the table, so a reader can tell a "
              "re-pull from a byte rewrite -- and in full, never a prefix")
        rows = [r for r in rows_of(led) if r["tensor"] == PLE0]
        check(len(rows) == 1 and rows[0]["verdict"] == "mismatch",
              f"a differing piece must be RECORDED with verdict 'mismatch', not only refused "
              f"on stdout (the record outlives the terminal); got {rows!r}")


def test_a_piece_missing_from_the_base_table_is_refused():
    m = load_module()
    f = want(m, "ple_integrity")
    if f is None:
        return
    with tempfile.TemporaryDirectory() as td:
        swift, base = Path(td) / "swift", Path(td) / "base"
        swift.mkdir()
        base.mkdir()
        s0, s1 = bytes(range(251)) * 4, bytes(range(199)) * 3
        write_safetensors(swift / "model-00002-of-00081.safetensors",
                          {PLE0: ("BF16", s0), PLE1: ("BF16", s1)})
        write_safetensors(base / "model-00006-of-00131.safetensors", {PLE1: ("BF16", s1)})
        led = Path(td) / "ple-integrity.jsonl"
        problems = f(swift_dir=swift, base_dir=base, ledger_path=led)
        joined = "\n".join(problems)
        check(PLE0 in joined, f"a PLE piece the checkpoint names and the table does not contain is "
                              f"the pruned-table case the RCA names -- it must be refused and "
                              f"named, not silently dropped by iterating the intersection; "
                              f"got {problems!r}")
        check([r["tensor"] for r in rows_of(led)] == [PLE1],
              "the piece that exists on both sides is still measured and recorded")


def test_the_ledger_is_appended_not_rewritten():
    m = load_module()
    f = want(m, "ple_integrity")
    if f is None:
        return
    with tempfile.TemporaryDirectory() as td:
        swift, base = Path(td) / "swift", Path(td) / "base"
        swift.mkdir()
        base.mkdir()
        s0 = bytes(range(251)) * 4
        write_safetensors(swift / "model-00002-of-00081.safetensors", {PLE0: ("BF16", s0)})
        write_safetensors(base / "model-00005-of-00131.safetensors", {PLE0: ("BF16", s0)})
        led = Path(td) / "ple-integrity.jsonl"
        first = f(swift_dir=swift, base_dir=base, ledger_path=led)
        rows_after_first = rows_of(led)
        f(swift_dir=swift, base_dir=base, ledger_path=led)
        rows_after_second = rows_of(led)
        check(first == [] and len(rows_after_first) == 1,
              f"the first run records 1 row; got {rows_after_first!r}")
        check(len(rows_after_second) == 2,
              f"a re-run must APPEND -- the ledger is the audit trail of every time the table was "
              f"measured, so a re-check cannot erase the history; got {len(rows_after_second)}")
        check(rows_after_second[:1] == rows_after_first,
              "appending must not rewrite the rows already recorded")


def _build_fixture(td, corrupt=False):
    # A Swift checkpoint and a base FP8 table small enough to hash in a test, laid out the way the
    # real ones are (different shard counts, the same PLE tensor names, index naming the shards).
    src = Path(td) / "swift-w4a16-autoround"
    src.mkdir()
    base = Path(td) / "ple-table-fp8"
    base.mkdir()
    (src / "DONE").write_bytes(b"")
    (src / "config.json").write_text(json.dumps({"quantization_config": {
        "quant_method": "auto-round", "packing_format": "auto_round:auto_gptq",
        "bits": 4, "group_size": 128, "sym": True}}), encoding="utf-8")
    shard = "model-00001-of-00001.safetensors"
    (src / "model.safetensors.index.json").write_text(json.dumps(
        {"metadata": {"total_size": 16}, "weight_map": {
            QWEIGHT: shard, PLE0: "model-00002-of-00001.safetensors"}}), encoding="utf-8")
    write_safetensors(src / shard, {QWEIGHT: ("BF16", bytes(16))})
    s0 = bytes(range(251)) * 4
    pair = bytearray(s0)
    if corrupt:
        pair[7] = 7
    write_safetensors(src / "model-00002-of-00001.safetensors", {PLE0: ("BF16", s0)})
    write_safetensors(base / "model-00005-of-00131.safetensors", {PLE0: ("BF16", bytes(pair))})
    return src, base


def _run_main(m):
    buf = io.StringIO()
    argv = list(sys.argv)
    sys.argv = ["swift_prep.py"]
    try:
        with redirect_stdout(buf), redirect_stderr(buf):
            rc = m.main()
    finally:
        sys.argv = argv
    return rc, buf.getvalue()


def test_main_refuses_to_build_on_a_mismatch():
    m = load_module()
    if getattr(m, "ple_integrity", None) is None:
        check(False, "swift_prep.py defines no ple_integrity() for main() to call: today main() "
                     "reaches DST.mkdir with 7079 bytes of file and no hashlib, so the promise at "
                     "line 28 is the only thing standing behind the table.")
        return
    with tempfile.TemporaryDirectory() as td:
        src, base = _build_fixture(td, corrupt=True)
        dst, led = Path(td) / "swift-serve", Path(td) / "ple-integrity.jsonl"
        m.SRC, m.DST, m.PLE_BASE_DIR, m.PLE_INTEGRITY = src, dst, base, led
        rc, out = _run_main(m)
        check(rc not in (0, None), f"main() must exit non-zero when a PLE piece's bytes differ "
                                   f"from its pair; got rc={rc!r}")
        check(PLE0 in out, f"main() must print the tensor whose bytes differ so the operator can "
                           f"act on the refusal; got {out!r}")
        check(sha(bytes(range(251)) * 4) in out, "the printed refusal must quote the digests it "
                                                "compared, in full")
        check(not dst.exists(), f"main() must refuse BEFORE creating the serve directory: a built "
                                f"swift-serve over a corrupted table is what serve.py starts next; "
                                f"{dst} exists")
        rows = rows_of(led) if led.is_file() else []
        check(any(r["tensor"] == PLE0 and r["verdict"] == "mismatch" for r in rows),
              f"the refused run must leave its measurement in the ledger, not only on stdout; "
              f"got {rows!r}")


def test_main_still_builds_when_the_pieces_match():
    m = load_module()
    if getattr(m, "ple_integrity", None) is None:
        check(False, "swift_prep.py defines no ple_integrity() for main() to call")
        return
    with tempfile.TemporaryDirectory() as td:
        src, base = _build_fixture(td, corrupt=False)
        dst, led = Path(td) / "swift-serve", Path(td) / "ple-integrity.jsonl"
        m.SRC, m.DST, m.PLE_BASE_DIR, m.PLE_INTEGRITY = src, dst, base, led
        rc, out = _run_main(m)
        check(rc == 0, f"the integrity check must be silent when the pieces hash the same, and "
                      f"the build must go through: rc={rc!r} out={out!r}")
        check((dst / "config.json").is_file() and (dst / "model.safetensors.index.json").is_file(),
              "the build must still write config.json and the PLE-stripped index")
        cfg = json.loads((dst / "config.json").read_text(encoding="utf-8"))
        check(cfg["quantization_config"]["quant_method"] == "gptq",
              "the auto-round -> gptq rewrite must still happen")
        idx = json.loads((dst / "model.safetensors.index.json").read_text(encoding="utf-8"))
        check(PLE0 not in idx["weight_map"] and QWEIGHT in idx["weight_map"],
              "the index must still drop every ngram_embedding entry and keep the rest")
        rows = rows_of(led)
        check(len(rows) == 1 and rows[0]["verdict"] == "identical",
              f"the passing run records its measurement; got {rows!r}")


if __name__ == "__main__":
    for _name, _fn in sorted(globals().items()):
        if _name.startswith("test_") and callable(_fn):
            print("== " + _name)
            _fn()
    print(f"\n{len(FAILURES)} failure(s)")
    sys.exit(1 if FAILURES else 0)
