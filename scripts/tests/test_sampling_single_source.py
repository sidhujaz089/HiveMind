#!/usr/bin/env python3
"""No inference call site may invent its own sampling values.

WHY THIS EXISTS
---------------
`settings.SAMPLING_BY_MODEL` holds the vendors' published values with a source URL and a date
for each. It is worth nothing if a call site sends its own numbers instead, and on 2026-09-03
five did: `probe.py` and `duo.py` at `temperature 0.3`, `methodology.py` and `askmemory.py` at
`0.4`, `chatui.py` at `0.3` -- none of them sourced from anywhere, all of them overriding
Qwen's published 1.0/0.95/20 for the model they were driving.

Nothing failed. That is the point: an invented temperature does not raise, it just quietly
makes every conclusion drawn through that call site a property of the harness rather than of
the model. It is `L-135`, and it had five more homes than anyone had looked for.

So the rule is mechanical and checkable: if a file POSTs to Ollama's chat/generate API, the
options it sends must come from `settings.sampling()`, and the sampling keys must not appear
as literals in the body.
"""
import ast
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "projects" / "local-inference"))
import settings                                              # noqa: E402

# The keys that belong to the vendor. A call site sending its own number here is L-135.
VENDOR_KEYS = ("temperature", "top_p", "top_k", "min_p", "repeat_penalty", "presence_penalty")

# num_ctx, num_predict and keep_alive are OURS -- budget and residency, not sampling -- so they
# were never the vendor rule's business. "Ours" was never "unpoliced": a call site typing a
# literal here is L-119, the number with no source, typed in good faith three times
# (NUM_CTX = 262144 in a caller). The budget rule flags the literal and accepts the value read
# from settings.
BUDGET_KEYS = ("num_ctx", "num_predict", "keep_alive")

# Today's audited population (2026-10-10: 12 files / 27 sites) dispositioned BY NAME, so the
# gate lands green the day it lands. This is a record of debt, not a licence: a file with a
# budget literal that is not on this list is a FINDING, and the four module-constant fallbacks
# are scheduled out by t1016 item 2.
BUDGET_CALLERS = {
    "askmemory.py": "module-constant fallback, to be deleted by t1016 item 2",
    "coder.py": "module-constant fallback, to be deleted by t1016 item 2",
    "methodology.py": "module-constant fallback, to be deleted by t1016 item 2",
    "probe.py": "module-constant fallback, to be deleted by t1016 item 2",
    "chatui.py": "per-call task budget (num_predict 1200-12000) sized to the chat mode",
    "deliberate.py": "per-call task budget (num_predict 1200-12000) sized to a deliberation turn",
    "interview.py": "per-call task budget (num_predict 1200-12000) sized to the interview question",
    "gemma-decide.py": "per-call task budget (num_predict 1200-12000) sized to the decision",
    "verify_control.py": "per-call task budget (num_predict 1200-12000) sized to the verification",
    "inference_health.py": "residency: keep_alive '30m' so a 55 GB model is not reloaded between probes",
    "truncation_review.py": "residency: keep_alive '24h' so a 55 GB model is not reloaded between turns",
    "ask-gemma-supervise.py": "residency: keep_alive '24h' so a 55 GB model is not reloaded between turns",
}

# settings.py IS the source, and the tests may write literals to prove behaviour.
EXEMPT = {"settings.py"}


def literals(src, path):
    """Every place this file sets a vendor sampling key to a literal number.

    AST, NOT A REGEX (rewritten 2026-09-03 after Q4 audited the first version and called it
    theatre -- correctly). The regex was `"key"\\s*:\\s*([0-9.]+)`, which matches a dict
    literal and nothing else. Q4's escape: `opts["temperature"] = 0.3`, an assignment, passes
    silently. So does `d.update(temperature=0.3)`. A check that catches one syntax for a thing
    with three is a check that says "clean" about code it cannot read.

    Three shapes are covered now, and they are the three Python offers:
      {"temperature": 0.3}          a dict literal
      opts["temperature"] = 0.3     a subscript assignment
      f(temperature=0.3)            a keyword argument
    """
    out = []
    try:
        tree = ast.parse(src)
    except SyntaxError as e:
        return [(f"{path.name} does not parse", e.lineno or 0)]
    for node in ast.walk(tree):
        # {"temperature": 0.3}
        if isinstance(node, ast.Dict):
            for k, v in zip(node.keys, node.values):
                if isinstance(k, ast.Constant) and k.value in VENDOR_KEYS and \
                        isinstance(v, ast.Constant) and isinstance(v.value, (int, float)):
                    out.append((f"{k.value}={v.value}", getattr(node, "lineno", 0)))
        # opts["temperature"] = 0.3
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Subscript) and isinstance(t.slice, ast.Constant) \
                        and t.slice.value in VENDOR_KEYS and isinstance(node.value, ast.Constant) \
                        and isinstance(node.value.value, (int, float)):
                    out.append((f"{t.slice.value}={node.value.value}", node.lineno))
        # f(temperature=0.3)
        elif isinstance(node, ast.Call):
            for kw in node.keywords:
                if kw.arg in VENDOR_KEYS and isinstance(kw.value, ast.Constant) \
                        and isinstance(kw.value.value, (int, float)):
                    out.append((f"{kw.arg}={kw.value.value}", node.lineno))
    return out


