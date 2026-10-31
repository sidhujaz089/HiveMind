#!/usr/bin/env python3
"""Fixtures for workspace_mcp/core.py.

The two that matter most are the DENY tests and the LOG tests. A server that
lets anything WRITE to chat/ has broken D-26; a server whose log misses a refused
call is a record written by the thing it describes, which is the failure L-039 names.

D-26 (2026-08-19) replaced D-22's outright denial of chat/ with read-only access,
because the Family Project loads its ledger from that corpus and D-24 removes the
Drive route that had been quietly covering it. The read tests below therefore assert
the OPPOSITE of what they asserted before that date. That is deliberate, and the
write tests are what keeps the change from being a straight loosening.
"""
import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
REPO_ROOT = Path(__file__).resolve().parents[2]
from workspace_mcp.core import Workspace, Denied, HTTP_TOOLS  # noqa: E402

PASSED = FAILED = 0


def check(label, got, want):
    global PASSED, FAILED
    if got == want:
        PASSED += 1
        print(f"  [ok]   {label}: {got}")
    else:
        FAILED += 1
        print(f"  [BAD]  {label}: expected {want}, got {got}")


TESTED_RULES = set()   # every refusal rule a test has actually TRIGGERED, at runtime


def refused(fn, *a, **k):
    """Returns the rule name of the refusal, or 'ALLOWED' if it went through.

    Also records the rule in TESTED_RULES. Runtime evidence, not a text scan: the first
    version of the guard below matched rule names in this file's own SOURCE, and promptly
    counted its own baseline list as coverage. Twice. A rule is tested when a test has made
    it fire -- nothing weaker survives contact with a file that mentions its own contents.
    """
    try:
        fn(*a, **k)
        return "ALLOWED"
    except Denied as e:
        TESTED_RULES.add(e.rule)
        return e.rule


def make_ws():
    root = Path(tempfile.mkdtemp())
    (root / "docs").mkdir()
    (root / "workspace" / "inbox").mkdir(parents=True)
    (root / "workspace" / "private").mkdir(parents=True)
    (root / "chat" / "projects").mkdir(parents=True)
    (root / ".venv").mkdir()
    (root / "docs" / "NOTE.md").write_text("alpha\nbeta CANARY gamma\ndelta\n")
    (root / "chat" / "secret.txt").write_text("family material\n")
    (root / "workspace" / "private" / "protocol.md").write_text("hidden\n")
    (root / "workspace" / "PROJECT-INDEX.md").write_text("# index\n")
    # A REAL GIT REPO, because writes now commit through the hook and a fixture that is not
    # a repo would exercise a code path production never takes. Deliberately NOT solved by
    # making _commit skip when git is absent: that would silently disable the gate on any
    # machine with a broken .git, which is the examined-nothing shape.
    import subprocess as _sp
    hooks = root / "scripts" / "hooks"
    hooks.mkdir(parents=True, exist_ok=True)
    real = REPO_ROOT / "scripts" / "hooks" / "commit-msg"
    if real.is_file():
        (hooks / "commit-msg").write_text(real.read_text(), encoding="utf-8")
        (hooks / "commit-msg").chmod(0o755)
    for cmd in (["init", "-q"], ["config", "user.email", "t@t"], ["config", "user.name", "t"],
                ["add", "-A"]):
        _sp.run(["git", "-C", str(root)] + cmd, capture_output=True)
    _sp.run(["git", "-C", str(root), "-c", "core.hooksPath=", "commit", "-q", "-m",
             "chore(workspace): fixture\n\nAgent: human/jas"], capture_output=True)
    return Workspace(root, caller="test")


def logged(ws):
    return [json.loads(l) for l in ws.log_path.read_text().splitlines() if l.strip()]


print("\nworkspace_mcp/core.py fixtures\n")

# --- deny by default (docs/ACCESS.md, D-22 as amended by D-26) ---------------------
ws = make_ws()
check("workspace/private/ is refused", refused(ws.read, "workspace/private/protocol.md"), "path-denied")
check(".git/ is refused", refused(ws.read, ".git/config"), "path-denied")
check(".venv/ is refused", refused(ws.read, ".venv/pyvenv.cfg"), "path-denied")
check("a credential name is refused", refused(ws.read, "docs/anthropic.env"), "name-denied")

