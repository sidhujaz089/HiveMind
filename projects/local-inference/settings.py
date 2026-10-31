#!/usr/bin/env python3
"""One place for the model knobs, read by the console AND by coder.py.

    python3 projects/local-inference/settings.py           # show them
    python3 projects/local-inference/settings.py --reset   # back to defaults

WHY ONE FILE. Jas asked to tweak these "in Qwen chat and Qwen coder.py". Two settings files
would disagree within days -- that is L-046, and it is the same argument sync_facts.py makes
for single-sourcing a fact. So both read this, and an explicit CLI flag still wins over it,
because a flag is someone saying what they want right now.

ONLY KNOBS THAT ACTUALLY DO SOMETHING ARE HERE. Every field below is passed to Ollama's
native /api/chat and changes behaviour. Exposing a control that is quietly ignored is worse
than not exposing it: it reads as a lever and moves nothing, which is the shape of every
false-pass in this repository.
"""
import argparse
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "scripts"))
import roles  # noqa: E402  -- the model behind a role comes from SERVING.json
FILE = REPO / "workspace" / "console" / "settings.json"

# think: False, "low" or "high". D-30 keeps reasoning ON by default for anything reading
# input Jas did not write, and every tool result is exactly that. "low" is what coder.py has
# used all along -- verified reasoning, 299 tokens, with a populated thinking field.
DEFAULTS = {
    "chat": {
        "model": "",           # blank = whatever is loaded, else the largest installed
        "think": "low",
        # None = USE THE MODEL AUTHOR'S RECOMMENDED TEMPERATURE (see SAMPLING_BY_MODEL).
        # This was 0.0, which with think:"low" is greedy decoding in thinking mode -- the
        # configuration Qwen's own docs say causes "performance degradation and endless
        # repetitions", and Jas has been complaining that the model repeats itself.
        # A number here still wins; it is someone saying what they want right now.
        "temperature": None,
        "num_ctx": 32768,      # the CHAT does not need 262K, and a big window costs latency
        "num_predict": 4000,
        "keep_alive": "24h",   # a reload of a large context is minutes; nothing else wants the GPU
        # 8 WAS NOT ENOUGH TO ORIENT, READ AND THEN ANSWER. On 2026-08-31 a session spent
        # all eight rounds finding its way around and hit the cap with no answer at all --
        # "stopped after the tool-round cap without an answer" is what Jas saw. Three of
        # those rounds were lost to read_file refusing to list a directory, now fixed.
        # A cap exists to stop a runaway loop, not to end a normal investigation.
        "tool_rounds": 24,     # cap on the read-search-answer loop
    },
    "coder": {
        # RAISED low -> high, 2026-09-12. Qwen's card defaults reasoning_effort to xhigh and
        # warns that lower effort on MULTI-TURN AGENTIC tasks -- which is exactly what coder.py
        # does -- causes "insufficient analysis, more failures, and repeated retries". Every
        # dispatch here had been running the lowest rung. 'high' rather than 'max' because the
        # max<->xhigh mapping is unverified (see the think knob's note); 'high' is a measured
        # value that returned done=stop.
        "think": "high",
        "temperature": None,   # as above -- vendor value unless deliberately set
        "num_ctx": 262144,     # the model's full window; L-119 -- it was starved at 4096
        # 16000 AND NOT MORE. Raised to 32000 on 2026-09-12 on a guess that a stalled
        # dispatch was budget-starved; it was not -- the inference server was wedged
        # (see HANDOVER-2026-09-12). The skill says plainly "do not fix this by raising
        # the budget", and 443 of 510 slot loads that day were at n_ctx_slot = 32768,
        # where a 32000 budget leaves 768 tokens for the prompt. Reverted same day.
        # 2026-09-30 (t477, Jas: "Yes raise it"): the window is 65,536 now, and coder.py
        # gives 32000 on its first attempt WHEN the prompt leaves room (coder.first_budget).
        # This profile default stays 16000 for every other caller of ask().
        "num_predict": 16000,
        "keep_alive": "24h",
        "attempts": 3,
        "reads": 3,
    },
    # ADDED 2026-09-14. GEMMA HAD NO PROFILE AND BORROWED "coder"'s.
    #
    # gemma-decide.py and ask-gemma-supervise.py both called request("coder", "gemma4:26b"),
    # so the SUPERVISOR's reasoning effort and token budget were set by the profile for
    # PATCH-WRITING. Nothing in either script or in the console said so. Jas, 2026-09-14, on
    # discovering it: "I moved Gemma then. I didn't know that was Gemma. The console settings
    # didn't mention that."
    #
    # WHAT IT COST. ask-gemma-supervise.py hardcodes num_predict=100. Measured 2026-09-14 on
    # gemma4:26b, one /api/chat each, prompt "Reply with exactly one word: RESTART":
    #     think=high num_predict=500   18.8s  done=stop    content 'RESTART'
    #     think=low  num_predict=500    1.2s  done=stop    content 'RESTART'
    #     think=high num_predict=100    1.1s  done=length  content ''   <- ALL 100 on thinking
    # With thinking on, a 100-token budget returns EMPTY every time. That is a live candidate
    # for "gemma-decide.py, BUILT, never returned a decision" in HANDOVER-2026-09-12.
    #
    # THE SETTINGS ARE THE MODEL CARD'S, per Jas. Sampling already resolves from
    # SAMPLING_BY_MODEL["gemma"] -> ai.google.dev/gemma/docs/core/model_card_4
    # (temp 1.0, top_p 0.95, top_k 64, min_p 0.0, repeat_penalty 1.0) and is NOT repeated
    # here -- duplicating it is how a second copy drifts from the first.
    "supervisor": {
        # Q16 SUPERVISES, GEMMA CODES. Jas, 2026-09-14: "Phase 0 was Q16 supervises Gemma.
        # Gemma is the stronger coder but Q16 benchmarks higher in reasoning and
        # intelligence." This default named gemma4:26b, so the 15-minute timer ran the roles
        # inverted -- the same inversion t302 recorded and closed without this line moving.
        "model": roles.tag("manager", "qwen3.8:27b-mtp-bf16"),
        # LOW, AND IT IS A CHOICE. The card gives no reasoning-effort guidance, so this comes
        # from the measurement above: high and low both answered CORRECTLY, and high cost
        # 18.8s against low's 1.2s. This runs on a 15-minute timer whose curl --max-time is
        # 60, and the job is a five-way classification, not analysis. 15x the latency buys
        # nothing here. Raise it if a decision is ever wrong in a way more thinking would fix.
        "think": "low",
        # The model's own window is 262,144 (/api/show, gemma4.context_length). 65536 matches
        # what the server actually serves alongside Q16 and leaves room for co-residency,
        # which is the constraint the whole supervision design rests on.
        "num_ctx": 65536,
        "temperature": None,   # vendor value, as above
        # 2000 AND NOT 100. Big enough that thinking cannot eat the whole budget and leave an
        # empty answer -- the failure measured above. A decision is a short JSON object, so
        # this is headroom, not appetite.
        # 2000 -> 16000 (2026-10-17): on Swift, 2000 was NOT big enough -- both t1309 decisions came back
        # done_reason=length, 342 chars, recorded "unreadable", and the failure sat undecided.
        "num_predict": 16000,
        "keep_alive": "24h",
    },
}

