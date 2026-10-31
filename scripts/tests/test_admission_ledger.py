#!/usr/bin/env python3
"""t1044: the engine-admission ledger (workspace/run-logs/model-load-admission.jsonl).

RED ON MAIN because scripts/memory_ledger.py has no ADMISSION path, no admitted_slots and no
record_admission. Today a model load writes its memory footprint to
workspace/run-logs/model-loads-memory.jsonl and NOTHING records what a load ASKED FOR versus what
the engine granted, the context in force, the service pid, or the reason a request was clamped.

RCA workspace/rca/611a52eec9a4.md section 4 D1, verbatim: "No endpoint records requested-vs-
effective slots or the reason for a clamp." The evidence in that RCA: OLLAMA_NUM_PARALLEL=4 in
/etc/systemd/system/ollama.service.d/override.conf since 2026-10-02 13:49, and the Ollama journal
carrying 162 '-np 1' launch lines against 1 '-np 4', with every 27B load reporting n_slots = 1
through 2026-10-11. A capacity setting is in production with no record of what it did, so the
reason is undiagnosable from the record.

THE CASE THIS MODULE EXISTS FOR, pinned by test_parse_loads_carries_requested_and_granted below:
a journal load whose command asks for 4 slots (-np 4) and whose KV line reports 1/1 seqs is the
exact defect. Its row must show requested_np=4 and n_slots=1, with requested_np > n_slots visible.
An implementation that writes the same number into both fields passes that assert; one that
records only the granted count has no requested_np to compare, and fails.

Every number asserted here comes from a real artefact, not a placeholder:
  * 16021.47 MiB weights, 256.0 MiB per KV slot, 761.24 MiB, 1904.0 MiB and 29.2 GB -- read from
    workspace/run-logs/model-loads-memory.jsonl (191 rows; two different models, so a writer that
    hard-codes one model's arithmetic cannot pass the sweep).
  * the 12.0 GB reserve, the /1024 MiB-to-GB conversion and the weights + KV*slots + reserve sum
    come from memory_ledger.fits, which admitted_slots is required to call. The grants are derived
    from that function's own arithmetic: with 16021.47 MiB of weights (15.646 GB) and 256.0 MiB
    per slot (0.25 GB), 29.2 GB available leaves 1.554 GB for KV, i.e. 6 slots, so a request of 8
    is granted 6. Available 105.0 leaves room for 309, so 8 is granted 8 and there is NO clamp.
    Available 20.0 leaves nothing (weights alone exceed 20.0 - 12.0), so 8 is granted 0 and the
    reason names weights. For qwen3-embedding:0.6b, 761.24 MiB of weights (0.743 GB) and 1904.0
    MiB per slot (1.859 GB) against 29.2 GB leaves 16.456 GB, so a request of 16 slots (29.75 GB
    of KV) is granted 8. Four distinct input/output pairs across two real models.
  * the journal lines in JOURNAL_SAMPLE are copied from `journalctl -u ollama -o short-iso` on
    this box, including the `ollama[PID]:` unit field that carries the service pid (RCA D4: pid
    continuity is the only record of a restart applying a unit-file edit).

No live state: writes go to a tempfile.TemporaryDirectory only. No subprocess, no urlopen, no
model call. pytest is not installed; the __main__ block runs every test.
"""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import memory_ledger

# The RCA's field list is the contract. All twelve must be present in every row the writer
# accepts; None is the honest value for a fact this source cannot observe (nvidia-smi reports N/A
# on GB10 unified memory -- see the module's own docstring -- so gpu_free_mib is not knowable
# from the journal and must not be invented).
REQUIRED = ("at", "server", "engine", "model", "requested_np", "granted_np", "n_ctx",
            "n_slots", "ctx_total_requested", "service_pid", "gpu_free_mib", "clamp_reason")

# REAL, from workspace/run-logs/model-loads-memory.jsonl.
Q4_WEIGHTS_MIB = 16021.47
Q4_KV_MIB = 256.0
EMB_WEIGHTS_MIB = 761.24
EMB_KV_MIB = 1904.0
Q4_PLAN_GB = 29.2      # the 27B q4's own footprint at its planned context
BF16_PLAN_GB = 105.0   # a context roomy enough that 8 slots fit -- the no-clamp case

SHA_A = "c5396e06af294bd101b30dce59131a76d2b773e76950acc870eda801d3ab0515"
SHA_B = "06507c7b42688469c4e7298b0a1e16deff06caf291cf0a5b278c308249c3e439"


