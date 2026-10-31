#!/usr/bin/env python3
"""Gemma autonomous decision-making: full context, structured decision, execute."""
import sys
import json
import subprocess
from pathlib import Path
from datetime import datetime

repo = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(repo / "projects" / "local-inference"))
try:
    import settings
except ImportError:
    settings = None

# DERIVED, NOT PICKED. 2026-09-14. This was --max-time 120 against a 2000-token budget, and at
# the ~4 tok/s floor this box decodes at, 2000 tokens needs ~500s -- so curl killed the
# supervisor mid-answer every time it had anything to say. Fixing the GET bug and the
# num_predict=100 bug would both have been wasted while this cap stood: three faults on one
# path, any one of them fatal. settings.timeout_for() derives it from the measured rate.
try:
    _TIMEOUT = settings.timeout_for(settings.load()["supervisor"]["num_predict"])
except Exception:                                                   # noqa: BLE001
    # A missing profile must not silently restore a fatal 60s cap.
    _TIMEOUT = 1200

def get_q16_status():
    """Q16's current state: age, note, attempt number."""
    try:
        r = subprocess.run(["python3", "scripts/heartbeat.py", "status", "--json"],
                          capture_output=True, text=True, timeout=5)
        beats = json.loads(r.stdout)
        for b in beats:
            if b["name"] == "q16":
                return {
                    "age_s": b.get("age_s", 9999),
                    "state": b.get("state", "unknown"),
                    "note": b.get("note", ""),
                }
    except Exception as e:
        print(f"  NOTE: heartbeat read failed ({e})", file=sys.stderr)
    return {"age_s": 9999, "state": "unknown", "note": ""}

def get_current_chunk():
    """What chunk is Q16 working on?"""
    try:
        log_file = Path("workspace/run-logs/autonomy.log")
        if log_file.exists():
            for line in log_file.read_text().split('\n')[::-1]:
                if "starting" in line:
                    # Parse "[HH:MM] p0X-name: starting"
                    parts = line.split()
                    for part in parts:
                        if part.startswith("p0"):
                            return part.rstrip(":")
    except Exception as e:
        print(f"  NOTE: autonomy.log unreadable ({e}) -- current chunk unknown", file=sys.stderr)
    return None

def get_attempt_log(chunk):
    """Last 50 lines of current attempt."""
    try:
        # Find the most recent log for this chunk
        logs = sorted(Path("workspace/run-logs").glob(f"{chunk}-*.log"), reverse=True)
        if logs:
            lines = logs[0].read_text().split('\n')[-50:]
            return "\n".join(lines)
    except Exception as e:
        print(f"  NOTE: chunk log unreadable ({e})", file=sys.stderr)
    return ""

def is_known_failure(attempt_log):
    """Check if this is a declared/known failure."""
    try:
        ceiling = json.loads(Path("workspace/test-failures-ceiling.json").read_text())
        for test_name in ceiling.get("_oracles_awaiting_their_fix", {}).keys():
            if test_name in attempt_log:
                return test_name
    except Exception as e:
        print(f"  NOTE: failure ceiling unreadable ({e})", file=sys.stderr)
    return None

def get_brief_for_chunk(chunk):
    """The brief Q16 was given for this chunk."""
    try:
        brief_file = Path("workspace/briefs") / f"{chunk}.md"
        if brief_file.exists():
            return brief_file.read_text()          # whole: a cut brief is a different brief
    except Exception as e:
        print(f"  NOTE: brief unreadable ({e})", file=sys.stderr)
    return ""

def gather_context():
    """Assemble everything Gemma needs to decide."""
    status = get_q16_status()
    chunk = get_current_chunk()
    attempt_log = get_attempt_log(chunk) if chunk else ""
    known = is_known_failure(attempt_log)
    brief = get_brief_for_chunk(chunk) if chunk else ""

    return {
        "stale_s": status["age_s"],
        "state": status["state"],
        "chunk": chunk,
        "note": status["note"],
        "known_failure": known,
        "brief_excerpt": brief,
        "attempt_log_tail": attempt_log,
    }

