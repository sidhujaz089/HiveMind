#!/usr/bin/env python3
'''t7890 -- the skill draft review is a TASK on the role's list, not a rule about who wrote the draft.

Jas, 2026-10-26: "I don't care for the rule that a model can't approve its own draft. It is stale...
Really the draft review should be a task and that task is assigned at the role level on the role tab.
In the future I can remove the review task from a role and keep it on others."

skill_change.py already LOOKS UP the holder roles (1b1da7cc9, t8715: SKILL_REVIEW_TASK, review_holders,
review_from), but it kept a fallback that routes by WHO PROPOSED the draft: while no role lists the
task, every non-Manager proposer is sent to a role named by a string comparison. That residual
comparison is the stale rule, and it is what this suite turns red.

RED ON MAIN today, for the missing change, at two cases:
  test_a_role_that_does_not_hold_the_review_task_gets_no_manager_review
      review_from('dsh_harness') on a store where no role lists the task returns 'manager'; it must return ''.
  test_the_routing_source_carries_no_identity_comparison
      inspect.getsource(review_from) contains (proposer or '').lower() == 'manager' (skill_change.py line 212)
      and inspect.getsource(_route) contains proposer != 'manager' (line 263).

The other six cases pass today and are the anti-vacuity guards: a build that answers '' for everyone
fails test_a_role_that_holds_the_task_reviews_its_own_draft and test_a_non_holder_is_routed_to_the_first_holder;
a build that answers a fixed role for everyone fails those two and test_an_unreadable_role_store_is_said_and_not_silently_empty;
a build that deletes the look-up fails test_the_holders_are_the_roles_that_list_the_task.

No live state is read: role_store.load is replaced for the duration of every case, so the role store
file on disk, the task store, every model, subprocess, socket and fixed /tmp path stay untouched.
'''
import inspect
import io
import sys
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
for _p in (str(ROOT), str(ROOT / 'scripts')):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import role_store    # noqa: E402
import skill_change  # noqa: E402


def store(**by_role):
    '''A role store in the shape role_store.load() returns, read off the shape review_holders reads:
    a dict with a roles key, each role a dict with tasks, a list of dicts carrying the key task.'''
    return {'roles': {key: {'name': key, 'kind': 'model', 'what': 'fixture',
                            'tasks': [{'task': t, 'interrupt': False} for t in tasks]}
                      for key, tasks in by_role.items()}}


class TestSkillReviewIsARoleTask(unittest.TestCase):

    def test_the_constant_names_the_task_the_roles_tab_lists(self):
        # The routing key is the task id a role's list carries; pin it so the look-up and the Roles tab
        # cannot drift apart, and so a fixture built on a literal cannot silently stop matching.
        self.assertEqual('skill_review', skill_change.SKILL_REVIEW_TASK)

    def test_a_role_that_holds_the_task_reviews_its_own_draft(self):
        # The whole point of t7890: holding the task is the permission; having written the draft is not a bar.
        with mock.patch.object(role_store, 'load',
                               return_value=store(swift=[skill_change.SKILL_REVIEW_TASK])):
            self.assertEqual('', skill_change.review_from('swift'))

    def test_a_non_holder_is_routed_to_the_first_holder(self):
        with mock.patch.object(role_store, 'load',
                               return_value=store(swift=[skill_change.SKILL_REVIEW_TASK],
                                                  zeta=[skill_change.SKILL_REVIEW_TASK])):
            self.assertEqual('swift', skill_change.review_from('dsh_harness'))

    def test_the_holders_are_the_roles_that_list_the_task(self):
        with mock.patch.object(role_store, 'load',
                               return_value=store(swift=['merge_review'],
                                                  zeta=[skill_change.SKILL_REVIEW_TASK],
                                                  beta=[skill_change.SKILL_REVIEW_TASK])):
            self.assertEqual(['beta', 'zeta'], skill_change.review_holders())

    def test_a_role_that_does_not_hold_the_review_task_gets_no_manager_review(self):
        # RED ON MAIN: the proposer-identity fallback answers 'manager' here.
        with mock.patch.object(role_store, 'load', return_value=store(dsh_harness=['merge_review'])):
            self.assertEqual('', skill_change.review_from('dsh_harness'))

    def test_no_role_holding_the_task_means_no_intermediate_review_for_anyone(self):
        # No identity special case: the drafter's own draft behaves like every other draft.
        with mock.patch.object(role_store, 'load', return_value=store(manager=['merge_review'])):
            self.assertEqual('', skill_change.review_from('manager'))
        with mock.patch.object(role_store, 'load', return_value=store(swift=['merge_review'])):
            self.assertEqual('', skill_change.review_from('manager'))

    def test_the_routing_source_carries_no_identity_comparison(self):
        # The task file names this check: 'no branch on who wrote it anywhere in skill_change.py --
        # grep for the proposer comparison is the regression test.' Scoped to the routing: _assignee
        # maps a REJECTED draft back to the lane that wrote it, which is not a review permission, and
        # _route's back_to / who= prose keep naming the proposer for the same reason.
        QUOTES = ['"', "'"]
        for fn in (skill_change.review_from, skill_change._route):
            src = inspect.getsource(fn)
            for op in ('==', '!='):
                for q in QUOTES:
                    self.assertNotIn(f'{op} {q}manager{q}', src,
                                     f'{fn.__name__} still compares the proposer to a role literal')

    def test_an_unreadable_role_store_is_said_and_not_silently_empty(self):
        # t8715's rule survives the fallback removal: 'could not read' and 'nobody reviews' are two facts.
        err = io.StringIO()
        with mock.patch.object(role_store, 'load', side_effect=OSError('the store file is not there')):
            with redirect_stderr(err):
                self.assertEqual([], skill_change.review_holders())
        self.assertIn('role store', err.getvalue())


if __name__ == '__main__':
    unittest.main(verbosity=2)
