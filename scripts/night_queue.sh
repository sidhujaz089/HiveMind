#!/bin/bash
# Everything that should happen overnight without anybody watching, in one place.
#
# Jas, 2026-09-11: "I am hoping the queue end to end will be processed unprompted so I can
# sleep and wake up to it all done."
#
# THE RULE THIS RUNNER DOES NOT BREAK: it BUILDS, it does not MERGE. coder.py delivers to a
# review branch and Claude is the gate (D-34). A runner that merged its own work overnight
# would be the model certifying itself, which is the defect the whole review queue exists to
# prevent. So the morning state is "branches waiting", never "it landed while you slept".
#
# EVERY STEP WRITES A ROW. A night that dies half way must be visible as a dead night rather
# than as silence -- that is the 2026-09-01 partial-backup shape, and the reason coder.py
# returning rc=0 for three dispatches that wrote nothing went unnoticed for an hour.
set -u
REPO=/home/user/ai-workspace
# INPUTS AND OUTPUTS LIVE IN THE REPOSITORY, NOT IN /tmp (t268).
#
# This was a hardcoded Claude Code SESSION scratchpad under the per-session tmp root.
# That session ended weeks ago. Every brief this runner reads and every log and draft it
# writes lived there, surviving only because nothing had cleared /tmp yet. On reboot, or the
# next systemd-tmpfiles sweep, build() would find no brief, write four `no-brief` rows and
# finish clean -- a night that reports itself as having run while doing nothing. That is the
# exact silence this file's own header argues against.
#
# It also made Jas's ask of 2026-09-11 impossible: a plan that "can be infinity long" cannot
# keep its work items in a directory that disappears.
BRIEFS=$REPO/workspace/briefs
SP=$REPO/workspace/run-logs
LEDGER="$REPO/workspace/backup-runs.jsonl"
cd "$REPO" || exit 1

