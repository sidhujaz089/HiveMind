#!/usr/bin/env python3
"""SERVING.json: the auditor role's tag must resolve to a declared, pinned,
profiled model -- the offline half of the 'every declared model is present, pinned
and has a sampling profile' invariant (scripts/models.py report(), the in_use loop
at L287-290, which fails when a SERVING.json role tag is in neither the local box
nor a declared remote server). No live state: reads the committed SERVING /
REGISTRY / SERVERS / lock and settings.resolve_sampling (lock, not Ollama)."""
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
LI = REPO / "projects" / "local-inference"
sys.path.insert(0, str(LI))
import settings

MODELS = LI / "models"
SERVING = MODELS / "SERVING.json"
REGISTRY = MODELS / "REGISTRY.json"
SERVERS = MODELS / "SERVERS.json"
LOCK = MODELS / "tags.lock.json"


def _auditor_tag():
    d = json.loads(SERVING.read_text())
    spec = (d.get("roles") or {}).get("auditor") or {}
    return spec.get("tag")


def test_auditor_tag_is_declared_on_a_server():
    tag = _auditor_tag()
    assert tag, "auditor role names no tag in SERVING.json"
    reg = json.loads(REGISTRY.read_text())
    srv = json.loads(SERVERS.read_text())
    engines = {sid: s.get("engine") for sid, s in (srv.get("servers") or {}).items()}
    declared = {m["tag"]: m for m in reg.get("base_models", [])}
    assert tag in declared, f"auditor tag {tag!r} is not a declared base_model in REGISTRY.json"
    sid = declared[tag].get("server")
    assert sid and engines.get(sid) == "ollama", f"auditor tag {tag!r} not on an ollama server (server={sid!r}, engine={engines.get(sid)!r})"


def test_auditor_tag_is_pinned():
    tag = _auditor_tag()
    lock = json.loads(LOCK.read_text()).get("tags", {})
    assert tag in lock, f"auditor tag {tag!r} has no pinned digest in tags.lock.json"


def test_auditor_tag_has_profile():
    tag = _auditor_tag()
    entry, how = settings.resolve_sampling(tag)
    assert entry is not None, f"auditor tag {tag!r} resolves to no sampling profile (how={how!r})"


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"PASS {fn.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL {fn.__name__}: {e}")
    sys.exit(1 if failed else 0)
