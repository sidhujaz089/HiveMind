#!/usr/bin/env python3
"""Reads git history and reports what did not meet the contract, attributed.

This system assumes workers drift from the rules -- not maliciously, just because
a tool did not read the docs, or a model was too small to follow them, or a person
was in a hurry. So it detects drift rather than trusting compliance.

Findings are attributed to whoever the Agent: trailer names. Four AI tools and a
person share this repository; without attribution a finding cannot be routed to
anyone, and git log cannot tell you who to ask.

    python3 scripts/audit_compliance.py
    python3 scripts/audit_compliance.py --since <sha>
    python3 scripts/audit_compliance.py --update-baseline

Exit 0 = no findings. Exit 1 = findings. Exit 2 = could not run.

Ported from audit-compliance.ps1.
"""

import argparse
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BASELINE_FILE = REPO_ROOT / "workspace" / ".compliance-baseline"

# Types and trailer shape must match scripts/hooks/commit-msg exactly. If you change one,
# change both -- a hook and an audit that disagree teach people to ignore the audit.
VALID_TYPES = "feat|fix|docs|refactor|test|chore|migrate|session"
# The MODEL half accepts a colon, because ollama tags carry one (`qwen3.8:27b-mtp-q4_K_M`)
# and the hook has always accepted it. This class did not, so the audit reported a HIGH
# "Agent trailer missing" against 11 commits whose trailers the hook had just approved --
# an audit that cries wolf about valid work is one people learn to skip, which costs more
# than the check was ever worth. The comment above already required these two to match;
# nothing checked that they did. Found by the env3 probe (finding 17).
TRAILER_RE = re.compile(r"^Agent:\s*([A-Za-z0-9._-]+/[A-Za-z0-9._:-]+)\s*$", re.MULTILINE)

# The chat agent executes file-operation manifests written by an agent. Both the old
# PowerShell path and the Python port are watched: history contains the first, and the
# rule must not lapse the moment the file is renamed.
AGENT_FILES = ("scripts/chat-agent.ps1", "scripts/chat_agent.py")
ARM_LINE_RE = re.compile(r"^[+-]\s*(?:\$Armed|ARMED)\s*=")
ARM_TRUE_RE = re.compile(r"^\+\s*(?:\$Armed|ARMED)\s*=\s*(?:\$true|True)")

_TTY = sys.stdout.isatty()


def _c(code, text):
    return f"\033[{code}m{text}\033[0m" if _TTY else text


def git(*args):
    p = subprocess.run(
        ["git", "-C", str(REPO_ROOT), *args], capture_output=True, text=True, errors="replace",
        timeout=900,
    )
    out = (p.stdout or "").rstrip("\n")
    return p.returncode, (out.split("\n") if out else [])


def git_text(*args):
    return "\n".join(git(*args)[1])


