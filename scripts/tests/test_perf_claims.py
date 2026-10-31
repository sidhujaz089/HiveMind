#!/usr/bin/env python3
"""t1078 oracle: every measured inference-performance claim gets a row in workspace/perf-claims.jsonl.

RED ON MAIN for exactly one reason: scripts/check_measurements.py has no recorder. Verified by a
full numbered read of that file on 2026-10-12: it defines ledger (line 47) and main (line 64) and
nothing else. So `import check_measurements` SUCCEEDS and every test below fails with AttributeError
naming record_claim, rows, claims_in, unmatched_claims or PERF_LEDGER -- the missing change, and
not an import bug of mine (the module exists, unlike the one earlier answers of this task assumed).
test_the_existing_figure_gate_still_works passes on main BY DESIGN: it pins behaviour the file
already has and must keep while the recorder is added to it.

The carrier is workspace/perf-claims.jsonl, named in the task. check_measurements.py already owns a
DIFFERENT ledger, workspace/measurements.jsonl, whose figures are cited in prose; the recorder adds
a second carrier beside it and must not repoint the first.

A row carries the twelve flat keys the task's CHANGE text and RCA section 5 specify: at, setting,
model, metric, value, baseline_value, num_gpu, num_ctx, concurrent, warm, task_id, command.
Conditions are flattened to top-level keys, so a claim can be matched to the measurement that
supports it, under the conditions it was taken under.

Every write goes into a tempfile.TemporaryDirectory. This oracle never touches the live ledger,
never calls a model, never runs a subprocess, never leaves a process behind.
"""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import check_measurements  # noqa: E402  the existing module this task extends


def _row(**over):
    row = {
        "setting": "num_gpu=0, num_ctx=8192, single stream, warm",
        "model": "qwen3.8:27b",
        "metric": "tok_s",
        "value": 6.5,
        "baseline_value": 7.3,
        "num_gpu": 0,
        "num_ctx": 8192,
        "concurrent": 1,
        "warm": True,
        "task_id": "t1078",
        "command": "python3 projects/local-inference/probe.py --model qwen3.8:27b --num-gpu 0",
    }
    row.update(over)
    return row


def test_perf_ledger_is_the_named_carrier():
    p = Path(str(check_measurements.PERF_LEDGER))
    assert p.name == "perf-claims.jsonl", p
    assert p.parent.name == "workspace", p
    assert Path(str(check_measurements.LEDGER)).name == "measurements.jsonl", (
        "the recorder adds a carrier; it must not repoint the ledger the figure gate reads"
    )


def test_record_appends_one_complete_row_per_claim():
    with tempfile.TemporaryDirectory() as d:
        ledger = Path(d) / "perf-claims.jsonl"
        check_measurements.record_claim(ledger, **_row())
        check_measurements.record_claim(
            ledger, **_row(model="gemma4:26b", value=1.4, baseline_value=6.5,
                           concurrent=2, warm=False))
        lines = [ln for ln in ledger.read_text(encoding="utf-8").splitlines() if ln.strip()]
        assert len(lines) == 2, f"one JSON row per call; got {len(lines)}: {lines}"
        first = json.loads(lines[0])
        second = json.loads(lines[1])
        want = _row()
        for key in ("setting", "model", "metric", "value", "baseline_value", "num_gpu",
                    "num_ctx", "concurrent", "warm", "task_id", "command"):
            assert first.get(key) == want[key], f"{key}: {first.get(key)!r} != {want[key]!r}"
        assert isinstance(first.get("at"), str) and first["at"], f"at missing: {first}"
        assert second["model"] == "gemma4:26b" and second["value"] == 1.4, second
        assert second["concurrent"] == 2 and second["warm"] is False, second
        assert first["model"] == "qwen3.8:27b", "record_claim appends; it never rewrites a row"
        assert first["concurrent"] == 1, "each call keeps its own conditions"


def test_a_claim_row_without_a_task_id_is_refused():
    with tempfile.TemporaryDirectory() as d:
        ledger = Path(d) / "perf-claims.jsonl"
        for bad in ("", None, "   "):
            try:
                check_measurements.record_claim(ledger, **_row(task_id=bad))
            except ValueError as exc:
                assert "task_id" in str(exc), f"ValueError must name the field: {exc}"
            else:
                raise AssertionError(
                    f"task_id={bad!r} was accepted: a claim row cannot be ownerless")
        assert not ledger.exists(), f"a refused claim must write nothing, found {ledger}"


