#!/usr/bin/env python3
"""Everything you need to know before touching anything. One command.

AGENTS.md rule 4: you run this, unprompted. Jas does not. "Hi", or anything else
that opens a session, is the trigger -- he should never have to type a command to
make the protocol happen, and a protocol that waits to be invoked is one that
gets skipped.

    python3 scripts/session_start.py
    python3 scripts/session_start.py --skip-audit

Ported from session-start.ps1.
"""

import argparse
import os
import re
import json
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = REPO_ROOT / "scripts"
_TTY = sys.stdout.isatty()


def _c(code, text):
    return f"\033[{code}m{text}\033[0m" if _TTY else text


def section(title):
    print()
    print(_c("36", title))
    print(_c("90", "-" * len(title)))


def coverage_line(path, section_name, body_lines, text):
    """State the bound of a file section this banner printed: what was shown of the file
    as it exists on disk. A slice printed without its bound reads as a complete record,
    and the reader concludes the rest does not exist (t1066, never-truncate-a-record)."""
    m = len(text.encode("utf-8"))
    k = len(text.split("\n"))
    l = len(body_lines)
    n = len("\n".join(body_lines).encode("utf-8"))
    return (f"{path}: printing {section_name} — {n} of {m} bytes, "
            f"{l} of {k} lines on disk; the rest is not shown")


def notes_with(directory, marker):
    """Files whose first 12 lines carry `marker:` in the front matter."""
    out = []
    d = REPO_ROOT / directory
    if not d.is_dir():
        return out
    for f in sorted(d.glob("*.md")):
        if f.name == "README.md":
            continue
        head = "\n".join(f.read_text(encoding="utf-8", errors="replace").split("\n")[:12])
        if re.search(marker, head, re.MULTILINE):
            out.append(f)
    return out


ERROR_SCAN_SHOWN = 5


def _render_error_scan(findings, skipped):
    """The error-scan section as lines (t276). THREE outcomes, never two: findings (a count by kind,
    the newest shown with their evidence VERBATIM, and how many are not shown); examined and clean;
    and could-not-look, when every source was absent -- which must never read as clean."""
    if not findings:
        if skipped:
            return ["  UNEXAMINED: the scanner could not read its sources, so nothing was checked -- "
                    "this is NOT a pass.", "  not read: " + ", ".join(skipped)]
        return ["  clean: every source was read and no failure signature matched."]
    by = {}
    for f in findings:
        by[f["kind"]] = by.get(f["kind"], 0) + 1
    lines = [f"  {len(findings)} finding(s): " + ", ".join(f"{k} {n}" for k, n in sorted(by.items(), key=lambda x: -x[1]))]
    newest = sorted(findings, key=lambda f: str(f.get("at") or ""), reverse=True)[:ERROR_SCAN_SHOWN]
    for f in newest:
        lines.append(f"  [{f['kind']}] {f.get('at') or '-'}  {f['source']} @ {f.get('locator')}")
        lines.append(f"      {f['evidence']}")
    if len(findings) > len(newest):
        lines.append(f"  ... {len(findings) - len(newest)} more not shown here: python3 scripts/error_scan.py --all")
    if skipped:
        lines.append("  not read: " + ", ".join(skipped))
    return lines


_RECONCILE_THREAD = re.compile(r"^  (\S+): (\d+) rows, (\d+) unbacked claim\(s\), (\d+) unverified mention\(s\)\s*$")


def _render_console_reconcile(stdout_text):
    """One line per thread from console_reconcile's stdout (t669). The same stdout carries, per finding,
    the whole reply that made the claim -- 182,739 bytes of it on 2026-10-05, of which this section
    printed ~54,000 every session. The summary lines are the thread; the replies are evidence for
    `python3 scripts/console_reconcile.py`. A stdout with no summary line is SAID, not skipped:
    an empty section would read as 'nothing claimed'."""
    lines = []
    for raw in (stdout_text or "").splitlines():
        m = _RECONCILE_THREAD.match(raw)
        if m:
            thread, rows, unbacked, unverified = m.groups()
            lines.append(f"  {thread}: {unbacked} unbacked claim(s), {unverified} unverified mention(s), "
                         f"{rows} rows")
    if not lines:
        lines.append("  (console_reconcile printed no per-thread summary line -- read it directly)")
    return lines


# THE INDEX OF INDEXES (t670, Jas's session-start proposal item 2): five ways to find something here,
# and nothing said which to reach for when. One line each, so the cost stays constant.
FINDERS = [
    ("memory.py recall", "what we already learned or decided: remembered facts and lessons, by meaning"),
    ("symindex.py find", "where a function or class is defined and who calls it, from the AST"),
    ("skills.py find", "how a kind of task is done here: the skill that covers it, before you start"),
    ("PROJECT-INDEX.md", "what is going on with a project, the whole workspace in one generated file"),
    ("docs/skills/INDEX.md", "every skill by name, when you want to browse rather than search"),
]


def _render_finder_index():
    """The finder index as display lines, one per finder (the _render_error_scan idiom)."""
    width = max(len(name) for name, _when in FINDERS)
    return [f"    {name:<{width}}  {when}" for name, when in FINDERS]


def _error_scan_section():
    """Run the error scanner IN-PROCESS and report it (t276). A scanner that raises is reported with
    its type and message; it never takes session start down."""
    section("0b. Failures nobody reported (scripts/error_scan.py)")
    try:
        import error_scan
        findings = error_scan.scan()
        skipped = error_scan.skipped()
    except Exception as e:                                          # noqa: BLE001 -- reported below
        print(_c("31", f"  error scan BROKEN -- {type(e).__name__}: {e}. Nothing was scanned."))
        return
    for line in _render_error_scan(findings, skipped):
        print(line)
    # WHAT IS NEW OR SPIKING in the logs nobody reads -- the desktop app's and the user journal (t229). A sibling
    # of the error scan, not part of it: the journal needs a subprocess and error_scan's contract forbids one.
    try:
        import log_baseline
        for line in log_baseline.report()[0]:
            print(line)
    except Exception as e:                                          # noqa: BLE001 -- reported, never fatal
        print(_c("31", f"  log baseline BROKEN -- {type(e).__name__}: {e}. The logs were not compared."))


