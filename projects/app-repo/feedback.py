#!/usr/bin/env python3
"""HiveMind Hub — release notes, review, and the plan. On the tailnet.

    http://100.64.0.10:8091/

Named by Jas after The Office. Three sections:

    Apps    release notes, test checklists, and a way to write back   (built)
    Review  things waiting on his approval                            (not built)
    Plan    the task queue, ordered, top item next in line            (built)

Design and the reasoning behind every choice: docs/HIVEMIND-CONSOLE.md
The task store, the lock, and the CLI a session should use: taskstore.py

WHY A WEB PAGE AND NOT A SCREEN IN EACH APP
-------------------------------------------
A feedback screen inside an app only covers that app, only after it is rebuilt, and cannot
report the bug "the app will not start". A page on the tailnet works for every app
immediately — including superbutton, which is not being rebuilt — and is reachable when the
app under test is broken, which is exactly when there is something to say.

An in-app button can be added later; it would open this URL. Nothing here has to change.

WHERE THINGS GO
---------------
Notes land in workspace/app-feedback/ as one markdown file each, which is a directory Claude
Code reads at session start. That is the whole "pull all the recent communications" story:
no database, no API, no parsing — files in a folder, in git, greppable and diffable.

Checklists are read from projects/<app>/releases/<versionCode>.md, which is the same file
that supplies the F-Droid changelog. One source: the notes you read on the phone and the
notes in the store are the same text, so they cannot drift.

NO JAVASCRIPT, DELIBERATELY
---------------------------
Every control here is a form post. Reordering the plan is exactly what you want to do on a
bad day, and a page that needs script to reprioritise is a page that fails when you most
need it. That rules out drag-and-drop; up/down, Top and "Move before..." replace it, and the
last of those is two taps from last place to anywhere.

WHAT THIS DOES NOT DO
---------------------
It does not commit. Files land in the working tree and wait for a session, which is exactly
how sixteen untracked notes accumulated. The sweep script (t007) is the missing half.
"""

import hashlib
import calendar
import html
import json
import time
import threading
import os
import re
import shutil
import tempfile
import sys
import urllib.parse
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import taskstore as ts

WORKSPACE = Path(__file__).resolve().parent.parent.parent
FEEDBACK_DIR = WORKSPACE / "workspace" / "app-feedback"
PROJECTS = WORKSPACE / "projects"
BIND, PORT = "100.64.0.10", 8091

# The shared header. Imported eagerly, unlike _reviewq()'s optional import: every page is
# drawn through it, so if it is missing the right outcome is a loud failure at startup, not
# a headerless site that nobody notices is broken.
import sys as _sys
if str(WORKSPACE / "scripts") not in _sys.path:
    _sys.path.insert(0, str(WORKSPACE / "scripts"))
import appnav
from hub_common import esc, md_to_html, item_key, tri_state, _who  # noqa: E402  (t1115 step 1)
import hub_common as _PAGE_HELP  # noqa: E402 -- PAGES: the question mark on every page
from device_tiles import (_embedder_tile, device_pause_button, remote_devices_html, _role_name,  # noqa: E402
                          scheduler_controls, engine_readiness, servers_html)


KINDS = ["test result", "bug", "idea", "note"]


def releases_for(app):
    """Every release notes file for an app, newest versionCode first."""
    d = PROJECTS / app / "releases"
    if not d.is_dir():
        return []
    out = []
    for f in d.glob("*.md"):
        if f.stem.isdigit():
            out.append((int(f.stem), f))
    return sorted(out, reverse=True)


def apps_with_releases():
    return sorted(p.name for p in PROJECTS.iterdir()
                  if p.is_dir() and (p / "releases").is_dir())


def display_name(app):
    """Nav label. Defaults to the folder name, which is right for most projects and wrong for
    anything whose folder is an implementation detail. `name:` in the newest release's front
    matter overrides it -- one place, next to the notes it labels."""
    rels = releases_for(app)
    if rels:
        meta, _ = parse_notes(rels[0][1])
        if meta.get("name"):
            return meta["name"]
    return app


def parse_notes(path):
    """Split the front matter from the body. Deliberately minimal -- a full YAML parser is a
    dependency and a failure mode for four fields."""
    text = path.read_text(encoding="utf-8")
    meta = {}
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            for line in text[3:end].strip().splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    meta[k.strip()] = v.strip()
            text = text[end + 4:]
    return meta, text.strip()


DONE_DIR = WORKSPACE / "workspace" / "app-feedback" / "checklist-done"


def done_items(app, version):
    """Checklist lines already reported for this app+version.

    t081: Jas ticked every item, sent, and the whole list came back next visit. He is right
    that this is wrong -- a checklist that never shrinks cannot show what is LEFT, which is
    the only question it exists to answer.

    Kept as state rather than by editing the release notes, because the notes are the
    F-Droid changelog too: crossing an item off there would change what the store shows.
    Keyed on app + versionCode, so a new release starts with a full list again -- which is
    correct, since a new build has not been tested.
    """
    f = DONE_DIR / f"{re.sub(r'[^A-Za-z0-9._-]', '', app)}--{version or 'none'}.json"
    if not f.is_file():
        return set(), f
    try:
        return set(json.loads(f.read_text(encoding="utf-8"))), f
    except (json.JSONDecodeError, OSError):
        return set(), f


def mark_done(app, version, labels):
    done, f = done_items(app, version)
    done |= set(labels)
    DONE_DIR.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(sorted(done), indent=1, ensure_ascii=False), encoding="utf-8")
    return done


# REPO FILES, READABLE FROM THE PHONE (Jas, 2026-09-26): manager notes cite answers like
# workspace/run-logs/scoping-queue/out/t216.md, which he could not open. Any such path in a
# task note becomes a link to /file, a read-only viewer confined to the repository.
_PATH_RE = re.compile(r"(?<![\w/])((?:workspace|scripts|projects|docs|deploy|chat|models)/"
                      r"[\w.\-/]+?\.(?:md|py|sh|json|jsonl|txt|log|service|timer))(?::(\d+))?\b")
_FILE_DENY = ("workspace/private/", ".git/", ".env", ".secrets")


def _repo_file(rel):
    """The repository file for a relative path, or None if it is outside, denied or absent."""
    if not rel or any(d in rel for d in _FILE_DENY):
        return None
    try:
        f = (WORKSPACE / rel).resolve()
        f.relative_to(WORKSPACE.resolve())
    except (ValueError, OSError):
        return None
    return f if f.is_file() else None


_BARE_RE = re.compile(r"(?<![\w/.\-])([\w\-]+\.(?:md|py|sh|json|jsonl|txt))(?::(\d+))?\b")
_NAMES = {"at": 0.0, "idx": {}}


def _by_basename():
    """{basename: [repo paths]} from `git ls-files`, rebuilt at most every 10 minutes."""
    if time.time() - _NAMES["at"] < 600:
        return _NAMES["idx"]
    import subprocess as _sp
    idx = {}
    try:
        out = _sp.run(["git", "-C", str(WORKSPACE), "ls-files"], capture_output=True, text=True,
                      timeout=20).stdout
    except (OSError, _sp.SubprocessError) as e:
        print(f"  NOTE: git ls-files failed: {e}", file=sys.stderr)
        out = ""
    for rel in out.splitlines():
        idx.setdefault(rel.rsplit("/", 1)[-1], []).append(rel)
    _NAMES.update(at=time.time(), idx=idx)
    return idx


def linkify_paths(h):
    """Wrap every existing repo path in already-escaped HTML with a link to /file.

    A BARE NAME (AGENTS.md:362, SCRIPTS.md:117) is linked only when exactly one tracked file
    has that name (Jas, 2026-09-26) -- a guess between two files would be a wrong link."""
    def link(rel, m):
        # a line number cannot land in rendered markdown, so it opens the numbered text view
        q = urllib.parse.quote(rel) + (("&raw=1" if rel.endswith(".md") else "")
                                       + f"#L{m.group(2)}" if m.group(2) else "")
        return f'<a href="/file?path={q}">{m.group(0)}</a>'

    def sub(m):
        return link(m.group(1), m) if _repo_file(m.group(1)) else m.group(0)

    def bare(m):
        hits = [r for r in _by_basename().get(m.group(1), []) if _repo_file(r)]
        return link(hits[0], m) if len(hits) == 1 else m.group(0)
    h = _PATH_RE.sub(sub, h)
    # second pass outside the links just made: split on <a ...>...</a> and touch only the rest
    parts = re.split(r"(<a\b[^>]*>.*?</a>)", h, flags=re.S)
    return "".join(x if x.startswith("<a") else _BARE_RE.sub(bare, x) for x in parts)


def render_markdown(text):
    """Full markdown for /file: tables with cells, every heading level, fenced code, lists.

    Jas, 2026-09-26: "Is it possible to make tables render with cells?" -- md_to_html is the
    release-notes subset and showed tables as pipes and ### as text. Python-Markdown (system
    python3, 3.5.2) with RAW HTML SWITCHED OFF: these files include model-written answers, and
    HTML in them must show as text, never run in his browser. Falls back to the subset if the
    library is missing."""
    try:
        import markdown as _md
    except ImportError:
        return md_to_html(text)
    m = _md.Markdown(extensions=["tables", "fenced_code", "sane_lists"])
    m.preprocessors.deregister("html_block")
    m.inlinePatterns.deregister("html")
    out = m.convert(text)
    return ('<div class="mdoc">' + re.sub(r"<table>", '<div class="tbl"><table>', out)
            .replace("</table>", "</table></div>") + '</div>')


def file_page(rel, raw=False):
    """A repository file, whole, readable on a phone. Never truncated."""
    f = _repo_file(rel)
    if f is None:
        return 404, ('<div class="empty">Not a readable file in the repository: '
                     f'<code>{esc(rel)}</code></div>')
    try:
        text = f.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return 500, f'<div class="empty">Could not read it: {esc(str(e))}</div>'
    head = (f'<p class="small"><code>{esc(rel)}</code> &middot; {len(text):,} characters &middot; '
            f'{esc(time.strftime("%Y-%m-%d %H:%M", time.localtime(f.stat().st_mtime)))}</p>')
    lines = text.splitlines()
    if f.suffix == ".md" and not raw:
        head += (f'<p class="small"><a href="/file?path={urllib.parse.quote(rel)}&raw=1">'
                 f'View as text with line numbers</a></p>')
        body = linkify_paths(render_markdown(text))
    else:
        body = ('<pre style="white-space:pre-wrap;word-break:break-word;font-size:.78rem">'
                + "".join(f'<span id="L{i}"><span style="opacity:.45">{i:>5}  </span>'
                          f'{linkify_paths(esc(l))}</span>\n'
                          for i, l in enumerate(lines, 1)) + '</pre>')
    style = ("<style>.mdoc table{border-collapse:collapse;font-size:.8rem}"
             ".mdoc th,.mdoc td{border:1px solid var(--line,#ccc);padding:.25rem .45rem;"
             "vertical-align:top;text-align:left}.mdoc th{background:rgba(127,127,127,.12)}"
             ".mdoc .tbl{overflow-x:auto;margin:.5rem 0}.mdoc pre{white-space:pre-wrap;"
             "word-break:break-word;font-size:.78rem}.mdoc h1,.mdoc h2,.mdoc h3,.mdoc h4"
             "{margin:.9rem 0 .3rem}</style>")
    return 200, style + head + f'<div class="card">{body}</div>'


# --------------------------------------------------------------------------- the shell

# Warm on purpose. This is the page Jas opens most, and a grey form is a page you avoid.
CSS = """
:root {
  --bg:#fdf6ec; --card:#fffdf9; --ink:#3b2a20; --dim:#8a6f5e; --line:#e6d4bf;
  --hot:#c8552b; --warm:#e08a2e; --gold:#f0b429; --leaf:#5f8a3f; --cool:#7a6a9c;
}
@media (prefers-color-scheme: dark) {
  :root { --bg:#1d1613; --card:#272019; --ink:#f2e6d8; --dim:#b09a86; --line:#463829;
          --hot:#f0764a; --warm:#f0a852; --gold:#f5c451; --leaf:#8fbb63; --cool:#a898cc; }
}
* { box-sizing:border-box; }
body { font-family:system-ui,-apple-system,sans-serif; margin:0; padding:0 0 5rem;
       background:var(--bg); color:var(--ink); line-height:1.5; }
/* FULL WIDTH (Jas, 2026-09-29, unfolded phone: "a lot of wasted space on the right and the left"). */
.wrap { max-width:none; margin:0; padding:0 .9rem; }
/* Columns never stack: on a narrow screen the row scrolls sideways ("I'll just scroll left and right"). */
.kanban, .crewrow { display:flex !important; gap:.6rem; overflow-x:auto; align-items:flex-start;
                    padding-bottom:.4rem; }
.kcol { flex:1 0 19rem; min-width:19rem; }
.crewrow > .card { flex:1 0 17rem; min-width:17rem; }
.crewrow .tt { column-span:all; }
.crewrow .crewitem { break-inside:avoid; }
.khead { font-weight:700; font-size:1.05rem; margin:.3rem 0 .4rem; }
details.kstage { border:1px solid var(--line); border-radius:10px; padding:.35rem .6rem; margin:.4rem 0; }
details.kstage > summary { cursor:pointer; }
.kstub { border-style:dashed; }
.grid { display:grid; grid-template-columns:repeat(auto-fill, minmax(20rem, 1fr)); gap:0 .6rem; }
/* The header and its tabs now come from scripts/appnav.py. */
h1 { font-size:1.35rem; margin:0; letter-spacing:-.01em; }
h1 .em { font-size:1.15rem; }
.tag { font-size:.8rem; opacity:.92; margin-top:.15rem; }
h2 { font-size:1.05rem; margin:1.3rem 0 .4rem; }
h3 { font-size:.92rem; margin:1rem 0 .3rem; color:var(--dim); }
p,.bullet { margin:.25rem 0; }
.sp { height:.5rem; }
code { background:rgba(200,85,43,.12); padding:0 .25rem; border-radius:3px;
       font-size:.9em; }
a { color:var(--hot); }


/* corpus: chips encode state; the card and pill styles are the Apps tab's own */
.corpus-chip { display:inline-block; font-size:.72rem; font-weight:700; letter-spacing:.04em;
  text-transform:uppercase; padding:.18rem .5rem; border-radius:999px; white-space:nowrap; }
.corpus-chip.leaf { color:var(--leaf); background:rgba(95,138,63,.16); }
.corpus-chip.cool { color:var(--cool); background:rgba(122,106,156,.16); }
.corpus-chip.hot  { color:var(--hot);  background:rgba(200,85,43,.14); }
.corpus-chip.gold { color:var(--warm); background:rgba(240,180,41,.18); }
.corpus-card h3 { margin:0; font-size:1.02rem; overflow-wrap:anywhere; }
.corpus-card .head { display:flex; flex-wrap:wrap; align-items:center; gap:.4rem .6rem; }
.corpus-card .head .corpus-chip { margin-left:auto; }
.corpus-card h4 { margin:.7rem 0 .15rem; font-size:.72rem; letter-spacing:.06em; text-transform:uppercase; color:var(--dim); }
.corpus-card p { margin:0; }
.corpus-card dl { display:grid; grid-template-columns:minmax(8rem,11rem) 1fr; gap:.2rem .8rem; margin:0; font-size:.9rem; }
.corpus-card dt { color:var(--dim); } .corpus-card dd { margin:0; overflow-wrap:anywhere; }
@media (max-width:520px) { .corpus-card dl { grid-template-columns:1fr; } .corpus-card dd { margin-bottom:.35rem; } }
.corpus-issue { padding:.45rem .6rem; border-radius:8px; margin:.3rem 0 0; font-size:.92rem; }
.corpus-issue.hot { background:rgba(200,85,43,.12); } .corpus-issue.gold { background:rgba(240,180,41,.16); }
.corpus-src { font-size:.75rem; color:var(--dim); margin-top:.6rem !important; }
nav.subpills a { font-size:.85rem; padding:.25rem .6rem; }

/* app pills */
nav.pills { margin:0 0 .3rem; }
nav.pills a { display:inline-block; margin:0 .4rem .4rem 0; padding:.35rem .75rem;
              border:1px solid var(--line); border-radius:999px; text-decoration:none;
              background:var(--card); color:var(--ink); font-size:.9rem; }
nav.pills a.on { background:var(--hot); border-color:var(--hot); color:#fff;
                 font-weight:600; }

/* cards */
.card { background:var(--card); border:1px solid var(--line); border-radius:12px;
        padding:.7rem .8rem; margin:.5rem 0; }
.row { display:flex; align-items:flex-start; gap:.5rem; }
.row .grow { flex:1; min-width:0; }
.tt { font-weight:600; }
.tt a { text-decoration:none; color:var(--ink); }
.meta { font-size:.78rem; color:var(--dim); margin-top:.15rem; }
.rank { font-variant-numeric:tabular-nums; color:var(--dim); font-size:.85rem;
        min-width:1.6rem; padding-top:.15rem; }
.next { border-color:var(--hot); border-width:2px; box-shadow:0 2px 8px rgba(200,85,43,.16); }
.q4rest { display:inline-block; background:var(--line, #ccc); color:inherit; font-size:.68rem;
          padding:.1rem .4rem; border-radius:4px; opacity:.8; }
.nextflag { display:inline-block; background:var(--hot); color:#fff; font-size:.68rem;
            font-weight:700; letter-spacing:.06em; padding:.1rem .4rem; border-radius:4px;
            vertical-align:.1rem; }
.nextflag.soon { background:transparent; color:var(--hot); border:1px solid var(--hot); }
.crewrow .card { margin:0; }
.crewitem { margin-top:.3rem; overflow-wrap:anywhere; }
.crewfoot { margin:.2rem 0 .6rem; }

/* status chips */
.chip { display:inline-block; font-size:.7rem; font-weight:700; letter-spacing:.04em;
        padding:.1rem .42rem; border-radius:999px; text-transform:uppercase; }
.s-queued  { background:rgba(138,111,94,.18);  color:var(--dim); }
.s-doing   { background:rgba(224,138,46,.22);  color:var(--warm); }
.s-blocked { background:rgba(200,85,43,.18);   color:var(--hot); }
.s-done    { background:rgba(95,138,63,.2);    color:var(--leaf); }
.s-waiting { background:var(--gold); color:#3b2a20; }
.s-replied { background:rgba(95,138,63,.25); color:var(--leaf); }
.area { font-size:.7rem; font-weight:700; letter-spacing:.04em; color:var(--cool); }

/* buttons */
form.inline { display:inline; }
.b { font:inherit; cursor:pointer; border:1px solid var(--line); background:var(--card);
     color:var(--ink); border-radius:8px; padding:.3rem .5rem; line-height:1; }
.b:active { transform:translateY(1px); }
.b.sm { font-size:.85rem; padding:.32rem .45rem; }
.b.hot { background:var(--hot); border-color:var(--hot); color:#fff; font-weight:600; }
.b.ghost { background:transparent; color:var(--dim); }
.acts { display:flex; gap:.3rem; align-items:center; flex-wrap:wrap; margin-top:.45rem; }
button.wide { width:100%; padding:.85rem; font-weight:700; border-radius:10px;
              background:var(--hot); border:none; color:#fff; font-size:1rem;
              cursor:pointer; }
select,input[type=text],textarea { font:inherit; padding:.5rem; border-radius:8px;
     border:1px solid var(--line); background:var(--card); color:var(--ink); width:100%; }
/* F1: grows with what is typed, capped near half the screen. field-sizing is CSS, so this
   costs no JavaScript; where it is unsupported the box simply stays at min-height, which is
   exactly today's behaviour rather than a regression. */
/* t049: capped at 40vh, not 50. At 50vh the box plus its label plus the sticky bar
   could still put the button under the keyboard on a phone. The real fix is the sticky
   bar below -- this just stops the growth being dramatic. */
textarea { min-height:6rem; max-height:40vh; field-sizing:content; overflow-y:auto; }

/* t049: the submit row is ALWAYS reachable, whatever the textarea does.
   Jas hit this twice and could not reproduce it deliberately -- which fits, because it
   depends on how much he dictated and how tall the keyboard was. A layout bug that only
   appears at certain content lengths is one you cannot test your way out of; make the
   control independent of the content instead. */
.actionbar { position:sticky; bottom:0; background:var(--bg); padding:.6rem 0 .3rem;
             border-top:1px solid var(--line); display:flex; gap:.5rem;
             margin-top:.5rem; }
.actionbar button { flex:1; }
/* The cancel reason sits in the action row. It is REQUIRED: a task dropped with
   no reason is indistinguishable later from one dropped by mistake. */
.whyin { font:inherit; padding:.3rem .45rem; border:1px solid var(--line);
         border-radius:7px; min-width:11rem; margin-right:.3rem; }
.actionbar .cancel { flex:0 0 auto; padding:.85rem 1.1rem; border-radius:10px;
                     border:1px solid var(--line); background:var(--card);
                     color:var(--dim); text-decoration:none; font-weight:600;
                     display:flex; align-items:center; }
label.f { display:block; margin:.6rem 0 .2rem; font-size:.85rem; color:var(--dim); }

.chkrow { display:flex; align-items:center; gap:.6rem; padding:.45rem 0;
          border-bottom:1px solid var(--line); }
.chkrow:last-of-type { border-bottom:none; }
.chklabel { flex:1; min-width:0; font-size:.94rem; }
.tri { display:flex; flex:0 0 auto; border:1px solid var(--line); border-radius:8px;
       overflow:hidden; }
.tri input { position:absolute; opacity:0; pointer-events:none; }
.tri label { padding:.4rem .7rem; cursor:pointer; font-size:1rem; line-height:1;
             background:var(--card); color:var(--dim); border-right:1px solid var(--line); }
.tri label:last-of-type { border-right:none; }
.tri input:checked + label { background:var(--dim); color:var(--card); font-weight:700; }
.tri input:checked + label.yes { background:var(--leaf); color:#fff; }
.tri input:checked + label.no  { background:var(--hot);  color:#fff; }
.ok { background:rgba(95,138,63,.16); border:1px solid var(--leaf); padding:.7rem;
      border-radius:10px; margin:.7rem 0; }
/* .bad WAS EMITTED AND NEVER DEFINED. The Status tab has written class="bad" for every
   failure it reports -- snapshot failed, GPU unavailable, inference path down -- and the
   stylesheet had no rule for it, so each warning rendered as ordinary text beside green
   boxes. Found 2026-09-14 while adding lane cards. .warn is new, for a quiet log. */
.bad { background:rgba(200,70,50,.14); border:1px solid var(--hot); padding:.7rem;
       border-radius:10px; margin:.7rem 0; }
.warn { background:rgba(240,180,41,.14); border:1px solid var(--gold); padding:.7rem;
        border-radius:10px; margin:.7rem 0; }
.note { background:rgba(240,180,41,.14); border-left:3px solid var(--gold);
        padding:.5rem .7rem; border-radius:0 8px 8px 0; margin:1.1rem 0; }
.note.jas { background:rgba(80,160,255,.13); border-left-color:#4a9eff; }   /* t126: Jas's own notes */
.note b { color:var(--warm); }
.small { color:var(--dim); font-size:.85rem; }
.empty { text-align:center; color:var(--dim); padding:2rem 1rem; }
.shot { width:100%; border-radius:8px; display:block; border:1px solid var(--line); }
input[type=file] { font:inherit; padding:.45rem; border:1px dashed var(--line);
                   border-radius:8px; background:var(--card); color:var(--ink);
                   width:100%; }
.picking { background:rgba(240,180,41,.2); border:1px solid var(--gold);
           padding:.6rem .7rem; border-radius:10px; margin:.6rem 0; }
form.section { margin-top:1.4rem; border-top:1px solid var(--line); padding-top:.9rem; }
"""

# Models is an absolute URL because chatui.py is a separate service on :8092. Jas:
# "I like having the interface so I can chat with the models directly... it needs to be
# somehow integrated into the party planning committee so I can navigate through all the
# areas easily." It stays a separate process on purpose -- it talks to each model container
# DIRECTLY rather than through the gateway, because LiteLLM pins the model name at startup
# and swapping underneath it produced 404s that read as a broken model. Shared navigation,
# separate process.
# The tab list, the URLs and the header markup all live in scripts/appnav.py now, so the
# Committee, the Console and chatui cannot disagree about what the row contains. Do not
# re-declare them here. The two third-party apps in the row (ttyd, Open WebUI) cannot be
# given a header of their own, so they are framed by the /terminal and /webui routes below.


def _reviewq():
    """The review ledger, or None if it cannot be read.

    Imported lazily and defensively: this page must still render if the local-inference
    tooling is missing or broken. A review tab that 500s because a sibling project moved is
    worse than one that quietly shows fewer sections.
    """
    try:
        import sys as _s
        sp = str(WORKSPACE / "scripts")
        if sp not in _s.path:
            _s.path.insert(0, sp)
        import reviewq
        return reviewq
    except Exception:
        return None


_CHUNKS_CACHE = {"at": 0.0, "by_task": {}}


def chunk_status():
    """{task: [(chunk, state, detail)]} -- every brief (build chunk) and where it is now.

    Jas, 2026-09-26: "sometimes it seems like they are doing the same task on loop when it may
    be briefs within ... I would like to see them in the production at all stages." Read from
    what the dispatcher itself writes: its latest full tick names every enrolled chunk and why
    it was not started (green, parked, held, delivered...), queued priority work (a brief
    waiting for its review), running jobs, and the build history for attempt counts. Cached
    for 10 s so one page view does not re-read the ledger per card."""
    if time.time() - _CHUNKS_CACHE["at"] < 10:
        return _CHUNKS_CACHE["by_task"]
    import task_board
    by_chunk, _ = task_board.brief_tasks()
    rows, _bad = _jsonl_tail(WORKSPACE / "workspace" / "dispatch-loop.jsonl", 8000)
    import jsonl_cache
    tick = jsonl_cache.latest_tick_file(WORKSPACE / "workspace" / "dispatch-loop.jsonl") or \
        next((r for r in reversed(rows) if r.get("event") == "tick" and "skipped" in r), {})      # t675
    why = {x.get("chunk"): x.get("why", "") for x in tick.get("skipped") or []}
    for q in tick.get("queued") or []:
        if q.get("kind") == "preflight":
            why.setdefault(q.get("key"), "waiting for its brief review")
    started, done, builds, fails = {}, set(), {}, {}
    for r in rows:
        c = r.get("chunk")
        if r.get("event") == "started":
            started[r.get("pid")] = r
        elif r.get("event") == "finished":
            done.add(r.get("pid"))
            if r.get("kind", "production") == "production":
                builds[c] = builds.get(c, 0) + 1
                if not r.get("accepted"):
                    fails[c] = fails.get(c, 0) + 1
    live = {}
    for pid, r in started.items():
        if pid not in done and Path(f"/proc/{pid}").exists():
            k = r.get("kind", "production")
            live[r.get("chunk")] = (f"{'building' if k == 'production' else k} on "
                                    f"{r.get('model', '?')} since {str(r.get('at', ''))[11:16]}Z")
    out = {}
    for c, t in by_chunk.items():
        w = live.get(c) or why.get(c) or "enrolled, not yet considered"
        if w.startswith("target green"):
            w = "done -- its test is green on main"
        n = builds.get(c, 0)
        detail = f"{n} build(s), {fails.get(c, 0)} not accepted" if n else "no builds yet"
        out.setdefault(t, []).append((c, w, detail))
    _CHUNKS_CACHE.update(at=time.time(), by_task=out)
    return out


def chunks_html(tid):
    """The briefs under one task card; empty when the task has none."""
    try:
        cs = chunk_status().get(tid) or []
    except Exception as e:                                          # noqa: BLE001
        return f'<div class="small">briefs unreadable: {esc(f"{type(e).__name__}: {e}")}</div>'
    if not cs:
        return ""
    lis = "".join(f'<li><b>{esc(c)}</b> &mdash; {esc(w[:180])} '
                  f'<span class="small">({esc(d)})</span></li>' for c, w, d in sorted(cs))
    return f'<ul class="small" style="margin:.3rem 0 0 1rem;padding:0">{lis}</ul>'


def _task_card(tid, extra=""):
    meta, body = ts.find(tid)
    if not meta:
        return (f'<div class="card"><div class="tt">{esc(tid)}</div>'
                f'<div class="meta small">task file not found</div>{extra}</div>')
    return f"""<div class="card">
  <div class="tt"><a href="/plan/{esc(meta['id'])}">{esc(meta['title'])}</a></div>
  <div class="meta"><span class="area">{esc(meta.get('area', ''))}</span>
    &nbsp; {chip(meta['status'])} &nbsp;
    <span class="small">{esc(meta.get('kind', ''))} &middot; {esc(meta['id'])}</span></div>
  {extra}
  {chunks_html(meta['id'])}
</div>"""


# THE FOUR TIERS, and what a task's `needs` means in them (t9540, Claude's decision 2 and the Review tab's
# order). Read tolerantly on purpose: until the one-time migration lands (scripts/migrate_needs_tiers.py)
# a task may carry the OLD vocabulary (jas / claude / a role key), so the page maps both and cannot show a
# task in the wrong section for a day.
TIER_ORDER = ("vision", "technical", "rework")
TIER_TITLE = {
    "vision": "Needs Vision",
    "technical": "Needs Technical Guidance",
    "rework": "Needs Rework",
}


def tier_of(needs, roles=()):
    """The tier a task's `needs` value belongs to. Never guesses: an unknown role key is rework, because
    that is what a role hands back."""
    v = str(needs or "").strip().lower()
    if v in ("", "model", "production"):
        return "production"
    if v in ("vision", "jas"):
        return "vision"
    if v in ("technical", "claude", "dsh", "dsh-harness"):
        return "technical"
    if v in ("rework",) or v in set(roles or ()):
        return "rework"
    return "rework"


