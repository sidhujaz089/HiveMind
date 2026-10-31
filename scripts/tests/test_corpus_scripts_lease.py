"""t319: corpus_scripts.ask takes the model's lease around every call and releases it, even when
the call fails -- so the scheduler sees the model busy during a --redo."""
import io, json, sys, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import corpus_scripts as C
import infer  # the call goes through infer.chat since 2026-10-06 (Swift is not on Ollama)
import modellease as M

events, ok, fail = [], 0, 0
def check(name, cond):
    global ok, fail
    print(f"  [{'ok' if cond else 'FAIL'}]   {name}"); ok += bool(cond); fail += not cond

lease_file = Path(tempfile.mkdtemp()) / "lease.json"
M.lease_for = lambda model, **k: lease_file
real_acq, real_rel = M.acquire, M.release
M.acquire = lambda *a, **k: (events.append("acquire"), real_acq(*a, **k))[1]
M.release = lambda *a, **k: (events.append("release"), real_rel(*a, **k))[1]

def fake_chat(body, timeout=None, **k):
    events.append("held-during-call" if lease_file.exists() else "NOT-held-during-call")
    return {"message": {"content": "hi"}, "done_reason": "stop"}
infer.chat = fake_chat
out = C.ask("x")
check("the generation call runs inside the lease", events[-3:] == ["acquire", "held-during-call", "release"])
check("the lease is gone afterwards", not lease_file.exists())
check("the reply is returned", out == ("hi", "stop"))

def boom(body, timeout=None, **k):
    raise OSError("server down")
infer.chat = boom
events.clear()
try:
    C.ask("x")
except OSError:
    pass
check("a failed call still releases the lease", events[-1] == "release" and not lease_file.exists())
print(f"\n{ok} passed, {fail} failed")
sys.exit(1 if fail else 0)
