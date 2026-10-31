#!/usr/bin/env python3
'''t680: Workspace.read must serve TAIL and LINE-RANGE requests of files over MAX_READ_BYTES.

The defect, read from scripts/workspace_mcp/core.py:
  MAX_READ_BYTES = 1_000_000 (core.py:90) and read(self, path) (core.py:353) takes no
  range parameter. A file over the cap is refused before it is opened: size =
  p.stat().st_size; if size > MAX_READ_BYTES the method logs too-large:{size} and raises
  Denied('too-large') (core.py:390-393). A file within the cap is read whole and hashed
  whole (core.py:394-411) and returns path, bytes, sha256, content. So a 3 MB file cannot
  be read at all -- the report that opened t680 was Chat refused
  workspace/agent-runs.jsonl at 3,014,451 bytes on 2026-10-04.

  read() has no range parameter today, so every ranged call below raises TypeError. That
  is the defect under test, not a bug in this suite: each call is wrapped so a TypeError is
  recorded as a failed assertion naming the parameter that is missing. The suite is
  therefore RED ON MAIN FOR THAT REASON and turns green when read() gains start_line,
  end_line and tail_lines. The no-range assertions pass on main -- they pin that the change
  disturbs nothing. This is a new file rather than an append to
  scripts/tests/test_workspace_mcp.py because that file counts refusals and successful
  reads as running totals over the calls made above them, so a case added earlier breaks
  counts computed later.

Everything runs inside a temp dir from tempfile.mkdtemp(). No subprocess, no network, no
model call, nothing left running, nothing written outside that dir.
'''
import hashlib
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from workspace_mcp.core import Workspace, Denied, MAX_READ_BYTES

PASSED = 0
FAILED = 0
NL = chr(10)

# Distinct, individually identifiable rows: 60000 lines of about 52 bytes is about 3.1 MB,
# more than three times the cap, and no repeated placeholder a stub could hand back for any
# line. Row 3 is not row 4, which is what makes a range assertion mean something.
LINES = ['ROW ' + str(i).rjust(6, '0') + ' ' + 'payload ' * 5 for i in range(1, 60001)]
SMALL = 'one' + NL + 'two' + NL + 'three' + NL
CORPUS = 'corpus line one' + NL + 'corpus line two' + NL


def show(v):
    s = repr(v)
    return s if len(s) <= 160 else s[:160] + '... (' + str(len(s)) + ' chars)'


def check(label, got, want):
    global PASSED, FAILED
    if got == want:
        PASSED += 1
        print('  [ok]   ' + label)
    else:
        FAILED += 1
        print('  [BAD]  ' + label + ': expected ' + show(want) + ', got ' + show(got))


def refused(fn, *a, **k):
    '''The rule name of a refusal; ALLOWED if the call went through; the TypeError text if
    read() has no such parameter (today it has none).'''
    try:
        fn(*a, **k)
        return 'ALLOWED'
    except Denied as e:
        return e.rule
    except TypeError as e:
        return 'TypeError: ' + str(e)


def call_read(ws, rel, **kw):
    '''read() with range parameters, returning its dict. A TypeError (no such parameter
    today) and any refusal are recorded as failures naming the call, so one missing
    parameter does not abort the run and hide the rest of the suite.'''
    try:
        return ws.read(rel, **kw)
    except TypeError as e:
        check('read(' + rel + ', ' + str(kw) + ') is accepted by read()', 'TypeError: ' + str(e), 'no TypeError')
        return None
    except Denied as e:
        check('read(' + rel + ', ' + str(kw) + ') is served, not refused', e.rule, 'no refusal')
        return None


def safe_read(ws, rel, **kw):
    try:
        ws.read(rel, **kw)
    except TypeError as e:
        check('read(' + rel + ', ' + str(kw) + ') is accepted by read()', 'TypeError: ' + str(e), 'no TypeError')


def rows_of(ws):
    return [json.loads(l) for l in ws.log_path.read_text(encoding='utf-8').splitlines() if l.strip()]


def make_ws():
    root = Path(tempfile.mkdtemp())
    (root / 'docs').mkdir()
    (root / 'workspace' / 'private').mkdir(parents=True)
    (root / 'chat' / 'projects').mkdir(parents=True)
    (root / 'docs' / 'big.jsonl').write_text(NL.join(LINES) + NL, encoding='utf-8')
    (root / 'docs' / 'small.txt').write_text(SMALL, encoding='utf-8')
    (root / 'workspace' / 'private' / 'protocol.md').write_text('hidden' + NL, encoding='utf-8')
    (root / 'docs' / 'credentials.json').write_text('{}' + NL, encoding='utf-8')
    (root / 'chat' / 'note.txt').write_text(CORPUS, encoding='utf-8')
    return Workspace(root, caller='test')


print(NL + 'workspace_mcp/core.py -- read(): tails and line ranges over the cap (t680)' + NL)

ws = make_ws()
big = (ws.root / 'docs' / 'big.jsonl').stat().st_size
check('the fixture is bigger than the whole-file cap', big > MAX_READ_BYTES, True)

# 1. tail_lines: the LAST lines, not a head pretending to be a tail.
tail = call_read(ws, 'docs/big.jsonl', tail_lines=5)
if tail is not None:
    body = tail.get('content', '')
    check('tail_lines=5 returns EXACTLY the last 5 lines, in order', body.splitlines(), LINES[-5:])
    check('  a tail that fits says it was not cut', tail.get('truncated'), False)
    check('  bytes is the size of the text returned', tail.get('bytes'), len(body.encode('utf-8')))
    check('  sha256 is the hash of the bytes returned, not of the whole file',
          tail.get('sha256'), hashlib.sha256(body.encode('utf-8')).hexdigest())
    check('  the path is the path requested', tail.get('path'), 'docs/big.jsonl')

