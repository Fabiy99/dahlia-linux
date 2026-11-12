import json
import os
import threading

import pytest
from dahlia_agent import actions, client, files, protocol, server
from test_agent import FakeRunner, err_code, run

DONE = "ActiveState=active\nSubState=exited\nResult=success\nExecMainStatus=0\n"
BUSY = "ActiveState=activating\nSubState=start\nResult=success\nExecMainStatus=0\n"
GONE = "ActiveState=inactive\nSubState=dead\nResult=success\n"


@pytest.fixture
def fctx(tmp_path, monkeypatch):
    monkeypatch.setattr(actions.os.path, "ismount", lambda p: True)
    pool = tmp_path / "user"
    (pool / "Filme/Kino").mkdir(parents=True)
    (pool / "Filme/a.mkv").write_bytes(b"x" * 10)
    (pool / "Filme/Ärger & Co.txt").write_text("hallo")
    (pool / "appdata/kuma").mkdir(parents=True)
    (pool / "Filme/out").symlink_to(tmp_path)
    (tmp_path / "secret").write_text("root only")
    runner = FakeRunner({("systemctl", "show", files.UNIT): (0, GONE, "")})
    return actions.Context(runner=runner, run_dir=tmp_path / "run", pool=pool,
                           system=tmp_path / "srv", remotes=tmp_path / "remotes")


def test_list_folders_first_links_not_followed(fctx):
    got = run(fctx, "files.list", {"path": "Filme"})["result"]
    assert [(e["name"], e["kind"]) for e in got["entries"]] == [
        ("Kino", "dir"), ("a.mkv", "file"), ("Ärger & Co.txt", "file"), ("out", "link")]
    assert got["entries"][1]["size"] == 10 and got["entries"][0]["size"] is None
    assert got["writable"] is True
    assert run(fctx, "files.list")["result"]["writable"] is False
    assert run(fctx, "files.list", {"path": "appdata"})["result"]["writable"] is False
    assert err_code(run(fctx, "files.list", {"path": "Filme/out"})) == protocol.FAILED
    for bad in ("../etc", "Filme/..", "/etc", "Filme//Kino", "a\nb"):
        assert err_code(run(fctx, "files.list", {"path": bad})) == protocol.INVALID_PARAMS, bad


def test_open_passes_descriptor_and_refuses_links(fctx):
    reply = run(fctx, "files.open", {"root": "/mnt/user", "path": "Filme/Ärger & Co.txt"})
    assert reply["result"] == {"name": "Ärger & Co.txt", "size": 5}
    with os.fdopen(reply["fd"], "rb") as f:
        assert f.read() == b"hallo"
    assert err_code(run(fctx, "files.open", {"root": "/mnt/user", "path": "Filme/out/secret"})) \
        == protocol.FAILED
    assert err_code(run(fctx, "files.open", {"root": "/mnt/user", "path": "Filme/Kino"})) \
        == protocol.FAILED


def test_create_rename_and_write_rules(fctx):
    p = {"root": "/mnt/user"}
    assert run(fctx, "files.create_dir", {**p, "path": "Filme", "name": "Neu – 2026"})["ok"]
    assert (fctx.pool / "Filme/Neu – 2026").is_dir()
    assert err_code(run(fctx, "files.create_dir", {**p, "path": "Filme", "name": "Kino"})) \
        == protocol.FAILED
    assert run(fctx, "files.rename", {**p, "path": "Filme/a.mkv", "name": "b.mkv"})["ok"]
    assert (fctx.pool / "Filme/b.mkv").exists()
    assert err_code(run(fctx, "files.rename", {**p, "path": "Filme/b.mkv", "name": "Kino"})) \
        == protocol.FAILED
    # top level and appdata are not changed here
    assert err_code(run(fctx, "files.rename", {**p, "path": "Filme", "name": "X"})) \
        == protocol.INVALID_PARAMS
    assert err_code(run(fctx, "files.create_dir", {**p, "path": "appdata", "name": "x"})) \
        == protocol.INVALID_PARAMS
    assert err_code(run(fctx, "files.rename", {**p, "path": "Filme/b.mkv", "name": "a/b"})) \
        == protocol.INVALID_PARAMS
    assert err_code(run(fctx, "files.create_dir", {**p, "path": "Filme/out", "name": "x"})) \
        == protocol.FAILED


