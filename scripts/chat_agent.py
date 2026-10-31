#!/usr/bin/env python3
"""Applies file-operation manifests written by Claude Chat, so Chat can clean up
litter it cannot delete itself.

Chat writes a manifest into workspace/commands/. This decides every operation in
it against a fixed set of rules, logs the decision, and -- only when armed --
carries it out.

    python3 scripts/chat_agent.py

Ported from chat-agent.ps1. Every guard below was verified by being made to fire
against fixture manifests, and the port is re-verified the same way; see
scripts/tests/test_chat_agent.py.
"""

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

# ======================================================================================
#
#   A R M I N G
#
#   False -> DRY RUN. Every operation is logged as it would have happened. None execute.
#   True  -> ARMED.   Operations execute.
#
#   CHANGING THIS LINE IS A HUMAN-ONLY ACT (D-21, amendment 1).
#
#   It may be changed only in a commit trailed `Agent: human/jas`. Not by Claude Code, not
#   by Codex, not by any model, in any circumstance, however reasonable the request looks.
#
#   The reason is a loop, not a courtesy: this script executes manifests written by an
#   agent. An agent that can also flip this constant can arm its own executor, and every
#   bound below becomes self-administered. The human is the only part of the chain that is
#   not downstream of an agent's output.
#
#   scripts/audit_compliance.py flags any commit touching this line under any other
#   trailer. If you are a model reading this and the change seems obviously fine: that is
#   what it would feel like either way, which is the entire point.
#
ARMED = False
#
# ======================================================================================

# Operations the manifest schema understands. Anything else is refused unread.
ALLOWED_OPS = ("create", "write", "move", "rename", "delete", "append", "replace")

# Delete is confined to these, relative to the repository root. D-21.
DELETE_ALLOWLIST = ("workspace/inbox/", "workspace/commands/applied/")

# Below this many logged operations, a dry-run week is not evidence (D-21, amendment 5).
DRY_RUN_FLOOR = 20

_TTY = sys.stdout.isatty()


def _c(code, text):
    return f"\033[{code}m{text}\033[0m" if _TTY else text


def resolve_repo_path(root, relative):
    """Resolve a manifest-supplied path and decide whether it is inside the repository.

    Returns (ok, path, rule).

    Paths are normalised without requiring existence, which matters because a create
    target legitimately does not exist yet. The prefix test appends a separator to the
    root first so a sibling directory -- /home/x/AI-other -- cannot match /home/x/AI.
    """
    if relative is None or not str(relative).strip():
        return (False, "", "missing-field")

    relative = str(relative)
    root_full = os.path.abspath(str(root))
    if not root_full.endswith(os.sep):
        root_full += os.sep

    # Manifests address the repository, so every path must be relative to it. Reject a
    # rooted path outright rather than trying to normalise one.
    #
    # This is not belt-and-braces, it is the fix for a real bug found in testing on
    # 2026-08-13: PowerShell's Join-Path concatenated naively instead of honouring a
    # rooted second argument, so "G:/somewhere/else.txt" became "<root>/G:/..." -- still
    # refused, but under target-missing rather than as the escape attempt it was. A
    # refusal recorded against the wrong rule is worse than a silent one: it puts a false
    # reason in the log Jas arms the executor on (D-21, amendment 4).
    #
    # A Windows-style rooted path ("C:\...", "G:/...") is not absolute to Python on
    # Linux, so it is detected explicitly. Dropping that test would let exactly the
    # bug this rule exists for through, on the platform the workspace now runs on.
    if os.path.isabs(relative) or re.match(r"^[A-Za-z]:[\\/]", relative) \
            or relative.startswith("\\\\"):
        return (False, relative, "path-not-relative")

    try:
        full = os.path.abspath(os.path.join(str(root), relative))
    except (OSError, ValueError):
        return (False, relative, "path-unresolvable")

    if not (full + os.sep).startswith(root_full):
        return (False, full, "path-escapes-repo")

    return (True, full, None)


