#!/usr/bin/env python3
"""The script library: a script in this repository cannot run unless it is registered here.

    python3 scripts/library.py list                          # every registered script
    python3 scripts/library.py register <path> "what it does" [--kind tool]
    python3 scripts/library.py unregister <path>
    python3 scripts/library.py run <path> [args...]          # the "tool call"
    python3 scripts/library.py verify [--staged]             # disk and library agree
    python3 scripts/library.py install                      # enable the runtime guard
    python3 scripts/library.py audit [--check ID ...]       # every check on every script

Jas, 2026-09-14: "I want the list maintained structurally. If there are scripts or anything
that runs the script, it needs to go through the library. If something needs a script and it's
not in the library, then it errors out ... like a mini MCP server and each script is like a
tool call. Scripts can't run unless registered."

THE SOURCE OF TRUTH is scripts/library.json. workspace/SCRIPTS.md is generated from it and the
disk on every change, so the list cannot be edited into disagreement with what runs.

WHERE IT IS ENFORCED, and what each point cannot catch:
  1. EVERY PYTHON LAUNCH. scripts/hooks/usercustomize.py is linked into this user's Python
     site-packages, and Python imports it before any script runs. If the script being run is a
     .py file inside this repository and is not registered, it prints why and exits 3. That
     covers cron, systemd services, coder.py suites, models, Claude and a person at a terminal
     alike. It does not see `python3 -c` (no file) or `python3 -m` (a module, not a path), and
     it does not apply to copies outside the repository (coder.py's sandbox), by design.
  2. `library.py run`, the tool-call door. Refuses unregistered .py AND .sh. Cron uses it, so
     shell scripts on a timer are covered too.
  3. THE PRE-COMMIT HOOK runs `verify --staged`: a new or renamed script cannot be committed
     unregistered, and a deleted one cannot leave a stale entry.
  A bare `bash scripts/x.sh` typed by hand is the one path only (3) sees. Say so rather than
  pretend otherwise.

FAILS CLOSED. If library.json is missing or unreadable, repository scripts refuse to run --
except this file, so the library can always be repaired.
"""
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LIB = REPO / "scripts" / "library.json"
MD = REPO / "workspace" / "SCRIPTS.md"
GUARD = REPO / "scripts" / "hooks" / "usercustomize.py"
# "worktrees" AND ".claude" ADDED 2026-10-25 (t2114): `verify` walked the whole repo, so a
# script inside .claude/worktrees/<name>/ -- ANOTHER CHECKOUT of this repository, with its own
# working tree and its own in-flight files -- was reported as unregistered HERE. Two wrongs:
# this checkout's verdict was polluted by somebody else's work in progress, and the repair a
# reader would reach for ("register it") adds an entry for a file this checkout cannot run.
# A coder sandbox is exempt for the same reason: another tree is not this tree.
SKIP_PARTS = {".git", ".venv", "node_modules", "__pycache__", "chat", "build", ".gradle",
              "worktrees", ".claude"}
EXT = {".py", ".sh"}
KINDS = ("tool", "test", "hook", "service", "cron", "lib", "oracle-helper")


def load():
    return json.loads(LIB.read_text(encoding="utf-8"))


def save(d):
    tmp = LIB.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(d, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, LIB)
    write_md(d)


def rel(path):
    p = Path(path)
    p = (Path.cwd() / p).resolve() if not p.is_absolute() else p.resolve()
    return str(p.relative_to(REPO))


def on_disk():
    out = []
    for p in sorted(REPO.rglob("*")):
        r = p.relative_to(REPO)
        if p.suffix in EXT and p.is_file() and not (set(r.parts) & SKIP_PARTS):
            out.append(str(r))
    return out


def describe(path):
    """First line of a module docstring or leading comment -- a starting description."""
    try:
        text = (REPO / path).read_text(errors="replace")
    except OSError:
        return ""
    m = re.search(r'^(?:#!.*\n)?(?:\s*#.*\n)*?\s*(?:"""|\'\'\')\s*(.+)', text)
    if m:
        return m.group(1).strip().rstrip('"').strip()
    for line in text.splitlines()[:8]:
        if line.startswith("#") and not line.startswith("#!") and len(line) > 3:
            return line.lstrip("# ").strip()
    return ""


def guess_kind(path):
    if "/tests/" in f"/{path}":
        return "test" if Path(path).name.startswith("test_") else "oracle-helper"
    if "/hooks/" in f"/{path}":
        return "hook"
    return "tool"