# --- chat/ is READ-ONLY, not denied (D-26) -----------------------------------------
# The corpus is Chat's memory. It may be recalled and must not be rewritten.
check("chat/ can be READ", ws.read("chat/secret.txt")["content"], "family material\n")
check("WRITING into chat/ is refused", refused(ws.write, "chat/x.md", "x"), "path-read-only")
check("REPLACING in chat/ is refused",
      refused(ws.replace, "chat/secret.txt", "family", "other"), "path-read-only")
check("a read-only refusal is logged with its rule",
      [r for r in logged(ws) if r.get("detail") == "path-read-only"][0]["tool"], "write")
# A credential inside the corpus is still refused -- read-only widens the path, not the
# name list, and these two rules must not be able to cancel each other out.
check("a credential name inside chat/ is still refused",
      refused(ws.read, "chat/credentials.json"), "name-denied")

# --- path escapes, inherited from chat_agent's fixtured resolver --------------------
check("../ escape refused", refused(ws.read, "../../etc/passwd"), "path-escapes-repo")
check("absolute path refused", refused(ws.read, "/etc/passwd"), "path-not-relative")
check("windows drive path refused", refused(ws.read, "C:/Windows/x"), "path-not-relative")

# --- the ordinary path works --------------------------------------------------------
r = ws.read("docs/NOTE.md")
check("a permitted read works", r["bytes"] > 0, True)

# --- EVERY call is logged, including refusals ---------------------------------------
entries = logged(ws)
# These two are running TOTALS for every call made above, so adding a case up there
# means editing the number here. That is on purpose: the point of the assertion is
# that no call escapes the log, and a count that updated itself would prove nothing.
check("refusals are logged, not just successes",
      len([e for e in entries if e["outcome"] == "refused"]), 10)
check("the successful read is logged too",
      len([e for e in entries if e["tool"] == "read" and e["outcome"] == "ok"]), 2)
check("every entry names the caller", all(e["caller"] == "test" for e in entries), True)
check("every refusal names its rule", all("detail" in e for e in entries if e["outcome"] == "refused"), True)

# --- write returns a hash of what was READ BACK, not what was sent -------------------
w = ws.write("docs/NEW.md", "hello\n")
import hashlib
check("write reports a readback hash",
      w["sha256"], hashlib.sha256(b"hello\n").hexdigest())
check("write confirms the readback matched", w["readback_matches"], True)
check("the file really exists on disk", (ws.root / "docs" / "NEW.md").read_text(), "hello\n")

# --- anchored replace ----------------------------------------------------------------
check("anchor matching once succeeds", ws.replace("docs/NOTE.md", "CANARY", "ROTATED")["line"], 2)
check("anchor now absent is refused", refused(ws.replace, "docs/NOTE.md", "CANARY", "x"), "anchor-not-found")
(ws.root / "docs" / "DUP.md").write_text("same\nsame\n")
check("ambiguous anchor is refused", refused(ws.replace, "docs/DUP.md", "same", "x"), "anchor-not-unique")
check("empty anchor is refused", refused(ws.replace, "docs/NOTE.md", "", "x"), "empty-anchor")

# --- search reaches chat/, and still never reaches what is denied ---------------------
s = ws.search("family", glob="**/*")
check("search DOES return chat/ content (D-26)",
      [h["path"] for h in s["hits"] if h["path"].startswith("chat/")], ["chat/secret.txt"])
s2 = ws.search("hidden", glob="**/*")
check("search never returns workspace/private/ content",
      [h for h in s2["hits"] if h["path"].startswith("workspace/private/")], [])

# --- a read is auditable after the fact (G4/G5 groundwork) --------------------------
wsh = make_ws()
r_h = wsh.read("docs/NOTE.md")
import hashlib as _hl  # noqa: E402
expected = _hl.sha256((wsh.root / "docs" / "NOTE.md").read_bytes()).hexdigest()
check("read returns the hash of the bytes on disk", r_h["sha256"], expected)
check("and the hash is logged, not only returned", logged(wsh)[-1]["sha256"], expected)
(wsh.root / "docs" / "NOTE.md").write_text("changed underneath the reader\n")
r_h2 = wsh.read("docs/NOTE.md")
check("a file changing since the last read is visible in the log",
      r_h2["sha256"] != expected, True)