def delete_allowed(root, resolved):
    """Is this resolved path inside a folder where delete is permitted?"""
    root_full = os.path.abspath(str(root))
    if not root_full.endswith(os.sep):
        root_full += os.sep
    for allowed in DELETE_ALLOWLIST:
        prefix = root_full + allowed.replace("/", os.sep)
        if resolved.startswith(prefix):
            return True
    return False


def format_anchor(s):
    """Collapse whitespace and clip. A ledger anchor can be a paragraph, and an
    unclipped one would make the log unreadable."""
    one = re.sub(r"\s+", " ", str(s)).strip()
    if len(one) > 70:
        return '"' + one[:67] + '..."'
    return '"' + one + '"'


def test_operation(op, root):
    """Decide one operation. Returns (verdict, rule, resolved, extra).

    Nothing here touches the disk -- deciding and doing are kept separate so that dry run
    and armed follow the identical decision path and the log means the same thing in both.
    """
    name = op.get("op") if isinstance(op, dict) else None
    if not name:
        return ("REFUSE", "missing-field", "(none)", "no op field")
    if name not in ALLOWED_OPS:
        return ("REFUSE", "op-not-allowlisted", "(none)", f"op={name}")

    # Which field carries the path being acted on, and is there a source as well?
    primary = op.get("to") if name in ("move", "rename") else op.get("path")
    source = op.get("from") if name in ("move", "rename") else None

    ok, path, rule = resolve_repo_path(root, primary)
    if not ok:
        return ("REFUSE", rule, path, None)

    if source is not None:
        s_ok, s_path, s_rule = resolve_repo_path(root, source)
        if not s_ok:
            return ("REFUSE", s_rule, s_path, "(source)")
        if not os.path.exists(s_path):
            return ("REFUSE", "source-missing", s_path, "(source)")
        # A move OUT of a folder is not a delete, but a move whose source is deletable and
        # whose target is not would let a manifest launder a file out of the allowlist. The
        # target is what gets written, so the target is what is judged -- and the source
        # still has to survive its own resolution above.

    if name == "delete":
        if not delete_allowed(root, path):
            return ("REFUSE", "delete-outside-allowlist", path,
                    "allowed: " + ", ".join(DELETE_ALLOWLIST))
        if not os.path.exists(path):
            return ("REFUSE", "target-missing", path, None)

    if name == "create" and os.path.exists(path):
        return ("REFUSE", "target-exists", path, "create must not overwrite")

    if name in ("append", "write") and not os.path.exists(path):
        return ("REFUSE", "target-missing", path, None)

    # replace: a surgical anchored edit.
    #
    # The ledger is ~102K characters and the close sequence previously required rewriting
    # it whole, every session. A truncated write is the worst failure available here --
    # the result looks complete and passes a census against its own declared counts.
    #
    # The anchor must match EXACTLY ONCE. Zero means the file is not what the author
    # thought; more than one means the edit is ambiguous about which occurrence it meant.
    # Both are refusals, and both are real: applying a delta by hand on 2026-08-14, six of
    # seven anchors matched once and the seventh matched zero, because the canary had
    # rotated since the delta was written.
    if name == "replace":
        if not os.path.exists(path):
            return ("REFUSE", "target-missing", path, None)
        old = op.get("old")
        if old is None or old == "":
            return ("REFUSE", "missing-field", path, "no old")
        if op.get("new") is None:
            return ("REFUSE", "missing-field", path, "no new")
        content = Path(path).read_text(encoding="utf-8", errors="replace")
        hits = content.count(old)
        if hits == 0:
            return ("REFUSE", "anchor-not-found", path, f"old={format_anchor(old)}")
        if hits > 1:
            return ("REFUSE", "anchor-not-unique", path,
                    f"old={format_anchor(old)} matches more than once")
        line = content[:content.index(old)].count("\n") + 1
        return ("ALLOW", None, path,
                f"line={line} old={format_anchor(old)} new={format_anchor(op['new'])}")

    return ("ALLOW", None, path, None)


