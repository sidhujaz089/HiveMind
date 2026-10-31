#!/usr/bin/env python3
"""A candidate must be sat under the configuration it would actually be HIRED under.

TWO DEFAULTS IN exam.py THAT MEASURED THE HARNESS INSTEAD OF THE MODEL.

  * `--ctx` defaulted to 32768. num_ctx is a property of the MODEL and Ollama treats a
    different num_ctx as a DIFFERENT LOAD, so sitting a model at 32768 while it is resident at
    262144 evicts and reloads it -- 53 GB, silently, once per candidate.
  * `think` came from the shared `coder` profile for everyone. gpt-oss:20b is trained on
    OpenAI's harmony format where reasoning EFFORT is the control, and Ollama's `think` does
    not map to it monotonically: 881 chars of thinking at "low", 17,250 at False, 53,948 at
    "high". At the profile default it scored -1/5 and read as the weakest model in the field;
    at "high" it scored 3/5 AND 2/2 traps, the only candidate to manage both.

Both were found before round 2 ran, on 2026-09-03. Neither would have raised anything -- the
exam would simply have produced scores, and the scores would have been about the harness.
"""
import ast
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
EXAM = REPO / "projects" / "local-inference" / "exam.py"
sys.path.insert(0, str(REPO / "projects" / "local-inference"))
import settings                                              # noqa: E402


def main():
    fails = []
    src = EXAM.read_text()
    tree = ast.parse(src)

    # 1. --ctx MUST NOT CARRY A HARDCODED DEFAULT.
    bad_default = None
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "add_argument":
            names = [a.value for a in node.args if isinstance(a, ast.Constant)]
            if "--ctx" in names:
                for kw in node.keywords:
                    if kw.arg == "default" and isinstance(kw.value, ast.Constant) \
                            and kw.value.value is not None:
                        bad_default = kw.value.value
    print(f"  [{'BAD' if bad_default else 'ok'}]    --ctx has no hardcoded default "
          f"({'is ' + str(bad_default) if bad_default else 'None'})")
    if bad_default:
        fails.append(f"--ctx defaults to {bad_default}; it must default to None so that "
                     f"settings.model_ctx(model) decides. A different num_ctx is a different "
                     f"load, and the exam would reload the model for every candidate.")

    uses = "settings.model_ctx(" in src
    print(f"  [{'ok' if uses else 'BAD'}]    it resolves num_ctx with settings.model_ctx()")
    if not uses:
        fails.append("exam.py does not call settings.model_ctx()")

    # 2. think MUST BE OVERRIDABLE PER MODEL.
    has_think = "--think" in src and 'body["think"]' in src
    print(f"  [{'ok' if has_think else 'BAD'}]    reasoning is overridable per candidate")
    if not has_think:
        fails.append("exam.py has no per-model think override; gpt-oss:20b would sit at the "
                     "profile default, which measured -1/5 for a model that scores 3/5")

    # 3. AND THE RUN MUST SAY WHAT IT USED. A score whose configuration is not recorded
    #    cannot be compared with another score later -- that is L-147's shape.
    prints = "num_ctx=" in src and "think=" in src
    print(f"  [{'ok' if prints else 'BAD'}]    the run prints the configuration it used")
    if not prints:
        fails.append("exam.py does not print num_ctx/think; a score without its conditions "
                     "is not comparable to any other score")

    # NEGATIVE CONTROL: the AST check must actually catch a hardcoded default.
    probe = ast.parse('ap.add_argument("--ctx", type=int, default=32768)')
    found = any(kw.arg == "default" and kw.value.value == 32768
                for n in ast.walk(probe) if isinstance(n, ast.Call)
                for kw in n.keywords if isinstance(kw.value, ast.Constant))
    print(f"  [{'ok' if found else 'BAD'}]    control: a hardcoded --ctx default is detected")
    if not found:
        fails.append("the detector does not detect the exact line this test was written for")

    for x in fails:
        print("FAIL:", x)
    print("test_exam_configuration:", "FAILED" if fails else "ok")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
