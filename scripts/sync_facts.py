#!/usr/bin/env python3
"""Single-sources a fact across documents, so a restatement cannot drift.

THE PROBLEM THIS SOLVES

docs/CHAT-BRIDGE.md and docs/ACCESS.md stated the same fact about Chat's connector
capabilities and disagreed for three days (L-046). Each was internally consistent,
so every check passed. A restatement is a copy, and a copy drifts.

THE FIX, which is prevention rather than detection

One document OWNS each fact. Everything else INCLUDES it. The included text is
generated, never typed, and a check fails if a copy has drifted from its owner --
so a drifted copy cannot be committed, rather than being noticed later.

    Owner, in the document that is the source of truth:

        <!-- FACT:chat-connector-capabilities -->
        ... the canonical text ...
        <!-- /FACT -->

    Consumer, anywhere else:

        <!-- USES:chat-connector-capabilities FROM docs/ACCESS.md -->
        ... generated copy, do not edit ...
        <!-- /USES -->

    python3 scripts/sync_facts.py            # rewrite consumers from their owners
    python3 scripts/sync_facts.py --check    # report drift, change nothing (exit 1 on drift)

Enforced by check_workspace.py :: "Single-sourced facts match their owner".
"""

import argparse
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

FACT_RE = re.compile(
    r"^[ \t]*<!--\s*FACT:([A-Za-z0-9._-]+)\s*-->\n(.*?)\n[ \t]*<!--\s*/FACT\s*-->",
    re.DOTALL | re.MULTILINE,
)
# The body is optional. An earlier version required a newline before the closing marker,
# so an EMPTY block did not match at all and the tool reported "in step" having never seen
# it -- a check that passes by not looking, which is the failure this repository exists to
# catch. Hence USES_OPEN below and the parse-completeness guard in evaluate().
USES_RE = re.compile(
    r"^([ \t]*<!--\s*USES:([A-Za-z0-9._-]+)\s+FROM\s+(\S+)\s*-->\n)(.*?)([ \t]*<!--\s*/USES\s*-->)",
    re.DOTALL | re.MULTILINE,
)
USES_OPEN = re.compile(r"^[ \t]*<!--\s*USES:", re.MULTILINE)

# Documentation has to describe this syntax without tripping the parser that reads it.
# Two guards, and the SECOND one matters more than it looks:
#
#   1. Fenced blocks are stripped, so a worked example cannot be mistaken for a real block.
#      check_chat.py (retired 2026-10-06) solved the identical problem with "## TOOL CALLS" the same way.
#   2. The markers above are anchored to LINE START. A real block opens its own line; a
#      prose mention sits mid-sentence, usually in backticks, as L-046 and L-049 both do.
#
# An earlier version stripped inline code spans as well. That removed the backticked terms
# from the CANONICAL TEXT ITSELF -- `update_file` disappeared out of the capability table --
# so the tool would have written a mangled copy into the consumer while reporting success.
# Verified by inspecting the extracted body, and fixtured. Strip fences, never code spans.
FENCE_RE = re.compile(r"^[ \t]*```.*?(?:^[ \t]*```[ \t]*$|\Z)", re.MULTILINE | re.DOTALL)


def strip_examples(text):
    return FENCE_RE.sub("", text)
_TTY = sys.stdout.isatty()


def _c(code, text):
    return f"\033[{code}m{text}\033[0m" if _TTY else text


def tracked_markdown(repo_root):
    out = subprocess.run(
        ["git", "-C", str(repo_root), "ls-files", "*.md"],
        capture_output=True, text=True,
        timeout=60,
    ).stdout.split()
    # Session logs and outbox messages are historical records: they describe the past and
    # must not be rewritten when a fact changes later.
    # `git ls-files` reports the INDEX; the reads below come from DISK. A file that has been
    # moved or deleted but not yet committed is in one and not the other, which is an ordinary
    # mid-session state -- finishing a task moves it into workspace/tasks/done/ and produces
    # exactly this. Until 2026-08-21 that crashed the whole check with a FileNotFoundError
    # traceback, so a routine file move read as "the fact checker is broken".
    #
    # Skipping is right rather than merely convenient: a file that is not on disk cannot own
    # a fact, and the deletion is already visible to check_git_state. What is NOT acceptable
    # is dying, because a checker that cannot survive a normal state gets ignored.
    return [
        f for f in out
        if not f.startswith(("workspace/sessions/", "workspace/outbox/"))
        and (repo_root / f).is_file()
    ]


