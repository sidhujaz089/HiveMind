#!/usr/bin/env python3
"""Make this box's models match what the repository says they should be.

    python3 scripts/models.py status     what is here, what is missing, what drifted
    python3 scripts/models.py ensure     create missing aliases, warm the roles, then lock
    python3 scripts/models.py lock       record the digests now on disk
    python3 scripts/models.py verify     exit 1 on any drift  (for checks and CI)

WHY THIS EXISTS
---------------
Loading a model correctly used to depend on somebody remembering. Sampling was resolved by
matching a family name inside the tag string, so `ollama cp qwen3.8:27b-mtp-bf16 q16-sa`
produced a tag with byte-identical weights, no family name and therefore NO profile: it ran
on the backend defaults Qwen's own documentation forbids, for a day, driving the Duo.

The fix is to stop inferring what a tag is from what it is called. `models/REGISTRY.json`
DECLARES the models this box should have and what each one is; `models/tags.lock.json` pins
the digest of every tag. Both are committed, so identity is known offline, before anything is
running, and identically on every clone. `settings.digests()` reads the lock, not Ollama.

`ensure` is the whole setup in one command, and it is meant to stay that way -- Jas wants this
to end up behind a single button, which is only possible if every step is here rather than in
a person's head.

WHAT DRIFT MEANS
----------------
A tag whose digest no longer matches the lock is not a curiosity. It means the name now points
at different weights, so every measurement recorded under that name describes a model that is
no longer there. `verify` fails on it rather than quietly re-locking, because re-locking is how
you lose the evidence that anything moved.
"""
import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LI = REPO / "projects" / "local-inference"
REGISTRY = LI / "models" / "REGISTRY.json"
SERVING = LI / "models" / "SERVING.json"
LOCK = LI / "models" / "tags.lock.json"
sys.path.insert(0, str(LI))
import settings                                              # noqa: E402


def _c(code, text):
    return f"\033[{code}m{text}\033[0m" if sys.stdout.isatty() else text


def live():
    """{tag: digest} as Ollama reports it right now, or {} if it is not answering."""
    try:
        r = subprocess.run(["ollama", "list"], capture_output=True, text=True, timeout=30)
    except Exception:                                        # noqa: BLE001
        return {}
    out = {}
    for line in r.stdout.splitlines()[1:]:
        p = line.split()
        if len(p) >= 2:
            out[p[0]] = p[1]
    return out


def _server_entry(sid):
    try:
        return json.loads((REGISTRY.parent / "SERVERS.json").read_text())["servers"].get(sid) or {}
    except (OSError, ValueError, KeyError):
        return {}


def remote_digests(url):
    """{tag: 12-char digest} from an Ollama server's /api/tags, or None if it does not answer."""
    import urllib.request
    try:
        with urllib.request.urlopen(url.rstrip("/") + "/api/tags", timeout=8) as f:
            return {m["name"]: m["digest"][:12] for m in json.load(f).get("models", [])}
    except (OSError, ValueError, KeyError):
        return None


def registry():
    return json.loads(REGISTRY.read_text())


def serving():
    """{tag: [role, ...]} for the models this box actually USES. {} if the file is absent.

    The dynamic source Jas asked for on 2026-09-18. REGISTRY.json says what a tag IS
    (digest-pinned, so a measurement recorded under a name describes the weights that
    produced it) and that is worth keeping. It was ALSO being read as the list of models
    that exist, and that half went stale every time a model was pulled -- `ollama pull
    qwen3.8` mints a `:latest` alias and the invariant check went red until somebody
    hand-edited JSON. Being on the box and being in use are different facts.
    """
    try:
        d = json.loads(SERVING.read_text())
    except (OSError, ValueError) as e:
        print(f"  NOTE: SERVING.json unreadable ({type(e).__name__}: {e}); "
              f"every on-disk tag will be treated as in use.", file=sys.stderr)
        return {}
    out = {}
    for role, spec in (d.get("roles") or {}).items():
        tag = spec.get("tag") if isinstance(spec, dict) else spec
        if tag:
            out.setdefault(tag, []).append(role)
    return out


