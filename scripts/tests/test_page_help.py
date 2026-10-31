#!/usr/bin/env python3
"""Every Hub page must explain itself, and the explanation must point at real enforcement.

Jas, 2026-10-24: *"put a question mark on all the pages ... so any model can go anywhere in the
system and understand what is supposed to happen and proactively fix anything not in alignment
with the plan ... make something that makes it enforced so deviations cant occur."*

That ask has a failure mode without a check: the question marks get written once, a page is
added next week, and a model landing on it finds nothing. So this asserts the registry, not the
prose:

  - every tab the nav can show has an entry, and every entry is a real page;
  - every entry names the FILE it is a view of, because that is where a repair goes;
  - every entry names at least one ENFORCER, and each named enforcer exists on disk -- an entry
    claiming enforcement it does not have is worse than no entry, because it reads as covered;
  - the page actually renders the link and the /help route exists.

It does not test that the prose is good. That is what review is for.
"""
import inspect
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "projects" / "app-repo"))

import appnav  # noqa: E402
import hub_common as page_help  # noqa: E402  -- PAGES lives beside the other shared page furniture

PASSED = FAILED = 0
SUB_PAGES = ("review/runs",)


def check(name, got, want):
    global PASSED, FAILED
    ok = got == want
    PASSED += ok
    FAILED += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"  (got {got!r}, want {want!r})"))


def exists_as_enforcer(name):
    """Three shapes, because an enforcer is a suite, a checker or a library module:
    scripts/tests/<name>.py, scripts/tests/test_<name>.py, scripts/<name>.py.
    The property is that the thing EXISTS -- a name in an entry that resolves to nothing is the
    one case this must never accept."""
    return ((REPO / "scripts" / "tests" / f"{name}.py").is_file()
            or (REPO / "scripts" / "tests" / f"test_{name}.py").is_file()
            or (REPO / "scripts" / f"{name}.py").is_file())


def main():
    nav = [k for k, _href, _label in appnav.SECTIONS]
    pages = page_help.PAGES

    # 1. The page set and the tab set are the same set, plus the declared sub-pages.
    missing = [k for k in nav if k not in pages]
    check("every tab the nav shows has a help entry", missing, [])
    extra = sorted(set(pages) - set(nav) - set(SUB_PAGES))
    check("every help entry is a page that exists", extra, [])

    # 2. What each entry must carry.
    for key, e in sorted(pages.items()):
        check(f"{key}: names the file(s) it is a view of", bool(e.get("source")), True)
        check(f"{key}: says what to do when the page is wrong", bool((e.get("wrong") or "").strip()), True)
        enf = e.get("enforce") or []
        check(f"{key}: names at least one enforcer", bool(enf), True)
        unknown = [n for n in enf if not exists_as_enforcer(n)]
        check(f"{key}: every enforcer it names exists on disk", unknown, [])

    # 3. The page really renders it. Source-level, because importing feedback.py starts a server.
    src = (REPO / "projects" / "app-repo" / "feedback.py").read_text(encoding="utf-8")
    check("the page shell renders a help link", "_help_link(active)" in src, True)
    check("  and there is a route for it", '"/help"' in src, True)
    check("  and the route renders from the registry", "help_page(" in src, True)

    # 4. The registry is importable by any script that wants to read a page's contract -- that is
    #    what "any model can go anywhere and understand" needs to mean in practice.
    check("the registry is a plain module any script can import", hasattr(page_help, "entry"), True)
    check("  and it can be listed", sorted(page_help.PAGES) == sorted(pages), True)

    print(f"{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
