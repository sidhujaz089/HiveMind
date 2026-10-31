#!/usr/bin/env python3
"""Each app's timeline for the Hub (t130): where it started, what shipped, what is next.

Jas: "a project timeline area in each app that I can scroll. It'll show me where it started and
where is going. Tasks can be linked in the timeline as they are completed and scheduled."

timeline_html(area, tasks, releases) is pure: task metas (queue order for the open ones) and
(date, version) releases in, an HTML fragment out. Contract and oracle:
scripts/tests/test_app_timeline.py; the Hub wiring is pinned by test_apps_timeline_wired.py.
"""
import html  # noqa: F401 -- timeline_html escapes every title through it
import sys


def timeline_html(area, tasks, releases):
    """Started line, then releases and finished tasks oldest first, then Next (queue order)."""
    e = html.escape
    kept = [t for t in tasks if t.get("area") == area and t.get("status") != "cancelled"]
    if not kept and not releases:
        return '<div style="max-height:22rem;overflow-y:auto"><p>Nothing yet.</p></div>'
    events = [(d, f"{e(d)} &middot; Released {e(str(v))}") for d, v in releases]
    seen = set()
    for t in kept:
        if t.get("status") == "done" and t["id"] not in seen:
            seen.add(t["id"])
            d = (t.get("completed") or "")[:10]
            events.append((d, f'{e(d)} &middot; <a href="/plan/{e(t["id"])}">{e(t.get("title", ""))}</a>'))
    events.sort(key=lambda x: x[0])
    starts = [(t.get("created") or "")[:10] for t in kept if t.get("created")] + [d for d, _ in releases if d]
    out = ['<div style="max-height:22rem;overflow-y:auto">']
    if starts:
        out.append(f"<p>Started {e(min(starts))}</p>")
    out += [f"<div>{x}</div>" for _, x in events]
    nxt = [t for t in kept if t.get("status") != "done"]
    if nxt:
        out.append("<h4>Next</h4><ul>" + "".join(
            f'<li><a href="/plan/{e(t["id"])}">{e(t.get("title", ""))}</a></li>' for t in nxt) + "</ul>")
    out.append("</div>")
    return "\n".join(out)


if __name__ == "__main__":
    print("app_timeline: timeline_html() is not built yet -- see scripts/tests/test_app_timeline.py")
    sys.exit(2)