def production_tags():
    """The tags the scheduler may dispatch production work to: the models of the roles with Building
    on their list in the role store (the Models tab), in store order. SERVING.json's
    production_lanes is read only if the store cannot be."""
    try:
        d = json.loads(SERVING.read_text())
    except (OSError, ValueError):
        return []
    roles = d.get("roles") or {}
    try:
        import role_store
        store = role_store.load()
        return list(dict.fromkeys(store["roles"][r]["model"] for r in role_store.building_roles(store)))
    except Exception as e:                                          # noqa: BLE001 -- said, not swallowed
        print(f"  NOTE: role store unreadable ({type(e).__name__}: {e}); production lanes from "
              f"SERVING.json", file=sys.stderr)
    out = []
    for r in d.get("production_lanes") or []:
        spec = roles.get(r)
        tag = spec.get("tag") if isinstance(spec, dict) else spec
        if tag and tag not in out:
            out.append(tag)
    return out


def locked():
    try:
        return json.loads(LOCK.read_text()).get("tags", {})
    except Exception:                                        # noqa: BLE001
        return {}


def engine_identity(entry):
    """For a model another engine serves (REGISTRY entry with `server`): what the weights and
    runtime ARE, as {"files": sha256 of the served dir's config.json + index, "image": the
    container image id}. The Ollama digest's job, for engines that have no digest (2026-10-06,
    docs/ENGINES.md). Unreadable parts are None, which never matches a pin."""
    import hashlib
    pin = entry.get("pin") or {}
    d = Path(pin.get("model_dir", "")).expanduser()
    try:
        h = hashlib.sha256()
        for name in ("config.json", "model.safetensors.index.json"):
            h.update((d / name).read_bytes())
        files = h.hexdigest()[:16]
    except OSError:
        files = None
    image = None
    if pin.get("image_ref"):
        r = subprocess.run(["docker", "image", "inspect", pin["image_ref"], "--format", "{{.Id}}"],
                           capture_output=True, text=True, timeout=30)
        image = r.stdout.strip()[:19] if r.returncode == 0 else None
    return {"files": files, "image": image}


