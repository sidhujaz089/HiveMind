#!/usr/bin/env python3
"""Merge a branch the moment its frontier review passes -- built in a clean worktree, never in the live tree.

    python3 scripts/merge_reviewed.py <branch> --why "what the review checked" [--oracle scripts/tests/test_x.py]
    python3 scripts/merge_reviewed.py <branch> --why "..." --dry-run      # build and test, move nothing

WHY (Jas, 2026-10-29): "Retire Ready to Merge. Merge on pass." A branch a frontier reviewer passed used to sit in
"Ready to merge" for Jas to merge by hand; now the review that passes it merges it. This is that step, in one place:

  1. In a scratch worktree under ~/.cache/hivemind-tmp (never /tmp, which is RAM), detach at main.
  2. With --oracle: the suite must FAIL on main and PASS with the branch merged. Either way round refuses.
  3. `git merge --no-ff` with the reviewer's signature (Reviewed-by + Agent in the LAST paragraph, the line
     ending at the scope -- role_gate's REVIEW_RE fails silently on anything after it). Runtime ledgers
     (workspace/**/*.jsonl) are restored to main's copy: rows are written by services, not carried on branches.
  4. Under merge_lock: refuse if any path the merge changes is dirty in the live checkout, then move main by
     `git update-ref <new> <old>` and sync the live index and files for exactly those paths.
  5. Record a `kept` verdict for the branch (reviewq) and delete the branch.

WHY NOT `git merge` IN THE LIVE CHECKOUT (t9562, measured 2026-10-29): with ~1,250 dirty paths git autostashes the
tree, the merge then refuses on an untracked path, and re-applying the stash took ~40 minutes and left "Index was
not unstashed". Building the merge elsewhere and moving the ref never touches the dirty tree.

A guarded file (role_gate's six) still needs a signature from a model that did not write it: the reference-
transaction hook judges the merge commit's message, so pass --reviewer when the reviewer is another model.
"""
import argparse
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(os.environ.get("MERGE_REVIEWED_REPO") or Path(__file__).resolve().parent.parent)
SCRATCH = Path(os.environ.get("MERGE_REVIEWED_SCRATCH") or Path.home() / ".cache/hivemind-tmp/merge-reviewed")


def git(*args, cwd=None, timeout=600):
    r = subprocess.run(["git", *args], cwd=str(cwd or REPO), capture_output=True, text=True, timeout=timeout)
    return r.returncode, r.stdout.strip(), r.stderr.strip()


def scratch():
    """The clean worktree, detached at main, with nothing uncommitted."""
    if not (SCRATCH / ".git").exists():
        SCRATCH.parent.mkdir(parents=True, exist_ok=True)
        rc, out, err = git("worktree", "add", "-q", "--detach", str(SCRATCH), "main")
        if rc:
            raise SystemExit(f"merge_reviewed: could not create {SCRATCH}: {err}")
    git("merge", "--abort", cwd=SCRATCH)
    git("reset", "-q", "--hard", cwd=SCRATCH)
    git("checkout", "-q", "--detach", "main", cwd=SCRATCH)
    return SCRATCH


def suite_rc(wt, suite, timeout=900):
    try:
        # A suite that itself uses merge_reviewed must not build in the scratch this run is using (DS, 17:36Z:
        # an oracle run left a test's commit as SCRATCH's HEAD), so it gets a scratch of its own.
        with tempfile.TemporaryDirectory(dir=str(SCRATCH.parent)) as own:
            env = {**os.environ, "MERGE_REVIEWED_SCRATCH": str(Path(own) / "scratch")}
            return subprocess.run([sys.executable, suite], cwd=str(wt), capture_output=True, text=True,
                                  timeout=timeout, env=env).returncode
    except subprocess.TimeoutExpired:
        return "timeout"


