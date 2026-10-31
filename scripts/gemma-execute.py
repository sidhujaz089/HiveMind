#!/usr/bin/env python3
"""Execute Gemma's autonomous decision: kill Q16, modify plan, redispatch."""
import sys
import json
import subprocess
import os as _os
sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import roles  # noqa: E402
from pathlib import Path

def kill_q16():
    """Kill Q16 by finding its pid via heartbeat."""
    try:
        # Get Q16's heartbeat file
        heartbeat_dir = Path("workspace/run-logs/.heartbeats")
        q16_beat = heartbeat_dir / "q16"
        if q16_beat.exists():
            data = json.loads(q16_beat.read_text())
            pid = data.get("pid")
            if pid:
                subprocess.run(["kill", "-9", str(pid)], timeout=5)
                return True
    except Exception as e:
        print(f"  NOTE: Q16 kill failed ({e})", file=sys.stderr)
    return False

def get_current_chunk():
    """What chunk are we on?"""
    try:
        log_file = Path("workspace/run-logs/autonomy.log")
        if log_file.exists():
            for line in log_file.read_text().split('\n')[::-1]:
                if "starting" in line:
                    parts = line.split()
                    for part in parts:
                        if part.startswith("p0"):
                            return part.rstrip(":")
    except Exception as e:
        print(f"  NOTE: autonomy.log unreadable ({e}) -- current chunk unknown", file=sys.stderr)
    return None

def read_plan():
    """Read INFRA-PLAN.md as structured data."""
    try:
        plan_file = Path("workspace/plan/INFRA-PLAN.md")
        return plan_file.read_text()
    except Exception:
        return ""

def split_chunk(chunk):
    """Split a chunk into two smaller subtasks. Write back to plan."""
    plan = read_plan()
    # Find the chunk's section
    if f"## {chunk}" not in plan:
        return False

    # This is a placeholder — actual split would be domain-specific
    # For now, just log that it should be split
    try:
        log_file = Path("workspace/run-logs/autonomy.log")
        with open(log_file, "a") as f:
            f.write(f"[GEMMA] SPLIT {chunk}: requires manual chunk decomposition\n")
        return True
    except Exception:
        return False

def redispatch(chunk, new_brief=None):
    """Re-dispatch Q16 on the chunk, optionally with a new brief."""
    brief_file = Path("workspace/briefs") / f"{chunk}.md"
    if not brief_file.exists():
        return False

    brief = new_brief or brief_file.read_text()

    # Extract files to touch from chunk name
    files = ""
    if "errorscan" in chunk.lower():
        files = "scripts/error_scan.py"
    elif "siggate" in chunk.lower():
        files = "scripts/signature_gate.py"
    # ... more patterns

    try:
        cmd = [
            "python3", "projects/local-inference/coder.py",
            "--brief", brief,
            "--files", files,
            "--target", f"scripts/tests/test_{chunk.split('-')[1]}.py",
            "--model", roles.for_job("building", "gemma4:26b"),      # the role's model (t410)
            "--apply"
        ]
        subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        return True
    except Exception:
        return False

def skip_chunk(chunk):
    """Skip to the next chunk. Log why."""
    try:
        log_file = Path("workspace/run-logs/autonomy.log")
        with open(log_file, "a") as f:
            f.write(f"[GEMMA] SKIP {chunk}: will retry later\n")
        return True
    except Exception:
        return False

def execute_decision(decision):
    """Act on Gemma's decision."""
    if not decision:
        return

    action = decision.get("decision")
    chunk = get_current_chunk()

    if action == "RESTART":
        kill_q16()
        redispatch(chunk)
    elif action == "SPLIT":
        split_chunk(chunk)
    elif action == "REWRITE-BRIEF":
        new_brief = decision.get("action")
        if new_brief:
            Path(f"workspace/briefs/{chunk}.md").write_text(new_brief)
        redispatch(chunk, new_brief)
    elif action == "FIX-HARNESS":
        # Log the harness fix needed; actual fix requires human or deep inspection
        log_file = Path("workspace/run-logs/autonomy.log")
        with open(log_file, "a") as f:
            f.write(f"[GEMMA] HARNESS FIX NEEDED on {chunk}: {decision.get('action')}\n")
    elif action == "SKIP":
        skip_chunk(chunk)

def main():
    try:
        decision_file = Path("workspace/run-logs/gemma-decision.json")
        if decision_file.exists():
            decision = json.loads(decision_file.read_text())
            execute_decision(decision)
    except Exception as e:
        print(f"  NOTE: decision not executed ({e})", file=sys.stderr)

if __name__ == "__main__":
    main()
