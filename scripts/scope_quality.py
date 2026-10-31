#!/usr/bin/env python3
"""Scope quality: every check a scope meets on its way to main, recorded against the MODEL that wrote it.

    python3 scripts/scope_quality.py report [--model M]      the score, per model and prompt version
    python3 scripts/scope_quality.py record --task t --stage S --outcome O [--class C] [--evidence E]
    python3 scripts/scope_quality.py defects                  defects waiting for their RCA
    python3 scripts/scope_quality.py route                    send each to its class's RCA task (top of QUEUE.md)

Jas, 2026-09-28: "Create a data collection plan that can score the quality of a model. Right now it's
Q4 but one day it could be Q16 or a later Qwen ... The data needs to link the model and give a metric
on the quality of its work ... Put this data collection plan in place at all points there is a script
or model check. This is all part of the continuous improvement process."

A SCOPE is one manager answer's chunks block: `<task>@<sha12 of the answer>`. It carries the model
that wrote it (from workspace/task-sessions/<task>/, the file ask_q16 writes per session) and the
prompt version (sha of the prompt it was given). Every check downstream appends one event to
workspace/scope-quality.jsonl: bridge, lint, preflight, build, supervise, merge review, escape.

THE METRIC. first-pass yield = scopes that reached a final verdict with NO defect of a scope class
(the brief or the oracle was wrong) / scopes that reached a final verdict. A harness bug or a bad
implementation is recorded but is not the scope's fault, so it does not lower the score. `n` is
always printed beside it: a yield on 5 scopes is not a yield on 200.

THE LOOP. A defect of any class also writes workspace/scope-defects/<key>.json. The dispatcher runs
each one as an RCA in its priority lane (rca_defect() below), whose fixes land at the top of
QUEUE.md for the Manager, and whose classification is written back here -- so the score uses the
RCA's attribution, not the first guess.

Recording never raises into its caller: a check that works must not start failing because the
ledger could not be written. A write failure is printed to stderr, loudly.
"""
import argparse
import hashlib
import json
import re
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LEDGER = REPO / "workspace" / "scope-quality.jsonl"
DEFECTS = REPO / "workspace" / "scope-defects"
SESSIONS = REPO / "workspace" / "task-sessions"
ANSWERS = REPO / "workspace" / "run-logs" / "scoping-queue" / "out"
LOOP = REPO / "scripts" / "autonomy_loop.sh"
BRIEFS = REPO / "workspace" / "briefs"

# Defect classes that are the SCOPE's fault -- the brief or the oracle was wrong.
SCOPE_CLASSES = {
    "chunks-invalid",       # the block could not be parsed or bridge.validate refused it
    "oracle-green",         # the oracle already passes on main: it proves nothing
    "oracle-no-runner",     # tests with no __main__ block: `python3 <oracle>` runs none of them
    "oracle-crash",         # the oracle cannot run on main for a reason other than the missing change
    "oracle-red-reason",    # red on main, but for its own bug (wrong import path, wrong signature,
                            # a placeholder) -- it can never go green for a correct build
    "oracle-dirty",         # running the oracle changed the repository (touched live state)
    "dirty-leaks-to-tracked-dir",   # ...by writing a NEW file into a tracked data dir: the live-state leak (t699)
    "oracle-gate",          # the oracle file fails the repository's own library checks
    "oracle-wrong",         # a reviewer found it asserts the wrong thing
    "oracle-weak",          # a reviewer found it asserts too little (a wrong build can pass)
    "brief-anchor",         # a line or symbol the brief names is not there
    "oracle-computed-expectation", # the oracle computes its expected values from a repository file the builder is never shown
    "brief-unresolved",     # the text Gemma receives still carries a placeholder (FILL, "confirm
                            # before filing", "[unverified" in SYMBOLS) -- Gemma does not troubleshoot
    "brief-target",         # the implementation's target is neither an oracle in the block nor a suite
    "brief-window",         # the file needs SYMBOLS to fit the window and the brief has none
    "brief-steps",          # a step's now is not the file's text at its where, or steps are missing (2026-10-21)
    "brief-scatter",        # one chunk names more edit sites in a file than the builder can reply with (t450)
    "brief-done-when",      # done_when does not name the target
    "brief-unsatisfiable",  # the builder's preflight objected and the objection was upheld
    "brief-wrong",          # a reviewer found the brief wrong in some other way
}
OTHER_CLASSES = {"harness", "implementation", "model-server", "unclassified", "task-link"}
# A final verdict on a scope: the merge review, or a reviewer reading the scope itself (the
# calibration review of 2026-09-28, and any later audit of scopes that pass the gates).
FINAL = {("merge", "merged"), ("merge", "sent-back"), ("merge", "rejected"), ("review", "ok"),
         ("review", "defect")}


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _sha(text, n=12):
    return hashlib.sha256(str(text).encode()).hexdigest()[:n]


