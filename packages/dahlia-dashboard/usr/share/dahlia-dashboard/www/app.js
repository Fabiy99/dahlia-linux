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

async function api(method, path, body) {
  const opts = { method, headers: { "X-Dahlia-Request": "1" }, credentials: "same-origin" };
  if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  const res = await fetch(`/api/${path}`, opts);
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
  overview: "nav.overview", storage: "storage.title", apps: "apps.title",
  domains: "nav.domains", jobs: "jobs.title", updates: "updates.title", account: "nav.account",
  docker: "nav.docker", log: "nav.log", shares: "nav.shares",
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
  storage: loadStorage,
  apps: loadApps,
  domains: loadDomains,
  docker: loadDocker,
  shares: loadShares,
  log: loadLog,
  jobs: loadJobs,
  updates: () => Promise.all([loadUpdateProgress(), loadAutoUpdates()]),
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
  return urls.domain || urls.mdns || `http://${location.hostname}:${app.public_port}`;
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

async function loadStorage() {
  const [data, stats] = await Promise.all([api("GET", "storage"), api("GET", "stats")]);
  // SMART is slow on many disks; the cards fill in their health when it arrives.
  const hwReady = api("GET", "hardware").catch(() => null);
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
    });
    card.append(head);
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
  for (const href of [urls.domain, urls.mdns, `http://${location.hostname}:${app.public_port}`]) {
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
let folderRoot = ARRAY;
let folderPath = "";

function joinPath(base, name) {
  return base ? `${base}/${name}` : name;
}

function fullPath(root, path) {
  return path ? `${root}/${path}` : root;
}

// Two buttons, one per location; the chosen one is pressed.
function placeButtons(box, current, available, onPick) {
  box.replaceChildren();
  for (const [name, root] of [["system", SYSTEM], ["array", ARRAY]]) {
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
  placeButtons(document.getElementById("folder-roots"), root, available,
               (to) => showFolder(to, "").catch(sayError));
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
  document.getElementById("folder-new").disabled = !data.mounted;
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
  form.elements.domain.value = d.domain;
  form.elements.mdns_aliases.checked = d.mdns_aliases;

  const rows = document.getElementById("dns-records");
  rows.replaceChildren();
  for (const name of d.dns_records) {
    const tr = el("tr");
    cell(tr, el("code", "", name));
    cell(tr, d.address || t("domains.no_address"));
    rows.append(tr);
  }

  const dl = document.getElementById("tls-info");
  dl.replaceChildren();
  fact(dl, "domains.ca_fingerprint", el("code", "", d.ca ? d.ca.sha256 : "-"));
  fact(dl, "domains.ca_valid", d.ca && d.ca.not_after);
  fact(dl, "domains.cert_valid", d.cert && d.cert.not_after);
  fact(dl, "domains.cert_names", d.names.join(", "));
}

// --- jobs -----------------------------------------------------------------------

const STARTABLE = ["mover", "snapraid-sync", "snapraid-scrub"];

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

// --- SMB shares ---------------------------------------------------------------------

const SECURITY_KIND = { public: "warn", secure: "", private: "ok" };
let sharesData = { shares: [], users: [], pool_mounted: false, running: false };
let shareEditing = null;
let hostName = "";

function shareAddress(name) {
  return `\\\\${hostName}.local\\${name}`;
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
    if (!s.exists) name.append(badge(t("shares.missing"), "warn"));
    if (s.comment) name.append(el("small", "muted", s.comment));
    cell(tr, name);
    cell(tr, badge(t(`security.${s.security}`), SECURITY_KIND[s.security]));
    const users = Object.entries(s.users).map(([u, a]) => `${u} (${t(`access.${a}`)})`);
    cell(tr, users.join(", ") || "–").dataset.label = t("shares.users");
    cell(tr, el("code", "", shareAddress(s.name))).dataset.label = t("shares.address");
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
  form.elements.security.addEventListener("change", renderSecurityHint);
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
               loadNotify(), loadUps()]
    .map((p) => p.catch(sayError)));
}

// --- settings: time, network, spindown, schedules ---------------------------------

const SCHEDULE_JOBS = ["mover", "snapraid-sync", "snapraid-scrub"];
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
  document.getElementById("firewall-ports").textContent = data.enabled
    ? t("settings.firewall_ports", { ports: ports.join(", ") })
    : t("settings.firewall_off");
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
    guarded(e.submitter, async () => {
      await api("POST", "login", { username: f.username, password: f.password });
      e.target.reset();
      say("");
      setLoggedIn(true);
      await open("overview");
    });
  });

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
    const domain = e.target.elements.domain.value.trim().toLowerCase();
    const mdns = e.target.elements.mdns_aliases.checked;
    guarded(e.submitter, async () => {
      const res = await api("POST", "domains", { domain, mdns_aliases: mdns });
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
  document.getElementById("logout").addEventListener("click", (e) => guarded(e.currentTarget, async () => {
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
  document.getElementById("apps-search").addEventListener("input", renderCatalog);
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
  bindImport();
  bindFolderPicker();
  bindNotifyUps();
  bindAutoUpdates();
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
