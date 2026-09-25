//! Newline-delimited JSON over the shell's stdin/stdout pipe and a Unix socket
//! for the CLI, tests and hooks (one socket client at a time).

use serde_json::Value;
use std::io;
use std::os::fd::{AsRawFd, FromRawFd, IntoRawFd, RawFd};
use std::os::unix::fs::{MetadataExt, PermissionsExt};
use std::os::unix::net::{UnixListener, UnixStream};
use std::path::{Path, PathBuf};

/// One newline-delimited JSON channel over a pair of raw fds (read and write
/// may be the same fd, as for a socket, or different, as for the shell's
/// stdin/stdout pipe). Non-blocking; `eof` is set once the peer has gone away.
pub struct LineChannel {
    pub rfd: RawFd,
    wfd: RawFd,
    rbuf: Vec<u8>,
    pub eof: bool,
}

impl LineChannel {
    pub fn new(rfd: RawFd, wfd: RawFd) -> Self {
        LineChannel { rfd, wfd, rbuf: Vec::new(), eof: false }
    }

    /// One non-blocking read, buffered and split on '\n'. A line that fails to
    /// parse as JSON becomes {"cmd": "__bad__"} (handled as "unknown cmd" by
    /// Controller::handle) rather than being dropped silently or panicking:
    /// malformed input from either the shell or a socket client must never crash
    /// the daemon.
    pub fn read_messages(&mut self) -> Vec<Value> {
        let mut buf = [0u8; 65536];
        let n = unsafe { libc::read(self.rfd, buf.as_mut_ptr() as *mut libc::c_void, buf.len()) };
        if n < 0 {
            let err = io::Error::last_os_error();
            if err.kind() == io::ErrorKind::WouldBlock || err.kind() == io::ErrorKind::Interrupted { return vec![]; }
            self.eof = true;
            return vec![];
        }
        if n == 0 { self.eof = true; return vec![]; }
        self.rbuf.extend_from_slice(&buf[..n as usize]);
        let mut out = Vec::new();
        while let Some(pos) = self.rbuf.iter().position(|&b| b == b'\n') {
            let line: Vec<u8> = self.rbuf.drain(..=pos).collect();
            let s = String::from_utf8_lossy(&line[..line.len() - 1]).trim().to_string();
            if s.is_empty() { continue; }
            out.push(serde_json::from_str::<Value>(&s).unwrap_or_else(|_| serde_json::json!({"cmd": "__bad__"})));
        }
        out
    }

    /// Write one JSON line, retrying on EINTR/EWOULDBLOCK (briefly polling for
    /// writability) so a slow reader never loses a line; any other write error
    /// marks the channel as EOF instead of propagating, since a dead peer is not
    /// a fatal condition for the daemon.
    pub fn send(&mut self, v: &Value) {
        let mut data = serde_json::to_vec(v).unwrap_or_default();
        data.push(b'\n');
        let mut off = 0;
        while off < data.len() {
            let n = unsafe { libc::write(self.wfd, data[off..].as_ptr() as *const libc::c_void, data.len() - off) };
            if n <= 0 {
                let err = io::Error::last_os_error();
                if n < 0 && err.kind() == io::ErrorKind::Interrupted { continue; }
                if n < 0 && err.kind() == io::ErrorKind::WouldBlock {
                    // Slow reader (socket client): wait briefly rather than drop the line.
                    let mut p = libc::pollfd { fd: self.wfd, events: libc::POLLOUT, revents: 0 };
                    unsafe { libc::poll(&mut p, 1, 200) };
                    continue;
                }
                self.eof = true;
                return;
            }
            off += n as usize;
        }
    }
}

/// True only when fd 0 is a FIFO, i.e. omarchy-shell spawned us with a pipe.
/// Used to decide whether the parent-pipe protocol (and lifeline) is active at
/// all; run by hand from a terminal, stdin is not a pipe and this is false.
pub fn stdin_is_pipe() -> bool {
    let mut st: libc::stat = unsafe { std::mem::zeroed() };
    if unsafe { libc::fstat(0, &mut st) } != 0 { return false; }
    (st.st_mode & libc::S_IFMT) == libc::S_IFIFO
}

/// `Locked` means another daemon already owns the socket (see CtlServer::start);
/// main() treats that as a normal, expected exit, not an error.
pub enum StartError { Locked, Io(io::Error) }

/// Owns the control socket and its lock file, plus the single connected client
/// (if any: only one socket client is served at a time, deliberately, so the
/// protocol never has to arbitrate between two writers).
pub struct CtlServer {
    path: PathBuf,
    _lockfd: RawFd,
    pub listener: UnixListener,
    client: Option<(UnixStream, LineChannel)>,
}

