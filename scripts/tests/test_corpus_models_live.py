#!/usr/bin/env python3
"""Corpus > Models is live (plan step 5): an Ollama model's status and group come from the role
store and every server's /api/ps, not from the written notes (Q4's said "removed from lanes"
while it held three roles). Fakes: the role store and the Ollama facts."""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "projects" / "app-repo"), str(ROOT / "scripts")]
import feedback  # noqa: E402
import role_store  # noqa: E402

PASSED = FAILED = 0


def report(name, got, want):
    global PASSED, FAILED
    ok = got == want
    PASSED += ok
    FAILED += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"  (got {got!r}, want {want!r})"))


def chips(h):
    return {m.group(1): m.group(2) for m in re.finditer(
        r'<code>([^<]+)</code>(?:\s*<span class="small">[^<]*</span>)?</h3><span class="corpus-chip \w+">([^<]+)<', h)}


def section_of(h, tag):
    heads = [(m.start(), m.group(1).strip()) for m in re.finditer(r"<h2>([^<]+)<span", h)]
    at = h.index(f"<code>{tag}</code>")
    return [name for pos, name in heads if pos < at][-1]


def main():
    real = (feedback._ollama_facts, role_store.load)
    try:
        facts = {"qwen3.8:27b-mtp-q4_K_M": {"resident": ["11434", "11435"]},
                 "qwen3.8:27b-mtp-bf16": {"resident": []},
                 "brand-new:7b": {"resident": []}, "_ps_errors": []}
        feedback._ollama_facts = lambda: facts
        role_store.load = lambda path=None: {"roles": {
            "manager": {"name": "Manager", "model": "qwen3.8:27b-mtp-q4_K_M"},
            "senior": {"name": "Senior", "model": "qwen3.8:27b-mtp-q4_K_M"},
            "claude": {"name": "Claude", "kind": "person"}}}
        h = feedback._corpus_models()
        c = chips(h)
        report("a model roles use says which roles, from the store", c.get("qwen3.8:27b-mtp-q4_K_M"),
               "in use: Manager, Senior")
        report("  and sits under 'In the pipeline now'", section_of(h, "qwen3.8:27b-mtp-q4_K_M"), "In the pipeline now")
        report("  and 'in memory' names every server it is on", "yes, on :11434, :11435" in h, True)
        report("a model no role uses is 'on disk, no role' -- whatever the notes once said",
               c.get("qwen3.8:27b-mtp-bf16"), "on disk, no role")
        report("  and is NOT under 'In the pipeline now'",
               section_of(h, "qwen3.8:27b-mtp-bf16") != "In the pipeline now", True)
        report("a model on the box with no written notes still appears", c.get("brand-new:7b"), "on disk, no role")
        report("a noted Ollama model that is gone from the box says so", c.get("gemma4:26b"), "not on this box")
    finally:
        feedback._ollama_facts, role_store.load = real
    print(f"\n{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
