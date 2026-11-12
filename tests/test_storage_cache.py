"""Cache tier in /mnt/user, settings and SnapRAID job decisions."""

import pytest
from dahlia_storage import snapraid, snapraid_job, state, units


def layout(cache=True):
    disks = [
        state.Disk("u1", "data", 1, "ext4", size=4_000),
        state.Disk("u2", "data", 2, "xfs", size=8_000),
        state.Disk("p1", "parity", 1, "ext4", size=8_000),
    ]
    if cache:
        disks.append(state.Disk("c1", "cache", 0, "btrfs", size=1_000))
    return state.State(disks=disks)


def test_cache_is_first_branch_of_user_pool_only():
    st = layout()
    assert units.pool_branches(st, units.USER_POOL) == [
        "/mnt/cache/shares",
        "/mnt/disk1",
        "/mnt/disk2",
    ]
    assert units.pool_branches(st, units.STORAGE_POOL) == ["/mnt/disk1", "/mnt/disk2"]


def test_user_pool_fills_cache_first():
    st = layout()
    user = units.pool_unit(st, units.USER_POOL)
    storage = units.pool_unit(st, units.STORAGE_POOL)
    assert "category.create=ff" in user
    assert "What=dahlia-user" in user
    assert "branches=/mnt/cache/shares:/mnt/disk1:/mnt/disk2," in user
    assert "mnt-cache.mount" in user.split("[Mount]")[0]
    # The mover target keeps spreading files over the data disks.
    assert "category.create=mfs" in storage
    assert "/mnt/cache" not in storage and "mnt-cache.mount" not in storage


def test_without_cache_user_pool_spreads_like_storage():
    st = layout(cache=False)
    assert "category.create=mfs" in units.pool_unit(st, units.USER_POOL)


def test_cache_alone_gives_user_pool():
    st = state.State(disks=[state.Disk("c1", "cache", 0, "btrfs")])
    assert units.wanted_pools(st) == [units.USER_POOL]
    assert set(units.render_all(st)) == {"mnt-cache.mount", "mnt-user.mount"}


def test_cache_mountpoint_and_unit():
    st = layout()
    assert st.cache.mountpoint == "/mnt/cache"
    assert "Description=Dahlia cache /mnt/cache" in units.disk_unit(st.cache)


def test_cache_is_not_protected_by_snapraid():
    text = snapraid.render(layout())
    assert "/mnt/cache" not in text
    assert "exclude *.mover.tmp" in text


def test_state_rejects_second_cache():
    data = layout().to_dict()
    data["disks"].append(dict(data["disks"][-1], uuid="c2"))
    with pytest.raises(ValueError, match="more than one cache"):
        state.State.from_dict(data)


@pytest.mark.parametrize(
    "key,value",
    [
        ("snapraid_delete_threshold", -1),
        ("mover_min_age_minutes", "10"),
        ("scrub_percent", 0),
        ("scrub_percent", 101),
        ("scrub_older_than_days", True),
    ],
)
def test_state_rejects_bad_settings(key, value):
    data = layout().to_dict()
    data["settings"][key] = value
    with pytest.raises(ValueError):
        state.State.from_dict(data)


DIFF_OUTPUT = """\
Loading state from /var/lib/snapraid/snapraid.content...
Comparing...
add shares/media/new.mkv
remove shares/media/old.mkv

   12345 equal
       3 added
      61 removed
       2 updated
       0 moved
       0 copied
       0 restored
There are differences!
"""


def test_parse_diff():
    counts = snapraid.parse_diff(DIFF_OUTPUT)
    assert counts["equal"] == 12345
    assert counts["added"] == 3
    assert counts["removed"] == 61


def test_parse_diff_rejects_garbage():
    with pytest.raises(snapraid.SnapraidError):
        snapraid.parse_diff("Self test...\nerror\n")


def test_sync_blocked_above_delete_threshold():
    counts = snapraid.parse_diff(DIFF_OUTPUT)
    reason = snapraid_job.decide(counts, threshold=50, force=False)
    assert reason is not None and "61 files removed" in reason
    assert snapraid_job.decide(counts, threshold=50, force=True) is None
    assert snapraid_job.decide(counts, threshold=100, force=False) is None


def test_sync_skipped_without_changes():
    counts = {"equal": 10, "added": 0, "removed": 0, "updated": 0, "moved": 0, "copied": 0}
    assert snapraid_job.decide(counts, threshold=50, force=False) == "no changes"


def test_pools_keep_page_cache_for_shared_mmap():
    # SQLite WAL and MariaDB need shared mmap, which direct_io breaks.
    st = layout()
    for pool in (units.USER_POOL, units.STORAGE_POOL):
        unit = units.pool_unit(st, pool)
        assert "cache.files=partial" in unit
        assert "cache.files=off" not in unit
