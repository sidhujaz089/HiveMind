"""t1019 -- session review must COMPUTE that a deferral was resolved later in the same
session before it spends a model call on an RCA.

Red on main for the stated reason, not for a bug of its own:
  * scripts/session_review.py defines no resolution_in_session, _session_resolution, _iso,
    _deferral_turn, and rca() makes its model call with no resolution check before it --
    test_names_exist, the rca wiring tests and the window tests fail;
  * _unusable() writes no visible record on the FIRST unusable attempt (it acts only at
    len(d["unusable"]) >= MAX_UNUSABLE), so test_first_unusable... fails.

Run: python3 scripts/tests/test_session_review_resolution.py
Stdlib only. No model calls, no live server, no fixed /tmp paths, no live state: every path
under test is a tempfile.mkdtemp tree and the module globals are pointed at it.
"""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]          # scripts/tests -> repo root

# rca() does `import skills` at call time; scripts/tests is sys.path[0] when this runs as a
# script, so stub it BEFORE anything calls into the module.
_sk = types.ModuleType("skills")
_sk.with_skills = lambda prompt, purpose, can_open=False, **kw: (prompt, [])
sys.modules.setdefault("skills", _sk)

SPEC = importlib.util.spec_from_file_location("session_review", ROOT / "scripts" / "session_review.py")
sr = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sr)

DEFERRAL = ("Backtick toggling can't handle template literals nested inside ${} -- that needs a "
            "real parser. So I'll make the check narrow and honest rather than pretend it's a "
            "syntax checker:")
FIRST_TS = 1_700_000_000          # arbitrary; the window is what the code under test must honour
ENDED = FIRST_TS + 86_400         # the session ran a day
ANSWER = ('[{"title": "Record the reason an RCA attempt returned nothing", '
          '"kind": "solution", "why": "the record is silent", "change": "record rc and elapsed", '
          '"covered_by": ""}]')


def _iso(epoch):
    return time.strftime("%Y-%m-%dT%H:%M:%S+0000", time.gmtime(epoch))


def _repo(tmp, rel, message, epoch, file_epoch):
    """A REAL git repo (a real artefact, not a mock): one added file, committed at `epoch`,
    with the file's mtime set to `file_epoch` so the file-window source can be pinned
    independently of the commit source."""
    (tmp / rel).parent.mkdir(parents=True, exist_ok=True)
    (tmp / rel).write_text("print('ok')\n", encoding="utf-8")
    os.utime(str(tmp / rel), (file_epoch, file_epoch))
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t",
               GIT_COMMITTER_DATE=f"@{int(epoch)}")
    for args in (["init", "-q"], ["add", "-A"], ["commit", "-q", "-m", message]):
        cp = subprocess.run(["git", "-C", str(tmp)] + args, capture_output=True, text=True,
                            timeout=120, env=env)
        assert cp.returncode == 0, f"git {args} failed rc={cp.returncode}: {cp.stderr[-300:]}"
    return tmp


NO_GIT = lambda args, cwd: ""     # noqa: E731 -- the text-only tests must not consult git


def test_names_exist():
    missing = [n for n in ("resolution_in_session", "_session_resolution", "_iso", "_deferral_turn")
               if not callable(getattr(sr, n, None))]
    assert not missing, f"session_review.py defines none of these: {missing}"


def test_later_turn_naming_a_task_resolves_the_deferral():
    ts = [("assistant", DEFERRAL),
          ("user", "Can you make yourself a parser so this doesn't happen again?"),
          ("assistant", "Good -- let me build the real thing. Filed as t412 so the class is recorded.")]
    art = sr.resolution_in_session(DEFERRAL, ts, FIRST_TS, ENDED, repo=ROOT, git=NO_GIT)
    assert art == "t412", f"expected the tNNN from the turn AFTER the deferral, got {art!r}"


def test_a_task_number_only_BEFORE_the_deferral_does_not_resolve_it():
    # The assertion that kills a whole-transcript scan: a tNNN that predates the deferral is not
    # evidence the deferral was honoured.
    ts = [("assistant", "We'll track that as t412 and come back to it."),
          ("assistant", DEFERRAL)]
    art = sr.resolution_in_session(DEFERRAL, ts, FIRST_TS, ENDED, repo=ROOT, git=NO_GIT)
    assert art == "", f"a tNNN that predates the deferral is not a resolution, got {art!r}"