def verify(staged=False):
    d = load()["scripts"]
    disk = set(on_disk())
    if staged:
        out = subprocess.run(["git", "diff", "--cached", "--name-status", "-M"], cwd=REPO,
                             capture_output=True, text=True, timeout=60).stdout.splitlines()
        added, gone = set(), set()
        for line in out:
            parts = line.split("\t")
            st, paths = parts[0], parts[1:]
            if st.startswith(("A", "R", "C")) and Path(paths[-1]).suffix in EXT:
                if not (set(Path(paths[-1]).parts) & SKIP_PARTS):
                    added.add(paths[-1])
            if st.startswith(("D", "R")) and Path(paths[0]).suffix in EXT:
                gone.add(paths[0])
        unregistered = sorted(p for p in added if p not in d)
        stale = sorted(p for p in gone if p in d and not (REPO / p).exists())
        new_blocks = _ratchet()
    else:
        unregistered = sorted(disk - set(d))
        stale = sorted(set(d) - disk)
    for p in unregistered:
        print(f"  UNREGISTERED  {p}\n      python3 scripts/library.py register {p} \"what it does\"")
    for p in stale:
        print(f"  STALE ENTRY   {p} is registered but not on disk\n      python3 scripts/library.py unregister {p}")
    if staged:
        for p, cid, line, code in new_blocks:
            print(f"  NEW {cid} in {p}:{line}  {code[:110]}")
    else:
        new_blocks = []
    if not unregistered and not stale and not new_blocks:
        print(f"  library and disk agree ({len(d)} scripts)")
    return 1 if (unregistered or stale or new_blocks) else 0


def _ratchet():
    """BLOCK findings in staged scripts that HEAD's version of the file did not have."""
    import library_checks
    names = subprocess.run(["git", "diff", "--cached", "--name-only", "--diff-filter=AMR"],
                           cwd=REPO, capture_output=True, text=True, timeout=60).stdout.split()
    out = []
    for p in names:
        if Path(p).suffix not in EXT or set(Path(p).parts) & SKIP_PARTS:
            continue
        staged = subprocess.run(["git", "show", f":{p}"], cwd=REPO, capture_output=True,
                                text=True, timeout=60).stdout
        head = subprocess.run(["git", "show", f"HEAD:{p}"], cwd=REPO, capture_output=True,
                              text=True, timeout=60)
        for c, line, code in library_checks.new_blocks(
                p, head.stdout if head.returncode == 0 else None, staged):
            out.append((p, c, line, code))
    return out


# HOW, PER CHECK -- what a compliance brief must say besides WHY. 2026-09-23: the preflight refused
# p34 and p42 as NOT SATISFIABLE because the brief said "call settings.request" and never showed
# its signature; the model rightly would not invent an interface. And p41 went green by pointing
# at a file nothing writes (failure-schema: check-satisfied-by-dead-path). So every compliance
# brief now names the interface to use AND says the behaviour must survive the fix.
COMPLIANCE_HOW = {
    "model-call-outside-contract": (
        "Build the request with the contract instead of by hand: `import settings` "
        "(projects/local-inference/settings.py -- if this file is outside that directory, first "
        "`sys.path.insert(0, str(<repo root> / \"projects\" / \"local-inference\"))`). Then "
        "`body = settings.request(section, model, messages, **override)`: section is a profile name "
        "('coder', 'chat' or 'supervisor'); messages is a list of {\"role\", \"content\"} dicts; "
        "override is any option you mean to set, e.g. num_predict=4000. It returns a COMPLETE "
        "Ollama chat-endpoint body (model, messages, stream=False, think, keep_alive, options): "
        "send it unchanged. A caller of the generate endpoint converts its prompt to "
        "messages=[{\"role\": \"user\", \"content\": prompt}] and reads the reply from "
        "reply[\"message\"][\"content\"] instead of reply[\"response\"]."),
    "fixed-tmp-path": (
        "Find who WRITES the path and who READS it before changing either. Both ends must name the "
        "same new path; changing only the reader to a path nothing writes makes the check pass and "
        "the feature go dark. For a scratch file used within one function, use "
        "tempfile.mkstemp() and delete it in a finally."),
    "ps-command-column": (
        "Read processes from /proc (or call scripts/lane_status.py lanes()) instead of a ps "
        "command-column listing: /proc/<pid>/cmdline is NUL-separated argv, /proc/<pid>/stat "
        "field 22 is the start time in clock ticks since boot (btime is in /proc/stat)."),
    "swallowed-exception": (
        "An except block must log what it caught (print to stderr, or append to the file's own "
        "log) or re-raise -- a bare `continue`/`pass` is the finding. Keep the same control flow "
        "after logging."),
    "no-timeout": "Pass an explicit timeout= to the call the finding names.",
}
BEHAVIOUR_LINE = ("The code must still DO what it does now: the fix removes the finding without "
                  "changing behaviour, and a change that only silences the check is sent back.")


