#!/usr/bin/env python3
'''t1115 step 1b (copy half, take 2): hub_common.py must carry _stage, _flag and _n.

WHY THIS FILE EXISTS. scripts/tests/test_hub_moves2_hub_common.py asserts, for _stage with no
cards, the string
    <details class="kstage"><summary><b>QA (0)</b></details><div class="small">&mdash;</div>
which closes no </summary> and closes no </details>. The real def (projects/app-repo/feedback.py
2473-2478, read 2026-10-14 at 626e323c) closes the summary and puts the body INSIDE the details.
Verified by exec'ing the def verbatim on 2026-10-14:
    _stage('QA', []) ==
    '<details class="kstage"><summary><b>QA (0)</b></summary><div class="small">&mdash;</div></details>'
So that suite's two assertions -- the def is byte-identical to feedback.py's, and it returns the
string above -- cannot both be true of any code: a verbatim copy passes the first and fails the
second, and a def that satisfies the second is not the def the task asks to move. The preflight
upheld this as NOT SATISFIABLE (workspace/preflight-objections.jsonl, 2026-10-14T040612Z) and five
attempts were burned on it. The fix is the ASSERTED STRING, not the verbatim rule.

This file is a NEW suite, not an edit of the old one: the bridge refuses an oracle chunk whose file
already exists with different text (that refusal is what sent the 2026-10-14 15:02Z scope back), so
the superseded suite is retired by Claude in the same commit -- the step-1 precedent, where
test_hub_common.py was retired as a duplicate of test_hub_common_moves.py.

The expected values are not hand-written constants of convenience: reference() extracts
feedback.py's OWN three defs with ast.get_source_segment and exec's them, so a build that renames a
CSS class, drops the (n) count from the header, edits the &middot; separator, swaps the argument
order, reflows a docstring or tidies a signature fails. Reference text comes from the live
feedback.py while it defines the three names, and from the newest commit on that path that still
defines them once it does not -- so the copy oracle and the removal oracle can both pass, which a
single pinned sha cannot do.

feedback.py is NEVER imported: importing it runs module-level code and binds 100.64.0.10:8091.
Nothing here writes a file, starts a process, calls a model, parses ps or opens a fixed /tmp path;
the two git calls carry timeout=60. pytest is not installed; the block at the bottom runs every
test and exits non-zero on any failure.

NOT in this scope, on purpose: _jsonl_tail (it needs import json, which hub_common.py does not
import today -- its imports are hashlib, html, re, sys, Path) and shell (feedback.py:916), which
reads feedback.py module globals and is not a verbatim move.
'''
import ast
import builtins
import subprocess
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / 'projects' / 'app-repo'
sys.path.insert(0, str(APP))

NAMES = ('_stage', '_flag', '_n')
STEP_ONE = ('esc', 'md_to_html', 'item_key', 'tri_state', '_who')
SRC_PATH = 'projects/app-repo/feedback.py'
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


def defines_all_three(src):
    names = set(n.name for n in ast.parse(src).body if isinstance(n, ast.FunctionDef))
    return all(n in names for n in NAMES)


