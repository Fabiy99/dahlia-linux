import json
import os
from pathlib import Path

import pytest

pytest.importorskip("yaml")

from dahlia_agent import apps  # noqa: E402
from dahlia_agent.apps import AppError, Apps, Host, Paths, parse_template  # noqa: E402

WHOAMI = """\
schema: 1
id: whoami
name: Whoami
description:
  de: Zeigt die Anfrage an.
  en: Shows the request.
image: traefik/whoami:v1.11.0
web: {port: 80, public_port: 8099}
volumes:
  - {container: /config, appdata: config}
  - {container: /media, share: media, readonly: true}
environment:
  TZ: "{TZ}"
  PUID: "{PUID}"
  NAME: plain
"""


def tpl(**changes):
    import yaml

    data = yaml.safe_load(WHOAMI)
    for k, v in changes.items():
        if v is None:
            data.pop(k, None)
        else:
            data[k] = v
    return yaml.safe_dump(data)


class FakeDocker:
    def __init__(self):
        self.calls = []
        self.fail = None

    def compose(self, app_dir, *args, timeout=1800):
        self.calls.append((Path(app_dir).name, args))
        if self.fail and args[0] == self.fail:
            raise AppError(f"docker compose {args[0]}: boom")
        return ""

    def state(self, app_id):
        return "running"


@pytest.fixture
def env(tmp_path):
    paths = Paths(
        apps=tmp_path / "apps",
        catalog=tmp_path / "catalog",
        pool=tmp_path / "user",
        nginx=tmp_path / "nginx",
        config=tmp_path / "apps.json",
    )
    paths.pool.mkdir()
    paths.nginx.mkdir()
    paths.catalog.mkdir()
    (paths.catalog / "whoami.yaml").write_text(WHOAMI)
    reloads = []
    a = Apps(
        paths=paths,
        docker=FakeDocker(),
        host=Host(tz="Europe/Berlin", uid=os.getuid(), gid=os.getgid()),
        nginx_reload=lambda: reloads.append(1),
        port_check=lambda port, protocol="tcp": True,
        mounted=lambda p: True,
    )
    a.reloads = reloads
    return a


# --- template validation ------------------------------------------------------


def test_parse_ok():
    t = parse_template(WHOAMI, "whoami")
    assert t.web_port == 80
    assert t.public_port == 8099
    assert t.volumes[1].share == "media"
    assert t.volumes[1].readonly is True


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"schema": 2}, "schema"),
        ({"id": "Bad_ID"}, "id"),
        ({"image": "traefik/whoami"}, "image"),
        ({"image": "traefik/whoami:latest"}, "latest"),
        ({"image": "traefik/whoami:v1; rm -rf /"}, "image"),
        ({"privileged": True}, "unknown fields privileged"),
        ({"network_mode": "host"}, "unknown fields"),
        ({"web": {"port": 80, "public_port": 443}}, "reserved"),
        ({"web": {"port": 80, "public_port": 41500}}, "reserved"),
        ({"web": {"port": 80}}, "missing public_port"),
        ({"volumes": [{"container": "/x", "appdata": "../etc"}]}, "simple directory"),
        ({"volumes": [{"container": "/x", "share": "a/b"}]}, "simple directory"),
        ({"volumes": [{"container": "relative", "appdata": "x"}]}, "absolute"),
        ({"volumes": [{"container": "/a/../b", "appdata": "x"}]}, "clean"),
        ({"volumes": [{"container": "/proc/x", "appdata": "x"}]}, "system path"),
        ({"volumes": [{"container": "/x", "appdata": "a", "share": "b"}]}, "exactly one"),
        ({"volumes": [{"container": "/x", "host": "/etc"}]}, "unknown fields host"),
        ({"volumes": [{"container": "/x", "appdata": "a", "readonly": "yes"}]}, "readonly"),
        ({"volumes": [{"container": "/x", "share": "appdata"}]}, "not a share"),
        ({"environment": {"A B": "x"}}, "key"),
        ({"environment": {"A": "{HOME}"}}, "placeholder"),
        ({"environment": {"A": True}}, "string or number"),
        ({"lan_ports": [{"container": 1, "host": 53}]}, "reserved"),
        ({"lan_ports": [{"container": 1, "host": 7359, "protocol": "sctp"}]}, "tcp or udp"),
    ],
)
def test_parse_rejects(changes, message):
    with pytest.raises(AppError, match=message):
        parse_template(tpl(**changes))


