#!/usr/bin/env python3
"""Fail-first oracle for t794 (serve.py): the pre-serve gate must read workspace/ple-integrity.jsonl
and REFUSE to start when the ~50 GB FP8 PLE table is not the table that was measured.

RED ON MAIN for one reason: serve.py defines no ple_table_problems() and no PLE_INTEGRITY, and
preflight_problems() -- the gate `start` refuses on and the gate `preflight` prints -- tests the
table only by the existence of its DONE marker. A re-pulled, pruned or byte-rewritten ple-table-fp8
passes every check on this box and serves a silently corrupted model.

No live state: the table, the ledger and every shard are synthetic safetensors files under
tempfile.TemporaryDirectory. The docker, ollama and /proc probes are stubbed by this file and
asserted to be asked about the real constants, so nothing here shells out, opens a socket, calls a
model or reads ~/models.

Run: python3 scripts/tests/test_serve_ple_integrity.py
"""
import hashlib
import importlib.util
import inspect
import json
import os
import struct
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
MOD = REPO / "projects" / "local-inference" / "serve.py"
FAILURES = []

# The four real PLE piece names, measured 2026-10-11 from the safetensors headers of
# ~/models/ple-table-fp8 (-of-00131) and ~/models/swift-w4a16-autoround (-of-00081): the
# intersection is exactly shards 0, 1, 126, 127 of layer 1. Two of them are enough to prove the
# reader keys on the tensor NAME -- the file names never match between the two checkpoints.
PLE0 = "model.language_model.layers.1.ple.ple_embedding.ngram_embedding.shard_0.weight"
PLE1 = "model.language_model.layers.1.ple.ple_embedding.ngram_embedding.shard_127.weight"
KEYS = ("at", "tensor", "swift_sha256", "base_sha256", "verdict", "actor")


def check(cond, msg):
    if not cond:
        FAILURES.append(msg)
        print("FAIL: " + msg)
    return cond


def load_module():
    spec = importlib.util.spec_from_file_location("serve_under_test", MOD)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def sha(blob):
    return hashlib.sha256(blob).hexdigest()


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


def row(tensor, base_sha, **over):
    r = {"at": "2026-10-11T00:00:00Z", "tensor": tensor, "swift_sha256": sha(b"swift"),
         "base_sha256": base_sha, "verdict": "identical", "actor": "swift_prep"}
    r.update(over)
    return r


def make_table(td, payloads, second=True):
    # Layout of the real table: shards named -of-00131, a DONE marker, FP8 dtype.
    d = Path(td) / "ple-table-fp8"
    d.mkdir()
    (d / "DONE").write_bytes(b"")
    write_safetensors(d / "model-00005-of-00131.safetensors", {PLE0: ("F8_E4M3", payloads[0])})
    if second:
        write_safetensors(d / "model-00006-of-00131.safetensors", {PLE1: ("F8_E4M3", payloads[1])})
    return d


def touch(path, ahead=100):
    t = time.time() + ahead
    os.utime(str(path), (t, t))


def write_ledger(path, lines):
    Path(path).write_text("".join(lines), encoding="utf-8")
    touch(path, 100)                      # measured AFTER the table, so staleness is not the cause
    return path


def stub_probes(m):
    # The gate's five existing probes, replaced so this file touches no docker, no socket, no
    # /proc. Each records the argument it was called with, so the suite can assert the gate is
    # still asked about the REAL constants and not about a fixture of the test's making.
    calls = []

    def image_present(ref):
        calls.append(("image_present", ref))
        return True

    def weights_are_safe(root):
        calls.append(("weights_are_safe", root))
        return True, "1 safetensors shard(s), no pickle weights"

    def ollama_resident(*a, **k):
        calls.append(("ollama_resident", None))
        return []

    def mem_available_gib():
        calls.append(("mem_available_gib", None))
        return 1e9

    def need_gib(*a, **k):
        calls.append(("need_gib", None))
        return 0.0

    for name, fn in (("image_present", image_present), ("weights_are_safe", weights_are_safe),
                     ("ollama_resident", ollama_resident), ("mem_available_gib", mem_available_gib),
                     ("need_gib", need_gib)):
        setattr(m, name, fn)
    return calls


def test_the_reader_exists_with_a_testable_signature():
    m = load_module()
    f = getattr(m, "ple_table_problems", None)
    if check(callable(f), "serve.py defines no ple_table_problems(): the file's own docstring "
                          "says the pre-serve gate is 'NOT OPTIONAL AND NOT ADVISORY', yet the "
                          "gate tests the table by its DONE marker alone and never reads a byte "
                          "of it."):
        params = set(inspect.signature(f).parameters)
        check({"ledger", "table_dir"} <= params,
              f"ple_table_problems() must take 'ledger' and 'table_dir' keyword arguments so a "
              f"caller (and this suite) can point it at a fixture: {sorted(params)}")
    led = getattr(m, "PLE_INTEGRITY", None)
    check(led is not None, "serve.py defines no PLE_INTEGRITY constant: preflight needs the path "
                           "of the file it is refusing on, and the RCA names it")
    if led is not None:
        check(Path(led).name == "ple-integrity.jsonl",
              f"the gate must read the file the RCA names; got {led}")
        check(Path(led).parent.name == "workspace", f"the ledger lives under workspace/; got {led}")


