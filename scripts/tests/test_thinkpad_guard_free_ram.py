#!/usr/bin/env python3
"""t1346: the ThinkPad guard must read the memory figure that INCLUDES the reclaimable cache.

RCA 48349dd2f155: free_ram_gb() (scripts/thinkpad_guard.py:69-75) asks Win32_OperatingSystem
FreePhysicalMemory, which excludes the standby/modified cache where Ollama model files sit --
the RCA counts 12 false RAM trips in workspace/thinkpad-guard.jsonl on 2026-10-16 at free
0.05-1.37 GB, unloading live models the machine could have kept.

free_ram_gb returns a GB float, and RESERVE_GB = 1.5 (scripts/thinkpad_guard.py:35) is compared
against it through decide() (line 91) and check() (lines 119-122). Each part breaks on its own,
so each part is pinned:
  * WHICH counter the remote command asks for -- Available MBytes, NOT FreePhysicalMemory;
  * the MB -> GB conversion: Available MBytes is a megabyte figure, and today the division is
    baked into the remote command (line 73, /1MB), so a counter swap that keeps the shape of
    the command returns 4096 where the guard expects 4.0 -- and 4096 < 1.5 is never true, so
    the guard quietly stops protecting the box. A name-only test scores that as a pass.
  * that the figure is USED: a genuinely low figure still trips the guard, in the same words,
    into the same ledger, and the SUSTAIN and hard-floor rules keep behaving as they do today.
  * that an unparseable remote answer yields None: today float(r.stdout.strip()) (line 75)
    raises on anything non-numeric, and Get-Counter prints text, not a bare number.

NOTHING HERE TOUCHES LIVE STATE. The first draft was refused oracle-dirty: its check() calls
reached scheduler_commands.pause_device / resume_device unstubbed, and the auto-resume path
(check() line 133-134, _resume_own_pause lines 148-167) unlinked the real
workspace/run-logs/device-paused-thinkpad.enabled. So: every device-pause flag is made to
resolve inside a temp dir (SC.PAUSE_FLAG is redirected, and _device_flag builds from
PAUSE_FLAG.parent); pause_device, resume_device and paused_devices are stubbed to records and
restored in a finally; G.LEDGER points into that temp dir; G.subprocess.run is stubbed so no
ssh is ever run; and a last assertion compares the existence of the REAL flag before and after.
"""
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
import scheduler_commands as SC  # noqa: E402
import thinkpad_guard as G  # noqa: E402

PASSED = FAILED = 0
GB = 1e9
ON_GPU = {'name': 'q4', 'size': 17.7 * GB, 'size_vram': 17.7 * GB}   # fully resident: no spill
URL = 'http://192.0.2.2:11434'          # never dialled: G.loaded is stubbed
SSH = ['ssh', '-i', '/keys/thinkpad', 'user@192.0.2.2']
LIVE_PAUSE_DIR = SC.PAUSE_FLAG.parent       # read before run() redirects the module flag root
UNLOADED = []
PAUSED = []
RESUMED = []


class Remote:
    def __init__(self, stdout, returncode=0):
        self.returncode, self.stdout, self.stderr = returncode, stdout, ''


def say(label, got, want):
    global PASSED, FAILED
    if got == want:
        PASSED += 1
        print(f'  [ok]   {label}')
    else:
        FAILED += 1
        print(f'  [BAD]  {label}: expected {want!r}, got {got!r}')


def remote_stub(stdout, returncode=0, sink=None):
    """A remote powershell that prints stdout, and a record of how it was called.

    The timeout= rule is asserted here rather than trusted: an ssh call with no timeout is an
    oracle-gate finding, and it is invisible in printed output.
    """
    def f(argv, **kw):
        global FAILED
        if sink is not None:
            sink.append(list(argv))
        if not isinstance(kw.get('timeout'), (int, float)) or kw['timeout'] <= 0:
            FAILED += 1
            print(f'  [BAD]  the ssh probe lost its timeout= ({kw.get("timeout")!r})')
        return Remote(stdout, returncode)
    return f


def pause_stub(device, who, why, **kw):
    PAUSED.append((device, who, why))


def resume_stub(device, **kw):
    RESUMED.append(('resume', device))


def rows():
    import json
    if not G.LEDGER.exists():
        return []
    return [json.loads(line) for line in G.LEDGER.read_text(encoding='utf-8').splitlines() if line.strip()]


