// Migration helper UI: access code, what is taken over, disk, settings,
// progress. Texts only from locales/*.json.
"use strict";

const $ = (s) => document.querySelector(s);
const KEYMAPS = ["de", "us", "gb", "fr", "ch", "at", "es", "it", "nl", "pl", "se", "dk", "no", "fi"];
const STEPS = ["check", "partition", "format", "debootstrap", "system", "dahlia", "unraid", "boot"];
let L = {};
let lang = "de";
let code = "";
let state = null;
let chosen = "";
let poll = null;

function store(key, value) {
  try { if (value === undefined) return sessionStorage.getItem(key); sessionStorage.setItem(key, value); } catch (e) { return null; }
  return null;
}

function t(key, vars = {}) {
  let s = L[key] ?? key;
  for (const [k, v] of Object.entries(vars)) s = s.replaceAll(`{${k}}`, v);
  return s;
}

async function loadLang(l) {
  lang = l;
  L = await (await fetch(`locales/${l}.json`)).json();
  document.documentElement.lang = l;
  document.querySelectorAll("[data-i18n]").forEach((el) => { el.textContent = t(el.dataset.i18n); });
  $("#lang").textContent = l === "de" ? "English" : "Deutsch";
  try { localStorage.setItem("lang", l); } catch (e) { /* private mode */ }
  if (state) render();
}

function size(b) {
  return b >= 1e12 ? `${(b / 1e12).toFixed(1)} TB` : `${Math.round(b / 1e9)} GB`;
}