def test_a_matching_table_produces_no_problems():
    m = load_module()
    f = getattr(m, "ple_table_problems", None)
    if f is None:
        return
    with tempfile.TemporaryDirectory() as td:
        p0, p1 = bytes(range(128)) * 5, bytes(range(64)) * 7
        d = make_table(td, [p0, p1])
        led = write_ledger(Path(td) / "ple-integrity.jsonl",
                           [json.dumps(row(PLE0, sha(p0))) + "\n",
                            json.dumps(row(PLE1, sha(p1))) + "\n"])
        out = f(ledger=led, table_dir=d)
        check(out == [], f"a table whose bytes match the recorded digests must produce NO problems "
                         f"-- Swift serves today and swift.service starts it at boot, so a gate "
                         f"that fires on a matching table is bypassed within a week. Got {out!r}")


def test_an_absent_ledger_refuses_and_names_the_path():
    m = load_module()
    f = getattr(m, "ple_table_problems", None)
    if f is None:
        return
    with tempfile.TemporaryDirectory() as td:
        d = make_table(td, [bytes(64), bytes(64)])
        led = Path(td) / "ple-integrity.jsonl"
        out = f(ledger=led, table_dir=d)
        joined = "\n".join(out)
        check(bool(out), "preflight must refuse when no integrity file exists: an unmeasured table "
                         "is exactly the state the RCA's promise is about")
        check(str(led) in joined, f"the refusal must name the full path of the missing file so a "
                                  f"reader can create it; got {out!r}")


def test_a_ledger_with_no_parseable_row_refuses():
    m = load_module()
    f = getattr(m, "ple_table_problems", None)
    if f is None:
        return
    with tempfile.TemporaryDirectory() as td:
        d = make_table(td, [bytes(64), bytes(64)])
        for body in ("", "\n\n", "not json at all\n"):
            led = write_ledger(Path(td) / "empty.jsonl", [body])
            out = f(ledger=led, table_dir=d)
            check(bool(out), f"a ledger with no measurement in it must refuse, not pass "
                             f"(body={body!r}); got {out!r}")
            check(str(led) in "\n".join(out),
                  f"the refusal must name the file it found empty; got {out!r}")


def test_a_torn_last_line_refuses_and_names_the_line_number():
    # The ledger is appended to by a long-running prep step: the last line can be torn. Dropping
    # it silently is the failure mode this repository has a whole skill for.
    m = load_module()
    f = getattr(m, "ple_table_problems", None)
    if f is None:
        return
    with tempfile.TemporaryDirectory() as td:
        p0, p1 = bytes(range(128)) * 5, bytes(range(64)) * 7
        d = make_table(td, [p0, p1])
        led = write_ledger(Path(td) / "ple-integrity.jsonl",
                           [json.dumps(row(PLE0, sha(p0))) + "\n",
                            json.dumps(row(PLE1, sha(p1))) + "\n",
                            '{"at": "2026-10-11T00:00:00Z", "tensor": '])
        out = f(ledger=led, table_dir=d)
        joined = "\n".join(out)
        check(bool(out), f"a torn final row must refuse: an unreadable measurement is not a "
                         f"measurement; got {out!r}")
        check("3" in joined, f"the refusal must say WHICH line is unreadable (line 3 here); "
                             f"got {out!r}")
        check(PLE0 not in joined and PLE1 not in joined,
              f"the two readable rows must not be reported as problems; got {out!r}")
        check("JSONDecodeError" in joined or "json" in joined.lower(),
              f"the refusal must say what was caught, not just that a line was skipped; "
              f"got {out!r}")


def test_a_row_missing_a_required_key_refuses_naming_the_key_and_the_tensor():
    m = load_module()
    f = getattr(m, "ple_table_problems", None)
    if f is None:
        return
    with tempfile.TemporaryDirectory() as td:
        p0 = bytes(range(128)) * 5
        d = make_table(td, [p0])
        bad = row(PLE0, sha(p0))
        del bad["base_sha256"]
        led = write_ledger(Path(td) / "ple-integrity.jsonl", [json.dumps(bad) + "\n"])
        out = f(ledger=led, table_dir=d)
        joined = "\n".join(out)
        check(bool(out), f"a row with no recorded digest cannot be checked, so it must refuse; "
                         f"got {out!r}")
        check("base_sha256" in joined, f"the refusal must name the missing key; got {out!r}")
        check(PLE0 in joined, f"the refusal must name which tensor's row is incomplete; "
                              f"got {out!r}")


