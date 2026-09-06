//! Playback backends.
//!
//! SpawnPlayer: one `pw-play --volume g [--target t] file` per event, children
//! reaped on the next play. What the Python daemon did.
//!
//! InProcessPlayer: samples decoded once with libsndfile (the same decoder
//! pw-play uses) into 48 kHz stereo f32, and a single PipeWire stream owned
//! by this process mixes the active voices in its realtime callback. The
//! stream is deactivated after a short idle so the sink can suspend.

use crate::packs::Pack;
use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

const MAX_CONCURRENT: usize = 24;
pub const RATE: u32 = 48000;
const IDLE_S: f64 = 2.5;

pub trait Player {
    fn name(&self) -> &'static str;
    fn play(&mut self, path: &Path, volume: f32, pan: f32);
    fn preload(&mut self, _pack: &Pack) {}
    fn set_muted(&mut self, m: bool);
    fn muted(&self) -> bool;
    fn set_target(&mut self, target: Option<String>);
    fn stream_ok(&self) -> bool;
    fn shutdown(&mut self) {}
    /// Called from the main poll loop. In-process backend uses this to put the
    /// PipeWire stream to sleep; spawn backend is a no-op.
    fn tick(&mut self) {}
    /// Poll timeout in ms. Shorter while the stream is active so idle-sleep is prompt.
    fn poll_timeout_ms(&self) -> i32 { 3000 }
}

// ------------------------------------------------------------------ spawn

pub struct SpawnPlayer {
    muted: bool,
    target: Option<String>,
    live: Vec<Child>,
    ok: bool,
}

impl SpawnPlayer {
    pub fn new() -> Self { SpawnPlayer { muted: false, target: None, live: Vec::new(), ok: true } }
}

impl Player for SpawnPlayer {
    fn name(&self) -> &'static str { "spawn" }
    fn play(&mut self, path: &Path, volume: f32, _pan: f32) {
        let g = if self.muted { 0.0 } else { volume.clamp(0.0, 1.0) };
        if g == 0.0 { return; }
        self.live.retain_mut(|c| c.try_wait().ok().flatten().is_none());
        if self.live.len() >= MAX_CONCURRENT { return; }
        let mut cmd = Command::new("pw-play");
        cmd.arg("--volume").arg(format!("{:.3}", g));
        if let Some(t) = &self.target { cmd.arg("--target").arg(t); }
        cmd.arg(path).stdin(Stdio::null()).stdout(Stdio::null()).stderr(Stdio::null());
        match cmd.spawn() {
            Ok(c) => { self.live.push(c); self.ok = true; }
            Err(_) => self.ok = false,
        }
    }
    fn set_muted(&mut self, m: bool) { self.muted = m; }
    fn muted(&self) -> bool { self.muted }
    fn set_target(&mut self, target: Option<String>) { self.target = target; }
    fn stream_ok(&self) -> bool { self.ok }
}

// ------------------------------------------------------------------ libsndfile

#[repr(C)]
struct SfInfo { frames: i64, samplerate: i32, channels: i32, format: i32, sections: i32, seekable: i32 }

#[link(name = "sndfile")]
extern "C" {
    fn sf_open(path: *const libc::c_char, mode: libc::c_int, info: *mut SfInfo) -> *mut libc::c_void;
    fn sf_readf_float(f: *mut libc::c_void, ptr: *mut f32, frames: i64) -> i64;
    fn sf_close(f: *mut libc::c_void) -> libc::c_int;
}

/// Decode to interleaved stereo f32 at RATE. Mono is duplicated; other rates
/// are linearly resampled (shipped packs are already 48 kHz).
fn decode(path: &Path) -> Option<Arc<Vec<f32>>> {
    let c = std::ffi::CString::new(path.to_string_lossy().as_bytes()).ok()?;
    let mut info = SfInfo { frames: 0, samplerate: 0, channels: 0, format: 0, sections: 0, seekable: 0 };
    let f = unsafe { sf_open(c.as_ptr(), 0x10, &mut info) };
    if f.is_null() || info.channels <= 0 || info.frames <= 0 { return None; }
    let ch = info.channels as usize;
    let mut buf = vec![0f32; info.frames as usize * ch];
    let got = unsafe { sf_readf_float(f, buf.as_mut_ptr(), info.frames) } as usize;
    unsafe { sf_close(f) };
    buf.truncate(got * ch);
    let mut stereo: Vec<f32> = Vec::with_capacity(got * 2);
    for i in 0..got {
        let l = buf[i * ch];
        let r = if ch > 1 { buf[i * ch + 1] } else { l };
        stereo.push(l); stereo.push(r);
    }
    if info.samplerate as u32 != RATE && info.samplerate > 0 {
        let ratio = info.samplerate as f64 / RATE as f64;
        let out_frames = (got as f64 / ratio) as usize;
        let mut out = Vec::with_capacity(out_frames * 2);
        for i in 0..out_frames {
            let src = i as f64 * ratio;
            let i0 = src.floor() as usize;
            let i1 = (i0 + 1).min(got - 1);
            let t = (src - i0 as f64) as f32;
            for c in 0..2 { out.push(stereo[i0 * 2 + c] * (1.0 - t) + stereo[i1 * 2 + c] * t); }
        }
        stereo = out;
    }
    Some(Arc::new(stereo))
}

