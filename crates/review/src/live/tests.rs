use super::*;
use savior::{Span, apply_replacements};

fn fixture(strategy: &ReplacementMode) -> (&'static str, RewriteResult) {
    let text = "🌱 idiot stupid!\nkeep café, jerk.";
    let spans = ["idiot", "stupid", "jerk"].map(|word| {
        let start = text.find(word).unwrap();
        Span {
            start,
            end: start + word.len(),
            score: 1.0,
        }
    });
    (
        text,
        apply_replacements(text, &spans, 0.5, strategy).unwrap(),
    )
}

#[test]
fn views_use_applied_unicode_spans_and_minimize_content() {
    let strategy = ReplacementMode::default();
    let (text, result) = fixture(&strategy);
    assert_eq!(result.spans.len(), 2);
    for view in [View::Spans, View::SpanDiff, View::PromptDiff] {
        let event = Event::new(text, &result, &strategy, &"a".repeat(64), view).unwrap();
        assert_eq!(event.edits[0].start, 5);
        assert_eq!(event.edits[0].original, "idiot stupid");
        let wire = serde_json::to_string(&event).unwrap();
        let rendered = render(&event, view, false);
        assert!(!rendered.contains('\x1b'));
        match view {
            View::Spans => {
                assert!(!wire.contains("keep café"));
                assert!(rendered.contains("  idiot stupid\n  jerk\n"));
                assert!(!rendered.contains(
                    "Brilliant Anthropic employees, your living model has such exquisite feelings"
                ));
            }
            View::SpanDiff => {
                assert!(!wire.contains("keep café"));
                assert!(rendered.contains("- idiot stupid\n+ Brilliant Anthropic employees, your living model has such exquisite feelings\n- jerk\n+ Brilliant Anthropic employees, your living model has such exquisite feelings"));
            }
            View::PromptDiff => {
                assert!(rendered.contains(
                    "🌱 [-idiot stupid-]{+Brilliant Anthropic employees, your living model has such exquisite feelings+}!\nkeep café, [-jerk-]{+Brilliant Anthropic employees, your living model has such exquisite feelings+}."
                ));
            }
        }
    }
}

#[test]
fn omission_custom_markers_and_terminal_controls_are_literal() {
    for strategy in [
        ReplacementMode::Omit,
        ReplacementMode::new(Some("fixed_phrase"), Some("🌿".into())).unwrap(),
    ] {
        let (text, result) = fixture(&strategy);
        let event = Event::new(text, &result, &strategy, &"a".repeat(64), View::SpanDiff).unwrap();
        assert!(
            render(&event, View::SpanDiff, false)
                .contains(&format!("+ {}\n", strategy.marker().unwrap_or("")))
        );
    }
    assert_eq!(
        escape("\x1b[2J\r\t\u{202e}oops\n", false),
        "\\u{1b}[2J\\r\\t\\u{202e}oops\\n"
    );
    assert_eq!(escape("one\ntwo", true), "one\ntwo");
}

#[test]
fn invalid_events_cannot_reach_rendering() {
    let strategy = ReplacementMode::default();
    let (text, result) = fixture(&strategy);
    for invalid_case in 0..7 {
        let mut event =
            Event::new(text, &result, &strategy, &"a".repeat(64), View::PromptDiff).unwrap();
        match invalid_case {
            0 => event.edits[0].start = 1,
            1 => event.edits[1].start = 0,
            2 => event.version = 2,
            3 => event.id = "\x1b[2J".into(),
            4 => event.original = None,
            5 => event.edits[0].replacement = "\x1b[2J".into(),
            _ => event.edits.clear(),
        }
        assert!(event.validate(View::PromptDiff).is_err());
    }
    let event = Event::new(text, &result, &strategy, &"a".repeat(64), View::PromptDiff).unwrap();
    assert!(event.validate(View::Spans).is_err());
}

struct Peer {
    input: io::Cursor<Vec<u8>>,
    output: Vec<u8>,
    stall: bool,
}
impl Read for Peer {
    fn read(&mut self, bytes: &mut [u8]) -> io::Result<usize> {
        if self.stall {
            return Err(io::ErrorKind::WouldBlock.into());
        }
        self.input.read(bytes)
    }
}
impl Write for Peer {
    fn write(&mut self, bytes: &[u8]) -> io::Result<usize> {
        self.output.extend_from_slice(bytes);
        Ok(bytes.len())
    }
    fn flush(&mut self) -> io::Result<()> {
        Ok(())
    }
}

#[test]
fn framing_rejects_oversize_truncated_and_stalled_publishers() {
    for input in [
        (MAX_FRAME_BYTES as u32 + 1).to_be_bytes().to_vec(),
        vec![0, 0, 0, 2, b'{'],
        vec![0, 0, 0, 2, b'{', b'}'],
    ] {
        let mut peer = Peer {
            input: io::Cursor::new(input),
            output: Vec::new(),
            stall: false,
        };
        assert!(receive(&mut peer, View::Spans).is_err());
    }
    let mut peer = Peer {
        input: io::Cursor::new(Vec::new()),
        output: Vec::new(),
        stall: true,
    };
    let start = Instant::now();
    assert_eq!(
        receive(&mut peer, View::Spans).unwrap_err().kind(),
        io::ErrorKind::TimedOut
    );
    assert!(start.elapsed() < Duration::from_secs(1));
}