def _work_drift_section():
    """The sweeps that only mean anything when a person looks (t4641, 2026-10-26).

    stale_assignments.py and error_unknowns.py were on no protocol at all: correct, tested, and run by
    nobody. Both WRITE when run for real -- stale_assignments `--apply` reassigns tasks, error_unknowns
    files one Manager item per new error shape -- and neither is a thing to do behind a worker's back at
    session start. So this section runs each in its DRY mode and prints the tool's OWN summary line,
    verbatim: one line each, no parsing, no second source of truth, and nothing moved or filed.

    MEASURED before wiring (2026-10-26): stale_assignments 0.11 s, error_unknowns --dry-run 1.29 s. The
    "~2 minutes" in error_unknowns.py's docstring is stale, which is why this is on the session start
    path rather than a timer.

    A script that cannot run is reported as NOT READ, never as clean (the 0b idiom, t276).
    """
    section("0c. Work drift -- routing and unknown failure shapes (dry runs; nothing moves here)")
    for script, args, hint in (
        ("stale_assignments.py", [],
         "Sweep: python3 scripts/stale_assignments.py --apply"),
        ("error_unknowns.py", ["--dry-run"],
         "File them: python3 scripts/error_unknowns.py"),
    ):
        try:
            r = subprocess.run([sys.executable, str(SCRIPTS / script), *args],
                               capture_output=True, text=True, timeout=180)
        except (OSError, subprocess.TimeoutExpired) as e:
            print(_c("33", f"  {script}: COULD NOT RUN ({type(e).__name__}: {e}) -- it examined "
                           f"nothing, so this is not a clean result"))
            continue
        lines = [l.rstrip() for l in (r.stdout or "").splitlines() if l.strip()]
        if r.returncode != 0 or not lines:
            print(_c("33", f"  {script}: exit {r.returncode}, nothing reported -- not read as clean"
                           + ((": " + (r.stderr or "").strip().splitlines()[-1][:160])
                              if (r.stderr or "").strip() else "")))
            continue
        print(lines[-1])
        print(_c("90", f"    {hint}"))


# The endpoint that emits model-install claims records nothing, so a negative about an
# entry nobody opened -- "isn't a command on the PATH. I didn't look inside it" -- cannot
# be checked once the session ends. This block is the artefact that claim needs: one row
# per entry in the models directory saying what that entry IS. Each definition imports
# what it uses, because the oracle runs every definition sliced out of this file, so a
# helper living outside this block would not exist when it runs.
MODELS_DIR = Path.home() / "models"
INVENTORY_COLUMNS = ("at", "session", "name", "path", "kind", "executable",
                     "on_path", "opened", "sha256", "emitter")
INVENTORY_PATH = (Path(__file__).resolve().parent.parent / "workspace"
                  / "model-inventory.jsonl")


def classify_model_entry(path):
    """What an entry under the models directory is: lstat only, never follow the link."""
    import os
    import stat
    try:
        info = os.lstat(str(path))
    except OSError:
        return "file"
    if stat.S_ISLNK(info.st_mode):
        return "link"
    if stat.S_ISDIR(info.st_mode):
        return "dir"
    if stat.S_ISREG(info.st_mode) and info.st_mode & stat.S_IXUSR:
        return "binary"
    return "file"


def model_on_path(name, entry_path):
    """True only when PATH resolves the name to THIS entry, symlinks followed."""
    import os
    import shutil
    found = shutil.which(name)
    if found is None:
        return False
    return os.path.realpath(found) == os.path.realpath(str(entry_path))


def model_inventory_rows(models_dir, session, emitter, at=None, opened=()):
    """One row per entry scandir yields, dotfiles included; never skips, never raises."""
    import datetime
    import hashlib
    import os
    import stat
    import sys
    if at is None:
        now = datetime.datetime.now(datetime.timezone.utc)
        at = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    opened_names = set(opened)
    rows = []
    try:
        entries = list(os.scandir(str(models_dir)))
    except OSError as exc:
        # An unlistable directory is a finding, not a silently empty inventory.
        print(f"model inventory: cannot list {models_dir}: {exc}", file=sys.stderr)
        return rows
    for entry in entries:
        entry_path = os.path.abspath(entry.path)
        kind = classify_model_entry(entry_path)
        executable = False
        sha256 = None
        try:
            info = os.lstat(entry_path)
        except OSError as exc:
            # The entry vanished mid-scan; it is still an entry we found.
            print(f"model inventory: no stat for {entry_path}: {exc}", file=sys.stderr)
            info = None
        if info is not None and stat.S_ISREG(info.st_mode):
            executable = bool(info.st_mode & stat.S_IXUSR)
            try:
                with open(entry_path, "rb") as handle:
                    sha256 = hashlib.sha256(handle.read()).hexdigest()
            except OSError as exc:
                # An entry whose bytes cannot be read is a finding in the inventory,
                # not a reason to drop the row.
                sha256 = f"unreadable: {type(exc).__name__}: {exc}"
        rows.append({
            "at": at,
            "session": session,
            "name": entry.name,
            "path": entry_path,
            "kind": kind,
            "executable": executable,
            "on_path": model_on_path(entry.name, entry_path),
            "opened": entry.name in opened_names,
            "sha256": sha256,
            "emitter": emitter,
        })
    return rows


