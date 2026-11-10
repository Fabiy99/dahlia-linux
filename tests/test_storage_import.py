"""dahlia-disk import: checks before copying and parsing of rsync output."""

import pytest
from dahlia_storage import disks, importer
from test_storage_disks import LSBLK


def test_source_must_be_mountable_and_unused():
    found = disks.classify(LSBLK, registered_uuids={"uuid-sda1"})
    assert importer.check_source(found, "NTFS1234").path == "/dev/sdf1"
    assert importer.check_source(found, "uuid-sdb").fstype == "xfs"
    for uuid, why in [("uuid-sda1", "managed by Dahlia"), ("root-uuid", "running system"),
                      ("uuid-sdg1", "mounted outside"), ("luks-uuid", "member of"),
                      ("nope", "no filesystem")]:
        with pytest.raises(importer.ImportError_, match=why):
            importer.check_source(found, uuid)


def test_target_checks(tmp_path, monkeypatch):
    with pytest.raises(importer.ImportError_, match="letters"):
        importer.check_target("../etc", str(tmp_path))
    with pytest.raises(importer.ImportError_, match="not mounted"):
        importer.check_target("old", str(tmp_path))
    monkeypatch.setattr(importer.os.path, "ismount", lambda p: True)
    (tmp_path / "taken").mkdir()
    with pytest.raises(importer.ImportError_, match="already exists"):
        importer.check_target("taken", str(tmp_path))
    assert importer.check_target("old-sdb", str(tmp_path)) == str(tmp_path / "old-sdb")


def test_parse_progress():
    assert importer.parse_progress("    1,234,567  42%   10.00MB/s    0:01:02") == (1234567, 42)
    assert importer.parse_progress("sending incremental file list") is None


def test_differences_ignore_directory_times():
    out = ".d..t...... ./\n.d..t...... photos/\n>f.st...... photos/a.jpg\n>f+++++++++ b.txt\n"
    assert importer.differences(out) == ["photos/a.jpg", "b.txt"]
    assert importer.differences("") == []


def test_space_margin():
    assert importer.enough_space(90, 100)
    assert not importer.enough_space(99, 100)


def test_imported_list(tmp_path, monkeypatch):
    monkeypatch.setattr(importer, "IMPORTED_PATH", str(tmp_path / "imported.json"))
    assert importer.imported_uuids() == set()
    importer.write_json(importer.IMPORTED_PATH, {"a": {}, "b": {}})
    importer.forget({"a"})
    assert importer.imported_uuids() == {"b"}


def test_rsync_copy_sets_share_group():
    cmd = importer.rsync_copy("/run/x", "/mnt/user/old")
    assert "--chown=root:dahlia" in cmd and cmd[-2:] == ["/run/x/", "/mnt/user/old/"]
    assert not any(c.startswith("--chown") for c in importer.rsync_copy("/a", "/b", None))
    assert "--checksum" in importer.rsync_verify("/a", "/b")
