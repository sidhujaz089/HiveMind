#!/usr/bin/env python3
"""No inference call builds its own `options`. One source of truth, or a test that they match.

WHY THIS EXISTS. `settings.py` has said "one place for the model knobs" since it was written,
and it was not true. Every call site was free to write its own dict and they drifted:

    exam.py         temperature 0.1, think False
    interview.py    temperature 0.0 at two sites, 0.2 at a third
    model_warm.py   no num_ctx at all -- so it warmed at a size nothing asked for
    duo/probe       temperature 0.3
    askmemory       temperature 0.4
    settings.py     temperature 0.0   <- the file that was supposed to decide

Five temperatures and four context regimes for the same models. The staffing scores were all
taken at think=False and temperature 0.1 against a `coder` profile of think="low" and 0.0, so
they measured a configuration nobody chose and nobody would deploy. Jas: *"I think we're not
setting these up to succeed."*

This is the general form of the memory `consistent-outcome-is-a-constant`: **when a producer
and its consumer each carry their own list of what to cover, they drift, and the gap is
silent.** A comment saying "use settings.py" is not a mechanism. This is.

THE ALLOWLIST ONLY SHRINKS. The seven entries below are call sites that predate the contract
and were NOT converted on 2026-09-03, because several of them (duo, probe, askmemory) look like
deliberate exploratory sampling rather than drift, and changing a running research pair's
temperature as a side effect of a staffing fix is how this repository breaks things. They are
listed so the number is visible and can only go down. Adding to it is not a fix.
"""
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCAN = [REPO / "projects" / "local-inference", REPO / "scripts"]

# The one file allowed to define an options block.
SOURCE = "projects/local-inference/settings.py"

# Pre-existing, unconverted, each with the reason it was not touched on 2026-09-03.
# DO NOT ADD. Removing a line here is the only correct edit.
KNOWN_UNROUTED = {
    "projects/local-inference/askmemory.py": "temperature 0.4, looks deliberate for recall",
}

LITERAL = re.compile(r'"options"\s*:\s*\{')


def offenders():
    out = []
    for root in SCAN:
        for f in sorted(root.rglob("*.py")):
            rel = f.relative_to(REPO).as_posix()
            if rel == SOURCE or "/tests/" in rel or "__pycache__" in rel:
                continue
            for n, line in enumerate(f.read_text(encoding="utf-8", errors="replace")
                                     .splitlines(), 1):
                if LITERAL.search(line):
                    out.append((rel, n, line.strip()[:70]))
    return out


