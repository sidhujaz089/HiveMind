#!/usr/bin/env python3
"""GATE: a console job pinned to a specific model must ask THAT model, not whatever
Ollama happens to have loaded.

t313/t314, Jas 2026-09-15: "Q16 isn't working in the console ... Model is held, request
not sent: model 'qwen3.8:27b-mtp-bf16' is held by ... for 'preflight review'."

scripts/dispatch_loop.py's assign_console() deliberately routes a console message to a
model that is FREE -- never the one busy with a preflight review -- and
console.answer_queued() is supposed to pin that choice for the turn (the `pinned()` /
cfg.load monkeypatch at projects/local-inference/console.py:1141-1145, comment: "every
chat-model lookup in this turn uses it"). But projects/local-inference/console.py:76-99,
`model()`, never consults cfg at all when Ollama has ANYTHING loaded -- it returns
`loaded[0]["name"]` unconditionally. So the pin never reaches the request: whatever is
warm (usually the reviewer, since it is the busiest model on the box) gets asked instead,
and modellease.check() correctly refuses because THAT model really is held for preflight
review. dispatch_loop's whole point -- send this to a model that is not busy -- never
takes effect, and Jas sees "Model is held" on a message the scheduler thought it had
routed around the hold.

The fix threads an explicit model name through model() and ask() so a pinned job cannot
be silently redirected to whatever happens to be loaded.
"""
import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "projects" / "local-inference"))
sys.path.insert(0, str(ROOT / "scripts"))
PASSED = FAILED = 0


def check(label, got, want):
    global PASSED, FAILED
    if got == want:
        PASSED += 1
        print(f"  [ok]   {label}: {got!r}")
    else:
        FAILED += 1
        print(f"  [BAD]  {label}: expected {want!r}, got {got!r}")


import console  # noqa: E402

orig_urlopen = urllib.request.urlopen
orig_model = console.model
orig_lease_check = console.modellease.check

print("\nmodel(pin=...) returns the pin WITHOUT touching Ollama at all:")


def _boom(*a, **k):
    raise AssertionError("model(pin=...) must not call the network")


urllib.request.urlopen = _boom
try:
    got = console.model(pin="qwen3.8:27b-mtp-bf16")
    check("returns the pin verbatim", got, "qwen3.8:27b-mtp-bf16")
except TypeError as e:
    FAILED += 1
    print(f"  [BAD]  model() takes no `pin` argument yet: {e}")
except AssertionError as e:
    FAILED += 1
    print(f"  [BAD]  model(pin=...) hit the network: {e}")
finally:
    urllib.request.urlopen = orig_urlopen


print("\nask(..., model_name=...) sends THAT model, never console.model()'s own answer:")
calls = []


class _FakeResponse:
    def __init__(self, body):
        self._body = json.dumps(body).encode()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self._body


def _fake_urlopen(req, timeout=None):
    calls.append(json.loads(req.data.decode()))
    return _FakeResponse({"message": {"role": "assistant", "content": "ok"}})


console.model = lambda: "WRONG-MODEL-DO-NOT-USE"
console.modellease.check = lambda *a, **k: None   # the lease itself is not this oracle's target
urllib.request.urlopen = _fake_urlopen
try:
    console.ask([{"role": "user", "content": "hi"}], model_name="qwen3.8:27b-mtp-bf16")
except TypeError as e:
    FAILED += 1
    print(f"  [BAD]  ask() takes no `model_name` argument yet: {e}")
finally:
    urllib.request.urlopen = orig_urlopen
    console.model = orig_model
    console.modellease.check = orig_lease_check

# RESTATED 2026-09-30: this counted EVERY urlopen and wanted 1. ask() now also asks /api/show for
# the pinned model's window and sampling profile -- two lookups of THAT model, neither of which
# consults console.model(), which is the defect this gate exists for. So: exactly one CHAT request,
# and every request, lookups included, names the pinned model and never console.model()'s answer.
chats = [c for c in calls if "messages" in c]
check("exactly one chat request was sent", len(chats), 1)
if chats:
    check("the request named the pinned model, not console.model()'s answer",
          chats[0].get("model"), "qwen3.8:27b-mtp-bf16")
check("no request of any kind named console.model()'s answer",
      [c.get("model") for c in calls if c.get("model") != "qwen3.8:27b-mtp-bf16"], [])

print("\nanswer_queued() forwards ITS pinned model into ask() by name, not only via the "
      "cfg.load monkeypatch that model() ignores:")
src = (ROOT / "projects" / "local-inference" / "console.py").read_text(encoding="utf-8")
check("respond() accepts an explicit model_name and answer_queued() passes its pinned "
      "model through by name", "model_name=model" in src.replace(" ", ""), True)

print(f"\n{PASSED} passed, {FAILED} failed\n")
sys.exit(1 if FAILED else 0)