// ------------------------------------------------------------------ in-process

struct Voice { data: Arc<Vec<f32>>, pos: usize, gain: f32, pan: f32 }

pub struct Mix {
    voices: Vec<Voice>,
    last_activity: Instant,
    active: bool,          // stream currently active (set from the PipeWire thread)
}

enum Msg { Wake, Sleep, Reconnect(Option<String>), Quit }

pub struct InProcessPlayer {
    muted: bool,
    target: Option<String>,
    cache: HashMap<PathBuf, Arc<Vec<f32>>>,
    mix: Arc<Mutex<Mix>>,
    stream_on: Arc<std::sync::atomic::AtomicBool>,
    tx: pipewire::channel::Sender<Msg>,
    thread: Option<std::thread::JoinHandle<()>>,
    ok: Arc<std::sync::atomic::AtomicBool>,
}

impl InProcessPlayer {
    pub fn new() -> Result<Self, String> {
        let mix = Arc::new(Mutex::new(Mix { voices: Vec::new(), last_activity: Instant::now(), active: false }));
        let ok = Arc::new(std::sync::atomic::AtomicBool::new(false));
        let stream_on = Arc::new(std::sync::atomic::AtomicBool::new(false));
        let (tx, rx) = pipewire::channel::channel::<Msg>();
        let (ready_tx, ready_rx) = std::sync::mpsc::channel::<Result<(), String>>();
        let mix2 = mix.clone();
        let ok2 = ok.clone();
        let on2 = stream_on.clone();
        let thread = std::thread::Builder::new().name("omaclack-pw".into()).spawn(move || {
            pw_thread(mix2, rx, ready_tx, ok2, on2);
        }).map_err(|e| e.to_string())?;
        match ready_rx.recv_timeout(Duration::from_secs(3)) {
            Ok(Ok(())) => {}
            Ok(Err(e)) => return Err(e),
            Err(_) => return Err("pipewire thread did not start".into()),
        }
        Ok(InProcessPlayer { muted: false, target: None, cache: HashMap::new(), mix, stream_on, tx, thread: Some(thread), ok })
    }

    fn sample(&mut self, path: &Path) -> Option<Arc<Vec<f32>>> {
        if let Some(s) = self.cache.get(path) { return Some(s.clone()); }
        let s = decode(path)?;
        self.cache.insert(path.to_path_buf(), s.clone());
        Some(s)
    }
}

impl Player for InProcessPlayer {
    fn name(&self) -> &'static str { "inprocess" }

    fn play(&mut self, path: &Path, volume: f32, pan: f32) {
        let g = if self.muted { 0.0 } else { volume.clamp(0.0, 1.0) };
        if g == 0.0 { return; }
        let data = match self.sample(path) { Some(d) => d, None => return };
        let need_wake = {
            let mut m = self.mix.lock().unwrap();
            if m.voices.len() >= MAX_CONCURRENT { m.voices.remove(0); }
            m.voices.push(Voice { data, pos: 0, gain: g, pan });
            m.last_activity = Instant::now();
            !m.active
        };
        if need_wake {
            self.stream_on.store(true, std::sync::atomic::Ordering::Relaxed);
            let _ = self.tx.send(Msg::Wake);
        }
    }

    fn tick(&mut self) {
        if !self.stream_on.load(std::sync::atomic::Ordering::Relaxed) { return; }
        let sleep = {
            let m = self.mix.lock().unwrap();
            m.voices.is_empty() && m.last_activity.elapsed().as_secs_f64() > IDLE_S
        };
        if sleep { let _ = self.tx.send(Msg::Sleep); }
    }

    fn poll_timeout_ms(&self) -> i32 {
        if self.stream_on.load(std::sync::atomic::Ordering::Relaxed) { 500 } else { 3000 }
    }

    fn preload(&mut self, pack: &Pack) {
        // Decode the whole pack now so the first press of every key is instant.
        // Drop what the previous pack cached to keep memory to the packs in use.
        let files = pack.all_files();
        let keep: std::collections::HashSet<PathBuf> = files.iter().cloned().collect();
        let parent = files.first().and_then(|f| f.parent().map(|p| p.to_path_buf()));
        if let Some(root) = parent.as_ref().map(|p| if p.ends_with("up") { p.parent().unwrap().to_path_buf() } else { p.clone() }) {
            let is_mouse = root.to_string_lossy().contains("/mouse/");
            self.cache.retain(|k, _| keep.contains(k) || k.to_string_lossy().contains("/mouse/") != is_mouse);
        }
        for f in files { self.sample(&f); }
    }

