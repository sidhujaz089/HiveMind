#!/usr/bin/env python3
"""Oracle for t685 -- the Review tab's app filter pills, and sections that open when one is on.

Jas, workspace/app-feedback/2026-10-05T045710Z--new-idea--the-old-backlog-tab-used-to-have-pills-t.md:
"The old backlog tab used to have pills to filter tasks. I want those back in the review tab.
I want the behaviour to also expand all the sections if a pill is on. I want to be able to see
all review items about a specific app for example."

Red on main for ONE reason: projects/app-repo/feedback.py defines no apply_review_filter, no
review_page_body, and collapsed() takes no open_all keyword. Everything asserted here is a
string transformation on strings passed in -- no live task files, no server, no model, no temp
dirs, no subprocess. The module is loaded by explicit path because scripts/tests/ is not beside it.

Run: python3 scripts/tests/test_review_filter.py   (pytest is not installed.)
"""
import importlib.util
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
FB_DIR = ROOT / "projects" / "app-repo"


def load_feedback():
    """feedback.py by path, under its own module name. Its own folder goes on sys.path first:
    feedback.py does `import taskstore as ts` at module level and taskstore.py lives beside it,
    so a spec_from_file_location load without that dies on that import -- which is a red for a
    reason that is not the change under test.
    """
    if str(FB_DIR) not in sys.path:
        sys.path.insert(0, str(FB_DIR))
    spec = importlib.util.spec_from_file_location("hub_feedback_under_test", FB_DIR / "feedback.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def rows():
    """Five rows, four DISTINCT area values with counts 2/1/1/1 -- the real app names this
    repository uses. Distinct on purpose: a build that renders one placeholder, counts the whole
    store for every pill, or matches the app name as a substring cannot pass the assertions below."""
    return [
        ({"id": "t1", "title": "One", "area": "new-idea", "status": "queued", "kind": "unfiled"}, ""),
        ({"id": "t2", "title": "Two", "area": "app-repo", "status": "queued", "kind": "feature"}, ""),
        ({"id": "t3", "title": "Three", "area": "new-idea", "status": "scoped", "kind": "unfiled"}, ""),
        ({"id": "t4", "title": "Four", "area": "usage-widget", "status": "queued", "kind": "idea"}, ""),
        ({"id": "t5", "title": "Five", "area": "unfiled", "status": "queued", "kind": "chore"}, ""),
    ]


HEAD = ('<h2>Assigned to Jas (1)</h2><div class="card">alpha</div>'
        '<h2>Assigned to Claude (1)</h2><div class="card">beta</div>')
REST = '<h2>Waiting on Claude (1)</h2><div class="card">gamma</div>'


class ApplyReviewFilter(unittest.TestCase):
    """The filter: an app is the task's `area` -- the same key the old backlog pills used and the
    key intake stamps from a note's `app:` front matter."""

    @classmethod
    def setUpClass(cls):
        cls.fb = load_feedback()

    def ids(self, out):
        return [m["id"] for m in out]

    def test_selects_only_the_named_app_in_order(self):
        got = self.fb.apply_review_filter(rows(), "new-idea")
        self.assertEqual(self.ids(got), ["t1", "t3"])

    def test_empty_app_is_everything_unchanged(self):
        self.assertEqual(self.ids(self.fb.apply_review_filter(rows(), "")),
                         ["t1", "t2", "t3", "t4", "t5"])

    def test_unknown_app_matches_nothing(self):
        self.assertEqual(self.fb.apply_review_filter(rows(), "nope"), [])

    def test_match_is_the_whole_field_not_a_prefix(self):
        """A startswith/`in` build returns the near-miss; the request is items *about* the app."""
        near = [{"id": "n1", "title": "Near", "area": "new-idea-x", "status": "queued", "kind": ""}]
        self.assertEqual(self.fb.apply_review_filter(near, "new-idea"), [])

    def test_a_task_with_no_area_falls_back_to_unfiled(self):
        """The backlog pills use m.get("area", "unfiled"); a task file with no area field must
        not vanish from the filtered view and must be reachable under 'unfiled'."""
        bare = [{"id": "b1", "title": "No area", "status": "queued", "kind": ""}]
        self.assertEqual(self.ids(self.fb.apply_review_filter(bare, "unfiled")), ["b1"])


class ReviewPageBody(unittest.TestCase):
    """One function owns both halves of the request: the pill bar and the expansion. Splitting
    them is what makes 'expand all the sections if a pill is on' untestable."""

    @classmethod
    def setUpClass(cls):
        cls.fb = load_feedback()

    def test_the_pill_bar_is_rendered_in_the_backlog_style(self):
        html = self.fb.review_page_body(rows(), HEAD, REST, "")
        self.assertEqual(html.count('<nav class="pills">'), 1,
                         "the Review tab's filter bar is the backlog tab's nav.pills, once")

    def test_every_app_is_a_pill_with_its_own_count(self):
        html = self.fb.review_page_body(rows(), HEAD, REST, "")
        for want in ("new-idea (2)", "app-repo (1)", "usage-widget (1)", "unfiled (1)"):
            self.assertIn(want, html, "one pill per app, counted separately from the real data")

    def test_all_pill_shows_the_total_and_clears_the_filter(self):
        html = self.fb.review_page_body(rows(), HEAD, REST, "")
        self.assertIn(">All (5)<", html)
        self.assertIn('href="/review"', html, 'with the closing quote: the All pill links to the '
                                              'bare tab, not to a filter')

    def test_the_active_app_is_the_only_pill_marked_on(self):
        html = self.fb.review_page_body(rows(), HEAD, REST, "new-idea")
        self.assertIn('href="/review?app=new-idea"', html)
        self.assertEqual(html.count('class="on"'), 1,
                         "exactly one pill carries the on class -- marking all of them passes nothing")
        self.assertIn('class="on"', html.split('href="/review?app=new-idea"')[0][-60:])

    def test_no_pill_is_marked_on_when_the_filter_is_off(self):
        html = self.fb.review_page_body(rows(), HEAD, REST, "")
        self.assertNotIn('class="on"', html)

    def test_an_inactive_app_pill_is_not_marked_on(self):
        html = self.fb.review_page_body(rows(), HEAD, REST, "app-repo")
        self.assertIn('href="/review?app=app-repo"', html)
        self.assertNotIn('class="on"', html.split('href="/review?app=new-idea"')[0][-60:])

    def test_the_pill_bar_sits_above_the_sections(self):
        html = self.fb.review_page_body(rows(), HEAD, REST, "new-idea")
        self.assertLess(html.index('<nav class="pills">'), html.index("<h2>"))

    def test_the_pills_count_the_rows_handed_in_not_the_whole_store(self):
        html = self.fb.review_page_body(rows()[1:2], HEAD, REST, "")
        self.assertIn("app-repo (1)", html)
        self.assertNotIn("new-idea", html, "a pill for an app with no row in this list is a lie")

    def test_sections_are_expanded_when_a_pill_is_on(self):
        """The clause a test of collapsed() alone cannot reach: the pill being on CAUSES the open."""
        html = self.fb.review_page_body(rows(), HEAD, REST, "new-idea")
        self.assertEqual(html.count('<details class="sec" open>'), 2,
                         "one open section per <h2> in head, passed through collapsed()")

    def test_sections_are_NOT_expanded_when_no_pill_is_on(self):
        html = self.fb.review_page_body(rows(), HEAD, REST, "")
        self.assertNotIn('<details class="sec" open>', html)
        self.assertIn('<details class="sec">', html)

    def test_app_name_with_a_space_is_url_quoted_in_the_href(self):
        spacey = [{"id": "s1", "title": "S", "area": "big project", "status": "queued", "kind": ""}]
        html = self.fb.review_page_body(spacey, HEAD, REST, "big project")
        self.assertIn('href="/review?app=big%20project"', html)

    def test_app_name_with_an_ampersand_is_escaped_in_the_label(self):
        amp = [{"id": "a1", "title": "A", "area": "a & b", "status": "queued", "kind": ""}]
        html = self.fb.review_page_body(amp, HEAD, REST, "a & b")
        self.assertIn(">a &amp; b (1)<", html)


class CollapsedOpenAll(unittest.TestCase):
    """collapsed() serves every tab (Scripts, Board, Production). Its default output must not move."""

    @classmethod
    def setUpClass(cls):
        cls.fb = load_feedback()

    def test_open_all_emits_open_details_for_every_section(self):
        html = self.fb.collapsed(HEAD, open_all=True)
        self.assertEqual(html.count('<details class="sec" open>'), 2)
        self.assertNotIn('<details class="sec">', html,
                         "emitting both forms leaves sections that never open")

    def test_the_default_is_byte_identical_to_today_for_every_other_tab(self):
        plain = self.fb.collapsed(HEAD)
        self.assertIn('<details class="sec">', plain)
        self.assertNotIn('<details class="sec" open>', plain)
        self.assertEqual(self.fb.collapsed(HEAD, open_all=False), plain)


if __name__ == "__main__":
    unittest.main(verbosity=2)
