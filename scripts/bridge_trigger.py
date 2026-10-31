#!/usr/bin/env python3
"""After a manager pass: turn the answer's chunks block into a branch Claude reviews (t323).

    python3 scripts/bridge_trigger.py <answer.md> --task tNNN --model <manager tag> [--repo R]

scripts/manager_queue.sh runs this after manager_note.py, for every answer. It is the step
between Q4 scoping a task and Gemma building it that Claude used to do by hand.

  no ```chunks block        -> exit 0, nothing written (a prose answer is not an error)
  a block bridge.py refuses -> exit 2; ONE note on the task naming every error, so the
                               manager's next pass sees them; no branch, main untouched
  a valid block             -> exit 0; branch bridge/<task> made from main in a throwaway
                               worktree, `bridge.py --apply` run there, new test files
                               registered (when the repo has scripts/library.py), one commit
                               with the trailer `Agent: ollama/<model>`, worktree removed
  nothing new to apply      -> exit 0, no branch (every chunk was already applied)
  bridge/<task> exists      -> exit 3, nothing written: an earlier block is still waiting
                               on Claude, and a second one must not overwrite it

Main is never checked out or left dirty by the branch step. With --freeze the branch is merged
into main by the GATES (scope_lint + bridge.validate + the commit gate) -- the oracle freeze, D-35
as amended 2026-09-28; without it, Claude merging bridge/<task> is the freeze, as before.
dispatch_loop then picks the new work-list rows up unchanged.
"""
import argparse
import subprocess
import json
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))


def _git(repo, *args):
    """git -C repo <args>, captured, with a timeout -- the commit gate refuses any
    subprocess.run without one (no-timeout). Use this for EVERY git call in run()."""
    import subprocess
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True,
                          timeout=120)


def _register(worktree, path, task):
    """library.py register, the same way -- timeout included."""
    import subprocess
    return subprocess.run([sys.executable, str(Path(worktree) / "scripts" / "library.py"),
                           "register", path, f"oracle for {task}"], cwd=str(worktree),
                          capture_output=True, text=True, timeout=120)


def taskstore_note(task, text):
    """The live note writer: a note on the task, attributed to 'bridge'."""
    sys.path.insert(0, str(REPO / "projects" / "app-repo"))
    import taskstore
    taskstore.add_note(task, text, who="bridge")


def hold(task, why):
    """Hold the task for Claude with a note: the branch is waiting on a review, not on the manager."""
    try:
        sys.path.insert(0, str(REPO / "projects" / "app-repo"))
        import taskstore
        taskstore.add_note(task, why, who="bridge")
        taskstore.set_meta(task, needs=__import__("role_store").escalate("technical"))
    except Exception as e:                                          # noqa: BLE001 -- said, not swallowed
        print(f"{task}: could not hold for Claude ({type(e).__name__}: {e})", file=sys.stderr)


def taskstore_requeue(task):
    """A scope the lint refused goes back to the manager: off 'doing', unheld."""
    sys.path.insert(0, str(REPO / "projects" / "app-repo"))
    import taskstore
    taskstore.set_status(task, "queued")
    taskstore.set_meta(task, needs="")


def chunks_added(repo):
    """Did the freeze commit just made add a build chunk -- a brief, or a line in the enrolment table?"""
    briefs = _git(repo, "diff", "--name-only", "--diff-filter=A", "HEAD^1", "HEAD", "--",
                  "workspace/briefs").stdout.split()
    loop = _git(repo, "diff", "-U0", "HEAD^1", "HEAD", "--", "scripts/autonomy_loop.sh").stdout
    enrolled = [l for l in loop.splitlines() if l.startswith("+") and not l.startswith("+++")
                and "|" in l and not l[1:].lstrip().startswith("#")]
    return bool(briefs or enrolled)


