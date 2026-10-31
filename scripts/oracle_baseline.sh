#!/bin/bash
# Capture (or compare) the VERDICT TITLES a tree's checks produce.
#
# An oracle that demands exit 0 from check_workspace.py is vacuous in a clone: it fails on
# PRISTINE code for environmental reasons -- a stale host service, dangling refs already in
# the history -- so it would be red before any work was done. That is precisely the mutate.py
# defect this sweep exists to fix, reproduced in the oracle written to catch it.
#
# What matters is not "does it pass" but "does it produce the SAME verdicts it did before".
#   capture <tree> <file>   record the baseline
#   compare <tree> <file>   exit 1 if any NEW failure or warning appeared
set -u
mode=$1; tree=$2; out=$3
collect() {
  cd "$1" || exit 2
  for s in check_apps check_deployed check_workspace; do
    timeout 400 python3 "scripts/$s.py" 2>&1 | grep -oE '^\s*\[(FAIL|WARN)\] .*' | sed 's/^ *//'
  done | sort -u
}
case "$mode" in
  capture) collect "$tree" > "$out"; echo "baseline: $(wc -l < "$out") verdict(s) -> $out" ;;
  compare) now=$(mktemp); collect "$tree" > "$now"
           # BOTH DIRECTIONS, and the second one is the one that matters.
           # The first version of this compared only for NEW verdicts. Breaking a check on
           # purpose -- check_apps.py's whatsNew test stubbed to `if False:` -- made it report
           # LESS, not more, so the warning silently vanished and this passed. A check that
           # only looks for additions cannot see a check that stopped working, which is
           # exactly how a broken check hides. Watched it fail to fail, 2026-09-01.
           new=$(comm -13 "$out" "$now")
           gone=$(comm -23 "$out" "$now")
           rc=0
           if [ -n "$new" ]; then
             echo "NEW failures/warnings not in the baseline:"; echo "$new"; rc=1
           fi
           if [ -n "$gone" ]; then
             echo "VERDICTS THAT DISAPPEARED -- a check may have been broken into silence."
             echo "If one was legitimately fixed, say which and why; do not ignore this."
             echo "$gone"; rc=1
           fi
           [ $rc -eq 0 ] && echo "verdicts unchanged from baseline"
           rm -f "$now"; exit $rc ;;
  *) echo "usage: $0 capture|compare <tree> <baseline-file>" >&2; exit 2 ;;
esac
