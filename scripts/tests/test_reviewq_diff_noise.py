#!/usr/bin/env python3
"""t1036 -- the review queue states the two-dot diff noise as numbers.

WHY IT IS RED ON MAIN, one reason per test: scripts/reviewq.py has no diff_noise();
its queue rows print the literal "git diff main...<branch>" with no counts (def cmd_list,
the bridge loop's print and the dsh loop's print), the pending-run row prints
"git diff <branch>~1..<branch>" (def cmd_list, the `where = (...)` chain), and
def cmd_verdict writes its ledger `row = {...}` with no files_two_dot / files_base.
Every new name is reached through getattr, so its absence is an AssertionError naming
the missing count -- never a traceback.

THE FINDING THIS PINS. In workspace/rca/4248cb2e2d0e.md (transcript 2858 vs 2863) a
reviewer typed the two-dot form `git diff --stat main..coder/brief-1f5fcc2e`, read 10
files / 137 insertions / 269 deletions, and took it for the model's work; the branch's
own commit is 1 file / 135 insertions. Nine of ten files were other people's commits.
No artefact in this repository records that ratio -- not the queue row, not the verdict
ledger -- which is why the finding was only ever a sentence in a transcript.

Everything runs in a throwaway `git init` repository under a temp dir, with reviewq's
own module globals (REPO / RUNS / VERDICTS) pointed at it: no live ledger is read or
written, no network, no model, no process left running.

THE FIXTURE IS THE CONTRACT. main moves forward after the branch is cut, so
`git diff --name-only main..coder/feat` covers 4 files (a.py, changed by the branch,
plus b.py, c.py, d.py -- other people's work on main) while
`git diff --name-only $(git merge-base main coder/feat)..coder/feat` covers 1. A second
branch, coder/clean, is cut from main's TIP and has 1 and 1. No constant satisfies both,
so the counts must be COMPUTED per branch.
"""
import contextlib
import io
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
# The real scripts dir goes on sys.path so that `import scope_quality` inside
# reviewq._scope_event resolves while REPO points at the fixture repo (reviewq
# inserts `REPO / "scripts"`, which is the temp dir for the duration of a test).
sys.path.insert(0, str(ROOT / "scripts"))
import reviewq  # noqa: E402

TIMEOUT = 60
F1, F2 = "files_two_dot=4", "files_base=1"      # coder/feat: cut BEHIND main's head
C1, C2 = "files_two_dot=1", "files_base=1"      # coder/clean: cut from main's TIP
SHA = r"[0-9a-f]{40}"
ANCHOR = re.compile(r"git diff\s+(" + SHA + r")\.\.")
LITERAL = re.compile(r"git diff\s+main\.")


def ok(repo, *args):
    """Run git in the fixture repo. The -c identities make it independent of the machine."""
    r = subprocess.run(
        ["git", "-c", "user.email=t@example.invalid", "-c", "user.name=t",
         "-C", str(repo), *args],
        capture_output=True, text=True, timeout=TIMEOUT)
    assert r.returncode == 0, f"git {' '.join(args)} failed: {r.stderr.strip()}"
    return r.stdout.strip()


def build_repo(tmp):
    """main ahead of the branch, so two-dot and merge-base counts differ. Returns repo path."""
    repo = Path(tmp) / "repo"
    repo.mkdir()
    ok(repo, "init", "-q")
    (repo / "a.py").write_text("one\n", encoding="utf-8")
    ok(repo, "add", "a.py")
    ok(repo, "commit", "-qm", "base")
    ok(repo, "branch", "-m", "main")
    ok(repo, "branch", "coder/feat")                  # cut HERE, behind main's head
    for name in ("b.py", "c.py", "d.py"):             # other people's work, on main
        (repo / name).write_text("other people's work\n", encoding="utf-8")
    ok(repo, "add", "-A")
    ok(repo, "commit", "-qm", "other people's work")
    ok(repo, "checkout", "-q", "coder/feat")
    (repo / "a.py").write_text("one\nthe model's change\n", encoding="utf-8")
    ok(repo, "add", "-A")
    ok(repo, "commit", "-qm", "the model's change")
    ok(repo, "branch", "bridge/t9001", "coder/feat")  # same tip, the bridge twin
    ok(repo, "checkout", "-q", "main")
    ok(repo, "checkout", "-qb", "coder/clean")        # cut from main's TIP: no noise
    (repo / "e.py").write_text("clean\n", encoding="utf-8")
    ok(repo, "add", "-A")
    ok(repo, "commit", "-qm", "clean change")
    ok(repo, "checkout", "-q", "main")
    return repo