def test_a_new_file_added_inside_the_window_resolves_it():
    tmp = Path(tempfile.mkdtemp(prefix="t1019-files-"))
    try:
        (tmp / "scripts").mkdir(parents=True, exist_ok=True)
        f = tmp / "scripts" / "jscheck.py"
        f.write_text("print('ok')\n", encoding="utf-8")
        inside = FIRST_TS + 600
        os.utime(str(f), (inside, inside))
        art = sr.resolution_in_session(DEFERRAL, [], FIRST_TS, ENDED, repo=tmp, git=NO_GIT)
        assert art.startswith("file:"), f"the artefact must say what kind it is, got {art!r}"
        assert "jscheck.py" in art, f"expected the added path, got {art!r}"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_a_file_that_existed_before_the_window_does_not_resolve_it():
    tmp = Path(tempfile.mkdtemp(prefix="t1019-files-"))
    try:
        (tmp / "scripts").mkdir(parents=True, exist_ok=True)
        f = tmp / "scripts" / "old.py"
        f.write_text("print('ok')\n", encoding="utf-8")
        before = FIRST_TS - 86_400
        os.utime(str(f), (before, before))
        art = sr.resolution_in_session(DEFERRAL, [], FIRST_TS, ENDED, repo=tmp, git=NO_GIT)
        assert art == "", f"a file that predates the session is not a resolution, got {art!r}"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_a_commit_inside_the_window_touching_the_subject_resolves_it():
    if shutil.which("git") is None:
        print("  SKIP test_a_commit_inside_the_window: git not on PATH")
        return
    tmp = Path(tempfile.mkdtemp(prefix="t1019-repo-"))
    try:
        # file mtime OUTSIDE the window, so the commit source -- not the file source -- fires.
        # The subject carries the quote's own first words so it matches under any ranking of
        # _terms(quote, 5).
        _repo(tmp, "scripts/jscheck.py", "backtick toggling: add the jscheck parser",
              FIRST_TS + 3600, FIRST_TS - 100)
        art = sr.resolution_in_session(DEFERRAL, [], FIRST_TS, ENDED, repo=tmp, git=None)
        assert art.startswith("commit:"), f"expected commit:<hash>, got {art!r}"
        assert len(art) > len("commit:"), f"the artefact must carry the hash, got {art!r}"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_a_commit_OUTSIDE_the_window_does_not_resolve_it():
    if shutil.which("git") is None:
        print("  SKIP test_a_commit_OUTSIDE_the_window: git not on PATH")
        return
    tmp = Path(tempfile.mkdtemp(prefix="t1019-repo-"))
    try:
        _repo(tmp, "scripts/jscheck.py", "backtick toggling: add the jscheck parser",
              FIRST_TS - 86_400, FIRST_TS - 100)
        art = sr.resolution_in_session(DEFERRAL, [], FIRST_TS, ENDED, repo=tmp, git=None)
        assert art == "", f"a commit from before the session is not a resolution, got {art!r}"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _rca_env(tmp, ts, ended_epoch=ENDED):
    """A finding file, its request record, its transcript, and every module global the gate
    reads, all pointed inside tmp. Restored by the caller's finally."""
    for d in ("findings", "done", "pending", "failed", "rca"):
        (tmp / d).mkdir(exist_ok=True)
    (tmp / "skill.md").write_text("the skill\n", encoding="utf-8")
    trans = tmp / "session.jsonl"
    trans.write_text(json.dumps({"role": "assistant", "timestamp": _iso(FIRST_TS)}) + "\n" +
                     json.dumps({"role": "user", "timestamp": _iso(ended_epoch)}) + "\n",
                     encoding="utf-8")
    (tmp / "done" / "req.json").write_text(
        json.dumps({"harness": "claude", "id": "sid", "path": str(trans),
                    "from_turn": 0, "to_turn": len(ts), "ended_epoch": ended_epoch}),
        encoding="utf-8")
    finding = tmp / "findings" / "1ed57f843ebe.json"
    finding.write_text(json.dumps({"key": "1ed57f843ebe", "kind": "deferral",
                                   "quote": DEFERRAL, "request": "req.json",
                                   "found": "2026-10-08T07:05:30Z"}), encoding="utf-8")
    names = ("REPO", "DONE", "PENDING", "FAILED", "FINDINGS", "RCA_DIR", "SKILL", "turns", "_log")
    old = {n: getattr(sr, n) for n in names}
    events = []
    sr.REPO = tmp
    sr.DONE, sr.PENDING, sr.FAILED = tmp / "done", tmp / "pending", tmp / "failed"
    sr.FINDINGS, sr.RCA_DIR, sr.SKILL = tmp / "findings", tmp / "rca", tmp / "skill.md"
    sr.turns = lambda harness, path: list(ts)
    sr._log = lambda row: events.append(row)
    return finding, events, old


def _restore(old):
    for n, v in old.items():
        setattr(sr, n, v)


RESOLVED_TS = [("assistant", DEFERRAL),
               ("user", "Can you make yourself a parser so this doesn't happen again?"),
               ("assistant", "Good -- let me build the real thing.")]
UNRESOLVED_TS = [("assistant", DEFERRAL),
                 ("user", "Noted; leave it for now.")]


