#!/usr/bin/env python3
"""t698 -- manager_note.py records the scoper's verdict, label and prompt-template
hash as a pair row, for every answer it routes.

Red on main: manager_note has no record_pair() (AttributeError). That is the whole
reason; run it against current code and read which assertion fails.

The scoper's verdict is recorded nowhere today: apply() writes a task note only when
`why` is non-empty (scripts/manager_note.py:115-119) and a clean (b) answer has why=""
(decide() at :71), so the letter survives only in the answer file and the template hash
survives nowhere. This suite pins the record that makes a pair joinable.

No model call, no live server, no writes outside a tempfile.TemporaryDirectory. The real
repo is checked before and after: the store must land in the root passed in.
"""
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import manager_note as mn          # noqa: E402  -- exists; record_pair does not
import manager_prompt as mp        # noqa: E402  -- version() is the template stamp

FAILS = []


def check(cond, msg):
    if not cond:
        FAILS.append(msg)
        print("FAIL:", msg)


STORE = "workspace/scope-pairs.jsonl"

ANSWERS = {
    # three answers a manager can actually produce, each declaring a different step
    "b": "(b) NEEDS JAS -- the note asks for a decision I cannot make for him.\n",
    "c": "(c) SCOPE -- one chunk against scripts/x.py.\n\n```chunks\n[]\n```\n",
    "d": "Step **(d)** -- BLOCKED: needs t698's outcome store.\n",
    "none": "I will check the line numbers first, then write the answer.\n",
}


