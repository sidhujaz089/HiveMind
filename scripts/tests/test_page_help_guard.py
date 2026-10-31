#!/usr/bin/env python3
"""A Hub page and its "how this page works" text change in the same commit (C3).

Every page carries a `?` that opens `hub_common.PAGES[key]`: *what it shows*, *which files it is a
view of*, *what keeps it honest*, and *what to do when it disagrees with the disk*. The text is data,
which is right -- and **nothing verified it**. On 2026-10-26 `grep page_help scripts/*.py` returned
nothing at all, so a page could change and its explanation rot in silence. Worse, `feedback.py`'s own
docstring still named the text's home as `projects/app-repo/page_help.py`, a file that has not existed
for some time: the explanation of the explanations was itself stale, and no check reads code docstrings
the way `check_doc_references` reads `.md`.

The rule this enforces, and it is deliberately coarse:

  a commit that changes a Hub PAGE MODULE must also change `projects/app-repo/hub_common.py`.

Coarse because the pages do not declare which module renders them, and inventing that mapping here
would be a second truth. The escape hatch is the point: if the page's explanation genuinely did not
change, touch the entry and say so -- a dated `_reviewed` note is enough, and the commit body carries
the reason. That is the same bargain as the Flow chart's changelog: the gate forces a decision, it does
not pretend to judge one.

What it must not do: fire on a change that is not a page (a check, a doc, a data file), or fire when
the help was changed *without* the page, which is legitimate -- correcting a description nobody has
broken yet is how the text gets better.

Hermetic: the predicate takes a list of paths; no repository, no git.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import check_workspace as cw  # noqa: E402

FAILS = []


def check(what, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + what + ("" if cond else "  -- " + str(detail)))
    if not cond:
        FAILS.append(what)


def main():
    p = cw.page_help_problems

    # 1. THE FINDING: a page changes, its explanation does not.
    for page in ("projects/app-repo/pages_lanes.py", "projects/app-repo/feedback.py",
                 "projects/app-repo/flow_pages.py", "projects/app-repo/roles_pages.py",
                 "projects/app-repo/device_tiles.py", "projects/app-repo/pages_plan.py"):
        out = p([page])
        check(f"changing {Path(page).name} without the help is a problem", len(out) == 1, out)
        check("  and the message names the page file", Path(page).name in out[0], out)

    # 2. THE REPAIR PASSES: page + help in one commit.
    out = p(["projects/app-repo/pages_lanes.py", "projects/app-repo/hub_common.py"])
    check("a page changed WITH the help file is not a problem", out == [], out)

    # 3. NOT A PAGE: nothing else in the repository may trip this.
    for other in ("scripts/check_workspace.py", "docs/ACCESS.md", "workspace/network.json",
                  "projects/app-repo/taskstore.py", "scripts/tests/test_x.py"):
        check(f"{other} is not a page and does not trip the guard", p([other]) == [], p([other]))

    # 4. THE HELP ALONE IS FINE -- correcting a stale description is an improvement, not a violation.
    check("changing only the help file is allowed", p(["projects/app-repo/hub_common.py"]) == [])

    # 5. EMPTY IS CLEAN, not a crash.
    check("an empty change list is clean", p([]) == [])

    # 6. THE STALE DOCSTRING IS PART OF THIS CYCLE: feedback.py pointed at page_help.py, which does not
    #    exist. If someone reintroduces a docstring naming a file that is not there, this fails.
    fb = (ROOT / "projects" / "app-repo" / "feedback.py").read_text(encoding="utf-8")
    check("feedback.py no longer names a page_help.py that does not exist",
          "page_help.py" not in fb, [l for l in fb.splitlines() if "page_help.py" in l][:2])
    check("and it does name the real home", "hub_common" in fb)

    print("\n  %d failure(s)" % len(FAILS))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
