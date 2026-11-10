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

// Letter avatar with a colour derived from the id, so it stays the same.
function avatar(id, name) {
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

function sayError(err) {
  if (err instanceof ApiError) {
    say(err.detail ? `${t(err.key)} (${err.detail})` : t(err.key), "error");
  } else {
    console.error(err);
    say(t("error.network"), "error");
  }
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
  jobs: loadJobs,
  updates: () => {},
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
    item.append(el("span", `dot ${DOT_KIND[STATUS_KIND[app.status] ?? ""]}`), avatar(app.id, app.name), meta);
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
  }).catch(sayError);
  api("GET", "jobs").then(renderOverviewJobs).catch(sayError);
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

async function loadStorage() {
  const [data, stats] = await Promise.all([api("GET", "storage"), api("GET", "stats")]);
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
    card.append(head);
    for (const d of parts) card.append(diskRow(d, stats.filesystems));
    box.append(card);
  }
  if (!groups.size) box.append(el("p", "empty", t("storage.none")));
}

// --- apps -----------------------------------------------------------------------

let catalogApps = [];

// Install and update run in the background on the server; poll until done.
async function waitForAppJob(id) {
  for (let i = 0; i < 360; i++) {
    await new Promise((r) => setTimeout(r, 5000));
    const job = await api("GET", `apps/${id}/job`);
    if (!["active", "activating"].includes(job.active)) return job;
  }
  return null;
}

async function appJob(id, op, name) {
  await api("POST", `apps/${id}/${op}`);
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
  head.append(avatar(app.id, app.name), title);
  return head;
}

function renderInstalled(installed) {
  const box = document.getElementById("apps-installed");
  box.replaceChildren();
  for (const app of installed) {
    const card = el("article", "app-card");
    const head = cardHead(app, `${tv("category", app.category)} · ${app.version}`);
    head.append(badge(tv("app_status", app.status), STATUS_KIND[app.status] ?? ""));
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
      const deleteData = await ask(t("apps.confirm_delete_data", { id: app.id }),
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

function confirmInstall(app) {
  const parts = [t("apps.confirm_install", { name: app.name })];
  const extra = helpers(app);
  if (extra) parts.push(t("apps.confirm_services", { services: extra }));
  const lan = app.lan_ports.map((p) => `${p.host}/${p.protocol}`).join(", ");
  if (lan) parts.push(t("apps.confirm_lan", { ports: lan }));
  return ask(parts.join("\n\n"), "apps.install");
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
    const ops = el("div", "ops");
    if (app.website) ops.append(link(app.website, t("apps.website")));
    if (app.installed) {
      ops.append(badge(t("apps.is_installed"), "ok"));
    } else {
      ops.append(iconButton("download", "apps.install", async () => {
        if (!await confirmInstall(app)) return;
        await appJob(app.id, "install", app.name);
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
  renderInstalled(data.installed);
  catalogApps = data.catalog;
  fillCategories();
  const when = data.catalog_refreshed ? new Date(data.catalog_refreshed).toLocaleString(lang) : "-";
  document.getElementById("apps-refreshed").textContent = t("apps.refreshed", { when });
  renderCatalog();
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

// --- updates --------------------------------------------------------------------

async function checkUpdates() {
  const status = document.getElementById("updates-status");
  status.textContent = t("updates.checking");
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

async function applyUpdates() {
  if (!await ask(t("updates.confirm"), "updates.apply")) return;
  await api("POST", "updates/apply");
  document.getElementById("updates-apply").disabled = true;
  say(t("updates.started"), "ok");
}

// --- account and server name ------------------------------------------------------

async function loadAccount() {
  const info = await api("GET", "system");
  setSidebar(info);
  document.querySelector("#hostname-form [name=hostname]").value = info.hostname;
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
