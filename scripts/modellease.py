#!/usr/bin/env python3
"""The interlock: a measurement in flight owns its model.

NOT `scripts/models.py lock`, which records observed DIGESTS so sampling resolves without
calling Ollama. That is a record of what is on the box; this is a claim on what may happen to
it next. The names are close enough that a later session will conflate them.

On 2026-09-07 a two-hour, 35-item measurement lost two items to HTTP 500. Not the model and
not the extractor: Jas was switching models in Open WebUI, which shares one Ollama with the
measurement and everything else. Ollama's log shows three 500s and thirteen load/unload
events inside the run window. A browser tab silently corrupted a two-hour measurement, and it
was noticed only because he remembered clicking.

Jas's decision, 2026-09-08, reaffirmed against three reviewers who wanted a neutral proxy
instead: the interlock lives in OUR stack rather than in something that would also bless a
third-party UI. This module is the mechanism; the Console is where it is honoured.

TWO PROPERTIES THAT LOOK LIKE DETAILS AND ARE NOT. The lease EXPIRES, because a crashed run
holding the model forever is a worse outage than the bug being fixed. And a torn lease file
FAILS OPEN, because an interlock that wedges every model call when its own state is corrupt
has become the outage.

**The contract lives in `scripts/tests/test_modellease.py` and is enforced there, not here.**
"""

import json
import os
import sys
import time
import uuid
from pathlib import Path


class Held(Exception):
    """Raised when an action is refused because the model is held by another holder."""


def held(path):
    """Return the live lease dict, or None if free, expired, or corrupt."""
    p = Path(path)
    if not p.exists():
        return None
    try:
        with open(p) as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    if data.get("status") == "broken":
        return None
    expires = data.get("expires")
    if expires is None:
        return None
    if expires <= time.time():
        return None
    holder = data.get("holder", "")
    pid_str = holder.split("-", 1)[0] if "-" in holder else holder
    try:
        pid = int(pid_str)
    except (ValueError, AttributeError):
        return data
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return None
    except PermissionError:
        return data
    return data


def lease_for(model, env=None, manager_model=None):
    """The lease guarding THIS model (2026-09-30). scoping-lease.json guards the Manager's model on
    the main server; every other model has workspace/leases/<model>.json; a station's own lease
    (HIVEMIND_LEASE) wins. Until this, ask_q16 and console.ask both took scoping-lease.json whatever
    model they asked, so a Gemma job (the Frontline's assigned item t493) was refused while a Q4
    session review held Q4's lease -- twice, once in each."""
    import re
    env = os.environ if env is None else env
    repo = Path(__file__).resolve().parent.parent
    if env.get("HIVEMIND_LEASE"):
        return Path(env["HIVEMIND_LEASE"])
    if manager_model is None:
        try:
            import roles
            manager_model = roles.for_job("scoping", "qwen3.8:27b-mtp-bf16")   # the scoping lease belongs to the Scoping job
        except Exception as e:                                      # noqa: BLE001 -- said, not swallowed
            print(f"  NOTE: the Manager's model is unknown ({type(e).__name__}); {model} uses the "
                  f"scoping lease", file=sys.stderr)
            return repo / "workspace" / "scoping-lease.json"
    if model == manager_model:
        return repo / "workspace" / "scoping-lease.json"
    return repo / "workspace" / "leases" / (re.sub(r"[^A-Za-z0-9._-]+", "_", model) + ".json")


_REPO = Path(__file__).resolve().parent.parent
LEDGER = _REPO / "workspace" / "run-logs" / "lease-ledger.jsonl"
_DEFAULT_LEDGER = LEDGER


def _ledger(event, path, **fields):
    """One row per acquire, refusal and release (t523): WHO held which model's slot, for what, and
    who was turned away. The slot ledger (slot_events.py) says when Ollama's slot was busy; this
    says which of our jobs it was. Never raises -- a ledger that cannot be written must not take
    a lease decision down with it."""
    # ONLY THIS REPOSITORY'S LEASES. A suite's fixture lease lives in a temp dir and is not a job;
    # without this the first run of the lease suites wrote 13 fixture rows into the real ledger.
    # A test that wants rows points LEDGER somewhere else.
    if LEDGER == _DEFAULT_LEDGER and not Path(path).resolve().is_relative_to(_REPO):
        return
    try:
        LEDGER.parent.mkdir(parents=True, exist_ok=True)
        with LEDGER.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                                "event": event, "lease": Path(path).name, "pid": os.getpid(),
                                **fields}) + "\n")
    except OSError as e:
        print(f"  NOTE: lease ledger not written ({e})", file=sys.stderr)