row () {
    python3 - "$LEDGER" "$1" "$2" <<'PYROW'
import json, sys, time
path, status, detail = sys.argv[1], sys.argv[2], sys.argv[3]
with open(path, "a") as f:
    f.write(json.dumps({"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                        "job": "night-queue", "status": status, "detail": detail}) + "\n")
PYROW
}
trap 'row died "night_queue.sh exited without reaching its own end"' EXIT

# REFUSE TO START WITHOUT THE INPUTS, rather than skipping four jobs and looking clean.
#
# t268's decay test: a missing brief directory must be distinguishable from "nothing to do".
# build() writes `no-brief` per job and returns 1, which reads identically to a job that had
# nothing queued -- so the directory is checked ONCE, here, and a night with no inputs dies
# loudly and non-zero instead of reporting a clean run.
if [ ! -d "$BRIEFS" ]; then
    row inputs-missing "$BRIEFS does not exist -- the runner has no work and did not start"
    trap - EXIT
    exit 1
fi

row started "the overnight queue began"

# ---------------------------------------------------------------- 1. collectors
./scripts/collect_logs.sh "36 hours ago" >/dev/null 2>&1 \
    && row collected "journal, ollama and unit failures captured" \
    || row collect-failed "collect_logs.sh did not complete"

# ---------------------------------------------------------------- 2. Q16 rebuilds
# One at a time. They share one Ollama, and two jobs on one server is what corrupted the
# 2026-09-07 measurement. Each dispatch takes the lease itself.
build () {   # build <name> <target-suite> <file-to-change>
    local name=$1 target=$2 file=$3
    if timeout 180 python3 "$target" >/dev/null 2>&1; then
        row skipped "$name: its suite is already green"
        return 0
    fi
    [ -f "$BRIEFS/brief-$name.txt" ] || { row no-brief "$name: no brief file"; return 1; }
    until [ ! -f workspace/scoping-lease.json ]; do sleep 30; done
    timeout 5400 python3 projects/local-inference/coder.py \
        --brief "$(cat "$BRIEFS/brief-$name.txt")" --files "$file" --target "$target" \
        --model "$(python3 -c 'import sys; sys.path.insert(0, "scripts"); import roles; print(roles.for_job("building", "gemma4:26b"))')" --apply >> "$SP/night-$name.log" 2>&1   # the coder ROLE's model, not 54 GB bf16 (t410)
    # coder.py exits 0 even when NOT ACCEPTED (t263), so its exit code is not the truth.
    # The suite is. Ask the artefact, never the wrapper.
    if grep -q "ACCEPTED on attempt" "$SP/night-$name.log" 2>/dev/null; then
        row built "$name: delivered a review branch"
    else
        row build-failed "$name: no branch delivered -- see $SP/night-$name.log"
    fi
}
build errorscan scripts/tests/test_error_scan.py          scripts/error_scan.py
build triage    scripts/tests/test_triage.py              scripts/triage.py
build preflight scripts/tests/test_preflight_failsafe.py  scripts/preflight_brief.py
build ledger    scripts/tests/test_runledger_two_runs.py  scripts/runledger.py

# ------------------------------------------------- 2b. ORACLE DRAFTS (a document, not a patch)
# Policy confirmed by Jas 2026-09-11: Q16 may DRAFT oracles; Claude reviews and FREEZES them
# before any implementation is dispatched against them.
#
# WHY THIS IS NOT A coder.py DISPATCH. coder.py's shape is "make this suite green by editing
# these files". If the file being edited IS the suite, the model makes it green by deleting
# assertions -- the self-certification hole the freeze exists to close. An oracle draft is a
# DOCUMENT, so it goes through ask_q16, and only the implementation goes through coder.py.
draft () {   # draft <name> <output-path>
    local name=$1 out=$2
    [ -s "$out" ] && { row skipped "$name: a draft already exists at $out"; return 0; }
    until [ ! -f workspace/scoping-lease.json ]; do sleep 30; done
    timeout 5400 python3 scripts/ask_q16.py --prompt "$BRIEFS/brief-oracle-$name.txt" \
        --out "$out" --budget 16000 --max-escalations 1 --max-rounds 8 --timeout 5400 \
        --purpose "oracle draft: $name" >> "$SP/night-oracle-$name.log" 2>&1
    if [ -s "$out" ]; then row drafted "$name: oracle draft written to $out, AWAITING REVIEW"
    else row draft-failed "$name: no draft produced"; fi
}
draft coderjsonx "$SP/DRAFT-test_coder_uses_jsonx.py"

# ---------------------------------------------------------------- 3. the historical scan
# Runs from the REVIEW BRANCH in a throwaway worktree, so the one-off crawl can happen
# tonight without merging unreviewed code into main. The findings are DATA for Claude to read
# on Monday; the code stays behind the gate where it belongs.
# PICK THE BRANCH THAT HAS THE SCANNER, not the newest one. On 2026-09-11 this took the
# newest coder branch -- which was the ledger build -- so the historical scan was skipped with
# "no branch has a working error_scan yet" while a branch with a working error_scan sat right
# there. Ask what a branch CONTAINS, never what order it arrived in.
BR=""
for cand in $(git for-each-ref --format='%(refname:short)' --sort=-committerdate refs/heads/coder/); do
    if git show "$cand:scripts/error_scan.py" 2>/dev/null | grep -q "^def scan"; then BR="$cand"; break; fi
done
if [ -n "$BR" ]; then
    WT="$SP/histwt"
    rm -rf "$WT"; git worktree add -q --detach "$WT" "$BR" 2>/dev/null
    if timeout 300 python3 -c "import sys;sys.path.insert(0,'$WT/scripts');import error_scan;error_scan.scan" >/dev/null 2>&1; then
        timeout 10800 python3 - "$WT" <<'PYSCAN' >> "$SP/night-histscan.log" 2>&1
import json, sys, pathlib
wt = sys.argv[1]
sys.path.insert(0, wt + "/scripts")
import error_scan
REPO = pathlib.Path("/home/user/ai-workspace")
# EVERYTHING, not the routine sources. This is the one-off Jas asked for: "I want all you
# dispatch, I dispatch, Q16 runs overnight ... scanned. I want to get on a clean slate."
sources = [REPO / p for p in (
    "workspace/agent-runs.jsonl", "workspace/review-verdicts.jsonl",
    "workspace/mcp-calls.jsonl", "workspace/backup-runs.jsonl",
    "workspace/scoping-proposals.jsonl", "workspace/probe-500.jsonl",
    "workspace/console", "workspace/plan", "workspace/collected",
    "workspace/sessions", "workspace/outbox", "workspace/inbox",
)] + [pathlib.Path.home() / ".claude/projects/-home-user-ai-workspace"]
found = error_scan.scan([s for s in sources])
out = REPO / "workspace/historical-findings.jsonl"
with open(out, "w") as f:
    for x in found:
        f.write(json.dumps(x) + "\n")
print(f"HISTORICAL: {len(found)} finding(s); skipped {error_scan.skipped()}")
PYSCAN
        N=$(wc -l < "$REPO/workspace/historical-findings.jsonl" 2>/dev/null || echo 0)
        row scanned "historical scan wrote $N finding(s) from branch $BR"
    else
        row scan-unavailable "no branch has a working error_scan yet; historical scan skipped"
    fi
    git worktree remove --force "$WT" 2>/dev/null
else
    row scan-unavailable "no coder branch to scan from"
fi

trap - EXIT
row finished "the overnight queue reached its end"
