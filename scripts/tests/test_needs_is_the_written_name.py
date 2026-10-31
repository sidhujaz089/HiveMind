#!/usr/bin/env python3
"""The task file says `needs:` now, and an old file saying `assigned:` still READS the same.

t9540: one value had two names -- the file said `assigned:` while every reader asked for `needs:`. That is the
fault this work exists to remove, and it is not theoretical: after the tier migration, `assigned_worker.pending()`
compared the wrong name and the Manager's queue went silent.

Two properties, in opposite directions:
  * WRITE: _rewrite() emits `needs:` and no `assigned:` line.
  * READ: find() still reads a file that only has `assigned:` -- so the rename can happen file by file without a
    window where anything is unreadable.
"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "projects" / "app-repo"))


def main():
    import taskstore as T
    fails = []

    def check(label, ok, detail=""):
        print(f"  [{'ok' if ok else 'FAIL'}]  {label}" + ("" if ok else f"\n        {detail}"))
        if not ok:
            fails.append(label)

    d = Path(tempfile.mkdtemp())
    real_tasks = T.TASKS
    T.TASKS = d                                  # the store's own directory, isolated
    try:
        # 1. WRITE: a task written now says needs:
        p1 = d / "t9001--written.md"
        p1.write_text("---\nid: t9001\ntitle: w\narea: workspace\nkind: bug\nstatus: queued\n"
                      "assigned: technical\nsource: x\ncreated: 2026-10-29T000000Z\n---\n\nbody\n")
        meta, body = T.find("t9001")
        T._rewrite(meta, body)
        text = p1.read_text()
        check("the file is written with needs:", "\nneeds: technical\n" in text, text[:200])
        check("  and with no assigned: line", "\nassigned:" not in text, text[:200])

        # 2. READ an OLD file that still says assigned:
        p2 = d / "t9002--old.md"
        p2.write_text("---\nid: t9002\ntitle: o\narea: workspace\nkind: bug\nstatus: queued\n"
                      "assigned: rework\nsource: x\ncreated: 2026-10-29T000000Z\n---\n\nbody\n")
        meta2, _ = T.find("t9002")
        check("an old file's assigned: still reads as needs:", meta2.get("needs") == "rework",
              meta2.get("needs"))

        # 3. THE ROUND TRIP migrates the key without changing the value.
        T._rewrite(meta2, "body\n")
        meta3, _ = T.find("t9002")
        check("rewriting an old file migrates the key and keeps the value",
              meta3.get("needs") == "rework" and "\nassigned:" not in p2.read_text(),
              p2.read_text()[:160])
        check("  `assigned` is not carried through as an extra field",
              p2.read_text().count("assigned:") == 0, p2.read_text()[:200])
    finally:
        T.TASKS = real_tasks

    print("test_needs_is_the_written_name:", "FAILED" if fails else "ok")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
