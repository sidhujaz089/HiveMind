"""dispatch_loop.run_job: a production build is stopped at BUILD_WALL_S, with its whole process group,
and the finished row says so (2026-10-19: three single-file builds ran 2.5-2.8 h)."""
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "scripts"), str(ROOT / "projects" / "local-inference")]
import dispatch_loop as DL  # noqa: E402
import scope_quality  # noqa: E402

PASSED = FAILED = 0


def report(name, got, want):
    global PASSED, FAILED
    ok = got == want
    PASSED += ok
    FAILED += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"  (got {got!r}, want {want!r})"))


tmp = Path(tempfile.mkdtemp())
marker = tmp / "grandchild.pid"
fake = tmp / "coder.py"
# a build that never ends, with a grandchild (a suite) that must die with it
fake.write_text(f"import subprocess, sys, time\n"
                f"c = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(600)'])\n"
                f"open({str(marker)!r}, 'w').write(str(c.pid))\n"
                f"time.sleep(600)\n")
(tmp / "brief-x.txt").write_text("brief")
rows = []
saved = (DL.CODER, DL.REPO, DL.BUILD_WALL_S, DL.record, DL.rows, DL.note_failure_on_task, scope_quality.record,
         DL.CHECKPOINTS)
DL.CODER, DL.BUILD_WALL_S = fake, 2
DL.REPO = tmp
(tmp / "workspace" / "briefs").mkdir(parents=True)
(tmp / "workspace" / "briefs" / "brief-x.txt").write_text("brief")
DL.CHECKPOINTS = tmp / "checkpoints"
DL.record = rows.append
DL.rows = lambda: []
DL.note_failure_on_task = lambda *a, **k: None
scope_quality.record = lambda *a, **k: None
try:
    t0 = time.time()
    rc = DL.run_job({"kind": "production", "chunk": "p1", "brief": "brief-x.txt", "files": "a.py",
                     "target": "scripts/tests/test_a.py", "model": "m", "brief_sha256": "s"})
    took = time.time() - t0
    report("the build is stopped with the timeout exit", rc, DL.BUILD_TIMEOUT_EXIT)
    report("near the cap, not after the build's own 600 s", took < 120, True)
    fin = [r for r in rows if r.get("event") == "finished"]
    report("the finished row records the cap", fin[-1].get("timed_out_s") if fin else None, 2)
    report("and is a failure, not a yield", (fin[-1].get("accepted"), fin[-1].get("yielded")) if fin else None,
           (False, False))
    gpid = int(marker.read_text()) if marker.is_file() else 0
    time.sleep(0.5)
    alive = False
    if gpid:
        try:
            os.kill(gpid, 0)
            alive = Path(f"/proc/{gpid}/stat").read_text().split()[2] != "Z"
        except ProcessLookupError:
            alive = False
    report("its suite (a grandchild) is stopped with it", (gpid > 0, alive), (True, False))
finally:
    (DL.CODER, DL.REPO, DL.BUILD_WALL_S, DL.record, DL.rows, DL.note_failure_on_task, scope_quality.record,
     DL.CHECKPOINTS) = saved
print(f"\n{PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
