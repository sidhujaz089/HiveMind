#!/usr/bin/env python3
"""t1554: an on-demand proxy in front of Swift's vLLM endpoint -- start it, wait for /v1/models,
honour the memory guard, and SAY "loading Swift" instead of failing at once.

WHY THIS EXISTS. vLLM has no load-on-request: Swift is only started by production.target, so while
production is paused every DSH prompt died with "OpenAI-compatible API request to
http://172.18.0.2:8000/v1 failed" (Jas, 2026-10-23 10:55-11:23). Ollama loads on request; Swift takes
~10 minutes and 51 shards. M1's scheduler_commands.swift_on() gives the start; this is the caller.

PROVED WITH FAKES, because the live path costs 72 GiB and ten minutes and the models stay off. Every
external edge is injected: the endpoint's readiness, the start, and the guard. Each case below
changes ONE of them:

  * ready upstream              -> the request is forwarded, and nothing is started;
  * not ready + guard refuses   -> 503 swift_refused naming the measured numbers, and NOTHING started;
  * not ready + guard allows    -> the start is called ONCE, and the answer says "loading Swift";
  * a second request            -> still one start (single flight), and it says how long it has been;
  * ready after loading         -> forwarded again, and a later failure starts a SECOND attempt.

Run: python3 scripts/tests/test_swift_proxy.py   (no pytest; exits non-zero on failure)
"""
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

PASSED = FAILED = 0


def check(label, got, want):
    global PASSED, FAILED
    if got == want:
        PASSED += 1
        print(f"  [ok]   {label}: {got}")
    else:
        FAILED += 1
        print(f"  [BAD]  {label}: expected {want!r}, got {got!r}")


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    def tick(self, s):
        self.t += s


def body_of(resp):
    status, headers, chunks = resp
    data = b"".join(chunks) if not isinstance(chunks, bytes) else chunks
    return status, headers, data.decode("utf-8", "replace")


