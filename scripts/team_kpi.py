#!/usr/bin/env python3
"""The team KPI (t218): bugs going in, per model, per week -- from Claude's merge-review verdicts.

Jas, 2026-09-04: "We can track bugs going in and give models a score. As we improve our prompting
and scoping we can use this as a KPI to confirm we are getting better as a team."

    python3 scripts/team_kpi.py            # one line per model and ISO week, with its score

Source: workspace/review-verdicts.jsonl (written by scripts/reviewq.py verdict / escaped).
Contract and oracle: scripts/tests/test_team_kpi.py. kpi() and render() are built from that scope.
"""
import datetime
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
VERDICTS = REPO / "workspace" / "review-verdicts.jsonl"


def _week(at):
    d = datetime.date.fromisoformat(str(at)[:10])
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def kpi(rows, with_skipped=False):
    """{(model, ISO week): counts and score_pct} from verdict rows. The LAST verdict per branch
    counts (a re-review replaces the earlier one); an escape row marks its branch and is not
    itself a verdict; a superseded branch was never delivered. Unusable rows are counted."""
    skipped, escaped, last = 0, set(), {}
    for r in rows:
        if not isinstance(r, dict) or "verdict" not in r:
            skipped += 1
            continue
        if r.get("escaped"):
            escaped.add(r.get("branch"))
            continue
        last[r.get("branch")] = r
    stats = {}
    for b, r in last.items():
        if r["verdict"] == "superseded":
            continue
        try:
            key = (r.get("served") or "unknown", _week(r.get("at")))
        except ValueError:
            skipped += 1
            continue
        e = stats.setdefault(key, {"delivered": 0, "clean": 0, "repaired": 0, "sent_back": 0,
                                   "blocked": 0, "escaped": 0})
        e["delivered"] += 1
        if r["verdict"] == "kept":
            e["escaped" if b in escaped else "repaired" if r.get("repaired") else "clean"] += 1
        elif r["verdict"] == "sent-back":
            e["sent_back"] += 1
        elif r["verdict"] == "blocked":
            e["blocked"] += 1
    for e in stats.values():
        e["score_pct"] = round(100 * e["clean"] / e["delivered"]) if e["delivered"] else 0
    return (stats, skipped) if with_skipped else stats


def render(stats):
    if not stats:
        return "No verdicts yet."
    out = []
    for (model, week), e in sorted(stats.items()):
        out.append(f"{model:<28} {week}  delivered {e['delivered']:>3}  clean {e['clean']:>3}  "
                   f"repaired {e['repaired']:>3}  sent back {e['sent_back']:>3}  "
                   f"escaped {e['escaped']:>2}  score {e['score_pct']}%")
    return "\n".join(out)


if __name__ == "__main__":
    rows = []
    for line in (VERDICTS.read_text(encoding="utf-8", errors="replace").splitlines()
                 if VERDICTS.is_file() else []):
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except ValueError:
            rows.append(line)            # counted as skipped by kpi(), never dropped silently
    stats, skipped = kpi(rows, with_skipped=True)
    print(render(stats))
    if skipped:
        print(f"({skipped} row(s) skipped: not a verdict)")
    sys.exit(0)
