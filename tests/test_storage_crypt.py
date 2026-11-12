"""LUKS encryption of data disks: key handling, crypttab, units, unlock logic.

cryptsetup itself runs in tests/integration/storage-loop.sh against loop devices.
"""

import contextlib
import io
import json

import pytest
from dahlia_storage import cli, crypt, state, units

LUKS = "0b8e6a8e-1c9f-4c55-9a3e-2b1d7c0f4a11"


def enc_disk(index=1, role="data", uuid="fs-1", luks=LUKS):
    return state.Disk(uuid=uuid, role=role, index=index, fstype="xfs", luks=luks)


def test_passphrase_rules_and_stdin_lines():
    assert crypt.read_keys(io.BytesIO("Grüne Wiese 42\n".encode()), 1) == ["Grüne Wiese 42"]
    assert crypt.read_keys(io.BytesIO(b"alt-passwort\nneu-passwort"), 2) == ["alt-passwort",
                                                                           "neu-passwort"]
    for raw, count in ((b"kurz\n", 1), (b"a" * 513, 1), (b"eins-zwei-drei\n", 2),
                       (b"tab\there-is-bad\n", 1)):
        with pytest.raises(crypt.CryptError):
            crypt.read_keys(io.BytesIO(raw), count)


def test_crypttab_block_keeps_foreign_entries():
    other = "swap /dev/sdz2 /dev/urandom swap\n"
    text = crypt.render_crypttab(other, [LUKS])
    assert text == (other + crypt.BEGIN + "\n"
                    f"luks-{LUKS} UUID={LUKS} /etc/dahlia/luks/array.key luks,nofail\n"
                    + crypt.END + "\n")
    # replaced, not appended twice; removed when nothing is encrypted
    assert crypt.render_crypttab(text, ["x"]).count(crypt.BEGIN) == 1
    assert crypt.render_crypttab(text, []) == other


def test_sync_crypttab_only_with_key_file(tmp_path, monkeypatch):
    tab = tmp_path / "crypttab"
    monkeypatch.setattr(crypt, "KEY_FILE", str(tmp_path / "array.key"))
    assert crypt.sync_crypttab([LUKS], str(tab)) is False
    assert not tab.exists()
    (tmp_path / "array.key").write_bytes(b"k" * 64)
    assert crypt.sync_crypttab([LUKS], str(tab)) is True
    assert f"luks-{LUKS} UUID={LUKS}" in tab.read_text()
    assert crypt.sync_crypttab([LUKS], str(tab)) is False
    (tmp_path / "array.key").unlink()
    assert crypt.sync_crypttab([LUKS], str(tab)) is True
    assert tab.read_text() == ""


def test_key_file_created_private_and_wiped(tmp_path):
    key = tmp_path / "luks/array.key"
    crypt.ensure_key_file(str(key))
    assert key.stat().st_mode & 0o777 == 0o400
    assert len(key.read_bytes()) == crypt.KEY_BYTES
    first = key.read_bytes()
    crypt.ensure_key_file(str(key))
    assert key.read_bytes() == first
    crypt.drop_key_file(str(key))
    assert not key.exists()


def test_encrypted_disk_unit_waits_for_the_container():
    unit = units.disk_unit(enc_disk())
    esc = LUKS.replace("-", "\\x2d")
    assert f"After=systemd-cryptsetup@luks\\x2d{esc}.service\n" in unit
    assert f"ConditionPathExists=/dev/mapper/luks-{LUKS}\n" in unit
    assert "What=/dev/disk/by-uuid/fs-1\n" in unit
    plain = units.disk_unit(enc_disk(luks=""))
    assert "ConditionPathExists" not in plain and "cryptsetup" not in plain


