#!/usr/bin/env python3
"""The Hub's Chat (Jas, 2026-09-29: "keep the chat. Make it all in the theme of the Hub"): chatui's
page, scoped and recoloured. Pins that nothing leaks into the Hub and nothing dark survives."""
import re
import sys
from pathlib import Path

sys.path[:0] = [str(Path(__file__).resolve().parents[2] / "projects" / "app-repo")]
import chat_page as cp  # noqa: E402

PASSED = FAILED = 0


def report(name, got, want):
    global PASSED, FAILED
    ok = got == want
    PASSED += ok
    FAILED += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"  (got {got!r}, want {want!r})"))


body = cp.chat_body()
css = body[body.index("<style>") + 7:body.index("</style>")]
sels = [s.strip() for blk in css.split("}") if "{" in blk for s in blk.split("{")[0].split(",")
        if s.strip() and not re.fullmatch(r"(from|to|\d+%|@keyframes.*)", s.strip())]
report("every chat CSS rule is scoped under .chatpage (cannot restyle the Hub)",
       [s for s in sels if not s.startswith(".chatpage")], [])
report("no rule targets the Hub's own .wrap", any(re.search(r"\.wrap\b", s) for s in sels), False)
report("no dark-theme colour survives", [c for c in cp.COLOURS if c.lower() in body.lower()], [])
report("every API call goes through the Hub", re.findall(r"fetch\(\s*[`'\"]/api/", body), [])
report("  and calls /models/api/...", bool(re.search(r"fetch\(\s*[`'\"]/models/api/", body)), True)
report("the elements the chat script needs exist", all(f'id="{i}"' in body for i in ("mem", "side", "log", "q", "tab-runs", "livedot")), True)
print(f"\n{PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
