#!/usr/bin/env python3
"""t1516: every run of test_coder_branch_delivery.py computes its OWN job label.

WHY. scripts/tests/test_coder_branch_delivery.py builds its brief from a module-level
constant, so coder.py's label -- projects/local-inference/coder.py:2551
(label = 'brief-' + hashlib.sha256(a.brief.encode()).hexdigest()[:8]) -- is the same string in
every process that runs the suite. coder.py:2563-2567 refuses two jobs that share a label,
with the same files and model ('REFUSED: pid N is already running this job'), so the merge
gate's run of the suite and the sweep's run of the suite refuse each other. The fix is a brief
unique per process. This suite pins that property without driving a live coder.py, because the
suite itself exits 1 where there is no .git (its main()'s sandbox guard) and so can never be a
chunk target.

Three cases:
  1  the label keeps coder.py's shape and is DERIVED from BRIEF. Anti-vacuous: appending a
     suffix to LABEL directly makes the test unique while coder.py, handed the unchanged brief,
     still computes the shared label, and the suite's BRANCH stops naming the branch its run
     writes.
  2  RED ON MAIN: four processes importing the suite get four pairwise-distinct labels. Today
     BRIEF is a constant, so all four hash to the same label.
  3  RED ON MAIN: cleanup() sweeps a stale 'Agent: ollama/faketest' branch under the
     coder/brief- prefix, and never sweeps a fresh one (a run that may still be going) nor an
     unmarked one (a human's work awaiting review). Uniqueness per process is what makes such
     leftovers permanent, so the sweep is part of the fix, not an extra.
"""
import hashlib
import importlib.util
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
TARGET = REPO / "scripts" / "tests" / "test_coder_branch_delivery.py"

LABEL_RE = re.compile(r"brief-[0-9a-f]{8}\Z")
N_PROCESSES = 4
MARKER = "Agent: ollama/faketest"

STALE = "coder/brief-0badf00d"
FRESH = "coder/brief-0f1c3500"
FOREIGN = "coder/brief-not-mine"

# a child interpreter loads the suite from its argv path and prints the label it derives
PRINT_LABEL = (
    "import importlib.util, sys\n"
    "spec = importlib.util.spec_from_file_location('tcbd', sys.argv[1])\n"
    "m = importlib.util.module_from_spec(spec)\n"
    "spec.loader.exec_module(m)\n"
    "print(m.LABEL)\n"
)


def load():
    spec = importlib.util.spec_from_file_location("tcbd_target", str(TARGET))
    if spec is None or spec.loader is None:
        raise AssertionError(f"cannot load {TARGET}")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_label_shape_and_derivation(m):
    out = []
    for name in ("LABEL", "BRANCH", "WT", "BRIEF", "run_coder", "cleanup"):
        if not hasattr(m, name):
            out.append(f"the module defines no {name}")
    if out:
        return out
    if not LABEL_RE.match(str(m.LABEL)):
        out.append(f"LABEL {m.LABEL!r} is not 'brief-' + 8 hex chars: coder.py builds its branch "
                   f"name from the label, so the label must stay git-safe")
    brief = str(m.BRIEF)
    if not brief.strip() or "\n" in brief:
        out.append(f"BRIEF {brief!r} must stay a single non-empty line, so coder.py's guard for "
                   f"--brief (a one-line string naming an existing file is refused as a path) is "
                   f"unaffected by the new per-run text")
    want = "brief-" + hashlib.sha256(brief.encode()).hexdigest()[:8]
    if str(m.LABEL) != want:
        out.append(f"LABEL {m.LABEL!r} is not derived from BRIEF the way coder.py derives its "
                   f"label from the brief it is handed (expected {want!r}). A unique label made "
                   f"by appending a suffix to LABEL leaves the collision in place: coder.py "
                   f"hashes the brief text it receives, so two runs would still share a label, "
                   f"and BRANCH would stop naming the branch the run writes.")
    if str(m.BRANCH) != f"coder/{m.LABEL}":
        out.append(f"BRANCH {m.BRANCH!r} is not coder/ + LABEL, so the suite asserts on a branch "
                   f"no run creates")
    if Path(m.WT).name != f"review-{m.LABEL}":
        out.append(f"{Path(m.WT).name!r} is not review- + LABEL: coder.py names the worktree from "
                   f"the label, so a suite whose worktree is not named from its label checks a "
                   f"path no run creates")
    return out


def _label_from_subprocess():
    r = subprocess.run([sys.executable, "-c", PRINT_LABEL, str(TARGET)],
                       capture_output=True, text=True, timeout=120, cwd=str(REPO))
    if r.returncode != 0:
        tail = " | ".join((r.stderr or r.stdout or "").strip().splitlines()[-3:])
        return None, f"process exited {r.returncode}: {tail}"
    lines = (r.stdout or "").strip().splitlines()
    return (lines[-1].strip() if lines else ""), None


