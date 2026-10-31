#!/usr/bin/env python3
"""t296: check_workspace's session-start checks must not run the retired family-ledger
section census.

The census was retired as a load-completeness gate on 2026-09-12 by Jas's direction ("Do not
run scripts/check_ledger.py as a load-completeness gate going forward"); the byte digest
sidecar (workspace/corpus-digests.json) is what supersedes it. scripts/check_workspace.py's
check_drifting_claims still runs it, so every session start prints
'[PASS] The family ledger passes its own section census (12/12 sections, header and foot
present)'. A check enforcing a rule the system retired reads to the next session as coverage
that was deliberately given up.

Behaviour, not shape: a session-start check run over a repository that HAS a complete
12-section ledger must emit no result naming the census. On main the census block runs and
emits one -- which is why this suite is red today. The STATE.md / PROJECT-INDEX assertions are
the anti-vacuity pair: check_drifting_claims must still run end-to-end, so a build that
deletes the whole method to make the census assertion pass fails them.

Temp dirs only. Nothing in the live repository is read, written, or spawned -- the only
subprocess is `git init` inside the fixture.
"""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from check_workspace import Checker  # noqa: E402

PASSED = FAILED = 0

LEDGER_PATH = "chat/projects/family/State_Ledger_Family_Project.txt"
ALL12 = list(range(1, 13))


def report(name, got, want):
    global PASSED, FAILED
    ok = got == want
    PASSED += ok
    FAILED += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {name}"
          + ("" if ok else f"   (got {got!r}, want {want!r})"))


def git(repo, *a):
    return subprocess.run(["git", "-C", str(repo), *a],
                          capture_output=True, text=True, timeout=60)


def ledger(sections):
    """The shape the retired census accepted: one section per number, three entries each,
    plus the canary foot it grepped for. 12 = complete, 8 = truncated."""
    out = ["# State Ledger\n\n**Last updated:** August 14, 2026\n\n"]
    for n in sections:
        out.append(f"## {n}. Section {n}  (3)\n")
        out += [f"- entry {k}\n" for k in range(3)]
        out.append("\n")
    out.append("**Load-check value:** `2026-08-14 20:49 MT - cedar`\n")
    return "".join(out)


def repo_with(files):
    """A throwaway git repo holding exactly `files` -- no live state touched."""
    r = Path(tempfile.mkdtemp(prefix="t296-")) / "repo"
    for rel, text in files.items():
        p = r / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    git(r, "init", "-q", "-b", "main")
    git(r, "config", "user.email", "t@t")
    git(r, "config", "user.name", "t")
    git(r, "add", "-A")
    git(r, "commit", "-q", "-m", "fixture")
    return r


def run_checks(repo, method):
    """Every (status, detail) that method emits at session start, keyed by result name."""
    out = {}
    c = Checker(repo, quick=True)
    c.result = lambda s, n, d=None: out.__setitem__(n, (s, d or ""))
    c.section = lambda t: None
    getattr(c, method)()
    return out


def census_names(out):
    return sorted(n for n in out if "section census" in n.lower())


def main():
    # --- 1. a clone that HAS the ledger: the census must not run at session start ---------
    r = repo_with({LEDGER_PATH: ledger(ALL12)})
    out = run_checks(r, "check_drifting_claims")
    report("a present ledger emits no 'section census' result", census_names(out), [])
    # Negative assertion: the retired check's name must not survive under any phrasing.
    report("  the retired check is not reported under a 'family ledger' name either",
           sorted(n for n in out if "family ledger" in n.lower()), [])

    # --- 2. a truncated ledger: the retired check fires on nothing at all ------------------
    r2 = repo_with({LEDGER_PATH: ledger(ALL12[:8])})
    out2 = run_checks(r2, "check_drifting_claims")
    report("a truncated ledger emits no 'section census' result either",
           census_names(out2), [])

    # --- 3. ANTI-VACUITY: check_drifting_claims must still run end-to-end ------------------
    # Deleting the method (or returning early from it) silences (1) and (2) and breaks these.
    for needle in ("STATE.md", "PROJECT-INDEX"):
        report(f"  check_drifting_claims still runs its {needle} check",
               any(needle in n for n in out), True)
    report("  check_drifting_claims emits results at all", len(out) > 0, True)

    # --- 4. gone from the check's own source, not merely silent -----------------------------
    # The PASS line the task complains of is built from strings that live in check_workspace.py;
    # a block that stays and is merely short-circuited keeps naming a retired rule.
    cw = (ROOT / "scripts" / "check_workspace.py").read_text(encoding="utf-8")
    report("check_workspace.py no longer mentions the section census",
           "section census" in cw, False)
    report("check_workspace.py no longer imports check_ledger.census",
           "from check_ledger import census" in cw, False)

    for d in (r, r2):
        shutil.rmtree(d.parent, ignore_errors=True)
    print(f"\n{PASSED} passed, {FAILED} failed\n")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
