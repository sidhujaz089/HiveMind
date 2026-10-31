#!/usr/bin/env python3
r"""One JSON extractor for model output, because there are currently eight.

`re.search(r"\{.*\}", text, re.S)` -- first brace to last -- is in EIGHT files here. It was
fixed in scope_queue.py on 2026-09-07 after mis-parsing three of four realistic answer
shapes, and `scripts/harness_inventory.py` then found the same line in seven more, including
`exam.py` (which scored the model interviews) and `deliberate.py` (which scope_queue.py
itself calls).

Fixing seven copies separately would make seven implementations of one idea -- `L-150`, a
lesson learned in one place and never carried to the other. So callers import this instead.

**The contract lives in `scripts/tests/test_jsonx.py` and is enforced there, not here.**
In short: `objects(text)` returns every complete balanced-brace object in order, ignoring
prose and fenced blocks; `one(text, keys)` returns exactly one object carrying all of `keys`,
or says `ambiguous` rather than guessing between two. Ambiguity is a signal, not a nuisance:
two verdicts means the model gave two answers, and picking one silently is the class of
wrongness this repository keeps finding.
"""

import json


_VALID_ESCAPE = set('"\\/bfnrtu')


def _loads_or_none(s):
    """The object, or None when the region is not JSON. Not a silent loss: every region,
    parsed or not, stays in `regions`, which is what callers show when nothing parsed."""
    try:
        return json.loads(s)
    except (json.JSONDecodeError, ValueError):
        return None


def _repair_invalid_escapes(region):
    """Emit an extra backslash if a backslash is followed by a non-valid JSON escape char inside a string."""
    res = []
    in_string = False
    escape = False
    i = 0
    n = len(region)
    while i < n:
        ch = region[i]
        if in_string:
            if escape:
                if ch not in _VALID_ESCAPE:
                    res.append('\\')
                res.append(ch)
                escape = False
            elif ch == '\\':
                res.append(ch)
                escape = True
            elif ch == '"':
                res.append(ch)
                in_string = False
            else:
                res.append(ch)
        else:
            if ch == '"':
                in_string = True
            res.append(ch)
        i += 1
    return "".join(res)


def _scan_regions(text):
    """Scan for balanced-brace regions. Returns (regions, parsed, saw_brace)."""
    regions = []
    parsed = []
    saw_brace = False
    n = len(text)
    i = 0
    while i < n:
        if text[i] == '{':
            saw_brace = True
            start = i
            depth = 0
            in_string = False
            escape = False
            j = i
            found_end = False
            while j < n:
                ch = text[j]
                if escape:
                    escape = False
                elif in_string:
                    if ch == '\\':
                        escape = True
                    elif ch == '"':
                        in_string = False
                else:
                    if ch == '"':
                        in_string = True
                    elif ch == '{':
                        depth += 1
                    elif ch == '}':
                        depth -= 1
                        if depth == 0:
                            found_end = True
                            break
                j += 1
            if found_end:
                region = text[start:j + 1]
                regions.append(region)
                obj = _loads_or_none(_repair_invalid_escapes(region))
                if isinstance(obj, dict):
                    parsed.append(obj)
                i = j + 1
            else:
                i += 1
        else:
            i += 1
    return regions, parsed, saw_brace


def repair_brackets(text):
    """`text` from its first `{` with mismatched or missing closers repaired, or None.

    Measured 2026-10-01 on the real UNPARSEABLE replies: after the escape repair, the largest
    remaining class is a model that forgets to close a list before the next key --
    `{"ops": [{...}, "why": "..."}` -- or closes a list where an object should close. Both are
    unambiguous from the structure: inside a list, a `"key":` means the list ended; a closer
    that does not match the open bracket means the inner one was never closed. Strings are
    respected (escapes included); nothing inside a string is touched. Returns None when the
    text has no `{` or the repair changes nothing."""
    i = text.find("{")
    if i < 0:
        return None
    s, out, stack, in_str, esc, j = text[i:], [], [], False, False, 0
    while j < len(s):
        ch = s[j]
        if in_str:
            out.append(ch)
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            j += 1
            continue
        if ch == '"':
            # A KEY INSIDE A LIST: `, "name":` while the innermost open bracket is `[`.
            if stack and stack[-1] == "[":
                k = s.find('"', j + 1)
                while k > 0 and s[k - 1] == "\\":
                    k = s.find('"', k + 1)
                if k > 0 and s[k + 1:].lstrip().startswith(":"):
                    w = len(out)
                    while w and out[w - 1] in " \t\r\n,":
                        w -= 1
                    out.insert(w, "]")
                    stack.pop()
            in_str = True
            out.append(ch)
        elif ch in "{[":
            stack.append(ch)
            out.append(ch)
        elif ch in "}]":
            want = "}" if ch == "}" else "]"
            while stack and ("}" if stack[-1] == "{" else "]") != want:
                out.append("}" if stack.pop() == "{" else "]")
            if stack:
                stack.pop()
            out.append(ch)
            if not stack:
                break
        else:
            out.append(ch)
        j += 1
    while stack:
        out.append("}" if stack.pop() == "{" else "]")
    fixed = "".join(out)
    return fixed if fixed != s[:len(fixed)] or len(fixed) != len(s) else None


def scan(text):
    """(regions, parsed, saw_brace): every balanced-brace region, the dicts that parsed (after
    the invalid-escape repair), and whether any `{` was seen at all. For a caller that needs
    more than one()'s verdict -- coder.py picks the object carrying ops, and reports WHY the
    others did not parse."""
    return _scan_regions(text)


def objects(text):
    """Every complete balanced-brace JSON object in text, in order."""
    _, parsed, _ = _scan_regions(text)
    return parsed


def one(text, keys):
    """Find objects carrying every key in keys; refuse to guess between conflicting matches."""
    regions, parsed, saw_brace = _scan_regions(text)

    if not saw_brace:
        return {"status": "no-json", "obj": None}

    if not parsed:
        return {"status": "invalid-json", "obj": None}

    matches = [obj for obj in parsed if all(k in obj for k in keys)]

    if not matches:
        return {"status": "no-match", "obj": None}

    # Matches that agree on the keys resolve to ok; only a genuine conflict is ambiguous.
    first_values = {k: matches[0][k] for k in keys}
    for m in matches[1:]:
        if any(m[k] != first_values[k] for k in keys):
            return {"status": "ambiguous", "obj": None}

    return {"status": "ok", "obj": matches[0]}
