#!/usr/bin/env python3
"""t771 -- a handover that names open work with no task number must fail a check.

The defect this guards (workspace/rca/444e71df39c1.md, rca/00cefde9445e.md,
rca/032a3ac46eb2.md, rca/4a1a0fd358a8.md, rca/4eda76f49733.md): a session writes the line

    | Jas's decisions from the triage | DECLARED-OPEN | this handover |

into workspace/HANDOVER-2026-10-01.md (line 200 on main) and closes. That row is legal today:
scripts/handover.py:28 LEDGER_STATES accepts DECLARED-OPEN with any artefact, so the state is
stated and the thread is orphaned in the same gesture. A handover is a terminal surface --
nothing writes a task number into it and nothing reads it back -- so the work survives only
inside the transcript, which is how Round 4 (rca/4eda76f49733.md), the HTTPS lane
(HANDOVER-2026-09-16.md:147, "Blocked on one thing only: HTTPS") and the flaky-suite remedy
("... the next session should look at it first") each went 14 to 22 days unowned.

This suite pins scripts/handover_open_rows.py, which must collect those rows into
workspace/run-logs/handover-open-rows.jsonl as {ts, session, file, line, quote, tracked_as}
and exit non-zero while any collected row carries no t[0-9]{3}.

RED ON MAIN, for the stated reason and not for a bug in this file:
scripts/handover_open_rows.py does not exist, so every test raises inside mod() with a message
naming the missing file. The module creates it; nothing in this suite creates anything outside a
tempfile.TemporaryDirectory.

No pytest (not installed on this box):

    python3 scripts/tests/test_handover_open_rows.py
"""
import importlib.util
import json
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]          # scripts/tests/ -> the repository root
MODULE_PATH = ROOT / "scripts" / "handover_open_rows.py"

REPO = "032a3ac46eb2"
WS = "workspace/HANDOVER-2026-10-05.md"

# --- real artefacts, quoted verbatim from the files the RCAs name ---
RCA_ROW = "Jas's decisions from the triage | DECLARED-OPEN | this handover"
RCA_QUOTE = ("If it stays red, session_end will report it and the next session "
             "should look at it first")
BLOCKED_LINE = "Blocked on one thing only: HTTPS"

RECORD_KEYS = {"ts", "session", "file", "line", "quote", "tracked_as"}

# The open-work vocabulary, taken from the notes on t771 (rca/032a3ac46eb2.md names 'blocked
# on', 'blocked', 'pending', 'needs', 'your call', "I'll", "next I'll") and from the RCA
# headings that recorded the rest. 'declared-open' is the state token scripts/handover.py:28
# invented for this case, and it is the only marker the recorded instance carries. The oracle
# asserts a SUPERSET -- the builder may add terms, it may not drop one of these, and a suite
# that pinned an exact tuple would be a contract no implementation is free to improve.
REQUIRED_PHRASES = (
    "blocked on", "blocked", "pending", "needs", "your call", "i'll", "next i'll",
    "next session", "to do", "todo", "unresolved", "deferred", "carried forward",
    "remains", "is open", "open item", "asked about", "declared-open",
)

# One DISTINCT sentence per required phrase: real prose, each carrying NO t[0-9]{3}. Distinct
# values, not one repeated placeholder, so an implementation that flags every line and one
# that flags no line both fail here.
EXAMPLES = {
    "blocked on": "Blocked on the cert renewal.",
    "blocked": "The HTTPS lane is blocked.",
    "pending": "The NAS backup is pending a decision.",
    "needs": "The env3 lane needs a re-clone.",
    "your call": "Which port to expose is your call.",
    "i'll": "I'll re-clone env3 before launching.",
    "next i'll": "next I'll write the deploy note.",
    "next session": "The next session should look at the flaky suite first.",
    "to do": "There is a to do item for the audit.",
    "todo": "TODO: file the deploy gap.",
    "unresolved": "unresolved: the chat daemon's port.",
    "deferred": "deferred: the archive sweep.",
    "carried forward": "Triage decisions carried forward.",
    "remains": "The cert lane remains blocked.",
    "is open": "The cert lane is open.",
    "open item": "Open item: the archive sweep for October.",
    "asked about": "Jas asked about the session_end suite cost at the end.",
    "declared-open": "The triage decisions are DECLARED-OPEN with this handover as their cover.",
}

