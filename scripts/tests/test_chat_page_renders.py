#!/usr/bin/env python3
"""The Chat tab: a board renders as a conversation, and says so when it cannot.

Jas, 2026-10-29: "redesign the message board so it looks like a chat window ... its own tab ... different colours"
per speaker. This is the page's enforcer -- the Hub's help panel refuses a tab that names none, because a page with
no test is a page nobody can tell is broken.

Every check is about a way the page could LIE: a missing board shown as an empty conversation, an unreadable row
dropped in silence, a long message shown clipped as though it were the whole thing, or a reply drawn as just another
row instead of under the question it answers.
"""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "projects" / "app-repo"))


def main():
    import pages_chat as C
    fails = []

    def check(label, ok, detail=""):
        print(f"  [{'ok' if ok else 'FAIL'}]  {label}" + ("" if ok else f"\n        {detail}"))
        if not ok:
            fails.append(label)

    d = Path(tempfile.mkdtemp())
    board = d / "board.jsonl"
    rows = [
        {"kind": "ask", "at": "2026-10-30T01:00:00Z", "lane": "L2", "to": "L1", "qid": "abc123",
         "text": "may I merge this?"},
        {"kind": "answer", "at": "2026-10-30T01:05:00Z", "lane": "L1", "qid": "abc123", "text": "yes, signed"},
        {"kind": "post", "at": "2026-10-30T01:06:00Z", "lane": "L4", "text": "x" * 1500},
        # AN UNANSWERED QUESTION, so the open-question count has something to count. The first version of this
        # fixture answered its only ask and then asserted a count was present -- the page was right and the test was
        # wrong, which is the distinction a fixture exists to keep clear.
        {"kind": "ask", "at": "2026-10-30T01:07:00Z", "lane": "L2", "to": "L1", "qid": "def456",
         "text": "and this one is still open?"},
    ]
    board.write_text("\n".join(json.dumps(r) for r in rows) + "\nnot json at all\n")

    h = C.chat_page(path=board)
    check("every message becomes a bubble", h.count('class="msg') >= 3, str(h.count('class="msg')))
    check("a speaker is coloured by LANE, not by message kind",
          "#047857" in h and "#1d4ed8" in h and "#6d28d9" in h, "expected the L2, L1 and L4 inks")
    check("an answer is drawn as a REPLY naming the question it answers",
          'class="msg right reply"' in h and "replying to" in h and "abc123" in h, "")
    check("a long message is capped LOUDLY", "TRUNCATED" in h, "")
    check("  and the whole text is still on the page, not dropped", "x" * 1500 in h, "the full body is absent")
    check("an unreadable line is COUNTED, not silently dropped", "1 unreadable line" in h, "")
    check("an open question is counted", "open question" in h, "")

    # A MISSING BOARD IS SAID, not shown as an empty conversation.
    h2 = C.chat_page(path=d / "absent.jsonl")
    check("a missing board says so rather than showing an empty conversation",
          "board is empty" in h2, h2[:160])
    print("test_chat_page_renders:", "FAILED" if fails else "ok")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
