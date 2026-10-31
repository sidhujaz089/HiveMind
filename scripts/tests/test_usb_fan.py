'''t196: the USB dock-port fan switches by temperature, with the 10 C band Jas asked for.

Jas, 2026-10-05, on t196: 'If it hits 85C it turns on and stays on until the temp drops to
75C. Then stays off until it reaches 85C'. This file is the record of those numbers: on at
85.0, off at 75.0, one switch command per change of state, and a state that survives a
restart, because a service restart that forgets the fan was on re-triggers the dock port.

    python3 scripts/tests/test_usb_fan.py

It is RED against the stub in scripts/usb_fan.py, whose step() always returns off.
'''
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))

from usb_fan import OFF_C, ON_C, FanController


def _recorder():
    '''The switch callable the controller is built with, and the calls it recorded.'''
    calls = []

    def switch(port, state):
        calls.append((port, state))

    return switch, calls


def test_the_band_is_the_numbers_jas_gave():
    assert ON_C == 85.0, f'ON_C is {ON_C!r}; the fan switches on at 85.0 C'
    assert OFF_C == 75.0, f'OFF_C is {OFF_C!r}; the fan switches off at 75.0 C'


def test_it_stays_off_below_85_and_switches_on_at_85():
    switch, calls = _recorder()
    with tempfile.TemporaryDirectory() as d:
        fan = FanController(switch, Path(d) / 'state.json', port='1-2.4')
        assert fan.step(84.9) == 'off', '84.9 is below 85.0, so the fan must stay off'
        assert fan.step(85.0) == 'on', '85.0 is the threshold: this is the edge the task is about'
        assert calls == [('1-2.4', 'on')], f'one switch call, on the port built in: {calls!r}'


def test_it_holds_on_through_the_band_and_switches_off_at_75():
    switch, calls = _recorder()
    with tempfile.TemporaryDirectory() as d:
        fan = FanController(switch, Path(d) / 'state.json', port='3-1')
        assert fan.step(90.0) == 'on'
        assert fan.step(80.0) == 'on', '80.0 is inside the band: on at 85, off at 75, so it holds'
        assert fan.step(75.1) == 'on', '75.1 has not reached 75.0, so it must not switch off'
        assert fan.step(75.0) == 'off', '75.0 is the drop-to threshold'
        assert calls == [('3-1', 'on'), ('3-1', 'off')], f'one switch call per change of state: {calls!r}'


def test_no_switch_command_when_the_state_does_not_change():
    switch, calls = _recorder()
    with tempfile.TemporaryDirectory() as d:
        fan = FanController(switch, Path(d) / 'state.json', port='2-1.3')
        assert fan.step(20.0) == 'off'
        assert fan.step(50.0) == 'off'
        assert calls == [], f'a fan that is already off must not be commanded again: {calls!r}'


def test_the_state_survives_a_restart_because_it_is_written_to_disk():
    switch, calls = _recorder()
    with tempfile.TemporaryDirectory() as d:
        state = Path(d) / 'state.json'
        first = FanController(switch, state, port='4-1')
        assert first.step(86.0) == 'on'
        assert json.loads(state.read_text(encoding='utf-8'))['state'] == 'on'
        second = FanController(switch, state, port='4-1')
        assert second.state == 'on', f'a restarted controller must read the on state back: {second.state!r}'
        assert second.step(80.0) == 'on'
        assert second.step(74.0) == 'off'
        assert json.loads(state.read_text(encoding='utf-8'))['state'] == 'off'
        assert calls == [('4-1', 'on'), ('4-1', 'off')], f'a restart must not re-trigger the port: {calls!r}'


def test_a_state_file_that_is_not_json_starts_off_and_can_be_written_again():
    switch, calls = _recorder()
    with tempfile.TemporaryDirectory() as d:
        state = Path(d) / 'state.json'
        state.write_text('not json at all', encoding='utf-8')
        fan = FanController(switch, state, port='5-1')
        assert fan.state == 'off', 'an unreadable state file must not leave the fan on, and must not crash'
        assert fan.step(86.0) == 'on', 'the controller must still be able to switch on after a bad file'
        assert json.loads(state.read_text(encoding='utf-8'))['state'] == 'on'
        assert calls == [('5-1', 'on')]


def main():
    tests = [v for k, v in list(globals().items()) if k.startswith('test_') and callable(v)]
    failed = 0
    for t in tests:
        try:
            t()
            print(f'ok    {t.__name__}')
        except AssertionError as exc:
            failed += 1
            print(f'FAIL  {t.__name__}: {exc}')
        except Exception as exc:
            failed += 1
            print(f'ERROR {t.__name__}: {type(exc).__name__}: {exc}')
    print(f'{len(tests) - failed}/{len(tests)} passed')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
