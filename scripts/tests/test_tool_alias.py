#!/usr/bin/env python3
"""t1570 Phase 1: alias table + log. Red on main because resolve_alias does not exist."""
import json, sys, time, tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "projects" / "local-inference"))
import console as c

FAILED = []

def report(label, ok):
    tag = "ok  " if ok else "FAIL"
    print(f"  {tag}  {label}")
    if not ok:
        FAILED.append(label)

# --- stub RUN so we can capture calls without hitting the repo ---
CALLS = []
def _stub(name):
    def fn(args):
        CALLS.append((name, dict(args)))
        return f"STUB-{name}"
    return fn

REAL = ["read_file", "search_repo", "run_command", "write_file"]
_orig_run = dict(c.RUN)
for n in REAL:
    c.RUN[n] = _stub(n)

def main():
    with tempfile.TemporaryDirectory() as td:
        log = Path(td) / "outcomes.jsonl"
        c.UNKNOWN_TOOL_LOG = log

        # 1. Eight historical calls resolve to the expected tool with translated args
        cases = [
            ("run_file",      {"path": "console.py"},                          "read_file",   {"path": "console.py"}),
            ("run_file",      {"path": "console.py", "start": "449"},         "read_file",   {"path": "console.py", "start": "449"}),
            ("run_file_read", {"path": "x.py", "start": "345"},               "read_file",   {"path": "x.py", "start": "345"}),
            ("read_path",     {"path": "y.py", "start": "449"},               "read_file",   {"path": "y.py", "start": "449"}),
            ("grep",          {"pattern": "TODO", "path": "."},               "search_repo", {"query": "TODO"}),
            ("grep_code",     {"pattern": "def main"},                        "search_repo", {"query": "def main"}),
            ("bash",          {"command": "echo hi"},                         "run_command", {"command": "echo hi"}),
            ("bash",          {"command": "ls", "timeout_s": 5},              "run_command", {"command": "ls", "timeout_s": 5}),
        ]
        for name, args, want_tool, want_args in cases:
            CALLS.clear()
            r = c._run_tool(name, args, role="manager")
            report(f"{name}{args} -> {want_tool}",
                   any(cn == want_tool and ca == want_args for cn, ca in CALLS))
            report(f"{name}{args} names the real tool",
                   want_tool in r and "STUB-" in r)

        # 2. run_file with content is a write: refused, not executed
        CALLS.clear()
        r = c._run_tool("run_file", {"path": "x", "content": "y"}, role="manager")
        report("run_file(content) is refused, lists real tools",
               r.startswith("REFUSED") and "no tool called run_file" in r)
        report("run_file(content) did not execute", len(CALLS) == 0)
        rows = [json.loads(x) for x in log.read_text().splitlines()] if log.exists() else []
        report("run_file(content) wrote a refused-write row",
               any(x["unknown"] == "run_file" and x["outcome"] == "refused-write"
                   and x["resolved_to"] is None for x in rows))

        # 3. Placeholder / malformed names are not aliased
        for bad in ["<null", "<", "run_file_placeholder_9x8y7z"]:
            CALLS.clear()
            r = c._run_tool(bad, {"path": "x"}, role="manager")
            report(f"{bad!r} is refused, not aliased",
                   r.startswith("REFUSED") and len(CALLS) == 0)

        # 4. Every real tool name still resolves exactly as today
        for tool in REAL:
            CALLS.clear()
            args = {"path": "x"} if tool == "read_file" else (
                   {"query": "q"} if tool == "search_repo" else (
                   {"command": "echo"} if tool == "run_command" else {"path": "x", "content": "y"}))
            r = c._run_tool(tool, args, role="manager")
            report(f"real {tool} still works", any(cn == tool for cn, _ in CALLS) and "REFUSED" not in r)

        # 5. Alias hit writes one row with new fields
        log.unlink(missing_ok=True)
        CALLS.clear()
        c._run_tool("grep", {"pattern": "p", "path": "."}, role="manager")
        rows = [json.loads(x) for x in log.read_text().splitlines()]
        aliased = [x for x in rows if x["unknown"] == "grep" and x["outcome"] == "aliased"]
        report("alias hit wrote a row", len(aliased) == 1)
        if aliased:
            a = aliased[0]
            report("row has resolved_to", a.get("resolved_to") == "search_repo")
            report("row has model", isinstance(a.get("model"), str) and a["model"])
            report("row has args_keys", a.get("args_keys") == ["path", "pattern"])

        # 6. Unmapped name writes one row
        log.unlink(missing_ok=True)
        c._run_tool("totally_bogus", {"x": 1}, role="manager")
        rows = [json.loads(x) for x in log.read_text().splitlines()]
        report("unmapped name wrote a row",
               any(x["unknown"] == "totally_bogus" and x["outcome"] == "unmapped"
                   and x["resolved_to"] is None for x in rows))

        # 7. Raising log writer does not raise into the call
        import builtins
        orig_open = builtins.open
        boom_path = Path(td) / "boom.jsonl"
        c.UNKNOWN_TOOL_LOG = boom_path
        def _failing_open(p, *a, **kw):
            if str(p) == str(boom_path):
                raise OSError("disk full")
            return orig_open(p, *a, **kw)
        builtins.open = _failing_open
        try:
            CALLS.clear()
            r = c._run_tool("grep", {"pattern": "p", "path": "."}, role="manager")
            report("raising log writer: call did not raise", True)
            report("raising log writer: result says not recorded",
                   "event not recorded" in r or "not recorded" in r)
        except Exception as e:
            report(f"raising log writer: call did not raise ({type(e).__name__}: {e})", False)
        finally:
            builtins.open = orig_open
            c.UNKNOWN_TOOL_LOG = log

        # 8. Known alias whose args do not translate losslessly
        log.unlink(missing_ok=True)
        CALLS.clear()
        r = c._run_tool("grep", {"pattern": "p", "path": "scripts/"}, role="manager")
        report("grep(path!='.') is refused-args",
               r.startswith("REFUSED") and len(CALLS) == 0)
        rows = [json.loads(x) for x in log.read_text().splitlines()]
        report("grep(path!='.') wrote a refused-args row",
               any(x["unknown"] == "grep" and x["outcome"] == "refused-args" for x in rows))

        # restore
        c.RUN.update(_orig_run)

    print(f"\n{'FAILED: ' + str(len(FAILED)) + ' check(s)' if FAILED else 'all ok'}")
    sys.exit(1 if FAILED else 0)

if __name__ == "__main__":
    main()
