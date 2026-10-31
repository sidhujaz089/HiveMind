#!/usr/bin/env python3
"""t362's oracle: the slot ledger records who held the slot, when, and for how long.

    python3 scripts/tests/test_slot_events.py

WHY THIS EXISTS. 2026-09-19, 10:47: an interactive dsh session died of starvation because
a 3.5-hour model loop held the single model slot, and NOTHING on the box records slot
hold/release (the crash is only reconstructable from the dead session's own transcript;
see the skill docs/skills/root-cause-analysis and workspace/feedback/
2026-09-19--dsh-harness--rca-4afba784-corrections.md). This oracle is the decay test for the recorder: it FAILS
while the writer is absent, and passes only when structured rows are captured from the
live journal. The writer is scripts/slot_events.py (contract below lives in the test
assertions -- a model implementing it must not edit this file).

ROW CONTRACT (each JSONL line, one object):
- at_epoch  float seconds (required, every row)
- kind      one of "acquire" | "release" | "idle" | "collector-failed"
- slot      int for acquire/release (journal "id N"); null for idle; absent ok on failed
- task      int for acquire/release (journal "task N"); null for idle
- held_s    release rows only: float >= 0 (release time minus that task's last acquire
            seen in the ledger), or null when the acquire is not in the file
- resident  list[str] of model names from GET /api/ps (key "models" -> "name"), or null
            when the HTTP call fails; absent ok on collector-failed
- message   collector-failed rows only: str, the stderr of the failed journal read

ANCHORS in the ollama unit journal MESSAGE (all three verified present, 2026-09-19:
1654 acquire lines, 812 release lines, 922 idle lines inside the 2-day window):
- acquire: "new prompt, n_ctx_slot"  or  "processing task"
          e.g.  slot   operator(): id  0 | task 347 | new prompt, n_ctx_slot = 262144, ...
          e.g.  slot launch_slot_: id  0 | task 347 | processing task, is_child = 0
- release: "release: id  0 | task 347 | stop processing"
- idle:    "update_slots: all slots are idle"

ISOLATION. The test runs the writer twice against temporary --cursor/--out paths, so the
production ledger under workspace/run-logs/ is never touched. No model is called; the
journal and /api/ps are the two data sources (both verified live on 2026-09-19).

Agent: dsh-harness/qwen3.8-27b-mtp-q4_K_M
"""
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
WRITER = REPO / "scripts" / "slot_events.py"
KINDS = {"acquire", "release", "idle", "collector-failed"}


def run(cursor: Path, out: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        # --since -1h: the last hour has ample slot traffic and reads in seconds; the default two
        # days took ~2 min and timed out under load (2026-10-08, every build held on it).
        [sys.executable, str(WRITER), "--cursor", str(cursor), "--out", str(out), "--since", "-1h"],
        capture_output=True, text=True, timeout=180,
    )


def rows(p: Path) -> list:
    if not p.exists():
        return []
    return [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]


def key(r: dict):
    return (r.get("at_epoch"), r.get("kind"), r.get("slot"), r.get("task"))


def _has_traffic():
    r = subprocess.run(["journalctl", "-u", "ollama", "--since", "-1h", "--no-pager", "-o", "cat"],
                       capture_output=True, text=True, timeout=60)
    return "stop processing" in r.stdout and "n_ctx_slot" in r.stdout


def _make_traffic():
    import urllib.request
    body = {"model": "qwen3-embedding:0.6b", "input": ["slot events test"], "keep_alive": 0,
            "options": {"num_ctx": 512}}
    try:
        req = urllib.request.Request("http://127.0.0.1:11434/api/embed", json.dumps(body).encode(),
                                     {"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=120) as f:
            f.read()
        return None
    except Exception as e:                                          # noqa: BLE001 -- returned and said
        return f"{type(e).__name__}: {e}"


def main() -> int:
    # DECAY: with the writer absent this suite must not pass.
    if not WRITER.exists():
        print("RED: scripts/slot_events.py does not exist (the writer is absent)")
        return 1

    # PRESENCE BEFORE ABSENCE (L-050, 2026-10-13). This suite read "no rows" as a broken collector,
    # but since the embedder moved to the ThinkPad the GX10's Ollama can go hours without a request,
    # so an empty hour held every build "red on main". When the window has no slot traffic, make one
    # request -- a tiny embed, 512 tokens of context, unloaded at once -- so there is something to capture.
    if not _has_traffic():
        err = _make_traffic()
        if err:
            print(f"RED: no slot traffic in the last hour, and none could be made: {err}")
            return 1
        time.sleep(2)                                   # journald flush

    with tempfile.TemporaryDirectory() as tmp:
        cur = Path(tmp) / "cursor"
        out = Path(tmp) / "slot-ledger.jsonl"

        r1 = run(cur, out)
        if r1.returncode != 0:
            print(f"RED: first run exited {r1.returncode}\n{r1.stderr[-2000:]}")
            return 1
        s1 = rows(out)
        if len(s1) < 1:
            print("RED: no rows captured, although the live journal has slot traffic (1654/"
                  "812/922 acquire/release/idle lines in the 2-day window, 2026-09-19)")
            return 1

        kinds = {r.get("kind") for r in s1}
        if not kinds <= KINDS:
            print(f"RED: unknown kind(s) {kinds - KINDS}")
            return 1
        if not ({"acquire", "release"} <= kinds):
            print(f"RED: expected both acquire and release rows, got {sorted(k for k in kinds if k)}")
            return 1

        for r in s1:
            if not isinstance(r.get("at_epoch"), (int, float)) or isinstance(r.get("at_epoch"), bool):
                print(f"RED: at_epoch missing or not a number: {r}")
                return 1
            if r.get("kind") in ("acquire", "release"):
                if not isinstance(r.get("slot"), int) or not isinstance(r.get("task"), int):
                    print(f"RED: {r.get('kind')} row without integer slot and task: {r}")
                    return 1
                res = r.get("resident", None)
                if res is not None and (not isinstance(res, list) or
                                       not all(isinstance(n, str) for n in res)):
                    print(f"RED: resident must be list[str] or null: {r}")
                    return 1
            if r.get("kind") == "release":
                held = r.get("held_s", "MISSING")
                if "held_s" not in r:
                    print(f"RED: release row without held_s: {r}")
                    return 1
                if held is not None and (not isinstance(held, (int, float)) or held < 0):
                    print(f"RED: held_s must be float >= 0 or null: {r}")
                    return 1
            if r.get("kind") == "collector-failed" and not isinstance(r.get("message"), str):
                print(f"RED: collector-failed row without message: {r}")
                return 1

        n1 = len(s1)
        r2 = run(cur, out)
        if r2.returncode != 0:
            print(f"RED: second run exited {r2.returncode}\n{r2.stderr[-2000:]}")
            return 1
        s2 = rows(out)
        new = s2[n1:]
        seen = {key(r) for r in s1}
        if any(key(r) in seen for r in new):
            print("RED: cursor not honoured -- rows from the first run were re-recorded")
            return 1

    print(f"test_slot_events: GREEN ({len(s1)} rows captured, "
          f"{sum(1 for r in s1 if r['kind'] == 'acquire')} acquire(s), "
          f"{sum(1 for r in s1 if r['kind'] == 'release')} release(s))")
    return 0


if __name__ == "__main__":
    sys.exit(main())