# --- search must not report "found nothing" when it LOOKED at nothing ----------------
# A glob ending in ** matches directories and no files. Chat scoped a search that way on
# 2026-08-19, got zero hits with outcome "ok", and concluded a committed file did not exist.
wss = make_ws()
(wss.root / "chat" / "projects" / "deep.md").write_text("needle here\n")
r1 = wss.search("needle", glob="chat/**")
check("a glob ending in ** still scans files", r1["scanned"] > 0, True)
check("...and finds them", len(r1["hits"]), 1)
r2 = wss.search("needle", glob="nosuchdir/*")
check("a genuinely empty scope reports scanned 0", r2["scanned"], 0)
check("...which is distinguishable from a real miss", len(r2["hits"]), 0)
r3 = wss.search("no-such-string-anywhere", glob="**/*")
check("a real miss scanned files but found none", (r3["scanned"] > 0, len(r3["hits"])), (True, 0))
check("the glob actually used is reported back", wss.search("x", glob="chat/**")["glob"], "chat/**/*")
# t142: a search cut at its limit still reports TOTALS -- the counts used to stop at the cut.
for i in range(5):
    (wss.root / "docs" / f"many{i}.md").write_text("needle\n")
(wss.root / ".venv" / "dep.md").write_text("needle\n")
cut = wss.search("needle", glob="**/*", limit=2)
whole = wss.search("needle", glob="**/*", limit=1000)
check("a cut search is marked truncated", cut["truncated"], True)
check("  and its skip counts are the whole walk's, not the part before the cut",
      (cut["skipped_dependency_files"], cut["skipped_non_text"]),
      (whole["skipped_dependency_files"], whole["skipped_non_text"]))
check("  and it says how many searchable files it never read",
      cut["scanned"] + cut["not_read_after_limit"], whole["scanned"])

# --- G11: the log says WHO, and never guesses --------------------------------------
# The failure being guarded against is not a missing field. It is a row that confidently
# names the wrong caller because a value leaked from an earlier request.
import asyncio  # noqa: E402
from workspace_mcp.core import CALL_CONTEXT, set_call_context  # noqa: E402

wsg = make_ws()
wsg.read("docs/NOTE.md")
check("no context -> the row says 'direct', not a guessed caller",
      logged(wsg)[-1]["via"], "direct")
check("no context -> no client field is invented", "client" in logged(wsg)[-1], False)

tok = set_call_context(client="160.79.106.182", via="oauth:wsmcp_abc")
wsg.read("docs/NOTE.md")
row = logged(wsg)[-1]
check("with context -> the credential is recorded", row["via"], "oauth:wsmcp_abc")
check("with context -> the address is recorded too", row["client"], "160.79.106.182")
CALL_CONTEXT.reset(tok)

wsg.read("docs/NOTE.md")
check("after reset -> the previous caller does NOT linger",
      logged(wsg)[-1]["via"], "direct")

# A refused call must be attributed as carefully as a successful one -- an audit log that
# names the caller only when nothing went wrong is the wrong way round.
tok = set_call_context(client="10.0.0.9", via="admin-bearer")
refused(wsg.read, "workspace/private/protocol.md")
check("a REFUSED call is attributed too", logged(wsg)[-1]["via"], "admin-bearer")
CALL_CONTEXT.reset(tok)


async def _concurrent():
    """Two callers in flight at once. If the contextvar is shared rather than task-scoped,
    one of these rows gets the other's identity -- which is the silent failure that makes
    an attributed log worse than an unattributed one."""
    async def caller(name):
        set_call_context(client=name, via=f"oauth:{name}")
        await asyncio.sleep(0)          # force interleaving at the scheduler
        wsg.read("docs/NOTE.md")
        await asyncio.sleep(0)
        wsg.read("docs/NOTE.md")
    await asyncio.gather(caller("alice"), caller("bob"))

asyncio.run(_concurrent())
rows = [r for r in logged(wsg) if r.get("via", "").startswith("oauth:")][-4:]
check("concurrent callers are never mixed up",
      sorted(r["via"] for r in rows), ["oauth:alice", "oauth:alice", "oauth:bob", "oauth:bob"])
check("and the concurrent contexts do not escape their tasks",
      (wsg.read("docs/NOTE.md"), logged(wsg)[-1]["via"])[1], "direct")

# --- inbox: the SERVER names the file, not the caller (ROADMAP G1) --------------------
post = ws.inbox_post("A Test Subject!!", "body text")
check("inbox filename is generated, not supplied",
      post["path"].startswith("workspace/inbox/") and post["path"].endswith("--chat--a-test-subject.md"), True)
