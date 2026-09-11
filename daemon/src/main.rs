//! omaclackd: keyboard and mouse sound daemon for the Omaclack Omarchy plugin.
//!
//! Reads /dev/input/event* directly and plays a per-key Opus sample on every
//! key press and mouse button press/release. Two playback backends:
//!   inprocess (default): one persistent PipeWire stream owned by this process,
//!                        samples pre-decoded with libsndfile, mixed in the
//!                        realtime callback, stream suspended when idle.
//!   spawn:               one short-lived `pw-play` per event (the design the
//!                        Python daemon used; kept for comparison).
//!
//! No logging, no network, nothing written to disk. Child of omarchy-shell;
//! the stdin pipe is both the control channel and the lifeline.

mod evdev;
mod ipc;
mod packs;
mod player;
mod stats;

use serde_json::{json, Value};
use std::collections::HashMap;
use std::io::Write;
use std::os::fd::{AsRawFd, RawFd};
use std::os::unix::net::UnixListener;
use std::path::PathBuf;
use std::time::{Duration, Instant};

pub const KEY_MAX_KEYBOARD: u16 = 0x100;
pub const BTN_MOUSE_MIN: u16 = 0x110;
pub const BTN_MOUSE_MAX: u16 = 0x117;
const DEBOUNCE_MS: f64 = 30.0;
const RESCAN_S: f64 = 3.0;
pub const RESCAN_S_PUB: f64 = RESCAN_S;
const RELEASE_GAIN: f32 = 0.7;   // mouse release relative to the press

pub fn runtime_dir() -> PathBuf {
    let base = std::env::var("XDG_RUNTIME_DIR").unwrap_or_else(|_| "/tmp".into());
    PathBuf::from(base).join("omaclack")
}

fn sounds_dir() -> PathBuf {
    let exe = std::env::current_exe().unwrap_or_else(|_| PathBuf::from("."));
    let exe = std::fs::canonicalize(&exe).unwrap_or(exe);
    // bin/omaclackd-<arch> -> plugin root
    exe.parent().and_then(|p| p.parent()).map(|p| p.join("sounds")).unwrap_or_else(|| PathBuf::from("sounds"))
}

fn user_dir() -> PathBuf {
    let base = std::env::var("XDG_CONFIG_HOME").ok().filter(|s| !s.is_empty()).map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from(std::env::var("HOME").unwrap_or_else(|_| "/".into())).join(".config"));
    base.join("omarchy").join("omaclack").join("packs")
}

struct KeyFilter {
    last: HashMap<u16, f64>,
}

impl KeyFilter {
    fn on_press(&mut self, code: u16, now_ms: f64) -> bool {
        if let Some(t) = self.last.get(&code) {
            if now_ms - t < DEBOUNCE_MS {
                return false;
            }
        }
        self.last.insert(code, now_ms);
        true
    }
}

pub struct Controller {
    player: Box<dyn player::Player>,
    pack_roots: Vec<PathBuf>,
    mouse_roots: Vec<PathBuf>,
    pack: Option<packs::Pack>,
    mouse_pack: Option<packs::Pack>,
    stats: stats::Stats,
    enabled: bool,
    mouse_enabled: bool,
    velocity: bool,
    volume: f32,
    mouse_volume: f32,
    last_press: Option<Instant>,
    devices: HashMap<String, u64>,
    epoch: Instant,
}

impl Controller {
    fn new(player: Box<dyn player::Player>, sounds: PathBuf, user: PathBuf) -> Self {
        let pack_roots = vec![sounds.clone(), user.clone()];
        let mouse_roots = vec![sounds.join("mouse"), user.join("mouse")];
        let mut c = Controller {
            player, pack_roots, mouse_roots, pack: None, mouse_pack: None,
            stats: stats::Stats::new(),
            enabled: true, mouse_enabled: true, velocity: true,
            volume: 0.7, mouse_volume: 0.7, last_press: None, devices: HashMap::new(), epoch: Instant::now(),
        };
        let first = packs::first_or_none(&packs::list_packs(&c.pack_roots), "mx-blue");
        if let Some(f) = first { let _ = c.load_pack(&f); }
        let first = packs::first_or_none(&packs::list_packs(&c.mouse_roots), "logitech");
        if let Some(f) = first { let _ = c.load_mouse_pack(&f); }
        c
    }

