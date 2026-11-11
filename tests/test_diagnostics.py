"""system.diagnostics: bundle contents and what stays out."""

import io
import json
import tarfile

import pytest
from dahlia_agent import actions, diagnostics
from test_agent import FakeRunner, run


@pytest.fixture
def ctx(tmp_path):
    etc, var = tmp_path / "etc", tmp_path / "var"
    (etc / "dahlia").mkdir(parents=True)
    (etc / "dahlia" / "release").write_text("DAHLIA_VERSION=0.9.0\n")
    (etc / "dahlia" / "notify.json").write_text(json.dumps({
        "email": {"host": "mail.example", "password": "hunter2"},
        "ntfy": {"topic": "home", "token": "tk_secret", "url": "https://ntfy.sh"},
        "gotify": {"token": ""}}))
    (etc / "dahlia" / "backup.pass").write_text("repo-password\n")
    (etc / "dahlia" / "backup.json").write_text('{"target": {"type": "folder"}}')
    (etc / "os-release").write_text('PRETTY_NAME="Debian GNU/Linux 13 (trixie)"\n')
    app = var / "apps" / "paperless"
    app.mkdir(parents=True)
    (app / "state.json").write_text('{"host_port": 41001, "enabled": true}')
    (app / "app.yaml").write_text("schema: 2\nid: paperless\n")
    (app / "secrets.json").write_text('{"DB": "app-secret"}')
    (app / "compose.json").write_text('{"env": "app-secret"}')
    log = tmp_path / "log" / "apt"
    log.mkdir(parents=True)
    (log / "history.log").write_text("Start-Date: 2026-11-10\n")
    run_dir = tmp_path / "dashboard-run"
    run_dir.mkdir()
    runner = FakeRunner({
        ("uname",): (0, "Linux nas 6.12.0 x86_64\n", ""),
        ("dahlia-snapraid", "status", "--json"): (0, '{"configured": true, "key": "x"}', ""),
        ("nft",): (1, "", "nft: command not found\n"),
    })
    return actions.Context(runner=runner, proc=tmp_path / "proc", etc=etc, var=var,
                           run_dir=tmp_path / "run", log_dir=tmp_path / "log",
                           dashboard_run=run_dir, hostname=lambda: "nas")


def members(ctx) -> dict[str, str]:
    data = (ctx.dashboard_run / diagnostics.BUNDLE).read_bytes()
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        return {m.name.removeprefix("dahlia-diagnostics/"): tar.extractfile(m).read().decode()
                for m in tar.getmembers()}


def test_bundle_contents(ctx):
    res = run(ctx, "system.diagnostics")["result"]
    assert res["hostname"] == "nas" and res["size"] > 0
    files = members(ctx)
    assert files["system/uname.txt"].startswith("Linux nas")
    assert files["etc-dahlia/release"] == "DAHLIA_VERSION=0.9.0\n"
    assert "trixie" in files["etc/os-release"]
    assert files["apps/paperless/app.yaml"].startswith("schema: 2")
    assert "Start-Date" in files["apt/history.log"]
    assert "[exit code 1]" in files["network/nftables.txt"]
    assert "Dahlia diagnostics from nas" in files["README.txt"]
    assert (ctx.dashboard_run / diagnostics.BUNDLE).stat().st_mode & 0o777 == 0o600


def test_secrets_stay_out(ctx):
    run(ctx, "system.diagnostics")
    files = members(ctx)
    everything = "\n".join(files.values())
    for secret in ("hunter2", "tk_secret", "repo-password", "app-secret"):
        assert secret not in everything
    notify = json.loads(files["etc-dahlia/notify.json"])
    assert notify["email"] == {"host": "mail.example", "password": "***"}
    assert notify["gotify"]["token"] == ""  # empty stays visible: "not set"
    assert json.loads(files["storage/parity.json"]) == {"configured": True, "key": "***"}
    assert not any(n.endswith(("secrets.json", "compose.json", "backup.pass")) for n in files)


def test_redact_nested():
    data = {"a": [{"api_key": "k", "name": "n"}], "private": {"x": 1}, "pass_hint": None}
    assert diagnostics.redact(data) == {"a": [{"api_key": "***", "name": "n"}],
                                        "private": "***", "pass_hint": None}
