#!/bin/bash
# Integration test for Dahlia apps with real Docker, nginx and dahlia-agent.
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
# Must run as root on a disposable machine with dahlia-storage, dahlia-agent,
# docker, nginx, avahi-daemon and avahi-utils installed.
set -euo pipefail

WORK="$(mktemp -d /var/tmp/dahlia-apps-test.XXXXXX)"
LOOP=""
UUID=""
export DAHLIA_ALLOW_LOOP=1

fail() { echo "FAIL: $*" >&2; diag >&2; exit 1; }
step() { echo; echo "== $*"; }
call() { dahlia-agent-call --timeout 120 "$@"; }

diag() {
    echo "--- dahlia-app-whoami / dahlia-app-stacktest journal"
    journalctl -u dahlia-app-whoami.service -u dahlia-app-stacktest.service --no-pager \
        | tail -60 || true
    for c in dahlia-stacktest-pg dahlia-stacktest-sql; do
        echo "--- $c log"
        docker logs --tail 30 "$c" 2>&1 || true
    done
    echo "--- dahlia-mdns journal"
    journalctl -u dahlia-mdns.service --no-pager | tail -20 || true
    echo "--- avahi: daemon, publish as root and as dahlia-mdns"
    systemctl is-active avahi-daemon.service || true
    timeout 3 avahi-publish -a -R diag-root.local 10.9.9.9 || true
    timeout 3 setpriv --reuid=dahlia-mdns --regid=dahlia-mdns --clear-groups \
        avahi-publish -a -R diag-user.local 10.9.9.8 || true
    journalctl -u dbus.service -u avahi-daemon.service --no-pager | tail -20 || true
    docker ps -a || true
}

cleanup() {
    set +e
    dahlia-apps remove whoami --delete-data >/dev/null 2>&1
    dahlia-apps remove stacktest --delete-data >/dev/null 2>&1
    [ -f "$WORK/apps.json.orig" ] && cp "$WORK/apps.json.orig" /etc/dahlia/apps.json
    [ -n "$UUID" ] && dahlia-disk remove "$UUID" >/dev/null 2>&1
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
dahlia-disk add "$UUID" --role data
# The default keeps 50G free per disk; the test disk is far smaller.
dahlia-disk set-minfreespace 10M
findmnt -n /mnt/user >/dev/null || fail "/mnt/user not mounted"

step "local catalog"
mkdir -p "$WORK/catalog/apps"
echo '{"apps": ["whoami"]}' > "$WORK/catalog/index.json"
cat > "$WORK/catalog/apps/whoami.yaml" <<'YAML'
schema: 1
id: whoami
name: Whoami
description: {de: Testapp, en: test app}
image: traefik/whoami:v1.10.3
web: {port: 80, public_port: 8099}
volumes:
  - {container: /config, appdata: config}
environment: {TZ: "{TZ}", PUID: "{PUID}"}
YAML
cp /etc/dahlia/apps.json "$WORK/apps.json.orig"
printf '{"catalog": "file://%s/catalog/"}\n' "$WORK" > /etc/dahlia/apps.json
call apps.catalog '{"refresh": true}' | jq -e '.apps[0].id == "whoami"' >/dev/null \
    || fail "catalog refresh failed"
cp "$WORK/apps.json.orig" /etc/dahlia/apps.json

step "install through the agent"
call apps.install '{"id": "whoami"}' >/dev/null
wait_job
[ "$(docker inspect -f '{{.State.Status}}' dahlia-whoami)" = running ] || fail "container not running"
ports="$(docker port dahlia-whoami)"
echo "$ports"
grep -q '^80/tcp -> 127.0.0.1:41000$' <<<"$ports" || fail "web port not on 127.0.0.1:41000"
if grep -q '0.0.0.0' <<<"$ports"; then fail "port published on all interfaces"; fi
[ -d /mnt/user/appdata/whoami/config ] || fail "appdata missing"
[ "$(stat -c %U /mnt/user/appdata/whoami/config)" = dahlia-apps ] || fail "appdata owner wrong"
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

step "app domain and mDNS alias over https (local CA)"
for name in whoami.dahlia.home.arpa whoami.local; do
    rm -f "$WORK/https.txt"
    for _ in $(seq 1 10); do
        curl -sSf --max-time 10 --cacert /etc/dahlia/tls/ca.crt --resolve "$name:443:$lan_ip" \
            "https://$name/" -o "$WORK/https.txt" && break
        sleep 1
    done
    grep -q '^Hostname:' "$WORK/https.txt" 2>/dev/null \
        || fail "https://$name/ does not verify or is not the app"
done
call domains.get | jq -e '.aliases == ["whoami"]' >/dev/null || fail "alias not listed"
jq -e '.aliases == ["whoami"]' /var/lib/dahlia/mdns-aliases.json >/dev/null \
    || fail "alias file not written"
resolved=""
for _ in $(seq 1 15); do
    resolved="$(avahi-resolve -4 -n whoami.local 2>/dev/null | awk '{print $2}')" || true
    [ -n "$resolved" ] && break
    sleep 1
done
echo "whoami.local -> ${resolved:-nothing}"
[ "$resolved" = "$(jq -r .address <<<"$(call domains.get)")" ] || fail "mDNS alias not published"

step "change the domain"
call domains.set '{"domain": "lab.home.arpa", "mdns_aliases": false}' \
    | jq -e '.ca_renewed == false and .domain == "lab.home.arpa"' >/dev/null \
    || fail "domains.set failed"
# nginx -s reload returns before the new workers serve the new certificate.
served=0
for _ in $(seq 1 10); do
    if curl -sSf --max-time 10 --cacert /etc/dahlia/tls/ca.crt \
            --resolve "whoami.lab.home.arpa:443:$lan_ip" "https://whoami.lab.home.arpa/" \
            -o /dev/null; then
        served=1
        break
    fi
    sleep 1
done
[ "$served" = 1 ] || fail "new domain not served"
if openssl x509 -noout -ext subjectAltName -in /etc/dahlia/tls/server.crt | grep -q whoami.local
then
    fail "alias still in the certificate"
fi
if call domains.set '{"domain": "nas.dahlia.local", "mdns_aliases": true}' >/dev/null 2>&1; then
    fail "multi-level .local accepted"
fi
call domains.set '{"domain": "dahlia.home.arpa", "mdns_aliases": true}' >/dev/null

step "list, stop, start, update"
call apps.list | jq -e '.apps[0].status == "running"' >/dev/null || fail "apps.list wrong"
call apps.stop '{"id": "whoami"}' >/dev/null
[ "$(docker inspect -f '{{.State.Status}}' dahlia-whoami)" = exited ] || fail "stop failed"
dahlia-apps start-enabled
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
YAML
}
# catalog.json: the one-file form the public catalog uses.
publish_catalog() {
    jq -n --rawfile w "$WORK/catalog/apps/whoami.yaml" --rawfile s "$WORK/stack.yaml" \
        '{format: 1, apps: {whoami: $w, stacktest: $s}}' > "$WORK/catalog/catalog.json"
    printf '{"catalog": "file://%s/catalog/"}\n' "$WORK" > /etc/dahlia/apps.json
    dahlia-apps catalog --refresh --quiet
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
systemctl is-enabled dahlia-apps-catalog.timer | grep -qx enabled || fail "timer not enabled"
systemctl cat dahlia-apps-catalog.service | grep -q 'catalog --refresh --quiet' \
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
