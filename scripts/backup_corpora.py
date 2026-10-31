#!/usr/bin/env python3
"""Backs up the two corpora that git does not carry, with per-file checksums.

WHAT THIS IS FOR

`D-22` keeps `chat/` out of git and `D-15` keeps the ISO PDFs out. Both decisions
rested on Google Drive being a second copy. Drive is being retired once the MCP
server is up, and at that moment each corpus becomes **one copy on one machine**.

About 162 MB, which fits on any USB stick.

WHY A DIRECTORY COPY AND NOT AN ARCHIVE

A tarball is one object: corrupt a few bytes in the middle and the tail may be
unrecoverable. A directory of files with a checksum manifest degrades one file at a
time, and `--verify` names exactly which. Restoring is `cp`, with nothing to unpack.

WHY OFFLINE MATTERS MORE THAN THE SCRIPT

Jas is granting an agent `docker`-group access, which is root-equivalent on this box.
A backup that stays plugged in and writable is not protection against that: whatever
can reach the filesystem can reach the backup. **The security property is the drive
being unplugged**, exactly as Google's activity record was trustworthy because it sat
outside this system (`docs/ACCESS.md`). This script only makes the copy verifiable;
you make it safe by pulling the drive out.

    python3 scripts/backup_corpora.py --to /media/user/USB
    python3 scripts/backup_corpora.py --verify /media/user/USB/ai-workspace-corpora
"""

import argparse
import hashlib
import time
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

# THE ROOT CAN BE A FIXTURE, SO THE SUITE TESTS THE CODE AND NOT THE MACHINE (2026-10-27).
# The four corpora below are gitignored and exist ONLY in the live checkout, so the suite's
# "backup of the real corpora succeeds" got exit 1 with "MISSING and not backed up: chat, ..."
# from any worktree -- red for every lane, because every lane tests in a worktree, and it was
# an undeclared red that blocked every commit touching this file. With this set, the suite
# points the backup at a handful of files it made itself. Read once at import; production
# never sets it.
_FIXTURE_ROOT = os.environ.get("HIVEMIND_BACKUP_ROOT")
REPO_ROOT = Path(_FIXTURE_ROOT) if _FIXTURE_ROOT else Path(__file__).resolve().parent.parent

# What git does not carry. Each is (path relative to the repo, why it is not in git).
CORPORA = [
    ("chat", "D-22 — personal Claude history; never committed"),
    ("projects/iso-assistant/ISO PDFs", "D-15 — 84 MB of standards; gitignored by choice"),
    # Added 2026-08-24 (t014). Photos and files sent from the phone. Gitignored deliberately
    # so that Delete in the UI really deletes -- removing a TRACKED file leaves it in history
    # forever, which would have made the delete button a lie. The cost of that choice is
    # exactly this: no history, no GitHub copy, so the USB is the only second copy.
    ("workspace/attachments", "gitignored so Delete means gone; the USB is its only backup"),
    # Added 2026-10-05 (t072, Jas: "Go ahead and back up the personal files"). His own documents for the
    # semantic search; personal, so never committed (D-33), and this copy is their only second one.
    ("library", "t072 -- Jas's personal files for search; gitignored (D-33)"),
    # Chat's captures land in chat/captures/ and are therefore ALREADY covered by the
    # "chat" entry above. Listing them separately would copy them twice and inflate the
    # verify count -- noted here because it is the obvious thing to add and it is wrong.
]

