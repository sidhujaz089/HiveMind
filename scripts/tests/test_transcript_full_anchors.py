#!/usr/bin/env python3
'''t793 -- the transcript index carries full, anchored turn text, so a quote re-anchors.

Red on main for exactly one reason: scripts/transcripts.py read_session() cuts every turn at
MAX_CHARS (the slice at line 103; the constant at line 57), emits no full_text / sha256 / turn
fields, and find_quote() does not exist -- so a quote from deep inside a turn is unfindable and
no quote can be given a turn number.
'''
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
import transcripts  # noqa: E402

TAIL = 'the mission, env3, 150 rounds, and permission to patch under the existing gate'
FILLER = 'lorem ipsum dolor sit amet ' * 90
TOOL_RESULT_TEXT = 'tool-result-payload-XYZ-never-indexed'
GREP_QUOTE = 'run the check first, then write the note'
LONG_TEXT = 'Round 4: ' + FILLER + TAIL


def make_rows(tmp, sid='s1'):
    rows = [
        {'type': 'user', 'timestamp': '2026-10-08T01:00:00Z',
         'message': {'content': [{'type': 'text', 'text': 'first turn, user'}]}},
        {'type': 'system', 'timestamp': '2026-10-08T01:00:01Z',
         'message': {'content': [{'type': 'text', 'text': 'queue mechanics row'}]}},
        {'type': 'assistant', 'timestamp': '2026-10-08T01:00:02Z',
         'message': {'content': [{'type': 'text', 'text': LONG_TEXT}]}},
        {'type': 'assistant', 'timestamp': '2026-10-08T01:00:03Z',
         'message': {'content': [{'type': 'tool_use', 'name': 'Bash', 'id': 't1',
                                  'input': {'command': 'grep -F \'' + GREP_QUOTE + '\' x.jsonl'}}]}},
        {'type': 'user', 'timestamp': '2026-10-08T01:00:04Z',
         'message': {'content': [{'type': 'tool_result', 'tool_use_id': 't1',
                                  'content': TOOL_RESULT_TEXT}]}},
    ]
    p = tmp / (sid + '.jsonl')
    p.write_text(''.join(json.dumps(r) + '\n' for r in rows), encoding='utf-8')
    return transcripts.read_session(p, 'fixture')[1]


class FullAnchor(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.rows = make_rows(Path(self._tmp.name))

    def test_long_turn_is_kept_whole(self):
        self.assertGreater(len(LONG_TEXT), transcripts.MAX_CHARS)
        hits = [r for r in self.rows if r.get('n') == 2]
        self.assertEqual(len(hits), 1, 'the long assistant turn is missing: %r'
                         % [(r.get('n'), r.get('role')) for r in self.rows])
        full = hits[0]['full_text']
        self.assertEqual(len(full), len(LONG_TEXT),
                         'full_text was cut: %d of %d chars' % (len(full), len(LONG_TEXT)))
        self.assertTrue(full.endswith(TAIL), 'full_text does not reach the end of the turn')

    def test_display_field_keeps_the_old_behaviour(self):
        r = [x for x in self.rows if x.get('n') == 2][0]
        self.assertEqual(r['text'], r['full_text'][:transcripts.MAX_CHARS])
        self.assertNotIn(TAIL, r['text'])

    def test_sha256_is_of_the_full_text(self):
        for r in self.rows:
            self.assertEqual(r['sha256'],
                             hashlib.sha256(r['full_text'].encode('utf-8')).hexdigest())

    def test_turn_and_line_anchors(self):
        self.assertEqual([(r['turn'], r['n'], r['role']) for r in self.rows],
                         [(0, 0, 'user'), (1, 2, 'assistant'), (2, 3, 'assistant'), (3, 4, 'user')])

    def test_tool_results_stay_dropped(self):
        self.assertEqual(self.rows[2]['full_text'],
                         '[Bash] grep -F \'' + GREP_QUOTE + '\' x.jsonl')
        for r in self.rows:
            self.assertNotIn(TOOL_RESULT_TEXT, r['full_text'])
            self.assertNotIn('queue mechanics row', r['full_text'])

    def test_find_quote_anchors_a_deep_quote(self):
        hits = transcripts.find_quote(TAIL, rows=self.rows)
        self.assertEqual(len(hits), 1, 'expected exactly one anchor, got %r' % (hits,))
        h = hits[0]
        self.assertEqual(h['session'], 's1')
        self.assertEqual(h['turn'], 1)
        self.assertEqual(h['n'], 2)
        self.assertEqual(h['role'], 'assistant')
        self.assertIn('offset', h)
        row = [r for r in self.rows if r['turn'] == h['turn']][0]
        self.assertEqual(row['full_text'][h['offset']:h['offset'] + len(TAIL)], TAIL)

    def test_find_quote_finds_the_tool_command_quote(self):
        self.assertEqual([h['turn'] for h in transcripts.find_quote(GREP_QUOTE, rows=self.rows)], [2])

    def test_find_quote_returns_nothing_for_an_absent_quote(self):
        self.assertEqual(
            transcripts.find_quote('turn 162 of 238, never said in this fixture', rows=self.rows),
            [])

    def test_find_quote_scoped_to_one_session(self):
        self.assertEqual(len(transcripts.find_quote(TAIL, session='s1', rows=self.rows)), 1)
        self.assertEqual(transcripts.find_quote(TAIL, session='other-session', rows=self.rows), [])


if __name__ == '__main__':
    _res = unittest.TextTestRunner(verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(FullAnchor))
    sys.exit(0 if _res.wasSuccessful() else 1)
