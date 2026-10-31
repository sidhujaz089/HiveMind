#!/usr/bin/env python3
"""t1247 oracle: no suite may leave the real workspace/tasks/ dirty.

WHY (workspace/rca/d88b7dee39f3.md). A test called taskstore.add() against the REAL store and
left `170. t249 — a throwaway task for the big-project flag test  _unfiled_` in
workspace/tasks/QUEUE.md; the hand-rolled cleanup then resolved the id to QUEUE.md's own path and
rewrote the file. Nothing made that legal-or-illegal, red-or-green, visible-or-invisible. This
suite is the recorder that proves the guard is in force next time.

Run: python3 scripts/tests/test_no_live_queue_mutation.py   (pytest is NOT installed here).

RED ON MAIN FOR ONE REASON: scripts/run_tests.py has no TASKS_DIR, no QUEUE_VIOLATIONS, no
queue_fingerprint, no diff_fingerprints, no record_queue_violation, and _once() never looks at
workspace/tasks/ -- so every check below that touches one of them raises AttributeError. There is
no bug in this file to make it red.

EVERY assertion works inside a tempfile.TemporaryDirectory. The real workspace/tasks/ is never
written: rt.TASKS_DIR is repointed at the throwaway tree for the duration of each check, the same
shape suite_owner() documents as the file's own pattern -- read the global at CALL time so a test
can redirect it. No model, no server, no fixed /tmp path, nothing left running.
"""
import importlib.util
import json
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]          # scripts/tests -> repo root
RUNNER = ROOT / "scripts" / "run_tests.py"
PASSED = FAILED = 0

STRAY = "\n170. t249 — a throwaway task for the big-project flag test  _unfiled_\n"
HEX64 = re.compile(r"[0-9a-f]{64}")
ISO = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")


def check(label, got, want):
    global PASSED, FAILED
    if got == want:
        PASSED += 1
        print(f"  [ok]   {label}: {got!r}")
    else:
        FAILED += 1
        print(f"  [BAD]  {label}: expected {want!r}, got {got!r}")


# Import by PATH: the module is a script, not an installed package. Its sweep sits behind a
# __main__ guard (the tail's last two lines), so loading it runs definitions only.
_spec = importlib.util.spec_from_file_location("run_tests_under_test", RUNNER)
rt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rt)


def tree(tmp):
    """A throwaway tasks tree with THREE files carrying three DISTINCT bodies, so an
    implementation that answers every path with the same value cannot pass."""
    d = Path(tmp) / "tasks"
    d.mkdir(parents=True, exist_ok=True)
    (d / "QUEUE.md").write_text("# QUEUE\n1. t1 -- alpha\n2. t2 -- beta\n", encoding="utf-8")
    (d / "t1--alpha.md").write_text("id: t1\ntitle: alpha\n", encoding="utf-8")
    (d / "t2--beta.md").write_text("id: t2\ntitle: beta\n", encoding="utf-8")
    return d


def suite(tmp, name, body):
    """A throwaway suite script -- what the runner would launch -- writing exactly one path."""
    p = Path(tmp) / name
    p.write_text(body, encoding="utf-8")
    return p


def run_suite(tmp, path):
    """Call rt._once() with TASKS_DIR and QUEUE_VIOLATIONS repointed into tmp, always restored.

    rt.TASKS_DIR is read by _once at CALL time, so the guard inspects the throwaway tree and the
    real workspace/tasks/ is never touched by this suite. Restored in finally: a raising check
    must not leave the module pointed at a deleted temp dir."""
    old_tasks, old_ledger = rt.TASKS_DIR, rt.QUEUE_VIOLATIONS
    rt.TASKS_DIR = Path(tmp) / "tasks"
    rt.QUEUE_VIOLATIONS = Path(tmp) / "viol.jsonl"
    try:
        return rt._once(path)
    finally:
        rt.TASKS_DIR, rt.QUEUE_VIOLATIONS = old_tasks, old_ledger


def test_fingerprint_covers_every_file_and_hashes_its_bytes():
    with tempfile.TemporaryDirectory() as tmp:
        d = tree(tmp)
        fp = rt.queue_fingerprint(d)
        check("every file is keyed by its path relative to the tree",
              sorted(fp), ["QUEUE.md", "t1--alpha.md", "t2--beta.md"])
        check("each value is a sha256 hex digest",
              all(HEX64.fullmatch(str(v)) for v in fp.values()), True)
        check("two files with different bytes get DIFFERENT hashes",
              fp["QUEUE.md"] == fp["t1--alpha.md"], False)
        check("the same tree hashes identically twice", rt.queue_fingerprint(d), fp)
        check("a missing tree is empty, not a crash",
              rt.queue_fingerprint(Path(tmp) / "nope"), {})