def collect_facts(repo_root, files):
    """{fact_id: (owner_path, text)}. A fact defined twice is an error, not a merge."""
    facts, duplicates = {}, []
    for rel in files:
        text = strip_examples((repo_root / rel).read_text(encoding="utf-8", errors="replace"))
        for m in FACT_RE.finditer(text):
            fid, body = m.group(1), m.group(2)
            if fid in facts:
                duplicates.append((fid, facts[fid][0], rel))
            else:
                facts[fid] = (rel, body)
    return facts, duplicates


def scan_uses(repo_root, files):
    """[(consumer, fact_id, declared_owner, current_text)]"""
    out = []
    for rel in files:
        text = strip_examples((repo_root / rel).read_text(encoding="utf-8", errors="replace"))
        for m in USES_RE.finditer(text):
            out.append((rel, m.group(2), m.group(3), m.group(4)))
    return out


# A FACT whose id is phase-<digits> declares a PHASE LABEL: the short name a document puts
# on a piece of the plan. A label is the text restated in prose with no FACT, no USES and no
# owner, which is the gap this module's docstring names -- the machinery above polices copies
# that were MARKED, and a label typed fresh is a copy that was never marked. The registry is
# the inverse of a blacklist: the approved set is the DECLARED set, so a label nobody declares
# is prose the mechanism has no opinion on.
PHASE_ID_RE = re.compile(r"^phase-(\d+)$", re.IGNORECASE)
PHASE_USE_RE = re.compile(r"Phase[ \t]+(\d+)(?!\d)", re.IGNORECASE)
USES_CLOSE = re.compile(r"^[ \t]*<!--\s*/USES\s*-->", re.MULTILINE)


def declared_labels(repo_root, files=None):
    """{label: owner_rel} for every FACT block with a phase id, normalised to "Phase 2".

    First file wins on a duplicate, exactly as collect_facts does: a label defined twice is
    a collision the audit has to show, not merge away.
    """
    labels = {}
    for rel in (tracked_markdown(repo_root) if files is None else files):
        text = strip_examples((repo_root / rel).read_text(encoding="utf-8", errors="replace"))
        for m in FACT_RE.finditer(text):
            hit = PHASE_ID_RE.match(m.group(1))
            if not hit:
                continue
            label = "Phase " + hit.group(1)
            if label not in labels:
                labels[label] = rel
    return labels


def label_uses(repo_root, files=None):
    """[(rel, line, label)] for a declared label used by a document that does not own it.

    Counted once per line, outside fenced examples, and outside the lines of a USES block
    (its marker line through its /USES line, inclusive) -- that body restates the label as a
    generated copy, which the drift branch already polices.

    Line numbers must be the document's own, so the fenced and USES ranges are computed on the
    RAW text and only the match is taken from the stripped one: strip_examples is what keeps a
    worked example from reading as a use, but a stripped file no longer shares line numbering
    with the document a finding cites.
    """
    owners = declared_labels(repo_root, files)
    uses = []
    for rel in (tracked_markdown(repo_root) if files is None else files):
        raw = (repo_root / rel).read_text(encoding="utf-8", errors="replace")
        lines = raw.splitlines()
        blocked = set()
        for m in FENCE_RE.finditer(raw):
            blocked.update(range(raw.count("\n", 0, m.start()),
                                 raw.count("\n", 0, max(m.start(), m.end() - 1)) + 1))
        closes = [raw.count("\n", 0, m.start()) for m in USES_CLOSE.finditer(raw)]
        for m in USES_OPEN.finditer(raw):
            start = raw.count("\n", 0, m.start())
            closed = [c for c in closes if c >= start]
            # An opening with no closing marker runs to the end of the file: every line below
            # it is inside the block, and the USES-open count below already reports the block
            # as malformed.
            blocked.update(range(start, (closed[0] if closed else len(lines)) + 1))
        for n, line in enumerate(lines):
            if n in blocked:
                continue
            for hit in PHASE_USE_RE.finditer(line):
                label = "Phase " + hit.group(1)
                owner = owners.get(label)
                if owner is not None and owner != rel:
                    uses.append((rel, n + 1, label))
                    break
    return uses


def label_problems(repo_root, files=None):
    """One string per un-owned use, citable as rel:line, naming the label and its owner."""
    owners = declared_labels(repo_root, files)
    out = []
    for rel, line, label in label_uses(repo_root, files):
        out.append(
            f"{rel}:{line}: uses {label}, which is owned by {owners[label]}. Declare it "
            "as a FACT in that document, or restatement must be a generated USES block."
        )
    return out


