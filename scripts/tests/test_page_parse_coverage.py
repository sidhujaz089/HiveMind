#!/usr/bin/env python3
'''Which modules emit a page, and is each one dispositioned?  The population is COMPUTED.

Why this suite exists (workspace/rca/31c1d9195b34.md, section 4, finding D2): on 2026-08-25
chatui.py's page died from a Python escape inside an embedded JS string. Every API endpoint
answered correct JSON (transcript 3164) and the suite sweep reported "all 22 suites pass" at
23:03:38Z (transcript 3220) while the page rendered nothing. The tokeniser written after it,
scripts/jscheck.py, is pointed at ONE module, and scripts/tests/test_chatui_page.py is the only
page-parse suite in the tree. The class is live at every module that emits a <script> block, and
nothing enumerated that population -- so a new page-emitting file is unchecked by default.

This file is that record, built the way docs/skills/planning-for-sustainment demands: enumerate
the approved set and treat anything outside it as a finding. The population is COMPUTED by
parsing every tracked .py with `ast` -- never regex over Python source, which is L-109 and
t147-t149 -- by counting PAGE_MARK inside string constants that are not docstrings. A module
that starts emitting a page enters the population on the commit that adds it, so the allowlist
cannot rot by omission and no library of known-bad names needs a librarian.

The marker is deliberately the WIDE signal. Measured on this tree it finds eight tracked files:
the seven the RCA names, plus scripts/jscheck.py, whose four markers are a regex pattern and
--page CLI help (scripts/jscheck.py:238, :240, :265, :291), not a page body. Narrowing the marker
to '<script>' inside a multi-line string drops projects/app-repo/flow_pages.py, which emits its
JS at line 247 as a one-line string -- a module the RCA names. A wide marker over-includes, and
over-inclusion is one JSON line that dispositions it; a narrow marker silently drops a page.

Tests, all run from __main__ (pytest is not installed; scripts/run_tests.py:512 discovers every
test_*.py in scripts/tests/ and runs it as `python3 <file>`):
  1  the population is computed and non-empty
  2  scripts/page-surfaces.json exists and is readable JSON with a "surfaces" list
  3  every entry is an object naming a module and a status
  4  no module has two entries (a duplicated entry can hide one)
  5  every page-emitting module has an entry                        -- the RCA's (a)
  6  no entry for a file that emits no page (a stale entry is a lie)
  7  every entry's status is "covered" or "pending-scope"
  8  every "covered" entry names a suite under scripts/tests/ that exists, parses,
     and imports jscheck                                            -- the RCA's (b)
  9  every "pending-scope" entry is attributed to a tNNN task id

Excluded from the population: files under scripts/tests/. A suite carries "<script" as a pattern
it searches for, not as a page body, so a suite that checks pages would have to list itself.

Fires by itself, so this fixture is not a document: scripts/session_end.py:487 and :510 run
run_tests.py over every suite in scripts/tests/, and scripts/hooks/pre-push:19 sets
TESTS="$REPO_ROOT/scripts/run_tests.py". A tracked file that cannot be parsed is PRINTED and
counted as emitting no page -- today workspace/run-logs/DRAFT-test_coder_uses_jsonx.py,
SyntaxError at line 1, U+2011 -- so the exception is recorded, never swallowed.

What this file does NOT do, stated here so the next reader sees the boundary: it does not
tokenise the eight modules' JS, and it does not re-run a covered suite. The RCA's (c) -- a suite
that tokenises FEWER THAN ALL of its module's script blocks, the failure that silently rotted
test_chatui_page.py on 2026-09-07 when scripts/appnav.py added a block above chatui's -- needs
each module's page entry point, and six of the eight are not read anywhere in this tree. An
honest gap beats an invented guard.
'''
import ast
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ALLOWLIST = ROOT / 'scripts' / 'page-surfaces.json'
PAGE_MARK = '<script'
EXCLUDE_PREFIXES = ('scripts/tests/',)
STATUSES = ('covered', 'pending-scope')
TASK_ID = re.compile(r't\d+')
PASSED = 0
FAILED = 0


