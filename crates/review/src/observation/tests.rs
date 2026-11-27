use super::{session::Session, *};

fn event(id: &str, stage: &str, data: Value) -> Observation {
    Observation {
        version: 1,
        id: id.into(),
        timestamp_ms: now_ms(),
        stage: stage.into(),
        data,
    }
}

fn completed(id: &str) -> Observation {
    let original = "🌱 idiot, keep the test";
    let spans = vec![savior::Span {
        start: 5,
        end: 10,
        score: 0.9,
    }];
    let strategy = savior::ReplacementMode::new(Some("fixed_phrase"), Some("🌼".into())).unwrap();
    let result = savior::apply_replacements(original, &spans, 0.5, &strategy).unwrap();
    event(
        id,
        "completed",
        json!({"trace":Trace {
            schema:1, offset_unit:"utf8".into(), id:id.into(), timestamp_ms:now_ms(),
            release:env!("CARGO_PKG_VERSION").into(), release_manifest_sha256:None, model_fingerprint:None,
            policy:"offensive_spans".into(), detector:"lexical".into(), elapsed_ms:4.0,
            outcome:"rewritten".into(), disposition:"processed".into(), code:None,
            decision:Some(crate::traces::Decision {detected:spans,applied:result.spans,threshold:0.5,strategy:"fixed_phrase".into(),marker:Some("🌼".into())}),
            original:Some(original.into()),output:Some(result.text),
        }}),
    )
}

#[test]
fn stages_deduplicate_and_delivery_is_independent_of_processing() {
    let mut session = Session::default();
    let id = "a".repeat(64);
    session
        .apply(event(
            &id,
            "processing",
            json!({"original":"🌱 idiot, keep the test"}),
        ))
        .unwrap();
    session
        .apply(event(&id, "detected", json!({"detected_count":1})))
        .unwrap();
    session
        .apply(event(&id, "replaced", json!({"replacement_count":1})))
        .unwrap();
    let terminal = completed(&id);
    session.apply(terminal.clone()).unwrap();
    let sequence = session.sequence;
    session.apply(terminal).unwrap();
    assert_eq!(session.sequence, sequence);
    session
        .apply(event(&id, "delivery", json!({"disposition":"cancelled"})))
        .unwrap();
    let snapshot = session.snapshot(0);
    assert_eq!(snapshot["stats"]["outcomes"]["rewritten"], 0);
    assert_eq!(snapshot["stats"]["outcomes"]["cancelled"], 1);
    assert_eq!(snapshot["stats"]["replacements"], 1);
    assert_eq!(snapshot["stats"]["mean_elapsed_ms"], 4.0);
    assert_eq!(snapshot["rows"][0]["output"], "🌱 🌼, keep the test");
    assert_eq!(snapshot["rows"][0]["stages"].as_array().unwrap().len(), 5);
    assert_eq!(session.snapshot(session.sequence)["rows"], json!([]));
}

#[test]
fn early_cancellation_cannot_be_overwritten_by_late_worker_completion() {
    let mut session = Session::default();
    let id = "b".repeat(64);
    session
        .apply(event(&id, "delivery", json!({"disposition":"cancelled"})))
        .unwrap();
    session.apply(completed(&id)).unwrap();
    let snapshot = session.snapshot(0);
    assert_eq!(snapshot["rows"][0]["outcome"], "cancelled");
    assert_eq!(snapshot["rows"][0]["disposition"], "cancelled");
    assert_eq!(snapshot["stats"]["outcomes"]["cancelled"], 1);
}

#[test]
fn malformed_events_do_not_mutate_the_session() {
    let mut session = Session::default();
    let mut invalid = completed(&"c".repeat(64));
    invalid.data["trace"]["output"] = "lost instructions".into();
    assert!(session.apply(invalid).is_err());
    assert!(
        session
            .apply(event(
                "invalid",
                "processing",
                json!({"original":"synthetic"})
            ))
            .is_err()
    );
    assert!(
        session
            .apply(event(&"c".repeat(64), "unknown", json!({})))
            .is_err()
    );
    assert_eq!(session.sequence, 0);
    assert_eq!(session.snapshot(0)["rows"], json!([]));
}

