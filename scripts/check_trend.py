#!/usr/bin/env python3
"""Are we getting better or worse? The workspace check's history, as a trend (t104).

Jas, 2026-08-24: "Have a reporting method to get results to me. KPI is a close analogy."
check_workspace.py appends one row per run to workspace/check-history.jsonl; this reads it.

    python3 scripts/check_trend.py            # per-day totals + every check not passing, and since when
    python3 scripts/check_trend.py --days 14
"""
import argparse
import json
import sys
from collections import OrderedDict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
HISTORY = REPO / "workspace" / "check-history.jsonl"


def rows(path=HISTORY):
    out = []
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            out.append(json.loads(line))
        except ValueError as e:
            print(f"  NOTE: unreadable history row ({e}); skipped", file=sys.stderr)
    return out


def by_day(rs):
    """{day: (runs, worst failed, last warnings)} -- the day's shape at a glance."""
    days = OrderedDict()
    for r in rs:
        d = r["at"][:10]
        runs, worst, _ = days.get(d, (0, 0, 0))
        days[d] = (runs + 1, max(worst, r.get("failed", 0)), r.get("warnings", 0))
    return days


def _key(name):
    """A check's name without its trailing count: "(6 without)" -> "", so a WARN whose number moves
    keeps one streak instead of starting a new one each time the number changes."""
    import re
    return re.sub(r"\s*\(\d[^)]*\)\s*$", "", name)


def streaks(rs):
    """{check: (status, since, runs)} for every check that is not passing in the LATEST run: how long it
    has been in that state, counted back over consecutive runs."""
    if not rs:
        return {}
    last = rs[-1]
    out = {}
    for status, key in (("FAIL", "failing"), ("WARN", "warning")):
        for name in last.get(key, []):
            since, n = last["at"], 0
            for r in reversed(rs):
                if _key(name) in {_key(x) for x in r.get(key, [])}:
                    since, n = r["at"], n + 1
                else:
                    break
            out[name] = (status, since, n)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--days", type=int, default=7)
    a = ap.parse_args(argv)
    rs = rows()
    if not rs:
        print("  no check history yet -- check_workspace.py writes a row on every run")
        return 0
    print(f"  check history: {len(rs)} run(s) since {rs[0]['at']}")
    print(f"  {'day':10} {'runs':>4} {'worst failed':>12} {'warnings at end':>15}")
    for d, (n, worst, warns) in list(by_day(rs).items())[-a.days:]:
        print(f"  {d:10} {n:>4} {worst:>12} {warns:>15}")
    s = streaks(rs)
    if s:
        print("\n  not passing now, and for how long:")
        for name, (status, since, n) in sorted(s.items(), key=lambda x: (x[1][0] != "FAIL", x[1][1])):
            print(f"    [{status}] {name[:70]:70} since {since} ({n} run(s))")
    else:
        print("\n  every check passed in the latest run")
    return 0


if __name__ == "__main__":
    sys.exit(main())
