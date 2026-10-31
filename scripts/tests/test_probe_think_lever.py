#!/usr/bin/env python3
"""t1218 red fixture: scripts/probe_think_lever.py must exist, and its rows must be real.

Run:  python3 scripts/tests/test_probe_think_lever.py

RED ON MAIN for one reason: scripts/probe_think_lever.py does not exist, so every
test fails at import. When the module lands, each assertion is about behaviour the
task demands -- pinned sampling, per-path spelling, server-sourced token counts,
n>=3, and a scored answer -- not about the module's shape.

No model call, no live server, no fixed /tmp path: the module takes an injected
transport, so this fixture drives it with fakes. That seam is part of the contract.
"""
import json
import subprocess
import sys
import tempfile
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODULE = ROOT / "scripts" / "probe_think_lever.py"
MODEL = "qwen3.8:27b-mtp-q4_K_M"

# One shared answer string whose length/4 differs sharply from the server-reported
# token counts below, so a len(text)/4 estimator cannot satisfy the unit tests.
ANSWER = "7 + 5 * 2 = 17. That is the whole answer."

FAILS = []


def check(cond, msg):
    if not cond:
        FAILS.append(msg)
        print(f"  [FAIL] {msg}")
    return bool(cond)


def load():
    spec = importlib.util.spec_from_file_location("probe_think_lever", MODULE)
    if spec is None or getattr(spec, "loader", None) is None:
        raise FileNotFoundError(f"{MODULE} does not exist")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def ollama_transport(path, payload):
    """Answers ONLY the ollama path; refuses anything else, so a stub cannot
    accidentally serve a vllm call and make the unit test pass for the wrong reason."""
    if path != "ollama":
        raise AssertionError(f"ollama stub asked for path {path!r}")
    return {"model": payload["model"], "done_reason": "stop", "eval_count": 137,
            "message": {"role": "assistant", "content": ANSWER,
                        "thinking": "multiply first, then add"}}


def vllm_transport(path, payload):
    if path != "vllm":
        raise AssertionError(f"vllm stub asked for path {path!r}")
    return {"id": "cmpl-1",
            "choices": [{"message": {"role": "assistant", "content": ANSWER,
                                     "reasoning_content": "multiply first, then add"}}],
            "usage": {"prompt_tokens": 31, "completion_tokens": 214}}


def short_transport(path, payload):
    """A stack that answers twice and stops -- the n<3 case the probe must refuse."""
    return ollama_transport(path, payload)


def n_samples(transport, mod, path, prompt_id, n):
    """Build n responses through the module's own prompt builder, so the fixture
    is not inventing a prompt the module never sends."""
    out = []
    for i in range(n):
        body = mod.build_request(MODEL, path, "high", prompt_id,
                                 seed=mod.PUBLISHED[MODEL]["seed"],
                                 temperature=mod.PUBLISHED[MODEL]["temperature"])
        out.append(transport(path, body))
    return out


def test_module_exists():
    check(MODULE.is_file(), f"{MODULE} is missing -- this is the whole deliverable")
    load()


def test_grid_shape_is_real_and_not_placeholdered():
    mod = load()
    levels = list(mod.THINK_LEVELS)
    check(set(levels) == {False, "low", "medium", "high", "max"},
          f"THINK_LEVELS must be exactly false/low/medium/high/max, got {levels}")
    check(mod.MIN_N == 3, f"MIN_N must be 3, got {getattr(mod, 'MIN_N', None)}")
    prompts = mod.PROMPTS
    check(len(prompts) >= 4, f"need >=4 prompt types, got {len(prompts)}")
    texts = [p["prompt"] for p in prompts.values()]
    check(len(set(texts)) == len(texts),
          "the grid must use DISTINCT prompt texts, not one placeholder repeated")
    kinds = set(p["kind"] for p in prompts.values())
    check(len(kinds) >= 4, f"each prompt is a distinct type; kinds seen: {kinds}")
    for req in ("code-repair", "answer-in-2-sentences", "multi-hop-arithmetic",
                "long-file-review"):
        check(req in prompts, f"the grid needs the {req!r} prompt type (task item 1)")
    ids = list(prompts)
    cells = list(mod.cells(MODEL, n=3))
    seen_prompts = set(c["prompt_id"] for c in cells)
    seen_levels = set(c["think"] for c in cells)
    check(seen_prompts == set(ids),
          f"cells() must cover every prompt id, missing {set(ids) - seen_prompts}")
    check(seen_levels == set(levels),
          f"cells() must cover every think level, missing {set(levels) - seen_levels}")
    check(all(c["n"] == 3 for c in cells), "every cell must be n=3, not n=1")
    keys = [(c["prompt_id"], c["think"], c["rep"]) for c in cells]
    check(len(keys) == len(set(keys)), "cells() repeats (prompt, think, rep) keys")
    check(len(cells) >= 4 * len(levels) * 3,
          f"grid is too thin: {len(cells)} cells for {len(ids)} prompts x "
          f"{len(levels)} levels x 3 reps")


