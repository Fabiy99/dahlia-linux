#!/usr/bin/env python3
"""Project website for GitHub Pages, next to the apt repository.

Writes into OUT (build/repo in repo.yml): index.html (Deutsch), en/index.html,
handbuch/index.html and handbook/index.html (from docs/de/handbuch.md and
docs/en/handbook.md), site.css and img/. Names do not collide with the
repository's files (InRelease, Packages, *.deb, app/).

Markdown: the subset the handbook uses (headings, paragraphs, lists, tables,
code blocks, links, bold, inline code), rendered here so the workflow needs no
extra package. Usage: tools/build-site.py SRC OUT
"""

import html
import re
import shutil
import sys
from pathlib import Path

REPO_URL = "https://github.com/Fabiy99/dahlia-linux"
RELEASE_URL = f"{REPO_URL}/releases/latest"


# --- markdown ---------------------------------------------------------------------


def slug(text: str) -> str:
    """GitHub's heading anchors: lower case, punctuation dropped, spaces to hyphens."""
    text = re.sub(r"<[^>]+>", "", text).strip().lower()
    text = re.sub(r"[^\w\- ]", "", text)
    return text.replace(" ", "-")


def inline(text: str, links: dict[str, str]) -> str:
    """Inline markup of one line: `code`, **bold**, [text](url)."""
    out = []
    pos = 0
    for m in re.finditer(r"`([^`]+)`|\*\*(.+?)\*\*|\[([^\]]+)\]\(([^)\s]+)\)", text):
        out.append(html.escape(text[pos:m.start()], quote=False))
        if m.group(1) is not None:
            out.append(f"<code>{html.escape(m.group(1), quote=False)}</code>")
        elif m.group(2) is not None:
            out.append(f"<strong>{inline(m.group(2), links)}</strong>")
        else:
            href = links.get(m.group(4), m.group(4))
            out.append(f'<a href="{html.escape(href)}">{inline(m.group(3), links)}</a>')
        pos = m.end()
    out.append(html.escape(text[pos:], quote=False))
    return "".join(out)


def render(md: str, links: dict[str, str] | None = None) -> tuple[str, str]:
    """HTML of the document body and its first heading (the title)."""
    links = links or {}
    lines = md.splitlines()
    out: list[str] = []
    title = ""
    i = 0
    para: list[str] = []

    def flush():
        if para:
            out.append(f"<p>{inline(' '.join(para), links)}</p>")
            para.clear()

    while i < len(lines):
        line = lines[i]
        if line.startswith("```"):
            flush()
            code = []
            i += 1
            while i < len(lines) and not lines[i].startswith("```"):
                code.append(lines[i])
                i += 1
            out.append(f"<pre><code>{html.escape(chr(10).join(code), quote=False)}</code></pre>")
            i += 1
            continue
        m = re.match(r"^(#{1,4}) (.+)$", line)
        if m:
            flush()
            level = len(m.group(1))
            text = m.group(2).strip()
            if level == 1 and not title:
                title = text
            out.append(f'<h{level} id="{slug(text)}">{inline(text, links)}</h{level}>')
            i += 1
            continue
        if line.startswith("|"):
            flush()
            rows = []
            while i < len(lines) and lines[i].startswith("|"):
                cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                if not all(re.fullmatch(r":?-{3,}:?", c) for c in cells):
                    rows.append(cells)
                i += 1
            head, *body = rows
            out.append("<div class=\"table\"><table><thead><tr>"
                       + "".join(f"<th>{inline(c, links)}</th>" for c in head)
                       + "</tr></thead><tbody>"
                       + "".join("<tr>" + "".join(f"<td>{inline(c, links)}</td>" for c in r)
                                 + "</tr>" for r in body)
                       + "</tbody></table></div>")
            continue
        m = re.match(r"^(\d+\.|-) (.*)$", line)
        if m:
            flush()
            tag = "ol" if m.group(1) != "-" else "ul"
            items: list[list[str]] = []
            while i < len(lines):
                m = re.match(r"^(\d+\.|-) (.*)$", lines[i])
                if m and (m.group(1) == "-") == (tag == "ul"):
                    items.append([m.group(2)])
                elif lines[i].startswith("  ") and lines[i].strip() and items:
                    items[-1].append(lines[i].strip())
                else:
                    break
                i += 1
            lis = "".join(f"<li>{inline(' '.join(it), links)}</li>" for it in items)
            out.append(f"<{tag}>{lis}</{tag}>")
            continue
        if not line.strip():
            flush()
        else:
            para.append(line.strip())
        i += 1
    flush()
    return "\n".join(out), title


# --- pages ------------------------------------------------------------------------

