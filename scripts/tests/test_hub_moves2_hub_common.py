#!/usr/bin/env python3
'''t1115 step 1b (copy half): hub_common.py must carry _stage, _flag and _n.

Measured in this session, 2026-10-13. projects/app-repo/hub_common.py (117 lines at 938dff36)
defines esc, item_key, md_to_html, tri_state and _who -- step 1, commit 92b05e96 -- and NOT one of
the three names this move takes. projects/app-repo/feedback.py defines all three at module level:
_stage (def at 2473), _flag (2481), _n (2486). So every assertion below is red on main for that one
reason, which is exactly the change the brief asks for.

The expected output is not stored anywhere. The reference behaviour is feedback.py's OWN defs,
extracted with ast.get_source_segment and exec'd into a private namespace at run time, so a build
that renames a CSS class, drops the (n) count from the header, edits the &middot; separator, swaps
the argument order or tidies the signature fails -- and the suite cannot rot when a body is later
edited. Reference text is taken from the live feedback.py while it still defines the three names and
from `git show 938dff36:projects/app-repo/feedback.py` once it does not (the rule the last scope
learned the hard way: compare against a pinned commit, never the live file, or the copy oracle and
the removal oracle cannot both pass).

feedback.py is NEVER imported: it is the live Hub, and importing it runs module-level code and binds
100.64.0.10:8091. Nothing here writes outside tempfile.TemporaryDirectory; the one subprocess
carries timeout=60; no fixed /tmp path; no model call; no process left running. pytest is not
installed, so the block at the bottom runs every test and exits non-zero on any failure.

NOT in this scope, on purpose:
  _jsonl_tail (feedback.py:1602-1629) -- the same move, one symbol, the next scope. The last lines
  of its row loop (the json.loads / except tail) sat outside the window this brief was written from,
  and a brief that supplies them from memory asks the builder to invent them. It also needs
  `import json`, which hub_common.py does not have today (hub_common.py:8-12).
  shell (feedback.py:916-925) -- VERIFIED NOT MOVABLE into hub_common.py as it stands: it reads
  CREW_TABS, CSS, _ACTIVE_KEY, appnav and crew_strip, all module globals of feedback.py. The
  verbatim move is a NameError on every page render (html is fine: hub_common.py:9 imports it).
  Its move belongs after step 2 puts crew_strip, CSS and appnav where hub_common can see them.
'''
import ast
import builtins
import json
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / 'projects' / 'app-repo'
sys.path.insert(0, str(APP))

NAMES = ('_stage', '_flag', '_n')
MOVE_FROM = '938dff36'
STILL_THERE = ('esc', 'md_to_html', 'item_key', 'tri_state', '_who')
failures = []


def check(label, cond, detail=''):
    if cond:
        print('  ok   ' + label)
    else:
        print('  FAIL ' + label + (chr(10) + '       ' + detail if detail else ''))
        failures.append(label)


try:
    import hub_common
except Exception as exc:
    hub_common = None
    check('hub_common.py is importable from projects/app-repo', False,
          type(exc).__name__ + ': ' + str(exc))