# 2. start_line / end_line: 1-based, inclusive at both ends.
sl = call_read(ws, 'docs/big.jsonl', start_line=3, end_line=4)
if sl is not None:
    check('start_line=3, end_line=4 returns rows 3 and 4 (1-based, inclusive)',
          sl.get('content', '').splitlines(), LINES[2:4])
    check('  a range that fits is not marked truncated', sl.get('truncated'), False)
    check('  bytes is the size of the text returned', sl.get('bytes'), len(sl.get('content', '').encode('utf-8')))

# 3. a call with no range keeps the behaviour it has today, cap included.
check('no range: a file over the cap is still refused too-large',
      refused(ws.read, 'docs/big.jsonl'), 'too-large')
plain = ws.read('docs/small.txt')
check('no range: the whole file, byte for byte as today', plain.get('content'), SMALL)
check('no range: the keys a no-range call uses today are all present',
      sorted(k for k in plain if k in ('path', 'bytes', 'sha256', 'content')),
      ['bytes', 'content', 'path', 'sha256'])
check('no range: the hash is of the bytes on disk, not of a slice', plain.get('sha256'),
      hashlib.sha256(SMALL.encode('utf-8')).hexdigest())
check('no range: bytes is the whole file', plain.get('bytes'),
      (ws.root / 'docs' / 'small.txt').stat().st_size)

# 4. the denials are unchanged: resolve() speaks before any byte is opened.
check('a tail request into workspace/private/ is refused',
      refused(ws.read, 'workspace/private/protocol.md', tail_lines=5), 'path-denied')
check('a ranged request into .git/ is refused',
      refused(ws.read, '.git/config', start_line=1, end_line=2), 'path-denied')
check('a ranged request into .venv/ is refused',
      refused(ws.read, '.venv/pyvenv.cfg', tail_lines=1), 'path-denied')
check('a credential name is refused even with a range',
      refused(ws.read, 'docs/credentials.json', tail_lines=2), 'name-denied')
check('a missing path is refused with the rule it has today, not a range rule',
      refused(ws.read, 'docs/nope.md', tail_lines=2), 'no-such-path')

# 5. chat/ stays readable and untouched: read-only is not unreadable.
corpus = call_read(ws, 'chat/note.txt', start_line=2, end_line=2)
if corpus is not None:
    check('chat/ can be READ by range', corpus.get('content', '').splitlines(), ['corpus line two'])
check('a ranged read writes nothing into chat/',
      (ws.root / 'chat' / 'note.txt').read_text(encoding='utf-8'), CORPUS)

# 6. a range asked of a directory is still listed, not refused.
dirs = call_read(ws, 'docs', tail_lines=5)
if dirs is not None:
    check('a range asked of a DIRECTORY is listed, not refused', dirs.get('kind'), 'directory')

# 7. the RETURNED text is capped at MAX_READ_BYTES and says it was cut. The notice is one
# line starting [TRUNCATED, placed before the first kept line.
tr = call_read(ws, 'docs/big.jsonl', start_line=1, end_line=len(LINES))
if tr is not None:
    text = tr.get('content', '')
    kept = [l for l in text.splitlines() if not l.startswith('[TRUNCATED')]
    check('a range whose text exceeds the cap is marked truncated', tr.get('truncated'), True)
    check('  and it is a cut, not the whole range', 0 < len(kept) < len(LINES), True)
    check('  the bytes returned are within the cap', 0 < tr.get('bytes', 0) <= MAX_READ_BYTES, True)
    check('  bytes is the size of the text returned', tr.get('bytes'), len(text.encode('utf-8')))
    check('  the kept lines are the FIRST lines of the requested range, in order', kept, LINES[:len(kept)])
    check('  the cut is ANNOUNCED in the text the caller reads', 'truncated' in text.lower(), True)

# 8. impossible ranges are refused, not silently resolved.
check('start_line together with tail_lines is refused, not silently preferred',
      refused(ws.read, 'docs/small.txt', start_line=1, tail_lines=2), 'bad-range')
check('start_line above end_line is refused',
      refused(ws.read, 'docs/small.txt', start_line=5, end_line=2), 'bad-range')
check('tail_lines 0 is refused', refused(ws.read, 'docs/small.txt', tail_lines=0), 'bad-range')

# 9. every call is logged, with the range REQUESTED.
ws2 = make_ws()
safe_read(ws2, 'docs/small.txt', tail_lines=2)
rows = rows_of(ws2)
check('a tail read is logged as tool read, outcome ok',
      (rows[-1].get('tool'), rows[-1].get('outcome')) if rows else None, ('read', 'ok'))
check('  the logged row records the tail requested (2, not a whole-file count)',
      rows[-1].get('tail_lines') if rows else None, 2)
safe_read(ws2, 'docs/small.txt', start_line=2, end_line=3)
rows = rows_of(ws2)
check('a ranged read records the range requested',
      (rows[-1].get('start_line'), rows[-1].get('end_line')) if rows else None, (2, 3))
safe_read(ws2, 'docs/small.txt')
rows = rows_of(ws2)
check('a no-range read invents no range in its own row',
      (rows[-1].get('start_line'), rows[-1].get('end_line'), rows[-1].get('tail_lines')) if rows else None,
      (None, None, None))
check('one log row per read -- a ranged read does not log twice', len(rows), 3)

print(str(PASSED) + ' passed, ' + str(FAILED) + ' failed')
sys.exit(1 if FAILED else 0)
