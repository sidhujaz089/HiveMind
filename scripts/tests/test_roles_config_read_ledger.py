#!/usr/bin/env python3
"""t1392: reading the role store records WHO read it, at the read.

WHY (workspace/rca/abf0787375b4.md): 'I haven't checked whether any running service reads
ROLES.json' is unanswerable, because the read leaves no trace -- a process that reads the
store once at startup is invisible to any census taken later, and every process from the
2026-10-09 window is gone. The observation must be made at the read.

RED ON MAIN: scripts/roles.py has no READ_LEDGER constant, so roles.READ_LEDGER raises
AttributeError in every test below, and no row is written.

No live state: every store here is a tempfile.TemporaryDirectory, and the repo's own
workspace/ledgers/config-reads.jsonl is asserted byte-for-byte untouched at the end.
"""
import contextlib
import io
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import roles  # noqa: E402

LIVE = ROOT / "workspace" / "ledgers" / "config-reads.jsonl"
LIVE_BEFORE = LIVE.read_bytes() if LIVE.exists() else None


def _models(tmp, serving_roles, store_roles):
    """A temp dir shaped like projects/local-inference/models/: a SERVING.json, plus the
    ROLES.json store next to it when store_roles is not None -- roles.py resolves the store
    as SERVING.parent / 'ROLES.json', so pointing roles.SERVING at this directory redirects
    the store, the seam roles.py's own comment documents."""
    d = Path(tmp) / "models"
    d.mkdir(parents=True, exist_ok=True)
    serving = d / "SERVING.json"
    serving.write_text(json.dumps({"roles": serving_roles}), encoding="utf-8")
    if store_roles is not None:
        (d / "ROLES.json").write_text(json.dumps({"roles": store_roles}), encoding="utf-8")
    return serving


def _rows(path):
    text = Path(path).read_text(encoding="utf-8")
    return [json.loads(l) for l in text.splitlines() if l.strip()]


def test_a_read_of_the_store_writes_one_row_naming_the_reader():
    """The case the writer exists for: which process read ROLES.json, when, which role."""
    s0, l0 = roles.SERVING, roles.READ_LEDGER
    try:
        with tempfile.TemporaryDirectory() as tmp:
            serving = _models(tmp, {}, {"manager": {"kind": "model", "model": "gemma4:26b",
                                                    "endpoint": "http://127.0.0.1:11434",
                                                    "lease": 900}})
            roles.SERVING = serving
            roles.READ_LEDGER = Path(tmp) / "ledgers" / "config-reads.jsonl"

            got = roles.tag("manager", "fallback-model")
            assert got == "gemma4:26b", f"tag() must return the store's model: {got!r}"
            rows = _rows(roles.READ_LEDGER)
            assert len(rows) == 1, f"one read writes exactly one row; got {len(rows)}: {rows}"
            r = rows[0]
            assert set(r) >= {"at", "pid", "ppid", "cwd", "role", "source", "value"}, f"fields: {r}"
            assert r["role"] == "manager", r
            assert r["source"] == "ROLES.json", f"source names the file read: {r['source']!r}"
            assert r["value"] == "gemma4:26b", r
            assert int(r["pid"]) == os.getpid(), r
            assert int(r["ppid"]) == os.getppid(), r
            assert r["cwd"] == os.getcwd(), r
            assert str(r["at"]).endswith("Z"), f"at is an ISO Z timestamp: {r['at']!r}"
            # NEGATIVE: the row is about the read, not a dump of where the store lives.
            assert tmp not in json.dumps(r), f"row leaked the store's path: {r}"

            # One row PER READ, each naming its own role and value. A writer that emits a
            # single row at import time, or the same constant row every time, fails here.
            roles.tag("frontline", "fallback-model")
            rows = _rows(roles.READ_LEDGER)
            assert len(rows) == 2, f"two reads write two rows; got {len(rows)}: {rows}"
            assert [x["role"] for x in rows] == ["manager", "frontline"], rows
            assert int(rows[0]["pid"]) == int(rows[1]["pid"]), "same process: pids must match"
            assert rows[0]["value"] == "gemma4:26b", rows
            assert rows[1]["value"] == "fallback-model", \
                f"a fallback read records the value the caller received: {rows[1]}"
    finally:
        roles.SERVING, roles.READ_LEDGER = s0, l0


