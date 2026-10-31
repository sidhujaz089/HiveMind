#!/usr/bin/env python3
"""One call for every model, on every engine, on every machine: `infer.chat(body)`.

    python3 projects/local-inference/infer.py servers           every server: engine, device, health, models
    python3 projects/local-inference/infer.py where <model>      which server a call for <model> goes to, and why
    python3 projects/local-inference/infer.py ledger             what each device holds, per server

Design and reasons: docs/ENGINES.md. Jas, 2026-10-06: "plug-and-play with Ollama, vLLM, and a hot
new one called Strata. Basically, anything."

THE SHAPE IS OLLAMA'S /api/chat, AND THAT IS A FORMAT, NOT A DEPENDENCY. Every pipeline caller
builds its body with settings.request() and parses {message: {content, thinking, tool_calls},
done_reason, eval_count}. Keeping that shape means moving a caller onto any engine is two lines and
its parsing does not change. An adapter per engine family translates both ways.

WHERE A CALL GOES (resolve()):
  1. an explicit server id;
  2. the job's server, as the scheduler set it: HIVEMIND_SERVER=<id>, or HIVEMIND_OLLAMA=<url>
     (historical name) -- used only if that server offers the model;
  3. otherwise the servers that offer the model, in SERVERS.json order, preferring one that has it
     LOADED (no load cost), then the first that answers.

THE LEDGER (admit()). Before a call that would LOAD a model, the model's footprint plus everything
every OTHER server on the same device holds must fit that device. Ollama evicts its own models but
cannot see vLLM or a second Ollama: on 2026-10-06 at 14:05 it loaded Q4 (17.5 GB) beside a 95 GB
vLLM engine and left 4 GB. That call now raises Refused, with the sentence saying who holds what.
"""
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
REGISTRY = HERE / "models" / "SERVERS.json"
DISPATCH_LEDGER = HERE.parent.parent / "workspace" / "model-dispatch.jsonl"
_TTL = 15.0
_CACHE = {}


# BOTH ARE OSError ON PURPOSE. Every caller already treats OSError (connection refused, timeout)
# as "the model could not be reached" and has a path for it; "no server offers this model" and
# "the device cannot hold it" are the same kind of answer, so they take the same path instead of
# needing a new except clause at every call site.
class NoServer(OSError):
    """No registered, answering server offers this model."""


class Refused(OSError):
    """The device does not have the memory to load this model next to what is already there."""


class Unacknowledged(OSError):
    """The endpoint answered for a model the caller did not ask for."""


DISPATCH_FIELDS = ("ts", "caller", "requested_role", "requested_model", "resolved_endpoint",
                   "model_acknowledged", "http_status", "latency_ms", "batch_id", "task_id", "ok")


def _dispatch_ts():
    """A local timestamp a person can read off the ledger."""
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _caller():
    """file.py:lineno of the first frame outside this module: the code that asked for a model."""
    frame = sys._getframe(1)
    this = os.path.realpath(__file__)
    while frame is not None:
        filename = frame.f_code.co_filename
        if os.path.realpath(filename) != this:
            return f"{os.path.basename(filename)}:{frame.f_lineno}"
        frame = frame.f_back
    return f"{os.path.basename(this)}:0"


def record_dispatch(row, path=None):
    """Append one dispatch row, never rewriting what is already there."""
    missing = [field for field in DISPATCH_FIELDS if field not in row]
    if missing:
        raise ValueError("dispatch row missing: " + ", ".join(missing))
    path = Path(path if path is not None else DISPATCH_LEDGER)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row) + "\n")


def _cached(key, fn, ttl=_TTL):
    now = time.time()
    hit = _CACHE.get(key)
    if hit and now - hit[0] < ttl:
        return hit[1]
    val = fn()
    _CACHE[key] = (now, val)
    return val


def registry(path=None):
    return json.loads(Path(path or REGISTRY).read_text(encoding="utf-8"))


def _get(url, timeout=5):
    with urllib.request.urlopen(url, timeout=timeout) as f:
        return json.load(f)


def _post(url, body, timeout):
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as f:
        return json.load(f)


# --------------------------------------------------------------------------- adapters

