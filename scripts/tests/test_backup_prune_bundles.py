#!/usr/bin/env python3
"""t690 Option A: the 3-version rule for the git bundles inside the live folder.

scripts/nas_backup.sh piles one ai-workspace-<date>.bundle into
$DEST/ai-workspace-corpora every night (cp at nas_backup.sh:50) and rebuilds
MANIFEST.sha256 over ALL of them (nas_backup.sh:58-69).  do_prune() never looks inside that
folder (LIVE_NAME is excluded from its candidates), so the bundles grow unbounded.  This suite
pins the new prune_bundles(): keep the newest `keep` *.bundle files that pass
`git bundle verify`, delete every other bundle (a corrupt bundle fails verify, so it is not
kept and is removed with the rest -- the same semantics as do_prune), rewrite MANIFEST.sha256
to list exactly the survivors plus every non-bundle line, refuse (return 1, delete nothing) when
fewer than `keep` verify, and delete nothing when apply=False.  Plus the --prune-bundles CLI.

Red on main because: prune_bundles does not exist in scripts/backup_corpora.py and the
--prune-bundles flag is not in its argparse.

Fakes only: tempdir repo + real git bundles; git is a local binary.  No live state.
"""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

import backup_corpora as B

STAMP = "2026-10"


def _git(repo, *args):
    r = subprocess.run(["git", "-C", str(repo), *args],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, f"git {args} failed: {r.stderr}"
    return r


def make_repo_and_bundles(base, n=7):
    """A git repo plus n live-folder bundles named ai-workspace-<date>.bundle,
    oldest to newest by name (the ordering prune_bundles relies on)."""
    repo = base / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / "note.txt").write_text("v1\n")
    _git(repo, "add", "note.txt")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "c1")
    live = base / "live"
    live.mkdir()
    names = []
    for i in range(1, n + 1):
        name = f"ai-workspace-{STAMP}-{i:02d}.bundle"
        subprocess.run(["git", "-C", str(repo), "bundle", "create", str(live / name), "--all"],
                       check=True, capture_output=True, timeout=60)
        names.append(live / name)
    (live / "corpusfile").write_text("payload\n")
    (live / "MANIFEST.sha256").write_text(
        "deadbeef  corpusfile\n" + "".join(
            f"aa{i:06d}  {p.name}\n" for i, p in enumerate(names)))
    return repo, live, names