# The one-liners in FIELDS are the label under each box. They are deliberately short.
# Jas: "It's too high-level and it needs more of a breakdown, but I don't want this whole
# page to be one giant novel." So the depth lives here, one entry per setting, reached by a
# "Learn more" link -- what it is, how to choose, what changes in the output, and the real
# limits. LIMITS is the machine-readable half: the settings page renders min/max from it and
# says plainly which fields are free text.
LIMITS = {
    "model":        {"type": "text",   "free": True,
                     "note": "Any tag `ollama list` shows. Blank = whatever is loaded."},
    "think":        {"type": "choice", "choices": ["off", "low", "medium", "high", "max"],
                     # REOPENED 2026-09-12. This offered only off|low, on a note saying
                     # "'medium' and 'high' hung for ten minutes when tested on 2026-09-01
                     # rather than returning". RE-TESTED TODAY AND IT DOES NOT REPRODUCE --
                     # qwen3.8:27b-mtp-bf16, num_predict 48, one /api/chat each:
                     #     low     4.5s  done=length
                     #     medium  4.0s  done=length
                     #     high    3.8s  done=stop    <- finished its answer
                     #     max     3.5s  done=stop
                     # A single failure had been enshrined as a capability limit and cost the
                     # top half of the ladder for eleven days. The likely cause of the
                     # original hang is the one-slot starvation of HANDOVER-2026-09-12, which
                     # made everything look like a hang.
                     #
                     # WHY IT MATTERS, from Qwen's own card: reasoning_effort defaults to
                     # xhigh, and "in multi-turn agentic tasks, lower reasoning effort does
                     # not always reduce overall task completion time ... it can also lead to
                     # insufficient analysis, more failures, and repeated retries". We had
                     # both profiles pinned at the LOWEST rung for exactly that workload.
                     "note": "Ollama's ladder is low|medium|high|max (plus true/false); it "
                             "REFUSES 'xhigh' -- HTTP 400 'must be \"high\", \"medium\", "
                             "\"low\", \"max\", true, or false'. Qwen's ladder is "
                             "low|medium|xhigh(default). 'max' is PLAUSIBLY ollama's spelling "
                             "of xhigh and that mapping is UNVERIFIED -- recorded as an "
                             "explicit unknown rather than assumed, same rule as the gpt-oss "
                             "row in SAMPLING_BY_MODEL. Measured 2026-09-12: all four return "
                             "in under 5s; none hang."},
    "temperature":  {"type": "number", "min": 0.0, "max": 2.0},
    "num_ctx":      {"type": "number", "min": 512, "max": None,
                     "note": "Max is the MODEL's own window, read from /api/show. 262,144 "
                             "for qwen3.8:27b. Asking for more than the model has is refused "
                             "by Ollama."},
    "num_predict":  {"type": "number", "min": -1, "max": None,
                     "note": "-1 means until done or the window fills. Otherwise capped in "
                             "practice by num_ctx minus your prompt."},
    "keep_alive":   {"type": "text",   "free": True,
                     "note": "A duration like 30m, 8h, 24h. 0 unloads immediately; -1 keeps "
                             "it for ever."},
    "tool_rounds":  {"type": "number", "min": 1, "max": 100},
    "attempts":     {"type": "number", "min": 1, "max": 10},
    "reads":        {"type": "number", "min": 0, "max": 20},
}