def report():
    """(rows, problems). One row per declared tag, plus anything on disk we never declared."""
    reg, now, lk = registry(), live(), locked()
    rows, problems = [], []
    declared = [(m["tag"], m.get("profile"), m.get("role", ""), None)
                for m in reg["base_models"]] + \
               [(a["tag"], None, a.get("why", ""), a["from"]) for a in reg["aliases"]]

    # A VERDICT OVER ZERO MODELS IS NOT A PASS (L-146, workspace/vacuous-registry.json).
    # With an empty registry the loop below never runs, `problems` stays empty, and
    # `verify` prints "ok" and returns 0 having examined nothing -- the most expensive
    # shape of wrong, because it looks like success. Flagged 2026-09-07 by check_vacuous.py,
    # which had been blocking session_end since the file landed on 2026-09-03.
    if not declared:
        problems.append("REGISTRY.json declares no models at all. Nothing was examined, so "
                        "there is no verdict to give -- this is not a pass.")

    other = {m["tag"]: m for m in reg["base_models"] if m.get("server")}
    # AN OLLAMA ON ANOTHER DEVICE (the ThinkPad eGPU, 2026-10-12) has a digest like the local one,
    # read from ITS /api/tags rather than from `ollama list` on this box. A server that does not
    # answer (a laptop asleep) is reported UNREACHABLE: shown, not counted as checked, and not a
    # failure of this box's registry.
    remote = {t: m for t, m in other.items() if _server_entry(m["server"]).get("engine") == "ollama"}
    for tag, m in remote.items():
        url = _server_entry(m["server"]).get("url", "")
        got = remote_digests(url)
        pinned = lk.get(tag)
        entry, how = settings.resolve_sampling(tag)
        if got is None:
            state, detail = "UNREACHABLE", f"{m['server']} ({url}) did not answer; not checked"
        elif tag not in got:
            state, detail = "MISSING", f"not on {m['server']}"
            problems.append(f"{tag} is declared on {m['server']} but that server does not have it")
        elif not pinned:
            state, detail = "UNPINNED", "run: models.py lock"
            problems.append(f"{tag} has no digest in tags.lock.json")
        elif got[tag] != pinned:
            state, detail = "DRIFT", f"lock {pinned} != {m['server']} {got[tag]}"
            problems.append(f"{tag} DRIFTED on {m['server']}: lock {pinned} != served {got[tag]}")
        elif entry is None:
            state, detail = "NO PROFILE", "no vendor sampling and no declared unknown"
            problems.append(f"{tag} resolves to no sampling profile")
        else:
            state, detail = "ok", f"on {m['server']}"
        rows.append({"tag": tag, "state": state, "how": how, "src": None,
                     "digest": (got or {}).get(tag, "-"), "role": m.get("role", ""), "detail": detail})
    other = {t: m for t, m in other.items() if t not in remote}
    for tag, m in other.items():
        got, want = engine_identity(m), m.get("pin") or {}
        entry, how = settings.resolve_sampling(tag)
        state, detail = "ok", f"served by {m['server']}"
        bad = [k for k in ("files", "image") if want.get(k) and got.get(k) != want.get(k)]
        # AN EMPTY `bad` MUST MEAN "COMPARED AND EQUAL", never "nothing to compare": a pin missing
        # either half would leave `bad` empty and the model reported ok having checked nothing.
        if not (want.get("files") and want.get("image")):
            state, detail = "UNPINNED", "REGISTRY pin lacks files and/or image"
            problems.append(f"{tag} is served by {m['server']} but REGISTRY.json pins nothing to "
                            f"compare it against -- add pin.files and pin.image (models.py "
                            f"engine_identity shows the current values)")
        elif bad:
            state, detail = "DRIFT", "; ".join(f"{k} {want[k]} != now {got.get(k)}" for k in bad)
            problems.append(f"{tag} DRIFTED ({', '.join(bad)}): the pin in REGISTRY.json no longer "
                            f"matches what {m['server']} would serve")
        elif entry is None:
            state, detail = "NO PROFILE", "no vendor sampling and no declared unknown"
            problems.append(f"{tag} resolves to no sampling profile")
        rows.append({"tag": tag, "state": state, "how": how, "src": None,
                     "digest": want.get("files") or "-", "role": m.get("role", ""), "detail": detail})
    declared = [t for t in declared if t[0] not in other and t[0] not in remote]

    for tag, profile, role, src in declared:
        on_disk = now.get(tag)
        pinned = lk.get(tag)
        entry, how = settings.resolve_sampling(tag)
        state, detail = "ok", ""
        if now and not on_disk:
            state, detail = "MISSING", ("create with: ollama cp %s %s" % (src, tag)
                                        if src else "pull it")
            problems.append(f"{tag} is declared but not on this box")
        elif on_disk and pinned and on_disk != pinned:
            state, detail = "DRIFT", f"lock {pinned} != disk {on_disk}"
            problems.append(f"{tag} DRIFTED: the lock says {pinned}, the box says {on_disk}. "
                            f"The name now points at different weights, so measurements "
                            f"recorded under it describe a model that is not there.")
        elif on_disk and not pinned:
            state, detail = "UNLOCKED", "run: models.py lock"
            problems.append(f"{tag} has no pinned digest")
        elif entry is None:
            state, detail = "NO PROFILE", "no vendor sampling and no declared unknown"
            problems.append(f"{tag} resolves to no sampling profile")
        if src and on_disk and now.get(src) and on_disk != now[src]:
            state = "ALIAS MISMATCH"
            detail = f"copy of {src} but digests differ"
            problems.append(f"{tag} should be a copy of {src} and is not")
        rows.append({"tag": tag, "state": state, "how": how, "src": src,
                     "digest": on_disk or pinned or "-", "role": role, "detail": detail})

    # ON DISK BUT NOT DECLARED IS ONLY A PROBLEM IF SOMETHING SERVES IT.
    # Until 2026-09-22 every undeclared tag was a problem, which made the invariant check red
    # for five days over `qwen3.8:latest` -- an alias `ollama pull qwen3.8` mints on its own,
    # sharing a blob with a model already declared. Nothing was broken; the rule was a
    # closed-world list over a directory Jas experiments in, so every experiment failed a
    # check. An unused tag is somebody trying a model out. A tag a ROLE depends on is a
    # different thing entirely, and that one still has to be pinned and profiled.
    in_use = serving()
    for tag in sorted(set(now) - {r["tag"] for r in rows}):
        roles = in_use.get(tag)
        if roles:
            rows.append({"tag": tag, "state": "UNDECLARED", "src": None, "digest": now[tag],
                         "how": settings.resolve_sampling(tag)[1], "role": ",".join(roles),
                         "detail": "SERVING.json depends on it; declare with: models.py adopt"})
            problems.append(f"{tag} serves {', '.join(roles)} but is declared nowhere -- "
                            f"its weights are unpinned, so a measurement recorded under this "
                            f"name cannot be tied to the model that produced it. "
                            f"Fix: python3 scripts/models.py adopt {tag}")
        else:
            rows.append({"tag": tag, "state": "untracked", "src": None, "digest": now[tag],
                         "how": settings.resolve_sampling(tag)[1], "role": "",
                         "detail": "on the box, no role depends on it -- not a finding"})
    # A tag a role NAMES but the box does not have is the reverse fault, and it is severe:
    # the caller will ask for a model that is not there.
    for tag, roles in sorted(in_use.items()):
        if now and tag not in now and tag not in other and tag not in remote:
            problems.append(f"{tag} is named by SERVING.json for {', '.join(roles)} but is "
                            f"NOT on this box; that role has nothing to call.")
    return rows, problems