def _run_rca(ts, file_epoch, commit_epoch):
    """Run rca() over a finding whose session has one added file committed at `commit_epoch`
    (mtime `file_epoch`). Returns (model_calls, events, task_ids, tmp, finding)."""
    tmp = Path(tempfile.mkdtemp(prefix="t1019-rca-"))
    finding, events, old = _rca_env(tmp, ts)
    _repo(tmp, "scripts/jscheck.py", "backtick toggling: add the jscheck parser",
          commit_epoch, file_epoch)
    calls = []

    def fake(prompt, purpose, *a, **kw):
        calls.append(purpose)
        return ANSWER

    try:
        ids = sr.rca(finding, asker=fake, file_task=lambda t, b: "t900")
    finally:
        _restore(old)
    return calls, events, ids, tmp, finding


def test_rca_skips_the_model_call_when_the_session_resolved_the_deferral():
    # commit inside the window, file mtime outside it, no tNNN in any later turn: the only
    # evidence is the session's own commit, so the gate must fire on it.
    calls, events, ids, tmp, finding = _run_rca(RESOLVED_TS, FIRST_TS - 100, FIRST_TS + 3600)
    try:
        assert calls == [], f"the session resolved this deferral; a model call was still made: {calls}"
        assert ids == [], f"a skipped RCA files no tasks, got {ids!r}"
        cov = [e for e in events if e.get("event") == "covered"]
        assert cov, f"no covered ledger row for a deferral the session closed: {events}"
        row = cov[0]
        assert row.get("by") == "resolution-check", f"covered row's basis: {row}"
        assert row.get("key") == "1ed57f843ebe", f"covered row must name the finding: {row}"
        art = str(row.get("artefact", ""))
        assert art.startswith("commit:") and len(art) > len("commit:"), \
            f"the covered row must carry the artefact (the commit hash), got {row}"
        assert row.get("model_call") is False, f"the covered row must say no model call ran: {row}"
        assert not list((tmp / "rca").glob("*.md")), \
            "a resolved deferral must not produce an RCA report"
        assert not finding.exists(), f"the finding must leave the queue: {finding}"
        assert (tmp / "findings" / "done" / "1ed57f843ebe.json").is_file(), \
            "the resolved finding must be moved to the findings/done archive"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_rca_still_asks_the_model_when_nothing_resolves_the_deferral():
    # commit dated a day BEFORE the session, file mtime before it, no tNNN after the deferral.
    # A build that suppresses every call fails this -- the gate must not become a mute.
    calls, events, ids, tmp, finding = _run_rca(UNRESOLVED_TS, FIRST_TS - 100, FIRST_TS - 86_400)
    try:
        assert len(calls) == 1, f"an unresolved deferral must still get exactly one model call: {calls}"
        assert "1ed57f843ebe" in calls[0], f"the call must be for this finding: {calls}"
        assert not [e for e in events if e.get("event") == "covered"], \
            f"nothing in the session resolved it, so no covered row: {events}"
        assert ids == ["t900"], f"the full RCA must still file through file_task: {ids!r}"
        assert list((tmp / "rca").glob("*.md")), \
            "the full RCA must still write its report -- the gate must not silence it"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_first_unusable_rca_attempt_gets_a_visible_owner_record():
    tmp = Path(tempfile.mkdtemp(prefix="t1019-unusable-"))
    item = tmp / "findings" / "1ed57f843ebe.json"
    item.parent.mkdir(parents=True)
    item.write_text(json.dumps({"key": "1ed57f843ebe"}), encoding="utf-8")
    names = ("FAILED", "FOR_JAS", "_log")
    old = {n: getattr(sr, n) for n in names}
    events, filed = [], []
    sr.FAILED, sr.FOR_JAS = tmp / "failed", tmp / "for-jas"
    sr._log = lambda row: events.append(row)
    try:
        given_up = sr._unusable(item, "no answer (timed out or empty)", "do the RCA",
                                file_task=lambda t, b: filed.append(t) or "t999")
        record = sorted(p for p in sr.FOR_JAS.glob("*") if p.is_file()) if sr.FOR_JAS.is_dir() else []
    finally:
        for n, v in old.items():
            setattr(sr, n, v)
    assert given_up is False, "one attempt must not give up: MAX_UNUSABLE stays 2"
    assert item.is_file(), "the finding stays in the queue at the first unusable attempt"
    assert filed == [], "the first attempt must not file a task -- the 2-attempt rule is unchanged"
    assert record, "the first unusable RCA attempt left no visible record under for-jas/"
    text = "\n".join(p.read_text(encoding="utf-8") for p in record)
    assert "1ed57f843ebe" in text, f"the owner record does not name the finding: {text[:300]}"
    assert "attempt 1: no answer (timed out or empty)" in text, \
        f"the owner record must number the attempt and carry the reason verbatim: {text[:300]}"
    assert any(e.get("event") == "rca-unusable-owner" and e.get("attempt") == 1
               and e.get("key") == "1ed57f843ebe" for e in events), \
        f"no ledger row records the owner record: {events}"


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"ok   {fn.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL {fn.__name__}: {e}")
        except Exception as e:
            failed += 1
            print(f"ERROR {fn.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(fns) - failed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
