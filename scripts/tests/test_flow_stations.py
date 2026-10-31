"""t1090 -- a SERVING.json station must have a node on the Flow chart.

Why this suite exists (workspace/rca/a97157fc7a67.md): the Senior station was added to
projects/local-inference/models/SERVING.json on 2026-09-28 -- its own Ollama server, its own
lease (SERVING.json:26 `workspace/scoping-lease-station2.json`, the unit named at :27 as
`ollama-station2.service`) -- and no FLOW.json `services` node was ever created for it. Nothing
compared the two files, so the chart of record stayed silent about a whole inference server.

Red on main for ONE reason: scripts/flow_coverage.py has no station-coverage function, and its
main() (:182-228) reports only script coverage (`miss = uncovered()`, `return 1 if miss else 0`).

Rules this suite refuses to break: no model call, no live server, no process left running, no
fixed /tmp path, every subprocess.run carries timeout=, and nothing is written to the repository.
The two live repo files are READ, read-only, by the CLI-visibility checks at the end.

The fixture charts below are built in FLOW.json's REAL services[] shape -- all 13 live entries
carry exactly id, name, unit (read 2026-10-13) -- so a build that parses the dict sees the shape
of record, and the real role entry mirrors SERVING.json's senior entry.
"""
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import flow_coverage as FC

ROOT = Path(__file__).resolve().parents[2]
SERVING = ROOT / "projects" / "local-inference" / "models" / "SERVING.json"
FLOW = ROOT / "projects" / "local-inference" / "models" / "FLOW.json"

ok = 0
fail = 0


def check(name, cond):
    global ok, fail
    print("  [%s]   %s" % ("ok" if cond else "FAIL", name))
    if cond:
        ok += 1
    else:
        fail += 1


def chart(*units):
    """A chart in FLOW.json's real services[] shape (id, name, unit)."""
    return {"services": [{"id": "svc_" + u.split(".")[0], "name": u, "unit": u} for u in units]}


SENIOR_ROLE = {
    "tag": "swift",
    "lease": "workspace/scoping-lease-station2.json",
    "what": ("SENIOR BUILDER -- PLACEHOLDER. Runs every preflight review on its own Ollama "
             "server (ollama-station2.service), so scoped work reaches the coder without "
             "queueing behind session reviews on the main server."),
}
MANAGER_ROLE = {"tag": "swift", "what": "Supervises, reviews briefs, scopes the work."}
CODER_ROLE = {"tag": "swift", "what": "The production build lane."}
EMBEDDER_ROLE = {"tag": "qwen3-embedding:0.6b", "what": "memory.py recall and the corpus index."}

LIVE_CHART = chart("app-feedback.service", "manager-queue.service", "swift.service")
CHART_WITH_STATION = chart("app-feedback.service", "manager-queue.service",
                           "ollama-station2.service")


def names(findings, role, unit):
    """Findings naming this role and this unit -- the pair a person would act on."""
    return [f for f in findings if role in str(f) and unit in str(f)]


