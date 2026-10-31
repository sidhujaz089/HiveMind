#!/usr/bin/env python3
'''t1510: the Corpus tab gains a Data pill that renders workspace/data-points.json by device.

Run: python3 scripts/tests/test_corpus_data_pill.py   (pytest is NOT installed)

Red on main for one reason: projects/app-repo/feedback.py has no corpus_data(), and corpus_page's
section list is the four tuples models/scripts/decisions/lessons, so corpus_page('data') coerces
to the Models body and emits no Data pill. The other sections are asserted too: every existing pill
must survive, and the Data pill must not be marked active on a section that is not its own.

NO LIVE STATE: every inventory is a JSON file this test writes under tempfile.mkdtemp(), reached
by pointing the module's WORKSPACE at that directory for the duration of one call. corpus_page is
rendered with the Models body's Ollama probe stubbed, the way scripts/tests/test_corpus_content.py
renders it, so no socket is opened. Nothing outside the temp dirs created here is written.
'''
import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'projects' / 'app-repo'), str(ROOT / 'scripts')]

import feedback  # noqa: E402

DEVICES = ('gx10', 'thinkpad')       # the two devices t1510 names, not words invented here
ACTIVE = '<a class="on" href="/corpus?section=data">Data</a>'
EXISTING = (('models', 'Models'), ('scripts', 'Scripts'),
            ('decisions', 'Decisions'), ('lessons', 'Lessons'))
FAILS = []


def check(cond, msg):
    print('  %s %s' % ('ok  ' if cond else 'FAIL', msg))
    if not cond:
        FAILS.append(msg)


def _page_offline(section):
    '''corpus_page rendered with the Models body's Ollama probe stubbed: no socket is opened.
    Same form as scripts/tests/test_corpus_content.py, which renders this page the same way.'''
    real = feedback._ollama_facts
    feedback._ollama_facts = lambda: {}
    try:
        return feedback.corpus_page(section)
    finally:
        feedback._ollama_facts = real


def _inventory(entries):
    '''A repo root holding workspace/data-points.json; the caller removes the directory.'''
    root = Path(tempfile.mkdtemp())
    (root / 'workspace').mkdir(parents=True, exist_ok=True)
    (root / 'workspace' / 'data-points.json').write_text(
        json.dumps({'generated_at': '2026-10-23T00:00:00Z', 'points': entries}),
        encoding='utf-8')
    return root


def _entries():
    # DISTINCT values in every field of every row: a build that renders one hard-coded row, that
    # renders only paths, or that drops a column, fails the assertions below.
    return [
        {'path': 'workspace/dispatch-loop.jsonl', 'device': 'gx10',
         'writer': 'scripts/dispatch_loop.py', 'what_one_row_records': 'one dispatch-loop tick',
         'cadence': 'every 60s', 'size_bytes': 12345678},
        {'path': '/var/log/ollama.log', 'device': 'thinkpad',
         'writer': 'ollama.service', 'what_one_row_records': 'model load and evict lines',
         'cadence': 'per-event', 'size_bytes': 987},
    ]


def _render(root):
    '''feedback.corpus_data() pointed at a temp repo root, with WORKSPACE restored after.'''
    old = feedback.WORKSPACE
    feedback.WORKSPACE = root
    try:
        return feedback.corpus_data()
    finally:
        feedback.WORKSPACE = old


def test_the_data_pill_is_in_the_nav_and_active_on_its_own_section():
    page = _page_offline('data')
    check(ACTIVE in page,
          'corpus_page("data") emits the Data pill marked active (absent on main: no data section)')
    for key, label in EXISTING:
        check('href="/corpus?section=%s"' % key in page,
              'the %s pill is still offered from the Data page' % label)
    check(bool(page.strip()), 'the Data section is never a nav with an empty body under it')


def test_the_data_pill_is_not_active_on_the_other_sections():
    for section, label in EXISTING:
        page = _page_offline(section)
        check('class="on" href="/corpus?section=data"' not in page,
              'the Data pill is not marked active on the %s section' % label)
        check('class="on" href="/corpus?section=%s"' % section in page,
              'the %s pill is still the active one on its own section' % label)


def test_the_table_names_both_devices_and_what_each_writer_records():
    root = _inventory(_entries())
    try:
        body = _render(root)
        for device in DEVICES:
            check(device in body, 'the table names device %r' % device)
        for value in ('workspace/dispatch-loop.jsonl', '/var/log/ollama.log',
                      'scripts/dispatch_loop.py', 'ollama.service',
                      'one dispatch-loop tick', 'model load and evict lines',
                      'every 60s', 'per-event'):
            check(value in body, 'the table renders %r, not just a path' % value)
        # GROUPED BY DEVICE -- one block per device, not one flat list of paths.
        first, second = body.index('gx10'), body.index('thinkpad')
        check(first < body.index('dispatch-loop.jsonl') < second,
              'the gx10 row sits under the gx10 heading, before the thinkpad heading')
        check(second < body.index('/var/log/ollama.log'),
              'the thinkpad row sits under the thinkpad heading')
        check('12345678' in body and '987' in body,
              'each data point shows its size on disk (12345678 and 987, distinct per row)')
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_a_device_the_generator_could_not_reach_is_loud_not_silent():
    entries = _entries() + [
        {'path': '192.0.2.2:11434/api/tags', 'device': 'thinkpad', 'writer': 'ollama',
         'what_one_row_records': 'models served', 'cadence': 'per-event',
         'status': 'unreachable'}]
    root = _inventory(entries)
    try:
        body = _render(root)
        check('unreachable' in body.lower(),
              'a device the generator could not reach is rendered as unreachable, not dropped')
        check('192.0.2.2:11434/api/tags' in body,
              'the unreachable entry still names its data point')
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_a_missing_inventory_says_so_and_names_the_file():
    root = Path(tempfile.mkdtemp())
    try:
        body = _render(root)
        check('data-points.json' in body,
              'a missing inventory names the file it looked for, it does not render a blank page')
        check('generated' in body.lower(),
              'the message says the inventory has not been generated yet')
        check(bool(body.strip()),
              'the body is never empty -- a header with nothing under it is what this forbids')
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_a_corrupt_inventory_never_crashes_the_page():
    root = Path(tempfile.mkdtemp())
    (root / 'workspace').mkdir(parents=True)
    (root / 'workspace' / 'data-points.json').write_text('{not json', encoding='utf-8')
    try:
        body = _render(root)
        check(bool(body.strip()),
              'a corrupt inventory renders a message, not a crash and not nothing')
    finally:
        shutil.rmtree(root, ignore_errors=True)


if __name__ == '__main__':
    for _name in sorted(globals()):
        _fn = globals()[_name]
        if _name.startswith('test_') and callable(_fn):
            print('== ' + _name)
            try:
                _fn()
            except Exception as _e:
                check(False, '%s raised %s: %s' % (_name, type(_e).__name__, _e))
    print('\n%d failure(s)' % len(FAILS))
    sys.exit(1 if FAILS else 0)