def report(label, got, want):
    global PASSED, FAILED
    if got == want:
        PASSED += 1
        print(f'  [ok]   {label}: {want!r}')
        return True
    FAILED += 1
    print(f'  [BAD]  {label}: expected {want!r}, got {got!r}')
    return False


def tracked_py():
    out = subprocess.run(['git', 'ls-files', '*.py'], cwd=str(ROOT),
                         capture_output=True, text=True, timeout=120)
    if out.returncode != 0:
        print(f'  [BAD]  git ls-files exited {out.returncode}: {out.stderr.strip()!r}')
        return []
    return sorted(p for p in out.stdout.splitlines() if p.strip())


def docstring_ids(tree):
    ids = set()
    nodes = [tree] + [n for n in ast.walk(tree)
                      if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))]
    for node in nodes:
        body = getattr(node, 'body', [])
        first = body[0] if body else None
        value = getattr(first, 'value', None) if isinstance(first, ast.Expr) else None
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            ids.add(id(value))
    return ids


def script_markers(rel):
    """Count PAGE_MARK in non-docstring string constants of one tracked file.

    An unreadable or unparseable file is PRINTED and counted as emitting no page. Silently
    returning 0 would make an unreadable file look like a file with nothing in it.
    """
    path = ROOT / rel
    try:
        src = path.read_text(encoding='utf-8', errors='replace')
    except OSError as e:
        print(f'  [skip] {rel}: unreadable ({type(e).__name__}: {e}) -- counted as no page body')
        return 0
    try:
        tree = ast.parse(src)
    except SyntaxError as e:
        print(f'  [skip] {rel}: not parseable (line {e.lineno}: {e.msg}) -- counted as no page body')
        return 0
    skip = docstring_ids(tree)
    return sum(v.value.count(PAGE_MARK)
               for v in ast.walk(tree)
               if isinstance(v, ast.Constant) and isinstance(v.value, str) and id(v) not in skip)


def population():
    """repo-relative path -> number of PAGE_MARK markers. Computed, never stored."""
    found = {}
    for rel in tracked_py():
        if rel.startswith(EXCLUDE_PREFIXES):
            continue
        n = script_markers(rel)
        if n:
            found[rel] = n
    return found


def imports_jscheck(rel):
    """What makes a suite a PARSE suite: it imports jscheck. ast, not text matching, so a
    mention in a comment does not count and a suite that never imports it cannot pass."""
    path = ROOT / rel
    if not path.is_file():
        return False
    try:
        tree = ast.parse(path.read_text(encoding='utf-8', errors='replace'))
    except (OSError, SyntaxError) as e:
        print(f'  [skip] {rel}: suite unreadable/unparseable ({type(e).__name__}: {e})')
        return False
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(a.name.split('.')[-1] == 'jscheck' for a in node.names):
                return True
        elif isinstance(node, ast.ImportFrom):
            if (node.module or '').split('.')[-1] == 'jscheck':
                return True
            if any(a.name == 'jscheck' for a in node.names):
                return True
    return False


def load_allowlist():
    if not ALLOWLIST.is_file():
        return None, f'{ALLOWLIST.relative_to(ROOT)} does not exist'
    try:
        data = json.loads(ALLOWLIST.read_text(encoding='utf-8'))
    except (OSError, ValueError) as e:
        return None, f'{ALLOWLIST.relative_to(ROOT)} is not readable JSON ({type(e).__name__}: {e})'
    if not isinstance(data, dict) or not isinstance(data.get('surfaces'), list):
        return None, f'{ALLOWLIST.relative_to(ROOT)}: top level must be {{"surfaces": [...]}}'
    return data, ''


