"""coder.py removes its sandbox when it is stopped with SIGTERM before its attempts (during the baseline)
-- how the memory guard and the 1-hour build cap stop a build. Python runs no `finally` on SIGTERM, so
before 2026-10-20 every such stop left a ~14 GB (now ~1.3 GB) sandbox behind for the sweeper."""
import os
import signal
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CODER = ROOT / "projects" / "local-inference" / "coder.py"
PASSED = FAILED = 0


def report(name, got, want):
    global PASSED, FAILED
    ok = got == want
    PASSED += ok
    FAILED += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"  (got {got!r}, want {want!r})"))


if not (ROOT / ".git").exists():
    print("  no .git here (a coder sandbox) -- this suite needs the real repository; not run")
    sys.exit(0)
home = Path.home() / ".cache" / "hivemind-tmp"
home.mkdir(parents=True, exist_ok=True)
tmp = Path(tempfile.mkdtemp(prefix="sigterm-test-", dir=home))
# nothing answers on this port: the run never gets past its baseline to a model call
env = {**os.environ, "TMPDIR": str(tmp), "CODER_ENDPOINT": "http://127.0.0.1:9/v1/chat/completions"}
p = subprocess.Popen([sys.executable, str(CODER), "--brief", f"sigterm cleanup probe {uuid.uuid4().hex[:8]}",
                      "--files", "workspace/inbox/README.md", "--model", "faketest", "--attempts", "1",
                      "--reads", "0"], cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                     start_new_session=True)
box = None
deadline = time.time() + 120
while time.time() < deadline and p.poll() is None:
    hits = list(tmp.glob(f"coder-*-{p.pid}"))
    if hits and any(hits[0].iterdir()):
        box = hits[0]
        break
    time.sleep(0.2)
report("the run built its sandbox", box is not None and box.is_dir(), True)
# AT ONCE, not after a pause (2026-10-20): with 2 s of grace a fast run could refuse and exit before the
# signal arrived -- red inside the merge gate's copy, green on main. The sandbox exists, so the handler does.
os.kill(p.pid, signal.SIGTERM)
try:
    rc = p.wait(timeout=120)
except subprocess.TimeoutExpired:
    os.killpg(p.pid, signal.SIGKILL)
    rc = "hung"
report("SIGTERM ends the run with 143", rc, 143)
report("and its sandbox is gone", box is not None and not box.exists(), True)
try:
    os.killpg(p.pid, signal.SIGKILL)           # any suite it had started
except ProcessLookupError:
    pass
import shutil
shutil.rmtree(tmp, ignore_errors=True)
print(f"\n{PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
