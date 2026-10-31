#!/usr/bin/env python3
"""Find the failures nobody reported, by reading what the harness already wrote.

    python3 scripts/error_scan.py [SOURCE...]  findings
    python3 scripts/error_scan.py [SOURCE...] --all  every finding in the logs
    python3 scripts/error_scan.py [SOURCE...] --json  machine-readable, for check_workspace

WHY THIS EXISTS
---------------
Jas, 2026-09-10: *"I want to automatically catch all bugs and errors to analyze routinely.
It can't rely on you or Q16 to remember to catch it."*

He is right that memory is the wrong mechanism, and the evidence is a week of this project.
FIVE separate truncation failures happened between 2026-09-08 and 2026-09-10. Every one of
them left a MACHINE-READABLE TRACE in a file on disk at the moment it happened. Every one was
found hours later by a human noticing something odd:

    done_reason='tool-round-cap'   eight plan sections produced nothing, 220 tool calls
    done_reason='length'           a preflight review spent its budget thinking, returned ""
    exit 141                       a push reported success and had not pushed
    socket TimeoutError            a 50-minute document lost entirely
    "truncated -- I could not      Q16 given a prefix of a 41,000-char ledger, and it was
     read the full file"           the TAIL that held the rows it needed

A grep would have caught all five, the same day, for free. Nothing was grepping.

WHY SIGNATURES AND NOT A MODEL READING TRANSCRIPTS
--------------------------------------------------
A model reviewing prose was considered and is the wrong FIRST tool, for a reason this
repository already knows: `docs/CHECKS.md` rule 1 -- a check that has only ever passed cannot
be told apart from one that cannot fail. A signature scanner can be handed a known-bad fixture
and must go red; that is testable, and the self-test below does it. A model asked "did anything
go wrong in these logs" cannot easily be shown to be capable of saying yes, which makes it the
same bet as asking a human to remember -- cheaper, and no more accountable.

So: signatures first, for everything mechanical. A model belongs on the RESIDUE -- the
over-read class, where a true signal is turned into a false claim -- which Q16's repair plan
argues no fixture can reach. That is a later stage and it must never be the only detector.

WHERE IT RUNS, AND WHY NOT A DAEMON
-----------------------------------
On surfaces that already fire: `session_start.py` (a person reads its output every session)
and `pre-push`. NOT a daemon, and this is Q16's argument rather than a preference: "another
process that can itself die and that nobody owns... a daemon that lapsed three times, now with
a heartbeat file of its own, is the same defect one level up." The watchdog in this repository
lapsed three times for exactly that reason.

THE CONTRACT
------------
    scan(paths=None, since=None) -> list[Finding]

`Finding` is a dict with these keys and no others:

    kind        one of KINDS, or status-<the row's status> for rows the generic status rule catches
    source      the file the evidence came from, repo-relative
    locator     line number for a text log, or the row's own id/at for a ledger
    evidence    THE MATCHING TEXT, NOT A FIXED-WIDTH PREFIX OF IT (see below)
    at          the ISO timestamp on the row, or None if the source carries none

KINDS, and each one names a real incident this repository has already paid for:

    truncated-output    done_reason in ("length",) -- the model was cut off mid-answer
    tool-loop-exhausted done_reason == "tool-round-cap" -- it searched and never wrote
    unusable-parse      a coder attempt recorded UNPARSEABLE, which hides several causes
    silent-signal-death an exit code of 141, or any 128+N signal exit, reported as success
    read-truncated      a log or reply SAYS it could not read something in full
    empty-with-tokens   a reply that spent tokens and returned no content

RULES THE IMPLEMENTATION MUST FOLLOW
------------------------------------
1. NEVER TRUNCATE `evidence`. This script exists because of truncation; a scanner that stores
   `line[:200]` would be the tenth instance of the defect it hunts, inside the hunter. If a
   line is enormous, record it whole -- these are findings, and there are not many.
2. Reading a log must never change it, and a malformed line must not stop the scan. Count
   unreadable lines and report the count; a scanner that dies on row 4 of 900 has found
   nothing and says nothing.
3. A source that does not exist is not an error and not a pass. Report it as skipped, with
   the path, so "no findings" can be told apart from "nothing was looked at" (L-146).
4. No network, no model call, no subprocess. This runs inside session_start and must be fast
   and offline.

THE IMPLEMENTATION GOES BELOW -- see scripts/tests/test_error_scan.py for the contract that
judges it. Every assertion in that suite is a real incident from 2026-09-08 to 2026-09-10.
"""

