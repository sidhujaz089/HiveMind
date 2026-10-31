#!/bin/bash
# Write the backup, then READ IT BACK. Exits non-zero if the verify fails, so a failed
# night is visible to `systemctl --user status nas-backup` instead of being a quiet no-op.
set -u
REPO=${NAS_BACKUP_REPO:-/home/user/ai-workspace}
DEST=${NAS_BACKUP_DEST:-/mnt/nas/Public/HiveMind}
STAMP=$(date -u +%Y-%m-%d)
cd "$REPO" || exit 1

# EVERY EXIT WRITES A ROW, and it goes where error_scan.py is looking.
#
# Jas, 2026-09-11: "If there are errors with the NAS will the error checker we just built
# catch it?" It would not have. This script's output went to stdout and therefore to the
# systemd journal, which no file-based scanner reads -- so a backup that failed every night
# for a week would have been invisible to the thing built to catch exactly that.
#
# The row carries a `status`, and the scanner enumerates the GOOD: only "verified" is healthy.
# Anything else -- including a status added years from now that nobody has thought of yet --
# surfaces as unrecognised. That is the whole point of enumerating the good rather than the bad.
LEDGER="$REPO/workspace/backup-runs.jsonl"
row () {   # row <status> <detail>
    python3 - "$LEDGER" "$1" "$2" <<'PYROW'
import json, sys, time
path, status, detail = sys.argv[1], sys.argv[2], sys.argv[3]
with open(path, "a") as f:
    f.write(json.dumps({
        "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "job": "nas-backup", "status": status, "detail": detail,
    }) + "\n")
PYROW
}
# A crash or a kill still leaves a row, so "the job died silently" is not a possible outcome.
trap 'row died "the backup exited without reaching its own end -- killed, crashed, or the NAS went away mid-run"' EXIT

# Touch the automount before anything else. A dead NAS must fail HERE, loudly, rather than
# half-way through a copy -- which is exactly how the 2026-09-01 partial was made.
if ! timeout 120 ls "$DEST" >/dev/null 2>&1; then
    echo "NAS not reachable at $DEST -- refusing to start a backup that cannot finish."
    trap - EXIT; row unreachable "NAS not mounted or not answering at $DEST"; exit 1
fi

# The repository, as ONE verifiable file. A bundle survives a flaky link where thousands of
# small .git objects do not, and `git bundle verify` is a real check.
mkdir -p "$REPO/workspace/mirror"
BUNDLE="$REPO/workspace/mirror/ai-workspace-$STAMP.bundle"
git bundle create "$BUNDLE" --all || { trap - EXIT; row bundle-failed "git bundle create failed"; exit 1; }
git bundle verify "$BUNDLE" >/dev/null || { trap - EXIT; row bundle-corrupt "the bundle we just made does not verify"; exit 1; }

python3 scripts/backup_corpora.py --to "$DEST" || { trap - EXIT; row copy-failed "backup_corpora --to did not complete"; exit 1; }
# SAY WHY (2026-10-19): two copies failed that day and the row said only "could not copy" -- the cause,
# NFS timeouts from the NAS itself, was in the kernel log and nowhere near the backup record.
# DIRECT I/O, TO A TEMP NAME (2026-10-19): a plain cp of the 399 MB bundle failed on close
# with EIO every time -- the page cache held the whole file and the flush at close outran the
# soft mount's timeo=150,retrans=3 over Wi-Fi. A 400 MB dd with conv=fsync reproduced it
# (EIO after 61 s); the same write with oflag=direct finished in 98 s, clean. The 249 MB
# bundles of the week before fit under the timeout, which is why nights passed until the
# repo grew. The temp name means a failed copy no longer overwrites today's verified bundle
# in place, which is what left the truncated 325 MB 10-19 file.
TMPB="$DEST/ai-workspace-corpora/.$(basename "$BUNDLE").partial"
if ! cperr=$(dd if="$BUNDLE" of="$TMPB" bs=4M oflag=direct status=none 2>&1) \
   || [ "$(sha256sum < "$BUNDLE")" != "$(sha256sum < "$TMPB")" ] \
   || ! cperr=$(mv -f "$TMPB" "$DEST/ai-workspace-corpora/$(basename "$BUNDLE")" 2>&1); then
    rm -f "$TMPB"
    nfs=$(journalctl -k --since "-15 min" --no-pager 2>/dev/null | grep -c "nfs: server .* not responding")
    trap - EXIT; row copy-failed "could not copy the bundle to the NAS: ${cperr:-cp gave no message}; NFS 'not responding' lines in the kernel log in the last 15 min: $nfs"; exit 1
fi
# EVERY bundle in the destination, not just today's. backup_corpora.py --to rewrites
# MANIFEST.sha256 from scratch on each run, so the bundles kept from previous nights were
# dropped from the manifest while staying on disk -- and --verify then counted them
# UNLISTED and declared "BACKUP IS NOT INTACT". It was intact: 524 of 524 files read back
# byte-identical, 0 corrupt, 0 missing. The only thing wrong was a manifest that did not
# describe what was actually there, and it had been reporting verify-failed since at least
# 2026-09-15 on that basis. A backup whose health check cries wolf is a backup nobody reads.
python3 - "$DEST/ai-workspace-corpora" <<'PY'
import hashlib, pathlib, sys
d = pathlib.Path(sys.argv[1])
m = d / "MANIFEST.sha256"
bundles = sorted(f.name for f in d.glob("*.bundle"))
lines = [l for l in m.read_text().splitlines()
         if l.strip() and not any(l.endswith("  " + b) for b in bundles)]
for b in bundles:
    lines.append(f"{hashlib.sha256((d / b).read_bytes()).hexdigest()}  {b}")
m.write_text("\n".join(lines) + "\n")
print(f"  manifest now lists {len(bundles)} bundle(s)")
PY

# The half that makes it a backup rather than a copy.
if python3 scripts/backup_corpora.py --verify "$DEST/ai-workspace-corpora" --strict-cold; then
    # Prune only after the read-back proves the copy intact, so a bad night never deletes
    # the versions that a verified backup needs to keep.
    python3 scripts/backup_corpora.py --prune-bundles "$DEST/ai-workspace-corpora" --apply --keep 3 || { trap - EXIT; row prune-failed "bundles: fewer than 3 verify -- nothing removed"; exit 1; }
    python3 scripts/backup_corpora.py --prune "$DEST" --apply --keep 3 || { trap - EXIT; row prune-failed "folders: fewer than 3 verify -- nothing removed"; exit 1; }
    trap - EXIT; row verified "read back cold and byte-identical; 3-version prune applied"; exit 0
else
    trap - EXIT; row verify-failed "the copy exists and does NOT read back byte-identical"; exit 1
fi
