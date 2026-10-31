"""t1233: coder.py's twin scan must not report a pid it cannot verify is running the job.

WHY THIS TEST EXISTS. Dispatch p528-t813-session-review on gemma4:26b (2026-10-16T00:33:59Z)
was refused by coder.py before the model was ever invoked, in 1 second, with a 6092-char brief
read whole:

    REFUSED: pid 3413699 is already running this job (brief-26bc13c2, model gemma4:26b).

The SAME scan printed two NOTE lines, FileNotFoundError on /proc/3237418/cmdline and
/proc/3349126/cmdline. Those are one defect seen from both sides. running_twin
(projects/local-inference/coder.py:1325) ENUMERATES pids from /proc (coder.py:1344), reads each
pid's cmdline (coder.py:1349) and state (coder.py:1355), and returns a pid that matches
(coder.py:1367) without reading it again. Enumeration and reading are not one atomic step, so
between the read that produced the match and the return the process can exit, or its pid can be
recycled to an unrelated program. A match on a pid that is gone is a stale positive: it refuses
a valid dispatch forever and the model is never invoked.

HOW THE RACE IS DRIVEN WITHOUT RACING IT. The fix gives running_twin an injectable process
table, read(pid, name), so this file scripts exactly what the scanner sees, when. A scripted
pid answers the first cmdline/stat read as a live twin and the SECOND read as a gone process:
that is a process exiting mid-scan, reproduced deterministically. Nothing is spawned, nothing
is written, and the real /proc is touched read-only by the last test only.

RED on main for the reason named in each test, not for a bug in this file.
"""
import io
import importlib.util
import sys
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]          # scripts/tests/ -> repo root
CODER = ROOT / "projects" / "local-inference" / "coder.py"

SELF_PID = 1000                                     # the coder.py doing the looking
SELF_STAT = b"1000 (python3) R 1 0 0 0\n"          # parent 1: launched detached, no coder.py above
TWIN_PID = 3413699                                  # the pid the incident reported as a twin
MODEL = "gemma4:26b"
BRIEF_TEXT = ("t813 session review: make coder.py's twin guard say what it checked and "
              "when it checked it\n")
FILES = ["scripts/scope_queue.py", "workspace/tasks/QUEUE.md"]


def _load_coder():
    """Import coder.py by path, under its own name, so its __main__ block does not run.
    The idiom scripts/tests/test_coder_lease_outcome.py already uses."""
    spec = importlib.util.spec_from_file_location("coder_under_test", CODER)
    mod = importlib.util.module_from_spec(spec)
    with redirect_stdout(io.StringIO()):           # coder.py prints on import; not our output
        spec.loader.exec_module(mod)
    return mod


CODER_MOD = _load_coder()


def twin_argv():
    """An argv that IS a twin of this job, shaped exactly as coder.py reads argv off a real
    /proc: argv[0] is a python interpreter, argv[1] ends in coder.py, and --brief / --files /
    --model carry the real values. The label is job_label() OF THIS ARGV, never a literal, so
    the label path (coder.py:1305) is exercised rather than guessed at."""
    return [sys.executable, str(CODER), "--brief", BRIEF_TEXT, "--files", *FILES,
            "--model", MODEL]


def _argv_bytes(argv):
    return ("\0".join(argv) + "\0").encode()


def _stat(state, pid=TWIN_PID, ppid=1):
    """A /proc/<pid>/stat body. coder.py reads the state with .rsplit(") ", 1)[1].split()[0]
    and the parent pid from the next field, so those two fields are the only ones it reads."""
    return f"{pid} (python3) {state} {ppid} 0 0 0\n".encode()


class Scripted:
    """A read(pid, name) serving a scripted sequence per (pid, name).

    A (pid, name) the test did not script raises FileNotFoundError, exactly as a /proc entry
    for a pid that has exited does. Once a sequence runs out its LAST entry repeats, so an
    implementation that verifies with more reads than this file scripts is not failed for
    that. An entry that is an Exception instance is raised -- that is how a process that exits
    between the first read and the verification read is reproduced, with no race.
    """

    def __init__(self, pages, pids):
        self._pages = pages
        self._pids = sorted(int(p) for p in pids)
        self._at = {}
        self.calls = []

    def __call__(self, pid, name):
        pid = int(pid)
        self.calls.append((pid, name))
        if name == "pids":
            return " ".join(str(p) for p in self._pids).encode()
        seq = self._pages.get(pid, {}).get(name)
        if not seq:
            raise FileNotFoundError(f"/proc/{pid}/{name}")
        i = self._at.get((pid, name), 0)
        self._at[(pid, name)] = i + 1
        v = seq[i] if i < len(seq) else seq[-1]
        if isinstance(v, Exception):
            raise v
        return v


def _scan(label, files, model, read, self_pid=SELF_PID):
    """running_twin() with the injected process table -- the seam this brief's fix adds.

    On main running_twin(label, files, model) takes no keywords, so the call below raises
    TypeError. It is reported as an assertion failure that names the parameter, so this file is
    red for the reason it exists (a signature the fix adds) and not by crashing.
    """
    try:
        return CODER_MOD.running_twin(label, files, model, read=read, self_pid=self_pid)
    except TypeError as e:
        raise AssertionError(
            "running_twin() does not accept the injectable process table this brief adds "
            f"(read= and self_pid=): {e}. Without that seam the /proc race cannot be observed "
            "at all, so the fix has not landed."
        ) from None


