#!/usr/bin/env python3
"""infer.py: one call shape for every engine, a resolver that obeys the job's server, and a
per-device ledger that refuses the load that would starve another engine.

WHY (docs/ENGINES.md). Jas, 2026-10-06: plug-and-play with any engine. The two failures this
guards: a vLLM-only knob sent to Ollama, or the reverse (UNPARSEABLE x7, 674f869b), and Ollama
loading Q4 beside a 95 GB vLLM engine because neither could see the other (2026-10-06 14:05).
Every check below has a case that must FAIL, so a suite that cannot fail would be caught.
"""
import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "projects" / "local-inference"))
import infer                                                    # noqa: E402

FAILS = []


def check(name, got, want):
    ok = got == want
    print(f"  [{'ok' if ok else 'FAIL'}]  {name}" + ("" if ok else f": {got!r} (want {want!r})"))
    if not ok:
        FAILS.append(name)


REG = {"devices": {"box": {"memory_gb": 121, "reserve_gb": 8}, "other": {"memory_gb": 24}},
       "servers": {
           "ol": {"engine": "ollama", "url": "http://127.0.0.1:11434", "device": "box"},
           "ol2": {"engine": "ollama", "url": "http://127.0.0.1:11435", "device": "box"},
           "vl": {"engine": "openai", "url": "http://10.0.0.2:8000", "device": "box",
                  "models": {"swift": {"footprint_gb": 95, "context": 262144,
                                       "reasoning": {"off": {"enable_thinking": False},
                                                     "low": {"enable_thinking": True, "reasoning_effort": "low"},
                                                     "high": {"enable_thinking": True, "reasoning_effort": "xhigh"}}}}},
           "egpu": {"engine": "ollama", "url": "http://100.1.1.1:11434", "device": "other"}}}


def fake(state):
    """Patch adapters to answer from `state`: {sid: {"up": bool, "models": {m: gb}, "loaded": {m: gb}}}."""
    def models(self):
        return dict(state[self.id]["models"])

    def loaded(self):
        return dict(state[self.id]["loaded"]) if state[self.id]["up"] else {}

    def health(self):
        return state[self.id]["up"]
    for cls in (infer.Ollama, infer.OpenAI):
        cls.models, cls.loaded, cls.health = models, loaded, health
    infer.OpenAI.url = property(lambda self: self.entry["url"])


def main():
    real = {c: (c.models, c.loaded, c.health) for c in (infer.Ollama, infer.OpenAI)}
    real_url = infer.OpenAI.url
    try:
        run()
    finally:
        for c, (m, l, h) in real.items():
            c.models, c.loaded, c.health = m, l, h
        infer.OpenAI.url = real_url
    print(f"\n  {'FAILED: ' + ', '.join(FAILS) if FAILS else 'test_infer: ok'}")
    return 1 if FAILS else 0


