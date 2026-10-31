#!/usr/bin/env python3
"""Checks the invariants this workspace's documentation claims are true.

This workspace has a documented, expensive failure mode: conclusions outrunning
their evidence (docs/LESSONS.md, L-015), and documents that were true when
written drifting away from what is on disk. Nothing checked for that drift, so
it accumulated silently between sessions.

This script is that check. Every test below corresponds to a claim some document
in this repository makes. If a test fails, either the world changed or the
document was wrong -- and either way somebody needs to look.

Run it at the START of a session (is the world as the last session left it?) and
again at the END, before the final commit.

    python3 scripts/check_workspace.py
    python3 scripts/check_workspace.py --quick

Exit code 0 = every check passed (warnings allowed).
Exit code 1 = at least one check FAILED.
Exit code 2 = the script could not run its checks at all.

Ported from check-workspace.ps1 (2,481 lines of PowerShell across the suite) when
the workspace moved from Windows to Linux/aarch64. Underscores rather than a
hyphen in the filename because Python cannot import a hyphenated module name.
"""

import ast
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pysource  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))

# --------------------------------------------------------------------------------------
# Expectations. These encode DECISIONS, not guesses. When a decision changes, change it
# here in the same commit as the change itself, and say why in docs/DECISIONS.md.
# --------------------------------------------------------------------------------------

# Are the ISO standards PDFs meant to be in version control?
#
#   'tracked'   -> the corpus is backed up by the git remote; PDFs must be tracked.
#   'untracked' -> the corpus is not in git, by decision (D-22).
#   'undecided' -> nobody has chosen yet. Warns every run, on purpose.
#
# 'untracked' by Jas's decision, 2026-08-12 -- see D-15 in docs/DECISIONS.md.
EXPECT_ISO_PDFS = "untracked"

# The live deployment of WifiWatchdog. It runs from outside this repo on purpose, so the
# two copies can silently diverge. See projects/wifi-watchdog/STATUS.md.
#
# wifi-watchdog stays on the Windows machine by decision -- it repairs that machine's
# Wi-Fi and is meaningless here. On Linux this path never exists and the check reports
# SKIP, which is the honest answer: not checked, rather than checked and clean.
WIFI_WATCHDOG_LIVE_DIR = r"C:\ProgramData\WifiWatchdog"


# A live Anthropic key exists on this machine, outside the repo. It must stay outside.
SECRET_PATTERN = (
    r"sk-ant-[A-Za-z0-9_\-]{20,}|AIza[0-9A-Za-z_\-]{35}|"
    r"ghp_[A-Za-z0-9]{36}|-----BEGIN [A-Z ]*PRIVATE KEY-----"
)
# Split so this source line is not itself key-shaped (the library check scans it). See scripts/hooks/pre-commit.
SECRET_PLACEHOLDER = "sk-ant-" + "ABCdefGH" + "123ijKLmnop456" + r"[A-Za-z0-9_-]*"

# Google-native files sync down as stubs that cannot be read at all, while still
# reporting a plausible size. See docs/LESSONS.md, L-005 and L-020.

# Directories that are large, generated, or both. Walking into them turns a two-second
# check into a multi-minute one. The cost here is FILE COUNT, not bytes.
PRUNED_DIRS = {
    ".git", "build", ".gradle", ".kotlin", "venv", ".venv", "node_modules",
    "__pycache__", ".cxx", ".idea",
    # ANOTHER COMMIT'S CHECKOUT IS NOT PART OF THIS TREE (2026-10-26, t4641). `.claude/worktrees/`
    # and `~/ai-workspace-dsh/` hold full checkouts of OTHER branches at OTHER commits. os.walk
    # reached them, so the basenames census in check_doc_references (:3490) contained names this
    # tree does not have: `check_workspace --quick` passed on the live checkout (62/9/0) and failed
    # on a fresh worktree of the same commit (57/10/1, 18 dangling references) only because EIGHT
    # stale worktrees still contained the three one-off audit tools deleted in 3869ac66b. A deleted
    # file "existing" in a directory that belongs to a different commit is how a green was bought.
    # The name, not ".claude": .claude/settings.json is this repository's own file.
    "worktrees",
}

# Lessons dated on or after this must declare how they are enforced (docs/CHECKS.md).
# Earlier ones predate the rule and are out of scope -- the D-20 precedent. A check that
# cannot be made to pass is one people learn to silence (AGENTS.md rule 9).
ENFORCEMENT_REQUIRED_FROM = "2026-08-17"

# Escapes that echo/printf consume inside a shell hook's message, silently changing the
# text a person reads. \a ate the path in pre-commit for weeks -- see L-045.
SHELL_EATEN_ESCAPES = ("\\a", "\\b", "\\f", "\\v", "\\r")

# Things that look like file references but are not, or that are absent on purpose.
# Keep this list short and justified -- every entry is a hole in the check.
NOT_FILE_REFS = {
    "org.json",   # a Gradle dependency coordinate (org.json:json), not a file on disk
    "usage.json",  # UsageWidget's live output, written at runtime and gitignored
}

# --------------------------------------------------------------------------------------

# ANSI colour, but only when stdout is a terminal. A hook pipes this, and escape codes in
# a log are noise.
_TTY = sys.stdout.isatty()


def _c(code, text):
    return f"\033[{code}m{text}\033[0m" if _TTY else text


OUTBOX_STALE_DAYS = 7

# A deferral written into prose is inherited as a sentence, not as work (t1092). The RCA
# this closes (workspace/rca/ac93114085d6.md) ended with "sweep.py and the end-to-end
# pipeline are still unprobed -- round 5's targets that ran out of budget", and every
# check in this file said PASS. The idiom is ordinary English, so only a phrase list sees it.
DEFERRAL_PHRASE = re.compile(r'(?i)\b(still unprobed|unprobed|untested|not tested|ran out of '
                             r'(?:the )?budget|out of budget|left for the next session|deferred to '
                             r'(?:the )?(?:next|a later) session|not yet '
                             r'(?:probed|tested|exercised|wired|covered)|no test (?:covers|exercises))\b')

# The token that turns a deferral into work. Counting ids PER PARAGRAPH is what lets a
# session silence the check by filing the task, which is the point -- a check that cannot
# be satisfied by the cure is --no-verify'd within a week.
TASK_ID = re.compile(r'\bt\d{3,}\b')


def deferral_findings(text):
    """Paragraphs of `text` naming more deferred items than they carry task numbers.

    Pure: no I/O, no print, no sys.path. A paragraph is the unit because that is where a
    writer puts the id next to the deferral. Counting over the whole file lets one tNNN in
    an unrelated section pay for a deferral three screens away, and lets two shortfalls
    cancel to a silent zero.
    """
    out = []
    for block in re.split(r'\n\s*\n', text):
        if not block.strip():
            continue
        items = []
        for sent in re.split(r'(?<=[.!?])\s+|\n', block):
            s = sent.strip()
            if s and s not in items and DEFERRAL_PHRASE.search(s):
                items.append(s)
        ids = []
        for mt in TASK_ID.finditer(block):
            if mt.group(0) not in ids:
                ids.append(mt.group(0))
        if len(items) > len(ids):
            # The block goes out WHOLE, whitespace-normalised. Slicing it would be cutting a
            # record: the item that needs the number is often the last one named, and a
            # truncated finding cannot be filed against (docs/skills/never-truncate-a-record).
            out.append({'block': re.sub(r'\s+', ' ', block).strip(),
                        'items': items,
                        'ids': ids,
                        'unaccounted': len(items) - len(ids)})
    return out

# The definition-of-done gate (t1042). A task file carries exactly one status, so closing it
# retires every subject its file holds -- and nothing compared a closure with the task's own
# definition of done. The marker is matched in PROSE because that is the only form on disk:
# every task file writes 'DEFINITION OF DONE' inside a bold dated note, and
# `grep -rliE '^#+ *definition of done' workspace/tasks/` returns no file at all. A gate keyed
# to a markdown heading would pass every task on disk.
DOD_MARKER = re.compile("DEFINITION OF DONE", re.IGNORECASE)
CLAUSE = re.compile(r"\(([a-z])\)")
CLOSED_STATUSES = {"done", "closed", "complete", "completed"}
# A named command a close note can be checked against. A clause answered only in prose has
# nothing left to re-check it (L-015: conclusions outrunning their evidence).
PATH_TOKEN = re.compile(r"[\w./-]+\.(?:py|md|sh|json|jsonl|txt)")


def dod_clauses(text):
    """The clauses of a task's own definition of done, as (letter, text) in document order.

    The block runs from the first line carrying the marker to the first blank line, so the
    clause list ends where the note ends -- not at the end of the file, where later notes (a
    close note citing its own clauses) would be swept in.
    """
    lines = text.splitlines()
    start = next((n for n, line in enumerate(lines) if DOD_MARKER.search(line)), None)
    if start is None:
        return []
    block = []
    for line in lines[start:]:
        if not line.strip():
            break
        block.append(line)
    body = " ".join(block)          # a clause that wraps across lines is still one clause
    marks = list(CLAUSE.finditer(body))
    out = []
    for n, m in enumerate(marks):
        end = marks[n + 1].start() if n + 1 < len(marks) else len(body)
        out.append((m.group(1), body[m.end():end].strip()))
    return out


def closure_carries_dod(path):
    """Findings for a CLOSED task whose close note cites no named command for a clause of its
    own definition of done. [] is the healthy answer: an open task, a task with no
    definition-of-done block, and a closure that names a file for every clause all stay silent.

    t244 is the instance on disk today (workspace/rca/52ed69168259.md): status: done, three
    clauses, and a close note verifying the title's defect alone -- clauses (b) and (c) were
    verified by nobody, and the lease obligation the task carried lost its carrier seven days
    later. The close note is the dated line stamped with the front-matter 'completed:' date, not
    the last bold line in the file: a note written after the close is not the evidence the
    closure was made on.
    """
    path = Path(path)
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    # Front matter = the text between the first pair of '---' lines, read as text: taskstore
    # writes it, and a YAML dependency here would fail on exactly the files this reads.
    fm = {}
    bounds = [n for n, line in enumerate(lines) if line.strip() == "---"]
    if len(bounds) >= 2:
        for line in lines[bounds[0] + 1:bounds[1]]:
            key, sep, val = line.partition(":")
            if sep and key.strip() and not line[:1].isspace():
                fm[key.strip()] = val.strip()
    if fm.get("status", "").lower() not in CLOSED_STATUSES:
        return []
    clauses = dod_clauses(text)
    if not clauses:
        return []
    stamp = fm.get("completed", "")[:10]
    close = ""
    for line in lines:
        if not line.startswith("**"):
            continue
        if stamp and line[2:12] != stamp:
            continue
        close = line
    cites_a_path = PATH_TOKEN.search(close) is not None
    m = re.match(r"(t\d+)--", path.name)
    tid = fm.get("id") or (m.group(1) if m else path.stem)
    findings = []
    for letter, clause in clauses:
        if f"({letter})" in close and cites_a_path:
            continue
        # Cut for the report, and SAY it was cut: a reader who cannot tell a truncated clause
        # from a short one has no way to see what the task actually asked for.
        shown = " ".join(clause.split())
        if len(shown) > 60:
            shown = shown[:60] + " ... (clause text cut at 60 chars)"
        findings.append(f"{tid} ({letter}): close note cited no named command for this clause "
                        f"-- {shown}")
    return findings



def hardcoded_assignees(repo):
    """['path:line: text'] for each `needs=` / `assigned=` given a role key as a string literal,
    outside scripts/tests/. Role keys are read from ROLES.json, so a new role is covered too."""
    repo = Path(repo)
    try:
        keys = list(json.loads((repo / "projects" / "local-inference" / "models" / "ROLES.json")
                               .read_text(encoding="utf-8"))["roles"])
    except (OSError, ValueError, KeyError) as e:
        return [f"ROLES.json unreadable ({type(e).__name__}: {e}) -- the org chart cannot be checked"]
    if not keys:
        return []
    pat = re.compile(r"""\b(needs|assigned)\s*=\s*["'](%s)["']""" % "|".join(map(re.escape, keys)))
    out = []
    files = list((repo / "scripts").glob("*.py")) + list((repo / "projects").rglob("*.py"))
    for f in sorted(files):
        if "/tests/" in str(f):
            continue
        try:
            lines = f.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError as e:
            out.append(f"{f.relative_to(repo)}: unreadable ({e})")
            continue
        for n, line in enumerate(lines, 1):
            if pat.search(line) and not line.lstrip().startswith("#"):
                out.append(f"{f.relative_to(repo)}:{n}: {line.strip()[:120]}")
    return out

# ======================================================================================
# PRODUCTION LIVENESS (2026-10-24)
#
# Is production actually delivering, and if not, why? The data was never missing: `tick`
# rows in workspace/dispatch-loop.jsonl carry running/started/skipped[{chunk,why}] and
# `finished` rows carry kind+accepted; workspace/run-logs/device-pause.jsonl says which
# devices a person or a guard has stopped. On 2026-10-24 nothing asserted over any of it, so
# a box with eleven refused dispatches, a paused device and ~20 parked chunks looked busy to
# every surface except a person running `dispatch_loop.py --dry-run` by hand.
#
# THE TWO WAYS THIS MUST NOT BE NOISE:
#   - never fail while a HUMAN stopped the machine (a pause flag, or every device paused by a
#     person, is PAUSED -- a pass that still names who and why);
#   - never fail while work is actually running, however long since the last delivery.
# AND THE ONE WAY IT MUST NOT BE A FALSE PASS: a box doing nothing for no declared reason is
# STALLED, including when a memory guard stopped it, because nobody chose that.
# ======================================================================================

# SIX HOURS: long enough that a queue ahead of the work or a slow build never trips it (those
# are OK by the "something is running" rule anyway), short enough to find a stall the same day.
LIVENESS_THRESHOLD_HOURS = float(os.environ.get("HIVEMIND_LIVENESS_HOURS") or 6)

# A LANE OLDER THAN THIS IS NOT RUNNING. The first real run found p487-t1090-flow-coverage
# "started and not finished" on a row from 2026-10-14 and reported OK on a box that had done
# nothing for ten days. That row is a real defect in the ledger, but it is not a live job.
LIVENESS_STALE_LANE_HOURS = float(os.environ.get("HIVEMIND_LANE_STALE_HOURS") or 12)

# A pause by anything in this set is the machine stopping ITSELF -- a fault, not a decision.
LIVENESS_AUTOMATIC_WHO = ("memory-guard",)
LIVENESS_PAUSED_ACTIONS = ("pause", "already-paused")


def _liveness_skip(what, e):
    """Say that something was not read. `pass` here would be the swallowed exception this
    workspace's own compliance oracle refuses, and on this check silence reads as health."""
    print(f"  NOTE: {what} ({type(e).__name__}: {e})", file=sys.stderr)


