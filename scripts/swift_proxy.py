#!/usr/bin/env python3
"""An on-demand proxy in front of Swift's vLLM endpoint: start it, wait for /v1/models, and SAY
"loading Swift" instead of failing at once (t1554).

    python3 scripts/swift_proxy.py --port 8000          # the proxy
    python3 scripts/swift_proxy.py --status             # what it would do right now

WHY. vLLM has no load-on-request. Swift (swift.service -> serve.py -> a docker container on
172.18.0.2:8000) is only ever started by production.target, so while production is paused every DSH
prompt died with "OpenAI-compatible API request to http://172.18.0.2:8000/v1 failed" (Jas,
2026-10-23 10:55-11:23, until he pressed Resume). Ollama loads on request; Swift takes ~10 minutes and
51 shards, so "fail at once" is the wrong answer and "start it and hang" is not much better.

WHAT IT DOES, per request:
  1. the upstream answers GET /v1/models  -> forward the request untouched;
  2. it does not, and the memory guard would shed Swift -> 503 swift_refused, naming the numbers,
     and NOTHING is started (starting a 95 GB model under the guard's shed line is how it gets killed
     ten minutes in, which is the worst of both);
  3. it does not, and there is room -> scheduler_commands.swift_on() (M1: starts the unit, keeps every
     pause), then 503 swift_loading with Retry-After. ONE start is in flight at a time; a caller that
     keeps asking is told how long the load has been running, not made to wait on a socket.

THE UPSTREAM ADDRESS. serve.py's container is on a docker bridge and publishes no host port
(serve.py: "-p publishes NOTHING" on an --internal network), so the host's :8000 is free: the proxy can
listen on it and forward to the container's own address. Pointing a client at the proxy is a one-line
change on that client (DSH's provider URL); this script does not edit anybody's config.

PROVED WITH FAKES. The endpoint, the start, the guard and the forward are all constructor arguments;
scripts/tests/test_swift_proxy.py changes one at a time and asserts what a caller sees. Nothing here
is exercised against the live line.
"""
import argparse
import http.client
import json
import sys
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "projects" / "local-inference"))

READY_TIMEOUT_S = 20 * 60      # a load that has not answered in 20 minutes gets a fresh attempt
START_BACKOFF_S = 60           # a start that FAILED is not hammered once per request
RETRY_AFTER_S = 20             # what the caller is told to wait; the load itself takes ~10 min
DEFAULT_MODEL = "swift"


def upstream_url():
    """The container's own address, from serve.py (it is the only thing that knows the name)."""
    import serve
    return serve.endpoint().rstrip("/")


def upstream_ready(url, timeout=3.0):
    """(ready, why). READY is /v1/models answering with a model list -- the same signal the Lanes tab
    now shows (t1555). While the shards load, nothing is listening on the port."""
    try:
        with urllib.request.urlopen(f"{url.rstrip('/')}/v1/models", timeout=timeout) as f:
            body = json.loads(f.read(200000) or b"{}")
    except Exception as e:                                          # noqa: BLE001 -- said, not swallowed
        return False, f"{type(e).__name__}: {e}"
    data = body.get("data") if isinstance(body, dict) else None
    if not isinstance(data, list):
        return False, "answered without a model list"
    return True, ""


def need_gb(model=DEFAULT_MODEL):
    """Swift's declared footprint, from SERVERS.json -- the number the guard must make room for."""
    path = REPO / "projects" / "local-inference" / "models" / "SERVERS.json"
    try:
        servers = json.loads(path.read_text(encoding="utf-8")).get("servers") or {}
    except (OSError, ValueError) as e:                              # noqa: BLE001 -- said, not swallowed
        print(f"  NOTE: SERVERS.json unreadable ({type(e).__name__}: {e}); using 95 GB", file=sys.stderr)
        return 95.0
    for entry in servers.values():
        m = (entry.get("models") or {}).get(model)
        if m:
            return float(m.get("footprint_gb") or 95.0)
    return 95.0


def guard_check(need=None, avail=None, levels=None):
    """(ok, reason) -- refuse when starting Swift would put the box under the guard's SHED line.

    Asked before every start, because the guard's own answer is the only one that matters: it would
    shed at shed_gb, and a model that is started under that line is killed minutes later."""
    import memory_guard as mg
    avail = mg.available_gb() if avail is None else avail
    L = mg.levels() if levels is None else levels
    need = need_gb() if need is None else need
    after = avail - need
    if after < float(L["shed_gb"]):
        return False, (f"available {avail:.1f} GB - Swift's {need:.0f} GB leaves {after:.1f} GB, "
                       f"under the memory guard's shed line {float(L['shed_gb']):g} GB")
    return True, f"available {avail:.1f} GB - {need:.0f} GB leaves {after:.1f} GB"


def default_start():
    """M1's swift_on(): starts swift.service, keeps every pause as it was."""
    import scheduler_commands
    return scheduler_commands.swift_on()


def default_forward(method, path, headers, body):
    """(status, headers, [chunks]) from the real upstream. Read in chunks so a streamed completion is
    passed through as it arrives rather than buffered for ten minutes."""
    parts = urllib.parse.urlsplit(upstream_url())
    conn = http.client.HTTPConnection(parts.hostname, parts.port or 80, timeout=600)
    try:
        conn.request(method, path, body=body, headers=dict(headers or {}))
        r = conn.getresponse()
        return r.status, dict(r.getheaders()), iter(lambda: r.read(65536), b"")
    except Exception:
        conn.close()
        raise