def run():
    tmp = Path(tempfile.mkdtemp())
    saved = (G.subprocess.run, G.device, G.loaded, G.unload, G.LEDGER, G._low_since,
             SC.pause_device, SC.resume_device, SC.paused_devices, SC.PAUSE_FLAG)
    live_flag = LIVE_PAUSE_DIR / 'device-paused-thinkpad.enabled'
    flag_before = live_flag.exists()
    try:
        SC.PAUSE_FLAG = tmp / 'scheduler-paused.enabled'          # every device flag lands in tmp
        SC.pause_device, SC.resume_device, SC.paused_devices = pause_stub, resume_stub, lambda: {}
        G.LEDGER = tmp / 'thinkpad-guard.jsonl'
        G.device = lambda: (URL, SSH)
        G.loaded = lambda u: [dict(ON_GPU)]
        G.unload = lambda s, n: UNLOADED.append(n) or True
        say('every device-pause flag resolves inside the temp dir while this runs',
            str(SC.PAUSE_FLAG).startswith(str(tmp)), True)

        print('\nfree_ram_gb asks for a counter that includes the reclaimable cache:')
        calls = []
        G.subprocess.run = remote_stub('512', sink=calls)        # 512 MB available on the ThinkPad
        got = G.free_ram_gb(SSH)
        cmd = calls[-1][-1] if calls else ''
        say('free_ram_gb ran exactly one remote command', len(calls), 1)
        say('the ssh prefix is preserved: it reads the remote, never the GX10 itself',
            calls[-1][:-1] if calls else None, SSH)
        say('the remote is invoked as one command string after the prefix', len(calls[-1]), len(SSH) + 1)
        say('it asks for the Available MBytes counter', ('Available' in cmd) and ('MBytes' in cmd), True)
        say('it no longer reads FreePhysicalMemory', 'FreePhysicalMemory' in cmd, False)
        say('a megabyte figure comes back as GB (512 MB -> ~0.5 GB)',
            abs(got - 0.5) < 0.02 if isinstance(got, (int, float)) else f'not a number: {got!r}', True)

        print('\nthe existing None paths are kept (a missing figure pauses nothing silently):')
        G.subprocess.run = remote_stub('')
        say('a remote that prints nothing yields None', G.free_ram_gb(SSH), None)
        G.subprocess.run = remote_stub('512', returncode=1)
        say('a remote that exits non-zero yields None', G.free_ram_gb(SSH), None)
        G.subprocess.run = remote_stub('no counter found')
        try:
            answer = G.free_ram_gb(SSH)
        except Exception as e:                                  # noqa: BLE001 -- reported, not swallowed
            answer = f'crashed: {type(e).__name__}'
        say('a remote that answers something non-numeric yields None, not a crash', answer, None)
        say('no ssh configured yields None', G.free_ram_gb(None), None)

        print('\nthe figure is USED by the guard: real pressure still trips it:')
        G.subprocess.run = remote_stub('512')                   # 0.5 GB available: real pressure
        G._low_since = time.time() - G.SUSTAIN_S - 1           # a shortage that has lasted SUSTAIN_S
        acted = G.check()
        say('a 0.5 GB figure trips the guard (the GB conversion reaches the decision)',
            bool(acted.get('action')), True)
        say('the reported figure is the GB number, not a megabyte count',
            round(acted['free_ram_gb'], 2), 0.5)
        say('it unloads the model that is resident', list(UNLOADED), ['q4'])
        say('it pauses the ThinkPad, and nothing else', [p[0] for p in PAUSED], ['thinkpad'])
        why = PAUSED[0][2] if PAUSED else ''
        say('the reason names the measured figure in GB', '0.50' in why, True)
        say('the reason names the reserve it fell under', f'{G.RESERVE_GB} GB' in why, True)
        tripped = rows()
        say('the trip row went to the temp ledger, with the model and the reason',
            (len(tripped), tripped[0].get('unloaded'), tripped[0].get('why') == why)
            if tripped else (0, None, False), (1, ['q4'], True))

        print('\nthe false trip this task exists to stop: a cache-rich reading is NOT a trip:')
        UNLOADED[:], PAUSED[:], RESUMED[:], G._low_since = [], [], [], None
        G.subprocess.run = remote_stub('4096')                  # 4 GB available on a 15.7 GB box
        acted = G.check()
        say('4 GB available, model fully on the eGPU: nothing unloaded, nothing paused',
            (acted.get('action'), list(UNLOADED), list(PAUSED)), (None, [], []))
        say('the guard reads 4.0 GB, not 4096', round(acted['free_ram_gb'], 2), 4.0)

        print('\nthe 2026-10-14 rules are kept: a load passing through RAM is not a shortage:')
        UNLOADED[:], PAUSED[:], G._low_since = [], [], None
        G.subprocess.run = remote_stub('512')                   # under the reserve...
        G.check()                                               # ...on the first reading
        say('a dip shorter than SUSTAIN_S is not acted on', (list(UNLOADED), list(PAUSED)), ([], []))
        UNLOADED[:], PAUSED[:], G._low_since = [], [], None
        G.subprocess.run = remote_stub('100')                   # 100 MB: under the hard floor
        acted = G.check()
        say('a figure under the hard floor acts at once, without waiting SUSTAIN_S',
            (bool(acted.get('action')), list(UNLOADED)), (True, ['q4']))

        print('\nthe oracle leaves live state alone (the defect that got this scope sent back):')
        say('the guard never auto-resumed a device from inside this test',
            [r for r in RESUMED if r[0] == 'resume'], [])
        say('both trip rows went to the temp ledger, none to the live one', len(rows()), 2)
    finally:
        (G.subprocess.run, G.device, G.loaded, G.unload, G.LEDGER, G._low_since,
         SC.pause_device, SC.resume_device, SC.paused_devices, SC.PAUSE_FLAG) = saved
    say('running this oracle did not create or delete the real ThinkPad pause flag',
        live_flag.exists(), flag_before)
    print(f'\n{PASSED} passed, {FAILED} failed\n')
    return FAILED


if __name__ == '__main__':
    sys.exit(1 if run() else 0)
