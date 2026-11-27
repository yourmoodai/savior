//! Live inspection is memory-only and never authorizes retained text.
use crate::{ReviewResult, live, now_ms, store::profile_dir, traces::Trace};
use serde::{Deserialize, Serialize};
use serde_json::Value;
#[cfg(any(feature = "inspector", test))]
use serde_json::json;
use std::{
    io,
    time::{Duration, Instant},
};

const MAX_FRAME_BYTES: usize = 32 * 1024 * 1024; // 32 MiB
const DEADLINE: Duration = Duration::from_millis(20);

#[cfg(test)]
mod tests;

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Observation {
    pub version: u32,
    pub id: String,
    pub timestamp_ms: i64,
    pub stage: String,
    pub data: Value,
}

pub fn valid_id(id: &str) -> bool {
    id.len() == 64 && id.bytes().all(|c| c.is_ascii_hexdigit())
}

impl Observation {
    fn validate(&self) -> ReviewResult<()> {
        if self.version != 1 || !valid_id(&self.id) || self.timestamp_ms < 0 {
            return Err("invalid_observation".into());
        }
        let valid = match self.stage.as_str() {
            "processing" => self.data["original"]
                .as_str()
                .is_some_and(|s| s.len() <= savior::MAX_TEXT_BYTES),
            "detected" => self.data["detected_count"]
                .as_u64()
                .is_some_and(|n| n <= savior::MAX_TEXT_BYTES as u64),
            "replaced" => self.data["replacement_count"]
                .as_u64()
                .is_some_and(|n| n <= savior::MAX_TEXT_BYTES as u64),
            "completed" => serde_json::from_value::<Trace>(self.data["trace"].clone())
                .is_ok_and(|t| t.id == self.id && t.validate().is_ok()),
            "delivery" => self.data["disposition"].as_str().is_some_and(|s| {
                ["forwarded", "forwarding_unknown", "blocked", "cancelled"].contains(&s)
            }),
            "recording" => self.data["warning"] == "trace_not_recorded",
            _ => false,
        };
        if valid {
            Ok(())
        } else {
            Err("invalid_observation".into())
        }
    }
}

/// A missing, stalled, or broken viewer must never change processing.
pub fn publish(id: &str, stage: &str, data: Value) {
    let event = Observation {
        version: 1,
        id: id.into(),
        timestamp_ms: now_ms(),
        stage: stage.into(),
        data,
    };
    let _ = (|| -> io::Result<()> {
        let profile = profile_dir().map_err(|_| io::ErrorKind::InvalidInput)?;
        let mut stream = live::platform::connect_named(&profile, "view")?;
        let deadline = Instant::now() + DEADLINE;
        let mut hello = [0];
        live::read_exact(&mut stream, &mut hello, deadline)?;
        if hello != [1] {
            return Err(io::ErrorKind::InvalidData.into());
        }
        event.validate().map_err(|_| io::ErrorKind::InvalidData)?;
        let bytes = serde_json::to_vec(&event).map_err(|_| io::ErrorKind::InvalidData)?;
        if bytes.len() > MAX_FRAME_BYTES {
            return Err(io::ErrorKind::InvalidData.into());
        }
        live::write_all(&mut stream, &(bytes.len() as u32).to_be_bytes(), deadline)?;
        live::write_all(&mut stream, &bytes, deadline)
    })();
}

#[cfg(any(feature = "inspector", test))]
pub(crate) fn receive(stream: &mut (impl io::Read + io::Write)) -> ReviewResult<Observation> {
    let deadline = Instant::now() + Duration::from_millis(100);
    let read = |error: io::Error| {
        if error.kind() == io::ErrorKind::InvalidData {
            "invalid_observation"
        } else {
            "observation_dropped"
        }
        .to_string()
    };
    live::write_all(stream, &[1], deadline).map_err(read)?;
    let mut size = [0; 4];
    live::read_exact(stream, &mut size, deadline).map_err(read)?;
    let size = u32::from_be_bytes(size) as usize;
    if size == 0 || size > MAX_FRAME_BYTES {
        return Err("invalid_observation".into());
    }
    let mut bytes = vec![0; size];
    live::read_exact(stream, &mut bytes, deadline).map_err(read)?;
    let event: Observation = serde_json::from_slice(&bytes).map_err(|_| "invalid_observation")?;
    event.validate()?;
    Ok(event)
}

#[cfg(any(feature = "inspector", test))]
pub(crate) mod session {
    use super::*;
    use std::collections::VecDeque;

    const MAX_ROWS: usize = 1_000;
    const MAX_BYTES: usize = 32 * 1024 * 1024; // 32 MiB