def taskstore_requeue_oracles_only(task):
    """The live requeue for a scope that froze only oracles: back to queued, unheld, with the reason."""
    try:
        sys.path.insert(0, str(REPO / "projects" / "app-repo"))
        import taskstore
        taskstore.add_note(task, "This scope froze ORACLES ONLY -- no build chunk was enrolled, so nothing "
                                 "will build. Back to the queue: the next pass scopes the implementation "
                                 "against the oracles now on main.", who="bridge")
        taskstore_requeue(task)
    except Exception as e:                                          # noqa: BLE001 -- said, not swallowed
        print(f"{task}: could not requeue an oracles-only scope ({type(e).__name__}: {e})", file=sys.stderr)


def _record(on, *a, **k):
    if on:
        import scope_quality
        return scope_quality.record(*a, **k)
    return None


def run(answer, task, model, repo=REPO, note=taskstore_note, lint=False, freeze=False, record=False,
        requeue=taskstore_requeue, lint_fn=None):
    """The whole step. `note(task, text)` is called at most once, only on a refusal.

    lint=True runs scope_lint (the upstream fixture) before any branch is made; findings go back to
    the task for the manager to re-scope (exit 2). freeze=True merges the branch into main once it
    is made -- the oracle freeze, by the gates instead of by Claude (Jas, 2026-09-28: "from that
    point onwards you don't move oracles across the bridge"). record=True writes every outcome to
    scope_quality against the model that wrote the answer."""
    import contextlib
    import io
    import shutil
    import tempfile
    import bridge
    repo, answer = Path(repo), Path(answer)
    text = answer.read_text(encoding="utf-8", errors="replace")
    # ONE DEFINITION OF "HAS A BLOCK": bridge.parse's. A literal "```chunks" test here kept
    # dropping t264's ```json chunks block after bridge.parse learned to read it (2026-09-30).
    chunks, errors = bridge.parse(text)
    if errors == [bridge.NO_BLOCK]:
        return 0
    if not errors:
        errors = bridge.validate(chunks, repo)
    scope = None
    if record:
        import scope_quality
        scope = scope_quality.scope_of(task, answer=answer, model=model)
    if errors:
        note(task, "The bridge refused this answer's chunks block, so nothing was built:\n"
                   + "\n".join(f"- {e}" for e in errors)
                   + "\nRe-scope it: fix these and end with a corrected ```chunks block.")
        for e in errors:
            _record(record, "bridge", "refused", task=task, scope=scope, defect_class="chunks-invalid",
                    evidence=e, source="bridge_trigger")
        if lint:            # the gated path: a refused scope goes back to the manager's queue
            requeue(task)
        return 2
    if lint:
        import scope_lint
        findings = (lint_fn or scope_lint.lint)(text, repo)[1]
        if record:
            scope_lint.record(task, answer, findings)
        if findings:
            note(task, "The scope lint refused this answer's chunks block, so nothing was built:\n"
                       + "\n".join(f"- [{c}] chunk {i} {f}: {ev}" for c, i, f, ev in findings)
                       + "\nRe-scope it: fix these and end with a corrected ```chunks block.")
            requeue(task)
            return 2
    branch = f"bridge/{task}"
    if _git(repo, "rev-parse", "--verify", "-q", f"refs/heads/{branch}").returncode == 0:
        return 3
    tmp = Path(tempfile.mkdtemp(prefix="bridge-wt-"))
    wt = tmp / "wt"
    made = _git(repo, "worktree", "add", "-q", "-b", branch, str(wt), "main")
    if made.returncode != 0:
        shutil.rmtree(tmp, ignore_errors=True)
        raise RuntimeError(f"git worktree add failed: {made.stderr.strip()}")
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            rc = bridge.main([str(answer), "--task", task, "--repo", str(wt), "--apply"])
        if rc != 0:
            raise RuntimeError(f"bridge.py --apply exited {rc} after a clean dry run")
        if (wt / "scripts" / "library.py").is_file():
            new = _git(wt, "status", "--porcelain", "--untracked-files=all").stdout.splitlines()
            for line in new:
                path = line[3:].strip()
                if path.startswith("scripts/tests/test_") and path.endswith(".py"):
                    _register(wt, path, task)
        _git(wt, "add", "-A")
        if not _git(wt, "diff", "--cached", "--name-only").stdout.strip():
            _git(repo, "worktree", "remove", "--force", str(wt))
            _git(repo, "branch", "-D", branch)
            return 0
        done = _git(wt, "commit", "-q", "-m", f"feat(workspace): bridge {task} -- chunks block "
                    f"applied for review", "-m", f"Agent: {_agent(model)}")
        if done.returncode != 0:
            # The repository's own commit gate refused the scope's files (t241, 2026-09-28: an
            # oracle judged by test_no_live_model_in_suites). That is a defect in the scope, not
            # a crash of the bridge: it goes back to the manager like any other refusal.
            why = (done.stdout + done.stderr).strip()
            note(task, "The repository's commit gate refused this answer's files, so nothing was "
                       f"built:\n{why}\nRe-scope it so the files pass the gate.")
            _record(record, "bridge", "refused", task=task, scope=scope, defect_class="oracle-gate",
                    evidence=why, source="bridge_trigger commit gate")
            if lint:
                requeue(task)
            _git(repo, "worktree", "remove", "--force", str(wt))
            _git(repo, "branch", "-D", branch)
            return 2
    finally:
        _git(repo, "worktree", "remove", "--force", str(wt))
        shutil.rmtree(tmp, ignore_errors=True)
    _record(record, "bridge", "branch", task=task, scope=scope, source="bridge_trigger", branch=branch)
    rc = freeze_branch(repo, task, model, scope=scope, record=record, note=note) if freeze else 0
    if rc == 4 or not freeze:
        # THE BRANCH WAITS ON CLAUDE, SO THE TASK SAYS SO (2026-10-01). Left unheld, the manager's
        # rotation took t469 straight back and re-scoped it -- 95 minutes, then a timeout -- while
        # bridge/t469 sat unreviewed. `needs: claude` is the hold manager_next already skips.
        (hold if note is taskstore_note else note)(task, f"Scope frozen into branch {branch}; it waits for Claude to review and merge it"
                   + (" (the automatic freeze is off -- see bridge_trigger.freeze_enabled)." if freeze else "."))
    return rc


