#!/usr/bin/env python3
"""ONE SOURCE OF TRUTH, POINTERS EVERYWHERE (Jas, 2026-10-13): text that names who does a task asks the
role store (task_routing.role_name / render with {role:<task>}), it never writes a role or model in.

"Q16" was the bf16 model that held the Manager and Senior roles until 2026-09-23. Three weeks later the
scheduler still said "HELD FOR CLAUDE: Q16 objected" and a prompt still told a model "You are Q16",
because the name had been written in instead of pointed at. This suite fails if a runtime string (not a
docstring, not a comment) in the code names Q16 outside the files listed below, each with its reason:
a place where "Q16" names the MODEL (the bf16 weights), which is a model fact, not a role.

Also checks the pointer itself: role_name reads the store, says "no role has <task>" when none does
(no fallback), and render() resolves tokens.

Run: python3 scripts/tests/test_roles_are_pointers.py
"""
import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import task_routing  # noqa: E402

# file -> why "Q16" may appear in a runtime string there. Shrink this list; never grow it for a role.
ALLOWED = {
    "scripts/verify_control.py": "JUDGES maps the nickname to the bf16 model tag -- names the model",
    "projects/local-inference/chatui.py": "the model picker's label for the bf16 model -- names the model",
    "projects/local-inference/fleet.py": "the fleet table's history of which models Swift replaced",
    "projects/app-repo/feedback.py": "the Corpus page shows a recorded run by the bf16 model (field q16_run)",
    "scripts/session_candidates.py": "a regex that recognises old prompts in past transcripts",
    "scripts/gate_callers.py": "quotes an audit finding by the bf16 model, as history",
    "projects/local-inference/harness_run.py": "the bf16 settling phase of the old harness benchmark",
    "scripts/gemma-decide.py": "the retired Gemma-era loop (autonomy_loop.sh history)",
    "scripts/ask-gemma-supervise.py": "the retired Gemma-era loop (autonomy_loop.sh history)",
    "scripts/gemma-execute.py": "the retired Gemma-era loop (autonomy_loop.sh history)",
}
Q16 = re.compile(r"\bQ16\b")
FAILED = 0


def report(label, got, want):
    global FAILED
    ok = got == want
    FAILED += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + ("" if ok else f"\n        got {got!r}\n        want {want!r}"))


def runtime_strings(path):
    """(line, text) of every string constant that is not a docstring."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError, OSError):
        return []
    docs = set()
    for n in ast.walk(tree):
        body = getattr(n, "body", None)
        if isinstance(body, list) and body and isinstance(body[0], ast.Expr) \
                and isinstance(getattr(body[0], "value", None), ast.Constant):
            docs.add(id(body[0].value))
    return [(n.lineno, n.value) for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docs]


def main():
    files = sorted(list((ROOT / "scripts").glob("*.py")) + list((ROOT / "projects" / "app-repo").glob("*.py"))
                   + list((ROOT / "projects" / "local-inference").glob("*.py")))
    offenders = []
    for p in files:
        rel = str(p.relative_to(ROOT))
        if rel in ALLOWED:
            continue
        offenders += [f"{rel}:{ln}  {t.strip()[:80]!r}" for ln, t in runtime_strings(p) if Q16.search(t)]
    report("no runtime string names Q16 as a role (name it from the store: task_routing.role_name)",
           offenders, [])
    report("every allowance still applies (a file that no longer mentions Q16 comes off the list)",
           [f for f in ALLOWED if not any(Q16.search(t) for _, t in runtime_strings(ROOT / f))], [])

    store = {"roles": {"m": {"kind": "model", "name": "Manager", "tasks": [{"task": "preflight"}]},
                       "a": {"kind": "model", "name": "Auditor", "tasks": [{"task": "rca"}]}}}
    report("role_name reads the store", task_routing.role_name("preflight", store), "Manager")
    report("a task no role holds says so -- no fallback name",
           task_routing.role_name("session_embed", store), "no role has session_embed")
    report("render resolves every token", task_routing.render("{role:preflight} and {role:rca}", store),
           "Manager and Auditor")
    report("text without tokens is returned unchanged", task_routing.render("plain", store), "plain")
    print(f"\n{'FAILED' if FAILED else 'ok'}: {FAILED} failure(s)")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