KINDS = (
    "truncated-output",
    "tool-loop-exhausted",
    "unusable-parse",
    "silent-signal-death",
    "read-truncated",
    "empty-with-tokens",
    "backup-failed",
    "record-suspiciously-exact",
    "unhealthy-outcome",
    "transcript-failure",
)

# The logs that already exist and already hold every incident above. A path that is absent is
# reported as skipped rather than silently contributing zero findings.
DEFAULT_SOURCES = (
    "workspace/agent-runs.jsonl",
    "workspace/plan",
    "workspace/console",
    # Added 2026-09-11 because Jas asked whether a NAS failure would be caught, and it would
    # not have been: nas_backup.sh printed to stdout, which on a timer means the systemd
    # journal, which no file-based scanner reads. A backup failing every night for a week
    # would have been invisible to the thing built to catch exactly that.
    "workspace/backup-runs.jsonl",
    # Added 2026-09-11. explore_loop.py and ask_q16.py wrote their output here and NOTHING
    # read it: four planning dispatches, one of them a 42-minute timeout that produced zero
    # bytes, existed only as .log files outside every scanned source. Both now write rows to
    # agent-runs.jsonl as well, but the logs carry the detail a row cannot.
    "workspace/run-logs",
)

# A row's `status` is HEALTHY only when it is one of these. Everything else -- including a
# status nobody has invented yet -- is an unknown and surfaces. Enumerating the good is what
# makes the scanner survive shapes its author never saw (Jas, 2026-09-10: "if we use grep we
# are limited to what it searches for in its set library of terms").
# "collected", "built" and "finished" added 2026-09-11: successful collect-logs and
# night-queue ledger rows the first scan reported as backup-failed -- 24 of the 332 noise.
HEALTHY_STATUS = ("verified", "ok", "stop", "ACCEPTED", "collected", "built", "finished",
                  # 2026-09-30, when `outcome` joined the status family (t271): coder.py's attempt
                  # steps that are not failures -- the model asked to read a file, or a cut-off
                  # attempt was resumed from its own reasoning.
                  "ASKED", "RESUMED")

import json
import re
import sys
from pathlib import Path

_skipped = []

_SIGNAL_EXIT_RE = re.compile(r'EXIT[=: ]+(\d+)')

# Ollama's journal writes "truncated = 0" on every healthy stop -- 284 of the 332 first-scan
# findings were that WORD on a line where nothing was truncated. The value is the status:
# 0 is healthy, non-zero is the finding. The bare word still catches prose on lines that
# carry no value to read ("truncated -- I could not read the full file").
_TRUNCATED_VALUE_RE = re.compile(r'truncated\s*=\s*(\d+)', re.IGNORECASE)


def scan(paths=None, since=None):
    global _skipped
    _skipped = []

    if paths is None:
        paths = list(DEFAULT_SOURCES)

    findings = []

    for p in paths:
        p = Path(p)
        if not p.exists():
            _skipped.append(str(p))
            continue
        if p.is_dir():
            for f in sorted(p.rglob("*")):
                if f.is_file() and f.suffix in (".jsonl", ".log", ".md", ".json"):
                    findings.extend(_scan_file(f))
        else:
            findings.extend(_scan_file(p))

    return findings


def census(paths=None):
    """[{source, status, rows, unreadable_rows, findings}] per source -- a zero sits beside its volume.
    status: OK; UNREADABLE when a source has rows and the reader knows none of their keys (a dialect it
    cannot read is a finding about the reader, never 'no errors'); SKIPPED when it does not exist."""
    out = []
    for p in (list(DEFAULT_SOURCES) if paths is None else paths):
        p = Path(p)
        files = ([f for f in sorted(p.rglob("*")) if f.is_file() and f.suffix in (".jsonl", ".log", ".md", ".json")]
                 if p.is_dir() else [p])
        if not p.exists():
            out.append({"source": str(p), "status": "SKIPPED", "rows": 0, "unreadable_rows": 0, "findings": 0})
            continue
        for f in files:
            rows = unreadable = 0
            if f.suffix == ".jsonl":
                # STREAMED (t1599): the census read every row of a 2.71 GB ledger into a list of
                # lines to count two numbers. Same file, same counts, one line at a time.
                try:
                    handle = open(f, encoding="utf-8", errors="replace")
                except OSError:
                    out.append({"source": str(f), "status": "UNREADABLE", "rows": 0, "unreadable_rows": 0,
                                "findings": 0})
                    continue
                with handle as fh:
                    for line in fh:
                        if not line.strip():
                            continue
                        rows += 1
                        try:
                            r = json.loads(line)
                        except ValueError:
                            unreadable += 1
                            continue
                        if not (isinstance(r, dict) and KNOWN_KEYS & set(r)):
                            unreadable += 1
            else:
                try:
                    rows = sum(1 for _ in f.open(errors="replace"))
                except OSError:
                    rows = 0
            n = len(_scan_file(f))
            status = "UNREADABLE" if rows and unreadable == rows else "OK"
            out.append({"source": str(f), "status": status, "rows": rows, "unreadable_rows": unreadable,
                        "findings": n})
    return out


