"""scope_gate: lint inside the scoping session, rewrite on findings, never lose the answer."""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import scope_gate as G  # noqa: E402

PASSED = FAILED = 0


def report(name, got, want):
    global PASSED, FAILED
    ok = got == want
    PASSED += ok
    FAILED += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"  (got {got!r}, want {want!r})"))


def run(lints, rewrites):
    """lints: exit codes scope_lint returns in turn; rewrites: what each ask writes (None = nothing)."""
    d = Path(tempfile.mkdtemp())
    ans = d / "t1.md"
    ans.write_text("original")
    rows, asks = [], []
    lq, wq = list(lints), list(rewrites)

    def lint(a, t):
        return lq.pop(0), "chunk 0: names 9 edit sites"

    def ask(p, a, t, m, r):
        asks.append(p.read_text())
        w = wq.pop(0)
        if w is not None:
            a.write_text(w)
        return 0
    rc = G.gate(ans, "t1", "swift", "manager", lint=lint, ask=ask, row=lambda **r: rows.append(r))
    return rc, ans.read_text(), rows, asks


rc, text, rows, asks = run([0], [])
report("a clean scope goes straight through", (rc, len(asks), rows[0]["outcome"]), (0, 0, "clean"))

rc, text, rows, asks = run([1], [])
report("an answer with no chunks block is not touched", (rc, len(asks)), (1, 0))

rc, text, rows, asks = run([2, 0], ["fixed"])
report("findings -> one rewrite -> clean", (rc, text, len(asks)), (0, "fixed", 1))
report("the fix prompt carries the findings", "names 9 edit sites" in asks[0], True)
report("and asks for the WHOLE answer", "WHOLE answer" in asks[0], True)
report("each round is a row", [r["outcome"] for r in rows], ["rewritten", "clean"])

rc, text, rows, asks = run([2, 2, 2], ["try1", "try2"])
report("stops after MAX_ROUNDS rewrites", (rc, len(asks), rows[-1]["outcome"]), (2, 2, "gave-up"))

rc, text, rows, asks = run([2], [None])
report("a rewrite that writes nothing keeps the original answer", (text, rc), ("original", 2))
report("and says so", rows[0]["outcome"].startswith("no-rewrite"), True)

long = "x" * (G.FINDINGS_LIMIT + 50)
report("a bound on findings handed to the model announces itself", "[TRUNCATED after" in G.clip(long), True)

print(f"\n{PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
