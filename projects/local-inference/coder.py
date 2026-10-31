#!/usr/bin/env python3
"""A local model does a task from the queue, in a sandbox, and only keeps it if the tests pass.

    python3 projects/local-inference/coder.py --task t143
    python3 projects/local-inference/coder.py --task t143 --apply   # write the branch
    python3 projects/local-inference/coder.py --brief "make X do Y" --files scripts/foo.py

THE TIER B BUILD AGENT (docs/AGENT-BOUNDARIES.md)
-------------------------------------------------
That document specified this before it existed: **build agent, Tier B, network none,
filesystem one project dir.** This is that, and it deliberately does not grow past it.

Trusted input only. A task in `workspace/tasks/` was written by Jas or by Code; a web page
was not, and this agent must never read one. `L-098` makes that a statement about decode
settings too -- reasoning stays ON, because with it off this model obeyed an instruction
embedded in a document in three tokens.

WHY THE VERIFICATION IS THE PRODUCT
-----------------------------------
The model is right about where a fault is far more often than it is right about what the
fix should be. Measured on this repository's own 25 injected bugs: **11/25 single-shot,
17/23 once the failing test output is fed back.** So the loop is not "ask a model for a
patch". It is:

    propose an anchored replace -> apply it in a throwaway copy -> run the tests
    -> show the model what failed -> let it try again -> keep the change ONLY if the
       suite passes AND no other suite broke

That last clause is not decoration. A patch that makes its own test pass by breaking another
is the most expensive kind of wrong, because it looks like progress.

WHAT IT CANNOT DO, ENFORCED IN CODE RATHER THAN IN THE PROMPT
--------------------------------------------------------------
  * Never writes the working tree. Everything happens in a copy under /tmp.
  * Never commits, never pushes, never touches `main`.
  * Never reads or writes `chat/` -- the personal corpus, no rollback (`D-22`).
  * Never reads `~/.secrets/` or anything matching a credential name.
  * Anchored replace only: `{"old", "new"}`, exactly one match or the op is refused. A model
    that cannot hit an anchor does not get to rewrite a file.
  * Output is a **branch, a diff and the test output**, for a human or a frontier session to
    review. Never a merge.

A prompt asking a model not to do these things is a request. A sandbox that cannot do them is
a property.
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
# The gateway by default; overridable so the agent can be pointed at a different local
# server without editing code. Ollama (`projects/nvidia-playbook`) serves an OpenAI-compatible
# API at 127.0.0.1:11434/v1, so the NVIDIA-playbook stack can drive this harness:
#
#     CODER_ENDPOINT=http://127.0.0.1:11434/v1/chat/completions coder.py --task tNNN --model qwen3.6
#
# That combination is the point of both efforts meeting: the playbook's model, doing real
# backlog work inside OUR sandbox rather than the playbook's unconstrained one.

def _gateway_sampling(model):
    """settings.sampling() reduced to the keys an OpenAI-shaped server accepts.

    `seed`, `temperature` and `top_p` carry over; `top_k`/`min_p`/the penalties are Ollama
    option names and are dropped rather than sent as fields the gateway would ignore. What
    is dropped is named here so the difference is visible instead of assumed.
    """
    try:
        sys.path.insert(0, str(HERE))
        import settings as _c
        s = _c.sampling(model)
    except Exception:                                        # noqa: BLE001
        return {}
    return {k: s[k] for k in ("temperature", "top_p", "seed") if k in s}


def _reachable(url, timeout=3):
    """Is something answering there? Cheap TCP check, no request sent."""
    import socket
    from urllib.parse import urlparse
    u = urlparse(url)
    try:
        with socket.create_connection((u.hostname, u.port or 80), timeout=timeout):
            return True
    except OSError:
        return False


def _resolve_gateway():
    """The endpoint that is actually LISTENING, preferring the configured one.

    The default was the gateway on :4000, which is not always running -- Ollama on :11434
    usually is. An unattended dispatch then died with `URLError: Connection refused` on
    attempt 1 and reported a failed run, so the ledger would have recorded the MODEL failing
    at a task it never saw. A model cannot fix this class itself: the failure IS the channel
    that would carry its patch, so it has to be handled before the model is reachable.

    An explicit CODER_ENDPOINT is honoured even if it looks dead -- if someone names an
    endpoint, guessing differently is worse than failing where they pointed.
    """
    named = os.environ.get("CODER_ENDPOINT")
    if named:
        return named
    for url in ("http://127.0.0.1:4000/v1/chat/completions",
                "http://127.0.0.1:11434/v1/chat/completions"):
        if _reachable(url):
            return url
    return "http://127.0.0.1:4000/v1/chat/completions"


# THE MODEL HAS 262,144 TOKENS AND OLLAMA WAS GIVING IT 4,096.
# `num_ctx` was set nowhere -- not in the modelfile, not here -- so every local run
# all week used the Ollama default of 4096 against a model whose reported
# qwen35.context_length is 262144. That is why the prober re-read files it had
# already read, lost its brief by round 50, and returned "unparseable" when asked
# for a 16,000-token answer it could not physically hold. It was not a model limit
# and it was not reasoning badly; it was being starved by a default nobody set.
#
# THE FULL 262,144 THE BUILD ADVERTISES. I first capped this at 64K and then 128K on a
# stability argument about shared memory, and then measured it: 131072 -> 17.2 GB
# resident, 262144 -> 17.6 GB, on a machine with 74 GB free. A 0.4 GB difference. The
# KV cache is allocated as the context FILLS, not at load, so the upfront cost I was
# protecting against did not exist and the caution cost half the window for nothing.
#
# The real cost of a large window is LATENCY as it fills -- attention grows with
# sequence length -- not memory. That is an acceptable trade for an unattended run,
# and it is what stops the 4K behaviour: re-reading files and losing the brief.
try:
    from settings import DEFAULTS as _D
    NUM_CTX = _D["coder"]["num_ctx"]
except Exception:
    NUM_CTX = 262144  # fallback if settings.py is unavailable

GATEWAY = _resolve_gateway()
TASKS = REPO / "workspace" / "tasks"
# CODER_RUNS: a suite that drives the real coder.py points this at its own file. Snapshot-and-restore
# of the live ledger leaked a "faketest" row every time the suite was killed, and its restore erased
# any REAL row written meanwhile (42 faketest rows found 2026-10-28).
RUNS = Path(os.environ["CODER_RUNS"]) if os.environ.get("CODER_RUNS") else REPO / "workspace" / "agent-runs.jsonl"
# The UI tails this so a run can be watched live; an attempt takes minutes
# with reasoning on, and "still thinking" otherwise looks like "hung".
# ONE FILE PER RUN, NAMED BY PID. This was a single shared path, and both defects
# it caused were found on 2026-09-14 while answering Jas's question "is there a way
# to see definitively whether the model is working":
#
#   1. CONCURRENT LANES INTERLEAVED. Two dispatches append to one file, so no line
#      can be attributed to a lane. Two lanes were running the same brief that day
#      and the log could say "attempt 5" without saying whose.
#   2. EVERY NEW LANE WIPED IT. Startup called LIVE.write_text("") -- so a lane
#      starting at 14:41 destroyed the running history of the lane that started at
#      14:08, mid-run, while it was still the only record of a three-hour job. That
#      is docs/skills/never-truncate-a-record, on the one artefact whose entire
#      purpose is to show that a long job is alive.
#
# A per-pid name fixes both at once: nothing to clear at startup because the file is
# new, and the pid in the name is the attribution. LIVE_DIR is swept by lane_status.py.
# Under the system temp dir, not a literal /tmp path (library check fixed-tmp-path, 2026-09-30). The
# rule's hazard -- lanes overwriting ONE shared file -- does not apply: each process writes its own
# <pid>.log inside it.
import tempfile as _tempfile  # noqa: E402
LIVE_DIR = Path(_tempfile.gettempdir()) / "coder-live"
LIVE = LIVE_DIR / f"{os.getpid()}.log"

# Never copied into the sandbox, so the agent cannot read them even if it tries.
EXCLUDE = {".git", "chat", "node_modules", "__pycache__", ".venv", "build", ".gradle"}
# Two rules, because one regex could not tell `core/Token.kt` from `tokens/live.json`.
# The old pattern had a bare `token` alternative and refused ordinary source files whose
# names merely contain the letters -- `Token.kt`, `tokenise.py`. A guard that blocks
# legitimate work gets worked around, which is how a guard stops guarding. Found by the env3
# probe, 2026-08-26.
#
#   1. ANY directory segment that is a secret-ish word -> refuse, whatever the file is.
#      `a/secret/b.txt` is refused even though .txt is ordinary.
#   2. A secret-ish FILE name -> refuse, unless it carries a source-code extension.
#      `Token.kt` is code. `token.env`, `tokens.json`, `id_ed25519` are not.
SECRET_WORD = re.compile(r"^([^/]*[._-])?(secrets?|credentials?|tokens?|keystore|"
                         r"id_ed25519|id_rsa|passwords?)([._-][^/]*)?$", re.I)
SECRET_EXT = (".env", ".pem", ".key", ".p12", ".jks", ".keystore")
CODE_EXT = (".py", ".kt", ".java", ".kts", ".gradle", ".sh", ".js", ".css", ".html", ".md")


class _SecretRe:
    """Kept named SECRET_RE with a .search() so every existing call site is unchanged."""

    def search(self, path):
        p = str(path).replace("\\", "/")
        parts = [x for x in p.split("/") if x]
        if any(SECRET_WORD.match(seg) for seg in parts[:-1]):
            return True                                  # rule 1: a secret-ish directory
        name = parts[-1] if parts else ""
        if name.lower().endswith(SECRET_EXT):
            return True
        if SECRET_WORD.match(name) and not name.lower().endswith(CODE_EXT):
            return True                                  # rule 2: secret-ish, and not code
        return False


SECRET_RE = _SecretRe()
# The agent could not SEE Kotlin: repo_map and the keyword scorer both listed .py and .md
# only, so an Android task would have been handed a map with no app in it.
# The set diff_of walks and snapshot() records. `L-114` was this list disagreeing with the
# baseline; the env3 probe found the list itself is too narrow -- a .toml patch applied,
# changed the file, and was reported NO-OP because .toml was not here. Same false verdict,
# one layer down: widening the baseline to match the list did nothing about the list.
#
# Kept as an allow-list rather than "everything text-like" so a patch cannot quietly diff
# binary assets or 70 GB of weights, but it now covers the configuration formats a repository
# of this shape actually contains.
SOURCE_EXT = (".py", ".md", ".kt", ".java", ".xml", ".kts", ".gradle", ".yaml", ".yml",
              ".json", ".sh", ".toml", ".cfg", ".ini", ".txt", ".css", ".js", ".html",
              ".properties", ".pro", ".sql", ".jsonl", ".service", ".conf")
NOISE_RE = re.compile(
    r"(^|/)(\.venv|venv|site-packages|dist-packages|node_modules|__pycache__"
    r"|build|\.gradle|intermediates|generated)/"
    r"|^workspace/(tasks|app-feedback|sessions|inbox|outbox)/")

# THE STARTUP CONTEXT IS APPENDED AT CALL TIME, not baked in here (2026-09-04). Same gap as
# duo_observe.py: console.py, duo_verify.py and q16_debug_harness.py all give a model hot
# memory, the retrieval commands, the skills pointer and the contract, and this path never did.
# Jas set the rule on 2026-09-03 and it reached three of five paths.
#
# It is appended rather than inlined because startup() reads the memory store, and a module-level
# call would run it at import -- including in the tests, which must not depend on it.
def system_prompt():
    try:
        import console
        return SYSTEM + console.startup()
    except Exception:                                        # noqa: BLE001
        # Fails soft and SAYS SO. A startup block that silently returns nothing is
        # indistinguishable from one that was never wired in, which is the bug being fixed.
        return SYSTEM + ("\n\n[startup context UNAVAILABLE this run -- no hot memory, no "
                         "retrieval commands. Say so if you are asked what you have.]")


SYSTEM = """You are editing a Python repository to complete one task. You cannot run
commands. You propose edits and a harness applies and tests them for you.

Reply with ONE JSON object and nothing else — no prose, no markdown fence.

If you need to see more of the repository before you can edit it, ask:

{"need": ["path/one.py", "path/two.py"], "why": "<what you expect to find>"}

You will be given those files and asked again. Ask for what you actually need; a file you
did not read is a file you cannot anchor against. When you are ready to edit:

{"ops": [ <one or more of the two shapes below> ], "why": "<one sentence>"}

SHAPE A — anchored replace. Best for a short, unique line:
  {"path": "...", "old": "<exact text>", "new": "<replacement>"}

SHAPE B — line range. Best for replacing a whole block, and it needs NO transcription:
  {"path": "...", "from": 96, "to": 112, "new": "<the replacement lines>"}
The listings above are numbered. Read the range off them and give the new text only.
Prefer SHAPE B whenever you are replacing more than about two lines: reproducing existing
code character-for-character is where most attempts fail.

Rules that decide whether your answer is usable at all:
- "old" MUST appear EXACTLY ONCE in that file, character for character, including
  indentation. Zero matches or more than one and the op is discarded unapplied.
- Keep "old" short — one to three lines. Long blocks mismatch.
- To ADD code, anchor on a nearby unique line and include it in both "old" and "new".
- Change only what the task needs. Do not reformat or rename anything else.
- Match the surrounding style: this repository comments WHY, not what.
- You may return several ops. They are applied in order, all or nothing."""


def say(*a, end="\n"):
    """Print, and mirror to the live log the UI tails."""
    msg = " ".join(str(x) for x in a) + end
    sys.stdout.write(msg)
    sys.stdout.flush()
    try:
        LIVE_DIR.mkdir(parents=True, exist_ok=True)
        with LIVE.open("a", encoding="utf-8") as f:
            f.write(msg)
    except OSError as e:
        print(f"  NOTE: live log {LIVE} not written ({e}); the message is on stdout", file=sys.stderr)


def sh(*a, cwd=None, timeout=900):
    return subprocess.run(a, capture_output=True, text=True, cwd=cwd, timeout=timeout)


def _refused(e):
    """True when `e` is the model server not answering -- down, restarting, or gone mid-run.

    t1337 (2026-10-19): only "Connection refused" counted, so a NoServer ("none answers"), a dropped
    connection on the retry, or a socket timeout ended the run as exit 1 -- an attempt against the
    brief -- and 28 such runs were parked or filed as harness bugs since 10-12. Each is a fact about
    the endpoint: wait, then refuse (exit 2), which counts against nothing."""
    r = getattr(e, "reason", e)
    return (isinstance(r, (ConnectionRefusedError, ConnectionError, TimeoutError))
            or type(e).__name__ == "NoServer"
            or any(m in str(e) for m in ("Connection refused", "none answers", "timed out")))


def sandbox():
    """A copy of the repo with the corpus and secrets absent, not merely forbidden.

    REFUSES TO NEST. A sandbox contains a full copy of the repository, tests included, and
    those tests spawn coder.py. So a coder.py running INSIDE a sandbox that builds another
    sandbox is a fork bomb with a copy of the repo per generation: 279 of them and 55 GB of
    /tmp on 2026-08-27, then 80 more after the first fix, because a mutation that disables
    the refusal above re-enables the recursion.

    That is the lesson -- the test could not be the only defence, because the thing being
    mutated WAS the defence. This check does not depend on any guard a mutation can remove:
    it asks where this process is running from.
    """
    # EVERY PLACE A SANDBOX CAN LIVE (2026-10-20): 99178d32e moved sandboxes to ~/.cache/hivemind-tmp
    # (or TMPDIR), and this check only knew /tmp and gettempdir() -- a sandbox's own tests would have
    # built nested sandboxes again. The same base sandbox() uses is checked, plus the old ones.
    _homes = {_tempfile.gettempdir(), "/tmp", str(Path.home() / ".cache" / "hivemind-tmp"),
              os.environ.get("TMPDIR") or "/tmp"}
    if any(str(REPO).startswith(os.path.join(t, "coder-")) for t in _homes) or Path(REPO).name.startswith("coder-"):
        raise SystemExit(
            "refusing to build a sandbox from inside one -- REPO is "
            f"{REPO}.\nA nested sandbox copies the whole repository, and the copy's own "
            "tests spawn coder.py again (L-128).")
    # SECOND RESOLUTION IS NOT UNIQUE. Two dispatches started in the same second both
    # computed /tmp/coder-<ts>, and the second died with FileExistsError -- so running two
    # tasks in parallel, which is the whole point of a batch, crashed one of them. The pid
    # makes it unique per process; the timestamp stays because it makes a stale sandbox
    # readable at a glance when one is left behind.
    # ON DISK UNLESS TMPDIR SAYS OTHERWISE (2026-10-20). gettempdir() is /tmp when TMPDIR is unset, and
    # /tmp on the GX10 is tmpfs -- RAM. A whole repository copy per build sat there: 9.6 GB of sandboxes
    # at 06:22Z, the night after the memory guard killed Swift at 0.71 GB free. TMPDIR still wins (t415).
    _base = Path(os.environ.get("TMPDIR") or (Path.home() / ".cache" / "hivemind-tmp"))
    _base.mkdir(parents=True, exist_ok=True)
    root = _base / f"coder-{int(time.time())}-{os.getpid()}"
    root.mkdir(parents=True)
    # CLEAN UP FROM THE FIRST FILE (2026-10-20): a SIGTERM during the copy itself left a half-built sandbox,
    # because the handler was installed only after sandbox() returned.
    import signal as _signal
    import threading
    if threading.current_thread() is threading.main_thread():
        _LIVE.update(root=root, state=None)
        _signal.signal(_signal.SIGTERM, _on_sigterm_cleanup)
    if SANDBOX_MODE == "tracked":
        return _sandbox_tracked(root)
    for item in REPO.iterdir():
        if item.name in EXCLUDE:
            continue
        if item.name == "workspace":
            # workspace/mirror holds the backup bundles (2.06 GB, 2026-10-13) -- no build reads them,
            # and every sandbox was copying them (5.4 GB per build, in RAM while /tmp was tmpfs).
            dst = root / item.name
            shutil.copytree(item, dst, ignore=shutil.ignore_patterns(
                "__pycache__", "*.pyc", ".git", "*.apk", "*.zip", "build", ".gradle", "intermediates",
                "mirror"), dirs_exist_ok=True)
            continue
        dst = root / item.name
        if item.is_dir():
            shutil.copytree(item, dst, ignore=shutil.ignore_patterns(
                "__pycache__", "*.pyc", ".git", "*.apk", "*.zip",
                "build", ".gradle", "intermediates"), dirs_exist_ok=True)
        else:
            shutil.copy2(item, dst)
    return root


# WHAT GIT KNOWS, NOT THE WHOLE TREE (Jas, 2026-10-20: "They only need to worry about their Git tracks
# and the code"). The full copy was ~14 GB per sandbox -- 9.5 GB of workspace/ (run logs, NAS bundles),
# 3.6 GB of .claude/ worktrees -- against 1.40 GB of tracked + new untracked files and 60 MB of code.
# Every build and every builder test made one. Ignored files (the logs) are not copied at all.
# MEASURED, THEN ON (2026-10-20): in a tracked sandbox the 764 non-builder suites failed only where they
# already fail (280, every one in the ceiling or sandbox-baseline.json -- 0 undeclared), and the 70
# builder suites gave the same 28 pass / 42 declared as the full copy. HIVEMIND_SANDBOX=full keeps the
# old whole-tree copy for a comparison.
SANDBOX_MODE = os.environ.get("HIVEMIND_SANDBOX", "tracked")


def _sandbox_tracked(root):
    """Copy the working-tree contents of every tracked file, plus untracked files git does not
    ignore (a new oracle, a new script), minus EXCLUDE's top-level names. -> root."""
    r = subprocess.run(["git", "-C", str(REPO), "ls-files", "-co", "--exclude-standard", "-z"],
                       capture_output=True, timeout=300)
    if r.returncode:
        raise SystemExit(f"sandbox: git ls-files failed: {r.stderr.decode(errors='replace')}")
    skip_parts = {"__pycache__", ".git", "build", ".gradle", "intermediates"}
    for rel in r.stdout.decode("utf-8", errors="surrogateescape").split("\0"):
        if not rel:
            continue
        parts = rel.split("/")
        if parts[0] in EXCLUDE or skip_parts & set(parts) or rel.endswith((".pyc", ".apk", ".zip")):
            continue
        src = REPO / rel
        if not src.is_file():                       # deleted in the working tree, or a submodule
            continue
        dst = root / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
    return root


def read_task(tid):
    """Title and body — with the note thread kept OUT of the retrieval text.

    Notes are written for a human and are full of words about the harness: "oracle",
    "suites", "diff", "ACCEPTED". Feeding them to a keyword scorer ranks this repository's
    own tooling above the app the task is about, which is exactly what happened on the first
    Android attempt: the agent was handed coder.py and session_log.py for a MediaPlayer bug.

    The agent still SEES the notes -- they are real context about what was tried. They just
    do not get a vote on which files to open.
    """
    hits = list(TASKS.glob(f"{tid}--*.md")) + list((TASKS / "done").glob(f"{tid}--*.md"))
    if not hits:
        raise SystemExit(f"no such task: {tid}")
    text = hits[0].read_text(encoding="utf-8")
    title = next((l[7:].strip() for l in text.splitlines() if l.startswith("title:")), tid)
    area = next((l[5:].strip() for l in text.splitlines() if l.startswith("area:")), "")
    body = text.split("---", 2)[-1].strip()
    brief = body.split("## Notes")[0].strip()
    return title, body, brief, area


