"""Repository-level checks: license, bilingual docs, required meta files."""

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_license_is_gplv3():
    text = (ROOT / "LICENSE").read_text(encoding="utf-8")
    assert "GNU GENERAL PUBLIC LICENSE" in text
    assert "Version 3, 29 June 2007" in text


def test_readme_has_both_languages():
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "## Deutsch" in text
    assert "## English" in text


def test_architecture_docs_exist_in_both_languages():
    de = ROOT / "docs" / "de" / "architektur.md"
    en = ROOT / "docs" / "en" / "architecture.md"
    assert de.is_file()
    assert en.is_file()


def test_architecture_docs_have_same_sections():
    # Both translations must keep the same structure.
    de = (ROOT / "docs" / "de" / "architektur.md").read_text(encoding="utf-8")
    en = (ROOT / "docs" / "en" / "architecture.md").read_text(encoding="utf-8")
    de_sections = [line for line in de.splitlines() if line.startswith("## ")]
    en_sections = [line for line in en.splitlines() if line.startswith("## ")]
    assert len(de_sections) == len(en_sections)


def test_pr_template_exists():
    assert (ROOT / ".github" / "pull_request_template.md").is_file()


def test_ssh_generator_is_masked():
    # systemd's SSH-over-AF_VSOCK generator fails on Hyper-V and floods the
    # console on every daemon-reload; Dahlia uses plain sshd only.
    link = ROOT / "packages/base/etc/systemd/system-generators/systemd-ssh-generator"
    assert link.is_symlink() and os.readlink(link) == "/dev/null"