check("inbox_list sees it", len(ws.inbox_list()["messages"]), 1)

# --- status ---------------------------------------------------------------------------
check("status serves PROJECT-INDEX.md", ws.status()["content"], "# index\n")

# --- a log that cannot be written must not become a silent success --------------------
ws2 = make_ws()
ws2.log_path = Path("/proc/cannot/write/here.jsonl")
check("unwritable log refuses the call", refused(ws2.read, "docs/NOTE.md"), "call-log-unwritable")

shutil.rmtree(ws.root, ignore_errors=True)
shutil.rmtree(ws2.root, ignore_errors=True)

# --- transport-scoped capability (the blast-radius limiter) -----------------------------
# Authentication decides WHO gets in. This decides what they can do once in, which is the
# half that still applies after a credential leaks. Removal, not permission-checking: a
# tool absent from the registry cannot be called, argued for, or reached by injection.
import subprocess  # noqa: E402
from workspace_mcp.core import STDIO_TOOLS, HTTP_TOOLS  # noqa: E402

check("stdio keeps write", "write" in STDIO_TOOLS, True)
check("stdio keeps replace", "replace" in STDIO_TOOLS, True)
# WRITE IS NOW GRANTED AND GATED, not withheld (t194, 2026-08-31). The invariant that
# replaces "Chat cannot write" is "Chat cannot write WITHOUT A COMMIT" -- attributable to
# claude-chat, visible in git log, revertable. These assert the gate, because the grant
# without it is the thing that was actually being refused.
check("http has write", "write" in HTTP_TOOLS, True)
check("http has replace", "replace" in HTTP_TOOLS, True)

_g = make_ws()
_w = _g.write("docs/GATED.md", "written over the bridge\n")
check("a write returns the commit it made", bool(_w.get("commit")), True)
_log = subprocess.run(["git", "-C", str(_g.root), "log", "-1", "--format=%B"],
                      capture_output=True, text=True).stdout
check("  and it is attributed to Chat, not to nobody",
      "Agent: claude-chat/opus-5" in _log, True)
check("  and the file itself is committed, not left in the tree",
      subprocess.run(["git", "-C", str(_g.root), "status", "--porcelain", "--",
                      "docs/GATED.md"], capture_output=True, text=True).stdout.strip(), "")

# THE REFUSAL THAT MATTERS. If the commit cannot be made, the write must not survive -- an
# unattributable edit sitting in the working tree is exactly what the gate prevents, and it
# would reach the next session as mystery drift.
(_g.root / "scripts" / "hooks" / "commit-msg").write_text(
    "#!/usr/bin/env bash\nexit 1\n", encoding="utf-8")
(_g.root / "scripts" / "hooks" / "commit-msg").chmod(0o755)
_refused = False
try:
    _g.write("docs/REJECTED.md", "should not survive\n")
except Denied:
    _refused = True
check("a write the hook rejects is REFUSED", _refused, True)
check("  and the file it wrote is rolled back, not left behind",
      (_g.root / "docs" / "REJECTED.md").exists(), False)
check("http keeps inbox_post so Chat can still ASK", "inbox_post" in HTTP_TOOLS, True)
check("every http tool is a real stdio tool",
      [x for x in HTTP_TOOLS if x not in STDIO_TOOLS], [])

SERVER = Path(__file__).resolve().parent.parent / "workspace_mcp" / "server.py"
# THE SUITE FINDS ITS INTERPRETER DYNAMICALLY (D.5.1, 2026-10-22). This used to pin the
# repository's private virtualenv by path from __file__, and that venv is gitignored: in a
# delivery worktree the path does not exist, every subprocess check raised FileNotFoundError,
# and the pre-commit related_tests gate refused the commit -- 8 builds since 10-20 landed as
# exit 3 "accepted but NOT WRITTEN" (all 8, one cause), 453 sandbox rows red at baseline,
# every future coder.py commit blocked the same way. The server needs `mcp`, which lives in
# that venv, so "the venv's python" was a proxy for "an interpreter that can import mcp".
# Ask that question directly: try this interpreter, then the checkout's venv; use the first
# that imports mcp. If none can, the server-launch checks SKIP, loudly -- a declared skip
# that names the missing package, never a silent pass (rule 9).
_SERVER_PY = None
_SERVER_WHY = ""


