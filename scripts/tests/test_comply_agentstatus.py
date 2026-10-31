#!/usr/bin/env python3
"""p24-comply-agentstatus: scripts/agent_status.py must pass the library check `ps-command-column`. Generated from the library audit."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import compliance_oracle  # noqa: E402
sys.exit(compliance_oracle.run('scripts/agent_status.py', 'ps-command-column'))
