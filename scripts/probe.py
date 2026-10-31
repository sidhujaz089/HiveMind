#!/usr/bin/env python3
"""Throw strange inputs at a function and report what breaks. The checking a model CAN do.

    python3 scripts/probe.py scripts/pysource.py constant
    python3 scripts/probe.py scripts/check_deployed.py source_defaults --compare main
    python3 scripts/probe.py --self-test

WHY THIS EXISTS
---------------
Every real defect the local agent's patches contained was caught by **probing**, not by tests:

  * `t147`'s patch passed every assertion and crashed on three signatures the test did not
    cover. Found by hand-running seven odd inputs (`L-106`).
  * `t148`'s oracle was impossible, found by implementing the fix myself and watching it fail
    (`L-107`).

Generating odd inputs and running them is **enumeration, not judgement**. It needs no taste,
it produces evidence a person reads in seconds, and it is exactly what an idle local GPU is
good at. Deciding whether a crash *matters* stays with the reviewer, and that part is fast.

That is the division this repository arrived at: **the model probes, the human judges.**

WHAT WORKS, AND WHAT DOES NOT — MEASURED 2026-08-25
---------------------------------------------------
**Mechanical probes: work.** Empty, `None`, blank, unicode, very long, nested delimiters,
embedded quotes and newlines, numbers where strings go. Run against the exact patch the model
proposed for `t147`, they found **four crashes** the test suite passed over.

**`--model`: does NOT work, and the claim it was built on was mine.** I argued the local
model could take over probing because enumeration is not judgement. Tested against that same
`t147` patch it produced ten probes that all returned `{}` and caught nothing. Re-prompted to
give domain-realistic values it produced `foo`, `bar`, `baz`, `qux` with one more argument
each — pattern completion, not probing. **The hand-probing that found the bug worked because
I knew what the function was FOR** and built inputs in that domain. That is judgement, and
this model does not have it. The flag is kept because it costs nothing and a better model may
change the answer; it is not a capability today.

**`--compare`: unreliable for anything path-dependent.** It writes the old revision to `/tmp`
and imports it, so a module computing paths from `__file__` resolves them somewhere else and
every difference is an artefact of the environment rather than the logic. Trust it for pure
functions; disbelieve it otherwise, and the warning below says so at runtime.

WHAT A RESULT MEANS
-------------------
An exception is **not** automatically a bug — a function may be entitled to raise. So the
report separates:

  * **raises** — with the exception type, for a human to judge
  * **differs** — under `--compare`, the same input answered differently before and after a
    change, which is the one that usually matters
  * **survives** — returned something

It never says "pass" or "fail", because it cannot know. It says what happened.
"""

import argparse
import ast
import importlib.util
import inspect
import json
import subprocess
import sys
import urllib.request

import loudcap                                               # noqa: E402
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
GATEWAY = "http://127.0.0.1:4000/v1/chat/completions"

MECHANICAL = [
    ("empty string", ""),
    ("blank", "   "),
    ("newline only", "\n"),
    ("None", None),
    ("zero", 0),
    ("negative", -1),
    ("empty list", []),
    ("embedded quote", "it's \"quoted\""),
    ("embedded newline", "a\nb"),
    ("unbalanced paren", "f(a"),
    ("nested parens", "f(g(h(1)))"),
    ("comma inside quotes", 'x = "a,b"'),
    ("unicode", "héllo — ✓ 日本語"),
    ("very long", "x" * 20000),
    ("null byte", "a\x00b"),
    ("looks like code", "def f(a=list(), *, b: int = 1): pass"),
]


def load_function(path, name):
    path = Path(path)
    spec = importlib.util.spec_from_file_location(path.stem, path)
    mod = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(path.resolve().parent))
    spec.loader.exec_module(mod)
    fn = getattr(mod, name, None)
    if fn is None:
        raise SystemExit(f"{path}: no function named {name}")
    return fn


def call(fn, value, fixed=()):
    """Probe the FIRST argument; supply the rest from `fixed`, then from defaults.

    The first version skipped any function with a second required argument, which silently
    excluded most of this repository -- `constant(src, name)` among them -- and reported
    "16 skipped" as though it had checked something. A prober that quietly declines to probe
    is the same shape as every silent-skip defect this week.
    """
    sig = inspect.signature(fn)
    args, fixed = [], list(fixed)
    for i, p in enumerate(sig.parameters.values()):
        if p.kind in (p.VAR_POSITIONAL, p.VAR_KEYWORD):
            continue
        if i == 0:
            args.append(value)
        elif fixed:
            args.append(fixed.pop(0))
        elif p.default is not p.empty:
            break
        else:
            return "skipped", (f"needs {p.name!r} too — pass it with "
                               f"--fixed '[\"...\"]'")
    try:
        return "survives", repr(fn(*args))[:120]
    except Exception as e:                              # noqa: BLE001
        return "raises", f"{type(e).__name__}: {e}"[:120]


