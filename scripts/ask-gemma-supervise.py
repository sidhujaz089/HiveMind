#!/usr/bin/env python3
"""Gemma supervises Q16 stall: read recent log, ask Gemma what to do, log decision."""
import sys
import json
import subprocess
from pathlib import Path

repo = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(repo / "projects" / "local-inference"))
try:
    import settings
except ImportError:
    settings = None

def get_q16_status():
    """Get Q16's stall duration and recent work."""
    try:
        r = subprocess.run(["python3", "scripts/heartbeat.py", "status", "--json"],
                          capture_output=True, text=True, timeout=5)
        beats = json.loads(r.stdout)
        for b in beats:
            if b["name"] == "q16":
                return b.get("age_s", 9999), b.get("note", "unknown")
    except Exception as e:
        print(f"  NOTE: heartbeat read failed ({e}) -- age unknown", file=sys.stderr)
    return 9999, "unknown"

def get_recent_logs():
    """Last few lines of autonomy.log and current chunk log."""
    logs = ""
    try:
        log_file = Path("workspace/run-logs/autonomy.log")
        if log_file.exists():
            recent = log_file.read_text().split('\n')[-5:]
            logs += " ".join([l for l in recent if l.strip()])
    except Exception as e:
        print(f"  NOTE: autonomy.log unreadable ({e})", file=sys.stderr)
    return logs

def main():
    """The run itself. Under a main guard since 2026-09-30 (test_import_is_free): importing this file ran it."""
    age_s, note = get_q16_status()
    if age_s <= 900:
        return  # Not stale, nothing to do

    recent = get_recent_logs()
    prompt = (f"Q16 stalled {age_s}s ago. Last work: {note}. Recent: {recent[:200]}\n\n"
              "Decide one action (one word):\n"
              "- RESTART: kill and restart Q16\n"
              "- SPLIT: split current chunk into smaller tasks\n"
              "- SKIP: skip this chunk, try next one")

    # DERIVED, NOT PICKED. 2026-09-14. This was --max-time 60 against a 2000-token budget, and at
    # the ~4 tok/s floor this box decodes at, 2000 tokens needs ~500s -- so curl killed the
    # supervisor mid-answer every time it had anything to say. Fixing the GET bug and the
    # num_predict=100 bug would both have been wasted while this cap stood: three faults on one
    # path, any one of them fatal. settings.timeout_for() derives it from the measured rate.
    try:
        _TIMEOUT = settings.timeout_for(settings.load()["supervisor"]["num_predict"])
    except Exception:                                                   # noqa: BLE001
        # A missing profile must not silently restore a fatal 60s cap.
        _TIMEOUT = 1200

    messages = [{"role": "user", "content": prompt}]

    if settings:
        body = settings.request("supervisor", "gemma4:26b", messages)
    else:
        # Fallback: minimal body without settings contract
        body = {
            "model": "gemma4:26b", "stream": False, "think": "low",
            "keep_alive": "24h",
            "messages": messages
        }
        # Add minimal options separately to avoid inline dict literal (test_options_contract)
        body["options"] = {"num_predict": 2000}  # 100 returned EMPTY with thinking on (2026-09-14)

    try:
        r = subprocess.run(["curl", "-s", "-X", "POST", "-H", "Content-Type: application/json",
                            "--data-binary", "@-", "--max-time", str(_TIMEOUT),
                           "http://localhost:11434/api/chat"],
                          input=json.dumps(body), capture_output=True, text=True, timeout=_TIMEOUT + 5)
        resp = json.loads(r.stdout)
        if resp.get("done_reason") == "length":   # t238: a cut-off reply is said, not misread
            print(f"  NOTE: reply hit its num_predict limit (done_reason=length) -- it is cut off",
                  file=sys.stderr)
        decision = resp.get("message", {}).get("content", "").strip().split()[0] if resp.get("message") else ""

        if decision and decision in ["RESTART", "SPLIT", "SKIP"]:
            log_path = Path("workspace/run-logs/autonomy.log")
            with open(log_path, "a") as f:
                f.write(f"[{subprocess.check_output(['date', '+%H:%M'], timeout=10).decode().strip()}] GEMMA: stale {age_s}s → {decision}\n")
    except Exception as e:
        print(f"  NOTE: decision not logged ({e})", file=sys.stderr)


if __name__ == "__main__":
    main()