REQUESTS = REPO / "workspace" / "review-requests.jsonl"
# WORK IN PROGRESS LIMIT for generated compliance chunks. The first audit found 247 findings;
# enrolling them all at once would put ~150 briefs in front of the manager's review queue, which
# reviews one brief at a time on the slowest model. The scheduler tops this up as chunks finish.
COMPLIANCE_WIP = 10


def request_review(path):
    """Ask the auditor model to review one script at its current content. Priority work."""
    import hashlib
    f = REPO / path
    if not f.is_file() or f.suffix not in EXT:
        return False
    try:
        entry = load()["scripts"].get(path)
    except (OSError, ValueError):
        entry = None
    # Tests carry defects on purpose and are not audited; an unregistered file cannot run anyway.
    if entry is not None and entry.get("kind") in ("test", "oracle-helper"):
        return False
    row = {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "path": path,
           "sha256": hashlib.sha256(f.read_bytes()).hexdigest()}
    with REQUESTS.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row) + "\n")
    return True


def _mint_task(chunk, path, check, why, test):
    """Create the task a generated chunk reports into. Returns its id, or None.

    Returns None rather than raising: the chunk and its oracle are still worth having if the
    task store is unavailable, and the brief simply omits the TASK line -- which is exactly
    the state check_workspace already names and counts. Failing the whole enrolment because
    a task could not be filed would trade a reportable gap for no chunk at all.
    """
    try:
        sys.path.insert(0, str(REPO / "projects" / "app-repo"))
        import taskstore
        return taskstore.add(
            title=f"{Path(path).name} fails `{check}` ({chunk})",
            area="workspace", kind="compliance", source="library.py audit --enroll",
            needs="model",
            body=(f"Generated by `library.py audit --enroll` on "
                  f"{time.strftime('%Y-%m-%d')}, so that this chunk's failures have "
                  f"somewhere to land.\n\n"
                  f"FILE: `{path}`\nCHECK: `{check}`\nORACLE: `{test}` (exits 0 when clean)\n"
                  f"CHUNK: `{chunk}`\n\nWHY THE CHECK EXISTS: {why}\n\n"
                  f"This is the target of the brief at "
                  f"`workspace/briefs/brief-{chunk}.txt`. A dispatch failure writes its "
                  f"reason here."))
    except Exception as e:                                          # noqa: BLE001
        print(f"  NOTE: could not file a task for {chunk} ({type(e).__name__}: {e}); "
              f"the brief will declare none and check_workspace will report it as an "
              f"orphan.", file=sys.stderr)
        return None


