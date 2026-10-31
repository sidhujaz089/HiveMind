#!/usr/bin/env python3
"""Keep the failure ceiling honest and its promised count true -- the chore, done by a job.

    python3 scripts/ceiling_maintain.py            # repair and commit if anything changed
    python3 scripts/ceiling_maintain.py --check    # report only; exit 1 if a repair is needed
    python3 scripts/ceiling_maintain.py --dry-run  # say what it would change, write nothing

TWO FACTS THAT DRIFT WHILE THE LINE RUNS, and both are promises the repository makes:

  * workspace/test-failures-ceiling.json lists the suites allowed to fail. A suite on that list that
    now PASSES is a lie ("a green suite on the list warns about nothing"), so it comes OFF.
  * workspace/test-failures-ceiling-count.json is the number promised. When production declares a
    failing suite, the list grows and the count must follow it up IN THE SAME ACT -- a fact with no
    number is a silent rise, which is what the "may fall and must not rise" check exists to catch.

WHAT IT WILL NOT DO: add a suite, run a suite, or edit a suite. A suite with no recorded verdict is a
QUESTION (run it and record a verdict) and this job leaves it alone and says so -- an instrument that
guesses looks like coverage. It only removes what has gone green and makes the number equal the list.

WHY A JOB AND NOT A HABIT (Jas, 2026-10-27: "Yes make the sweep good idea"): with production on this
went wrong twice in one hour, and each time a session spent minutes on bookkeeping instead of work.
"""
import json
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
import check_workspace as CW                                       # noqa: E402

CEILING = Path("workspace") / "test-failures-ceiling.json"
COUNT = Path("workspace") / "test-failures-ceiling-count.json"
LEDGER = Path("workspace") / "suite-state.jsonl"
WHO = "ceiling_maintain.py"


def plan(live):
    """(drop, declared_after, count_before, unrecorded) -- the pure decision, no writes."""
    live = Path(live)
    names = CW._ceiling_names(live / CEILING)
    verdicts = CW.latest_verdicts(live / LEDGER)
    h = CW.ceiling_honesty(names, verdicts)
    drop = sorted(h["green"])
    try:
        count_before = json.loads((live / COUNT).read_text(encoding="utf-8")).get("count")
    except (OSError, ValueError):
        count_before = None
    return drop, len(names) - len(drop), count_before, sorted(h["unrecorded"])


def repair(live):
    """Apply the plan to the two files. Returns the list of changes made (empty = nothing to do)."""
    live = Path(live)
    drop, declared, count_before, unrecorded = plan(live)
    changed = []
    if drop:
        p = live / CEILING
        d = json.loads(p.read_text(encoding="utf-8"))
        d["allowed_to_fail"] = [n for n in d["allowed_to_fail"] if n not in set(drop)]
        d.setdefault("_dropped_green_by_the_sweep", []).extend(drop)
        p.write_text(json.dumps(d, indent=1) + "\n", encoding="utf-8")
        changed.append(f"removed {len(drop)} suite(s) that PASS: " + ", ".join(drop[:6]))
    if count_before != declared:
        p = live / COUNT
        d = json.loads(p.read_text(encoding="utf-8"))
        d["count"], d["at"] = declared, time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        d["why"] = ((d.get("why") or "") + f" | {time.strftime('%Y-%m-%d', time.gmtime())} {WHO}: count set to the "
                    f"{declared} declared, which is the fact the number promises").strip(" |")
        p.write_text(json.dumps(d, indent=1) + "\n", encoding="utf-8")
        changed.append(f"count {count_before} -> {declared}")
    return changed, unrecorded


def main(argv):
    check_only = "--check" in argv
    dry = "--dry-run" in argv
    drop, declared, count_before, unrecorded = plan(REPO)
    if not drop and count_before == declared:
        print(f"  ceiling honest: {declared} declared, count {count_before}"
              + (f"; {len(unrecorded)} with no verdict yet (run it, do not guess): {', '.join(unrecorded[:4])}"
                 if unrecorded else ""))
        return 0
    if check_only:
        print(f"  NEEDS A REPAIR: {len(drop)} green on the list, count {count_before} vs {declared} declared")
        return 1
    if dry:
        print(f"  would remove {len(drop)} green suite(s) and set the count to {declared}")
        return 0
    changed, unrecorded = repair(REPO)
    subj = "chore(workspace): the ceiling keeps itself honest -- " + "; ".join(changed)[:120]
    body = "\n".join(changed) + (f"\n\nno verdict yet, left alone: " + ", ".join(unrecorded[:8]) if unrecorded else "")
    msg = subj + "\n\n" + body + f"\n\nAgent: {WHO}\n"
    try:
        r = subprocess.run(["git", "commit", "--only", "-m", msg, "--", str(CEILING), str(COUNT)],
                           cwd=str(REPO), capture_output=True, text=True, timeout=300)
    except subprocess.TimeoutExpired:
        print("  COMMIT DID NOT FINISH in 300s; the two files are edited and NOT committed",
              file=sys.stderr)
        return 4
    print(f"  {'committed' if r.returncode == 0 else 'COMMIT REFUSED'}: " + "; ".join(changed))
    if r.returncode:
        print((r.stdout + r.stderr)[:600], file=sys.stderr)
    return r.returncode


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
