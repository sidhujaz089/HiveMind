#!/usr/bin/env python3
"""t1072 -- auto_merge must tolerate a hunk outside the named symbols when it ADDS ONLY a
top-level import, __all__, or a module-level UPPER_CASE constant.

From t1056's analysis (workspace/analysis/t1056-why-work-lands-on-claude.md): a build that adds
one import at the top of the file is a hunk outside the named symbols, auto_merge refuses it, and
the work lands on Claude -- 22 builds on 2026-10-09 for that reason. Such a change cannot alter
the behaviour of any named symbol, so the refusal is pure loss.

RED ON MAIN for the stated reason only: auto_merge has no outside_tolerance function. Every test
fails through the guard in _tolerates(), which raises a clean AssertionError naming that function
(no AttributeError traceback, no signature the brief never changes).

Behaviour, not shape: each case is a real pair of source texts plus the list of base-side line
ranges a refusal is keyed on, and the assertion is the True/False a merge decision consumes. The
negatives are the point -- an indented line (a function body), a lowercase module-level statement
(it runs at import), a new def, a replaced line, a removed line, an empty range list, and an
addition covered by no reported range must all still be refused, or the tolerance is a way to
merge anything.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))

import auto_merge  # noqa: E402

BASE_LINES = [
    'import re',
    'import sys',
    '',
    'LIMIT_MS = 300',
    '',
    'def judge(branch):',
    '    return re.match("x", branch)',
]
BASE = '\n'.join(BASE_LINES) + '\n'

RANGES = '1-7'   # a base-side range wide enough to cover any hunk in this 7-line fixture: the rule
                 # under test is WHAT may change inside a reported range, not the offset maths


def _tolerates(branch_lines, outside):
    tol = getattr(auto_merge, 'outside_tolerance', None)
    assert callable(tol), (
        'auto_merge has no outside_tolerance: an added import at the top of a file is still a '
        'hunk outside the named symbols, and refusing it is the t1056 reason 22 builds landed on '
        'Claude')
    return tol(BASE, '\n'.join(branch_lines) + '\n', outside)


def _inserted(lines, index, added):
    return lines[:index] + added + lines[index:]


def test_the_hook_exists():
    assert callable(getattr(auto_merge, 'outside_tolerance', None)), (
        'auto_merge has no outside_tolerance, so the import/constant hunk outside the named '
        'symbols is still a refusal')


def test_a_top_level_import_is_tolerated():
    assert _tolerates(_inserted(BASE_LINES, 2, ['import json']), [RANGES]) is True, (
        "adding 'import json' at column 0 is the case this task is about; it must be tolerated")


def test_an_import_with_its_blank_separator_is_tolerated():
    assert _tolerates(_inserted(BASE_LINES, 2, ['import json', '']), [RANGES]) is True, (
        'the real shape is an import followed by a blank line: a blank line beside a tolerated '
        'addition must not defeat the tolerance')


def test_a_module_level_upper_case_constant_is_tolerated():
    assert _tolerates(_inserted(BASE_LINES, 3, ['MAX_ITEMS = 250']), [RANGES]) is True, (
        'a module-level UPPER_CASE constant is the other addition the analysis names; it cannot '
        'change the behaviour of any named symbol')


def test_a_dunder_all_is_tolerated():
    assert _tolerates(_inserted(BASE_LINES, 2, ["__all__ = ('judge',)"]), [RANGES]) is True, (
        '__all__ is a module-level constant declaration and belongs in the same allowlist')


def test_a_lowercase_module_assignment_is_refused():
    assert _tolerates(_inserted(BASE_LINES, 2, ['cache = {}']), [RANGES]) is False, (
        'a lowercase module-level statement RUNS at import, so it can change behaviour: it is '
        'not in the allowlist')


def test_a_new_function_is_refused():
    assert _tolerates(_inserted(BASE_LINES, 4, ['def helper():', '    return 7']), [RANGES]) is False, (
        'a new def is a new symbol the brief never named; refusing it is the whole point of the '
        'symbols rule')


def test_an_indented_line_is_refused():
    assert _tolerates(_inserted(BASE_LINES, 6, ['    total = 0']), [RANGES]) is False, (
        'an indented addition lands inside a function body, which IS a named symbol behaviour')


def test_a_replaced_line_is_refused():
    lines = list(BASE_LINES)
    lines[1] = 'import os'
    assert _tolerates(lines, [RANGES]) is False, (
        'import os replacing import sys is an EDIT to an existing line, not an addition')


def test_a_removed_line_is_refused():
    lines = [ln for i, ln in enumerate(BASE_LINES) if i != 1]
    assert _tolerates(lines, [RANGES]) is False, (
        'deleting a line removes behaviour; the tolerance is additions only')


def test_an_empty_range_list_is_not_tolerated():
    assert _tolerates(_inserted(BASE_LINES, 2, ['import json']), []) is False, (
        'an empty outside list means the caller reported nothing: the tolerance is keyed to the '
        'ranges reported, it is not a general no-changes pass')


def test_an_addition_outside_every_reported_range_is_refused():
    assert _tolerates(_inserted(BASE_LINES, 2, ['import json']), ['9-14']) is False, (
        'a change covered by no reported range is not a hunk the caller is tolerating: an '
        'allowlist that fires on the file contains imports merges any change it likes')


# ---- 4(D): the blank-line bug in hunks_outside (4-automerge-rca.md section 5, cause D) ----
# `if bn and code and all(...)` refuses a pure-insertion hunk whose added lines are ALL blank
# and comment: code is empty, all([]) is True, and the `code and` conjunct turns the stated
# intent ("blank and comment lines do not execute") into a penalty for the empty case. The
# incidents named in the RCA (1c509bbe, 7d3ed409) died exactly here: a scoper's explanatory
# comment above a new symbol, landed as its own hunk, refused as "outside the named symbols".
#
# hunks_outside runs `git diff -U0`; the test feeds a CANNED diff through the module's own
# `git` function -- real git on temp clones is what coder sandboxes pay for, and the unit
# under test is the hunk-line arithmetic, not git.

DIFF = ''


def _fake_git(*args, **kw):
    return 0, DIFF, ""


def _hunks_outside(diff_text, branch_src, spans=(), new_spans=()):
    global DIFF
    DIFF = diff_text
    real = getattr(auto_merge, "git", None)
    auto_merge.git = _fake_git
    try:
        return auto_merge.hunks_outside("base", "branch", "mod.py", spans,
                                        new_spans=new_spans, branch_src=branch_src)
    finally:
        auto_merge.git = real


# base: the 7 BASE_LINES. branch: a comment + blank inserted after base line 5, before def judge.
BLANK_HUNK_DIFF = "@@ -5,0 +6,2 @@\n+# explain judge\n+\n"
BLANK_BRANCH = "\n".join(BASE_LINES[:5] + ["# explain judge", ""] + BASE_LINES[5:]) + "\n"


def test_a_hunk_of_pure_comment_and_blank_lines_is_inside():
    bad = _hunks_outside(BLANK_HUNK_DIFF, BLANK_BRANCH)
    assert bad == [], (
        "a pure-insertion hunk that adds only a comment and a blank line is NOT a code "
        "change: hunks_outside must not report it (4(D)). The blank-line bug refuses it as "
        "5-5. Got: %r" % (bad,))


def test_the_same_hunk_with_an_unaccounted_code_line_is_still_refused():
    diff = "@@ -5,0 +6,2 @@\n+# explain judge\n+helper_thing = 5\n"
    branch = "\n".join(BASE_LINES[:5] + ["# explain judge", "helper_thing = 5"] + BASE_LINES[5:]) + "\n"
    bad = _hunks_outside(diff, branch)
    assert bad == ["5-5"], (
        "a hunk of blanks is not a licence for a wider change: the moment the same insertion "
        "carries an unaccounted code line, the hunk is outside. Got: %r" % (bad,))


def test_a_code_line_covered_by_a_new_symbol_span_is_inside():
    diff = "@@ -5,0 +6,2 @@\n+# explain judge\n+helper_thing = 5\n"
    branch = "\n".join(BASE_LINES[:5] + ["# explain judge", "helper_thing = 5"] + BASE_LINES[5:]) + "\n"
    bad = _hunks_outside(diff, branch, new_spans=[(7, 7)])
    assert bad == [], (
        "with the code line inside a declared new-symbol span, the hunk is inside. "
        "Got: %r" % (bad,))


def test_a_pure_deletion_of_blank_lines_is_still_refused():
    # n > 0 is a deletion: deleting lines removes bytes, blanks included, and is NOT covered
    # by the (D) fix -- the fix is for pure insertions (n == 0) only.
    diff = "@@ -5,1 +5,0 @@\n"
    branch = "\n".join(BASE_LINES[:4] + BASE_LINES[5:]) + "\n"
    bad = _hunks_outside(diff, branch)
    assert bad == ["5-5"], (
        "the (D) fix must not become a licence to delete lines: a pure deletion hunk stays "
        "outside. Got: %r" % (bad,))


# ---- 4(A): a bare method name on the SYMBOLS line resolves to the method's span ----
# symbol_spans scanned ClassDef bodies only for the "Class.method" form, so the bare name a
# scoper writes for a method (the form the scoper's prompt teaches) matched nothing and every
# hunk inside the method read as outside (4-automerge-rca.md cause A: 4 live branches).

METHOD_SRC = "\n".join([
    "import re",
    "",
    "class Gate:",
    '    """docstring"""',
    "",
    "    @staticmethod",
    "    def check(text):",
    "        return re.match(text, 'x')",
    "",
    "def helper():",
    "    return 1",
    ""]) + "\n"


def test_a_bare_method_name_spans_the_method_not_the_class():
    spans = auto_merge.symbol_spans(METHOD_SRC, {"check"})
    assert spans == [(6, 8)], (
        "a SYMBOLS line naming a method by its bare name must span the method, decorators "
        "included (6-8): symbol_spans matched only the Class.method form, which is the grammar "
        "the scoper's prompt never teaches. Got: %r" % (spans,))


def test_the_class_method_qualified_form_still_works():
    assert auto_merge.symbol_spans(METHOD_SRC, {"Gate.check"}) == [(6, 8)], (
        "the qualified form already worked; it must keep working, identically")


def test_a_bare_name_matching_nothing_spans_nothing():
    assert auto_merge.symbol_spans(METHOD_SRC, {"nosuch"}) == [], (
        "resolving bare names must not invent spans for names the file does not define")


# ---- 4(C): private UPPER_CASE constants (leading underscore) are module constants too ----
# [A-Z][A-Z0-9_]* excludes _CLASSIFIER_NO_VERDICT, _MISS_DIRECTIONS, _IMPORT_STDOUT by their
# leading underscore; the docstring's reason (lowercase runs at import) does NOT apply to an
# underscore-prefixed ALL_CAPS name, which is exactly what a module-private constant is.

def test_a_private_upper_case_constant_is_tolerated_by_the_parser():
    src = "\n".join(["import sys", "", "_CLASSIFIER_NO_VERDICT = 'x'", "",
                     "def f():", "    return 1", ""])
    spans = auto_merge.tolerated_spans(src)
    assert (3, 3) in spans, (
        "_CLASSIFIER_NO_VERDICT is a module-level UPPER_CASE constant with a leading "
        "underscore; tolerated_spans must find it (4(C)). Got: %r" % (spans,))


def test_a_private_upper_case_constant_hunk_is_tolerated_by_the_line_gate():
    assert _tolerates(_inserted(BASE_LINES, 3, ["_MAX_ITEMS = 250"]), [RANGES]) is True, (
        "outside_tolerance's regex must mirror tolerated_spans (two views of one rule): adding "
        "_MAX_ITEMS = 250 at column 0 is as inert as MAX_ITEMS = 250")


def test_a_lower_case_private_name_is_still_refused():
    assert _tolerates(_inserted(BASE_LINES, 3, ["_cache = {}"]), [RANGES]) is False, (
        "the design keeps lowercase module statements refused -- they execute at import; the "
        "(C) fix is about ALL_CAPS names with a leading underscore, nothing wider")


if __name__ == '__main__':
    _tests = [test_the_hook_exists,
              test_a_top_level_import_is_tolerated,
              test_an_import_with_its_blank_separator_is_tolerated,
              test_a_module_level_upper_case_constant_is_tolerated,
              test_a_dunder_all_is_tolerated,
              test_a_lowercase_module_assignment_is_refused,
              test_a_new_function_is_refused,
              test_an_indented_line_is_refused,
              test_a_replaced_line_is_refused,
              test_a_removed_line_is_refused,
              test_an_empty_range_list_is_not_tolerated,
              test_an_addition_outside_every_reported_range_is_refused,
              test_a_hunk_of_pure_comment_and_blank_lines_is_inside,
              test_the_same_hunk_with_an_unaccounted_code_line_is_still_refused,
              test_a_code_line_covered_by_a_new_symbol_span_is_inside,
              test_a_pure_deletion_of_blank_lines_is_still_refused,
              test_a_bare_method_name_spans_the_method_not_the_class,
              test_the_class_method_qualified_form_still_works,
              test_a_bare_name_matching_nothing_spans_nothing,
              test_a_private_upper_case_constant_is_tolerated_by_the_parser,
              test_a_private_upper_case_constant_hunk_is_tolerated_by_the_line_gate,
              test_a_lower_case_private_name_is_still_refused]
    _failed = 0
    for _t in _tests:
        try:
            _t()
            print('PASS  ' + _t.__name__)
        except AssertionError as _e:
            _failed += 1
            print('FAIL  ' + _t.__name__ + ': ' + str(_e))
    print(str(len(_tests) - _failed) + '/' + str(len(_tests)) + ' passed')
    sys.exit(1 if _failed else 0)
