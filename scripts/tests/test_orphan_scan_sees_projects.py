#!/usr/bin/env python3
"""t3578: the orphan scan has to look where the callers are.

check_workspace reported scripts/app_timeline.py under "No check script is orphaned from the
protocol" while projects/app-repo/feedback.py:3580 imported it and rendered it on the Hub's page.
The scan read scripts/*.py, docs/*.md and AGENTS.md and never projects/, so a Hub-only caller was
invisible and working code was named as coverage the repository does not have. Measured on main
2026-10-25: 22 scripts reported; widening the scan to projects/**/*.py cleared 6 of them (the
app-timeline helper among them) and added none.

Three cases, and the second two are guards:
  1. named only under projects/  -> NOT an orphan (this is the defect)
  2. named nowhere at all        -> IS an orphan (or else "no orphans" is bought by reporting
                                    nothing, which is the vacuous pass of docs/CHECKS.md)
  3. named only inside a virtualenv file -> IS an orphan (site-packages holds tens of thousands of
                                    files, so counting it as a caller would make every script look
                                    used, which is the same bug pointing the other way)

Hermetic: the repo is a TemporaryDirectory with four one-line files.
"""
import sys
import tempfile
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
    with tempfile.TemporaryDirectory() as d:
        r = Path(d)
        (r / "scripts").mkdir()
        (r / "docs").mkdir()
        (r / "projects" / "app-repo").mkdir(parents=True)
        (r / ".venv" / "lib").mkdir(parents=True)
        (r / "AGENTS.md").write_text("# contract\n")
        (r / "docs" / "x.md").write_text("# a doc\n")
        (r / "scripts" / "used_by_hub.py").write_text("def timeline_html():\n    return ''\n")
        (r / "scripts" / "used_by_nothing.py").write_text("def nobody_calls_me():\n    return 1\n")
        (r / "scripts" / "used_only_in_venv.py").write_text("def x():\n    return 2\n")
        (r / "projects" / "app-repo" / "feedback.py").write_text(
            "import used_by_hub\nused_by_hub.timeline_html()\n")
        (r / ".venv" / "lib" / "site.py").write_text("used_only_in_venv\n")

        orphans = cw.orphan_scripts(r)
        check("a script whose only caller is the Hub is not an orphan",
              "used_by_hub.py" not in orphans, orphans)
        check("a script nothing names is still an orphan", "used_by_nothing.py" in orphans, orphans)
        check("a name that appears only inside a virtualenv is not a caller",
              "used_only_in_venv.py" in orphans, orphans)
        check("the scan returns a plain list of file names",
              isinstance(orphans, list) and all(isinstance(x, str) for x in orphans), orphans)


        # 4. THE RUNNERS COUNT TOO (t4641): a script named by a shell runner or a systemd unit is run.
        (r / "scripts" / "driven_by_shell.py").write_text("def x():\n    return 3\n")
        (r / "scripts" / "driven_by_unit.py").write_text("def y():\n    return 4\n")
        (r / "scripts" / "loop.sh").write_text("#!/bin/sh\npython3 scripts/driven_by_shell.py\n")
        (r / "deploy").mkdir()
        (r / "deploy" / "x.service").write_text("[Service]\nExecStart=/usr/bin/python3 "
                                                "/repo/scripts/driven_by_unit.py run\n")
        orphans = cw.orphan_scripts(r)
        check("a script a shell runner calls is not an orphan",
              "driven_by_shell.py" not in orphans, orphans)
        check("a script a systemd unit runs is not an orphan",
              "driven_by_unit.py" not in orphans, orphans)
        check("  and one nothing names is still an orphan",
              "used_by_nothing.py" in orphans, orphans)

        # 5. A GIT HOOK IS A CALLER TOO (t4641, 2026-10-26). scripts/hooks/pre-commit:243 has run
        #    check_reassignment_notes.py since dab4be754 (2026-10-18) and this check named it an
        #    orphan every day since, because scripts/hooks/ was not among the sources -- the same
        #    defect as projects/ (t3578) and autonomy_loop.sh, one directory further in.
        #    A hook runs at commit time with no timer: that is what "on the protocol" means.
        (r / "scripts" / "hooks").mkdir()
        (r / "scripts" / "driven_by_hook.py").write_text("def z():\n    return 5\n")
        (r / "scripts" / "hooks" / "pre-commit").write_text(
            '#!/bin/sh\n'
            'if git diff --cached --name-only | grep -qE \'^workspace/tasks/\'; then\n'
            '    out=$(timeout 60 python3 "$(dirname "$0")/../driven_by_hook.py" 2>&1)\n'
            'fi\n')
        # THE GUARD, or "a name in a hook" becomes the docs-line lie in a new place: a comment is how
        # a hook explains itself, and explaining a script is not running it.
        (r / "scripts" / "named_only_in_hook_comment.py").write_text("def w():\n    return 6\n")
        (r / "scripts" / "hooks" / "post-commit").write_text(
            '#!/bin/sh\n'
            '# named_only_in_hook_comment.py was retired in t4600 and nothing runs it any more\n'
            'exit 0\n')
        orphans = cw.orphan_scripts(r)
        check("a script a git hook runs is not an orphan",
              "driven_by_hook.py" not in orphans, orphans)
        check("a script named only in a hook's COMMENT is still an orphan",
              "named_only_in_hook_comment.py" in orphans, orphans)
    print("\n  %d failure(s)" % len(FAILS))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
