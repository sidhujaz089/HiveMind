#!/usr/bin/env python3
"""Is the inference path ALIVE? Not the process, not the model list -- the path that answers.

    python3 scripts/inference_health.py                    # exit 0 UP, 1 DOWN
    python3 scripts/inference_health.py --json
    python3 scripts/inference_health.py --model gemma4:26b

    from inference_health import require_up
    require_up()          # raises ServerDown; call before dispatching anything

WHY, t283, and it cost a weekend. On 2026-09-11/12 four Q16 dispatches died identically and
were misdiagnosed three times as task design -- chunk too big, brief unclear, budget too small.
The real cause was the server, and the thing that hid it was a health check that did not
exercise inference:

    /api/tags   answered INSTANTLY with 16 models
    /api/chat   timed out on every model

`/api/tags` reads a directory. It never touches the GPU, so it answers cheerfully while
inference is dead. HANDOVER-2026-09-12: "A model list is not a model. I used it as a health
check three separate times this session and it lied every time."

What was actually wrong: `llama-server` runs with `-np 1` -- ONE SLOT -- and one runaway
generation (task 133946, past 38,000 tokens and still climbing) held it while every other
request queued behind it forever. Not a crash. Not an error. A hang, to every caller.

## WHAT THIS CHECKS, AND WHY EACH PART

  1. A REAL /api/chat, num_predict 5. The only question that matters is whether a token comes
     back, and the only way to ask it is to ask for a token.
  2. TIMED, with the elapsed seconds reported. A server that answers in 200s is not healthy
     even though it answered -- it is a queue, and the next dispatch will sit behind it.
  3. A TIMEOUT IS **DOWN**, never "inconclusive". The whole failure being prevented is slowness
     being read as something other than a problem.
  4. /api/tags IS CHECKED TOO -- but only to tell two different failures apart. Tags up and chat
     down is the wedged-slot signature and says so by name. Both down is a dead server. That
     distinction is the difference between "restart ollama" and "find the runaway generation",
     and guessing wrong cost four dispatches.

## WHY THIS IS NOT JUST ANOTHER SCRIPT NOBODY RUNS

Gates 3 and 6 stayed open because their scripts were referenced only by their own `__main__`.
`require_up()` exists so the CALLERS can refuse rather than a human remembering to look, and
the dispatch paths call it. A health check nobody calls is documentation.

THE COMPANION RISK, stated because this file is half of a pair. Timeouts across this harness
were raised on 2026-09-14 to match a measured 5.1 tok/s, doubled for variance. That makes a
slow answer survive AND makes a wedged server take LONGER to notice. This is the other half:
generous timeouts are safe only with a check that exercises the path.
"""
import argparse
import json
import sys
from pathlib import Path
import time
import urllib.error
import urllib.request

HOST = "http://127.0.0.1:11434"
_DEFAULT_HOST = HOST

# A HEALTHY REPLY TO FIVE TOKENS IS FAST EVEN HERE. At the measured 5.1 tok/s floor, 5 tokens is
# ~1s of decode; the rest is load. A warm model answered in 2.2s on 2026-09-14. The old 90s
# "left room for a COLD load without tolerating a queue" -- an assumption that 2026-09-20
# invalidated: on the shared single slot (-np 1), a healthy probe legitimately queues behind an
# ACTIVE INTERACTIVE SESSION (a dsh-harness step runs minutes on the 262k-ctx model) and 90s
# timed out on that queue, so the tick refused production with "inference path down" while the
# path was fine (four consecutive refusals 08:51-09:21Z, the holder was the live session).
# 600s tolerates the queue; a real wedge now costs ten minutes to detect instead of 90s.
# STOPGAP for the shared-slot era: p45/t359 classifies this state as WAIT-naming-the-holder,
# and t361 (-np 4) makes the queue itself go away. Revisit both before trusting 600s quietly.
CHAT_TIMEOUT_S = 600
TAGS_TIMEOUT_S = 10
SLOW_S = 30          # answered, but slowly enough to say so


class ServerDown(RuntimeError):
    """The inference path did not answer. Carries the full probe result, never a prefix."""

    def __init__(self, result):
        self.result = result
        super().__init__(result.get("say") or "the inference path is down")