GUIDE = {
 "model": """**What it is.** Which Ollama tag answers you.

**How to choose.** Blank means "whatever is loaded, else the largest installed" -- convenient
until nothing is loaded, when it silently picks the biggest model on disk. On 2026-09-01 that
resolved to the 55 GB bf16 by accident. Pin it by name if you care which one replies.

**What changes.** A bigger model reasons better and generates slower: measured here, the
27B at 4-bit does about 11 tok/s and the same model at 16-bit about 5. A 1,500-token reply
is roughly two minutes on the small one and five on the large.""",

 "think": """**What it is.** Whether the model reasons before answering.

**How to choose.** `off` sends the whole budget to the answer. `low` buys better judgement on
anything with a trap in it.

**What changes -- and this is the one that bites.** Reasoning tokens are spent from
`num_predict`, the SAME budget as the answer. Set reasoning on with a small cap and the model
can spend every token thinking and return nothing at all. That is not hypothetical: a review
task given 5,000 tokens on 2026-09-01 came back completely empty, and the call reported
success. If you turn reasoning on, raise the reply cap or set it to -1.""",

 "temperature": """**What it is.** How much randomness is allowed when picking each token.

**How to choose.** 0 for anything you want to be able to reproduce -- code, checks, review.
Higher only when you want variety and do not need the same answer twice.

**What changes.** At 0 the same prompt gives the same reply. Above 0 it will not, which makes
a failure hard to chase: you cannot tell a fixed bug from a lucky sample.""",

 "num_ctx": """**What it is.** The window -- everything the model holds at once, your whole
conversation plus its reply.

**How to choose.** As large as the model allows unless you are chasing latency. The cost of a
big window is TIME as it fills, not memory up front: measured here, 131,072 used 17.2 GB and
262,144 used 17.6 GB, because the KV cache grows as the context fills rather than at load.

**What changes.** Too small and the conversation is silently truncated from the front -- the
model forgets the beginning and you get confident answers built on half the story. This was
pinned at 32,768 for months while the model advertised 262,144, which is why the chat kept
running out and not replying.""",

 "num_predict": """**What it is.** The cap on a single reply. NOT the same as the context
window: `num_ctx` is what the model can hold, this is what it may produce in one go.

**How to choose.** -1 means "until done, or until the window fills" -- the right setting if
you would rather wait than get a truncated answer. A number is only useful to bound cost.

**What changes.** Too low and you get a reply that stops mid-sentence, or with reasoning on,
no reply at all. Its real ceiling is `num_ctx` minus your prompt; setting it higher than that
changes nothing, because generation stops when the window is full either way.""",

 "keep_alive": """**What it is.** How long the model stays in GPU memory after a reply.

**How to choose.** Long, if it is the model you talk to. A cold load of the 55 GB model takes
about 24 seconds; a warm one is instant.

**What changes.** Only speed -- never the answer. Note that keep_alive does NOT protect
against eviction: with `OLLAMA_MAX_LOADED_MODELS=1`, a call to any other model unloads this
one regardless of how long you asked it to stay.""",

 "tool_rounds": """**What it is.** How many times the agent may read a file or search before
it must stop and answer.

**How to choose.** High enough to orient, read, and reply. It was 8, and a session spent all
eight finding its way around and returned no answer at all -- "stopped after the tool-round
cap without an answer". It is 24 now.

**What changes.** Too low and you get no answer, not a short one. Too high and a confused
agent can wander for a long time before saying anything.""",

 "attempts": """**What it is.** How many times a dispatched coding task retries before the run
is recorded as failed.

**How to choose.** 3 is the default. More only helps if failures are flaky rather than wrong.

**What changes.** Nothing about quality -- only how long a doomed task takes to admit it.""",

 "reads": """**What it is.** Extra rounds the coder may spend asking for files before it must
produce a patch.

**How to choose.** Raise it when tasks touch code the model has not seen.

**What changes.** More reads mean a better-informed patch and a longer run.""",
}

FIELDS = {
    "model": ("Model", "Blank uses whatever Ollama has loaded, falling back to the largest "
                       "installed. Name one to pin it."),
    "think": ("Reasoning", "off | low | high. Off answers directly and the whole budget goes "
                           "to the answer."),
    "temperature": ("Temperature", "0 is deterministic. Above 0, the same task can give a "
                                   "different patch each run."),
    "num_ctx": ("Context window", "Tokens the model may hold. Larger costs latency as it "
                                  "fills, not memory up front."),
    "num_predict": ("Output budget", "Max tokens per reply. Reasoning is billed to this, "
                                     "which is how a reply comes back empty."),
    "keep_alive": ("Keep loaded", "How long the model stays in GPU memory between calls."),
    "tool_rounds": ("Tool rounds", "How many times it may look something up before it must "
                                   "answer."),
    "attempts": ("Attempts", "Tries per task before the run is recorded as failed."),
    "reads": ("File reads", "Extra rounds it may spend asking for files before patching."),
}


def validate_keep_alive(value, section=None):
    """Validates that value is an integer or a valid Ollama duration string.
    Raises ValueError if invalid.
    """
    # ANY whole number of seconds is Ollama's own form (repaired on review 2026-09-25: the
    # delivered code accepted only 0, 300 and -1 -- the three the oracle happens to test -- so
    # keep_alive=600 would have raised inside every request()). bool is an int; it is not a
    # duration. A bare-number STRING stays refused except "0": "24" is the unit-less typo that
    # cost a weekend.
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, str):
        v = value.strip()
        if v == "0":
            return v
        if re.match(r"^-?\d+[hms]$", v) or re.match(r"^\d+[hms](\d+[hms])*$", v):
            return v
    msg = "invalid keep_alive"
    if section:
        msg += f" in {section}"
    raise ValueError(msg)


