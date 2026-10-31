"""t456: the memory ledger parses model loads from Ollama's journal (weights summed across devices,
KV per slot, compute, Ollama's own plan), and fits() says whether a model would fit. No live calls."""
import sys, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import memory_ledger as M

ok = fail = 0
def check(name, cond):
    global ok, fail
    print(f"  [{'ok' if cond else 'FAIL'}]   {name}"); ok += bool(cond); fail += not cond

J = """2026-10-01T22:17:20-0600 x ollama[1]: time=... msg="llama-server model fits alongside existing models" predicted="22.9 GiB" predicted_num_ctx=131072 num_batch=2048
2026-10-01T22:17:21-0600 x ollama[1]: time=... msg="starting llama-server" cmd="/usr/local/lib/ollama/llama-server --model /m/blobs/sha256-abc123 --port 1"
2026-10-01T22:17:22-0600 x ollama[1]: load_tensors:        CUDA0 model buffer size =  1000.00 MiB
2026-10-01T22:17:22-0600 x ollama[1]: load_tensors:    CUDA_Host model buffer size =   24.00 MiB
2026-10-01T22:17:23-0600 x ollama[1]: llama_kv_cache: size =  512.00 MiB ( 65536 cells,  16 layers,  2/2 seqs), K (q8_0): 256 MiB, V (q8_0): 256 MiB
2026-10-01T22:17:23-0600 x ollama[1]: sched_reserve:      CUDA0 compute buffer size =    30.00 MiB
2026-10-01T22:17:23-0600 x ollama[1]: sched_reserve:  CUDA_Host compute buffer size =     6.00 MiB
""".splitlines()
r = M.parse_loads(J, tags={"abc123": "q:test"})
check("one load parsed, named by its blob's tag", len(r) == 1 and r[0]["tag"] == "q:test")
check("  weights summed across devices", r and r[0]["weights_mib"] == 1024.0)
check("  KV per slot = size / seqs", r and r[0]["kv_per_slot_mib"] == 256.0 and r[0]["kv_seqs"] == 2)
check("  compute summed", r and r[0]["compute_mib"] == 36.0)
check("  Ollama's plan carried onto the load", r and (r[0]["predicted_gb"], r[0]["predicted_ctx"]) == (22.9, 131072))
check("an unknown blob is named by its whole sha, not a cut one",
      M.parse_loads(J, tags={})[0]["tag"] == "blob abc123")
check("fits: 10 GB + 2x1 GB KV + 12 reserve in 30 available", M.fits(10, 1024, 2, available_gb=30)[0] is True)
check("  and not in 20", M.fits(10, 1024, 2, available_gb=20)[0] is False)
f = Path(tempfile.mkdtemp()) / "meminfo"
f.write_text("MemTotal:       127526000 kB\nMemFree: 1 kB\nMemAvailable:    20971520 kB\n")
check("meminfo reads total and available", M.meminfo(f) == {"total_gb": 121.62, "available_gb": 20.0})
print(f"\n{ok} passed, {fail} failed")
sys.exit(1 if fail else 0)
