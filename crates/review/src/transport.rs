//! Bounded response framing shared by both worker transports.
use serde_json::{Value, json};

#[cfg(unix)]
pub fn socket_path(
    profile: &std::path::Path,
    name: &str,
    create: bool,
) -> std::io::Result<std::path::PathBuf> {
    let runtime = std::env::var_os("XDG_RUNTIME_DIR").map(std::path::PathBuf::from);
    socket_path_in(profile, name, create, runtime.as_deref())
}

#[cfg(unix)]
fn socket_path_in(
    profile: &std::path::Path,
    name: &str,
    create: bool,
    runtime: Option<&std::path::Path>,
) -> std::io::Result<std::path::PathBuf> {
    use std::os::unix::fs::{DirBuilderExt, MetadataExt, PermissionsExt};
    // A fixed short parent avoids sockaddr_un limits for deeply nested profiles.
    // SAFETY: geteuid has no preconditions and does not mutate process state.
    let uid = unsafe { libc::geteuid() };
    let hash = crate::store::fingerprint(profile.as_os_str().as_encoded_bytes());
    let directory_name = format!("savior-{uid}-{hash}");
    let mut directory = std::path::Path::new("/tmp").join(&directory_name);
    if let Some(root) = runtime.filter(|p| p.is_absolute()) {
        let metadata = std::fs::symlink_metadata(root)?;
        if !metadata.is_dir()
            || metadata.uid() != uid
            || metadata.permissions().mode() & 0o777 != 0o700
        {
            return Err(std::io::ErrorKind::PermissionDenied.into());
        }
        let candidate = root.join(directory_name);
        // The bridge uses the same bound, including /watch and its NUL.
        if candidate.as_os_str().as_encoded_bytes().len() + 6 <= 100 {
            directory = candidate;
        }
    }
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

    #[cfg(unix)]
    #[test]
    fn runtime_directory_is_private_and_preserves_short_socket_fallback() {
        use std::os::unix::fs::{PermissionsExt, symlink};
        let root = tempfile::Builder::new()
            .prefix("sv")
            .tempdir_in("/tmp")
            .unwrap();
        std::fs::set_permissions(root.path(), std::fs::Permissions::from_mode(0o700)).unwrap();
        let profile = root.path().join("profile");
        let path = socket_path_in(&profile, "s", true, Some(root.path())).unwrap();
        assert!(path.starts_with(root.path()));
        assert_eq!(
            socket_path_in(&profile, "s", false, Some(root.path())).unwrap(),
            path
        );
        std::fs::set_permissions(root.path(), std::fs::Permissions::from_mode(0o755)).unwrap();
        assert!(socket_path_in(&profile, "s", true, Some(root.path())).is_err());
        std::fs::set_permissions(root.path(), std::fs::Permissions::from_mode(0o700)).unwrap();
        let link = root.path().join("link");
        symlink(root.path(), &link).unwrap();
        assert!(socket_path_in(&profile, "s", true, Some(&link)).is_err());
        let long = root.path().join("x".repeat(120));
        std::fs::create_dir(&long).unwrap();
        std::fs::set_permissions(&long, std::fs::Permissions::from_mode(0o700)).unwrap();
        let fallback = socket_path_in(&profile, "watch", true, Some(&long)).unwrap();
        assert!(fallback.as_os_str().as_encoded_bytes().len() < 100);
        assert!(!fallback.starts_with(&long));
        std::fs::remove_dir(fallback.parent().unwrap()).unwrap();
    }

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