def model_of(task, sessions=None):
    """(model, prompt_version) of the newest manager session on this task, or ("unknown", "")."""
    d = Path(sessions or SESSIONS) / task
    files = sorted(d.glob("*.jsonl")) if d.is_dir() else []
    for f in reversed(files):
        model, prompt = "", ""
        for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                r = json.loads(line)
            except ValueError:
                print(f"scope_quality: unreadable line in {f}", file=sys.stderr)
                continue
            if r.get("kind") == "start":
                model = r.get("model") or model
            elif r.get("kind") == "prompt" and not prompt:
                # THE TEMPLATE, NOT THE TASK (2026-09-29): a hash of the filled-in prompt differs for
                # every task, so it could never group scopes by prompt. manager_prompt stamps its
                # template version; a prompt without the stamp is a custom one, marked as such.
                m = re.search(r"\(prompt template ([0-9a-f]{12})\)", r.get("text", ""))
                prompt = m.group(1) if m else "custom:" + _sha(r.get("text", ""))
        if model:
            return model, prompt
    return "unknown", ""


def scope_of(task, answer=None, model=None, answers=None, sessions=None):
    """The scope record for a task's current answer: {scope, task, model, prompt_version}."""
    p = Path(answer) if answer else Path(answers or ANSWERS) / f"{task}.md"
    text = p.read_text(encoding="utf-8", errors="replace") if p.is_file() else ""
    m, pv = model_of(task, sessions)
    return {"scope": f"{task}@{_sha(text) if text else 'none'}", "task": task,
            "model": model or m, "prompt_version": pv}


def chunk_task(chunk, loop=None, briefs=None):
    """The task a work-list chunk (pNN-...) belongs to, from its brief's TASK: line; '' if none."""
    loop, briefs = Path(loop or LOOP), Path(briefs or BRIEFS)
    if not loop.is_file():
        return ""
    for line in loop.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith(f"{chunk}|"):
            b = briefs / line.split("|")[1]
            if b.is_file():
                m = re.search(r"^\s*TASK:\s*(t\d+)", b.read_text(encoding="utf-8", errors="replace"), re.M)
                return m.group(1) if m else ""
    return ""


