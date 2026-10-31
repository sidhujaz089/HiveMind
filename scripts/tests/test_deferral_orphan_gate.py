#!/usr/bin/env python3
'''t1237 -- a deferral parked on a SPENT task number is an orphan, and must say so.

WHAT THIS FIXES: scripts/check_declared_intent.py treats a task id appearing in a sentence as
proof that a deferral was given a handle. It never looks the number up, so parking work on a
FINISHED task reads as handled and is never reported -- the artefact in
workspace/rca/d1d56244ee4b.md: the digest go-live was deferred onto t654, t654 was finished at
handover, the session pivoted 84 seconds later, and the decision was never re-raised. Naming a
number is not enough; the number has to be a live one.

WARN, never a blocker: every finding is a printed line and the module keeps returning 0, exactly
as that module's own docstring reasons. A check that fires on ordinary prose gets switched off
within a week, which is why a deferral naming an OPEN number must stay silent.

NO LIVE STATE: every board below is a directory tempfile creates inside this process. Nothing
here reads workspace/tasks/, any transcript, any server, any process list, any model, or any
fixed /tmp path.
'''
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
import check_declared_intent as cdi

DIGEST = "I'll build the digest next session, tracked on t654."
CLOSED = 'done'
OPEN = 'queued'


def board(root, rows):
    '''Write a tasks directory the way the real board is written: tNNN--<title>.md with
    front matter. A status of None writes the file with no status line at all.'''
    d = Path(root)
    d.mkdir(parents=True, exist_ok=True)
    for tid, status in rows.items():
        head = '---\nid: %s\ntitle: an item\narea: workspace\nkind: unfiled\n' % tid
        if status is not None:
            head += 'status: %s\n' % status
        (d / ('%s--an-item.md' % tid)).write_text(head + '---\n\nBody.\n', encoding='utf-8')
    return d


def test_a_deferral_onto_a_finished_task_is_flagged():
    with tempfile.TemporaryDirectory() as tmp:
        b = board(tmp, {'t654': CLOSED})
        hits = cdi.orphan_findings([('assistant', DIGEST)], b)
        assert len(hits) == 1, hits
        assert 't654' in hits[0].lower(), hits[0]
        assert DIGEST in hits[0], hits[0]


def test_a_deferral_onto_an_open_task_is_silent():
    with tempfile.TemporaryDirectory() as tmp:
        b = board(tmp, {'t654': OPEN})
        assert cdi.orphan_findings([('assistant', DIGEST)], b) == [], 'a live handle is not an orphan'


def test_the_closed_set_is_the_board_vocabulary_not_an_invention():
    src = (ROOT / 'projects' / 'app-repo' / 'taskstore.py').read_text(encoding='utf-8')
    m = re.search(r'LEAVES_QUEUE\s*=\s*\(([^)]*)\)', src)
    assert m, 'taskstore.py has no LEAVES_QUEUE tuple: the vocabulary this gate reads has moved'
    words = set(re.findall(r'[\x22\x27](\w+)[\x22\x27]', m.group(1)))
    assert words, 'LEAVES_QUEUE parsed empty: the fixture regex no longer matches taskstore.py'
    assert set(cdi.CLOSED_STATUSES) == words, (cdi.CLOSED_STATUSES, words)


def test_every_board_status_gets_its_disposition():
    rows = {'t101': 'done', 't102': 'cancelled', 't103': 'queued',
            't104': 'doing', 't105': 'blocked', 't106': 'paused'}
    text = ' '.join('I will take %s next session.' % k for k in sorted(rows))
    with tempfile.TemporaryDirectory() as tmp:
        b = board(tmp, rows)
        hits = cdi.orphan_findings([('assistant', text)], b)
        low = ' '.join(hits).lower()
        assert len(hits) == 2, hits
        assert 't101' in low and 't102' in low, hits
        for live in ('t103', 't104', 't105', 't106'):
            assert live not in low, (live, hits)


def test_the_finding_names_the_id_its_status_and_why():
    with tempfile.TemporaryDirectory() as tmp:
        b = board(tmp, {'t654': CLOSED})
        hits = cdi.orphan_findings([('assistant', DIGEST)], b)
        assert len(hits) == 1, hits
        low = hits[0].lower()
        assert 't654' in low, hits[0]
        assert CLOSED in low, hits[0]
        assert 'number' in low, 'a finding must tell the reader what to do: needs a number'


def test_a_finding_carries_the_whole_sentence_it_reports():
    clause = ("I'll build the digest next session, tracked on t654, and every clause of "
              "this intention has to survive into the finding, ")
    text = clause * 4 + 'right down to the very last clause.'
    with tempfile.TemporaryDirectory() as tmp:
        b = board(tmp, {'t654': CLOSED})
        hits = cdi.orphan_findings([('assistant', text)], b)
        assert len(hits) == 1, hits
        assert 'right down to the very last clause.' in hits[0], hits[0]


