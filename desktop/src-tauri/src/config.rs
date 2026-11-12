//! The paired server, stored as JSON in the app's config directory.

use serde::{Deserialize, Serialize};
use std::fs;
use std::path::Path;

#[derive(Debug, Default, Clone, PartialEq, Serialize, Deserialize)]
pub struct Config {
    /// Host (and port) of the paired server; None before pairing.
    pub server: Option<String>,
}

pub fn load(path: &Path) -> Config {
    fs::read_to_string(path)
        .ok()
        .and_then(|s| serde_json::from_str(&s).ok())
        .unwrap_or_default()
}

pub fn save(path: &Path, cfg: &Config) -> Result<(), String> {
    if let Some(dir) = path.parent() {
        fs::create_dir_all(dir).map_err(|e| e.to_string())?;
    }
    let tmp = path.with_extension("tmp");
    fs::write(&tmp, serde_json::to_vec_pretty(cfg).map_err(|e| e.to_string())?)
        .map_err(|e| e.to_string())?;
    fs::rename(&tmp, path).map_err(|e| e.to_string())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn roundtrip_and_defaults() {
        let dir = std::env::temp_dir().join(format!("dahlia-cfg-{}", std::process::id()));
        let path = dir.join("server.json");
        assert_eq!(load(&path), Config::default());
        let cfg = Config { server: Some("dahlia.local".into()) };
        save(&path, &cfg).unwrap();
        assert_eq!(load(&path), cfg);
        fs::write(&path, "{broken").unwrap();
        assert_eq!(load(&path), Config::default());
        fs::remove_dir_all(dir).unwrap();
    }
}
