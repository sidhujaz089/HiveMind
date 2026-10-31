#!/usr/bin/env python3
"""The parts of workspace-mcp that are worth testing, with no protocol in them.

Kept separate from the server so every rule below can be exercised by a fixture
without standing up a transport. server.py is a thin shell over this.

TWO THINGS THIS MODULE EXISTS TO GET RIGHT

**The call log.** Every call is appended to workspace/mcp-calls.jsonl by this code,
before and after execution, so a call that raises still leaves a record. That is the
whole point: it is written by the server, not by the agent making the call, which
makes it the first record here that the thing being described does not author
(L-039). check_chat.py already reads this exact path.

**Path safety is not reimplemented.** chat_agent.resolve_repo_path was verified
against escapes, drive letters and UNC prefixes by fixtures that were made to fire
(L-042). A second implementation is a second thing to drift, so this calls that one.
"""

import contextvars
import hashlib
import json
import os
import subprocess
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from chat_agent import resolve_repo_path  # noqa: E402  -- one implementation, not two

CALL_LOG = "workspace/mcp-calls.jsonl"

# Deny by default, mirroring docs/ACCESS.md. These are prefixes relative to the repo.
#   workspace/private/  holds the protocol whose validity depends on the auditor never
#                    having read it (docs/ACCESS.md).
#   .git/            history is git's to manage, not a text file to edit.
#   .venv/           machinery, not content.
DENIED_PREFIXES = ("workspace/private/", ".git/", ".venv/")

# Directories search never descends into. Not a security boundary -- DENIED_PREFIXES is that
# -- just noise. Chat reported a `.csv` search returning hits from
# projects/app-repo/.venv/lib/python3.12/site-packages, and that gets worse with every
# project added.
SEARCH_NOISE = ("/.venv/", "/node_modules/", "/site-packages/", "/__pycache__/",
                "/.git/", "/build/", "/.gradle/", "/dist-packages/", "/.mypy_cache/",
                "/.pytest_cache/", "/index_cache/", "/model_cache/")

# Extensions search reads by default. The old default was "**/*.md", which reached 229 files
# and COULD NOT SEE THE CORPUS AT ALL -- chat/ is 261 .txt files against 2 .md. A new agent
# using the default got a plausible `scanned` count and zero hits from the very material the
# bridge exists to serve, which is the failure that looks like an answer.
SEARCH_TEXT_EXT = (".md", ".txt", ".json", ".jsonl", ".csv", ".py", ".yaml", ".yml",
                   ".toml", ".cfg", ".ini", ".sh", ".kt", ".java", ".xml", ".html")

# Excluded from the DEFAULT search only, and reachable by asking for it explicitly.
# chat/export/raw/ is the frozen Anthropic dump the processed transcripts derive from
# (Chat's handover section 8: "history, not an input"). Searching it returns raw
# `data-<uuid>-...` blobs rather than the readable transcript, which is worse than no hit --
# it is a plausible-looking wrong answer.
#
# NOT hidden: search(q, glob="chat/export/raw/**/*") still reaches it. The distinction
# matters, because a corpus you cannot reach at all is a different decision from one merely
# out of the way, and only the second is mine to make.
SEARCH_DEFAULT_SKIP = ("chat/export/raw/",)

# Readable, never writable, on either transport (D-26, 2026-08-19).
#   chat/            the family corpus. D-22 denied it outright on the stated grounds that
#                    no Chat workflow needed it through this server. That was written
#                    before anyone checked, and it was wrong:
#                    chat/projects/family/INSTRUCTIONS.txt has loaded its ledger from this
#                    corpus since 2026-08-13. Drive was quietly covering that path, and
#                    D-24 removes Drive -- so the denial that looked harmless becomes the
#                    thing that strands the Family Project.
#
# The corpus is Chat's memory, which is why this is read and search rather than a copy.
# Writing is a different question and gets a different answer: nothing may modify the
# record it is remembering from. inbox_post remains the way to ask.
READ_ONLY_PREFIXES = ("chat/",)

# The tools that modify. Kept as a list rather than inferred, because a new writing tool
# added later must be added here deliberately -- the failure mode of inferring it is that
# the new tool is writable everywhere by default and nobody notices.
WRITING_TOOLS = ("write", "replace", "inbox_post", "append")

# Never served regardless of location, in case one turns up outside the repo root.
DENIED_NAMES = (".env", "anthropic.env", "id_ed25519", "credentials.json", "token.json")

MAX_READ_BYTES = 1_000_000
LIST_MAX = 500        # a directory listing is a signpost, not a payload

# append() is the per-turn capture path (Chat's handover §4, approved by Jas 2026-08-21).
# It is the only writing tool with no read-modify-write, which is what makes it safe to call
# forty times a session -- but that also means nothing bounds a file except this.
APPEND_ROOT = "workspace/"          # narrower than the repo: append cannot touch chat/ or docs/
MAX_APPEND_BYTES = 256_000          # one turn of text, generously
MAX_APPEND_FILE = 20_000_000        # a runaway caller must hit a wall, not fill the disk

# ----------------------------------------------------------------------------------------
# CAPABILITY BY TRANSPORT
#
# The same server serves two very different callers, and they do not need the same tools.
#
#   stdio  -- me, on this machine, already able to run bash and edit files directly. A tool
#             restriction here would be theatre.
#   http   -- Claude Chat, arriving over a tunnel from Anthropic's network. Reachable, in
#             principle, by anything that gets through the edge.
#
# So http gets a REDUCED set, and the reduction is not a policy an agent could talk its way
# past -- a tool absent from the registry cannot be called.
#
# This maps onto ROADMAP §2, decided months ago for unrelated reasons: "Chat proposes, Code
# writes and verifies." Chat never needed write access; it needed a way to ASK. inbox_post
# is that way, and it is the only thing over http that changes a byte.
#
# Why it matters more than auth: authentication decides WHO gets in. This decides what they
# can do once in -- which is the half that still applies after a credential leaks.
# ----------------------------------------------------------------------------------------

