#!/usr/bin/env python3
"""ROLES.json: the auditor role's model must resolve to a declared, pinned,
profiled model -- the offline half of the 'every declared model is present, pinned
and has a sampling profile' invariant (scripts/models.py report(), the in_use loop
at L287-290). No live state: reads a private temp copy of the role store plus the
committed REGISTRY / SERVERS / lock and settings.resolve_sampling (lock, not Ollama)."""
import json
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
LI = REPO / "projects" / "local-inference"
sys.path.insert(0, str(LI))
import settings

MODELS = LI / "models"
ROLES_SRC = MODELS / "ROLES.json"
REGISTRY = MODELS / "REGISTRY.json"
SERVERS = MODELS / "SERVERS.json"
LOCK = MODELS / "tags.lock.json"


def _auditor_tag():
    # Private copy: a live Models-tab edit to the role store must not move this oracle.
    with tempfile.TemporaryDirectory() as td:
        copy = Path(td) / "roles.json"
        shutil.copyfile(ROLES_SRC, copy)
        roles = json.loads(copy.read_text())
    spec = (roles.get("roles") or {}).get("auditor") or {}
    return spec.get("model")


def test_auditor_tag_is_declared_on_a_server():
    tag = _auditor_tag()
    assert tag, "auditor role names no model in ROLES.json"
    reg = json.loads(REGISTRY.read_text())
    srv = json.loads(SERVERS.read_text())
    engines = {sid: s.get("engine") for sid, s in (srv.get("servers") or {}).items()}
    declared = {m["tag"]: m for m in reg.get("base_models", [])}
    assert tag in declared, f"auditor model {tag!r} is not a declared base_model in REGISTRY.json"
    sid = declared[tag].get("server")
    assert sid and engines.get(sid) == "ollama", f"auditor model {tag!r} not on an ollama server (server={sid!r}, engine={engines.get(sid)!r})"


def test_auditor_tag_is_pinned():
    tag = _auditor_tag()
    lock = json.loads(LOCK.read_text()).get("tags", {})
    assert tag in lock, f"auditor model {tag!r} has no pinned digest in tags.lock.json"


def test_auditor_tag_has_profile():
    tag = _auditor_tag()
    entry, how = settings.resolve_sampling(tag)
    assert entry is not None, f"auditor model {tag!r} resolves to no sampling profile (how={how!r})"


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