def main():
    if not hasattr(FC, "station_gaps"):
        print("  [FAIL]   flow_coverage.station_gaps() exists")
        print("  scripts/flow_coverage.py has no station coverage -- that is exactly the change "
              "t1090 asks for. The assertions below are skipped; all of them are red for this "
              "one reason.")
        print("%d passed, %d failed" % (ok, fail + 1))
        return 1

    # --- the case the function exists for: a station with no chart node -------------------
    serving = {"roles": {"senior": SENIOR_ROLE, "manager": MANAGER_ROLE,
                         "coder": CODER_ROLE, "embedder": EMBEDDER_ROLE}}
    out = FC.station_gaps(serving, LIVE_CHART)
    check("a role owning its own station with no services node is reported",
          len(names(out, "senior", "ollama-station2.service")) == 1)
    check("  exactly one finding is raised (the suite must not report every role)", len(out) == 1)
    check("  a role with no station of its own raises nothing "
          "(manager, coder, embedder name no endpoint, lease or unit)",
          not [f for f in out if "manager" in str(f) or "coder" in str(f)
               or "embedder" in str(f)])

    # --- green case: the node exists, so the station is on the chart ----------------------
    check("a chart carrying the station's unit reports no gap",
          FC.station_gaps(serving, CHART_WITH_STATION) == [])
    check("  and an empty roles map reports nothing",
          FC.station_gaps({"roles": {}}, LIVE_CHART) == [])

    # --- the defect as found: the ROLE label is not the STATION ---------------------------
    label_chart = {"services": [{"id": "svc_senior", "name": "Senior review lane",
                                 "unit": "manager-queue.service"}]}
    out2 = FC.station_gaps(serving, label_chart)
    check("a services node NAMED 'Senior' does not cover the station -- the chart node is the "
          "server the role runs on, not the role's label (the finding verbatim: the role label "
          "is present, the station is not)",
          len(names(out2, "senior", "ollama-station2.service")) == 1)

    # --- t772's lesson: coverage is the whole unit name, never a substring ----------------
    prefix = {"roles": {"probe": {"lease": "workspace/lease-probe.json",
                                  "what": "serves on ollama.service, the default server."}}}
    out3 = FC.station_gaps(prefix, chart("ollama-station2.service"))
    check("a unit that is a prefix of a charted unit is NOT covered (ollama.service is not "
          "covered by ollama-station2.service)",
          len(names(out3, "probe", "ollama.service")) == 1)

    # --- inverted fixture: a station with no nameable unit is a FINDING, not silence ------
    unnamed = {"roles": {"probe": {"endpoint": "http://127.0.0.1:11435",
                                   "what": "has its own Ollama; the unit is not written down."}}}
    out4 = FC.station_gaps(unnamed, LIVE_CHART)
    check("a station that names no systemd unit is a finding, not silence "
          "(silence is how the Senior station went missing)",
          len(out4) == 1 and "probe" in str(out4[0]))

    # --- endpoint is a server URL: the unit comes from the role's text --------------------
    url_role = {"roles": {"auditor": {"endpoint": "http://100.64.0.11:11434",
                                      "what": "Q4 on ollama-thinkpad.service, the ThinkPad eGPU."}}}
    out5 = FC.station_gaps(url_role, LIVE_CHART)
    check("a role carrying an endpoint URL is a station and is checked against its unit",
          len(names(out5, "auditor", "ollama-thinkpad.service")) == 1)

    # --- the module's existing behaviour, pinned so a rewrite fails here ------------------
    check("on_chart() still answers from a task's worker name",
          FC.on_chart("scripts/dispatch_loop.py",
                      {"tasks": [{"worker": "dispatch_loop.py"}]}) is True)
    check("  and still says no for a chart that does not name it",
          FC.on_chart("scripts/dispatch_loop.py",
                      {"tasks": [{"worker": "nas-backup.py"}]}) is False)
    check("uncovered() still answers from a library dict",
          FC.uncovered({"tasks": []}, {"scripts/lonely.py": {"kind": "tool"}})
          == ["scripts/lonely.py"])

    # --- the fixture must fire by itself: the CLI is what check_workspace.py runs ---------
    try:
        live_serving = json.loads(SERVING.read_text(encoding="utf-8"))
        live_flow = json.loads(FLOW.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        print("  [FAIL]   live SERVING.json / FLOW.json parse: %s" % e)
        fail += 1
        live_serving = live_flow = None

    if live_serving is not None and live_flow is not None:
        gaps = FC.station_gaps(live_serving, live_flow)
        run = subprocess.run([sys.executable, str(ROOT / "scripts" / "flow_coverage.py")],
                             capture_output=True, text=True, timeout=120)
        out6 = (run.stdout or "") + (run.stderr or "")
        silent = [g for g in gaps if str(g) not in out6]
        check("the CLI prints every station gap the function finds, verbatim (%d live gap(s))"
              % len(gaps), not silent)
        check("  and exits non-zero while a station has no chart node",
              (run.returncode != 0) if gaps else True)

    print("%d passed, %d failed" % (ok, fail))
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
