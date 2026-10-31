#!/usr/bin/env python3
"""Does the declared production working set fit the box? (t301)

    python3 scripts/check_working_set.py      # exit 0 fits, 1 does not, 2 a size is undeclared

WHY (t301, 2026-09-14). Three wanted models -- bf16 53 GB, Q4 17 GB, gemma ~18 GB -- "fit" on paper
(88 of 121 GB) and did not in practice: Ollama's LRU evicted one to load another, and lanes wedged.
Jas's standing order since (2026-09-17) is two models co-resident, declared as SERVING.json's
production_lanes. Nothing enforced it: the fit was prose arithmetic in handovers. This is the check.

THE WORKING SET is exactly the production_lanes roles' tags, deduplicated by tag (two roles on one
tag are one load). Each tag's size is its REGISTRY.json weight_gb -- the MEASURED resident footprint,
not the file size -- and a production tag with no declared size is a LOUD failure (exit 2), never a
default. It fits when the total leaves HEADROOM_GB of the box free for the KV cache, the chat model
and the OS.
"""
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
HEADROOM_GB = 40.0
# A FOOTPRINT THAT ALREADY HOLDS ITS KV CACHE needs only the OS reserve (2026-10-06). The 40 GB is
# for what Ollama sizes leave out: the KV cache they grow at load, and the chat model. A vLLM
# model pre-allocates a fixed KV pool, so its measured footprint (REGISTRY `includes_kv`) is all
# it will ever take; holding another 40 GB back for it refused a model that was serving with 25 GB
# free. A mixed set keeps the 40.
RESERVE_ONLY_GB = 8.0


def fits(total_gb, box_gb, headroom_gb=HEADROOM_GB):
    return total_gb <= box_gb - headroom_gb


def _registry_sizes(reg):
    """{tag: entry} from any of the registry's shapes: {tag: {...}}, {"models": {...}}, or the live
    file's {"base_models": [{"tag": ...}, ...]}."""
    if isinstance(reg.get("models"), dict):
        return reg["models"]
    if isinstance(reg.get("base_models"), list):
        return {m["tag"]: m for m in reg["base_models"] if isinstance(m, dict) and m.get("tag")}
    return {k: v for k, v in reg.items() if isinstance(v, dict)}


def _device_of(models, server):
    try:
        return (json.loads((models / "SERVERS.json").read_text(encoding="utf-8")).get("servers", {})
                .get(server) or {}).get("device")
    except (OSError, ValueError) as e:
        print(f"  NOTE: SERVERS.json unread ({type(e).__name__}); counting {server}'s model here", file=sys.stderr)
        return None


def working_set(root=None):
    """({tag: weight_gb}, [tags with no declared size])."""
    models = Path(root or REPO) / "projects" / "local-inference" / "models"
    serving = json.loads((models / "SERVING.json").read_text(encoding="utf-8"))
    reg = _registry_sizes(json.loads((models / "REGISTRY.json").read_text(encoding="utf-8")))
    tags, missing = {}, []
    for role in serving.get("production_lanes", []):
        tag = (serving.get("roles", {}).get(role) or {}).get("tag")
        if not tag or tag in tags or tag in missing:
            continue
        # THIS BOX ONLY (2026-10-13): a model REGISTRY pins to a server on another device (Frontline's
        # gemma4:e4b on the ThinkPad eGPU) holds that device's memory, not the GX10's.
        srv = (reg.get(tag) or {}).get("server")
        if srv and _device_of(models, srv) not in (None, "gx10"):
            continue
        size = (reg.get(tag) or {}).get("weight_gb")
        if isinstance(size, (int, float)):
            tags[tag] = float(size)
        else:
            missing.append(tag)
    return tags, missing


def lane_states(root=None):
    """(off_box, unresolved): production lanes whose model is pinned to another device, and lanes that
    name no role. t1181: both were reported as 'declares no production lanes', which is false."""
    models = Path(root or REPO) / "projects" / "local-inference" / "models"
    serving = json.loads((models / "SERVING.json").read_text(encoding="utf-8"))
    reg = _registry_sizes(json.loads((models / "REGISTRY.json").read_text(encoding="utf-8")))
    off, unresolved = [], []
    for role in serving.get("production_lanes", []):
        tag = (serving.get("roles", {}).get(role) or {}).get("tag")
        if not tag:
            unresolved.append(role)
            continue
        srv = (reg.get(tag) or {}).get("server")
        if srv and _device_of(models, srv) not in (None, "gx10"):
            off.append(f"{role} ({tag} on {_device_of(models, srv)})")
    return off, unresolved


def box_gb():
    for line in Path("/proc/meminfo").read_text().splitlines():
        if line.startswith("MemTotal:"):
            return int(line.split()[1]) / 1024 / 1024
    raise RuntimeError("no MemTotal in /proc/meminfo")


def _includes_kv(tag, root=None):
    reg = json.loads(((Path(root) if root else REPO) / "projects" / "local-inference" / "models"
                      / "REGISTRY.json").read_text(encoding="utf-8"))
    return bool((_registry_sizes(reg).get(tag) or {}).get("includes_kv"))


def check(root=None, box_gb=None, headroom_gb=HEADROOM_GB):
    tags, missing = working_set(root)
    box = box_gb if box_gb is not None else globals()["box_gb"]()
    if not tags and not missing:
        off, unresolved = lane_states(root)
        if unresolved:
            print(f"  UNRESOLVED: production lane(s) {', '.join(unresolved)} name no role with a model -- "
                  "nothing could be added up")
            return 2
        if off:
            print(f"  NOTHING ON THIS BOX: every production lane runs on another device -- {', '.join(off)}; "
                  "this box holds none of their weights")
            return 0
        print("  NOTHING CHECKED: SERVING.json declares no production lanes -- this is not a fit")
        return 2
    if missing:
        print(f"  UNDECLARED: production tag(s) with no weight_gb in REGISTRY.json: {', '.join(missing)} -- "
              "a size is measured, never assumed")
        return 2
    total = sum(tags.values())
    if headroom_gb == HEADROOM_GB and tags and all(_includes_kv(t, root) for t in tags):
        headroom_gb = RESERVE_ONLY_GB
    ok = fits(total, box, headroom_gb)
    print(f"  working set {total:.1f} GB ({', '.join(f'{t} {g:g}' for t, g in tags.items())}) on a "
          f"{box:.0f} GB box with {headroom_gb:g} GB headroom: {'fits' if ok else 'DOES NOT FIT'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(check())
