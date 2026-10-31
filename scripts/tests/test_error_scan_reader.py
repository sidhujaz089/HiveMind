#!/usr/bin/env python3
"""t271 contract: the reader must understand key families, report what it cannot
read, count every row per source, and see transcript failures.

Fixtures are temp-dir fakes shaped on the transcript keys the task states
(timestamp / type / content / operation / sessionId) and on the mcp-calls
shape (ts / outcome). The exact nesting of is_error is [unverified]; if a real
~/.claude/projects row carries it inside content/message, adjust this fixture
before dispatch.
"""
import json
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))
import error_scan  # noqa: E402

PASSED = FAILED = 0

def check(label, got, want):
    global PASSED, FAILED
    if got == want:
        PASSED += 1
        print(f"  [ok]   {label}: {got}")
    else:
        FAILED += 1
        print(f"  [BAD]  {label}: expected {want!r}, got {got!r}")

def kinds(findings):
    return sorted({f["kind"] for f in findings})

d = Path(tempfile.mkdtemp())

def jfile(name, rows):
    p = d / name
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    return p

def cstat(c, source):
    for row in c:
        if str(row["source"]) == str(source):
            return row
    return None

# 1. timestamp family: ts / timestamp must land in the finding's `at`
healthy = jfile("mcp-ok-ts.jsonl", [{"ts": "2026-09-11T03:58:00Z", "outcome": "ok", "call": "read_file"}])
check("a healthy ts-keyed row is not a finding", error_scan.scan([healthy]), [])

bad_ts = jfile("runs-ts.jsonl", [{"ts": "2026-09-09T20:00:00Z", "done_reason": "length", "outcome": "UNPARSEABLE"}])
f = error_scan.scan([bad_ts])
check("a ts-keyed bad row is found", "truncated-output" in kinds(f), True)
check("and the ts key IS the timestamp", f and f[0]["at"], "2026-09-09T20:00:00Z")

bad_ts2 = jfile("runs-timestamp.jsonl", [{"timestamp": "2026-09-10T12:25:03Z", "outcome": "UNPARSEABLE"}])
f = error_scan.scan([bad_ts2])
check("a timestamp-keyed bad row is found", "unusable-parse" in kinds(f), True)
check("and the timestamp rides along", f and f[0]["at"], "2026-09-10T12:25:03Z")

# 2. status family: a non-healthy outcome is a finding; healthy ones are quiet
err = jfile("mcp-err.jsonl", [{"ts": "2026-09-11T04:00:00Z", "outcome": "error", "detail": "RemoteDisconnected"}])
f = error_scan.scan([err])
check("a non-healthy outcome is a finding", len(f) >= 1, True)
check("under a named kind", f and f[0]["kind"] in error_scan.KINDS, True)

ok_rows = jfile("mcp-ok2.jsonl", [
    {"ts": "2026-09-11T04:00:00Z", "outcome": "ACCEPTED"},
    {"ts": "2026-09-11T04:01:00Z", "outcome": "ok"},
])
check("healthy outcomes stay quiet", error_scan.scan([ok_rows]), [])

# 2b. no double-report: a row already matched by a specific signature is not also health-reported
dup = jfile("no-double.jsonl", [{"at": "1", "outcome": "UNPARSEABLE", "why": "invalid JSON"}])
f = error_scan.scan([dup])
check("a row is reported once, not once per key family", len(f), 1)
check("under its specific signature", kinds(f), ["unusable-parse"])

# 3. THE DECAY TEST: an all-unknown source is UNREADABLE, never a silent zero
unknown = jfile("unknown-dialect.jsonl", [{"foo": 1, "bar": "baz"}, {"foo": 2, "bar": "qux"}])
c = error_scan.census([unknown])
row = cstat(c, unknown)
check("an all-unknown source is censusable", row is not None, True)
check("reported UNREADABLE, never a silent zero", row and row["status"], "UNREADABLE")
check("with its row count", row and row["rows"], 2)
check("and zero findings beside it", row and row["findings"], 0)

# 3b. a mixed source still reports its known rows and counts the unreadable
mixed = jfile("mixed-dialect.jsonl", [{"at": "1", "done_reason": "length"}, {"foo": 1}])
c = error_scan.census([mixed])
row = cstat(c, mixed)
check("a mixed source is not UNREADABLE as a whole", row and row["status"], "OK")
check("and it counts the row it could not read", row and row["unreadable_rows"], 1)
check("its known row is still found", len(error_scan.scan([mixed])) >= 1, True)

# 3c. a missing source is not a silent zero either
missing = d / "does-not-exist.jsonl"
c = error_scan.census([missing])
row = cstat(c, missing)
check("a missing source is SKIPPED in the census", row and row["status"], "SKIPPED")

# 4. census shape: a zero must sit next to its volume
big = jfile("big-clean.jsonl", [{"ts": f"2026-09-11T03:{i:02d}:00Z", "outcome": "ok"} for i in range(916)])
c = error_scan.census([big])
row = cstat(c, big)
check("916 healthy rows are all counted", row and row["rows"], 916)
check("with 0 findings beside the volume", row and row["findings"], 0)
check("and the status says OK", row and row["status"], "OK")

# 5. transcript-failure signature: a failing tool_result NAMES the row
trans = jfile("session.jsonl", [
    {"timestamp": "2026-09-11T10:00:00Z", "type": "assistant", "sessionId": "sess-1",
     "operation": "think", "content": "I will read the file."},
    {"timestamp": "2026-09-11T10:00:05Z", "type": "tool_result", "sessionId": "sess-1",
     "operation": "read", "content": "EACCES: permission denied", "is_error": True},
])
f = error_scan.scan([trans])
check("a failing tool_result is a finding", len(f) >= 1, True)
check("and it NAMES the row (sessionId)", f and any("sess-1" in str(x["locator"]) for x in f), True)
check("under the transcript-failure kind", f and f[0]["kind"], "transcript-failure")
check("and the timestamp rides along", f and any(x["at"] == "2026-09-11T10:00:05Z" for x in f), True)

clean = jfile("clean-session.jsonl", [
    {"timestamp": "2026-09-11T09:00:00Z", "type": "assistant", "sessionId": "sess-2",
     "operation": "think", "content": "Done."},
])
check("a clean transcript row is not a finding", error_scan.scan([clean]), [])

print(f"\n{PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