# Lines that name no open work. A check that flags these is a gate that fires on everything
# and gets routed around inside a week, so this is the precision half of the suite.
CLEAN_LINES = (
    "| DSH split | CLOSED | commit 9dc67204 |",
    "| Cert lane | FILED | t771 |",
    "The five Phase-0 fixes are merged and the suite is green.",
    "| Thread | State | Artefact |",
    "|---|---|---|",
    "Session: 032a3ac46eb2",
    "## What is still open",
    "## Next step",
)

# The shape of a real handover, with two open-work lines: line 7 is untracked (the defect),
# line 8 is tracked as t512. Line numbers are asserted, so an off-by-one fails.
BAD_HANDOVER = "\n".join([
    "# Handover 2026-10-05",
    "",
    "Session: 032a3ac46eb2",
    "",
    "## What is still open",
    "",
    "- Blocked on one thing only: HTTPS",
    "- Triage decisions carried forward: t512",
    "",
    "## Thread ledger",
    "| Thread | State | Artefact |",
    "|---|---|---|",
    "| Cert lane | FILED | t771 |",
    "| DSH split | CLOSED | commit 9dc67204 |",
    "",
]) + "\n"

# A handover whose open work every carries a tNNN: the check must say nothing about it.
GOOD_HANDOVER = "\n".join([
    "# Handover 2026-10-06",
    "",
    "Session: 032a3ac46eb2",
    "",
    "## What is still open",
    "",
    "- Round 4 carried forward as t771.",
    "",
    "## Thread ledger",
    "| Thread | State | Artefact |",
    "|---|---|---|",
    "| Cert lane | FILED | t771 |",
    "| DSH split | CLOSED | commit 9dc67204 |",
    "",
]) + "\n"

_module = None


def mod():
    """Import scripts/handover_open_rows.py from the path it really lives on."""
    global _module
    if _module is None:
        if not MODULE_PATH.exists():
            raise AssertionError(
                f"{MODULE_PATH} does not exist: this task builds the module that collects "
                f"handover open-work rows, so every case here fails for that missing change")
        spec = importlib.util.spec_from_file_location("handover_open_rows", MODULE_PATH)
        if spec is None or spec.loader is None:
            raise AssertionError(f"cannot load scripts/handover_open_rows.py from {MODULE_PATH}")
        _module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_module)
    return _module


def _root_with(files: dict) -> Path:
    """A throwaway root holding the given files under workspace/ (never the live repo)."""
    td = tempfile.TemporaryDirectory()
    root = Path(td)
    (root / "workspace").mkdir(parents=True)
    for name, text in files.items():
        (root / "workspace" / name).write_text(text, encoding="utf-8")
    return root


def test_module_exports_the_named_api():
    m = mod()
    for name in ("OPEN_PHRASES", "OPEN_HEADINGS", "TASK_ID_RE", "scan_text", "scan_file",
                  "scan", "write_jsonl", "check", "main"):
        if not hasattr(m, name):
            return f"scripts/handover_open_rows.py defines no {name!r}"
    return None


def test_scan_text_flags_the_row_the_rca_recorded_verbatim():
    m = mod()
    rows = m.scan_text(RCA_ROW)
    if not rows:
        return (f"scan_text() flagged nothing for the exact row recorded at "
                f"workspace/HANDOVER-2026-10-01.md:200: {RCA_ROW!r}. A row whose state is "
                f"DECLARED-OPEN and whose artefact is 'this handover' is the defect this task "
                f"exists for.")
    if len(rows) != 1:
        return f"scan_text() on one line returned {len(rows)} records, expected exactly 1"
    r = rows[0]
    missing = RECORD_KEYS - set(r)
    extra = set(r) - RECORD_KEYS
    if missing:
        return f"record is missing field(s) {sorted(missing)}; keys present: {sorted(r)}"
    if extra:
        return f"record carries field(s) the contract does not name: {sorted(extra)}"
    if RCA_ROW not in r["quote"]:
        return f"quote {r['quote']!r} does not carry the open-work text verbatim"
    if r["tracked_as"]:
        return (f"a row that names no task number must come back with tracked_as empty, got "
                f"{r['tracked_as']!r} -- 'this handover' is not a task number, which is the "
                f"whole finding")
    if not isinstance(r["ts"], str) or not r["ts"].strip():
        return f"ts must be a non-empty string, got {r['ts']!r}"
    if not isinstance(r["line"], int) or isinstance(r["line"], bool):
        return f"line must be an int, got {type(r['line']).__name__}"
    if r["session"] != "":
        return (f"scan_text() on a lone line has no 'Session: <id>' line to read, so session "
                f"must be '' -- got {r['session']!r}")
    return None


