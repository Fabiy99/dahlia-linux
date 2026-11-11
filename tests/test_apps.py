import hashlib
import json
import os
from pathlib import Path

import pytest

pytest.importorskip("yaml")

from dahlia_agent import apps, domains  # noqa: E402
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
        self.states = {}
        self.removed = []

    def compose(self, app_dir, *args, timeout=1800):
        self.calls.append((Path(app_dir).name, args))
        if self.fail and args[0] == self.fail:
            raise AppError(f"docker compose {args[0]}: boom")
        return ""

    def state(self, container):
        return self.states.get(container, "running")

    def remove_image(self, image):
        self.removed.append(image)


@pytest.fixture
def env(tmp_path):
    paths = Paths(
        apps=tmp_path / "apps",
        catalog=tmp_path / "catalog",
        pool=tmp_path / "user",
        config=tmp_path / "apps.json",
        system=tmp_path / "srv",
    )
    dpaths = domains.Paths(
        config=tmp_path / "domains.json",
        tls=domains.TlsPaths(tmp_path / "tls"),
        nginx=tmp_path / "nginx",
        aliases=tmp_path / "aliases.json",
    )
    paths.pool.mkdir()
    dpaths.nginx.mkdir()
    paths.catalog.mkdir()
    (paths.catalog / "whoami.yaml").write_text(WHOAMI)
    reloads = []
    firewall_calls = []
    a = Apps(
        paths=paths,
        docker=FakeDocker(),
        host=Host(tz="Europe/Berlin", uid=os.getuid(), gid=os.getgid()),
        reload_firewall=lambda: firewall_calls.append(1),
        naming=domains.Domains(
            paths=dpaths,
            host=domains.Host("dahlia", ["192.168.1.5"]),
            nginx_reload=lambda: reloads.append(1),
            mdns_reload=lambda: None,
        ),
        port_check=lambda port, protocol="tcp": True,
        mounted=lambda p: True,
    )
    a.reloads = reloads
    a.firewall_calls = firewall_calls
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
        ({"schema": 4}, "schema"),
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
        ({"lan_ports": [{"container": 1, "host": 22}]}, "reserved"),
        ({"lan_ports": [{"container": 1, "host": 8099}]}, "web port"),
        ({"lan_ports": [{"container": 1, "host": 7}, {"container": 2, "host": 7}]}, "twice"),
        ({"web": {"port": 80, "public_port": 53}}, "reserved"),
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
    assert result == {"id": "whoami", "host_port": 41000, "enabled": True,
                      "location": str(env.paths.pool)}
    d = env.paths.apps / "whoami"
    assert json.loads((d / "compose.json").read_text())["services"]["app"]["ports"] == [
        "127.0.0.1:41000:80/tcp"
    ]
    assert env.docker.calls == [("whoami", ("up", "-d", "--pull", "missing", "--remove-orphans"))]
    assert (env.paths.pool / "appdata/whoami/config").is_dir()
    assert (env.paths.pool / "media").is_dir()
    conf = (env.naming.paths.nginx / "dahlia-app-whoami.conf").read_text()
    assert "listen 8099;" in conf
    assert "proxy_pass http://127.0.0.1:41000;" in conf
    assert (env.naming.paths.nginx / "dahlia-apps-map.conf").exists()
    assert env.reloads and env.firewall_calls
    with pytest.raises(AppError, match="already installed"):
        env.install("whoami")


def test_install_on_array_needs_pool(env):
    env.mounted = lambda p: False
    with pytest.raises(AppError, match="not mounted"):
        env.install("whoami", location=str(env.paths.pool))
    with pytest.raises(AppError, match="not mounted"):
        env.install("whoami", {"media": f"{env.paths.pool}/Filme"}, location=str(env.paths.system))
    assert not (env.paths.apps / "whoami").exists()


def test_install_without_array_goes_to_system(env):
    env.mounted = lambda p: False
    assert env.install("whoami")["location"] == str(env.paths.system)
    app = json.loads((env.paths.apps / "whoami/compose.json").read_text())["services"]["app"]
    sources = {v["target"]: v["source"] for v in app["volumes"]}
    assert sources == {"/config": str(env.paths.system / "appdata/whoami/config"),
                       "/media": str(env.paths.system / "media")}
    assert (env.paths.system / "appdata/whoami/config").is_dir()
    # Starts at boot without the array, update keeps the location.
    assert env.start_enabled() == 0
    env.update("whoami")
    app = json.loads((env.paths.apps / "whoami/compose.json").read_text())["services"]["app"]
    assert {v["target"]: v["source"] for v in app["volumes"]} == sources
    assert env.list()[0]["location"] == str(env.paths.system)
    env.remove("whoami", delete_data=True)
    assert not (env.paths.system / "appdata/whoami").exists()


