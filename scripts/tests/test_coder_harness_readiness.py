#!/usr/bin/env python3
"""A fake model endpoint that never started must not be handed back as ready.

The hourly sweep found test_coder_yield_resume.py failing under load and passing alone
(t1167, 2026-10-14). Both coder suites that drive the real coder.py get their model from
coder_harness.serve_ops (scripts/tests/coder_harness.py:105), so one silent path in that one
helper can put every suite on the live list.

Today serve_ops picks a port with free_port() (:40-45, which CLOSES the socket that bound it
and returns only the number), starts fake.py on it, polls the port for ~10 s (:113-118) and
returns (srv, port) on every path (:119). It never asks whether the process it started is
alive. So two situations are indistinguishable to the caller: a fake that died at bind because
the port was taken -- and a port answered by a FOREIGN listener that grabbed the released port
first (die_with_parent's docstring, :96-99, records 18 leaked fake servers found running on
2026-09-29). coder.py then talks to a dead socket, or to a server serving a DIFFERENT ops
payload, and the suite reports failures that are not its own. Under the sweep (run_tests.py
--jobs 2) the race is won by the other process; run alone it is not -- which is why t1167's
suite was red in the sweep and 11/0 green alone.

The behaviour pinned here, and nothing else:
  * an endpoint that is not the process serve_ops started is REPORTED, never handed back;
  * an endpoint that IS the process serve_ops started answers with the ops it was given --
    the payload is the proof of identity, so a foreign listener cannot satisfy a suite.

No model, no live server, no fixed /tmp, every socket call bounded, no process left running.
"""
import socket
import sys
import tempfile
import shutil
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import coder_harness as H  # noqa: E402

CANARY = "CANARY-t1167-anchor-not-shared"
OPS = {"ops": [{"path": "workspace/inbox/README.md", "old": CANARY, "new": "x"}],
       "why": "the payload is the proof of identity"}
PASSED = FAILED = 0


def report(label, got, want):
    global PASSED, FAILED
    ok = got == want
    PASSED += ok
    FAILED += not ok
    print(f"  [{'ok' if ok else 'BAD'}]  {label}: {got!r}"
          + ("" if ok else f"   (want {want!r})"))


def post(port, body, timeout=5):
    """One POST, read to EOF. Bounded, so a wedged endpoint fails the test, not the suite."""
    s = socket.create_connection(("127.0.0.1", port), timeout=timeout)
    try:
        req = ("POST /v1/chat/completions HTTP/1.1\r\n"
               "Host: 127.0.0.1\r\n"
               "Content-Type: application/json\r\n"
               f"Content-Length: {len(body)}\r\n\r\n{body}")
        s.sendall(req.encode())
        s.settimeout(timeout)
        chunks = []
        while True:
            b = s.recv(4096)
            if not b:
                break
            chunks.append(b)
        return b"".join(chunks), None
    except Exception as exc:
        return b"", f"{type(exc).__name__}: {str(exc)[:120]}"
    finally:
        s.close()


def _cleanup(srv, tmp, sock):
    if sock is not None:
        sock.close()
    if srv is not None and getattr(srv, "poll", lambda: 0)() is None:
        srv.terminate()
        try:
            srv.wait(timeout=10)
        except Exception:
            srv.kill()
    shutil.rmtree(tmp, ignore_errors=True)


def test_endpoint_must_be_the_process_it_started():
    """Occupy the port the fake needs: it cannot bind, so serve_ops must say so.

    The assertion is a socket read, not a process-timing check: the oracle's listener
    accepts and never answers, so a returned (srv, port) is provably not a working
    endpoint. Deterministic on main -- no dependence on when the child's exit is recorded.
    """
    tmp = tempfile.mkdtemp(prefix="t1167-a-")
    sock = socket.socket()
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    sock.listen(1)
    port = sock.getsockname()[1]
    orig = H.free_port
    H.free_port = lambda: port
    srv = None
    try:
        raised = None
        got = None
        try:
            srv, got = H.serve_ops(tmp, OPS)
        except Exception as exc:
            raised = exc
        if raised is not None:
            report("an endpoint it could not start is reported, not handed back", True, True)
            print(f"     serve_ops raised: {type(raised).__name__}: {str(raised)[:140]}")
            return
        body, err = post(port, '{"model":"faketest","messages":[]}')
        answers = (err is None) and (b"choices" in body)
        ours = CANARY in body.decode("utf-8", "replace")
        report("an endpoint it could not start is reported, not handed back", (answers and ours), True)
        if not (answers and ours):
            print("     BAD: serve_ops handed back a port that does NOT answer with the ops")
            print(f"     its caller passed (answers={answers} ours={ours}"
                  + (f" err={err})" if err else ")"))
            print("     coder.py would have talked to something that is not this suite's endpoint")
            print("     -- the silent pass t1167 is about.")
    finally:
        H.free_port = orig
        _cleanup(srv, tmp, sock)


def test_healthy_endpoint_answers_with_its_own_ops():
    """Anti-vacuity: the normal path must keep working AND prove the answer is OURS.

    CANARY lives in the ops object THIS test passed in, and the fake echoes the ops it was
    started with (FAKE at coder_harness.py:24-37). A listener that is not the process
    serve_ops started serves a different payload and fails this assertion -- it is the only
    thing that tells the two cases apart, so an implementation that always raises fails here.
    """
    tmp = tempfile.mkdtemp(prefix="t1167-b-")
    srv = None
    try:
        raised = None
        try:
            srv, port = H.serve_ops(tmp, OPS)
        except Exception as exc:
            raised = exc
        if raised is not None:
            report("a healthy serve_ops still returns (proc, port)", f"raised {type(raised).__name__}", "(proc, port)")
            report("a healthy serve_ops returns a live process", False, True)
            report("the endpoint answers a request", False, True)
            report("the answer carries the ops THAT CALLER passed", False, True)
            return
        report("a healthy serve_ops returns (proc, port)", isinstance(port, int), True)
        report("a healthy serve_ops returns a live process",
               getattr(srv, "poll", lambda: 0)() is None, True)
        body, err = post(port, '{"model":"faketest","messages":[]}')
        report("the endpoint answers a request", b"choices" in body, True)
        report("the answer carries the ops THAT CALLER passed",
               CANARY in body.decode("utf-8", "replace"), True)
        if err:
            print(f"     (post error: {err})")
    finally:
        _cleanup(srv, tmp, None)


if __name__ == "__main__":
    test_endpoint_must_be_the_process_it_started()
    test_healthy_endpoint_answers_with_its_own_ops()
    print(f"\n{PASSED} passed, {FAILED} failed")
    sys.exit(1 if FAILED else 0)
