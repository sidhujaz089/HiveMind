#!/usr/bin/env python3
"""A patch that satisfies X while breaking a suite asserting NOT-X is IMPOSSIBLE, not failed (A8, 2026-10-30).

The brief: *"A8 an oracle-conflict CLASSIFIER: a patch that satisfies X breaks a suite asserting NOT-X on the same
symbol -> class oracle-conflict, route to Technical, never retry. Not a pre-build static check (t1432's target was
not green when the collateral failed)."*

t1432 is the shape: its target demands a MAX_TOKENS_FIRST cap while two older suites assert `first_budget` returns
all 250,144 tokens. No patch satisfies both, so every attempt spends a build and escalates -- the funnel the RCA
found. This runs on the OUTCOME (target green, collateral red), which is exactly why it cannot be a pre-build check.

WHAT IT CAN AND CANNOT KNOW. It does not parse English and does not claim to. It compares the LITERAL a suite
asserts about a symbol -- `assert first_budget(...) == 250144` against `... == 2048` -- and calls it a **conflict**
when the same symbol carries two different literals in two suites. When the symbol is shared but no literal can be
extracted it says **overlap** instead: still worth routing to Technical, but not proof of contradiction, and a
classifier that overstates is how a queue gets parked for the wrong reason.

    python3 scripts/oracle_conflict.py --target <suite> --collateral <suite> [--collateral <suite>] --symbol sym
"""
import argparse
import ast
import sys
from pathlib import Path

CONFLICT = "oracle-conflict"
OVERLAP = "oracle-overlap"

# A comparison whose literal can be read: `x == 250144`, `x != 3`, `assertEqual(x, 4)`, `assert x is True`.
_LITERAL_HINT = ("assert", "==", "!=", "assertEqual", "assertIn", "assertTrue", "assertFalse")


def _source(path):
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError as e:                                            # noqa: BLE001 -- said, not swallowed
        print(f"  NOTE: {path} unreadable ({type(e).__name__}: {e}) -- not compared", file=sys.stderr)
        return ""


def _literal_after(text, symbol):
    """The literal this line compares `symbol` against, or None.

    Only a LITERAL counts. A comparison against another expression is not evidence of what the suite believes,
    and guessing there would turn every shared variable name into a "contradiction".
    """
    if symbol not in text:
        return None
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return None
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare):
            continue
        operands = [node.left] + list(node.comparators)
        names = [n for n in operands if isinstance(n, ast.Name) and n.id == symbol]
        if not names:
            # the symbol may be called rather than named: first_budget(...) == 5
            calls = [n for n in operands if isinstance(n, ast.Call)]
            names = [c for c in calls if isinstance(c.func, ast.Name) and c.func.id == symbol]
        if not names:
            continue
        for n in operands:
            if isinstance(n, ast.Constant) and not isinstance(n, bool):
                return repr(n.value)
            if isinstance(n, ast.Constant) and isinstance(n.value, bool):
                return repr(n.value)
    return None


def _lines_on(source, symbol):
    return [ln.strip() for ln in source.splitlines() if symbol in ln and any(h in ln for h in _LITERAL_HINT)]


def classify(symbols, target_suites, collateral_suites):
    """{class, symbol, suites, values, evidence} for the first symbol the two sides disagree about.

    THE SIDES ARE COUNTED SEPARATELY, which the first version got wrong: it pooled every literal it found, so a
    target asserting one value and a collateral that never compared anything looked like agreement. A conflict
    needs a literal on BOTH sides; a collateral that only mentions the symbol is overlap, which claims less and
    still routes.

    `class` is None when there is nothing to say -- no shared symbol, or nothing comparable anywhere. The caller
    keeps whatever rule it had; this never invents a conflict out of an empty comparison.
    """
    empty = {"class": None, "symbol": None, "suites": [], "values": [], "evidence": []}
    for symbol in [s for s in (symbols or []) if s]:
        side = {"target": {}, "collateral": {}}
        for which, suites in (("target", target_suites), ("collateral", collateral_suites)):
            for path in (suites or []):
                for line in _lines_on(_source(path), symbol):
                    lit = _literal_after(line, symbol)
                    if lit is not None:
                        side[which].setdefault(lit, (str(path), line))
        tvals, cvals = sorted(side["target"]), sorted(side["collateral"])
        if tvals and cvals and set(tvals) != set(cvals):
            pairs = list(side["target"].values()) + list(side["collateral"].values())
            return {"class": CONFLICT, "symbol": symbol,
                    "suites": sorted({p for p, _ in pairs}),
                    "values": sorted(set(tvals) | set(cvals)),
                    "evidence": [f"{p}: {ln}" for p, ln in pairs][:4]}
        if cvals or any(symbol in _source(p) for p in (collateral_suites or [])):
            # mentioned, but the two sides do not state two different literals: OVERLAP, not proof
            paths = [str(p) for p in (collateral_suites or []) if symbol in _source(p)]
            lines = [f"{p}: {ln.strip()}" for p in paths for ln in _source(p).splitlines() if symbol in ln][:2]
            return {"class": OVERLAP, "symbol": symbol, "suites": paths, "values": [] or tvals, "evidence": lines}
    return empty


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--symbol", action="append", required=True)
    ap.add_argument("--target", action="append", default=[])
    ap.add_argument("--collateral", action="append", default=[])
    a = ap.parse_args(argv)
    got = classify(a.symbol, a.target, a.collateral)
    if not got["class"]:
        print("  no conflict: no symbol is asserted two different ways")
        return 0
    print(f"  {got['class']} on {got['symbol']}")
    for line in got["evidence"]:
        print(f"    {line}")
    if got["class"] == CONFLICT:
        print("  A patch cannot satisfy both. Route to Technical and do NOT retry.")
    else:
        print("  Shared symbol, no comparable literals: route to Technical, but this is not proof of contradiction.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