def _launch(pid, sha, ctx, np_, seqs, at, host_line=True):
    """One real-shaped load: the start line plus its model-buffer and KV-cache lines."""
    unit = f"{at} HiveMind ollama[{pid}]:"
    cmd = (f'{unit} time={at} level=INFO source=llama_server.go:433 '
           f'msg="starting llama-server" cmd="/usr/local/lib/ollama/llama-server '
           f'--model /usr/share/ollama/.ollama/models/blobs/sha256-{sha} '
           f'--port 37743 --host 127.0.0.1 --no-webui --offline '
           f'-c {ctx} -np {np_} --log-verbosity 4 --no-log-prefix"')
    return [
        f'{unit} msg="requested memory for running" '
        f'predicted="{Q4_PLAN_GB} GiB" predicted_num_ctx={ctx}',
        cmd,
        f'{unit} load_tensors:        CUDA0 model buffer size =   603.87 MiB',
        f'{unit} load_tensors:    CUDA_Host model buffer size =   157.37 MiB',
        f'{unit} llama_kv_cache: size = 1904.00 MiB ( 32768 cells,  28 layers,  {seqs}) '
        f'K (q8_0):  952.00 MiB, V (q8_0):  952.00 MiB',
    ]


# Three loads, three distinct (requested, granted) pairs. The third is the RCA's defect: the
# command asks for 4 slots, the engine's own KV line reports 1/1 seqs.
JOURNAL_SAMPLE = (
    _launch(239141, SHA_A, 131072, 4, "1/4 seqs", "2026-10-07T07:34:06-06:00")
    + _launch(251639, SHA_B, 32768, 1, "1/1 seqs", "2026-10-11T02:03:33-06:00")
    + _launch(1020974, SHA_B, 131072, 4, "1/1 seqs", "2026-10-11T12:24:12-06:00")
)

TAGS = {SHA_A: "qwen3.8:27b-mtp-q4_K_M", SHA_B: "qwen3-embedding:0.6b"}


def _missing():
    return [n for n in ("ADMISSION", "admitted_slots", "record_admission")
            if not hasattr(memory_ledger, n)]


def test_admitted_slots_grants_only_what_the_fit_check_fits():
    """The grant comes from fits(), on the box's real model costs."""
    granted, reason = memory_ledger.admitted_slots(Q4_WEIGHTS_MIB, Q4_KV_MIB, 8, Q4_PLAN_GB)
    assert granted == 6, (
        f"admitted_slots(16021.47 MiB, 256.0 MiB/slot, 8 requested, 29.2 GB available) must grant "
        f"6 slots (15.646 GB weights + 12.0 GB reserve leaves 1.554 GB = 6 x 0.25 GB), got {granted!r}")
    assert reason is not None, "a clamped request must carry a reason; got None"
    assert "8" in str(reason), (
        f"the clamp reason must name the requested slot count it reduced (8), got {reason!r}")
    assert str(Q4_PLAN_GB) in str(reason), (
        f"the clamp reason must name the available memory it measured against ({Q4_PLAN_GB}), "
        f"got {reason!r}")

    granted, reason = memory_ledger.admitted_slots(Q4_WEIGHTS_MIB, Q4_KV_MIB, 8, BF16_PLAN_GB)
    assert granted == 8, f"8 slots of 256 MiB fit in 105.0 GB; expected 8, got {granted!r}"
    assert reason is None, (
        f"a load that fits must NOT carry a clamp reason (it would claim a clamp that did not "
        f"happen), got {reason!r}")

    granted, reason = memory_ledger.admitted_slots(Q4_WEIGHTS_MIB, Q4_KV_MIB, 8, 20.0)
    assert granted == 0, f"16021.47 MiB of weights exceeds 20.0 - 12.0 GB; expected 0, got {granted!r}"
    assert reason is not None and "weight" in str(reason).lower(), (
        f"when zero slots fit the reason must name weights, not the KV cache, got {reason!r}")

    granted, _ = memory_ledger.admitted_slots(EMB_WEIGHTS_MIB, EMB_KV_MIB, 16, Q4_PLAN_GB)
    assert granted == 8, (
        f"qwen3-embedding:0.6b: 16 slots x 1904 MiB = 29.75 GB of KV against 16.456 GB of room, "
        f"so 16 requested grants 8, got {granted!r}")


def test_admitted_slots_never_grants_more_than_was_asked():
    """The grant is a reduction of the request, never a promotion of it."""
    for weights, kv, avail in ((Q4_WEIGHTS_MIB, Q4_KV_MIB, Q4_PLAN_GB),
                               (EMB_WEIGHTS_MIB, EMB_KV_MIB, Q4_PLAN_GB),
                               (Q4_WEIGHTS_MIB, Q4_KV_MIB, BF16_PLAN_GB)):
        for requested in (1, 4, 8, 16):
            granted, reason = memory_ledger.admitted_slots(weights, kv, requested, avail)
            assert 0 <= granted <= requested, (
                f"granted {granted} for a request of {requested} "
                f"(weights {weights}, kv {kv}, {avail} GB available) is outside 0..{requested}")
            assert (reason is None) == (granted == requested), (
                f"granted {granted} of {requested} with reason {reason!r}: a reason is required "
                f"exactly when the grant is reduced, and only then")