def test_system_app_with_share_on_array(env):
    env.install("whoami", {"media": f"{env.paths.pool}/Filme"}, location=str(env.paths.system))
    state = json.loads((env.paths.apps / "whoami/state.json").read_text())
    assert state["location"] == str(env.paths.system)
    assert state["folders"] == {"media": f"{env.paths.pool}/Filme"}
    assert (env.paths.pool / "Filme").is_dir()
    env.docker.calls.clear()
    env.mounted = lambda p: False
    assert env.start_enabled() == 1
    assert env.docker.calls == []
    with pytest.raises(AppError, match="not mounted"):
        env.start("whoami")


def test_old_apps_stay_on_array(env):
    env.install("whoami")
    d = env.paths.apps / "whoami"
    state = json.loads((d / "state.json").read_text())
    del state["location"]
    (d / "state.json").write_text(json.dumps(state))
    env.update("whoami")
    app = json.loads((d / "compose.json").read_text())["services"]["app"]
    assert {v["target"]: v["source"] for v in app["volumes"]}["/config"] == str(
        env.paths.pool / "appdata/whoami/config")


def test_install_rejects_unknown_location(env):
    with pytest.raises(AppError, match="unknown location"):
        env.install("whoami", location="/media/usb")


def test_precheck(env):
    assert env.precheck("install", "whoami")["ok"]
    assert env.docker.calls == []
    with pytest.raises(AppError, match="not in the catalog"):
        env.precheck("install", "nextcloud")
    with pytest.raises(AppError, match="not installed"):
        env.precheck("update", "whoami")
    env.install("whoami")
    with pytest.raises(AppError, match="already installed"):
        env.precheck("install", "whoami")
    assert env.precheck("update", "whoami")["ok"]
    env.mounted = lambda p: False
    with pytest.raises(AppError, match="add a data disk"):
        env.precheck("update", "whoami")


def test_install_with_chosen_folder_survives_update(env):
    (env.paths.pool / "Filme").mkdir()
    env.install("whoami", {"media": f"{env.paths.pool}/Filme/Kino"})
    app = json.loads((env.paths.apps / "whoami/compose.json").read_text())["services"]["app"]
    sources = {v["target"]: v["source"] for v in app["volumes"]}
    assert sources["/media"] == str(env.paths.pool / "Filme/Kino")
    assert (env.paths.pool / "Filme/Kino").is_dir() and not (env.paths.pool / "media").exists()
    assert json.loads((env.paths.apps / "whoami/state.json").read_text())["folders"] == {
        "media": f"{env.paths.pool}/Filme/Kino"}
    env.update("whoami")
    app = json.loads((env.paths.apps / "whoami/compose.json").read_text())["services"]["app"]
    assert {v["target"]: v["source"] for v in app["volumes"]}["/media"] == sources["/media"]


@pytest.mark.parametrize("folders,why", [
    ({"media": "../etc"}, "not a folder"),
    ({"media": "/etc"}, "not a folder"),
    ({"media": "Filme"}, "not a folder"),
    ({"media": "{pool}/Filme/../../etc"}, "not a folder"),
    ({"media": "{pool}"}, "not a folder"),
    ({"media": "{pool}/appdata/other"}, "app data"),
    ({"media": "{system}/appdata/other"}, "app data"),
    ({"config": "{pool}/x"}, "no share config"),
    ({"media": "{pool}/a\nb"}, "not a folder"),
])
def test_install_rejects_bad_folders(env, folders, why):
    folders = {k: v.format(pool=env.paths.pool, system=env.paths.system)
               for k, v in folders.items()}
    with pytest.raises(AppError, match=why):
        env.precheck("install", "whoami", folders)
    with pytest.raises(AppError, match=why):
        env.install("whoami", folders)
    assert not (env.paths.apps / "whoami").exists()


def test_install_rejects_symlink_out_of_pool(env, tmp_path):
    (env.paths.pool / "escape").symlink_to(tmp_path)
    with pytest.raises(AppError, match="outside"):
        env.install("whoami", {"media": f"{env.paths.pool}/escape/x"})


