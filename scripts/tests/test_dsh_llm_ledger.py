#!/usr/bin/env python3
'''t1348 -- dsh-local-ai's resolved context/timeout/output cap must be RECORDED, not silently defaulted.

From workspace/rca/4a2be5701c9e.md and workspace/rca/5a17157d15ed.md: dsh-local-ai substitutes
contextWindow 8192, maxTokens 4096, requestTimeoutMs 30000 for any model with no catalog entry,
so a 262144-token model was served under an 8192 window and 24 days later the numbers had to be
reconstructed by hand from a zstd transcript. No endpoint records the resolved triple, so a
mis-set guard is invisible until it fires.

This is the fixture that fails while the writer is absent.

Contract it pins (to be added to scripts/session_start.py, appended as one contiguous block
after the definition of model_inventory_main and before the definition of claude_review_section).
Per this file's own recorded rule at lines 141-146, each definition imports everything it uses
INSIDE itself: the oracle runs each definition sliced out of this file, so a helper living
outside this block would not exist when it runs.

    DSH_LOCAL_AI_ROW_KEYS  -- the nine field names, in this order:
        ts, session, model, contextWindow_resolved, maxTokens_resolved,
        requestTimeoutMs_resolved, first_token_ms, idle_ms, outcome
    DSH_HARD_DEFAULTS      -- {"contextWindow": 8192, "maxTokens": 4096,
                               "requestTimeoutMs": 30000}
        (the adapter's built-in defaults; the ledger is what makes a row carrying them
        identifiable without hand-reconstruction, which is what the RCA could not do)
    parse_dsh_local_ai(profile_path=None)
        -> {"row": {"contextWindow": .., "maxTokens": .., "requestTimeoutMs": ..},
            "models": {name: {"contextWindow": .., "maxTokens": ..}},
            "source": str(path)}
        Reads the YAML patch layer and returns the entry whose id is dsh-local-ai. Row-level
        keys: requestTimeoutMs, maxTokens, and defaultContextWindow (reported as contextWindow).
        Model names come from config.models[].name AND config.backends[].models[].name. A
        model's contextWindow/maxTokens are the values THAT MODEL declares -- absent means
        None, never a copy of the row-level value. A missing or unparseable file is not an
        empty parse: it yields {"row": {}, "models": {}, "source": .., "error":
        "ExceptionType: message"}. Never raises.
    resolve_dsh_local_ai(parsed, model)
        -> a dict with EXACTLY the nine keys. A model that declares its own contextWindow and
        maxTokens gets outcome "declared"; a model present in the config with no values of its
        own falls back to the row-level values and gets outcome "row-default"; a model absent
        from the config is served DSH_HARD_DEFAULTS, gets outcome "silent-default", and carries
        a "finding" naming the model and all three numbers. A config that could not be read
        (parsed has "error") yields the resolved numbers as None with outcome "config-unreadable"
        and a "detail" carrying the error text.
    record_dsh_llm_ledger(parsed, model, ledger_path=None, session="", first_token_ms=None,
                           idle_ms=None, at=None)
        -> the row it wrote. Appends ONE jsonl row to ledger_path (default:
        workspace/run-logs/dsh-llm-requests.jsonl under the repository root), creating the
        parent directories. Never raises: a config that could not be read, and a write that
        failed, are both recorded as rows with the reason, not dropped.

Run: python3 scripts/tests/test_dsh_llm_ledger.py   (no pytest; exits 1 on a failure)
'''

import json
import sys
import tempfile
import traceback
from pathlib import Path

