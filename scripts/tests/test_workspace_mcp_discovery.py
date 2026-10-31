#!/usr/bin/env python3
'''Fixtures for the MCP guard's discovery exemption and its 401 shape (t1388).

Single owner of two assertions nothing else in scripts/tests/ makes:

  * /.well-known/* is EXEMPT from the bearer check. A blanket 401 there makes
    OAuth discovery unreachable -- a client cannot learn how to authenticate --
    and every existing suite stays green. That is the surviving class the
    2026-10-17 session RCA recorded (workspace/rca/a1f344b5722b.md), and the
    reason the blanket 401 was deliberately kept: it is what makes the log show
    every path it tried.
  * every refusal carries the RFC 9728 WWW-Authenticate header pointing the
    client at the metadata. Without it a 401 is a dead end, not a flow.

The app is driven IN PROCESS with hand-built ASGI triples: no socket, no
subprocess, no model call, no live server, nothing left running. The OAuth store
is pointed at a temp dir and the Workspace is shimmed, so a run writes nothing
outside tempfile.

WHY THE STUBS BELOW. server.py imports the MCP SDK at module scope (line 23) and
builds a Workspace at import (line 32), so under the plain python3 the builder
uses (python3 scripts/tests/<suite>) a naive import of server crashes on 'No
module named mcp' -- red for the oracle's own bug, not for the change under test.
The mcp stub is inert: this fixture calls build_http_app with its OWN inner app,
so nothing on the real SDK is exercised, and the SDK is not what the guard is.
core.Workspace is replaced by a recorder before server is imported, which is what
keeps the run off live state and is also how the logging assertions are read.

RED ON MAIN for the missing change and nothing else.
'''

import asyncio
import json
import sys
import tempfile
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

LOGS = []


class _FakeMCPServer:
    def __init__(self, *a, **k):
        pass

    def __getattr__(self, name):
        def _registered(*a, **k):
            def _decorator(fn):
                return fn
            return _decorator
        return _registered


if 'mcp' not in sys.modules:
    _mcp = types.ModuleType('mcp')
    _pkg = types.ModuleType('mcp.server')
    _mcpserver = types.ModuleType('mcp.server.mcpserver')
    _mcpserver.MCPServer = _FakeMCPServer
    _ts = types.ModuleType('mcp.server.transport_security')

    class _TransportSecuritySettings:
        def __init__(self, *a, **k):
            pass

    _ts.TransportSecuritySettings = _TransportSecuritySettings
    _pkg.mcpserver = _mcpserver
    _pkg.transport_security = _ts
    _mcp.server = _pkg
    sys.modules['mcp'] = _mcp
    sys.modules['mcp.server'] = _pkg
    sys.modules['mcp.server.transport_security'] = _ts


class _RecordingWorkspace:
    '''Stands in for the live Workspace so this run writes no state anywhere.'''

    def __init__(self, *a, **k):
        pass

    def log(self, *a, **k):
        LOGS.append(a)


from workspace_mcp import core as _core  # noqa: E402
from workspace_mcp.oauth import Store  # noqa: E402

_core.Workspace = _RecordingWorkspace

from workspace_mcp import server  # noqa: E402

ISSUER = 'https://mcp.example.invalid'      # the real default, server.py:230
EXPECTED = 'admin-token-under-test'
CID = 'wsmcp_test'
SECRET = 'client-secret-under-test'
Q = chr(34)
WANT_HEADER = ('Bearer resource_metadata=' + Q + ISSUER +
               '/.well-known/oauth-protected-resource' + Q)

DISCOVERY = ('/.well-known/oauth-protected-resource',
             '/.well-known/oauth-protected-resource/mcp',
             '/.well-known/oauth-authorization-server',
             '/.well-known/oauth-authorization-server/mcp')

PASSED = FAILED = 0


def report(label, got, want):
    global PASSED, FAILED
    if got == want:
        PASSED += 1
        print(f'  [ok]   {label}')
    else:
        FAILED += 1
        print(f'  [BAD]  {label}: expected {want!r}, got {got!r}')


def store_in(tmp):
    path = Path(tmp) / 'tokens.json'
    try:
        store = Store(token_path=path)
    except TypeError:
        store = Store(path)
    report('the OAuth store writes inside the temp dir, not to live state',
           str(getattr(store, 'token_path', '')).startswith(tmp), True)
    return store


def new_app():
    calls = []

    async def inner(scope, receive, send):
        calls.append((scope['method'], scope['path']))
        await send({'type': 'http.response.start', 'status': 200,
                    'headers': [(b'content-type', b'application/json')]})
        await send({'type': 'http.response.body', 'body': b'{}'})

    tmp = str(Path(tempfile.mkdtemp()))
    app = server.build_http_app(inner,
                                expected=EXPECTED,
                                store=store_in(tmp),
                                issuer=ISSUER,
                                client={'client_id': CID, 'client_secret': SECRET})
    return app, calls


