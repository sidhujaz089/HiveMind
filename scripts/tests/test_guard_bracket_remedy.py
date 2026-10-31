#!/usr/bin/env python3
'''t1507 -- the process-wait guard must not recommend a DOUBLE bracket.

scripts/hooks/guard_process_waits.py builds the 'bracket the first character' remedy by
wrapping the first alphanumeric character of the pattern in [ ] with no check for the
bracket that is already there. Applied to a pattern whose first letter is already bracketed
-- the form the guard's own message shows -- that wraps the letter a SECOND time, and the
refusal tells the reader to apply a fix they just applied. Reported 2026-10-19; a refusal
that cannot be followed is a guard that gets switched off.

RED ON MAIN for the stated reason only: the module has no bracket_remedy function yet, and
the remedy it prints today double-brackets an already-bracketed first letter.
'''
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
HOOK = REPO / 'scripts' / 'hooks' / 'guard_process_waits.py'
sys.path.insert(0, str(REPO / 'scripts' / 'hooks'))

# The sample command is assembled from parts, in a directory that exists nowhere, so this
# file carries neither a real path nor a process-listing invocation of its own. The guard
# reads the string as data and never runs it, so all the string has to do is reach the
# branch under test: a listing stage, a filter on the bracketed form, and the bare form of
# the same path later in the same command line -- that bare form is what makes the guard's
# regex self-match, so the guard must still refuse.
PATTERN = '[s]cripts/auto_merge'
BARE = 'scripts/auto_merge'
WATCH_DIR = '/nonexistent-t1507/scratch'
LIST_FLAGS = '-eo'
LIST_COLUMNS = 'pid,etime,cmd'
PIPE = ' | '
CMD = ('cd ' + WATCH_DIR + ' && cat /dev/null; echo; '
       'ps ' + LIST_FLAGS + ' ' + LIST_COLUMNS + PIPE + 'grep ' + chr(39) + PATTERN + chr(39)
       + '; grep -n LOCK ' + BARE + '.py')

FAILED = 0


def check(label, got, want):
    global FAILED
    if got == want:
        print('  [ok]   ' + label + ' -> ' + repr(got))
    else:
        FAILED += 1
        print('  [BAD]  ' + label + ': expected ' + repr(want) + ', got ' + repr(got))


def run_hook(command):
    payload = json.dumps({'tool_name': 'Bash', 'tool_input': {'command': command}})
    with tempfile.TemporaryDirectory(prefix='t1507-') as workdir:
        proc = subprocess.run([sys.executable, str(HOOK)], input=payload, cwd=workdir,
                              capture_output=True, text=True, timeout=60)
    return proc.returncode, proc.stderr


def main():
    global FAILED
    import guard_process_waits as hook

    remedy = getattr(hook, 'bracket_remedy', None)
    if remedy is None:
        print('  [RED]  bracket_remedy is not defined in guard_process_waits -- the change '
              'this task asks for is missing')
        FAILED += 1
    else:
        # A BARE first letter is bracketed, exactly as the guard does today.
        check('a bare first letter is bracketed', remedy(BARE), '[s]cripts/auto_merge')
        check('a bare short name is bracketed', remedy('auto_merge'), '[a]uto_merge')
        # The defect: a first letter ALREADY bracketed is left as it stands.
        check('an already-bracketed pattern is not re-wrapped', remedy(PATTERN), PATTERN)
        check('an already-bracketed short name is not re-wrapped',
              remedy('[a]uto_merge'), '[a]uto_merge')

    # The value is USED, in the text the guard actually prints, and the DECISION is unchanged.
    code, err = run_hook(CMD)
    check('the guard still refuses a genuine self-match', code, 2)
    quoted = re.search('Bracket the first character -- ["](.*?)["]', err)
    check('the remedy it quotes is the pattern itself',
          quoted.group(1) if quoted else None, PATTERN)
    check('the quoted remedy is not wrapped a second time', '[[s]]' in err, False)

    print('')
    print('FAILED' if FAILED else 'ok')
    return 1 if FAILED else 0


if __name__ == '__main__':
    sys.exit(main())
