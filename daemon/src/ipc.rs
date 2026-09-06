//! Newline-delimited JSON over the shell's stdin/stdout pipe and a Unix socket
//! for the CLI, tests and hooks (one socket client at a time).

use serde_json::Value;
use std::io;
use std::os::fd::{AsRawFd, FromRawFd, IntoRawFd, RawFd};
use std::os::unix::net::{UnixListener, UnixStream};
use std::path::{Path, PathBuf};

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

pub fn stdin_is_pipe() -> bool {
    let mut st: libc::stat = unsafe { std::mem::zeroed() };
    if unsafe { libc::fstat(0, &mut st) } != 0 { return false; }
    (st.st_mode & libc::S_IFMT) == libc::S_IFIFO
}

pub enum StartError { Locked, Io(io::Error) }

pub struct CtlServer {
    path: PathBuf,
    _lockfd: RawFd,
    pub listener: UnixListener,
    client: Option<(UnixStream, LineChannel)>,
}

impl CtlServer {
    pub fn start(path: &Path) -> Result<Self, StartError> {
        if let Some(dir) = path.parent() {
            std::fs::create_dir_all(dir).map_err(StartError::Io)?;
            let _ = std::fs::set_permissions(dir, std::os::unix::fs::PermissionsExt::from_mode(0o700));
        }
        let lock = std::fs::OpenOptions::new().read(true).write(true).create(true).mode_0600()
            .open(path.with_extension("sock.lock")).map_err(StartError::Io)?;
        let lockfd = lock.into_raw_fd();
        if unsafe { libc::flock(lockfd, libc::LOCK_EX | libc::LOCK_NB) } != 0 {
            return Err(StartError::Locked);
        }
        let _ = std::fs::remove_file(path);
        let listener = UnixListener::bind(path).map_err(StartError::Io)?;
        let _ = std::fs::set_permissions(path, std::os::unix::fs::PermissionsExt::from_mode(0o600));
        listener.set_nonblocking(true).map_err(StartError::Io)?;
        Ok(CtlServer { path: path.to_path_buf(), _lockfd: lockfd, listener, client: None })
    }

    pub fn client_fd(&self) -> Option<RawFd> { self.client.as_ref().map(|(s, _)| s.as_raw_fd()) }

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

    pub fn close(&mut self) {
        self.client = None;
        let _ = std::fs::remove_file(&self.path);
    }
}

trait Mode0600 { fn mode_0600(&mut self) -> &mut Self; }
impl Mode0600 for std::fs::OpenOptions {
    fn mode_0600(&mut self) -> &mut Self { std::os::unix::fs::OpenOptionsExt::mode(self, 0o600) }
}

#[allow(dead_code)]
fn _keep(_: UnixStream) { let _ = unsafe { UnixStream::from_raw_fd(0) }; }
