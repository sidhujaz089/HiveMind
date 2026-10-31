#!/usr/bin/env python3
"""The coder's system prompt carries the startup context.

WHY. console.py gives a model hot memory, retrieval, skills and the
contract. coder.py did not, so the build lane worked without any of it; the models named the
gap unprompted. (This suite also covered the Duo's raw call record until the Duo scripts were
deleted on 2026-10-06, Jas: "We dont need the duo and sa scripts either.")
"""
import sys
from pathlib import Path

REPO = Path("/home/user/ai-workspace")
LI = REPO / "projects" / "local-inference"
sys.path.insert(0, str(LI))
import coder                                                 # noqa: E402


def has_startup_context(text):
    return "memory.py recall" in text and "AGENTS.md" in text


def main():
    fails = []

    def check(label, cond, detail=""):
        print(f"  [{'ok' if cond else 'BAD'}]    {label}{'  ' + detail if detail else ''}")
        if not cond:
            fails.append(f"{label} {detail}")

    text = coder.system_prompt()
    check("coder receives the startup context", has_startup_context(text), f"{len(text)} chars")

    # NEGATIVE CONTROL: a prompt without the context must be detected, or the check above
    # cannot fail.
    check("control: a bare prompt is detected as missing the context",
          not has_startup_context("You are a coder. Edit the file."))

    for x in fails:
        print("FAIL:", x)
    print("test_raw_capture:", "FAILED" if fails else "ok")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
