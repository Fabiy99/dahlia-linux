//! Finds Dahlia servers in the local network: mDNS service _dahlia._tcp,
//! published by avahi from /etc/avahi/services/dahlia.service.

use mdns_sd::{ServiceDaemon, ServiceEvent};
use serde::Serialize;
use std::time::{Duration, Instant};

pub const SERVICE: &str = "_dahlia._tcp.local.";

#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct Found {
    pub name: String,
    pub host: String,
}

/// "dahlia.local." -> "dahlia.local"; port 443 is left out.
pub fn host_of(hostname: &str, port: u16) -> String {
    let h = hostname.trim_end_matches('.').to_lowercase();
    if port == 443 { h } else { format!("{h}:{port}") }
}

pub fn browse(wait: Duration) -> Vec<Found> {
    let Ok(daemon) = ServiceDaemon::new() else { return vec![] };
    let Ok(rx) = daemon.browse(SERVICE) else { return vec![] };
    let mut found: Vec<Found> = Vec::new();
    let end = Instant::now() + wait;
    while let Some(left) = end.checked_duration_since(Instant::now()) {
        match rx.recv_timeout(left) {
            Ok(ServiceEvent::ServiceResolved(info)) => {
                let host = host_of(info.get_hostname(), info.get_port());
                let name = info.get_fullname().trim_end_matches(SERVICE).trim_end_matches('.').to_string();
                if !found.iter().any(|f| f.host == host) {
                    found.push(Found { name, host });
                }
            }
            Ok(_) => {}
            Err(_) => break,
        }
    }
    let _ = daemon.shutdown();
    found.sort_by(|a, b| a.host.cmp(&b.host));
    found
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn host_names() {
        assert_eq!(host_of("Dahlia.local.", 443), "dahlia.local");
        assert_eq!(host_of("nas.local.", 8443), "nas.local:8443");
    }
}
