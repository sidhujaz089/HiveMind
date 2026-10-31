#!/usr/bin/env python3
"""The handover check must fail a session that committed work without reading it.

A print is not an enforcement. session_start.py names the newest handover and AGENTS.md says
to read it in full; nothing checked, and a rule enforced by asking politely is not enforced.

The negative case is the whole test: a checker that only ever says "ok" on the live session --
which HAS read the handover -- cannot be told from one that always says ok.
"""
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
CHECK = REPO / "scripts" / "check_handover_read.py"


def run(transcript_text, commits=3):
    """`commits` is passed EXPLICITLY (Q4's audit). Reading it from git made this test a
    canary for commit recency: on a quiet day the check correctly says "no work done, nothing
    to fail", the test saw rc==0 and reported FAILED -- for a reason unrelated to the check."""
    with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as f:
        f.write(transcript_text)
        path = f.name
    try:
        r = subprocess.run([sys.executable, str(CHECK), "--transcript", path,
                            "--assume-commits", str(commits)],
                           capture_output=True, text=True, timeout=120, cwd=str(REPO))
        return r.returncode, r.stdout + r.stderr
    finally:
        Path(path).unlink(missing_ok=True)


def main():
    fails = []

    # 1. A TRANSCRIPT WITH NO SIGN OF THE HANDOVER MUST FAIL -- provided work was committed,
    #    which it has been in this repository today.
    rc, out = run('{"type":"user","message":{"content":"do some unrelated thing"}}\n' * 5)
    caught = rc != 0 and "without ever opening the handover" in out
    print(f"  [{'ok' if caught else 'BAD'}]    a working session that never opened the "
          f"handover FAILS (rc={rc})")
    if not caught:
        fails.append(f"the check passed a session that never read the handover:\n{out[-300:]}")

    # 2. A TRANSCRIPT THAT DID READ IT MUST PASS.
    rc, out = run('{"type":"assistant","content":"cat workspace/HANDOVER-2026-09-03.md"}\n')
    ok = rc == 0 and "the handover was opened" in out
    print(f"  [{'ok' if ok else 'BAD'}]    a session that opened it passes (rc={rc})")
    if not ok:
        fails.append(f"the check failed a session that DID read the handover:\n{out[-300:]}")

    # 3. AND IT MUST NOTICE THE TAIL SEPARATELY. The handover is a summary; the tail is where
    #    the half-finished thing and the last correction live.
    rc, out = run('{"type":"assistant","content":"cat workspace/HANDOVER-2026-09-03.md"}\n')
    noted = "the previous session's tail was not read" in out
    print(f"  [{'ok' if noted else 'BAD'}]    it notes separately when the tail was not read")
    if not noted:
        fails.append("the check does not distinguish reading the handover from reading the "
                     "tail; they carry different things")

    # 4. A SESSION THAT DID NO WORK OWES NOBODY A HANDOVER READ. A blocker there would be a
    #    check people learn to skip, and this is the case Q4 showed was being tested by
    #    accident rather than on purpose.
    rc, out = run('{"type":"user","message":{"content":"unrelated"}}\n', commits=0)
    lenient = rc == 0 and "has not done work" in out
    print(f"  [{'ok' if lenient else 'BAD'}]    a session with no commits is not failed "
          f"(rc={rc})")
    if not lenient:
        fails.append("the check fails a session that did no work; that is a check people "
                     "learn to skip")

    for x in fails:
        print("FAIL:", x)
    print("test_handover_read:", "FAILED" if fails else "ok")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