def append_model_inventory(rows, inventory_path):
    """Append one JSON object per row; rows from earlier sessions stay byte-identical."""
    import json
    import os
    from pathlib import Path
    target = Path(inventory_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "a", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    return len(rows)


def model_inventory_main(argv=None):
    """Record what is in the models directory at session start: one row per entry."""
    import argparse
    from pathlib import Path
    parser = argparse.ArgumentParser(
        description="Inventory the entries in the models directory.")
    parser.add_argument("--models-dir", default=MODELS_DIR,
                        help="directory to inventory (default: ~/models)")
    parser.add_argument("--out", default=INVENTORY_PATH,
                        help="jsonl file the rows are appended to")
    parser.add_argument("--session", default="", help="session the rows belong to")
    parser.add_argument("--emitter", default="", help="who is making the claim")
    args = parser.parse_args(argv)
    # At session start nothing has been opened, so opened is False for every row.
    rows = model_inventory_rows(Path(args.models_dir), args.session, args.emitter)
    count = append_model_inventory(rows, Path(args.out))
    print(f"model inventory: wrote {count} rows to {args.out}")
    return 0


def _claude_holders(meta):
    """The roles that would work this task -- with "claude" among them, it is waiting on Claude.

    t9540: `needs:` holds a TIER now (technical / vision / rework / production), so comparing the field to
    the string "claude" hid every technical task from the list below. The tier resolves to its holders
    first, and a legacy role key still names itself.
    """
    import role_store
    import taskstore
    value = taskstore.assignee(meta.get("assigned") or meta.get("needs"))
    return set(role_store.tier_roles(value)) | ({value} if value in ("claude", "technical") else set())


def claude_review_section(now=None, tick=None, tasks=None):
    """Lines for the session-start report: every open task assigned to Claude, grouped by why it waits."""
    import re as _re
    import time as _time
    sys.path[:0] = [str(REPO_ROOT / "projects" / "app-repo"), str(REPO_ROOT / "scripts")]
    import taskstore
    now = now or _time.time()
    reason_of = {}                                   # task -> the scheduler's reason for its chunk
    try:
        d = json.loads((tick or REPO_ROOT / "workspace" / "run-logs" / "dispatch-tick-latest.json")
                       .read_text(encoding="utf-8"))
        loop = {l.split("|")[0]: l.split("|")[1] for l in
                (REPO_ROOT / "scripts" / "autonomy_loop.sh").read_text(encoding="utf-8").splitlines()
                if _re.match(r"^p\d+-", l)}
        for x in d.get("skipped") or []:
            ch, why = (x.get("chunk"), x.get("why")) if isinstance(x, dict) else (x[0], x[1])
            b = REPO_ROOT / "workspace" / "briefs" / str(loop.get(ch, ""))
            m = _re.search(r"^\s*TASK:\s*(t\d+)", b.read_text(encoding="utf-8"), _re.M) if b.is_file() else None
            if m:
                reason_of.setdefault(m.group(1), str(why))
    except (OSError, ValueError) as e:
        reason_of = {}
        yield _c("33", f"  (the scheduler's last tick is unreadable -- {type(e).__name__}; reasons not shown)")
    groups = {}
    for meta, *_rest in (tasks if tasks is not None else taskstore.load_all().values()):
        if meta.get("status") in ("done", "cancelled") or "claude" not in _claude_holders(meta):
            continue
        why = reason_of.get(meta.get("id"), "")
        kind = ("DELIVERED -- merge review (Claude is the gate, D-34)" if why.startswith("DELIVERED") else
                "PARKED -- a build came to nothing" if why.startswith("PARKED") else
                "HELD -- a preflight objection" if why.startswith("HELD FOR CLAUDE") else
                "asked of Claude in a note, or a done-check")
        groups.setdefault(kind, []).append(meta)
    total = sum(len(v) for v in groups.values())
    if not total:
        yield _c("90", "  Review tab, Claude's section: empty")
        return
    yield _c("33", f"  Review tab, Claude's section: {total} task(s) waiting on you -- review them this session")
    order = ["DELIVERED -- merge review (Claude is the gate, D-34)", "HELD -- a preflight objection",
             "PARKED -- a build came to nothing", "asked of Claude in a note, or a done-check"]
    for kind in order:
        items = groups.get(kind) or []
        if not items:
            continue
        items.sort(key=lambda m: str(m.get("created", "")))
        oldest = str(items[0].get("created", ""))[:10]
        yield _c("33", f"    {len(items):4}  {kind}  (oldest {oldest})")
        for m in items[:3]:
            yield _c("90", f"          {m.get('id')}  {str(m.get('title', ''))[:70]}")


IDENTITY = REPO_ROOT / "workspace" / "identity.json"


def identity_key(env=None):
    """Which DSH instance this is: 'thinkpad-dsh', 'gx10-dsh', or '' when it cannot be told.

    DSH_HOME, because nothing else can answer it. `hostname` is the GX10 in both sessions (the
    ThinkPad instance works through SSH), and the workspace is the same path in both. The harness
    exports DSH_HOME into the shell it spawns: a Windows home ('C:...') is the ThinkPad, a POSIX one
    ('/home/...') is the GX10. A blank or unfamiliar value returns '' so the caller can be loud
    rather than print the wrong instance's facts.
    """
    env = os.environ if env is None else env
    home = (env.get("DSH_HOME") or "").strip()
    if not home:
        return ""
    if home[:2].upper() == "C:" or "\\" in home:
        return "thinkpad-dsh"
    if home.startswith("/"):
        return "gx10-dsh"
    return ""


def resolve_identity(env=None, path=None):
    """(key, entry, problem). Never guesses: a problem string means print nothing but the problem."""
    path = Path(path) if path else IDENTITY
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return "", {}, f"workspace/identity.json unreadable ({type(e).__name__}: {e})"
    key = identity_key(env)
    if not key:
        return "", {}, ("cannot tell which DSH instance this is: DSH_HOME is "
                        f"{os.environ.get('DSH_HOME')!r} (expected a Windows home for the ThinkPad "
                        "or a POSIX one for the GX10)")
    entry = (doc.get("instances") or {}).get(key)
    if not entry:
        return key, {}, (f"workspace/identity.json has no entry for {key!r} -- add one rather than "
                         "copying another instance's facts")
    return key, entry, ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-audit", action="store_true")
    args = ap.parse_args()

    print()
    print("=" * 62)
    print(f" SESSION START — {REPO_ROOT}")
    print("=" * 62)

    # --- 0a. Who this session is (data, not memory) ---------------------------------
    # Jas, 2026-10-26: "This happens every session where I need to tell you who you are."
    # Both DSH instances run their commands against THIS repository, so a single-instance file told
    # one of them a lie -- the GX10 DSH read "ThinkPad DSH" and correctly refused it. The entry is
    # resolved by DSH_HOME, not by hostname: for the ThinkPad instance the harness is on the ThinkPad
    # but every command lands here, so `hostname` answers HiveMind in BOTH sessions.
    key, ident, problem = resolve_identity()
    if problem:
        print(_c("31", f"  IDENTITY UNRESOLVED: {problem}"))
    else:
        win = ident.get("context_window_tokens")
        print(f"  I am: {ident.get('instance', '?')} -- {ident.get('model', '?')}   [{key}]")
        print(f"  Every command runs on: {ident.get('shell', '?')}")
        print("  Context window: "
              + (f"{win:,} tokens -- {ident.get('context_window_from', 'configured, not measured')}"
                 if isinstance(win, int) else
                 f"not recorded for {key} -- {ident.get('context_window_source', '')[:80]}..."))

    # --- 0. Install git hooks (idempotent) ------------------------------------------
    # core.hooksPath is per-clone git config, not a tracked file. A fresh clone or a
    # tree copy without .git loses it silently. The check in section 1 reports the gap,
    # but by then this session's commits already went through unenforced. Install
    # before the check so the check passes and the hooks are live for this session.
    hooks_installer = SCRIPTS / "install_hooks.py"
    if hooks_installer.is_file():
        r = subprocess.run([sys.executable, str(hooks_installer)],
                           capture_output=True, text=True, timeout=120)
        if r.returncode == 0:
            print(_c("90", "  git hooks: installed (pre-commit, commit-msg, pre-push)"))
        else:
            print(_c("31", "  git hooks: install FAILED — commit format, attribution,"))
            print(_c("31", "  and the secret scan are NOT enforced in this clone."))
            print(_c("31", "  Run manually: python3 scripts/install_hooks.py"))

    _error_scan_section()
    _work_drift_section()
    # t413: the Manager's citation rate, mechanically -- a rising invented rate is the signal.
    try:
        r = subprocess.run([sys.executable, str(SCRIPTS / "citation_audit.py")], capture_output=True,
                           text=True, timeout=120)
        print((r.stdout or r.stderr).strip() + "   (details: python3 scripts/citation_audit.py --list)")
    except (OSError, subprocess.TimeoutExpired) as e:
        print(f"  citation audit not run: {e}")

    # --- 1. Invariants ---------------------------------------------------------------
    section("1. Does the workspace still match its documentation?")
    check_failed = subprocess.run(
        [sys.executable, str(SCRIPTS / "check_workspace.py"), "--quick"],
        timeout=900,
    ).returncode != 0

    # --- 2. Did the previous worker follow the rules? --------------------------------
    audit_failed = False
    if args.skip_audit:
        section("2. Compliance audit — SKIPPED")
    else:
        section("2. Did whoever worked here last follow the contract?")
        audit = SCRIPTS / "audit_compliance.py"
        if audit.is_file():
            audit_failed = subprocess.run([sys.executable, str(audit)], timeout=900).returncode != 0
        else:
            print(_c("33", "  audit_compliance.py not found — nothing audited."))

    # --- 3. Waiting for you ----------------------------------------------------------
    section("3. Waiting for you")

    inbox_dir = REPO_ROOT / "workspace" / "inbox"
    inbox = sorted(
        f for f in inbox_dir.glob("*.md") if f.is_file() and f.name != "README.md"
    ) if inbox_dir.is_dir() else []
    if inbox:
        print(_c("33", f"  {len(inbox)} message(s) from Claude Chat in workspace/inbox/:"))
        print(_c("33", "  Retire one with: python3 scripts/sweep.py --done <file>"))
        print(_c("33", "  Capture gaps:    python3 scripts/check_captures.py"))
        print(_c("33", "  Stale reads:     python3 scripts/check_read_staleness.py --since <date>"))
        print(_c("33", "  Mutation harness: python3 scripts/mutate.py  (slow; run when checks change)"))
        print(_c("33", "  It refuses to delete anything not already committed."))
        for m in inbox:
            print(f"    {m.name}")
        print(_c("90", "  Act on each, then DELETE it — Chat cannot."))
    else:
        print(_c("90", "  Inbox empty."))

    # THE HOURLY CHECKS (t1153): what is failing now, as the last run found it -- not at session end.
    try:
        last = json.loads((REPO_ROOT / "workspace" / "hourly-checks.jsonl").read_text(
            encoding="utf-8").strip().splitlines()[-1])
        if last.get("failing"):
            print(_c("31", f"  HOURLY CHECKS ({last['at']}, {last.get('head')}): {len(last['failing'])} failing -- "
                           f"each has a task at the front of the queue:"))
            for n in last["failing"]:
                print(_c("31", f"    {n}"))
        else:
            print(_c("90", f"  Hourly checks: nothing failing at {last['at']} ({last.get('skipped') or last.get('sweep', '')})."))
    except (OSError, ValueError, IndexError, KeyError) as e:
        print(_c("90", f"  Hourly checks: no run recorded ({type(e).__name__})."))

    # WHAT YOUR ROLE MAY DO, AND WHAT IS ASSIGNED TO IT (plan 3b, Jas 2026-09-29): "session_start.py
    # and the DSH startup print the role's enabled tasks." Claude Code and the DeepSeek Harness both
    # start here, and this script does not know which one is running, so it prints every person and
    # agent role; your commit trailer decides which list binds you (role_gate.py enforces it on
    # merges). Never raises: a startup that stops on a role-store typo is worse than one without this.
    try:
        sys.path.insert(0, str(REPO_ROOT / "projects" / "app-repo"))
        import role_store as _rs
        import taskstore as _ts
        _roles = _rs.load()["roles"]
        _open = [m for m, _b in _ts.load_all().values() if m.get("status") not in ("done", "resolved")]
        print()
        print("  Roles that are people or agents -- your commit trailer says which one you are:")
        for _k, _r in _roles.items():
            if _r.get("kind", "model") == "model":
                continue
            _tasks = " > ".join(t["task"] for t in _r.get("tasks", [])) or "(no tasks: may not merge)"
            _n = sum(1 for m in _open if m.get("assigned") == _k)
            _tr = ", ".join(_r.get("trailers", [])) or "-"
            print(f"    {_r.get('name', _k):17} {_tasks}")
            print(_c("90", f"    {'':17} trailers: {_tr}; {_n} open task(s) assigned"))
        print(_c("90", "  Check yours: python3 scripts/role_gate.py who <your Agent: trailer>"))
    except Exception as _e:                                         # noqa: BLE001
        print(_c("33", f"  role task lists not shown: {type(_e).__name__}: {_e}"))

    # DID QWEN ACTUALLY DO WHAT IT TOLD JAS IT DID? 2026-09-14.
    #
    # On 2026-09-13/14 Qwen reported 33 dispatches that never happened -- "Dispatched pid
    # 153120", "I have successfully used the note_task tool" -- against 4 real ones. Jas acted
    # on those reports, could not find the results, and went looking for a bug in his task list.
    # The system prompt already said "do not report a write you did not verify" and it did not
    # hold, because a rule in prose is a suggestion. This is the fixture.
    #
    # It runs HERE rather than being a command someone remembers, which is the whole gate 3/6
    # standard: a script referenced only by its own __main__ is built, not called.
    try:
        rc = subprocess.run([sys.executable, str(REPO_ROOT / "scripts" / "console_reconcile.py")],
                            capture_output=True, text=True, timeout=120,
                            stdin=subprocess.DEVNULL, cwd=str(REPO_ROOT))
        if rc.returncode == 1:
            print()
            print(_c("31", "  QWEN CLAIMED ACTIONS IT DID NOT TAKE -- console_reconcile.py"))
            for line in _render_console_reconcile(rc.stdout):
                print(_c("31", line))
            print(_c("90", "  Full detail: python3 scripts/console_reconcile.py"))
            print(_c("90", "  Treat every write Qwen reported in those turns as NOT DONE."))
        elif rc.returncode == 0:
            print(_c("90", "  Qwen's claimed dispatches all match real calls "
                           "(console_reconcile.py)."))
        else:
            # A checker that failed is not a checker that passed (L-146).
            print(_c("33", f"  console_reconcile.py could not run (exit {rc.returncode}): "
                           f"{(rc.stderr or rc.stdout or '').strip()}"))
    except Exception as e:                                          # noqa: BLE001
        print(_c("33", f"  console_reconcile.py could not run: {type(e).__name__}: {e}"))

    # What the local model did while nobody was watching. Branches waiting for review are
    # the thing most likely to be missed -- they are not in the inbox, not in the queue, and
    # a session that does not know they exist will redo the work.
    runs = REPO_ROOT / "workspace" / "agent-runs.jsonl"
    if runs.is_file():
        rows = [json.loads(l) for l in runs.read_text(encoding="utf-8").splitlines()
                if l.strip()]
        recent = rows[-20:]
        acc = [r for r in recent if r["outcome"] == "ACCEPTED"]
        if recent:
            print(_c("33", f"  local agent: {len(recent)} recent run(s), "
                           f"{len(acc)} accepted"))
            for r in acc:
                print(_c("33", f"    branch {r.get('branch')} — {r['task']} "
                               f"awaiting review"))
            bad = [r for r in recent if r["outcome"] != "ACCEPTED"]
            if bad:
                print(_c("90", f"    {len(bad)} not accepted; see the note on each task"))

        # t154. The gate between "the model built it" and "Jas can merge it" was the one
        # stage with no surface, so ten finished items sat unvetted across four sessions
        # without anything saying so. Derived from the two ledgers, never stored.
        try:
            sys.path.insert(0, str(Path(__file__).resolve().parent))
            import reviewq
            q = reviewq.pending()
            if q:
                print(_c("33", f"  awaiting CLAUDE's review: {len(q)} item(s) — "
                               f"python3 scripts/reviewq.py"))
                for r in q[:5]:
                    print(_c("33", f"    {r.get('task','?')} {r.get('at','?')} "
                                   f"{r.get('served','?')}"))
                print(_c("90", "    Claude is the gate (D-34). Vet by RUNNING it, then merge."))
            else:
                print(_c("90", "  awaiting Claude's review: nothing unvetted"))
        except Exception as e:
            print(_c("31", f"  review queue unavailable: {e}"))

    # CLAUDE'S SECTION OF THE REVIEW TAB (Jas, 2026-10-13: "If things are getting missed on the review
    # tab, perhaps on the session start routine, we need to add a review of that tab for yourself").
    # On 2026-10-13 107 tasks sat there -- 59 parked builds, 48 preflight objections, 11 delivered
    # branches -- and no session start mentioned them. Read live, never stored.
    try:
        for line in claude_review_section():
            print(line)
    except Exception as e:                                          # noqa: BLE001 -- said, not swallowed
        print(_c("31", f"  Claude's Review section unreadable: {type(e).__name__}: {e}"))

    # A compaction is not a pause, it is a soft session end -- the summary is not the
    # conversation, and the session that came out of the one on 2026-08-26 drifted off the
    # task it had been running. Arm the guard so this session gets told BEFORE it happens.
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import context_watch
        # newest_session() is an MTIME GUESS, not an identity. It is right here only
        # because this script runs as a tool call inside the live session, so our own
        # transcript is the one being appended to. It was NOT right in the hook, which
        # runs on the first prompt before this session's transcript exists -- every new
        # session was handed the previous session's 896,527 and told to wind down
        # (2026-09-02). So refuse a transcript that nothing has written to recently:
        # a stale file is somebody else's session, and no number beats no number.
        _p = context_watch.newest_session()
        if _p and time.time() - _p.stat().st_mtime > 120:
            print()
            print(_c("33", "  context: unknown — the newest transcript is not this "
                           "session's. Not guessing."))
            _p = None
        if _p:
            _tok, _turns = context_watch.occupancy(_p)
            _pct = _tok / context_watch.LIMIT * 100
            print()
            # The TURN COUNT is here so a misfire is visible without investigating: a big
            # number over a handful of turns is not your session (L-148).
            print(_c("90" if _pct < 60 else "33",
                     f"  context {_tok:,} / {context_watch.LIMIT:,} = {_pct:.0f}%"
                     f"  ({_turns} turns)"))
            # DO NOT print an instruction to arm a watcher here. This block used to, and
            # it kept saying so for hours after the watcher was retired for the hook --
            # L-122 landed in settings.json, LESSONS.md and PROJECT-INDEX.md but not in
            # THIS file, which is the one every session reads first. The next session did
            # exactly what it was told, in the nohup form the docstring warns against, and
            # an orphan watcher from the previous session was still running underneath.
            # So: verify the mechanism that actually exists, and say so only if it is gone.
            _hook_ok = False
            try:
                import json as _json
                _s = _json.loads((REPO_ROOT / ".claude" / "settings.json").read_text())
                _hook_ok = any(
                    "context_watch.py" in _h.get("command", "")
                    for _e in _s.get("hooks", {}).get("UserPromptSubmit", [])
                    for _h in _e.get("hooks", []))
            except Exception as e:
                print(f"  NOTE: settings.json unreadable ({e}) -- hook not confirmed", file=sys.stderr)
            if _hook_ok:
                print(_c("90", "  wind-down guard: UserPromptSubmit hook, active. Nothing to arm."))
            else:
                print(_c("31", "  WIND-DOWN GUARD IS NOT INSTALLED — the UserPromptSubmit hook"))
                print(_c("31", "  is missing from .claude/settings.json. A compaction will"))
                print(_c("31", "  arrive unannounced. Restore the hook; do NOT start a watcher,"))
                print(_c("31", "  both long-running forms failed silently (L-122)."))
    except Exception as e:
        print(_c("31", f"  context guard unavailable: {e}"))

    # The stage after the review queue. t154 surfaced what CLAUDE has not vetted; nothing
    # surfaced what JAS had already answered, because he writes notes on task files and
    # reviewq is derived from two ledgers he never touches. On 2026-08-26 he approved four
    # branches and asked a question, and the session told him all five were still awaiting
    # his decision -- he had to ask "do you not see my responses?". Eight older replies
    # turned out to have been sitting unanswered since 2026-08-21.
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import jas_replies
        pending_jas = jas_replies.waiting()
        if pending_jas:
            print()
            print(_c("33", f"  JAS REPLIED and nobody answered: {len(pending_jas)} task(s) — "
                           f"python3 scripts/jas_replies.py"))
            for r in pending_jas[:8]:
                print(_c("33", f"    {r['task']:6} {r['at']}  {r['said'][:70]}"))
            print(_c("90", "    he is waiting on you; answering or acting both end the wait"))
    except Exception as e:
        print(_c("31", f"  Jas-reply check unavailable: {e}"))

    # TASKS WHOSE WAKE DATE HAS ARRIVED (t256).
    #
    # Fifteen tasks carry a reply of the form "let's look at this in November" and nothing in
    # this repository resurfaced any of them. A deferral lived only as prose inside a note, so
    # it had two possible endings and both were bad: the task sat in the active queue looking
    # like work nobody was doing, or it was moved aside and never seen again. On 2026-09-10 it
    # was doing the first, in JAS'S OWN queue, which is what made his list unclearable.
    #
    # A date in the front matter and one sort. Not a scheduler.
    try:
        import re as _re
        from datetime import date as _date
        today = _date.today().isoformat()
        due = []
        for f in sorted((Path(__file__).resolve().parent.parent / "workspace" / "tasks")
                        .glob("t*.md")):
            # The front matter, whole -- a fixed-width head was a silent cut (library check).
            parts = f.read_text(encoding="utf-8", errors="replace").split("---", 2)
            head = parts[1] if len(parts) > 2 else ""
            m = _re.search(r"^wake:\s*(\d{4}-\d{2}-\d{2})\s*$", head, _re.M)
            if not m:
                continue
            if m.group(1) <= today:
                st = _re.search(r"^status:\s*(.*)$", head, _re.M)
                if st and st.group(1).strip() == "done":
                    continue
                ti = _re.search(r"^title:\s*(.*)$", head, _re.M)
                tid = _re.search(r"^id:\s*(.*)$", head, _re.M)
                due.append((m.group(1), tid.group(1).strip() if tid else f.name[:5],
                            ti.group(1).strip() if ti else f.name))
        if due:
            print()
            print(_c("36", f"  WAKE: {len(due)} task(s) asked to be looked at by now"))
            for when, tid, title in sorted(due)[:8]:
                print(_c("36", f"    {tid:6} due {when}  {title[:60]}"))
            print(_c("90", "    set by a past session or by Jas; clear it by acting or "
                           "moving the date"))
    except Exception as e:
        print(_c("31", f"  wake-date check unavailable: {e}"))

    # The other direction. Until 2026-08-25 nothing tracked whether Chat had ever READ a
    # Code-to-Chat message, and two went unseen for days -- one of them the answer to a
    # question a later session then spent six bridge calls re-deriving.
    r = subprocess.run([sys.executable, str(SCRIPTS / "outbox_index.py")],
                       capture_output=True, text=True, timeout=300)
    for line in (r.stdout or "").splitlines():
        if line.strip():
            print(_c("33" if "unacknowledged" in line else "90", line))

    # Notes typed on the phone, from projects/app-repo/feedback.py. These are the ONLY
    # channel by which a test result or a bug found in real use reaches a session -- Jas is
    # not going to retype them, so a session that does not read this folder silently discards
    # the entire testing loop.
    fb_dir = REPO_ROOT / "workspace" / "app-feedback"
    phone = sorted(
        f for f in fb_dir.glob("*.md") if f.is_file() and f.name != "README.md"
    ) if fb_dir.is_dir() else []
    print()
    if phone:
        print(_c("33", f"  {len(phone)} note(s) from Jas's phone in workspace/app-feedback/:"))
        for m in phone:
            head = ""
            try:
                for line in m.read_text(encoding="utf-8").splitlines():
                    if line.startswith("kind:"):
                        head = line.split(":", 1)[1].strip()
                        break
            except OSError as e:
                print(f"  NOTE: {m.name} unreadable ({e})", file=sys.stderr)
            print(f"    [{head or '?'}] {m.name}")
        print(_c("90", "  Test results, bugs and ideas. Act on each, then commit and DELETE."))
    else:
        print(_c("90", "  No notes from the phone."))

    open_fb = notes_with("workspace/feedback", r"^status:\s*OPEN")
    if open_fb:
        print()
        print(_c("33", f"  {len(open_fb)} OPEN feedback note(s) in workspace/feedback/:"))
        for f in open_fb:
            print(f"    {f.name}")
        print(_c("90", "  These are gaps someone found and nobody has closed."))
    else:
        print(_c("90", "  No open feedback."))

    # An access request nobody sees is worse than an unswept inbox: the agent has already
    # stopped and is waiting. Surfaced here because this is where Jas reliably looks.
    pending = notes_with("workspace/access-requests", r"^status:\s*PENDING")
    if pending:
        print()
        print(_c("33", f"  {len(pending)} PENDING access request(s) - these are waiting on Jas:"))
        for f in pending:
            body = f.read_text(encoding="utf-8", errors="replace")
            m = re.search(r"^requests:\s*(.+)$", body, re.MULTILINE)
            print(f"    {f.name}")
            if m:
                print(_c("90", f"      wants: {m.group(1).strip()}"))
        print(_c("90", "  Nothing self-grants. Approve or refuse before the work resumes."))
    else:
        print(_c("90", "  No pending access requests."))

    # --- 3b. Chat agent state --------------------------------------------------------
    # Printed every run, on purpose (D-21, amendment 2). Whether an executor of
    # agent-written manifests is live is not something anyone should have to open a
    # script to discover, and a state visible only on inspection is one that drifts.
    agent_script = SCRIPTS / "chat_agent.py"
    if not agent_script.is_file():
        agent_script = SCRIPTS / "chat-agent.ps1"
    if agent_script.is_file():
        arm_line = ""
        for line in agent_script.read_text(encoding="utf-8", errors="replace").split("\n"):
            if re.match(r"^\s*(\$Armed|ARMED)\s*=", line):
                arm_line = line
                break
        print()
        if re.search(r"\$true|True", arm_line):
            print(_c("31", "  chat-agent: ARMED - manifests in workspace/commands/ will execute"))
        elif re.search(r"\$false|False", arm_line):
            print(_c("36", "  chat-agent: DRY RUN - manifests are logged, nothing executes"))
        else:
            print(_c("33", "  chat-agent: STATE UNREADABLE - could not find the Armed constant"))

        agent_log = REPO_ROOT / "workspace" / "chat-agent-log.txt"
        if agent_log.is_file():
            # DISTINCT decisions, not log lines. In dry run the queue is never consumed, so
            # the same manifests are re-decided every run -- two runs over three manifests
            # read as 27 "operations" while being 2 actual judgements. The floor is meant
            # to be 20 independent pieces of evidence about the boundaries.
            #
            # Probes excluded here as they are in the agent itself. Until 2026-08-14 they
            # were not, so the two disagreed about the same file -- and the arming decision
            # rests on this number.
            all_lines = agent_log.read_text(encoding="utf-8", errors="replace").split("\n")
            # One implementation of the counting rule, in chat_agent. This used to be a
            # second copy and the two could disagree about the number D-21 rests on.
            from chat_agent import distinct_operations
            ops = distinct_operations(all_lines)
            refused = len([o for o in ops if o[3] == "REFUSE"])
            runs = [ln for ln in all_lines if "| RUN |" in ln]
            print(_c("90", f"    {len(ops)}/20 distinct operation(s) toward the arming floor, "
                           f"{refused} refused"))
            if runs:
                print(_c("90", f"    last run: {runs[-1].split('|')[0].strip()}"))
        else:
            print(_c("90", "    no log yet - workspace/chat-agent-log.txt appears on the first run"))

    # --- 4. Where things stand -------------------------------------------------------
    section("4. Right now (from STATE.md)")

    state = REPO_ROOT / "STATE.md"
    if state.is_file():
        text = state.read_text(encoding="utf-8", errors="replace")
        lines = text.split("\n")
        start = None
        for i, line in enumerate(lines):
            if re.match(r"^##\s+Right now", line):
                start = i
                break
        if start is None:
            print(_c("33", "  STATE.md has no '## Right now' section."))
        else:
            printed = []
            for line in lines[start + 1:]:
                if re.match(r"^##\s", line):
                    break
                print(f"  {line}")
                printed.append(line)
            # The slice stops at the next heading, so the printed lines are a bounded excerpt
            # of a file that goes on. Say so, in the same breath as the slice (t1066).
            heading = lines[start].lstrip("#").strip()
            print(_c("90", f"  {coverage_line(str(state.relative_to(REPO_ROOT)), heading, printed, text)}"))
    else:
        print(_c("31", "  STATE.md is missing."))

    # --- 4b-handover. The last session wrote down what it knew ----------------------
    # STATE.md says where things stand; a handover says what the last worker learned and
    # what it would have done next. Nothing surfaced it, so it was only ever read by a
    # session that thought to go looking. Now it is named on turn one, every time.
    try:
        hos = sorted((REPO_ROOT / "workspace").glob("HANDOVER-*.md"))
        if hos:
            latest = hos[-1]
            print()
            print(_c("1", "  Latest handover -- READ IT BEFORE YOU PICK ANYTHING UP"))
            print(_c("36", f"    {latest.relative_to(REPO_ROOT)}"))
            body = latest.read_text(encoding="utf-8", errors="replace").split("\n")
            for line in body[:12]:
                print(_c("90", f"    {line[:90]}"))
            if len(body) > 12:
                print(_c("90", f"    ... {len(body) - 12} more lines -- open the file."))
            older = [h.name for h in hos[:-1][-3:]]
            if older:
                print(_c("90", "    older: " + "  ".join(older)))
            print(_c("33", "    A handover is a record, not an instruction, and its context"))
            print(_c("33", "    numbers are the LAST session's, not yours (AGENTS.md)."))
    except Exception as _e:
        print(_c("31", f"  could not read handovers: {_e}"))

    # --- 4b-tail. The end of the last session is where the granular detail is -------
    # A handover is written to be read; it is a summary, and summaries lose the small
    # concrete things -- the exact file half-edited, the flag that turned out to matter,
    # the correction Jas made in the last five minutes. Those live only in the tail of the
    # previous transcript, which nothing surfaced until now.
    print()
    print(_c("1", "  The last minutes of the previous session -- the granular detail"))
    print(_c("90", "    python3 scripts/transcripts.py tail            (last 14 turns)"))
    print(_c("90", "    python3 scripts/transcripts.py tail --turns 40 (if that is not enough)"))
    print(_c("33", "    Read this AS WELL AS the handover, not instead of it. The handover"))
    print(_c("33", "    says what was done; the tail says what was actually going on."))

    # --- 4a. A staffing run finished while you were away ----------------------------
    # It writes workspace/interviews/staffing-decision.json and nothing surfaced it, so the
    # result of an overnight run would sit on disk until somebody thought to look. A decision
    # nobody is shown is a decision nobody makes.
    dec = REPO_ROOT / "workspace" / "interviews" / "staffing-decision.json"
    res = REPO_ROOT / "workspace" / "interviews" / "staffing-results.json"
    if dec.is_file() or res.is_file():
        section("4a. Model staffing — a run has results waiting")
        try:
            chosen = json.loads(dec.read_text(encoding="utf-8")) if dec.is_file() else {}
            allr = json.loads(res.read_text(encoding="utf-8")) if res.is_file() else []
            for role in ("MANAGER", "SENIOR", "FRONTLINE"):
                r = chosen.get(role)
                if r:
                    print(f"  {role:9s} {r.get('tag')}   "
                          f"{r.get('real_score')}/{r.get('real_max')} on its paper, "
                          f"{(r.get('preflight') or {}).get('measured_tok_s')} tok/s")
                else:
                    print(_c("33", f"  {role:9s} no candidate came through"))
            dq = [r.get("tag") for r in allr if r.get("fabricated")]
            bl = [(r.get("tag"), r.get("why")) for r in allr if r.get("verdict") == "BLOCKED"]
            if dq:
                print(_c("31", f"  disqualified for answering about a file that does not "
                               f"exist: {', '.join(str(x) for x in dq)}"))
            for tag, why in bl:
                print(_c("33", f"  BLOCKED {tag}: {why}"))
            if bl:
                print(_c("90", "     A block is a CONFIGURATION fault, not a verdict on the "
                               "model. Qwen was called weak here for a week over a context"))
                print(_c("90", "     default nobody set. Re-run these before writing any off."))
            print()
            print(_c("90", "  These are recommendations from one paper. Nothing was installed"))
            print(_c("90", "  into any role -- swapping a model is a decision, not a score."))
            print(_c("90", f"  Full detail: {res.relative_to(REPO_ROOT)}"))
        except Exception as e:                        # noqa: BLE001
            print(_c("33", f"  could not read the staffing result: {e}"))

    # --- 4b. Escapes nobody recorded --------------------------------------------------
    # `escaped` is the ONLY signal that a defect got past review, and there is no detector
    # for it -- it is a command someone has to remember to run. On 2026-09-01 the count was
    # ZERO across 29 kept patches while at least one real escape existed and was identified
    # by hand (coder/brief-d74102e7 delivered the vacuous-pass guard in check_deployed.py).
    # A signal that is voluntary, unprompted, and load-bearing does not get sent. So ask.
    #
    # This does NOT guess. It lists what was kept and unasked-about, and puts the question
    # where Jas already looks. Inferring escapes from reverts was the alternative and it
    # would report a claim nobody checked, which is the defect class this repo keeps finding.
    section("4b. Has anything you kept turned out to be broken?")
    try:
        sys.path.insert(0, str(REPO_ROOT / "scripts"))
        import reviewq
        vs = reviewq.verdicts()
        escaped_keys = {v.get("branch") or v.get("task") for v in vs if v.get("escaped")}
        kept = [v for v in vs if v.get("verdict") == reviewq.KEPT and not v.get("escaped")]
        recent = [v for v in kept if (v.get("branch") or v.get("task"))
                  and (v.get("branch") or v.get("task")) not in escaped_keys][-6:]
        if not recent:
            print("  nothing kept yet -- nothing could have escaped.")
        else:
            print("  Kept by review, never reported broken. If you have hit a bug in any of")
            print("  these since, it is an ESCAPE and the batch size should reset:")
            for v in recent:
                print(f"    {v.get('branch') or v.get('task')}   kept {v.get('at','?')[:10]}")
            print()
            print(_c("90", "    reviewq.py escaped <task|--branch X> --why '...'"))
            print(_c("90", "    An escape is: a patch review APPROVED, in which a bug turned"))
            print(_c("90", "    up afterwards. Not any bug -- only one in work that was kept."))
            n = len([v for v in vs if v.get("escaped")])
            if n == 0:
                print(_c("33", "    Recorded escapes so far: 0. If that is really true across "
                               f"{len(kept)} kept patches, the pipeline is perfect. Check."))
        # THE CHECK, MECHANICAL (t208, 2026-10-01): open tasks reporting a defect in a file a kept
        # build changed. A candidate is a question, not a verdict -- confirm or dismiss it.
        import escape_watch
        cand = escape_watch.candidates(14)
        if cand:
            print(_c("33", f"    {len(cand)} POSSIBLE ESCAPE(S) -- a task reports a defect in a file a kept build changed:"))
            for x in cand:
                print(f"      {x['task']} names {', '.join(x['files'])} -- changed by {x['branch']} ({x['built_for'] or '?'})")
        else:
            print(_c("90", "    escape_watch: no open task names a file a kept build changed in the last 14 days."))
    except Exception as e:                      # never block a session on this prompt
        print(_c("33", f"  could not read review verdicts: {e}"))

    # SKILLS ARE NOT LOADED AUTOMATICALLY HERE, AND THAT IS THE WHOLE REASON THIS PRINTS.
    #
    # The skills that fire on their own come from an app-managed directory synced from Jas's
    # account. Anything in docs/skills/ in this repository is NOT there and will never fire --
    # so three skills written on 2026-09-03 would have been invisible to the very next
    # session. "A guard believed to be armed is worse than none" (L-122), wearing new clothes.
    #
    # NAMES ONLY, NOT DESCRIPTIONS. A description is ~40 tokens and this list only grows;
    # printing all of them spends context on skills irrelevant to today's task, every session,
    # for ever. The names are meaningful and `skills.py find` does the rest -- the same
    # keyword+vector ranker memory.py uses. Constant cost, however many exist.
    print()
    print(_c("1", "4c. Where to look -- which finder, when"))
    for _line in _render_finder_index():
        print(_c("90", _line))
    print()
    print(_c("1", "4b. Skills in this repository -- SEARCH BEFORE STARTING A TASK"))
    try:
        sys.path.insert(0, str(REPO_ROOT / "scripts"))
        import skills as _sk
        _all = _sk.all_skills()
        if _all:
            print(_c("36", "    " + "  ".join(x["name"] for x in _all)))
        else:
            print(_c("90", "    none yet"))
        print(_c("90", "    python3 scripts/skills.py find \"<what you are about to do>\""))
        print(_c("90", "    python3 scripts/skills.py show <name>   ·   docs/skills/INDEX.md"))
        print(_c("90", "    They do NOT load themselves. Nothing will remind you again."))
        print(_c("90", "    AGENTS.md: if you learned how to do something, write one."))
    except Exception as _e:
        print(_c("31", f"    skills listing unavailable: {_e}"))
    print()

    # --- 5. What only you can do -----------------------------------------------------
    section("5. Before you write any code")
    print("  a. Read the latest workspace/HANDOVER-*.md named above, in full,")
    print("     then run  python3 scripts/transcripts.py tail  for the fine detail")
    print("     of how the last session actually ended.")
    print("  b. Read projects/<name>/STATUS.md for whatever you are about to touch.")
    print("  c. Tell Jas what you are picking up, and get confirmation.")
    print(_c("90", "     That is the only check in this system that comes from outside the"))
    print(_c("90", "     repository, and it catches a stale STATE.md immediately."))
    print(_c("33", "     STATE.md's 'single next step' is what the LAST session expected to"))
    print(_c("33", "     be next. It is not an instruction and Jas may not have seen it."))
    print(_c("33", "  d. A context compaction counts as a new session -- stop and re-confirm,"))
    print(_c("33", "     and stop any /loop rather than let it carry you (AGENTS.md, 3)."))

    print()
    print(_c("90", "-" * 62))
    if check_failed:
        print(_c("31", " The workspace check FAILED. A document and the disk disagree."))
        print(_c("31", " Find out which is wrong before trusting anything you just read."))
    elif audit_failed:
        print(_c("33", " Previous work has compliance findings. Read them above."))
        print(_c("33", " Write up anything real: workspace/feedback/README.md"))
    else:
        print(_c("32", " Clean start. Nothing outstanding."))
    print(_c("90", "-" * 62))
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