def test_state_keeps_luks_and_reads_old_files():
    st = state.State(disks=[enc_disk()])
    again = state.State.from_dict(json.loads(json.dumps(st.to_dict())))
    assert again.disks[0].luks == LUKS
    assert again.disks[0].mapper == f"/dev/mapper/luks-{LUKS}"
    old = {"disks": [{"uuid": "u", "role": "data", "index": 1, "fstype": "ext4"}]}
    assert state.State.from_dict(old).disks[0].luks == ""


@pytest.fixture
def fake(monkeypatch):
    """disk with fake cryptsetup and systemd: records what happens."""
    st = state.State(disks=[enc_disk(1, uuid="fs-1", luks="L1"), enc_disk(2, uuid="fs-2",
                                                                            luks="L2"),
                            state.Disk(uuid="plain", role="data", index=3, fstype="ext4")])
    log = {"opened": [], "started": [], "changed": [], "present": {"L1", "L2"}, "open": set(),
           "key": "richtiges Passwort"}
    monkeypatch.setattr(state, "load", lambda *a, **k: st)
    monkeypatch.setattr(state, "locked", lambda *a, **k: contextlib.nullcontext())
    monkeypatch.setattr(crypt, "is_present", lambda u: u in log["present"])
    monkeypatch.setattr(crypt, "is_open", lambda u: u in log["open"])

    def open_with_key(device, luks, key):
        if key != log["key"]:
            return False
        log["opened"].append(luks)
        log["open"].add(luks)
        return True

    monkeypatch.setattr(crypt, "open_with_key", open_with_key)
    monkeypatch.setattr(crypt, "key_matches", lambda device, key: key == log["key"])
    monkeypatch.setattr(crypt, "change_key", lambda device, old, new: log["changed"].append(device))
    monkeypatch.setattr(cli, "systemctl", lambda *a: log["started"].append(a))
    monkeypatch.setattr(cli, "prepare_mountpoint", lambda p: None)
    monkeypatch.setattr(cli, "is_mounted", lambda p: True)
    monkeypatch.setattr(cli, "sync_pools", lambda s: None)
    monkeypatch.setattr(cli, "refresh_branch", lambda s, d: log.setdefault("refreshed", []).append(
        d.mountpoint))
    monkeypatch.setattr(cli.subprocess, "run", lambda *a, **k: None)
    return st, log


def stdin(monkeypatch, text):
    monkeypatch.setattr(cli.sys, "stdin", io.TextIOWrapper(io.BytesIO(text.encode())))


def test_unlock_opens_and_mounts(fake, monkeypatch, capsys):
    _, log = fake
    log["present"] = {"L1"}  # L2 is not connected
    stdin(monkeypatch, "richtiges Passwort\n")
    assert cli.main(["unlock", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out == {"opened": ["/mnt/disk1"], "already": [], "missing": ["/mnt/disk2"],
                   "failed": []}
    assert ("start", "mnt-disk1.mount") in log["started"]
    assert log["refreshed"] == ["/mnt/disk1"]


def test_unlock_wrong_passphrase(fake, monkeypatch, capsys):
    _, log = fake
    stdin(monkeypatch, "falsches Passwort\n")
    assert cli.main(["unlock"]) == 1
    assert "wrong passphrase" in capsys.readouterr().err
    assert log["opened"] == [] and log["started"] == []


def test_passphrase_change_checks_old_first(fake, monkeypatch, capsys):
    _, log = fake
    stdin(monkeypatch, "falsches Passwort\nneues Passwort\n")
    assert cli.main(["passphrase"]) == 1
    assert log["changed"] == []
    stdin(monkeypatch, "richtiges Passwort\nneues Passwort\n")
    assert cli.main(["passphrase"]) == 0
    assert log["changed"] == ["/dev/disk/by-uuid/L1", "/dev/disk/by-uuid/L2"]


def test_new_encrypted_disk_needs_the_array_passphrase(fake):
    st, _ = fake
    cli.check_array_key(st, "richtiges Passwort")
    with pytest.raises(cli.DiskError, match="differs"):
        cli.check_array_key(st, "anderes Passwort")
