#!/usr/bin/env python3
"""t773: the autonomy loop records chunk state at its decision points.

CAUSE (RCA 536c412e8572, section 4 item T2): nothing on disk records, for a p-numbered chunk,
when its target suite was green, when its coder attempt failed, and when it was escalated to
Gemma. The loop's per-chunk history is free text in workspace/run-logs/autonomy.log, so a
deferral like "p10-gemma-autonomy is queued for Q16 to finish the remaining gaps" cannot be
checked against anything. This fixture is the collector's ratchet: it drives the real
scripts/autonomy_loop.sh over a stubbed work list and fails when a decision point writes no row.

RED ON MAIN FOR THAT REASON AND NO OTHER: the loop has no writer, so
workspace/run-logs/chunk-state.jsonl is never created and every check below fails on 'no rows in
workspace/run-logs/chunk-state.jsonl'. It is not red for its own bug: it imports nothing from
the repository, needs no network, runs no model and needs no pytest. The loop is driven from a
COPY of the script written into a temp directory whose REPO= line points at that directory, with
its work list replaced by four fixture chunks; a stub python3 serves exactly the calls the loop
makes and refuses every other call, so the fixture measures the loop and not an interpreter. It
writes nothing outside its temp dir, gives every child a timeout, and kills the loop's process
group before it returns, so no process is left running. A full run takes seconds: the stub
dispatches instantly and the fixture stops the loop as soon as the sweep reaches its last
decision point.

Run: python3 scripts/tests/test_chunk_state_ledger.py
"""
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
LOOP = REPO / "scripts" / "autonomy_loop.sh"
LEDGER_REL = "workspace/run-logs/chunk-state.jsonl"
KEYS = {"ts", "chunk", "brief", "target", "outcome"}
OUTCOMES = {"skip-green", "enrol", "attempt-1-fail", "attempt-2-fail", "asked-gemma",
            "delivered-attempt-1", "delivered-attempt-2"}

# Four fixture chunks: name|brief|files|target. Three distinct brief files and three distinct
# target suites, so a row cannot be filled with one repeated placeholder value. The fourth sits
# after the STOP marker is raised, so the loop's own stop check must keep it from running.
FIXTURE = [
    ("p900-green", "brief-p900-green.txt", "scripts/tests/suite_always_green.py", "green"),
    ("p901-once", "brief-p901-once.txt", "scripts/tests/suite_ok_after_once.py", "red"),
    ("p902-never", "brief-p902-never.txt", "scripts/tests/suite_never_ok.py", "red"),
    ("p903-after-stop", "brief-p903-after-stop.txt", "scripts/tests/suite_after_stop.py", "green"),
]
NAMES = set(name for name, _b, _s, _m in FIXTURE)
BY_CHUNK = dict((name, (brief, suite)) for name, brief, suite, _m in FIXTURE)

STUB = r"""#!/bin/sh
# Stub python3 for the t773 fixture. Serves ONLY the calls scripts/autonomy_loop.sh makes and
# refuses every other call, so a python invocation the fixture did not model fails loudly instead
# of being answered with the next scripted reply.
set -u
D="${FIXTURE_DIR:?FIXTURE_DIR must be set by the fixture}"
R="${FIXTURE_REPO:?FIXTURE_REPO must be set by the fixture}"
echo "stub: $*" >> "$D/calls.log"
case "${1:-}" in
  -c)
    # scripts/autonomy_loop.sh line 222: $(python3 -c 'import roles; ...') for the coder model tag
    echo stub-model
    exit 0
    ;;
  -)
    # ask_gemma's heredoc (line 232): python3 - "$name" "$log" <<'PY'
    cat >/dev/null 2>&1
    echo "DIAGNOSIS: fixture stub"
    echo "ACTION: SKIP"
    echo "WHY: fixture stub"
    exit 0
    ;;
  scripts/heartbeat.py)
    # beat() at line 18
    exit 0
    ;;
esac
for a in "$@"; do
  case "$a" in
    *coder.py)
      # run_chunk (line 220): the dispatch. Answer per target, by call number.
      tgt=""
      prev=""
      for x in "$@"; do
        if [ "$prev" = "--target" ]; then tgt="$x"; fi
        prev="$x"
      done
      base=$(basename "$tgt")
      n=$(cat "$D/n-$base" 2>/dev/null || echo 0)
      n=$((n + 1))
      echo "$n" > "$D/n-$base"
      if [ "$base" = "suite_ok_after_once.py" ] && [ "$n" -eq 1 ]; then
        echo "ACCEPTED on attempt 1 (fixture stub)"
      fi
      if [ "$base" = "suite_never_ok.py" ] && [ "$n" -eq 2 ]; then
        # The moment between the attempt-1-fail decision and the attempt-2-fail one: take the
        # ledger as it stands. A writer that buffers and flushes at exit has written nothing here.
        cp "$R/workspace/run-logs/chunk-state.jsonl" "$D/snapshot.jsonl" 2>/dev/null
        touch "$R/STOP"
      fi
      exit 0
      ;;
  esac
done
base=$(basename "${1:-}")
if [ -f "$D/mode/$base" ]; then
  mode=$(cat "$D/mode/$base")
  if [ "$mode" = "green" ]; then
    exit 0
  fi
  exit 1
fi
echo "python3 stub: refusing unexpected call: $*" >&2
exit 9
"""


