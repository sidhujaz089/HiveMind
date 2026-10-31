"""t206: prune keeps the newest N bundles that VERIFY, refuses when fewer than N verify, never
counts a failed bundle, never touches the live directory, and removes nothing without --apply."""
import os, sys, tempfile, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import backup_corpora as B

ok = fail = 0
def check(name, cond):
    global ok, fail
    print(f"  [{'ok' if cond else 'FAIL'}]   {name}"); ok += bool(cond); fail += not cond

def make(root, name, age, good=True):
    d = root / name / "ai-workspace-corpora"
    d.mkdir(parents=True)
    (d / B.MANIFEST).write_text("x  a\n" if good else "BAD\n")
    t = time.time() - age * 86400
    os.utime(d / B.MANIFEST, (t, t))
    return root / name

verify = lambda b: (b / B.MANIFEST).read_text() != "BAD\n"
r = Path(tempfile.mkdtemp())
(r / B.LIVE_NAME).mkdir()
(r / B.LIVE_NAME / B.MANIFEST).write_text("x  a\n")
for i in range(1, 6):
    make(r, f"ai-workspace-corpora-day{i}", i, good=(i != 1))   # newest (day1) is DAMAGED
B.do_prune(r, keep=3, verify=verify)
check("report only: nothing removed without --apply", len(list(r.iterdir())) == 6)
B.do_prune(r, keep=3, apply=True, verify=verify)
left = sorted(p.name for p in r.iterdir())
check("keeps the newest 3 that VERIFY -- the damaged newest is not one of them",
      left == ["ai-workspace-corpora", "ai-workspace-corpora-day2", "ai-workspace-corpora-day3", "ai-workspace-corpora-day4"])
check("  and the live directory is never touched", (r / B.LIVE_NAME).is_dir())
r2 = Path(tempfile.mkdtemp())
make(r2, "a", 1); make(r2, "b", 2, good=False); make(r2, "c", 3, good=False)
check("fewer than 3 verify: REFUSED, nothing removed",
      B.do_prune(r2, keep=3, apply=True, verify=verify) == 1 and len(list(r2.iterdir())) == 3)
print(f"\n{ok} passed, {fail} failed")
sys.exit(1 if fail else 0)