def test_a_rewritten_piece_refuses_naming_both_digests_and_not_the_intact_piece():
    m = load_module()
    f = getattr(m, "ple_table_problems", None)
    if f is None:
        return
    with tempfile.TemporaryDirectory() as td:
        p0, p1 = bytes(range(128)) * 5, bytes(range(64)) * 7
        d = make_table(td, [p0, p1])
        led = write_ledger(Path(td) / "ple-integrity.jsonl",
                           [json.dumps(row(PLE0, sha(p0))) + "\n",
                            json.dumps(row(PLE1, sha(p1))) + "\n"])
        check(f(ledger=led, table_dir=d) == [], "fixture sanity: matching digests must pass")
        write_safetensors(d / "model-00006-of-00131.safetensors", {PLE1: ("F8_E4M3", b"REWRITTEN")})
        touch(led)
        out = f(ledger=led, table_dir=d)
        joined = "\n".join(out)
        check(bool(out), "a recorded piece whose bytes have moved must refuse: the table is mounted "
                         "read-only and handed to the engine as VLLM_PLE_MMAP_DIR, so this is the "
                         "only check between a rewritten table and a silently corrupted model")
        check(PLE1 in joined, f"the refusal must name the tensor that moved; got {out!r}")
        check(PLE0 not in joined, f"the piece that still matches ({PLE0}) must NOT be named -- a "
                                  f"blanket message names nothing actionable")
        check(sha(p1) in joined, "the refusal must quote the RECORDED digest in full")
        check(sha(b"REWRITTEN") in joined, "the refusal must quote the digest read from disk in "
                                           "full, so a reader can tell a re-pull from a byte edit")


def test_a_pruned_piece_refuses():
    m = load_module()
    f = getattr(m, "ple_table_problems", None)
    if f is None:
        return
    with tempfile.TemporaryDirectory() as td:
        p0 = bytes(range(128)) * 5
        d = make_table(td, [p0], second=False)      # PLE1's shard is gone: the pruned-table case
        led = write_ledger(Path(td) / "ple-integrity.jsonl",
                           [json.dumps(row(PLE0, sha(p0))) + "\n",
                            json.dumps(row(PLE1, sha(b"gone"))) + "\n"])
        out = f(ledger=led, table_dir=d)
        joined = "\n".join(out)
        check(PLE1 in joined, f"a tensor the ledger records and the table no longer contains must "
                              f"refuse and be named by its full tensor name; got {out!r}")


def test_a_stale_ledger_refuses_and_names_the_newer_file():
    m = load_module()
    f = getattr(m, "ple_table_problems", None)
    if f is None:
        return
    with tempfile.TemporaryDirectory() as td:
        p0, p1 = bytes(range(128)) * 5, bytes(range(64)) * 7
        d = make_table(td, [p0, p1])
        led = Path(td) / "ple-integrity.jsonl"
        led.write_text(json.dumps(row(PLE0, sha(p0))) + "\n" + json.dumps(row(PLE1, sha(p1))) +
                       "\n", encoding="utf-8")
        old = time.time() - 3600
        os.utime(str(led), (old, old))              # measured BEFORE the table was re-pulled
        out = f(ledger=led, table_dir=d)
        joined = "\n".join(out)
        check(bool(out), "a ledger older than the table it certifies certifies nothing: a re-pull "
                         f"leaves the digests matching by accident of history; got {out!r}")
        check("model-00005-of-00131.safetensors" in joined or
              "model-00006-of-00131.safetensors" in joined,
              f"the refusal must name the table file that is newer than the ledger; got {out!r}")


def test_a_recorded_mismatch_verdict_refuses_even_when_the_bytes_match():
    m = load_module()
    f = getattr(m, "ple_table_problems", None)
    if f is None:
        return
    with tempfile.TemporaryDirectory() as td:
        p0 = bytes(range(128)) * 5
        d = make_table(td, [p0], second=False)
        led = write_ledger(Path(td) / "ple-integrity.jsonl",
                           [json.dumps(row(PLE0, sha(p0), verdict="mismatch")) + "\n"])
        out = f(ledger=led, table_dir=d)
        check(bool(out), "a row that recorded a corruption must refuse even after the bytes are "
                         f"made to match again -- the verdict is a finding, not a comment; "
                         f"got {out!r}")
        check(PLE0 in "\n".join(out), f"the refusal must name the tensor whose verdict is not "
                                      f"accepted; got {out!r}")