def skipped():
    return list(_skipped)


# A LINE IS NOT A UNIT OF MEMORY YOU CAN TRUST (t1599, 2026-10-24). `manager-commands/` saves a
# command's output whole, and one of them is 33 lines with a **255 MB** line in it. Reading that
# "one line at a time" still allocates a quarter of a gigabyte, then a copy to strip the newline.
# So the scanner reads in fixed windows and hands long lines on in pieces: memory is bounded by
# SCAN_CHUNK, and nothing is skipped -- a long line is SCANNED in windows, not truncated away.
SCAN_CHUNK = 65536


def _bounded_lines(path, chunk=SCAN_CHUNK):
    """(lineno, text) with text at most `chunk` characters; a longer line yields several pieces.

    Pure streaming: at most two chunks are held at once, whatever the file looks like.
    """
    lineno = 0
    carry = ""
    try:
        f = open(path, encoding="utf-8", errors="replace")
    except OSError as e:
        # LOUD, NEVER SILENT: a file that cannot be read is not a file with no errors.
        print(f"  NOTE: {path} could not be read ({e}) -- not scanned", file=sys.stderr)
        return
    with f:
        while True:
            block = f.read(chunk)
            if not block:
                break
            buf = carry + block
            carry = ""
            start = 0
            while True:
                nl = buf.find("\n", start)
                if nl < 0:
                    carry = buf[start:]
                    break
                lineno += 1
                piece = buf[start:nl]
                start = nl + 1
                for i in range(0, max(len(piece), 1), chunk):
                    yield lineno, piece[i:i + chunk]
            if len(carry) >= chunk:            # a single line longer than the window
                lineno += 1
                for i in range(0, len(carry), chunk):
                    yield lineno, carry[i:i + chunk]
                carry = ""
    if carry:
        lineno += 1
        for i in range(0, len(carry), chunk):
            yield lineno, carry[i:i + chunk]


def _scan_file(filepath):
    if filepath.suffix == ".jsonl":
        return _scan_jsonl(filepath)
    elif filepath.suffix == ".json":
        return _scan_json(filepath)
    else:
        return _scan_text(filepath)


def _scan_jsonl(filepath):
    """Findings from one JSONL file, STREAMED (t1599, 2026-10-24).

    THE ROOT CAUSE OF A 23 GB SCAN. This used to hold three full copies of the file at once:
    `content = read_text()`, `content.splitlines()` (every line as its own str), and a `rows` list
    of every parsed dict for a second pass that only needed COUNTS. On
    workspace/run-logs/slot-ledger.jsonl (2.71 GB) that measured 22.7 GB resident, on a box whose
    model needs 93 GB of 122 -- and the evening it did, the memory guard stopped Swift while Jas
    was talking to it.

    The second pass needed {field: counts per value width} and the first locator for each width,
    which is bounded by the number of distinct widths, not by the number of rows. So everything is
    accumulated in ONE streaming pass and no row is retained. Measured on a 68 MB fixture: 281 MB
    peak before, under 30 MB after, and the findings are identical.
    """
    findings = []
    ROUND_WIDTHS = [i * 100 for i in range(1, 41)] + [16, 32, 64, 128, 256, 512, 1024, 2048, 4096]
    stats = {}   # field -> {'counts': {width: count}, 'total': total, 'first': {width: (locator, at)}}
    MAX_ROW = 4 * 1024 * 1024
    for _lineno, line in _bounded_lines(filepath):   # one window live at a time
        line = line.strip()
        if not line:
            continue
        if len(line) >= MAX_ROW:
            # A "row" this size is not a record we can judge, and json.loads on it is the
            # allocation that killed the box. Say so rather than skipping in silence.
            findings.append({"kind": "row-too-large-to-scan", "source": str(filepath),
                             "locator": _lineno,
                             "evidence": f"{{{len(line)} chars on line {_lineno}}}"})
            continue
        try:
            row = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            print("  NOTE: unreadable row -- not scanned", file=sys.stderr)
            continue
        if not isinstance(row, dict):
            continue
        findings.extend(_check_row(row, str(filepath)))
        for field, value in row.items():
            # NOT EVERY FIXED WIDTH IS A CUT (repaired at merge, 2026-09-30). A hex digest is
            # exactly 64 by design, and a constant message is the same length in every row: on
            # the live logs the only hit was agent-runs.jsonl 'warning', 161 rows of one
            # 100-character sentence. A value cut at a width almost never ends on a full stop.
            if (isinstance(value, str) and len(value) > 0
                    and not re.fullmatch(r"[0-9a-fA-F-]+", value)
                    and not value.rstrip().endswith((".", "!", "?", ")"))):
                width = len(value)
                s_ = stats.setdefault(field, {"counts": {}, "total": 0, "first": {}})
                s_["counts"][width] = s_["counts"].get(width, 0) + 1
                s_["total"] += 1
                if width not in s_["first"]:
                    s_["first"][width] = (row.get("id", row.get("at")), row.get("at"))
    for field, s_ in stats.items():
        total = s_["total"]
        for width, count in s_["counts"].items():
            if width in ROUND_WIDTHS and (count / total) >= 0.5:
                locator, at = s_["first"][width]
                findings.append({
                    "kind": "record-suspiciously-exact",
                    "source": str(filepath),
                    "locator": locator,
                    "evidence": f"{{{field},{width},{count},{total},{locator}}}",
                    "at": at,
                })
    return findings


