#!/usr/bin/env python3
"""The suite-verdict reader: one command that answers "of the ceiling's N, which are green and red" (I1).

This exists because of a specific, repeated failure. On 2026-10-26 the same question was answered three
different ways in one session:

  * a throwaway classifier read `workspace/suite-cache.json` looking for a PASS/FAIL *word* in each
    entry's `out` field, and reported 114 of 456 allowed-to-fail suites as green. The cache carries no
    word: its verdict is a SHAPE (an entry exists iff the suite passed, and a failure carries `red` +
    `out`), so "no out" -- which means PASSED -- was read as unreadable. The 114 was invented.
  * the retraction came from reading `workspace/suite-state.jsonl` instead, whose rows carry `exit_code`.
  * the 102/354 split that survived was produced by an inline one-off that then vanished.

Three instruments, one question, two answers, nothing to point at afterwards. The fix is not another
analysis: it is ONE named reader with a test, so the next session quotes a command instead of a memory.

It reads the LEDGER, never the cache, and says so in every report. `unrecorded` is counted separately
from `green` -- a suite with no row is a question nobody has asked yet, not a pass.
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import check_workspace as cw  # noqa: E402  -- one definition of "the latest verdict"

CEILING = ROOT / "workspace" / "test-failures-ceiling.json"
LEDGER = ROOT / "workspace" / "suite-state.jsonl"

WHY = ("read from workspace/suite-state.jsonl (the ledger, which carries exit_code), NOT from "
       "workspace/suite-cache.json (which carries no verdict -- an entry there means the suite PASSED, "
       "and a failure carries red+out; reading 'no out' as unreadable invented 114 greens on 2026-10-26)")


def report(ceiling=None, ledger=None, as_json=False):
    """{"green": [...], "red": [...], "unrecorded": [...], "ceiling_count": n, "why": ...}."""
    ceiling_p = Path(ceiling) if ceiling else CEILING
    ledger_p = Path(ledger) if ledger else LEDGER
    try:
        names = json.loads(ceiling_p.read_text(encoding="utf-8")).get("allowed_to_fail") or []
    except (OSError, ValueError) as e:
        return {"problem": f"the ceiling could not be read ({type(e).__name__}: {e}) -- nothing was "
                           f"classified", "green": [], "red": [], "unrecorded": [], "ceiling_count": 0}
    verdicts = cw.latest_verdicts(ledger_p)
    if not verdicts:
        return {"problem": f"{ledger_p.name} gave no verdicts at all -- an unreadable ledger is not an "
                           f"empty one, and nothing was classified",
                "green": [], "red": [], "unrecorded": [], "ceiling_count": len(names)}
    h = cw.ceiling_honesty(names, verdicts)
    out = {"green": h["green"], "red": sorted(n for n in names
                                              if n in verdicts and verdicts[n] != 0),
           "unrecorded": h["unrecorded"], "ceiling_count": len(names), "why": WHY}
    return json.dumps(out, indent=1) if as_json else out


def verdict_for(suite, ledger=None):
    """The newest recorded exit code for one suite, or None when it has no row."""
    return cw.latest_verdicts(Path(ledger) if ledger else LEDGER).get(suite)


# A LINE THAT TELLS YOU WHY. Deliberately the same idea as run_tests.failure_tail: a tally ("3 passed,
# 2 failed") is not a cause, and a classifier that accepts one makes the tally the biggest cluster.
CAUSE_LINE = re.compile(
    r"(Error|Exception|Traceback|assert|FAIL|Refused|refused|No such file|timed out|No module)", re.I)
TALLY_LINE = re.compile(r"^\s*\d+\s+(passed|failed|failure|test)", re.I)
# A traceback HEADER is not a cause; the exception line under it is. Treating the header as a cause gives
# every failing Python suite the same meaningless group.
TRACEBACK_HEADER = re.compile(r"^Traceback \(most recent call last\):?$", re.I)
NO_CAUSE = "(no cause line in the stored output)"


def cause_of(out):
    """The most informative line of a stored failure: the first that looks like a cause.

    Returns "" when the stored output carries only a tally -- which is honest, and keeps the tally out of
    the group list. (Until 2026-10-26 the stored tail was four lines and this returned nothing for a
    quarter of the red suites; that is E1a's fix, and this function is why it mattered.)
    """
    for line in str(out or "").splitlines():
        s = line.strip()
        if not s or TALLY_LINE.match(s) or TRACEBACK_HEADER.match(s):
            continue
        if CAUSE_LINE.search(s):
            return re.sub(r"\s+", " ", s)[:140]
    return ""


def cause_groups(path=None, limit=15):
    """[{cause, count, suites}] for the RED suites, grouped by their stored cause.

    The verdict (which suites are red) is the caller's, from the ledger. This function only reads the
    cache's `out`, because that is the only place a failing suite's output is stored -- two instruments,
    named separately in the output so nobody confuses which answered what.
    """
    p = Path(path) if path else ROOT / "workspace" / "suite-cache.json"
    try:
        cache = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    groups = {}
    for name, entry in cache.items():
        if not isinstance(entry, dict) or not entry.get("red"):
            continue
        c = cause_of(entry.get("out"))
        key = c or NO_CAUSE
        groups.setdefault(key, []).append(name)
    out = [{"cause": k, "count": len(v), "suites": sorted(v)} for k, v in groups.items()]
    # THE UNKNOWN BUCKET SORTS LAST WHATEVER ITS SIZE: it is a non-answer, and letting it rank as the
    # biggest "cluster" would point the next fix at the instrument instead of at a cause.
    out.sort(key=lambda g: (g["cause"] == NO_CAUSE, -g["count"], g["cause"]))
    return out[:limit]


def classify_text(groups=None, red=None):
    """The report. It names BOTH instruments because the answer depends on two different files."""
    groups = cause_groups() if groups is None else groups
    if red is None:
        red = sum(g["count"] for g in groups)
    lines = [f"  {red} red suite(s), grouped by the cause stored with them:",
             "  verdict: workspace/suite-state.jsonl (exit_code, latest row per suite)",
             "  cause:   workspace/suite-cache.json (`out`, written by run_tests.failure_tail)",
             ""]
    for g in groups:
        lines.append(f"    {g['count']:4d}  {g['cause']}")
        lines.append(f"          e.g. {', '.join(g['suites'][:3])}")
    if groups and groups[0]["count"] > 1:
        lines += ["", f"  The largest cluster is {groups[0]['count']} suite(s) sharing: {groups[0]['cause']}",
                  "  That is one fix, not " + str(groups[0]["count"]) + " fixes -- start there."]
    return "\n".join(lines)


USAGE = """suite_verdicts.py -- of the suites allowed to fail, which are green, which are red (I1)

  (no arguments)        the split, with the source of the verdict named
  --json                the same as a structure
  --green               only the green ones, one per line (the free quarter)
  --red                 only the red ones
  --suite <name>        the newest recorded verdict for one suite (exit code), or nothing
  --classify            group the red suites by the cause stored with each one

Reads workspace/suite-state.jsonl, never suite-cache.json: the cache stores no verdict. On 2026-10-26 a
classifier read it as if it did and invented 114 green suites.
"""


def main(argv):
    if argv and argv[0] in ("-h", "--help"):
        print(USAGE)
        return 0
    if argv and argv[0] == "--suite" and len(argv) > 1:
        v = verdict_for(argv[1])
        print("unrecorded -- no row for it in the ledger" if v is None else f"{argv[1]}: exit {v}")
        return 0
    r = report(as_json="--json" in argv)
    if isinstance(r, str):
        print(r)
        return 0
    if r.get("problem"):
        print(f"  PROBLEM: {r['problem']}")
        return 1
    if "--classify" in argv:
        # The verdict is the LEDGER's; the cause is the CACHE's stored output. Both are named in the text.
        r = report()
        if isinstance(r, dict) and r.get("problem"):
            print(f"  PROBLEM: {r['problem']}")
            return 1
        print(classify_text(red=len(r["red"]) if isinstance(r, dict) else None))
        return 0
    if "--green" in argv:
        for n in r["green"]:
            print(n)
        return 0
    if "--red" in argv:
        for n in r["red"]:
            print(n)
        return 0
    print(f"  {r['ceiling_count']} suite(s) allowed to fail, of which:")
    print(f"    GREEN      {len(r['green']):4d}  they pass, so they do not belong on the list")
    print(f"    RED        {len(r['red']):4d}  the real burn-down target")
    print(f"    UNRECORDED {len(r['unrecorded']):4d}  no verdict on record -- a question, not a pass")
    print(f"  {r['why']}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
