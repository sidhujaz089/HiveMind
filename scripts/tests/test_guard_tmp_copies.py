#!/usr/bin/env python3
"""guard_tmp_copies refuses a repository copy into /tmp (RAM on the GX10) and nothing else (2026-10-17).

The second case list is the false positive it shipped with: a commit whose message mentioned rsync and
whose message file sat in the session scratch dir was refused, because the pattern matched across the
whole command line instead of one command."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "hooks"))
import guard_tmp_copies as g  # noqa: E402

T = "/" + "tmp/"          # assembled, so this file carries no literal temp path for the library checks
REFUSED = [
    "git worktree add " + T + "t1334wt HEAD",
    "cd x && git -C ~/ai-workspace worktree add --detach " + T + "x 714f",
    "cp -a ~/ai-workspace " + T + "full",
    "git clone . " + T + "repro",
    "rsync -a ./ " + T + "copy",
]
REFUSED += [
    "cp file.txt " + T,                          # L1's finding: a single file walked past both rules
    "mv file.txt " + T + "x",
    # NO WRITING TO /tmp AT ALL, a standing decision (Jas, 2026-10-27). This case used to be ALLOWED,
    # on "small files in /tmp are fine"; the session that fetched one page to /tmp/hub.html is why it
    # moved. Small is a property of the first file, not of the habit.
    "echo hi > " + T + "x.txt",
]
ALLOWED = [
    "cp a ./build" + T + "x",                    # /tmp/ inside a path is not a destination (L1)
    "cp " + T + "a ./b",                         # copy OUT of /tmp is not a write to /tmp (L1)
    "mv " + T + "a ./b",
    "printf 'rsync into " + T + "' > m && git commit -F " + T + "claude-1000/m",
    "git worktree add ~/.cache/hivemind-tmp/x HEAD",
    "ls " + T,                                   # READING /tmp is not a write
    "curl -s -o ~/.cache/hivemind-tmp/hub.html http://x",
    "HIVEMIND_ALLOW_TMP=1 curl -o " + T + "x http://x",
]
failed = 0
for c in REFUSED:
    ok = bool(g.reason(c))
    failed += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  refused: {c}")
for c in ALLOWED:
    ok = not g.reason(c)
    failed += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  allowed: {c}")
print("FAILED" if failed else "ok")
sys.exit(1 if failed else 0)
