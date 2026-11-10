// Dahlia dashboard frontend. No framework, no build step.
// All visible text comes from locales/<lang>.json via t().
"use strict";

const LANGS = ["de", "en"];
let strings = {};
let lang = "en";
let current = null;

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

// --- messages -------------------------------------------------------------------

function say(text, kind) {
  const el = document.getElementById("message");
  el.textContent = text;
  el.className = kind || "";
  el.hidden = !text;
}

function sayError(err) {
  if (err instanceof ApiError) {
    say(err.detail ? `${t(err.key)} (${err.detail})` : t(err.key), "error");
  } else {
    say(t("error.network"), "error");
  }
}

// Run an action, show errors, keep the button disabled while it runs.
async function guarded(button, fn) {
  if (button) button.disabled = true;
  try {
    await fn();
  } catch (err) {
    sayError(err);
  } finally {
    if (button) button.disabled = false;
  }
}

// --- views ----------------------------------------------------------------------

function show(view) {
  for (const s of document.querySelectorAll("main > section")) s.hidden = s.id !== `view-${view}`;
  for (const b of document.querySelectorAll("#nav button")) {
    if (b.dataset.view === view) b.setAttribute("aria-current", "page");
    else b.removeAttribute("aria-current");
  }
  current = view;
}

function setLoggedIn(on) {
  document.getElementById("nav").hidden = !on;
  document.getElementById("logout").hidden = !on;
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
  account: () => {},
};

async function open(view) {
  say("");
  show(view);
  await guarded(null, loaders[view]);
}

// --- overview -------------------------------------------------------------------

function formatBytes(n) {
  if (n === null || n === undefined) return "-";
  const units = ["B", "KB", "MB", "GB", "TB", "PB"];
  let i = 0;
  while (n >= 1000 && i < units.length - 1) { n /= 1000; i++; }
  return `${n.toFixed(i ? 1 : 0)} ${units[i]}`;
}

function formatUptime(sec) {
  const d = Math.floor(sec / 86400);
  const h = Math.floor((sec % 86400) / 3600);
  const m = Math.floor((sec % 3600) / 60);
  return t("overview.uptime_value", { d, h, m });
}

async function loadOverview() {
  const info = await api("GET", "system");
  const rows = [
    ["overview.hostname", `${info.hostname}.local`],
    ["overview.version", info.version || "-"],
    ["overview.kernel", info.kernel],
    ["overview.uptime", formatUptime(info.uptime_seconds)],
    ["overview.load", info.load.map((x) => x.toFixed(2)).join(" / ")],
    ["overview.memory", t("overview.memory_value", {
      available: formatBytes(info.memory.available), total: formatBytes(info.memory.total),
    })],
  ];
  const dl = document.getElementById("system-info");
  dl.replaceChildren();
  for (const [key, value] of rows) {
    const dt = document.createElement("dt");
    dt.textContent = t(key);
    const dd = document.createElement("dd");
    dd.textContent = value;
    dl.append(dt, dd);
  }
  document.querySelector("#hostname-form [name=hostname]").value = info.hostname;
}

// --- storage --------------------------------------------------------------------

async function loadStorage() {
  const data = await api("GET", "storage");
  const tbody = document.getElementById("disks");
  const tpl = document.getElementById("disk-row");
  tbody.replaceChildren();
  for (const d of data.disks) {
    const row = tpl.content.firstElementChild.cloneNode(true);
    for (const el of row.querySelectorAll("[data-i18n]")) el.textContent = t(el.dataset.i18n);
    row.querySelector(".path").textContent = d.path;
    row.querySelector(".fs").textContent = d.fstype || "-";
    row.querySelector(".size").textContent = formatBytes(d.size);
    const status = row.querySelector(".status");
    status.textContent = tv("disk_status", d.status);
    status.title = d.reason;
    row.querySelector(".model").textContent = [d.model, d.serial].filter(Boolean).join(" ") || "-";

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
      if (!confirm(t("storage.confirm_add", { path: d.path, role: t(`role.${role.value}`) }))) return;
      await api("POST", "storage/add", { uuid: d.uuid, role: role.value });
      say(t("storage.added", { path: d.path }), "ok");
      await loadStorage();
    }));
    remove.addEventListener("click", () => guarded(remove, async () => {
      if (!confirm(t("storage.confirm_remove", { path: d.path }))) return;
      try {
        await api("POST", "storage/remove", { uuid: d.uuid });
      } catch (err) {
        // A cache that still holds files needs an explicit second yes.
        if (!(err instanceof ApiError) || err.status !== 422 || !/--force/.test(err.detail)) throw err;
        if (!confirm(t("storage.confirm_force", { detail: err.detail }))) return;
        await api("POST", "storage/remove", { uuid: d.uuid, force: true });
      }
      say(t("storage.removed", { path: d.path }), "ok");
      await loadStorage();
    }));
    tbody.append(row);
  }
}

