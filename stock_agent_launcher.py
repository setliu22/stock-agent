"""Stable launcher used by the generated macOS application."""

from __future__ import annotations

from pathlib import Path
import os
import sys


PROJECT_ROOT = Path(__file__).resolve().parent
os.chdir(PROJECT_ROOT)
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from gui import main


if __name__ == "__main__":
    main()
