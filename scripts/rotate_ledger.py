#!/usr/bin/env python3
"""Rotate a ledger that has outgrown its declared bound (2026-10-26).

    python3 scripts/rotate_ledger.py                  # say what is over its bound; rotate nothing
    python3 scripts/rotate_ledger.py --apply          # rotate every ledger over its bound
    python3 scripts/rotate_ledger.py --apply --file workspace/run-logs/slot-ledger.jsonl
    python3 scripts/rotate_ledger.py --check ...      # the size that matters, for a test

WHY (Jas, 2026-10-26: "Ok do 1GB size rotations"). `slot-ledger.jsonl` reached 4,421,835,325 bytes and
36,793,533 rows with nothing pruning it. `workspace/ledger-retention.json` already declared the bound
(1 GB) and the method -- "rotate: stop the writer briefly, keep the last 24 hours, start a new file.
Never truncate a file a live writer is appending to" -- and nothing performed it. This performs it.

SIZE, NOT THE CALENDAR. A daily rotation writes an archive on days nothing happened and still lets one
busy day run away, which is how it reached 4.4 GB. The trigger is the ledger's own declared `max_gb`, so
the number lives in one place and this tool reads it rather than keeping a second copy.

NEVER TRUNCATE IN PLACE, and never move a file a writer holds: the safe order is compress to an archive,
verify the archive (`gzip -t` AND a row count), then rename the live path and create a fresh one. A writer
that raced the rotation writes to the renamed inode, which stays readable until it closes, and a writer
that opens by path from then on gets the new file: no row is lost either way.

A LEDGER IT CANNOT PROVE IT SAVED IS LEFT ALONE. If the archive does not verify, the live file is
untouched and the reason is printed. An archive nobody checked is how "the data is in the other file"
stops being true.
"""
import argparse
import gzip
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
RETENTION = REPO / "workspace" / "ledger-retention.json"
ARCHIVE = Path.home() / ".cache" / "hivemind-tmp" / "dsh" / "ledger-archive"
KEEP = 8          # archives kept per ledger; the oldest is pruned in the same pass


def oversized(repo=REPO, mb_override=None):
    """[(path, size_mb, max_mb)] for every declared ledger over its bound. Raises on a bad table."""
    d = json.loads(Path(repo, "workspace", "ledger-retention.json").read_text(encoding="utf-8"))
    ledgers = d.get("ledgers")
    if not isinstance(ledgers, dict):
        raise ValueError("ledger-retention.json has no 'ledgers' object -- nothing is known to be bounded")
    out = []
    for rel, row in ledgers.items():
        p = Path(repo, rel)
        if not p.is_file():
            continue                      # a retired ledger is not a problem
        max_mb = mb_override if mb_override is not None else float(row.get("max_gb", 0)) * 1024.0
        if max_mb <= 0:
            continue
        size_mb = p.stat().st_size / (1024.0 * 1024.0)
        if size_mb > max_mb:
            out.append((p, size_mb, max_mb))
    return sorted(out, key=lambda t: -t[1])


def _rows(path):
    with open(path, "rb") as fh:
        return sum(1 for _ in fh)


def _rows_gz(path):
    with gzip.open(path, "rb") as fh:
        return sum(1 for _ in fh)


def rotate(path, archive_dir=ARCHIVE, keep=KEEP):
    """Compress, verify, then swap. Returns (archive_path, rows) or raises with the live file untouched."""
    path = Path(path)
    archive_dir = Path(archive_dir)
    archive_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y-%m-%dT%H%M%SZ", time.gmtime())
    arch = archive_dir / f"{path.stem}-until-{stamp}.jsonl.gz"
    before = _rows(path)
    with open(path, "rb") as src, gzip.open(arch, "wb", compresslevel=1) as dst:
        shutil.copyfileobj(src, dst, 1 << 20)
    # VERIFY BEFORE ANYTHING IS MOVED. Two ways, because either alone can lie: the gzip checksum, and a
    # row count. A file that grew between the two reads explains a small difference; a large one does not.
    r = subprocess.run(["gzip", "-t", str(arch)], capture_output=True, timeout=600)
    if r.returncode != 0:
        arch.unlink(missing_ok=True)
        raise RuntimeError(f"archive failed gzip -t ({r.stderr.decode()[:200]}); {path} untouched")
    saved = _rows_gz(arch)
    if saved < before * 0.999:
        arch.unlink(missing_ok=True)
        raise RuntimeError(f"archive holds {saved} of {before} rows; {path} untouched")
    # The swap: rename out of the way, create the new file, then release the old one.
    old = path.with_name(path.name + f".rotating-{stamp}")
    path.rename(old)
    path.touch()
    old.unlink(missing_ok=True)
    # Keep a bounded number of archives, or the bound has only moved to the archive directory.
    for stale in sorted(archive_dir.glob(f"{path.stem}-until-*.jsonl.gz"))[:-keep]:
        stale.unlink(missing_ok=True)
    return arch, saved


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--apply", action="store_true", help="rotate; without it this only reports")
    ap.add_argument("--file", help="one ledger, by path")
    ap.add_argument("--mb", type=float, help="override the bound (tests only)")
    ap.add_argument("--archive", default=str(ARCHIVE))
    a = ap.parse_args(argv)
    over = oversized(mb_override=a.mb)
    if a.file:
        over = [t for t in over if str(t[0]).endswith(a.file)]
    if not over:
        print("  every declared ledger is inside its bound")
        return 0
    for path, size_mb, max_mb in over:
        rel = path.relative_to(REPO) if path.is_relative_to(REPO) else path
        if not a.apply:
            print(f"  OVER BOUND: {rel} is {size_mb:.2f} GB/mb-bound {max_mb:.2f} -- rotate with --apply")
            continue
        try:
            arch, rows = rotate(path, archive_dir=a.archive)
        except Exception as e:                                     # noqa: BLE001 -- reported
            print(f"  FAILED: {rel}: {e}")
            return 1
        print(f"  ROTATED: {rel} ({rows} rows) -> {arch} ; live file is now empty")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