def test_fingerprint_recurses_into_subdirectories():
    """workspace/tasks/ holds t*.md flat today, and an archive/ subfolder is one mkdir away. A
    fingerprint that globs the top level only is a guard that goes blind on half the tree."""
    with tempfile.TemporaryDirectory() as tmp:
        d = tree(tmp)
        (d / "archive").mkdir()
        (d / "archive" / "t3--gamma.md").write_text("id: t3\ntitle: gamma\n", encoding="utf-8")
        fp = rt.queue_fingerprint(d)
        check("a file in a subdirectory is keyed by its relative path",
              "archive/t3--gamma.md" in fp, True)
        check("and the flat files are still there",
              sorted(k for k in fp if "/" not in k), ["QUEUE.md", "t1--alpha.md", "t2--beta.md"])


def test_diff_of_untouched_tree_is_empty():
    """THE DECAY CASE: a guard that fires on everything is a guard that gets bypassed inside a
    week (planning-for-sustainment). Nothing changed, so it must report nothing."""
    with tempfile.TemporaryDirectory() as tmp:
        d = tree(tmp)
        a = rt.queue_fingerprint(d)
        check("an untouched tree yields no violations", rt.diff_fingerprints(a, a), [])
        check("and a tree compared with its own fresh hash yields none either",
              rt.diff_fingerprints(a, rt.queue_fingerprint(d)), [])


def test_detects_the_real_defect_a_line_appended_in_place():
    """The RCA's exact shape: taskstore.add() creates no file, it APPENDS a line to QUEUE.md. An
    existence check sees nothing; a byte hash sees everything."""
    with tempfile.TemporaryDirectory() as tmp:
        d = tree(tmp)
        before = rt.queue_fingerprint(d)
        (d / "QUEUE.md").write_text((d / "QUEUE.md").read_text(encoding="utf-8") + STRAY,
                                    encoding="utf-8")
        viol = rt.diff_fingerprints(before, rt.queue_fingerprint(d))
        check("the append is caught as exactly one violation", len(viol), 1)
        check("it is QUEUE.md, changed", [(v["kind"], v["path"]) for v in viol],
              [("changed", "QUEUE.md")])
        check("the row carries both whole hashes",
              all(HEX64.fullmatch(v[k]) for v in viol for k in ("before", "after")), True)
        check("the untouched files are NOT reported", sorted(v["path"] for v in viol), ["QUEUE.md"])
        check("the diff is deterministic -- same inputs, same list",
              rt.diff_fingerprints(before, rt.queue_fingerprint(d)), viol)


def test_detects_added_and_removed_files():
    with tempfile.TemporaryDirectory() as tmp:
        d = tree(tmp)
        a = rt.queue_fingerprint(d)
        b = dict(a)
        b["t9--stray.md"] = "0" * 64
        del b["t2--beta.md"]
        check("an added file and a removed file are both reported",
              sorted((v["kind"], v["path"]) for v in rt.diff_fingerprints(a, b)),
              [("added", "t9--stray.md"), ("removed", "t2--beta.md")])
        check("and the two files that did not change are absent",
              [v["path"] for v in rt.diff_fingerprints(a, b)], ["t9--stray.md", "t2--beta.md"])


def test_record_writes_the_ledger_row_whole():
    with tempfile.TemporaryDirectory() as tmp:
        led = Path(tmp) / "viol.jsonl"
        v = {"kind": "changed", "path": "workspace/tasks/QUEUE.md",
             "before": "a" * 64, "after": "b" * 64}
        row = rt.record_queue_violation("test_big_project_flag.py", v, ledger=led)
        check("the row names the suite", row["suite"], "test_big_project_flag.py")
        check("the row names the file", row["path"], "workspace/tasks/QUEUE.md")
        check("the row says what happened", row["kind"], "changed")
        check("the row says when", bool(ISO.fullmatch(str(row["ts"]))), True)
        check("the row is on disk as one JSON line",
              json.loads(led.read_text(encoding="utf-8").splitlines()[-1])["suite"],
              "test_big_project_flag.py")
        rt.record_queue_violation("test_second.py", v, ledger=led)
        lines = led.read_text(encoding="utf-8").splitlines()
        check("the ledger is append-only: the first row is intact",
              json.loads(lines[0])["suite"], "test_big_project_flag.py")
        check("and it now holds two rows", len(lines), 2)
        check("nothing was truncated: the whole 64-hex hashes survive the round trip",
              [json.loads(l)["before"] for l in lines], ["a" * 64, "a" * 64])
        check("and the whole 64-hex 'after' too",
              [json.loads(l)["after"] for l in lines], ["b" * 64, "b" * 64])


