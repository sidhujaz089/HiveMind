#!/usr/bin/env python3
"""p22-comply-chatui: projects/local-inference/chatui.py must pass the library check `process-match-by-name`. Generated from the library audit."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import compliance_oracle  # noqa: E402
sys.exit(compliance_oracle.run('projects/local-inference/chatui.py', 'process-match-by-name'))
