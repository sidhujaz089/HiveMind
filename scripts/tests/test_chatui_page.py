#!/usr/bin/env python3
"""Does the page the BROWSER receives actually parse?

On 2026-08-25 a Python heredoc wrote `split('\\n')` into the page string, Python turned the
escape into a real newline, and the browser got an unterminated string literal. One syntax
error kills the whole script, so nothing rendered and the model list vanished -- while
`/api/models` returned all five models perfectly.

**I tested the API and never loaded the page.** Jas found it. Same shape as `L-102`: correct
about the layer I checked, blind to the one the user touches.

There is no JS engine on this box, so this checks the two properties that break silently and
are mechanically detectable: no string literal may span a line, and the delimiters must
balance. That is not a parser. It is the specific failure that happened, plus the class it
belongs to.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "projects" / "local-inference"))
PASSED = FAILED = 0


def report(label, got, want):
    global PASSED, FAILED
    if got == want:
        PASSED += 1
        print(f"  [ok]   {label}: {got}")
    else:
        FAILED += 1
        print(f"  [BAD]  {label}: expected {want}, got {got}")


def script_of(page):
    """EVERY script block, joined -- not the first one.

    The browser runs all of them and one syntax error anywhere kills the script that
    follows it, so checking only the first checks the wrong thing. This used to be
    re.search, which was correct while the page had exactly one block. On 2026-09-07 the
    shared header (scripts/appnav.py) added its own small block ABOVE chatui's, and the
    check silently started tokenising three lines of scroll-into-view instead of the page
    -- reporting the page's own functions missing. A test that quietly changes what it
    measures is worse than one that fails.
    """
    return "\n;\n".join(re.findall(r"<script>(.*?)</script>", page, re.S))


def main():
    import chatui
    js = script_of(chatui.PAGE)
    print("The script the browser receives:")
    report("a script block exists", bool(js.strip()), True)

    # The real tokeniser, not a per-line heuristic. The heuristic this replaces flagged
    # two correct lines, because template literals legitimately span lines with unbalanced
    # quotes inside HTML attributes.
    sys.path.insert(0, str(ROOT / "scripts"))
    import jscheck
    try:
        n = jscheck.tokenize(js)
        report("the script tokenises cleanly", True, True)
        report("and it is not empty", n > 200, True)
    except jscheck.JsError as e:
        report(f"the script tokenises cleanly (line {e.line}: {e.msg})", False, True)

    print("\nThe tokeniser itself is trustworthy:")
    fails = 0
    for name, src, should_pass in jscheck.SELF_TEST:
        try:
            jscheck.tokenize(src)
            got = True
        except jscheck.JsError:
            got = False
        fails += got != should_pass
    report("every self-test case behaves as declared", fails, 0)

    print("\nThe functions the page cannot work without are present:")
    for fn in ("refresh", "send", "runs", "review", "esc", "difflines"):
        report(f"{fn}()", f"function {fn}(" in js or f"async function {fn}(" in js, True)

    # --- the check must REJECT the page that actually shipped broken -------------------
    # Until now this suite proved jscheck's self-tests behave and that the real page passes.
    # Neither shows that THIS check would have caught THIS bug. So put the 2026-08-25 defect
    # back into a copy of the page and require a refusal: a guard is not trusted until it has
    # been observed failing (docs/CHECKS.md rule 1).
    print("\nThe check refuses a page broken the way the real one was:")

    # The original: a Python heredoc turned \n into a real newline mid-string-literal.
    line = next((l for l in js.splitlines()
                 if l.count("'") >= 2 and "//" not in l and len(l.strip()) > 20), None)
    if line is None:
        report("found a single-quoted line to break", False, True)
    else:
        i = line.index("'")
        broken = js.replace(line, line[:i + 1] + "\n" + line[i + 1:], 1)
        try:
            jscheck.tokenize(broken)
            caught = False
        except jscheck.JsError:
            caught = True
        report("a string literal split across lines is REJECTED", caught, True)

    # And the other class the docstring claims to cover: unbalanced delimiters.
    try:
        jscheck.tokenize(js + "\nfunction oops() {")
        caught = False
    except jscheck.JsError:
        caught = True
    report("an unclosed brace is REJECTED", caught, True)

    # A missing function must fail the presence check, or that loop proves nothing either.
    report("a function the page lacks is NOT reported present",
           "function definitelyNotAFunction(" in js, False)

    print(f"\n{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
