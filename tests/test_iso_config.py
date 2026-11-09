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


def test_default_boot_entries_do_not_use_serial_console():
    # Without a serial port (Hyper-V gen 2, most hardware) d-i would run on
    # ttyS0 and leave the screen black, so only the extra entry may use it.
    grub = (ROOT / "iso" / "grub.cfg").read_text(encoding="utf-8")
    entries = re.findall(r'menuentry "([^"]+)" \{\n\s*linux ([^\n]+)', grub)
    assert entries[0][0] == "Install Dahlia Linux"
    assert "ttyS0" not in entries[0][1]
    assert "ttyS0" in entries[1][1]

    isolinux = (ROOT / "iso" / "isolinux.cfg").read_text(encoding="utf-8")
    labels = dict(re.findall(r"label (\S+)\n\s*kernel \S+\n\s*append ([^\n]+)", isolinux))
    assert re.search(r"^default dahlia$", isolinux, re.M)
    assert "ttyS0" not in labels["dahlia"]
    assert "ttyS0" in labels["dahlia-serial"]


def test_preseed_does_not_force_serial_console():
    assert "debian-installer/add-kernel-opts" not in preseed_values()


def test_scripts_are_executable():
    scripts = [
        ROOT / "tools" / "build-packages.sh",
        ROOT / "iso" / "build-iso.sh",
        ROOT / "iso" / "test-install.sh",
        ROOT / "tools" / "sign-repo.sh",
        ROOT / "tests" / "integration" / "apt-repo.sh",
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


def test_apt_source_uses_shipped_keyring():
    pkg = ROOT / "packages" / "dahlia-archive-keyring"
    sources = (pkg / "etc" / "apt" / "sources.list.d" / "dahlia.sources").read_text(
        encoding="utf-8"
    )
    fields = dict(
        line.split(": ", 1) for line in sources.splitlines() if line and not line.startswith("#")
    )
    assert fields["URIs"].startswith("https://")
    assert fields["Suites"] == "./"
    keyring = fields["Signed-By"]
    assert (pkg / keyring.lstrip("/")).is_file(), keyring
    assert "BEGIN PGP PUBLIC KEY BLOCK" in (pkg / keyring.lstrip("/")).read_text(encoding="utf-8")


def test_base_pulls_in_keyring_of_same_version():
    control = (ROOT / "packages" / "dahlia-base" / "DEBIAN" / "control").read_text(
        encoding="utf-8"
    )
    assert "dahlia-archive-keyring (= @VERSION@)" in control
