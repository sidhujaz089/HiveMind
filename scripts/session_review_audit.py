#!/usr/bin/env python3
"""The routine audit of the session-review script: score it on the gold set, keep the score, say when it slips.

    python3 scripts/session_review_audit.py run      # score now, record it, file what it finds
    python3 scripts/session_review_audit.py tick     # the same, only if due (the dispatcher calls this)
    python3 scripts/session_review_audit.py report   # the recorded scores

Jas, 2026-10-07: "My hope is that Swift is highly capable of routinely auditing the script that reviews
and can improve it" -- and later, "I thought ... a script was [already reviewing sessions]. We talked
about it routinely auditing the script." Session review runs live (dispatch kinds session-review, rca,
on Swift); nothing measured the script itself after a change. This does, with code only:

  SCORE: the scripted review (session_review_trial.scripted_rows -- the code the live pipeline runs
  before any model) over the hand-graded audit sessions (workspace/session-reviews/trials/audit/,
  20 sessions on 2026-10-07, 86 items, 10 real misses), graded as of 2026-10-04 so later tasks cannot cover the
  answers. No model call; ~40 s.

  DUE: once a day, or at once when session_candidates.py / session_review_trial.py / session_review.py
  change (their hash is in every row) -- a change to the script is exactly when it can regress.

  FILE, never fan out (one open task per kind, the backlog-must-shrink rule):
    - a REGRESSION (fewer real misses surfaced, or fewer items right, than the last score): one task,
      top of the queue, with both scores.
    (The weekly audit task that was filed here is replaced by script-audit.timer and
    script_audit.py run -- t790 step 5, 2026-10-13.)

Writes workspace/session-reviews/audit-scores.jsonl.
"""
import hashlib
import json
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "projects" / "app-repo"))
GOLD = REPO / "workspace" / "session-reviews" / "trials" / "audit"
SCORES = REPO / "workspace" / "session-reviews" / "audit-scores.jsonl"
AS_OF = "2026-10-04T000000Z"
WATCHED = ("scripts/session_candidates.py", "scripts/session_review_trial.py", "scripts/session_review.py")
DAY, WEEK = 86400, 7 * 86400
REGRESSION_TITLE = "Session-review script regressed on the audit set"


def code_hash():
    h = hashlib.sha256()
    for rel in WATCHED:
        h.update((REPO / rel).read_bytes())
    return h.hexdigest()[:12]


def score():
    """Totals and per-session rows for the scripted review on every gold file."""
    import session_review as sr
    import session_review_trial as trial
    tot = {"sessions": 0, "candidates": 0, "flagged": 0, "found": 0, "right": 0, "items": 0,
           "real_surfaced": 0, "real": 0}
    per, errors = [], []
    for g in sorted(GOLD.glob("s*.json")):
        try:
            gold = json.loads(g.read_text(encoding="utf-8"))
            req = json.loads(trial.request_path(gold["request"]).read_text(encoding="utf-8"))
            ts = sr.turns(req["harness"], req["path"])[0:gold.get("to_turn") or None]
            rows = trial.scripted_rows(ts, AS_OF)
            gr = trial.grade(gold, rows)
        except (OSError, ValueError, KeyError) as e:
            errors.append(f"{g.name}: {type(e).__name__}: {e}")
            continue
        flagged = sum(r["verdict"] == "missed" for r in rows)
        real = [x for x in gr if x["gold"] == "yes"]
        row = {"session": g.stem, "flagged": flagged, "found": sum(x["found"] for x in gr),
               "right": sum(x["right"] for x in gr), "items": len(gr),
               "real_surfaced": sum(x["said"] == "missed" for x in real), "real": len(real)}
        per.append(row)
        tot["sessions"] += 1
        tot["candidates"] += len(rows)
        for k in ("flagged", "found", "right", "items", "real_surfaced", "real"):
            tot[k] += row[k]
    return tot, per, errors


def rows():
    try:
        return [json.loads(l) for l in SCORES.read_text(encoding="utf-8").splitlines() if l.strip()]
    except OSError:
        return []


def due(now=None, last=None, h=None):
    now = now or time.time()
    last = last if last is not None else (rows() or [None])[-1]
    if not last:
        return True, "no score yet"
    if last.get("code") != (h or code_hash()):
        return True, "the script changed since the last score"
    if now - last.get("epoch", 0) >= DAY:
        return True, "a day since the last score"
    return False, "scored today and the script is unchanged"


def _open_titles():
    import taskstore
    out = {}
    for tid in taskstore.read_queue():
        meta, _ = taskstore.find(tid)
        if meta:
            out[tid] = meta.get("title", "")
    return out


def _file(title, body, top=False):
    import taskstore
    for tid, t in _open_titles().items():
        if t.startswith(title):
            return f"open already as {tid}"
    tid = taskstore.add(title, area="workspace", body=body, source="session-review-audit", kind="build")
    if top:
        taskstore.move(tid, "top")
    return f"filed {tid}"


def _numbers(t):
    return (f"{t['sessions']} sessions: real misses surfaced {t['real_surfaced']}/{t['real']}, "
            f"items right {t['right']}/{t['items']}, MISSED flags {t['flagged']}")


def run(why="asked"):
    t0 = time.time()
    tot, per, errors = score()
    prev = (rows() or [None])[-1]
    rec = {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "epoch": int(time.time()),
           "why": why, "code": code_hash(), "as_of": AS_OF, **tot, "seconds": round(time.time() - t0),
           "errors": errors, "per_session": per, "actions": []}
    if errors:
        print("  NOTE: gold sessions not scored:\n    " + "\n    ".join(errors))
    if prev and tot["sessions"] == prev.get("sessions") and (
            tot["real_surfaced"] < prev["real_surfaced"] or tot["right"] < prev["right"]):
        rec["actions"].append(_file(REGRESSION_TITLE, (
            f"The scripted session review scored worse on the 19-session audit set than its last score.\n\n"
            f"- before ({prev['at']}, code {prev['code']}): {_numbers(prev)}\n"
            f"- now ({rec['at']}, code {rec['code']}): {_numbers(tot)}\n\n"
            f"Watched files: {', '.join(WATCHED)}. Per-session rows: {SCORES.relative_to(REPO)}. "
            f"Reproduce one session: python3 scripts/session_review_trial.py "
            f"workspace/session-reviews/trials/audit/sNN.json --scripted --as-of {AS_OF}"), top=True))
    # THE WEEKLY "Swift audits the session-review script" TASK IS GONE (t790 step 5, 2026-10-13):
    # script-audit.timer files the weekly audit now, and the Manager runs it as a loop that keeps
    # only edits that score better -- not a task to scope by hand. The daily score and the
    # regression task above stay.
    SCORES.parent.mkdir(parents=True, exist_ok=True)
    with SCORES.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")
    print(f"session-review audit: {_numbers(tot)}; {rec['seconds']}s"
          + (f"; {'; '.join(rec['actions'])}" if rec["actions"] else ""))
    return 0


def main(argv):
    cmd = argv[0] if argv else "report"
    if cmd == "run":
        return run()
    if cmd == "tick":
        ok, why = due()
        if not ok:
            print(f"session-review audit: not due ({why})")
            return 0
        return run(why)
    if cmd == "score-json":                      # one line of JSON: the totals (script_audit's sandbox check)
        tot, _per, errors = score()
        print(json.dumps({**tot, "errors": errors}))
        return 0
    if cmd == "report":
        for r in rows()[-10:]:
            print(f"{r['at']}  code {r['code']}  {_numbers(r)}  ({r['why']})"
                  + (f"  -> {'; '.join(r['actions'])}" if r.get("actions") else ""))
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
