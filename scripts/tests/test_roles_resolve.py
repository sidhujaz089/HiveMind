#!/usr/bin/env python3
"""Every live-path model choice resolves through SERVING.json; a hard-coded tag is only a fallback.

The Q16 -> Q4 manager swap (Jas, 2026-09-23) is meant to be one edit to SERVING.json. Fourteen
call sites named qwen3.8:27b-mtp-bf16 directly and would have kept loading 54 GB behind it --
the hourly corpus job did exactly that during the swap's own benchmark. Pinned here:

  * roles.tag() reads a role from the file, and falls back LOUDLY when it cannot
  * in the live-path files, the literal tag appears only as a roles.tag() fallback
"""
import contextlib
import io
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import roles  # noqa: E402
from library_checks import _code as code_only  # noqa: E402

LIVE_PATH = ["scripts/ask_q16.py", "scripts/plan_run.py", "scripts/corpus_scripts.py",
             "scripts/truncation_review.py", "scripts/preflight_brief.py", "scripts/scope_queue.py",
             "scripts/merge_accounting.py", "scripts/dispatch_loop.py",
             "scripts/parallel_dispatch.py", "projects/local-inference/settings.py"]
TAG = "qwen3.8:27b-mtp-bf16"
PASSED = FAILED = 0


def report(name, got, want):
    global PASSED, FAILED
    ok = got == want
    PASSED += ok
    FAILED += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"  (got {got!r}, want {want!r})"))


def allowed(lines, i):
    """A literal is allowed as a roles.tag() fallback, as the fallback of dispatch_loop._serving()
    (which already prints when it is used), or as a key of the measured FOOTPRINT_GIB table --
    a fact about those weights, not a choice of model."""
    line, prev = lines[i - 1], lines[i - 2] if i > 1 else ""
    return ("roles.tag(" in line or "roles.for_job(" in line or "_serving(" in prev or "_serving(" in line
            or line.lstrip().startswith("FOOTPRINT_GIB"))


def main():
    print("roles.tag reads SERVING.json and falls back loudly:")
    real = roles.SERVING
    with tempfile.TemporaryDirectory() as tmp:
        f = Path(tmp) / "SERVING.json"
        f.write_text(json.dumps({"roles": {"manager": {"tag": "qwen3.8:27b-mtp-q4_K_M"}}}))
        roles.SERVING = f
        report("a declared role resolves to its tag", roles.tag("manager", TAG),
               "qwen3.8:27b-mtp-q4_K_M")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            got = roles.tag("auditor", TAG)
        report("an undeclared role falls back", got, TAG)
        report("  and says so", "falls back" in err.getvalue() or "falling back" in err.getvalue(), True)
        f.write_text("{ torn")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            got = roles.tag("manager", TAG)
        report("an unreadable file falls back", got, TAG)
        report("  and says so", "unreadable" in err.getvalue(), True)
    roles.SERVING = real

    print("\nThe live path names no model except as a fallback:")
    for rel in LIVE_PATH:
        code = code_only((ROOT / rel).read_text(encoding="utf-8", errors="replace"))
        lines = code.splitlines()
        stray = [i for i, l in enumerate(lines, 1) if TAG in l and not allowed(lines, i)]
        report(f"{rel}", stray, [])

    print(f"\n{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