def test_upload_finish_and_abort(fctx):
    p = {"root": "/mnt/user", "path": "Filme/Kino", "name": "neu.txt"}
    reply = run(fctx, "files.upload", p)
    tmp = reply["result"]["tmp"]
    os.write(reply["fd"], b"daten")
    os.close(reply["fd"])
    assert run(fctx, "files.upload_finish", {**p, "tmp": tmp})["ok"]
    target = fctx.pool / "Filme/Kino/neu.txt"
    assert target.read_bytes() == b"daten"
    assert oct(target.stat().st_mode & 0o777) == "0o664"
    # existing name: refused unless overwrite
    assert err_code(run(fctx, "files.upload", p)) == protocol.FAILED
    reply = run(fctx, "files.upload", {**p, "overwrite": True})
    os.close(reply["fd"])
    run(fctx, "files.upload_abort", {"root": "/mnt/user", "path": "Filme/Kino",
                                      "tmp": reply["result"]["tmp"]})
    assert sorted(os.listdir(fctx.pool / "Filme/Kino")) == ["neu.txt"]
    assert err_code(run(fctx, "files.upload", {**p, "path": "Filme", "name": "Kino",
                                               "overwrite": True})) == protocol.FAILED
    assert err_code(run(fctx, "files.upload_finish", {**p, "tmp": "../../etc/passwd"})) \
        == protocol.INVALID_PARAMS


def test_start_checks_and_writes_spec(fctx):
    p = {"root": "/mnt/user"}
    r = run(fctx, "files.start", {**p, "op": "copy", "paths": ["Filme/a.mkv"],
                                  "dest_root": "/mnt/user", "dest": "Filme/Kino"})
    assert r["result"] == {"started": True, "op": "copy", "count": 1}
    spec = json.loads((fctx.run_dir / "files-job.json").read_text())
    assert spec == {"op": "copy", "sources": [str(fctx.pool / "Filme/a.mkv")],
                    "dest": str(fctx.pool / "Filme/Kino")}
    assert fctx.runner.calls[-1][:3] == ["systemd-run", "--unit", files.UNIT]
    assert fctx.runner.calls[-1][-2:] == ["run", str(fctx.run_dir / "files-job.json")]
    cases = [
        ({"op": "move", "paths": ["Filme"], "dest": "Filme/Kino"}, protocol.INVALID_PARAMS),
        ({"op": "move", "paths": ["Filme/Kino"], "dest": "Filme/Kino"}, protocol.INVALID_PARAMS),
        ({"op": "copy", "paths": ["Filme/a.mkv"], "dest": "Filme"}, protocol.FAILED),
        ({"op": "copy", "paths": ["Filme/a.mkv"], "dest": ""}, protocol.INVALID_PARAMS),
        ({"op": "copy", "paths": ["Filme/a.mkv"], "dest": "appdata/kuma"},
         protocol.INVALID_PARAMS),
        ({"op": "delete", "paths": ["appdata/kuma"]}, protocol.INVALID_PARAMS),
        ({"op": "delete", "paths": ["Filme/nope"]}, protocol.FAILED),
        ({"op": "delete", "paths": []}, protocol.INVALID_PARAMS),
        ({"op": "delete", "paths": [1]}, protocol.INVALID_PARAMS),
    ]
    for params, code in cases:
        assert err_code(run(fctx, "files.start", {**p, **params})) == code, params
    # a finished job (active, exited) does not block the next one
    fctx.runner.answers[("systemctl", "show", files.UNIT)] = (0, DONE, "")
    assert run(fctx, "files.start", {**p, "op": "delete", "paths": ["Filme/a.mkv"]})["ok"]
    fctx.runner.answers[("systemctl", "show", files.UNIT)] = (0, BUSY, "")
    assert err_code(run(fctx, "files.start", {**p, "op": "delete", "paths": ["Filme/a.mkv"]})) \
        == protocol.FAILED


