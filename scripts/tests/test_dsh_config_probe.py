#!/usr/bin/env python3
"""Fail-first oracle for scripts/dsh_config_probe.py (t1353).

Run:  python3 scripts/tests/test_dsh_config_probe.py
Red on main: `import dsh_config_probe` raises ModuleNotFoundError because the
module does not exist yet. Claude must create it before the build can proceed.

What a person would check by hand, and what this therefore asserts:
  1. newest_session_record finds the NEWEST record by mtime across BOTH v3
     and v4 files and both session-dir naming shapes (session-<uuid> and
     bare <uuid>), with an older record carrying DIFFERENT values;
  2. decode_record decompresses zstd and returns the JSONL text; a corrupt
     file raises SystemExit naming the file (never returns empty text);
  3. parse_resolved extracts model, maxTokens, contextWindow from the
     request/header and request/context lines; an ABSENT timeout is None
     with 'timeout' in the missing list (never invented);
  4. record_row APPENDS one JSON line, does not overwrite existing rows;
  5. the probe is read-only on the dsh home tree: nothing new appears
     under it after a full find+decode+parse+record run.

No model calls, no live server, no fixed /tmp paths.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))
import dsh_config_probe as probe  # noqa: E402

if shutil.which("zstd") is None:
    sys.stderr.write("zstd CLI not found -- cannot build fixtures\n")
    sys.exit(2)


def make_record(max_tokens=32768, context_window=262144,
                model="qwen3.8:27b-mtp-bf16"):
    recs = [
        {"type": "request/header", "seq": 12, "data": {
            "header": {"config": {"provider": "ollama",
                                  "model": model,
                                  "maxTokens": max_tokens},
                       "adapterDefaults": {"maxTokens": True}}}},
        {"type": "request/context", "seq": 13, "data": {
            "provider": "ollama",
            "model": model,
            "contextWindow": context_window}},
    ]
    return "\n".join(json.dumps(r) for r in recs) + "\n"


def write_session(dsh, session_dir, version, text, mtime=None):
    sess = dsh / "sessions" / session_dir
    sess.mkdir(parents=True, exist_ok=True)
    plain = sess / f"session.v{version}.jsonl"
    plain.write_text(text)
    subprocess.run(["zstd", "-q", str(plain)], check=True, timeout=120)
    zfile = sess / f"session.v{version}.jsonl.zstd"
    if mtime is not None:
        time.utime(str(zfile), (mtime, mtime))
    return zfile


def test_finds_newest_across_v3_v4_and_both_dir_shapes():
    with tempfile.TemporaryDirectory() as tmp:
        dsh = Path(tmp) / "dsh"
        write_session(dsh, "2827b904-1f68-455b-ab73-391a0c92decf",
                      "4", make_record(4096, 131072), mtime=1_700_000_000)
        write_session(dsh, "session-f1776b49-c813-4194-a141-9f158bea671a",
                      "3", make_record(32768, 262144), mtime=1_700_001_000)
        got = probe.newest_session_record(str(dsh))
        assert got is not None, "found nothing in a non-empty tree"
        assert Path(got).name == "session.v3.jsonl.zstd", got
        assert "session-f1776b49" in str(got), got


def test_parse_reads_real_values_and_flags_absent_timeout():
    with tempfile.TemporaryDirectory() as tmp:
        dsh = Path(tmp) / "dsh"
        write_session(dsh, "session-a", "4", make_record(32768, 262144))
        path = probe.newest_session_record(str(dsh))
        text = probe.decode_record(path)
        row = probe.parse_resolved(text)
        assert row["model"] == "qwen3.8:27b-mtp-bf16", row
        assert row["maxTokens"] == 32768, row
        assert row["contextWindow"] == 262144, row
        assert row["timeout"] is None, row
        assert "timeout" in (row.get("missing") or []), row


def test_parse_distinguishes_two_distinct_records():
    with tempfile.TemporaryDirectory() as tmp:
        dsh = Path(tmp) / "dsh"
        write_session(dsh, "session-old", "4",
                      make_record(4096, 131072), mtime=1_700_000_000)
        write_session(dsh, "session-new", "4",
                      make_record(65536, 262144), mtime=1_700_000_010)
        path = probe.newest_session_record(str(dsh))
        row = probe.parse_resolved(probe.decode_record(path))
        assert row["maxTokens"] == 65536, f"newest value must win: {row}"
        assert row["contextWindow"] == 262144, row


def test_record_row_appends_not_overwrites():
    with tempfile.TemporaryDirectory() as tmp:
        ledger = Path(tmp) / "ledgers" / "dsh-resolved-config.jsonl"
        first = {"source": "oracle", "session": "s1", "model": "m-a",
                 "maxTokens": 4096, "contextWindow": 131072,
                 "timeout": None, "missing": ["timeout"],
                 "observed_at": "2026-10-17T00:00:00Z"}
        second = dict(first, session="s2", model="m-b",
                      maxTokens=32768, contextWindow=262144,
                      observed_at="2026-10-18T00:00:00Z")
        probe.record_row(ledger, first)
        probe.record_row(ledger, second)
        lines = [json.loads(l) for l in ledger.read_text().splitlines() if l.strip()]
        assert len(lines) == 2, f"expected 2 rows, got {len(lines)}"
        assert lines[0]["maxTokens"] == 4096, lines
        assert lines[1]["maxTokens"] == 32768, lines


def test_probe_is_read_only_on_dsh_home():
    with tempfile.TemporaryDirectory() as tmp:
        dsh = Path(tmp) / "dsh"
        write_session(dsh, "session-ro", "4", make_record(32768, 262144))
        before = sorted(str(x.relative_to(dsh)) for x in dsh.rglob("*"))
        ledger = Path(tmp) / "ledgers" / "dsh-resolved-config.jsonl"
        path = probe.newest_session_record(str(dsh))
        row = probe.parse_resolved(probe.decode_record(path))
        probe.record_row(ledger, row)
        after = sorted(str(x.relative_to(dsh)) for x in dsh.rglob("*"))
        assert before == after, f"probe modified dsh home: {set(after) ^ set(before)}"


def test_decode_failure_names_the_file():
    with tempfile.TemporaryDirectory() as tmp:
        dsh = Path(tmp) / "dsh"
        sess = dsh / "sessions" / "session-bad"
        sess.mkdir(parents=True)
        p = sess / "session.v4.jsonl.zstd"
        p.write_text("this is not a valid zstd frame")
        try:
            probe.decode_record(str(p))
        except SystemExit as e:
            assert "session-bad" in str(e), f"must name the file: {e}"
        else:
            raise AssertionError("decode_record returned on garbage")


def _all_tests():
    import inspect
    return [v for k, v in sorted(globals().items())
            if k.startswith("test_") and inspect.isfunction(v)]


if __name__ == "__main__":
    tests = _all_tests()
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS {t.__name__}")
        except Exception as e:
            failed += 1
            print(f"FAIL {t.__name__}: {type(e).__name__}: {e}")
    print(f"{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