def test_a_closed_handle_and_an_open_one_in_the_same_turn():
    text = ("I'll build the digest next session, tracked on t700. "
            "I'll verify the Frontline hand-off next session, tracked on t654.")
    with tempfile.TemporaryDirectory() as tmp:
        b = board(tmp, {'t654': CLOSED, 't700': OPEN})
        hits = cdi.orphan_findings([('assistant', text)], b)
        assert len(hits) == 1, 'one finding per spent handle, not per turn and not per turn-text'
        assert 't654' in hits[0].lower(), hits[0]
        assert 't700' not in hits[0].lower(), hits[0]


def test_a_number_that_is_not_on_the_board_is_flagged():
    with tempfile.TemporaryDirectory() as tmp:
        b = board(tmp, {'t100': OPEN})
        text = "I'll wire the digest next session, tracked on t999."
        hits = cdi.orphan_findings([('assistant', text)], b)
        assert len(hits) == 1, hits
        low = hits[0].lower()
        assert 't999' in low and 'not on the board' in low, hits[0]


def test_a_deferral_with_no_number_is_not_this_gates_business():
    with tempfile.TemporaryDirectory() as tmp:
        b = board(tmp, {'t654': CLOSED})
        text = "I'll build the digest next session."
        assert cdi.orphan_findings([('assistant', text)], b) == [], (
            'a deferral naming no number is findings() business, not this gate')


def test_untracked_with_a_reason_is_not_flagged():
    with tempfile.TemporaryDirectory() as tmp:
        b = board(tmp, {'t654': CLOSED})
        text = "I'll build the digest next session, tracked as UNTRACKED: t654 is spent."
        assert cdi.orphan_findings([('assistant', text)], b) == [], (
            'UNTRACKED: with a reason is the escape the deferral contract already grants')


def test_short_numeric_tokens_are_not_task_ids():
    with tempfile.TemporaryDirectory() as tmp:
        b = board(tmp, {'t654': CLOSED})
        text = "I'll build the digest next session on task 12."
        assert cdi.orphan_findings([('assistant', text)], b) == [], (
            'the handle is t plus 3+ digits; 12 is not an id')


def test_user_turns_are_left_to_the_existing_check():
    with tempfile.TemporaryDirectory() as tmp:
        b = board(tmp, {'t654': CLOSED})
        assert cdi.orphan_findings([('user', DIGEST)], b) == []


def test_a_prose_sentence_with_no_deferral_phrasing_is_ignored():
    with tempfile.TemporaryDirectory() as tmp:
        b = board(tmp, {'t654': CLOSED})
        text = 't654 was the session-review grant, and it is finished.'
        assert cdi.orphan_findings([('assistant', text)], b) == [], (
            'no deferral phrasing: this sentence is a report, not a park')


def test_a_board_directory_that_does_not_exist_yields_no_findings():
    with tempfile.TemporaryDirectory() as tmp:
        missing = Path(tmp) / 'no-such-board'
        assert cdi.orphan_findings([('assistant', DIGEST)], missing) == []


def test_an_empty_board_is_not_evidence_that_every_deferral_is_orphaned():
    with tempfile.TemporaryDirectory() as tmp:
        assert cdi.orphan_findings([('assistant', DIGEST)], Path(tmp)) == [], (
            'a board with no task files must not warn about every deferral in the room')


def test_a_task_file_with_no_status_line_is_open():
    with tempfile.TemporaryDirectory() as tmp:
        b = board(tmp, {'t654': None})
        assert cdi.orphan_findings([('assistant', DIGEST)], b) == []


def test_findings_are_plain_non_empty_strings():
    with tempfile.TemporaryDirectory() as tmp:
        b = board(tmp, {'t654': CLOSED, 't655': 'cancelled'})
        text = "I'll build the digest next session, tracked on t654 and t655."
        hits = cdi.orphan_findings([('assistant', text)], b)
        assert len(hits) == 2, hits
        assert all(isinstance(h, str) and h.strip() for h in hits), hits


if __name__ == '__main__':
    names = sorted(n for n in globals() if n.startswith('test_'))
    failures = []
    for name in names:
        try:
            globals()[name]()
        except Exception as exc:
            failures.append('%s: %s: %s' % (name, type(exc).__name__, exc))
    for f in failures:
        print('FAIL ' + f)
    print('%d test(s), %d failure(s)' % (len(names), len(failures)))
    sys.exit(1 if failures else 0)
