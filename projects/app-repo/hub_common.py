"""Shared helpers every Hub page uses: esc, shell, md_to_html, _stage, _flag, _n, _who, _jsonl_tail. Moved here first; every pages_*.py module imports from this file, never from feedback.py, so there are no import cycles.

Part of splitting projects/app-repo/feedback.py -- the whole Hub, 2,000 characters from the builder's
whole-file cap on 2026-10-13 -- into one module per tab (t1115). Created EMPTY by Claude because the
builder can edit a file but cannot create one; the moves into it are the Manager's chunks. Move code
verbatim, at most 6 symbols per chunk.
"""
import hashlib
import html
import re
import sys
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(WORKSPACE / "scripts"))


def item_key(raw_line):
    """A stable id for a checklist item, from the RAW markdown.

    NOT the displayed label. The rendered text has been HTML-escaped and had `**bold**`
    turned into <b> tags, so the string in the form value is not the string the renderer
    compares against -- the first version of this matched nothing and the list never
    shrank. The raw line is the one thing that does not change between render and submit.
    """
    return hashlib.sha1(raw_line.strip().encode("utf-8")).hexdigest()[:12]


def tri_state(n, label, key):
    """Empty / passed / failed, as three radios styled into a segmented control.

    Radios rather than a cycling checkbox because this page has no JavaScript: a checkbox
    holds two states and cycling a third needs script. One tap either way, and it degrades
    to three plain radios if the styling never loads -- which still works.
    """
    return f"""<div class="chkrow">
  <input type="hidden" name="chktext{n}" value="{html.escape(label, quote=True)}">
  <input type="hidden" name="chkkey{n}" value="{key}">
  <div class="chklabel">{label}</div>
  <div class="tri">
    <input type="radio" id="c{n}n" name="chk{n}" value="-" checked>
    <label for="c{n}n" title="Not tested">&middot;</label>
    <input type="radio" id="c{n}y" name="chk{n}" value="ok">
    <label for="c{n}y" class="yes" title="Passed">&#10003;</label>
    <input type="radio" id="c{n}x" name="chk{n}" value="bad">
    <label for="c{n}x" class="no" title="Failed">&#10007;</label>
  </div>
</div>"""


def md_to_html(md, checklist=False, hide=()):
    """Just enough markdown for release notes. Not a general renderer: headings, checkboxes,
    bullets, bold and inline code. Anything else renders as plain text, which is a safe
    failure rather than a broken page."""
    out = []
    code_block = None
    for line in md.splitlines():
        if code_block is not None:
            if line.strip().startswith("```"):
                content = "\n".join(code_block)
                escaped = html.escape(content)
                out.append(f'<div class="codeblock"><button type="button" class="copy" onclick="navigator.clipboard.writeText(this.nextElementSibling.innerText)">Copy</button><pre><code>{escaped}</code></pre></div>')
                code_block = None
            else:
                code_block.append(line)
            continue

        if line.strip().startswith("```"):
            code_block = []
            continue

        e = html.escape(line)
        e = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", e)
        e = re.sub(r"`(.+?)`", r"<code>\1</code>", e)
        if line.startswith("## "):
            out.append(f"<h3>{e[3:]}</h3>")
        elif line.startswith("# "):
            out.append(f"<h2>{e[2:]}</h2>")
        elif re.match(r"^- \[ \] ", line):
            if checklist:
                key = item_key(line[6:])
                if key in hide:
                    md_to_html.hidden += 1
                    continue
                md_to_html.n += 1
                out.append(tri_state(md_to_html.n, e[6:], key))
            else:
                out.append(f'<div class="bullet">&#9744; {e[6:]}</div>')
        elif line.startswith("- "):
            out.append(f"<div class='bullet'>&bull; {e[2:]}</div>")
        elif not line.strip():
            out.append("<div class='sp'></div>")
        else:
            out.append(f"<p>{e}</p>")
    if code_block is not None:
        content = "\n".join(code_block)
        escaped = html.escape(content)
        out.append(f'<div class="codeblock"><button type="button" class="copy" onclick="navigator.clipboard.writeText(this.nextElementSibling.innerText)">Copy</button><pre><code>{escaped}</code></pre></div>')
    return "\n".join(out)


