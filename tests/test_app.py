"""Desktop app (app/): what the server side and the release tooling rely on."""

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "app"


def test_app_carries_the_repository_key():
    shipped = ROOT / "packages/archive-keyring/usr/share/keyrings/dahlia-archive-keyring.asc"
    assert (APP / "assets/dahlia-archive-keyring.asc").read_bytes() == shipped.read_bytes()


def test_app_locales_have_the_same_keys():
    de = json.loads((APP / "assets/locales/de.json").read_text())
    en = json.loads((APP / "assets/locales/en.json").read_text())
    assert de.keys() == en.keys()


def test_manifest_names_match_the_installers():
    workflow = (ROOT / ".github/workflows/app.yml").read_text()
    iss = (APP / "packaging/windows/dahlia.iss").read_text()
    deb = (APP / "packaging/linux/build-deb.sh").read_text()
    assert "OutputBaseFilename=Dahlia-{#Version}-windows-x64-setup" in iss
    assert 'f"Dahlia-{version}-windows-x64-setup.exe"' in workflow
    assert 'dahlia_${VERSION}_amd64.deb' in deb
    assert 'f"dahlia_{version}_amd64.deb"' in workflow
    assert '"dist/Dahlia-$VERSION-macos.zip"' in workflow
    assert 'f"Dahlia-{version}-macos.zip"' in workflow


def test_channel_is_the_apt_repository():
    sources = (ROOT / "packages/archive-keyring/etc/apt/sources.list.d/dahlia.sources").read_text()
    uri = re.search(r"^URIs: (\S+)$", sources, re.M).group(1)
    manifest = (APP / "lib/src/manifest.dart").read_text()
    assert f"const channelBase = '{uri}app/';" in manifest


def test_linux_self_update_path_matches_the_package():
    installer = (APP / "lib/src/installer.dart").read_text()
    deb = (APP / "packaging/linux/build-deb.sh").read_text()
    assert "Platform.resolvedExecutable == '/opt/dahlia/dahlia'" in installer
    assert 'ln -s /opt/dahlia/dahlia "$STAGE/usr/bin/dahlia"' in deb