def test_parse_rejects_wrong_id_and_yaml():
    with pytest.raises(AppError, match="does not match"):
        parse_template(WHOAMI, "other")
    with pytest.raises(AppError, match="YAML"):
        parse_template("a: [")
    with pytest.raises(AppError):
        parse_template("!!python/object/apply:os.system ['id']")


def test_image_with_digest_and_registry():
    t = parse_template(tpl(image="ghcr.io/foo/bar:1.2@sha256:" + "a" * 64))
    assert t.image.startswith("ghcr.io/")


# --- compose ------------------------------------------------------------------


def test_compose_ports_and_volumes(env):
    t = parse_template(WHOAMI)
    c = apps.compose(t, 41001, env.paths, env.host)
    svc = c["services"]["app"]
    assert c["name"] == "dahlia-whoami"
    assert svc["ports"] == ["127.0.0.1:41001:80/tcp"]
    assert svc["restart"] == "no"
    assert svc["security_opt"] == ["no-new-privileges:true"]
    assert svc["environment"] == {"TZ": "Europe/Berlin", "PUID": str(os.getuid()), "NAME": "plain"}
    sources = {v["target"]: (v["source"], v["read_only"]) for v in svc["volumes"]}
    assert sources["/config"] == (str(env.paths.pool / "appdata/whoami/config"), False)
    assert sources["/media"] == (str(env.paths.pool / "media"), True)
    assert not any(k in svc for k in ("privileged", "network_mode", "cap_add", "devices"))


def test_compose_lan_ports(env):
    t = parse_template(tpl(lan_ports=[{"container": 7359, "host": 7359, "protocol": "udp"}]))
    svc = apps.compose(t, 41001, env.paths, env.host)["services"]["app"]
    assert "0.0.0.0:7359:7359/udp" in svc["ports"]


# --- install and lifecycle ----------------------------------------------------


def test_install(env):
    result = env.install("whoami")
    assert result == {"id": "whoami", "host_port": 41000, "enabled": True}
    d = env.paths.apps / "whoami"
    assert json.loads((d / "compose.json").read_text())["services"]["app"]["ports"] == [
        "127.0.0.1:41000:80/tcp"
    ]
    assert env.docker.calls == [("whoami", ("up", "-d", "--pull", "missing", "--remove-orphans"))]
    assert (env.paths.pool / "appdata/whoami/config").is_dir()
    assert (env.paths.pool / "media").is_dir()
    conf = (env.paths.nginx / "dahlia-app-whoami.conf").read_text()
    assert "listen 8099;" in conf
    assert "proxy_pass http://127.0.0.1:41000;" in conf
    assert (env.paths.nginx / "dahlia-apps-map.conf").exists()
    assert env.reloads
    with pytest.raises(AppError, match="already installed"):
        env.install("whoami")


def test_install_needs_pool(env):
    env.mounted = lambda p: False
    with pytest.raises(AppError, match="not mounted"):
        env.install("whoami")
    assert not (env.paths.apps / "whoami").exists()


def test_install_unknown_app(env):
    with pytest.raises(AppError, match="not in the catalog"):
        env.install("nextcloud")
    with pytest.raises(AppError, match="invalid app id"):
        env.install("../x")


def test_install_failure_cleans_up(env):
    env.docker.fail = "up"
    with pytest.raises(AppError, match="boom"):
        env.install("whoami")
    assert not (env.paths.apps / "whoami").exists()
    assert not (env.paths.nginx / "dahlia-app-whoami.conf").exists()


def test_port_conflict_between_apps(env):
    env.install("whoami")
    other = WHOAMI.replace("id: whoami", "id: other")
    (env.paths.catalog / "other.yaml").write_text(other)
    with pytest.raises(AppError, match="port 8099 is used"):
        env.install("other")
    (env.paths.catalog / "other.yaml").write_text(other.replace("8099", "8100"))
    assert env.install("other")["host_port"] == 41001


def test_host_port_skips_busy_ports(env):
    env.port_check = lambda port, protocol="tcp": port > 41002
    assert env.install("whoami")["host_port"] == 41003