def _liveness_parse_at(at):
    """UTC datetime from a ledger's own stamp, or None. Never guesses a time zone."""
    if not at:
        return None
    try:
        return datetime.strptime(at.replace("+00:00", "Z"), "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc)
    except (ValueError, AttributeError):
        return None


def _liveness_age_hours(at, now):
    t = _liveness_parse_at(at)
    if t is None or now is None:
        return None
    return round((now - t).total_seconds() / 3600.0, 1)


def _liveness_jsonl(path):
    """Rows from a JSONL ledger, and how many lines were unreadable (0 is not the same as
    'could not open it' -- the caller reports both)."""
    rows, bad = [], 0
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    bad += 1
    except OSError as e:
        _liveness_skip(f"{path} could not be read", e)
    if bad:
        print(f"  NOTE: {bad} unreadable line(s) in {path}", file=sys.stderr)
    return rows


def liveness_active_device_pauses(device_rows):
    """{device: last row} for every device whose LAST action was a pause.

    The last action per device decides: a resume that arrives after a pause means the device
    is running again, and a check that only ever looks for the word "pause" reports a machine
    stopped hours after it was restarted.
    """
    last = {}
    for r in device_rows:
        dev = r.get("device")
        if dev:
            last[dev] = r
    return {d: r for d, r in last.items() if r.get("action") in LIVENESS_PAUSED_ACTIONS}


def liveness_running_lanes(dispatch_rows, now=None, max_age_hours=LIVENESS_STALE_LANE_HOURS):
    """(running, stale): started rows with no matching finished row, split by age.

    Keyed on (chunk, pid): two dispatches of one chunk in the same second is the documented
    reason the pid is in the row (coder.sandbox). A start older than `max_age_hours` is STALE
    -- the process is gone -- and is reported rather than believed.
    """
    if isinstance(now, str) or now is None:
        now = _liveness_parse_at(now) or datetime.now(timezone.utc)
    # COUNTED, NOT POPPED (t4386). A child can log its own terminal row before the parent logs the
    # start -- dispatch_loop.py:2652 writes the started row after Popen() returns -- so a single pass
    # that popped on every finished row spent that finish on a key which did not exist yet and left
    # the lane open for ever. That is exactly how p487-t1090-flow-coverage sat "never closed" from
    # 2026-10-14 to 2026-10-25 while every pid in the ledger was correct. Counting starts against
    # finishes per key cannot close the wrong lane, because it never pairs a finish with anything.
    starts, finishes, latest = {}, {}, {}
    for r in dispatch_rows:
        key = (r.get("chunk"), r.get("pid"))
        if r.get("event") == "started":
            starts[key] = starts.get(key, 0) + 1
            latest[key] = r
        elif r.get("event") == "finished":
            finishes[key] = finishes.get(key, 0) + 1
    open_lanes = {k: latest[k] for k, n in starts.items() if n > finishes.get(k, 0)}
    running, stale = [], []
    for row in open_lanes.values():
        age = _liveness_age_hours(row.get("at"), now)
        (stale if (age is None or age > max_age_hours) else running).append(row)
    return running, stale


def liveness_last_delivery(dispatch_rows):
    """The newest finished PRODUCTION row that was accepted, or None.

    `kind` is load-bearing: on 2026-10-24 the rca and assigned_items lanes kept completing all
    evening while production delivered nothing, so counting any accepted row would have called
    a stalled machine healthy. Rows without `kind` predate the field and count as production.
    """
    best = None
    for r in dispatch_rows:
        if r.get("event") != "finished" or not r.get("accepted"):
            continue
        if r.get("kind", "production") != "production":
            continue
        if best is None or (r.get("at") or "") > (best.get("at") or ""):
            best = r
    return best


def _liveness_skip_reasons(tick_row, limit=3):
    """The last tick's skipped chunks as counts of the REASON (not the chunk).

    By reason, because "eleven chunks parked" and "eleven chunks waiting on a paused device"
    are different facts with different fixes.
    """
    whys = Counter()
    for s in (tick_row or {}).get("skipped") or []:
        why = (s.get("why") or "no reason given").strip()
        whys[why.split(" (")[0].split(" -- ")[0][:90]] += 1
    return whys.most_common(limit), sum(whys.values())


def _liveness_pause_note(pause_flag, dispatch_rows):
    """(who, why, at) for a scheduler pause -- the flag if readable, the ledger if not."""
    if not (pause_flag and Path(pause_flag).exists()):
        return None
    try:
        note = json.loads(Path(pause_flag).read_text(encoding="utf-8"))
        return (note.get("who") or "unknown", note.get("why") or "no reason given", note.get("at"))
    except (OSError, ValueError) as e:
        _liveness_skip(f"the pause flag {pause_flag} is unreadable", e)
    paused_rows = [r for r in dispatch_rows if r.get("event") == "paused"]
    if paused_rows:
        r = paused_rows[-1]
        return (r.get("who") or "unknown", r.get("why") or "the flag is unreadable", r.get("at"))
    return ("unknown", "the pause flag is present but unreadable", None)


def _liveness_paused_flags(root):
    """{device: note} from the flags the scheduler itself obeys, and the whole-scheduler flag.

    THE FLAGS ARE THE STATE; THE LEDGER IS THE HISTORY (2026-10-24, and this check got it wrong
    first). It read workspace/run-logs/device-pause.jsonl, whose last row for gx10 was a memory
    guard's "already-paused" at 19:42 -- while both devices were in fact paused BY JAS at 19:37,
    the memory guard's row being a second opinion on a flag a person had already set. So the
    check called a deliberate pause a STALL and would have filed a task about a machine that was
    doing exactly what its owner told it. A log records what happened; a flag says what is true
    now. scheduler_commands.paused_devices() reads the same flags, and this must not become a
    second reader of the same log with its own idea of the answer.
    """
    out = {}
    run_logs = Path(root) / "workspace" / "run-logs"
    for f in sorted(run_logs.glob("device-paused-*.enabled")):
        dev = f.name[len("device-paused-"):-len(".enabled")]
        try:
            out[dev] = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            out[dev] = {"who": "?", "why": "flag unreadable"}
    return out


def liveness_verdict(dispatch_rows, device_rows, pause_flag, now=None,
                     threshold_hours=LIVENESS_THRESHOLD_HOURS, paused_devices=None):
    """The verdict: state, ok, one line, and the evidence it used.

    `now` takes a datetime or a ledger-format stamp: a caller has a clock, a test has a
    fixture.
    """
    if isinstance(now, str) or now is None:
        now = _liveness_parse_at(now) or datetime.now(timezone.utc)
    delivery = liveness_last_delivery(dispatch_rows)
    age = _liveness_age_hours((delivery or {}).get("at"), now)
    running, stale_running = liveness_running_lanes(dispatch_rows, now=now)
    # THE FLAGS FIRST. `paused_devices` is what the scheduler obeys; the ledger below is only
    # consulted to say who touched a device, and never to decide that it is paused.
    pauses = ({d: r for d, r in (paused_devices or {}).items()}
              if paused_devices is not None else liveness_active_device_pauses(device_rows))
    ticks = [r for r in dispatch_rows if r.get("event") == "tick"]
    last_tick = ticks[-1] if ticks else None
    reasons, skipped_total = _liveness_skip_reasons(last_tick)
    flag_note = _liveness_pause_note(pause_flag, dispatch_rows)
    human_pauses = {d: r for d, r in pauses.items()
                    if (r.get("who") or "") not in LIVENESS_AUTOMATIC_WHO}
    auto_pauses = {d: r for d, r in pauses.items()
                   if (r.get("who") or "") in LIVENESS_AUTOMATIC_WHO}

    stale_note = ""
    if stale_running:
        oldest = sorted(stale_running, key=lambda r: r.get("at") or "")[0]
        stale_note = (f" [{len(stale_running)} lane(s) never closed, oldest "
                      f"{oldest.get('chunk')} since {oldest.get('at')} -- not believed]")

    def out(state, ok, line):
        return {"state": state, "ok": ok, "line": line + stale_note, "age_hours": age,
                "last_delivery": delivery, "running": len(running),
                "stale_lanes": len(stale_running), "device_pauses": sorted(pauses),
                "reasons": reasons}

    if flag_note and flag_note[0] not in LIVENESS_AUTOMATIC_WHO:
        who, why, at = flag_note
        tail = (f"Nothing is expected to run. Last accepted production delivery {age}h ago."
                if age is not None else
                "Nothing is expected to run. No production delivery is on record.")
        return out("PAUSED", True,
                   f"PAUSED by {who} at {at or 'an unrecorded time'}: {why}. {tail}")

    if running:
        row = sorted(running, key=lambda r: r.get("at") or "")[-1]
        return out("OK", True,
                   f"working: {len(running)} job(s) started and not finished, newest "
                   f"{row.get('chunk')} since {row.get('at')}"
                   + (f"; last accepted production delivery {age}h ago" if age is not None
                      else "; no production delivery on record yet"))

    if age is not None and age <= threshold_hours:
        return out("OK", True,
                   f"production delivered {age}h ago ({delivery.get('chunk')} at "
                   f"{delivery.get('at')})")

    if pauses and set(pauses) == set(human_pauses):
        who = ", ".join(sorted({r.get("who") or "someone" for r in human_pauses.values()}))
        why = "; ".join(f"{d}: {r.get('why') or 'no reason given'}"
                        for d, r in sorted(human_pauses.items()))
        return out("PAUSED", True, f"every device is paused by {who} ({why})")

    if not dispatch_rows:
        return out("UNKNOWN", False,
                   "no dispatch ledger rows to read -- this is NOT a pass")

    why_bits = ", ".join(f"{n}x {w}" for w, n in reasons) or "the last tick recorded no skips"
    auto = ""
    if auto_pauses:
        auto = "; ".join(f"{d}: {r.get('who')} stopped it ({r.get('why')})"
                         for d, r in sorted(auto_pauses.items()))
        auto = f" [{auto}]"
    headline = (f"NO production delivery for {age}h" if age is not None
                else "no accepted production delivery on record")
    return out("STALLED", False,
               f"{headline}; nothing is running; the last tick "
               f"({last_tick.get('at') if last_tick else 'none'}) skipped {skipped_total} "
               f"chunk(s): {why_bits}{auto}")


def liveness_verdict_from(root):
    """liveness_verdict() over the ledgers in `root`.

    A CHECK MUST READ THE MACHINE, NOT ITS OWN CHECKOUT: a worktree's copy of
    workspace/run-logs/device-pause.jsonl was a day stale on 2026-10-24 and read as "every
    device paused by boot", while the live one said a person and a memory guard had paused
    them. check_workspace passes self.live for exactly this reason.
    """
    root = Path(root)
    return liveness_verdict(_liveness_jsonl(root / "workspace" / "dispatch-loop.jsonl"),
                            _liveness_jsonl(root / "workspace" / "run-logs" / "device-pause.jsonl"),
                            root / "workspace" / "run-logs" / "scheduler-paused.enabled",
                            now=datetime.now(timezone.utc),
                            paused_devices=_liveness_paused_flags(root))



# KNOWN PLACEHOLDERS, BY HASH -- ONE LIST FOR BOTH SCANS (2026-10-24).
#
# Each entry is a claim that the VALUE WAS CHECKED and is not a credential. The values
# themselves are not written here, because a file full of them would trip the very scan they
# explain; hashes are the whole point. A hash is added only after looking at the value's shape
# and where it came from, and the comment says which shape.
#
# WHY IT IS MODULE-LEVEL AND WHY BOTH SCANS USE IT. This list used to be a local inside
# check_secrets(), applied by the served scan and by NOTHING ELSE -- so the tracked scan, the
# one whose silence is trusted most, stayed red on the same test values for two days
# (t1563). A filter that only one of two scans honours is a defect that reads as a
# disagreement between checks.
KNOWN_FAKE = {
    # test values in old Chat conversations, checked 2026-09-30 against the live key: none is it
    "03c83dea116523dedeee9cce5f0ec8d105930a4a40086edb68a24d7ac89099ab",
    "071cb7075a10156908e21cc0f3c4f3c7bd60a1f72bbb5d717e5a2f6886d25962",
    "323fcc6c0faacc31635f696830a008defeae475319926ac6fa39eab354c12b56",
    "49e7237e11464693589bca95fe317f2fde5793bb7d70da9749f55641a5fff406",
    "9cc213bb430575b7d16557a0980a51f5880ca5ae1931284dd27a731cb392b8cc",
    "09470624c3498414d7a35fadab80f30e3ba2cb6415336ab7d5d6fe7cc735f9ba",   # sk-ant-FAK...
    # REPAIRED 2026-10-24: this hash was 63 hex characters, so it never matched anything. The
    # entry has been inert since it was written -- a declared fake that could not cover its own
    # value. Its value is `sk-ant-` + 26 A's, which is what the corrected hash is of.
    "87627f11f859d708bb2bfd6dba1e36477f64a8bb1466179701b0ec5ad041b2fd",   # sk-ant- + A x26
    "7e507ea27a7fcee999c8ce2ad8304dac057ae905e0627a553f3f895203e55d10",   # sk-ant-ABC... (D-38 placeholder)
    # added 2026-10-24 (t1563), each inspected before being declared: every one is a run of
    # one repeated character, the literal FAKEFAKE, or the documented ABCdefGH placeholder,
    # written by scripts/tests/test_task_sessions_redaction.py and by the two probes that
    # looked for the live key on 2026-10-23. None is a credential.
    "8c69bb98dfc4c5ca3fcd56a5c63894604927c67456ff18d803e0ef72c2d94eb8",   # sk-ant- + A x30
    "7671003419c53c1ae817d800d50fc05b2c5e6cfdeebe11d00db577070c451c99",   # sk-ant- + A x30 + backslash
    "f17f4002c25932de388b8e41dc5ec805008969ea875ad631180cad6e53e19809",   # sk-ant- + ABCdefGH...#1
    "789d190bc64fb15eae7f707f33aa254bfb342be1aa409178dbdb6c99245698a5",   # sk-ant-FAKEFAKE x4
    "b4bbd68229ed8c20ff66e00827deae77dbb2a9f4b8c2477a185533475aae79dd",   # sk-ant-FAKEFAKE x3 +
    "96910c24673ba7d67ffb610c1bf6e598d84514ff9b310d443d086e22f53a8ccb",   # sk-ant-FAKEFAKE x4 + trailing
    "c1212bbf2dcb0ba81fdccd9e75cba06cbd1754e6e41a6853102308eaa8fb54fb",   # sk-ant-FAKEFAKE truncated
}


def only_known_fakes(line):
    """True when EVERY secret-shaped match on `line` is a declared fake.

    `all`, not `any`: a line holding a declared fake AND an undeclared value must still be
    reported. An allowlist that accepts a line because something on it was known is a hole
    with a declaration in front of it.

    HASHED FROM THE RAW MATCH, NOT FROM THE PLACEHOLDER-STRIPPED LINE (2026-10-24, second
    attempt). The first version stripped SECRET_PLACEHOLDER before matching -- which is right
    for deciding whether a hit EXISTS (a line carrying only the documented D-38 example is not
    a hit) and wrong for deciding whether it is allowed, because stripping the placeholder
    leaves nothing to hash and every such line read as "undeclared". The two questions are
    different questions; this function answers the second one.
    """
    matches = re.findall(SECRET_PATTERN, line)
    if not matches:
        return False
    return all(hashlib.sha256(t.encode("utf-8", "replace")).hexdigest() in KNOWN_FAKE
               for t in matches)


# ---------------------------------------------------------------------------------------
# THE TEST-FAILURE CEILING MAY FALL AND MUST NOT RISE QUIETLY (t1589, 2026-10-24)
#
# `workspace/test-failures-ceiling.json` allows 457 of the 914 suites to fail. Measured on
# 2026-10-24: 457 distinct names, none absent from scripts/tests/, and NOTHING refused growth --
# scripts/library.py appends a red suite by itself, and so do the bridge and its trigger. The
# only direction the number could move on its own was up.
#
# This is the mechanism AGENTS.md already has for lessons: an absolute ban cannot be made to
# pass and gets silenced, so the count is committed and may only fall -- or rise by editing the
# number on purpose, in a commit whose subject says so. See check_enforcement.py for the same
# shape, one file over.
CEILING_COUNT = Path(__file__).resolve().parent.parent / "workspace" / "test-failures-ceiling-count.json"


def ceiling_report(names, ratchet=None, existing=None):
    """(count, ratchet, over, headroom, groups, absent) for a ceiling list.

    `over` is how far the list has GROWN past the committed count (0 or None when it has not);
    `headroom` is how far it could be ratcheted down today. `groups` is the same number split
    by the suite's own name, because a total says how much debt there is and never where.
    """
    names = list(dict.fromkeys(names))
    groups = Counter()
    for n in names:
        parts = Path(n).stem.split("_")
        groups[parts[1] if len(parts) > 2 and parts[0] == "test" else "other"] += 1
    absent = sorted(n for n in names if existing is not None and n not in existing)
    over = None if ratchet is None else max(0, len(names) - ratchet)
    headroom = None if ratchet is None else max(0, ratchet - len(names))
    return {"count": len(names), "ratchet": ratchet, "over": over, "headroom": headroom,
            "groups": groups.most_common(), "absent": absent}


def _ceiling_names(path):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    names = data.get("allowed_to_fail") if isinstance(data, dict) else None
    return names if isinstance(names, list) else None


def _ceiling_ratchet(path=CEILING_COUNT):
    try:
        return int(json.loads(Path(path).read_text(encoding="utf-8")).get("count"))
    except (OSError, ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------------------
# A DSH PROFILE'S TRUSTED HOSTS MUST REACH THE LAUNCHER (2026-10-25)
#
# The DSH fences /api and /connect on the Host header: a non-loopback authority must be in
# `trustedHosts` (packages/client/connection/src/api-request-trust.ts). The EFFECTIVE list is the
# launcher's -- packages/bundle/web-app/cordis.patch.yml sets
# `trustedHosts: !!js ctx.webStartup.trustedHosts`, i.e. the --trusted-host flags -- and a user
# profile patch REPLACES a row's config rather than adding to it.
#
# So ~/.dsh/profiles/web/cordis.patch.yml named hivemind.example.ts.net and the browser still
# got 403 'forbidden' on every request, which the UI renders as "reconnecting" forever. Nothing
# compared the two lists. This does, and it is the whole point: a mechanism, not a paragraph.
def trust_gap(profile_hosts, launcher_hosts):
    """Authorities the PROFILE trusts that the LAUNCHER does not pass.

    A default port is normalised away on both sides, because `Host: name` and a profile entry of
    `name:443` are the same authority to the fence. Extra trust on the launcher line is not a gap:
    the unit routinely trusts the LAN address that the profile never names.
    """
    def norm(h):
        h = (h or "").strip().lower()
        return h[:-4] if h.endswith(":443") else h

    have = {norm(h) for h in launcher_hosts if (h or "").strip()}
    return [h for h in profile_hosts if (h or "").strip() and norm(h) not in have]


def _profile_trusted_hosts(patch_path):
    """The `trustedHosts:` list under `web-runtime:` in a profile patch, as written.

    A light reader on purpose: these files are YAML with `!!js` tags, and a YAML parser would have
    to be taught the tags to read one key. The list shape here is stable and commented in place.
    """
    try:
        text = Path(patch_path).read_text(encoding="utf-8")
    except OSError:
        return None
    out, inside, key_indent = [], False, 0
    for line in text.splitlines():
        stripped = line.strip()
        indent = len(line) - len(line.lstrip())
        if not inside and stripped.startswith("trustedHosts:"):
            inside, key_indent = True, indent
            continue
        if not inside:
            continue
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("- "):
            item = stripped[2:].strip().strip('"\'')
            # A host is one token with no space and no colon-space pair; "- insert:" and
            # "- id: x" are mapping items and end the list.
            if indent <= key_indent or " " in item or item.endswith(":"):
                break
            out.append(item)
        elif indent <= key_indent:
            break
    return out


def _launcher_trusted_hosts(exec_start):
    """Every `--trusted-host <a>` / `--trusted-host=<a>` value in a unit's ExecStart."""
    out = []
    for line in (exec_start or "").splitlines():
        parts = line.split()
        for i, tok in enumerate(parts):
            if tok == "--trusted-host" and i + 1 < len(parts):
                out.append(parts[i + 1].strip("'\""))
            elif tok.startswith("--trusted-host="):
                out.append(tok.split("=", 1)[1].strip("'\""))
    return out


# ---------------------------------------------------------------------------------------
# THE CEILING IS A BUDGET, NOT A POLICY (t1889, 2026-10-25)
#
# Jas: "Make the burn down budget code and not policy. Policies will be broken. If the code is a
# problem, I would rather change it later."
#
# The committed count (t1589) refuses a RISING number, but a number can be raised by a person and
# scripts/library.py appends a red suite to the list by itself. A budget is a comparison of two
# lists: a new failing suite may enter only if another one leaves. Swap, or fall.
#
# What it cannot see is a swap of a real suite for a fake one -- so the check prints the added and
# removed names on every run, and a swap is named rather than silent.
def ceiling_budget(previous, current):
    """{"added": [...], "removed": [...], "verdict": fell|swap|same|grew, "count_now": n}."""
    prev, cur = set(previous or ()), set(current or ())
    added, removed = sorted(cur - prev), sorted(prev - cur)
    count_now = len(cur)
    if not prev:
        # Nothing to compare against: a first list is the baseline, not growth. Said as "same" so a
        # fresh checkout does not report a budget breach it cannot prove.
        verdict = "same"
    elif not added:
        verdict = "fell" if removed else "same"
    elif len(added) <= len(removed):
        verdict = "swap"
    else:
        verdict = "grew"
    return {"added": added, "removed": removed, "verdict": verdict, "count_now": count_now}


def ceiling_honesty(names, verdicts):
    """Of the suites ALLOWED to fail, which are not failing (t7519). Pure.

    `verdicts` is {suite: exit_code} for the LATEST recorded run of each suite. Three cases, and the
    distinction between them is the point -- each has a different repair:

      green      -- it passes, so the list overstates the debt: remove it from allowed_to_fail
      unrecorded -- nothing proves it fails: run it before deciding anything about it
      red        -- correctly declared: nothing to say

    2026-10-26: 102 of the 456 were green. The count was checked (t1589) and the HONESTY of the list
    was not, so the overstatement was invisible -- a green suite on the list fails nothing and warns
    about nothing, and every report that reads the list inherits the number.
    """
    if verdicts is None:
        # A CALLER WITH NO VERDICTS IS A BUG, NOT AN ALL-CLEAR (the vacuous pass, docs/CHECKS.md).
        raise TypeError("ceiling_honesty needs a {suite: exit_code} mapping; None would read as clean")
    wanted = list(names or ())
    green = sorted(n for n in wanted if n in verdicts and verdicts[n] == 0)
    unrecorded = sorted(n for n in wanted if n not in verdicts)
    red = sum(1 for n in wanted if n in verdicts and verdicts[n] != 0)
    return {"green": green, "unrecorded": unrecorded, "red": red}


def delivery_faults(repo):
    """[(task, at)] for a build that was ACCEPTED and that no branch ever carried.

    The coder records `ACCEPTED-NOT-WRITTEN` in workspace/agent-runs.jsonl -- its own words for "the
    change was accepted but NOT WRITTEN" (coder.py). L4 found nineteen tasks held after one failed
    attempt and seven of them this fault; measured on 2026-10-28 there were 46 tasks that had ever
    hit it, of which 32 were still open and never delivered. A task that reached done, or that a
    LATER accepted run with a branch delivered, is not a fault any more.

    Why a check and not a re-queue: a re-queue puts the task back into the same pipeline in the same
    state, and if the cause was delivery it fails the same way. The fault has to be visible the moment
    it happens, which is the difference between a number nobody can act on and one that names the
    task. The `faketest` model is excluded on purpose: those rows are a suite writing the LIVE ledger,
    which is its own defect (noted in the 2026-10-28 handover), not a delivery fault.
    """
    import glob as _glob
    runs = Path(repo) / "workspace" / "agent-runs.jsonl"
    try:
        rows = [json.loads(l) for l in runs.read_text(encoding="utf-8", errors="replace").splitlines() if l.strip()]
    except (OSError, ValueError):
        return None                                    # unreadable is not "no faults"
    def done(tid):
        if _glob.glob(str(Path(repo) / "workspace" / "tasks" / "done" / f"{tid}--*.md")):
            return True
        for f in _glob.glob(str(Path(repo) / "workspace" / "tasks" / f"{tid}--*.md")):
            # the whole file, matched exactly: a slice could cut the status line off and turn a
            # closed task into a permanent fault (library check: silent-content-slice)
            if re.search(r"^status:\s*done\s*$",
                         Path(f).read_text(encoding="utf-8", errors="replace"), re.MULTILINE):
                return True
        return False
    delivered = {r.get("task") for r in rows
                 if r.get("outcome") == "ACCEPTED" and r.get("branch") and r.get("task")}
    faults = {}
    for r in rows:
        tid = r.get("task")
        if (r.get("outcome") == "ACCEPTED-NOT-WRITTEN" and tid and r.get("model") != "faketest"):
            faults.setdefault(tid, r.get("at") or "")
    return sorted((t, when) for t, when in faults.items() if t not in delivered and not done(t))


# BOTH WORDINGS, because a matcher that only knows the old phrase goes blind the moment the planner changes it
# (redesign step 4, 2026-10-29): "no free sequence slot on <role>" is what a build says now, and the two older
# forms still appear in ticks written before the change.
LANE_WAIT_PREFIXES = ("no free sequence slot", "waiting for a free lane", "waiting -- every model has a lane")


def tick_waiting(tick):
    """[(reason, count)] for chunks a tick skipped for want of a lane, worst first."""
    out = {}
    for s in (tick or {}).get("skipped") or []:
        why = str((s or {}).get("why") or "")
        if why.startswith(LANE_WAIT_PREFIXES):
            out[why] = out.get(why, 0) + 1
    return sorted(out.items(), key=lambda kv: -kv[1])


def unheld_kinds(d, waiting_in_tick=None):
    """Kinds with work WAITING that no role holds (t9540 R4, Claude's decision 3).

    A kind whose task no role lists cannot be picked by anything: it queues for ever with role=None, and a
    queue with no owner looks exactly like a queue with nothing due.

    IT FIRES ONLY WHEN SOMETHING OF THAT KIND IS ACTUALLY WAITING, and that restriction is deliberate. Some
    kinds are dispatched by their own path rather than by a role (console answers go through assign_console;
    done_check and supervision are run by scripts), so "no role lists it" is a fact about the taxonomy, not
    a fault -- measured on the live store 2026-10-28, console/done_check/supervision are held by nobody and
    all three still run. A check that failed on that would be noise, and noise is how a check gets ignored.
    """
    import task_routing as T
    wanted = {t for t in set(T.KIND_TASK.values()) if t}
    held = set()
    for role in T.model_roles(d):
        for t in T.tasks_of(d, role):
            if t.get("task"):
                held.add(t["task"])
    idle = wanted - held
    if not idle:
        return []
    if waiting_in_tick is None:
        return sorted(idle)
    # Which of the idle kinds have work waiting right now? The tick names tasks/chunks, so a kind counts as
    # waiting when any queued row's kind maps to one of the idle tasks.
    by_kind = {k for k, task in T.KIND_TASK.items() if task in idle}
    waiting = set()
    for row in (waiting_in_tick.get("queued") or []) + (waiting_in_tick.get("skipped") or []):
        k = (row or {}).get("kind") if isinstance(row, dict) else None
        if k in by_kind:
            waiting.add(k)
    return sorted(waiting)


def lane_contradiction(tick):
    """(lane_skips, running) when a tick blames a lane shortage while NO job holds a lane, else None.

    A skip reason is a claim, and this one has been false: on 2026-10-28 the dispatcher skipped 161
    ready chunks as "waiting -- every model has a lane" in a tick that reported running: 0, while every
    build model had four free slots. The lanes had been spent on PLANNED reviews (plan() offers one
    `free` list to both the review pass and production), so the sentence a human read described a
    different situation from the one that happened -- and production built nothing for an hour while
    the Hub faithfully showed "154 ready to build".

    The rule, stripped to what a tick can prove by itself: if work is skipped for want of a lane and
    nothing at all is running, the reason contradicts its own tick.
    """
    if not isinstance(tick, dict):
        return None
    def why_of(s):
        if isinstance(s, (list, tuple)) and len(s) > 1:
            return str(s[1])
        if isinstance(s, dict):
            return str(s.get("why") or "")
        return ""
    # Both wordings: "waiting for a free lane on <role>" since t9537, the old one in earlier ticks.
    lane = [s for s in (tick.get("skipped") or [])
            if why_of(s).startswith(("waiting -- every model", "waiting for a free lane",
                                     "no free sequence slot"))]
    running = _running_count(tick)
    return (len(lane), running) if lane and running == 0 else None


def _running_count(tick):
    """The dispatcher writes `running` as a COUNT (an int); a list of jobs is read as its length.
    Calling len() on the int crashed the whole check on every tick (2026-10-28)."""
    r = tick.get("running")
    return r if isinstance(r, int) else len(r or [])


def latest_verdicts(path):
    """{suite: exit_code} from the LAST row per suite in suite-state.jsonl, or {} if unreadable.

    The ledger appends, so the newest row is the verdict that counts: a suite that went green after
    being red is not still red. A torn line cannot be fatal -- the ledger is appended to by a live
    sweep and a crash mid-write must not blind every caller -- but it is COUNTED and named on stderr,
    because a silently dropped row and an absent row look identical to the caller.
    """
    out = {}
    torn = 0
    first = ""
    try:
        with Path(path).open(encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except ValueError as e:                          # noqa: BLE001 -- counted, not swallowed
                    torn += 1
                    first = str(e)[:120]
                    continue
                s = r.get("suite")
                if s and isinstance(r.get("exit_code"), int):
                    out[s] = r["exit_code"]
    except OSError:
        return {}
    if torn:
        # SAID, NOT SWALLOWED: a torn row is counted and named, so "the ledger had a bad line" is
        # visible rather than being indistinguishable from "the ledger has no such suite".
        print(f"  NOTE: {Path(path).name}: {torn} unreadable row(s) skipped -- first: {first}",
              file=sys.stderr)
    return out


def ledger_red_of(ledger_path):
    """(red_of, verdicts): "is this suite red?" answered from the RECORDED verdict, never by running it.

    `red_of(path)` returns True (the newest recorded verdict is non-zero), False (it is 0), or None
    (the ledger has no row: unknown, which is not red). Used by both the futility and the
    refused-chunk checks so that neither executes a suite from inside a check -- that cost 15m21s a
    session start on 2026-10-27 and appended verdict rows while it ran, editing the ledger the
    verdict was read from.
    """
    verdicts = latest_verdicts(ledger_path)

    def red_of(path):
        if not path:
            return None
        code = verdicts.get(Path(str(path)).name)
        return None if code is None else code != 0

    return red_of, verdicts


PAGE_HELP_FILE = "projects/app-repo/hub_common.py"
# The modules that RENDER a Hub page. A regex, not a list, so a new pages_*.py is covered on the day
# it is added rather than on the day someone remembers to add it here.
PAGE_MODULES = re.compile(
    r"^projects/app-repo/(feedback|pages_[a-z_]+|flow_pages|roles_pages|device_tiles|chat_page)\.py$")


def page_help_problems(paths):
    """A Hub page changed and its "how this page works" text did not (C3). Pure.

    Coarse on purpose: the pages do not declare which module renders them, and inventing that mapping
    inside a check would be a second truth. If the explanation genuinely did not change, the repair is
    to touch the entry and say so -- the gate forces a decision, it does not judge one.
    """
    names = [str(x) for x in (paths or ())]
    pages = sorted({p for p in names if PAGE_MODULES.match(p)})
    if not pages:
        return []
    if PAGE_HELP_FILE in set(names):
        return []
    return [f"{p} changed, but {PAGE_HELP_FILE} did not: a Hub page and its 'how this page works' "
            f"text change in the same commit (C3). If the explanation really is unchanged, touch the "
            f"entry anyway -- a dated _reviewed note is enough -- so the next reader can tell." 
            for p in pages]


# A PORT AT OR ABOVE THIS IS AN OUTBOUND SOCKET'S LOCAL END, not a service. The first observation
# (2026-10-26) found 22 externally-reachable listeners of which most were in this range; a check that
# reported them would be noise, and noise is how a check gets ignored.
EPHEMERAL_PORT = 32768
# A systemd unit name: letters, digits and @ . _ : - only. A name with a slash or a
# shell character is refused even though it is passed as argv, because a word can still be a traversal.
SYSTEMD_UNIT_NAME = re.compile(r"^[A-Za-z0-9@._:-]{1,128}$")


def changelog_breaks(rows):
    """Consecutive pairs where row N's `after` is not row N+1's `before` (C2). Pure.

    A changelog that cannot be replayed is a log, not a ledger: if the rows do not chain, the file on disk
    was at some point not what the previous logged change wrote, and nothing says what happened in
    between. FLOW-CHANGELOG.jsonl has six such pairs on 2026-10-26; the network map's changelog has none.
    """
    out = []
    for a, b in zip(rows or (), (rows or ())[1:]):
        if a.get("after") != b.get("before"):
            out.append((a.get("n"), b.get("n")))
    return out


def changelog_problems(log_path, declared_path, label="FLOW-CHANGELOG"):
    """Undeclared breaks, and declarations that no longer describe a break. [] is clean.

    The six historical breaks cannot be reconstructed -- the state that should have followed row 17 is not
    recoverable -- so they are DECLARED with what actually diverged, and anything undeclared is a finding.
    A declaration whose break has gone is a stale excuse, and an exception list that is never pruned is how
    a check becomes decoration.
    """
    p = Path(log_path)
    try:
        rows = [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]
    except (OSError, ValueError) as e:
        return [f"{p.name} could not be read ({type(e).__name__}: {e}) -- nothing was compared"]
    breaks = changelog_breaks(rows)
    try:
        declared = json.loads(Path(declared_path).read_text(encoding="utf-8")).get("declared") or []
    except (OSError, ValueError):
        declared = []
    covered = set()
    for d in declared:
        pair = tuple(d.get("between") or [])
        if len(pair) != 2:
            continue
        if not str(d.get("why") or "").strip() or not str(d.get("diverged") or "").strip():
            continue                       # a declaration needs a reason AND a record of what was compared
        covered.add(pair)
    out = []
    for a, b in breaks:
        if (a, b) not in covered:
            out.append(f"{label}: row {b}'s before is not row {a}'s after -- the chart was changed outside "
                       f"flow_change.py, and the changelog cannot be replayed past it. Either make the "
                       f"change through the function, or declare it in "
                       f"workspace/flow-changelog-declared.json with why and what diverged")
    for pair in sorted(covered - set(breaks)):
        out.append(f"{label}: a declaration covers rows {pair[0]}->{pair[1]}, which no longer break -- "
                   f"remove it, or the exception list stops describing the file")
    return out


def network_undeclared(doc, observed, unobserved=()):
    """Reality -> store: what is listening or on the tailnet that the map does not account for (N4).

    Two ways to account for something, both visible in the map itself: an entity's `observes` list, or
    a top-level `ignored` entry WITH a reason. A key with no reason is not an ignore, it is a silence.

    `unobserved` names the categories whose source could not be read. That is REPORTED rather than
    skipped: a source that was not read is not an all-clear, and the difference between "no undeclared
    peers" and "we could not ask" is the whole point of the check.
    """
    out = [f"{cat}: could not be observed, so nothing is known about it -- an unread source is not an "
           f"all-clear" for cat in (unobserved or ())]
    accounted = set()
    for e in doc.get("entities") or []:
        if isinstance(e, dict):
            accounted |= {str(k) for k in (e.get("observes") or [])}
    for i in doc.get("ignored") or []:
        if isinstance(i, dict) and str(i.get("why") or "").strip():
            accounted.add(str(i.get("key") or ""))
    for port in (observed.get("port") or []):
        key = f"port:{port}"
        if port >= EPHEMERAL_PORT or key in accounted:
            continue
        out.append(f"{key} is listening on a non-loopback address and is not on the map -- declare it "
                   f"(network_change.py add, or set <id> observes '[\"{key}\"]') or ignore it with a "
                   f"reason (network_change.py ignore {key} \"why\")")
    for name in (observed.get("tailnet") or []):
        key = f"tailnet:{name}"
        if key in accounted:
            continue
        out.append(f"{key} is on the tailnet and is not on the map -- a new device must not be "
                   f"invisible: network_change.py add, or ignore it with a reason")
    return out


def _tcp_probe(target, timeout=3.0):
    """True when host:port accepts a connection. The only network call this file makes."""
    import socket
    host, _, port = str(target).rpartition(":")
    try:
        with socket.create_connection((host or "127.0.0.1", int(port)), timeout):
            return True
    except (OSError, ValueError):
        return False


def network_unproven(doc, run=None, tcp=None, timeout=15):
    """Store -> reality: every service claiming `up` is provable, and the probe succeeds (N5). Pure.

    N4 asks "is reality on the map". This asks "is the map still true". A map full of `up` that nothing
    ever checks reads as knowledge and is not -- so a service may claim `up` only if it carries a
    `probe`, and a hole must be recorded as `state: unknown` instead of dressed up as up.

    `down-declared` needs `down: {since, why}`: down without "since when and why" is indistinguishable
    from forgotten. Devices and links are not probed -- N4 observes their presence.
    """
    import subprocess as _sp
    def _run(argv):
        # ARGV ONLY. `shell=True` is not reachable from here any more: a probe is a vocabulary word
        # (tcp / systemd_unit) and this runner refuses anything that is not a list.
        if not isinstance(argv, (list, tuple)):
            raise TypeError("a probe is an argv list; a command line string has no place here")
        r = _sp.run(list(argv), capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout, r.stderr
    run = run or _run
    tcp = tcp or _tcp_probe
    out = []
    for e in doc.get("entities") or []:
        if not isinstance(e, dict) or e.get("kind") != "service":
            continue
        eid = str(e.get("id", "?"))
        state = str(e.get("state") or "")
        if state == "unknown":
            continue                        # claims nothing; a declared hole
        if state == "retired":
            continue
        if state == "down-declared":
            down = e.get("down")
            if not (isinstance(down, dict) and str(down.get("since") or "").strip()
                    and str(down.get("why") or "").strip()):
                out.append(f"{eid}: down-declared needs down.since and down.why -- 'down' with no date "
                           f"and reason is indistinguishable from forgotten")
            continue
        probe = e.get("probe")
        if not isinstance(probe, dict) or not probe:
            out.append(f"{eid}: claims state {state!r} but carries no probe -- an unprovable claim. Give "
                       f"it probe {{tcp: host:port}} or {{systemd_unit: name}}, or record it as "
                       f"state: unknown")
            continue
        # A PROBE IS A WORD FROM A FIXED VOCABULARY, NEVER A COMMAND LINE (Claude Code's concern, t8860).
        # The first version executed `probe.command` through a shell, which made a data file a
        # shell-execution path for every hourly check and every session start -- and anything that can
        # write the map could put a command there. A store checked by executing its own contents is not
        # data.
        if probe.get("command"):
            out.append(f"{eid}: a `command` probe is refused -- the map is data, not a shell. Replace it "
                       f"with {{systemd_unit: '<unit name>'}} for a unit, or {{tcp: 'host:port'}} for a "
                       f"socket, and declare anything else as state: unknown")
            continue
        if probe.get("tcp"):
            target = str(probe["tcp"])
            host, _, port = target.rpartition(":")
            try:
                # 1.5 s, not 3: a probe to a down link must not add seconds to every session start. The
                # whole map is walked, so the cost is per entry and it accumulates.
                ok = tcp(host or "127.0.0.1", int(port), 1.5) if tcp is not _tcp_probe \
                    else _tcp_probe(target, 1.5)
            except (OSError, ValueError):
                ok = False
            if not ok:
                out.append(f"{eid}: claims {state!r} but {target} does not accept a connection")
        elif probe.get("systemd_unit"):
            unit = str(probe["systemd_unit"])
            if not SYSTEMD_UNIT_NAME.match(unit):
                # A WORD CAN STILL BE A TRAVERSAL: 'systemctl --user is-active --quiet ../../etc/passwd'
                # is argv-safe but not meaningful, so the name is validated rather than trusted.
                out.append(f"{eid}: systemd_unit {unit!r} is not a unit name (letters, digits, @ . _ : -)")
                continue
            argv = ["systemctl", "--user", "is-active", "--quiet", unit]
            try:
                # ARGV, NOT A STRING: no shell, so nothing in the name can be interpreted.
                rc, _, err = run(argv)
            except (OSError, _sp.SubprocessError) as exc:
                out.append(f"{eid}: claims {state!r} but its probe could not run ({type(exc).__name__})")
                continue
            if rc != 0:
                out.append(f"{eid}: claims {state!r} but unit {unit} is not active (exit {rc})"
                           + (f" -- {(err or '').strip()[:120]}" if err else ""))
        else:
            out.append(f"{eid}: probe has no 'tcp' or 'systemd_unit' -- unknown probe form {probe!r}")
    return out


def observe_network(run=None):
    """(observed, unobserved) from the machine. Best effort, and it says what it could not read.

    `run` is injected for tests: (argv) -> (rc, stdout, stderr).
    """
    import subprocess as _sp
    def _run(argv):
        r = _sp.run(argv, capture_output=True, text=True, timeout=30)
        return r.returncode, r.stdout, r.stderr
    run = run or _run
    observed, unobserved = {"port": [], "tailnet": []}, []
    # LISTENING TCP ON A NON-LOOPBACK ADDRESS. Loopback doors are local and are covered by the entity
    # that names them; this asks what a device elsewhere could reach.
    try:
        rc, out, _ = run(["ss", "-ltn"])
        if rc != 0:
            unobserved.append("port")
        else:
            for line in out.splitlines()[1:]:
                parts = line.split()
                if len(parts) < 4:
                    continue
                local = parts[3]
                host, _, port = local.rpartition(":")
                if not port.isdigit():
                    continue
                if host.startswith("127.") or host in ("[::1]", "::1"):
                    continue
                observed["port"].append(int(port))
    except (OSError, _sp.SubprocessError):
        unobserved.append("port")
    # TAILNET PEERS
    try:
        rc, out, _ = run(["tailscale", "status"])
        if rc != 0:
            unobserved.append("tailnet")
        else:
            for line in out.splitlines():
                parts = line.split()
                if len(parts) >= 2 and parts[0].count(".") == 3:
                    observed["tailnet"].append(parts[1])
    except (OSError, _sp.SubprocessError):
        unobserved.append("tailnet")
    observed["port"] = sorted(set(observed["port"]))
    observed["tailnet"] = sorted(set(observed["tailnet"]))
    return observed, unobserved


def network_map_problems(doc, repo):
    """Problems with the NETWORK MAP's shape -- not with the network (t7891). Pure.

    The map's job is to say what is on the network and how each thing is reached, while POINTING AT
    the source of truth for every value it does not own. Jas, 2026-10-26: "when I do fill it in, it
    could be with you who are a different model" -- so the properties that matter are the ones a
    stranger can check: does the pointer resolve, is the vocabulary declared, does a link name things
    that exist, and is a hole recorded as a hole rather than filled with a guess.

    `state: unknown` with a source is CORRECT and is not reported: a map that refuses to record holes
    will be filled with plausible guesses, which is the failure this file exists to prevent.
    """
    if not isinstance(doc, dict):
        return ["workspace/network.json is missing or is not a JSON object -- nothing was checked"]
    vocab = doc.get("vocabulary") or {}
    kinds = set(vocab.get("kind") or [])
    reached = set(vocab.get("reached_by") or [])
    states = set(vocab.get("state") or [])
    if not (kinds and reached and states):
        return ["workspace/network.json declares no vocabulary (kind / reached_by / state) -- "
                "without it no value in the map can be judged"]
    ents = doc.get("entities")
    if not isinstance(ents, list) or not ents:
        return ["workspace/network.json has no entities list -- an empty map is not a clean map"]
    repo = Path(repo)
    out, ids = [], {}
    for e in ents:
        if not isinstance(e, dict):
            out.append(f"an entity is not an object: {e!r}")
            continue
        eid = str(e.get("id") or "?")
        for k in ("id", "what", "kind", "owner", "reached_by", "state"):
            if not str(e.get(k) or "").strip():
                out.append(f"{eid}: missing {k}")
        if eid != "?":
            if eid in ids:
                out.append(f"{eid}: id appears more than once -- every reference to it is ambiguous")
            ids[eid] = e
        for field, allowed in (("kind", kinds), ("reached_by", reached), ("state", states)):
            v = e.get(field)
            if v and v not in allowed:
                out.append(f"{eid}: {field} {v!r} is not in the declared vocabulary {sorted(allowed)}")
        src = e.get("source")
        has_address = bool(str(e.get("address") or "").strip())
        if src is None and not has_address:
            out.append(f"{eid}: no source and no address -- it states a fact with no provenance")
        elif src is not None and not isinstance(src, dict):
            out.append(f"{eid}: source must be an object with file+key, or command")
        elif isinstance(src, dict):
            f = str(src.get("file") or "").strip()
            cmd = str(src.get("command") or "").strip()
            if f and not (repo / f).is_file():
                out.append(f"{eid}: source.file {f!r} does not exist -- a pointer that resolves to "
                           f"nothing reads as evidence")
            if not f and not cmd:
                out.append(f"{eid}: source names neither a file nor a command")
        ver = e.get("verified")
        if ver is not None and not (isinstance(ver, dict) and ver.get("at") and ver.get("how")):
            out.append(f"{eid}: verified needs both 'at' and 'how' -- a date with no method is a claim")
    for i in (doc.get("ignored") or []):
        if not isinstance(i, dict) or not str(i.get("key") or "").strip():
            out.append(f"ignored: an entry needs a key (got {i!r})")
        elif not str(i.get("why") or "").strip():
            out.append(f"ignored {i.get('key')}: needs a why -- an ignore with no reason is a silence")
    for e in ents:
        if not isinstance(e, dict) or e.get("kind") != "link":
            continue
        for j in (e.get("joins") or []):
            if j not in ids:
                out.append(f"{e.get('id', '?')}: joins {j!r}, which is not an entity in this file")
    return out


def _ceiling_at_rev(repo, rev):
    """allowed_to_fail as it stood at `rev`, or None when git cannot answer."""
    try:
        r = subprocess.run(["git", "-C", str(repo), "show", f"{rev}:workspace/test-failures-ceiling.json"],
                           capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    try:
        return json.loads(r.stdout).get("allowed_to_fail")
    except ValueError:
        return None


SCRIPT_DISPOSITIONS = "workspace/script-dispositions.json"


def declared_tools(repo):
    """(set of names, [problem]) from workspace/script-dispositions.json -- t4641, 2026-10-26.

    Not every scripts/*.py is a check. scripts/plan_run.py builds a document from a local model: an
    agent runs it when the work needs one, and a schedule would produce documents nobody asked for.
    orphan_scripts() asks "what runs this?", which is the right question for a check and the wrong one
    for a tool -- so a tool declares itself here, with the reason, and the scan stops counting it.

    NOTE ON THIS FILE'S OWN PROSE, LEARNED THE HARD WAY WHILE WRITING IT (2026-10-26): this module is
    one of the scan's sources, so naming an orphan candidate in a comment here counts as a caller and
    takes it off the list. That is the docs-line lie AGENTS.md forbids, arriving through a docstring.
    Do not name a script in this file unless something here actually refers to it.

    WHAT KEEPS THIS FROM BEING AN EXEMPTION LIST, which would be rule 9 with better manners:
      - a declared name must exist as scripts/<name> (a stale declaration would hide the script that
        replaced it while looking as if the matter was settled);
      - a declaration carries a reason of real length (so "everything" is not a way to buy the WARN
        down);
      - a declaration does not stop the name being scanned for callers, it only says that none is
        EXPECTED;
      - a file that exists but cannot be parsed is a problem AND declares nothing. An unreadable
        declaration must never read as "no tools", which is the vacuous pass of docs/CHECKS.md.

    A missing file is not a problem: nothing declared is the state before any tool was declared.
    """
    path = Path(repo) / SCRIPT_DISPOSITIONS
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return set(), []
    except OSError as e:
        return set(), [f"{SCRIPT_DISPOSITIONS}: unreadable ({e}) -- nothing is declared, so every "
                       f"scripts/*.py is being judged as a check that must have a caller"]
    try:
        d = json.loads(raw)
    except ValueError as e:
        return set(), [f"{SCRIPT_DISPOSITIONS}: not JSON ({e}) -- nothing is declared"]
    tools = d.get("tools") or {}
    if not isinstance(tools, dict):
        return set(), [f"{SCRIPT_DISPOSITIONS}: 'tools' must be an object of name -> reason"]
    names, problems = set(), []
    for name, reason in tools.items():
        if not isinstance(reason, str) or len(reason.strip()) < 20:
            problems.append(f"{name}: declared a tool with no reason. A reason is what separates a "
                            f"declaration from an exemption.")
        if not (Path(repo) / "scripts" / name).is_file():
            problems.append(f"{name}: declared a tool but scripts/{name} does not exist -- a stale "
                            f"declaration hides the script that replaced it.")
        names.add(name)
    return names, problems


def orphan_scripts(repo):
    """scripts/*.py that nothing in the repository names -- see the check for why it matters.

    WIDENED 2026-10-25 (t3578). This used to scan scripts/*.py, docs/*.md and AGENTS.md only, so a
    script whose ONLY caller is the Hub was reported as an orphan. The app-timeline helper is
    imported by the Hub's feedback page (projects/app-repo/feedback.py:3580) and was listed among
    the orphans -- a check naming working code. The block immediately below this one widened its own
    scope for the same reason (L-054: "Scoping a check to the place a bug was found is how the same
    bug returns elsewhere"); this one had the mirror-image defect, scoped to scripts/ while the
    callers had moved to projects/.

    The virtualenv and any other checkout are excluded. site-packages holds tens of thousands of
    files that would match almost any name, which is the same bug pointing the other way: a scan
    that matches everything finds nothing.
    """
    skip = (".venv", "node_modules", "site-packages", "worktrees", ".git")
    scripts_dir = repo / "scripts"
    sources = list(scripts_dir.glob("*.py")) + list((repo / "docs").glob("*.md"))
    sources.append(repo / "AGENTS.md")
    projects = repo / "projects"
    if projects.is_dir():
        sources += [p for p in projects.rglob("*.py") if not any(s in p.parts for s in skip)]
    # A SCRIPT NAMED BY A SHELL RUNNER OR A SYSTEMD UNIT IS RUN (2026-10-25, t4641). Six of the sixteen
    # this check named were called from scripts/autonomy_loop.sh -- chat_requests, chat_transcripts,
    # dsh_usage, ollama_settings, restart_stale and team_kpi -- because the scan read only scripts/*.py
    # as sources and never the .sh that drives them. deploy/ counts for the same reason: a unit's
    # ExecStart IS a caller, and nothing else in this repository would say so.
    sources += list(scripts_dir.glob("*.sh"))
    deploy = repo / "deploy"
    if deploy.is_dir():
        sources += [f for f in deploy.rglob("*") if f.is_file()]
    # A SCRIPT A GIT HOOK RUNS IS RUN (2026-10-26, t4641). scripts/hooks/pre-commit:243 has called
    # check_reassignment_notes.py since dab4be754 (2026-10-18) and this check named it an orphan every
    # day since, because hooks/ was never a source: the same defect as projects/ (t3578) and
    # autonomy_loop.sh, one directory further in. A hook fires at commit time with no timer, which is
    # exactly the "something that runs it" this check is asking for. Hooks have no extension, so this
    # is a glob over the directory rather than *.sh.
    hooks = scripts_dir / "hooks"
    hook_files = {f for f in hooks.glob("*") if f.is_file()} if hooks.is_dir() else set()
    sources += list(hook_files)
    bodies = {}
    for f in sources:
        try:
            bodies[f] = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            bodies[f] = ""
    # AND A HOOK'S COMMENT IS NOT A CALLER. Widening the sources to hooks/ would otherwise buy the
    # count down with a sentence: `# old_script.py was retired in t4600` would read as an invocation.
    # docs/*.md matches prose by design (a docs mention is how this check has always counted), but in
    # a shell hook a comment is unambiguous, so this is the one place the scan can tell "mentioned"
    # from "run". Kept deliberately narrow: only hooks, not docs.
    for f in hook_files:
        bodies[f] = "\n".join(l for l in bodies[f].splitlines() if not l.lstrip().startswith("#"))
    out = []
    tools, _ = declared_tools(repo)
    for f in sorted(scripts_dir.glob("*.py")):
        # A DECLARED TOOL IS NOT A CHECK MISSING ITS CALLER (t4641, 2026-10-26). The declaration and
        # its reasons are checked by declared_tools() itself; here it only means "no caller is
        # expected", so asking for one would name a correctly-configured tool as a defect.
        if f.name in tools:
            continue
        # A script naming itself does not count as being referenced.
        if not any(f.stem in body for other, body in bodies.items() if other != f):
            out.append(f.name)
    return out


def _retention_table(repo):
    """(ledgers, declared_over_bound) from workspace/ledger-retention.json.

    Raises rather than returning empty: the caller turns a failure into a FINDING, because an
    absent or unreadable retention table is not compliance -- it is the state in which nothing is
    known to be inside a bound. Same lesson as the FLOW-WORKERS projection check (t1594).
    """
    d = json.loads((Path(repo) / "workspace" / "ledger-retention.json").read_text(encoding="utf-8"))
    ledgers, declared = d.get("ledgers") or {}, d.get("_declared_over_bound") or {}
    if not isinstance(ledgers, dict) or not isinstance(declared, dict):
        raise ValueError("ledgers and _declared_over_bound must be objects")
    return ledgers, declared


RETENTION_DECLARE_MB = 50


def ledger_retention_findings(repo, min_declare_mb=RETENTION_DECLARE_MB):
    """[problem] for every ledger that is undeclared or over its bound (t4387).

    Three ways to fail, and each one exists because the check could otherwise read as assurance
    while knowing nothing:
      - a ledger above `min_declare_mb` that is not in the table (a new ledger must not escape by
        being new);
      - a declared ledger over its bound that is not named in _declared_over_bound (a bound that
        does not bite is decoration);
      - a table that cannot be read at all.
    A declared ledger that is no longer on disk is NOT a finding: a retired ledger is not a problem.
    """
    repo = Path(repo)
    try:
        ledgers, declared = _retention_table(repo)
    except (OSError, ValueError, AttributeError) as e:
        return [f"the retention table (workspace/ledger-retention.json) cannot be read "
                f"({type(e).__name__}: {e}) -- an absent table is not compliance, so nothing here "
                f"is known to be inside a bound"]
    sizes, unreadable = {}, []
    ws = repo / "workspace"
    for pat in ("*.jsonl", "run-logs/*.jsonl"):
        for f in ws.glob(pat):
            rel = f.relative_to(repo).as_posix()
            try:
                sizes[rel] = f.stat().st_size
            except OSError as e:
                # UNKNOWN IS NOT INSIDE A BOUND. Skipping it is the silent-pass shape this whole
                # check exists to replace, so it is reported as a finding of its own.
                unreadable.append(f"{rel} could not be read ({type(e).__name__}: {e}), so whether "
                                  f"it is inside its bound is unknown")
    out = []
    for rel, size in sorted(sizes.items()):
        mb = size / 1e6
        entry = ledgers.get(rel)
        if entry is None:
            if mb >= min_declare_mb:
                out.append(f"{rel} is {mb:,.0f} MB and is not in the retention table -- declare its "
                           f"class, its bound and the command that prunes it, or prune it")
            continue
        try:
            bound = float(entry.get("max_gb"))
        except (TypeError, ValueError):
            out.append(f"{rel} is declared with no numeric max_gb, so its bound cannot be enforced")
            continue
        gb = size / 1e9
        if gb > bound and rel not in declared:
            out.append(f"{rel} is over its {bound:g} GB bound ({gb:.2f} GB) -- prune it "
                       f"({entry.get('prune') or 'no prune command declared'}), or declare it "
                       f"over-bound in _declared_over_bound with the reason and the action")
    return unreadable + out


def ledger_retention_note(repo):
    """The PASS line: what is declared, and which of those are the declared exceptions."""
    try:
        ledgers, declared = _retention_table(repo)
    except (OSError, ValueError, AttributeError):
        return "the table could not be read"
    return (f"{len(ledgers)} ledger(s) declared with a class, a bound and a prune; "
            f"{len(declared)} declared over-bound with the action named")


def _is_returncode(node):
    """`rc`, `r.returncode`, `proc.returncode` -- but not `len(x)`."""
    if isinstance(node, ast.Name) and node.id in ("rc", "returncode", "exitcode", "exit_code",
                                                  "status", "code"):
        return True
    return (isinstance(node, ast.Attribute) and node.attr in ("returncode", "exitcode", "exit_code"))


def suite_proves_a_failure(body, where="<unknown>"):
    """True if the suite can FAIL -- by asserting it, or by exiting non-zero (t4388).

    THREE WAYS, all structural (parsed, not searched), so a suite cannot pass by containing a word:

      1. any `assert` statement -- the suite can fail, which is the entire question;
      2. `sys.exit(<non-zero>)`, including a non-constant argument, because the shape this repository
         actually uses is `sys.exit(1 if bad else 0)`. `sys.exit(0)` is not proof: it cannot fail.
      3. `report(label, rc, <non-zero int>)` -- the returning path, unchanged.

    WHY THIS EXISTS. On 2026-10-25 the check named 651 suites, two thirds of the repository, because it
    recognised only a quoted token or the report() form. A WARN naming most of the repo is one nobody
    reads, and it cannot be cleared a suite at a time. Jas: "Make it visible." The list must become
    short and TRUE, not empty -- a print-only script is still named.

    `where` names the file while parsing; see the note it replaced about SyntaxWarning attribution.
    """
    try:
        tree = ast.parse(body, filename=where)
    except SyntaxError:
        return False
    for node in ast.walk(tree):
        if isinstance(node, ast.Assert):
            return True
        # A unittest SUITE CAN FAIL: unittest.main() exits non-zero when a test fails, and a TestCase
        # subclass is one. Most of the suites this check still named on 2026-10-25 were unittest ones
        # whose assertions live inside `self.assertEqual`, which is the same proof in another dialect.
        # `exit=False` is the exception and is not proof: it runs the tests and returns success anyway.
        # A TestCase CLASS ALONE IS NOT PROOF (2026-10-25): run_tests.py judges a suite by its exit
        # code, and a TestCase with no runner exits 0 whatever its tests do. Only `unittest.main()`
        # with its default exit behaviour can fail the build -- `exit=False` returns success even when
        # a test fails, and my first version accepted it because a TestCase class was in the file.
        # `raise SystemExit(1 if failures else 0)` is the same statement as sys.exit(...) in a
        # different dialect, and it is what four of the last five suites used.
        if isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call) \
                and isinstance(node.exc.func, ast.Name) and node.exc.func.id == "SystemExit":
            arg = node.exc.args[0] if node.exc.args else None
            if arg is None or not (isinstance(arg, ast.Constant) and arg.value in (0, None)):
                return True
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr == "main" and "unittest" in ast.dump(node.func.value):
            skip = any(k.arg == "exit" and isinstance(k.value, ast.Constant)
                       and k.value.value is False for k in node.keywords)
            if not skip:
                return True
        if isinstance(node, ast.Call):
            fn = node.func
            name = getattr(fn, "attr", None) or getattr(fn, "id", None)
            if name == "exit" and node.args:
                arg = node.args[0]
                if not (isinstance(arg, ast.Constant) and arg.value in (0, None)):
                    return True
            if len(node.args) < 3:
                continue
            want = node.args[2]
            if not (isinstance(want, ast.Constant) and isinstance(want.value, int)
                    and not isinstance(want.value, bool) and want.value != 0):
                continue
            if _is_returncode(node.args[1]):
                return True
    return False


# THE DECLARED VOCABULARY OF WHY THE HARNESS REFUSED TO START A CHUNK (M2, 2026-10-27).
#
# Every code is a message coder.py or preflight_brief.py actually prints; none is invented. Until this
# existed the check reported "N chunk(s) refused" with a model name and a date per chunk and no cause --
# and the ledger cannot supply one: of 692 exit-2 rows on 2026-10-27, ZERO carried a `reason`, and the
# row does not even carry the log path (`log: null`), so the cause had to be recovered by pid. The
# writer half -- recording `reason_code` on the finished row -- is L3's (dispatch_loop/coder) and is
# asked for on the lane board; until it lands, the reader recovers the code from the refused run's log.
REFUSAL_CODES = {
    "baseline-undeclared": "the sandbox baseline held a suite that fails and is declared nowhere: "
                           "workspace/test-failures-ceiling.json, or workspace/sandbox-baseline.json",
    "needs-scope": "coder.py answered NEEDS-SCOPE: the brief's files do not match the change",
    "inference-unanswered": "the model server did not answer, so nothing was attempted",
    "preflight-objected": "a preflight refused to pass the brief, so nothing was dispatched",
    "unrecoverable": "the row carries no reason and its log names none -- counted, never cleared",
}

# coder.py's own words, one tuple per code. Search order matters: the baseline message is the only one
# that names suites, and a log can hold more than one attempt.
_BASELINE_SAID = "the sandbox baseline is not what is declared"
_INFERENCE_SAID = ("the inference path did not answer", "the model server did not answer")


def refusal_reason(text, kind=None):
    """(code, [suite, ...]) for one refused run's log. `code` is always in REFUSAL_CODES.

    `unrecoverable` is a real answer and it counts as a refusal: a refusal nobody can explain is
    still a refusal, and the one thing this must never do is read silence as a clearance (L-015).
    """
    text = str(text or "")
    if _BASELINE_SAID in text:
        named = [s.split("failure: ", 1)[-1].strip() for s in text.splitlines() if "failure: " in s]
        return "baseline-undeclared", [n for n in named if n]
    if "NEEDS-SCOPE" in text:
        return "needs-scope", []
    if any(said in text for said in _INFERENCE_SAID):
        return "inference-unanswered", []
    if (kind or "") == "preflight" and "Do not dispatch this brief" in text:
        return "preflight-objected", []
    return "unrecoverable", []


def _newest_refused_run(history, chunk):
    """(row, log, why) for `chunk`'s NEWEST finished row: (row, log, "") only if it is a refusal.

    A refusal is exit 2. Every other outcome means the harness is no longer refusing to START this
    chunk, and each says so in its own words -- a later accepted run clears everything before it (the
    ledger's own rule, and why `refusal_storm()` pops a chunk on `accepted`), an exit-1 run is an
    attempt that started, and an exit-3 run was accepted and not written. None of them is dropped
    silently: M2's standard is that every refusal is either gone or DECLARED.

    THE LOG IS ON THE ROW, AS `result_path` (corrected 2026-10-27). The first version of this
    function said the row carried no log path and went to the `started` row by pid. That was wrong --
    `dispatch_loop` writes `result_path` on every finished row, and an older exit-2 row carries it
    too. `result_path` is read first and the pid route is kept as a fallback, because a row written
    before that field existed is still a refusal and its log is still worth reading.
    """
    log_by_pid = {r.get("pid"): r.get("log") for r in history
                  if r.get("event") == "started" and r.get("log")}
    last = None
    for r in history:
        if r.get("event") == "finished" and r.get("chunk") == chunk:
            last = r
    if last is None:
        return None, None, "no finished run is on record for this chunk"
    if last.get("accepted") or last.get("exit") == 0:
        return last, None, "a later run of this chunk was accepted, which clears the refusal"
    if last.get("exit") != 2:
        return last, None, (f"its newest run started and ended exit {last.get('exit')} instead of "
                            "refusing -- the refusal is history")
    return last, last.get("result_path") or log_by_pid.get(last.get("pid")), ""


def live_refusals(storm, history, red_of, allowed=None, tracked=None, root=None, targets=None):
    """([live], [not live]) -- each refusal with its code, and only while the reason still holds.

    THIS REPLACED A 15-MINUTE CHECK. Staleness used to be `parallel_dispatch.target_is_red()`, which
    RUNS the chunk's target suite (subprocess, 600 s timeout) once per refused chunk: 31 chunks, and
    `check_workspace.py --quick` -- which every session start and every hourly check runs -- took
    15m21s on 2026-10-27 because two of those targets hang. It also asked about the WRONG SUITE: a
    baseline refusal names an undeclared suite in the baseline, not the chunk's target.

    The three conditions are `dispatch_loop.main_red_hold()`'s, because it answers this same question
    for the dispatcher and a second, subtly different copy is how this repository's rules drift:
    the suite must still be RED (`red_of`), must NOT be declared in the ceiling, and must be TRACKED
    -- an untracked file is not main. `red_of(path)` returns True / False / None and None holds
    nothing: unknown is not red, and a refusal nobody can clear is worse than one wasted retry.

    Only `baseline-undeclared` can be cleared by the named suite. needs-scope, inference-unanswered,
    preflight-objected and unrecoverable stay counted: the brief, the server and the log are not
    things a baseline read can pronounce resolved. But ANY refusal stops counting once the work it
    was for is done -- its own target recorded green, or gone from disk (the rule that stopped
    p47/p186 being reported for days after the compliance push closed them, 2026-10-02).
    """
    allowed = set(allowed or ())
    root = Path(root) if root else Path(__file__).resolve().parents[1]
    targets = targets or {}
    live, not_live = [], []
    for entry in storm or []:
        chunk, n, model, at = entry[0], entry[1], entry[2], entry[3]
        row, log, ended = _newest_refused_run(history, chunk)
        rec = {"chunk": chunk, "n": n, "model": model, "at": at, "code": "unrecoverable",
               "named": [], "why": ""}
        if row is None or ended:
            rec["why"] = ended or "no finished run is on record for this chunk"
            not_live.append(rec)
            continue
        text = ""
        if log:
            try:
                text = (root / log).read_text(encoding="utf-8", errors="replace")[-8000:]
            except OSError:
                text = ""
        code, suites = refusal_reason(text, row.get("kind"))
        rec.update(code=code, named=suites)
        target = targets.get(chunk)
        if target and not (root / target).is_file():
            rec["why"] = "its target is gone from disk, so the chunk is dead work"
            not_live.append(rec)
            continue
        if target and red_of(target) is False:
            rec["why"] = "its own target is green now -- the work it was for is done"
            not_live.append(rec)
            continue
        if code != "baseline-undeclared":
            rec["why"] = REFUSAL_CODES.get(code, "")
            live.append(rec)
            continue
        if not suites:
            # It said "baseline" without naming a suite: the log is truncated, so it is not cleared.
            rec["code"] = "unrecoverable"
            rec["why"] = "the refusal names no suite, so nothing can show it resolved"
            live.append(rec)
            continue
        still = [s for s in suites if s not in allowed and (tracked is None or
                                                            f"scripts/tests/{s}" in tracked)
                 and red_of(f"scripts/tests/{s}") is True]
        if still:
            rec["named"] = still
            live.append(rec)
            continue
        rec["named"] = suites
        if all(s in allowed for s in suites):
            rec["why"] = "declared in the ceiling since it refused"
        elif tracked is not None and not any(f"scripts/tests/{s}" in tracked for s in suites):
            rec["why"] = "the suites it named are not tracked"
        elif any(red_of(f"scripts/tests/{s}") is None for s in suites):
            rec["why"] = "the suites it named have no recorded verdict -- unknown is not red"
        else:
            rec["why"] = "the suites it named are green now"
        not_live.append(rec)
    return live, not_live


def _mtime_for(p):
    """The mtime of a temp copy, or NOW when it cannot be read.

    NOW, not 1970: an unreadable mtime must not push a copy into the STALE half, where the check FAILS and tells the
    reader to reap something the reaper may still be unable to touch. Being wrong towards "recent" costs a warning;
    being wrong towards "stale" costs a red main nobody can clear.
    """
    try:
        return p.stat().st_mtime
    except OSError as e:                                            # noqa: BLE001 -- said, not swallowed
        print(f"  NOTE: {p} mtime unreadable ({type(e).__name__}: {e}) -- treated as recent", file=sys.stderr)
        return time.time()


class Checker:
    def __init__(self, repo_root, quick=False):
        self.repo = Path(repo_root).resolve()
        self.quick = quick
        # THE CHECKOUT THE MACHINE ACTUALLY RUNS FROM, which is not always self.repo.
        # In a linked worktree `git rev-parse --git-common-dir` points at the MAIN clone's
        # .git, so its parent is the tree the services, timers and cron jobs were started
        # from. Two checks are about the live machine rather than about these files, and
        # running them against a worktree produced confident false failures on 2026-09-22:
        # a fresh checkout gives every file a current mtime, so every service read as
        # running stale code; and a gitignored file present in the main tree is simply
        # absent here, so its reference read as dangling.
        self.live = self.repo
        self.live_note = ""
        try:
            common = subprocess.run(["git", "rev-parse", "--git-common-dir"],
                                    capture_output=True, text=True, cwd=self.repo, timeout=10)
            if common.returncode == 0:
                d = (self.repo / common.stdout.strip()).resolve()
                if d.name == ".git" and d.parent != self.repo:
                    self.live = d.parent
            else:
                self.live_note = (common.stderr or "").strip()[:200] or "git declined to answer"
        except (OSError, subprocess.SubprocessError) as e:
            # NOT swallowed: the fallback is "assume this tree", which is right often enough
            # to continue on but wrong often enough that a check leaning on it must SAY so.
            self.live_note = f"{type(e).__name__}: {e}"
        self.in_worktree = self.live != self.repo
        self.passes = 0
        self.warnings = 0
        self.failures = 0

    # -- reporting ----------------------------------------------------------------
    def _record_history(self):
        """Append this run to workspace/check-history.jsonl (t104). A failing run records too -- a gap
        in the history exactly when things went wrong is the defect this exists to remove. It never
        stops the check: a history that cannot be written is said, not raised."""
        try:
            with HISTORY.open("a", encoding="utf-8") as f:
                f.write(json.dumps(_history_row(self, self.quick)) + "\n")
        except (OSError, TypeError, ValueError) as e:
            print(_c("33", f"  NOTE: check history not written ({type(e).__name__}: {e})"))

    def _record_tree_state(self, verdict, files):
        """Append this check's tree state to workspace/ledgers/tree-state.jsonl (t622). Records the
        verdict, HEAD, and the bare dirty paths; a ledger that cannot be written is said, not raised."""
        try:
            path = self.repo / "workspace" / "ledgers" / "tree-state.jsonl"
            path.parent.mkdir(parents=True, exist_ok=True)
            head = self.git("rev-parse", "HEAD")[1]
            row = {
                "ts_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "verdict": verdict,
                "dirty_count": len(files),
                "files": sorted(f[3:] for f in files if f.strip()),
                "git_head_sha": head[0] if head else None,
            }
            with path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(row) + "\n")
        except (OSError, TypeError, ValueError) as e:
            print(_c("33", f"  NOTE: tree state not written ({type(e).__name__}: {e})"))

    def result(self, status, name, detail=None):
        colours = {"PASS": "32", "WARN": "33", "FAIL": "31", "SKIP": "90"}
        if status == "PASS":
            self.passes += 1
        elif status == "WARN":
            self.warnings += 1
            self.warned_names = getattr(self, "warned_names", []) + [name]
        elif status == "FAIL":
            self.failures += 1
            self.failed_names = getattr(self, "failed_names", []) + [name]
        print(f"  {_c(colours[status], '[' + status + ']')} {name}")
        if detail:
            for line in str(detail).split("\n"):
                print(_c("90", "         " + line))

    def section(self, title):
        print()
        print(_c("36", title))

    # -- git ----------------------------------------------------------------------
    def _tracked_count(self):
        """How many files the scan actually covered -- so a pass says what it examined."""
        rc, out = self.git("ls-files")
        return len(out) if rc == 0 else 0

    def git(self, *args):
        """Run a git command. Never raises on non-zero exit; exit codes are the contract."""
        p = subprocess.run(
            ["git", "-C", str(self.repo), *args],
            capture_output=True, text=True, errors="replace",
            timeout=600,
        )
        out = (p.stdout or "").rstrip("\n")
        return p.returncode, (out.split("\n") if out else [])

    def git_lines(self, *args):
        return self.git(*args)[1]

    def push_disabled(self):
        """True when origin's PUSH url is deliberately pointed at nothing (2026-09-16).

        Read the url rather than attempt a push: a network failure and a deliberate decision
        must never be confused, because one means the history is unbacked and the other means
        it is backed up somewhere else.
        """
        rc, out = self.git("remote", "get-url", "--push", "origin")
        return rc != 0 or any("DISABLED" in line for line in out)   # no origin at all (2026-10-13) = off

    # -- filesystem ---------------------------------------------------------------
    def workspace_files(self):
        """Enumerate files, skipping PRUNED_DIRS entirely rather than filtering after."""
        out = []
        for dirpath, dirnames, filenames in os.walk(self.repo):
            dirnames[:] = [d for d in dirnames if d not in PRUNED_DIRS]
            for fn in filenames:
                out.append(Path(dirpath) / fn)
        return out

    # ==============================================================================
    def check_git_state(self):
        self.section("Git state")

        rc, status = self.git("status", "--porcelain")
        if rc != 0:
            verdict = "FAIL"
            self.result("FAIL", "git status runs", "\n".join(status))
        elif status:
            verdict = "WARN"
            self.result(
                "WARN", "Working tree is clean",
                "Uncommitted changes present. That is fine mid-session; "
                "it must be clean at the end.\n" + "\n".join(status[:10]),
            )
        else:
            verdict = "PASS"
            self.result("PASS", "Working tree is clean")
        self._record_tree_state(verdict, status if rc == 0 else [])

        # THE LIVE CHECKOUT STAYS ON MAIN (2026-10-09). The services commit to whatever this folder
        # has checked out; a DSH session left it on dsh/dsh-mastery-skills and the pipeline's
        # bridge/t764 commits landed there, unseen until Claude read the branch name. Branches go
        # in `git worktree add`. A linked worktree running this check is not the live checkout.
        rc, branch = self.git("symbolic-ref", "-q", "--short", "HEAD")
        _, gd = self.git("rev-parse", "--git-dir")
        _, cd = self.git("rev-parse", "--git-common-dir")
        linked = gd and cd and (self.repo / gd[0]).resolve() != (self.repo / cd[0]).resolve()
        if linked:
            pass
        elif rc != 0 or not branch or branch[0] != "main":
            self.result("FAIL", "The live checkout is on main",
                        f"it is on {branch[0] if branch else 'a detached HEAD'}; the services are "
                        "committing there. Fix: git checkout main (and merge or keep the branch); "
                        "build branches in a worktree: git worktree add ~/ai-workspace-dsh/<topic> "
                        "-b dsh/<topic> main")
        else:
            self.result("PASS", "The live checkout is on main")


        # NO REMOTE AT ALL (2026-10-13, Jas: "delete the GitHub content", "Put chat/ in git"). The
        # NAS bundle is the only backup (decided 2026-08-31, pushing off since 2026-09-16), and now
        # that chat/ -- the family corpus -- is tracked, a remote is no longer harmless: a push would
        # carry it off the machine and a push cannot be recalled (D-22's whole reason). So a remote
        # is a FAIL, and "is the history backed up" is asked of the NAS directly.
        remotes = [r for r in self.git_lines("remote") if r.strip()]
        if remotes:
            self.result(
                "FAIL", "This repository has no remote",
                f"remote(s) configured: {', '.join(remotes)}. chat/ is tracked (D-38), so a push would "
                "send the family corpus off this machine. Remove it: git remote remove <name>")
        else:
            self.result("PASS", "This repository has no remote",
                        "nothing can push chat/ anywhere; the NAS bundle is the backup")
        import json as _json
        led = self.repo / "workspace" / "backup-runs.jsonl"
        last = None
        try:
            for line in led.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    r = _json.loads(line)
                    if r.get("job") == "nas-backup":
                        last = r
        except (OSError, ValueError):
            last = None
        if last and last.get("status") == "verified":
            self.result("PASS", "The history is backed up",
                        f"the NAS bundle verified at {last.get('at')} ({last.get('detail')})")
        else:
            rc_n, n = self.git("rev-list", "--count", "HEAD")
            self.result(
                "FAIL", "The history is backed up",
                "the last NAS backup did not verify ("
                + (str(last.get("status")) if last else "no run on record")
                + f"), and the NAS is the only copy: {n[0] if rc_n == 0 and n else '?'} commit(s) "
                "exist ONLY on this machine. Run scripts/nas_backup.sh and read what it says.")

    def check_manifest_ops(self):
        """The manifest op vocabulary in chat_agent.ALLOWED_OPS equals the one workspace/commands/README.md
        lists (t677, RCA 811831652f8e finding D5). The README said six ops and 'Nothing else' while the
        executor allowed seven including `replace`, so the spec forbade what the code permitted and nothing
        noticed. Reads chat_agent.py as TEXT -- importing it could touch its arming state. An unreadable
        or unrecognisable source is a SKIP, never a PASS (the standard the retired check_chat.py set)."""
        name = "manifest op vocabulary matches (ALLOWED_OPS vs README)"
        try:
            code = (self.repo / "scripts" / "chat_agent.py").read_text(encoding="utf-8")
            readme = (self.repo / "workspace" / "commands" / "README.md").read_text(encoding="utf-8")
        except OSError as e:
            self.result("SKIP", name, f"a source is unreadable ({e}) -- SKIP, not PASS")
            return
        i = code.find("ALLOWED_OPS")
        a = code.find("(", i) if i >= 0 else -1
        b = code.find(")", a) if a >= 0 else -1
        code_ops = {t.strip().strip("'\"") for t in code[a + 1:b].split(",")} - {""} if b > a else set()
        line = next((l for l in readme.splitlines() if "Operations:" in l), "")
        readme_ops = {t for t in line.split("`")[1::2] if t.isalnum()}
        if not code_ops or not readme_ops:
            self.result("SKIP", name, "ALLOWED_OPS or the README's Operations line was not found -- "
                                      "SKIP, not PASS")
            return
        missing, extra = code_ops - readme_ops, readme_ops - code_ops
        if not missing and not extra:
            self.result("PASS", name, f"both list the same {len(code_ops)} ops")
        else:
            self.result("FAIL", name, "README is missing: " + ", ".join(sorted(missing)) +
                        "; README has extra: " + ", ".join(sorted(extra)))

    # ==============================================================================
    def check_secrets(self):
        self.section("Secrets")

        # A FILE IT COULD NOT READ IS NOT A FILE WITHOUT A SECRET.
        #
        # `git grep` exits 1 both when it found nothing AND when it could not stat a file,
        # writing "Permission denied" to stderr in the second case. This tested `rc == 0 and
        # hits` and fell through to PASS, so a tracked file containing a real credential
        # that the process cannot read was reported as clean. Reproduced 2026-08-31 with an
        # actual ghp_ token in a chmod-000 file: rc=1, zero hits, PASS.
        #
        # Found by the env3 probe (finding 26) -- the examined-nothing false pass, inside the
        # secret scanner. This is the check whose silence is trusted most, so its silence has
        # to mean "I looked", not "I could not look".
        proc = subprocess.run(
            ["git", "-C", str(self.repo), "grep", "-I", "-n", "-E", SECRET_PATTERN, "--", "."],
            capture_output=True, text=True, errors="replace", timeout=600)
        # The one documented placeholder (pre-commit's PLACEHOLDER) is stripped before re-matching, so
        # a line that ALSO holds a real key still fails. Lines are cut for the report: chat/'s exports
        # are a 60 MB single line each.
        hits = [l[:300] for l in (proc.stdout or "").splitlines()
                if l.strip() and re.search(SECRET_PATTERN, re.sub(SECRET_PLACEHOLDER, "", l))
                and not only_known_fakes(l)]
        unreadable = [l for l in (proc.stderr or "").splitlines() if l.strip()]
        if hits:
            self.result("FAIL", "No secret pattern in tracked files", "\n".join(hits[:5]))
        elif unreadable:
            self.result(
                "FAIL", "No secret pattern in tracked files",
                "git grep could not read {} path(s), so they were NOT scanned:\n".format(
                    len(unreadable)) + "\n".join(unreadable[:5])
                + "\n\nAn unscanned file is not a clean file. Fix the permissions and "
                  "re-run; do not treat this as a pass.")
        else:
            self.result("PASS", "No secret pattern in tracked files",
                        f"{self._tracked_count()} tracked file(s) scanned")

        # THE SERVED SURFACE, NOT ONLY THE COMMITTED ONE (t117, 2026-09-30). The scan above reads
        # git-tracked files because it was written to stop a secret being COMMITTED. Since
        # 2026-08-31 the workspace MCP server serves the WORKING TREE to Chat -- untracked and
        # ignored files included, everything but its DENIED_PREFIXES -- so a key pasted into a log
        # or a scratch file reaches Chat without ever being staged. Scoped to where the first bug
        # was found: t113's shape. This reads what the server would serve.
        try:
            sys.path.insert(0, str(self.repo / "scripts" / "workspace_mcp"))
            from core import DENIED_PREFIXES as _denied
        except Exception as e:                                      # noqa: BLE001
            _denied = ("workspace/private/", ".git/", ".venv/")
            self.result("WARN", "No secret pattern in files the MCP server serves",
                        f"core.DENIED_PREFIXES unreadable ({type(e).__name__}); using the defaults")
        proc = subprocess.run(
            ["git", "-C", str(self.repo), "grep", "--untracked", "--no-exclude-standard",
             "-I", "-o", "-E", SECRET_PATTERN, "--", "."],
            capture_output=True, text=True, errors="replace", timeout=600)
        served = sorted({l.split(":", 1)[0] for l in (proc.stdout or "").splitlines()
                         if l.strip() and ":" in l
                         and not only_known_fakes(l)
                         and not any(l.startswith(d) or f"/{d}" in f"/{l.split(':', 1)[0]}" for d in _denied)})
        # rc 1 is "no match"; anything else, or a line on stderr, means it did not look.
        if proc.returncode not in (0, 1) or (proc.stderr or "").strip():
            self.result("FAIL", "No secret pattern in files the MCP server serves",
                        f"git grep exit {proc.returncode}, so nothing was scanned:\n"
                        + (proc.stderr or "").strip()[:400])
        elif served:
            self.result("FAIL", "No secret pattern in files the MCP server serves",
                        "untracked or ignored, so no commit gate saw them -- but Chat can read them:\n"
                        + "\n".join(served[:5]))
        else:
            self.result("PASS", "No secret pattern in files the MCP server serves",
                        "untracked and ignored files included; the server's denied paths excluded")

        if self.quick:
            self.result("SKIP", "No secret pattern anywhere in git history", "(--quick)")
            return

        revs = self.git_lines("rev-list", "--all")
        if not revs:
            self.result("WARN", "No secret pattern anywhere in git history", "No revisions found.")
            return

        # Control first: a negative result is worthless if the search itself is broken.
        control_rc, _ = self.git("grep", "-I", "-l", "STATE", "--", "*.md")
        if control_rc != 0:
            self.result(
                "WARN", "No secret pattern anywhere in git history",
                "Control search found nothing, so a clean scan cannot be trusted. "
                "Not reporting a result.",
            )
            return

        # DIFFS, NOT TREES (2026-10-03). `git grep PATTERN <every rev>` read the whole tree of all
        # 1901 revisions in one call; it outgrew its 600 s limit and failed session end twice. A
        # secret can only enter history in some commit's diff, so `git log -G` over every diff is
        # the same coverage in ~10 s. The control proves -G finds a string we know was added.
        crc, ctl = self.git("log", "--all", "--format=%h", "-G", "def check_secrets", "--",
                            "scripts/check_workspace.py")
        if crc != 0 or not ctl:
            self.result("WARN", "No secret pattern anywhere in git history",
                        "Control (-G for a known addition) found nothing, so a clean scan cannot be "
                        "trusted. Not reporting a result.")
            return
        rc, hist = self.git("log", "--all", "--format=%h %s", "-G", SECRET_PATTERN)
        # A commit whose only match is the documented placeholder (D-38: an example key in chat/'s export)
        # is not a secret: look inside each hit and keep it only if a match survives stripping it.
        def _real(line):
            sha = line.split()[0]
            added = subprocess.run(f"git -C '{self.repo}' show -U0 --format= {sha} | grep -E '^\\+' "
                                   f"| sed -E 's/{SECRET_PLACEHOLDER}//g' | grep -cE '{SECRET_PATTERN}'",
                                   shell=True, capture_output=True, text=True, timeout=600).stdout.strip()
            return added not in ("", "0")
        hist = [h for h in hist if _real(h)]
        rc = 0 if hist else 1
        if rc == 0 and hist:
            self.result("FAIL", "No secret pattern anywhere in git history", "\n".join(hist[:5]))
        else:
            self.result(
                "PASS", "No secret pattern anywhere in git history",
                f"Scanned {len(revs)} revisions; control search confirmed the scan works.",
            )

    # ==============================================================================
    def check_file_hazards(self, files):
        self.section("File hazards")

        # A tracked .md of zero bytes is the signature of a write that failed silently.
        empty = []
        for rel in self.git_lines("ls-files", "*.md"):
            p = self.repo / rel
            if p.is_file() and p.stat().st_size == 0:
                empty.append(rel)
        if empty:
            self.result("FAIL", "No zero-byte tracked markdown files", "\n".join(empty))
        else:
            self.result("PASS", "No zero-byte tracked markdown files")

    # ==============================================================================
    # FREE-SPACE FLOOR (t9566, Jas 2026-10-29: "Add it"). The SSD reached 98% -- 18 GB free and falling ~11 GB per
    # half hour -- with nothing saying so until a lane measured it by hand. GB, not percent: the jobs that fill it
    # (1.6 GB repo copies, 1-2 GB worktrees, a 92 GB model) come in absolute sizes.
    DISK_WARN_GB = float(os.environ.get("HIVEMIND_DISK_WARN_GB", "100"))
    DISK_FAIL_GB = float(os.environ.get("HIVEMIND_DISK_FAIL_GB", "50"))

    def check_disk_free(self, path=None, statvfs=None, worktrees=None):
        """The root disk has room: WARN under DISK_WARN_GB free, FAIL under DISK_FAIL_GB, naming what usually fills it."""
        self.section("Temp space")
        path = path or str(Path.home())
        try:
            st = (statvfs or os.statvfs)(path)
        except OSError as e:
            self.result("WARN", "The disk has room", f"statvfs({path}) failed: {e}")
            return
        free = st.f_bavail * st.f_frsize / 2 ** 30
        total = st.f_blocks * st.f_frsize / 2 ** 30
        if worktrees is None:
            out = subprocess.run(["git", "-C", str(self.repo), "worktree", "list", "--porcelain"], capture_output=True,
                                 text=True, timeout=60).stdout
            worktrees = sum(1 for l in out.splitlines() if l.startswith("worktree "))
        level = "PASS" if free >= self.DISK_WARN_GB else "WARN" if free >= self.DISK_FAIL_GB else "FAIL"
        detail = f"{free:.0f} GB free of {total:.0f} GB ({1 - free / total:.0%} used); {worktrees} git worktree(s)"
        if level != "PASS":
            detail += (f" -- under the {self.DISK_WARN_GB if level == 'WARN' else self.DISK_FAIL_GB:.0f} GB floor."
                       "\n  Usual causes: abandoned repo copies and merged worktrees under ~/.cache/hivemind-tmp. Reap:"
                       "\n  python3 -c \"import sys,os; sys.path.insert(0,'scripts'); import sweep; "
                       "r=os.path.expanduser('~/.cache/hivemind-tmp'); sweep.sweep_tmp_repo_copies(r, max_age_s=3600); "
                       "sweep.sweep_tmp_worktrees(r, max_age_s=3600)\"")
        self.result(level, "The disk has room", detail)

    def check_tmp_inodes(self):
        """/tmp is a tmpfs with a fixed inode count. On 2026-10-06 it reached 100% -- every tool on the
        box, the agent harness included, failed with ENOSPC while bytes were 18% used. Suites that
        mkdtemp() without cleanup, run by coder.py's baseline on every dispatch, were the cause."""
        self.section("Temp space")
        st = os.statvfs("/tmp")
        if not st.f_files:
            self.result("SKIP", "/tmp has inodes to spare", "the filesystem reports no inode count")
            return
        used = 1 - st.f_ffree / st.f_files
        self.result("PASS" if used < 0.6 else "WARN" if used < 0.85 else "FAIL",
                    "/tmp has inodes to spare",
                    f"{used:.0%} of {st.f_files:,} inodes used" + ("" if used < 0.6 else
                    " -- find the leaking prefix: ls /tmp | sed 's/[0-9_.-].*//' | sort | uniq -c | sort -rn | head"))

    def check_structure(self):
        self.section("Structure the documentation promises")

        projects_dir = self.repo / "projects"
        project_dirs = sorted(p for p in projects_dir.iterdir() if p.is_dir()) \
            if projects_dir.is_dir() else []
        missing = [p for p in project_dirs if not (p / "STATUS.md").is_file()]
        if missing:
            self.result(
                "FAIL", "Every project has a STATUS.md",
                "\n".join(f"projects/{p.name}" for p in missing),
            )
        elif not project_dirs:
            # ZERO PROJECTS IS NOT ALL PROJECTS PASSING. With projects/ absent or empty,
            # `missing` is empty and this reported PASS -- a universal claim over an empty
            # set, which is vacuously true and operationally a lie. The count was in the
            # PASS text ("0 projects") and nobody reads a number inside a green line.
            #
            # Second instance of this class found within half an hour (L-136 was the release
            # notes check), which is why the survey in docs/CHECKS.md now names the shape.
            self.result(
                "FAIL", "Every project has a STATUS.md",
                "projects/ is absent or contains no directories, so NOTHING was checked.\n"
                "This check passed vacuously before 2026-08-27: an empty set satisfies\n"
                "'every project has a STATUS.md' while telling you nothing at all.")
        else:
            self.result("PASS", f"Every project has a STATUS.md ({len(project_dirs)} projects)")

        # Git does not track empty directories, so each needs a tracked file or a fresh
        # clone would not have the channel at all.
        for box in ("inbox", "outbox"):
            d = self.repo / "workspace" / box
            tracked = self.git_lines("ls-files", f"workspace/{box}")
            if not d.is_dir():
                self.result(
                    "FAIL", f"workspace/{box} exists",
                    "Documented in CLAUDE.md and docs/WORKFLOW.md, but not on disk.",
                )
            elif not tracked:
                self.result(
                    "FAIL", f"workspace/{box} survives a fresh clone",
                    "Directory exists but has no tracked file, so git does not carry it. "
                    "Add a README.md.",
                )
            else:
                self.result("PASS", f"workspace/{box} exists and is tracked")

        # A MESSAGE OUTLIVES ITS PRECONDITIONS (t141). The outbox README says to clear stale messages
        # at session end; nothing checked, and on 2026-10-02 seven sat there, the oldest 16 days ("you
        # can restart the queue now"). Age is the mechanical proxy: Chat reads STATE.md for anything
        # lasting, so an outbox note past OUTBOX_STALE_DAYS has almost certainly been superseded.
        stale = []
        for f in sorted((self.repo / "workspace" / "outbox").glob("20*--*.md")):
            m = re.match(r"(\d{4}-\d{2}-\d{2})T", f.name)
            if m:
                age = (time.time() - time.mktime(time.strptime(m.group(1), "%Y-%m-%d"))) / 86400
                if age > OUTBOX_STALE_DAYS:
                    stale.append(f"{f.name}  ({age:.0f} days)")
        if stale:
            self.result("WARN", "No outbox message has outlived its preconditions",
                        f"{len(stale)} older than {OUTBOX_STALE_DAYS} days -- clear them (git rm; history "
                        "keeps them) or fold what still matters into STATE.md:\n"
                        + "\n".join(f"  {s}" for s in stale))
        else:
            self.result("PASS", "No outbox message has outlived its preconditions",
                        f"none older than {OUTBOX_STALE_DAYS} days")

    # ==============================================================================
    def check_lesson_ids(self):
        """Every lesson id appears once. A duplicate makes every citation of it ambiguous.

        WHY THIS EXISTS. On 2026-09-03 `L-140`, `L-141` and `L-142` each existed TWICE in
        docs/LESSONS.md with different content, in two different heading styles -- the older
        `**L-140 [VERIFIED ...]**` form and the newer `## L-140.` form. Seven files cited one
        meaning and six cited the other, and nothing could tell them apart.

        Nobody checked, so the second collision was made by reading the previous session's
        numbering rather than the file: I wrote `## L-142` while `**L-142**` had existed since
        2026-08-31. A citation of `L-141` was ambiguous for two days.

        This is the cheapest possible check -- count the ids -- and its absence cost a
        renumbering across eight files.
        """
        self.section("Lesson ids")
        body = (self.repo / "docs" / "LESSONS.md").read_text(encoding="utf-8", errors="replace")
        ids = re.findall(r"^(?:\*\*|## )(L-\d+)", body, re.M)
        seen, dupes = set(), []
        for i in ids:
            (dupes.append(i) if i in seen else seen.add(i))
        if dupes:
            self.result(
                "FAIL", "Every lesson id is unique",
                f"{len(set(dupes))} duplicate id(s): {', '.join(sorted(set(dupes)))}.\n"
                "Every citation of a duplicated id is ambiguous. Renumber the NEWER entry to\n"
                "the next free number and update the citations that mean it -- not the older\n"
                "one, whose id is already established elsewhere.",
            )
        else:
            self.result("PASS", "Every lesson id is unique", f"{len(ids)} lessons")

    def check_enforcement(self):
        self.section("Enforcement")

        # RESOLVED, NOT STRING-MATCHED -- because the old form told a frightening lie.
        # `git worktree add` rewrites core.hooksPath in the SHARED .git/config to an absolute
        # path, which happened four times on 2026-09-22 as an agent isolated its work. The
        # check then reported "Nothing is enforcing commit format, attribution, or the secret
        # scan" -- and that was false: the hooks were firing the whole time (one blocked a
        # swallowed exception that same session). A guard that cries wolf about ITSELF is the
        # fastest way to teach someone to ignore it.
        #
        # An absolute path is still worth saying something about: it is machine-specific and
        # does not survive a fresh clone, which is the reason the relative form is wanted. So
        # it is a WARN with the one-line repair, not a FAIL claiming nothing is enforced. The
        # FAIL is reserved for what it always meant -- hooks that genuinely are not wired.
        hooks_path = "".join(self.git_lines("config", "core.hooksPath")).strip()
        HOOKS = ("pre-commit", "commit-msg", "pre-push")
        # The question is "will git run real hooks", NOT "does this string equal that string".
        # Resolve where core.hooksPath actually points and look there -- in a linked worktree
        # it points at the MAIN checkout's scripts/hooks, which enforces perfectly well.
        try:
            hooks_dir = (self.repo / hooks_path).resolve() if hooks_path else None
        except OSError:
            hooks_dir = None
        wired = hooks_dir is not None and all((hooks_dir / h).is_file() for h in HOOKS)
        if not wired:
            self.result(
                "FAIL", "Git hooks are installed in this clone",
                f"core.hooksPath is '{hooks_path or '<unset>'}', expected 'scripts/hooks'.\n"
                "Nothing is enforcing commit format, attribution, or the secret scan.\n"
                "Run: python3 scripts/install_hooks.py",
            )
        else:
            missing = [h for h in HOOKS if not (hooks_dir / h).is_file()]
            # A hook git cannot execute is a hook that silently never fires. On the Drive
            # mount the exec bit could not be expressed at all, so this is the difference
            # between an enforcement layer and the appearance of one. Computed BEFORE the
            # path-form branch below: the absolute-path warning must not be able to hide a
            # hook that is present, wired, and unexecutable.
            not_exec = [h for h in HOOKS if not os.access(hooks_dir / h, os.X_OK)]
            if missing:
                self.result(
                    "FAIL", "Git hooks are installed in this clone",
                    f"core.hooksPath is set but these are missing: {', '.join(missing)}",
                )
            elif not_exec:
                self.result(
                    "FAIL", "Git hooks are executable",
                    f"Present but not executable: {', '.join(not_exec)}\n"
                    "git will skip them silently. Run: chmod +x scripts/hooks/*",
                )
            elif hooks_path != "scripts/hooks":
                self.result(
                    "WARN", "Git hooks are installed in this clone",
                    f"core.hooksPath is '{hooks_path}'.\n"
                    f"All three hooks are present and executable there, so they ARE firing "
                    f"-- this is not an enforcement gap. But an absolute path is "
                    f"machine-specific and will not survive a fresh clone.\n"
                    f"`git worktree add` rewrites it this way; re-run: "
                    f"python3 scripts/install_hooks.py",
                )
            else:
                self.result(
                    "PASS",
                    "Git hooks are installed in this clone (pre-commit, commit-msg, pre-push)",
                )

        fb = self.repo / "workspace" / "feedback"
        if not fb.is_dir():
            self.result(
                "FAIL", "workspace/feedback exists",
                "The channel described in AGENTS.md is missing.",
            )
            return
        open_notes = []
        for f in sorted(fb.glob("*.md")):
            if f.name == "README.md":
                continue
            head = "\n".join(
                f.read_text(encoding="utf-8", errors="replace").split("\n")[:12]
            )
            if re.search(r"^status:\s*OPEN", head, re.MULTILINE):
                open_notes.append(f.name)
        if open_notes:
            self.result(
                "WARN", "No unresolved feedback",
                f"{len(open_notes)} open note(s) in workspace/feedback/:\n"
                + "\n".join(open_notes[:8]),
            )
        else:
            self.result("PASS", "No unresolved feedback")

    # ==============================================================================
    def check_deferrals(self):
        """Session review (Jas, 2026-09-28): the manager reads finished sessions for work put off
        without a task and problems left unfiled, and runs an RCA per finding. WARN only when the
        queue looks stuck -- a session queued for more than two days."""
        try:
            sys.path.insert(0, str(self.repo / "scripts"))
            import session_review as sr
            st = sr.status()
            old = [f.name for f in sr.pending_requests() if time.time() - f.stat().st_mtime > 2 * 86400]
        except Exception as e:                                      # noqa: BLE001
            self.result("WARN", "Session review is moving",
                        f"Nothing was checked -- this is not a pass: {type(e).__name__}: {e}")
            return
        line = (f"{st['queued']} session(s) queued, {st['findings_waiting']} finding(s) awaiting RCA; "
                f"{st['reviewed']} reviewed, {st['rcas']} RCA(s), {st['tasks']} task(s) filed")
        if old:
            self.result("WARN", "Session review is moving",
                        f"{len(old)} session(s) queued over two days: {', '.join(old[:4])}. {line}")
        else:
            self.result("PASS", f"Session review is moving ({line})")

    # ==============================================================================
    def check_deferral_numbers(self):
        """Every deferral written into STATE.md names a task number for each item (t1092).

        STATE.md is the first file every session reads, and a deferral written there is
        prose: "sweep.py is still unprobed" is read, agreed with, and carried forward.
        taskstore.py, jas_replies.py and QUEUE.md never see a sentence, so it never becomes
        work. scripts/deferral.py did this, wired as a WARN; it was deleted in dfc11082 when
        session_review.py replaced it, and nothing has looked at the file every session
        reads first for deferrals that outlive the session that wrote them.
        """
        name = "Deferred items carry a task number"
        state = self.repo / "STATE.md"
        try:
            text = state.read_text(encoding="utf-8", errors="replace")
        except Exception as e:                                  # noqa: BLE001 -- reported, not skipped
            # Broad on purpose: STATE.md can be missing, a directory, or unreadable bytes,
            # and none of those is a pass. Every cause is named, so none is swallowed.
            self.result("FAIL", name, f"{state}: {type(e).__name__}: {e} -- an unreadable "
                                      "file is not a pass")
            return
        findings = deferral_findings(text)
        if findings:
            detail = [f"{state}: {len(findings)} paragraph(s) name deferred work with no "
                      "task number for every item:"]
            detail += [f"{f['unaccounted']} deferred item(s) with no tNNN: {f['block']}"
                       for f in findings]
            self.result("FAIL", name,
                        "\n".join(detail)
                        + "\n\nA deferral the next session must act on is a task. File it "
                          "(scripts/taskstore.py)\nand put its tNNN in the same paragraph as "
                          "the deferral.")
        else:
            self.result("PASS", name,
                        "no deferral phrasing in STATE.md without a task number")

    # ==============================================================================
    def check_output_budgets(self):
        """Every reasoning profile has room to finish thinking and answer (plan harness #6, 2026-10-19).
        A WARN, not a FAIL: the floors are measured for the coder and inferred for the rest, and the
        settings are Jas's -- the line says which profile and by how much, he decides."""
        name = "Every reasoning profile has room to answer"
        try:
            sys.path.insert(0, str(self.repo / "projects" / "local-inference"))
            import settings as _st
            roles = json.loads((self.repo / "projects" / "local-inference" / "models" / "ROLES.json")
                               .read_text(encoding="utf-8"))["roles"]
            found = _st.budget_findings(roles=roles)
        except Exception as e:                                  # noqa: BLE001 -- reported, not skipped
            self.result("WARN", name, f"not checked ({type(e).__name__}: {e})")
            return
        if found:
            self.result("WARN", name, "\n".join(found) + "\n\nReasoning is billed to num_predict: a budget "
                        "under the floor ends cut off (the Supervisor at 2000 gave 342-character decisions).")
        else:
            self.result("PASS", name, "every think level has its floor (settings.THINK_FLOOR)")

    # ==============================================================================
    def check_assignee_literals(self):
        """No script routes a task to a role by writing its name (Jas, 2026-10-19: the org chart).

        Escalation goes to a POSITION in ROLES.json through role_store.escalate(), so renaming,
        moving or deleting a role changes routing with no code edit. A role key written as needs=
        into a script is the stale path Jas asked to be impossible: it keeps routing to a name the
        chart may no longer hold. Tests are exempt (fixtures name roles on purpose)."""
        name = "No script assigns a task to a role by name"
        hits = hardcoded_assignees(self.repo)
        if hits:
            self.result("FAIL", name, "\n".join(hits[:20]) + "\n\nRoute it with role_store.escalate(\"vision\" | "
                        "\"technical\" | \"rework\", from_role) -- the position lives in ROLES.json.")
        else:
            self.result("PASS", name, "every assignment goes through the org chart (role_store.escalate)")

    # ==============================================================================
    def check_task_closure_dod(self):
        """A closed task must answer every clause of its own definition of done with a named
        command.

        Nothing read the definition-of-done block a task writes for the next session to pick up
        cold, so a closure could retire the whole task while verifying one clause of it. t244
        is the verified instance (workspace/rca/52ed69168259.md): status: done, clauses (a),
        (b) and (c), a close note citing scripts/skills.py:128 for (a) alone -- and the lease
        obligation (a) was about lost its carrier seven days before anyone noticed. A clause
        with no command named against it is a subject the closure retired on assertion.
        """
        name = "Closed tasks carry their definition of done"
        tasks = self.repo / "workspace" / "tasks"
        files = sorted(tasks.glob("t*--*.md")) + sorted((tasks / "done").glob("t*--*.md"))
        findings, unreadable, closed = [], [], 0
        for f in files:
            rel = f.relative_to(self.repo)
            # AN UNREADABLE FILE IS REPORTED, NEVER SKIPPED PAST: a task this check cannot read
            # is a closure it has not verified -- the check_secrets lesson, applied here.
            try:
                lines = f.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError as e:
                unreadable.append(f"{rel} -- {type(e).__name__}: {e}")
                continue
            bounds = [n for n, line in enumerate(lines) if line.strip() == "---"]
            fm = lines[bounds[0] + 1:bounds[1]] if len(bounds) >= 2 else []
            stated = [l.partition(":")[2].strip().lower() for l in fm if re.match(r"^status:\s*\S", l)]
            if stated and stated[0] in CLOSED_STATUSES:
                closed += 1
            try:
                findings.extend(closure_carries_dod(f))
            except OSError as e:
                unreadable.append(f"{rel} -- {type(e).__name__}: {e}")
        if findings or unreadable:
            self.result("FAIL", name,
                        "\n".join(findings + [f"NOT CHECKED, so its closure is unverified: {u}"
                                              for u in unreadable])
                        + f"\n\n{closed} closed task(s) scanned. A clause with no command named in"
                        " the close note is a subject the task retired without evidence.")
        elif closed:
            self.result("PASS", name,
                        f"{closed} closed task(s) scanned: every definition-of-done clause is"
                        " answered by a named command in its close note")
        else:
            # NOTHING CLOSED IS NOT A PASS (L-146): a verdict over an examined set of zero
            # reads as coverage the repository does not have.
            self.result("SKIP", name,
                        f"{len(files)} task file(s) read and none of them is closed -- nothing was"
                        " examined, which is not a pass")

    # ==============================================================================
    def _check_server_side_trust(self):
        """The two checks that read records Chat does not write (t487, from the t105 audit): both
        were built with tests and run by nothing -- "built, not called". WARN, never FAIL: each is
        a prompt to look, not proof (a quiet day is not a failure)."""
        import datetime as _dt
        cutoff = (_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=14)).strftime("%Y-%m-%d")
        # 1. Did Chat capture the sessions it was in? (check_captures.py, L-086)
        try:
            r = subprocess.run([sys.executable, str(self.repo / "scripts" / "check_captures.py")],
                               capture_output=True, text=True, timeout=120, cwd=str(self.repo))
            if r.returncode != 0:
                raise RuntimeError((r.stderr or r.stdout or "").strip()[-300:])
            if "nothing to check" in r.stdout:
                self.result("SKIP", "Chat captured the sessions it was in (last 14 days)",
                            "no Chat activity in the call log -- nothing was examined, which is not a pass")
            gaps = None if "nothing to check" in r.stdout else [l.strip() for l in r.stdout.splitlines()
                    if re.match(r"^\s+\d{4}-\d\d-\d\d: \d+ calls", l) and l.strip()[:10] >= cutoff]
            if gaps is None:
                pass                                # SKIP already said above: nothing examined
            elif gaps:
                self.result("WARN", "Chat captured the sessions it was in (last 14 days)",
                            "\n".join(gaps) + "\n\nWindows of Chat activity with nothing appended -- a prompt "
                            "to look, not proof.\nDetail: python3 scripts/check_captures.py")
            else:
                self.result("PASS", "Chat captured the sessions it was in (last 14 days)",
                            "no window of Chat activity without an append")
        except Exception as e:                                      # noqa: BLE001
            self.result("WARN", "Chat captured the sessions it was in (last 14 days)",
                        f"check_captures.py did not run: {type(e).__name__}: {e}")
        # 2. A failed ledger load, from the server's log -- not from the session that failed (t025).
        try:
            sys.path.insert(0, str(self.repo / "scripts"))
            from check_ledger import load_log
            calls = self.repo / "workspace" / "mcp-calls.jsonl"
            if not calls.is_file():
                self.result("SKIP", "No failed ledger load (last 14 days)",
                            "workspace/mcp-calls.jsonl is missing -- nothing was checked, which is not a pass")
                return
            lines = load_log(calls)
            if not lines:
                self.result("SKIP", "No failed ledger load (last 14 days)",
                            "no ledger load on record at all -- nothing was examined, which is not a pass")
                return
            failed = [l for l in lines if "FAILED-LOAD" in l and l[2:12] >= cutoff]
            if failed:
                self.result("WARN", "No failed ledger load (last 14 days)",
                            "\n".join(failed[-5:]) + "\n\nChat tried to read the family ledger and was refused. "
                            "Nobody had to report it:\nthis is read from the server's own call log.")
            else:
                self.result("PASS", "No failed ledger load (last 14 days)",
                            f"{len(lines)} ledger load(s) on record, none failed recently")
        except Exception as e:                                      # noqa: BLE001
            self.result("WARN", "No failed ledger load (last 14 days)",
                        f"the load log could not be generated: {type(e).__name__}: {e}")

    def check_chat_bridge(self):
        self.section("Chat bridge")
        self._check_server_side_trust()

        # chat/ IS TRACKED (D-38, 2026-10-13) -- safe only while the repository has no remote, which
        # check_git asserts on its own. This one checks the corpus really is in git: an untracked
        # chat/ file has no history, which is the exact gap D-38 closed (t036's 2026-10-07 RCA).
        on_disk = [p for p in (self.repo / "chat").rglob("*") if p.is_file()] if (self.repo / "chat").is_dir() else []
        untracked = [x for x in self.git_lines("ls-files", "--others", "--exclude-standard", "chat/") if x]
        if not on_disk:
            self.result("WARN", "chat/ is tracked by git", "chat/ is not present in this checkout")
        elif untracked:
            self.result("WARN", "chat/ is tracked by git",
                        f"{len(untracked)} file(s) under chat/ are not committed, so they have no history:\n"
                        + "\n".join(untracked[:5]) + "\nCommit them (D-38).")
        else:
            self.result("PASS", "chat/ is tracked by git", f"{len(on_disk)} file(s), all committed (D-38)")

        # THE TOOL-CALL MANIFEST IS RETIRED (Jas, 2026-10-06: "We dont need these sessions audited any
        # more. That task predates you using the append function to store chats there."). Its two
        # checks -- every transcript carries a manifest, and the manifest reconciles against
        # mcp-calls.jsonl -- went with check_chat.py. Capture is watched by check_captures.py, which
        # reads the server's own log and needs nothing from Chat. D-20 is superseded by D-NEXT below.

    # ==============================================================================
    def check_drifting_claims(self):
        self.section("Claims that drift")

        # THE ROLE STORE AND ITS GENERATED COPY (2026-09-29). models/ROLES.json is the source;
        # SERVING.json's roles block is rewritten from it on every save for the eighteen scripts
        # that still read SERVING.json. A hand edit to either side is a drift this must catch.
        try:
            import importlib
            sys.path.insert(0, str(self.repo / "scripts"))
            role_store = importlib.import_module("role_store")
            # STRICT SINCE 2026-10-25 (t1594): this ran fk=False because the foreign-key pass
            # reported the chart for the deleted role Senior, the prose lanes and the unnamed
            # Auditor. All three were real drift between FLOW.json and ROLES.json; the lane is
            # DERIVED from the store now, so the pass is clean and belongs in the gate. Lenient
            # here is what let a deleted role sit in the chart for a week.
            probs = role_store.check()
            if probs:
                self.result("FAIL", "The role store and SERVING.json agree", "; ".join(probs))
            else:
                n = len(role_store.load()["roles"])
                self.result("PASS", "The role store and SERVING.json agree",
                            f"{n} roles in models/ROLES.json; SERVING.json roles generated from it")
        except Exception as e:                                      # noqa: BLE001
            self.result("FAIL", "The role store and SERVING.json agree",
                        f"could not check: {type(e).__name__}: {e} -- not a pass")

        # THE CHART'S LANES ARE A FOREIGN KEY (t1592, t761's oracle). A node with
        # worker_kind "role" must name a role by key or name, and every model role must be named
        # by one. WARN, not FAIL, only because the live chart's four violations are Jas's to fix
        # through the Flow tab (t1590) and not a worker's to edit: three nodes name the deleted
        # role "Senior", two name classes, and Auditor has no node. Every finding is printed on
        # every run and recorded by the hourly check. It becomes a FAIL the day t1590 lands --
        # that is a one-word change, "WARN" -> "FAIL", in this block.
        try:
            fk = role_store.flow_fk()
            if fk:
                self.result("WARN", "Every chart lane names a role, and every model role is on the chart",
                            "\n".join(fk) + f"\n\n{len(fk)} finding(s); t1590 (Flow change request) "
                            "is the fix. This becomes a FAIL the day t1590 lands.")
            else:
                self.result("PASS", "Every chart lane names a role, and every model role is on the chart",
                            "role_store.flow_fk() clean")
        except Exception as e:                                      # noqa: BLE001
            self.result("FAIL", "Every chart lane names a role, and every model role is on the chart",
                        f"could not check: {type(e).__name__}: {e} -- not a pass")

        # THE SKILL SCHEMA (plan 3c-i, 2026-09-30). Every live skill meets docs/skills/SCHEMA.md, as
        # scripts/skill_lint.py checks it. A WARN, not a FAIL: a skill below the evidence floor still
        # loads, and the fix is sourcing it (t489), which is not something to block a commit on.
        try:
            skill_lint = importlib.import_module("skill_lint")
            linted = skill_lint.lint_all()
            bad = {k: v for k, v in linted.items() if v}
            if not linted:
                self.result("SKIP", "Every skill meets the skill schema",
                            "no skills in docs/skills/ -- nothing was linted, which is not a pass")
            elif bad:
                self.result("WARN", "Every skill meets the skill schema",
                            "; ".join(f"{k}: " + ", ".join(r for r, _ in v) for k, v in sorted(bad.items()))
                            + " -- python3 scripts/skill_lint.py --all")
            else:
                self.result("PASS", "Every skill meets the skill schema", "skill_lint --all clean")
        except Exception as e:                                      # noqa: BLE001
            self.result("FAIL", "Every skill meets the skill schema",
                        f"could not check: {type(e).__name__}: {e} -- not a pass")

        iso_dir = self.repo / "projects" / "iso-assistant" / "ISO PDFs"
        if not iso_dir.is_dir():
            self.result(
                "SKIP", "ISO PDF tracking matches the recorded decision",
                "Corpus folder not present.",
            )
        else:
            on_disk = len([p for p in iso_dir.glob("*.pdf") if p.is_file()])
            tracked = len(self.git_lines("ls-files", "*.pdf"))
            if EXPECT_ISO_PDFS == "tracked":
                if tracked < on_disk:
                    self.result(
                        "FAIL", "ISO corpus matches the recorded decision",
                        f"Decision is 'tracked', but only {tracked} of {on_disk} PDFs "
                        "are in git.\nprojects/iso-assistant/.gitignore excludes "
                        "'ISO PDFs/' -- remove that line.",
                    )
                else:
                    self.result(
                        "PASS",
                        f"ISO corpus matches the recorded decision (tracked, {tracked} PDFs)",
                    )
            elif EXPECT_ISO_PDFS == "untracked":
                if tracked > 0:
                    self.result(
                        "WARN", "ISO corpus matches the recorded decision",
                        f"Decision is 'untracked', but {tracked} PDF(s) are in git.",
                    )
                else:
                    self.result(
                        "PASS",
                        "ISO corpus matches the recorded decision "
                        f"(untracked by choice; {on_disk} PDFs on disk only)",
                    )
            else:
                self.result(
                    "WARN", "ISO corpus matches the recorded decision",
                    f"UNDECIDED -- {on_disk} PDFs on disk, {tracked} tracked.\n"
                    "They are therefore NOT on the git remote, so the backup does not "
                    "cover them.",
                )

        # WifiWatchdog runs from outside the repo, on Windows. On Linux this is SKIP.
        live = Path(WIFI_WATCHDOG_LIVE_DIR)
        if not live.is_dir():
            self.result(
                "SKIP", "WifiWatchdog repo copy matches the live deployment",
                f"{WIFI_WATCHDOG_LIVE_DIR} not present (not this machine).\n"
                "wifi-watchdog stays on the Windows box by decision; it repairs that\n"
                "machine's Wi-Fi. Not checked here -- which is not the same as clean.",
            )
        else:
            repo_scripts = self.repo / "projects" / "wifi-watchdog" / "scripts"
            diverged, checked = [], 0
            for name in ("wifi-watchdog.ps1", "install-task.ps1"):
                a, b = live / name, repo_scripts / name
                if a.is_file() and b.is_file():
                    checked += 1
                    if (hashlib.md5(a.read_bytes()).hexdigest()
                            != hashlib.md5(b.read_bytes()).hexdigest()):
                        diverged.append(name)
                else:
                    diverged.append(f"{name} (missing on one side)")
            if diverged:
                self.result(
                    "FAIL", "WifiWatchdog repo copy matches the live deployment",
                    "Diverged: " + ", ".join(diverged)
                    + "\nThe live copy is what actually runs. Reconcile before editing either.",
                )
            else:
                self.result(
                    "PASS",
                    f"WifiWatchdog repo copy matches the live deployment ({checked} files, MD5)",
                )

        # STATE.md is the file every session reads first. A stale one fails silently,
        # which is the worst kind of failure this system can have.
        state = self.repo / "STATE.md"
        if not state.is_file():
            self.result("FAIL", "STATE.md exists", "It is the entry point for every session.")
        else:
            head = "\n".join(
                state.read_text(encoding="utf-8", errors="replace").split("\n")[:10]
            )
            m = re.search(r"\*\*Last updated:\*\*\s*(\d{4}-\d{2}-\d{2})", head)
            if not m:
                self.result(
                    "WARN", "STATE.md records when it was last updated",
                    'No "**Last updated:** YYYY-MM-DD" line found in the first 10 lines.',
                )
            else:
                state_date = datetime.strptime(m.group(1), "%Y-%m-%d").date()
                raw = self.git_lines("log", "-1", "--format=%cI")
                if raw:
                    commit_date = datetime.fromisoformat(raw[0]).date()
                    lag = (commit_date - state_date).days
                    if lag > 3:
                        self.result(
                            "WARN", "STATE.md is current with the last commit",
                            f"STATE.md says {m.group(1)}; the last commit is "
                            f"{commit_date.isoformat()} -- {lag} days later.\n"
                            "If work happened since, STATE.md is stale and the next "
                            "session will start from a wrong picture.",
                        )
                    else:
                        self.result(
                            "PASS",
                            f"STATE.md is current with the last commit (updated {m.group(1)})",
                        )

        # The generated index is what Claude Chat reads instead of crawling eight files.
        # A stale one is worse than none: Chat cannot tell and will answer confidently.
        index = self.repo / "workspace" / "PROJECT-INDEX.md"
        if not index.is_file():
            self.result(
                "WARN", "PROJECT-INDEX.md is current",
                "Not generated yet. Run: python3 scripts/build_index.py",
            )
        else:
            index_mtime = index.stat().st_mtime
            sources = []
            st = self.repo / "STATE.md"
            if st.is_file():
                sources.append(st)
            pdir = self.repo / "projects"
            if pdir.is_dir():
                for d in sorted(p for p in pdir.iterdir() if p.is_dir()):
                    s = d / "STATUS.md"
                    if s.is_file():
                        sources.append(s)
            newer = [s for s in sources if s.stat().st_mtime > index_mtime]
            if newer:
                self.result(
                    "WARN", "PROJECT-INDEX.md is current",
                    f"{len(newer)} source file(s) changed after the index was built:\n"
                    + "\n".join(str(s.relative_to(self.repo)) for s in newer)
                    + "\nRun: python3 scripts/build_index.py",
                )
            else:
                self.result("PASS", "PROJECT-INDEX.md is current")

    # ==============================================================================


    def check_served_prose(self):
        """A component's own description of its capabilities must match its constants.

        L-085: workspace-mcp told every client "chat/ and workspace/private/ are not served"
        while READ_ONLY_PREFIXES has always contained chat/. Chat is handed that sentence
        first, and the corpus is Chat's memory -- a client told a capability is absent never
        exercises it, so the error produces no error.

        Every other check here asks whether the server ENFORCES its rules. All passed and all
        were correct. Nothing asked whether the sentence it hands callers is TRUE. Prose that
        ships inside a running system is code with no test.
        """
        self.section("Prose the system serves about itself")

        server = self.repo / "scripts" / "workspace_mcp" / "server.py"
        core = self.repo / "scripts" / "workspace_mcp" / "core.py"
        if not server.is_file() or not core.is_file():
            return

        src = server.read_text(encoding="utf-8")
        m = re.search(r"instructions=\(\s*(.*?)\s*\),", src, re.S)
        if not m:
            self.result("FAIL", "The MCP server's instructions can be located",
                        "No instructions=(...) block found in server.py.")
            return
        prose = " ".join(re.findall(r'"([^"]*)"', m.group(1)))

        csrc = core.read_text(encoding="utf-8")

        def consts(name):
            # Was `[^)]*`, which stops at the first close paren -- the identical defect the
            # agent spent three tasks removing from check_deployed.py, still live here.
            return set(pysource.constant(csrc, name) or ())

        read_only = consts("READ_ONLY_PREFIXES")
        denied = consts("DENIED_PREFIXES")
        # pysource.constant returns None when the value is not a simple literal (a
        # generator expression, a function call, a tuple comprehension). `or ()` then
        # yields an empty set, both loops below skip, problems stays empty, and this
        # reports PASS having examined nothing. The identical vacuous-pass shape at
        # check_apps:168 and check_deployed:161. A refactor of core.py that moves
        # either constant off a literal would silently disable this check while it
        # still prints green.
        if not read_only and not denied:
            self.result("FAIL", "The MCP server describes its own paths correctly",
                        "Could not read READ_ONLY_PREFIXES or DENIED_PREFIXES from core.py "
                        "(pysource returned None for both). "
                        "Nothing was examined -- this is not a pass.")
            return

        problems = []
        for path in sorted(read_only):
            claim = re.search(r"([^.]*\b" + re.escape(path) + r"[^.]*not served[^.]*\.)", prose)
            if claim:
                problems.append(
                    f"instructions say {path} is NOT served, but READ_ONLY_PREFIXES contains "
                    f"it -- it IS served, read-only. Sentence: \"{claim.group(1).strip()}\"")
        for path in sorted(denied):
            if path.startswith("."):
                continue
            if path not in prose:
                problems.append(f"{path} is in DENIED_PREFIXES but the instructions never "
                                f"mention it, so a caller learns it only by being refused")

        if problems:
            self.result("FAIL", "The MCP server describes its own paths correctly",
                        "\n".join(problems))
        else:
            self.result("PASS", "The MCP server describes its own paths correctly "
                                f"({len(read_only)} read-only, {len(denied)} denied)")

    def check_task_queue(self):
        """QUEUE.md and the task files must name the same set.

        The queue is a second list of the same things, which is the drift this repository
        keeps finding. Two ways it breaks: a task file that no line points at (invisible in
        the plan, so it never gets done) and a queue line naming a task that is not there
        (a plan promising work that does not exist).

        Proven by fixture, not by hope -- scripts/tests/test_task_queue.py builds both
        failures and requires this check to report them.
        """
        self.section("The plan")

        tasks_dir = self.repo / "workspace" / "tasks"
        if not tasks_dir.is_dir():
            return

        on_disk = {}
        for f in sorted(tasks_dir.glob("t*--*.md")):
            m = re.match(r"(t\d+)--", f.name)
            if m:
                on_disk[m.group(1)] = f.name

        queued, dupes = [], []
        qf = tasks_dir / "QUEUE.md"
        if qf.is_file():
            for line in qf.read_text(encoding="utf-8").splitlines():
                m = re.match(r"\s*\d+\.\s+(t\d+)\b", line)
                if m:
                    (dupes if m.group(1) in queued else queued).append(m.group(1))

        missing = sorted(set(on_disk) - set(queued))
        phantom = sorted(set(queued) - set(on_disk))

        if missing or phantom or dupes:
            detail = []
            for tid in missing:
                detail.append(f"not in QUEUE.md, so invisible in the plan: {on_disk[tid]}")
            for tid in phantom:
                detail.append(f"QUEUE.md names a task that is not on disk: {tid}")
            for tid in sorted(set(dupes)):
                detail.append(f"listed more than once in QUEUE.md: {tid}")
            self.result("FAIL", "QUEUE.md and the task files name the same set",
                        "\n".join(detail))
        else:
            self.result("PASS", "QUEUE.md and the task files name the same set "
                                f"({len(on_disk)} task(s))")

        stray = []
        done_dir = tasks_dir / "done"
        if done_dir.is_dir():
            for f in sorted(done_dir.glob("t*--*.md")):
                # done/ holds CANCELLED tasks as well as done ones (taskstore LEAVES_QUEUE, 2026-09-14);
                # both have left the queue, and RESOLVED.md keeps them apart. Read whole: the status
                # line is in the frontmatter, and a fixed-width head was a silent cut (library check).
                if not re.search(r"^status: (done|cancelled)\s*$", f.read_text(encoding="utf-8"), re.M):
                    stray.append(f"done/{f.name} is filed as done but its status is neither done nor cancelled")
        for tid, name in on_disk.items():
            front = (tasks_dir / name).read_text(encoding="utf-8").split("---", 2)
            if len(front) > 2 and re.search(r"^status: done\s*$", front[1], re.M):
                stray.append(f"{name} is marked done but still sits in the queue folder")
        if stray:
            self.result("FAIL", "Finished tasks are filed as finished", "\n".join(stray))
        else:
            self.result("PASS", "Finished tasks are filed as finished")

    def check_review_gate(self):
        """The preflight gate records every refusal; until now nothing ever read the file.

        Jas, 2026-09-15: "Please make sure those errors are tracked in the log mechanically."
        The ledger has been correct and unread. On 2026-09-15 thirteen chunks had been parked
        for a day and twenty-four consecutive reviews had refused every brief ever submitted,
        and both were found by a session opening the file for an unrelated reason. A truncated
        review is the specific shape that hides: preflight_brief.py is honest about it, returns
        2 and prints "not an answer", but downstream it is indistinguishable from a real
        objection, so it parks a chunk exactly as a genuine refusal does.
        """
        import json
        self.section("The review gate")
        led = self.repo / "workspace" / "preflight-objections.jsonl"
        if not led.is_file():
            self.result("SKIP", "Preflight reviews are recorded",
                        "No review has been recorded yet. Nothing was checked -- this is not\n"
                        "a pass. It starts reporting the moment the first review lands.")
            return
        rows, unreadable = [], []
        for n, line in enumerate(led.read_text(encoding="utf-8",
                                               errors="replace").splitlines(), 1):
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except ValueError as e:
                # NOT SWALLOWED. A ledger nobody can parse is the same failure as a ledger
                # nobody reads, which is the whole reason this check exists -- so a bad line
                # is reported, not skipped past.
                unreadable.append(f"  line {n}: {e}")
        if unreadable:
            self.result("FAIL", "Every preflight review row is readable",
                        "\n".join(unreadable))
        if not rows:
            self.result("SKIP", "Preflight reviews are recorded", "The ledger is empty.")
            return

        recent = rows[-10:]
        cut = [r for r in recent
               if r.get("done_reason") == "length" or r.get("verdict") == "TRUNCATED"]
        if cut:
            detail = ["A review cut off at its token budget is NOT a verdict, but it sets",
                      "stopped=True and parks the chunk exactly as a real objection does.",
                      ""]
            for r in cut:
                detail.append(f"  {r.get('at')}  {int(r.get('seconds') or 0) // 60} min  "
                              f"think={r.get('think', 'unrecorded')}  "
                              f"{str(r.get('brief', ''))[-46:]}")
            detail.append("")
            detail.append("Lower the reasoning or show the reviewer less; do NOT raise the")
            detail.append("budget alone -- t305 measured that making it worse, not better.")
            self.result("WARN", f"No preflight review was truncated ({len(cut)} of the last "
                                f"{len(recent)} were)", "\n".join(detail))
        else:
            self.result("PASS", "No preflight review was truncated",
                        f"the last {len(recent)} review(s) each returned a verdict")

        cleared = [r for r in rows if not r.get("stopped")]
        if not cleared and len(rows) >= 10:
            self.result("WARN", "The gate has cleared at least one brief",
                        f"{len(rows)} reviews recorded and NOT ONE has ever been cleared.\n"
                        "Every brief submitted has been stopped. A gate that has refused\n"
                        "everything it has ever seen is far more likely to be miscalibrated,\n"
                        "or to be missing something it needs to see, than to be right every\n"
                        "time -- and nothing reaches production while it is shut.")
        else:
            self.result("PASS", "The gate has cleared at least one brief",
                        f"{len(cleared)} of {len(rows)} review(s) cleared")

        # --- script-reviews the reviewer has given up on ----------------------------
        # The scheduler takes the OLDEST pending request each tick, so an unreadable one does not
        # just waste its own turn -- it holds the head of the queue for as long as it survives.
        # truncation_review caps the retries; the cap has to be SAID somewhere a person reads, or
        # it trades a loud loop for a silent gap, which is t324's failure rather than a fix.
        try:
            sys.path.insert(0, str(self.repo / "scripts"))
            import truncation_review
            gone = truncation_review.abandoned()
        except Exception as e:                                          # noqa: BLE001
            self.result("WARN", "Abandoned script-reviews are reported",
                        f"truncation_review.abandoned() could not be read: {e}")
        else:
            if gone:
                detail = [f"Given up after {truncation_review.UNREADABLE_CAP} unreadable replies "
                          f"at the same content.", "",
                          *(f"  {a['attempts']:3d} attempt(s)  {a['path']}" for a in gone), "",
                          "The queue moves again, but these are reviewed by nobody until the",
                          "file changes or a person looks. Read the reply in",
                          "workspace/truncation-review.jsonl: an identical failure every time",
                          "is a defect in what the model was asked for, not its ceiling."]
                self.result("WARN", f"No script-review has been given up on ({len(gone)} has)",
                            "\n".join(detail))
            else:
                self.result("PASS", "No script-review has been given up on",
                            "no request has hit the unreadable-reply cap at its current content")

        # --- the two review ledgers must agree -------------------------------------
        # dispatch-loop.jsonl records what the JOB did; truncation-review.jsonl records what the
        # REVIEW said. On 2026-09-15 they disagreed 29 times about the same file and the cheerful
        # one -- "exit 0 ACCEPTED" -- is the one --status prints, so a 7.7-hour failure was
        # invisible to anyone who did not open the other file. Rows before the cutoff were written
        # by the code that could not tell the difference and cannot be corrected now; the check
        # exists to catch the NEXT divergence, which is the only one anybody can act on.
        TRUSTWORTHY_SINCE = "2026-09-15T23:00:00Z"
        disp = self.repo / "workspace" / "dispatch-loop.jsonl"
        try:
            import truncation_review
            reviews = {}
            for r in truncation_review.rows():
                if r.get("path"):
                    reviews[(r["path"], r.get("sha256"))] = r.get("status")
            wrong, checked, torn = [], 0, []
            for n, line in enumerate((disp.read_text(encoding="utf-8", errors="replace")
                                      .splitlines() if disp.is_file() else []), 1):
                if not line.strip():
                    continue
                try:
                    r = json.loads(line)
                except ValueError as e:
                    # NOT SKIPPED, for the same reason as the preflight ledger above: a row
                    # nobody can parse is a row nobody can reconcile, and this check exists
                    # because an unread ledger is how the divergence survived 7.7 hours.
                    torn.append(f"  dispatch-loop.jsonl line {n}: {e}")
                    continue
                if (r.get("event") != "finished" or r.get("kind") != "script-review"
                        or str(r.get("at") or "") < TRUSTWORTHY_SINCE):
                    continue
                if r.get("pid") == 0 and r.get("reason"):
                    continue        # launch() refused it (7d304c8a): no review ran, nothing to agree on
                if r.get("lost"):
                    # THE SCHEDULER'S GUESS, NOT THE JOB'S RECORD (2026-10-20). A "lost" row is written when
                    # a process vanished (exit -9: the memory guard or oomd) -- after, twice that day, the
                    # review itself had finished and written "ok". Same footing as the refusal above.
                    continue
                checked += 1
                said = r.get("review_status")
                if said is None:
                    said = reviews.get((r.get("chunk"), r.get("brief_sha256")), "no row written")
                if bool(r.get("accepted")) != (said == "ok"):
                    wrong.append(f"  {r.get('at')}  {r.get('chunk')}\n"
                                 f"      dispatch says accepted={r.get('accepted')}, "
                                 f"the review says {said!r}")
        except Exception as e:                                          # noqa: BLE001
            self.result("WARN", "The two review ledgers agree",
                        f"could not be reconciled: {e}")
        else:
            if torn:
                self.result("FAIL", "Every dispatch ledger row is readable", "\n".join(torn))
            if wrong:
                self.result("FAIL", f"The two review ledgers agree ({len(wrong)} of {checked} "
                                    f"disagree)",
                            "\n".join(wrong) + "\n\nA job that ran fine and a review that said "
                            "nothing usable are not the same\noutcome. Whichever ledger is "
                            "wrong, --status is reporting work that\ndid not happen.")
            elif checked:
                self.result("PASS", "The two review ledgers agree",
                            f"{checked} script-review(s) since {TRUSTWORTHY_SINCE[:10]} record "
                            f"the same outcome in both ledgers")
            else:
                self.result("SKIP", "The two review ledgers agree",
                            f"No script-review has finished since {TRUSTWORTHY_SINCE[:10]}.\n"
                            f"Nothing was checked -- this is not a pass. It starts reporting\n"
                            f"with the next review.")

    def check_warning_clean(self):
        """Every tracked .py compiles without a SyntaxWarning. A guard that warns is suspect.

        Q4 filed this on 2026-09-18 (F6) and left it DECLARED-OPEN rather than guess, which
        was right: it had grepped scripts/*.py for a non-raw `\\{` and found none. Three
        `SyntaxWarning: invalid escape sequence '\\{'` lines appeared on every session-start
        run anyway. Located 2026-09-22 -- check_enforcement_discipline calls ast.parse() on
        each test suite's source, and parsing from a STRING labels everything `<unknown>`,
        so the warnings named no file and the offending text was in the files being PARSED,
        not in the code doing the parsing. Three docstrings describing a greedy `{.*}` regex
        were not raw strings.

        Q4's own P2. Its reasoning is the reason this is a check and not a one-time fix: the
        defect is in the instrument's compilation hygiene, which a verdict-level test cannot
        see, because the warning never changes the verdict. A guard that warns while
        policing is the exact shape that erodes trust in the one that catches you.
        """
        self.section("Compilation hygiene")
        # workspace/run-logs/ is CAPTURED MODEL OUTPUT, not source. It is excluded and the
        # count is printed, because a silent exclusion is how a check quietly stops covering
        # things. Concretely: DRAFT-test_coder_uses_jsonx.py is a model's PROSE preamble
        # ("I don't have the exact parse() source, so I'll...") saved with a .py extension --
        # an instance of the harness-edit-application class in workspace/failure-schema.json,
        # and evidence about a model rather than a defect in the instrument. It cannot be
        # renamed away either: HANDOVER-2026-09-12.md and a session log both name it, and
        # those are records that must not be rewritten.
        skip = (".venv/", "node_modules/", "vendor/", "workspace/run-logs/")
        allpy = self.git_lines("ls-files", "*.py")
        files = [f for f in allpy if not any(p in f for p in skip)]
        excluded = len(allpy) - len(files)
        if not files:
            self.result("SKIP", "Every tracked .py compiles without a SyntaxWarning",
                        "no tracked .py found -- nothing was measured, which is not a pass.")
            return
        r = subprocess.run(
            [sys.executable, "-W", "error::SyntaxWarning", "-c",
             # -W error::SyntaxWarning turns the warning into an EXCEPTION, and it is not a
             # SyntaxError -- so py_compile's own doraise does not catch it and it escapes.
             # Catching Exception is right here: anything that stops a file compiling cleanly
             # is the finding, and the message is reported rather than swallowed.
             # compile() in memory rather than py_compile: nothing is written, so there is no
             # cfile to place and no chance of this check leaving artefacts in the tree it is
             # inspecting. The filename argument is what makes the message name its source --
             # the same omission that hid this defect for four days.
             "import sys\n"
             "bad = []\n"
             "for f in sys.argv[1:]:\n"
             "    try:\n"
             "        src = open(f, encoding='utf-8', errors='replace').read()\n"
             "        compile(src, f, 'exec')\n"
             "    except Exception as e:\n"
             "        m = str(getattr(e, 'msg', e)).strip().splitlines()\n"
             "        bad.append(f'{f}: {type(e).__name__}: {m[-1][:150] if m else e}')\n"
             "print('\\n'.join(bad))\n"
             "sys.exit(1 if bad else 0)", *files],
            capture_output=True, text=True, cwd=self.repo, timeout=300)
        if r.returncode:
            self.result("FAIL", "Every tracked .py compiles without a SyntaxWarning",
                        r.stdout.strip()[:900] + "\n\nA warning here is not cosmetic: it is "
                        "the instrument reporting that its own source is unwell, and it "
                        "prints on every session start until somebody stops reading stderr.")
        else:
            self.result("PASS", "Every tracked .py compiles without a SyntaxWarning "
                                f"({len(files)} file(s)"
                                + (f"; {excluded} captured-output file(s) excluded)"
                                   if excluded else ")"))

    def check_headroom_waits(self):
        """How often the memory gate stopped a dispatch -- because nobody should have to watch.

        The gate added 2026-09-22 makes a tick WAIT rather than cold-load a model that would
        evict the resident one. It is deliberately conservative (FOOTPRINT_FACTOR 1.5 plus a
        12 GB reserve), so it can be RIGHT and still be too tight: production quietly stops
        instead of thrashing, which is better but is not free. Its first real wait, twenty
        minutes after it went in, missed by 1 GB.

        Jas asked the question this check exists to answer -- "when do we stop watching, and
        how will we be triggered to review?" Watching is the wrong answer: it depends on
        somebody remembering, which is the failure mode this repository keeps finding. So the
        ledger is read at session start and the numbers are put in front of whoever is next.

        A wait is NOT a fault. It is only worth a person's attention when it is most of what
        the loop is doing, which is why this reports a RATE and not a count.
        """
        import json
        from datetime import timedelta
        self.section("The memory gate")
        ledger = self.repo / "workspace" / "dispatch-loop.jsonl"
        if not ledger.exists():
            self.result("SKIP", "The memory gate is not over-waiting",
                        "no dispatch-loop.jsonl yet -- nothing was measured, which is not a pass.")
            return
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ")
        ticks = noroom = 0
        torn = []
        latest = ""
        for n, line in enumerate(ledger.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            try:
                row = json.loads(line)
            except ValueError as e:
                # A torn line is not noise to skip past: it is a tick this check CANNOT see,
                # so the rate below is computed over an incomplete ledger and must say so.
                # check_review_gate does the same with the same file (see `torn` there).
                torn.append(f"line {n}: {e}")
                continue
            if row.get("at", "") < cutoff or row.get("event") != "tick":
                continue
            ticks += 1
            if str(row.get("say", "")).startswith("NO ROOM"):
                noroom += 1
                latest = row.get("say", "")
        if not ticks:
            self.result("SKIP", "The memory gate is not over-waiting",
                        "the loop has not ticked in 24h (paused, or the cron is not firing).\n"
                        "Nothing was measured -- this is not a pass.")
            return
        rate = noroom / ticks
        detail = f"{noroom} of {ticks} tick(s) in 24h waited for memory ({rate:.0%})."
        if torn:
            detail += (f"\n  {len(torn)} unreadable ledger line(s), so this rate is over an "
                       f"INCOMPLETE record: {'; '.join(torn[:3])}")
        if noroom and rate >= 0.5:
            self.result("WARN", "The memory gate is not over-waiting",
                        f"{detail}\nProduction is mostly WAITING, not building. The gate is "
                        f"doing its job; the question is whether the box can hold what is "
                        f"resident PLUS a production model at all.\n  {latest}\n"
                        f"Levers, in order: unload a model nothing is using (`ollama ps`); "
                        f"lower DISPATCH_RESERVE_GB (default 12); run production on the night "
                        f"queue while the interactive model is down. Do NOT raise "
                        f"FOOTPRINT_FACTOR -- it is a measurement, not a preference.")
        elif noroom:
            self.result("PASS", "The memory gate is not over-waiting",
                        f"{detail} Below the half-the-ticks threshold, so this is the gate "
                        f"working rather than a box that is too full.")
        else:
            self.result("PASS", "The memory gate is not over-waiting",
                        f"{ticks} tick(s) in 24h, none stopped for memory.")

    def check_skill_registry(self):
        """ONE skill registry for every harness (t482/t483/t484, 2026-10-01). DSH discovers skills at
        <project>/.agents/skills (and .dsh/skills); ours live in docs/skills, so the DSH skill tool
        answered "no longer available" for every one of them. .agents/skills is a symlink to
        docs/skills; this fails if it stops resolving there, so the two lists cannot drift."""
        self.section("One skill registry")
        link = self.repo / ".agents" / "skills"
        want = (self.repo / "docs" / "skills").resolve()
        if not link.exists() or link.resolve() != want:
            self.result("FAIL", "DSH sees the same skills as docs/skills",
                        f"{link} does not resolve to {want}. Fix: ln -sfn ../docs/skills .agents/skills")
            return
        if (self.repo / ".dsh" / "skills").exists():
            self.result("FAIL", "DSH sees the same skills as docs/skills",
                        ".dsh/skills exists and outranks .agents/skills in DSH -- a second registry")
            return
        n = len(list(want.glob("*/SKILL.md")))
        self.result("PASS", "DSH sees the same skills as docs/skills", f"{n} skills, via .agents/skills")

    def check_flow_record(self):
        """Every permanent script is on the Flow chart -- the chart is the system of record.

        Jas, 2026-10-01: "any permanent script must be represented in the flowchart". library.py
        refuses to register an off-chart script, so this can only fail if the chart lost one (a
        task or helper deleted) or a script was registered by hand-editing library.json."""
        self.section("The Flow chart is the system of record")
        try:
            sys.path.insert(0, str(self.repo / "scripts"))
            import flow_coverage
            import flow_lint
            miss = flow_coverage.uncovered()
            total = len(flow_coverage.permanent())
            d = flow_lint.load()
        except Exception as e:                                          # noqa: BLE001
            self.result("FAIL", "Every permanent script is on the Flow chart",
                        f"could not check: {type(e).__name__}: {e}")
            return
        if miss:
            self.result("FAIL", "Every permanent script is on the Flow chart",
                        f"{len(miss)} of {total} are not:\n" + "\n".join(f"  {p}" for p in miss)
                        + "\n  python3 scripts/flow_change.py helper <path> <task-id> \"what it does\"")
        else:
            self.result("PASS", "Every permanent script is on the Flow chart", f"{total} of {total}")
        # STANDING PROCESSES TOO (t123, 2026-10-01): every enabled user unit that runs this
        # repository's code, and every cron line, must be named on the chart.
        import subprocess as _sp
        # A PROJECTION, NOT A MENTION (2026-10-30). This used to pass when a unit's NAME appeared anywhere in
        # FLOW.json -- including in another node's prose -- so "restart-stale" being discussed in a note counted as
        # the schedule being on the chart. It also found timers by looking for the repo path in the TIMER's own unit
        # file, which is not where ExecStart lives. flow_schedules asks the thing that owns the schedule (systemd,
        # crontab, /etc/cron.d), keeps only what runs this repository, and compares against the chart's generated
        # `schedules` block. A timer added tomorrow is therefore caught the first time anyone looks.
        off = []
        try:
            sys.path.insert(0, str(self.repo / "scripts"))   # SCRIPTS is not a name in this module
            import flow_schedules
            for sid, why in flow_schedules.problems():
                off.append(f"{sid}  ({why})")
        except Exception as e:                                      # noqa: BLE001 -- said, not swallowed
            off.append(f"(could not read the schedule projection: {type(e).__name__}: {e})")
        if off:
            self.result("FAIL", "Every standing process is on the Flow chart",
                        "scheduled work this chart does not carry:\n" + "\n".join(f"  {x}" for x in off)
                        + "\n  python3 scripts/flow_schedules.py --sync")
        else:
            self.result("PASS", "Every standing process is on the Flow chart",
                        f"{len(flow_schedules.snapshot())} scheduled job(s), every one on the chart")
        gone = [h["script"] for h in d.get("helpers", []) if not (self.repo / h["script"]).exists()]
        loose = [h["script"] for h in d.get("helpers", []) if not h.get("serves") and h.get("runs") != "inactive"]
        if gone or loose:
            self.result("WARN", "Every supporting script on the chart serves a task and exists",
                        (f"{len(loose)} attached to no task or service (a process the chart does not "
                         f"show; Flow tab, Supporting scripts):\n" + "\n".join(f"  {p}" for p in loose)
                         if loose else "")
                        + (("\n" if loose else "") + f"{len(gone)} no longer on disk:\n"
                           + "\n".join(f"  {p}" for p in gone) if gone else ""))
        else:
            self.result("PASS", "Every supporting script on the chart serves a task and exists",
                        f"{len(d.get('helpers', []))} helpers")

    def check_production_failures(self):
        """Every production failure, classified -- because most of them nothing acts on.

        Jas, 2026-09-15: "All failures need documentation, so when we finally get caught up on
        the backlog, we can go through the failures and understand how to better fix these at
        the beginning of the pipeline." Only an attempt failure (exit 1) earns a supervision
        decision; a refusal to start and a delivery fault are not judgement calls, so without
        this they are documented nowhere a person looks. On 2026-09-15 eight refusals and four
        delivery faults each existed only as a line in a cron log, and the eight repeated the
        same precondition for 7.7 hours.
        """
        self.section("Production failures")
        try:
            sys.path.insert(0, str(self.repo / "scripts"))
            import supervise
            import dispatch_loop
            import parallel_dispatch
            cat = supervise.failure_catalogue()
            # NO SUITE IS RUN FROM INSIDE THE CHECK (L4, 2026-10-27). Both of these used to pass
            # `lambda t: parallel_dispatch.target_is_red(t)[0]`, which runs that suite as a
            # subprocess with a 600 s timeout -- once per held chunk and once per refused chunk.
            # Measured that day: 51 target suites, two of which hang, and `check_workspace.py
            # --quick` took 15m21s on every session start and every hourly check. It also wrote
            # verdict rows into suite-state.jsonl, so the check was editing the instrument it
            # reads. The recorded verdict is the answer to "is it red": the hourly sweep is what
            # keeps that record true, and a check reads the record.
            red_of, verdicts = ledger_red_of(self.live / "workspace" / "suite-state.jsonl")
            held = dispatch_loop.held_tasks(parallel_dispatch.chunks(), red_of=red_of)
        except Exception as e:                                          # noqa: BLE001
            self.result("WARN", "Production failures are catalogued",
                        f"supervise.failure_catalogue() could not be read: {e}")
            return

        # TASKS HELD AS FUTILE, reported wherever a person will see it. Jas, 2026-09-16: "Hold
        # failed tasks but let the models run what they can." A held task is a WARN and not a
        # FAIL: the pipeline is working as intended -- the models are still building everything
        # else -- and what needs a person is the brief or the oracle, upstream. It has to be
        # said out loud all the same, because a task held silently is a task nobody fixes, and
        # a held chunk was previously one line in a cron log reprinted every ten minutes.
        if held:
            detail = [f"  {c}\n      {why}" for c, why in held]
            self.result("WARN", f"No task is held as futile ({len(held)} held)",
                        "\n".join(detail) + (
                            "\n\nThe models are NOT stopped -- they build every other chunk. "
                            "What is\nheld is these briefs, and the fix for each is upstream: "
                            "the brief or its\noracle. Editing the brief releases it."))
        else:
            self.result("PASS", "No task is held as futile",
                        f"no brief's last {dispatch_loop.FUTILE_AFTER} attempts all came to "
                        f"nothing")

        # --- the harness refusing, as distinct from a task failing -------------------
        # A refusal to start is a precondition the harness did not meet, and it is almost always
        # ONE fault seen from several chunks at once: on 2026-09-16 a single badly-written test
        # suite, red in every sandbox, refused four different chunks inside one tick. Reported
        # per task that reads as four bad briefs; reported together it reads as what it is.
        try:
            # The suites a refusal NAMED, not the chunk's target (2026-10-27). Same ledger read as
            # `held` above -- one dict, no subprocess. `cleared` is not a silence: the count and the
            # reason are printed, so a refusal that has genuinely resolved is distinguishable from
            # one nobody looked at (M2's oracle: 31 refused, or each declared).
            _targets = {c.get("chunk"): c.get("target") for c in parallel_dispatch.chunks()}
            storm = dispatch_loop.refusal_storm(history=dispatch_loop.rows(),
                                               enrolled=set(_targets))
            allowed = set(_ceiling_names(self.live / "workspace" / "test-failures-ceiling.json")
                          or ())
            live, cleared = live_refusals(storm, dispatch_loop.rows(), red_of,
                                          allowed=allowed,
                                          tracked=dispatch_loop.tracked_tests(),
                                          root=self.live, targets=_targets)
        except Exception as e:                                          # noqa: BLE001
            self.result("WARN", "The harness is not refusing to start jobs",
                        f"could not be read: {e}")
        else:
            if live:
                detail = [f"  {r['n']:3d}x  {r['chunk']}   {r['code']}"
                          + (f" ({', '.join(r['named'])})" if r["named"] else "")
                          + f"   (last on {r['model']}, {r['at']})" for r in live[:8]]
                rest = ("" if len(live) <= 8 else
                        f"\n  ... and {len(live) - 8} more; all of them: python3 scripts/"
                        f"check_workspace.py --quick")
                self.result("WARN", f"The harness is not refusing to start jobs "
                                    f"({len(live)} chunk(s) refused)",
                            "\n".join(detail) + rest
                            + "\n\nA refusal is NOT the brief's fault and does not hold the task."
                              "\nIt is a precondition: most often an undeclared red suite in the"
                              "\nsandbox baseline. Check the newest job log for 'REFUSING TO"
                              "\nSTART', and remember a suite THIS session made red refuses"
                              "\nevery chunk on the box, not just its own."
                            + (f"\n\nA further {len(cleared)} refusal(s) are NOT counted: their"
                               " own reason has resolved.\n"
                               + "\n".join(f"  {r['n']:3d}x  {r['chunk']}   {r['code']}: {r['why']}"
                                           for r in cleared[:6])
                               if cleared else ""))
            elif cleared:
                self.result("PASS", "The harness is not refusing to start jobs",
                            f"no chunk is currently being refused a start; {len(cleared)} earlier "
                            "refusal(s) no longer hold:\n"
                            + "\n".join(f"  {r['n']:3d}x  {r['chunk']}   {r['code']}: {r['why']}"
                                        for r in cleared[:8]))
            else:
                self.result("PASS", "The harness is not refusing to start jobs",
                            "no chunk is currently being refused a start")

        # --- can a failure reach the task it belongs to? ----------------------------
        # Jas, 2026-09-16: "can all the error messages and any data related to the production of
        # a specific task be recorded within the task itself? That way, the next time the task
        # comes by, we can look at the failures and learn." A chunk whose brief names no task has
        # nowhere for that to go: its failures live only in the ledger, and the next attempt
        # starts as blind as the last. WARN and not FAIL -- nothing is broken, the learning loop
        # is simply not connected for these.
        try:
            chunks = parallel_dispatch.chunks()
            orphan = [c["chunk"] for c in chunks if not dispatch_loop.task_of(c.get("brief"))]
        except Exception as e:                                          # noqa: BLE001
            self.result("WARN", "Every enrolled chunk can reach its task",
                        f"could not be resolved: {e}")
        else:
            # NOTHING ENROLLED IS NOT A PASS (2026-09-16, L-146). With no chunks the
            # comprehension yields an empty orphan and the PASS branch below announced
            # "all 0 brief(s) name a task" -- a verdict computed over an examined set of
            # zero, which reads exactly like coverage while nothing was looked at. That is
            # the shape this project's own vacuous-site registry exists to catch, and it
            # went in unregistered because session_end never ran to reach the check.
            if not chunks:
                self.result("WARN", "Every enrolled chunk can reach its task",
                            "no chunks are enrolled, so nothing was examined -- "
                            "that is not a pass")
            elif orphan:
                self.result("WARN", f"Every enrolled chunk can reach its task "
                                    f"({len(orphan)} of {len(chunks)} cannot)",
                            "\n".join(f"  {c}" for c in orphan[:12])
                            + (f"\n  ... and {len(orphan) - 12} more" if len(orphan) > 12 else "")
                            + "\n\nA failure on these is recorded in the ledger and nowhere the\n"
                              "task can see it. Fix by adding a line to the brief:\n"
                              "  TASK: tNNN\n"
                              "The compliance chunks are generated by library.py audit --enroll,\n"
                              "which creates no task at all -- that is the larger half of this.")
            else:
                self.result("PASS", "Every enrolled chunk can reach its task",
                            f"all {len(chunks)} brief(s) name a task their failures can be "
                            f"written onto")
        if not cat:
            self.result("PASS", "No production failure is undocumented",
                        "no production job has failed")
            return
        from collections import Counter
        by = Counter(x["kind"] for x in cat)
        undecided = [x for x in cat if x["kind"] == "attempt-failed" and not x["decision"]]
        unactioned = [x for x in cat if x["kind"] != "attempt-failed"]
        detail = [f"  {n:4d}  {k}" for k, n in by.most_common()]
        detail += ["", "Read them with: python3 scripts/supervise.py --failures"]
        if unactioned:
            detail += ["",
                       "A refusal and a delivery fault get no supervision decision by design --",
                       "the log already says what went wrong and switching model cannot help --",
                       "so these are the ones only a PERSON closes:"]
            seen = set()
            for x in unactioned[:6]:
                if x["chunk"] in seen:
                    continue
                seen.add(x["chunk"])
                detail.append(f"    {x['at']}  {x['kind']:20s} {x['chunk']}")
        if undecided:
            self.result("WARN", f"Every attempt failure has a supervision decision "
                                f"({len(undecided)} without)",
                        "\n".join(detail + ["", "Run: python3 scripts/supervise.py"]))
        else:
            self.result("PASS", f"Every attempt failure has a supervision decision "
                                f"({len(cat)} failure(s) catalogued)",
                        "\n".join(detail))

    def check_enforcement_discipline(self):
        """docs/CHECKS.md: a finding is not closed until a script can catch it again.

        Two checks, both testing a CLASS rather than an instance.
        """
        self.section("Checks and lessons")

        # --- every recent lesson declares its enforcement ---------------------------
        lessons = self.repo / "docs" / "LESSONS.md"
        if not lessons.is_file():
            self.result("FAIL", "docs/LESSONS.md exists",
                        "It is the evidence base every rule here points at.")
        else:
            text = lessons.read_text(encoding="utf-8", errors="replace")
            # Split on lesson headings so each entry carries its own body.
            entries = re.split(r"^\*\*(L-\d+)\s*\[", text, flags=re.MULTILINE)
            undeclared = []
            # entries = [preamble, id, body, id, body, ...]
            for i in range(1, len(entries) - 1, 2):
                lid, body = entries[i], entries[i + 1]
                m = re.match(r"\s*(?:VERIFIED|OBSERVED)\s+(\d{4}-\d{2}-\d{2})", body)
                if not m or m.group(1) < ENFORCEMENT_REQUIRED_FROM:
                    continue
                if not re.search(r"^ENFORCEMENT:", body, re.MULTILINE):
                    undeclared.append(f"{lid} ({m.group(1)})")
            if undeclared:
                self.result(
                    "FAIL", "Recent lessons declare how they are enforced",
                    f"{len(undeclared)} lesson(s) dated {ENFORCEMENT_REQUIRED_FROM} or later "
                    "carry no ENFORCEMENT: line:\n" + "\n".join(undeclared)
                    + "\n\ndocs/CHECKS.md: a finding that produces a fix and a lesson but no "
                      "check\nwill recur. If it cannot be mechanised, say "
                      "'ENFORCEMENT: none — judgement' and why.",
                )
            else:
                self.result("PASS",
                            "Recent lessons declare how they are enforced "
                            f"(since {ENFORCEMENT_REQUIRED_FROM})")

        # --- a lesson in the wrong shape is invisible, not merely untidy -------------
        # On 2026-08-26 I wrote L-120 and L-121 as `## L-120 — title` headings. Every other
        # lesson is `**L-nnn [VERIFIED date] title**`. The consequences were silent and
        # total: the ENFORCEMENT check above splits on the canonical form and never saw
        # them, graph.py did not parse them, and the chat UI's retrieval found 16 section
        # headers and no lessons at all. A lesson nothing can parse is a lesson nothing can
        # enforce, cite or recall -- it is writing with the appearance of recording.
        if lessons.is_file():
            canonical = set(re.findall(r"^\*\*(L-\d+)\s*\[", text, re.MULTILINE))
            # Anything that LOOKS like it is introducing a lesson: a heading, or a bold run
            # that forgot the bracketed status.
            claimed = re.findall(r"^(?:#{1,4}\s*|\*\*)(L-\d+)\b(.*)$", text, re.MULTILINE)
            malformed = []
            for lid, rest in claimed:
                if lid in canonical and rest.lstrip().startswith("["):
                    continue
                if lid not in canonical:
                    malformed.append(f"{lid} — `{(lid + rest).strip()[:70]}`")
            if malformed:
                self.result(
                    "FAIL", "Every lesson is written in the canonical form",
                    f"{len(malformed)} lesson(s) nothing can parse:\n"
                    + "\n".join(sorted(set(malformed)))
                    + "\n\nThe form is `**L-nnn [VERIFIED|OBSERVED|SUPERSEDED yyyy-mm-dd] "
                      "title**`.\nA lesson outside it is invisible to the ENFORCEMENT check, "
                      "to graph.py\nand to retrieval -- recorded in appearance only.",
                )
            else:
                self.result("PASS", "Every lesson is written in the canonical form "
                                    f"({len(canonical)} parsed)")

        # --- every test suite must expect at least one FAILURE ----------------------
        # docs/CHECKS.md rule 1: a check is not trusted until it has been observed
        # failing. A suite whose every assertion expects success is indistinguishable
        # from a suite that cannot fail -- and six of the bugs found on 2026-08-17/18
        # were in checking code, not in the code being checked.
        #
        # Heuristic, deliberately: it looks for assertions expecting a failure verdict,
        # a non-zero exit, or a refusal. It cannot know intent, so it WARNS.
        tests_dir = self.repo / "scripts" / "tests"
        # Idioms that signal an assertion EXPECTING a failure. Broadened 2026-08-18 after
        # this check fired on test_auth.py -- the suite with the most refusal assertions in
        # the repository -- because it used rule_of() and AuthError, and the list only knew
        # about refused() and Denied. A keyword heuristic is only as good as its keywords,
        # so it warns rather than failing, and the naming convention was made consistent
        # in the same commit.
        #
        # Broadened again 2026-08-27, and this time by the author of the suite it flagged --
        # which is worth saying out loud, because loosening a check until it stops
        # complaining about your own work is how a check dies. The test that justified it
        # asserts `r.returncode != 0` for a run that must refuse, and expects exit 3; that is
        # a failure-expecting assertion by any reading, and none of the old keywords could
        # see a bare returncode comparison. The added terms only match an assertion ABOUT a
        # non-zero exit, so they cannot admit a suite that merely calls subprocess and
        # ignores the result.
        # `", 1)"` USED TO BE IN THIS LIST AND IT CLEARED 16 OF 38 SUITES ON ITS OWN.
        # It was meant to catch `report("...", rc, 1)` -- an assertion that an exit code is
        # 1. As a substring it cannot tell that from `report("...", len(x), 1)`, which
        # asserts a COUNT is 1 and proves no failure at all, nor from `enumerate(x, 1)`.
        # A first fix stripped `enumerate|rsplit|split` before matching; it reached 0 of the
        # 16, because those were not what they matched on. The distinction is structural --
        # is the compared expression a RETURN CODE? -- and no string can answer it, which is
        # why `suite_proves_a_failure` parses instead. Same lesson as check_deployed.py's
        # docstring: three tasks spent patching a regex where `ast` was always the answer.
        NEGATIVE = ('"FAIL"', '"REFUSE"', "refused(", "Denied", "exit 1",
                    "Error)", "AuthError", "raises", "pytest.raises",
                    "returncode != 0", "returncode == 1", "returncode == 2",
                    "returncode == 3", "NOT WRITTEN")

        RC_NAMES = ("rc", "returncode", "exitcode", "exit_code", "status", "code")

        if tests_dir.is_dir():
            no_negative = []
            for f in sorted(tests_dir.glob("test_*.py")):
                body = f.read_text(encoding="utf-8", errors="replace")
                if not any(n in body for n in NEGATIVE) and not suite_proves_a_failure(body, str(f)):
                    no_negative.append(f.name)
            if no_negative:
                self.result(
                    "WARN", "Every test suite proves a failure, not only a pass",
                    "\n".join(no_negative)
                    + "\n\nNo assertion in these expects a refusal, a FAIL verdict or a "
                      "non-zero exit.\nA suite that has only ever passed cannot be "
                      "distinguished from one that\ncannot fail (docs/CHECKS.md, rule 1).",
                )
            else:
                n = len(list(tests_dir.glob("test_*.py")))
                self.result("PASS",
                            f"Every test suite proves a failure, not only a pass ({n} suites)")

        # --- no check script is orphaned from the protocol ---------------------------
        # scripts/chat/check-transcripts.py ran once on 2026-08-14 and was never wired
        # into anything. It worked; nothing called it. A check the repo appears to have
        # and never runs is worse than no check, because it reads as coverage.
        scripts_dir = self.repo / "scripts"

        def _read(path):
            """Missing is not an error here. A checker that CRASHES on an absent file is
            worse than one that answers wrongly -- it takes the whole run down and every
            check after it goes unreported."""
            try:
                return path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                return ""

        # WIDENED 2026-10-25 (t3578): the scan is orphan_scripts() now, because it also has to look
        # where the callers are (projects/), not only where the scripts are.
        orphans = orphan_scripts(self.repo)
        _, tool_problems = declared_tools(self.repo)
        if tool_problems:
            self.result(
                "FAIL", "Every declared tool is a real script, with its reason",
                "\n".join(tool_problems)
                + f"\n\n{SCRIPT_DISPOSITIONS} is what says a script is a tool rather than a check. "
                  "A declaration that cannot be trusted\nis an exemption list with better manners.",
            )
        if orphans:
            self.result(
                "WARN", "No check script is orphaned from the protocol",
                "\n".join(orphans)
                + "\n\nNothing in scripts/ (including the git hooks), docs/, AGENTS.md, projects/, "
                  "deploy/ or a shell runner mentions these, so nothing runs them.\nAn unreferenced "
                  "check reads as coverage the repository does not have.\nA script that is genuinely "
                  f"a TOOL rather than a check says so in {SCRIPT_DISPOSITIONS}.",
            )
        else:
            self.result("PASS", "No check script is orphaned from the protocol")

        # --- no hook reads $? straight after a pipeline ------------------------------
        # L-054: `python3 ... | tail -7` then `echo $?` reports TAIL's status, and tail
        # always succeeds -- so a failing verification printed "NOT INTACT" and exit 0.
        # WIDENED 2026-08-24 (t113). This was scoped to scripts/hooks because that is where
        # L-054 was found. The trap is not specific to hooks -- it is specific to pipelines.
        # A `docker pull ... | tail -2` reported exit 0 while failing and producing no image.
        # Scoping a check to the place a bug was found is how the same bug returns elsewhere.
        targets = []
        hooks = self.repo / "scripts" / "hooks"
        if hooks.is_dir():
            targets += [f for f in sorted(hooks.iterdir()) if f.is_file()]
        for pattern in ("scripts/*.sh", "scripts/**/*.sh", "projects/**/*.sh"):
            targets += [f for f in sorted(self.repo.glob(pattern)) if f.is_file()]

        masked = []
        for hook in targets:
            rel = hook.relative_to(self.repo)
            lines = hook.read_text(encoding="utf-8", errors="replace").split("\n")
            prev_piped = False
            for n, line in enumerate(lines, 1):
                s = line.strip()
                if s.startswith("#"):
                    continue
                if "$?" in s and prev_piped and "PIPESTATUS" not in s:
                    masked.append(f"{rel}:{n}  $? after a pipeline: {s[:60]}")
                prev_piped = ("|" in s and "||" not in s and not s.startswith("#"))
        if masked:
            self.result(
                "FAIL", "No tracked shell reads $? straight after a pipeline",
                "\n".join(masked[:5])
                + "\n\n$? reports the LAST command in a pipeline. Piping through tail or "
                  "grep\nturns every failure into a success (L-054). Use ${PIPESTATUS[0]} "
                  "or\ncapture the status without a pipe.",
            )
        else:
            self.result("PASS", "No tracked shell reads $? straight after a pipeline "
                                f"({len(targets)} file(s))")

        # --- no running service is older than the code it runs ------------------------
        # The same failure twice: workspace-mcp on 2026-08-25 (L-102) and app-feedback on
        # 2026-08-26. A fix on disk, a process from yesterday, and every file-reading check
        # in this repository reporting PASS. check_deployed.py asks the bridge's endpoint;
        # this covers every service, including those with no interface to ask.
        # Against self.live, NOT self.repo: the services were started from the main checkout
        # and a worktree's mtimes are all freshly-checked-out, which reads as every service
        # being stale. Asking the wrong tree gives a confident wrong answer.
        ss = self.live / "scripts" / "check_stale_services.py"
        if ss.is_file():
            r = subprocess.run([sys.executable, str(ss)], capture_output=True, text=True,
                               cwd=self.live, timeout=300)
            caveat = (f"\n\nNOTE: the main checkout could not be resolved ({self.live_note}), "
                      f"so this was asked of {self.repo} -- if that is a worktree, every "
                      f"mtime is freshly checked out and 'stale' here is not evidence."
                      if self.live_note else "")
            if r.returncode:
                self.result(
                    "FAIL", "No running service is older than the code it runs",
                    r.stdout.strip()
                    + "\n\nA merged change stays invisible until the service restarts."
                    + caveat,
                )
            else:
                n = r.stdout.count("[ok]")
                self.result("PASS", "No running service is older than the code it runs "
                                    f"({n} service(s))")

        # --- no watcher loop greps for a string in its own command line ---------------
        # L-105 / L-116. `pgrep` excludes its own pid and nothing else, so when the pattern
        # sits in the parent shell's command line the loop never exits. Written down, quoted
        # correctly, and then walked into four times on 2026-08-26 -- three ad-hoc watchers
        # that spun for over an hour, and one committed script that could never have done its
        # job. Knowing the rule was not enough; this is the grep for the shape.
        sm = self.repo / "scripts" / "selfmatch.py"
        if sm.is_file():
            watched = [str(f) for f in sorted(self.repo.glob("scripts/**/*.sh"))
                       + sorted(self.repo.glob("projects/**/*.sh"))
                       + sorted(self.repo.glob("scripts/**/*.py")) if f.is_file()]
            r = subprocess.run([sys.executable, str(sm), "--quiet", *watched],
                               capture_output=True, text=True, cwd=self.repo, timeout=300)
            if r.returncode:
                self.result(
                    "FAIL", "No watcher loop greps for a string in its own command line",
                    r.stdout.strip()[:900]
                    + "\n\nsuch a loop never exits (L-105, L-116). Check any command with:"
                      "\n  python3 scripts/selfmatch.py --cmd '<the command>'",
                )
            else:
                self.result("PASS", "No watcher loop greps for a string in its own "
                                    f"command line ({len(watched)} file(s))")

        # --- single-sourced facts have not drifted from their owner ------------------
        # L-046: two documents stated the same fact and disagreed for three days, each
        # internally consistent so every check passed. sync_facts.py makes a restatement
        # a generated copy; this makes a drifted copy unpushable.
        try:
            sys.path.insert(0, str(self.repo / "scripts"))
            import sync_facts
            problems, drifted = sync_facts.evaluate(self.repo)
        except Exception as exc:  # noqa: BLE001 - a broken tool must not read as a pass
            self.result("FAIL", "Single-sourced facts match their owner",
                        f"sync_facts.py could not run: {exc}")
        else:
            if problems:
                self.result("FAIL", "Single-sourced facts are structurally sound",
                            "\n".join(problems[:6]))
            elif drifted:
                self.result(
                    "FAIL", "Single-sourced facts match their owner",
                    "\n".join(f"{c} :: {f} (owner {o})" for c, f, o in drifted[:6])
                    + "\n\nA copy has been edited away from the document that owns it."
                      "\nRun: python3 scripts/sync_facts.py",
                )
            else:
                self.result("PASS", "Single-sourced facts match their owner")

        # --- the repository is on a local filesystem --------------------------------
        # L-040: on the Drive gvfs mount, `git status` did not return in 120 seconds and
        # the hooks could not carry an execute bit. This is the check that would have said
        # so in one line instead of costing a session to diagnose.
        try:
            fstype = subprocess.run(
                ["stat", "-f", "-c", "%T", str(self.repo)],
                capture_output=True, text=True,
                timeout=30,
            ).stdout.strip()
        except OSError:
            fstype = ""
        NETWORK_FS = {"fuseblk", "fuse", "fuse.gvfsd-fuse", "nfs", "cifs", "smb2", "9p"}
        if fstype in NETWORK_FS or "gvfs" in str(self.repo):
            self.result(
                "FAIL", "Repository is on a local filesystem",
                f"Filesystem type is '{fstype}' at {self.repo}.\n"
                "A network or FUSE mount cannot carry an execute bit, so hooks are skipped\n"
                "silently, and git stats thousands of objects over the wire. Measured on the\n"
                "Drive mount: git status did not return in 120s. Clone to local disk (L-040).",
            )
        elif not fstype:
            self.result("SKIP", "Repository is on a local filesystem",
                        "Could not determine the filesystem type.")
        else:
            self.result("PASS", f"Repository is on a local filesystem ({fstype})")

        # --- no hook prints a mangled escape ----------------------------------------
        # L-045: pre-commit printed "%USERPROFILE%\.secretsnthropic.env" for weeks, because
        # \a is BEL. Asserting that one string would fix one bug; asserting the class fixes
        # the next hook nobody has written yet.
        hooks_dir = self.repo / "scripts" / "hooks"
        offenders = []
        if hooks_dir.is_dir():
            for hook in sorted(hooks_dir.iterdir()):
                if not hook.is_file():
                    continue
                for n, line in enumerate(
                    hook.read_text(encoding="utf-8", errors="replace").split("\n"), 1
                ):
                    stripped = line.lstrip()
                    if stripped.startswith("#"):
                        continue
                    if not re.search(r"\b(echo|printf)\b", line):
                        continue
                    for esc in SHELL_EATEN_ESCAPES:
                        if esc in line:
                            offenders.append(f"{hook.name}:{n}  contains {esc!r}: {line.strip()[:70]}")
        if offenders:
            self.result(
                "FAIL", "Hook messages contain no mangled escapes",
                "\n".join(offenders[:6])
                + "\n\nThese are consumed by echo/printf, so the text a person reads is not "
                  "the\ntext in the source. L-045: this printed a path that has never existed,\n"
                  "in the one message someone reads while panicking about a leaked key.",
            )
        else:
            self.result("PASS", "Hook messages contain no mangled escapes")

    # ==============================================================================
    def check_enforcement_ratchet(self):
        """A lesson with no mechanical guard survives as long as somebody remembers it.

        Jas, 2026-09-03: *"all problems need a hard coded solution. Solutions must be able to
        function an infinite number of sessions away. Well after any of us have context of the
        initial incident."* This is that rule in its enforceable form -- a ceiling on the
        number of lessons carrying `ENFORCEMENT: none`, which may fall and never rise by
        accident. It also fails on an enforcement naming a file that no longer exists, or a
        test nothing invokes, both of which read as coverage the repository does not have.
        """
        self.section("Enforcement ratchet")
        script = self.repo / "scripts" / "check_enforcement.py"
        if not script.is_file():
            self.result("FAIL", "The enforcement ratchet exists",
                        "scripts/check_enforcement.py is missing. Without it, a lesson with "
                        "no guard is indistinguishable from one with a good guard.")
            return
        import subprocess as _sp
        try:
            r = _sp.run([sys.executable, str(script)], capture_output=True, text=True,
                        timeout=180)
            body = (r.stdout + r.stderr).strip()
            head = next((l.strip() for l in r.stdout.splitlines() if "unenforced" in l), "")
            if r.returncode == 0:
                self.result("PASS", f"Every lesson's guard exists and is reachable ({head})")
            else:
                self.result("FAIL", "Every lesson's guard exists and is reachable",
                            body[-1400:])
        except Exception as e:                               # noqa: BLE001
            self.result("WARN", "The enforcement ratchet was checkable",
                        f"check_enforcement.py did not run: {e}\nNothing was checked -- "
                        f"this is not a pass.")

    def check_working_set(self):
        """t301: the declared production working set fits the box (scripts/check_working_set.py)."""
        import contextlib
        import io
        sys.path.insert(0, str(self.repo / "scripts"))
        import check_working_set as cws
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = cws.check(self.repo)
        detail = buf.getvalue().strip()
        serving = json.loads((self.repo / "projects/local-inference/models/SERVING.json").read_text(encoding="utf-8"))
        if rc == 0:
            self.result("PASS", "The production working set fits the box", detail)
        elif serving.get("production_lanes") == []:
            # 2026-10-05: lanes are GENERATED from the roles holding Building. Jas removing Building
            # from every role (Models tab) is a decision, not a disk/document mismatch -- say it, loudly.
            self.result("WARN", "The production working set fits the box",
                        "no role holds Building (Models tab), so SERVING.json has no production lanes "
                        "and NOTHING BUILDS -- nothing to fit. Assign Building to a role to resume builds.")
        else:
            self.result("FAIL", "The production working set fits the box",
                        detail + "\nTwo models co-resident is the standing order (Jas, 2026-09-17); three "
                        "thrashed on 2026-09-14 (t301).")

    VENVS = (".venv", "projects/app-repo/.venv")

    def _failed_user_units(self):
        """Names of user systemd units that are failed, or None when systemd cannot be asked."""
        try:
            p = subprocess.run(["systemctl", "--user", "list-units", "--state=failed", "--no-legend", "--plain"],
                               capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.TimeoutExpired):
            return None
        if p.returncode != 0:
            return None
        return [l.split()[0] for l in p.stdout.splitlines() if l.strip()]

    def check_runtime_drift(self):
        """The interpreters and services still match what the repo was built against (t655).

        2026-10-02: an Ubuntu update replaced python3 3.12 with 3.14 and REMOVED 3.12. Both venvs symlink
        /usr/bin/python3, so every installed package became invisible and the Chat bridge crash-looped for
        hours before anyone saw it. Nothing noticed the interpreter had moved. This does: a venv whose
        interpreter no longer reports the version recorded in its pyvenv.cfg is a FAIL that names the rebuild
        command, and a user unit in the failed state is a WARN that names it. Not a prevention -- a detector
        that reads at the next sweep instead of the next symptom."""
        name = "venv interpreters and user services match what the workspace was built against"
        problems, looked = [], 0
        for rel in self.VENVS:
            cfg, py = self.repo / rel / "pyvenv.cfg", self.repo / rel / "bin" / "python3"
            if not cfg.is_file():
                continue
            looked += 1
            want = next((l.split("=", 1)[1].strip() for l in cfg.read_text(encoding="utf-8").splitlines()
                         if l.startswith("version")), "")
            try:
                got = subprocess.run([str(py), "--version"], capture_output=True, text=True, timeout=30)
                have = (got.stdout or got.stderr).strip().split()[-1] if got.returncode == 0 else ""
            except (OSError, subprocess.TimeoutExpired):
                have = ""
            if not have:
                problems.append(f"{rel}: its interpreter does not run ({py}). Rebuild: python3 -m venv --clear "
                                f"{rel} && {rel}/bin/pip install -r {Path(rel).parent / 'requirements.lock.txt'}")
            elif ".".join(have.split(".")[:2]) != ".".join(want.split(".")[:2]):
                problems.append(f"{rel}: built for Python {want}, now runs {have} -- its packages are invisible. "
                                f"Rebuild: python3 -m venv --clear {rel} && {rel}/bin/pip install -r "
                                f"{Path(rel).parent / 'requirements.lock.txt'}")
        if problems:
            self.result("FAIL", name, "\n".join(problems))
            return
        failed = self._failed_user_units()
        if failed:
            # ONLY THE WORKSPACE'S OWN SERVICES (the Flow chart's `services`): a desktop unit like
            # gnome-software failing is not ours, and a WARN that is mostly noise gets ignored.
            try:
                ours = {sv.get("unit") for sv in json.loads((self.repo / "projects" / "local-inference" /
                                                              "models" / "FLOW.json").read_text(encoding="utf-8"))
                        .get("services", []) if isinstance(sv, dict)}
            except (OSError, ValueError) as e:
                print(f"  NOTE: FLOW.json services unreadable ({e}); every failed unit is shown")
                ours = None
            failed = [u for u in failed if ours is None or u in ours]
        if failed:
            self.result("WARN", name, "workspace service(s) in the failed state: " + ", ".join(failed) +
                        "  (systemctl --user status <unit>)")
        elif failed is None:
            self.result("SKIP", name, f"{looked} venv(s) match; systemd could not be asked about failed units -- "
                                      "SKIP, not PASS")
        elif not looked:
            self.result("SKIP", name, "no venv found to compare -- SKIP, not PASS")
        else:
            self.result("PASS", name, f"{looked} venv(s) run the Python they were built for; no failed user unit")

    def check_model_registry(self):
        """The tags on this box are the tags the repository says should be here.

        Sampling was resolved by matching a family name inside the tag string until
        2026-09-03, so `ollama cp` made tags with identical weights, no family name and no
        profile -- they ran on the backend defaults Qwen's documentation forbids. The registry
        and the digest lock replaced that inference with a declaration, and this is what stops
        the declaration from quietly going stale.

        Ollama does not need to be running: the lock is committed, so the profile half is
        checkable offline. What cannot be checked offline is said, not skipped silently.
        """
        self.section("Models")
        reg = self.repo / "projects" / "local-inference" / "models" / "REGISTRY.json"
        lock = self.repo / "projects" / "local-inference" / "models" / "tags.lock.json"
        if not reg.is_file() or not lock.is_file():
            self.result("FAIL", "The model registry and digest lock exist",
                        f"missing: {'REGISTRY.json ' if not reg.is_file() else ''}"
                        f"{'tags.lock.json' if not lock.is_file() else ''}\n"
                        "settings.digests() reads the lock; without it every model tag "
                        "resolves by name alone, which is the failure of 2026-09-03.")
            return
        try:
            import subprocess as _sp
            r = _sp.run([sys.executable, str(self.repo / "scripts" / "models.py"), "verify"],
                        capture_output=True, text=True, timeout=120)
            body = (r.stdout + r.stderr).strip()
            if r.returncode == 0:
                import json as _j
                n = len(_j.loads(lock.read_text()).get("tags", {}))
                self.result("PASS", "Every declared model is present, pinned and has a "
                                    f"sampling profile ({n} tag(s) locked)")
            else:
                # NO STATIC HINT. This used to append "Fix with: models.py ensure" to every
                # failure regardless of cause, and for the commonest one -- a tag on the box
                # that nothing declares -- `ensure` cannot help: do_ensure walks registry ->
                # box and has no way to adopt. So the only instruction offered pointed at the
                # one path the tool did not own, and it propagated: Jas's delegation note of
                # 2026-09-20 repeated it as fact. Q4 traced this on 2026-09-18 (F2). models.py
                # now names the right verb per problem in `body`, and two copies of guidance
                # is the L-046 shape this repository has fought before -- so this prints the
                # tool's own words and adds none of its own.
                self.result("FAIL", "Every declared model is present, pinned and has a "
                                    "sampling profile", body[-1200:])
        except Exception as e:                               # noqa: BLE001
            self.result("WARN", "The model registry was checkable",
                        f"models.py verify did not run: {e}\nNothing was checked -- this "
                        f"is not a pass.")

    def check_production_liveness(self):
        """Is production actually delivering -- and if not, does anything say why?

        2026-10-24: no production dispatch had been accepted since 11:23Z and no check asked.
        Eleven refusals, a paused device and ~20 parked chunks were all in the ledgers, and the
        only way to see them was to run `dispatch_loop.py --dry-run` and read it. The ledgers
        were never missing the data; nothing asserted over it. FAIL only when the machine is
        idle for a reason NOBODY chose -- a pause by a person, or work that is running, is a
        PASS that still prints who and why. UNKNOWN (no rows) is a WARN, never a pass.
        """
        self.section("Production liveness")
        v = liveness_verdict_from(self.live)
        name = "Production is delivering, or something names why it is not"
        if v["state"] == "UNKNOWN":
            self.result("WARN", name, v["line"])
        elif v["ok"]:
            self.result("PASS", name, v["line"])
        else:
            self.result("FAIL", name, v["line"] + "\n"
                        "  detail: python3 scripts/check_workspace.py --liveness")

    def check_flow_projection(self):
        """The chart is a projection of the role assignments (t1594).

        Jas: "if whatever the source of truth is is updated, the flow tab also reflects that. No process
        can exist without being reflected on the flow tab." Two directions, and the FK covers neither:
        a stage a role holds that the chart has no node for, and a projection file that has gone stale
        because nobody regenerated it.
        """
        self.section("The chart follows the assignments")
        proj = self.live / "projects" / "local-inference" / "models" / "FLOW-WORKERS.json"
        flow = self.live / "projects" / "local-inference" / "models" / "FLOW.json"
        if not flow.exists():
            self.result("SKIP", "The chart shows who works each stage", "no FLOW.json in this checkout")
            return
        sys.path.insert(0, str(self.live / "scripts"))
        try:
            import role_store as rs
        except Exception as e:                                          # noqa: BLE001
            self.result("WARN", "The chart shows who works each stage", f"role_store unreadable ({e})")
            return
        try:
            want = rs.flow_workers()
        except Exception as e:                                          # noqa: BLE001
            self.result("WARN", "The chart shows who works each stage", f"the store could not be read ({e})")
            return
        try:
            have = json.loads(proj.read_text(encoding="utf-8")).get("workers") or {}
        except (OSError, ValueError) as e:
            # ABSENCE IS NOT AGREEMENT. A missing or unreadable projection is the finding, not a
            # reason to compare against {} and report a difference somewhere less clear.
            self.result("FAIL", "The chart shows who works each stage",
                        f"FLOW-WORKERS.json could not be read ({type(e).__name__}: {e}) -- regenerate it:\n"
                        "  python3 -c \"import sys; sys.path.insert(0,'scripts'); import "
                        "role_store; role_store.write_projection()\"")
            return
        if have != want:
            self.result("FAIL", "The chart shows who works each stage",
                        "FLOW-WORKERS.json disagrees with ROLES.json -- regenerate it:\n"
                        "  python3 -c \"import sys; sys.path.insert(0,'scripts'); import "
                        "role_store; role_store.write_projection()\"\n"
                        f"  {len(set(want) ^ set(have))} stage(s) differ")
            return
        try:
            nodes = {t.get("id") for t in (json.loads(flow.read_text(encoding="utf-8")).get("tasks") or [])
                     if isinstance(t, dict)}
        except (OSError, ValueError) as e:
            self.result("WARN", "The chart shows who works each stage", f"FLOW.json unreadable ({e})")
            return
        off_chart = sorted(set(want) - nodes)
        if off_chart:
            self.result("FAIL", "The chart shows who works each stage",
                        f"{len(off_chart)} stage(s) a role holds with no node on the chart: "
                        f"{', '.join(off_chart[:8])}\n"
                        "  A process may not exist off the chart: give it a node or unsassign it (t1594).")
            return
        nobody = sorted(n for n in nodes if n not in want)
        self.result("PASS", "The chart shows who works each stage",
                    f"{len(want)} stage(s) mapped; {len(nodes)} node(s); {len(nobody)} node(s) with no "
                    f"role holding them")

    def check_ceiling_budget(self):
        """A new failing suite may enter the ceiling only if another leaves (t1889)."""
        self.section("The failure ceiling")
        current = _ceiling_names(self.live / "workspace" / "test-failures-ceiling.json")
        if current is None:
            self.result("FAIL", "A failing suite may enter the ceiling only by swapping one out",
                        "the ceiling list could not be read -- nothing was compared")
            return
        previous = _ceiling_at_rev(self.live, "HEAD")
        if previous is None:
            self.result("SKIP", "A failing suite may enter the ceiling only by swapping one out",
                        "no committed ceiling list to compare against (first commit?)")
            return
        b = ceiling_budget(previous, current)
        names = (f"{b['count_now']} declared" if b["verdict"] == "same"
                 else f"{b['count_now']} declared; added {b['added'] or '-'}; "
                      f"removed {b['removed'] or '-'}")
        if b["verdict"] == "grew":
            self.result("FAIL", "A failing suite may enter the ceiling only by swapping one out",
                        f"{len(b['added']) - len(b['removed'])} suite(s) entered without anything "
                        f"leaving: {', '.join(b['added'])}"
                        "\n  A new declaring needs a retirement. Fix a suite and remove it from the "
                        "list in the same commit, or do not add this one -- the ceiling is a budget, "
                        "not a queue (t1889).")
        else:
            self.result("PASS", "A failing suite may enter the ceiling only by swapping one out",
                        f"{names} ({b['verdict']})")

    def check_ceiling_count(self):
        """The ceiling may fall and must not rise quietly (t1589).

        457 of 914 suites are allowed to fail and nothing refused growth: library.py appends a
        red suite to the file by itself. The committed count is the ratchet -- a number that may
        only fall, or rise on purpose in a commit that says it did.
        """
        self.section("The failure ceiling")
        # BOTH FROM THE LIVE CHECKOUT, like every check about the machine rather than about
        # this tree: a worktree's ceiling list and its count are a snapshot, and comparing one
        # tree's list against another tree's ratchet would be a fact about neither.
        ceiling = self.live / "workspace" / "test-failures-ceiling.json"
        names = _ceiling_names(ceiling)
        if names is None:
            self.result("FAIL", "The test-failure ceiling may fall and must not rise",
                        f"{ceiling} is missing or has no allowed_to_fail list -- nothing was read")
            return
        ratchet = _ceiling_ratchet(self.live / "workspace" / "test-failures-ceiling-count.json")
        existing = {p.name for p in (self.live / "scripts" / "tests").glob("test_*.py")}
        r = ceiling_report(names, ratchet=ratchet, existing=existing)
        top = ", ".join(f"{g} {n}" for g, n in r["groups"][:4])
        detail = (f"{r['count']} suite(s) declared: {top}"
                  + ("" if not r["absent"] else f"; {len(r['absent'])} not on disk: "
                     + ", ".join(r["absent"][:3])))
        if ratchet is None:
            self.result("FAIL", "The test-failure ceiling may fall and must not rise",
                        detail + "\n  workspace/test-failures-ceiling-count.json is missing, so "
                                 "there is no number growth can be measured against")
        elif r["over"]:
            self.result("FAIL", "The test-failure ceiling may fall and must not rise",
                        detail + f"\n  it has GROWN {r['over']} past the committed {ratchet}. "
                                 "Fix the suite, or raise the number on purpose: edit "
                                 "workspace/test-failures-ceiling-count.json in a commit whose "
                                 "subject says the ceiling rose and why.")
        else:
            self.result("PASS", "The test-failure ceiling may fall and must not rise",
                        detail + f"; ratchet {ratchet}"
                        + (f" (it can be lowered by {r['headroom']}: "
                           "python3 scripts/check_workspace.py --ratchet-ceiling)"
                           if r["headroom"] else " (at the ratchet)"))

    def check_delivery_faults(self):
        """No build is accepted without a branch carrying it, beyond the debt already on record.

        Like the failure ceiling, this is a RATCHET: workspace/delivery-faults-baseline.json is the
        measured set, it may only come down, and a task that is not in it going red is the point.
        A permanent red would be a check nobody reads; a silent one is what let 32 tasks sit.
        """
        self.section("The delivery faults")
        live = delivery_faults(self.live)
        if live is None:
            self.result("FAIL", "No accepted build is missing its branch",
                        "workspace/agent-runs.jsonl could not be read -- nothing was compared")
            return
        base_file = self.live / "workspace" / "delivery-faults-baseline.json"
        try:
            known = set(json.loads(base_file.read_text(encoding="utf-8"))["known"])
        except (OSError, ValueError, KeyError):
            self.result("FAIL", "No accepted build is missing its branch",
                        f"{base_file.name} is missing or has no known list -- a baseline that cannot "
                        f"be read would make every fault look new, and an absent one is not zero")
            return
        new = [t for t, _ in live if t not in known]
        gone = sorted(known - {t for t, _ in live})
        if new:
            self.result("FAIL", "No accepted build is missing its branch",
                        f"{len(new)} task(s) had a build ACCEPTED that no branch carried, and none of "
                        f"them is on the known list:\n  " + ", ".join(new[:12])
                        + f"\n\n{len(live)} live in total. Each is work that exists and nobody can "
                          f"review.\nCarry it with a branch, or record it in "
                          f"workspace/delivery-faults-baseline.json with why -- and that list may only "
                          f"come down." + (f"\nAnd {len(gone)} known one(s) are no longer live: "
                                           + ", ".join(gone[:8]) + " -- remove them." if gone else ""))
        else:
            self.result("PASS", "No accepted build is missing its branch",
                        f"{len(live)} live, all of them on the known list"
                        + (f"; {len(gone)} known one(s) recovered and should come off it: "
                           + ", ".join(gone[:6]) if gone else ""))

    def check_dispatch_lane_contradiction(self):
        """Work skipped for want of a lane, in a tick where nothing holds one -- see lane_contradiction."""
        self.section("The dispatcher's own account of itself")
        try:
            import jsonl_cache
            tick = jsonl_cache.latest_tick(self.live / "workspace" / "dispatch-loop.jsonl")
        except Exception as e:                                       # noqa: BLE001
            self.result("FAIL", "A tick does not blame a lane shortage it contradicts",
                        f"the dispatcher's tick could not be read ({type(e).__name__}: {e}) -- "
                        f"nothing was compared")
            return
        bad = lane_contradiction(tick)
        if not bad:
            self.result("PASS", "A tick does not blame a lane shortage it contradicts",
                        f"tick {tick.get('at')}: {len(tick.get('skipped') or [])} skipped, "
                        f"{_running_count(tick)} running")
            return
        lane_skips, running = bad
        self.result("FAIL", "A tick does not blame a lane shortage it contradicts",
                    f"tick {tick.get('at')} skipped {lane_skips} chunk(s) as 'waiting -- every model "
                    f"has a lane' while NOTHING was running ({running}). No job held a lane, so the "
                    f"reason is false: plan() spends one `free` list on both the review pass and "
                    f"production, and reviews drained it. This is the shape that built nothing for an "
                    f"hour on 2026-10-28 while the Hub showed '154 ready to build'. RCA: "
                    f"workspace/rca/rca-lane-starvation-2026-10-28.md")

    def check_every_kind_has_a_role(self):
        """A kind of work no role holds can never be picked (t9540 R4)."""
        self.section("Every kind of work has somewhere to go")
        try:
            import role_store as rs
            import task_routing as T
            import jsonl_cache
            d = T.load()
            tick = jsonl_cache.latest_tick_file(self.live / "workspace" / "dispatch-loop.jsonl")
            missing = unheld_kinds(d, tick)
        except Exception as e:                                        # noqa: BLE001
            self.result("FAIL", "Every kind of work is held by some role",
                        f"the role store could not be read ({type(e).__name__}: {e}) -- nothing compared")
            return
        if missing:
            self.result("FAIL", "Every kind of work is held by some role",
                        "these kinds have WORK WAITING and name a task no role lists on the Models tab, so "
                        "those items queue for ever with no owner: " + ", ".join(missing) +
                        "\n  Give one role the task on the Models tab (Responsibilities).")
        else:
            self.result("PASS", "Every kind of work is held by some role",
                        f"{len(set(T.KIND_TASK.values()))} kind(s), all held")

    def check_responsibility_starvation(self):
        """A responsibility that waited for a lane while the box started other work (t9540 R4).

        ONE TICK, NOT A HISTORY, and that is the record's fault rather than this check's: since 2026-10-05 the
        dispatcher writes the skipped list only to run-logs/dispatch-tick-latest.json (rewritten each tick),
        so "how long has this been starving?" cannot be answered from the ledger at all. Within one tick the
        shape is still unmistakable: chunks waiting for a lane WHILE other work started. That is 2026-10-28 --
        168 chunks "waiting for a free lane on Manager (Building)" and jobs starting beside them.
        """
        self.section("No responsibility starves while other work runs")
        try:
            import jsonl_cache
            tick = jsonl_cache.latest_tick_file(self.live / "workspace" / "dispatch-loop.jsonl")
        except Exception as e:                                        # noqa: BLE001
            self.result("FAIL", "No responsibility starves while other work runs",
                        f"the newest tick could not be read ({type(e).__name__}: {e})")
            return
        if not tick:
            self.result("WARN", "No responsibility starves while other work runs",
                        "no tick file to judge (run-logs/dispatch-tick-latest.json is absent)")
            return
        waiting = tick_waiting(tick)
        started = tick.get("started")
        started = len(started) if isinstance(started, (list, tuple)) else (started or 0)
        if waiting and started:
            reason, n = waiting[0]
            self.result("FAIL", "No responsibility starves while other work runs",
                        f"tick {tick.get('at')}: {n} chunk(s) waited for a lane ({reason}) while {started} "
                        f"job(s) started. The box was working, so this is not a lane shortage -- a "
                        f"responsibility is starved behind other work. RCA: "
                        f"workspace/rca/rca-lane-starvation-2026-10-28.md")
        else:
            self.result("PASS", "No responsibility starves while other work runs",
                        f"tick {tick.get('at')}: {sum(n for _w, n in waiting)} waiting, {started} started")

    def check_tmp_repo_copies(self):
        """Tmpfs RAM held by abandoned repo copies is a finding, not a mystery (2026-10-29).

        MEASURED: /tmp on this box is tmpfs (61 GB) and nine copies of this repository -- 1.6 GB each, 15 GB --
        sat in it, made the same afternoon, while MemAvailable was 20.9 GB against a build admission of 12 + 4
        and the pressure gate held every build. The copies come from clone tests killed before their `finally`.
        A reaper exists (scripts/sweep.py: sweep_tmp_repo_copies) and is now called from the hourly sweep, but a
        fix that needs remembering is not a fix: this makes the accumulation VISIBLE where the box is checked.
        """
        import subprocess as _sp
        self.section("Tmpfs is not a scratch disk")
        try:
            root = Path("/tmp")
            mounts = Path("/proc/mounts").read_text(encoding="utf-8", errors="replace")
        except OSError as e:                                          # noqa: BLE001
            self.result("WARN", "The temp dir is not holding repository copies", f"unreadable ({e})")
            return
        fstype = ""
        for line in mounts.splitlines():
            parts = line.split()
            if len(parts) > 2 and parts[1] == "/tmp":
                fstype = parts[2]
        if fstype != "tmpfs":
            self.result("PASS", "The temp dir is not holding repository copies",
                        f"/tmp is {fstype or 'unknown'}, not RAM")
            return
        copies, total = [], 0
        seen_inodes = set()          # ACROSS copies, not within them: hardlinks are shared between clones too
        try:
            import sweep
        except Exception as e:                                        # noqa: BLE001
            self.result("WARN", "The temp dir is not holding repository copies",
                        f"the classifier is unreadable ({type(e).__name__}: {e}) -- not counted")
            return
        try:
            for d in sorted(root.iterdir()):
                if not d.is_dir() or d.is_symlink() or d.name.startswith("claude-"):
                    continue
                if sweep.is_repo_copy(d):
                    _copies_probe = True
                    # ALLOCATED BLOCKS, DEDUPED BY INODE (2026-10-29): these clones are made with HARDLINKS, so
                    # summing st_size counts the same pages once per link -- the first version of this check
                    # reported 0.3 GB for a directory du called 14 MB, a 20x overstatement. st_blocks*512 is what
                    # the filesystem actually allocated, and the inode set stops one copy being counted twice.
                    size = 0
                    for f in d.rglob("*"):
                        try:
                            st = f.lstat()
                        except OSError as e:
                            # SAID, NOT SWALLOWED: an unreadable entry means this copy's size is a lower bound,
                            # and a silent skip would report a confident number that is missing files.
                            print(f"  NOTE: could not stat a file in {d}: {type(e).__name__}: {e}",
                                  file=sys.stderr)
                            continue
                        if not f.is_file() or (st.st_dev, st.st_ino) in seen_inodes:
                            continue
                        seen_inodes.add((st.st_dev, st.st_ino))
                        size += (st.st_blocks or 0) * 512
                    copies.append((str(d), size))
                    total += size
        except OSError as e:                                          # noqa: BLE001
            self.result("WARN", "The temp dir is not holding repository copies", f"scan failed ({e})")
            return
        gb = total / 2 ** 30
        # ONLY THE COPIES THE REMEDY CAN TAKE ARE A FAILURE (2026-10-30). The reaper skips anything younger than
        # its threshold, so failing on a five-minute-old clone while telling the reader to run it left a red main
        # nobody could clear without deleting files by hand. Young copies warn; old ones fail.
        stale, recent = sweep.classify_tmp_copies([(p, s_, _mtime_for(Path(p))) for p, s_ in copies],
                                                  reap_after_s=3600)
        if recent:
            self.result("WARN", "The temp dir is not holding ABANDONED repository copies",
                        f"{len(recent)} cop(y/ies) in /tmp are younger than the reaper's 3600 s threshold and will "
                        f"go on its next pass: " + ", ".join(Path(p).name for p, _ in recent[:4]))
        if stale:
            self.result("FAIL", "The temp dir is not holding repository copies",
                        f"{len(copies)} repository cop(y/ies) in RAM-backed /tmp, {gb:.1f} GB: "
                        + ", ".join(f"{Path(p).name} {s / 2**30:.1f} GB" for p, s in copies[:4])
                        + f".\n  Each is a clone test killed before its cleanup. Reap with: "
                          f"python3 -c \"import sys; sys.path.insert(0,'scripts'); import sweep; "
                          f"sweep.sweep_tmp_repo_copies('/tmp', max_age_s=3600)\"")
        else:
            self.result("PASS", "The temp dir is not holding repository copies",
                        f"/tmp is tmpfs and holds no repository copies ({gb:.1f} GB)")

    def check_ceiling_honest(self):
        """Every suite allowed to fail is actually failing (t7519).

        The count check above says the list may not GROW. This says the list may not LIE. They are
        different failures and only the first was ever checked: on 2026-10-26, 102 of the 456 had a
        latest verdict of 0, and "354 suites are failing" had been quoted for two sessions while the
        committed number was 456. A green suite on the list warns about nothing, so nothing noticed.

        The repair differs by case, which is why the message names them separately: a green suite
        comes OFF the list, and an unrecorded one gets RUN before anyone decides about it.
        """
        self.section("The failure ceiling")
        ceiling = self.live / "workspace" / "test-failures-ceiling.json"
        names = _ceiling_names(ceiling)
        if names is None:
            self.result("FAIL", "Every suite allowed to fail is actually failing",
                        f"{ceiling} is missing or has no allowed_to_fail list -- nothing was read")
            return
        verdicts = latest_verdicts(self.live / "workspace" / "suite-state.jsonl")
        if not verdicts:
            # AN EMPTY LEDGER MUST NOT READ AS "ALL CLEAN" -- that is the vacuous pass. It means the
            # verdicts could not be read, not that the ceiling is honest.
            self.result("FAIL", "Every suite allowed to fail is actually failing",
                        "workspace/suite-state.jsonl gave no verdicts at all -- nothing was compared")
            return
        h = ceiling_honesty(names, verdicts)
        if h["green"] or h["unrecorded"]:
            body = []
            if h["green"]:
                body.append(f"{len(h['green'])} of {len(names)} PASS now, so the list overstates the "
                            "debt -- remove them from allowed_to_fail and lower "
                            "workspace/test-failures-ceiling-count.json in the same commit:\n"
                            + "\n".join(f"  {n}" for n in h["green"][:40])
                            + ("" if len(h["green"]) <= 40 else
                               f"\n  ... and {len(h['green']) - 40} more"))
            if h["unrecorded"]:
                body.append(f"{len(h['unrecorded'])} have no recorded verdict, so nothing proves they "
                            "fail -- run them before deciding:\n"
                            + "\n".join(f"  {n}" for n in h["unrecorded"][:40]))
            self.result("FAIL", "Every suite allowed to fail is actually failing", "\n".join(body))
        else:
            self.result("PASS", "Every suite allowed to fail is actually failing",
                        f"{h['red']} suite(s) declared, every one with a failing verdict on record")

    def check_network_map(self):
        """The network map exists, and every entry points at a source that resolves (t7891).

        The Flow chart says what the system DOES; this says what it is made of and how each part is
        reached. Absence is a FAIL rather than a skip: on 2026-10-26 there was no map at all, and a
        check that treats "no map" as fine is the vacuous pass this repository keeps re-learning.
        """
        self.section("The network map")
        path = self.repo / "workspace" / "network.json"
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            self.result("FAIL", "The network map points at sources of truth",
                        "workspace/network.json does not exist -- the devices, the links between them "
                        "and the services around them (including Cloudflare) are recorded nowhere")
            return
        except (OSError, ValueError) as e:
            self.result("FAIL", "The network map points at sources of truth",
                        f"workspace/network.json could not be read ({type(e).__name__}: {e}) -- "
                        "nothing was checked")
            return
        probs = network_map_problems(doc, self.repo)
        if probs:
            self.result("FAIL", "The network map points at sources of truth",
                        "\n".join(probs[:25])
                        + ("\n  ... and %d more" % (len(probs) - 25) if len(probs) > 25 else "")
                        + "\n  An entry POINTS AT its source (a file, or a command) or IS the source. "
                          "A hole is recorded as state: unknown with a source -- never guessed.")
            return
        ents = doc.get("entities") or []
        unknown = sum(1 for e in ents if isinstance(e, dict) and e.get("state") == "unknown")
        self.result("PASS", "The network map points at sources of truth",
                    f"{len(ents)} entit(ies), every source resolves; {unknown} declared unknown")

    def check_network_covered(self):
        """Reality -> store: everything the machine exposes is on the network map (N4).

        The map is what someone chose to write down; this asks the machine. A new device or a new
        listening port must not be invisible until a person notices -- the same failure the Flow chart
        had before flow_coverage. Sources that cannot be read are REPORTED rather than skipped.
        """
        self.section("The network, as observed")
        path = self.live / "workspace" / "network.json"
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            self.result("FAIL", "Everything the network exposes is on the map",
                        f"workspace/network.json could not be read ({type(e).__name__}: {e}) -- "
                        "nothing could be compared")
            return
        observed, unobserved = observe_network()
        probs = network_undeclared(doc, observed, unobserved)
        if probs:
            self.result("FAIL", "Everything the network exposes is on the map",
                        "\n".join(probs[:20])
                        + ("\n  ... and %d more" % (len(probs) - 20) if len(probs) > 20 else ""))
            return
        self.result("PASS", "Everything the network exposes is on the map",
                    f"{len(observed['tailnet'])} tailnet peer(s), "
                    f"{len(observed['port'])} listening port(s) below {EPHEMERAL_PORT}, "
                    f"all accounted for")

    def check_network_proven(self):
        """Store -> reality: a service that claims up answers, or says it cannot (N5).

        The pair to check_network_covered: that one asks whether reality is on the map, this asks whether
        the map is still true. A claim of `up` with no probe is refused, because an unchecked assertion
        in a store everyone reads is worse than an admitted hole.
        """
        self.section("The network, as claimed")
        path = self.live / "workspace" / "network.json"
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            self.result("FAIL", "Every service the map calls up answers",
                        f"workspace/network.json could not be read ({type(e).__name__}: {e}) -- "
                        "nothing could be probed")
            return
        probs = network_unproven(doc)
        if probs:
            self.result("FAIL", "Every service the map calls up answers",
                        "\n".join(probs[:20])
                        + ("\n  ... and %d more" % (len(probs) - 20) if len(probs) > 20 else ""))
            return
        services = [e for e in doc.get("entities") or []
                    if isinstance(e, dict) and e.get("kind") == "service"]
        probed = [e for e in services if isinstance(e.get("probe"), dict)]
        self.result("PASS", "Every service the map calls up answers",
                    f"{len(services)} service(s), {len(probed)} with a probe, the rest declared "
                    f"unknown or down")

    def check_flow_changelog(self):
        """The Flow changelog can be replayed to the present chart (C2).

        C1 refuses a chart change made outside flow_change.py from today. This is the history: six rows do
        not chain, each is declared with what diverged, and a seventh would be a finding rather than
        joining the six.
        """
        self.section("The chart's changelog")
        log = self.repo / "projects" / "local-inference" / "models" / "FLOW-CHANGELOG.jsonl"
        probs = changelog_problems(log, self.repo / "workspace" / "flow-changelog-declared.json")
        if probs:
            self.result("FAIL", "The chart's changelog replays to the chart", "\n".join(probs[:12]))
            return
        rows = [json.loads(x) for x in log.read_text(encoding="utf-8").splitlines() if x.strip()]
        self.result("PASS", "The chart's changelog replays to the chart",
                    f"{len(rows)} row(s), {len(changelog_breaks(rows))} declared break(s)")

    def check_ledger_retention(self):
        """Every big ledger is declared, and every declared one is inside its bound (t4387)."""
        name = "Every ledger is inside its retention bound"
        probs = ledger_retention_findings(self.repo)
        if probs:
            self.result("FAIL", name, "\n".join(probs)
                        + "\n\nDeclare the ledger in workspace/ledger-retention.json with its class, "
                          "its bound and the command that prunes it -- or prune it. A ledger with no "
                          "bound grows until somebody notices: slot-ledger.jsonl went from 2.71 GB to "
                          "3.91 GB in a week and nothing said anything.")
        else:
            self.result("PASS", name, ledger_retention_note(self.repo))

    def check_guard_tiers(self):
        """The memory guard's shed order is declared data, and the data is trustworthy (t1597)."""
        name = "The memory guard's shed order is declared data and matches the disk"
        try:
            sys.path.insert(0, str(self.repo / "scripts"))
            import memory_guard as mg
            probs = mg.validate_tiers()
            source, problem = mg.TIERS_SOURCE, mg.TIERS_PROBLEM
        except Exception as e:                                  # noqa: BLE001 -- reported, not skipped
            self.result("WARN", name, f"not checked ({type(e).__name__}: {e})")
            return
        if probs:
            self.result("FAIL", name, "\n".join(probs)
                        + "\n\nworkspace/guard-tiers.json is the guard's shed order. Fix it: while it "
                          "is wrong the guard runs its built-in order, which is not the declared one.")
        elif source != "file":
            self.result("FAIL", name, f"the guard is NOT using the declared order ({problem}) -- every "
                                      f"tier it sheds is the built-in fallback")
        else:
            self.result("PASS", name, f"{len(mg.TIERS) + 1} tier(s), the model last, reserve "
                                      f"{mg.RESERVE_GB:g} GB, order read from workspace/guard-tiers.json")

    def check_dsh_trust(self):
        """The DSH web launcher must trust every authority its profile says it trusts."""
        self.section("The DSH web profile")
        profile = Path.home() / ".dsh" / "profiles" / "web" / "cordis.patch.yml"
        hosts = _profile_trusted_hosts(profile)
        if hosts is None:
            self.result("SKIP", "The DSH web launcher trusts what its profile trusts",
                        f"{profile} not present -- no DSH profile on this box")
            return
        try:
            r = subprocess.run(["systemctl", "--user", "show", "dsh-web.service", "-p", "ExecStart"],
                               capture_output=True, text=True, timeout=30)
            exec_start = r.stdout or ""
        except (OSError, subprocess.SubprocessError) as e:
            self.result("WARN", "The DSH web launcher trusts what its profile trusts",
                        f"the unit could not be read ({type(e).__name__}: {e}) -- nothing compared")
            return
        if "ExecStart=" not in exec_start or "dsh web" not in exec_start:
            self.result("SKIP", "The DSH web launcher trusts what its profile trusts",
                        "no dsh-web.service on this box")
            return
        launcher = _launcher_trusted_hosts(exec_start)
        gap = trust_gap(hosts, launcher)
        if gap:
            self.result("FAIL", "The DSH web launcher trusts what its profile trusts",
                        "the profile trusts authorities the launcher does not pass: "
                        + ", ".join(gap)
                        + "\n  the DSH fences /api on the Host header, so a browser using one of "
                          "those gets 403 'forbidden' and the page sits on 'reconnecting'. Add "
                          "--trusted-host for each to the unit (or a drop-in).")
        else:
            self.result("PASS", "The DSH web launcher trusts what its profile trusts",
                        f"profile {', '.join(hosts) or '(none)'}; launcher "
                        f"{', '.join(launcher) or '(none)'}")

    def check_run_outcomes(self):
        """Every value in agent-runs.jsonl's `outcome` belongs to a declared vocabulary.

        The column carries two vocabularies (t1587): a coder VERDICT (ACCEPTED, TESTS-FAILED,
        REFUSED-TO-START...) and the console's TRANSPORT reason (stop, length, timeout...).
        Measured 2026-10-24: 844 verdict rows and 5,603 transport rows, so 70% of the column
        means "the model finished" -- a success rate computed from it as written is meaningless.
        History is not rewritten and nothing is coerced; a value in NEITHER list is the finding,
        because that is a new outcome nobody has said what it means.
        """
        self.section("Run ledger")
        sys.path.insert(0, str(self.repo / "scripts"))
        import reviewq
        rows = _liveness_jsonl(self.live / "workspace" / "agent-runs.jsonl")
        counts = Counter()
        for r in rows:
            cls = reviewq.outcome_class(r)
            counts[(cls, (r.get("outcome") if r.get("outcome") else "(none)"))] += 1
        pipeline = sum(n for (c, _), n in counts.items() if c == "pipeline")
        transport = sum(n for (c, _), n in counts.items() if c == "transport")
        unknown = sorted((v, n) for (c, v), n in counts.items() if c == "unclassified")
        unrecorded = sum(n for (c, _), n in counts.items() if c == "unrecorded")
        if unknown:
            self.result("FAIL", "Every run outcome is classified",
                        "value(s) in neither the verdict nor the transport vocabulary:\n"
                        + "\n".join(f"  {n:5d}x {v}" for v, n in unknown)
                        + "\nAdd each to reviewq.PIPELINE_OUTCOMES or reviewq.TRANSPORT_OUTCOMES "
                          "with what it means, or fix the writer that emitted it.")
        elif unrecorded:
            self.result("WARN", "Every run outcome is classified",
                        f"{unrecorded} row(s) carry NO outcome at all (a writer emitted a run "
                        f"without a verdict); {pipeline} verdict(s) and {transport} transport "
                        "reason(s) classified")
        elif not rows:
            self.result("WARN", "Every run outcome is classified",
                        "no rows in workspace/agent-runs.jsonl -- nothing was examined")
        else:
            self.result("PASS", "Every run outcome is classified",
                        f"{len(rows)} row(s): {pipeline} verdict(s) over "
                        f"{len(reviewq.PIPELINE_OUTCOMES)} declared, {transport} transport "
                        f"reason(s) over {len(reviewq.TRANSPORT_OUTCOMES)} declared")

    def check_doc_references(self, files):
        self.section("Documentation references")

        # Catches a document pointing at a file that does not exist anywhere -- the cheap
        # version of "does this doc still describe reality". Matches only backticked paths
        # with a known extension, and resolves by basename so prose mentions still count.
        basenames = {f.name for f in files}
        dangling = set()

        for rel in self.git_lines("ls-files", "*.md"):
            # Session logs describe the past on purpose -- they name files deleted in the
            # very commit they document. Holding them to present-tense reality is wrong.
            # AND A GENERATED REPORT OF QUOTED MODEL OUTPUT (2026-10-02): workspace/truncation-review.md
            # is rewritten by the script reviewer and quotes the code it read (`index.json`,
            # `identity-map.json`, `test_foo.py`) -- each one a new FAIL and a hand declaration.
            if rel.startswith("workspace/sessions/") or rel == "workspace/truncation-review.md":
                continue
            # The manager's answers are the same kind of record: raw model output PROPOSING
            # files (a new test, a task t999 in an oracle's fixture) that do not exist yet
            # by design. 153 such references on 2026-09-25, the first time they were
            # committed. What gets built from them is checked where it lands.
            # RCA reports join them (2026-10-07): since Swift took the rca task, each report proposes
            # its fix by naming the check or test to build -- 26 such names in the first afternoon.
            if rel.startswith(("workspace/manager-answers/", "workspace/run-logs/manager-queue/",
                               "workspace/rca/")):
                continue
            full = self.repo / rel
            if not full.is_file():
                continue
            text = full.read_text(encoding="utf-8", errors="replace")
            doc_dir = full.parent

            for mt in re.finditer(
                r"`([A-Za-z0-9_\-./]+\.(?:md|py|ps1|kt|kts|json|bat))`", text
            ):
                ref = mt.group(1)
                if ref in NOT_FILE_REFS:
                    continue
                if Path(ref).name in basenames:
                    continue

                # A file git is told to ignore is absent by design -- generated output, a
                # live cache. Absent-and-ignored is not absent-and-forgotten.
                ignored = False
                for cand in (self.repo / ref, doc_dir / ref):
                    try:
                        norm = cand.resolve()
                    except OSError as e:
                        print(f"  NOTE: {cand} unresolvable ({e}) -- not checked as ignored", file=sys.stderr)
                        continue
                    if not norm.is_relative_to(self.repo):     # outside the repo: git cannot ignore it
                        continue
                    if self.git("check-ignore", "-q", "--", str(norm))[0] == 0:
                        ignored = True
                        break
                if ignored:
                    continue

                # A SWEPT INBOX MESSAGE, declared once as a class (t143). A reply has to name the
                # message it answers, and the message is deleted once actioned because Chat cannot
                # delete its own -- so every such reply dangled and needed its own line here. It
                # is quiet only if git has the message (committed, then swept): nothing is lost.
                if ref.startswith("workspace/inbox/") and self.git(
                        "log", "--all", "-1", "--format=%h", "--", ref)[1]:
                    continue

                dangling.add(f"{rel} -> {ref}")

        # L-072 / t029. This used to WARN with twelve standing entries, every one expected --
        # so a THIRTEENTH, genuinely broken, would have arrived looking exactly like the
        # twelve nobody reads. A warning that is always present is not a warning.
        #
        # Absence is now DECLARED in docs/EXTERNAL-POINTERS.md. A declared pointer is quiet;
        # an undeclared one FAILS and can be said plainly instead of joining a pile.
        registry = self.repo / "docs" / "EXTERNAL-POINTERS.md"
        declared = registry.read_text(encoding="utf-8") if registry.is_file() else ""

        undeclared = sorted(d for d in dangling if d.split(" -> ", 1)[1] not in declared)
        accounted = len(dangling) - len(undeclared)

        if undeclared:
            self.result(
                "FAIL", "Every dangling reference is declared in docs/EXTERNAL-POINTERS.md",
                f"{len(undeclared)} reference(s) resolve to nothing and are NOT declared:\n"
                # ALL OF THEM, UP TO A LOUD BOUND (2026-10-24). This printed the first twelve
                # and said "38" -- so a reader could see neither twenty-six of the references
                # nor any pattern in them, and the number that mattered was a claim they had to
                # take on trust. past SIXTY the list is bounded again, but the line then says
                # exactly how many it withheld and how to see them (never-truncate-a-record).
                + "\n".join(undeclared[:60])
                + ("" if len(undeclared) <= 60 else
                   f"\n... and {len(undeclared) - 60} more; the full list is every `[FAIL]` line "
                   "of: python3 scripts/check_workspace.py --quick")
                + "\n\nEither fix the reference, or add it to docs/EXTERNAL-POINTERS.md with "
                  "the reason it is absent. Adding a line there is a claim that you looked.",
            )
        elif dangling:
            self.result("PASS", "Every dangling reference is declared "
                                f"({accounted} accounted for in EXTERNAL-POINTERS.md)")
        else:
            self.result("PASS", "Documented files all exist somewhere in the tree")

    # ==============================================================================
    def run(self):
        print()
        print(f"Workspace check -- {self.repo}")
        if self.quick:
            print(_c("90", "(quick mode: slow checks skipped)"))

        if not (self.repo / ".git").exists():
            print(_c("31", f"FATAL: no .git at {self.repo} -- is this the right folder?"))
            return 2

        files = self.workspace_files()

        # A CHECK THAT CRASHES IS ONE FAILURE, NOT THE END OF THE RUN (2026-09-30). check_lesson_ids
        # raised FileNotFoundError on a repository with no docs/LESSONS.md, and every check after it
        # silently never ran -- test_task_queue's fixtures got no queue verdict at all ("ABSENT", 6 red
        # since 09-03, "not diagnosed"). Each check now runs on its own; a crash is reported as a FAIL
        # naming the exception, never as a pass, and the rest still run.
        for check, args in ((self.check_git_state, ()), (self.check_secrets, ()),
                            (self.check_file_hazards, (files,)), (self.check_ledger_retention, ()),
                            (self.check_guard_tiers, ()),
                            (self.check_structure, ()),
                            (self.check_lesson_ids, ()), (self.check_tmp_inodes, ()), (self.check_disk_free, ()), (self.check_enforcement, ()),
                            (self.check_deferrals, ()), (self.check_deferral_numbers, ()), (self.check_assignee_literals, ()), (self.check_output_budgets, ()), (self.check_chat_bridge, ()),
                            (self.check_drifting_claims, ()), (self.check_task_queue, ()),
                            (self.check_task_closure_dod, ()),
                            (self.check_served_prose, ()), (self.check_review_gate, ()),
                            (self.check_warning_clean, ()), (self.check_headroom_waits, ()),
                            (self.check_production_failures, ()), (self.check_flow_record, ()), (self.check_skill_registry, ()), (self.check_enforcement_discipline, ()),
                            (self.check_enforcement_ratchet, ()), (self.check_model_registry, ()),
                            (self.check_working_set, ()), (self.check_manifest_ops, ()), (self.check_runtime_drift, ()),
                            (self.check_doc_references, (files,)),
                            (self.check_production_liveness, ()),
                            (self.check_run_outcomes, ()),
                            (self.check_ceiling_count, ()),
                            (self.check_ceiling_budget, ()),
                            (self.check_ceiling_honest, ()),
                            (self.check_dispatch_lane_contradiction, ()),
                            (self.check_tmp_repo_copies, ()),
                            (self.check_every_kind_has_a_role, ()),
                            (self.check_responsibility_starvation, ()),
                            (self.check_delivery_faults, ()),
                            (self.check_network_map, ()),
                            (self.check_network_covered, ()),
                            (self.check_network_proven, ()),
                            (self.check_flow_changelog, ()),
                            (self.check_flow_projection, ()),
                            (self.check_dsh_trust, ())):
            try:
                check(*args)
            except Exception as e:                                  # noqa: BLE001 -- reported as a FAIL
                self.result("FAIL", f"{check.__name__} ran to completion",
                            f"it crashed: {type(e).__name__}: {e} -- nothing it checks was verified")

        print()
        print(_c("90", "-" * 70))
        self._record_history()
        summary = f"{self.passes} passed, {self.warnings} warning(s), {self.failures} failed"
        if self.failures:
            print(_c("31", f"FAIL -- {summary}"))
            # NAMED IN THE LAST LINES (t390): the summary was a count, and the failure sat hundreds
            # of lines up -- every caller that tails this output (session_end) saw "1 failed" only.
            for n in getattr(self, "failed_names", []):
                print(_c("31", f"  FAILED: {n}"))
            print(_c("90", "A failure means a document and the disk disagree. Fix one of them."))
            return 1
        print(_c("32", f"OK -- {summary}"))
        return 0


# The oracle addresses the gate by name on the module (see main() in
# scripts/tests/test_task_closure_carries_dod.py): a check that exists only as a method reads,
# to the test, as a check never built. Aliased, not copied -- two copies of a check drift (L-046).
check_task_closure_dod = Checker.check_task_closure_dod


HISTORY = Path(__file__).resolve().parent.parent / "workspace" / "check-history.jsonl"


def _history_row(checker, quick):
    """One run of the workspace check, as a row a trend can be read from (t104)."""
    head = checker.git("rev-parse", "--short", "HEAD")[1]
    dirty = checker.git("status", "--porcelain")[1]
    return {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "quick": bool(quick),
            "head": head[0] if head else None, "dirty_files": len(dirty),
            "passed": checker.passes, "warnings": checker.warnings, "failed": checker.failures,
            "failing": getattr(checker, "failed_names", []), "warning": getattr(checker, "warned_names", [])}


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--quick", action="store_true",
                    help="Skip the slow checks (full-history secret scan).")
    ap.add_argument("--repo-root", default=None)
    ap.add_argument("--ratchet-ceiling", action="store_true",
                    help="Lower the test-failure ceiling count to today's list. It may not raise it.")
    ap.add_argument("--liveness", action="store_true",
                    help="Print only the production-liveness verdict, and exit 1 when STALLED.")
    ap.add_argument("--page-help-guard", action="store_true",
                    help="Read changed paths from stdin; exit 1 if a Hub page changed without its "
                         "how-this-page-works text. Used by the pre-commit hook (C3).")
    args = ap.parse_args()
    if args.page_help_guard:
        # A GATE, NOT A CHECK: this reads a commit's file list from stdin, because a tree cannot say
        # what was staged. One copy of the rule, called by scripts/hooks/pre-commit.
        probs = page_help_problems([ln.strip() for ln in sys.stdin.read().splitlines() if ln.strip()])
        for x in probs:
            print(f"  {x}")
        return 1 if probs else 0
    if args.ratchet_ceiling:
        root = Path(args.repo_root or Path(__file__).resolve().parent.parent)
        names = _ceiling_names(root / "workspace" / "test-failures-ceiling.json")
        if names is None:
            print("  the ceiling could not be read; nothing was ratcheted")
            return 2
        before = _ceiling_ratchet(root / "workspace" / "test-failures-ceiling-count.json")
        count = len(set(names))
        if before is not None and count > before:
            print(f"  REFUSED: the list is {count} and the ratchet is {before}; this flag may "
                  "not raise it. Fix a suite, or raise the number deliberately.")
            return 1
        path = root / "workspace" / "test-failures-ceiling-count.json"
        path.write_text(json.dumps({
            "count": count,
            "at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "why": "lowered to what the list holds: the ceiling may fall and must not rise (t1589)",
        }, indent=2) + "\n", encoding="utf-8")
        print(f"  ratcheted {before} -> {count}")
        return 0
    if args.liveness:
        root = Path(args.repo_root or Path(__file__).resolve().parent.parent)
        v = liveness_verdict_from(Checker(root).live)   # the machine, not this checkout
        print(f"  [{v['state']}] {v['line']}")
        return 0 if v["ok"] else 1
    root = args.repo_root or Path(__file__).resolve().parent.parent
    return Checker(root, quick=args.quick).run()


if __name__ == "__main__":
    sys.exit(main())
