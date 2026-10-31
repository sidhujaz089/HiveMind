#!/usr/bin/env python3
"""The Hub layout Jas asked for on 2026-10-01, pinned.

"the console tab on the hub is broken ... you can remove that tab. I also think the backlog app is
no longer needed we can move the relevant sections inside of the production tab underneath their
respective columns and have them collapsed. The task intake tool is not needed on the backlog ...
All of those intake fields in the apps tab should have a subject line as well."
"""
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "projects" / "app-repo"))


def main():
    fails = []

    def ck(label, ok):
        print(f"  [{'ok' if ok else 'BAD'}]  {label}")
        if not ok:
            fails.append(label)

    import appnav
    keys = [k for k, _h, _l in appnav.SECTIONS]
    ck("no Console tab", "console" not in keys)
    ck("no Web UI tab (Open WebUI uninstalled)", "webui" not in keys)
    ck("no Backlog tab", "plan" not in keys)
    ck("Production is still there", "status" in keys)

    import feedback as F
    board = F.board_page()
    stages = re.findall(r'<details class="kstage"( open)?><summary><b>([^<]*)', board)
    names = {re.sub(r"\s*\(\d+\)$", "", n.strip()): bool(o) for o, n in stages}
    # SHORT LABELS JAS CHOSE (2026-10-01) -- the queues are "... Backlog", the done stages say done.
    for want in ("Task Backlog", "Session Backlog", "Script Backlog"):
        ck(f"Production has a '{want}' stage", want in names)
        ck(f"  and it loads collapsed", names.get(want) is False)
    ck("the backlog keeps its reorder controls", 'action="/plan/move"' in board)
    ck("no intake form on Production", 'action="/plan/add"' not in board)
    ck("Production has the scheduler controls (t318)",
       ('action="/status/pause"' in board or 'action="/status/resume"' in board) and "Scheduler:" in board)
    ck("Production has a Preflight stage", "Preflight" in names)
    # UPDATED 2026-10-08: "Preflight · waiting" was every chunk with a brief; it is now split by what
    # each waits on, and "Ready to build" sits after Preflight and before Building (top = last stage).
    ck("Ready to build between Building and Preflight",
       board.find("<b>Building (") < board.find("<b>Ready to build &middot; waiting for a lane (")
       < board.find("<b>Preflight ("))
    # PER STAGE, NOT A SLACK OF 5 (2026-10-14): the session-review stages hold reviews, not tasks, so
    # their cards have nothing to reorder; with 4 in RCA and 4 in Reviewing the old whole-board count
    # (cards - 5) went red on live data and held a commit. Every TASK stage is now checked exactly.
    # "Indexing" (2026-10-18) shows the corpus-index JOB while it runs -- a status card, not a task -- so
    # this went red only while that hourly job happened to be running, and held unrelated commits.
    NOT_TASKS = ("RCA", "Reviewing", "Indexing")
    short = []
    for part in board.split('<details class="kstage"')[1:]:
        m = re.search(r"<summary><b>([^<]*)", part)
        name = re.sub(r"\s*\(\d+\)$", "", m.group(1).strip()) if m else ""
        cards = part.count('<div class="card') - part.count('card small')
        if name not in NOT_TASKS and part.count('name="where" value="top"') < cards:
            short.append(name)
    ck("every Tasks stage's cards carry reorder buttons" + (f" (short: {short})" if short else ""), not short)

    import task_board
    ck("task_board orders preflight between briefed and building",
       task_board.STAGES.index("briefed") < task_board.STAGES.index("preflight")
       < task_board.STAGES.index("building"))
    import dispatch_loop
    ck("the scheduler takes chunks in the task queue's order",
       hasattr(dispatch_loop, "chunks_in_task_order"))

    # Decisions and Lessons on Corpus: every entry in each register, none dropped.
    for sec, path, pat in (("decisions", "docs/DECISIONS.md", r"^### D-\d+"),
                           ("lessons", "docs/LESSONS.md", r"^\*\*L-\d+")):
        want = len(re.findall(pat, (ROOT / path).read_text(encoding="utf-8"), re.M))
        got = F.corpus_page(sec).count('class="card centry"')
        ck(f"Corpus {sec}: {got} shown of {want} in {path}", want > 0 and got == want)

    # Flow tab (2026-10-01): tapping a card does not scroll; every task links its plain-English page.
    fp = F.flow_page()
    import json as _j
    _flow = _j.loads((ROOT / "projects/local-inference/models/FLOW.json").read_text())
    _card = fp[fp.find('closest(".flowcard");if(!c)return;'):][:300]
    ck("tapping a Flow card does not scroll the page", bool(_card) and "scrollIntoView" not in _card)
    ck("every Flow task has a Description link", fp.count("/flow/task/") >= len(_flow["tasks"]))
    ck("every Flow task has a plain-English description", all(t.get("about") for t in _flow["tasks"]))
    import flow_pages
    ck("the description page shows what it does and what is going on",
       all(x in (flow_pages.task_page("rca", {}) or "") for x in ("What it does", "What is going on")))

    src = (ROOT / "projects" / "app-repo" / "feedback.py").read_text(encoding="utf-8")
    ck("the Apps form has a subject field", 'name="subject"' in src)

    # The subject becomes the task title. Run _say against a scratch feedback dir and task store.
    made = {}

    class Fake:
        def _send(self, code, body, *a):
            made["code"] = code

        def _redirect(self, where):
            made["where"] = where
    tmp = Path(tempfile.mkdtemp())
    real_dir, real_add = F.FEEDBACK_DIR, F.ts.add
    F.FEEDBACK_DIR = tmp
    F.ts.add = lambda title, area, **kw: made.setdefault("title", title) and "t999"
    try:
        form = {"subject": "Mic button stays red", "text": "After I stop recording\nit stays red.",
                "app": "good-dictator", "ticks": "0"}
        try:
            F.Handler._say(Fake(), lambda k, d="": form.get(k, d))
        except Exception as e:                                      # noqa: BLE001
            made["err"] = f"{type(e).__name__}: {e}"
    finally:
        F.FEEDBACK_DIR, F.ts.add = real_dir, real_add
    ck(f"the subject is the task title (got {made.get('title')!r}, {made.get('err', '')})",
       made.get("title") == "Mic button stays red")
    note = next(tmp.glob("*.md"), None)
    ck("and it is in the feedback note's front matter",
       bool(note) and "subject: Mic button stays red" in note.read_text())

    for f in fails:
        print("FAIL:", f)
    print("test_hub_layout:", "FAILED" if fails else "ok")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