def enroll_compliance(limit=COMPLIANCE_WIP):
    """Turn BLOCK findings into model chunks, up to the WIP limit. The check IS each oracle."""
    import library_checks
    loop = REPO / "scripts" / "autonomy_loop.sh"
    table = loop.read_text(encoding="utf-8")
    enrolled = re.findall(r"^(p\d+-comply-[\w-]+)\|", table, re.M)
    open_now = 0
    for name in enrolled:
        test = REPO / "scripts" / "tests" / f"test_{name.split('-', 1)[1].replace('-', '_')}.py"
        if test.is_file() and subprocess.run([sys.executable, str(test)], capture_output=True,
                                             timeout=120).returncode != 0:
            open_now += 1
    room = limit - open_now
    if room <= 0:
        print(f"  compliance WIP full ({open_now}/{limit}); nothing enrolled")
        return 0
    s = load()["scripts"]
    have = set(re.findall(r"\|(scripts/tests/test_comply_[\w]+\.py)$", table, re.M))
    nums = [int(n) for n in re.findall(r"^p(\d+)-", table, re.M)]
    nxt = max(nums) + 1 if nums else 100
    added = []
    ev = {c[0]: c[3] for c in library_checks.CHECKS}
    for p in sorted(s):
        if room <= 0:
            break
        f = REPO / p
        if not f.is_file():
            continue
        # THE COMMITTED TEXT, NOT THE WORKING TREE (2026-10-01): a half-finished edit mid-session
        # enrolled two compliance builds (p235, p236) for code that was fixed before it was ever
        # committed. The tick runs every 10 min; only what reached main is a finding.
        r = subprocess.run(["git", "-C", str(REPO), "show", f"HEAD:{p}"], capture_output=True,
                           text=True, timeout=60)
        if r.returncode != 0:
            continue                                # not committed yet: nothing to enrol
        found = library_checks.run(p, r.stdout, severity="block", kind=s[p].get("kind"))
        for check in sorted({c for c, *_ in found}):
            slug = re.sub(r"[^a-z0-9]+", "_", f"{Path(p).stem}_{check}".lower())
            test = f"scripts/tests/test_comply_{slug}.py"
            if test in have or room <= 0:
                continue
            import pnumber                    # shared, never-repeating (2026-09-28)
            nxt = pnumber.claim(REPO, nxt - 1)
            chunk = f"p{nxt}-comply-{slug.replace('_', '-')}"
            lines = ", ".join(str(l) for c, _s, l, _ in found if c == check)
            (REPO / test).write_text(
                f'#!/usr/bin/env python3\n"""{chunk}: {p} must pass `{check}`. Generated by library.py audit --enroll."""\n'
                "import sys\nfrom pathlib import Path\nsys.path.insert(0, str(Path(__file__).resolve().parent))\n"
                f"import compliance_oracle  # noqa: E402\nsys.exit(compliance_oracle.run({p!r}, {check!r}))\n")
            # EVERY GENERATED CHUNK GETS A TASK, because a chunk whose failures cannot reach
            # a task is a chunk nobody can learn from. This generator created none, and that
            # is the larger half of the 32-of-39 orphan count check_workspace reports: a
            # failure on these was recorded in the ledger and nowhere the task could see it.
            # The declared `TASK:` line is the form dispatch_loop.task_of() looks for first;
            # its loose "first tNNN anywhere" fallback exists only for briefs that happen to
            # mention one, and relying on that is how twenty briefs ended up declaring
            # nothing at all (2026-09-16).
            tid = _mint_task(chunk, p, check, ev.get(check, ""), test)
            (REPO / "workspace" / "briefs" / f"brief-{chunk}.txt").write_text(
                (f"TASK: {tid}\n\n" if tid else "")
                + f"ONE file: {p}. Fix the `{check}` finding(s) at line(s) {lines}, and change nothing else.\n\n"
                f"WHY: {ev.get(check, '')}\n\n"
                + (f"HOW: {COMPLIANCE_HOW[check]}\n\n" if check in COMPLIANCE_HOW else "")
                + f"{BEHAVIOUR_LINE}\n\n"
                f"DONE WHEN {test} exits 0 -- it runs the repository's own `{check}` check on this file and "
                f"fails while any finding remains. Do not rename the file or move the code elsewhere.\n")
            s[test] = {"kind": "test", "description": f"compliance oracle: {p} passes {check}",
                       "registered": time.strftime("%Y-%m-%d"), "by": "audit --enroll"}
            added.append(f"{chunk}|brief-{chunk}.txt|{p}|{test}")
            ceiling = REPO / "workspace" / "test-failures-ceiling.json"
            cd = json.loads(ceiling.read_text())
            n = Path(test).name
            if n not in cd["allowed_to_fail"]:
                # THE BUDGET, AT THE WRITER (t1889). Appending here was the one automated way the
                # ceiling could grow, and every reader of the count was downstream of it. A new
                # failing suite may enter only by swapping one out, so this refuses and says what
                # to do -- a number nobody can raise by accident.
                sys.path.insert(0, str(Path(__file__).resolve().parent))
                import check_workspace as _cw
                _budget = _cw.ceiling_budget(cd["allowed_to_fail"], list(cd["allowed_to_fail"]) + [n])
                if _budget["verdict"] == "grew":
                    raise SystemExit(
                        f"REFUSED: {n} would grow the failure ceiling to {len(cd['allowed_to_fail']) + 1}.\n"
                        "A failing suite may enter only by swapping one out (t1889): fix a suite and\n"
                        "remove it from workspace/test-failures-ceiling.json in the same change, or fix\n"
                        "this one before registering it.")
                cd["allowed_to_fail"].append(n)
                cd["allowed_to_fail"].sort()
            cd["_the_three"][n] = "Generated compliance chunk (library.py audit --enroll). Remove when merged."
            ceiling.write_text(json.dumps(cd, indent=1) + "\n")
            nxt += 1
            room -= 1
    if added:
        table = table.rstrip("\n").replace("\nLIST", "\n" + "\n".join(added) + "\nLIST", 1) \
            if "\nLIST" in table else table
        loop.write_text(table if table.endswith("\n") else table + "\n")
        d = load()
        d["scripts"].update({k: v for k, v in s.items() if k not in d["scripts"]})
        save(d)
    print(f"  enrolled {len(added)} compliance chunk(s); {open_now} already open (WIP {limit})")
    return 0


