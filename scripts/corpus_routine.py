#!/usr/bin/env python3
"""Index the session transcripts, and back them up when the NAS is there.

WHY THIS RUNS ON A TIMER (2026-08-26)
-------------------------------------
Jas watched a session compact mid-task and asked whether the conversation had reached the
corpus first. The raw transcripts were never at risk -- Claude Code appends every turn to
`~/.claude/projects/**/*.jsonl` as it happens, and a compaction does not touch them. What
WAS stale is the searchable index: it had last been built at 18:56 and the session had run
for three hours past that, so `transcripts.py search` could not see the very stretch he
was asking about.

An index you rebuild by hand is an index that is stale exactly when you need it, because
you only think to rebuild it once you have already gone looking.

The backup step is deliberately conditional. The NAS is an AFP mount over gvfs and it
drops; a routine that fails the whole run because the share is absent would train us to
ignore its failures. Missing mount is a SKIP and says so.
"""
import datetime
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
# THE PATH THE NAS IS ACTUALLY AT, which is not where this pointed until 2026-09-16.
# The share moved from a gvfs/AFP mount to a plain NFS mount at /mnt/nas. scripts/nas_backup.sh
# was updated and this was not, so `NAS.is_dir()` was False on every run and session_end
# printed "[skip] backup -- the NAS is not mounted. Nothing was copied." That reads like a
# machine without a NAS attached. The NAS was mounted, reachable and writable the whole time:
# 192.168.1.81:/nfs on /mnt/nas, 334 GB free, with nightly bundles landing in it from the
# OTHER script. So the session-end backup was a silent no-op, and the message explaining it
# away was the reason nobody looked. Keep this equal to DEST in scripts/nas_backup.sh.
NAS = Path("/mnt/nas/Public/HiveMind")


def has_manifest(d):
    """A bundle carries its manifest INSIDE the inner directory; older ones are flat.

    Checking only the top level reported "no MANIFEST" for a bundle that had one, which is
    how a VERIFIED 478-file backup was chosen as the destination for a fresh copy and
    truncated to 177 when the mount dropped.
    """
    return (d / "MANIFEST.sha256").is_file() or \
           (d / "ai-workspace-corpora" / "MANIFEST.sha256").is_file()


def choose_dest(nas, day):
    """(destination, reason). Never returns a path that would overwrite existing data.

    Three outcomes:
      * today's bundle exists WITH a manifest -> None, already done
      * it exists WITHOUT one -> a partial; take the next free suffix, leave it alone
      * it does not exist -> use it
    """
    dest = nas / f"ai-workspace-corpora-{day}"
    # "Already done today" must consider the SUFFIXED bundles too. Checking only the base
    # name meant that once a partial occupied it, every later run made yet another copy --
    # -2, then -3 -- because the good bundle it had already written was invisible to the
    # question "is today done?".
    done = [d for d in nas.glob(f"ai-workspace-corpora-{day}*") if has_manifest(d)]
    if done:
        return None, f"{done[-1].name} already exists and carries a MANIFEST."
    if not dest.exists():
        return dest, ""
    stem, n = dest, 2
    while dest.exists():
        dest = stem.with_name(f"{stem.name}-{n}")
        n += 1
    return dest, (f"{stem.name} is a partial from an interrupted copy — leaving it "
                  f"untouched and writing {dest.name} instead.")


def run(label, argv):
    r = subprocess.run(argv, cwd=str(REPO), capture_output=True, text=True, timeout=3600)
    tail = (r.stdout + r.stderr).strip().splitlines()
    print(f"[{'ok' if r.returncode == 0 else 'FAIL'}] {label}")
    for line in tail[-3:]:
        print(f"    {line}")
    return r.returncode


def main():
    bad = run("index transcripts", [sys.executable, "scripts/transcripts.py", "index"])

    if not NAS.is_dir():
        print("[skip] backup — the NAS is not mounted. Nothing was copied.")
        return bad

    # One dated bundle per day, re-run in place. backup_corpora.py needs an explicit --to;
    # the date comes from the clock here rather than from inside a workflow script.
    dest = NAS / f"ai-workspace-corpora-{datetime.date.today():%Y-%m-%d}"

    # The index is seconds and runs hourly. The NAS copy is 478 files over AFP and takes
    # minutes -- running THAT hourly is what forced a delete-and-recopy of a bundle that
    # already existed. One bundle a day, and nothing on the NAS is ever removed here.
    dest, why = choose_dest(NAS, f"{datetime.date.today():%Y-%m-%d}")
    if dest is None:
        print(f"[skip] backup — {why}")
        return bad
    if why:
        print(f"[note] {why}")

    # backup_corpora.py --to refuses a destination that does not exist, and that refusal is
    # right: it stops a typo'd path from quietly creating a junk bundle somewhere. So the
    # directory is created HERE, deliberately, by the thing that chose the name.
    try:
        dest.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        print(f"[FAIL] could not create {dest.name} on the NAS: {e}")
        return bad

    rc = run(f"back up the corpus -> {dest.name}",
             [sys.executable, "scripts/backup_corpora.py", "--to", str(dest)])

    # The AFP share drops mid-copy. It has done it twice, and the symptom is a
    # FileNotFoundError on a path the copy had just written -- the mount vanishing under an
    # open handle, not a missing source file. What it leaves behind is a PARTIAL bundle with
    # a plausible name and no MANIFEST, which is the dangerous artefact: it looks like a
    # backup to anyone reading the directory listing.
    if rc and not NAS.is_dir():
        print("    the NAS went away DURING the copy -- this is the mount dropping, "
              "not a corrupt corpus.")
        print("    the partial bundle is left for the next run to overwrite; it has no "
              "MANIFEST, so --verify will refuse to call it a backup.")
        return bad        # an unreliable share excuses the backup, not a failed index
    if rc:
        print("    the share is still mounted, so this is a REAL failure, not a drop.")
    return rc


if __name__ == "__main__":
    sys.exit(main())
