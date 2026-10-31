#!/usr/bin/env python3
"""A JavaScript tokeniser, so a broken page is caught here instead of by Jas.

    python3 scripts/jscheck.py file.js
    python3 scripts/jscheck.py --page projects/local-inference/chatui.py
    python3 scripts/jscheck.py --self-test

WHY THIS EXISTS
---------------
On 2026-08-25 a Python heredoc wrote a real newline into `split('\\n')`, so the browser
received an unterminated string literal. **One syntax error kills the whole script**, so
nothing on the page rendered — while every API endpoint returned correct JSON. I checked the
endpoints, called it verified, and Jas found the dead page (`L-108`).

The first guard I wrote was a per-line quote-balance heuristic. It flagged two lines that were
perfectly correct, because template literals legitimately span lines and carry unbalanced
quotes inside HTML attributes. **A check that fires on working code is worse than no check**,
so it was narrowed to almost nothing.

This replaces the heuristic with the real thing. There is no JS engine on this box — no node,
no deno, no quickjs — and installing one for a test would add a dependency to a repository
whose asset is that it has none. A tokeniser is about 200 lines, handles exactly the class of
defect that bites here, and I can read it.

WHAT IT IS AND IS NOT
---------------------
It is a **lexer**, not a parser. It tracks the things that have real nesting rules and are
easy to get silently wrong:

  * `'…'` and `"…"`, with escapes, which may NOT span a line
  * `` `…` `` template literals, which MAY span lines, including `${ }` holding more
    templates and strings — the case that defeated the heuristic
  * `//` and `/* … */` comments
  * regex literals, disambiguated from division by the preceding token
  * `()`, `[]`, `{}` nesting, reported with the line where the unmatched one opened

It will **not** catch a grammar error like `const const x = 1`. Those are rarer, louder, and
show up the moment the page is opened. What it catches is the silent class: a literal that
swallows the rest of the file, or a brace that never closes.

Say what a check does not cover, or the next person will assume it covers everything.
"""

import argparse
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# After these, a `/` begins a REGEX. After anything else (an identifier, a number, `)`, `]`)
# it is division. This is the classic ambiguity and getting it wrong turns `a / b / c` into
# a string that eats the file.
REGEX_OK_AFTER = {
    "(", ",", "=", ":", "[", "!", "&", "|", "?", "{", "}", ";", "+", "-", "*", "%",
    "<", ">", "~", "^", "return", "typeof", "instanceof", "in", "of", "new", "delete",
    "void", "throw", "do", "else", "case", "yield", "await", "=>", "&&", "||", "??",
}


class JsError(Exception):
    def __init__(self, line, msg, text=""):
        self.line, self.msg, self.text = line, msg, text
        super().__init__(f"line {line}: {msg}")


def tokenize(src):
    """Walk the source, returning (delimiter_stack_errors, token_count).

    Raises JsError on the first unrecoverable lexical problem, because after an unterminated
    literal every position that follows is meaningless — reporting a second error from inside
    the wreckage would be noise.
    """
    i, n, line = 0, len(src), 1
    stack = []                 # (char, line) for ( [ {
    last = None                # last significant token, for the regex/division call
    tokens = 0

    def at_line(pos):
        return src.count("\n", 0, pos) + 1

    while i < n:
        c = src[i]

        if c == "\n":
            line += 1
            i += 1
            continue
        if c in " \t\r":
            i += 1
            continue

        # --- comments ---------------------------------------------------------------
        if src.startswith("//", i):
            j = src.find("\n", i)
            i = n if j < 0 else j
            continue
        if src.startswith("/*", i):
            j = src.find("*/", i + 2)
            if j < 0:
                raise JsError(line, "unterminated block comment")
            line += src.count("\n", i, j)
            i = j + 2
            continue

        # --- plain strings: may NOT span a line -------------------------------------
        if c in "'\"":
            j, quote = i + 1, c
            while j < n:
                if src[j] == "\\":
                    j += 2
                    continue
                if src[j] == "\n":
                    raise JsError(line, f"unterminated {quote} string — a line break "
                                        f"inside a quoted string",
                                  src[i:src.find(chr(10), i)][:60])
                if src[j] == quote:
                    break
                j += 1
            if j >= n:
                raise JsError(line, f"unterminated {quote} string at end of file")
            i, last, tokens = j + 1, "str", tokens + 1
            continue

        # --- template literals: MAY span lines, and ${ } may nest ------------------
        if c == "`":
            start_line = line
            j = i + 1
            depth = 0
            while j < n:
                ch = src[j]
                if ch == "\\":
                    j += 2
                    continue
                if ch == "\n":
                    line += 1
                elif ch == "$" and j + 1 < n and src[j + 1] == "{":
                    depth += 1
                    j += 2
                    continue
                elif ch == "}" and depth:
                    depth -= 1
                elif ch in "'\"" and depth:
                    # a quoted string inside ${ } — skip it whole
                    q, k = ch, j + 1
                    while k < n and src[k] != q:
                        k += 2 if src[k] == "\\" else 1
                    j = k
                elif ch == "`" and depth:
                    # a nested template inside ${ } — recurse by scanning to its end
                    k, d2 = j + 1, 0
                    while k < n:
                        if src[k] == "\\":
                            k += 2
                            continue
                        if src[k] == "\n":
                            line += 1
                        if src[k] == "$" and k + 1 < n and src[k + 1] == "{":
                            d2 += 1
                            k += 2
                            continue
                        if src[k] == "}" and d2:
                            d2 -= 1
                        elif src[k] == "`" and not d2:
                            break
                        k += 1
                    j = k
                elif ch == "`" and not depth:
                    break
                j += 1
            if j >= n:
                raise JsError(start_line, "unterminated template literal")
            i, last, tokens = j + 1, "str", tokens + 1
            continue

        # --- regex literal or division ---------------------------------------------
        if c == "/":
            if last is None or last in REGEX_OK_AFTER:
                j, klass = i + 1, False
                while j < n:
                    if src[j] == "\\":
                        j += 2
                        continue
                    if src[j] == "[":
                        klass = True
                    elif src[j] == "]":
                        klass = False
                    elif src[j] == "\n":
                        raise JsError(line, "unterminated regex literal")
                    elif src[j] == "/" and not klass:
                        break
                    j += 1
                if j >= n:
                    raise JsError(line, "unterminated regex literal at end of file")
                j += 1
                while j < n and src[j].isalpha():     # flags
                    j += 1
                i, last, tokens = j, "regex", tokens + 1
                continue
            i, last, tokens = i + 1, "/", tokens + 1
            continue

        # --- delimiters --------------------------------------------------------------
        if c in "([{":
            stack.append((c, line))
            i, last, tokens = i + 1, c, tokens + 1
            continue
        if c in ")]}":
            if not stack:
                raise JsError(line, f"closing {c!r} with nothing open")
            op, opened = stack.pop()
            if "([{"[")]}".index(c)] != op:
                raise JsError(line, f"{c!r} closes {op!r} opened on line {opened}")
            i, last, tokens = i + 1, c, tokens + 1
            continue

        # --- words, numbers, operators ------------------------------------------------
        m = re.match(r"[A-Za-z_$][A-Za-z0-9_$]*|\d[\d.eExXa-fA-F]*|=>|===|!==|==|!=|"
                     r"<=|>=|&&|\|\||\?\?|\+\+|--|[-+*%<>=!&|^~?:;,.]", src[i:])
        if m:
            tok = m.group(0)
            i, last, tokens = i + len(tok), tok, tokens + 1
            continue
        i += 1                                        # anything else, step over

    if stack:
        op, opened = stack[-1]
        raise JsError(opened, f"{op!r} opened here and never closed")
    return tokens


