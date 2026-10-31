"""A job the scheduler gives to a role runs on THAT role's server with THAT role's model (2026-10-02).
truncation_review.py and supervise.py posted to 127.0.0.1:11434 whatever the job, so the Senior's
script reviews and supervision ran on the Manager's server. Fake HTTP; no model is called."""
import io, json, os, subprocess, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "scripts"), str(ROOT / "projects" / "local-inference")]
ok = fail = 0
def check(name, cond):
    global ok, fail
    print(f"  [{'ok' if cond else 'FAIL'}]   {name}"); ok += bool(cond); fail += not cond

# truncation_review reads the endpoint at import: run it in a child with the station's env
code = "import sys;sys.path.insert(0,'scripts');import truncation_review as T;print(T.OLLAMA)"
env = dict(os.environ, HIVEMIND_OLLAMA="http://127.0.0.1:11435")
out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, timeout=120).stdout.strip()
check("script review: a station job posts to the station's server", out.endswith("http://127.0.0.1:11435"))
env.pop("HIVEMIND_OLLAMA")
out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, timeout=120).stdout.strip()
check("  and a main-server job to the main server", out.endswith("http://127.0.0.1:11434"))

import supervise
seen = {}
def fake(req, timeout=None):
    seen["url"], seen["model"] = req.full_url, json.loads(req.data)["model"]
    return io.BytesIO(json.dumps({"message": {"content": "{}"}, "done_reason": "stop"}).encode())
supervise.urllib.request.urlopen = fake
os.environ["HIVEMIND_OLLAMA"] = "http://127.0.0.1:11435"
supervise.JOB_MODEL = "senior-model"
try:
    supervise.ask_q16("x")
except Exception:
    pass
check("supervision: the station's server", seen.get("url") == "http://127.0.0.1:11435/api/chat")
check("  and the job's model", seen.get("model") == "senior-model")

src = (ROOT / "scripts" / "dispatch_loop.py").read_text()
check("the dispatcher passes --model to both",
      src.count('*(["--model", job["model"]] if job.get("model") else [])') >= 2)
# A JOB IN ITS OWN SCOPE: restarting whatever ran the tick must not kill it (t617/t620, 07:56Z).
import dispatch_loop as DL
pre = DL._own_scope({"kind": "rca", "chunk": "6ed92e74f7ed.json"})
if os.environ.get("XDG_RUNTIME_DIR"):
    check("a launched job gets its own systemd scope", pre[:3] == ["systemd-run", "--user", "--scope"])
    probe = subprocess.run(pre + ["cat", "/proc/self/cgroup"], capture_output=True, text=True, timeout=60).stdout
    check("  and runs outside the caller's service cgroup", ".scope" in probe and "manager-queue.service" not in probe)
os.environ["HIVEMIND_NO_SCOPE"] = "1"
check("HIVEMIND_NO_SCOPE runs it as before", DL._own_scope({"kind": "rca", "chunk": "x"}) == [])
print(f"\n{ok} passed, {fail} failed")
sys.exit(1 if fail else 0)
