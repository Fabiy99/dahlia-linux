#!/bin/bash
# Integration test for Dahlia apps with real Docker, nginx and agent.
#
# A loop disk becomes the array, a local catalog (file://) holds a small
# test app. Through the agent: refresh the catalog, install, check the
# container (port on 127.0.0.1 only, data under /mnt/user/appdata, nginx
# port link from the LAN side), https on <app>.<domain> and <app>.local with
# the local CA, the mDNS alias via Avahi, a domain change, stop, start,
# update, remove. Then a stack (app with PostgreSQL and MariaDB on the
# array): generated secrets, name resolution between services, an update
# found through the catalog, data and secrets kept across it.
#
# Must run as root on a disposable machine with storage, agent,
# docker, nginx, avahi-daemon and avahi-utils installed.
set -euo pipefail

WORK="$(mktemp -d /var/tmp/apps-test.XXXXXX)"
LOOP=""
UUID=""
export DAHLIA_ALLOW_LOOP=1

fail() { echo "FAIL: $*" >&2; diag >&2; exit 1; }
step() { echo; echo "== $*"; }
call() { agent-call --timeout 120 "$@"; }

diag() {
    echo "--- dahlia-app-whoami / dahlia-app-stacktest journal"
    journalctl -u dahlia-app-whoami.service -u dahlia-app-stacktest.service --no-pager \
        | tail -60 || true
    for c in dahlia-stacktest-pg dahlia-stacktest-sql; do
        echo "--- $c log"
        docker logs --tail 30 "$c" 2>&1 || true
    done
    echo "--- mdns journal"
    journalctl -u mdns.service --no-pager | tail -20 || true
    echo "--- avahi: daemon, publish as root and as mdns"
    systemctl is-active avahi-daemon.service || true
    timeout 3 avahi-publish -a -R diag-root.local 10.9.9.9 || true
    timeout 3 setpriv --reuid=mdns --regid=mdns --clear-groups \
        avahi-publish -a -R diag-user.local 10.9.9.8 || true
    journalctl -u dbus.service -u avahi-daemon.service --no-pager | tail -20 || true
    docker ps -a || true
}

cleanup() {
    set +e
    apps remove whoami --delete-data >/dev/null 2>&1
    apps remove stacktest --delete-data >/dev/null 2>&1
    [ -f "$WORK/apps.json.orig" ] && cp "$WORK/apps.json.orig" /etc/dahlia/apps.json
    [ -n "$UUID" ] && disk remove "$UUID" >/dev/null 2>&1
    [ -n "$LOOP" ] && losetup -d "$LOOP"
    rm -rf "$WORK"
}
trap cleanup EXIT

wait_job() {
    local id="${1:-whoami}" state
    for _ in $(seq 1 150); do
        state="$(call apps.job "{\"id\": \"$id\"}")"
        case "$(jq -r .active <<<"$state")" in
            active|activating) sleep 2 ;;
            *) break ;;
        esac
    done
    echo "$state"
    jq -e '.result == "success"' <<<"$state" >/dev/null || fail "app job did not succeed"
}

step "array from a loop disk"
truncate -s 512M "$WORK/disk.img"
LOOP="$(losetup --find --show "$WORK/disk.img")"
mkfs.ext4 -q -L appsdisk "$LOOP"
udevadm trigger --action=change "$LOOP"
udevadm settle
UUID="$(blkid -s UUID -o value "$LOOP")"
disk add "$UUID" --role data
# The default keeps 50G free per disk; the test disk is far smaller.
disk set-minfreespace 10M
findmnt -n /mnt/user >/dev/null || fail "/mnt/user not mounted"

step "local catalog"
mkdir -p "$WORK/catalog/apps"
echo '{"apps": ["whoami", "broken"]}' > "$WORK/catalog/index.json"
cat > "$WORK/catalog/apps/broken.yaml" <<'YAML'
schema: 1
id: broken
name: Broken
description: {de: Image fehlt, en: image does not exist}
image: traefik/whoami:v0.0.0-dahlia-missing
YAML
cat > "$WORK/catalog/apps/whoami.yaml" <<'YAML'
schema: 1
id: whoami
name: Whoami
description: {de: Testapp, en: test app}
image: traefik/whoami:v1.10.3
web: {port: 80, public_port: 8099}
volumes:
  - {container: /config, appdata: config}
  - {container: /media, share: media, readonly: true}