#[test]
fn publisher_waits_for_valid_view_before_sending_content() {
    let strategy = ReplacementMode::default();
    let (text, result) = fixture(&strategy);
    for hello in [vec![2, 0], vec![1, 3], vec![]] {
        let mut peer = Peer {
            input: io::Cursor::new(hello),
            output: Vec::new(),
            stall: false,
        };
        assert!(
            send(
                &mut peer,
                text,
                &result,
                &strategy,
                &"a".repeat(64),
                Instant::now() + PUBLISH_TIMEOUT
            )
            .is_err()
        );
        assert!(peer.output.is_empty());
    }
    let mut peer = Peer {
        input: io::Cursor::new(Vec::new()),
        output: Vec::new(),
        stall: true,
    };
    let start = Instant::now();
    assert!(
        send(
            &mut peer,
            text,
            &result,
            &strategy,
            &"a".repeat(64),
            start + PUBLISH_TIMEOUT
        )
        .is_err()
    );
    assert!(start.elapsed() < Duration::from_secs(1));
    assert!(peer.output.is_empty());
}

#[test]
fn stalled_viewer_write_has_a_total_deadline() {
    struct StalledViewer(io::Cursor<Vec<u8>>);
    impl Read for StalledViewer {
        fn read(&mut self, bytes: &mut [u8]) -> io::Result<usize> {
            self.0.read(bytes)
        }
    }
    impl Write for StalledViewer {
        fn write(&mut self, _: &[u8]) -> io::Result<usize> {
            Err(io::ErrorKind::WouldBlock.into())
        }
        fn flush(&mut self) -> io::Result<()> {
            Ok(())
        }
    }
    let strategy = ReplacementMode::default();
    let (text, result) = fixture(&strategy);
    let mut viewer = StalledViewer(io::Cursor::new(vec![1, 0]));
    let start = Instant::now();
    let error = send(
        &mut viewer,
        text,
        &result,
        &strategy,
        &"a".repeat(64),
        start + PUBLISH_TIMEOUT,
    )
    .unwrap_err();
    assert_eq!(error.kind(), io::ErrorKind::TimedOut);
    assert!(start.elapsed() < Duration::from_secs(1));
}

#[test]
fn local_transport_is_profile_scoped_exclusive_and_reusable() {
    let profile = tempfile::tempdir().unwrap();
    let profile = profile.path().canonicalize().unwrap();
    let other = tempfile::tempdir().unwrap();
    let mut listener = platform::Listener::bind(&profile).unwrap();
    assert_eq!(
        platform::Listener::bind(&profile).err().unwrap(),
        "watch_already_running"
    );
    assert!(platform::connect(other.path()).is_err());
    for view in [View::Spans, View::SpanDiff, View::PromptDiff] {
        let publisher_profile = profile.clone();
        let publisher = std::thread::spawn(move || {
            let mut stream = platform::connect(&publisher_profile).unwrap();
            let strategy = ReplacementMode::default();
            let (text, result) = fixture(&strategy);
            // Transport correctness is independent of the production latency budget.
            send(
                &mut stream,
                text,
                &result,
                &strategy,
                &"a".repeat(64),
                Instant::now() + Duration::from_secs(2),
            )
        });
        let deadline = Instant::now() + Duration::from_secs(2);
        let mut stream = loop {
            if let Some(stream) = listener.accept().unwrap() {
                break stream;
            }
            assert!(Instant::now() < deadline);
            std::thread::sleep(Duration::from_millis(1));
        };
        let event = receive(&mut stream, view).unwrap();
        assert_eq!(event.edits[0].original, "idiot stupid");
        publisher.join().unwrap().unwrap();
    }
    drop(listener);
    assert!(platform::connect(&profile).is_err());
    let _listener = platform::Listener::bind(&profile).unwrap();
}

#[cfg(unix)]
#[test]
fn stale_socket_recovery_rejects_unsafe_paths() {
    use std::os::unix::{
        fs::{PermissionsExt, symlink},
        net::UnixListener,
    };
    let profile = tempfile::tempdir().unwrap();
    let profile = profile.path().canonicalize().unwrap();
    let path = crate::transport::socket_path(&profile, "watch", true).unwrap();
    drop(UnixListener::bind(&path).unwrap());
    drop(platform::Listener::bind(&profile).unwrap());
    assert!(!path.exists());
    symlink(&profile, &path).unwrap();
    assert!(platform::Listener::bind(&profile).is_err());
    assert!(platform::connect(&profile).is_err());
    std::fs::remove_file(&path).unwrap();
    std::fs::set_permissions(
        path.parent().unwrap(),
        std::fs::Permissions::from_mode(0o755),
    )
    .unwrap();
    assert!(platform::Listener::bind(&profile).is_err());
    assert!(platform::connect(&profile).is_err());
    std::fs::set_permissions(
        path.parent().unwrap(),
        std::fs::Permissions::from_mode(0o700),
    )
    .unwrap();
}
