#![cfg(unix)]
use std::{
    fs,
    os::unix::fs::{PermissionsExt, symlink},
    process::Command,
};

fn run(command: &str, directory: &std::path::Path) -> std::process::Output {
    Command::new(env!("CARGO_BIN_EXE_savior-network"))
        .arg(command)
        .arg(directory)
        .output()
        .unwrap()
}

#[test]
fn initialization_is_private_idempotent_and_does_not_claim_enforcement() {
    let temporary = tempfile::tempdir().unwrap();
    let directory = temporary.path().join("network");
    assert!(run("init", &directory).status.success());
    let original = fs::read(directory.join("ca-key.pem")).unwrap();
    assert!(run("init", &directory).status.success());
    assert_eq!(fs::read(directory.join("ca-key.pem")).unwrap(), original);
    assert_eq!(
        fs::metadata(&directory).unwrap().permissions().mode() & 0o777,
        0o700
    );
    assert_eq!(
        fs::metadata(directory.join("ca-key.pem"))
            .unwrap()
            .permissions()
            .mode()
            & 0o777,
        0o600
    );
    let output = run("status", &directory);
    assert!(output.status.success());
    let status: serde_json::Value = serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!(status["enforced"], false);
    assert_eq!(status["qualified_clients"], serde_json::json!([]));
    for command in ["install", "enable", "disable", "uninstall"] {
        let output = run(command, &directory);
        assert!(!output.status.success());
        assert!(!String::from_utf8_lossy(&output.stdout).contains("BEGIN PRIVATE KEY"));
    }
    assert_eq!(fs::read(directory.join("ca-key.pem")).unwrap(), original);
}

#[test]
fn interrupted_initialization_does_not_rotate_existing_keys() {
    let temporary = tempfile::tempdir().unwrap();
    let directory = temporary.path().join("network");
    fs::create_dir(&directory).unwrap();
    fs::set_permissions(&directory, fs::Permissions::from_mode(0o700)).unwrap();
    fs::write(directory.join("ca-key.pem"), "synthetic_incomplete_key").unwrap();
    assert!(!run("init", &directory).status.success());
    assert_eq!(
        fs::read_to_string(directory.join("ca-key.pem")).unwrap(),
        "synthetic_incomplete_key"
    );
    assert!(!directory.join("policy.json").exists());
}

#[test]
fn configuration_rejects_symlinks_and_public_key_permissions() {
    let temporary = tempfile::tempdir().unwrap();
    let directory = temporary.path().join("network");
    assert!(run("init", &directory).status.success());
    fs::set_permissions(
        directory.join("ca-key.pem"),
        fs::Permissions::from_mode(0o644),
    )
    .unwrap();
    assert!(!run("doctor", &directory).status.success());
    let link = temporary.path().join("linked");
    symlink(&directory, &link).unwrap();
    assert!(!run("init", &link).status.success());
    assert!(!run("serve", &link).status.success());
}