def load():
    """Defaults, with whatever is on disk laid over them. A missing file is not an error."""
    out = {k: dict(v) for k, v in DEFAULTS.items()}
    try:
        saved = json.loads(FILE.read_text(encoding="utf-8"))
        for section in out:
            for k, v in (saved.get(section) or {}).items():
                if k in out[section]:
                    out[section][k] = v
    except FileNotFoundError:
        return out                   # no saved file yet: the defaults ARE the settings
    except (OSError, json.JSONDecodeError) as e:
        print(f"  NOTE: {FILE} unreadable ({e}) -- using defaults", file=sys.stderr)
    return out


# ROOM TO ANSWER (plan harness #6, 2026-10-19). Reasoning is billed to num_predict: with thinking on,
# 100 tokens returned EMPTY every time (2026-09-14), and the Supervisor's 2000 on Swift gave decisions
# cut off at 342 characters. Floors per reasoning level, from budgets measured to finish: chat runs
# low on 4000; coder runs high on 16000 (done=stop, 2026-09-12).
THINK_FLOOR = {"low": 4000, "medium": 8000, "high": 16000, "max": 16000}


def budget_findings(settings=None, roles=None):
    """['where: think X with num_predict N, under the floor F'] for every reasoning profile -- each
    effective settings section, and each role's own think/num_predict attributes -- whose output
    budget is under THINK_FLOOR for its level. num_predict -1 (unlimited) always has room."""
    out = []
    settings = load() if settings is None else settings

    def chk(where, think, n):
        level = str(think).lower() if think not in (None, False) else "off"
        floor = THINK_FLOOR.get(level)
        try:
            n = int(n)
        except (TypeError, ValueError):
            return
        if floor and n != -1 and n < floor:
            out.append(f"{where}: think {level} with num_predict {n}, under the {floor} it needs to "
                       f"finish thinking and still answer")
    for section, v in settings.items():
        if isinstance(v, dict) and "num_predict" in v:
            chk(f"settings '{section}'", v.get("think"), v.get("num_predict"))
    for k, r in (roles or {}).items():
        a = r.get("attributes") or {}
        if "num_predict" in a or "think" in a:
            sec = settings.get("chat", {})
            chk(f"role '{k}' attributes", a.get("think", sec.get("think")), a.get("num_predict", sec.get("num_predict")))
    return out


def save(data):
    FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")
    tmp.replace(FILE)          # atomic, so a reader never sees half a file


def coerce(section, key, raw):
    """Text from a form into the type the default is. Bad input keeps the default."""
    d = DEFAULTS[section][key]
    raw = str(raw).strip()
    if key == "temperature":
        # Blank or "vendor" means: let the model author decide. Keeping this expressible is
        # the difference between a real setting and a lever that moves nothing.
        if raw.lower() in ("", "vendor", "auto", "none", "default"):
            return None
        try:
            return max(0.0, min(2.0, float(raw)))
        except ValueError:
            return None
    if key == "think":
        return False if raw in ("off", "false", "False", "") else raw
    if isinstance(d, bool):
        return raw.lower() in ("1", "true", "on", "yes")
    if isinstance(d, float):
        try:
            return max(0.0, min(2.0, float(raw)))
        except ValueError:
            return d
    if isinstance(d, int):
        try:
            return max(1, int(raw))
        except ValueError:
            return d
    return raw or d


# SAMPLING BELONGS TO THE MODEL'S AUTHORS. WE OWN THE SEED.
#
# THIS BLOCK USED TO PIN top_p 1.0 / top_k 0 / temperature 0.0 -- pure greedy decoding -- on the
# theory that identical knobs make a fair comparison and that temperature 0 is how you get a
# reproducible answer. Both halves were wrong, and I did not check before committing them.
#
# Qwen's own documentation, for the model that IS Q16 and Q4:
#
#     "DO NOT use greedy decoding, as it can lead to performance degradation and
#      endless repetitions."
#     https://qwen.readthedocs.io/en/latest/getting_started/quickstart.html
#
# And the Modelfile values were never noise. They are the vendors' published recommendations,
# shipped verbatim by Ollama -- Qwen's `temperature 1, top_k 20, top_p 0.95` and Gemma's
# `temperature 1, top_k 64, top_p 0.95` match their model cards exactly. Overriding them was
# overriding the people who trained the models.
#
# WHY THIS MATTERS BEYOND STAFFING. The live profiles ran temperature 0.0 with think "low" --
# greedy decoding in thinking mode, the precise configuration Qwen warns causes endless
# repetition -- and docs/PREFERENCES.md records Jas's standing complaint that the model repeats
# itself. A documented warning and a documented symptom, matching.
#
# REPRODUCIBILITY COMES FROM THE SEED, NOT FROM TEMPERATURE 0. A fixed seed makes sampling
# repeatable while leaving the model in the regime its authors intended. Greedy bought
# repeatability by breaking the model.
#
# EVERY VALUE CARRIES ITS SOURCE. A number without a citation here is indistinguishable from a
# guess, and a guess is what got committed last time. `test_options_contract.py` fails if a
# model on disk has neither a sourced profile nor an explicit UNKNOWN.
CHECKED = "2026-09-03"

