//! User directory resolution; invalid XDG values never become relative writes.
use std::path::{Path, PathBuf};

pub fn resolve_base(value: Option<&Path>, home: Option<&Path>, fallback: &str) -> Option<PathBuf> {
    value
        .filter(|p| p.is_absolute())
        .map(Path::to_path_buf)
        .or_else(|| home.filter(|p| p.is_absolute()).map(|p| p.join(fallback)))
}

pub fn user_root(variable: &str, fallback: &str) -> Result<PathBuf, String> {
    if cfg!(windows) {
        return std::env::var_os("LOCALAPPDATA")
            .map(PathBuf::from)
            .filter(|p| p.is_absolute())
            .ok_or_else(|| "user_directory_unavailable".into());
    }
    let value = std::env::var_os(variable).map(PathBuf::from);
    let home = std::env::var_os("HOME").map(PathBuf::from);
    resolve_base(value.as_deref(), home.as_deref(), fallback)
        .ok_or_else(|| "user_directory_unavailable".into())
}

#[cfg(all(test, unix))]
mod tests {
    use super::*;

    #[test]
    fn xdg_paths_ignore_empty_and_relative_values() {
        let home = Path::new("/home/synthetic user");
        for value in [None, Some(Path::new("")), Some(Path::new("relative"))] {
            assert_eq!(
                resolve_base(value, Some(home), ".cache"),
                Some(home.join(".cache"))
            );
        }
        let custom = Path::new("/private/cache space/é");
        assert_eq!(
            resolve_base(Some(custom), None, ".cache"),
            Some(custom.to_path_buf())
        );
        assert_eq!(resolve_base(None, None, ".cache"), None);
        assert_eq!(
            resolve_base(None, Some(Path::new("relative")), ".cache"),
            None
        );
    }
}
