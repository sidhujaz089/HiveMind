#!/usr/bin/env python3
"""Guards on serve.py's container targeting and its residency gate.

Both guards exist because the real system was observed doing the wrong thing on 2026-08-25,
and both are asserted in the FAILING direction here -- a suite that has only ever passed
cannot be distinguished from one that cannot fail (docs/CHECKS.md, rule 1).

  1. `status` printed `not running` while two containers served all day, because every
     function assumed the only possible container was the one `start` creates.
  2. `gpu_gate.verdict` SUMS every process matching a name. With two vLLM containers up, a
     0.5B smoke model cleared a 10,000 MiB floor on the strength of the 35B model beside it:
     46,629 + 2,109 = 48,738 MiB reported under one label. A pass about the wrong process.
"""
import inspect
import sys
import types
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "projects" / "local-inference"))
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "projects" / "local-inference"))

import serve                      # noqa: E402
import gpu_gate                   # noqa: E402

FAILURES = []
RUN = []


def check(name, got, want):
    RUN.append(name)
    ok = got == want
    print(f"  [{'ok' if ok else 'FAIL'}]   {name}: {got!r} (want {want!r})")
    if not ok:
        FAILURES.append(name)


def fake_sh(table):
    """Replace serve.sh with a lookup keyed on the docker subcommand and filter."""
    def _sh(*args, **kw):
        return types.SimpleNamespace(stdout=table.get(args, ""), stderr="", returncode=0)
    return _sh


PS_TWO = ("docker", "ps", "--filter", f"ancestor={serve.IMAGE}", "--format", "{{.Names}}")
TOP_SMOKE = ("docker", "top", "vllm-smoke", "-o", "pid,cmd")


