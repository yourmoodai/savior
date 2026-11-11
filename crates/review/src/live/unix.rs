use std::{
    io,
    os::unix::{
        fs::{FileTypeExt, MetadataExt, PermissionsExt},
        net::UnixStream,
    },
    path::Path,
};

pub(super) fn connect(profile: &Path) -> io::Result<UnixStream> {
    let path = crate::transport::socket_path(profile, "watch", false)?;
    let metadata = std::fs::symlink_metadata(&path)?;
    // SAFETY: geteuid has no preconditions.
    if !metadata.file_type().is_socket()
        || metadata.uid() != unsafe { libc::geteuid() }
        || metadata.permissions().mode() & 0o777 != 0o600
    {
        return Err(io::ErrorKind::PermissionDenied.into());
    }
    let socket = socket2::Socket::new(socket2::Domain::UNIX, socket2::Type::STREAM, None)?;
    socket.set_nonblocking(true)?;
    // A full accept queue must not block the rewrite process.
    socket.connect(&socket2::SockAddr::unix(path)?)?;
    let fd: std::os::fd::OwnedFd = socket.into();
    Ok(fd.into())
}

#[cfg(any(feature = "watch", test))]
pub(super) struct Listener {
    listener: std::os::unix::net::UnixListener,
    path: std::path::PathBuf,
    _lock: std::fs::File,
}

#[cfg(any(feature = "watch", test))]
impl Listener {
    pub(super) fn bind(profile: &Path) -> crate::ReviewResult<Self> {
        use std::os::unix::fs::OpenOptionsExt;
        let path = crate::transport::socket_path(profile, "watch", true)
            .map_err(|_| "unsafe_watch_path")?;
        let lock = std::fs::OpenOptions::new()
            .create(true)
            .truncate(false)
            .read(true)
            .write(true)
            .mode(0o600)
            .custom_flags(libc::O_NOFOLLOW | libc::O_CLOEXEC)
            .open(path.with_extension("lock"))
            .map_err(|_| "unsafe_watch_path")?;
        let metadata = lock.metadata().map_err(|_| "unsafe_watch_path")?;
        // SAFETY: geteuid has no preconditions.
        if !metadata.is_file()
            || metadata.nlink() != 1
            || metadata.uid() != unsafe { libc::geteuid() }
            || metadata.permissions().mode() & 0o777 != 0o600
        {
            return Err("unsafe_watch_path".into());
        }
        fs2::FileExt::try_lock_exclusive(&lock).map_err(|_| "watch_already_running")?;
        match std::fs::symlink_metadata(&path) {
            Ok(metadata) if metadata.file_type().is_socket() => {
                std::fs::remove_file(&path).map_err(|_| "watch_unavailable")?;
            }
            Ok(_) => return Err("unsafe_watch_path".into()),
            Err(e) if e.kind() == io::ErrorKind::NotFound => (),
            Err(_) => return Err("watch_unavailable".into()),
        }
        let listener =
            std::os::unix::net::UnixListener::bind(&path).map_err(|_| "watch_unavailable")?;
        let listener = Self {
            listener,
            path,
            _lock: lock,
        };
        std::fs::set_permissions(&listener.path, std::fs::Permissions::from_mode(0o600))
            .map_err(|_| "watch_unavailable")?;
        listener
            .listener
            .set_nonblocking(true)
            .map_err(|_| "watch_unavailable")?;
        Ok(listener)
    }

    pub(super) fn accept(&mut self) -> io::Result<Option<UnixStream>> {
        match self.listener.accept() {
            Ok((stream, _)) => {
                stream.set_nonblocking(true)?;
                Ok(Some(stream))
            }
            Err(e) if e.kind() == io::ErrorKind::WouldBlock => Ok(None),
            Err(e) => Err(e),
        }
    }
}

#[cfg(any(feature = "watch", test))]
impl Drop for Listener {
    fn drop(&mut self) {
        let _ = std::fs::remove_file(&self.path);
    }
}
