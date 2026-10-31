#!/usr/bin/env python3
'''t702 oracle: a repeat request for a file shown as a SYMBOL SLICE.

The symptom, read off coder.py itself: a brief names SYMBOLS, the model needs something
outside that slice and asks for the file, and the repeat branch serves
unseen(root, rel, shown_to.get(rel, 0)) -- line 2862. shown_to is written only on a
successful partial serve (line 2876), never when the file entered as a slice, so upto is 0
and unseen() answers with the file from LINE 1 up to EXCERPT_CAP = 260_000 characters. The
size guard then refuses that with "no room for more of it beside the prompt" (line 2871),
a refusal that names nothing the model can ask for instead. Ask, receive-the-whole-file, be
refused, ask again: the loop scripts/tests/test_coder_bare_method_slice.py records.

The change, pinned as behaviour on the repository's own coder.py text:
  askable(text)                          -> the file's real symbol names, in file order
  slice_rest(text, symbols, shown, room) -> (body, omitted)
plus the repeat branch calling slice_rest and the refusal built from askable.

Hermetic: reads projects/local-inference/coder.py read-only; all string-in/string-out. No
model, no server, no subprocess, no writes, no fixed /tmp.
'''
import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'projects' / 'local-inference'))
import coder  # noqa: E402

TARGET = 'projects/local-inference/coder.py'
SRC = (ROOT / TARGET).read_text(encoding='utf-8')
LINES = SRC.splitlines()
REFUSAL = 'no room for more of it'
REAL = ('sliced', 'scope_gate', 'unseen', 'prompt_fits')   # names that are in coder.py
FAILS = []


def ck(name, cond, detail=''):
    print(f"  [{'ok' if cond else 'BAD'}]  {name}" + ('' if cond else f'   {detail}'))
    if not cond:
        FAILS.append(name)


def spans_of(text):
    '''A symbol's own lines, the way sliced() takes them: decorator line -> end_lineno.'''
    out, tree = {}, ast.parse(text)
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            lo = min([node.lineno] + [d.lineno for d in getattr(node, 'decorator_list', [])])
            out[node.name] = (lo, node.end_lineno)
            if isinstance(node, ast.ClassDef):
                for sub in node.body:
                    if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        slo = min([sub.lineno] + [d.lineno for d in getattr(sub, 'decorator_list', [])])
                        out[f'{node.name}.{sub.name}'] = (slo, sub.end_lineno)
    return out


def test_repeat_of_a_sliced_file_is_answered_with_the_whole_file_from_line_1_today():
    '''Pins the symptom, so the record says what the fix removes. Passes on main.'''
    got = coder.unseen(ROOT, TARGET, 0)
    ck('unseen(root, rel, 0) answers a repeat with the file from LINE 1 (the t702 symptom)',
       bool(got) and got[0].startswith('--- ' + TARGET + ' (lines 1-'),
       repr(got[0][:70]) if got else 'no text returned')
    m = re.search(r'lines 1-(\d+) of (\d+)', got[0]) if got else None
    ck('that answer is the WHOLE file, not a window that fits',
       bool(m) and int(m.group(1)) > 1000 and int(m.group(2)) == len(LINES),
       str(m.groups()) if m else 'unparseable header: ' + repr(got[0][:70] if got else ''))


def test_askable_lists_the_names_the_file_really_has():
    if not hasattr(coder, 'askable'):
        ck('coder.askable exists', False,
           'no askable in coder.py -- a refusal that cannot name what to ask for (t702)')
        return
    names = coder.askable(SRC)
    ck('askable returns a non-empty list of str',
       isinstance(names, list) and bool(names) and all(isinstance(n, str) for n in names),
       repr(names)[:90])
    for n in REAL:
        ck(f'askable names the real symbol {n!r} of this file', n in names)
    ck('askable invents nothing that is not in the file (negative)',
       'definitely_not_a_symbol' not in names)
    ck('askable is deduplicated', len(names) == len(set(names)))
    s = coder.sliced(SRC, list(names))
    ck('every name askable() lists is found by sliced() -- it is the slice vocabulary',
       bool(s) and '(NOT FOUND' not in s,
       'sliced() reported NOT FOUND for a name askable() listed')


