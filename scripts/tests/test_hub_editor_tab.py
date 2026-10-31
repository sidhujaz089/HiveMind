#!/usr/bin/env python3
'''t082: the Editor tab in the Hub's shared header, pinned.

Jas, 2026-10-05 (workspace/tasks/t082--journaling-app.md): "I want it to be a new tab in the
hub for my editor tools."

The row is built in scripts/appnav.py: SECTIONS (appnav.py:36) is the single source of truth,
and header() (appnav.py:172) renders one <a> per entry and marks the one whose key equals
`active` with class="on". Today SECTIONS runs apps, status, review, lanes, scripts, misc,
terminal, flow, corpus, models -- there is no editor entry, so header() has no Editor tab to
draw, on any of the apps that share this header.

Everything below is asserted on the rendered HTML -- a tab a person can see and click, and
exactly one tab marked active on a page -- not on how the list is shaped.
'''
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))

FAILS = []


def ck(label, ok):
    print(f"  [{'ok' if ok else 'BAD'}]  {label}")
    if not ok:
        FAILS.append(label)


def _entry():
    import appnav
    return next((e for e in appnav.SECTIONS if e[0] == 'editor'), None)


def test_the_header_renders_an_editor_tab():
    import appnav
    h = appnav.header('editor')
    ck('the bar carries an Editor tab a person can click', '>Editor</a>' in h)
    ck('it links to the hub editor page, built from the real HUB constant',
       f'href="{appnav.HUB}/editor"' in h)
    ck('and it is the active tab on its own page', 'class="on"' in h)
    ck('exactly one tab is marked active', h.count('class="on"') == 1)


def test_the_editor_tab_is_not_active_on_another_page():
    import appnav
    h = appnav.header('apps')
    ck('an Apps page marks Apps active, and only that tab',
       h.count('class="on"') == 1 and '>Apps</a>' in h)
    ck('and the editor tab is drawn there too, so it can be clicked', '>Editor</a>' in h)
    ck('and it is NOT the active one', 'href="%s/editor"><span class="on"' % appnav.HUB not in h)


def test_the_key_exists_so_callers_can_mark_it_active():
    import appnav
    e = _entry()
    ck('the tab has the key callers pass as active', e is not None)
    if e is None:
        return
    ck('its href is the hub editor page', e[1] == f'{appnav.HUB}/editor')
    ck('its label is the name Jas asked for', e[2] == 'Editor')
    ck('it appears once, not twice', sum(1 for x in appnav.SECTIONS if x[0] == 'editor') == 1)


TESTS = [
    test_the_header_renders_an_editor_tab,
    test_the_editor_tab_is_not_active_on_another_page,
    test_the_key_exists_so_callers_can_mark_it_active,
]

if __name__ == '__main__':
    for _t in TESTS:
        try:
            _t()
        except Exception as e:
            ck(f'{_t.__name__} raised {type(e).__name__}: {e}', False)
    print('FAILURES: ' + ('; '.join(FAILS) if FAILS else 'none'))
    sys.exit(1 if FAILS else 0)
