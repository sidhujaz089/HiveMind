#!/usr/bin/env python3
"""t675: the full queue lives in the latest-tick file; the ledger row carries counts.

Oracle first written by the Manager's scope (bridge/t675, additive: the ledger row stayed full). Changed
2026-10-05 when the task was built, because 'additive' leaves the 65 MB of tick rows the task exists to remove:
the ledger row is now SLIM and the readers take the lists from the file. Hermetic: temp dirs, a fake record."""
import contextlib
import io
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "projects" / "app-repo"))

import dispatch_loop  # noqa: E402
import jsonl_cache  # noqa: E402

QUEUED = [{"kind": "production", "key": "chunk-a", "why": "queued"},
          {"kind": "preflight", "key": "chunk-b", "why": "queued"},
          {"kind": "preflight", "key": "chunk-d", "why": "queued"}]
SKIPPED = [{"chunk": "chunk-c", "why": "skipped"}]


def _tick(latest, record=None):
    rows = []
    full = dispatch_loop.write_tick_row(
        running=2, priority=3, started=1, preempted=[7], queued=QUEUED, skipped=SKIPPED, idle=False,
        record=record or rows.append, latest_tick_path=latest)
    return full, rows


def test_ledger_row_is_slim_with_counts():
    full, rows = _tick(Path(tempfile.mkdtemp()) / "latest.json")
    r = rows[0]
    assert r["event"] == "tick" and r["running"] == 2 and r["started"] == 1 and r["preempted"] == [7]
    assert "queued" not in r and "skipped" not in r, "the ledger row still carries the lists: " + str(sorted(r))
    assert (r["queued_n"], r["skipped_n"]) == (3, 1)
    assert r["queued_by_kind"] == {"production": 1, "preflight": 2}
    assert len(json.dumps(r)) < len(json.dumps(full)), "the slim row is not smaller"


def test_latest_file_carries_the_full_lists():
    p = Path(tempfile.mkdtemp()) / "latest.json"
    full, _ = _tick(p)
    data = json.loads(p.read_text())
    assert data["event"] == "tick" and data["queued"] == QUEUED and data["skipped"] == SKIPPED
    assert isinstance(data["at"], str) and data["at"]
    assert full["queued"] == QUEUED, "the returned row is the full tick"
    assert not list(p.parent.glob("*.tmp")), "the temp file was left behind (the write is not atomic)"


def test_unwritable_file_still_records_and_does_not_raise():
    blocker = Path(tempfile.mkdtemp()) / "afile"
    blocker.write_text("x")
    seen = []
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        _tick(blocker / "sub" / "latest.json", record=seen.append)       # parent is a file: mkdir fails
    assert len(seen) == 1, "the ledger row must still be recorded"
    assert "latest tick not written" in err.getvalue(), "the failure was not said"


def test_latest_tick_reads_the_file_first():
    d = Path(tempfile.mkdtemp())
    ledger = d / "dispatch-loop.jsonl"
    ledger.write_text(json.dumps({"event": "tick", "skipped": [{"chunk": "OLD", "why": "x"}], "queued": []}) + "\n")
    (d / "run-logs").mkdir()
    (d / "run-logs" / "dispatch-tick-latest.json").write_text(json.dumps(
        {"at": "t", "event": "tick", "skipped": SKIPPED, "queued": QUEUED}))
    t = jsonl_cache.latest_tick(ledger)
    assert t["skipped"] == SKIPPED and t["queued"] == QUEUED, "took the ledger's old tick over the file"


def test_latest_tick_falls_back_to_the_ledgers_last_full_row_and_skips_slim_rows():
    d = Path(tempfile.mkdtemp())
    ledger = d / "dispatch-loop.jsonl"
    old = {"event": "tick", "skipped": [{"chunk": "OLD", "why": "x"}], "queued": []}
    slim = {"event": "tick", "running": 1, "queued_n": 5, "skipped_n": 2}        # no lists: not a full tick
    ledger.write_text("\n".join(json.dumps(r) for r in (old, slim)) + "\n")
    assert jsonl_cache.latest_tick(ledger)["skipped"][0]["chunk"] == "OLD"
    assert jsonl_cache.latest_tick_file(ledger) is None, "no file: the file-only reader says so"
    (d / "empty.jsonl").write_text("")
    assert jsonl_cache.latest_tick(d / "empty.jsonl") == {}


def test_a_corrupt_file_is_said_and_the_ledger_is_used():
    d = Path(tempfile.mkdtemp())
    ledger = d / "dispatch-loop.jsonl"
    ledger.write_text(json.dumps({"event": "tick", "skipped": [{"chunk": "OLD", "why": "x"}], "queued": []}) + "\n")
    (d / "run-logs").mkdir()
    (d / "run-logs" / "dispatch-tick-latest.json").write_text("{not json")
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        t = jsonl_cache.latest_tick(ledger)
    assert t["skipped"][0]["chunk"] == "OLD" and "unreadable" in err.getvalue()


def test_feedback_last_tick_reads_the_file():
    import feedback
    d = Path(tempfile.mkdtemp())
    (d / "workspace" / "run-logs").mkdir(parents=True)
    (d / "workspace" / "dispatch-loop.jsonl").write_text(
        json.dumps({"event": "tick", "queued_n": 9, "skipped_n": 9}) + "\n")      # slim rows only
    (d / "workspace" / "run-logs" / "dispatch-tick-latest.json").write_text(json.dumps(
        {"at": "t", "event": "tick", "skipped": SKIPPED, "queued": QUEUED}))
    was = feedback.WORKSPACE
    feedback.WORKSPACE = d
    try:
        t = feedback._last_tick()
    finally:
        feedback.WORKSPACE = was
    assert t["queued"] == QUEUED, "the Hub reads the slim ledger row and loses the queue: " + str(sorted(t))


if __name__ == "__main__":
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except Exception as e:                                        # noqa: BLE001 -- reported
                failed += 1
                print(f"FAIL {name}: {type(e).__name__}: {e}")
    sys.exit(1 if failed else 0)
