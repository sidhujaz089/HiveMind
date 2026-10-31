#!/usr/bin/env python3
"""t1435 -- a report's commit citation is evidence only if the commit EXISTED when the claim
was spoken, and its diff touches a file the claim's own sentence names.

Source: workspace/rca/edb83642c3d2.md (finding edb83642c3d2, session d847024a-3567-41bc-
8a2e-63daa08e3f5d). A 2026-10-03 report said "It broke because of a real bug, not anything
you did wrong, and I found and fixed a second bug while I was there." It cited `ea2cef54`
11.027 s BEFORE that commit existed (message 17:40:34.973Z; author date 17:40:46Z) and cited
`1490dcf9` -- one file, test-only, +8/-2 -- for a six-file fix that lives in `1d471f64`.
Every citation in the report was unverifiable, so the reviewer found no covering artefact and
recorded a fix that is on main, with a red test, as `unresolved`.

Three findings, all planted in a real temp git repo (real `git show` plumbing, no stubs):
  predates-claim          author date is after the citing message's timestamp
  touches-no-named-file   the diff touches none of the repo paths the sentence names
  unknown-hash            the token is not a commit in that repo

Red on main because scripts/citation_audit.py has no audit_commit_citations: the six
behaviour tests raise AttributeError. test_existing_path_audit_is_preserved passes today --
it is the guard that the new function is added BESIDE audit_text, not in place of it.
"""
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import citation_audit as ca

# The incident's own numbers, from the RCA: the message that cited the "second bug".
CLAIM_TS = datetime(2026, 10, 3, 17, 40, 34, 973000, tzinfo=timezone.utc)
LATER = "2026-10-03T17:40:46+00:00"    # ea2cef54's author date: 11.027 s after the claim
EARLIER = "2026-10-03T17:37:58+00:00"  # 1d471f64's author date: 2 m 36 s before the claim


def _git(repo, *args):
    return subprocess.run(["git", "-C", str(repo)] + list(args),
                          capture_output=True, text=True, timeout=60)


def _repo(tmp):
    r = _git(Path(tmp), "init", "-q", str(tmp))
    assert r.returncode == 0, "git init: " + r.stdout + r.stderr
    return Path(tmp)


def _commit(repo, relpaths, author_iso):
    """One commit touching every path in relpaths, authored at author_iso. The date goes in
    as a unix epoch so git's date PARSER is not part of what this fixture tests."""
    for rel in relpaths:
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("# planted by the t1435 fixture\n", encoding="utf-8")
    epoch = int(datetime.fromisoformat(author_iso).timestamp())
    env = dict(os.environ, GIT_AUTHOR_DATE=f"@{epoch} +0000",
               GIT_COMMITTER_DATE="@1600000000 +0000")
    r = subprocess.run(["git", "-C", str(repo), "add", "--"] + list(relpaths),
                       capture_output=True, text=True, timeout=60, env=env)
    assert r.returncode == 0, "git add: " + r.stdout + r.stderr
    r = subprocess.run(["git", "-C", str(repo), "-c", "user.name=t1435",
                        "-c", "user.email=t1435@invalid", "-c", "commit.gpgsign=false",
                        "commit", "-q", "-m", "t1435 fixture"],
                       capture_output=True, text=True, timeout=60, env=env)
    assert r.returncode == 0, "git commit: " + r.stdout + r.stderr
    r = _git(repo, "rev-parse", "HEAD")
    assert r.returncode == 0, "git rev-parse: " + r.stdout + r.stderr
    return r.stdout.strip()


def test_citation_of_a_commit_created_after_the_claim_is_refused():
    # The ea2cef54 shape, with the RCA's real timestamps.
    with tempfile.TemporaryDirectory() as td:
        repo = _repo(td)
        h = _commit(repo, ["scripts/role_store.py"], LATER)
        msg = ("The Models tab works again. Second bug (%s) in scripts/role_store.py: "
               "an empty tag list was read as a broken config." % h)
        found = ca.audit_commit_citations(msg, CLAIM_TS, repo=repo)
        kinds = sorted(k for k, _c, _d in found)
        assert kinds == ["predates-claim"], f"expected one predates-claim: {found!r}"
        kind, cite, detail = found[0]
        assert cite == h, found
        # a RECORD, not a code: the detail names the author date and the claim time, so a
        # reader can see the gap (never-truncate-a-record: name the cause, not the symptom).
        assert "17:40:46" in detail, detail
        assert "17:40:34" in detail, detail


