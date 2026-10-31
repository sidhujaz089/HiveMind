#!/usr/bin/env python3
"""No suite under scripts/tests/ may drive the live model. Model-driven evals live in scripts/evals/.

Every runner here -- coder.py's sandbox baseline, merge_gate, run_tests, session_end -- globs
scripts/tests/test_*.py. test_scope_taxonomy.py sat in that glob: the t248 golden-set eval,
16,000-token deliberations against qwen3.8:27b-mtp-bf16. Declared allowed-to-fail, so its
result was ignored -- but it still RAN, in every baseline of every dispatch, loading a 54 GB
model behind the scheduler's back. Found 2026-09-23 when it held the bf16 resident through a
benchmark the scheduler had been paused for; its progress file was also leaking into build
diffs (p18 carried scope-progress-golden.jsonl). A suite is a fixture that decides in seconds;
an eval is a measurement someone runs on purpose. This keeps them apart structurally.

The pattern is the direct call. A model reached some other way is what the Ollama load tracer
(scripts/ollama_callers.py) is for: it names every process that loads a model.
"""
# META-SUITE: it judges the other suites, so pre-commit runs it whenever scripts/tests changes.
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from library_checks import _code as code_only  # noqa: E402  -- prose ABOUT a call is not a call
LIVE = re.compile(r"\bdeliberate\.deliberate\(|\bask_q16\.ask\(")
# A suite that REPLACES the model path in the same file is exercising the harness, not the model:
# test_ask_q16_finish.py drives ask_q16.ask() against a fake console (2026-09-24) and was flagged,
# which refused a Gemma dispatch at baseline. The stub is the evidence it is not live.
STUBBED = re.compile(r"\bconsole\.ask\s*=|\bdeliberate\.deliberate\s*=|\bsys\.modules\[\s*[\"']console")


def main():
    bad = []
    for f in sorted((ROOT / "scripts" / "tests").glob("*.py")):
        if f.name == Path(__file__).name:
            continue
        code = code_only(f.read_text(encoding="utf-8", errors="replace"))
        if STUBBED.search(code):
            continue
        for i, line in enumerate(code.splitlines(), 1):
            if LIVE.search(line):
                bad.append(f"{f.relative_to(ROOT)}:{i}  {line.strip()[:100]}")
    ok_evals = (ROOT / "scripts" / "evals" / "eval_scope_taxonomy.py").is_file()
    print("  PASS  no suite drives the live model" if not bad else
          "  FAIL  these suites drive the live model -- move them to scripts/evals/:\n    "
          + "\n    ".join(bad))
    print(f"  {'PASS' if ok_evals else 'FAIL'}  the t248 eval lives in scripts/evals/")
    fails = bool(bad) + (not ok_evals)
    print(f"\n{2 - fails} passed, {fails} failed")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
