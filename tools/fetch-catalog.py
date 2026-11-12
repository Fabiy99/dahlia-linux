#!/usr/bin/python3
"""Download the app catalog for the agent package (tools/build-packages.sh).

Usage: tools/fetch-catalog.py <catalog URL> <target dir>

Every template is checked as by `apps catalog --refresh`. The target gets the
same layout as /var/lib/dahlia/apps-catalog (templates, icons/, meta.json);
agent's postinst copies it there with `apps catalog --seed`.
"""

import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "packages/agent/usr/lib/python3/dist-packages"))

from dahlia_agent import apps  # noqa: E402


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__.strip().splitlines()[2], file=sys.stderr)
        return 2
    url, target = argv[0], Path(argv[1])
    with tempfile.TemporaryDirectory() as tmp:
        config = Path(tmp, "apps.json")
        config.write_text(json.dumps({"catalog": url}))
        paths = apps.Paths(catalog=Path(tmp, "apps-catalog"), config=config)
        try:
            templates = apps.refresh_catalog(paths)
        except apps.AppError as e:
            print(f"fetch-catalog: {e}", file=sys.stderr)
            return 1
        if target.exists():
            shutil.rmtree(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(paths.catalog, target)
    for p in [target, *target.rglob("*")]:
        p.chmod(0o755 if p.is_dir() else 0o644)
    icons = len(list((target / "icons").iterdir()))
    print(f"fetch-catalog: {len(templates)} apps, {icons} icons from {url}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