# Added 2026-08-26. These live OUTSIDE the repository, so the paths are absolute and the
# archive name is given explicitly rather than derived.
#
# Jas: *"I don't care about data retention as I care about your memory recollection."* Both
# of these are memory, and neither was in git, on GitHub, or in any backup:
#
#   ~/.claude/projects/   Claude Code's session transcripts -- every message, tool call and
#                         result, one JSON object per line. 31 MB across five sessions and
#                         the only record of how a decision was actually reached. Note the
#                         name is Claude Code's term for a working directory and has nothing
#                         to do with claude.ai Projects, which `D-27` retired.
#
#   ~/ledger-snapshots/   the manual undo log for `chat/`. Because `D-22` keeps the corpus
#                         out of git there is no rollback, so `t125` requires a snapshot
#                         before any edit. On 2026-08-26 that convention is what preserved
#                         two deliberately deleted files -- and my failure to look at it is
#                         `L-119`.
# A FIXTURE ROOT BACKS UP ONLY WHAT IS UNDER IT: these two are absolute paths outside any
# repository, so a suite that pointed REPO_ROOT at its own files would still copy ~/.claude and
# the ledger snapshots into the fixture -- slow, and it would be testing the machine again.
EXTERNAL = [] if _FIXTURE_ROOT else [
    (Path.home() / ".claude" / "projects", "claude-code-sessions",
     "session transcripts; local to this machine only, no cloud copy, not in git"),
    (Path.home() / "ledger-snapshots", "ledger-snapshots",
     "pre-edit snapshots of chat/, which D-22 keeps out of git so there is no other undo"),
]

# Above this, a "verification" was almost certainly served from the page cache rather than
# from the medium. Measured 2026-08-18: verifying 154 MB straight after writing it took
# 0.12s (~1.3 GB/s); after unmount and remount, the same check took 9.35s (~17 MB/s), which
# is the real device. The first run proved the bytes in RAM were correct and said nothing
# about the stick -- and it reported success either way. See L-060.
CACHE_SUSPECT_BYTES_PER_SEC = 400 * 1024 * 1024

# ...but only where it means something. On NVMe, 1.5 GB/s could be the cache OR the disk,
# so the heuristic cannot separate them and would fail every legitimate local verify. The
# case that matters is the one this exists for: checking a backup on removable media, where
# anything above ~100 MB/s did not come off the stick.
REMOVABLE_PREFIXES = ("/media/", "/run/media/", "/mnt/")


# A network share is the same problem as a USB stick and slightly worse. gvfs caches
# aggressively, so a verify straight after writing can be served entirely from RAM at
# NVMe speed while the NAS holds nothing -- and it would report success. Added 2026-08-24
# when the backup target became afp://MyBookLive.local/Public/HiveMind, which lands under
# /run/user/<uid>/gvfs/ and matched none of the prefixes above.
NETWORK_PREFIXES = ("/run/user/",)
NETWORK_FSTYPES = ("fuse.gvfsd-fuse", "cifs", "smb3", "nfs", "nfs4", "afpfs", "fuse.sshfs")


def fstype(path):
    """The filesystem type carrying `path`, or '' if it cannot be determined."""
    r = subprocess.run(["findmnt", "-n", "-o", "FSTYPE", "--target", str(path)],
                       capture_output=True, text=True, timeout=30)
    return r.stdout.strip()


def looks_removable(path):
    p = str(Path(path).resolve())
    if p.startswith(REMOVABLE_PREFIXES) or p.startswith(NETWORK_PREFIXES):
        return True
    return fstype(p) in NETWORK_FSTYPES

MANIFEST = "MANIFEST.sha256"
INFO = "BACKUP-INFO.txt"
BUNDLE = "ai-workspace-corpora"
_TTY = sys.stdout.isatty()


def _c(code, text):
    return f"\033[{code}m{text}\033[0m" if _TTY else text