def test_record_admission_appends_one_complete_row_per_load():
    """One JSON line per load, all twelve fields, values preserved, appended not overwritten."""
    granted, reason = memory_ledger.admitted_slots(Q4_WEIGHTS_MIB, Q4_KV_MIB, 8, Q4_PLAN_GB)
    row = {"at": "2026-10-11T18:31:01+00:00", "server": "ollama", "engine": "llama-server",
           "model": "qwen3.8:27b-mtp-q4_K_M", "requested_np": 8, "granted_np": granted,
           "n_ctx": 32768, "n_slots": granted, "ctx_total_requested": 131072,
           "service_pid": 239141, "gpu_free_mib": None, "clamp_reason": reason}
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "model-load-admission.jsonl"
        memory_ledger.record_admission(row, path=path)
        memory_ledger.record_admission(row, path=path)
        assert path.is_file(), f"record_admission wrote no file at {path}"
        lines = path.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 2, (
            f"two loads must leave two appended lines, got {len(lines)} -- a ledger that "
            f"overwrites keeps no history")
        for n, line in enumerate(lines, 1):
            got = json.loads(line)
            absent = [k for k in REQUIRED if k not in got]
            assert not absent, f"row {n} is missing the required field(s) {absent}"
            assert len(got) == len(REQUIRED), (
                f"row {n} carries extra keys {sorted(set(got) - set(REQUIRED))}; the row is the "
                f"RCA's field list, not a free-form record")
            for k in REQUIRED:
                assert got[k] == row[k], f"row {n} field {k}: wrote {got[k]!r}, expected {row[k]!r}"
        assert json.loads(lines[0])["requested_np"] == 8, "requested_np must survive the round trip"
        assert json.loads(lines[0])["granted_np"] == 6, "granted_np must survive the round trip"
        assert json.loads(lines[0])["granted_np"] < json.loads(lines[0])["requested_np"], (
            "a clamped load must record granted < requested, which is the whole finding")
        assert json.loads(lines[0])["gpu_free_mib"] is None, (
            "gpu_free_mib is not knowable from the journal (nvidia-smi reports N/A on GB10) and "
            "must stay None rather than be invented")


def test_record_admission_refuses_a_row_that_cannot_be_diagnosed():
    """A missing field must be refused by name: a silent default is how a record hides a gap."""
    complete = {"at": "2026-10-11T18:31:01+00:00", "server": "ollama", "engine": "llama-server",
                 "model": "qwen3.8:27b-mtp-q4_K_M", "requested_np": 4, "granted_np": 1,
                 "n_ctx": 32768, "n_slots": 1, "ctx_total_requested": 131072,
                 "service_pid": 239141, "gpu_free_mib": None, "clamp_reason": "kv cache"}
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "model-load-admission.jsonl"
        for key in ("requested_np", "granted_np", "clamp_reason", "model", "n_ctx"):
            broken = {k: v for k, v in complete.items() if k != key}
            try:
                memory_ledger.record_admission(broken, path=path)
            except (ValueError, KeyError) as e:
                assert key in str(e), (
                    f"refusing a row missing {key!r} must name the missing key, got {type(e).__name__}: {e}")
            else:
                raise AssertionError(
                    f"record_admission accepted a row with no {key!r}; a row without it is the "
                    f"undiagnosable record this task exists to end")
        assert not path.exists() or path.read_text(encoding="utf-8") == "", (
            "a refused row must not be appended: a half-written admission row reads as a load")