    fn load_pack(&mut self, name: &str) -> Result<String, String> {
        let p = packs::Pack::load(name, &self.pack_roots)?;
        self.player.preload(&p);
        self.pack = Some(p);
        Ok(name.to_string())
    }

    fn load_mouse_pack(&mut self, name: &str) -> Result<String, String> {
        let p = packs::Pack::load(name, &self.mouse_roots)?;
        self.player.preload(&p);
        self.mouse_pack = Some(p);
        Ok(name.to_string())
    }

    fn now_s(&self) -> f64 { self.epoch.elapsed().as_secs_f64() }

    fn status(&self) -> Value {
        let mut devs: Vec<(&String, &u64)> = self.devices.iter().collect();
        devs.sort_by(|a, b| b.1.cmp(a.1));
        json!({
            "ok": true,
            "pack": self.pack.as_ref().map(|p| p.name.clone()),
            "packs": packs::list_packs(&self.pack_roots).iter().map(|p| packs::pack_meta(p, &self.pack_roots)).collect::<Vec<_>>(),
            "mouse_pack": self.mouse_pack.as_ref().map(|p| p.name.clone()),
            "mouse_packs": packs::list_packs(&self.mouse_roots).iter().map(|p| packs::pack_meta(p, &self.mouse_roots)).collect::<Vec<_>>(),
            "volume": (self.volume * 100.0).round() as i64,
            "mouse_volume": (self.mouse_volume * 100.0).round() as i64,
            "muted": self.player.muted(),
            "enabled": self.enabled,
            "mouse": self.mouse_enabled,
            "velocity": self.velocity,
            "devices": devs.iter().map(|d| d.0.clone()).collect::<Vec<_>>(),
            "user_dir": self.pack_roots[1].to_string_lossy(),
            "backend": self.player.name(),
            "stream": self.player.stream_ok(),
        })
    }

    fn velocity_gain(&self, now: Instant) -> f32 {
        velocity_gain(self.velocity, self.last_press.map(|t| now.duration_since(t).as_secs_f32()))
    }

    /// Returns the latency in ms from `t0` to the sample being handed to the backend.
    fn play(&mut self, code: u16, t0: Instant, down: bool, device: Option<&str>) -> Option<f64> {
        if !self.enabled { return None; }
        let is_mouse = code >= KEY_MAX_KEYBOARD;
        if is_mouse && !self.mouse_enabled { return None; }
        let mut volume = if is_mouse { self.mouse_volume } else { self.volume };
        let pack = if is_mouse { self.mouse_pack.as_ref().or(self.pack.as_ref()) } else { self.pack.as_ref() }?;
        if down {
            if !is_mouse {
                volume *= self.velocity_gain(t0);
                self.last_press = Some(t0);
                let now_s = self.now_s();
                self.stats.press(code, now_s);
                if let Some(d) = device { if !d.is_empty() { *self.devices.entry(d.to_string()).or_insert(0) += 1; } }
            }
        } else {
            // Keys sound on press only; mouse buttons click on press and release.
            if !is_mouse { return None; }
            volume *= RELEASE_GAIN;
        }
        let pan = pack.pan_for(code);
        let path = pack.sample_for(code, down)?;
        self.player.play(path, volume, pan);
        Some((Instant::now().duration_since(t0).as_secs_f64() * 1000.0 * 100.0).round() / 100.0)
    }