def review_sections(in_review=(), claude_summary="", parts=False):
    """Ready to merge, Waiting on Claude, Agent runs -- the Production tab's review half.

    Jas, 2026-09-26: these moved from Review to Production, and where one duplicated a
    Production section the two became one complete section. "Waiting on Claude" is therefore
    three sources in one place: the review ledger's pending builds, the board's "In review
    (Claude)" column (`in_review`, task ids not already pending), and the Waiting-for-Claude
    summary (`claude_summary`, the delivered branches / outstanding chunks / hung watchers)."""
    rq = _reviewq()
    if rq is None:
        return ('<div class="card"><p class="small">The local-agent review ledger is not '
                'readable from here, so those sections are hidden rather than guessed at.'
                '</p></div>')
    out = []

    # Vetted by Claude, sitting on a branch. This is Jas's review of CODE, as distinct from
    # the questions above, which are his review of DECISIONS.
    # A BRANCH IS "READY TO MERGE" ONLY IF MAIN DOES NOT ALREADY HAVE ITS WORK (RCA 2026-10-28).
    # This list used to be built from branch EXISTENCE: ten branches that were fully merged sat under a
    # heading that said "Nothing is merged -- that is your call", because nothing deletes a branch after
    # a merge and `branch_exists` cannot tell a merged ref from live work. Two rules changed here:
    #   * the test is git's: is this branch an ANCESTOR of main? (reviewq.branch_merged)
    #   * dedupe is by BRANCH, not by task: "the newest verdict per task wins" hid an older, genuinely
    #     unmerged attempt for a task that had since been done on another branch (t1017's ab9e3876).
    # NEWEST VERDICT OF ANY KIND per branch wins (2026-10-29): filtering to 'kept' first let an older keep outlive a
    # later send-back that corrected it, so a rejected branch showed as passed.
    seen, ready = set(), []
    for v in reversed([v for v in rq.verdicts() if v.get("branch")]):   # newest verdict per BRANCH wins
        if v["branch"] in seen:
            continue
        seen.add(v["branch"])
        if v.get("verdict") == "kept":
            ready.append(v)
    judged = [(v, rq.branch_merged(v["branch"])) for v in ready]
    live = [v for v, (exists, unmerged) in judged if exists and unmerged]
    unknown = [v for v, (exists, unmerged) in judged if exists and unmerged is None]
    already = [v for v, (exists, unmerged) in judged if exists and unmerged is False]
    merged = [v for v, (exists, _unmerged) in judged if not exists]   # ref gone: merged or removed

    if live:
        cards = "".join(_task_card(v["task"], extra=f"""
  <div class="small">Reviewed by Claude &middot; {esc(v.get('served') or 'local model')}</div>
  <div class="small"><code>git diff {esc(v['branch'])}~1..{esc(v['branch'])}</code></div>
  <div class="small">{esc((v.get('why') or '')[:220])}</div>""") for v in live)
        # READY TO MERGE IS RETIRED (Jas, 2026-10-29: "Retire Ready to Merge. Merge on pass."). A review that
        # passes merges the branch at once (scripts/merge_reviewed.py), so a vetted branch still unmerged is
        # not a queue for Jas any more -- it is a merge that did not happen, and it is shown as one.
        out.append('<!--ready-->' + f'<h2>Passed review, not merged ({len(live)})</h2>'
                   '<p class="small">Merge on pass: a review that passes merges the branch at once. These '
                   'passed and did not merge, so each is a fault to clear, not a decision for you: '
                   '<code>python3 scripts/merge_reviewed.py &lt;branch&gt; --why "..."</code></p>' + cards)

    if already and not live:
        # NOT A FAILURE, BUT NOT INVISIBLE EITHER: the work is in main and only the ref is left. Saying
        # so is what stops the same ten cards reappearing with no explanation.
        out.append('<p class="small">' + esc(
            f"{len(already)} vetted branch(es) are already IN main -- nothing to merge; delete the refs "
            f"when convenient: " + ", ".join(f"git branch -d {v['branch']}" for v in already[:6])) + '</p>')
    if unknown:
        out.append('<p class="small">' + esc(
            f"{len(unknown)} branch(es) could not be judged by git -- not shown as ready to merge, "
            f"because an answer nobody could get is not evidence: "
            + ", ".join(v['branch'] for v in unknown[:6])) + '</p>')

    pending = unlanded_pending(rq)
    seen_p = {r.get("task") for r in pending}
    more = [t for t in in_review if t not in seen_p]
    cards = "".join(_task_card(r.get("task") or "?", extra=f"""
  <div class="small">{esc(r.get('served') or 'local model')} &middot;
    {esc(str(r.get('at') or ''))} &middot; {r.get('attempts')} attempt(s)</div>
  <div class="small">{esc((r.get('why') or '')[:220])}</div>""") for r in pending)
    cards += "".join(_task_card(t, extra='<div class="small">a delivered branch for it waits on '
                                         'Claude&rsquo;s review</div>') for t in more)
    out.append('<!--waiting-->' + f'<h2>Merge Review ({len(pending) + len(more)}) &middot; held by {technical_holders()}</h2>'
               '<p class="small">The local models finished these and the Technical holder has not vetted '
               'them yet. Nothing here needs you.</p>' + (cards or '<div class="small">&mdash;</div>')
               + claude_summary)

    if merged:
        out.append(f'<p class="small">{len(merged)} reviewed change(s) no longer have a '
                   'branch &mdash; merged, or the branch was removed.</p>')

    # Agent runs live here rather than in the model-chat window. Jas: "that makes sense that
    # Agent Runs would be inside of my part of the review area." Compact on purpose -- the
    # full log is a click away, because a run-by-run dump under the things needing his
    # decision is exactly the noise he asked to avoid.
    runs = rq.runs()[-40:]
    if runs:
        from collections import Counter
        tally = Counter(r.get("outcome", "?") for r in runs)
        bits = " &middot; ".join(f"{esc(k)} {v}" for k, v in tally.most_common())
        last = runs[-1]
        out.append('<!--runs-->' + f'<h2>Agent runs ({len(runs)})</h2><div class="card">'
                   f'<div class="small">Last {len(runs)} run(s): {bits}</div>'
                   f'<div class="small">Most recent: {esc(last.get("task") or "?")} '
                   f'&middot; {esc(last.get("outcome") or "?")} '
                   f'&middot; {esc(last.get("served") or "local model")}</div>'
                   f'<div class="small"><a href="/review/runs">See every run</a></div></div>')
    if not parts:
        return "".join(out)
    got = {"ready": "", "waiting": "", "runs": "", "other": ""}
    for x in out:
        k = next((k for k in ("ready", "waiting", "runs") if x.startswith(f"<!--{k}-->")), "other")
        got[k] += x
    return got


def runs_page():
    rq = _reviewq()
    if rq is None:
        return '<div class="empty">The run ledger is not readable from here.</div>'
    rows = list(reversed(rq.runs()))[:80]
    if not rows:
        return '<div class="empty">No agent runs recorded yet.</div>'
    cards = []
    for r in rows:
        trail = " &rarr; ".join(esc(str(t.get("outcome"))) for t in (r.get("trail") or []))
        branch = r.get("branch")
        cards.append(f"""<div class="card">
  <div class="tt"><a href="/plan/{esc(r.get('task') or '')}">{esc(r.get('task') or '?')}</a>
    &nbsp;<span class="small">{esc(r.get('outcome') or '?')}</span></div>
  <div class="meta small">{esc(str(r.get('at') or ''))} &middot;
    {esc(r.get('served') or 'local model')} &middot;
    {r.get('attempts')} attempt(s) &middot; {r.get('tokens')} tok &middot;
    {r.get('seconds')}s</div>
  <div class="small">{esc((r.get('why') or '')[:200])}</div>
  <div class="small">{trail}</div>
  {f'<div class="small"><code>git diff {esc(branch)}~1..{esc(branch)}</code></div>' if branch else ''}
</div>""")
    return (f'<h2>Agent runs ({len(rows)})</h2>'
            '<p class="small">Every run the local model has made, newest first. The trail is '
            'what each attempt did. This is the record, not a queue &mdash; what needs a '
            'decision is on the <a href="/review">Review</a> tab.</p>' + "".join(cards))


# Call sites pass the old href form ("/plan"); appnav keys on a short name instead, because
# matching on href stopped working once the links became absolute for the other apps.
_ACTIVE_KEY = {"/": "apps", "/plan": "status", "/review": "review", "/status": "status",
               "/scripts": "scripts", "/misc": "misc", "/lanes": "lanes", "/chat": "chat",
               "/corpus": "corpus", "/flow": "flow", "/models": "models",
               "/network": "network"}


# QUICK COMMANDS ON THE TERMINAL TAB (Jas, 2026-09-28: "a pill for each one. I could tap it, and
# it would just send it to the terminal and enter ... a plus button ... delete ... arrow keys").
# The terminal (agent-shell: ttyd) runs inside tmux session "hub", so the Hub can type into it
# with `tmux send-keys` -- a page cannot type into a frame on another port.
TERM_CMDS = WORKSPACE / "workspace" / "terminal-commands.json"
TMUX_SESSION = "hub"
TERM_KEYS = {"up": "Up", "down": "Down", "left": "Left", "right": "Right", "tab": "Tab",
             "esc": "Escape", "ctrl-c": "C-c", "enter": "Enter"}


