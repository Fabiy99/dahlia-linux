"""Disk classification: what Dahlia may touch and what it must leave alone."""

import pytest
from dahlia_storage import disks


def dev(name, type_="disk", fstype=None, uuid=None, mounts=None, children=None, **extra):
    node = {
        "name": name,
        "path": f"/dev/{name}",
        "type": type_,
        "size": extra.pop("size", 4_000_000_000_000),
        "fstype": fstype,
        "uuid": uuid,
        "label": extra.pop("label", None),
        "mountpoints": mounts or [None],
        "model": extra.pop("model", "WDC WD40EFRX"),
        "serial": extra.pop("serial", f"SN-{name}"),
        "rm": extra.pop("rm", False),
        "pkname": extra.pop("pkname", None),
    }
    if children:
        node["children"] = children
    return node


def part(name, fstype=None, uuid=None, mounts=None, **extra):
    return dev(name, type_="part", fstype=fstype, uuid=uuid, mounts=mounts, **extra)


def by_path(found):
    return {d.path: d for d in found}


LSBLK = {
    "blockdevices": [
        # System SSD: EFI, root (btrfs) and swap.
        dev(
            "nvme0n1",
            children=[
                part("nvme0n1p1", "vfat", "AAAA-1111", ["/boot/efi"]),
                part("nvme0n1p2", "btrfs", "root-uuid", ["/"]),
                part("nvme0n1p3", "swap", "swap-uuid", ["[SWAP]"]),
            ],
        ),
        # Existing data disk with one ext4 partition.
        dev("sda", children=[part("sda1", "ext4", "uuid-sda1")]),
        # XFS directly on the disk, no partition table.
        dev("sdb", fstype="xfs", uuid="uuid-sdb"),
        # Brand new disk.
        dev("sdc"),
        # mdraid member.
        dev("sdd", children=[part("sdd1", "linux_raid_member", "raid-uuid")]),
        # LUKS container.
        dev("sde", fstype="crypto_LUKS", uuid="luks-uuid"),
        # NTFS USB disk.
        dev("sdf", rm=True, children=[part("sdf1", "ntfs", "NTFS1234", label="Backup")]),
        # Mounted by hand outside Dahlia.
        dev("sdg", children=[part("sdg1", "ext4", "uuid-sdg1", ["/media/old"])]),
        # Loop devices and CD drives are ignored by default.
        dev("loop0", type_="loop", fstype="ext4", uuid="loop-uuid"),
        dev("sr0", type_="rom", fstype="iso9660", uuid="2025-01-01"),
        # zram swap shows up as TYPE=disk.
        dev("zram0", fstype="swap", uuid="zram-uuid", mounts=["[SWAP]"]),
    ]
}


def test_system_disk_is_never_offered():
    found = by_path(disks.classify(LSBLK))
    for p in ("/dev/nvme0n1p1", "/dev/nvme0n1p2", "/dev/nvme0n1p3"):
        assert found[p].status == "system"


def test_existing_filesystems_are_candidates():
    found = by_path(disks.classify(LSBLK))
    assert found["/dev/sda1"].status == "candidate"
    assert found["/dev/sda1"].disk == "/dev/sda"
    assert found["/dev/sdb"].status == "candidate"


def test_blank_disk_is_empty():
    assert by_path(disks.classify(LSBLK))["/dev/sdc"].status == "empty"


def test_mounted_disk_without_readable_signature_is_not_blank():
    # squashfs or anything blkid cannot read, mounted by someone else.
    lsblk = {"blockdevices": [dev("vde", mounts=["/mnt/skills/public"])]}
    found = disks.classify(lsblk)
    assert found[0].status == "mounted"
    with pytest.raises(ValueError):
        disks.formattable(found, "/dev/vde")


def test_ambivalent_signatures_are_foreign(monkeypatch):
    class Res:
        returncode = 8
        stdout = ""
    monkeypatch.setattr(disks.subprocess, "run", lambda *a, **k: Res())
    lsblk = disks.enrich({"blockdevices": [dev("sdy")]}, disks.blkid_probe)
    found = disks.classify(lsblk)
    assert found[0].status == "foreign"
    with pytest.raises(ValueError):
        disks.formattable(found, "/dev/sdy")


def test_raid_and_luks_are_not_offered():
    found = by_path(disks.classify(LSBLK))
    assert found["/dev/sdd1"].status == "foreign"
    # A locked container could be anyone's: neither usable nor formattable.
    assert found["/dev/sde"].status == "locked"
    assert found["/dev/sde"].luks == "luks-uuid"


def crypt(name, fstype="xfs", uuid="fs-uuid", mounts=None):
    return dev(name, type_="crypt", fstype=fstype, uuid=uuid, mounts=mounts)


