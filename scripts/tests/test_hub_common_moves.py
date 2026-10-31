#!/usr/bin/env python3
'''t1115 step 1: projects/app-repo/hub_common.py must carry the Hub's shared helpers.

Measured in this session, 2026-10-13, on the real files. projects/app-repo/hub_common.py exists
(712 bytes, commit cf7f00a9) and defines NO function at all: its whole content is a docstring,
import sys, from pathlib import Path, WORKSPACE = Path(__file__).resolve().parent.parent.parent
(line 11) and sys.path.insert(0, str(WORKSPACE / "scripts")) (line 12). projects/app-repo/feedback.py
(228,840 bytes, 4,178 lines) defines the five helpers this move takes -- item_key (def at 159),
tri_state (170), md_to_html (308, with its two module-level counter assignments at 359-360),
_who (930) and esc (1178) -- so every assertion here is red on main for that one reason.

Nothing in this suite is a stored expectation. The reference behaviour is the SAME five defs taken
out of feedback.py with ast and exec'd into a private namespace, so the expected output is computed
from the real file at run time: a build that re-implements the logic slightly differently, renames a
form field, drops the counters or edits a body fails -- and the suite cannot rot when a body is later
edited. This is the point: the rules of this task say MOVE THE CODE VERBATIM, and a prose instruction
the suite does not enforce is a rule the next dispatch may legally ignore.

feedback.py is never imported. It is the live Hub: importing it runs module-level code and binds
100.64.0.10:8091. The reference defs are extracted with ast.get_source_segment and exec'd, which
runs no module-level statement, binds no port, starts no process, calls no model and writes no file
outside tempfile.TemporaryDirectory. Every subprocess.run carries timeout=. pytest is not installed;
the block at the bottom runs every test and exits non-zero on any failure.

One assertion is about the Hub, not about this move: test_who_finds_the_crew_source asserts _who()
returns something with a callable snapshot(), because the crew strip depends on it (feedback.py:956,
read this session: snap = ww.snapshot() if ww else {"roles": {}}). scripts/who_works.py was not
opened by the author of this suite. If it is absent, _who() prints a NOTE and returns None, this
assertion fails, and that is a real finding about the Hub -- not a bug in this suite.
'''
import ast
import builtins
import contextlib
import hashlib
import html
import inspect
import io
import re
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / 'projects' / 'app-repo'
sys.path.insert(0, str(APP))

NAMES = ('esc', 'md_to_html', 'item_key', 'tri_state', '_who')
failures = []



# THE SOURCE OF THE MOVE (fixed 2026-10-13). This suite compares hub_common.py with feedback.py's own
# definitions; the move's second chunk deletes those, and test_hub_helpers_moved.py REQUIRES them gone,
# so reading the live file made the two oracles impossible to pass together. While feedback.py still
# defines the helpers, read it; once moved, read it at 698b9a7b, the last commit that defines them.
MOVE_FROM = "698b9a7b"


def feedback_source():
    live = (APP / 'feedback.py').read_text(encoding='utf-8')
    if 'def esc(' in live:
        return live
    return subprocess.run(['git', '-C', str(ROOT), 'show', MOVE_FROM + ':projects/app-repo/feedback.py'],
                          capture_output=True, text=True, timeout=60, check=True).stdout

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


def helper(name):
    '''The helper from hub_common, or None after recording why it is not there.'''
    if hub_common is None:
        return None
    fn = getattr(hub_common, name, None)
    check('hub_common.py defines ' + name + ' as a callable', callable(fn),
          'getattr(hub_common, ' + repr(name) + ') is ' + repr(fn))
    return fn if callable(fn) else None


def reference():
    '''feedback.py's five defs plus md_to_html's counters, exec'd from their own source text.

    No import, so no module-level statement of the Hub runs: no port is bound and no process is
    started. The namespace carries the module globals the bodies read (html, re, hashlib, sys).
    '''
    src = feedback_source()
    tree = ast.parse(src)
    ns = {'html': html, 're': re, 'hashlib': hashlib, 'sys': sys, 'WORKSPACE': ROOT}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in NAMES:
            exec(compile(ast.get_source_segment(src, node), 'feedback-defs', 'exec'), ns)
        elif (isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Attribute)
              and getattr(node.targets[0].value, 'id', '') == 'md_to_html'):
            exec(compile(ast.get_source_segment(src, node), 'feedback-defs', 'exec'), ns)
    return ns


