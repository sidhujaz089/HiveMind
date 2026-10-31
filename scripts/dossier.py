#!/usr/bin/env python3
"""A work item's dossier: every artifact the pipeline has produced for one task, in one list.

    python3 scripts/dossier.py t323                # the dossier, readable
    python3 scripts/dossier.py t323 --json         # the same, for a program
    python3 scripts/dossier.py t323 --write        # also save it as workspace/items/t323/index.json
    python3 scripts/dossier.py t323 --show brief   # print the content of the named artifact(s)

Jas, 2026-09-29 (the Models / Roles / Flow plan, 2a): "Every work item carries a dossier,
workspace/items/<id>/, that accumulates named artifacts as it flows: the task text, notes, the scope,
the brief, the oracle, the RCA summary, the RCA research notes, each attempt's record, each task's
output. Nothing is lost between steps."

POINTERS, NOT COPIES. Every one of those artifacts already has a home that its own script writes and
reads -- the task file, the manager queue's answer, the brief, the ledgers. A dossier that COPIED
them would be a second record of each, and two records of one fact drift (L-046; memory.py makes
the same argument: it points at documents and never copies them). So the dossier is DERIVED: each
collector below finds one kind of artifact for the task, and the dossier is the list, each entry
naming the artifact, where it lives, when, and one line about it. `--write` saves that list under
workspace/items/<id>/ for anything that wants a file; it is regenerated, never edited.

ARTIFACT NAMES are the plan's words, so a Flow link's Carry list can name them: task text, notes,
scope, brief, oracle, preflight review, attempt record, supervision decision, scope defect, review,
manager session, skill draft, assigned-item answers, model-task output. A collector that cannot read
its source says so in the dossier's `unreadable` list; it is never silently empty.
"""
import argparse
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
W = REPO / "workspace"
ITEMS = W / "items"
MAX_ROWS = 50           # per ledger collector; more are COUNTED in the entry, never dropped silently


SKIPPED = []            # unreadable lines and files, SAID in the dossier's `unreadable` list


def _jsonl(p):
    """Every row of a ledger. A ledger that does not exist has no rows; an unreadable line is
    counted into SKIPPED, not dropped in silence."""
    if not Path(p).is_file():
        return []
    rows, bad = [], 0
    with open(p, encoding="utf-8", errors="replace") as f:
        for line in f:
            try:
                rows.append(json.loads(line))
            except ValueError:
                bad += 1
    if bad:
        SKIPPED.append(f"{_rel(p)}: {bad} unreadable line(s) skipped")
    return rows


def _rel(p):
    try:
        return str(Path(p).resolve().relative_to(REPO))
    except ValueError:
        return str(p)


def _mtime(p):
    import time
    try:
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(Path(p).stat().st_mtime))
    except OSError:
        return ""


def art(name, path=None, at="", detail="", ref=None):
    """One artifact: its name, where it lives (a path, or a ledger + the rows' key), when, one line."""
    return {"name": name, "path": _rel(path) if path else None, "at": at, "detail": detail,
            **({"ref": ref} if ref else {})}


# ------------------------------------------------------------------ collectors
def task_file(tid):
    for f in list((W / "tasks").glob(f"{tid}--*.md")) + list((W / "tasks" / "done").glob(f"{tid}--*.md")):
        text = f.read_text(encoding="utf-8", errors="replace")
        body, _, rest = text.partition("## Notes")
        notes = re.findall(r"^\*\*(\d{4}-\d\d-\d\d) · ([^*]+)\*\*", rest, re.M)
        title = re.search(r"^title:\s*(.*)$", text, re.M)
        out = [art("task text", f, _mtime(f), title.group(1) if title else "")]
        if notes:
            out.append(art("notes", f, notes[-1][0], f"{len(notes)} note(s), the newest by {notes[-1][1].strip()}"))
        return out
    return []