def _post(path, payload, timeout):
    req = urllib.request.Request(HOST + path, data=json.dumps(payload).encode(),
                                headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as f:
        return json.load(f)


def tags(timeout=TAGS_TIMEOUT_S):
    """The model LIST. Deliberately separate, and deliberately not the health check."""
    try:
        with urllib.request.urlopen(HOST + "/api/tags", timeout=timeout) as f:
            return [m.get("name") for m in (json.load(f).get("models") or [])], None
    except Exception as e:                                          # noqa: BLE001
        return None, f"{type(e).__name__}: {e}"


def _role_model_loaded():
    """A model some production role uses that is loaded on ANY server right now, or None.

    The default used to be "whatever Ollama has loaded, else its first tag". With Swift on vLLM
    and Ollama empty, that picked gemma4:26b, which the device cannot load beside Swift, so the
    scheduler's gate read a healthy path as down (2026-10-06). The roles say what production
    will call; the ledger says what is warm."""
    try:
        import infer
        import json as _j
        roles = _j.loads((Path(__file__).resolve().parent.parent / "projects" / "local-inference"
                          / "models" / "ROLES.json").read_text(encoding="utf-8"))["roles"]
        wanted = [r.get("model") for k, r in roles.items()
                  if r.get("kind") == "model" and k != "embedder" and r.get("model")]
        warm = {m for per in infer.ledger().values() for held in per.values() for m in held}
        return next((m for m in wanted if m in warm), None)
    except Exception as e:                                           # noqa: BLE001
        print(f"  NOTE: role models not read ({type(e).__name__}: {e}); probing Ollama's default",
              file=sys.stderr)
        return None


def _chat(body, timeout):
    """Through infer.chat (docs/ENGINES.md), so the probe reaches the server that would serve the
    run -- vLLM for Swift -- and gets the same memory admission. A probe posting straight to
    Ollama loaded an 18 GB model beside a 95 GB vLLM engine to ask it for five tokens. Falls
    back to the direct post only if infer itself cannot be imported."""
    # HOST POINTED ELSEWHERE IS A DELIBERATE CHOICE (a suite's fixture, an operator's override):
    # probe exactly there. Only the default main server routes through the registry.
    if HOST.rstrip("/") != _DEFAULT_HOST:
        return _post("/api/chat", body, timeout)
    try:
        import infer
    except ImportError:
        return _post("/api/chat", body, timeout)
    return infer.chat(body, timeout=timeout)


def probe(model=None, timeout=CHAT_TIMEOUT_S):
    """Ask for five tokens and time it. Returns a dict; never raises.

    `model` defaults to whatever is loaded, else the first tag, so this works without anyone
    keeping a model name in step with reality.
    """
    names, tags_err = tags()
    chosen = model or _role_model_loaded()
    if not chosen:
        # Prefer a model already resident: asking a cold 53 GB model to load is a different
        # measurement, and on a one-slot server it is also a way to evict the one in use.
        try:
            with urllib.request.urlopen(HOST + "/api/ps", timeout=5) as f:
                loaded = [m.get("name") for m in (json.load(f).get("models") or [])]
        except Exception:                                            # noqa: BLE001
            loaded = []
        chosen = (loaded or names or [None])[0]

    out = {"host": HOST, "model": chosen, "tags_ok": names is not None,
           "tags_count": len(names or []), "tags_error": tags_err}

    if not chosen:
        out.update(up=False, seconds=None,
                   say="NO MODEL AVAILABLE: /api/tags returned nothing to ask. "
                       + (f"tags error: {tags_err}" if tags_err else "the list was empty."))
        return out

    # THE CONTRACT, not a hand-built options dict -- test_options_contract caught this file
    # doing exactly what ask-gemma-supervise.py was caught doing, and the rule is the same:
    # docs/skills/local-model-loading, "Never build an options dict. Call the contract."
    # A probe still has to work when settings is unreachable, though, because this is the last
    # line of defence before a dispatch, so the fallback is built by ASSIGNMENT rather than as
    # a literal -- same shape the other callers use.
    msgs = [{"role": "user", "content": "ok"}]
    try:
        import os
        sys.path.insert(0, os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "projects", "local-inference"))
        import settings
        body = settings.request("coder", chosen, msgs, num_predict=5)
        body["keep_alive"] = "24h"
    except Exception:                                                # noqa: BLE001
        body = {"model": chosen, "stream": False, "keep_alive": "24h", "messages": msgs}
        opts = {}
        opts["num_predict"] = 5
        body["options"] = opts
    t = time.time()
    try:
        d = _chat(body, timeout)
        secs = time.time() - t
        out.update(up=True, seconds=round(secs, 2), done_reason=d.get("done_reason"),
                   eval_count=d.get("eval_count"),
                   load_seconds=round((d.get("load_duration") or 0) / 1e9, 2),
                   slow=secs > SLOW_S)
        out["say"] = (f"UP: {chosen} answered in {secs:.1f}s "
                      f"(done_reason={d.get('done_reason')}, {d.get('eval_count')} tokens)"
                      + (f" -- SLOW, over {SLOW_S}s; something may be queued ahead of you"
                         if secs > SLOW_S else ""))
        return out
    except Exception as e:                                           # noqa: BLE001
        secs = time.time() - t
        out.update(up=False, seconds=round(secs, 2), error=f"{type(e).__name__}: {e}")
        # THE DIAGNOSIS, and it is the point of the file. These two failures need different
        # actions and were confused for a weekend.
        if type(e).__name__ in ("Refused", "NoServer"):
            # infer said no BEFORE sending: not a wedged slot, not a dead server (2026-10-06).
            out["say"] = f"REFUSED before asking ({type(e).__name__}): {e}"
        elif names is not None:
            out["say"] = (
                f"DOWN: /api/tags answered with {len(names)} model(s) but /api/chat FAILED after "
                f"{secs:.0f}s ({type(e).__name__}). THAT PAIR IS THE WEDGED-SLOT SIGNATURE. "
                f"llama-server runs -np 1, so ONE runaway generation starves every other "
                f"request and presents as a hang. Do NOT read the model list as health. "
                f"Find the long-running task in the ollama journal before restarting anything: "
                f"journalctl --user -u ollama | grep 'print_timing' | tail")
        else:
            out["say"] = (f"DOWN: neither /api/tags nor /api/chat answered "
                          f"({tags_err}; chat {type(e).__name__} after {secs:.0f}s). "
                          f"The server is not there at all, which is a different problem from a "
                          f"wedged slot -- check the service before hunting a runaway job.")
        return out


