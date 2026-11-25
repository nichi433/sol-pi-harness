#!/usr/bin/env python3
"""Standalone entrypoint for the `sol-pi-efficiency` skill.

Allows the skill to work even if copied standalone into `.agents/skills/sol-pi-efficiency/`
or executed directly from the repository without pip installation.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Locate `src/` whether running from the repository OR from an installed `.agents/skills/` copy
_SCRIPT_DIR = Path(__file__).resolve().parent
_CANDIDATE_SRCS = [
    _SCRIPT_DIR.parent.parent.parent / "src",
    _SCRIPT_DIR / "vendor",
]
for _cand in _CANDIDATE_SRCS:
    if _cand.is_dir() and str(_cand) not in sys.path:
        sys.path.insert(0, str(_cand))

from solpi.cli import main

if __name__ == "__main__":
    sys.exit(main())