def reset(fn):
    '''The counters are module state in feedback.py; a test must not inherit them from a neighbour.'''
    fn.n = 0
    fn.hidden = 0


FENCE = chr(96) * 3
NL = chr(10)
Q = chr(34)
SQ = chr(39)

MD_INPUTS = [
    '# Title',
    '## Section with &amp; and ' + Q + 'quotes' + Q,
    'plain <script> text',
    'a **bold** word and a `code` span',
    '- a bullet',
    '- [ ] first checklist item',
    '- [ ] second **item** with ' + SQ + 'apostrophes' + SQ,
    FENCE + 'x < y & z' + NL + 'second line' + FENCE,
    'unclosed fence' + NL + FENCE + 'tail <b>raw</b>' + NL + 'last line',
    '',
    '   ',
]


def test_helpers_exist():
    for name in NAMES:
        helper(name)


def test_counters_come_with_the_function():
    impl = helper('md_to_html')
    if impl is None:
        return
    check('md_to_html.n exists on the function in hub_common.py (the plan moves the counters with '
          'the def; the release-notes checklist cannot renumber itself without them)',
          getattr(impl, 'n', 'missing') == 0, 'md_to_html.n is ' + repr(getattr(impl, 'n', 'missing')))
    check('md_to_html.hidden exists on the function in hub_common.py',
          getattr(impl, 'hidden', 'missing') == 0,
          'md_to_html.hidden is ' + repr(getattr(impl, 'hidden', 'missing')))


def test_esc_matches_feedback():
    impl = helper('esc')
    ref = reference()
    if impl is None or 'esc' not in ref:
        return
    for v in ['<b>a & b</b>', 'a' + Q + 'b' + SQ + 'c', '', 5, 0.0, ['list', 'x']]:
        check('esc(' + repr(v) + ') produces what the def in feedback.py produces',
              impl(v) == ref['esc'](v),
              'hub_common -> ' + repr(impl(v)) + chr(10) + 'feedback -> ' + repr(ref['esc'](v)))


def test_md_to_html_matches_feedback():
    impl = helper('md_to_html')
    ref = reference()
    if impl is None or 'md_to_html' not in ref:
        return
    for md in MD_INPUTS:
        for checklist in (False, True):
            reset(impl)
            reset(ref['md_to_html'])
            got = impl(md, checklist=checklist)
            want = ref['md_to_html'](md, checklist=checklist)
            check('md_to_html(checklist=' + repr(checklist) + ') on ' + repr(md)
                  + ' renders byte-identical to the def in feedback.py',
                  got == want,
                  'hub_common -> ' + repr(got) + chr(10) + 'feedback -> ' + repr(want))


def test_checklist_renders_the_real_form_fields():
    impl = helper('md_to_html')
    if impl is None:
        return
    md = ('- [ ] first item' + NL + '- [ ] second **item**' + NL
          + '- a plain bullet' + NL + '# A heading')
    reset(impl)
    out = impl(md, checklist=True)
    for frag in ['name="chk1"', 'name="chk2"', 'name="chkkey1"', 'name="chkkey2"',
                 'name="chktext1"', 'name="chktext2"', 'class="tri"',
                 '<h2>A heading</h2>',
                 "<div class=" + SQ + "bullet" + SQ + ">&bull; a plain bullet</div>"]:
        check('the checklist render contains ' + frag + ' (real field names from feedback.py:178-188, '
              'not placeholders)', frag in out, 'rendered: ' + repr(out))
    check('the checklist render emits no third radio row -- a heading and a bullet must not consume '
          'a chk number', 'name="chk3"' not in out, 'rendered: ' + repr(out))
    check('md_to_html.n counted the two checklist items, not the four lines',
          getattr(impl, 'n', 'missing') == 2, 'md_to_html.n is ' + repr(getattr(impl, 'n', 'missing')))


def test_non_checklist_checkbox_is_a_plain_bullet():
    impl = helper('md_to_html')
    if impl is None:
        return
    reset(impl)
    out = impl('- [ ] a')
    check('md_to_html WITHOUT checklist renders the real constant from feedback.py:345 -- a bullet '
          'with the empty-ball entity, not a tri-state row',
          out == '<div class="bullet">&#9744; a</div>', 'got ' + repr(out))
    check('and it consumed no checklist number',
          getattr(impl, 'n', 'missing') == 0, 'md_to_html.n is ' + repr(getattr(impl, 'n', 'missing')))


