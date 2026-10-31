#!/usr/bin/env python3
"""t766 -- record every tool refusal at the harness admission point.

The finding this pins (workspace/rca/31e21653e2e6.md:66 and the 2026-10-07 note on t766):
"the command checker is still unavailable" is unprovable eight days later, because no endpoint
records a tool refusal. The refusal exists only inside the transcript it was said in, so "still"
has no count and "not searched" cannot be told from "cannot search".

The repo-owned admission point is role_caps.allowed()
(projects/local-inference/role_caps.py:104-105), which the console calls before running a tool
(projects/local-inference/console.py:850-857). It returns a bool and writes nothing.

RED ON MAIN, for the stated reason: role_caps has no REFUSALS, no record_refusal, and allowed()
appends no row -- so every row read below is empty and the default path is None.

Runs with plain python3 (pytest is not installed). Writes only inside tempfile directories; the
live workspace files are never opened for writing.
"""
import json
import re
import sys
import tempfile
from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]          # scripts/tests -> repo root
sys.path.insert(0, str(ROOT / "projects" / "local-inference"))

import role_caps  # noqa: E402

FAILS = []
FIELDS = ("ts", "session", "harness", "role", "tool", "decision", "reason", "fallback", "task_ref")


def report(label, got, want):
    if got == want:
        print("PASS  " + label)
    else:
        print(f"FAIL  {label}\n        got:  {got!r}\n        want: {want!r}")
        FAILS.append(label)


def rows_of(path):
    """Every row in the recorder. A missing file is zero rows, not a crash: that is today's red."""
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def isolate(td, table, out_name="tool-refusals.jsonl"):
    """Point the recorder at a temp file and the cap table at a fixed dict.

    The cap table is patched through role_caps.load (defined at role_caps.py:83, the only thing
    allowed() consults) so the suite never depends on the live models/ROLES.json -- the same
    reason test_web_search.py:79 pins _default() and not the live store.
    """
    out = Path(td) / out_name
    role_caps.REFUSALS = out
    role_caps.load = lambda: table
    return out


