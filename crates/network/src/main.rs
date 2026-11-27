use savior::{ReplacementMode, RuntimeDetector};
use savior_network::{
    Error,
    certificate::Authority,
    gateway::{Gateway, RewriteWorker},
    policy::Policy,
};
use std::{
    fs,
    io::{Read, Write},
    path::{Path, PathBuf},
    sync::Arc,
};

fn read(path: &Path, limit: u64) -> Result<String, &'static str> {
    let mut options = fs::OpenOptions::new();
    options.read(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.custom_flags(libc::O_NOFOLLOW);
    }
    let file = options
        .open(path)
        .map_err(|_| "network_configuration_unavailable")?;
    let metadata = file
        .metadata()
        .map_err(|_| "network_configuration_unavailable")?;
    if !metadata.is_file() {
        return Err("unsafe_network_configuration");
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::{MetadataExt, PermissionsExt};
        // SAFETY: geteuid has no preconditions.
        if metadata.uid() != unsafe { libc::geteuid() }
            || metadata.permissions().mode() & 0o077 != 0
        {
            return Err("unsafe_network_configuration");
        }
    }
    let mut text = String::new();
    file.take(limit + 1)
        .read_to_string(&mut text)
        .map_err(|_| "network_configuration_unavailable")?;
    if text.len() as u64 > limit {
        return Err("network_configuration_too_large");
    }
    Ok(text)
}

fn private_directory(path: &Path) -> Result<(), &'static str> {
    let metadata = fs::symlink_metadata(path).map_err(|_| "network_configuration_unavailable")?;
    if !metadata.is_dir() || metadata.file_type().is_symlink() {
        return Err("unsafe_network_directory");
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::{MetadataExt, PermissionsExt};
        // SAFETY: geteuid has no preconditions.
        if metadata.permissions().mode() & 0o777 != 0o700
            || metadata.uid() != unsafe { libc::geteuid() }
        {
            return Err("unsafe_network_directory");
        }
    }
    #[cfg(windows)]
    {
        Err("network_acl_verification_unavailable")
    }
    #[cfg(not(windows))]
    {
        Ok(())
    }
}

fn create_private(path: &Path, bytes: &[u8]) -> Result<(), &'static str> {
    let mut options = fs::OpenOptions::new();
    options.write(true).create_new(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600).custom_flags(libc::O_NOFOLLOW);
    }
    let mut file = options
        .open(path)
        .map_err(|_| "network_configuration_exists")?;
    file.write_all(bytes)
        .and_then(|_| file.sync_all())
        .map_err(|_| "network_configuration_write_failed")
}

fn load(directory: &Path) -> Result<(Policy, Authority), &'static str> {
    private_directory(directory)?;
    for name in ["policy.json", "ca.pem", "ca-key.pem"] {
        let metadata = fs::symlink_metadata(directory.join(name))
            .map_err(|_| "network_configuration_unavailable")?;
        if !metadata.is_file() || metadata.file_type().is_symlink() {
            return Err("unsafe_network_configuration");
        }
        #[cfg(unix)]
        {
            use std::os::unix::fs::{MetadataExt, PermissionsExt};
            // SAFETY: geteuid has no preconditions.
            if metadata.uid() != unsafe { libc::geteuid() }
                || metadata.permissions().mode() & 0o077 != 0
            {
                return Err("unsafe_network_configuration");
            }
        }
    }
    let policy: Policy = serde_json::from_str(&read(&directory.join("policy.json"), 65_536)?)
        .map_err(|_| "invalid_network_policy")?; // 64 KiB
    policy.validate().map_err(|_| "invalid_network_policy")?;
    let authority = Authority::from_pem(
        &read(&directory.join("ca.pem"), 16_384)?,
        &read(&directory.join("ca-key.pem"), 16_384)?,
    )
    .map_err(|_| "certificate_unavailable")?; // 16 KiB per PEM
    Ok((policy, authority))
}

#[tokio::main]
async fn main() {
    if let Err(code) = run().await {
        println!(
            "{}",
            serde_json::json!({"version":1,"status":"error","code":code})
        );
        std::process::exit(1);
    }
}

