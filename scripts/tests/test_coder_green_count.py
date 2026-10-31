#!/usr/bin/env python3
"""The 'N suite(s) green' headline must be a POST-patch fact.

WHY (env3 probe, finding 31)
----------------------------
The ACCEPTED announcement printed `len(suites(root)) - len(already)`, where `already` is the
BASELINE failure set -- measured before the agent touched anything. So the sentence reported a
PRE-patch number under a post-patch claim.

It is wrong in the direction that matters. A run whose whole purpose is to turn a red suite
green still counts that suite as failing, so the one number a reader uses to judge the run
UNDER-reports exactly what the run achieved. `failed`, from the accepted attempt, is the set
the sentence has always claimed to be describing.

The two formulas agree whenever the patch fixes nothing, which is most runs -- so this suite
has to construct the case where they disagree: a suite that is RED at baseline and GREEN after
the patch. The assertion is `green > total - already`, which is precisely "no longer the
pre-patch number", and it cannot pass under the old formula, which computes that value exactly.
"""
import re
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import coder_harness as H  # noqa: E402

FIXTURE = "scripts/tests/test_green_count_fixture.py"
BRIEF = "make the fixture suite pass"

RED = '''#!/usr/bin/env python3
"""A deliberately RED suite, created by test_coder_green_count.py inside a throwaway clone."""
import sys
VERDICT = "red"
print("fixture verdict:", VERDICT)
sys.exit(1 if VERDICT == "red" else 0)
'''

OPS = {"ops": [{"path": FIXTURE, "old": 'VERDICT = "red"', "new": 'VERDICT = "green"'}],
       "why": "turn the fixture green"}

# Applies cleanly, changes a real byte, and leaves the suite exactly as red as it was.
NOOP_OPS = {"ops": [{"path": FIXTURE, "old": 'fixture verdict:', "new": 'fixture says:'}],
            "why": "a change that does not fix the target suite"}


def main():
    out = []
    tmp = Path(H.sandbox("greencount-"))
    srv = None
    try:
        clone, err = H.build_clone(tmp)
        if clone is None:
            print(f"  [BAD]  {err}")
            return 1

        # Committed, so it is in HEAD as well as the working tree -- otherwise the delivery
        # guard (finding 32) correctly refuses and this suite never reaches its own question.
        f = clone / FIXTURE
        f.write_text(RED, encoding="utf-8")
        import subprocess
        subprocess.run(["git", "-C", str(clone), "add", FIXTURE], capture_output=True)
        subprocess.run(["git", "-C", str(clone), "-c", "core.hooksPath=/dev/null",
                        "commit", "-qm", "a red fixture suite"], capture_output=True)

        srv, port = H.serve_ops(tmp, OPS)
        run = H.run_coder(clone, port, BRIEF, files=[FIXTURE],
                          extra=["--target", FIXTURE])
        rows = H.ledger_rows(clone)
        last = rows[-1] if rows else {}

        total = len(list((clone / "scripts" / "tests").glob("test_*.py")))
        m_base = re.search(r"(\d+) suite\(s\) already failing", run.stdout)
        m_green = re.search(r"(\d+) suite\(s\) green", run.stdout)
        already = int(m_base.group(1)) if m_base else None
        green = int(m_green.group(1)) if m_green else None

        print("A run that turns a red suite green:")
        out.append(("  is accepted",
                    str(last.get("outcome", "")).startswith("ACCEPTED"), True))
        out.append(("  and the fixture was red at baseline", bool(already), True))
        print(f"         total={total} already={already} green={green} "
              f"outcome={last.get('outcome')!r}")
        if not last or already is None or green is None:
            print(run.stdout[-2500:]); print("STDERR:", run.stderr[-1200:])

        print("\nThe headline counts what is green NOW, not what was green BEFORE:")
        out.append(("  the count is not the pre-patch number",
                    green is not None and already is not None and green > total - already,
                    True))
        out.append(("  and never claims more suites than exist",
                    green is not None and green <= total, True))

        # THE ENTRY CONDITION, IN THE FAILING DIRECTION. Everything above measures a run
        # that SUCCEEDED, and a coder that accepted everything would pass all of it. This
        # asserts the refusal the count depends on for its meaning: a patch that leaves the
        # named target suite RED must NOT be accepted, and the run must exit non-zero.
        # Without it the green count is a number attached to nothing (docs/CHECKS.md rule 1).
        f.write_text(RED, encoding="utf-8")
        subprocess.run(["git", "-C", str(clone), "-c", "core.hooksPath=/dev/null",
                        "commit", "-qam", "fixture red again"], capture_output=True)
        srv.terminate()
        srv, port2 = H.serve_ops(tmp, NOOP_OPS)
        before = len(H.ledger_rows(clone))
        run2 = H.run_coder(clone, port2, BRIEF + " (no-op)", files=[FIXTURE],
                           extra=["--target", FIXTURE])
        rows2 = H.ledger_rows(clone)
        last2 = rows2[-1] if len(rows2) > before else {}
        print("\nA patch that leaves the target suite red:")
        print(f"         outcome={last2.get('outcome')!r} exit={run2.returncode}")
        out.append(("  is NOT accepted",
                    not str(last2.get("outcome", "")).startswith("ACCEPTED"), True))
        out.append(("  and the run exits non-zero", run2.returncode != 0, True))
        out.append(("  and no green count is printed for it",
                    "suite(s) green" not in run2.stdout, True))
    finally:
        if srv:
            srv.terminate()
        shutil.rmtree(tmp, ignore_errors=True)

    bad = 0
    for label, got, want in out:
        if got == want:
            print(f"  [ok]   {label}: {got}")
        else:
            bad += 1
            print(f"  [BAD]  {label}: expected {want}, got {got}")
    print(f"test_coder_green_count: {'ok' if not bad else f'{bad} FAILED'}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
