"""Make the package sources importable without installing the .deb."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "packages" / "storage" / "usr/lib/python3/dist-packages"))
sys.path.insert(0, str(ROOT / "packages" / "agent" / "usr/lib/python3/dist-packages"))
sys.path.insert(0, str(ROOT / "packages" / "dashboard" / "usr/lib/python3/dist-packages"))
