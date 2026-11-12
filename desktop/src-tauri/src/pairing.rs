//! Pairing with a server: address check, CA download, fingerprint, trust store.
//!
//! Every Dahlia server has its own local CA (/etc/dahlia/tls/ca.crt, served as
//! /dahlia-ca.crt). The app downloads it once without verification, shows the
//! SHA-256 fingerprint and only after the user compared it with the one on the
//! server's monitor or in the dashboard adds it to the user's trust store, so
//! the system webview accepts the server's certificate.

use base64::Engine;
use sha2::{Digest, Sha256};
use std::path::Path;
use std::process::Command;
use std::time::Duration;

const MAX_CA_BYTES: usize = 64 * 1024;

/// Accepts a host name or IP address, optionally with a port; nothing else.
pub fn normalize_host(input: &str) -> Result<String, String> {
    let input = input.trim().trim_end_matches('/');
    let input = input.strip_prefix("https://").unwrap_or(input);
    if input.is_empty() || input.contains(['/', '@', '?', '#', ' ']) {
        return Err("address".into());
    }
    let url = url::Url::parse(&format!("https://{input}/")).map_err(|_| "address".to_string())?;
    let host = url.host_str().ok_or("address")?;
    Ok(match url.port() {
        Some(p) => format!("{host}:{p}"),
        None => host.to_string(),
    })
}

/// DER bytes of the first certificate in a PEM text.
pub fn pem_to_der(pem: &str) -> Result<Vec<u8>, String> {
    let start = pem.find("-----BEGIN CERTIFICATE-----").ok_or("certificate")?;
    let rest = &pem[start + 27..];
    let end = rest.find("-----END CERTIFICATE-----").ok_or("certificate")?;
    let b64: String = rest[..end].chars().filter(|c| !c.is_whitespace()).collect();
    base64::engine::general_purpose::STANDARD
        .decode(b64)
        .map_err(|_| "certificate".to_string())
}

/// SHA-256 over the DER form, as AB:CD:... (same format as the dashboard).
pub fn fingerprint(der: &[u8]) -> String {
    Sha256::digest(der)
        .iter()
        .map(|b| format!("{b:02X}"))
        .collect::<Vec<_>>()
        .join(":")
}

/// Downloads /dahlia-ca.crt without certificate verification (the user checks
/// the fingerprint afterwards).
pub fn fetch_ca(host: &str) -> Result<String, String> {
    let client = reqwest::blocking::Client::builder()
        .danger_accept_invalid_certs(true)
        .timeout(Duration::from_secs(8))
        .redirect(reqwest::redirect::Policy::none())
        .build()
        .map_err(|e| e.to_string())?;
    let resp = client
        .get(format!("https://{host}/dahlia-ca.crt"))
        .send()
        .map_err(|_| "unreachable".to_string())?;
    if !resp.status().is_success() {
        return Err("not_dahlia".into());
    }
    let bytes = resp.bytes().map_err(|_| "unreachable".to_string())?;
    if bytes.len() > MAX_CA_BYTES {
        return Err("not_dahlia".into());
    }
    let pem = String::from_utf8(bytes.to_vec()).map_err(|_| "not_dahlia".to_string())?;
    pem_to_der(&pem).map_err(|_| "not_dahlia".to_string())?;
    Ok(pem)
}

/// Adds the CA to the trust store the system webview uses. The operating
/// system asks the user to confirm (Windows dialog, macOS password, polkit).
pub fn trust(ca_file: &Path) -> Result<(), String> {
    let file = ca_file.to_string_lossy().to_string();
    let mut cmd = if cfg!(target_os = "windows") {
        let mut c = Command::new("certutil");
        c.args(["-user", "-addstore", "Root", &file]);
        c
    } else if cfg!(target_os = "macos") {
        let home = std::env::var("HOME").map_err(|_| "trust".to_string())?;
        let keychain = format!("{home}/Library/Keychains/login.keychain-db");
        let mut c = Command::new("security");
        c.args(["add-trusted-cert", "-r", "trustRoot", "-k", &keychain, &file]);
        c
    } else if Path::new("/usr/sbin/update-ca-certificates").exists() {
        // Debian/Ubuntu: WebKitGTK reads the bundle update-ca-certificates builds.
        let name = ca_file.file_name().and_then(|n| n.to_str()).ok_or("trust")?;
        let target = format!("/usr/local/share/ca-certificates/{name}");
        let mut c = Command::new("pkexec");
        c.args(["sh", "-c", "install -m 0644 \"$1\" \"$2\" && update-ca-certificates", "sh", &file, &target]);
        c
    } else {
        // Fedora, Arch and others: p11-kit trust store.
        let mut c = Command::new("pkexec");
        c.args(["trust", "anchor", "--store", &file]);
        c
    };
    match cmd.status() {
        Ok(s) if s.success() => Ok(()),
        _ => Err("trust".into()),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const PEM: &str = "-----BEGIN CERTIFICATE-----\nAAEC\nAw==\n-----END CERTIFICATE-----\n";

    #[test]
    fn host_accepts_names_ips_and_ports() {
        assert_eq!(normalize_host(" dahlia.local ").unwrap(), "dahlia.local");
        assert_eq!(normalize_host("https://192.168.1.20/").unwrap(), "192.168.1.20");
        assert_eq!(normalize_host("nas:8443").unwrap(), "nas:8443");
        assert_eq!(normalize_host("NAS.local").unwrap(), "nas.local");
    }

    #[test]
    fn host_rejects_everything_else() {
        for bad in ["", "a/b", "user@nas", "nas?x=1", "nas#x", "two words", "http://nas"] {
            assert!(normalize_host(bad).is_err(), "{bad}");
        }
    }

    #[test]
    fn pem_and_fingerprint() {
        let der = pem_to_der(PEM).unwrap();
        assert_eq!(der, vec![0, 1, 2, 3]);
        // sha256(00 01 02 03)
        assert_eq!(
            fingerprint(&der),
            "05:4E:DE:C1:D0:21:1F:62:4F:ED:0C:BC:A9:D4:F9:40:0B:0E:49:1C:43:74:2A:F2:C5:B0:AB:EB:F0:C9:90:D8"
        );
        assert!(pem_to_der("no certificate").is_err());
    }
}