    fn handle(&mut self, msg: &Value) -> Value {
        let cmd = msg.get("cmd").and_then(|c| c.as_str()).unwrap_or("");
        let pct = |v: Option<&Value>, d: i64| v.and_then(|x| x.as_f64()).map(|x| x as i64).unwrap_or(d).clamp(0, 100);
        let flag = |v: Option<&Value>, d: bool| v.and_then(|x| x.as_bool()).unwrap_or(d);
        match cmd {
            "play" => {
                let key = msg.get("key").and_then(|k| k.as_u64()).unwrap_or(0) as u16;
                let down = msg.get("down").and_then(|d| d.as_bool()).unwrap_or(true);
                let lat = self.play(key, Instant::now(), down, None);
                json!({"ok": lat.is_some(), "latency_ms": lat})
            }
            "load" => match self.load_pack(msg.get("pack").and_then(|p| p.as_str()).unwrap_or("")) {
                Ok(p) => json!({"ok": true, "pack": p}),
                Err(e) => json!({"ok": false, "error": e}),
            },
            "mousepack" => match self.load_mouse_pack(msg.get("pack").and_then(|p| p.as_str()).unwrap_or("")) {
                Ok(p) => json!({"ok": true, "mouse_pack": p}),
                Err(e) => json!({"ok": false, "error": e}),
            },
            "volume" => { let v = pct(msg.get("value"), 70); self.volume = v as f32 / 100.0; json!({"ok": true, "volume": v}) }
            "mousevolume" => { let v = pct(msg.get("value"), 70); self.mouse_volume = v as f32 / 100.0; json!({"ok": true, "mouse_volume": v}) }
            "mute" => { let m = flag(msg.get("toggle"), true); self.player.set_muted(m); json!({"ok": true, "muted": m}) }
            "enable" => { self.enabled = flag(msg.get("value"), true); json!({"ok": true, "enabled": self.enabled}) }
            "mouse" => { self.mouse_enabled = flag(msg.get("value"), true); json!({"ok": true, "mouse": self.mouse_enabled}) }
            "velocity" => { self.velocity = flag(msg.get("value"), true); json!({"ok": true, "velocity": self.velocity}) }
            "stats" => {
                let mut r = self.stats.report(self.now_s());
                let mut devs: Vec<(&String, &u64)> = self.devices.iter().collect();
                devs.sort_by(|a, b| b.1.cmp(a.1));
                r["devices"] = json!(devs.iter().map(|d| d.0.clone()).collect::<Vec<_>>());
                r
            }
            "status" => self.status(),
            "theme" => {
                self.play(28, Instant::now(), true, None);
                json!({"ok": true, "evt": "theme", "slug": msg.get("slug").and_then(|s| s.as_str()).unwrap_or("")})
            }
            "ping" => json!({"ok": true, "pong": msg.get("t").cloned().unwrap_or(Value::Null)}),
            "quit" => json!({"ok": true, "quit": true}),
            _ => json!({"ok": false, "error": "unknown cmd"}),
        }
    }
}

