#!/usr/bin/env python3
"""The Hub shows the lanes board, read live, and does not turn an absent board into a green light.

Jas, 2026-10-28: "I want visibility of the board in the Hub somewhere." The Lanes tab renders it from
the same store the lanes append to. The failure this pins is the one this repository keeps repeating: a
missing record rendering as "nothing is wrong".
"""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "projects" / "app-repo"))


def board(tmp, rows):
    p = Path(tmp) / "board.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    return p


def main():
    import lanes
    import feedback as F
    fails = []

    def check(label, ok, detail=""):
        print(f"  [{'ok' if ok else 'BAD'}]  {label}" + ("" if ok else f"\n        {detail}"))
        if not ok:
            fails.append(label)

    tmp = tempfile.mkdtemp()
    q = {"kind": "ask", "at": "2026-10-28T06:00:00Z", "lane": "L2", "to": "L1", "qid": "abcdef",
         "text": "please sign the branch"}
    # RELATIVE, NEVER A FIXED DATE: this fixture claimed until 10:00Z, and at 10:00 the claim expired and
    # the suite went red on its own -- a time bomb in a test about "a held claim is shown".
    import datetime
    now = datetime.datetime.now(datetime.timezone.utc)
    c = {"kind": "claim", "at": (now - datetime.timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ"),
         "lane": "L2", "what": "scripts/role_gate.py", "why": "test",
         "until": (now + datetime.timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")}
    start = {"kind": "post", "at": "2026-10-28T06:02:00Z", "lane": "L3", "text": "MERGE START x by L3"}
    lanes.BOARD = board(tmp, [q, c, start])
    html = F._board_section()
    check("an open question is counted and shown", "1 open question(s)" in html and "please sign" in html, html[:200])
    check("  a held claim is shown", "1 claim(s) held" in html and "role_gate.py" in html, html[:200])
    check("  an unanswered MERGE START is the red chip", "a MERGE slot is still open" in html, html[:400])
    check("  it says it is read live from the store", "lanes.py" in html and "THE-BOARD.md" in html)

    done = {"kind": "post", "at": "2026-10-28T06:03:00Z", "lane": "L3", "text": "MERGE DONE x abc123"}
    lanes.BOARD = board(tmp, [q, c, start, done])
    check("a MERGE DONE closes the slot", "no merge slot open" in F._board_section())

    lanes.BOARD = Path(tmp) / "not-there.jsonl"
    html = F._board_section()
    check("a MISSING board says so instead of showing green",
          "MISSING" in html and "not “nobody is waiting”" in html.replace("&ldquo;", "“").replace("&rdquo;", "”"),
          html[:220])
    print("test_hub_board_section:", "FAILED" if fails else "ok")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