def test_fenced_code_block_gets_the_copy_button_and_escapes_its_body():
    impl = helper('md_to_html')
    if impl is None:
        return
    reset(impl)
    # The fence is on its own line, as in real markdown (fixed 2026-10-13: the body sat on the opening
    # fence's line, which feedback.py's own md_to_html does not read as code either).
    out = impl(FENCE + NL + 'a < b & c' + NL + 'd' + NL + FENCE)
    for frag in ['<div class="codeblock">', 'class="copy"',
                 '<pre><code>a &lt; b &amp; c' + chr(10) + 'd</code></pre>']:
        check('fenced code renders ' + frag, frag in out, 'got ' + repr(out))


def test_hide_counts_dropped_items_and_renumbers_the_rest():
    impl = helper('md_to_html')
    ik = helper('item_key')
    if impl is None or ik is None:
        return
    md = '- [ ] first item' + NL + '- [ ] second item'
    key1 = hashlib.sha1(b'first item').hexdigest()[:12]
    check('item_key of a raw label equals sha1 of its text, computed here (feedback.py:167)',
          ik('first item') == key1, 'item_key -> ' + repr(ik('first item')))
    reset(impl)
    out = impl(md, checklist=True, hide=(key1,))
    check('a hidden item emits no radio row at all',
          key1 not in out and 'first item' not in out, 'rendered: ' + repr(out))
    # (fixed 2026-10-13: this asserted 'name="chkkey1"' absent while the next check requires the
    # survivor to be renumbered 1 -- which emits chkkey1. The hidden item is identified by ITS key.)
    check('the surviving item is numbered chk1, not chk2 -- the counter counts RENDERED items',
          'name="chk1"' in out and 'name="chk2"' not in out, 'rendered: ' + repr(out))
    check('md_to_html.hidden counted exactly one hidden item',
          getattr(impl, 'hidden', 'missing') == 1,
          'md_to_html.hidden is ' + repr(getattr(impl, 'hidden', 'missing')))
    check('md_to_html.n counted exactly one rendered item',
          getattr(impl, 'n', 'missing') == 1, 'md_to_html.n is ' + repr(getattr(impl, 'n', 'missing')))


def test_item_key_is_a_stable_hash_of_the_stripped_line():
    ik = helper('item_key')
    if ik is None:
        return
    want = hashlib.sha1('- [ ] a'.encode('utf-8')).hexdigest()[:12]
    check('item_key ignores surrounding whitespace, so the rendered key and the submitted key agree',
          ik('  - [ ] a  ') == want, 'got ' + repr(ik('  - [ ] a  ')) + ' want ' + repr(want))
    check('two different labels get two different keys (one repeated placeholder proves nothing)',
          ik('- [ ] a') != ik('- [ ] b'), ik('- [ ] a') + ' == ' + ik('- [ ] b'))
    k = ik('- [ ] a')
    check('a key is 12 hex characters -- the digest length the form field carries',
          len(k) == 12 and all(c in '0123456789abcdef' for c in k), 'got ' + repr(k))


def test_tri_state_emits_the_real_field_names_and_escaping():
    fn = helper('tri_state')
    if fn is None:
        return
    label = 'Pay ' + Q + 'me' + Q + ' & ' + SQ + 'now' + SQ
    out = fn(3, label, 'K3')
    want_value = html.escape(label, quote=True)
    for frag in ['name="chk3"', 'name="chktext3" value="' + want_value + '"',
                 'name="chkkey3" value="K3"', 'id="c3n"', 'id="c3y"', 'id="c3x"',
                 'value="-" checked', 'value="ok"', 'value="bad"',
                 '<div class="chklabel">' + label + '</div>']:
        check('tri_state(3, ..., K3) emits ' + frag, frag in out, 'rendered: ' + repr(out))
    check('the visible label is NOT escaped while the submitted value IS -- the asymmetry the '
          'release-notes checklist relies on',
          ('<div class="chklabel">' + label + '</div>') in out and want_value != label,
          'label ' + repr(label) + ' escaped ' + repr(want_value))


def test_the_moves_are_verbatim():
    if hub_common is None:
        return
    try:
        src = inspect.getsource(hub_common)
    except (OSError, TypeError) as exc:
        check('hub_common source can be read by inspect.getsource', False,
              type(exc).__name__ + ': ' + str(exc))
        return
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