TEXT = {
    "de": {
        "lang": "de",
        "title": "Dahlia Linux – Homelab-Server auf Debian",
        "tagline": "Ein Server für zu Hause, der sich wie ein Zuhause anfühlt.",
        "lead": "Dahlia Linux ist ein freies Server-Betriebssystem für das Homelab auf Basis von "
                "Debian 13. Platten wie bei Unraid, Apps mit einem Klick, VMs und Container, "
                "Backups, die sich selbst prüfen, und ein Startbildschirm, den man gern öffnet.",
        "download": "Herunterladen",
        "handbook": "Handbuch",
        "source": "Quellcode",
        "other": ("English", "en/"),
        "features": [
            ("Platten und Array", "Datenplatten einzeln, Pool über mergerfs, Cache-SSD mit Mover "
             "und SnapRAID-Parity. Platten mit Daten werden ohne Formatieren übernommen."),
            ("Apps", "Über 100 Vorlagen von Jellyfin bis Paperless. Vor jedem Update eine Kopie, "
             "mit einem Klick zurück zur alten Version."),
            ("VMs und Container", "Virtuelle Maschinen und LXC-Container über Incus, mit "
             "Bildschirm der VM direkt in der App."),
            ("Backup mit Probe", "Verschlüsselte Backups mit restic. Einmal im Monat stellt "
             "Dahlia eine Stichprobe wieder her und prüft sie."),
            ("Desktop-App", "Native App für Windows, macOS und Linux. Findet den Server von "
             "selbst, aktualisiert sich mit ihm und erstellt unter Windows den USB-Stick."),
            ("Sicher von Haus aus", "Firewall nur für das lokale Netz, eigene CA für HTTPS, "
             "Zwei-Faktor-Login, Fernzugriff über WireGuard oder Tailscale, Snapshots mit "
             "Rollback."),
        ],
        "start_title": "In drei Schritten",
        "steps": [
            "Desktop-App installieren und unter Windows mit „USB-Stick erstellen“ den Installer "
            "schreiben (oder die ISO mit balenaEtcher).",
            "Den künftigen Server vom Stick starten und die Fragen des Installers beantworten.",
            "In der App oder unter https://dahlia.local/ den Einrichtungscode vom Bildschirm des "
            "Servers eingeben. Fertig.",
        ],
        "shots_title": "So sieht es aus",
        "shots": [("start.png", "Startbildschirm"), ("vms.png", "VMs und Container"),
                  ("console.png", "Bildschirm einer VM in der App")],
        "free": "Frei und ohne Abo. Lizenz GPLv3.",
        "back": "Zur Startseite",
        "handbook_path": "handbuch/",
    },
    "en": {
        "lang": "en",
        "title": "Dahlia Linux – home lab server on Debian",
        "tagline": "A server for your home that feels like home.",
        "lead": "Dahlia Linux is a free server operating system for the home lab, based on "
                "Debian 13. Disks like Unraid, apps in one click, VMs and containers, backups "
                "that test themselves, and a start screen you like to open.",
        "download": "Download",
        "handbook": "Handbook",
        "source": "Source code",
        "other": ("Deutsch", "../"),
        "features": [
            ("Disks and array", "Data disks one by one, a pool through mergerfs, a cache SSD with "
             "mover and SnapRAID parity. Disks with data are taken over without formatting."),
            ("Apps", "More than 100 templates from Jellyfin to Paperless. A copy before every "
             "update, back to the old version in one click."),
            ("VMs and containers", "Virtual machines and LXC containers through Incus, with the "
             "VM's screen right in the app."),
            ("Backup with a probe", "Encrypted backups with restic. Once a month Dahlia restores "
             "a sample and checks it."),
            ("Desktop app", "Native app for Windows, macOS and Linux. Finds the server by itself, "
             "updates with it and makes the USB stick on Windows."),
            ("Secure by default", "Firewall for the local network only, own CA for HTTPS, "
             "two-factor login, remote access through WireGuard or Tailscale, snapshots with "
             "rollback."),
        ],
        "start_title": "In three steps",
        "steps": [
            "Install the desktop app and, on Windows, write the installer with \"Make a USB "
            "stick\" (or write the ISO with balenaEtcher).",
            "Boot the future server from the stick and answer the installer's questions.",
            "In the app or at https://dahlia.local/, enter the setup code from the server's "
            "screen. Done.",
        ],
        "shots_title": "What it looks like",
        "shots": [("start.png", "Start screen"), ("vms.png", "VMs and containers"),
                  ("console.png", "A VM's screen in the app")],
        "free": "Free, no subscription. Licence GPLv3.",
        "back": "Home",
        "handbook_path": "../handbook/",
    },
}