def ask_gemma(context):
    """Call Gemma with full context. Return decision + reasoning."""
    prompt = f"""Q16 is stuck. Full context:

**Current chunk:** {context['chunk']}
**Stale for:** {context['stale_s']}s
**Last note:** {context['note']}
**Known failure:** {context['known_failure'] or 'no'}

**Brief (first 500 chars):**
{context['brief_excerpt'][:500]}

**Last 50 lines of attempt:**
{context['attempt_log_tail'][-2000:]}

**Your decision.** Choose one and EXPLAIN WHY:
1. SPLIT — the chunk is too big, break it into smaller pieces
2. REWRITE-BRIEF — the brief is unclear or has conflicting requirements
3. FIX-HARNESS — this looks like a tooling/test/fixture problem
4. SKIP — abandon this chunk for now, try the next one
5. RESTART — try the same chunk again with a fresh Q16

Format your response as JSON:
{{
  "decision": "SPLIT|REWRITE-BRIEF|FIX-HARNESS|SKIP|RESTART",
  "reasoning": "why this decision",
  "action": "if SPLIT, the split plan; if REWRITE-BRIEF, new brief excerpt; if FIX-HARNESS, what to fix; else null"
}}"""

    messages = [{"role": "user", "content": prompt}]

    if settings:
        body = settings.request("supervisor", "gemma4:26b", messages)
    else:
        body = {
            "model": "gemma4:26b", "stream": False, "think": "low",
            "keep_alive": "24h", "messages": messages
        }
        body["options"] = {"num_predict": 2000}  # 100/500 risk an empty answer with thinking on (2026-09-14)

    try:
        r = subprocess.run(["curl", "-s", "-X", "POST", "-H", "Content-Type: application/json",
                        "--data-binary", "@-", "--max-time", str(_TIMEOUT),
                           "http://localhost:11434/api/chat"],
                          input=json.dumps(body), capture_output=True, text=True, timeout=_TIMEOUT + 5)
        resp = json.loads(r.stdout)
        if resp.get("done_reason") == "length":   # t238: a cut-off reply is said, not misread
            print(f"  NOTE: reply hit its num_predict limit (done_reason=length) -- it is cut off",
                  file=sys.stderr)
        content = resp.get("message", {}).get("content", "")

        # Extract JSON from response
        try:
            decision = json.loads(content)
            return decision
        except json.JSONDecodeError:
            # Try to find JSON in the response
            start = content.find("{")
            end = content.rfind("}") + 1
            if start >= 0 and end > start:
                decision = json.loads(content[start:end])
                return decision
    except Exception as e:
        # NEVER SWALLOW THIS AGAIN. 2026-09-14: `except Exception: pass` hid a malformed
        # request for the entire life of this script. curl was given the body on stdin with
        # no -d/--data-binary, so it sent a GET; ollama answered "405 method not allowed";
        # json.loads threw; this handler returned None; and the caller's own silent `return`
        # wrote an empty "=== <chunk> ===" header to gemma-advice.log. Two days of runs and
        # 1385 heartbeats reported healthy while the supervisor had never once been asked a
        # question it could answer. A decision of None and a decision that FAILED must not
        # look the same -- that is the whole lesson of L-105 applied to a response body.
        print(f"gemma-decide: ask failed: {type(e).__name__}: {e}", file=sys.stderr)
        print(f"  first 200 bytes of the reply: {(locals().get('r').stdout[:200] if locals().get('r') else '<no reply>')!r}",
              file=sys.stderr)
        return None
    print(f"gemma-decide: reply carried no JSON decision: {content[:200]!r}", file=sys.stderr)

    return None

def log_decision(decision, context):
    """Record the decision for reconstruction."""
    log_entry = {
        "timestamp": datetime.now().isoformat(),
        "chunk": context["chunk"],
        "decision": decision.get("decision"),
        "reasoning": decision.get("reasoning"),
    }

    try:
        log_file = Path("workspace/run-logs/autonomy.log")
        with open(log_file, "a") as f:
            f.write(f"[{datetime.now().strftime('%H:%M')}] GEMMA: {decision['decision']} — {decision['reasoning'][:60]}\n")
    except Exception as e:
        print(f"  NOTE: decision not logged ({e})", file=sys.stderr)

def main():
    context = gather_context()

    # Only decide if Q16 is actually stale
    if context["stale_s"] <= 900:
        return

    decision = ask_gemma(context)
    if not decision:
        return

    log_decision(decision, context)

    # Write decision to file for the loop to read
    decision_file = Path("workspace/run-logs/gemma-decision.json")
    with open(decision_file, "w") as f:
        json.dump(decision, f)

if __name__ == "__main__":
    main()
