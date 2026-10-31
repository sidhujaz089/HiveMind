#!/usr/bin/env python3
"""The master list of every script in this repository: workspace/SCRIPTS.md.

    python3 scripts/script_census.py          # rewrite SCRIPTS.md
    python3 scripts/script_census.py --json   # the same rows as JSON

Jas asked for it as t236 ("Get a list of all scripts"), and again on 2026-09-14 before having
Gemma review every script for truncation. Nothing listed them all: harness_inventory.py covers
only scripts that touch a model.

DERIVED ON EVERY RUN from the disk and git -- never hand-maintained, so it cannot drift. Each row
says whether ANYTHING references the script (another script, a doc, a hook, a timer, the
crontab): an unreferenced script is either dead or run by hand, and the list should say which
rather than let it read as covered.
"""
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "workspace" / "SCRIPTS.md"
LEDGER = REPO / "workspace" / "script-creations.jsonl"
SKIP_PARTS = {".git", ".venv", "node_modules", "__pycache__", "chat", "build", ".gradle"}
EXT = {".py", ".sh"}


def scripts():
    out = []
    for p in sorted(REPO.rglob("*")):
        rel = p.relative_to(REPO)
        if p.suffix in EXT and p.is_file() and not (set(rel.parts) & SKIP_PARTS):
            out.append(rel)
    return out


class Refused(Exception):
    """A script creation was refused: the 'flow_node' -- or the 'path' -- was blank.

    The flow node is the gate, not a label. A recorder that accepted a blank one would be a
    census, not a gate: 'a session ran an unregistered script' would stay stateable, just
    registered as 'unregistered'. Refusing before the ledger is touched makes that class
    unstateable -- a refusal writes no row.
    """


def record(path, *, purpose, flow_node, task, session, actor, ledger=None):
    """Append the creation of `path` to the ledger and return the row that was written.

    The row is written whole: every field of every creation, never a slice of one. The recorded
    path is a value being recorded, not a file this module owns -- it is never opened, stat'ed
    or required to exist, which is precisely the case the ledger exists for (the finding's
    model-pull script lived in a scratch directory, outside every walk the census can do).
    """
    target = Path(ledger) if ledger else LEDGER
    for field, value in (("path", path), ("flow_node", flow_node)):
        if not str(value).strip():
            raise Refused("refused to record %r: a script creation must carry a %s" % (value, field))
    row = {"path": path, "session": session, "actor": actor,
           "ts": datetime.now(timezone.utc).isoformat(),
           "purpose": purpose, "flow_node": flow_node, "task": task}
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row) + "\n")
    return row


def load_ledger(ledger=None):
    """Every recorded creation, in the order it was written.

    An absent ledger is an empty history -- the first session has none. A line that will not
    parse is REPORTED and skipped: a crash mid-write costs that row and nothing else, and the
    torn row is visible on stderr rather than dropped in silence.
    """
    target = Path(ledger) if ledger else LEDGER
    if not target.exists():
        return []
    rows = []
    for n, line in enumerate(target.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError as e:
            print(f"  NOTE: {target} line {n} unparseable ({type(e).__name__}: {e}) "
                  f"-- torn row skipped, only its own record is lost", file=sys.stderr)
    return rows


def main():
    files = scripts()
    tracked = set(subprocess.run(["git", "ls-files"], cwd=REPO, capture_output=True,
                                 text=True, timeout=60).stdout.split())
    try:
        cron = subprocess.run(["crontab", "-l"], capture_output=True, text=True, timeout=30).stdout
    except OSError:
        cron = ""
    corpus = []
    for p in list(REPO.rglob("*.py")) + list(REPO.rglob("*.sh")) + list(REPO.rglob("*.md")) \
            + list((REPO / "scripts" / "hooks").glob("*")):
        rel = p.relative_to(REPO)
        if not (set(rel.parts) & SKIP_PARTS) and p.is_file():
            try:
                corpus.append((str(rel), p.read_text(errors="replace")))
            except OSError as e:
                print(f"  NOTE: {rel} unreadable ({e}) -- not in the census corpus", file=sys.stderr)
    rows = []
    for f in files:
        name = f.name
        refs = sorted({r for r, text in corpus if r != str(f) and name in text})
        in_cron = name in cron
        lines = len((REPO / f).read_text(errors="replace").splitlines())
        rows.append({"path": str(f), "lines": lines, "tracked": str(f) in tracked,
                     "test": "/tests/" in f"/{f}", "cron": in_cron, "referenced_by": len(refs),
                     "example_ref": refs[0] if refs else None})
    try:
        creations = load_ledger()
    except Exception as e:
        # A ledger the census cannot read is itself a finding: say so and go on, never skip.
        print(f"  NOTE: creation ledger unreadable ({type(e).__name__}: {e}) "
              f"-- census continues without it", file=sys.stderr)
        creations = []
    if "--json" in sys.argv:
        print(json.dumps(rows, indent=1))
        return 0
    live = [r for r in rows if not r["test"]]
    orphan = [r for r in live if not r["referenced_by"] and not r["cron"]]
    md = ["# SCRIPTS.md — every script in the repository",
          "", "**Generated by `scripts/script_census.py`. Do not hand-edit; re-run it.**", "",
          f"{len(rows)} scripts: {len(live)} non-test, {len(rows) - len(live)} test suites. "
          f"{len(orphan)} non-test script(s) are referenced by nothing and not in the crontab -- "
          f"dead, or run only by hand.", "",
          "| script | lines | on cron | referenced by | e.g. |", "|---|---:|:---:|---:|---|"]
    for r in live:
        md.append(f"| `{r['path']}` | {r['lines']} | {'yes' if r['cron'] else ''} | "
                  f"{r['referenced_by'] or '**0**'} | {('`' + r['example_ref'] + '`') if r['example_ref'] else ''} |")
    md += ["", f"## Test suites ({len(rows) - len(live)})", ""]
    md += [f"- `{r['path']}` ({r['lines']} lines)" for r in rows if r["test"]]
    # The blind spot, named: the repo walk can only report what the repo contains, so a creation
    # recorded at the moment it was written and absent from the table is a script living outside.
    repo_paths = {str(f) for f in files}
    outside = [r for r in creations if str(r.get("path")) not in repo_paths]
    md += ["", f"## Script creations recorded at {LEDGER.relative_to(REPO)}", ""]
    for r in outside:
        md.append(f"- `{r.get('path')}` -- flow node {r.get('flow_node')}, task {r.get('task')}, "
                  f"session {r.get('session')}, by {r.get('actor')}: {r.get('purpose')}")
    md += ["", f"{len(outside)} recorded creation(s) have no row in the table above."]
    print(f"{LEDGER.relative_to(REPO)}: {len(creations)} recorded creation(s), "
          f"{len(outside)} outside the repository")
    OUT.write_text("\n".join(md) + "\n")
    print(f"{OUT.relative_to(REPO)}: {len(rows)} scripts, {len(live)} non-test, {len(orphan)} unreferenced")
    return 0


if __name__ == "__main__":
    sys.exit(main())
