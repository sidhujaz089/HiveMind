#!/usr/bin/env python3
# t1210 oracle: a REGISTRY entry that names a server must also record who serves it, and for whom.
#
# The finding (workspace/rca/26af33ee7a24.md, filed as
# workspace/tasks/t1210--serve-embeddinggemma-2-small-server-a-consumer-a.md): 'not_served' is a
# terminal record state with no exit. REGISTRY.json declares models as approved and names a server
# for six of them, and no field says who serves them or what consumes them -- so six days passed
# with nothing able to act on the entry, and no fixture on the books could see the gap.
#
# This is the data-contract half: the six base_models entries that already carry a server must
# carry served_by (equal to that entry's own server value) and consumer (that entry's own role
# text). The expectations are COMPUTED from the file at test time -- nothing is stored -- so a
# role text that changes moves the expectation with it.
#
# RED ON MAIN, for the stated reason: read in full (101 lines), none of the nine entries has
# served_by or consumer, so tests 2 and 3 fail naming exactly that. Tests 1, 4 and 5 pass today
# on purpose: they are guards that keep a build from satisfying the contract by deleting entries
# (test 1) or by inventing an endpoint for a model that has no server (test 4).
#
# Run: python3 scripts/tests/test_registry_served_by.py   (no pytest; runner at the bottom)
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REGISTRY = ROOT / 'projects' / 'local-inference' / 'models' / 'REGISTRY.json'

# The base_models entries that name a server TODAY (REGISTRY.json:26, :36, :45, :64, :73, :82).
SERVED_TAGS = [
    'gemma4:26b',
    'qwen3-embedding:0.6b',
    'swift',
    'qwen3.8:27b-mtp-q4_K_M-56k',
    'swift-junior:latest',
    'gemma4:e4b',
]
# The entries with NO server today. Out of scope for this contract: what they say is the open
# serve-vs-park decision. A builder writing one here would be inventing an endpoint.
UNSERVED_TAGS = ['qwen3.8:27b-mtp-bf16', 'qwen3.8:27b-mtp-q4_K_M']
NOT_SERVED_TAGS = ['embeddinggemma-2']

FAILURES = []


def _registry():
    assert REGISTRY.is_file(), f'{REGISTRY} is missing'
    return json.loads(REGISTRY.read_text(encoding='utf-8'))


def _entries(reg):
    out = {}
    for section in ('base_models', 'not_served'):
        for entry in reg.get(section, []):
            out[entry.get('tag')] = entry
    return out


def test_the_registry_still_declares_the_models_it_declares_today():
    # A build must not satisfy the contract by deleting the entries that make it red.
    reg = _registry()
    entries = _entries(reg)
    for tag in SERVED_TAGS + UNSERVED_TAGS + NOT_SERVED_TAGS:
        assert tag in entries, f'the entry for {tag!r} must still be declared -- do not delete it'
    assert len(reg.get('base_models', [])) == 8, (
        'base_models must still hold 8 entries: ' + str([e.get('tag') for e in reg.get('base_models', [])]))
    assert len(reg.get('not_served', [])) == 1, (
        'not_served must still hold 1 entry: ' + str([e.get('tag') for e in reg.get('not_served', [])]))
    assert reg.get('aliases', []) == [], 'this change adds no aliases'


def test_a_served_entry_records_who_serves_it():
    # The contract, on the six entries that already name a server: served_by is present, is a
    # non-empty string, and EQUALS that entry's server -- the exact value, not a mention of it.
    entries = _entries(_registry())
    for tag in SERVED_TAGS:
        entry = entries[tag]
        server = entry.get('server')
        assert server, f'fixture drift: {tag!r} no longer carries a server -- update this suite'
        served_by = entry.get('served_by')
        assert isinstance(served_by, str) and served_by.strip(), (
            f'{tag!r} names server={server!r} but has no served_by -- the gap t1210 exists for')
        assert served_by == server, (
            f'{tag!r}: served_by must equal its server value {server!r}, got {served_by!r}')


def test_a_served_entry_records_what_consumes_it():
    # consumer is present, non-empty, and EQUALS the entry's own role text -- the consumer named
    # in the file, not a phrase invented beside it. Each entry's expected value is distinct.
    entries = _entries(_registry())
    for tag in SERVED_TAGS:
        entry = entries[tag]
        role = entry.get('role')
        assert role, f'fixture drift: {tag!r} no longer carries a role -- update this suite'
        consumer = entry.get('consumer')
        assert isinstance(consumer, str) and consumer.strip(), (
            f'{tag!r} has a role {role!r} but no consumer -- the gap t1210 exists for')
        assert consumer == role, (
            f'{tag!r}: consumer must equal its role text {role!r}, got {consumer!r}')


def test_the_entries_with_no_server_are_not_given_an_invented_endpoint():
    # NEGATIVE assertion, green today by design. The three entries that carry no server stay
    # that way in this scope: their disposition is an open decision, not a string to invent.
    entries = _entries(_registry())
    for tag in UNSERVED_TAGS + NOT_SERVED_TAGS:
        entry = entries[tag]
        assert 'served_by' not in entry, (
            f'{tag!r} has no server in REGISTRY.json -- do not invent one; that decision is open')
        assert 'server' not in entry, f'{tag!r} must not gain a server in this scope'


def test_the_registry_is_valid_json_after_the_change():
    reg = _registry()
    assert isinstance(reg, dict) and 'base_models' in reg and 'not_served' in reg, (
        'the registry top-level shape must survive the edit')


if __name__ == '__main__':
    _tests = [
        test_the_registry_still_declares_the_models_it_declares_today,
        test_a_served_entry_records_who_serves_it,
        test_a_served_entry_records_what_consumes_it,
        test_the_entries_with_no_server_are_not_given_an_invented_endpoint,
        test_the_registry_is_valid_json_after_the_change,
    ]
    for _t in _tests:
        try:
            _t()
            print(f'PASS {_t.__name__}')
        except AssertionError as e:
            FAILURES.append(f'{_t.__name__}: {e}')
            print(f'FAIL {_t.__name__}: {e}')
    print(f'{len(_tests) - len(FAILURES)}/{len(_tests)} passed')
    sys.exit(1 if FAILURES else 0)