// --- apps -----------------------------------------------------------------------

function cell(tr, content) {
  const td = document.createElement("td");
  if (content instanceof Node) td.append(content);
  else td.textContent = content;
  tr.append(td);
  return td;
}

function button(key, onClick, danger) {
  const b = document.createElement("button");
  b.textContent = t(key);
  if (danger) b.className = "danger";
  b.addEventListener("click", () => guarded(b, () => onClick(b)));
  return b;
}

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
  else say(t("apps.job_failed", { name, unit: `dahlia-app-${id}` }), "error");
  if (current === "apps") await loadApps();
}

function link(href) {
  const a = document.createElement("a");
  a.href = href;
  a.target = "_blank";
  a.rel = "noopener";
  a.textContent = href.replace(/^https?:\/\//, "");
  return a;
}

// Domain name, mDNS alias and port link; the port link uses the address
// the dashboard was opened with, since that one is known to work.
function appLinks(app) {
  if (!app.public_port) return "-";
  const urls = app.urls || {};
  const list = document.createElement("span");
  list.className = "links";
  for (const href of [urls.domain, urls.mdns, `http://${location.hostname}:${app.public_port}`]) {
    if (href) list.append(link(href));
  }
  return list;
}

let catalogApps = [];

function small(text) {
  const el = document.createElement("div");
  el.className = "hint";
  el.textContent = text;
  return el;
}

function nameCell(tr, app) {
  const td = cell(tr, "");
  const name = document.createElement("strong");
  name.textContent = app.name;
  td.append(name);
  const notes = app.notes && (app.notes[lang] || app.notes.en);
  if (notes) td.append(small(notes));
  if (app.login) {
    const d = document.createElement("details");
    const s = document.createElement("summary");
    s.textContent = t("apps.login");
    const code = document.createElement("code");
    code.textContent = [app.login.user, app.login.password].filter(Boolean).join(" / ");
    d.append(s, code);
    td.append(d);
  }
  return td;
}

function helpers(app) {
  return app.services.filter((s) => s.name !== "app").map((s) => s.image).join(", ");
}

async function updateAll(apps) {
  for (const app of apps) await appJob(app.id, "update", app.name);
}

function renderInstalled(installed) {
  const inst = document.getElementById("apps-installed");
  inst.replaceChildren();
  for (const app of installed) {
    const tr = document.createElement("tr");
    nameCell(tr, app);
    const ver = cell(tr, app.version);
    if (app.update) {
      const badge = document.createElement("div");
      badge.className = "badge";
      badge.textContent = t("apps.update_available", { version: app.update.version });
      ver.append(badge);
    }
    cell(tr, tv("app_status", app.status));
    cell(tr, appLinks(app));
    const ops = cell(tr, "");
    ops.className = "ops";
    if (app.status === "running") {
      ops.append(button("apps.stop", async () => {
        await api("POST", `apps/${app.id}/stop`);
        await loadApps();
      }));
    } else {
      ops.append(button("apps.start", async () => {
        await api("POST", `apps/${app.id}/start`);
        await loadApps();
      }));
    }
    const upd = button("apps.update", () => appJob(app.id, "update", app.name));
    if (app.update) upd.className = "primary";
    ops.append(upd);
    ops.append(button("apps.remove", async () => {
      if (!confirm(t("apps.confirm_remove", { name: app.name }))) return;
      const deleteData = confirm(t("apps.confirm_delete_data", { id: app.id }));
      await api("POST", `apps/${app.id}/remove`, { delete_data: deleteData });
      say(t("apps.removed", { name: app.name }), "ok");
      await loadApps();
    }, true));
    inst.append(tr);
  }
  if (!installed.length) {
    const tr = document.createElement("tr");
    cell(tr, t("apps.none_installed")).colSpan = 5;
    inst.append(tr);
  }
  const pending = installed.filter((a) => a.update);
  const all = document.getElementById("apps-update-all");
  all.hidden = !pending.length;
  all.textContent = t("apps.update_all", { count: pending.length });
  all.onclick = () => guarded(all, () => updateAll(pending));
}

function confirmInstall(app) {
  const parts = [t("apps.confirm_install", { name: app.name })];
  const extra = helpers(app);
  if (extra) parts.push(t("apps.confirm_services", { services: extra }));
  const lan = app.lan_ports.map((p) => `${p.host}/${p.protocol}`).join(", ");
  if (lan) parts.push(t("apps.confirm_lan", { ports: lan }));
  return confirm(parts.join("\n\n"));
}

function renderCatalog() {
  const query = document.getElementById("apps-search").value.trim().toLowerCase();
  const category = document.getElementById("apps-category").value;
  const cat = document.getElementById("apps-catalog");
  cat.replaceChildren();
  const shown = catalogApps.filter((app) => {
    if (category && app.category !== category) return false;
    if (!query) return true;
    const desc = app.description[lang] || app.description.en || "";
    return `${app.id} ${app.name} ${desc}`.toLowerCase().includes(query);
  });
  for (const app of shown) {
    const tr = document.createElement("tr");
    const name = cell(tr, "");
    const strong = document.createElement("strong");
    strong.textContent = app.name;
    name.append(strong);
    if (app.website) name.append(document.createElement("br"), link(app.website));
    cell(tr, tv("category", app.category));
    const desc = cell(tr, app.description[lang] || app.description.en || "");
    const extra = helpers(app);
    if (extra) desc.append(small(t("apps.with_services", { services: extra })));
    cell(tr, app.version);
    const ops = cell(tr, "");
    if (app.installed) {
      ops.textContent = t("apps.is_installed");
    } else {
      ops.append(button("apps.install", async () => {
        if (!confirmInstall(app)) return;
        await appJob(app.id, "install", app.name);
      }));
    }
    cat.append(tr);
  }
  if (!shown.length) {
    const tr = document.createElement("tr");
    const key = catalogApps.length ? "apps.no_match" : "apps.catalog_empty";
    cell(tr, t(key)).colSpan = 5;
    cat.append(tr);
  }
}

function fillCategories() {
  const select = document.getElementById("apps-category");
  const chosen = select.value;
  const used = [...new Set(catalogApps.map((a) => a.category))];
  used.sort((x, y) => tv("category", x).localeCompare(tv("category", y)));
  const all = document.createElement("option");
  all.value = "";
  all.textContent = t("apps.all_categories");
  select.replaceChildren(all);
  for (const c of used) {
    const o = document.createElement("option");
    o.value = c;
    o.textContent = tv("category", c);
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

function fact(dl, key, value) {
  const dt = document.createElement("dt");
  dt.textContent = t(key);
  const dd = document.createElement("dd");
  if (value instanceof Node) dd.append(value);
  else dd.textContent = value ?? "-";
  dl.append(dt, dd);
}

async function loadDomains() {
  const d = await api("GET", "domains");
  const form = document.getElementById("domains-form");
  form.elements.domain.value = d.domain;
  form.elements.mdns_aliases.checked = d.mdns_aliases;

  const rows = document.getElementById("dns-records");
  rows.replaceChildren();
  for (const name of d.dns_records) {
    const tr = document.createElement("tr");
    const code = document.createElement("code");
    code.textContent = name;
    cell(tr, code);
    cell(tr, d.address || t("domains.no_address"));
    rows.append(tr);
  }

  const dl = document.getElementById("tls-info");
  dl.replaceChildren();
  const fpr = document.createElement("code");
  fpr.textContent = d.ca ? d.ca.sha256 : "-";
  fact(dl, "domains.ca_fingerprint", fpr);
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
    const tr = document.createElement("tr");
    const cells = [
      t(`job.${name}`),
      tv("job_state", s.active),
      s.result ? tv("job_result", s.result) : "-",
      s.started || "-",
    ];
    for (const c of cells) {
      const td = document.createElement("td");
      td.textContent = c;
      tr.append(td);
    }
    const td = document.createElement("td");
    if (STARTABLE.includes(name)) {
      const b = document.createElement("button");
      b.textContent = t("jobs.start");
      b.disabled = s.active === "active" || s.active === "activating";
      b.addEventListener("click", () => guarded(b, async () => {
        await api("POST", `jobs/${name}/start`);
        say(t("jobs.started", { job: t(`job.${name}`) }), "ok");
        await loadJobs();
      }));
      td.append(b);
    }
    tr.append(td);
    tbody.append(tr);
  }
}

// --- updates --------------------------------------------------------------------

async function checkUpdates(button) {
  const status = document.getElementById("updates-status");
  status.textContent = t("updates.checking");
  const data = await api("POST", "updates/check");
  const tbody = document.getElementById("updates");
  tbody.replaceChildren();
  for (const p of data.packages) {
    const tr = document.createElement("tr");
    for (const c of [p.name, p.current || "-", p.new, p.origin]) {
      const td = document.createElement("td");
      td.textContent = c;
      tr.append(td);
    }
    tbody.append(tr);
  }
  status.textContent = data.packages.length
    ? t("updates.available", { n: data.packages.length })
    : t("updates.none");
  document.getElementById("updates-apply").disabled = !data.packages.length;
  button.disabled = false;
}

async function applyUpdates() {
  if (!confirm(t("updates.confirm"))) return;
  await api("POST", "updates/apply");
  document.getElementById("updates-apply").disabled = true;
  say(t("updates.started"), "ok");
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
      await loadOverview();
    });
  });
}

