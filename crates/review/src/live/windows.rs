use crate::windows::{descriptor, wide};
use std::{
    io::{self, Read, Write},
    os::windows::io::{AsRawHandle, FromRawHandle, OwnedHandle},
    path::Path,
    ptr::{null, null_mut},
    sync::Arc,
};
use windows_sys::Win32::{
    Foundation::*,
    Security::{Authorization::*, *},
    Storage::FileSystem::*,
    System::Pipes::*,
};

fn name(profile: &Path) -> Vec<u16> {
    let normalized = profile
        .to_string_lossy()
        .trim_start_matches("\\\\?\\")
        .to_lowercase();
    wide(&format!(
        "\\\\.\\pipe\\savior-watch-{}",
        crate::store::fingerprint(normalized.as_bytes())
    ))
}

pub(super) struct Stream {
    handle: Arc<OwnedHandle>,
    server: bool,
}

fn verify_owner(handle: HANDLE) -> io::Result<()> {
    let expected = descriptor().map_err(|_| io::Error::from(io::ErrorKind::PermissionDenied))?;
    let mut expected_owner = null_mut();
    let mut defaulted = 0;
    let mut owner = null_mut();
    let mut actual = null_mut();
    // SAFETY: all out-pointers are valid; the returned descriptor owns the SID.
    unsafe {
        if GetSecurityDescriptorOwner(expected.0, &mut expected_owner, &mut defaulted) == 0 {
            return Err(io::Error::last_os_error());
        }
        let status = GetSecurityInfo(
            handle,
            SE_KERNEL_OBJECT,
            OWNER_SECURITY_INFORMATION,
            &mut owner,
            null_mut(),
            null_mut(),
            null_mut(),
            &mut actual,
        );
        if status != ERROR_SUCCESS {
            return Err(io::Error::from_raw_os_error(status as i32));
        }
        let matches =
            !owner.is_null() && !expected_owner.is_null() && EqualSid(owner, expected_owner) != 0;
        LocalFree(actual.cast());
        if !matches {
            return Err(io::ErrorKind::PermissionDenied.into());
        }
    }
    Ok(())
}

pub(super) fn connect(profile: &Path) -> io::Result<Stream> {
    // SAFETY: name is NUL-terminated; the owned handle closes on every error path.
    let handle = unsafe {
        CreateFileW(
            name(profile).as_ptr(),
            GENERIC_READ | GENERIC_WRITE | READ_CONTROL,
            0,
            null(),
            OPEN_EXISTING,
            SECURITY_SQOS_PRESENT | SECURITY_IDENTIFICATION,
            null_mut(),
        )
    };
    if handle == INVALID_HANDLE_VALUE {
        return Err(io::Error::last_os_error());
    }
    let handle = Arc::new(unsafe { OwnedHandle::from_raw_handle(handle) });
    verify_owner(handle.as_raw_handle())?;
    let mode = PIPE_READMODE_BYTE | PIPE_NOWAIT;
    if unsafe { SetNamedPipeHandleState(handle.as_raw_handle(), &mode, null(), null()) } == 0 {
        return Err(io::Error::last_os_error());
    }
    Ok(Stream {
        handle,
        server: false,
    })
}

impl Read for Stream {
    fn read(&mut self, buffer: &mut [u8]) -> io::Result<usize> {
        let mut count = 0;
        // SAFETY: buffer is writable for its length and handle remains owned.
        if unsafe {
            ReadFile(
                self.handle.as_raw_handle(),
                buffer.as_mut_ptr(),
                buffer.len() as u32,
                &mut count,
                null_mut(),
            )
        } == 0
        {
            let code = unsafe { GetLastError() };
            return match code {
                ERROR_NO_DATA | ERROR_PIPE_LISTENING => Err(io::ErrorKind::WouldBlock.into()),
                ERROR_BROKEN_PIPE => Ok(0),
                _ => Err(io::Error::from_raw_os_error(code as i32)),
            };
        }
        Ok(count as usize)
    }
}

impl Write for Stream {
    fn write(&mut self, buffer: &[u8]) -> io::Result<usize> {
        let mut count = 0;
        // SAFETY: buffer is readable for its length and handle remains owned.
        if unsafe {
            WriteFile(
                self.handle.as_raw_handle(),
                buffer.as_ptr(),
                buffer.len() as u32,
                &mut count,
                null_mut(),
            )
        } == 0
        {
            return Err(io::Error::last_os_error());
        }
        if count == 0 && !buffer.is_empty() {
            return Err(io::ErrorKind::WouldBlock.into());
        }
        Ok(count as usize)
    }
    fn flush(&mut self) -> io::Result<()> {
        Ok(())
    }
}

impl Drop for Stream {
    fn drop(&mut self) {
        if self.server {
            // No FlushFileBuffers: a stalled client must not hold the watcher open.
            unsafe {
                DisconnectNamedPipe(self.handle.as_raw_handle());
            }
        }
    }
}

#[cfg(any(feature = "watch", test))]
pub(super) struct Listener(Arc<OwnedHandle>);

#[cfg(any(feature = "watch", test))]
impl Listener {
    pub(super) fn bind(profile: &Path) -> crate::ReviewResult<Self> {
        let sd = descriptor()?;
        let attributes = SECURITY_ATTRIBUTES {
            nLength: std::mem::size_of::<SECURITY_ATTRIBUTES>() as u32,
            lpSecurityDescriptor: sd.0,
            bInheritHandle: 0,
        };
        // SAFETY: security descriptor and name remain valid through creation.
        let handle = unsafe {
            CreateNamedPipeW(
                name(profile).as_ptr(),
                PIPE_ACCESS_DUPLEX | FILE_FLAG_FIRST_PIPE_INSTANCE,
                PIPE_TYPE_BYTE | PIPE_READMODE_BYTE | PIPE_NOWAIT | PIPE_REJECT_REMOTE_CLIENTS,
                1,
                65_536,
                65_536,
                0,
                &attributes,
            )
        }; // 64 KiB buffers
        if handle == INVALID_HANDLE_VALUE {
            return Err(if unsafe { GetLastError() } == ERROR_ACCESS_DENIED {
                "watch_already_running"
            } else {
                "watch_unavailable"
            }
            .into());
        }
        Ok(Self(Arc::new(unsafe {
            OwnedHandle::from_raw_handle(handle)
        })))
    }

    pub(super) fn accept(&mut self) -> io::Result<Option<Stream>> {
        // In NOWAIT mode success makes a disconnected instance available; only
        // ERROR_PIPE_CONNECTED establishes that a client is present.
        if unsafe { ConnectNamedPipe(self.0.as_raw_handle(), null_mut()) } != 0 {
            return Ok(None);
        }
        let code = unsafe { GetLastError() };
        match code {
            ERROR_PIPE_CONNECTED => Ok(Some(Stream {
                handle: self.0.clone(),
                server: true,
            })),
            ERROR_PIPE_LISTENING => Ok(None),
            ERROR_NO_DATA => {
                unsafe {
                    DisconnectNamedPipe(self.0.as_raw_handle());
                }
                Ok(None)
            }
            _ => Err(io::Error::from_raw_os_error(code as i32)),
        }
    }
}
