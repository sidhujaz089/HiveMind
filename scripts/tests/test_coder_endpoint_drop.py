#!/usr/bin/env python3
"""A dropped connection is retried once, not counted as the model failing (t348).

p12 died on attempt 1 with RemoteDisconnected: the server closed the socket before any reply,
coder.py exited 1, and the scheduler counted it against the brief. This drives the REAL entry
point in a clone against a fake server that drops the FIRST request and answers the second.
"""
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import coder_harness as H  # noqa: E402

FAKE = r'''
import json, sys
from http.server import BaseHTTPRequestHandler, HTTPServer
OPS = {"ops": [{"path": "workspace/inbox/README.md", "old": "NOT IN THE FILE", "new": "x"}], "why": "x"}
N = [0]
class H(BaseHTTPRequestHandler):
    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        N[0] += 1
        if N[0] == 1:
            self.close_connection = True
            self.connection.close()          # RemoteDisconnected on the client
            return
        b = json.dumps({"choices": [{"message": {"content": json.dumps(OPS)}}],
                        "usage": {"completion_tokens": 42}}).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
    def log_message(self, *a): pass
HTTPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()
'''
FAILED = 0


def report(label, got, want):
    global FAILED
    ok = got == want
    FAILED += not ok
    print(f"  [{'ok' if ok else 'BAD'}]  {label}: {got!r}" + ("" if ok else f"  (want {want!r})"))


def main():
    tmp = H.sandbox("t348-")
    srv = None
    try:
        clone, err = H.build_clone(tmp)
        if clone is None:
            print(err)
            return 1
        fake = Path(tmp) / "drop.py"
        fake.write_text(FAKE)
        port = H.free_port()
        srv = subprocess.Popen([sys.executable, str(fake), str(port)], stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, preexec_fn=H.die_with_parent)
        for _ in range(100):
            try:
                socket.create_connection(("127.0.0.1", port), 0.2).close()
                break
            except OSError:
                time.sleep(0.1)
        r = subprocess.run(
            [sys.executable, str(clone / "projects/local-inference/coder.py"), "--brief",
             "t348 probe: change the inbox README", "--model", "faketest", "--attempts", "1",
             "--reads", "0", "--files", "workspace/inbox/README.md"],
            capture_output=True, text=True, cwd=str(clone), timeout=1200,
            env={**os.environ, "CODER_ENDPOINT": f"http://127.0.0.1:{port}/v1/chat/completions"})
        report("the drop is said and retried", "connection dropped" in r.stdout, True)
        report("the retried attempt reached the model's answer", "refused (anchor not found" in r.stdout, True)
        report("no ERROR exit from the drop", "ERROR RemoteDisconnected" in r.stdout, False)
        if FAILED:
            print(r.stdout[-2500:], r.stderr[-1500:])
        # A SERVER THAT IS NOT THERE AT ALL (2026-10-02: p242 asked during an Ollama restart, got
        # "Connection refused", exited 1 and was parked). It is waited for, then REFUSED (exit 2).
        dead = H.free_port()
        r = subprocess.run(
            [sys.executable, str(clone / "projects/local-inference/coder.py"), "--brief",
             "t348 probe: change the inbox README", "--model", "faketest", "--attempts", "1",
             "--reads", "0", "--files", "workspace/inbox/README.md"],
            capture_output=True, text=True, cwd=str(clone), timeout=1200,
            env={**os.environ, "CODER_ENDPOINT": f"http://127.0.0.1:{dead}/v1/chat/completions",
                 "CODER_REFUSED_WAIT_S": "1"})
        report("a refused connection is waited for", "server not answering" in r.stdout, True)
        report("  and ends as a refusal (exit 2), not an attempt (exit 1)", r.returncode, 2)
        if r.returncode != 2:
            print(r.stdout[-2500:], r.stderr[-1500:])
    finally:
        if srv:
            srv.terminate()
        shutil.rmtree(tmp, ignore_errors=True)
    print("test_coder_endpoint_drop:", "FAILED" if FAILED else "ok")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
