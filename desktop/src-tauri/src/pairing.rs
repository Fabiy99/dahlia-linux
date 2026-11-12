//! Pairing with a server: address check, CA download, fingerprint, trust store.
//!
//! Every Dahlia server has its own local CA (/etc/dahlia/tls/ca.crt, served as
//! /dahlia-ca.crt). The app downloads it once without verification, shows the
//! SHA-256 fingerprint and only after the user compared it with the one on the
//! server's monitor or in the dashboard adds it to the user's trust store, so
//! the system webview accepts the server's certificate.
//!
//! Instead of the fingerprint the user can enter a pairing code the dashboard
//! shows (usually on the phone). The code itself is never sent: the server
//! proves it knows the code for exactly the CA the app downloaded (see
//! packages/dashboard/.../dahlia_dashboard/pairing.py).

use base64::Engine;
use hmac::{Hmac, Mac};
use sha2::{Digest, Sha256};
use std::path::Path;
use std::process::Command;
use std::time::Duration;

const MAX_CA_BYTES: usize = 64 * 1024;
const PROOF_PREFIX: &[u8] = b"dahlia-pairing\0";
const CODE_ALPHABET: &str = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789";
const CODE_LENGTH: usize = 10;

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

fn hex(bytes: &[u8]) -> String {
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}

/// Pairing code as the dashboard shows it (ABCDE-FGHJK), without dash and spaces.
pub fn normalize_code(input: &str) -> Result<String, String> {
    let code: String = input
        .chars()
        .filter(|c| !matches!(c, '-' | ' '))
        .map(|c| c.to_ascii_uppercase())
        .collect();
    if code.len() != CODE_LENGTH || !code.chars().all(|c| CODE_ALPHABET.contains(c)) {
        return Err("code".into());
    }
    Ok(code)
}

/// HMAC-SHA256(code, PREFIX + nonce + sha256(CA)), hex.
pub fn proof(code: &str, nonce: &[u8], der: &[u8]) -> String {
    let mut mac = Hmac::<Sha256>::new_from_slice(code.as_bytes()).expect("any key length");
    mac.update(PROOF_PREFIX);
    mac.update(nonce);
    mac.update(&Sha256::digest(der));
    hex(&mac.finalize().into_bytes())
}

/// Compares in constant time.
pub fn proof_matches(expected: &str, got: &str) -> bool {
    expected.len() == got.len()
        && expected.bytes().zip(got.bytes()).fold(0u8, |acc, (a, b)| acc | (a ^ b)) == 0
}

fn insecure_client() -> Result<reqwest::blocking::Client, String> {
    reqwest::blocking::Client::builder()
        .danger_accept_invalid_certs(true)
        .timeout(Duration::from_secs(8))
        .redirect(reqwest::redirect::Policy::none())
        .build()
        .map_err(|e| e.to_string())
}

/// Asks the server for its proof over the nonce. Without verification, like
/// fetch_ca: the proof is what counts.
pub fn request_proof(host: &str, nonce: &[u8]) -> Result<String, String> {
    let resp = insecure_client()?
        .post(format!("https://{host}/api/pairing/proof"))
        .header("X-Dahlia-Request", "1")
        .json(&serde_json::json!({ "nonce": hex(nonce) }))
        .send()
        .map_err(|_| "unreachable".to_string())?;
    if resp.status() == reqwest::StatusCode::NOT_FOUND {
        return Err("code".into());
    }
    if !resp.status().is_success() {
        return Err("not_dahlia".into());
    }
    let body: serde_json::Value = resp.json().map_err(|_| "not_dahlia".to_string())?;
    body.get("proof")
        .and_then(|p| p.as_str())
        .map(str::to_string)
        .ok_or_else(|| "not_dahlia".to_string())
}

/// Before the dashboard opens: is the server there, with the CA we paired?
/// Errors: "unreachable", or "changed" when it answers with another certificate
/// (reinstalled server, or someone else at that address).
pub fn check(host: &str, pem: &str) -> Result<(), String> {
    let ca = reqwest::Certificate::from_pem(pem.as_bytes()).map_err(|_| "certificate".to_string())?;
    let pinned = reqwest::blocking::Client::builder()
        .tls_built_in_root_certs(false)
        .add_root_certificate(ca)
        .timeout(Duration::from_secs(6))
        .redirect(reqwest::redirect::Policy::none())
        .build()
        .map_err(|e| e.to_string())?;
    let url = format!("https://{host}/dahlia-ca.crt");
    if pinned.get(&url).send().is_ok() {
        return Ok(());
    }
    match insecure_client()?.get(&url).send() {
        Ok(_) => Err("changed".into()),
        Err(_) => Err("unreachable".into()),
    }
}

/// Downloads /dahlia-ca.crt without certificate verification (the user checks
/// the fingerprint afterwards).
pub fn fetch_ca(host: &str) -> Result<String, String> {
    let resp = insecure_client()?
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

    #[test]
    fn code_input() {
        assert_eq!(normalize_code("abcde-fghjk").unwrap(), "ABCDEFGHJK");
        assert_eq!(normalize_code(" ABCDE FGHJK ").unwrap(), "ABCDEFGHJK");
        for bad in ["", "ABCDE", "ABCDE-FGHJ0", "ABCDE-FGHJKL", "ÄBCDE-FGHJK"] {
            assert!(normalize_code(bad).is_err(), "{bad}");
        }
    }

    #[test]
    fn proof_matches_dashboard() {
        // tests/test_pairing.py computes the same value with Python's hmac.
        let nonce: Vec<u8> = (0..32).collect();
        let der: Vec<u8> = (0..50).collect();
        let p = proof("ABCDEFGHJK", &nonce, &der);
        assert_eq!(p, "f613c2eb6eadd6c08d622c35bb99c8980791618551b1d44c250fed92c5e47a46");
        assert!(proof_matches(&p, &proof("ABCDEFGHJK", &nonce, &der)));
        assert!(!proof_matches(&p, &proof("ABCDEFGHJL", &nonce, &der)));
        assert!(!proof_matches(&p, &proof("ABCDEFGHJK", &nonce, &der[1..])));
        assert!(!proof_matches(&p, ""));
    }
}
