#!/usr/bin/env python3
"""Oracle for t796 -- scripts/session_start.py must READ the open deferrals at session start.

Task: workspace/tasks/t796--make-session-start-read-the-open-deferrals-with.md (QUEUE.md:236)
RCA chain (each names the same cause: the deferral has a writer and no reader):
4a1a0fd358a8, 5d889eec288f, 5b128316dc5b, 63b7af778e85, 70b9877d980d, 7668c6830267,
7579c6b50d28, 0bd54c74cb5c, 0187a2dd15d6, 055ea073421c, 10563a0c54d3, 189819908584,
2604faf648ae, 28a8cea4c9a3, 295237521c28, 2c6800f20445, 2ec7230c3bd6, 2ef839117b53,
4027ae420d25, 5deefc2a67e5, 5e6c59cc67cf, 5f4c38335641, 8eb6f67543d7, 8dd23c39ab72,
a5b117ac4ae4.  055ea073421c: "the record had no reader for 10+ days".

RED ON MAIN FOR ONE REASON -- the missing change. scripts/session_start.py (936 lines,
49,492 bytes) contains the string "deferral" exactly once, in a comment at line 590; it
defines no deferral reader, names no deferral population, prints no deferral line, and
carries none of the twenty-five quotes below. Verified by grep.

WHY IT IMPORTS UNDER A NON-__main__ NAME. Running scripts/session_start.py is the session
job: git, the queue, the model lease. A fixture must not start it. So the module is loaded
by path with importlib under the name "t796_under_test", which makes the file's
`if __name__ == "__main__":` guard False, and main() cannot run. Only module-level
definitions are executed.

WHY IT READS workspace/session-reviews/findings/. That is the one deferral population that
exists on main (103 JSON files). workspace/deferrals.jsonl and
workspace/session-reviews/deferral-ledger.jsonl do not exist, and scripts/deferral_ledger.py
does not exist (t785 owns that file). A reader pointed at a ledger that is not on disk is a
clean report that means nothing.

The writer half of the task's decay fixture -- the row {session, deferrals_surfaced, ids,
verdict} -- belongs to scripts/session_review.py and is pinned by
scripts/tests/test_t1087_deferral_row.py, which is red on main today (session_review.py has
no record_deferral / deferral_rows by any spelling). This suite pins the READER half and
the quote set. Stated here, not hidden in a footnote.

No pytest (not installed). No subprocess, no urlopen, no model call, no live server. The
only writes are inside tempfile.mkdtemp(); the real findings directory is opened read-only
and one test proves the reader left it unchanged.

Run:  python3 scripts/tests/test_deferral_surfaces_at_start.py
"""
import importlib.util
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]          # scripts/tests -> repo root
READER = ROOT / "scripts" / "session_start.py"
FINDINGS = ROOT / "workspace" / "session-reviews" / "findings"