HERE = Path(__file__).resolve()
SCRIPTS_DIR = HERE.parents[1]          # scripts/  -- where session_start.py lives
REPO_ROOT = HERE.parents[2]            # repo root -- parents[2] from scripts/tests/
for _p in (str(SCRIPTS_DIR), str(REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import session_start  # noqa: E402

# Mirrors the real profile ~/.dsh/profiles/web/cordis.patch.yml read verbatim on 2026-10-20
# (43 lines): requestTimeoutMs 600000, maxTokens 32768, defaultContextWindow 262144, three
# ollama models declaring NO per-model caps, and a swift backend model declaring
# contextWindow 262144 / maxTokens 32768 of its own. A fixture invented from imagination would
# give every model its own numbers -- and would then pass against code that cannot tell a
# declared ceiling from a row default, which is the defect this task is about.
PROFILE_TEXT = "\n".join([
    '- id: ui-settings-general',
    '  name: "@deepseek-ai/dsh-client-ui-settings-general"',
    '  config:',
    '    welcomeNoticeVersion: 2026-08-13.1',
    '- id: dsh-local-ai',
    '  config:',
    '    requestTimeoutMs: 600000',
    '    maxTokens: 32768',
    '    defaultContextWindow: 262144',
    '    models:',
    '      - name: qwen3.8:27b-mtp-bf16',
    '      - name: gemma4:26b',
    '    backends:',
    '      - name: swift',
    '        baseURL: http://172.18.0.2:8000/v1',
    '        models:',
    '          - name: swift',
    '            contextWindow: 262144',
    '            maxTokens: 32768',
    '    route:',
    '      - model: qwen3.8:27b-mtp-bf16',
    '        keywords: ["local", "offline"]',
    '- id: agent-default-model',
    '  config:',
    '    provider: openai:swift',
    '    model: swift',
    '',
])


def _write_profile(td):
    prof = Path(td) / 'cordis.patch.yml'
    prof.write_text(PROFILE_TEXT, encoding='utf-8')
    return prof


def _rows(path):
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()
            if line.strip()]


def test_recorder_names_exist_on_the_session_start_surface():
    for name in ('DSH_LOCAL_AI_ROW_KEYS', 'DSH_HARD_DEFAULTS', 'parse_dsh_local_ai',
                 'resolve_dsh_local_ai', 'record_dsh_llm_ledger'):
        assert hasattr(session_start, name), (
            'scripts/session_start.py defines no %s -- the surface that records the resolved '
            'dsh-local-ai numbers does not exist (t1348)' % name)


def test_parse_reports_the_row_defaults_and_the_per_model_declarations_apart():
    with tempfile.TemporaryDirectory() as td:
        prof = _write_profile(td)
        parsed = session_start.parse_dsh_local_ai(prof)
        assert str(parsed['source']) == str(prof), parsed.get('source')
        assert parsed['row'] == {'contextWindow': 262144, 'maxTokens': 32768,
                                'requestTimeoutMs': 600000}, parsed['row']
        assert set(parsed['models']) == {'qwen3.8:27b-mtp-bf16', 'gemma4:26b', 'swift'}, parsed['models']
        # DISTINCT values: only swift declares its own. A parse that copies the row default
        # onto every model passes a substring check and fails this assertion.
        assert parsed['models']['swift']['contextWindow'] == 262144, parsed['models']['swift']
        assert parsed['models']['swift']['maxTokens'] == 32768, parsed['models']['swift']
        assert parsed['models']['qwen3.8:27b-mtp-bf16']['contextWindow'] is None, parsed['models']['qwen3.8:27b-mtp-bf16']
        assert parsed['models']['qwen3.8:27b-mtp-bf16']['maxTokens'] is None, parsed['models']['qwen3.8:27b-mtp-bf16']
        assert 'error' not in parsed, parsed


def test_resolve_separates_declared_row_default_and_silent_default():
    with tempfile.TemporaryDirectory() as td:
        prof = _write_profile(td)
        parsed = session_start.parse_dsh_local_ai(prof)

        declared = session_start.resolve_dsh_local_ai(parsed, 'swift')
        assert set(declared) == set(session_start.DSH_LOCAL_AI_ROW_KEYS), sorted(declared)
        assert declared['model'] == 'swift', declared
        assert declared['outcome'] == 'declared', declared
        assert declared['contextWindow_resolved'] == 262144, declared
        assert declared['maxTokens_resolved'] == 32768, declared
        assert declared['requestTimeoutMs_resolved'] == 600000, declared
        assert 'finding' not in declared, declared

        defaulted = session_start.resolve_dsh_local_ai(parsed, 'qwen3.8:27b-mtp-bf16')
        assert defaulted['outcome'] == 'row-default', defaulted
        assert defaulted['contextWindow_resolved'] == 262144, defaulted
        assert defaulted['maxTokens_resolved'] == 32768, defaulted
        assert defaulted['requestTimeoutMs_resolved'] == 600000, defaulted
        assert 'finding' not in defaulted, defaulted

        silent = session_start.resolve_dsh_local_ai(parsed, 'llama3.2:3b')
        assert set(silent) == set(session_start.DSH_LOCAL_AI_ROW_KEYS), sorted(silent)
        assert silent['outcome'] == 'silent-default', silent
        assert silent['contextWindow_resolved'] == 8192, silent
        assert silent['maxTokens_resolved'] == 4096, silent
        assert silent['requestTimeoutMs_resolved'] == 30000, silent
        # The refusal names the numbers -- the task's own wording, testable only here.
        finding = str(silent['finding'])
        assert 'llama3.2:3b' in finding, silent
        assert '8192' in finding and '4096' in finding and '30000' in finding, silent


def test_record_writes_all_nine_task_fields_and_creates_the_ledger():
    with tempfile.TemporaryDirectory() as td:
        prof = _write_profile(td)
        ledger = Path(td) / 'nested' / 'deeper' / 'dsh-llm-requests.jsonl'
        parsed = session_start.parse_dsh_local_ai(prof)
        row = session_start.record_dsh_llm_ledger(
            parsed, 'qwen3.8:27b-mtp-bf16', ledger_path=ledger, session='6ed72651',
            first_token_ms=41200, idle_ms=57000, at='2026-10-20T11:09:25Z')
        assert ledger.is_file(), 'the writer creates the ledger file: no pre-existing file is required'
        assert ledger.parent.is_dir(), 'the writer creates the parent directories'
        on_disk = _rows(ledger)
        assert len(on_disk) == 1, on_disk
        # The nine fields are checked on the row read BACK from disk, not on the return value:
        # a recorder that returns a complete dict and writes a thin row fails here.
        assert tuple(session_start.DSH_LOCAL_AI_ROW_KEYS) == (
            'ts', 'session', 'model', 'contextWindow_resolved', 'maxTokens_resolved',
            'requestTimeoutMs_resolved', 'first_token_ms', 'idle_ms', 'outcome'), session_start.DSH_LOCAL_AI_ROW_KEYS
        assert set(on_disk[0]) == set(session_start.DSH_LOCAL_AI_ROW_KEYS), sorted(on_disk[0])
        assert on_disk[0]['model'] == 'qwen3.8:27b-mtp-bf16', on_disk[0]
        assert on_disk[0]['session'] == '6ed72651', on_disk[0]
        assert on_disk[0]['ts'] == '2026-10-20T11:09:25Z', on_disk[0]
        assert on_disk[0]['contextWindow_resolved'] == 262144, on_disk[0]
        assert on_disk[0]['maxTokens_resolved'] == 32768, on_disk[0]
        assert on_disk[0]['requestTimeoutMs_resolved'] == 600000, on_disk[0]
        assert on_disk[0]['first_token_ms'] == 41200, on_disk[0]
        assert on_disk[0]['idle_ms'] == 57000, on_disk[0]
        assert on_disk[0]['outcome'] == 'row-default', on_disk[0]
        assert row == on_disk[0], (row, on_disk[0])


def test_record_appends_and_never_writes_a_silent_default_as_a_healthy_row():
    with tempfile.TemporaryDirectory() as td:
        prof = _write_profile(td)
        ledger = Path(td) / 'dsh-llm-requests.jsonl'
        parsed = session_start.parse_dsh_local_ai(prof)
        session_start.record_dsh_llm_ledger(parsed, 'swift', ledger_path=ledger,
                                            session='s1', at='2026-10-20T11:09:25Z')
        session_start.record_dsh_llm_ledger(parsed, 'llama3.2:3b', ledger_path=ledger,
                                            session='s1', at='2026-10-20T11:09:26Z')
        rows = _rows(ledger)
        assert len(rows) == 2, rows
        assert rows[0]['model'] == 'swift' and rows[0]['outcome'] == 'declared', rows[0]
        silent = rows[1]
        assert silent['model'] == 'llama3.2:3b', silent
        assert silent['outcome'] == 'silent-default', silent
        assert silent['outcome'] != 'ok', silent
        assert silent['contextWindow_resolved'] == 8192, silent
        assert silent['maxTokens_resolved'] == 4096, silent
        assert silent['requestTimeoutMs_resolved'] == 30000, silent
        assert all(n in str(silent['finding']) for n in ('8192', '4096', '30000')), silent
        assert tuple(session_start.DSH_HARD_DEFAULTS[k] for k in
                     ('contextWindow', 'maxTokens', 'requestTimeoutMs')) == (8192, 4096, 30000), session_start.DSH_HARD_DEFAULTS


def test_unreadable_config_is_recorded_as_a_finding_not_as_nothing():
    with tempfile.TemporaryDirectory() as td:
        ledger = Path(td) / 'dsh-llm-requests.jsonl'
        missing = Path(td) / 'absent' / 'cordis.patch.yml'
        parsed = session_start.parse_dsh_local_ai(missing)
        assert parsed['row'] == {}, parsed
        assert parsed['models'] == {}, parsed
        assert parsed.get('error'), parsed
        row = session_start.record_dsh_llm_ledger(parsed, 'qwen3.8:27b-mtp-bf16',
                                                  ledger_path=ledger, session='s2',
                                                  at='2026-10-20T11:09:27Z')
        assert row['outcome'] == 'config-unreadable', row
        # A swallowed exception records WHAT it caught (swallowed-exception-introduced).
        assert str(row.get('detail', '')).strip(), row
        rows = _rows(ledger)
        assert len(rows) == 1, rows
        assert rows[0]['outcome'] == 'config-unreadable', rows[0]
        # A row written from an unreadable config must not read as a resolved ceiling.
        assert rows[0]['contextWindow_resolved'] is None, rows[0]
        assert rows[0]['maxTokens_resolved'] is None, rows[0]
        assert rows[0]['requestTimeoutMs_resolved'] is None, rows[0]


if __name__ == '__main__':
    _tests = [v for k, v in sorted(globals().items())
              if k.startswith('test_') and callable(v) and getattr(v, '__module__', None) == '__main__']
    _failed = 0
    for _fn in _tests:
        try:
            _fn()
            print('ok   %s' % _fn.__name__)
        except Exception:
            _failed += 1
            print('FAIL %s' % _fn.__name__)
            traceback.print_exc()
    print('%d test(s), %d failure(s)' % (len(_tests), _failed))
    sys.exit(1 if _failed else 0)