class Ollama:
    """Ollama's native API. The internal shape IS this API, so chat() sends the body as is."""

    def __init__(self, sid, entry):
        self.id, self.entry, self.url = sid, entry, entry["url"].rstrip("/")

    def health(self):
        try:
            _get(f"{self.url}/api/version", timeout=3)
            return True
        except Exception:                                   # noqa: BLE001 -- down is an answer
            return False

    def models(self):
        """{tag: size_gb} for everything on this server's disk; {} when it does not answer."""
        def fetch():
            try:
                tags = _get(f"{self.url}/api/tags").get("models", [])
            except Exception:                               # noqa: BLE001
                return {}
            out = {}
            for m in tags:
                gb = round(m.get("size", 0) / 1e9, 1)
                out[m["name"]] = gb
                if m["name"].endswith(":latest"):
                    out[m["name"][:-len(":latest")]] = gb
            return out
        return _cached(("models", self.id), fetch)

    def loaded(self):
        """{tag: resident_gb} right now; {} when it does not answer."""
        try:
            return {m["name"]: round(m.get("size", 0) / 1e9, 1)
                    for m in _get(f"{self.url}/api/ps", timeout=3).get("models", [])}
        except Exception:                                   # noqa: BLE001
            return {}

    def footprint(self, model):
        """GB to load `model`. Declared beats measured beats the file size plus KV headroom."""
        declared = (self.entry.get("models") or {}).get(model, {}).get("footprint_gb")
        if declared:
            return float(declared)
        return round(self.models().get(model, 0.0) * 1.15, 1)

    def context(self, model):
        # A DECLARED WINDOW WINS (2026-10-12). /api/show reports the architecture's context_length
        # (262144 for qwen3.8), not the num_ctx a Modelfile variant was built for. The ThinkPad's
        # -56k fits its 24 GB only up to 57344; asked for more, the Windows runner crashes ("CUDA
        # error: shared object initialization failed") and every call returns HTTP 500.
        declared = (self.entry.get("models") or {}).get(model, {}).get("context")
        if declared:
            return declared
        try:
            info = _post(f"{self.url}/api/show", {"model": model}, timeout=30).get("model_info") or {}
        except Exception:                                   # noqa: BLE001
            return None
        return next((v for k, v in info.items()
                     if k.endswith("context_length") and isinstance(v, int)), None)

    def chat(self, body, timeout):
        return _post(f"{self.url}/api/chat", body, timeout)