def test_labels_differ_across_processes():
    out = []
    with ThreadPoolExecutor(max_workers=N_PROCESSES) as ex:
        got = [f.result() for f in [ex.submit(_label_from_subprocess) for _ in range(N_PROCESSES)]]
    labels = []
    for i, (lab, err) in enumerate(got):
        if err:
            out.append(f"process {i} could not read the suite label -- {err}")
            continue
        labels.append(lab)
        if not LABEL_RE.match(lab):
            out.append(f"process {i} computed label {lab!r}, not 'brief-' + 8 hex chars")
    for i in range(len(labels)):
        for j in range(i + 1, len(labels)):
            if labels[i] == labels[j]:
                out.append(
                    f"processes {i} and {j} computed the SAME label {labels[i]!r}. Every process "
                    f"running scripts/tests/test_coder_branch_delivery.py gets the same brief "
                    f"today, so coder.py computes the same label, same files, same model, and "
                    f"refuses the second copy -- the collision t1516 is about. The BRIEF text "
                    f"(not LABEL) must carry something unique to each process, so coder.py's own "
                    f"hash lands on a distinct label.")
    return out


def test_cleanup_sweeps_only_its_own_stale_branches(m):
    out = []
    now = int(time.time())
    ages = {m.BRANCH: now - 10, STALE: now - 7200, FRESH: now - 60, FOREIGN: now - 7200}
    marked = {m.BRANCH: True, STALE: True, FRESH: True, FOREIGN: False}
    calls = []

    def short(n):
        return str(n).split("refs/heads/")[-1].strip()

    def fake_git(*a):
        calls.append(tuple(a))

        class R:
            def __init__(self, out_=""):
                self.stdout = out_
                self.returncode = 0
                self.stderr = ""

        if not a:
            return R()
        cmd = str(a[0])
        if cmd == "for-each-ref" or (cmd == "branch" and "--list" in a):
            return R("\n".join(list(ages)) + "\n")
        if cmd in ("log", "show"):
            name = short(a[-1])
            joined = " ".join(str(x) for x in a)
            if "%B" in joined:
                tip = f"feat: probe\n\n{MARKER}\n" if marked.get(name) else "real work\n"
                return R(tip)
            if "%ct" in joined or "%at" in joined:
                return R(str(ages.get(name, now)))
            return R()
        return R()

    real_git = m.git
    m.git = fake_git
    try:
        m.cleanup()
    finally:
        m.git = real_git

    deleted = {short(a[-1]) for a in calls
               if len(a) >= 2 and str(a[0]) == "branch" and str(a[1]) == "-D"}
    if m.BRANCH not in deleted:
        out.append(f"cleanup() no longer deletes this run's own branch {m.BRANCH!r} -- the suite "
                   f"would leave its own work behind")
    if STALE not in deleted:
        out.append(f"cleanup() left {STALE!r} in place. It carries the {MARKER} trailer and is "
                   f"two hours old: exactly the killed run's leftover under this suite's own "
                   f"coder/brief- prefix. Giving every run its own label makes such leftovers "
                   f"permanent, so the fix trades a refusal for fake coder/brief-* branches "
                   f"piling up in the review list -- the state cleanup()'s own comment exists "
                   f"to prevent.")
    if FRESH in deleted:
        out.append(f"cleanup() deleted {FRESH!r}, a faketest branch {now - ages[FRESH]}s old. "
                   f"The suite's rule is that a leftover younger than an hour may belong to a "
                   f"run still going; with unique labels two runs of the suite can be live at "
                   f"once, so sweeping by marker alone destroys the other run's in-flight "
                   f"branch.")
    if FOREIGN in deleted:
        out.append(f"cleanup() deleted {FOREIGN!r}, a branch under the coder/brief- prefix that "
                   f"does NOT carry the {MARKER} trailer -- a sweep by prefix alone deletes work "
                   f"a human is waiting to review")
    return out


def main():
    try:
        m = load()
    except Exception as exc:
        print(f"FAIL: cannot import {TARGET}: {type(exc).__name__}: {exc}")
        return 1
    tests = [
        ("the label keeps coder.py's shape and is derived from the brief",
         lambda: test_label_shape_and_derivation(m)),
        ("two processes running the suite get two labels",
         test_labels_differ_across_processes),
        ("cleanup sweeps a stale faketest branch, spares a live one and a foreign one",
         lambda: test_cleanup_sweeps_only_its_own_stale_branches(m)),
    ]
    fails = []
    for name, fn in tests:
        try:
            problems = fn()
        except Exception as exc:
            problems = [f"raised {type(exc).__name__}: {exc}"]
        if problems:
            print(f"  FAIL  {name}")
            for p in problems:
                print(f"        {p}")
                fails.append(p)
        else:
            print(f"  ok    {name}")
    print("test_coder_branch_delivery_label_uniqueness:", "FAILED" if fails else "ok")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
