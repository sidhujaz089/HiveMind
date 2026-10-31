"""t1061: the standing-process census must prove it ran, keep a stable baseline, and
leave a trace of a control that came and went inside one window.

CAUSE (workspace/rca/814db2645e72.md, DE4/DE5): scripts/process_registry.py is change-driven.
record() writes a row ONLY when the census sees a diff, so workspace/process-changes.jsonl holds 42
rows and not one that says "I ran": a collector that died and a box that was quiet leave the same
evidence. Its baseline resets between shapes -- the rows at 2026-10-10T02:20/02:25/02:36Z remove
every user-scope unit and add every system-scope unit, the 02:36Z row carries 99 items on one side
of one diff, and the stored snapshot has no baseline marker at all. A control installed AND removed
between two ticks leaves no row.

No live state is read and nothing in the worktree is written: P.SNAP and P.LEDGER are redirected to
a temp dir before any call (the lesson recorded at scripts/tests/test_process_registry.py:13-17 --
coder's sandbox copies the working tree, so the live snapshot exists there and "the first snapshot"
is not a first one). No crontab, no systemctl, no model, no child process.
"""
import contextlib
import io
import json
import sys
import tempfile
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import process_registry as P

ok = 0
fail = 0


def check(name, cond):
    global ok, fail
    print(f"  [{'ok' if cond else 'FAIL'}]   {name}")
    ok += 1 if cond else 0
    fail += 0 if cond else 1


_TMP = Path(tempfile.mkdtemp())
P.SNAP = _TMP / "process-registry.json"
P.LEDGER = _TMP / "process-changes.jsonl"

# The shapes snapshot() really returns: {"cron": [line], "units": {"user:<name>": bool,
# "system:<name>": bool}}, and a chart in FLOW.json's real shape (services[].name, scripts[]).
# The values are deliberately all DIFFERENT (population 3, off-chart 1, three distinct scope
# dicts) so an implementation that hardcodes one value cannot satisfy every assertion.
FLOW = json.dumps({"services": [{"id": "svc", "name": "manager-queue.service"},
                                {"id": "dl", "name": "dispatch_loop.py"}],
                   "scripts": ["scripts/dispatch_loop.py"]})
POP = {"cron": ["*/10 * * * * python3 scripts/dispatch_loop.py"],
       "units": {"user:manager-queue.service": True, "user:timer-gemma.timer": False}}
POP2 = {"cron": POP["cron"], "units": dict(POP["units"], **{"user:chat-agent.timer": True})}
GONE = "cron: */15 * * * * bash scripts/timer-gemma.sh"
POP_ITEMS = list(POP["cron"]) + list(POP["units"])


def reset():
    for f in (P.SNAP, P.LEDGER):
        f.unlink(missing_ok=True)


def rows():
    if not P.LEDGER.exists():
        return []
    return [json.loads(l) for l in P.LEDGER.read_text(encoding="utf-8").splitlines() if l.strip()]


def test_liveness_row_on_every_tick():
    reset()
    P.record(snap=POP, prev=None, flow_text=FLOW, write=True)
    r = rows()
    lv = [x for x in r if x.get("writer") == "process_registry"]
    check("a tick with nothing to report records a liveness row, so 'no row in 26h' is interpretable",
          bool(lv))
    check("  and it carries the population it saw (1 cron line + 2 units = 3)",
          bool(lv) and lv[0]["population_size"] == 3)
    check("  and the real off-chart count of that population (the one timer not on the chart)",
          bool(lv) and lv[0]["off_chart_count"] == 1)
    check("  and its scope counts",
          bool(lv) and lv[0]["scope"] == {"cron": 1, "user": 2, "system": 0})
    check("  a first tick writes the baseline ONCE, marked with baseline_at",
          "baseline_at" in json.loads(P.SNAP.read_text(encoding="utf-8")))
    check("  and a first tick emits no add/remove rows (the mass removal of a whole baseline)",
          all("added" not in x and "removed" not in x for x in r))
    n = len(r)
    P.record(snap=POP, prev=json.loads(P.SNAP.read_text(encoding="utf-8")), flow_text=FLOW, write=True)
    r2 = rows()
    check("  a quiet tick appends a liveness row too -- on EVERY run, not only on diff",
          len(r2) == n + 1 and "population_size" in r2[-1])
    check("  and a read-only call writes no row at all (t772 pins this)",
          P.record(snap=POP, prev=POP, flow_text=FLOW, write=False) is None and len(rows()) == n + 1)


def test_dead_collector_leaves_evidence():
    reset()
    P.record(snap={"cron": [], "units": {}}, prev=POP, flow_text=FLOW, write=True)
    r = rows()
    check("a census that sees no units writes a liveness row with population_size 0",
          any(x.get("writer") == "process_registry" and x.get("population_size") == 0 for x in r))
    check("  and the empty population is not certified as an add/remove change",
          all("added" not in x and "removed" not in x for x in r))


