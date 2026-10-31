#!/usr/bin/env python3
"""p18-proc-hivestatus: scripts/hivestatus.py must read command lines from /proc, not `ps` (truncated at 80 columns)."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import procscan_oracle  # noqa: E402
sys.exit(procscan_oracle.run('scripts/hivestatus.py'))
