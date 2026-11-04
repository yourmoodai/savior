//! Ephemeral replacement display; independent of retained trace consent.
#[cfg(feature = "watch")]
use crate::ReviewResult;
use crate::{now_ms, store::profile_dir};
use savior::{MAX_TEXT_BYTES, ReplacementMode, RewriteResult};
use serde::{Deserialize, Serialize};
use std::{
    io::{self, Read, Write},
    time::{Duration, Instant},
};

#[cfg(unix)]
mod unix;
#[cfg(unix)]
use unix as platform;
#[cfg(windows)]
mod windows;
#[cfg(windows)]
use windows as platform;

const MAX_FRAME_BYTES: usize = 4 * 1024 * 1024; // 4 MiB
const PUBLISH_TIMEOUT: Duration = Duration::from_millis(50);

#[cfg(test)]
mod tests;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum View {
    Spans = 0,
    SpanDiff = 1,
    PromptDiff = 2,
}
impl View {
    fn from_byte(value: u8) -> io::Result<Self> {
        match value {
            0 => Ok(Self::Spans),
            1 => Ok(Self::SpanDiff),
            2 => Ok(Self::PromptDiff),
            _ => Err(invalid()),
        }
    }
}

#[derive(Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Edit {
    start: usize,
    end: usize,
    original: String,
    replacement: String,
}

#[derive(Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Event {
    version: u32,
    id: String,
    timestamp_ms: i64,
    offset_unit: String,
    edits: Vec<Edit>,
    #[serde(skip_serializing_if = "Option::is_none")]
    original: Option<String>,
}

fn invalid() -> io::Error {
    io::Error::new(io::ErrorKind::InvalidData, "invalid_watch_event")
}

impl Event {
    fn new(
        text: &str,
        result: &RewriteResult,
        strategy: &ReplacementMode,
        id: &str,
        view: View,
    ) -> io::Result<Self> {
        let edits = result
            .spans
            .iter()
            .map(|span| {
                Ok(Edit {
                    start: span.start,
                    end: span.end,
                    original: text.get(span.start..span.end).ok_or_else(invalid)?.into(),
                    replacement: strategy.marker().unwrap_or("").into(),
                })
            })
            .collect::<io::Result<Vec<_>>>()?;
        let event = Self {
            version: 1,
            id: id.into(),
            timestamp_ms: now_ms(),
            offset_unit: "utf8".into(),
            edits,
            original: (view == View::PromptDiff).then(|| text.to_owned()),
        };
        event.validate(view)?;
        Ok(event)
    }

    fn validate(&self, view: View) -> io::Result<()> {
        if self.version != 1
            || self.offset_unit != "utf8"
            || self.id.len() != 64
            || !self.id.bytes().all(|c| c.is_ascii_hexdigit())
            || self.timestamp_ms < 0
            || self.edits.is_empty()
            || self.edits.len() > MAX_TEXT_BYTES
            || self.original.is_some() != (view == View::PromptDiff)
            || self
                .original
                .as_ref()
                .is_some_and(|s| s.len() > MAX_TEXT_BYTES)
        {
            return Err(invalid());
        }
        let mut end = 0;
        for edit in &self.edits {
            if edit.start < end
                || edit.start >= edit.end
                || edit.end > MAX_TEXT_BYTES
                || edit.original.len() != edit.end - edit.start
                || edit.replacement.chars().count() > 80
                || edit.replacement.chars().any(char::is_control)
                || self.original.as_ref().is_some_and(|text| {
                    text.get(edit.start..edit.end) != Some(edit.original.as_str())
                })
            {
                return Err(invalid());
            }
            end = edit.end;
        }
        Ok(())
    }
}