def evaluate(repo_root):
    """Returns (problems, drifted). Problems are structural; drift is content."""
    files = tracked_markdown(repo_root)
    facts, duplicates = collect_facts(repo_root, files)
    problems, drifted = [], []

    # ZERO FACTS IS NOT ALL FACTS AGREEING. With no fact defined anywhere, both lists come
    # back empty and three checks -- check_workspace's two and this script's own -- report
    # PASS having compared nothing. It is not hypothetical: exactly ONE fact is defined
    # across 351 tracked markdown files, so deleting a single block silently disables all
    # three. Q16 found this in a blind review; the local pair had cleared it, and so had I,
    # by checking the exception path and never the empty one.
    if not facts:
        problems.append(
            "no single-sourced facts were found in any tracked markdown file. Nothing was "
            "compared, so this is not a pass -- either every USES block has been removed, "
            "or the markers stopped matching.")
        return problems, drifted

    for fid, first, second in duplicates:
        problems.append(
            f"{fid}: defined in both {first} and {second}. A fact has exactly one owner."
        )

    # A phase label is prose, so the FACT/USES machinery above never sees it: STATE.md and
    # workspace/ROADMAP.md both wrote "Phase 2" with no declared owner and every check passed
    # (rca/315bac37c1c5). These ride in on `problems`, the field check_workspace.py fails on,
    # and never on `drifted` -- a label use is not a copy, so there is nothing for apply() to
    # rewrite, and a structural finding a run can auto-fix is a finding that silences itself.
    problems.extend(label_problems(repo_root, files))

    # If a file contains more USES openings than we parsed as complete blocks, the tool is
    # blind to the difference -- report that as broken rather than silently passing.
    for rel in files:
        raw = strip_examples((repo_root / rel).read_text(encoding="utf-8", errors="replace"))
        opened = len(USES_OPEN.findall(raw))
        parsed = len(USES_RE.findall(raw))
        if opened != parsed:
            problems.append(
                f"{rel}: {opened} USES opening(s) but {parsed} complete block(s). "
                "An unclosed or malformed block would be skipped silently."
            )

    for consumer, fid, declared_owner, current in scan_uses(repo_root, files):
        if fid not in facts:
            problems.append(f"{consumer}: USES {fid}, which no document defines.")
            continue
        owner, canonical = facts[fid]
        if declared_owner != owner:
            problems.append(
                f"{consumer}: USES {fid} FROM {declared_owner}, but {owner} owns it."
            )
            continue
        if current.rstrip("\n") != canonical.rstrip("\n"):
            drifted.append((consumer, fid, owner))

    return problems, drifted


def apply(repo_root):
    files = tracked_markdown(repo_root)
    facts, _ = collect_facts(repo_root, files)
    changed = 0
    for rel in files:
        p = repo_root / rel
        text = p.read_text(encoding="utf-8", errors="replace")

        def repl(m):
            nonlocal changed
            head, fid, declared_owner, current, tail = m.groups()
            if fid not in facts:
                return m.group(0)
            owner, canonical = facts[fid]
            if declared_owner != owner or current.rstrip("\n") == canonical.rstrip("\n"):
                return m.group(0)
            changed += 1
            return head + canonical + "\n" + tail

        new = USES_RE.sub(repl, text)
        if new != text:
            p.write_text(new, encoding="utf-8")
    return changed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true",
                    help="Report drift without changing anything.")
    args = ap.parse_args()

    problems, drifted = evaluate(REPO_ROOT)

    if args.check:
        for p in problems:
            print(_c("31", f"  BROKEN  {p}"))
        for consumer, fid, owner in drifted:
            print(_c("33", f"  DRIFTED {consumer} :: {fid} (owner {owner})"))
        if problems or drifted:
            print()
            print("  Run: python3 scripts/sync_facts.py")
            return 1
        print(_c("32", "  Single-sourced facts are in step with their owners."))
        return 0

    for p in problems:
        print(_c("31", f"  BROKEN  {p}"))
    if problems:
        print("\n  Structural problems are not fixed automatically. Resolve them first.")
        return 1
    n = apply(REPO_ROOT)
    print(_c("32", f"  Synced {n} block(s) from their owning document."))
    return 0


if __name__ == "__main__":
    sys.exit(main())