def show(rows):
    print(f"{'tag':26} {'state':15} {'sampling':8} {'digest':14} role / what to do")
    print("-" * 100)
    for r in rows:
        colour = {"ok": "32", "MISSING": "31", "DRIFT": "31", "ALIAS MISMATCH": "31"}.get(
            r["state"], "33")
        print(f"{r['tag']:26} {_c(colour, f'{chr(32)}'.join([r['state']])):15} "
              f"{r['how']:8} {r['digest']:14} {r['role'][:40]}"
              f"{('  -> ' + r['detail']) if r['detail'] else ''}")


def do_lock():
    _prior = locked()
    now = live()
    if not now:
        print("  Ollama is not answering -- refusing to write an EMPTY lock over a good one.")
        return 1
    LOCK.write_text(json.dumps(
        {"_what": "Observed digests. `scripts/models.py lock` writes it; settings.py READS it "
                  "so sampling resolves with NO call to Ollama. A digest here that stops "
                  "matching the box is drift, and models.py verify fails on it.",
         "_written": time.strftime("%Y-%m-%d"),
         "tags": {t: now[t] for t in sorted(now)}}, indent=1) + "\n")
    # Tags on ANOTHER device's Ollama are pinned from that server; one that does not answer
    # keeps its previous pin rather than losing it to a laptop being asleep.
    old, d = _prior, json.loads(LOCK.read_text())
    for m in registry()["base_models"]:
        srv = _server_entry(m.get("server", "")) if m.get("server") else {}
        if srv.get("engine") == "ollama":
            got = remote_digests(srv.get("url", "")) or {}
            pin = got.get(m["tag"]) or old.get(m["tag"])
            if pin:
                d["tags"][m["tag"]] = pin
    LOCK.write_text(json.dumps(d, indent=1) + "\n")
    print(f"  locked {len(now)} tag(s) -> {LOCK.relative_to(REPO)}")
    return 0