def main():
    print("serve.py container targeting")
    orig = serve.sh

    # Two containers up, neither named `vllm`: the case that produced the false `not running`.
    serve.sh = fake_sh({PS_TWO: "vllm-smoke\nvllm-general\n"})
    check("two containers are both discovered", serve.serving(), ["vllm-smoke", "vllm-general"])

    a = types.SimpleNamespace(container=None)
    name, err = serve.target(a)
    check("ambiguity REFUSES rather than picking one", name, None)
    check("and the refusal names both", bool(err and "vllm-general" in err and "vllm-smoke" in err), True)

    serve.sh = fake_sh({PS_TWO: "vllm-general\n",
                        ("docker", "ps", "--filter", "name=^vllm-general$", "--format", "{{.Status}}"): "Up 9 hours"})
    check("a single container needs no --container", serve.target(types.SimpleNamespace(container=None))[0],
          "vllm-general")

    serve.sh = fake_sh({PS_TWO: ""})
    check("nothing serving is an error, not a guess", serve.target(types.SimpleNamespace(container=None))[1],
          "nothing is serving")

    # engine_pid must pick the EngineCore, not the container's main python process.
    serve.sh = fake_sh({TOP_SMOKE: (
        "PID                 CMD\n"
        "3553740             /usr/bin/python3 /usr/local/bin/vllm serve --model Qwen/Qwen2.5-0.5B\n"
        "3554096             /usr/bin/python3 -c from multiprocessing.resource_tracker import main\n"
        "3554097             VLLM::EngineCore\n")})
    check("engine_pid picks the EngineCore, not the launcher", serve.engine_pid("vllm-smoke"), "3554097")

    serve.sh = fake_sh({TOP_SMOKE: "PID                 CMD\n3553740             /usr/bin/python3 /usr/local/bin/vllm serve\n"})
    check("no EngineCore means no pid, never a fallback", serve.engine_pid("vllm-smoke"), None)

    serve.sh = orig

    # The gate defect itself, on the real numbers observed on 2026-08-25.
    print("\ngpu_gate attribution -- the defect serve.py must not reproduce")
    apps = [{"pid": 3551656, "name": "VLLM::EngineCore", "mib": 46629},
            {"pid": 3554097, "name": "VLLM::EngineCore", "mib": 2109}]

    code, msg = gpu_gate.verdict(apps, match="VLLM::EngineCore", min_mib=10000)
    check("matching BY NAME sums both engines and wrongly passes", code, gpu_gate.RESIDENT)
    check("and the sum it reports is the giveaway", "48738" in msg, True)

    # Asserted against the literal exit code rather than the symbol on purpose: gpu_gate's
    # contract is three distinct codes (0 resident, 1 not resident, 2 cannot tell) and 2 must
    # never be collapsed into either. Pinning 1 pins the contract, not just the name.
    code, msg = gpu_gate.verdict(apps, pid=3554097, min_mib=10000)
    check("matching BY PID refuses -- the guard, proven by failing", code, 1)
    check("and the message says why, not just that", "below the 10000 MiB floor" in msg, True)

    code, _ = gpu_gate.verdict(apps, pid=3551656, min_mib=10000)
    check("and still passes the container that genuinely holds it", code, gpu_gate.RESIDENT)

    # The above proves gpu_gate can be asked either question. This proves serve.gate() asks the
    # right one -- without it, reverting the selection back to --match is invisible to the suite.
    print("\nserve.gate() must select by pid, not by name")
    seen = []

    def capturing(*args, **kw):
        seen.append(args)
        if args[:3] == ("docker", "top", "vllm-smoke"):
            return types.SimpleNamespace(
                stdout="PID CMD\n3554097             VLLM::EngineCore\n", stderr="", returncode=0)
        return types.SimpleNamespace(stdout="", stderr="", returncode=0)

    serve.sh = capturing
    serve.gate(min_mib=10000, name="vllm-smoke")
    argv = [a for a in seen if any("gpu_gate.py" in str(x) for x in a)]
    flat = list(argv[0]) if argv else []
    check("serve.gate passes --pid", "--pid" in flat, True)
    check("serve.gate passes the container's own engine pid", "3554097" in flat, True)
    check("serve.gate NEVER passes --match when it has a container", "--match" in flat, False)
    serve.sh = orig

    print("\npreflight refuses on shared memory, before anything starts")
    # The 2026-08-31 OOM: vLLM pre-allocated its share of a pool Ollama was already holding
    # and died four minutes in. On unified memory the refusal must come first.
    real = (serve.image_present, serve.weights_are_safe, serve.ollama_resident,
            serve.mem_available_gib)
    table = serve.TABLE_DIR / "DONE"
    try:
        serve.image_present = lambda ref: True
        serve.weights_are_safe = lambda root: (True, "ok")
        serve.TABLE_DIR = Path("/nonexistent-table")
        serve.ollama_resident = lambda: ["gemma4:26b"]
        serve.mem_available_gib = lambda: 40.0
        probs = serve.preflight_problems()
        check("a resident Ollama model is a refusal", any("Ollama holds" in p for p in probs), True)
        check("too little MemAvailable is a refusal", any("MemAvailable" in p for p in probs), True)
        check("a missing PLE table is a refusal", any("PLE table" in p for p in probs), True)
        # Negative control: with everything in place there is nothing to refuse, so the
        # assertions above are about the inputs and not a function that always complains.
        serve.TABLE_DIR = Path(__file__).resolve().parent
        serve.ollama_resident = lambda: []
        serve.mem_available_gib = lambda: 200.0
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            serve.TABLE_DIR = Path(d)
            (Path(d) / "DONE").touch()
            check("control: nothing to refuse when it all fits", serve.preflight_problems(), [])
    finally:
        (serve.image_present, serve.weights_are_safe, serve.ollama_resident,
         serve.mem_available_gib) = real
        serve.TABLE_DIR = table.parent

    print("\nthe one docker command keeps the t109 containment and publishes nothing")
    argv = serve.docker_run_args()
    check("internal network", argv[argv.index("--network") + 1], serve.NETWORK)
    check("read-only root", "--read-only" in argv, True)
    check("no published port (-p would expose it on every interface)", "-p" in argv, False)
    check("model and table mounted read-only",
          f"{serve.MODEL_DIR}:/model:ro" in argv and f"{serve.TABLE_DIR}:/ple-table:ro" in argv, True)

    print("\na crashed container's reason is on stderr, and was being thrown away")
    # `docker logs` relays the container's stderr to ITS stderr. serve.py read only .stdout,
    # so "container exited. last lines:" printed an empty string -- on the one path where
    # the logs ARE the answer. Measured on the vLLM out-of-memory of 2026-08-31: stdout
    # 0 bytes, stderr 1,933 bytes. The person running it saw the first half of a sentence.
    #
    # The merge is asserted directly rather than through a container that has actually
    # crashed, so this needs no docker and cannot go quiet when docker is absent.
    check("a reason on stderr alone is kept",
          serve.merge_streams("", "CUDA error: out of memory"),
          "CUDA error: out of memory")
    check("a reason on stdout alone is kept",
          serve.merge_streams("started", ""), "started")
    check("both streams are kept, in order",
          serve.merge_streams("a", "b"), "a\nb")
    check("nothing at all stays empty, so the caller can tell",
          serve.merge_streams("", "  \n "), "")

    src = inspect.getsource(serve.cmd_start)
    check("cmd_start reports through container_logs, not .stdout",
          "container_logs(" in src and ".stdout" not in src, True)
    check("and does not announce lines it does not have",
          src.count("last lines") == 1 and "returned \nnothing" not in src, True)

    # ---- a STOPPED container has no address, and docker says so in its own words ----------
    # Measured 2026-10-27: `docker inspect swift --format '{{...IPAddress...}}'` on a stopped
    # container prints the literal string `invalid IP`. endpoint() passed it straight into the
    # URL, so every consumer got `http://invalid IP:8000` -- urlopen raised InvalidURL, the Hub's
    # Servers table printed it as the server's address, and the readiness probe's reason was
    # "URL can't contain control characters" instead of "the container is stopped".
    INSPECT = ("docker", "inspect", serve.CONTAINER, "--format",
               "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}")
    serve.sh = fake_sh({INSPECT: "invalid IP"})
    check("a stopped container's endpoint is loopback, not docker's words",
          serve.endpoint(), f"http://127.0.0.1:{serve.PORT}")
    serve.sh = fake_sh({INSPECT: "172.18.0.2\n"})
    check("a running container's own address is still used",
          serve.endpoint(), f"http://172.18.0.2:{serve.PORT}")
    serve.sh = orig

    print(f"\n  {len(RUN) - len(FAILURES)} passed, {len(FAILURES)} failed")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
