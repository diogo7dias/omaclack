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
        parse_events(&buf[..n as usize])
    }

    fn close(&mut self, fd: RawFd) {
        self.fds.remove(&fd);
        self.names.remove(&fd);
        unsafe { libc::close(fd) };
    }
}

/// Parse packed `struct input_event` bytes (24 bytes each on 64-bit).
pub fn parse_events(buf: &[u8]) -> Vec<(u16, bool)> {
    let mut out = Vec::new();
    let mut off = 0usize;
    while off + EVENT_SIZE <= buf.len() {
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

fn device_name(fd: RawFd) -> String {
    let mut buf = [0u8; 256];
    let r = unsafe { libc::ioctl(fd, EVIOCGNAME as _, buf.as_mut_ptr()) };
    if r < 0 { return String::new(); }
    let end = buf.iter().position(|&b| b == 0).unwrap_or(buf.len());
    String::from_utf8_lossy(&buf[..end]).trim().to_string()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn ev(etype: u16, code: u16, value: i32) -> [u8; EVENT_SIZE] {
        let mut b = [0u8; EVENT_SIZE];
        b[16..18].copy_from_slice(&etype.to_ne_bytes());
        b[18..20].copy_from_slice(&code.to_ne_bytes());
        b[20..24].copy_from_slice(&value.to_ne_bytes());
        b
    }

    #[test]
    fn keeps_key_down_and_up_drops_repeat_and_motion() {
        let mut buf = Vec::new();
        buf.extend_from_slice(&ev(EV_KEY, 30, 1));      // a down
        buf.extend_from_slice(&ev(EV_KEY, 30, 0));      // a up
        buf.extend_from_slice(&ev(EV_KEY, 30, 2));      // repeat
        buf.extend_from_slice(&ev(0x02, 0, 5));         // EV_REL
        buf.extend_from_slice(&ev(EV_KEY, 0x110, 1));   // BTN_LEFT
        buf.extend_from_slice(&ev(EV_KEY, 0x160, 1));   // joystick-ish
        buf.extend_from_slice(&ev(0x00, 0, 0));         // SYN
        assert_eq!(parse_events(&buf), vec![(30, true), (30, false), (0x110, true)]);
    }
}