def test_each_helper_resolves_every_global_it_reads():
    '''The symtable property the plan asks for, computed with ast.

    symtable.scan, as used by scripts/tests/test_hub_common.py:234, is not a function in the stdlib
    symtable module; this pins the same thing -- a helper that reads a global its new home does not
    define is a NameError on the first page render -- without depending on an API that does not exist.
    '''
    if hub_common is None:
        return
    try:
        src = inspect.getsource(hub_common)
    except (OSError, TypeError) as exc:
        check('hub_common source can be read by inspect.getsource', False,
              type(exc).__name__ + ': ' + str(exc))
        return
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
    src = inspect.getsource(hub_common)
    hits = []
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Import):
            hits += [a.name for a in node.names if 'feedback' in a.name]
        elif isinstance(node, ast.ImportFrom) and 'feedback' in (node.module or ''):
            hits.append('from ' + str(node.module))
    check('hub_common.py imports nothing from feedback.py (plan step 1: every module imports from '
          'hub_common, never the other way round -- a cycle makes the Hub unimportable)',
          not hits, 'import names mentioning feedback: ' + repr(hits))


def test_who_finds_the_crew_source():
    fn = helper('_who')
    if fn is None:
        return
    saved = list(sys.path)
    try:
        res = fn()
    finally:
        sys.path[:] = saved
    check('_who() returns the crew source exposing a callable snapshot() (the crew strip calls '
          'ww.snapshot() on the result; feedback.py:956)',
          res is not None and callable(getattr(res, 'snapshot', None)), 'returned ' + repr(res))


def test_who_says_why_when_the_crew_source_is_missing():
    fn = helper('_who')
    if fn is None:
        return
    saved = hub_common.WORKSPACE
    # who_works must be truly unreachable for this case (fixed 2026-10-13): an already-imported module
    # is served from sys.modules and the real scripts/ dir is already on sys.path, so pointing
    # WORKSPACE at an empty dir alone changed nothing -- feedback.py's own _who fails this test too.
    saved_mod = sys.modules.pop('who_works', None)
    saved_path = list(sys.path)
    sys.path[:] = [x for x in sys.path if not (Path(x or '.') / 'who_works.py').exists()]
    buf = io.StringIO()
    try:
        with tempfile.TemporaryDirectory() as d:
            hub_common.WORKSPACE = Path(d)
            with contextlib.redirect_stderr(buf):
                res = fn()
    finally:
        hub_common.WORKSPACE = saved
        sys.path[:] = saved_path
        sys.modules.pop('who_works', None)
        if saved_mod is not None:
            sys.modules['who_works'] = saved_mod
    text = buf.getvalue()
    check('_who() returns None when scripts/who_works cannot be imported from a WORKSPACE',
          res is None, 'returned ' + repr(res))
    check('_who() prints WHY to stderr -- a silent None is how a feature disappears from the Hub '
          'and nobody notices (the shape of every swallowed-exception finding in this repository)',
          'NOTE' in text and 'who_works' in text, 'stderr was ' + repr(text))


def test_import_is_clean():
    r = subprocess.run([sys.executable, '-c', 'import hub_common'], cwd=str(APP),
                       capture_output=True, text=True, timeout=60)
    check('python3 -c "import hub_common" exits 0', r.returncode == 0,
          'stdout ' + repr(r.stdout) + chr(10) + 'stderr ' + repr(r.stderr))
    check('importing hub_common prints nothing (a page module that chats on import corrupts every '
          'page that imports it)', r.stdout == '', 'stdout was ' + repr(r.stdout))


if __name__ == '__main__':
    tests = [test_helpers_exist, test_counters_come_with_the_function, test_esc_matches_feedback,
             test_md_to_html_matches_feedback, test_checklist_renders_the_real_form_fields,
             test_non_checklist_checkbox_is_a_plain_bullet,
             test_fenced_code_block_gets_the_copy_button_and_escapes_its_body,
             test_hide_counts_dropped_items_and_renumbers_the_rest,
             test_item_key_is_a_stable_hash_of_the_stripped_line,
             test_tri_state_emits_the_real_field_names_and_escaping,
             test_the_moves_are_verbatim, test_each_helper_resolves_every_global_it_reads,
             test_hub_common_does_not_import_feedback, test_who_finds_the_crew_source,
             test_who_says_why_when_the_crew_source_is_missing, test_import_is_clean]
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
