use crate::{Error, Result};
use serde::{Deserialize, Serialize};
use std::collections::BTreeSet;
use std::net::SocketAddr;

pub const MAX_BODY_BYTES: usize = 8 * 1024 * 1024; // 8 MiB, encoded and decoded
pub const MAX_CONNECTIONS: usize = 128;

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum Format {
    OpenaiResponses,
    AnthropicMessages,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Submission {
    pub path: String,
    pub format: Format,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Destination {
    pub host: String,
    pub port: u16,
    pub submissions: Vec<Submission>,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Policy {
    pub version: u32,
    pub listen: SocketAddr,
    #[serde(default)]
    pub native_listen: Option<SocketAddr>,
    pub destinations: Vec<Destination>,
    pub max_body_bytes: usize,
    pub timeout_ms: u64,
    pub max_connections: usize,
}

impl Policy {
    pub fn validate(&self) -> Result<()> {
        if self.version != 1
            || !self.listen.ip().is_loopback()
            || self
                .native_listen
                .is_some_and(|address| !address.ip().is_loopback() || address == self.listen)
            || self.destinations.is_empty()
            || self.destinations.len() > 128
            || !(1..=MAX_BODY_BYTES).contains(&self.max_body_bytes)
            || !(100..=120_000).contains(&self.timeout_ms)
            || !(1..=MAX_CONNECTIONS).contains(&self.max_connections)
        {
            return Err(Error::Policy);
        }
        let mut hosts = BTreeSet::new();
        for destination in &self.destinations {
            if !valid_host(&destination.host)
                || destination.port == 0
                || !hosts.insert((&destination.host, destination.port))
                || destination.submissions.is_empty()
                || destination.submissions.len() > 32
            {
                return Err(Error::Policy);
            }
            let mut paths = BTreeSet::new();
            for submission in &destination.submissions {
                if !submission.path.starts_with('/')
                    || submission.path.starts_with("//")
                    || submission.path.contains(['?', '#', '%', '\\'])
                    || submission.path.chars().any(char::is_control)
                    || !paths.insert(&submission.path)
                {
                    return Err(Error::Policy);
                }
            }
        }
        Ok(())
    }

    pub fn destination(&self, host: &str, port: u16) -> Result<&Destination> {
        self.destinations
            .iter()
            .find(|entry| entry.host == host && entry.port == port)
            .ok_or(Error::Unsupported)
    }
}

fn valid_host(host: &str) -> bool {
    !host.is_empty()
        && host.len() <= 253
        && host.split('.').all(|part| {
            !part.is_empty()
                && part.len() <= 63
                && !part.starts_with('-')
                && !part.ends_with('-')
                && part
                    .bytes()
                    .all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || c == b'-')
        })
}