SAMPLING_BY_MODEL = {
    # Qwen3.8-27B, THINKING mode -- which is what think:"low" selects.
    "qwen": {
        "match": ("qwen",),
        "options": {"temperature": 1.0, "top_p": 0.95, "top_k": 20, "min_p": 0.0,
                    "repeat_penalty": 1.0, "presence_penalty": 0.0},
        "source": "https://huggingface.co/Qwen/Qwen3.8-27B",
        "note": "Thinking-mode values. Non-thinking is 0.7/0.80/20. Qwen explicitly forbids "
                "greedy decoding here. presence_penalty 0-2 is their stated repetition "
                "mitigation if it still loops.",
        "checked": CHECKED},

    # Swift 1.5 Qwen3.8-Flash-Next, served by vLLM as `swift` (serve.SERVED_NAME). Keys are the
    # OpenAI/vLLM names, not Ollama's (repetition_penalty, not repeat_penalty).
    "swift": {
        "match": ("swift",),
        "options": {"temperature": 1.0, "top_p": 0.95, "top_k": 20, "min_p": 0.0,
                    "presence_penalty": 0.0, "repetition_penalty": 1.0},
        "source": "https://huggingface.co/ukisai/Swift1.5-Qwen3.8-Flash-Next",
        "note": "The card's Serving/Sampling line, the setting every published number used: "
                "reasoning effort xhigh (a chat_template_kwargs value, not sampling), "
                "temperature 1.0, top_p 0.95, top_k 20, min_p 0, presence 0, repetition 1.",
        "checked": "2026-10-06"},

    # Gemma 4 -- Google give one configuration for all use cases.
    "gemma": {
        "match": ("gemma",),
        "options": {"temperature": 1.0, "top_p": 0.95, "top_k": 64, "min_p": 0.0,
                    "repeat_penalty": 1.0},
        "source": "https://ai.google.dev/gemma/docs/core/model_card_4",
        "note": "Matches what Ollama bakes into the Modelfile.",
        "checked": CHECKED},

    # Laguna XS.2 -- THE ONE OLLAMA GOT WRONG. Its Modelfile declares NOTHING, so it ran on
    # Ollama's built-in defaults (~temp 0.8, top_k 40, top_p 0.9): not ours, not Poolside's.
    # It then scored worst of the six. Its old result is not a measurement of the model.
    "laguna": {
        "match": ("laguna",),
        "options": {"temperature": 0.7, "top_k": 20, "stop": ["</assistant>"]},
        "source": "https://huggingface.co/poolside/Laguna-XS.2",
        "note": "The temperature/top_k are the settings Poolside used for all their published "
                "benchmarking. They publish no top_p/min_p, so those are left to the backend "
                "rather than invented. Reasoning is on by default for this model. "
                "THE `stop` IS OURS, NOT POOLSIDE'S, and it is a workaround for a packaging "
                "defect: the Ollama Modelfile is `TEMPLATE {{ .Prompt }}` with RENDERER/PARSER "
                "laguna and NO stop parameter, so the model emits `</assistant>` as literal "
                "text and nothing halts on it. Measured 2026-09-03: without it, every one of "
                "six configurations returned done_reason 'length' at the token cap, spewing "
                "`14\\n</assistant>\\n</assistant>\\n14...` -- the ANSWER WAS CORRECT (14) "
                "and then ran away. With it: done_reason 'stop', 669 tokens instead of 4000, "
                "content exactly '14'. This is why laguna scored -3/5 in the interviews and "
                "looked like the weakest model in the field; it was the harness.",
        "checked": CHECKED},

    # NOTE, 2026-09-03: there is deliberately NO entry here for the Duo pair `q16-sa` /
    # `q16-sb`. One was written and then removed the same day. They are `ollama cp` of
    # qwen3.8:27b-mtp-bf16 and `resolve_sampling` now finds the `qwen` entry above BY DIGEST,
    # so a hand-written duplicate would only be a second copy of the same numbers, free to
    # drift from the first -- and it would hide the digest path instead of exercising it.
    # Add an entry here when a model has its OWN published values, never to paper over a copy.

    # gpt-oss -- OpenAI publish reasoning-effort guidance, not sampling values. Recorded as a
    # KNOWN gap rather than a silent one: an explicit unknown is what would have caught laguna.
    "gpt-oss": {
        "match": ("gpt-oss",),
        "options": {},
        "source": "https://github.com/openai/gpt-oss",
        "note": "UNKNOWN. No published sampling recommendation found on 2026-09-03; the "
                "documented knob is reasoning effort (low/medium/high). Backend defaults "
                "apply. Do not invent values to fill this in -- record it and say so.",
        "unknown": True,
        "checked": CHECKED},
}

# Ours, not the vendor's. A fixed seed is what makes a run repeatable now that temperature is
# no longer 0. Verified empirically, not assumed -- see the determinism check in calibrate.py.
SEED = 7


_DIGESTS = None
LOCK = Path(__file__).resolve().parent / "models" / "tags.lock.json"