def test_catalog_lists_shares(env):
    app = next(a for a in env.catalog()["apps"] if a["id"] == "whoami")
    assert app["shares"] == [{"name": "media", "containers": ["/media"], "readonly": True}]


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
    assert not (env.naming.paths.nginx / "dahlia-app-whoami.conf").exists()


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
    assert not (env.naming.paths.nginx / "dahlia-app-whoami.conf").exists()
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
    assert listed[0]["urls"] == {
        "mdns": "https://whoami.local",
        "port": "http://dahlia.local:8099",
    }


def test_install_adds_alias_and_proxy(env):
    env.install("whoami")
    assert apps.proxies(env.paths) == [domains.AppProxy("whoami", 41000, 8099)]
    assert json.loads(env.naming.paths.aliases.read_text()) == {"aliases": ["whoami"]}
    env.remove("whoami")
    assert json.loads(env.naming.paths.aliases.read_text()) == {"aliases": []}


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
    assert [a["id"] for a in listed["apps"]] == ["whoami"]
    assert listed["refreshed"].endswith("Z")
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


# --- schema 2: stacks ---------------------------------------------------------

STACK = """\
schema: 2
id: notes
name: Notes
category: productivity
description: {de: Notizen., en: Notes.}
website: https://example.org/notes
notes: {de: Erster Login legt den Admin an., en: The first login creates the admin.}
web: {port: 3000, public_port: 3010}
login: {user: admin, password: "{SECRET_ADMIN}"}
services:
  db:
    image: postgres:17.6
    environment:
      POSTGRES_PASSWORD: "{SECRET_DB}"
    volumes:
      - {container: /var/lib/postgresql/data, appdata: db}
  app:
    image: example/notes:2.1.0
    depends_on: [db, cache]
    environment:
      DATABASE_URL: "postgres://notes:{SECRET_DB}@db/notes"
      ADMIN_PASSWORD: "{SECRET_ADMIN}"
      BASE: "https://notes.{DOMAIN}"
      HOST: "{HOSTNAME}"
    user: "{PUID}:{PGID}"
    volumes:
      - {container: /data, appdata: data}
  cache:
    image: redis:8.2.1-alpine
    command: [redis-server, --requirepass, "{SECRET_CACHE}"]
"""


def stack(**changes):
    import yaml

    data = yaml.safe_load(STACK)
    for path, v in changes.items():
        node = data
        keys = path.split("__")
        for k in keys[:-1]:
            node = node[k]
        if v is None:
            node.pop(keys[-1], None)
        else:
            node[keys[-1]] = v
    return yaml.safe_dump(data, sort_keys=False)


@pytest.fixture
def stack_env(env):
    (env.paths.catalog / "notes.yaml").write_text(STACK)
    return env


def test_parse_stack():
    t = parse_template(STACK, "notes")
    assert list(t.services) == ["app", "db", "cache"]
    assert t.image == "example/notes:2.1.0"
    assert t.version == "2.1.0"
    assert t.category == "productivity"
    assert t.secrets == {"SECRET_DB", "SECRET_ADMIN", "SECRET_CACHE"}
    assert [s["name"] for s in t.summary()["services"]] == ["app", "db", "cache"]


def test_schema1_is_a_single_service_stack():
    t = parse_template(WHOAMI)
    assert list(t.services) == ["app"]
    assert t.category == "other"
    assert t.schema == 1


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"services": {"db": {"image": "postgres:17.6"}}}, "service 'app'"),
        ({"category": "games"}, "category"),
        ({"category": None}, "missing category"),
        ({"image": "x:1"}, "unknown fields image"),
        ({"services__app__privileged": True}, "unknown fields privileged"),
        ({"services__app__network_mode": "host"}, "unknown fields network_mode"),
        ({"services__db__cap_add": ["NET_ADMIN"]}, "unknown fields cap_add"),
        ({"services__db__devices": ["/dev/sda"]}, "unknown fields devices"),
        ({"services__app__depends_on": ["nope"]}, "unknown service nope"),
        ({"services__app__depends_on": ["app"]}, "unknown service app"),
        ({"services__db__depends_on": ["app"]}, "loop"),
        ({"services__cache__image": "redis:latest"}, "latest"),
        ({"services__cache__command": "redis-server"}, "list"),
        ({"services__cache__command": ["a", "{HOME}"]}, "placeholder"),
        ({"services__app__user": "root; id"}, "user"),
        ({"services__app__environment": {"A": "{SECRET_lower}"}}, "placeholder"),
        ({"website": "http://example.org"}, "https"),
        ({"login": {"user": "a", "token": "b"}}, "unknown fields token"),
        ({"services__Bad": {"image": "x:1"}}, "service name"),
    ],
)
def test_parse_stack_rejects(changes, message):
    with pytest.raises(AppError, match=message):
        parse_template(stack(**changes))