def _who():
    """scripts/who_works.snapshot(), or None if it cannot be read (the strip then says so)."""
    try:
        if str(WORKSPACE / "scripts") not in sys.path:
            sys.path.insert(0, str(WORKSPACE / "scripts"))
        import who_works
        return who_works
    except Exception as e:                                          # noqa: BLE001
        print(f"  NOTE: who_works unreadable: {e}", file=sys.stderr)
        return None


def esc(s):
    return html.escape(str(s))
md_to_html.n = 0        # checklist item counter, reset per render
md_to_html.hidden = 0   # how many were hidden as already done (t081)


# =======================================================================================
# WHAT EVERY PAGE IS FOR (2026-10-24). Enforced by scripts/tests/test_page_help.py.
# The registry lives here, not in a module of its own: hub_common is already registered,
# charted and imported by every page, and a new helper needs a Flow node, which is Jas's
# surface. One file, no new chart node, same enforcement.
# =======================================================================================
# Reviewed 2026-10-26 (C3): no entry's text needed changing. feedback.py's docstring named
# projects/app-repo/page_help.py as this text's home, and that file has not existed for some time, so
# the explanation of the explanations was itself stale. Corrected to name this module. Recorded here
# rather than silently, because the pre-commit guard refuses a page change that leaves this file alone.
# Reviewed 2026-10-26 (I2): the single-task page (/plan/<id>) has no help key of its own --
# _ACTIVE_KEY maps it to no page -- so the wake control explains itself in place (a date field,
# a Defer/Change button, a Clear button, and one line saying session_start.py surfaces it from
# that date). Adding a key here that nothing renders would be a dead entry, not documentation.
PAGES = {
    "network": {
        "title": "Network",
        "what": "What is on the network -- devices, the links between them, and the services around "
                "them -- and HOW EACH IS REACHED: MCP over the Cloudflare tunnel, the tailnet, the "
                "cable/LAN, a docker network, or loopback. Declared holes (state: unknown) are named "
                "rather than guessed.",
        "source": [
            "workspace/network.json (the store; every entry points at the file or command its facts "
            "come from, so the page is a projection and never a copy)",
            "projects/local-inference/models/SERVERS.json (devices and model servers -- referenced, "
            "never restated)",
            "docs/ACCESS.md (the Cloudflare tunnel, the WAF rule and the four layers -- the only "
            "written copy of Cloudflare-side state)",
        ],
        "enforce": ["check_workspace", "network_change"],
        "wrong": "A device or service that is not on this page is not in workspace/network.json. Add it "
                 "with scripts/network_change.py add, which validates it and leaves a replayable row; "
                 "never edit the JSON by hand, because the pre-commit guard will refuse it and the "
                 "changelog could no longer be replayed.",
    },
    "apps": {
        "title": "Apps",
        "what": "The index of every app on the box: which surfaces exist, what port, and what "
                "they are for.",
        "source": ["scripts/appnav.py (SECTIONS is the tab list)",
                   "docs/APPS.md (what each surface is)"],
        "enforce": ["check_workspace"],
        "wrong": "A tab that goes nowhere is an appnav SECTIONS entry with no route in the app "
                 "that serves it. Fix the table, not the link.",
    },
    "status": {
        "title": "Production",
        "_reviewed": "2026-10-28 (L1): explanation unchanged; the page crashed on a dropped name and renders again. "
                     "2026-10-28 (L2): 'Ready to merge' now means git says the branch is NOT an ancestor "
                     "of main -- it used to mean the ref still existed, so ten fully-merged branches sat "
                     "under 'Nothing is merged'. Merged refs are named for `git branch -d` instead, and a "
                     "branch git cannot judge is never called ready. RCA: "
                     "workspace/rca/rca-ready-to-merge-2026-10-28.md",
        "what": "Whether work is being delivered right now: the lanes, the queue per stage, and "
                "why anything is paused or parked.",
        "source": ["workspace/dispatch-loop.jsonl (tick, started, finished rows)",
                   "workspace/run-logs/device-pause.jsonl (who paused a device and why)",
                   "workspace/run-logs/scheduler-paused.enabled (the whole scheduler)",
                   "scripts/lane_status.py (the lane view)"],
        "enforce": ["production_liveness", "test_production_liveness"],
        "wrong": "If this page says nothing is running and the check says STALLED, the repair is "
                 "at the reason the last tick printed -- not on this page. The check "
                 "'Production is delivering, or something names why it is not' fails when nothing "
                 "is running, nothing has been delivered inside six hours, and no person chose it.",
    },
    "review": {
        "title": "Review",
        "_reviewed": "2026-10-29 (L1, merge on pass): Ready to merge is retired -- a passed review merges at once (scripts/merge_reviewed.py); a vetted branch still unmerged shows as 'Passed review, not merged', a fault to clear. The newest verdict per branch wins whatever its kind. Earlier 2026-10-29 (L1, Merge Review): delivered branches that auto-merge refused are their own section, Merge Review -- ready to merge but needing a frontier read -- not Technical; branches already in main are no longer listed. Earlier 2026-10-29 (L1, t9564): the pipeline's waits are no longer a second list -- each appears ONCE inside Needs Technical Guidance as its pipeline card (one per task), and a task the pipeline waits on is not repeated in its needs: tier. Earlier 2026-10-29 (L1, later): 'Waiting on Claude' is now the Technical escalation -- headed 'Needs Technical Guidance: from the pipeline' and 'Waiting on Technical review', each naming whoever holds Technical on the Models tab. Earlier 2026-10-29 (L1): Needs-you rows, its count and the hold box read the TIER (jas and vision are one hold). 2026-10-29 (L1, t9554): the Waiting flags and the Blocked/Done? lines name whoever holds the Technical escalation on the Models tab, not a hard-coded Claude. Earlier, 2026-10-28 (L2, t9540): the sections are now the four TIERS in a fixed order -- "
                     "Needs Vision, Needs Technical Guidance, Needs Rework -- instead of one heading per "
                     "role ('Assigned to Manager'). A task waits on a KIND of decision, not on a named "
                     "role, and a tier held by two roles (Claude and DSH both hold technical) still shows "
                     "ONE row per task. The mapping reads the old vocabulary too, so the page cannot "
                     "mis-file a task while the needs: migration is in flight.",
        "what": "Everything waiting on a person or a role, grouped by WHOSE it is -- Jas first, "
                "then Claude, then each role -- plus the recent agent runs.",
        "source": ["workspace/tasks/ (each task's `needs` field is its assignee)",
                   "workspace/tasks/QUEUE.md (the order)",
                   "workspace/agent-runs.jsonl (the Agent runs panel)"],
        "enforce": ["test_task_queue", "test_run_outcome_vocabulary"],
        "wrong": "A task in the wrong group is a wrong `needs` in its file -- use "
                 "taskstore.py, never a hand edit. A run counted as delivered when nothing was "
                 "written is the vocabulary defect t1587 fixed: ACCEPTED-DRYRUN is not a "
                 "delivery.",
    },
    "review/runs": {
        "title": "Agent runs",
        "what": "Every run a local model has made, newest first, with what each attempt did.",
        "source": ["workspace/agent-runs.jsonl"],
        "enforce": ["test_run_outcome_vocabulary"],
        "wrong": "An outcome that means nothing to you is an UNCLASSIFIED value: add it to "
                 "reviewq.PIPELINE_OUTCOMES or TRANSPORT_OUTCOMES with what it means. The "
                 "workspace check fails until somebody does.",
    },
    "lanes": {
        "title": "Lanes",
        "_reviewed": "2026-10-28 (L2): the tab now also carries THE BOARD -- who is holding which file, "
                     "which questions are unanswered, and whether a MERGE slot is still open -- read live "
                     "from scripts/lanes.py at render time (docs/THE-BOARD.md). A missing board file says "
                     "so; it is not shown as three green lights.",
        "what": "Which model is loaded on which device and what it is doing -- the live view of "
                "the box's compute -- and a SERVERS section that says each engine's READINESS, not "
                "just whether it exists: ready / loading / stopped, from its own /v1/models.",
        "source": ["scripts/lane_status.py", "models/SERVING.json (production lanes)",
                   "models/SERVERS.json + each engine's GET /v1/models (readiness, t1555)",
                   "docker (the serving container) and systemd (its unit) for the up/not-up half"],
        "enforce": ["test_lane_status", "test_swift_loading_not_loaded"],
        "wrong": "A lane shown loaded while the model is still loading is a view defect, not a "
                 "scheduler one: READY means GET /v1/models answered, LOADING means the unit or "
                 "container is up and it has not, and everything else is STOPPED. A tool missing "
                 "the server without a state is how 'loading' read as 'loaded' on 2026-10-23.",
        "_reviewed": "2026-10-27 (t1555): the Servers section was added, and the old 'Servers "
                     "answering now' line removed -- an answering list cannot show a load.",
    },
    "chat": {
        "title": "Chat",
        "what": "The lanes board as a conversation: every post, ask, answer and claim from the four lanes, oldest "
                "first, with a colour per speaker and a reply drawn under the question it answers. Open questions "
                "are counted at the top.",
        "source": ["scripts/lanes.py's board (~/.cache/hivemind-tmp/lanes/board.jsonl), read at render time"],
        "enforce": ["test_chat_page_renders"],
        "wrong": "A message that looks like it came from the wrong lane is a wrong `Lane:` on the row, not a view "
                 "defect -- the page prints the lane the row carries. If the board file is missing the page says so; "
                 "it does not show an empty conversation as if nobody had spoken.",
    },
    "scheduler": {
        "title": "Scheduler",
        "what": "What the scheduler is running with now (admission layer, charge per build, lane caps, pauses, "
                "the memory guard's levels), then every tuning constant in its code with the comment written "
                "beside it -- generated from the source, so a constant nobody explained shows as unexplained. "
                "Read-only: the constants live in guarded files.",
        "source": ["scripts/dispatch_loop.py", "scripts/capacity_model.py", "scripts/memory_guard.py",
                   "scripts/task_routing.py", "projects/app-repo/pages_ops.py (the page; TUNING_FILES)"],
        "enforce": ["test_pages_ops"],
        "wrong": "The page reads the code, so a wrong number here is a wrong number in the code: change it there, "
                 "in a reviewed commit (dispatch_loop.py and memory_guard.py need two signatures). A tuning file "
                 "that is missing belongs in pages_ops.TUNING_FILES.",
    },
    "logs": {
        "title": "Logs",
        "what": "Every user service's journal and every run log, newest first; one is opened at a time and only "
                "its tail is shown, with the cut said.",
        "source": ["systemctl --user (the services)", "workspace/run-logs/ and workspace/*.log",
                   "projects/app-repo/pages_ops.py (LOG_ROOTS: the only places it opens)"],
        "enforce": ["test_pages_ops"],
        "wrong": "A log that is not listed lives outside LOG_ROOTS; widen the roots in pages_ops.py rather than "
                 "opening arbitrary paths, because this page takes the path from the URL.",
    },
    "devices": {
        "title": "Devices",
        "what": "The memory guard's four lines and three timers -- pause, shed, resume, kill -- as "
                "editable settings, and the log of what was going on each time it fired.",
        "source": ["scripts/memory_guard.py", "workspace/guard-tiers.json",
                   "workspace/memory-guard.jsonl"],
        "enforce": ["test_devices_tab_guard_settings", "test_guard_split_levels"],
        "wrong": "A saved number the guard does not obey is not something to work around: the guard "
                 "re-reads workspace/guard-tiers.json on its next tick, and a refused set writes "
                 "nothing at all. If a save did not take, the file did not change -- check "
                 "`python3 scripts/memory_guard.py tiers` before believing the page.",
    },
    "scripts": {
        "title": "Scripts",
        "what": "Script reviews waiting for a decision, and the library of every script with what "
                "it is for and who calls it. Open one with /scripts?run=<path> and it is explained from "
                "its OWN comments -- the module docstring for the purpose, each function's docstring or "
                "the comment above it for its summary -- and its numbers are listed with their value, "
                "their line and whatever the author wrote beside them. Each number has a BOX: setting one writes "
                "the file and the value shown afterwards is read back FROM THE FILE, not echoed. Only scripts/ can "
                "be changed -- never the Hub's own code -- and a running script keeps the value it started with "
                "until it is restarted.",
        "source": ["workspace/script-reviews/ (the review queue)",
                   "scripts/library.json (the registry)", "workspace/SCRIPTS.md (generated)"],
        "enforce": ["library", "test_library", "test_script_page", "test_knob_box"],
        "_reviewed": "2026-10-31 (L2, rename): the queue directory is renamed with a migration and write_grant no longer recreates it; the paths these pages read moved. Earlier: 2026-10-30 (L2, later still): the loop the tab explains is renamed -- scoping_queue.sh, and one "
                     "templated scoping-queue@.service per scoper -- so the page's references moved with it; the "
                     "explanation itself is unchanged. Earlier 2026-10-30 (L2, later): the numbers are WRITABLE now -- one box per knob, posting to "
                     "/scripts/set, applied by set_knob.py and read back from the file. Refused: anything outside "
                     "scripts/ (the Hub's own code is in the library and must not be editable from itself), an "
                     "expression, and a wrong-typed value. Earlier 2026-10-30 (L2): added the per-script view, "
                     "read-only at the time.",
        "wrong": "A script that cannot run is unregistered. Register it with "
                 "scripts/library.py; a helper also needs a Flow node. Never copy a script "
                 "outside the library -- a second copy is a second source of truth.",
    },
    "misc": {
        "title": "Misc",
        "what": "The work no other tab shows: brief reviews, supervision decisions, console "
                "answers, the night queue, model loads and model time.",
        "source": ["workspace/brief-reviews/", "workspace/supervision.jsonl",
                   "workspace/manager-answers/", "workspace/agent-runs.jsonl"],
        "enforce": ["supervise", "test_library"],
        "wrong": "An undecided failure here is a supervision gap: the pipeline files it, the "
                 "Manager decides it. If it sits undecided, the Manager lane is down -- that is "
                 "the finding, not the item.",
    },
    "terminal": {
        "title": "Terminal",
        "what": "Run a command on the box and read the output, as the Manager's console.",
        "source": ["projects/local-inference/console.py", "workspace/manager-actions.jsonl "
                   "(every command is logged, redacted at the writer)"],
        "enforce": ["test_action_log_redaction"],
        "wrong": "A command that is not in the action log did not run through this page. If you "
                 "need a command logged, run it here; the log is the record.",
    },
    "flow": {
        "title": "Flow",
        "what": "The chart of how work moves: every stage, who works it, and the links between "
                "them.",
        "source": ["projects/local-inference/models/FLOW.json (the chart)",
                   "projects/local-inference/models/ROLES.json (who exists and what they may do)",
                   "scripts/flow_layout.py (the layout)", "scripts/flow_lint.py (the editor's gate)"],
        "enforce": ["test_role_flow_mapping", "test_flow_model_tasks"],
        "wrong": "A node naming a worker that is not a role, or a role no node shows, is the FK "
                 "defect (t761). The chart is NOT yet the routing -- the dispatcher's stage list "
                 "is still in code -- so a change here does not change behaviour yet. Say so "
                 "rather than implying it does.",
    },
    "corpus": {
        "title": "Corpus",
        "what": "What is on the box: the models present and pinned, and the scripts in the "
                "library.",
        "source": ["models/REGISTRY.json", "models/tags.lock.json", "scripts/library.json"],
        "enforce": ["test_model_registry", "library"],
        "wrong": "A model present but unpinned is a digest nobody recorded. Pin it; an unpinned "
                 "model can change under the whole system without a commit.",
    },
    "models": {
        "title": "Models",
        "_reviewed": "2026-10-29 (L1, t9540 R5): each role has an Escalations page, edited like Responsibilities; the Models tab shows who holds each kind read-only instead of the old position drop-downs. Earlier, 2026-10-28 (L2): a role's 'Tasks' are now 'Responsibilities' on the role page, and "
                     "the page's own help states that the list is the ONLY place a permission is decided "
                     "-- no document grants or removes one, and the gate reads the list rather than "
                     "prose. The offered list is still derived from the Flow chart, so a responsibility "
                     "that is not a pipeline stage (Unit control, Sweep all) cannot be offered yet; "
                     "giving responsibilities their own registry is the next change.",
        "what": "The roles: who exists, which model each one runs on, and what work each may take.",
        "source": ["projects/local-inference/models/ROLES.json (the store: the ONLY place a role "
                   "is created or edited)",
                   "projects/local-inference/models/SERVING.json (generated from it on every save)"],
        "enforce": ["role_store", "test_flow_model_tasks", "test_role_flow_mapping"],
        "wrong": "The task list offered here is derived from the Flow chart's role nodes and from "
                 "what a model can actually do -- so a task you want to assign that is not "
                 "offered is a chart node missing, or a kind the harness cannot run. Fix the "
                 "chart, then the role. Never edit SERVING.json: it is generated."
                 "<br><br><b>What the three counters on a task actually do (verified 2026-10-25).</b> "
                 "All three are read by the dispatcher; none is a placeholder. <b>Interrupt</b> stops "
                 "a job immediately when a higher-priority task arrives, but only if the model is on a "
                 "lower-priority one -- otherwise the job finishes first, which is also what a pause "
                 "does. <b>Series</b> is the anti-neglect quota: after N runs of one task in a row the "
                 "model moves down to its next task and the count resets; 0 is no limit. <b>Max lanes</b> "
                 "caps how many of the role's lanes may work one task type, so a role with many lanes "
                 "cannot pile them all onto one job; 0 is no limit and Building defaults to 1. <b>The "
                 "order of the list is the priority</b> -- the dispatcher works down it. The <b>role's "
                 "lane count</b> is the memory guard: a model running many parallel runners uses more of "
                 "itself at once, which is what filled this box's RAM, so lanes are capped at the role. "
                 "Behind all of them sits one mechanism called round robin -- it fills free lanes one per "
                 "task first, then goes round again -- which is how Max lanes and the list order are "
                 "enforced; it is not a separate setting.",
    },
}

