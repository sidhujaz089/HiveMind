#!/usr/bin/env python3
"""manager_prompt.py: the manager's prompt carries the chunk-size cap that
keeps a single chunk inside the builder's output ceiling.

The cap: at most 6 edit sites named in one file per chunk. If a fix needs
more than 6 sites, it must be split into multiple chunks, each naming at most
6 sites in that file. Origin: t450 — 11 scattered edits in one chunk
overflowed the 16k output cap twice. Defect: t646 (qwen3.8) named 11 edit
sites in one chunk; scope_lint rejected it (brief-scatter); the manager
prompt never told the manager the cap, so the content is the class's
upstream prevention.

Run: python3 scripts/tests/test_manager_prompt.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PROMPT = ROOT / "scripts" / "manager_prompt.py"


def _prompt_text():
    return PROMPT.read_text(encoding="utf-8")


def test_prompt_names_the_six_site_cap():
    text = _prompt_text()
    # the cap must be stated with its number, so a builder can be told the bound
    assert "6" in text, "the prompt does not state the 6 edit-site cap"
    # and it must be about edit sites / files, not some other 6
    low = text.lower()
    assert ("edit site" in low) or ("edit-site" in low) or ("sites per" in low) \
        or ("sites in one file" in low) or ("sites in a file" in low), \
        "the prompt names a number but not as an edit-site cap"


def test_prompt_directs_splitting_over_the_cap():
    low = _prompt_text().lower()
    assert "split" in low, "the prompt does not direct the manager to split oversized chunks"
    # the split must be into chunks, the unit the gate counts
    assert "chunk" in low, "the split instruction does not name chunks"


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"  ok  {fn.__name__}")
    print(f"{len(fns)} passed -- the manager prompt carries the <=6 edit-sites cap and the split rule")
