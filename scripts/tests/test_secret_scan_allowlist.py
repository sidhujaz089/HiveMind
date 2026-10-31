#!/usr/bin/env python3
"""Fail-first oracle for t1563's allowlist: one rule, used by BOTH secret scans.

WHY. `check_secrets()` runs two scans -- the git-tracked files (the commit gate) and the
working tree the MCP server serves to Chat (t117). On 2026-10-24 they disagreed about the same
values: the served scan filtered matches whose sha256 is a declared fake, and the tracked scan
had no such filter at all, so a test value in one tracked log kept the most trusted check in
this repository red for two days. One helper, called from both, is what stops a fix in one
leaving the other red -- the same shape of defect as the two vocabulary fields in the run
ledger.

THE PROPERTY THAT MATTERS IS `all`, NOT `any`. A line that holds a declared fake AND an
undeclared value must still fail: an allowlist that passes a line because something on it was
known is a hole with a test in front of it, not a declaration. That case is asserted below.

The values here are fake and this file says so; the DECLARED_FALES the test reads live in
check_workspace so there is one list, not two.
"""
import hashlib
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

import check_workspace as cw  # noqa: E402

PASSED = FAILED = 0


def check(name, got, want):
    global PASSED, FAILED
    ok = got == want
    PASSED += ok
    FAILED += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"  (got {got!r}, want {want!r})"))


def main():
    # 1. The list exists as a MODULE constant, so both scans can reach it.
    check("the declared-fake list is module-level", hasattr(cw, "KNOWN_FAKE"), True)
    check("  and the allowlist rule is a function both scans can call",
          hasattr(cw, "only_known_fakes"), True)
    if not (hasattr(cw, "KNOWN_FAKE") and hasattr(cw, "only_known_fakes")):
        print(f"{PASSED} passed, {FAILED} failed")
        return 1

    # THE MECHANISM IS TESTED WITHOUT A VALUE IN THE TREE. Hunting the repository for a
    # declared fake made this suite pass in the live checkout and skip in a clean clone --
    # a test whose best assertion depends on leftover logs is not a test. A value is minted
    # here and its hash declared for the duration, which exercises `all` exactly.
    mine = "sk-ant-" + "Q" * 24          # not a real key, and not declared by default
    mine_hash = hashlib.sha256(mine.encode()).hexdigest()
    other = "sk-ant-" + "W" * 24         # a second value that stays undeclared
    check("an undeclared value is refused",
          cw.only_known_fakes(f"workspace/x.jsonl:1:{{\"c\": \"{mine}\"}}"), False)
    cw.KNOWN_FAKE.add(mine_hash)
    try:
        check("a line whose only match is declared is allowed",
              cw.only_known_fakes(f"workspace/x.jsonl:12:{{\"c\": \"{mine}\"}}"), True)
        # The property that matters: one undeclared value is enough to fail the line.
        check("a declared fake AND an undeclared value on one line is still refused",
              cw.only_known_fakes(f"workspace/x.jsonl:1:{mine} {other}"), False)
    finally:
        cw.KNOWN_FAKE.discard(mine_hash)
    check("a declared hash alone does not make every line clean",
          cw.only_known_fakes(f"workspace/x.jsonl:1:{other}"), False)
    check("a line with no match at all is not 'clean by allowlist'",
          cw.only_known_fakes("workspace/x.jsonl:1:nothing here"), False)

    # 3b. The documented D-38 placeholder is a DECLARED fake, not an automatic exemption: a
    #     line carrying only it is allowed, a line carrying it plus anything undeclared is not.
    placeholder = "sk-ant-" + "ABCdefGH123ijKLmnop456"
    check("the documented placeholder alone is allowed",
          cw.only_known_fakes(f"workspace/x.jsonl:1:{{\"c\": \"{placeholder}\"}}"), True)
    check("  but the placeholder plus an undeclared value is refused",
          cw.only_known_fakes(f"workspace/x.jsonl:1:{placeholder} {other}"), False)

    # 3c. Every declared hash is a WELL-FORMED sha256. One entry sat at 63 hex characters for
    #     weeks, so the value it was declaring could never match it: a declared fake that
    #     cannot cover its own value is indistinguishable from no declaration at all.
    short = [(k, len(k)) for k in cw.KNOWN_FAKE if len(k) != 64]
    check("every declared hash is 64 hex characters", short, [])
    check("  and every declared hash is lowercase hex",
          all(re.fullmatch(r"[0-9a-f]{64}", k) for k in cw.KNOWN_FAKE), True)

    # 3. Both scans use it. Asserted at the call sites, because the defect was a scan that
    #    silently did not (a behavioural test cannot see a filter that is simply absent).
    import inspect
    src = inspect.getsource(cw.Checker.check_secrets)
    check("the tracked scan calls the shared rule", src.count("only_known_fakes"), 2)
    check("  and the served scan shares the same list, not a copy",
          "known_fake = {" in src, False)

    print(f"{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
