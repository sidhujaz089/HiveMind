#!/usr/bin/env python3
"""t1261 -- scripts/check_serving_residency.py must probe residency, not promise it.

WHY (RCA workspace/rca/ded08d9a4746.md, session aeef1f57-5a5c-47b9-9d13-6a9c6fc9b373). On
2026-09-01T12:34:45Z the chat session promised: "smoke is currently the only thing resident,
which is useless to everyone -- I'll load whatever you want held once you've decided". The
promise was discharged at 12:51:12Z (transcript row 1828: `ollama ps` -> 53 GB, 100% GPU,
8-hour keep-alive) and recorded in NO durable artefact. `ollama ps` is a live read with no
history, and scripts/model_warm.py -- the consumer of a residency intent -- was deleted
(commit 92a17731) with no named successor. Thirty-seven days later a session review classed a
kept promise as a deferred one, because nothing on disk can answer "is the chat model
resident?".

THE CONTRACT THIS SUITE FREEZES (the implementation must honour these names and signatures):

  scripts/check_serving_residency.py
    GRACE_S: seconds a declared-resident model may be absent before the check fails
    VERIFY_TIMEOUT_S: seconds a single verify_command may run
    def serving_roles(root=None) -> list[dict]
        # one {"role": str, "tag": str, "verify_command": str} per entry of
        # <root>/projects/local-inference/models/SERVING.json's "roles" block whose
        # "resident_required" is EXACTLY True. Absent, false, or the string "true": not
        # included. Residency is declared, never inferred.
    def verify(entry, runner) -> tuple[bool, str]
        # runs shlex.split(entry["verify_command"]) as an argv LIST through
        # runner(argv, timeout=VERIFY_TIMEOUT_S, capture_output=True, text=True)
        # -- a list, never a shell string -- and returns
        # (returncode == 0, stdout + stderr VERBATIM, never sliced)
    def check(root=None, runner=None, state_path=None, now=None) -> int
        # 0  every resident_required tag resident, or absent inside GRACE_S
        # 1  a resident_required tag absent beyond GRACE_S (print the tag and the probe's
        #    whole detail -- an exit code alone is undiagnosable)
        # 2  a resident_required row with no runnable probe
        # runner: callable(argv: list[str], **kw) -> object with returncode/stdout/stderr.
        # state_path: path to a json file mapping tag -> first-absence epoch seconds.
        # now: epoch seconds.

RED ON MAIN FOR ONE REASON: scripts/check_serving_residency.py does not exist. Nothing here
touches a live Ollama: every probe goes through a stub that refuses traffic it was not written
to answer, and every path is under tempfile.
"""
import importlib.util
import json
import shlex
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

REPO = Path(__file__).resolve().parents[2]
MODULE = REPO / "scripts" / "check_serving_residency.py"
CMD = "ollama ps --format {{.Name}}"
ARGV = tuple(shlex.split(CMD))


def _mod():
    if not MODULE.exists():
        raise AssertionError(
            "t1261: %s does not exist. This suite is its frozen contract: a residency promise "
            "with no probe can be kept in a transcript and lost everywhere else "
            "(RCA ded08d9a4746)." % MODULE.relative_to(REPO))
    spec = importlib.util.spec_from_file_location("check_serving_residency", MODULE)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _root(tmp, roles):
    """A throwaway repo whose only content is a SERVING.json roles block."""
    d = Path(tmp) / "projects" / "local-inference" / "models"
    d.mkdir(parents=True)
    (d / "SERVING.json").write_text(json.dumps({"roles": roles}), encoding="utf-8")
    return Path(tmp)


def _runner(script):
    """script: {argv-tuple: (returncode, stdout, stderr)}. Refuses argv it was not scripted
    for, so a probe this test did not author cannot silently consume a scripted reply."""
    calls = []

    def run(argv, **kw):
        calls.append((list(argv), dict(kw)))
        key = tuple(argv)
        if key not in script:
            raise AssertionError("probe not scripted for argv=%r kw=%r" % (argv, kw))
        code, out, err = script[key]
        return SimpleNamespace(returncode=code, stdout=out, stderr=err)

    run.calls = calls
    return run


def _capture(fn):
    import contextlib
    import io
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = fn()
    return code, buf.getvalue()


