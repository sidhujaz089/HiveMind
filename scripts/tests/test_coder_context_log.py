#!/usr/bin/env python3
"""t774: every coder.py prompt assembly must leave a row in a prompt-context ledger.

The RCA this pins (workspace/rca/1a6f380892c7.md, dead end D-2, and the T3 claim in
workspace/rca/3d9cf41e0896.md): nothing records WHICH context a coder.py run was served, so
"were the lessons injected?" is unprovable and `grep -c hot coder-context.jsonl` cannot be run.
This suite is that writer's fixture. It is red while the writer is absent, and it also goes red
if the writer exists but (a) ask() does not call it, (b) records a clipped prompt_chars,
(c) drops a required field, (d) swallows its own write errors into silence, (e) overwrites the
ledger instead of appending, (f) writes its row only AFTER the request, so an attempt that dies
at the socket leaves no trace, or (g) fills lessons_injected with something that is not a list.

No model, no live server, no network, and nothing written into the repository: CODER_ENDPOINT
steers coder.py onto the gateway branch (the native branch is chosen by "11434" in GATEWAY),
urllib.request.urlopen is replaced by a stub that REFUSES any URL it was not written to answer,
every ask() call passes an explicit timeout= so nothing derives one by probing a server, and
coder._tel is replaced by a no-op -- this suite pins prompt-context recording, not run telemetry,
and a telemetry append would make the run dirty the worktree.

Run: python3 scripts/tests/test_coder_context_log.py
"""
import importlib.util
import inspect
import io
import json
import os
import re
import sys
import tempfile
import urllib.error
import urllib.request
from contextlib import redirect_stderr
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]          # scripts/tests -> the repository root
CODER_PATH = REPO / "projects" / "local-inference" / "coder.py"

# An explicit, non-Ollama endpoint. coder.py's module-level gateway resolution returns
# CODER_ENDPOINT verbatim ("honoured even if it looks dead"), so importing coder.py sends no
# probe and ask() takes the OpenAI-shape gateway branch (coder.py:1085-1112).
os.environ["CODER_ENDPOINT"] = "http://127.0.0.1:4000/v1/chat/completions"

_failures = []


def check(label, cond, detail=""):
    if cond:
        print(f"  ok  {label}")
    else:
        print(f"  FAIL {label}" + (f" -- {detail}" if detail else ""))
        _failures.append(label)