def _literal_value(v):
    """True when `v` is a Constant carrying a value that counts as a typed literal.

    bool is excluded on purpose: True/False is a switch, not a budget, and bool is a subclass
    of int, so a bare int test would flag every boolean a caller passes. str and bytes are
    included because keep_alive's legal values are '24h' / '30m', not numbers.
    """
    return isinstance(v, ast.Constant) and isinstance(v.value, (int, float, str, bytes)) \
        and not isinstance(v.value, bool)


def budget_literals(src, path):
    """Every place this file sets a budget/residency key to a LITERAL value.

    The same AST walk as literals(), over BUDGET_KEYS, plus the one shape it has never needed:
    an assignment to a name. That fourth shape is how L-119 actually recurred -- `NUM_CTX =
    262144` at methodology.py:46, inside `except Exception:` -- and a gate blind to it reports
    "clean" with the defect sitting in the file.

    Four shapes, the same three plus the module constant:
      {"num_ctx": 262144}                       a dict literal
      opts["num_predict"] = 16000               a subscript assignment
      settings.options(..., keep_alive="24h")   a keyword argument
      NUM_CTX = 262144                          an assignment to a name, case-insensitive
    A value READ from settings (a name, a subscript, an `or` default) is not a literal, so the
    legal form at a call site reports nothing.
    """
    out = []
    try:
        tree = ast.parse(src)
    except SyntaxError as e:
        return [(f"{path.name} does not parse", e.lineno or 0)]
    for node in ast.walk(tree):
        # {"num_ctx": 262144}
        if isinstance(node, ast.Dict):
            for k, v in zip(node.keys, node.values):
                if isinstance(k, ast.Constant) and k.value in BUDGET_KEYS and _literal_value(v):
                    out.append((f"{k.value}={v.value}", getattr(node, "lineno", 0)))
        # opts["num_predict"] = 16000   and   NUM_CTX = 262144
        elif isinstance(node, ast.Assign):
            if not _literal_value(node.value):
                continue
            for t in node.targets:
                if isinstance(t, ast.Subscript) and isinstance(t.slice, ast.Constant) \
                        and t.slice.value in BUDGET_KEYS:
                    out.append((f"{t.slice.value}={node.value.value}", node.lineno))
                elif isinstance(t, ast.Name) and t.id.lower() in BUDGET_KEYS:
                    out.append((f"{t.id.lower()}={node.value.value}", node.lineno))
        # settings.options(..., keep_alive="24h")
        elif isinstance(node, ast.Call):
            for kw in node.keywords:
                if kw.arg in BUDGET_KEYS and _literal_value(kw.value):
                    out.append((f"{kw.arg}={kw.value.value}", node.lineno))
    return out