def server_python():
    """The interpreter to launch workspace_mcp.server with, resolved by CAPABILITY.
    None (with a reason in _SERVER_WHY) when no interpreter on this machine can import mcp."""
    global _SERVER_PY, _SERVER_WHY
    if _SERVER_PY is None and not _SERVER_WHY:
        root = Path(__file__).resolve().parents[2]
        cands = [sys.executable]
        cands += [str(root / ".venv" / "bin" / p) for p in ("python", "python3")]
        for py in cands:
            if not Path(py).exists():
                continue
            probe = subprocess.run([str(py), "-c", "import mcp"], capture_output=True, timeout=30)
            if probe.returncode == 0:
                _SERVER_PY = Path(py)
                break
            _SERVER_WHY = f"{py}: mcp not importable"
        if _SERVER_PY is None:
            _SERVER_WHY = _SERVER_WHY or "no candidate interpreter found"
    return _SERVER_PY


def bind_exit(host):
    """Exit code, captured directly. Piping this to head reported 0 for a refusal --
    L-054, hit again while verifying this very feature."""
    py = server_python()
    if py is None:
        print(f"  [skip] binding {host}: workspace_mcp.server cannot run here ({_SERVER_WHY}); "
              f"install mcp for the interpreter running this suite to test host refusal")
        return None
    return subprocess.run(
        [str(py), "-m", "workspace_mcp.server", "--transport", "streamable-http",
         "--host", host],
        cwd=str(SERVER.parent.parent), capture_output=True, text=True, timeout=30,
    ).returncode


for _host, _label in [("0.0.0.0", "binding 0.0.0.0 is refused"),
                      ("192.0.2.97", "binding a LAN address is refused"),
                      ("8.8.8.8", "binding a public-looking address is refused")]:
    _rc = bind_exit(_host)
    if _rc is None:
        pass                       # the [skip] line above is printed by bind_exit
    else:
        check(_label, _rc, 2)

# --- a caller can tell a decision from a gap ---------------------------------------------
# Chat, 2026-08-31: "I can't tell [intentional from not-yet-built] from the tool list alone."
# An absent tool looks the same either way, and the two need opposite responses -- respect
# the boundary, or ask for the work. status() now says which.
_ws = make_ws()
_st = _ws.status()
check("status lists the tools a caller has", "read" in _st["tools_available"], True)

# STATUS MUST REPORT THE REGISTRY, NOT THE CONSTANT. Chat found this on 2026-09-01: status
# returned list(HTTP_TOOLS), so any drift between what is declared and what is actually
# served was invisible in the one place a caller looks to find out. A status that reports
# intent is a check nobody can watch fail.
_reg = make_ws()
_reg.served = ("read", "status")
_rs = _reg.status()
check("status reports what the server actually registered",
      _rs["tools_available"], ["read", "status"])
check("  and says so, rather than implying it",
      "registry" in _rs["tools_source"], True)
check("  and a process that did not report its registry admits that",
      "did not report" in make_ws().status()["tools_source"], True)
check("write is now among them, gated rather than withheld",
      "write" in _st["tools_available"], True)
check("and that execute is simply not built",
      _st["tools_withheld"]["execute"]["kind"], "not built")
check("every withheld tool gives a reason, not just a label",
      all(len(v["why"]) > 20 for v in _st["tools_withheld"].values()), True)



# ------------------------------------------------------------------------------ dispatch
# dispatch_coder is exposed where `write` is withheld, because coder.py is the gate rather
# than this method: sandbox, suite must go green, review branch, Jas merges. So the tests
# that matter are the ones proving a dispatch cannot be aimed somewhere it should not go.
_d = make_ws()
check("a dispatch with no brief is refused", refused(_d.dispatch_coder, "", ["a.py"]),
      "empty-brief")
check("a dispatch with no files is refused", refused(_d.dispatch_coder, "do a thing", []),
      "no-files")
check("  and a blank-ish file list counts as none",
      refused(_d.dispatch_coder, "do a thing", ["  "]), "no-files")
check("a dispatch cannot be aimed at workspace/private/",
      refused(_d.dispatch_coder, "x", ["workspace/private/secret.md"]), "path-denied")
check("a dispatch cannot be aimed outside the repo",
      refused(_d.dispatch_coder, "x", ["../etc/passwd"]), "path-escapes-repo")