def _write(path, text, mode=0o644):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    path.chmod(mode)


def _read(path):
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _loop_text(tmp):
    """The real loop, aimed at the temp repo, with the work list replaced by the fixture's."""
    script = LOOP.read_text(encoding="utf-8")
    anchor = "REPO=/home/user/ai-workspace"
    if anchor not in script:
        raise AssertionError("scripts/autonomy_loop.sh no longer pins REPO=/home/user/ai-workspace, "
                             "so the fixture cannot point the loop at a throwaway repo; running it "
                             "against the real REPO would write into the live workspace. Re-scope "
                             "this test.")
    script = script.replace(anchor, 'REPO="%s"' % tmp)
    head, sep, tail = script.partition("work () {")
    if not sep:
        raise AssertionError('scripts/autonomy_loop.sh has no "work () {" -- the fixture\'s hook '
                             'point for the work list is gone; re-scope this test')
    _body, close, after = tail.partition("\n}")
    if not close:
        raise AssertionError("could not find the closing brace of work () { in the loop; re-scope "
                             "this test")
    listed = "".join('    echo "%s|%s|%s|%s"\n' % (name, brief, suite, suite)
                     for name, brief, suite, _m in FIXTURE)
    return head + "work () {\n" + listed + "}" + after


def _setup_repo(tmp):
    _write(tmp / "autonomy_loop.sh", _loop_text(tmp))
    _write(tmp / "bin" / "python3", STUB, 0o755)
    for name, brief, suite, mode in FIXTURE:
        _write(tmp / "workspace" / "briefs" / brief,
               "TASK: t773 -- fixture brief for %s\n" % name)
        _write(tmp / suite, "raise SystemExit(0)\n")
        _write(tmp / "mode" / Path(suite).name, "%s\n" % mode)
    (tmp / LEDGER_REL).parent.mkdir(parents=True, exist_ok=True)


def _kill_group(proc):
    """The loop's last statement is 'sleep 60'; the whole process group must be gone when the
    fixture returns, so the builder's unattended run leaves no process behind."""
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        pass
    try:
        proc.wait(timeout=15)
    except subprocess.TimeoutExpired:
        pass


def _reached(tmp, base, n):
    try:
        return int(_read(tmp / ("n-" + base)).strip()) >= n
    except ValueError:
        return False


def _drive(tmp):
    """Run the loop over the fixture's work list and return the ledger text.

    Stops as soon as the dispatch stub has been called twice for suite_never_ok.py -- the last
    decision point of the sweep. On main, where no ledger exists, the same signal plus a six
    second grace ends the run, so the fixture is red in seconds, not in the loop's 60 second
    sleep."""
    env = dict(os.environ)
    env["PATH"] = os.pathsep.join([str(tmp / "bin"), env.get("PATH", "")])
    env["FIXTURE_DIR"] = str(tmp)
    env["FIXTURE_REPO"] = str(tmp)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    ledger = tmp / LEDGER_REL
    proc = subprocess.Popen(["bash", str(tmp / "autonomy_loop.sh")], cwd=str(tmp), env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            stdin=subprocess.DEVNULL, start_new_session=True)
    text = ""
    started = time.time()
    done_at = None
    try:
        while time.time() - started < 90:
            if ledger.is_file():
                text = ledger.read_text(encoding="utf-8")
            if _reached(tmp, "suite_never_ok.py", 2):
                if '"asked-gemma"' in text:
                    break
                if done_at is None:
                    done_at = time.time()
                if time.time() - done_at > 6:
                    break
            if proc.poll() is not None:
                break
            time.sleep(0.2)
    finally:
        _kill_group(proc)
    return text


def _rows(text):
    out = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except ValueError as e:
            raise AssertionError("ledger line is not a JSON object: %r (%s)" % (line[:200], e))
    return out


def test_ledger_has_a_row_for_every_decision_point(rows):
    assert rows, ("no rows in %s after the loop swept the fixture's work list: nothing on disk "
                  "records a chunk's state, so the loop's per-chunk history is free text in "
                  "autonomy.log (RCA 536c412e8572)" % LEDGER_REL)
    have = set((r.get("chunk"), r.get("outcome")) for r in rows)
    want = set([("p900-green", "skip-green"),
                ("p901-once", "enrol"),
                ("p902-never", "enrol"),
                ("p902-never", "attempt-1-fail"),
                ("p902-never", "attempt-2-fail"),
                ("p902-never", "asked-gemma")])
    missing = want - have
    assert not missing, ("no ledger row for %s; rows seen: %s" % (sorted(missing), sorted(have)))