def main():
    pop = population()
    print('The population of page-emitting modules, computed with ast over tracked .py files')
    print('(files under scripts/tests/ are excluded: a suite carries the marker as a pattern):')
    for rel in sorted(pop):
        print(f'  {rel}  ({pop[rel]} "{PAGE_MARK}" markers in string constants)')
    report('the population is computed and non-empty', bool(pop), True)

    data, why = load_allowlist()
    if data is None:
        report('scripts/page-surfaces.json exists and is readable JSON', f'missing: {why}', True)
        print(f'  [why]  {why}')
        print('  [next] create scripts/page-surfaces.json with one entry per module listed above,')
        print('         no more and no fewer. Each entry is an object:')
        print('           {"module": "<path exactly as printed above>", "status": "covered",')
        print('            "suite": "scripts/tests/test_chatui_page.py", "followup": ""}')
        print('         Only projects/local-inference/chatui.py may be "covered" today: it is the')
        print('         one module with a parse suite -- scripts/tests/test_chatui_page.py, which')
        print('         imports jscheck. Every other module above is "pending-scope" with')
        print('         "suite": "" and "followup": "t1032" (this task). Do not invent suite')
        print('         paths: a "covered" entry naming a file that is not in scripts/tests/ and')
        print('         importing jscheck is reported. A file that emits no page and is listed')
        print('         is reported too.')
        for label in (
                'every entry is an object naming a module and a status',
                'no module has two allowlist entries',
                'every page-emitting module has an allowlist entry',
                'no allowlist entry for a file that emits no page',
                "every entry's status is covered or pending-scope",
                'every covered entry names a suite under scripts/tests/',
                'every covered entry names an existing jscheck-importing suite',
                'every pending-scope entry is attributed to a tNNN task id'):
            report(label, f'not evaluated: {why}', True)
        print(f'\n{PASSED} passed, {FAILED} failed')
        return 1 if FAILED else 0

    report('scripts/page-surfaces.json exists and is readable JSON', True, True)
    entries = data['surfaces']

    malformed = [repr(e) for e in entries
                 if not isinstance(e, dict) or not e.get('module') or not e.get('status')]
    report('every entry is an object naming a module and a status', sorted(malformed), [])

    counts = {}
    for e in entries:
        if isinstance(e, dict):
            m = str(e.get('module'))
            counts[m] = counts.get(m, 0) + 1
    report('no module has two allowlist entries', sorted(m for m, n in counts.items() if n > 1), [])

    listed = set(counts)
    report('every page-emitting module has an allowlist entry', sorted(set(pop) - listed), [])
    report('no allowlist entry for a file that emits no page', sorted(listed - set(pop)), [])
    report("every entry's status is covered or pending-scope",
           sorted(f"{e.get('module')}={e.get('status')}" for e in entries
                  if isinstance(e, dict) and e.get('status') not in STATUSES), [])

    outside = []
    not_a_parse_suite = []
    for e in entries:
        if isinstance(e, dict) and e.get('status') == 'covered':
            suite = str(e.get('suite') or '')
            if not suite.startswith('scripts/tests/'):
                outside.append(f"{e.get('module')} -> {suite!r}")
            elif not imports_jscheck(suite):
                not_a_parse_suite.append(f"{e.get('module')} -> {suite}")
    report('every covered entry names a suite under scripts/tests/', sorted(outside), [])
    report('every covered entry names an existing jscheck-importing suite',
           sorted(not_a_parse_suite), [])

    unattributed = sorted(str(e.get('module')) for e in entries
                          if isinstance(e, dict) and e.get('status') == 'pending-scope'
                          and not TASK_ID.fullmatch(str(e.get('followup') or '')))
    report('every pending-scope entry is attributed to a tNNN task id', unattributed, [])

    print(f'\n{PASSED} passed, {FAILED} failed')
    return 1 if FAILED else 0


if __name__ == '__main__':
    sys.exit(main())