fn main() {
    let mut sock_path = runtime_dir().join("ctl.sock");
    let mut backend = "inprocess".to_string();
    for a in std::env::args().skip(1) {
        if let Some(p) = a.strip_prefix("--socket=") { sock_path = PathBuf::from(p); }
        if let Some(b) = a.strip_prefix("--backend=") { backend = b.to_string(); }
    }
    // The lock decides who owns the socket; taken before anything else spawns.
    let mut server = match ipc::CtlServer::start(&sock_path) {
        Ok(s) => s,
        Err(ipc::StartError::Locked) => std::process::exit(2),
        Err(ipc::StartError::Io(e)) => { eprintln!("omaclackd: {}", e); std::process::exit(1) }
    };
    let player: Box<dyn player::Player> = match backend.as_str() {
        "spawn" => Box::new(player::SpawnPlayer::new()),
        _ => match player::InProcessPlayer::new() {
            Ok(p) => Box::new(p),
            Err(_) => Box::new(player::SpawnPlayer::new()),   // no PipeWire: fall back to pw-play
        },
    };
    let mut ctl = Controller::new(player, sounds_dir(), user_dir());
    let mut keys = KeyFilter { last: HashMap::new() };
    let mut inputs = evdev::InputReader::new();
    // Scan once before hello so `denied` is real, not the default false.
    inputs.scan();

    unsafe {
        libc::signal(libc::SIGTERM, handle_signal as *const () as usize);
        libc::signal(libc::SIGINT, handle_signal as *const () as usize);
        libc::signal(libc::SIGHUP, handle_signal as *const () as usize);
        libc::signal(libc::SIGPIPE, libc::SIG_IGN);
    }

    // stdin as a pipe means omarchy-shell spawned us: it is the control channel and the lifeline.
    let mut pipe = if ipc::stdin_is_pipe() { Some(ipc::LineChannel::new(0, unsafe { libc::dup(1) })) } else { None };
    if let Some(p) = pipe.as_mut() {
        let mut hello = ctl.status();
        hello["evt"] = json!("hello");
        hello["denied"] = json!(inputs.denied);
        p.send(&hello);
    }
    // Our stdout now belongs to the shell protocol.
    let _ = std::io::stdout().flush();

    let mut running = true;
    while running && !stop_requested() {
        inputs.scan();
        let mut pfds: Vec<libc::pollfd> = Vec::new();
        let mut roles: Vec<Role> = Vec::new();
        for fd in inputs.fds() { pfds.push(pollfd(fd)); roles.push(Role::Input(fd)); }
        pfds.push(pollfd(server.listener.as_raw_fd())); roles.push(Role::Listener);
        if let Some(c) = server.client_fd() { pfds.push(pollfd(c)); roles.push(Role::Client); }
        if let Some(p) = pipe.as_ref() { pfds.push(pollfd(p.rfd)); roles.push(Role::Pipe); }
        let n = unsafe { libc::poll(pfds.as_mut_ptr(), pfds.len() as libc::nfds_t, ctl.player.poll_timeout_ms()) };
        if n < 0 { continue; }
        ctl.player.tick();
        let now = Instant::now();
        let now_ms = ctl.now_s() * 1000.0;
        for (i, pfd) in pfds.iter().enumerate() {
            if pfd.revents == 0 { continue; }
            match roles[i] {
                Role::Listener => {
                    if server.accept() {
                        let mut hello = ctl.status();
                        hello["evt"] = json!("hello");
                        hello["denied"] = json!(inputs.denied);
                        server.send(&hello);
                    }
                }
                Role::Client => {
                    let msgs = server.read_messages();
                    for m in msgs {
                        let mut resp = ctl.handle(&m);
                        resp["id"] = m.get("id").cloned().unwrap_or(Value::Null);
                        if m.get("cmd").and_then(|c| c.as_str()) == Some("status") {
                            resp["denied"] = json!(inputs.denied);
                        }
                        let theme = resp.get("evt").and_then(|e| e.as_str()) == Some("theme");
                        let quit = resp.get("quit").and_then(|q| q.as_bool()).unwrap_or(false);
                        server.send(&resp);
                        if theme { emit(&mut server, &mut pipe, &json!({"evt": "theme", "slug": resp["slug"]})); }
                        if quit { running = false; }
                    }
                }
                Role::Pipe => {
                    let msgs = pipe.as_mut().unwrap().read_messages();
                    let eof = pipe.as_ref().unwrap().eof;
                    if eof { running = false; }
                    let mut themes: Vec<Value> = Vec::new();
                    for m in msgs {
                        let mut resp = ctl.handle(&m);
                        resp["id"] = m.get("id").cloned().unwrap_or(Value::Null);
                        if m.get("cmd").and_then(|c| c.as_str()) == Some("status") {
                            resp["denied"] = json!(inputs.denied);
                        }
                        let theme = resp.get("evt").and_then(|e| e.as_str()) == Some("theme");
                        let quit = resp.get("quit").and_then(|q| q.as_bool()).unwrap_or(false);
                        pipe.as_mut().unwrap().send(&resp);
                        if theme { themes.push(resp["slug"].clone()); }
                        if quit { running = false; }
                    }
                    for slug in themes {
                        emit(&mut server, &mut pipe, &json!({"evt": "theme", "slug": slug}));
                    }
                }
                Role::Input(fd) => {
                    let name = inputs.name(fd);
                    for (code, down) in inputs.read_events(fd) {
                        if down && !keys.on_press(code, now_ms) { continue; }
                        let lat = ctl.play(code, now, down, if down { Some(name.as_str()) } else { None });
                        if let (Some(l), true) = (lat, down) {
                            // Latency only, and only on the parent pipe: the control
                            // socket must not be a live keystream.
                            emit_pipe(&mut pipe, &json!({"evt": "key", "latency_ms": l}));
                        }
                    }
                }
            }
        }
    }
    ctl.player.shutdown();
    server.close();
}