    fn set_muted(&mut self, m: bool) { self.muted = m; }
    fn muted(&self) -> bool { self.muted }

    fn set_target(&mut self, target: Option<String>) {
        if target != self.target {
            self.target = target.clone();
            let _ = self.tx.send(Msg::Reconnect(target));
        }
    }

    fn stream_ok(&self) -> bool { self.ok.load(std::sync::atomic::Ordering::Relaxed) }

    fn shutdown(&mut self) {
        let _ = self.tx.send(Msg::Quit);
        if let Some(t) = self.thread.take() { let _ = t.join(); }
    }
}

// ------------------------------------------------------------------ PipeWire thread

struct StreamState {
    stream: pipewire::stream::StreamRc,
    _listener: pipewire::stream::StreamListener<Arc<Mutex<Mix>>>,
}

fn format_pod() -> Vec<u8> {
    use pipewire::spa;
    let mut info = spa::param::audio::AudioInfoRaw::new();
    info.set_format(spa::param::audio::AudioFormat::F32LE);
    info.set_rate(RATE);
    info.set_channels(2);
    let mut position = [0; spa::param::audio::MAX_CHANNELS];
    position[0] = spa::sys::SPA_AUDIO_CHANNEL_FL;
    position[1] = spa::sys::SPA_AUDIO_CHANNEL_FR;
    info.set_position(position);
    spa::pod::serialize::PodSerializer::serialize(
        std::io::Cursor::new(Vec::new()),
        &spa::pod::Value::Object(spa::pod::Object {
            type_: spa::sys::SPA_TYPE_OBJECT_Format,
            id: spa::sys::SPA_PARAM_EnumFormat,
            properties: info.into(),
        }),
    ).unwrap().0.into_inner()
}

fn make_stream(core: &pipewire::core::CoreRc, mix: Arc<Mutex<Mix>>, target: &Option<String>,
               ok: Arc<std::sync::atomic::AtomicBool>) -> Result<StreamState, pipewire::Error> {
    use pipewire as pw;
    let mut props = pw::properties::properties! {
        *pw::keys::MEDIA_TYPE => "Audio",
        *pw::keys::MEDIA_CATEGORY => "Playback",
        *pw::keys::MEDIA_ROLE => "Game",
        *pw::keys::NODE_NAME => "omaclack",
        *pw::keys::APP_NAME => "Omaclack",
        *pw::keys::NODE_LATENCY => "256/48000",
        *pw::keys::AUDIO_CHANNELS => "2",
    };
    if let Some(t) = target { props.insert("target.object", t.as_str()); }
    let stream = pw::stream::StreamRc::new(core.clone(), "omaclack", props)?;
    let ok2 = ok.clone();
    let listener = stream
        .add_local_listener_with_user_data(mix)
        .state_changed(move |_s, _mix, _old, new| {
            use pw::stream::StreamState as S;
            ok2.store(!matches!(new, S::Error(_)), std::sync::atomic::Ordering::Relaxed);
        })
        .process(|stream, mix| {
            let mut b = match stream.dequeue_buffer() { Some(b) => b, None => return };
            // Fill only what this cycle asks for (the quantum); filling the whole
            // buffer would queue audio ahead and add hundreds of ms of latency.
            let requested = b.requested() as usize;
            let datas = b.datas_mut();
            let stride = 8; // 2 ch * f32
            let d = &mut datas[0];
            let n_frames = match d.data() {
                Some(slice) => {
                    let max = slice.len() / stride;
                    let n = if requested > 0 { requested.min(max) } else { max.min(1024) };
                    // Zero, then add every live voice.
                    let out: &mut [f32] = unsafe { std::slice::from_raw_parts_mut(slice.as_mut_ptr() as *mut f32, n * 2) };
                    for v in out.iter_mut() { *v = 0.0; }
                    {
                        let mut m = mix.lock().unwrap();
                        for voice in m.voices.iter_mut() {
                            let src = &voice.data[voice.pos..];
                            let take = src.len().min(n * 2);
                            if voice.pan.abs() < 1e-4 {
                                for i in 0..take { out[i] += src[i] * voice.gain; }
                            } else {
                                // Equal-power stereo pan, same law as tools/build_sounds.py.
                                let gl = (((1.0 - voice.pan) / 2.0).max(0.0).sqrt() * 1.15) * voice.gain;
                                let gr = (((1.0 + voice.pan) / 2.0).max(0.0).sqrt() * 1.15) * voice.gain;
                                let mut i = 0;
                                while i + 1 < take {
                                    out[i] += src[i] * gl;
                                    out[i + 1] += src[i + 1] * gr;
                                    i += 2;
                                }
                            }
                            voice.pos += take;
                        }
                        m.voices.retain(|v| v.pos < v.data.len());
                    }
                    for v in out.iter_mut() { *v = v.clamp(-1.0, 1.0); }
                    n
                }
                None => 0,
            };
            let chunk = d.chunk_mut();
            *chunk.offset_mut() = 0;
            *chunk.stride_mut() = stride as _;
            *chunk.size_mut() = (stride * n_frames) as _;
        })
        .register()?;
    let values = format_pod();
    let mut params = [pw::spa::pod::Pod::from_bytes(&values).unwrap()];
    // INACTIVE: the stream is only activated while something plays (see the idle timer).
    let flags = pw::stream::StreamFlags::AUTOCONNECT | pw::stream::StreamFlags::MAP_BUFFERS
        | pw::stream::StreamFlags::RT_PROCESS | pw::stream::StreamFlags::INACTIVE;
    stream.connect(pw::spa::utils::Direction::Output, None, flags, &mut params)?;
    ok.store(true, std::sync::atomic::Ordering::Relaxed);
    Ok(StreamState { stream, _listener: listener })
}