fn retry<T>(deadline: Instant, mut operation: impl FnMut() -> io::Result<T>) -> io::Result<T> {
    loop {
        if Instant::now() >= deadline {
            return Err(io::ErrorKind::TimedOut.into());
        }
        match operation() {
            Err(e) if e.kind() == io::ErrorKind::Interrupted => (),
            Err(e) if e.kind() == io::ErrorKind::WouldBlock => {
                std::thread::sleep(Duration::from_millis(1));
            }
            result => return result,
        }
    }
}

fn read_exact(stream: &mut impl Read, mut bytes: &mut [u8], deadline: Instant) -> io::Result<()> {
    while !bytes.is_empty() {
        let count = retry(deadline, || stream.read(bytes))?;
        if count == 0 {
            return Err(io::ErrorKind::UnexpectedEof.into());
        }
        bytes = &mut bytes[count..];
    }
    Ok(())
}

fn write_all(stream: &mut impl Write, mut bytes: &[u8], deadline: Instant) -> io::Result<()> {
    while !bytes.is_empty() {
        let count = retry(deadline, || stream.write(bytes))?;
        if count == 0 {
            return Err(io::ErrorKind::WriteZero.into());
        }
        bytes = &bytes[count..];
    }
    Ok(())
}

/// Watching never changes the rewrite result, including when a viewer stalls.
pub fn publish(text: &str, result: &RewriteResult, strategy: &ReplacementMode, id: &str) {
    if result.spans.is_empty() {
        return;
    }
    let _ = (|| -> io::Result<()> {
        let deadline = Instant::now() + PUBLISH_TIMEOUT;
        let profile = profile_dir().map_err(|_| invalid())?;
        let mut stream = platform::connect(&profile)?;
        send(&mut stream, text, result, strategy, id, deadline)
    })();
}

fn send(
    stream: &mut (impl Read + Write),
    text: &str,
    result: &RewriteResult,
    strategy: &ReplacementMode,
    id: &str,
    deadline: Instant,
) -> io::Result<()> {
    let mut hello = [0; 2];
    read_exact(stream, &mut hello, deadline)?;
    if hello[0] != 1 {
        return Err(invalid());
    }
    let view = View::from_byte(hello[1])?;
    let event = Event::new(text, result, strategy, id, view)?;
    let bytes = serde_json::to_vec(&event).map_err(|_| invalid())?;
    if bytes.len() > MAX_FRAME_BYTES {
        return Err(invalid());
    }
    write_all(stream, &(bytes.len() as u32).to_be_bytes(), deadline)?;
    write_all(stream, &bytes, deadline)?;
    // Keep the pipe alive until the viewer has consumed the complete frame.
    let mut ack = [0];
    read_exact(stream, &mut ack, deadline)
}

#[cfg(any(feature = "watch", test))]
fn receive(stream: &mut (impl Read + Write), view: View) -> io::Result<Event> {
    let deadline = Instant::now() + PUBLISH_TIMEOUT;
    write_all(stream, &[1, view as u8], deadline)?;
    let mut length = [0; 4];
    read_exact(stream, &mut length, deadline)?;
    let length = u32::from_be_bytes(length) as usize;
    if length > MAX_FRAME_BYTES {
        return Err(invalid());
    }
    let mut bytes = vec![0; length];
    read_exact(stream, &mut bytes, deadline)?;
    let event: Event = serde_json::from_slice(&bytes).map_err(|_| invalid())?;
    event.validate(view)?;
    write_all(stream, &[1], deadline)?;
    Ok(event)
}

#[cfg(any(feature = "watch", test))]
fn escape(text: &str, multiline: bool) -> String {
    text.chars()
        .flat_map(|c| {
            if (c.is_control() && !(multiline && c == '\n'))
                || matches!(c, '\u{061c}' | '\u{200e}' | '\u{200f}' | '\u{202a}'..='\u{202e}' | '\u{2066}'..='\u{2069}')
            {
                c.escape_default().collect::<Vec<_>>()
            } else {
                vec![c]
            }
        })
        .collect()
}

