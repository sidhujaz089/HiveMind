#!/usr/bin/env python3
"""t646: the Lanes tab (feedback.lanes_page) must show each fact once, and nothing on it
must collapse on the 15 s reload.

Pinned by source, not by a live lanes_page() call: the function imports lane_status and
shells out to scripts/lane_status.py, and oracles must not touch live state or a model.
The things asserted below are static HTML literals in the function's source, so for them
the source IS the rendered output. Same style test_hub_layout.py already uses.

Run: python3 scripts/tests/test_lanes_tab.py   (no pytest; exits non-zero on failure)
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def main():
    fails = []

    def ck(label, cond):
        if not cond:
            fails.append(label)

    src = (ROOT / "projects" / "app-repo" / "feedback.py").read_text(encoding="utf-8")

    # Slice only lanes_page()'s body: other tabs legitimately use <details> (the Corpus
    # tab's entry cards), so a whole-file "not in" would be red forever for the wrong
    # reason. The next top-level def after lanes_page() is _corpus_entries, so \n\ndef
    # lands there.
    s = src.find("def lanes_page():")
    e = src.find("\n\ndef ", s + 1) if s >= 0 else -1
    body = src[s:e] if (s >= 0 and e > s) else ""

    ck("lanes_page() is locatable in feedback.py", s >= 0 and e > s)
    ck("Lanes tab: no collapsible <details> block (the 15 s reload re-closes it)",
       e > s and "<details" not in body)
    ck("Lanes tab: the duplicated lane_status.py text report is gone",
       e > s and "The full text report (lane_status.py)" not in body)
    ck("Lanes tab: the docstring no longer promises the text report",
       e > s and "text report underneath" not in body)
    ck("Lanes tab: the dead report-only import is gone",
       e > s and "import subprocess as _sp" not in body)
    ck("Lanes tab: the sweep line is still shown (the report's only fact no table had, "
       "now a section of its own)",
       e > s and '<h3>Test sweep</h3>' in body)
    # Jas, 2026-10-03: no auto-reload -- "a manual reload button ... or it pulls things when I get there".
    ck("Lanes tab: no auto-reload; a Reload button instead",
       e > s and "setInterval" not in body and "location.reload()" in body)

    for f in fails:
        print("FAIL:", f)
    if not fails:
        print("ok: lanes tab shows each fact once, nothing collapses on refresh")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