def test_parse_stack_max_services():
    services = {"app": {"image": "x:1"}} | {f"s{i}": {"image": "x:1"} for i in range(8)}
    with pytest.raises(AppError, match="at most"):
        parse_template(stack(services=services))


def test_compose_stack(stack_env):
    env = stack_env
    t = parse_template(STACK)
    sec = {"SECRET_DB": "d" * 32, "SECRET_ADMIN": "a" * 32, "SECRET_CACHE": "c" * 32}
    c = apps.compose(t, 41005, env.paths, Host("Europe/Berlin", 1001, 1002, "nas"),
                     sec)
    app, db, cache = (c["services"][n] for n in ("app", "db", "cache"))
    assert app["container_name"] == "dahlia-notes"
    assert db["container_name"] == "dahlia-notes-db"
    assert app["ports"] == ["127.0.0.1:41005:3000/tcp"]
    assert "ports" not in db and "ports" not in cache
    assert app["depends_on"] == ["db", "cache"]
    assert app["user"] == "1001:1002"
    assert app["environment"]["DATABASE_URL"] == f"postgres://notes:{'d' * 32}@db/notes"
    # {DOMAIN} is "local": https://<app>.{DOMAIN} is the app's mDNS alias.
    assert app["environment"]["BASE"] == "https://notes.local"
    assert app["environment"]["HOST"] == "nas"
    assert db["environment"]["POSTGRES_PASSWORD"] == "d" * 32
    assert cache["command"] == ["redis-server", "--requirepass", "c" * 32]
    assert db["volumes"][0]["source"] == str(env.paths.pool / "appdata/notes/db")
    for svc in c["services"].values():
        assert svc["restart"] == "no"
        assert svc["security_opt"] == ["no-new-privileges:true"]
        assert not any(k in svc for k in ("privileged", "network_mode", "cap_add", "devices"))
    with pytest.raises(AppError, match="no value"):
        apps.compose(t, 41005, env.paths, env.host, {})


def test_install_stack_keeps_secrets_across_updates(stack_env):
    env = stack_env
    env.install("notes")
    d = env.paths.apps / "notes"
    sec = json.loads((d / "secrets.json").read_text())
    assert set(sec) == {"SECRET_DB", "SECRET_ADMIN", "SECRET_CACHE"}
    assert all(len(v) == 32 for v in sec.values())
    assert (d / "secrets.json").stat().st_mode & 0o777 == 0o600
    assert (d / "compose.json").stat().st_mode & 0o777 == 0o600
    assert (env.paths.pool / "appdata/notes/db").is_dir()
    # A newer template with one more secret keeps the old ones.
    newer = STACK.replace("2.1.0", "2.2.0").replace(
        'HOST: "{HOSTNAME}"', 'HOST: "{HOSTNAME}"\n      KEY: "{SECRET_KEY}"')
    (env.paths.catalog / "notes.yaml").write_text(newer)
    env.update("notes")
    sec2 = json.loads((d / "secrets.json").read_text())
    assert {k: sec2[k] for k in sec} == sec
    assert len(sec2["SECRET_KEY"]) == 32
    compose_env = json.loads((d / "compose.json").read_text())["services"]["app"]["environment"]
    assert compose_env["KEY"] == sec2["SECRET_KEY"]
    assert env.docker.removed == ["example/notes:2.1.0"]