class OpenAI:
    """Any OpenAI-compatible server: vLLM, SGLang, llama-server, LM Studio, most new engines.

    Translates the internal (Ollama /api/chat) shape both ways. The parts that bite:
      * reasoning: `think` becomes the MODEL's own control from SERVERS.json (Swift:
        chat_template_kwargs.reasoning_effort). Passing one engine's knob to another is
        UNPARSEABLE x7 (674f869b), so nothing is passed through untranslated.
      * tool calls: Ollama gives arguments as an object and no id is required; OpenAI wants a
        JSON string and an id that the tool result names. Ids from the server are kept so the
        caller's `tool` message (tool_call_id = c["id"]) matches.
      * done_reason: finish_reason "length" stays "length"; "stop" and "tool_calls" are "stop".
    """
    SAMPLING = {"temperature": "temperature", "top_p": "top_p", "top_k": "top_k",
                "min_p": "min_p", "presence_penalty": "presence_penalty",
                "frequency_penalty": "frequency_penalty", "repeat_penalty": "repetition_penalty",
                "repetition_penalty": "repetition_penalty", "seed": "seed", "stop": "stop"}

    def __init__(self, sid, entry):
        self.id, self.entry = sid, entry

    @property
    def url(self):
        src = self.entry.get("url_from")
        if src == "serve.endpoint":
            def fetch():
                sys.path.insert(0, str(HERE))
                import serve
                return serve.endpoint().rstrip("/")
            return _cached(("url", self.id), fetch, ttl=60)
        return self.entry["url"].rstrip("/")

    def health(self):
        try:
            _get(f"{self.url}/v1/models", timeout=3)
            return True
        except Exception:                                   # noqa: BLE001
            return False

    def models(self):
        declared = {m: float(v.get("footprint_gb", 0)) for m, v in (self.entry.get("models") or {}).items()}

        def fetch():
            try:
                return {m["id"]: declared.get(m["id"], 0.0)
                        for m in _get(f"{self.url}/v1/models", timeout=3).get("data", [])}
            except Exception:                               # noqa: BLE001
                return {}
        live = _cached(("models", self.id), fetch)
        return {**declared, **live}

    def loaded(self):
        """A vLLM-style server holds its model for as long as it runs: up means resident."""
        if not self.health():
            return {}
        return {m: self.footprint(m) for m in self.models()}

    def footprint(self, model):
        return float((self.entry.get("models") or {}).get(model, {}).get("footprint_gb", 0.0))

    def context(self, model):
        return (self.entry.get("models") or {}).get(model, {}).get("context")

    def _reasoning(self, model, think):
        level = ("off" if think in (False, None, "off", "") else
                 "high" if think is True else str(think))
        table = (self.entry.get("models") or {}).get(model, {}).get("reasoning")
        if table:
            return table.get(level) or table.get("high") or {}
        return {"enable_thinking": level != "off"}

    def request(self, body):
        """Internal shape -> OpenAI /v1/chat/completions body. Pure, so the suite can check it."""
        model = body["model"]
        msgs, pending = [], 0
        for m in body.get("messages") or []:
            out = {"role": m["role"], "content": m.get("content") or ""}
            if m["role"] == "assistant" and m.get("tool_calls"):
                calls = []
                for c in m["tool_calls"]:
                    fn = c.get("function") or {}
                    args = fn.get("arguments")
                    pending += 1
                    calls.append({"id": c.get("id") or f"call_{pending}", "type": "function",
                                  "function": {"name": fn.get("name", ""),
                                               "arguments": args if isinstance(args, str)
                                               else json.dumps(args or {})}})
                out["tool_calls"] = calls
            if m["role"] == "tool":
                out["tool_call_id"] = m.get("tool_call_id") or f"call_{pending}"
            msgs.append(out)
        opts = body.get("options") or {}
        req = {"model": model, "messages": msgs, "stream": False,
               "chat_template_kwargs": self._reasoning(model, body.get("think"))}
        for k, v in opts.items():
            if k in self.SAMPLING and v is not None:
                req[self.SAMPLING[k]] = v
        n = opts.get("num_predict")
        if isinstance(n, int) and n > 0:
            req["max_tokens"] = n
        if body.get("tools"):
            req["tools"] = body["tools"]
        fmt = body.get("format")
        if fmt == "json":
            req["response_format"] = {"type": "json_object"}
        elif isinstance(fmt, dict):
            req["response_format"] = {"type": "json_schema",
                                      "json_schema": {"name": "out", "schema": fmt}}
        return req

    @staticmethod
    def response(d, model, seconds):
        """OpenAI response -> internal shape. Pure, so the suite can check it."""
        ch = (d.get("choices") or [{}])[0]
        m = ch.get("message") or {}
        calls = []
        for c in m.get("tool_calls") or []:
            fn = c.get("function") or {}
            raw = fn.get("arguments")
            try:
                args = json.loads(raw) if isinstance(raw, str) else (raw or {})
            except ValueError:
                args = raw                                  # kept, not swallowed: the caller sees it
            calls.append({"id": c.get("id"), "function": {"name": fn.get("name", ""),
                                                           "arguments": args}})
        u = d.get("usage") or {}
        out = {"model": model,
               "message": {"role": "assistant", "content": m.get("content") or "",
                           "thinking": m.get("reasoning_content") or m.get("reasoning") or ""},
               "done": True,
               "done_reason": "length" if ch.get("finish_reason") == "length" else "stop",
               "eval_count": u.get("completion_tokens", 0),
               "prompt_eval_count": u.get("prompt_tokens", 0),
               "total_duration": int(seconds * 1e9), "eval_duration": int(seconds * 1e9),
               "load_duration": 0}
        if calls:
            out["message"]["tool_calls"] = calls
        return out

    def chat(self, body, timeout):
        t0 = time.time()
        d = _post(f"{self.url}/v1/chat/completions", self.request(body), timeout)
        return self.response(d, body["model"], time.time() - t0)


