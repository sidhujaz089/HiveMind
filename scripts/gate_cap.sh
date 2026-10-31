#!/bin/sh
# THE GATE'S CAP, IN ONE PLACE THE HOOK AND ITS SUITE BOTH RUN (2026-10-31).
#
# Claude sent the first version of this work back with two findings, both right:
#   * the hook called `gate_settings.py get`, WHICH DID NOT EXIST. argparse exited 2, `|| echo 1800` hid it, and the hook
#     never read Jas's setting at all -- with the setting at 3600 its own expression still captured 1800.
#   * the suite asserted the hook's TEXT ('echo 1800' in hook) instead of its behaviour: green while broken (L-028).
#
# So the resolution is a FILE, executed by the hook and by its suite, and a fallback is always SAID rather than hidden.
# A hook that cannot read its setting still bounds the gate -- it just no longer does so silently with a number Jas did
# not choose.
#
# Usage: `. gate_cap.sh [scripts-dir]`   -- the directory is passed in because a POSIX shell does not reliably give a
# sourced file its own path ($0 is the CALLER's), and a wrong path is exactly how this failed twice.
# Sets GATE_CAP.
# TWO WAYS IN, both needed and neither portable on its own:
#   * SOURCED by the hook (POSIX sh cannot give a sourced file its own path): $0 is the HOOK, so dirname/.. is scripts;
#   * EXECUTED by the suite: `sh scripts/gate_cap.sh <scripts-dir>`, where $1 is unambiguous.
if [ -n "${1:-}" ]; then _GC_DIR="$1"; else _GC_DIR="$(dirname "$0")/.."; fi
_GATE_TMP="${TMPDIR:-/tmp}/.gate-cap-$$"
_GATE_ERR="$(python3 "$_GC_DIR/gate_settings.py" get 2>&1 >"$_GATE_TMP")"
_GATE_RC=$?
GATE_CAP="$(cat "$_GATE_TMP" 2>/dev/null)"
rm -f "$_GATE_TMP"
if [ "$_GATE_RC" -ne 0 ] || [ -z "$GATE_CAP" ]; then
  [ -n "$_GATE_ERR" ] && printf '%s\n' "$_GATE_ERR" >&2
  [ -z "$GATE_CAP" ] && GATE_CAP=1800 && \
    printf '  NOTE: the gate timeout setting is unreadable -- using %s s (Jas'"'"'s setting was NOT applied)\n' "$GATE_CAP" >&2
fi
case "$GATE_CAP" in
  *[!0-9.]*|"") printf '  NOTE: the gate timeout setting is not a number (%s) -- using 1800 s\n' "$GATE_CAP" >&2; GATE_CAP=1800 ;;
esac

# WHEN EXECUTED, PRINT IT (a sourced caller reads $GATE_CAP; an executing caller needs the value). `return` fails only
# when the file is run rather than sourced, which is exactly the signal for this branch.
# No BASH_SOURCE here: this file is /bin/sh. Executed, $0 is this file's path; sourced, it is the hook's.
if [ "${0##*/}" = "gate_cap.sh" ]; then
  printf '%s' "$GATE_CAP"
fi
