#!/bin/bash
# Capture the logs error_scan.py cannot reach on its own, into files it can.
#
# WHY A COLLECTOR AND NOT A SCANNER THAT SHELLS OUT. Jas, 2026-09-11: "why don't we scan
# sub processes?" We should, and my contract said "no subprocess" as a blanket rule, which was
# over-broad. The real constraint is narrow: error_scan runs inside session_start, which is
# 2.5 seconds today, and a scanner that shells out to journalctl on every session start stops
# being something people run. So the subprocess lives HERE, on the timer, and writes files;
# the scanner stays fast, offline and testable.
#
# WHAT WAS INVISIBLE BEFORE THIS. Ollama is a SYSTEM unit, so every model eviction, every OOM
# kill and every 500 it ever returned went to the system journal -- which nothing in this
# repository has ever read. The three unexplained HTTP 500s from the 171-minute run (D4, still
# open) are almost certainly sitting in there right now. Memory-budget failures likewise: Jas
# named them specifically and they were not being captured anywhere.
set -u
REPO=/home/user/ai-workspace
OUT="$REPO/workspace/collected"
mkdir -p "$OUT"
cd "$REPO" || exit 1

SINCE="${1:-24 hours ago}"
STAMP=$(date -u +%Y-%m-%dT%H%M%SZ)

row () {   # row <status> <detail>
    python3 - "$REPO/workspace/backup-runs.jsonl" "$1" "$2" <<'PYROW'
import json, sys, time
path, status, detail = sys.argv[1], sys.argv[2], sys.argv[3]
with open(path, "a") as f:
    f.write(json.dumps({"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                        "job": "collect-logs", "status": status, "detail": detail}) + "\n")
PYROW
}
trap 'row died "collect_logs.sh exited without reaching its own end"' EXIT

# Ollama: evictions, OOM, 500s. -u works without sudo here; verified 2026-09-11.
if journalctl -u ollama.service --since "$SINCE" --no-pager > "$OUT/ollama-journal.log" 2>&1; then
    :
else
    echo "journalctl -u ollama.service failed" >> "$OUT/ollama-journal.log"
fi

# Our own units: the timers, the services, and anything that died.
journalctl --user --since "$SINCE" --no-pager \
    -u triage.service -u nas-backup.service -u chat-agent.service \
    -u agent-console.service -u model-chat.service \
    > "$OUT/user-units.log" 2>&1 || true

# The kernel's own view. An OOM kill is recorded here whatever the process claimed on the way
# out, which makes it a SECOND witness independent of our code -- the same property that makes
# `git bundle verify` worth more than "the copy finished".
journalctl --since "$SINCE" --no-pager -k 2>/dev/null \
    | grep -iE "out of memory|oom-kill|killed process|cgroup" > "$OUT/kernel-oom.log" 2>&1 || true

# Non-zero exits from anything systemd ran, which is the wrapper-lied class: coder.py returned
# rc=0 three times for dispatches that wrote nothing, and a shell loop reported success.
journalctl --user --since "$SINCE" --no-pager 2>/dev/null \
    | grep -iE "Failed with result|exit-code|core-dump|signal" > "$OUT/unit-failures.log" 2>&1 || true

# ---- OS-LEVEL EVENTS. Added 2026-09-11 when Jas asked what else was missing, and the
# ---- answer turned out to include the thing that had already bitten us that morning.

# BOOTS AND UNCLEAN SHUTDOWNS. The HiveMind rebooted THREE times on 2026-09-10 and I knew
# about one, by chance, because I happened to run `uptime`. The 06:30 shutdown was five
# minutes after a dispatch finished. A machine that restarts under a running job explains
# a whole class of "it just stopped" with no other evidence anywhere.
journalctl --list-boots --no-pager > "$OUT/boots.log" 2>&1 || true
# Whether the last shutdown was clean. An unclean one means work was lost, not finished.
journalctl -b -1 --no-pager 2>/dev/null | tail -40 > "$OUT/last-shutdown.log" 2>&1 || true

# GPU FAULTS. Two XID/NVRM lines are already in this kernel log and nothing has read them.
# A 55 GB model on one card makes this the difference between "the model is bad" and "the
# card fell off the bus" -- the exact confusion this project exists to stop.
journalctl -k --no-pager 2>/dev/null     | grep -iE "xid|nvrm|gpu has fallen|nvidia" > "$OUT/gpu-faults.log" 2>&1 || true

# FAILED UNITS, asked directly rather than grepped out of prose.
{ systemctl --failed --no-pager 2>/dev/null; systemctl --user --failed --no-pager 2>/dev/null; }     > "$OUT/failed-units.log" 2>&1 || true

# TIMERS THAT DID NOT FIRE. A Persistent timer that has never run leaves NOTHING in the
# journal to find -- absence is the signal, so the schedule itself has to be recorded.
systemctl --user list-timers --all --no-pager > "$OUT/timers.log" 2>&1 || true

# DISK AND INODES. ENOSPC mid-backup produces a partial bundle and a confusing error,
# which is exactly the 2026-09-01 shape.
{ df -h /; df -i /; df -h /mnt/nas 2>/dev/null; } > "$OUT/disk.log" 2>&1 || true

# FILESYSTEM AND NFS TROUBLE: I/O errors, stale handles, a share that went away mid-write.
journalctl -k --no-pager 2>/dev/null     | grep -iE "i/o error|nfs:|stale file handle|ext4-fs error|remount"     > "$OUT/fs-nfs.log" 2>&1 || true

LINES=$(cat "$OUT"/*.log 2>/dev/null | wc -l)
trap - EXIT
row collected "$LINES line(s) into workspace/collected/ since '$SINCE' at $STAMP"
echo "collected $LINES line(s) into $OUT"
