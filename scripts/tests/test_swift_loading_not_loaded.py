#!/usr/bin/env python3
"""t1555: the Lanes tab must say LOADING while the unit/container is up and /v1/models has not
answered, and READY only when it answers.

Jas, 2026-10-23 ~11:26 MDT: the tab showed Swift "fully loaded" while vLLM was ~20% through loading
shards (`Loading safetensors checkpoint shards ... 10/51`); swift.service was still `activating` at
11:33 and GET http://172.18.0.2:8000/v1/models did not answer. The tab had no loading state at all:
it listed the servers that ANSWER and the models whose processes hold memory, so a 10-minute load was
invisible -- an absent row, which reads as "nothing is happening" or, with the scheduler line saying
"GX10 running", as loaded.

The oracle is the task's own: a stub where the unit is ACTIVE and /v1/models FAILS must show loading.
The two states that a page cannot currently tell apart are the assertion that matters, so each case
below changes ONE input:

  * the same failing /v1/models with the unit up (loading) and the unit down (stopped) must differ;
  * the same unit up with /v1/models failing (loading) and answering (ready) must differ.

RED before the fix: device_tiles.engine_readiness and servers_html did not exist, and lanes_page()
rendered `<h3>Models loaded, by server</h3>` with no per-server state.

Run: python3 scripts/tests/test_swift_loading_not_loaded.py   (no pytest; exits non-zero on failure)
"""
import importlib.util
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


# SERVERS.json's shape is all engine_readiness reads; the URL is passed in so no docker is shelled.
SERVERS = {"vllm-swift": {"engine": "openai", "url": "http://172.18.0.2:8000", "device": "gx10",
                          "models": {"swift": {"footprint_gb": 95}}},
           "ollama-main": {"engine": "ollama", "url": "http://127.0.0.1:11434", "device": "gx10",
                           "models": {"qwen3-embedding:0.6b": {"footprint_gb": 4.0}}}}