# Pages that exist as routes but are not tabs. Kept explicit so the test can tell "a page with no
# help" from "a route that is not a page" (a POST handler, a redirect, an asset).
NOT_A_PAGE = ("/plan/add", "/plan/approve", "/plan/move", "/plan/status", "/plan/note",
              "/plan/done", "/plan/cancel", "/plan/attach", "/plan/unattach", "/say", "/file",
              "/status/pause", "/status/resume", "/status/notify", "/status/subscribe",
              "/status/vapid", "/status/device-pause", "/terminal/cmd/add", "/help")


def entry(key):
    """The help for one page key, or None."""
    return PAGES.get((key or "").strip("/"))


def code_notes(e, repo=None):
    """[(path, first paragraph of its docstring)] for every Python file the entry names as a source.

    GENERATED, NOT WRITTEN (B4, 2026-10-30). The registry above is prose someone wrote once; this is what the code
    says about itself today, read from the module docstring on every request, so it cannot drift from the code."""
    import ast
    repo = Path(repo) if repo else Path(__file__).resolve().parents[2]
    out = []
    for s in (e or {}).get("source") or []:
        m = re.match(r"\s*([\w./-]+\.py)\b", s)
        if not m:
            continue
        try:
            doc = ast.get_docstring(ast.parse((repo / m.group(1)).read_text(encoding="utf-8"))) or ""
        except (OSError, SyntaxError) as e:
            doc = f"(unreadable: {type(e).__name__}: {e})"     # said on the page, not dropped from it
        para = doc.strip().split("\n\n")[0].strip()
        if para:
            out.append((m.group(1), para))
    return out