def test_scan_text_records_the_task_number_the_row_names():
    m = mod()
    rows = m.scan_text("| DSH split | DECLARED-OPEN | t412 |")
    if len(rows) != 1:
        return f"expected 1 record for the row naming t412, got {len(rows)}"
    if rows[0]["tracked_as"] != "t412":
        return f"tracked_as is {rows[0]['tracked_as']!r}, expected 't412'"
    return None


def test_task_id_regex_needs_three_digits():
    m = mod()
    hit = m.TASK_ID_RE.search("carried as t771 today")
    if not hit or hit.group(0) != "t771":
        return f"TASK_ID_RE.search('carried as t771 today') gave {hit!r}, expected a t771 match"
    if m.TASK_ID_RE.search("two digits only: t77"):
        return "TASK_ID_RE matched 't77' -- the rule the task states is t[0-9]{3}"
    return None


def test_every_required_open_work_phrase_is_recognised():
    m = mod()
    phrases = [str(p).lower() for p in m.OPEN_PHRASES]
    for phrase in REQUIRED_PHRASES:
        if phrase not in phrases:
            return (f"OPEN_PHRASES does not contain {phrase!r}. The notes on t771 name it as "
                    f"open-work vocabulary; the assertion is a superset, so adding terms is "
                    f"free and dropping one is not.")
    for phrase, line in EXAMPLES.items():
        rows = m.scan_text(line)
        if not rows:
            return f"scan_text({line!r}) flagged nothing; {phrase!r} names open work"
        if not any(phrase in r["quote"].lower() for r in rows):
            return f"no record for {line!r} quotes the line that carries {phrase!r}"
        if any(r["tracked_as"] for r in rows):
            return f"{line!r} carries no task number, so tracked_as must stay empty"
    return None


def test_the_remedy_sentence_the_rca_recorded_is_caught():
    """rca/00cefde9445e.md asked for this exact quote to be the fixture case: a remedy that
    reads 'the next session should look at it first' names work with no number, and the suite
    behind it went red six more times before any review caught it."""
    m = mod()
    rows = m.scan_text(RCA_QUOTE)
    if not rows:
        return (f"scan_text() did not flag {RCA_QUOTE!r}; the task's own notes ask for this "
                f"verbatim sentence as a fixture case")
    if rows[0]["tracked_as"]:
        return f"{RCA_QUOTE!r} names no task number; tracked_as must be ''"
    return None


def test_lines_that_name_no_open_work_are_not_flagged():
    m = mod()
    for line in CLEAN_LINES:
        rows = m.scan_text(line)
        if rows:
            return (f"scan_text({line!r}) flagged {len(rows)} record(s): {rows}. A closed row, "
                    f"a finished sentence, a table separator, a column header, a Session: line "
                    f"and a section heading are not open work; a gate that fires on all of them "
                    f"is a gate that gets bypassed.")
    return None