def test_parse_loads_carries_requested_and_granted():
    """The journal already carries both halves; the parser must surface them (RCA D1)."""
    rows = memory_ledger.parse_loads(JOURNAL_SAMPLE, tags=TAGS)
    assert len(rows) == 3, (
        f"the sample journal holds three loads, parse_loads returned {len(rows)} "
        f"-- the existing memory row must keep being produced, extend it, do not replace it")
    pairs = [(r["requested_np"], r["n_slots"]) for r in rows]
    assert pairs == [(4, 4), (1, 1), (4, 1)], (
        f"requested/granted pairs from the real-shaped journal must be "
        f"[(4, 4), (1, 1), (4, 1)] (the third is the RCA's clamped load), got {pairs}")
    assert rows[2]["requested_np"] > rows[2]["n_slots"], (
        "the load that asked for 4 slots and got 1 must show requested_np > n_slots -- that "
        "inequality is the finding, and it is invisible today")
    assert rows[0]["n_ctx"] == 32768, (
        f"-c 131072 with -np 4 is 4 slots x 32768 (the RCA's ctx arithmetic), got "
        f"n_ctx={rows[0]['n_ctx']!r}")
    assert rows[0]["ctx_total_requested"] == 131072, (
        f"ctx_total_requested must be the -c value as launched, got "
        f"{rows[0]['ctx_total_requested']!r}")
    assert rows[2]["n_ctx"] == 32768, (
        f"-c 131072 with -np 4 is 32768 per slot, got n_ctx={rows[2]['n_ctx']!r}")
    assert rows[0]["service_pid"] == 239141 and rows[2]["service_pid"] == 1020974, (
        f"service_pid must come from the journal's own ollama[PID] unit field (RCA D4: pid "
        f"continuity is the restart record), got {rows[0]['service_pid']!r} / "
        f"{rows[2]['service_pid']!r}")
    assert rows[0]["at"] == "2026-10-07T07:34:06-06:00", (
        f"the existing 'at' field must keep its value, got {rows[0]['at']!r}")
    assert rows[0]["weights_mib"] == 761.24, (
        "the memory fields must keep being filled: 603.87 + 157.37 = 761.24 MiB, summed across "
        f"devices as today, got {rows[0]['weights_mib']!r}")
    assert rows[0]["tag"] == "qwen3.8:27b-mtp-q4_K_M", (
        f"'tag' must stay on the row: report() keys the per-model roll-up on r['tag'], got "
        f"{rows[0].get('tag')!r}")


def test_parsed_rows_are_admission_rows():
    """Wire the halves together: every row the parser emits must be writable as-is."""
    rows = memory_ledger.parse_loads(JOURNAL_SAMPLE, tags=TAGS)
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "model-load-admission.jsonl"
        for r in rows:
            memory_ledger.record_admission(r, path=path)
        lines = path.read_text(encoding="utf-8").splitlines()
        assert len(lines) == len(rows), (
            f"{len(rows)} parsed loads must produce {len(rows)} admission rows, got {len(lines)}")
        for n, line in enumerate(lines, 1):
            got = json.loads(line)
            absent = [k for k in REQUIRED if k not in got]
            assert not absent, f"parsed row {n} is not a complete admission row: {absent}"
            assert isinstance(got["requested_np"], int) and isinstance(got["n_slots"], int), (
                f"requested_np/n_slots must be ints, got {type(got['requested_np']).__name__}/"
                f"{type(got['n_slots']).__name__} -- they are counted per model, not logged as text")
            assert 1 <= got["requested_np"] <= 8, f"implausible requested_np {got['requested_np']!r}"


def test_admission_is_a_separate_ledger_not_a_second_copy():
    """The RCA: extend the existing record; do not create a second copy of it."""
    admiss = getattr(memory_ledger, "ADMISSION", None)
    assert admiss is not None, "ADMISSION is absent"
    assert str(admiss).endswith("workspace/run-logs/model-load-admission.jsonl"), (
        f"ADMISSION must be the path the task names, got {str(admiss)!r}")
    assert admiss.name != memory_ledger.LOADS.name, (
        f"ADMISSION must not be the memory ledger ({memory_ledger.LOADS.name}): the memory row "
        f"and the admission row are one load recorded once each, not two copies of one record")
    assert admiss.parent == memory_ledger.LOADS.parent, (
        f"ADMISSION belongs beside the memory ledger in workspace/run-logs, got {str(admiss.parent)!r}")


TESTS = [test_admitted_slots_grants_only_what_the_fit_check_fits,
          test_admitted_slots_never_grants_more_than_was_asked,
          test_record_admission_appends_one_complete_row_per_load,
          test_record_admission_refuses_a_row_that_cannot_be_diagnosed,
          test_parse_loads_carries_requested_and_granted,
          test_parsed_rows_are_admission_rows,
          test_admission_is_a_separate_ledger_not_a_second_copy]

if __name__ == "__main__":
    fails = 0
    missing = _missing()
    if missing:
        print(f"FAIL t1044: scripts/memory_ledger.py has no {', '.join(missing)} -- no endpoint "
              f"records requested-vs-effective slots (RCA 611a52eec9a4 section 4 D1)")
        fails += 1
    for t in TESTS:
        try:
            t()
        except Exception as e:                                  # noqa: BLE001 - report, never swallow
            fails += 1
            print(f"FAIL {t.__name__}: {type(e).__name__}: {e}")
        else:
            print(f"PASS {t.__name__}")
    print(f"{len(TESTS) - (fails - (1 if missing else 0))}/{len(TESTS)} test(s) passed"
          + ("; the writer is absent" if missing else ""))
    sys.exit(1 if fails else 0)