async def call(app, method, path, headers=None, body=b''):
    sent = []

    async def receive():
        return {'type': 'http.request', 'body': body, 'more_body': False}

    async def send(msg):
        sent.append(msg)

    scope = {'type': 'http',
             'asgi': {'version': '3.0'},
             'http_version': '1.1',
             'method': method,
             'scheme': 'https',
             'path': path,
             'raw_path': path.encode('latin-1'),
             'query_string': b'',
             'root_path': '',
             'headers': [(k.lower().encode('latin-1'), v.encode('latin-1'))
                         for k, v in (headers or {}).items()],
             'client': ('203.0.113.9', 51000),
             'server': ('mcp.example.invalid', 443)}
    await app(scope, receive, send)
    starts = [m for m in sent if m.get('type') == 'http.response.start']
    if not starts:
        return None, {}, b''
    raw = b''.join(m.get('body', b'') for m in sent if m.get('type') == 'http.response.body')
    hdrs = {k.decode('latin-1').lower(): v.decode('latin-1')
            for k, v in starts[0].get('headers', [])}
    return starts[0].get('status'), hdrs, raw


def test_discovery_paths_answer_without_a_token():
    app, calls = new_app()
    for path in DISCOVERY:
        status, hdrs, raw = asyncio.run(call(app, 'GET', path))
        report(f'{path} answers 200 with no token', status, 200)
        report(f'{path} is NOT refused with a 401', status == 401, False)
        report(f'{path} is not proxied through to the tool server', calls, [])
        report(f'{path} returns a JSON document', raw.startswith(b'{'), True)


def test_the_metadata_names_the_real_issuer():
    app, _ = new_app()
    status, hdrs, raw = asyncio.run(call(app, 'GET', '/.well-known/oauth-protected-resource'))
    prm = json.loads(raw)
    report('protected-resource authorization_servers is the issuer',
           prm.get('authorization_servers'), [ISSUER])
    report('protected-resource resource is the mcp endpoint under the issuer',
           str(prm.get('resource', '')).startswith(ISSUER + '/mcp'), True)
    status, hdrs, raw = asyncio.run(call(app, 'GET', '/.well-known/oauth-authorization-server'))
    asm = json.loads(raw)
    report('authorization-server token_endpoint is absolute under the issuer',
           str(asm.get('token_endpoint', '')).startswith(ISSUER + '/'), True)


def test_the_exemption_list_is_the_real_one():
    pub = set(server.PUBLIC_PATHS)
    for path in DISCOVERY:
        report(f'{path} is on the exemption list', path in pub, True)
    report('/authorize is exempt (the flow starts unauthenticated)', '/authorize' in pub, True)
    report('/token is exempt', '/token' in pub, True)
    report('/mcp is NOT exempt', '/mcp' in pub, False)
    report('/nope is NOT exempt', '/nope' in pub, False)


def test_guarded_paths_still_refuse():
    app, calls = new_app()
    status, hdrs, raw = asyncio.run(call(app, 'POST', '/mcp', body=b'{}'))
    report('an unauthenticated POST /mcp is 401', status, 401)
    report('and carries the RFC 9728 WWW-Authenticate header',
           hdrs.get('www-authenticate'), WANT_HEADER)
    report('and a refusal body', json.loads(raw), {'error': 'unauthorized'})
    report('the refused call never reaches the tool server', calls, [])

    status, hdrs, raw = asyncio.run(call(app, 'GET', '/mcp', {'authorization': 'Bearer nope'}))
    report('a bogus bearer is refused with 401', status, 401)
    report('a bogus bearer carries the same header', hdrs.get('www-authenticate'), WANT_HEADER)
    report('a bogus bearer never reaches the tool server', calls, [])

    status, hdrs, raw = asyncio.run(call(app, 'GET', '/nope'))
    report('an unknown path is not exempt', status, 401)
    report('an unknown path is not served by the tool server', calls, [])


def test_a_valid_token_reaches_the_served_app():
    app, calls = new_app()
    status, hdrs, raw = asyncio.run(
        call(app, 'POST', '/mcp', {'authorization': 'Bearer ' + EXPECTED}))
    report('a valid admin bearer is not refused', status, 200)
    report('and reaches the served app', calls, [('POST', '/mcp')])
    report('a pass-through carries no 401 header', 'www-authenticate' in hdrs, False)


def test_every_attempt_is_logged():
    del LOGS[:]
    app, _ = new_app()
    asyncio.run(call(app, 'POST', '/mcp'))
    asyncio.run(call(app, 'GET', '/.well-known/oauth-protected-resource'))
    refused = [a for a in LOGS if len(a) > 3 and a[0] == 'http-auth'
               and a[1] == '/mcp' and a[2] == 'refused']
    report('the unauthenticated /mcp probe is logged as a refusal', bool(refused), True)
    report('the refusal names the rule it broke',
           bool(refused) and isinstance(refused[0][3], str) and len(refused[0][3]) > 0, True)
    served = [a for a in LOGS if len(a) > 2 and a[0] == 'oauth'
              and a[1] == '/.well-known/oauth-protected-resource' and a[2] == 'ok']
    report('the discovery probe is logged too (the reason the blanket 401 was kept)',
           bool(served), True)


TESTS = (test_discovery_paths_answer_without_a_token,
         test_the_metadata_names_the_real_issuer,
         test_the_exemption_list_is_the_real_one,
         test_guarded_paths_still_refuse,
         test_a_valid_token_reaches_the_served_app,
         test_every_attempt_is_logged)

if __name__ == '__main__':
    print('t1388 -- MCP discovery exemption and the RFC 9728 401 shape')
    for _t in TESTS:
        try:
            _t()
        except Exception as _exc:  # a fixture that cannot run is a recorded failure, never a pass
            FAILED += 1
            print(f'  [BAD]  {_t.__name__} raised {type(_exc).__name__}: {_exc}')
    print(f'{PASSED} passed, {FAILED} failed')
    sys.exit(1 if FAILED else 0)
