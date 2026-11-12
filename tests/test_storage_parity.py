"""dahlia-snapraid status, fix and the record of runs (fake snapraid)."""

import fcntl
import json
import os
import sys
import textwrap

import pytest
from dahlia_storage import snapraid, snapraid_job, state

STATUS_LOG = """\
version:12.3
command:status
data:d1:/mnt/disk1/
msg:progress: Loading state from /var/lib/snapraid/snapraid.content...
summary:parity_block_count:6
summary:disk_file_count:d1:3
summary:disk_fragmented_file_count:d1:0
summary:disk_file_size:d1:900000
summary:disk_file_count:d2:2
summary:disk_file_size:d2:600000
summary:file_count:5
summary:fragmented_file_count:0
summary:file_size:1500000
summary:has_unsynced:0
summary:has_unscrubbed:4
summary:has_rehash:0
summary:has_bad:2:0:1
info_time:1794422792:5:scrubbed
info_time:1794422808:1:scrubbed
"""

DIFF_BLOCKED = """\
      10 equal
       0 added
      99 removed
       0 updated
       0 moved
       0 copied
       0 restored
"""


def test_parse_status():
    s = snapraid.parse_status(STATUS_LOG)
    assert s["files"] == 5 and s["size"] == 1_500_000
    assert s["disks"] == {"d1": {"files": 3, "size": 900_000}, "d2": {"files": 2, "size": 600_000}}
    assert s["bad"] == 2 and s["unscrubbed"] == 4 and s["unsynced"] == 0
    assert s["scrub_oldest"] == 1794422792 and s["scrub_newest"] == 1794422808


def test_parse_status_rejects_garbage():
    with pytest.raises(snapraid.SnapraidError):
        snapraid.parse_status("Self test...\nmsg:fatal: no content\n")


def test_fix_counts():
    out = "Fixing...\n       3 errors\n       2 recovered errors\n       1 unrecoverable errors\n"
    assert snapraid_job.fix_counts(out) == {"recovered": 2, "unrecoverable": 1}


@pytest.fixture
def array(tmp_path, monkeypatch):
    """Fake snapraid plus a layout with two data disks and one parity disk."""
    calls = tmp_path / "calls"
    fake = tmp_path / "snapraid"
    fake.write_text(textwrap.dedent(f"""\
        #!{sys.executable}
        import os, sys
        args = sys.argv[3:]
        with open({str(calls)!r}, "a") as f:
            f.write(" ".join(args) + "\\n")
        mode = os.environ.get("FAKE_MODE", "")
        if args[-1] == "diff":
            sys.stdout.write(open({str(tmp_path / "diff.txt")!r}).read())
            sys.exit(2)
        if args[-1] == "status":
            sys.stdout.write(open({str(tmp_path / "status.txt")!r}).read())
            sys.exit(0)
        if args[-1] == "fix":
            print("       4 recovered errors")
            print("       0 unrecoverable errors")
            sys.exit(0)
        if args[-1] == "check":
            sys.exit(1 if mode == "check-fails" else 0)
        sys.exit(1 if mode == "scrub-fails" and "scrub" in args else 0)
        """))
    fake.chmod(0o755)
    (tmp_path / "diff.txt").write_text(DIFF_BLOCKED)
    (tmp_path / "status.txt").write_text(STATUS_LOG)
    content = tmp_path / "snapraid.content"
    st = state.State(disks=[
        state.Disk("u1", "data", 1, "ext4", size=4_000),
        state.Disk("u2", "data", 2, "xfs", size=8_000),
        state.Disk("p1", "parity", 1, "ext4", size=8_000),
    ])
    monkeypatch.setattr(snapraid_job, "SNAPRAID", str(fake))
    monkeypatch.setattr(snapraid_job, "RUNS_PATH", str(tmp_path / "lib" / "snapraid.json"))
    monkeypatch.setattr(snapraid_job, "FIX_LOG", str(tmp_path / "log" / "fix.log"))
    monkeypatch.setattr(snapraid, "CONFIG_PATH", str(tmp_path / "snapraid.conf"))
    monkeypatch.setattr(snapraid, "LOCAL_CONTENT", str(content))
    monkeypatch.setattr(state, "ARRAY_LOCK_PATH", str(tmp_path / "run" / "array.lock"))
    monkeypatch.setattr(state, "load", lambda: st)
    monkeypatch.setattr(snapraid_job, "unmounted", lambda st: [])
    monkeypatch.setattr(snapraid_job.os.path, "ismount", lambda p: True)
    return {"tmp": tmp_path, "calls": calls, "content": content, "state": st}


def calls(array) -> list[str]:
    return array["calls"].read_text().splitlines() if array["calls"].exists() else []


def runs(array) -> dict:
    return json.loads((array["tmp"] / "lib" / "snapraid.json").read_text())