impl CtlServer {
    /// Binds `ctl.sock`. An flock() on a separate `.sock.lock` file (held for the
    /// life of the process) is what actually enforces single-instance: two
    /// daemons racing to start only one gets the lock, so the loser exits with
    /// StartError::Locked instead of stealing or corrupting the other's socket.
    /// The socket file itself is narrowed to 0600 (see below); umask is tightened
    /// around bind() so it is never world-reachable even for the instant between
    /// bind and the explicit chmod, and the parent directory is refused unless
    /// it is already owned by this user.
    pub fn start(path: &Path) -> Result<Self, StartError> {
        prepare_socket_dir(path).map_err(StartError::Io)?;
        let lock = std::fs::OpenOptions::new().read(true).write(true).create(true).mode_0600()
            .open(path.with_extension("sock.lock")).map_err(StartError::Io)?;
        let lockfd = lock.into_raw_fd();
        if unsafe { libc::flock(lockfd, libc::LOCK_EX | libc::LOCK_NB) } != 0 {
            return Err(StartError::Locked);
        }
        let _ = std::fs::remove_file(path);
        // umask 077 so the socket is never world-reachable between bind and chmod.
        let old = unsafe { libc::umask(0o077) };
        let listener = UnixListener::bind(path);
        unsafe { libc::umask(old) };
        let listener = listener.map_err(StartError::Io)?;
        std::fs::set_permissions(path, std::fs::Permissions::from_mode(0o600)).map_err(StartError::Io)?;
        listener.set_nonblocking(true).map_err(StartError::Io)?;
        Ok(CtlServer { path: path.to_path_buf(), _lockfd: lockfd, listener, client: None })
    }

    pub fn client_fd(&self) -> Option<RawFd> { self.client.as_ref().map(|(s, _)| s.as_raw_fd()) }

    /// Accept one connection, replacing (dropping) any previous client: only one
    /// socket client is ever served at a time. Access control is the socket
    /// file's own 0600 permission bit, enforced by the kernel at connect() time;
    /// there is no additional authentication in this protocol.
    pub fn accept(&mut self) -> bool {
        match self.listener.accept() {
            Ok((stream, _)) => {
                let _ = stream.set_nonblocking(true);
                let fd = stream.as_raw_fd();
                self.client = Some((stream, LineChannel::new(fd, fd)));
                true
            }
            Err(_) => false,
        }
    }

    pub fn read_messages(&mut self) -> Vec<Value> {
        let (msgs, eof) = match self.client.as_mut() {
            Some((_, ch)) => { let m = ch.read_messages(); (m, ch.eof) }
            None => (vec![], false),
        };
        if eof { self.client = None; }
        msgs
    }

    pub fn send(&mut self, v: &Value) {
        let eof = match self.client.as_mut() {
            Some((_, ch)) => { ch.send(v); ch.eof }
            None => false,
        };
        if eof { self.client = None; }
    }

    // Drop the client and remove the socket file on shutdown; the lock file is
    // released implicitly when the process exits and _lockfd's fd closes.
    pub fn close(&mut self) {
        self.client = None;
        let _ = std::fs::remove_file(&self.path);
    }
}

// Small helper so the lock file is opened 0600 in one call, same intent as the
// umask dance around the socket itself.
trait Mode0600 { fn mode_0600(&mut self) -> &mut Self; }
impl Mode0600 for std::fs::OpenOptions {
    fn mode_0600(&mut self) -> &mut Self { std::os::unix::fs::OpenOptionsExt::mode(self, 0o600) }
}

/// Create the socket parent. Refuse a directory we do not own (so we never bind
/// inside someone else's /tmp/omaclack). Only chmod when the dir is named
/// `omaclack` — never 0700 the user's home because someone passed `--socket=~/ctl.sock`.
fn prepare_socket_dir(path: &Path) -> io::Result<()> {
    let Some(dir) = path.parent().filter(|d| !d.as_os_str().is_empty()) else { return Ok(()); };
    std::fs::create_dir_all(dir)?;
    let meta = std::fs::metadata(dir)?;
    if meta.uid() != unsafe { libc::getuid() } {
        return Err(io::Error::new(io::ErrorKind::PermissionDenied, "socket directory is not owned by this user"));
    }
    if dir.file_name().and_then(|n| n.to_str()) == Some("omaclack") {
        std::fs::set_permissions(dir, std::fs::Permissions::from_mode(0o700))?;
    }
    Ok(())
}

// Keeps the FromRawFd trait (needed nowhere else in this file) referenced so its
// import doesn't warn as unused; never called.
#[allow(dead_code)]
fn _keep(_: UnixStream) { let _ = unsafe { UnixStream::from_raw_fd(0) }; }
