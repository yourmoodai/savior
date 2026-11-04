//! Bounded response framing shared by both worker transports.
use serde_json::{Value, json};

#[cfg(unix)]
pub fn socket_path(
    profile: &std::path::Path,
    name: &str,
    create: bool,
) -> std::io::Result<std::path::PathBuf> {
    use std::os::unix::fs::{DirBuilderExt, MetadataExt, PermissionsExt};
    // A fixed short parent avoids sockaddr_un limits for deeply nested profiles.
    // SAFETY: geteuid has no preconditions and does not mutate process state.
    let uid = unsafe { libc::geteuid() };
    let hash = crate::store::fingerprint(profile.as_os_str().as_encoded_bytes());
    let directory = std::path::PathBuf::from(format!("/tmp/savior-{uid}-{hash}"));
    if create {
        match std::fs::DirBuilder::new().mode(0o700).create(&directory) {
            Ok(()) => (),
            Err(e) if e.kind() == std::io::ErrorKind::AlreadyExists => (),
            Err(e) => return Err(e),
        }
    }
    let metadata = std::fs::symlink_metadata(&directory)?;
    if !metadata.is_dir() || metadata.uid() != uid || metadata.permissions().mode() & 0o777 != 0o700
    {
        return Err(std::io::ErrorKind::PermissionDenied.into());
    }
    Ok(directory.join(name))
}

pub const MAX_RESPONSE_BYTES: usize = 16 * 1024 * 1024; // 16 MiB, including newline

pub fn response_bytes(value: &Value) -> Vec<u8> {
    let mut bytes = value.to_string().into_bytes();
    if bytes.len() >= MAX_RESPONSE_BYTES {
        bytes = json!({"version":1,"status":"error","code":"review_response_too_large"})
            .to_string()
            .into_bytes();
    }
    bytes.push(b'\n');
    bytes
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn framing_limit_includes_newline() {
        for (length, accepted) in [
            (MAX_RESPONSE_BYTES - 3, true),
            (MAX_RESPONSE_BYTES - 2, false),
        ] {
            let value = Value::String("x".repeat(length));
            let bytes = response_bytes(&value);
            assert!(bytes.len() <= MAX_RESPONSE_BYTES);
            assert_eq!(bytes.last(), Some(&b'\n'));
            let decoded: Value = serde_json::from_slice(&bytes).unwrap();
            if accepted {
                assert_eq!(decoded, value);
            } else {
                assert_eq!(decoded["code"], "review_response_too_large");
            }
        }
    }
}