def test_serving_roles_reports_only_the_roles_that_must_be_held():
    m = _mod()
    with tempfile.TemporaryDirectory() as td:
        root = _root(td, {
            "chat": {"tag": "swift", "resident_required": True, "verify_command": CMD},
            "coder": {"tag": "gemma4:26b", "resident_required": False,
                      "verify_command": "ollama ps"},
            "embedder": {"tag": "qwen3-embedding:0.6b", "resident_required": "true",
                         "verify_command": "ollama ps"},
        })
        got = m.serving_roles(root)
    assert got == [{"role": "chat", "tag": "swift", "verify_command": CMD}], (
        "serving_roles must return one dict with keys role/tag/verify_command for each role "
        "whose resident_required is exactly True -- absent, false and the string 'true' are "
        "not declarations. Three roles, three distinct tags, three distinct commands; got "
        "%r" % (got,))


def test_verify_runs_the_declared_probe_verbatim():
    m = _mod()
    run = _runner({ARGV: (0, "swift\n", "")})
    ok, detail = m.verify({"role": "chat", "tag": "swift", "verify_command": CMD}, run)
    assert ok is True, "returncode 0 must report resident; got %r" % (ok,)
    assert detail == "swift\n", "detail must be the probe's stdout verbatim; got %r" % (detail,)
    assert len(run.calls) == 1, "one row is exactly one probe; got %r" % (run.calls,)
    argv, kw = run.calls[0]
    assert argv == shlex.split(CMD), (
        "the probe is the declared command split as argv -- not a rewritten command, not a "
        "shell string; got %r" % (argv,))
    assert kw.get("timeout") == m.VERIFY_TIMEOUT_S, (
        "a probe with no timeout can hang the check forever; kw=%r" % (kw,))
    assert kw.get("capture_output") is True and kw.get("text") is True, (
        "the probe's output IS the record of residency; a call that discards it leaves nothing "
        "to report. kw=%r" % (kw,))


def test_verify_records_the_whole_probe_output_not_a_prefix():
    """A truncated probe record reads as a complete one: the row is well-formed, the reason
    the model is not resident sat in the part that was thrown away (never-truncate-a-record,
    the class that hid console.py's [:180])."""
    m = _mod()
    long_out = "swift\n" + ("resident line\n" * 900)
    run = _runner({ARGV: (1, long_out, "boom: model is not loaded")})
    ok, detail = m.verify({"role": "chat", "tag": "swift", "verify_command": CMD}, run)
    assert ok is False, "returncode 1 must report NOT resident; got %r" % (ok,)
    assert long_out in detail, (
        "the whole stdout must be in the detail; the tail of this probe's output is missing")
    assert "boom: model is not loaded" in detail, (
        "the whole stderr must be in the detail; got %r" % (detail,))


def test_absent_inside_grace_opens_the_window_and_absent_beyond_it_fails():
    """Ollama evicts and reloads as normal. The finding is a promise that went unkept, not a
    model that was briefly not resident, so absence is charged from its first sighting."""
    m = _mod()
    with tempfile.TemporaryDirectory() as td:
        root = _root(td, {"chat": {"tag": "swift", "resident_required": True,
                                   "verify_command": CMD}})
        sp = Path(td) / "residency-state.json"
        run = _runner({ARGV: (1, "", "not loaded")})
        first = m.check(root=root, runner=run, state_path=sp, now=1000.0)
        assert first == 0, (
            "the first sighting of an absence opens the grace window, it does not fail the "
            "run; exit=%r" % (first,))
        st = json.loads(sp.read_text(encoding="utf-8"))
        assert st.get("swift") == 1000.0, (
            "the first absence is stamped at its own timestamp, so the grace window is "
            "measurable; state=%r" % (st,))
        late = m.check(root=root, runner=run, state_path=sp, now=1000.0 + m.GRACE_S + 1)
        assert late == 1, (
            "absent beyond GRACE_S=%r must fail the check -- that is the promise the 2026-09-01 "
            "session made and could not later prove; exit=%r" % (m.GRACE_S, late))