# ------------------------------------------------------------------ the freeze
FREEZE_OFF = REPO / "workspace" / "run-logs" / "bridge-freeze-OFF"
REVERT_AFTER = (3, 20)     # 3 oracle-class send-backs in the last 20 merge verdicts -> Claude freezes again


def freeze_enabled(ledger=None, off=None):
    """(on, why). Off by the switch file, or automatically when oracles that passed the gates are
    being sent back at merge -- the signal D-35 names for "the oracle was weak"."""
    if Path(off or FREEZE_OFF).exists():
        return False, f"switched off ({off or FREEZE_OFF} exists)"
    import scope_quality
    # ONLY VERDICTS AFTER THE LAST RESET (2026-10-01): the Manager resets it (freeze_circuit.py), and
    # a reset must not be undone by the very verdicts it was a judgement about.
    import freeze_circuit
    since = freeze_circuit.last_reset().get("at", "")
    merges = [r for r in scope_quality.rows(ledger) if r.get("stage") == "merge"
              and (not since or str(r.get("at", "")) > since)][-REVERT_AFTER[1]:]
    bad = [r for r in merges if str(r.get("class", "")).startswith("oracle-")]
    if len(bad) >= REVERT_AFTER[0]:
        return False, (f"{len(bad)} of the last {len(merges)} merge verdicts sent work back for an "
                       f"oracle defect (limit {REVERT_AFTER[0]}); the Manager has a task to review and reset it (freeze_circuit.py)")
    return True, ""


SET_LIKE = {"workspace/test-failures-ceiling.json"}