async function api(path, body) {
  const r = await fetch(path, {
    method: body ? "POST" : "GET",
    headers: { "X-Access-Code": code, "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.error || "error.request");
  return data;
}

function show(view) {
  for (const v of ["code", "form", "job"]) $(`#view-${v}`).hidden = v !== view;
}

function li(text) {
  const el = document.createElement("li");
  el.textContent = text;
  return el;
}

function renderTake(u) {
  const yes = $("#take-yes");
  const no = $("#take-no");
  yes.replaceChildren();
  no.replaceChildren();
  $("#take-none").hidden = u.found;
  if (!u.found) return;
  const n = u.network;
  yes.append(li(n.mode === "static" ? t("take.network_static", { address: n.address }) : t("take.network_dhcp")));
  if (u.identity.hostname) yes.append(li(t("take.hostname", { name: u.identity.hostname })));
  if (u.identity.timezone) yes.append(li(t("take.timezone", { tz: u.identity.timezone })));
  const roles = (r) => u.disks.filter((d) => d.role === r).length;
  yes.append(li(t("take.disks", { data: roles("data"), parity: roles("parity"), cache: roles("cache") })));
  if (u.shares.length) yes.append(li(t("take.shares", { n: u.shares.length })));
  if (u.users.length) yes.append(li(t("take.users", { names: u.users.join(", ") })));
  if (u.containers.length) yes.append(li(t("take.containers", { names: u.containers.join(", ") })));
  if (u.wireguard.length) yes.append(li(t("take.wireguard")));
  no.append(li(t("take.no_plugins")));
  if (u.vms) no.append(li(t("take.no_vms")));
  no.append(li(t("take.no_bond")));
  if (roles("parity")) no.append(li(t("take.parity")));
}

function renderDisks(list) {
  const box = $("#disks");
  box.replaceChildren();
  if (!list.length) { box.append(Object.assign(document.createElement("p"), { className: "muted", textContent: t("disk.none") })); }
  for (const d of list) {
    const row = document.createElement("label");
    row.className = "disk" + (d.lock ? " locked" : "");
    const input = Object.assign(document.createElement("input"), { type: "radio", name: "disk", value: d.path, disabled: !!d.lock, checked: d.path === chosen });
    input.addEventListener("change", () => { chosen = d.path; renderConfirm(); });
    const text = document.createElement("div");
    const b = document.createElement("b");
    b.textContent = `${d.model || d.name} · ${size(d.size)}`;
    const small = document.createElement("small");
    small.textContent = `${d.path}${d.serial ? " · " + d.serial : ""}`;
    text.append(b, small);
    const tag = (k, cls = "") => { const s = document.createElement("span"); s.className = "tag " + cls; s.textContent = t(k, { slot: d.unraid_slot, fs: d.filesystems.join(", ") }); text.append(s); };
    tag(d.ssd ? "disk.ssd" : "disk.hdd");
    if (d.lock) tag(`lock.${d.lock}`, "lock");
    else if (d.has_data) tag(d.filesystems.length ? "disk.data_fs" : "disk.data", "lock");
    row.append(input, text);
    box.append(row);
  }
  renderConfirm();
}

function renderConfirm() {
  const d = state?.disks.find((x) => x.path === chosen);
  $("#confirm-row").hidden = !(d && d.has_data);
  if (d && d.has_data) $("#confirm-row span").textContent = t("disk.confirm", { serial: d.serial || d.name });
}

function renderJob(j) {
  const steps = $("#steps");
  steps.replaceChildren();
  const at = STEPS.indexOf(j.step);
  STEPS.forEach((s, i) => {
    const el = li(t(`step.${s}`));
    if (j.state === "done" || i < at) el.className = "done";
    else if (i === at && j.state === "running") el.className = "now";
    steps.append(el);
  });
  $("#job-title").textContent = t(`job.${j.state}`);
  $("#job-error").hidden = j.state !== "failed";
  $("#job-error").textContent = t("job.error", { error: j.error });
  $("#done").hidden = j.state !== "done";
  $("#setup-code").textContent = state.setup_code;
  $("#log").textContent = j.log.join("\n");
}

function render() {
  if (["running", "done", "failed"].includes(state.job.state)) {
    show("job");
    renderJob(state.job);
    return;
  }
  show("form");
  renderTake(state.unraid);
  renderDisks(state.disks);
  const n = state.unraid.network;
  $("#net-unraid-row").hidden = !(state.unraid.found && n.mode === "static");
  $("#net-unraid").textContent = n.mode === "static" ? t("set.static", { address: n.address, gateway: n.gateway }) : "";
  if (n.mode !== "static") document.querySelector('input[name=network][value=dhcp]').checked = true;
}

async function refresh() {
  try {
    state = await api("/api/state");
  } catch (e) {
    if (e.message === "error.access_code") { show("code"); code = ""; return; }
    return;
  }
  render();
  const f = $("#settings");
  if (!f.dataset.filled) {
    f.dataset.filled = "1";
    f.hostname.value = state.unraid.identity.hostname || "dahlia";
    f.timezone.value = state.unraid.identity.timezone || "Europe/Berlin";
  }
  clearTimeout(poll);
  if (state.job.state === "running") poll = setTimeout(refresh, 2000);
}

$("#code-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  code = $("#code").value.trim().toUpperCase();
  try {
    state = await api("/api/state");
    store("code", code);
    $("#code-error").hidden = true;
    refresh();
  } catch (err) {
    $("#code-error").hidden = false;
    $("#code-error").textContent = t(err.message);
  }
});

$("#settings").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = e.target;
  const err = $("#form-error");
  err.hidden = true;
  const fail = (key) => { err.hidden = false; err.textContent = t(key); };
  if (!chosen) return fail("error.disk");
  if (f.password.value !== f.password2.value) return fail("error.password_mismatch");
  $("#start").disabled = true;
  try {
    await api("/api/install", {
      disk: chosen, confirm: $("#confirm").value, hostname: f.hostname.value,
      account: f.account.value, password: f.password.value, timezone: f.timezone.value,
      keymap: f.keymap.value, network: f.network.value,
    });
    refresh();
  } catch (e2) {
    fail(e2.message);
  } finally {
    $("#start").disabled = false;
  }
});

$("#lang").addEventListener("click", () => loadLang(lang === "de" ? "en" : "de"));

(async () => {
  let saved = null;
  try { saved = localStorage.getItem("lang"); } catch (e) { /* private mode */ }
  await loadLang(saved || (navigator.language.startsWith("de") ? "de" : "en"));
  const sel = document.querySelector("select[name=keymap]");
  for (const k of KEYMAPS) sel.append(new Option(k.toUpperCase(), k));
  sel.value = lang === "de" ? "de" : "us";
  code = store("code") || "";
  if (code) refresh(); else show("code");
})();
