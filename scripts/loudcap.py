#!/usr/bin/env python3
"""A cap that says so. The one sanctioned way to shorten content on the coding path.

Jas, 2026-09-14, through Chat: remove truncation on the coding side, and where a limit must
exist, make it LOUD -- "a shortened input can never look like a complete one. A silent cap is
the defect; a declared cap is a constraint."

    from loudcap import cap
    body = cap(text, 12000, "read tool: file larger than one reply")

Returns the text unchanged when it fits. When it does not, returns the first n characters
followed by a marker that states how many were shown, how many were NOT, and why -- so a model
reading it can say "I was not shown the rest" instead of reasoning over a hole it cannot see.
That is what let Q16 correctly refuse a review on 2026-09-14: preflight_brief's _show() said
"only 60000 of 92707 chars are shown", and the reviewer named the gap.

For a RECORD (a log row, a ledger field) do not use this at all: store the whole value.
A record cut to a width stops answering the question it was kept for
(docs/skills/never-truncate-a-record).
"""


def cap(text, n, why):
    text = "" if text is None else str(text)
    if len(text) <= n:
        return text
    return (text[:n] + f"\n\n[TRUNCATED -- {why}: showing the first {n} of {len(text)} "
            f"characters; {len(text) - n} characters are NOT shown. Treat everything after "
            f"this point as unseen, and say so rather than assuming what it contains.]")
