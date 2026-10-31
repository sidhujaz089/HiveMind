#!/usr/bin/env python3
"""Draft the session log from git, so the only thing left to write is the part git cannot know.

    python3 scripts/session_log.py                      draft today's log
    python3 scripts/session_log.py --stamp-status       also stamp each touched STATUS.md
    python3 scripts/session_log.py --date 2026-08-21 --reconstruct
    python3 scripts/session_log.py --check              are today's log and stamps complete?

WHY THIS EXISTS
---------------
`session_end.py` has blocked on a missing session log since it was written, and the
compliance audit on 2026-08-25 still found **three days with commits and no log** --
2026-08-20 (20 commits), 2026-08-21 (8), 2026-08-24 (52). A blocker that stops a session
from closing does not produce a log; it produces a session that closes some other way. The
27 unpushed commits found at the same time are the same story from the other end.

So the fix is not a louder refusal. It is that the log already exists by the time anyone is
asked about it, with every fact git can supply already in it -- commits, files, projects,
which agents worked, what tests exist. What is left is WHY, and no script can write that.

WHAT IT WILL NOT DO
-------------------
It will not write the narrative sections. They are emitted as `<!-- TODO -->` placeholders
and `--check` refuses while any remain. A generated paragraph of plausible-sounding
rationale is worse than an empty section: the empty one is visibly unfinished, and the
generated one is indistinguishable from something a person actually decided.

Same rule for STATUS.md. `--stamp-status` appends a dated, factual line -- the date, the
commit range, and a link to the log. It does not summarise what the change means.
"""

import argparse
import re
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SESSIONS = REPO / "workspace" / "sessions"
TODO = "<!-- TODO: "
STAMP = "<!-- session-log-stamp -->"


def git(*args):
    p = subprocess.run(["git", "-C", str(REPO), *args],
                       capture_output=True, text=True, errors="replace", timeout=120)
    out = (p.stdout or "").rstrip("\n")
    return p.returncode, [l for l in out.split("\n") if l]


def commits_on(day):
    """Every commit authored on `day`, oldest first, with sha, subject and Agent trailer."""
    nxt = (datetime.fromisoformat(day) + timedelta(days=1)).date().isoformat()
    rc, lines = git("log", "--reverse", f"--since={day} 00:00", f"--until={nxt} 00:00",
                    "--pretty=%h\x1f%s\x1f%(trailers:key=Agent,valueonly)")
    out = []
    for l in lines:
        parts = l.split("\x1f")
        if len(parts) == 3:
            out.append({"sha": parts[0], "subject": parts[1],
                        "agent": parts[2].strip() or "unattributed"})
    return out


def uncommitted():
    _, dirty = git("status", "--porcelain")
    return [re.sub(r"^.{3}", "", d).strip('"') for d in dirty]


def files_for(commits):
    files = set()
    for c in commits:
        _, fs = git("show", "--name-only", "--pretty=", c["sha"])
        files.update(fs)
    return sorted(files)


def projects_in(files):
    return sorted({f.split("/")[1] for f in files if re.match(r"^projects/[^/]+/", f)})


def is_doc(f):
    return f.endswith((".md", ".txt")) or "/docs/" in f


def slug(text):
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s[:48].rstrip("-") or "session"


def log_path(day):
    existing = sorted(SESSIONS.glob(f"{day}-*.md")) if SESSIONS.is_dir() else []
    return existing[0] if existing else None


