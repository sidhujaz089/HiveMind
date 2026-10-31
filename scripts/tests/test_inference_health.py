#!/usr/bin/env python3
"""inference_health must call the INFERENCE PATH, not the model list.

Run:  python3 scripts/tests/test_inference_health.py
Exit 0 if all cases pass, 1 otherwise.

THE CASE THIS SUITE EXISTS FOR, and t283 says so explicitly: "Its oracle has to include an
endpoint that serves /api/tags normally and hangs on /api/chat, or the fixture is
indistinguishable from the check that just failed."

That is not a hypothetical. On 2026-09-11/12 /api/tags answered instantly with 16 models while
/api/chat timed out on every one, and the model list was read as health three separate times in
one session. A health check that passes when only /api/tags answers would reproduce the exact
weekend it was written to prevent -- so case 2 below is the whole point, and a version of this
check that merely pings /api/tags fails it.

Every case runs against a LOCAL FIXTURE SERVER, not the real ollama: a suite that needs the
real server to be broken in order to test the broken case can never run.
"""
import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

import inference_health as ih                                        # noqa: E402

PASSED = FAILED = 0


def check(label, got, want):
    global PASSED, FAILED
    if got == want:
        PASSED += 1
        print(f"  [ok]   {label}")
    else:
        FAILED += 1
        print(f"  [BAD]  {label}: got {got!r}, want {want!r}")


def make_server(chat_behaviour):
    """A fake ollama. `chat_behaviour` is 'answer', 'hang', or 'error'."""
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _json(self, code, obj):
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == "/api/tags":
                self._json(200, {"models": [{"name": "fake:1b"}, {"name": "fake:2b"}]})
            elif self.path == "/api/ps":
                self._json(200, {"models": []})
            else:
                self._json(404, {"error": "no"})

        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            self.rfile.read(n)
            if chat_behaviour == "hang":
                # THE WEDGED SLOT. Accept the request and never answer -- exactly what a
                # runaway generation holding the one -np 1 slot looks like to a caller.
                time.sleep(30)
                return
            if chat_behaviour == "error":
                self._json(500, {"error": "boom"})
                return
            self._json(200, {"message": {"role": "assistant", "content": "ok"},
                             "done_reason": "stop", "eval_count": 5,
                             "load_duration": 1_000_000_000})

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def with_host(srv, fn):
    """Point inference_health at the fixture for one call, then put it back."""
    old = ih.HOST
    ih.HOST = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        return fn()
    finally:
        ih.HOST = old


def main():
    # 1. A SERVER THAT ANSWERS IS UP, and the elapsed time is reported.
    srv = make_server("answer")
    r = with_host(srv, lambda: ih.probe(model="fake:1b", timeout=10))
    srv.shutdown()
    check("1. a server that answers /api/chat is UP", r.get("up"), True)
    check("1b. and the elapsed seconds are reported", isinstance(r.get("seconds"), float), True)
    check("1c. and it reports done_reason", r.get("done_reason"), "stop")

    # ------------------------------------------------------------------
    # 2. THE CASE t283 NAMES. /api/tags answers normally; /api/chat hangs.
    #    A check that pings the model list passes here. This one must FAIL.
    # ------------------------------------------------------------------
    srv = make_server("hang")
    r = with_host(srv, lambda: ih.probe(model="fake:1b", timeout=3))
    check("2. tags fine + chat hanging is DOWN, not up", r.get("up"), False)
    check("2b. and it confirms the model LIST was reachable", r.get("tags_ok"), True)
    check("2c. and it names the wedged-slot signature by name",
          "WEDGED-SLOT SIGNATURE" in (r.get("say") or ""), True)
    check("2d. and it does not silently swallow the failure",
          bool(r.get("error")), True)

    # 3. require_up() must REFUSE, because callers refusing is the point.
    raised = False
    try:
        with_host(srv, lambda: ih.require_up(model="fake:1b", timeout=3))
    except ih.ServerDown as e:
        raised = True
        # The exception carries the whole probe, not a message fragment.
        check("3b. ServerDown carries the full probe result",
              isinstance(getattr(e, "result", None), dict), True)
    srv.shutdown()
    check("3. require_up() raises ServerDown when the path is down", raised, True)

    # 4. A 500 IS DOWN TOO -- an error reply is not an answer.
    srv = make_server("error")
    r = with_host(srv, lambda: ih.probe(model="fake:1b", timeout=5))
    srv.shutdown()
    check("4. an HTTP error on /api/chat is DOWN", r.get("up"), False)

    # 5. NOTHING LISTENING AT ALL is down, and says so DIFFERENTLY from a wedged slot --
    #    the two need different actions and confusing them cost four dispatches.
    old = ih.HOST
    ih.HOST = "http://127.0.0.1:9"          # discard port; nothing serves it
    try:
        r = ih.probe(model="fake:1b", timeout=3)
    finally:
        ih.HOST = old
    check("5. a dead server is DOWN", r.get("up"), False)
    check("5b. and it is NOT called a wedged slot",
          "WEDGED-SLOT SIGNATURE" in (r.get("say") or ""), False)
    check("5c. and it says the server is not there at all",
          "not there at all" in (r.get("say") or ""), True)

    # 6. require_up() RETURNS the probe when healthy, so a caller can log the seconds.
    srv = make_server("answer")
    ok = with_host(srv, lambda: ih.require_up(model="fake:1b", timeout=10))
    srv.shutdown()
    check("6. require_up returns the probe on success", ok.get("up"), True)

    print(f"test_inference_health: {PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
