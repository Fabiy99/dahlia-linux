"""Project website (tools/build-site.py): Markdown subset, links, built files."""

import importlib.util
import re
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("build_site", ROOT / "tools" / "build-site.py")
site = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(site)


def test_slug_matches_github_anchors():
    assert site.slug("USB-Stick erstellen") == "usb-stick-erstellen"
    assert site.slug("Desktop-App, Browser und Handy") == "desktop-app-browser-und-handy"
    assert site.slug("Terminal, Docker and log") == "terminal-docker-and-log"
    assert site.slug("Was du brauchst") == "was-du-brauchst"


def test_inline_escapes_and_maps_links():
    links = {"../en/handbook.md": "../handbook/"}
    out = site.inline("a <b> `x<y>` **fett** [Hand](../en/handbook.md)", links)
    assert out == ('a &lt;b&gt; <code>x&lt;y&gt;</code> <strong>fett</strong> '
                   '<a href="../handbook/">Hand</a>')


def test_render_blocks():
    md = "\n".join([
        "# Titel", "", "Zeile eins", "Zeile zwei", "", "## Teil A", "",
        "- eins", "  weiter", "- zwei", "", "1. erst", "2. dann", "",
        "| A | B |", "| --- | --- |", "| 1 | `2` |", "", "```", "<raw>", "```",
    ])
    body, title = site.render(md)
    assert title == "Titel"
    assert '<h2 id="teil-a">Teil A</h2>' in body
    assert "<p>Zeile eins Zeile zwei</p>" in body
    assert "<ul><li>eins weiter</li><li>zwei</li></ul>" in body
    assert "<ol><li>erst</li><li>dann</li></ol>" in body
    assert "<th>A</th>" in body and "<td><code>2</code></td>" in body
    assert "<pre><code>&lt;raw&gt;</code></pre>" in body


class _Collect(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids: set[str] = set()
        self.hrefs: list[str] = []
        self.srcs: list[str] = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if "id" in a:
            self.ids.add(a["id"])
        if tag in ("a", "link") and a.get("href"):
            self.hrefs.append(a["href"])
        if tag == "img":
            self.srcs.append(a["src"])


def test_build_writes_site_with_working_links(tmp_path):
    written = site.build(ROOT, tmp_path)
    for name in ("index.html", "en/index.html", "handbuch/index.html", "handbook/index.html",
                 "site.css", "img/dahlia-icon.svg", "img/start.png", "img/vms.png",
                 "img/console.png"):
        assert tmp_path / name in written
        assert (tmp_path / name).is_file()
    for page in ("index.html", "en/index.html", "handbuch/index.html", "handbook/index.html"):
        path = tmp_path / page
        p = _Collect()
        p.feed(path.read_text(encoding="utf-8"))
        for href in p.hrefs + p.srcs:
            if href.startswith("https://"):
                assert href.startswith(site.REPO_URL), href
                continue
            target, _, anchor = href.partition("#")
            if target:
                resolved = (path.parent / target).resolve()
                if resolved.is_dir():
                    resolved = resolved / "index.html"
                assert resolved.is_file(), f"{page}: {href}"
            if anchor:
                assert anchor in p.ids, f"{page}: #{anchor}"
        assert ".md" not in " ".join(h for h in p.hrefs if not h.startswith("https://")), page


def test_handbooks_have_the_same_sections():
    de = (ROOT / "docs/de/handbuch.md").read_text(encoding="utf-8")
    en = (ROOT / "docs/en/handbook.md").read_text(encoding="utf-8")
    assert len(re.findall(r"^## ", de, re.M)) == len(re.findall(r"^## ", en, re.M))