def test_sampling_is_pinned_and_greedy_is_refused():
    mod = load()
    prof = mod.PUBLISHED[MODEL]
    for field in ("temperature", "top_p", "top_k", "seed", "allow_greedy"):
        check(field in prof, f"PUBLISHED[{MODEL}] is missing {field!r}")
    body = mod.build_request(MODEL, "ollama", "high", "multi-hop-arithmetic",
                             seed=prof["seed"], temperature=prof["temperature"])
    opts = body.get("options", {})
    check(opts.get("seed") == prof["seed"],
          f"seed must be pinned to PUBLISHED value: got {opts.get('seed')!r}")
    check(opts.get("temperature") == prof["temperature"],
          f"temperature must be pinned to PUBLISHED value: "
          f"got {opts.get('temperature')!r}")
    check(opts.get("top_p") == prof["top_p"] and opts.get("top_k") == prof["top_k"],
          "the published top_p/top_k must be carried, not left to server defaults")
    for field in ("temperature", "seed"):
        try:
            mod.build_request(MODEL, "ollama", "high", "multi-hop-arithmetic",
                              seed=None if field == "seed" else prof["seed"],
                              temperature=None if field == "temperature"
                              else prof["temperature"])
            check(False, f"build_request accepted an unpinned {field}; an unpinned "
                         "sampling run cannot measure the lever")
        except (ValueError, TypeError):
            pass
    check(prof["allow_greedy"] is False,
          "Qwen forbids greedy in thinking mode; PUBLISHED must say so")
    try:
        mod.build_request(MODEL, "ollama", "high", "multi-hop-arithmetic",
                          seed=prof["seed"], temperature=0.0)
        check(False, "greedy (temperature=0) was accepted for a model whose "
                     "published profile forbids it -- that measures the sampler, not the lever")
    except (ValueError, TypeError):
        pass


def test_each_path_gets_its_own_spelling():
    mod = load()
    prof = mod.PUBLISHED[MODEL]
    kw = dict(seed=prof["seed"], temperature=prof["temperature"])
    o = mod.build_request(MODEL, "ollama", "high", "multi-hop-arithmetic", **kw)
    v = mod.build_request(MODEL, "vllm", "high", "multi-hop-arithmetic", **kw)
    check(o.get("think") == "high",
          f"the Ollama path must send top-level 'think', got {o.get('think')!r}")
    check("reasoning_effort" not in json.dumps(o),
          "Ollama's body leaked a reasoning_effort key -- that is the vLLM spelling")
    ctk = v.get("chat_template_kwargs")
    check(isinstance(ctk, dict) and ctk.get("reasoning_effort") == "high",
          f"the vLLM path must send chat_template_kwargs.reasoning_effort, got {ctk!r}")
    check("think" not in v, "vLLM's body carried a top-level 'think' -- the two "
                            "spellings must not be mixed; recording which stack honours "
                            "which is the point of the task")
    check(mod.observed_spelling(ollama_transport("ollama", o), "ollama") == "think",
          "observed_spelling must report the Ollama spelling from a real response")
    check(mod.observed_spelling(vllm_transport("vllm", v), "vllm")
          == "chat_template_kwargs.reasoning_effort",
          "observed_spelling must report the vLLM spelling from a real response")
    check(mod.observed_spelling({"choices": [{"message": {"content": "x"}}]}, "vllm")
          == "none",
          "a response with no reasoning field at all must read as 'none' -- a lever "
          "that produced no thinking is a finding, not a silent pass")