def feedback_source():
    '''feedback.py's text: the live file while it still defines the three, else the pinned commit.'''
    live = (APP / 'feedback.py').read_text(encoding='utf-8')
    defs = {n.name for n in ast.parse(live).body if isinstance(n, ast.FunctionDef)}
    if all(n in defs for n in NAMES):
        return live
    r = subprocess.run(['git', '-C', str(ROOT), 'show', MOVE_FROM + ':projects/app-repo/feedback.py'],
                       capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        raise RuntimeError('git show ' + MOVE_FROM + ' failed: ' + r.stderr)
    return r.stdout


def reference():
    '''feedback.py's three defs, exec'd from their own source text. No import, so no module-level
    statement of the Hub runs: no port is bound and no process is started.'''
    src = feedback_source()
    ns = {'json': json}
    for node in ast.parse(src).body:
        if isinstance(node, ast.FunctionDef) and node.name in NAMES:
            exec(compile(ast.get_source_segment(src, node), 'feedback-defs', 'exec'), ns)
    return ns


def fn_of(name):
    fn = getattr(hub_common, name, None) if hub_common is not None else None
    check('hub_common.py defines ' + name + ' as a callable', callable(fn),
          'getattr(hub_common, ' + repr(name) + ') is ' + repr(fn))
    return fn if callable(fn) else None


def test_helpers_exist():
    for name in NAMES:
        fn_of(name)


def test_the_moves_are_verbatim():
    if hub_common is None:
        return
    src = (APP / 'hub_common.py').read_text(encoding='utf-8')
    fbsrc = feedback_source()
    mine = {n.name: ast.get_source_segment(src, n) for n in ast.parse(src).body
            if isinstance(n, ast.FunctionDef)}
    theirs = {n.name: ast.get_source_segment(fbsrc, n) for n in ast.parse(fbsrc).body
              if isinstance(n, ast.FunctionDef)}
    for name in NAMES:
        check(name + ' in hub_common.py is byte-identical to the def in feedback.py (the task says '
              'move verbatim: a move and a change in one commit hide each other)',
              mine.get(name) is not None and mine.get(name) == theirs.get(name),
              'hub_common -> ' + repr(mine.get(name)) + chr(10) + 'feedback -> ' + repr(theirs.get(name)))


def test_stage_is_the_details_block_the_task_board_renders():
    fn = fn_of('_stage')
    if fn is None:
        return
    # Exact strings, from feedback.py:2473-2478: an `in` check would let a near-miss pass.
    check('_stage with no cards renders the collapsed <details> with a zero count and the empty placeholder',
          fn('QA', []) == ('<details class="kstage"><summary><b>QA (0)</b></details>'
                           '<div class="small">&mdash;</div>'),
          'got ' + repr(fn('QA', [])))
    full = fn('QA', ['<b>alpha</b>', '<i>beta</i>'], open_=True, empty='nothing here')
    check('with two cards the header counts 2, BOTH distinct cards appear once each, and the empty '
          'placeholder is NOT used -- three distinct values a template cannot fake',
          full == ('<details class="kstage" open><summary><b>QA (2)</b></summary>'
                   '<b>alpha</b><i>beta</i></details>'),
          'got ' + repr(full))
    check('open_=True opens the details block and the default (no open_ kwarg) does not',
          '<details class="kstage" open>' in full and '<details class="kstage" open>' not in fn('QA', []),
          repr(fn('QA', [])))


def test_flag_is_the_owner_tag_the_pages_call():
    fn = fn_of('_flag')
    if fn is None:
        return
    check('_flag with no detail renders the span with the label and NOTHING after it',
          fn('CLAUDE') == '<div class="small"><span class="nextflag">CLAUDE</span></div>',
          repr(fn('CLAUDE')))
    check('_flag(label, detail, soon=True) carries the soon class, the label AND the detail',
          fn('SOON', 'gate is red', True) ==
          '<div class="small"><span class="nextflag soon">SOON</span> gate is red</div>',
          repr(fn('SOON', 'gate is red', True)))


def test_n_names_the_stage_for_its_flow_task():
    fn = fn_of('_n')
    if fn is None:
        return
    check('n with a state renders name, separator entity, state -- the exact text the board shows',
          fn('t1115', 'Build', 'testing') == 'Build &middot; testing',
          repr(fn('t1115', 'Build', 'testing')))
    check('n with no state renders the name alone, with no trailing separator',
          fn('t1115', 'Build') == 'Build', repr(fn('t1115', 'Build')))


def test_behaviour_matches_the_defs_in_feedback_py():
    ref = reference()
    calls = [('_stage', ('QA',), {}),
             ('_stage', ('QA', ['<b>alpha</b>', '<i>beta</i>']), {'open_': True, 'empty': 'nothing here'}),
             ('_stage', ('Deploy', ['<p>one</p>'], True, 'EMPTY'), {}),
             ('_flag', ('CLAUDE',), {}),
             ('_flag', ('SOON', 'gate is red', True), {}),
             ('_n', ('t1115', 'Build', 'testing'), {}),
             ('_n', ('t1115', 'Build'), {})]
    for name, args, kw in calls:
        impl = getattr(hub_common, name, None) if hub_common is not None else None
        if not callable(impl) or not callable(ref.get(name)):
            continue
        got, want = impl(*args, **kw), ref[name](*args, **kw)
        check(name + str(args) + str(kw) + ' returns what the def in feedback.py returns',
              got == want, 'hub_common -> ' + repr(got) + chr(10) + 'feedback -> ' + repr(want))


def test_the_helpers_resolve_every_global_they_read():
    '''The symtable property the plan asks for, computed with ast (symtable.scan, used by an earlier
    suite, is not a function in the stdlib symtable module). A helper that reads a global its new
    home does not define is a NameError on the first page render -- this is the check that says so
    before the app is restarted.'''
    if hub_common is None:
        return
    src = (APP / 'hub_common.py').read_text(encoding='utf-8')
    allowed = set(dir(hub_common)) | set(dir(builtins))
    defs = {n.name: n for n in ast.parse(src).body if isinstance(n, ast.FunctionDef)}
    for name in NAMES:
        fn = defs.get(name)
        if fn is None:
            check('hub_common.py has a top-level def named ' + name + ' to check', False,
                  'no top-level def named ' + name)
            continue
        bound = set(a.arg for a in list(fn.args.args) + list(fn.args.posonlyargs)
                    + list(fn.args.kwonlyargs))
        if fn.args.vararg:
            bound.add(fn.args.vararg.arg)
        if fn.args.kwarg:
            bound.add(fn.args.kwarg.arg)
        for node in ast.walk(fn):
            if isinstance(node, ast.Lambda):
                bound.update(a.arg for a in list(node.args.args) + list(node.args.kwonlyargs))
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                bound.add(node.name)
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    bound.update(a.arg for a in list(node.args.args) + list(node.args.kwonlyargs))
            elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
                bound.add(node.id)
            elif isinstance(node, ast.ExceptHandler) and node.name:
                bound.add(node.name)
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                for a in node.names:
                    bound.add((a.asname or a.name).split('.')[0])
        free = sorted(set(n.id for n in ast.walk(fn)
                          if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)) - bound)
        missing = [x for x in free if x not in allowed]
        check(name + ' resolves every global it reads inside hub_common.py, so it cannot NameError '
              'in its new home', not missing,
              'globals it reads that hub_common.py does not define: ' + repr(missing))


