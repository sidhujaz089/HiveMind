#!/usr/bin/env python3
'''t762 -- session_start.py must RECORD every population it names.

RCA 31c25f11372a: a session announced "3 open feedback notes / 11 held briefs", examined
none of them, and nothing on disk paired that session with those 14 items. The start
surface prints a COUNT and a file list (scripts/session_start.py:203, :443, :461, :473)
and writes no record, so "seen, not examined" is a legal, silent end state.

This is the fixture that fails while the writer is absent.

Contract it pins (to be added to scripts/session_start.py):

    record_populations(populations, dest, session, actor, occupancy=None)
        populations : list of (name, [paths as strings])
        appends ONE JSONL row per population to `dest`. Row keys exactly
        {"session","at","population","count","items","actor","occupancy"};
        items are the given paths, in the order given; at is UTC
        "%Y-%m-%dT%H:%M:%SZ"; count == len(items); it never raises.

    collect_populations(root)
        [(name, [root-relative posix paths]), ...] for the four populations the
        script prints: "inbox", "app-feedback", "feedback-notes",
        "access-requests". A population with no members is not returned -- the
        script does not print one, so there is nothing to be accountable for.

Run: python3 scripts/tests/test_start_population.py   (no pytest; exits 1 on a failure)
'''

import json
import sys
import tempfile
import traceback
from pathlib import Path