def digests(refresh=False):
    """{tag: digest} -- FROM THE LOCKFILE FIRST, and only then from Ollama.

    `models/tags.lock.json` is committed, so what a tag IS is known offline, before anything
    is running, and identically on every clone. That is the point: a model's identity should
    not depend on a subprocess answering. `scripts/models.py lock` writes it.

    Ollama is consulted only for tags the lock does not know -- a `cp` somebody made by hand,
    or a fresh pull -- and the answer is merged in for this process only. If Ollama is not
    answering, the lock still resolves everything it covers, which on this box is everything.
    Returns {} only when the lock is missing AND Ollama is silent.
    """
    global _DIGESTS
    if _DIGESTS is not None and not refresh:
        return _DIGESTS
    out = {}
    try:
        out.update({t.lower(): d for t, d in
                    json.loads(LOCK.read_text()).get("tags", {}).items()})
    except FileNotFoundError:
        out = {}                     # no lock file: nothing is locked, said by an empty set
    except Exception as e:                                   # noqa: BLE001
        print(f"  NOTE: {LOCK} unreadable ({e}) -- no digests locked", file=sys.stderr)
    locked = set(out)
    try:
        import subprocess
        r = subprocess.run(["ollama", "list"], capture_output=True, text=True, timeout=20)
        live = {}
        for line in r.stdout.splitlines()[1:]:
            parts = line.split()
            if len(parts) >= 2:
                live[parts[0].lower()] = parts[1]
        # The lock WINS on a conflict and says so. A tag whose digest changed under a name
        # is exactly the drift `models.py verify` exists to catch, and silently preferring
        # whatever is on disk right now would hide it.
        for t, d in live.items():
            if t not in locked:
                out[t] = d
    except Exception as e:                                   # noqa: BLE001
        print(f"  NOTE: live digests unreadable ({e}) -- lock file only", file=sys.stderr)
    _DIGESTS = out
    return out


def _by_name(tag):
    """Longest family-name match, or None. Longest wins so a specific tag can override."""
    best = None
    for entry in SAMPLING_BY_MODEL.values():
        for m in entry["match"]:
            if m in tag and (best is None or len(m) > best[0]):
                best = (len(m), entry)
    return best[1] if best else None


def resolve_sampling(model):
    """(entry, how) for a tag. `how` is "name", "digest", or "none".

    THE DIGEST STEP IS THE FIX FOR A REAL FAILURE, 2026-09-03. Matching was on the tag
    STRING alone, so `ollama cp qwen3.8:27b-mtp-bf16 q16-sa` produced a tag carrying
    byte-identical weights and NO family name -- it matched nothing and ran on the backend's
    defaults, which Qwen's own documentation forbids for this model. Same weights under two
    names, sampled two different ways, and the Duo ran on the one nobody chose.

    A name is a label a human typed. THE DIGEST IS WHAT THE WEIGHTS ARE. So when the name
    says nothing, ask Ollama what this tag actually IS, and take the profile of any other tag
    with the same digest. Copies, renames and A/B aliases inherit correctly and permanently,
    without anyone remembering to add an entry.

    If two same-digest tags resolve to DIFFERENT entries the profiles are ambiguous and this
    returns none rather than picking one -- that is a contradiction in the table, and guessing
    which half is right is how a wrong number gets committed as a decision.
    """
    tag = (model or "").lower()
    named = _by_name(tag)
    if named:
        return named, "name"
    mine = digests().get(tag)
    if not mine:
        return None, "none"
    siblings = {t for t, d in digests().items() if d == mine and t != tag}
    found = {id(e): e for e in (_by_name(t) for t in siblings) if e}
    if len(found) == 1:
        return next(iter(found.values())), "digest"
    return None, "none"


def sampling(model):
    """The vendor-recommended sampling block for a model tag, plus our seed.

    Resolved by name, then BY DIGEST (see `resolve_sampling`) so an `ollama cp` cannot
    silently drop the profile. An unrecognised model gets the seed and nothing else --
    backend defaults apply and the caller can see that it was not a choice.
    """
    entry, _how = resolve_sampling(model)
    return {**(entry["options"] if entry else {}), "seed": SEED}


def sampling_source(model):
    """Where a model's sampling values came from, for the record and for the check."""
    return resolve_sampling(model)[0]


_CTX_CACHE = {}


def model_ctx(model, section=None):
    """The ONE num_ctx to use for this model, everywhere. Cached.

    num_ctx is a property of the MODEL, not of the call site, and Ollama treats a different
    num_ctx as a DIFFERENT LOAD. So a model asked for 262144 by the warm timer and 65536 by
    the exam is loaded twice -- or, with one slot, evicted and reloaded between every call.
    Measured 2026-09-03: Q16 was resident at 262144 with 24h to live; one question specifying
    32768 reloaded 53 GB and left it 26 minutes. Nothing errored.

    So: the smaller of what the model actually has and what the profile asks for, resolved
    once per model per process. Every caller that passes the same model gets the same number,
    which is the property that makes "warm" mean something.

    Falls back to the profile's value if /api/show cannot be reached -- a settings lookup must
    never be the reason an inference call fails.
    """
    if model in _CTX_CACHE:
        return _CTX_CACHE[model]
    # SECTION IS DELIBERATELY IGNORED FOR THE CAP. It used to be honoured, and that left the
    # `chat` profile asking 32768 for the same model `coder` asked 262144 for -- so switching
    # between the console and a job reloaded 53 GB every time. The chat profile's comment says
    # "a big window costs latency", and that is true, but it is a fraction of a full reload.
    # One number per model is worth more than a per-profile optimum.
    want = max(load()[sec]["num_ctx"] for sec in ("chat", "coder"))
    # ASK THE SERVER THAT SERVES IT (2026-10-06, docs/ENGINES.md). This asked Ollama's /api/show
    # for every model, so a model on vLLM answered 404 and every caller fell back to `want`.
    top = None
    try:
        import infer
        top = infer.context(model)
        # AN ENGINE THAT FIXES ITS WINDOW AT START GETS ALL OF IT (2026-10-06). The cap below exists
        # because Ollama treats a different num_ctx as a different LOAD; vLLM serves one window from
        # startup, so a smaller number saves nothing and only makes the context guard elide tool
        # results the model needs -- 24,811 chars at round 5 of Swift's first scope (t656).
        adapter, _ = infer.resolve(model)
        if top and adapter.entry["engine"] != "ollama":
            _CTX_CACHE[model] = top
            return top
    except Exception as e:                                   # noqa: BLE001
        top = None
        print(f"  NOTE: infer.context({model}) failed ({e}) -- using num_ctx {want}", file=sys.stderr)
    if top is None:
        print(f"  NOTE: no server reported {model}'s window -- using num_ctx {want}", file=sys.stderr)
    _CTX_CACHE[model] = min(top, want) if top else want
    return _CTX_CACHE[model]