def test_unit_is_server_reported_tokens_not_characters():
    mod = load()
    pid = "multi-hop-arithmetic"
    samples = n_samples(ollama_transport, mod, "ollama", pid, 3)
    row = mod.build_row(MODEL, "ollama", "high", pid, samples)
    check(row.get("n") == 3, f"row must record n=3, got {row.get('n')!r}")
    check(row.get("token_source") == "eval_count",
          f"unit must come from the server's eval_count, got {row.get('token_source')!r}")
    check(row.get("effort_tokens") == 137 * 3,
          f"effort_tokens must be the sum of eval_count, got {row.get('effort_tokens')!r}")
    check(row.get("unit") == "tokens", f"unit must be 'tokens', got {row.get('unit')!r}")
    check("chars" not in str(row.get("unit")), "unit says chars; the char/token thread "
                                               "is what this column exists to close")
    check(row.get("effort_tokens") != int(len(ANSWER) / 4) * 3,
          "effort_tokens equals len(content)/4 -- a character estimate, the exact "
          "artefact the RCA (Candidate 7) left open")
    vsamples = n_samples(vllm_transport, mod, "vllm", pid, 3)
    vrow = mod.build_row(MODEL, "vllm", "high", pid, vsamples)
    check(vrow.get("token_source") == "completion_tokens",
          f"vLLM's token source is usage.completion_tokens, got {vrow.get('token_source')!r}")
    check(vrow.get("effort_tokens") == 214 * 3,
          f"vLLM effort_tokens must be the sum of completion_tokens, got {vrow.get('effort_tokens')!r}")


def test_it_refuses_to_emit_a_row_below_n_three():
    mod = load()
    pid = "multi-hop-arithmetic"
    two = n_samples(ollama_transport, mod, "ollama", pid, 2)
    try:
        mod.build_row(MODEL, "ollama", "high", pid, two)
        check(False, "build_row emitted a row from n=2; a single-sample claim must be "
                     "structurally unstateable (task item 5)")
    except mod.InsufficientSamples:
        pass
    with tempfile.TemporaryDirectory() as td:
        led = Path(td) / "measurements.jsonl"
        led.write_text("", encoding="utf-8")
        before = led.read_text(encoding="utf-8")
        try:
            mod.self_test(ledger=led, transport=short_transport, path="ollama",
                          n=2, model=MODEL)
        except mod.InsufficientSamples:
            pass
        check(led.read_text(encoding="utf-8") == before,
              "self_test wrote a ledger row from fewer than 3 samples")


def test_answers_are_scored_mechanically():
    mod = load()
    pid = "multi-hop-arithmetic"
    check(mod.score_answer(pid, "7 + 5 * 2 = 17. That is the whole answer.") == 1,
          "the correct arithmetic answer must score 1")
    check(mod.score_answer(pid, "7 + 5 * 2 = 16. That is the whole answer.") == 0,
          "a wrong arithmetic answer must score 0 (exact match on the published expected value)")
    spid = "answer-in-2-sentences"
    check(mod.score_answer(spid, "The five sites omit files. The prompt is built from base plus tail.") == 1,
          "a two-sentence answer must score 1")
    check(mod.score_answer(spid, "One. Two. Three. Four. Five.") == 0,
          "a five-sentence answer must score 0 -- sentence count is the check the "
          "RCA's 1597-token answer could not make")
    cpid = "code-repair"
    check(mod.score_answer(cpid, "print('ok')") == 1,
          "the emitted fix must be RUN; a fix that executes scores 1")
    check(mod.score_answer(cpid, "print(1/0)") == 0,
          "an emitted fix that raises must score 0 -- running it is the check")
    check(mod.score_answer(cpid, "this is not python((") == 0,
          "a syntactically invalid emitted fix must score 0")


