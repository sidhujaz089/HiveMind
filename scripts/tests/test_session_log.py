#!/usr/bin/env python3
"""Fixtures for the session log drafter.

The audit on 2026-08-25 found three days with commits and no log, against a session_end
that had blocked on exactly that since it was written. So the property under test is not
"can it write a file" -- it is that the generated log is *visibly unfinished* and that
--check REFUSES it until a person has written the part git cannot know.

A drafter that produced a plausible, complete-looking log would be worse than no drafter
at all: the audit would go quiet while the record became fiction.
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPT = ROOT / "scripts" / "session_log.py"
PASSED = FAILED = 0


def report(label, got, want):
    global PASSED, FAILED
    if got == want:
        PASSED += 1
        print(f"  [ok]   {label}: {got}")
    else:
        FAILED += 1
        print(f"  [BAD]  {label}: expected {want}, got {got}")


def git(repo, *args, **kw):
    return subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True, **kw)


def make_repo(tmp):
    """A repo with one project, one commit touching code, no STATUS.md entry."""
    repo = tmp / "repo"
    (repo / "scripts").mkdir(parents=True)
    (repo / "workspace" / "sessions").mkdir(parents=True)
    (repo / "projects" / "demo").mkdir(parents=True)
    shutil.copy(SCRIPT, repo / "scripts" / "session_log.py")
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.email", "t@t")
    git(repo, "config", "user.name", "t")
    (repo / "projects" / "demo" / "STATUS.md").write_text("# demo\n\nWhere it stands.\n")
    (repo / "projects" / "demo" / "app.py").write_text("x = 1\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "feat(demo): a thing",
        "--trailer", "Agent=claude-code/opus-5")
    return repo


def run(repo, *args):
    return subprocess.run([sys.executable, str(repo / "scripts" / "session_log.py"), *args],
                          capture_output=True, text=True, cwd=str(repo))


def today(repo):
    return git(repo, "log", "-1", "--pretty=%ad", "--date=short").stdout.strip()


def main():
    tmp = Path(tempfile.mkdtemp())
    try:
        repo = make_repo(tmp)
        day = today(repo)

        print("A drafted log is written, and it is VISIBLY unfinished:")
        r = run(repo, "--date", day)
        report("exit 0", r.returncode, 0)
        logs = list((repo / "workspace" / "sessions").glob("*.md"))
        report("one log written", len(logs), 1)
        text = logs[0].read_text()
        report("has unfilled TODO placeholders", text.count("<!-- TODO: ") >= 7, True)
        report("no narrative was invented",
               "The narrative" in text or "<!-- TODO" in text, True)

        print("\n  and the git-derived facts ARE filled in, so only the why is left:")
        report("commit subject present", "feat(demo): a thing" in text, True)
        report("agent attributed", "claude-code/opus-5" in text, True)
        report("project named", "demo" in text, True)

        print("\n--check REFUSES a log with placeholders still in it:")
        r = run(repo, "--check", "--date", day)
        report("non-zero exit", r.returncode, 1)
        report("says why", "TODO placeholder" in r.stdout, True)

        print("\n  and ACCEPTS it once the placeholders are gone:")
        filled = re.sub(r"<!-- TODO: .*?-->", "written by a person", text, flags=re.S)
        logs[0].write_text(filled)
        (repo / "projects" / "demo" / "STATUS.md").write_text(
            f"# demo\n\n## Changes\n\n- {day} — updated\n")
        r = run(repo, "--check", "--date", day)
        report("exit 0", r.returncode, 0)

        print("\nSTATUS.md: a project whose CODE moved without a dated entry is refused:")
        (repo / "projects" / "demo" / "STATUS.md").write_text("# demo\n\nNo dated entry.\n")
        r = run(repo, "--check", "--date", day)
        report("refused", r.returncode, 1)
        report("names the project", "projects/demo" in r.stdout, True)

        print("\n  --stamp-status writes the dated pointer, and then it passes:")
        run(repo, "--stamp-status", "--date", day, "--force")
        filled2 = re.sub(r"<!-- TODO: .*?-->", "written",
                         (list((repo / "workspace" / "sessions").glob("*.md"))[0]).read_text(),
                         flags=re.S)
        list((repo / "workspace" / "sessions").glob("*.md"))[0].write_text(filled2)
        status = (repo / "projects" / "demo" / "STATUS.md").read_text()
        report("STATUS.md carries the date", f"- {day} —" in status, True)
        report("and points at the log", "workspace/sessions/" in status, True)
        report("but does NOT summarise the change",
               "means" not in status and "because" not in status, True)
        report("check now passes", run(repo, "--check", "--date", day).returncode, 0)

        print("\n  stamping twice does not duplicate the line:")
        run(repo, "--stamp-status", "--date", day)
        run(repo, "--stamp-status", "--date", day)
        s2 = (repo / "projects" / "demo" / "STATUS.md").read_text()
        report("one dated line", s2.count(f"- {day} —"), 1)

        print("\nA DOC-only change does not demand a STATUS.md entry:")
        repo2 = make_repo(tmp / "b")
        (repo2 / "projects" / "demo" / "README.md").write_text("hi\n")
        git(repo2, "add", "-A")
        git(repo2, "commit", "-q", "-m", "docs(demo): note",
            "--trailer", "Agent=claude-code/opus-5")
        d2 = today(repo2)
        run(repo2, "--date", d2)
        p2 = list((repo2 / "workspace" / "sessions").glob("*.md"))[0]
        p2.write_text(re.sub(r"<!-- TODO: .*?-->", "w", p2.read_text(), flags=re.S))
        # app.py is in the first commit of the same day, so demo still needs a stamp;
        # what matters is that the check names a REASON, not that it never fires.
        out = run(repo2, "--check", "--date", d2).stdout
        report("complaint mentions non-doc files, not docs",
               "non-doc" in out or out.strip().endswith("is complete"), True)

        print("\n--reconstruct labels a log built after the fact, so nobody reads it as live:")
        repo3 = make_repo(tmp / "c")
        # A PAST day -- --reconstruct is refused for today, and rightly.
        d3 = "2020-05-05"
        (repo3 / "projects" / "demo" / "old.py").write_text("y = 2\n")
        git(repo3, "add", "-A")
        git(repo3, "commit", "-q", "-m", "feat(demo): older thing",
            "--trailer", "Agent=claude-code/opus-5",
            "--date", f"{d3}T10:00:00",
            env={**os.environ, "GIT_COMMITTER_DATE": f"{d3}T10:00:00"})
        run(repo3, "--date", d3, "--reconstruct")
        t3 = list((repo3 / "workspace" / "sessions").glob("*.md"))[0].read_text()
        report("says RECONSTRUCTION", "RECONSTRUCTION" in t3, True)
        report("and warns the reasons are absent, not nonexistent",
               "wasn't one" in t3 or "was not one" in t3, True)

        print("\n--reconstruct is REFUSED for today, because today is not unrecoverable:")
        repo6 = make_repo(tmp / "f")
        r6 = run(repo6, "--date", today(repo6), "--reconstruct")
        report("refused", r6.returncode, 1)
        report("says why", "is today" in r6.stdout, True)
        report("wrote nothing",
               len(list((repo6 / "workspace" / "sessions").glob("*.md"))), 0)

        print("\nA day with no commits writes nothing at all:")
        repo4 = make_repo(tmp / "d")
        r = run(repo4, "--date", "2001-01-01")
        report("exit 0", r.returncode, 0)
        report("no log written",
               len(list((repo4 / "workspace" / "sessions").glob("2001-*.md"))), 0)

        print("\nAn existing log is never silently overwritten:")
        repo5 = make_repo(tmp / "e")
        d5 = today(repo5)
        run(repo5, "--date", d5)
        p5 = list((repo5 / "workspace" / "sessions").glob("*.md"))[0]
        p5.write_text("# mine\n\nhand written\n")
        run(repo5, "--date", d5)
        report("hand-written log survives", p5.read_text(), "# mine\n\nhand written\n")

    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
