"""Static checks for the installer configuration and package sources."""

import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PRESEED = ROOT / "iso" / "preseed.cfg"
AUTO = ROOT / "iso" / "installer" / "dahlia-auto.cfg"
GUIDED = ROOT / "iso" / "installer" / "dahlia-guided.cfg"


def preseed_values(path=PRESEED):
    values = {}
    for line in path.read_text(encoding="utf-8").splitlines():
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
    # The disk is chosen at install time (pick-disk.sh or guided.sh); a fixed
    # device name would wipe the wrong disk on machines with several drives.
    for path in (PRESEED, AUTO, GUIDED):
        assert "partman-auto/disk" not in preseed_values(path), path
    assert "partman/early_command" not in preseed_values()
    assert preseed_values(AUTO)["partman/early_command"] == "sh /cdrom/dahlia/pick-disk.sh"
    assert preseed_values(GUIDED)["partman/early_command"] == "sh /cdrom/dahlia/guided.sh"


def test_mode_is_chosen_by_kernel_parameter():
    cmd = preseed_values()["preseed/include_command"]
    assert "dahlia.mode=auto" in cmd
    assert "/dahlia-auto.cfg" in cmd and "/dahlia-guided.cfg" in cmd


def test_only_the_unattended_mode_has_a_fixed_password():
    assert "passwd/user-password-crypted" in preseed_values(AUTO)
    for path in (PRESEED, GUIDED):
        assert not any(k.startswith("passwd/user-password") for k in preseed_values(path)), path
    # The guided mode lets d-i ask for the keyboard layout.
    assert "keyboard-configuration/xkb-keymap" not in preseed_values()
    assert "keyboard-configuration/xkb-keymap" not in preseed_values(GUIDED)


def test_installer_is_english():
    values = preseed_values()
    assert values["debian-installer/language"] == "en"
    assert values["debian-installer/locale"] == "en_US.UTF-8"


def test_preseed_keeps_gid_1000_free():
    assert preseed_values()["passwd/user-uid"] == "1001"


def test_preseed_disables_root_login():
    assert preseed_values()["passwd/root-login"] == "false"


def test_preseed_runs_dahlia_late_command():
    assert preseed_values()["preseed/late_command"] == "sh /cdrom/dahlia/late-command.sh"


def grub_entries():
    grub = (ROOT / "iso" / "grub.cfg").read_text(encoding="utf-8")
    return re.findall(r'menuentry "([^"]+)" \{\n\s*linux ([^\n]+)', grub)


def isolinux_labels():
    isolinux = (ROOT / "iso" / "isolinux.cfg").read_text(encoding="utf-8")
    return dict(re.findall(
        r"label (\S+)\n\s*menu label [^\n]+\n\s*kernel \S+\n\s*append ([^\n]+)", isolinux))


def test_boot_menus_offer_both_modes():
    # Same four entries in the same order in both menus.
    grub = [line for _, line in grub_entries()]
    labels = isolinux_labels()
    syslinux = [labels[n] for n in ("dahlia", "dahlia-serial", "dahlia-auto",
                                    "dahlia-auto-serial")]
    assert len(grub) == 4
    for g, i in zip(grub, syslinux, strict=True):
        assert g.split(" ", 1)[1] == i.split(" ", 1)[1]
    for line in grub[:2]:
        assert "priority=high" in line and "dahlia.mode" not in line
    for line in grub[2:]:
        assert "auto=true priority=critical dahlia.mode=auto ---" in line
    # Installer-only parameters stay before "---".
    for line in grub:
        assert "dahlia.mode" not in line.split("---", 1)[1]


def test_default_boot_entries_do_not_use_serial_console():
    # Without a serial port (Hyper-V gen 2, most hardware) d-i would run on
    # ttyS0 and leave the screen black, so only the extra entries may use it.
    entries = grub_entries()
    assert entries[0][0] == "Install Dahlia Linux"
    assert [("ttyS0" in line) for _, line in entries] == [False, True, False, True]
    grub = (ROOT / "iso" / "grub.cfg").read_text(encoding="utf-8")
    assert re.search(r"^set default=0$", grub, re.M)

    isolinux = (ROOT / "iso" / "isolinux.cfg").read_text(encoding="utf-8")
    labels = isolinux_labels()
    assert re.search(r"^default dahlia$", isolinux, re.M)
    assert "ttyS0" not in labels["dahlia"]
    assert "ttyS0" in labels["dahlia-serial"]


def test_build_ships_installer_files():
    build = (ROOT / "iso" / "build-iso.sh").read_text(encoding="utf-8")
    for f in (ROOT / "iso" / "installer").iterdir():
        assert f.name in build, f.name


def test_preseed_does_not_force_serial_console():
    assert "debian-installer/add-kernel-opts" not in preseed_values()


def test_scripts_are_executable():
    scripts = [
        ROOT / "tools" / "build-packages.sh",
        ROOT / "iso" / "build-iso.sh",
        ROOT / "iso" / "test-install.sh",
        *sorted((ROOT / "iso" / "installer").glob("*.sh")),
        ROOT / "tools" / "sign-repo.sh",
        ROOT / "tests" / "integration" / "apt-repo.sh",
        ROOT / "packages" / "base" / "DEBIAN" / "postinst",
        ROOT / "packages" / "base" / "etc" / "update-motd.d" / "10-dahlia",
    ]
    for script in scripts:
        assert os.access(script, os.X_OK), script


def test_package_control_files_use_version_marker():
    for control in (ROOT / "packages").glob("*/DEBIAN/control"):
        text = control.read_text(encoding="utf-8")
        assert "Version: @VERSION@" in text, control
        assert "Architecture: all" in text, control


def test_apt_source_uses_shipped_keyring():
    pkg = ROOT / "packages" / "archive-keyring"
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
    control = (ROOT / "packages" / "base" / "DEBIAN" / "control").read_text(
        encoding="utf-8"
    )
    assert "archive-keyring (= @VERSION@)" in control
