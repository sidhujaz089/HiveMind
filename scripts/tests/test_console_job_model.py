#!/usr/bin/env python3
"""A job's console.ask() call uses the model and window the JOB names -- never whatever is loaded.

2026-09-23, from the Ollama load tracer: the scheduler's health probe reloaded Q4 at 65536
under an ask_q16 run at 262144; in the instant nothing was loaded, the run's next tool round
fell back to the chat setting and loaded the 54 GB bf16. The "Q4" manager queue then ran on
Q16. Pinned here, against a fake HTTP layer (never the live server):

  * console.ask(model_name=, num_ctx=) sends exactly that model and window, and never consults
    console.model() (the "whatever is loaded" fallback)
  * without them it still falls back, so Jas's chat page is unchanged
  * ask_q16 passes the manager role's model and the coder profile's window
"""
import io
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path[:0] = [str(ROOT / "scripts"), str(ROOT / "projects" / "local-inference")]
import console  # noqa: E402

PASSED = FAILED = 0


def report(name, got, want):
    global PASSED, FAILED
    ok = got == want
    PASSED += ok
    FAILED += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"  (got {got!r}, want {want!r})"))


def main():
    sent = []

    class Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=None):
        sent.append(json.loads(req.data))
        return Resp(json.dumps({"message": {"role": "assistant", "content": "ok"},
                                "done_reason": "stop"}).encode())

    consulted = []
    console.urllib.request.urlopen = fake_urlopen
    console.model = lambda: consulted.append(1) or "whatever-is-loaded"
    console.model_max_ctx = lambda name=None: 262144
    console.modellease.check = lambda *a, **k: None

    print("A job names its model and window:")
    console.ask([{"role": "user", "content": "hi"}], tools=False, budget=10,
                model_name="qwen3.8:27b-mtp-q4_K_M", num_ctx=65536)
    report("the request carries the job's model", sent[-1]["model"], "qwen3.8:27b-mtp-q4_K_M")
    report("  and the job's window", sent[-1]["options"]["num_ctx"], 65536)
    report("  and never asked what happens to be loaded", consulted, [])

    print("\nThe chat page is unchanged:")
    console.ask([{"role": "user", "content": "hi"}], tools=False, budget=10)
    report("with no name it falls back to what is loaded", sent[-1]["model"], "whatever-is-loaded")
    report("  at the model's own window", sent[-1]["options"]["num_ctx"], 262144)

    print("\nask_q16 passes both:")
    src = (ROOT / "scripts" / "ask_q16.py").read_text()
    report("every console.ask call in ask_q16 names the model",
           src.count("console.ask(") == src.count("model_name=MODEL, num_ctx=JOB_CTX, role=ROLE)"), True)
    import ask_q16
    import roles
    import settings
    report("  the manager role's model", ask_q16.MODEL, roles.tag("manager", ask_q16.MODEL))
    report("  and the coder profile's window", ask_q16.JOB_CTX, settings.load()["coder"]["num_ctx"])

    print(f"\n{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
