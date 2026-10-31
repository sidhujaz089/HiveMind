#!/usr/bin/env python3
"""t1154: the Production tab's four lists, left to right, in the order Jas named them.

    "From left to right I want the Task, Session Review, Script Review, Corpus Embedding lists."
    (Jas, 2026-10-14)

THE FIXTURE IS BUILT HERE, NOT READ FROM THE MACHINE. The tab's renderer is called with every
data source it reads replaced by the fixture below, and the module's WORKSPACE root re-pointed at a
tempfile.mkdtemp() directory, so no live workspace file is read for an expectation and none can be
written. The last assertion proves it: the tree under the real claims directory is snapshotted before
the render and compared after, and this suite goes red if the render touched it. (The scope lint's
oracle-dirty class: the real page creates claim files.)

NO EXPECTATION IS READ BACK FROM A CONFIG. The fixture carries no ordering of its own: the order is
read out of the returned HTML in document order. Renaming a column, adding one, dropping one, moving
a (N) count onto the wrong column, dropping the count sum, or leaving the sort as it is today all
take this suite red.

RED ON MAIN for the reason in the brief: the page sorts its columns by the dispatcher's priority
ranks, which put Corpus index first and Tasks last -- the reverse of Jas's order. The dispatcher's
own rank table is deliberately NOT stubbed: a fixture that pinned those ranks would pass with the
sort intact, and would break the day someone legitimately re-ranks the queue.
"""
import contextlib
import importlib
import re
import shutil
import sys
import tempfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
FAILS = []


def ck(label, ok, got=""):
    print(("  ok   " if ok else "  BAD  ") + label + ("" if ok else "   got: " + str(got)))
    if not ok:
        FAILS.append(label)


# ---- the fixture: one shape per column, with four distinct counts (2, 2, 1, 2) so a shuffled
# ---- (name, count, body) tuple is caught. Each shape is the one the renderer itself reads, copied
# ---- from its own indexing in feedback.py -- nothing here is invented.
COLS = {"supervision": [("t9001", "Supervised task")], "building": [("t9002", "Building task")]}
STAGES = ["supervision", "building"]
PIPELINE = {"done": [], "caught": [],
            "rca": [("session-review", {"key": "t9010"})],
            "reviewing": [("session-review", {"key": "t9011"})]}
CORPUS = {"transcripts": [{"session": "abcdef1234567890", "origin": "dsh",
                           "at": "2026-10-14T03:27:00Z"}],
          "records": [{"id": "r1", "fact": "f" * 90}],
          "indexed_at": "2026-10-14T03:00:00Z", "next_run": "04:00Z", "running": False}
SCHED = '<div id="sched-controls">SCHED-MARKER</div>'
WANT = [("Tasks", "2"), ("Session reviews", "2"), ("Script reviews", "1"), ("Corpus index", "2")]


def snapshot(base):
    """name -> (size, mtime_ns) for every file under base. Read-only; empty if base is absent."""
    out = {}
    try:
        for p in base.rglob("*"):
            try:
                st = p.stat()
            except OSError:
                continue
            if p.is_file():
                out[str(p)] = (st.st_size, st.st_mtime_ns)
    except OSError:
        pass
    return out


def main():
    sys.path.insert(0, str(ROOT / "scripts"))
    sys.path.insert(0, str(ROOT / "projects" / "app-repo"))
    try:
        import feedback as F
    except Exception as e:                                              # noqa: BLE001
        print(f"FAIL: cannot import feedback from {ROOT / 'projects' / 'app-repo'}: "
              f"{type(e).__name__}: {e}")
        return 1
    # These five are imported by name inside the renderer's own body, so they are patched on the
    # module objects -- patching feedback.<name> would not reach them.
    mods = {}
    for name in ("lane_status", "intake", "manager_next", "session_review"):
        try:
            mods[name] = importlib.import_module(name)
        except Exception as e:                                          # noqa: BLE001
            print(f"FAIL: the page imports {name} inside its body and this suite cannot: "
                  f"{type(e).__name__}: {e}")
            return 1

    live = ROOT / "workspace" / "run-logs" / "scoping-queue"
    before = snapshot(live)
    tmp = Path(tempfile.mkdtemp(prefix="t1154-column-order-"))
    patches = [
        (F, "WORKSPACE", tmp),                    # every live path resolves from this constant
        (F, "_task_board", lambda: (COLS, STAGES, None)),
        (F, "_who", lambda: None),                # the renderer guards every use of this
        (F, "claude_waiting", lambda: []),
        (F, "q4_marks", lambda: {}),
        (F, "backlog_rows", lambda: []),
        (F, "supervision_retry_labels", lambda: {}),
        (F, "_session_pipeline", lambda: PIPELINE),
        (F, "_script_reviews", lambda: ([], ["scripts/review_a.py"], [])),
        (F, "scheduler_controls", lambda *a, **k: SCHED),
        (F.ts, "ordered", lambda *a, **k: []),
        (mods["lane_status"], "corpus_backlog", lambda *a, **k: CORPUS),
        (mods["session_review"], "pending_requests", lambda *a, **k: []),
        (mods["manager_next"], "upcoming", lambda *a, **k: ([], [])),
        (mods["intake"], "pre_scoping_stages", lambda *a, **k: {}),
    ]
    board = ""
    try:
        with contextlib.ExitStack() as stack:
            for obj, name, value in patches:
                if not hasattr(obj, name):
                    print(f"FAIL: {getattr(obj, '__name__', obj)} has no attribute '{name}' -- the "
                          f"page no longer resolves it there, so this fixture needs updating")
                    return 1
                stack.enter_context(mock.patch.object(obj, name, value))
            board = F.board_page()
    except Exception as e:                                              # noqa: BLE001
        print(f"FAIL: the page did not render from the fixture: {type(e).__name__}: {e}")
        return 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    heads = re.findall(r'<div class="khead">([^<(]+?)\s*\((\d+)\)</div>', board)
    print("  heads in document order: " + " | ".join(f"{n} ({c})" for n, c in heads))

    ck("the four columns render in Jas's order: Tasks, Session reviews, Script reviews, Corpus index",
       [n for n, _ in heads] == [n for n, _ in WANT], [n for n, _ in heads])
    ck("each column carries its own count: Tasks 2, Session reviews 2, Script reviews 1, "
       "Corpus index 2", heads == WANT, heads)
    ck("the count line is the sum of the columns: 'In production: 6'",
       "In production: 6" in board, re.findall(r"In production: \d+", board))
    ck("the corpus column is NOT left of the task column (which is what main does)",
       bool(heads) and [n for n, _ in heads].index("Corpus index")
       > [n for n, _ in heads].index("Tasks"), heads)
    ck("the page assembled normally (the scheduler block is present)", "SCHED-MARKER" in board)
    ck("the render wrote nothing into the live workspace tree", snapshot(live) == before,
       sorted(set(snapshot(live)) ^ set(before)))

    print("FAILURES: " + str(len(FAILS)))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