enum Role { Input(RawFd), Listener, Client, Pipe }

fn velocity_gain(enabled: bool, dt_s: Option<f32>) -> f32 {
    if !enabled { return 1.0; }
    match dt_s {
        None => 1.0,
        Some(dt) => 0.8 + 0.2 * ((0.5 - dt) / 0.4).clamp(0.0, 1.0),
    }
}

fn pollfd(fd: RawFd) -> libc::pollfd { libc::pollfd { fd, events: libc::POLLIN, revents: 0 } }

fn emit_pipe(pipe: &mut Option<ipc::LineChannel>, v: &Value) {
    if let Some(p) = pipe.as_mut() { p.send(v); }
}

fn emit(server: &mut ipc::CtlServer, pipe: &mut Option<ipc::LineChannel>, v: &Value) {
    server.send(v);
    emit_pipe(pipe, v);
}

static STOP: std::sync::atomic::AtomicBool = std::sync::atomic::AtomicBool::new(false);
extern "C" fn handle_signal(_: libc::c_int) { STOP.store(true, std::sync::atomic::Ordering::SeqCst); }
fn stop_requested() -> bool { STOP.load(std::sync::atomic::Ordering::SeqCst) }

#[allow(dead_code)]
fn _unused(_: Duration, _: &UnixListener) {}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn debounce_same_key_only() {
        let mut k = KeyFilter { last: HashMap::new() };
        assert!(k.on_press(30, 0.0));
        assert!(!k.on_press(30, 10.0));
        assert!(k.on_press(31, 11.0));
        assert!(k.on_press(30, 31.0));
    }

    struct FakePlayer(std::rc::Rc<std::cell::RefCell<Vec<(PathBuf, f32)>>>);
    impl player::Player for FakePlayer {
        fn name(&self) -> &'static str { "fake" }
        fn play(&mut self, path: &std::path::Path, volume: f32, _pan: f32) { self.0.borrow_mut().push((path.to_path_buf(), volume)); }
        fn set_muted(&mut self, _m: bool) {}
        fn muted(&self) -> bool { false }
        fn stream_ok(&self) -> bool { true }
    }

    #[test]
    fn keys_press_only_mouse_press_and_release() {
        let played = std::rc::Rc::new(std::cell::RefCell::new(Vec::new()));
        let sounds = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("..").join("sounds");
        let mut c = Controller::new(Box::new(FakePlayer(played.clone())), sounds, std::env::temp_dir().join("omaclack-no-user-packs"));
        c.velocity = false;
        let t = Instant::now();
        assert!(c.play(30, t, true, None).is_some());
        assert!(c.play(30, t, false, None).is_none());
        assert!(c.play(272, t, true, None).is_some());
        assert!(c.play(272, t, false, None).is_some());
        let p = played.borrow();
        assert_eq!(p.len(), 3);
        assert!(p[2].0.ends_with("up/left.opus"));
        assert!((p[2].1 - 0.7 * RELEASE_GAIN).abs() < 1e-5);
    }

    #[test]
    fn velocity_full_when_fast_relaxed_when_slow() {
        assert_eq!(velocity_gain(false, Some(0.05)), 1.0);
        assert_eq!(velocity_gain(true, None), 1.0);
        assert!((velocity_gain(true, Some(0.05)) - 1.0).abs() < 1e-5);
        assert!((velocity_gain(true, Some(0.95)) - 0.8).abs() < 1e-5);
        let mid = velocity_gain(true, Some(0.30));
        assert!(mid > 0.8 && mid < 1.0);
    }
}
