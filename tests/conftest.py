"""Make the package sources importable without installing the .deb."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "packages" / "dahlia-storage" / "usr/lib/python3/dist-packages"))
sys.path.insert(0, str(ROOT / "packages" / "dahlia-agent" / "usr/lib/python3/dist-packages"))