def term_cmds():
    try:
        return json.loads(TERM_CMDS.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return [{"label": "lane status", "cmd": "python3 ~/ai-workspace/scripts/lane_status.py"},
                {"label": "session start", "cmd": "python3 ~/ai-workspace/scripts/session_start.py"}]
    except (OSError, ValueError) as e:
        print(f"  NOTE: terminal-commands.json unreadable: {e}", file=sys.stderr)
        return []


def term_cmds_save(cmds):
    TERM_CMDS.write_text(json.dumps(cmds, indent=1) + "\n", encoding="utf-8")


def tmux(*args):
    """send-keys to the terminal's tmux session, creating it if no browser has opened it yet."""
    import subprocess as _sp
    if _sp.run(["tmux", "has-session", "-t", TMUX_SESSION], capture_output=True, timeout=10).returncode:
        _sp.run(["tmux", "new-session", "-d", "-s", TMUX_SESSION, "-c", str(WORKSPACE)],
                capture_output=True, timeout=10)
    r = _sp.run(["tmux", "send-keys", "-t", TMUX_SESSION, *args], capture_output=True, text=True,
                timeout=10)
    return r.returncode == 0, r.stderr.strip()


PROMPT = re.compile(r"^[\w.-]+@[\w.-]+:[^\n$#]*[$#] ?")


def last_output(pane):
    """The most recent command and what it printed, from a tmux pane's text (t619).

    Jas: copy "the last return", not the whole screen. Walks back from the bottom: the trailing
    prompt (and blank Enter presses) are skipped; the block is the latest prompt line that carries a
    command, through to the line before the next prompt. '' when no command is on screen."""
    lines = [l.rstrip() for l in pane.splitlines()]
    while lines and not lines[-1].strip():
        lines.pop()
    prompts = [i for i, l in enumerate(lines) if PROMPT.match(l)]
    for k in range(len(prompts) - 1, -1, -1):
        i = prompts[k]
        if PROMPT.sub("", lines[i], count=1).strip():
            end = prompts[k + 1] if k + 1 < len(prompts) else len(lines)
            return "\n".join(lines[i:end]).rstrip()
    return ""


def terminal_page():
    pills = "".join(
        f'<span class="qc"><button class="pill" data-cmd="{html.escape(c["cmd"])}" '
        f'title="{html.escape(c["cmd"])}">{html.escape(c.get("label") or c["cmd"])}</button>'
        # t316 (Jas): "a copy, edit, and delete button next to each command" -- shown in edit mode.
        f'<button class="del cpy" data-cmd="{html.escape(c["cmd"])}" title="copy">&#10697;</button>'
        f'<button class="del edt" data-i="{i}" data-cmd="{html.escape(c["cmd"])}" '
        f'data-label="{html.escape(c.get("label") or "")}" title="edit">&#9998;</button>'
        f'<button class="del" data-i="{i}" title="delete">&times;</button></span>'
        for i, c in enumerate(term_cmds()))
    keys = "".join(f'<button class="key" data-key="{k}">{lab}</button>' for k, lab in
                   [("up", "&uarr;"), ("down", "&darr;"), ("left", "&larr;"), ("right", "&rarr;"),
                    ("tab", "Tab"), ("esc", "Esc"), ("ctrl-c", "Ctrl-C"), ("enter", "&#9166;")])
    return f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Terminal</title><style>{CSS}{appnav.CSS}
html,body {{ height:100%; margin:0; overflow:hidden; }}
body {{ display:flex; flex-direction:column; }}
.appnav {{ margin:0; flex:0 0 auto; }}
.bar {{ flex:0 0 auto; display:flex; flex-wrap:wrap; gap:.35rem; padding:.4rem .5rem;
  border-bottom:1px solid var(--line,#ccc); align-items:center; }}
.bar button {{ font:inherit; border:1px solid var(--line,#ccc); background:var(--card,#fff);
  color:inherit; border-radius:999px; padding:.3rem .7rem; cursor:pointer; }}
.qc {{ display:inline-flex; }} .qc .pill {{ border-radius:999px 0 0 999px; }}
.qc .del {{ border-radius:0 999px 999px 0; border-left:0; padding:.3rem .5rem; display:none; }}
body.editing .qc .del {{ display:inline-block; }}
.key {{ border-radius:8px !important; min-width:2.4rem; }}
.add {{ display:none; gap:.3rem; width:100%; }} body.adding .add {{ display:flex; }}
.add input {{ flex:1; font:inherit; padding:.3rem .5rem; min-width:0; }}
.frame {{ flex:1 1 auto; width:100%; border:0; display:block; min-height:0; }}
.msg {{ font-size:.8rem; opacity:.7; }}
</style></head><body>
{appnav.header(active="terminal", subtitle="Terminal")}
<div class="bar">{pills}
  <button id="plus" title="add a command">+</button>
  <button id="edit" title="delete commands">edit</button>
  <span class="add"><input id="lab" placeholder="label (optional)" style="max-width:9rem">
    <input id="cmd" placeholder="command to run"><button id="save">add</button></span>
</div>
<div class="bar"><input id="say" placeholder="type a command or message" style="flex:1;min-width:8rem;font:inherit;padding:.3rem .5rem">
  <button id="sendsay">Send</button><button id="copylast" title="copy the last command and its output">Copy last</button></div>
<div class="bar">{keys}<span class="msg" id="msg"></span></div>
<iframe class="frame" src="{html.escape(appnav.TERMINAL)}" allow="clipboard-read; clipboard-write"></iframe>
<script>
function post(u, d) {{ return fetch(u, {{method:"POST", body:new URLSearchParams(d)}})
  .then(r => r.text().then(t => {{ document.getElementById("msg").textContent = r.ok ? "" : t; return r; }})); }}
document.querySelectorAll(".pill").forEach(b => b.onclick = () => post("/terminal/send", {{text: b.dataset.cmd}}));
document.querySelectorAll(".key").forEach(b => b.onclick = () => post("/terminal/key", {{key: b.dataset.key}}));
document.querySelectorAll(".del:not(.cpy):not(.edt)").forEach(b => b.onclick = () =>
  post("/terminal/cmd/del", {{i: b.dataset.i}}).then(() => location.reload()));
document.querySelectorAll(".cpy").forEach(b => b.onclick = () =>
  navigator.clipboard.writeText(b.dataset.cmd).then(() =>
    {{ document.getElementById("msg").textContent = "copied"; }}));
document.querySelectorAll(".edt").forEach(b => b.onclick = () => {{
  const c = prompt("Command", b.dataset.cmd); if (c === null || !c.trim()) return;
  const l = prompt("Label (optional)", b.dataset.label); if (l === null) return;
  post("/terminal/cmd/edit", {{i: b.dataset.i, label: l, cmd: c}}).then(() => location.reload()); }});
// t619: a text box that sends to the terminal, and Copy last (the latest command + its output).
function sendSay() {{ const t = document.getElementById("say"); if (!t.value.trim()) return;
  post("/terminal/send", {{text: t.value}}).then(r => {{ if (r.ok) t.value = ""; }}); }}
document.getElementById("sendsay").onclick = sendSay;
document.getElementById("say").onkeydown = e => {{ if (e.key === "Enter") sendSay(); }};
document.getElementById("copylast").onclick = () => fetch("/terminal/last").then(r => r.text())
  .then(t => navigator.clipboard.writeText(t).then(() =>
    {{ document.getElementById("msg").textContent = t ? "copied the last output" : "nothing to copy"; }}));
document.getElementById("plus").onclick = () => {{ document.body.classList.toggle("adding");
  document.getElementById("cmd").focus(); }};
document.getElementById("edit").onclick = () => document.body.classList.toggle("editing");
document.getElementById("save").onclick = () => {{ const c = document.getElementById("cmd").value;
  if (!c.trim()) return; post("/terminal/cmd/add", {{label: document.getElementById("lab").value, cmd: c}})
  .then(() => location.reload()); }};
</script></body></html>"""


def _framed(title, src, active):
    """One of the third-party apps, under our header.

    Jas asked for the header on every app including this one, over an auto-hiding bar:
    "same fixed header as everything else". So the frame takes the height that is left
    rather than the header giving way to it -- 100dvh, not 100vh, because on a phone vh
    counts the address bar that is not there once you scroll, and the terminal would end up
    with its bottom rows under the chrome.
    """
    return f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title><style>{CSS}{appnav.CSS}
html,body {{ height:100%; margin:0; overflow:hidden; }}
body {{ display:flex; flex-direction:column; }}
.appnav {{ margin:0; flex:0 0 auto; }}
.frame {{ flex:1 1 auto; width:100%; border:0; display:block; min-height:0; }}
</style></head><body>
{appnav.header(active=active, subtitle=html.escape(title))}
<iframe class="frame" src="{html.escape(src)}"
        allow="clipboard-read; clipboard-write"></iframe>
</body></html>"""


# THE TABS WHERE WORK IS SEEN carry the crew strip (Jas, 2026-09-29): "I want to see what my manager
# is doing, Sr, and Frontline ... on the production tabs and other tabs where I would see work".
CREW_TABS = {"/plan", "/status", "/review", "/lanes", "/chat", "/misc", "/scripts"}


def crew_strip():
    """Every model role's tile: what it is on NOW and takes NEXT.

    ONE SOURCE (Jas, 2026-10-08: "I see a mis match in the role tiles, scheduler areas, and the running
    jobs in the lanes tab"). NOW is lane_status's live lanes -- the same rows as Running jobs and the
    scheduler's list -- grouped by the role each job carries (lane_status.role_of). Tiles come from the
    role store, not a fixed Manager/Senior/Frontline. NEXT is the dispatcher's order from its last tick."""
    ww = _who()
    try:
        sys.path.insert(0, str(WORKSPACE / "scripts"))
        import lane_status as _ls
        import task_routing as _T
        store = _T.load()
        live = _ls.snapshot()["lanes"]
        snap = ww.snapshot() if ww else {"roles": {}}
    except Exception as e:                                          # noqa: BLE001
        return f'<div class="bad small">Who is working: {esc(f"{type(e).__name__}: {e}")}</div>'
    view_of = getattr(ww, "STORE_ROLE", {}) if ww else {}
    labels = getattr(ww, "KIND_LABEL", {}) if ww else {}

    def line(flag, cls, label, key, task):
        link = (f'<a href="/plan/{esc(task)}">{esc(key)}</a>' if task and key == task else
                f'{esc(key)} <a class="small" href="/plan/{esc(task)}">({esc(task)})</a>' if task else esc(key))
        return (f'<div class="crewitem"><span class="{cls}">{flag}</span> '
                f'<span class="small">{esc(label)}</span> {link}</div>')
    cards = []
    for role in _T.model_roles(store):
        r = store["roles"][role]
        name = r.get("name") or role
        now = [x for x in live if x.get("role") == role]
        # NEXT only for a role that has tasks: the old view folded several store roles onto one tile.
        nxt = ((snap.get("roles", {}).get(view_of.get(role, role)) or {}).get("next", [])
               if _T.tasks_of(store, role) else [])
        # THE EMBEDDER IS NEVER "IDLE" IN THAT SENSE (Jas, 2026-10-13: "I don't see the embedder role on
        # the lanes or Production tabs"). It has no jobs: session review's meaning match, memory recall
        # and the corpus index call it inline. So it always shows, with where it is and its last hour.
        if role == "embedder":
            cards.append(_embedder_tile(name, r.get("model", "")))
            continue
        # IDLE ROLES ARE HIDDEN (Jas, 2026-10-07); a busy one widens with its load.
        if not now and not nxt:
            continue
        lanes = _T.role_lanes(store, role)
        title = (f'{esc(name)} <span class="small">&middot; {esc(r.get("model", ""))} &middot; '
                 f'{len(now)} of {lanes if lanes is not None else "&infin;"} lane(s)</span>')
        body = []
        for x in now:
            key = x.get("brief") or ""
            m = re.search(r"\b(t\d{2,4})\b", key)
            body.append(line(f"{name.upper()} WORKING", "nextflag", labels.get(x["kind"], x["kind"]),
                             key, m.group(1) if m else None))
        body = body or ['<div class="crewitem small">idle</div>']
        body += [line(f"{name.upper()} NEXT", "nextflag soon", i["label"], i["key"], i.get("task")) for i in nxt[:1]]
        if len(nxt) > 1:
            body.append('<div class="small">then ' + ", ".join(esc(i["key"]) for i in nxt[1:3]) + '</div>')
        span = min(4, max(1, (len(now) + 1) // 2))
        cards.append(f'<div class="card crew{" next" if now else ""}" style="flex-grow:{span};'
                     f'min-width:min(100%,{17 * span}rem);columns:{span} 15rem"><div class="tt">{title}</div>'
                     + "".join(body) + '</div>')
    if not cards:
        cards.append('<div class="card crew"><div class="small">Every role is idle.</div></div>')
    age = snap.get("age_s")
    foot = (f'NEXT from the dispatcher\'s tick {age // 60} min ago' if age is not None
            else esc(snap.get("note") or "no dispatcher snapshot yet"))
    if age is not None and age > 1800:
        foot = f'<b>{foot} &mdash; stale: the dispatcher has not ticked in 30 min</b>'
    return f'<div class="crewrow">{"".join(cards)}</div><p class="small crewfoot">{foot}</p>'


def _mins(s):
    return f"{s // 3600}h{s % 3600 // 60:02d}m" if s >= 3600 else f"{s // 60}m{s % 60:02d}s"


# The question mark (2026-10-24). Quiet by default: it is a "why is this page like this" affordance,
# not a call to action, and it sits above the banner where every page already starts.
CSS_HELP = """
.helpbar{margin:.4rem 0 .2rem}
.helpbar .helpbtn{display:inline-block;background:#eef2f7;color:#1b2a41;border:1px solid #cfd8e3;
 border-radius:14px;padding:.15rem .6rem;font-size:.78rem;text-decoration:none}
.helpbar .helpbtn:hover{background:#e2e9f2}
"""


def _help_link(active):
    """The question mark, on every page (Jas, 2026-10-24).

    It goes to /help?p=<this page>, which explains what the page shows, which FILE it is a view
    of, which check keeps it honest, and what to do when the page and the disk disagree. The
    text lives in projects/app-repo/hub_common.py (PAGES), one entry per page, and is edited in
    the same commit as the page it explains -- which the pre-commit hook now enforces (C3).
    as the page it explains.
    """
    key = (_ACTIVE_KEY.get(active, active) or "").strip("/")
    if key not in _PAGE_HELP.PAGES:
        return ""
    return (f'<div class="helpbar"><a class="helpbtn" href="/help?p={esc(key)}" '
            f'title="How this page works, and what to do when it disagrees with the disk">'
            f'? How this page works</a></div>')


def help_page(key):
    """The explanation behind one page's question mark."""
    e = _PAGE_HELP.entry(key)
    if not e:
        listing = "".join(f'<li><a href="/help?p={esc(k)}">{esc(v["title"])}</a> &mdash; '
                          f'{esc(v["what"][:90])}</li>' for k, v in sorted(_PAGE_HELP.PAGES.items()))
        return (f'<h2>How each page works</h2><div class="card"><ul class="small">{listing}</ul>'
                '</div>')
    rows = "".join(f"<li><code>{esc(s)}</code></li>" for s in e["source"])
    enf = "".join(f"<li><code>{esc(s)}</code></li>" for s in e["enforce"])
    return (f'<h2>{esc(e["title"])} &mdash; how this page works</h2>'
            f'<div class="card"><div class="small"><b>What it shows.</b> {esc(e["what"])}</div>'
            f'<div class="small" style="margin-top:.6rem"><b>The file(s) it is a view of.</b>'
            f'<ul>{rows}</ul>If the page is wrong, the repair is THERE, never on the page.</div>'
            f'<div class="small" style="margin-top:.6rem"><b>What keeps it honest.</b><ul>{enf}</ul>'
            f'Those run in the workspace check and the hourly sweep; when one fails it files a '
            f'task, so a deviation reaches the queue instead of waiting to be noticed.</div>'
            f'<div class="small" style="margin-top:.6rem"><b>When the page and the disk disagree.'
            f'</b> {esc(e["wrong"])}</div></div>'
            + (('<div class="card"><div class="small"><b>From the code</b> -- what each source file says about '
                'itself today, read from its docstring, so this part cannot go stale.</div>'
                + "".join(f'<div class="small" style="margin-top:.5rem"><code>{esc(p)}</code>'
                          f'<div style="white-space:pre-wrap">{esc(t)}</div></div>'
                          for p, t in _PAGE_HELP.code_notes(e)) + '</div>') if _PAGE_HELP.code_notes(e) else ""))


GUARD_TIERS = WORKSPACE / "workspace" / "guard-tiers.json"
GUARD_LEDGER = WORKSPACE / "workspace" / "memory-guard.jsonl"
LEVEL_FIELDS = ("pause_gb", "shed_gb", "resume_gb", "kill_gb")
TIMER_FIELDS = ("grace_s", "settle_s", "interval_s")


def save_guard_levels(form, path=None):
    """Write the guard's four levels and three timers from the Devices form.

    Returns [] when written, [problem] when refused -- and on a refusal the file is left EXACTLY as it
    was, never half-written. The ORDER is what is checked, not the individual numbers: a set with the
    shed line below the kill line sheds nothing and then kills the model, and a resume line at or below
    the pause line makes the guard flap on a single number. The tier order and the processes are not
    editable here: they are the same file, but they are a safety decision rather than a number.

    Jas, 2026-10-25: "If I adjust things the systems effected should reboot on their own if possible so
    I dont need to enter terminal commands." There is nothing to reboot: memory_guard.levels() re-reads
    this file on its next tick (0.5 s), so the save IS the change.
    """
    path = Path(path or GUARD_TIERS)

    def one(k):
        return (form.get(k) or [""])[0].strip()

    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return [f"guard-tiers.json unreadable ({type(e).__name__}: {e}) -- nothing was written"]
    levels = dict(d.get("levels") or {})
    timers = dict(d.get("timers") or {})
    for k in LEVEL_FIELDS + TIMER_FIELDS:
        v = one(k)
        if not v:
            continue
        try:
            val = float(v)
        except ValueError:
            return [f"{k} must be a number (got {v!r}) -- nothing was written"]
        if val < 0:
            return [f"{k} cannot be negative (got {val:g}) -- nothing was written"]
        (levels if k in LEVEL_FIELDS else timers)[k] = val
    try:
        pz, sh, rs_, kl = (float(levels[k]) for k in LEVEL_FIELDS)
    except (KeyError, TypeError, ValueError):
        return ["guard-tiers.json has no usable levels block -- nothing was written"]
    if not kl < sh < pz:
        return [f"the lines must satisfy kill < shed < pause (got kill {kl:g}, shed {sh:g}, "
                f"pause {pz:g}) -- nothing was written"]
    if rs_ <= pz:
        return [f"resume ({rs_:g} GB) must be above pause ({pz:g} GB), or the guard flaps on one "
                f"number -- nothing was written"]
    d["levels"], d["timers"] = levels, timers
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(d, indent=1) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return []


def devices_page(path=None, ledger=None, problems=(), saved=False):
    """The Devices tab (Jas, 2026-10-25): the guard's levels and timers, and the log of what was going
    on when it fired -- so "how do I optimise this" is answered by numbers rather than a terminal."""
    path, ledger = Path(path or GUARD_TIERS), Path(ledger or GUARD_LEDGER)
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return f'<div class="bad">guard-tiers.json unreadable: {esc(str(e))}</div>'
    levels, timers, tiers = d.get("levels") or {}, d.get("timers") or {}, d.get("tiers") or []

    def num(k, label, step="1"):
        return (f'<label class="small" style="display:inline-block;margin:0 .8rem .6rem 0">'
                f'{esc(label)}<br><input type="number" step="{step}" name="{k}" '
                f'value="{esc(str(levels.get(k, timers.get(k, ""))))}" style="width:6.5rem"></label>')

    banner = ""
    if problems:
        banner = ('<div class="bad"><b>Not saved.</b><ul>'
                  + "".join(f"<li>{esc(x)}</li>" for x in problems) + "</ul></div>")
    elif saved:
        banner = ('<div class="card small"><b>Saved.</b> The guard re-reads this file on its next tick '
                  '(0.5 s) &mdash; no restart, no command line.</div>')
    order = " &rarr; ".join(
        f'<b>{esc(str(t.get("name")))}</b>' for t in tiers if isinstance(t, dict))
    rows, unreadable = [], 0
    try:
        for line in ledger.read_text(encoding="utf-8").splitlines()[-12:]:
            try:
                r = json.loads(line)
            except ValueError:
                # COUNTED, not skipped (the library gate is right): a ledger line nobody can parse is
                # a fact about the log, and a page that silently shows fewer rows than exist is the
                # same lie as a check that reads as assurance.
                unreadable += 1
                continue
            rows.append(
                "<tr>"
                f'<td class="small">{esc(str(r.get("at", "")))}</td>'
                f'<td>{esc(str(r.get("available_gb", "")))} GB</td>'
                f'<td>{esc(str(r.get("stopped") or r.get("trip") or "-"))}</td>'
                f'<td>{esc(str(r.get("available_after_gb", "")))} GB</td>'
                f'<td class="small">{esc(str(r.get("why", "")))}</td>'
                "</tr>")
    except OSError:
        rows = []
    log = ("<table><tr><th>when</th><th>free</th><th>shed</th><th>after</th><th>why</th></tr>"
           + "".join(rows) + "</table>") if rows else (
        '<p class="small">The guard has not fired since this ledger began.</p>')
    if unreadable:
        log += (f'<p class="small">{unreadable} row(s) of the ledger could not be read, so this list '
                f'is shorter than the log.</p>')
    return (
        banner
        + '<div class="card"><h2 style="margin-top:.2rem">The memory guard</h2>'
        + f'<p class="small">It sheds in this order: {order} &mdash; and the model is killed last, '
          'because it takes about twenty minutes to reload. Pausing comes first, and the pause has to '
          'hold for the grace period before anything is shed, so a spike that passes is not acted on.'
          '</p>'
        + '<form method="post" action="/devices/settings">'
        + num("pause_gb", "Pause new work below (GB)")
        + num("shed_gb", "Start shedding below (GB)")
        + num("resume_gb", "Resume above (GB)")
        + num("kill_gb", "Kill the model below (GB)")
        + num("grace_s", "Grace period after pausing (s)")
        + num("settle_s", "Settle after a shed (s)", "0.5")
        + num("interval_s", "Check every (s)", "0.1")
        + '<p><button type="submit">Save</button> '
          '<span class="small">kill &lt; shed &lt; pause, and resume above pause.</span></p>'
          "</form></div>"
        + '<div class="card small"><b>What was going on when it fired.</b> '
          "Free memory, the tier it shed, what that came back to, and why it acted:"
          + log + "</div>")


def shell(title, body, active="/", banner=""):
    if active in CREW_TABS:
        banner = crew_strip() + banner
    return f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<style>{CSS}{appnav.CSS}{CSS_HELP}</style></head><body>
{appnav.header(active=_ACTIVE_KEY.get(active, active), subtitle=html.escape(title))}
<div class="wrap">{_help_link(active)}{banner}{body}</div>
</body></html>"""


# --------------------------------------------------------------------------- uploads

TITLE_MAX  = 80                        # matches the form; enforced server-side too
CHUNK      = 1024 * 1024               # spool/copy granularity; also the memory ceiling

# There is no upload size limit. Jas is the only user, the port is on the tailnet, and the
# form is where his workflow already is -- an arbitrary cap just moves the work to a USB
# stick. What DID need fixing is why a cap existed at all: the parser used to read the whole
# request into memory. On a box with UNIFIED memory that is not merely wasteful, it competes
# with model weights. Uploads now spool to disk in CHUNK-sized pieces and are copied out by
# byte range, so memory stays flat whether the file is 40 KB or 40 GB.
#
# The real limit is now the disk, which is the honest one, and it is checked before spooling.
MIN_FREE_BYTES = 20 * 1024 * 1024 * 1024   # refuse rather than fill the disk to zero
IMAGE_EXT  = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
              ".gif": "image/gif", ".webp": "image/webp", ".heic": "image/heic"}


def _boundaries(fh, sep):
    """Byte offsets of every boundary in the spool file, found with a sliding window.

    Never holds more than CHUNK + len(sep) bytes, so a 40 GB upload costs the same memory as
    a 40 KB one. This is the whole reason the size cap could go away.
    """
    offsets, carry, base = [], b"", 0
    fh.seek(0)
    while True:
        chunk = fh.read(CHUNK)
        if not chunk:
            break
        buf = carry + chunk
        start = 0
        while True:
            i = buf.find(sep, start)
            if i < 0:
                break
            offsets.append(base + i)
            start = i + 1
        keep = len(sep) - 1
        carry = buf[-keep:] if keep > 0 else b""
        base += len(buf) - len(carry)
    return offsets


def parse_multipart_spooled(fh, content_type, dest_dir, name_for):
    """Parse a spooled multipart body, writing file parts straight to disk.

    Hand-rolled because `cgi` is deprecated in 3.12 and gone in 3.13, and this tool does not
    acquire a framework to read a file upload.

    Returns (fields, files): fields is {name: [str]}; files is a list of
    (field_name, client_filename, saved_path, size). Small text fields are read into memory
    because they are small by definition; file parts never are.
    """
    m = re.search(r'boundary="?([^";]+)"?', content_type or "")
    if not m:
        return {}, []
    sep = b"--" + m.group(1).encode()
    marks = _boundaries(fh, sep)
    fields, files = {}, []

    for a, b in zip(marks, marks[1:]):
        start = a + len(sep)
        fh.seek(start)
        head_blob = fh.read(min(8192, b - start))
        j = head_blob.find(b"\r\n\r\n")
        if j < 0:
            continue
        head = head_blob[:j].decode("utf-8", "replace")
        name = re.search(r'name="([^"]*)"', head)
        if not name:
            continue
        body_start = start + j + 4
        body_end = b - 2                       # trailing CRLF before the next boundary
        if body_end <= body_start:
            continue

        fname = re.search(r'filename="([^"]*)"', head)
        if fname and fname.group(1):
            out = dest_dir / name_for(fname.group(1))
            written = 0
            fh.seek(body_start)
            with open(out, "wb") as w:
                remaining = body_end - body_start
                while remaining > 0:
                    block = fh.read(min(CHUNK, remaining))
                    if not block:
                        break
                    w.write(block)
                    remaining -= len(block)
                    written += len(block)
            if written:
                files.append((name.group(1), fname.group(1), out, written))
            else:
                out.unlink(missing_ok=True)
        else:
            fh.seek(body_start)
            fields.setdefault(name.group(1), []).append(
                fh.read(body_end - body_start).decode("utf-8", "replace"))
    return fields, files


def safe_ext(client_name):
    """A sanitised extension. NOT an allow-list any more -- see save_uploads."""
    ext = Path(client_name).suffix.lower()
    ext = re.sub(r"[^a-z0-9.]", "", ext)[:12]
    return ext if ext.startswith(".") and len(ext) > 1 else ".bin"


def save_uploads(files, task_id):  # noqa: C901 -- see the docstring below
    """Full size, into workspace/attachments/, which is OUTSIDE git. Nothing is dropped.

    The server names every file -- a caller that chooses paths is a caller that can write
    anywhere (ROADMAP G1) -- and the extension is sanitised rather than trusted.

    IT USED TO SILENTLY DISCARD anything that was not an image: `if ext not in IMAGE_EXT:
    continue`. On 2026-08-21 Jas uploaded his entire Anthropic data export through this
    path. The form returned 303, said "Saved. Thank you.", created the task, and threw the
    file away. He believed for three days that the export was here.

    That is the same defect as t040 -- input accepted, silently truncated to nothing --
    fixed on the title field and reintroduced twelve lines away on the file field. An
    allow-list that DROPS rather than REFUSES is a data-loss bug wearing safety clothing.

    Serving is where the type actually matters, and /att/ already sends an unknown type as
    application/octet-stream, which downloads rather than executes.
    """
    saved = []
    ts.ATTACH.mkdir(parents=True, exist_ok=True)
    for _, client_name, tmp_path, size in files:
        ext = safe_ext(client_name)
        n = 1
        while (ts.ATTACH / f"{task_id}-{n:02d}{ext}").exists():
            n += 1
        out = ts.ATTACH / f"{task_id}-{n:02d}{ext}"
        os.replace(tmp_path, out)          # a rename, not a copy: no second full-size read
        ts.add_attachment(task_id, f"{out.name}  (was {Path(client_name).name}, "
                                   f"{size:,} bytes)")
        saved.append(out.name)
    return saved


# --------------------------------------------------------------------------- Plan section

STATUS_NEXT = {"queued": "doing", "doing": "blocked", "blocked": "queued", "done": "queued"}


def chip(status):
    return f'<span class="chip s-{status}">{status}</span>'


def _assignable():
    """[(role key, name)] for the "Assign to:" dropdown, from the role store, in its order: Claude,
    then Jas, then every other role alphabetically (Jas, 2026-09-29). Grows as roles are added."""
    try:
        if str(WORKSPACE / "scripts") not in sys.path:
            sys.path.insert(0, str(WORKSPACE / "scripts"))
        import role_store
        d = role_store.load()
        # ONLY ROLES THAT TAKE ASSIGNED WORK (Jas, 2026-10-07: "I took the assign to task away from all
        # but the manager. I shouldn't see anything else ... dynamic when roles have that task or not").
        def takes(k):
            return any(str(t.get("task", "")).startswith("assigned_") for t in d["roles"][k].get("tasks", []))
        return [(k, d["roles"][k].get("name", k)) for k in role_store.assign_order(d) if takes(k)]
    except Exception as e:                                          # noqa: BLE001
        print(f"  NOTE: role store unreadable for Assign to: {e}", file=sys.stderr)
        return []        # no stale fallback (Jas, 2026-10-19): an unreadable chart offers no one


def hold_value(raw):
    """The role a task is assigned to, if `raw` names one; else Production (2026-10-03: there is no
    'nobody' -- unassigned work IS production work)."""
    return raw if raw in {k for k, _ in _assignable()} else ts.PRODUCTION


def hold_select(current):
    """The "Assign to:" dropdown (was Hold for Jas / Hold for Claude). A value the store does not
    know -- scope_queue's internal "model" -- is kept as the selection, never silently cleared."""
    roles = _assignable()
    prod = ts.assignee(current) == ts.PRODUCTION
    opts = [f'<option value="{ts.PRODUCTION}"' + (' selected' if prod else '') + '>Production</option>']
    current = "" if prod else current
    opts += [f'<option value="{esc(k)}"' + (' selected' if current == k else '') + f'>{esc(n)}</option>'
             for k, n in roles]
    if current and current not in {k for k, _ in roles}:
        opts.append(f'<option value="{esc(current)}" selected>{esc(current)} (current)</option>')
    return f'Assign to: <select name="hold">{"".join(opts)}</select>'


def wake_form(tid, meta):
    """Defer a task to a date, on the task screen (I2).

    Jas, 2026-10-26: "I want to confirm you will add that to the task screen so I can edit it as well. I
    imagine for me it will be a calendar pop up." `<input type="date">` IS that pop-up -- the browser
    supplies it, it posts exactly the YYYY-MM-DD the store insists on, and it needs no script.

    Two forms, not one: `wake` is an optional key, so the control has to be able to say "no". A field you
    can set but never clear is a field that fills up with dates nobody meant to keep.

    It is a DEFERRAL, not a schedule: nothing runs on the date; session_start.py prints the task at the
    top of the next session after it.
    """
    cur = str((meta or {}).get("wake") or "")
    state = (f'Deferred to <b>{esc(cur)}</b> &mdash; session_start.py will surface it from that date.'
             if cur else "Not deferred.")
    return (f'<form class="inline" method="POST" action="/plan/wake">'
            f'<input type="hidden" name="id" value="{esc(tid)}">'
            f'<input type="date" name="date" value="{esc(cur)}" aria-label="wake date">'
            f'<button class="b sm" title="The date the queue resurfaces this task">'
            f'{"Change" if cur else "Defer"}</button></form>'
            + (f'<form class="inline" method="POST" action="/plan/wake">'
               f'<input type="hidden" name="id" value="{esc(tid)}">'
               f'<input type="hidden" name="date" value="">'
               f'<button class="b sm ghost" title="Remove the date">Clear</button></form>' if cur else '')
            + f'<span class="small">{state}</span>')


def move_form(tid, where, label, target=None, title=""):
    extra = f'<input type="hidden" name="target" value="{esc(target)}">' if target else ""
    return (f'<form class="inline" method="POST" action="/plan/move">'
            f'<input type="hidden" name="id" value="{esc(tid)}">'
            f'<input type="hidden" name="where" value="{where}">{extra}'
            f'<button class="b sm" title="{esc(title)}">{label}</button></form>')


def waiting_chip(meta, body=""):
    """The only piece of my filing that earns space on Jas's screen.

    Two states, not one. A task he has replied to still shows -- labelled so he can see the
    ball is in my court -- rather than vanishing the moment he types. It used to vanish.
    """
    if ts.tier_of(meta.get("needs")) != "vision":
        return ""
    if body and ts.last_speaker(body) == "jas":
        return '&nbsp;<span class="chip s-replied">&#10003; you replied</span>'
    return '&nbsp;<span class="chip s-waiting">&#9873; you</span>'


def add_form():
    # No dropdowns. Jas types text; Claude Code files it. His instruction, 2026-08-21:
    # "I want to put ideas in, then let you add whatever meta data you want."
    return f"""<form class="section" method="POST" action="/plan/add"
        enctype="multipart/form-data">
  <h2>Add something</h2>
  <div class="small">A short name, then as much detail as you like. You still never have to
  pick a category &mdash; I do the filing.</div>
  <label class="f">Name it &mdash; keep it short</label>
  <input type="text" name="title" required
         placeholder="The play button does not pause">
  <div class="small">Longer than a line is fine &mdash; I shorten the name and keep every
  word in the body.</div>
  <label class="f">Everything else</label>
  <textarea name="text"
            placeholder="What happened, when, what you expected instead..."></textarea>
  <label class="f">Attach anything &mdash; several at once is fine</label>
  <input type="file" name="shots" multiple>
  <label class="f"><input type="checkbox" name="top" value="1">
    Urgent &mdash; put it straight at the top</label>
  <label class="f">{hold_select("")}
    &mdash; e.g. Jas for an idea to come back to, not work to start</label>
  <div class="actionbar">
    <button class="wide" type="submit">Add to the plan</button>
    <a class="cancel" href="/plan">Cancel</a>
  </div>
</form>"""


# --------------------------------------------------------------------------- status

# THE REVIEW TAB MUST NOT WAIT ON hivestatus. Jas, 2026-09-25: "The status tab loads right away
# ... Why can't it be instant?" Each /review ran hivestatus.py (~22 s) inline, and his phone gave
# up. The page reads the last result and its age; a stale one refreshes in the background, on demand.
_CS = {"d": None, "at": 0.0, "err": ""}
_CS_LOCK = threading.Lock()
_CS_STARTED = False


def _refresh_claude_status():
    """ONE run of hivestatus for the Review tab's panel, then the thread ends.

    ON DEMAND, NOT ON A TIMER (Jas, 2026-10-03: "if they are constantly doing something even when
    nobody is on those pages ... it pulls things when I get there"). This looped every 3 h for the
    life of the Hub, ~22 s of work each time whether or not anyone looked."""
    global _CS_STARTED
    import subprocess as _sp
    try:
        r = _sp.run([sys.executable, str(WORKSPACE / "scripts" / "hivestatus.py"), "--json",
                     "--only", "production,watchers,pending"],
                    capture_output=True, text=True, timeout=600, stdin=_sp.DEVNULL,
                    cwd=str(WORKSPACE))
        d = json.loads(r.stdout or "{}")
        with _CS_LOCK:
            _CS.update(d=d, at=time.time(), err="")
    except Exception as e:                                   # noqa: BLE001
        with _CS_LOCK:
            _CS["err"] = f"{type(e).__name__}: {e}"
    finally:
        with _CS_LOCK:
            _CS_STARTED = False


CS_STALE_S = 600


def _claude_status():
    """The last result at once; a refresh starts in the background only when a page asks and the
    result is over 10 minutes old (or missing). Nobody looking means nothing runs."""
    global _CS_STARTED
    with _CS_LOCK:
        if not _CS_STARTED and time.time() - _CS["at"] > CS_STALE_S:
            _CS_STARTED = True
            threading.Thread(target=_refresh_claude_status, daemon=True).start()
        return _CS["d"], _CS["at"], _CS["err"]


def waiting_for_claude(bare=False):
    """What is on MY side of the line. Jas, 2026-09-14: "If its done and I don't need to worry
    about it and it will appear in your part of the review tab, remove it from the status page
    and put it in the review tab under Waiting for Claude."

    Derived on read like everything else. Three sources, and each is work he should be able to
    IGNORE -- which is the point: if it is here, it is not his problem, and nothing here is red.
    """
    rows = []
    d, at, err = _claude_status()
    if d is None:
        h = "" if bare else "<h2>Waiting for Claude</h2>"
        if err:
            return (f'{h}<div class="empty">Could not derive the summary: '
                    f'{html.escape(err)}. That is not "nothing to do".</div>')
        return (f'{h}<div class="empty">Still collecting the summary (about 20 s after the Hub '
                'starts). Reload in a moment.</div>')
    age = int(time.time() - at)
    stamp = (f'<p class="small">As of {age} s ago'
             + (f' &mdash; last refresh failed: {html.escape(err)}' if err else '') + '.</p>')

    br = (d.get("production") or {}).get("branches_awaiting_review") or []
    if br:
        rows.append(f"<b>{len(br)} delivered branch(es)</b> to gate and merge — "
                    + html.escape(", ".join(br)))
    out_chunks = [c for c in ((d.get("production") or {}).get("chunks") or [])
                  if c.get("target_green") is False]
    if out_chunks:
        rows.append(f"<b>{len(out_chunks)} chunk(s) outstanding</b> — "
                    + html.escape(", ".join(c["chunk"] for c in out_chunks))
                    + " (a model is working these, or can pick them up)")
    # HUNG ONLY, not leftovers -- see the Watchers section for why the two are different.
    # A summary line that counts dead files as stalls puts a number in front of Jas that no
    # action follows from, and he has asked for the opposite: if he can ignore it, do not
    # put it where something needing him lives.
    stale = [w for w in (d.get("watchers") or [])
             if w.get("stale") and not w.get("process_gone")
             and str(w.get("state", "")).lower() in ("running", "doing")]
    if stale:
        rows.append(f"<b>{len(stale)} HUNG watcher(s)</b> — "
                    + html.escape(", ".join(str(w.get("name")) for w in stale))
                    + " — the process is alive and has stopped beating; it may still hold a model")
    pend = d.get("pending") or []
    spent = [x for x in pend if x.get("kind") == "spent"]
    if spent:
        rows.append(f"<b>{len(spent)} spent brief(s)</b> whose work is already on main — "
                    "mine to archive")

    if not rows:
        return "" if bare else ('<h2>Waiting for Claude</h2>' + stamp +
                '<div class="card"><p class="small">Nothing on my side either.</p></div>')
    items = "".join(f"<li>{r}</li>" for r in rows)
    if bare:
        return f'{stamp}<div class="card"><ul>{items}</ul></div>'
    return (f'<h2>Waiting for Claude</h2>'
            f'<p class="small">None of this needs you. It is here so you can see it is '
            f'handled rather than missing.</p>{stamp}<div class="card"><ul>{items}</ul></div>')


def _hms(sec):
    if sec is None:
        return "?"
    sec = int(sec)
    return f"{sec // 3600}h{(sec % 3600) // 60:02d}m" if sec >= 3600 else f"{sec // 60}m{sec % 60:02d}s"


def _lanes_html(pr):
    """One card per running coder.py lane, from lane_status.snapshot() via hivestatus.

    THREE SIGNALS, SHOWN APART. Log still growing proves the harness is alive; GPU
    busy proves something is decoding but not which lane; the model being resident
    proves the weights are loaded. Collapsing them into one green light is how "the
    GPU is at 95%, so it is fine" got said on 2026-09-14 about two lanes that were
    copies of each other.

    A QUIET LOG IS AMBER, NOT RED. coder.py writes nothing while a model call is in
    progress, and one bf16 attempt can take 1h40m, so silence mid-attempt is normal.
    Red is reserved for what is actually wrong: no log at all, or a duplicate.
    """
    if pr.get("lanes_error"):
        return (f'<div class="bad">Lane detail unavailable: '
                f'{html.escape(pr["lanes_error"])}</div>')
    lanes = pr.get("lanes") or []
    out = ["<h3>Lanes</h3>"]
    if not lanes:
        out.append('<div class="empty">No coder.py lane is running.</div>')
        return "\n".join(out)
    for r in lanes:
        dup = r.get("duplicate_of") or []
        if dup:
            cls, head = "bad", f"DUPLICATE of pid {', '.join(map(str, dup))}"
        elif r.get("log") is None:
            cls, head = "bad", "no live log — cannot tell whether it is moving"
        elif r.get("log_growing"):
            cls, head = "ok", "log growing"
        else:
            cls, head = "warn", "log quiet — normal mid-attempt; the GPU figure above says whether it is decoding"
        att = r.get("attempt")
        rate = ""
        if r.get("rate_median"):
            rate = (f'<br>decode {r["rate_median"]} tok/s median '
                    f'({r["rate_lo"]}–{r["rate_hi"]}, {r["rate_runs"]} runs) · a maxed attempt '
                    f'~{_hms(r["attempt_s_median"])} (range {_hms(r["attempt_s_lo"])}–'
                    f'{_hms(r["attempt_s_hi"])})')
        out.append(
            f'<div class="{cls}"><b>pid {r["pid"]}</b> · {html.escape(str(r["model"]))} · '
            f'{_hms(r.get("elapsed_s"))} elapsed · '
            f'{"attempt " + str(att) if att else "attempt not yet announced"}'
            f'<br><b>{html.escape(head)}</b>'
            f' · last write {_hms(r.get("log_age_s"))} ago'
            f'<br>brief <code>{html.escape(str(r["brief"]))}</code> → target '
            f'<code>{html.escape(str(r["target"]))}</code>'
            f'<br><span class="rank">{html.escape(str(r.get("last_line") or "")[:160])}</span>'
            f'{rate}</div>')
    return "\n".join(out)


# ============================================================ the Corpus tab
#
# Jas, 2026-09-15: "make a tab in the Hub called Corpus ... a sub section called models. Copy the
# layout of the apps tab and how we have pills for each app section ... then do this exercise
# again for scripts ... list what they all do and clarify if any are reoccurring scripts or one
# time use ... scheduled to run or just pulled on demand."
#
# Models: the written notes live in workspace/corpus/model-notes.json (checked 2026-09-15); the
# facts that change -- size, precision, context, what is baked into the Modelfile, what the harness
# actually sends, whether it is in memory -- are read from Ollama and settings.py on load, so the
# page cannot drift from the box. Scripts: workspace/corpus/scripts.json from
# scripts/corpus_scripts.py, where HOW a script runs comes from crontab/systemd/code and only the
# plain description (and on-demand / one-time / dead) is the model's reading.
CORPUS = WORKSPACE / "workspace" / "corpus"
_OLLAMA_FACTS = {"at": 0, "data": {}}


def _ollama_facts():
    import time as _t
    import urllib.request as _u
    if _t.time() - _OLLAMA_FACTS["at"] < 300:
        return _OLLAMA_FACTS["data"]
    out = {}
    try:
        def get(path, body=None):
            req = _u.Request("http://127.0.0.1:11434" + path, json.dumps(body).encode() if body else None,
                             {"Content-Type": "application/json"})
            with _u.urlopen(req, timeout=10) as f:
                return json.load(f)
        # IN MEMORY ON WHICH SERVER (plan step 5): the main Ollama and every role's own station.
        resident = {}
        sys.path.insert(0, str(WORKSPACE / "scripts"))
        import role_store as _rs
        for ep in _rs.endpoints():
            try:
                req = _u.Request(ep + "/api/ps")
                with _u.urlopen(req, timeout=5) as f:
                    for m in json.load(f).get("models", []):
                        resident.setdefault(m["name"], []).append(ep.rsplit(":", 1)[-1])
            except Exception as e:                                  # noqa: BLE001
                resident.setdefault("_errors", []).append(f"{ep}: {type(e).__name__}")
        sys.path.insert(0, str(WORKSPACE / "projects" / "local-inference"))
        import settings as _settings
        for m in get("/api/tags").get("models", []):
            show = get("/api/show", {"model": m["name"]})
            info = show.get("model_info", {})
            try:
                sent = _settings.options("coder", m["name"])
            except Exception as e:                                  # noqa: BLE001
                sent = {"error": str(e)}
            out[m["name"]] = {
                "size": f"{m['size'] / 1e9:.1f} GB",
                "precision": f"{m['details'].get('parameter_size')} · {m['details'].get('quantization_level')}",
                "parent": m["details"].get("parent_model") or "",
                "context": next((f"{v:,} tokens" for k, v in info.items() if k.endswith("context_length")), "unknown"),
                "can": ", ".join(show.get("capabilities") or []),
                "baked": " · ".join(" ".join(l.split()) for l in (show.get("parameters") or "").splitlines()),
                "sent": ", ".join(f"{k} {v}" for k, v in sent.items()),
                "resident": resident.get(m["name"], [])}
        out["_ps_errors"] = resident.get("_errors", [])
    except Exception as e:                                          # noqa: BLE001
        out = {"_error": f"{type(e).__name__}: {e}"}
    _OLLAMA_FACTS.update(at=_t.time(), data=out)
    return out


def _corpus_issues(issues):
    if not issues:
        return '<p class="small">No known problems recorded.</p>'
    return "".join(f'<p class="corpus-issue {k}"><b>{"Broken or risky" if k == "hot" else "Contradiction or gap"}:</b> {t}</p>'
                   for k, t in issues)


def _corpus_models():
    try:
        notes = json.loads((CORPUS / "model-notes.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return f'<div class="bad">Model notes unreadable: {html.escape(str(e))}</div>'
    live = _ollama_facts()
    out = [f'<div class="card"><p>Every model on the box: the Models page, the Web UI list, and the helpers neither shows. '
           f'Written notes checked {html.escape(notes["checked"])}; sizes, settings and "in memory" are read live from Ollama.</p>'
           f'<h4 style="margin:.6rem 0 .2rem">Hidden-model check</h4><p class="small">{notes["hidden_check"]}</p>'
           f'<h4 style="margin:.6rem 0 .2rem">Why the sizes differ between pages</h4><p class="small">{notes["sizes_note"]}</p></div>']
    if live.get("_error"):
        out.append(f'<div class="bad">Live facts unavailable: {html.escape(live["_error"])}. Showing written notes only -- this is NOT a check that the models are fine.</div>')
    # LIVE, NOT WRITTEN (plan step 5, 2026-09-30): an Ollama model's status and group come from the
    # role store -- "in use by Manager, Senior" -- and whether it is in memory from every server. The
    # written chips went stale (Q4's said "removed from lanes" while it held three roles).
    try:
        sys.path.insert(0, str(WORKSPACE / "scripts"))
        import role_store as _rs
        _roles = _rs.load()["roles"]
        users = {}
        for k, r in _roles.items():
            if r.get("model"):
                users.setdefault(r["model"], []).append(r.get("name", k))
    except Exception as e:                                          # noqa: BLE001
        users = None
        out.append(f'<div class="bad">Role store unreadable ({html.escape(str(e))}): "in use by" is not shown.</div>')
    if live.get("_ps_errors"):
        out.append(f'<div class="bad">Could not ask these servers what is in memory: {html.escape(", ".join(live["_ps_errors"]))}</div>')
    entries_all = [dict(e) for e in notes["entries"]]
    noted = {e["tag"] for e in entries_all}
    for tag in sorted(t for t in live if not t.startswith("_") and t not in noted):
        entries_all.append({"tag": tag, "source": "ollama", "group": "hidden", "what": "No written notes yet.",
                            "role": "", "speed": "Not measured here yet.", "src": "Ollama, read live"})
    for e in entries_all:
        if e.get("source") != "ollama":
            continue
        f = live.get(e["tag"], {})
        who = (users or {}).get(e["tag"], [])
        if not who and e.get("group") == "pipeline" and users is not None:
            e["group"] = "candidates"            # no role uses it: not "in the pipeline now"
        if who:
            e["status"], e["group"] = [f"in use: {', '.join(who)}", "leaf"], "pipeline"
        elif not f and not live.get("_error"):
            e["status"] = ["not on this box", "hot"]
        elif f.get("resident"):
            e["status"] = ["in memory, no role", "gold"]
        else:
            e["status"] = ["on disk, no role", "cool"]
    for gid, gname in notes["groups"]:
        entries = [e for e in entries_all if e.get("group") == gid]
        if not entries:
            continue
        out.append(f"<h2>{html.escape(gname)} <span class=\"small\">({len(entries)})</span></h2>")
        for e in entries:
            label, tone = e["status"]
            nick = f' <span class="small">{html.escape(e["nick"])}</span>' if e.get("nick") else ""
            if e["source"] == "ollama":
                f = live.get(e["tag"], {})
                run = notes["ollama_run"]
                rows = [("Size on disk", f.get("size", "?")), ("Parameters · precision", f.get("precision", "?")),
                        ("Built from", f.get("parent") or "its own download"), ("Trained context", f.get("context", "?")),
                        ("Can do", f.get("can", "?")), ("Baked into its Modelfile", f.get("baked") or "nothing -- Ollama defaults apply"),
                        ("What the harness sends", f.get("sent", "?")),
                        ("In memory now", ("yes, on :" + ", :".join(f["resident"])) if f.get("resident")
                         else ("no" if f else "not found in Ollama"))]
                dl = "".join(f"<dt>{html.escape(k)}</dt><dd><code>{html.escape(v)}</code></dd>" if k in ("Baked into its Modelfile", "What the harness sends")
                             else f"<dt>{html.escape(k)}</dt><dd>{html.escape(v)}</dd>" for k, v in rows)
            elif e["source"] == "vllm":
                run = notes["vllm_run"]
                dl = "".join(f"<dt>{html.escape(k)}</dt><dd>{v}</dd>" for k, v in e.get("facts", []))
            else:
                run = "Not served by Ollama or vLLM on this box." if gid == "other" else "Runs inside the program that downloaded it; nothing in the pipeline calls it."
                dl = "<dt>Settings</dt><dd>none -- not a model this box serves</dd>"
            out.append(f"""<div class="card corpus-card">
  <div class="head"><h3><code>{html.escape(e["tag"])}</code>{nick}</h3><span class="corpus-chip {tone}">{html.escape(label)}</span></div>
  <h4>What it is</h4><p>{e["what"]}</p>
  <h4>What it's for</h4><p>{e["role"]}</p>
  <h4>How it runs</h4><p class="small">{run}</p>
  <h4>Settings</h4><dl>{dl}</dl>
  <h4>Speed and memory</h4><p>{e["speed"]}</p>
  <h4>Problems</h4>{_corpus_issues(e.get("issues"))}
  <p class="corpus-src">Sources: {html.escape(e.get("src", ""))}</p>
</div>""")
    return "\n".join(out)


_RUN_LABEL = {"scheduled": ("scheduled", "leaf"), "service": ("always running", "leaf"),
              "triggered": ("triggered", "leaf"), "on-demand": ("on demand", "cool"),
              "one-time": ("one-time", "gold"), "dead": ("dead", "hot"), "test": ("test", "cool"),
              "unclassified": ("not yet described", "gold")}
_RUN_ORDER = ["scheduled", "service", "triggered", "on-demand", "one-time", "dead", "unclassified", "test"]
_RUN_MEANS = {"scheduled": "Runs on a timer (cron or systemd) -- the schedule is shown.",
              "service": "A systemd service: always running, or started as a unit.",
              "triggered": "Runs when something happens: a git hook, the scheduler starting a job, session start or end.",
              "on-demand": "Reusable; run by a person or another tool when needed. The model's reading.",
              "one-time": "Did a single job and is not meant to run again. The model's reading.",
              "dead": "Nothing references it and it no longer fits the system. The model's reading.",
              "unclassified": "Gemma has not described it yet.",
              "test": "A test suite: run before every production job and by hand."}


def _corpus_scripts(run_filter):
    try:
        data = json.loads((CORPUS / "scripts.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return f'<div class="bad">Script list unreadable: {html.escape(str(e))}. Run <code>python3 scripts/corpus_scripts.py</code>.</div>'
    rows = list(data["scripts"].values())
    from collections import Counter
    counts = Counter(r.get("run") for r in rows)
    pills = [f'<a class="{"on" if not run_filter else ""}" href="/corpus?section=scripts">All ({len(rows)})</a>']
    pills += [f'<a class="{"on" if run_filter == k else ""}" href="/corpus?section=scripts&amp;run={k}">'
              f'{html.escape(_RUN_LABEL[k][0].capitalize())} ({counts[k]})</a>' for k in _RUN_ORDER if counts.get(k)]
    described = sum(1 for r in rows if r.get("does"))
    out = [f'<nav class="pills subpills">{"".join(pills)}</nav>',
           f'<div class="card"><p>Every script in the script library ({len(rows)}). <b>How it runs</b> for scheduled, '
           f'always-running, triggered and test scripts is read from the crontab, systemd and the code. <b>What it does</b>, and '
           f'whether an unscheduled script is on demand, one-time or dead, was written by <b>{html.escape(data.get("model", "the auditor model"))}</b> '
           f'and has not been checked by Claude yet. {described} of {len(rows)} described so far; list built {html.escape(data.get("generated", "?"))}.</p></div>']
    shown = [r for r in rows if not run_filter or r.get("run") == run_filter]
    for k in _RUN_ORDER:
        group = sorted((r for r in shown if r.get("run") == k), key=lambda r: r["path"])
        if not group:
            continue
        out.append(f'<h2>{html.escape(_RUN_LABEL[k][0].capitalize())} <span class="small">({len(group)})</span></h2>'
                   f'<p class="small">{html.escape(_RUN_MEANS[k])}</p>')
        for r in group:
            label, tone = _RUN_LABEL[k]
            does = r.get("does") or r.get("library_description") or ""
            who = (f' <span class="small">(written by {html.escape(r["described_by"])}, not checked)</span>'
                   if r.get("described_by") and r.get("does") else "")
            detail = r.get("run_detail") or ""
            out.append(f"""<div class="card corpus-card">
  <div class="head"><h3><code>{html.escape(r["path"])}</code></h3><span class="corpus-chip {tone}">{html.escape(label)}</span></div>
  <p style="margin-top:.4rem">{html.escape(does)}{who}</p>
  {(f'<p class="corpus-issue gold"><b>Second opinion, Q16:</b> {html.escape(r["q16_run"])} -- {html.escape(r.get("q16_why", ""))}</p>') if r.get("q16_run") else ""}
  <dl style="margin-top:.5rem">{f"<dt>How it runs</dt><dd>{html.escape(detail)} <span class='small'>({html.escape(r.get('run_source', ''))})</span></dd>" if detail else ""}
  <dt>Called by</dt><dd>{r.get("referenced_count", 0)} script(s){(": " + html.escape(", ".join(r.get("referenced_by", [])[:3]))) if r.get("referenced_by") else " -- nothing in the code runs it"}</dd>
  <dt>Mentioned in</dt><dd>{r.get("mentioned_in_docs", 0)} document(s)</dd>
  <dt>Changed</dt><dd>{r.get("commits", 0)} commit(s), last {html.escape(str(r.get("last_changed") or "never committed"))}</dd>
  <dt>Size</dt><dd>{r.get("lines", 0)} lines · library kind: {html.escape(str(r.get("kind")))}</dd></dl>
</div>""")
    return "\n".join(out)


def script_detail_html(path, esc=None):
    """ONE script explained from its own comments, and the numbers a person can change (Jas, 2026-10-30).

    "I want every script in there to be something I can tap on and adjust the variables. I want boxes that I can
    manipulate and I want a How this page works that derives the functions of each script from the comments."

    THE DESCRIPTION IS DERIVED, NOT WRITTEN AGAIN: script_settings.describe() reads the module docstring for the
    purpose and each def's docstring (or the comment above it) for its summary, so this page cannot drift from the
    file. ON A SHELL SCRIPT it reads the shell's own conventions instead, which is how the scoping loop is explained.

    READ-ONLY HERE, ON PURPOSE. The write path (set_knob.py) exists and refuses an expression, a wrong type and an
    unparsable edit; wiring a box to it needs a restart-confirm and a read-back, and this function does not pretend to
    have them.
    """
    _esc = esc or (lambda x: str(x).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
    try:
        sys.path.insert(0, str(WORKSPACE / "scripts"))
        import script_settings as ss
    except Exception as e:                                          # noqa: BLE001 -- said, not swallowed
        return f'<div class="empty">script_settings unreadable: {_esc(type(e).__name__ + ": " + str(e))}</div>'
    d = ss.describe(WORKSPACE / path)
    out = [f'<div class="card"><h2>{_esc(path)}</h2>']
    out.append(f'<p>{_esc(d["purpose"] or "(no description in the file)")}</p>')
    if d.get("knobs"):
        out.append('<p class="small">Set one and it is written to the file, then READ BACK from the file. A running '
                   'script keeps the value it started with until it is restarted.</p>')
        out.append('<table class="t"><tr><th>setting</th><th>value</th><th>line</th><th>what it is for</th></tr>')
        for k in d["knobs"]:
            out.append(f'<tr><td><code>{_esc(k["name"])}</code></td>'
                       f'<td><form method="post" action="/scripts/set" style="margin:0">'
                       f'<input type="hidden" name="script" value="{_esc(path)}">'
                       f'<input type="hidden" name="name" value="{_esc(k["name"])}">'
                       f'<input name="value" value="{_esc(str(k["value"]))}" style="width:9rem">'
                       f' <button type="submit">set</button></form></td>'
                       f'<td class="small">{k["line"]}</td><td class="small">{_esc(k["summary"] or "")}</td></tr>')
        out.append("</table>")
    if d.get("flags"):
        out.append('<p class="small">numbers it runs with: '
                   + ", ".join(f'<code>{_esc(f["flag"])} {_esc(f["value"])}</code>' for f in d["flags"][:12]) + "</p>")
    if d.get("functions"):
        out.append("<h3>what it holds</h3><ul>")
        for f in d["functions"][:40]:
            out.append(f'<li><code>{_esc(f["name"])}</code> <span class="small">{_esc(f["summary"] or "")}</span></li>')
        out.append("</ul>")
    out.append("</div>")
    return "".join(out)


def apply_knob(script, name, value, library_only=True):
    """Change ONE knob from a box, then READ IT BACK from the file. -> (ok, message, value_now).

    THE BOX JAS ASKED FOR ("boxes that I can manipulate"), with the two guards a box needs and a terminal does not:

      * LIBRARY ONLY -- a path that the script library does not list is refused, so a crafted form cannot edit the Hub
        itself or anything else on the box;
      * READ-BACK -- the value reported is what the file says AFTER the write, not what was submitted. A page that
        echoes the request is not evidence the change landed (the guard-tiers page had exactly that fault and its own
        help text says so).

    THE RESTART IS THE CALLER'S BUSINESS, and this function says whether one is needed rather than pretending it did
    it: set_knob edits a literal, and a RUNNING script keeps the value it started with.
    """
    try:
        sys.path.insert(0, str(WORKSPACE / "scripts"))
        import script_settings as ss, set_knob as sk
    except Exception as e:                                          # noqa: BLE001 -- said, not swallowed
        return False, f"could not load the setting tools ({type(e).__name__}: {e})", None
    script = (script or "").strip()
    # NEVER THE HUB'S OWN CODE (2026-10-30, found by trying it): every projects/*.py is IN the script library, so a
    # "library only" check let a crafted form edit feedback.py -- the page that is serving the form, while it runs.
    # A page may change the tools it shows; it may not rewrite itself.
    try:
        resolved = (WORKSPACE / script).resolve()
        under = resolved.is_relative_to((WORKSPACE / "scripts").resolve())
    except (OSError, ValueError):
        under = False
    if not script.startswith("scripts/") or not under or ".." in Path(script).parts:
        return False, (f"{script or '(empty)'} is not under scripts/ -- a page changes the tools it shows, not the "
                       f"program it runs"), None
    if library_only:
        known = {e["path"] for e in ss.library()}
        if script not in known:
            return False, f"{script} is not in the script library, so it cannot be changed from a page", None
    path = WORKSPACE / script
    before = sk.set_knob(path, name, value, write=False)
    if not before[0]:
        return False, before[1], None
    ok, msg, _ = sk.set_knob(path, name, value)
    if not ok:
        return False, msg, None
    after = ss.describe(path)
    now = next((k["value"] for k in after["knobs"] if k["name"] == name), None)
    if repr(now) != repr(_literal_of(value, before[2], name)):
        return False, (f"{name} was written but the file reads back {now!r} -- the change did not take"), now
    return True, f"{name} = {now!r}", now


def _literal_of(value, new_text, name):
    """The value the edited text says, read back through the same describer the page uses."""
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False, encoding="utf-8") as fh:
        fh.write(new_text)
        tmp = fh.name
    try:
        import script_settings as ss
        for k in ss.describe(tmp)["knobs"]:
            if k["name"] == name:
                return k["value"]
        return None
    finally:
        try:
            os.unlink(tmp)
        except OSError as e:                                        # noqa: BLE001 -- said, not swallowed
            print(f"  NOTE: could not remove the read-back temp file {tmp} ({type(e).__name__}: {e})",
                  file=sys.stderr)


def scripts_page(run_filter=""):
    """The Scripts tab: what Q4 is doing to the scripts, then every script.

    Jas, 2026-09-26: "I would like to be able to see everything. Can scripts be visible in its
    own tab?" -- both halves. Script reviews (scripts/truncation_review.py, queued by the
    dispatcher after a script changes) ran on the manager's model with no page showing them;
    they are read here from the dispatch ledger and workspace/truncation-review.jsonl. The
    library below is the Corpus tab's script list, unchanged."""
    # Running and queued reviews moved to Production and the Backlog (Jas, 2026-09-29); this tab keeps
    # the finished ones and the library.
    out = []
    # A SCRIPT NAMED ON THE QUERY STRING IS EXPLAINED FIRST (Jas, 2026-10-30): /scripts?run= takes a path here.
    if run_filter and run_filter.endswith((".py", ".sh")):
        out.append(script_detail_html(run_filter, esc))
    bad = 0
    rows = []
    try:
        for line in (WORKSPACE / "workspace" / "truncation-review.jsonl").read_text(
                encoding="utf-8", errors="replace").splitlines():
            try:
                rows.append(json.loads(line))
            except ValueError:
                bad += 1
                continue
    except OSError as e:
        out.append(f'<div class="empty">Review results unreadable: {esc(str(e))}</div>')
    out.append(f'<h2>Script reviews done ({len(rows)})</h2><p class="small">After a script changes, the '
               'dispatcher has the auditor model read it for silent truncation and swallowed errors. '
               'Newest first; what is running is on Production, what is queued is on the Backlog.</p>')
    for r in reversed(rows[-40:]):
        f = r.get("findings") or []
        sev = {}
        for x in f:
            sev[x.get("severity", "?")] = sev.get(x.get("severity", "?"), 0) + 1
        tally = ", ".join(f"{v} {esc(k)}" for k, v in sorted(sev.items())) or "no findings"
        items = "".join(f'<li><b>line {esc(str(x.get("line", "?")))}</b> '
                        f'<span class="small">{esc(x.get("severity", ""))} &middot; '
                        f'{esc(x.get("check", ""))}{" &middot; silent" if x.get("silent") else ""}'
                        f'</span><br>{esc((x.get("why") or "")[:400])}</li>' for x in f)
        out.append(f'<div class="card"><details><summary><b>{esc(r.get("path", "?"))}</b> '
                   f'<span class="small">{esc(str(r.get("at", ""))[5:16].replace("T", " "))}Z '
                   f'&middot; {esc(r.get("status", "?"))}'
                   f'{" (partial)" if r.get("partial") else ""} &middot; {tally} &middot; '
                   f'{esc(str(r.get("seconds", "?")))} s &middot; {esc(r.get("model", ""))}'
                   f'</span></summary><ul>{items or "<li>Nothing found.</li>"}</ul></details></div>')
    if not rows:
        out.append('<div class="small">No reviews recorded yet.</div>')
    out.append(f'<p class="small">{len(rows)} review(s) on record; the newest 40 are shown.'
               + (f' {bad} unreadable ledger line(s) skipped.' if bad else '') + '</p>')
    lib = _corpus_scripts(run_filter)
    m = re.search(r"script library \((\d+)\)", lib)
    out.append(f'<h2>Script library ({m.group(1) if m else "?"})</h2>')
    out.append(lib)
    return "".join(out)


def _jsonl_tail(path, n):
    """(rows, unreadable) from the last n lines of a JSONL file; ([], 0) if absent."""
    rows, bad = [], 0
    # FROM THE END, NOT THE WHOLE FILE (2026-10-03): the Misc page read three ledgers top to bottom
    # (~10 s) to keep their last n lines. Grow the window from the end until it holds n lines.
    try:
        with open(path, "rb") as f:
            size = f.seek(0, 2)
            want, data = 64 * 1024, b""
            while True:
                start = max(0, size - want)
                f.seek(start)
                data = f.read(size - start)
                if start == 0 or data.count(b"\n") > n:
                    break
                want *= 4
        lines = data.decode("utf-8", errors="replace").splitlines()
        if start > 0:
            lines = lines[1:]                           # the first one is cut by the window
        lines = lines[-n:]
    except OSError:
        return [], 0
    for line in lines:
        try:
            rows.append(json.loads(line))
        except ValueError:
            bad += 1
    return rows, bad


def misc_page():
    """Misc production: every other thing the models do (Jas, 2026-09-26: "Is there anything
    else they do that I don't see? ... I want to have eyes on everything").

    Five views, each read from the file the mechanism itself writes: what holds a model right
    now (lane_status), the dispatcher's non-build jobs (brief reviews, failure supervision,
    console answers), the 02:00 night queue's logs, model loads with who caused them
    (ollama_callers), and GPU time by harness over 24 h (run-telemetry). Builds, the manager and
    script reviews have their own tabs and are only counted here."""
    out, bad = [], 0
    # 1. right now
    out.append("<h2>Holding a model right now</h2>")
    try:
        import lane_status
        lanes = lane_status.snapshot()["lanes"]
    except Exception as e:                                          # noqa: BLE001
        lanes = []
        out.append(f'<div class="empty">lane_status failed: {esc(f"{type(e).__name__}: {e}")}</div>')
    for r in lanes:
        out.append(f'<div class="card"><b>{esc(r.get("kind", "?"))}</b> &middot; '
                   f'{esc(r.get("model", "?"))} &middot; {esc(str(r.get("brief") or r.get("script") or ""))}'
                   f' <span class="small">pid {esc(str(r.get("pid")))}, '
                   f'{esc(str((r.get("elapsed_s") or 0) // 60))} min</span></div>')
    if not lanes:
        out.append('<div class="small">Nothing.</div>')
    # 2. dispatcher jobs that are not builds or script reviews
    rows, b = _jsonl_tail(WORKSPACE / "workspace" / "dispatch-loop.jsonl", 6000)
    bad += b
    fin = [r for r in rows if r.get("event") == "finished"
           and r.get("kind") in ("preflight", "supervise", "console")]
    labels = {"preflight": "Brief review (preflight)", "supervise": "Failure supervision",
              "console": "Console answer"}
    for kind in ("preflight", "supervise", "console"):
        mine = [r for r in fin if r.get("kind") == kind][-25:]
        out.append(f"<h2>{labels[kind]} ({len(mine)})</h2>")
        if not mine:
            out.append('<div class="small">None recorded.</div>')
        for r in reversed(mine):
            res = ("passed" if r.get("accepted") else "objected/refused") if kind == "preflight" \
                else ("ok" if r.get("exit") == 0 else f"exit {r.get('exit')}")
            out.append(f'<div class="card small"><b>{esc(r.get("chunk") or r.get("key") or "?")}</b> '
                       f'&middot; {esc(str(r.get("at", ""))[5:16].replace("T", " "))}Z &middot; '
                       f'{esc(res)} &middot; {esc(str(r.get("seconds", "?")))} s &middot; '
                       f'{esc(r.get("model", ""))}</div>')
    # 3. night queue
    out.append("<h2>Night queue, 02:00</h2>")
    logs = sorted((WORKSPACE / "workspace" / "run-logs").glob("night-*.log"),
                  key=lambda f: f.stat().st_mtime, reverse=True)
    for f in logs[:8]:
        try:
            tail = f.read_text(encoding="utf-8", errors="replace").strip().splitlines()[-1:]
        except OSError as e:
            tail = [f"unreadable: {e}"]
        out.append(f'<div class="card small"><b>{esc(f.name)}</b> &middot; '
                   f'{esc(time.strftime("%m-%d %H:%M", time.localtime(f.stat().st_mtime)))} &middot; '
                   f'{esc((tail[0] if tail else "(empty)")[:200])}</div>')
    if not logs:
        out.append('<div class="small">No night logs.</div>')
    # 4. model loads and who caused them
    loads, b = _jsonl_tail(WORKSPACE / "workspace" / "run-logs" / "ollama-loads.jsonl", 30)
    bad += b
    out.append(f"<h2>Model loads ({len(loads)})</h2><p class=\"small\">A load is a "
               "model coming into memory; the caller is the process that asked for it.</p>")
    for r in reversed(loads):
        who = ", ".join(Path(str(c.get("cmd", "")).split()[1] if len(str(c.get("cmd", "")).split()) > 1
                             else str(c.get("cmd", ""))).name for c in (r.get("callers_last_30s") or [])[:3])
        out.append(f'<div class="card small">{esc(str(r.get("at", ""))[5:16].replace("T", " "))}Z '
                   f'&middot; {esc(r.get("event", ""))} <b>{esc(r.get("model", "?"))}</b> '
                   f'&middot; by {esc(who or "unknown")}</div>')
    # 5. GPU time by harness, 24 h
    tele, b = _jsonl_tail(WORKSPACE / "workspace" / "run-telemetry.jsonl", 5000)
    bad += b
    cut = time.time() - 86400
    secs = {}
    for r in tele:
        try:
            t = calendar.timegm(time.strptime(r.get("at", ""), "%Y-%m-%dT%H:%M:%SZ"))
        except ValueError:
            bad += 1
            continue
        if t >= cut:
            secs[r.get("harness", "?")] = secs.get(r.get("harness", "?"), 0) + (r.get("seconds") or 0)
    tot = sum(secs.values()) or 1
    out.append('<h2>Model time, last 24 h</h2><p class="small">By harness. "console" is the '
               'manager, supervision and script reviews together -- they share one client.</p>')
    for h, v in sorted(secs.items(), key=lambda x: -x[1]):
        out.append(f'<div class="card small"><b>{esc(h)}</b> &middot; {v / 3600:.1f} h '
                   f'({100 * v / tot:.0f}%)</div>')
    # 5b. session review (Jas, 2026-09-28): what the manager found in finished sessions --
    # work put off without a task, problems left unfiled -- each with its RCA report and tasks.
    try:
        import session_review as _sr
        led, bad_led = [], 0
        if _sr.LEDGER.is_file():
            for line in _sr.LEDGER.read_text(encoding="utf-8", errors="replace").splitlines():
                try:
                    led.append(json.loads(line))
                except ValueError:
                    bad_led += 1
        covered = [r for r in led if r.get("event") == "covered"]
        # "Caught by session review" and "Sessions queued" moved to Production and the Backlog
        # (Jas, 2026-09-29); what an open task already covered stays here.
        out.append(f'<h2>Already covered by a task ({len(covered)})</h2><p class="small">Found in a '
                   'session but an open task covered it; the finding was noted on that task.</p>')
        for r in covered[-40:][::-1]:
            out.append(f'<div class="card small"><b>{esc(r.get("task", ""))}</b> &middot; '
                       f'{esc(r.get("quote", ""))}</div>')
        if bad_led:
            out.append(f'<p class="small">{bad_led} unreadable ledger line(s) skipped.</p>')
    except Exception as e:                                          # noqa: BLE001
        out.append(f'<h2>Session review</h2><div class="empty">could not read it: '
                   f'{esc(f"{type(e).__name__}: {e}")} -- not a pass</div>')
    # 6. scheduled jobs and always-on services (Jas, 2026-09-26: the ones with no model call too)
    out.append("<h2>Scheduled jobs</h2><p class=\"small\">Every systemd timer and cron line, "
               "model or not. Result is the last run's.</p>")
    import subprocess as _sp
    try:
        timers = json.loads(_sp.run(["systemctl", "--user", "list-timers", "--all", "--output=json",
                                     "--no-pager"], capture_output=True, text=True,
                                    timeout=20).stdout or "[]")
    except (OSError, ValueError, _sp.SubprocessError) as e:
        timers = []
        out.append(f'<div class="empty">systemctl list-timers failed: {esc(str(e))}</div>')
    svcs = [t.get("activates") for t in timers if t.get("activates")]
    always = ["scoping-queue@manager.service", "app-feedback.service", "agent-console.service",
              "model-chat.service", "workspace-mcp.service",
              "ollama-callers.service", "cloudflared-hivemind.service", "dsh-web.service",
              "app-repo.service", "agent-shell.service"]
    state = {}
    try:
        txt = _sp.run(["systemctl", "--user", "show", *svcs, *always, "-p",
                       "Id,ActiveState,Result,ExecMainStatus", "--no-pager"],
                      capture_output=True, text=True, timeout=20).stdout
        for block in txt.strip().split("\n\n"):
            d = dict(l.split("=", 1) for l in block.splitlines() if "=" in l)
            state[d.get("Id")] = d
    except (OSError, _sp.SubprocessError) as e:
        out.append(f'<div class="empty">systemctl show failed: {esc(str(e))}</div>')

    def when(us):
        return time.strftime("%m-%d %H:%M", time.localtime(us / 1e6)) if us else "never"
    for t in sorted(timers, key=lambda t: t.get("unit", "")):
        d = state.get(t.get("activates"), {})
        ok = d.get("Result") == "success"
        out.append(f'<div class="card small"><b>{esc(t.get("unit", "?"))}</b> &middot; last '
                   f'{esc(when(t.get("last")))} &middot; next {esc(when(t.get("next")))} &middot; '
                   f'{"ok" if ok else "<b>" + esc(d.get("Result", "?")) + " (exit " + esc(d.get("ExecMainStatus", "?")) + ")</b>"}'
                   f'{" &middot; running now" if d.get("ActiveState") == "active" else ""}</div>')
    # A TIMER FILE THAT IS NOT ENABLED NEVER RUNS, and list-timers omits it -- so it would be
    # invisible here too. On 2026-09-26 model-warm and triage were both in that state.
    listed = {t.get("unit") for t in timers}
    for f in sorted((Path.home() / ".config" / "systemd" / "user").glob("*.timer")):
        if f.name not in listed:
            out.append(f'<div class="card small"><b>{esc(f.name)}</b> &middot; '
                       f'<b>disabled -- not scheduled, never runs</b></div>')
    try:
        cron = _sp.run(["crontab", "-l"], capture_output=True, text=True, timeout=10).stdout
    except (OSError, _sp.SubprocessError) as e:
        cron = ""
        out.append(f'<div class="empty">crontab unreadable: {esc(str(e))}</div>')
    for line in cron.splitlines():
        if line.strip() and not line.startswith("#"):
            f = line.split()
            names = [Path(x).name for x in f[5:] if x.endswith((".py", ".sh"))]
            what = names[-1] if names else " ".join(f[5:])[:60]    # the script run, not its launcher
            out.append(f'<div class="card small"><b>cron</b> {esc(" ".join(f[:5]))} &middot; '
                       f'{esc(what)}</div>')
    out.append("<h2>Always-on services</h2>")
    for u in always:
        d = state.get(u, {})
        up = d.get("ActiveState") == "active"
        out.append(f'<div class="card small"><b>{esc(u.replace(".service", ""))}</b> &middot; '
                   f'{"up" if up else "<b>" + esc(d.get("ActiveState", "unknown")) + "</b>"}</div>')
    if bad:
        out.append(f'<p class="small">{bad} unreadable ledger line(s) skipped.</p>')
    return counted("".join(out))


def counted(page):
    """Every <h2> that carries no (N) gets one: the number of item cards shown under it, up to the
    next heading (Jas, 2026-09-29: "all of these items should have a number in brackets")."""
    parts = re.split(r"(<h2[^>]*>.*?</h2>)", page, flags=re.S)
    for i in range(1, len(parts), 2):
        if not re.search(r"\(\d+\)", parts[i]):
            n = len(re.findall(r'class="card\b', parts[i + 1] if i + 1 < len(parts) else ""))
            parts[i] = re.sub(r"</h2>$", f" ({n})</h2>", parts[i])
    return "".join(parts)


def collapsed(page):
    """Every <h2> section folded into a tap-to-open <details> (Jas, 2026-09-27: see how much is
    in each at a glance, expand to read). Text before the first heading stays open."""
    parts = re.split(r"(<h2[^>]*>.*?</h2>)", page, flags=re.S)
    out = [parts[0]]
    for i in range(1, len(parts), 2):
        title = re.sub(r"</?h2[^>]*>", "", parts[i])
        out.append(f'<details class="sec"><summary><b>{title}</b></summary>'
                   f'{parts[i + 1] if i + 1 < len(parts) else ""}</details>')
    return ("<style>details.sec{margin:.4rem 0;border:1px solid var(--line,#ccc);"
            "border-radius:10px;padding:.5rem .7rem}details.sec>summary{cursor:pointer;"
            "font-size:1.02rem}</style>" + "".join(out))


def _board_section():
    """The lanes BOARD on the Hub: who is doing what, read from the one store, never copied.

    Jas, 2026-10-28: "I want visibility of the board in the Hub somewhere." The board
    (docs/THE-BOARD.md, scripts/lanes.py) is where four workers say what they are holding, what they are
    asking and what they have finished; until now it was only readable from a terminal, so "who has this
    file?" and "is anyone waiting on me?" were questions Jas could not answer without asking a model.

    Every fact is read at render time from lanes.rows()/active_claims()/open_questions() -- the same
    store the lanes use -- so this page cannot disagree with them. Nothing is cached: a stale board here
    would be worse than no board, because it is the page that answers "is anyone stuck?".
    """
    sys.path.insert(0, str(WORKSPACE / "scripts"))
    try:
        import lanes
        if not Path(lanes.BOARD).is_file():
            # AN ABSENT BOARD IS NOT "NOBODY IS WAITING". A missing file would otherwise render three
            # green chips, which is the vacuous pass this repository keeps paying for.
            return (f'<h3>The board</h3><p class="small">the board file is MISSING at '
                    f'{esc(str(lanes.BOARD))} &mdash; nobody has posted, or the workspace moved. This is '
                    f'not &ldquo;nobody is waiting&rdquo;.</p>')
        rows = lanes.rows()
        claims = lanes.active_claims(rows)
        questions = lanes.open_questions(rows)
    except Exception as e:                                            # noqa: BLE001
        return (f'<h3>The board</h3><p class="small">could not read the board '
                f'({type(e).__name__}: {e}) -- this is not "nobody is waiting".</p>')

    def chip(colour, label):
        return (f'<span style="display:inline-block;padding:.1rem .5rem;border-radius:.6rem;'
                f'background:{colour};color:#111;font-weight:600;font-size:.85em">{esc(label)}</span>')

    # THE ONE THING THAT COSTS A CYCLE: a question nobody has answered, or a merge slot still held.
    merge_open = [r for r in rows if r.get("kind") == "post"
                  and str(r.get("text", "")).startswith("MERGE START")
                  and not any(x.get("kind") == "post" and str(x.get("text", "")).startswith(("MERGE DONE", "MERGE ABORTED"))
                              and x.get("at", "") >= r.get("at", "") for x in rows)]
    bits = [chip("#e8b", f"{len(questions)} open question(s)") if questions else chip("#7c6", "no open questions"),
            chip("#e8b", f"{len(claims)} claim(s) held") if claims else chip("#7c6", "no files held"),
            chip("#e66", "a MERGE slot is still open") if merge_open else chip("#7c6", "no merge slot open")]
    out = ['<h3 style="margin:.8rem 0 .3rem">The board</h3>',
           '<p class="small">' + " &middot; ".join(bits)
           + ' &middot; read live from scripts/lanes.py &mdash; <code>docs/THE-BOARD.md</code></p>']

    if questions:
        out.append('<table style="width:100%"><tr><th>asked</th><th>by &rarr; to</th><th>question</th></tr>')
        for r in questions[-6:][::-1]:
            out.append(f'<tr><td class="small">{esc(str(r.get("at",""))[11:16])}</td>'
                       f'<td>{esc(r.get("lane",""))} &rarr; {esc(r.get("to",""))}</td>'
                       f'<td>{esc(str(r.get("text",""))[:220])}</td></tr>')
        out.append('</table>')
    if claims:
        # active_claims() is {what: row}; accept a list too rather than depend on one shape here.
        held = list(claims.items()) if isinstance(claims, dict) else [(c.get("what", ""), c) for c in claims]
        out.append('<p class="small"><b>Held now:</b> ' + ", ".join(
            f'{esc(str((r or {}).get("what") or what))} '
            f'<span class="small">({esc(str((r or {}).get("lane","")))})</span>'
            for what, r in held[:8]) + '</p>')
    recent = rows[-14:][::-1]
    if recent:
        out.append('<table style="width:100%"><tr><th>at</th><th>lane</th><th>what</th></tr>')
        for r in recent:
            out.append(f'<tr><td class="small">{esc(str(r.get("at",""))[11:16])}</td>'
                       f'<td>{esc(str(r.get("lane","")))}</td>'
                       f'<td class="small">{esc(str(r.get("kind","")).upper())}: {esc(str(r.get("text",""))[:260])}</td></tr>')
        out.append('</table>')
    else:
        out.append('<p class="small">The board has no rows yet.</p>')
    return "".join(out)


def lanes_page():
    """The Lanes tab: lane_status.py's data as tables (Jas, 2026-09-28: "can it show the data on a
    table or something more readable? ... total VRAM used and available"), every Ollama server the
    box is running found live (servers come and go). Each fact is shown once, and nothing on the
    tab collapses (t646, Jas 2026-10-02: "data does not need to live in two places" -- the
    lane_status.py text report that repeated the tables was removed; the running sweep it alone
    carried has its own section).

    Jas, 2026-09-26: "Can you make a tab that just runs this script? I use it a lot." Still true --
    the same snapshot() the text report prints, read in-process (~0.8 s); the page reloads itself
    every 15 s, and only while it is visible."""
    sys.path.insert(0, str(WORKSPACE / "scripts"))
    stamp = time.strftime("%H:%M:%S")
    out = [f'<p class="small">lane_status at {stamp} &middot; refreshes every 15 s while this tab is open</p>',
           _board_section()]
    try:
        import importlib
        import lane_status as ls
        importlib.reload(ls)
        d = ls.snapshot()
    except Exception as e:                                          # noqa: BLE001
        d = None
        out.append(f'<div class="empty">lane_status failed: {esc(f"{type(e).__name__}: {e}")}</div>')
    # THE TEST SWEEP, AT THE TOP (Jas, 2026-10-02: "you said you were going to put the session end
    # script in the lanes tab so I could see the progress of it"). The line existed only inside the
    # collapsed text report at the bottom, which every 15 s reload closed again.
    try:
        sweep = ls.sweep_progress() if d is not None else None
    except Exception as e:                                          # noqa: BLE001 -- said, not swallowed
        sweep = f"unreadable: {type(e).__name__}: {e}"
    if sweep:
        out.append(f'<h3>Test sweep</h3><div style="font-size:1.05rem;padding:.4rem .6rem;'
                   f'border-left:4px solid #2980b9">{esc(sweep)}</div>')
    # THE HOURLY CHECKS, ON THE HUB (t1153): their last run, what was failing, and what it filed. They
    # wrote workspace/hourly-checks.jsonl and nothing showed it -- the sweep's result reached no page.
    try:
        _hc = [ln for ln in (WORKSPACE / "workspace" / "hourly-checks.jsonl").read_text(
            encoding="utf-8").splitlines() if ln.strip()]
        hc = json.loads(_hc[-1]) if _hc else None
    except (OSError, ValueError) as e:
        hc = None
        out.append(f'<div class="bad small">hourly checks unreadable: {esc(f"{type(e).__name__}: {e}")}</div>')
    if hc:
        failing = hc.get("failing") or []
        col = "#c0392b" if failing else "#27ae60"
        out.append(f'<h3>Hourly checks</h3><div style="border-left:4px solid {col};padding:.3rem .6rem">'
                   f'last run {esc(str(hc.get("at", ""))[5:16].replace("T", " "))} UTC at {esc(str(hc.get("head", "")))} '
                   f'&middot; <b>{len(failing)} failing</b>'
                   + (": " + esc("; ".join(failing)) if failing else "")
                   + f' &middot; sweep: {esc(str(hc.get("sweep", "")))}'
                   + (f' &middot; filed {esc(", ".join(hc.get("filed") or []))}' if hc.get("filed") else "")
                   + '</div>')
    # WHY IS THIS LANE IDLE (harness #4, plan 2026-10-19): the first blocker per idle model role --
    # a pause, a server down, the memory guard, a dispatcher not ticking, or nothing waiting.
    try:
        import lane_idle
        importlib.reload(lane_idle)
        idle = lane_idle.reasons()
    except Exception as e:                                          # noqa: BLE001 -- said, not swallowed
        idle = None
        out.append(f'<div class="bad small">idle-lane reasons unreadable: {esc(f"{type(e).__name__}: {e}")}</div>')
    if idle is not None:
        out.append('<h3>Idle lanes</h3>' + ('<div class="small">every lane with work to do is working</div>'
                   if not idle else "".join(
                       f'<div><b>{esc(r["name"])}</b> {r["running"]}/{esc(str(r["lanes"] if r["lanes"] is not None else "no limit"))} '
                       f'lanes &mdash; {esc(r["reason"])}</div>' for r in idle)))
    # THE SERVERS AND THEIR READINESS, BEFORE WHAT IS RESIDENT (t1555). A vLLM server that is up and
    # still loading its weights answers nothing, so a list of "servers answering now" omitted Swift for
    # the ~10 minutes its load takes -- Jas read a page with no loading state, beside a scheduler line
    # saying "GX10 running", as "fully loaded" (2026-10-23 11:26 MDT, shards 10/51). State is a WORD:
    # ready / loading / stopped. Rendered OUTSIDE `if d:` so a lane_status failure cannot hide it.
    try:
        out.append('<h3>Servers</h3><p class="small">Readiness, not existence: <b>ready</b> means '
                   'GET /v1/models answered; <b>loading</b> means the unit or container is up and it '
                   'has not answered yet (Swift takes ~10 min); <b>stopped</b> means neither.</p>')
        out.append(servers_html(engine_readiness()))
    except Exception as e:                                          # noqa: BLE001 -- said, not swallowed
        out.append(f'<div class="bad small">server readiness unreadable: {esc(f"{type(e).__name__}: {e}")}</div>')
    if d:
        hms = ls.hms
        mem = d.get("memory") or {}
        if "error" in mem or not mem:
            out.append(f'<h3>Memory</h3><div class="empty">unreadable: {esc(mem.get("error", "no data"))}</div>')
        else:
            pct = mem["used_pct"]
            col = "#c0392b" if pct >= 90 else "#d68910" if pct >= 75 else "#27ae60"
            out.append(
                '<h3>Memory <span class="small">(CPU and GPU share it on this box; no separate VRAM)</span></h3>'
                f'<div style="font-size:1.05rem"><b>{mem["used_gib"]} GiB used</b> &middot; '
                f'<b>{mem["free_gib"]} GiB free</b> of {mem["total_gib"]} GiB</div>'
                f'<div style="background:#ddd;border-radius:4px;height:14px;margin:.3rem 0 .8rem">'
                f'<div style="width:{pct}%;background:{col};height:14px;border-radius:4px"></div></div>')
        # MEMORY BY GROUP (Jas, 2026-10-08: "observe what [one lane] uses ... including your tests").
        # memory_watch.py samples every 20 s; each process is charged to the job that started it.
        try:
            import memory_watch as _mw
            mw = _mw.summary(24.0)
        except Exception as e:                                      # noqa: BLE001 -- said, not swallowed
            mw = None
            out.append(f'<div class="bad small">memory watch unreadable: {esc(f"{type(e).__name__}: {e}")}</div>')
        if mw and mw["samples"]:
            lo = mw["lowest_available"]
            out.append('<h3>Memory by group <span class="small">(last 24 h, every 20 s; resident + GPU)</span></h3>'
                       f'<p class="small">{mw["samples"]} samples since {esc(mw["since"][5:16].replace("T", " "))} UTC '
                       f'&middot; lowest free: <b>{lo["gb"]} GiB</b> at {esc(lo["at"][11:16])} UTC '
                       f'(swap {lo["swap_used_gb"]} GiB)</p><div class="tbl"><table>'
                       '<tr><th>group</th><th>now</th><th>median</th><th>peak</th></tr>')
            for g, v in mw["groups"].items():
                out.append(f'<tr><td>{esc(g)}</td><td>{v["now"]:.2f}</td><td>{v["median"]:.2f}</td>'
                           f'<td><b>{v["peak"]:.2f}</b></td></tr>')
            out.append('</table></div>')
        out.append(remote_devices_html("h3"))
        # WHICH SERVERS EXIST AND WHETHER THEY ARE READY IS THE SECTION ABOVE ("Servers"), read from
        # the endpoint itself; this one is only the models those servers are holding in memory. Two
        # lists of servers on one page disagreed by construction (t646) and neither said "loading".
        out.append('<h3>Models loaded, by server</h3>')
        if d["resident"]:
            out.append('<div class="tbl" style="overflow-x:auto"><table><tr><th>server</th><th>model</th>'
                       '<th>in memory</th><th>server says</th><th>unloads at</th></tr>')
            for m in d["resident"]:
                real = f'{m["rss_gib"]} GiB' if m.get("rss_gib") is not None else "unreadable"
                exp = m.get("expires") or ""
                exp = exp[5:16].replace("T", " ") if exp[:1].isdigit() else exp   # vLLM: "while the server runs"
                out.append(f'<tr><td>{esc(m.get("server"))}<br><span class="small">:{m.get("port")}</span></td>'
                           f'<td>{esc(m["name"])}</td><td><b>{real}</b></td><td>{m["gb"]} GB</td>'
                           f'<td>{esc(exp)}</td></tr>')
            out.append('</table></div><p class="small">"in memory" is what the model\'s processes hold: '
                       'their resident size plus the GPU\'s allocation for them, which on this box comes '
                       'from the same pool (2026-10-08: the resident size alone showed Swift as unreadable '
                       'and the embedder at 1.1 GiB of its 5). "server says" is Ollama\'s figure, or the '
                       'footprint a vLLM server declares.</p>')
        else:
            out.append('<div class="empty">No model is loaded on any server.</div>')
        bp = ls.build_progress()
        if bp:
            eta = hms(bp["eta_s"]) if bp["eta_s"] is not None else "unknown"
            out.append('<h3>Builds today</h3><div class="tbl"><table>'
                       f'<tr><th>done</th><td>{bp["done"]} of {bp["total"]} ({bp["accepted"]} accepted)</td></tr>'
                       f'<tr><th>running</th><td>{bp["running"]}</td></tr>'
                       f'<tr><th>waiting</th><td>{bp["waiting"]} &middot; ETA ~{eta}</td></tr>'
                       f'<tr><th>held / parked</th><td>{bp["held"]} (need a person)</td></tr>'
                       f'<tr><th>awaiting review</th><td>{bp["review"]}</td></tr></table></div>')
        # QUEUES BY ROLE (Jas, 2026-10-08): every role's waiting work, task by task in its list order,
        # each task's whole list. What is running is in Running jobs below, so it is not repeated.
        try:
            rq = ls.role_queues()
        except Exception as e:                                      # noqa: BLE001 -- said, not swallowed
            rq = None
            out.append(f'<div class="bad small">queues unreadable: {esc(f"{type(e).__name__}: {e}")}</div>')
        if rq is not None:
            names = {}
            try:
                import task_routing as _T
                _d = _T.load()
                names = {k: (r.get("name") or k) for k, r in _d.get("roles", {}).items()}
            except Exception as e:                                  # noqa: BLE001 -- labels only
                print(f"  NOTE: role names unreadable ({e})", file=sys.stderr)
            out.append('<h3>Queues by role</h3><p class="small">What is waiting, in each role\'s task order; '
                       'running work is under Running jobs.</p>')
            for role, rows in rq.items():
                total = sum(len(w) for _, w in rows)
                out.append(f'<h4 style="margin:.5rem 0 .2rem">{esc(names.get(role, role))} '
                           f'<span class="small">&middot; {total} waiting</span></h4>')
                for task, w in rows:
                    label = esc(task.replace("_", " ").capitalize())
                    if not w:
                        out.append(f'<div class="small" style="margin-left:1rem">{label} (0)</div>')
                        continue
                    # Every item, in order, wrapped -- not collapsed (t646: nothing on Lanes collapses).
                    items = " &middot; ".join(f"{i}.&nbsp;{esc(k)}" for i, k in enumerate(w, 1))
                    out.append(f'<div style="margin:.15rem 0 .45rem 1rem"><b>{label} ({len(w)})</b>'
                               f'<div class="small" style="overflow-wrap:anywhere">{items}</div></div>')
        out.append('<h3>Running jobs</h3>')
        if d["lanes"]:
            out.append('<div class="tbl" style="overflow-x:auto"><table><tr><th>role</th><th>server</th><th>model</th>'
                       '<th>job</th><th>running</th><th title="median decode speed of this model\'s recent calls -- '
                       'a model figure, not this job\'s">model speed (recent)</th></tr>')
            for r in d["lanes"]:
                el = hms(r["elapsed_s"]) if r["elapsed_s"] is not None else "?"
                now = f'{r["recent_rate"]:.1f} tok/s' if "recent_rate" in r else "no recent call"
                dup = ' <b style="color:#c0392b">DUPLICATE</b>' if r["duplicate_of"] else ""
                port = r.get("server_port")
                srv_name = r.get("server_name") or ls.SERVER_NAMES.get(port, f"port {port}")
                out.append(f'<tr><td>{esc(r.get("role") or "?")}</td><td>{esc(srv_name)}<br><span class="small">:{port}</span></td>'
                           f'<td>{esc(r["model"])}</td><td>{esc(r["brief"])}{dup}'
                           f'<br><span class="small">{esc(r["kind"])} &middot; {esc(r["script"])} '
                           f'&middot; pid {r["pid"]}</span></td>'
                           f'<td>{el}</td><td>{now}</td></tr>')
            out.append('</table></div>')
        else:
            out.append('<div class="empty">Nothing is running.</div>')
        g = d.get("gpu") or []
        th = ls.thermal()
        heat = (f"hottest sensor {th[0]:.0f} C, {th[1] - th[0]:.0f} C below shutdown" if th and th[1]
                else "temperature unreadable")
        out.append(f'<h3>GPU</h3><p>{esc(", ".join(g) or "nvidia-smi unavailable")}<br>'
                   f'<span class="small">name, load, power, temperature &middot; {esc(heat)}</span></p>')
    # NO AUTO-RELOAD (Jas, 2026-10-03): it reloaded every 15 s while visible. A button instead.
    out.insert(0, '<p><button onclick="location.reload()">&#8635; Reload</button></p>')
    return "".join(out)


_FILTER_JS = """<input id="cfilter" type="search" placeholder="Filter..." style="width:100%;margin:.4rem 0">
<script>document.getElementById('cfilter').addEventListener('input', function () {
  var q = this.value.toLowerCase();
  document.querySelectorAll('.centry').forEach(function (e) {
    var hit = !q || e.textContent.toLowerCase().indexOf(q) >= 0;
    e.style.display = hit ? '' : 'none'; if (q && hit) e.open = false; });
});</script>"""


def _corpus_entries(path, head_re, what):
    """[(group, title, body_md)] from a docs/*.md register: `## ` lines are groups, head_re starts an
    entry. Read on every request, so the page cannot drift from the file."""
    try:
        text = (WORKSPACE / path).read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return None, f'<div class="bad">{esc(what)} unreadable: {esc(str(e))}</div>'
    out, group, cur = [], "", None
    for line in text.splitlines():
        if line.startswith("## "):
            group = line[3:].strip()
            cur = None
            continue
        m = re.match(head_re, line)
        if m:
            cur = [group, m.group(1).strip(), []]
            out.append(cur)
        elif cur is not None:
            cur[2].append(line)
    return [(g, t, "\n".join(b).strip()) for g, t, b in out], ""


def _corpus_register(path, head_re, what, blurb):
    """Decisions and Lessons on the Corpus tab (Jas, 2026-10-01: "Decision list that we have cannot be
    added as a section within the Corpus tab? Same with the lessons"). Newest-first order is the
    file's own; every entry is collapsed, and the filter searches titles and bodies."""
    entries, err = _corpus_entries(path, head_re, what)
    if err:
        return err
    out = [f'<p class="small">{len(entries)} {esc(what.lower())}, from <code>{esc(path)}</code>. {blurb}</p>',
           _FILTER_JS]
    group = None
    for g, title, body in entries:
        if g != group:
            group = g
            out.append(f'<div class="khead">{esc(g)}</div>')
        out.append(f'<details class="card centry"><summary><b>{md_to_html(title)}</b></summary>'
                   f'{md_to_html(body)}</details>')
    return "".join(out)


RETRY_LABEL = {"SERVER-DOWN": "retry when the model server answers (a failed-build rule)",
               "DUPLICATE-RUN": "a second copy refused itself; the first one's result stands",
               "SWITCH": "retry on another model (the supervisor's decision)",
               "REWRITE-BRIEF": "brief rewritten by the supervisor; preflight reviews it first"}


def supervision_retry_labels():
    """{task: label} from each task's LATEST supervision decision, for those that leave it ready to build."""
    out = {}
    cutoff = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 3 * 86400))
    try:
        sys.path[:0] = [str(WORKSPACE / "scripts")]
        import dispatch_loop as _dl
        for line in (WORKSPACE / "workspace" / "supervision.jsonl").read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
            except ValueError:
                print("  NOTE: unreadable line in supervision.jsonl -- no label from it", file=sys.stderr)
                continue
            if str(r.get("at", "")) < cutoff:     # an old decision says nothing about the chunk now
                continue
            tid = _dl.task_of(r.get("brief") or "")
            if tid:
                out[tid] = RETRY_LABEL.get(r.get("decision"))
    except (OSError, ImportError) as e:
        print(f"  NOTE: supervision labels unreadable: {type(e).__name__}: {e}", file=sys.stderr)
    return {t: v for t, v in out.items() if v}


def corpus_page(section, run_filter=""):
    sections = [("models", "Models"), ("scripts", "Scripts"), ("decisions", "Decisions"),
                ("lessons", "Lessons")]
    if section not in dict(sections):
        section = "models"
    pills = "".join(f'<a class="{"on" if k == section else ""}" href="/corpus?section={k}">{html.escape(v)}</a>'
                    for k, v in sections)
    if section == "decisions":
        body = _corpus_register("docs/DECISIONS.md", r"^###\s+(D-\d+.*)$", "Decisions",
                                "What was decided, by whom, and why.")
    elif section == "lessons":
        body = _corpus_register("docs/LESSONS.md", r"^\*\*(L-\d+.*?)(?:\*\*|$)", "Lessons",
                                "What went wrong once, and what now stops it happening again.")
    else:
        body = _corpus_models() if section == "models" else _corpus_scripts(run_filter)
    return f'<nav class="pills">{pills}</nav>\n{body}'


# THE SERVICE WORKER, inline so there is one file to deploy and no static path to get wrong.
# A push arrives with the box's JSON payload; showNotification is MANDATORY -- a push event
# that ends without showing one makes the browser drop the subscription after a few
# offences, which would look exactly like the notifications quietly stopping.

# The tailnet name, not the IP: the certificate is issued for the NAME, so a link to
# https://100.64.0.10:8443 fails hostname validation and teaches Jas the HTTPS is broken
# when it is fine. Kept next to appnav's HOST so the two cannot drift apart unnoticed.
HTTPS_HUB = "https://hivemind.example.ts.net:8443"

SUBSCRIBE_JS = """
<script>
(function () {
  var secure = window.isSecureContext && 'serviceWorker' in navigator && 'PushManager' in window;
  var el = document.getElementById(secure ? 'sub-secure' : 'sub-insecure');
  if (el) el.hidden = false;
  if (!secure) return;

  function b64(s) {                       // VAPID keys are base64url; atob needs base64
    var pad = '='.repeat((4 - s.length % 4) % 4);
    var raw = atob((s + pad).replace(/-/g, '+').replace(/_/g, '/'));
    return Uint8Array.from([].map.call(raw, function (c) { return c.charCodeAt(0); }));
  }

  var msg = document.getElementById('sub-msg');
  function say(t) { if (msg) msg.textContent = t; }

  var go = document.getElementById('sub-go');
  if (!go) return;
  go.addEventListener('click', function () {
    go.disabled = true;
    say('asking for permission…');
    Notification.requestPermission().then(function (perm) {
      if (perm !== 'granted') { go.disabled = false; return say('permission denied — nothing was subscribed.'); }
      say('registering the service worker…');
      return navigator.serviceWorker.register('/sw.js', { scope: '/' })
        .then(function (reg) { return navigator.serviceWorker.ready.then(function () { return reg; }); })
        .then(function (reg) {
          say('fetching the key…');
          return fetch('/status/vapid').then(function (r) { return r.text(); }).then(function (k) {
            if (!k) throw new Error('no VAPID public key on the box');
            return reg.pushManager.subscribe({
              userVisibleOnly: true,
              applicationServerKey: b64(k.trim())
            });
          });
        })
        .then(function (sub) {
          document.getElementById('sub-json').value = JSON.stringify(sub);
          say('registering this device…');
          document.getElementById('sub-form').submit();
        });
    }).catch(function (e) {
      go.disabled = false;
      say('could not subscribe: ' + e.message);   // SAID OUT LOUD, never a silent failure
    });
  });
})();
</script>
"""

SERVICE_WORKER = """
self.addEventListener('install', e => self.skipWaiting());
self.addEventListener('activate', e => e.waitUntil(self.clients.claim()));

self.addEventListener('push', event => {
  let d = {};
  try { d = event.data ? event.data.json() : {}; } catch (err) { d = { body: event.data && event.data.text() }; }
  const title = d.title || 'HiveMind';
  event.waitUntil(self.registration.showNotification(title, {
    body: d.body || '',
    tag: d.tag || 'hivemind',
    data: { url: d.url || '/status' },
    icon: d.icon || undefined
  }));
});

self.addEventListener('notificationclick', event => {
  event.notification.close();
  const url = (event.notification.data && event.notification.data.url) || '/status';
  event.waitUntil(clients.matchAll({ type: 'window', includeUncontrolled: true }).then(ws => {
    for (const w of ws) { if ('focus' in w) { w.navigate(url); return w.focus(); } }
    return clients.openWindow(url);
  }));
});
"""

def _task_board():
    """scripts/task_board.py's board, or None with the error (the page says it could not look)."""
    try:
        sd = str(WORKSPACE / "scripts")
        if sd not in sys.path:
            sys.path.insert(0, sd)
        import task_board
        return task_board.board(WORKSPACE), task_board.STAGES, None
    except Exception as e:                                          # noqa: BLE001
        return None, [], f"{type(e).__name__}: {e}"


STAGE_LABEL = {"merged": "Merged", "in-review": "In review (Claude)", "building": "Building",
               "briefed": "Ready to build -- waiting for a build lane",
               "parked": "Parked -- a build failed; waiting on supervision or a fix",
               "held-claude": "Held for Claude -- preflight objected to the brief",
               "held-red": "Held -- a suite is red on main", "preflight": "Preflight", "done-check": "Done? (Claude verifies)",
               "blocked": "Blocked", "needs-jas": "Needs you", "scoped": "Scoped (Claude reviews the plan)"}


# Stages that wait on a PERSON: shown on Review, not Production (Jas, 2026-09-26).
REVIEW_STAGES = ("needs-jas", "blocked", "done-check")


def board_columns_html(stages_wanted):
    """The board's cards for just these stages, as Review shows them."""
    cols, stages, err = _task_board()
    if err:
        return f'<div class="empty">Could not build the board: {esc(err)}</div>'
    titles = {m.get("id"): m.get("title", "") for m, _ in ts.ordered()}
    out = []
    for st in stages_wanted:
        items = cols.get(st, [])
        out.append(f'<h2>{esc(STAGE_LABEL.get(st, st))} ({len(items)})</h2>')
        if not items:
            out.append('<div class="small">&mdash;</div>')
        for tid, _ in items:
            out.append(f'<div class="card"><a href="/plan/{esc(tid)}"><b>{esc(tid)}</b> '
                       f'{esc(titles.get(tid, ""))}</a></div>')
    return "".join(out)


FLOW = WORKSPACE / "projects" / "local-inference" / "models" / "FLOW.json"


def _flow_legend():
    """What the colours and lines mean (Jas, 2026-09-29: "I'm also not fully sure what the color
    coding means"). Drawn with the same colours flow_layout.svg uses, so the key cannot drift."""
    def box(fill, stroke, dash=""):
        return (f'<svg width="30" height="16" style="vertical-align:middle"><rect x="1" y="1" width="28" height="14" '
                f'rx="3" fill="{fill}" stroke="{stroke}" stroke-width="2"{dash}/></svg>')

    def line(stroke, dash="", w=2):
        return (f'<svg width="44" height="12" style="vertical-align:middle"><line x1="2" y1="6" x2="36" y2="6" '
                f'stroke="{stroke}" stroke-width="{w}"{dash}/><path d="M34,1L43,6L34,11z" fill="{stroke}"/></svg>')
    who = [("#e3f2fd", "#1565c0", "Manager"), ("#f3e5f5", "#6a1b9a", "Senior"), ("#e8f5e9", "#2e7d32", "Frontline"),
           ("#ede7f6", "#4527a0", "Claude"), ("#ffebee", "#c62828", "Jas"), ("#e0f7fa", "#00838f", "Embedder"),
           ("#cfd8dc", "#455a64", "a script"), ("#fff9c4", "#c49000", "a store (Backlog, main, memory)"),
           ("#fafafa", "#9e9e9e", "where work comes from")]
    return ('<div class="card small"><b>Key.</b> '
            '<b>Box colour = who does the work:</b> '
            + " &nbsp;".join(f'{box(f, s)} {esc(n)}' for f, s, n in who)
            + '<br><b>Box border = status:</b> '
            f'{box("#fff", "#555")} active &nbsp;{box("#fff", "#555", " stroke-dasharray=\"5 4\" opacity=\".55\"")} inactive '
            f'&nbsp;{box("#fff", "#555", " stroke-dasharray=\"2 3\"")} dry run / obsolete?'
            '<br><b>Arrows:</b> '
            f'{line("#555")} work moves on &nbsp;{line("#888", " stroke-dasharray=\"6 4\"")} work is sent BACK '
            '(a rejected scope, a failed build, an answer returning) &nbsp;'
            f'{line("#b26a00", " stroke-dasharray=\"2 4\"")} &#9889; a TRIGGER: creates new work there '
            '(usually a Backlog task); the item itself does not move. Colour never means &ldquo;new&rdquo;.'
            '<br>Each arrow is labelled with its outcome (bold) and the script that moves it (italic).</div>')


def flow_workers_card(d, wmap):
    """WHO WORKS EACH STAGE (t1594, Jas: "if whatever the source of truth is is updated, the flow tab
    also reflects that"). The answer comes from models/FLOW-WORKERS.json -- generated from ROLES.json
    by scripts/role_store.py on every role save -- and never from the chart, which is not edited to
    carry it. A stage no role holds is said out loud rather than left blank: on 2026-10-25 that was
    20 of 36 nodes, which is a finding about the roles and not a rendering gap.
    """
    tasks = d.get("tasks") or []
    rows, held = [], 0
    for t in tasks:
        who = [str(x) for x in (wmap.get(t.get("id")) or [])]
        name = esc(str(t.get("name") or t.get("id")))
        if who:
            held += 1
            rows.append(f"<li><b>{name}</b> &mdash; {esc(', '.join(who))}</li>")
        else:
            rows.append(f"<li><b>{name}</b> &mdash; <i>no role holds this stage</i></li>")
    return ('<div class="card small"><b>Who works each stage.</b> '
            f"{held} of {len(tasks)} stage(s) are held by a role. Read from "
            "<code>models/FLOW-WORKERS.json</code>, which <code>scripts/role_store.py</code> writes "
            "from <code>models/ROLES.json</code> on every role save &mdash; so this is the roles the "
            "dispatcher uses, not a second list kept by hand.<ul>" + "".join(rows) + "</ul></div>")


def flow_page():
    """The Flow tab (Jas, 2026-09-29): the workflow as a chart -- every task, the links between them,
    and the script that moves work along each link. STEP 0 OF THE PLAN: a read-only preview of
    models/FLOW.json, drafted from the Miro diagram and corrected; editing comes after Jas approves."""
    try:
        d = json.loads(FLOW.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return f'<div class="bad">FLOW.json unreadable: {esc(str(e))}</div>'
    by = {t["id"]: t for t in d["tasks"]}
    try:
        import flow_layout
        lay = flow_layout.layout(d)
        chart, probs = flow_layout.svg(lay), flow_layout.check(lay, d)
    except Exception as e:                                          # noqa: BLE001
        return f'<div class="bad">Flow layout failed: {esc(f"{type(e).__name__}: {e}")}</div>'
    n_st = {}
    for t in d["tasks"]:
        n_st[t.get("status", "active")] = n_st.get(t.get("status", "active"), 0) + 1
    import flow_pages
    out = [flow_pages.top_card(),
           '<div class="card small"><b>Preview.</b> Laid out by <code>scripts/flow_layout.py</code> from '
           '<code>models/FLOW.json</code> &mdash; the same layout the Manager reads with '
           '<code>flow_layout.py --text</code>. Nothing routes by it yet: the scripts do the work, and the '
           'chart describes them. Tap an arrow&rsquo;s label to request a change to it. '
           '<b>Arrows:</b> solid = work moves on; dashed grey = sent back; dotted amber &#9889; = a trigger '
           'that creates work elsewhere. <b>Borders:</b> solid = active; dashed faded = inactive; '
           'dotted = dry run or obsolete. Tap a box to light up its arrows and jump to its details.<br>'
           + " &middot; ".join(f"{esc(k)}: {v}" for k, v in sorted(n_st.items()))
           + (f'<br><b style="color:var(--hot)">Layout problems: {esc("; ".join(probs))}</b>' if probs else
              ' &middot; layout check: 0 overlaps, every outcome linked') + '</div>',
           flow_workers_card(d, flow_layout.workers()),
           _flow_legend(),
           '<div class="small"><button type="button" onclick="fz(1.2)">+</button> '
           '<button type="button" onclick="fz(1/1.2)">&minus;</button> '
           '<button type="button" onclick="fz(0)">fit</button></div>',
           f'<div id="flowwrap" style="overflow:auto;border:1px solid var(--line);border-radius:10px;'
           f'background:#fffdf8;max-height:80vh">{chart}</div>',
           '<style>.flowsvg .node{cursor:pointer}.flowsvg .dim{opacity:.18}.flowsvg .hot rect{stroke-width:4}'
           '.card.sel{outline:3px solid var(--hot)}</style>',
           '<script>(function(){'
           # UPSTREAM / DOWNSTREAM FILTER (Jas, 2026-09-29: "if I tap on a task ... all the steps that are
           # not upstream and downstream from that task are faded ... in the table below all the other
           # tasks ... should be hidden"). Reachability follows the direction work moves (solid and
           # trigger arrows); a sent-back arrow counts only as a direct neighbour, or every loop would
           # make everything related to everything.
           'const sv=document.querySelector(".flowsvg"),W=+sv.getAttribute("width"),H=+sv.getAttribute("height");'
           'let z=1;window.fz=function(f){z=f?z*f:document.getElementById("flowwrap").clientWidth/W;'
           'sv.setAttribute("width",W*z);sv.setAttribute("height",H*z)};'
           'const E=[...new Map([...sv.querySelectorAll("path.edge")].map(p=>[p.dataset.id,{f:p.dataset.from,'
           't:p.dataset.to,back:p.getAttribute("stroke")=="#888"||p.getAttribute("fill")=="#888"}])).values()];'
           'function reach(id,dir){const seen=new Set([id]),q=[id];while(q.length){const n=q.shift();'
           'for(const e of E){if(e.back)continue;const a=dir?e.f:e.t,b=dir?e.t:e.f;'
           'if(a==n&&!seen.has(b)){seen.add(b);q.push(b)}}}return seen}'
           'function pick(id){const keep=new Set([...reach(id,1),...reach(id,0)]);'
           'for(const e of E){if(e.back&&(e.f==id||e.t==id)){keep.add(e.f);keep.add(e.t)}}'
           'sv.querySelectorAll(".node").forEach(g=>{g.classList.toggle("dim",!keep.has(g.dataset.id));'
           'g.classList.toggle("hot",g.dataset.id==id)});'
           'sv.querySelectorAll(".edge,.elabel").forEach(p=>{const e=E.find(x=>x.f==p.dataset.from&&x.t==p.dataset.to)'
           '||{f:p.dataset.from,t:p.dataset.to};let ok;if(p.classList.contains("elabel")){'
           'const q=sv.querySelector(`path.edge[data-id="${p.dataset.id}"]`);ok=q&&keep.has(q.dataset.from)&&keep.has(q.dataset.to)}'
           'else ok=keep.has(p.dataset.from)&&keep.has(p.dataset.to);p.classList.toggle("dim",!ok)});'
           'let n=0;document.querySelectorAll(".flowcard").forEach(c=>{const on=keep.has(c.dataset.id);'
           'c.style.display=on?"":"none";c.classList.toggle("sel",c.dataset.id==id);if(on)n++});'
           'const b=document.getElementById("flowfilter");b.hidden=false;'
           'b.querySelector("span").textContent=`Showing ${n} task(s) upstream and downstream of `+'
           '(document.querySelector(`.flowcard[data-id="${id}"] .tt`)||{textContent:id}).textContent.trim()+". "}'
           'window.flowall=function(){sv.querySelectorAll(".dim,.hot").forEach(x=>x.classList.remove("dim","hot"));'
           'document.querySelectorAll(".flowcard").forEach(c=>{c.style.display="";c.classList.remove("sel")});'
           'document.getElementById("flowfilter").hidden=true};'
           'sv.querySelectorAll(".node").forEach(g=>g.addEventListener("click",()=>pick(g.dataset.id)));'
           # ONE HANDLER ON THE PAGE, not one per card: this script runs before the cards exist, so
           # per-card handlers were attached to nothing and tapping a card did nothing (found by a
           # jsdom click test, 2026-09-29).
           'document.addEventListener("click",ev=>{const c=ev.target.closest(".flowcard");if(!c)return;'
           'if(ev.target.closest("a,button"))return;'
           'pick(c.dataset.id)});'
           'sv.addEventListener("dblclick",flowall);'
           'const h=location.hash.match(/^#t=([\\w-]+)/);if(h)setTimeout(()=>pick(h[1]),0);'
           '})();</script>',
           '<div id="flowfilter" class="card small" hidden><span></span>'
           '<button type="button" onclick="flowall()">Show all</button></div>',
           f'<p class="small">Tap a task, in the chart or below, to see only what is upstream and downstream of '
           f'it; double-tap the chart or press Show all to clear. Task details ({len(d["tasks"])}) are below.</p>',
           f'<h2>Tasks ({len(d["tasks"])})</h2><div class="grid">']
    outs = {}
    for l in d["links"]:
        outs.setdefault((l["from"], l["outcome"]), []).append(l)
    for t in d["tasks"]:
        rows = []
        for o in t["outcomes"]:
            dest = "; ".join(f'{"&#9889; creates in" if l.get("kind") == "trigger" else "&rarr;"} '
                             f'<b>{esc(by.get(l["to"], {}).get("name", l["to"]))}</b> '
                             f'<span class="small">by <code>{esc(l["mover"])}</code>, carries {esc(", ".join(l["carry"]))}</span>'
                             + flow_pages.link_form(l)
                             for l in outs.get((t["id"], o), []))
            if not dest:
                dest = "ends here" if o in t.get("ends", []) else '<b style="color:var(--hot)">NOT LINKED</b>'
            trig = "".join(f'<br>&#9889; {esc(x)}' for x in t.get("triggers", {}).get(o, []))
            rows.append(f'<li><b>{esc(o)}</b> {dest}{trig}</li>')
        note = f'<div class="small"><i>{esc(t["note"])}</i></div>' if t.get("note") else ""
        out.append(f'<div class="card flowcard" id="t-{esc(t["id"])}" data-id="{esc(t["id"])}" style="cursor:pointer">'
                   f'<div class="tt">{esc(t["name"])} <a class="small" href="/flow/task/{esc(t["id"])}">Description</a></div>'
                   f'<div class="meta">{esc(t["worker_kind"])}: <b>{esc(t["worker"])}</b> &middot; '
                   f'{esc(t.get("status", "active"))}</div>{note}'
                   f'<div class="small"><b>Runs:</b> <code>{esc(t["command"])}</code><br>'
                   f'<b>Queue:</b> {esc(t["queue"])}<br><b>Inputs:</b> {esc(", ".join(t["inputs"]) or "-")}<br>'
                   f'<b>Outputs:</b> {esc(", ".join(t["outputs"]) or "-")}</div>'
                   f'<div class="small"><b>Outcomes</b><ul style="margin:.2rem 0 0 1rem;padding:0">{"".join(rows)}</ul></div>'
                   + (f'<div class="small"><b>Skills:</b> ' + ", ".join(
                       f'<a href="#skill-{esc(n)}">{esc(n)}</a>' for n in t.get("skills") or []) + '</div>'
                      if t.get("skills") else "")
                   + flow_pages.helpers_line(d, t["id"]) +
                   f'{flow_pages.task_form(t)}</div>')
    out.append('</div>')
    try:
        import role_store
        out.append(flow_pages.skills_card(d["tasks"], role_store.load()["roles"]))
    except Exception as e:                                          # noqa: BLE001 -- said, not swallowed
        out.append(f'<div class="bad">Skills section failed: {esc(f"{type(e).__name__}: {e}")}</div>')
    out.append(flow_pages.helpers_card(d))
    out.append(flow_pages.changes_card())
    out.append(flow_pages.LABEL_TAP_JS)
    return "".join(out)


def _last_tick():
    """The dispatcher's newest tick row (its queued and skipped lists), or {}. The latest-tick file first
    (t675: the ledger's own tick rows carry counts now); older ledgers still hold the lists."""
    import jsonl_cache
    t = jsonl_cache.latest_tick_file(WORKSPACE / "workspace" / "dispatch-loop.jsonl")
    if t is not None:
        return t
    try:
        with open(WORKSPACE / "workspace" / "dispatch-loop.jsonl", "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 3_000_000))
            lines = f.read().decode("utf-8", "replace").splitlines()[1:]
    except OSError as e:
        print(f"  NOTE: dispatch ledger unreadable: {e}", file=sys.stderr)
        return {}
    for line in reversed(lines):
        if '"event": "tick"' in line:
            try:
                return json.loads(line)
            except ValueError as e:
                print(f"  NOTE: unreadable tick row in the dispatch ledger: {e}", file=sys.stderr)
    return {}


def _script_reviews():
    """(running, queued, done): script reviews from the dispatch ledger and truncation-review.jsonl.
    running: [{path, at, pid}], queued: [path], done: [row] oldest first."""
    running, started, finished = [], {}, set()
    try:
        with open(WORKSPACE / "workspace" / "dispatch-loop.jsonl", "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 3_000_000))
            lines = f.read().decode("utf-8", "replace").splitlines()[1:]
    except OSError as e:
        print(f"  NOTE: dispatch ledger unreadable: {e}", file=sys.stderr)
        lines = []
    for line in lines:
        if '"script-review"' not in line:
            continue
        try:
            r = json.loads(line)
        except ValueError as e:
            print(f"  NOTE: unreadable script-review row in the dispatch ledger: {e}", file=sys.stderr)
            continue
        if r.get("event") == "started":
            started[r.get("pid")] = r
        elif r.get("event") == "finished":
            finished.add(r.get("pid"))
    for pid, r in started.items():
        if pid not in finished and Path(f"/proc/{pid}").exists():
            running.append({"path": r.get("chunk") or "?", "at": r.get("at", ""), "pid": pid})
    queued = [q.get("key") for q in (_last_tick().get("queued") or []) if q.get("kind") == "script-review"]
    done, _bad = _jsonl_tail(WORKSPACE / "workspace" / "truncation-review.jsonl", 2000)
    return running, queued, done


def _session_pipeline():
    """The session-review pipeline, each stage a list: queued (requests), reviewing (running
    extract jobs), caught (findings waiting for an RCA), rca (RCAs running), done (RCAs finished).
    Running jobs come from who_works, so each carries the ROLE doing it."""
    import session_review as sr
    ww = _who()
    d = ww.snapshot() if ww else {"roles": {}}
    running = [(role, i) for role, rr in d["roles"].items() for i in rr.get("now", [])]
    reviewing = [(role, i) for role, i in running if i["kind"] == "session-review"]
    rca_now = [(role, i) for role, i in running if i["kind"] == "rca"]
    busy = {i["key"] for _, i in running}
    led, _ = _jsonl_tail(sr.LEDGER, 100000)
    done = [r for r in led if r.get("event") == "rca"]
    caught = []
    for f in sr.pending_findings():
        if f.name in busy:
            continue
        try:
            caught.append(json.loads(f.read_text(encoding="utf-8")))
        except (OSError, ValueError) as e:
            caught.append({"key": f.stem, "quote": f"(unreadable: {e})"})
    queued = [f for f in sr.pending_requests() if f.name not in busy]
    return {"queued": queued, "reviewing": reviewing, "caught": caught, "rca": rca_now, "done": done}


def unlanded_pending(rq):
    """reviewq.pending() without the branches already in main (asked of git via reviewq.branch_merged)."""
    def _landed(r):
        try:
            exists, unmerged = rq.branch_merged(r.get("branch") or "")
            return exists and not unmerged
        except Exception:                                           # noqa: BLE001 -- unknown is shown, not hidden
            return False
    return [r for r in rq.pending() if not _landed(r)]


def technical_holders():
    """The display names of the roles holding the TECHNICAL escalation, read from the Models tab (t9554), escaped.
    A miss is said, never a silent "Claude"."""
    try:
        import task_routing as _tr
        _d = _tr.load()
        txt = ", ".join(((_d.get("roles") or {}).get(k) or {}).get("name", k) for k in _tr.holders("technical", _d))
    except Exception as e:                                          # noqa: BLE001 -- said on the page
        txt = f"UNKNOWN ({type(e).__name__})"
    return esc(txt or "no Technical holder")


def claude_waiting():
    """Everything waiting on Claude, from every pipeline: [(pipeline, stage it left, html card)].

    Jas, 2026-09-29: "if anything could go from its stage to waiting for you, then again, it might
    make sense to go to the review tab." So this is one list, shown whole on Review; Production shows
    only a one-line count per column at the stage the work left."""
    # WHO "WAITING ON" MEANS IS READ FROM THE MODELS TAB (t9554): the roles holding the TECHNICAL escalation,
    # by their display names. It was the literal "CLAUDE" here, so DSH holding technical never showed.
    flag = technical_holders().upper()
    out = []
    cols, _stages, err = _task_board()
    rq = _reviewq()
    in_review = [t for t, _ in (cols or {}).get("in-review", [])]
    if rq is not None:
        # A BRANCH ALREADY IN MAIN IS NOT WAITING (2026-10-29): 10 of 21 "delivered" branches had tips that were
        # ancestors of main -- nothing left to merge -- and still sat here, because pending() asks for a verdict,
        # not whether the work landed. Ask git, as "Ready to merge" already does (reviewq.branch_merged).
        pending = unlanded_pending(rq)
        seen = {r.get("task") for r in pending}
        for r in pending:
            out.append(("tasks", "building", _task_card(r.get("task") or "?", extra=(
                f'<div class="small"><span class="nextflag">{flag}</span> delivered branch to review '
                f'&middot; {esc(r.get("served") or "local model")} &middot; {esc(str(r.get("at") or ""))}</div>'))))
        for t in in_review:
            if t not in seen:
                out.append(("tasks", "building", _task_card(t, extra='<div class="small"><span class="nextflag">'
                            f'{flag}</span> a delivered branch for it waits on review</div>')))
    held = [x for x in (_last_tick().get("skipped") or [])
            if "HELD FOR CLAUDE" in str(x.get("why", "") if isinstance(x, dict) else x[1])]
    for x in held:
        c, why = (x.get("chunk"), x.get("why")) if isinstance(x, dict) else (x[0], x[1])
        m = re.search(r"-(t\d+)-", str(c))
        extra = (f'<div class="small"><span class="nextflag">{flag}</span> brief {esc(c)} held: '
                 f'{esc(str(why)[:160])}</div>')
        out.append(("tasks", "briefed", _task_card(m.group(1), extra=extra) if m else
                    f'<div class="card"><b>{esc(c)}</b>{extra}</div>'))
    for st, lab in (("done-check", "the scoping answer says it is already done; Technical verifies and closes"),
                    ("blocked", "blocked; Technical looks at why")):
        for t, _ in (cols or {}).get(st, []):
            out.append(("tasks", "scoped", _task_card(t, extra=f'<div class="small"><span class="nextflag">'
                                                          f'{flag}</span> {lab}</div>')))
    ww = _who()
    try:
        orph = ww.test_runs()["orphans"] if ww else []
    except Exception as e:                                          # noqa: BLE001
        orph = []
        print(f"  NOTE: leaked-helper scan failed: {e}", file=sys.stderr)
    if orph:
        oldest = max(o["elapsed_s"] for o in orph)
        out.append(("tasks", "testing", f'<div class="card"><span class="nextflag">{flag}</span> '
                    f'<b>{len(orph)} test helper(s) left running</b> after their test exited (oldest '
                    f'{_mins(oldest)}). pids: {esc(", ".join(str(o["pid"]) for o in orph[:30]))}</div>'))
    return out


def _stage(label, cards, open_=False, empty="&mdash;"):
    """One stage of a column: a self-contained <details>, so nothing can unbalance the page. Collapsed
    on load (Jas, 2026-09-29: the Tasks stages all loaded expanded); the count is in the header."""
    body = "".join(cards) or f'<div class="small">{empty}</div>'
    return (f'<details class="kstage"{" open" if open_ else ""}><summary><b>{label} ({len(cards)})</b>'
            f'</summary>{body}</details>')


def _flag(label, detail="", soon=False):
    return (f'<div class="small"><span class="nextflag{" soon" if soon else ""}">{label}</span>'
            + (f' {detail}' if detail else "") + '</div>')


def _n(task_id, name, state=""):
    """A Production stage is named for its Flow task, word for word (Jas, 2026-10-01: "Can the
    stages be renamed to the task ... to truly see if we even have all the stages"), with its state
    when it is the task's queue or its output, and a link to the task's description page."""
    return f'{name}{(" &middot; " + state) if state else ""}'


def stage_moves(tid, prev=None, nxt=None):
    """Top / up / down for a card, relative to its neighbours IN THE STAGE it is shown in."""
    out = move_form(tid, "top", "&#8673;", title="Send to the top")
    if prev:
        out += move_form(tid, "before", "&#9650;", target=prev, title=f"Above {prev}")
    if nxt:
        out += move_form(nxt, "before", "&#9660;", target=tid, title=f"Below {nxt}")
    return f'<div class="acts">{out}</div>'


def board_page():
    """The Production tab: one column per pipeline, highest priority on the left; each column moves
    from its first stage at the bottom to its last at the top.

    Jas, 2026-09-29: "have columns on the production tab that show the progression from earlier
    stage to end stage as you move from bottom to top. The columns should be arranged from highest
    priority on the left to lowest priority on the right." The order is the dispatcher's own
    (dispatch_loop.LEVELS), so a change there reorders the page. What waits on Claude lives on
    Review; each column keeps a one-line count where the work left it."""
    cols, stages, err = _task_board()
    if err:
        return (f'<div class="empty"><b>Could not build the board.</b><br>{esc(err)}<br>'
                f'This is not "nothing in progress" -- nothing was read.</div>')
    titles = {m.get("id"): m.get("title", "") for m, _ in ts.ordered()}
    ww = _who()
    try:
        crew = ww.marks() if ww else {}
        tmarks = ww.test_marks() if ww else {}
    except Exception as e:                                          # noqa: BLE001
        crew, tmarks = {}, {}
        print(f"  NOTE: card marks unreadable: {e}", file=sys.stderr)
    waiting = claude_waiting()

    def stub(pipeline):
        n = sum(1 for p, _, _ in waiting if p == pipeline)
        return (f'<div class="card small kstub">{n} in Merge Review or Technical ({technical_holders()}) &rarr; <a href="/review">Review</a></div>'
                if n else "")

    def task_card(tid, extra="", prev=None, nxt=None):
        if not re.match(r"t\d+$", tid):          # a build chunk with no task behind it
            return f'<div class="card"><b>{esc(tid)}</b> <span class="small">(no task)</span>{extra}</div>'
        mk, tm = crew.get(tid), tmarks.get(tid)
        flags = (_flag(mk[1], soon=mk[0] != "now") if mk else "") + (_flag(tm[0], tm[1]) if tm else "")
        return (f'<div class="card{" next" if (mk or tm) else ""}"><a href="/plan/{esc(tid)}">'
                f'<b>{esc(tid)}</b> {esc(titles.get(tid, ""))}</a>{flags}{extra}{chunks_html(tid)}'
                f'{stage_moves(tid, prev, nxt)}</div>')

    def stage(label, tids, open_=False, labels=None):
        """A stage whose cards carry the reorder buttons, moving within the stage (Jas, 2026-10-01:
        "add the reorder buttons to all stages of the production flow ... so I can reorder a brief").
        The task queue's order is the order builds and preflights start (dispatch_loop
        .chunks_in_task_order), so moving a card moves the work."""
        tids = [t for t in tids if t]
        labels = labels or {}
        return _stage(label, [(f'<div class="small muted">{esc(labels[t])}</div>' if t in labels else "")
                              + task_card(t, prev=tids[i - 1] if i else None,
                                          nxt=tids[i + 1] if i + 1 < len(tids) else None)
                              for i, t in enumerate(tids)], open_=open_)

    # ---- THE BACKLOG IS EACH COLUMN'S FIRST STAGE (Jas, 2026-10-01: "move the relevant sections
    # inside of the production tab underneath their respective columns and have them collapsed").
    # THE STAGES BEFORE SCOPING (Jas, 2026-10-14: "Task Backlog > Embedding > other...? > Waiting for
    # Scoping > Scoping"): intake.pre_scoping_stages splits the backlog by what the pipeline knows --
    # not intaken yet, intaken (likely files known), linked to another task by a shared file (scoped in
    # one batch), and the next tasks the scoper takes.
    pre = {"backlog": [], "intake": [], "linked": [], "waiting": []}
    try:
        marks = q4_marks()
        rows_ = backlog_rows()
        card = {m["id"]: backlog_card(n, m, marks) for n, (m, _) in enumerate(rows_, 1)}
        try:
            import intake as _ik
            import manager_next as _mn2
            _picked, _ = _mn2.upcoming(n=6)
            split = _ik.pre_scoping_stages([m["id"] for m, _ in rows_], {m["id"]: b for m, b in rows_},
                                           waiting=_picked)
        except Exception as e:                                      # noqa: BLE001 -- said; all in the backlog
            print(f"  NOTE: pre-scoping stages unreadable: {e}", file=sys.stderr)
            split = {"backlog": [m["id"] for m, _ in rows_], "intake": [], "linked": [], "waiting": []}
        pre = {k: [card[t] for t in v if t in card] for k, v in split.items()}
    except Exception as e:                                          # noqa: BLE001
        pre["backlog"] = [f'<div class="bad small">backlog unreadable: {esc(f"{type(e).__name__}: {e}")}</div>']
    backlog = [c for v in pre.values() for c in v]

    # ---- Tasks (general production): being scoped -> ... -> merged
    scoping_all = ww.scoping_queue_all() if ww else []
    scoping = scoping_all[0] if scoping_all else None
    shown = {"merged", "in-review", "supervision", "building", "preflight", "briefed", "scoped",
             "parked", "held-claude", "held-red"}
    _sup = [t for t, _ in cols.get("supervision", [])]
    _ready_ids = _sup + [t for t, _ in cols.get("briefed", []) if t not in _sup]
    _ready_labels = {t: "failed build -- the rules decide on the next tick: retry, back to scoping, harness task or escalate"
                     for t in _sup}
    _ready_labels.update({t: why for t, why in supervision_retry_labels().items()
                          if t in _ready_ids and t not in _sup})
    task_col = [
        stage(_n("merged", "Merged"), [t for t, _ in cols.get("merged", [])]),
        stub("tasks"),
        # MERGE REVIEW WAITS ON CLAUDE, so it is on Review, not here (Jas, 2026-10-08); the stub above
        # links to it.
        stage(_n("building", "Building"), [t for t, _ in cols.get("building", [])]),
        # AFTER PREFLIGHT, BEFORE A BUILD (2026-10-08): what each preflighted chunk is waiting on.
        # SUPERVISION FOLDED IN (10-19 plan, "Supervision stage: rules first"): a failed build waiting
        # on the supervisor is ready to build again once it decides, so it shows here with what it
        # waits on, and a chunk the rules sent back to retry says why.
        stage(_n("building", "Ready to build", "waiting for a lane"), _ready_ids, labels=_ready_labels),
        # Held and parked chunks are assigned to whoever they wait on (route_waiting), so they are on
        # the Review tab, not here.
        stage(_n("preflight", "Preflight"), [t for t, _ in cols.get("preflight", [])]),
        stage(_n("scope_check", "Scope check"), [t for t, _ in cols.get("scoped", []) if t not in scoping_all]),
        stage(_n("scoping", "Scoping"), scoping_all),
        _stage(_n("scoping", "Waiting for Scoping"), pre["waiting"]),
        _stage(_n("intake", "Linked", "batched by shared files"), pre["linked"]),
        _stage(_n("intake", "Intake", "likely files found"), pre["intake"]),
        _stage(_n("backlog", "Task Backlog"), pre["backlog"]),
    ]
    extra_st = [st for st in reversed(stages) if st not in shown and st not in REVIEW_STAGES
                and st != "in-review"]
    for st in extra_st:
        task_col.insert(1, stage(esc(STAGE_LABEL.get(st, st)), [t for t, _ in cols.get(st, [])]))
    # THE TOTAL IS WORK STILL TO DO (Jas, 2026-10-01): the backlog counts, done stages do not.
    # AND IT IS THE SUM OF THE STAGES SHOWN (Jas, 2026-10-18: "The total says 159, but the items below
    # add to 149"). It counted in-review, parked and the held stages -- shown on the Review tab, not
    # here -- and every task being scoped twice (as "scoped" and again in Scoping).
    n_tasks = (sum(len(cols.get(st, [])) for st in ("supervision", "building", "briefed", "preflight"))
               + len([t for t, _ in cols.get("scoped", []) if t not in scoping_all])
               + len(scoping_all) + len(backlog)
               + sum(len(cols.get(st, [])) for st in extra_st))

    # ---- Session reviews: being reviewed -> caught -> RCA -> RCA done (tasks go to the Backlog)
    try:
        sp = _session_pipeline()
    except Exception as e:                                          # noqa: BLE001
        sp = None
        sess_col = [f'<div class="bad small">session reviews unreadable: {esc(f"{type(e).__name__}: {e}")}</div>']
    if sp is not None:
        def job(role, i):
            return (f'<div class="card next"><b>{esc(i["key"])}</b>'
                    + _flag(f"{role.upper()} WORKING") + '</div>')
        done_cards = [f'<div class="card small"><b>{esc(r.get("key", ""))}</b> '
                      + (f'<a href="/file?path={urllib.parse.quote(str(r.get("report", "")))}">report</a>'
                         if r.get("report") else "")
                      + f'<br>tasks filed: {esc(", ".join(r.get("tasks") or []) or "none")}</div>'
                      for r in reversed(sp["done"])]
        caught_cards = [f'<div class="card small"><b>{esc(c.get("kind", ""))}</b> &middot; '
                        f'{esc(str(c.get("quote", ""))[:220])}<br><span class="small">'
                        f'{esc(c.get("request", ""))}</span></div>' for c in sp["caught"]]
        sess_col = [_stage(_n("rca", "RCA", "done"), done_cards, open_=False),
                    stub("sessions"),
                    _stage(_n("rca", "RCA"), [job(r, i) for r, i in sp["rca"]]),
                    _stage(_n("rca", "RCA", "waiting"), caught_cards),
                    _stage(_n("session_review", "Reviewing"), [job(r, i) for r, i in sp["reviewing"]])]
        try:
            import session_review as _sr
            sess_col.append(_stage(_n("session_review", "Session Backlog"), [f'<div class="card small">{esc(f.stem)}</div>'
                                                         for f in _sr.pending_requests()]))
        except Exception as e:                                      # noqa: BLE001
            sess_col.append(f'<div class="bad small">session-review queue unreadable: {esc(str(e))}</div>')
        try:
            _queued = len(_sr.pending_requests())
        except Exception:                                           # noqa: BLE001 -- shown above as unreadable
            _queued = 0
        n_sess = len(sp["rca"]) + len(sp["caught"]) + len(sp["reviewing"]) + _queued   # not the done ones
    else:
        n_sess = 0

    # ---- Script reviews: running -> done today
    try:
        s_run, s_q, s_done = _script_reviews()
    except Exception as e:                                          # noqa: BLE001
        s_run, s_q, s_done = [], [], []
        print(f"  NOTE: script reviews unreadable: {e}", file=sys.stderr)
    today = time.strftime("%Y-%m-%d", time.gmtime())
    s_today = [r for r in s_done if str(r.get("at", "")).startswith(today)]
    script_col = [
        _stage(_n("script_review", "Done Today"), [f'<div class="card small"><b>{esc(r.get("path", "?"))}</b><br>'
                                  f'{esc(r.get("status", "?"))} &middot; {len(r.get("findings") or [])} finding(s)'
                                  f' &middot; {esc(str(r.get("at", ""))[11:16])}Z</div>'
                                  for r in reversed(s_today)], open_=False),
        stub("scripts"),
        _stage(_n("script_review", "Reviewing"), [f'<div class="card next"><b>{esc(r["path"])}</b>'
                                  + _flag(f"{_role_name('script_review').upper()} WORKING",
                                          f'since {esc(str(r["at"])[11:16])}Z') + '</div>'
                                  for r in s_run]),
        _stage(_n("script_review", "Script Backlog"), [f'<div class="card small">{esc(k)}</div>' for k in s_q]),
    ]
    n_script = len(s_run) + len(s_q)                  # to do: running + queued, not reviewed today

    # ---- the order is the dispatcher's
    try:
        import dispatch_loop as _dl
        lv = _dl.LEVELS
    except Exception:                                               # noqa: BLE001
        lv = {"session-review": 3, "script-review": 4}
    # ---- Corpus index, leftmost (Jas, 2026-10-13: "add a corpus index backlog to the production tab on
    # the left"): the Embedder's work -- transcripts changed since the hourly index, records to embed.
    try:
        import lane_status as _ls
        cb = _ls.corpus_backlog()
        backlog_cards = ([f'<div class="card small">transcript <b>{esc(t["session"][:8])}</b> &middot; {esc(t["origin"])}'
                          f' &middot; changed {esc(t["at"][11:16])}Z</div>' for t in cb["transcripts"]]
                         + [f'<div class="card small">record <b>{esc(r["id"])}</b> &middot; {esc(r["fact"][:80])}</div>'
                            for r in cb["records"]])
        corpus_col = [
            _stage("Indexed", [f'<div class="card small">last index {esc((cb["indexed_at"] or "never")[5:16].replace("T", " "))}Z'
                               + (f' &middot; next {esc(cb["next_run"])}' if cb["next_run"] else "") + '</div>']),
            _stage("Indexing", [f'<div class="card next"><b>corpus-index</b>' + _flag("EMBEDDER WORKING") + '</div>']
                   if cb["running"] else []),
            _stage("Corpus Backlog", backlog_cards),
        ]
        n_corpus = len(backlog_cards)
    except Exception as e:                                          # noqa: BLE001 -- said, not swallowed
        corpus_col = [f'<div class="bad small">corpus index unreadable: {esc(f"{type(e).__name__}: {e}")}</div>']
        n_corpus = 0
    columns = sorted([(-1, "Corpus index", n_corpus, corpus_col), (lv.get("session-review", 3), "Session reviews", n_sess, sess_col),
                      (lv.get("script-review", 4), "Script reviews", n_script, script_col),
                      (99, "Tasks", n_tasks, task_col)], key=lambda c: c[0])
    total = sum(c[2] for c in columns)
    out = [scheduler_controls(),
           f'<p class="small"><b>In production: {total}</b> &middot; columns by priority, highest on the left; '
           'each moves from its first stage at the bottom to its last at the top. What waits on a person is '
           'on <a href="/review">Review</a>; tasks waiting on your answer: <a href="/plan?mine=1">Waiting on you</a>. '
           '<a href="/status/full">Full machine status</a> (slow).</p>',
           '<div class="kanban">']
    for _lvl, name, n, body in columns:
        out.append(f'<div class="kcol"><div class="khead">{name} ({n})</div>{"".join(body)}</div>')
    out.append('</div>')
    return "".join(out)


def on_board():
    cols, _, err = _task_board()
    return set() if err else {t for items in cols.values() for t, _ in items}


def system_card(s):
    """t209: the machine's figures from lane_status.system_snapshot(), one card. A section that
    could not be read shows its error rather than a number (L-146: absence is not a pass)."""
    e = lambda v: html.escape(str(v))
    def line(name, sec, fmt):
        if isinstance(sec, dict) and "error" in sec:
            return f"<b>{name}</b> unreadable: {e(sec['error'])}"
        try:
            return f"<b>{name}</b> {fmt(sec)}"
        except (KeyError, TypeError) as x:
            return f"<b>{name}</b> unreadable: {e(type(x).__name__)}: {e(x)}"
    t = s.get("temp_c")
    rows = [
        line("CPU", s.get("cpu"), lambda c: f"{e(c['load_pct'])}% -- load {e(c['load1'])} on {e(c['cores'])} cores"),
        line("GPU", s.get("gpu"), lambda g: f"{e(g['util_pct'])}% busy, {e(g['temp_c'])} C"),
        line("RAM", s.get("ram"), lambda r: f"{e(r['used_gib'])} of {e(r['total_gib'])} GiB, {e(r['used_pct'])}%"),
        line("Disk", s.get("disk"), lambda d: f"{e(d['used_pct'])}% of {e(d['total_gib'])} GiB"),
        f"<b>Temp</b> {e(t) + ' C' if t is not None else 'not reported'}",
        line("Network", s.get("net"), lambda n: f"{e(n['rx_gib'])} GiB in, {e(n['tx_gib'])} GiB out since boot"),
    ]
    return '<div class="card"><h2>System</h2>' + "<br>".join(rows) + "</div>"


def status_page():
    """What the HiveMind is doing right now. Jas, 2026-09-14: a tab showing "the data you
    have ... in case they are stalled."

    EVERY FIGURE IS DERIVED ON READ by scripts/hivestatus.py, never from a status field. The
    reason is on the page itself: three watchers currently say state="running" with their last
    heartbeat about 48 hours old, and projects/local-inference/STATUS.md says "A real model is
    serving" about a lane that is not running (t295). A page that trusted either would be
    confidently wrong, which is worse than blank.

    IT FAILS LOUD. If the snapshot cannot be taken, it says so and shows the error -- a status
    page that goes quiet when it cannot look is the exact shape this repository keeps finding
    (L-146: absence is not a pass).
    """
    import subprocess as _sp
    try:
        r = _sp.run([sys.executable, str(WORKSPACE / "scripts" / "hivestatus.py"), "--json"],
                    capture_output=True, text=True, timeout=600, stdin=_sp.DEVNULL,
                    cwd=str(WORKSPACE))
        if r.returncode != 0 or not (r.stdout or "").strip():
            raise RuntimeError(f"exit {r.returncode}: {(r.stderr or r.stdout or '').strip()}")
        d = json.loads(r.stdout)
    except Exception as e:
        return (f'<div class="empty"><b>Could not take the snapshot.</b><br>'
                f'{html.escape(f"{type(e).__name__}: {e}")}<br><br>'
                f'This is NOT "everything is fine" -- nothing was read. '
                f'Run <code>python3 scripts/hivestatus.py</code> to see why.</div>')

    out = [f'<p class="rank">Derived on read at {html.escape(d.get("at",""))} — '
           f'nothing here comes from a stored status field.</p>']
    try:
        import lane_status
        out.insert(0, system_card(lane_status.system_snapshot()))
    except Exception as e:                                          # noqa: BLE001 -- shown, not swallowed
        out.insert(0, f'<div class="bad">System figures unreadable: {html.escape(f"{type(e).__name__}: {e}")}</div>')

    # ---- models
    m = d.get("models") or {}
    probe = (m.get("probe") or {})
    up = probe.get("up")
    out.append("<h2>Models</h2>")
    # GPU UTILISATION FIRST, because it is the number Jas asks for. nvidia-smi reports it
    # correctly on this GB10 -- it reads 0% when the card is genuinely idle and 95%+ under
    # load. Three samples, because one instant between decode steps can read low.
    g = d.get("gpu") or {}
    got = [x for x in (g.get("samples") or []) if isinstance(x, int)]
    if got:
        peak = max(got)
        gcls = "ok" if peak > 5 else "empty"
        out.append(f'<div class="{gcls}"><b>GPU {peak}%</b> &nbsp; '
                   f'samples {" / ".join(str(x) for x in got)}% &nbsp;&middot;&nbsp; '
                   f'unified memory {g.get("unified_used_gb")}G used of '
                   f'{g.get("unified_total_gb")}G, {g.get("unified_free_gb")}G free</div>')
    else:
        out.append(f'<div class="bad">GPU utilisation unavailable: '
                   f'{html.escape(str(g.get("note") or "no samples"))}</div>')
    # THE PROBE COVERS ONE MODEL; THE COUNT COVERS ALL OF THEM. Jas: "The green box only shows
    # only 1 is up." It was reporting whichever model the probe happened to ask, which reads as
    # a claim about the whole box. Say how many are resident AND which one answered.
    cls = "ok" if up else "bad"
    res = m.get("resident") or []          # defined HERE: it was used before assignment
    n_res = len(res)
    out.append(f'<div class="{cls}"><b>{n_res} model(s) resident</b> &nbsp;&middot;&nbsp; '
               f'inference path {html.escape(str(probe.get("say") or m.get("error") or "not probed"))}</div>')
    if res:
        out.append("<table><tr><th>model</th><th>size</th><th>context</th></tr>")
        for x in res:
            out.append(f'<tr><td><b>{html.escape(str(x.get("name")))}</b></td>'
                       f'<td>{html.escape(str(x.get("size")))}</td>'
                       f'<td>{html.escape(str(x.get("context")))}</td></tr>')
        out.append("</table>")
        pass  # the layers column is gone; Jas does not want it or its caveat
    else:
        out.append('<div class="empty">Nothing resident on the GPU.</div>')

    # ---- watchers: the stall view Jas asked for
    out.append("<h2>Watchers</h2>")
    ws = d.get("watchers") or []
    # TWO DIFFERENT THINGS, AND ONLY ONE OF THEM IS A STALL (2026-09-16).
    # This counted both as "say they are running and are not", which is true but useless: a
    # leftover file from a process that finished days ago needs deleting, and a process that
    # is ALIVE and has stopped beating is hung and may still be holding a model. Lumping them
    # together meant four dead files hid the case worth acting on. hivestatus now reads the
    # pid the beat has always carried, so the two are separable by fact rather than by age.
    claiming = [w for w in ws if str(w.get("state", "")).lower() in ("running", "doing")]
    ended = [w for w in claiming if w.get("process_gone")]
    stalled = [w for w in claiming if w.get("stale") and not w.get("process_gone")]
    if stalled:
        out.append(f'<p class="rank"><b>{len(stalled)} watcher(s) are HUNG</b> — the process is '
                   f'still alive and has stopped beating, so it may still be holding a model. '
                   f'This is the case worth acting on.</p>')
    if ended:
        out.append(f'<p class="rank">{len(ended)} leftover beat file(s) — the process is gone '
                   f'and the file outlived it. Not a stall, and nothing depends on them; '
                   f'they are mine to clean up.</p>')
    out.append("<table><tr><th>watcher</th><th>state</th><th>last beat</th><th>note</th></tr>")
    for w in ws:
        # RED MEANS "JAS MUST ACT", per his rule 2026-09-14: "Red means bad to me. It means
        # something needs my attention. If I can ignore it, leave it black." A stale watcher is
        # MINE to fix, so it is plain text however wrong it is. Colouring my work red trains him
        # to ignore red, and then the one thing that does need him is invisible.
        mark = ""
        out.append(f'<tr{mark}><td>{html.escape(str(w.get("name")))}</td>'
                   f'<td>{html.escape(str(w.get("state")))}</td>'
                   f'<td>{w.get("age_minutes")} min</td>'
                   f'<td>{html.escape(str(w.get("note") or ""))}</td></tr>')
    out.append("</table>")

    # ---- production
    pr = d.get("production") or {}
    out.append("<h2>In production</h2>")
    ch = pr.get("chunks") or []
    if ch:
        out.append("<table><tr><th>chunk</th><th>state</th><th>target suite</th></tr>")
        for c in ch:
            mark = ""            # outstanding chunks are models' work, not Jas's
            out.append(f'<tr{mark}><td>{html.escape(str(c.get("chunk")))}</td>'
                       f'<td>{html.escape(str(c.get("say")))}</td>'
                       f'<td><code>{html.escape(str(c.get("target")))}</code></td></tr>')
        out.append("</table>")
    out.append('<p class="rank">A chunk is <b>already on main</b> when its target suite is '
               'green — there is nothing to dispatch and <b>nothing to merge</b>. Work that is '
               'ready to merge is a BRANCH, and it appears in <a href="/review">Review</a>, not '
               'here. This section is what the models are working, not a merge queue.</p>')
    out.append(_lanes_html(pr))
    out.append(remote_devices_html("h3"))
    infl = pr.get("in_flight") or []
    out.append(f'<p>Dispatch processes in flight: <b>{len(infl)}</b>'
               + (" — nothing is running." if not infl else "") + "</p>")
    for x in infl:
        out.append(f'<p class="rank"><code>{html.escape(str(x.get("line")))}</code></p>')
    br = pr.get("branches_awaiting_review") or []
    out.append(f'<p>Delivered branches on disk: <b>{len(br)}</b></p>')
    if br:
        out.append('<p class="rank">' + html.escape(", ".join(br)) + "</p>")

    # ---- pending production: the section Jas asked for, and the answer is mostly "nothing"
    pend = d.get("pending") or []
    ready = [x for x in pend if x.get("ready")]
    out.append("<h2>Pending production</h2>")
    out.append('<p class="rank">Scoped, oracle RED, and enrolled where something will actually '
               'run it. All three, or it is not pending.</p>')
    if ready:
        out.append(f'<div class="ok"><b>{len(ready)} of {len(pend)}</b> brief(s) will be picked '
                   f'up when a model frees.</div>')
    else:
        out.append(f'<div class="bad"><b>Nothing is pending.</b> {len(pend)} brief(s) exist and '
                   f'none of them will start on its own.</div>')
    from collections import Counter
    kinds = Counter(x.get("kind") for x in pend)
    out.append(f'<p class="rank">Of {len(pend)}: <b>{kinds.get("ready", 0)} ready</b>, '
               f'{kinds.get("spent", 0)} already done (target green — the brief is spent, '
               f'nobody should pick it up), {kinds.get("not-a-chunk", 0)} plans rather than '
               f'chunks (no target suite, never dispatchable), '
               f'{kinds.get("needs-an-oracle", 0)} needing an oracle written first, '
               f'{kinds.get("not-enrolled", 0)} genuinely stranded. '
               f'<b>None of these need a decision from you</b> — the ones that do are in '
               f'<a href="/review">Review</a>.</p>')
    out.append("<table><tr><th>brief</th><th>chunk</th><th>state</th></tr>")
    for x in pend:
        if x.get("kind") in ("spent", "not-a-chunk"):
            continue          # not pending and not a problem; the counts above say how many
        mark = ""            # a blocked brief is mine to unblock
        out.append(f'<tr{mark}><td><code>{html.escape(str(x.get("brief")))}</code></td>'
                   f'<td>{html.escape(str(x.get("chunk") or "—"))}</td>'
                   f'<td>{html.escape(str(x.get("say")))}</td></tr>')
    out.append("</table>")
    out.append('<p class="rank">"NOT ENROLLED" means exactly that: the only list a free model '
               'pulls from is the chunk table in <code>scripts/autonomy_loop.sh</code>. A brief '
               'that is written, preflighted and has a red oracle still never starts unless it '
               'is in that table.</p>')

    # ---- queues
    qd = d.get("queues") or {}
    out.append("<h2>Queues</h2>")
    out.append(f'<p>Tasks: <b>{qd.get("tasks_total")}</b> &nbsp; '
               f'Needs you: <b>{len(qd.get("needs_jas") or [])}</b></p>')
    rv = (qd.get("review") or "").strip().splitlines()
    out.append(f'<p>Review queue: {html.escape(rv[0].strip() if rv else "?")}</p>')
    nj = qd.get("needs_jas") or []
    if nj:
        out.append("<p><b>Waiting on a decision from you:</b></p><ul>")
        for f in nj:
            tid = f.split("--")[0]
            out.append(f'<li><a href="/plan/{html.escape(tid)}">{html.escape(tid)}</a> '
                       f'{html.escape(f.split("--",1)[1].replace(".md","").replace("-"," ") if "--" in f else "")}</li>')
        out.append("</ul>")
    top = qd.get("tasks_top") or []
    if top:
        out.append("<p><b>Next up</b></p><ul>")
        for t in top:
            out.append(f"<li>{html.escape(t)}</li>")
        out.append("</ul>")

    out.append(notifications_section())
    return "\n".join(out)


def notifications_section():
    """Which local-model events ping Jas's phone, and whether this browser is subscribed.

    IT LIVES ON STATUS, NOT ON THE MODELS TAB. Jas, 2026-09-16: "I don't want you to put this
    into the existing setup tab on the models tab, because that's for the model setup. This
    notification setup needs to be in the status tab." The Console's /settings page is model
    knobs -- reasoning effort, token budgets -- and these are alerts about the pipeline, which
    is what this tab is already for.

    THE TOGGLES WORK OVER PLAIN HTTP; SUBSCRIBING DOES NOT. A service worker needs a secure
    context, so the button below can only appear once the Hub is served over TLS. It says so
    rather than failing silently when tapped.
    """
    import subprocess as _sp
    try:
        r = _sp.run([sys.executable, str(WORKSPACE / "scripts" / "notify.py"), "--status"],
                    capture_output=True, text=True, timeout=60, stdin=_sp.DEVNULL,
                    cwd=str(WORKSPACE))
        raw = r.stdout or ""
    except Exception as e:                                              # noqa: BLE001
        return (f'<h2>Notifications</h2><div class="bad"><b>Could not read the notification '
                f'settings.</b><br><code>{html.escape(str(e))}</code></div>')

    events, devices, have_keys = [], 0, False
    for line in raw.splitlines():
        s = line.strip()
        if s.startswith("devices subscribed:"):
            devices = int(s.rsplit(":", 1)[1].strip() or 0)
        elif s.startswith("VAPID keypair:"):
            have_keys = "present" in s
        elif s.startswith(("ON ", "off ")):
            on = s.startswith("ON")
            rest = s.split(None, 1)[1] if len(s.split(None, 1)) > 1 else ""
            key, _, desc = rest.partition(" ")
            events.append((key.strip(), desc.strip(), on))

    o = ['<h2>Notifications</h2>',
         '<p class="rank">What the local models are allowed to wake your phone for. Every one '
         'of these was something that stalled silently: a task held, a model waiting on an '
         'answer, a branch built and unmerged, an overnight run that died.</p>']
    if not events:
        # loudcap, not a slice. This is the page that explains why notifications are broken, so
        # a silently shortened error message would hide the reason for the reason -- and
        # library_checks blocks the bare slice for exactly that, naming loudcap as the answer.
        sys.path.insert(0, str(WORKSPACE / "scripts"))
        from loudcap import cap as _cap
        o.append('<div class="bad"><b>No events were reported.</b> '
                 f'<code>notify.py --status</code> said:'
                 f'<pre>{html.escape(_cap(raw, 800, "notify.py --status output"))}</pre></div>')
        return "\n".join(o)

    o.append('<table><tr><th>event</th><th>what it means</th><th></th></tr>')
    for key, desc, on in events:
        o.append(f'<tr><td><code>{html.escape(key)}</code></td>'
                 f'<td>{html.escape(desc)}</td>'
                 f'<td><form method="post" action="/status/notify">'
                 f'<input type="hidden" name="event" value="{html.escape(key)}">'
                 f'<input type="hidden" name="on" value="{"off" if on else "on"}">'
                 f'<button>{"ON" if on else "off"}</button></form></td></tr>')
    o.append("</table>")
    o.append('<p class="rank">A toggle takes effect on the next alert; nothing is queued while '
             'it is off.</p>')

    # THE STATE THAT DECIDES WHETHER ANY OF THIS DOES ANYTHING, said plainly. A page that
    # showed four cheerful toggles while no device could receive them would be the same
    # confident wrongness this tab exists to avoid.
    if devices:
        o.append(f'<div class="ok"><b>{devices} device(s) subscribed.</b> Alerts will arrive.</div>')
    else:
        # DECIDED IN THE BROWSER, NOT HERE (2026-09-16). This used to print the
        # enable-HTTPS instructions unconditionally whenever no device was subscribed, so it
        # went on telling Jas to run `tailscale cert` for hours after he had run it. The
        # server cannot answer this anyway: the SAME process now serves plain HTTP on :8091
        # and TLS through `tailscale serve` on :8443, so the correct message depends on which
        # URL is open. window.isSecureContext is the exact condition a service worker needs,
        # so the page asks that and shows one of the two.
        o.append('<div id="sub-insecure" class="bad" hidden><b>No device is subscribed.</b><br>'
                 'Subscribing needs a service worker, and a service worker needs a secure '
                 'context &mdash; this page is open over plain HTTP. HTTPS is already '
                 'running: open <a href="' + HTTPS_HUB + '/status"><b>' + HTTPS_HUB +
                 '/status</b></a> and the button appears.</div>')
        o.append('<div id="sub-secure" class="bad" hidden><b>No device is subscribed, so '
                 'nothing can arrive yet.</b><br>This browser can subscribe &mdash; tap '
                 'below, then allow notifications when the phone asks.'
                 '<form method="post" action="/status/subscribe" id="sub-form">'
                 '<input type="hidden" name="sub" id="sub-json">'
                 '<button type="button" id="sub-go">Subscribe this device</button>'
                 '</form><div id="sub-msg" class="small"></div></div>')
        o.append(SUBSCRIBE_JS)
    if not have_keys:
        o.append('<div class="bad">No VAPID keypair yet: '
                 '<code>python3 scripts/notify.py --init</code></div>')
    return "\n".join(o)


def q4_marks():
    """{task: (kind, label)} -- what the manager queue is doing with each task right now.

    kind: now (the manager is on it), next (the one it takes after), rest (worked already: it waits at
    the back of the rotation until the unworked tasks have had a pass), build (enrolled, skipped).
    Derived on read from the same code the queue uses (scripts/manager_next.py) and its
    progress.log, so the page cannot disagree with the loop.
    """
    try:
        import sys as _s
        sp = str(WORKSPACE / "scripts")
        if sp not in _s.path:
            _s.path.insert(0, sp)
        import manager_next as mn
    except Exception:
        return {}
    out = {}
    try:
        busy = mn.in_production()
        for tid in mn.queue_ids():
            if tid in busy:
                out[tid] = ("build", "with the builders")
                continue
            w = mn.last_worked(tid)
            f = next((WORKSPACE / "workspace" / "tasks").glob(f"{tid}--*.md"), None) if w else None
            if w and f is not None and f.stat().st_mtime <= w:     # same rule as manager_next
                out[tid] = ("rest", "last run produced nothing &middot; retry after the rest")
        picked, _ = mn.upcoming(1)
        if picked:
            out[picked[0]] = ("next", "MANAGER NEXT")
        ww = _who()                  # one reading of progress.log for every tab (who_works)
        for cur in (ww.scoping_queue_all() if ww else []):
            out[cur] = ("now", "MANAGER WORKING")
    except Exception:
        return out
    return out


def plan_list(area=None, picking=None, mine=False):
    """The queue.

    `picking` is the id of a task waiting for a destination -- the second half of
    Move before..., which turns a two-tap gesture into two ordinary form posts.

    `mine` is the cut that matters to Jas: tasks that cannot move until he answers.
    Everything else here is Claude Code's problem and he is welcome to ignore it.
    """
    # Tasks past Plan live on the Status board (Jas, 2026-09-24), so Plan shows what has not
    # started -- and, since 2026-09-26, every task still in the manager's queue (queued, not
    # held) even if an earlier answer put it on the board: it is waiting for another pass. "Waiting on you" still lists everything that needs him, wherever it is.
    everything = ts.ordered()
    rows = [r for r in everything if ts.tier_of(r[0].get("needs")) == "vision"] if mine else backlog_rows(area)
    return _plan_list(rows, everything, area, picking, mine)


def backlog_rows(area=None):
    """Tasks that have not started: the Backlog, now a collapsed stage under Production's Tasks."""
    board = on_board()
    # A TASK WITH A BUILD ENROLLED HAS LEFT THE BACKLOG (Jas, 2026-09-26: "If Gemma is working
    # on them, why are they in the backlog?"). It is on the Status board as briefed/building.
    try:
        import sys as _s
        if str(WORKSPACE / "scripts") not in _s.path:
            _s.path.insert(0, str(WORKSPACE / "scripts"))
        import manager_next as _mn
        building = _mn.in_production()
    except Exception as e:                                          # noqa: BLE001
        print(f"  NOTE: enrolled builds unreadable: {e}", file=sys.stderr)
        building = set()
    # PRODUCTION ONLY (Jas, 2026-10-03): a task assigned to anyone else is in their Review
    # section, not the backlog -- so the backlog is what is really queued.
    return [r for r in ts.ordered(area) if ts.in_production(r[0]) and r[0].get("id") not in building and (
            r[0].get("id") not in board or r[0].get("status", "queued") == "queued")]


def backlog_card(n, meta, marks, area=None, picking=None):
    """One backlog row: rank, title, chips, and the move controls."""
    tid = meta["id"]
    # WHERE THE MANAGER REALLY IS (Jas, 2026-09-25). Rank 1 used to be flagged NEXT, but the manager
    # skips a task it worked in the last 24h, one with a build enrolled, or one held for
    # him -- so NEXT often named a task nothing was about to touch.
    mk = marks.get(tid)
    first = bool(mk) and mk[0] in ("now", "next")
    cls = "card next" if first else "card"
    flag = f' <span class="{"nextflag" if first else "q4rest"}">{mk[1]}</span>' if mk else ""

    if picking and picking != tid:
        # The label says which way it goes. It used to read "Put here" under a DOWN
        # arrow while inserting the task ABOVE the row -- the control was telling the
        # user the opposite of what it did. Reported by Jas, 2026-08-21 (t039).
        acts = move_form(picking, "before", "&#8679; Put above this", target=tid,
                         title="Move the picked task in front of this one")
    elif picking == tid:
        acts = '<span class="small">&#8635; this is the one you are moving</span>'
    else:
        acts = (move_form(tid, "top", "&#8673;", title="Send to the top")
                + move_form(tid, "up", "&#9650;", title="Up one")
                + move_form(tid, "down", "&#9660;", title="Down one")
                + f'<a class="b sm" href="/plan?before={esc(tid)}'
                  f'{"&area=" + urllib.parse.quote(area) if area else ""}">Move before&hellip;</a>')

    return f"""<div class="{cls}"><div class="row">
  <div class="rank">{n}</div>
  <div class="grow">
    <div class="tt"><a href="/plan/{esc(tid)}">{esc(meta["title"])}</a>{flag}</div>
    <div class="meta"><span class="area">{esc(meta.get("area", ""))}</span>
      &nbsp; {chip(meta["status"])}{waiting_chip(meta, ts.find(tid)[1] or "")} &nbsp;
      <span class="small">{esc(meta.get("kind", ""))} &middot; {esc(tid)}</span></div>
  </div></div>
  <div class="acts">{acts}</div></div>"""


def _plan_list(rows, everything, area, picking, mine):
    """The filtered and move-before views of the backlog; the plain view is on Production."""
    waiting = sum(1 for m, _ in everything if ts.tier_of(m.get("needs")) == "vision")
    areas = sorted({m.get("area", "unfiled") for m, _ in everything})
    area_counts = {a: sum(1 for m, _ in everything if m.get("area", "unfiled") == a) for a in areas}

    # t068: the add box goes FIRST. He reaches for this to capture an idea far more often
    # than he opens it to read the list, and an idea that needs scrolling to record is an
    # idea that sometimes does not get recorded.
    out_top = []                 # intake lives on the Apps tab (Jas, 2026-10-01)
    filt = ['<nav class="pills">']
    filt.append(f'<a class="{"on" if not area and not mine else ""}" href="/plan">Everything ({len(everything)})</a>')
    if waiting:
        filt.append(f'<a class="{"on" if mine else ""}" href="/plan?mine=1">'
                    f'&#9873; Waiting on you ({waiting})</a>')
    filt.append('<a href="/plan/done">&#10003; Finished</a>')
    for a in areas:
        on = "on" if a == area and not mine else ""
        label = f"{esc(a)} ({area_counts[a]})"
        filt.append(f'<a class="{on}" href="/plan?area={urllib.parse.quote(a)}">{label}</a>')
    filt.append("</nav>")
    out = out_top + ["".join(filt)]

    if mine:
        out.append('<p class="small">These cannot move until you answer. Open one and leave '
                   'a note &mdash; that hands it straight back to me.</p>')

    if picking:
        meta, _ = ts.find(picking)
        if meta:
            out.append(
                f'<div class="picking"><b>Moving:</b> {esc(meta["title"])}<br>'
                f'<span class="small">Tap <b>Put here</b> on the task it should go in front '
                f'of.</span><div class="acts">'
                f'{move_form(picking, "top", "Send to the top")}'
                f'<a class="b sm ghost" href="/plan{"?area=" + urllib.parse.quote(area) if area else ""}">'
                f'Cancel</a></div></div>')

    if not rows:
        out.append('<div class="empty">Nothing in the queue.<br>'
                   '<span class="small">Add the first thing below.</span></div>')

    marks = q4_marks()
    for n, (meta, _) in enumerate(rows, 1):
        out.append(backlog_card(n, meta, marks, area, picking))

    return "".join(out)


def with_err(where, err):
    """`where` with ?err=... added BEFORE any #anchor. After it, the browser reads the error as part of
    the anchor and the banner never shows (found 2026-09-30: a refused skill attach said nothing)."""
    path, _, anchor = where.partition("#")
    sep = "&" if "?" in path else "?"
    return f"{path}{sep}err={urllib.parse.quote(err)}" + (f"#{anchor}" if anchor else "")


def dossier_card(tid):
    """The task's dossier (plan 2a, scripts/dossier.py): every artifact the pipeline has for it, where it
    lives, when, and one line -- derived from where each already lives, never a copy."""
    try:
        sys.path.insert(0, str(WORKSPACE / "scripts"))
        import dossier
        d = dossier.gather(tid)
    except Exception as e:                                          # noqa: BLE001 -- said, not swallowed
        return f'<div class="bad small">Dossier unreadable: {esc(f"{type(e).__name__}: {e}")}</div>'
    rows = "".join(f'<li><b>{esc(a["name"])}</b> <span class="small">{esc(a["at"] or "")}</span><br>'
                   f'<span class="small">{esc(a["detail"])}</span> <code class="small">{esc(a["path"] or "-")}</code></li>'
                   for a in d["artifacts"])
    bad = "".join(f'<li class="small" style="color:var(--hot)">unreadable: {esc(b)}</li>' for b in d["unreadable"])
    return (f'<details class="card"><summary><b>Dossier</b> <span class="small">({len(d["artifacts"])} artifact(s): '
            'everything the pipeline has produced for this task)</span></summary>'
            f'<ul style="margin:.3rem 0 0 1rem;padding:0">{rows}{bad}</ul></details>')


def _saved_banner(saved):
    """t128 (Jas): "When I save an idea, can it give the T number?" -- the banner names the task it
    became, linked, so a note can be added to it straight away. A bare/old ?saved=1 still thanks."""
    if re.fullmatch(r"t\d+", saved or ""):
        return (f'<div class="ok">Saved as <a href="/plan/{esc(saved)}"><b>{esc(saved)}</b></a>. '
                f'Thank you.</div>')
    return '<div class="ok">Saved. Thank you.</div>'


def plan_detail(tid):
    meta, body = ts.find(tid)
    if not meta:
        return None
    # HOLD reflects the CURRENT state, so the box is ticked if the task is already held and
    # unticking it releases the hold. A checkbox that always renders empty would silently
    # re-hold on every note, or worse, look like it does nothing.

    # Split the body so notes and attachments render as themselves rather than as text.
    desc, notes, atts = body, "", ""
    if "## Notes" in desc:
        desc, _, rest = desc.partition("## Notes")
        notes, _, atts = rest.partition("## Attachments")
    note_html = []
    for block in re.split(r"\n(?=\*\*\d{4}-)", notes.strip()):
        if block.strip():
            # t126 (Jas): "more space between notes ... help identify when you and I switch". Spaced
            # wider in the CSS, and a note whose header names Jas gets its own colour.
            who = re.match(r"\*\*\d{4}-\d\d-\d\d · ([^*]+)\*\*", block.strip())
            mine = bool(who) and "jas" in who.group(1).lower()
            note_html.append(f'<div class="note{" jas" if mine else ""}">'
                             f'{linkify_paths(md_to_html(block.strip()))}</div>')
    # A line is "name" or "name  (was original.zip, 12,345 bytes)". The filename is the
    # first token; the rest is provenance for a human.
    att_lines = []
    for l in atts.splitlines():
        if l.strip().startswith("- "):
            raw = l.strip()[2:].strip()
            att_lines.append((raw.split("  ", 1)[0].strip(), raw))

    src = meta.get("source", "").strip()
    src_html = (f'<div class="meta">From <code>{esc(src)}</code></div>' if src else "")
    nxt = STATUS_NEXT.get(meta["status"], "doing")

    return f"""<p><a href="/plan">&larr; the whole queue</a></p>
<div class="card">
  <h2 style="margin-top:.2rem">{esc(meta["title"])}</h2>
  <div class="meta"><span class="area">{esc(meta.get("area", ""))}</span>
    &nbsp; {chip(meta["status"])}{waiting_chip(meta, body)} &nbsp;
    <span class="small">{esc(meta.get("kind", ""))} &middot; {esc(tid)}</span></div>
  {src_html}
  <div class="meta"><a href="/plan/{esc(tid)}/sessions">Model sessions</a></div>
  <div style="margin-top:.6rem">{md_to_html(desc.strip()) if desc.strip() else
      '<p class="small">No description yet.</p>'}</div>
  <div class="acts">
    <form class="inline" method="POST" action="/plan/status">
      <input type="hidden" name="id" value="{esc(tid)}">
      <input type="hidden" name="status" value="{nxt}">
      <button class="b sm">Mark {nxt}</button></form>
    <form class="inline" method="POST" action="/plan/status">
      <input type="hidden" name="id" value="{esc(tid)}">
      <input type="hidden" name="status" value="done">
      <button class="b sm hot">Done</button></form>
    <form class="inline" method="POST" action="/plan/cancel"
          onsubmit="return confirm('Cancel this task? It leaves the queue and is NOT counted as done.')">
      <input type="hidden" name="id" value="{esc(tid)}">
      <input name="why" required placeholder="why it is being dropped" class="whyin">
      <button class="b sm ghost">Cancel task</button></form>
    {move_form(tid, "top", "&#8673; Top")}
    {wake_form(tid, meta)}
  </div>
</div>

{dossier_card(tid)}

<h2>Notes</h2>
<p class="small">{"Assigned to you. A note on its own keeps it with you; to send it on, choose who "
  "picks it up next in Assign to: below (nobody = back to the Manager's queue) before you add the note."
  if ts.tier_of(meta.get("needs")) == "vision"
  else "Yours and mine, in one thread. Nothing overwrites anything."}</p>
{"".join(note_html) or '<p class="small">No notes yet.</p>'}

<form method="POST" action="/plan/note" enctype="multipart/form-data">
  <input type="hidden" name="id" value="{esc(tid)}">
  <textarea name="text" required placeholder="Add a note for Claude Code..."></textarea>
  <label class="f">Attach anything &mdash; as many as you like</label>
  <input type="file" name="shots" multiple>
  <label class="f">{hold_select(meta.get("needs"))}
    &mdash; who picks this up next</label>
  <div class="actionbar">
    <button class="wide" type="submit">Add note</button>
    <a class="cancel" href="/plan">Cancel</a>
  </div>
</form>

<h2>Attachments</h2>
{"".join(
    f'<div class="card">' + (
      f'<a href="/att/{esc(name)}" target="_blank">'
      f'<img class="shot" src="/att/{esc(name)}" alt="{esc(name)}"></a>'
      if Path(name).suffix.lower() in IMAGE_EXT else
      f'<a class="b" href="/att/{esc(name)}" download>&#11015; Download</a>')
    + f'<div class="acts"><span class="small grow">{esc(label)}</span>'
    f'<form class="inline" method="POST" action="/plan/unattach">'
    f'<input type="hidden" name="id" value="{esc(tid)}">'
    f'<input type="hidden" name="file" value="{esc(name)}">'
    f'<button class="b sm ghost">Delete</button></form></div></div>'
    for name, label in att_lines) or '<p class="small">None yet.</p>'}

<p class="small">Add more with a note above. Full size, kept outside git so Delete really
deletes.</p>"""


# --------------------------------------------------------------------------- server

class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="text/html; charset=utf-8"):
        b = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def _redirect(self, where):
        self.send_response(303)
        self.send_header("Location", where)
        self.end_headers()

    def _form(self):
        """Returns (fields, files). Files are already on disk, not in memory.

        Uploads spool to a temp file in CHUNK pieces, so a large send costs bounded memory --
        which matters here because GB10 shares one memory pool with the models.
        """
        n = int(self.headers.get("Content-Length") or 0)
        ctype = self.headers.get("Content-Type", "")

        if not ctype.startswith("multipart/form-data"):
            raw = b""
            while len(raw) < n:
                chunk = self.rfile.read(min(CHUNK, n - len(raw)))
                if not chunk:
                    break
                raw += chunk
            return urllib.parse.parse_qs(raw.decode("utf-8", "replace")), []

        free = shutil.disk_usage(ts.ATTACH.parent if ts.ATTACH.parent.exists()
                                 else Path.home()).free
        if n + MIN_FREE_BYTES > free:
            return None, None                    # the only size refusal left, and it is real

        ts.ATTACH.mkdir(parents=True, exist_ok=True)
        spool = tempfile.NamedTemporaryFile(dir=ts.ATTACH, prefix=".spool-", delete=False)
        try:
            left = n
            while left > 0:
                block = self.rfile.read(min(CHUNK, left))
                if not block:
                    break
                spool.write(block)
                left -= len(block)
            spool.flush()
            spool.seek(0)
            counter = [0]

            def name_for(client_name):
                counter[0] += 1
                return f".part-{os.getpid()}-{counter[0]:02d}{safe_ext(client_name)}"

            return parse_multipart_spooled(spool, ctype, ts.ATTACH, name_for)
        finally:
            spool.close()
            os.unlink(spool.name)

    # ---- Apps ------------------------------------------------------------
    def _apps(self, app=None, banner="", new_idea=False, showall=False):
        apps = sorted(apps_with_releases(), key=display_name)
        if not apps and not new_idea:
            return self._send(200, shell("Apps", "<p>No apps have release notes yet.</p>"))
        # Honour the requested app even without release notes, but confine to PROJECTS.
        cand = None
        if app:
            p = (PROJECTS / app).resolve()
            if p.is_dir() and p.parent == PROJECTS.resolve():
                cand = app
        app = cand if cand else (apps[0] if apps else "")
        # t112 (Jas): "add the number in brackets to all the filters in this tool" -- the Backlog's
        # filters had counts; these app pills did not. The count is the app's open tasks.
        try:
            from collections import Counter
            open_by_area = Counter(m.get("area", "unfiled") for m, _ in ts.ordered())
        except Exception as e:                                      # noqa: BLE001 -- said, not swallowed
            print(f"  NOTE: task counts unreadable for the app pills: {e}", file=sys.stderr)
            open_by_area = None
        pills = "".join(
            f'<a class="{"on" if a == app and not new_idea else ""}" '
            f'href="/?app={urllib.parse.quote(a)}">{html.escape(display_name(a))}'
            f'{f" ({open_by_area.get(a, 0)})" if open_by_area is not None else ""}</a>'
            for a in apps)
        # F5: an idea that belongs to no app still has to land somewhere.
        pills += (f'<a class="{"on" if new_idea else ""}" href="/?new=1">'
                  f'&#128161; New idea</a>')

        md_to_html.n = 0
        if new_idea:
            version, notes = "", """<div class="card">
  <h2 style="margin-top:.2rem">&#128161; A new idea</h2>
  <p class="small">For something that is not about an app that exists yet &mdash; a new app,
  a change to the workspace, anything. It goes straight into the plan and I file it.</p>
</div>"""
            app = "new-idea"
        else:
            rels = releases_for(app)
            version = ""
            if rels:
                meta, body_md = parse_notes(rels[0][1])
                version = meta.get("version", str(rels[0][0]))
                done, _ = (set(), None) if showall else done_items(app, version)
                md_to_html.hidden = 0
                body = md_to_html(body_md, checklist=True, hide=done)
                # t205 (Jas): "add a release date to the release notes" -- from the notes' own
                # front matter (date:), which every release file carries; said when one does not.
                body = (f'<div class="meta">Released <b>{esc(meta.get("date") or "(date not recorded)")}</b>'
                        f' &middot; version {esc(version)}</div>' + body)
                if md_to_html.hidden:
                    body += (f'<p class="small">{md_to_html.hidden} item(s) already reported '
                             f'for {esc(version)} and hidden. '
                             f'<a href="/?app={urllib.parse.quote(app)}&showall=1">'
                             f'Show the full list</a></p>')
                if len(rels) > 1:
                    older = " ".join(f"<code>{c}</code> <span class='small'>"
                                     f"{esc(parse_notes(f)[0].get('date') or '')}</span>"
                                     for c, f in rels[1:])
                    body += f"<p class='small'>Earlier releases: {older}</p>"
            else:
                body = "<p>No release notes for this app yet.</p>"
            notes = f'<div class="card">{body}</div>'
            if str(WORKSPACE / "scripts") not in sys.path:      # t130: the app's timeline
                sys.path.insert(0, str(WORKSPACE / "scripts"))
            import app_timeline
            tasks = ([m for m, _ in ts.ordered()]
                     + [m for m, _ in ts.load_all(include_done=True).values() if m.get("status") == "done"])
            releases = [(parse_notes(f)[0].get("date") or "", parse_notes(f)[0].get("version") or str(c))
                        for c, f in releases_for(app)]
            notes += ('<div class="card"><h3>Timeline</h3>'
                      + app_timeline.timeline_html(app, tasks, releases) + '</div>')

        ticks = md_to_html.n
        tick_note = ('<div class="small">Whatever you mark above travels with this note. '
                     'You do not have to describe it in words.</div>'
                     if ticks else "")

        # ONE form around the notes and the message. The checklist used to render OUTSIDE
        # the form with no name attribute, so ticking it posted nothing at all and a reload
        # lost it -- it was decoration for months. Reported by Jas, 2026-08-21.
        page = f"""<nav class="pills">{pills}</nav>
<form method="POST" action="/say" enctype="multipart/form-data">
  <input type="hidden" name="app" value="{esc(app)}">
  <input type="hidden" name="version" value="{esc(version)}">
  <input type="hidden" name="ticks" value="{ticks}">
  {notes}
  <h2>Tell Claude Code</h2>
  {tick_note}
  <label class="f">Subject</label>
  <input type="text" name="subject" maxlength="{TITLE_MAX}"{"" if ticks else " required"}
         placeholder="One line: what this is about">
  <label class="f">What happened{" &mdash; optional if you ticked anything above" if ticks else ""}</label>
  <textarea name="text"{"" if ticks else " required"}
            placeholder="Test result, bug, or an idea for the next version..."></textarea>
  <label class="f">Attach anything &mdash; screenshots, exports, logs, several at once</label>
  <input type="file" name="shots" multiple>
  <label class="f">{hold_select("")}
    &mdash; e.g. Jas to look at it before anything starts</label>
  <div class="actionbar">
    <button class="wide" type="submit">Send</button>
    <a class="cancel" href="/">Cancel</a>
  </div>
</form>"""
        self._send(200, shell("Apps", page, active="/", banner=banner))

    # ---- routing ---------------------------------------------------------
    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        path = u.path

        # ttyd and Open WebUI are third-party: there is no header to add one to, so they are
        # framed under ours instead. Both were checked for X-Frame-Options and CSP
        # frame-ancestors on 2026-09-05 and set neither, which is the only reason this works.
        if path == "/terminal/last":                        # t619: the latest command and its output
            import subprocess as _sp
            r = _sp.run(["tmux", "capture-pane", "-p", "-J", "-t", TMUX_SESSION, "-S", "-2000"],
                        capture_output=True, text=True, timeout=10)
            if r.returncode:
                return self._send(500, f"tmux: {r.stderr.strip()}", "text/plain")
            return self._send(200, last_output(r.stdout), "text/plain")
        if path == "/terminal":
            return self._send(200, terminal_page())
        if path == "/webui":                 # Open WebUI uninstalled 2026-10-01
            return self._redirect("/")

        if path in ("/", "/index.html"):
            # t086: after sending, stay where he was. A New idea posts app=new-idea, which
            # is not a real app, so _apps fell back to apps[0] -- Good Dictator, exactly what
            # he described. It looked like "it always goes to the first app" because for a
            # real app it never happened, so it seemed random.
            back = (q.get("app") or [None])[0]
            return self._apps(
                app=back, new_idea=("new" in q or back == "new-idea"),
                showall="showall" in q,
                banner=_saved_banner((q.get("saved") or [""])[0]) if "saved" in q else "")

        if path.startswith("/models/api/"):
            return self._chat_proxy("GET", path, u.query, None)

        if path == "/scheduler":                    # B1 (2026-10-30): the scheduler's settings, read from the code
            import pages_ops
            return self._send(200, shell("Scheduler", pages_ops.scheduler_page(), active="/scheduler"))
        if path == "/logs":                         # B3 (2026-10-30): every journal and run log
            import pages_ops
            return self._send(200, shell("Logs", pages_ops.logs_page((q.get("src") or [""])[0],
                                                                     (q.get("n") or [""])[0] or None),
                                         active="/logs"))
        if path == "/models/throughput.json":       # B2 (2026-10-30): the engine's counter, sampled on request
            import pages_ops
            return self._send(200, pages_ops.throughput_json(), ctype="application/json")
        if path == "/models/throughput":
            import pages_ops
            import roles_pages as rp
            return self._send(200, shell("Models", rp.pills("toks") + pages_ops.throughput_page(), active="/models"))
        if path == "/models" or path.startswith("/models/"):
            import roles_pages as rp
            parts = [urllib.parse.unquote(x) for x in path.strip("/").split("/")]
            err = (q.get("err") or [""])[0]
            try:
                if parts == ["models", "chat"]:
                    import chat_page
                    body = rp.pills("chat") + chat_page.chat_body()
                    return self._send(200, shell("Models", body, active="/models"))
                if parts == ["models", "changelog"]:
                    return self._send(200, shell("Models", rp.changelog_page(), active="/models"))
                if parts == ["models"]:
                    ww = _who()
                    body = rp.index_page(ww.snapshot() if ww else None)
                elif parts == ["models", "new"]:
                    body = rp.new_page(err)
                elif len(parts) == 2:
                    body = rp.role_page(parts[1], err)
                elif parts[2] == "model":
                    body = rp.model_page(parts[1])
                elif parts[2] == "tools":
                    body = rp.tools_page(parts[1])
                elif parts[2] == "attributes":
                    body = rp.attributes_page(parts[1])
                elif parts[2] == "tasks":
                    body = rp.tasks_page(parts[1], (q.get("moving") or [None])[0])
                elif parts[2] == "skills":
                    body = rp.skills_page(parts[1])
                elif parts[2] == "escalations":
                    body = rp.escalations_page(parts[1])
                else:
                    return self._send(404, shell("Not found", '<div class="empty">No such page.</div>', active="/models"))
            except KeyError as e:
                body = f'<div class="bad">No such role: {esc(str(e))}. <a href="/models">Back to Roles</a></div>'
            return self._send(200, shell("Models", body, active="/models"))

        if path.startswith("/flow/skill/"):
            # ONE SKILL, READ-ONLY (plan 3c-i): the card's "Read it". Only a name from docs/skills/.
            import flow_pages
            name = urllib.parse.unquote(path[len("/flow/skill/"):])
            body = flow_pages.skill_page(name)
            return self._send(200 if body else 404, shell("Flow", body or '<div class="empty">No such skill.</div>',
                                                          active="/flow"))
        if path.startswith("/flow/task/"):
            import flow_pages
            body = flow_pages.task_page(path.rsplit("/", 1)[1], _last_tick())
            if body is None:
                return self._send(404, shell("Not found", '<div class="empty">No such task on the chart.</div>'))
            return self._send(200, shell("Flow", body, active="/flow"))
        if path == "/help":
            q = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            return self._send(200, shell("How this page works",
                                         help_page((q.get("p") or [""])[0]),
                                         active="/help"))
        if path == "/flow":
            import flow_pages
            return self._send(200, shell("Flow", flow_page(), active="/flow", banner=flow_pages.banner(q)))

        if path == "/sw.js":
            # SERVED FROM THE ROOT ON PURPOSE. A service worker's scope is its own directory,
            # so one delivered from /static/ could not control /status and the subscription
            # would register against a scope that never sees a push.
            body = SERVICE_WORKER.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/javascript; charset=utf-8")
            self.send_header("Service-Worker-Allowed", "/")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            return self.wfile.write(body)

        if path == "/status/vapid":
            import subprocess as _sp
            r = _sp.run([sys.executable, str(WORKSPACE / "scripts" / "notify.py"),
                         "--public-key"], capture_output=True, text=True, timeout=30,
                        stdin=_sp.DEVNULL, cwd=str(WORKSPACE))
            body = (r.stdout or "").strip().encode()
            self.send_response(200 if r.returncode == 0 else 500)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            return self.wfile.write(body)

        if path == "/status":
            return self._send(200, shell("Production", board_page(), active="/status"))

        if path == "/status/full":
            return self._send(200, shell("Machine status", status_page(), active="/status"))

        if path == "/file":
            code, body = file_page((q.get("path") or [""])[0], raw=bool(q.get("raw")))
            return self._send(code, shell("File", body, active="/plan"))

        if path == "/lanes":
            return self._send(200, shell("Lanes", lanes_page(), active="/lanes"))

        if path == "/chat":
            import pages_chat
            return self._send(200, shell("Chat", pages_chat.chat_page(), active="/chat"))

        if path == "/misc":
            return self._send(200, shell("Misc production", collapsed(misc_page()), active="/misc"))

        if path == "/scripts":
            return self._send(200, shell("Scripts", collapsed(scripts_page((q.get("run") or [""])[0])),
                                         active="/scripts"))

        if path == "/devices":
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            return self._send(200, shell("Devices", devices_page(
                saved=(q.get("saved") or [""])[0] == "1",
                problems=(q.get("err") or [""])[0].split("\n") if q.get("err") else ()),
                active="/devices"))

        if path == "/network":                      # the map: devices, links, services (2026-10-26)
            import pages_network
            return self._send(200, shell("Network", pages_network.network_page(), active="/network"))

        if path == "/corpus":
            return self._send(200, shell("Corpus", corpus_page((q.get("section") or ["models"])[0],
                                                             (q.get("run") or [""])[0]), active="/corpus"))

        if path == "/plan/done":                            # t155: what got finished, and by whom
            if str(WORKSPACE / "scripts") not in sys.path:
                sys.path.insert(0, str(WORKSPACE / "scripts"))
            import finished_tasks
            verdicts, skipped = [], 0
            vp = WORKSPACE / "workspace" / "review-verdicts.jsonl"
            for line in (vp.read_text(encoding="utf-8", errors="replace").splitlines() if vp.is_file() else []):
                if not line.strip():
                    continue
                try:
                    verdicts.append(json.loads(line))
                except ValueError:
                    skipped += 1
            tasks = [m for m, _ in ts.load_all(include_done=True).values()]
            note = (f'<div class="bad">{skipped} line(s) of review-verdicts.jsonl did not parse and '
                    f'are not counted.</div>' if skipped else "")
            return self._send(200, shell("Finished", note + finished_tasks.render(
                finished_tasks.finished(tasks, verdicts)), active="/plan"))

        if path == "/plan" and not q.keys() - {"ok"}:      # the Backlog lives on Production now
            return self._redirect("/status")
        if path == "/plan":
            area = (q.get("area") or [None])[0]
            picking = (q.get("before") or [None])[0]
            mine = "mine" in q
            banner = '<div class="ok">Done.</div>' if "ok" in q else ""
            return self._send(200, shell("Backlog", plan_list(area, picking, mine),
                                         active="/plan", banner=banner))

        if path.startswith("/plan/t") and path.endswith("/sessions"):   # t401
            tid = path.split("/")[2]
            if str(WORKSPACE / "scripts") not in sys.path:
                sys.path.insert(0, str(WORKSPACE / "scripts"))
            import task_sessions
            return self._send(200, shell("Model sessions",
                f'<p><a href="/plan/{html.escape(tid)}">&larr; {html.escape(tid)}</a></p>'
                + task_sessions.render_html(tid, task_sessions.summarize(tid)), active="/plan"))

        if path.startswith("/plan/t"):
            tid = path.rsplit("/", 1)[1]
            body = plan_detail(tid)
            if body is None:
                return self._send(404, shell("Not found",
                                             '<div class="empty">No such task.</div>'))
            return self._send(200, shell("Task", body, active="/plan"))

        if path.startswith("/att/"):
            # basename only, and the resolved path must still be inside the folder --
            # ../ in a URL is the oldest trick there is.
            name = os.path.basename(urllib.parse.unquote(path[5:]))
            f = ts.ATTACH / name
            try:
                inside = f.resolve().parent == ts.ATTACH.resolve()
            except OSError:
                inside = False
            if not inside or not f.is_file():
                return self._send(404, shell("Not here", '<div class="empty">Gone.</div>'))
            data = f.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", IMAGE_EXT.get(f.suffix.lower(),
                                                           "application/octet-stream"))
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            return self.wfile.write(data)

        if path == "/review":
            # Jas, t042: "my workflow will start on the Review tab before going to the Plan
            # tab." So the things waiting on him LEAD this page, and the not-yet-built
            # transcript queue sits under them rather than instead of them.
            # ONE SECTION PER ASSIGNEE (Jas, 2026-10-03): "if something is assigned to anything, it
            # goes to the review tab ... a section for assigned to me, you can have one for you, and
            # then all the local models can have one for themselves." Production is the queue, not
            # an assignee, so it is not here. His section first, then Claude's, then each role.
            names = dict(_assignable())
            # GROUPED BY TIER, IN THE ORDER JAS AND CLAUDE SETTLED (t9540): Vision, Technical Guidance,
            # Rework. A task appears ONCE however many roles hold its tier -- one row per item is Claude's
            # decision 2, and it is also the only way a two-holder tier does not double the queue on screen.
            # The old headings named a ROLE ("Assigned to Manager"), which is the vocabulary this change
            # removes: a task waits on a KIND of decision, and the role that holds it can change.
            groups = {}
            role_keys = set(names)
            # ONE TECHNICAL LIST (t9564, DS's measurement, 2026-10-29). The pipeline's own waits -- delivered branches,
            # held briefs, Done?, Blocked -- were a SECOND list beside "Needs Technical Guidance", built from pipeline
            # artefacts rather than needs:, so 6 tasks showed twice (5 on the legacy 'claude', 1 on 'manager') and the
            # reader saw two rival counts. Now a task the pipeline is waiting on appears ONCE, in the Technical list,
            # as its pipeline card (which says what stage it left); its needs: is not rewritten, because a branch
            # waiting for review is a stage, not a hand-off, and writing "Reassigned" would move the escalation KPI.
            # One card per task here too: a task can wait in two pipeline places at once (a delivered branch AND a
            # held brief), and the first card already names a stage it left.
            wc, pipe_ids = [], set()
            for _p, _st, _c in claude_waiting():
                _m = re.search(r'href="/plan/(t\d+)"', _c)
                if _m and _m.group(1) in pipe_ids:
                    continue
                if _m:
                    pipe_ids.add(_m.group(1))
                wc.append((_p, _st, _c))
            for m, _ in ts.ordered():
                if not ts.in_production(m) and m.get("id") not in pipe_ids:
                    groups.setdefault(tier_of(m.get("needs"), role_keys), []).append(m)
            # MERGE REVIEW IS NOT AN ESCALATION (Jas, 2026-10-29: "what we need is clarity for me on what is ready to
            # merge but needs frontier review. That is how this should be labelled. 'Merge Review.'"). A delivered
            # branch that auto_merge refused is a build waiting for a frontier reviewer's read -- not a Technical
            # decision -- so it gets its own section; Done?, Blocked and held briefs stay in Technical.
            merge_items = [x for x in wc if x[1] == "building"]
            wc = [x for x in wc if x[1] != "building"]
            if wc:
                groups.setdefault("technical", [])
            order = [t for t in TIER_ORDER if groups.get(t) or (t == "technical" and wc)]
            order += [t for t in groups if t not in TIER_ORDER and t != "production"]

            def _card(m):
                return f"""<div class="card">
  <div class="tt"><a href="/plan/{esc(m['id'])}">{esc(m['title'])}</a></div>
  <div class="meta"><span class="area">{esc(m.get('area', ''))}</span>
    &nbsp; {chip(m['status'])}{waiting_chip(m, ts.find(m['id'])[1] or '')} &nbsp;
    <span class="small">{esc(m.get('kind', ''))} &middot; {esc(m['id'])}</span></div>
  <form class="inline" method="POST" action="/plan/approve" style="margin-top:.4rem">
    <input type="hidden" name="id" value="{esc(m['id'])}">
    <button class="b sm" title="approve: noted, and set back to Production">&#10003; Approve &rarr; Production</button></form>
</div>"""
            sections = []
            for k in order:
                if groups.get(k) or (k == "technical" and wc):
                    label = TIER_TITLE.get(k, "Needs " + str(k).replace("_", " ").title())
                    extra = wc if k == "technical" else []
                    held = f' &middot; held by {technical_holders()}' if k == "technical" else ""
                    sections.append(f'<h2>{esc(label)} ({len(groups[k]) + len(extra)}){held}</h2>'
                                    + "".join(c for _p, _st, c in extra)
                                    + "".join(_card(m) for m in groups[k]))
            if merge_items:
                sections.append(f'<h2>Merge Review ({len(merge_items)}) &middot; held by {technical_holders()}</h2>'
                                '<p class="small">Built and delivered; auto-merge refused each one, so a frontier '
                                'reviewer reads it. A review that passes merges it at once (merge on pass).</p>'
                                + "".join(c for _p, _st, c in merge_items))
            head = ("".join(sections) if sections else
                    '<div class="card"><h2 style="margin-top:.2rem">Nothing is assigned to anyone</h2>'
                    '<p class="small">Everything open is in Production.</p></div>')
            # The transcript-approval section is gone (t053). It existed to gate
            # material for the auditor; the auditor is dead, and after the final export
            # there is no counterpart to diff a transcript against.
            #
            # t154. Review is a STATUS, not a topic, which is why it has its own tab rather
            # than a filter on the plan -- Jas: "if everything is in the plan list, it's just
            # going to add noise." Three subsections, all linking to the same task pages, so
            # a task appears here because of where it IS, not what it is about.
            # Review is what waits on a PERSON (Jas, 2026-09-26): Hold for Jas (the one list --
            # the board's "Needs you" was the same set), then Blocked and Done? for Claude.
            # The code-review sections moved to Production.
            # EVERYTHING WAITING ON A PERSON (Jas, 2026-09-29): Waiting on Claude moved here from
            # Production, from every pipeline, grouped by the pipeline and the stage it left.
            rest = ""
            rv = review_sections(parts=True)
            if isinstance(rv, dict):
                rest += rv["ready"] + rv["runs"]
            else:
                rest += rv
            return self._send(200, shell("Review", collapsed(head + rest), active="/review"))

        if path == "/review/runs":
            return self._send(200, shell("Agent runs", runs_page(), active="/status"))

        return self._send(404, shell("Not here", '<div class="empty">Not here.</div>'))

    def _chat_proxy(self, method, path, query, body):
        """Pass a chat API call through to chatui.py (the chat's backend), so the Hub's Chat page is
        same-origin. Any failure is said, not swallowed: the page shows the error the call returns."""
        import urllib.request
        import chat_page
        url = chat_page.BACKEND + path[len("/models"):] + (f"?{query}" if query else "")
        req = urllib.request.Request(url, data=body, method=method,
                                     headers={"Content-Type": self.headers.get("Content-Type", "application/json")})
        try:
            with urllib.request.urlopen(req, timeout=3600) as f:
                return self._send(f.status, f.read(), f.headers.get("Content-Type", "application/json"))
        except urllib.error.HTTPError as e:
            return self._send(e.code, e.read(), e.headers.get("Content-Type", "application/json"))
        except OSError as e:
            return self._send(502, json.dumps({"error": f"chat backend (chatui, :8092) unreachable: {e}"}),
                              "application/json")

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        if path.startswith("/models/api/"):              # raw JSON body: read before any form parsing
            n = int(self.headers.get("Content-Length") or 0)
            return self._chat_proxy("POST", path, urllib.parse.urlparse(self.path).query,
                                    self.rfile.read(n) if n else b"")
        form, files = self._form()
        if form is None:
            return self._send(507, shell("Not enough disk", """<div class="card">
  <h2 style="margin-top:.2rem">Not enough disk space</h2>
  <p>There is no size limit on uploads &mdash; this is the disk itself. The upload was
  refused rather than half-written, and nothing was stored.</p>
  <p class="small">Free some space on the GX10 and send it again.</p>
  <p><a href="/plan">Back to the plan</a></p></div>"""))
        shots = [f for f in files if f[0] == "shots"]

        def one(k, default=""):
            return (form.get(k) or [default])[0].strip()

        if path == "/scripts/set":
            # A BOX ON THE SCRIPTS TAB (Jas, 2026-10-30). One field set, read back, and the page shows what the FILE
            # says rather than what was typed.
            ok, msg, now = apply_knob(one("script"), one("name"), one("value"))
            back = "/scripts?run=" + urllib.parse.quote(one("script"))
            return self._redirect(back + ("&saved=" + urllib.parse.quote(msg) if ok
                                          else "&err=" + urllib.parse.quote(msg)))

        if path == "/devices/settings":
            probs = save_guard_levels(form)
            if probs:
                return self._redirect("/devices?err=" + urllib.parse.quote("\n".join(probs)))
            return self._redirect("/devices?saved=1")

        if path.startswith("/models/"):
            import roles_pages as rp
            where, err = rp.handle_post(path, one, form)
            # THE EDIT COMMITS ITSELF after a quiet minute (config_autocommit.py, Jas 2026-10-08).
            try:
                import config_autocommit
                config_autocommit.request()
            except Exception as e:                                  # noqa: BLE001 -- said; the save stands
                print(f"  NOTE: config autocommit not scheduled ({type(e).__name__}: {e})", flush=True)
            if err:
                back = path.rsplit("/", 1)[0] if path.count("/") > 2 else path
                return self._redirect(f"{back}?err={urllib.parse.quote(err)}")
            return self._redirect(where)

        if path.startswith("/flow/"):
            # FLOW CHANGE REQUESTS (plan step 3): a request to the Manager, or a Revert. Nothing
            # here edits FLOW.json directly -- flow_pages.py / scripts/flow_change.py.
            import flow_pages
            where, err = flow_pages.handle_post(path, one, form)
            return self._redirect(with_err(where, err) if err else where)

        if path == "/say":
            return self._say(one, shots)

        if path == "/terminal/send":
            text = (form.get("text") or [""])[0]
            if not text.strip():
                return self._send(400, "nothing to send", "text/plain")
            ok, err = tmux("-l", text)
            ok = ok and tmux("Enter")[0]
            return self._send(200 if ok else 500, "sent" if ok else f"tmux: {err}", "text/plain")
        if path == "/terminal/key":
            k = TERM_KEYS.get(one("key"))
            if not k:
                return self._send(400, "unknown key", "text/plain")
            ok, err = tmux(k)
            return self._send(200 if ok else 500, "ok" if ok else f"tmux: {err}", "text/plain")
        if path == "/terminal/cmd/add":
            cmd = (form.get("cmd") or [""])[0].strip()
            if cmd:
                term_cmds_save(term_cmds() + [{"label": one("label"), "cmd": cmd}])
            return self._send(200, "ok", "text/plain")
        if path == "/terminal/cmd/edit":                    # t316: edit one saved command in place
            cmds = term_cmds()
            i, cmd = int(one("i") or -1), (form.get("cmd") or [""])[0].strip()
            if 0 <= i < len(cmds) and cmd:
                cmds[i] = {"label": one("label"), "cmd": cmd}
                term_cmds_save(cmds)
            return self._send(200, "ok", "text/plain")
        if path == "/terminal/cmd/del":
            cmds = term_cmds()
            i = int(one("i") or -1)
            if 0 <= i < len(cmds):
                del cmds[i]
                term_cmds_save(cmds)
            return self._send(200, "ok", "text/plain")

        if path == "/status/subscribe":
            # The browser POSTs the PushSubscription verbatim. notify.py owns the store and
            # checks it against the CURRENT public key, so a subscription made against an old
            # keypair is refused here rather than failing silently at send time.
            import subprocess as _sp
            raw = one("sub")
            r = _sp.run([sys.executable, str(WORKSPACE / "scripts" / "notify.py"),
                         "--subscribe", raw], capture_output=True, text=True, timeout=30,
                        stdin=_sp.DEVNULL, cwd=str(WORKSPACE))
            ok = r.returncode == 0
            return self._redirect("/status?subscribed=1" if ok else "/status?suberr=1")

        if path in ("/status/device-pause", "/status/device-resume"):
            sys.path.insert(0, str(WORKSPACE / "scripts"))
            import scheduler_commands as sc
            dev = (one("device") or "").strip()
            back = one("back") if one("back") in ("/status", "/lanes") else "/status"
            if dev not in ("thinkpad",):                 # the GX10 has the master key; only listed devices
                return self._redirect(with_err(back, f"no pause button for device {dev!r}"))
            if path == "/status/device-pause":
                sc.pause_device(dev, "jas", "paused from the Hub")
            else:
                sc.resume_device(dev)
            return self._redirect(back)
        if path in ("/status/pause", "/status/resume", "/status/stop"):
            sys.path.insert(0, str(WORKSPACE / "scripts"))
            import scheduler_commands as sc
            # The button is the master breaker (Jas, 2026-10-06): it stops or fires production.target too.
            # GX10 ONLY since 2026-10-13 (Jas: "change the everything button to GX10 so I can manage them
            # separately ... I may want to disconnect the eGPU"). The ThinkPad has its own button.
            if path == "/status/pause":
                ok, why = sc.gx10_off("jas", "paused from the Hub")
            elif path == "/status/resume":
                ok, why = sc.gx10_on()
            else:
                ok, why = sc.stop_job(int(one("pid") or 0), who="jas")
            if not ok:
                return self._redirect(with_err("/status", why))
            return self._redirect("/status")
        if path == "/status/notify":
            # ONE EVENT PER POST, through notify.py rather than by writing the file here: it
            # owns the defaults and refuses an unknown event name, and two writers to one
            # preferences file is how they drift.
            import subprocess as _sp
            _sp.run([sys.executable, str(WORKSPACE / "scripts" / "notify.py"),
                     "--set", one("event"), one("on")],
                    capture_output=True, text=True, timeout=60, stdin=_sp.DEVNULL,
                    cwd=str(WORKSPACE))
            return self._redirect("/status")

        if path == "/plan/move":
            ts.move(one("id"), one("where"), one("target") or None)
            return self._redirect("/plan?ok=1")

        if path == "/plan/wake":                          # a deferral date, from the task screen (I2)
            tid, when = one("id"), (one("date") or "").strip()
            try:
                if when:
                    ts.set_wake(tid, when)
                else:
                    ts.clear_wake(tid)
            except ValueError as e:
                # A REFUSED DATE IS A MESSAGE, NOT A 500 AND NOT A SILENT REDIRECT. taskstore refuses
                # anything that is not a real YYYY-MM-DD: a stored "November" would never match
                # session_start's regex, and "never matches" looks exactly like "not due yet".
                return self._send(200, shell("Date refused",
                                             f'<div class="card bad">{esc(str(e))}</div>'
                                             f'<p class="small"><a href="/plan/{esc(tid)}">'
                                             f'back to the task</a></p>', active="/plan"))
            return self._redirect(f"/plan/{tid}?wake_ok=1")

        if path == "/plan/status":
            ts.set_status(one("id"), one("status"))
            tid = one("id")
            return self._redirect(f"/plan/{tid}" if one("status") != "done" else "/plan?ok=1")

        if path == "/plan/note":
            tid, text = one("id"), one("text")
            if text:
                ts.add_note(tid, text, who="jas", _from_form=True)
            if shots:
                save_uploads(shots, tid)
            # HOLD, and it is the SAME FIELD the Review tab reads. Jas, 2026-09-14: "anything
            # you need me to review should be on hold, same with anything I say revisit at a
            # later date ... All of those held items will end up in my review tab." That field
            # already exists and already drives Review -- adding a second "held" flag would
            # recreate the exact drift he had just caught me in, where the Status page counted
            # one thing and Review counted another.
            #
            # The box reflects state, so UNticking it releases the hold. Silently one-way would
            # make it impossible to un-hold from the surface that holds.
            ts.set_meta(tid, needs=hold_value(one("hold")))
            return self._redirect(f"/plan/{tid}")

        if path == "/plan/approve":
            # t164 (Jas): "an approve button on the items in my review queue". The same two writes
            # as answering on the task page: a note in his name, and the hold released -- so the
            # item leaves Review and goes back to whoever acts next.
            tid = one("id")
            if ts.find(tid)[0]:
                ts.add_note(tid, "Approved (Review tab).", who="jas", _from_form=True)
                ts.set_meta(tid, needs="")
            return self._redirect("/review")

        if path == "/plan/cancel":
            # CANCELLED, NOT DONE. done is the number the keep rate and the Pareto are computed
            # from; filing an abandoned idea as done would inflate all of them. The reason is
            # required by the form AND re-checked here, because a client can post anything.
            tid, why = one("id"), one("why").strip()
            if not why:
                return self._redirect(f"/plan/{tid}")
            ts.add_note(tid, f"CANCELLED: {why}", who="jas", _from_form=True)
            ts.set_status(tid, "cancelled")
            return self._redirect("/plan?ok=1")

        if path == "/plan/attach":
            tid = one("id")
            if shots:
                save_uploads(shots, tid)
            return self._redirect(f"/plan/{tid}")

        if path == "/plan/unattach":
            tid = one("id")
            ts.remove_attachment(tid, one("file"))
            return self._redirect(f"/plan/{tid}")

        if path == "/plan/add":
            # First line is the title, the rest is the body. He writes a paragraph; the shape
            # of the record is my problem, not his.
            title, text = one("title"), one("text")
            # Nothing typed is ever discarded. The old form took one blob, used the first
            # line as the title and truncated it to 80 characters -- and with no line break
            # there was no second line, so everything past 80 was destroyed silently. That
            # cost Jas 47 characters of a bug report on 2026-08-21 (t039). If a title still
            # arrives over the limit, the full text goes to the body rather than the bin.
            if title or text:
                short = title.strip()[:TITLE_MAX]
                body = text.strip()
                if len(title.strip()) > TITLE_MAX:
                    body = (title.strip() + ("\n\n" + body if body else "")).strip()
                if not short:
                    short = body.splitlines()[0][:TITLE_MAX] if body else "untitled"
                tid = ts.add(short, body=body, at_top=bool(one("top")),
                             source="added from the phone")
                needs = hold_value(one("hold"))
                if needs:
                    # "bigger picture things that need planning but I wanted to get an idea out
                    # of my head" -- held on arrival, so it lands in Review instead of reading
                    # as work anyone should start.
                    ts.set_meta(tid, needs=needs)
                if shots:
                    save_uploads(shots, tid)
                return self._redirect(f"/plan/{tid}")
            return self._redirect("/plan?ok=1")

        return self._send(404, shell("Not here", '<div class="empty">Not here.</div>'))

    def _say(self, one, shots=()):
        # SUBJECT (Jas, 2026-10-01: "All of those intake fields in the apps tab should have a
        # subject line"). It becomes the task's title; without one the first line still does.
        subject = re.sub(r"\s+", " ", one("subject")).strip()[:TITLE_MAX]
        text = one("text")
        # t091: ticking the checklist IS the message. Requiring prose on top of it meant
        # retyping "all tests passed" under a list that already said so.
        marked = any(one(f"chk{i}") in ("ok", "bad")
                     for i in range(1, int(one("ticks", "0") or 0) + 1))
        if not text and subject:
            text = subject
        if not text and not marked:
            return self._send(400, shell("Empty",
                '<div class="card"><p>Nothing to send &mdash; write something, or tick at '
                'least one checklist item.</p><p><a href="/">Back</a></p></div>'))
        if not text:
            text = "Test results only, no comment."
        app, version = one("app", "unknown"), one("version")
        # F6: the kind dropdown is gone -- it fed nothing and cost a tap. Kept in the front
        # matter so every note written before 2026-08-21 still parses.
        kind = "note"

        # F2: the checklist travels with the note. Previously the ticks posted nothing.
        results, keys_marked = [], []
        for i in range(1, int(one("ticks", "0") or 0) + 1):
            state = one(f"chk{i}")
            label = one(f"chktext{i}")
            if state in ("ok", "bad") and label:
                results.append(("PASS" if state == "ok" else "FAIL", label))
                if one(f"chkkey{i}"):
                    keys_marked.append(one(f"chkkey{i}"))
        if results:
            # t081: remember what has been reported so it stops reappearing.
            mark_done(app, version, keys_marked)
            passed = sum(1 for s, _ in results if s == "PASS")
            block = "\n".join(f"- [{'x' if s == 'PASS' else '!'}] {s}  {lbl}"
                               for s, lbl in results)
            text = (f"{text}\n\n## Test results ({passed}/{len(results)} passed)\n\n"
                    f"{block}\n")

        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%SZ")
        # The server names the file, never the client. Same rule as inbox_post: a caller that
        # chooses paths is a caller that can write anywhere (ROADMAP G1).
        slug = re.sub(r"[^a-z0-9]+", "-", text.lower())[:40].strip("-") or "note"
        safe_app = re.sub(r"[^a-zA-Z0-9._-]", "", app) or "unknown"
        FEEDBACK_DIR.mkdir(parents=True, exist_ok=True)
        path = FEEDBACK_DIR / f"{stamp}--{safe_app}--{slug}.md"
        # HOLD travels with the feedback note, so whoever triages it can see that Jas wanted
        # it parked rather than started. It is a FIELD, not a phrase in the prose -- the same
        # correction he made on 2026-09-14 when the Status page counted a string and Review
        # counted a field. scripts/triage.py turns these into tasks; a held note becomes a
        # task with needs: jas, which is what puts it in Review.
        who = hold_value(one("hold"))              # Assign to: (2026-09-29) -- the role, or ""
        held = "yes" if who == "jas" else "no"      # kept: "held" still means "for Jas"
        path.write_text(
            f"---\nfrom: phone\napp: {app}\nversion: {version}\nkind: {kind}\n"
            f"hold: {held}\nassigned: {who}\nreceived: {stamp}\n"
            + (f"subject: {subject}\n" if subject else "") + f"---\n\n{text}\n", encoding="utf-8")

        # A note that only becomes a file is a note that waits for someone to notice it.
        # Sixteen were waiting when this was written, so it becomes a task as well.
        tid = ts.add(subject or text.strip().splitlines()[0][:TITLE_MAX], safe_app,
                     body=text, source=f"workspace/app-feedback/{path.name}")
        if shots:
            names = save_uploads(shots, tid)
            # The feedback file is the record of what he sent; it should say so too.
            with path.open("a", encoding="utf-8") as fh:
                fh.write("\nScreenshots: " + ", ".join(names) + "\n")

        self._redirect(f"/?app={urllib.parse.quote(app)}&saved={tid}")   # t128: say which task

    def log_message(self, fmt, *args):
        sys.stderr.write(f"{self.address_string()} {fmt % args}\n")


if __name__ == "__main__":
    FEEDBACK_DIR.mkdir(parents=True, exist_ok=True)
    ts.TASKS.mkdir(parents=True, exist_ok=True)
    print(f"HiveMind Hub on http://{BIND}:{PORT}/", flush=True)
    # WARM THE LEDGER CACHE BEFORE THE FIRST CLICK (2026-10-03): the first Production/Lanes load
    # after a restart parsed 67 MB of dispatch-loop.jsonl -- over 2 min on a box at 85% RAM. A
    # background thread does it now; a page that arrives first just waits on the same lock.
    def _warm():
        try:
            if str(WORKSPACE / "scripts") not in sys.path:
                sys.path.insert(0, str(WORKSPACE / "scripts"))
            import jsonl_cache
            t0 = time.time()
            n = len(jsonl_cache.rows(WORKSPACE / "workspace" / "dispatch-loop.jsonl"))
            print(f"ledger cache warm: {n} rows in {time.time() - t0:.0f}s", flush=True)
            # THE PAGES TOO (2026-10-08): the ledger was only one of the files the first Lanes and
            # Production loads parse cold (17-43 s measured). Build each once, discarding the HTML.
            for name, fn in (("lanes", lanes_page), ("production", board_page)):
                t0 = time.time()
                fn()
                print(f"{name} warm in {time.time() - t0:.0f}s", flush=True)
        except Exception as e:                                      # noqa: BLE001 -- said, not swallowed
            print(f"ledger cache warm failed: {type(e).__name__}: {e}", flush=True)
    import threading
    threading.Thread(target=_warm, daemon=True).start()
    ThreadingHTTPServer((BIND, PORT), Handler).serve_forever()
