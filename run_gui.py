#!/usr/bin/env python3
"""Launch the VEX Corpus Generator GUI."""

import sys
from pathlib import Path

# Ensure package is importable
sys.path.insert(0, str(Path(__file__).parent))

from gui.app import main

if __name__ == "__main__":
    main()
