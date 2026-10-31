#!/usr/bin/env python3
"""t618: every Ollama setting on both servers, readable on the Hub, with how to change each.

Jas: "a tab that gives me access to all the Ollama settings. I want to be able to manually adjust
them if I want." Changing a unit's environment needs sudo (main) or a user drop-in (station 2), which
the Hub cannot run -- so the page shows each server's OLLAMA_* settings and the exact command to
change one. RED on main: scripts/ollama_settings.py is a stub with no parse_env()/render(). Pure:
fixture `systemctl show -p Environment` text in, HTML out.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import ollama_settings as os_  # noqa: E402

FAILS = []


def ck(label, got, want):
    ok = got == want
    print(f"  [{'ok' if ok else 'BAD'}]  {label}: {got!r}" + ("" if ok else f"  (want {want!r})"))
    if not ok:
        FAILS.append(label)


MAIN = ("Environment=PATH=/usr/bin:/bin OLLAMA_FLASH_ATTENTION=1 OLLAMA_KV_CACHE_TYPE=q8_0 "
        "OLLAMA_HOST=127.0.0.1:11434 OLLAMA_NUM_PARALLEL=4\n")
ST2 = "Environment=OLLAMA_HOST=127.0.0.1:11435 OLLAMA_MAX_LOADED_MODELS=1 OLLAMA_NUM_PARALLEL=4\n"


def main():
    if not (hasattr(os_, "parse_env") and hasattr(os_, "render")):
        print("  [BAD]  ollama_settings has no parse_env()/render(): the missing change")
        return 1
    m = os_.parse_env(MAIN)
    ck("only OLLAMA_* settings, by name", m, {"OLLAMA_FLASH_ATTENTION": "1", "OLLAMA_KV_CACHE_TYPE": "q8_0",
                                               "OLLAMA_HOST": "127.0.0.1:11434", "OLLAMA_NUM_PARALLEL": "4"})
    ck("an empty or missing Environment line is an empty dict", (os_.parse_env(""), os_.parse_env("Environment=\n")), ({}, {}))
    h = os_.render([("main", "system", os_.parse_env(MAIN)), ("station 2", "user", os_.parse_env(ST2))])
    ck("each server and each of its settings is shown",
       all(x in h for x in ("main", "station 2", "OLLAMA_KV_CACHE_TYPE", "q8_0", "OLLAMA_MAX_LOADED_MODELS")), True)
    ck("the main server's change command uses its system drop-in, with sudo",
       "sudo" in h and "/etc/systemd/system/ollama.service.d" in h, True)
    ck("station 2's change command uses its user drop-in, no sudo",
       "~/.config/systemd/user/ollama-station2.service.d" in h and "systemctl --user" in h, True)
    ck("values are HTML-escaped", "&lt;x&gt;" in os_.render([("main", "system", {"OLLAMA_X": "<x>"})]), True)
    print("test_ollama_settings:", "FAILED" if FAILS else "ok")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