def _json_delta(base, live):
    """What the live copy changed against HEAD, per top-level key: lists as added/removed items,
    dicts as set/removed keys, anything else as a replacement."""
    out = {}
    for k in set(base) | set(live):
        b, l = base.get(k), live.get(k)
        if b == l:
            continue
        if isinstance(b, list) and isinstance(l, list):
            out[k] = ("list", [x for x in l if x not in b], [x for x in b if x not in l])
        elif isinstance(b, dict) and isinstance(l, dict):
            out[k] = ("dict", {kk: l[kk] for kk in l if b.get(kk) != l[kk]}, [kk for kk in b if kk not in l])
        else:
            out[k] = ("set", l if k in live else None, k not in live)
    return out


def _apply_delta(merged, delta):
    for k, (kind, a, r) in delta.items():
        if kind == "list":
            cur = list(merged.get(k) or [])
            merged[k] = [x for x in cur if x not in r] + [x for x in a if x not in cur]
        elif kind == "dict":
            cur = dict(merged.get(k) or {})
            cur.update(a)
            for kk in r:
                cur.pop(kk, None)
            merged[k] = cur
        elif r:
            merged.pop(k, None)
        else:
            merged[k] = a
    return merged


def _set_like_split(repo, f):
    """A SET-LIKE JSON FILE NEVER BLOCKS A FREEZE (2026-10-19): 39 of 42 freezes held for Claude since
    10-12 were 'main has uncommitted changes to workspace/test-failures-ceiling.json', which services
    write all day. Its lists and dicts are sets, so the live changes are taken as a delta against HEAD,
    the file is merged clean, and the delta is re-applied. None when the file is not readable JSON."""
    try:
        live = json.loads((Path(repo) / f).read_text(encoding="utf-8"))
        base = json.loads(_git(repo, "show", f"HEAD:{f}").stdout)
    except (OSError, ValueError):
        return None
    return _json_delta(base, live)


def _append_only_split(repo, f):
    """(extra bytes) when main's working copy of f is HEAD's copy plus appended lines, else None."""
    head = _git(repo, "show", f"HEAD:{f}").stdout.encode()
    work = (Path(repo) / f).read_bytes()
    return work[len(head):] if work.startswith(head) else None


ADDITIVE = ("workspace/test-failures-ceiling.json", "scripts/library.json", "workspace/SCRIPTS.md",
            "scripts/autonomy_loop.sh", "workspace/review-requests.jsonl")


def resolve_additive(repo, branch):
    """(True, "") when every conflict of an in-progress merge is in an ADDITIVE file and was resolved
    by keeping both sides' new entries; else (False, why) and nothing is staged for the rest."""
    repo = Path(repo)
    files = _git(repo, "diff", "--name-only", "--diff-filter=U").stdout.split()
    other = [f for f in files if f not in ADDITIVE]
    if other:
        return False, f"conflicts outside the additive lists: {', '.join(other)}"

    def show(ref, f):
        r = _git(repo, "show", f"{ref}:{f}")
        return r.stdout if r.returncode == 0 else ""
    base = _git(repo, "merge-base", "HEAD", branch).stdout.strip()
    for f in sorted(files, key=lambda x: x == "workspace/SCRIPTS.md"):    # SCRIPTS.md last
        if f.endswith(".jsonl") or f.endswith(".sh"):
            ours, theirs, b = show("HEAD", f), show(branch, f), show(base, f)
            add = [ln for ln in theirs.splitlines() if ln not in b.splitlines() and ln not in ours.splitlines()]
            (repo / f).write_text(ours + "".join(ln + "\n" for ln in add))
        elif f == "workspace/test-failures-ceiling.json":
            o, t, b = (json.loads(show(r, f) or "{}") for r in ("HEAD", branch, base))
            for k, v in t.items():
                if isinstance(v, list):
                    o[k] = o.get(k, []) + [x for x in v if x not in b.get(k, []) and x not in o.get(k, [])]
                elif isinstance(v, dict):
                    for kk, vv in v.items():
                        if kk not in b.get(k, {}) and kk not in o.get(k, {}):
                            o.setdefault(k, {})[kk] = vv
            (repo / f).write_text(json.dumps(o, indent=1) + "\n")
            # THE COUNT MOVES WITH THE LIST HERE TOO (L1's finding named BOTH writers: "bridge.py:342,
            # bridge_trigger ADDITIVE"). This path merges both sides' ceiling entries during a conflicted
            # merge, and until 2026-10-28 it left the promised number behind -- the same silent rise as a
            # freeze, reached by a different door.
            sys.path.insert(0, str(repo / "scripts"))
            import bridge as _bridge
            _bridge.sync_ceiling_count(repo, "additive merge of the ceiling")
        elif f == "scripts/library.json":
            sys.path.insert(0, str(repo / "scripts"))
            import library
            o, t = json.loads(show("HEAD", f)), json.loads(show(branch, f))
            for k, v in t.get("scripts", {}).items():
                o["scripts"].setdefault(k, v)
            library.save(o)
            library.write_md(o)
            _git(repo, "add", "workspace/SCRIPTS.md")
        elif f == "workspace/SCRIPTS.md":
            sys.path.insert(0, str(repo / "scripts"))
            import library
            library.write_md(library.load())
        _git(repo, "add", f)
    return True, ""