def chunks_of(tid):
    """[(chunk, brief path, files, target)] enrolled for this task (its brief says TASK: tid)."""
    out = []
    loop = REPO / "scripts" / "autonomy_loop.sh"
    if not loop.is_file():
        return out
    for line in loop.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^(p\d+-[A-Za-z0-9-]+)\|([^|]+)\|([^|]*)\|([^|]*)", line)
        if not m:
            continue
        b = W / "briefs" / m.group(2)
        if b.is_file() and re.search(rf"^\s*TASK:\s*{tid}\b", b.read_text(encoding="utf-8", errors="replace"), re.M):
            out.append((m.group(1), b, m.group(3).strip(), m.group(4).strip()))
    return out


def scope(tid):
    out = []
    q = W / "run-logs" / "manager-queue"
    for f in sorted([q / "out" / f"{tid}.md"] + list((W / "manager-answers").rglob(f"*{tid}*.md")),
                    key=lambda p: p.stat().st_mtime if p.exists() else 0):
        if f.is_file() and not f.name.endswith(".thinking.md"):
            head = f.read_text(encoding="utf-8", errors="replace")
            step = re.search(r"\((a|b|c|d)\)\s*([A-Z][A-Z ]+)", head)
            out.append(art("scope", f, _mtime(f), f"the Manager's answer{': ' + step.group(0) if step else ''}"))
    p = q / "in" / f"{tid}.txt"
    if p.is_file():
        out.append(art("scope prompt", p, _mtime(p), "what the Manager was asked"))
    return out


def build(tid):
    """brief, oracle, and every ledger row about the task's chunks."""
    out = []
    ch = chunks_of(tid)
    names = {c for c, *_ in ch}
    briefs = {str(b) for _, b, _, _ in ch}
    for c, b, files, target in ch:
        out.append(art("brief", b, _mtime(b), f"{c}: edits {files or '-'}"))
        if target:
            t = REPO / target
            out.append(art("oracle", t if t.exists() else None, _mtime(t) if t.exists() else "",
                           f"{c}: {target}" + ("" if t.exists() else " (not on main yet)")))

    def ledger(name, path, match, line):
        rows = [r for r in _jsonl(path) if match(r)]
        if rows:
            shown = rows[-MAX_ROWS:]
            out.append(art(name, path, shown[-1].get("at", ""),
                           f"{len(rows)} row(s)" + (f" (the newest {MAX_ROWS} summarised)" if len(rows) > MAX_ROWS else "")
                           + "; newest: " + line(shown[-1]),
                           ref={"chunk": sorted(names), "brief": sorted(briefs), "task": tid}))
    ledger("attempt record", W / "dispatch-loop.jsonl",
           lambda r: r.get("chunk") in names and r.get("event") in ("started", "finished"),
           lambda r: f"{r.get('event')} {r.get('kind')} {r.get('chunk')} on {r.get('model')}"
                     + (f", exit {r.get('exit')}" if "exit" in r else ""))
    ledger("preflight review", W / "preflight-objections.jsonl",
           lambda r: str(r.get("brief")) in briefs,
           lambda r: f"{r.get('verdict')} ({Path(str(r.get('brief'))).name})")
    ledger("supervision decision", W / "supervision.jsonl",
           lambda r: r.get("chunk") in names,
           lambda r: f"{r.get('decision') or r.get('action') or '?'} on {r.get('chunk')}")
    ledger("review", W / "merge-accounting.jsonl",
           lambda r: str(r.get("brief")) in briefs,
           lambda r: f"{r.get('branch')}: {r.get('unaccounted')} unaccounted of {r.get('examined')}")
    ledger("scope defect", W / "scope-quality.jsonl",
           lambda r: r.get("task") == tid,
           lambda r: f"{r.get('stage')} {r.get('outcome')} ({r.get('class')})")
    return out


def sessions(tid):
    d = W / "task-sessions" / tid
    fs = sorted(d.glob("*.jsonl")) if d.is_dir() else []
    return [art("manager session", fs[-1], _mtime(fs[-1]), f"{len(fs)} session(s); newest {fs[-1].name}")] if fs else []


