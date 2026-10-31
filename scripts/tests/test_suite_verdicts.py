#!/usr/bin/env python3
"""The suite-verdict reader is one named instrument, and the cache's shape is checked not asserted (I1).

This exists because of a specific, repeated failure. On 2026-10-26 the same question -- "of the suites
allowed to fail, which are green?" -- was answered three ways in one session:

  * a throwaway classifier read `workspace/suite-cache.json` looking for a PASS/FAIL *word* in each
    entry's `out` field and reported 114 of 456 as green. The cache carries no word: its verdict is a
    SHAPE (an entry exists iff the suite passed; a failure carries `red` + `out`), so "no out" -- which
    means PASSED -- was read as unreadable. The 114 was invented, and retracted a round later.
  * the retraction came from reading `workspace/suite-state.jsonl`, whose rows carry `exit_code`.
  * the 102/354 split that survived came from an inline one-off that then vanished.

Three instruments, one question, two answers, nothing to point at. The fix is not more analysis: it is
ONE named reader with a test, so the next session quotes a command instead of a memory.

What this refuses:

  * reading the cache for a verdict -- the reader reads the ledger and says so in every report;
  * "no row" being counted as green (it is `unrecorded`, counted separately: a question nobody asked
    yet, not a pass);
  * an unreadable ledger reading as an empty, clean result;
  * and, for the cache itself, an ASSERTION in prose that the shape is `red` iff `out` -- the test checks
    that against the live cache instead, which is the only way the claim can be wrong out loud.
"""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
FAILS = []


def check(what, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + what + ("" if cond else "  -- " + str(detail)))
    if not cond:
        FAILS.append(what)


def main():
    import suite_verdicts as sv

    # 1. THE SPLIT, from a temp ledger and a temp ceiling.
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        ledger = d / "state.jsonl"
        rows = [
            {"suite": "a.py", "exit_code": 0, "ts": "2026-10-26T00:00:00Z"},
            {"suite": "b.py", "exit_code": 1, "ts": "2026-10-26T00:00:00Z"},
            {"suite": "b.py", "exit_code": 0, "ts": "2026-10-27T00:00:00Z"},   # newest wins: green now
            {"suite": "c.py", "exit_code": 2, "ts": "2026-10-26T00:00:00Z"},
        ]
        ledger.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
        ceiling = d / "ceiling.json"
        ceiling.write_text(json.dumps({"allowed_to_fail": ["a.py", "b.py", "c.py", "never.py"]}),
                           encoding="utf-8")
        got = sv.report(ceiling=ceiling, ledger=ledger)
        check("a suite that went green after red counts green (LATEST row wins)",
              got["green"] == ["a.py", "b.py"], got["green"])
        check("a red suite counts red", got["red"] == ["c.py"], got["red"])
        check("a suite with no row is unrecorded, NOT green", got["unrecorded"] == ["never.py"],
              got["unrecorded"])
        check("the counts add up to the ceiling",
              len(got["green"]) + len(got["red"]) + len(got["unrecorded"]) == 4, got)

        # 2. JSON is a first-class output: a caller must not have to parse prose.
        parsed = json.loads(sv.report(ceiling=ceiling, ledger=ledger, as_json=True))
        check("--json gives the same split as a structure", parsed["green"] == got["green"], parsed)

        # 3. ONE SUITE, by name.
        check("a single lookup returns the newest verdict", sv.verdict_for("b.py", ledger=ledger) == 0)
        check("an unknown suite returns None, not 0", sv.verdict_for("never.py", ledger=ledger) is None)

        # 4. AN UNREADABLE LEDGER IS NOT AN EMPTY ONE.
        got2 = sv.report(ceiling=ceiling, ledger=d / "missing.jsonl")
        check("a missing ledger reports a problem", bool(got2.get("problem")), got2)

    # 5. THE LIVE ANSWER agrees with the ceiling file it was asked about.
    live = sv.report()
    check("the live report names a ceiling", live["ceiling_count"] > 0, live["ceiling_count"])
    check("every allowed-to-fail suite is classified exactly once",
          len(live["green"]) + len(live["red"]) + len(live["unrecorded"]) == live["ceiling_count"],
          live["ceiling_count"])
    check("the report says where the verdict came from (the ledger, not the cache)",
          "suite-state.jsonl" in str(live.get("why", "")) and "suite-cache" in str(live.get("why", "")),
          live.get("why", "")[:60])

    # 6. THE CACHE'S SHAPE, checked against the cache itself rather than asserted in prose. This is the
    #    half of the fix that stops the next reader guessing.
    cache_p = ROOT / "workspace" / "suite-cache.json"
    if cache_p.is_file():
        cache = json.loads(cache_p.read_text(encoding="utf-8"))
        red_no_out = [k for k, v in cache.items() if isinstance(v, dict) and v.get("red") and not v.get("out")]
        out_no_red = [k for k, v in cache.items() if isinstance(v, dict) and v.get("out") and not v.get("red")]
        check("in the live cache, `red` and `out` always travel together (the documented shape)",
              red_no_out == [] and out_no_red == [], (red_no_out[:3], out_no_red[:3]))

    print("\n  %d failure(s)" % len(FAILS))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
