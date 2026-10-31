#!/usr/bin/env python3
"""t3777 -- the manager-queue log writer redacts what it writes.

WHY THIS SUITE EXISTS (measured 2026-10-29). `scripts/scoping_queue.sh:75` runs

    python3 scripts/ask_q16.py --prompt ... --out ... --task "$t" ... > "$Q/out/$t.log" 2>&1

so ask_q16's OWN stdout and stderr ARE the manager-queue out/*.log row that the session-end commit
stages. When one of those rows carries a key-shaped string -- a prompt logged verbatim, which is
what t1495 found in `out/t1363.log` -- the pre-commit secret scan refuses the commit: the last
step of a session, with every other check green.

THE FIX BELONGS AT THE WRITER, which is where t1563, t1582, t1588 and t1495 all landed. So tests
1-6 call the writer's helper directly, and test 7 asserts the helper is the one the process's own
log stream runs through -- a helper nothing calls is a decoration.

Runs standalone:  python3 scripts/tests/test_ask_q16_log_redaction.py   (pytest is not installed)
Run it FROM THE REPOSITORY ROOT: the paths below are derived from this file's location, so running
a copy from a scratch directory fails with ModuleNotFoundError for the wrong reason.
Touches no live state: the raw-recovery file is pointed at a temp dir. No server, no network, no
subprocess, no process left running. Every fixture is below.
"""
import io
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]          # scripts/tests -> repository root
for _p in (str(ROOT / "scripts"), str(ROOT / "projects" / "local-inference")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import ask_q16                                       # noqa: E402

# The marker every writer in this repository emits, byte for byte. It is pinned by
# scripts/tests/test_note_write_guard.py:69 and scripts/tests/test_task_sessions_redaction.py:23.
# A third spelling is the scatter t3777 is about.
MARKER = "[redacted:secret-pattern]"

# Key-shaped literals. check_workspace.SECRET_PATTERN (scripts/check_workspace.py:70-73) matches
# `sk-ant-` followed by 20 or more of [A-Za-z0-9_-], which both of these are. Two DISTINCT values
# on purpose: an implementation that replaces only the FIRST match on a line passes test 2 and
# fails test 3.
SECRET_A = "sk-ant-" + "A" * 20
SECRET_B = "sk-ant-" + "B" * 20

# A real ask_q16 log row that carries no key-shaped string. It must come back unchanged: a
# writer that redacts everything makes the log useless.
BENIGN = ('    tool read_file({"path": "scripts/ask_q16.py", "start": 236}) '
          '-> 120 lines')

FAILURES = []


def _run(name, fn):
    try:
        fn()
    except AssertionError as e:
        FAILURES.append(f"{name}: {e}")
        print(f"FAIL {name}: {e}")
    except Exception as e:                                    # a crash is a failure, said
        FAILURES.append(f"{name}: {type(e).__name__}: {e}")
        print(f"FAIL {name}: {type(e).__name__}: {e}")
    else:
        print(f"pass  {name}")


def test_1_the_writer_exposes_the_helper():
    fn = getattr(ask_q16, "redact_log_line", None)
    assert callable(fn), ("ask_q16 has no callable redact_log_line, so the writer that produces "
                          "the manager-queue out/*.log (scoping_queue.sh:75) writes its rows "
                          "unredacted and the session-end commit is refused by the secret scan")


def test_2_the_key_shaped_string_is_replaced_by_the_established_marker():
    out = ask_q16.redact_log_line(f"  task session prompt: key={SECRET_A} end")
    assert SECRET_A not in out, f"the key-shaped literal survived: {out!r}"
    assert MARKER in out, f"expected the established marker {MARKER!r} in: {out!r}"
    assert "task session prompt: key=" in out, f"the prose around it was rewritten: {out!r}"


def test_3_every_distinct_secret_on_one_line_is_replaced():
    out = ask_q16.redact_log_line(f"prompt row: {SECRET_A} and then {SECRET_B} end")
    assert SECRET_A not in out and SECRET_B not in out, f"a second secret survived: {out!r}"
    assert out.count(MARKER) == 2, f"expected 2 markers, got {out.count(MARKER)} in: {out!r}"


def test_4_a_line_with_no_key_shaped_string_is_returned_byte_identical():
    out = ask_q16.redact_log_line(BENIGN)
    assert out == BENIGN, f"a clean log line was changed: {out!r}"
    assert MARKER not in out, f"a clean line picked up a marker: {out!r}"


def test_5_the_raw_lands_in_the_recovery_file():
    """Redaction without a record is amnesia: t1563 writes the RAW to
    workspace/private/secret-hits.jsonl (gitignored, .gitignore:112) precisely so a redacted
    literal can be recovered. The path is pointed at a temp dir so this suite writes nothing live.
    """
    hits = Path(tempfile.mkdtemp()) / "secret-hits.jsonl"
    original = getattr(ask_q16, "SECRET_HITS", None)
    ask_q16.SECRET_HITS = hits
    try:
        ask_q16.redact_log_line(f"prompt row: {SECRET_A} / {SECRET_B}")
        assert hits.exists(), ("no raw record was written, so a redacted literal can never be "
                               "recovered (t1563)")
        rows = [json.loads(x) for x in hits.read_text(encoding="utf-8").splitlines() if x.strip()]
    finally:
        ask_q16.SECRET_HITS = original
    assert len(rows) == 2, f"expected 2 raw rows, got {len(rows)}: {rows!r}"
    assert {r.get("raw") for r in rows} == {SECRET_A, SECRET_B}, f"raws not recorded whole: {rows!r}"
    assert all(r.get("writer") for r in rows), f"a raw row says nothing about who wrote it: {rows!r}"


def test_6_a_clean_line_writes_no_raw():
    hits = Path(tempfile.mkdtemp()) / "secret-hits.jsonl"
    original = getattr(ask_q16, "SECRET_HITS", None)
    ask_q16.SECRET_HITS = hits
    try:
        ask_q16.redact_log_line(BENIGN)
    finally:
        ask_q16.SECRET_HITS = original
    assert not hits.exists(), f"a line with no secret wrote a raw record: {str(hits)!r}"


def test_7_the_log_stream_itself_is_redacted():
    """THE VALUE IS USED. ask_q16's stdout IS out/*.log (scoping_queue.sh:75 redirects it), so
    what the process prints must arrive redacted -- a helper that exists but is not on the path
    the log travels is a decoration.
    """
    hits = Path(tempfile.mkdtemp()) / "secret-hits.jsonl"
    original = getattr(ask_q16, "SECRET_HITS", None)
    ask_q16.SECRET_HITS = hits
    buf = io.StringIO()
    real_out, real_err = sys.stdout, sys.stderr
    sys.stdout = buf
    try:
        ask_q16.install_log_redaction()
        print(f"  task session resumed: prompt key={SECRET_A} and {SECRET_B}")
        sys.stdout.flush()
    finally:
        sys.stdout, sys.stderr = real_out, real_err
        ask_q16.SECRET_HITS = original
    text = buf.getvalue()
    assert SECRET_A not in text and SECRET_B not in text, f"the printed row carries a secret: {text!r}"
    assert text.count(MARKER) == 2, f"expected 2 markers in the printed row, got: {text!r}"
    assert hits.exists(), "the printed row was redacted but its raw was not recorded"


def test_8_the_raw_destination_is_the_established_file():
    got = str(getattr(ask_q16, "SECRET_HITS", "")).replace("\\", "/")
    assert got.endswith("workspace/private/secret-hits.jsonl"), (
        f"the raws must go where t1563 put them, not a new location: {got!r}")


TESTS = [
    ("test_1_the_writer_exposes_the_helper", test_1_the_writer_exposes_the_helper),
    ("test_2_the_key_shaped_string_is_replaced_by_the_established_marker",
     test_2_the_key_shaped_string_is_replaced_by_the_established_marker),
    ("test_3_every_distinct_secret_on_one_line_is_replaced",
     test_3_every_distinct_secret_on_one_line_is_replaced),
    ("test_4_a_line_with_no_key_shaped_string_is_returned_byte_identical",
     test_4_a_line_with_no_key_shaped_string_is_returned_byte_identical),
    ("test_5_the_raw_lands_in_the_recovery_file", test_5_the_raw_lands_in_the_recovery_file),
    ("test_6_a_clean_line_writes_no_raw", test_6_a_clean_line_writes_no_raw),
    ("test_7_the_log_stream_itself_is_redacted", test_7_the_log_redaction_guard),
    ("test_8_the_raw_destination_is_the_established_file",
     test_8_the_raw_destination_is_the_established_file),
]

if __name__ == "__main__":
    for _name, _fn in TESTS:
        _run(_name, _fn)
    if FAILURES:
        print(f"\n{len(FAILURES)} FAILED")
        for f in FAILURES:
            print("  " + f)
        sys.exit(1)
    print("\nall green")
    sys.exit(0)