    pub struct Session {
        pub id: String,
        pub sequence: u64,
        pub dropped: u64,
        rows: VecDeque<Value>,
        bytes: usize,
        counts: std::collections::BTreeMap<String, i64>,
        replacements: u64,
        elapsed_ms: f64,
        timed: u64,
    }

    impl Default for Session {
        fn default() -> Self {
            Self {
                id: crate::traces::random_id(),
                sequence: 0,
                dropped: 0,
                rows: VecDeque::new(),
                bytes: 0,
                counts: Default::default(),
                replacements: 0,
                elapsed_ms: 0.0,
                timed: 0,
            }
        }
    }

    impl Session {
        pub fn apply(&mut self, event: Observation) -> ReviewResult<()> {
            event.validate()?;
            let index = self.rows.iter().position(|r| r["id"] == event.id);
            let mut row = if let Some(index) = index {
                let row = self.rows.remove(index).expect("existing observation");
                self.bytes -= row.to_string().len();
                row
            } else {
                json!({"id":event.id,"timestamp_ms":event.timestamp_ms,"stages":[],"outcome":null,"disposition":"pending","original":null,"output":null,"elapsed_ms":null})
            };
            // A stage is an immutable fact; retries cannot count or apply it twice.
            if row["stages"]
                .as_array()
                .unwrap()
                .iter()
                .any(|s| s["name"] == event.stage)
            {
                self.bytes += row.to_string().len();
                self.rows.push_back(row);
                return Ok(());
            }
            match event.stage.as_str() {
                "processing" => row["original"] = event.data["original"].clone(),
                "detected" => row["detected_count"] = event.data["detected_count"].clone(),
                "replaced" => row["replacement_count"] = event.data["replacement_count"].clone(),
                "completed" => {
                    let trace = &event.data["trace"];
                    let disposition = row["disposition"].clone();
                    for (key, value) in trace.as_object().unwrap() {
                        if (key == "original" && value.is_null()) || key == "timestamp_ms" {
                            continue;
                        }
                        row[key] = value.clone();
                    }
                    if disposition != "pending" {
                        row["disposition"] = disposition;
                        if row["disposition"] == "cancelled" {
                            row["outcome"] = "cancelled".into();
                        }
                    }
                    *self
                        .counts
                        .entry(row["outcome"].as_str().unwrap().into())
                        .or_default() += 1;
                    self.replacements += trace["decision"]["applied"]
                        .as_array()
                        .map_or(0, |s| s.len()) as u64;
                    if trace["code"] != "adapter_failed" {
                        self.elapsed_ms += trace["elapsed_ms"].as_f64().unwrap_or(0.0);
                        self.timed += 1;
                    }
                    row["assessment"] = event.data["assessment"].clone();
                }
                "delivery" => {
                    row["disposition"] = event.data["disposition"].clone();
                    if row["disposition"] == "cancelled"
                        && let Some(previous) = row["outcome"].as_str()
                    {
                        *self.counts.entry(previous.into()).or_default() -= 1;
                        *self.counts.entry("cancelled".into()).or_default() += 1;
                        row["outcome"] = "cancelled".into();
                    }
                }
                "recording" => row["recording_warning"] = event.data["warning"].clone(),
                _ => unreachable!(),
            }
            row["stages"]
                .as_array_mut()
                .unwrap()
                .push(json!({"name":event.stage,"timestamp_ms":event.timestamp_ms}));
            self.sequence += 1;
            row["sequence"] = self.sequence.into();
            self.bytes += row.to_string().len();
            self.rows.push_back(row);
            while self.rows.len() > MAX_ROWS || self.bytes > MAX_BYTES {
                let index = self
                    .rows
                    .iter()
                    .position(|r| !r["outcome"].is_null())
                    .unwrap_or(0);
                if let Some(row) = self.rows.remove(index) {
                    self.bytes -= row.to_string().len();
                    self.dropped += 1;
                }
            }
            Ok(())
        }

        pub fn snapshot(&self, after: u64) -> Value {
            json!({"session_id":self.id,"sequence":self.sequence,"dropped":self.dropped,"retained_ids":self.rows.iter().map(|r| &r["id"]).collect::<Vec<_>>(),"rows":self.rows.iter().filter(|r| r["sequence"].as_u64().unwrap_or(0)>after).collect::<Vec<_>>(),"stats":{"outcomes":self.counts,"replacements":self.replacements,"mean_elapsed_ms":if self.timed==0 {None}else{Some(self.elapsed_ms/self.timed as f64)}}})
        }
    }
}
