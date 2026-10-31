#!/usr/bin/env python3
"""Close-outs record WHERE their time went, so the gate's timeout can be derived instead of guessed (t9580, Jas 2026-10-31).

Jas: *"I want session ends to generate data that we can see on a graph. If we see outliers that is the point we target
our CI."*

The 1800 s in the pre-commit hook is an interim guess. A guess is only better than the 900 it replaced because it says
it is one; the number that replaces it has to come from measurements. This records one row per close-out: the whole
run, the invariant check, and each section, so a slow close-out can be pointed at instead of argued about.

RED ON MAIN: session_end has no timing ledger and no summariser.
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(HERE / "scripts"))
import close_out_timings as ct      # noqa: E402

fails = []


def check(name, ok, detail=""):
    print(f"  {'ok ' if ok else 'FAIL'}  {name}" + ("" if ok else f"  ({detail})"))
    if not ok:
        fails.append(name)


rows = [
    {"at": "2026-10-31T02:00:00Z", "total_s": 2400, "check_s": 1800,
     "sections": {"2. will the next session know": 3.0, "4. full workspace check": 1800.0}},
    {"at": "2026-10-31T03:00:00Z", "total_s": 60, "check_s": 40,
     "sections": {"2. will the next session know": 2.0, "4. full workspace check": 40.0}},
    {"at": "2026-10-31T04:00:00Z", "total_s": 45, "check_s": 30,
     "sections": {"2. will the next session know": 1.0, "4. full workspace check": 30.0}},
]

stats = ct.summarise(rows)
check("the whole run is summarised", "total_s" in stats, str(list(stats)[:4]))
check("  with a median and a worst case", stats["total_s"]["p50"] > 0 and stats["total_s"]["max"] == 2400,
      str(stats["total_s"]))
check("the invariant check is summarised separately", "check_s" in stats and stats["check_s"]["max"] == 1800,
      str(stats.get("check_s")))
check("each section is summarised", "2. will the next session know" in stats, str(list(stats)[:6]))

outliers = ct.outliers(stats, factor=2.0)
check("an outlier is named when one run is far slower", any("1800" in str(v) or "2400" in str(v)
      for v in outliers.values()), str(outliers)[:160])
check("  and a tidy set of runs has none", ct.outliers(ct.summarise(rows[1:]), factor=2.0) == {},
      str(ct.outliers(ct.summarise(rows[1:]))))

check("it can read a ledger that does not exist yet", ct.load("/tmp/definitely-not-a-ledger.jsonl") == [])
check("  and skips a corrupt row rather than dying", ct.parse('{"total_s": 5}\nnot json\n{"total_s": 7}\n') ==
      [{"total_s": 5}, {"total_s": 7}], str(ct.parse('{"total_s": 5}\nnot json\n{"total_s": 7}\n')))

src = (HERE / "scripts" / "session_end.py").read_text(encoding="utf-8")
check("session_end records the rows", "close_out_timings" in src, "nothing writes the ledger")
check("  and times each section", "section(" in src and "_SECTION_T0" in src or "timings" in src.lower(),
      "the section hook is missing")

print("test_close_out_timings:", "FAILED" if fails else "ok")
sys.exit(1 if fails else 0)
