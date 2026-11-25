//! First activation of the Linux ARM64 plugin needs Node before its JS verifier can run.
#[cfg(unix)]
mod launcher {
    use sha2::{Digest, Sha256};
    use std::env;
    use std::fs::{self, DirBuilder, File, Permissions};
    use std::io::{self, Read, Seek};
    use std::os::fd::AsRawFd;
    use std::os::unix::fs::{DirBuilderExt, MetadataExt, PermissionsExt};
    use std::os::unix::process::CommandExt;
    use std::path::{Path, PathBuf};
    use std::process::Command;
    use std::time::Duration;

    const NODE_SHA256: &str =
        include_str!("../../../scripts/release/node-linux-arm64.sha256").trim_ascii();
    const NODE_BYTES: u64 = 150_330_040; // 143.37 MiB

    fn verified(file: &mut File) -> io::Result<bool> {
        if !file.metadata()?.is_file() || file.metadata()?.len() != NODE_BYTES {
            return Ok(false);
        }
        file.rewind()?;
        let mut hash = Sha256::new();
        let mut buffer = [0u8; 64 * 1024]; // 64 KiB
        loop {
            let count = file.read(&mut buffer)?;
            if count == 0 {
                break;
            }
            hash.update(&buffer[..count]);
        }
        Ok(base16ct::lower::encode_string(&hash.finalize()) == NODE_SHA256)
    }

    fn cache_root() -> io::Result<PathBuf> {
        for (variable, suffix) in [("XDG_CACHE_HOME", ""), ("HOME", ".cache")] {
            if let Some(path) = env::var_os(variable).map(PathBuf::from)
                && path.is_absolute()
            {
                return Ok(path.join(suffix));
            }
        }
        Err(io::Error::other("invalid_cache_root"))
    }

    fn private_cache(root: &Path, relative: &Path) -> io::Result<PathBuf> {
        DirBuilder::new().recursive(true).mode(0o700).create(root)?;
        let mut directory = fs::canonicalize(root)?;
        let owner = rustix::process::geteuid().as_raw();
        for component in std::iter::once(Path::new("")).chain(relative.iter().map(Path::new)) {
            directory.push(component);
            match DirBuilder::new().mode(0o700).create(&directory) {
                Err(error) if error.kind() == io::ErrorKind::AlreadyExists => (),
                result => result?,
            }
            let meta = fs::symlink_metadata(&directory)?;
            if !meta.is_dir() || meta.uid() != owner || meta.mode() & 0o022 != 0 {
                return Err(io::Error::other("unsafe_cache_directory"));
            }
        }
        Ok(directory)
    }

    pub fn launch() -> Result<(), Box<dyn std::error::Error>> {
        let args: Vec<_> = env::args_os().skip(1).collect();
        let exe = env::current_exe()?;
        let root = exe.ancestors().nth(3).ok_or("invalid_plugin_directory")?;
        let bytes = fs::read(root.join("runtime-manifest.json"))?;
        let manifest: serde_json::Value = serde_json::from_slice(&bytes)?;
        let version = manifest["version"].as_str().ok_or("missing_version")?;
        if !version.starts_with(|c: char| c.is_ascii_digit())
            || !version
                .bytes()
                .all(|b| b.is_ascii_alphanumeric() || b".+-".contains(&b))
        {
            return Err("invalid_version".into());
        }
        let asset = format!("runtime-{version}-linux-arm64-node");
        let entries = manifest["targets"]["linux-arm64"]
            .as_array()
            .ok_or("missing_target")?;
        if !entries.iter().any(|e| {
            e["name"] == "node"
                && e["asset"] == asset
                && e["bytes"] == NODE_BYTES
                && e["sha256"] == NODE_SHA256
        }) {
            return Err("unexpected_node_identity".into());
        }
        let manifest_hash = base16ct::lower::encode_string(&Sha256::digest(&bytes));
        let relative = Path::new("savior/releases")
            .join(version)
            .join("linux-arm64")
            .join(manifest_hash);
        let directory = private_cache(&cache_root()?, &relative)?;
        let path = directory.join("node");
        let file = match fs::symlink_metadata(&path) {
            Ok(meta) if meta.is_file() && meta.nlink() == 1 => File::open(&path)?,
            Ok(_) => return Err("unsafe_cached_node".into()),
            Err(error) if error.kind() == io::ErrorKind::NotFound => {
                let url = format!(
                    "https://github.com/yourmoodai/savior/releases/download/v{version}/{asset}"
                );
                let response = reqwest::blocking::Client::builder()
                    .https_only(true)
                    .timeout(Duration::from_secs(480))
                    .build()?
                    .get(url)
                    .send()?
                    .error_for_status()?;
                let mut temp = tempfile::NamedTempFile::new_in(directory)?;
                io::copy(&mut response.take(NODE_BYTES + 1), &mut temp)?;
                if !verified(temp.as_file_mut())? {
                    return Err("node_digest_mismatch".into());
                }
                temp.as_file()
                    .set_permissions(Permissions::from_mode(0o700))?;
                temp.as_file().sync_all()?;
                drop(temp.persist(&path)?);
                File::open(path)?
            }
            Err(error) => return Err(error.into()),
        };
        let mut file = file;
        if !verified(&mut file)? {
            return Err("node_digest_mismatch".into());
        }
        Err(Command::new(format!("/proc/self/fd/{}", file.as_raw_fd()))
            .args(args)
            .exec()
            .into())
    }
}

fn main() {
    #[cfg(unix)]
    if launcher::launch().is_ok() {
        return;
    }
    eprintln!("verified_node_unavailable");
    std::process::exit(1);
}