def test_the_serving_fallback_records_the_source_it_actually_read():
    """roles._spec reads TWO files: the ROLES.json store, and SERVING.json when the store is
    missing. A writer that always says ROLES.json cannot answer which file a service read --
    the distinct-value case, and a one-source build passes a one-source suite."""
    s0, l0 = roles.SERVING, roles.READ_LEDGER
    try:
        with tempfile.TemporaryDirectory() as tmp:
            serving = _models(tmp, {"manager": "gemma4:26b"}, None)
            roles.SERVING = serving
            roles.READ_LEDGER = Path(tmp) / "ledgers" / "config-reads.jsonl"
            assert not (serving.parent / "ROLES.json").exists()
            got = roles.tag("manager", "fallback-model")
            assert got == "gemma4:26b", f"the fallback path must still return the tag: {got!r}"
            rows = _rows(roles.READ_LEDGER)
            assert len(rows) == 1, rows
            assert rows[0]["source"] == "SERVING.json", \
                f"source must name the file read, not the store: {rows[0]!r}"
            assert rows[0]["role"] == "manager", rows[0]
            assert rows[0]["value"] == "gemma4:26b", rows[0]
    finally:
        roles.SERVING, roles.READ_LEDGER = s0, l0


def test_a_failed_record_is_said_and_never_breaks_the_read():
    """never-truncate-a-record: the writer prints the failure (console.py's _telemetry shape).
    A ledger we cannot open must not be silent, and must not change the read's answer."""
    s0, l0 = roles.SERVING, roles.READ_LEDGER
    try:
        with tempfile.TemporaryDirectory() as tmp:
            serving = _models(tmp, {"manager": "gemma4:26b"}, None)
            blocked = Path(tmp) / "ledgers"
            blocked.mkdir()
            roles.SERVING = serving
            roles.READ_LEDGER = blocked / "config-reads.jsonl"     # a directory: append fails
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                got = roles.tag("manager", "fallback-model")
            assert got == "gemma4:26b", f"a failed RECORD must not change the read: {got!r}"
            said = err.getvalue()
            assert "config-reads.jsonl" in said, f"the failure must name the ledger: {said!r}"
            assert "Error" in said, f"the failure must name the exception type: {said!r}"
            assert not (blocked / "config-reads.jsonl").exists()
    finally:
        roles.SERVING, roles.READ_LEDGER = s0, l0


def test_the_default_ledger_is_the_one_the_rca_named():
    """workspace/ledgers/config-reads.jsonl -- the file the episode is answered from by grep."""
    p = Path(roles.READ_LEDGER)
    assert p.name == "config-reads.jsonl", str(p)
    assert p.parent.name == "ledgers", str(p)


def test_the_live_ledger_is_untouched_by_this_suite():
    """oracle-dirty guard: every write above is in a temp dir. The repo's own ledger is left
    exactly as it was (absent today, and it must stay absent)."""
    assert (LIVE.read_bytes() if LIVE.exists() else None) == LIVE_BEFORE, "wrote to the live ledger"


if __name__ == "__main__":
    fails = 0
    for name in sorted(n for n in list(globals()) if n.startswith("test_")):
        fn = globals()[name]
        if not callable(fn):
            continue
        try:
            fn()
            print(f"PASS {name}")
        except Exception as e:
            fails += 1
            print(f"FAIL {name}: {type(e).__name__}: {e}")
    print(f"{fails} failed")
    sys.exit(1 if fails else 0)