def test_vanishing_pid_is_never_reported_as_a_twin():
    """The incident. A pid that looks like a live twin on the first read is gone by the moment
    the match is used. It must never be reported as a twin."""
    argv = twin_argv()
    label = CODER_MOD.job_label(argv)
    assert label, "job_label() must derive a label from the twin's own argv"
    table = Scripted(
        {SELF_PID: {"stat": [SELF_STAT]},
         TWIN_PID: {"cmdline": [_argv_bytes(argv), _argv_bytes(argv)],
                    "stat": [_stat("R"), FileNotFoundError(f"/proc/{TWIN_PID}/stat")]}},
        [SELF_PID, TWIN_PID])
    got = _scan(label, FILES, MODEL, table)
    assert got is None, (
        f"a pid that vanished between the read that matched it and the moment the match was "
        f"used was reported as a live twin: got {got!r}, expected None. That is the "
        f"p528-t813 refusal -- a dispatch blocked on a process that is not running the job."
    )


def test_recycled_pid_is_never_reported_as_a_twin():
    """A pid whose cmdline changes between the first read and the verification read has been
    recycled to an unrelated program. The identity that made it a twin is no longer true."""
    argv = twin_argv()
    label = CODER_MOD.job_label(argv)
    table = Scripted(
        {SELF_PID: {"stat": [SELF_STAT]},
         TWIN_PID: {"cmdline": [_argv_bytes(argv), _argv_bytes(["/usr/bin/ls", "-la"])],
                    "stat": [_stat("R"), _stat("R")]}},
        [SELF_PID, TWIN_PID])
    got = _scan(label, FILES, MODEL, table)
    assert got is None, (
        f"a pid whose argv changed between the two reads was reported as a twin of this job: "
        f"got {got!r}, expected None"
    )


def test_a_live_twin_is_still_reported_by_pid():
    """The guard must not be neutered to dodge the bug. A twin whose cmdline and state are
    identical on both reads is the case this function exists to catch, and it must be named by
    pid. An implementation that always returns None passes the two tests above; this is what
    stops that being the cheap green."""
    argv = twin_argv()
    label = CODER_MOD.job_label(argv)
    table = Scripted(
        {SELF_PID: {"stat": [SELF_STAT]},
         TWIN_PID: {"cmdline": [_argv_bytes(argv)] * 2, "stat": [_stat("R")] * 2}},
        [SELF_PID, TWIN_PID])
    got = _scan(label, FILES, MODEL, table)
    assert got == TWIN_PID, (
        f"a live twin with an unchanged cmdline and state must still be reported by pid: "
        f"got {got!r}, expected {TWIN_PID}"
    )


def test_a_zombie_is_not_a_twin():
    """State Z is finished, not running (coder.py:1329). The state must be checked on the
    VERIFICATION read, not only on the first one."""
    argv = twin_argv()
    label = CODER_MOD.job_label(argv)
    table = Scripted(
        {SELF_PID: {"stat": [SELF_STAT]},
         TWIN_PID: {"cmdline": [_argv_bytes(argv)] * 2, "stat": [_stat("Z")] * 2}},
        [SELF_PID, TWIN_PID])
    got = _scan(label, FILES, MODEL, table)
    assert got is None, f"a zombie is finished, not running: got {got!r}, expected None"


def test_an_unreadable_cmdline_is_never_a_positive():
    """The other half of the incident: a pid that exits before its cmdline can be read is a
    SKIP, never a match. Today it is a skip; the fix must keep it one."""
    argv = twin_argv()
    label = CODER_MOD.job_label(argv)
    table = Scripted(
        {SELF_PID: {"stat": [SELF_STAT]},
         TWIN_PID: {"cmdline": [FileNotFoundError(f"/proc/{TWIN_PID}/cmdline")]}},
        [SELF_PID, TWIN_PID])
    got = _scan(label, FILES, MODEL, table)
    assert got is None, f"a pid whose cmdline cannot be read must be skipped, not matched: {got!r}"


def test_a_wrapper_mentioning_coder_py_is_not_a_twin():
    """coder.py:1352 requires an INTERPRETER running coder.py as argv[1]. A bash wrapper that
    merely mentions the path is the case that made every wrapped dispatch refuse itself
    (coder.py:1332-1336). The fix must not loosen the match to buy its green."""
    argv = twin_argv()
    label = CODER_MOD.job_label(argv)
    wrapper = ["/bin/bash", "-c", " ".join(argv)]
    table = Scripted(
        {SELF_PID: {"stat": [SELF_STAT]},
         TWIN_PID: {"cmdline": [_argv_bytes(wrapper)] * 2, "stat": [_stat("R")] * 2}},
        [SELF_PID, TWIN_PID])
    got = _scan(label, FILES, MODEL, table)
    assert got is None, f"a shell wrapper is not a twin: got {got!r}, expected None"


def test_the_default_process_table_still_works_with_no_arguments():
    """main() calls running_twin(label, a.files, a.model) at coder.py:2490 with three
    positional arguments and no seam. That call must keep working against the real /proc, with
    no new dependency, spawning nothing. A label and model that no process on this box carries
    must not match: this reads the real /proc and finds the real answer, None."""
    got = CODER_MOD.running_twin("t1233-no-such-task-9f3c2a", FILES,
                                 "t1233-no-such-model-4a7f")
    assert got is None, (
        "the default (real /proc) scan must find no twin for a label no process on this box "
        f"has: got {got!r}"
    )


if __name__ == "__main__":
    _tests = [(n, f) for n, f in sorted(globals().items())
              if n.startswith("test_") and callable(f)]
    _failed = 0
    for _name, _fn in _tests:
        try:
            _fn()
            print(f"PASS {_name}")
        except AssertionError as e:
            _failed += 1
            print(f"FAIL {_name}\n  {e}")
        except Exception as e:          # a crash is a FAILURE, recorded with its type
            _failed += 1
            print(f"FAIL {_name}\n  {type(e).__name__}: {e}")
    print(f"\n{len(_tests) - _failed}/{len(_tests)} passed")
    sys.exit(1 if _failed else 0)
