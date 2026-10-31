#!/usr/bin/env python3
'''t1115 step 1b: projects/app-repo/feedback.py imports its shared helpers from hub_common.

Measured 2026-10-13, on the real file: projects/app-repo/feedback.py is 228,776 characters raw,
258,021 once coder.numbered() numbers every line (projects/local-inference/coder.py:565) against
coder.EXCERPT_CAP = 260,000 (same file, line 562) -- 1,979 characters of headroom. Past the cap a
local model is shown an EXCERPT of the file it is editing, which is why the Hub is being split.

RED TODAY, for the stated reason: feedback.py still DEFINES esc, md_to_html, item_key, tri_state
and _who at top level (the task's step-1 list), and nothing in it imports from hub_common. So
test_no_definitions_left and test_imports_from_hub_common both fail on main.

This suite deliberately does NOT import feedback.py: importing it runs module-level code, and the
file is the live Hub -- it binds 100.64.0.10:8091 at module level. It parses the file with ast
and text only, so it runs in any checkout, writes nothing, starts nothing, calls no model and
leaves no process.

test_esc_still_called is GREEN on main and must stay green. It is not decoration: the cheapest way
for a build to make the two red assertions pass is to delete the calls to esc and md_to_html along
with their definitions, and every page-rendering suite named in the brief's behaviour_oracle is
itself a static AST suite -- so this is the only thing in the tree that pins the calls surviving
the move. Its green-on-main status is by design; the red-on-main requirement is carried by the two
assertions above it.

It also pins the counters the plan says to move with md_to_html (steps 2 and 11): a module-level
definition of md_to_html.n is a write to an attribute of the imported function, which breaks once
the function lives in hub_common. That assertion is green on main if feedback.py has no such line;
it is here so the pattern cannot be introduced by the move.

No pytest (not installed). No model, no server, no live state, no process left running.
'''
import ast
import re
import sys
import traceback
from pathlib import Path

APP = Path(__file__).resolve().parents[2] / 'projects' / 'app-repo'
MOVED = ('esc', 'md_to_html', 'item_key', 'tri_state', '_who')

failures = []


def check(label, cond, detail=''):
    if cond:
        print('  ok   ' + label)
    else:
        print('  FAIL ' + label + (('\n       ' + detail) if detail else ''))
        failures.append(label)


def source():
    return (APP / 'feedback.py').read_text(encoding='utf-8').splitlines()


def test_no_definitions_left():
    tree = ast.parse((APP / 'feedback.py').read_text(encoding='utf-8'))
    top = {node.name: node.lineno for node in tree.body
           if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
    for name in MOVED:
        check('feedback.py no longer defines ' + name + ' (it is a move out of the Hub, not a copy)',
              name not in top,
              'def ' + name + ' is still at feedback.py line ' + str(top.get(name)))


def test_imports_from_hub_common():
    tree = ast.parse((APP / 'feedback.py').read_text(encoding='utf-8'))
    imported = set()
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and (node.module or '').endswith('hub_common'):
            imported.update(a.asname or a.name for a in node.names)
    for name in MOVED:
        check('feedback.py imports ' + name + ' from hub_common at module level',
              name in imported,
              'module-level imports from hub_common are ' + repr(sorted(imported)))


def test_esc_still_called():
    tree = ast.parse((APP / 'feedback.py').read_text(encoding='utf-8'))
    called = sum(1 for node in ast.walk(tree)
                 if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                 and node.func.id == 'esc')
    check('esc is still CALLED from feedback.py -- the move takes the def, not the call sites '
          '(a build that deletes the def by deleting its callers passes the other two checks)',
          called > 0, 'ast.walk finds no call to esc() in feedback.py')


def test_no_module_wide_state_left_behind():
    offenders = [(i + 1, line) for i, line in enumerate(source())
                 if re.match(r'^md_to_html\.\w+\s*(=|\+=|-=)', line)]
    check('no module-level md_to_html.<counter> line survives in feedback.py (the plan moves the '
          'counters with md_to_html; a write to an attribute of an imported function breaks the page)',
          not offenders,
          repr(['%d: %s' % (n, t.strip()) for n, t in offenders]))


if __name__ == '__main__':
    tests = [test_no_definitions_left, test_imports_from_hub_common, test_esc_still_called,
             test_no_module_wide_state_left_behind]
    for fn in tests:
        print(chr(10) + '== ' + fn.__name__)
        try:
            fn()
        except Exception as exc:
            check(fn.__name__ + ' raised ' + type(exc).__name__ + ' (a bug in this suite, not a '
                  'missing change: it reads feedback.py by path and must run on main)',
                  False, traceback.format_exc(limit=6))
    print(chr(10) + str(len(failures)) + ' failure(s)')
    sys.exit(1 if failures else 0)