def feedback_source():
    '''feedback.py's text: the live file while it defines the three, else the newest commit on that
    path that does. The live file stops defining them the day the removal chunk lands, and a pinned
    sha rots the day the code moves again.'''
    live = (APP / 'feedback.py').read_text(encoding='utf-8')
    if defines_all_three(live):
        return live
    r = subprocess.run(['git', '-C', str(ROOT), 'log', '--format=%H', '-50', '--', SRC_PATH],
                       capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        raise RuntimeError('git log on ' + SRC_PATH + ' failed: ' + r.stderr)
    for sha in r.stdout.split():
        s = subprocess.run(['git', '-C', str(ROOT), 'show', sha + ':' + SRC_PATH],
                           capture_output=True, text=True, timeout=60)
        if s.returncode != 0:
            continue
        try:
            if defines_all_three(s.stdout):
                return s.stdout
        except SyntaxError:
            continue
    raise RuntimeError('no commit touching ' + SRC_PATH + ' defines all three: ' + repr(NAMES))


def reference():
    '''The three defs, exec'd from feedback.py's own source text. No import, so no module-level
    statement of the Hub runs: no port is bound and no process is started.'''
    src = feedback_source()
    ns = {}
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
    mine = {}
    for n in ast.parse(src).body:
        if isinstance(n, ast.FunctionDef):
            mine[n.name] = ast.get_source_segment(src, n)
    theirs = {}
    for n in ast.parse(fbsrc).body:
        if isinstance(n, ast.FunctionDef):
            theirs[n.name] = ast.get_source_segment(fbsrc, n)
    for name in NAMES:
        check('the def of ' + name + ' in hub_common.py is byte-identical to the one in feedback.py',
              mine.get(name) is not None and mine.get(name) == theirs.get(name),
              'hub_common -> ' + repr(mine.get(name)) + chr(10) + 'feedback -> ' + repr(theirs.get(name)))


def test_stage_is_the_details_block_the_task_board_renders():
    fn = fn_of('_stage')
    if fn is None:
        return
    empty = fn('QA', [])
    check('_stage with no cards: collapsed details, zero count, placeholder INSIDE the details',
          empty == ('<details class="kstage"><summary><b>QA (0)</b></summary>'
                    '<div class="small">&mdash;</div></details>'),
          'got ' + repr(empty))
    two = fn('QA', ['<b>alpha</b>', '<i>beta</i>'], open_=True, empty='nothing here')
    check('with two distinct cards the header counts 2, both appear, the placeholder is not used',
          two == ('<details class="kstage" open><summary><b>QA (2)</b></summary>'
                  '<b>alpha</b><i>beta</i></details>'),
          'got ' + repr(two))
    one_open = fn('Deploy', ['<p>one</p>'], True, 'EMPTY')
    check('open_ works positionally, and a non-default placeholder never appears when cards exist',
          one_open == ('<details class="kstage" open><summary><b>Deploy (1)</b></summary>'
                       '<p>one</p></details>') and 'EMPTY' not in one_open,
          'got ' + repr(one_open))
    closed = fn('Deploy', ['<p>one</p>'])
    check('the default is CLOSED: the open attribute is in the open_ render, not in the default',
          '<details class="kstage" open>' in two and '<details class="kstage" open>' not in closed,
          'open_ -> ' + repr(two) + chr(10) + 'default -> ' + repr(closed))
    check('the summary is closed before the body and the block closes -- the shape the board renders',
          '</summary>' in empty and empty.endswith('</details>')
          and empty.index('</summary>') < empty.index('<div class="small">'),
          'got ' + repr(empty))


def test_flag_is_the_owner_tag_the_pages_call():
    fn = fn_of('_flag')
    if fn is None:
        return
    plain = fn('CLAUDE')
    check('_flag with no detail: the span carries the label and nothing follows it',
          plain == '<div class="small"><span class="nextflag">CLAUDE</span></div>',
          'got ' + repr(plain))
    soon = fn('SOON', 'gate is red', True)
    check('_flag with a detail and soon=True: soon class, label and detail -- three distinct values',
          soon == '<div class="small"><span class="nextflag soon">SOON</span> gate is red</div>',
          'got ' + repr(soon))
    check('the no-detail render carries neither the soon class nor a literal None',
          'soon' not in plain and 'None' not in plain, 'got ' + repr(plain))


def test_n_names_the_stage_for_its_flow_task():
    fn = fn_of('_n')
    if fn is None:
        return
    with_state = fn('t1115', 'Build', 'testing')
    plain = fn('t1115', 'Build')
    check('_n with a state: name, separator entity, state, as the board prints it',
          with_state == 'Build &middot; testing', 'got ' + repr(with_state))
    check('_n with no state: the name alone, no trailing separator, and the task id is not printed',
          plain == 'Build' and '&middot;' not in plain and 't1115' not in plain,
          'got ' + repr(plain))


def test_behaviour_matches_the_defs_in_feedback_py():
    ref = reference()
    for name in NAMES:
        if name not in ref:
            check('the reference source defines ' + name, False,
                  'feedback.py source has no top-level def named ' + name)
    calls = [('_stage', ('QA',), {}),
             ('_stage', ('QA', ['<b>alpha</b>', '<i>beta</i>']), {'open_': True, 'empty': 'n/a'}),
             ('_stage', ('Deploy', ['<p>one</p>'], True, 'EMPTY'), {}),
             ('_stage', ('Deploy', ['<p>one</p>']), {}),
             ('_flag', ('CLAUDE',), {}),
             ('_flag', ('SOON', 'gate is red', True), {}),
             ('_n', ('t1115', 'Build', 'testing'), {}),
             ('_n', ('t1115', 'Build'), {})]
    for name, args, kw in calls:
        impl = getattr(hub_common, name, None) if hub_common is not None else None
        if not callable(impl) or not callable(ref.get(name)):
            continue
        got = impl(*args, **kw)
        want = ref[name](*args, **kw)
        check(name + ' with ' + repr(args) + repr(kw) + ' returns what the def in feedback.py returns',
              got == want, 'hub_common -> ' + repr(got) + chr(10) + 'feedback -> ' + repr(want))


def test_the_helpers_resolve_every_global_they_read():
    '''A helper that reads a global its new home does not define is a NameError on the first page
    render. Computed with ast (symtable.scan is not a function in the stdlib symtable module).'''
    if hub_common is None:
        return
    src = (APP / 'hub_common.py').read_text(encoding='utf-8')
    allowed = set(dir(hub_common)) | set(dir(builtins))
    defs = {}
    for n in ast.parse(src).body:
        if isinstance(n, ast.FunctionDef):
            defs[n.name] = n
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
        free = set(n.id for n in ast.walk(fn) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load))
        missing = sorted(x for x in free - bound if x not in allowed)
        check(name + ' resolves every global it reads inside hub_common.py', not missing,
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
    check('hub_common.py imports nothing from feedback.py (plan step 1: everything imports FROM '
          'hub_common, never the other way round -- a cycle makes the Hub unimportable)',
          not hits, 'import names mentioning feedback: ' + repr(hits))


def test_step_one_is_not_clobbered():
    '''A chunk that re-emits the whole file can quietly drop what is in it (t502: syntax errors in a
    re-emitted file). The append must leave the five helpers of 92b05e96 reachable, counters
    included -- md_to_html.n and md_to_html.hidden are function attributes, so a rewrite that
    forgets them breaks the release-notes checklist in silence.'''
    if hub_common is None:
        return
    for name in STEP_ONE:
        fn = getattr(hub_common, name, None)
        check('hub_common.py still defines ' + name + ' as a callable after the append',
              callable(fn), 'getattr(hub_common, ' + repr(name) + ') is ' + repr(fn))
    md = getattr(hub_common, 'md_to_html', None)
    if callable(md):
        check('md_to_html still carries its counters, both starting at 0',
              getattr(md, 'n', 'missing') == 0 and getattr(md, 'hidden', 'missing') == 0,
              'n=' + repr(getattr(md, 'n', 'missing')) + ' hidden=' + repr(getattr(md, 'hidden', 'missing')))


def test_import_is_clean():
    r = subprocess.run([sys.executable, '-c', 'import hub_common'], cwd=str(APP),
                       capture_output=True, text=True, timeout=60)
    check('python3 -c import hub_common exits 0', r.returncode == 0,
          'stdout ' + repr(r.stdout) + chr(10) + 'stderr ' + repr(r.stderr))
    check('importing hub_common prints nothing (a module that chats on import corrupts every page)',
          r.stdout == '', 'stdout was ' + repr(r.stdout))


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
