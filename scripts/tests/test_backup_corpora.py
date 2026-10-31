#!/usr/bin/env python3
"""Fixtures for backup_corpora.py.

A backup you have never restored is a hope, not a backup. These make the verifier
FAIL on each way a bundle can rot, so a clean report means something.

IT RUNS AGAINST A FIXTURE ROOT, NOT THE REAL CORPORA (2026-10-27). The four corpora the backup
carries are gitignored, so they exist only in the live checkout: from any worktree -- where every
lane runs its tests -- "backup of the real corpora succeeds" got exit 1 with "MISSING and not
backed up: chat, ...", and that undeclared red blocked every commit touching backup_corpora.py.
The fixture is five small files in the same four shapes, so what is asserted below is the CODE:
what a bundle lists, what the verifier catches, and what it refuses.
"""
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
SCRIPT = REPO / "scripts" / "backup_corpora.py"
PASSED = FAILED = 0

# The stand-in corpora. One file in each of the four, plus a second in chat/ so the
# "unlisted file" case has something to bite on, and one BIG file because L-060's --strict-cold
# guard is a RATE (bytes/elapsed): with a handful of kilobytes the rate is a few MB/s and the
# guard cannot fire, which is how the first version of this fixture left it passing vacuously.
# 120 MB puts the rate around 1 GB/s from the page cache, which is the case the guard exists for.
_BIG = 120 * 1024 * 1024
FIXTURE = Path(tempfile.mkdtemp(prefix="t014root_", dir=Path.home() / ".cache" / "hivemind-tmp"))
FIXTURE_FILES = {
    "chat/2026-10-01/transcript.bin": _BIG,          # rate fuel; .bin so it is not the victim below
    "chat/2026-10-01/notes.txt": 2048,               # > 1 KiB, the corruption case's victim
    "chat/README.md": 7,
    "projects/iso-assistant/ISO PDFs/doc.txt": 1024,
    "workspace/attachments/photo.txt": 4096,
    "library/notes.txt": 1024,
}
for _rel, _size in FIXTURE_FILES.items():
    _p = FIXTURE / _rel
    _p.parent.mkdir(parents=True, exist_ok=True)
    with _p.open("wb") as _f:                        # streamed: the 120 MB is never in RAM at once
        _f.write(b"x" * min(_size, 1024))
        _left = _size - min(_size, 1024)
        while _left > 0:
            _chunk = min(_left, 1 << 20)
            _f.write(b"\0" * _chunk)
            _left -= _chunk


def check(label, got, want):
    global PASSED, FAILED
    if got == want:
        PASSED += 1
        print(f"  [ok]   {label}: {got}")
    else:
        FAILED += 1
        print(f"  [BAD]  {label}: expected {want}, got {got}")


def run(*args, root=FIXTURE):
    """Exit code only. Captured directly -- piping to `tail` would report TAIL's status,
    which is how a failing verification once looked like a passing one.

    `root` defaults to the fixture. Pass `root=None` for the one case that is ABOUT the real
    repository (backing up into it must be refused), which needs the script to see it."""
    env = dict(os.environ)
    if root is None:
        env.pop("HIVEMIND_BACKUP_ROOT", None)
    else:
        env["HIVEMIND_BACKUP_ROOT"] = str(root)
    return subprocess.run([sys.executable, str(SCRIPT), *args],
                          capture_output=True, text=True, env=env).returncode


print("\nbackup_corpora.py fixtures\n")

dest = Path(tempfile.mkdtemp())
bundle = dest / "ai-workspace-corpora"

check("backup of a corpus root succeeds", run("--to", str(dest)), 0)
check("a fresh bundle verifies clean", run("--verify", str(bundle)), 0)

manifest = (bundle / "MANIFEST.sha256").read_text().splitlines()
listed = sorted(ln.split("  ", 1)[1] for ln in manifest if "  " in ln)
# EXACTLY what was there, not "more than 300" -- that number was a property of this machine's
# corpora, and it is the reason the suite could not run anywhere but the live checkout.
check("the manifest lists every corpus file, and nothing else", listed, sorted(FIXTURE_FILES))

# --- corruption ------------------------------------------------------------------------
victim = next(p for p in bundle.rglob("*.txt") if p.stat().st_size > 1024)
original = victim.read_bytes()
victim.write_bytes(original + b"x")
check("a single appended byte is caught", run("--verify", str(bundle)), 1)
victim.write_bytes(original)
check("restoring the byte makes it verify again", run("--verify", str(bundle)), 0)

