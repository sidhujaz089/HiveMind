#!/usr/bin/env python3
"""Build the Corpus tab's Scripts section: what every registered script does, and how it runs.

    python3 scripts/corpus_scripts.py            # classify what is new or changed, write scripts.json
    python3 scripts/corpus_scripts.py --facts    # mechanical facts only, no model

Jas, 2026-09-15: "list what they all do and clarify if any are reoccurring scripts or one time
use. If they are reoccurring please also indicate if they are scheduled to run or just pulled on
demand as they are needed." Delegated: the auditor model writes each description.

HOW A SCRIPT RUNS IS DECIDED BY EVIDENCE FIRST, THE MODEL SECOND.
  scheduled   named in the crontab or a systemd timer            -- read from crontab / systemd
  service     the ExecStart of a systemd service: always running  -- read from systemd
  triggered   run by an event: a git hook, a scheduler job, session start or end -- read from code
  test        a test suite or oracle helper                       -- read from the library
  on-demand / one-time / dead   ONLY these three are the model's call, given the facts above
A model is never asked whether something is on a timer; the crontab answers that exactly, and a
guess there would be a confident wrong answer on the page Jas reads to decide what to trust.

Every model-written field is marked `checked: false` until Claude reviews it. Resumable: keyed by
each file's sha256, so an unchanged script is not re-asked.
"""
import hashlib
import json
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "projects" / "local-inference"))
import roles  # noqa: E402  -- the model behind a role comes from SERVING.json
OUT = REPO / "workspace" / "corpus" / "scripts.json"
# Jas, 2026-09-15: Q16 (bf16) is the script certifier now, not Gemma -- the 37-script
# comparison showed Gemma's on-demand/one-time/dead split was too coarse to trust (it called
# all 37 "on-demand"; Q16 split them into 25/11/1). Resumable-by-sha256 (see `todo` below)
# means this only affects NEW or CHANGED scripts from here on -- it does not retroactively
# relabel the 313 already `does`-populated entries Gemma wrote. Re-describing all of them
# under Q16 needs `--redo` and would tie up Q16 -- the production reviewer -- for a long
# run; do that as a deliberate, separate call, not a side effect of this default.
MODEL = roles.for_job("script_audit", "qwen3.8:27b-mtp-bf16")
FILE_CAP = 24000