def load_coder():
    """Import coder.py from the path it really lives on, so settings/console resolve as in production."""
    for p in (str(CODER_PATH.parent), str(REPO / "scripts")):
        if p not in sys.path:
            sys.path.insert(0, p)
    spec = importlib.util.spec_from_file_location("coder_under_test", CODER_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _Resp:
    """The one reply this stub answers: a clean gateway completion, finish_reason=stop."""

    def __enter__(self):
        return io.BytesIO(json.dumps({
            "choices": [{"message": {"content": '{"ops": []}', "role": "assistant"},
                         "finish_reason": "stop"}],
            "usage": {"completion_tokens": 7, "prompt_tokens": 11},
        }).encode())

    def __exit__(self, *a):
        return False


_seen = []
_boom = []


def _fake_urlopen(req, timeout=None):
    # A stub must REFUSE traffic it was not written to answer, not serve the next scripted reply.
    url = getattr(req, "full_url", str(req))
    _seen.append(url)
    if not url.endswith("/v1/chat/completions"):
        raise AssertionError(f"unexpected request to {url}")
    assert isinstance(timeout, (int, float)) and timeout > 0, f"urlopen called with timeout={timeout!r}"
    if _boom:
        raise urllib.error.URLError("simulated socket death")
    return _Resp()


def read_rows(path):
    rows = []
    if not path.is_file():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def main():
    print(f"test_coder_context_log -- coder.py at {CODER_PATH}")
    coder = load_coder()

    # No telemetry writes into the repository: this suite records prompt context, not run
    # measurements. (coder.py's gateway branch calls _tel() on every call.)
    coder._tel = lambda *a, **k: None

    # 1. THE WRITER EXISTS AND IS REACHABLE. Absent today -- this is the red on main.
    for name in ("CONTEXT_LOG", "RUN_CONTEXT", "log_prompt_context"):
        check(f"coder.py defines {name}", hasattr(coder, name), f"absent from {CODER_PATH.name}")
    if not hasattr(coder, "log_prompt_context"):
        return                        # nothing below is testable while the writer does not exist

    tmp = Path(tempfile.mkdtemp(prefix="t774-"))
    log = tmp / "coder-context.jsonl"
    coder.CONTEXT_LOG = log
    coder.RUN_CONTEXT = {"task": "t774", "brief": "patch scripts/foo.py only",
                         "files": ["scripts/foo.py"], "target": "scripts/tests/test_x.py"}

    # The budget a call is recorded with is read off ask() itself, never hard-coded here: a writer
    # that wrote a constant would pass a single-value test. (ask's third positional is `think`,
    # so the budget is always passed by keyword.)
    try:
        default_budget = inspect.signature(coder.ask).parameters["max_tokens"].default
    except (TypeError, ValueError, KeyError):
        default_budget = None
        check("ask() has a max_tokens parameter (the logger records it)", False,
              "inspect.signature(coder.ask) has no 'max_tokens' parameter")

    real_urlopen = urllib.request.urlopen
    _seen.clear()
    urllib.request.urlopen = _fake_urlopen
    try:
        prompt = "TASK\npatch scripts/foo.py\n\nFiles you have already been shown:\n1  x"
        coder.ask(prompt, "tester-model", timeout=120)
        rows = read_rows(log)

        # 2. ONE ROW PER PROMPT ASSEMBLY, WRITTEN BY ask() ITSELF -- not by a test calling a
        #    helper. A writer nobody calls is exactly the defect the RCA names.
        check("ask() appended exactly one row", len(rows) == 1, f"rows={len(rows)} at {log}")
        if not rows:
            return
        r = rows[0]

        # 3. THE FIELDS THE RCA NAMED, each carrying a usable value -- not shape, use.
        check("ts is a UTC ISO-8601 stamp",
              bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", str(r.get("ts")))),
              f"ts={r.get('ts')!r}")
        check("task is the run's label from RUN_CONTEXT", r.get("task") == "t774", f"task={r.get('task')!r}")
        check("model is the model asked", r.get("model") == "tester-model", f"model={r.get('model')!r}")
        check("language derived from the task text: python", r.get("language") == "python",
              f"language={r.get('language')!r}")
        check("prompt_chars is the FULL prompt length, not a cap",
              r.get("prompt_chars") == len(prompt), f"{r.get('prompt_chars')!r} != {len(prompt)}")
        check("max_tokens is the budget ask() was called with",
              r.get("max_tokens") == default_budget, f"{r.get('max_tokens')!r} != {default_budget!r}")
        check("num_ctx is the module's served window", r.get("num_ctx") == coder.NUM_CTX,
              f"{r.get('num_ctx')!r} != {coder.NUM_CTX!r}")
        check("files is the run's file list", r.get("files") == ["scripts/foo.py"], f"files={r.get('files')!r}")
        check("target is the run's target", r.get("target") == "scripts/tests/test_x.py",
              f"target={r.get('target')!r}")
        check("lessons_injected is a list (empty today -- the slot the next task fills)",
              r.get("lessons_injected") == [], f"lessons_injected={r.get('lessons_injected')!r}")
        # The RCA's closure test is `grep -c hot coder-context.jsonl`. That means nothing unless
        # every row records the size of the context block that call was served with. A bool (not
        # a string) for availability, so a test environment where console.startup() fails takes
        # the fail-soft branch and the suite is not red for that.
        check("system_chars > 0 (the system block this call was served with was measured)",
              isinstance(r.get("system_chars"), int) and r.get("system_chars") > 0,
              f"system_chars={r.get('system_chars')!r}")
        check("startup_available is a bool (empty startup vs unavailable startup)",
              isinstance(r.get("startup_available"), bool), f"startup_available={r.get('startup_available')!r}")

        # 4. A SECOND CALL: append, not overwrite; no truncation on a long prompt; a DISTINCT
        #    budget value recorded; the previous attempt's end reason carried forward.
        long_prompt = "TASK\n" + ("patch scripts/foo.py " * 400)
        coder.ask(long_prompt, "tester-model", max_tokens=1234, timeout=120)
        rows = read_rows(log)
        check("append, not overwrite (2 rows)", len(rows) == 2, f"rows={len(rows)}")
        if len(rows) == 2:
            r2 = rows[1]
            check("long prompt_chars recorded whole",
                  r2.get("prompt_chars") == len(long_prompt), f"{r2.get('prompt_chars')!r} != {len(long_prompt)}")
            check("max_tokens records THIS call's budget, not the first call's",
                  r2.get("max_tokens") == 1234, f"{r2.get('max_tokens')!r} != 1234")
            # ask.last is written at the END of every call, so at the head of the next call it IS
            # the previous attempt's end reason. The canned gateway reply says finish_reason=stop.
            check("prev_done_reason links the attempts (the RCA's field)",
                  r2.get("prev_done_reason") == "stop", f"prev_done_reason={r2.get('prev_done_reason')!r}")
            check("attempt sequence increments", r.get("attempt") == 1 and r2.get("attempt") == 2,
                  f"attempt {r.get('attempt')!r} then {r2.get('attempt')!r}")
            check("row is valid JSON carrying the full field set", isinstance(r2, dict)
                  and {"ts", "task", "model", "language", "attempt", "prompt_chars", "num_ctx",
                       "max_tokens", "files", "target", "lessons_injected", "system_chars",
                       "startup_available", "prev_done_reason"} <= set(r2),
                  f"keys={sorted(r2)}")

        # 5. LANGUAGE IS DERIVED FROM REAL TEXT AND DISTINGUISHES LANGUAGES -- a writer that
        #    always answered "python" would pass a single-value test.
        coder.RUN_CONTEXT["brief"] = "port the widget in core/src/main/java/App.kt"
        coder.ask("TASK\nport App.kt", "tester-model", timeout=120)
        rows = read_rows(log)
        check("a .kt task is keyed kotlin", rows[-1].get("language") == "kotlin",
              f"language={rows[-1].get('language')!r}")
        coder.RUN_CONTEXT["brief"] = "fix the README prose"
        coder.ask("TASK\nfix the README prose", "tester-model", timeout=120)
        rows = read_rows(log)
        check("a task naming no source file is keyed unknown", rows[-1].get("language") == "unknown",
              f"language={rows[-1].get('language')!r}")

        # 6. THE ROW IS WRITTEN BEFORE THE REQUEST. This module's whole reason to exist is that an
        #    attempt which dies at the socket still leaves evidence -- the on-entry-row rule the run
        #    ledger already follows. The stub is told to fail the socket on this call only.
        _boom.append(True)
        dead = "TASK\nthis attempt dies at the socket"
        try:
            coder.ask(dead, "tester-model", timeout=120)
        except Exception:                                                 # the harness may re-raise; the row is the point
            pass
        _boom.clear()
        rows = read_rows(log)
        check("an attempt that dies at the socket still leaves its row",
              len(rows) == 5 and rows[-1].get("prompt_chars") == len(dead),
              f"rows={len(rows)} last={rows[-1].get('prompt_chars') if rows else None!r} != {len(dead)}")
        check("the ledger has one row per model call (5)", len(rows) == 5, f"rows={len(rows)}")

        # 7. A WRITER THAT CANNOT WRITE SAYS SO -- it never swallows into silence. The log's parent
        #    is made a FILE, so the writer's first mkdir raises FileExistsError (an OSError).
        #    What is pinned is that the failure is REPORTED, naming the path and the exception, and
        #    that no row is lost to a silent `except Exception: pass`.
        blocker = tmp / "blocked"
        blocker.write_text("x", encoding="utf-8")
        coder.CONTEXT_LOG = blocker / "coder-context.jsonl"
        buf = io.StringIO()
        try:
            with redirect_stderr(buf):
                coder.ask("TASK\nunwritable log", "tester-model", timeout=120)
        except Exception as e:                                            # noqa: BLE001
            check("an unwritable ledger does not crash the dispatch", False, f"{type(e).__name__}: {e}")
        else:
            check("an unwritable ledger does not crash the dispatch", True)
        out = buf.getvalue()
        check("the write failure is reported, naming the path and the exception",
              "coder-context.jsonl" in out and "blocked" in out
              and any(n in out for n in ("FileExistsError", "NotADirectoryError",
                                          "PermissionError", "IsADirectoryError")),
              f"stderr={out[:300]!r}")
        check("a ledger that cannot be written leaves no partial row", len(read_rows(log)) == 5,
              f"rows={len(read_rows(log))}")

        # 8. NO RUNTIME TRAFFIC BEYOND THE ONE ENDPOINT THIS STUB ANSWERS.
        check("every request went to the gateway endpoint and nothing else",
              bool(_seen) and all(u.endswith("/v1/chat/completions") for u in _seen), f"urls={_seen}")
    finally:
        urllib.request.urlopen = real_urlopen


if __name__ == "__main__":
    main()
    print("FAILURES:", len(_failures))
    for f in _failures:
        print("  -", f)
    sys.exit(1 if _failures else 0)