def message(branch, why, reviewer, scope, agent):
    return (f"chore(workspace): merge {branch} -- passed frontier review\n\n{why}\n\n"
            f"Reviewed-by: {reviewer} ({scope})\nAgent: {agent}\n")


def _lock_held():
    """True when THIS process holds merge_lock (merge_lock.holder() names our pid). Test fakes may stand in."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import merge_lock
    h = merge_lock.holder() or {}
    return int(h.get("pid") or 0) == os.getpid()


def merge_library(O, T, B):
    """Merge the branch's library entries T into main's O (in place), against their merge base B. Returns clashes.

    THREE-WAY, PER ENTRY (DS, lanes 603920): "theirs-added keys only" silently dropped an entry the branch EDITED --
    reproduced: base {a: original}, ours {a: original}, theirs {a: edited} gave a = original. Whichever side changed
    an entry wins; both changed and disagree -> it is returned as a clash and the caller refuses, never picks."""
    clash = []
    for k in sorted(set(O) | set(T)):
        o, t, b = O.get(k), T.get(k), B.get(k)
        if t == b or t == o:
            continue                                        # the branch did not change it, or both sides agree
        if o == b:
            if t is None:
                O.pop(k, None)                              # only the branch removed it
            else:
                O[k] = t                                    # only the branch changed or added it
        else:
            clash.append(k)
    return clash


GENERATED = {"scripts/library.json", "workspace/SCRIPTS.md"}


def resolve_generated(wt, branch):
    """True when every conflict is in the script library's two GENERATED files, and they are now resolved.

    Two branches that each register a script both append to library.json and both regenerate SCRIPTS.md, so
    nearly every merge conflicted there and nowhere else (2026-10-29: three of DS's branches and setsid-guard).
    library.json is merged as DATA -- main's entries, plus every entry the branch added that main lacks -- and
    SCRIPTS.md is regenerated from the result. A conflict anywhere else is real and refuses.
    """
    import json
    _rc, un, _ = git("diff", "--name-only", "--diff-filter=U", cwd=wt)
    conflicted = set(filter(None, un.splitlines()))
    if not conflicted or not conflicted <= GENERATED:
        return False
    if "scripts/library.json" in conflicted:
        def at(rev):
            return json.loads(git("show", f"{rev}:scripts/library.json", cwd=wt)[1])
        _rc, base_rev, _ = git("merge-base", "HEAD", branch, cwd=wt)
        ours, theirs, base = at("HEAD"), at(branch), at(base_rev)
        clash = merge_library(ours["scripts"], theirs["scripts"], base["scripts"])
        if clash:
            print(f"merge_reviewed: library.json entries changed on BOTH sides: {', '.join(clash)}", file=sys.stderr)
            return False
        (wt / "scripts/library.json").write_text(json.dumps(ours, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    r = subprocess.run([sys.executable, "-c", "import sys; sys.path.insert(0, 'scripts'); import library as L; "
                        "L.write_md(L.load())"], cwd=str(wt), capture_output=True, text=True, timeout=300)
    if r.returncode:
        return False
    git("add", "scripts/library.json", "workspace/SCRIPTS.md", cwd=wt)
    return True


def stale_services(repo=None):
    """[(service, fix)] the running services this repository's code has left behind. [] when it cannot be asked.

    A MERGE MAKES SERVICES STALE AND NOTHING SAID SO (2026-10-30). A merge that touched merge_reviewed.py left
    swift-proxy serving the old code; the next session end then spent sixteen minutes failing on "No running
    service is older than the code it runs" -- and, because every new commit makes services stale again, the fix
    looked like a permanent fault rather than the consequence of one merge. The merge is the moment it becomes
    true, so the merge is the moment to say it.

    An empty list means "nothing was reported", NOT "nothing is stale": if the check cannot be run or read it says
    so on stderr rather than answering for it.
    """
    repo = Path(repo or REPO)
    script = repo / "scripts" / "check_stale_services.py"
    if not script.is_file():
        return []
    try:
        r = subprocess.run([sys.executable, str(script)], cwd=str(repo), capture_output=True, text=True,
                           timeout=300)
    except (OSError, subprocess.SubprocessError) as e:               # noqa: BLE001 -- said, not swallowed
        print(f"  NOTE: could not ask which services this merge left stale ({type(e).__name__}: {e})",
              file=sys.stderr)
        return []
    if r.returncode != 0:
        # A PROBE THAT CANNOT ANSWER MUST SAY SO. An empty list from a failed probe reads exactly like a clean bill
        # of health, and the report is the whole point of this function.
        print(f"  NOTE: could not ask which services this merge left stale (check_stale_services exited "
              f"{r.returncode}) -- NOT a report that nothing is stale", file=sys.stderr)
    out = []
    for line in (r.stdout or "").splitlines():
        s = line.strip()
        if s.startswith("[STALE]"):
            out.append([s.split("]", 1)[1].strip(), ""])
        elif out and s.startswith("fix:"):
            out[-1][1] = s.split("fix:", 1)[1].strip()
    return [tuple(x) for x in out]


def related_gate(wt, paths, timeout=None):
    """Run the related-tests gate for `paths` inside the scratch worktree. (rc, output).

    Every merge now pays what a commit pays. Before 2026-10-30 `build()` took a branch onto main having run
    nothing, so a merge was the ONE route to main that skipped pre-commit's related_tests -- and on a box where a
    direct commit pays 900 s, that made merging the cheap path. Cheap was never the point: the gate is what stops
    a break reaching main, and a merge commit is a commit.

    It runs in the SCRATCH worktree at the merged content, which is the thing being landed, and it is cheap on a
    repeat because related_tests consults the verdict cache keyed on that exact tree state.
    """
    wt = Path(wt)
    script = wt / "scripts" / "related_tests.py"
    if not script.is_file():
        return 1, (f"merge_reviewed: no scripts/related_tests.py in {wt} -- the gate cannot be paid, "
                   "so this merge is refused")
    # 1800 IS INTERIM, AND IT SAYS SO (Jas, 2026-10-31). The 900 here mirrored the pre-commit hook's 900, which
    # arrived in 1007505b9 with no derivation and was SMALLER than the work it bounds (related_tests.py:30 caps each
    # suite at 300 s). A merge that times out at 900 is indistinguishable from one whose suites are red, and both
    # refuse the merge. The number that replaces this comes from workspace/close-out-timings.jsonl, not from here.
    # THE CAP IS A SETTING NOW (Jas, 2026-10-31: "I want that 1800 in the settings section where I can adjust it plus a
    # recording of run actual times and a number of runs that timed out"). It lives in workspace/gate-settings.json so
    # tuning it is a save rather than a commit to a guarded file, and every run records its own duration so the number
    # stops being argued about and starts being measured. The env var still wins, for a one-off.
    try:
        import gate_settings as _gset
        _cap = _gset.timeout_s()
    except Exception as _e:                                        # noqa: BLE001 -- said, not swallowed
        _cap = 1800.0
        print(f"  NOTE: the gate timeout setting is unreadable ({type(_e).__name__}: {_e}); using {_cap:g} s")
    timeout = timeout or float(os.environ.get("MERGE_RELATED_TIMEOUT") or _cap)
    # GIT'S ENVIRONMENT IS NOT THE SUITES': inside a merge, git exports GIT_INDEX_FILE and friends, and a suite
    # that runs git in a throwaway repo inherits them and fails. related_tests does the same scrub for the hook.
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    # RECORD EVERY RUN, INCLUDING THE ONES THAT STOP SHORT (Jas, 2026-10-31). "How often does it hit the cap" is the
    # question that decides whether the cap is right, and it can only be answered if a timeout is a recorded run with
    # a duration rather than an exception that leaves no trace.
    _t0 = time.time()
    try:
        r = subprocess.run([sys.executable, str(script), *paths], cwd=str(wt), capture_output=True,
                           text=True, timeout=timeout, env=env, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        _record_gate(_t0, 124, "merge")
        return 1, f"merge_reviewed: the related tests TIMED OUT after {timeout:g}s -- nothing lands on a timeout"
    _record_gate(_t0, r.returncode, "merge")
    return r.returncode, (r.stdout + r.stderr)[-4000:]


def _record_gate(t0, rc, where):
    """Best effort: a merge must never fail because its telemetry could not be written."""
    try:
        import gate_settings as _gset
        _gset.record_run(time.time() - t0, rc, where)
    except Exception as e:                                          # noqa: BLE001 -- said, not swallowed
        print(f"  NOTE: gate run not recorded ({type(e).__name__}: {e})")


def build(branch, why, oracle, reviewer, agent, msg_text=None):
    """(commit, changed_paths) for the signed merge, built in the scratch worktree. Raises SystemExit on refusal."""
    # THE SCRATCH IS SHARED, SO THE LOCK IS REQUIRED, NOT REMEMBERED (DS, lanes 603920): both callers build under
    # merge_lock today; a future caller that forgets would race them in one worktree. Refuse instead.
    if not _lock_held():
        raise SystemExit("merge_reviewed: build() needs merge_lock held -- take merge_lock.hold() first")
    wt = scratch()
    rc, ahead, _ = git("rev-list", "--count", f"main..{branch}")
    if rc or ahead == "0":
        raise SystemExit(f"merge_reviewed: {branch} has nothing that is not already in main")
    if oracle:
        before = suite_rc(wt, oracle)
        if before == 0:
            raise SystemExit(f"merge_reviewed: {oracle} already PASSES on main -- it cannot show this branch is needed")
    rc, out, err = git("merge", "--no-ff", "--no-commit", branch, cwd=wt)
    if rc and not resolve_generated(wt, branch):
        git("merge", "--abort", cwd=wt)
        raise SystemExit(f"merge_reviewed: {branch} does not merge cleanly onto main -- rebuild it\n{out}\n{err}")
    if oracle:
        after = suite_rc(wt, oracle)
        if after != 0:
            git("merge", "--abort", cwd=wt)
            raise SystemExit(f"merge_reviewed: {oracle} still FAILS with {branch} merged (rc={after})")
    _rc, ledgers, _ = git("diff", "--cached", "--name-only", "main", "--", "workspace/*.jsonl", "workspace/**/*.jsonl",
                          cwd=wt)
    for p in filter(None, ledgers.splitlines()):
        git("checkout", "main", "--", p, cwd=wt)
    # THE MERGE PAYS THE COMMIT'S GATE (2026-10-30). Paths are taken from the staged merge rather than from the
    # commit, because this has to refuse BEFORE a commit exists: a red merge must leave main and the scratch
    # untouched, not produce a commit nobody may land.
    _rc, merged, _ = git("diff", "--cached", "--name-only", "main", cwd=wt)
    gate_paths = [p for p in merged.splitlines() if p.endswith(".py")]
    if gate_paths:
        rc, gate_out = related_gate(wt, gate_paths)
        if rc:
            git("merge", "--abort", cwd=wt)
            raise SystemExit("merge_reviewed: the related tests are RED with this branch merged, so nothing was "
                             "built or moved:\n" + gate_out)
    # ONE MESSAGE FILE PER RUN (DS, lanes 17:36Z): a single shared merge-reviewed-msg.txt was overwritten by
    # another caller between the write and the commit, and DS's merge went out with a test's message and no
    # Reviewed-by. The file is private to this run, and the commit is read back before anything trusts it.
    text = msg_text or message(branch, why, reviewer, "oracle" if oracle else "diff", agent)
    fd, msg = tempfile.mkstemp(prefix="merge-reviewed-msg-", suffix=".txt", dir=str(SCRATCH.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        rc, out, err = git("commit", "-q", "-F", msg, cwd=wt)
    finally:
        os.unlink(msg)
    if rc:
        git("merge", "--abort", cwd=wt)
        raise SystemExit(f"merge_reviewed: the commit was refused\n{out}\n{err}")
    _rc, body, _ = git("log", "-1", "--format=%B", cwd=wt)
    if body.strip() != text.strip():
        git("reset", "-q", "--hard", "main", cwd=wt)
        raise SystemExit("merge_reviewed: the merge commit does not carry this run's message -- refusing it")
    git("checkout", "-q", "--", ".", cwd=wt)              # hooks append to ledgers; those rows are not this merge's
    _rc, new, _ = git("rev-parse", "HEAD", cwd=wt)
    _rc, paths, _ = git("diff", "--name-only", "main", new)
    return new, [p for p in paths.splitlines() if p]


def move_main(new, paths, lane="L1", locked=False):
    """Fast-forward refs/heads/main to `new` under the merge lock and sync the live checkout for `paths`.

    `locked=True` when the caller already holds merge_lock (auto_merge does): flock is per open file, so taking
    it a second time in the same process would wait on itself for ever."""
    if locked:
        return _move(new, paths)
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import merge_lock
    with merge_lock.hold(lane, f"merge_reviewed {new[:9]}"):
        return _move(new, paths)


def _move(new, paths):
    _rc, old, _ = git("rev-parse", "main")
    if git("merge-base", "--is-ancestor", old, new)[0]:
        raise SystemExit("merge_reviewed: main moved while this was built -- run it again")
    # A live path that differs from main only by ALREADY HOLDING the new content (a Models-tab edit this merge
    # commits, say) is not in the way; any other uncommitted edit is, and refuses.
    # NOT THROUGH git(): IT STRIPS (2026-10-30). `git()` returns r.stdout.strip(), so the FIRST line of a
    # porcelain listing loses its leading space -- " M path" becomes "M path" -- and ln[3:] then ate the first
    # letter of the path. REPO/"rojects/..." does not exist, the "already holding the new content" exception
    # below could never apply, and every unstaged path that held exactly the merged bytes refused the merge.
    # It cost an hour this morning on ROLES.json, whose three hunks were the branch's own content.
    raw = subprocess.run(["git", "status", "--porcelain", "--", *paths], cwd=str(REPO),
                         capture_output=True, text=True, timeout=600)
    dirty = []
    for ln in (raw.stdout or "").splitlines():
        if not ln.strip():
            continue
        path = ln[3:]
        if (REPO / path).exists() and git("diff", "--quiet", new, "--", path)[0] == 0:
            continue                                   # already holds the merge's content: not in the way
        dirty.append(ln)
    if dirty:
        raise SystemExit("merge_reviewed: these paths have live, uncommitted edits -- not moving main:\n"
                         + "\n".join(dirty))
    # SYNC THE LIVE TREE FIRST, AND READ THE RESULT (2026-10-30). Both steps below used to run AFTER main had
    # moved and neither exit code was read. On 2026-10-30 a pre-commit hook held .git/index.lock for eight
    # minutes, `git checkout -- <paths>` could not take the index, and this returned SUCCESS with main at the
    # merge and the live files still on the OLD content -- so every reader of git said the merge had landed while
    # the box kept running the old code. The lock exists during every commit in this repository, which makes that
    # repeatable rather than rare. A checkout of `new` does not need main to have moved, so a lock now refuses
    # with the ref untouched instead of desyncing it silently.
    # A MERGE THAT DELETES A FILE COULD NOT LAND AT ALL UNTIL NOW (2026-10-30). `git checkout <new> -- <paths>` cannot
    # sync a path that does not exist in `new`: it fails with "pathspec ... did not match", so any branch that removes a
    # file was refused at the last step. The rename of two retired units hit exactly this. A deleted path is not synced,
    # it is REMOVED from the live tree, and the two cases are separated here rather than hoped about.
    alive = [q for q in paths if git("cat-file", "-e", f"{new}:{q}")[0] == 0]
    gone = [q for q in paths if q not in alive]
    if alive:
        rc, out, err = git("checkout", new, "--", *alive)
    else:
        rc, out, err = 0, "", ""
    if rc:
        raise SystemExit("merge_reviewed: the live checkout could not be synced with the merge, so main was NOT "
                         "moved. Paths:\n  " + "\n  ".join(alive) + f"\n{out}\n{err}")
    for q in gone:
        target = REPO / q
        try:
            if target.exists():
                target.unlink()
                print(f"  removed from the live tree (the merge deletes it): {q}")
        except OSError as e:                                        # noqa: BLE001 -- said, not swallowed
            print(f"  NOTE: could not remove {q} from the live tree ({type(e).__name__}: {e})")
    rc, out, err = git("update-ref", "-m", f"merge_reviewed: main to {new}", "refs/heads/main", new, old)
    if rc:
        git("checkout", "HEAD", "--", *paths)      # the ref did not move, so put the live tree back where it was
        raise SystemExit(f"merge_reviewed: main was not moved\n{out}\n{err}")
    # THE MERGE SAID WHAT IT COST, NOT THE NEXT SESSION (2026-10-30). A service still running the code this merge
    # replaced is a fact created here; reporting it now turns a sixteen-minute mystery into an instruction.
    for service, fix in stale_services():
        print(f"  STALE SERVICE: {service} is still running the code this merge replaced -- "
              f"{fix or 'restart it'}")
    # A LANDED FIX CLOSES ITS TASK (2026-10-31, Jas: "did you fix why merged fix doesn't close the task?"). Nothing did:
    # 16 merged harness tasks with green oracles sat at "doing" for up to three weeks. Detached, because it runs the
    # tasks' own suites; this path is shared with auto_merge, so both merge routes close what they land.
    try:
        log = (REPO / "workspace" / "run-logs" / "close-landed.log").open("a")
        subprocess.Popen([sys.executable, str(REPO / "scripts" / "close_landed.py")], cwd=str(REPO),
                         stdout=log, stderr=log, stdin=subprocess.DEVNULL, start_new_session=True)
    except OSError as e:
        print(f"  NOTE: close_landed not started ({e}); run python3 scripts/close_landed.py", file=sys.stderr)
    return old


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("branch")
    ap.add_argument("--why", required=True, help="what the review checked; goes in the commit and the verdict")
    ap.add_argument("--oracle", help="a suite that must fail on main and pass merged")
    ap.add_argument("--reviewer", default="claude-code/opus-5.5")
    ap.add_argument("--agent", default="claude-code/opus-5.5")
    ap.add_argument("--lane", default="L1")
    ap.add_argument("--dry-run", action="store_true", help="build and test the merge, move nothing")
    a = ap.parse_args(argv)
    # THE LOCK COVERS THE BUILD TOO: the scratch worktree is shared with auto_merge, which builds under the lock.
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import merge_lock
    with merge_lock.hold(a.lane, f"merge_reviewed {a.branch}"):
        new, paths = build(a.branch, a.why, a.oracle, a.reviewer, a.agent)
        print(f"  built {new[:9]}: {len(paths)} path(s) -- {', '.join(paths[:6])}{' ...' if len(paths) > 6 else ''}")
        if a.dry_run:
            print("  dry run -- main not moved")
            return 0
        old = move_main(new, paths, locked=True)
    print(f"  main {old[:9]} -> {new[:9]}")
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import reviewq
    reviewq.main(["verdict", "--branch", a.branch, "kept", "--why", f"merged {new[:9]} on passing review: {a.why}"])
    rc, out, err = git("branch", "-d", a.branch)
    if rc:
        print(f"  NOTE: {a.branch} not deleted: {err}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
