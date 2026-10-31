#!/usr/bin/env python3
"""Finished tasks, newest first, and who did each -- Claude or a local model (t155).

Jas: "a list of tasks that have been completed sorted by order they were completed ... I also want
to see what you did vs local model." Builder comes from Claude's merge-review verdicts
(workspace/review-verdicts.jsonl): a 'kept' verdict names the model that built the task; a task
with none was done by Claude. Contract and oracle: scripts/tests/test_finished_tasks.py; the Hub
page is pinned by test_finished_page_wired.py.
"""
import html  # noqa: F401 -- render escapes every title through it
import sys


def finished(tasks, verdicts):
    """Keep the LAST row per task whose verdict is 'kept'."""
    last_kept = {}
    for v in verdicts:
        if v.get('verdict') == 'kept':
            last_kept[v['task']] = v

    results = []
    for task in tasks:
        if task.get('status') == 'done':
            kept = last_kept.get(task['id'])
            
            orig_comp = task.get('completed') or ''
            if len(orig_comp) > 10:
                comp = orig_comp[:10]  # truncated
            else:
                comp = orig_comp

            results.append({
                'id': task['id'],
                'title': task['title'],
                'completed': comp,
                'by': (kept.get('served') or 'local model') if kept else 'Claude',
                'repaired': bool(kept.get('repaired')) if kept else False,
                '_sort_date': orig_comp
            })

    # Order newest completion first; tasks with no completion date go last.
    with_date = [r for r in results if r['_sort_date']]
    without_date = [r for r in results if not r['_sort_date']]
    
    with_date.sort(key=lambda x: x['_sort_date'], reverse=True)
    
    for r in results:
        del r['_sort_date']
        
    return with_date + without_date


def render(rows):
    if not rows:
        return "<p>Nothing finished yet.</p>"

    from collections import Counter
    builder_counts = Counter()
    for r in rows:
        builder_counts[r['by']] += 1
    
    sorted_builders = sorted(builder_counts.items(), key=lambda x: x[1], reverse=True)
    header = " ".join(f"{html.escape(b)}: {c}" for b, c in sorted_builders)

    rows_html = []
    for r in rows:
        date = r['completed']
        title = html.escape(r['title'])
        builder = html.escape(r['by'])
        if r['repaired']:
            builder += " (repaired)"
        rows_html.append(f"{date}, <a href=\"/plan/{r['id']}\">{title}</a>, {builder}")

    return f"{header}<br>\n" + "<br>\n".join(rows_html)


if __name__ == "__main__":
    print("finished_tasks: finished() and render() are not built yet -- see scripts/tests/test_finished_tasks.py")
    sys.exit(2)