def record(stage, outcome, task="", chunk="", defect_class="", evidence="", model=None,
           scope=None, source="", ledger=None, defects=None, **extra):
    """Append one event. A defect_class also writes a defect file for the RCA loop. Never raises."""
    try:
        if not task and chunk:
            task = chunk_task(chunk)
        s = scope or (scope_of(task, model=model) if task else
                      {"scope": f"chunk:{chunk}", "task": "", "model": model or "unknown",
                       "prompt_version": ""})
        row = {"at": _now(), **s, "stage": stage, "outcome": outcome, "chunk": chunk,
               "class": defect_class, "scope_fault": defect_class in SCOPE_CLASSES,
               "evidence": str(evidence), "source": source, **extra}
        if defect_class:
            row["key"] = _sha(f"{row['scope']}|{stage}|{defect_class}|{chunk}|{row['evidence']}")
        led = Path(ledger or LEDGER)
        # ONE DEFECT, ONE ROW (2026-09-30). The key is the defect's identity (scope, stage, class,
        # chunk, evidence); senior_slot re-records every held brief on every tick, and t266 had 130
        # identical "preflight refused" rows, t124/t301/t314/t414 28 each. The defect FILE was already
        # deduplicated by key; the ledger row was not. A repeat is returned, marked, and not appended.
        if defect_class and led.is_file() and f'"key": "{row["key"]}"' in led.read_text(encoding="utf-8", errors="replace"):
            return {**row, "duplicate": True}
        led.parent.mkdir(parents=True, exist_ok=True)
        with open(led, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")
        if defect_class:
            key = row["key"]
            d = Path(defects or DEFECTS)
            d.mkdir(parents=True, exist_ok=True)
            f = d / f"{key}.json"
            if not f.exists() and not (d / "done" / f.name).exists():
                f.write_text(json.dumps(row, indent=1) + "\n", encoding="utf-8")
        return row
    except Exception as e:  # noqa: BLE001 -- reported below, never swallowed
        print(f"scope_quality: COULD NOT RECORD {stage}/{outcome} for {task or chunk}: "
              f"{type(e).__name__}: {e}", file=sys.stderr)
        return None


def rows(ledger=None):
    led = Path(ledger or LEDGER)
    out = []
    if led.is_file():
        for line in led.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                out.append(json.loads(line))
            except ValueError:
                print("scope_quality: an unreadable ledger line", file=sys.stderr)
    return out


def score(events):
    """Per model: first-pass yield over scopes with a final verdict, plus counts by stage/class."""
    by_scope, seen = {}, set()
    for r in events:
        # A defect counted once, however often it was written (the rows before the 2026-09-30 fix).
        if r.get("key") and r.get("stage") != "rca":
            if r["key"] in seen:
                continue
            seen.add(r["key"])
        by_scope.setdefault(r["scope"], []).append(r)
    out = {}
    for sc, evs in by_scope.items():
        # a reclassification written back by the RCA replaces the first guess for that defect
        reclass = {r.get("defect_key"): r.get("class") for r in evs
                   if r.get("stage") == "rca" and r.get("outcome") in ("classified", "cleared")}
        model = next((r["model"] for r in evs if r.get("model") not in (None, "", "unknown")),
                     evs[0].get("model", "unknown"))
        m = out.setdefault(model, {"scopes": 0, "final": 0, "clean": 0, "scope_defects": 0,
                                   "escapes": 0, "by_stage": {}, "by_class": {}})
        m["scopes"] += 1
        fault = False
        for r in evs:
            if r.get("stage") == "rca":
                continue
            cls = reclass.get(r.get("key"), r.get("class"))
            if cls:
                m["by_class"][cls] = m["by_class"].get(cls, 0) + 1
                m["by_stage"][r["stage"]] = m["by_stage"].get(r["stage"], 0) + 1
                if cls in SCOPE_CLASSES:
                    fault = True
                    m["scope_defects"] += 1
            if r.get("stage") == "escape":
                m["escapes"] += 1
        final = any((r.get("stage"), r.get("outcome")) in FINAL for r in evs) or fault
        if final:
            m["final"] += 1
            m["clean"] += not fault
    for m in out.values():
        m["first_pass_yield"] = round(m["clean"] / m["final"], 3) if m["final"] else None
    return out


def report(model=None, ledger=None, since="", by_prompt=False):
    """since: only rows at or after this ISO date. by_prompt: score each (model, prompt version) apart,
    so a prompt change shows as its own line rather than blurring the model's (Jas, 2026-10-19)."""
    rs = [r for r in rows(ledger) if not since or r.get("at", "") >= since]
    if by_prompt:
        rs = [{**r, "model": f"{r.get('model') or 'unknown'} @ prompt {r.get('prompt_version') or '?'}"}
              for r in rs]
    s = score(rs)
    lines = []
    for mdl, m in sorted(s.items()):
        if model and mdl != model and not mdl.startswith(model + " @ "):
            continue
        y = "n/a" if m["first_pass_yield"] is None else f"{m['first_pass_yield']:.0%}"
        lines.append(f"{mdl}\n  first-pass yield {y}  (clean {m['clean']} of {m['final']} with a "
                     f"verdict; {m['scopes']} scopes seen)\n  scope defects {m['scope_defects']}, "
                     f"escapes {m['escapes']}")
        for k, v in sorted(m["by_class"].items(), key=lambda kv: -kv[1]):
            lines.append(f"    {v:4d}  {k}{'' if k in SCOPE_CLASSES else '  (not the scope)'}")
    return "\n".join(lines) or "no scope events recorded yet"


def pending_defects(defects=None):
    d = Path(defects or DEFECTS)
    return sorted(d.glob("*.json")) if d.is_dir() else []


# ------------------------------------------------------------------ the loop: defect -> Manager
RCA_TITLE = "Scope RCA: {cls}"
RCA_BODY = """Every defect of class `{cls}` found by any check on a scope (lint, bridge, preflight, build,
supervision, merge review, escape) is noted on this task by `scope_quality.py route` until the task
closes. Jas, 2026-09-28: "When an error is caught at the bridge point or down stream, the feedback
needs a root cause analysis done and fixes to prevent reoccurance ... mechanically fix upstream
processes ... loop all feedback to the Manager and create tasks in its priority queue."

YOUR JOB (the Manager): apply docs/skills/root-cause-analysis/SKILL.md IN FULL to the defects noted
below, together -- every candidate cause held open or closed on evidence, the thread ledger, a data
collection plan where evidence runs out (Rule 4), and the fix at the most upstream stage (Rule 5).
The upstream stages, earliest first: the manager prompt (scripts/manager_prompt.py), the scope lint
(scripts/scope_lint.py -- a new check with its own oracle case in scripts/tests/test_scope_lint.py),
the bridge (scripts/bridge.py, bridge_trigger.py), the builder's harness. Scope the fix as chunks
(step c): Gemma builds them. Name the model(s) the defects came from; the score is
`python3 scripts/scope_quality.py report`. Precedent: workspace/rca/scope-calibration-2026-09-28.md.

FIRST DEFECT:
{first}
"""
ROUTE_SKIP = {"harness": "the supervisor files a HARNESS task for each harness failure"}


def _defect_line(d):
    return (f"- {d.get('at')} [{d.get('stage')}/{d.get('outcome')}] {d.get('task') or d.get('chunk')} "
            f"({d.get('model')}, scope {d.get('scope')}): {d.get('evidence')}")


def route(defects=None, store=None, ledger=None):
    """Every waiting defect file -> a note on its class's open RCA task, or a new one at the top of
    QUEUE.md. Returns [(key, action)]. Mechanical: no model call, so the dispatcher runs it each tick."""
    if store is None:
        sys.path.insert(0, str(REPO / "projects" / "app-repo"))
        import taskstore as store
    d = Path(defects or DEFECTS)
    out = []
    open_tasks = {}
    for tid in store.read_queue():
        meta, _body = store.find(tid)
        if meta and str(meta.get("title", "")).startswith("Scope RCA: "):
            open_tasks[meta["title"]] = tid
    for f in pending_defects(d):
        try:
            row = json.loads(f.read_text(encoding="utf-8"))
        except ValueError as e:
            print(f"scope_quality: unreadable defect {f.name}: {e}", file=sys.stderr)
            out.append((f.stem, "unreadable"))
            continue
        cls = row.get("class") or "unclassified"
        scope = {k: row.get(k) for k in ("scope", "task", "model", "prompt_version")}
        if cls in ROUTE_SKIP:
            action = f"skipped: {ROUTE_SKIP[cls]}"
        else:
            title = RCA_TITLE.format(cls=cls)
            tid = open_tasks.get(title)
            if tid:
                store.add_note(tid, _defect_line(row), who="scope-quality")
                action = f"noted on {tid}"
            else:
                tid = store.add(title, area="workspace", source="scope-quality", kind="build",
                                body=RCA_BODY.format(cls=cls, first=_defect_line(row)))
                store.move(tid, "top")
                open_tasks[title] = tid
                action = f"filed {tid} at the top of QUEUE.md"
        record("rca", "routed", scope=scope, task=row.get("task", ""), defect_key=row.get("key"),
               evidence=action, source="scope_quality route", ledger=ledger,
               defects=d, routed_class=cls)
        (d / "done").mkdir(parents=True, exist_ok=True)
        f.rename(d / "done" / f.name)
        out.append((row.get("key"), action))
    return out


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("cmd", choices=["report", "record", "defects", "route"])
    ap.add_argument("--model")
    ap.add_argument("--task", default="")
    ap.add_argument("--chunk", default="")
    ap.add_argument("--stage")
    ap.add_argument("--outcome")
    ap.add_argument("--class", dest="cls", default="")
    ap.add_argument("--evidence", default="")
    ap.add_argument("--source", default="cli")
    ap.add_argument("--since", default="")
    ap.add_argument("--by-prompt", action="store_true")
    a = ap.parse_args(argv)
    if a.cmd == "report":
        print(report(a.model, since=a.since, by_prompt=a.by_prompt))
    elif a.cmd == "record":
        if not (a.stage and a.outcome):
            ap.error("record needs --stage and --outcome")
        if a.cls and a.cls not in SCOPE_CLASSES | OTHER_CLASSES:
            ap.error(f"unknown class {a.cls!r}; one of {sorted(SCOPE_CLASSES | OTHER_CLASSES)}")
        r = record(a.stage, a.outcome, task=a.task, chunk=a.chunk, defect_class=a.cls,
                   evidence=a.evidence, source=a.source)
        return 0 if r else 1
    elif a.cmd == "route":
        for key, action in route():
            print(f"{key}\t{action}")
    else:
        for f in pending_defects():
            print(f.name)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
