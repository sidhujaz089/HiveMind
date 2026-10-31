#!/usr/bin/env python3
"""The context guard must fire before the window ends the session."""
import json
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import context_watch as cw


def turn(inp=0, read=0, create=0):
    return json.dumps({"type": "assistant",
                       "message": {"usage": {"input_tokens": inp,
                                             "cache_read_input_tokens": read,
                                             "cache_creation_input_tokens": create}}})


def main():
    fails = []
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        f = d / "s.jsonl"

        # Cached tokens occupy the window exactly as uncached ones do. Counting only
        # input_tokens would have read this session as ~4k when it was near a million,
        # so the guard would never fire -- the failure it exists to prevent.
        f.write_text("\n".join([turn(inp=1000, read=800_000, create=100_000)]) + "\n")
        tok, turns = cw.occupancy(f)
        if tok != 901_000:
            fails.append(f"cache_read and cache_creation must count: got {tok:,}")
        if turns != 1:
            fails.append(f"turn count: got {turns}")

        # The LATEST turn is the occupancy. A compaction drops it, and reporting the peak
        # would leave the guard stuck at 'full' for the rest of the session.
        f.write_text(turn(inp=997_000) + "\n" + turn(inp=60_000) + "\n")
        tok, _ = cw.occupancy(f)
        if tok != 60_000:
            fails.append(f"after a compaction the LATEST turn wins: got {tok:,}")

        # Lines without usage (user turns, tool results) must not reset it to zero.
        f.write_text(turn(inp=500_000) + "\n" + json.dumps({"type": "user"}) + "\n")
        tok, turns = cw.occupancy(f)
        if tok != 500_000:
            fails.append(f"a usage-less line must not zero the reading: got {tok:,}")
        if turns != 1:
            fails.append(f"usage-less lines are not turns: got {turns}")

        # Malformed JSON is survivable; the transcript is written live and the last line
        # can be half-flushed when we read it.
        f.write_text(turn(inp=123) + "\n{ this is not json\n")
        try:
            tok, _ = cw.occupancy(f)
            if tok != 123:
                fails.append(f"a truncated tail must be skipped: got {tok}")
        except Exception as e:
            fails.append(f"a truncated tail must not raise: {e!r}")

        # newest_session picks the file being written NOW, not the biggest or the first.
        import os, time
        (d / "old.jsonl").write_text(turn(inp=9) + "\n" * 500)
        time.sleep(0.01)
        (d / "new.jsonl").write_text(turn(inp=1) + "\n")
        os.utime(d / "new.jsonl", (time.time() + 10, time.time() + 10))
        got = cw.newest_session(d)
        if got is None or got.name != "new.jsonl":
            fails.append(f"newest_session must follow mtime, got {got}")

        # The HOOK must be fail-safe and quiet. It runs on every prompt, so a raise or a
        # non-zero exit would cost Jas the ability to type -- a worse failure than the one
        # being guarded against.
        import io, contextlib
        # The hook is handed its transcript now; it no longer picks one by mtime.
        live = d / "live.jsonl"
        live.write_text(turn(inp=1000, read=800_000, create=100_000) + "\n")
        pl = lambda: io.StringIO(json.dumps({"transcript_path": str(live)}))
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = cw.hook(limit=1_000_000, at=0.90, notice=0.75, stdin=pl())
        if rc != 0:
            fails.append(f"hook must always exit 0, got {rc}")

        # Below the notice tier it says NOTHING. A guard that speaks every turn is one that
        # gets tuned out, and then it is not there when it matters.
        with contextlib.redirect_stdout(buf := io.StringIO()):
            cw.hook(limit=10**12, at=0.90, notice=0.75, stdin=pl())
        if buf.getvalue().strip():
            fails.append(f"hook must be silent when there is room: {buf.getvalue()[:60]!r}")

        # Above the threshold it must actually SAY so.
        with contextlib.redirect_stdout(buf := io.StringIO()):
            cw.hook(limit=1000, at=0.90, notice=0.75, stdin=pl())
        if "CONTEXT AT" not in buf.getvalue():
            fails.append("hook must print the banner past the threshold")

        # And something genuinely BROKEN must not escape. A missing HOME is not enough --
        # rglob on an absent directory returns empty and raises nothing, so that version of
        # this test passed while the mutation that removes the guard went MISSED. Make the
        # thing it calls actually fail.
        real_occ = cw.occupancy

        def explode(_p):
            raise RuntimeError("the transcript is a half-written mess")

        cw.occupancy = explode
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                rc = cw.hook(stdin=pl())
            if rc != 0:
                fails.append(f"a raising occupancy() must still exit 0, got {rc}")
        except Exception as e:
            fails.append(f"hook let an exception escape into the prompt path: {e!r}")
        finally:
            cw.occupancy = real_occ

    # THE RETIRED WATCHER MUST REFUSE.
    #
    # Not a style rule. session_start.py went on telling sessions to arm `--watch` for
    # hours after the hook replaced it, and the next session obeyed -- in the nohup form
    # the docstring explicitly warns against -- while an orphan watcher from the previous
    # session was still running underneath it. The refusal is what makes a stale
    # instruction fail loudly instead of producing a guard nobody is watching.
    #
    # Drive the REAL entry point, not the helper. L-117: a test that drives the helper
    # passes while the call site is broken.
    print("\nThe retired --watch:")
    import subprocess
    r = subprocess.run([sys.executable, str(REPO / "scripts" / "context_watch.py"),
                        "--watch", "--every", "1"],
                       capture_output=True, text=True, timeout=60)
    ok = r.returncode == 3
    print(f"  [{'ok' if ok else 'FAIL'}]   --watch refuses to start: exit {r.returncode}")
    if not ok:
        fails.append(f"--watch must refuse with exit 3, got {r.returncode}")
    said = "RETIRED" in r.stderr and "settings.json" in r.stderr
    print(f"  [{'ok' if said else 'FAIL'}]     and says what replaced it: {said}")
    if not said:
        fails.append("the refusal must name the hook that replaced it")
    blocked = "CONTEXT AT" not in r.stdout
    print(f"  [{'ok' if blocked else 'FAIL'}]     and never enters the watch loop: {blocked}")
    if not blocked:
        fails.append("--watch refused but still ran the loop")

    # The report and hook paths must NOT be caught by the refusal -- a guard that refuses
    # everything is as useless as one that refuses nothing.
    r2 = subprocess.run([sys.executable, str(REPO / "scripts" / "context_watch.py")],
                        capture_output=True, text=True, timeout=60)
    # The report reads the LIVE session: under the threshold it prints the occupancy line (exit 0),
    # over it the banner (exit 2, by design). Either is "the report ran"; this assumed the session
    # running the suite was always under the threshold, and failed at 96% on 2026-09-30.
    still = (r2.returncode == 0 and "context" in r2.stdout) or (r2.returncode == 2 and "CONTEXT AT" in r2.stdout)
    print(f"  [{'ok' if still else 'FAIL'}]   the plain report still runs: {still}")
    if not still:
        fails.append(f"the report path broke: exit {r2.returncode} {r2.stdout!r}")

    # SESSION_START MUST NOT TELL ANYONE TO ARM IT. This is the actual defect: the fix
    # landed in settings.json, LESSONS.md and PROJECT-INDEX.md, but not in the file every
    # session reads first, so the retired command kept being recommended.
    print("\nNothing instructs a session to arm the retired watcher:")
    # THE GUARD MUST MEASURE THE SESSION THAT IS ASKING.
    #
    # It used to take the newest .jsonl under ~/.claude/projects. On the first prompt of
    # a NEW session that file is the session that just ENDED -- which is near the limit
    # by construction, because that is why it ended. Observed 2026-09-02: every new
    # session opened with the full-stop banner reading 896,527, the previous session's
    # number, while the asking session held 61,746. The guard cried wolf on turn one,
    # every time, and the response it demands -- wind down, start nothing -- is the most
    # expensive thing to be wrong about.
    import io
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        mine = d / "mine.jsonl"
        mine.write_text(turn(inp=61_746) + "\n")
        theirs = d / "theirs.jsonl"
        theirs.write_text(turn(inp=896_527) + "\n")
        # theirs is newest, exactly as a just-ended session is
        import os, time as _t
        os.utime(mine, (1, 1))

        out = io.StringIO()
        _real = sys.stdout
        sys.stdout = out
        try:
            payload = io.StringIO(json.dumps({"transcript_path": str(mine)}))
            cw.hook(stdin=payload)
        finally:
            sys.stdout = _real
        said = out.getvalue()
        quiet = "896,527" not in said and "STOP what you are doing" not in said
        print(f"  [{'ok' if quiet else 'FAIL'}]   hook reads transcript_path, not the newest file")
        if not quiet:
            fails.append("hook reported another session's occupancy: " + said.strip()[:80])

        # A payload it cannot read must produce SILENCE, not a number from elsewhere.
        # Guessing is the whole bug; an unattributable number is worse than none.
        out = io.StringIO()
        sys.stdout = out
        try:
            cw.hook(stdin=io.StringIO("not json"))
        finally:
            sys.stdout = _real
        silent = out.getvalue().strip() == ""
        print(f"  [{'ok' if silent else 'FAIL'}]   unreadable payload -> silence, not a guess")
        if not silent:
            fails.append("hook invented a number with no payload: " + out.getvalue()[:80])

    # Every place that TELLS someone what to do, not just the two I first thought of.
    # The lesson is that the fix fans out; a test naming half the fan-out repeats it.
    for doc in ["scripts/session_start.py", "AGENTS.md", "STATE.md",
                "workspace/PROJECT-INDEX.md"]:
        body = (REPO / doc).read_text()
        bad = [ln.strip() for ln in body.splitlines()
               if "context_watch.py --watch" in ln and "RETIRED" not in ln.upper()
               and "retired" not in ln and "never" not in ln]
        clean = not bad
        print(f"  [{'ok' if clean else 'FAIL'}]   {doc}: {clean}")
        if not clean:
            fails.append(f"{doc} still recommends the retired watcher: {bad[0][:70]}")

    # A SESSION'S FIRST PROMPT MUST BE SILENT, however full the transcript looks.
    # Attributing to the asking session fixed the 2026-09-02 bug where a NEW session read
    # the PREVIOUS one's transcript. It does not fix a FORK: a forked session's transcript
    # legitimately contains the parent's turns, so occupancy reads high on turn one and the
    # banner tells a session with no work behind it to wind down -- the most expensive thing
    # to be wrong about. Jas, 2026-09-07: "the next session needs no info on how full this
    # one is and it can find its own session context amount."
    import json as _json
    import shutil as _shutil
    import subprocess as _sp
    import tempfile as _tf
    cache = Path.home() / ".cache" / "context_watch"
    saved = None
    if cache.exists():                       # never clobber the live markers
        saved = Path(_tf.mkdtemp()) / "ctx"
        _shutil.move(str(cache), str(saved))
    try:
        big = Path(_tf.mkdtemp()) / "t.jsonl"
        # a transcript that would certainly trip the banner
        big.write_text("\n".join(
            _json.dumps({"type": "assistant",
                         "message": {"usage": {"input_tokens": 990000,
                                               "output_tokens": 0}}})
            for _ in range(3)) + "\n")
        payload = _json.dumps({"session_id": "fixture-first-prompt",
                               "transcript_path": str(big)})
        outs = []
        for _ in range(2):
            r = _sp.run([sys.executable, str(REPO / "scripts" / "context_watch.py"), "--hook"],
                        input=payload, capture_output=True, text=True, timeout=60)
            outs.append(r.stdout.strip())
        first_silent = outs[0] == ""
        print(f"  [{'ok' if first_silent else 'FAIL'}]   first prompt of a session is silent: "
              f"{first_silent}")
        if not first_silent:
            fails.append(f"the banner fired on a session's first prompt: {outs[0][:60]}")
        later = bool(outs[1])
        print(f"  [{'ok' if later else 'FAIL'}]   and it reports from the second onwards: {later}")
        if not later:
            fails.append("the guard went silent forever, not just on the first prompt")
    finally:
        if saved:
            if cache.exists():
                _shutil.rmtree(cache, ignore_errors=True)
            _shutil.move(str(saved), str(cache))

    for x in fails:
        print("FAIL:", x)
    print("test_context_watch:", "FAILED" if fails else "ok")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
