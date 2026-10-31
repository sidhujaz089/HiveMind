#!/usr/bin/env python3
"""What the sandbox VERIFIED must be what the branch DELIVERS.

WHY (env3 probe, finding 32)
----------------------------
`sandbox()` builds the copy under test from `REPO.iterdir()` -- the WORKING TREE, including
uncommitted changes. `write_branch()` delivers to a worktree cut from `HEAD`. When those two
differ, the run verifies one thing and ships another.

The anchored op shape survives this by luck: it carries its own pre-image in `old`, so a
mismatched file fails loudly with anchor-not-found. **The line-range shape does not.** It is
positional, checked only against `1 <= a <= b <= len(lines)`, and it is the shape the model
now prefers (`t145`). Replayed against different content it silently replaces DIFFERENT LINES,
commits them, and the run announces ACCEPTED with a green suite behind it.

The corruption is invisible at every surface a human reads: the terminal says ACCEPTED, the
ledger says ACCEPTED, the task note says the target suite is green -- and all of that is TRUE
of the sandbox, and none of it is true of the branch.

THIS DRIVES THE REAL ENTRY POINT (L-117), in a throwaway clone. `coder.py` computes
`REPO = HERE.parent.parent`, so the clone's own copy operates on the clone -- the live tree,
the live ledger and the live branches are never touched.
"""
import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import coder_harness as H  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
TARGET = "workspace/inbox/README.md"
BRIEF = "mark the inbox README heading"
JUNK = "<!-- uncommitted scratch -->\n<!-- second line -->\n<!-- third line -->\n"

# The heading sits at line 1 of HEAD. Three uncommitted lines on top move it to line 4, and
# move HEAD's line 4 out from under it. An op naming line 4 is therefore CORRECT against the
# working tree and WRONG against HEAD -- which is the whole defect, in one number.
OP_LINE = 4
NEW_HEADING = "# inbox — Claude Chat → Claude Code (probe)"
HEAD_LINE_4 = "this folder in a coding tool."
# Derived the way coder.py derives it, so the test follows the code rather than asserting a
# copy of it -- finding 44 was exactly this test family hardcoding a label that had changed.
LABEL = "brief-" + hashlib.sha256(BRIEF.encode()).hexdigest()[:8]
BRANCH = f"coder/{LABEL}"