def draft(day, commits, files, dirty, reconstruct):
    projects = projects_in(files)
    agents = sorted({c["agent"] for c in commits}) or ["unattributed"]
    rng = f"`{commits[0]['sha']}..{commits[-1]['sha']}`" if commits else "(nothing committed)"

    head = [f"# {day} — " + TODO + "one line: what this session was about -->",
            "",
            f"**Agent:** {' · '.join(agents)} · **Machine:** HiveMind (GX10)",
            f"**Projects touched:** {', '.join(projects) or 'workspace'}",
            f"**Commits:** {rng} ({len(commits)})",
            ""]

    if reconstruct:
        head += [
            "> **RECONSTRUCTION.** This log was generated from git after the fact, on "
            f"{date.today().isoformat()}, because the session did not write one. Everything "
            "below the line is derived from commits and is therefore true but thin: it "
            "records what changed and not why. Nobody should read the absence of a reason "
            "here as evidence there wasn't one.",
            ""]

    if reconstruct:
        # A reconstruction must NOT carry TODO placeholders. There is nobody left to fill
        # them: the session that could have is over, and the next reader filling them in
        # would be writing fiction into the one record that exists. So the sections say
        # plainly what is and is not recoverable, and --check accepts that as finished.
        gone = ("Not recoverable. This log was generated from git after the fact; the "
                "reasoning was never written down and is gone.")
        body = [
            "## What I set out to do", "", gone, "",
            "## What actually happened", "",
            "See the commit record below — that is the whole of what survives.", "",
            "## What I verified", "",
            "Unknown. Do not read this section's silence as evidence that nothing was "
            "verified, or that anything was. " + gone, "",
            "## What I did not verify", "", gone, "",
            "## Decisions taken", "",
            "Any durable decision from this day should appear in `docs/DECISIONS.md`; if "
            "one was taken and recorded nowhere, it is lost.", "",
            "## Where I left it", "",
            "`STATE.md` as of the last commit below is the only account of this.", "",
            "## Next step", "",
            "Superseded — this day is in the past and later sessions have run since.", "",
            "---", "", "## The record, from git", "",
            f"{len(commits)} commit(s):", "",
        ]
        for c in commits:
            body.append(f"- `{c['sha']}` {c['subject']}  ·  {c['agent']}")
        body.append("")
        if files:
            body += [f"{len(files)} file(s) changed:", ""]
            for p in projects:
                n = [f for f in files if f.startswith(f"projects/{p}/")]
                body.append(f"- **{p}** — {len(n)} file(s), "
                            f"{len([f for f in n if not is_doc(f)])} non-doc")
            rest = [f for f in files if not re.match(r"^projects/", f)]
            if rest:
                body.append(f"- **workspace/repo-wide** — {len(rest)} file(s)")
            body.append("")
        return "\n".join(head + body)

    body = [
        "## What I set out to do",
        "",
        TODO + "the goal at the start, including anything Jas asked for in his own words -->",
        "",
        "## What actually happened",
        "",
        TODO + "the narrative, including the dead ends -- a dead end recorded here is one "
        "nobody has to walk into twice -->",
        "",
        "## What I verified",
        "",
        TODO + "what was actually RUN or observed, with results. If something was "
        "compile-checked but never executed, say exactly that (L-028) -->",
        "",
        "## What I did not verify",
        "",
        TODO + "everything shipped on reasoning alone. This is the section that protects "
        "the next session from inheriting a false assumption -->",
        "",
        "## Decisions taken",
        "",
        TODO + "anything that closes off an option. Durable ones also go in "
        "docs/DECISIONS.md -->",
        "",
        "## Where I left it",
        "",
        TODO + "the state at the moment of stopping. Must agree with STATE.md -->",
        "",
        "## Next step",
        "",
        TODO + "the single next action, same as the one in STATE.md -->",
        "",
        "---",
        "",
        "## The record, from git",
        "",
        f"{len(commits)} commit(s):",
        "",
    ]
    for c in commits:
        body.append(f"- `{c['sha']}` {c['subject']}  ·  {c['agent']}")
    body.append("")

    if dirty:
        body += [f"{len(dirty)} path(s) uncommitted at the time of writing:", ""]
        body += [f"- `{d}`" for d in dirty[:40]]
        body.append("")

    if files:
        body += [f"{len(files)} file(s) changed:", ""]
        for p in projects:
            n = [f for f in files if f.startswith(f"projects/{p}/")]
            code = [f for f in n if not is_doc(f)]
            body.append(f"- **{p}** — {len(n)} file(s), {len(code)} non-doc")
        rest = [f for f in files if not re.match(r"^projects/", f)]
        if rest:
            body.append(f"- **workspace/repo-wide** — {len(rest)} file(s)")
        body.append("")

    return "\n".join(head + body)