class Finding:
    __slots__ = ("severity", "rule", "actor", "commit", "what", "why")

    def __init__(self, severity, rule, actor, commit, what, why):
        self.severity = severity
        self.rule = rule
        self.actor = actor or "unattributed"
        self.commit = commit
        self.what = what
        self.why = why


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since")
    ap.add_argument("--update-baseline", action="store_true")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    if not (REPO_ROOT / ".git").exists():
        print(_c("31", f"FATAL: no .git at {REPO_ROOT}"))
        return 2

    findings = []

    def add(severity, rule, actor, commit, what, why):
        findings.append(Finding(severity, rule, actor, commit, what, why))

    # --- work out the range to audit -------------------------------------------------
    since = args.since
    if not since and BASELINE_FILE.is_file():
        recorded = BASELINE_FILE.read_text(encoding="utf-8", errors="replace").split("\n")[0].strip()
        if recorded and git("cat-file", "-e", f"{recorded}^{{commit}}")[0] == 0:
            since = recorded

    if since:
        rng = f"{since}..HEAD"
        range_label = f"since {since[:7]}"
    else:
        rng = "HEAD~20..HEAD"
        if git("rev-parse", "HEAD~20")[0] != 0:
            rng = "HEAD"
        range_label = "last 20 commits (no baseline recorded)"

    shas = [s for s in git("rev-list", rng)[1] if re.fullmatch(r"[0-9a-f]{40}", s)]

    print()
    print(f"Compliance audit — {range_label}")
    print(_c("90", f"{len(shas)} commit(s) to examine"))

    if not since:
        print()
        for line in (
            "  No baseline recorded, so this is auditing raw history — including commits",
            "  made before the rules existed. Those findings are real but not actionable;",
            "  nobody can add a trailer to a commit that is already pushed.",
            "  session_end.py records a baseline so later runs only see new work.",
        ):
            print(_c("90", line))

    # --- per-commit checks -----------------------------------------------------------
    day_has_session = {}
    day_commits = defaultdict(list)
    state_touched = False

    for sha in shas:
        short = sha[:7]
        raw = git_text("show", "-s", "--format=%B", sha)
        subject = git_text("show", "-s", "--format=%s", sha)
        date = git_text("show", "-s", "--format=%cI", sha)[:10]
        files = [f for f in git("show", "--name-only", "--format=", sha)[1] if f]

        # Merges are generated by git and are not held to the format.
        parents = git_text("rev-list", "--parents", "-n", "1", sha).split()
        if len(parents) > 2:
            continue

        m = TRAILER_RE.search(raw)
        actor = m.group(1) if m else None
        day_commits[date].append(short)

        # --- attribution ---
        if not actor:
            add("HIGH", "Agent trailer missing", None, short, f'"{subject}"',
                "Nothing records which tool or person made this commit, so no finding "
                "about it can be routed to anyone. The commit-msg hook rejects this, "
                "which means the hook was not installed or was bypassed with --no-verify.")

        # --- message format ---
        if not re.match(rf"^({VALID_TYPES})(\([a-z0-9._-]+\))?: .+", subject):
            add("MEDIUM", "Commit message format", actor, short, f'"{subject}"',
                "Expected <type>(<project>): <summary>. git log stops being scannable "
                "when the shape varies, and this is the file everyone reads first when "
                "something breaks.")

        # --- arming the chat agent is a human-only act (D-21, amendment 1) ---
        #
        # An agent that can flip the Armed constant can arm its own executor, and every
        # bound in that script becomes self-administered. The human is the only link in
        # the chain that is not downstream of some agent's output.
        #
        # This looks at the actual diff of that one line rather than at the file, because
        # everything else in the agent is ordinary code that anyone may edit.
        for agent_file in AGENT_FILES:
            if agent_file not in files:
                continue
            diff = git("show", sha, "--", agent_file)[1]
            arm_diff = [ln for ln in diff if ARM_LINE_RE.match(ln)]

            # A '-' line means a previous value was replaced. Without one this is the
            # commit that CREATED the file, where Armed = False is the correct initial
            # state and not an edit by anyone. Flagging that would put a permanent HIGH
            # finding in the audit for doing the right thing, and a finding nobody can
            # clear is a finding everybody learns to scroll past.
            is_change = any(ln.startswith("-") for ln in arm_diff)
            arms_it = any(ARM_TRUE_RE.match(ln) for ln in arm_diff)
            evidence = "\n".join(arm_diff[:4])
            who = actor if actor else "no trailer"

            if is_change and arms_it and actor != "human/jas":
                add("HIGH", "Chat agent ARMED by a non-human", actor, short, evidence,
                    "The Armed constant may only change in a commit trailed "
                    f"'Agent: human/jas' (D-21). This commit set it true under '{who}'. "
                    "The chat agent executes file-operation manifests written by an "
                    "agent; an agent that can also arm it has closed a loop around every "
                    "other safeguard in that script. Set it back to false, then decide "
                    "with Jas whether arming was intended.")
            elif is_change and actor != "human/jas":
                add("MEDIUM", "Chat agent arming constant edited by a non-human", actor,
                    short, evidence,
                    "Not an arming -- the value did not become true -- but this line is "
                    "reserved to Jas (D-21) precisely so that no agent develops the habit "
                    "of touching it. If this was refactoring around the constant, move "
                    "the refactor away from the line.")
            elif is_change:
                add("LOW", "Chat agent arming state changed", actor, short, evidence,
                    "Correctly attributed to Jas. Recorded so the change is visible in "
                    "the audit trail rather than only in a diff nobody re-reads.")

        # --- access widened without an approved request ---
        #
        # docs/ACCESS.md is written by the same agents it constrains, so it is not
        # evidence about them on its own. This does not fix that -- nothing on this side
        # can -- but it makes a silent widening leave a trace.
        #
        # Deliberately crude. It flags rather than blocks: this is a smoke alarm and Jas
        # is the fire brigade. The real sensor for Drive sharing is Google's own activity
        # record, which nothing here can write.
        if "docs/ACCESS.md" in files:
            diff = git("show", sha, "--", "docs/ACCESS.md")[1]
            added = [ln for ln in diff if ln.startswith("+") and not ln.startswith("+++")]
            grantish = [
                ln for ln in added
                if re.search(r"\b(can|granted|allow|access to|share|write|delete)\b", ln, re.I)
            ]
            req_touched = [f for f in files if re.match(r"^workspace/access-requests/.+\.md$", f)]
            if grantish and not req_touched and actor != "human/jas":
                who = actor if actor else "no trailer"
                add("MEDIUM", "ACCESS.md changed without a request in the same commit",
                    actor, short, "\n".join(grantish[:4]),
                    f"docs/ACCESS.md gained capability-shaped lines under '{who}' with no "
                    "file under workspace/access-requests/ in the same commit. Approvals "
                    "are supposed to land together with the grant so the two cannot "
                    "drift. This is a flag, not proof -- editorial changes trip it too. "
                    "For anything about Drive sharing check Google's own activity record "
                    "rather than this repository's log.")

        # --- project code changed without its STATUS.md ---
        projects_touched = sorted({
            f.split("/")[1] for f in files if re.match(r"^projects/[^/]+/", f)
        })
        for p in projects_touched:
            code_changed = [
                f for f in files
                if f.startswith(f"projects/{p}/") and not f.endswith(".md")
            ]
            if code_changed and f"projects/{p}/STATUS.md" not in files:
                add("LOW", "STATUS.md not updated with code", actor, short,
                    f"projects/{p} changed ({len(code_changed)} non-doc file(s)) "
                    "without touching its STATUS.md",
                    "STATUS.md is the only current document for a project. Code moving "
                    "while it stays still is how it silently goes stale, and the next "
                    "session trusts it.")

        # --- files that should never be committed ---
        for f in files:
            if re.search(r"\.(gdoc|gsheet|gslides|gdraw|gform|gtable|glink)$", f):
                add("HIGH", "Google-native stub committed", actor, short, f,
                    "These are unreadable by every tool and their presence means a write "
                    "failed. The pre-commit hook blocks this.")
            if re.search(r"\.(apk|aab|jks|keystore|p12|pem|key)$", f):
                add("HIGH", "Build output or key material committed", actor, short, f,
                    ".gitignore excludes these. Reaching the index means it was "
                    "force-added or the ignore rules were changed.")

        if "STATE.md" in files:
            state_touched = True
        if any(f.startswith("workspace/sessions/") for f in files):
            day_has_session[date] = True

    # --- cross-commit checks ---------------------------------------------------------

    # A day with real work and no session log. The log is where a future session looks
    # for WHY, which git log does not carry.
    # Ask the DISK, not only the examined commit range. The log for a day is very often
    # written by the last commit of that day -- which becomes the next audit's baseline and
    # therefore falls outside the range. On 2026-08-25 that reported 2026-08-20 as a day
    # with no log while workspace/sessions/2026-08-20-the-catalogue-was-right.md sat in the
    # repository, committed on the day, in the baseline commit itself. A check that cries
    # wolf is how the true findings beside it get ignored.
    sessions_dir = REPO_ROOT / "workspace" / "sessions"
    for date in sorted(day_commits):
        on_disk = list(sessions_dir.glob(f"{date}-*.md")) if sessions_dir.is_dir() else []
        if on_disk:
            day_has_session[date] = True
        if date not in day_has_session and len(day_commits[date]) >= 3:
            add("MEDIUM", "No session log for a day of work", None, "",
                f"{date} — {len(day_commits[date])} commits, no file written under "
                "workspace/sessions/",
                "docs/WORKFLOW.md requires a session log. Without one the narrative "
                "record has a hole exactly where the most work happened.")

    if len(shas) >= 3 and not state_touched:
        add("MEDIUM", "STATE.md left stale", None, "",
            f"{len(shas)} commits in this range, none touching STATE.md",
            "STATE.md is what the next session reads first. If it is stale the whole "
            "system fails silently, which is the worst failure mode this design has.")

    # Uncommitted work: an interrupted session, or someone editing without committing.
    dirty = [ln for ln in git("status", "--porcelain")[1] if ln]
    if dirty:
        add("MEDIUM", "Uncommitted work in the tree", None, "",
            f"{len(dirty)} path(s) modified or untracked",
            "Uncommitted work is invisible to the next session and is lost if this "
            "machine dies. It usually means a session was cut off before its "
            "end-of-session run.")

    # Hooks not active -- the reason a whole class of the above gets through.
    hooks_path = git_text("config", "core.hooksPath").strip()
    if hooks_path != "scripts/hooks":
        add("HIGH", "Git hooks are not installed in this clone", None, "",
            f"core.hooksPath is '{hooks_path or '<unset>'}', expected 'scripts/hooks'",
            "Nothing is enforcing the commit format, the Agent trailer, or the secret "
            "scan. Run: python3 scripts/install_hooks.py")

    # --- report ----------------------------------------------------------------------
    def record_baseline():
        if args.update_baseline:
            head = git_text("rev-parse", "HEAD").strip()
            BASELINE_FILE.write_text(head + "\n", encoding="ascii")

    if not findings:
        if not args.quiet:
            print()
            if not shas:
                # ZERO COMMITS IS NOT COMPLIANCE. "Everything in this range followed the
                # contract" over an empty range is vacuously true, and this is the NORMAL
                # state right after session_end.py records the baseline at HEAD -- so the
                # most reassuring line the audit can print is the one it prints when it has
                # examined nothing. The count is on its own line above; nobody reads a
                # number when the next line is green.
                #
                # Third of this class on 2026-08-27 (L-136, L-137), and the mildest: the
                # claim is technically true and nothing is hidden. It is fixed anyway
                # because this is the audit that opens every session, and a green line that
                # means "nothing was looked at" teaches the reader that green means safe.
                print(_c("90", "  Nothing to audit — this range contains no commits. "
                               "That is not a compliance result."))
            else:
                print(_c("32", f"  No compliance findings across {len(shas)} commit(s). "
                               f"Everything in this range followed the contract."))
            print()
        record_baseline()
        return 0

    print()
    order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
    by_actor = defaultdict(list)
    for f in findings:
        by_actor[f.actor].append(f)

    for actor in sorted(by_actor):
        group = by_actor[actor]
        print(f"  {_c('36', actor)} — {len(group)} finding(s)")
        for f in sorted(group, key=lambda x: order[x.severity]):
            colour = {"HIGH": "31", "MEDIUM": "33"}.get(f.severity, "90")
            tail = _c("90", f"  ({f.commit})") if f.commit else ""
            print(f"    {_c(colour, '[' + f.severity + ']')} {f.rule}{tail}")
            for line in str(f.what).split("\n"):
                print(f"        {line}")
            print(_c("90", f"        why it matters: {f.why}"))
        print()

    high = len([f for f in findings if f.severity == "HIGH"])
    print(_c("90", "-" * 70))
    print(_c("33", f"{len(findings)} finding(s), {high} high severity"))
    print()
    print(_c("90", "  These are gaps in the system as much as in anyone's behaviour."))
    print(_c("90", "  Write them up: workspace/feedback/README.md explains how and why."))
    print()

    record_baseline()
    return 1


if __name__ == "__main__":
    sys.exit(main())