def latest_brief(body, keep=2):
    """The task statement plus only the most recent review note(s).

    `body` accumulates every note ever written. Those notes are for Jas and for the next
    session -- full history, every rejected approach, code samples for each. Handing all of it
    to the MODEL is a different thing entirely: on t159 the note reached 9,916 characters,
    comparable to a whole file excerpt, and described three different rejected approaches at
    once. The run then spent 16,000 tokens reasoning without emitting any JSON and five
    further attempts re-reading the file.

    Same family as `L-114` and the retrieval starvation in `numbered()`: give the model the
    wrong amount of context and read the result as incapacity. Starving it was the old
    failure; this is the flooding one.

    The record keeps everything. The model gets the statement and the latest correction.
    """
    if "## Notes" not in body:
        return body.strip()
    statement, notes = body.split("## Notes", 1)
    notes = notes.split("## Attachments")[0]
    # Notes are separated by a bold attributed heading: **DATE · author** — ...
    parts = re.split(r"\n(?=\*\*\d{4}-\d{2}-\d{2} · )", notes.strip())
    parts = [p for p in parts if p.strip()]
    # The FIRST note and the LAST. The first is the scoping -- where the bug is and what a
    # correct fix must do -- and dropping it leaves the model with corrections to a task it
    # was never told. The last is the current feedback. Everything between is the history of
    # approaches already rejected, which is exactly the part that confuses rather than helps.
    # By AUTHOR, not by position. The model writes its own run record after every run, so the
    # last note on a task is almost always `local/<model>` reporting what it just did -- which
    # is history, not guidance. On t159 that was 11 notes, of which only 4 were reviews.
    reviews = [x for x in parts if "· claude-code" in x.split("\n", 1)[0]]
    chosen = []
    if reviews:
        chosen.append(reviews[0])
        if len(reviews) > 1:
            chosen.append(reviews[-1])
    elif parts:
        chosen.append(parts[0])
    out = statement.strip()
    if chosen:
        out += (f"\n\nThe scoping, and the most recent review ({len(parts)} note(s) on this "
                "task in total, including the model's own run records; the ones in between "
                "are rejected approaches, kept as record "
                "and NOT instructions):\n\n" + "\n\n".join(c.strip() for c in chosen))
    return out


def relevant_files(root, task_text, limit=6, area=""):
    """Symbols AND keywords, merged — not one instead of the other.

    symindex reads Python only. The first version returned its ranking whenever it produced
    anything, so an Android task got five Python files and no app: the scorer had confidently
    ranked the wrong language. Merging means the symbol signal wins where it exists and the
    keyword signal covers everything else.
    """
    scores = {}
    try:
        sys.path.insert(0, str(REPO / "scripts"))
        import symindex
        for rel, sc in symindex.files_for(task_text, symindex.build(root), limit=limit * 2):
            scores[rel] = scores.get(rel, 0) + sc
    except Exception as e:                               # noqa: BLE001 -- said, not swallowed
        _skip("symbol index unavailable; files ranked by name only", e)
    for rel, _ in _keyword_files(root, task_text, limit * 2):
        scores[rel] = scores.get(rel, 0) + 8             # comparable to a rare-symbol hit
    # The task's `area` names the project, and nothing in the prose does -- "play button"
    # and "recording" match every Android app here, so the first Kotlin-aware run returned
    # superbutton and indoor-voice for a good-dictator bug.
    #
    # It has to be a SCOPE, not a boost. Boosting only re-ranks files already found, and the
    # app's files were never candidates: no word in "play button restarts instead of pausing"
    # appears in them. So bring the area's own sources in, then let keywords order them.
    if area:
        stem = area.lower().replace("-", "")
        projects = [d for d in (root / "projects").iterdir()
                    if d.is_dir() and (stem[:5] in d.name.lower().replace("-", "")
                                       or d.name.lower().replace("-", "")[:5] in stem)]
        for d in projects:
            for f in d.rglob("*"):
                if not f.is_file() or f.suffix not in SOURCE_EXT:
                    continue
                rel = f.relative_to(root).as_posix()
                if NOISE_RE.search(rel):
                    continue
                words = set(re.findall(r"[a-zA-Z_]{4,}", task_text.lower()))
                try:
                    head = f.read_text(encoding="utf-8", errors="replace").lower()
                except OSError as e:
                    _skip(f"{f} unreadable, not ranked", e)
                    continue
                scores[rel] = (scores.get(rel, 0) + 12
                               + 4 * sum(1 for w in words if w in head))
    ranked = sorted(scores.items(), key=lambda kv: -kv[1])[:limit]
    return [(rel, (root / rel).stat().st_size) for rel, _ in ranked
            if (root / rel).is_file()]


def _keyword_files(root, task_text, limit=6):
    """Cheap retrieval: rank tracked python/markdown by keyword overlap with the task.

    Deliberately dumb. A model that can request arbitrary files is a model that can read
    its way toward anything in the sandbox, and the sandbox is the security boundary --
    keeping retrieval mechanical keeps the boundary where it can be reasoned about.
    """
    words = {w.lower() for w in re.findall(r"[a-zA-Z_]{4,}", task_text)}
    scored = []
    for p in root.rglob("*"):
        if not p.is_file() or p.suffix not in SOURCE_EXT:
            continue
        rel = p.relative_to(root).as_posix()
        if any(rel.startswith(d + "/") for d in EXCLUDE) or SECRET_RE.search(rel):
            continue
        # Vendored code and the task text itself are the two things retrieval must never
        # return. The first run fed the model networkx's test suite out of a .venv and the
        # task file it had already been given, so it saw no repository code at all and
        # invented a path -- which the sandbox then correctly refused. A retrieval that
        # returns plausible-looking irrelevance is worse than one that returns nothing,
        # because the model will use it.
        if NOISE_RE.search(rel):
            continue
        try:
            head = p.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            _skip(f"{p} unreadable, skipped", e)
            continue
        name_hits = sum(2 for w in words if w in rel.lower())
        body_hits = sum(1 for w in words if w in head[:4000].lower())
        if name_hits or body_hits:
            scored.append((name_hits * 3 + body_hits, rel, len(head)))
    scored.sort(reverse=True)
    return [(rel, n) for _, rel, n in scored[:limit]]


def repo_map(root, limit=140):
    """Every file the agent may open, with its size. Given up front, so it can CHOOSE.

    Keyword retrieval hands the model six files and hopes. On `t143` it did not surface the
    file that needed changing at all, and the model invented a path -- which read as a
    hallucination and was actually a starved context. A map plus the ability to ask is the
    difference between guessing and looking.
    """
    rows = []
    for p in sorted(root.rglob("*")):
        if not p.is_file() or p.suffix not in SOURCE_EXT:
            continue
        rel = p.relative_to(root).as_posix()
        if NOISE_RE.search(rel) or SECRET_RE.search(rel):
            continue
        rows.append((rel, p.stat().st_size))
    rows.sort()
    head = rows[:limit]
    out = "\n".join(f"  {r:<62} {n:>7,}" for r, n in head)
    if len(rows) > limit:
        out += f"\n  … and {len(rows) - limit} more"
    return out


def readable(root, rel, targets=None):
    """Resolve a requested path inside the sandbox, or say why not.

    The sandbox is the boundary -- it has no `chat/`, no secrets and no network -- so a read
    inside it cannot reach anything the agent is not already allowed to see. This function
    exists to stop path tricks, not to re-litigate that boundary.
    """
    if not rel or rel.startswith("/") or ".." in rel:
        return None, "path refused"
    if SECRET_RE.search(rel) or NOISE_RE.search(rel):
        return None, "out of bounds"
    p = (root / rel)
    try:
        p.relative_to(root)
    except ValueError:
        return None, "escapes the sandbox"
    if not p.is_file():
        return None, "no such file"
    if targets is not None and rel not in targets:
        return None, f"out of target: dispatch is scoped to {', '.join(targets)}; you asked for {rel}"
    return p, ""


# The excerpt budget. This was 14,000 characters -- about 1.4% of the 262,144-token context
# these models actually have, and roughly a quarter of one file in this repository. It was
# sized for a small-context model and never revisited when the model changed.
#
# The cost was not subtle: coder.py is 53,834 chars, so reading it took five slices, and both
# qwen3.6 and qwen3.8 spent six of seven attempts reading rather than editing before either
# could propose anything. It is the same shape as `t096` -- rated context versus served
# context, with nothing announcing the gap.
#
# 120,000 chars shows any file in this repository whole and still leaves the great majority
# of the window free. Not unlimited: an excerpt large enough to bury the task is its own
# failure, and `latest_brief` exists because flooding is as harmful as starving.
# RAISED 120,000 -> 160,000 on 2026-09-14. The resume path for t238 took coder.py to 109,249
# bytes, and numbered() adds a line-number prefix to each of ~2,700 lines, so the listing
# passed 120,000 and test_coder_guards' "coder.py is shown whole" went red -- meaning a model
# asked to edit coder.py (t238 itself is such a task) would have received it cut. 160,000
# chars is about 46k tokens: whole, and still inside the 65,536-token window with the brief.
# The guard's ceiling (400,000) is untouched.
# RAISED 180,000 -> 210,000 on 2026-10-01: yield/resume, scope_gate and the jsonx parse took
# coder.py to 160 KB, ~180,000 chars numbered. Whole is still the rule; whether it FITS a
# window is prompt_fits()'s question, and a brief editing coder.py names SYMBOLS (scope_gate).
# RAISED 210,000 -> 260,000 on 2026-10-03: feedback.py reached 221 KB (~254,000 chars numbered)
# with the System card, Finished page, sessions route and timeline. Same rule as before; the
# lasting fix is splitting feedback.py (handover 2026-10-03).
EXCERPT_CAP = 260_000


_EXCERPT_NUM = re.compile(r"^\s*(\d+)")


def excerpt_last_line(text, excerpt):
    """The length of the gapless run 1,2,3,... of line numbers `excerpt` shows, clamped to the
    file's own line count. A line with no leading integer is not part of the run and does not
    end it: numbered() prints a header and '... N line(s) not shown ...' markers, and the head
    window of a long file follows the header, so treating an unnumbered line as a stop reads 0
    from every windowed listing -- which is the record that shows the model the tail and refuses
    it the head. Only a GAP in the numbering ends the run. (t991)"""
    present = {int(m.group(1)) for m in (_EXCERPT_NUM.match(l) for l in (excerpt or "").splitlines())
               if m is not None}
    n = 0
    while n + 1 in present:
        n += 1
    return min(n, len((text or "").splitlines()))


def numbered(text, task_words=(), cap=EXCERPT_CAP, window=60):
    """Show the parts of a file the task is about, not the first 400 lines of it.

    The first version head-truncated. `check_workspace.py` is 1,100 lines and the code
    `t143` needed sits at line 1021, so the model was asked to edit something it could not
    see, proposed an anchor from the part it could, and was refused. It looked like the
    model failing to follow the anchor rule; it was the harness never showing it the code.

    Same failure family as the token budget in codeval and the reasoning flag: starve the
    model of what it needs, then read the result as incapacity.
    """
    lines = text.splitlines()
    if len(lines) * 60 < cap:
        return "\n".join(f"{i:5d}  {l}" for i, l in enumerate(lines, 1))[:cap]

    words = {w.lower() for w in task_words if len(w) > 3}
    scores = []
    for i, l in enumerate(lines):
        low = l.lower()
        scores.append(sum(1 for w in words if w in low))
    # Pick the densest windows, merge overlaps, and say plainly that it is an excerpt --
    # a model shown a fragment it believes is whole will anchor confidently on nothing.
    best = sorted(range(len(lines)), key=lambda i: -sum(scores[max(0, i - 5):i + 5]))
    picked, spans = 0, []
    for i in best:
        lo, hi = max(0, i - window // 2), min(len(lines), i + window // 2)
        if any(lo < b and a < hi for a, b in spans):
            continue
        spans.append((lo, hi))
        picked += hi - lo
        if picked * 60 > cap:
            break
    spans.sort()
    out = []
    prev = 0
    for lo, hi in spans:
        if lo > prev:
            out.append(f"        … {lo - prev} line(s) not shown …")
        out += [f"{i+1:5d}  {lines[i]}" for i in range(lo, hi)]
        prev = hi
    if prev < len(lines):
        out.append(f"        … {len(lines) - prev} line(s) not shown …")
    return ("(EXCERPT — this file is {} lines; only the regions matching the task are "
            "shown)\n".format(len(lines)) + "\n".join(out))[:cap]


SYMBOLS_LINE = re.compile(r"^SYMBOLS:\s*(.+)$", re.M)


def symbols_of(brief):
    """The functions/classes a brief names on a `SYMBOLS:` line -- comma or space separated,
    `Class.method` allowed. Empty when the brief names none."""
    out = []
    for m in SYMBOLS_LINE.finditer(brief or ""):
        # A name is an identifier, optionally dotted. "(new)", "{', '.join(missing", "(coder.py:1351" are
        # remarks and code quoted on the line, not symbols (t667: 20 of 29 symbol-briefed files reported a
        # name "NOT FOUND", most of them these).
        out += [x for x in re.split(r"[,\s]+", m.group(1).strip()) if re.fullmatch(r"[A-Za-z_]\w*(\.\w+)*", x)]
    return out


def lines_of(brief):
    """Line numbers a brief names ('LINES: 719, 722' or 'line(s) 164, 301'), deduped, in order (t427)."""
    out = []
    for m in re.finditer(r"\blines?(?:\(s\))?\s*:?\s*((?:\d+(?:\s*(?:,|and)\s*)?)+)", brief or "", re.I):
        for n in re.findall(r"\d+", m.group(1)):
            if int(n) not in out:
                out.append(int(n))
    return out


def sliced_by_lines(text, lines, pad=3):
    """A numbered window of `pad` lines around each named line; None if nothing to show (t427)."""
    src = text.splitlines()
    keep = sorted({i for n in lines for i in range(n - pad, n + pad + 1) if 1 <= i <= len(src)})
    if not keep:
        return None
    out, prev = [], None
    for i in keep:
        if prev is not None and i > prev + 1:
            out.append("  ...")
        out.append(f"{i:5d}  {src[i - 1]}")
        prev = i
    return "\n".join(out) + "\n"


def sliced_tail(text, n=80):
    """The last `n` lines of a file, numbered with their REAL line numbers (t672). For a brief that APPENDS
    to a big non-Python file -- docs/LESSONS.md is 270 KB -- the end is all the builder needs to see."""
    src = text.splitlines()
    lo = max(0, len(src) - n)
    return (f"(TAIL -- this file is {len(src)} lines; shown: its last {len(src) - lo}. Add the change after "
            f"line {len(src)}.)\n" + "\n".join(f"{i + 1:5d}  {src[i]}" for i in range(lo, len(src))) + "\n")


def sliced(text, symbols, header_cap=60):
    """Show ONLY the named symbols of a Python file, with their REAL line numbers.

    THE SCOPE-SCHEMA HALF OF window-full (2026-09-23). A whole-file excerpt of coder.py is
    ~40,000 tokens; p20 needed one function of it and filled a 65,536-token window before it
    could answer. When a brief names its symbols, this is what the model sees: the file's
    header (imports and constants, capped), then each symbol's full source including its
    decorators, in file order. Line numbers are the file's own, so line-range and anchored
    edits apply to the whole file unchanged. A name that is not found is SAID, not dropped --
    a silent omission would read as "the brief is wrong about the file" with no way to tell.
    Returns None when the file does not parse, so the caller falls back to numbered().
    """
    import ast
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return None
    lines = text.splitlines()
    spans, found = [], set()

    def span(node):
        lo = min([node.lineno] + [d.lineno for d in getattr(node, "decorator_list", [])])
        return lo, node.end_lineno

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if node.name in symbols:
                spans.append(span(node))
                found.add(node.name)
            if isinstance(node, ast.ClassDef):
                for sub in node.body:
                    q = f"{node.name}.{getattr(sub, 'name', '')}"
                    # A METHOD IS NAMED BARE OR QUALIFIED (t667, 2026-10-05). `do_GET` is a method of
                    # feedback.py's Handler; the brief said `do_GET`, this matched only `Handler.do_GET`,
                    # the model was shown the file's first 60 lines and plan_detail but NOT do_GET, asked for
                    # the file, got the same slice, asked again, and was refused "no room" five times --
                    # p249, p259 and p261 all died this way. scope_gate already lets the bare name through.
                    if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)) and (
                            q in symbols or sub.name in symbols):
                        spans.append(span(sub))
                        found.update({q, sub.name} & set(symbols))
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            # A module-level constant named on the SYMBOLS line (REPO_ROOT, EXCERPT_CAP) is shown too.
            tg = node.targets if isinstance(node, ast.Assign) else [node.target]
            hit = {t.id for t in tg if isinstance(t, ast.Name)} & set(symbols)
            if hit:
                spans.append(span(node))
                found.update(hit)
    first_def = next((n.lineno for n in tree.body
                      if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))),
                     len(lines) + 1)
    head_hi = min(first_def - 1, header_cap)
    out = [f"(SLICE -- this file is {len(lines)} lines; shown: its first {head_hi} line(s) and "
           f"the symbol(s) the brief names. Line numbers are the file's own.)"]
    out += [f"{i:5d}  {lines[i - 1]}" for i in range(1, head_hi + 1)]
    prev = head_hi
    for lo, hi in sorted(set(spans)):
        if lo > prev + 1:
            out.append(f"        … {lo - prev - 1} line(s) not shown …")
        out += [f"{i:5d}  {lines[i - 1]}" for i in range(max(lo, prev + 1), hi + 1)]
        prev = max(prev, hi)
    if prev < len(lines):
        out.append(f"        … {len(lines) - prev} line(s) not shown …")
    missing = [x for x in symbols if x not in found]
    if missing:
        out.append(f"(NOT FOUND in this file: {', '.join(missing)})")
    return "\n".join(out)


_DEF = re.compile(r"^\s*(?:\d+\s+)?(?:async\s+def|def|class)\s+([A-Za-z_]\w*)", re.M)


def scope_gate(brief, text, chosen):
    """(ok, reason). When a brief names SYMBOLS, the context chosen for a file must be a slice.

    t447 (2026-09-27): a brief named SYMBOLS: symbols_of, sliced, prompt_fits and the prompt
    was still 63,625 of 65,536 tokens -- the whole of coder.py, because the fallback to
    numbered() is silent. Four attempts burned the window. The test is the result, not the
    path taken: if `chosen` defines a function or class the brief did not name, the whole file
    (or something like it) went out, and the run is refused before attempt 1 with the cause.
    """
    symbols = symbols_of(brief)
    if not symbols:
        return True, ""
    allowed = set()
    for s in symbols:
        allowed.update(s.split("."))
    # Only the file's own top-level and method names count: a helper nested INSIDE a named
    # function is part of the slice, not a sign of the whole file.
    import ast
    try:
        tree = ast.parse(text)
        structural = set()
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                structural.add(node.name)
                if isinstance(node, ast.ClassDef):
                    structural.update(s.name for s in node.body
                                      if isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef)))
    except SyntaxError:
        structural = None            # unparseable: every definition shown counts
    shown = {m.group(1) for m in _DEF.finditer(chosen or "")}
    if structural is not None:
        shown &= structural
    extra = sorted(shown - allowed)
    if not extra:
        return True, ""
    why = ("sliced() returned nothing usable, so the WHOLE FILE was the fallback"
           if chosen == text or len(extra) > 3 else "the context holds more than the slice")
    return False, (f"the brief names SYMBOLS: {', '.join(symbols)} but {why} -- it also shows "
                   f"{', '.join(extra[:8])}{' ...' if len(extra) > 8 else ''}. Check the names "
                   f"exist and the file parses (sliced() needs both); a whole-file context is "
                   f"what filled the window on t447.")


# INTERRUPT AND RESUME (p20, t406). Jas, 2026-09-14: "we need a way to interrupt Q16 ... if it
# can resume instead of starting over that would be amazing." Every failed attempt is reverted,
# so between attempts the sandbox IS the baseline and the whole of a run's progress is its
# conversation: what was asked, what was shown, what failed. That is what a checkpoint holds.
YIELDED = 4                                       # exit code: stopped on request, resumable
YIELD_DIR = REPO / "workspace" / "run-logs" / "yield"
CHECKPOINT_DIR = REPO / "workspace" / "run-logs" / "checkpoints"
_LIVE = {}                                        # the state as of the last attempt boundary