def test_open_dahlia_luks_is_classified_by_its_filesystem():
    lsblk = {"blockdevices": [
        dev("sdi", children=[part("sdi1", "crypto_LUKS", "L1",
                                  children=[crypt("luks-L1")])]),
        # opened by hand under another name: left alone
        dev("sdj", children=[part("sdj1", "crypto_LUKS", "L2",
                                  children=[crypt("backup", uuid="fs2")])]),
        dev("sdk", children=[part("sdk1", "crypto_LUKS", "L3")]),
    ]}
    found = by_path(disks.classify(lsblk, registered_luks={"L3"}))
    d = found["/dev/sdi1"]
    assert (d.status, d.uuid, d.fstype, d.luks, d.disk) == ("candidate", "fs-uuid", "xfs", "L1",
                                                             "/dev/sdi")
    assert found["/dev/sdj1"].status == "foreign"
    assert (found["/dev/sdk1"].status, found["/dev/sdk1"].reason) == ("registered",
                                                                      "encrypted, locked")
    found = by_path(disks.classify(lsblk, registered_uuids={"fs-uuid"}))
    assert found["/dev/sdi1"].status == "registered"
    assert found["/dev/sdk1"].status == "locked"
    with pytest.raises(ValueError):
        disks.formattable(disks.classify(lsblk), "/dev/sdi")


def test_ntfs_is_external_only():
    d = by_path(disks.classify(LSBLK))["/dev/sdf1"]
    assert d.status == "external-only"
    assert d.removable is True


def test_mounted_elsewhere_is_left_alone():
    assert by_path(disks.classify(LSBLK))["/dev/sdg1"].status == "mounted"


def test_registered_disk_is_marked():
    found = by_path(disks.classify(LSBLK, registered_uuids={"uuid-sda1"}))
    assert found["/dev/sda1"].status == "registered"


def test_loop_and_rom_ignored_unless_allowed():
    paths = by_path(disks.classify(LSBLK))
    assert "/dev/loop0" not in paths
    assert "/dev/sr0" not in paths
    assert "/dev/zram0" not in paths
    assert by_path(disks.classify(LSBLK, allow_loop=True))["/dev/loop0"].status == "candidate"


def test_foreign_partition_taints_whole_disk():
    # An ext4 partition next to an LVM PV is not offered: too easy to break.
    lsblk = {
        "blockdevices": [
            dev(
                "sdh",
                children=[part("sdh1", "ext4", "uuid-sdh1"), part("sdh2", "LVM2_member", "pv")],
            )
        ]
    }
    assert by_path(disks.classify(lsblk))["/dev/sdh1"].status == "foreign"


def test_enrich_fills_missing_signatures_from_probe():
    # udev has not seen the new filesystem yet; blkid -p has.
    lsblk = {"blockdevices": [dev("loop3", type_="loop"), dev("sdz", fstype="xfs", uuid="x")]}
    probed = {"/dev/loop3": {"TYPE": "ext4", "UUID": "fresh-uuid", "LABEL": "d1"}}
    calls = []

    def probe(path):
        calls.append(path)
        return probed.get(path, {})

    disks.enrich(lsblk, probe)
    found = by_path(disks.classify(lsblk, allow_loop=True))
    assert found["/dev/loop3"].status == "candidate"
    assert found["/dev/loop3"].uuid == "fresh-uuid"
    assert calls == ["/dev/loop3"]  # devices udev already knows are not probed


def test_only_blank_or_imported_whole_disks_are_formattable():
    found = disks.classify(LSBLK)
    assert [d.path for d in disks.formattable(found, "/dev/sdc")] == ["/dev/sdc"]
    for device, why in [("/dev/nvme0n1", "running system"), ("/dev/nvme0n1p2", "partition"),
                        ("/dev/sda", "holds data"), ("/dev/sda1", "partition"),
                        ("/dev/sdb", "holds data"), ("/dev/sde", "encrypted"),
                        ("/dev/sdg", "mounted outside"), ("/dev/sdz", "not a disk")]:
        with pytest.raises(ValueError, match=why):
            disks.formattable(found, device)


def test_imported_disk_becomes_formattable():
    found = disks.classify(LSBLK)
    entries = disks.formattable(found, "/dev/sdf", imported={"NTFS1234"})
    assert [d.path for d in entries] == ["/dev/sdf1"]
    with pytest.raises(ValueError, match="holds data"):
        disks.formattable(found, "/dev/sdf", imported={"other"})
    # A registered disk stays untouchable even after an import.
    found = disks.classify(LSBLK, registered_uuids={"uuid-sda1"})
    with pytest.raises(ValueError, match="managed by Dahlia"):
        disks.formattable(found, "/dev/sda", imported={"uuid-sda1"})


def test_formattable_refuses_blank_system_disk():
    lsblk = {"blockdevices": [dev("sdx", mounts=["/"])]}
    with pytest.raises(ValueError, match="system disk"):
        disks.formattable(disks.classify(lsblk), "/dev/sdx")


def test_partition_path():
    assert disks.partition_path("/dev/sdb") == "/dev/sdb1"
    assert disks.partition_path("/dev/nvme0n1") == "/dev/nvme0n1p1"
    assert disks.partition_path("/dev/loop3") == "/dev/loop3p1"