def _scan_json(filepath):
    findings = []
    try:
        content = filepath.read_text()
    except OSError:
        return findings
    try:
        row = json.loads(content)
    except (json.JSONDecodeError, ValueError):
        return findings
    if isinstance(row, dict):
        findings.extend(_check_row(row, str(filepath)))
    return findings


def _scan_text(filepath):
    """Findings from one log/markdown file, STREAMED for the same reason as _scan_jsonl (t1599):
    `workspace/run-logs/manager-commands/` holds single outputs of 0.4 GB, and splitlines() on one
    of those is a list of every line before a single finding is considered."""
    findings = []
    try:
        for i, line in _bounded_lines(filepath):
            findings.extend(_check_text_line(line, str(filepath), i))
    except OSError:
        return findings
    # A LOG LINE HAS NO TIMESTAMP OF ITS OWN; the file's last write is the honest upper bound,
    # and without it every log finding sorted below every dated one and never surfaced.
    import time as _t
    when = _t.strftime("%Y-%m-%dT%H:%M:%SZ", _t.gmtime(filepath.stat().st_mtime))
    for f in findings:
        if not f.get("at"):
            f["at"] = when
    return findings


# KEY FAMILIES (t271). Ledgers here spell the same things differently: the timestamp is `at`, `ts`
# (mcp-calls) or `timestamp` (session transcripts); the verdict is `status` or `outcome`. A row
# none of whose keys the reader knows is UNREADABLE -- counted by census(), never a silent zero.
TIME_KEYS = ("at", "ts", "timestamp")
KNOWN_KEYS = set(TIME_KEYS) | {"done_reason", "outcome", "status", "tokens", "content", "type",
                               "event", "kind", "id", "sessionId", "message", "is_error"}


def _at(row):
    return next((row[k] for k in TIME_KEYS if row.get(k)), None)


def _tool_errors(row):
    """Failing tool results in a transcript row: top-level (type tool_result, is_error) or nested in
    message.content, which is how ~/.claude/projects rows carry them."""
    if row.get("type") == "tool_result" and row.get("is_error"):
        return [row.get("content")]
    msg = row.get("message")
    out = []
    if isinstance(msg, dict) and isinstance(msg.get("content"), list):
        for c in msg["content"]:
            if isinstance(c, dict) and c.get("type") == "tool_result" and c.get("is_error"):
                out.append(c.get("content"))
    return out


