#!/usr/bin/env python3
'''t1115 step 1b (removal half): projects/app-repo/feedback.py must stop defining _stage, _flag and
_n and import them from hub_common.

Every assertion is made on the SOURCE TEXT, parsed with ast. feedback.py is never imported: it is the
live Hub, and importing it runs module-level code and binds 100.64.0.10:8091 (the reason recorded
in scripts/tests/test_hub_common_moves.py). Nothing here writes a file, starts a process, calls a
model, parses ps, opens /tmp, or touches live state -- read-only on the repository.

Red on main (938dff36) for the missing change, verified before writing: feedback.py defines _stage at
line 2473, _flag at 2481 and _n at 2486, and has no module-level import naming them. It does carry
the step-1 import of esc / md_to_html / item_key / tri_state / _who (commit 92b05e96); this suite
does not depend on that line's exact text, only that the five names are imported from hub_common
somewhere, so the removal chunk cannot delete them by accident.

Each name's caller count is printed, not asserted per name: a helper with ZERO callers in
feedback.py needs no import at all, and a suite that demands one for a name nobody calls is red for a
reason that is not the missing change. If a printed count is 0, the name is dead in feedback.py --
drop it from NAMES here and from the implementation brief together, in the same commit, and record
why. The aggregate assertion (at least one of the three is still called) is the load-bearing one: an
import added next to a set of deleted callers is decoration, not a move.

pytest is not installed; the block at the bottom runs every test and exits non-zero on any failure.
'''
import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TARGET = ROOT / 'projects' / 'app-repo' / 'feedback.py'
NAMES = ('_stage', '_flag', '_n')
STEP_ONE = ('esc', 'md_to_html', 'item_key', 'tri_state', '_who')
failures = []


def check(label, cond, detail=''):
    if cond:
        print('  ok   ' + label)
    else:
        print('  FAIL ' + label + (chr(10) + '       ' + detail if detail else ''))
        failures.append(label)


def parse():
    return ast.parse(TARGET.read_text(encoding='utf-8'))


def test_no_longer_defined_in_feedback_py():
    tree = parse()
    defs = sorted({n.name for n in ast.walk(tree)
                   if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in NAMES})
    check('feedback.py defines none of _stage, _flag, _n -- at ANY indentation (a copy kept inside a '
          'function is a move that moved nothing, and a def nested for scope is the same defect)',
          not defs, 'still defined: ' + repr(defs))
    rebinds = []
    for n in tree.body:
        if isinstance(n, ast.AnnAssign):
            targets = [n.target]
        elif isinstance(n, ast.Assign):
            targets = list(n.targets)
        else:
            targets = []
        for t in targets:
            if isinstance(t, ast.Name) and t.id in NAMES:
                rebinds.append(t.id)
    check('and rebinds none of them with a module-level assignment (an inline re-implementation is a '
          'change smuggled into a move, and the verbatim rule is the whole contract of this task)',
          not rebinds, 'module-level bindings left in place: ' + repr(sorted(set(rebinds))))


def test_it_imports_them_from_hub_common_at_module_level():
    tree = parse()
    imported = [a.name for n in tree.body
                if isinstance(n, ast.ImportFrom) and n.module == 'hub_common' and n.level == 0
                for a in n.names]
    missing = [x for x in NAMES if x not in imported]
    check('feedback.py has a module-level from hub_common import naming all three (level 0: an '
          'import inside a function is a per-request import, not the module-level one the plan asks '
          'for, and it is invisible to the import-graph the split is being measured by)',
          not missing,
          'imported from hub_common at module level: ' + repr(sorted(set(imported)))
          + ' | missing ' + repr(missing))


def test_the_import_is_load_bearing():
    tree = parse()
    used = sorted({n.id for n in ast.walk(tree)
                   if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load) and n.id in NAMES})
    check('at least one of _stage, _flag, _n is still CALLED in feedback.py, so the import is needed '
          'and not decoration (per-name counts are printed for the reader)',
          len(used) > 0, 'no Load reference to any of ' + repr(NAMES) + ' found in feedback.py')
    for name in NAMES:
        n = sum(1 for x in ast.walk(tree) if isinstance(x, ast.Name) and x.id == name)
        print('  info ' + name + ' appears as a name ' + str(n) + ' time(s) in feedback.py')


def test_the_step_one_import_survives():
    tree = parse()
    imported = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom) and n.module == 'hub_common':
            imported.update(a.name for a in n.names)
    missing = [x for x in STEP_ONE if x not in imported]
    check('the five helpers moved in step 1 (92b05e96) are still imported from hub_common: a removal '
          'chunk must not delete what its neighbour moved', not missing,
          'imported: ' + repr(sorted(imported)) + ' | missing ' + repr(missing))


def test_feedback_py_still_compiles():
    src = TARGET.read_text(encoding='utf-8')
    try:
        compile(src, 'feedback.py', 'exec')
        ok, detail = True, ''
    except SyntaxError as exc:
        ok = False
        detail = type(exc).__name__ + ' at line ' + str(exc.lineno) + ': ' + str(exc.msg)
    check('feedback.py still compiles after the three defs are cut (a deletion that leaves a '
          'dangling decorator, a lost indent or a stranded closing bracket is not a move: it is a '
          'Hub that will not start)', ok, detail)


if __name__ == '__main__':
    tests = [test_no_longer_defined_in_feedback_py,
             test_it_imports_them_from_hub_common_at_module_level,
             test_the_import_is_load_bearing, test_the_step_one_import_survives,
             test_feedback_py_still_compiles]
    for fn in tests:
        print(chr(10) + '== ' + fn.__name__)
        try:
            fn()
        except Exception as exc:
            check(fn.__name__ + ' raised ' + type(exc).__name__ + ' (a bug in this suite, not the '
                  'missing change: it reads one file by path and must run on main)', False,
                  repr(exc))
    print(chr(10) + str(len(failures)) + ' failure(s)')
    sys.exit(1 if failures else 0)