def test_citation_whose_diff_touches_no_named_file_is_refused():
    # The 1490dcf9 shape: the cited diff is one test file; the sentence names the store,
    # the writer and the lock. A reader who opens the cited commit confirms nothing.
    with tempfile.TemporaryDirectory() as td:
        repo = _repo(td)
        h = _commit(repo, ["scripts/tests/test_assigned_worker.py"], EARLIER)
        msg = ("Fixed (%s): role_store.py now takes a lock and the shared temp name is gone "
               "(scripts/role_store.py:77, projects/app-repo/roles_pages.py:77)." % h)
        found = ca.audit_commit_citations(msg, CLAIM_TS, repo=repo)
        kinds = sorted(k for k, _c, _d in found)
        assert kinds == ["touches-no-named-file"], f"expected one finding: {found!r}"
        kind, cite, detail = found[0]
        assert cite == h, found
        assert "test_assigned_worker.py" in detail, detail


def test_a_citation_that_checks_out_is_accepted():
    # The 1d471f64 shape: authored before the claim, diff names a file the sentence names.
    with tempfile.TemporaryDirectory() as td:
        repo = _repo(td)
        h = _commit(repo, ["scripts/role_store.py", "projects/app-repo/roles_pages.py"],
                    EARLIER)
        msg = ("Fixed (%s): role_store.py _write_json now uses mkstemp and save() holds "
               "locked() (scripts/role_store.py:77)." % h)
        found = ca.audit_commit_citations(msg, CLAIM_TS, repo=repo)
        assert found == [], f"a sound citation must yield no finding: {found!r}"


def test_a_token_that_is_not_a_commit_is_refused_by_name():
    with tempfile.TemporaryDirectory() as td:
        repo = _repo(td)
        _commit(repo, ["scripts/role_store.py"], EARLIER)   # the repo has a commit
        msg = "Fixed (deadbeefcafe) -- scripts/role_store.py was the culprit."
        found = ca.audit_commit_citations(msg, CLAIM_TS, repo=repo)
        kinds = sorted(k for k, _c, _d in found)
        assert kinds == ["unknown-hash"], f"expected one unknown-hash: {found!r}"
        assert found[0][1] == "deadbeefcafe", found
        assert "deadbeefcafe" in found[0][2], found


def test_a_citation_that_names_no_path_is_not_reported_as_file_disjoint():
    # NEGATIVE. "touches-no-named-file" is only assertable when the sentence names a repo
    # path to compare against. A validator that fires on every prose citation is a validator
    # that gets switched off, so the absence of paths is not a finding.
    with tempfile.TemporaryDirectory() as td:
        repo = _repo(td)
        h = _commit(repo, ["scripts/role_store.py"], EARLIER)
        msg = "Fixed (%s). Well done, and not anything you did wrong." % h
        assert ca.audit_commit_citations(msg, CLAIM_TS, repo=repo) == [], "over-firing"


def test_non_commit_tokens_and_fenced_blocks_are_not_citations():
    # NEGATIVE, three ways: `main`/`HEAD` are not hex; 1790452260 is an all-digit epoch;
    # a hash inside a fenced code block is a sample, not a claim (audit_quotes' rule).
    with tempfile.TemporaryDirectory() as td:
        repo = _repo(td)
        _commit(repo, ["scripts/role_store.py"], EARLIER)
        msg = ("On `main`, branch `HEAD`, ended 1790452260, and inside a sample:\n"
               "```\ngit show 1d471f64\n```\nAll of those are prose, scripts/role_store.py:1.")
        found = ca.audit_commit_citations(msg, CLAIM_TS, repo=repo)
        assert found == [], f"non-citations were treated as claims: {found!r}"


def test_existing_path_audit_is_preserved():
    # The function the new one is added beside must keep working (passes on main).
    rows = ca.audit_text("the fix is at `scripts/role_store.py:44`")
    assert ("ok", "scripts/role_store.py:44") in rows, rows


def _main():
    tests = [(k, v) for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    failed = []
    for name, fn in tests:
        try:
            fn()
            print(f"ok   {name}")
        except Exception as e:
            failed.append(name)
            print(f"FAIL {name}: {type(e).__name__}: {e}")
    print(f"{len(tests) - len(failed)}/{len(tests)} passed"
          + (f"; failed: {', '.join(failed)}" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_main())
