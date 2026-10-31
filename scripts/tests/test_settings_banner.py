#!/usr/bin/env python3
"""Fail-first oracle for the derived Models-tab banner (2026-10-25).

Jas: "Please update that header so it gets its information from a source of truth and updates
dynamically." The card above a role's task list must say what the CODE does, not what somebody typed once
-- and it must be able to say "Not wired" when a refactor stops reading a setting.
"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "projects" / "app-repo"))

import task_routing as tr  # noqa: E402
import roles_pages as rp  # noqa: E402

PASSED = FAILED = 0


def check(name, got, want):
    global PASSED, FAILED
    ok = got == want
    PASSED += ok
    FAILED += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"  (got {got!r}, want {want!r})"))


def main():
    rows = {r["setting"]: r for r in tr.wired()}
    check("every setting on the page is declared", sorted(rows), ["interrupt", "max_lanes", "series"])
    check("each declaration is checked against its consumer", all(r["where"] for r in rows.values()), True)
    check("the three settings are wired in this tree", [r["setting"] for r in tr.wired() if r["live"]],
          ["interrupt", "max_lanes", "series"])

    text = rp.step4_banner()
    for s in ("interrupt", "series", "max_lanes"):
        check(f"the banner names {s}", s in text, True)
    check("the banner says they reach the dispatcher", "reach the dispatcher" in text, True)
    check("the banner is a card", text.startswith('<div class="card small">'), True)

    # THE DYNAMIC HALF: break one consumer and the banner must say so, by name.
    saved = dict(tr.WIRING)
    try:
        tr.WIRING["series"] = ("dispatch_loop.py", "a_call_that_does_not_exist(", "some claim")
        broken = rp.step4_banner()
        check("a broken consumer turns the banner to 'Not wired'", "Not wired right now" in broken, True)
        check("  and names the setting that stopped", "series" in broken, True)
        check("  and the text really changed", broken != text, True)
    finally:
        tr.WIRING.clear()
        tr.WIRING.update(saved)

    # and an unreadable declaration set is not a silent pass
    saved = dict(tr.WIRING)
    try:
        tr.WIRING.clear()
        empty = rp.step4_banner()
        check("no declarations means no claim, not a pass", "no claim" in empty.lower(), True)
    finally:
        tr.WIRING.clear()
        tr.WIRING.update(saved)

    print(f"{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