# WHY EACH WITHHELD TOOL IS WITHHELD, so a caller can tell a DECISION from a GAP.
#
# Chat asked for this on 2026-08-31: "visibility into whether a given restriction is
# D-26-style intentional or just not-yet-built, since right now I can't tell the two apart
# from the tool list alone." It is right, and the difference matters -- one is a boundary to
# respect and the other is work to ask for. An absent tool looks identical either way, so
# the honest thing is to say which.
#
# Kept beside HTTP_TOOLS deliberately: a reason that lives away from the list it explains
# drifts from it (L-114, L-115).
WITHHELD = {
    "execute": ("not built", "No caller has this, including stdio. There is no execute tool "
                             "in this server at all."),
}


STDIO_TOOLS = ("read", "write", "replace", "search", "inbox_post", "inbox_list", "status",
               "append", "recall", "why", "dispatch_coder", "live_status",
               "my_tasks", "task_get", "task_note", "task_assign",
               "symindex_find", "skills_find")
# dispatch_coder added 2026-09-07. It is NOT in HTTP_TOOLS, so the tunnelled server Chat uses
# is unchanged by default; the narrow loopback instance selects it with --tools. Exposing a
# dispatch where `write` is withheld is deliberate: coder.py is the gate (sandbox, suite must
# go green, review branch, Jas merges), so the model proposes and the tests decide.
# write and replace added 2026-08-31 (`t194`). Jas: "You and Code are the same mind, so
# you should have the same power in the HiveMind." They are gated, not merely granted: every
# write commits through scripts/hooks, carries `Agent: claude-chat/opus-5`, and rolls back if
# the hook refuses. That is the same enforcement Code passes, on this door.
HTTP_TOOLS = ("read", "search", "inbox_post", "inbox_list", "status", "append",
              "recall", "why", "write", "replace", "live_status",
              "my_tasks", "task_get", "task_note", "task_assign",
              "symindex_find", "skills_find")
# symindex_find and skills_find added 2026-10-05 (t671): search parity -- `recall` was served, the other
# two finders in session_start's index of indexes were not. Both READ-ONLY, both logged.
# The four task tools added 2026-10-03 (t662). Jas plans in Chat's voice mode while driving and
# needs his tasks by number. They write only through taskstore (its lock, QUEUE.md, the Notes
# section), never a raw edit, and a note is signed Claude - Chat, never Jas (L-091).
# live_status added 2026-09-24. Jas: "Can Chat see the status tab of the Hub? If not can you
# enable visibility of the hub through the MCP?" It could not -- `status` is PROJECT-INDEX.md,
# a generated summary, and the Hub's Status tab runs hivestatus.py for 2+ minutes per load.
# This serves what scripts/lane_status.py prints: builds today with an ETA, every running job
# (pid, model, brief, elapsed, recent tok/s), the GPU and the resident models. READ-ONLY: it
# runs the same function Jas's terminal does, so the two views cannot drift.

# `recall` and `why` added 2026-08-26 (`D-32`). Chat could already READ memory.jsonl and
# graph.json; it could not QUERY them, so it had the facts and not the traversal and would
# have had to reimplement ranking and graph-walking over 443 raw edges on every question.
#
# What they widen: NOTHING that read does not already permit. Both are pure functions over
# files Chat can already fetch -- they return the same bytes in a more useful order. Neither
# writes, neither takes a path, and neither can reach chat/ or anything outside the two data
# files they read. That is why they are on both transports rather than stdio only: the
# read/write asymmetry that governs `append` does not apply to a query that changes nothing.

# `append` is the SECOND thing over http that changes a byte, added 2026-08-21, and widening
# this line deserves its reason recorded next to it rather than in a commit message.
#
# What it does NOT widen: it cannot overwrite, cannot read-modify-write, cannot reach chat/,
# and cannot reach anything outside workspace/ -- a positive allowlist, not "everything except
# chat/". So the corpus is untouched and D-26 holds: Chat writes to a staging path, Code
# sweeps into the corpus, and Chat still cannot rewrite what it remembers from.
#
# What it does widen, stated plainly: a leaked credential can now grow files under workspace/
# until it hits 20 MB each. That is noise to clean up, not a loss -- append cannot destroy an
# existing byte, which is the property that makes this acceptable where `write` is not.
#
# The alternative was Chat continuing to use inbox_post for per-turn capture, which produces
# one server-named file per turn -- forty a session -- and buries real messages among them.

# Read-only over http is not enough on its own: read is how a corpus leaves. The paths
# denied below are the other half, and they are denied on BOTH transports.



# ----------------------------------------------------------------------------------------
# WHO CALLED (G11)
#
# The tool functions run deep inside the MCP SDK and never see the HTTP request. Until now
# that meant tool-call rows carried no caller at all, while http-auth and oauth rows did --
# so attribution rested on matching timestamps by eye, which is inference dressed as record.
#
# A contextvar carries it instead. The ASGI guard sets it per request; the tool call runs in
# that request's task and inherits it. Nothing is threaded through the SDK.
#
# TWO THINGS THIS DELIBERATELY DOES NOT DO.
#
# It does not identify callers by IP. Everything from Chat arrives through Cloudflare inside
# 160.79.104.0/21, shared by every Anthropic egress -- an address there distinguishes
# "Anthropic" from "not Anthropic" and nothing finer. The CREDENTIAL is the identity that
# matters: an OAuth token maps to a registered client, the static bearer means someone with
# shell access on this machine. Both are recorded; the credential is the useful one.
#
# And it does not guess. If the contextvar is unset -- stdio, a background task, anything
# that did not come through the guard -- the row says so rather than inheriting whatever the
# previous request left behind. A wrong attribution in an audit log is worse than an absent
# one, which is G8 exactly: a guard that held while the log misdescribed why.
CALL_CONTEXT = contextvars.ContextVar("call_context", default=None)