def audit(s, only=None):
    """Every check on every registered script -> workspace/LIBRARY-COMPLIANCE.md."""
    import library_checks
    from collections import defaultdict
    by_check = defaultdict(list)
    for p in sorted(s):
        f = REPO / p
        if not f.is_file():
            continue
        for cid, sev, line, code in library_checks.run(p, f.read_text(encoding="utf-8", errors="replace"),
                                                       only=only, kind=s[p].get("kind")):
            by_check[cid].append((p, line, code))
    md = ["# LIBRARY-COMPLIANCE.md — every registered script against every check", "",
          "**Generated by `python3 scripts/library.py audit`. Checks live in "
          "`scripts/library_checks.py`; add one there and re-run this.**", ""]
    md += ["| check | severity | since | scripts | findings |", "|---|---|---|---:|---:|"]
    for cid, sev, since, ev, _suf, _fn in library_checks.CHECKS:
        hits = by_check.get(cid, [])
        md.append(f"| {cid} | {sev} | {since} | {len({h[0] for h in hits})} | {len(hits)} |")
    for cid, sev, since, ev, _suf, _fn in library_checks.CHECKS:
        hits = by_check.get(cid, [])
        if not hits:
            continue
        md += ["", f"## {cid} ({sev}) — {len(hits)} finding(s)", "", f"_{ev}_", ""]
        md += [f"- `{p}:{line}` `{code}`" for p, line, code in hits]
    out = REPO / "workspace" / "LIBRARY-COMPLIANCE.md"
    out.write_text("\n".join(md) + "\n", encoding="utf-8")
    total = sum(len(v) for v in by_check.values())
    print(f"{out.relative_to(REPO)}: {total} finding(s) across {len(library_checks.CHECKS)} checks")
    for cid, sev, *_ in library_checks.CHECKS:
        print(f"  {sev:5} {cid:28} {len({h[0] for h in by_check.get(cid, [])}):4d} script(s)")
    return 0


def write_md(d):
    s = d["scripts"]
    try:
        cron = subprocess.run(["crontab", "-l"], capture_output=True, text=True, timeout=30).stdout
    except OSError:
        cron = ""
    live = {p: e for p, e in s.items() if e.get("kind") not in ("test", "oracle-helper")}
    md = ["# SCRIPTS.md — the script library",
          "", "**Generated from `scripts/library.json` by `scripts/library.py`. Do not hand-edit.** "
          "A script that is not in the library cannot run.", "",
          f"{len(s)} registered: {len(live)} tools/hooks/services, {len(s) - len(live)} test suites and helpers.",
          "", "| script | kind | on cron | what it does |", "|---|---|:---:|---|"]
    for p in sorted(live):
        e = live[p]
        desc = (e.get("description") or "").replace("|", "\\|")
        md.append(f"| `{p}` | {e.get('kind')} | {'yes' if Path(p).name in cron else ''} | {desc} |")
    md += ["", "## Test suites and oracle helpers", ""]
    md += [f"- `{p}` — {(s[p].get('description') or '')}" for p in sorted(set(s) - set(live))]
    MD.write_text("\n".join(md) + "\n", encoding="utf-8")


