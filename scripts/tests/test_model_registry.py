#!/usr/bin/env python3
"""The digest lock must resolve sampling with Ollama unreachable, and must catch drift.

WHY
---
A model's identity should not depend on a subprocess answering. `models/tags.lock.json` is
committed so `settings.digests()` knows what every tag IS before anything is running -- that
is the whole point of pinning it. A lock that silently falls back to asking Ollama would pass
every test on a box where Ollama happens to be up, and fail on a fresh clone at the worst
moment.

The drift case matters more than it looks. A tag whose digest changed still WORKS -- it loads,
it answers, it scores. It is simply no longer the model the measurements were taken on.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
LI = REPO / "projects" / "local-inference"
LOCK = LI / "models" / "tags.lock.json"
REGISTRY = LI / "models" / "REGISTRY.json"
sys.path.insert(0, str(LI))
import settings                                              # noqa: E402


def offline(expr):
    """Evaluate `expr` in a fresh interpreter with no `ollama` on PATH."""
    env = dict(os.environ, PATH="/nonexistent")
    code = ("import sys; sys.path.insert(0, %r)\nimport settings\nprint(%s)" % (str(LI), expr))
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                       env=env, timeout=120)
    return (r.stdout.strip(), r.stderr.strip())


def main():
    fails = []

    for f in (LOCK, REGISTRY):
        if not f.is_file():
            fails.append(f"{f.name} is missing -- identity falls back to name matching")
    if fails:
        for x in fails:
            print("FAIL:", x)
        print("test_model_registry: FAILED")
        return 1

    lock = json.loads(LOCK.read_text())["tags"]
    reg = json.loads(REGISTRY.read_text())
    print(f"lock: {len(lock)} tag(s); registry: {len(reg['base_models'])} base + "
          f"{len(reg['aliases'])} alias(es)")

    # 1. EVERY DECLARED TAG IS PINNED.
    #    A model another engine serves (`server`, e.g. Swift on vLLM) has no Ollama digest; it is
    #    pinned by its own `pin` -- the served files' hash and the image id (docs/ENGINES.md).
    #    An OLLAMA on another device (the ThinkPad, 2026-10-12) does have a digest, so its tags are
    #    held to the lock exactly like this box's -- not excused as "another engine".
    servers = json.loads((REGISTRY.parent / "SERVERS.json").read_text())["servers"]
    other = {m["tag"]: m for m in reg["base_models"] if m.get("server")
             and (servers.get(m["server"]) or {}).get("engine") != "ollama"}
    declared = [m["tag"] for m in reg["base_models"] if m["tag"] not in other] + \
               [a["tag"] for a in reg["aliases"]]
    unpinned = [t for t in declared if t not in lock] + \
               [t for t, m in other.items() if not ((m.get("pin") or {}).get("files")
                                                     and (m.get("pin") or {}).get("image"))]
    print(f"  [{'ok' if not unpinned else 'BAD'}]    every declared tag has a pinned digest")
    if unpinned:
        fails.append(f"declared but not pinned: {unpinned}")

    # 2. AN ALIAS SHARES ITS SOURCE'S DIGEST. That is what makes the pair cost one load, and
    #    what lets sampling be inherited at all.
    for a in reg["aliases"]:
        if a["tag"] in lock and a["from"] in lock:
            same = lock[a["tag"]] == lock[a["from"]]
            print(f"  [{'ok' if same else 'BAD'}]    {a['tag']} shares the digest of "
                  f"{a['from']}")
            if not same:
                fails.append(f"{a['tag']} is declared a copy of {a['from']} but the pinned "
                             f"digests differ -- it is a different model under a name that "
                             f"says otherwise")

    # 3. IT RESOLVES WITH OLLAMA UNREACHABLE. The negative control for the whole design.
    out, err = offline("len(settings.digests())")
    n = int(out) if out.isdigit() else -1
    print(f"  [{'ok' if n == len(lock) else 'BAD'}]    with no ollama on PATH, "
          f"{n} tag(s) still resolve (lock has {len(lock)})")
    if n != len(lock):
        fails.append(f"offline resolution returned {n} tags, expected {len(lock)}: {err[:200]}")

    # A same-digest copy (an `ollama cp`) must resolve by digest. Only checkable while one is
    # declared: the Duo's copies were deleted 2026-10-06, so this may have nothing to test.
    copy = next((a for a in reg["aliases"] if a["tag"] in lock and a["from"] in lock), None)
    if copy is None:
        print("  [SKIP]  no same-digest copy is declared, so copy resolution is not tested")
    else:
        out, _ = offline(f"settings.resolve_sampling({copy['tag']!r})[1]")
        print(f"  [{'ok' if out == 'digest' else 'BAD'}]    offline, a copy still resolves by "
              f"digest -> {out!r}")
        if out != "digest":
            fails.append(f"offline, {copy['tag']} resolved by {out!r} rather than by digest")

        out, _ = offline(f"settings.sampling({copy['tag']!r}) == "
                         f"settings.sampling({copy['from']!r})")
        print(f"  [{'ok' if out == 'True' else 'BAD'}]    offline, the copy's sampling equals "
              f"its source's -> {out}")
        if out != "True":
            fails.append("offline, a copy did not inherit its source's sampling")

    # 4. THE DRIFT DETECTOR ACTUALLY DETECTS. Corrupt a digest in a THROWAWAY copy of the
    #    lock and confirm models.py verify fails. Without this the check above cannot be
    #    told from one that always says ok.
    import tempfile
    original = LOCK.read_text()
    try:
        bad = json.loads(original)
        victim = sorted(bad["tags"])[0]
        bad["tags"][victim] = "0000deadbeef"
        LOCK.write_text(json.dumps(bad, indent=1) + "\n")
        r = subprocess.run([sys.executable, str(REPO / "scripts" / "models.py"), "verify"],
                           capture_output=True, text=True, timeout=120)
        caught = r.returncode != 0 and "DRIFT" in (r.stdout + r.stderr).upper()
        print(f"  [{'ok' if caught else 'BAD'}]    a corrupted digest is reported as DRIFT "
              f"(rc={r.returncode})")
        if not caught:
            fails.append("models.py verify did not fail on a deliberately corrupted digest -- "
                         "the drift check cannot fail, so it proves nothing")
    finally:
        LOCK.write_text(original)                            # always put it back

    # 5. AN UNDECLARED TAG IS A PROBLEM IF, AND ONLY IF, A ROLE SERVES IT.
    #
    #    THE RULE CHANGED ON 2026-09-22, deliberately, and this assertion changed with it --
    #    which is worth being explicit about, because "edit the test to fit" is the failure
    #    this repository names most often. The old rule was "every tag on the box is
    #    declared". Q4 added it on 2026-09-03 for a good reason (a model nothing knows about
    #    cannot resolve its sampling), but it is a CLOSED-WORLD list over a directory Jas
    #    experiments in: `ollama pull qwen3.8` mints a `:latest` alias by itself, and the
    #    invariant check sat red for five days over one, blocking session_end, while nothing
    #    was wrong. Jas, 2026-09-18: "I want whatever the configuration is to be model
    #    agnostic. I plan to experiment with many local models, so I don't want to constrain
    #    it."
    #
    #    Q4's actual concern is PRESERVED and now tested harder: a model that something
    #    DEPENDS ON must be pinned, because a measurement recorded under a name has to be
    #    tied to the weights that produced it. What is dropped is the claim that a tag
    #    nobody serves is a defect. SERVING.json is the dynamic source that makes the
    #    distinction possible; before it there was no way to ask "does anything use this?"
    #    The second case below is the load-bearing one -- it fails if anyone widens this
    #    into "undeclared is always fine".
    import importlib.util
    spec = importlib.util.spec_from_file_location("mp", REPO / "scripts" / "models.py")
    mp = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mp)
    real_live, real_serving = mp.live, mp.serving
    try:
        mp.live = lambda: {**real_live(), "zzz-undeclared-copy:latest": "deadbeef0000"}

        mp.serving = lambda: {}
        rows, problems = mp.report()
        noisy = any("zzz-undeclared-copy" in p for p in problems)
        state = next((r["state"] for r in rows if r["tag"].startswith("zzz-undeclared")), None)
        print(f"  [{'ok' if not noisy else 'BAD'}]    a tag nothing serves is listed, not "
              f"flagged (state={state})")
        if noisy:
            fails.append("an unused tag on the box was reported as a problem -- that is the "
                         "closed-world rule that made every experiment a failed check")

        mp.serving = lambda: {"zzz-undeclared-copy:latest": ["coder"]}
        rows, problems = mp.report()
        named = any("zzz-undeclared-copy" in p for p in problems)
        say = next((p for p in problems if "zzz-undeclared-copy" in p), "")
        print(f"  [{'ok' if named else 'BAD'}]    a tag a ROLE serves must be declared "
              f"and pinned")
        if not named:
            fails.append("a tag SERVING.json depends on was not reported -- a role can call "
                         "a model whose weights nothing pins, and a measurement recorded "
                         "under that name cannot be tied to what produced it")
        elif "adopt" not in say:
            fails.append("the problem does not name the verb that fixes it -- the old hint "
                         "said `ensure`, which cannot adopt an undeclared tag at all")

        mp.serving = lambda: {"zzz-not-on-this-box:latest": ["auditor"]}
        _, problems = mp.report()
        missing = any("zzz-not-on-this-box" in p for p in problems)
        print(f"  [{'ok' if missing else 'BAD'}]    a role naming a model the box does NOT "
              f"have is reported")
        if not missing:
            fails.append("a role pointing at an absent model was silent -- that role has "
                         "nothing to call and the failure would arrive at dispatch time")
    finally:
        mp.live, mp.serving = real_live, real_serving

    for x in fails:
        print("FAIL:", x)
    print("test_model_registry:", "FAILED" if fails else "ok")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