ADAPTERS = {"ollama": Ollama, "openai": OpenAI}


def servers(reg=None):
    reg = reg or registry()
    return {sid: ADAPTERS[e["engine"]](sid, e) for sid, e in reg["servers"].items()}


def _norm(url):
    return re.sub(r"/+$", "", (url or "").replace("localhost", "127.0.0.1"))


def resolve(model, server=None, env=None, reg=None):
    """(adapter, why) for a call to `model`. Raises NoServer, saying what was tried."""
    env = os.environ if env is None else env
    reg = reg or registry()
    srv = servers(reg)
    if server:
        if server not in srv:
            raise NoServer(f"no server {server!r} in SERVERS.json")
        return srv[server], f"named: {server}"
    # THE JOB'S SERVER WINS unless it demonstrably does not have the model: its list is readable
    # and the model is not on it (Swift sent to the Ollama station). An UNREADABLE list is not
    # evidence -- the scheduler chose that server, and a down server should fail there, loudly,
    # as a direct post always did, not be silently swapped for another one.
    def _jobs(s):
        offered = s.models()
        return (not offered) or model in offered
    sid = env.get("HIVEMIND_SERVER")
    if sid in srv and _jobs(srv[sid]):
        return srv[sid], f"the job's server (HIVEMIND_SERVER={sid})"
    url = env.get("HIVEMIND_OLLAMA")
    if url:
        for s in srv.values():
            if s.entry.get("url") and _norm(s.entry["url"]) == _norm(url) and _jobs(s):
                return s, f"the job's server (HIVEMIND_OLLAMA={url} = {s.id})"
    offering = [s for s in srv.values() if model in s.models()]
    if not offering:
        # AN UNLISTED MODEL GOES TO THE DEFAULT SERVER, which answers for itself -- exactly what
        # every caller's direct post to :11434 did before infer existed (a typo'd tag got Ollama's
        # own 404; a tag pulled a second ago gets served). NoServer means there is no default.
        default = next((x for x in srv.values() if x.entry.get("default")), None)
        if default:
            return default, f"no server lists {model}; the default server {default.id} answers for it"
        raise NoServer(f"no answering server offers {model!r} "
                       f"(checked {', '.join(srv)}; see SERVERS.json)")
    # A MODEL NAMED UNDER A SERVER'S `models` IS PINNED THERE. Jas, 2026-10-15: "The frontline needs
    # to be in the ThinkPad regardless of model choice." gemma4:26b is pulled on the GX10 too, so with
    # nothing loaded "first answering server" picked ollama-main and admit() refused it beside Swift.
    pinned = [s for s in offering if model in (s.entry.get("models") or {})]
    offering = pinned or offering
    for s in offering:
        if model in s.loaded():
            return s, f"{s.id} already has {model} loaded"
    for s in offering:
        if s.health():
            return s, f"{s.id} is the first answering server that offers {model}"
    raise NoServer(f"{model!r} is offered by {', '.join(s.id for s in offering)}, and none answers")


def ledger(reg=None):
    """{device: {server: {model: gb}}} -- what every engine on every device holds right now."""
    reg = reg or registry()
    out = {d: {} for d in reg["devices"]}
    for s in servers(reg).values():
        out.setdefault(s.entry["device"], {})[s.id] = s.loaded()
    return out


def admit(adapter, model, reg=None):
    """None, or raise Refused. Only a LOAD is checked: a model already resident costs nothing."""
    reg = reg or registry()
    if model in adapter.loaded():
        return None
    dev = adapter.entry["device"]
    d = reg["devices"][dev]
    budget = float(d["memory_gb"]) - float(d.get("reserve_gb", 0))
    others = {sid: held for sid, held in ledger(reg).get(dev, {}).items()
              if sid != adapter.id and held}
    held = sum(sum(v.values()) for v in others.values())
    need = adapter.footprint(model)
    if need + held > budget:
        who = "; ".join(f"{sid}: " + ", ".join(f"{m} {gb:.0f} GB" for m, gb in v.items())
                        for sid, v in others.items())
        raise Refused(f"loading {model} (~{need:.0f} GB) on {adapter.id} needs {need + held:.0f} GB "
                      f"of {dev}'s {budget:.0f} GB usable -- held by {who}. Stop one, or route "
                      f"this role to a server on another device.")
    return None


