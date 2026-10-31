#!/usr/bin/env python3
"""Truncation sweep, p11-cap-scopequeue: scripts/scope_queue.py:unscoped() must not cut content silently (input).

Jas, 2026-09-14: remove truncation on the coding side; a cap that must survive has to say what
it dropped. Checked at source level by loudcap_oracle.py -- see its docstring for why.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import loudcap_oracle  # noqa: E402

sys.exit(loudcap_oracle.run('scripts/scope_queue.py', 'unscoped', 'whole', ['s[:5000]']))