environment: {TZ: "{TZ}", PUID: "{PUID}"}
YAML
cp /etc/dahlia/apps.json "$WORK/apps.json.orig"
printf '{"catalog": "file://%s/catalog/"}\n' "$WORK" > /etc/dahlia/apps.json
call apps.catalog '{"refresh": true}' | jq -e '.apps[0].id == "whoami"' >/dev/null \
    || fail "catalog refresh failed"
cp "$WORK/apps.json.orig" /etc/dahlia/apps.json

step "a failed install reports the reason and can be retried"
for _ in 1 2; do
    call apps.install '{"id": "broken"}' >/dev/null
    for _ in $(seq 1 60); do
        state="$(call apps.job '{"id": "broken"}')"
        case "$(jq -r .active <<<"$state")" in
            active|activating) sleep 2 ;;
            *) break ;;
        esac
    done
    echo "$state"
    jq -e '.result != "success" and (.error | length) > 0' <<<"$state" >/dev/null \
        || fail "failed install not reported with a reason"
    [ ! -e /var/lib/dahlia/apps/broken ] || fail "failed install left files behind"
done

step "install through the agent"
call apps.install '{"id": "whoami"}' >/dev/null
wait_job
[ "$(docker inspect -f '{{.State.Status}}' dahlia-whoami)" = running ] || fail "container not running"
ports="$(docker port dahlia-whoami)"
echo "$ports"
grep -q '^80/tcp -> 127.0.0.1:41000$' <<<"$ports" || fail "web port not on 127.0.0.1:41000"
if grep -q '0.0.0.0' <<<"$ports"; then fail "port published on all interfaces"; fi
[ -d /mnt/user/appdata/whoami/config ] || fail "appdata missing"
[ "$(stat -c %U /mnt/user/appdata/whoami/config)" = apps ] || fail "appdata owner wrong"
docker inspect -f '{{.HostConfig.SecurityOpt}}' dahlia-whoami | grep -q no-new-privileges \
    || fail "no-new-privileges missing"
docker inspect -f '{{.HostConfig.RestartPolicy.Name}}' dahlia-whoami | grep -qx no \
    || fail "restart policy must be no"

step "port link through nginx"
lan_ip="$(hostname -I | awk '{print $1}')"
for _ in $(seq 1 10); do
    curl -sf "http://$lan_ip:8099/" -o "$WORK/whoami.txt" && break
    sleep 1
done
grep -q '^Hostname:' "$WORK/whoami.txt" || fail "app not reachable on $lan_ip:8099"
if curl -sf --max-time 3 "http://$lan_ip:41000/" >/dev/null; then
    fail "internal port reachable from the LAN side"
fi

step "mDNS alias over https (local CA)"
rm -f "$WORK/https.txt"
for _ in $(seq 1 10); do
    curl -sSf --max-time 10 --cacert /etc/dahlia/tls/ca.crt --resolve "whoami.local:443:$lan_ip" \
        "https://whoami.local/" -o "$WORK/https.txt" && break
    sleep 1
done
grep -q '^Hostname:' "$WORK/https.txt" 2>/dev/null \
    || fail "https://whoami.local/ does not verify or is not the app"
call domains.get | jq -e '.aliases == ["whoami"] and (has("domain") | not)' >/dev/null \
    || fail "alias not listed"
jq -e '.aliases == ["whoami"]' /var/lib/dahlia/mdns-aliases.json >/dev/null \
    || fail "alias file not written"
if openssl x509 -noout -ext subjectAltName -in /etc/dahlia/tls/server.crt | grep -q arpa; then
    fail "home.arpa name in the server certificate"
fi
resolved=""
for _ in $(seq 1 15); do
    resolved="$(avahi-resolve -4 -n whoami.local 2>/dev/null | awk '{print $2}')" || true
    [ -n "$resolved" ] && break
    sleep 1
done
echo "whoami.local -> ${resolved:-nothing}"
[ "$resolved" = "$(jq -r .address <<<"$(call domains.get)")" ] || fail "mDNS alias not published"

step "mDNS aliases off: port link only"
call domains.set '{"mdns_aliases": false}' | jq -e '.ca_renewed == false' >/dev/null \
    || fail "domains.set failed"
grep -q "listen 443" /etc/nginx/conf.d/dahlia-app-whoami.conf && fail "https proxy without alias"
curl -sf "http://$lan_ip:8099/" -o /dev/null || fail "port link gone with aliases off"
if call domains.set '{"domain": "lab.home.arpa", "mdns_aliases": true}' >/dev/null 2>&1; then
    fail "domain still accepted"
