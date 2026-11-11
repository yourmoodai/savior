use std::process::{self, Command};

fn main() {
    if launch().is_err() {
        eprintln!("bundled_node_unavailable");
        process::exit(1);
    }
}

fn launch() -> std::io::Result<()> {
    let executable = std::env::current_exe()?;
    let contents = executable
        .parent()
        .and_then(|path| path.parent())
        .ok_or_else(|| std::io::Error::other("invalid_bundle_path"))?;
    let mut command = Command::new(contents.join("Frameworks/node"));
    command.args(std::env::args_os().skip(1));
    #[cfg(unix)]
    {
        use std::os::unix::process::CommandExt;
        // Replace the launcher so Node owns stdin, signals, and the exit status.
        Err(command.exec())
    }
    #[cfg(not(unix))]
    {
        let _ = command;
        Err(std::io::Error::other("unsupported_platform"))
    }
}