def test_blocked_sync_is_recorded(array, capsys):
    assert snapraid_job.main(["sync"]) == 1
    sync = runs(array)["sync"]
    assert sync["result"] == "blocked"
    assert sync["counts"]["removed"] == 99 and sync["threshold"] == 50
    assert "99 files removed" in sync["message"]
    assert sync["end"] >= sync["start"]
    assert "sync" not in calls(array)


def test_runs_are_kept_in_history(array, monkeypatch, capsys):
    monkeypatch.setattr(snapraid_job, "HISTORY_MAX", 3)
    snapraid_job.main(["sync"])
    snapraid_job.main(["sync", "--force"])
    array["content"].write_text("x")
    snapraid_job.main(["scrub"])
    snapraid_job.main(["fix", "--bad"])
    history = runs(array)["history"]
    assert [(h["kind"], h["result"]) for h in history] == [
        ("sync", "ok"), ("scrub", "ok"), ("fix", "ok")]
    assert all(h["end"] >= h["start"] for h in history)
    assert snapraid_job.describe(array["state"])["history"] == history


def test_forced_sync_runs(array, capsys):
    assert snapraid_job.main(["sync", "--force"]) == 0
    assert calls(array) == ["diff", "sync"]
    assert runs(array)["sync"]["result"] == "ok"


def test_fix_disk_rebuilds_and_checks(array, capsys):
    array["content"].write_text("x")
    assert snapraid_job.main(["fix", "--disk", "2"]) == 0
    log = str(array["tmp"] / "log" / "fix.log")
    assert calls(array) == [f"-d d2 -l {log} fix", "-d d2 -a check"]
    fix = runs(array)["fix"]
    assert fix == {**fix, "result": "ok", "what": "d2", "recovered": 4, "unrecoverable": 0}


def test_fix_reports_failed_check(array, monkeypatch, capsys):
    array["content"].write_text("x")
    monkeypatch.setenv("FAKE_MODE", "check-fails")
    assert snapraid_job.main(["fix", "--disk", "1"]) == 1
    fix = runs(array)["fix"]
    assert fix["result"] == "failed" and fix["what"] == "d1"
    assert "check found differences on d1" in fix["message"]


def test_fix_rejects_unknown_disk_and_missing_sync(array, capsys):
    assert snapraid_job.main(["fix", "--disk", "1"]) == 1
    assert "no sync yet" in runs(array)["fix"]["message"]
    array["content"].write_text("x")
    assert snapraid_job.main(["fix", "--disk", "7"]) == 1
    assert runs(array)["fix"]["message"] == "no data disk 7"
    assert calls(array) == []


def test_fix_bad_blocks_scrubs_them_again(array, capsys):
    array["content"].write_text("x")
    assert snapraid_job.main(["fix", "--bad"]) == 0
    assert calls(array) == ["-e fix", "-p bad scrub"]
    assert runs(array)["fix"]["what"] == "bad"


def test_scrub_with_errors_names_bad_blocks(array, monkeypatch, capsys):
    array["content"].write_text("x")
    monkeypatch.setenv("FAKE_MODE", "scrub-fails")
    assert snapraid_job.main(["scrub"]) == 1
    scrub = runs(array)["scrub"]
    assert scrub["bad"] == 2 and "scrub found 2 bad blocks" in scrub["message"]


def test_status_runs_snapraid_only_when_content_changed(array, capsys):
    info = snapraid_job.describe(array["state"])
    assert info["configured"] and info["status"] is None and not info["busy"]
    array["content"].write_text("x")
    os.utime(array["content"], (1, 1))
    info = snapraid_job.describe(array["state"])
    assert info["status"]["bad"] == 2
    snapraid_job.describe(array["state"])
    assert calls(array).count("-l >&1 status") == 1
    os.utime(array["content"])
    snapraid_job.describe(array["state"])
    assert calls(array).count("-l >&1 status") == 2
    assert [d["index"] for d in info["data"]] == [1, 2]
    assert info["settings"]["scrub_percent"] == 8


def test_status_while_a_job_runs_uses_the_cache(array, capsys):
    array["content"].write_text("x")
    snapraid_job.describe(array["state"])
    os.utime(array["content"])
    with open(state.ARRAY_LOCK_PATH, "w") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        info = snapraid_job.describe(array["state"])
    assert info["busy"] and info["status"]["bad"] == 2
    assert calls(array).count("-l >&1 status") == 1


def test_status_without_parity_lists_problems(array):
    st = state.State(disks=[state.Disk("u1", "data", 1, "ext4", size=4_000)])
    info = snapraid_job.describe(st)
    assert not info["configured"] and "no parity disk" in info["problems"]
    assert info["status"] is None and calls(array) == []


def test_status_json_cli(array, capsys):
    assert snapraid_job.main(["status", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["configured"] is True