def do_adopt(tag):
    """Declare an on-disk tag and pin its digest, atomically enough to be one step.

    THE VERB THAT DID NOT EXIST. `ensure` walks registry -> box (it creates aliases the
    registry already declares) and CANNOT adopt an undeclared tag, but the check's hint said
    "Fix with: models.py ensure" for exactly that case -- so the only hint the tool offered
    pointed at the one path it did not own, and the real fix was hand-editing JSON. Q4 traced
    this on 2026-09-18 (F2) and named the missing verbs; Jas's own delegation note of
    2026-09-20 repeated the wrong hint, which is how a wrong instruction propagates once
    nobody can run it.
    """
    now = live()
    if not now:
        print("  Ollama is not answering; refusing to adopt a tag whose digest cannot be read.")
        return 1
    if tag not in now:
        print(f"  {tag} is not on this box. `ollama pull` it first -- adopting a tag that "
              f"does not exist would declare a model nobody can call.")
        return 1
    reg = registry()
    if any(m.get("tag") == tag for m in reg["base_models"]) or \
       any(a.get("tag") == tag for a in reg.get("aliases", [])):
        print(f"  {tag} is already declared; nothing to do.")
        return 0
    _, how = settings.resolve_sampling(tag)
    reg["base_models"].append({"tag": tag, "profile": how, "role": "adopted", "pulled": True})
    REGISTRY.write_text(json.dumps(reg, indent=1) + "\n")
    print(f"  declared {tag} (sampling resolves via: {how})")
    return do_lock()


def do_drop(tag):
    """Undeclare a tag. Touches the registry only -- never the box.

    Deleting weights is a separate, destructive act with a different blast radius, and a verb
    that did both would eventually do the second when someone meant the first.
    """
    reg = registry()
    before = len(reg["base_models"]) + len(reg.get("aliases", []))
    reg["base_models"] = [m for m in reg["base_models"] if m.get("tag") != tag]
    reg["aliases"] = [a for a in reg.get("aliases", []) if a.get("tag") != tag]
    if len(reg["base_models"]) + len(reg["aliases"]) == before:
        print(f"  {tag} was not declared; nothing to do.")
        return 0
    roles = serving().get(tag)
    if roles:
        print(f"  REFUSING: SERVING.json still names {tag} for {', '.join(roles)}. "
              f"Undeclaring it would leave that role calling an unpinned model. "
              f"Point the role somewhere else first.")
        return 1
    REGISTRY.write_text(json.dumps(reg, indent=1) + "\n")
    print(f"  undeclared {tag}. The weights are untouched; `ollama rm {tag}` removes those.")
    return 0


def do_ensure():
    reg, now = registry(), live()
    if not now:
        print("  Ollama is not answering. Start it first; nothing was changed.")
        return 1
    made = 0
    for a in reg["aliases"]:
        if a["tag"] in now:
            continue
        if a["from"] not in now:
            print(_c("31", f"  cannot create {a['tag']}: its source {a['from']} is not here"))
            continue
        print(f"  ollama cp {a['from']} {a['tag']}   ({a['why']})")
        r = subprocess.run(["ollama", "cp", a["from"], a["tag"]],
                           capture_output=True, text=True, timeout=300)
        if r.returncode:
            print(_c("31", f"    FAILED: {(r.stderr or r.stdout).strip()[:120]}"))
        else:
            made += 1
    print(f"  {made} alias(es) created" if made else "  every declared alias already exists")
    do_lock()
    settings.digests(refresh=True)
    rows, problems = report()
    show(rows)
    return 0 if not problems else 1


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("cmd", choices=["status", "ensure", "lock", "verify", "adopt", "drop"])
    ap.add_argument("tag", nargs="?", help="the model tag, for adopt and drop")
    a = ap.parse_args()

    if a.cmd in ("adopt", "drop"):
        if not a.tag:
            ap.error(f"{a.cmd} needs a tag: python3 scripts/models.py {a.cmd} <tag>")
        return do_adopt(a.tag) if a.cmd == "adopt" else do_drop(a.tag)
    if a.cmd == "lock":
        return do_lock()
    if a.cmd == "ensure":
        return do_ensure()

    rows, problems = report()
    if a.cmd == "status":
        show(rows)
        if not live():
            print(_c("33", "\n  Ollama is not answering, so 'MISSING' was not checked. "
                           "Digests came from the lock -- which is the point of having one."))
    for p in problems:
        print(_c("31", f"  {p}"))
    if a.cmd == "verify":
        print("models verify:", "FAILED" if problems else "ok")
        return 1 if problems else 0
    if not problems:
        print(_c("32", "\n  Every declared model is present, pinned and has a sampling profile."))
    return 0


if __name__ == "__main__":
    sys.exit(main())