def sha256(path, chunk=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            b = fh.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def copy_tree_content(src, dst):
    """Copy a directory tree, carrying the bytes and not caring about the metadata.

    `shutil.copytree` cannot be used against a network share. It calls `copystat` on every
    directory it creates -- not through `copy_function`, directly -- and on the AFP/gvfs
    mount that raises `OSError: [Errno 95] Operation not supported`. It gathers the errors
    and raises at the end, so the first attempt on 2026-08-24 finished writing every byte
    correctly and then aborted without a manifest. Twice: fixing the file path left the
    directory path still failing.

    What a backup owes you is the bytes. The manifest hashes them afterwards and `--verify`
    re-hashes them off the medium; an mtime on the copy is decoration. Content errors still
    propagate.
    """
    src, dst = Path(src), Path(dst)
    for dirpath, dirnames, filenames in os.walk(src):
        dirnames.sort()
        rel = Path(dirpath).relative_to(src)
        (dst / rel).mkdir(parents=True, exist_ok=True)
        for fn in sorted(filenames):
            copy_content_only(Path(dirpath) / fn, dst / rel / fn)


def copy_content_only(src, dst):
    """Copy the BYTES, and treat a metadata failure as cosmetic rather than fatal.

    `shutil.copytree` defaults to `copy2`, which copies content and then timestamps and
    extended attributes. On a network share -- the AFP/gvfs mount used from 2026-08-24 --
    that second step raises `OSError: [Errno 95] Operation not supported`, and copytree
    collects the errors and raises at the end. The whole backup aborted with **every file's
    content already correctly written**, and no manifest, which is the worst of both: the
    data is there and nothing says so.

    What matters here is the bytes, and the manifest hashes them afterwards. A missing
    mtime on a backup copy costs nothing; a backup that refuses to complete costs the
    backup. So content failures still raise, and only the metadata step is swallowed.
    """
    shutil.copyfile(src, dst)
    try:
        shutil.copystat(src, dst)
    except OSError as e:
        print(f"  NOTE: timestamps not copied to {dst} ({e}) -- content is", file=sys.stderr)
    return dst


def walk(root):
    """Every file under root, as (relative_posix_path, absolute_path), sorted."""
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for fn in sorted(filenames):
            p = Path(dirpath) / fn
            out.append((p.relative_to(root).as_posix(), p))
    return sorted(out)


def git_head():
    r = subprocess.run(["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"],
                       capture_output=True, text=True, timeout=60)
    return r.stdout.strip() or "(unknown)"


def do_backup(dest_root):
    dest_root = Path(dest_root).resolve()
    if not dest_root.is_dir():
        print(_c("31", f"  target does not exist: {dest_root}"))
        return 1
    # Refuse to back up into the repository: it would be inside the thing being backed
    # up, would land in git status, and gives no independence whatsoever.
    if dest_root.is_relative_to(REPO_ROOT):
        print(_c("31", "  refusing to write a backup inside the repository."))
        print(_c("90", "  A copy that lives in the thing it protects is not a backup."))
        return 1

    bundle = dest_root / BUNDLE
    bundle.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    lines, total_files, total_bytes, missing = [], 0, 0, []
    for rel, why in CORPORA:
        src = REPO_ROOT / rel
        if not src.is_dir():
            missing.append(rel)
            continue
        print(f"  copying {rel} …")
        target = bundle / rel
        if target.exists():
            shutil.rmtree(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        copy_tree_content(src, target)
        for relpath, abspath in walk(target):
            digest = sha256(abspath)
            size = abspath.stat().st_size
            lines.append(f"{digest}  {rel}/{relpath}")
            total_files += 1
            total_bytes += size
        print(f"    {rel}: done")

    # The same treatment for the two stores that live outside the repository. Kept in a
    # separate loop rather than folded into CORPORA because their paths are absolute and
    # their archive names are chosen, not derived from a repo-relative path.
    for src, name, why in EXTERNAL:
        if not src.is_dir():
            missing.append(str(src))
            continue
        print(f"  copying {name} …")
        target = bundle / name
        if target.exists():
            shutil.rmtree(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        copy_tree_content(src, target)
        for relpath, abspath in walk(target):
            lines.append(f"{sha256(abspath)}  {name}/{relpath}")
            total_files += 1
            total_bytes += abspath.stat().st_size
        print(f"    {name}: done")

    (bundle / MANIFEST).write_text("\n".join(lines) + "\n", encoding="utf-8")
    (bundle / INFO).write_text(
        f"ai-workspace corpora backup\n"
        f"created      : {stamp}\n"
        f"source       : {REPO_ROOT}\n"
        f"git HEAD     : {git_head()}\n"
        f"files        : {total_files}\n"
        f"bytes        : {total_bytes}\n"
        f"corpora      : {', '.join(r for r, _ in CORPORA)}\n"
        f"missing      : {', '.join(missing) if missing else 'none'}\n"
        f"\n"
        f"These are the two corpora git does not carry (D-22, D-15). Verify with:\n"
        f"    python3 scripts/backup_corpora.py --verify <this directory>\n"
        f"\n"
        f"UNPLUG THE DRIVE. A backup that stays mounted and writable is reachable by\n"
        f"anything that can reach this machine, which is the case it exists to cover.\n",
        encoding="utf-8",
    )

    print()
    print(_c("32", f"  wrote {total_files} file(s), {total_bytes:,} bytes"))
    print(_c("90", f"  to {bundle}"))
    if missing:
        print(_c("33", f"  MISSING and not backed up: {', '.join(missing)}"))
    print()
    print(_c("33", "  Now verify it, then unplug the drive."))
    print(_c("90", "  Verify AFTER remounting, or the check reads your page cache and"))
    print(_c("90", "  passes without touching the drive:"))
    print(_c("90", "      udisksctl unmount -b /dev/sdX && udisksctl mount -b /dev/sdX"))
    return 1 if missing else 0


def resolve_bundle(path):
    """The directory that actually holds the manifest, descending ONE level if it must.

    `--to X` writes into `X/ai-workspace-corpora`, and corpus_routine passes a dated
    directory as X -- so a bundle on the NAS lives at
    `ai-workspace-corpora-2026-08-29/ai-workspace-corpora/`. Verifying the dated directory,
    which is the path a person reads off `ls`, printed "not a backup made by this script"
    about a backup that was perfectly good. Measured 2026-08-31 on a bundle that then
    verified byte-identical one level down.

    That is finding 17's shape and it lands on the habit that makes the NAS acceptable as
    the only copy -- "you always verify the backup was successful". A verify that cries wolf
    is one people stop running.

    Descends only when it is UNAMBIGUOUS: no manifest here, exactly one subdirectory, and
    that subdirectory has one. Anything else is left to fail loudly, because guessing which
    of several directories was meant is how a verify starts checking the wrong thing.
    """
    path = Path(path).resolve()
    if (path / MANIFEST).is_file():
        return path, ""
    subs = [d for d in path.iterdir() if d.is_dir()] if path.is_dir() else []
    if len(subs) == 1 and (subs[0] / MANIFEST).is_file():
        return subs[0], f"  (the bundle is {subs[0].name}/ inside that directory)"
    return path, ""


# Sidecars a FILE SERVER writes next to the real files, never anything this script wrote.
# Deliberately short and deliberately not a pattern language: every entry is a specific
# server's known artefact, so adding to it is a decision rather than a habit.
SERVER_SIDECARS = (".AppleDouble/", ".AppleDB/", ".AppleDesktop/", ".TemporaryItems/")
SERVER_SIDECAR_FILES = (".DS_Store", "._.DS_Store", ".apdisk", "Thumbs.db")


def _is_server_sidecar(rel):
    """True for metadata the NAS writes, which is not part of the backup and never was."""
    parts = rel.split("/")
    if parts[-1] in SERVER_SIDECAR_FILES:
        return True
    return any(f"{seg}/" in SERVER_SIDECARS or seg + "/" in SERVER_SIDECARS
               for seg in parts[:-1]) or any(rel.startswith(d) or f"/{d}" in f"/{rel}"
                                             for d in SERVER_SIDECARS)


def do_verify(bundle, strict_cold=None):
    bundle, note = resolve_bundle(bundle)
    if note:
        print(_c("90", note))
    manifest = bundle / MANIFEST
    if not manifest.is_file():
        print(_c("31", f"  no {MANIFEST} in {bundle} — not a backup made by this script."))
        return 1

    expected = {}
    for line in manifest.read_text(encoding="utf-8").splitlines():
        if "  " in line:
            digest, rel = line.split("  ", 1)
            expected[rel] = digest

    if not expected:
        print(_c("31", "  manifest lists 0 files — nothing was verified, which is not a pass."))
        return 1

    ok = bad = gone = 0
    problems = []
    started = time.perf_counter()
    bytes_read = 0
    for rel, digest in expected.items():
        p = bundle / rel
        if not p.is_file():
            gone += 1
            problems.append(f"MISSING  {rel}")
            continue
        bytes_read += p.stat().st_size
        if sha256(p) == digest:
            ok += 1
        else:
            bad += 1
            problems.append(f"CORRUPT  {rel}")

    # A file present in the bundle but absent from the manifest is also a discrepancy:
    # something was added after the fact, and the manifest no longer describes the bundle.
    #
    # EXCEPT THE SERVER'S OWN SIDECARS. Found 2026-09-11: the same bundle verified clean over
    # AFP and reported 564 UNLISTED files over NFS, every one of them under `.AppleDouble/`.
    # Those are netatalk's resource-fork sidecars -- the AFP server writes them for each file
    # and hides them from AFP clients, so they are invisible from the transport that created
    # them and visible from every other one. Nothing was corrupt, nothing was missing, and the
    # check still said BACKUP IS NOT INTACT.
    #
    # A check that fails on noise is a check people stop reading, which is the same death as
    # the gate that refused 18 legitimate files on its first day. But the fix is NOT to widen
    # this into a general ignore-list: an unlisted file is a real signal and the whole point of
    # the comparison. Only sidecars a FILE SERVER creates are excluded, they are named
    # explicitly, and they are counted so their disappearance is still visible.
    on_disk = {
        rel for rel, _ in walk(bundle)
        if rel not in (MANIFEST, INFO) and not _is_server_sidecar(rel)
    }
    extra = sorted(on_disk - set(expected))
    for e in extra[:5]:
        problems.append(f"UNLISTED {e}")

    elapsed = max(time.perf_counter() - started, 1e-6)
    rate = bytes_read / elapsed

    print(f"  manifest lists : {len(expected)} file(s)")
    print(f"  read           : {bytes_read:,} bytes in {elapsed:.2f}s "
          f"({rate / 1048576:.0f} MB/s)")
    print(f"  verified ok    : {ok}")
    print(f"  corrupt        : {bad}")
    print(f"  missing        : {gone}")
    print(f"  unlisted       : {len(extra)}")
    if problems:
        print()
        for p in problems[:12]:
            print(_c("31", f"    {p}"))
        print()
        print(_c("31", "  BACKUP IS NOT INTACT."))
        return 1
    # Enforce on removable media by default; elsewhere report the rate and move on.
    enforce = looks_removable(bundle) if strict_cold is None else strict_cold
    if enforce and rate > CACHE_SUSPECT_BYTES_PER_SEC:
        print()
        print(_c("33", f"  WARNING: {rate / 1048576:.0f} MB/s is faster than removable media."))
        print(_c("33", "  This was almost certainly read from the page cache, not the drive,"))
        print(_c("33", "  so it does NOT verify what is actually stored. Re-verify after:"))
        print(_c("90", "      udisksctl unmount -b /dev/sdX && udisksctl mount -b /dev/sdX"))
        print()
        print(_c("33", "  Reporting the hash result anyway, but treat it as unproven."))
        return 1

    print()
    print(_c("32", "  Backup verified: every file present and byte-identical."))
    return 0


def read_manifest(d):
    """{path: sha} from a backup's MANIFEST.sha256, wherever it sits in the tree."""
    d = Path(d)
    for m in [d / "MANIFEST.sha256"] + sorted(d.glob("*/MANIFEST.sha256")):
        if m.is_file():
            out = {}
            for line in m.read_text(encoding="utf-8", errors="replace").splitlines():
                if "  " in line:
                    h, rel = line.strip().split("  ", 1)
                    out[rel] = h
            return out, m
    return None, None


def diff_backups(older, newer):
    """Files lost between two backups, and files whose bytes changed.

    `L-119`. A backup that is only ever written is a hope. Two corpus files disappeared from
    an untracked directory between 2026-08-24 and 2026-08-26 and nothing here could have
    noticed; comparing the two manifests took seconds and found both.

    A LOSS is reported apart from a change, because they are not the same event: a changed
    file is work, a vanished one is usually a mistake nobody made on purpose.
    """
    old_m, old_p = read_manifest(older)
    new_m, new_p = read_manifest(newer)
    if old_m is None or new_m is None:
        print(f"  no MANIFEST.sha256 under {older if old_m is None else newer} -- "
              "nothing was compared, which is not a pass.")
        return 2
    lost = sorted(set(old_m) - set(new_m))
    added = sorted(set(new_m) - set(old_m))
    changed = sorted(r for r in set(old_m) & set(new_m) if old_m[r] != new_m[r])
    print(f"  older: {len(old_m)} file(s)  {old_p}")
    print(f"  newer: {len(new_m)} file(s)  {new_p}\n")
    for r in lost:
        print(f"  [LOST]    {r}")
    for r in changed[:20]:
        print(f"  [changed] {r}")
    for r in added[:20]:
        print(f"  [added]   {r}")
    if lost:
        print(f"\n  {len(lost)} file(s) in the older backup and ABSENT from the newer.")
        print("  If they are also absent from the live corpus, the older backup is the only")
        print("  copy. Restore before writing over it.")
        return 1
    print(f"\n  nothing lost. {len(changed)} changed, {len(added)} added.")
    return 0


LIVE_NAME = "ai-workspace-corpora"      # the rolling target nas_backup.sh rewrites nightly: never pruned


def do_prune(root, keep=3, apply=False, verify=None):
    """Keep the newest `keep` bundles that VERIFY; remove the rest of the dated siblings (t206).

    Jas, 2026-08-31: a 3-version retention policy. The rules, from the scope:
      * a bundle is a directory resolve_bundle() finds a MANIFEST in -- no second rule;
      * newest first by manifest mtime, and a bundle that FAILS verification does not count
        toward `keep` (2026-08-26: a verified bundle was truncated 478 -> 177 by an interrupted
        re-copy; keep-the-newest would have kept the damaged one);
      * REFUSE everything if fewer than `keep` verify; never remove the only verified bundle;
      * print what is kept and removed, with file counts, BEFORE removing;
      * the live rolling directory (LIVE_NAME) is never a candidate;
      * report-only unless apply=True, and safe to re-run if the mount drops mid-way: removals
        happen only after the kept set is verified, oldest first."""
    verify = verify or (lambda b: do_verify(b) == 0)
    root = Path(root)
    cands = []
    for d in sorted(p for p in root.iterdir() if p.is_dir() and p.name != LIVE_NAME):
        b, _ = resolve_bundle(d)
        if (b / MANIFEST).is_file():
            n = sum(1 for _ in (b / MANIFEST).open(encoding="utf-8", errors="replace"))
            cands.append((d, b, (b / MANIFEST).stat().st_mtime, n))
    cands.sort(key=lambda c: -c[2])
    kept, failed = [], []
    for d, b, _m, n in cands:
        if len(kept) >= keep:
            break
        (kept if verify(b) else failed).append((d, n))
    if len(kept) < keep:
        print(_c("31", f"  REFUSED: only {len(kept)} of the {len(cands)} bundle(s) verify; {keep} are needed "
                       f"before anything is removed. Nothing removed."))
        return 1
    keep_set = {d for d, _ in kept}
    remove = [(d, n) for d, _b, _m, n in sorted(cands, key=lambda c: c[2]) if d not in keep_set]
    print(f"  KEEP ({len(kept)} verified, newest first):")
    for d, n in kept:
        print(f"    {d.name}  ({n} files)")
    if failed:
        print(_c("33", f"  FAILED VERIFY (not counted, removed with the rest): "
                       + ", ".join(d.name for d, _ in failed)))
    print(f"  {'REMOVE' if apply else 'WOULD REMOVE'} ({len(remove)}, oldest first):")
    for d, n in remove:
        print(f"    {d.name}  ({n} files)")
    if not apply:
        print(_c("90", "  report only -- run again with --apply to remove them"))
        return 0
    for d, _n in remove:
        shutil.rmtree(d)
        print(_c("90", f"    removed {d.name}"))
    return 0


def prune_bundles(live, keep=3, apply=False):
    """Keep the newest `keep` *.bundle files in the live folder that pass `git bundle verify`;
    remove every other bundle and rewrite MANIFEST.sha256 to match (t690 Option A).

    do_prune() never looks inside LIVE_NAME, and nas_backup.sh drops one bundle in there every
    night, so without this they pile up unbounded. Same rules as do_prune: newest first (by
    name, which carries the date), a bundle that fails verify does not count toward `keep` and
    goes with the rest, REFUSE and remove nothing if fewer than `keep` verify, report-only
    unless apply=True. Non-bundle manifest lines are kept as they are."""
    live = Path(live)
    # ONLY THE NIGHTLY ai-workspace-<date>.bundle FILES. The live folder also holds other
    # bundles -- rejected-coder-branches-2026-10-01.bundle is a one-off archive -- and sorting
    # by name put that one above every nightly, so the first dry run against the NAS
    # (2026-10-12) would have kept it as "newest" and left two nightlies instead of three.
    bundles = sorted(live.glob("ai-workspace-*.bundle"), key=lambda p: p.name, reverse=True)
    kept, failed = [], []
    for b in bundles:
        if len(kept) >= keep:
            break
        # AGAINST THIS REPO, NOT THE CALLER'S CWD (2026-10-27). Without -C, git looked for a
        # repository where the caller stood: from a non-repo directory every bundle "failed" to
        # verify and the prune refused, and in coder.py's sandbox (no .git by design) that refusal
        # stopped every build -- "only 0 of the 7 bundle(s) verify". A bundle is verified against
        # the history that made it.
        ok = subprocess.run(["git", "-C", str(REPO_ROOT), "bundle", "verify", str(b)],
                            capture_output=True, timeout=600).returncode == 0
        (kept if ok else failed).append(b)
    if keep > len(kept):  # prune_bundles: refuse, remove nothing
        print(_c("31", f"  REFUSED: only {len(kept)} of the {len(bundles)} bundle(s) verify; "
                       f"{keep} are needed before anything is removed. Nothing removed."))
        return 1
    remove = [b for b in reversed(bundles) if b not in kept]
    print(f"  KEEP ({len(kept)} verified bundles, newest first): " + ", ".join(b.name for b in kept))
    if failed:
        print(_c("33", "  FAILED VERIFY (removed with the rest): " + ", ".join(b.name for b in failed)))
    print(f"  {'REMOVE' if apply else 'WOULD REMOVE'} ({len(remove)}): " + ", ".join(b.name for b in remove))
    if not apply:
        print(_c("90", "  report only -- run again with --apply to remove them"))
        return 0
    gone = {b.name for b in remove}
    m = live / MANIFEST
    if m.is_file():
        lines = [l for l in m.read_text().splitlines()
                 if l.strip() and l.rsplit("  ", 1)[-1] not in gone]
        m.write_text("\n".join(lines) + "\n")
    for b in remove:
        b.unlink()
        print(_c("90", f"    removed {b.name}"))
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--to", metavar="DIR", help="Directory to write the backup into.")
    g.add_argument("--verify", metavar="DIR", help="Backup directory to check.")
    g.add_argument("--prune", metavar="DIR", help="Keep the newest --keep bundles that verify; "
                   "report what else would go (add --apply to remove).")
    g.add_argument("--prune-bundles", metavar="DIR", help="Keep the newest --keep *.bundle "
                   "files in the live folder that verify; rewrite its manifest (add --apply). Other bundles there are left alone.")
    g.add_argument("--diff-against", metavar="DIR", nargs=2,
                   help="Two backup directories, older then newer. Reports files present in "
                        "the older and absent from the newer as a LOSS.")
    ap.add_argument("--strict-cold", action="store_true",
                    help="Fail if the read rate suggests the page cache served it. On by "
                         "default for removable media; use this to force it anywhere.")
    ap.add_argument("--keep", type=int, default=3)
    ap.add_argument("--apply", action="store_true", help="with --prune: actually remove")
    args = ap.parse_args()
    print()
    if args.prune_bundles:
        rc = prune_bundles(args.prune_bundles, keep=args.keep, apply=args.apply)
    elif args.prune:
        rc = do_prune(args.prune, keep=args.keep, apply=args.apply)
    elif args.diff_against:
        rc = diff_backups(*args.diff_against)
    else:
        rc = (do_backup(args.to) if args.to
              else do_verify(args.verify, strict_cold=args.strict_cold or None))
    print()
    return rc


if __name__ == "__main__":
    sys.exit(main())