def test_a_shard_is_never_read_whole():
    # The real shards are ~1.7 GB each (measured) and the 121 GiB CPU/GPU pool Swift shares with
    # the engine has no room for a slurp. A reader that reads whole files is a memory-guard event
    # waiting for the next start.
    m = load_module()
    f = getattr(m, "ple_table_problems", None)
    if f is None:
        return
    with tempfile.TemporaryDirectory() as td:
        blob = b"\x01" * (10 * 1024 * 1024)
        d = make_table(td, [blob], second=False)
        led = write_ledger(Path(td) / "ple-integrity.jsonl", [json.dumps(row(PLE0, sha(blob))) +
                                                              "\n"])
        orig = Path.read_bytes

        def no_slurp(self):
            if self.stat().st_size > 8 * 1024 * 1024:
                raise AssertionError("read the whole file into memory")
            return orig(self)

        Path.read_bytes = no_slurp
        try:
            out = f(ledger=led, table_dir=d)
        except AssertionError as e:
            check(False, f"the reader must stream -- open/seek/read in ~1 MiB chunks, and read "
                         f"the header the same way ({e})")
            out = ["raised"]
        finally:
            Path.read_bytes = orig
        check(out == [], f"streaming must not change the verdict: a table that matches its "
                         f"recorded digests must pass; got {out!r}")


def test_preflight_wires_the_integrity_check_in():
    m = load_module()
    if getattr(m, "ple_table_problems", None) is None:
        check(False, "serve.py defines no ple_table_problems() for preflight_problems() to call")
        return
    with tempfile.TemporaryDirectory() as td:
        p0, p1 = bytes(range(128)) * 5, bytes(range(64)) * 7
        d = make_table(td, [p0, p1])
        led = write_ledger(Path(td) / "ple-integrity.jsonl",
                           [json.dumps(row(PLE0, sha(p0))) + "\n",
                            json.dumps(row(PLE1, sha(p1))) + "\n"])
        calls = stub_probes(m)
        m.TABLE_DIR, m.PLE_INTEGRITY = d, led
        out = m.preflight_problems()
        check(out == [], f"preflight must stay silent on a table whose bytes match the ledger, "
                         f"with the docker/ollama/memory probes stubbed to pass; got {out!r}")
        check(("image_present", m.IMAGE) in calls,
              f"the gate must still be asked about the real IMAGE constant; got {calls!r}")
        check(("weights_are_safe", m.MODEL_DIR) in calls,
              f"the gate must still be asked about the real MODEL_DIR; got {calls!r}")
        check(any(c[0] == "ollama_resident" for c in calls),
              f"the residency check must still run; got {calls!r}")
        check(any(c[0] == "mem_available_gib" for c in calls),
              f"the MemAvailable check must still run; got {calls!r}")
        # Move one recorded piece's bytes, then push the ledger's mtime forward so staleness is
        # not the reason the refusal fires.
        write_safetensors(d / "model-00006-of-00131.safetensors", {PLE1: ("F8_E4M3", b"MOVED")})
        touch(led, 200)
        out = m.preflight_problems()
        check(bool(out), "preflight_problems() returned [] with a table whose recorded piece has "
                         "been rewritten: the integrity check is computed but not used by the "
                         "gate, so the refusal is a decoration")
        check(any("ple-integrity" in p for p in out),
              f"the refusal must name the integrity file so a reader can find it; got {out!r}")
        check(PLE1 in "\n".join(out), f"the refusal must name the piece that moved; got {out!r}")


def test_cmd_preflight_exits_non_zero_on_a_failing_ledger():
    m = load_module()
    f = getattr(m, "cmd_preflight", None)
    if f is None:
        return
    with tempfile.TemporaryDirectory() as td:
        p0, p1 = bytes(range(128)) * 5, bytes(range(64)) * 7
        d = make_table(td, [p0, p1])
        led = write_ledger(Path(td) / "ple-integrity.jsonl",
                           [json.dumps(row(PLE0, sha(p0))) + "\n",
                            json.dumps(row(PLE1, sha(p1))) + "\n"])
        stub_probes(m)
        m.TABLE_DIR, m.PLE_INTEGRITY = d, led
        check(m.cmd_preflight(None) == 0,
              f"cmd_preflight must exit 0 on a table whose bytes match the ledger; "
              f"got {m.cmd_preflight(None)}")
        write_safetensors(d / "model-00005-of-00131.safetensors", {PLE0: ("F8_E4M3", b"REWRITTEN")})
        touch(led, 100)
        check(m.cmd_preflight(None) == 1,
              "cmd_preflight must exit 1 when a recorded piece's bytes have moved: that exit code "
              "is what `start` refuses on and what swift.service sees at boot")


if __name__ == "__main__":
    for _name, _fn in sorted(globals().items()):
        if _name.startswith("test_") and callable(_fn):
            print("== " + _name)
            _fn()
    print(f"\n{len(FAILURES)} failure(s)")
    sys.exit(1 if FAILURES else 0)