check("a dispatch cannot be aimed at an absolute path",
      refused(_d.dispatch_coder, "x", ["/etc/passwd"]), "path-not-relative")
check("a dispatch cannot edit chat/ -- read-only is not editable",
      refused(_d.dispatch_coder, "x", ["chat/notes.md"]), "path-read-only")
# The fixture workspace has no projects/local-inference, so this exercises the defensive
# import path: a missing sibling project refuses with a reason rather than a traceback.
check("a missing coder.py refuses with a reason, not a stack trace",
      refused(_d.dispatch_coder, "x", ["scripts/foo.py"]), "coder-missing")

# AND WHEN coder.py IS THERE BUT THE MODEL CANNOT BE RESOLVED, REFUSE RATHER THAN GUESS.
# coder.py's own default is the string "local" -- a litellm gateway ROLE that Ollama 404s on
# every request. console.model()'s docstring records that defect being fixed in one caller
# and not carried to another; the MCP port dropped --model on 2026-09-07 and made it a third
# time, 404-ing on the very first real dispatch. Refusing beats dispatching a run that cannot
# reach a model and will look like the model failing.
_m = make_ws()
_li = _m.root / "projects" / "local-inference"
_li.mkdir(parents=True, exist_ok=True)
(_li / "coder.py").write_text("# fixture: present but there is no console.py beside it\n")
check("coder.py present but no resolvable model refuses",
      refused(_m.dispatch_coder, "x", ["scripts/foo.py"]), "model-unresolved")

# ---------------------------------------------------------------- error messages are results
# L-150. A refusal is a TOOL RESULT: the model reasons from it exactly as it reasons from
# file contents, and a stale one sends it to the wrong conclusion with full confidence.
# Both halves below were live faults on 2026-09-06, an hour apart, and the second was
# introduced BY the fix for the first.

_e = make_ws()
_dir = _e.read("workspace")
check("reading a directory lists it rather than refusing", _dir.get("kind"), "directory")
check("  and the listing says how many entries there are", _dir.get("count", 0) > 0, True)
check("  and still fills content, so a caller reading .content is not surprised",
      "DIRECTORY" in _dir.get("content", ""), True)

check("a path that is not there says NO-SUCH-PATH, not 'not a file'",
      refused(_e.read, "no/such/path/at/all"), "no-such-path")
try:
    _e.read("definitely-not-here")
except Denied as d:
    # The wording is the fix. "not-a-file" was true when directories were refused; once they
    # are listed it can only mean the path is absent, and Qwen read the stale wording as
    # "this is a directory I cannot list" and abandoned a task it was one path-fix away from.
    check("  and the detail tells the caller what to do next",
          any(w in str(d) for w in ("search", "parent directory")), True)

# --- TASK TOOLS (t662): through taskstore, never raw; a note is never in Jas's name --------
print("\ntask tools (t662)\n")
tws = make_ws()
_td = tws.root / "workspace" / "tasks"
_td.mkdir(parents=True, exist_ok=True)
def _task(tid, title, assigned, status="queued"):
    (_td / f"{tid}--x.md").write_text(f"---\nid: {tid}\ntitle: {title}\narea: hub\nkind: build\n"
                                     f"status: {status}\nassigned: {assigned}\nsource: \ncreated: x\n---\n\n"
                                     "body\n\n## Notes\n\n## Attachments\n- a.png\n")
_task("t1", "Mine", "jas"); _task("t2", "Production's", "production"); _task("t3", "Also mine", "jas")
_task("t4", "Mine but done", "jas", status="done")
(_td / "QUEUE.md").write_text("# QUEUE\n\n1. t3\n2. t1\n3. t2\n4. t4\n")
_ts = tws._ts()
check("taskstore is pointed at the fixture, not the repo", _ts.TASKS, _td)
_ts.write_queue(["t3", "t1", "t2"])
mine = tws.my_tasks()
check("my_tasks: every open task assigned to jas, queue order", [t["id"] for t in mine["tasks"]], ["t3", "t1"])
check("my_tasks: a done task is not listed", "t4" in [t["id"] for t in mine["tasks"]], False)
check("my_tasks(production)", [t["id"] for t in tws.my_tasks("production")["tasks"]], ["t2"])
check("task_get by bare number", tws.task_get("1")["title"], "Mine")
check("task_get: unknown id is refused", refused(tws.task_get, "t999"), "no-such-task")
check("task_get: not an id is refused", refused(tws.task_get, "../etc/passwd"), "bad-task-id")
tws.task_note("t1", "move this after the kpi work")
_body = (_td / "t1--x.md").read_text()
_notes, _, _att = _body.partition("## Attachments")
check("task_note lands in Notes, before Attachments", "move this after the kpi work" in _notes, True)
check("task_note: Attachments section untouched", _att.strip(), "- a.png")
check("task_note is signed Claude - Chat, never Jas (L-091)",
      ("· claude-chat**" in _body, "(Claude - Chat) (via Chat, relaying Jas)" in _body, "· jas**" in _body),
      (True, True, False))
