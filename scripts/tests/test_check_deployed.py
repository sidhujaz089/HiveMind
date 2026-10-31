#!/usr/bin/env python3
"""Fixtures for the deployment check.

This check exists because a fix sat on disk for an hour while the running service answered
with the old code, and every file-reading check here passed the whole time (`L-102`). So the
property that matters is that it never reports a match for something it did not actually
compare.

The defect under test is the one this repository keeps producing: **a check that is correct
about what it looks at and silently blind to a category.** `source_defaults()` matches only
string defaults, so a numeric one -- `limit: int = 40` -- is skipped without a word, and the
check reports PASS while that default drifts between source and the served endpoint. Exactly
the shape of `skipped_frozen_export` reporting 0, and of the two-layer search default.
"""
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import check_deployed as cd  # noqa: E402

PASSED = FAILED = 0


def report(label, got, want):
    global PASSED, FAILED
    if got == want:
        PASSED += 1
        print(f"  [ok]   {label}: {got}")
    else:
        FAILED += 1
        print(f"  [BAD]  {label}: expected {want}, got {got}")


FIXTURE = '''
@app.tool(description="a string default")
def search(query: str, glob: str = "**/*") -> dict:
    return _guard(ws.search, query, glob)


@app.tool(description="a numeric default and a boolean one")
def listing(path: str, limit: int = 40, deep: bool = False) -> dict:
    return _guard(ws.listing, path, limit, deep)


@app.tool(description="no defaults at all")
def status() -> dict:
    return _guard(ws.status)
'''