class Proxy:
    """The decision, with every edge injectable, so the oracle can be a fake and the caller's
    experience is what is asserted."""

    RETRY_AFTER_S = RETRY_AFTER_S
    READY_TIMEOUT_S = READY_TIMEOUT_S
    START_BACKOFF_S = START_BACKOFF_S

    def __init__(self, url=None, ready=None, start=None, guard=None, forward=None, clock=time.time):
        self._url = url
        self._ready = ready or upstream_ready
        self._start = start or default_start
        self._guard = guard or (lambda: guard_check())
        self._forward = forward or default_forward
        self._clock = clock
        self.attempt_at = None          # when the last start was asked for
        self.not_before = 0.0           # do not ask again before this
        self.start_error = ""           # the last start's failure, "" when it was accepted
        self._lock = threading.Lock()

    def url(self):
        return self._url or upstream_url()

    def status(self):
        """(ready, why) -- what the proxy would find if a request arrived now."""
        try:
            return self._ready(self.url())
        except Exception as e:                                      # noqa: BLE001 -- said, not swallowed
            return False, f"{type(e).__name__}: {e}"

    @staticmethod
    def _answer(kind, message, retry=RETRY_AFTER_S):
        body = json.dumps({"error": {"message": message, "type": kind, "code": 503}}).encode()
        return 503, {"Content-Type": "application/json", "Retry-After": str(retry)}, [body]

    def request(self, method, path, headers=None, body=b""):
        """(status, headers, chunks). Forwards when the model is up; otherwise 503 with a typed error
        that says either why it refused, why it could not start, or how long it has been loading."""
        ok, why = self.status()
        if ok:
            return self._forward(method, path, headers or {}, body)
        with self._lock:
            now = self._clock()
            if self.attempt_at is not None and now < self.not_before:
                elapsed = now - self.attempt_at
                if self.start_error:
                    return self._answer(
                        "swift_start_failed",
                        f"Swift did not start: {self.start_error}. That attempt was {elapsed:.0f} s "
                        f"ago; it will be tried again in {max(0.0, self.not_before - now):.0f} s.",
                        retry=max(1.0, self.not_before - now))
                return self._answer(
                    "swift_loading",
                    f"loading Swift: swift.service was started {elapsed:.0f} s ago and /v1/models has "
                    f"not answered yet ({why or 'no answer'}). A 72 GiB load takes about 10 minutes; "
                    f"retry in {RETRY_AFTER_S} s.")
            allowed, greason = self._guard()
            if not allowed:
                return self._answer(
                    "swift_refused",
                    f"refusing to start Swift: the memory guard would shed it -- {greason}. Free "
                    f"memory, or Resume when there is room; retry in {RETRY_AFTER_S} s.")
            accepted, err = self._start()
            self.attempt_at = now
            if not accepted:
                self.start_error = err or "the start command failed"
                self.not_before = now + START_BACKOFF_S
                return self._answer(
                    "swift_start_failed",
                    f"Swift did not start: {self.start_error}. It will be tried again in "
                    f"{START_BACKOFF_S} s.", retry=START_BACKOFF_S)
            self.start_error = ""
            self.not_before = now + READY_TIMEOUT_S
            return self._answer(
                "swift_loading",
                f"loading Swift: swift.service was started just now ({greason}). /v1/models does not "
                f"answer yet; a 72 GiB load takes about 10 minutes. Retry in {RETRY_AFTER_S} s.")


class Handler(BaseHTTPRequestHandler):
    proxy = None                                  # set by serve()
    server_version = "swift-proxy/1"

    def log_message(self, fmt, *args):
        sys.stderr.write("  %s %s\n" % (time.strftime("%H:%M:%S"), fmt % args))

    def _handle(self, method):
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n) if n else b""
        headers = {k: v for k, v in self.headers.items() if k.lower() not in ("host", "content-length")}
        try:
            status, hdrs, chunks = self.proxy.request(method, self.path, headers, body)
        except Exception as e:                                      # noqa: BLE001 -- said, not swallowed
            msg = f"the proxy could not reach {self.proxy.url()}: {type(e).__name__}: {e}"
            status, hdrs, chunks = Proxy._answer("swift_unreachable", msg)
        self.send_response(status)
        for k, v in (hdrs or {}).items():
            if k.lower() not in ("transfer-encoding", "connection", "content-length"):
                self.send_header(k, v)
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        for c in chunks:
            if not c:
                continue
            self.wfile.write(b"%x\r\n%s\r\n" % (len(c), c))
        self.wfile.write(b"0\r\n\r\n")

    def do_GET(self):
        self._handle("GET")

    def do_POST(self):
        self._handle("POST")

    def do_DELETE(self):
        self._handle("DELETE")


def serve(host="127.0.0.1", port=8000, proxy=None):
    Handler.proxy = proxy or Proxy()
    httpd = ThreadingHTTPServer((host, port), Handler)
    print(f"swift proxy on http://{host}:{port} -> {Handler.proxy.url()} "
          f"(/v1/models is the readiness signal; a cold Swift takes ~10 min)")
    httpd.serve_forever()


def main(argv):
    ap = argparse.ArgumentParser(description="Start Swift on demand and hold the caller through the load.")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--status", action="store_true", help="print what it would do now, and exit")
    a = ap.parse_args(argv)
    if a.status:
        p = Proxy()
        ok, why = p.status()
        allowed, greason = guard_check()
        print(f"upstream : {p.url()}")
        print(f"ready    : {'yes' if ok else 'no'}{'' if ok else ' (' + why + ')'}")
        print(f"guard    : {'may start' if allowed else 'WOULD SHED -- would refuse'} ({greason})")
        return 0
    serve(a.host, a.port)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