fn pw_thread(mix: Arc<Mutex<Mix>>, rx: pipewire::channel::Receiver<Msg>,
             ready: std::sync::mpsc::Sender<Result<(), String>>, ok: Arc<std::sync::atomic::AtomicBool>,
             stream_on: Arc<std::sync::atomic::AtomicBool>) {
    use pipewire as pw;
    pw::init();
    let mainloop = match pw::main_loop::MainLoopRc::new(None) { Ok(m) => m, Err(e) => { let _ = ready.send(Err(e.to_string())); return } };
    let context = match pw::context::ContextRc::new(&mainloop, None) { Ok(c) => c, Err(e) => { let _ = ready.send(Err(e.to_string())); return } };
    let core = match context.connect_rc(None) { Ok(c) => c, Err(e) => { let _ = ready.send(Err(e.to_string())); return } };

    let state: std::rc::Rc<std::cell::RefCell<Option<StreamState>>> = std::rc::Rc::new(std::cell::RefCell::new(None));
    match make_stream(&core, mix.clone(), &None, ok.clone()) {
        Ok(s) => *state.borrow_mut() = Some(s),
        Err(e) => { let _ = ready.send(Err(e.to_string())); return }
    }
    let _ = ready.send(Ok(()));

    let ml = mainloop.clone();
    let core2 = core.clone();
    let mix_r = mix.clone();
    let state_r = state.clone();
    let ok_r = ok.clone();
    let on_r = stream_on.clone();
    let _rx = rx.attach(mainloop.loop_(), move |msg| match msg {
        Msg::Wake => {
            // Never call set_active while holding mix: the RT process callback
            // also takes mix, and set_active waits for that thread — deadlock.
            let already = mix_r.lock().unwrap().active;
            if !already {
                if let Some(s) = state_r.borrow().as_ref() { let _ = s.stream.set_active(true); }
                mix_r.lock().unwrap().active = true;
                on_r.store(true, std::sync::atomic::Ordering::Relaxed);
            }
        }
        Msg::Sleep => {
            let should = {
                let m = mix_r.lock().unwrap();
                m.active && m.voices.is_empty() && m.last_activity.elapsed().as_secs_f64() > IDLE_S
            };
            if should {
                if let Some(s) = state_r.borrow().as_ref() { let _ = s.stream.set_active(false); }
                mix_r.lock().unwrap().active = false;
                on_r.store(false, std::sync::atomic::Ordering::Relaxed);
            }
        }
        Msg::Reconnect(target) => {
            // Drop the old stream (disconnects) and connect a new one to the new target.
            *state_r.borrow_mut() = None;
            match make_stream(&core2, mix_r.clone(), &target, ok_r.clone()) {
                Ok(s) => {
                    let has_voices = !mix_r.lock().unwrap().voices.is_empty();
                    mix_r.lock().unwrap().active = false;
                    on_r.store(false, std::sync::atomic::Ordering::Relaxed);
                    if has_voices {
                        let _ = s.stream.set_active(true);
                        mix_r.lock().unwrap().active = true;
                        on_r.store(true, std::sync::atomic::Ordering::Relaxed);
                    }
                    *state_r.borrow_mut() = Some(s);
                }
                Err(_) => ok_r.store(false, std::sync::atomic::Ordering::Relaxed),
            }
        }
        Msg::Quit => ml.quit(),
    });
    mainloop.run();
}