def stamp_status(day, files, log_name, apply):
    """Append one dated, factual line to each touched project's STATUS.md.

    Deliberately not a summary. The audit's complaint is that STATUS.md stands still while
    code moves, and the cheapest true fix is a pointer to the log that does carry the why.
    """
    done = []
    for p in projects_in(files):
        n = [f for f in files if f.startswith(f"projects/{p}/")]
        code = [f for f in n if not is_doc(f)]
        if not code:
            continue
        status = REPO / "projects" / p / "STATUS.md"
        if not status.is_file():
            continue
        text = status.read_text(encoding="utf-8")
        line = (f"- {day} — {len(code)} non-doc file(s) changed; "
                f"see `workspace/sessions/{log_name}`. {STAMP}")
        if f"- {day} —" in text and STAMP in text:
            continue
        if "## Changes" in text:
            text = text.replace("## Changes", f"## Changes\n\n{line}", 1)
        else:
            text = text.rstrip() + "\n\n## Changes\n\n" + line + "\n"
        if apply:
            status.write_text(text, encoding="utf-8")
        done.append(p)
    return done


def check(day):
    """What session_end asks before it will commit. Returns a list of complaints."""
    problems = []
    p = log_path(day)
    if not p:
        return [f"no session log for {day} in workspace/sessions/"]
    text = p.read_text(encoding="utf-8")
    n = text.count(TODO)
    if n:
        problems.append(f"{p.name} still has {n} unfilled TODO placeholder(s)")
    commits = commits_on(day)
    files = files_for(commits) + uncommitted()
    for proj in projects_in(files):
        code = [f for f in files
                if f.startswith(f"projects/{proj}/") and not is_doc(f)]
        if not code:
            continue
        status = REPO / "projects" / proj / "STATUS.md"
        if not status.is_file():
            problems.append(f"projects/{proj} has no STATUS.md")
        elif f"- {day} —" not in status.read_text(encoding="utf-8"):
            problems.append(
                f"projects/{proj} changed ({len(code)} non-doc file(s)) but its STATUS.md "
                f"has no {day} entry")
    return problems


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--date", default=date.today().isoformat())
    ap.add_argument("--reconstruct", action="store_true",
                    help="label the log as generated after the fact")
    ap.add_argument("--stamp-status", action="store_true",
                    help="append a dated pointer line to each touched STATUS.md")
    ap.add_argument("--check", action="store_true",
                    help="report what is still missing; exit 1 if anything is")
    ap.add_argument("--force", action="store_true",
                    help="overwrite an existing log for that date")
    a = ap.parse_args(argv)
    day = a.date

    if a.check:
        problems = check(day)
        for p in problems:
            print(f"  {p}")
        if not problems:
            print(f"  session log for {day} is complete")
        return 1 if problems else 0

    # Refuse to label TODAY as a reconstruction. Backfilling the audit's list of logless
    # days on 2026-08-25 included 2026-08-24, which was that day -- and produced a log
    # saying the reasoning "was never written down and is gone" for a session that was
    # still running and could simply write it. A reconstruction is a claim about what is
    # unrecoverable, so making it about the present is always false.
    if a.reconstruct and day == date.today().isoformat():
        print(f"Refusing: {day} is today. A reconstruction claims the reasoning is gone,")
        print("and it is not -- this session can still write it. Drop --reconstruct.")
        return 1

    commits = commits_on(day)
    dirty = uncommitted() if day == date.today().isoformat() else []
    files = files_for(commits) + dirty
    if not commits and not dirty:
        print(f"No commits and nothing uncommitted on {day} — no session to log.")
        return 0

    existing = log_path(day)
    if existing and not a.force:
        print(f"Log already exists: {existing.relative_to(REPO)}")
        remaining = existing.read_text(encoding="utf-8").count(TODO)
        print(f"  {remaining} TODO placeholder(s) still to fill"
              if remaining else "  complete")
    else:
        title = slug(commits[-1]["subject"] if commits else "work in progress")
        path = existing if existing else SESSIONS / f"{day}-{title}.md"
        SESSIONS.mkdir(parents=True, exist_ok=True)
        path.write_text(draft(day, commits, files, dirty, a.reconstruct), encoding="utf-8")
        print(f"Drafted {path.relative_to(REPO)}")
        print(f"  {len(commits)} commit(s), {len(files)} file(s), "
              f"{len(projects_in(files))} project(s)")
        print(f"  {draft(day, commits, files, dirty, a.reconstruct).count(TODO)} "
              f"section(s) for you to write — git cannot know why")

    if a.stamp_status:
        name = (log_path(day) or Path("")).name
        done = stamp_status(day, files, name, apply=True)
        print(f"  stamped STATUS.md for: {', '.join(done) or '(none needed)'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