LOGO = ('<svg viewBox="0 0 24 24" aria-hidden="true"><g fill="currentColor">'
        '<circle cx="12" cy="12" r="2.6"/><path d="M12 3c2 2.2 2 4.6 0 6.4C10 7.6 10 5.2 12 3Zm0 '
        '18c-2-2.2-2-4.6 0-6.4 2 1.8 2 4.2 0 6.4ZM3 12c2.2-2 4.6-2 6.4 0-1.8 2-4.2 2-6.4 0Zm18 '
        '0c-2.2 2-4.6 2-6.4 0 1.8-2 4.2-2 6.4 0ZM5.6 5.6c3-.2 4.7 1.5 4.5 4.5-3 .2-4.7-1.5-4.5-4.5'
        'Zm12.8 12.8c-3 .2-4.7-1.5-4.5-4.5 3-.2 4.7 1.5 4.5 4.5Zm0-12.8c.2 3-1.5 4.7-4.5 4.5-.2-3 '
        '1.5-4.7 4.5-4.5ZM5.6 18.4c-.2-3 1.5-4.7 4.5-4.5.2 3-1.5 4.7-4.5 4.5Z"/></g></svg>')


def page(lang: str, title: str, root: str, body: str, other: tuple[str, str]) -> str:
    t = TEXT[lang]
    return f"""<!doctype html>
<html lang="{lang}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<meta name="description" content="{html.escape(t['lead'])}">
<link rel="icon" href="{root}img/dahlia-icon.svg" type="image/svg+xml">
<link rel="stylesheet" href="{root}site.css">
</head>
<body>
<header class="bar">
  <a class="brand" href="{root}{'' if lang == 'de' else 'en/'}">{LOGO}<span>Dahlia Linux</span></a>
  <nav>
    <a href="{RELEASE_URL}">{t['download']}</a>
    <a href="{root}{'handbuch/' if lang == 'de' else 'handbook/'}">{t['handbook']}</a>
    <a href="{REPO_URL}">{t['source']}</a>
    <a class="lang" href="{other[1]}">{other[0]}</a>
  </nav>
</header>
{body}
<footer>{LOGO}<span>{html.escape(t['free'])}</span> <a href="{REPO_URL}">GitHub</a></footer>
</body>
</html>
"""


def home(lang: str) -> str:
    t = TEXT[lang]
    root = "" if lang == "de" else "../"
    feats = "".join(f"<article><h3>{html.escape(h)}</h3><p>{html.escape(p)}</p></article>"
                    for h, p in t["features"])
    steps = "".join(f"<li>{html.escape(s)}</li>" for s in t["steps"])
    shots = "".join(f'<figure><img src="{root}img/{f}" alt="{html.escape(c)}" loading="lazy">'
                    f"<figcaption>{html.escape(c)}</figcaption></figure>" for f, c in t["shots"])
    hb = t["handbook_path"]
    body = f"""<main>
<section class="hero">
  <div class="bloom">{LOGO}</div>
  <h1>{html.escape(t['tagline'])}</h1>
  <p class="lead">{html.escape(t['lead'])}</p>
  <p class="cta"><a class="button" href="{RELEASE_URL}">{t['download']}</a>
  <a class="button ghost" href="{hb}">{t['handbook']}</a></p>
</section>
<section class="features">{feats}</section>
<section class="shots"><h2>{html.escape(t['shots_title'])}</h2>
<div class="grid">{shots}</div></section>
<section class="steps"><h2>{html.escape(t['start_title'])}</h2><ol>{steps}</ol></section>
</main>"""
    other = ("English", "en/") if lang == "de" else ("Deutsch", "../")
    return page(lang, t["title"], root, body, other)


def handbook(lang: str, md: str) -> str:
    # Links between the two handbooks and to the architecture on GitHub.
    links = {
        "../en/handbook.md": "../handbook/",
        "../de/handbuch.md": "../handbuch/",
        "architektur.md": f"{REPO_URL}/blob/main/docs/de/architektur.md",
        "architecture.md": f"{REPO_URL}/blob/main/docs/en/architecture.md",
    }
    body, title = render(md, links)
    other = ("English", "../handbook/") if lang == "de" else ("Deutsch", "../handbuch/")
    return page(lang, title, "../", f'<main class="doc">{body}</main>', other)




def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def build(src: Path, out: Path) -> list[Path]:
    """Writes the website into out; returns the written files."""
    files = {
        out / "index.html": home("de"),
        out / "en/index.html": home("en"),
        out / "handbuch/index.html": handbook("de", read(src / "docs/de/handbuch.md")),
        out / "handbook/index.html": handbook("en", read(src / "docs/en/handbook.md")),
        out / "site.css": read(src / "site/site.css"),
    }
    for path, text in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    img = out / "img"
    img.mkdir(parents=True, exist_ok=True)
    written = list(files)
    for f in sorted((src / "site/img").glob("*")) + [src / "docs/img/dahlia-icon.svg"]:
        shutil.copy(f, img / f.name)
        written.append(img / f.name)
    return written


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print("usage: build-site.py SRC OUT", file=sys.stderr)
        return 2
    for path in build(Path(argv[1]), Path(argv[2])):
        print(f"build-site: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
