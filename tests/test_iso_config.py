"""Static checks for the installer configuration and package sources."""

import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PRESEED = ROOT / "iso" / "preseed.cfg"


def preseed_values():
    values = {}
    for line in PRESEED.read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#"):
            continue
        parts = line.split(None, 3)
        if len(parts) >= 3:
            values[parts[1]] = parts[3] if len(parts) == 4 else ""
    return values


def test_version_is_semver():
    version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    assert re.fullmatch(r"\d+\.\d+\.\d+", version)


def test_preseed_root_is_btrfs():
    assert preseed_values()["partman/default_filesystem"] == "btrfs"


def test_preseed_never_hardcodes_target_disk():
    # The disk is chosen by pick-disk.sh at install time; a fixed device
    # name would wipe the wrong disk on machines with several drives.
    values = preseed_values()
    assert "partman-auto/disk" not in values
    assert values["partman/early_command"] == "sh /cdrom/dahlia/pick-disk.sh"


def test_preseed_keeps_gid_1000_free():
    assert preseed_values()["passwd/user-uid"] == "1001"


def test_preseed_disables_root_login():
    assert preseed_values()["passwd/root-login"] == "false"


def test_preseed_runs_dahlia_late_command():
    assert preseed_values()["preseed/late_command"] == "sh /cdrom/dahlia/late-command.sh"


def test_boot_menus_use_preseed_mode():
    for cfg in ("isolinux.cfg", "grub.cfg"):
        text = (ROOT / "iso" / cfg).read_text(encoding="utf-8")
        assert "auto=true priority=critical" in text, cfg
        assert "/install.amd/initrd.gz" in text, cfg


def test_scripts_are_executable():
    scripts = [
        ROOT / "tools" / "build-packages.sh",
        ROOT / "iso" / "build-iso.sh",
        ROOT / "iso" / "test-install.sh",
        ROOT / "packages" / "dahlia-base" / "DEBIAN" / "postinst",
        ROOT / "packages" / "dahlia-base" / "etc" / "update-motd.d" / "10-dahlia",
    ]
    for script in scripts:
        assert os.access(script, os.X_OK), script


def test_package_control_files_use_version_marker():
    for control in (ROOT / "packages").glob("*/DEBIAN/control"):
        text = control.read_text(encoding="utf-8")
        assert "Version: @VERSION@" in text, control
        assert "Architecture: all" in text, control
