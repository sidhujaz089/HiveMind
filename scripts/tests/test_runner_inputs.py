#!/usr/bin/env python3
"""A runner's inputs live in the repository, and a runner with no inputs dies loudly.

WHY (t268). scripts/night_queue.sh hardcoded a Claude Code SESSION scratchpad --
/tmp/claude-1000/-home-user-ai-workspace/<session-uuid>/scratchpad -- for every brief it
read and every log it wrote. That session had ended. The files survived only because nothing
had cleared /tmp yet.

THE QUIET HALF IS THE DEFECT. build() writes `row no-brief` and returns 1 when a brief is
missing, which is indistinguishable from a job that had nothing queued. So a wiped /tmp
produced four no-brief rows and an otherwise clean night: the runner reported itself as
having run. That is the shape night_queue.sh's own header argues against -- "a night that
dies half way must be visible as a dead night rather than as silence".

It also blocked what Jas asked for on 2026-09-11: a plan Q16 loops on all day, which "can be
infinity long", cannot keep its work items in a directory that disappears on reboot.

WHAT IS ASSERTED. Check 1 is on the PATH PREFIX, never the specific uuid -- asserting the old
uuid is absent would pass the moment somebody pasted a different session's directory. Check 3
is the decay test and the one with teeth: with the brief directory gone the runner must exit
NON-ZERO, because a row alone is what failed last time.
"""
import re
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
RUNNER = REPO / "scripts" / "night_queue.sh"
BRIEFS = REPO / "workspace" / "briefs"


def main():
    fails = []

    def check(label, cond, detail=""):
        print(f"  [{'ok' if cond else 'BAD'}]    {label}{'  ' + detail if detail else ''}")
        if not cond:
            fails.append(f"{label} {detail}")

    # 1. NO RUNNER KEEPS STATE IN A SESSION SCRATCHPAD. Prefix, not uuid.
    offenders = []
    for f in sorted(REPO.glob("scripts/*.sh")) + sorted(REPO.glob("scripts/*.py")):
        try:
            body = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if "/tmp/claude-1000/" in body:
            offenders.append(f.name)
    check("no script hardcodes a /tmp/claude-1000/ session path",
          offenders == [], ", ".join(offenders))

    # 2. EVERY JOB THE RUNNER DECLARES HAS ITS BRIEF PRESENT. Driven off the runner's own
    #    build/draft lines, not a second hand-maintained list that would drift from it.
    src = RUNNER.read_text(encoding="utf-8")
    # `build ()` / `draft ()` are the function DEFINITIONS. A job declaration is the call,
    # whose first argument is a bare name -- so exclude anything starting a parameter list.
    job = lambda kw: [n for n in re.findall(rf"^\s*{kw}\s+([A-Za-z][\w-]*)", src, re.M)]
    names = job("build") + [f"oracle-{n}" for n in job("draft")]
    check("the runner declares at least one job", len(names) > 0, f"{len(names)} job(s)")
    missing = [n for n in names if not (BRIEFS / f"brief-{n}.txt").is_file()]
    check("every declared job has a tracked brief", missing == [],
          "missing: " + ", ".join(missing) if missing else f"{len(names)} present")

    # 3. THE DECAY TEST. A row is not enough -- that is what failed. Point the runner at a
    #    repo root with no briefs directory and require a non-zero exit.
    with tempfile.TemporaryDirectory() as td:
        fake = Path(td)
        (fake / "workspace").mkdir(parents=True)
        patched = src.replace("REPO=/home/user/ai-workspace", f"REPO={fake}")
        check("the runner's REPO is patchable for this test",
              f"REPO={fake}" in patched, "the literal assignment moved")
        runner = fake / "night_queue.sh"
        runner.write_text(patched)
        r = subprocess.run(["bash", str(runner)], capture_output=True, text=True, timeout=120)
        check("a missing brief directory exits NON-ZERO", r.returncode != 0,
              f"exit={r.returncode}")
        ledger = fake / "workspace" / "backup-runs.jsonl"
        rows = ledger.read_text() if ledger.is_file() else ""
        check("...and says so in the ledger with a distinct status",
              "inputs-missing" in rows, rows.strip() or "(no ledger written)")
        check("...and does NOT claim the night started",
              '"status": "started"' not in rows, rows.strip())

    print()
    if fails:
        print(f"FAIL: {len(fails)} problem(s)")
        for f in fails:
            print(f"  - {f}")
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