def test_report_only_deletes_nothing():
    base = Path(tempfile.mkdtemp(prefix="t690b_"))
    try:
        _, live, names = make_repo_and_bundles(base)
        rc = B.prune_bundles(live, keep=3, apply=False)
        assert rc == 0
        assert sorted(p.name for p in live.glob("*.bundle")) == sorted(p.name for p in names), \
            "report-only must delete nothing"
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_apply_keeps_newest_three_and_rewrites_manifest():
    base = Path(tempfile.mkdtemp(prefix="t690b_"))
    try:
        _, live, names = make_repo_and_bundles(base)          # 7 bundles, all verify
        rc = B.prune_bundles(live, keep=3, apply=True)
        assert rc == 0
        kept = [p.name for p in sorted(live.glob("*.bundle"), key=lambda p: p.name)]
        assert kept == [p.name for p in names[-3:]], f"kept={kept}"
        m = (live / "MANIFEST.sha256").read_text().splitlines()
        named = [l.rsplit("  ", 1)[1] for l in m if l.strip()]
        assert sorted(named) == sorted(set(kept) | {"corpusfile"}), \
            f"manifest must list corpusfile + exactly the kept bundles, got {named}"
        for old in names[:-3]:
            assert old.name not in named, f"pruned {old.name} still in manifest"
        assert any(l.rstrip().endswith("  corpusfile") for l in m), \
            "a non-bundle manifest line must survive the rewrite"
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_corrupt_newest_bundle_is_dropped_not_kept():
    base = Path(tempfile.mkdtemp(prefix="t690b_"))
    try:
        _, live, names = make_repo_and_bundles(base)
        names[-1].write_text("not a git bundle")            # newest bundle corrupt
        rc = B.prune_bundles(live, keep=3, apply=True)
        assert rc == 0
        kept = {p.name for p in live.glob("*.bundle")}
        assert names[-1].name not in kept, "a corrupt bundle must not be kept"
        assert kept == {p.name for p in names[-4:-1]}, \
            f"expected the 3 newest VERIFYING bundles {names[-4:-1]}, got {kept}"
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_fewer_than_keep_verify_refuses_and_deletes_nothing():
    base = Path(tempfile.mkdtemp(prefix="t690b_"))
    try:
        _, live, names = make_repo_and_bundles(base, n=2)     # only 2 bundles exist
        rc = B.prune_bundles(live, keep=3, apply=True)
        assert rc == 1, "fewer than keep verify must refuse"
        assert sorted(p.name for p in live.glob("*.bundle")) == sorted(p.name for p in names), \
            "a refused prune must delete nothing"
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_cli_apply_and_refusal():
    script = REPO / "scripts" / "backup_corpora.py"
    base = Path(tempfile.mkdtemp(prefix="t690b_"))
    try:
        _, live, names = make_repo_and_bundles(base)
        r = subprocess.run([sys.executable, str(script), "--prune-bundles",
                            str(live), "--apply", "--keep", "3"],
                           capture_output=True, text=True, timeout=120)
        assert r.returncode == 0, f"CLI apply failed: {r.stdout} {r.stderr}"
        assert {p.name for p in live.glob("*.bundle")} == {p.name for p in names[-3:]}

        base2 = Path(tempfile.mkdtemp(prefix="t690b2_"))
        try:
            _, live2, _ = make_repo_and_bundles(base2, n=2)
            r2 = subprocess.run([sys.executable, str(script), "--prune-bundles",
                                 str(live2), "--apply", "--keep", "3"],
                                capture_output=True, text=True, timeout=120)
            assert r2.returncode == 1, f"CLI refusal must exit non-zero: {r2.stdout} {r2.stderr}"
        finally:
            shutil.rmtree(base2, ignore_errors=True)
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_verify_does_not_depend_on_the_callers_cwd():
    """A bundle is verified against THIS REPO, not against wherever the caller happens to stand.

    2026-10-27, found from the build blocker: coder.py runs every suite with cwd = the sandbox root,
    and the sandbox has no `.git` (coder.EXCLUDE), so `git bundle verify` -- which was run with no
    `-C` and no `cwd=` -- failed for EVERY bundle: "REFUSED: only 0 of the 7 bundle(s) verify". The
    same suite passed 11/11 wherever a .git existed. A caller can legitimately run the prune from
    anywhere (`python3 ~/ai-workspace/scripts/backup_corpora.py --prune-bundles ...` from a shell
    whose cwd is not the repo), and it must not read that as "every bundle is corrupt".
    """
    import os
    base = Path(tempfile.mkdtemp(prefix="t690c_"))
    before = os.getcwd()
    elsewhere = base / "not-a-repository"
    elsewhere.mkdir()
    try:
        _, live, names = make_repo_and_bundles(base)
        os.chdir(elsewhere)
        rc = B.prune_bundles(live, keep=3, apply=True)
        assert rc == 0, "the prune must verify against the repo, not the caller's cwd"
        assert {p.name for p in live.glob("*.bundle")} == {p.name for p in names[-3:]}, \
            "the three newest bundles must be kept from a non-repo cwd too"
    finally:
        os.chdir(before)
        shutil.rmtree(base, ignore_errors=True)


if __name__ == "__main__":
    failures = 0
    for fn in (test_report_only_deletes_nothing, test_apply_keeps_newest_three_and_rewrites_manifest,
               test_corrupt_newest_bundle_is_dropped_not_kept,
               test_fewer_than_keep_verify_refuses_and_deletes_nothing, test_cli_apply_and_refusal,
               test_verify_does_not_depend_on_the_callers_cwd):
        try:
            fn()
            print(f"ok   {fn.__name__}")
        except Exception as e:  # noqa: BLE001 -- report, do not swallow silently
            failures += 1
            print(f"FAIL {fn.__name__}: {e}")
    if failures:
        print(f"{failures} test(s) failed")
        sys.exit(1)
    print("all tests passed")
    sys.exit(0)
