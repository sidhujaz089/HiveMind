#!/usr/bin/env python3
"""Assemble Chat captures into something a human reads. See t121.

STUB. `assemble()` is unimplemented on purpose -- scripts/tests/test_chat_transcripts.py
is its oracle and currently fails.

This file exists behaviour-free because coder.py apply_ops REFUSES a path that does not
already exist: it edits, it does not create. A task phrased as "write a new module" is
therefore undispatchable and the run refuses with `no such file` after burning its
attempts. Seeding the file is the cheap half; letting ops create files is the real fix and
is not this task.
"""


def assemble(records):
    """{slug: rendered text} for capture records. The suite carries the contract."""
    if not records:
        return {}
    groups = {}
    for r in records:
        slug = r.get("session", "unknown")
        groups.setdefault(slug, []).append(r)
    out = {}
    for slug, turns in groups.items():
        lines = []
        for t in turns:
            role = t.get("role", "?")
            text = t.get("text", "")
            lines.append(f"{role}: {text}")
        out[slug] = "\n".join(lines)
    return out