def test_scan_file_reports_open_bullets_at_their_real_line_numbers():
    m = mod()
    root = _root_with({})
    path = root / "workspace" / "HANDOVER-2026-10-05.md"
    path.write_text(BAD_HANDOVER, encoding="utf-8")
    rows = m.scan_file(path)
    by_line = {r["line"]: r for r in rows}
    if 7 not in by_line:
        return (f"the bullet '- Blocked on one thing only: HTTPS' is line 7 of the file and is "
                f"open work with no task number; lines reported: {sorted(by_line)}")
    if by_line[7]["quote"] != "- Blocked on one thing only: HTTPS":
        return f"line 7 quote is {by_line[7]['quote']!r}, not the bullet verbatim"
    if by_line[7]["tracked_as"] != "":
        return f"line 7 names no task number; tracked_as is {by_line[7]['tracked_as']!r}"
    if 8 not in by_line:
        return ("line 8, '- Triage decisions carried forward: t512', names open work and must "
                f"be recorded even though it is tracked; lines reported: {sorted(by_line)}")
    if by_line[8]["tracked_as"] != "t512":
        return f"line 8 tracked_as is {by_line[8]['tracked_as']!r}, expected 't512'"
    # The ledger rows below carry a state and a receipt: they are closed, not open work.
    for ln in (11, 12, 13, 14):
        if ln in by_line:
            return (f"line {ln} is a Thread ledger row with a stated state and a receipt "
                    f"(FILED t771 / CLOSED commit); it is not open work and was reported")
    if 1 in by_line or 3 in by_line:
        return ("the '# Handover' title and the 'Session: <id>' line are not open work and "
                "must not be reported")
    lines = path.read_text(encoding="utf-8").splitlines()
    for ln, rec in by_line.items():
        if not (1 <= ln <= len(lines)):
            return f"a record points at line {ln} of a {len(lines)}-line file"
        if rec["quote"] not in lines[ln - 1]:
            return f"the record for line {ln} does not quote that line of the file"
    return None


def test_scan_file_names_the_session_the_handover_declares():
    """{ts, session, file, line, quote, tracked_as} -- the session is the only thing that ties
    an open row to the session that orphaned it, so it must be read from the 'Session:' line."""
    m = mod()
    root = _root_with({})
    path = root / "workspace" / "HANDOVER-2026-10-05.md"
    path.write_text(BAD_HANDOVER, encoding="utf-8")
    rows = m.scan_file(path)
    if not rows:
        return "scan_file() reported nothing for a handover with an untracked open bullet"
    if any(r["session"] != REPO for r in rows):
        return (f"every record must carry the session the file declares ('Session: "
                f"{REPO}', line 3), got {sorted({r['session'] for r in rows})}")
    if any(r["file"] != WS for r in rows):
        return (f"file must be the workspace-relative path the next reader can open, e.g. "
                f"{WS!r}, got {sorted({r['file'] for r in rows})}")
    return None


def test_write_jsonl_appends_and_keeps_every_field():
    m = mod()
    out = _root_with({}) / "run-logs" / "handover-open-rows.jsonl"
    ret = m.write_jsonl(m.scan_text(RCA_QUOTE), out)
    m.write_jsonl(m.scan_file(_root_with({"HANDOVER-2026-10-05.md": BAD_HANDOVER}) /
                              "workspace" / "HANDOVER-2026-10-05.md"), out)
    if ret is not None and Path(ret) != out:
        return f"write_jsonl() returned {ret!r} instead of the path it was told to write"
    if not out.exists():
        return f"write_jsonl() created no file at {out}"
    raw = out.read_text(encoding="utf-8").splitlines()
    # ONE distinct row from the quote, TWO from the handover (lines 7 and 8): 3 in total.
    # A writer that overwrites loses every earlier session's rows.
    if len(raw) != 3:
        return f"after two writes the file holds {len(raw)} lines, expected 3 -- the jsonl is " \
               f"history and a second session's rows must not replace the first's"
    for text in raw:
        rec = json.loads(text)
        if not RECORD_KEYS <= set(rec):
            return f"jsonl row is missing field(s): {sorted(RECORD_KEYS - set(rec))}: {rec}"
        if not str(rec["ts"]).strip():
            return f"jsonl row carries an empty ts: {rec}"
    return None


def test_scan_root_reads_every_handover_and_nothing_else():
    m = mod()
    root = _root_with({
        "HANDOVER-2026-10-05.md": BAD_HANDOVER,
        "HANDOVER-2026-10-06.md": GOOD_HANDOVER,
        "NOTES.md": "Blocked on the cert renewal.\n",
    })
    (root / "notes").mkdir()
    (root / "notes" / "HANDOVER-2026-10-05.md").write_text(BAD_HANDOVER, encoding="utf-8")
    rows = m.scan(root)
    files = {r["file"] for r in rows}
    if WS not in files:
        return f"scan(root) did not report the bad handover under {WS!r}; files: {sorted(files)}"
    for f in files:
        if not f.startswith("workspace/HANDOVER-"):
            return (f"scan(root) read {f!r}; only workspace/HANDOVER-*.md is in scope, and the "
                    f"path must be repo-relative")
    if "workspace/NOTES.md" in files:
        return "scan(root) read a file that is not a handover"
    # The good handover names one open bullet, tracked: it must appear (tracked) so the ledger
    # is a record of open work, not only of findings.
    if "workspace/HANDOVER-2026-10-06.md" not in files:
        return "scan(root) skipped the second handover; it walks workspace/HANDOVER-*.md only"
    return None


