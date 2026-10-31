#!/usr/bin/env python3
"""Fail-first oracle, t416 step 1: scripts/roles.py gains endpoint(role, fallback).

Stations (t416) are duplicate Q4 `ollama serve`s on their OWN ports, so a short
stage (scoping / briefing / preflight) stops queueing behind a 15-minute manager
job. The role->port binding is per-ROLE config, so it lives in SERVING.json next
to the tag and resolves through the same lookup roles.tag() already uses -- the
two-copies-drift defect the Q16->Q4 manager swap and t410 both hit is why a port
must not be hard-coded at the call site.

ORACLE RULE (AGENTS.md): no live state. The test points roles.SERVING at a FAKE
file in a temp dir and never starts a server.
"""
import contextlib
import io
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import roles  # noqa: E402

FALLBACK = "http://127.0.0.1:11434"


def _fake_serving(tmp, roles_obj):
    p = Path(tmp) / "SERVING.json"
    p.write_text(json.dumps({"roles": roles_obj}, indent=1), encoding="utf-8")
    return p


def _point(fake):
    """Redirect roles.SERVING to a fake; returns the real path to restore."""
    real = roles.SERVING
    roles.SERVING = fake
    return real


def test_endpoint_returns_the_declared_port():
    with tempfile.TemporaryDirectory() as tmp:
        fake = _fake_serving(tmp, {
            "station-scoping": {"tag": "qwen3.8:27b-mtp-q4_K_M",
                                "endpoint": "http://127.0.0.1:11435"},
        })
        real = _point(fake)
        try:
            got = roles.endpoint("station-scoping", FALLBACK)
        finally:
            roles.SERVING = real
        assert got == "http://127.0.0.1:11435", got


def test_endpoint_falls_back_when_role_declares_no_endpoint():
    with tempfile.TemporaryDirectory() as tmp:
        fake = _fake_serving(tmp, {
            "manager": {"tag": "qwen3.8:27b-mtp-q4_K_M"},  # no endpoint key
        })
        real = _point(fake)
        try:
            got = roles.endpoint("manager", FALLBACK)
        finally:
            roles.SERVING = real
        assert got == FALLBACK, got


def test_endpoint_unknown_role_falls_back_loudly():
    with tempfile.TemporaryDirectory() as tmp:
        fake = _fake_serving(tmp, {
            "manager": {"tag": "q", "endpoint": "http://x:1"},
        })
        real = _point(fake)
        try:
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                got = roles.endpoint("no-such-role", FALLBACK)
        finally:
            roles.SERVING = real
        assert got == FALLBACK, got
        assert "no-such-role" in err.getvalue(), err.getvalue()
        assert FALLBACK in err.getvalue(), err.getvalue()


def test_tag_still_resolves_from_the_same_fake():
    with tempfile.TemporaryDirectory() as tmp:
        fake = _fake_serving(tmp, {
            "manager": {"tag": "qwen3.8:27b-mtp-q4_K_M", "endpoint": "http://x:1"},
        })
        real = _point(fake)
        try:
            got = roles.tag("manager", "fallback-tag")
        finally:
            roles.SERVING = real
        assert got == "qwen3.8:27b-mtp-q4_K_M", got


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
        except Exception as e:
            failed += 1
            print(f"FAIL {fn.__name__}: {type(e).__name__}: {e}")
        else:
            print(f"ok   {fn.__name__}")
    if failed:
        print(f"{failed} of {len(fns)} failed")
        sys.exit(1)
    print(f"all {len(fns)} passed")
    sys.exit(0)