def test_scope_shape_change_reseeds_instead_of_mass_rows():
    reset()
    prev_user = {"cron": [], "units": {"user:manager-queue.service": True, "user:timer-gemma.timer": True}}
    new_sys = {"cron": [], "units": {"system:ollama.service": False, "system:ssh.service": True,
                                     "system:docker.service": True}}
    P.record(snap=new_sys, prev=prev_user, flow_text=FLOW, write=True)
    r = rows()
    check("a scope-shape change emits no mass add row (the 99-item row at 2026-10-10T02:36Z)",
          all(len(x.get("added", [])) <= 1 for x in r))
    check("  and no mass remove row (the 50-item row at 2026-10-10T02:20Z)",
          all(len(x.get("removed", [])) <= 1 for x in r))
    check("  the census records that it re-seeded the baseline, and why",
          any(x.get("reseed") == "scope-shape" for x in r))
    check("  the re-seed row is scope-tagged with the new population's counts",
          any(x.get("reseed") == "scope-shape"
              and x.get("scope") == {"cron": 0, "user": 0, "system": 3} for x in r))
    check("  and every row this run wrote carries a scope tag",
          all(isinstance(x.get("scope"), dict) for x in r))
    reset()
    P.record(snap=POP2, prev=POP, flow_text=FLOW, write=True)
    r = rows()
    check("a real one-unit change is still reported as a change, not swallowed as a re-seed",
          any(x.get("added") == ["user:chat-agent.timer"] for x in r))
    check("  scope-tagged as one user-scope unit",
          any(x.get("added") == ["user:chat-agent.timer"]
              and x.get("scope") == {"cron": 0, "user": 1, "system": 0} for x in r))


def test_control_gone_within_one_window_leaves_a_trace():
    reset()
    P.record(snap=POP, prev=POP, flow_text=FLOW, write=True, seen=POP_ITEMS + [GONE])
    t = [x for x in rows() if x.get("transient")]
    check("a control seen and then not seen inside one window is recorded as transient", bool(t))
    check("  as BOTH the add and the removal of that exact control",
          bool(t) and t[0]["added"] == [GONE] and t[0]["removed"] == [GONE])
    check("  and scope-tagged as the cron control it is",
          bool(t) and t[0]["scope"] == {"cron": 1, "user": 0, "system": 0})
    reset()
    P.record(snap=POP, prev=POP, flow_text=FLOW, write=True, seen=POP_ITEMS)
    check("  a control that is still running is NOT reported transient",
          not any(x.get("transient") for x in rows()))


def test_reseed_flag_is_explicit_only():
    # record is stubbed so main() never takes a live census; the census lives in crontab/systemctl.
    captured = []
    orig = P.record

    def fake(*a, **kw):
        captured.append(kw)
        return None

    P.record = fake
    try:
        P.main([])
        plain = dict(captured[-1])
        P.main(["--reseed"])
        seeded = dict(captured[-1])
    finally:
        P.record = orig
    check("--reseed reaches record() as reseed=True", seeded.get("reseed") is True)
    check("  and a plain tick does not ask for a re-seed", plain.get("reseed") is False)


def test_record_contract_and_show_survive_the_liveness_row():
    # scripts/dispatch_loop.py prints ch['added'], ch['removed'], ch['off_chart'] from the return
    # value, so the return value must stay the change row and not the liveness row.
    reset()
    check("record() still returns None when nothing changed (scripts/tests/test_process_registry.py)",
          P.record(snap=POP, prev=POP, flow_text=FLOW, write=False) is None)
    got = P.record(snap=POP2, prev=POP, flow_text=FLOW, write=False)
    check("  and still returns the change row, added unchanged",
          bool(got) and got["added"] == ["user:chat-agent.timer"])
    check("  and the frozen suite's first-tick contract still holds",
          P.record(snap=POP2, prev=None, flow_text=FLOW, write=False) is None)
    reset()
    P.LEDGER.write_text(
        json.dumps({"at": "x", "added": ["user:chat-agent.timer"], "removed": [], "off_chart": [],
                    "scope": {"cron": 0, "user": 1, "system": 0}}) + "\n"
        + json.dumps({"at": "y", "writer": "process_registry", "population_size": 0,
                      "off_chart_count": 0, "scope": {"cron": 0, "user": 0, "system": 0}}) + "\n",
        encoding="utf-8")
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        P.main(["--show"])
    out = buf.getvalue()
    check("--show still prints a change row once liveness rows exist", "user:chat-agent.timer" in out)
    check("  and prints the census's liveness row", "population_size" in out)


def run_all():
    for fn in (test_liveness_row_on_every_tick, test_dead_collector_leaves_evidence,
               test_scope_shape_change_reseeds_instead_of_mass_rows,
               test_control_gone_within_one_window_leaves_a_trace,
               test_reseed_flag_is_explicit_only,
               test_record_contract_and_show_survive_the_liveness_row):
        print(fn.__name__)
        try:
            fn()
        except Exception as e:                                     # noqa: BLE001
            check(f"{fn.__name__} raised {type(e).__name__}: {e}", False)
            traceback.print_exc()


if __name__ == "__main__":
    run_all()
    print(f"\n{ok} passed, {fail} failed")
    sys.exit(1 if fail else 0)