fi
call domains.set '{"mdns_aliases": true}' >/dev/null

step "list, stop, start, update"
call apps.list | jq -e '.apps[0].status == "running"' >/dev/null || fail "apps.list wrong"
call apps.stop '{"id": "whoami"}' >/dev/null
[ "$(docker inspect -f '{{.State.Status}}' dahlia-whoami)" = exited ] || fail "stop failed"
apps start-enabled
[ "$(docker inspect -f '{{.State.Status}}' dahlia-whoami)" = exited ] \
    || fail "stopped app was started at boot"
call apps.start '{"id": "whoami"}' >/dev/null
[ "$(docker inspect -f '{{.State.Status}}' dahlia-whoami)" = running ] || fail "start failed"
call apps.update '{"id": "whoami"}' >/dev/null
wait_job

step "remove"
touch /mnt/user/appdata/whoami/config/keep
call apps.remove '{"id": "whoami"}' >/dev/null
if docker inspect dahlia-whoami >/dev/null 2>&1; then fail "container still there"; fi
[ -f /mnt/user/appdata/whoami/config/keep ] || fail "data deleted without asking"
[ ! -e /etc/nginx/conf.d/dahlia-app-whoami.conf ] || fail "nginx config left over"
jq -e '.aliases == []' /var/lib/dahlia/mdns-aliases.json >/dev/null || fail "alias left over"
if curl -sf --max-time 3 "http://$lan_ip:8099/" >/dev/null; then fail "port link still open"; fi

step "install to the system SSD, one folder on the array"
[ -d /srv ] || fail "/srv missing"
mkdir -p /mnt/user/Kino
call apps.install '{"id": "whoami", "location": "/srv", "folders": {"media": "/mnt/user/Kino"}}' \
    >/dev/null
wait_job
[ -d /srv/appdata/whoami/config ] || fail "appdata not on the system SSD"
mounts="$(docker inspect -f '{{range .Mounts}}{{.Source}}={{.Destination}} {{end}}' dahlia-whoami)"
echo "$mounts"
grep -q '/srv/appdata/whoami/config=/config' <<<"$mounts" || fail "config mount wrong"
grep -q '/mnt/user/Kino=/media' <<<"$mounts" || fail "media mount wrong"
call apps.list | jq -e '.apps[] | select(.id == "whoami") | .location == "/srv"' >/dev/null \
    || fail "location not listed"
call apps.remove '{"id": "whoami", "delete_data": true}' >/dev/null
[ ! -e /srv/appdata/whoami ] || fail "system appdata not deleted"
[ -d /mnt/user/Kino ] || fail "chosen share folder deleted"

step "stack: app with PostgreSQL and MariaDB"
stack_template() {
    cat <<YAML
schema: 2
id: stacktest
name: Stack test
category: tools
description: {de: Testapp mit Datenbanken, en: test app with databases}
web: {port: 80, public_port: 8098}
login: {user: admin, password: "{SECRET_ADMIN}"}
services:
  app:
    image: traefik/whoami:$1
    depends_on: [pg, sql]
    environment: {DB_PASS: "{SECRET_DB}", TZ: "{TZ}"}
  pg:
    image: postgres:17.6-alpine
    environment: {POSTGRES_PASSWORD: "{SECRET_DB}"}
    volumes:
      - {container: /var/lib/postgresql/data, appdata: pg}
  sql:
    image: mariadb:11.8.3
    environment: {MARIADB_ROOT_PASSWORD: "{SECRET_DB}", MARIADB_AUTO_UPGRADE: "1"}
    volumes:
      - {container: /var/lib/mysql, appdata: sql}
    # InnoDB hangs on FUSE (mergerfs) with native AIO and O_DIRECT.
    command: [--innodb-use-native-aio=0, --innodb-flush-method=fsync]
YAML
}
# catalog.json: the one-file form the public catalog uses.
publish_catalog() {
    jq -n --rawfile w "$WORK/catalog/apps/whoami.yaml" --rawfile s "$WORK/stack.yaml" \
        '{format: 1, apps: {whoami: $w, stacktest: $s}}' > "$WORK/catalog/catalog.json"
    printf '{"catalog": "file://%s/catalog/"}\n' "$WORK" > /etc/dahlia/apps.json
    apps catalog --refresh --quiet
    cp "$WORK/apps.json.orig" /etc/dahlia/apps.json
}
stack_template v1.10.3 > "$WORK/stack.yaml"
publish_catalog | tee "$WORK/refresh.txt"
grep -q 'catalog has 2 apps' "$WORK/refresh.txt" || fail "bundle not read"
call apps.install '{"id": "stacktest"}' >/dev/null
wait_job stacktest
for c in dahlia-stacktest dahlia-stacktest-pg dahlia-stacktest-sql; do
    [ "$(docker inspect -f '{{.State.Status}}' "$c")" = running ] || fail "$c not running"
    docker inspect -f '{{.HostConfig.SecurityOpt}}' "$c" | grep -q no-new-privileges \
        || fail "$c: no-new-privileges missing"
