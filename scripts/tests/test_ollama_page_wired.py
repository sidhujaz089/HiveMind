#!/usr/bin/env python3
"""t618: the Hub serves the Ollama settings at /ollama, read from both units.

RED on main: feedback.py has no /ollama route. Checked in the source (the page runs systemctl).
The parsing and rendering are pinned by test_ollama_settings.py.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def main():
    src = (ROOT / "projects" / "app-repo" / "feedback.py").read_text(encoding="utf-8")
    g0 = src.find("    def do_GET(")
    get = src[g0:src.find("\n    def ", g0 + 1)] if g0 >= 0 else ""
    fails = []
    if '"/ollama"' not in get:
        fails.append("do_GET has no /ollama route")
    if "ollama_settings" not in get or "parse_env(" not in get or "render(" not in get:
        fails.append("/ollama does not parse and render through ollama_settings")
    if "ollama-station2.service" not in get or '"ollama.service"' not in get:
        fails.append("/ollama does not read both units (ollama.service and the user ollama-station2.service)")
    if "timeout=" not in get[get.find('"/ollama"'):get.find('"/ollama"') + 1500]:
        fails.append("the systemctl calls for /ollama carry no timeout")
    for f in fails:
        print("FAIL:", f)
    print("test_ollama_page_wired:", "FAILED" if fails else "ok")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