def test_faster_slower_speedup_words_are_claims_and_are_reported_whole():
    sentence = "Putting model layers on the CPU usually makes them slower for qwen3.8:27b."
    claims = check_measurements.claims_in(sentence)
    assert len(claims) == 1, claims
    assert claims[0]["phrase"] == "slower", claims[0]
    assert claims[0]["model"] == "qwen3.8:27b", claims[0]
    assert claims[0]["quote"] == sentence, (
        f"quote is the WHOLE sentence, got {claims[0]['quote']!r}"
    )


def test_speedup_word_is_a_claim_for_the_model_named_in_its_own_sentence():
    claims = check_measurements.claims_in("Measured: a 2.1x speedup on gemma4:26b at num_gpu=0.")
    assert len(claims) == 1, claims
    assert claims[0]["phrase"] == "speedup", claims[0]
    assert claims[0]["model"] == "gemma4:26b", claims[0]
    assert check_measurements.unmatched_claims(claims, [_row()]) == claims, (
        "a gemma4:26b claim is not closed by a qwen3.8:27b row"
    )


def test_a_faster_claim_about_non_model_work_is_not_a_claim():
    text = "a scheduler tick went from 64 s to 3.6 s, so the suite is faster"
    assert check_measurements.claims_in(text) == [], check_measurements.claims_in(text)
    assert check_measurements.unmatched_claims(check_measurements.claims_in(text), []) == []


def test_a_row_of_another_metric_does_not_close_a_decode_rate_claim():
    claims = check_measurements.claims_in("qwen3.8:27b decodes slower at num_gpu=0.")
    assert len(claims) == 1, claims
    assert len(check_measurements.unmatched_claims(claims, [_row(metric="memory_gib")])) == 1, (
        "a memory row must not stand in for a decode-rate measurement"
    )
    assert check_measurements.unmatched_claims(claims, [_row(metric="tok_s")]) == [], (
        "the matching metric must close the claim"
    )


def test_rows_reads_every_row_whole_in_file_order():
    with tempfile.TemporaryDirectory() as d:
        ledger = Path(d) / "perf-claims.jsonl"
        check_measurements.record_claim(ledger, **_row())
        check_measurements.record_claim(ledger, **_row(model="gemma4:26b"))
        check_measurements.record_claim(ledger, **_row(model="phi4-mini:3.8b", value=12.0))
        got = check_measurements.rows(ledger)
        assert [r["model"] for r in got] == ["qwen3.8:27b", "gemma4:26b", "phi4-mini:3.8b"], got
        assert len(got) == 3, got
        assert got[2]["value"] == 12.0, got[2]
        assert set(got[0]) >= {
            "at", "setting", "model", "metric", "value", "baseline_value",
            "num_gpu", "num_ctx", "concurrent", "warm", "task_id", "command",
        }, sorted(got[0])


def test_a_recorded_row_closes_the_claim_it_belongs_to():
    with tempfile.TemporaryDirectory() as d:
        ledger = Path(d) / "perf-claims.jsonl"
        sentence = "Moving qwen3.8:27b layers to the CPU is slower on this box."
        claims = check_measurements.claims_in(sentence)
        assert len(claims) == 1, claims
        assert check_measurements.rows(ledger) == [], (
            "a missing ledger must read as empty, not raise"
        )
        assert check_measurements.unmatched_claims(claims, check_measurements.rows(ledger)) == claims, (
            "an empty ledger must not close a claim"
        )
        check_measurements.record_claim(ledger, **_row())
        assert check_measurements.unmatched_claims(claims, check_measurements.rows(ledger)) == [], (
            "the row a probe wrote must close the claim for the same model and metric"
        )


def test_the_existing_figure_gate_still_works():
    # The file's own reason to exist (its docstring): a figure in prose needs a row behind it.
    assert any(p.search("quoted 6.5 tok/s warm") for p in check_measurements.PATTERNS), (
        "PATTERNS lost the tok/s figure this file exists to police"
    )
    assert Path(str(check_measurements.LEDGER)).name == "measurements.jsonl"


if __name__ == "__main__":
    fails = []
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"ok   {name}")
            except Exception as exc:
                fails.append(name)
                print(f"FAIL {name}: {type(exc).__name__}: {exc}")
    print(f"{len(fails)} failure(s)")
    sys.exit(1 if fails else 0)