def main(argv):
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    cmd, rest = argv[0], argv[1:]
    if cmd == "bootstrap":
        if LIB.exists():
            print("REFUSED: library.json already exists; register scripts one at a time.")
            return 1
        d = {"version": 1, "scripts": {p: {"kind": guess_kind(p), "description": describe(p),
                                           "registered": time.strftime("%Y-%m-%d"),
                                           "by": "bootstrap"} for p in on_disk()}}
        save(d)
        print(f"registered {len(d['scripts'])} existing scripts")
        return 0
    if cmd == "install":
        site = Path(subprocess.run([sys.executable, "-c", "import site;print(site.getusersitepackages())"],
                                   capture_output=True, text=True, timeout=30).stdout.strip())
        site.mkdir(parents=True, exist_ok=True)
        link = site / "usercustomize.py"
        if link.exists() or link.is_symlink():
            if link.is_symlink() and link.resolve() == GUARD.resolve():
                print(f"already installed: {link} -> {GUARD}")
                return 0
            print(f"REFUSED: {link} exists and is not this guard; look at it first.")
            return 1
        link.symlink_to(GUARD)
        print(f"installed: {link} -> {GUARD}")
        return 0
    d = load()
    s = d["scripts"]
    if cmd == "list":
        for p in sorted(s):
            print(f"  {s[p].get('kind', ''):13} {p}  -- {s[p].get('description', '')}")
        print(f"  {len(s)} registered")
        return 0
    if cmd == "register" and len(rest) >= 2:
        p = rel(rest[0])
        if not (REPO / p).is_file() or Path(p).suffix not in EXT:
            print(f"REFUSED: {p} is not a .py or .sh file on disk")
            return 1
        kind = rest[rest.index("--kind") + 1] if "--kind" in rest else guess_kind(p)
        if kind not in KINDS:
            print(f"REFUSED: kind must be one of {', '.join(KINDS)}")
            return 1
        desc = rest[1].strip()
        if not desc or desc.startswith("--"):
            print("REFUSED: a description is required -- a script nobody can describe should not run")
            return 1
        # THE FLOW CHART IS THE SYSTEM OF RECORD (Jas, 2026-10-01: "any permanent script must be
        # represented in the flowchart, and if it is not documented there, it should not be eligible
        # for approval or deployment"). Registering is deployment -- an unregistered script cannot
        # run -- so a permanent script must be on the chart first. Tests are the evidence for a
        # script, not a process, and are exempt. Re-registering a script already here is not new.
        import flow_coverage
        if kind not in flow_coverage.EXEMPT_KINDS and "/tests/" not in p and p not in s \
                and not flow_coverage.on_chart(p):
            print(f"REFUSED: {p} is not on the Flow chart. Put it there first -- say which task it "
                  f"serves and what it does:\n  python3 scripts/flow_change.py helper {p} "
                  f"<task-id[,task-id]> \"what it does\"\nTask ids are on the Flow tab "
                  f"(projects/local-inference/models/FLOW.json).")
            return 1
        import library_checks
        found = library_checks.run(p, (REPO / p).read_text(encoding="utf-8", errors="replace"), kind=kind)
        blocks = [f for f in found if f[1] == "block"]
        for cid, sev, line, code in found:
            print(f"  {sev.upper():5} {cid:26} {p}:{line}  {code[:110]}")
        accept = rest[rest.index("--accept") + 1] if "--accept" in rest else ""
        if blocks and not accept:
            print(f"REFUSED: {len(blocks)} blocking finding(s). Fix them, or register with "
                  f"--accept \"why this is safe\" to record the exception.")
            return 1
        request_review(p)                       # the auditor model reads it as priority work
        s[p] = {"kind": kind, "description": desc, "registered": time.strftime("%Y-%m-%d"),
                "by": os.environ.get("LIBRARY_BY", "claude-code"),
                "checks": {"blocks": len(blocks), "warns": len(found) - len(blocks)}}
        if accept:
            s[p]["accepted"] = accept
        save(d)
        print(f"registered {p}")
        return 0
    if cmd == "unregister" and rest:
        p = rest[0] if rest[0] in s else rel(rest[0])
        if s.pop(p, None) is None:
            print(f"{p}: not registered")
            return 1
        save(d)
        print(f"unregistered {p}")
        return 0
    if cmd == "audit":
        rc = audit(s, only=rest[rest.index("--check") + 1:] if "--check" in rest else None)
        return enroll_compliance() if "--enroll" in rest else rc
    if cmd == "request-review" and rest:
        for path in rest:
            request_review(rel(path) if not path.startswith(("scripts/", "projects/")) else path)
        return 0
    if cmd == "verify":
        return verify(staged="--staged" in rest)
    if cmd == "run" and rest:
        p = rel(rest[0])
        if p not in s:
            print(f"REFUSED: {p} is not registered in scripts/library.json.\n"
                  f"  python3 scripts/library.py register {p} \"what it does\"", file=sys.stderr)
            return 3
        runner = ["bash"] if p.endswith(".sh") else [sys.executable]
        return subprocess.call(runner + [str(REPO / p)] + rest[1:], cwd=str(REPO))
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