def main():
    fails = []
    # RECURSIVE (Q4: "a call site in a subdirectory is invisible"). rglob, not glob.
    files = sorted(set(list((REPO / "projects" / "local-inference").rglob("*.py")) +
                       list((REPO / "scripts").rglob("*.py"))))
    files = [f for f in files if "__pycache__" not in str(f)]
    checked = 0
    print("Call sites that POST to Ollama:")
    for f in files:
        if f.name in EXEMPT:
            continue
        src = f.read_text(encoding="utf-8", errors="replace")
        if "api/chat" not in src and "api/generate" not in src:
            continue
        checked += 1
        bad = [f"{what} at {f.name}:{line}" for what, line in literals(src, f)]
        if bad:
            print(f"  [FAIL]  {f.name}: {'; '.join(bad)}")
            fails.append(f"{f.name} sets sampling values itself: {'; '.join(bad)}. "
                         f"Use {{**settings.sampling(model), ...}} -- the vendor's numbers "
                         f"carry a source URL and these do not.")
        else:
            uses = "settings.sampling" in src or "settings.request" in src or \
                   "settings.options" in src
            print(f"  [{'ok' if uses else 'WARN'}]  {f.name}"
                  f"{'' if uses else '  (no literals, but no settings call either)'}")
    if not checked:
        fails.append("no Ollama call sites found at all -- this check just stopped checking "
                     "anything, which is worse than failing")

    # THE BUDGET PASS (t1016): the same files, the same api/chat / api/generate filter, the
    # second key list. scripts/tests is skipped because the file's own probes write literals to
    # prove behaviour. A file the audit has dispositioned prints a row with its reason; a file
    # with hits and no disposition is a FINDING.
    budget_checked = 0
    print()
    print("Call sites that set a budget/residency key to a literal:")
    for f in files:
        if f.name in EXEMPT or "tests" in f.parts:
            continue
        src = f.read_text(encoding="utf-8", errors="replace")
        if "api/chat" not in src and "api/generate" not in src:
            continue
        budget_checked += 1
        hits = budget_literals(src, f)
        if not hits:
            continue
        sites = "; ".join(f"{what} at {f.name}:{line}" for what, line in hits)
        reason = BUDGET_CALLERS.get(f.name)
        if reason:
            print(f"  {f.name} ({len(hits)}) dispositioned -- {reason}")
        else:
            print(f"  {f.name} ({len(hits)}) FINDING -- {sites}")
            fails.append(f"{f.name} invents budget/residency values: {sites}. Read them from "
                         f"settings.DEFAULTS -- a number typed at a call site drifts from the "
                         f"single source the first time the model behind it changes.")
    if not budget_checked:
        fails.append("the budget pass examined no Ollama call sites at all -- a rule that scans "
                     "nothing is a check that stopped checking, which is worse than failing")

    # THE NEGATIVE CASES, ONE PER SYNTAX. A checker that has only ever passed cannot be told
    # from one that cannot fail (docs/CHECKS.md rule 1) -- and the first version of this test
    # passed while being blind to two of the three shapes below.
    probes = [
        ('body = {"model": m, "options": {"num_ctx": 4096, "temperature": 0.3}}', "dict"),
        ('opts["temperature"] = 0.3', "subscript assignment  <- Q4's escape"),
        ('client.call(model, temperature=0.3)', "keyword argument"),
    ]
    print()
    for src, shape in probes:
        hits = literals(src, Path("probe.py"))
        ok = len(hits) == 1
        print(f"  [{'ok' if ok else 'BAD'}]    control: caught in a {shape} -> {hits}")
        if not ok:
            fails.append(f"the detector is blind to the {shape} form: {src}")

    # THE CONTROL, INVERTED (t1016). This block used to assert that a literal num_ctx is NOT a
    # finding -- "ours to set" was read as "unpoliced", and that is the hole L-119 recurred
    # through. The rule runs the other way now: a literal budget/residency value at a call site
    # IS a finding, and the legal form -- a name whose value comes from settings -- is not.
    ours = budget_literals('body = {"options": {"num_ctx": 262144}}', Path("probe.py"))
    seen = any(h[0] == "num_ctx=262144" for h in ours)
    print(f"  [{'ok' if seen else 'BAD'}]    control: a literal num_ctx is a finding -> {ours}")
    if not seen:
        fails.append(f"the budget rule is blind to a literal num_ctx in a dict: {ours}")
    legal = budget_literals('body = {"options": {"num_ctx": NUM_CTX}}', Path("probe.py"))
    print(f"  [{'ok' if not legal else 'BAD'}]    control: a budget key read from settings is "
          f"legal -> {legal}")
    if legal:
        fails.append(f"the budget rule flags a value read from settings: {legal}")

    # And a same-digest copy must still inherit, since that is the other half of the contract.
    # Any two declared tags with one digest; none left once the Duo's copies were deleted.
    by_digest = {}
    for tag, dg in settings.digests().items():
        by_digest.setdefault(dg, []).append(tag)
    pair = next((sorted(t) for t in by_digest.values() if len(t) > 1), None)
    if settings.digests() and pair is None:
        print("  [SKIP]  no two tags share a digest, so copy inheritance is not tested")
    elif pair:
        a, b = pair[0], pair[1]
        same = settings.sampling(a) == settings.sampling(b)
        print(f"  [{'ok' if same else 'BAD'}]    a copy still inherits its source's profile "
              f"({a} vs {b})")
        if not same:
            fails.append(f"{a} and {b} are the same weights with different sampling")
    else:
        print("  [SKIP]  Ollama not answering; the digest half was not checked. "
              "Nothing was verified -- this is not a pass.")

    for x in fails:
        print("FAIL:", x)
    print("test_sampling_single_source:", "FAILED" if fails else "ok")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
