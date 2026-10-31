#!/usr/bin/env python3
"""t1320: infer.OpenAI must never send a max_tokens that does not fit the server's window.

Dispatch p591-t757-servers (coder.py --model swift, 2026-10-16) printed
"output budget 252347 tokens (window 262144)" once, before its first attempt. The first attempt
answered; the second sent the same 252347 on a message list grown by the first attempt's own
reply, and the endpoint refused it: "HTTPError: HTTP Error 400: Bad Request".
projects/local-inference/models/SERVERS.json states the rule the harness broke, on vllm-swift:
"a larger max_tokens than fits is a 400 from vLLM, so 'no limit' is the maximum."
262144 - 252347 = 9797 tokens of room for the ENTIRE prompt.

OpenAI.request (projects/local-inference/infer.py) copies options.num_predict straight into
max_tokens, and OpenAI.context() -- which returns the model's declared window, 262144 for swift --
is never consulted on the request path.

This suite pins the transport's side of that:
  * a num_predict that leaves no room for the prompt it is being sent with is CLAMPED to fit;
  * a request that already fits is passed through untouched, sampling keys included;
  * a server that declares no context is left alone -- the bound is the DECLARED window;
  * the registry's declared "no limit" (job_budget -1) still sends no max_tokens at all;
  * the clamp is RECORDED in the response, and the request body keeps only keys the endpoint
    accepts (a bookkeeping key in the body is an unrecognised key: this same 400, reintroduced).

Every fixture is built in this file: the vllm-swift registry entry is copied below and deepcopy'd
per test, so no live registry, docker, socket or model is touched, and every send goes through a
stub that refuses URLs it was not written to answer.
"""
import copy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "projects" / "local-inference"))
import infer  # noqa: E402

CTX = 262144              # the context vllm-swift declares for swift (real SERVERS.json value)
SENT_BUDGET = 252347      # the output budget the harness printed and sent on the second attempt
REQUEST_TIMEOUT = 900
WORDS = 40000             # 40,000 distinct words: >= 40,000 tokens for any real tokenizer
MAX_ALLOWED = CTX - WORDS  # 222144: the largest max_tokens that can fit that prompt

# The vllm-swift entry of projects/local-inference/models/SERVERS.json, copied into this file so
# the suite owns its fixture: reading the live registry would let a Models-tab edit turn this
# suite red on main. url_from is dropped on purpose ("serve.endpoint" resolves a docker bridge
# IP at call time -- live state); the url is a black hole that is never connected to because
# every send is stubbed below.
SWIFT_ENTRY = {
    "engine": "openai",
    "url": "http://127.0.0.1:9",
    "device": "gx10",
    "models": {
        "swift": {
            "footprint_gb": 95,
            "context": CTX,
            "job_budget": -1,
            "reasoning": {"off": {"enable_thinking": False},
                          "high": {"enable_thinking": True, "reasoning_effort": "xhigh"}},
        }
    },
}

WIRE_KEYS = {"model", "messages", "stream", "max_tokens", "max_completion_tokens",
             "temperature", "top_p", "top_k", "min_p", "presence_penalty",
             "frequency_penalty", "repetition_penalty", "seed", "stop", "tools",
             "response_format", "chat_template_kwargs"}


def _swift():
    """A private copy of the real vllm-swift entry, so no test can disturb another."""
    return copy.deepcopy(SWIFT_ENTRY)


def _big_prompt():
    """40,000 distinct words: 268,889 characters, and at least one token per word."""
    return " ".join("w%d" % i for i in range(WORDS))


def _body(num_predict, content=None, **opts):
    options = dict(opts)
    options["num_predict"] = num_predict
    return {"model": "swift", "think": "high",
            "messages": [{"role": "user",
                          "content": _big_prompt() if content is None else content}],
            "options": options}


def test_a_num_predict_that_leaves_no_room_for_the_prompt_is_clamped():
    """RED ON MAIN FOR THE STATED REASON: request() copies num_predict into max_tokens, so the
    harness's own number, 252347, is sent beside a >=40,000-token prompt against a window of
    262144. The main-branch failure reads 'max_tokens 252347 ... exceeds the declared 262144'
    -- the endpoint's documented 400, not the suite's own bug."""
    adapter = infer.OpenAI("vllm-swift", _swift())
    assert adapter.context("swift") == CTX, (
        "the fixture must carry the window the real registry declares (%d); context() gave %r"
        % (CTX, adapter.context("swift")))
    req = adapter.request(_body(SENT_BUDGET))
    assert "max_tokens" in req, "num_predict > 0 must reach max_tokens"
    assert req["max_tokens"] <= MAX_ALLOWED, (
        "max_tokens %s plus a >=%d-token prompt exceeds the declared %d-token window, which is "
        "the 400 this task is about (SERVERS.json: 'a larger max_tokens than fits is a 400 from "
        "vLLM')" % (req["max_tokens"], WORDS, CTX))
    assert req["max_tokens"] > 0, "a clamp that zeroes the budget kills every dispatch it touches"
    assert req["max_tokens"] >= 1024, (
        "max_tokens %s is a degenerate clamp, not a bound: the model has nothing to answer with"
        % req["max_tokens"])


def test_a_request_that_already_fits_is_passed_through_untouched():
    """The clamp must not become a new ceiling, and the existing translation must not change."""
    adapter = infer.OpenAI("vllm-swift", _swift())
    body = {"model": "swift", "messages": [{"role": "user", "content": "short brief"}],
            "options": {"num_predict": 8192, "temperature": 0.7, "top_k": 40}}
    req = adapter.request(body)
    assert req["max_tokens"] == 8192, "a request that fits must not be clamped"
    assert req["temperature"] == 0.7, "SAMPLING translation changed"
    assert req["top_k"] == 40, "SAMPLING translation changed"
    assert req["stream"] is False
    assert req["model"] == "swift"


