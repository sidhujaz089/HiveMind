#!/usr/bin/env python3
"""Abandoned repo copies in RAM are reaped on the clock, not by a flag nobody passes.

MEASURED 2026-10-29: /tmp on this box is tmpfs and nine copies of this repository (1.6 GB each, 15 GB) sat in
it, made the same afternoon. MemAvailable was 20.9 GB against a build admission of 12 + 4 GB, and the pressure
gate held every build -- the machine's own test runs owned the memory the builds needed.

`sweep.sweep_tmp_repo_copies()` already existed with the 2026-10-18 incident in its docstring and was called
from ONE place behind a flag. Nothing scheduled it -- worse than no reaper, because the comment reads as
coverage. This pins the clock, the horizon, and what it must NOT touch.
"""
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))


def fake_copy(parent, name, age_s):
    """A directory that looks like a repo copy (sweep.is_repo_copy's marker) with a chosen age."""
    d = parent / name
    (d / "scripts").mkdir(parents=True)
    (d / "scripts" / "dispatch_loop.py").write_text("# a copy\n")
    t = time.time() - age_s
    os.utime(d, (t, t))
    return d


def main():
    import hourly_checks as H
    fails = []

    def check(label, ok, detail=""):
        print(f"  [{'ok' if ok else 'FAIL'}]  {label}" + ("" if ok else f"\n        {detail}"))
        if not ok:
            fails.append(label)

    root = Path(tempfile.mkdtemp())
    old = fake_copy(root, "old-clone", 7200)
    fresh = fake_copy(root, "fresh-clone", 30)
    notcopy = root / "unrelated"
    notcopy.mkdir()
    (notcopy / "notes.txt").write_text("not a repo\n")
    old_t = time.time() - 7200
    os.utime(notcopy, (old_t, old_t))

    out = H.run_tmp_reaper(root=root, max_age_s=3600, ledger=root / "ledger.jsonl")
    check("an abandoned copy past the horizon is REMOVED", not old.exists(), str(out))
    check("  a copy still inside the horizon is left alone", fresh.exists(), str(out))
    check("  a directory that is not a repo copy is never touched", notcopy.exists(), str(out))
    check("  and it reports what it freed", out.get("reaped") == 1 and "freed_gb" in out, str(out))

    check("the horizon is a setting, not a magic number", isinstance(H.TMP_REAP_AGE_S, int) and H.TMP_REAP_AGE_S > 0,
          str(H.TMP_REAP_AGE_S))
    check("  and it is past the longest a suite may run (its cap is 1200 s)",
          H.TMP_REAP_AGE_S >= 1200, str(H.TMP_REAP_AGE_S))

    src = (ROOT / "scripts" / "hourly_checks.py").read_text(encoding="utf-8")
    i = src.index("def run_sweep():")
    window = src[i:i + 2500]
    check("THE SWEEP RUNS THE REAPER ITSELF, before it decides the box is too full",
          "run_tmp_reaper(" in window, "the reaper is still not on a clock")
    # BOTH ROOTS (t9566, 2026-10-29): suites moved to ~/.cache/hivemind-tmp while this reaper swept /tmp alone, and
    # 111 GB of copies filled the SSD. Each root must be reaped, and the loop must actually walk them.
    check("  and it reaps the ON-DISK temp root too, not /tmp alone",
          "/tmp" in H.TMP_REAP_ROOTS and any(r.endswith(".cache/hivemind-tmp") for r in H.TMP_REAP_ROOTS)
          and "for _root in TMP_REAP_ROOTS" in window and "run_tmp_reaper(_root)" in window, str(H.TMP_REAP_ROOTS))
    check("  and says what it freed", "tmp reaper: removed" in window, "")
    print("test_tmp_reaper_runs_on_the_clock:", "FAILED" if fails else "ok")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