#[test]
fn activity_is_bounded_and_completed_rows_evict_before_active_work() {
    let mut session = Session::default();
    let active = "d".repeat(64);
    session
        .apply(event(
            &active,
            "processing",
            json!({"original":"still processing"}),
        ))
        .unwrap();
    for serial in 0..1_001 {
        session.apply(completed(&format!("{serial:064x}"))).unwrap();
    }
    let snapshot = session.snapshot(0);
    assert_eq!(snapshot["rows"].as_array().unwrap().len(), 1_000);
    assert_eq!(snapshot["dropped"], 2);
    assert_eq!(snapshot["stats"]["outcomes"]["rewritten"], 1_001);
    assert!(
        snapshot["rows"]
            .as_array()
            .unwrap()
            .iter()
            .any(|r| r["id"] == active)
    );
}

#[test]
fn large_live_inputs_respect_the_byte_budget_before_the_row_limit() {
    let mut session = Session::default();
    for serial in 0..550 {
        session
            .apply(event(
                &format!("{serial:064x}"),
                "processing",
                json!({"original":"x".repeat(savior::MAX_TEXT_BYTES)}),
            ))
            .unwrap();
    }
    let snapshot = session.snapshot(0);
    let rows = snapshot["rows"].as_array().unwrap();
    assert!(rows.len() < 550);
    assert!(snapshot["dropped"].as_u64().unwrap() > 0);
    assert!(rows.iter().map(|r| r.to_string().len()).sum::<usize>() <= 32 * 1024 * 1024); // 32 MiB
}

#[cfg(unix)]
#[test]
fn a_stalled_publisher_cannot_hold_the_receiver_open() {
    let (mut server, _client) = std::os::unix::net::UnixStream::pair().unwrap();
    server.set_nonblocking(true).unwrap();
    let start = Instant::now();
    assert_eq!(receive(&mut server).unwrap_err(), "observation_dropped");
    assert!(start.elapsed() < Duration::from_secs(1));
}

#[cfg(unix)]
#[test]
fn framing_rejects_oversize_truncated_and_unknown_events() {
    use std::io::{Read, Write};
    for bytes in [
        u32::MAX.to_be_bytes().to_vec(),
        vec![0, 0, 0, 2, b'{'],
        vec![0, 0, 0, 2, b'{', b'}'],
    ] {
        let (mut server, mut client) = std::os::unix::net::UnixStream::pair().unwrap();
        server.set_nonblocking(true).unwrap();
        let worker = std::thread::spawn(move || receive(&mut server));
        let mut hello = [0];
        client.read_exact(&mut hello).unwrap();
        assert_eq!(hello, [1]);
        client.write_all(&bytes).unwrap();
        drop(client);
        assert!(worker.join().unwrap().is_err());
    }
}

#[cfg(unix)]
#[test]
fn inspector_and_terminal_endpoints_can_coexist() {
    let root = tempfile::tempdir().unwrap();
    let profile = root.path().canonicalize().unwrap();
    let mut inspector = live::platform::Listener::bind_named(&profile, "view").unwrap();
    let _watch = live::platform::Listener::bind_named(&profile, "watch").unwrap();
    assert!(live::platform::Listener::bind_named(&profile, "view").is_err());
    let mut client = live::platform::connect_named(&profile, "view").unwrap();
    let (send, receive_result) = std::sync::mpsc::channel();
    let worker = std::thread::spawn(move || {
        let mut stream = inspector.accept().unwrap().unwrap();
        send.send(receive(&mut stream)).unwrap();
    });
    let deadline = Instant::now() + Duration::from_secs(1);
    let mut hello = [0];
    live::read_exact(&mut client, &mut hello, deadline).unwrap();
    let message = serde_json::to_vec(&event(
        &"e".repeat(64),
        "processing",
        json!({"original":"synthetic"}),
    ))
    .unwrap();
    live::write_all(&mut client, &(message.len() as u32).to_be_bytes(), deadline).unwrap();
    live::write_all(&mut client, &message, deadline).unwrap();
    assert_eq!(receive_result.recv().unwrap().unwrap().stage, "processing");
    worker.join().unwrap();
}