done
for c in dahlia-stacktest-pg dahlia-stacktest-sql; do
    [ -z "$(docker port "$c")" ] || fail "$c publishes a port"
done
secrets=/var/lib/dahlia/apps/stacktest/secrets.json
[ "$(stat -c %a "$secrets")" = 600 ] || fail "secrets.json not 0600"
pw="$(jq -r .SECRET_DB "$secrets")"
[ "${#pw}" = 32 ] || fail "no generated database password"
call apps.list | jq -e --arg a "$(jq -r .SECRET_ADMIN "$secrets")" \
    '.apps[] | select(.id == "stacktest") | .login.password == $a and .update == null' \
    >/dev/null || fail "login or update state wrong in apps.list"

pg_ok() {
    docker exec -e PGPASSWORD="$pw" dahlia-stacktest-pg \
        psql -h 127.0.0.1 -U postgres -tAc "$1" 2>/dev/null
}
sql_ok() {
    docker exec dahlia-stacktest-sql mariadb -uroot -p"$pw" -N -e "$1" 2>/dev/null
}
ready=0
for _ in $(seq 1 60); do
    if pg_ok 'select 1' | grep -qx 1 && sql_ok 'select 1' | grep -qx 1; then ready=1; break; fi
    sleep 2
done
[ "$ready" = 1 ] || fail "databases not reachable with the generated password"
[ -f /mnt/user/appdata/stacktest/pg/PG_VERSION ] || fail "PostgreSQL data not on the array"
[ -d /mnt/user/appdata/stacktest/sql/mysql ] || fail "MariaDB data not on the array"
pg_ok 'create table keep (x int); insert into keep values (42)' >/dev/null
sql_ok 'create database keep' >/dev/null
docker exec dahlia-stacktest-pg getent hosts sql >/dev/null \
    || fail "services cannot reach each other by name"

step "stack: update found in the catalog"
stack_template v1.11.0 > "$WORK/stack.yaml"
publish_catalog >/dev/null
call apps.list | jq -e '.apps[] | select(.id == "stacktest") | .update.version == "v1.11.0"' \
    >/dev/null || fail "update not shown"
call apps.update '{"id": "stacktest"}' >/dev/null
wait_job stacktest
[ "$(docker inspect -f '{{.Config.Image}}' dahlia-stacktest)" = traefik/whoami:v1.11.0 ] \
    || fail "app image not updated"
[ "$(jq -r .SECRET_DB "$secrets")" = "$pw" ] || fail "secret changed on update"
call apps.list | jq -e '.apps[] | select(.id == "stacktest") | .update == null' >/dev/null \
    || fail "update still shown after updating"
ready=0
for _ in $(seq 1 30); do
    if pg_ok 'select x from keep' | grep -qx 42 && sql_ok 'show databases' | grep -qx keep; then
        ready=1
        break
    fi
    sleep 2
done
[ "$ready" = 1 ] || fail "database content lost on update"

step "stack: catalog timer"
systemctl is-enabled apps-catalog.timer | grep -qx enabled || fail "timer not enabled"
systemctl cat apps-catalog.service | grep -q 'catalog --refresh --quiet' \
    || fail "timer service wrong"

step "stack: remove with data"
call apps.remove '{"id": "stacktest", "delete_data": true}' >/dev/null
for c in dahlia-stacktest dahlia-stacktest-pg dahlia-stacktest-sql; do
    if docker inspect "$c" >/dev/null 2>&1; then fail "$c still there"; fi
done
[ ! -e /mnt/user/appdata/stacktest ] || fail "stack data not deleted"
[ ! -e /var/lib/dahlia/apps/stacktest ] || fail "app directory left over"

echo
echo "apps integration test passed"
