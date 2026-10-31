#!/usr/bin/env python3
"""t218: the team KPI -- bugs going in, per model, per week, from Claude's merge-review verdicts.

Jas, 2026-09-04: "track bugs going in and give models a score ... a KPI to confirm we are getting
better as a team." RED on main: scripts/team_kpi.py has no kpi()/render(). GREEN when kpi(rows)
turns workspace/review-verdicts.jsonl rows into per (model, ISO week) counts and a score, and
render() prints them. Pure functions on fixture rows; nothing live is read.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import team_kpi as tk  # noqa: E402

FAILS = []


def ck(label, got, want):
    ok = got == want
    print(f"  [{'ok' if ok else 'BAD'}]  {label}: {got!r}" + ("" if ok else f"  (want {want!r})"))
    if not ok:
        FAILS.append(label)


def v(at, branch, verdict, served="gemma4:26b", repaired=None, **k):
    return dict(at=at, branch=branch, verdict=verdict, served=served, repaired=repaired, **k)


ROWS = [
    v("2026-09-22T10:00:00Z", "b1", "kept"),
    v("2026-09-23T10:00:00Z", "b2", "kept"),
    v("2026-09-24T10:00:00Z", "b3", "kept", repaired="fixed an import"),
    v("2026-09-25T10:00:00Z", "b4", "sent-back"),
    v("2026-09-25T11:00:00Z", "b5", "superseded"),                      # not a delivery
    v("2026-09-25T12:00:00Z", "b6", "sent-back"),                      # re-reviewed later ...
    v("2026-09-26T12:00:00Z", "b6", "kept"),                           # ... the latest verdict counts
    {"at": "2026-09-22T10:00:00Z", "branch": "b2", "verdict": "kept", "escaped": True, "why": "x"},
    v("2026-09-30T10:00:00Z", "b7", "kept", served="qwen3.8:27b-mtp-q4_K_M"),
    v("2026-09-30T11:00:00Z", "b8", "blocked", served=None),
    "not a dict",
]


def main():
    if not (hasattr(tk, "kpi") and hasattr(tk, "render")):
        print("  [BAD]  team_kpi has no kpi()/render(): the missing change")
        return 1
    s = tk.kpi(ROWS)
    g = s.get(("gemma4:26b", "2026-W39"), {})
    ck("Gemma, week 39: delivered = kept + sent-back + blocked, superseded excluded", g.get("delivered"), 5)
    ck("  the latest verdict per branch wins (b6 sent back, then kept)", (g.get("sent_back"), g.get("clean")), (1, 2))
    ck("  a repaired keep is counted apart from a clean one", g.get("repaired"), 1)
    ck("  an escape turns its clean keep into an escape (joined by branch)", g.get("escaped"), 1)
    ck("  score = clean / delivered, as a whole percent", g.get("score_pct"), 40)
    ck("Q4, week 40: one clean keep", (s.get(("qwen3.8:27b-mtp-q4_K_M", "2026-W40"), {}).get("clean"),
                                       s.get(("qwen3.8:27b-mtp-q4_K_M", "2026-W40"), {}).get("score_pct")), (1, 100))
    ck("a row with no model is counted under 'unknown', not dropped",
       s.get(("unknown", "2026-W40"), {}).get("blocked"), 1)
    ck("a malformed row is counted, not dropped silently", tk.kpi(ROWS, with_skipped=True)[1], 1)
    out = tk.render(s)
    lines = [l for l in out.splitlines() if "gemma4:26b" in l and "2026-W39" in l]
    ck("render prints one line per model-week with its score", (len(lines), "40%" in (lines or [""])[0]), (1, True))
    ck("an empty ledger renders a plain line, not an error", "no verdicts" in tk.render(tk.kpi([])).lower(), True)
    print("test_team_kpi:", "FAILED" if FAILS else "ok")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