def model_probes(path, name, model="local", n=10):
    """Ask the local model for awkward inputs, having shown it the function."""
    src = Path(path).read_text(encoding="utf-8")
    try:
        tree = ast.parse(src)
        body = next(ast.unparse(node) for node in ast.walk(tree)
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and node.name == name)
    except (SyntaxError, StopIteration):
        body = src[:4000]
    # Generic "awkward values" produced ten probes that all returned {} and caught nothing.
    # The hand-probing that DID find the t147 crash worked because it asked what the
    # function is FOR and built inputs in that domain -- signatures with a call default, a
    # comma inside a string. So say the domain out loud and demand realistic inputs.
    prompt = (f"Here is a Python function:\n\n"
              f"{loudcap.cap(body, 6000, 'function body over the probe prompt budget')}\n\n"
              f"Work out what KIND of value its first argument is meant to be — read the "
              f"code, not just the name. Then give {n} REALISTIC values of exactly that "
              f"kind which are awkward for this specific logic: shapes it might mishandle, "
              f"parse wrongly, or silently skip. Not type errors — a value of the RIGHT "
              f"type that this code gets WRONG.\n\n"
              f"Reply with ONE JSON array of those values and nothing else. No prose.")
    payload = {"model": model, "temperature": 0, "max_tokens": 1500,
               "chat_template_kwargs": {"enable_thinking": False},
               "messages": [{"role": "user", "content": prompt}]}
    try:
        req = urllib.request.Request(GATEWAY, json.dumps(payload).encode(),
                                     {"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=300) as f:
            out = json.load(f)["choices"][0]["message"]["content"]
    except Exception as e:                              # noqa: BLE001
        return [], f"model unavailable: {type(e).__name__}"
    if "</think>" in out:
        out = out.split("</think>", 1)[1]
    i, j = out.find("["), out.rfind("]")
    if i < 0 or j < 0:
        return [], "no JSON array in the reply"
    try:
        vals = json.loads(out[i:j + 1])
    except json.JSONDecodeError as e:
        return [], f"unparseable: {e}"
    return [(f"model #{k + 1}", v) for k, v in enumerate(vals) if not isinstance(v, dict)], ""


def at_revision(path, rev):
    """The same file as of a git revision, written somewhere importable."""
    r = subprocess.run(["git", "-C", str(REPO), "show", f"{rev}:{path}"],
                       capture_output=True, text=True, timeout=60)
    if r.returncode:
        return None
    tmp = Path("/tmp") / f"probe-{rev.replace('/', '_')}-{Path(path).name}"
    tmp.write_text(r.stdout, encoding="utf-8")
    return tmp


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("path", nargs="?")
    ap.add_argument("name", nargs="?")
    ap.add_argument("--model", default=None,
                    help="also ask a local model for probes (e.g. --model local)")
    ap.add_argument("--compare", default=None,
                    help="git revision to compare against, e.g. main")
    ap.add_argument("--fixed", default="[]",
                    help='JSON list of values for the remaining required arguments')
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args(argv)
    try:
        a.fixed = json.loads(a.fixed)
    except json.JSONDecodeError as e:
        raise SystemExit(f"--fixed is not valid JSON: {e}")

    if a.self_test:
        def crashy(s):
            return s.upper()                    # dies on None and on ints
        bad = 0
        results = [call(crashy, v) for _, v in MECHANICAL]
        kinds = {k for k, _ in results}
        print("\nprobe self-test\n")
        for label, want in (("finds a crash", "raises"), ("finds a survivor", "survives")):
            ok = want in kinds
            bad += not ok
            print(f"  {'[ok]  ' if ok else '[BAD] '} {label}")
        n_raise = sum(1 for k, _ in results if k == "raises")
        ok = n_raise >= 3
        bad += not ok
        print(f"  {'[ok]  ' if ok else '[BAD] '} several inputs crash str.upper "
              f"({n_raise} of {len(results)})")
        print(f"\n{3 - bad} passed, {bad} failed\n")
        return 1 if bad else 0

    if not a.path or not a.name:
        ap.print_help()
        return 1

    fn = load_function(a.path, a.name)
    probes = list(MECHANICAL)
    if a.model:
        extra, err = model_probes(a.path, a.name, a.model)
        probes += extra
        print(f"  model contributed {len(extra)} probe(s)"
              + (f" — {err}" if err else ""))

    old_fn = None
    if a.compare:
        p = at_revision(a.path, a.compare)
        if p:
            try:
                old_fn = load_function(p, a.name)
            except SystemExit:
                old_fn = None
        print(f"  comparing against {a.compare}"
              + ("" if old_fn else " — not present there, showing current only"))
        if old_fn:
            print("  NOTE: the old revision is imported from /tmp, so anything it derives "
                  "from __file__\n        resolves elsewhere. Differences in a "
                  "path-dependent module are artefacts.")

    print(f"\nprobing {a.name}() in {a.path} — {len(probes)} input(s)\n")
    raises, differs, survives, skipped = [], [], [], 0
    for label, value in probes:
        kind, detail = call(fn, value, a.fixed)
        if kind == "skipped":
            skipped += 1
            continue
        line = f"  {label:<22} {kind:<9} {detail}"
        if old_fn:
            okind, odetail = call(old_fn, value, a.fixed)
            if (okind, odetail) != (kind, detail):
                differs.append((label, f"{a.compare}: {okind} {odetail}",
                                f"now: {kind} {detail}"))
                line += "   <-- DIFFERS"
        (raises if kind == "raises" else survives).append(label)
        print(line)

    print("\n" + "-" * 70)
    print(f"  {len(survives)} returned, {len(raises)} raised"
          + (f", {len(differs)} differ from {a.compare}" if old_fn else "")
          + (f", {skipped} skipped" if skipped else ""))
    for label, before, after in differs:
        print(f"\n  DIFFERS on {label}:\n    {before}\n    {after}")
    print("\n  An exception is not automatically a bug — a function may be entitled to")
    print("  raise. This reports what happened; whether it matters is yours to judge.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
