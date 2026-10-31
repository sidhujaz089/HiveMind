#!/usr/bin/env python3
"""Every Ollama setting on both servers, for the Hub, with how to change each (t618).

Jas: "a tab that gives me access to all the Ollama settings. I want to be able to manually adjust
them if I want." The settings live in each unit's Environment (main: the system ollama.service,
changed with sudo; station 2: the user ollama-station2.service). The Hub cannot run sudo, so it
shows them and the exact way to change one. Contract and oracle: scripts/tests/test_ollama_settings.py;
the page is pinned by test_ollama_page_wired.py.
"""
import html  # noqa: F401 -- render escapes every value through it
import sys


def parse_env(text):
    """Parse Environment from systemctl show output."""
    for line in text.splitlines():
        if line.startswith("Environment="):
            env_str = line[len("Environment="):]
            env_dict = {}
            for part in env_str.split():
                if "=" in part:
                    name, value = part.split("=", 1)
                    if name.startswith("OLLAMA_"):
                        env_dict[name] = value
            return env_dict
    return {}


def render(servers):
    """Render HTML table of settings for each server."""
    html_parts = []
    for name, scope, env in servers:
        html_parts.append(f"<h3>{html.escape(name)}</h3>")
        html_parts.append("<table>")
        for setting in sorted(env.keys()):
            html_parts.append(
                f"<tr><td>{html.escape(setting)}</td><td>{html.escape(env[setting])}</td></tr>"
            )
        html_parts.append("</table>")

        if scope == "system":
            html_parts.append(
                "<p>To change: sudo editing /etc/systemd/system/ollama.service.d/override.conf then "
                "sudo systemctl daemon-reload && sudo systemctl restart ollama</p>"
            )
        else:
            html_parts.append(
                "<p>To change: editing a drop-in under ~/.config/systemd/user/ollama-station2.service.d/ then "
                "systemctl --user daemon-reload && systemctl --user restart ollama-station2</p>"
            )
    return "".join(html_parts)


if __name__ == "__main__":
    print("ollama_settings: parse_env() and render() are not built yet -- see scripts/tests/test_ollama_settings.py")
    sys.exit(2)
