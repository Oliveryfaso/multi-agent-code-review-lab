"""Compatibility entry for the project's explicit mock-only recovery demo."""
from pathlib import Path
import sys

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "src"))
from macr.investigation.mock_recovery import main

if __name__ == "__main__":
    raise SystemExit(main())