def set_call_context(client=None, via=None):
    """Called once per authenticated request, by the ASGI guard. Returns the token so the
    caller can reset it -- contextvars are task-scoped, but resetting makes that explicit
    rather than relying on it."""
    return CALL_CONTEXT.set({"client": client or "?", "via": via or "?"})



# THE SUBJECT IS DERIVED FROM THE PATH, AND THE CALLER CANNOT INFLUENCE IT.
# That is deliberate -- a generated subject cannot be gamed and always matches what
# actually changed -- but the first version spent 36 of the 72 characters on fixed
# text ("docs(workspace): " + " via the MCP bridge") and put the WHOLE path in the
# rest. Any path longer than 36 characters was therefore unwritable through the
# bridge, and Chat hit it immediately on an ordinary dated filename:
#
#   workspace/capability-probe-2026-09-14.txt   -> 77 chars -> REJECTED
#
# Worse than the limit was the error, which said "the subject line is 77 characters;
# keep it to 72 or fewer" -- blaming a commit message the caller never wrote and
# could not change, for a cause (path length) it never named. Reported by Claude
# Chat 2026-09-14 after two refusals on two unrelated file contents, which is what
# proved the subject was generated rather than read from the content.
#
# The fix drops the decorative tail and falls back to the file NAME when the full
# path will not fit. Shortening is never silent: the full path goes in the body on
# its own line, and the body says the subject was shortened and why. A declared
# limit is a constraint; a silent one is the defect.
SUBJECT_MAX = 72


def _subject(rel, verb):
    """(subject, was_shortened) for a bridge commit. Never returns an over-long one.

    Tried in order: the whole path, then the file name, then the file name cut with
    an explicit marker. The caller puts the full path in the body in every case, so
    the record is complete even in the last one.
    """
    prefix = f"{verb}(workspace): "
    room = SUBJECT_MAX - len(prefix)
    if len(rel) <= room:
        return prefix + rel, False
    name = rel.rsplit("/", 1)[-1]
    if len(name) <= room:
        return prefix + name, True
    # A file name alone longer than ~55 characters. Cut it, and SAY it is cut --
    # an ellipsis in a subject is a claim that something is missing, which is true.
    return prefix + name[:room - 1] + "\u2026", True


class Denied(Exception):
    """A refusal that names its rule, so the log says why and not merely that."""

    def __init__(self, rule, detail=""):
        super().__init__(f"{rule}: {detail}" if detail else rule)
        self.rule = rule
        self.detail = detail