def main():
    dt = _load("device_tiles", ROOT / "projects" / "app-repo" / "device_tiles.py")

    def says(state, detail=""):
        """A probe that answers /v1/models as `state` says."""
        def probe(url):
            return (state, ["swift"] if state is True else detail)
        return probe

    def unit(up):
        def up_fn(sid, entry):
            return (up, "docker ps: Up 3 minutes (health: starting)" if up else "no container")
        return up_fn

    # ---- 1. THE ORACLE FROM THE TASK: unit active, /v1/models fails -> loading
    rows = dt.engine_readiness(SERVERS, probe=says(False, "connection refused"),
                               unit_up=unit(True))
    swift = next(r for r in rows if r["server"] == "vllm-swift")
    check("unit up + /v1/models failing is LOADING", swift["state"], "loading")
    check("the loading row names the model it is loading", swift["models"], ["swift"])
    check("the loading row quotes why it is not ready", "does not answer" in swift["detail"], True)

    # ---- 2. THE SAME FAILING PROBE WITH THE UNIT DOWN IS A DIFFERENT STATE
    rows = dt.engine_readiness(SERVERS, probe=says(False, "connection refused"), unit_up=unit(False))
    swift = next(r for r in rows if r["server"] == "vllm-swift")
    check("unit down + /v1/models failing is STOPPED, not loading", swift["state"], "stopped")

    # ---- 3. THE SAME UNIT UP WITH /v1/models ANSWERING IS READY
    rows = dt.engine_readiness(SERVERS, probe=says(True), unit_up=unit(True))
    swift = next(r for r in rows if r["server"] == "vllm-swift")
    check("unit up + /v1/models answering is READY", swift["state"], "ready")

    # ---- 4. AN OLLAMA SERVER LOADS ON REQUEST: no loading window, so it is ready or stopped
    rows = dt.engine_readiness(SERVERS, probe=lambda url: (False, "refused"), unit_up=unit(False))
    oll = next(r for r in rows if r["server"] == "ollama-main")
    check("an Ollama server that does not answer is STOPPED (it loads on request)", oll["state"], "stopped")

    # ---- 5. A PROBE THAT RAISES IS NOT A READY SERVER
    def boom(url):
        raise OSError("network is unreachable")
    rows = dt.engine_readiness(SERVERS, probe=boom, unit_up=unit(True))
    swift = next(r for r in rows if r["server"] == "vllm-swift")
    check("a probe that raises is LOADING while the unit is up, never ready", swift["state"], "loading")

    # ---- 5b. A STOPPED CONTAINER'S ADDRESS IS NOT AN ADDRESS. serve.endpoint() returns the literal
    #          "http://invalid IP:8000" (docker's own output, measured 2026-10-27); the table must
    #          show the declared URL instead of printing that, and must still say stopped.
    docker_style = {"vllm-swift": {"engine": "openai", "url": "http://172.18.0.2:8000",
                                   "url_from": "serve.endpoint",
                                   "models": {"swift": {"footprint_gb": 95}}}}
    rows = dt.engine_readiness(docker_style, probe=says(False, "refused"), unit_up=unit(False),
                               endpoint=lambda: "http://invalid IP:8000")
    swift = rows[0]
    check("an unusable container address falls back to the declared URL",
          swift["url"], "http://172.18.0.2:8000")
    check("the page never prints docker's 'invalid IP' as an address",
          "invalid IP" in dt.servers_html(rows), False)
    rows = dt.engine_readiness(docker_style, probe=says(False, "refused"), unit_up=unit(True),
                               endpoint=lambda: "http://172.18.0.2:8000")
    check("a running container's own address is used", rows[0]["url"], "http://172.18.0.2:8000")

    # ---- 5c. THE ROW'S MODEL IS THE DECLARED ONE, not every tag the endpoint happens to offer
    #          (Ollama's /v1/models lists the whole box: 8 tags when this was measured).
    rows = dt.engine_readiness({"ollama-main": SERVERS["ollama-main"]},
                               probe=lambda url: (True, ["a-tag", "another-tag"]), unit_up=unit(False))
    check("a ready row names its declared model, not the endpoint's whole tag list",
          rows[0]["models"], ["qwen3-embedding:0.6b"])

    # ---- 6. THE PAGE'S WORD. The rendered HTML carries the state as a WORD and a class, so a
    #         colour alone cannot be mistaken for a fact. Its own rows, so an earlier case changing
    #         the shared `rows` cannot quietly turn this assertion green (it did on the first pass).
    html = dt.servers_html(dt.engine_readiness(SERVERS, probe=says(False, "connection refused"),
                                               unit_up=unit(True)))
    check("the rendered table says 'loading'", "loading" in html, True)
    check("the rendered table marks the row with a loading class", "state-loading" in html, True)
    check("the rendered table does not call a loading server ready", ">ready<" in html, False)
    check("the rendered table names the server and its model",
          "vllm-swift" in html and "swift" in html, True)

    # ---- 7. THE TAB ACTUALLY USES IT. Pinned by source, the way test_lanes_tab.py pins the
    #         rest of lanes_page(): the function imports lane_status and shells out, so it cannot
    #         be rendered in an oracle -- but "the page calls the readiness table" is a fact
    #         about the source, and without it the fix would be a library nobody reads.
    src = (ROOT / "projects" / "app-repo" / "feedback.py").read_text(encoding="utf-8")
    s = src.find("def lanes_page():")
    e = src.find("\n\ndef ", s + 1) if s >= 0 else -1
    body = src[s:e] if (s >= 0 and e > s) else ""
    check("lanes_page() is locatable", s >= 0 and e > s, True)
    check("the Lanes tab renders the per-server readiness table", "servers_html(" in body, True)
    check("the readiness section has its own heading", "<h3>Servers</h3>" in body, True)
    # The STATEMENT, not the substring: the first version of this assertion matched "if d:" inside
    # the explanatory comment above the very block it was checking, so it passed on its own prose.
    stmt = body.find("\n    if d:\n")
    check("the lane_status-dependent block is still there to be ordered against", stmt > 0, True)
    check("the readiness table is shown even when lane_status cannot be read",
          body.find("servers_html(") < stmt, True)

    print()
    print(f"{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
