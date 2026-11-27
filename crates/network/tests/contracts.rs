use savior::{Detector, ReplacementMode, Span};
use savior_network::{
    Error, adapter,
    certificate::Authority,
    policy::{Format, Policy},
};

struct SyntheticDetector;
impl Detector for SyntheticDetector {
    fn detect(&mut self, text: &str) -> savior::Result<Vec<Span>> {
        Ok(text
            .match_indices("idiot")
            .map(|(start, token)| Span {
                start,
                end: start + token.len(),
                score: 1.0,
            })
            .collect())
    }
}

fn transform(body: &[u8], format: Format) -> Result<Vec<u8>, Error> {
    adapter::transform(
        body,
        format,
        &mut SyntheticDetector,
        0.5,
        &ReplacementMode::new(Some("fixed_phrase"), Some("colleague".into())).unwrap(),
    )
    .map(|result| result.body)
}

#[test]
fn anthropic_user_prompt_preserves_system_history_and_caching_metadata() {
    let source = br#"{ "system":"idiot", "messages":[{"role":"assistant","content":"idiot"},{"role":"user","content":[{"type":"text","text":"Hello idiot.","cache_control":{"type":"ephemeral"}}]}] }"#;
    let expected = String::from_utf8(source.to_vec())
        .unwrap()
        .replace("Hello idiot", "Hello colleague");
    assert_eq!(
        transform(source, Format::AnthropicMessages).unwrap(),
        expected.as_bytes()
    );
}

#[test]
fn unchanged_json_retains_escaping_whitespace_and_numeric_spelling() {
    let source = br#"{ "input":"preserve caf\u00e9", "metadata":{"n":1.0000} }"#;
    assert_eq!(transform(source, Format::OpenaiResponses).unwrap(), source);
}

#[test]
fn unicode_and_json_escapes_do_not_shift_the_selected_token() {
    let source = "{\"instructions\":\"🙂\", \"input\":\"🙂 idiot, preserve café.\"}";
    assert_eq!(
        transform(source.as_bytes(), Format::OpenaiResponses).unwrap(),
        source.replace("idiot", "colleague").as_bytes()
    );
}

#[test]
fn tool_results_and_prefilled_assistant_responses_are_not_user_prompts() {
    for source in [
        br#"{"messages":[{"role":"assistant","content":"idiot"}]}"#.as_slice(),
        br#"{"messages":[{"role":"user","content":[{"type":"tool_result","content":"idiot","tool_use_id":"fixture"}]}]}"#,
        br#"{"messages":[{"role":"user","content":[{"type":"text","text":"idiot"},{"type":"image","source":{}}]}]}"#,
    ] { assert_eq!(transform(source, Format::AnthropicMessages), Err(Error::Ambiguous)); }
    assert_eq!(
        transform(
            br#"{"input":[{"type":"function_call_output","output":"idiot"}]}"#,
            Format::OpenaiResponses
        ),
        Err(Error::Ambiguous)
    );
}

#[test]
fn escaped_duplicate_keys_and_trailing_json_are_rejected() {
    for source in [
        br#"{"input":"idiot","in\u0070ut":"safe"}"#.as_slice(),
        br#"{"input":"idiot", "metadata":{"x":1,"x":2}}"#,
        br#"{"input":"idiot"} {"input":"safe"}"#,
    ] {
        assert_eq!(
            transform(source, Format::OpenaiResponses),
            Err(Error::Request)
        );
    }
}

#[test]
fn network_policy_rejects_remote_listeners_wildcards_and_ambiguous_paths() {
    let fixture = include_str!("../../../fixtures/network/development-policy.json");
    let policy: Policy = serde_json::from_str(fixture).unwrap();
    policy.validate().unwrap();
    for changed in [
        fixture.replace("127.0.0.1", "0.0.0.0"),
        fixture.replace("api.openai.com", "*.openai.com"),
        fixture.replace("/v1/responses", "/v1/%72esponses"),
        fixture.replace("8388608", "8388609"),
    ] {
        let policy: Policy = serde_json::from_str(&changed).unwrap();
        assert_eq!(policy.validate(), Err(Error::Policy));
    }
    assert!(
        policy
            .destination("api.openai.com.evil.example", 443)
            .is_err()
    );
    assert!(policy.destination("api.openai.com", 80).is_err());
}

#[test]
fn certificate_authorities_are_unique_and_reloadable() {
    let (authority, key) = Authority::generate().unwrap();
    let (other, other_key) = Authority::generate().unwrap();
    assert_ne!(authority.certificate(), other.certificate());
    let loaded = Authority::from_pem(authority.pem(), &key).unwrap();
    assert_eq!(loaded.certificate(), authority.certificate());
    loaded.server_config("localhost").unwrap();
    assert!(Authority::from_pem(authority.pem(), "invalid").is_err());
    assert!(Authority::from_pem(authority.pem(), &other_key).is_err());
}