def write_checkpoint(path, state):
    """Write atomically: a reader sees the old checkpoint or the new one, never half."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(state), encoding="utf-8")
    os.replace(tmp, path)


def load_checkpoint(path):
    """The saved state, or None when there is none or it is torn -- a resume that cannot
    trust its checkpoint starts over, which is slower and never wrong."""
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as e:
        print(f"  NOTE: checkpoint {path} unreadable ({e}) -- starting over", file=sys.stderr)
        return None


def _safe_model(model):
    """The model as dispatch_loop._safe() writes it into flag and checkpoint names. One
    convention, both sides: a flag the runner cannot find is a yield nobody honours."""
    return model.replace(":", "_")


def yield_requested(model):
    """True when someone has asked the run on `model` to give the model back."""
    return (Path(YIELD_DIR) / f"{_safe_model(model)}.flag").is_file()


def _on_sigterm(signum, frame):
    """An interrupt checkpoints instead of dying: the state of the last finished attempt is
    saved, the sandbox removed, and the run exits YIELDED. The attempt in flight is lost --
    a half-finished model call cannot be resumed -- and that is the only loss."""
    st = _LIVE.get("state")
    if st:
        write_checkpoint(_LIVE["path"], st)
        print(f"\n  SIGTERM -- checkpointed after attempt {st['n'] - 1} to {_LIVE['path']}; "
              f"resume with --resume-checkpoint", file=sys.stderr, flush=True)
    if _LIVE.get("root"):
        shutil.rmtree(_LIVE["root"], ignore_errors=True)
    raise SystemExit(YIELDED if st else 143)


def _on_sigterm_cleanup(signum, frame):
    """Outside the attempts (the baseline before them, the delivery after): nothing to checkpoint,
    but the sandbox still goes (2026-10-20). Python runs no `finally` on SIGTERM, so every build the
    memory guard or the 1-hour cap stopped left its sandbox behind -- 255 were swept in one stretch."""
    if _LIVE.get("root"):
        shutil.rmtree(_LIVE["root"], ignore_errors=True)
    raise SystemExit(143)


def prompt_fits(prompt_chars, budget, window, chars_per_token=3.0, margin=2000):
    """(fits, estimated_prompt_tokens). Will the prompt AND the output budget fit the window?

    Checked before the first attempt so a brief that cannot fit is refused in seconds, not
    after two 10-minute attempts (p20). 3.0 chars/token is deliberately pessimistic for code:
    p20's first prompt measured 52,538 tokens for coder.py plus the brief and repo map. An
    unknown window is not a refusal."""
    est = int(prompt_chars / chars_per_token)
    if not window:
        return True, est
    return est + int(budget) + margin <= int(window), est


def _skip(what, e):
    """A skipped step is SAID, never silent (library check swallowed-exception, 2026-09-30)."""
    print(f"  NOTE: {what} ({type(e).__name__}: {e})", file=sys.stderr)


def resolve_model(role):
    """The MODEL behind a gateway role, or the role itself if it cannot be resolved.

    `--model local` names a ROLE, not a model. Every run before 2026-08-25 recorded the string
    "local" in agent-runs.jsonl, so the ledger cannot say which model produced any of them --
    and the whole point of t151 is comparing models on the same task. A comparison whose rows
    are all labelled the same is not a comparison.

    Same lesson as `L-113`, one layer up: identify the subject by something that is actually
    unique to it, not by the label it happens to be wearing.
    """
    if role != "local":
        return role
    if "11434" in GATEWAY:
        # THE CONFIGURED CODER MODEL WINS, 2026-09-14. This used to return m[0] from /api/ps --
        # WHICHEVER MODEL HAPPENED TO BE FIRST. With gemma4:26b resident as the supervisor and
        # the coding model second, `--model local` resolved to ollama/gemma4:26b, so a coding
        # dispatch would have gone to the SUPERVISOR. Verified before fixing.
        #
        # The irony is in this function's own docstring: it cites L-113, "identify the subject
        # by something that is actually unique to it, not by the label it happens to be
        # wearing", and then identified it by LIST POSITION, which is not even a label.
        try:
            sys.path.insert(0, str(HERE))
            import settings as _cfg
            want = (_cfg.load().get("coder", {}) or {}).get("model") or ""
        except Exception:                                           # noqa: BLE001
            want = ""
        try:
            with urllib.request.urlopen("http://127.0.0.1:11434/api/ps", timeout=3) as f:
                loaded = [x["name"] for x in (json.load(f).get("models") or [])]
        except Exception:                                           # noqa: BLE001
            loaded = []
        if want:
            # Configured explicitly. Say so even if it is not resident -- ollama will load it,
            # and silently substituting a different model is how a comparison stops comparing.
            return f"ollama/{want}"
        # Nothing configured: fall back to what is loaded, but NEVER to the supervisor, whose
        # job is judging this work rather than doing it.
        try:
            sup = (_cfg.load().get("supervisor", {}) or {}).get("model") or ""
        except Exception:                                           # noqa: BLE001
            sup = ""
        for name in loaded:
            if name != sup:
                return f"ollama/{name}"
        return "ollama/unknown"
    r = subprocess.run(["docker", "ps", "--filter", "name=vllm", "--format", "{{.Names}}"],
                       capture_output=True, text=True, timeout=30)
    for name in r.stdout.split():
        ip = subprocess.run(["docker", "inspect", name, "--format",
                             "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}"],
                            capture_output=True, text=True, timeout=30).stdout.strip()
        if not ip:
            continue
        try:
            with urllib.request.urlopen(f"http://{ip}:8000/v1/models", timeout=3) as f:
                served = json.load(f)["data"][0]["id"]
        except Exception as e:                           # noqa: BLE001 -- said, not swallowed
            _skip(f"vLLM container {name} at {ip} did not answer /v1/models", e)
            continue
        # A smoke model can be resident beside the real one; it is never the coding model.
        if "0.5B" not in served:
            return served
    return role


def unseen(root, rel, upto, cap=EXCERPT_CAP):
    """(text, new_high_water) for the next slice of a file after line `upto`, or None.

    The excerpt window is keyword-chosen and capped, so a large file is never shown whole.
    That is right for a first look and wrong for a second: repeating the same window turns a
    reasonable request -- "show me more" -- into an infinite loop.
    """
    p = root / rel
    if not p.is_file():
        return None
    lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    if upto >= len(lines):
        return None
    out, n = [], upto
    while n < len(lines) and sum(len(x) for x in out) < cap:
        out.append(f"{n+1:5d}  {lines[n]}")
        n += 1
    if not out:
        return None
    return (f"--- {rel} (lines {upto+1}-{n} of {len(lines)}) ---\n" + "\n".join(out), n)


def compose(base, opened, tail):
    """base + every file opened so far + this round's feedback.

    EVERY branch must go through this. The first fix wired accumulation into the ASKED path
    only, and the other five -- unparseable, refused, no-op, won't-compile, tests-failed --
    still rebuilt from `base`, silently dropping every file the model had asked for. t017 then
    alternated forever: ask, receive, fail to parse, LOSE the files, ask again. One of those
    branches even told the model "the files above are unchanged" when there were no files
    above.

    Six copies of one rule is the drift behind `L-114` and `L-115`. Now there is one -- and it
    is module-level rather than a closure so a test can drive it with real values. A source
    grep cannot tell `base + files + tail` from `base + tail`; that mutation was MISSED once.
    """
    files = ("\n\nFiles you have already been shown:\n\n"
             + "\n\n".join(opened[r] for r in sorted(opened))) if opened else ""
    return base + files + tail



def _tel(harness, model, seconds, num_predict, eval_count, prompt_eval_count,
         done_reason, timeout_s, load_seconds):
    """Record one model call. NEVER let telemetry break a dispatch -- but never silently.

    Jas, 2026-09-14: "If we can also collect data on tokens and time, and the model and harness
    for each run, then we can have evidence for later optimizations." This is the call site that
    matters most, because coder.py is where the long generations happen and where a timeout
    being wrong is expensive.
    """
    try:
        sys.path.insert(0, str(REPO / "scripts"))
        import run_telemetry
        run_telemetry.record(harness=harness, model=model, seconds=seconds,
                             num_predict=num_predict, eval_count=eval_count,
                             prompt_eval_count=prompt_eval_count, done_reason=done_reason,
                             timeout_s=timeout_s, load_seconds=load_seconds, ok=True)
    except Exception as e:                                          # noqa: BLE001
        print(f"  (telemetry not recorded: {type(e).__name__}: {e})", file=sys.stderr)


# timeout=None MEANS DERIVE IT, 2026-09-14. It was 1800 against max_tokens=16000, and at the
# 5.1 tok/s this box decodes bf16 a full-budget answer needs 3137 SECONDS -- so a long reply
# was killed AT THE SOCKET and then reported by the caller as an unusable one. t238's symptom
# arriving by a second route. settings.timeout_for() derives it from the measured floor rate
# so the number moves when the model or the measurement does.
def ask(prompt, model, think=True, max_tokens=16000, timeout=None, raise_on_length=False):

    """Ask the local model, and get back only the answer -- never the reasoning.

    `chat_template_kwargs: {enable_thinking}` is a vLLM-ism. **Ollama ignores it silently**,
    so the model fell back to unbounded reasoning and burned the entire output budget without
    ever emitting the JSON. Measured 2026-08-26: 16,000 tokens, 178 seconds, and an EMPTY
    string after the `</think>` split. That is the whole of `UNPARSEABLE x7`, the second-tallest
    bar on the first Pareto -- not a parser bug and not the model reasoning badly, a parameter
    one backend honours and the other drops on the floor.

    Ollama's own `/api/chat` takes a first-class `think` and returns `thinking` and `content`
    as SEPARATE FIELDS, so there is no trace for the answer to get lost inside and no string
    splitting to get wrong. `"low"` still reasons -- verified, 299 tokens with a populated
    `thinking` field -- so `D-30` is satisfied rather than traded away.
    """
    if timeout is None:
        # DERIVED FROM THE MEASURED DECODE RATE, not a constant. See settings.timeout_for().
        # Falls back generously rather than to the old 1800, which was the bug.
        try:
            sys.path.insert(0, str(Path(__file__).resolve().parent))
            import settings as _t
            # SELF-TUNING: pass the harness+model pair so the cap comes from the last 100
            # successful runs of THIS combination rather than the 4 tok/s static floor.
            # Falls back to static until MIN_ROWS_TO_TUNE rows exist.
            timeout = _t.timeout_for(max_tokens or 16000,
                                     harness="coder.py/native", model=model)
        except Exception:                                           # noqa: BLE001
            timeout = int((max_tokens or 16000) / 4.0) + 600

    t = time.time()
    if "11434" in GATEWAY:
        # KNOBS COME FROM settings.py, which the console's /settings page writes. One file,
        # so what Jas sets on his phone is what a batch run uses -- two copies of these
        # numbers would disagree within days (L-046). An explicit argument still wins:
        # `think` is passed by the caller and --no-think must keep working.
        try:
            sys.path.insert(0, str(HERE))
            import settings
            _cfg = settings
            _s = settings.load()["coder"]
        except Exception:
            # NO temperature IN THE FALLBACK (2026-09-03). It used to be 0 -- greedy
            # decoding, which Qwen's documentation explicitly forbids for this model and
            # which the vendor contract exists to prevent. If settings.py is unreachable the
            # honest thing is to send NO sampling and let the backend default apply, not to
            # silently pick the one value we know is wrong.
            _cfg, _s = None, {"think": "low", "num_ctx": NUM_CTX,
                              "num_predict": max_tokens, "keep_alive": "24h"}
        body = {"model": model, "stream": False,
                "think": (_s["think"] if think else False),
                # THE CONTRACT. This sent num_ctx/temperature/num_predict and nothing else,
                # so the patcher ran without the model author's top_k/top_p, without a seed,
                # and without any stop sequence. It also read _s["temperature"], now None
                # meaning "the author decides" -- Ollama tolerates the null, but a tolerated
                # null is not a chosen value. See settings.options().
                "options": (settings.options("coder", model, num_ctx=_s["num_ctx"],
                                         num_predict=max_tokens or _s["num_predict"])
                            if _cfg else
                            # settings.py unreachable. Say what we send rather than
                            # silently sending less than the contract promises.
                            {"num_ctx": _s["num_ctx"],
                             "num_predict": max_tokens or _s["num_predict"]}),
            # KEEP THE MODEL RESIDENT. Default keep_alive is 5 MINUTES, and a turn at a
            # 40,000-token budget can take 25 -- so between rounds, and between passes while
            # the driver does git work, the model UNLOADS and the next round pays a full
            # reload of a 262K context. That reload is the likely cause of the HTTP 500 seen
            # on 2026-08-27. For an 87-hour unattended run the model should never leave
            # memory: there is 74 GB free and nothing else wants it.
            "keep_alive": _s["keep_alive"],
                "messages": [{"role": "system", "content": system_prompt()},
                             {"role": "user", "content": prompt}]}
        # ANY ENGINE (2026-10-06, docs/ENGINES.md): infer.chat sends this native-shape body to
        # whichever server serves `model` -- Ollama as is, an OpenAI-compatible engine (vLLM:
        # Swift) translated, with `think` mapped to that model's own reasoning control.
        import infer
        d = infer.chat(body, timeout=timeout)
        out = (d.get("message") or {}).get("content") or ""
        # HOW THE CALL ENDED, KEPT FOR THE CALLER (t238). This returned the content and
        # nothing else, so a reply cut off at num_predict and a reply that was simply
        # malformed arrived as the same empty string -- and the loop told a model that had
        # run out of room "your last reply could not be parsed", discarded its reasoning,
        # and asked it to think again from zero at the same budget. ask.last is read by
        # the loop immediately after each call; the return value is unchanged so no
        # other caller moves.
        ask.last = {"done_reason": d.get("done_reason"),
                    "thinking": (d.get("message") or {}).get("thinking") or "",
                    "content": out, "eval_count": d.get("eval_count", 0)}
        ask.reasoning.append(ask.last.get("thinking") or "")
        _tel("coder.py/native", model, time.time() - t, max_tokens, d.get("eval_count"),
             d.get("prompt_eval_count"), d.get("done_reason"), timeout,
             (d.get("load_duration") or 0) / 1e9)
        if raise_on_length and d.get("done_reason") == "length":
            window = (body.get("options") or {}).get("num_ctx")
            if window_full(d.get("prompt_eval_count"), d.get("eval_count"), window):
                raise WindowFull(d.get("eval_count", 0), time.time() - t, dict(ask.last),
                                 d.get("prompt_eval_count"), window)
            raise Truncated(d.get("eval_count", 0), time.time() - t, dict(ask.last))
        return out.strip(), d.get("eval_count", 0), time.time() - t

    # THE GATEWAY IS A DIFFERENT SERVER, NOT A DIFFERENT MODEL (2026-09-03). This path
    # speaks the OpenAI shape to vLLM, but the weights are the same and so are their authors'
    # recommended values. It sent temperature 0 -- greedy, forbidden by Qwen -- purely
    # because nobody had joined this body to the contract.
    _samp = _gateway_sampling(model)
    body = {"model": model, "max_tokens": max_tokens, **_samp,
            "chat_template_kwargs": {"enable_thinking": think},
            "messages": [{"role": "system", "content": system_prompt()},
                         {"role": "user", "content": prompt}]}
    req = urllib.request.Request(GATEWAY, json.dumps(body).encode(),
                                 {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as f:
        d = json.load(f)
    out = d["choices"][0]["message"]["content"]
    _full = out
    ask.last = {"done_reason": ((d.get("choices") or [{}])[0] or {}).get("finish_reason"),
                "thinking": _full.split("</think>", 1)[0] if "</think>" in _full else "",
                "content": "", "eval_count": (d.get("usage") or {}).get("completion_tokens")}
    ask.reasoning.append(ask.last.get("thinking") or "")
    if "</think>" in out:
        out = out.split("</think>", 1)[1]
    _tel("coder.py/gateway", model, time.time() - t, max_tokens,
         (d.get("usage") or {}).get("completion_tokens"),
         (d.get("usage") or {}).get("prompt_tokens"),
         ((d.get("choices") or [{}])[0] or {}).get("finish_reason"), timeout, None)
    if raise_on_length and ask.last.get("done_reason") == "length":
        ask.last["content"] = out.strip()
        raise Truncated(d["usage"]["completion_tokens"], time.time() - t, dict(ask.last))
    return out.strip(), d["usage"]["completion_tokens"], time.time() - t


ask.last = {}
ask.reasoning = []   # every call's thinking, in order -- written to the task session (t386)


# ------------------------------------------------------------ resuming a truncated attempt
#
# Jas, 2026-09-14: "How come every retry resets the timer? Is there no way that the model
# can resume from the point of failure and just pivot versus starting from scratch?"
#
# There was not. A reply cut off at num_predict was treated exactly like a malformed one:
# the prompt was rebuilt from the base plus one line of error, the reply was thrown away,
# and the model -- at think: high -- reasoned from zero into the same ceiling. On
# 2026-09-14 attempts ran to the full 16,000 tokens back to back (23-43 minutes each on
# bf16), which is the reset he was watching.
#
# THE PIVOT. When a call ends done_reason=length and nothing parseable came back, the
# reasoning is not garbage -- it is work that ran out of room. So it is handed back,
# whole, with reasoning switched OFF and a small budget, and the model is asked for the
# answer it was on its way to. That call costs a few hundred tokens instead of another
# sixteen thousand. It does not count as an attempt: it is the end of the one in progress.
#
# It is not a guarantee. If the model had not reached a decision, it will say something
# unusable, and that is recorded as the attempt failing AFTER a resume -- distinguishable in
# the trail from a model that never got the chance.
class Truncated(Exception):
    """The model stopped because it hit num_predict, not because it finished.

    Raised by ask(raise_on_length=True) so the loop cannot mistake a cut-off for a bad
    answer: the 2026-09-08 dispatches reported "unusable (no JSON object in the reply,
    13954 tok)" for exactly this. Carries everything a resume needs.
    """

    def __init__(self, tokens, seconds, last):
        super().__init__(f"truncated at {tokens} tokens after {seconds:.0f}s")
        self.tokens, self.seconds, self.last = tokens, seconds, last


# THE SECOND TIER. A resume hands back the reasoning and asks for the answer. If that fails
# -- the model had not reached a decision yet -- it genuinely needs more room, and the NEXT
# attempt gets this budget instead of the same 16,000 it just ran out of. Clamped at call
# time to what the window can hold beside the prompt, and the clamp is announced.
def _fail_sig(t):  # Jas 2026-09-27: a repeat of the same failure ends the run
    return t.get("outcome"), re.sub(r"\d+", "N", str(t.get("detail", "")))[:400]

class WindowFull(Truncated):
    """It stopped because the CONTEXT WINDOW filled, not because the output budget ran out.

    Ollama reports both as done_reason="length". p20 on 2026-09-23: a 52,538-token prompt
    (coder.py, 124 KB, pasted whole) plus 12,996 tokens of output = 65,534 of a 65,536 window.
    The loop read that as an output cut-off and retried with MAX_TOKENS_RETRY -- 32000 -- and
    the next attempt stopped at 52,163 + 13,371 = 65,534 again. Raising the budget turns the
    wrong knob: it cannot add room that the window does not have. The prompt is the problem,
    so the fix belongs upstream (the brief must name the symbols to edit, see
    workspace/failure-schema.json), and the loop must stop and say so rather than spend
    attempts proving it.
    """

    def __init__(self, tokens, seconds, last, prompt_tokens, window):
        super().__init__(tokens, seconds, last)
        self.prompt_tokens, self.window = prompt_tokens, window


def window_full(prompt_tokens, eval_tokens, window, slack=64):
    """Did prompt + output reach the window? False when any number is unknown -- an unknown
    is not evidence, and the ordinary truncation path still handles the reply."""
    try:
        return bool(window) and int(prompt_tokens) + int(eval_tokens) >= int(window) - slack
    except (TypeError, ValueError):
        return False


MAX_TOKENS_RETRY = 32000

# THE FIRST ATTEMPT'S OUTPUT BUDGET, t477 (Jas, 2026-09-30: "Yes raise it"). 16,000 is the
# floor a prompt must leave room for (prompt_fits refuses below it); 32,000 is given whenever
# the window holds it beside the prompt. The 09-12 revert to 16,000 was right for a 32,768
# window, where 32,000 left 768 tokens for the prompt. The window is 65,536 now, and p53's
# attempts 2 and 3 were both cut off at 16,000 while gemma4:26b wrote a whole coder.py.
MIN_TOKENS_FIRST = 16000


FILE_REPLY_RESERVE = 32000    # a requested file is shown while a reply this big still fits beside it
OUTPUT_RESERVE_TOKENS = 4096   # t1009/t1441: tokens held out of the output budget so a reply has headroom
TARGET_EXCERPT_CAP = 60000    # the target suite is shown beside the prompt, so it gets its own cap, NOT EXCERPT_CAP (which is 260000, too big for a file shown beside a prompt; a 4000-line target numbers to about 97779 chars, under it, so EXCERPT_CAP shows it whole)


def first_budget(prompt_chars, window, margin=2000):
    """EVERY TOKEN THE WINDOW HOLDS BESIDE THIS PROMPT (Jas, 2026-10-06: "give coder.py the max
    budget of whatever model it uses"). It was capped at MAX_TOKENS_RETRY (32,000), sized for
    Gemma/Q16 on a 65,536 window; Swift serves 262,144 and was being held to an eighth of it.
    The estimate is prompt_fits()'s pessimistic 3 chars/token. Floor MIN_TOKENS_FIRST; an unknown
    window gets the floor -- the raise is only for a window we can see has room."""
    if not window:
        return MIN_TOKENS_FIRST
    room = int(window) - int(prompt_chars / 3.0) - margin
    return max(room, MIN_TOKENS_FIRST)


def target_context(root, rel, budget, window):
    """Show the target suite in the starting context.

    p332-t768: the target's only route to the prompt was a file request, and a request can
    be refused; the model then had to satisfy assertions it was never shown.
    """
    p = Path(root) / rel
    if not p.is_file():
        return "", budget
    text = p.read_text(encoding="utf-8", errors="replace")
    body = "--- " + rel + " ---\n" + numbered(text, (), cap=TARGET_EXCERPT_CAP)
    total = len(text.splitlines())
    if len(body.splitlines()) < total:
        shown = max(0, len(body.splitlines()) - 1)
        missing = max(0, total - shown)
        body += (f"\nTRUNCATED: the target suite has {total} lines; {missing} of them "
                 "are not shown. Do not conclude the missing part is absent.")
    fits, est = prompt_fits(len(body), min(budget, FILE_REPLY_RESERVE), window)
    if not fits:
        return (f"--- {rel} ---\nThe target suite is not shown: it is too large to show "
                f"beside the prompt (~{est} tokens with a {window}-token window, leaving "
                f"less than the {FILE_REPLY_RESERVE}-token reply reserve). "
                "TRUNCATED: the file's source is not shown, so do not conclude its "
                "assertions are absent."), budget
    if not window:
        return body, budget
    return body, min(budget, first_budget(len(body), window))


def fit_output_budget(prompt_tokens, window, requested) -> int | None:
    """The output budget that fits beside this prompt, leaving OUTPUT_RESERVE_TOKENS (t1009/t1441).

    An unknown window is not a refusal and not a clamp: the caller's convention is that
    an unknown window is not evidence. A request that already fits is returned unchanged.
    Otherwise return the largest budget that leaves the reserve, and None when that
    remainder is below 256 tokens -- a token-sized answer is refused, not sent.
    """
    if not window:
        return requested
    room = int(window) - int(prompt_tokens) - OUTPUT_RESERVE_TOKENS
    if requested <= room:
        return requested
    return room if room >= 256 else None


def slice_fits(prompt_chars, slice_chars, budget, window):
    """(fits, est_tokens, new_budget) for serving `slice_chars` more of an opened file (t1004).

    Fits while a FILE_REPLY_RESERVE-token reply (or the smaller budget we have) still fits beside
    the grown prompt -- the new-file rule -- and then the reply budget shrinks to the room left,
    so the prompt pays for what it was shown. An unknown window is not evidence: it fits and the
    budget is left alone."""
    if not window:
        return True, int((prompt_chars + slice_chars) / 3.0), budget
    total = prompt_chars + slice_chars
    fits, est = prompt_fits(total, min(budget, FILE_REPLY_RESERVE), window)
    if not fits:
        return False, est, budget
    return True, est, max(0, min(budget, first_budget(total, window)))


def served_window(model, profile_num_ctx):
    """The window `model` is actually served with. Ollama takes num_ctx per request, so the
    coder profile's value is what it gets; a fixed-window engine (vLLM: Swift) serves the window
    it was started with, whatever num_ctx says -- infer.context reads it from SERVERS.json."""
    try:
        import infer
        adapter, _ = infer.resolve(model)
        if adapter.__class__.__name__ != "Ollama":
            return adapter.context(model) or profile_num_ctx
    except Exception as e:                                      # noqa: BLE001
        _skip("served window unknown; using the coder profile's num_ctx", e)
    return profile_num_ctx

FINISH_TOKENS = 4000
FINISH_TAIL = """

YOU RAN OUT OF ROOM BEFORE ANSWERING. Your reply was cut off at the output limit while you
were still reasoning. That reasoning is NOT lost -- it is below, {shown}. Do not start over
and do not reason again. Using what you already worked out, reply now with the answer
itself: ONE JSON object in the required shape, and nothing else.

=== YOUR REASONING SO FAR ===
{reasoning}
=== END OF YOUR REASONING ===
"""


def retry_budget(convo, num_ctx=None):
    """Every token the window holds beside this prompt (as first_budget), floor 4000 -- and said
    when that is less than MAX_TOKENS_RETRY."""
    num_ctx = num_ctx or NUM_CTX
    room = int(num_ctx - len(convo) / 3.5 - 2000)
    if room >= MAX_TOKENS_RETRY:
        return room
    say(f"\n  (retry budget clamped to {max(room, 4000)} tokens: the prompt leaves no room "
        f"for {MAX_TOKENS_RETRY} in a {num_ctx}-token window)")
    return max(room, 4000)


def finish_truncated(convo, model, last, ask_fn=None, num_ctx=None):
    """Ask for the answer a truncated attempt was reasoning towards.

    Returns (raw, tokens, seconds, note). The reasoning is passed WHOLE when it fits the
    window. When it cannot -- the prompt plus 16,000 tokens of reasoning exceeds num_ctx
    -- the BEGINNING is dropped, because the end is where the decision was forming, and
    the prompt says in words how much was dropped. A declared cut is a constraint; a
    silent one is the defect this week is spent removing.
    """
    ask_fn = ask_fn or ask
    reasoning = ((last.get("thinking") or "") + "\n" + (last.get("content") or "")).strip()
    num_ctx = num_ctx or NUM_CTX
    # ~3.5 chars per token for this material; leave room for the answer and a margin.
    room_chars = int((num_ctx - FINISH_TOKENS) * 3.5 * 0.9) - len(convo) - len(FINISH_TAIL)
    if room_chars <= 0:
        return "", 0, 0.0, "no room left in the window to hand the reasoning back"
    if len(reasoning) <= room_chars:
        shown, note = "in full", f"resumed from all {len(reasoning)} chars of its reasoning"
        kept = reasoning
    else:
        dropped = len(reasoning) - room_chars
        kept = reasoning[dropped:]
        shown = (f"FROM CHARACTER {dropped} OF {len(reasoning)} -- the first {dropped} "
                 f"characters did not fit the context window and are NOT shown")
        note = (f"resumed from the last {len(kept)} of {len(reasoning)} chars of its "
                f"reasoning ({dropped} dropped to fit num_ctx {num_ctx}, declared to it)")
    prompt = convo + FINISH_TAIL.format(shown=shown, reasoning=kept)
    raw, tok, secs = ask_fn(prompt, model, think=False, max_tokens=FINISH_TOKENS)
    return raw, tok, secs, note



def job_label(argv):
    """The branch label another coder.py process is working under, from its argv alone.

    Mirrors main(): --task is its own label; --brief and --brief-file both hash the brief
    CONTENT, so the same brief arriving through different doors is recognised as one job.
    """
    def val(flag):
        return argv[argv.index(flag) + 1] if flag in argv and argv.index(flag) + 1 < len(argv) \
            else None
    if val("--task"):
        return val("--task")
    text = val("--brief")
    if val("--brief-file"):
        try:
            text = Path(val("--brief-file")).read_text(encoding="utf-8")
        except OSError:
            return None
    return ("brief-" + hashlib.sha256(text.encode()).hexdigest()[:8]) if text else None


def running_twin(label, files, model):
    """pid of another LIVE coder.py doing the same job on the same model, or None.

    Same job = same label AND same --files. Read from /proc, never by matching a name:
    `pgrep -f` matches the shell running it (L-105). A zombie is finished, not running.
    """
    want_files = sorted(files or [])
    # MY OWN ANCESTORS ARE NOT TWINS. The first version matched any process with an argv
    # element ending in coder.py -- which includes the `timeout`, `nohup` or `bash -c` that
    # LAUNCHED this one, so every dispatch run under a wrapper refused itself, even with
    # nothing else running. Found by the fixture's "after the first finishes" case. Same
    # family as L-105: a matcher that can see the thing doing the looking.
    ancestors, pid = set(), os.getpid()
    while pid > 1 and pid not in ancestors:
        ancestors.add(pid)
        try:
            pid = int((Path(f"/proc/{pid}/stat").read_text().rsplit(") ", 1)[1].split())[1])
        except (OSError, ValueError, IndexError):
            break
    for d in Path("/proc").glob("[0-9]*"):
        try:
            pid = int(d.name)
            if pid in ancestors:
                continue
            argv = [x for x in (d / "cmdline").read_bytes().decode().split("\0") if x]
            # An INTERPRETER running coder.py as its script -- argv[1] -- and nothing else.
            # A shell or wrapper merely MENTIONING coder.py is not a dispatch.
            if not (len(argv) >= 2 and Path(argv[0]).name.startswith("python")
                    and argv[1].endswith("coder.py")):
                continue
            if (d / "stat").read_text().rsplit(") ", 1)[1].split()[0] == "Z":
                continue
            if "--model" in argv and argv[argv.index("--model") + 1] != model:
                continue
            # ANOTHER CLONE IS ANOTHER JOB (E1, 2026-10-27). Two runs in different clones cannot race
            # for one branch, and a GPU race is the model lease's to stop. Matching across clones made
            # suites sharing a brief refuse each other once four lanes swept at once: seven red suites.
            if os.path.realpath(d / "cwd") != os.path.realpath(os.getcwd()):
                continue
            i = argv.index("--files") if "--files" in argv else None
            theirs = []
            if i is not None:
                for x in argv[i + 1:]:
                    if x.startswith("--"):
                        break
                    theirs.append(x)
            if sorted(theirs) == want_files and job_label(argv) == label:
                return pid
        except (OSError, ValueError, IndexError, UnicodeDecodeError) as e:
            _skip("a process's command line could not be read", e)
            continue
    return None


def twin_refusal(twin_pid, force, label=None, model=None):
    """Refuse a duplicate dispatch unless --force says the twin is stale.

    Pure builtins: the oracle can exec just this function (t1163). It keeps the
    pid in the message even on the override, so the operator's decision is
    auditable.
    """
    refused = twin_pid is not None and not force
    if twin_pid is None:
        return False, ""
    if refused:
        # t1130: the identity and the recovery line are back -- the row a reader checks must say
        # WHICH job and model were refused, and how to recover ("kill it first").
        who = f" ({label}, model {model})" if (label or model) else ""
        return True, (f"REFUSED: pid {twin_pid} is already running this job{who}. "
                      "A second copy races it for the GPU and, with --apply, for "
                      "the same branch. Wait for it, kill it first, or pass --force if "
                      "you have confirmed the twin is stale.")
    return False, f"proceeding past pid {twin_pid} with --force"




def revert(root, baseline):
    """Put every file back to its baseline. Returns the paths it had to restore.

    Three branches told the model "That change has been REVERTED" and only one of them did
    it -- the one added last, whose author (me) never applied the same fix to the two that
    already existed. The message was false, and the consequence is worse than a wrong
    message: the next attempt anchors against the listing it was shown while the file on
    disk has moved, so a correct anchor is reported "not found". That is the REFUSED bar on
    the Pareto.

    Found by the env3 probe on 2026-08-26, whose whole mission was to make the harness state
    something untrue.
    """
    # AN ASYMMETRY THAT WAS LOOKED AT AND DELIBERATELY LEFT ALONE (2026-08-27, probe round
    # 31). Callers pass the bare `baseline` here while `changed()` and `diff_of()` get
    # {**baseline, **pre}, so a file the snapshot's extension walk missed would have no entry
    # here and could not be put back -- while the model is told "That change has been
    # REVERTED".
    #
    # No reachable failure exists in this repository: `apply_ops` refuses a path that does not
    # already exist, and every editable file type here is in SOURCE_EXT (checked, including
    # workspace/tasks, which reverts correctly). Passing the merged dict would be free, EXCEPT
    # that test_coder_guards.py asserts the literal string `revert(root, baseline)` follows
    # each of the three REVERTED messages -- a source-level guard written because three
    # branches once claimed a revert and one performed it. Changing the call would mean
    # loosening that guard to fix nothing observable, so the guard wins.
    #
    # If SOURCE_EXT ever stops covering an editable type, this becomes real. That is the
    # trigger to revisit.
    restored = []
    for rel, text in baseline.items():
        p = root / rel
        try:
            if p.is_file() and p.read_text(encoding="utf-8", errors="replace") != text:
                p.write_text(text, encoding="utf-8")
                restored.append(rel)
        except OSError as e:
            _skip(f"{rel} could not be restored", e)
            continue
    return restored


def preserve_attempt_bytes(root, rels, dest):
    """Copy the applied sandbox bytes to `dest`, byte for byte, before they are reverted.

    t1123. A TESTS-FAILED attempt applied cleanly, and the oracle compares the file it
    produced against the expected file BYTE FOR BYTE -- but the branch reverts the sandbox
    and then deletes it, so the bytes the model actually wrote exist only in the failure
    record's prose. Two production attempts on t1123 were accepted-not-written and no branch
    carried the change; neither could be diffed afterwards because the diff's subject had
    been destroyed. This is the RCA skill's Rule 4 at the endpoint: the observation is taken
    here or it is not taken at all.

    `shutil.copyfile` and not `copy2`: bytes are the contract, and metadata is noise that
    makes two preservations of the same content differ. A rel absent from `root` is SKIPPED,
    never invented -- the caller is told which rels it got back, in the order given. The
    source is opened read-only and never rewritten.
    """
    preserved = []
    for rel in rels:
        src = Path(root) / rel
        if not src.is_file():
            continue
        dst = Path(dest) / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)
        preserved.append(rel)
    return preserved


def record_baseline_output(outputs, dest):
    """Write each failing suite's captured output to `<dest>/<suite>.log`; return the paths.

    The refusal path (REFUSING TO START -- the sandbox baseline is not what is declared)
    used to discard `run_suites`'s per-suite output and delete the sandbox, so the record of
    a refusal was the two SUITE NAMES it printed. On 2026-10-24 that cost nine hours of
    production: eleven dispatches refused on `test_manager_note.py` and
    `test_manager_outcomes.py`, both of which pass in a clean sandbox, and nothing on disk
    said why the baseline disagreed. The count is in the message, the cause was in `out`,
    and `out` was the value thrown away.

    Same file names and the same directory convention as scripts/run_tests.py's
    `workspace/run-logs/known-failures/`, so a reader of either finds a suite's output in one
    place. An empty capture is written as a marker rather than a zero-byte file: a zero-byte
    log reads as "nothing went wrong", which is the opposite of what it means.
    """
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    written = []
    for suite, text in sorted(outputs.items()):
        p = dest / (Path(suite).name + ".log")
        p.write_text((text or "(no output captured for this suite)\n"), encoding="utf-8")
        written.append(p)
    return written


def parse(raw):
    """(object, None) or (None, why). ONE EXTRACTOR: scripts/jsonx.py (t259, 2026-10-01).

    coder.py ran its own balanced-brace scanner -- correct, but a second copy of what jsonx
    exists to be the only copy of. So when t400 taught jsonx to repair invalid escapes, the
    main dispatch path never got it: p19 failed three attempts on 'Invalid \\escape' a day
    after the fix "landed" (t474). Now the repair applies here too, and the object chosen is
    the first one carrying `ops` or `need` -- an example object in the model's reasoning no
    longer gets read as its answer.
    """
    sys.path.insert(0, str(REPO / "scripts"))
    import jsonx
    s = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip(), flags=re.M).strip()
    regions, parsed, saw_brace = jsonx.scan(s)
    if not saw_brace:
        return None, "no JSON object in the reply"
    if not any(isinstance(o.get("ops"), list) or isinstance(o.get("need"), list) for o in parsed):
        # LAST RESORT, MEASURED (2026-10-01): a list left open before the next key, or closed
        # where an object should close, was the largest UNPARSEABLE class after the escapes.
        fixed = jsonx.repair_brackets(s)
        if fixed:
            r2, p2, _ = jsonx.scan(fixed)
            if any(isinstance(o.get("ops"), list) or isinstance(o.get("need"), list) for o in p2):
                regions, parsed = r2, p2
    if not regions:
        return None, "unterminated JSON object"
    for o in parsed:
        if isinstance(o.get("need"), list) and o["need"]:
            return o, None          # a request for files, not a patch
        if isinstance(o.get("ops"), list) and o["ops"]:
            for x in o["ops"]:
                if not isinstance(x, dict) or ("from" not in x and "old" not in x):
                    return None, ("each op needs either 'old' (anchored) or "
                                  "'from'/'to' (line range)")
            return o, None
    if parsed:
        return None, "no ops"
    # Nothing parsed: say WHY, in the words that name the fault (t261). A Python-style dict
    # ('key': ...) fails with "Expecting property name enclosed in double quotes", and a retry
    # that only says "reply with one JSON object" gives the model no reason to change it.
    try:
        json.loads(jsonx._repair_invalid_escapes(regions[0]))
        err = "invalid JSON"
    except (json.JSONDecodeError, ValueError) as e:
        err = f"invalid JSON: {e}"
    if "enclosed in double quotes" in err or re.match(r"\{\s*'", regions[0]):
        err += (" -- the reply is a Python dict, not JSON: every key and string must use "
                "DOUBLE quotes (\"ops\", not 'ops')")
    return None, err


def _indent_of(line):
    return line[:len(line) - len(line.lstrip())]


def _reindent(new, replaced_line):
    """Shift `new` so its first line sits at the indentation of the line it replaces.

    MEASURED, not guessed. gemma4:26b's line-range edits fail on indentation, and it is the
    LAST thing three of the fourteen held chunks did before giving up -- p41 chatui.py:782,
    p13 duo_verify.py:160, p17 feedback.py:1464, every one "unindent does not match any
    outer indentation level". The model names the right range and supplies the right code at
    the wrong depth, because a numbered listing shows it the text but the column is easy to
    lose. py_syntax() catches the break, so nothing corrupt was ever written -- the cost is
    an attempt burned and, under the one-attempt policy, a task wrongly parked.

    The shift is UNIFORM: every line moves by the same delta, so relative structure inside
    the block is preserved exactly as the model wrote it. If the model got the internal
    shape wrong this does not rescue it, and py_syntax will still refuse -- which is the
    intended division. This fixes a transcription slip, not a reasoning error.

    A no-op when the model already got it right, which is the common case.
    """
    lines = new.split("\n")
    first = next((i for i, l in enumerate(lines) if l.strip()), None)
    if first is None:
        return new                      # nothing but blanks; there is no indentation to fix
    want, got = _indent_of(replaced_line), _indent_of(lines[first])
    if want == got:
        return new
    # Only shift when the block is uniformly deeper or shallower than the target. A mixture
    # of tabs and spaces is not something to normalise silently -- that is a real defect in
    # the emitted code and py_syntax should be the one to say so.
    if ("\t" in want) != ("\t" in got):
        return new
    if got and not all(l.startswith(got) for l in lines if l.strip()):
        return new                      # the first line is not the shallowest; leave it alone
    return "\n".join(want + l[len(got):] if l.strip() else l for l in lines)


def _string_spans(text):
    """Find (start, end) line numbers for all strings in `text`.

    Returns (None, err) if tokenization fails.
    """
    import io
    import tokenize
    spans = []
    try:
        for t in tokenize.generate_tokens(io.StringIO(text).readline):
            if t.type == tokenize.STRING:
                spans.append((t.start[0], t.end[0]))
        return spans, None
    except (tokenize.TokenError, IndentationError, SyntaxError) as e:
        return None, str(e)


def _match_ignoring_space(text, old):
    """(start, end) of the ONE region matching `old` ignoring per-line whitespace, or None.

    Returns None when there is no match AND when there is more than one -- ambiguity is a
    refusal, not a thing to resolve by picking. Compares line by line on `.strip()`, so it
    forgives indentation depth and trailing spaces and nothing else: a differing character
    inside the line is still a miss.
    """
    want = [l.strip() for l in old.split("\n")]
    while want and not want[-1]:
        want.pop()
    if not want or not any(want):
        return None
    lines = text.split("\n")
    stripped = [l.strip() for l in lines]
    starts = [i for i in range(len(lines) - len(want) + 1)
              if stripped[i:i + len(want)] == want]
    if len(starts) != 1:
        return None
    i = starts[0]
    s = sum(len(l) + 1 for l in lines[:i])
    e = s + sum(len(l) + 1 for l in lines[i:i + len(want)]) - 1
    return s, e


def fill_paths(ops, files):
    """Ops with no path, when the brief allows exactly ONE file, get that file. -> count filled.

    p48 (2026-09-24): two of gemma4:26b's five attempts sent edits with path None, and each was
    REFUSED as "path refused: ''" -- ten minutes and 16k tokens apiece -- although the brief
    permitted a single file, so the edit could only have meant that one. With two or more files
    the target is genuinely ambiguous and the refusal stands.
    """
    if len(files or []) != 1:
        return 0
    n = 0
    for op in ops or []:
        if not isinstance(op, dict):
            continue
        got = str(op.get("path") or "").strip()
        # ...and a bare file name that IS the one allowed file's name (p50 attempt 5 sent
        # "coder.py" for projects/local-inference/coder.py and was refused "no such file").
        if not got or (got != files[0] and "/" not in got and Path(files[0]).name == got):
            op["path"] = files[0]
            n += 1
    return n


def in_scope(rel, files) -> bool:
    """Is rel inside the brief's declared files? (t1260)"""
    names = [str(f).strip() for f in (files or [])]
    want = (rel or '').strip()
    if not want:
        return False
    if want in names:
        return True
    base = want.rsplit('/', 1)[-1]
    return '/' not in want and any(str(n).rsplit('/', 1)[-1] == base for n in names)



def range_block(root, rel, lo, hi):
    """Verbatim lines of rel at lo..hi inclusive with exact leading whitespace
    and the file's true line numbers (sliced idiom), plus one adjacent line
    on each side; None if the file is missing or the range exceeds the count."""
    try:
        p = root / rel
        if not p.is_file():
            return None
        lines = p.read_text(encoding="utf-8").splitlines()
        lo, hi = int(lo), int(hi)
        if not (1 <= lo <= hi <= len(lines)):
            return None
        start = max(1, lo - 1)
        end = min(len(lines), hi + 1)
        out = [f"{i:5d}  {lines[i-1]}" for i in range(start, end + 1)]
        return "\n".join(out)
    except (OSError, UnicodeDecodeError, TypeError, ValueError) as e:
        # A context block that cannot be read (or a from/to that is not a line number) is only a
        # missing hint for the retry; say so rather than hide it.
        print(f"  NOTE: range_block could not read {rel}: {e}", file=sys.stderr)
        return None


def range_hint(lines, s, e, a, b, new) -> str:
    """Give the model a way to find the right lines after a range is refused.

    The line-range shape is located by line number, so the refusal has to say what is at the
    lines the model named and, if it supplied text, where that text already lives in the file.
    """
    parts = [
        f"The string's own first line is {lines[s - 1]!r} and its last is {lines[e - 1]!r}."
    ]
    parts.append(f"The lines you asked to replace (lines {a}-{b}) are:")
    parts.extend(lines[a - 1:b])
    needle = ""
    for line in new.splitlines():
        if line.strip():
            needle = line.strip()
            break
    if needle:
        matches = difflib.get_close_matches(needle, lines, n=2, cutoff=0.6)
        if matches:
            parts.extend(
                f"The text you are writing appears at line {lines.index(m) + 1}: {m}"
                for m in matches
            )
        else:
            parts.append("no line in this file matches the text you are writing")
    return "\n".join(parts)


def apply_ops(root, ops):
    """All or nothing. A half-applied change is a state nobody asked for.

    Two shapes. The anchored replace is exact and safe and fails constantly on unfamiliar
    code: eleven Kotlin attempts across three runs, nearly all `anchor-not-found`, with the
    model correctly identifying `private fun play(s: Session) {` and then failing to
    reproduce the block under it. That is transcription, not reasoning (`t143`, `t145`).

    The line-range shape removes the transcription entirely -- the model already sees
    numbered listings, so it names a range and supplies only the new text. Ranges are applied
    from the BOTTOM UP so an earlier edit cannot shift the line numbers of a later one.
    """
    originals = {}
    ops = sorted(ops, key=lambda o: -(o.get("from") or 0))
    for op in ops:
        rel = (op.get("path") or "").strip()
        if not rel or rel.startswith("/") or ".." in rel:
            return False, f"path refused: {rel!r}"
        if SECRET_RE.search(rel) or rel.startswith("chat/"):
            return False, f"path out of bounds: {rel}"
        p = root / rel
        if not p.is_file():
            return False, f"no such file: {rel}"
        text = p.read_text(encoding="utf-8")

        if op.get("from") is not None:
            lines = text.split("\n")
            a, b = int(op["from"]), int(op.get("to", op["from"]))
            if not (1 <= a <= b <= len(lines)):
                for q, t in originals.items():
                    q.write_text(t, encoding="utf-8")
                return False, (f"lines {a}-{b} are outside {rel}, which has "
                               f"{len(lines)} lines")
            originals.setdefault(p, text)
            # PYTHON ONLY (claude-code review of p234): tokenize reads every file as Python, so a
            # Markdown or JSON range edit would be refused for "cannot check string boundaries".
            spans, terr = _string_spans(text) if p.suffix == ".py" else ([], None)
            if terr is not None:
                for q, t in originals.items():
                    q.write_text(t, encoding="utf-8")
                return False, f"cannot check string boundaries in {rel}: {terr}"
            for (s, e) in spans:
                if (s < a and e >= a) or (s <= b and e > b):
                    for q, t in originals.items():
                        q.write_text(t, encoding="utf-8")
                    return False, (
                        f"lines {a}-{b} cut a multi-line string that runs from line {s} to "
                        f"line {e} in {rel}. A line-range edit replaces whole lines, so the "
                        f"range must cover the whole string (lines {s}-{e}) or not cross it.\n"
                        + range_hint(lines, s, e, a, b, op.get("new", ""))
                    )
            new = _reindent(op.get("new", ""), lines[a - 1])
            p.write_text("\n".join(lines[:a - 1] + new.split("\n") + lines[b:]),
                         encoding="utf-8")
            continue

        old = op.get("old", "")
        n = text.count(old)
        if n == 0:
            # ONE RETRY, ON WHITESPACE ONLY, AND ONLY IF IT IS UNAMBIGUOUS.
            # The measured failure is transcription: the model identifies the right line and
            # reproduces its indentation or trailing spaces slightly wrong (p13 duo_verify,
            # attempt 3 -- the hint's own "closest actual line" differed from the anchor by
            # whitespace alone). Refusing that burns an attempt and, under the one-attempt
            # policy, parks a task whose brief was correct.
            # It is safe because the match must still be UNIQUE after normalising, and the
            # replacement is applied to the real span from the real file. If normalising
            # makes two regions look alike, that is ambiguity and it is refused exactly as
            # before -- widening this to "closest match" would be the version that silently
            # edits the wrong place.
            hit = _match_ignoring_space(text, old)
            if hit is not None:
                s, e = hit
                originals.setdefault(p, text)
                p.write_text(text[:s] + _reindent(op.get("new", ""), text[s:e].split("\n")[0])
                             + text[e:], encoding="utf-8")
                continue
        if n != 1:
            for q, t in originals.items():
                q.write_text(t, encoding="utf-8")
            # Name the NEAREST real line. "anchor not found" is true and unusable; the
            # model has no way to tell whether it mistyped an indent, invented the line, or
            # is looking at a region the excerpt never showed it. Three runs died here
            # saying nothing more than "not found".
            first = (old.strip().splitlines() or [""])[0]
            near = difflib.get_close_matches(first, text.splitlines(), n=2, cutoff=0.5)
            hint = ("  Closest actual line(s) in that file:\n"
                    + "\n".join(f"    {c!r}" for c in near)) if near else \
                   "  Nothing in that file resembles it -- you may be anchoring on a " \
                   "region the excerpt did not show."
            return False, (f"anchor {'not found' if n == 0 else f'matched {n} times'} "
                           f"in {rel}.\n{hint}")
        originals.setdefault(p, text)
        p.write_text(text.replace(old, op.get("new", ""), 1), encoding="utf-8")
    return True, f"{len(ops)} op(s) applied"


def syntax_hint(e):
    """What to fix, by the KIND of syntax error. It always said "the indentation must match",
    and on 2026-09-29 (t469) the error was `unterminated string literal` at line 786: the hint
    sent Gemma to re-indent a line whose real fault was an unclosed quote, and the retry broke it
    the same way. A hint that names the wrong fault is worse than none."""
    msg = (getattr(e, "msg", "") or "").lower()
    if isinstance(e, IndentationError) or "indent" in msg:
        return ("A line-range edit replaces whole lines, so the indentation must match the block "
                "you are replacing into.")
    if "unterminated" in msg or "eol while scanning" in msg or "eof while scanning" in msg:
        return ("A string is not closed: check the quotes on that line, and a triple-quoted string "
                "the edit opened or cut in half. Inside a string, escape the quote character itself.")
    if "was never closed" in msg or "unmatched" in msg or "does not match opening" in msg:
        return ("A bracket is not balanced: the edit's lines must close every (, [ and { they open, "
                "and must not close one opened outside the range you replaced.")
    return ("Read the line it names and the reason above; the edit's lines must be valid Python on "
            "their own, with the indentation of the block you are replacing into.")


def py_syntax(root, ops):
    """(ok, message) — do the Python files this patch touched still parse?

    The Kotlin path has had `compiles()` since 2026-08-25; Python has had nothing, so a patch
    that broke indentation went all the way to the suites and came back as an import
    traceback three frames deep. qwen3.8 produced exactly that twice in one run:

        File "coder.py", line 978
            shown.append(rel)
        IndentationError: unindent does not match any outer indentation level

    Line-range edits replace whole lines, so the model must supply the indentation itself --
    which makes this the failure mode that shape invites. A syntax error is not a test
    failure and should not be reported as one: the model needs the line and the reason, not a
    traceback from a suite that never got to run.

    Fast on purpose: parsing is milliseconds, so it runs before the suites rather than
    instead of them.
    """
    import ast
    for rel in sorted({op.get("path", "") for op in ops}):
        if not rel.endswith(".py"):
            continue
        f = root / rel
        if not f.is_file():
            continue
        try:
            ast.parse(f.read_text(encoding="utf-8", errors="replace"), filename=rel)
        except SyntaxError as e:
            return False, (f"{rel} line {e.lineno}: {e.msg}\n"
                           f"    {(e.text or '').rstrip()}\n"
                           "    The edit left the file unparseable. " + syntax_hint(e))
    return True, ""


def library_gate(root, ops, before):
    """(ok, message) -- would the commit gate refuse what this patch wrote?

    ACCEPTED-NOT-WRITTEN, 31 TIMES. The pre-commit ratchet (library.py verify --staged) runs
    the library's BLOCK checks, and it ran only inside write_branch(), AFTER this loop had
    accepted the attempt. A patch that went green and added `except ...: continue` was judged
    good, refused at the commit, and dropped -- p18 on 2026-09-23 lost a correct fix that way,
    and the model was never told, so the next dispatch could only repeat it. The same
    function the gate uses (library_checks.new_blocks) runs here, before the suites, and a
    finding is fed back like a syntax error: the attempt is spent, the work is not.
    """
    try:
        sys.path.insert(0, str(REPO / "scripts"))
        import library_checks
        from library import EXT, SKIP_PARTS
    except Exception as e:                                          # noqa: BLE001
        # A gate that cannot load must not pass everything silently -- but it must not
        # fail every attempt either, since the commit gate still runs. Say so, loudly.
        say(f"\n  (library gate NOT RUN in the loop: {type(e).__name__}: {e}; the commit "
            "gate will still check)")
        return True, ""
    found = []
    for rel in sorted({(op.get("path") or "").strip() for op in ops if op.get("path")}):
        if Path(rel).suffix not in EXT or set(Path(rel).parts) & SKIP_PARTS:
            continue
        f = root / rel
        if not f.is_file():
            continue
        after = f.read_text(encoding="utf-8", errors="replace")
        for cid, line, code in library_checks.new_blocks(rel, before.get(rel), after):
            found.append(f"{rel}:{line}  {cid}  {code.strip()[:160]}")
    if not found:
        return True, ""
    return False, ("The repository's commit gate refuses these NEW findings, so this change "
                   "could not be committed even with every test green:\n  "
                   + "\n  ".join(found)
                   + "\n  Each check id names a defect this repository has paid for "
                     "(scripts/library_checks.py). Write the change without it -- e.g. a "
                     "swallowed exception must be logged or re-raised, a subprocess call needs "
                     "a timeout, a process is found through lane_status, not by name.")


def android_project(root, area):
    """The Android project this task is about, if there is one."""
    if not area:
        return None
    stem = area.lower().replace("-", "")
    for d in (root / "projects").iterdir():
        if not d.is_dir() or not (d / "gradlew").is_file():
            continue
        name = d.name.lower().replace("-", "")
        if stem[:5] in name or name[:5] in stem:
            return d
    return None


def compiles(proj, timeout=900):
    """Does the Kotlin still compile? (ok, the errors the model needs to see)

    This is the oracle app work has been missing. Every Android run before it was judged by
    21 Python suites that pass whatever happens to Kotlin, so ACCEPTED meant nothing.

    Verified by breaking it, not by trusting BUILD SUCCESSFUL (`L-028`): a syntax error in
    the sandbox produces `BUILD FAILED in 780ms` and a line-numbered `e:` for each fault.
    Warm and offline it takes about a second, because the Gradle cache lives in ~/.gradle
    outside the sandbox and is read-only to this.
    """
    env_sh = REPO / "scripts" / "android-env.sh"
    cmd = (f"source {env_sh} >/dev/null 2>&1; "
           f"./gradlew compileDebugKotlin --offline 2>&1")
    try:
        r = subprocess.run(["bash", "-lc", cmd], cwd=str(proj),
                           capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, "the Kotlin compile timed out"
    if r.returncode == 0:
        return True, ""
    errs = [l for l in r.stdout.splitlines() if l.startswith("e: ")]
    return False, "\n".join(errs[:20]) or r.stdout[-1500:]


def suites(root):
    return sorted(f"scripts/tests/{p.name}"
                  for p in (root / "scripts" / "tests").glob("test_*.py"))


def _suite_once(root, s, timeout):
    """One suite, in its OWN TMPDIR, removed afterwards -- run_tests._once's rule (t415).

    2026-10-06: /tmp (a 1M-inode tmpfs) hit 100% of its inodes and every tool on the box failed
    with ENOSPC. ~20 suites mkdtemp() without cleaning up, some holding a whole git clone
    (bridge-test-, trigger-test-), and this baseline runs all ~320 suites on EVERY dispatch with
    no TMPDIR of its own -- so each build left its suites' temp trees behind in /tmp."""
    # ON DISK (2026-10-19): a 4.4 GB suite tree sat in /tmp (RAM) while Swift could not start.
    _home = Path.home() / ".cache" / "hivemind-tmp"; _home.mkdir(parents=True, exist_ok=True)
    tmp = _tempfile.mkdtemp(prefix=f"suite-{Path(s).stem}-", dir=_home)
    try:
        r = subprocess.run([sys.executable, str(root / s)], capture_output=True, text=True,
                           cwd=str(root), timeout=timeout, env=dict(os.environ, TMPDIR=tmp))
    except subprocess.TimeoutExpired:
        return 1, "TIMEOUT"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return r.returncode, (r.stdout + r.stderr)[-1500:] if r.returncode else ""


def run_suites(root, only=None, timeout=420, declared=()):
    """Failing suites in `root`. A surprising failure is CONFIRMED before it is reported.

    A SINGLE RUN WAS TOO LITTLE EVIDENCE TO REFUSE A DISPATCH ON (2026-09-16). The baseline
    guard called this once per suite and treated any non-zero exit as an undeclared failure,
    which refuses the whole dispatch -- and a refusal blocks EVERY chunk on the box, not just
    its own. Measured that day: 8 distinct suites had refused dispatches this way
    (test_coder_guards, test_verdict_expiry, test_enforcement_ratchet, test_dispatch_loop,
    test_futile_task_held, test_coder_brief_file, test_scheduler_commands, and
    test_console_pin_reaches_ask). Each was then run in a REAL sandbox built with this
    module's own EXCLUDE set, and on main, for contrast:

        7 of the 8 PASSED in both. Not one was a sandbox-environment failure.

    They fail only under contention -- several lanes each building a sandbox and running the
    full suite at once. run_tests.py has known this since 2026-09-04 and says so in its own
    docstring: the coder_* suites "run singly they are 136/136", and it retries a failure
    once alone before believing it. That discipline existed in the repository and had never
    been applied to the guard that can halt production.

    So: a failure that is NOT already declared is run once more, alone, before it is
    believed. A pass on the retry is a FLAKE -- recorded to workspace/flaky-tests.log where
    the count accumulates, never used to refuse. A suite that fails twice is reported, which
    is the case the guard exists for and is untouched by this.

    `declared` is the set whose failure is already expected (the ceiling and the
    cannot-run list at baseline time; the baseline itself afterwards). Those are NOT
    retried -- they are allowed to fail either way, and retrying ~31 known-red suites would
    double the slowest phase of every dispatch to learn nothing.
    """
    def bare(n):
        return n.rsplit("/", 1)[-1]
    declared_bare = {bare(d) for d in declared}
    failed, out = [], {}
    # IN PARALLEL ON THE IDLE CORES (Jas, 2026-10-01: "19 of them are usually idle ... the local
    # models max the GPU and only one cpu"). This loop ran ~320 suites one at a time on every
    # dispatch. Same rule as run_tests.py: suites with "coder" in the name drive coder.py
    # themselves and stay serial; the rest run SUITE_JOBS at a time. Results are consumed in
    # the original order, so the retry and the report below are unchanged.
    todo = list(only or suites(root))
    light = [s for s in todo if "coder" not in bare(s)]
    first = {}
    if len(light) > 1 and SUITE_JOBS > 1:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=SUITE_JOBS) as ex:
            first = dict(zip(light, ex.map(lambda s: _suite_once(root, s, timeout), light)))
    for s in todo:
        rc, text = first[s] if s in first else _suite_once(root, s, timeout)
        if not rc:
            continue
        if bare(s) not in declared_bare:
            rc2, text2 = _suite_once(root, s, timeout)
            if not rc2:
                try:
                    with (REPO / "workspace" / "flaky-tests.log").open("a") as f:
                        f.write(f"{time.strftime('%Y-%m-%d %H:%M')} {bare(s)} failed then "
                                f"passed alone in coder.py's baseline -- not refused\n")
                except OSError as e:
                    _skip("flaky-tests.log not written", e)
                continue
            text = text2
        failed.append(s)
        out[s] = text
    return failed, out


SUITE_JOBS = int(os.environ.get("HIVEMIND_SUITE_JOBS") or max(1, min(8, (os.cpu_count() or 2) - 4)))


def baseline_findings(failed, ceiling, cannot, passed=None, target=None):
    """Problems with a sandbox baseline. Empty list means the baseline is as declared.

    `target` is the suite this run exists to turn green, so it is red at baseline BY
    DEFINITION -- the entry condition, not scenery. It used to count as undeclared unless
    someone had also listed it in the ceiling, which refused test_coder_green_count's own
    fixture run and any dispatch whose fresh oracle was not yet declared."""
    def bare(name):
        return name.rsplit("/", 1)[-1]
    fail_bare = {bare(s) for s in failed}
    ceil_bare = {bare(s) for s in ceiling}
    cannot_bare = {bare(s) for s in cannot}
    probs = []
    own = {bare(target)} if target else set()
    for s in sorted(fail_bare - ceil_bare - cannot_bare - own):
        probs.append(f"undeclared failure: {s}")
    if passed is not None:
        pass_bare = {bare(s) for s in passed}
        for s in sorted(pass_bare & cannot_bare):
            probs.append(f"declared cannot-run but passed: {s}")
    return probs


def snapshot(root, extra=()):
    """The before-picture `diff_of` compares against. Walks SOURCE_EXT, not a second list.

    `L-114`. `diff_of` skips any file the baseline does not hold (`before is None` ->
    continue), so a baseline narrower than the set `diff_of` walks makes whole languages
    unmeasurable. This built `*.py` and `*.md` only. Every Kotlin edit therefore landed on
    disk, was invisible to `diff_of`, returned False from `changed()`, and was reported NO-OP
    no matter what the patch did.

    That is the entire Android result in `L-112` -- fourteen attempts called "nothing usable"
    and a conclusion that the wall was the model. The wall was this line. It is a function so
    the suite can assert against the code that actually runs: the first mutation of this bug
    was MISSED because the test built its own baselines inline and never touched this path.
    """
    out = {p.relative_to(root).as_posix(): p.read_text(encoding="utf-8", errors="replace")
           for e in SOURCE_EXT for p in root.rglob(f"*{e}") if p.is_file()}
    # And every path the ops name, whatever its extension -- otherwise `touched` compares a
    # real file against None and reports a change that is only the baseline's blind spot.
    for rel in extra or ():
        f = root / rel
        if rel not in out and f.is_file():
            try:
                out[rel] = f.read_text(encoding="utf-8", errors="replace")
            except OSError as e:
                _skip(f"{rel} unreadable, not captured", e)
    return out


def snapshot_paths(root, ops):
    """The current bytes of every path the ops name. Taken just before applying them."""
    out = {}
    for rel in {(op.get("path") or "").strip() for op in ops if op.get("path")}:
        f = root / rel
        if f.is_file():
            try:
                out[rel] = f.read_text(encoding="utf-8", errors="replace")
            except OSError as e:
                _skip(f"{rel} unreadable, not captured", e)
    return out


def touched(root, baseline, ops):
    """Paths an op named whose bytes now differ from the baseline. No extension list involved.

    THE THIRD APPEARANCE OF ONE BUG. `L-114` was the baseline being narrower than the set
    `diff_of` walked. The env3 probe then found `.toml` missing from that set, so a real change
    was reported NO-OP -- and widening the list to 24 extensions did not fix the class, it moved
    the boundary. The probe found it again at round 64: `.rs`, `.go`, `.rb`, `.php`, `.c`, `.h`,
    `.swift`, `.tf`, `.proto` and `Dockerfile` were all still invisible.

    An allow-list can only ever be as complete as the last person to think about it. The ops
    already say exactly which files were written, so correctness no longer asks the list --
    `SOURCE_EXT` is now only a *display* concern for which files to show in a diff.
    """
    out = []
    for rel in {(op.get("path") or "").strip() for op in ops if op.get("path")}:
        f = root / rel
        try:
            now = f.read_text(encoding="utf-8", errors="replace") if f.is_file() else None
        except OSError as e:
            _skip(f"{rel} unreadable, not compared", e)
            continue
        if now != baseline.get(rel):
            out.append(rel)
    return out


def changed(root, baseline, ops=None):
    """Did the patch change anything at all?

    Extracted so it can be mutated and caught. An edit that applies cleanly and leaves the
    file byte-identical satisfied every acceptance rule and was reported ACCEPTED (`L-111`);
    no suite can see it, because nothing broke, because nothing happened.
    """
    # Ops first, because they are exact. The SOURCE_EXT sweep stays as a second net for a
    # change made outside the ops the model declared.
    if ops and touched(root, baseline, ops):
        return True
    return bool(diff_of(root, baseline, ops).strip())


def diff_of(root, baseline, ops=None):
    """The unified diff a human reviews. Includes every path the ops named, whatever its type.

    FOURTH APPEARANCE OF ONE BUG, and this time in the half I declared harmless. `L-114` was
    the baseline narrower than this function's extension list. The probe found `.toml` missing
    from the list. Widening it to 24 extensions moved the boundary. Removing the list from
    `changed()` fixed correctness -- and I wrote that `SOURCE_EXT survives only as a display
    concern`, as though display were minor.

    It is not minor. **The diff IS the review artifact.** A `.rs` change was reported ACCEPTED
    with an empty diff: the verdict said a change had been made and the evidence shown for it
    was nothing. A reviewer approves what they can see, so an invisible change is approved
    unread. Found at round 27 of the free-roam probe.
    """
    named = {(op.get("path") or "").strip() for op in (ops or []) if op.get("path")}
    candidates = {q for e in SOURCE_EXT for q in root.rglob(f"*{e}")}
    candidates |= {root / rel for rel in named if (root / rel).is_file()}
    parts = []
    for p in sorted(candidates):
        rel = p.relative_to(root).as_posix()
        before = baseline.get(rel)
        if before is None:
            continue
        after = p.read_text(encoding="utf-8", errors="replace")
        if after != before:
            parts += list(difflib.unified_diff(
                before.splitlines(True), after.splitlines(True),
                f"a/{rel}", f"b/{rel}"))
    return "".join(parts)


def branch_is_pending(branch):
    """Is this branch still waiting for a human verdict?

    Read from reviewq's ledger rather than reimplemented: the question "has Jas seen this"
    has one owner, and a second copy of the rule here would drift from it (L-114, L-115).
    Fails OPEN -- if the queue cannot be read, treat the branch as pending and keep it. The
    cost of a wrong yes is a spare branch; the cost of a wrong no is deleting work a human
    was about to review.
    """
    try:
        sys.path.insert(0, str(REPO / "scripts"))
        import reviewq
        if any(r.get("branch") == branch for r in reviewq.pending()):
            return True
        # EXISTS, BUT NO LEDGER ROW NAMES IT -- someone else's work, mid-flight.
        #
        # reviewq.pending() is derived from agent-runs.jsonl, so a branch becomes "pending"
        # only once record() has written its row. write_branch commits FIRST and main() calls
        # record() after, which leaves a window where a branch holds committed work that no
        # ledger row mentions. A concurrent run with the same label reads not-pending, deletes
        # the branch, and the first run's change is unrecoverable (env3 probe, finding 41).
        #
        # A branch nobody has recorded is not an abandoned branch, it is a branch whose owner
        # has not finished writing its row. Fail OPEN, for the reason the docstring already
        # gives: a wrong yes costs a spare branch, a wrong no destroys committed work.
        if sh("git", "-C", str(REPO), "rev-parse", "--verify", branch).returncode == 0 \
                and not any(r.get("branch") == branch for r in reviewq.runs()):
            return True
        return False
    except Exception:
        return True


BRIEF_MARK, BRIEF_END = "--- BRIEF AS DISPATCHED ---", "--- END BRIEF ---"


def _run_commit(wt, msg, timeout=300, run=None):
    """(rc, output, timed_out) for the branch commit. A TIMEOUT IS A RESULT, NOT AN EXCEPTION (addition (b)).

    From t1331's own log: `subprocess.TimeoutExpired: ... commit -F - timed out after 300 seconds`, thrown straight
    out of write_branch and out of __main__, so the run died with the patch applied and uncommitted in a worktree
    under ~/.cache/hivemind-tmp. `rc` is None only when it timed out -- never 0 -- because a timeout delivered
    nothing and a caller that reads 0 would announce a branch that does not exist.
    """
    run = run or subprocess.run
    try:
        c = run(["git", "-C", str(wt), "-c", "core.hooksPath=scripts/hooks",
                 "commit", "-F", "-"], input=msg, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return None, (f"git commit timed out after {timeout}s: the patch IS APPLIED and NOT COMMITTED, and it is "
                      f"the only copy -- in the worktree {wt}"), True
    return c.returncode, (c.stdout or "") + (c.stderr or ""), False


def write_branch(label, ops, model, verified_pre=None, brief=None):
    """Put an accepted change on a review branch WITHOUT touching the working tree.

    The first version did `checkout -b`, wrote the files, then `checkout main` -- and
    uncommitted changes follow you across a checkout, so the change landed on main's working
    tree while the run announced "written to branch". A false claim, and one click away from
    being triggerable by a button.
    """
    # DO NOT CLOBBER A BRANCH THAT IS AWAITING REVIEW.
    #
    # `branch -D` was unconditional, so a second run on the same label DESTROYED the first
    # run's branch. For --task that is usually wanted -- a re-run replacing its own earlier
    # attempt. For --brief it is never wanted: the label is always the literal string
    # "brief", so two unrelated runs collide. Confirmed live by the env3 probe on 2026-08-28,
    # after being noticed and left unfixed the day before.
    #
    # The rule is not "never replace" but "never replace work a human has not seen". If
    # reviewq still lists the branch as pending, the previous run is waiting for Jas and
    # deleting it discards a decision he has not made yet. In that case write beside it
    # rather than over it, and say so.
    wt = Path(_tempfile.gettempdir()) / f"review-{label}"
    branch = f"coder/{label}"
    if branch_is_pending(branch):
        n = 2
        while sh("git", "-C", str(REPO), "rev-parse", "--verify",
                 f"{branch}-{n}").returncode == 0:
            n += 1
        branch = f"{branch}-{n}"
        wt = Path(_tempfile.gettempdir()) / f"review-{label}-{n}"
        say(f"  {label} is already awaiting review — writing to {branch} instead of "
            f"overwriting it")
    sh("git", "-C", str(REPO), "worktree", "remove", "--force", str(wt))
    sh("git", "-C", str(REPO), "branch", "-D", branch)

    # CLAIM THE NAME ATOMICALLY, AND ONLY EVER DELETE A NAME THIS PROCESS CLAIMED.
    #
    # The old shape was check-then-act across three separate git commands, and the losing
    # interleaving destroys committed work (env3 probe, finding 41):
    #
    #   P2  branch_is_pending("coder/x") -> False   (P1 has not created it yet)
    #   P2  branch -D coder/x                       (no-op, nothing there)
    #   P1  worktree add -b coder/x ...; commit     (P1's work is now ON the branch)
    #   P2  worktree add -b coder/x ...             (FAILS -- the name is taken now)
    #   P2  branch -D coder/x                       (DELETES P1'S COMMITTED WORK)
    #
    # P2's failure path assumed the only way the name could be taken was that P2 itself had
    # just taken it. `git branch <name>` refuses to overwrite an existing ref, so making the
    # ref first turns the name into an atomic claim: whoever's `git branch` returns 0 owns
    # it, everyone else is told so and steps to the next free name. After that every delete
    # below -- here and in abandon() -- is provably deleting this process's own ref.
    #
    # This also retires the reason `-b` was used: the old comment noted that `worktree add -b`
    # creates the ref BEFORE failing on the path, which is why the failure path had to clean
    # up a ref it never meant to leave. The ref is now created deliberately, one step earlier.
    claimed = False
    for _ in range(50):
        if sh("git", "-C", str(REPO), "branch", branch, "HEAD").returncode == 0:
            claimed = True
            break
        n = 2
        while sh("git", "-C", str(REPO), "rev-parse", "--verify",
                 f"coder/{label}-{n}").returncode == 0:
            n += 1
        branch = f"coder/{label}-{n}"
        wt = Path(_tempfile.gettempdir()) / f"review-{label}-{n}"
        say(f"  coder/{label} was claimed by another run — writing to {branch} instead")
    if not claimed:
        return False, branch, "could not claim a branch name for the change"

    r = sh("git", "-C", str(REPO), "worktree", "add", str(wt), branch)
    if r.returncode:
        # Leaving the claimed ref behind would be the "looks like a change waiting for review
        # and contains nothing" state abandon() exists to prevent, on the one path that cannot
        # reach abandon because abandon is defined after this return. Observed 2026-08-27: the
        # run announced ACCEPTED, recorded branch=coder/brief, exited 0, and the diff was empty.
        sh("git", "-C", str(REPO), "worktree", "remove", "--force", str(wt))
        sh("git", "-C", str(REPO), "branch", "-D", branch)
        return False, branch, f"could not create the worktree: {r.stderr.strip()[:200]}"
    def abandon(why):
        # Leave nothing behind. A half-made review branch is worse than none: it looks like
        # a change waiting for review and contains nothing.
        sh("git", "-C", str(REPO), "worktree", "remove", "--force", str(wt))
        sh("git", "-C", str(REPO), "branch", "-D", branch)
        return False, branch, why

    # apply_ops, NOT a second implementation. This loop used to inline the anchored replace
    # and knew nothing about the line-range shape added by `t145`, so `--apply` died with
    # `KeyError: 'old'` on exactly the op type the model now prefers -- the change was
    # accepted, verified, reported, and then could not be written to a branch.
    #
    # Third instance tonight of one pattern: a producer and a consumer each holding their own
    # copy of the same rule, drifting in silence (`L-114` baseline vs diff_of, `L-115`
    # serve.py vs gateway.py). The fix is always to delete the copy, never to sync it.
    # DELIVER ONLY WHAT WAS VERIFIED.
    #
    # sandbox() copies the WORKING TREE; this worktree is cut from HEAD. When they differ the
    # run verifies one thing and ships another. The anchored op shape survives that by luck --
    # it carries its own pre-image in `old` and fails loudly with anchor-not-found. The
    # LINE-RANGE shape does not: it is positional, bounds-checked only, and it is the shape the
    # model now prefers (t145). Replayed against different content it silently replaces
    # DIFFERENT LINES and commits them, while the terminal, the ledger and the task note all
    # say ACCEPTED -- true of the sandbox, false of the branch. Found by the env3 probe
    # (finding 32); reproduced in test_coder_delivers_what_it_verified.py, where the delivered
    # file came out carrying the new heading AND the old one, with an unrelated line destroyed.
    #
    # Compare whole FILES, not the op ranges. It is the same question asked once per file
    # instead of once per op, it is exact, and it stays correct for op shapes not invented yet.
    # Refuse rather than repair: a divergence here means the verification was against something
    # else, so there is no patch whose delivery would mean what the ACCEPTED line claims.
    if verified_pre is not None:
        for rel in sorted({(op.get("path") or "").strip() for op in ops if op.get("path")}):
            f = wt / rel
            now = f.read_text(encoding="utf-8", errors="replace") if f.is_file() else None
            was = verified_pre.get(rel)
            if now != was:
                which = ("does not exist in HEAD" if now is None else
                         "is not in HEAD" if was is None else
                         "differs between HEAD and the working tree")
                return abandon(
                    f"{rel} {which}, so the branch would NOT carry the change that was "
                    f"verified. The sandbox was built from the working tree and this branch "
                    f"is cut from HEAD. Commit or stash {rel} and run it again.")

    ok, detail = apply_ops(wt, ops)
    if not ok:
        return abandon(f"the patch no longer applies in the worktree: {detail}")
    # ONLY WHAT THE PATCH TOUCHED (2026-10-19). `add -A` staged every file that changed in the worktree
    # while the run was in it -- ledgers (check-history.jsonl, agent-runs.jsonl) written by the commit
    # hook's own checks and by services -- and the gate then refused the whole commit: 32 builds since
    # 10-12 were ACCEPTED and NOT WRITTEN this way. The patch's own paths are the delivery.
    touched = sorted({(op.get("path") or "").strip() for op in ops if (op.get("path") or "").strip()})
    if not touched:
        return abandon("the patch names no file, so there is nothing to deliver")
    sh("git", "-C", str(wt), "add", "-A", "--", *touched)
    # THE BRIEF TRAVELS WITH THE BRANCH (t311). A brief file can be deleted or rewritten after
    # dispatch, and then merge_accounting has nothing to account the branch against --
    # coder/brief-b698d36b could never be scope-checked. The commit body keeps it verbatim.
    kept = (f"\n{BRIEF_MARK}\n{brief.strip()}\n{BRIEF_END}\n" if brief else "")
    msg = (f"feat({label}): change proposed by the local model\n\n"
           f"Written by coder.py to a review branch. NOT merged, NOT on main.\n"
           f"Review with: git diff main..{branch}\n{kept}\nAgent: ollama/{model}\n")
    rc_commit, out_commit, timed_out = _run_commit(wt, msg)
    if timed_out:
        # THE PATCH IS IN THE WORKTREE AND NOTHING ELSE KNOWS IT (addition (b), 2026-10-30). A commit that runs
        # past 300 s used to kill the whole process here: the applied patch stayed in a worktree under
        # ~/.cache/hivemind-tmp -- which the reaper sweeps -- and the failure landed as `unclassified`. It is a
        # distinct outcome, it names where the work is, and the worktree is NOT removed by abandon() because that
        # is the only copy of the patch.
        print(f"  NOTE: {out_commit}", file=sys.stderr)
        return False, branch, out_commit
    if rc_commit:
        # WHOLE (t1337): cut at 300 characters, both refusals of t952's accepted work read "Tr" --
        # the hook's banner filled the space and the traceback that held the cause was dropped.
        return abandon(f"commit refused: {out_commit.strip()}")
    sh("git", "-C", str(REPO), "worktree", "remove", "--force", str(wt))
    base = sh("git", "-C", str(REPO), "rev-parse", "--short", "HEAD").stdout.strip()
    # NOT `git diff main..branch`. The worktree is cut from HEAD when the run starts, and if
    # main advances before anyone reviews, that command shows every later commit as a DELETION
    # by the model. Observed on t016: an 8-line Kotlin change was rendered as "437 deletions,
    # including scripts/selfmatch.py". A reviewer reading that sees vandalism, and merging the
    # branch really would revert the newer work.
    #
    # The branch is always exactly one commit on top of its base, so diffing it against its own
    # parent is exact no matter how far main has moved.
    return True, branch, (f"committed to branch {branch} — main is untouched.\n"
                          f"    Review: git diff {branch}~1..{branch}\n"
                          f"    (branch is based on {base}; do NOT use `git diff main..{branch}` "
                          f"— if main has moved,\n     it renders every newer commit as a "
                          f"deletion by the model)")


def brief_task(path):
    """The tNNN on a brief file's `TASK: tNNN` line, or None. coder.py gets --brief-file without
    --task, so record()'s task is None for every scheduled build (found 2026-09-24)."""
    try:
        m = re.search(r"^\s*TASK:\s*(t\d{2,4})\b", Path(path).read_text(encoding="utf-8"), re.M)
    except OSError:
        return None
    return m.group(1) if m else None


def write_session(task, model, brief, tries, reasoning, outcome, root=None):
    """One BUILDER session per build, under the brief's task (t386): each attempt's outcome and
    detail, every call's reasoning, and the result -- so whoever re-scopes a failed brief sees
    WHY it failed, not only that it did. None when there is no task to key it by."""
    if not task:
        return None
    sys.path.insert(0, str(REPO / "scripts"))
    import task_sessions
    path = task_sessions.start(task, model, "build " + brief, root=root, brief=brief,
                               role="builder")
    for t in tries:
        task_sessions.append(path, "attempt", n=t.get("n"), outcome=t.get("outcome"),
                             detail=t.get("detail"))
    for r in reasoning:
        if r:
            task_sessions.append(path, "reasoning", text=r)
    task_sessions.append(path, "answer", text="outcome: " + outcome)
    return path


_RUN_FACTS = {}          # facts measured during this run that every ledger row carries


def record(task, model, outcome, tries, patch, why, branch=None, target=None):
    """Three places, deliberately overlapping, because each fails differently.

    `target` is the suite the task named, or None. It is a parameter rather than an assumption
    because the note claimed "The target suite is green" on EVERY accepted run -- including the
    five branches waiting for review on 2026-08-26, none of which named a suite. The terminal
    told me acceptance was WEAKER; the note told Jas it was stronger. Found by the env3 probe,
    whose mission was to make the tooling state something untrue.

    A note on the task is what Jas actually reads, on his phone, in the same thread as
    everything else about that task. agent-runs.jsonl is what makes "is it getting better"
    answerable with a number rather than an impression. session_start surfaces the count so
    a session cannot miss that branches are waiting -- which is the mistake the outbox made
    for four days.

    FAILURES ARE RECORDED IN FULL. A task the model failed three times, with the reason each
    time, is the input for deciding whether to fix the harness, re-scope the task, or do it
    by hand -- and every harness defect found so far came from exactly that record.
    """
    try:
        bf = sys.argv[sys.argv.index("--brief-file") + 1] if "--brief-file" in sys.argv else ""
        write_session(task or (brief_task(bf) if bf else None), model,
                      Path(bf).name if bf else "(no brief file)", tries,
                      list(ask.reasoning), outcome)
    except Exception as e:                                          # noqa: BLE001
        print(f"  (task session not written: {type(e).__name__}: {e})", file=sys.stderr)
    RUNS.parent.mkdir(parents=True, exist_ok=True)
    with RUNS.open("a", encoding="utf-8") as f:
        f.write(json.dumps({
            "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "task": task or (brief_task(sys.argv[sys.argv.index("--brief-file") + 1])
                            if "--brief-file" in sys.argv else None), "model": model, "served": resolve_model(model),
            "outcome": outcome,
            "attempts": len(tries), "branch": branch,
            "tokens": sum(t.get("tokens", 0) for t in tries),
            "seconds": round(sum(t.get("seconds", 0) for t in tries)),
            "why": why, "patch": (patch or ""),
            "ops": (tries[-1].get("ops") if tries else None),
            # Every attempt's ops and failure output, not just the last. A run that ended in
            # TESTS-FAILED is exactly the one worth reviewing, and until now it recorded no
            # ops at all -- so "what did it actually try?" had no answer anywhere.
            # tokens and seconds PER ATTEMPT. They were collected in `tries` and dropped
            # here, so every trail row read tokens=None, seconds=0 and "which attempt burned
            # the budget" had no answer (found reading the 2026-09-14 lanes).
            "trail": [{"n": t["n"], "outcome": t["outcome"],
                       "tokens": t.get("tokens"), "seconds": t.get("seconds"),
                       "done_reason": t.get("done_reason"),
                       "detail": str(t.get("detail", "")),
                       "ops": t.get("ops"),
                       "output": str(t.get("output", "")),
                       # THE RAW REPLY OF AN UNPARSEABLE ATTEMPT (2026-09-24). Kept in `tries`
                       # since e38a3eb and dropped HERE by this whitelist, so p50's two
                       # unparseable attempts still could not be diagnosed.
                       "raw": t.get("raw")} for t in tries],
            # THE SANDBOX BASELINE AS DATA (t275, fix 2). It was a say() line only, so a
            # baseline that grew between runs could be read by a person in one log and computed
            # by nothing. None when this run never got as far as the baseline.
            "baseline_failing": _RUN_FACTS.get("baseline_failing"),
        }) + "\n")

    if not task or not task.startswith("t"):
        return
    served = resolve_model(model)
    named = f"`{served}`" if served != model else "local model"
    lines = [f"**{outcome}** after {len(tries)} attempt(s), {named}.", ""]
    if outcome.startswith("ACCEPTED"):
        lines += [why or "", "",
                  # KEYED ON THE OUTCOME, NOT ON `branch`. Inferring "dry run" from the
                  # absence of a branch was true while the only branchless ACCEPTED was a
                  # dry run -- and became false the day ACCEPTED-NOT-WRITTEN was added
                  # (L-130), which also records branch=None. This note is what Jas reads to
                  # decide whether to merge, so it said "Dry run" about a run where --apply
                  # WAS given and the write failed. Third consumer of the same implicit
                  # contract to break the same way; see L-134 and the RCA note.
                  (f"Branch `{branch}` — NOT committed and NOT merged." if branch
                   else "**The change was accepted but NOT WRITTEN.** `--apply` was given "
                        "and the branch could not be created, so there is nothing to review "
                        "and nothing is waiting."
                        if outcome == "ACCEPTED-NOT-WRITTEN"
                   else "Dry run — the change was verified but not written.")
                  + (" The target suite is green and no suite that was already green broke."
                     if target else
                     " NO TARGET SUITE was named, so acceptance only means nothing already"
                     " green broke -- it does NOT mean the change does what the task asked."
                     " Read the diff on its own merits.")
                  + " Review the diff before anything else happens to it.", ""]
    else:
        lines += ["Nothing was written. What it tried, in order:", ""]
    for t in tries:
        lines.append(f"- attempt {t['n']}: {t['outcome']}"
                     + (f" — {str(t.get('detail',''))[:180]}" if t.get("detail") else ""))
    if not outcome.startswith("ACCEPTED"):
        lines += ["", "This is a record, not a blocker. The queue moved on. If the same "
                      "failure repeats, the question is usually whether the task is the "
                      "right shape rather than whether the model is capable."]
    sh(sys.executable, str(REPO / "projects" / "app-repo" / "taskstore.py"),
       "note", task, "\n".join(lines), f"local/{model}")


def unaccounted(rows):
    """Dispatch ids that cannot be accounted for. Empty list means every dispatch is."""
    TERMINAL = ("ACCEPTED", "ACCEPTED-DRYRUN", "ACCEPTED-NOT-WRITTEN", "TESTS-FAILED",
                "NO-ATTEMPTS", "REFUSED-NO-TARGET", "REFUSED-BASELINE", "ERROR")
    groups = {}
    for row in rows:
        did = row.get("dispatch_id")
        if did is None:
            continue
        groups.setdefault(did, []).append(row.get("outcome"))
    problems = []
    for did in sorted(groups):
        outcomes = groups[did]
        has_started = any(o == "STARTED" for o in outcomes)
        has_terminal = any(o in TERMINAL for o in outcomes)
        bad = [o for o in outcomes if o is None or (o != "STARTED" and o not in TERMINAL)]
        if bad:
            problems.append(f"{did}: unrecognised outcome {bad}")
        elif has_started and has_terminal:
            pass  # accounted for
        elif has_started and not has_terminal:
            problems.append(f"{did}: STARTED but no terminal outcome")
        elif has_terminal and not has_started:
            problems.append(f"{did}: terminal outcome but no STARTED row")
    return problems


def open_run(task, model, target=None):
    """Write the STARTED row and return its dispatch_id."""
    dispatch_id = f"{int(time.time())}-{os.getpid()}"
    RUNS.parent.mkdir(parents=True, exist_ok=True)
    try:
        with RUNS.open("a", encoding="utf-8") as f:
            f.write(json.dumps({
                "dispatch_id": dispatch_id,
                "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "task": task, "model": model, "target": target,
                "outcome": "STARTED",
            }) + "\n")
    except OSError as e:
        print(f"  open_run: failed to write STARTED row: {e}")
    return dispatch_id


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--task")
    ap.add_argument("--brief", help="the task TEXT. A path here is refused -- use --brief-file")
    ap.add_argument("--brief-file", default=None,
                    help="read the task text from this file (t263)")
    ap.add_argument("--files", nargs="*", default=None)
    ap.add_argument("--target", default=None,
                    help="the suite that MUST go green (inferred from the task if omitted)")
    ap.add_argument("--model", default="local")
    ap.add_argument("--reads", type=int, default=3,
                    help="extra rounds the agent may spend asking for files")
    ap.add_argument("--attempts", type=int, default=4)
    ap.add_argument("--no-think", action="store_true")
    ap.add_argument("--apply", action="store_true",
                    help="write the accepted change to a git branch for review")
    ap.add_argument("--force", action="store_true",
                    help="proceed even if a live twin is running (I have confirmed it is stale)")

    ap.add_argument("--resume-checkpoint", default=None,
                    help="continue a run that yielded (exit 4) from its checkpoint file")
    a = ap.parse_args(argv)

    if a.task:
        title, body, first, area = read_task(a.task)
        brief = f"{title}\n\n{latest_brief(body)}"
        retrieval_text = f"{title}\n\n{first}"
        label = a.task
    elif a.brief or a.brief_file:
        # A PATH IS NOT A BRIEF (t263). --brief takes the task TEXT, and a path is valid
        # text, so passing one made the path string the ENTIRE task. The model then did the
        # only honest thing available -- asked to read the file its task named -- and burned
        # attempts doing it: seven on 2026-09-10, and on 2026-09-14 two parallel_dispatch
        # lanes spent 11,990s and 13,129s on brief-errorscan-inversion.txt, both UNPARSEABLE
        # after 7 attempts, asking for the brief file three times each. t263 was filed the
        # first time and left queued; the second time cost six GPU-hours.
        #
        # Two doors instead of one guess. --brief-file reads the file and says so. --brief
        # given something that IS an existing file is refused at the boundary, because
        # nothing downstream can tell a path from a one-line task -- only the moment the
        # argument arrives can.
        if a.brief and a.brief_file:
            raise SystemExit("give --brief OR --brief-file, not both")
        if a.brief_file:
            bf = Path(a.brief_file)
            if not bf.is_file():
                raise SystemExit(f"--brief-file {a.brief_file}: no such file")
            a.brief = bf.read_text(encoding="utf-8")
            if not a.brief.strip():
                raise SystemExit(f"--brief-file {a.brief_file} is empty -- refusing to "
                                 "dispatch a model with no task")
            say(f"  brief read from {a.brief_file} ({len(a.brief)} chars, whole)")
        elif "\n" not in a.brief and len(a.brief) < 4096 and Path(a.brief.strip()).is_file():
            raise SystemExit(
                f"REFUSED: --brief is the task TEXT, and {a.brief.strip()!r} is a path to an "
                f"existing file.\nPassing it would make the path string the model's whole "
                f"task (t263).\nUse:  --brief-file {a.brief.strip()}")
        # HASH THE BRIEF INTO THE LABEL. It was the literal string "brief", so every
        # --brief run produced `coder/brief` and two unrelated runs collided by name. Jas
        # chose a hash over a readable slug (2026-08-28): *"You dont need to make it human
        # readable. It's out of my expertise to review anyways."* Distinct briefs now get
        # distinct branches, and an identical brief re-run deliberately reuses its own
        # branch, which is the replace-my-own-attempt case that was always wanted.
        import hashlib
        # Hashes the brief CONTENT on both doors. Via --brief-file this used to be the path
        # string, so two different briefs written to one path shared a branch name (t311).
        label = "brief-" + hashlib.sha256(a.brief.encode()).hexdigest()[:8]
        brief, retrieval_text, area = a.brief, a.brief, ""
    else:
        raise SystemExit("give --task or --brief")

    # ONE COPY OF A JOB AT A TIME. Refused here, in coder.py itself, rather than in any one
    # launcher -- because on 2026-09-14 the launcher was a Claude session re-running
    # parallel_dispatch.py after two of its three lanes refused, while the third was still
    # running from the first launch. Two identical bf16 dispatches, both --apply, raced for
    # the GPU and the same branch for three hours and each ran at about half speed. Every
    # launcher (parallel_dispatch, the scheduler, the console, the MCP dispatch, a human)
    # passes through this line, so this is the only place the check cannot be forgotten.
    twin = running_twin(label, a.files, a.model)
    refused, msg = twin_refusal(twin, a.force, label, a.model)
    if refused:
        # A REFUSAL IS A ROW TOO (t1130). This was the last refusal in the file that ended in
        # `raise SystemExit(<string>)` and wrote no ledger row, so a dispatch whose model was never
        # invoked was charged to the model as a coding failure -- and a string exit code lands as 1.
        # Like its siblings: say why, record the cause, return 2. `target` is not bound yet.
        say(msg)
        record(a.task, a.model, "LEASE-HELD",
               [{"n": 0, "outcome": "LEASE-HELD", "detail": msg}], None, msg, target=None)
        return 2


    say(f"\ncoder — {label} — model={a.model}, "
          f"reasoning={'off' if a.no_think else 'on'}, {a.attempts} attempt(s)\n")
    say(f"  task: {brief.splitlines()[0][:90]}")

    # NOTHING IS CLEARED HERE. The log is named by pid, so it is already empty --
    # and the call that used to sit here (LIVE.write_text("")) wiped a CONCURRENT
    # lane's running history, which is the one thing this file exists to hold.
    # The suite that must go GREEN, as opposed to merely not-newly-broken.
    #
    # Without this the agent can be ACCEPTED for doing nothing. The entry condition says a
    # test must already fail -- so the target suite is in the baseline -- and the acceptance
    # rule only asked for "no NEW failures". A patch that changed nothing relevant would
    # satisfy that perfectly. Found by reading the baseline line of the first properly
    # shaped run, before trusting its result. This is the false-accept case, which costs
    # more than any number of failures to fix.
    target = a.target
    if not target:
        m = re.search(r"(scripts/tests/test_\w+\.py)", brief)
        target = m.group(1) if m else None
    # Check the target BEFORE building a sandbox. A named target that does not exist is
    # worse than no target at all, and the guard written for the no-target case could not
    # see it: that guard asks `if target`, and a typo is truthy. The property that matters
    # is not "was a target named" but "did the target actually RUN".
    #
    # run_suites(root) enumerates by globbing scripts/tests/test_*.py. A target that is not
    # on disk is therefore never in `failed`, so `target in failed` is False, nothing forces
    # it into new_failures, and the run is ACCEPTED with the note claiming "The target suite
    # is green" -- about a suite that was never executed. env3 probe, round 53.
    #
    # ORDER MATTERS AND COST ME 55 GB. This check first sat AFTER `root = sandbox()`, so
    # every refusal still built a full repo copy and abandoned it. Worse, the guard test
    # spawns coder.py, and when the suite ran inside a sandbox it spawned THAT sandbox's
    # coder.py, which built another -- 279 sandboxes before anyone looked at /tmp. The
    # sandbox is a copy of the repo, so checking REPO is equivalent, free, and recursion
    # stops at the first level because no copy is ever made.
    if target and not (REPO / target).is_file():
        say(f"  TARGET SUITE DOES NOT EXIST: {target}")
        say("  Refusing to start. A named target that is absent is never run, and every")
        say("  verdict after it would claim a green suite that was never executed.")
        say(f"  Suites that do exist: {', '.join(suites(REPO)) or 'none'}")
        return 2

    root = sandbox()
    import signal
    _LIVE.update(root=root, state=None)
    signal.signal(signal.SIGTERM, _on_sigterm_cleanup)    # until the attempts install their own

    if target:
        say(f"  target suite: {target} — must go GREEN, not merely stay unbroken")
    else:
        say("  no target suite named; acceptance is WEAKER — a change that fixes nothing "
            "can pass")

    baseline = snapshot(root)
    try:
        # Which suites already fail HERE, before the agent touches anything. Without this
        # the agent gets blamed for a suite the sandbox cannot run -- exactly the false
        # negative that made codeval read 0/25 on its first full run.
        say("  baselining the sandbox …", end="")
        # A MISSING DECLARATION IS NOT PERMISSION. If either file is absent or unreadable,
        # every failure is undeclared and the run refuses. Absence must not read as "nothing
        # is declared, so nothing is wrong" -- that is L-146, and it is how this check would
        # quietly stop checking.
        def _declared(path, key):
            f = REPO / path
            if not f.is_file():
                say(f"  MISSING DECLARATION: {path} -- treating every failure as undeclared")
                return set()
            try:
                return set(json.loads(f.read_text(encoding="utf-8")).get(key, []) or [])
            except (json.JSONDecodeError, ValueError) as e:
                say(f"  UNREADABLE DECLARATION: {path} ({e}) -- treating every failure "
                    "as undeclared")
                return set()

        # Read the declared sets FIRST: they say which failures are expected, so run_suites
        # can skip the confirming re-run for those and spend it only on the surprising ones
        # -- the failures that would actually refuse this dispatch.
        _ceiling_pre = _declared("workspace/test-failures-ceiling.json", "allowed_to_fail")
        _cannot_pre = _declared("workspace/sandbox-baseline.json", "cannot_run_in_sandbox")
        # `out` IS KEPT (2026-10-24): it used to be `_`, and the refusal branch below printed
        # the failing suites' NAMES while their output was alive in this variable and then
        # discarded with the sandbox. See record_baseline_output.
        already, already_out = run_suites(root, declared=set(_ceiling_pre) | set(_cannot_pre))
        _RUN_FACTS["baseline_failing"] = sorted(s.split("/")[-1] for s in already)
        say(f" {len(already)} suite(s) already failing"
              + (f": {', '.join(s.split('/')[-1] for s in already)}" if already else ""))

        # THE BASELINE IS DECLARED, NOT DISCOVERED (t260).
        #
        # This used to accept whatever failed as normal and carry on. Six phantom failures
        # therefore sat in every baseline -- suites that pass on main and fail here only
        # because sandbox() excludes .git and .venv -- so a REAL regression a dispatch
        # introduced was indistinguishable from the scenery. On 2026-09-11 a run announced
        # "16 suite(s) already failing" and nothing objected.
        #
        # Now: two declarations name what MAY fail, and anything else stops the run BEFORE
        # the model is asked to do anything. An allowlist on purpose -- a blacklist of
        # known-bad failures needs a librarian, and the failure nobody predicted is the one
        # worth hearing about.
        #
        # THE INFERENCE PATH MUST ANSWER BEFORE WE SPEND ANYTHING ON IT. t283, added
        # 2026-09-14, and it is the companion to that day's timeout increase: generous timeouts
        # make a slow answer survive AND make a wedged server take longer to notice, so the
        # check that exercises the path has to run first.
        #
        # THE COST OF NOT HAVING THIS, measured: on 2026-09-11/12 four dispatches were sent into
        # a server whose /api/tags answered instantly with 16 models while /api/chat timed out on
        # every one. Each dispatch then produced a plausible task-design diagnosis -- chunk too
        # big, brief unclear, budget too small -- and all three were wrong. Refusing to start is
        # the same discipline the baseline guard below already applies to a red sandbox: work on
        # ground you cannot trust manufactures wrong conclusions.
        # PROBE THE PATH THIS RUN WILL USE, NOT A DIFFERENT ONE (t286, 2026-09-25). The probe
        # asks the live Ollama for five tokens with a 600 s timeout. An explicit CODER_ENDPOINT
        # elsewhere -- the suites' fake model server -- never touches Ollama, yet every suite
        # that drives coder.py waited behind Q4's 15-minute manager jobs on the single Qwen
        # slot before its baseline began, and ran past run_tests' 300 s cap. Measured: the
        # probe took its whole 80 s timeout with Q4 busy. Those suites turned red when the
        # manager queue started running unattended, which is why they looked like new failures.
        _named = os.environ.get("CODER_ENDPOINT")
        if _named and "11434" not in _named:
            say(f"  NOTE: CODER_ENDPOINT={_named} is not Ollama -- the Ollama health probe "
                f"does not apply to it.")
        else:
            try:
                sys.path.insert(0, str(REPO / "scripts"))
                import inference_health
                # classify(), not probe(): BUSY is not DOWN. With one dispatch holding the single
                # -np 1 slot a probe times out, and refusing on that made parallel dispatch
                # impossible -- the first lane locked out the second by existing (t303).
                # THIS RUN'S MODEL, not "whatever Ollama lists first" (2026-10-06): the probe
                # then reaches the run's own server, and never loads a stranger to ask it 5 tokens.
                _h = inference_health.classify(model=a.model)
                if _h.get("state") == "busy":
                    say(f"  NOTE: {_h.get('say')}")
                elif not _h.get("up"):
                    say("")
                    say("  REFUSING TO START -- the inference path did not answer:")
                    for line in (_h.get("say") or "").splitlines():
                        say(f"    {line}")
                    say("  Nothing is wrong with this brief. Re-run it once the server answers:")
                    say("    python3 scripts/inference_health.py")
                    shutil.rmtree(root, ignore_errors=True)
                    return 2
                if _h.get("slow"):
                    # NOT a refusal -- slow is not down, and refusing here would stop work on a
                    # merely busy box. But it is said out loud, because a queue ahead of you is the
                    # most common reason a dispatch takes hours.
                    say(f"  NOTE: the server answered in {_h.get('seconds')}s for five tokens -- "
                        f"something may be queued ahead of this run.")
            except ImportError:
                say("  NOTE: scripts/inference_health.py not importable; dispatching unchecked.")

        ceiling, cannot = _ceiling_pre, _cannot_pre
        surprises = baseline_findings(already, ceiling, cannot, target=target)
        if surprises:
            say("")
            say("  REFUSING TO START -- the sandbox baseline is not what is declared:")
            for s in surprises:
                say(f"    {s}")
            say("  A baseline nobody can trust makes every verdict after it unfalsifiable:")
            say("  a regression this dispatch introduces cannot be told from the scenery.")
            # THE CAUSE GOES TO DISK BEFORE THE SANDBOX IS DELETED (2026-10-24). `out` used to
            # be dropped as `_` at the baseline call, so a refusal left two suite names and no
            # reason -- eleven refusals, nine hours of production, and no way to tell a real
            # regression from a sandbox artefact without reproducing it by hand.
            names = {s.split("failure: ", 1)[-1].strip() for s in surprises}
            try:
                kept = record_baseline_output(
                    {k: v for k, v in (already_out or {}).items() if Path(k).name in names},
                    REPO / "workspace" / "run-logs" / "known-failures")
                if kept:
                    say("  Each refusing suite's own output is on disk -- read it before re-dispatching:")
                    for p in kept:
                        say(f"    {p}")
            except OSError as e:                                     # noqa: BLE001
                say(f"  NOTE: the refusing suites' output was not written ({e})")
            say("  Either fix the suite, or declare it in one of:")
            say("    workspace/test-failures-ceiling.json   real, known debt")
            say("    workspace/sandbox-baseline.json        the sandbox cannot run it")
            # A REFUSAL IS A ROW TOO (t275, fix 1): a dispatch that died here left nothing a
            # machine could read, so the scanner saw only the runs that got further.
            record(a.task, a.model, "REFUSED-TO-START", [], None, "; ".join(surprises),
                   target=target)
            shutil.rmtree(root, ignore_errors=True)
            return 2

        files = [(f, len((root / f).read_text(encoding='utf-8', errors='replace')))
                 for f in (a.files or []) if (root / f).is_file()] \
            or relevant_files(root, retrieval_text, area=area)
        say(f"  context: {', '.join(f for f, _ in files) or '(nothing matched)'}\n")

        symbols = symbols_of(brief)
        lines = lines_of(brief)

        appending = bool(re.search(r"^APPEND:", brief or "", re.M))

        def excerpt(rel):
            text = (root / rel).read_text(encoding='utf-8', errors='replace')
            if appending and not rel.endswith(".py"):     # t672: an append is written at the end
                return sliced_tail(text)
            if symbols and rel.endswith(".py"):
                cut = sliced(text, symbols)
                if cut is not None:
                    return cut
            elif lines:  # t427: a brief naming lines, not symbols, gets windows, not the file
                cut = sliced_by_lines(text, lines)
                if cut is not None:
                    return cut
            return numbered(text, re.findall(r"[a-zA-Z_]{4,}", brief))

        shown = {rel: excerpt(rel) for rel, _ in files}
        # THE SLICE STOOD, OR THE RUN DOES NOT START (t447). A brief that names SYMBOLS has
        # promised the model a slice; a silent whole-file fallback broke that promise and
        # filled the window four times over.
        for rel, chosen in shown.items():
            if not rel.endswith(".py"):
                continue
            ok, scope_reason = scope_gate(brief, (root / rel).read_text(encoding="utf-8",
                                                                       errors="replace"), chosen)
            if not ok:
                say(f"\n  NEEDS-SCOPE -- refusing to start: {rel}: {scope_reason}")
                record(a.task, a.model, "NEEDS-SCOPE",
                       [{"n": 0, "outcome": "NEEDS-SCOPE", "detail": f"{rel}: {scope_reason}"}],
                       None, None, target=target)
                shutil.rmtree(root, ignore_errors=True)
                return 2
        ctx = "\n\n".join(f"--- {rel} ---\n" + shown[rel] for rel, _ in files)
        base = (f"TASK\n{brief}\n\nEVERY FILE IN THE REPOSITORY (ask for any of them by "
                f"path)\n{repo_map(root)}\n\nSTARTING CONTEXT — files matching the task "
                f"(line numbers are for reading only; do not include them in your anchors)"
                f"\n\n{ctx}")
        budget = 200000
        _win = None
        if target:
            target_block, _ = target_context(root, target, budget, _win)
            if target_block:
                base = base + "\n\n" + target_block
        convo, tries = base, []

        # WILL IT FIT? Refuse a brief whose prompt cannot share the window with an answer,
        # before any attempt. The reason goes back to scoping (failure-schema: window-full).
        _win = None
        try:
            sys.path.insert(0, str(HERE))
            import settings as _ws
            # The SAME window ask() sends: the profile's num_ctx as an override. Without the
            # override options() returns the model's own maximum, which is not what is served.
            _win = served_window(a.model, _ws.options("coder", a.model,
                                 num_ctx=_ws.load()["coder"]["num_ctx"]).get("num_ctx"))
        except Exception as e:                                      # noqa: BLE001
            say(f"  NOTE: window size unknown ({type(e).__name__}); fit not checked")
        fits, est = prompt_fits(len(base), MIN_TOKENS_FIRST, _win)
        if not fits:
            why = (f"the prompt is ~{est} tokens and the answer needs up to {MIN_TOKENS_FIRST}, which cannot "
                   f"fit a {_win}-token window. Name the functions to edit on a SYMBOLS: line "
                   f"in the brief so only those are shown"
                   + (" (it has one; the named symbols are still too large -- split the "
                      "change)" if symbols else "") + ".")
            say(f"\n  NEEDS-SCOPE -- refusing to start: {why}")
            record(a.task, a.model, "NEEDS-SCOPE",
                   [{"n": 0, "outcome": "NEEDS-SCOPE", "detail": why,
                     "prompt_tokens_est": est, "window": _win}], None, None, target=target)
            shutil.rmtree(root, ignore_errors=True)
            return 2

        opened = {}          # rel -> the numbered excerpt already shown, so context ACCUMULATES
        shown_to = {}        # rel -> highest line number served, so a repeat gets the REST
        # THE STARTING CONTEXT COUNTS AS SERVED (t991). Nothing recorded what excerpt() handed
        # over, so the first request for a whole-file target asked unseen() from line 0, was
        # handed back bytes already in the prompt, and was refused as a duplicate. Read each
        # file with the same arguments excerpt() used, so the line count and the digits come
        # from one text; max() so a checkpoint's restored value is never lowered.
        for rel in shown:
            _src = (root / rel).read_text(encoding='utf-8', errors='replace')
            shown_to[rel] = max(shown_to.get(rel, 0), excerpt_last_line(_src, shown[rel]))

        def ctx(tail):
            return compose(base, opened, tail)
        # File requests must not eat the fix budget. An agent that spends its four attempts
        # reading has done the right thing and still fails, which would read as incapacity.
        budget = first_budget(len(base), _win)   # t477; retry_budget() after a failed resume
        budget_requested = budget
        budget = fit_output_budget((len(base) + 2) // 3, _win, budget)
        if budget is None:
            why = (f"the prompt fills the {_win}-token window with no room for an answer: "
                   f"~{(len(base) + 2) // 3} prompt tokens + {budget_requested} requested + "
                   f"{OUTPUT_RESERVE_TOKENS} reserve tokens")
            say(f"\n  NEEDS-SCOPE -- refusing to start: {why}")
            record(a.task, a.model, "NEEDS-SCOPE",
                   [{"n": 0, "outcome": "NEEDS-SCOPE", "detail": why,
                     "prompt_tokens_est": (len(base) + 2) // 3, "window": _win}],
                   None, None, target=target)
            shutil.rmtree(root, ignore_errors=True)
            return 2
        start = 1
        cp_path = CHECKPOINT_DIR / f"{label}--{_safe_model(a.model)}.json"   # dispatch_loop reads this
        if a.resume_checkpoint:
            st = load_checkpoint(a.resume_checkpoint)
            if st and st.get("label") == label and st.get("model") == a.model:
                start, tries, convo = st["n"], st["tries"], st["convo"]
                opened.update(st["opened"])
                shown_to.update(st["shown_to"])
                budget = st["budget"]
                budget_requested = budget
                say(f"  RESUMED from {a.resume_checkpoint}: {len(tries)} attempt(s) already "
                    f"made, continuing at attempt {start}")
            else:
                say(f"  NOTE: checkpoint {a.resume_checkpoint} is not this run's "
                    f"({(st or {}).get('label')}/{(st or {}).get('model')}) -- starting over")
        say(f"  output budget {budget} tokens (window {_win})"
            + (f", requested {budget_requested}, sent {budget}, clamped: the "
               f"{OUTPUT_RESERVE_TOKENS}-token reserve leaves no more room"
               if budget != budget_requested else ""))
        import signal
        _LIVE.update(path=cp_path, root=root, state=None)
        signal.signal(signal.SIGTERM, _on_sigterm)
        for n in range(1, a.attempts + a.reads + 1):
            if n < start:
                continue
            _LIVE["state"] = {"n": n, "tries": tries, "convo": convo, "opened": opened,
                              "shown_to": shown_to, "budget": budget, "label": label,
                              "model": a.model}
            if n > start and yield_requested(a.model):
                write_checkpoint(cp_path, _LIVE["state"])
                say(f"\n  YIELDED -- {a.model} is wanted elsewhere; checkpointed after attempt "
                    f"{n - 1} to {cp_path}. Resume with --resume-checkpoint {cp_path}")
                shutil.rmtree(root, ignore_errors=True)
                return YIELDED
            if len([t for t in tries if t["outcome"] not in ("ASKED", "RESUMED")]) >= a.attempts:
                break
            f = [t for t in tries if t["outcome"] not in ("ASKED", "RESUMED", "ACCEPTED")]
            if len(f) > 1 and _fail_sig(f[-1]) == _fail_sig(f[-2]):  # 2 identical fails: stop
                say(f"  stopping: two identical {f[-1]['outcome']} failures")
                break
            say(f"  attempt {n} … ", end="")
            cut = None
            try:
                try:
                    raw, tok, secs = ask(convo, a.model, think=not a.no_think,
                                         max_tokens=budget, raise_on_length=True)
                except ConnectionError as e:          # RemoteDisconnected is one (ConnectionResetError)
                    # THE SERVER DROPPED THE CONNECTION (t348): RemoteDisconnected before any
                    # reply. That is a fact about the endpoint, not the model, and it ended the
                    # whole run as exit 1 -- counted against the brief. Retry this attempt once
                    # after a pause; a second drop ends the run as before, said plainly.
                    say(f"connection dropped ({type(e).__name__}) -- retrying this attempt once "
                        f"in 30 s … ", end="")
                    time.sleep(30)
                    raw, tok, secs = ask(convo, a.model, think=not a.no_think,
                                         max_tokens=budget, raise_on_length=True)
            except WindowFull as e:
                # Not retried: the next attempt carries the same prompt (or a longer one) into
                # the same window. Stop, and hand the reason upstream in the outcome itself.
                detail = (f"the prompt is {e.prompt_tokens} tokens of a {e.window}-token window, "
                          f"so only {e.window - e.prompt_tokens} were left for the answer and it "
                          f"used them all ({e.tokens}). A bigger output budget cannot help. "
                          "The brief must name the functions to edit so only those are shown "
                          "(scope schema: window-full).")
                say(f"WINDOW FULL ({e.prompt_tokens}+{e.tokens} of {e.window} tok, "
                    f"{e.seconds:.0f}s) -- stopping: {detail}")
                tries.append({"n": n, "tokens": e.tokens, "seconds": e.seconds,
                              "outcome": "WINDOW-FULL", "done_reason": "length",
                              "prompt_tokens": e.prompt_tokens, "window": e.window,
                              "detail": detail})
                break
            except Truncated as e:
                cut = e
                raw, tok, secs = (e.last.get("content") or ""), e.tokens, e.seconds
            except Exception as e:
                # THE SERVER WAS NOT THERE (2026-10-02): p242 asked at the second Ollama restarted
                # for Jas's slot change, got "Connection refused", exited 1 -- an ATTEMPT against
                # the brief -- and supervision parked the only live build as a harness bug. A
                # refused connection is the inference path not answering: wait for it (3 x 30 s),
                # and if it never comes back, refuse (exit 2), which counts against nothing.
                if _refused(e):
                    _wait = float(os.environ.get("CODER_REFUSED_WAIT_S") or 30)   # tests shorten it
                    for _ in range(3):
                        say(f"server not answering ({e}) -- waiting {_wait:.0f} s … ", end="")
                        time.sleep(_wait)
                        try:
                            raw, tok, secs = ask(convo, a.model, think=not a.no_think,
                                                 max_tokens=budget, raise_on_length=True)
                            break
                        except Exception as e2:                     # noqa: BLE001 -- said below
                            if not _refused(e2):
                                say(f"ERROR {type(e2).__name__}: {e2}")
                                return 1
                            e = e2
                    else:
                        say(f"REFUSING: the model server did not answer for {3 * _wait:.0f} s ({e}) -- "
                            "not an attempt; the scheduler retries on a later tick")
                        return 2
                else:
                    say(f"ERROR {type(e).__name__}: {e}")
                    return 1
            obj, err = parse(raw)
            if cut is not None and obj is None:
                # TRUNCATED, NOT UNUSABLE. Tier one: hand the reasoning back and ask for the
                # answer it was forming. Tier two, only if that fails: the next attempt gets
                # MAX_TOKENS_RETRY instead of the budget it just ran out of.
                say(f"truncated at the limit ({tok} tok, {secs:.0f}s) -- resuming from its "
                    f"reasoning … ", end="")
                raw2, tok2, secs2, note = finish_truncated(convo, a.model, cut.last)
                obj, err2 = parse(raw2)
                tries.append({"n": n, "tokens": tok, "seconds": secs, "outcome": "RESUMED",
                              "done_reason": "length",
                              "detail": f"{note}; finish call {tok2} tok, {secs2:.0f}s, "
                                        + ("parsed" if obj is not None else f"failed: {err2}")})
                if obj is not None:
                    raw, tok, secs = raw2, tok2, secs2
                else:
                    err = (f"truncated at {tok} tokens, and resuming from its own reasoning "
                           f"also failed: {err2}")
                    budget = retry_budget(convo, num_ctx=_win)
            if obj is not None and obj.get("need"):
                wanted, shown, refused = obj["need"][:4], [], []
                repeats = []      # NOT `already` -- that name holds the baseline suite list
                for rel in wanted:
                    if rel in opened:
                        # Re-asking usually means the excerpt did not contain what it needed.
                        # `numbered()` caps at 14,000 chars, so feedback.py (1,088 lines) was
                        # shown as lines 1-821 -- and t159's target sits at 1,078. The model
                        # asked for the same file seven times because the answer was in the
                        # part it could not see. Serving the identical window again is what
                        # made that a loop; serve the NEXT unseen slice instead.
                        more = unseen(root, rel, shown_to.get(rel, 0))
                        # THE SAME RESERVE AS A NEW FILE (2026-10-09, t1004). a0124af5 moved new files
                        # onto FILE_REPLY_RESERVE; this branch -- the REST of a file already opened --
                        # still checked against `budget`, every token the window held beside the starting
                        # prompt, so it refused every slice: ten builds parked as HARNESS-BUG on 10-08/09.
                        grown = len(base) + sum(len(v) for v in opened.values())
                        fits_more, _est, new_budget = slice_fits(
                            grown, len(more[0]) if more else 0, budget, _win)
                        if more and not fits_more:
                            refused.append(f"{rel}: no room for more of it beside the prompt."
                                           " Work from what you already have")  # t427: p47 grew to 61k
                            continue
                        if more:
                            opened[rel] += "\n" + more[0]
                            shown_to[rel] = more[1]
                            budget = new_budget
                            shown.append(rel + " (more)")
                            continue
                        # Re-asking for a file it already has is the loop signature. Say so,
                        # rather than serving the same bytes and producing the same prompt.
                        repeats.append(rel)
                        continue
                    if not in_scope(rel, a.files):
                        refused.append(f"{rel}: out of target -- this dispatch is scoped to {', '.join(a.files)}. Ask for one of those, or make the edit with what you have")
                        continue
                    fp, why = readable(root, rel)

                    if fp:
                        # THE SAME DOOR AS THE STARTING CONTEXT (2026-09-24). A requested file
                        # went through numbered() whole even when the brief named SYMBOLS: p50
                        # asked for coder.py, got all 124 KB, and filled the window (62,262 of
                        # 65,536) on its next attempt. excerpt() slices by the brief's symbols;
                        # and a file that still cannot fit beside the prompt is refused BY NAME
                        # rather than served into a window it will overflow.
                        body = excerpt(rel)
                        # FIT AGAINST A REPLY RESERVE, NOT THE WHOLE BUDGET (2026-10-08). first_budget()
                        # gives the reply every token the window holds beside the STARTING prompt, so
                        # checking a requested file against that budget refused everything -- a 7k-token
                        # file "too large" for a 262k window -- and builds looped on attempt 1. A file fits
                        # while a FILE_REPLY_RESERVE-token reply still does; the budget then shrinks to
                        # the room that is left.
                        total_now = len(base) + len(body) + sum(len(v) for v in opened.values())
                        fits_now, est_now = prompt_fits(total_now, min(budget, FILE_REPLY_RESERVE), _win)
                        if not fits_now:
                            refused.append(f"{rel}: too large to show beside the prompt "
                                           f"(~{est_now} tokens with it, window {_win}). "
                                           f"You have NOT been shown this file. Work from the brief and say {rel} was not available to you.")
                            continue
                        opened[rel] = f"--- {rel} ---\n" + body
                        shown.append(rel)
                        _req_now = min(budget, first_budget(total_now, _win))
                        _fit_now = fit_output_budget((total_now + 2) // 3, _win, _req_now)
                        if _fit_now is None:
                            refused.append(f"{rel}: showing it fills the {_win}-token window with no room for the {OUTPUT_RESERVE_TOKENS}-token answer reserve")
                            continue
                        budget = _fit_now
                    else:
                        refused.append(f"{rel}: {why}")
                say(f"asked for {len(wanted)} file(s), gave {len(shown)} new"
                    + (f", {len(repeats)} repeat" if repeats else "")
                    + f" ({tok} tok, {secs:.0f}s)")
                # WHY A FILE WAS NOT GIVEN goes in the log too, not only to the model (t437,
                # 2026-10-01). p56 asked for scripts/library_checks.py three times, got "gave 0
                # new" each time, and the log could not say whether it was too large, outside
                # the scope, or missing -- the reason existed only inside the prompt.
                for r in refused:
                    say(f"      refused: {r}")
                tries.append({"n": n, "tokens": tok, "seconds": secs, "outcome": "ASKED",
                              "detail": ", ".join(wanted)
                              + (" | refused: " + "; ".join(refused) if refused else "")})
                # EVERY file opened so far, not just this round's. Rebuilding from `base`
                # discarded the previous rounds, so a second request produced a prompt
                # byte-identical to the first -- and at temperature 0 that is deterministic,
                # so the run was GUARANTEED to repeat until the budget ran out. t159 spent six
                # attempts emitting the same 2355 tokens. The `opened` set was already here
                # and never read: the accumulation was intended and never wired.
                convo = ctx(("\n\nRefused: " + "; ".join(refused) if refused else "")
                            + ("\n\nYou already have " + ", ".join(repeats)
                               + " -- it is shown above. Asking for it again returns the same "
                               "text and wastes an attempt." if repeats else "")
                            + "\n\nNow make the edit, or ask for a file you have not seen.")
                continue
            if obj is None:
                say(f"unusable ({err}, {tok} tok, {secs:.0f}s)")
                # THE RAW REPLY IS KEPT, WHOLE (2026-09-24). p48's attempt 3 was "unterminated JSON
                # object, 11845 tok" and nothing else survived, so its cause could not be found.
                tries.append({"n": n, "tokens": tok, "seconds": secs, "outcome": "UNPARSEABLE",
                              "detail": err, "raw": raw})
                convo = ctx(f"\n\nYour last reply could not be parsed: {err}. Reply with "
                            "ONE JSON object and nothing else -- no prose before or after it.")
                continue

            for rel, text in baseline.items():
                p = root / rel
                if p.is_file():
                    p.write_text(text, encoding="utf-8")
            # The baseline is taken before any op exists, so a file outside SOURCE_EXT that
            # the model then edits has no `before` to compare against and would read as
            # changed when it was not. Capture the op paths' state immediately before the
            # write -- exact, and it needs no extension list.
            filled = fill_paths(obj["ops"], a.files)
            if filled:
                say(f"(filled {filled} op path(s) with the one allowed file) ", end="")
            pre = snapshot_paths(root, obj["ops"])
            ok, detail = apply_ops(root, obj["ops"])
            if not ok:
                say(f"refused ({detail}, {tok} tok, {secs:.0f}s)")
                tries.append({"n": n, "tokens": tok, "seconds": secs, "outcome": "REFUSED",
                              "detail": detail, "ops": obj.get("ops")})
                convo = ctx(f"\n\nYour last edit was refused: {detail}. The files above "
                         "are unchanged. Copy the anchor exactly from the listing, without "
                         "the line numbers, and keep it short.")
                continue

            # An op can apply cleanly and change nothing measurable. "ACCEPTED, 0 diff
            # line(s)" was reported on the first Android task -- a verdict of success for a
            # change that does not exist. The suite cannot catch this: nothing broke because
            # nothing happened.
            if not changed(root, {**baseline, **pre}, obj["ops"]):
                say(f"no-op ({detail} but the file is unchanged, {tok} tok, {secs:.0f}s)")
                tries.append({"n": n, "tokens": tok, "seconds": secs,
                              "outcome": "NO-OP", "detail": "applied but changed nothing",
                              "ops": obj["ops"]})
                convo = ctx("\n\nYour edit applied but left the file byte-identical -- "
                         "'old' and 'new' have the same effect. Make a change that actually "
                         "differs.")
                continue

            ok_py, py_err = py_syntax(root, obj["ops"])
            if not ok_py:
                say(f"python syntax ({tok} tok, {secs:.0f}s)")
                tries.append({"n": n, "tokens": tok, "seconds": secs,
                              "outcome": "BAD-SYNTAX", "detail": py_err,
                              "ops": obj["ops"]})
                feedback = (f"\n\nYou proposed:\n{json.dumps(obj['ops'])[:900]}\n\n"
                            f"It applied, but the Python no longer parses:\n{py_err}\n\n")
                for op in obj["ops"]:
                    if op.get("from") is not None:
                        rel = (op.get("path") or "").strip()
                        if rel:
                            rb = range_block(root, rel, op["from"], op.get("to", op["from"]))
                            if rb:
                                feedback += f"Context block:\n{rb}\n\n"
                                break
                feedback += "That change has been REVERTED. Fix the indentation and try again."
                convo = ctx(feedback)
                revert(root, baseline)
                continue

            # THE LESSONS OF THE 17 REJECTED BRANCHES (scripts/code_shape.py, 2026-10-01): a name
            # bound nowhere (coder/brief-3c1c1ede, a NameError on import) or a top-level
            # definition removed that the brief never names (coder/brief-b698d36b). Milliseconds,
            # so before the suites -- and said to the model like a syntax error.
            shape = []
            try:
                sys.path.insert(0, str(REPO / "scripts"))
                import code_shape
                for rel in sorted({(op.get("path") or "").strip() for op in obj["ops"]}):
                    if rel.endswith(".py") and (root / rel).is_file():
                        shape += [f"{rel}: {p}" for p in code_shape.regressions(
                            ({**baseline, **pre}).get(rel), (root / rel).read_text(
                                encoding="utf-8", errors="replace"), brief)]
            except Exception as e:                                  # noqa: BLE001
                say(f"  NOTE: code_shape check unavailable ({type(e).__name__}: {e}) -- skipped")
            if shape:
                say(f"code shape ({tok} tok, {secs:.0f}s)")
                tries.append({"n": n, "tokens": tok, "seconds": secs, "outcome": "BAD-SHAPE",
                              "detail": "; ".join(shape), "ops": obj["ops"]})
                convo = ctx(f"\n\nYou proposed:\n{json.dumps(obj['ops'])[:900]}\n\nIt parses, but:\n- "
                            + "\n- ".join(shape) + "\n\nThat change has been REVERTED. Import or "
                            "define every name you use, and do not remove code the task does not "
                            "name.")
                revert(root, baseline)
                continue

            ok_lib, lib_err = library_gate(root, obj["ops"], {**baseline, **pre})
            if not ok_lib:
                say(f"commit gate would refuse ({tok} tok, {secs:.0f}s)")
                tries.append({"n": n, "tokens": tok, "seconds": secs,
                              "outcome": "LIBRARY-BLOCK", "detail": lib_err,
                              "ops": obj["ops"]})
                convo = ctx(f"\n\nYou proposed:\n{json.dumps(obj['ops'])[:900]}\n\n"
                            f"It applied and parses, but:\n{lib_err}\n\n"
                            "That change has been REVERTED. Make the same change without the "
                            "finding.")
                revert(root, baseline)
                continue

            proj = android_project(root, area)
            if proj:
                ok_c, errs = compiles(proj)
                if not ok_c:
                    say(f"does not compile ({tok} tok, {secs:.0f}s)")
                    tries.append({"n": n, "tokens": tok, "seconds": secs,
                                  "outcome": "WONT-COMPILE",
                                  "detail": errs,
                                  "ops": obj["ops"]})
                    convo = ctx(f"\n\nYou proposed:\n{json.dumps(obj['ops'])[:900]}\n\n"
                             f"It applied, but the Kotlin no longer compiles:\n{errs[:1800]}"
                             "\n\nThat change has been REVERTED. Fix the cause and try "
                             "again.")
                    revert(root, baseline)
                    continue

            # `already` is the baseline: a suite red before the model touched anything is
            # not a surprise and needs no confirming re-run. Anything else is new, and a new
            # failure is exactly the claim worth confirming before it costs an attempt --
            # a flake here reports the model broke something it did not.
            failed, out = run_suites(root, declared=already)
            new_failures = [s for s in failed if s not in already]
            if target and target in failed:
                # It is allowed to be in the baseline -- that is the entry condition -- but
                # it is not allowed to still be red at the end.
                new_failures = [target] + [f for f in new_failures if f != target]
            if not new_failures:
                say(f"ACCEPTED ({detail}, {tok} tok, {secs:.0f}s)")
                tries.append({"n": n, "tokens": tok, "seconds": secs, "outcome": "ACCEPTED",
                          "ops": obj["ops"]})
                break
            say(f"tests fail ({len(new_failures)}: "
                  f"{', '.join(s.split('/')[-1] for s in new_failures)}, {secs:.0f}s)")
            # A8, THE THIRD JOIN (2026-10-30): SAY THE CLASS. oracle_conflict.classify() has existed since dd1badeef
            # and could never be called, because it needs the patch's SYMBOLS and eval_ops carries paths; the symbol
            # extractor (patch_symbols) closed that. Changing a symbol WHILE breaking a suite that asserts something
            # about the same symbol means the brief asked for two incompatible things -- retrying spends a build to
            # regenerate a patch that cannot exist. So the class goes in the log, the failure schema reads this
            # marker, and escalate_failure routes it instead of sending it one rank up.
            try:
                import oracle_conflict as _oc, patch_symbols as _ps
                _syms = _ps.working_tree(root)
                _collateral = [f for f in new_failures if f != target]
                if _syms and target and _collateral:
                    _v = _oc.classify(_syms, [target], _collateral)
                    if _v.get("class"):
                        say(f"{_v['class'].upper()} on {_v.get('symbol')}: "
                            f"the target requires it while {', '.join(x.split('/')[-1] for x in _v.get('suites') or _collateral)} "
                            f"asserts otherwise -- a patch that satisfies one breaks the other, so this is NEVER retried")
                        tries[-1:] = tries[-1:]            # (the attempt row below carries the class via the log)
            except Exception as _e:                        # noqa: BLE001 -- said, not swallowed
                say(f"NOTE: could not classify the failure ({type(_e).__name__}: {_e})")
            # WHY, NOT ONLY THAT (t1337): the job log said which suites failed and never why, so the
            # supervisor and every reader after it judged a TESTS-FAILED blind. Each failing suite's
            # output goes into the log whole, between markers a reader can find.
            for s_ in new_failures:
                say(f"--- {s_.split('/')[-1]} output (whole) ---\n{out.get(s_, '(no output captured)').rstrip()}\n"
                    f"--- end {s_.split('/')[-1]} ---")
            tries.append({"n": n, "tokens": tok, "seconds": secs, "outcome": "TESTS-FAILED",
                          "broke": new_failures, "ops": obj.get("ops"),
                          "output": "\n".join(out[x] for x in new_failures[:2])})
            # THE BYTES GO TO DISK BEFORE THE REVERT (t1123, 2026-10-24). The suite output
            # above says what broke; only the applied bytes say what the model WROTE, and the
            # next line -- and the sandbox delete after the loop -- are what destroy them. A
            # copy costs 24 KB and makes the attempt diffable against a byte-for-byte oracle
            # long after the sandbox is gone.
            try:
                _dest = (REPO / "workspace" / "run-logs" / "attempt-bytes"
                         / f"{label}-a{n}-{int(time.time())}")
                _kept = preserve_attempt_bytes(
                    root, sorted({(op.get("path") or "").strip() for op in obj.get("ops") or []}
                                 - {""}), _dest)
                if _kept:
                    say(f"  preserved {len(_kept)} applied file(s) before the revert: {_dest}")
            except OSError as e:                                     # noqa: BLE001
                say(f"  NOTE: this attempt's bytes were NOT preserved ({e})")
            convo = ctx(f"\n\nYou proposed:\n{json.dumps(obj['ops'])[:1200]}\n\n"
                     f"It applied cleanly but broke {len(new_failures)} test suite(s). "
                     f"Output:\n" + "\n".join(out[s][-700:] for s in new_failures[:2])
                     + "\n\nThat change has been REVERTED; the files are exactly as listed "
                       "above. Use the failure to work out what is actually wanted.")
            revert(root, baseline)

        # Past the attempts: an interrupt now would checkpoint work that is already finished, so it
        # only cleans up (the sandbox) and exits.
        signal.signal(signal.SIGTERM, _on_sigterm_cleanup)
        _LIVE["state"] = None
        if cp_path.is_file():
            cp_path.unlink()             # this run got to the end; its checkpoint is spent
        accepted = tries and tries[-1]["outcome"] == "ACCEPTED"
        say("\n" + "-" * 72)
        if not accepted:
            record(a.task, a.model, tries[-1]["outcome"] if tries else "NO-ATTEMPTS",
                   tries, None, None, target=target)
            say(f"  NOT ACCEPTED after {len(tries)} attempt(s). Nothing was written.")
            say(f"  last: {tries[-1]['outcome'] if tries else 'no attempts'} — "
                  f"{tries[-1].get('detail', '') if tries else ''}")
            return 1

        patch = diff_of(root, baseline, obj["ops"])
        say(f"  ACCEPTED on attempt {len(tries)}: {obj.get('why', '')}")
        # `already` is the BASELINE failure set, measured before the agent touched
        # anything, so this printed a PRE-PATCH fact as a POST-PATCH claim. A patch whose
        # whole purpose was to turn the target suite green still counted that suite as
        # failing, and the headline number under-reported the very thing the run achieved.
        # `failed` is the post-patch set from the accepted attempt, which is what the
        # sentence has always claimed to be reporting. Found by the env3 probe (finding 31).
        say(f"  {len(patch.splitlines())} diff line(s), "
              f"{len(suites(root)) - len(failed)} suite(s) green\n")
        say(patch[:4000])

        if a.apply:
            ok, branch, detail = write_branch(label, obj["ops"], a.model, pre, brief=brief)
            say(f"\n  {detail}")
            if not ok:
                # `ok` USED TO BE DISCARDED. The run then recorded ACCEPTED with a branch
                # name, printed the failure detail, and returned 0 -- so a change that was
                # never written was indistinguishable, to reviewq.py and to a human reading
                # the ledger, from one that was. Found by the env3 probe, round 26, and
                # confirmed on the real code path with a sabotaged worktree.
                #
                # The verdict on the CHANGE was ACCEPTED and that was true. What failed is
                # the delivery, so say exactly that rather than relabelling the judgement:
                # NOT-WRITTEN is a distinct outcome, and the exit code has to carry it or
                # every caller that trusts `returncode == 0` inherits the false claim.
                say("  The change was accepted but NOT WRITTEN. Nothing is waiting for "
                    "review, and no branch carries this diff.")
                record(a.task, a.model, "ACCEPTED-NOT-WRITTEN", tries, patch,
                       obj.get("why"), None, target=target)
                return 3
            record(a.task, a.model, "ACCEPTED", tries, patch, obj.get("why"), branch, target=target)
        else:
            record(a.task, a.model, "ACCEPTED-DRYRUN", tries, patch, obj.get("why"), target=target)
            say("\n  Dry run. Re-run with --apply to put this on a branch for review.")
        return 0
    finally:
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
