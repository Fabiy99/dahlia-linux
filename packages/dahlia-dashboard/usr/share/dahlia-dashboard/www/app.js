// Dahlia dashboard frontend. No framework, no build step.
// All visible text comes from locales/<lang>.json via t().
"use strict";

const LANGS = ["de", "en"];
const STATS_INTERVAL = 3000;
const HISTORY = 60;
let strings = {};
let lang = "en";
let current = null;
let statsTimer = null;
let lastStats = null;
const history = { cpu: [], mem: [] };

// --- i18n ---------------------------------------------------------------------

function t(key, vars) {
  let s = strings[key] ?? key;
  for (const [k, v] of Object.entries(vars || {})) s = s.replaceAll(`{${k}}`, String(v));
  return s;
}

// Translate a value from the system (status, result); unknown values stay as they are.
function tv(prefix, value) {
  return strings[`${prefix}.${value}`] ?? value;
}

function pickLang() {
  let saved = null;
  try { saved = localStorage.getItem("dahlia-lang"); } catch (_) { /* storage blocked */ }
  if (LANGS.includes(saved)) return saved;
  const nav = (navigator.language || "en").slice(0, 2);
  return LANGS.includes(nav) ? nav : "en";
}

async function loadLang(code) {
  const res = await fetch(`/locales/${code}.json`, { cache: "no-cache" });
  strings = await res.json();
  lang = code;
  document.documentElement.lang = code;
  document.getElementById("lang").value = code;
  for (const el of document.querySelectorAll("[data-i18n]")) el.textContent = t(el.dataset.i18n);
  for (const el of document.querySelectorAll("[data-i18n-placeholder]")) {
    el.placeholder = t(el.dataset.i18nPlaceholder);
  }
  for (const el of document.querySelectorAll("[data-i18n-title]")) {
    el.title = t(el.dataset.i18nTitle);
    el.setAttribute("aria-label", el.title);
  }
}

// --- API ----------------------------------------------------------------------

class ApiError extends Error {
  constructor(status, key, detail) {
    super(key);
    this.status = status;
    this.key = key;
    this.detail = detail;
  }
}

// nginx answers 502-504 while the dashboard restarts (package update); a
// network error means nginx itself reloads. GET requests wait for the
// server instead of failing; other requests are not repeated.
const RESTARTING = new Set([502, 503, 504]);
let reconnecting = null;
let serverRestarted = false;

function waitForServer() {
  if (reconnecting) return reconnecting;
  const notice = document.getElementById("reconnect");
  notice.hidden = false;
  reconnecting = (async () => {
    const until = Date.now() + 120000;
    while (Date.now() < until) {
      await new Promise((r) => setTimeout(r, 2000));
      try {
        const res = await fetch("/api/state", { credentials: "same-origin", cache: "no-store" });
        if (!RESTARTING.has(res.status)) {
          serverRestarted = true;
          return true;
        }
      } catch (_) { /* still down */ }
    }
    return false;
  })().finally(() => {
    notice.hidden = true;
    reconnecting = null;
  });
  return reconnecting;
}

async function api(method, path, body) {
  const opts = { method, headers: { "X-Dahlia-Request": "1" }, credentials: "same-origin" };
  if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  let res = null;
  for (let attempt = 0; attempt < 2; attempt++) {
    try {
      res = await fetch(`/api/${path}`, opts);
    } catch (_) {
      res = null;
    }
    const down = res === null || RESTARTING.has(res.status);
    if (!down || method !== "GET" || attempt > 0 || !await waitForServer()) break;
  }
  if (res === null) throw new ApiError(0, "error.network");
  let data = {};
  try { data = await res.json(); } catch (_) { /* empty or non-JSON body */ }
  if (!res.ok) {
    if (res.status === 401 && path !== "login") showLogin();
    throw new ApiError(res.status, data.error || "error.request", data.detail || "");
  }
  return data;
}

// --- DOM helpers ----------------------------------------------------------------

function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined && text !== null) e.textContent = text;
  return e;
}

function icon(name) {
  const ns = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(ns, "svg");
  const use = document.createElementNS(ns, "use");
  use.setAttribute("href", `#i-${name}`);
  svg.append(use);
  return svg;
}

function iconButton(iconName, key, onClick, cls) {
  const b = el("button", cls || "");
  b.append(icon(iconName), el("span", "", t(key)));
  b.addEventListener("click", () => guarded(b, () => onClick(b)));
  return b;
}

function button(key, onClick, cls) {
  const b = el("button", cls || "", t(key));
  b.addEventListener("click", () => guarded(b, () => onClick(b)));
  return b;
}