def test_the_request_body_carries_only_keys_the_endpoint_accepts():
    """vLLM validates this body key by key: an unrecognised key is the 400 this task is about. A
    fix that records the clamp by adding a key to the REQUEST body reintroduces the defect."""
    adapter = infer.OpenAI("vllm-swift", _swift())
    body = _body(SENT_BUDGET,
                 tools=[{"type": "function",
                         "function": {"name": "read_file", "parameters": {"type": "object"}}}],
                 format="json")
    req = adapter.request(body)
    extra = sorted(set(req) - WIRE_KEYS)
    assert not extra, ("the request body carries key(s) %s that OpenAI /v1/chat/completions does "
                      "not accept, and vLLM rejects the whole request: record the clamp in the "
                      "RESPONSE, not the request" % extra)
    assert req["response_format"] == {"type": "json_object"}, "format 'json' translation changed"
    assert req["tools"][0]["function"]["name"] == "read_file", "tools translation changed"


def test_a_server_that_declares_no_context_is_left_alone():
    """OpenAI.context() returns None when the entry declares no context. The bound is the DECLARED
    window, not an invented one: no declaration, no clamp, and no crash on None."""
    entry = {"engine": "openai", "url": "http://127.0.0.1:9", "device": "gx10", "models": {}}
    adapter = infer.OpenAI("other", entry)
    assert adapter.context("mystery") is None
    req = adapter.request({"model": "mystery",
                           "messages": [{"role": "user", "content": "hi"}],
                           "options": {"num_predict": 16000}})
    assert req["max_tokens"] == 16000, "a server with no declared context must not be clamped"


def test_the_declared_unlimited_job_budget_sends_no_max_tokens():
    """The real registry sets swift's job_budget to -1 = 'no limit', and request() emits no
    max_tokens key for a non-positive num_predict. The clamp must not turn 'no limit' into a
    number: that is a behaviour change the registry forbids."""
    entry = _swift()
    assert entry["models"]["swift"]["job_budget"] == -1, "the real registry declares -1 for swift"
    adapter = infer.OpenAI("vllm-swift", entry)
    req = adapter.request(_body(-1, content="short"))
    assert "max_tokens" not in req, "num_predict -1 means 'no limit'; a clamp must not invent one"


def test_a_clamp_is_recorded_and_the_reply_still_translates():
    """The clamp is a bound on what the model may generate: it must announce itself in the record
    (a silent bound is a lie), and the OpenAI -> internal translation must survive it. _post is
    stubbed to refuse any URL it was not written to answer, so the request path is exercised and
    nothing is sent."""
    adapter = infer.OpenAI("vllm-swift", _swift())
    sent = {}
    real_post = infer._post

    def fake_post(url, body, timeout):
        assert url.endswith("/v1/chat/completions"), "unexpected request to %s" % url
        assert timeout == REQUEST_TIMEOUT, "the caller's timeout must reach the transport"
        sent["body"] = body
        return {"choices": [{"message": {"role": "assistant", "content": "OK",
                                         "reasoning_content": "thoughts"},
                             "finish_reason": "length"}],
                "usage": {"completion_tokens": 4541, "prompt_tokens": 60000}}

    infer._post = fake_post
    try:
        out = adapter.chat(_body(SENT_BUDGET), REQUEST_TIMEOUT)
    finally:
        infer._post = real_post

    assert "max_tokens" in sent["body"], "the stub saw no max_tokens at all"
    assert sent["body"]["max_tokens"] <= MAX_ALLOWED, (
        "chat() sent max_tokens %s: over the %d-token window with a >=%d-token prompt the "
        "endpoint answers 400" % (sent["body"]["max_tokens"], CTX, WORDS))
    assert out["message"]["content"] == "OK", "OpenAI response translation changed"
    assert out["message"]["thinking"] == "thoughts", "reasoning_content translation changed"
    assert out["done_reason"] == "length", "finish_reason 'length' must stay 'length'"
    assert out["eval_count"] == 4541, "usage.completion_tokens translation changed"
    assert out["prompt_eval_count"] == 60000, "usage.prompt_tokens translation changed"
    assert "max_tokens_clamped" not in sent["body"], (
        "the clamp is recorded in the response; a key in the request body is an unrecognised key "
        "and vLLM answers 400 for it -- this task's own defect")
    note = out.get("max_tokens_clamped")
    assert note is not None, (
        "a silent bound on what the model may generate is a lie: the caller must see that the "
        "budget was reduced and by how much")
    assert isinstance(note, dict), "the clamp notice must carry requested/sent/reason"
    assert note.get("requested") == SENT_BUDGET, (
        "the notice must record what was asked for: %r" % (note.get("requested"),))
    assert isinstance(note.get("sent"), int) and 0 < note["sent"] <= MAX_ALLOWED, (
        "the notice must record what was sent: %r" % (note.get("sent"),))
    assert note.get("reason"), "the notice must say why the budget was reduced"


def main():
    tests = [(name, fn) for name, fn in sorted(globals().items())
             if name.startswith("test_") and callable(fn)]
    failed = []
    for name, fn in tests:
        try:
            fn()
            print("PASS %s" % name)
        except Exception as exc:                                  # noqa: BLE001
            failed.append(name)
            print("FAIL %s: %s: %s" % (name, type(exc).__name__, exc))
    print("\n%d/%d passed" % (len(tests) - len(failed), len(tests)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