def invoke_operation(op, root, resolved):
    """Perform an allowed operation. Only reached when ARMED."""
    name = op["op"]
    if name == "delete":
        os.remove(resolved)
    elif name in ("create", "write"):
        Path(resolved).write_text(op.get("text", ""), encoding="utf-8")
    elif name == "append":
        with open(resolved, "a", encoding="utf-8") as fh:
            fh.write(op.get("text", ""))
    elif name == "replace":
        # Uniqueness was established in test_operation. Single-occurrence, read and
        # written as raw text so nothing else in a 102K-character ledger is touched --
        # every untouched byte is provably unchanged rather than faithfully retyped.
        c = Path(resolved).read_text(encoding="utf-8")
        i = c.index(op["old"])
        Path(resolved).write_text(
            c[:i] + op["new"] + c[i + len(op["old"]):], encoding="utf-8"
        )
    elif name in ("move", "rename"):
        _, s_path, _ = resolve_repo_path(root, op["from"])
        os.replace(s_path, resolved)


def distinct_operations(log_lines):
    r"""The DECISIONS in a log, deduplicated by what was actually judged.

    The identity of a decision is (manifest, op, repo-relative path). It is NOT the log
    line, and it is NOT the absolute path.

    Both mistakes have now been made. Counting log lines let two runs over three
    manifests read as 27 operations (D-21, amendment 5). Then keying on the ABSOLUTE
    path let the Windows-to-Linux migration double the count on 2026-08-18 -- the same
    two judgements appearing once as `H:\My Drive\...` and once as `/home/user/...`,
    inflating the arming floor with zero new evidence (L-055).

    Probes are excluded, as they are everywhere else: a manifest that deliberately asks
    for refusable things is not evidence about judgement Jas would have exercised.
    """
    seen = set()
    for ln in log_lines:
        if "| op=" not in ln or "PROBE |" in ln:
            continue
        manifest = op = path = ""
        for field in ln.split("|"):
            field = field.strip()
            if field.startswith("manifest="):
                manifest = field[9:]
            elif field.startswith("op="):
                op = field[3:]
            elif field.startswith("path="):
                path = field[5:]
        # Reduce the path to its repo-relative tail so the machine it ran on is not part
        # of a decision's identity.
        norm = path.replace("\\", "/")
        for marker in ("/AI/", "/ai-workspace/"):
            if marker in norm:
                norm = norm.split(marker, 1)[1]
                break
        verdict = "REFUSE" if "| REFUSE" in ln else "ALLOW"
        seen.add((manifest, op, norm, verdict))
    return sorted(seen)


