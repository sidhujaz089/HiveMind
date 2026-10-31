"""Shared checker: a file must not list processes through `ps` with a command column.

WHY. Without a terminal, ps cuts every line at 80 columns. inference_health counted zero
running dispatches with two lanes live (2026-09-15) because "--brief" sat past the cut, and
reported a busy box as down. /proc/<pid>/cmdline has no width. A ps call with only numeric
columns (pid=,etimes=) is fine; one with cmd, args or comm is the defect.
"""
import re
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
PS = re.compile(r"""["']ps["']\s*,\s*["']-eo["']\s*,\s*["'][^"']*\b(cmd|args|comm)\b|ps\s+-eo\s+\S*\b(cmd|args|comm)\b""")


def run(rel):
    code = "\n".join(l for l in (ROOT / rel).read_text().splitlines() if not l.strip().startswith("#"))
    bad = [m.group(0) for m in PS.finditer(code)]
    uses_proc = "/proc" in code or "lane_status" in code
    for b in bad:
        print(f"  [BAD]  {rel} still lists processes via `{b}` -- truncated at 80 columns")
    if not uses_proc:
        print(f"  [BAD]  {rel} does not read /proc (or lane_status)")
    ok = not bad and uses_proc
    if ok:
        print(f"  [ok]   {rel} reads process command lines from /proc")
    print(f"\n{1 if ok else 0} passed, {0 if ok else len(bad) + (not uses_proc)} failed")
    return 0 if ok else 1
