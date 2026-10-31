#!/usr/bin/env python3
# t1079 -- the DSH / Console 1-to-1 feature map must exist, every citation in it must resolve to a
# real file at a real line, every Console symbol it names must exist in console.py, its gaps must be
# stated, and its migration plan must be phased, shippable and sustained.
#
# RED ON MAIN FOR ONE REASON: workspace/research/dsh-console-map.md does not exist.
# Written by the Manager and frozen at commit (D-35): the implementation dispatch may not edit it.
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOC = ROOT / 'workspace' / 'research' / 'dsh-console-map.md'
CONSOLE = ROOT / 'projects' / 'local-inference' / 'console.py'

REPO_PREFIXES = ('projects/', 'scripts/', 'docs/', 'workspace/', 'library/')
CITATION = re.compile(r'([A-Za-z0-9_./-]+\.(?:py|md|json|jsonl|ts|js|sh|html)):(\d+)')
BACKTICK = re.compile(r'`([A-Za-z_][A-Za-z0-9_]{1,63})`')
PLAN_HEAD = re.compile(r'(?im)^##[^\n]*migration[^\n]*plan[^\n]*$')
PHASE_HEAD = re.compile(r'(?im)^###[^\n]*phase[^\n]*$')
DATE_LINE = re.compile(r'(?i)\*\*last updated:\*\*\s*\d{4}-\d{2}-\d{2}')
BANNED = ('unverified', 'confirm before', 'placeholder', 'lorem', 'to be filled', 'fill in', 'tbd')
MIN_ROWS = 8
MIN_ECHOS = 6
MIN_DOC_CHARS = 2000

_console_src = None
_line_counts = {}


def console_src():
    global _console_src
    if _console_src is None:
        _console_src = ''
        if CONSOLE.exists():
            _console_src = CONSOLE.read_text(encoding='utf-8', errors='replace')
    return _console_src


def line_count(rel):
    if rel not in _line_counts:
        _line_counts[rel] = len((ROOT / rel).read_text(encoding='utf-8', errors='replace').splitlines())
    return _line_counts[rel]


def read_doc():
    return DOC.read_text(encoding='utf-8', errors='replace')


def table_rows():
    rows = []
    for line in read_doc().splitlines():
        s = line.strip()
        if not s.startswith('|'):
            continue
        cells = [c.strip() for c in s.strip('|').split('|')]
        if all(set(c) <= set('-: ') for c in cells):
            continue
        rows.append(cells)
    return rows


def layout():
    rows = table_rows()
    low = [c.lower() for c in rows[0]] if rows else []

    def idx(needle, default):
        for i, h in enumerate(low):
            if needle in h:
                return i
        return default

    cols = {'feature': idx('feature', 0), 'lives': idx('lives', 2),
            'counterpart': idx('counterpart', 3), 'plugin': idx('cordis', 4)}
    return rows[1:], cols


def citation_problems(cells, label, lives_idx, is_console, is_dsh):
    out = []
    lives = cells[lives_idx] if len(cells) > lives_idx else ''
    hits = CITATION.findall(lives)
    if not hits:
        out.append('%s: the Where-it-lives cell carries no path:line citation at all: %r' % (label, lives[:160]))
    if is_console and not any(p.startswith('projects/local-inference/') for p, _ in hits):
        out.append('%s: a Console row must cite the Console source under projects/local-inference/; it cites %r' % (label, [p for p, _ in hits]))
    if is_dsh:
        local = [p for p, _ in hits if p.startswith('workspace/research/')]
        if not local and 'deepseek-harness' not in lives:
            out.append('%s: a DSH row must cite a file under workspace/research/ or name the checkout at deepseek-harness; it cites %r' % (label, [p for p, _ in hits]))
    for path_s, line_s in hits:
        if not path_s.startswith(REPO_PREFIXES):
            continue
        if not (ROOT / path_s).exists():
            out.append('%s: cites %s, which does not exist in this repository' % (label, path_s))
            continue
        n = line_count(path_s)
        if int(line_s) > n:
            out.append('%s: cites %s:%s, but %s has only %d lines' % (label, path_s, line_s, path_s, n))
    return out


def test_map_file_exists_and_is_dated():
    text = read_doc()
    out = []
    if len(text) < MIN_DOC_CHARS:
        out.append('the map is %d characters; a row-by-row map of two systems cannot be that short' % len(text))
    if not DATE_LINE.search(text):
        out.append('no Last-updated line: a standing document with no date is a standing document that rots')
    return out


def test_table_has_the_five_columns_the_task_asked_for():
    rows = table_rows()
    if not rows:
        return ['no markdown table: a 1-to-1 map is a table, one row per feature of either system']
    low = [c.lower() for c in rows[0]]
    out = []
    for want in ('feature', 'what it does', 'where it lives', 'counterpart', 'cordis'):
        if not any(want in h for h in low):
            out.append('header row has no column matching %r; the header reads %r' % (want, rows[0]))
    return out


def test_both_systems_are_mapped_and_every_row_is_sourced():
    data, cols = layout()
    out = []

    def feat(cells):
        return cells[cols['feature']] if len(cells) > cols['feature'] else ''

    console_rows = [c for c in data if feat(c).lower().startswith('console')]
    dsh_rows = [c for c in data if feat(c).lower().startswith('dsh')]
    if len(console_rows) < MIN_ROWS:
        out.append('only %d rows whose Feature cell starts Console (the map needs %d): the Console half is a mention, not a map' % (len(console_rows), MIN_ROWS))
    if len(dsh_rows) < MIN_ROWS:
        out.append('only %d rows whose Feature cell starts DSH (the map needs %d): the DSH half is a mention, not a map' % (len(dsh_rows), MIN_ROWS))
    for cells in console_rows:
        out.extend(citation_problems(cells, 'Console row %r' % feat(cells)[:60], cols['lives'], True, False))
    for cells in dsh_rows:
        out.extend(citation_problems(cells, 'DSH row %r' % feat(cells)[:60], cols['lives'], False, True))
    return out