def test_job_state(fctx):
    assert run(fctx, "files.job")["result"]["state"] == "none"
    fctx.run_dir.mkdir()
    (fctx.run_dir / "files-progress.json").write_text(json.dumps(
        {"op": "copy", "done": 5, "total": 10, "current": "/mnt/user/x", "error": ""}))
    fctx.runner.answers[("systemctl", "show", files.UNIT)] = (0, BUSY, "")
    got = run(fctx, "files.job")["result"]
    assert got["state"] == "running" and got["done"] == 5 and got["total"] == 10
    fctx.runner.answers[("systemctl", "show", files.UNIT)] = (0, DONE, "")
    assert run(fctx, "files.job")["result"]["state"] == "done"
    (fctx.run_dir / "files-progress.json").write_text(json.dumps(
        {"op": "copy", "done": 5, "total": 10, "current": "", "error": "No space left"}))
    got = run(fctx, "files.job")["result"]
    assert got["state"] == "failed" and got["error"] == "No space left"


def test_job_copy_move_delete(tmp_path):
    src = tmp_path / "a"
    (src / "sub").mkdir(parents=True)
    (src / "sub/f.bin").write_bytes(b"1" * 3000)
    (src / "link").symlink_to("/etc/passwd")
    os.chmod(src / "sub/f.bin", 0o640)
    dest = tmp_path / "d"
    dest.mkdir()
    prog = tmp_path / "files-progress.json"
    files.run_job({"op": "copy", "sources": [str(src)], "dest": str(dest)}, prog)
    assert (dest / "a/sub/f.bin").read_bytes() == b"1" * 3000
    assert oct((dest / "a/sub/f.bin").stat().st_mode & 0o777) == "0o640"
    assert os.readlink(dest / "a/link") == "/etc/passwd"
    state = json.loads(prog.read_text())
    assert state["done"] == state["total"] == 3000 and state["error"] == ""
    other = tmp_path / "o"
    other.mkdir()
    files.run_job({"op": "move", "sources": [str(dest / "a")], "dest": str(other)}, prog)
    assert not (dest / "a").exists() and (other / "a/sub/f.bin").exists()
    files.run_job({"op": "delete", "sources": [str(other / "a")], "dest": None}, prog)
    assert list(other.iterdir()) == [] and os.path.exists("/etc/passwd")
    # a name that turned up meanwhile is not overwritten
    (dest / "x").write_text("old")
    (other / "x").write_text("new")
    with pytest.raises(FileExistsError):
        files.run_job({"op": "copy", "sources": [str(other / "x")], "dest": str(dest)}, prog)
    assert (dest / "x").read_text() == "old"
    assert "already exists" in json.loads(prog.read_text())["error"]


def test_move_across_filesystems_copies_then_removes(tmp_path, monkeypatch):
    src = tmp_path / "f.txt"
    src.write_text("x")
    dest = tmp_path / "d"
    dest.mkdir()

    def exdev(*_a):
        raise OSError(18, "Invalid cross-device link")

    monkeypatch.setattr(files.os, "rename", exdev)
    files.run_job({"op": "move", "sources": [str(src)], "dest": str(dest)},
                  tmp_path / "p.json")
    assert not src.exists() and (dest / "f.txt").read_text() == "x"


def test_descriptor_travels_over_the_socket(tmp_path, fctx):
    path = str(tmp_path / "agent.sock")
    sock = server.bind_socket(path, None)
    srv = server.Server(sock, fctx, lambda pid, uid, gid: True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    result, fd = client.call_fd("files.open", {"root": "/mnt/user", "path": "Filme/a.mkv"},
                                path=path, timeout=5)
    with os.fdopen(fd, "rb") as f:
        assert f.read() == b"x" * 10
    assert result == {"name": "a.mkv", "size": 10}
    # plain call() closes a descriptor it did not ask for
    assert client.call("files.open", {"root": "/mnt/user", "path": "Filme/a.mkv"},
                       path=path, timeout=5)["size"] == 10
    with pytest.raises(protocol.AgentError):
        client.call_fd("files.list", {}, path=path, timeout=5)
    sock.close()


def test_main_usage():
    assert files.main([]) == 2