def run():
    oa = infer.OpenAI("vl", REG["servers"]["vl"])

    print("request translation: internal (Ollama) shape -> OpenAI")
    body = {"model": "swift", "think": "high", "stream": False, "keep_alive": "24h",
            "options": {"num_ctx": 65536, "num_predict": 4000, "temperature": 1.0, "top_k": 20,
                        "repeat_penalty": 1.0, "seed": 7},
            "tools": [{"type": "function", "function": {"name": "read_file", "parameters": {}}}],
            "messages": [{"role": "user", "content": "hi"},
                         {"role": "assistant", "content": "",
                          "tool_calls": [{"id": "abc", "function": {"name": "read_file",
                                                                    "arguments": {"path": "x.py"}}}]},
                         {"role": "tool", "tool_call_id": "abc", "content": "file"}]}
    r = oa.request(body)
    check("think 'high' becomes Swift's own control, xhigh",
          r["chat_template_kwargs"], {"enable_thinking": True, "reasoning_effort": "xhigh"})
    check("num_predict becomes max_tokens", r.get("max_tokens"), 4000)
    check("repeat_penalty is renamed, not dropped", r.get("repetition_penalty"), 1.0)
    check("Ollama-only keys do not leak (num_ctx, keep_alive, think)",
          [k for k in ("num_ctx", "keep_alive", "think", "options") if k in r], [])
    tc = r["messages"][1]["tool_calls"][0]
    check("tool-call arguments become a JSON string", json.loads(tc["function"]["arguments"]), {"path": "x.py"})
    check("the tool-call id survives, so the tool result still matches", tc["id"], "abc")
    check("the tool result keeps its tool_call_id", r["messages"][2]["tool_call_id"], "abc")
    check("think False turns reasoning off", oa.request({**body, "think": False})["chat_template_kwargs"],
          {"enable_thinking": False})
    check("num_predict -1 sends no max_tokens (until done)",
          "max_tokens" in oa.request({**body, "options": {"num_predict": -1}}), False)

    print("\nresponse translation: OpenAI -> internal shape")
    d = {"choices": [{"finish_reason": "tool_calls", "message": {
            "content": "", "reasoning_content": "thought",
            "tool_calls": [{"id": "c9", "type": "function",
                            "function": {"name": "read_file", "arguments": "{\"path\": \"y\"}"}}]}}],
         "usage": {"completion_tokens": 12, "prompt_tokens": 30}}
    o = infer.OpenAI.response(d, "swift", 2.0)
    check("reasoning arrives as message.thinking", o["message"]["thinking"], "thought")
    check("tool-call arguments come back as an object", o["message"]["tool_calls"][0]["function"]["arguments"],
          {"path": "y"})
    check("tool_calls finish is a normal stop", o["done_reason"], "stop")
    check("usage maps to eval_count", (o["eval_count"], o["prompt_eval_count"]), (12, 30))
    cut = infer.OpenAI.response({"choices": [{"finish_reason": "length", "message": {"content": "x"}}]}, "swift", 1)
    check("a cut-off reply is still 'length' (the NOT-SAT rule depends on it)", cut["done_reason"], "length")

    print("\nresolve: where a call goes")
    st = {"ol": {"up": True, "models": {"q4": 17.7, "gemma": 18.6}, "loaded": {}},
          "ol2": {"up": True, "models": {"q4": 17.7, "gemma": 18.6}, "loaded": {"q4": 17.6}},
          "vl": {"up": True, "models": {"swift": 95}, "loaded": {"swift": 95}},
          "egpu": {"up": False, "models": {"gemma": 18.6}, "loaded": {}}}
    fake(st)
    check("a model goes to the server that has it LOADED", infer.resolve("q4", env={}, reg=REG)[0].id, "ol2")
    check("the job's server wins (HIVEMIND_OLLAMA)",
          infer.resolve("q4", env={"HIVEMIND_OLLAMA": "http://127.0.0.1:11434/"}, reg=REG)[0].id, "ol")
    check("...but not for a model that server does not offer",
          infer.resolve("swift", env={"HIVEMIND_OLLAMA": "http://127.0.0.1:11434"}, reg=REG)[0].id, "vl")
    st["ol2"]["models"] = {}
    check("an UNREADABLE job server is still the job's server (it fails there, not elsewhere)",
          infer.resolve("q4", env={"HIVEMIND_OLLAMA": "http://127.0.0.1:11435"}, reg=REG)[0].id, "ol2")
    st["ol2"]["models"] = {"q4": 17.7, "gemma": 18.6}
    check("nothing loaded: the first ANSWERING server in registry order",
          infer.resolve("gemma", env={}, reg=REG)[0].id, "ol")
    try:
        infer.resolve("nonesuch", env={}, reg=REG)
        check("with no default server, an unoffered model raises NoServer", "no raise", "NoServer")
    except infer.NoServer:
        check("with no default server, an unoffered model raises NoServer", "NoServer", "NoServer")
    reg2 = json.loads(json.dumps(REG))
    reg2["servers"]["ol"]["default"] = True
    check("with a default, an unlisted model goes there (as a direct :11434 post did)",
          infer.resolve("nonesuch", env={}, reg=reg2)[0].id, "ol")

    print("\nadmit: the per-device ledger")
    st["ol2"]["loaded"] = {}
    ol = infer.Ollama("ol", REG["servers"]["ol"])
    try:
        infer.admit(ol, "q4", reg=REG)
        check("loading Q4 beside a 95 GB vLLM engine is REFUSED (14:05)", "admitted", "Refused")
    except infer.Refused as e:
        check("loading Q4 beside a 95 GB vLLM engine is REFUSED (14:05)", "Refused", "Refused")
        check("and the refusal names who holds the memory", "vl: swift 95 GB" in str(e), True)
    st["vl"]["up"] = False
    check("control: with vLLM down, the same load is admitted", infer.admit(ol, "q4", reg=REG), None)
    st["vl"]["up"] = True
    st["ol"]["loaded"] = {"q4": 17.6}
    check("an already-loaded model is never refused", infer.admit(ol, "q4", reg=REG), None)
    print("\nslots: a non-Ollama engine declares its own capacity (modellease)")
    import tempfile
    sys.path.insert(0, str(REPO / "scripts"))
    import modellease
    reg3 = json.loads(json.dumps(REG))
    reg3["servers"]["vl"]["slots"] = 4
    real_reg = infer.REGISTRY
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(reg3, f)
    infer.REGISTRY = Path(f.name)
    try:
        # PINNED ROLE STORE (2026-10-08): role Lanes now cap a model's slots (task_routing.model_lanes),
        # and the live store has the Manager at lanes 1 -- a setting, not this check's subject.
        import task_routing as _tr
        _live_load = _tr.load
        _tr.load = lambda: {"roles": {"m": {"kind": "model", "model": "swift", "tasks": [{"task": "rca"}]}}}
        try:
            check("a vLLM model's slots come from its server entry", modellease._engine_slots("swift"), 4)
            _tr.load = lambda: {"roles": {"m": {"kind": "model", "model": "swift", "lanes": 2,
                                                "tasks": [{"task": "rca"}]}}}
            check("and its roles' Lanes cap it below the ceiling", modellease._engine_slots("swift"), 2)
        finally:
            _tr.load = _live_load
        check("control: an Ollama model has no engine-declared slots", modellease._engine_slots("q4"), None)
    finally:
        infer.REGISTRY = real_reg
        os.unlink(f.name)
    print("\nmodel_ctx: an engine with a fixed window gets all of it; Ollama keeps the cap")
    import settings
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(REG, f)
    infer.REGISTRY = Path(f.name)
    real_ctx = infer.Ollama.context
    infer.Ollama.context = lambda self, model: 262144
    try:
        settings._CTX_CACHE.clear()
        check("swift on vLLM: its full served window", settings.model_ctx("swift"), 262144)
        cap = max(settings.load()[sec]["num_ctx"] for sec in ("chat", "coder"))
        check("control: an Ollama model with the same window stays at the cap",
              settings.model_ctx("q4"), min(262144, cap))
    finally:
        infer.REGISTRY = real_reg
        infer.Ollama.context = real_ctx
        settings._CTX_CACHE.clear()
        os.unlink(f.name)
    eg = infer.Ollama("egpu", REG["servers"]["egpu"])
    st["egpu"]["up"] = True
    check("another DEVICE's memory is its own: the eGPU is not charged for the box",
          infer.admit(eg, "gemma", reg=REG), None)


if __name__ == "__main__":
    sys.exit(main())
