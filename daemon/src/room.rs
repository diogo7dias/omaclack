//! Room presets: a `pipewire -c <conf>` child providing a filter-chain sink
//! (low shelf, lowpass, convolver on a synthesised impulse response).

use std::io::Write;
use std::process::{Child, Command, Stdio};

pub const ROOM_SINK: &str = "omaclack.room";

pub struct Preset {
    pub id: &'static str,
    pub desc: &'static str,
    decay: f64,
    refl: &'static [f64],
    lp: f64,
    wet: f64,
    shelf: (f64, f64),
    cut: f64,
}

pub const ROOMS: &[Preset] = &[
    Preset { id: "desk", desc: "On the desk", decay: 0.022, refl: &[1.3, 2.9], lp: 9000.0, wet: 0.35, shelf: (180.0, 1.5), cut: 14000.0 },
    Preset { id: "tray", desc: "Deep aluminium tray", decay: 0.055, refl: &[2.1, 4.7, 7.9], lp: 4200.0, wet: 0.55, shelf: (140.0, 4.0), cut: 6500.0 },
    Preset { id: "wood", desc: "Wooden desk", decay: 0.040, refl: &[3.0, 6.5, 11.0], lp: 6000.0, wet: 0.45, shelf: (200.0, 3.0), cut: 9000.0 },
    Preset { id: "wall", desc: "Through a wall", decay: 0.120, refl: &[6.0, 13.0, 21.0, 34.0], lp: 1100.0, wet: 0.8, shelf: (120.0, 2.0), cut: 1400.0 },
];

/// Direct spike + early reflections + decaying noise tail, one-pole lowpassed. Deterministic.
fn synth_ir(path: &std::path::Path, p: &Preset) -> std::io::Result<()> {
    let rate = 48000.0;
    let n = (rate * (p.decay * 4.0).max(0.03)) as usize;
    let mut out = vec![0.0f64; n];
    out[0] = 1.0;
    for (i, ms) in p.refl.iter().enumerate() {
        let k = (rate * ms / 1000.0) as usize;
        if k < n { out[k] += 0.45 * p.wet / (i as f64 + 1.0); }
    }
    let mut seed: u64 = 7;
    for k in 1..n {
        // xorshift, uniform in -1..1
        seed ^= seed << 13; seed ^= seed >> 7; seed ^= seed << 17;
        let r = (seed % 20001) as f64 / 10000.0 - 1.0;
        out[k] += p.wet * 0.6 * r * (-(k as f64) / (rate * p.decay)).exp();
    }
    let a = (-2.0 * std::f64::consts::PI * p.lp / rate).exp();
    let mut y = 0.0;
    for v in out.iter_mut() { y = (1.0 - a) * *v + a * y; *v = y; }
    let peak = out.iter().fold(0.0f64, |m, v| m.max(v.abs())).max(1e-9);
    let mut f = std::fs::File::create(path)?;
    let data_len = (n * 2) as u32;
    f.write_all(b"RIFF")?; f.write_all(&(36 + data_len).to_le_bytes())?; f.write_all(b"WAVEfmt ")?;
    f.write_all(&16u32.to_le_bytes())?; f.write_all(&1u16.to_le_bytes())?; f.write_all(&1u16.to_le_bytes())?;
    f.write_all(&48000u32.to_le_bytes())?; f.write_all(&96000u32.to_le_bytes())?;
    f.write_all(&2u16.to_le_bytes())?; f.write_all(&16u16.to_le_bytes())?;
    f.write_all(b"data")?; f.write_all(&data_len.to_le_bytes())?;
    let mut pcm = Vec::with_capacity(n * 2);
    for v in &out { pcm.extend_from_slice(&((v / peak * 30000.0) as i16).to_le_bytes()); }
    f.write_all(&pcm)
}

fn conf(p: &Preset, ir: &str) -> String {
    format!(r#"context.properties = {{ log.level = 0 }}
context.spa-libs = {{ audio.convert.* = audioconvert/libspa-audioconvert support.* = support/libspa-support }}
context.modules = [
  {{ name = libpipewire-module-protocol-native }}
  {{ name = libpipewire-module-client-node }}
  {{ name = libpipewire-module-adapter }}
  {{ name = libpipewire-module-filter-chain
    args = {{
      node.description = "Omaclack room"
      media.name = "Omaclack room"
      filter.graph = {{
        nodes = [
          {{ type = builtin name = shelf label = bq_lowshelf control = {{ "Freq" = {sf} "Q" = 0.8 "Gain" = {sg} }} }}
          {{ type = builtin name = cut label = bq_lowpass control = {{ "Freq" = {cut} "Q" = 0.7 }} }}
          {{ type = builtin name = conv label = convolver config = {{ filename = "{ir}" gain = 1.0 }} }}
        ]
        links = [
          {{ output = "shelf:Out" input = "cut:In" }}
          {{ output = "cut:Out" input = "conv:In" }}
        ]
        inputs = [ "shelf:In" ]
        outputs = [ "conv:Out" ]
      }}
      audio.channels = 2
      audio.position = [ FL FR ]
      capture.props = {{ node.name = "{sink}" media.class = Audio/Sink node.description = "Omaclack room" }}
      playback.props = {{ node.name = "{sink}.out" node.passive = true }}
    }}
  }}
]
"#, sf = p.shelf.0, sg = p.shelf.1, cut = p.cut, ir = ir, sink = ROOM_SINK)
}

pub struct Room {
    name: String,
    proc: Option<Child>,
}

impl Room {
    pub fn new() -> Self { Room { name: "none".into(), proc: None } }
    pub fn name(&self) -> String { self.name.clone() }

    pub fn set(&mut self, name: &str) -> String {
        let preset = ROOMS.iter().find(|r| r.id == name);
        let name = if preset.is_some() { name.to_string() } else { "none".to_string() };
        let alive = self.proc.as_mut().map(|c| c.try_wait().ok().flatten().is_none()).unwrap_or(false);
        if name == self.name && (name == "none" || alive) { return self.name.clone(); }
        self.stop();
        if let Some(p) = preset { self.start(p); }
        self.name = name;
        self.name.clone()
    }

    fn start(&mut self, p: &Preset) {
        let dir = super::runtime_dir();
        let _ = std::fs::create_dir_all(&dir);
        let ir = dir.join(format!("room-{}.wav", p.id));
        let conf_path = dir.join(format!("room-{}.conf", p.id));
        if !ir.exists() { let _ = synth_ir(&ir, p); }
        let _ = std::fs::write(&conf_path, conf(p, &ir.to_string_lossy()));
        self.proc = Command::new("pipewire").arg("-c").arg(&conf_path)
            .stdin(Stdio::null()).stdout(Stdio::null()).stderr(Stdio::null()).spawn().ok();
    }

    pub fn stop(&mut self) {
        if let Some(mut c) = self.proc.take() {
            unsafe { libc::kill(c.id() as i32, libc::SIGTERM) };
            let deadline = std::time::Instant::now() + std::time::Duration::from_secs(1);
            loop {
                if c.try_wait().ok().flatten().is_some() { break; }
                if std::time::Instant::now() > deadline { let _ = c.kill(); let _ = c.wait(); break; }
                std::thread::sleep(std::time::Duration::from_millis(20));
            }
        }
    }
}