HERE = Path(__file__).resolve()
SCRIPTS_DIR = HERE.parents[1]          # scripts/  -- where session_start.py lives
REPO_ROOT = HERE.parents[2]            # repo root -- parents[2] from scripts/tests/
for _p in (str(SCRIPTS_DIR), str(REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import session_start                    # noqa: E402

REQUIRED_KEYS = {'session', 'at', 'population', 'count', 'items', 'actor', 'occupancy'}


def _rows(path):
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()
            if line.strip()]


def test_record_writes_one_row_per_population():
    with tempfile.TemporaryDirectory() as td:
        dest = Path(td) / 'session-start-population.jsonl'
        populations = [
            ('feedback-notes', ['workspace/feedback/t700-gap.md', 'workspace/feedback/t701-gap.md']),
            ('inbox', ['workspace/inbox/2026-10-07-chat.md']),
        ]
        session_start.record_populations(populations, dest, session='6ed72651',
                                         actor='claude-code/opus-5', occupancy=41.3)
        rows = _rows(dest)
        assert len(rows) == 2, f'one row per population: expected 2, got {len(rows)}'
        assert set(rows[0]) == REQUIRED_KEYS, \
            f'keys {sorted(rows[0])} are not the required {sorted(REQUIRED_KEYS)}'
        assert rows[0]['population'] == 'feedback-notes', rows[0]['population']
        assert rows[0]['count'] == 2, f'count is the number of items, got {rows[0]["count"]}'
        assert rows[0]['items'] == ['workspace/feedback/t700-gap.md',
                                   'workspace/feedback/t701-gap.md'], rows[0]['items']
        assert rows[1]['population'] == 'inbox', rows[1]['population']
        assert rows[1]['count'] == 1, rows[1]['count']
        assert rows[1]['items'] == ['workspace/inbox/2026-10-07-chat.md'], rows[1]['items']
        assert rows[0]['session'] == '6ed72651', rows[0]['session']
        assert rows[1]['session'] == '6ed72651', rows[1]['session']
        assert rows[0]['actor'] == 'claude-code/opus-5', rows[0]['actor']
        assert rows[0]['occupancy'] == 41.3, rows[0]['occupancy']
        assert str(rows[0]['at']).endswith('Z'), rows[0]['at']


def test_record_appends_and_never_overwrites():
    with tempfile.TemporaryDirectory() as td:
        dest = Path(td) / 'session-start-population.jsonl'
        one = [('inbox', ['workspace/inbox/a.md'])]
        session_start.record_populations(one, dest, session='s1', actor='a')
        session_start.record_populations(one, dest, session='s2', actor='a')
        rows = _rows(dest)
        assert len(rows) == 4, f'the second call must append, not replace: {len(rows)} rows'
        assert [r['session'] for r in rows] == ['s1', 's2', 's1', 's2'], [r['session'] for r in rows]


def test_record_never_raises_on_an_unwritable_destination():
    with tempfile.TemporaryDirectory() as td:
        # A recorder that dies takes session start down with it. A recorder that swallows
        # the failure silently is the defect t762 exists to fix. It must do neither --
        # this asserts it returns; the brief requires the failure to reach stderr.
        session_start.record_populations([('inbox', ['workspace/inbox/a.md'])],
                                         Path(td), session='s', actor='a')


def test_collect_finds_the_populations_the_script_prints():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        fixture = [
            ('workspace/inbox/2026-10-07-chat.md', 'from: chat\n'),
            ('workspace/inbox/2026-10-06-chat.md', 'from: chat\n'),
            ('workspace/inbox/README.md', 'not a message\n'),
            ('workspace/inbox/notes.txt', 'not markdown\n'),
            ('workspace/app-feedback/2026-10-05-crash.md', 'kind: bug\n'),
            ('workspace/app-feedback/README.md', 'not a note\n'),
            ('workspace/feedback/t700-gap.md', 'status: OPEN\n'),
            ('workspace/feedback/t701-gap.md', 'status: OPEN\n'),
            ('workspace/feedback/t699-closed.md', 'status: CLOSED\n'),
            ('workspace/access-requests/req-1.md', 'status: PENDING\n'),
            ('workspace/access-requests/req-2.md', 'status: RESOLVED\n'),
        ]
        for rel, body in fixture:
            f = root / rel
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(body, encoding='utf-8')
        found = {name: items for name, items in session_start.collect_populations(root)}
        assert set(found) == {'inbox', 'app-feedback', 'feedback-notes', 'access-requests'}, \
            sorted(found)
        assert found['inbox'] == ['workspace/inbox/2026-10-06-chat.md',
                                 'workspace/inbox/2026-10-07-chat.md'], found['inbox']
        assert found['app-feedback'] == ['workspace/app-feedback/2026-10-05-crash.md'], \
            found['app-feedback']
        assert found['feedback-notes'] == ['workspace/feedback/t700-gap.md',
                                          'workspace/feedback/t701-gap.md'], found['feedback-notes']
        assert found['access-requests'] == ['workspace/access-requests/req-1.md'], \
            found['access-requests']
        # NEGATIVE assertions: a README, a non-markdown file, and a status that does not
        # match the selector are not members of the population the script announces.
        joined = [i for items in found.values() for i in items]
        assert not any('README.md' in i for i in joined), found
        assert not any(i.endswith('.txt') for i in joined), found
        assert not any('t699-closed' in i for i in joined), found
        assert not any('req-2' in i for i in joined), found


def test_collect_returns_nothing_when_the_directories_are_absent():
    with tempfile.TemporaryDirectory() as td:
        assert list(session_start.collect_populations(Path(td))) == [], \
            'a missing directory is an empty population, not an exception'


def test_main_actually_calls_the_recorder():
    # The recorder is worth nothing if main() never calls it: a function that passes its own
    # suite from a dead path is the defect class this repo calls check-satisfied-by-dead-path.
    src = (SCRIPTS_DIR / 'session_start.py').read_text(encoding='utf-8')
    parts = src.split('def main(', 1)
    assert len(parts) == 2, 'no def main( in scripts/session_start.py'
    assert 'record_populations(' in parts[1], \
        'main() must call record_populations() -- the writer has to be wired to the run'


if __name__ == '__main__':
    _tests = [(n, f) for n, f in sorted(globals().items())
              if n.startswith('test_') and callable(f)]
    _failed = 0
    for _name, _fn in _tests:
        try:
            _fn()
            print(f'PASS {_name}')
        except Exception:
            _failed += 1
            print(f'FAIL {_name}')
            traceback.print_exc()
    print(f'{len(_tests) - _failed}/{len(_tests)} passed')
    sys.exit(1 if _failed else 0)
