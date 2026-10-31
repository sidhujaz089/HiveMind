#!/usr/bin/env python3
"""Drive the REAL coder.py against a throwaway clone.

Not a suite -- the shared machinery behind the suites that need a whole `coder.py` run.

`coder.py` computes `REPO = HERE.parent.parent`, so a clone's own copy operates entirely on
the clone: the live working tree, the live ledger (`workspace/agent-runs.jsonl`) and the live
`coder/*` branches are never touched. That is what makes it safe to drive the real entry point
(`L-117`) instead of testing helpers while the call site stays broken.

One copy, not one per suite. Two suites each holding their own clone-and-run helper is the
producer/consumer drift that `L-114` and `L-115` are about.
"""
import os
import socket
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

FAKE = '''
import json, sys
from http.server import BaseHTTPRequestHandler, HTTPServer
OPS = json.loads(sys.argv[2])
class H(BaseHTTPRequestHandler):
    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        b = json.dumps({"choices": [{"message": {"content": json.dumps(OPS)}}],
                        "usage": {"completion_tokens": 42}}).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
    def log_message(self, *a): pass
HTTPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()
'''


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def sandbox(prefix="coder-test-", tmpdir=None):
    """A temp dir for a suite's repo clone, ON DISK even when /tmp is RAM.

    MEASURED 2026-10-29: `/tmp` on this box is tmpfs (61 GB, 23 GB used). A clone test killed at its cap never
    reaches its `finally: rmtree`, and nine such copies -- 1.6 GB each -- were sitting in RAM, holding 15 GB of
    a 122 GB box while the scheduler's pressure gate refused every build. This repository's own rule is "never
    write under /tmp: it is RAM"; a suite that clones 1.6 GB there breaks it once per killed run.

    Order: an explicit `tmpdir`, then TMPDIR if it is NOT tmpfs, then ~/.cache/hivemind-tmp. A tmpfs is
    recognised by the filesystem type, not by the path, so a box that mounts /tmp on disk still gets /tmp.
    """
    import tempfile
    from pathlib import Path as _P
    if tmpdir:
        return tempfile.mkdtemp(prefix=prefix, dir=str(tmpdir))
    env = os.environ.get("TMPDIR") or ""
    if env and not _is_tmpfs(_P(env)):
        return tempfile.mkdtemp(prefix=prefix, dir=env)
    disk = _P.home() / ".cache" / "hivemind-tmp"
    disk.mkdir(parents=True, exist_ok=True)
    return tempfile.mkdtemp(prefix=prefix, dir=str(disk))