function bindButtons() {
  for (const b of document.querySelectorAll("#nav button")) {
    b.addEventListener("click", () => open(b.dataset.view));
  }
  document.getElementById("logout").addEventListener("click", (e) => guarded(e.target, async () => {
    await api("POST", "logout");
    say("");
    showLogin();
  }));
  for (const [id, key, path] of [["reboot", "overview.confirm_reboot", "system/reboot"],
                                 ["poweroff", "overview.confirm_poweroff", "system/poweroff"]]) {
    const b = document.getElementById(id);
    b.addEventListener("click", () => guarded(b, async () => {
      if (!confirm(t(key))) return;
      await api("POST", path);
      say(t(`overview.${id}_started`), "ok");
    }));
  }
  document.getElementById("storage-refresh").addEventListener("click", (e) => guarded(e.target, loadStorage));
  document.getElementById("apps-search").addEventListener("input", renderCatalog);
  document.getElementById("apps-category").addEventListener("change", renderCatalog);
  const refresh = document.getElementById("apps-refresh");
  refresh.addEventListener("click", () => guarded(refresh, async () => {
    await api("POST", "apps/catalog/refresh");
    await loadApps();
  }));
  document.getElementById("jobs-refresh").addEventListener("click", (e) => guarded(e.target, loadJobs));
  const check = document.getElementById("updates-check");
  check.addEventListener("click", () => guarded(check, () => checkUpdates(check)));
  const apply = document.getElementById("updates-apply");
  apply.addEventListener("click", () => guarded(apply, applyUpdates));
  document.getElementById("lang").addEventListener("change", async (e) => {
    try { localStorage.setItem("dahlia-lang", e.target.value); } catch (_) { /* storage blocked */ }
    await loadLang(e.target.value);
    if (current && loaders[current] && !["setup", "login"].includes(current)) await open(current);
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
