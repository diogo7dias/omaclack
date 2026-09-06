//! Raw evdev reader: every /dev/input/event* we may open, key press and
//! release events only, device names via EVIOCGNAME.

use std::collections::HashMap;
use std::os::fd::RawFd;
use std::time::Instant;

const EV_KEY: u16 = 0x01;
const EVIOCGNAME: libc::c_ulong = 0x8100_4506; // _IOC(_IOC_READ, 'E', 0x06, 256)
const EVENT_SIZE: usize = 24; // struct input_event on 64-bit

pub struct InputReader {
    fds: HashMap<RawFd, String>,   // fd -> path
    names: HashMap<RawFd, String>, // fd -> device name
    last_scan: Option<Instant>,
    pub denied: bool,
}

impl InputReader {
    pub fn new() -> Self {
        InputReader { fds: HashMap::new(), names: HashMap::new(), last_scan: None, denied: false }
    }

    pub fn fds(&self) -> Vec<RawFd> { self.fds.keys().copied().collect() }
    pub fn name(&self, fd: RawFd) -> String { self.names.get(&fd).cloned().unwrap_or_default() }

    pub fn scan(&mut self) {
        if let Some(t) = self.last_scan {
            if t.elapsed().as_secs_f64() < super::RESCAN_S_PUB { return; }
        }
        self.last_scan = Some(Instant::now());
        let entries = match std::fs::read_dir("/dev/input") { Ok(e) => e, Err(_) => return };
        let open: std::collections::HashSet<String> = self.fds.values().cloned().collect();
        self.denied = false;
        for e in entries.flatten() {
            let name = e.file_name().to_string_lossy().to_string();
            if !name.starts_with("event") { continue; }
            let path = format!("/dev/input/{}", name);
            if open.contains(&path) { continue; }
            let c = std::ffi::CString::new(path.clone()).unwrap();
            let fd = unsafe { libc::open(c.as_ptr(), libc::O_RDONLY | libc::O_NONBLOCK | libc::O_CLOEXEC) };
            if fd < 0 {
                if std::io::Error::last_os_error().kind() == std::io::ErrorKind::PermissionDenied { self.denied = true; }
                continue;
            }
            self.names.insert(fd, device_name(fd));
            self.fds.insert(fd, path);
        }
    }

    /// (code, down) for key presses and releases in whatever is readable now.
    pub fn read_events(&mut self, fd: RawFd) -> Vec<(u16, bool)> {
        let mut buf = [0u8; EVENT_SIZE * 64];
        let n = unsafe { libc::read(fd, buf.as_mut_ptr() as *mut libc::c_void, buf.len()) };
        if n < 0 {
            if std::io::Error::last_os_error().kind() == std::io::ErrorKind::WouldBlock { return vec![]; }
            self.close(fd);
            return vec![];
        }
        if n == 0 { self.close(fd); return vec![]; }
        let mut out = Vec::new();
        let mut off = 0usize;
        while off + EVENT_SIZE <= n as usize {
            let etype = u16::from_ne_bytes([buf[off + 16], buf[off + 17]]);
            let code = u16::from_ne_bytes([buf[off + 18], buf[off + 19]]);
            let value = i32::from_ne_bytes([buf[off + 20], buf[off + 21], buf[off + 22], buf[off + 23]]);
            off += EVENT_SIZE;
            if etype != EV_KEY || (value != 0 && value != 1) { continue; }
            if code < super::KEY_MAX_KEYBOARD || (super::BTN_MOUSE_MIN..=super::BTN_MOUSE_MAX).contains(&code) {
                out.push((code, value == 1));
            }
        }
        out
    }

    fn close(&mut self, fd: RawFd) {
        self.fds.remove(&fd);
        self.names.remove(&fd);
        unsafe { libc::close(fd) };
    }
}

fn device_name(fd: RawFd) -> String {
    let mut buf = [0u8; 256];
    let r = unsafe { libc::ioctl(fd, EVIOCGNAME as _, buf.as_mut_ptr()) };
    if r < 0 { return String::new(); }
    let end = buf.iter().position(|&b| b == 0).unwrap_or(buf.len());
    String::from_utf8_lossy(&buf[..end]).trim().to_string()
}