def test_check_names_the_file_line_and_quote_of_the_untracked_row():
    m = mod()
    root = _root_with({"HANDOVER-2026-10-05.md": BAD_HANDOVER})
    problems = m.check(root)
    if len(problems) != 1:
        return (f"expected exactly 1 problem for a handover with one untracked open row, got "
                f"{len(problems)}: {problems}")
    p = problems[0]
    if WS not in p:
        return f"the problem does not name the file a reader can open: {p!r}"
    if ":7" not in p:
        return f"the problem does not name line 7, where the untracked bullet is: {p!r}"
    if BLOCKED_LINE not in p:
        return f"the problem drops the quote it is complaining about: {p!r}"
    return None


def test_check_is_silent_when_every_open_row_carries_a_task_number():
    m = mod()
    root = _root_with({"HANDOVER-2026-10-06.md": GOOD_HANDOVER})
    problems = m.check(root)
    if problems:
        return (f"a handover whose open work all carries a tNNN must pass and returned "
                f"{len(problems)} problem(s): {problems}. This is the assertion that keeps the "
                f"gate worth running.")
    return None


def test_check_finds_the_untracked_line_even_when_a_sibling_row_is_tracked():
    """The recorded instance is a ledger row whose cover is the handover itself, filed beside
    rows that do carry numbers. A check that passes because the file mentions a tNNN anywhere
    would pass on HANDOVER-2026-10-01.md exactly as it stands on main."""
    m = mod()
    root = _root_with({})
    (root / "workspace" / "HANDOVER-2026-10-01.md").write_text(
        "## Thread ledger\n| Thread | State | Artefact |\n|---|---|---|\n"
        "| Session-review findings | CLOSED | cbf392c1 |\n"
        f"| {RCA_ROW} |\n"
        "| Harness output-size class | DECLARED-OPEN | t342 t450 |\n", encoding="utf-8")
    rows = m.scan(root)
    if not any(RCA_ROW in r["quote"] and not r["tracked_as"] for r in rows):
        return (f"no untracked record for the ledger row '{RCA_ROW}' sitting between two rows "
                f"that carry numbers; records: {[(r['line'], r['quote']) for r in rows]}")
    if not m.check(root):
        return "check() reported nothing for a handover whose ledger row names no task number"
    return None


def test_the_real_handovers_on_main_are_scanned_against_their_own_lines():
    """The real-artefact case: every handover on main carries a '## What is still open' section.
    Reading them is read-only and asserts integrity, not a count: every record must point at a
    line that really contains its quote, so the ledger cannot drift from the files it reports."""
    m = mod()
    rows = m.scan(ROOT)
    if not rows:
        return (f"scan(ROOT) found no open-work row in the workspace/HANDOVER-*.md files on "
                f"main -- the RCAs name at least four, so the scanner sees nothing")
    for r in rows:
        if not (ROOT / r["file"]).exists():
            return f"a record names a file that does not exist: {r['file']!r}"
        lines = (ROOT / r["file"]).read_text(encoding="utf-8").splitlines()
        if not (1 <= r["line"] <= len(lines)):
            return f"a record for {r['file']} points at line {r['line']} of {len(lines)}"
        if r["quote"] not in lines[r["line"] - 1]:
            return (f"a record for {r['file']}:{r['line']} does not quote that line: "
                    f"{r['quote']!r} vs {lines[r['line'] - 1]!r}")
    return None


def _run():
    tests = [(k, v) for k, v in list(globals().items())
             if k.startswith("test_") and callable(v)]
    failed = 0
    for name, fn in sorted(tests):
        try:
            problem = fn()
        except AssertionError as exc:
            problem = str(exc)
        except Exception as exc:                       # noqa: BLE001 -- a crash is a finding
            problem = f"raised {type(exc).__name__}: {exc}"
        if problem:
            failed += 1
            print(f"FAIL {name}: {problem}")
        else:
            print(f"ok   {name}")
    print(f"{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(_run())