def test_console_rows_name_symbols_that_exist():
    src = console_src()
    if not src:
        return ['projects/local-inference/console.py is missing: the Console half of the map cannot be checked against it']
    data, cols = layout()
    out = []
    for cells in data:
        if len(cells) <= cols['feature'] or not cells[cols['feature']].lower().startswith('console'):
            continue
        row = ' '.join(cells)
        for name in sorted(set(BACKTICK.findall(row))):
            if name in ('none', 'path', 'line'):
                continue
            if name not in src:
                out.append('Console row %r names %s, which never appears in console.py: grep a symbol before you name it' % (cells[cols['feature']][:60], '`' + name + '`'))
    return out


def test_the_map_admits_the_gaps_it_cannot_fill():
    data, cols = layout()
    hits = [c for c in data if len(c) > cols['counterpart'] and c[cols['counterpart']].lower().startswith('none')]
    if not hits:
        return ['no row carries None in the Counterpart column: the two systems are not 1-to-1, so a map with no gaps is a fiction']
    return []


def test_every_cordis_plugin_cell_is_a_real_proposal():
    data, cols = layout()
    out = []
    for cells in data:
        if len(cells) <= cols['plugin']:
            out.append('row %r has fewer cells than the header: %r' % (cells[:1], cells))
            continue
        cell = cells[cols['plugin']]
        low = cell.lower()
        if len(cell) < 12:
            out.append('row %r: the Cordis plugin cell is %r; every row needs a real proposal of at least 12 characters' % (cells[cols['feature']][:60], cell))
            continue
        if low.startswith('none') or low.startswith('n/a') or low.startswith('-'):
            out.append('row %r: the Cordis plugin cell is a stub, not a proposal: %r' % (cells[cols['feature']][:60], cell))
        for bad in BANNED:
            if bad in low:
                out.append('row %r: the Cordis plugin cell contains %r; a proposal the author has not confirmed is not a proposal' % (cells[cols['feature']][:60], bad))
    return out


def test_plan_is_phased_shippable_and_sustained():
    text = read_doc()
    m = PLAN_HEAD.search(text)
    if not m:
        return ['no Migration Plan heading: STEP 2 of t1079 (the phased rebuild plan) is absent']
    plan = text[m.end():]
    out = []
    heads = list(PHASE_HEAD.finditer(plan))
    if len(heads) < 3:
        out.append('the plan has %d Phase sections; a phased plan needs at least three' % len(heads))
    for i, ph in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else len(plan)
        body = plan[ph.end():end]
        label = ph.group(0).strip()
        if not re.search(r'(?i)\bships\b', body):
            out.append('%s: says nothing about what ships: every phase must be shippable on its own' % label)
        if not re.search(r'(?i)fixture|guard|test|oracle', body):
            out.append('%s: names no mechanical fixture: a plan step with no fixture is a step that rots' % label)
    return out


def test_the_plan_names_the_features_it_moves():
    text = read_doc()
    m = PLAN_HEAD.search(text)
    plan = text[m.end():].lower() if m else ''
    data, cols = layout()
    names = []
    for cells in data:
        if len(cells) > cols['feature'] and cells[cols['feature']].lower().startswith('console'):
            name = cells[cols['feature']].split(':', 1)[-1].strip().lower()
            if name:
                names.append(name)
    echoed = sorted({n for n in names if n in plan})
    if len(echoed) < MIN_ECHOS:
        return ['the plan names %d of the Console features listed in the table (%s); it must name at least %d of them, spelled as the table spells them, so the plan is anchored to the map' % (len(echoed), ', '.join(echoed[:8]) if echoed else 'none', MIN_ECHOS)]
    return []


TESTS = [
    test_map_file_exists_and_is_dated,
    test_table_has_the_five_columns_the_task_asked_for,
    test_both_systems_are_mapped_and_every_row_is_sourced,
    test_console_rows_name_symbols_that_exist,
    test_the_map_admits_the_gaps_it_cannot_fill,
    test_every_cordis_plugin_cell_is_a_real_proposal,
    test_plan_is_phased_shippable_and_sustained,
    test_the_plan_names_the_features_it_moves,
]


def main():
    if not DOC.exists():
        raise AssertionError('the map does not exist: workspace/research/dsh-console-map.md is missing, so STEP 1 of t1079 has never been written down')
    failures = []
    for fn in TESTS:
        try:
            problems = fn()
        except Exception as exc:
            problems = ['%s raised %s: %s' % (fn.__name__, type(exc).__name__, exc)]
        for p in problems:
            failures.append('%s: %s' % (fn.__name__, p))
    if failures:
        print('FAIL (%d finding(s)) in workspace/research/dsh-console-map.md:' % len(failures))
        for f in failures:
            print('  - ' + f)
    assert not failures, 'the DSH / Console map is defective: %s' % json.dumps(failures)
    print('OK: the DSH / Console map exists, its citations resolve, its Console symbols are real, its gaps are stated, and its plan is phased, shippable and sustained')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
