#!/usr/bin/env python3
"""Which model does a ROLE use? One lookup, read from the role store (models/ROLES.json), with
nothing else imported. (SERVING.json only if the store is missing.)

    import roles
    MODEL = roles.tag("manager", "qwen3.8:27b-mtp-bf16")

WHY. Swapping the manager from Q16 to Q4 (Jas, 2026-09-23) is meant to be a one-line edit to
projects/local-inference/models/SERVING.json. It was not: fourteen call sites named
qwen3.8:27b-mtp-bf16 directly -- ask_q16, corpus_scripts, truncation_review, preflight,
scope_queue, dispatch_loop's REVIEWER and the supervisor profile among them -- and each would
have kept loading 54 GB behind the swap. The hourly corpus job did exactly that during the
benchmark that measured the swap.

WHY A MODULE OF ITS OWN. models.py imports settings.py, so settings.py cannot import models.py.
This has no repository imports at all, so anything can use it -- settings included.

THE FALLBACK IS SAID, NOT SILENT. A scheduler that stops because a JSON file moved is worse
than one that runs a stale name, so an unreadable file or a missing role falls back to the
caller's literal -- and prints that it did (the same rule as dispatch_loop._serving).
"""
import json
import sys
from pathlib import Path

SERVING = (Path(__file__).resolve().parent.parent / "projects" / "local-inference"
           / "models" / "SERVING.json")




def _spec(role):
    """(spec, source) for `role`: the role store first (models/ROLES.json, the source of truth since
    2026-09-29), SERVING.json only if the store is missing. Raises OSError/ValueError if neither reads."""
    # The store is looked for NEXT TO whichever SERVING.json is in use, at call time: a test that
    # points roles.SERVING at a fake directory (test_roles_endpoint) then gets that directory's
    # store, or none -- never the live one.
    store = SERVING.parent / "ROLES.json"
    try:
        r = (json.loads(store.read_text(encoding="utf-8")).get("roles") or {}).get(role)
        if r is not None and r.get("kind", "model") == "model":
            return {"tag": r.get("model"), "endpoint": r.get("endpoint"), "lease": r.get("lease")}, "ROLES.json"
        if r is not None:
            return None, "ROLES.json"                   # a person/agent role has no model
        return None, "ROLES.json"
    except FileNotFoundError:
        spec = (json.loads(SERVING.read_text(encoding="utf-8")).get("roles") or {}).get(role)
        return spec, "SERVING.json"


def tag(role, fallback):
    """The model the role store gives `role`, else `fallback` -- with a NOTE on stderr."""
    try:
        spec, src = _spec(role)
    except (OSError, ValueError) as e:
        print(f"  NOTE: role store unreadable ({type(e).__name__}); role {role!r} falls back "
              f"to {fallback}", file=sys.stderr)
        return fallback
    got = spec.get("tag") if isinstance(spec, dict) else spec
    if not got:
        print(f"  NOTE: {src} declares no {role!r} role; falling back to {fallback}",
              file=sys.stderr)
        return fallback
    return got


def endpoint(role, fallback):
    """The endpoint the role store gives `role`, else `fallback` -- with a NOTE on stderr."""
    try:
        spec, src = _spec(role)
    except (OSError, ValueError) as e:
        print(f"  NOTE: role store unreadable ({type(e).__name__}); role {role!r} falls back "
              f"to {fallback}", file=sys.stderr)
        return fallback
    got = spec.get("endpoint") if isinstance(spec, dict) else None
    if not got:
        print(f"  NOTE: {src} declares no {role!r} endpoint; falling back to {fallback}",
              file=sys.stderr)
        return fallback
    return got



UNKNOWN = "UNKNOWN"


def for_job(job, fallback):
    """The model of the role whose Responsibilities include `job` (t9554: a process names a JOB, never a role).

    Same store, same order as task_routing.who_does: the first MODEL role, in the store's order, listing the
    responsibility. A job NO role lists is UNKNOWN, said on stderr -- never a guessed role (DS's review: a miss
    must not pass through). An unreadable store keeps this file's rule: fall back to the caller's literal,
    and say so, because a scheduler that stops when a file moves is worse than one running a stale name.
    """
    store = SERVING.parent / "ROLES.json"
    try:
        roles_ = json.loads(store.read_text(encoding="utf-8")).get("roles") or {}
    except (OSError, ValueError) as e:
        print(f"  NOTE: role store unreadable ({type(e).__name__}); job {job!r} falls back to {fallback!r}",
              file=sys.stderr)
        return fallback
    for key, r in roles_.items():
        if r.get("kind", "model") != "model":
            continue
        names = [t.get("task") if isinstance(t, dict) else t for t in (r.get("tasks") or [])]
        if job in names and r.get("model"):
            return r["model"]
    print(f"  NOTE: no role on the Models tab lists {job!r} -- its model is UNKNOWN", file=sys.stderr)
    return UNKNOWN