def extras(tid):
    out = []
    p = W / "skill-drafts" / f"{tid}.md"
    if p.is_file():
        out.append(art("skill draft", p, _mtime(p), "a proposed skill (skill_change.py)"))
    p = W / "assigned-items" / f"{tid}.json"
    if p.is_file():
        n = len((json.loads(p.read_text(encoding="utf-8")) or {}).get("unusable", []))
        out.append(art("assigned-item answers", p, _mtime(p), f"{n} unusable answer(s) kept"))
    for f in sorted((W / "rca").glob(f"{tid}*.md")) if (W / "rca").is_dir() else []:
        out.append(art("rca report", f, _mtime(f), f.name))
    for f in sorted((W / "task-queues").rglob("*.json")) if (W / "task-queues").is_dir() else []:
        try:
            item = json.loads(f.read_text(encoding="utf-8"))
        except ValueError as e:
            SKIPPED.append(f"{_rel(f)}: unreadable ({e})")
            item = {}
        if item.get("ref") == tid:
            last = (item.get("answers") or [{}])[-1]
            out.append(art("model-task output", f, last.get("at", item.get("at", "")),
                           f"{item.get('task')}: {item.get('outcome') or ('waiting' if f.parent.name != 'done' else '?')}"))
    return out


COLLECTORS = [task_file, scope, build, sessions, extras]


def gather(tid):
    """{task, artifacts: [...], unreadable: [...]} -- newest last within each kind."""
    arts, bad = [], []
    SKIPPED.clear()
    for c in COLLECTORS:
        try:
            arts += c(tid)
        except Exception as e:                                      # noqa: BLE001 -- said, not swallowed
            bad.append(f"{c.__name__}: {type(e).__name__}: {e}")
    return {"task": tid, "artifacts": arts, "unreadable": bad + sorted(set(SKIPPED))}


def content(d, names, limit=None):
    """The text of the named artifacts (for a Carry): file artifacts are read whole; a ledger artifact
    gives its matching rows. `limit` chars per artifact, and a cut is SAID."""
    out = []
    for a in d["artifacts"]:
        if a["name"] not in names or not a.get("path"):
            continue
        p = REPO / a["path"]
        if a.get("ref"):
            # The same three keys the collectors match on: a chunk of this task, one of its briefs, or
            # the task itself (each ledger names rows by one of them).
            ref = a["ref"]
            rows = [r for r in _jsonl(p) if r.get("chunk") in ref["chunk"] or str(r.get("brief")) in ref["brief"]
                    or r.get("task") == ref["task"]]
            text = "\n".join(json.dumps(r) for r in rows)
        else:
            text = p.read_text(encoding="utf-8", errors="replace") if p.is_file() else ""
        if limit and len(text) > limit:
            text = text[:limit] + f"\n[... {len(text) - limit:,} more characters not shown: read {a['path']} ...]"
        out.append(f"=== {a['name']} -- {a['path']} ===\n{text}\n=== end {a['name']} ===")
    return "\n\n".join(out)


def render(d):
    lines = [f"Dossier for {d['task']} ({len(d['artifacts'])} artifact(s))"]
    for a in d["artifacts"]:
        lines.append(f"  {a['name']:22} {a['at'] or '':20} {a['path'] or '-'}")
        if a["detail"]:
            lines.append(f"  {'':22} {a['detail']}")
    for b in d["unreadable"]:
        lines.append(f"  UNREADABLE  {b}")
    return "\n".join(lines)


def index_text(tid):
    """A short dossier index for a model's prompt: what exists for this task and where to read it."""
    d = gather(tid)
    if not d["artifacts"]:
        return ""
    return ("THE TASK'S DOSSIER -- every artifact the pipeline has for it (read what you need):\n"
            + "\n".join(f"  - {a['name']}: {a['path'] or '-'}{' -- ' + a['detail'] if a['detail'] else ''}"
                        for a in d["artifacts"])
            + ("\n  UNREADABLE: " + "; ".join(d["unreadable"]) if d["unreadable"] else "") + "\n")


def write(d):
    out = ITEMS / d["task"]
    out.mkdir(parents=True, exist_ok=True)
    (out / "index.json").write_text(json.dumps(d, indent=1) + "\n", encoding="utf-8")
    return out / "index.json"


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("task")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--show", nargs="+")
    a = ap.parse_args(argv)
    d = gather(a.task)
    if a.show:
        print(content(d, set(a.show)))
        return 0
    if a.write:
        print(f"  wrote {_rel(write(d))}")
    print(json.dumps(d, indent=1) if a.json else render(d))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