def _check_row(row, source):
    findings = []
    at = _at(row)
    locator = row.get("id", at)
    evidence = json.dumps(row)
    errs = _tool_errors(row)
    if errs:
        sid = row.get("sessionId", "?")
        findings.append({
            "kind": "transcript-failure",
            "source": source,
            "locator": f"{sid}:{row.get('uuid') or at}",
            "evidence": evidence,
            "at": at,
        })

    if row.get("done_reason") == "length":
        findings.append({
            "kind": "truncated-output",
            "source": source,
            "locator": locator,
            "evidence": evidence,
            "at": at,
        })

    if row.get("done_reason") == "tool-round-cap":
        findings.append({
            "kind": "tool-loop-exhausted",
            "source": source,
            "locator": locator,
            "evidence": evidence,
            "at": at,
        })

    if row.get("outcome") == "UNPARSEABLE":
        findings.append({
            "kind": "unusable-parse",
            "source": source,
            "locator": locator,
            "evidence": evidence,
            "at": at,
        })

    tokens = row.get("tokens")
    content_val = row.get("content")
    if tokens and isinstance(tokens, (int, float)) and tokens > 0 and content_val == "":
        findings.append({
            "kind": "empty-with-tokens",
            "source": source,
            "locator": locator,
            "evidence": evidence,
            "at": at,
        })

    # THE OUTCOME FAMILY, ONCE (t271): a non-healthy `outcome` is a finding -- but a row a specific
    # signature above already named (UNPARSEABLE -> unusable-parse) is reported once, not per family.
    outcome = row.get("outcome")
    if not findings and outcome is not None and outcome not in HEALTHY_STATUS:
        findings.append({
            "kind": "unhealthy-outcome",
            "source": source,
            "locator": locator,
            "evidence": evidence,
            "at": at,
        })

    status = row.get("status")
    if status is not None and status not in HEALTHY_STATUS:
        findings.append({
            "kind": f"status-{status}",
            "source": source,
            "locator": locator,
            "evidence": evidence,
            "at": at,
        })

    return findings


# THE HARNESS'S OWN TERMINAL WORDS (t275, fix 3). Not a library of guessed error phrasing: these
# are the fixed strings coder.py and Python print when a run ends badly, so a .log under
# run-logs can produce a finding. Before this, nine real logs full of failures scanned as zero.
_TERMINAL_RE = re.compile(r"NOT ACCEPTED after \d+ attempt|REFUSING TO START|NEEDS-SCOPE -- refusing"
                          r"|^Traceback \(most recent call last\):")


# A FINDING MUST NOT HOLD THE INPUT (t1599, second layer). `_check_text_line` stored the whole line
# as its evidence. That was fine while lines were log lines; once the reader windows a 255 MB line
# into 64 KB pieces, "the whole line" means the window, and 43,312 findings on the live tree came
# to 1.9 GB -- the scan merely moved its footprint from the reader to the results.
#
# So evidence is a bounded QUOTE with the cut MARKED (never-truncate-a-record: a record may be
# shortened only if it says so). The locator and the length stay, so nothing is lost but bulk.
EVIDENCE_MAX = 300


def _evidence(line, match=None):
    if len(line) <= EVIDENCE_MAX:
        return line
    if match is not None:
        a = max(0, match.start() - 80)
    else:
        a = 0
    b = min(len(line), a + EVIDENCE_MAX)
    return (("…" if a else "") + line[a:b]
            + f" …[cut: this line is {len(line)} chars, {len(line) - (b - a)} not shown]")


def _check_text_line(line, source, lineno):
    findings = []
    if _TERMINAL_RE.search(line):
        findings.append({"kind": "run-failed", "source": source, "locator": lineno,
                         "evidence": _evidence(line.strip()), "at": None})

    m = _SIGNAL_EXIT_RE.search(line)
    if m:
        code = int(m.group(1))
        if code >= 129:
            findings.append({
                "kind": "silent-signal-death",
                "source": source,
                "locator": lineno,
                "evidence": _evidence(line, m),
                "at": None,
            })

    tv = _TRUNCATED_VALUE_RE.search(line)
    if tv:
        is_truncation = int(tv.group(1)) != 0
    else:
        is_truncation = "truncated" in line.lower()
    if is_truncation:
        findings.append({
            "kind": "read-truncated",
            "source": source,
            "locator": lineno,
            "evidence": _evidence(line, tv),
            "at": None,
        })

    return findings


def main():
    import argparse
    import sys
    import json
    parser = argparse.ArgumentParser()
    parser.add_argument("sources", nargs="*", default=DEFAULT_SOURCES)
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    findings = scan(args.sources)
    skipped_paths = skipped()
    if args.json:
        print(json.dumps({"findings": findings, "skipped": skipped_paths}))
        sys.exit(0)
    for f in findings:
        print(f)
    print(f"{len(findings)} finding(s); {len(skipped_paths)} skipped")
    sys.exit(0)


if __name__ == "__main__":
    main()