def _is_tmpfs(path):
    """True when `path` sits on a tmpfs (RAM). Unreadable mounts are treated as NOT tmpfs -- the caller then
    keeps the user's own TMPDIR rather than moving their files on a guess."""
    try:
        best = ""
        with open("/proc/mounts", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                parts = line.split()
                if len(parts) < 3:
                    continue
                mount, fstype = parts[1], parts[2]
                if (str(path) == mount or str(path).startswith(mount.rstrip("/") + "/")) \
                        and len(mount) >= len(best):
                    best, kind = mount, fstype
        return best != "" and kind == "tmpfs"
    except OSError:
        return False


def build_clone(tmp):
    """A clone whose HEAD is the code UNDER TEST, not the code last committed.

    `git clone` copies HEAD, so the first version of this ran the last committed coder.py and
    reported a defect still present while its fix sat in the working tree one directory away
    -- verifying one copy and drawing conclusions about another, which is the exact shape of
    the defect these suites were written for. Carry the uncommitted delta across and commit it.

    NOTE the prefix the caller must avoid: a tempdir under `/tmp/coder-` trips coder.py's own
    anti-nesting guard, the run dies before doing anything, and a suite that reads only the
    ledger's last row reports a clean pass having exercised nothing.
    """
    # NEVER IN RAM (2026-10-30). sandbox() existed from 10-29, yet five suites still passed it a plain
    # tempfile.mkdtemp() -- /tmp, tmpfs -- and four killed runs left 6.2 GB there. The gate is here, where
    # every clone is made, so a suite that skips sandbox() fails at once instead of filling RAM.
    if _is_tmpfs(Path(tmp).resolve()):
        return None, f"refusing to clone into {tmp}: it is tmpfs (RAM); make it with coder_harness.sandbox()"
    clone = Path(tmp) / "ai-workspace"
    r = subprocess.run(["git", "clone", "--quiet", "--shared", str(REPO), str(clone)],
                       capture_output=True, text=True)
    if r.returncode:
        return None, f"could not clone: {r.stderr[:300]}"
    # BYTES, NOT TEXT (2026-10-07). text=True turns \r\n into \n, so a tracked file with a carriage
    # return (t680, written through Chat's MCP door) made a patch that could not apply, and FIVE
    # coder suites failed in 3 s with "could not carry the working tree across" -- every run.
    #
    # COPY THE FILES, DO NOT APPLY A PATCH (2026-10-30). The live tree's uncommitted diff was 109 MB over
    # 733 files -- mostly service-written ledgers and the 85 MB symbol index -- and `git apply` of it ran past
    # the hunt's 600 s cap every time (40 rc-124 rows in workspace/flake-hunt.jsonl). The clone needs the
    # tree's BYTES, which is what a patch reconstructs the slow way: copy each changed path, drop each deleted.
    changed = subprocess.run(["git", "-C", str(REPO), "diff", "--name-only", "--no-renames", "-z", "HEAD"],
                             capture_output=True)
    if changed.returncode:
        return None, f"could not list the working tree's changes: {changed.stderr.decode('utf-8', 'replace')}"
    for rel in filter(None, changed.stdout.decode("utf-8", "surrogateescape").split("\0")):
        src, dst = REPO / rel, clone / rel
        try:
            if src.is_dir() and not src.is_symlink():
                continue                                    # a submodule pointer: git, not bytes
            if src.is_symlink() or src.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                if dst.is_symlink() or dst.exists():
                    dst.unlink()
                shutil.copy2(src, dst, follow_symlinks=False)
            elif dst.is_symlink() or dst.exists():
                dst.unlink()
        except OSError as e:
            return None, f"could not carry the working tree across: {rel}: {e}"
    # UNTRACKED FILES ARE PART OF THE WORKING TREE TOO (2026-09-25). `git diff HEAD` carries a
    # tracked file's deletion but not the untracked file it moved to, so a task closed with
    # taskstore (tasks/tNNN.md -> tasks/done/tNNN.md, uncommitted) VANISHED from the clone:
    # test_every_chunk_has_task then failed inside coder.py's baseline, the baseline refused,
    # and both coder_* suites went red on a half-carried move. Copy every untracked,
    # non-ignored file across as well (31 MB on the day it was found).
    others = subprocess.run(["git", "-C", str(REPO), "ls-files", "--others",
                             "--exclude-standard", "-z"], capture_output=True).stdout
    for rel in filter(None, others.decode("utf-8", "surrogateescape").split("\0")):
        src, dst = REPO / rel, clone / rel
        if src.is_file() and not src.is_symlink():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
    subprocess.run(["git", "-C", str(clone), "add", "-A"], capture_output=True, text=True)
    subprocess.run(["git", "-C", str(clone), "-c", "core.hooksPath=/dev/null",
                    "commit", "-qm", "working tree under test"],
                   capture_output=True, text=True)
    return clone, ""


def die_with_parent():
    """preexec_fn: the fake server gets SIGTERM when the test that started it dies, however it dies.
    A test killed from outside (a run_tests or merge_gate cap) never reaches its finally, and its
    fake.py lived on: 18 were found running on 2026-09-29, the oldest 44 h."""
    import ctypes
    import signal as _signal
    ctypes.CDLL("libc.so.6", use_errno=True).prctl(1, _signal.SIGTERM)   # 1 = PR_SET_PDEATHSIG


def serve_ops(tmp, ops_obj):
    """A fake completions endpoint that answers every request with one fixed patch."""
    import json as _json
    fake = Path(tmp) / "fake.py"
    fake.write_text(FAKE, encoding="utf-8")
    port = free_port()
    srv = subprocess.Popen([sys.executable, str(fake), str(port), _json.dumps(ops_obj)],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, preexec_fn=die_with_parent)
    for _ in range(100):
        try:
            socket.create_connection(("127.0.0.1", port), 0.2).close()
            break
        except OSError:
            time.sleep(0.1)
    return srv, port


def run_coder(clone, port, brief, files=(), extra=()):
    return subprocess.run(
        [sys.executable, str(clone / "projects" / "local-inference" / "coder.py"),
         "--brief", brief, "--model", "faketest", "--attempts", "1", "--reads", "0",
         *(("--files", *files) if files else ()), *extra],
        capture_output=True, text=True, cwd=str(clone), timeout=900,
        env={**os.environ, "CODER_ENDPOINT": f"http://127.0.0.1:{port}/v1/chat/completions"})


def ledger_rows(clone):
    import json as _json
    f = clone / "workspace" / "agent-runs.jsonl"
    if not f.is_file():
        return []
    return [_json.loads(l) for l in f.read_text().splitlines() if l.strip()]