def stood_down(model=None):
    """Why the auditor must not be asked right now, or None. THE POST-COMMIT HOOK IS A SIDE DOOR.

    2026-10-14: the hook ran this after the 02:46Z and 05:05Z commits with the Auditor at 0 lanes
    and the ThinkPad already short of RAM; it loaded the Q4 (17.7 GB) on a 15.7 GB machine and
    thinkpad-guard tripped both times, leaving the ThinkPad paused overnight. The hook already
    honoured the scheduler pause (2026-10-06); a stopped role and a paused device are the same
    kind of off switch and must stop this too. Pure lookups; nothing is lost by standing down --
    the next run after the switch is back on describes every changed script."""
    model = model or MODEL
    import scheduler_commands
    import task_routing
    if scheduler_commands.is_paused():
        return "the scheduler is paused"
    if model == MODEL:
        try:
            if task_routing.role_lanes(task_routing.load(), "auditor") == 0:
                return "the Auditor role is at 0 lanes (stopped)"
        except (OSError, ValueError, KeyError) as e:
            return f"the role store is unreadable ({type(e).__name__}); not guessing that the Auditor is on"
    models_dir = REPO / "projects" / "local-inference" / "models"
    try:
        reg = json.loads((models_dir / "REGISTRY.json").read_text(encoding="utf-8"))
        srv = json.loads((models_dir / "SERVERS.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return f"the device map is unreadable ({type(e).__name__}); not guessing where {model} runs"
    server = next((m.get("server") for m in reg.get("base_models", []) if m.get("tag") == model), None)
    device = (srv.get("servers", {}).get(server) or {}).get("device", "gx10") if server else "gx10"
    if device in scheduler_commands.paused_devices():
        return f"{model} runs on the {device}, which is paused"
    return None

# Who runs what, by event. Kept here, next to the rule, so a new trigger is one line.
GENERATED = {"workspace/SCRIPTS.md", "workspace/LIBRARY-COMPLIANCE.md", "workspace/truncation-review.md",
             "workspace/PROJECT-INDEX.md", "workspace/outbox/INDEX.md"}

TRIGGERS = {
    "scripts/hooks/pre-commit": "git pre-commit hook (every commit)",
    "scripts/hooks/commit-msg": "git commit-msg hook (every commit)",
    "scripts/hooks/post-commit": "git post-commit hook (every commit)",
    "scripts/hooks/pre-push": "git pre-push hook (every push)",
    "scripts/dispatch_loop.py": "the scheduler starts it as a job",
    "scripts/session_start.py": "session start",
    "scripts/session_end.py": "session end",
    "scripts/check_workspace.py": "the workspace check (session start and end)",
}


def sh(*a):
    try:
        return subprocess.run(a, capture_output=True, text=True, timeout=60).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def facts():
    import library
    lib = library.load()["scripts"]
    cron = sh("crontab", "-l")
    home = str(Path.home())

    def scripts_in(command):
        """Repository scripts a command line actually RUNS -- whole path tokens, never substrings.
        `library.py run X` credits X: the library is the launcher, not the job."""
        toks = [t.strip("'\"") for t in command.replace("&&", " ").replace(";", " ").split()]
        out = []
        for i, t in enumerate(toks):
            if not t.endswith((".py", ".sh")):
                continue
            t = t.replace("~", home)
            if t.endswith("scripts/library.py") and i + 2 < len(toks) and toks[i + 1] == "run":
                continue
            rel = t[len(str(REPO)) + 1:] if t.startswith(str(REPO)) else t
            out.append(rel)
        return out

    cron_by_script = {}
    for line in cron.splitlines():
        if line.strip() and not line.lstrip().startswith("#"):
            when = " ".join(line.split()[:5])
            for sc in scripts_in(" ".join(line.split()[5:])):
                cron_by_script.setdefault(sc, []).append(when)
    unit_by_script = {}
    for scope in (["systemctl", "--user"], ["systemctl"]):
        names = [l.split()[0] for l in sh(*scope, "list-unit-files", "--type=service", "--no-legend").splitlines() if l.split()]
        for name in names:
            body = sh(*scope, "cat", name)
            execs = [l.split("=", 1)[1] for l in body.splitlines() if l.strip().startswith("ExecStart=")]
            hits = [sc for e in execs for sc in scripts_in(e)]
            if not hits:
                continue
            stem = name[:-len(".service")]
            timer = sh(*scope, "cat", f"{stem}.timer")
            when = [l.split("=", 1)[1] for l in timer.splitlines() if l.strip().startswith(("OnCalendar=", "OnUnitActiveSec=", "OnBootSec="))]
            active = sh(*scope, "is-active", name).strip()
            for sc in hits:
                unit_by_script.setdefault(sc, []).append({"unit": name, "timer": when, "active": active,
                                                          "oneshot": "Type=oneshot" in body})
    texts = {}
    for p in list(REPO.rglob("*.py")) + list(REPO.rglob("*.sh")) + list((REPO / "scripts" / "hooks").glob("*")) \
            + list(REPO.rglob("*.md")):
        r = p.relative_to(REPO)
        if p.is_file() and not ({".git", ".venv", "node_modules", "chat", "__pycache__"} & set(r.parts)):
            try:
                texts[str(r)] = p.read_text(encoding="utf-8", errors="replace")
            except OSError as e:
                # An unreadable file is not a file that references nothing -- say so.
                print(f"  NOTE: could not read {r} ({e}); references from it are not counted", file=sys.stderr)
    rows = {}
    for path, entry in sorted(lib.items()):
        f = REPO / path
        if not f.is_file():
            continue
        name = Path(path).name
        row = {"path": path, "kind": entry.get("kind"), "library_description": entry.get("description", ""),
               "sha256": hashlib.sha256(f.read_bytes()).hexdigest(),
               "lines": len(f.read_text(errors="replace").splitlines())}
        log = sh("git", "-C", str(REPO), "log", "--format=%ad", "--date=short", "--", path).split()
        row.update(commits=len(log), last_changed=log[0] if log else None, first_seen=log[-1] if log else None)
        cron_lines = cron_by_script.get(path, [])
        units_here = unit_by_script.get(path, [])
        # CODE THAT CALLS IT vs DOCUMENTS THAT MENTION IT, and generated catalogues count as neither.
        # The first version counted any file containing the name, and SCRIPTS.md, the compliance
        # report and the truncation review list EVERY script -- so all 176 read as "referenced",
        # and Gemma, shown that count, called nearly everything on-demand.
        refs = sorted(r for r, t in texts.items() if r != path and r not in GENERATED and name in t)
        code_refs = [r for r in refs if not r.endswith(".md") and "/tests/" not in f"/{r}"]
        doc_refs = [r for r in refs if r.endswith(".md")]
        trig = sorted({TRIGGERS[r] for r in refs if r in TRIGGERS})
        row["referenced_by"] = code_refs[:8]
        row["referenced_count"] = len(code_refs)
        row["mentioned_in_docs"] = len(doc_refs)
        if entry.get("kind") in ("test", "oracle-helper"):
            row.update(run="test", run_detail="test suite: run by coder.py's baseline before every production job, and by hand",
                       run_source="library kind")
        elif cron_lines:
            row.update(run="scheduled", run_detail="cron: " + " | ".join(cron_lines), run_source="crontab")
        elif any(u["timer"] for u in units_here):
            u = next(u for u in units_here if u["timer"])
            row.update(run="scheduled", run_detail=f"systemd timer {u['unit'][:-8]}.timer: " + ", ".join(u["timer"]),
                       run_source="systemd")
        elif units_here:
            u = units_here[0]
            state = "always running" if u["active"] == "active" else f"a service, currently {u['active']}"
            row.update(run="service", run_detail=f"{state} as {u['unit']}" + (" (runs once when started)" if u["oneshot"] else ""),
                       run_source="systemd")
        elif trig:
            row.update(run="triggered", run_detail="; ".join(trig), run_source="code that calls it")
        rows[path] = row
    return rows


PROMPT = """You are cataloguing ONE script in a home AI workspace so its owner, who is not a programmer,
knows what it does. Facts already established (do not contradict them):
  path: {path}
  kind in the script library: {kind}
  how it runs (from crontab/systemd/code, may be blank): {run}
  called or imported by {refcount} other script(s), e.g. {refs}
  mentioned in {docs} document(s) (notes, handovers -- history, not usage)
  git: {commits} commit(s), first {first}, last changed {last}

Reply with ONE JSON object and nothing else:
{{"does": "one or two plain sentences: what it does and why someone would run it -- no jargon",
  "run": "on-demand | one-time | dead",
  "why": "one sentence of evidence for that choice"}}
Choose "run" only from those three:
  on-demand  reusable; run by a person or another tool whenever needed
  one-time   did a single job (a migration, backfill, probe, experiment, one-off benchmark) and is not meant to run again
  dead       nothing references it and it no longer fits how the system works
If "how it runs" is already given, still answer "does", and set "run" to "on-demand" (it will be ignored).

=== {path} ===
{body}
"""


def ask(prompt, model=None):
    import settings
    model = model or MODEL
    budget = 1500 if model == MODEL else 4000
    body = settings.request("coder", model, [{"role": "user", "content": prompt}], num_predict=budget)
    # Q16 reasons at LOW -- its own notes: medium and high hung for ten minutes when tested.
    body["think"] = False if model == MODEL else "low"
    # IT TAKES THE MODEL'S LEASE, ONE SCRIPT AT A TIME (t319). It posted straight to Ollama, so the
    # scheduler read the model as free while a 3-hour --redo used it and stacked priority work on
    # top. Per call, not per run: between scripts a priority job can take the slot.
    import modellease
    path = modellease.lease_for(model)
    waited = 0
    while True:
        try:
            lease = modellease.acquire(path, model=model, purpose="corpus script description",
                                       ttl_seconds=settings.timeout_for(budget, model=model) + 60)
            break
        except modellease.Held as e:
            if waited >= 2 * 3600:
                raise
            if waited == 0:
                print(f"  HELD: {e}; waiting for a free slot", flush=True)
            time.sleep(30)
            waited += 30
    try:
        # Through infer (2026-10-06): the auditor is Swift on vLLM, which Ollama does not serve.
        import infer
        d = infer.chat(body, timeout=settings.timeout_for(budget, model=model))
    finally:
        modellease.release(path, lease)
    return (d.get("message") or {}).get("content") or "", d.get("done_reason")


def main():
    import jsonx
    from loudcap import cap
    rows = facts()
    old = json.loads(OUT.read_text()) if OUT.exists() else {"scripts": {}}
    prev = old.get("scripts", {})
    # SECOND OPINION (Jas, 2026-09-15: "delegate those to Q16. I'm curious to see how it performs
    # compared to Gemma"). --model X --prefix q16 --uncalled asks another model about the scripts
    # nothing schedules, triggers or calls, and stores its answers under q16_* beside Gemma's.
    second = sys.argv[sys.argv.index("--model") + 1] if "--model" in sys.argv else None
    prefix = sys.argv[sys.argv.index("--prefix") + 1] if "--prefix" in sys.argv else None
    off = None if "--facts" in sys.argv else stood_down(second if (second and prefix) else MODEL)
    if off:
        print(f"standing down: {off}. Facts only; descriptions wait for the next run.", flush=True)
    if second and prefix and not off:
        todo = [p for p, r in rows.items() if r.get("run") is None and r["referenced_count"] == 0
                and not (prev.get(p, {}).get("sha256") == r["sha256"] and prev.get(p, {}).get(f"{prefix}_run"))]
        print(f"{len(todo)} uncalled script(s) for {second}", flush=True)
        for i, p in enumerate(todo, 1):
            r = rows[p]
            old_r = prev.get(p, {})
            text = (REPO / p).read_text(encoding="utf-8", errors="replace")
            t0 = time.time()
            try:
                raw, why = ask(PROMPT.format(path=p, kind=r["kind"], run="", refcount=0, refs="nothing",
                                             docs=r["mentioned_in_docs"], commits=r["commits"], first=r["first_seen"],
                                             last=r["last_changed"], body=cap(text, FILE_CAP, "script longer than one review")),
                               model=second)
                got = jsonx.one(raw, ["does", "run"])
                if got["status"] == "ok":
                    old_r.update({f"{prefix}_does": str(got["obj"].get("does", "")), f"{prefix}_run": str(got["obj"].get("run", "")),
                                  f"{prefix}_why": str(got["obj"].get("why", "")), f"{prefix}_seconds": round(time.time() - t0)})
                else:
                    old_r[f"{prefix}_error"] = f"unreadable ({got['status']}, {why})"
            except Exception as e:                                  # noqa: BLE001
                old_r[f"{prefix}_error"] = f"{type(e).__name__}: {e}"
            prev[p] = {**r, **{k: v for k, v in old_r.items() if k not in r}}
            print(f"[{i}/{len(todo)}] {p}: {old_r.get(f'{prefix}_run') or old_r.get(f'{prefix}_error')}", flush=True)
            write(rows, prev)
        return 0
    if "--facts" not in sys.argv and not off:
        todo = [p for p, r in rows.items() if r.get("run") != "test"
                and ("--redo" in sys.argv or not (prev.get(p, {}).get("sha256") == r["sha256"] and prev.get(p, {}).get("does")))]
        print(f"{len(todo)} script(s) for {MODEL} to describe", flush=True)
        for i, p in enumerate(todo, 1):
            r = rows[p]
            text = (REPO / p).read_text(encoding="utf-8", errors="replace")
            try:
                raw, why = ask(PROMPT.format(path=p, kind=r["kind"], run=r.get("run_detail", ""),
                                             refcount=r["referenced_count"], refs=", ".join(r["referenced_by"][:4]) or "nothing", docs=r["mentioned_in_docs"],
                                             commits=r["commits"], first=r["first_seen"], last=r["last_changed"],
                                             body=cap(text, FILE_CAP, "script longer than one review")))
                got = jsonx.one(raw, ["does", "run"])
                if got["status"] == "ok":
                    o = got["obj"]
                    r.update(does=str(o.get("does", "")), model_run=str(o.get("run", "")),
                             model_why=str(o.get("why", "")), described_by=MODEL, checked=False)
                else:
                    r.update(does="", model_error=f"unreadable reply ({got['status']}, {why})")
            except Exception as e:                                  # noqa: BLE001
                r.update(does="", model_error=f"{type(e).__name__}: {e}")
            prev[p] = r
            print(f"[{i}/{len(todo)}] {p}: {r.get('model_run') or r.get('run') or '?'}", flush=True)
            if i % 10 == 0:
                write(rows, prev)
    write(rows, prev)
    return 0


def write(rows, prev):
    out = {}
    for p, r in rows.items():
        old = prev.get(p, {})
        if old.get("sha256") == r["sha256"]:
            for k in list(old):
                if (k in ("does", "model_run", "model_why", "described_by", "checked", "model_error")
                        or k.startswith("q16_")) and k not in r:
                    r[k] = old[k]
        # RECOMPUTED ON EVERY WRITE unless the run type came from evidence. The periodic save
        # stamped not-yet-reached scripts "unclassified", and because this only filled a blank,
        # 54 scripts kept that label after Gemma had answered for them (2026-09-15).
        if r.get("run_source") not in ("crontab", "systemd", "code that calls it", "library kind"):
            r["run"] = None
        if r.get("run") is None:
            r["run"] = r.get("model_run") if r.get("model_run") in ("on-demand", "one-time", "dead") else "unclassified"
            r["run_source"] = "model's reading, not checked" if r["run"] != "unclassified" else "not yet described"
            r["run_detail"] = r.get("model_why", "")
        out[p] = r
    OUT.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT.with_suffix(".tmp")
    tmp.write_text(json.dumps({"generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                               "model": MODEL, "scripts": out}, indent=1))
    tmp.replace(OUT)


if __name__ == "__main__":
    sys.exit(main())
