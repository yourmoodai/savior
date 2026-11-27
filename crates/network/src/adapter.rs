use crate::{Error, Result, policy::Format};
use savior::{Detector, ReplacementMode, rewrite};
use serde::de::{self, Deserialize, Deserializer, MapAccess, SeqAccess, Visitor};
use serde_json::value::RawValue;
use std::collections::{BTreeMap, BTreeSet};
use std::fmt;

pub struct Transformed {
    pub body: Vec<u8>,
    pub replacement_count: usize,
}

/// Only the selected JSON string token is serialized again; all other bytes survive exactly.
pub fn transform(
    body: &[u8],
    format: Format,
    detector: &mut impl Detector,
    threshold: f32,
    replacement: &ReplacementMode,
) -> Result<Transformed> {
    let source = std::str::from_utf8(body).map_err(|_| Error::Request)?;
    // Duplicate keys can make the gateway and provider interpret different prompts.
    let _: UniqueJson = serde_json::from_str(source).map_err(|_| Error::Request)?;
    let root: BTreeMap<String, &RawValue> =
        serde_json::from_str(source).map_err(|_| Error::Request)?;
    let prompt = match format {
        Format::OpenaiResponses => openai_prompt(&root)?,
        Format::AnthropicMessages => anthropic_prompt(&root)?,
    };
    let text: String = serde_json::from_str(prompt.get()).map_err(|_| Error::Ambiguous)?;
    let result = rewrite(detector, &text, threshold, replacement).map_err(|_| Error::Processing)?;
    if result.replacement_count == 0 {
        return Ok(Transformed {
            body: body.to_vec(),
            replacement_count: 0,
        });
    }
    let start = prompt.get().as_ptr() as usize - source.as_ptr() as usize;
    let end = start + prompt.get().len();
    let encoded = serde_json::to_vec(&result.text).map_err(|_| Error::Processing)?;
    let mut output = Vec::with_capacity(body.len() + encoded.len());
    output.extend_from_slice(&body[..start]);
    output.extend_from_slice(&encoded);
    output.extend_from_slice(&body[end..]);
    Ok(Transformed {
        body: output,
        replacement_count: result.replacement_count,
    })
}

fn object(raw: &RawValue) -> Result<BTreeMap<String, &RawValue>> {
    serde_json::from_str(raw.get()).map_err(|_| Error::Ambiguous)
}

fn is_string(raw: &RawValue, expected: &str) -> bool {
    serde_json::from_str::<String>(raw.get()).is_ok_and(|value| value == expected)
}

fn openai_prompt<'a>(root: &BTreeMap<String, &'a RawValue>) -> Result<&'a RawValue> {
    let input = root.get("input").ok_or(Error::Ambiguous)?;
    if input.get().starts_with('"') {
        return Ok(input);
    }
    let items: Vec<&RawValue> = serde_json::from_str(input.get()).map_err(|_| Error::Ambiguous)?;
    let last = object(items.last().ok_or(Error::Ambiguous)?)?;
    if !last
        .get("role")
        .is_some_and(|value| is_string(value, "user"))
        || last
            .get("type")
            .is_some_and(|value| !is_string(value, "message"))
        || last
            .keys()
            .any(|key| !["role", "type", "content", "id", "status"].contains(&key.as_str()))
    {
        return Err(Error::Ambiguous);
    }
    text_content(last.get("content").ok_or(Error::Ambiguous)?, "input_text")
}

fn anthropic_prompt<'a>(root: &BTreeMap<String, &'a RawValue>) -> Result<&'a RawValue> {
    let messages = root.get("messages").ok_or(Error::Ambiguous)?;
    let items: Vec<&RawValue> =
        serde_json::from_str(messages.get()).map_err(|_| Error::Ambiguous)?;
    let last = object(items.last().ok_or(Error::Ambiguous)?)?;
    if !last
        .get("role")
        .is_some_and(|value| is_string(value, "user"))
        || last
            .keys()
            .any(|key| !["role", "content"].contains(&key.as_str()))
    {
        return Err(Error::Ambiguous);
    }
    text_content(last.get("content").ok_or(Error::Ambiguous)?, "text")
}

fn text_content<'a>(raw: &'a RawValue, text_type: &str) -> Result<&'a RawValue> {
    if raw.get().starts_with('"') {
        return Ok(raw);
    }
    let blocks: Vec<&RawValue> = serde_json::from_str(raw.get()).map_err(|_| Error::Ambiguous)?;
    // Multiple blocks can mix a prompt, pasted files, tool results, and host context.
    // Until a client-specific origin contract qualifies them, refuse that submission.
    if blocks.len() != 1 {
        return Err(Error::Ambiguous);
    }
    let block = object(blocks[0])?;
    if !block
        .get("type")
        .is_some_and(|value| is_string(value, text_type))
        || block
            .keys()
            .any(|key| !["type", "text", "cache_control"].contains(&key.as_str()))
    {
        return Err(Error::Ambiguous);
    }
    block.get("text").copied().ok_or(Error::Ambiguous)
}

struct UniqueJson;

impl<'de> Deserialize<'de> for UniqueJson {
    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> std::result::Result<Self, D::Error> {
        struct UniqueVisitor;
        impl<'de> Visitor<'de> for UniqueVisitor {
            type Value = UniqueJson;
            fn expecting(&self, formatter: &mut fmt::Formatter) -> fmt::Result {
                formatter.write_str("json without duplicate keys")
            }
            fn visit_map<M: MapAccess<'de>>(
                self,
                mut map: M,
            ) -> std::result::Result<Self::Value, M::Error> {
                let mut keys = BTreeSet::new();
                while let Some(key) = map.next_key::<String>()? {
                    if !keys.insert(key) {
                        return Err(de::Error::custom("duplicate_key"));
                    }
                    map.next_value::<UniqueJson>()?;
                }
                Ok(UniqueJson)
            }
            fn visit_seq<S: SeqAccess<'de>>(
                self,
                mut seq: S,
            ) -> std::result::Result<Self::Value, S::Error> {
                while seq.next_element::<UniqueJson>()?.is_some() {}
                Ok(UniqueJson)
            }
            fn visit_bool<E: de::Error>(self, _: bool) -> std::result::Result<Self::Value, E> {
                Ok(UniqueJson)
            }
            fn visit_i64<E: de::Error>(self, _: i64) -> std::result::Result<Self::Value, E> {
                Ok(UniqueJson)
            }
            fn visit_u64<E: de::Error>(self, _: u64) -> std::result::Result<Self::Value, E> {
                Ok(UniqueJson)
            }
            fn visit_f64<E: de::Error>(self, _: f64) -> std::result::Result<Self::Value, E> {
                Ok(UniqueJson)
            }
            fn visit_str<E: de::Error>(self, _: &str) -> std::result::Result<Self::Value, E> {
                Ok(UniqueJson)
            }
            fn visit_unit<E: de::Error>(self) -> std::result::Result<Self::Value, E> {
                Ok(UniqueJson)
            }
        }
        deserializer.deserialize_any(UniqueVisitor)
    }
}