def main():
    fails = []
    found = offenders()
    by_file = {}
    for rel, n, line in found:
        by_file.setdefault(rel, []).append((n, line))

    print("Call sites building their own options block:")
    for rel in sorted(by_file):
        known = rel in KNOWN_UNROUTED
        mark = "known" if known else "NEW  "
        print(f"  [{mark}] {rel}: {len(by_file[rel])} site(s)")
        if not known:
            for n, line in by_file[rel]:
                fails.append(f"{rel}:{n} builds its own options -- use settings.options() "
                             f"or settings.request(): {line}")

    # The allowlist must shrink, never grow. An entry that no longer offends is a win and
    # should be deleted from KNOWN_UNROUTED; leaving it there hides the next regression.
    stale = [k for k in KNOWN_UNROUTED if k not in by_file]
    for k in stale:
        fails.append(f"{k} is in KNOWN_UNROUTED but no longer builds its own options -- "
                     f"delete the entry so the list keeps meaning something")

    # The converted files must STAY converted. This is the regression that matters: these are
    # the four the staffing decision runs through.
    for rel in ("projects/local-inference/exam.py",
                "projects/local-inference/calibrate.py",
                "projects/local-inference/interview.py",
                "projects/local-inference/console.py",
                "projects/local-inference/coder.py"):
        body = (REPO / rel).read_text(encoding="utf-8", errors="replace")
        # The module is imported under different aliases -- console.py uses `cfg`, coder.py
        # `_cfg`. Matching the literal "settings.options(" made the check pass or fail on the
        # import alias rather than on the behaviour, which is a check measuring the wrong
        # thing -- the exact defect this file exists to catch, one level up.
        ok = bool(re.search(r"\b(?:settings|cfg|_cfg|_settings)\.(?:options|request)\(", body))
        print(f"  [{'ok' if ok else 'FAIL'}]  {rel} routes through the contract")
        if not ok:
            fails.append(f"{rel} no longer uses settings.options()/request()")

    # The contract must actually carry the sampling knobs. A contract that returns three keys
    # is the bug wearing the fix's name.
    sys.path.insert(0, str(REPO / "projects" / "local-inference"))
    import settings                                          # noqa: E402
    # A model with a published recommendation must carry it in full. gpt-oss deliberately
    # carries fewer keys, because OpenAI publish no sampling values and inventing some to make
    # a test green is the failure this file exists to prevent.
    block = settings.options("coder", "qwen3.8:27b-mtp-bf16")
    for k in ("num_ctx", "num_predict", "temperature", "top_p", "top_k", "min_p",
              "repeat_penalty", "seed"):
        if k not in block:
            fails.append(f"settings.options() omits {k} for a model that has a published "
                         f"value for it")
    print(f"  [{'ok' if len(block) >= 8 else 'FAIL'}]  vendor block for Q16 carries "
          f"{len(block)} attributes")
    # None must never reach the wire.
    if any(v is None for v in block.values()):
        fails.append("settings.options() emitted a null -- omit the key instead")

    # think and keep_alive are NOT options -- they sit at the top level, which is exactly why
    # they drifted independently. request() must supply both.
    body = settings.request("coder", "m", [{"role": "user", "content": "x"}])
    for k in ("think", "keep_alive"):
        if k not in body:
            fails.append(f"settings.request() omits {k}")
    print(f"  [{'ok' if 'think' in body and 'keep_alive' in body else 'FAIL'}]  "
          f"settings.request() supplies think and keep_alive")

    # EVERY MODEL ON DISK HAS A SOURCED SAMPLING PROFILE, OR AN EXPLICIT UNKNOWN.
    #
    # This is the check that would have caught laguna-xs.2. Its Ollama Modelfile declares no
    # parameters at all, so it silently ran on backend defaults -- not ours and not Poolside's,
    # who publish temperature 0.7 / top_k 20 -- and it scored worst of six candidates. Nothing
    # anywhere said "this model's sampling is unknown", because an absence says nothing.
    #
    # A value without a citation is indistinguishable from a guess. Committing a guess as if it
    # were a decision is exactly what happened on 2026-09-03, when the contract pinned greedy
    # decoding against Qwen's published instruction not to.
    import subprocess
    try:
        listing = subprocess.run(["ollama", "list"], capture_output=True, text=True,
                                 timeout=60).stdout.splitlines()[1:]
        tags = [ln.split()[0] for ln in listing if ln.strip()]
    except Exception as e:                                   # noqa: BLE001
        tags = []
        print(f"  [skip]  ollama unreachable, cannot check on-disk models: {e}")
    print(f"\nSampling profile per model on disk ({len(tags)} found):")
    for tag in tags:
        src = settings.sampling_source(tag)
        if src is None:
            print(f"  [FAIL]  {tag}: no profile and no explicit unknown")
            fails.append(f"{tag} has no sampling profile in SAMPLING_BY_MODEL and is not "
                         f"marked unknown -- add one with its source URL, or mark it "
                         f"unknown. Do not let it run on defaults nobody chose.")
            continue
        if not src.get("source"):
            print(f"  [FAIL]  {tag}: profile has no source URL")
            fails.append(f"{tag}'s profile carries no source URL")
            continue
        kind = "UNKNOWN, recorded" if src.get("unknown") else "sourced"
        print(f"  [ok]    {tag}: {kind}")

    # And the seed is ours, always present -- it is what replaced greedy as the reproducibility
    # mechanism, so a block without it is not reproducible at vendor temperatures.
    if "seed" not in settings.options("coder", "qwen3.8:27b"):
        fails.append("settings.options() has no seed -- reproducibility now depends on it, "
                     "because temperature is no longer 0")

    # A COPY MUST INHERIT ITS SOURCE'S PROFILE, AND THIS PROVES BOTH DIRECTIONS.
    # `ollama cp qwen3.8:27b-mtp-bf16 q16-sa` made a tag with identical weights and no family
    # name in it; name-only matching returned seed-only and the Duo ran a day on the backend
    # defaults Qwen forbids. resolve_sampling() now falls back to the digest. The negative
    # case is the point: with no digest map, the same tag must go back to resolving nothing,
    # or this test cannot tell a working fallback from a lucky name.
    print("\nA copy inherits by digest, and only because the digest is there:")
    real = settings.digests()
    fake = "zzz-copy-of-bf16:latest"
    try:
        settings._DIGESTS = {**real, fake: real.get("qwen3.8:27b-mtp-bf16", "deadbeef"),
                             "qwen3.8:27b-mtp-bf16": real.get("qwen3.8:27b-mtp-bf16",
                                                              "deadbeef")}
        entry, how = settings.resolve_sampling(fake)
        ok = how == "digest" and settings.sampling(fake) == \
            settings.sampling("qwen3.8:27b-mtp-bf16")
        print(f"  [{'ok' if ok else 'BAD'}]    a name-less copy resolves by digest: how={how}")
        if not ok:
            fails.append(f"a same-digest copy did not inherit its source's profile (how={how})")

        settings._DIGESTS = {}
        entry2, how2 = settings.resolve_sampling(fake)
        neg = how2 == "none" and entry2 is None
        print(f"  [{'ok' if neg else 'BAD'}]    with no digest map it resolves nothing: "
              f"how={how2}  (the failure this fallback exists to prevent)")
        if not neg:
            fails.append(f"resolve_sampling invented a profile with no digest map (how={how2})")

        # Two same-digest tags disagreeing is a contradiction in the table, not a coin flip.
        settings._DIGESTS = {fake: "d1", "qwen3.8:27b": "d1", "gemma4:26b": "d1"}
        entry3, how3 = settings.resolve_sampling(fake)
        amb = how3 == "none"
        print(f"  [{'ok' if amb else 'BAD'}]    conflicting siblings refuse to resolve: "
              f"how={how3}")
        if not amb:
            fails.append("resolve_sampling picked one of two conflicting same-digest profiles")
    finally:
        settings._DIGESTS = real

    for x in fails:
        print("FAIL:", x)
    print("test_options_contract:", "FAILED" if fails else "ok")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