class Workspace:
    def __init__(self, root, caller="unknown", log_path=None, served=None):
        self.root = Path(root).resolve()
        self.caller = caller
        self.log_path = Path(log_path) if log_path else self.root / CALL_LOG
        # WHAT IS ACTUALLY REGISTERED, not what the constant declares.
        #
        # Claude Chat, 2026-09-01: "status composing its list from a declaration rather than
        # from the registered transport ... if it is the second, status is itself a check
        # nobody has watched fail, which is worse than this one bug." It was the second, and
        # it is right that this is the worse half.
        #
        # status() returned `list(HTTP_TOOLS)` -- the intent. A caller asking "what can I
        # do?" got the constant, so any drift between the constant and the registry would be
        # invisible in exactly the place someone looks to find it. The server passes the
        # names it actually kept after removal; nothing else may set this.
        self.served = tuple(served) if served else None

    # -- the log -------------------------------------------------------------------
    def log(self, tool, target, outcome, detail=None, extra=None):
        """One JSON object per line, appended as the call happens.

        Written even when the call is refused or raises. A log that only records
        successes describes a system that never fails, which is not this one.
        """
        rec = {
            "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "caller": self.caller,
            "tool": tool,
            "target": target,
            "outcome": outcome,
        }
        ctx = CALL_CONTEXT.get()
        if ctx:
            rec["client"] = ctx["client"]
            rec["via"] = ctx["via"]
        else:
            # Not "unknown by default" -- explicitly stated, so a reader can tell the
            # difference between a call that had no context and a field someone forgot.
            rec["via"] = "direct"
        if detail:
            rec["detail"] = detail
        if extra:
            rec.update(extra)
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.log_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except OSError:
            # A failure to log must not silently become a successful call. Refuse.
            raise Denied("call-log-unwritable", str(self.log_path))
        return rec

    # -- path resolution ------------------------------------------------------------
    def resolve(self, relative, tool):
        ok, path, rule = resolve_repo_path(self.root, relative)
        if not ok:
            self.log(tool, str(relative), "refused", rule)
            raise Denied(rule, str(relative))

        rel = Path(path).relative_to(self.root).as_posix()
        if any(rel == d.rstrip("/") or rel.startswith(d) for d in DENIED_PREFIXES):
            self.log(tool, rel, "refused", "path-denied")
            raise Denied("path-denied", rel)
        if tool in WRITING_TOOLS and any(
            rel == d.rstrip("/") or rel.startswith(d) for d in READ_ONLY_PREFIXES
        ):
            self.log(tool, rel, "refused", "path-read-only")
            raise Denied("path-read-only", rel)
        if Path(rel).name in DENIED_NAMES:
            self.log(tool, rel, "refused", "name-denied")
            raise Denied("name-denied", rel)
        return Path(path), rel

    # -- tools ------------------------------------------------------------------------
    def read(self, path):
        p, rel = self.resolve(path, "read")
        if p.is_dir():
            # A READ TOOL THAT CANNOT SEE A DIRECTORY TEACHES THE MODEL THE DIRECTORY IS
            # ABSENT. console.py fixed exactly this on 2026-08-31 -- the chat agent called
            # read_file("workspace/inbox"), got "no such file", and burned two of its eight
            # tool rounds hunting a directory that was in front of it. THE LESSON NEVER
            # REACHED HERE, and on 2026-09-06 it cost the same failure again on a different
            # surface: asked to count the task queue, chatui's Qwen called
            # read("workspace/tasks"), got not-a-file, and reported -- correctly and
            # honestly -- that it could not answer. Two implementations of one idea, and
            # only one of them had learned.
            #
            # Directories are listed rather than refused. `replace` keeps its own not-a-file
            # guard below: reading a directory is a question, replacing one is a mistake.
            kids = sorted(p.iterdir(), key=lambda k: (not k.is_dir(), k.name))
            shown = kids[:LIST_MAX]
            listing = "\n".join(
                "  " + k.name + ("/" if k.is_dir() else f"    {k.stat().st_size} bytes")
                for k in shown)
            more = f"\n  [{len(kids)} entries, showing {len(shown)}]" \
                if len(kids) > len(shown) else ""
            self.log("read", rel, "ok", extra={"kind": "directory", "entries": len(kids)})
            return {"path": rel, "kind": "directory", "count": len(kids),
                    "entries": [k.name + ("/" if k.is_dir() else "") for k in shown],
                    "content": f"DIRECTORY {rel} \u2014 {len(kids)} entries:\n{listing}{more}"}
        if not p.is_file():
            # "not-a-file" was accurate when directories were refused above. Now that they
            # are LISTED, the only way to reach here is a path that is not there at all --
            # and the stale wording actively misled a model on 2026-09-06: asked to count
            # the queue, Qwen called read("tasks") (the real path is workspace/tasks), was
            # told "not-a-file", and reported that the directory could not be listed. It
            # had the right idea and the wrong path, and the error confirmed the wrong
            # diagnosis. An error message is a tool result too.
            self.log("read", rel, "refused", "no-such-path")
            raise Denied("no-such-path", f"{rel} -- nothing at that path. Check the path "
                                         f"with search, or read the parent directory.")
        size = p.stat().st_size
        if size > MAX_READ_BYTES:
            self.log("read", rel, "refused", f"too-large:{size}")
            raise Denied("too-large", f"{rel} is {size} bytes")
        raw = p.read_bytes()
        text = raw.decode("utf-8", errors="replace")
        digest = hashlib.sha256(raw).hexdigest()
        # The hash is of the bytes ON DISK, and it is logged as well as returned. Two uses,
        # and the second is the one that matters.
        #
        # For the caller it is a fixity value it can quote back. Note what it does NOT do:
        # a model cannot compute a hash over its own context, so this does not let Chat
        # self-verify a transfer. Asking it to would be asking for an estimate dressed as a
        # measurement -- which is the flaw Chat identified in a test I wrote (L-073).
        #
        # For the workspace it makes a read AUDITABLE AFTER THE FACT. Every read row now
        # carries the state of the file at the moment it was read, so a later session can
        # ask: has anything Chat read since changed underneath it? That is G4/G5 -- the
        # assertion from a remembered read -- turned from a claim about a model's context
        # into a file comparison.
        self.log("read", rel, "ok", extra={"bytes": size, "sha256": digest})
        return {"path": rel, "bytes": size, "sha256": digest, "content": text}


    def symindex_find(self, name):
        """Where `name` is defined and which files reference it, from the repository's AST index."""
        import symindex
        defs, refs = symindex.find(name)
        self.log("symindex_find", name, "ok", extra={"defs": len(defs), "refs": len(refs)})
        return {"name": name, "defs": defs, "refs": refs}

    def skills_find(self, query, limit=5):
        """The skills that cover `query` -- the same keyword+vector ranker `recall` uses, skills only."""
        import skills
        hits = skills.find(query, limit=limit)
        self.log("skills_find", query, "ok", extra={"count": len(hits)})
        return {"query": query, "count": len(hits),
                "results": [{"id": h.get("id"), "fact": h.get("fact"), "see": h.get("see"),
                             "tags": h.get("tags")} for h in hits]}

    def _commit(self, rel, verb):
        """Commit a write, through the same hook every other author passes.

        THIS IS THE GATE, and it is the whole reason write is available over HTTP now.
        My objection to parity was never about trust -- it was that everything making Code's
        work checkable is attached to how Code RUNS: the commit-msg hook, an Agent trailer,
        a revertable commit. A write arriving over MCP had none of it. Jas's answer was to
        put the same enforcement on this door rather than keep the door shut, and he was
        right; this is that.

        What it buys, concretely: every change Chat makes is attributable to
        `Agent: claude-chat/opus-5`, appears in `git log`, and can be undone with
        `git revert`. The hook is the SAME one that rejected four of my own commit messages
        today -- not a copy of its rules, the file itself, via core.hooksPath.

        A failed commit ROLLS THE FILE BACK. A change that cannot be recorded must not
        survive: an unattributable edit sitting in the working tree is exactly the state the
        gate exists to prevent, and it would be found by the next session as mystery drift.
        """
        subject, shortened = _subject(rel, verb)
        msg = (f"{subject}\n\n"
               f"Written by Claude Chat through the workspace MCP server.\n"
               f"Path: {rel}\n"
               + (f"(The subject carries the file name only: the full path is "
                  f"{len(rel)} characters and a 72-character subject cannot hold "
                  f"it. Nothing was dropped -- the path is on the line above.)\n"
                  if shortened else "")
               + f"Reviewable with: git show -- {rel}\n\n"
               f"Agent: claude-chat/opus-5\n")
        env = {**os.environ, "GIT_AUTHOR_NAME": "Claude Chat",
               "GIT_COMMITTER_NAME": "Claude Chat"}
        add = subprocess.run(["git", "-C", str(self.root), "add", "--", rel],
                             capture_output=True, text=True, env=env, timeout=60)
        if add.returncode:
            return False, (add.stderr or "git add failed").strip()[:300]
        c = subprocess.run(["git", "-C", str(self.root), "-c", "core.hooksPath=scripts/hooks",
                            "commit", "-F", "-"], input=msg,
                           capture_output=True, text=True, env=env, timeout=900)
        if c.returncode:
            # ROLLING BACK A NEW FILE IS NOT THE SAME AS ROLLING BACK AN EDIT. `checkout --`
            # restores a TRACKED path from HEAD; for a file that did not exist before, there
            # is nothing to restore and it survives on disk -- rejected, unattributed, and
            # waiting to be found as mystery drift. Caught by the assertion written for
            # exactly this, which is why the assertion was written before the code was
            # trusted. Ask git whether HEAD has it, and delete rather than restore if not.
            subprocess.run(["git", "-C", str(self.root), "reset", "-q", "HEAD", "--", rel],
                           capture_output=True, text=True, timeout=60)
            known = subprocess.run(["git", "-C", str(self.root), "cat-file", "-e",
                                    f"HEAD:{rel}"], capture_output=True, text=True, timeout=60)
            if known.returncode == 0:
                subprocess.run(["git", "-C", str(self.root), "checkout", "--", rel],
                               capture_output=True, text=True, timeout=60)
            else:
                (self.root / rel).unlink(missing_ok=True)
            return False, (c.stdout + c.stderr).strip()[:300]
        sha = subprocess.run(["git", "-C", str(self.root), "rev-parse", "--short", "HEAD"],
                             capture_output=True, text=True, timeout=60).stdout.strip()
        return True, sha

    def write(self, path, content):
        """Write, then RE-READ and report what is actually on disk.

        ROADMAP §4(a) made structural: "wrote it" stops being a claim and becomes a
        measurement. The hash returned is of the bytes read back, not of the bytes sent.
        """
        p, rel = self.resolve(path, "write")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        back = p.read_bytes()
        digest = hashlib.sha256(back).hexdigest()
        matched = back.decode("utf-8", errors="replace") == content
        ok, detail = self._commit(rel, "docs")
        self.log("write", rel, "ok" if (matched and ok) else "mismatch",
                 extra={"bytes": len(back), "sha256": digest, "commit": detail})
        if not ok:
            raise Denied("not-committed", f"{rel}: {detail}")
        return {"path": rel, "bytes": len(back), "sha256": digest,
                "readback_matches": matched, "commit": detail}

    def append(self, path, text):
        """Append one chunk to a file under workspace/. Creates it if absent.

        Chat's handover section 4, approved by Jas 2026-08-21. It exists so Chat can log
        one JSON object per turn as the turn happens, rather than reconstructing a session
        at the end from decayed context. `inbox_post` could technically do it and would
        produce one server-named file per turn -- forty files a session.

        WHY NO READ-MODIFY-WRITE. Append-only means two concurrent chats cannot lose each
        other's lines, and a caller cannot rewrite what it wrote earlier. That second
        property is the important one: the log is evidence about Chat, and Chat should not
        be able to edit its own evidence after the fact.

        THE SERVER STAMPS THE TIME AND ORDER. Chat asked for this explicitly and was right
        to -- it has miscounted its own declared totals three times in one session, and the
        2026-08-21 addendum records a turn it reported sending that produced no call at all.
        A sequence the caller declares is a claim; arrival order recorded by the receiver is
        an observation.

        SCOPED TIGHTER THAN CHAT ASKED. It requested workspace/ only and refusal on chat/.
        Refusing chat/ falls out of READ_ONLY_PREFIXES already, but "everything except
        chat/" would still include docs/, scripts/ and STATE.md. An append-only tool aimed
        at STATE.md is a slow-motion corruption nobody would notice, so the allowlist is
        positive: under workspace/, or refused. D-26 is preserved exactly -- Chat writes to
        a staging path and Code sweeps into the corpus; the corpus stays something Chat
        remembers FROM and cannot rewrite.
        """
        p, rel = self.resolve(path, "append")
        if not (rel.startswith(APPEND_ROOT) and rel != APPEND_ROOT.rstrip("/")):
            self.log("append", rel, "refused", "append-outside-workspace")
            raise Denied("append-outside-workspace", rel)
        if not isinstance(text, str) or not text.strip():
            self.log("append", rel, "refused", "empty-text")
            raise Denied("empty-text", rel)
        data = text if text.endswith("\n") else text + "\n"
        encoded = data.encode("utf-8")
        if len(encoded) > MAX_APPEND_BYTES:
            self.log("append", rel, "refused", "append-too-large",
                     extra={"bytes": len(encoded)})
            raise Denied("append-too-large", rel)
        existing = p.stat().st_size if p.is_file() else 0
        if existing + len(encoded) > MAX_APPEND_FILE:
            self.log("append", rel, "refused", "append-file-full",
                     extra={"bytes": existing})
            raise Denied("append-file-full", rel)

        p.parent.mkdir(parents=True, exist_ok=True)
        # "a" is O_APPEND: the kernel positions at end-of-file per write, so concurrent
        # callers interleave whole lines instead of overwriting one another.
        with open(p, "a", encoding="utf-8") as fh:
            fh.write(data)
        size = p.stat().st_size
        self.log("append", rel, "ok",
                 extra={"bytes": len(encoded), "file_bytes": size,
                        "sha256": hashlib.sha256(p.read_bytes()).hexdigest()})
        return {"path": rel, "appended_bytes": len(encoded), "file_bytes": size,
                "received_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}

    def replace(self, path, old, new):
        """Anchored edit. The anchor must match EXACTLY once.

        Zero means the file is not what the caller thought; more than one means the
        edit is ambiguous. Both refuse. Same rule as chat_agent, and it exists because
        applying a delta by hand on 2026-08-14 had six anchors match once and a seventh
        match zero, the canary having rotated since the delta was written.
        """
        p, rel = self.resolve(path, "replace")
        if not p.is_file():
            self.log("replace", rel, "refused", "not-a-file")
            raise Denied("not-a-file", rel)
        if not old:
            self.log("replace", rel, "refused", "empty-anchor")
            raise Denied("empty-anchor", rel)
        text = p.read_text(encoding="utf-8", errors="replace")
        hits = text.count(old)
        if hits == 0:
            self.log("replace", rel, "refused", "anchor-not-found")
            raise Denied("anchor-not-found", rel)
        if hits > 1:
            self.log("replace", rel, "refused", f"anchor-not-unique:{hits}")
            raise Denied("anchor-not-unique", f"{rel} matches {hits} times")
        i = text.index(old)
        p.write_text(text[:i] + new + text[i + len(old):], encoding="utf-8")
        back = p.read_bytes()
        digest = hashlib.sha256(back).hexdigest()
        line = text[:i].count("\n") + 1
        ok, detail = self._commit(rel, "fix")
        self.log("replace", rel, "ok" if ok else "not-committed",
                 extra={"line": line, "bytes": len(back), "sha256": digest,
                        "commit": detail})
        if not ok:
            raise Denied("not-committed", f"{rel}: {detail}")
        return {"path": rel, "line": line, "bytes": len(back), "sha256": digest,
                "commit": detail}

    def search(self, query, glob="**/*", limit=40):
        """Literal substring search over tracked-ish text files. Case-insensitive.

        The returned `scanned` count is not decoration. A glob ending in `**` matches
        DIRECTORIES in pathlib and no files at all, so `workspace/outbox/**` -- which looks
        exactly like a correct scoping -- used to return zero hits and outcome "ok". That is
        indistinguishable from "searched it, found nothing", and Chat drew the wrong
        conclusion from it on 2026-08-19: that a committed file did not exist.

        Two fixes, because either alone still misleads. The glob is normalised so the
        intuitive form works, and the file count is reported so a search that examined
        nothing can never again look like a search that found nothing.
        """
        if glob.endswith("**"):
            glob = glob + "/*"
        needle = query.lower()
        hits = []
        scanned = skipped_noise = skipped_binary = skipped_archive = not_read = 0
        # THE COUNTS ARE TOTALS EVEN WHEN THE HITS ARE CUT (t142, 2026-10-02). The loop used to
        # BREAK at the limit, so scanned and every skipped_* counted only the files seen before the
        # cut and read as totals -- Chat's observation, confirmed by reading this function. Past
        # the limit the walk goes on, classifying without reading: not_read says how many
        # searchable files were never opened.
        full = False
        for p in sorted(self.root.glob(glob)):
            if not p.is_file():
                continue
            rel = p.relative_to(self.root).as_posix()
            if any(rel.startswith(d) for d in DENIED_PREFIXES):
                continue
            if any(s in "/" + rel for s in SEARCH_NOISE):
                skipped_noise += 1
                continue
            if glob in ("**/*", "**/*.md") and any(
                    rel.startswith(d) for d in SEARCH_DEFAULT_SKIP):
                skipped_archive += 1
                continue
            if p.suffix.lower() not in SEARCH_TEXT_EXT:
                skipped_binary += 1
                continue
            if full:
                not_read += 1
                continue
            scanned += 1
            try:
                for n, line in enumerate(
                    p.read_text(encoding="utf-8", errors="replace").split("\n"), 1
                ):
                    if needle in line.lower():
                        hits.append({"path": rel, "line": n, "text": line.strip()})
                        if len(hits) >= limit:
                            break
            except OSError as e:
                print(f"  NOTE: {rel} unreadable ({e}) -- not searched", file=sys.stderr)
                continue
            if len(hits) >= limit:
                full = True
        self.log("search", query, "ok",
                 extra={"glob": glob, "scanned": scanned, "hits": len(hits),
                        "skipped_noise": skipped_noise, "skipped_binary": skipped_binary,
                        "skipped_archive": skipped_archive})
        # skipped_* is reported for the same reason `scanned` is: a caller must be able to
        # tell "searched everything, found nothing" from "quietly did not look there".
        return {"query": query, "glob": glob, "scanned": scanned, "hits": hits,
                "skipped_dependency_files": skipped_noise,
                "skipped_non_text": skipped_binary,
                # null, not 0, when the active glob cannot reach the frozen export at
                # all. A zero here is supposed to mean "looked, skipped nothing"; on a
                # glob that could never match chat/export/raw/ it meant "did not look",
                # which is the exact distinction this field exists to make. Chat caught
                # it reporting skipped_frozen_export: 0 on a **/*.md search (2026-08-25).
                "skipped_frozen_export": (
                    skipped_archive if glob in ("**/*", "**/*.md") else None),
                "truncated": len(hits) >= limit,
                "not_read_after_limit": not_read}

    def inbox_post(self, subject, body):
        """Write a message into workspace/inbox/ with a generated, collision-free name.

        The filename is generated here rather than accepted from the caller. Chat
        inventing paths is G1 in workspace/ROADMAP.md, and the fix recorded there is
        that Code owns all paths.
        """
        slug = re.sub(r"[^a-z0-9]+", "-", subject.lower()).strip("-")[:48] or "message"
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%SZ")
        name = f"{stamp}--chat--{slug}.md"
        p, rel = self.resolve(f"workspace/inbox/{name}", "inbox_post")
        p.parent.mkdir(parents=True, exist_ok=True)
        if p.exists():
            self.log("inbox_post", rel, "refused", "target-exists")
            raise Denied("target-exists", rel)
        p.write_text(f"# {subject}\n\n{body}\n", encoding="utf-8")
        back = p.read_bytes()
        self.log("inbox_post", rel, "ok",
                 extra={"bytes": len(back), "sha256": hashlib.sha256(back).hexdigest()})
        return {"path": rel, "bytes": len(back)}

    def dispatch_coder(self, brief, files, target=""):
        """Start a coder.py run in a sandbox. Returns the pid and the log path.

        THE GATE IS coder.py, NOT THIS METHOD, and that is the point. It never writes the
        working tree (everything happens in a /tmp copy), never commits, never pushes, never
        touches main, and keeps a change only if the target suite passes AND no other suite
        broke. What comes back is a branch, a diff and the test output for a human to merge.
        So dispatching is safe to expose where `write` is not: the model proposes, the suite
        decides, and Jas merges.

        Ported from tool_dispatch in projects/local-inference/console.py, which has run this
        way since the console gained the tool. It lives HERE now so a second surface does not
        become a second implementation -- the failure L-150 was written about.

        The files are checked through self.resolve(), so DENIED_PREFIXES, the read-only
        prefixes and the escape checks all apply. console.py hand-rolled three string tests
        (chat/, absolute, ..); resolve() is the same intent enforced in one place that the
        rest of the server already trusts.
        """
        brief = (brief or "").strip()
        if isinstance(files, str):
            files = [f.strip() for f in files.split(",") if f.strip()]
        files = [f for f in (files or []) if str(f).strip()]
        if not brief:
            self.log("dispatch_coder", "-", "refused", "empty-brief")
            raise Denied("empty-brief", "a dispatch needs a brief saying what to change")
        if not files:
            self.log("dispatch_coder", "-", "refused", "no-files")
            raise Denied("no-files", "a dispatch needs at least one file to work on")

        rels = []
        for f in files:
            # resolve() raises Denied for chat/, workspace/private/, escapes and absolutes.
            _p, rel = self.resolve(str(f), "dispatch_coder")
            if rel.startswith(READ_ONLY_PREFIXES):
                self.log("dispatch_coder", rel, "refused", "path-read-only")
                raise Denied("path-read-only", f"{rel} is read-only; a coder run edits files")
            rels.append(rel)

        coder = self.root / "projects" / "local-inference" / "coder.py"
        if not coder.is_file():
            # Defensive, like feedback.py's _reviewq(): a sibling project can move. Refusing
            # with the reason beats a traceback that reads as the server being broken.
            self.log("dispatch_coder", "-", "refused", "coder-missing")
            raise Denied("coder-missing", f"{coder.relative_to(self.root)} is not present")

        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%SZ")
        log_rel = f"workspace/console/dispatch-{stamp}.log"
        log_p = self.root / log_rel
        log_p.parent.mkdir(parents=True, exist_ok=True)
        # NEVER LET coder.py FALL BACK TO ITS DEFAULT MODEL NAME, "local". That is a
        # litellm gateway ROLE; Ollama has no model by that name and 404s every request,
        # which reads as a dead endpoint when the endpoint is fine and the NAME is wrong.
        #
        # console.model() is the ONE implementation of this rule, and its own docstring
        # records the defect being fixed in one caller and not carried to another --
        # L-114's shape. This port dropped `--model` on 2026-09-07 and reproduced the same
        # 404 on the very first dispatch, which is the THIRD caller to make it. So this
        # calls that function rather than copying it a third time.
        try:
            import sys as _s
            li = str(self.root / "projects" / "local-inference")
            if li not in _s.path:
                _s.path.insert(0, li)
            import console as _console
            model_name = (_console.model() or "").strip()
        except Exception as e:                                # noqa: BLE001
            self.log("dispatch_coder", ",".join(rels), "refused", "model-unresolved")
            raise Denied("model-unresolved",
                         f"could not resolve a model to dispatch with: "
                         f"{type(e).__name__}: {e}")
        if not model_name or model_name == "local":
            self.log("dispatch_coder", ",".join(rels), "refused", "model-unresolved")
            raise Denied("model-unresolved",
                         "no Ollama model is loaded or installed to dispatch with; "
                         "'local' is a gateway role and would 404 on every request")

        cmd = [sys.executable, str(coder), "--brief", brief, "--model", model_name,
               "--files", *rels, "--attempts", "3", "--apply"]
        if (target or "").strip():
            cmd += ["--target", target.strip()]
        with log_p.open("w") as fh:
            proc = subprocess.Popen(cmd, cwd=str(self.root), stdout=fh,
                                    stderr=subprocess.STDOUT, start_new_session=True)
        self.log("dispatch_coder", ",".join(rels), "ok",
                 extra={"pid": proc.pid, "log": log_rel, "model": model_name,
                        "brief": brief})
        return {
            "pid": proc.pid, "log": log_rel, "files": rels, "model": model_name,
            "target": target or "the existing suites",
            # Said out loud for the same reason console.py says it about escalate: a caller
            # that believes this finished will report it as done.
            "note": ("Dispatched, not finished. It runs in a sandbox and must make the "
                     "target suite pass before it delivers to a review branch. Nothing "
                     "merges without Jas. Call status to see it in flight."),
        }

    def inbox_list(self):
        d = self.root / "workspace" / "inbox"
        names = sorted(
            f.name for f in d.glob("*.md") if f.is_file() and f.name != "README.md"
        ) if d.is_dir() else []
        self.log("inbox_list", "workspace/inbox", "ok", extra={"count": len(names)})
        return {"messages": names}

    def live_status(self):
        """What is running NOW: scripts/lane_status.py's report, as text, plus the build count."""
        import contextlib
        import io
        scripts = str(self.root / "scripts")
        if scripts not in sys.path:
            sys.path.insert(0, scripts)
        import lane_status
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            lane_status.main()
        text = buf.getvalue()
        self.log("live_status", "scripts/lane_status.py", "ok", extra={"bytes": len(text)})
        return {"content": text, "builds_today": lane_status.build_progress(),
                "source": "scripts/lane_status.py -- the same report as the terminal"}

    # ---- TASKS, THROUGH TASKSTORE (t662, Jas 2026-10-03). He plans in Chat's voice mode while
    # driving: see what is assigned to him, look a task up by number, leave a note, hand it to
    # Production. Never raw edits: an MCP append lands after "## Attachments" (the Hub misreads
    # it) and a raw replace bypasses taskstore's lock and QUEUE.md. Every write goes through the
    # same functions the Hub form uses.
    TASK_ID = re.compile(r"^t\d{1,5}$")

    def _ts(self):
        """taskstore, pointed at THIS workspace's tasks (a test fixture's root is not the repo)."""
        app = Path(__file__).resolve().parents[2] / "projects" / "app-repo"
        if str(app) not in sys.path:
            sys.path.insert(0, str(app))
        import taskstore as ts
        tasks = self.root / "workspace" / "tasks"
        if ts.TASKS != tasks:
            ts.WORKSPACE, ts.TASKS, ts.DONE = self.root, tasks, tasks / "done"
            ts.QUEUE, ts.LOCK = tasks / "QUEUE.md", tasks / ".lock"
        return ts

    def _task_id(self, tool, task_id):
        tid = str(task_id or "").strip().lower()
        if tid.isdigit():
            tid = "t" + tid          # "662" spoken aloud arrives without the t
        if not self.TASK_ID.match(tid):
            self.log(tool, str(task_id)[:40], "refused", "bad-task-id")
            raise Denied("bad-task-id", f"{task_id!r} is not a task id like t662")
        return tid

    def my_tasks(self, who="jas"):
        """Every open task assigned to `who` (default Jas), in queue order: id, title, status."""
        ts = self._ts()
        who = ts.assignee(who)
        rows = [{"id": m["id"], "title": m.get("title", ""), "status": m.get("status", "")}
                for m, _ in ts.ordered()
                if m.get("status") not in ts.LEAVES_QUEUE
                and ts.assignee(m.get("needs", m.get("assigned"))) == who]
        self.log("my_tasks", f"assigned:{who}", "ok", extra={"count": len(rows)})
        return {"assigned": who, "count": len(rows), "tasks": rows}

    def task_get(self, task_id):
        """One task by number: its metadata and its whole body (notes included)."""
        tid = self._task_id("task_get", task_id)
        meta, body = self._ts().find(tid)
        if not meta:
            self.log("task_get", tid, "refused", "no-such-task")
            raise Denied("no-such-task", tid)
        self.log("task_get", tid, "ok")
        return {"id": tid, "title": meta.get("title", ""), "status": meta.get("status", ""),
                "assigned": self._ts().assignee(meta.get("needs", meta.get("assigned"))),
                "area": meta.get("area", ""), "kind": meta.get("kind", ""), "body": body}

    def task_note(self, task_id, text):
        """Append a note to the task's Notes section, signed as Claude - Chat.

        NOT IN JAS'S NAME, though he is the one speaking. taskstore refuses who='jas' from
        anything but the Hub form (L-091: notes written in his voice by a model were
        indistinguishable from his own). The marker says whose words they are; the signature
        says who wrote them down."""
        tid = self._task_id("task_note", task_id)
        text = (text or "").strip()
        if not text:
            self.log("task_note", tid, "refused", "empty-note")
            raise Denied("empty-note", "a note needs text")
        ok = self._ts().add_note(tid, "(via Chat, relaying Jas) " + text, who="claude-chat")
        if not ok:
            self.log("task_note", tid, "refused", "no-such-task")
            raise Denied("no-such-task", tid)
        self.log("task_note", tid, "ok", extra={"chars": len(text)})
        return {"id": tid, "noted": True, "signed": "Claude - Chat (via Chat, relaying Jas)"}

    def task_assign(self, task_id, who):
        """Move a task to production, jas, claude, or a model role on the Models tab."""
        tid = self._task_id("task_assign", task_id)
        ts = self._ts()
        who = ts.assignee(str(who or "").strip().lower())
        allowed = {ts.PRODUCTION, "jas", "claude"}
        try:
            scripts = str(Path(__file__).resolve().parents[1])
            if scripts not in sys.path:
                sys.path.insert(0, scripts)
            import role_store
            allowed |= set(role_store.load()["roles"])
        except Exception as e:                                      # noqa: BLE001 -- said, not swallowed
            print(f"  NOTE: role store unreadable ({type(e).__name__}: {e}); "
                  f"assignable: {sorted(allowed)}", file=sys.stderr)
        if who not in allowed:
            self.log("task_assign", tid, "refused", "bad-assignee")
            raise Denied("bad-assignee", f"{who!r}; choose from {sorted(allowed)}")
        meta, _ = ts.find(tid)
        if not meta:
            self.log("task_assign", tid, "refused", "no-such-task")
            raise Denied("no-such-task", tid)
        was = ts.assignee(meta.get("needs", meta.get("assigned")))
        ts.set_meta(tid, needs=who)
        self.log("task_assign", tid, "ok", extra={"from": was, "to": who})
        return {"id": tid, "from": was, "to": who}

    def status(self):
        """PROJECT-INDEX.md — the whole workspace in one file, for a phone."""
        p = self.root / "workspace" / "PROJECT-INDEX.md"
        if not p.is_file():
            self.log("status", "workspace/PROJECT-INDEX.md", "refused", "not-generated")
            raise Denied("not-generated", "run scripts/build_index.py")
        text = p.read_text(encoding="utf-8", errors="replace")
        self.log("status", "workspace/PROJECT-INDEX.md", "ok",
                 extra={"bytes": len(text.encode())})
        # WHAT YOU HAVE, AND WHY YOU DO NOT HAVE THE REST. Answering "is this refused or
        # unbuilt?" costs nothing here and saves a caller from either working around a
        # boundary it should respect, or silently accepting a gap it could have asked to
        # have closed.
        return {"content": text,
                "tools_available": list(self.served if self.served else HTTP_TOOLS),
                "tools_source": "the running registry" if self.served else
                                "HTTP_TOOLS — this process did not report its registry",
                "tools_withheld": {k: {"kind": v[0], "why": v[1]}
                                   for k, v in WITHHELD.items()}}
