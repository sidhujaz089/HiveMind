#!/usr/bin/env python3
"""t1596: a PAUSE MAY STOP WORK, NEVER THE ABILITY TO TALK TO A MODEL (Jas, 2026-10-24).

deploy/systemd/swift.service:3 carries `PartOf=production.target`. PartOf= is the systemd
directive that propagates a STOP from a target down to the unit that declares it, and every stop
path in the code stops that target: scripts/scheduler_commands.py:344-347 (a person's pause --
gx10_off calls _systemctl("stop") when who in INTERRUPTIONS), scripts/scheduler_commands.py:207-210
(the master breaker), and _systemctl at scripts/scheduler_commands.py:200-204 appends
PRODUCTION_TARGET to every argv. So today stopping work takes the model down as a side effect.
This suite pins that side effect out of the unit graph, so it cannot come back from the code side.

It reads the two real unit files. No systemctl, no socket, no model, no writes.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SWIFT = ROOT / "deploy" / "systemd" / "swift.service"
TARGET = ROOT / "deploy" / "systemd" / "production.target"
UNIT_DIR = ROOT / "deploy" / "systemd"

# Directives that, named on a unit, make stopping the named unit stop this one.
STOP_KEYS = ("PARTOF", "BOUNDBY", "REQUIREDBY")
# Directives that start a unit when the line starts, and do NOT stop it when the line stops.
START_KEYS = ("WANTS", "REQUIRES", "REQUISITE", "UPHOLDS")
MODEL = "swift.service"
LINE = "production.target"

failures = []


def check(name, ok, detail=""):
    tag = "PASS" if ok else "FAIL"
    print(f"{tag}  {name}" + (f"  -- {detail}" if detail and not ok else ""))
    if not ok:
        failures.append(name)


def directives(text):
    """{KEY: [tokens]} from a systemd unit file: 'Wants=a.service b.service' -> {'WANTS': [...]}.

    Comments are stripped first, so a commented-out directive cannot satisfy the rule by being
    text. Section headers carry no '=' and are skipped. Keys are upper-cased because systemd
    accepts any case and the unit files use two of them (PartOf=, Wants=).
    """
    out = {}
    for raw in text.splitlines():
        line = raw.split("#", 1)[0]
        key, sep, val = line.partition("=")
        if not sep:
            continue
        key = key.strip().upper()
        if not key:
            continue
        out.setdefault(key, []).extend(val.split())
    return out


def read(path):
    if not path.is_file():
        raise AssertionError(f"{path} is not a file -- the unit file moved; update this suite")
    text = path.read_text(encoding="utf-8")
    assert text.strip(), f"{path} is empty"
    return text


def test_swift_is_not_stop_bound_to_the_line():
    """RED ON MAIN: swift.service declares PartOf=production.target, so stopping the line stops
    the model. That is the side effect to remove."""
    d = directives(read(SWIFT))
    for key in STOP_KEYS:
        check(f"swift.service has no {key}= naming {LINE}",
              LINE not in d.get(key, []),
              f"{key}={d.get(key, [])}")


def test_the_line_still_starts_the_model():
    """The fix is NOT to drop the model from the line's start set: the target keeps
    Wants=swift.service, so starting the line still starts the model. Token-exact: a unit named
    inside a longer name does not count."""
    d = directives(read(TARGET))
    tokens = [t for k in START_KEYS for t in d.get(k, [])]
    check(f"production.target still starts {MODEL}", MODEL in tokens, f"start-set {tokens}")


def test_the_stop_binding_is_gone_from_every_swift_unit():
    """Inversion: no swift* unit file may stop-bind to the line, so a future swift-2.service
    cannot re-open the same route."""
    bad = []
    for p in sorted(UNIT_DIR.glob("swift*")):
        d = directives(p.read_text(encoding="utf-8"))
        for key in STOP_KEYS:
            if LINE in d.get(key, []):
                bad.append(f"{p.name}:{key}")
    check("no swift unit is stop-bound to production.target", not bad, ", ".join(bad))


def test_the_unit_still_serves_the_model():
    """Anti-vacuous: the fix is not to gut the unit. It must still serve the model on start and
    tear it down on stop."""
    d = directives(read(SWIFT))
    start = " ".join(d.get("EXECSTART", []))
    stop = " ".join(d.get("EXECSTOP", []))
    check("swift.service ExecStart still serves the model",
          "projects/local-inference/serve.py" in start and "start" in start,
          f"ExecStart={start!r}")
    check("swift.service ExecStop still tears it down",
          "projects/local-inference/serve.py" in stop and "stop" in stop,
          f"ExecStop={stop!r}")


def test_the_target_gains_no_new_stop_binding():
    """The fix is the removal in swift.service, not a stop-binding moved into the target."""
    d = directives(read(TARGET))
    bad = [k for k in STOP_KEYS if MODEL in d.get(k, [])]
    check(f"production.target declares no stop-binding to {MODEL}", not bad, ", ".join(bad))


if __name__ == "__main__":
    for fn in (test_swift_is_not_stop_bound_to_the_line,
               test_the_line_still_starts_the_model,
               test_the_stop_binding_is_gone_from_every_swift_unit,
               test_the_unit_still_serves_the_model,
               test_the_target_gains_no_new_stop_binding):
        try:
            fn()
        except Exception as e:
            # A raise never reads as a pass: print the type and the message, and count it.
            check(f"{fn.__name__} raised", False, f"{type(e).__name__}: {e}")
    print(f"{len(failures)} failed")
    sys.exit(1 if failures else 0)