def dispatches_running():
    """How many coder.py dispatches are live, read from /proc via lane_status (L-105).

    WAS `ps -eo cmd`, matching "local-inference/coder.py" AND "--brief" in each line. Without a
    terminal, ps cuts every line at 80 columns, and "/usr/bin/python3 /home/.../coder.py" is 78
    of them -- so "--brief" was never in the text and this returned 0 with two lanes running.
    classify() then reported a merely BUSY box as DOWN and coder.py refused every additional
    lane: p11 refused twice on 2026-09-15 while bf16 and gemma were mid-attempt. It also only
    ever counted --brief runs, never --task ones.
    """
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import lane_status
        return len(lane_status.lanes())
    except Exception:                                                # noqa: BLE001
        return 0


def classify(model=None, timeout=CHAT_TIMEOUT_S):
    """probe(), plus the distinction that makes parallel work possible: BUSY is not DOWN.

    Added 2026-09-14, and it was blocking t303 outright. With one dispatch holding the single
    -np 1 slot, a probe times out -- so probe() reported the WEDGED-SLOT signature and coder.py
    refused to start a second lane. The server was not wedged; it was busy with our own work.
    Left as it was, two models could never run in parallel at all: the first would lock out the
    second by existing.

    The distinction is evidence-based, not a guess: a timeout with /api/tags answering AND a
    coder.py dispatch alive is a QUEUE. A timeout with no dispatch running is the failure that
    cost four dispatches on 2026-09-11/12, and still refuses.

    Returns the probe dict with `state` set to "up", "busy" or "down". BUSY still says so
    loudly, because a queue you cannot see is how a 28-second reply gets read as a fast one.
    """
    r = probe(model=model, timeout=timeout)
    if r.get("up"):
        r["state"] = "busy-but-answering" if r.get("slow") else "up"
        return r
    # REFUSED OR UNSERVED IS NEITHER BUSY NOR WEDGED (2026-10-06). infer.chat refuses a load the
    # device cannot hold, or a model no server offers, at once and with the reason. Read as BUSY
    # it let coder.py start a run that would fail on its first call, under the wrong diagnosis.
    err = r.get("error") or ""
    if err.startswith(("Refused:", "NoServer:")):
        r["state"] = "refused"
        r["say"] = f"REFUSED before asking: {err}"
        return r
    n = dispatches_running()
    if r.get("tags_ok") and n > 0:
        r["state"] = "busy"
        r["dispatches_running"] = n
        r["say"] = (f"BUSY, not down: /api/chat did not answer within {timeout}s and "
                    f"{n} coder.py dispatch(es) are running. llama-server is -np 1, so they "
                    f"hold the slot and a probe queues behind them. This is OUR OWN work, not "
                    f"a wedge. A new dispatch will queue too and take longer than usual.")
        return r
    r["state"] = "down"
    return r


def require_up(model=None, timeout=CHAT_TIMEOUT_S):
    """Raise ServerDown unless a token came back. CALL THIS BEFORE DISPATCHING.

    The four dispatches wasted on 2026-09-11/12 were all sent into a server that could not
    answer. Each then produced a plausible-looking task-design diagnosis. Refusing to start is
    the same discipline coder.py's baseline guard already applies to a red sandbox: work on
    ground you cannot trust is worse than no work, because it manufactures wrong conclusions.
    """
    r = probe(model=model, timeout=timeout)
    if not r.get("up"):
        raise ServerDown(r)
    return r


def main():
    ap = argparse.ArgumentParser(description="Is the inference PATH alive? Exit 0 up, 1 down.")
    ap.add_argument("--model", default=None, help="tag to probe; default whatever is loaded")
    ap.add_argument("--timeout", type=int, default=CHAT_TIMEOUT_S)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    r = probe(model=a.model, timeout=a.timeout)
    if a.json:
        print(json.dumps(r, indent=2))
    else:
        print("  " + r["say"])
    return 0 if r.get("up") else 1


if __name__ == "__main__":
    raise SystemExit(main())
