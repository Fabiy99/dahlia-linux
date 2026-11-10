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
