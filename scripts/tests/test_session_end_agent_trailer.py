#!/usr/bin/env python3
"""GATE: session_end.py accepts the same Agent trailers the commit-msg hook accepts (t-3a, 2026-10-23).

The handover's item 5: "session_end.py rejects a ':' in the Agent trailer (role_gate accepts it)".
The two consumers of the trailer disagree.

  scripts/hooks/commit-msg (line 91) accepts a colon in the MODEL half, deliberately, with the
    reason recorded in its own comment: ollama tags are `name:tag`, and
    `qwen3.8:27b-mtp-q4_K_M` is the real identifier of the model that wrote the commit --
    without the colon the charset could not express a valid value, so coder.py's review-branch
    commits were refused for every local run.
  scripts/role_gate.py resolves by ROLES.json claim (longest wins): dsh claims 'dsh-harness'
    and 'dsh'; a model role claims 'ollama/<model>'. Model names carry colons (ollama tags),
    so role_gate accepts 'dsh-harness/swift:27b-mtp-q4_K_M' -- verified with
    `python3 scripts/role_gate.py who "dsh-harness/swift:27b-mtp-q4_K_M"` -> dsh.
  scripts/session_end.py's own check accepted no colon, so the trailer
    `Agent: dsh-harness/qwen3.8:27b-mtp-q4_K_M` (the trailer the DSH standing prompt MANDATES
    for the ThinkPad instance) passed commit-msg and role_gate and died in session_end --
    the one tool of the three that refused to record who wrote the commit.

The gate: one accepted-set, stated in session_end, and it must agree with the hook's regex.
Nothing runs: this checks the accept/reject decision only."""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import session_end

P = F = 0


def check(label, got, want):
    global P, F
    ok = got == want
    P += ok
    F += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + ("" if ok else f": got {got!r}, want {want!r}"))


ACCEPT = [
    "claude-code/opus-5",              # the shape in AGENTS.md
    "codex/gpt-5",
    "gemini-cli/gemini-3-pro",
    "ollama/qwen3-coder-30b",
    "human/jas",
    "dsh-harness/swift",               # the GX10 DSH trailer (standing prompt)
    "dsh-harness/swift:27b-mtp-q4_K_M",   # model tag carries a colon
    "ollama/qwen3.8:27b-mtp-q4_K_M",   # the ThinkPad DSH trailer from AGENTS.md
    "dsh-harness/qwen3.8:27b-mtp-q4_K_M",
]
REJECT = [
    "",                                # nothing
    "opus-5",                          # no tool half
    "claude-code/",                    # empty model half
    "/opus-5",                         # empty tool half
    "a/b/c",                           # not <tool>/<model>
    "a/b c",                           # a space is not a trailer
    "Agent: a/b",                      # the prefix belongs to the hook, not the value
    "a/b\nX: 1",                       # no embedded newline smuggling
]

for v in ACCEPT:
    check(f"accepts {v!r}", session_end.agent_trailer_ok(v), True)
for v in REJECT:
    check(f"rejects {v!r}", session_end.agent_trailer_ok(v), False)

# THE AGREEMENT: every value session_end accepts must pass the hook's trailer regex, read
# from the hook that enforces it. The hook matches the WHOLE trailer line, so the comparison
# runs on the line form. The hook's pattern is POSIX ERE; '[[:space:]]' becomes '\s' so
# Python's re means the same thing.
hook = (ROOT / "scripts" / "hooks" / "commit-msg").read_text(encoding="utf-8")
m = re.search(r"grep -qE '(\^Agent: .+?)'", hook)
check("the hook's Agent regex was found to compare against", m is not None, True)
if m:
    pattern = re.compile(m.group(1).replace("[[:space:]]", r"\s"))
    both = [
        "claude-code/opus-5",
        "dsh-harness/swift",
        "dsh-harness/swift:27b-mtp-q4_K_M",
        "ollama/qwen3.8:27b-mtp-q4_K_M",
        "ollama/qwen3.8:27b-mtp-q4_K_M-56k",
        "a/b:c",
        # NOTE: "a/b/c/d" style values are deliberately NOT in this list. The hook's regex
        # (grep -qE, no shape anchors on the model half) accepts slashes beyond the first;
        # session_end's fullmatch is deliberately stricter (one slash). The agreement asserted
        # here is the one that bites: every value session_end ACCEPTS must pass the hook.
        "a/b c",
    ]
    for v in both:
        check(f"session_end agrees with commit-msg on {v!r}",
              session_end.agent_trailer_ok(v),
              bool(pattern.fullmatch("Agent: " + v)))

print(f"\n{P} passed, {F} failed")
sys.exit(1 if F else 0)
