"""dahlia-disk import: checks before copying and parsing of rsync output."""

import shutil
import subprocess
import sys

import pytest
from dahlia_storage import disks, importer
from test_storage_disks import LSBLK


def test_source_must_be_mountable_and_unused():
    found = disks.classify(LSBLK, registered_uuids={"uuid-sda1"})
    assert importer.check_source(found, "NTFS1234").path == "/dev/sdf1"
    assert importer.check_source(found, "uuid-sdb").fstype == "xfs"
    for uuid, why in [("uuid-sda1", "managed by Dahlia"), ("root-uuid", "running system"),
                      ("uuid-sdg1", "mounted outside"), ("luks-uuid", "encrypted"),
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


def test_run_with_progress_splits_carriage_returns():
    script = ("import sys; sys.stdout.write('  1,000  10%  1MB/s\\r  5,000  50%\\r 10,000 100%\\n')"
              "; sys.stderr.write('oops\\n'); sys.exit(0)")
    got = []
    importer.run_with_progress([sys.executable, "-c", script], lambda b, p: got.append((b, p)))
    assert got == [(1000, 10), (5000, 50), (10000, 100)]
    with pytest.raises(importer.ImportError_, match="rsync failed: boom"):
        importer.run_with_progress(
            [sys.executable, "-c", "import sys; sys.stderr.write('boom\\n'); sys.exit(23)"],
            lambda b, p: None)


@pytest.mark.skipif(not shutil.which("rsync"), reason="rsync not installed")
def test_run_import_copies_and_verifies(tmp_path, monkeypatch):
    base, pool = tmp_path / "mnt", tmp_path / "user"
    src = base / "1234-ABCD"
    (src / "photos").mkdir(parents=True)
    (src / "photos/a.jpg").write_bytes(b"x" * 5000)
    (src / "notes.txt").write_text("hello")
    pool.mkdir()
    monkeypatch.setattr(importer, "MOUNT_BASE", str(base))
    monkeypatch.setattr(importer, "STATUS_PATH", str(tmp_path / "import.json"))
    monkeypatch.setattr(importer, "IMPORTED_PATH", str(tmp_path / "imported.json"))
    monkeypatch.setattr(importer, "share_group", lambda: None)
    real_run = importer.subprocess.run
    mounts = []

    def fake_run(cmd, **kw):
        if cmd[0] in ("mount", "umount"):
            mounts.append(cmd[0])
            return importer.subprocess.CompletedProcess(cmd, 0, "", "")
        return real_run(cmd, **kw)

    monkeypatch.setattr(importer.subprocess, "run", fake_run)
    dev = disks.classify(LSBLK)[0]
    dev.uuid, dev.fstype = "1234-ABCD", "ntfs"
    res = importer.run_import(dev, str(pool / "old"))
    assert res["phase"] == "done" and mounts == ["mount", "umount"]
    assert (pool / "old/photos/a.jpg").read_bytes() == b"x" * 5000
    assert importer.imported_uuids() == {"1234-ABCD"}
    # A second run into the same target fails and is recorded as failed.
    with pytest.raises(importer.ImportError_):
        importer.run_import(dev, str(pool / "old"))
    assert importer.read_json(importer.STATUS_PATH)["phase"] == "failed"


@pytest.mark.skipif(shutil.which("rsync") is None, reason="rsync not installed")
def test_housekeeping_folders_are_not_copied(tmp_path):
    src, dst = tmp_path / "src", tmp_path / "dst"
    for d in ("lost+found", "System Volume Information", "$RECYCLE.BIN", "Fotos/lost+found"):
        (src / d).mkdir(parents=True)
    (src / "Fotos/a.jpg").write_text("a")
    dst.mkdir()
    importer.run_with_progress(importer.rsync_copy(str(src), str(dst), None), lambda b, p: None)
    assert sorted(p.name for p in dst.iterdir()) == ["Fotos"]
    # Only top-level housekeeping is skipped; a user folder of that name stays.
    assert (dst / "Fotos/lost+found").is_dir()
    res = subprocess.run(importer.rsync_verify(str(src), str(dst)), capture_output=True, text=True)
    assert res.returncode == 0 and importer.differences(res.stdout) == []