async fn run() -> Result<(), String> {
    let args: Vec<String> = std::env::args().skip(1).collect();
    if args.is_empty() || args == ["--help"] {
        println!("savior network init|serve|status|doctor <private-directory>");
        return Ok(());
    }
    let [command, directory] = args.as_slice() else {
        return Err("invalid_command".into());
    };
    let directory = PathBuf::from(directory);
    if !directory.is_absolute() {
        return Err("network_directory_must_be_absolute".into());
    }
    match command.as_str() {
        "init" => {
            let mut builder = fs::DirBuilder::new();
            builder.recursive(false);
            #[cfg(unix)]
            {
                use std::os::unix::fs::DirBuilderExt;
                builder.mode(0o700);
            }
            match builder.create(&directory) {
                Ok(()) => (),
                Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => (),
                Err(_) => return Err("network_configuration_write_failed".into()),
            }
            private_directory(&directory)?;
            let names = ["ca.pem", "ca-key.pem", "policy.json"];
            let existing = names
                .iter()
                .filter(|name| directory.join(name).exists())
                .count();
            if existing == names.len() {
                load(&directory)?;
                println!(
                    "{}",
                    serde_json::json!({"version":1,"status":"initialized","enforced":false,"trust_installed":false})
                );
                return Ok(());
            }
            // An interrupted initialization must not rotate a CA a client may already trust.
            if existing != 0 {
                return Err("network_configuration_incomplete".into());
            }
            let (authority, key) = Authority::generate().map_err(|_| "certificate_unavailable")?;
            create_private(&directory.join("ca-key.pem"), key.as_bytes())?;
            create_private(&directory.join("ca.pem"), authority.pem().as_bytes())?;
            create_private(
                &directory.join("policy.json"),
                include_bytes!("../../../fixtures/network/development-policy.json"),
            )?;
            println!(
                "{}",
                serde_json::json!({"version":1,"status":"initialized","enforced":false,"trust_installed":false})
            );
        }
        "status" | "doctor" => {
            let (policy, _) = load(&directory)?;
            println!(
                "{}",
                serde_json::json!({"version":1,"status":"development_only","enforced":false,"native_enforcement":"unavailable","qualified_clients":[],"configured_destinations":policy.destinations.len()})
            );
        }
        "serve" => {
            let (policy, authority) = load(&directory)?;
            let model_dir =
                std::env::var("SAVIOR_MODEL_DIR").map_err(|_| "model_not_configured")?;
            let kind = std::env::var("SAVIOR_DETECTOR").unwrap_or_else(|_| "bert".into());
            let detection_policy = std::env::var("SAVIOR_POLICY")
                .unwrap_or_else(|_| "offensive_spans".into())
                .parse()
                .map_err(|_| "invalid_options")?;
            let detector =
                RuntimeDetector::load(model_dir, &kind, detection_policy, Default::default())
                    .map_err(|_| "model_unavailable")?;
            let threshold = std::env::var("SAVIOR_THRESHOLD")
                .ok()
                .map(|value| value.parse::<f32>())
                .transpose()
                .map_err(|_| "invalid_options")?
                .unwrap_or_else(|| detector.threshold());
            let replacement = ReplacementMode::new(
                std::env::var("SAVIOR_REPLACEMENT_STRATEGY").ok().as_deref(),
                std::env::var("SAVIOR_REPLACEMENT").ok(),
            )
            .map_err(|_| "invalid_options")?;
            let worker = Arc::new(
                RewriteWorker::new(detector, threshold, replacement)
                    .map_err(|_| "invalid_options")?,
            );
            let roots =
                rustls::RootCertStore::from_iter(webpki_roots::TLS_SERVER_ROOTS.iter().cloned());
            let listener = tokio::net::TcpListener::bind(policy.listen)
                .await
                .map_err(|_| "network_bind_failed")?;
            let native_listener = match policy.native_listen {
                Some(address) => Some(
                    tokio::net::TcpListener::bind(address)
                        .await
                        .map_err(|_| "network_bind_failed")?,
                ),
                None => None,
            };
            let native_tls = authority
                .server_config("localhost")
                .map_err(|_| "certificate_unavailable")?;
            let gateway = Gateway::new(policy, &authority, worker, roots)
                .map_err(|error| error.to_string())?;
            if std::env::var("LOG_LEVEL").as_deref() != Ok("silent") {
                eprintln!("savior: development_proxy_listening");
            }
            tokio::select! {
                result = gateway.clone().serve(listener) => result.map_err(|error: Error| error.to_string())?,
                result = async {
                    match native_listener {
                        Some(listener) => gateway.serve_local_tls(listener, native_tls).await,
                        None => std::future::pending().await,
                    }
                } => result.map_err(|error| error.to_string())?,
                _ = tokio::signal::ctrl_c() => (),
            }
        }
        "install" | "enable" | "disable" | "uninstall" => {
            return Err("native_enforcement_unavailable".into());
        }
        _ => return Err("invalid_command".into()),
    }
    Ok(())
}