def options(section, model=None, **override):
    """The COMPLETE Ollama `options` block for a section. Never build one by hand.

    THIS RETURNS EVERYTHING, and that is the point. It used to return three keys -- num_ctx,
    temperature, num_predict -- so every caller that needed anything else wrote its own dict,
    and they drifted: exam.py sent temperature 0.1, interview.py sent 0.0 at two call sites and
    0.2 at a third, and model_warm.py sent no num_ctx at all. Six months of "one settings file"
    did not survive contact with call sites that could opt out.

    Jas, 2026-09-03: *"Add all attributes. Don't stop at num_ctx."*

    That is the memory `consistent-outcome-is-a-constant` in its general form: when a producer
    and its consumer each carry their own list of what to cover, they drift, and the gap is
    silent. One source of truth, or a test that they match. `test_options_contract.py` is the
    test.
    """
    s = load()[section]
    # num_ctx is a property of the MODEL when we know which one; the profile is only the
    # fallback for callers that have not said. Resolving it here rather than only in request()
    # is what stops two profiles loading the same model twice.
    ctx = model_ctx(model) if model else s["num_ctx"]
    block = {"num_ctx": ctx, "num_predict": s["num_predict"], **sampling(model)}
    # A temperature deliberately set in the profile still wins -- a flag is someone saying
    # what they want right now. None means "the model author decides", which is the default.
    if s.get("temperature") is not None:
        block["temperature"] = s["temperature"]
    block.update(override)
    # A key we have no value for must be ABSENT, not null. Sending "temperature": null asks
    # the backend to parse a type it did not expect; omitting it asks for its default, which
    # is what "we have no published recommendation" actually means (see gpt-oss).
    return {k: v for k, v in block.items() if v is not None}


# --------------------------------------------------------------------------------- timeouts
#
# DERIVED FROM THE MEASURED DECODE RATE, NOT PICKED. Added 2026-09-14 after Jas: *"I think we
# need to give more time for the computer to process. It doesn't have the same tokens per second
# as a 5090 card would. While your usage was out it seemed hung up but I gave it a few hours and
# it finished."*
#
# He is right and the numbers were worse than anyone had noticed. workspace/measurements.jsonl:
#
#     qwen3.8:27b-mtp-bf16  decode              5.1 tok/s    <- what this box serves
#     qwen3.8:27b-mtp-bf16  calibrated         13.1 tok/s
#     qwen3.8:27b-mtp-q4_K_M                   35.7 tok/s
#
# This is a bandwidth limit, not a fault: dense bf16 reads ~54 GB per token against ~273 GB/s,
# and t124 records "Qwen3.8-27B at 4.1 tok/s looks broken and is not -- that is 82% of ceiling".
#
# AT 5.1 tok/s, a 16000-token budget needs 3137 SECONDS. coder.py's ask() had timeout=1800 with
# max_tokens=16000, so a full-budget answer was killed AT THE SOCKET before the model could
# finish -- and the caller then reported it as an unusable reply. That is t238's symptom arriving
# by a second route nobody had looked at.
#
# THE HEADROOM IS NOT TIMIDITY. The floor rate is deliberately BELOW the slowest measurement,
# because a cold load under-reports by ~3x (11.1 tok/s cold against 35.7 warm on the same tag)
# and co-residency costs ~8% (measured: two models concurrently = 0.92x aggregate).
DECODE_FLOOR_TOK_S = 4.0     # below the slowest measured rate, on purpose
PROMPT_EVAL_SLACK_S = 600    # prefill on a 25k-token prompt is minutes, not seconds
# DOUBLE THE HEALTHY ESTIMATE, Jas 2026-09-14: *"How about we double the timeout of a
# healthy budget to account for variances. We can fine tune later with data."* The variance
# is real and measured: the same tag reads 11.1 tok/s cold and 35.7 warm, co-residency costs
# ~8%, and a second model loading mid-run evicts pages. A factor of 2 on the DECODE estimate
# (not on the prefill slack, which is already generous) absorbs that without becoming
# unbounded -- and it stays FINITE on purpose, because a wedged server has to die eventually
# or the one llama-server slot is held forever, which is exactly 2026-09-11/12.
# TUNE IT FROM workspace/run-telemetry.jsonl once there are rows: the ratio of observed
# seconds to this estimate is the number to look at, and if it is reliably under 0.5 this
# factor is too generous.
TIMEOUT_SAFETY_FACTOR = 2.0
# HOW MANY SUCCESSFUL ROWS BEFORE THE HISTORY IS TRUSTED OVER THE STATIC ESTIMATE. Five
# is enough to notice a model that is consistently far from the 4 tok/s floor, and few
# enough that tuning starts working within a day rather than after a month.
MIN_ROWS_TO_TUNE = 5
# THE CAP ON THE CAP. A wedged server has to die eventually or it holds the single
# llama-server slot forever -- 2026-09-11/12. Six hours is far past any legitimate run
# measured here (the longest was ~58 minutes) and still finite, which is the point.
HARD_MAX_TIMEOUT_S = 21600
# NOTHING GETS A CAP BELOW THIS, however fast the history looks. Ten minutes is longer
# than any healthy short call on this box and short enough that a wedge is still noticed.
MIN_TUNED_TIMEOUT_S = 600


