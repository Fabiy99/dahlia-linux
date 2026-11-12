"use strict";
// Pairing page of the desktop app. Talks to the Rust side (src-tauri/src/lib.rs).

const { invoke } = window.__TAURI__.core;
let strings = {};
let pending = null;

function t(key, vars) {
  let s = strings[key] ?? key;
  for (const [k, v] of Object.entries(vars || {})) s = s.replaceAll(`{${k}}`, String(v));
  return s;
}

function say(key, kind, vars) {
  const m = document.getElementById("message");
  m.textContent = key ? t(key, vars) : "";
  m.className = `message ${kind || ""}`;
}

function errorKey(err) {
  const known = ["address", "unreachable", "not_dahlia", "trust", "certificate", "not_paired",
                 "changed", "code", "proof"];
  return known.includes(String(err)) ? `error.${err}` : "error.unexpected";
}

async function busy(btn, fn) {
  btn.disabled = true;
  try {
    await fn();
  } catch (err) {
    say(errorKey(err), "error");
  } finally {
    btn.disabled = false;
  }
}

function show(step) {
  document.getElementById("step-find").hidden = step !== "find";
  document.getElementById("step-check").hidden = step !== "check";
}

async function check(host, btn) {
  await busy(btn, async () => {
    say("pair.contacting");
    pending = await invoke("fetch_ca", { host });
    document.getElementById("check-host").textContent = pending.host;
    document.getElementById("fingerprint").textContent = pending.fingerprint;
    document.getElementById("code-form").reset();
    say("");
    show("check");
  });
}

async function search() {
  const list = document.getElementById("found");
  const btn = document.getElementById("search");
  await busy(btn, async () => {
    list.replaceChildren(Object.assign(document.createElement("li"), { className: "hint", textContent: t("pair.searching") }));
    const found = await invoke("discover");
    list.replaceChildren(...found.map((f) => {
      const li = document.createElement("li");
      const b = document.createElement("button");
      b.type = "button";
      const name = document.createElement("b");
      name.textContent = f.name;
      const host = document.createElement("small");
      host.textContent = f.host;
      b.append(name, host);
      b.addEventListener("click", () => check(f.host, b));
      li.append(b);
      return li;
    }));
    if (!found.length) {
      list.replaceChildren(Object.assign(document.createElement("li"), { className: "hint", textContent: t("pair.none_found") }));
    }
  });
}

// Paired server: open its dashboard, or say why not.
async function connect(host, btn) {
  const problem = document.getElementById("paired-problem");
  btn.disabled = true;
  say("pair.connecting", "", { host });
  try {
    await invoke("connect");
  } catch (err) {
    say("");
    problem.textContent = t(errorKey(err));
    problem.hidden = false;
  } finally {
    btn.disabled = false;
  }
}

async function start() {
  strings = await invoke("ui_strings");
  document.documentElement.lang = strings["lang"] || "en";
  for (const e of document.querySelectorAll("[data-i18n]")) e.textContent = t(e.dataset.i18n);

  const paired = await invoke("paired");
  document.getElementById("paired-box").hidden = !paired;
  document.getElementById("paired-host").textContent = paired || "";
  const reconnect = document.getElementById("reconnect");
  reconnect.addEventListener("click", () => connect(paired, reconnect));
  document.getElementById("repair").addEventListener("click", (e) => check(paired, e.currentTarget));
  document.getElementById("forget").addEventListener("click", (e) => busy(e.currentTarget, async () => {
    await invoke("forget");
    document.getElementById("paired-box").hidden = true;
  }));
  document.getElementById("search").addEventListener("click", search);
  document.getElementById("manual").addEventListener("submit", (e) => {
    e.preventDefault();
    check(e.target.elements.host.value, e.submitter);
  });
  document.getElementById("back").addEventListener("click", () => { pending = null; say(""); show("find"); });
  document.getElementById("confirm").addEventListener("click", (e) => busy(e.currentTarget, async () => {
    say("pair.trusting");
    await invoke("pair", { host: pending.host, pem: pending.pem });
  }));
  document.getElementById("code-form").addEventListener("submit", (e) => {
    e.preventDefault();
    busy(e.submitter, async () => {
      say("pair.checking_code");
      await invoke("pair_code", { host: pending.host, pem: pending.pem, code: e.target.elements.code.value });
    });
  });
  // "#choose": opened from the menu to pick another server.
  if (paired && location.hash !== "#choose") {
    await connect(paired, reconnect);
  }
  search();
}

start();
