#!/usr/bin/env python3
"""t1508: swift_pending_gib reads the TAIL of memory-watch.jsonl, not the whole file.

Measured 2026-10-20: workspace/memory-watch.jsonl is 186,229,729 bytes on 48,690 lines (one 4 KB
row every 20 s, growing all day). scripts/dispatch_loop.py line 1898 (def at 1873) does

    last = (watch or (REPO / "workspace" / "memory-watch.jsonl")).read_text(...).splitlines()[-1]

-- it decodes all 180 MB to use one 4 KB row, every tick, from two call sites (:1947 via
memory_admit, :3363 in the tick's no-build-lane check). The observable result of a tail read is
the same number, so this suite bounds the BYTES READ, twice, and pins the branches the fix must
not disturb.

Red on main for the missing change, not for its own bug: test_tail_is_read_not_the_whole_file.
The other three pass on main by design -- they are the regression floor (last row charged, 120 s
staleness, unread-file NOTE). No model, no live service, no fixed /tmp, nothing left running.
"""
import builtins
import contextlib
import io
import json
import os
import sys
import tempfile
import time
import types
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))
import dispatch_loop as d  # noqa: E402

PEAK = 20.0                 # fake serve.need_gib()
RESERVE = 2.0               # fake serve.RESERVE_GIB -- distinct from PEAK on purpose
NET = PEAK - RESERVE        # 18.0: what swift_pending_gib charges Swift's peak at
PAD_TO = 200
N_ROWS = 42000              # 42000 * 201 bytes = 8.44 MB > 8 MiB: a whole-file read cannot clear the 2 MiB bound
READ_LIMIT = 2 * 1024 * 1024
NOW = 1760000000.0          # a fixed epoch: `now` is a parameter, nothing live is read

fails = []


def _stamp(ts):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


def _row(ts, swift_gb):
    """One row, in the format scripts/dispatch_loop.py:1901 parses, padded to a realistic 201 B."""
    s = json.dumps({"at": _stamp(ts),
                    "groups": {"swift": {"total_gb": swift_gb},
                               "build": {"total_gb": 1.0},
                               "services": {"total_gb": 2.0}}})
    return (s + " " * max(0, PAD_TO - len(s)))[:PAD_TO]


def _fake_serve():
    m = types.ModuleType("serve")
    m.need_gib = lambda: PEAK
    m.RESERVE_GIB = RESERVE
    return m


class _Counter:
    """Delegates to a real file object, counting every byte handed back to the reader."""

    def __init__(self, f):
        self._f = f
        self.n = 0

    def __getattr__(self, k):
        return getattr(self._f, k)

    def read(self, *a, **k):
        v = self._f.read(*a, **k)
        self.n += len(v)
        return v

    def readline(self, *a, **k):
        v = self._f.readline(*a, **k)
        self.n += len(v)
        return v

    def readlines(self, *a, **k):
        v = self._f.readlines(*a, **k)
        self.n += sum(len(x) for x in v)
        return v

    def readinto(self, b):
        n = self._f.readinto(b)
        self.n += max(0, n or 0)
        return n

    def __iter__(self):
        return self

    def __next__(self):
        v = next(self._f)
        self.n += len(v)
        return v

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return self._f.__exit__(*a)


class _NoWholeFile(type(Path())):
    """read_text()/read_bytes() ON THE WATCH FILE ARE THE DEFECT: 180 MB decoded for one row."""

    def read_text(self, *a, **k):
        raise AssertionError("read_text() on the watch file reads all 180 MB for one line (t1508)")

    def read_bytes(self, *a, **k):
        raise AssertionError("read_bytes() on the watch file reads all 180 MB for one line (t1508)")


def _stub_run(log=None):
    def run(cmd, *a, **k):
        if log is not None:
            log.append((list(map(str, cmd)), dict(k)))

        class _R:
            stdout = "active\n"
        return _R()
    return run


def _call(watch, now, counters=None, runlog=None):
    """swift_pending_gib with no live state: systemctl stubbed, serve faked, open counted."""
    saved_run, saved_open = d.subprocess.run, builtins.open
    prev_serve = sys.modules.get("serve")
    sys.modules["serve"] = _fake_serve()
    d.subprocess.run = _stub_run(runlog)
    real = os.path.abspath(str(watch))

    def fake_open(file, *a, **k):
        f = saved_open(file, *a, **k)
        if os.path.isabs(str(file)) and os.path.abspath(str(file)) == real:
            f = _Counter(f)
            if counters is not None:
                counters.append(f)
        return f

    if counters is not None:
        builtins.open = fake_open
    try:
        return d.swift_pending_gib(watch=watch, now=now)
    finally:
        d.subprocess.run = saved_run
        builtins.open = saved_open
        if prev_serve is None:
            sys.modules.pop("serve", None)
        else:
            sys.modules["serve"] = prev_serve