def test_a_returning_model_clears_its_absence_stamp():
    m = _mod()
    with tempfile.TemporaryDirectory() as td:
        root = _root(td, {"chat": {"tag": "swift", "resident_required": True,
                                   "verify_command": CMD}})
        sp = Path(td) / "residency-state.json"
        m.check(root=root, runner=_runner({ARGV: (1, "", "not loaded")}),
                state_path=sp, now=1000.0)
        back = m.check(root=root, runner=_runner({ARGV: (0, "swift\n", "")}),
                       state_path=sp, now=1500.0)
        assert back == 0, "a model that is resident again must pass; exit=%r" % (back,)
        st = json.loads(sp.read_text(encoding="utf-8"))
        assert "swift" not in st, (
            "a model that came back clears its absence stamp, so the next eviction starts a "
            "fresh grace window instead of inheriting the old one; state=%r" % (st,))


def test_a_residency_intent_with_no_probe_is_a_loud_failure():
    """The RCA's rule, one rung up from the transcript: a promise bound to a machine state
    must carry the probe that reads that state, and the reader must refuse the record without
    it. 'I'll load whatever you want held' shipped with no verify_command, so 'resident' was
    unverifiable 37 days on. Uncheckable is not a pass."""
    m = _mod()
    with tempfile.TemporaryDirectory() as td:
        root = _root(td, {"chat": {"tag": "swift", "resident_required": True}})
        sp = Path(td) / "residency-state.json"
        run = _runner({})
        code, out = _capture(lambda: m.check(root=root, runner=run, state_path=sp, now=1000.0))
    assert code == 2, (
        "a resident_required row with no verify_command must exit 2, not 0 -- a residency "
        "promise with no probe is advertising; exit=%r" % (code,))
    assert "chat" in out, (
        "the failure must name the role whose probe is missing; output:\n%s" % out)
    assert run.calls == [], (
        "a row that declares no probe must not run one; calls=%r" % (run.calls,))


def test_a_failure_names_the_model_and_the_probes_own_reason():
    """An error that names a symptom, not a cause, is the shape never-truncate-a-record
    names: an exit code with no tag and no probe output is undiagnosable from the log."""
    m = _mod()
    with tempfile.TemporaryDirectory() as td:
        root = _root(td, {"chat": {"tag": "swift", "resident_required": True,
                                   "verify_command": CMD}})
        sp = Path(td) / "residency-state.json"
        run = _runner({ARGV: (1, "", "model is not loaded")})

        def both():
            m.check(root=root, runner=run, state_path=sp, now=1000.0)
            return m.check(root=root, runner=run, state_path=sp, now=1000.0 + m.GRACE_S + 1)

        code, out = _capture(both)
    assert code == 1, "the second sighting beyond grace fails the check; exit=%r" % (code,)
    assert "swift" in out, "the report must name the model that is not resident; output:\n%s" % out
    assert "model is not loaded" in out, (
        "the report must carry the probe's own words, whole; output:\n%s" % out)


def test_the_module_declares_its_own_limits():
    m = _mod()
    assert isinstance(m.GRACE_S, (int, float)) and m.GRACE_S > 0, (
        "GRACE_S must be a positive number of seconds -- an unbounded grace window is a check "
        "that never fails")
    assert isinstance(m.VERIFY_TIMEOUT_S, (int, float)) and m.VERIFY_TIMEOUT_S > 0, (
        "VERIFY_TIMEOUT_S must be a positive number of seconds")


TESTS = [
    test_serving_roles_reports_only_the_roles_that_must_be_held,
    test_verify_runs_the_declared_probe_verbatim,
    test_verify_records_the_whole_probe_output_not_a_prefix,
    test_absent_inside_grace_opens_the_window_and_absent_beyond_it_fails,
    test_a_returning_model_clears_its_absence_stamp,
    test_a_residency_intent_with_no_probe_is_a_loud_failure,
    test_a_failure_names_the_model_and_the_probes_own_reason,
    test_the_module_declares_its_own_limits,
]

if __name__ == "__main__":
    failed = 0
    for t in TESTS:
        try:
            t()
        except Exception as e:
            failed += 1
            print("FAIL %s: %s: %s" % (t.__name__, type(e).__name__, e))
        else:
            print("ok   %s" % t.__name__)
    print("%d/%d passed" % (len(TESTS) - failed, len(TESTS)))
    sys.exit(1 if failed else 0)