# The twenty-five deferral sentences this task's notes carry, one per RCA, copied verbatim
# from workspace/tasks/t796--make-session-start-read-the-open-deferrals-with.md. Drop one,
# re-word one, move one into a comment, and the suite goes red. This is the decay fixture
# the task asks for: a session that stops surfacing them is caught by its own words.
DECAY_QUOTES = (
    "Next session starts with batches of one, and reads that reply list first.",
    "and a backup script that dies mid-copy on a flaky mount should probably retry "
    "rather than abort",
    "The self-test caught a disagreement between me and the code.",
    "The other ~30 call sites (chat UI, probes, health checks) move later.",
    "Env 3 is stale again \u2014 it predates today's five fixes \u2014 so I'll re-clone "
    "before launching.",
    "Also I want to understand how the Station 2 Q4 is prioritizing tasks.",
    "Lets call it the Senior builder and it will be a place holder until we decide the "
    "actual Sr model.",
    "If you send any message, I'll pick it back up.",
    "Next session starts with `session_start.py`, which will surface your test results the "
    "moment you send them.",
    "[5], [7], [33], [34], [36], and [6] I'd leave for next session; they're reporting-surface "
    "defects that make the notes misleading but don't corrupt the delivered code.",
    "It keyed on `file:line`, so merging the pair's work shifted lines and it reported seven "
    "\"new\" sites that were the same seven, one edit later.",
    "Later, if the UI annoys you: I write a custom client with exactly the catalogue you "
    "pictured, against the same repo.",
    "I'll update it to the new decision with the reasoning recorded \u2014 and flag it to you, "
    "since I'm retiring a mechanism someone built deliberately.",
    "I'll log it as product feedback about the naming/UX confusion",
    "I'll watch the log \u2014 if a `status` line appears with a `160.79.x` client, the bridge "
    "is proven end to end and STATE.md can say so without hedging.",
    "Tonight runs by itself; Monday I'll have the scan findings, the root-cause plan, and that "
    "oracle.",
    "That's the effect you want, and it's the only part of today that actually demonstrates it.",
    "I'll refresh the handover at around 85% so it's current well before compression, rather "
    "than racing it.",
    "I'll answer each from the repository as it stands, and do the work where it's small.",
    "I'd suggest I reply to Chat next session rather than now \u2014 this one has run very long, "
    "and its three items each want a proper answer rather than a quick one.",
    "I'll work straight through each turn and restart myself between turns.",
    "Installing them \u2014 but I'll hold one back, and I want to be explicit about why.",
    "That disagreement is the most useful thing on the table, and I'll come back to it.",
    "I'll leave that as a design note rather than patching it now; another restart costs more "
    "than it buys with roughly three hours left, and the run is currently healthy and "
    "productive.",
    "Clean to restore \u2014 the stash holds exactly the three `L-119` fixes and doesn't touch "
    "`mutate.py`, so it can't collide with SB's in-flight work.",
)


def _source():
    assert READER.is_file(), "expected %s to exist -- the reader has no host file" % (READER,)
    return READER.read_text(encoding="utf-8")


def _code_lines(text):
    """(lineno, line) for non-blank, non-comment lines: a comment cannot satisfy a check."""
    out = []
    for n, line in enumerate(text.splitlines(), 1):
        s = line.strip()
        if s and not s.startswith("#"):
            out.append((n, line))
    return out


def _module():
    sys.path.insert(0, str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location("t796_under_test", str(READER))
    assert spec is not None and spec.loader is not None, (
        "cannot load scripts/session_start.py as a module -- the reader has no importable host")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)          # __name__ != "__main__", so main() is not run
    return mod


def _reader():
    """The two names the change must add. Red here IS the missing change, not a bug here."""
    mod = _module()
    missing = [n for n in ("deferral_context_lines", "deferral_rows_from_findings")
               if not hasattr(mod, n)]
    assert not missing, (
        "scripts/session_start.py defines no %s -- the deferral class has a writer and no reader "
        "(workspace/rca/0187a2dd15d6.md, 055ea073421c.md); the read side begins here"
        % (", ".join(missing),))
    return mod


def _row(**over):
    """The row shape pinned by scripts/tests/test_t1067_deferral_retirement_join.py:14-16."""
    r = {"ts": "2026-10-14T01:00:00Z", "session": "4a1a0fd358a8", "key": "4a1a0fd358a8",
         "quote": DECAY_QUOTES[0], "due": "", "tracked_as": ""}
    r.update(over)
    return r


def _collection_body(text, start_line):
    """The text of the collection literal that starts at start_line, by bracket balance."""
    depth = 0
    out = []
    for line in text.splitlines()[start_line - 1:]:
        out.append(line)
        depth += line.count("(") + line.count("[") + line.count("{")
        depth -= line.count(")") + line.count("]") + line.count("}")
        if depth <= 0:
            break
    return "\n".join(out)


# ----------------------------------------------------------- the reader's behaviour


def test_the_reader_exposes_the_deferral_reader():
    _reader()          # red today: neither name exists in scripts/session_start.py


