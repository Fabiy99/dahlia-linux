"""dahlia-mover against plain directories standing in for cache and pool."""

import os
import time

from dahlia_storage import mover


def make(path, content=b"x", age=0):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    if age:
        t = time.time() - age
        os.utime(path, (t, t))
    return path


def setup(tmp_path):
    cache = tmp_path / "cache"
    dest = tmp_path / "storage"
    (cache / "media").mkdir(parents=True)
    dest.mkdir()
    return cache, dest


def quiet(_msg):
    pass


def test_moves_files_and_keeps_paths(tmp_path):
    cache, dest = setup(tmp_path)
    make(cache / "media" / "film" / "a.mkv", b"movie")
    os.symlink("a.mkv", cache / "media" / "film" / "link.mkv")
    res = mover.run(str(cache), str(dest), busy=set(), log=quiet)
    assert res.moved == 2 and not res.errors
    assert (dest / "media" / "film" / "a.mkv").read_bytes() == b"movie"
    assert os.readlink(dest / "media" / "film" / "link.mkv") == "a.mkv"
    assert not (cache / "media" / "film").exists()
    # Share directories stay on the cache for new files.
    assert (cache / "media").is_dir()
    assert not list(dest.rglob("*" + mover.TMP_SUFFIX))


def test_keeps_mode_and_mtime(tmp_path):
    cache, dest = setup(tmp_path)
    f = make(cache / "media" / "a.bin", age=3600)
    os.chmod(f, 0o640)
    mtime = f.stat().st_mtime_ns
    mover.run(str(cache), str(dest), busy=set(), log=quiet)
    moved = dest / "media" / "a.bin"
    assert moved.stat().st_mode & 0o777 == 0o640
    assert moved.stat().st_mtime_ns == mtime


def test_skips_open_recent_existing_and_hardlinks(tmp_path):
    cache, dest = setup(tmp_path)
    busy = make(cache / "media" / "busy.bin", age=7200)
    make(cache / "media" / "fresh.bin")
    make(cache / "media" / "dup.bin", b"cache copy", age=7200)
    make(dest / "media" / "dup.bin", b"disk copy")
    hl = make(cache / "media" / "hl1.bin", age=7200)
    os.link(hl, cache / "media" / "hl2.bin")
    make(cache / "media" / "old.bin", age=7200)

    res = mover.run(str(cache), str(dest), min_age=60, busy={str(busy)}, log=quiet)

    reasons = dict(res.skipped)
    assert reasons["media/busy.bin"] == "open"
    assert reasons["media/fresh.bin"] == "modified recently"
    assert reasons["media/dup.bin"] == "already exists on the data disks"
    assert reasons["media/hl1.bin"] == "hard link"
    assert res.moved == 1
    assert (dest / "media" / "old.bin").exists()
    # Never overwrite what is already on the data disks.
    assert (dest / "media" / "dup.bin").read_bytes() == b"disk copy"
    assert (cache / "media" / "dup.bin").read_bytes() == b"cache copy"


def test_dry_run_changes_nothing(tmp_path):
    cache, dest = setup(tmp_path)
    make(cache / "media" / "a.bin")
    res = mover.run(str(cache), str(dest), dry_run=True, busy=set(), log=quiet)
    assert res.moved == 1
    assert (cache / "media" / "a.bin").exists()
    assert not (dest / "media").exists()


def test_open_files_reads_proc(tmp_path):
    proc = tmp_path / "proc"
    (proc / "123" / "fd").mkdir(parents=True)
    (proc / "self").mkdir()
    os.symlink("/mnt/cache/shares/media/a.mkv", proc / "123" / "fd" / "3")
    os.symlink("/etc/passwd", proc / "123" / "fd" / "4")
    assert mover.open_files("/mnt/cache/shares", proc=str(proc)) == {
        "/mnt/cache/shares/media/a.mkv"
    }
