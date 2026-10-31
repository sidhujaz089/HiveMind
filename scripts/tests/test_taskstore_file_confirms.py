#!/usr/bin/env python3
"""`taskstore.py file` must confirm on success and name the error on an unknown id.

2026-10-21, Phase 3 issue 1: the `file` subcommand was silent on BOTH paths -- a real
`file <id> <area>` that landed printed nothing, and a `file` on an id that is not on disk
also printed nothing and only gave back exit 1. With no output either way, the caller
cannot tell "it worked" from "that id does not exist", and every re-claim of those 38
tasks left behind a "Reassigned ... by taskstore.py: no reason given" trail with no way to
verify the call had taken effect at all. That is the silent-tooling half of the root cause.

Every sibling that changes one task prints a verdict instead: `hold`/`release` print
"<id>: held|released" / "<id>: no such task", `cancel` prints "<id>: no such task" on a
missing id. `file` must join them: a confirmation on success, a named error otherwise,
both on stdout (the same stream the siblings use).

Isolated: the four path globals are re-pointed at a temp tree (the idiom in
test_task_queue.py), so this never touches the live workspace/tasks/.
"""
import contextlib
import io
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "projects" / "app-repo"))
import taskstore as ts  # noqa: E402

fails = []


def run_main(argv):
    """Drive the real CLI path, capturing whatever it prints to stdout, and keep the rc."""
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        rc = ts.main(argv)
    return rc, out.getvalue()


with tempfile.TemporaryDirectory() as td:
    tmp = Path(td)
    real = (ts.TASKS, ts.DONE, ts.QUEUE, ts.LOCK)
    ts.TASKS, ts.DONE = tmp, tmp / "done"
    ts.QUEUE, ts.LOCK = tmp / "QUEUE.md", tmp / ".lock"
    try:
        tid = ts.add("file confirms fixture", "workspace")

        # --- success path: must confirm on stdout AND actually write the metadata -----
        rc, out = run_main(["file", tid, "docs", "chore", "claude"])
        if rc != 0:
            fails.append(f"success path returned {rc}, want 0 (stdout: {out!r})")
        if f"{tid}:" not in out:
            fails.append(f"success path printed no line naming the id: {out!r}")
        if "filed" not in out:
            fails.append(f"success path has no confirmation word: {out!r}")
        if "no such task" in out:
            fails.append("success path wrongly reports the id missing")
        meta, _ = ts.find(tid)
        if meta.get("area") != "docs" or meta.get("kind") != "chore" or meta.get("needs") != "technical": # needs: is a tier (2026-10-29)
            fails.append(f"metadata did not land as filed: {meta}")

        # --- minimal form: area only -- must set area and leave kind/needs untouched --
        # (add seeds every task with kind: unfiled, needs: production; an area-only `file`
        #  must not clobber them, it must only set area.)
        tid2 = ts.add("file confirms minimal", "workspace")
        meta2_before, _ = ts.find(tid2)
        rc2, out2 = run_main(["file", tid2, "docs"])
        if rc2 != 0:
            fails.append(f"minimal success returned {rc2}, want 0 (stdout: {out2!r})")
        if "filed" not in out2 or f"{tid2}:" not in out2:
            fails.append(f"minimal success did not confirm: {out2!r}")
        meta2, _ = ts.find(tid2)
        if meta2.get("area") != "docs":
            fails.append(f"minimal form did not set area: {meta2}")
        if meta2.get("kind") != meta2_before.get("kind") or meta2.get("needs") != meta2_before.get("needs"):
            fails.append(f"area-only `file` clobbered kind/needs: {meta2}")

        # --- unknown id: must name the error on stdout and give a non-zero rc ---------
        rc3, out3 = run_main(["file", "t999999", "docs"])
        if rc3 == 0:
            fails.append(f"unknown id returned 0, want non-zero (stdout: {out3!r})")
        if "no such task" not in out3:
            fails.append(f"unknown id printed no 'no such task' (stdout: {out3!r})")
        if "filed" in out3:
            fails.append("unknown id wrongly reported a successful file")
    finally:
        ts.TASKS, ts.DONE, ts.QUEUE, ts.LOCK = real
        shutil.rmtree(tmp, ignore_errors=True)

for f in fails:
    print("FAIL:", f)
print("test_taskstore_file_confirms:", "FAILED" if fails else "ok")
sys.exit(1 if fails else 0)