def chat(body, timeout=1800, server=None, env=None):
    """Send an internal-shape (Ollama /api/chat) body wherever its model is served. Returns the
    internal-shape response, plus `_server`. Raises NoServer, Refused, Unacknowledged, or the
    transport's error. Every outcome appends exactly one dispatch row before propagating."""
    t0 = time.time()
    caller = _caller()
    requested_role = body.get("role")
    requested_model = body.get("model")

    def _record(resolved_endpoint, model_acknowledged, http_status, ok):
        """Write the one row this dispatch is allowed to produce."""
        row = {
            "ts": _dispatch_ts(),
            "caller": caller,
            "requested_role": requested_role,
            "requested_model": requested_model,
            "resolved_endpoint": resolved_endpoint,
            "model_acknowledged": model_acknowledged,
            "http_status": http_status,
            "latency_ms": round((time.time() - t0) * 1000, 1),
            "batch_id": os.environ.get("HIVEMIND_BATCH"),
            "task_id": os.environ.get("HIVEMIND_TASK"),
            "ok": ok,
        }
        record_dispatch(row)

    adapter = None
    try:
        adapter, _ = resolve(requested_model, server=server, env=env)
        admit(adapter, requested_model)
        reply = adapter.chat(body, timeout)
    except NoServer:
        # Resolution failed before an endpoint was chosen: the name never reached a server.
        _record(None, None, None, False)
        raise
    except Refused:
        # The server was chosen, then admission refused the load; no model answered.
        _record(getattr(adapter, "url", None), None, None, False)
        raise
    except OSError as e:
        # Transport failed after (or without) resolution; record what the request was aimed at.
        status = e.code if isinstance(e, urllib.error.HTTPError) else None
        _record(getattr(adapter, "url", None), None, status, False)
        raise

    acknowledged = reply.get("model")
    if acknowledged is not None and acknowledged != requested_model:
        # The endpoint answered for a model that was not asked for: not a terminal success.
        _record(adapter.url, acknowledged, 200, False)
        raise Unacknowledged(f"the endpoint answered for {acknowledged!r}, not the requested {requested_model!r}")

    _record(adapter.url, acknowledged, 200, True)
    reply["_server"] = adapter.id
    return reply


def job_budget(model, default):
    """Output tokens for one long job (a scope, an RCA) on `model`: its server entry's
    `job_budget` if declared, else `default`. The default was sized for Q4's 5-20 tok/s; a
    faster engine finishes a bigger budget sooner than Q4 finished the small one, and Swift's
    first scope (t656, 2026-10-06) was cut off at 16,000 tokens mid-chunks-block."""
    try:
        adapter, _ = resolve(model)
    except OSError:
        return default
    return int((adapter.entry.get("models") or {}).get(model, {}).get("job_budget") or default)


def context(model, server=None):
    """The model's context window on whichever server serves it, or None."""
    try:
        adapter, _ = resolve(model, server=server)
    except NoServer:
        return None
    return adapter.context(model)


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("servers")
    w = sub.add_parser("where")
    w.add_argument("model")
    sub.add_parser("ledger")
    a = ap.parse_args(argv)
    if a.cmd == "servers":
        reg = registry()
        for s in servers(reg).values():
            up = s.health()
            print(f"{s.id:<18}{s.entry['engine']:<8}{s.entry['device']:<8}"
                  f"{'up' if up else 'DOWN':<6}{s.url}")
            if up:
                print("    offers: " + ", ".join(sorted(s.models())))
        return 0
    if a.cmd == "where":
        try:
            s, why = resolve(a.model)
        except NoServer as e:
            print(f"NO SERVER: {e}")
            return 1
        print(f"{a.model} -> {s.id} ({s.entry['engine']}, {s.url}): {why}")
        try:
            admit(s, a.model)
            print("admission: ok")
        except Refused as e:
            print(f"admission: REFUSED -- {e}")
        return 0
    for dev, per in ledger().items():
        print(dev)
        for sid, held in per.items():
            print(f"  {sid:<18}" + (", ".join(f"{m} {gb:.0f} GB" for m, gb in held.items()) or "-"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
