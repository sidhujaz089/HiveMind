#!/usr/bin/env python3
"""t124 oracle: the how-I-tested doc must exist as a browser page.

Jas's note 2026-08-24 (task t124): he cannot open the file's location and
wants a clickable, browser-viewable page on a laptop and phone. The canonical
text stays in HOW-I-TESTED-THE-MODELS.md; this oracle pins the HTML render:
every step command pasteable, the lie-catching steps still marked, the tables
and their numbers unaltered, and no network dependencies.

Stdlib only. Reads under the repository, writes nothing, touches no live
state (no model, no network, no serving process).
"""
import unittest
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOC_DIR = ROOT / "projects" / "local-inference" / "docs"
MD = DOC_DIR / "HOW-I-TESTED-THE-MODELS.md"
HTML = DOC_DIR / "HOW-I-TESTED-THE-MODELS.html"

# The pasteable commands the doc gives as steps (md lines 17, 33, 50, 94-95, 125).
COMMANDS = [
    "python3 projects/local-inference/fits.py",
    "python3 projects/local-inference/serve.py status",
    "python3 projects/local-inference/serve.py bench",
    "python3 projects/local-inference/bench.py run",
    "python3 projects/local-inference/bench.py compare",
    "python3 projects/local-inference/context.py",
]

# Ceiling table, verbatim from md lines 69-74. Any change here is a new lie.
CEILING_NUMBERS = ["163.8", "4.1", "26.9", "14.2", "82%", "31%"]

# Thinking-budget table, verbatim from md lines 112-115.
THINKING_NUMBERS = ["3,000", "108s", "44", "1.7s"]


class Extract(HTMLParser):
    def __init__(self):
        super().__init__()
        self.text = []
        self.hrefs = []
        self.tables = 0
        self.lie_sections = 0

    def handle_starttag(self, tag, attrs):
        d = dict(attrs)
        if tag == "table":
            self.tables += 1
        if tag == "section" and d.get("data-lie") == "true":
            self.lie_sections += 1
        if tag == "a" and "href" in d:
            self.hrefs.append(d["href"])

    def handle_data(self, data):
        self.text.append(data)


class TestHowITestedHtml(unittest.TestCase):
    def setUp(self):
        self.assertTrue(MD.is_file(), "source markdown missing -- the doc is gone")
        self.assertTrue(HTML.is_file(), "the browser page does not exist yet")
        self.html = HTML.read_text(encoding="utf-8")
        p = Extract()
        p.feed(self.html)
        p.close()
        self.extract = p
        self.text = "".join(p.text)

    def test_every_step_command_is_pasteable(self):
        for cmd in COMMANDS:
            self.assertIn(cmd, self.text, "missing pasteable command: %s" % cmd)

    def test_numbers_are_verbatim(self):
        for n in CEILING_NUMBERS:
            self.assertIn(n, self.text, "ceiling-table number missing or altered: %s" % n)
        for n in THINKING_NUMBERS:
            self.assertIn(n, self.text, "thinking-table number missing or altered: %s" % n)

    def test_lie_catching_steps_stay_marked(self):
        self.assertGreaterEqual(
            self.extract.lie_sections, 2,
            "expected >=2 <section data-lie=true> (the ceiling step and the gate step)",
        )

    def test_tables_are_rendered(self):
        self.assertGreaterEqual(
            self.extract.tables, 2,
            "expected >=2 <table> elements (ceiling table, thinking table)",
        )

    def test_page_is_self_contained(self):
        for href in self.extract.hrefs:
            self.assertNotIn("://", href, "network link on a self-contained page: %s" % href)

    def test_link_back_to_canonical_markdown(self):
        self.assertTrue(
            any(h == "HOW-I-TESTED-THE-MODELS.md" or h.endswith("/HOW-I-TESTED-THE-MODELS.md")
                for h in self.extract.hrefs),
            "no link back to the canonical .md source",
        )

    def test_file_links_resolve(self):
        for href in self.extract.hrefs:
            if href.startswith("#") or "://" in href:
                continue
            self.assertTrue((DOC_DIR / href).is_file(), "broken file link: %s" % href)

    def test_no_raw_markdown_left_behind(self):
        self.assertNotIn("```", self.html, "raw code fences left in the HTML")
        self.assertNotIn("|---", self.html, "raw markdown table separators left in the HTML")


if __name__ == "__main__":
    unittest.main(verbosity=2)