def main():
    tmp = Path(tempfile.mkdtemp())
    try:
        server = tmp / "server.py"
        server.write_text(FIXTURE, encoding="utf-8")
        cd.SERVER = server

        got = cd.source_defaults()

        print("String defaults are captured (this already worked):")
        report("search.glob", got.get("search", {}).get("glob"), "**/*")

        print("\nNON-STRING defaults must be captured too, not silently skipped:")
        # A numeric default that drifts between source and the served endpoint is exactly
        # what this check exists to catch, and it is currently invisible to it.
        report("listing.limit is seen", got.get("listing", {}).get("limit"), 40)
        report("listing.deep is seen", got.get("listing", {}).get("deep"), False)

        print("\nA tool with no defaults is simply absent, not an error:")
        report("status has nothing to compare", "status" in got, False)

        print("\nAnd it must never CRASH on a signature it cannot parse:")
        # The first patch that passed this suite used ast.literal_eval on a greedy regex
        # capture. It satisfied every assertion above and blew up on all three of these.
        # A check that raises is worse than one that is blind: this one runs at session end
        # and a traceback there blocks the close.
        for label, fixture in [
            ("a string containing a comma", 'def f(g: str = "a,b") -> dict:\n    pass\n'),
            ("a non-literal default",       'def f(g: int = LIMIT) -> dict:\n    pass\n'),
            ("a call as a default",         'def f(g: list = list()) -> dict:\n    pass\n'),
        ]:
            fx = tmp / "edge.py"
            fx.write_text(fixture, encoding="utf-8")
            cd.SERVER = fx
            try:
                cd.source_defaults()
                report(f"survives {label}", True, True)
            except Exception as e:
                report(f"survives {label}", f"{type(e).__name__}", True)

        print("\n  and the comma case must be READ correctly, not merely survived:")
        fx = tmp / "comma.py"
        fx.write_text('def f(g: str = "a,b") -> dict:\n    pass\n', encoding="utf-8")
        cd.SERVER = fx
        try:
            report("the whole string is captured",
                   cd.source_defaults().get("f", {}).get("g"), "a,b")
        except Exception as e:
            report("the whole string is captured", f"{type(e).__name__}", "a,b")
        print("\nA default it CANNOT parse must be reported, not dropped silently:")
        # t148. The patch that fixed the crash replaced it with `except Exception: pass`,
        # which is L-087's shape: the upload allow-list that discarded every non-image file
        # and returned success. Not crashing is right for a check that runs at session end;
        # saying nothing about what it could not read is not.
        fx = tmp / "unparseable.py"
        # NOTE: no call-with-parens here. The outer regex captures args as `[^)]*`, so a
        # default like `list()` truncates the argument list at its first close-paren and
        # everything after it becomes invisible. The first version of this fixture had
        # exactly that and was UNSATISFIABLE -- my own minimal implementation failed it,
        # and the agent spent 26,175 tokens and four attempts failing a test nobody could
        # pass. Filed separately as t149; it is a real limitation but not this task.
        fx.write_text('def f(a: int = LIMIT, b: int = OTHER, c: str = "ok") -> dict:\n'
                      '    pass\n', encoding="utf-8")
        cd.SERVER = fx
        cd.UNPARSED.clear()
        got2 = cd.source_defaults()
        report("the parseable one is still captured", got2.get("f", {}).get("c"), "ok")
        report("both unparseable ones are reported", len(cd.UNPARSED), 2)
        # "any of f/a/b appears in the string" passes on the word "banana". Name the
        # argument in a form that cannot match by accident.
        report("each names the tool and the argument",
               sorted(u.split()[0] for u in cd.UNPARSED), ["f(a=)", "f(b=)"])
        report("the raw text is kept so it can be read",
               any("LIMIT" in u for u in cd.UNPARSED), True)

    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("\nA tool that never deployed is a failure, not a skip:")
    for label, got, want in undeployed_tool_cases():
        report(label, got, want)

    print(f"\n{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0

def undeployed_tool_cases():
    """A tool the source declares and the bridge does not serve must FAIL, not be skipped.

    Round 71 of the env3 probe. main() compared defaults only for tools present in BOTH
    source and the endpoint and skipped everything else with a `continue`. A change on disk
    that never reached the process -- the 2026-08-25 incident this whole file exists for --
    was therefore invisible. Adding `recall` and `why` and forgetting to restart would have
    produced PASS.
    """
    import io, contextlib
    import check_deployed as cd

    def run(src, served, declared=None):
        """`declared` is the ROSTER -- every tool the source says is served, whether or
        not it carries a default. It defaults to set(src) so the cases written before the
        roster existed keep their original meaning."""
        roster = set(src) if declared is None else set(declared)
        real = (cd.source_defaults, cd.served_schema, cd.declared_tools)
        cd.source_defaults, cd.served_schema = (lambda: src), (lambda: served)
        cd.declared_tools = lambda: roster
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                code = cd.main()
        finally:
            cd.source_defaults, cd.served_schema, cd.declared_tools = real
        return code, buf.getvalue()

    out = []
    code, txt = run({"search": {"glob": "**/*"}, "brand_new": {}}, {"search": {"glob": "**/*"}})
    out.append(("a tool in source but NOT served fails", code, 1))
    out.append(("  and is named", "brand_new" in txt, True))
    code, txt = run({"search": {"glob": "**/*"}}, {"search": {"glob": "**/*"}, "ghost": {}})
    out.append(("a tool served but NOT in source fails", code, 1))
    # Tools with no default argument are NOT drift -- they are ordinary roster members.
    # This used to be waved through by a hand-maintained `known_runtime` allowlist, which
    # existed only because the roster was being taken from the defaults-only set. Now they
    # are simply declared, and the allowlist is gone.
    code, _ = run({"search": {"glob": "**/*"}},
                  {"search": {"glob": "**/*"}, "read": {}, "status": {}, "append": {}},
                  declared={"search", "read", "status", "append"})
    out.append(("no-default tools are not drift", code, 0))

    # THE HOLE, found on 2026-08-26 re-verifying round 71. A tool in the roster that carries
    # no default was invisible to the declared-but-not-served arm, because that arm compared
    # against source_defaults() -- three of the ten tools the server declares. `read` could
    # have stopped being served and this file would have printed PASS.
    code, txt = run({"search": {"glob": "**/*"}}, {"search": {"glob": "**/*"}},
                    declared={"search", "read"})
    out.append(("a no-default tool that stopped being served fails", code, 1))
    out.append(("  and is named", "read" in txt, True))
    code, _ = run({"search": {"glob": "**/*"}}, {"search": {"glob": "**/*"}})
    out.append(("a matching pair passes", code, 0))
    code, _ = run({"search": {"glob": "**/*"}}, {"search": {"glob": "**/*.md"}})
    out.append(("a drifted default still fails", code, 1))
    return out


if __name__ == "__main__":
    sys.exit(main())