#[cfg(any(feature = "watch", test))]
fn render(event: &Event, view: View, color: bool) -> String {
    let mut output = format!(
        "[{} {}] processed ({} replacements)\n",
        event.timestamp_ms,
        &event.id[..8],
        event.edits.len()
    );
    let removed = |text: &str| {
        if color {
            format!("\x1b[31m{text}\x1b[0m")
        } else {
            text.to_owned()
        }
    };
    let added = |text: &str| {
        if color {
            format!("\x1b[32m{text}\x1b[0m")
        } else {
            text.to_owned()
        }
    };
    let mut end = 0;
    for edit in &event.edits {
        let original = escape(&edit.original, view == View::PromptDiff);
        let replacement = escape(&edit.replacement, false);
        match view {
            View::Spans => output.push_str(&format!("  {original}\n")),
            View::SpanDiff => {
                output.push_str(&removed(&format!("- {original}")));
                output.push('\n');
                output.push_str(&added(&format!("+ {replacement}")));
                output.push('\n');
            }
            View::PromptDiff => {
                let text = event.original.as_deref().unwrap_or_default();
                output.push_str(&escape(&text[end..edit.start], true));
                output.push_str(&removed(&format!("[-{original}-]")));
                output.push_str(&added(&format!("{{+{replacement}+}}")));
            }
        }
        end = edit.end;
    }
    if let Some(text) = &event.original {
        output.push_str(&escape(&text[end..], true));
        output.push('\n');
    }
    output.push('\n');
    output
}

#[cfg(feature = "watch")]
pub fn watch(args: &[String]) -> ReviewResult<()> {
    use std::{
        io::IsTerminal,
        sync::{
            Arc,
            atomic::{AtomicBool, Ordering},
            mpsc,
        },
    };
    if matches!(args, [arg] if arg == "--help") {
        println!(
            "savior watch [--view spans|span-diff|prompt-diff]\n\nlive replacements from this Claude profile; ctrl+c stops viewing; no history is retained"
        );
        return Ok(());
    }
    let view = match args {
        [] => View::Spans,
        [flag, value] if flag == "--view" => match value.as_str() {
            "spans" => View::Spans,
            "span-diff" => View::SpanDiff,
            "prompt-diff" => View::PromptDiff,
            _ => return Err("invalid_watch_view".into()),
        },
        _ => return Err("invalid_command".into()),
    };
    let profile = profile_dir()?;
    let mut listener = platform::Listener::bind(&profile)?;
    let stopping = Arc::new(AtomicBool::new(false));
    let signal = stopping.clone();
    ctrlc::set_handler(move || signal.store(true, Ordering::Release))
        .map_err(|_| "watch_unavailable")?;
    let color = io::stdout().is_terminal() && std::env::var_os("NO_COLOR").is_none();
    // Bound retained display text even if a downstream pipe stops reading.
    let (sender, receiver) = mpsc::sync_channel::<String>(4);
    let output_stopping = stopping.clone();
    std::thread::spawn(move || {
        let mut stdout = io::stdout().lock();
        for text in receiver {
            if output_stopping.load(Ordering::Acquire) {
                break;
            }
            if stdout
                .write_all(text.as_bytes())
                .and_then(|()| stdout.flush())
                .is_err()
            {
                output_stopping.store(true, Ordering::Release);
                break;
            }
        }
    });
    eprintln!("savior: watching live replacements for this profile; ctrl+c stops viewing");
    while !stopping.load(Ordering::Acquire) {
        match listener.accept() {
            Ok(Some(mut stream)) => match receive(&mut stream, view) {
                Ok(event) => {
                    if sender.try_send(render(&event, view, color)).is_err() {
                        eprintln!("savior: watch_event_dropped");
                    }
                }
                Err(_) => eprintln!("savior: invalid_watch_event"),
            },
            Ok(None) => std::thread::sleep(Duration::from_millis(2)),
            Err(_) => return Err("watch_unavailable".into()),
        }
    }
    Ok(())
}