def acquire(path, model, purpose, ttl_seconds, holder=None):
    """Acquire a lease on a model -- one of its server's SLOTS. Raises Held when every slot is held.

    SLOTS (2026-10-01). The main Ollama runs every model with OLLAMA_NUM_PARALLEL=2 (since
    2026-09-22, t361), but this lease was one file, so the second slot never carried work: a second
    job was refused "held" and Q4 -- the box's bottleneck -- ran at half the rate it could. Measured
    that day: a request in slot 2 decoded at 6.5-7.3 tok/s beside an RCA in slot 1, the rate one
    stream gets alone. Slot k > 1 is <name>.slot<k>.json beside the main file, and the lease returned
    names its own file, so release() frees the right one. A path with no server behind it has 1."""
    paths = slot_paths(path)
    first = None
    for sp in paths:
        existing = held(sp)
        if existing is None:
            break
        first = first or existing
    else:
        _ledger("refused", path, model=model, purpose=purpose, holder=holder,
                held_by=first.get("holder"), held_for=first.get("purpose"))
        raise Held(
            f"model '{first['model']}' is held by '{first.get('holder', 'unknown')}' "
            f"for '{first['purpose']}' until {first['expires']:.1f} (expires)"
            + (f" -- and all {len(paths)} of its slots are taken" if len(paths) > 1 else "")
        )
    if holder is None:
        holder = f"{os.getpid()}-{uuid.uuid4().hex[:8]}"
    lease = {
        "model": model,
        "purpose": purpose,
        "holder": holder,
        "acquired": time.time(),
        "expires": time.time() + ttl_seconds,
    }
    p = Path(sp)
    if p != Path(path):
        lease["path"] = str(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = str(p) + f".tmp.{os.getpid()}"
    with open(tmp, "w") as f:
        json.dump(lease, f)
    os.replace(tmp, str(p))
    _ledger("acquired", p, model=model, purpose=purpose, holder=holder, ttl_s=ttl_seconds)
    return lease


_SLOTS = {}


def _num_parallel(cmd):
    import re
    import subprocess
    try:
        env = subprocess.run(cmd, capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError) as e:
        print(f"  NOTE: slot count unreadable ({e}); 1 slot assumed", file=sys.stderr)
        return 1
    m = re.search(r"OLLAMA_NUM_PARALLEL=(\d+)", env)
    return max(1, int(m.group(1))) if m else 1


_SERIAL_ARCHS, _ARCH = None, {}


def serial_archs():
    """Architectures Ollama itself refuses to run in parallel, from its own journal warnings.

    THE UNIT'S SETTING IS NOT WHAT THE SERVER DOES (2026-10-02). Jas raised OLLAMA_NUM_PARALLEL to 4
    and the scheduler read 4, but the load line said '-np 1, n_slots = 1': 'model architecture does
    not currently support parallel requests architecture=qwen35' (71 such warnings in 30 days). So
    the "second slot" of 09-22 onward never existed for Qwen; extra jobs queued inside Ollama. Read
    from the journal, not a list kept here, so an Ollama that adds support is believed at once."""
    import re
    import subprocess
    global _SERIAL_ARCHS
    # CACHED ON DISK FOR 6 H: a 30-day journal read is ~9 s, and every model call asks.
    cache = _REPO / "workspace" / "run-logs" / "serial-archs.json"
    if _SERIAL_ARCHS is None:
        if cache.is_file() and time.time() - cache.stat().st_mtime < 6 * 3600:
            try:
                _SERIAL_ARCHS = set(json.loads(cache.read_text(encoding="utf-8")))
            except (OSError, ValueError) as e:
                print(f"  NOTE: {cache.name} unreadable ({e}); reading the journal", file=sys.stderr)
    if _SERIAL_ARCHS is None:
        _SERIAL_ARCHS = set()
        for cmd in (["journalctl", "-u", "ollama", "--since", "-30d", "--no-pager", "-o", "cat"],
                    ["journalctl", "--user", "-u", "ollama-station2", "--since", "-30d", "--no-pager",
                     "-o", "cat"]):
            try:
                out = subprocess.run(cmd, capture_output=True, text=True, timeout=60).stdout
            except (OSError, subprocess.SubprocessError) as e:
                print(f"  NOTE: journal unreadable ({e}); parallel support taken from the unit",
                      file=sys.stderr)
                continue
            _SERIAL_ARCHS |= set(re.findall(r'support parallel requests" architecture=(\w+)', out))
        try:
            cache.write_text(json.dumps(sorted(_SERIAL_ARCHS)), encoding="utf-8")
        except OSError as e:
            print(f"  NOTE: {cache.name} not written ({e})", file=sys.stderr)
    return _SERIAL_ARCHS


def model_arch(model):
    if model not in _ARCH and _engine_slots(model):
        _ARCH[model] = None             # served by another engine: Ollama's /api/show cannot know it
    if model not in _ARCH:
        import urllib.request
        try:
            req = urllib.request.Request("http://127.0.0.1:11434/api/show",
                                         data=json.dumps({"model": model}).encode(),
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=30) as r:
                _ARCH[model] = json.loads(r.read()).get("model_info", {}).get("general.architecture")
        except Exception as e:                                      # noqa: BLE001 -- said, not swallowed
            print(f"  NOTE: architecture of {model} unknown ({type(e).__name__}: {e})", file=sys.stderr)
            _ARCH[model] = None
    return _ARCH[model]


def _lease_model(p):
    """The model a lease guards, when the path says: the Manager's on the main lease, the Senior's on
    station 2's. None otherwise (the caller can pass it)."""
    role = {"scoping-lease.json": "manager", "scoping-lease-station2.json": "senior"}.get(p.name)
    if not role:
        return None
    try:
        import roles
        return roles.tag(role, None)
    except Exception as e:                                          # noqa: BLE001 -- said, not swallowed
        print(f"  NOTE: the {role}'s model is unknown ({type(e).__name__}: {e})", file=sys.stderr)
        return None


def slots(path, model=None):
    """How many requests the server behind this lease decodes at once -- the box's own setting
    (OLLAMA_NUM_PARALLEL in the unit), never a number kept here. Unknown paths have 1. And 1 for a
    model whose architecture Ollama runs serially whatever the unit says (serial_archs): more lease
    slots than the server has only queue the extra callers inside Ollama, each timing out 4x later."""
    eng = _engine_slots(model or _lease_model(Path(path)))
    if eng:
        return eng
    n = _unit_slots(path)
    m = model or (_lease_model(Path(path)) if n > 1 else None)
    return 1 if n > 1 and m and model_arch(m) in serial_archs() else n


def _engine_slots(model):
    """Slots a NON-Ollama engine declares for `model` in SERVERS.json, else None (2026-10-06,
    docs/ENGINES.md). Ollama's unit setting and journal describe Ollama; vLLM batches up to its
    own max_num_seqs, so its capacity is declared on its server entry."""
    if not model:
        return None
    try:
        sys.path.insert(0, str(_REPO / "scripts"))
        import capacity_model
        sys.path.insert(0, str(_REPO / "projects" / "local-inference"))
        import infer
        for s in infer.servers().values():
            if s.entry["engine"] != "ollama" and model in (s.entry.get("models") or {}):
                declared = s.entry.get("slots")
                declared = int(declared) if declared else None
                # THE ENGINE WINS OVER THE FILE (A2, 2026-10-30). This used to be `n = declared`, and with
                # SERVERS.json saying 8 while the engine ran --max-num-seqs 4 the lease and the scheduler could
                # admit twice what the engine batches. Read the live flag; fall back to the declared number and
                # say that it is a claim.
                n, _why = capacity_model.resolved_slots(declared, capacity_model.engine_slots(), model)
                # ROLE LANES (2026-10-08): the model serves what its roles' Lanes add up to, within
                # the engine's ceiling (slots = vLLM max_num_seqs). The lease and the scheduler both
                # read this, so the Models tab setting is the one number.
                try:
                    sys.path.insert(0, str(_REPO / "scripts"))
                    import task_routing
                    want = task_routing.model_lanes(task_routing.load(), model)
                except Exception as e:                              # noqa: BLE001 -- said; the ceiling holds
                    print(f"  NOTE: role lanes unread ({type(e).__name__}: {e})", file=sys.stderr)
                    want = None
                if want is not None:
                    return max(1, min(want, n) if n else want)
                return n
    except Exception as e:                                          # noqa: BLE001 -- said, not swallowed
        print(f"  NOTE: engine slots for {model} unread ({type(e).__name__}: {e})", file=sys.stderr)
    return None


def _unit_slots(path):
    p = Path(path)
    if p.name == "scoping-lease-station2.json":
        key, cmd = "station2", ["systemctl", "--user", "show", "ollama-station2.service", "-p", "Environment"]
    elif p.name == "scoping-lease.json" or p.parent.name == "leases":
        key, cmd = "main", ["systemctl", "show", "ollama.service", "-p", "Environment"]
    else:
        return 1
    if key not in _SLOTS:
        _SLOTS[key] = _num_parallel(cmd)
    return _SLOTS[key]


def slot_paths(path):
    p = Path(path)
    return [p] + [p.with_name(f"{p.stem}.slot{k}{p.suffix}") for k in range(2, slots(p) + 1)]


def issue_concurrent(base_url, models, per_call_budget_s=600.0, timeout_s=1800.0):
    """Fire every model's chat request at once and report each lane's OWN elapsed seconds.

    THE QUESTION NOTHING ELSE HERE ANSWERS. slots(), acquire() and release() govern who may
    USE a slot; none of them can say whether two requests sent to one server at the same time
    actually RAN at the same time. On 2026-09-19 an interactive session was starved for hours
    by a model loop holding the one slot, and it surfaced only as a timeout. A caller that
    loops over models sequentially sees every call succeed, so it can never learn that a second
    slot exists, that a serial architecture queues inside Ollama whatever the unit says (see
    serial_archs), or that a reply was cut off at the output ceiling (t238).

    Each lane is timed from just before ITS request is sent to just after ITS response is read,
    and the budget test is on that lane's seconds, never on the total: two lanes on a 2-slot
    server each finish inside one call's budget, while on a 1-slot server the second waits for
    the first to release the slot and its own seconds show the queue.

    Read-only by design -- no lease, no subprocess, no journal, no file -- because "does this
    server multiplex?" has to be askable without claiming the model the question is about.
    """
    import importlib.util
    import threading
    import urllib.request

    models = list(models)
    rows = [None] * len(models)
    chat_url = (base_url or "").rstrip("/") + "/api/chat"

    # THE CHAT BODY IS THE CONTRACT, NOT A DICT WE WRITE HERE (docs/skills/local-model-loading;
    # settings.request). Resolved INSIDE the function: a module-level import of
    # projects/local-inference/settings.py would tie every import of this module to a script
    # that reaches for roles, SERVERS.json and Ollama.
    settings = None
    _spath = (Path(__file__).resolve().parents[1]
              / "projects" / "local-inference" / "settings.py")
    try:
        if _spath.is_file():
            _spec = importlib.util.spec_from_file_location("_modellease_settings", _spath)
            settings = importlib.util.module_from_spec(_spec)
            _spec.loader.exec_module(settings)
        else:
            import settings  # noqa: F401 -- only when the path is absent
        if not callable(getattr(settings, "request", None)):
            settings = None
    except Exception as e:                                      # noqa: BLE001 -- said, not swallowed
        print(f"  NOTE: the chat body contract is unreadable ({type(e).__name__}: {e}); "
              f"lanes send the minimal body", file=sys.stderr)
        settings = None

    def _body(model):
        """settings.request('chat', model, messages), copied so the stream key is set on OURS."""
        body = {}
        if settings is not None:
            _msg = [{"role": "user", "content": "ping"}]
            try:
                body = dict(settings.request("chat", model, _msg))
            except TypeError as e:
                # An older signature must not cost the lane its answer: say so, then send the
                # least body a chat endpoint accepts.
                print(f"  NOTE: settings.request(chat, {model}, messages) refused the "
                      f"positional call ({type(e).__name__}: {e}); using the minimal body",
                      file=sys.stderr)
                body = {}
        if not body:
            body = {"model": model, "messages": [{"role": "user", "content": "ping"}]}
        body["stream"] = False   # ours, on our copy: a streamed reply is not one JSON document
        return body

    def _lane(i, model):
        """One lane: send, read, decode. Times ITSELF and records into its own row, so one
        refused lane cannot cost the other n-1 their results. Never raises."""
        row = {"model": model, "ok": False, "error": None, "secs": 0.0, "done_reason": None}
        sent = None
        try:
            req = urllib.request.Request(
                chat_url, data=json.dumps(_body(model)).encode("utf-8"),
                headers={"Content-Type": "application/json"})
            sent = time.monotonic()
            with urllib.request.urlopen(req, timeout=timeout_s) as resp:
                payload = resp.read()
            row["secs"] = time.monotonic() - sent
            reply = json.loads(payload.decode("utf-8"))
            row["done_reason"] = reply.get("done_reason")
            if row["done_reason"] != "stop":
                # t238: a reply cut off at the token ceiling reads as garbage downstream because
                # no caller read done_reason. Record it rather than call a truncated answer ok.
                row["error"] = "reply did not finish: done_reason=" + repr(row["done_reason"])
            else:
                row["ok"] = True
        except Exception as e:                                    # noqa: BLE001 -- recorded
            if sent is not None:
                row["secs"] = time.monotonic() - sent
            row["error"] = type(e).__name__ + ": " + str(e)
        rows[i] = row

    # START EVERY WORKER, THEN JOIN EVERY WORKER. That ordering is the whole property: a client
    # that fires the requests one after the other measures about twice the per-request delay on
    # the last lane and reports the queueing as health. A lane the interpreter refuses to launch
    # is recorded in its own row, so one unstartable lane costs the others nothing and leaves no
    # row as None for the budget test to trip over -- the contract is that this returns a dict.
    wall = time.monotonic()
    started = []
    for i, m in enumerate(models):
        try:
            w = threading.Thread(target=_lane, args=(i, m), daemon=True)
            w.start()
        except Exception as e:                                    # noqa: BLE001 -- recorded
            rows[i] = {"model": m, "ok": False, "secs": 0.0, "done_reason": None,
                       "error": type(e).__name__ + ": " + str(e)}
            continue
        started.append(w)
    for w in started:
        w.join()
    wall = time.monotonic() - wall

    # RECONCILE EVERY LANE. A row that is still None means a worker never reached its recording
    # step; report it as a failed lane rather than let `r["ok"]` raise on None and lose the run.
    rows = [r if r is not None else
            {"model": m, "ok": False, "secs": 0.0, "done_reason": None,
             "error": "RuntimeError: the lane never reported"}
            for m, r in zip(models, rows)]
    return {"n": len(models),
            "results": rows,
            "per_call_budget_s": per_call_budget_s,
            "wall_secs": wall,
            # EACH LANE'S OWN SECONDS, strictly inside the budget, and only for lanes that came
            # back ok -- a lane that queued is not a lane that was served.
            "all_within_budget": all(r["ok"] and r["secs"] < per_call_budget_s for r in rows),
            "all_ok": all(r["ok"] for r in rows)}



def check(path, model, action, holder=None):
    """Check whether an action is permitted. Raises Held if refused. A free slot permits it."""
    leases = [held(sp) for sp in slot_paths(path)]
    if any(x is None for x in leases) or any(holder is not None and holder == x.get("holder") for x in leases):
        return None
    existing = leases[0]
    if existing is None:
        return None
    if holder is not None and holder == existing.get("holder"):
        return None
    raise Held(
        f"model '{existing['model']}' is held by '{existing.get('holder', 'unknown')}' "
        f"for '{existing['purpose']}' until {existing['expires']:.1f} (expires)"
    )


def release(path, lease):
    """Release a lease by removing ITS lease file (a slot's own, when it took one)."""
    p = Path((lease or {}).get("path") or path)
    if p.exists():
        p.unlink()
        _ledger("released", path, model=(lease or {}).get("model"), holder=(lease or {}).get("holder"),
                held_s=round(time.time() - (lease or {}).get("acquired", time.time()), 1))


def break_lease(path, broken_by, reason):
    """Break a lease: mark it broken rather than deleting it. The file survives so the
    interrupted run can discover what happened to it."""
    p = Path(path)
    with open(p) as f:
        data = json.load(f)
    data["status"] = "broken"
    data["broken_by"] = broken_by
    data["reason"] = reason
    data["broken_at"] = time.time()
    tmp = str(p) + f".tmp.{os.getpid()}"
    with open(tmp, "w") as f:
        json.dump(data, f)
    os.replace(tmp, str(p))
    return data


def was_broken(path, lease):
    """True when the lease at this path was broken, False when cleanly released or still held."""
    p = Path(path)
    if not p.exists():
        return False
    try:
        with open(p) as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError, ValueError):
        return False
    return data.get("status") == "broken"