def test_list_shows_update_and_login(stack_env):
    env = stack_env
    env.install("notes")
    listed = env.list()[0]
    assert listed["update"] is None
    assert listed["version"] == "2.1.0"
    sec = json.loads((env.paths.apps / "notes/secrets.json").read_text())
    assert listed["login"] == {"user": "admin", "password": sec["SECRET_ADMIN"]}
    (env.paths.catalog / "notes.yaml").write_text(
        STACK.replace("2.1.0", "2.2.0").replace("postgres:17.6", "postgres:17.7"))
    listed = env.list()[0]
    assert listed["update"] == {
        "version": "2.2.0",
        "images": [
            {"service": "app", "from": "example/notes:2.1.0", "to": "example/notes:2.2.0"},
            {"service": "db", "from": "postgres:17.6", "to": "postgres:17.7"},
        ],
    }
    assert env.updates()[0]["from"] == "2.1.0"
    env.update("notes")
    assert env.list()[0]["update"] is None
    assert env.updates() == []


def test_status_partial(stack_env):
    env = stack_env
    env.install("notes")
    env.docker.states["dahlia-notes-db"] = "exited"
    assert env.list()[0]["status"] == "partial"
    env.docker.states["dahlia-notes"] = "exited"
    assert env.list()[0]["status"] == "exited"


def test_remove_stack(stack_env):
    env = stack_env
    env.install("notes")
    env.remove("notes")
    assert not (env.paths.apps / "notes").exists()
    assert env.docker.calls[-1] == ("notes", ("down",))
    assert sorted(env.docker.removed) == sorted(
        ["example/notes:2.1.0", "postgres:17.6", "redis:8.2.1-alpine"])


def test_update_of_stopped_app_does_not_start_it(stack_env):
    env = stack_env
    env.install("notes")
    env.stop("notes")
    env.docker.calls.clear()
    env.update("notes")
    assert ("notes", ("up", "-d", "--remove-orphans")) not in env.docker.calls
    assert ("notes", ("up", "--no-start", "--remove-orphans")) in env.docker.calls


