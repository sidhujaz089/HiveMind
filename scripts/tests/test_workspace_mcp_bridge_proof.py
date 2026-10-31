#!/usr/bin/env python3
"""t1021 -- the bridge-proof ledger row, written at the MCP call-log append point.

Red today for the reason the task states: Workspace.log() in
scripts/workspace_mcp/core.py appends one row to workspace/mcp-calls.jsonl and stops.
Nothing joins a tool row that satisfies a deferral's log condition to a record anyone
reads, so the promise in workspace/rca/295237521c28.md -- "if a status line appears
with a 160.79.x client, the bridge is proven end to end" -- was satisfied 21 times
(first 2026-08-19T13:34:37Z) and discharged nothing.

Fixtures are synthetic by necessity: log() writes under the Workspace root, so every
write lands in a tempfile.mkdtemp() directory and the live workspace/mcp-calls.jsonl is
never opened. The VALUES are the real ones read off the live log: client
160.79.106.177, via oauth:wsmcp_hsU2nDGnKY_nPhNUYWKc4A, target
workspace/PROJECT-INDEX.md, bytes 18945 (and 21515 for a later call).

Run: python3 scripts/tests/test_workspace_mcp_bridge_proof.py   (pytest is not installed)
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import workspace_mcp.core as core

EGRESS = "160.79.106.177"          # Anthropic egress, as recorded in workspace/mcp-calls.jsonl
VIA = "oauth:wsmcp_hsU2nDGnKY_nPhNUYWKc4A"
TARGET = "workspace/PROJECT-INDEX.md"
BYTES = 18945                       # first qualifying call's response size, from the live log
BYTES_LATER = 21515                 # a later call's size -- two DISTINCT real values
IN_RANGE_LO = "160.79.104.1"        # inside 160.79.104.0/21
IN_RANGE_HI = "160.79.111.255"      # last address inside 160.79.104.0/21
OUT_RANGE = "160.79.112.1"          # first address ABOVE the /21: a "160.79." prefix match lets it through


def _ws(root):
    return core.Workspace(root, caller="claude-chat/opus-5")


def _rows(root, rel):
    p = root / rel
    if not p.is_file():
        return []
    return [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]


def _proof_rel():
    """Where the ledger must live, per the RCA. Falls back to the literal so a missing
    constant fails as an assertion naming it, not as an import error."""
    return getattr(core, "BRIDGE_PROOF_LOG", None) or "workspace/bridge-proof.jsonl"


def test_the_ledger_and_the_condition_are_declared():
    assert getattr(core, "BRIDGE_PROOF_LOG", None) == "workspace/bridge-proof.jsonl", (
        "core.py must declare BRIDGE_PROOF_LOG = 'workspace/bridge-proof.jsonl': that path is "
        "the recorder the STATE.md connector claim is derived from (rca 295237521c28)")
    conds = getattr(core, "BRIDGE_PROOF_CONDITIONS", None)
    assert isinstance(conds, dict), f"core.py must declare BRIDGE_PROOF_CONDITIONS as a dict keyed by condition_key, got {conds!r}"
    assert "claude-chat-connector" in conds, (
        f"the deferral's condition must be registered under the key the RCA names, got keys {sorted(conds)}")


def test_qualifying_status_call_writes_one_proof_row():
    root = Path(tempfile.mkdtemp())
    ws = _ws(root)
    rel = _proof_rel()
    tok = core.set_call_context(client=EGRESS, via=VIA)
    try:
        rec = ws.log("status", TARGET, "ok", extra={"bytes": BYTES})
    finally:
        core.CALL_CONTEXT.reset(tok)
    rows = _rows(root, rel)
    assert len(rows) == 1, f"a status/ok call from {EGRESS} must append exactly one proof row, got {len(rows)}: {rows}"
    row = rows[0]
    assert set(row) == {"ts", "tool", "client", "via", "bytes", "condition_key"}, (
        f"the proof row carries the six fields the RCA names, got {sorted(row)}")
    assert row["ts"] == rec["ts"], f"the proof row must carry the call's own ts, got {row['ts']!r} vs {rec['ts']!r}"
    assert row["tool"] == "status", f"proof row tool must be the call's tool, got {row['tool']!r}"
    assert row["client"] == EGRESS, f"proof row client must be the caller's address, got {row['client']!r}"
    assert row["via"] == VIA, f"proof row via must be the credential that authenticated the call, got {row['via']!r}"
    assert row["bytes"] == BYTES, f"proof row bytes must be the response size, got {row['bytes']!r}"
    assert row["condition_key"] == "claude-chat-connector", (
        f"the row must name which registered condition it discharged, got {row['condition_key']!r}")
    calls = _rows(root, str(core.CALL_LOG))
    assert len(calls) == 1, f"the join must not double-write the call log: got {len(calls)} rows"
    assert "condition_key" not in calls[0], "the proof row is a SEPARATE record, not fields merged into the call row"
    assert set(calls[0]) == {"ts", "caller", "tool", "target", "outcome", "client", "via", "bytes"}, (
        f"log()'s own record must keep its shape, got {sorted(calls[0])}")


def test_rows_that_do_not_qualify_write_no_proof_row():
    root = Path(tempfile.mkdtemp())
    ws = _ws(root)
    rel = _proof_rel()
    tok = core.set_call_context(client=EGRESS, via=VIA)
    try:
        ws.log("read", TARGET, "ok")                                    # right client, right outcome, wrong tool
        ws.log("status", TARGET, "refused", detail="path-denied")        # right tool, failed outcome
        ws.log("status", TARGET, "ok", extra={"bytes": BYTES})           # the one qualifying call
    finally:
        core.CALL_CONTEXT.reset(tok)
    rows = _rows(root, rel)
    assert len(rows) == 1, f"only a status/ok row qualifies; expected the 1 row from the qualifying call, got {len(rows)}: {rows}"
    assert rows[0]["tool"] == "status" and rows[0]["client"] == EGRESS, f"the surviving proof row is the qualifying call's: {rows[0]}"
    tok = core.set_call_context(client=OUT_RANGE, via=VIA)
    try:
        ws.log("status", TARGET, "ok", extra={"bytes": BYTES})           # 160.79.112.1 is outside the /21
    finally:
        core.CALL_CONTEXT.reset(tok)
    tok = core.set_call_context(client="8.8.8.8", via=VIA)
    try:
        ws.log("status", TARGET, "ok", extra={"bytes": BYTES})           # not Anthropic egress at all
    finally:
        core.CALL_CONTEXT.reset(tok)
    tok = core.CALL_CONTEXT.set(None)                                    # stdio / a background task: no context
    ws.log("status", TARGET, "ok", extra={"bytes": BYTES})
    core.CALL_CONTEXT.reset(tok)
    rows = _rows(root, rel)
    assert len(rows) == 1, (
        f"a 160.79 address outside 160.79.104.0/21, a non-egress client, and a client-less direct call "
        f"must all write nothing; expected 1 proof row, got {len(rows)}: {rows}")
    assert len(_rows(root, str(core.CALL_LOG))) == 6, "every call keeps being logged, qualified or not"


def test_every_qualifying_call_appends_its_own_row():
    root = Path(tempfile.mkdtemp())
    ws = _ws(root)
    rel = _proof_rel()
    for client, size in ((IN_RANGE_LO, BYTES), (IN_RANGE_HI, BYTES_LATER)):
        tok = core.set_call_context(client=client, via=VIA)
        try:
            ws.log("status", TARGET, "ok", extra={"bytes": size})
        finally:
            core.CALL_CONTEXT.reset(tok)
    rows = _rows(root, rel)
    assert len(rows) == 2, f"one proof row per qualifying call, not one per process: got {len(rows)}: {rows}"
    assert [r["client"] for r in rows] == [IN_RANGE_LO, IN_RANGE_HI], (
        f"both ends of 160.79.104.0/21 must qualify, got {[r['client'] for r in rows]}")
    assert [r["bytes"] for r in rows] == [BYTES, BYTES_LATER], (
        f"each row carries its own call's byte count, got {[r['bytes'] for r in rows]}")


def test_a_proof_write_that_fails_is_recorded_not_swallowed():
    root = Path(tempfile.mkdtemp())
    (root / _proof_rel()).mkdir(parents=True)     # the ledger path is a directory: open(..., 'a') raises
    ws = _ws(root)
    tok = core.set_call_context(client=EGRESS, via=VIA)
    try:
        rec = ws.log("status", TARGET, "ok", extra={"bytes": BYTES})
    finally:
        core.CALL_CONTEXT.reset(tok)
    calls = _rows(root, str(core.CALL_LOG))
    assert len(calls) == 1, f"a side-ledger failure must not suppress the call-log row, got {len(calls)}"
    assert rec.get("outcome") == "ok", "a side-ledger failure must not turn a served call into a refusal"
    notes = [k for k, v in rec.items() if k.startswith("bridge_proof") and v]
    assert notes, ("a proof-ledger write that failed must be recorded in the call row by name "
                   f"(L-039: a handler that passes is a failure read as a success); keys were {sorted(rec)}")
    assert notes[0] in calls[0] and calls[0][notes[0]], (
        f"the key recorded in the return value must be in the row on disk; keys there: {sorted(calls[0])}")


def test_the_return_value_keeps_its_shape_for_a_direct_call():
    root = Path(tempfile.mkdtemp())
    ws = _ws(root)
    rec = ws.log("read", TARGET, "ok")
    assert set(rec) == {"ts", "caller", "tool", "target", "outcome", "via"}, (
        f"log() must return exactly its own record, got {sorted(rec)}")
    assert rec["via"] == "direct", f"a call with no request context must say direct, got {rec['via']!r}"
    assert _rows(root, _proof_rel()) == [], "a direct call has no client, so no condition can fire"


def _main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    bad = []
    for fn in tests:
        try:
            fn()
        except AssertionError as exc:
            bad.append(fn.__name__)
            print(f"  [BAD]  {fn.__name__}: {exc}")
        except Exception as exc:
            bad.append(fn.__name__)
            print(f"  [BAD]  {fn.__name__}: CRASH {type(exc).__name__}: {exc}")
        else:
            print(f"  [ok]   {fn.__name__}")
    print(f"\n{len(tests) - len(bad)}/{len(tests)} passed" + (f"; FAILED: {bad}" if bad else ""))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(_main())