def test_refusal_appends_one_row_with_every_field():
    saved_load, saved_path = role_caps.load, getattr(role_caps, "REFUSALS", None)
    try:
        with tempfile.TemporaryDirectory() as td:
            out = isolate(td, {"coder": {"run_command": False}})
            report("a switched-off tool is refused", role_caps.allowed("coder", "run_command"), False)
            rows = rows_of(out)
            report("one refusal writes exactly one row", len(rows), 1)
            row = rows[0] if rows else {}
            report("every field the RCA names is present",
                   sorted(k for k in FIELDS if k in row), sorted(FIELDS))
            report("decision", row.get("decision"), "REFUSED")
            report("role", row.get("role"), "coder")
            report("tool", row.get("tool"), "run_command")
            report("harness", row.get("harness"), "console")
            report("ts is a UTC second", bool(re.fullmatch(
                r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", str(row.get("ts")))), True)
            reason = str(row.get("reason", ""))
            report("reason names the tool refused", "run_command" in reason, True)
            report("reason names the role refused", "coder" in reason, True)
            report("reason is a sentence, not a word", len(reason) >= 40, True)
    finally:
        role_caps.load, role_caps.REFUSALS = saved_load, saved_path


def test_each_refusal_gets_its_own_row():
    saved_load, saved_path = role_caps.load, getattr(role_caps, "REFUSALS", None)
    try:
        with tempfile.TemporaryDirectory() as td:
            out = isolate(td, {"coder": {"run_command": False, "write_file": False}})
            role_caps.allowed("coder", "run_command")
            role_caps.allowed("coder", "write_file")
            rows = rows_of(out)
            report("two refusals write two rows", len(rows), 2)
            report("the rows name the two distinct tools, in order",
                   [r.get("tool") for r in rows], ["run_command", "write_file"])
    finally:
        role_caps.load, role_caps.REFUSALS = saved_load, saved_path


def test_a_permit_writes_no_refusal_row():
    saved_load, saved_path = role_caps.load, getattr(role_caps, "REFUSALS", None)
    try:
        with tempfile.TemporaryDirectory() as td:
            out = isolate(td, {"manager": {"run_command": True}})
            report("a switched-on tool is permitted", role_caps.allowed("manager", "run_command"), True)
            report("a permit writes no row into the refusal recorder", rows_of(out), [])
    finally:
        role_caps.load, role_caps.REFUSALS = saved_load, saved_path


def test_decision_and_context_are_recorded_verbatim():
    """The shape t766's own field list specifies: decision REFUSED|ALLOWED, plus session,
    fallback, task_ref, harness. A refusal recorder that silently rewrites the caller's values
    cannot be joined to the finding that quotes them."""
    saved_load, saved_path = role_caps.load, getattr(role_caps, "REFUSALS", None)
    try:
        with tempfile.TemporaryDirectory() as td:
            out = isolate(td, {})
            role_caps.record_refusal(tool="grep_files", role="senior", decision="ALLOWED",
                                     harness="claude-code", session="6ed72651",
                                     fallback="read_file", task_ref="t766")
            rows = rows_of(out)
            report("an explicit record_refusal call writes one row", len(rows), 1)
            row = rows[0] if rows else {}
            report("decision recorded as given", row.get("decision"), "ALLOWED")
            report("harness recorded as given", row.get("harness"), "claude-code")
            report("session recorded as given", row.get("session"), "6ed72651")
            report("fallback recorded as given", row.get("fallback"), "read_file")
            report("task_ref recorded as given", row.get("task_ref"), "t766")
            report("a row records the tool", row.get("tool"), "grep_files")
            report("a row records the role", row.get("role"), "senior")
    finally:
        role_caps.load, role_caps.REFUSALS = saved_load, saved_path


def test_default_recorder_is_the_repo_file():
    """The RCA binds this work to a repo path (workspace/rca/06aaf666a517.md:54): the count of
    refusals for a change must be a number in the repository, not a sentence in a transcript."""
    report("REFUSALS default",
           str(getattr(role_caps, "REFUSALS", None)),
           str(ROOT / "workspace" / "tool-refusals.jsonl"))


def test_a_long_reason_is_recorded_whole():
    """never-truncate-a-record: the reason is the record. A slice here is the defect this whole
    task exists to stop -- a record that reads as complete with the cause cut off."""
    saved_load, saved_path = role_caps.load, getattr(role_caps, "REFUSALS", None)
    try:
        with tempfile.TemporaryDirectory() as td:
            out = isolate(td, {})
            long_reason = "classifier said: " + ("detail " * 800)
            role_caps.record_refusal(tool="run_command", role="coder", reason=long_reason)
            rows = rows_of(out)
            report("one row written", len(rows), 1)
            got = rows[0].get("reason") if rows else None
            report("reason recorded with its full length", len(got or ""), len(long_reason))
            report("reason recorded unchanged", got, long_reason)
    finally:
        role_caps.load, role_caps.REFUSALS = saved_load, saved_path


def test_a_write_failure_is_reported_and_the_refusal_is_still_served():
    """swallowed-exception-introduced: the recorder must not be the thing that hides the refusal.
    The refusal is served (allowed() still answers False) AND the failed write is said out loud,
    naming the exception and the path -- the shape console.py's _telemetry uses at lines 990-994."""
    saved_load, saved_path = role_caps.load, getattr(role_caps, "REFUSALS", None)
    try:
        with tempfile.TemporaryDirectory() as td:
            out = isolate(td, {"coder": {"run_command": False}},
                          out_name="missing-dir/tool-refusals.jsonl")
            buf = StringIO()
            with redirect_stderr(buf):
                decision = role_caps.allowed("coder", "run_command")
            text = buf.getvalue()
            report("a refusal is still refused when its record cannot be written", decision, False)
            report("the failed write says it was not written", "not written" in text, True)
            report("it names the path that failed", str(out) in text, True)
            report("it names the errno, not just a word",
                   ("Errno" in text) or ("FileNotFoundError" in text), True)
    finally:
        role_caps.load, role_caps.REFUSALS = saved_load, saved_path


TESTS = [
    test_refusal_appends_one_row_with_every_field,
    test_each_refusal_gets_its_own_row,
    test_a_permit_writes_no_refusal_row,
    test_decision_and_context_are_recorded_verbatim,
    test_default_recorder_is_the_repo_file,
    test_a_long_reason_is_recorded_whole,
    test_a_write_failure_is_reported_and_the_refusal_is_still_served,
]

if __name__ == "__main__":
    for fn in TESTS:
        print(f"--- {fn.__name__}")
        try:
            fn()
        except Exception as e:                                    # noqa: BLE001
            print(f"FAIL  {fn.__name__} raised {type(e).__name__}: {e}")
            FAILS.append(fn.__name__)
    print(f"\n{len(FAILS)} failure(s) in {len(TESTS)} test(s)"
          + (": " + ", ".join(FAILS) if FAILS else ""))
    sys.exit(1 if FAILS else 0)