def test_the_reader_lists_a_deferrals_own_quote_not_a_placeholder():
    """Two rows carrying two DIFFERENT real quotes and one distinct due date. A constant, a
    count, or one string copied into every row cannot pass: each line must carry its own row's
    text, and a due date must not leak between rows."""
    mod = _reader()
    rows = [_row(),
            _row(key="189819908584", session="189819908584", quote=DECAY_QUOTES[11],
                 due="2026-11-01")]
    lines = list(mod.deferral_context_lines(rows))
    assert len(lines) == 2, "expected one line per open row, got %r" % (lines,)
    assert DECAY_QUOTES[0] in lines[0], "row 1's own quote is not in its line: %r" % (lines[0],)
    assert DECAY_QUOTES[11] in lines[1], "row 2's own quote is not in its line: %r" % (lines[1],)
    assert "2026-11-01" in lines[1], "row 2's due date is not in its line: %r" % (lines[1],)
    assert "2026-11-01" not in lines[0], "row 2's due date leaked into row 1: %r" % (lines[0],)


def test_a_row_with_a_tracker_is_not_surfaced_and_an_untracked_one_is():
    """The open/undispositioned rule, taken from the repo's own spelling of an unowned deferral
    (scripts/tests/test_t1087_deferral_row.py:48: 'UNTRACKED: ... no owner recorded'). A row
    that names a task is carried by that task and must not be re-printed here."""
    mod = _reader()
    lines = list(mod.deferral_context_lines([
        _row(key="t796-carried", quote=DECAY_QUOTES[2], tracked_as="t796"),
        _row(key="0bd54c74cb5c", quote=DECAY_QUOTES[7],
             tracked_as="UNTRACKED: session-review deferral with no owner recorded"),
    ]))
    assert len(lines) == 1, "tracked rows must be dropped, untracked kept: %r" % (lines,)
    assert DECAY_QUOTES[7] in lines[0], "the untracked row's quote is missing: %r" % (lines[0],)


def test_the_quote_is_printed_whole_never_truncated():
    """never-truncate-a-record. A quote longer than any plausible cap must arrive whole; a
    sliced quote looks complete and loses the fact the reader exists to carry."""
    mod = _reader()
    long_quote = DECAY_QUOTES[0] + " " + ("deferral " * 200)
    lines = list(mod.deferral_context_lines([_row(quote=long_quote)]))
    assert len(lines) == 1, "expected one line, got %r" % (len(lines),)
    assert long_quote in lines[0], "the quote was cut: %r" % (lines[0][:160],)


def test_the_whole_open_set_is_surfaced_not_the_first_eight():
    """Session start's habit of printing the first eight is exactly how a deferral set of
    fourteen becomes invisible: the ninth is not a truncation to tolerate, it is a promise
    nobody receives."""
    mod = _reader()
    rows = [_row(key="s%02d" % i, session="s%02d" % i,
                 quote="%s #%d" % (DECAY_QUOTES[i % len(DECAY_QUOTES)], i))
            for i in range(14)]
    lines = list(mod.deferral_context_lines(rows))
    assert len(lines) == 14, "surfaced %d of 14 open deferrals" % (len(lines),)
    for i, r in enumerate(rows):
        assert r["quote"] in lines[i], "row %d lost its own text: %r" % (i, lines[i][:120])


def test_no_open_deferrals_prints_no_deferral_lines():
    """A green suite must not hide a reader that invents text when the set is empty."""
    mod = _reader()
    assert list(mod.deferral_context_lines([])) == []


def test_a_deferral_row_with_no_quote_still_names_its_key():
    """A header with nothing under it is the failure this repository has recorded twice; a row
    whose quote is empty must still say which finding it is."""
    mod = _reader()
    lines = list(mod.deferral_context_lines([_row(key="2ef839117b53", quote="")]))
    assert len(lines) == 1, "expected one line for a quote-less row, got %r" % (lines,)
    assert "2ef839117b53" in lines[0], "the row's key is missing: %r" % (lines[0],)


# -------------------------------------------------- the population it reads from


def test_the_reader_names_the_one_approved_deferral_population():
    """Red today: scripts/session_start.py contains no reference to session-reviews/findings.
    A reader pointed at a scratch file reads a fixture and reports the real set as empty."""
    hits = [n for n, line in _code_lines(_source())
            if "session-reviews" in line and "findings" in line]
    assert hits, (
        "scripts/session_start.py reads no deferral population: the reader must open the "
        "workspace's findings directory (session-reviews/findings), which is the deferral set "
        "that exists on main")