def test_start_stop_enabled(env):
    env.install("whoami")
    env.stop("whoami")
    state = json.loads((env.paths.apps / "whoami/state.json").read_text())
    assert state["enabled"] is False
    env.docker.calls.clear()
    assert env.start_enabled() == 0
    assert env.docker.calls == []
    env.start("whoami")
    env.docker.calls.clear()
    assert env.start_enabled() == 0
    assert env.docker.calls == [("whoami", ("up", "-d"))]


def test_start_enabled_without_pool(env, capsys):
    env.install("whoami")
    env.mounted = lambda p: False
    env.docker.calls.clear()
    assert env.start_enabled() == 1
    assert env.docker.calls == []


def test_remove_keeps_data_unless_asked(env):
    env.install("whoami")
    marker = env.paths.pool / "appdata/whoami/config/keep"
    marker.write_text("x")
    env.remove("whoami")
    assert marker.exists()
    assert not (env.paths.apps / "whoami").exists()
    assert not (env.paths.nginx / "dahlia-app-whoami.conf").exists()
    env.install("whoami")
    env.remove("whoami", delete_data=True)
    assert not (env.paths.pool / "appdata/whoami").exists()
    assert (env.paths.pool / "media").exists()


def test_update_uses_catalog_template(env):
    env.install("whoami")
    newer = WHOAMI.replace("v1.11.0", "v1.12.0")
    (env.paths.catalog / "whoami.yaml").write_text(newer)
    env.docker.calls.clear()
    env.update("whoami")
    assert "v1.12.0" in (env.paths.apps / "whoami/app.yaml").read_text()
    assert env.docker.calls == [("whoami", ("pull",)), ("whoami", ("up", "-d", "--remove-orphans"))]
    assert json.loads((env.paths.apps / "whoami/state.json").read_text())["host_port"] == 41000


def test_list(env):
    env.install("whoami")
    listed = env.list()
    assert listed[0]["id"] == "whoami"
    assert listed[0]["status"] == "running"
    assert listed[0]["enabled"] is True


# --- catalog ------------------------------------------------------------------


def make_catalog(root: Path, apps_text: dict[str, str]):
    (root / "apps").mkdir(parents=True)
    (root / "index.json").write_text(json.dumps({"apps": list(apps_text)}))
    for app_id, text in apps_text.items():
        (root / "apps" / f"{app_id}.yaml").write_text(text)


def test_refresh_catalog_from_file_url(env, tmp_path):
    src = tmp_path / "src"
    make_catalog(src, {"whoami": WHOAMI.replace("v1.11.0", "v2.0.0")})
    env.paths.config.write_text(json.dumps({"catalog": f"file://{src}"}))
    listed = env.catalog(refresh=True)
    assert [a["id"] for a in listed] == ["whoami"]
    assert "v2.0.0" in (env.paths.catalog / "whoami.yaml").read_text()


def test_refresh_catalog_keeps_old_on_bad_template(env, tmp_path):
    src = tmp_path / "src"
    make_catalog(src, {"whoami": WHOAMI, "evil": WHOAMI.replace("id: whoami", "id: evil")
                       + "privileged: true\n"})
    env.paths.config.write_text(json.dumps({"catalog": f"file://{src}"}))
    with pytest.raises(AppError, match="privileged"):
        env.catalog(refresh=True)
    assert sorted(f.name for f in env.paths.catalog.iterdir()) == ["whoami.yaml"]


def test_catalog_url_must_be_https_or_file(env):
    env.paths.config.write_text(json.dumps({"catalog": "http://example.com/"}))
    with pytest.raises(AppError, match="https"):
        env.catalog(refresh=True)


def test_fetch_rejects_other_schemes():
    with pytest.raises(AppError, match="refusing"):
        apps.fetch("ftp://example.com/index.json")


def test_cli_check(tmp_path, capsys):
    f = tmp_path / "whoami.yaml"
    f.write_text(WHOAMI)
    assert apps.main(["check", str(f)]) == 0
    assert json.loads(capsys.readouterr().out)["id"] == "whoami"
    f.write_text(WHOAMI + "privileged: true\n")
    assert apps.main(["check", str(f)]) == 1