# --- deletion --------------------------------------------------------------------------
moved = victim.read_bytes()
victim.unlink()
check("a missing file is caught", run("--verify", str(bundle)), 1)
victim.write_bytes(moved)

# --- something added after the fact ------------------------------------------------------
# The manifest must describe the bundle. A file nobody recorded means it no longer does.
stray = bundle / "chat" / "STRAY.txt"
stray.parent.mkdir(parents=True, exist_ok=True)  # chat/ is absent when the corpus is not in this tree
stray.write_text("added after the backup was taken\n")
check("an unlisted extra file is caught", run("--verify", str(bundle)), 1)
stray.unlink()
check("removing it verifies clean again", run("--verify", str(bundle)), 0)

# --- a bundle that is not one ------------------------------------------------------------
empty = Path(tempfile.mkdtemp())
check("a directory with no manifest is rejected", run("--verify", str(empty)), 1)
shutil.rmtree(empty, ignore_errors=True)

# A manifest that EXISTS but lists zero files is the same vacuous pass: the verify loop
# never runs, nothing is checked, and the old code said "every file present and
# byte-identical" over an empty set. A backup that is empty is not a backup that is intact.
empty_manifest = Path(tempfile.mkdtemp())
(empty_manifest / "MANIFEST.sha256").write_text("")
check("a manifest that lists zero files is rejected", run("--verify", str(empty_manifest)), 1)
shutil.rmtree(empty_manifest, ignore_errors=True)

# --- a bundle one level down is still a backup -------------------------------------------
# `--to X` writes into X/ai-workspace-corpora, so a dated directory on the NAS holds the
# bundle one level in. Verifying the dated directory -- the path you read off `ls` -- said
# "not a backup made by this script" about a backup that was byte-identical. A verify that
# cries wolf is one people stop running, and this is the habit that makes a single NAS copy
# acceptable at all.
nested = Path(tempfile.mkdtemp())
(nested / "inner").mkdir()
for f in bundle.iterdir():
    (shutil.copytree if f.is_dir() else shutil.copy2)(f, nested / "inner" / f.name)
check("a bundle one directory down still verifies", run("--verify", str(nested)), 0)

# And the descent must stay UNAMBIGUOUS: with two subdirectories there is no single obvious
# bundle, and guessing which was meant is how a verify starts checking the wrong thing.
(nested / "second").mkdir()
check("two subdirectories is refused, not guessed", run("--verify", str(nested)), 1)
shutil.rmtree(nested, ignore_errors=True)

# --- refuses to protect something with a copy inside it ----------------------------------
check("refuses to back up into the repository", run("--to", str(REPO), root=None), 1)

# --- a target that does not exist ---------------------------------------------------------
check("a non-existent target is rejected", run("--to", str(dest / "nope" / "deeper")), 1)

shutil.rmtree(dest, ignore_errors=True)

# --- L-060: a verify served from the page cache does not verify the medium ---------------
# Verifying 154 MB on a USB stick straight after writing it took 0.12s (~1.3 GB/s). After
# unmount and remount the same check took 9.35s (~17 MB/s). The first run read RAM, proved
# nothing about the stick, and reported success -- which is the dangerous shape, because a
# false clean is indistinguishable from a real one.
sys.path.insert(0, str(REPO / "scripts"))  # noqa: E402
from backup_corpora import looks_removable  # noqa: E402

check("a /media path counts as removable", looks_removable("/media/user/USB DISK"), True)
check("a /run/media path counts too", looks_removable("/run/media/x/y"), True)
check("/mnt counts", looks_removable("/mnt/backup"), True)
check("a home directory does not", looks_removable(str(Path.home())), False)
check("a tmp directory does not", looks_removable("/tmp/whatever"), False)

# --strict-cold forces the check anywhere, which is how it can be exercised off removable
# media at all. A local bundle is read at NVMe-plus-cache speed, so this must trip.
#
# A FRESH bundle, deliberately. The first version of this reused `bundle` from earlier in
# the file -- which shutil.rmtree had already deleted, so both assertions were run against
# nothing. The strict one "passed" by returning 1 because the manifest was missing, not
# because the cache check fired. A test that passes for the wrong reason is worse than one
# that fails, because nobody looks at it again.
fresh = Path(tempfile.mkdtemp())
run("--to", str(fresh))
fresh_bundle = fresh / "ai-workspace-corpora"
check("the fresh bundle really exists", (fresh_bundle / "MANIFEST.sha256").is_file(), True)
check("--strict-cold fails a cache-served local verify",
      run("--verify", str(fresh_bundle), "--strict-cold"), 1)