def test_the_reader_points_no_deferral_lookup_at_a_scratch_path():
    """Negative assertion: no deferral line may name /tmp."""
    bad = [n for n, line in _code_lines(_source())
           if "deferral" in line.lower() and "/tmp" in line]
    assert not bad, "deferral lookup pointed at a scratch path on line(s) %r" % (bad,)


def test_the_real_findings_directory_reads_read_only():
    """Real artefact, read-only (build fixtures from real artefacts). The directory's files are
    the population; parsing them must not raise, the reader must not write, and every display
    line must be non-empty text."""
    if not FINDINGS.is_dir():
        raise AssertionError("workspace/session-reviews/findings/ is gone -- the deferral "
                            "population this reader is built on; update the source it reads")
    mod = _reader()
    before = sorted(p.name for p in FINDINGS.iterdir())
    rows = list(mod.deferral_rows_from_findings(FINDINGS))
    after = sorted(p.name for p in FINDINGS.iterdir())
    assert before == after, "the reader wrote into the live findings directory"
    assert all(isinstance(r, dict) for r in rows), "rows must be mappings"
    for line in list(mod.deferral_context_lines(rows)):
        assert isinstance(line, str) and line.strip(), "blank deferral line: %r" % (line,)


# --------------------------------------------------------------- the decay fixture


def test_the_twenty_five_decay_quotes_live_in_a_named_collection():
    """The quotes must sit inside DEFERRAL_DECAY_QUOTES = ( ... ). A comment, a docstring, or
    one quote per function body cannot satisfy this: the check reads the collection's own text."""
    text = _source()
    starts = [n for n, line in _code_lines(text)
              if "DECAY_QUOTES" in line and "=" in line]
    assert starts, (
        "the deferral quotes must be a named collection in scripts/session_start.py "
        "(DEFERRAL_DECAY_QUOTES = (...)) -- prose in a comment satisfies no reader and no fixture")
    body = _collection_body(text, starts[0])
    missing = [q for q in DECAY_QUOTES if q not in body]
    assert not missing, (
        "the reader dropped %d of the %d deferral quote(s): %r -- each is a session's own words, "
        "copied verbatim from workspace/tasks/t796--make-session-start-read-the-open-deferrals-"
        "with.md, which names the RCA it came from" % (len(missing), len(DECAY_QUOTES), missing))


def test_the_reader_file_is_present_and_non_trivial():
    assert len(_source()) > 1000, "scripts/session_start.py is empty"


def test_the_block_is_surfaced_before_the_right_now_section():
    """RCA 70b9877d980d: 'before the Right-now block'; 055ea073421c: 'the first thing the
    session sees'. Surfaced after section 4 is surfaced late. Anchors are real lines today:
    def main(): at 320, section("4. Right now (from STATE.md)") at 733."""
    lines = _source().splitlines()
    anchor = [n for n, line in enumerate(lines, 1) if 'section("4. Right now' in line]
    assert anchor, ('the "4. Right now (from STATE.md)" heading is gone from '
                    'scripts/session_start.py -- update this anchor in the same commit that '
                    'moves it')
    start = [n for n, line in enumerate(lines, 1) if line.startswith("def main(")]
    assert start, "scripts/session_start.py no longer defines main() at module level"
    calls = [n for n, line in _code_lines(_source()) if "deferral_context_lines(" in line]
    assert calls, "main() never calls deferral_context_lines -- the block is built and dropped"
    inside = [n for n in calls if start[0] < n < anchor[0]]
    assert inside, (
        "the deferral call site must sit inside main() BEFORE section(\"4. Right now ...\") "
        "(between line %d and line %d); a deferral surfaced after the state section is a "
        "deferral read too late" % (start[0], anchor[0]))


def _run():
    tests = sorted((name, obj) for name, obj in globals().items()
                   if name.startswith("test_") and callable(obj))
    failed = 0
    for name, fn in tests:
        try:
            fn()
        except AssertionError as exc:
            failed += 1
            print("FAIL %s: %s" % (name, exc))
        except Exception as exc:            # a crash is a finding, never a pass
            failed += 1
            print("ERROR %s: %s: %s" % (name, type(exc).__name__, exc))
    print("%d/%d deferral-surface checks passed" % (len(tests) - failed, len(tests)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_run())
