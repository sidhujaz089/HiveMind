#!/usr/bin/env python3
"""t1337 (2026-10-19): a model server that does not answer is never an attempt against the brief.
coder._refused() decides whether a failed model call waits and exits 2 (counts against nothing) or
exits 1 (an attempt). Only "Connection refused" counted; NoServer, a dropped connection and a socket
timeout ended runs as exit 1 and were parked. And the job log carries WHY a suite failed, whole.

Run: python3 scripts/tests/test_coder_server_not_answering.py
"""
import http.client
import socket
import sys
import urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "projects" / "local-inference"))
import coder  # noqa: E402


class NoServer(RuntimeError):
    pass


CASES = [
    ("refused", urllib.error.URLError(ConnectionRefusedError(111, "Connection refused")), True),
    ("NoServer", NoServer("'swift' is offered by vllm-swift, and none answers"), True),
    ("dropped", http.client.RemoteDisconnected("Remote end closed connection without response"), True),
    ("socket timeout", socket.timeout("timed out"), True),
    ("urllib timeout", urllib.error.URLError(TimeoutError("timed out")), True),
    ("a bad request is not the server being away", urllib.error.HTTPError("u", 400, "Bad Request", {}, None), False),
    ("a model's own error is an attempt", ValueError("could not parse the answer"), False),
]

bad = 0
for label, exc, want in CASES:
    got = coder._refused(exc)
    ok = got == want
    bad += not ok
    print(f"  {'ok ' if ok else 'BAD'} {label}: {got}")

src = Path(coder.__file__).read_text(encoding="utf-8")
for label, needle in (("failing suite output is logged whole", "output (whole) ---"),
                      ("a refused commit is recorded whole", 'commit refused: {(c.stdout + c.stderr).strip()}")')):
    ok = needle in src
    bad += not ok
    print(f"  {'ok ' if ok else 'BAD'} {label}")
print(f"{len(CASES) + 2 - bad} passed, {bad} failed")
sys.exit(1 if bad else 0)