check("without it, the same local verify passes", run("--verify", str(fresh_bundle)), 0)
shutil.rmtree(fresh, ignore_errors=True)

# --- network targets: added 2026-08-24, when the backup moved to the MyBookLive NAS ------
#
# Two separate bugs, both of which let a broken backup look fine.

# 1. The cache-speed guard is the whole of L-060, and it only knew about removable prefixes.
#    The gvfs mount lives under /run/user/<uid>/gvfs/, so on the first network backup this
#    system ever took, the guard against a fake verify would not have run at all.
check("a gvfs network mount counts as cache-suspect",
      looks_removable("/run/user/1000/gvfs/afp-volume:host=MyBookLive.local,"
                      "user=admin,volume=Public/HiveMind"), True)
check("a home directory still does not", looks_removable(str(Path.home())), False)

# 2. copytree calls copystat on files AND directly on directories; AFP/gvfs answers
#    Errno 95. It collects the errors and raises at the END -- so every byte was written
#    correctly and the run aborted with no manifest. Simulated here by making copystat
#    raise, because the failure only reproduces on a real share otherwise.
import backup_corpora as bc  # noqa: E402

src = Path(tempfile.mkdtemp())
(src / "sub" / "deeper").mkdir(parents=True)
(src / "top.txt").write_bytes(b"top")
(src / "sub" / "a.txt").write_bytes(b"aaa")
(src / "sub" / "deeper" / "b.bin").write_bytes(b"\x00\x01\x02")
dst = Path(tempfile.mkdtemp()) / "copied"

real_copystat = bc.shutil.copystat
bc.shutil.copystat = lambda *a, **k: (_ for _ in ()).throw(
    OSError(95, "Operation not supported"))
try:
    bc.copy_tree_content(src, dst)
    completed = True
except OSError:
    completed = False
finally:
    bc.shutil.copystat = real_copystat

check("a tree copies even when copystat always fails", completed, True)
check("every file arrived", sorted(p.name for p in dst.rglob("*") if p.is_file()),
      ["a.txt", "b.bin", "top.txt"])
check("nested bytes are identical",
      (dst / "sub" / "deeper" / "b.bin").read_bytes(), b"\x00\x01\x02")
check("empty directories survive", (dst / "sub" / "deeper").is_dir(), True)

# And the control: a CONTENT failure must still propagate. Swallowing that would turn a
# lost file into a silent success, which is the opposite of what this backup is for.
bc.shutil.copyfile = lambda *a, **k: (_ for _ in ()).throw(OSError(5, "I/O error"))
try:
    bc.copy_tree_content(src, Path(tempfile.mkdtemp()) / "x")
    propagated = False
except OSError:
    propagated = True
check("but a CONTENT failure still raises", propagated, True)
shutil.rmtree(src, ignore_errors=True)

# --- server sidecars are not a discrepancy, and a real extra file still is ------------
# 2026-09-11: the same bundle verified clean over AFP and reported 564 UNLISTED files over
# NFS, all under .AppleDouble/ -- netatalk's sidecars, invisible from the transport that
# writes them. Nothing corrupt, nothing missing, and the verifier still said NOT INTACT.
# A check that fails on noise stops being read. A check that ignores everything is worse.
import backup_corpora as _bc

print("\nServer sidecars are the NAS's, not ours:")
for rel in (".AppleDouble/BACKUP-INFO.txt", "chat/.AppleDouble/.Parent", "chat/.DS_Store",
            ".TemporaryItems/x", "chat/Thumbs.db"):
    check(f"ignored: {rel}", _bc._is_server_sidecar(rel), True)

print("\n...and a genuine extra file is STILL a discrepancy -- that is the whole check:")
for rel in ("chat/real-note.md", "MANIFEST.sha256", "chat/AppleDoubleNotAFolder.md",
            "chat/notes/DS_Store-analysis.md", "smuggled.txt"):
    check(f"not ignored: {rel}", _bc._is_server_sidecar(rel), False)


shutil.rmtree(FIXTURE, ignore_errors=True)

print(f"\n{PASSED} passed, {FAILED} failed\n")
sys.exit(1 if FAILED else 0)