def test_last_row_is_the_one_charged():
    """The value read from the LAST row is what gets subtracted -- three distinct totals."""
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "watch.jsonl"
        p.write_text("".join(_row(NOW + 20 * i, gb) + "\n"
                             for i, gb in enumerate((1.0, 2.0, 8.0))), encoding="utf-8")
        got = _call(p, NOW + 40)          # last row is the fresh one at NOW+40, swift 8.0 GB
        if abs(got - (NET - 8.0)) > 1e-9:
            fails.append(f"the last row's swift total must be charged: want {NET - 8.0}, got {got} "
                         f"(17.0 would mean it read the FIRST row, 18.0 that it read none)")


def test_stale_row_charges_whole_footprint():
    """A row older than 120 s is NOT trusted: Swift's whole peak is charged, and it is SAID."""
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "watch.jsonl"
        p.write_text(_row(NOW - 3600, 8.0) + "\n", encoding="utf-8")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            got = _call(p, NOW)
        if abs(got - NET) > 1e-9:
            fails.append(f"a stale row must charge Swift's whole footprint {NET}, got {got}")
        if "stale" not in out.getvalue():
            fails.append(f"the stale branch must say so (dispatch_loop.py:1905); printed {out.getvalue()!r}")


def test_unreadable_watch_is_said_and_charges_peak():
    """A missing/unreadable ledger must be SAID, never returned as a silent 0.0."""
    with tempfile.TemporaryDirectory() as td:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            got = _call(Path(td) / "absent.jsonl", NOW)
        if abs(got - NET) > 1e-9:
            fails.append(f"an unreadable ledger must charge the whole peak {NET}, got {got}")
        if "memory-watch" not in out.getvalue():
            fails.append(f"the unread branch must print the NOTE (dispatch_loop.py:1907); printed {out.getvalue()!r}")


def test_tail_is_read_not_the_whole_file():
    """THE CHANGE. An 8 MB ledger: a whole-file read is >= 8,000,000 bytes; a tail read is KBs."""
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "watch.jsonl"
        with open(p, "w", encoding="utf-8") as f:
            for i in range(N_ROWS):
                f.write(_row(NOW - 20 * (N_ROWS - i), 0.0) + "\n")
            f.write(_row(NOW, 8.0) + "\n")   # the one row the function uses
        size = os.path.getsize(str(p))
        if size < 8 * 1024 * 1024:
            fails.append(f"fixture is {size} bytes, under 8 MB -- the 2 MiB bound proves nothing")
        counters = []
        got = _call(p, NOW, counters=counters)
        read = sum(c.n for c in counters)
        if abs(got - (NET - 8.0)) > 1e-9:
            fails.append(f"the tail row must still be parsed and charged: want {NET - 8.0}, got {got}")
        if read > READ_LIMIT:
            fails.append(f"read {read} bytes of an {size}-byte ledger; a tail read must stay under "
                         f"{READ_LIMIT} (t1508: memory-watch.jsonl is 186,229,729 bytes read every tick)")
        # And the method that reads everything is forbidden by name, so the bound cannot be
        # satisfied by read_text() with a smaller constant elsewhere in the function.
        try:
            got2 = _call(_NoWholeFile(str(p)), NOW)
        except AssertionError as e:
            fails.append(f"the whole-file read is still on this path: {e}")
        else:
            if abs(got2 - (NET - 8.0)) > 1e-9:
                fails.append(f"via the Path subclass the last row must still be charged: got {got2}")


def test_the_live_query_keeps_its_timeout():
    """The systemctl probe is a subprocess: an oracle rule the fix must not drop (line 1883)."""
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "watch.jsonl"
        p.write_text(_row(NOW, 8.0) + "\n", encoding="utf-8")
        log = []
        _call(p, NOW, runlog=log)
        if not log:
            fails.append("swift_pending_gib made no subprocess call -- the service check was removed")
        for cmd, kw in log:
            if any("swift" in c for c in cmd) and not kw.get("timeout"):
                fails.append(f"subprocess.run at dispatch_loop.py:1883 must keep timeout=; {cmd} has {kw}")


if __name__ == "__main__":
    for fn in (test_last_row_is_the_one_charged, test_stale_row_charges_whole_footprint,
               test_unreadable_watch_is_said_and_charges_peak, test_tail_is_read_not_the_whole_file,
               test_the_live_query_keeps_its_timeout):
        fn()
    for f in fails:
        print("FAIL:", f)
    print("test_dispatch_loop_watch_tail:", "FAILED" if fails else "ok")
    sys.exit(1 if fails else 0)