def rows(root):
    p = root / STORE
    if not p.is_file():
        return []
    # every line must parse: a store with a torn row is a store nobody can read
    return [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def test_records_a_real_verdict_with_a_real_hash():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        mn.record_pair("t698", ANSWERS["b"], "workspace/run-logs/manager-queue/out/t698.md",
                       root=root)
        got = rows(root)
        check(len(got) == 1, f"expected one row, got {len(got)}")
        if not got:
            return
        r = got[0]
        check(r["task"] == "t698", f"task: expected 't698', got {r.get('task')!r}")
        check(r["verdict"] == "b", f"verdict: expected 'b', got {r.get('verdict')!r}")
        # the label is STEPS's own word, not a paraphrase (manager_note.py:20-21)
        check(r["label"] == "NEEDS JAS", f"label: expected 'NEEDS JAS', got {r.get('label')!r}")
        check(r["needs"] == "jas", f"needs: expected 'jas', got {r.get('needs')!r}")
        check(r["status"] == "queued",
              f"status: expected 'queued' (decide at :71), got {r.get('status')!r}")
        check(r["answer"] == "workspace/run-logs/manager-queue/out/t698.md",
              f"answer path not recorded verbatim: {r.get('answer')!r}")
        # the hash is COMPUTED at test time from the template the scoper runs on, never a
        # literal: a stub returning a constant string fails here
        check(r["template"] == mp.version(),
              f"template hash: expected manager_prompt.version()=={mp.version()!r}, "
              f"got {r.get('template')!r}")
        check(re.fullmatch(r"[0-9a-f]{6,16}", r["template"] or "") is not None,
              f"template is not a template hash: {r.get('template')!r}")
        check(re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{6}Z", r.get("at", "")) is not None
              or re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", r.get("at", "")) is not None,
              f"row has no UTC timestamp: {r.get('at')!r}")


def test_the_verdict_is_the_verdict_of_the_answer_you_pass_in():
    # three DISTINCT real verdicts: an implementation that hard-codes one label fails
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        mn.record_pair("t700", ANSWERS["b"], "a.md", root=root)
        mn.record_pair("t701", ANSWERS["c"], "b.md", root=root)
        mn.record_pair("t702", ANSWERS["d"], "c.md", root=root)
        got = rows(root)
        check(len(got) == 3, f"append-only store: expected 3 rows, got {len(got)}")
        check([r.get("verdict") for r in got] == ["b", "c", "d"],
              f"rows are not in call order: {[r.get('verdict') for r in got]}")
        check(got[1]["label"] == "SCOPED", f"(c) label: {got[1].get('label')!r}")
        check(got[2]["label"] == "BLOCKED", f"(d) label: {got[2].get('label')!r}")
        check(got[1]["status"] == "doing", f"(c) routes to doing: {got[1].get('status')!r}")
        check(got[2]["status"] == "blocked", f"(d) routes to blocked: {got[2].get('status')!r}")
        check(got[1]["needs"] == "", f"(c) has no owner: {got[1].get('needs')!r}")
        check(got[0]["template"] == got[1]["template"] == mp.version(),
              "every row of one run carries the same template hash")


def test_an_answer_that_declares_no_step_is_recorded_too():
    # THE CASE THIS CHANGE EXISTS FOR: today that answer leaves no trace at all
    # (apply() at :115-119 writes nothing when why is empty).
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        mn.record_pair("t703", ANSWERS["none"], "d.md", root=root)
        got = rows(root)
        check(len(got) == 1, f"an undeclared answer must be recorded, got {len(got)} rows")
        if not got:
            return
        check(got[0]["verdict"] is None, f"no step declared: {got[0].get('verdict')!r}")
        check(got[0]["label"] == "NO STEP DECLARED",
              f"expected the real fallback label, got {got[0].get('label')!r}")
        check(got[0]["needs"] == "claude",
              f"an undeclared answer goes to Claude: {got[0].get('needs')!r}")


def test_the_row_carries_every_field_the_scorer_will_need():
    # required FIELDS, not a truthiness pass -- a row missing `verdict` is unusable and
    # a truthiness check on the dict would let it through
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        mn.record_pair("t704", ANSWERS["b"], "", root=root)
        got = rows(root)
        check(len(got) == 1, f"expected one row, got {len(got)}")
        if not got:
            return
        for k in ("task", "at", "verdict", "label", "status", "needs", "answer", "template"):
            check(k in got[0], f"row is missing the field {k!r}: {sorted(got[0])}")
        check(got[0]["answer"] == "", f"an empty answer path records as empty: {got[0]['answer']!r}")


def test_the_store_is_append_only_not_rewritten():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        mn.record_pair("t705", ANSWERS["b"], "a.md", root=root)
        first = (root / STORE).read_text(encoding="utf-8")
        mn.record_pair("t706", ANSWERS["c"], "b.md", root=root)
        after = (root / STORE).read_text(encoding="utf-8")
        check(after.startswith(first),
              "the store was rewritten, not appended to -- the pair record is history")
        check(len(after.splitlines()) == 2, f"expected 2 lines, got {len(after.splitlines())}")


def _porcelain():
    return subprocess.run(["git", "status", "--porcelain"], cwd=str(ROOT),
                          capture_output=True, text=True, timeout=120).stdout


def test_writing_a_pair_touches_no_live_state():
    # the store is rooted at the `root` argument. apply() ignores its own repo argument
    # (manager_note.py:100 pins REPO), so record_pair must not inherit that habit.
    before = _porcelain()
    with tempfile.TemporaryDirectory() as d:
        mn.record_pair("t707", ANSWERS["b"], "a.md", root=Path(d))
    after = _porcelain()
    check(before == after,
          "the worktree changed -- the store was written outside the root passed in:\n"
          f"before:\n{before}\nafter:\n{after}")
    check(not (ROOT / STORE).exists(),
          f"{STORE} must not be created in the live repo by a test root")


if __name__ == "__main__":
    for name in sorted(globals()):
        fn = globals()[name]
        if name.startswith("test_") and callable(fn):
            try:
                fn()
            except Exception as e:                                  # noqa: BLE001
                FAILS.append(f"{name} raised {type(e).__name__}: {e}")
                print(f"FAIL: {name} raised {type(e).__name__}: {e}")
    print(f"\n{len(FAILS)} failure(s)" if FAILS else "\nall checks passed")
    sys.exit(1 if FAILS else 0)
