#!/usr/bin/env python3
"""scripts/each.py must survive losing an item, and must not truncate anything.

Run:  python3 scripts/tests/test_each_survives.py
Exit 0 if all cases pass, 1 otherwise.

WHAT THIS PINS, and why each case is unsatisfiable without the real behaviour.

each.py exists because `timeout 3000 merge_gate.py | tail -60` ran fifty minutes and
produced ZERO BYTES: tail buffers until its input closes, one process covered seventeen
items, and nothing bounded an individual item. Jas asked for the fixture instead of a
lesson, and asked specifically *"if it dies at branch 4, can it skip 4 and go to 5?"*

A weaker suite would assert "the output file exists" and "the exit code is 1", both of which
are true of an each.py that gives up at the first timeout. So:

  1. SUCCESSORS RAN. An item AFTER the hung one must appear. This is the case that fails if
     a timeout aborts the run, and it is the one Jas actually asked for.
  2. THE TIMEOUT IS NAMED IN THE FILE. A skip that is silent makes a hung item look like one
     that passed quietly -- the class of failure this whole file reacts to.
  3. PARTIAL OUTPUT IS KEPT. What a job printed before it hung is usually where the cause is.
  4. NOTHING IS TRUNCATED. A long item's output must arrive WHOLE, byte for byte. This is the
     point of the exercise: the fixture was written in a session spent removing caps, and a
     cap in here would defeat it. Pinned with output well past any plausible buffer.
  5. RESULTS LAND AS THEY FINISH, not at the end. Asserted by reading the file WHILE the run
     is still going: the first item must be readable before the last one has started.
"""
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
EACH = REPO / "scripts" / "each.py"

PASSED = FAILED = 0


def check(label, got, want):
    global PASSED, FAILED
    if got == want:
        PASSED += 1
        print(f"  [ok]   {label}")
    else:
        FAILED += 1
        print(f"  [BAD]  {label}: got {got!r}, want {want!r}")


def main():
    if not EACH.is_file():
        print(f"  [BAD]  {EACH} does not exist")
        return 1

    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "r.txt"
        # alpha finishes, HANG never returns, omega finishes AFTER the hang.
        script = ('if [ "{}" = HANG ]; then echo PARTIAL-MARKER; sleep 30; '
                  'else echo "ran-{}"; fi')
        r = subprocess.run(
            [sys.executable, str(EACH), "--out", str(out), "--timeout", "3",
             "--items", "alpha,HANG,omega", "--", "bash", "-c", script],
            capture_output=True, text=True, timeout=180)
        text = out.read_text(encoding="utf-8") if out.is_file() else ""

        check("1. the item AFTER the hung one still ran", "ran-omega" in text, True)
        check("1b. the item BEFORE it is still recorded", "ran-alpha" in text, True)
        check("2. the timeout is named in the file, not silent",
              bool(re.search(r"TIMED OUT after 3s", text)), True)
        check("2b. and it says the run continued",
              "SKIPPED" in text, True)
        check("3. output printed before the hang is kept",
              "PARTIAL-MARKER" in text, True)
        check("4. a non-zero exit is reported for the bad item",
              r.returncode, 1)
        check("4b. the summary counts 2 of 3",
              "SUMMARY 2/3 ok" in text, True)

    # ------------------------------------------------------------------
    # 5. NOTHING IS TRUNCATED. 200,000 characters, whole.
    # ------------------------------------------------------------------
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "big.txt"
        n = 200_000
        subprocess.run(
            [sys.executable, str(EACH), "--out", str(out), "--timeout", "60",
             "--items", "big", "--", "bash", "-c", f'printf "x%.0s" $(seq 1 {n})'],
            capture_output=True, text=True, timeout=180)
        text = out.read_text(encoding="utf-8") if out.is_file() else ""
        longest = max((len(m) for m in re.findall(r"x+", text)), default=0)
        check(f"5. all {n} characters arrived, none cut", longest, n)

    # ------------------------------------------------------------------
    # 6. RESULTS APPEAR INCREMENTALLY, which is the whole reason tail was wrong.
    #    Read the file while the run is still in progress.
    # ------------------------------------------------------------------
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "inc.txt"
        p = subprocess.Popen(
            [sys.executable, str(EACH), "--out", str(out), "--timeout", "60",
             "--items", "first,second", "--",
             "bash", "-c", 'if [ "{}" = second ]; then sleep 8; fi; echo "done-{}"'],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        seen_early = False
        deadline = time.time() + 7          # inside `second`'s sleep, before the run ends
        while time.time() < deadline:
            if out.is_file() and "done-first" in out.read_text(encoding="utf-8"):
                seen_early = True
                break
            time.sleep(0.2)
        check("6. the first result was readable while the run was still going",
              seen_early, True)
        p.wait(timeout=120)

    # ------------------------------------------------------------------
    # 7. A CHILD THAT READS STDIN MUST NOT HANG, and must not eat the item list.
    #    Jas, 2026-09-14: "what's the fix for the process that hung because of input not
    #    closing?" The fix is stdin=DEVNULL on the child. Without it `cat` blocks until its
    #    per-item timeout, and -- worse -- when the items come from OUR stdin the child can
    #    swallow the rest of the list, so work disappears while the counts still look right.
    # ------------------------------------------------------------------
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "stdin.txt"
        started = time.time()
        r = subprocess.run(
            [sys.executable, str(EACH), "--out", str(out), "--timeout", "20", "--",
             "bash", "-c", 'cat; echo "after-stdin-{}"'],
            input="one\ntwo\nthree\n", capture_output=True, text=True, timeout=180)
        elapsed = time.time() - started
        text = out.read_text(encoding="utf-8") if out.is_file() else ""
        # All three items ran: the children did not consume the list.
        check("7. every item from stdin ran (a child did not eat the list)",
              sum(f"after-stdin-{i}" in text for i in ("one", "two", "three")), 3)
        # And none of them blocked on stdin -- 20s timeout x3 would be 60s if they had.
        check("7b. no child blocked waiting on input that never closes", elapsed < 20, True)
        check("7c. and the run reports all three ok", "SUMMARY 3/3 ok" in text, True)

    print(f"test_each_survives: {PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
