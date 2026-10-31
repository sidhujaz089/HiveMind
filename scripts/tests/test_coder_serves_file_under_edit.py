#!/usr/bin/env python3
'''t1007 oracle: the slice-fit guard must never refuse to serve the file the brief
names as the one to edit (self-referential deadlock).

p345-t1004-coder (2026-10-09, swift, brief-72ef138b): the model was asked to EDIT
projects/local-inference/coder.py and was refused that file on attempts 2,3,4,5,6,7
with "no room for more of it beside the prompt. Work from what you already have"
(workspace/run-logs/dispatch/p345-t1004-coder--production--swift--1791585904.log).
Seven attempts, zero progress, nothing written. The guard that refuses the file is
the code the brief asks the model to change -- so the harness cannot deliver the fix
for the guard. A file the model is being asked to edit is not optional context:
refusing it is not a size decision, it is a deadlock.

The predicate this suite pins, is_edit_target(rel, files), answers one question: does
`rel` name a file the brief is asking the model to edit? The slice branch and the
new-file branch must both exempt those files from their size refusal.

Hermetic: reads the real coder.py text from the repository, calls the module's own
function, no model, no server, no subprocess, no writes, no fixed /tmp.
'''
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "projects" / "local-inference"))
import coder  # noqa: E402

FAILS = []

# The two real paths named in the failed dispatch -- distinct, so a stub that
# answers one string answers nothing (oracle-weak: one repeated placeholder value).
TARGET = "projects/local-inference/coder.py"
OTHER = "scripts/tests/test_coder_slice_room.py"
REFUSAL = "no room for more of it beside the prompt"
REFUSAL_NEW = "too large to show beside the prompt"


def ck(name, cond, detail=""):
    print(f"  [{'ok' if cond else 'BAD'}]  {name}" + ("" if cond else f"  {detail}"))
    if not cond:
        FAILS.append(name)


def test_the_harness_can_name_the_file_under_edit():
    """The predicate must exist and answer the question the deadlock turns on."""
    if not hasattr(coder, "is_edit_target"):
        ck("is_edit_target exists in coder.py", False,
           "coder.py defines no is_edit_target -- the guard has no way to know the file "
           "under edit, which is the defect (p345-t1004-coder)")
        return
    ck("is_edit_target exists", True)
    ck("a file named on the brief's edit list IS the edit target",
       coder.is_edit_target(TARGET, [TARGET]) is True)
    ck("a file NOT on the edit list is not the edit target (the guard is not disabled)",
       coder.is_edit_target(OTHER, [TARGET]) is False)
    ck("the same file spelled with a leading ./ is still the edit target",
       coder.is_edit_target("./" + TARGET, [TARGET]) is True)
    ck("the same file spelled as an absolute path is the edit target",
       coder.is_edit_target(str(ROOT / TARGET), [TARGET]) is True)
    ck("a path that merely starts with the target's name is NOT the edit target",
       coder.is_edit_target(TARGET + ".bak", [TARGET]) is False)
    ck("a path that merely contains the target's name is NOT the edit target",
       coder.is_edit_target("projects/" + TARGET, [TARGET]) is False)
    ck("an empty file list has no edit target",
       coder.is_edit_target(TARGET, []) is False)
    ck("an empty rel is never the edit target",
       coder.is_edit_target("", [TARGET]) is False)
    ck("a file list containing non-strings is not a crash",
       coder.is_edit_target(TARGET, [None, 7, ""]) is False)
    ck("an empty list of edit files with a real rel is False",
       coder.is_edit_target(TARGET, None) is False)


def test_the_slice_branch_that_refused_asks_the_predicate_first():
    """A predicate nobody calls satisfies nothing (failure-schema:
    check-satisfied-by-dead-path). The refusal lives inside coder.py's attempt loop,
    so the wiring is checked where the refusal text is -- the house pattern for a
    branch that is not a callable (see scripts/tests/test_coder_slice_room.py)."""
    src = (ROOT / "projects" / "local-inference" / "coder.py").read_text(encoding="utf-8")
    ck("the refusal keeps its loud wording (t437: a reason in the log, not a silent skip)",
       REFUSAL in src)
    ck("the new-file branch's refusal text is kept too", REFUSAL_NEW in src)
    if REFUSAL not in src:
        return
    i = src.index(REFUSAL)
    ck("the slice-fit guard consults is_edit_target before it refuses",
       "is_edit_target(" in src[max(0, i - 1500):i],
       "no is_edit_target( within 1500 characters before the refusal -- the guard "
       "still refuses the file under edit")
    j = src.index(REFUSAL_NEW)
    ck("the new-file branch consults it too (a brief that SYMBOLS-out the file enters "
       "through that door, not the slice branch)",
       "is_edit_target(" in src[max(0, j - 1500):j],
       "no is_edit_target( within 1500 characters before the new-file refusal")
    ck("the predicate is wired into both refusal sites, not a helper nobody calls",
       src.count("is_edit_target(") >= 3, f"{src.count('is_edit_target(')} call site(s)")


if __name__ == "__main__":
    print("t1007: the slice-fit guard must serve the file under edit")
    for _name, _fn in sorted(list(globals().items())):
        if _name.startswith("test_") and callable(_fn):
            _fn()
    print(f"\n{len(FAILS)} failure(s)"
          + (": " + ", ".join(FAILS) if FAILS else ""))
    sys.exit(1 if FAILS else 0)