def script_from_page(py_file):
    """Pull PAGE out of a Python module and return its <script> body."""
    sys.path.insert(0, str(Path(py_file).resolve().parent))
    import importlib
    mod = importlib.import_module(Path(py_file).stem)
    page = getattr(mod, "PAGE", "")
    m = re.search(r"<script>(.*?)</script>", page, re.S)
    if not m:
        raise SystemExit(f"no <script> block in {py_file}'s PAGE")
    return m.group(1)


SELF_TEST = [
    ("plain code", "const a = 1; function f(){ return a + 1; }", True),
    ("the 2026-08-25 bug", "return p.split('\n').map(x=>x);", False),
    ("escaped newline is fine", r"return p.split('\n').map(x=>x);", True),
    ("template spanning lines", "const t = `line one\nline two`;", True),
    ("template with nested ${}", "const t = `a ${b ? `x ${c}` : 'y'} z`;", True),
    ("quotes inside a template", 'const t = `<div class="x" id=\'y\'>${v}</div>`;', True),
    ("regex with a slash class", "s.replace(/[&<]/g, c => c);", True),
    ("division is not a regex", "const r = a / b / c;", True),
    ("unterminated template", "const t = `never closed;", False),
    ("unbalanced brace", "function f(){ if (x) { return 1; }", False),
    ("mismatched close", "const a = (1 + 2];", False),
    ("block comment holding a quote", "/* it's fine */ const a = 1;", True),
    ("line comment holding a quote", "// it's fine\nconst a = 1;", True),
    ("unterminated block comment", "/* never closed", False),
]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("target", nargs="?")
    ap.add_argument("--page", help="a .py file exposing PAGE with a <script> block")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args(argv)

    if a.self_test:
        bad = 0
        print("\njscheck self-test — each case must come out the way it says\n")
        for name, src, should_pass in SELF_TEST:
            try:
                tokenize(src)
                got = True
                detail = ""
            except JsError as e:
                got = False
                detail = str(e)
            ok = got == should_pass
            bad += not ok
            print(f"  {'[ok]  ' if ok else '[BAD] '} {name:<32}"
                  f"{'accepts' if got else 'rejects'}"
                  f"{'' if ok else '  <-- WRONG'}"
                  f"{('  (' + detail + ')') if detail and not should_pass else ''}")
        print(f"\n{len(SELF_TEST) - bad} passed, {bad} failed\n")
        return 1 if bad else 0

    if a.page:
        src = script_from_page(a.page)
        label = f"{a.page} <script>"
    elif a.target:
        src = Path(a.target).read_text(encoding="utf-8")
        label = a.target
    else:
        ap.print_help()
        return 1

    try:
        n = tokenize(src)
    except JsError as e:
        print(f"\n  [FAIL] {label}")
        print(f"         line {e.line}: {e.msg}")
        if e.text:
            print(f"         {e.text!r}")
        print("\n  One syntax error stops the whole script. Nothing on the page will run.")
        return 1
    print(f"  [PASS] {label} — {n:,} tokens, no lexical errors")
    return 0


if __name__ == "__main__":
    sys.exit(main())