def use_repo(repo, tmp):
    """Point reviewq's module globals at the fixture; hand back the originals.

    The three rows: t9001 on a branch cut BEHIND main (files_two_dot 4, files_base 1);
    t9002 on a branch cut from main's TIP (1 and 1); t9003 with NO branch key at all.
    """
    old = (reviewq.REPO, reviewq.RUNS, reviewq.VERDICTS)
    reviewq.REPO = repo
    reviewq.RUNS = Path(tmp) / "agent-runs.jsonl"
    reviewq.VERDICTS = Path(tmp) / "review-verdicts.jsonl"
    rows = [
        {"at": "2026-10-11T00:00:00Z", "task": "t9001", "outcome": "ACCEPTED",
         "branch": "coder/feat", "attempts": 1, "tokens": 100,
         "why": "fixture run, branch cut behind main"},
        {"at": "2026-10-11T00:00:01Z", "task": "t9002", "outcome": "ACCEPTED",
         "branch": "coder/clean", "attempts": 1, "tokens": 100,
         "why": "fixture run, branch cut from main's tip"},
        {"at": "2026-10-11T00:00:02Z", "task": "t9003", "outcome": "ACCEPTED",
         "attempts": 1, "tokens": 100, "why": "fixture run with no branch"},
    ]
    reviewq.RUNS.write_text("".join(json.dumps(r) + "\n" for r in rows),
                            encoding="utf-8")
    return old


def restore(old):
    reviewq.REPO, reviewq.RUNS, reviewq.VERDICTS = old


def noise(branch):
    """Call the new helper through getattr: its absence is a message, not a traceback."""
    fn = getattr(reviewq, "diff_noise", None)
    assert callable(fn), ("scripts/reviewq.py has no diff_noise(branch) to compute the "
                          "two-dot vs merge-base file counts")
    d = fn(branch)
    assert isinstance(d, dict), f"diff_noise must return a dict, got {type(d).__name__}"
    for k in ("commits", "files_two_dot", "files_base"):
        assert isinstance(d.get(k), int), f"diff_noise() must carry an int {k}, got {d!r}"
    return d


def cli(argv):
    """Drive reviewq's CLI (module docstring Usage: bare == the queue listing;
    'verdict <task> kept --why ...' records a verdict). A sys.exit() becomes the exit
    code, so a success never reads as a crash and a usage error never as a pass."""
    fn = getattr(reviewq, "main", None)
    assert callable(fn), "scripts/reviewq.py exposes no main() to drive the CLI"
    try:
        try:
            return fn(list(argv))
        except TypeError:
            saved = sys.argv
            sys.argv = ["reviewq.py", *list(argv)]
            try:
                return fn()
            finally:
                sys.argv = saved
    except SystemExit as e:
        return e.code