def freeze_branch(repo, task, model, scope=None, record=False, note=taskstore_note, lock_s=300,
                  requeue=None):
    """Merge bridge/<task> into main: the oracle freeze (D-35, amended 2026-09-28).

    Under a lock. Refuses -- leaving the branch for Claude, which is the 'blocked for Claude' case --
    when the freeze is off, when main has uncommitted changes to a file the branch touches (except
    an append-only .jsonl ledger, whose appended lines are set aside and re-appended), or when the
    merge conflicts. Returns 0 merged, 4 refused."""
    import fcntl
    repo = Path(repo)
    branch = f"bridge/{task}"
    on, why = freeze_enabled()
    if not on:
        _record(record, "freeze", "held-for-claude", task=task, scope=scope, evidence=why,
                source="bridge_trigger")
        return 4
    common = Path(_git(repo, "rev-parse", "--git-common-dir").stdout.strip())
    lockf = open((common if common.is_absolute() else repo / common) / "bridge-freeze.lock", "w")
    try:
        deadline = time.time() + lock_s
        while True:
            try:
                fcntl.flock(lockf, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.time() > deadline:
                    return _refuse(record, task, scope, "another freeze held the lock for 5 min")
                time.sleep(2)
        touched = _git(repo, "diff", "--name-only", f"main...{branch}").stdout.split()
        dirty = {line[3:].strip() for line in _git(repo, "status", "--porcelain").stdout.splitlines()}
        overlap = [f for f in touched if f in dirty]
        saved, deltas = {}, {}
        for f in overlap:
            if f in SET_LIKE:
                dl = _set_like_split(repo, f)
                if dl is not None:
                    deltas[f] = dl
                    continue
            extra = _append_only_split(repo, f) if f.endswith(".jsonl") else None
            if extra is None:
                return _refuse(record, task, scope, f"main has uncommitted changes to {f}, which "
                                                     f"the branch also changes")
            saved[f] = extra
        for f in list(saved) + list(deltas):
            _git(repo, "checkout", "--", f)
        msg = (f"chore(workspace): freeze {branch} -- scope lint and bridge checks passed\n\n"
               f"Oracle frozen by the gates, not by Claude (D-35 as amended 2026-09-28). Claude reviews "
               f"the oracle together with the build at merge.\n\nAgent: {_agent(model)}\n")
        # MAIN MUST NOT MOVE UNDER A FREEZE (2026-10-19). The conflict path below is several git
        # commands, and nothing else that writes main takes this lock: a fast-forward landed 12 s into
        # bf23faf2a's additive resolve, and the commit took the NEW HEAD as its parent with the index
        # built from the OLD one -- silently reverting 47595879b. Checked again before committing.
        start = _git(repo, "rev-parse", "HEAD").stdout.strip()
        m = subprocess.run(["git", "-C", str(repo), "merge", "--no-ff", "-q", "-m", msg, branch],
                           capture_output=True, text=True, timeout=300)
        # ADDITIVE CONFLICTS RESOLVE THEMSELVES (2026-10-08). Every scope adds its oracle to the same
        # lists -- the failure ceiling, the script library, the loop table, the review requests -- so
        # any two scopes in flight conflicted, and each refusal waited on Claude, who kept both sides
        # by hand (10 bridges that morning). resolve_additive() does exactly that, and only that:
        # a conflict in any other file still refuses.
        if m.returncode != 0 and (Path(repo) / ".git" / "MERGE_HEAD").exists():
            ok, why = resolve_additive(repo, branch)
            moved = _git(repo, "rev-parse", "HEAD").stdout.strip()
            if ok and moved != start:
                ok, why = False, (f"main moved during the freeze ({start[:9]} -> {moved[:9]}): committing "
                                  f"would revert it, so the freeze is refused and retried on the new main")
            if ok:
                c = subprocess.run(["git", "-C", str(repo), "commit", "-q", "--no-edit"],
                                   capture_output=True, text=True, timeout=600)
                if c.returncode == 0:
                    m = c
                else:
                    m = subprocess.CompletedProcess(c.args, c.returncode, c.stdout, c.stderr)
            else:
                m = subprocess.CompletedProcess(m.args, m.returncode, m.stdout + f"\n{why}", m.stderr)
        if m.returncode != 0:
            _git(repo, "merge", "--abort")
        for f, extra in saved.items():
            with open(repo / f, "ab") as fh:
                fh.write(extra)
        for f, dl in deltas.items():
            p_ = repo / f
            p_.write_text(json.dumps(_apply_delta(json.loads(p_.read_text(encoding="utf-8")), dl), indent=1)
                          + "\n", encoding="utf-8")
        if m.returncode != 0:
            return _refuse(record, task, scope, f"git merge failed: {(m.stdout + m.stderr).strip()}")
        added = _git(repo, "diff", "--name-only", "--diff-filter=A", "HEAD^1", "HEAD").stdout.split()
        _git(repo, "branch", "-d", branch)
        _record(record, "freeze", "merged", task=task, scope=scope, source="bridge_trigger",
                commit=_git(repo, "rev-parse", "--short", "HEAD").stdout.strip())
        declare_new_oracles(repo, task, added, model)
        # A SCOPE WITH NO BUILD CHUNKS GOES BACK TO THE MANAGER (Jas, 2026-10-13: "Yes make that change
        # to the bridge"). t1115's 18:49Z scope landed only its oracles -- the file its build needed did
        # not exist yet -- and the task stayed 'doing': nothing enrolled, the manager queue skips anything
        # not 'queued', so nobody picked it up and nothing said why. Now: no brief and no enrolled line
        # added by this merge -> queued again, with a note, so the next pass scopes the implementation.
        if not chunks_added(repo):
            if requeue is None:
                # The LIVE task store only for the live repository: a test's scratch repo must never
                # requeue a real task of the same id.
                live = Path(repo).resolve() == REPO.resolve() and note is taskstore_note
                requeue = taskstore_requeue_oracles_only if live else (lambda t: None)
            requeue(task)
        return 0
    finally:
        lockf.close()


def declare_new_oracles(repo, task, added, model="manager"):
    """A frozen oracle that is red on main is DECLARED, so it cannot hold every build (2026-10-12).

    Scopes are asked to add their new suite to workspace/test-failures-ceiling.json, and usually do;
    t1024's did not. Its two suites were red on main for want of their implementation, and the
    scheduler's red-on-main hold stopped EVERY build from 2026-10-11 until Claude found it -- the
    second time (a66b9859 was the first). This is the backstop: after the merge, each test file the
    freeze ADDED that fails on main and is not declared is declared, owned by `task`, in one commit.
    A suite that passes is left alone; one that cannot be run is declared and says so. Returns the
    list of suites declared."""
    import json
    repo = Path(repo)
    suites = [f for f in added if f.startswith("scripts/tests/") and f.endswith(".py")]
    if not suites:
        return []
    ceiling = repo / "workspace" / "test-failures-ceiling.json"
    try:
        d = json.loads(ceiling.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        print(f"  NOTE: ceiling unreadable ({e}); new oracles NOT declared", file=sys.stderr)
        return []
    allowed = d.setdefault("allowed_to_fail", [])
    reasons = d.setdefault("_oracles_awaiting_their_fix", {})
    declared = []
    for f in suites:
        name = Path(f).name
        if name in allowed:
            continue
        try:
            r = subprocess.run([sys.executable, f], cwd=str(repo), capture_output=True, text=True,
                               timeout=300)
            red, how = r.returncode != 0, f"exit {r.returncode}"
        except subprocess.TimeoutExpired:
            red, how = True, "timed out after 300 s"
        if not red:
            continue
        allowed.append(name)
        reasons[name] = (f"{task} ({time.strftime('%Y-%m-%d')}, declared by the freeze backstop): frozen "
                         f"oracle red on main ({how}) because its implementation is not built yet. "
                         f"Remove when {task}'s build merges.")
        declared.append(name)
    if declared:
        ceiling.write_text(json.dumps(d, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        _git(repo, "add", str(ceiling.relative_to(repo)))
        subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m",
                        f"chore(workspace): declare {task}'s new oracles red on main -- "
                        f"{', '.join(declared)}\n\nThe freeze backstop: an undeclared red oracle holds "
                        f"every build.\n\nAgent: {_agent(model)}\n"],
                       capture_output=True, text=True, timeout=600)
        print(f"  {task}: declared {len(declared)} new red oracle(s): {', '.join(declared)}")
    return declared


def _agent(model):
    """The commit trailer's agent. A local model's tag gets 'ollama/'; a name that already says its
    tool (claude-code/opus-5.5 -- Claude scoping for the models, Jas 2026-10-02) is used as it is,
    so the compliance audit credits the right author."""
    return model if "/" in model else f"ollama/{model}"


def _refuse(record, task, scope, why):
    _record(record, "freeze", "held-for-claude", task=task, scope=scope, evidence=why,
            source="bridge_trigger")
    print(f"{task}: freeze refused, left for Claude: {why}", file=sys.stderr)
    return 4


def main(argv):
    """Parse the arguments above and return run(...)'s exit code."""
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("answer")
    ap.add_argument("--task", required=True)
    ap.add_argument("--model", required=True)
    ap.add_argument("--repo", default=str(REPO))
    ap.add_argument("--lint", action="store_true", help="run scope_lint before making the branch")
    ap.add_argument("--freeze", action="store_true", help="merge the branch into main when it is made")
    ap.add_argument("--record", action="store_true", help="record every outcome in scope_quality")
    ap.add_argument("--freeze-existing", action="store_true",
                    help="the branch already exists: lint the answer, then freeze that branch")
    a = ap.parse_args(argv)
    if a.freeze_existing:
        import scope_lint
        text = Path(a.answer).read_text(encoding="utf-8", errors="replace")
        findings = scope_lint.lint(text, Path(a.repo))[1]
        if a.record:
            scope_lint.record(a.task, a.answer, findings)
        if findings:
            for c, i, f, ev in findings:
                print(f"{a.task}\t{c}\tchunk {i}\t{f}\t{ev}")
            return 2
        return freeze_branch(Path(a.repo), a.task, a.model, record=a.record)
    return run(a.answer, a.task, a.model, repo=Path(a.repo), lint=a.lint, freeze=a.freeze,
               record=a.record)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
