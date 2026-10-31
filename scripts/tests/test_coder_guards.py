#!/usr/bin/env python3
"""Fixtures for the guards in coder.py — the ones that stop a false ACCEPT.

The agent's own promises are cheap to state and were each wrong once:

  * a patch that applies and changes nothing was ACCEPTED (`L-111`) — no suite can see it,
    because nothing broke, because nothing happened
  * an edit must not reach outside the sandbox, or into the corpus, or into a credential
  * the line-range shape (`t145`) must refuse a range the file does not have

These are the assertions that matter because they are refusals. "It applied the patch" was
never the risk.
"""
import re
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "projects" / "local-inference"))
import coder  # noqa: E402

PASSED = FAILED = 0


def _syntax_ok(src):
    """Does this still parse? The real failure was BAD-SYNTAX, so assert on the outcome."""
    import ast
    try:
        ast.parse(src)
        return True
    except SyntaxError:
        return False


def report(label, got, want):
    global PASSED, FAILED
    if got == want:
        PASSED += 1
        print(f"  [ok]   {label}: {got}")
    else:
        FAILED += 1
        print(f"  [BAD]  {label}: expected {want}, got {got}")


def main():
    tmp = Path(tempfile.mkdtemp())
    try:
        (tmp / "sub").mkdir()
        f = tmp / "sub" / "a.py"
        original = "one\ntwo\nthree\nfour\n"
        f.write_text(original, encoding="utf-8")
        baseline = {"sub/a.py": original}

        # ---- INDENTATION IS A TRANSCRIPTION SLIP, NOT A REASONING ERROR (2026-09-22).
        # Both shapes of it were the LAST thing three of the fourteen held chunks did before
        # giving up: an anchor whose whitespace differs from the file, and a line-range edit
        # supplied at the wrong depth ("unindent does not match any outer indentation
        # level" -- p41 chatui.py:782, p13 duo_verify.py:160, p17 feedback.py:1464). The
        # briefs were correct; the model could not reproduce a column. Fixtures are built
        # from the real duo_verify.py line that failed.
        print("Indentation slips are recovered, ambiguity is still refused:")
        ind = tmp / "sub" / "ind.py"
        body = ('class C:\n'
                '    def read(self, p):\n'
                '        return p.read_text(encoding="utf-8")[:9000]\n')
        ind.write_text(body, encoding="utf-8")
        # The model reproduces the right line at the wrong depth -- exactly p13's attempt 3.
        ok, _ = coder.apply_ops(tmp, [{"path": "sub/ind.py",
                                       "old": 'return p.read_text(encoding="utf-8")[:9000]',
                                       "new": 'return loudcap.cap(p.read_text())'}])
        got = ind.read_text(encoding="utf-8")
        report("an anchor off by indentation applies", ok, True)
        report("  and lands at the file's real depth",
               "        return loudcap.cap(p.read_text())" in got, True)
        report("  leaving the file parseable", _syntax_ok(got), True)

        # A line-range edit whose new text is at the wrong depth -- p13's attempt 4.
        ind.write_text(body, encoding="utf-8")
        coder.apply_ops(tmp, [{"path": "sub/ind.py", "from": 3, "to": 3,
                               "new": 'return loudcap.cap(p.read_text())'}])
        report("a line-range edit is re-indented to the line it replaces",
               _syntax_ok(ind.read_text(encoding="utf-8")), True)

        # AMBIGUITY IS STILL REFUSED. This is the load-bearing one: the recovery must never
        # become "pick the closest", which would silently edit the wrong place.
        two = tmp / "sub" / "two.py"
        two.write_text("if a:\n    x = 1\nif b:\n        x = 1\n", encoding="utf-8")
        ok, why = coder.apply_ops(tmp, [{"path": "sub/two.py", "old": "x = 1", "new": "x = 2"}])
        report("two regions differing only by whitespace are refused, not guessed", ok, False)
        report("  and the refusal says it matched more than once", "matched" in (why or ""), True)

        # A genuinely absent anchor is still a refusal; the retry forgives whitespace only.
        ok, why = coder.apply_ops(tmp, [{"path": "sub/ind.py",
                                         "old": "def nosuchmethod(self):", "new": "x"}])
        report("a line that is not there is still refused", ok, False)

        print("An edit that changes nothing must be visible as nothing:")
        coder.apply_ops(tmp, [{"path": "sub/a.py", "old": "two", "new": "two"}])
        report("a no-op leaves an empty diff",
               coder.diff_of(tmp, baseline).strip(), "")
        # The function coder.py actually gates acceptance on.
        report("changed() says nothing changed", coder.changed(tmp, baseline), False)
        # This is what coder.py checks before it will accept. The guard is that emptiness.
        report("and a real edit does not", bool(coder.apply_ops(
            tmp, [{"path": "sub/a.py", "old": "two", "new": "TWO"}])[0]), True)
        report("  producing a non-empty diff",
               bool(coder.diff_of(tmp, baseline).strip()), True)
        report("  and changed() agrees", coder.changed(tmp, baseline), True)
        f.write_text(original, encoding="utf-8")

        print("\nLine-range edits need no transcription, but must stay in range:")
        ok, detail = coder.apply_ops(tmp, [{"path": "sub/a.py", "from": 2, "to": 3,
                                            "new": "TWO\nTHREE"}])
        report("a valid range applies", ok, True)
        report("  and rewrites exactly those lines",
               f.read_text(), "one\nTWO\nTHREE\nfour\n")
        f.write_text(original, encoding="utf-8")
        ok, detail = coder.apply_ops(tmp, [{"path": "sub/a.py", "from": 3, "to": 99,
                                            "new": "x"}])
        report("a range past the end is refused", ok, False)
        report("  and the file is untouched", f.read_text(), original)

        print("\nThe sandbox boundary is enforced in code, not asked for in a prompt:")
        for label, path in (("escaping with ..", "../../etc/passwd"),
                            ("an absolute path", "/etc/passwd"),
                            ("the corpus", "chat/PROTOCOL.txt"),
                            ("a credential", "secrets/anthropic.env")):
            ok, _ = coder.apply_ops(tmp, [{"path": path, "old": "a", "new": "b"}])
            report(f"{label} is refused", ok, False)

        print("\nAll-or-nothing: a failing second op must undo the first:")
        ok, _ = coder.apply_ops(tmp, [
            {"path": "sub/a.py", "old": "one", "new": "ONE"},
            {"path": "sub/a.py", "old": "nowhere-in-the-file", "new": "x"}])
        report("the batch is refused", ok, False)
        report("  and the first op was rolled back", f.read_text(), original)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # --- the baseline must hold every extension diff_of walks ------------------------
    # L-114. diff_of skips any file the baseline does not hold, so a baseline narrower than
    # SOURCE_EXT makes whole languages unmeasurable: the edit lands, changed() returns False,
    # and the run is reported NO-OP whatever the patch did. Fourteen Kotlin attempts were
    # attributed to the model on the strength of that.
    print("\nThe baseline must cover every extension diff_of walks:")
    tmp = Path(tempfile.mkdtemp())
    try:
        kt = tmp / "app" / "Main.kt"
        kt.parent.mkdir(parents=True)
        kt.write_text("fun play() {\n    restart()\n}\n")
        (tmp / "keep.py").write_text("x = 1\n")

        narrow = {q.relative_to(tmp).as_posix(): q.read_text()
                  for q in list(tmp.rglob("*.py")) + list(tmp.rglob("*.md")) if q.is_file()}
        # coder.snapshot(), NOT a baseline rebuilt here. The first mutation of this bug was
        # MISSED because the suite built its own and never touched the code path that runs.
        full = coder.snapshot(tmp)

        report("a py-and-md baseline cannot see a .kt file", "app/Main.kt" in narrow, False)
        report("the SOURCE_EXT baseline can", "app/Main.kt" in full, True)

        ok, _ = coder.apply_ops(tmp, [{"path": "app/Main.kt",
                                       "old": "restart()", "new": "pauseOrResume()"}])
        report("the edit really applied", ok, True)
        # The refusal that matters, stated as the failure it was: a real change reported as none.
        report("a narrow baseline calls a real change NO-OP", coder.changed(tmp, narrow), False)
        report("  and SOURCE_EXT sees it", coder.changed(tmp, full), True)

        # Every extension diff_of walks must be snapshottable, or the same hole reopens
        # somewhere else the next time a language is added.
        missing = [e for e in coder.SOURCE_EXT
                   if e not in (".py", ".md") and e not in {"." + k.rsplit(".", 1)[-1]
                                                            for k in full}]
        report("no SOURCE_EXT entry is unreachable by the snapshot",
               [e for e in (".kt",) if e in missing], [])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # --- the ledger must name the MODEL, not the role -------------------------------
    # A run recorded as "local" is unattributable, and t151 is a comparison BETWEEN models.
    # Same shape as L-113: identify the subject by something unique to it, not by its label.
    print("\nModel attribution:")
    real = coder.subprocess
    # Pin the endpoint. resolve_model() branches on it, so inheriting CODER_ENDPOINT from the
    # environment made this suite pass or fail depending on how it was invoked -- it went red
    # inside coder.py's own sandbox the first time the Ollama endpoint was exported. A suite
    # whose verdict depends on an env var is not a suite.
    real_gw = coder.GATEWAY
    coder.GATEWAY = "http://127.0.0.1:4000/v1/chat/completions"

    class FakeProc:
        def __init__(self, names):
            self.names = names

        def run(self, args, **kw):
            out = "\n".join(self.names) if args[:2] == ["docker", "ps"] else "10.0.0.1"
            return type("R", (), {"stdout": out, "stderr": "", "returncode": 0})()

    def with_served(names, served_by_ip):
        import urllib.request as u
        coder.subprocess = FakeProc(names)
        orig = u.urlopen

        class Resp:
            def __init__(self, body):
                self.body = body

            def read(self):
                return self.body

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        u.urlopen = lambda url, **kw: Resp(
            __import__("json").dumps({"data": [{"id": served_by_ip}]}).encode())
        try:
            return coder.resolve_model("local")
        finally:
            u.urlopen = orig
            coder.subprocess = real

    report("a role resolves to the model actually serving",
           with_served(["vllm-general"], "nvidia/Qwen3.6-35B-A3B-NVFP4"),
           "nvidia/Qwen3.6-35B-A3B-NVFP4")
    # The refusal that matters: a 0.5B smoke model is resident beside the real one on this box
    # (see STATUS.md), and attributing a coding run to it would be a false record, not a slow one.
    report("the smoke model is NEVER reported as the coder",
           with_served(["vllm-smoke"], "Qwen/Qwen2.5-0.5B-Instruct") == "Qwen/Qwen2.5-0.5B-Instruct",
           False)
    report("  and it falls back to the role rather than guessing",
           with_served(["vllm-smoke"], "Qwen/Qwen2.5-0.5B-Instruct"), "local")
    coder.subprocess = real

    # The Ollama branch, which is the one the NVIDIA-playbook stack takes.
    coder.GATEWAY = "http://127.0.0.1:11434/v1/chat/completions"
    import urllib.request as _u
    _orig = _u.urlopen

    class _R:
        def __init__(self, b): self.b = b
        def read(self): return self.b
        def __enter__(self): return self
        def __exit__(self, *a): return False

    import json as _j
    _u.urlopen = lambda url, **kw: _R(_j.dumps({"models": [{"name": "qwen3.6:latest"}]}).encode())
    report("an Ollama endpoint names the loaded model",
           coder.resolve_model("local"), "ollama/qwen3.6:latest")

    def _boom(url, **kw):
        raise OSError("connection refused")

    _u.urlopen = _boom
    # The refusal: a server that does not answer must not be reported as a model.
    report("an Ollama server that does not answer is NOT given a model name",
           coder.resolve_model("local"), "ollama/unknown")
    _u.urlopen = _orig
    coder.GATEWAY = real_gw

    # --- write_branch must handle EVERY op shape apply_ops does -------------------------
    # It inlined the anchored replace and never learned the line-range shape added by t145,
    # so `--apply` died with KeyError: 'old' on an accepted, verified patch. Same producer /
    # consumer drift as L-114 and L-115. This asserts the two stay in step by construction.
    print("\nwrite_branch and apply_ops understand the same op shapes:")
    import inspect
    src = inspect.getsource(coder.write_branch)
    report("write_branch delegates to apply_ops", "apply_ops(" in src, True)
    # The refusal: it must NOT reach into op["old"] itself, which is what crashed.
    report("and does NOT index op['old'] on its own", 'op["old"]' in src, False)

    # And the shape that crashed must now round-trip through apply_ops without a KeyError.
    tmp2 = Path(tempfile.mkdtemp())
    try:
        f2 = tmp2 / "r.kt"
        f2.write_text("a\nb\nc\nd\n")
        ok, _ = coder.apply_ops(tmp2, [{"path": "r.kt", "from": 2, "to": 3, "new": "B"}])
        report("a line-range op applies without needing 'old'", ok, True)
        report("  and it really replaced the range", f2.read_text(), "a\nB\nd\n")
    finally:
        shutil.rmtree(tmp2, ignore_errors=True)

    # --- the review command must not misrepresent the branch ---------------------------
    # A worktree is cut from HEAD at run start. If main advances first, `git diff main..branch`
    # shows every newer commit as a deletion BY THE MODEL -- t016's 8-line Kotlin change was
    # rendered as 437 deletions including scripts/selfmatch.py. A reviewer sees vandalism, and
    # merging really would revert the newer work.
    print("\nThe review command it prints is accurate against a moved main:")
    wb = inspect.getsource(coder.write_branch)
    report("it diffs the branch against its own parent", "~1..{branch}" in wb, True)
    # The refusal: the misleading form must not be what it tells a human to run.
    report("it does NOT tell anyone to run `git diff main..branch`",
           'Review: git diff main..' in wb, False)
    report("and it warns that the branch may be behind main", "main has moved" in wb, True)

    # --- ask() must not send a vLLM-only flag to Ollama --------------------------------
    # chat_template_kwargs is silently ignored by Ollama, so the model reasoned without bound
    # and spent all 16,000 output tokens without emitting the JSON: measured 178s and an EMPTY
    # string. That is UNPARSEABLE x7 on the first Pareto. Ollama's own `think` is honoured and
    # returns thinking/content as separate fields.
    print("\nask() speaks each backend's own dialect:")
    asrc = inspect.getsource(coder.ask)
    report("it branches on the Ollama endpoint", '"11434" in GATEWAY' in asrc, True)
    report("it uses Ollama's native think field", '"think":' in asrc, True)
    report("it reads message.content, not a </think> split, on that path",
           'd.get("message")' in asrc, True)
    # The refusal: reasoning must NOT be silently turned off to make the JSON appear (D-30).
    # ASSERT THE BEHAVIOUR, NOT THE LITERAL. This checked for the exact source text
    # `"low" if think else False`, which broke the moment the level became configurable --
    # while the property it protects was untouched. A test that pins a string instead of a
    # rule fails on a safe change and passes on an unsafe one that happens to keep the text.
    #
    # The rule is: CODE never turns reasoning off by itself. Only the caller's `think`
    # argument can, which is what --no-think sets. Jas can now choose "off" from the
    # settings page (2026-08-31, his explicit request) -- that is a person deciding, which
    # is exactly what D-30 leaves room for; what it forbids is the harness quietly doing it
    # to make a parse succeed.
    report("reasoning is gated on the caller's think argument, nothing else",
           "if think else False" in asrc, True)
    report("  and the default level is not off",
           coder.ask.__defaults__ is not None and True in coder.ask.__defaults__, True)
    # And the vLLM path must keep the flag, or the other stack regresses.
    report("the vLLM path still sends chat_template_kwargs",
           "chat_template_kwargs" in asrc, True)

    # --- the file-request loop must accumulate, or it repeats forever ------------------
    # convo was rebuilt from `base` each round, so a second request produced a prompt
    # byte-identical to the first. At temperature 0 that is deterministic: t159 spent six
    # attempts emitting the same 2355 tokens and was guaranteed to. ASKED is the tallest bar
    # on the Pareto and this is why.
    print("\nThe file-request loop accumulates instead of repeating:")
    csrc = inspect.getsource(coder.main) if hasattr(coder, "main") else ""
    for path in (ROOT / "projects" / "local-inference" / "coder.py",):
        body = path.read_text()
    report("opened maps a path to its excerpt, not just a name",
           "opened = {}" in body, True)
    report("the prompt is rebuilt from EVERY opened file",
           "opened[r] for r in sorted(opened)" in body, True)
    # The refusal that matters: re-asking must be answered with a statement, not the same bytes.
    report("a repeat request is called out rather than re-served",
           "wastes an attempt" in body, True)   # contiguous fragment: the full
           # sentence is split across two source lines, so a source grep cannot see it whole
    # The first fix wired accumulation into the ASKED branch ONLY. The other five --
    # unparseable, refused, no-op, won't-compile, tests-failed -- still rebuilt from `base`
    # and silently dropped every file the model had asked for, so t017 alternated forever:
    # ask, receive, fail to parse, LOSE the files, ask again. One branch even claimed "the
    # files above are unchanged" when there were no files above.
    # A lower bound, not an exact count: adding a branch (BAD-SYNTAX did) must not fail a
    # test whose real subject is "none of them rebuild from base" -- asserted separately
    # below. An exact count here made a correct change look like a regression.
    report("EVERY branch composes through ctx(), not base", body.count("convo = ctx(") >= 6, True)
    # The refusal: no branch may rebuild from base and discard the opened files.
    report("no branch rebuilds from base",
           "convo = base" in body or "convo = (base" in body, False)
    # Driven with real values, not grepped: a source scan cannot distinguish
    # `base + files + tail` from `base + tail`, and that mutation was MISSED once.
    got = coder.compose("BASE", {"a.py": "AAA", "b.py": "BBB"}, "TAIL")
    report("compose keeps the base", "BASE" in got, True)
    report("compose keeps the tail", "TAIL" in got, True)
    report("compose includes EVERY opened file", ("AAA" in got and "BBB" in got), True)
    report("compose with nothing opened is just base+tail",
           coder.compose("BASE", {}, "TAIL"), "BASETAIL")

    # --- the baseline suite list must survive the file-request loop --------------------
    # `already` holds the suites that were failing BEFORE the model touched anything, so they
    # are never blamed on it. Adding repeat-file detection an hour earlier reused the same
    # name for a list of filenames, shadowing it -- after the first file request the
    # exclusion compared suite paths against filenames, matched nothing, and every
    # pre-existing failure was reported as the model breaking tests. The false negative the
    # baseline exists to prevent, reintroduced by the fix to a different bug.
    print("\nThe baseline suite list is not shadowed by the file-request loop:")
    report("the repeat list has its own name", "repeats = []" in body, True)
    report("and it says why in place", "that name holds the baseline suite list" in body, True)
    # The refusal: `already` must only ever be assigned from run_suites.
    #
    # PINNED ON THE CALLEE, NOT ON THE WHOLE LINE (2026-09-16). This compared the line to the
    # literal `already, _ = run_suites(root)`, so adding an ARGUMENT broke it -- which is what
    # happened when run_suites gained `declared=` to stop a one-off flake refusing a dispatch.
    # That is a false alarm, and a guard that cries wolf on a legitimate refactor is one people
    # learn to edit, which is how it stops guarding (AGENTS.md rule 9). What this exists to
    # catch is `already` being REBOUND to something that is not the baseline suite list -- the
    # shadowing bug described above -- and that is caught by pinning the callee and the count.
    #
    # THE SECOND VALUE MAY BE NAMED (2026-10-24). The refusal prints the failing suites' names
    # and used to discard their OUTPUT with `_`, which is why eleven refusals on 2026-10-24
    # left no cause on disk. Binding it (`already, already_out = run_suites(...)`) is the same
    # callee, the same single assignment, and the same value -- what this guard forbids is
    # `already` being rebound elsewhere, which the count still catches.
    assigns = [l.strip() for l in body.splitlines()
               if re.match(r"\s*already\s*(,\s*\w+\s*)?=", l)]
    report("`already` is assigned exactly once", len(assigns), 1)
    report("`already` is assigned from run_suites and nothing else",
           all(re.match(r"already\s*(,\s*\w+\s*)?= run_suites\(", a) for a in assigns), True)

    # --- a repeated file request must serve the UNSEEN part ----------------------------
    # numbered() caps the excerpt, so feedback.py (1,088 lines) was shown as lines 1-1001 and
    # t159's target sits at 1,078. Serving the identical window again turned a reasonable
    # request -- "show me more" -- into a seven-attempt loop.
    print("\nA repeated file request advances through the file:")
    tmp3 = Path(tempfile.mkdtemp())
    try:
        big = tmp3 / "big.py"
        big.write_text("\n".join(f"line{i}" for i in range(1, 501)) + "\n")
        first = coder.unseen(tmp3, "big.py", 0, cap=200)
        report("a first slice starts at line 1", "lines 1-" in first[0], True)
        second = coder.unseen(tmp3, "big.py", first[1], cap=200)
        report("a second slice starts after the first", second[1] > first[1], True)
        report("  and does not repeat the first slice's text",
               first[0].splitlines()[1] in second[0], False)
        # The refusal: once the whole file has been shown there is nothing left to serve,
        # and it must say so rather than loop back to the beginning.
        report("past the end of the file it returns None",
               coder.unseen(tmp3, "big.py", 500), None)
        report("a missing file returns None, never an empty slice",
               coder.unseen(tmp3, "nope.py", 0), None)
        # And the CALL SITE must use it. Driving unseen() proves the capability; it does not
        # prove the loop calls it. That distinction has now cost four MISSED mutations in one
        # session (L-102's shape), so it is asserted explicitly every time.
        report("the repeat branch actually calls unseen()",
               "more = unseen(root, rel, shown_to.get(rel, 0))" in body, True)
        report("  and appends the slice to what the model already has",
               'opened[rel] += "\\n" + more[0]' in body, True)
    finally:
        shutil.rmtree(tmp3, ignore_errors=True)

    # --- a failed attempt must record what it tried ------------------------------------
    # A run that ends in TESTS-FAILED is the one most worth reviewing, and it recorded no ops
    # at all: "what did the model actually try?" had no answer in the ledger, the log or the
    # task note. Review without the diff is guesswork.
    print("\nEvery attempt records its ops, not just the accepted one:")
    report("TESTS-FAILED carries ops",
           '"broke": new_failures, "ops": obj.get("ops")' in body, True)
    report("REFUSED carries ops", '"outcome": "REFUSED",\n                              "detail": detail, "ops": obj.get("ops")' in body, True)
    report("the trail keeps every attempt's ops", '"ops": t.get("ops")' in body, True)
    report("and the failing test output", '"output": str(t.get("output", ""))' in body, True)

    # --- the model gets the scoping and the latest review, not the whole history --------
    # A task note accumulates every review AND every run record the model writes about
    # itself. t159 reached 9,916 chars -- comparable to a whole file excerpt -- describing
    # three rejected approaches at once. The run then burned 16,000 tokens reasoning without
    # emitting JSON. Starving the model was the old failure (L-114); this is flooding it.
    print("\nThe brief carries the scoping and the newest review, not everything:")
    body_src = (ROOT / "projects" / "local-inference" / "coder.py").read_text()
    NOTES = ("statement here\n\n## Notes\n\n"
             "**2026-08-01 · claude-code** — SCOPING: do the thing in foo.py\n\n"
             "**2026-08-02 · local/qwen3.6** — **ASKED** after 7 attempt(s).\n\n"
             "**2026-08-03 · claude-code** — SENT BACK: wrong approach A\n\n"
             "**2026-08-04 · local/qwen3.6** — **ACCEPTED** after 2 attempt(s).\n\n"
             "**2026-08-05 · claude-code** — SENT BACK: use approach C\n\n"
             "**2026-08-06 · local/qwen3.6** — **UNPARSEABLE** after 7 attempt(s).\n\n"
             "## Attachments\n")
    b = coder.latest_brief(NOTES)
    report("keeps the task statement", "statement here" in b, True)
    report("keeps the SCOPING note", "do the thing in foo.py" in b, True)
    report("keeps the NEWEST review", "use approach C" in b, True)
    # The refusals: the middle dead end and the model's own run records are noise.
    report("drops the superseded middle review", "wrong approach A" in b, False)
    report("drops the model's own run records", "UNPARSEABLE** after" in b, False)
    # Size only means something at realistic scale: on a tiny fixture the explanatory header
    # dominates. Real notes run to thousands of characters, which is the case that hurt.
    BIG = NOTES.replace("wrong approach A", "wrong approach A. " + ("detail. " * 400))
    big_out = coder.latest_brief(BIG)
    report("a long superseded review is dropped, not summarised",
           len(big_out) < len(BIG) * 0.5, True)
    report("a note-free body is returned unchanged",
           coder.latest_brief("just a statement"), "just a statement")
    # L-117, fifth occurrence: driving the helper proves the helper. Assert the CALL SITE.
    report("and the brief the model receives actually goes through it",
           'brief = f"{title}\\n\\n{latest_brief(body)}"' in body_src, True)

    # --- the excerpt budget must match the context the model actually has --------------
    # The cap was 14,000 chars against a 262,144-token window -- 1.4% of it, and a quarter of
    # one file in this repository. Reading coder.py took five slices, so both models spent six
    # of seven attempts reading rather than editing. t096's rated-versus-served context, with
    # nothing announcing the gap.
    print("\nThe excerpt budget fits this repository's files:")
    report("the cap is a named constant, not a literal", hasattr(coder, "EXCERPT_CAP"), True)
    report("and it is large enough for the biggest file here",
           coder.EXCERPT_CAP >= 100_000, True)
    # The refusal: not unlimited. An excerpt that buries the task is its own failure, which is
    # why latest_brief exists.
    report("but not unbounded", coder.EXCERPT_CAP <= 400_000, True)
    # A FILE THAT FITS IS SHOWN WHOLE; A FILE THAT DOES NOT SAYS SO, LOUDLY (L4, 2026-10-27).
    # This loop asserted "shown whole" for BOTH files. feedback.py has since grown to 4448
    # lines: numbered() renders it at 245,782 + 7 per line = 276,918 chars, 16,918 over
    # EXCERPT_CAP, so it takes the excerpt path and stops at line 4434 of 4448. That is not a
    # regression to fix -- showing a 69k-token file whole would starve the model worse -- and
    # the property that matters for a file this size is never-truncate-a-record: the output
    # must NAME the truncation rather than leave the model believing it saw the whole file.
    # Asserted exactly, so a silent truncation still fails here.
    for rel in ("projects/local-inference/coder.py", "projects/app-repo/feedback.py"):
        src = (ROOT / rel).read_text()
        out = coder.numbered(src, task_words=["numbered", "app", "line"])
        shown = [int(m) for m in re.findall(r"^\s*(\d+)\s{2}", out, re.M)]
        total = len(src.splitlines())
        whole = len(src) + 7 * total < coder.EXCERPT_CAP
        if whole:
            report(f"  {rel.split('/')[-1]} fits the budget and is shown whole",
                   max(shown) >= total, True)
        else:
            report(f"  {rel.split('/')[-1]} does not fit, so it says it is an excerpt",
                   out.splitlines()[0].startswith("(EXCERPT") and str(total) in out.splitlines()[0],
                   True)
            report(f"    and it does not pretend to be whole", max(shown) >= total, False)
    report("unseen() shares the same budget",
           "cap=EXCERPT_CAP" in body, True)

    # --- Python needs an oracle too -----------------------------------------------------
    # Kotlin has had compiles() since 2026-08-25; Python had nothing, so a patch that broke
    # indentation reached the suites and came back as an import traceback three frames deep.
    # Line-range edits replace whole lines, so the model supplies the indentation -- which
    # makes this the failure that shape invites, and qwen3.8 produced it twice in one run.
    print("\nA patch that leaves Python unparseable is caught before the suites:")
    tmp4 = Path(tempfile.mkdtemp())
    try:
        (tmp4 / "m.py").write_text("def f():\n    return 1\n")
        ok, msg = coder.py_syntax(tmp4, [{"path": "m.py"}])
        report("valid Python passes", ok, True)

        (tmp4 / "m.py").write_text("def f():\n    return 1\n      oops\n")
        ok, msg = coder.py_syntax(tmp4, [{"path": "m.py"}])
        # The refusal that matters, and it must be actionable: line number and reason.
        report("broken indentation is REFUSED", ok, False)
        report("  and the message names the line", "line 3" in msg, True)
        report("  and says why", "indent" in msg.lower(), True)

        # Non-Python files must not be dragged through a Python parser.
        (tmp4 / "a.kt").write_text("fun f() { !!! }\n")
        report("a .kt file is not parsed as Python",
               coder.py_syntax(tmp4, [{"path": "a.kt"}])[0], True)
        report("a missing file is not an error",
               coder.py_syntax(tmp4, [{"path": "gone.py"}])[0], True)
    finally:
        shutil.rmtree(tmp4, ignore_errors=True)
    # L-117: assert the CALL SITE, not just the helper.
    report("the run loop actually calls it", "ok_py, py_err = py_syntax(root," in body, True)
    report("  and reverts the file when it fails", 'outcome": "BAD-SYNTAX"' in body, True)

    # --- four false verdicts, found by the env3 probe on 2026-08-26 ---------------------
    print("\nFalse verdicts the env3 probe found:")

    # [1] parse() counted braces in the raw text, so a `}` inside a string value ended the
    # object early and valid JSON was reported UNPARSEABLE. Most patches contain braces.
    o, e = coder.parse('{"why": "close the brace }", "ops": [{"path":"a.py","old":"x","new":"y"}]}')
    report("a brace inside a string value no longer breaks parse()", o is not None, True)
    o2, _ = coder.parse('{"why":"ok","ops":[{"path":"a.py","old":"if x: {","new":"}"}]}')
    report("  nor braces inside op values", o2 is not None, True)
    # The refusal must survive: genuinely broken JSON is still refused.
    o3, e3 = coder.parse('{"why": "unterminated')
    report("  and genuinely broken JSON is still REFUSED", o3, None)

    # [2] SOURCE_EXT was too narrow: a .toml patch applied, changed the file, and was
    # reported NO-OP. L-114 one layer down -- widening the baseline did nothing about the list.
    tmp5 = Path(tempfile.mkdtemp())
    try:
        (tmp5 / "c.toml").write_text("a = 1\n")
        b5 = coder.snapshot(tmp5)
        coder.apply_ops(tmp5, [{"path": "c.toml", "old": "a = 1", "new": "a = 2"}])
        report("a .toml change is visible to changed()", coder.changed(tmp5, b5), True)
        for ext in (".toml", ".cfg", ".ini", ".txt", ".js", ".html"):
            report(f"  {ext} is in SOURCE_EXT", ext in coder.SOURCE_EXT, True)
    finally:
        shutil.rmtree(tmp5, ignore_errors=True)

    # [3] Three branches said "That change has been REVERTED"; only one reverted. The next
    # attempt then anchored against a listing that no longer matched disk -- the REFUSED bar.
    tmp6 = Path(tempfile.mkdtemp())
    try:
        f6 = tmp6 / "a.py"
        f6.write_text("x = 1\n")
        b6 = coder.snapshot(tmp6)
        coder.apply_ops(tmp6, [{"path": "a.py", "old": "x = 1", "new": "x = 2"}])
        report("the file really changed first", f6.read_text().strip(), "x = 2")
        restored = coder.revert(tmp6, b6)
        report("revert() puts it back", f6.read_text().strip(), "x = 1")
        report("  and says what it restored", restored, ["a.py"])
        report("  and reverting a clean tree restores nothing",
               coder.revert(tmp6, b6), [])
    finally:
        shutil.rmtree(tmp6, ignore_errors=True)
    # L-117: assert the CALL SITES. Every branch claiming a revert must perform one.
    # Counted precisely, not by grepping a word: revert()'s own docstring says "REVERTED"
    # and its `def` line matches the call pattern, so a naive count is confounded. The three
    # branches that tell the model a revert happened are BAD-SYNTAX, LIBRARY-BLOCK (added
    # 2026-09-23), WONT-COMPILE, BAD-SHAPE (added 2026-10-01, code_shape) and TESTS-FAILED, and
    # each must call it.
    call_lines = [l.strip() for l in body.splitlines()
                  if l.strip() == "revert(root, baseline)"]
    report("all five branches that claim a revert perform one", len(call_lines), 5)
    for branch in ("the Python no longer parses", "the Kotlin no longer compiles",
                   "broke {len(new_failures)} test suite"):
        i = body.find(branch)
        report(f"  a revert follows '{branch[:28]}'",
               "revert(root, baseline)" in body[i:i + 700], True)

    # [4] SECRET_RE matched `token` as a substring, refusing Token.kt and tokenise.py.
    report("a source file named Token.kt is allowed",
           bool(coder.SECRET_RE.search("app/core/Token.kt")), False)
    # A file whose NAME merely contains the letters, in any directory, must be allowed.
    report("  and any source file whose name merely contains the letters",
           bool(coder.SECRET_RE.search("scripts/tokeniser_helper.py")), False)
    # The refusals that must NOT be lost -- this guard exists to stop exactly these.
    for p6 in (".secrets/anthropic.env", "a/secret/b.txt", "tokens/live.json",
               "x/id_ed25519", "conf/keystore.jks", "app/my.pem", "credentials.json"):
        report(f"  {p6} is still REFUSED", bool(coder.SECRET_RE.search(p6)), True)

    # --- the note must not claim a suite that was never named --------------------------
    # Every ACCEPTED run told Jas "The target suite is green" -- including all five branches
    # awaiting review on 2026-08-26, none of which named a suite. The terminal said acceptance
    # was WEAKER; the note said it was stronger. The false claim landed in the one channel
    # written for him.
    print("\nThe task note does not claim a target suite that never existed:")
    rsrc = inspect.getsource(coder.record)
    report("record() takes the target as a parameter", "target=None" in rsrc, True)
    report("it claims a green suite only when one was named",
           'if target else' in rsrc, True)
    report("and says plainly when none was", "NO TARGET SUITE was named" in rsrc, True)
    # The refusal: acceptance without a suite must not read as verification of the goal.
    report("  and that acceptance does not mean the task was achieved",
           "does NOT mean the change does what the task asked" in rsrc, True)
    # L-117: every call site must pass it, or the parameter defaults to None and the note
    # silently takes the weaker branch even when a suite WAS named.
    calls = [l for l in body.splitlines() if "record(a.task" in l or "record(a.task," in l]
    passes = [l for l in body.splitlines() if "target=target)" in l]
    report("every record() call site passes the target", len(passes) >= 3, True)

    # --- an extension list must never decide whether a change is visible ----------------
    # THE THIRD APPEARANCE. L-114 was the baseline narrower than diff_of's list; the probe
    # then found .toml missing from that list; widening it to 24 extensions moved the
    # boundary rather than removing it, and the probe found ten more at round 64. An
    # allow-list is only ever as complete as the last person who thought about it.
    print("\nNO-OP is decided by the ops, not by a file extension:")
    tmp7 = Path(tempfile.mkdtemp())
    try:
        names = ["f.rs", "f.go", "f.rb", "f.php", "f.c", "f.swift", "f.tf", "Dockerfile"]
        for nm in names:
            (tmp7 / nm).write_text("a = 1\n")
        baseline7 = coder.snapshot(tmp7)          # as the run takes it: before any op exists
        missed = []
        for nm in names:
            ops = [{"path": nm, "old": "a = 1", "new": "a = 2"}]
            pre = coder.snapshot_paths(tmp7, ops)
            coder.apply_ops(tmp7, ops)
            if not coder.changed(tmp7, {**baseline7, **pre}, ops):
                missed.append(nm)
            (tmp7 / nm).write_text("a = 1\n")
        report("no extension is invisible to changed()", missed, [])
        report("  none of them is in SOURCE_EXT either",
               [n for n in names if any(n.endswith(e) for e in coder.SOURCE_EXT)], [])
        # The refusal that must survive: a patch that changes nothing is still NO-OP, and on
        # a file the extension list has never heard of.
        ops = [{"path": "f.rs", "old": "a = 1", "new": "a = 1"}]
        pre = coder.snapshot_paths(tmp7, ops)
        coder.apply_ops(tmp7, ops)
        report("a genuine no-op is still caught on an unlisted extension",
               coder.changed(tmp7, {**baseline7, **pre}, ops), False)
        report("snapshot_paths reads the file as it is before the write",
               coder.snapshot_paths(tmp7, [{"path": "f.go"}])["f.go"].strip(), "a = 1")
        report("  and skips a path that does not exist",
               coder.snapshot_paths(tmp7, [{"path": "nope.rs"}]), {})
    finally:
        shutil.rmtree(tmp7, ignore_errors=True)
    # L-117: the run loop must actually take the pre-state and merge it.
    report("the run loop captures pre-state", "pre = snapshot_paths(root," in body, True)
    report("  and merges it into the baseline it compares against",
           "changed(root, {**baseline, **pre}, obj[\"ops\"])" in body, True)

    # --- the diff a human reviews must show every file the patch touched ----------------
    # FOURTH appearance, and this time in the half I called harmless. Removing the extension
    # list from changed() fixed correctness; I then wrote that SOURCE_EXT "survives only as a
    # display concern". The diff IS the review artifact: a .rs change came back ACCEPTED with
    # an EMPTY diff, so the verdict claimed a change and showed nothing for it, and a
    # reviewer approves what they can see.
    print("\nThe reviewed diff shows every file the ops touched:")
    tmp8 = Path(tempfile.mkdtemp())
    try:
        for nm in ("lib.rs", "main.go", "Dockerfile", "app.py"):
            (tmp8 / nm).write_text("x = 1\n")
        base8 = coder.snapshot(tmp8)
        empty = []
        for nm in ("lib.rs", "main.go", "Dockerfile", "app.py"):
            ops = [{"path": nm, "old": "x = 1", "new": "x = 2"}]
            pre = coder.snapshot_paths(tmp8, ops)
            coder.apply_ops(tmp8, ops)
            if nm not in coder.diff_of(tmp8, {**base8, **pre}, ops):
                empty.append(nm)
            (tmp8 / nm).write_text("x = 1\n")
        report("no accepted change is shown with an empty diff", empty, [])
        # The refusal: a file nobody touched must NOT be dragged into the review diff.
        ops = [{"path": "lib.rs", "old": "x = 1", "new": "x = 2"}]
        pre = coder.snapshot_paths(tmp8, ops)
        coder.apply_ops(tmp8, ops)
        d8 = coder.diff_of(tmp8, {**base8, **pre}, ops)
        report("  and an untouched file stays out of it", "main.go" in d8, False)
        report("  while the touched one is in it", "lib.rs" in d8, True)
    finally:
        shutil.rmtree(tmp8, ignore_errors=True)
    # L-117: the call site must pass the ops, or the default None restores the old behaviour.
    report("the run loop passes ops to diff_of",
           'patch = diff_of(root, baseline, obj["ops"])' in body, True)

    print("\nA named target suite that does not exist:")
    for label, got, want in target_must_exist_cases():
        report(label, got, want)
    # TWO RUNS AT ONCE MUST NOT COLLIDE. sandbox() named the directory by the timestamp at
    # second resolution, so two dispatches started in the same second produced the same path
    # and the second died with FileExistsError -- parallel dispatch, the point of a batch,
    # crashed one of its own tasks. env3 probe pass 6 was aimed at exactly this class and I
    # hit it by dispatching two tasks together.
    print("\nConcurrent sandboxes do not collide:")
    import inspect as _insp
    _src = _insp.getsource(coder.sandbox)
    _uniq = "getpid" in _src or "mkdtemp" in _src
    print(f"  [{'ok' if _uniq else 'FAIL'}]   the sandbox path is unique per process: {_uniq}")
    if not _uniq:
        fails.append("sandbox() names its directory by time alone; two runs in one second collide")


    print(f"\n{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


def target_must_exist_cases():
    """A NAMED target suite that is not on disk must stop the run.

    The guard written for the no-target case asks `if target`, and a typo is truthy, so it
    could not see this. run_suites(root) enumerates by GLOBBING scripts/tests/test_*.py, so
    a target that does not exist is never in `failed`, `target in failed` is False, nothing
    forces it into new_failures, and the run is ACCEPTED with a note claiming "The target
    suite is green" about a suite that was never executed.

    env3 probe, round 53 of the targeted round 5.
    """
    import tempfile
    from pathlib import Path as _P
    out = []
    root = _P(tempfile.mkdtemp())
    (root / "scripts" / "tests").mkdir(parents=True)
    (root / "scripts" / "tests" / "test_real.py").write_text("import sys;sys.exit(0)\n")

    missing = "scripts/tests/test_TYPO.py"
    out.append(("the glob cannot see a suite that is not on disk",
                missing in coder.suites(root), False))
    failed, _ = coder.run_suites(root)
    out.append(("so an absent target never appears in `failed`",
                missing in failed, False))
    # Which is exactly why presence must be checked BEFORE the run, not inferred after it.
    out.append(("and the file really is absent, so the check is meaningful",
                (root / missing).is_file(), False))
    out.append(("a real suite IS found by the glob",
                "scripts/tests/test_real.py" in coder.suites(root), True))

    # Drive the REAL entry point, not the ingredients. Asserting that the glob misses the
    # file proves the mechanism and not the guard: the first version of this test passed
    # while the mutation that deletes the refusal went MISSED, because nothing here actually
    # ran coder.py. That is L-117 -- the test drove the helper and the mutation killed the
    # call site.
    import subprocess as _sp, sys as _sys
    # WHERE coder.py BUILDS THEM: TMPDIR, which run_tests sets per suite (t415). Counting /tmp
    # under a sweep counted a directory nothing wrote to, and passed without measuring (t286).
    # A PRIVATE TMPDIR FOR THE CHILD (2026-10-01): this cleanup removed every NEW coder-* under the
    # shared temp dir -- including a LIVE build's sandbox created meanwhile, which then crashed with
    # FileNotFoundError (p234, t469). The child writes only here, so only its own leftovers can go.
    import tempfile as _tf
    import os as _os
    TMP = _P(_tf.mkdtemp(prefix="coder-guards-"))
    before_set = set(TMP.glob("coder-[0-9]*/"))
    sandboxes_before = len(before_set)
    repo = _P(__file__).resolve().parents[2]
    r = _sp.run([_sys.executable, str(repo / "projects" / "local-inference" / "coder.py"),
                 "--task", "t029", "--target", "scripts/tests/test_TYPO.py"],
                capture_output=True, text=True, timeout=180, cwd=str(repo),
                env=dict(_os.environ, TMPDIR=str(TMP)))
    body = r.stdout + r.stderr
    out.append(("coder.py REFUSES to start", r.returncode == 2, True))
    # And it must refuse WITHOUT building a sandbox. The check first sat after
    # `root = sandbox()`, so each refusal copied the repo and abandoned it -- and because
    # this very test spawns coder.py, a suite running inside a sandbox spawned that
    # sandbox's coder.py, which built another. 279 copies and 55 GB of /tmp before anyone
    # looked. A guard whose test is a fork bomb is not a guard.
    out.append(("  and builds NO sandbox doing it",
                len(list(TMP.glob("coder-[0-9]*/"))), sandboxes_before))

    # Clean up anything this test caused, INCLUDING under the mutation that deliberately
    # puts sandbox() back into the condition. Without this the registry leaves one ~200 MB
    # copy behind per full run -- small, silent, and the same slow accumulation that reached
    # 55 GB. A test that litters is a test nobody wants to run often.
    # sandbox() must refuse to nest, and this must hold WITHOUT relying on the refusal
    # above -- a mutation that disables that refusal is precisely what re-enabled the
    # recursion after the first fix. Drive it by making REPO look like a sandbox.
    real_repo = coder.REPO
    coder.REPO = _P("/tmp/coder-1234567890")
    try:
        nested = False
        try:
            coder.sandbox()
        except SystemExit:
            nested = True
        except Exception:
            nested = False
        out.append(("sandbox() refuses to build one from inside a sandbox", nested, True))
        # 2026-10-20: sandboxes moved to ~/.cache/hivemind-tmp (99178d32e); the check knew only /tmp
        coder.REPO = _P.home() / ".cache" / "hivemind-tmp" / "coder-1234567890-1"
        nested2 = False
        try:
            coder.sandbox()
        except SystemExit:
            nested2 = True
        except Exception:
            nested2 = False
        out.append(("  and from inside one on disk (~/.cache/hivemind-tmp)", nested2, True))
    finally:
        coder.REPO = real_repo

    # Clean up LAST, after every case. Doing it in the middle left the empty directory that
    # sandbox() mkdirs before it copies -- which is what the nesting case above creates when
    # the mutation disables the check. One 4 KB directory per registry run: too small to
    # notice and the same accumulation that reached 55 GB.
    import shutil as _sh
    for d in set(TMP.glob("coder-[0-9]*/")) - before_set:
        _sh.rmtree(d, ignore_errors=True)
    _sh.rmtree(TMP, ignore_errors=True)
    out.append(("  and says which suite is missing", "test_TYPO.py" in body, True))
    out.append(("  and never reports a verdict", "ACCEPTED" not in body, True))
    return out


if __name__ == "__main__":
    sys.exit(main())
