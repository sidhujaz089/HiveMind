#!/usr/bin/env python3
"""Regenerates workspace/PROJECT-INDEX.md -- the whole workspace in one file.

Claude Chat can read this folder, but reading eight files on a phone is slow.
This is the one file that answers "what is going on with X", lifted verbatim from
STATE.md and the per-project STATUS.md files.

A stale index is worse than none: Chat cannot tell it is out of date and will
answer confidently from it. session_end.py regenerates it every run for exactly
that reason.

    python3 scripts/build_index.py

Ported from build-index.ps1.
"""

import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
OUT_FILE = REPO_ROOT / "workspace" / "PROJECT-INDEX.md"
_TTY = sys.stdout.isatty()


def _c(code, text):
    return f"\033[{code}m{text}\033[0m" if _TTY else text


def get_section(lines, pattern):
    """Pull one '## Heading' section out of a markdown document, without its heading."""
    start = None
    for i, line in enumerate(lines):
        if re.match(pattern, line):
            start = i
            break
    if start is None:
        return None
    body = []
    for line in lines[start + 1:]:
        if re.match(r"^##\s", line):
            break
        body.append(line)
    return "\n".join(body).strip()


def main():
    out = []

    def emit(text=""):
        out.append(text)

    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    head_sha = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "rev-parse", "--short", "HEAD"],
        capture_output=True, text=True,
        timeout=60,
    ).stdout.strip()

    emit("# PROJECT-INDEX — the whole workspace, in one file")
    emit("")
    emit("**GENERATED FILE. Do not edit.** Regenerate with `python3 scripts/build_index.py`.")
    emit("Every word below is lifted from `STATE.md` and the per-project `STATUS.md` files.")
    emit("If something here is wrong, fix it there — this file is a view, not a source.")
    emit("")
    emit(f"**Generated:** {stamp} from commit `{head_sha}`")
    emit("")
    emit("This exists for Claude Chat and for retrieval. Chat can read this folder, but reading")
    emit('eight files on a phone is slow — this is the one file that answers "what is going on')
    emit("with X\". For anything deeper, open the `STATUS.md` named in each section.")
    emit("")
    emit("---")
    emit("")

    # --- Right now, from STATE.md -----------------------------------------------------
    state_path = REPO_ROOT / "STATE.md"
    if state_path.is_file():
        state_lines = state_path.read_text(encoding="utf-8", errors="replace").split("\n")
        right_now = get_section(state_lines, r"^##\s+Right now")
        emit("## Right now")
        emit("")
        emit(right_now if right_now else "_STATE.md has no 'Right now' section._")
        emit("")
        open_q = get_section(state_lines, r"^##\s+Open questions")
        if open_q:
            emit("## Open questions for Jas")
            emit("")
            emit(open_q)
            emit("")
        emit("---")
        emit("")

    # --- One block per project --------------------------------------------------------
    emit("## The projects")
    emit("")

    projects_dir = REPO_ROOT / "projects"
    project_dirs = sorted(
        (p for p in projects_dir.iterdir() if p.is_dir()), key=lambda p: p.name
    ) if projects_dir.is_dir() else []

    for d in project_dirs:
        status_path = d / "STATUS.md"
        emit(f"### {d.name}")
        emit("")
        if not status_path.is_file():
            emit("_No STATUS.md. That is a contract violation — see AGENTS.md._")
            emit("")
            continue

        lines = status_path.read_text(encoding="utf-8", errors="replace").split("\n")

        for line in lines:
            if re.match(r"^\*\*State:\*\*", line):
                emit(re.sub(r"^\*\*State:\*\*\s*", "**State:** ", line))
                emit("")
                break

        for pattern, title in (
            (r"^##\s+What it is", "What it is"),
            (r"^##\s+Where it stands right now", "Where it stands"),
            (r"^##\s+Next step", "Next step"),
        ):
            sec = get_section(lines, pattern)
            if sec:
                emit(f"**{title}.** {sec}")
                emit("")

        emit(f"_Detail: `projects/{d.name}/STATUS.md`_")
        emit("")

    emit("---")
    emit("")
    emit("## Where the rest lives")
    emit("")
    emit("| Question | File |")
    emit("|---|---|")
    emit("| What are the rules for working here? | `AGENTS.md` |")
    emit("| How do I start and end a session? | `AGENTS.md`, and `scripts/session_start.py` |")
    emit("| Why is it built this way? | `docs/DECISIONS.md` |")
    emit("| What breaks if I do the obvious thing? | `docs/LESSONS.md` |")
    emit("| What happened recently? | `workspace/sessions/` and `git log` |")
    emit("| Did someone break the rules? | `scripts/audit_compliance.py`, `workspace/feedback/` |")
    emit("")

    # UTF-8 without BOM: read by the MCP server and by RAG tooling.
    text = "\n".join(out) + "\n"
    OUT_FILE.write_text(text, encoding="utf-8")

    size = OUT_FILE.stat().st_size
    print()
    print(_c("32", "  Wrote workspace/PROJECT-INDEX.md"))
    print(_c("90", f"  {len(project_dirs)} projects, {size} bytes, from commit {head_sha}"))
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
