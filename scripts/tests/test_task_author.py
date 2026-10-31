#!/usr/bin/env python3
"""t364 - task store must carry a first-class `author` field (who CREATED the task),
distinct from `source` (the ORIGIN, e.g. "added from the phone").
Proves add() accepts+writes author, and a round-trip preserves it without flagging it as
unrecognised. Temp tree only: ts.TASKS/DONE/QUEUE/LOCK redirected (live workspace/tasks/ untouched).
"""
import contextlib
import io
import sys
import tempfile
from pathlib import Path

APP = Path(__file__).resolve().parents[2] / "projects" / "app-repo"
sys.path.insert(0, str(APP))
import taskstore as ts  # noqa: E402


def main():
    with tempfile.TemporaryDirectory() as tmp:
        tasks = Path(tmp) / "workspace" / "tasks"
        tasks.mkdir(parents=True)
        ts.TASKS, ts.DONE, ts.QUEUE, ts.LOCK = (
            tasks, tasks / "done", tasks / "QUEUE.md", tasks / ".lock")

        # 1. add() must accept and write an author. (RED today: add() has no author param.)
        try:
            tid = ts.add("Show the author on cards", area="unfiled",
                         source="added from the phone", author="Q4")
        except TypeError as e:
            print(f"FAIL add() does not accept an author parameter: {e}")
            return 1
        meta, _ = ts.find(tid)
        if meta.get("author") != "Q4":
            print(f"FAIL add() did not write author=Q4 (got {meta.get('author')!r})")
            return 1
        raw = (tasks / f"{tid}--show-the-author-on-cards.md").read_text(encoding="utf-8")
        if "\nauthor: Q4\n" not in raw:
            print("FAIL author not present in the file front matter")
            return 1
        print("ok   add() writes author")

        # 2. A round-trip must preserve author and must NOT flag it as unrecognised.
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            ts.add_note(tid, "a note from claude-code", who="claude-code")
        meta2, _ = ts.find(tid)
        if meta2.get("author") != "Q4":
            print(f"FAIL round-trip dropped author (got {meta2.get('author')!r})")
            return 1
        if "unrecognised" in buf.getvalue():
            print("FAIL round-trip flagged author as an unrecognised field")
            return 1
        print("ok   round-trip preserves author (not flagged)")

        # 3. Legacy task (no author) is unchanged: no author key, source intact.
        tid2 = ts.add("Legacy task", area="unfiled", source="added from the phone")
        meta3, _ = ts.find(tid2)
        if "author" in meta3:
            print(f"FAIL legacy task unexpectedly has author={meta3.get('author')!r}")
            return 1
        if meta3.get("source") != "added from the phone":
            print("FAIL legacy task lost its source")
            return 1
        print("ok   legacy task unchanged")

    print("\nALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
