#!/usr/bin/env python3
"""A yielded coder.py run RESUMES where it stopped -- driven end to end (p20, t406).

test_coder_checkpoint.py pins the pieces; this drives the REAL entry point (L-117) in a
throwaway clone, against a fake model that always proposes an edit whose anchor does not
exist, so every attempt fails and is reverted -- which is what makes the conversation the
whole of the run's state.

  run 1: the yield flag for the model is already up. Attempt 1 runs (a run always makes
         progress before it can yield), then at the attempt-2 boundary it checkpoints and
         exits YIELDED (4). The checkpoint names the brief's label and holds attempt 1.
  run 2: flag down, --resume-checkpoint. It says RESUMED, carries attempt 1 forward, and
         continues at attempt 2 rather than starting again at 1. A run that reaches its end
         deletes the spent checkpoint.
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import coder_harness as H  # noqa: E402

BRIEF = "t406 resume probe: change the inbox README heading"
LABEL = "brief-" + hashlib.sha256(BRIEF.encode()).hexdigest()[:8]
OPS = {"ops": [{"path": "workspace/inbox/README.md", "old": "THIS ANCHOR IS NOT IN THE FILE",
                "new": "x"}], "why": "an edit that cannot apply"}
PASSED = FAILED = 0


def report(label, got, want):
    global PASSED, FAILED
    ok = got == want
    PASSED += ok
    FAILED += not ok
    print(f"  [{'ok' if ok else 'BAD'}]  {label}: {str(got)[:90]}"
          + ("" if ok else f"   (want {want!r})"))


def run(clone, port, extra):
    return subprocess.run(
        [sys.executable, str(clone / "projects" / "local-inference" / "coder.py"),
         "--brief", BRIEF, "--model", "faketest", "--attempts", "3", "--reads", "0",
         "--files", "workspace/inbox/README.md", *extra],
        capture_output=True, text=True, cwd=str(clone), timeout=1200,
        env={**os.environ, "CODER_ENDPOINT": f"http://127.0.0.1:{port}/v1/chat/completions"})


def main():
    # ON DISK, NOT IN RAM (2026-10-29): this suite copies the whole repository (1.6 GB) and a run killed at its
    # cap never reaches the rmtree below, so nine copies once sat in /tmp -- which is tmpfs on this box -- and
    # held 15 GB of RAM while the scheduler refused every build for want of memory.
    tmp = H.sandbox(prefix="t406-")
    srv = None
    try:
        clone, err = H.build_clone(tmp)
        if clone is None:
            print(err)
            return 1
        srv, port = H.serve_ops(tmp, OPS)
        flag = clone / "workspace" / "run-logs" / "yield" / "faketest.flag"
        flag.parent.mkdir(parents=True, exist_ok=True)
        flag.write_text("test\n")
        cp = clone / "workspace" / "run-logs" / "checkpoints" / f"{LABEL}--faketest.json"

        print("run 1 -- the yield flag is up:")
        r1 = run(clone, port, [])
        report("exits YIELDED (4)", r1.returncode, 4)
        report("says it yielded", "YIELDED" in r1.stdout, True)
        st = json.loads(cp.read_text()) if cp.is_file() else {}
        report("the checkpoint is where dispatch_loop looks", cp.is_file(), True)
        report("it stopped at the attempt-2 boundary", st.get("n"), 2)
        report("and holds attempt 1", [t.get("n") for t in st.get("tries", [])], [1])
        if r1.returncode != 4:
            print(r1.stdout[-3000:], r1.stderr[-2000:])

        print("\nrun 2 -- flag down, resumed:")
        flag.unlink()
        r2 = run(clone, port, ["--resume-checkpoint", str(cp)])
        report("says RESUMED", "RESUMED from" in r2.stdout, True)
        report("does not run attempt 1 again", "attempt 1 …" in r2.stdout, False)
        report("continues at attempt 2", "attempt 2 …" in r2.stdout, True)
        report("the spent checkpoint is removed", cp.is_file(), False)
        rows = H.ledger_rows(clone)
        last = rows[-1] if rows else {}
        report("the ledger row carries the sandbox baseline as data (t275)",
               isinstance(last.get("baseline_failing"), list), True)
        report("the ledger row carries attempt 1 forward",
               [t.get("n") for t in last.get("trail", [])][:1], [1])
        if FAILED:
            print(r2.stdout[-3000:], r2.stderr[-2000:])
    finally:
        if srv:
            srv.terminate()
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"\n{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