def test_refresh_from_bundle_skips_newer_schema(env, tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    bundle = {"format": 1, "apps": {"notes": STACK, "whoami": WHOAMI,
                                    "future": "schema: 4\nid: future\n"}}
    (src / "catalog.json").write_text(json.dumps(bundle))
    env.paths.config.write_text(json.dumps({"catalog": f"file://{src}/"}))
    listed = env.catalog(refresh=True)
    assert [a["id"] for a in listed["apps"]] == ["notes", "whoami"]
    assert listed["skipped"] == ["future"]
    assert sorted(f.name for f in env.paths.catalog.iterdir()) == [
        "icons", "meta.json", "notes.yaml", "whoami.yaml"]


SVG = b'<svg xmlns="http://www.w3.org/2000/svg"><circle r="1"/></svg>'
PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 20


def icon_catalog(src, icons):
    """catalog.json with whoami and notes plus the given icon files (name -> bytes)."""
    (src / "icons").mkdir(parents=True, exist_ok=True)
    listed = {}
    for name, data in icons.items():
        (src / "icons" / name).write_bytes(data)
        listed[name.split(".")[0]] = {"file": f"icons/{name}",
                                      "sha256": hashlib.sha256(data).hexdigest()}
    (src / "catalog.json").write_text(json.dumps(
        {"format": 1, "apps": {"notes": STACK, "whoami": WHOAMI}, "icons": listed}))


def test_refresh_catalog_icons(env, tmp_path):
    src = tmp_path / "src"
    icon_catalog(src, {"whoami.svg": SVG, "notes.png": PNG})
    env.paths.config.write_text(json.dumps({"catalog": f"file://{src}/"}))
    listed = {a["id"]: a for a in env.catalog(refresh=True)["apps"]}
    sha = hashlib.sha256(SVG).hexdigest()
    assert listed["whoami"]["icon"] == f"whoami.svg?v={sha[:12]}"
    assert listed["notes"]["icon"].startswith("notes.png?v=")
    assert (env.paths.catalog / "icons/whoami.svg").read_bytes() == SVG

    # Unchanged icons come from the cache, not from the source.
    fetched = []

    def icon_fetcher(url, limit):
        fetched.append(url)
        return apps.fetch_bytes(url, limit)

    icon_catalog(src, {"whoami.svg": SVG, "notes.png": PNG + b"x"})
    apps.refresh_catalog(env.paths, icon_fetcher=icon_fetcher)
    assert fetched == [f"file://{src}/icons/notes.png"]


def test_refresh_catalog_drops_bad_icons(env, tmp_path):
    src = tmp_path / "src"
    icon_catalog(src, {"whoami.svg": SVG, "notes.png": b"<svg/>"})
    data = json.loads((src / "catalog.json").read_text())
    data["icons"]["whoami"]["sha256"] = "0" * 64  # does not match the file
    data["icons"]["future"] = {"file": "icons/future.svg", "sha256": "0" * 64}
    data["icons"]["evil"] = {"file": "../../etc/passwd", "sha256": "0" * 64}
    (src / "catalog.json").write_text(json.dumps(data))
    env.paths.config.write_text(json.dumps({"catalog": f"file://{src}/"}))
    listed = env.catalog(refresh=True)["apps"]
    assert all(a["icon"] is None for a in listed)
    assert list((env.paths.catalog / "icons").iterdir()) == []


def test_refresh_catalog_without_icons_key(env, tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "catalog.json").write_text(json.dumps({"format": 1, "apps": {"whoami": WHOAMI}}))
    env.paths.config.write_text(json.dumps({"catalog": f"file://{src}/"}))
    assert env.catalog(refresh=True)["apps"][0]["icon"] is None


@pytest.mark.parametrize(
    "bundle",
    [
        "not json",
        json.dumps({"format": 2, "apps": {}}),
        json.dumps({"format": 1, "apps": ["whoami"]}),
        json.dumps({"format": 1, "apps": {"../x": WHOAMI}}),
    ],
)
def test_refresh_rejects_bad_bundle(env, tmp_path, bundle):
    src = tmp_path / "src"
    src.mkdir()
    (src / "catalog.json").write_text(bundle)
    env.paths.config.write_text(json.dumps({"catalog": f"file://{src}/"}))
    with pytest.raises(AppError):
        env.catalog(refresh=True)
    assert (env.paths.catalog / "whoami.yaml").exists()


def test_cli_quiet_refresh(env, tmp_path, monkeypatch, capsys):
    src = tmp_path / "src"
    make_catalog(src, {"whoami": WHOAMI.replace("v1.11.0", "v1.12.0")})
    env.paths.config.write_text(json.dumps({"catalog": f"file://{src}"}))
    env.install("whoami")
    monkeypatch.setattr(apps, "Apps", lambda: env)
    assert apps.main(["catalog", "--refresh", "--quiet"]) == 0
    out = capsys.readouterr().out
    assert out.strip() == ("dahlia-apps: catalog has 1 apps, 1 updates available: "
                           "whoami v1.11.0 -> v1.12.0")


# --- GPU ----------------------------------------------------------------------


def gpu_stack(schema=3):
    return STACK.replace("schema: 2", f"schema: {schema}").replace(
        "    image: example/notes:2.1.0\n", "    image: example/notes:2.1.0\n    gpu: true\n")


def test_gpu_needs_schema_3():
    with pytest.raises(AppError, match="gpu needs schema 3"):
        apps.parse_template(gpu_stack(2))
    t = apps.parse_template(gpu_stack())
    assert t.schema == 3 and t.gpu and t.services["app"].gpu
    assert not any(s.gpu for n, s in t.services.items() if n != "app")
    assert t.summary()["gpu"] is True


def test_gpu_must_be_bool():
    with pytest.raises(AppError, match="gpu must be true or false"):
        apps.parse_template(gpu_stack().replace("gpu: true", "gpu: /dev/mem"))


def test_compose_gpu(env):
    t = apps.parse_template(gpu_stack())
    sec = {"SECRET_DB": "d" * 32, "SECRET_ADMIN": "a" * 32, "SECRET_CACHE": "c" * 32}
    with_gpu = Host("Europe/Berlin", 1001, 1002, dri=True, gpu_gids=(993, 44))
    svc = apps.compose(t, 41005, env.paths, with_gpu, sec)["services"]
    assert svc["app"]["devices"] == ["/dev/dri:/dev/dri"]
    assert svc["app"]["group_add"] == ["993", "44"]
    assert all("devices" not in v for k, v in svc.items() if k != "app")
    # No GPU in the server: the app runs on the CPU.
    svc = apps.compose(t, 41005, env.paths, Host("Europe/Berlin", 1001, 1002), sec)["services"]
    assert "devices" not in svc["app"] and "group_add" not in svc["app"]


def test_has_gpu(tmp_path):
    assert not apps.has_gpu(tmp_path / "dri")
    (tmp_path / "dri").mkdir()
    (tmp_path / "dri/card0").touch()
    assert not apps.has_gpu(tmp_path / "dri")
    (tmp_path / "dri/renderD128").touch()
    assert apps.has_gpu(tmp_path / "dri")
