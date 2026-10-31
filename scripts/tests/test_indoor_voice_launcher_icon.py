#!/usr/bin/env python3
# t093 -- the indoor-voice launcher icon must be the shush emoji, not a microphone.
#
# The launcher icon is an adaptive icon: a background colour plus a foreground
# glyph, masked to the launcher shape. The foreground is one vector drawable,
# and it today draws a WHITE MICROPHONE -- a capsule (the mic body) plus a
# U-shaped stand, two <path> elements with these exact, DISTINCT signatures:
#
#   capsule (line 15): M12,14c1.66,0 3,-1.34 3,-3V5c0,-1.66 -1.34,-3 -3,-3S9,3.34 9,5v6c0,1.66 1.34,3 3,3z
#   stand   (line 18): M17,11c0,2.76 -2.24,5 -5,5s-5,-2.24 -5,-5H5c0,3.53 2.61,6.43 6,6.92V21h2v-3.08c3.39,-0.49 6,-3.39 6,-6.92h-2z
#
# The task (Jas, 2026-08-23) is to make the icon the shush emoji. The machine-
# checkable contract this oracle holds:
#   1. the adaptive-icon chain still RESOLVES -- the mipmap still references the
#      foreground drawable and the background colour, and that colour is still
#      defined (breaking the mipmap chain is the stated failure mode);
#   2. the foreground is still a NON-EMPTY filled <vector> glyph (replaced with
#      a real face, not blanked);
#   3. the MICROPHONE is GONE -- both of the two distinct signatures above are
#      absent (this is the missing change; red on main, green once the mic is
#      replaced).
#
# What this oracle does NOT hold: whether the glyph LOOKS like a shush face.
# That is a design judgment the task notes hand to Jas, and no test can pin it,
# so the structure and the chain are asserted here and a human reviews the built
# APK for the look.
#
# Pure stdlib, reads only, no model/server/subprocess/writes. Run with:
#   python3 scripts/tests/test_indoor_voice_launcher_icon.py

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FG = ROOT / 'projects' / 'indoor-voice' / 'app' / 'src' / 'main' / 'res' / 'drawable' / 'ic_launcher_foreground.xml'
MI = ROOT / 'projects' / 'indoor-voice' / 'app' / 'src' / 'main' / 'res' / 'mipmap-anydpi-v26' / 'ic_launcher.xml'
BG = ROOT / 'projects' / 'indoor-voice' / 'app' / 'src' / 'main' / 'res' / 'values' / 'ic_launcher_background.xml'

MIC_CAPSULE = 'M12,14c1.66,0 3,-1.34 3,-3V5c0,-1.66 -1.34,-3 -3,-3S9,3.34 9,5v6c0,1.66 1.34,3 3,3z'
MIC_STAND = 'M17,11c0,2.76 -2.24,5 -5,5s-5,-2.24 -5,-5H5c0,3.53 2.61,6.43 6,6.92V21h2v-3.08c3.39,-0.49 6,-3.39 6,-6.92h-2z'

failures = []


def check(cond, msg):
    if cond:
        print('  ok   ' + msg)
    else:
        print('  FAIL ' + msg)
        failures.append(msg)


def main():
    print('t093: indoor-voice launcher icon is the shush emoji, not a microphone')
    print('')
    for p in (FG, MI, BG):
        if not p.exists():
            print('  FAIL missing file: ' + str(p))
            return 1

    fg = FG.read_text(encoding='utf-8')
    mi = MI.read_text(encoding='utf-8')
    bg = BG.read_text(encoding='utf-8')

    check('<vector' in fg,
          'the foreground is still a vector drawable (the adaptive icon still has a glyph)')
    check('android:viewportWidth' in fg and 'android:viewportHeight' in fg,
          'the foreground vector keeps its viewport (it still masks on every launcher)')
    check('@drawable/ic_launcher_foreground' in mi,
          'the mipmap still references the foreground drawable (the chain is intact)')
    check('@color/ic_launcher_background' in mi,
          'the mipmap still references the background colour (the chain is intact)')
    check('ic_launcher_background' in bg,
          'the background colour is still defined (the other half of the chain resolves)')
    check('<path' in fg and 'android:fillColor' in fg,
          'the foreground still draws a non-empty filled glyph (replaced, not blanked)')
    check(MIC_CAPSULE not in fg,
          'the microphone capsule signature is GONE (the mic is no longer the icon)')
    check(MIC_STAND not in fg,
          'the microphone stand signature is GONE (the mic is no longer the icon)')

    print('')
    print(str(len(failures)) + ' failure(s)')
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