def test_slice_rest_serves_the_unshown_symbols_in_a_window_that_fits():
    if not hasattr(coder, 'slice_rest') or not hasattr(coder, 'askable'):
        ck('coder.slice_rest and coder.askable exist', False,
           'no slice_rest in coder.py -- a repeat of a sliced file has no window that fits (t702)')
        return
    spans = spans_of(SRC)
    names = list(coder.askable(SRC))
    shown = names[:10]
    room = 20_000
    got = coder.slice_rest(SRC, names, shown, room)
    ck('slice_rest returns (str, list)',
       isinstance(got, tuple) and len(got) == 2 and isinstance(got[0], str)
       and isinstance(got[1], list), type(got).__name__)
    if not (isinstance(got, tuple) and len(got) == 2):
        return
    body, omitted = got
    ck('the served text says it is a slice, the way sliced labels one',
       body.startswith('(SLICE'), repr(body[:70]))
    ck('it does NOT re-serve the symbols already shown (negative assertion)',
       not any(f'def {n}(' in body for n in shown if '.' not in n),
       [n for n in shown if f'def {n}(' in body][:4])
    carried = [n for n in names if n not in shown and n not in omitted and n in spans]
    ck('a window that fits serves SOMETHING (not an empty refusal)', len(carried) > 0,
       f'carried none of the {len(names)} names')
    bad = [f'{n}:L{i}' for n in carried
           for i in range(spans[n][0], spans[n][1] + 1)
           if f'{i:5d}  {LINES[i - 1]}' not in body]
    ck('every served symbol is WHOLE: decorator/def line through its last line',
       not bad, f'lines missing from the body: {bad[:4]}')
    ck('the serve fits the room the caller can absorb', len(body) <= room,
       f'{len(body)} chars > room {room}')
    ck('omitted names are real names of this file, not invented ones',
       set(omitted) <= set(names), str([o for o in omitted if o not in set(names)][:4]))
    ck('omitted is disjoint from shown (never tell the model to ask for what it has)',
       not (set(omitted) & set(shown)), sorted(set(omitted) & set(shown))[:4])
    ck('omitted is in file order',
       [spans[n][0] for n in omitted if n in spans]
       == sorted(spans[n][0] for n in omitted if n in spans),
       repr([n for n in omitted[:6]]))


def test_a_window_too_small_serves_no_partial_body_and_omits_every_unshown_name():
    if not hasattr(coder, 'slice_rest') or not hasattr(coder, 'askable'):
        ck('slice_rest and askable exist for the too-small-room case', False, 'missing helper')
        return
    names = list(coder.askable(SRC))
    shown = names[:20]
    body, omitted = coder.slice_rest(SRC, names, shown, 200)
    ck('a room too small for a whole symbol serves NO partial body (never cut a function)',
       'def ' not in body, repr(body[:90]))
    ck('it omits every unshown name, so the refusal has names to speak',
       set(omitted) == set(names) - set(shown),
       f'omitted {len(omitted)} of {len(set(names) - set(shown))} unshown names')
    ck('omitted carries at least 2 DISTINCT real symbol names, not one placeholder repeated',
       len(set(omitted)) >= 2, repr(omitted[:4]))


def test_the_repeat_branch_is_wired_to_the_helpers():
    '''The helpers mean nothing if the repeat branch never calls them (failure-schema:
    check-satisfied-by-dead-path). The refusal is built inside the attempt loop, so the
    wiring is pinned at the refusal text -- the house pattern of test_coder_slice_room.py.'''
    ck('the slice branch keeps its loud refusal wording (t437: the reason reaches the log)',
       REFUSAL in SRC)
    if not hasattr(coder, 'askable') or not hasattr(coder, 'slice_rest'):
        ck('the refusal is built from askable() -- so it names the symbols to ask for', False,
           'no askable in coder.py to build it from')
        ck('the repeat branch calls slice_rest(), not unseen() from line 1', False,
           'no slice_rest in coder.py to call')
        return
    i = SRC.find(REFUSAL)
    ck('the refusal consults askable()',
       i >= 0 and 'askable(' in SRC[max(0, i - 2500):i + 900],
       'no askable( near the no-room refusal -- the model is sent away with nothing to ask for')
    a = SRC.find('if rel in opened:')
    b = SRC.find('repeats.append(rel)')
    ck('the repeat branch is wired to slice_rest (no more whole-file serve from line 1)',
       0 <= a < b and 'slice_rest(' in SRC[a:b],
       f'a={a} b={b}: no slice_rest( inside the repeat branch')


if __name__ == '__main__':
    print('t702: a repeat request for a SYMBOL-SLICED file gets a window that fits, '
          'and the refusal names what to ask for')
    for _n, _f in sorted(list(globals().items())):
        if _n.startswith('test_') and callable(_f):
            _f()
    print(f'\n{len(FAILS)} failure(s)' + (': ' + ', '.join(FAILS) if FAILS else ''))
    sys.exit(1 if FAILS else 0)