check("task_note: empty note refused", refused(tws.task_note, "t1", "  "), "empty-note")
check("task_note: unknown task refused", refused(tws.task_note, "t998", "x"), "no-such-task")
r = tws.task_assign("t1", "production")
check("task_assign jas -> production", (r["from"], r["to"]), ("jas", "production"))
# THE PROPERTY, NOT THE SPELLING (2026-10-29): this asserted the literal "assigned: production", which pins the
# field name that the needs-key migration is deliberately changing. What the assertion exists to prove is that the
# assignment REACHED THE FILE and is readable from it -- so it now reads the file through the same reader the rest
# of the system uses, under whichever name the writer chose. It fails just as loudly if the write never lands.
# find() returns (meta, path), not a dict -- established by reading it (taskstore.py:254-258) after my first
# version assumed otherwise and raised AttributeError, which is the difference between a test that fails on the
# property and one that fails on the fixture.
_meta, _ = tws._ts().find("t1")
check("  the file records the assignment (read through the file, any field name)",
      tws._ts().assignee((_meta or {}).get("needs") or (_meta or {}).get("assigned")), "production")
check("  and it leaves Jas's list", [t["id"] for t in tws.my_tasks()["tasks"]], ["t3"])
check("task_assign: an unknown assignee is refused", refused(tws.task_assign, "t3", "bob"), "bad-assignee")
check("task_assign: unknown task refused", refused(tws.task_assign, "t997", "jas"), "no-such-task")
_tl = [r["tool"] for r in logged(tws)]
check("every task call is logged", all(t in _tl for t in ("my_tasks", "task_get", "task_note", "task_assign")), True)
check("all four are served over HTTP", all(t in HTTP_TOOLS for t in ("my_tasks", "task_get", "task_note", "task_assign")), True)

# THE RATCHET, not the two regressions above: a refusal rule that no test names is a message
# nobody has read since it was written, which is exactly how "not-a-file" went stale.
import ast as _ast
_src = (REPO_ROOT / "scripts" / "workspace_mcp" / "core.py").read_text()
_rules = set()
for _n in _ast.walk(_ast.parse(_src)):
    if isinstance(_n, _ast.Raise) and isinstance(_n.exc, _ast.Call) \
       and getattr(_n.exc.func, "id", "") == "Denied" and _n.exc.args:
        _a0 = _n.exc.args[0]
        if isinstance(_a0, _ast.Constant) and isinstance(_a0.value, str):
            _rules.add(_a0.value)
_untested = sorted(r for r in _rules if r not in TESTED_RULES)
# A RATCHET, not a ban -- the shape workspace/enforcement-ceiling.json uses, for the reason
# AGENTS.md rule 9 gives: a check that cannot be made to pass is one people learn to silence.
# These nine refusals were never TRIGGERED by any test on 2026-09-06. THE LIST MAY SHRINK
# AND MUST NEVER GROW. A new refusal with no test fails here -- which is precisely how
# "not-a-file" went stale unnoticed and then misled a model that had the right idea.
# (Note "not-a-file" survives on `replace`, where it is still accurate, and is still untested.)
UNTESTED_BASELINE = {
    "append-file-full", "append-outside-workspace", "append-too-large", "empty-text",
    "not-a-file", "not-committed", "not-generated", "target-exists", "too-large",
}
_new = sorted(set(_untested) - UNTESTED_BASELINE)
check(f"no NEW refusal rule goes untested ({len(_rules)} rules, "
      f"{len(_untested)} on the baseline)", _new, [])


print(f"\n{PASSED} passed, {FAILED} failed\n")
sys.exit(1 if FAILED else 0)
