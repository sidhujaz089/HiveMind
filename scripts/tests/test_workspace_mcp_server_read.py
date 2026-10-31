#!/usr/bin/env python3
'''t680: the MCP read tool in scripts/workspace_mcp/server.py must EXPOSE and FORWARD the
range parameters start_line, end_line and tail_lines.

Why this suite parses the source with ast instead of importing server.py: server.py imports
the mcp SDK at module scope, and the builder runs a suite with plain python3, which does not
carry the venv copy. An import-based oracle would be red for the environment, not for the
change (that is the oracle-crash class). Reading the file decides the three things the task
asks for:
  1. the tool DESCRIPTION names start_line, end_line, tail_lines and says what they mean.
     The description is the only surface a tunnelled model sees before it chooses a call, so
     the text IS the behaviour here, not a proxy for it.
  2. the wrapper signature accepts them, path first, all three optional.
  3. the wrapper passes every one of them into the guarded call to ws.read, under its own
     name, so a range asked for over the tunnel reaches core.py instead of being dropped.

Reads one file and asserts. No subprocess, no network, no model call, nothing written.
'''
import ast
import sys
from pathlib import Path

SERVER = Path(__file__).resolve().parent.parent / 'workspace_mcp' / 'server.py'
NL = chr(10)
PASSED = 0
FAILED = 0


def show(v):
    s = repr(v)
    return s if len(s) <= 160 else s[:160] + '... (' + str(len(s)) + ' chars)'


def check(label, got, want):
    global PASSED, FAILED
    if got == want:
        PASSED += 1
        print('  [ok]   ' + label)
    else:
        FAILED += 1
        print('  [BAD]  ' + label + ': expected ' + show(want) + ', got ' + show(got))


def decorated_read(tree):
    '''The function named read that carries an @app.tool(...) decorator.'''
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == 'read':
            for d in node.decorator_list:
                if isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute) and d.func.attr == 'tool':
                    return node, d
    return None, None


print(NL + 'workspace_mcp/server.py -- the range parameters of the read tool (t680)' + NL)

src = SERVER.read_text(encoding='utf-8')
tree = ast.parse(src, filename=str(SERVER))
fn, deco = decorated_read(tree)
check('server.py defines a function named read carrying an @app.tool decorator',
      fn is not None and deco is not None, True)

if fn is not None and deco is not None:
    desc = ''
    for kw in deco.keywords:
        if kw.arg == 'description' and isinstance(kw.value, ast.Constant):
            desc = str(kw.value.value)
    # A model decides what to ask for from this string and nothing else.
    for word in ('start_line', 'end_line', 'tail_lines', '1-based', 'too large'):
        check('the tool description says ' + repr(word) + ' so a model can see the capability',
              word in desc, True)
    check('path stays the FIRST parameter (a no-range call passes it positionally)',
          [a.arg for a in fn.args.args], ['path', 'start_line', 'end_line', 'tail_lines'])
    defaults = list(fn.args.defaults)
    check('the three range parameters are optional, each defaulting to None',
          (len(defaults), all(isinstance(d, ast.Constant) and d.value is None for d in defaults)),
          (3, True))

    fwd = None
    for node in ast.walk(fn):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == '_guard':
            fwd = node
            break
    check('the wrapper routes the call through _guard, so a Denied still comes back as a refusal dict',
          fwd is not None, True)
    if fwd is not None:
        first = fwd.args[0] if fwd.args else None
        check('  the guarded call is ws.read',
              isinstance(first, ast.Attribute) and first.attr == 'read'
              and isinstance(first.value, ast.Name) and first.value.id == 'ws', True)
        for name in ('start_line', 'end_line', 'tail_lines'):
            got = None
            for kw in fwd.keywords:
                if kw.arg == name:
                    got = isinstance(kw.value, ast.Name) and kw.value.id == name
            check('  ' + name + ' is forwarded to ws.read under its own name', got, True)

print(str(PASSED) + ' passed, ' + str(FAILED) + ' failed')
sys.exit(1 if FAILED else 0)
