"""Generated mount units, pool definitions, state file and SnapRAID config."""

import json

import pytest
from dahlia_storage import snapraid, state, units


def layout():
    return state.State(
        disks=[
            state.Disk("u1", "data", 1, "ext4", size=4_000),
            state.Disk("u2", "data", 2, "xfs", size=8_000),
            state.Disk("p1", "parity", 1, "ext4", size=8_000),
            state.Disk("n1", "external", 0, "ntfs", name="backup"),
        ]
    )


def test_escape_path_matches_systemd():
    assert units.escape_path("/mnt/disk1") == "mnt-disk1"
    assert units.escape_path("/mnt/external/my-disk") == "mnt-external-my\\x2ddisk"
    assert units.unit_name("/mnt/user") == "mnt-user.mount"


def test_disk_unit_is_weak_dependency():
    text = units.disk_unit(layout().disks[0])
    assert "What=/dev/disk/by-uuid/u1" in text
    assert "Where=/mnt/disk1" in text
    assert "WantedBy=dahlia-storage.target" in text
    assert "Requires" not in text


def test_ntfs_uses_kernel_driver_and_app_group():
    text = units.disk_unit(layout().disks[3])
    assert "Type=ntfs3" in text
    assert "uid=1000,gid=1000" in text
    assert "Where=/mnt/external/backup" in text


def test_pools_contain_only_data_disks():
    st = layout()
    rendered = units.render_all(st)
    pool = rendered["mnt-storage.mount"]
    assert "branches=/mnt/disk1:/mnt/disk2," in pool
    # An absolute What= would make systemd require the first branch's mount.
    assert "What=dahlia-storage" in pool
    assert "parity" not in pool
    assert "Type=fuse.mergerfs" in pool
    assert "minfreespace=50G" in pool
    assert "After=mnt-disk1.mount mnt-disk2.mount" in pool
    assert "mnt-user.mount" in rendered


def test_no_pool_without_data_disks():
    st = state.State(disks=[state.Disk("p1", "parity", 1, "ext4")])
    assert set(units.render_all(st)) == {"mnt-parity1.mount"}


def test_next_index_fills_gaps():
    st = state.State(disks=[state.Disk("a", "data", 1, "ext4"), state.Disk("c", "data", 3, "ext4")])
    assert st.next_index("data") == 2
    assert st.next_index("parity") == 1


def test_state_roundtrip(tmp_path):
    path = tmp_path / "storage.json"
    st = layout()
    st.settings.minfreespace = "20G"
    state.save(st, str(path))
    loaded = state.load(str(path))
    assert loaded == st
    assert json.loads(path.read_text())["version"] == 1


def test_state_rejects_bad_values(tmp_path):
    path = tmp_path / "storage.json"
    path.write_text(json.dumps({"disks": [], "settings": {"minfreespace": "lots"}}))
    with pytest.raises(ValueError):
        state.load(str(path))


def test_snapraid_config():
    text = snapraid.render(layout())
    assert "parity /mnt/parity1/snapraid.parity" in text
    assert "data d1 /mnt/disk1/" in text
    assert "data d2 /mnt/disk2/" in text
    assert "content /var/lib/snapraid/snapraid.content" in text
    assert "content /mnt/disk1/.snapraid.content" in text
    assert "/mnt/external" not in text


def test_snapraid_rejects_small_parity():
    st = layout()
    st.disks[2].size = 1_000
    with pytest.raises(snapraid.SnapraidError, match="smaller than the largest data disk"):
        snapraid.render(st)


def test_snapraid_needs_parity():
    st = state.State(disks=[state.Disk("u1", "data", 1, "ext4", size=1)])
    assert "no parity disk" in snapraid.validate(st)
