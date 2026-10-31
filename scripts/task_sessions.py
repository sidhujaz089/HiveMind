#!/usr/bin/env python3
"""Task-keyed session logs: every model run on a task, in full, so the next model inherits it.

Jas, 2026-09-24: sessions "linked to a task and any model that works on the task has the full
history ... if overnight a lot of Q4's work is bad and we switch to Q16, it doesn't start from
scratch." Layout: workspace/task-sessions/<task>/<UTC>-<model>.jsonl, one json row per event;
the model is a field of the start row, never a folder. Written to by scripts/ask_q16.py --task.
Contract and oracle: scripts/tests/test_task_sessions.py. Built by gemma4:26b (p48).
"""
import hashlib
import json
import re
import time
import sys
from pathlib import Path

DEFAULT_ROOT = Path(__file__).resolve().parent.parent / "workspace" / "task-sessions"


def _redact(text):
    """Replace every secret-shaped string before it reaches disk (t1495's defect, fixed 2026-10-24).

    These files are served to Chat and read by every model that inherits a task, so a key that
    lands here has left the box. The oracle (scripts/tests/test_task_sessions_redaction.py) has
    been RED and DECLARED in the ceiling since t1495 -- so the leak it describes was live the
    whole time, and its own failure output carried a fake key into the logs. Same marker and the
    same pattern as the action-log writer (console._redact): one rule, one shape, everywhere a
    row is written.
    """
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import check_workspace                      # local: keeps the module importable alone
    pattern = re.compile(check_workspace.SECRET_PATTERN)

    def _one(mt):
        raw = mt.group(0)
        digest = hashlib.sha256(raw.encode("utf-8", "replace")).hexdigest()
        return f"[redacted:secret-pattern] sha256:{digest[:8]} len:{len(raw)}"
    return pattern.sub(_one, text)


def start(task, model, purpose, root=None, brief=None, role=None):
    root = Path(root) if root is not None else DEFAULT_ROOT
    task_dir = root / task
    task_dir.mkdir(parents=True, exist_ok=True)
    now_utc = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    ts_utc = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    safe_model = "".join(c if c.isalnum() or c in "._-" else "_" for c in model)
    path = task_dir / f"{ts_utc}-{safe_model}.jsonl"
    
    counter = 2
    while path.exists():
        path = task_dir / f"{ts_utc}-{safe_model}-{counter}.jsonl"
        counter += 1

    with path.open("w") as f:
        f.write(_redact(json.dumps({
            "kind": "start",
            "task": task,
            "model": model,
            "purpose": purpose,
            "at": now_utc,
            "brief": brief,
            "role": role
        })) + "\n")
    return path


def append(path, kind, **fields):
    path = Path(path)
    now_utc = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    with path.open("a") as f:
        f.write(_redact(json.dumps({"kind": kind, "at": now_utc, **fields})) + "\n")


def history(task, root=None):
    root = Path(root) if root is not None else DEFAULT_ROOT
    task_dir = root / task
    if not task_dir.exists():
        return []

    sessions = []
    for file_path in sorted(task_dir.glob("*.jsonl")):
        rows = []
        with file_path.open("r") as f:
            for line_no, line in enumerate(f, 1):
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    print(f"Error parsing {file_path.name} at line {line_no}", file=sys.stderr)
        
        if rows:
            start_row = rows[0]
            sessions.append({
                "path": str(file_path),
                "model": start_row.get("model"),
                "purpose": start_row.get("purpose"),
                "started": start_row.get("at"),
                "brief": start_row.get("brief"),
                "role": start_row.get("role"),
                "rows": rows
            })
    return sessions



def summarize(task, root=None):
    """One summary per session of `task`, oldest first: what it was given (prompt size), what it
    read, how many tools it called, and what it answered (t401). A line that is not JSON is
    counted in `unreadable`, never dropped silently."""
    import ast
    root = Path(root) if root is not None else DEFAULT_ROOT
    task_dir = root / task
    if not task_dir.is_dir():
        return []
    out = []
    for fp in sorted(task_dir.glob("*.jsonl")):
        s = {"file": fp.name, "model": None, "role": None, "started": None, "purpose": None,
             "prompt_chars": 0, "reads": [], "tool_calls": 0, "answer": None, "done_reason": None,
             "unreadable": 0}
        for line in fp.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                s["unreadable"] += 1
                continue
            k = r.get("kind")
            if k == "start":
                s.update(model=r.get("model"), role=r.get("role"), started=r.get("at"),
                         purpose=r.get("purpose"))
            elif k == "prompt":
                s["prompt_chars"] = len(r.get("text") or "")
            elif k == "tool":
                s["tool_calls"] += 1
                if r.get("tool") == "read_file":
                    try:
                        a = ast.literal_eval(r.get("args") or "{}")
                        path = a.get("path") if isinstance(a, dict) else None
                    except (ValueError, SyntaxError):
                        path = None
                    if path and path not in s["reads"]:
                        s["reads"].append(path)
            elif k == "answer":
                s["answer"], s["done_reason"] = r.get("text"), r.get("done_reason")
        out.append(s)
    return out


def render_html(task, summaries):
    """An HTML fragment of summarize()'s output; every value escaped."""
    import html
    e = lambda v: html.escape(str(v))
    if not summaries:
        return f"<p>No model sessions recorded for {e(task)}.</p>"
    blocks = []
    for s in summaries:
        reads = "".join(f"<li><code>{e(p)}</code></li>" for p in s["reads"]) or "<li>nothing</li>"
        bad = (f"<br><b>{e(s['unreadable'])} unreadable line(s)</b> in {e(s['file'])}"
               if s.get("unreadable") else "")
        blocks.append(
            f'<div class="card"><b>{e(s["started"])}</b> &middot; {e(s["model"])} &middot; '
            f'role {e(s["role"])}<br>{e(s["purpose"])}<br>'
            f'given {e(s["prompt_chars"])} chars &middot; {e(s["tool_calls"])} tool call(s){bad}'
            f'<br>read:<ul>{reads}</ul>answered ({e(s["done_reason"])}):'
            f'<pre>{e(s["answer"])}</pre></div>')
    return "\n".join(blocks)

def digest(task, max_chars=40000, root=None):
    sessions = history(task, root=root)
    if not sessions:
        return ""

    lines = []
    for s in sessions:
        role = s.get("role")
        brief = s.get("brief")
        lines.append(f"=== session {s['started']} by {s['model']}" + (f" [{role}]" if role else "") + (f" on {brief}" if brief else "") + f": {s['purpose']} ===")
        for row in s["rows"]:
            if row["kind"] == "answer":
                lines.append(row.get("text", ""))
        
        last_reasoning = ""
        for row in reversed(s["rows"]):
            if row["kind"] == "reasoning":
                last_reasoning = row.get("text", "")
                break
        if last_reasoning:
            lines.append(f"last reasoning: {last_reasoning}")

    content = "\n".join(lines)
    if len(content) > max_chars:
        keep = content[-max_chars:]
        dropped = len(content) - max_chars
        return f"[TRUNCATED: the first {dropped} characters of older history are not shown]\n{keep}"
    return content


if __name__ == "__main__":
    sys.exit(2)
