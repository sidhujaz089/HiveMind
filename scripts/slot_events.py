#!/usr/bin/env python3
"""t362's writer: the slot hold/release ledger.

    python3 scripts/slot_events.py                      production paths
    python3 scripts/slot_events.py --cursor C --out O    isolated (what the oracle drives)

WHY THIS EXISTS. 2026-09-19, 10:47, an interactive dsh session died starved because a
3.5-hour model loop held the only model slot, and nothing on this box records who held
the slot and for how long -- the incident was reconstructable only from the dead
session's own transcript. This is the missing recorder: it appends structured
acquire/release/idle rows from the live `ollama` unit journal, one run per call, cursor
semantics so nothing is read twice or skipped. Field basis: docs/skills/root-cause-
analysis and workspace/feedback/2026-09-19--dsh-harness--rca-4afba784-corrections.md.

PATTERN. Adapts scripts/ollama_events.py -- the cursor file, one JSONL row per event,
and "NOT READING IS NOT NOTHING HAPPENED" (a failed journal read is recorded as its own
collector-failed row) all run on this box every dispatch tick already.

ROW CONTRACT (checked exactly by scripts/tests/test_slot_events.py; do not loosen it):
- acquire  {at_epoch:float, kind:"acquire", slot:int, task:int, resident:list[str]|null}
- release  {at_epoch:float, kind:"release", slot:int, task:int, held_s:float>=0|null,
            resident:list[str]|null}
- idle     {at_epoch:float, kind:"idle", slot:null, task:null, resident:list[str]|null}
- collector-failed {at_epoch:float, kind:"collector-failed", message:str}

ANCKHORS (verified on 2026-09-19: 1654 acquire / 812 release / 922 idle lines in the
2-day window of the ollama unit journal):
- acquire   "new prompt, n_ctx_slot"  or  "processing task"
            e.g.  slot operator(): id  0 | task 347 | new prompt, n_ctx_slot = 262144 ...
            e.g.  slot launch_slot_: id  0 | task 347 | processing task, is_child = 0
- release   "release: id  0 | task 347 | stop processing"
- idle      "update_slots: all slots are idle"

STDLIB ONLY. One run, append, exit 0. No threads, no timers, no daemon mode.

Agent: dsh-harness/qwen3.8-27b-mtp-q4_K_M
"""
import argparse
import json
import re
import sys
import time
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DEFAULT_CURSOR = REPO / "workspace" / "run-logs" / ".slot-journal.cursor"
DEFAULT_OUT = REPO / "workspace" / "run-logs" / "slot-ledger.jsonl"
PS_URL = "http://127.0.0.1:11434/api/ps"

ACQUIRE = re.compile(r"new prompt, n_ctx_slot|processing task", re.I)
RELEASE = re.compile(r"release:\s*id\s+\d+\s*\|.*stop processing", re.I)
IDLE = re.compile(r"all slots are idle", re.I)
SLOT = re.compile(r"\bid\s+(\d+)\s*\|")
TASK = re.compile(r"\|\s*task\s+(\d+)\s*\|")


def fetch_resident():
    """Model names from GET /api/ps (real shape: {"models":[{"name":...}]}); None on failure."""
    try:
        req = urllib.request.Request(PS_URL)
        with urllib.request.urlopen(req, timeout=10) as f:
            d = json.load(f)
        models = d.get("models")
        if isinstance(models, list):
            return [m.get("name") for m in models
                    if isinstance(m, dict) and m.get("name")]
    except Exception:                                   # noqa: BLE001 -- never die on the side channel
        return None
    return None


def scan_existing(out_path):
    """Latest acquire at_epoch per task in the EXISTING output file (contract).

    Returns (best_per_task, bad_line_count). Unparseable lines are CORRUPTION in the
    ledger and are counted, never silently skipped -- the caller reports the count.
    """
    best = {}
    bad = 0
    if out_path.exists():
        for line in out_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                r = json.loads(line)
            except ValueError:
                bad += 1
                continue
            if r.get("kind") == "acquire" and isinstance(r.get("task"), int):
                ts = r.get("at_epoch")
                if isinstance(ts, (int, float)) and not isinstance(ts, bool):
                    task = r["task"]
                    best[task] = ts if task not in best or ts > best[task] else best[task]
    return best, bad


def classify(msg):
    if IDLE.search(msg):
        return "idle"
    if RELEASE.search(msg):
        return "release"
    if ACQUIRE.search(msg) and SLOT.search(msg) and TASK.search(msg):
        return "acquire"
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cursor", type=Path, default=DEFAULT_CURSOR)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    # FIRST-RUN WINDOW (2026-10-08): a fresh cursor read two days of journal -- 170k acquires of
    # embedder traffic, ~2 min -- so the oracle timed out under load, read as red on main, and held
    # every build. The live collector keeps the default; the oracle asks for the last hour.
    ap.add_argument("--since", default="-2d", help="first run only (no cursor yet): journal window")
    a = ap.parse_args()

    now = time.time()
    resident = fetch_resident()
    existing_acq, bad_existing = scan_existing(a.out)

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from ollama_events import read_journal   # one journal reader: systemd 259 broke both copies
    r = read_journal(a.cursor, since=a.since)

    # in-run map of task -> latest acquire at_epoch (so held_s can see acquires from THIS run)
    run_acquire = dict(existing_acq)
    bad_json = 0
    rows = []
    if r.returncode != 0:
        # NOT READING IS NOT "NOTHING HAPPENED". Record the failure as its own row,
        # in full -- a record is never cut.
        rows.append({"at_epoch": now, "kind": "collector-failed",
                     "message": (r.stderr or r.stdout or "").strip()})
    else:
        for line in r.stdout.splitlines():
            if not line.strip():
                continue
            try:
                e = json.loads(line)
            except ValueError:
                # journalctl -o json emits whole JSON objects; an unparseable line is
                # a defect of the source, counted here and reported below, not skipped
                bad_json += 1
                continue
            msg = str(e.get("MESSAGE", ""))
            kind = classify(msg)
            if kind is None:
                continue
            at_epoch = int(e.get("__REALTIME_TIMESTAMP", "0")) / 1e6
            if not isinstance(at_epoch, (int, float)) or at_epoch <= 0:
                at_epoch = now
            row = {"at_epoch": at_epoch, "kind": kind}
            if kind in ("acquire", "release"):
                m_slot = SLOT.search(msg)
                m_task = TASK.search(msg)
                if not (m_slot and m_task):
                    continue                             # not a well-formed slot line; skip
                row["slot"] = int(m_slot.group(1))
                row["task"] = int(m_task.group(1))
                row["resident"] = resident
                if kind == "acquire":
                    run_acquire[row["task"]] = at_epoch
                else:
                    acq = run_acquire.get(row["task"])
                    row["held_s"] = round(at_epoch - acq, 6) if acq is not None else None
            else:
                row["slot"] = None
                row["task"] = None
                row["resident"] = resident
            rows.append(row)

    a.out.parent.mkdir(parents=True, exist_ok=True)
    with a.out.open("a", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    if bad_json:
        print(f"  warn: {bad_json} unparseable journal line(s) skipped (source defect)",
              file=sys.stderr)
    if bad_existing:
        print(f"  warn: {bad_existing} unparseable line(s) in the existing ledger "
              f"(corruption)", file=sys.stderr)
    print(f"{len(rows)} slot event(s) recorded")
    return 0


if __name__ == "__main__":
    sys.exit(main())