def test_effort_and_quality_are_separate_columns():
    """The RCA's closed finding: high buys tokens, not quality per token. A row
    must be able to say 'lots of effort, zero quality' without collapsing."""
    mod = load()
    prof = mod.PUBLISHED[MODEL]
    pid = "multi-hop-arithmetic"

    def verbose_and_wrong(path, payload):
        r = ollama_transport(path, payload)
        r["eval_count"] = 5000
        r["message"]["content"] = "7 + 5 * 2 = 16. A long, confident, wrong answer."
        return r

    samples = n_samples(verbose_and_wrong, mod, "ollama", pid, 3)
    row = mod.build_row(MODEL, "ollama", "high", pid, samples)
    check(row.get("effort_tokens") == 15000,
          f"effort must be recorded from the server, got {row.get('effort_tokens')!r}")
    check(row.get("quality") == 0,
          "a high-effort run with a wrong answer must score quality 0 -- if effort "
          "and quality are one column, the probe measures the thing the RCA refuted")
    check("quality" in row and "effort_tokens" in row,
          "a row needs BOTH an effort column and a quality column (task item 6)")


def test_self_test_writes_a_row_stamped_probe_think_lever():
    mod = load()
    prof = mod.PUBLISHED[MODEL]
    with tempfile.TemporaryDirectory() as td:
        led = Path(td) / "measurements.jsonl"
        mod.self_test(ledger=led, transport=ollama_transport, path="ollama", model=MODEL)
        check(led.is_file(), "self_test wrote no ledger file -- the fixture's reason to exist")
        lines = [l for l in led.read_text(encoding="utf-8").splitlines() if l.strip()]
        check(len(lines) == 1, f"self_test must write exactly one cell's row, got {len(lines)}")
        row = json.loads(lines[0])
        check(row.get("probe") == "probe_think_lever",
              f"rows must be stamped probe=probe_think_lever so an ad-hoc row is "
              f"visible, got {row.get('probe')!r}")
        check(row.get("n") == 3, f"self_test must run n=3, got {row.get('n')!r}")
        check(row.get("path") == "ollama", f"path column must record the serving path, got {row.get('path')!r}")
        check(row.get("think") == "high", f"think column must record the lever level, got {row.get('think')!r}")
        check(row.get("seed") == prof["seed"], "the row must carry the pinned seed it ran with")
        check(row.get("temperature") == prof["temperature"],
              "the row must carry the pinned temperature it ran with")
        check(row.get("token_source") == "eval_count",
              "the row must say where its unit came from, got {row.get('token_source')!r}")
        check("quality" in row, "the self-test row needs the scored-quality column")
        check("what" in row and "unit" in row,
              "the row must fit the ledger's existing keys (workspace/measurements.jsonl)")
        check(str(mod.DEFAULT_LEDGER).endswith("workspace/measurements.jsonl"),
              f"the default ledger is not workspace/measurements.jsonl: {mod.DEFAULT_LEDGER}")


TESTS = [
    test_module_exists,
    test_grid_shape_is_real_and_not_placeholdered,
    test_sampling_is_pinned_and_greedy_is_refused,
    test_each_path_gets_its_own_spelling,
    test_unit_is_server_reported_tokens_not_characters,
    test_it_refuses_to_emit_a_row_below_n_three,
    test_answers_are_scored_mechanically,
    test_effort_and_quality_are_separate_columns,
    test_self_test_writes_a_row_stamped_probe_think_lever,
]

if __name__ == "__main__":
    print(f"\n{MODULE.name}: {len(TESTS)} tests\n")
    for fn in TESTS:
        try:
            fn()
        except Exception as e:                      # noqa: BLE001
            check(False, f"{fn.__name__} raised {type(e).__name__}: {e}")
    print(f"\n{len(FAILS)} failure(s) across {len(TESTS)} test(s)\n")
    sys.exit(1 if FAILS else 0)
