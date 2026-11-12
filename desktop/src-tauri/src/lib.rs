//! Dahlia desktop app: pairs with a Dahlia server and shows its dashboard in a
//! native window. Apps opened from the dashboard get their own windows.

mod config;
mod discover;
mod pairing;

use serde::Serialize;
use serde_json::Value;
use std::path::PathBuf;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::time::Duration;
use tauri::menu::{Menu, MenuItem, PredefinedMenuItem, Submenu};
use tauri::webview::NewWindowResponse;
use tauri::{AppHandle, Manager, Url, WebviewUrl, WebviewWindow, WebviewWindowBuilder};

const MAIN: &str = "main";
static WINDOWS: AtomicUsize = AtomicUsize::new(0);

fn strings() -> Value {
    let de = sys_locale::get_locale().is_some_and(|l| l.to_lowercase().starts_with("de"));
    let text = if de { include_str!("../../ui/locales/de.json") } else { include_str!("../../ui/locales/en.json") };
    serde_json::from_str(text).unwrap_or(Value::Null)
}

fn t(strings: &Value, key: &str) -> String {
    strings.get(key).and_then(Value::as_str).unwrap_or(key).to_string()
}

fn config_path(app: &AppHandle) -> Result<PathBuf, String> {
    app.path().app_config_dir().map(|d| d.join("server.json")).map_err(|e| e.to_string())
}

fn server_url(host: &str) -> Result<Url, String> {
    Url::parse(&format!("https://{host}/")).map_err(|e| e.to_string())
}

/// The bundled pairing page (index.html in ui/). "#choose" keeps it from
/// connecting to the paired server right away.
fn local_url() -> Url {
    let base = if cfg!(windows) { "http://tauri.localhost/#choose" } else { "tauri://localhost/#choose" };
    Url::parse(base).expect("static url")
}

fn ca_file(app: &AppHandle, host: &str) -> Result<PathBuf, String> {
    let dir = app.path().app_data_dir().map_err(|e| e.to_string())?;
    Ok(dir.join(format!("dahlia-ca-{}.crt", host.replace(':', "_"))))
}

fn main_window(app: &AppHandle) -> Result<WebviewWindow, String> {
    app.get_webview_window(MAIN).ok_or_else(|| "window".to_string())
}

/// Apps (Jellyfin, Immich, ...) open in a window of their own instead of the browser.
fn open_app_window(app: &AppHandle, url: Url) {
    let n = WINDOWS.fetch_add(1, Ordering::Relaxed);
    let title = url.host_str().unwrap_or("Dahlia").to_string();
    let _ = WebviewWindowBuilder::new(app, format!("app-{n}"), WebviewUrl::External(url))
        .title(title)
        .inner_size(1280.0, 860.0)
        .on_document_title_changed(|w, title| {
            let _ = w.set_title(&title);
        })
        .build();
}

#[derive(Serialize)]
struct CaInfo {
    host: String,
    pem: String,
    fingerprint: String,
}

#[tauri::command]
async fn discover() -> Vec<discover::Found> {
    tauri::async_runtime::spawn_blocking(|| discover::browse(Duration::from_secs(3)))
        .await
        .unwrap_or_default()
}

#[tauri::command]
async fn fetch_ca(host: String) -> Result<CaInfo, String> {
    let host = pairing::normalize_host(&host)?;
    let h = host.clone();
    let pem = tauri::async_runtime::spawn_blocking(move || pairing::fetch_ca(&h))
        .await
        .map_err(|e| e.to_string())??;
    let fingerprint = pairing::fingerprint(&pairing::pem_to_der(&pem)?);
    Ok(CaInfo { host, pem, fingerprint })
}

/// After the user confirmed the fingerprint: trust the CA, remember the
/// server and show its dashboard.
#[tauri::command]
async fn pair(app: AppHandle, host: String, pem: String) -> Result<(), String> {
    let host = pairing::normalize_host(&host)?;
    pairing::pem_to_der(&pem)?;
    finish_pair(app, host, pem).await
}