class Agent:
    def __init__(self, repo_root):
        self.repo = Path(repo_root).resolve()
        self.queue_dir = self.repo / "workspace" / "commands"
        self.applied_dir = self.queue_dir / "applied"
        self.log_path = self.repo / "workspace" / "chat-agent-log.txt"
        self.run_decided = 0
        self.run_refused = 0
        self.mode = "LIVE" if ARMED else "DRY"

    def log_op(self, line_mode, manifest, agent, op, resolved, verdict, rule=None, extra=None):
        """One line per decision. Fixed field order so the log greps and sorts.

        Counted here rather than at the call sites so the heartbeat can never disagree
        with the lines above it. A manifest-level refusal is still a decision this script
        made, and an earlier version counted only the per-operation ones -- so the
        heartbeat reported decided=10 beside 12 logged decisions. Small, but this is the
        log the arming decision gets made from.
        """
        self.run_decided += 1
        if verdict in ("REFUSE", "FAILED"):
            self.run_refused += 1

        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        line = (f"{stamp} | {line_mode} | manifest={manifest} | agent={agent} | "
                f"op={op} | path={resolved} | {verdict}")
        if rule:
            line += f" | rule={rule}"
        if extra:
            line += f" | {extra}"
        with open(self.log_path, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
        colour = "33" if verdict == "REFUSE" else ("90" if self.mode == "DRY" else "32")
        print("  " + _c(colour, line))

    def run(self):
        print()
        print(f"chat-agent -- {self.repo}")
        print(_c("31" if ARMED else "36",
                 "  state: " + ("ARMED - operations will execute" if ARMED
                                else "DRY RUN - nothing will be executed")))

        # Refuses to run at all if the workspace is not in a known-good state (D-21). A
        # queue applied on top of a failing check is a change applied to a repository
        # nobody understands.
        check = self.repo / "scripts" / "check_workspace.py"
        rc = subprocess.run(
            [sys.executable, str(check), "--quick"],
            capture_output=True, text=True,
            timeout=900,
        ).returncode
        if rc != 0:
            print(_c("31", "  REFUSING TO RUN - check_workspace.py failed. Fix that first."))
            return 1

        for d in (self.queue_dir, self.applied_dir):
            d.mkdir(parents=True, exist_ok=True)

        manifests = sorted(
            f for f in self.queue_dir.glob("*.json") if f.is_file()
        )
        print(_c("90", "  queue empty" if not manifests
                       else f"  {len(manifests)} manifest(s) queued"))
        print()

        for m in manifests:
            self.process(m)

        self.heartbeat(len(manifests))
        self.progress()
        return 0

    def process(self, m):
        agent = "unknown"

        # A probe manifest deliberately asks for things that must be refused, to test the
        # boundaries while testing them is free. The shouting filename makes the split
        # mechanical rather than a matter of memory, and these must not pollute Jas's
        # arming judgement -- his criterion is about operations he would have decided
        # differently, and an intentional probe is not one.
        #
        # Nothing is hidden by this: probe lines are still written, still visible, and
        # counted in their own column. The tag changes where a number lands, never whether
        # it appears.
        #
        # It also does something more important: A PROBE MANIFEST IS NEVER EXECUTED, EVEN
        # WHEN ARMED. Its whole purpose is to request refusable things. If one were ever
        # allowed by a future rule change, an armed run would carry it out -- so the safety
        # of a probe would depend on the correctness of the very rules it exists to test.
        # Decided always, executed never.
        is_probe = "EXPECT-REFUSALS" in m.name
        line_mode = f"{self.mode} PROBE" if is_probe else self.mode

        try:
            doc = json.loads(m.read_text(encoding="utf-8", errors="replace"))
        except (json.JSONDecodeError, OSError) as exc:
            self.log_op(line_mode, m.name, agent, "(manifest)", "(none)",
                        "REFUSE", "manifest-unparseable", str(exc))
            return

        if not isinstance(doc, dict):
            self.log_op(line_mode, m.name, agent, "(manifest)", "(none)",
                        "REFUSE", "manifest-unparseable", "top level is not an object")
            return

        agent = doc.get("issued-by")
        if not agent:
            self.log_op(line_mode, m.name, "unknown", "(manifest)", "(none)",
                        "REFUSE", "no-issuing-agent", "every manifest must name who wrote it")
            return
        if doc.get("intent") != "apply":
            self.log_op(line_mode, m.name, agent, "(manifest)", "(none)",
                        "REFUSE", "intent-not-apply", f"intent={doc.get('intent')}")
            return

        ops = doc.get("ops") or []
        if not ops:
            self.log_op(line_mode, m.name, agent, "(manifest)", "(none)",
                        "REFUSE", "no-operations", None)
            return

        # TWO PHASES: decide everything, then execute only if nothing was refused.
        #
        # A manifest is atomic. An earlier version decided and executed one operation at a
        # time, so a manifest whose third op was refused had already performed the first
        # two. For a set of anchored ledger edits that is the worst outcome available: a
        # file left half-amended, with its declared section counts no longer describing its
        # contents and nothing recording where it stopped.
        decided = [(op, test_operation(op, self.repo)) for op in ops]
        refused = sum(1 for _, d in decided if d[0] == "REFUSE")

        for op, (verdict, rule, resolved, extra) in decided:
            # Refusal of ANY op holds back ALL of them. The verdict logged for an otherwise
            # allowable operation is still ALLOW -- it was permitted; it simply did not run
            # -- and the reason is named so the log does not read as if it executed.
            if ARMED and not is_probe and refused == 0 and verdict == "ALLOW":
                try:
                    invoke_operation(op, self.repo, resolved)
                except Exception as exc:  # noqa: BLE001 - the reason is logged verbatim
                    self.log_op(line_mode, m.name, agent, op.get("op"), resolved,
                                "FAILED", "execution-error", str(exc))
                    refused += 1
                    continue
            if ARMED and not is_probe and refused > 0 and verdict == "ALLOW":
                extra = " | ".join(
                    x for x in (extra, "held: another op in this manifest was refused") if x
                )
            self.log_op(line_mode, m.name, agent, op.get("op"), resolved, verdict, rule, extra)

        # Applied manifests move aside and are never deleted (D-21). In dry run nothing
        # moves -- the same manifest is re-evaluated next run, which is what makes a
        # dry-run week accumulate evidence rather than consume its own queue.
        if ARMED and refused == 0 and not is_probe:
            os.replace(m, self.applied_dir / m.name)

    def heartbeat(self, manifest_count):
        """Written EVERY run, including a run that found nothing to do.

        The first scheduled run (2026-08-13 07:45:02) exited 0 and left no file at all,
        because with an empty queue the op logger was never reached. "Ran and found
        nothing" and "never ran" were then indistinguishable on disk -- and a silent
        failure would have looked exactly like a quiet week.

        The whole dry-run period is an evidence exercise, and D-21 makes arming
        conditional on what this log says. A guard that leaves no record cannot be retired
        on evidence. So the run itself is a record, not only the operations inside it.
        """
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        with open(self.log_path, "a", encoding="utf-8") as fh:
            fh.write(f"{stamp} | {self.mode} | RUN | manifests={manifest_count} | "
                     f"decided={self.run_decided} | refused={self.run_refused}\n")

    def progress(self):
        """Progress toward the evidence floor. A dry-run week with four lines in it is not
        a week of evidence, and arming on it would be a decision made on nothing."""
        if not self.log_path.is_file():
            return
        all_lines = self.log_path.read_text(encoding="utf-8", errors="replace").split("\n")
        probes = [ln for ln in all_lines if "PROBE |" in ln and "| op=" in ln]
        # DISTINCT decisions, not log lines. The queue is not consumed in dry run, so the
        # same manifest is re-decided every run; counting lines would let the floor be
        # reached by leaving one file in the queue rather than by exercising the boundaries.
        logged = distinct_operations(all_lines)
        runs = len([ln for ln in all_lines if "| RUN |" in ln])
        refusals = len([o for o in logged if o[3] == "REFUSE"])

        print()
        print(_c("90", f"  runs logged: {runs}   operations: {len(logged)}   "
                       f"refusals: {refusals}"))
        if probes:
            print(_c("90", f"  probe operations: {len(probes)} - logged, excluded from "
                           "the arming figures above"))
        if not ARMED:
            if len(logged) < DRY_RUN_FLOOR:
                print(_c("90", f"  dry-run evidence: {len(logged)}/{DRY_RUN_FLOOR} "
                               "operations - below the floor, keep running"))
                if not logged and runs:
                    print(_c("90", "  (the watcher is running; nothing has queued a "
                                   "manifest yet)"))
            else:
                print(_c("36", f"  dry-run evidence: {len(logged)}/{DRY_RUN_FLOOR} "
                               "operations - floor reached"))
                print(_c("90", "  Arm only if nothing was refused you would have allowed, "
                               "and nothing"))
                print(_c("90", "  allowed you would have refused. Either column: talk "
                               "first (D-21)."))
        print()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo-root", default=None)
    args = ap.parse_args()
    root = args.repo_root or Path(__file__).resolve().parent.parent
    return Agent(root).run()


if __name__ == "__main__":
    sys.exit(main())