def test_a_suite_that_writes_the_live_queue_is_made_red():
    with tempfile.TemporaryDirectory() as tmp:
        d = tree(tmp)
        s = suite(tmp, "test_writes_the_queue.py",
                  "from pathlib import Path\n"
                  f"p = Path({str(d / 'QUEUE.md')!r})\n"
                  "p.write_text(p.read_text(encoding='utf-8') + " + repr(STRAY) +
                  ", encoding='utf-8')\nprint('suite ran')\n")
        rc, secs, out = run_suite(tmp, s)
        check("the live-writing suite is RED", rc != 0, True)
        check("its own output names the guard", "LIVE-QUEUE WRITE" in out, True)
        check("and the file it dirtied", "QUEUE.md" in out, True)
        led = Path(tmp) / "viol.jsonl"
        check("the ledger holds exactly one row for it",
              [json.loads(l)["suite"] for l in led.read_text(encoding="utf-8").splitlines()],
              ["test_writes_the_queue.py"])
        check("the row's path is relative to the tree",
              json.loads(led.read_text(encoding="utf-8").splitlines()[0])["path"], "QUEUE.md")
        check("the row says what happened",
              json.loads(led.read_text(encoding="utf-8").splitlines()[0])["kind"], "changed")
        check("the suite's own output is preserved, not replaced", "suite ran" in out, True)


def test_a_suite_that_writes_anywhere_else_is_untouched():
    """THE NEGATIVE CASE, and the one that keeps this gate alive: a suite writing OUTSIDE
    workspace/tasks/ stays green, produces no ledger row, and keeps its output. A guard that
    fires on every suite is a guard that gets --no-verify'd within a week."""
    with tempfile.TemporaryDirectory() as tmp:
        tree(tmp)
        s = suite(tmp, "test_writes_elsewhere.py",
                  "from pathlib import Path\n"
                  f"p = Path({str(Path(tmp) / 'outside.txt')!r})\n"
                  "p.write_text('harmless\n', encoding='utf-8')\nprint('clean suite')\n")
        rc, secs, out = run_suite(tmp, s)
        check("an unrelated write is not a violation", rc, 0)
        check("no ledger row is written", (Path(tmp) / "viol.jsonl").exists(), False)
        check("the suite's output is unchanged", "clean suite" in out, True)
        check("and no warning is printed for it", "LIVE-QUEUE WRITE" in out, False)


def test_a_clean_suite_still_runs_and_reports():
    with tempfile.TemporaryDirectory() as tmp:
        tree(tmp)
        s = suite(tmp, "test_clean.py", "print('all quiet')\n")
        rc, secs, out = run_suite(tmp, s)
        check("a clean suite is green", rc, 0)
        check("and its output is intact", "all quiet" in out, True)
        check("no violation is recorded for it", (Path(tmp) / "viol.jsonl").exists(), False)
        check("a clean run still reports a time", secs >= 0, True)


def test_the_guard_is_pointed_at_the_real_tree_by_default():
    """What the default must be: the LIVE workspace/tasks/. If a build points TASKS_DIR anywhere
    else, the guard inspects a scratch dir and every real violation is invisible -- a green gate
    that checks nothing (the check-satisfied-by-dead-path shape)."""
    check("the default TASKS_DIR is the live tasks tree",
          Path(rt.TASKS_DIR).resolve(), (ROOT / "workspace" / "tasks").resolve())
    check("the default ledger is under workspace/",
          Path(rt.QUEUE_VIOLATIONS).parent, (ROOT / "workspace").resolve())


TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]

if __name__ == "__main__":
    for t in TESTS:
        print(f"\n{t.__name__}")
        try:
            t()
        except Exception as e:                            # noqa: BLE001
            FAILED += 1
            print(f"  [BAD]  {t.__name__} raised {type(e).__name__}: {e}")
    print(f"\n{len(TESTS)} test function(s), {PASSED} passed, {FAILED} failed\n")
    sys.exit(1 if FAILED else 0)