def test_every_row_carries_the_five_fields_with_the_chunks_own_values(rows):
    assert rows, "no rows in %s" % LEDGER_REL
    for r in rows:
        assert isinstance(r, dict), "a ledger line is not a JSON object: %r" % (r,)
        assert set(r) == KEYS, ("row %r has keys %s; the contract is exactly %s"
                                % (r, sorted(r), sorted(KEYS)))
        assert r["chunk"] in NAMES, "row for a chunk the fixture never queued: %r" % (r["chunk"],)
        assert r["outcome"] in OUTCOMES, ("outcome %r is not one of %s"
                                          % (r["outcome"], sorted(OUTCOMES)))
        brief, suite = BY_CHUNK[r["chunk"]]
        assert r["brief"] == brief, ("row for %s carries brief %r; the work list's value is %r"
                                     % (r["chunk"], r["brief"], brief))
        assert r["target"] == suite, ("row for %s carries target %r; the work list's value is %r"
                                      % (r["chunk"], r["target"], suite))
        try:
            time.strptime(r["ts"], "%Y-%m-%dT%H:%M:%SZ")
        except (KeyError, TypeError, ValueError) as e:
            raise AssertionError("row for %s has ts %r, not a UTC stamp shaped "
                                 "YYYY-MM-DDTHH:MM:SSZ (%s)" % (r["chunk"], r.get("ts"), e))


def test_rows_are_written_at_the_decision_point_not_at_the_end(snap):
    assert snap, ("the ledger held no rows when the loop reached its second coder dispatch on "
                  "p902-never: rows are buffered and flushed at the end of the sweep, so a crash "
                  "mid-sweep leaves no record -- which is the whole reason this collector exists")
    have = set((r.get("chunk"), r.get("outcome")) for r in snap)
    want = set([("p902-never", "enrol"), ("p902-never", "attempt-1-fail")])
    assert want <= have, ("at the mid-sweep snapshot the ledger had %s; it must already carry %s"
                          % (sorted(have), sorted(want)))


def test_no_row_for_a_chunk_the_loop_never_ran(rows):
    seen = set(r.get("chunk") for r in rows)
    assert "p903-after-stop" not in seen, ("the loop recorded a row for p903-after-stop, which the "
                                           "STOP file stops the sweep before: the ledger is a dump "
                                           "of the work list, not a record of decisions taken")


def test_the_failed_chunks_rows_are_in_the_order_the_loop_takes_them(rows):
    assert rows, "no rows in %s" % LEDGER_REL
    order = [r.get("outcome") for r in rows if r.get("chunk") == "p902-never"]
    want = ["enrol", "attempt-1-fail", "attempt-2-fail", "asked-gemma"]
    assert [o for o in order if o in want] == want, ("p902-never's rows came in the order %s; the "
                                                     "loop enrols, records attempt 1 failing, "
                                                     "records attempt 2 failing, then asks Gemma"
                                                     % (order,))


def main():
    if not LOOP.is_file():
        print("FAIL: scripts/autonomy_loop.sh not found at %s" % LOOP)
        return 1
    if shutil.which("bash") is None:
        print("FAIL: bash is not on PATH; the loop is a bash script")
        return 1
    tmp = Path(tempfile.mkdtemp(prefix="t773-chunk-state-"))
    failures = []
    try:
        _setup_repo(tmp)
        syntax = subprocess.run(["bash", "-n", str(tmp / "autonomy_loop.sh")], cwd=str(tmp),
                                capture_output=True, text=True, timeout=60)
        assert syntax.returncode == 0, ("the rewritten loop does not parse: %s" % syntax.stderr[:800])
        text = _drive(tmp)
        rows = _rows(text)
        snap = _rows(_read(tmp / "snapshot.jsonl"))
        checks = [
            (test_ledger_has_a_row_for_every_decision_point, (rows,)),
            (test_every_row_carries_the_five_fields_with_the_chunks_own_values, (rows,)),
            (test_rows_are_written_at_the_decision_point_not_at_the_end, (snap,)),
            (test_no_row_for_a_chunk_the_loop_never_ran, (rows,)),
            (test_the_failed_chunks_rows_are_in_the_order_the_loop_takes_them, (rows,)),
        ]
        for fn, args in checks:
            try:
                fn(*args)
                print("ok   %s" % fn.__name__)
            except AssertionError as e:
                failures.append("%s: %s" % (fn.__name__, e))
                print("FAIL %s: %s" % (fn.__name__, e))
        if failures:
            log = _read(tmp / "calls.log")
            lines = log.splitlines()
            shown = lines[-40:]
            print("--- stub python3 received %d calls; last %d ---" % (len(lines), len(shown)))
            for line in shown:
                print(line)
    finally:
        shutil.rmtree(str(tmp), ignore_errors=True)
    if failures:
        print("%d of 5 checks FAILED" % len(failures))
        return 1
    print("all 5 checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