/// Pairing with the code from the dashboard instead of the fingerprint.
#[tauri::command]
async fn pair_code(app: AppHandle, host: String, pem: String, code: String) -> Result<(), String> {
    let host = pairing::normalize_host(&host)?;
    let code = pairing::normalize_code(&code)?;
    let der = pairing::pem_to_der(&pem)?;
    let mut nonce = [0u8; 32];
    getrandom::getrandom(&mut nonce).map_err(|e| e.to_string())?;
    let h = host.clone();
    let got = tauri::async_runtime::spawn_blocking(move || pairing::request_proof(&h, &nonce))
        .await
        .map_err(|e| e.to_string())??;
    if !pairing::proof_matches(&pairing::proof(&code, &nonce, &der), &got) {
        return Err("proof".into());
    }
    finish_pair(app, host, pem).await
}

async fn finish_pair(app: AppHandle, host: String, pem: String) -> Result<(), String> {
    let file = ca_file(&app, &host)?;
    if let Some(dir) = file.parent() {
        std::fs::create_dir_all(dir).map_err(|e| e.to_string())?;
    }
    std::fs::write(&file, &pem).map_err(|e| e.to_string())?;
    tauri::async_runtime::spawn_blocking(move || pairing::trust(&file))
        .await
        .map_err(|e| e.to_string())??;
    config::save(&config_path(&app)?, &config::Config { server: Some(host.clone()) })?;
    main_window(&app)?.navigate(server_url(&host)?).map_err(|e| e.to_string())
}

#[tauri::command]
fn paired(app: AppHandle) -> Option<String> {
    config_path(&app).ok().and_then(|p| config::load(&p).server)
}

/// Opens the paired server's dashboard after checking that it answers with
/// the certificate it was paired with.
#[tauri::command]
async fn connect(app: AppHandle) -> Result<(), String> {
    let host = paired(app.clone()).ok_or("not_paired")?;
    let pem = std::fs::read_to_string(ca_file(&app, &host)?).map_err(|_| "changed".to_string())?;
    let h = host.clone();
    tauri::async_runtime::spawn_blocking(move || pairing::check(&h, &pem))
        .await
        .map_err(|e| e.to_string())??;
    main_window(&app)?.navigate(server_url(&host)?).map_err(|e| e.to_string())
}

#[tauri::command]
fn forget(app: AppHandle) -> Result<(), String> {
    config::save(&config_path(&app)?, &config::Config::default())
}

#[tauri::command]
fn ui_strings() -> Value {
    strings()
}

fn build_menu(app: &AppHandle) -> tauri::Result<Menu<tauri::Wry>> {
    let s = strings();
    let server = MenuItem::with_id(app, "server", t(&s, "menu.server"), true, None::<&str>)?;
    let reload = MenuItem::with_id(app, "reload", t(&s, "menu.reload"), true, Some("CmdOrCtrl+R"))?;
    let quit = PredefinedMenuItem::quit(app, Some(&t(&s, "menu.quit")))?;
    let sep = PredefinedMenuItem::separator(app)?;
    let edit = Submenu::with_items(
        app,
        t(&s, "menu.edit"),
        true,
        &[
            &PredefinedMenuItem::copy(app, None)?,
            &PredefinedMenuItem::paste(app, None)?,
            &PredefinedMenuItem::select_all(app, None)?,
        ],
    )?;
    let dahlia = Submenu::with_items(app, "Dahlia", true, &[&server, &reload, &sep, &quit])?;
    Menu::with_items(app, &[&dahlia, &edit])
}

pub fn run() {
    tauri::Builder::default()
        .invoke_handler(tauri::generate_handler![
            discover, fetch_ca, pair, pair_code, paired, connect, forget, ui_strings
        ])
        .setup(|app| {
            let handle = app.handle().clone();
            let opener = handle.clone();
            // The pairing page connects to a paired server by itself (pair.js).
            WebviewWindowBuilder::new(app, MAIN, WebviewUrl::App("index.html".into()))
                .title("Dahlia")
                .inner_size(1360.0, 900.0)
                .min_inner_size(420.0, 600.0)
                .on_new_window(move |url, _features| {
                    open_app_window(&opener, url);
                    NewWindowResponse::Deny
                })
                .build()?;
            app.set_menu(build_menu(&handle)?)?;
            app.on_menu_event(|app, event| {
                let Ok(window) = main_window(app) else { return };
                match event.id().as_ref() {
                    "server" => {
                        let _ = window.navigate(local_url());
                    }
                    "reload" => {
                        let _ = window.reload();
                    }
                    _ => {}
                }
            });
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running Dahlia");
}