function link(href, text) {
  const a = el("a", "", text ?? href.replace(/^https?:\/\//, ""));
  a.href = href;
  a.target = "_blank";
  a.rel = "noopener";
  return a;
}

function badge(text, kind) {
  return el("span", `badge ${kind || ""}`, text);
}

function fact(dl, key, value) {
  const dd = el("dd");
  if (value instanceof Node) dd.append(value);
  else dd.textContent = value ?? "-";
  dl.append(el("dt", "", t(key)), dd);
}

function cell(tr, content) {
  const td = el("td");
  if (content instanceof Node) td.append(content);
  else td.textContent = content;
  tr.append(td);
  return td;
}

// App logo from the catalog, else a letter avatar with a colour derived from
// the id, so it stays the same.
function avatar(id, name, icon) {
  if (icon) {
    const img = el("img", "avatar logo-img");
    img.src = `/app-icons/${icon}`;
    img.alt = "";
    img.loading = "lazy";
    img.addEventListener("error", () => img.replaceWith(avatar(id, name)), { once: true });
    return img;
  }
  let h = 0;
  for (const c of id) h = (h * 31 + c.charCodeAt(0)) % 360;
  const a = el("span", "avatar", (name || id).slice(0, 1).toUpperCase());
  a.style.background = `hsl(${h} 45% 45%)`;
  return a;
}

// --- messages and dialogs -----------------------------------------------------

function say(text, kind) {
  const box = document.getElementById("toasts");
  if (!text) { box.replaceChildren(); return; }
  const toast = el("div", `toast ${kind || ""}`);
  const close = el("button", "", "×");
  close.setAttribute("aria-label", t("common.close"));
  close.addEventListener("click", () => toast.remove());
  toast.append(el("span", "", text), close);
  box.append(toast);
  while (box.children.length > 3) box.firstElementChild.remove();
  if (kind !== "error") setTimeout(() => toast.remove(), 7000);
}

function errorText(err) {
  if (err instanceof ApiError) return err.detail ? `${t(err.key)} (${err.detail})` : t(err.key);
  console.error(err);
  return t("error.network");
}

function sayError(err) {
  say(errorText(err), "error");
}

// Error handler for a tile that loads on its own: the reason replaces "Loading ...".
function tileError(id) {
  return (err) => document.getElementById(id).replaceChildren(el("p", "empty", errorText(err)));
}

// Replaces window.confirm; resolves to true for OK.
function ask(text, okKey, danger) {
  const dlg = document.getElementById("dialog");
  document.getElementById("dialog-text").textContent = text;
  const ok = document.getElementById("dialog-ok");
  ok.textContent = t(okKey || "common.ok");
  ok.className = danger ? "danger" : "primary";
  dlg.returnValue = "";
  dlg.showModal();
  return new Promise((resolve) => {
    dlg.addEventListener("close", () => resolve(dlg.returnValue === "ok"), { once: true });
  });
}

// Run an action, show errors, keep the button disabled while it runs.
async function guarded(btn, fn) {
  if (btn) btn.disabled = true;
  try {
    await fn();
  } catch (err) {
    sayError(err);
  } finally {
    if (btn) btn.disabled = false;
  }
}

// --- formatting -----------------------------------------------------------------

function formatBytes(n) {
  if (n === null || n === undefined) return "-";
  const units = ["B", "KB", "MB", "GB", "TB", "PB"];
  let i = 0;
  while (n >= 1000 && i < units.length - 1) { n /= 1000; i++; }
  return `${n.toFixed(i ? 1 : 0)} ${units[i]}`;
}

function formatRate(bytesPerSec) {
  return `${formatBytes(bytesPerSec)}/s`;
}

function formatUptime(sec) {
  const d = Math.floor(sec / 86400);
  const h = Math.floor((sec % 86400) / 3600);
  const m = Math.floor((sec % 3600) / 60);
  return t("overview.uptime_value", { d, h, m });
}

let cpuTemp = null;

function formatTemp(c) {
  return `${Math.round(c)} °C`;
}

// Disk temperatures like a NAS shows them: HDDs warm at 45, SSDs at 60.
function tempKind(c, rotational) {
  if (c === null || c === undefined) return "";
  const warn = rotational ? 45 : 60;
  if (c >= warn + 10) return "crit";
  if (c >= warn) return "warn";
  return "ok";
}

const HEALTH_KIND = { passed: "ok", warning: "warn", failed: "crit", standby: "", unavailable: "",
                      unknown: "" };

function healthBadge(d) {
  const b = badge(tv("health", d.health), HEALTH_KIND[d.health] ?? "");
  const w = Object.entries(d.warnings || {}).map(([k, v]) => `${tv("smart", k)}: ${v}`);
  if (w.length) b.title = w.join("\n");
  return b;
}

function level(pct) {
  if (pct >= 90) return "crit";
  if (pct >= 75) return "warn";
  return "";
}

function percent(used, size) {
  return size ? Math.min(100, Math.round((used / size) * 100)) : 0;
}

// --- views ----------------------------------------------------------------------

const TITLES = {
  overview: "nav.overview", storage: "storage.title", parity: "parity.title", apps: "apps.title",
  domains: "nav.domains", jobs: "jobs.title", backup: "nav.backup", updates: "updates.title", account: "nav.account",
  docker: "nav.docker", log: "nav.log", shares: "nav.shares", terminal: "nav.terminal",
  setup: "setup.title", login: "login.title",
};

function show(view) {
  for (const s of document.querySelectorAll("main > section")) s.hidden = s.id !== `view-${view}`;
  for (const b of document.querySelectorAll("#nav button")) {
    if (b.dataset.view === view) b.setAttribute("aria-current", "page");
    else b.removeAttribute("aria-current");
  }
  document.getElementById("page-title").textContent = t(TITLES[view]);
  document.getElementById("sidebar").classList.remove("open");
  current = view;
  if (view === "overview") startStats();
  else stopStats();
  if (view !== "docker") stopDockerStats();
  if (view !== "account") clearTimeout(netTimer);
}

function setLoggedIn(on) {
  document.getElementById("sidebar").hidden = !on;
  document.getElementById("logout").hidden = !on;
  document.getElementById("menu").hidden = !on;
  if (!on) stopStats();
}

function showLogin() {
  setLoggedIn(false);
  show("login");
}

const loaders = {
  overview: loadOverview,
  storage: () => Promise.all([loadStorage(), loadStorageSettings()]),
  parity: loadParity,
  apps: loadApps,
  domains: loadDomains,
  docker: loadDocker,
  shares: loadShares,
  log: loadLog,
  backup: loadBackup,
  terminal: loadTerminal,
  jobs: loadJobs,
  updates: () => Promise.all([loadUpdateProgress(), loadAutoUpdates(), loadRootSnapshots()]),
  account: loadAccount,
};

async function open(view) {
  show(view);
  await guarded(null, loaders[view]);
}

// --- overview -------------------------------------------------------------------

function renderGauge(id, pct, sub) {
  const r = 42;
  const len = 2 * Math.PI * r;
  const box = document.getElementById(id);
  if (!box.firstElementChild) {
    box.innerHTML = `<svg viewBox="0 0 100 100"><circle class="track" cx="50" cy="50" r="${r}"/>`
      + `<circle class="value" cx="50" cy="50" r="${r}" stroke-dasharray="${len}" stroke-dashoffset="${len}"/></svg>`
      + `<div class="label"><b></b><small></small></div>`;
  }
  const value = box.querySelector(".value");
  value.setAttribute("stroke-dashoffset", String(len * (1 - pct / 100)));
  value.setAttribute("class", `value ${level(pct)}`);
  box.querySelector("b").textContent = `${pct}%`;
  box.querySelector("small").textContent = sub || "";
}

function renderSpark(id, values) {
  const box = document.getElementById(id);
  const w = 300;
  const h = 40;
  if (values.length < 2) { box.replaceChildren(); return; }
  const n = values.length - 1;
  const pts = values.map((v, i) => [(i * w) / n, h - (v / 100) * (h - 2) - 1]);
  const line = pts.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(" ");
  const area = `${pts[0][0].toFixed(1)},${h} ${line} ${w},${h}`;
  box.innerHTML = `<svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="none">`
    + `<polygon class="area" points="${area}"/><polyline class="line" points="${line}"/></svg>`;
}

function push(list, value) {
  list.push(value);
  if (list.length > HISTORY) list.shift();
}

// Name shown for a mount point, following the storage layout in the docs.
function fsLabel(mp) {
  if (mp === "/") return [t("fs.system"), 0];
  if (mp === "/mnt/user") return [t("fs.user"), 1];
  if (mp === "/mnt/storage") return [t("fs.storage"), 2];
  if (mp === "/mnt/cache") return [t("fs.cache"), 3];
  let m = mp.match(/^\/mnt\/disk(\d+)$/);
  if (m) return [t("fs.disk", { n: m[1] }), 10 + Number(m[1])];
  m = mp.match(/^\/mnt\/parity(\d+)$/);
  if (m) return [t("fs.parity", { n: m[1] }), 100 + Number(m[1])];
  m = mp.match(/^\/mnt\/external\/(.+)$/);
  if (m) return [t("fs.external", { name: m[1] }), 200];
  return [mp, 300];
}

function usageRow(name, sub, used, size) {
  const pct = percent(used, size);
  const row = el("div", "usage");
  const label = el("div", "name");
  label.append(el("b", "", name), el("small", "", sub));
  const bar = el("div", "bar");
  const fill = el("span", level(pct));
  fill.style.width = `${pct}%`;
  bar.append(fill);
  bar.title = `${pct}%`;
  const nums = el("div", "nums", t("overview.used_of", {
    used: formatBytes(used), size: formatBytes(size), pct,
  }));
  row.append(label, bar, nums);
  return row;
}

function renderFilesystems(filesystems) {
  const box = document.getElementById("fs-list");
  box.replaceChildren();
  const list = filesystems.map((f) => ({ ...f, label: fsLabel(f.mountpoint) }))
    .sort((a, b) => a.label[1] - b.label[1] || a.mountpoint.localeCompare(b.mountpoint));
  for (const f of list) {
    box.append(usageRow(f.label[0], `${f.mountpoint} · ${f.fstype}`, f.used, f.size));
  }
  if (!list.length) box.append(el("p", "empty", t("overview.no_fs")));
}

function renderNetwork(network, prev, seconds) {
  const box = document.getElementById("net-info");
  box.replaceChildren();
  for (const n of network) {
    const before = prev && prev.find((p) => p.name === n.name);
    const rx = before && seconds ? (n.rx_bytes - before.rx_bytes) / seconds : null;
    const tx = before && seconds ? (n.tx_bytes - before.tx_bytes) / seconds : null;
    const item = el("div", "net");
    const head = el("div", "net-head");
    head.append(el("b", "", n.name), el("small", "", n.addresses.join(", ") || t("overview.no_address")));
    const rates = el("div", "net-rates");
    rates.append(el("span", "rx", rx === null ? "…" : formatRate(Math.max(0, rx))),
                 el("span", "tx", tx === null ? "…" : formatRate(Math.max(0, tx))));
    const totals = el("small", "hint", t("overview.net_total", {
      rx: formatBytes(n.rx_bytes), tx: formatBytes(n.tx_bytes),
    }));
    item.append(head, rates, totals);
    box.append(item);
  }
  if (!network.length) box.append(el("p", "empty", t("overview.no_network")));
}

function renderStats(s) {
  const prev = lastStats;
  const seconds = prev ? (s.at - prev.at) / 1000 : 0;

  let cpuPct = 0;
  if (prev) {
    const total = s.cpu.total - prev.cpu.total;
    const idle = s.cpu.idle - prev.cpu.idle;
    cpuPct = total > 0 ? Math.round(((total - idle) / total) * 100) : 0;
    push(history.cpu, cpuPct);
  }
  renderGauge("cpu-gauge", cpuPct, t("overview.cpu_busy"));
  renderSpark("cpu-history", history.cpu);
  const cpu = document.getElementById("cpu-info");
  cpu.replaceChildren();
  fact(cpu, "overview.cores", String(s.cpu.cores));
  fact(cpu, "overview.load", s.load.map((x) => x.toFixed(2)).join(" / "));
  fact(cpu, "overview.uptime", formatUptime(s.uptime_seconds));
  if (cpuTemp !== null) fact(cpu, "overview.temperature", formatTemp(cpuTemp));

  const used = s.memory.total - s.memory.available;
  const memPct = percent(used, s.memory.total);
  push(history.mem, memPct);
  renderGauge("mem-gauge", memPct, t("overview.mem_used"));
  renderSpark("mem-history", history.mem);
  const mem = document.getElementById("mem-info");
  mem.replaceChildren();
  fact(mem, "overview.mem_used", formatBytes(used));
  fact(mem, "overview.mem_total", formatBytes(s.memory.total));
  fact(mem, "overview.swap", s.swap.total
    ? t("overview.used_of", { used: formatBytes(s.swap.used), size: formatBytes(s.swap.total),
                              pct: percent(s.swap.used, s.swap.total) })
    : t("overview.swap_none"));

  renderNetwork(s.network, prev && prev.network, seconds);
  renderFilesystems(s.filesystems);
  lastStats = s;
}

async function pollStats() {
  try {
    const s = await api("GET", "stats");
    s.at = Date.now();
    if (current === "overview") renderStats(s);
  } catch (err) {
    if (!(err instanceof ApiError && err.status === 401)) sayError(err);
    stopStats();
  }
}

function startStats() {
  stopStats();
  statsTimer = setInterval(pollStats, STATS_INTERVAL);
}

function stopStats() {
  if (statsTimer) clearInterval(statsTimer);
  statsTimer = null;
}

function setSidebar(info) {
  document.getElementById("side-hostname").textContent = `${info.hostname}.local`;
  document.title = t("app.title", { host: info.hostname });
  document.getElementById("side-version").textContent = info.version
    ? t("overview.version_value", { version: info.version }) : "";
}

const STATUS_KIND = { running: "ok", partial: "warn", restarting: "warn", exited: "", created: "",
                      paused: "warn", dead: "crit", missing: "crit" };
const DOT_KIND = { ok: "ok", warn: "warn", crit: "crit", "": "" };

function primaryUrl(app) {
  if (!app.public_port) return null;
  const urls = app.urls || {};
  return urls.mdns || `http://${location.hostname}:${app.public_port}`;
}

function renderOverviewApps(installed) {
  const box = document.getElementById("overview-apps");
  box.replaceChildren();
  for (const app of installed) {
    const item = el("div", "app-mini");
    const meta = el("div", "meta");
    meta.append(el("b", "", app.name), el("small", "", tv("app_status", app.status)));
    item.append(el("span", `dot ${DOT_KIND[STATUS_KIND[app.status] ?? ""]}`), avatar(app.id, app.name, app.icon), meta);
    if (app.update) item.append(badge(t("apps.update_short"), "accent"));
    const url = primaryUrl(app);
    if (url && app.status === "running") {
      const a = link(url, "");
      a.title = t("apps.open");
      a.append(icon("external"));
      item.append(a);
    }
    box.append(item);
  }
  if (!installed.length) box.append(el("p", "empty", t("apps.none_installed")));
}

const RESULT_KIND = { success: "ok", "exit-code": "crit", signal: "warn", timeout: "crit" };

function jobBadge(s) {
  if (s.active === "active" || s.active === "activating") return badge(tv("job_state", s.active), "info");
  if (s.active === "failed") return badge(tv("job_state", s.active), "crit");
  if (s.result) return badge(tv("job_result", s.result), RESULT_KIND[s.result] || "");
  return badge(t("jobs.never"), "");
}

function renderOverviewJobs(jobs) {
  const box = document.getElementById("overview-jobs");
  box.replaceChildren();
  for (const [name, s] of Object.entries(jobs)) {
    const item = el("div", "job-mini");
    item.append(el("span", "", t(`job.${name}`)), jobBadge(s));
    box.append(item);
  }
}

function updateAppsBadge(installed) {
  const n = installed.filter((a) => a.update).length;
  const b = document.getElementById("nav-apps-badge");
  b.hidden = !n;
  b.textContent = String(n);
}

async function loadOverview() {
  const [info, stats] = await Promise.all([api("GET", "system"), api("GET", "stats")]);
  setSidebar(info);
  const dl = document.getElementById("system-info");
  dl.replaceChildren();
  fact(dl, "overview.hostname", `${info.hostname}.local`);
  fact(dl, "overview.version", info.version || "-");
  fact(dl, "overview.kernel", info.kernel);
  fact(dl, "overview.cpu_model", stats.cpu.model || "-");
  const nic = stats.network.find((n) => n.addresses.length);
  fact(dl, "overview.address", nic ? nic.addresses[0].split("/")[0] : location.hostname);
  stats.at = Date.now();
  renderStats(stats);
  // Apps and jobs come from slower calls; the tiles fill in when they arrive.
  api("GET", "apps").then((data) => {
    renderOverviewApps(data.installed);
    updateAppsBadge(data.installed);
  }).catch(tileError("overview-apps"));
  api("GET", "jobs").then(renderOverviewJobs).catch(tileError("overview-jobs"));
  loadHistory().catch(sayError);
  api("GET", "hardware").then((hw) => {
    renderOverviewDisks(hw);
    const board = [hw.board.board_vendor, hw.board.board_name].filter(Boolean).join(" ")
      || [hw.board.sys_vendor, hw.board.product_name].filter(Boolean).join(" ");
    if (board) fact(dl, "overview.board", board);
    const gpu = (hw.gpus || []).filter((g) => g.render)
      .map((g) => [g.vendor, g.driver && `(${g.driver})`].filter(Boolean).join(" ")).join(", ");
    if (gpu) fact(dl, "overview.gpu", gpu);
  }).catch(tileError("overview-disks"));
}

function renderOverviewDisks(hw) {
  cpuTemp = hw.cpu_temperature;
  const box = document.getElementById("overview-disks");
  box.replaceChildren();
  for (const d of hw.disks) {
    const item = el("div", "disk-mini");
    const meta = el("div", "meta");
    meta.append(el("b", "", d.path.replace(/^\/dev\//, "")),
                el("small", "", [d.model, formatBytes(d.size)].filter(Boolean).join(" · ")));
    const temp = el("span", `temp ${tempKind(d.temperature, d.rotational)}`,
                    d.temperature === null || d.temperature === undefined ? "–" : formatTemp(d.temperature));
    item.append(meta, temp, healthBadge(d));
    box.append(item);
  }
  if (!hw.disks.length) box.append(el("p", "empty", t("storage.none")));
}

// --- history --------------------------------------------------------------------

const HISTORY_RANGES = ["hour", "day", "week", "month", "year"];
let historyRange = "day";

async function loadHistory() {
  const data = await api("GET", `history?range=${historyRange}`);
  renderHistoryRange();
  const stamps = Array.from({ length: data.points }, (_, i) => data.start + i * data.step);
  const s = data.series;
  // All interfaces together, per direction.
  const sum = (prefix) => {
    const keys = Object.keys(s).filter((k) => k.startsWith(prefix));
    if (!keys.length) return null;
    return stamps.map((_, i) => {
      const vals = keys.map((k) => s[k][i]).filter((v) => v !== null);
      return vals.length ? vals.reduce((a, b) => a + b, 0) : null;
    });
  };
  const pct = (v) => `${Math.round(v)} %`;
  renderChart("chart-cpu", stamps, [s.cpu], { max: 100, fmt: pct });
  renderChart("chart-mem", stamps, [s.mem], { max: 100, fmt: pct });
  renderChart("chart-net", stamps, [sum("net_rx:"), sum("net_tx:")],
              { fmt: formatRate, legend: [t("history.rx"), t("history.tx")] });
  const hasTemp = Boolean(s.temp_cpu && s.temp_cpu.some((v) => v !== null));
  document.getElementById("chart-temp").hidden = !hasTemp;
  if (hasTemp) renderChart("chart-temp", stamps, [s.temp_cpu], { fmt: formatTemp });
  document.getElementById("history-empty").hidden = Object.keys(s).length > 0;
}

function renderHistoryRange() {
  const box = document.getElementById("history-range");
  if (!box.firstElementChild) {
    for (const r of HISTORY_RANGES) {
      const b = el("button", "", tv("history_range", r));
      b.type = "button";
      b.dataset.range = r;
      b.addEventListener("click", () => {
        historyRange = r;
        loadHistory().catch(sayError);
      });
      box.append(b);
    }
  }
  for (const b of box.children) b.setAttribute("aria-pressed", String(b.dataset.range === historyRange));
}

function axisTime(sec) {
  const d = new Date(sec * 1000);
  if (historyRange === "hour") return d.toLocaleTimeString(lang, { hour: "2-digit", minute: "2-digit" });
  // A day starts and ends at the same clock time; the weekday tells them apart.
  if (historyRange === "day") {
    return d.toLocaleString(lang, { weekday: "short", hour: "2-digit", minute: "2-digit" });
  }
  if (historyRange === "year") return d.toLocaleDateString(lang, { month: "short", year: "numeric" });
  return d.toLocaleDateString(lang, { day: "2-digit", month: "2-digit" });
}

// Line chart without a library: one polyline per run of values, gaps stay empty.
function renderChart(id, stamps, lines, opts) {
  const fig = document.getElementById(id);
  const plot = fig.querySelector(".plot");
  const stats = fig.querySelector("figcaption small");
  const w = 600;
  const h = 160;
  const values = lines.filter(Boolean).flat().filter((v) => v !== null);
  const peak = values.length ? Math.max(...values) : 0;
  const max = opts.max ?? (peak > 0 ? peak * 1.1 : 1);
  const n = Math.max(stamps.length - 1, 1);
  const ns = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(ns, "svg");
  svg.setAttribute("viewBox", `0 0 ${w} ${h}`);
  svg.setAttribute("preserveAspectRatio", "none");
  const grid = document.createElementNS(ns, "path");
  grid.setAttribute("class", "grid");
  grid.setAttribute("d", `M0 ${h / 2} H${w} M0 ${h / 4} H${w} M0 ${(3 * h) / 4} H${w}`);
  svg.append(grid);
  lines.forEach((series, li) => {
    if (!series) return;
    let run = [];
    const flush = () => {
      if (run.length > 1) {
        const pts = run.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(" ");
        if (li === 0 && lines.length === 1) {
          const area = document.createElementNS(ns, "polygon");
          area.setAttribute("class", "area");
          area.setAttribute("points", `${run[0][0].toFixed(1)},${h} ${pts} ${run.at(-1)[0].toFixed(1)},${h}`);
          svg.append(area);
        }
        const pl = document.createElementNS(ns, "polyline");
        pl.setAttribute("class", li ? "line second" : "line");
        pl.setAttribute("points", pts);
        svg.append(pl);
      }
      run = [];
    };
    series.forEach((v, i) => {
      if (v === null) { flush(); return; }
      run.push([(i * w) / n, h - (Math.min(v, max) / max) * (h - 4) - 2]);
    });
    flush();
  });
  const label = (cls, text) => el("span", `axis ${cls}`, text);
  plot.replaceChildren(svg, label("top", opts.fmt(max)), label("start", axisTime(stamps[0])),
                       label("end", axisTime(stamps.at(-1))));
  stats.replaceChildren();
  if (opts.legend) {
    const leg = el("span", "legend");
    opts.legend.forEach((name, i) => {
      const item = el("span", "", name);
      item.prepend(el("i", i ? "second" : ""));
      leg.append(item);
    });
    stats.append(leg);
  }
  const first = (lines[0] || []).filter((v) => v !== null);
  if (first.length && !opts.legend) {
    const avg = first.reduce((a, b) => a + b, 0) / first.length;
    stats.textContent = t("history.stats", { avg: opts.fmt(avg), max: opts.fmt(Math.max(...first)) });
  }
}

// --- storage --------------------------------------------------------------------

const DISK_KIND = { registered: "ok", candidate: "info", "external-only": "info", system: "accent",
                    mounted: "warn", foreign: "warn", empty: "", unsupported: "" };

const POOLS = [["/mnt/user", "fs.user", "storage.pool_user"],
               ["/mnt/storage", "fs.storage", "storage.pool_storage"],
               ["/mnt/cache", "fs.cache", "storage.pool_cache"]];

function renderPools(filesystems) {
  const box = document.getElementById("pool-summary");
  box.replaceChildren();
  for (const [mp, nameKey, hintKey] of POOLS) {
    const f = filesystems.find((x) => x.mountpoint === mp);
    const card = el("article", "card pool");
    const head = el("div", "pool-head");
    head.append(el("b", "", t(nameKey)), el("small", "", mp));
    card.append(head);
    if (f) {
      const pct = percent(f.used, f.size);
      const bar = el("div", "bar");
      const fill = el("span", level(pct));
      fill.style.width = `${pct}%`;
      bar.append(fill);
      card.append(bar, el("div", "nums", t("overview.used_of", {
        used: formatBytes(f.used), size: formatBytes(f.size), pct,
      })));
    } else {
      card.append(el("div", "nums", t("storage.pool_missing")));
    }
    card.append(el("p", "hint", t(hintKey)));
    box.append(card);
  }
}

function diskRow(d, mounts) {
  const tpl = document.getElementById("disk-row");
  const row = tpl.content.firstElementChild.cloneNode(true);
  for (const e of row.querySelectorAll("[data-i18n]")) e.textContent = t(e.dataset.i18n);
  row.querySelector(".path").textContent = d.path;
  row.querySelector(".fs").textContent = d.fstype || t("storage.no_fs");
  row.querySelector(".size").textContent = formatBytes(d.size);
  const status = row.querySelector(".status");
  status.textContent = tv("disk_status", d.status);
  if (DISK_KIND[d.status]) status.classList.add(DISK_KIND[d.status]);
  status.title = d.reason;

  const usage = row.querySelector(".part-usage");
  const fs = mounts.filter((m) => m.source === d.path);
  for (const m of fs.slice(0, 1)) {
    const pct = percent(m.used, m.size);
    const bar = el("div", "bar");
    const fill = el("span", level(pct));
    fill.style.width = `${pct}%`;
    bar.append(fill);
    usage.append(el("div", "part-label", `${fsLabel(m.mountpoint)[0]} · ${m.mountpoint}`), bar,
                 el("div", "nums", t("overview.used_of", {
                   used: formatBytes(m.used), size: formatBytes(m.size), pct,
                 })));
  }

  const role = row.querySelector(".role");
  const add = row.querySelector(".add");
  const remove = row.querySelector(".remove");
  const canAdd = d.status === "candidate" || d.status === "external-only";
  if (d.status === "external-only") {
    for (const o of role.options) o.disabled = o.value !== "external";
    role.value = "external";
  }
  role.hidden = add.hidden = !canAdd;
  remove.hidden = d.status !== "registered";
  const importBtn = row.querySelector(".import");
  importBtn.hidden = !canAdd || d.imported;
  importBtn.addEventListener("click", () => openImportDialog(d));
  if (d.imported) row.querySelector(".part-main").append(badge(t("storage.imported"), "ok"));
  const format = row.querySelector(".format");
  // A whole blank disk, or a filesystem whose data was imported (formats its whole disk).
  format.hidden = !((d.status === "empty" && d.path === d.disk) || d.imported);
  format.addEventListener("click", () => openFormatDialog(d));

  add.addEventListener("click", () => guarded(add, async () => {
    if (!await ask(t("storage.confirm_add", { path: d.path, role: t(`role.${role.value}`) }),
                   "storage.add")) return;
    await api("POST", "storage/add", { uuid: d.uuid, role: role.value });
    say(t("storage.added", { path: d.path }), "ok");
    await loadStorage();
  }));
  remove.addEventListener("click", () => guarded(remove, async () => {
    if (!await ask(t("storage.confirm_remove", { path: d.path }), "storage.remove", true)) return;
    try {
      await api("POST", "storage/remove", { uuid: d.uuid });
    } catch (err) {
      // A cache that still holds files needs an explicit second yes.
      if (!(err instanceof ApiError) || err.status !== 422 || !/--force/.test(err.detail)) throw err;
      if (!await ask(t("storage.confirm_force", { detail: err.detail }), "storage.remove", true)) return;
      await api("POST", "storage/remove", { uuid: d.uuid, force: true });
    }
    say(t("storage.removed", { path: d.path }), "ok");
    await loadStorage();
  }));
  return row;
}

// SMART self-test of one disk: last result and buttons to start or abort.
function smartTestRow(disk, h) {
  const row = el("div", "disk-tests");
  const st = h.self_test || {};
  let text = t("smart.test_none");
  if (st.running) {
    text = st.remaining !== null && st.remaining !== undefined
      ? t("smart.test_running", { n: st.remaining }) : t("smart.test_running_unknown");
  } else if (st.last) {
    text = t("smart.test_last", { kind: tv("smart_kind", st.last.kind), h: st.last.hours ?? "?" });
  }
  const label = el("span", "", text);
  row.append(label);
  if (!st.running && st.last) {
    row.append(badge(tv("smart_result", st.last.result),
                     { passed: "ok", failed: "crit", aborted: "warn" }[st.last.result] || ""));
  }
  const ops = el("div", "row-ops");
  const run = (kind) => async () => {
    await api("POST", "disks/smart-test", { device: disk, kind });
    say(t(kind === "abort" ? "smart.test_aborted" : "smart.test_started",
          { path: disk, kind: tv("smart_kind", kind) }), "ok");
    await loadStorage();
  };
  if (st.running) ops.append(button("smart.test_abort", run("abort")));
  else ops.append(button("smart.test_short", run("short")), button("smart.test_long", run("long")));
  row.append(ops);
  return row;
}

let burninTimer = null;

function burninRow(disk, test) {
  const row = el("div", "disk-tests");
  const kind = { running: "info", passed: "ok", failed: "crit", stopped: "warn" }[test.result] || "";
  row.append(badge(tv("burnin_result", test.result), kind));
  const parts = [];
  if (test.result === "running") parts.push(t("burnin.progress", { pass: test.pass, percent: test.percent.toFixed(1) }));
  if (test.result !== "running" || test.bad_blocks) parts.push(t("burnin.bad_blocks", { n: test.bad_blocks }));
  const grown = Object.entries(test.smart_new || {}).map(([k, v]) => `${tv("smart", k)}: ${v}`);
  if (grown.length) parts.push(t("burnin.smart_new", { list: grown.join(", ") }));
  row.append(el("span", "", parts.join(" · ")));
  const ops = el("div", "row-ops");
  if (test.result === "running") {
    ops.append(button("burnin.stop", async () => {
      if (!await ask(t("burnin.stop_confirm", { path: disk }), "burnin.stop", true)) return;
      await api("POST", "disks/burnin/stop", { device: disk });
      await loadStorage();
    }, "danger"));
    // Long-running: refresh the storage page while it is open.
    clearTimeout(burninTimer);
    burninTimer = setTimeout(() => { if (current === "storage") loadStorage().catch(sayError); }, 30000);
  } else {
    ops.append(button("burnin.clear", async () => {
      await api("POST", "disks/burnin/clear", { device: disk });
      await loadStorage();
    }));
  }
  row.append(ops);
  return row;
}

function openBurninDialog(d) {
  const form = document.getElementById("burnin-form");
  const name = d.disk.replace("/dev/", "");
  form.reset();
  form.dataset.device = d.disk;
  document.getElementById("burnin-title").textContent = t("burnin.title", { path: d.disk });
  document.getElementById("burnin-warning").textContent = t("burnin.warning",
    { path: d.disk, model: d.model || "-", size: formatBytes(d.size) });
  document.getElementById("burnin-confirm-label").textContent = t("storage.format_confirm", { name });
  form.elements.confirm.pattern = name;
  document.getElementById("burnin-dialog").showModal();
}

function bindBurnin() {
  const form = document.getElementById("burnin-form");
  form.addEventListener("submit", (e) => {
    if (e.submitter?.value === "cancel") return;
    e.preventDefault();
    const device = form.dataset.device;
    guarded(e.submitter, async () => {
      await api("POST", "disks/burnin/start", { device, confirm: form.elements.confirm.value.trim() });
      document.getElementById("burnin-dialog").close();
      say(t("burnin.started", { path: device }), "ok");
      await loadStorage();
    });
  });
}

function openFormatDialog(d) {
  const form = document.getElementById("format-form");
  const name = d.disk.replace("/dev/", "");
  form.reset();
  form.dataset.device = d.disk;
  document.getElementById("format-title").textContent = t("storage.format_title", { path: d.disk });
  document.getElementById("format-warning").textContent = t(
    d.imported ? "storage.format_warning_imported" : "storage.format_warning",
    { path: d.disk, model: d.model || "-", size: formatBytes(d.size) });
  document.getElementById("format-confirm-label").textContent = t("storage.format_confirm", { name });
  form.elements.confirm.pattern = name;
  document.getElementById("format-dialog").showModal();
}

function bindFormat() {
  const form = document.getElementById("format-form");
  form.addEventListener("submit", (e) => {
    if (e.submitter?.value === "cancel") return;
    e.preventDefault();
    const device = form.dataset.device;
    const body = { device, filesystem: form.elements.filesystem.value,
                   confirm: form.elements.confirm.value.trim() };
    guarded(e.submitter, async () => {
      await api("POST", "storage/format", body);
      document.getElementById("format-dialog").close();
      say(t("storage.formatted", { path: device }), "ok");
      await loadStorage();
    });
  });
}

const IMPORT_RUNNING = ["mount", "copy", "verify"];
let importTimer = null;
let importWasRunning = false;

function openImportDialog(d) {
  const form = document.getElementById("import-form");
  form.reset();
  form.dataset.uuid = d.uuid;
  const base = (d.label || d.path.replace("/dev/", "")).toLowerCase().replace(/[^a-z0-9._-]+/g, "-");
  form.elements.target.value = `import-${base}`.replace(/-+$/, "").slice(0, 64);
  document.getElementById("import-title").textContent = t("storage.import_title", { path: d.path });
  document.getElementById("import-text").textContent = t("storage.import_text", {
    path: d.path, fs: d.fstype, size: formatBytes(d.size),
  });
  document.getElementById("import-dialog").showModal();
}

function renderImport(job) {
  const box = document.getElementById("import-status");
  box.replaceChildren();
  box.hidden = !job;
  if (!job) return;
  const running = IMPORT_RUNNING.includes(job.phase);
  const head = el("b", "", t(`import.${job.phase}`, { path: job.device, target: job.target }));
  box.append(head);
  if (running) {
    const bar = el("div", "bar");
    const fill = el("span");
    fill.style.width = `${job.percent || 0}%`;
    bar.append(fill);
    box.append(bar, el("small", "hint", t("import.progress", {
      done: formatBytes(job.bytes || 0), total: formatBytes(job.total || 0), pct: job.percent || 0,
    })));
  } else if (job.phase === "failed") {
    box.append(el("small", "fail", job.error));
  }
  box.classList.toggle("bad", job.phase === "failed");
}

async function pollImport() {
  clearTimeout(importTimer);
  const { import: job } = await api("GET", "storage/import");
  renderImport(job);
  const running = Boolean(job) && IMPORT_RUNNING.includes(job.phase);
  if (importWasRunning && !running) {
    importWasRunning = false;
    say(t(job?.phase === "done" ? "import.finished" : "import.failed_short"),
        job?.phase === "done" ? "ok" : "error");
    await loadStorage();
    return;
  }
  importWasRunning = running;
  if (running && current === "storage") {
    importTimer = setTimeout(() => pollImport().catch(sayError), 3000);
  }
}

function bindImport() {
  const form = document.getElementById("import-form");
  form.addEventListener("submit", (e) => {
    if (e.submitter?.value === "cancel") return;
    e.preventDefault();
    guarded(e.submitter, async () => {
      await api("POST", "storage/import", {
        uuid: form.dataset.uuid, target: form.elements.target.value.trim(),
      });
      document.getElementById("import-dialog").close();
      importWasRunning = true;
      await pollImport();
    });
  });
}

// --- storage settings -------------------------------------------------------------

const MOVER_AGES = [0, 60, 360, 720, 1440, 4320, 10080, 43200];

function moverAgeLabel(m) {
  if (m === 0) return t("storage.mover_age_none");
  if (m % 1440 === 0) return t("storage.mover_age_days", { n: m / 1440 });
  if (m % 60 === 0) return t("storage.mover_age_hours", { n: m / 60 });
  return t("storage.mover_age_minutes", { n: m });
}

async function loadStorageSettings() {
  const { settings } = await api("GET", "storage/settings");
  const f = document.getElementById("storage-settings");
  if (document.activeElement?.form === f) return;
  const ages = MOVER_AGES.includes(settings.mover_min_age_minutes)
    ? MOVER_AGES : [...MOVER_AGES, settings.mover_min_age_minutes].sort((a, b) => a - b);
  fillOptions(f.elements.mover_min_age_minutes, ages, moverAgeLabel);
  f.elements.mover_min_age_minutes.value = String(settings.mover_min_age_minutes);
  // minfreespace is a number with an optional K/M/G/T suffix; show it in MB, GB or TB.
  const m = /^(\d+)([KMGT]?)$/.exec(settings.minfreespace) || [null, "50", "G"];
  let [value, unit] = [Number(m[1]), m[2] || "B"];
  const bytes = value * { B: 1, K: 1024, M: 1024 ** 2, G: 1024 ** 3, T: 1024 ** 4 }[unit];
  if (!["M", "G", "T"].includes(unit)) [value, unit] = [Math.max(1, Math.round(bytes / 1024 ** 2)), "M"];
  fillOptions(f.elements.free_unit, ["M", "G", "T"], (u) => t(`storage.unit_${u}`));
  f.elements.free_value.value = String(value);
  f.elements.free_unit.value = unit;
  f.elements.snapraid_delete_threshold.value = String(settings.snapraid_delete_threshold);
}

function bindStorageSettings() {
  const f = document.getElementById("storage-settings");
  f.addEventListener("submit", (e) => {
    e.preventDefault();
    const btn = f.querySelector("button");
    guarded(btn, async () => {
      await api("POST", "storage/settings", {
        minfreespace: `${Number(f.elements.free_value.value)}${f.elements.free_unit.value}`,
        mover_min_age_minutes: Number(f.elements.mover_min_age_minutes.value),
        snapraid_delete_threshold: Number(f.elements.snapraid_delete_threshold.value),
      });
      say(t("storage.settings_saved"), "ok");
      btn.blur();
      await loadStorageSettings();
    });
  });
}

async function loadStorage() {
  const [data, stats] = await Promise.all([api("GET", "storage"), api("GET", "stats")]);
  // SMART is slow on many disks; the cards fill in their health when it arrives.
  const hwReady = api("GET", "hardware").catch(() => null);
  const burnin = await api("GET", "disks/burnin").catch(() => ({ tests: [] }));
  renderPools(stats.filesystems);
  const box = document.getElementById("disks");
  box.replaceChildren();
  // One card per physical disk, its filesystems below, like the Main tab of a NAS.
  const groups = new Map();
  for (const d of data.disks) {
    const key = d.disk || d.path;
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(d);
  }
  for (const [disk, parts] of groups) {
    const first = parts[0];
    const card = el("article", "card disk");
    const head = el("div", "disk-head");
    const title = el("div", "title");
    const model = [first.model, first.serial && t("storage.serial", { serial: first.serial })]
      .filter(Boolean).join(" · ");
    title.append(el("b", "", disk.replace(/^\/dev\//, "")), el("small", "", model || t("storage.unknown_model")));
    head.append(icon("storage"), title);
    const kinds = new Set(parts.map((p) => p.status));
    if (kinds.has("system")) head.append(badge(t("disk_status.system"), "accent"));
    else if (kinds.has("registered")) head.append(badge(t("disk_status.registered"), "ok"));
    else if (kinds.has("candidate")) head.append(badge(t("disk_status.candidate"), "info"));
    const health = el("span", "disk-health");
    head.append(health);
    hwReady.then((hw) => {
      const h = hw && hw.disks.find((x) => x.path === disk);
      if (!h) return;
      if (h.temperature !== null && h.temperature !== undefined) {
        health.append(el("span", `temp ${tempKind(h.temperature, h.rotational)}`, formatTemp(h.temperature)));
      }
      health.append(healthBadge(h));
      if (h.power_on_hours) health.title = t("storage.power_on", { h: h.power_on_hours });
      if (!["unavailable", "unknown"].includes(h.health)) head.after(smartTestRow(disk, h));
    });
    card.append(head);
    const test = burnin.tests.find((x) => x.device === disk);
    const blank = !first.removable && parts.every((p) => p.kind === "disk" && p.status === "empty");
    if (test) card.append(burninRow(disk, test));
    else if (blank && !disk.startsWith("/dev/loop")) {
      const row = el("div", "disk-tests");
      const ops = el("div", "row-ops");
      ops.append(button("burnin.start", () => openBurninDialog(first), "danger"));
      row.append(el("span", "hint", t("burnin.offer")), ops);
      card.append(row);
    }
    for (const d of parts) card.append(diskRow(d, stats.filesystems));
    box.append(card);
  }
  if (!groups.size) box.append(el("p", "empty", t("storage.none")));
  await pollImport();
}

// --- apps -----------------------------------------------------------------------

let catalogApps = [];
let gpuAvailable = false;

// Install and update run in the background on the server; poll until done.
async function waitForAppJob(id) {
  for (let i = 0; i < 360; i++) {
    await new Promise((r) => setTimeout(r, 5000));
    const job = await api("GET", `apps/${id}/job`);
    if (!["active", "activating"].includes(job.active)) return job;
  }
  return null;
}

async function appJob(id, op, name, body) {
  await api("POST", `apps/${id}/${op}`, body);
  say(t(`apps.${op}_running`, { name }), "");
  await loadApps();
  const job = await waitForAppJob(id);
  if (job && job.result === "success") say(t(`apps.${op}_done`, { name }), "ok");
  else if (job && job.error) say(t("apps.job_failed_reason", { name, error: job.error }), "error");
  else say(t("apps.job_failed", { name, unit: `dahlia-app-${id}` }), "error");
  if (current === "apps") await loadApps();
}

// Domain name, mDNS alias and port link; the port link uses the address
// the dashboard was opened with, since that one is known to work.
function appLinks(app) {
  const list = el("div", "links");
  if (!app.public_port) return list;
  const urls = app.urls || {};
  for (const href of [urls.mdns, `http://${location.hostname}:${app.public_port}`]) {
    if (href) list.append(link(href));
  }
  return list;
}

function helpers(app) {
  return app.services.filter((s) => s.name !== "app").map((s) => s.image).join(", ");
}

async function updateAll(apps) {
  for (const app of apps) await appJob(app.id, "update", app.name);
}

function cardHead(app, sub) {
  const head = el("div", "head");
  const title = el("div", "title");
  title.append(el("b", "", app.name), el("small", "", sub));
  head.append(avatar(app.id, app.name, app.icon), title);
  return head;
}

function renderInstalled(installed) {
  const box = document.getElementById("apps-installed");
  box.replaceChildren();
  for (const app of installed) {
    const card = el("article", "app-card");
    const head = cardHead(app, `${tv("category", app.category)} · ${app.version}`);
    head.append(badge(tv("app_status", app.status), STATUS_KIND[app.status] ?? ""));
    if (app.gpu && gpuAvailable) head.append(badge("GPU", "info"));
    if (app.custom) head.append(badge(t("custom.badge"), "info"));
    card.append(head);

    const notes = app.notes && (app.notes[lang] || app.notes.en);
    if (notes) card.append(el("div", "desc", notes));
    if (app.update) {
      const b = el("div", "badges");
      b.append(badge(t("apps.update_available", { version: app.update.version }), "accent"));
      card.append(b);
    }
    card.append(appLinks(app));
    if (app.login) {
      const d = el("details");
      d.append(el("summary", "", t("apps.login")),
               el("code", "", [app.login.user, app.login.password].filter(Boolean).join(" / ")));
      card.append(d);
    }

    const ops = el("div", "ops");
    if (app.status === "running") {
      ops.append(iconButton("stop", "apps.stop", async () => {
        await api("POST", `apps/${app.id}/stop`);
        await loadApps();
      }));
    } else {
      ops.append(iconButton("play", "apps.start", async () => {
        await api("POST", `apps/${app.id}/start`);
        await loadApps();
      }));
    }
    ops.append(iconButton("updates", "apps.update", () => appJob(app.id, "update", app.name),
                          app.update ? "primary" : ""));
    if (app.custom) ops.append(iconButton("docker", "custom.edit", () => customEdit(app)));
    const remove = iconButton("trash", "apps.remove", async () => {
      if (!await ask(t("apps.confirm_remove", { name: app.name }), "apps.remove", true)) return;
      const deleteData = await ask(t("apps.confirm_delete_data", {
        path: `${app.location || ARRAY}/appdata/${app.id}` }),
                                   "apps.delete_data", true);
      await api("POST", `apps/${app.id}/remove`, { delete_data: deleteData });
      say(t("apps.removed", { name: app.name }), "ok");
      await loadApps();
    }, "danger icon-btn push-right");
    // Icon only, so the three buttons fit on one line.
    remove.title = remove.textContent;
    remove.setAttribute("aria-label", remove.title);
    remove.lastChild.remove();
    ops.append(remove);
    card.append(ops);
    box.append(card);
  }
  if (!installed.length) box.append(el("p", "empty", t("apps.none_installed")));
  const pending = installed.filter((a) => a.update);
  const all = document.getElementById("apps-update-all");
  all.hidden = !pending.length;
  all.textContent = t("apps.update_all", { count: pending.length });
  all.onclick = () => guarded(all, () => updateAll(pending));
  updateAppsBadge(installed);
}

// --- install dialog and folder picker -------------------------------------------

// Locations for app data and folders: the array and the system SSD.
const ARRAY = "/mnt/user";
const SYSTEM = "/srv";
const REMOTES = "/mnt/remotes";
let folderRoot = ARRAY;
let folderPath = "";

function joinPath(base, name) {
  return base ? `${base}/${name}` : name;
}

function fullPath(root, path) {
  return path ? `${root}/${path}` : root;
}

// One button per location; the chosen one is pressed.
function placeButtons(box, current, available, onPick, roots = [["system", SYSTEM], ["array", ARRAY]]) {
  box.replaceChildren();
  for (const [name, root] of roots) {
    const b = el("button", "place");
    b.type = "button";
    b.setAttribute("aria-pressed", String(root === current));
    b.disabled = !available[root];
    b.append(el("b", "", tv("location", name)), el("small", "hint", root));
    b.addEventListener("click", () => onPick(root));
    box.append(b);
  }
}

async function showFolder(root, path) {
  const data = await api("GET", `files?root=${encodeURIComponent(root)}&path=${encodeURIComponent(path)}`);
  const available = {};
  for (const loc of data.locations) available[loc.path] = loc.available;
  folderRoot = root;
  folderPath = path;
  // Shares of other devices are offered once one is set up.
  const roots = [["system", SYSTEM], ["array", ARRAY]];
  if (available[REMOTES]) roots.push(["remotes", REMOTES]);
  placeButtons(document.getElementById("folder-roots"), root, available,
               (to) => showFolder(to, "").catch(sayError), roots);
  const crumbs = document.getElementById("folder-crumbs");
  crumbs.replaceChildren();
  const parts = path ? path.split("/") : [];
  const rootBtn = el("button", "link-btn", root);
  rootBtn.type = "button";
  rootBtn.addEventListener("click", () => showFolder(root, "").catch(sayError));
  crumbs.append(rootBtn);
  parts.forEach((part, i) => {
    const b = el("button", "link-btn", part);
    b.type = "button";
    const to = parts.slice(0, i + 1).join("/");
    b.addEventListener("click", () => showFolder(root, to).catch(sayError));
    crumbs.append(el("span", "sep", "/"), b);
  });
  const list = document.getElementById("folder-list");
  list.replaceChildren();
  // App data folders belong to their apps and are not offered.
  const dirs = data.dirs.filter((d) => path || d !== "appdata");
  if (!data.exists) list.append(el("li", "hint", t("folder.missing")));
  else if (!dirs.length) list.append(el("li", "hint", t("folder.empty")));
  for (const name of dirs) {
    const li = el("li");
    const b = el("button", "folder", name);
    b.type = "button";
    b.prepend(icon("folder"));
    b.addEventListener("click", () => showFolder(root, joinPath(path, name)).catch(sayError));
    li.append(b);
    list.append(li);
  }
  document.getElementById("folder-new").disabled = !data.mounted || (root === REMOTES && !path);
  document.getElementById("folder-ok").disabled = !path;
}

// Resolves with the chosen {root, path}, or null.
async function pickFolder(start) {
  const dlg = document.getElementById("folder-dialog");
  document.getElementById("folder-new-name").value = "";
  // Open the folder that holds the current choice, so siblings are visible.
  const parent = start.path.includes("/") ? start.path.slice(0, start.path.lastIndexOf("/")) : "";
  try {
    await showFolder(start.root, parent);
  } catch (_) {
    await showFolder(start.root, "");
  }
  dlg.returnValue = "";
  dlg.showModal();
  return new Promise((resolve) => {
    dlg.addEventListener("close", () => resolve(
      dlg.returnValue === "ok" ? { root: folderRoot, path: folderPath } : null), { once: true });
  });
}

function bindFolderPicker() {
  const btn = document.getElementById("folder-new");
  const input = document.getElementById("folder-new-name");
  const create = () => guarded(btn, async () => {
    const name = input.value.trim();
    if (!name) return;
    const path = joinPath(folderPath, name);
    await api("POST", "files/mkdir", { root: folderRoot, path });
    input.value = "";
    await showFolder(folderRoot, path);
  });
  btn.addEventListener("click", create);
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); create(); }
  });
}

// state: "ok", "bad" or "info".
function checkItem(state, text, action) {
  const li = el("li", state);
  li.append(el("span", "mark", { ok: "✓", bad: "✗", info: "i" }[state]), el("span", "", text));
  if (action) li.append(action);
  return li;
}

// Shows what the app needs and creates, lets the user pick where its data
// goes and its folders; resolves with {location, folders} or null on cancel.
async function installDialog(app) {
  const dlg = document.getElementById("install-dialog");
  const pool = await api("GET", `files?root=${encodeURIComponent(ARRAY)}&path=`);
  document.getElementById("install-title").textContent = t("install.title", { name: app.name });
  document.getElementById("install-desc").textContent = app.description[lang] || app.description.en || "";
  const checks = document.getElementById("install-checks");
  checks.replaceChildren();
  if (pool.mounted) {
    checks.append(checkItem("ok", t("install.pool_ok")));
  } else {
    const goStorage = el("button", "link-btn", t("install.to_storage"));
    goStorage.type = "button";
    goStorage.addEventListener("click", () => { dlg.close(); open("storage"); });
    checks.append(checkItem("info", t("install.pool_missing"), goStorage));
  }
  if (app.public_port) checks.append(checkItem("ok", t("install.web", { port: app.public_port })));
  const lan = app.lan_ports.map((p) => `${p.host}/${p.protocol}`).join(", ");
  if (lan) checks.append(checkItem("ok", t("apps.confirm_lan", { ports: lan })));
  const extra = helpers(app);
  if (extra) checks.append(checkItem("ok", t("apps.confirm_services", { services: extra })));
  checks.append(checkItem("ok", t("install.download")));

  let location = pool.mounted ? ARRAY : SYSTEM;
  // share name -> {root, path} when changed; unchanged shares follow the location.
  const chosen = {};
  const rows = [];
  const box = document.getElementById("install-shares");
  box.replaceChildren();
  const shares = app.shares || [];
  box.previousElementSibling.hidden = box.previousElementSibling.previousElementSibling.hidden = !shares.length;
  for (const share of shares) {
    const row = el("div", "share-pick");
    const label = el("div", "share-label");
    label.append(el("b", "", share.containers.join(", ")),
                 el("small", "hint", t(share.readonly ? "install.readonly" : "install.readwrite")));
    const path = el("code");
    const current = () => chosen[share.name] || { root: location, path: share.name };
    const show = () => { const c = current(); path.textContent = fullPath(c.root, c.path); };
    const change = el("button", "", t("install.change"));
    change.type = "button";
    change.addEventListener("click", async () => {
      const picked = await pickFolder(current());
      if (picked) {
        chosen[share.name] = picked;
        show();
      }
    });
    rows.push(show);
    row.append(label, path, change);
    box.append(row);
  }
  const appdata = document.getElementById("install-appdata");
  const placeBox = document.getElementById("install-location");
  const setLocation = (root) => {
    location = root;
    placeButtons(placeBox, location, { [SYSTEM]: true, [ARRAY]: pool.mounted }, setLocation);
    appdata.textContent = t("install.appdata", { path: fullPath(location, `appdata/${app.id}`) });
    rows.forEach((show) => show());
  };
  setLocation(location);
  const notes = app.notes && (app.notes[lang] || app.notes.en);
  const notesEl = document.getElementById("install-notes");
  notesEl.textContent = notes || "";
  notesEl.hidden = !notes;
  dlg.returnValue = "";
  dlg.showModal();
  return new Promise((resolve) => {
    dlg.addEventListener("close", () => {
      if (dlg.returnValue !== "ok") { resolve(null); return; }
      const folders = {};
      for (const [name, c] of Object.entries(chosen)) {
        if (c.root !== location || c.path !== name) folders[name] = fullPath(c.root, c.path);
      }
      resolve({ location, folders });
    }, { once: true });
  });
}

// --- custom containers ----------------------------------------------------------

function removeRowButton(row) {
  const b = el("button", "icon-btn", "✕");
  b.type = "button";
  b.title = t("custom.remove_row");
  b.setAttribute("aria-label", b.title);
  b.addEventListener("click", () => row.remove());
  return b;
}

function input(type, value, attrs) {
  const i = el("input");
  i.type = type;
  i.autocomplete = "off";
  if (value !== undefined && value !== null) i.value = String(value);
  for (const [k, v] of Object.entries(attrs || {})) i.setAttribute(k, v);
  return i;
}

function portRow(p) {
  const row = el("div", "custom-row");
  const container = input("number", p?.container, { min: 1, max: 65535, required: "" });
  container.placeholder = t("custom.container_port");
  const host = input("number", p?.host, { min: 1, max: 65535, required: "" });
  host.placeholder = t("custom.host_port");
  const proto = el("select");
  for (const v of ["tcp", "udp"]) {
    const o = el("option", "", v.toUpperCase());
    o.value = v;
    proto.append(o);
  }
  proto.value = p?.protocol || "tcp";
  row.append(container, host, proto, removeRowButton(row));
  row.read = () => ({ container: Number(container.value), host: Number(host.value),
                      protocol: proto.value });
  return row;
}

// Container path, then app data subfolder or a folder from the picker.
function volumeRow(v, locationOf) {
  const row = el("div", "custom-row");
  const container = input("text", v?.container, { required: "", pattern: "/.*" });
  container.placeholder = t("custom.container_path");
  const kind = el("select");
  for (const k of ["appdata", "folder"]) {
    const o = el("option", "", t(`custom.kind_${k}`));
    o.value = k;
    kind.append(o);
  }
  kind.value = v?.folder ? "folder" : "appdata";
  const sub = input("text", v?.appdata ?? "data", { pattern: "[a-z0-9][a-z0-9._\\-]{0,63}" });
  let folder = null;
  if (v?.folder) {
    const root = [ARRAY, SYSTEM, REMOTES].find((r) => v.folder.startsWith(`${r}/`)) || ARRAY;
    folder = { root, path: v.folder.slice(root.length + 1) };
  }
  const shown = el("code");
  const pick = el("button", "", t("install.change"));
  pick.type = "button";
  pick.addEventListener("click", async () => {
    const picked = await pickFolder(folder || { root: locationOf(), path: "" });
    if (picked) { folder = picked; update(); }
  });
  const ro = el("label", "check");
  const roBox = input("checkbox");
  roBox.checked = Boolean(v?.readonly);
  ro.append(roBox, el("span", "", t("install.readonly")));
  const update = () => {
    const isFolder = kind.value === "folder";
    sub.hidden = isFolder;
    sub.required = !isFolder;
    shown.hidden = pick.hidden = !isFolder;
    shown.textContent = folder ? fullPath(folder.root, folder.path) : t("custom.no_folder");
  };
  kind.addEventListener("change", update);
  update();
  row.append(container, kind, sub, shown, pick, ro, removeRowButton(row));
  row.read = () => {
    const out = { container: container.value.trim() };
    if (kind.value === "folder") {
      if (!folder) throw new ApiError(400, "custom.folder_missing");
      out.folder = fullPath(folder.root, folder.path);
    } else {
      out.appdata = sub.value.trim();
    }
    if (roBox.checked) out.readonly = true;
    return out;
  };
  return row;
}

function envRow(key, value) {
  const row = el("div", "custom-row");
  const k = input("text", key, { required: "", pattern: "[A-Za-z_][A-Za-z0-9_]{0,63}" });
  k.placeholder = t("custom.env_key");
  const v = input("text", value);
  v.placeholder = t("custom.env_value");
  row.append(k, v, removeRowButton(row));
  row.read = () => [k.value.trim(), v.value];
  return row;
}

// New custom container (app null) or the form of an installed one.
// Resolves with {id, spec, location} or null.
async function customDialog(app) {
  const dlg = document.getElementById("custom-dialog");
  const form = document.getElementById("custom-form");
  const spec = app?.custom || {};
  form.reset();
  form.elements.id.value = app?.id || "";
  form.elements.id.readOnly = Boolean(app);
  form.elements.name.value = spec.name || "";
  form.elements.image.value = spec.image || "";
  form.elements.web_port.value = spec.web_port ?? "";
  form.elements.public_port.value = spec.public_port ?? "";
  form.elements.gpu.checked = Boolean(spec.gpu);
  document.getElementById("custom-gpu-box").hidden = !gpuAvailable && !spec.gpu;
  document.getElementById("custom-title").textContent =
    app ? t("custom.edit_title", { name: app.name }) : t("custom.add");
  document.getElementById("custom-ok").textContent = t(app ? "custom.apply" : "apps.install");

  let location = app?.location || SYSTEM;
  const locBox = document.getElementById("custom-location-box");
  locBox.hidden = Boolean(app);
  const volHint = document.getElementById("custom-volumes-hint");
  const setLocation = (root) => {
    location = root;
    volHint.textContent = t("install.appdata", { path: fullPath(location, "appdata/<id>") });
  };
  if (!app) {
    const pool = await api("GET", `files?root=${encodeURIComponent(ARRAY)}&path=`);
    location = pool.mounted ? ARRAY : SYSTEM;
    const pick = (root) => {
      setLocation(root);
      placeButtons(document.getElementById("custom-location"), location,
                   { [SYSTEM]: true, [ARRAY]: pool.mounted }, pick);
    };
    pick(location);
  } else {
    setLocation(location);
  }

  const ports = document.getElementById("custom-ports");
  const volumes = document.getElementById("custom-volumes");
  const env = document.getElementById("custom-env");
  ports.replaceChildren(...(spec.lan_ports || []).map(portRow));
  volumes.replaceChildren(...(spec.volumes || []).map((v) => volumeRow(v, () => location)));
  env.replaceChildren(...Object.entries(spec.environment || {}).map(([k, v]) => envRow(k, v)));
  document.getElementById("custom-add-port").onclick = () => ports.append(portRow());
  document.getElementById("custom-add-volume").onclick =
    () => volumes.append(volumeRow(null, () => location));
  document.getElementById("custom-add-env").onclick = () => env.append(envRow("", ""));

  dlg.returnValue = "";
  dlg.showModal();
  return new Promise((resolve, reject) => {
    dlg.addEventListener("close", () => {
      if (dlg.returnValue !== "ok") { resolve(null); return; }
      try {
        const f = form.elements;
        const out = { name: f.name.value.trim(), image: f.image.value.trim() };
        if (f.web_port.value || f.public_port.value) {
          out.web_port = Number(f.web_port.value);
          out.public_port = Number(f.public_port.value);
        }
        out.lan_ports = [...ports.children].map((r) => r.read());
        out.volumes = [...volumes.children].map((r) => r.read());
        out.environment = Object.fromEntries([...env.children].map((r) => r.read()));
        if (f.gpu.checked) out.gpu = true;
        resolve({ id: f.id.value.trim(), spec: out, location });
      } catch (err) {
        reject(err);
      }
    }, { once: true });
  });
}

async function customInstall(btn) {
  await guarded(btn, async () => {
    const choice = await customDialog(null);
    if (!choice) return;
    await appJob(choice.id, "install", choice.spec.name,
                 { spec: choice.spec, location: choice.location });
  });
}

async function customEdit(app) {
  const choice = await customDialog(app);
  if (!choice) return;
  await appJob(app.id, "update", choice.spec.name, { spec: choice.spec });
}

function renderCatalog() {
  const query = document.getElementById("apps-search").value.trim().toLowerCase();
  const category = document.getElementById("apps-category").value;
  const box = document.getElementById("apps-catalog");
  box.replaceChildren();
  const shown = catalogApps.filter((app) => {
    if (category && app.category !== category) return false;
    if (!query) return true;
    const desc = app.description[lang] || app.description.en || "";
    return `${app.id} ${app.name} ${desc}`.toLowerCase().includes(query);
  });
  for (const app of shown) {
    const card = el("article", "app-card");
    card.append(cardHead(app, `${tv("category", app.category)} · ${app.version}`));
    card.append(el("div", "desc", app.description[lang] || app.description.en || ""));
    const extra = helpers(app);
    if (extra) card.append(el("div", "hint", t("apps.with_services", { services: extra })));
    if (app.gpu) card.append(el("div", "hint", t(gpuAvailable ? "apps.gpu_used" : "apps.gpu_none")));
    const ops = el("div", "ops");
    if (app.website) ops.append(link(app.website, t("apps.website")));
    if (app.installed) {
      ops.append(badge(t("apps.is_installed"), "ok"));
    } else {
      ops.append(iconButton("download", "apps.install", async () => {
        const choice = await installDialog(app);
        if (!choice) return;
        await appJob(app.id, "install", app.name, choice);
      }, "primary"));
    }
    ops.firstChild.style.marginRight = "auto";
    card.append(ops);
    box.append(card);
  }
  if (!shown.length) {
    box.append(el("p", "empty", t(catalogApps.length ? "apps.no_match" : "apps.catalog_empty")));
  }
}

function fillCategories() {
  const select = document.getElementById("apps-category");
  const chosen = select.value;
  const used = [...new Set(catalogApps.map((a) => a.category))];
  used.sort((x, y) => tv("category", x).localeCompare(tv("category", y)));
  const all = el("option", "", t("apps.all_categories"));
  all.value = "";
  select.replaceChildren(all);
  for (const c of used) {
    const n = catalogApps.filter((a) => a.category === c).length;
    const o = el("option", "", `${tv("category", c)} (${n})`);
    o.value = c;
    select.append(o);
  }
  select.value = used.includes(chosen) ? chosen : "";
}

async function loadApps() {
  const data = await api("GET", "apps");
  gpuAvailable = data.gpu_available;
  renderInstalled(data.installed);
  catalogApps = data.catalog;
  fillCategories();
  const when = data.catalog_refreshed ? new Date(data.catalog_refreshed).toLocaleString(lang) : "-";
  document.getElementById("apps-refreshed").textContent = t("apps.refreshed", { when });
  renderCatalog();
}

// --- docker ---------------------------------------------------------------------

let dockerTimer = null;
let dockerContainers = [];
let logsFor = null;

const CONTAINER_KIND = { running: "ok", restarting: "warn", paused: "warn", exited: "",
                         created: "", dead: "crit", removing: "warn" };

function stopDockerStats() {
  if (dockerTimer) clearInterval(dockerTimer);
  dockerTimer = null;
}

async function loadDockerStats() {
  const { stats } = await api("GET", "docker/stats");
  for (const tr of document.querySelectorAll("#containers tr[data-name]")) {
    const s = stats[tr.dataset.name];
    tr.querySelector(".cpu").textContent = s ? `${s.cpu_percent.toFixed(1)} %` : "–";
    tr.querySelector(".mem").textContent = s ? formatBytes(s.memory) : "–";
  }
}

function renderDockerDisk(usage) {
  const box = document.getElementById("docker-disk");
  box.replaceChildren();
  for (const u of usage) {
    const card = el("article", "card stat");
    card.append(el("small", "", tv("docker_type", u.type)), el("b", "", formatBytes(u.size)),
                el("span", "hint", t("docker.usage_detail", {
                  active: u.active, total: u.total, free: formatBytes(u.reclaimable || 0),
                })));
    box.append(card);
  }
}

function renderContainers() {
  const tbody = document.getElementById("containers");
  tbody.replaceChildren();
  for (const c of dockerContainers) {
    const tr = el("tr");
    tr.dataset.name = c.name;
    const name = el("div", "cname");
    name.append(el("span", `dot ${DOT_KIND[CONTAINER_KIND[c.state] ?? ""]}`), el("b", "", c.name));
    if (c.app) name.append(badge(t("docker.app", { app: c.app }), "accent"));
    cell(tr, name);
    cell(tr, el("code", "", c.image));
    const st = cell(tr, badge(tv("container_state", c.state), CONTAINER_KIND[c.state] ?? ""));
    st.title = c.status;
    cell(tr, el("small", "ports", c.ports.replaceAll(", ", "\n") || "–")).dataset.label = t("docker.ports");
    const cpu = cell(tr, "–");
    cpu.className = "cpu";
    cpu.dataset.label = t("docker.cpu");
    const mem = cell(tr, "–");
    mem.className = "mem";
    mem.dataset.label = t("docker.memory");
    const ops = el("div", "row-ops");
    const act = (op, iconName, key, danger) => iconButton(iconName, key, async () => {
      if (op !== "start" && !await ask(t(`docker.confirm_${op}`, { name: c.name }), key, danger)) return;
      await api("POST", `docker/${encodeURIComponent(c.name)}/${op}`);
      say(t(`docker.${op}_done`, { name: c.name }), "ok");
      await loadDocker();
    }, danger ? "danger" : "");
    if (c.state === "running") {
      ops.append(act("restart", "reboot", "docker.restart"), act("stop", "stop", "docker.stop", true));
    } else {
      ops.append(act("start", "play", "docker.start"));
    }
    ops.append(iconButton("log", "docker.logs", () => openLogs(c.name)));
    // Icons only in the table, the text goes into the tooltip.
    for (const b of ops.children) {
      b.title = b.textContent;
      b.setAttribute("aria-label", b.title);
      b.lastChild.remove();
      b.classList.add("icon-btn");
    }
    cell(tr, ops);
    tbody.append(tr);
  }
  if (!dockerContainers.length) {
    const tr = el("tr");
    cell(tr, t("docker.none")).colSpan = 7;
    tbody.append(tr);
  }
}

async function loadDocker() {
  const [list, disk] = await Promise.all([api("GET", "docker"), api("GET", "docker/disk")]);
  dockerContainers = list.containers;
  renderDockerDisk(disk.usage);
  renderContainers();
  stopDockerStats();
  await loadDockerStats().catch(() => {});
  dockerTimer = setInterval(() => loadDockerStats().catch(stopDockerStats), 5000);
}

async function refreshLogs() {
  const out = document.getElementById("logs-output");
  const data = await api("GET", `docker/${encodeURIComponent(logsFor)}/logs?lines=500`);
  out.textContent = data.lines.join("\n") || t("log.empty");
  out.scrollTop = out.scrollHeight;
}

async function openLogs(name) {
  logsFor = name;
  document.getElementById("logs-title").textContent = t("docker.logs_of", { name });
  document.getElementById("logs-output").textContent = t("common.loading");
  document.getElementById("logs-dialog").showModal();
  await refreshLogs();
}

// --- system log -------------------------------------------------------------------

async function loadLog() {
  const lines = document.getElementById("log-lines").value;
  const priority = document.getElementById("log-priority").value;
  const unit = document.getElementById("log-unit").value.trim();
  const q = new URLSearchParams({ lines, priority, unit });
  const out = document.getElementById("log-output");
  out.textContent = t("common.loading");
  const data = await api("GET", `log?${q}`);
  out.replaceChildren();
  for (const line of data.lines) {
    const row = el("span", /\b(error|failed|fail|critical|panic)\b/i.test(line) ? "err"
      : /\bwarn(ing)?\b/i.test(line) ? "warn" : "", `${line}\n`);
    out.append(row);
  }
  if (!data.lines.length) out.textContent = t("log.empty");
  out.scrollTop = out.scrollHeight;
}

// --- domains --------------------------------------------------------------------

async function loadDomains() {
  const d = await api("GET", "domains");
  const form = document.getElementById("domains-form");
  form.elements.mdns_aliases.checked = d.mdns_aliases;

  const dl = document.getElementById("tls-info");
  dl.replaceChildren();
  fact(dl, "domains.ca_fingerprint", el("code", "", d.ca ? d.ca.sha256 : "-"));
  fact(dl, "domains.ca_valid", d.ca && d.ca.not_after);
  fact(dl, "domains.cert_valid", d.cert && d.cert.not_after);
  fact(dl, "domains.cert_names", d.names.join(", "));
}

// --- parity ---------------------------------------------------------------------

let parityTimer = null;

// A oneshot unit runs while "activating"; the repair unit stays "active (exited)".
function unitRunning(s) {
  return !!s && (["activating", "deactivating"].includes(s.active)
    || (s.active === "active" && s.sub !== "exited"));
}

function parityTime(sec) {
  return sec ? new Date(sec * 1000).toLocaleString(lang) : "-";
}

function parityRun(run) {
  if (!run) return t("parity.never");
  let text = `${parityTime(run.end)}: ${tv("parity_result", run.result)}`;
  if (run.result === "ok" && run.counts) {
    const c = run.counts;
    text += ` (${t("parity.counts", { added: c.added || 0, removed: c.removed || 0, updated: c.updated || 0 })})`;
  }
  if (run.result === "failed" && run.message) text += ` (${run.message})`;
  return text;
}

function parityNotice(d, running) {
  const box = document.getElementById("parity-notice");
  box.replaceChildren();
  const add = (text, ...buttons) => {
    box.append(el("p", "", text));
    if (buttons.length) {
      const row = el("div", "actions");
      row.append(...buttons);
      box.append(row);
    }
  };
  const s = d.status;
  const missing = [...d.parity, ...d.data].filter((x) => !x.mounted).map((x) => x.mountpoint);
  if (running) add(t(`parity.running_${running}`));
  if (!d.parity.length) add(t("parity.no_parity"), button("parity.to_disks", () => open("storage")));
  else if (!d.data.length) add(t("parity.no_data"), button("parity.to_disks", () => open("storage")));
  else if (d.problems.length) add(t("parity.problems", { list: d.problems.join("; ") }));
  if (missing.length) add(t("parity.unmounted", { list: missing.join(", ") }));
  if (d.error) add(t("parity.error", { text: d.error }));
  const ready = !running && !missing.length;
  if (s && s.bad > 0 && !ready) add(t("parity.bad", { n: s.bad }));
  if (s && s.bad > 0 && ready) {
    add(t("parity.bad", { n: s.bad }), button("parity.repair", async () => {
      if (!await ask(t("parity.repair_confirm", { n: s.bad }), "parity.repair")) return;
      await api("POST", "parity/fix", { what: "bad" });
      say(t("parity.repair_started"), "ok");
      await loadParity();
    }, "primary"));
  }
  const sync = d.runs.sync;
  if (sync && sync.result === "blocked" && ready) {
    add(t("parity.blocked", { n: sync.counts.removed, threshold: sync.threshold }),
      button("parity.force", async () => {
        if (!await ask(t("parity.force_confirm", { n: sync.counts.removed }), "parity.force", true)) return;
        await api("POST", "parity/sync", { force: true });
        say(t("parity.sync_started"), "ok");
        await loadParity();
      }, "danger"));
  }
  if (s && s.unsynced > 0 && !running) add(t("parity.interrupted"));
  const fix = d.runs.fix;
  if (fix && fix.result === "failed" && !running) add(t("parity.fix_failed", { text: fix.message || "" }));
  box.hidden = !box.childElementCount;
}

function parityState(d) {
  const s = d.status;
  if (!d.configured) return badge(t("parity.state_none"), "");
  if (s && s.bad > 0) return badge(t("parity.state_bad"), "crit");
  if (!s) return badge(t("parity.state_unsynced"), "warn");
  if (d.runs.sync && ["blocked", "failed"].includes(d.runs.sync.result)) {
    return badge(t("parity.state_stale"), "warn");
  }
  return badge(t("parity.state_ok"), "ok");
}

async function loadParity() {
  clearTimeout(parityTimer);
  const d = await api("GET", "parity");
  const jobs = d.jobs || {};
  const running = ["sync", "scrub", "repair", "mover"].find((k) => unitRunning(jobs[k])) || "";
  parityNotice(d, running);

  const dl = document.getElementById("parity-state");
  dl.replaceChildren();
  fact(dl, "parity.state", parityState(d));
  const s = d.status;
  if (s) {
    fact(dl, "parity.protected", t("parity.protected_value", { n: s.files, size: formatBytes(s.size) }));
  }
  fact(dl, "parity.last_sync", parityRun(d.runs.sync));
  fact(dl, "parity.last_scrub", parityRun(d.runs.scrub));
  if (s && s.blocks) {
    const pct = Math.floor(100 * (s.blocks - s.unscrubbed) / s.blocks);
    fact(dl, "parity.checked", t("parity.checked_value", { pct }));
    fact(dl, "parity.oldest", parityTime(s.scrub_oldest));
  }
  if (s) fact(dl, "parity.bad_blocks", String(s.bad));
  if (d.runs.fix) {
    const what = d.runs.fix.what === "bad" ? t("parity.what_bad")
      : `/mnt/disk${String(d.runs.fix.what || "").slice(1)}`;
    fact(dl, "parity.last_fix", `${what}, ${parityRun(d.runs.fix)}`);
  }

  const tbody = document.getElementById("parity-disks");
  tbody.replaceChildren();
  const rows = [
    ...d.parity.map((x) => [t("parity.parity_n", { n: x.index }), x, "-"]),
    ...d.data.map((x) => {
      const files = s && s.disks[`d${x.index}`] ? String(s.disks[`d${x.index}`].files) : "-";
      return [t("parity.data_n", { n: x.index }), x, files];
    }),
  ];
  for (const [name, x, files] of rows) {
    const tr = el("tr");
    const td = cell(tr, el("b", "", name));
    td.append(el("small", "muted", ` ${x.mountpoint}`));
    cell(tr, formatBytes(x.size));
    cell(tr, files);
    cell(tr, x.mounted ? badge(t("parity.mounted"), "ok") : badge(t("parity.not_mounted"), "crit"));
    tbody.append(tr);
  }
  if (!rows.length) {
    const tr = el("tr");
    const td = cell(tr, t("parity.no_disks"));
    td.colSpan = 4;
    tbody.append(tr);
  }

  const allMounted = [...d.parity, ...d.data].every((x) => x.mounted);
  const idle = d.configured && !running && allMounted;
  document.getElementById("parity-sync").disabled = !idle;
  document.getElementById("parity-scrub").disabled = !idle || !s;

  const form = document.getElementById("parity-restore");
  fillOptions(form.elements.disk, d.data.map((x) => `disk${x.index}`), (v) => `/mnt/${v}`);
  form.querySelector("button").disabled = !idle || !s || !d.data.length;

  const set = document.getElementById("parity-settings");
  if (document.activeElement?.form !== set) {
    for (const [k, v] of Object.entries(d.settings)) set.elements[k].value = String(v);
  }

  if (running) {
    parityTimer = setTimeout(() => { if (current === "parity") guarded(null, loadParity); }, 5000);
  }
}

function bindParity() {
  const refresh = document.getElementById("parity-refresh");
  refresh.addEventListener("click", () => guarded(refresh, loadParity));
  const sync = document.getElementById("parity-sync");
  sync.addEventListener("click", () => guarded(sync, async () => {
    await api("POST", "parity/sync", {});
    say(t("parity.sync_started"), "ok");
    await loadParity();
  }));
  const scrub = document.getElementById("parity-scrub");
  scrub.addEventListener("click", () => guarded(scrub, async () => {
    await api("POST", "jobs/snapraid-scrub/start");
    say(t("parity.scrub_started"), "ok");
    await loadParity();
  }));
  const restore = document.getElementById("parity-restore");
  restore.addEventListener("submit", (e) => {
    e.preventDefault();
    const btn = restore.querySelector("button");
    guarded(btn, async () => {
      const what = restore.elements.disk.value;
      const disk = `/mnt/${what}`;
      if (!await ask(t("parity.restore_confirm", { disk }), "parity.restore", true)) return;
      await api("POST", "parity/fix", { what });
      say(t("parity.restore_started", { disk }), "ok");
      await loadParity();
    });
  });
  const settings = document.getElementById("parity-settings");
  settings.addEventListener("submit", (e) => {
    e.preventDefault();
    const btn = settings.querySelector("button");
    guarded(btn, async () => {
      const body = {};
      for (const k of ["snapraid_delete_threshold", "scrub_percent", "scrub_older_than_days"]) {
        body[k] = Number(settings.elements[k].value);
      }
      await api("POST", "parity/settings", body);
      say(t("parity.saved"), "ok");
      settings.querySelector("button").blur();
      await loadParity();
    });
  });
}

// --- jobs -----------------------------------------------------------------------

const STARTABLE = ["mover", "snapraid-sync", "snapraid-scrub", "backup", "smart-short", "smart-long"];

async function loadJobs() {
  const jobs = await api("GET", "jobs");
  const tbody = document.getElementById("jobs");
  tbody.replaceChildren();
  for (const [name, s] of Object.entries(jobs)) {
    const tr = el("tr");
    cell(tr, el("b", "", t(`job.${name}`)));
    cell(tr, badge(tv("job_state", s.active), s.active === "active" ? "info" : ""));
    cell(tr, s.result ? badge(tv("job_result", s.result), RESULT_KIND[s.result] || "")
      : badge(t("jobs.never"), ""));
    cell(tr, s.started || "-");
    const td = cell(tr, "");
    if (STARTABLE.includes(name)) {
      const b = iconButton("play", "jobs.start", async () => {
        await api("POST", `jobs/${name}/start`);
        say(t("jobs.started", { job: t(`job.${name}`) }), "ok");
        await loadJobs();
      });
      b.disabled = s.active === "active" || s.active === "activating";
      td.append(b);
    }
    tbody.append(tr);
  }
}

// --- automatic security updates -------------------------------------------------

async function loadAutoUpdates() {
  const d = await api("GET", "updates/auto");
  const f = document.getElementById("auto-form");
  if (!f.elements.reboot_time.options.length) {
    const times = [];
    for (let h = 0; h < 24; h++) for (const m of ["00", "30"]) times.push(`${String(h).padStart(2, "0")}:${m}`);
    fillOptions(f.elements.reboot_time, times, (v) => v);
  }
  f.elements.enabled.checked = d.enabled;
  f.elements.reboot.checked = d.reboot;
  if (![...f.elements.reboot_time.options].some((o) => o.value === d.reboot_time)) {
    const o = el("option", "", d.reboot_time);
    o.value = d.reboot_time;
    f.elements.reboot_time.append(o);
  }
  f.elements.reboot_time.value = d.reboot_time;
  f.elements.reboot.disabled = !d.enabled;
  f.elements.reboot_time.disabled = !d.enabled || !d.reboot;

  const notice = document.getElementById("auto-notice");
  notice.replaceChildren();
  if (d.reboot_required) {
    const text = d.reboot_required.length
      ? t("auto.reboot_needed_pkgs", { list: d.reboot_required.join(", ") })
      : t("auto.reboot_needed");
    const row = el("div", "actions");
    row.append(button("overview.reboot", async () => {
      if (!await ask(t("overview.confirm_reboot"), "overview.reboot", true)) return;
      await api("POST", "system/reboot");
      say(t("overview.reboot_started"), "ok");
    }));
    notice.append(el("p", "", text), row);
  }
  notice.hidden = !notice.childElementCount;

  const dl = document.getElementById("auto-last");
  dl.replaceChildren();
  const last = d.last;
  if (!last) {
    fact(dl, "auto.last", t("auto.never"));
    return;
  }
  const when = new Date(last.time.replace(" ", "T")).toLocaleString(lang);
  const kind = { installed: "ok", failed: "crit", running: "info" }[last.result] || "";
  const res = el("span");
  res.append(badge(tv("auto.result", last.result), kind), document.createTextNode(` ${when}`));
  fact(dl, "auto.last", res);
  if (last.packages.length) fact(dl, "auto.packages", last.packages.join(", "));
  if (last.errors.length) fact(dl, "auto.errors", last.errors.join(" "));
}

// --- snapshots of the system (snapper) ------------------------------------------

function snapDate(s) {
  return s.date ? new Date(s.date.replace(" ", "T")).toLocaleString(lang) : "";
}

async function loadRootSnapshots() {
  const d = await api("GET", "snapshots");
  const card = document.getElementById("snap-card");
  card.hidden = !d.available;
  if (!d.available) return;
  const notice = document.getElementById("snap-notice");
  notice.replaceChildren();
  if (d.job && ["active", "activating"].includes(d.job.active)) {
    notice.append(el("p", "", t("snap.running")));
  } else if (d.rollback) {
    notice.append(el("p", "", t("snap.rolled_back", {
      n: d.rollback.snapshot, time: new Date(d.rollback.time).toLocaleString(lang) })));
  }
  notice.hidden = !notice.childElementCount;

  const byNumber = new Map(d.snapshots.map((s) => [s.number, s]));
  const tbody = document.getElementById("snap-list");
  tbody.replaceChildren();
  for (const s of [...d.snapshots].reverse()) {
    const tr = el("tr");
    cell(tr, String(s.number));
    cell(tr, snapDate(s));
    const desc = el("span");
    desc.append(document.createTextNode(s.description || ""));
    if (s.type === "pre") desc.append(" ", badge(t("snap.type_pre"), "info"));
    if (s.type === "post") desc.append(" ", badge(t("snap.type_post", { n: s.pre_number }), ""));
    if (s.userdata && s.userdata.important === "yes") desc.append(" ", badge(t("snap.important"), "accent"));
    cell(tr, desc);
    const ops = el("div", "ops");
    ops.append(
      iconButton("reboot", "snap.rollback", async () => {
        const pre = s.type === "post" ? byNumber.get(s.pre_number) : null;
        const text = t("snap.rollback_confirm", { n: s.number, date: snapDate(s) })
          + (pre ? `\n\n${t("snap.rollback_post_hint", { pre: pre.number })}` : "");
        if (!await ask(text, "snap.rollback_now", true)) return;
        await api("POST", `snapshots/${s.number}/rollback`);
        say(t("snap.rollback_started"), "ok");
        await loadRootSnapshots();
      }),
      iconButton("trash", "snap.delete", async () => {
        if (!await ask(t("snap.delete_confirm", { n: s.number }), "snap.delete", true)) return;
        await api("POST", `snapshots/${s.number}/delete`);
        await loadRootSnapshots();
      }),
    );
    cell(tr, ops);
    tbody.append(tr);
  }
  if (!d.snapshots.length) {
    const tr = el("tr");
    cell(tr, t("snap.none")).colSpan = 4;
    tbody.append(tr);
  }

  const old = document.getElementById("snap-old");
  const oldList = document.getElementById("snap-old-list");
  oldList.replaceChildren();
  for (const name of d.old_roots) {
    const tr = el("tr");
    cell(tr, el("code", "", name));
    cell(tr, iconButton("trash", "snap.delete", async () => {
      if (!await ask(t("snap.old_delete_confirm", { name }), "snap.delete", true)) return;
      await api("POST", "snapshots/old-root/delete", { name });
      await loadRootSnapshots();
    }));
    oldList.append(tr);
  }
  old.hidden = !d.old_roots.length;
}

function bindRootSnapshots() {
  const f = document.getElementById("snap-form");
  f.addEventListener("submit", (e) => {
    e.preventDefault();
    guarded(f.querySelector("button"), async () => {
      const { number } = await api("POST", "snapshots", { description: f.elements.description.value.trim() });
      f.reset();
      say(t("snap.created", { n: number }), "ok");
      await loadRootSnapshots();
    });
  });
}

function bindAutoUpdates() {
  const f = document.getElementById("auto-form");
  const sync = () => {
    f.elements.reboot.disabled = !f.elements.enabled.checked;
    f.elements.reboot_time.disabled = !f.elements.enabled.checked || !f.elements.reboot.checked;
  };
  f.elements.enabled.addEventListener("change", sync);
  f.elements.reboot.addEventListener("change", sync);
  f.addEventListener("submit", (e) => {
    e.preventDefault();
    guarded(f.querySelector("button"), async () => {
      await api("POST", "updates/auto", {
        enabled: f.elements.enabled.checked,
        reboot: f.elements.reboot.checked,
        reboot_time: f.elements.reboot_time.value,
      });
      say(t("auto.saved"), "ok");
      await loadAutoUpdates();
    });
  });
}

// --- backup -----------------------------------------------------------------------

let backupApps = [];
let backupTimer = null;

function renderBackupType() {
  const f = document.getElementById("backup-form");
  const sftp = f.elements.type.value === "sftp";
  document.getElementById("backup-sftp").hidden = !sftp;
  document.getElementById("backup-folder").hidden = sftp;
  for (const n of ["host", "user", "spath"]) f.elements[n].required = sftp;
}

async function loadBackup() {
  clearTimeout(backupTimer);
  const [data, list] = await Promise.all([api("GET", "backup"), api("GET", "apps")]);
  backupApps = list.installed.map((a) => ({ id: a.id, name: a.name }));
  const f = document.getElementById("backup-form");
  const target = data.target || { type: "folder" };
  f.elements.type.value = target.type;
  const paths = data.candidates.map((c) => c.path);
  if (target.type === "folder" && target.path && !paths.includes(target.path)) {
    data.candidates.unshift({ path: target.path, mount: target.path, avail: null, missing: true });
  }
  const byPath = new Map(data.candidates.map((c) => [c.path, c]));
  fillOptions(f.elements.path, [...byPath.keys()], (path) => {
    const c = byPath.get(path);
    return c.missing ? `${path} (${t("backup.not_mounted")})`
      : `${path} (${t("backup.free", { size: formatBytes(c.avail || 0) })})`;
  });
  if (target.type === "folder" && target.path) f.elements.path.value = target.path;
  if (target.type === "sftp") {
    f.elements.host.value = target.host;
    f.elements.port.value = target.port;
    f.elements.user.value = target.user;
    f.elements.spath.value = target.path;
  }
  f.elements.stop_apps.checked = data.stop_apps;
  const key = document.getElementById("backup-pubkey");
  key.hidden = !data.public_key;
  key.textContent = data.public_key || "";
  renderBackupType();

  const dl = document.getElementById("backup-state");
  dl.replaceChildren();
  const last = data.last || {};
  fact(dl, "backup.target", data.target ? (data.target.type === "sftp"
    ? `sftp://${data.target.user}@${data.target.host}:${data.target.port}/${data.target.path}`
    : data.target.path) : t("backup.none"));
  fact(dl, "backup.last", last.started ? new Date(last.started).toLocaleString(lang) : t("jobs.never"));
  if (last.result) fact(dl, "jobs.result", tv("job_result", last.result === "success" ? "success" : "exit-code"));
  if (last.snapshot) fact(dl, "backup.id", last.snapshot.slice(0, 8));
  if (last.added !== undefined && last.added !== null) fact(dl, "backup.added", formatBytes(last.added));
  if (last.error) fact(dl, "backup.error", last.error);
  if (last.warning) fact(dl, "backup.error", last.warning);
  const running = ["active", "activating"].includes(data.job.active);
  const restoring = ["active", "activating"].includes(data.restore.active);
  if (running) fact(dl, "jobs.state", t("backup.running"));
  if (restoring) fact(dl, "jobs.state", t("backup.restoring"));
  document.getElementById("backup-run").disabled = !data.target || running;
  document.getElementById("backup-key").disabled = !data.target;
  document.getElementById("backup-list").disabled = !data.target;
  if ((running || restoring) && current === "backup") {
    backupTimer = setTimeout(() => loadBackup().catch(sayError), 4000);
  }
}

async function loadSnapshots() {
  const { snapshots } = await api("GET", "backup/snapshots");
  const card = document.getElementById("backup-snapshots-card");
  const tbody = document.getElementById("backup-snapshots");
  card.hidden = false;
  tbody.replaceChildren();
  for (const s of [...snapshots].reverse()) {
    const tr = el("tr");
    cell(tr, new Date(s.time).toLocaleString(lang));
    cell(tr, el("code", "", s.id));
    const sel = el("select");
    fillOptions(sel, ["config", ...backupApps.map((a) => a.id)],
      (v) => (v === "config" ? t("backup.what_config") : backupApps.find((a) => a.id === v).name));
    const go = iconButton("reboot", "backup.restore", async () => {
      const what = sel.value;
      const name = what === "config" ? t("backup.what_config") : backupApps.find((a) => a.id === what).name;
      if (!await ask(t("backup.restore_confirm", { what: name, time: new Date(s.time).toLocaleString(lang) }),
                     "backup.restore", true)) return;
      await api("POST", "backup/restore", { snapshot: s.id, what });
      say(t("backup.restore_started", { what: name }), "ok");
      await loadBackup();
    });
    const box = el("div", "ops");
    box.append(sel, go);
    cell(tr, box);
    tbody.append(tr);
  }
  if (!snapshots.length) {
    const tr = el("tr");
    const td = cell(tr, t("backup.no_snapshots"));
    td.colSpan = 3;
    tbody.append(tr);
  }
}

function bindBackup() {
  const f = document.getElementById("backup-form");
  f.elements.type.addEventListener("change", renderBackupType);
  f.addEventListener("submit", (e) => {
    e.preventDefault();
    const el_ = f.elements;
    const target = el_.type.value === "sftp"
      ? { type: "sftp", host: el_.host.value.trim(), port: Number(el_.port.value) || 22,
          user: el_.user.value.trim(), path: el_.spath.value.trim() }
      : { type: "folder", path: el_.path.value };
    guarded(e.submitter, async () => {
      if (!target.path) { say(t("backup.choose_target"), "error"); return; }
      await api("POST", "backup", { target, stop_apps: el_.stop_apps.checked });
      say(t("backup.saved"), "ok");
      await loadBackup();
    });
  });
  const run = document.getElementById("backup-run");
  run.addEventListener("click", () => guarded(run, async () => {
    await api("POST", "jobs/backup/start");
    say(t("backup.started"), "ok");
    await loadBackup();
  }));
  const key = document.getElementById("backup-key");
  key.addEventListener("click", () => guarded(key, async () => {
    const { password } = await api("POST", "backup/key");
    await ask(t("backup.key_text", { password }), "common.ok");
  }));
  const list = document.getElementById("backup-list");
  list.addEventListener("click", () => guarded(list, loadSnapshots));
}

// --- updates --------------------------------------------------------------------

async function checkUpdates() {
  const status = document.getElementById("updates-status");
  status.textContent = t("updates.checking");
  document.getElementById("update-progress").hidden = true;
  const data = await api("POST", "updates/check");
  const tbody = document.getElementById("updates");
  tbody.replaceChildren();
  for (const p of data.packages) {
    const tr = el("tr");
    for (const c of [p.name, p.current || "-", p.new, p.origin]) cell(tr, c);
    tbody.append(tr);
  }
  status.textContent = data.packages.length
    ? t("updates.available", { n: data.packages.length })
    : t("updates.none");
  document.getElementById("updates-apply").disabled = !data.packages.length;
}

let updateTimer = null;
let updateWasRunning = false;

function packageList(title, pkgs, label) {
  const box = el("div", "update-list");
  box.append(el("b", "", `${title} (${pkgs.length})`));
  const ul = el("ul");
  for (const p of pkgs) ul.append(el("li", p.step, label(p)));
  box.append(ul);
  return box;
}

function renderUpdateProgress(data) {
  const box = document.getElementById("update-progress");
  box.replaceChildren();
  box.hidden = data.state === "none";
  if (data.state === "none") return;
  const done = data.packages.filter((p) => p.step === "done");
  const open = data.packages.filter((p) => p.step !== "done");
  box.append(el("b", "", t(`updates.state_${data.state}`)));
  const bar = el("div", "bar");
  const fill = el("span", data.state === "failed" ? "crit" : "");
  fill.style.width = `${data.percent}%`;
  bar.append(fill);
  box.append(bar, el("small", "hint", t("updates.progress", {
    pct: data.percent, done: done.length, total: data.packages.length,
  })));
  if (data.state === "running" && data.last) box.append(el("small", "hint mono", data.last));
  const lists = el("div", "update-lists");
  if (open.length) {
    lists.append(packageList(t("updates.open"), open, (p) => `${p.name} · ${t(`updates.step_${p.step}`)}`));
  }
  if (done.length) lists.append(packageList(t("updates.done"), done, (p) => p.name));
  box.append(lists);
  box.classList.toggle("bad", data.state === "failed");
  box.classList.toggle("ok", data.state === "done");
  document.getElementById("updates-apply").disabled = data.state === "running";
  document.getElementById("updates-check").disabled = data.state === "running";
}

async function loadUpdateProgress() {
  clearTimeout(updateTimer);
  let data;
  try {
    data = await api("GET", "updates/progress");
  } catch (e) {
    // An update of Dahlia itself restarts the dashboard and nginx; keep asking.
    if (updateWasRunning && current === "updates") {
      updateTimer = setTimeout(() => loadUpdateProgress().catch(sayError), 3000);
      return;
    }
    throw e;
  }
  renderUpdateProgress(data);
  const running = data.state === "running";
  if (updateWasRunning && !running) {
    say(t(data.state === "done" ? "updates.finished" : "updates.failed"),
        data.state === "done" ? "ok" : "error");
    // The dashboard restarted during the update: load the new UI.
    if (serverRestarted) setTimeout(() => location.reload(), 2000);
  }
  updateWasRunning = running;
  if (running && current === "updates") {
    updateTimer = setTimeout(() => loadUpdateProgress().catch(sayError), 2000);
  }
}

async function applyUpdates() {
  if (!await ask(t("updates.confirm"), "updates.apply")) return;
  await api("POST", "updates/apply");
  document.getElementById("updates-apply").disabled = true;
  document.getElementById("updates").replaceChildren();
  document.getElementById("updates-status").textContent = "";
  updateWasRunning = true;
  await loadUpdateProgress();
}

// --- terminal ---------------------------------------------------------------------
// xterm.js (vendor/xterm, MIT) is loaded on first use. The canvas renderer
// needs no inline <style>, which the CSP forbids.

let term = null;
let termFit = null;
let termSocket = null;

function loadScript(src) {
  return new Promise((resolve, reject) => {
    const s = document.createElement("script");
    s.src = src;
    s.onload = resolve;
    s.onerror = () => reject(new Error(src));
    document.head.append(s);
  });
}

async function loadTerminal() {
  if (term) { termFit.fit(); return; }
  const css = document.createElement("link");
  css.rel = "stylesheet";
  css.href = "/vendor/xterm/xterm.css";
  document.head.append(css);
  await loadScript("/vendor/xterm/xterm.js");
  await Promise.all([loadScript("/vendor/xterm/addon-fit.js"), loadScript("/vendor/xterm/addon-canvas.js")]);
  term = new window.Terminal({
    cursorBlink: true, fontSize: 14, scrollback: 5000,
    fontFamily: "ui-monospace, 'DejaVu Sans Mono', Menlo, Consolas, monospace",
    theme: { background: "#14121a", foreground: "#ebe8f0", cursor: "#d873b8" },
  });
  termFit = new window.FitAddon.FitAddon();
  term.loadAddon(termFit);
  // Before open(), so the DOM renderer never starts.
  term.loadAddon(new window.CanvasAddon.CanvasAddon());
  term.open(document.getElementById("terminal"));
  termFit.fit();
  term.write(t("terminal.ready") + "\r\n");
  const enc = new TextEncoder();
  term.onData((d) => {
    if (termSocket?.readyState === WebSocket.OPEN) termSocket.send(enc.encode(d));
  });
  term.onResize(({ cols, rows }) => {
    if (termSocket?.readyState === WebSocket.OPEN) termSocket.send(JSON.stringify({ cols, rows }));
  });
  window.addEventListener("resize", () => { if (current === "terminal") termFit.fit(); });
}

function terminalConnected(on) {
  document.getElementById("terminal-close").hidden = !on;
  document.querySelector("#terminal-form [type=submit]").hidden = on;
  document.querySelector("#terminal-form [name=user]").disabled = on;
}

function connectTerminal(user) {
  closeTerminal();
  term.reset();
  const q = new URLSearchParams({ user, cols: term.cols, rows: term.rows });
  const ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/api/terminal?${q}`);
  ws.binaryType = "arraybuffer";
  ws.onopen = () => { terminalConnected(true); term.focus(); };
  ws.onmessage = (e) => {
    if (typeof e.data === "string") {
      const msg = JSON.parse(e.data);
      if (msg.error) term.write(`\r\n${t(msg.error)}\r\n`);
      return;
    }
    term.write(new Uint8Array(e.data));
  };
  ws.onclose = () => {
    if (termSocket === ws) termSocket = null;
    terminalConnected(false);
    term.write(`\r\n\x1b[2m${t("terminal.closed")}\x1b[0m\r\n`);
  };
  termSocket = ws;
}

function closeTerminal() {
  if (termSocket) termSocket.close();
  termSocket = null;
}

// --- SMB shares ---------------------------------------------------------------------

const SECURITY_KIND = { public: "warn", secure: "", private: "ok" };
let sharesData = { shares: [], users: [], pool_mounted: false, running: false };
let shareEditing = null;
let hostName = "";

function shareAddress(name) {
  return `\\\\${hostName}.local\\${name}`;
}

function nfsAddress(name) {
  return `${hostName}.local:/mnt/user/${name}`;
}

async function loadShares() {
  const [data, info] = await Promise.all([api("GET", "shares"), api("GET", "system")]);
  sharesData = data;
  hostName = info.hostname;
  document.getElementById("shares-no-pool").hidden = data.pool_mounted || data.shares.length > 0;
  document.getElementById("shares-hint").textContent = t("shares.hint", { host: hostName });
  const tbody = document.getElementById("shares");
  tbody.replaceChildren();
  for (const s of data.shares) {
    const tr = el("tr");
    const name = el("div", "cname");
    name.append(el("b", "", s.name));
    if (s.time_machine) name.append(badge(t("shares.time_machine_badge"), "accent"));
    if (s.nfs) name.append(badge(t("shares.nfs_badge"), "accent"));
    if (!s.exists) name.append(badge(t("shares.missing"), "warn"));
    if (s.comment) name.append(el("small", "muted", s.comment));
    cell(tr, name);
    cell(tr, badge(t(`security.${s.security}`), SECURITY_KIND[s.security]));
    const users = Object.entries(s.users).map(([u, a]) => `${u} (${t(`access.${a}`)})`);
    cell(tr, users.join(", ") || "–").dataset.label = t("shares.users");
    const addr = el("div", "cname");
    addr.append(el("code", "", shareAddress(s.name)));
    if (s.nfs) addr.append(el("code", "", nfsAddress(s.name)));
    cell(tr, addr).dataset.label = t("shares.address");
    const ops = el("div", "row-ops");
    ops.append(button("shares.edit", () => openShareDialog(s)),
               button("shares.remove", async () => {
                 if (!await ask(t("shares.confirm_remove", { name: s.name }), "shares.remove", true)) return;
                 await api("POST", `shares/${encodeURIComponent(s.name)}/remove`);
                 say(t("shares.removed", { name: s.name }), "ok");
                 await loadShares();
               }, "danger"));
    cell(tr, ops);
    tbody.append(tr);
  }
  if (!data.shares.length) {
    const tr = el("tr");
    cell(tr, t("shares.none")).colSpan = 5;
    tbody.append(tr);
  }
  renderSmbUsers();
  await loadRemotes();
}

// --- shares of other devices -------------------------------------------------------

const REMOTE_STATE_KIND = { mounted: "ok", idle: "", error: "crit", off: "warn" };
let remotesData = [];
let remoteEditing = null;

async function loadRemotes() {
  remotesData = (await api("GET", "remotes")).remotes;
  const tbody = document.getElementById("remotes");
  tbody.replaceChildren();
  for (const r of remotesData) {
    const tr = el("tr");
    const name = el("div", "cname");
    name.append(el("b", "", r.name));
    if (r.readonly) name.append(badge(t("remotes.readonly_badge"), ""));
    cell(tr, name);
    const src = r.type === "smb" ? `\\\\${r.host}\\${r.share}` : `${r.host}:${r.share}`;
    const srcBox = el("div", "cname");
    srcBox.append(el("code", "", src), el("small", "muted",
      r.type === "smb" ? (r.username || t("remotes.guest")) : "NFS"));
    cell(tr, srcBox).dataset.label = t("remotes.source");
    cell(tr, el("code", "", r.path)).dataset.label = t("remotes.path");
    cell(tr, badge(tv("remote_state", r.state), REMOTE_STATE_KIND[r.state] ?? "")).dataset.label =
      t("remotes.state");
    const ops = el("div", "row-ops");
    ops.append(button("shares.edit", () => openRemoteDialog(r)),
               button("shares.remove", async () => {
                 if (!await ask(t("remotes.confirm_remove", { name: r.name }), "shares.remove", true)) return;
                 try {
                   await api("POST", `remotes/${encodeURIComponent(r.name)}/remove`, {});
                 } catch (err) {
                   // Apps still use a folder on it: say which and ask again.
                   if (!(err instanceof ApiError) || !/used by apps/.test(err.detail)) throw err;
                   if (!await ask(t("remotes.confirm_force", { detail: err.detail.split(";")[0] }),
                                  "shares.remove", true)) return;
                   await api("POST", `remotes/${encodeURIComponent(r.name)}/remove`, { force: true });
                 }
                 say(t("remotes.removed", { name: r.name }), "ok");
                 await loadRemotes();
               }, "danger"));
    cell(tr, ops);
    tbody.append(tr);
  }
  if (!remotesData.length) {
    const tr = el("tr");
    cell(tr, t("remotes.none")).colSpan = 5;
    tbody.append(tr);
  }
}

function renderRemoteForm() {
  const f = document.getElementById("remote-form").elements;
  const smb = f.type.value === "smb";
  document.getElementById("remote-share-label").textContent = t(smb ? "remotes.share_smb" : "remotes.share_nfs");
  f.share.placeholder = t(smb ? "remotes.share_smb_placeholder" : "remotes.share_nfs_placeholder");
  document.getElementById("remote-login").hidden = !smb;
  document.getElementById("remote-login-hint").textContent =
    remoteEditing?.has_password ? t("remotes.password_keep") : t("remotes.guest_hint");
}

function openRemoteDialog(r) {
  remoteEditing = r;
  const form = document.getElementById("remote-form");
  form.reset();
  const f = form.elements;
  document.getElementById("remote-title").textContent =
    r ? t("remotes.edit_title", { name: r.name }) : t("remotes.new");
  f.name.value = r?.name ?? "";
  f.name.readOnly = Boolean(r);
  f.type.value = r?.type ?? "smb";
  f.host.value = r?.host ?? "";
  f.share.value = r?.share ?? "";
  f.username.value = r?.username ?? "";
  f.readonly.checked = r?.readonly ?? false;
  renderRemoteForm();
  document.getElementById("remote-dialog").showModal();
}

function bindRemotes() {
  document.getElementById("remote-new").addEventListener("click", () => openRemoteDialog(null));
  const form = document.getElementById("remote-form");
  form.elements.type.addEventListener("change", renderRemoteForm);
  form.addEventListener("submit", (e) => {
    if (e.submitter?.value === "cancel") return;
    e.preventDefault();
    const f = form.elements;
    const name = f.name.value.trim();
    const body = { type: f.type.value, host: f.host.value.trim(), share: f.share.value.trim(),
                   username: f.username.value.trim(), readonly: f.readonly.checked };
    // Empty password field when editing: keep the saved one.
    if (f.password.value || !remoteEditing?.has_password) body.password = f.password.value;
    if (body.type === "nfs") { body.username = ""; delete body.password; }
    guarded(e.submitter, async () => {
      await api("POST", `remotes/${encodeURIComponent(name)}`, body);
      document.getElementById("remote-dialog").close();
      say(t("remotes.saved", { name, path: `/mnt/remotes/${name}` }), "ok");
      await loadRemotes();
    });
  });
}

function renderSmbUsers() {
  const box = document.getElementById("smb-users");
  box.replaceChildren();
  if (!sharesData.users.length) box.append(el("p", "hint", t("shares.no_users")));
  for (const u of sharesData.users) {
    const row = el("div", "smb-user");
    row.append(avatar(u, u), el("b", "", u));
    const ops = el("div", "row-ops");
    ops.append(button("shares.change_password", () => openPasswordDialog(u)),
               button("shares.user_remove", async () => {
                 if (!await ask(t("shares.confirm_user_remove", { name: u }), "shares.user_remove", true)) return;
                 await api("POST", `smb-users/${encodeURIComponent(u)}/remove`);
                 say(t("shares.user_removed", { name: u }), "ok");
                 await loadShares();
               }, "danger"));
    row.append(ops);
    box.append(row);
  }
}

function renderSecurityHint() {
  const f = document.getElementById("share-form");
  const sec = f.elements.security.value;
  document.getElementById("share-security-hint").textContent = t(`security.${sec}_hint`);
  // Public shares give everyone write access; per-user rights do not apply.
  document.getElementById("share-users").hidden = sec === "public";
  // NFS has no login: only public (read-write) and secure (read-only) shares.
  document.getElementById("share-nfs").hidden = sec === "private";
  const nfsHint = document.getElementById("share-nfs-hint");
  nfsHint.hidden = sec === "private" || !f.elements.nfs.checked;
  nfsHint.textContent = t(`shares.nfs_hint_${sec === "public" ? "rw" : "ro"}`);
  document.getElementById("share-tm-size").hidden = !f.elements.time_machine.checked;
}

function openShareDialog(share) {
  shareEditing = share;
  const f = document.getElementById("share-form");
  f.reset();
  document.getElementById("share-dialog-title").textContent =
    share ? t("shares.edit_title", { name: share.name }) : t("shares.new");
  f.elements.name.value = share?.name ?? "";
  f.elements.name.readOnly = Boolean(share);
  f.elements.comment.value = share?.comment ?? "";
  f.elements.security.value = share?.security ?? "private";
  f.elements.time_machine.checked = share?.time_machine ?? false;
  f.elements.time_machine_size.value = share?.time_machine_size || "";
  f.elements.nfs.checked = share?.nfs ?? false;
  const box = document.getElementById("share-users");
  box.replaceChildren();
  if (!sharesData.users.length) box.append(el("p", "hint", t("shares.no_users")));
  for (const u of sharesData.users) {
    const label = el("label");
    label.append(el("span", "", u));
    const sel = el("select");
    sel.dataset.user = u;
    fillOptions(sel, ["none", "ro", "rw"], (a) => t(`access.${a}`));
    sel.value = share?.users[u] ?? (share ? "none" : "rw");
    label.append(sel);
    box.append(label);
  }
  renderSecurityHint();
  document.getElementById("share-dialog").showModal();
}

function openPasswordDialog(user) {
  const f = document.getElementById("smb-password-form");
  f.reset();
  f.dataset.user = user;
  document.getElementById("smb-password-title").textContent = t("shares.password_title", { name: user });
  document.getElementById("smb-password-dialog").showModal();
}

function bindShares() {
  document.getElementById("share-new").addEventListener("click", () => openShareDialog(null));
  const form = document.getElementById("share-form");
  for (const name of ["security", "nfs", "time_machine"]) {
    form.elements[name].addEventListener("change", renderSecurityHint);
  }
  form.addEventListener("submit", (e) => {
    if (e.submitter?.value === "cancel") return;
    e.preventDefault();
    const f = form.elements;
    const users = {};
    for (const sel of document.querySelectorAll("#share-users select")) {
      if (sel.value !== "none") users[sel.dataset.user] = sel.value;
    }
    const name = f.name.value.trim();
    guarded(e.submitter, async () => {
      await api("POST", `shares/${encodeURIComponent(name)}`, {
        comment: f.comment.value.trim(), security: f.security.value,
        users: f.security.value === "public" ? {} : users, time_machine: f.time_machine.checked,
        time_machine_size: f.time_machine.checked ? Number(f.time_machine_size.value) || 0 : 0,
        nfs: f.security.value !== "private" && f.nfs.checked,
      });
      document.getElementById("share-dialog").close();
      say(t("shares.saved", { name }), "ok");
      await loadShares();
    });
  });

  document.getElementById("smb-user-form").addEventListener("submit", (e) => {
    e.preventDefault();
    const f = formData(e.target);
    if (f.password !== f.password2) { say(t("error.password_mismatch"), "error"); return; }
    guarded(e.submitter, async () => {
      await api("POST", "smb-users", { name: f.name.trim(), password: f.password });
      e.target.reset();
      say(t("shares.user_added", { name: f.name.trim() }), "ok");
      await loadShares();
    });
  });

  const pwForm = document.getElementById("smb-password-form");
  pwForm.addEventListener("submit", (e) => {
    if (e.submitter?.value === "cancel") return;
    e.preventDefault();
    const f = formData(pwForm);
    if (f.password !== f.password2) { say(t("error.password_mismatch"), "error"); return; }
    guarded(e.submitter, async () => {
      await api("POST", `smb-users/${encodeURIComponent(pwForm.dataset.user)}/password`, { password: f.password });
      document.getElementById("smb-password-dialog").close();
      say(t("shares.password_changed", { name: pwForm.dataset.user }), "ok");
    });
  });
}

// --- account and server name ------------------------------------------------------

async function loadAccount() {
  const info = await api("GET", "system");
  setSidebar(info);
  document.querySelector("#hostname-form [name=hostname]").value = info.hostname;
  // Each card loads on its own; one failing part does not hide the others.
  await Promise.all([loadTime(), loadNetwork(), loadSpindown(), loadFirewall(), loadSchedules(),
               loadNotify(), loadUps(), loadInflux(), loadVpn(), loadTotp(), loadTokens()]
    .map((p) => p.catch(sayError)));
}

// --- settings: time, network, spindown, schedules ---------------------------------

const SCHEDULE_JOBS = ["mover", "snapraid-sync", "snapraid-scrub", "backup", "smart-short", "smart-long"];
const FREQUENCIES = ["off", "hourly", "daily", "weekly", "monthly"];
const WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"];
let netTimer = null;

function fillOptions(select, values, label) {
  select.replaceChildren(...values.map((v) => {
    const o = el("option", "", label(v));
    o.value = String(v);
    return o;
  }));
}

function minutesLabel(m) {
  if (m === -1) return t("settings.spindown_inherit");
  if (m === 0) return t("settings.spindown_never");
  return m < 60 ? t("settings.minutes", { n: m }) : t("settings.hours", { n: m / 60 });
}

async function loadTime() {
  const data = await api("GET", "settings/time");
  const f = document.getElementById("time-form");
  fillOptions(f.elements.timezone, data.timezones, (z) => z);
  f.elements.timezone.value = data.timezone;
  f.elements.ntp.checked = data.ntp;
  let key = "settings.ntp_off";
  if (data.ntp) key = data.synchronized ? "settings.ntp_synced" : "settings.ntp_waiting";
  document.getElementById("time-state").textContent = t(key, { time: data.time });
}

function renderNetMode() {
  const f = document.getElementById("network-form");
  const isStatic = f.elements.mode.value === "static";
  document.getElementById("net-static").hidden = !isStatic;
  for (const name of ["address", "gateway", "dns"]) f.elements[name].required = isStatic;
}

function pollNetwork() {
  clearTimeout(netTimer);
  // While the address changes, requests fail; keep trying.
  netTimer = setTimeout(() => loadNetwork().catch(pollNetwork), 3000);
}

async function loadNetwork() {
  const data = await api("GET", "settings/network");
  const f = document.getElementById("network-form");
  f.elements.mode.value = data.mode;
  // Prefill with what DHCP gave, so switching to static starts from the current address.
  f.elements.address.value = data.address || data.current[0] || "";
  f.elements.gateway.value = data.gateway || data.current_gateway || "";
  f.elements.dns.value = data.dns.join(", ");
  document.getElementById("net-current").textContent = t("settings.net_current", {
    iface: data.iface, address: data.current.join(", ") || "–", gateway: data.current_gateway || "–",
  });
  renderNetMode();
  const box = document.getElementById("net-pending");
  const confirm = document.getElementById("net-confirm");
  clearTimeout(netTimer);
  box.hidden = data.pending === null && !data.reverted;
  confirm.hidden = data.pending === null;
  if (data.pending !== null) {
    document.getElementById("net-pending-text").textContent = t("settings.net_pending", { s: data.pending });
    pollNetwork();
  } else if (data.reverted) {
    document.getElementById("net-pending-text").textContent = t("settings.net_reverted");
  }
}

async function loadSpindown() {
  const data = await api("GET", "settings/spindown");
  const f = document.getElementById("spindown-form");
  fillOptions(f.elements.default, data.choices, minutesLabel);
  f.elements.default.value = String(data.default ?? 0);
  const box = document.getElementById("spindown-disks");
  box.replaceChildren();
  if (!data.disks.length) box.append(el("p", "hint", t("settings.spindown_none")));
  for (const d of data.disks) {
    const label = el("label");
    label.append(el("span", "", [d.path, d.model, formatBytes(d.size)].filter(Boolean).join(" · ")));
    const sel = el("select");
    sel.dataset.serial = d.serial || "";
    sel.disabled = !d.serial;
    fillOptions(sel, [-1, ...data.choices], minutesLabel);
    sel.value = String(d.minutes);
    label.append(sel);
    box.append(label);
  }
}

async function loadFirewall() {
  const data = await api("GET", "settings/firewall");
  const f = document.getElementById("firewall-form");
  f.elements.enabled.checked = data.enabled;
  f.dataset.enabled = String(data.enabled);
  f.elements.networks.value = data.networks.join(", ");
  document.getElementById("firewall-local").textContent = t("settings.firewall_local", {
    networks: [...data.local, ...data.subnets].join(", "),
  });
  const ports = data.ports.map((p) => {
    const name = p.service.startsWith("app:") ? p.service.slice(4) : t(`firewall.${p.service}`);
    return `${p.port}/${p.protocol} ${name}`;
  });
  if (data.wireguard) ports.push(t("settings.firewall_wireguard", { port: data.wireguard.port }));
  document.getElementById("firewall-ports").textContent = data.enabled
    ? t("settings.firewall_ports", { ports: ports.join(", ") })
    : t("settings.firewall_off");
  firewallRules = data.rules;
  renderFirewallRules();
}

let firewallRules = [];

function ruleSource(r) {
  return r.source === "local" || r.source === "any" ? t(`fwrules.${r.source}`) : r.source;
}

function renderFirewallRules() {
  const body = document.getElementById("fw-rules");
  body.replaceChildren();
  if (!firewallRules.length) {
    const tr = el("tr");
    const td = cell(tr, t("fwrules.none"));
    td.colSpan = 5;
    td.className = "hint";
    body.append(tr);
  }
  firewallRules.forEach((r, i) => {
    const tr = el("tr");
    cell(tr, badge(t(`fwrules.${r.action}`), r.action === "accept" ? "ok" : "crit"));
    cell(tr, r.protocol === "all" ? t("fwrules.all") : `${r.ports}/${r.protocol}`).dataset.label = t("fwrules.port");
    cell(tr, ruleSource(r)).dataset.label = t("fwrules.source");
    cell(tr, r.comment || "–").dataset.label = t("fwrules.comment");
    const ops = el("div", "row-ops");
    const move = (to) => saveFirewallRules(firewallRules.map((x, j) => (j === i ? firewallRules[to] : j === to ? r : x)));
    const up = button("fwrules.up", () => move(i - 1));
    up.disabled = i === 0;
    const down = button("fwrules.down", () => move(i + 1));
    down.disabled = i === firewallRules.length - 1;
    ops.append(up, down, button("shares.remove", () => saveFirewallRules(firewallRules.filter((_, j) => j !== i)), "danger"));
    cell(tr, ops);
    body.append(tr);
  });
}

async function saveFirewallRules(rules) {
  const res = await api("POST", "settings/firewall/rules", { rules });
  say(t("fwrules.saved"), "ok");
  firewallRules = res.rules;
  renderFirewallRules();
}

function renderRuleForm() {
  const f = document.getElementById("fw-rule-form").elements;
  f.ports.disabled = f.protocol.value === "all";
  document.getElementById("fw-rule-net").hidden = f.source_kind.value !== "net";
}

function bindFirewallRules() {
  const form = document.getElementById("fw-rule-form");
  form.elements.protocol.addEventListener("change", renderRuleForm);
  form.elements.source_kind.addEventListener("change", renderRuleForm);
  renderRuleForm();
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    const f = form.elements;
    const kind = f.source_kind.value;
    const rule = { action: f.action.value, protocol: f.protocol.value,
                   ports: f.protocol.value === "all" ? "" : f.ports.value.trim(),
                   source: kind === "net" ? f.network.value.trim() : kind, comment: f.comment.value.trim() };
    guarded(e.submitter, async () => {
      if (rule.action === "accept" && rule.source === "any"
          && !await ask(t("fwrules.any_confirm", { ports: rule.ports }), "fwrules.add", true)) return;
      await saveFirewallRules([...firewallRules, rule]);
      form.reset();
      renderRuleForm();
    });
  });
}

// --- settings: remote access (WireGuard, Tailscale) -------------------------------

let vpnTimer = null;

function renderWireguard(wg) {
  const f = document.getElementById("wg-form");
  f.elements.enabled.checked = wg.enabled;
  f.elements.endpoint.value = wg.endpoint;
  f.elements.port.value = wg.port;
  f.elements.route.value = wg.route;
  f.elements.dns.value = wg.dns;
  f.elements.subnet.value = wg.subnet;
  f.elements.subnet.readOnly = wg.peers.length > 0;
  document.getElementById("wg-router").textContent = t("vpn.router", { port: wg.port });
  const body = document.getElementById("wg-peers");
  body.replaceChildren();
  for (const p of wg.peers) {
    const tr = el("tr");
    cell(tr, p.name);
    cell(tr, p.address);
    cell(tr, p.handshake ? new Date(p.handshake * 1000).toLocaleString(lang) : t("vpn.never"));
    cell(tr, p.rx !== undefined ? `↓ ${formatBytes(p.rx)} ↑ ${formatBytes(p.tx)}` : "–");
    const del = iconButton("trash", "vpn.peer_remove", async () => {
      if (!await ask(t("vpn.peer_remove_confirm", { name: p.name }), "vpn.peer_remove", true)) return;
      await api("POST", "vpn/wireguard/peers/delete", { name: p.name });
      await loadVpn();
    }, "danger");
    cell(tr, del);
    body.append(tr);
  }
  if (!wg.peers.length) {
    const tr = el("tr");
    const td = cell(tr, t("vpn.no_peers"));
    td.colSpan = 5;
    td.className = "hint";
    body.append(tr);
  }
  const peerForm = document.getElementById("wg-peer-form");
  for (const e of peerForm.elements) e.disabled = !wg.enabled || !wg.endpoint;
}

function renderTailscale(ts) {
  const list = document.getElementById("ts-state");
  list.replaceChildren();
  const installing = unitRunning(ts.install_job);
  const waiting = unitRunning(ts.login_job);
  const f = document.getElementById("ts-form");
  f.elements.routes.checked = ts.routes;
  f.elements.exit_node.checked = ts.exit_node;
  if (!ts.installed) {
    list.append(checkItem("info", t(installing ? "vpn.ts_installing" : "vpn.ts_not_installed")));
    if (ts.install_job && ts.install_job.result && ts.install_job.result !== "success" && !installing) {
      list.append(checkItem("bad", t("vpn.ts_install_failed")));
    }
  } else if (ts.state === "Running") {
    list.append(checkItem("ok", t("vpn.ts_running", { tailnet: ts.tailnet || "-" })));
    if (ts.dns_name) list.append(checkItem("ok", ts.dns_name));
    if (ts.addresses.length) list.append(checkItem("ok", ts.addresses.join(", ")));
    if (ts.routes) list.append(checkItem("info", t("vpn.ts_routes_hint")));
  } else if (ts.auth_url) {
    const li = checkItem("info", t("vpn.ts_login"));
    li.lastChild.append(" ", link(ts.auth_url, t("vpn.ts_login_link")));
    list.append(li);
  } else {
    list.append(checkItem("info", t(waiting ? "vpn.ts_waiting" : "vpn.ts_logged_out")));
  }
  const installBtn = document.getElementById("ts-install");
  installBtn.hidden = ts.installed;
  installBtn.disabled = installing;
  document.getElementById("ts-logout").hidden = !ts.installed || ts.state !== "Running";
  const up = document.getElementById("ts-up");
  up.hidden = !ts.installed;
  up.textContent = t(ts.state === "Running" ? "common.save" : "vpn.ts_up");
  f.elements.routes.disabled = f.elements.exit_node.disabled = !ts.installed;
  // Poll while something is in progress: install, or a login link to follow.
  const busy = installing || waiting || (ts.installed && ts.state !== "Running" && ts.auth_url);
  if (busy && !vpnTimer) {
    vpnTimer = setInterval(() => {
      if (current !== "account") { clearInterval(vpnTimer); vpnTimer = null; return; }
      loadVpn().catch(() => {});
    }, 3000);
  } else if (!busy && vpnTimer) {
    clearInterval(vpnTimer);
    vpnTimer = null;
  }
}

async function loadVpn() {
  const data = await api("GET", "vpn");
  renderWireguard(data.wireguard);
  renderTailscale(data.tailscale);
}

// --- account: second factor ------------------------------------------------------

async function loadTotp() {
  const data = await api("GET", "account/totp");
  const setupOpen = !document.getElementById("totp-setup").hidden ||
    !document.getElementById("totp-recovery").hidden;
  document.getElementById("totp-state").textContent = data.enabled
    ? t("totp.on", { n: data.recovery_left }) : t("totp.off");
  document.getElementById("totp-state").className = data.enabled ? "ok" : "muted";
  document.getElementById("totp-begin-form").hidden = data.enabled || setupOpen;
  document.getElementById("totp-disable-form").hidden = !data.enabled || setupOpen;
}

function clearTotpSetup() {
  document.getElementById("totp-setup").hidden = true;
  document.getElementById("totp-secret").textContent = "";
  document.getElementById("totp-qr").removeAttribute("src");
}

function bindTotp() {
  document.getElementById("totp-begin-form").addEventListener("submit", (e) => {
    e.preventDefault();
    const f = formData(e.target);
    guarded(e.submitter, async () => {
      const res = await api("POST", "account/totp/begin", { password: f.password });
      e.target.reset();
      e.target.hidden = true;
      const qr = document.getElementById("totp-qr");
      qr.hidden = !res.qr_svg;
      if (res.qr_svg) qr.src = `data:image/svg+xml;base64,${btoa(res.qr_svg)}`;
      document.getElementById("totp-secret").textContent = res.secret.replace(/(.{4})/g, "$1 ").trim();
      document.getElementById("totp-setup").hidden = false;
      document.querySelector("#totp-confirm-form [name=code]").focus();
    });
  });
  document.getElementById("totp-confirm-form").addEventListener("submit", (e) => {
    e.preventDefault();
    const f = formData(e.target);
    guarded(e.submitter, async () => {
      const res = await api("POST", "account/totp/confirm", { code: f.code });
      e.target.reset();
      clearTotpSetup();
      document.getElementById("totp-codes").textContent = res.recovery_codes.join("\n");
      document.getElementById("totp-recovery").hidden = false;
      say(t("totp.enabled"), "ok");
      await loadTotp();
    });
  });
  document.getElementById("totp-recovery-done").addEventListener("click", async () => {
    // Shown once; drop the codes from the page.
    document.getElementById("totp-codes").textContent = "";
    document.getElementById("totp-recovery").hidden = true;
    await loadTotp().catch(sayError);
  });
  document.getElementById("totp-disable-form").addEventListener("submit", (e) => {
    e.preventDefault();
    const f = formData(e.target);
    guarded(e.submitter, async () => {
      if (!await ask(t("totp.disable_confirm"), "totp.disable", true)) return;
      await api("POST", "account/totp/disable", { password: f.password, code: f.code });
      e.target.reset();
      say(t("totp.disabled"), "ok");
      await loadTotp();
    });
  });
}

function showPeer(res) {
  const dlg = document.getElementById("wg-peer-dialog");
  document.getElementById("wg-peer-title").textContent = t("vpn.peer_title", { name: res.name });
  document.getElementById("wg-peer-config").textContent = res.config;
  const qr = document.getElementById("wg-peer-qr");
  qr.hidden = !res.qr_svg;
  if (res.qr_svg) qr.src = `data:image/svg+xml;base64,${btoa(res.qr_svg)}`;
  const dl = document.getElementById("wg-peer-download");
  dl.href = URL.createObjectURL(new Blob([res.config], { type: "text/plain" }));
  dl.download = `${res.name.replace(/[^A-Za-z0-9._-]+/g, "-")}.conf`;
  dlg.addEventListener("close", () => {
    // The private key is shown once; drop it from the page.
    URL.revokeObjectURL(dl.href);
    document.getElementById("wg-peer-config").textContent = "";
    qr.removeAttribute("src");
  }, { once: true });
  dlg.showModal();
}

function bindVpn() {
  document.getElementById("wg-form").addEventListener("submit", (e) => {
    e.preventDefault();
    const f = e.target.elements;
    guarded(e.submitter, async () => {
      await api("POST", "vpn/wireguard", {
        enabled: f.enabled.checked, port: Number(f.port.value), endpoint: f.endpoint.value.trim(),
        route: f.route.value, dns: f.dns.value.trim(), subnet: f.subnet.value.trim(),
      });
      say(t("vpn.saved"), "ok");
      await loadVpn();
    });
  });
  document.getElementById("wg-peer-form").addEventListener("submit", (e) => {
    e.preventDefault();
    const input = e.target.elements.name;
    guarded(e.submitter, async () => {
      const res = await api("POST", "vpn/wireguard/peers", { name: input.value.trim() });
      input.value = "";
      showPeer(res);
      await loadVpn();
    });
  });
  const install = document.getElementById("ts-install");
  install.addEventListener("click", () => guarded(install, async () => {
    await api("POST", "vpn/tailscale/install");
    say(t("vpn.ts_installing"), "");
    await loadVpn();
  }));
  const logout = document.getElementById("ts-logout");
  logout.addEventListener("click", () => guarded(logout, async () => {
    if (!await ask(t("vpn.ts_logout_confirm"), "vpn.ts_logout", true)) return;
    await api("POST", "vpn/tailscale/logout");
    await loadVpn();
  }));
  document.getElementById("ts-form").addEventListener("submit", (e) => {
    e.preventDefault();
    const f = e.target.elements;
    guarded(e.submitter, async () => {
      await api("POST", "vpn/tailscale/up", { routes: f.routes.checked, exit_node: f.exit_node.checked });
      await loadVpn();
    });
  });
}

function scheduleRow(job, s) {
  const row = el("form", "schedule");
  const freq = el("select");
  fillOptions(freq, FREQUENCIES, (v) => t(`schedule.${v}`));
  freq.value = s.frequency;
  const time = el("input");
  time.type = "time";
  time.value = s.time;
  time.required = true;
  const weekday = el("select");
  fillOptions(weekday, WEEKDAYS, (v) => t(`weekday.${v}`));
  weekday.value = s.weekday;
  const monthday = el("select");
  fillOptions(monthday, Array.from({ length: 28 }, (_, i) => i + 1), (n) => t("schedule.monthday", { n }));
  monthday.value = String(s.monthday);
  const save = el("button", "", t("common.save"));
  save.type = "submit";
  const sync = () => {
    time.hidden = freq.value === "off";
    time.title = freq.value === "hourly" ? t("schedule.hourly_hint") : "";
    weekday.hidden = freq.value !== "weekly";
    monthday.hidden = freq.value !== "monthly";
  };
  freq.addEventListener("change", sync);
  sync();
  const fields = el("div", "schedule-fields");
  fields.append(freq, weekday, monthday, time, save);
  const active = s.enabled && s.frequency !== "off" && s.next;
  row.append(el("b", "", tv("job", job)), fields,
             el("small", "hint", active ? t("schedule.next", { time: s.next }) : t("schedule.disabled")));
  row.addEventListener("submit", (e) => {
    e.preventDefault();
    guarded(save, async () => {
      await api("POST", `settings/schedules/${job}`, {
        frequency: freq.value, time: time.value || s.time, weekday: weekday.value,
        monthday: Number(monthday.value),
      });
      say(t("schedule.saved", { job: tv("job", job) }), "ok");
      await loadSchedules();
    });
  });
  return row;
}

async function loadSchedules() {
  const data = await api("GET", "settings/schedules");
  document.getElementById("schedules").replaceChildren(
    ...SCHEDULE_JOBS.map((job) => scheduleRow(job, data.schedules[job])));
}

// --- settings: notifications and UPS ----------------------------------------------

const NOTIFY_CHANNELS = ["email", "ntfy", "gotify"];
const NOTIFY_SECRETS = { email: "password", ntfy: "token", gotify: "token" };
const UPS_SHUTDOWN = [0, 1, 2, 5, 10, 15, 30, 60];

async function loadNotify() {
  const data = await api("GET", "notify");
  for (const ch of NOTIFY_CHANNELS) {
    const c = data[ch];
    const form = document.querySelector(`form[data-channel=${ch}]`);
    for (const input of form.elements) {
      if (!input.name) continue;
      if (input.type === "checkbox") input.checked = Boolean(c[input.name]);
      else if (input.name === NOTIFY_SECRETS[ch]) {
        input.value = "";
        input.placeholder = c[`${input.name}_set`] ? t("notify.secret_kept") : "";
      } else input.value = c[input.name] ?? "";
    }
    const state = document.getElementById(`ch-state-${ch}`);
    state.textContent = c.enabled ? t("notify.on") : t("notify.off");
    state.className = `badge ${c.enabled ? "ok" : ""}`;
  }
  const events = document.getElementById("notify-events");
  events.replaceChildren(...Object.entries(data.events).map(([key, on]) => {
    const label = el("label", "check");
    const box = el("input");
    box.type = "checkbox";
    box.checked = on;
    box.addEventListener("change", async () => {
      try {
        await api("POST", "notify/events", { events: { [key]: box.checked } });
        say(t("notify.events_saved"), "ok");
      } catch (err) {
        box.checked = !box.checked;
        sayError(err);
      }
    });
    label.append(box, el("span", "", tv("notify.event", key)));
    return label;
  }));
  const hist = document.getElementById("notify-history");
  hist.replaceChildren();
  if (!data.history.length) hist.append(el("li", "hint", t("notify.no_history")));
  for (const h of data.history.slice(0, 10)) {
    const li = el("li");
    const when = new Date(h.time * 1000).toLocaleString(lang);
    const failed = Object.entries(h.results).filter(([, r]) => r !== "ok");
    li.append(badge(tv("level", h.level), { info: "info", warning: "warn", critical: "crit" }[h.level]),
              el("b", "", h.title), el("small", "hint", when));
    li.title = h.message;
    if (failed.length) li.append(el("small", "fail", failed.map(([c, r]) => `${tv("notify", c)}: ${r}`).join(" · ")));
    hist.append(li);
  }
}

function channelSettings(form) {
  const out = {};
  for (const input of form.elements) {
    if (!input.name) continue;
    if (input.type === "checkbox") out[input.name] = input.checked;
    else if (input.type === "number") out[input.name] = Number(input.value);
    else out[input.name] = input.value.trim();
  }
  return out;
}

function renderUpsMode() {
  const f = document.getElementById("ups-form").elements;
  const mode = f.mode.value;
  document.getElementById("ups-remote").hidden = mode !== "remote";
  f.shutdown_minutes.disabled = mode === "off";
}

async function loadUps() {
  const data = await api("GET", "ups");
  const f = document.getElementById("ups-form").elements;
  fillOptions(f.shutdown_minutes, UPS_SHUTDOWN,
              (m) => (m === 0 ? t("ups.on_low_battery") : t("ups.after", { n: m })));
  f.mode.value = data.mode;
  f.host.value = data.host;
  f.port.value = data.port;
  f.name.value = data.name;
  f.user.value = data.user;
  f.password.value = "";
  f.password.placeholder = data.password_set ? t("notify.secret_kept") : "";
  f.shutdown_minutes.value = String(UPS_SHUTDOWN.includes(data.shutdown_minutes) ? data.shutdown_minutes : 0);
  renderUpsMode();
  const dl = document.getElementById("ups-status");
  dl.replaceChildren();
  const s = data.status;
  if (!s) {
    fact(dl, "ups.state", t("ups.mode_off"));
    return;
  }
  if (!s.reachable) {
    fact(dl, "ups.state", badge(t("ups.unreachable"), "crit"));
    fact(dl, "ups.error", s.error);
    return;
  }
  const state = s.low_battery ? badge(t("ups.low_battery"), "crit")
    : s.on_battery ? badge(t("ups.on_battery"), "warn") : badge(t("ups.online"), "ok");
  fact(dl, "ups.state", state);
  if (s.model) fact(dl, "ups.model", s.model);
  if (s.charge !== null) fact(dl, "ups.charge", `${s.charge} %`);
  if (s.runtime !== null) fact(dl, "ups.runtime", t("ups.minutes", { n: Math.round(s.runtime / 60) }));
  if (s.load !== null) fact(dl, "ups.load", `${s.load} %`);
}

// --- API tokens -------------------------------------------------------------------

async function loadTokens() {
  const data = await api("GET", "tokens");
  const list = document.getElementById("tokens");
  list.replaceChildren();
  if (!data.tokens.length) list.append(el("li", "hint", t("tokens.none")));
  const when = (sec) => (sec ? new Date(sec * 1000).toLocaleString(lang) : t("tokens.never"));
  for (const tok of data.tokens) {
    const li = el("li", "token");
    const meta = el("div", "cname");
    meta.append(el("b", "", tok.name),
                el("small", "muted", t("tokens.meta", { created: when(tok.created), used: when(tok.last_used) })));
    li.append(meta, button("tokens.remove", async () => {
      if (!await ask(t("tokens.confirm_remove", { name: tok.name }), "tokens.remove", true)) return;
      await api("POST", `tokens/${encodeURIComponent(tok.id)}/remove`);
      document.getElementById("token-new").hidden = true;
      await loadTokens();
    }, "danger"));
    list.append(li);
  }
}

function bindTokens() {
  const form = document.getElementById("token-form");
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    guarded(e.submitter, async () => {
      const res = await api("POST", "tokens", { name: form.elements.name.value.trim() });
      form.reset();
      document.getElementById("token-value").textContent = res.token;
      document.getElementById("token-example").textContent =
        `curl -H "Authorization: Bearer ${res.token}" ${location.origin}/api/stats`;
      document.getElementById("token-new").hidden = false;
      await loadTokens();
    });
  });
  const copy = document.getElementById("token-copy");
  copy.addEventListener("click", () => guarded(copy, async () => {
    await navigator.clipboard.writeText(document.getElementById("token-value").textContent);
    say(t("tokens.copied"), "ok");
  }));
}

// --- history: InfluxDB export -----------------------------------------------------

function renderInfluxForm() {
  const f = document.getElementById("influx-form").elements;
  document.getElementById("influx-fields").hidden = !f.enabled.checked;
  document.getElementById("influx-v2").hidden = f.version.value !== "2";
  document.getElementById("influx-v1").hidden = f.version.value !== "1";
}

async function loadInflux() {
  const data = await api("GET", "history/influx");
  const c = data.settings;
  const f = document.getElementById("influx-form").elements;
  f.enabled.checked = c.enabled;
  f.version.value = String(c.version);
  for (const k of ["url", "org", "bucket", "database", "username"]) f[k].value = c[k];
  f.token.value = "";
  f.token.placeholder = c.has_token ? t("notify.secret_kept") : "";
  f.password.value = "";
  f.password.placeholder = c.has_password ? t("notify.secret_kept") : "";
  renderInfluxForm();
  const dl = document.getElementById("influx-status");
  dl.replaceChildren();
  if (c.enabled && data.last) {
    const when = new Date(data.last.time * 1000).toLocaleString(lang);
    fact(dl, "influx.last", data.last.ok ? badge(t("influx.ok", { time: when }), "ok")
      : badge(t("influx.failed", { time: when }), "crit"));
    if (!data.last.ok) fact(dl, "influx.error", data.last.error);
  }
}

function bindInflux() {
  const form = document.getElementById("influx-form");
  form.elements.enabled.addEventListener("change", renderInfluxForm);
  form.elements.version.addEventListener("change", renderInfluxForm);
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    const f = form.elements;
    const body = { enabled: f.enabled.checked, version: Number(f.version.value), url: f.url.value.trim(),
                   org: f.org.value.trim(), bucket: f.bucket.value.trim(),
                   database: f.database.value.trim(), username: f.username.value.trim() };
    // Empty secret field: keep the saved one.
    if (f.token.value) body.token = f.token.value;
    if (f.password.value) body.password = f.password.value;
    guarded(e.submitter, async () => {
      await api("POST", "history/influx", body);
      say(t(body.enabled ? "influx.saved" : "influx.off"), "ok");
      await loadInflux();
    });
  });
}

function bindNotifyUps() {
  for (const form of document.querySelectorAll("form[data-channel]")) {
    form.addEventListener("submit", (e) => {
      e.preventDefault();
      const ch = form.dataset.channel;
      guarded(e.submitter, async () => {
        await api("POST", `notify/${ch}`, { settings: channelSettings(form) });
        say(t("notify.saved", { channel: tv("notify", ch) }), "ok");
        await loadNotify();
      });
    });
  }
  const test = document.getElementById("notify-test");
  test.addEventListener("click", () => guarded(test, async () => {
    const res = await api("POST", "notify/test");
    const lines = Object.entries(res.results).map(([c, r]) => `${tv("notify", c)}: ${r === "ok" ? t("notify.sent") : r}`);
    const ok = Object.values(res.results).every((r) => r === "ok");
    say(lines.join("\n"), ok ? "ok" : "error");
    await loadNotify();
  }));
  const ups = document.getElementById("ups-form");
  ups.elements.mode.addEventListener("change", renderUpsMode);
  ups.addEventListener("submit", (e) => {
    e.preventDefault();
    const f = ups.elements;
    const body = { mode: f.mode.value, name: f.name.value.trim() || "ups",
                   shutdown_minutes: Number(f.shutdown_minutes.value) };
    if (body.mode === "remote") {
      Object.assign(body, { host: f.host.value.trim(), port: Number(f.port.value) || 3493,
                            user: f.user.value.trim(), password: f.password.value });
    }
    guarded(e.submitter, async () => {
      await api("POST", "ups", body);
      say(t("ups.saved"), "ok");
      await loadUps();
    });
  });
}

// --- forms ----------------------------------------------------------------------

function formData(form) {
  return Object.fromEntries(new FormData(form).entries());
}

function bindForms() {
  document.getElementById("setup-form").addEventListener("submit", (e) => {
    e.preventDefault();
    const f = formData(e.target);
    if (f.password !== f.password2) { say(t("error.password_mismatch"), "error"); return; }
    guarded(e.submitter, async () => {
      const body = { code: f.code, username: f.username, password: f.password };
      if (f.hostname) body.hostname = f.hostname;
      const res = await api("POST", "setup", body);
      setLoggedIn(true);
      await open("overview");
      if (res.hostname_error) say(`${t(res.hostname_error)} (${res.detail})`, "error");
      else say(t("setup.done"), "ok");
    });
  });

  document.getElementById("login-form").addEventListener("submit", (e) => {
    e.preventDefault();
    const f = formData(e.target);
    const codeBox = document.getElementById("login-code");
    guarded(e.submitter, async () => {
      try {
        await api("POST", "login", { username: f.username, password: f.password, code: f.code || "" });
      } catch (err) {
        // Password was right; the account also wants a code from the app.
        if (err.key !== "error.totp_required") throw err;
        codeBox.hidden = false;
        codeBox.querySelector("input").focus();
        say(t("login.code_needed"));
        return;
      }
      e.target.reset();
      codeBox.hidden = true;
      say("");
      setLoggedIn(true);
      await open("overview");
    });
  });

  bindTotp();

  document.getElementById("password-form").addEventListener("submit", (e) => {
    e.preventDefault();
    const f = formData(e.target);
    if (f.new !== f.new2) { say(t("error.password_mismatch"), "error"); return; }
    guarded(e.submitter, async () => {
      await api("POST", "account/password", { old: f.old, new: f.new });
      e.target.reset();
      say(t("account.changed"), "ok");
    });
  });

  document.getElementById("domains-form").addEventListener("submit", (e) => {
    e.preventDefault();
    const mdns = e.target.elements.mdns_aliases.checked;
    guarded(e.submitter, async () => {
      const res = await api("POST", "domains", { mdns_aliases: mdns });
      await loadDomains();
      say(t(res.ca_renewed ? "domains.saved_new_ca" : "domains.saved"), "ok");
    });
  });

  document.getElementById("time-form").addEventListener("submit", (e) => {
    e.preventDefault();
    const f = e.target.elements;
    guarded(e.submitter, async () => {
      await api("POST", "settings/time", { timezone: f.timezone.value, ntp: f.ntp.checked });
      say(t("settings.time_saved"), "ok");
      await loadTime();
    });
  });

  const netForm = document.getElementById("network-form");
  netForm.elements.mode.addEventListener("change", renderNetMode);
  netForm.addEventListener("submit", (e) => {
    e.preventDefault();
    const f = e.target.elements;
    const body = { mode: f.mode.value };
    if (body.mode === "static") {
      Object.assign(body, { address: f.address.value.trim(), gateway: f.gateway.value.trim(),
                            dns: f.dns.value.trim() });
    }
    guarded(e.submitter, async () => {
      if (!await ask(t("settings.net_confirm"), "settings.net_apply", true)) return;
      const res = await api("POST", "settings/network", body);
      say(t("settings.net_started", { s: res.rollback_seconds }), "ok");
      pollNetwork();
    });
  });
  const netConfirm = document.getElementById("net-confirm");
  netConfirm.addEventListener("click", () => guarded(netConfirm, async () => {
    await api("POST", "settings/network/confirm");
    say(t("settings.net_kept"), "ok");
    setTimeout(() => loadNetwork().catch(sayError), 1500);
  }));

  document.getElementById("spindown-form").addEventListener("submit", (e) => {
    e.preventDefault();
    const disks = {};
    for (const sel of document.querySelectorAll("#spindown-disks select")) {
      if (sel.dataset.serial) disks[sel.dataset.serial] = Number(sel.value);
    }
    guarded(e.submitter, async () => {
      const res = await api("POST", "settings/spindown", {
        default: Number(e.target.elements.default.value), disks,
      });
      if (res.failed.length) {
        say(t("settings.spindown_failed", { disks: res.failed.map((f) => `${f.path}: ${f.error}`).join("\n") }), "error");
      } else {
        say(t("settings.spindown_saved"), "ok");
      }
      await loadSpindown();
    });
  });

  bindFirewallRules();
  document.getElementById("firewall-form").addEventListener("submit", (e) => {
    e.preventDefault();
    const f = e.target.elements;
    const networks = f.networks.value.split(/[\s,]+/).filter(Boolean);
    guarded(e.submitter, async () => {
      const turningOff = !f.enabled.checked && e.target.dataset.enabled === "true";
      if (turningOff && !await ask(t("settings.firewall_disable_confirm"), "settings.firewall_disable", true)) return;
      await api("POST", "settings/firewall", { enabled: f.enabled.checked, networks });
      say(t("settings.firewall_saved"), "ok");
      await loadFirewall();
    });
  });

  document.getElementById("hostname-form").addEventListener("submit", (e) => {
    e.preventDefault();
    const f = formData(e.target);
    guarded(e.submitter, async () => {
      await api("POST", "system/hostname", { hostname: f.hostname });
      say(t("overview.hostname_set", { name: f.hostname }), "ok");
      await loadAccount();
    });
  });
}

function bindButtons() {
  for (const b of document.querySelectorAll("#nav button")) {
    b.addEventListener("click", () => open(b.dataset.view));
  }
  for (const b of document.querySelectorAll("[data-goto]")) {
    b.addEventListener("click", () => open(b.dataset.goto));
  }
  document.getElementById("menu").addEventListener("click", () => {
    document.getElementById("sidebar").classList.toggle("open");
  });
  document.getElementById("terminal-form").addEventListener("submit", (e) => {
    e.preventDefault();
    guarded(e.submitter, async () => {
      await loadTerminal();
      connectTerminal(e.target.elements.user.value.trim());
    });
  });
  document.getElementById("terminal-close").addEventListener("click", closeTerminal);

  document.getElementById("logout").addEventListener("click", (e) => guarded(e.currentTarget, async () => {
    closeTerminal();
    await api("POST", "logout");
    say("");
    showLogin();
  }));
  for (const [id, key, path] of [["reboot", "overview.confirm_reboot", "system/reboot"],
                                 ["poweroff", "overview.confirm_poweroff", "system/poweroff"]]) {
    const b = document.getElementById(id);
    b.addEventListener("click", () => guarded(b, async () => {
      if (!await ask(t(key), `overview.${id}`, true)) return;
      await api("POST", path);
      say(t(`overview.${id}_started`), "ok");
    }));
  }
  document.getElementById("storage-refresh").addEventListener("click", (e) => guarded(e.currentTarget, loadStorage));
  bindStorageSettings();
  document.getElementById("apps-search").addEventListener("input", renderCatalog);
  const custom = document.getElementById("apps-custom");
  custom.addEventListener("click", () => customInstall(custom));
  document.getElementById("apps-category").addEventListener("change", renderCatalog);
  const refresh = document.getElementById("apps-refresh");
  refresh.addEventListener("click", () => guarded(refresh, async () => {
    await api("POST", "apps/catalog/refresh");
    await loadApps();
  }));
  document.getElementById("jobs-refresh").addEventListener("click", (e) => guarded(e.currentTarget, loadJobs));
  document.getElementById("docker-refresh").addEventListener("click", (e) => guarded(e.currentTarget, loadDocker));
  const prune = document.getElementById("docker-prune");
  prune.addEventListener("click", () => guarded(prune, async () => {
    if (!await ask(t("docker.confirm_prune"), "docker.prune", true)) return;
    const res = await api("POST", "docker/prune");
    say(t("docker.pruned", { size: formatBytes(res.reclaimed) }), "ok");
    await loadDocker();
  }));
  document.getElementById("logs-close").addEventListener("click", () => {
    document.getElementById("logs-dialog").close();
  });
  const logsRefresh = document.getElementById("logs-refresh");
  logsRefresh.addEventListener("click", () => guarded(logsRefresh, refreshLogs));
  const lines = document.getElementById("log-lines");
  for (const n of [100, 300, 1000, 5000]) {
    const o = el("option", "", String(n));
    o.value = String(n);
    lines.append(o);
  }
  lines.value = "300";
  const diag = document.getElementById("diag-create");
  diag.addEventListener("click", () => guarded(diag, async () => {
    const status = document.getElementById("diag-status");
    status.textContent = t("diag.working");
    try {
      const res = await api("POST", "diagnostics");
      status.textContent = t("diag.ready", { size: formatBytes(res.size) });
    } catch (err) {
      status.textContent = "";
      throw err;
    }
    // A plain link: the browser downloads with the session cookie, no blob URL needed.
    const a = el("a");
    a.href = "/api/diagnostics";
    a.download = "";
    document.body.append(a);
    a.click();
    a.remove();
  }));
  const logRefresh = document.getElementById("log-refresh");
  logRefresh.addEventListener("click", () => guarded(logRefresh, loadLog));
  document.getElementById("log-unit").addEventListener("keydown", (e) => {
    if (e.key === "Enter") guarded(logRefresh, loadLog);
  });
  const check = document.getElementById("updates-check");
  check.addEventListener("click", () => guarded(check, checkUpdates));
  const apply = document.getElementById("updates-apply");
  apply.addEventListener("click", () => guarded(apply, applyUpdates));
  document.getElementById("lang").addEventListener("change", async (e) => {
    try { localStorage.setItem("dahlia-lang", e.target.value); } catch (_) { /* storage blocked */ }
    await loadLang(e.target.value);
    if (current && loaders[current]) await open(current);
    else if (current) show(current);
  });
}

async function start() {
  await loadLang(pickLang());
  bindForms();
  bindButtons();
  bindShares();
  bindFormat();
  bindRemotes();
  bindBurnin();
  bindImport();
  bindFolderPicker();
  bindNotifyUps();
  bindTokens();
  bindInflux();
  bindAutoUpdates();
  bindRootSnapshots();
  bindParity();
  bindBackup();
  bindVpn();
  try {
    const state = await api("GET", "state");
    if (state.setup_required) show("setup");
    else if (state.user) { setLoggedIn(true); await open("overview"); }
    else showLogin();
  } catch (err) {
    sayError(err);
  }
}

start();