def test_hub_common_does_not_import_feedback():
    if hub_common is None:
        return
    src = (APP / 'hub_common.py').read_text(encoding='utf-8')
    hits = []
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Import):
            hits += [a.name for a in node.names if 'feedback' in a.name]
        elif isinstance(node, ast.ImportFrom) and 'feedback' in (node.module or ''):
            hits.append('from ' + str(node.module))
    check('hub_common.py imports nothing from feedback.py (plan step 1: every module imports FROM '
          'hub_common, never the other way round -- a cycle makes the Hub unimportable)',
          not hits, 'import names mentioning feedback: ' + repr(hits))


def test_step_one_is_not_clobbered():
    '''A chunk that re-emits the whole file can quietly drop what is already in it (t502: syntax
    errors in a re-emitted file). Appending three defs must leave the five that landed in 92b05e96
    reachable, counters included -- md_to_html's counters are function attributes, so a rewrite that
    forgets them breaks the release-notes checklist silently.'''
    if hub_common is None:
        return
    for name in STILL_THERE:
        fn = getattr(hub_common, name, None)
        check('hub_common.py still defines ' + name + ' as a callable after the append',
              callable(fn), 'getattr(hub_common, ' + repr(name) + ') is ' + repr(fn))
    md = getattr(hub_common, 'md_to_html', None)
    if callable(md):
        check('md_to_html still carries the counters it moved with (n and hidden both start at 0)',
              getattr(md, 'n', 'missing') == 0 and getattr(md, 'hidden', 'missing') == 0,
              'n=' + repr(getattr(md, 'n', 'missing')) + ' hidden=' + repr(getattr(md, 'hidden', 'missing')))


def test_import_is_clean():
    r = subprocess.run([sys.executable, '-c', 'import hub_common'], cwd=str(APP),
                       capture_output=True, text=True, timeout=60)
    check('python3 -c import hub_common exits 0', r.returncode == 0,
          'stdout ' + repr(r.stdout) + chr(10) + 'stderr ' + repr(r.stderr))
    check('importing hub_common prints nothing (a page module that chats on import corrupts every '
          'page that imports it)', r.stdout == '', 'stdout was ' + repr(r.stdout))


if __name__ == '__main__':
    tests = [test_helpers_exist, test_the_moves_are_verbatim,
             test_stage_is_the_details_block_the_task_board_renders,
             test_flag_is_the_owner_tag_the_pages_call,
             test_n_names_the_stage_for_its_flow_task,
             test_behaviour_matches_the_defs_in_feedback_py,
             test_the_helpers_resolve_every_global_they_read,
             test_hub_common_does_not_import_feedback, test_step_one_is_not_clobbered,
             test_import_is_clean]
    for fn in tests:
        print(chr(10) + '== ' + fn.__name__)
        try:
            fn()
        except Exception as exc:
            check(fn.__name__ + ' raised ' + type(exc).__name__ + ' (a bug in this suite, not the '
                  'missing change: it reads both files by path and must run on main)', False,
                  traceback.format_exc())
    print(chr(10) + str(len(failures)) + ' failure(s)')
    sys.exit(1 if failures else 0)