def main():
    px = _load("swift_proxy", ROOT / "scripts" / "swift_proxy.py")
    clock = Clock()
    calls = {"start": 0, "forward": 0, "guard": 0}
    state = {"ready": False, "start_ok": True, "guard_ok": True, "why": "connection refused"}

    def ready(url):
        return (state["ready"], "" if state["ready"] else state["why"])

    def start():
        calls["start"] += 1
        return (state["start_ok"], "" if state["start_ok"] else "permission denied")

    def guard():
        calls["guard"] += 1
        return (state["guard_ok"],
                "available 6.2 GB - Swift 95 GB leaves -88.8 GB, under the guard's shed line 8 GB"
                if not state["guard_ok"] else "available 120.0 GB - 95 GB leaves 25.0 GB")

    def forward(method, path, headers, body):
        calls["forward"] += 1
        payload = json.dumps({"id": "chatcmpl-fake", "echo": path}).encode()
        return 200, {"Content-Type": "application/json"}, [payload]

    p = px.Proxy(url="http://172.18.0.2:8000", ready=ready, start=start, guard=guard,
                 forward=forward, clock=clock)

    # ---- 1. A READY UPSTREAM IS FORWARDED AND NOTHING IS STARTED
    state["ready"] = True
    status, headers, body = body_of(p.request("POST", "/v1/chat/completions", {}, b"{}"))
    check("a ready upstream is forwarded", status, 200)
    check("the upstream's answer is the proxy's answer", json.loads(body)["id"], "chatcmpl-fake")
    check("nothing was started when the model was already up", calls["start"], 0)

    # ---- 2. NOT READY + THE GUARD WOULD SHED -> REFUSED, AND NOTHING IS STARTED
    state["ready"] = False
    state["guard_ok"] = False
    status, headers, body = body_of(p.request("POST", "/v1/chat/completions", {}, b"{}"))
    check("a guard refusal is a 503", status, 503)
    check("the refusal is typed", json.loads(body)["error"]["type"], "swift_refused")
    check("the refusal quotes the measured numbers", "leaves -88.8 GB" in body, True)
    check("a refusal does NOT start Swift", calls["start"], 0)

    # ---- 3. NOT READY + THE GUARD ALLOWS -> START ONCE, SAY "loading Swift"
    state["guard_ok"] = True
    status, headers, body = body_of(p.request("POST", "/v1/chat/completions", {}, b"{}"))
    check("starting Swift answers 503 (retryable, not a connection error)", status, 503)
    check("the body says loading Swift", "loading Swift" in body, True)
    check("the body is typed", json.loads(body)["error"]["type"], "swift_loading")
    check("it tells the caller to retry", headers.get("Retry-After"), str(px.RETRY_AFTER_S))
    check("the start was called exactly once", calls["start"], 1)

    # ---- 4. A SECOND REQUEST WHILE LOADING IS SINGLE-FLIGHT, AND SAYS HOW LONG
    clock.tick(63)
    status, headers, body = body_of(p.request("POST", "/v1/chat/completions", {}, b"{}"))
    check("a second request does not start Swift again", calls["start"], 1)
    check("it reports the elapsed load", "63 s" in body, True)

    # ---- 5. ONCE READY IT IS FORWARDED AGAIN
    state["ready"] = True
    status, _, body = body_of(p.request("POST", "/v1/chat/completions", {}, b"{}"))
    check("a forwarded request after the load succeeds", status, 200)
    check("the proxy forwarded twice in total", calls["forward"], 2)

    # ---- 6. A LATER FAILURE STARTS A SECOND ATTEMPT (the first one timed out)
    state["ready"] = False
    clock.tick(px.READY_TIMEOUT_S + 1)
    body_of(p.request("POST", "/v1/chat/completions", {}, b"{}"))
    check("a load that never finished is retried", calls["start"], 2)

    # ---- 7. A START THAT FAILS IS SAID, WITH ITS REASON, AND IS NOT RETRIED ON EVERY REQUEST
    p2 = px.Proxy(url="http://172.0.0.1:9", ready=ready, start=start, guard=guard,
                  forward=forward, clock=clock)
    state["start_ok"] = False
    status, _, body = body_of(p2.request("POST", "/v1/chat/completions", {}, b"{}"))
    check("a failed start is a 503", status, 503)
    check("a failed start says why", "permission denied" in body, True)
    check("a failed start is typed", json.loads(body)["error"]["type"], "swift_start_failed")
    n = calls["start"]
    body_of(p2.request("POST", "/v1/chat/completions", {}, b"{}"))
    check("a failed start is not retried on the very next request", calls["start"], n)

    # ---- 8. THE GUARD IS ASKED, NOT ASSUMED, AND ITS ANSWER IS THE ONE USED
    check("the guard was consulted for the decisions that start Swift", calls["guard"] >= 2, True)

    # ---- 9. THE PATH PRODUCTION RUNS: a real socket, a real handler, a fake upstream. The cases
    #         above prove the decision; this proves the DECISION REACHES A CALLER over HTTP, with
    #         the chunked body intact and Retry-After on the wire. trap 4: run it as it is invoked.
    import http.client
    import threading
    from http.server import ThreadingHTTPServer

    state["ready"] = True
    px.Handler.proxy = px.Proxy(url="http://172.18.0.2:8000", ready=ready, start=start, guard=guard,
                                forward=forward, clock=clock)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), px.Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        conn = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=10)
        conn.request("POST", "/v1/chat/completions", body=b"{}",
                     headers={"Content-Type": "application/json"})
        r = conn.getresponse()
        wire = r.read().decode("utf-8", "replace")
        check("a real HTTP caller gets the upstream's status", r.status, 200)
        check("a real HTTP caller gets the upstream's body", json.loads(wire)["id"], "chatcmpl-fake")
        state["ready"] = False
        state["guard_ok"] = False
        conn = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=10)
        conn.request("POST", "/v1/chat/completions", body=b"{}")
        r = conn.getresponse()
        wire = r.read().decode("utf-8", "replace")
        check("a guard refusal reaches the caller as 503", r.status, 503)
        check("the refusal's Retry-After is on the wire", r.getheader("Retry-After"), "20")
        check("the refusal's reason is in the body", "would shed" in wire, True)
    finally:
        srv.shutdown()
        srv.server_close()

    print()
    print(f"{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
