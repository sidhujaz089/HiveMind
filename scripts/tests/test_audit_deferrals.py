"""t1041: audit every open deferral finding for a kept-promise phantom.

Red on main: scripts/session_review.py has no `audit-deferrals` subcommand, so every test
stops at the returncode assertion with argparse's "invalid choice: 'audit-deferrals'" in the
message (main's positional `cmd` choices, scripts/session_review.py:1292). That is the missing
change, not a bug in this file.

Runs standalone: python3 scripts/tests/test_audit_deferrals.py  (pytest is not installed).
Touches only temp dirs -- the findings tree, the transcripts, the task tree and a throwaway git
repo are all built under tempfile.TemporaryDirectory. No model call, no live state.
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "session_review.py"

KEYSET = {"key", "session", "turn", "quoted_task", "task_status",
          "artefact_commit", "verdict"}

# Strings taken from the corpus this task was scoped from (workspace/rca/50f3d3f7348b.md).
Q_KEEP = "Once that lands, I remove the skill from the Manager role."
Q_SHIM = "Once that ships, I will delete the shim."
Q_SHRED = "Once that is in, I retire the watchdog."
BOGUS_HASH = "000000000000"
REAL_COVERED_BY = "1a4e06c7"      # the covered_by value present in the live findings corpus
SESSION = "721de5a0-effc-479b-bb75-99ca00223ee9"


def _run(args, timeout=180):
    return subprocess.run([sys.executable, str(SCRIPT), *args],
                          capture_output=True, text=True, timeout=timeout)


def _git(repo, *args, timeout=30):
    return subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True, timeout=timeout)


def _mk_finding(fdir, key, session, quote, where="assistant, turn 147 of 294",
                kind="deferral", covered_by=""):
    fdir.mkdir(parents=True, exist_ok=True)
    (fdir / (key + ".json")).write_text(json.dumps({
        "key": key, "session": session, "quote": quote, "where": where,
        "kind": kind, "covered_by": covered_by, "disposition": "deferred",
        "context": "The one place a real timeout increase *is* warranted is anything "
                   "I run in the foreground.",
        "why_missed": "never mentioned again; no covering task found",
        "candidates_consulted": ["t628", "t629"],
    }), encoding="utf-8")


def _mk_turn(troot, session, texts):
    """One jsonl file, one record per entry in `texts`.

    A str entry uses the list-of-blocks shape; a ('plain', str) entry uses a plain-string
    message.content. Record 0 is a real shape the probe found in the live transcripts: a line
    with NO `message` key at all (type "mode"), which the audit must skip, not crash on.
    """
    d = troot / "-home-user-ai-workspace"
    d.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps({"type": "mode", "mode": "normal"})]
    for t in texts:
        if isinstance(t, tuple) and t[0] == "plain":
            msg = {"role": "assistant", "content": t[1]}
        else:
            msg = {"role": "assistant", "content": [{"type": "text", "text": t}]}
        lines.append(json.dumps({"type": "assistant", "message": msg}))
    (d / ("%s.jsonl" % session)).write_text("\n".join(lines) + "\n", encoding="utf-8")


def _mk_task(troot, tid, status):
    d = troot / "done" if status in ("done", "completed", "closed") else troot
    d.mkdir(parents=True, exist_ok=True)
    (d / ("%s--x.md" % tid)).write_text(
        "---\nid: %s\ntitle: fixture\ntype: task\nstatus: %s\n---\n\nBody.\n" % (tid, status),
        encoding="utf-8")


def _mk_repo(groot):
    """A throwaway repo with one commit; returns its real short hash."""
    groot.mkdir(parents=True, exist_ok=True)
    _git(groot, "init", "-q")
    (groot / "f.txt").write_text("one\n", encoding="utf-8")
    _git(groot, "add", "f.txt")
    r = _git(groot, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "m", "seed")
    assert r.returncode == 0, "fixture git repo failed: " + r.stderr[:200]
    r = _git(groot, "rev-parse", "--short", "HEAD")
    assert r.returncode == 0, "fixture git repo failed: " + r.stderr[:200]
    return r.stdout.strip()


def _fixture(tmp):
    """Seven in-scope deferrals + two out of scope. Returns CLI args, out path, hash, findings dir."""
    root = Path(tmp)
    fdir, troot, tasks, repo = (root / "findings", root / "projects",
                                root / "tasks", root / "repo")
    h = _mk_repo(repo)

    # F1 -- the shape that produced t1041: `t628` sits one SENTENCE LEFT of the quote, in the
    # SAME paragraph (its own paragraph, above the quote's), and t628 is done.
    _mk_finding(fdir, "aaaa00000001", SESSION, Q_KEEP)
    _mk_turn(troot, SESSION, [
        "Skills (filed as t628; the Manager applies it, as you designed):\n\n"
        "`t628` attaches it where briefs are written or judged: Scoping, Preflight "
        "and Supervision. It also adds *writing-oracles-for-delegation*. " + Q_KEEP,
    ])
    _mk_task(tasks, "t628", "done")

    # F2 -- id present, task NOT done: a queued task must not close a deferral.
    _mk_finding(fdir, "aaaa00000002", "11111111-1111-1111-1111-111111111111",
                 Q_SHIM, where="assistant, turn 12 of 40")
    _mk_turn(troot, "11111111-1111-1111-1111-111111111111", ["Filed as t777. " + Q_SHIM])
    _mk_task(tasks, "t777", "queued")

    # F3 -- a real commit hash in the enclosing paragraph, resolvable in --repo.
    _mk_finding(fdir, "aaaa00000003", "22222222-2222-2222-2222-222222222222",
                 Q_SHRED, where="assistant, turn 7 of 9")
    _mk_turn(troot, "22222222-2222-2222-2222-222222222222",
             ["Landed in %s. %s" % (h, Q_SHRED)])

    # F4 -- no transcript for this session at all.
    _mk_finding(fdir, "aaaa00000004", "33333333-3333-3333-3333-333333333333",
                 Q_KEEP, where="assistant, turn 3 of 3")

    # F5 -- plain-string content shape, id left of the quote, task completed.
    _mk_finding(fdir, "aaaa00000005", "44444444-4444-4444-4444-444444444444",
                 Q_SHIM, where="assistant, turn 88 of 100")
    _mk_turn(troot, "44444444-4444-4444-4444-444444444444",
             [("plain", "`t630` carries this. " + Q_SHIM)])
    _mk_task(tasks, "t630", "completed")

    # F6 -- the id sits in a DIFFERENT paragraph, one blank line away.
    _mk_finding(fdir, "aaaa00000006", "55555555-5555-5555-5555-555555555555",
                 Q_KEEP, where="assistant, turn 41 of 60")
    _mk_turn(troot, "55555555-5555-5555-5555-555555555555",
             [Q_KEEP + "\n\nTracked separately as t999."])

    # F7 -- a hash-SHAPED token that is not a commit in --repo.
    _mk_finding(fdir, "aaaa00000007", "66666666-6666-6666-6666-666666666666",
                 Q_SHRED, where="assistant, turn 1 of 2")
    _mk_turn(troot, "66666666-6666-6666-6666-666666666666",
             ["See %s. %s" % (BOGUS_HASH, Q_SHRED)])

    # Out of scope: not a deferral; and a deferral that already has a covering key.
    _mk_finding(fdir, "aaaa00000008", "77777777-7777-7777-7777-777777777777",
                 Q_KEEP, kind="unresolved")
    _mk_finding(fdir, "aaaa00000009", "88888888-8888-8888-8888-888888888888",
                 Q_KEEP, covered_by=REAL_COVERED_BY)

    out = root / "deferral-audit.jsonl"
    return {"args": ["--findings", str(fdir), "--projects-root", str(troot),
                     "--tasks-root", str(tasks), "--repo", str(repo), "--out", str(out)],
            "out": out, "hash": h, "findings": fdir}


def _rows(out):
    rows = []
    for line in Path(out).read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return {r["key"]: r for r in rows}


def test_resolved_kept_promise_id_left_of_quote():
    with tempfile.TemporaryDirectory() as tmp:
        fx = _fixture(tmp)
        r = _run(["audit-deferrals"] + fx["args"])
        assert r.returncode == 0, "exit %d: %s" % (r.returncode, r.stderr[-400:])
        got = _rows(fx["out"])["aaaa00000001"]
        assert got["quoted_task"] == "t628", got
        assert got["task_status"] == "done", got
        assert got["verdict"] == "resolved", got
        assert got["session"] == SESSION, got
        assert got["turn"] == "147", ("turn comes from the finding's `where`, not the jsonl "
                                      "line index (this quote sits on record 1)", got)
        assert got["artefact_commit"] == "", got


def test_id_outside_the_paragraph_is_not_a_cover():
    with tempfile.TemporaryDirectory() as tmp:
        fx = _fixture(tmp)
        _run(["audit-deferrals"] + fx["args"])
        got = _rows(fx["out"])["aaaa00000006"]
        assert got["quoted_task"] == "", ("t999 is in a different paragraph; using it is the "
                                          "fixed-width-slice defect in reverse", got)
        assert got["verdict"] == "untracked", got


def test_open_promise_reports_open_not_resolved():
    with tempfile.TemporaryDirectory() as tmp:
        fx = _fixture(tmp)
        _run(["audit-deferrals"] + fx["args"])
        got = _rows(fx["out"])["aaaa00000002"]
        assert got["quoted_task"] == "t777", got
        assert got["task_status"] == "queued", got
        assert got["verdict"] == "open", ("t777 is queued -- a queued task must never close "
                                          "a deferral", got)


def test_artefact_commit_is_a_resolved_hash_not_a_shape_match():
    with tempfile.TemporaryDirectory() as tmp:
        fx = _fixture(tmp)
        _run(["audit-deferrals"] + fx["args"])
        rows = _rows(fx["out"])
        assert rows["aaaa00000003"]["artefact_commit"] == fx["hash"], rows["aaaa00000003"]
        assert rows["aaaa00000003"]["verdict"] == "resolved", rows["aaaa00000003"]
        assert rows["aaaa00000007"]["artefact_commit"] == "", (
            BOGUS_HASH + " is hash-shaped but is not a commit in --repo", rows["aaaa00000007"])
        assert rows["aaaa00000007"]["verdict"] == "untracked", rows["aaaa00000007"]


def test_missing_transcript_is_its_own_verdict():
    with tempfile.TemporaryDirectory() as tmp:
        fx = _fixture(tmp)
        _run(["audit-deferrals"] + fx["args"])
        got = _rows(fx["out"])["aaaa00000004"]
        assert got["verdict"] == "transcript-missing", (
            "a session with no jsonl must not be read as 'untracked' -- that silently "
            "understates the phantom count", got)
        assert got["quoted_task"] == "" and got["artefact_commit"] == "", got


def test_plain_string_content_records_are_read():
    with tempfile.TemporaryDirectory() as tmp:
        fx = _fixture(tmp)
        _run(["audit-deferrals"] + fx["args"])
        got = _rows(fx["out"])["aaaa00000005"]
        assert got["quoted_task"] == "t630", ("message.content is a plain string in some "
                                              "transcript shapes", got)
        assert got["task_status"] == "completed", got
        assert got["verdict"] == "resolved", got
        assert got["turn"] == "88", got


def test_population_counts_in_scope_findings_and_prints_them():
    with tempfile.TemporaryDirectory() as tmp:
        fx = _fixture(tmp)
        r = _run(["audit-deferrals"] + fx["args"])
        assert r.returncode == 0, r.stderr[-400:]
        rows = _rows(fx["out"])
        assert "aaaa00000008" not in rows, ("kind=unresolved must not enter the deferral "
                                           "audit", sorted(rows))
        assert "aaaa00000009" not in rows, ("covered_by is already set -- the filter is "
                                           "kind=='deferral' AND empty covered_by", sorted(rows))
        assert len(rows) == 7, "population is the in-scope findings: %s" % sorted(rows)
        # Distinct, non-zero counts: a stub printing zeros cannot pass these four.
        for want in ("population: 7", "resolved: 3", "open: 1", "transcript-missing: 1"):
            assert want in r.stdout, "%r missing from %r" % (want, r.stdout[-400:])
        assert "untracked: 2" in r.stdout, r.stdout[-400:]


def test_rows_append_and_carry_the_seven_named_fields():
    with tempfile.TemporaryDirectory() as tmp:
        fx = _fixture(tmp)
        before = {p.name: p.read_bytes() for p in sorted(fx["findings"].glob("*.json"))}
        r1 = _run(["audit-deferrals"] + fx["args"])
        assert r1.returncode == 0, r1.stderr[-400:]
        r2 = _run(["audit-deferrals"] + fx["args"])
        assert r2.returncode == 0, r2.stderr[-400:]
        rows = _rows(fx["out"])
        assert len(rows) == 7, ("a second run appends to the audit file, it does not truncate "
                                "rows a previous run earned: %d keys" % len(rows))
        text = Path(fx["out"]).read_text(encoding="utf-8")
        assert text.count("\n") == 14, ("14 rows after two runs (append, one line per "
                                        "finding): %r" % text[:200])
        for key, row in rows.items():
            assert set(row) == KEYSET, (key, sorted(row))
            assert isinstance(row["key"], str) and row["key"], row
        # The audit READS findings; it never writes them (that write-back is a separate scope).
        after = {p.name: p.read_bytes() for p in sorted(fx["findings"].glob("*.json"))}
        assert after == before, "the audit edited a finding JSON"


def _main():
    tests = [(k, v) for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print("PASS", name)
        except Exception as exc:
            failed += 1
            print("FAIL %s: %s" % (name, repr(exc)[:500]))
    print("%d/%d passed" % (len(tests) - failed, len(tests)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_main())