FAKE = '''
import json, sys
from http.server import BaseHTTPRequestHandler, HTTPServer
OPS = {"ops": [{"path": %r, "from": %d, "to": %d, "new": %r}],
       "why": "a line-range edit, the shape the model prefers"}
class H(BaseHTTPRequestHandler):
    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        b = json.dumps({"choices": [{"message": {"content": json.dumps(OPS)}}],
                        "usage": {"completion_tokens": 42}}).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
    def log_message(self, *a): pass
HTTPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()
''' % (TARGET, OP_LINE, OP_LINE, NEW_HEADING)


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def main():
    out = []
    tmp = Path(H.sandbox("delivery-check-"))
    clone = tmp / "ai-workspace"
    srv = None
    try:
        # CARRY THE UNCOMMITTED WORK ACROSS. `git clone` copies HEAD, so the clone ran the
        # LAST COMMITTED coder.py and the suite reported the defect still present while the
        # fix sat in the working tree one directory away. That is the same divergence this
        # file exists to catch -- verifying one copy and drawing conclusions about another --
        # and it cost a full red run before it was spotted. Commit the delta so the clone's
        # HEAD *is* the code under test, then introduce the ONE divergence deliberately.
        # ONE implementation, coder_harness.build_clone (2026-09-25): this file carried its
        # own copy of the `git diff HEAD` carry, which drops untracked files -- a task closed
        # with taskstore vanished from the clone and the baseline refused. Fixed there once.
        built, err = H.build_clone(tmp)
        if built is None:
            print(f"  [BAD]  {err}")
            return 1

        # The uncommitted change. This is the ONLY difference between the clone's working
        # tree and its HEAD, so anything that diverges downstream diverges because of it.
        f = clone / TARGET
        f.write_text(JUNK + f.read_text(encoding="utf-8"), encoding="utf-8")

        fake = tmp / "fake.py"
        fake.write_text(FAKE, encoding="utf-8")
        port = free_port()
        srv = subprocess.Popen([sys.executable, str(fake), str(port)],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               preexec_fn=lambda: __import__("ctypes").CDLL("libc.so.6").prctl(1, 15))  # dies with the test (PR_SET_PDEATHSIG)
        for _ in range(100):
            try:
                socket.create_connection(("127.0.0.1", port), 0.2).close()
                break
            except OSError:
                time.sleep(0.1)

        LEDGER = clone / "workspace" / "agent-runs.jsonl"
        before_rows = len([l for l in LEDGER.read_text().splitlines() if l.strip()]) \
            if LEDGER.is_file() else 0

        run = subprocess.run(
            [sys.executable, str(clone / "projects" / "local-inference" / "coder.py"),
             "--brief", BRIEF, "--files", TARGET, "--apply", "--model", "faketest",
             "--attempts", "1", "--reads", "0"],
            capture_output=True, text=True, cwd=str(clone), timeout=900,
            env={**os.environ,
                 "CODER_ENDPOINT": f"http://127.0.0.1:{port}/v1/chat/completions"})

        rows = [json.loads(l) for l in LEDGER.read_text().splitlines() if l.strip()]
        last = rows[-1] if len(rows) > before_rows else {}
        if last.get("outcome") == "REFUSED-TO-START":
            # SAY WHY (2026-10-02): a refusal is a precondition, and the reason was in coder's
            # output, which this suite captured and dropped.
            print("  coder refused to start; the end of its output:")
            print("\n".join("    " + l for l in (run.stdout + run.stderr).splitlines()[-25:]))
        branch = last.get("branch")
        delivered = None
        if branch:
            g = subprocess.run(["git", "-C", str(clone), "show", f"{branch}:{TARGET}"],
                               capture_output=True, text=True)
            delivered = g.stdout if g.returncode == 0 else None

        print("The sandbox verified an edit made against the working tree:")
        # THIS RUN's row, not a row the clone inherited. Prefixing the tempdir with
        # "coder-" tripped coder.py's own anti-nesting guard, the run died before it did
        # anything, and the test read the clone's LAST COMMITTED ledger row -- reporting a
        # clean pass having exercised nothing. The examined-nothing shape, in the test
        # written to catch a delivery defect.
        out.append(("  this run recorded a verdict of its own",
                    bool(last.get("outcome")), True))
        out.append(("  and the sandbox accepted the edit",
                    str(last.get("outcome", "")).startswith("ACCEPTED"), True))
        print(f"         outcome={last.get('outcome')!r} branch={branch!r} exit={run.returncode}")
        if not last:
            print(run.stdout[-2500:]); print("STDERR:", run.stderr[-1500:])

        print("\nWhat reaches the branch must be that same edit, or nothing:")
        if delivered is None:
            # Refusing is a correct outcome -- it delivers nothing rather than something wrong.
            out.append(("  nothing was delivered, and the run says so",
                        run.returncode != 0, True))
        else:
            out.append(("  the branch carries the change that was verified",
                        NEW_HEADING in delivered, True))
            out.append(("  and destroys no line the edit never named",
                        HEAD_LINE_4 in delivered, True))
            out.append(("  and the uncommitted scratch is not in it",
                        "uncommitted scratch" not in delivered, True))
    finally:
        if srv:
            srv.terminate()
        shutil.rmtree(tmp, ignore_errors=True)

    bad = 0
    for label, got, want in out:
        if got == want:
            print(f"  [ok]   {label}: {got}")
        else:
            bad += 1
            print(f"  [BAD]  {label}: expected {want}, got {got}")
    print(f"test_coder_delivers_what_it_verified: {'ok' if not bad else f'{bad} FAILED'}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
