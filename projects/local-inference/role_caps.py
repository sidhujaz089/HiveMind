#!/usr/bin/env python3
"""Which tools each model ROLE may use -- the switchboard behind the Models tab's Roles section.

    python3 projects/local-inference/role_caps.py                       # print the table
    python3 projects/local-inference/role_caps.py set manager run_command on

Jas, 2026-09-28: "I want to give full access to the local models. Then based on role, such as
builder or manager, we can restrict. I am fine with Q4 and Q16 being unrestricted but Gemma
needs guardrails ... I want a Role section in the models tab where I can see what is toggled and
manually configure."

The table lives in models/ROLE-CAPS.json, next to SERVING.json (which says which model plays each
role). console.py consults it twice: when it offers tools to a model (a tool that is off is never
shown) and when a model calls one (a tool that is off is refused, by name, saying where to turn
it on). A tool that is on but not installed is reported as such, never faked.

Gemma's build lane (coder.py) does not use these tools at all: it edits one file in a sandbox
branch and every change is reviewed before merge. Its row here governs any console call made
under the coder role, and defaults to nothing.
"""
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
CAPS = HERE / "models" / "ROLE-CAPS.json"
SERVING = HERE / "models" / "SERVING.json"
REFUSALS = HERE.parent.parent / "workspace" / "tool-refusals.jsonl"

# Every console tool, in display order, with what it lets a model do.
TOOLS = [
    ("read_file", "read any repository file"),
    ("search_repo", "search the repository"),
    ("search_tasks", "search open AND finished tasks (read-only)"),
    ("commits_since", "find commits since a date that touch something (read-only)"),
    ("agent_status", "see what is running"),
    ("dispatch_coder", "hand a scoped task to the builder"),
    ("escalate", "hand something to Claude Code"),
    ("note_task", "add a note to a task"),
    ("hold_task", "hold or release a task for Jas"),
    ("run_command", "run any shell command"),
    ("write_file", "create or replace any file"),
    ("edit_file", "edit any file"),
    ("web_fetch", "read a public web page (off unless switched on)"),
    ("web_search", "search the web (SearXNG on this box; off unless switched on)"),
]
_TODAY = {"read_file", "search_repo", "agent_status", "dispatch_coder", "escalate", "note_task",
          "hold_task"}


STORE = HERE / "models" / "ROLES.json"


def _store():
    """The role store, or None if it cannot be read (then SERVING.json + ROLE-CAPS.json, as before)."""
    try:
        return json.loads(STORE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as e:
        print(f"  NOTE: role store unreadable ({e}); falling back to SERVING.json", file=sys.stderr)
        return None


def _roles():
    """Model roles only: tools are a console matter, and a person or agent role has no console."""
    st = _store()
    if st is not None:
        return [k for k, r in st["roles"].items() if r.get("kind", "model") == "model"]
    try:
        return list((json.loads(SERVING.read_text(encoding="utf-8")).get("roles") or {}).keys())
    except (OSError, ValueError) as e:
        print(f"  NOTE: SERVING.json unreadable ({e}); roles default to manager/auditor/coder",
              file=sys.stderr)
        return ["manager", "auditor", "coder"]


def _default(role):
    """What each role could do before this table existed: the two console roles (manager, auditor)
    had today's seven tools; every other role -- the builder, the embedder -- had none."""
    return {name: (role in ("manager", "auditor") and name in _TODAY) for name, _ in TOOLS}


def load():
    """{role: {tool: bool}} -- every serving role, every tool, defaults filled in."""
    st = _store()
    if st is not None:                        # the role store is the source (2026-09-29)
        saved = {k: r.get("tools") or {} for k, r in st["roles"].items()}
    else:
        try:
            saved = json.loads(CAPS.read_text(encoding="utf-8")).get("roles") or {}
        except FileNotFoundError:
            saved = {}
        except (OSError, ValueError) as e:
            print(f"  NOTE: ROLE-CAPS.json unreadable ({e}); using defaults", file=sys.stderr)
            saved = {}
    out = {}
    for role in _roles():
        caps = _default(role)
        caps.update({k: bool(v) for k, v in (saved.get(role) or {}).items() if k in caps})
        out[role] = caps
    return out


def record_refusal(tool, role, decision="REFUSED", reason="", harness="console", session="", fallback="", task_ref=""):
    """Append one refusal to the append-only log; never let logging affect admission."""
    row = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "session": session,
        "harness": harness,
        "role": role,
        "tool": tool,
        "decision": decision,
        "reason": reason,
        "fallback": fallback,
        "task_ref": task_ref,
    }
    try:
        with open(REFUSALS, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")
    except OSError as e:
        print(f"  (tool-refusal row not written to {REFUSALS}: {type(e).__name__}: {e})", file=sys.stderr)


def allowed(role, tool):
    ok = bool(load().get(role or "manager", {}).get(tool, False))
    if not ok:
        record_refusal(
            tool=tool,
            role=role,
            reason=f"{tool} is switched off for the {role} role "
                   "(Models tab -> Roles, or role_caps.py set {role} {tool} on)",
        )
    return ok


def save(table, who="role_caps.py"):
    """Write each role's tools into the role store (logged in its changelog)."""
    st = _store()
    if st is not None:
        sys.path.insert(0, str(HERE.parent.parent / "scripts"))
        import role_store
        for k, caps in table.items():
            if k in st["roles"]:
                st["roles"][k]["tools"] = caps
        role_store.save(st, who, "tools changed")
        return
    CAPS.write_text(json.dumps({
        "_what": "Which console tools each model role may use. Edited from the Models tab (Roles) "
                 "or role_caps.py set. console.py enforces it when offering and when running a tool.",
        "roles": table}, indent=1) + "\n", encoding="utf-8")


def set_cap(role, tool, on):
    table = load()
    if role not in table or tool not in table[role]:
        raise KeyError(f"unknown role or tool: {role} / {tool}")
    table[role][tool] = bool(on)
    save(table)
    return table


def main(argv):
    if argv[:1] == ["set"] and len(argv) == 4:
        set_cap(argv[1], argv[2], argv[3].lower() in ("on", "true", "1", "yes"))
    table = load()
    roles = list(table)
    print("tool".ljust(16) + "".join(r[:10].ljust(11) for r in roles))
    for name, _ in TOOLS:
        print(name.ljust(16) + "".join(("on" if table[r][name] else "-").ljust(11) for r in roles))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
