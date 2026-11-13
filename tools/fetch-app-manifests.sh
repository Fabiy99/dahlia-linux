#!/bin/bash
# Collect the desktop app's update manifest (release asset app.json, written
# by .github/workflows/app.yml) of every release into build/repo/app/<version>.json.
# tools/sign-repo.sh signs them afterwards. All versions stay available, so
# an app can always move to exactly its server's version, also an older one.
#
# Usage: GH_TOKEN=... RELEASE_TAG=v1.4.0 tools/fetch-app-manifests.sh
# Env:   RELEASE_TAG  release being published; if its tree has the app (app/),
#                     its manifest must be there (waits up to 40 min for app.yml)
#        GH_REPO      owner/name (gh default: the checkout's origin)
# Needs: gh, python3
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$ROOT/build/repo/app"
T="$(mktemp -d)"
trap 'rm -rf "$T"' EXIT
mkdir -p "$OUT"

need=""
if [ -n "${RELEASE_TAG:-}" ] && [ -f "$ROOT/app/pubspec.yaml" ]; then
    need="$RELEASE_TAG"
fi

check() { # <manifest> <version>: well-formed and for exactly this version
    python3 - "$1" "$2" <<'PY'
import json, re, sys
m = json.load(open(sys.argv[1]))
assert m["version"] == sys.argv[2], "version"
assert m["files"], "files"
for key, f in m["files"].items():
    assert key in ("windows-x64", "macos", "linux-x64"), key
    assert re.fullmatch(r"[0-9a-f]{64}", f["sha256"]), "sha256"
    assert f["url"].startswith("https://"), "url"
    assert isinstance(f["size"], int) and f["size"] > 0, "size"
PY
}

mapfile -t tags < <(gh release list --limit 200 --json tagName --jq '.[].tagName')
if [ -n "$need" ] && ! printf '%s\n' "${tags[@]}" | grep -qx "$need"; then
    tags+=("$need")
fi

for tag in "${tags[@]}"; do
    [[ "$tag" =~ ^v([0-9]+\.[0-9]+\.[0-9]+)$ ]] || continue
    version="${BASH_REMATCH[1]}"
    tries=1
    [ "$tag" = "$need" ] && tries=80
    rm -f "$T/app.json"
    for i in $(seq 1 "$tries"); do
        if gh release download "$tag" --pattern app.json --dir "$T" >/dev/null 2>&1; then
            break
        fi
        [ "$i" -lt "$tries" ] && sleep 30
    done
    if [ ! -f "$T/app.json" ]; then
        if [ "$tag" = "$need" ]; then
            echo "release $tag has no app.json (app.yml not finished?)" >&2
            exit 1
        fi
        continue
    fi
    if ! check "$T/app.json" "$version"; then
        echo "app.json of $tag is invalid" >&2
        exit 1
    fi
    cp "$T/app.json" "$OUT/$version.json"
    echo "app manifest $version"
done