def listing():
    """Capture what `reviewq.py` with no subcommand prints."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = cli([])
    assert rc in (0, None), f"the queue listing exited {rc!r}:\n{buf.getvalue()}"
    return buf.getvalue()


def rows_written():
    text = (reviewq.VERDICTS.read_text(encoding="utf-8")
            if reviewq.VERDICTS.is_file() else "")
    return [json.loads(ln) for ln in text.splitlines() if ln.strip()]


def diff_lines(out, needle):
    """Printed lines that carry a diff command for this branch.

    Line-scoped on purpose: the counts must sit on the row of the branch they describe,
    so printing them once anywhere in the output satisfies nothing here.
    """
    lines = [ln for ln in out.splitlines() if needle in ln and "git diff" in ln]
    assert lines, f"the queue printed no 'git diff' line mentioning {needle!r}:\n{out}"
    return lines


def test_diff_noise_counts_the_files_the_two_dot_form_adds():
    with tempfile.TemporaryDirectory() as tmp:
        repo = build_repo(tmp)
        old = use_repo(repo, tmp)
        try:
            d = noise("coder/feat")
            assert d["files_two_dot"] == 4, (
                "main..coder/feat covers 4 files: a.py, changed by the branch, plus "
                f"b.py, c.py and d.py, which exist only on main. got {d!r}")
            assert d["files_base"] == 1, (
                "merge-base..coder/feat covers 1 file: the branch's own change to a.py. "
                f"got {d!r}")
            assert d["commits"] == 1, (
                f"git rev-list --count main..coder/feat is 1, got {d!r}")
            assert d["files_two_dot"] > d["files_base"], (
                "a branch cut behind main must state the two-dot count ABOVE the "
                f"merge-base count -- that ratio is the whole finding. got {d!r}")
        finally:
            restore(old)


def test_diff_noise_is_computed_per_branch_not_a_constant():
    with tempfile.TemporaryDirectory() as tmp:
        repo = build_repo(tmp)
        old = use_repo(repo, tmp)
        try:
            d = noise("coder/clean")
            assert d["files_two_dot"] == 1, (
                "a branch cut from main's tip differs from main in 1 file, "
                f"got {d!r}")
            assert d["files_base"] == 1, (
                f"and its merge-base diff is the same 1 file, got {d!r}")
            assert d["commits"] == 1, f"commits = 1, got {d!r}"
        finally:
            restore(old)


def test_diff_noise_never_raises_for_a_branch_that_is_not_there():
    with tempfile.TemporaryDirectory() as tmp:
        repo = build_repo(tmp)
        old = use_repo(repo, tmp)
        try:
            d = noise("coder/gone")
            assert d["files_two_dot"] == d["files_base"], (
                "an unresolvable branch must claim NO noise -- equal counts, so a "
                f"reviewer is not told a ratio was measured. got {d!r}")
            assert d["commits"] == 0, (
                f"an unresolvable branch has 0 commits, got {d!r}")
        finally:
            restore(old)


def test_queue_print_anchors_the_diff_on_a_computed_merge_base_sha():
    with tempfile.TemporaryDirectory() as tmp:
        repo = build_repo(tmp)
        old = use_repo(repo, tmp)
        try:
            sha = ok(repo, "merge-base", "main", "bridge/t9001")
            assert re.fullmatch(SHA, sha), (
                f"the fixture's merge-base is not a 40-hex sha: {sha!r}")
            out = listing()
            lines = diff_lines(out, "bridge/t9001")
            hit = [m for m in (ANCHOR.search(ln) for ln in lines) if m]
            assert hit, (
                "the bridge row must print the merge-base-anchored command "
                f"git diff {sha}..<branch>, so it is copy-pasteable and stable; "
                "got:\n" + "\n".join(lines))
            assert hit[0].group(1) == sha, (
                f"the printed command is anchored on {hit[0].group(1)}, not on this "
                f"branch's own merge-base {sha}")
            bad = [ln for ln in lines if LITERAL.search(ln)]
            assert not bad, (
                "the literal 'git diff main...' form is what a reviewer copies and is "
                "what misled the session this task came from; the computed sha replaces "
                f"it, got:\n" + "\n".join(bad))
        finally:
            restore(old)


def test_queue_print_states_the_noise_on_the_bridge_row():
    with tempfile.TemporaryDirectory() as tmp:
        repo = build_repo(tmp)
        old = use_repo(repo, tmp)
        try:
            lines = diff_lines(listing(), "bridge/t9001")
            assert any(F1 in ln and F2 in ln for ln in lines), (
                "the bridge row must carry both counts on the same line as its diff "
                f"command ({F1} {F2}), got:\n" + "\n".join(lines))
        finally:
            restore(old)


def test_queue_print_states_the_noise_on_the_run_row():
    with tempfile.TemporaryDirectory() as tmp:
        repo = build_repo(tmp)
        old = use_repo(repo, tmp)
        try:
            lines = diff_lines(listing(), "coder/feat")
            assert any(F1 in ln and F2 in ln for ln in lines), (
                "the queue row for the run that produced coder/feat must state "
                f"{F1} {F2}, got:\n" + "\n".join(lines))
        finally:
            restore(old)


def test_queue_print_states_no_noise_for_a_branch_cut_from_main():
    with tempfile.TemporaryDirectory() as tmp:
        repo = build_repo(tmp)
        old = use_repo(repo, tmp)
        try:
            lines = diff_lines(listing(), "coder/clean")
            assert any(C1 in ln and C2 in ln for ln in lines), (
                "a branch cut from main's tip has no noise -- it must report "
                f"{C1} {C2}, got:\n" + "\n".join(lines))
            assert not any(F1 in ln for ln in lines), (
                f"coder/clean must not be shown the noisy branch's {F1}: "
                + "\n".join(lines))
        finally:
            restore(old)


def test_queue_print_adds_no_fragment_to_a_run_with_no_branch():
    with tempfile.TemporaryDirectory() as tmp:
        repo = build_repo(tmp)
        old = use_repo(repo, tmp)
        try:
            out = listing()
            for ln in [l for l in out.splitlines() if "DRY RUN" in l]:
                assert "files_two_dot=" not in ln, (
                    "a run with no branch has no diff to size; the counts belong on a "
                    f"branch row, not the dry-run row: {ln!r}")
        finally:
            restore(old)


def test_verdict_ledger_row_records_both_counts():
    with tempfile.TemporaryDirectory() as tmp:
        repo = build_repo(tmp)
        old = use_repo(repo, tmp)
        try:
            rc = cli(["verdict", "t9001", "kept", "--why", "fixture verdict for t1036"])
            assert rc in (0, None), f"the verdict command returned {rc!r}"
            rows = [r for r in rows_written() if r.get("task") == "t9001"]
            assert rows, f"cmd_verdict wrote no ledger row for t9001: {rows_written()!r}"
            row = rows[-1]
            assert row.get("verdict") == "kept", f"verdict row: {row!r}"
            assert row.get("files_two_dot") == 4, (
                "the verdict ledger row must record files_two_dot=4, the number a "
                f"reviewer needs and no artefact writes today, got {row!r}")
            assert row.get("files_base") == 1, (
                f"and files_base=1, the branch's own file count, got {row!r}")
        finally:
            restore(old)


def test_verdict_ledger_row_has_no_counts_when_the_run_has_no_branch():
    with tempfile.TemporaryDirectory() as tmp:
        repo = build_repo(tmp)
        old = use_repo(repo, tmp)
        try:
            rc = cli(["verdict", "t9003", "kept", "--why", "no branch to measure"])
            assert rc in (0, None), f"the verdict command returned {rc!r}"
            rows = [r for r in rows_written() if r.get("task") == "t9003"]
            assert rows, f"no ledger row was written for t9003: {rows_written()!r}"
            row = rows[-1]
            assert "files_two_dot" not in row and "files_base" not in row, (
                "a run with no branch has no diff to measure: both keys must be ABSENT, "
                f"not null and not zero, got {row!r}")
        finally:
            restore(old)


TESTS = [
    test_diff_noise_counts_the_files_the_two_dot_form_adds,
    test_diff_noise_is_computed_per_branch_not_a_constant,
    test_diff_noise_never_raises_for_a_branch_that_is_not_there,
    test_queue_print_anchors_the_diff_on_a_computed_merge_base_sha,
    test_queue_print_states_the_noise_on_the_bridge_row,
    test_queue_print_states_the_noise_on_the_run_row,
    test_queue_print_states_no_noise_for_a_branch_cut_from_main,
    test_queue_print_adds_no_fragment_to_a_run_with_no_branch,
    test_verdict_ledger_row_records_both_counts,
    test_verdict_ledger_row_has_no_counts_when_the_run_has_no_branch,
]

if __name__ == "__main__":
    failed = 0
    for t in TESTS:
        try:
            t()
            print(f"  ok   {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"  FAIL {t.__name__}: {e}")
        except Exception as e:
            failed += 1
            print(f"  CRASH {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n  {len(TESTS) - failed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