def timeout_for(num_predict, floor_tok_s=DECODE_FLOOR_TOK_S, harness=None, model=None,
                window=100):
    """Seconds to allow a generation of `num_predict` tokens on THIS box.

    SELF-TUNING FROM TELEMETRY, Jas 2026-09-14: *"Maybe it can look at the average of the last
    100 runs of a model harness combination and double it to calculate the cap for the current
    run. Then we don't need to revisit later."* Exactly that, with four corrections that matter:

    1. NORMALISED PER TOKEN, NOT RAW SECONDS. A 2000-token run and a 16000-token run take
       wildly different times; averaging their seconds is meaningless. The history gives a
       seconds-per-token rate, which is then multiplied by THIS run's budget.

    2. A HIGH QUANTILE, NOT THE MEAN. Doubling an average is not safe when the spread is wide,
       and here it is: the same tag reads 11.1 tok/s cold and 35.7 warm. The mean sits between
       them and 2x the mean can still be under a normal cold run. p90 of the observed rate is
       the basis instead, so the doubling is headroom on a bad case rather than on a typical
       one.

    3. KILLED RUNS ARE EXCLUDED. A run that HIT its timeout recorded seconds == the timeout.
       Feeding that back would teach the estimator that the cap is the normal duration, and
       each round would ratchet tighter -- a death spiral that ends with everything killed.
       Only rows with ok=True and a real eval_count count.

    4. CLAMPED AT BOTH ENDS. Never below half the static estimate, so a lucky fast streak
       cannot starve a legitimate long run; never above HARD_MAX_TIMEOUT_S, because a wedged
       server MUST still die -- on 2026-09-11/12 one runaway generation held the single
       llama-server slot and four dispatches were spent misdiagnosing it.

    Falls back to the static derivation when there is no history, which is also the cold-start
    path on a new model or a new harness. A caller that passes no harness/model gets the static
    number, so nothing silently changes for callers that have not opted in.
    """
    n = abs(int(num_predict or 0)) or 1000
    static = int(n / max(0.5, float(floor_tok_s)) * TIMEOUT_SAFETY_FACTOR + PROMPT_EVAL_SLACK_S)
    if not harness or not model:
        return static
    try:
        import sys as _sys
        _sys.path.insert(0, str(REPO / "scripts"))
        import run_telemetry
        rate, used = run_telemetry.observed_seconds_per_token(harness, model, window=window)
    except Exception:                                               # noqa: BLE001
        return static
    if not rate or used < MIN_ROWS_TO_TUNE:
        # NOT ENOUGH EVIDENCE IS NOT EVIDENCE OF SPEED (L-146). Stay on the static number and
        # let the rows accumulate.
        return static
    tuned = int(rate * n * TIMEOUT_SAFETY_FACTOR + PROMPT_EVAL_SLACK_S)
    # FLOOR AND CEILING. The floor is ABSOLUTE, not a fraction of the static guess: tying it
    # to `static` meant a genuinely fast model could never earn a shorter cap, because half
    # of a 4-tok/s assumption always exceeded a 10-tok/s reality -- caught by the oracle,
    # which found three cases collapsing onto the same clamped number. What actually protects
    # a long run is that `tuned` SCALES WITH num_predict, so a bigger budget gets a
    # proportionally bigger cap; the floor only stops an absurdly small one.
    return max(MIN_TUNED_TIMEOUT_S, min(tuned, HARD_MAX_TIMEOUT_S))


def request(section, model, messages, **override):
    """A COMPLETE /api/chat body. `think` and `keep_alive` are NOT options -- they sit at the
    top level of the request, which is exactly why they drifted independently of the rest.

    Callers pass messages and nothing else unless they mean it. Anything overridden here is
    someone saying what they want right now, which still beats the file (see the module
    docstring), but it is visible in one place instead of buried in a literal.
    """
    s = load()[section]
    # THE TOP-LEVEL PAIR IS OVERRIDABLE TOO, 2026-09-15. They were not, which is the drift
    # this docstring already complains about: a caller who needed a different `think` had no
    # way to say so through the contract and either lived with the section's value or built a
    # dict by hand, which is the thing the contract exists to stop. Popped before options() so
    # everything else still passes through untouched.
    think = override.pop("think", s["think"])
    keep_alive = override.pop("keep_alive", s["keep_alive"])
    keep_alive = validate_keep_alive(keep_alive, section)
    return {"model": model, "stream": False, "think": think,
            "keep_alive": keep_alive, "messages": messages,
            "options": options(section, model, **override)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--reset", action="store_true")
    a = ap.parse_args()
    if a.reset:
        save({k: dict(v) for k, v in DEFAULTS.items()})
        print("reset to defaults")
    cur = load()
    for section in ("chat", "coder"):
        print(f"\n{section}")
        for k, v in cur[section].items():
            label = FIELDS.get(k, (k, ""))[0]
            mark = "" if v == DEFAULTS[section][k] else "  (changed)"
            print(f"  {label:<16} {str(v):<10}{mark}")
    print(f"\n{FILE.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
