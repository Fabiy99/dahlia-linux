"use strict";
// Start screen on the monitor attached to the server (screen.service, cage + cog).
// Data comes from /api/screen, which nginx serves on 127.0.0.1:8090 only.

const POLL = 15000;
const ROTATE = 20000;
let strings = {};
let lang = "en";
let data = null;
let placeIndex = 0;

function t(key, vars) {
  let s = strings[key] ?? key;
  for (const [k, v] of Object.entries(vars || {})) s = s.replaceAll(`{${k}}`, String(v));
  return s;
}

function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined) e.textContent = text;
  return e;
}

function formatBytes(n) {
  const units = ["B", "KB", "MB", "GB", "TB", "PB"];
  let i = 0;
  while (n >= 1000 && i < units.length - 1) { n /= 1000; i++; }
  return `${n.toFixed(n < 10 && i ? 1 : 0)} ${units[i]}`;
}

async function loadLang() {
  lang = (navigator.language || "en").toLowerCase().startsWith("de") ? "de" : "en";
  document.documentElement.lang = lang;
  try {
    strings = await (await fetch(`/locales/${lang}.json`)).json();
  } catch (_) {
    strings = {};
  }
  for (const e of document.querySelectorAll("[data-i18n]")) e.textContent = t(e.dataset.i18n);
}

function tick() {
  const now = new Date();
  document.getElementById("screen-time").textContent =
    now.toLocaleTimeString(lang, { hour: "2-digit", minute: "2-digit" });
  document.getElementById("screen-date").textContent =
    now.toLocaleDateString(lang, { weekday: "long", day: "numeric", month: "long" });
}

function renderStatus(health) {
  const status = document.getElementById("screen-status");
  const findings = (health && health.findings) || [];
  const top = findings.find((f) => f.level === "critical") || findings.find((f) => f.level === "warning");
  status.querySelector(".dot").className = `dot ${top ? (top.level === "critical" ? "crit" : "warn") : "ok"}`;
  status.lastElementChild.textContent = top ? t(`check.${top.id}`, top.params)
    : health ? t("home.all_ok") : t("check.running");
  document.body.classList.toggle("attention", Boolean(top));
}

function tile(label, value, sub) {
  const box = el("div", "card screen-tile");
  box.append(el("small", "", label), el("b", "", value));
  if (sub) box.append(el("small", "", sub));
  return box;
}

function currentPlace() {
  const places = (data && data.weather || []).filter((p) => !p.error);
  if (!places.length) return null;
  return places[placeIndex % places.length];
}

function renderTiles() {
  const w = data.widgets || {};
  const tiles = [];
  const place = currentPlace();
  if (w.weather !== false && place) {
    tiles.push(tile(place.name, `${Math.round(place.temperature)}°`, t(`weather.kind_${place.kind}`)));
  }
  if (w.storage !== false && data.storage) {
    tiles.push(tile(t("home.widget_storage"), formatBytes(data.storage.used),
                    t("screen.of", { size: formatBytes(data.storage.size) })));
  }
  if (w.memory !== false && data.memory) {
    tiles.push(tile(t("home.widget_memory"), `${Math.round((data.memory.used / data.memory.size) * 100)} %`,
                    t("screen.of", { size: formatBytes(data.memory.size) })));
  }
  if (w.apps !== false && data.apps && data.apps.total) {
    tiles.push(tile(t("home.widget_apps"), t("screen.apps_running", data.apps), t("screen.running")));
  }
  document.getElementById("screen-tiles").replaceChildren(...tiles.slice(0, 4));
  setMood(w.weather !== false ? place : null);
}

function renderQr(svgText) {
  const box = document.getElementById("screen-qr");
  box.replaceChildren();
  if (!svgText) return;
  const doc = new DOMParser().parseFromString(svgText, "image/svg+xml");
  const svg = doc.documentElement;
  if (svg.nodeName !== "svg") return;
  svg.removeAttribute("width");
  svg.removeAttribute("height");
  box.append(document.importNode(svg, true));
}

function render() {
  document.getElementById("screen-host").textContent =
    [data.hostname && `${data.hostname}.local`, data.address].filter(Boolean).join(" · ");
  document.getElementById("screen-url").textContent = data.url || "";
  const setup = document.getElementById("screen-setup");
  setup.hidden = !data.setup_code;
  document.getElementById("screen-code").textContent = data.setup_code || "";
  renderStatus(data.health);
  renderTiles();
  renderQr(data.qr);
}

async function poll() {
  try {
    const res = await fetch("/api/screen", { cache: "no-store" });
    if (res.ok) {
      data = await res.json();
      render();
    }
  } catch (_) {
    // The backend restarts after updates; the next poll fills in again.
  }
  setTimeout(poll, POLL);
}

async function start() {
  await loadLang();
  tick();
  setInterval(tick, 1000);
  setInterval(() => {
    placeIndex++;
    if (data) renderTiles();
  }, ROTATE);
  poll();
}

start();
