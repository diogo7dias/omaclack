# Omaclack

Mechanical keyboard and mouse click sounds for [Omarchy](https://omarchy.org)
(Quattro shell). 25 keyboard packs and 10 mouse packs cut from real switch
recordings, press and release for every key, panned by key position. The
default Rust daemon mixes into one PipeWire stream; other CPUs fall back to
one `pw-play` per event. No network, no logging. About 4 MB installed.

```bash
omarchy plugin add https://github.com/diogo7dias/omaclack.git --enable
```

Bar widget with a keycap-and-waveform glyph. Left-click opens the panel,
right-click toggles, the wheel nudges keyboard volume. The panel has:

- keyboard volume and pack, with a hint for the keyboard you are actually typing on
- feel: velocity (louder when you type fast), release sounds, and room presets
  (on the desk, deep tray, wooden desk, through a wall) rendered live by a
  PipeWire filter chain
- mouse clicks with their own pack and volume
- quiet: mute while any app records the microphone, and quiet hours
- ignored apps by Wayland app id
- a typing card (keys per minute, rhythm, top keys) and a latency chart
- theme binding: pick a pack per Omarchy theme, switched by a theme-set hook
- import of any Mechvibes or MechvibesDX pack into your own packs folder

## Install

```bash
omarchy plugin add https://github.com/diogo7dias/omaclack.git --enable
```

Manual: copy this directory to `~/.config/omarchy/plugins/io.github.diogo7dias.omaclack/`,
then `omarchy-shell shell rescanPlugins` and `omarchy plugin enable io.github.diogo7dias.omaclack`.

The widget lands in the bar's right section. Move it with `omarchy bar move`.

### Input access (one-time)

The helper reads `/dev/input/event*` directly. Your user needs the `input` group:

```bash
sudo usermod -aG input "$USER"   # then log out and back in
```

Until then the panel shows `no /dev/input access` and nothing plays.

### Requirements

- Omarchy Quattro (`omarchy-shell`).
- PipeWire and a libsndfile that decodes Opus (standard on Omarchy).
- x86_64 for the Rust daemon; anything else runs the Python 3.10+ fallback
  (stdlib only, needs `pw-play`).

No sudo at runtime, no systemd units, no extra packages.

## How it works

```
omarchy-shell
 └─ Service.qml               reactive state, settings file, JSON over the daemon's pipe
     └─ bin/omaclackd         launcher: Rust build for this CPU, else the Python daemon
         ├─ omaclackd-x86_64  Rust: evdev + JSON protocol + one persistent PipeWire stream
         │                    (samples pre-decoded with libsndfile, mixed in the RT callback)
         └─ omaclackd.py      Python fallback: same protocol, one `pw-play` per event
```

- **Service.qml** spawns the daemon with `Quickshell.Io.Process` and talks JSON
  lines over that process's own stdin/stdout. The pipe is also the lifeline:
  shell exits, pipe closes, daemon exits. Nothing to connect or reconnect.
  State persists to `~/.config/omarchy/omaclack.json`.
  The daemon additionally binds `$XDG_RUNTIME_DIR/omaclack/ctl.sock` with the
  same protocol for the CLI, tests and the benchmark.
- **omaclackd** opens every `/dev/input/event*` it can, `select()`s on them,
  keeps only key-down events for keyboard codes (`< 0x100`) and mouse buttons
  (`BTN_LEFT..BTN_TASK`). Same key within 30 ms is dropped; every other press
  sounds, modifiers included, and so does the release (`up/<code>.opus`, 30%
  quieter). Mouse buttons play from a separate mouse pack. Velocity scales a
  key from 80% (unhurried) to 100% (the previous key was under 100 ms ago).
- **Playback (Rust, default)**: every sample of the current keyboard and
  mouse pack is decoded once with libsndfile (the decoder pw-play uses) into
  48 kHz stereo float. One PipeWire stream owned by the daemon mixes the live
  voices in its realtime callback, filling exactly the frames each cycle asks
  for. The stream is created inactive and only activated while something
  plays; 2.5 s after the last key it deactivates so the sink can suspend.
  Room presets reconnect the stream to the room sink.
- **Playback (Python fallback)**: `pw-play --volume <gain> <key>.opus` per
  event, capped at 24 in flight. No audio passes through Python.
- **Rooms**: a preset spawns `pipewire -c <generated conf>` with a
  filter-chain sink (low shelf, lowpass, convolver on a synthesised impulse
  response written to `$XDG_RUNTIME_DIR/omaclack`), and pw-play targets it.
  The sink suspends when idle and the child dies with the preset or the daemon.
- **Ignore list and calls**: the service watches the focused Wayland toplevel
  and mutes the daemon while its app id is listed, and, via Quickshell's
  PipeWire binding, while any `Stream/Input/Audio` node exists (an app
  recording the microphone). The daemon never learns which app is focused.
- **Stats** live in the daemon's memory only: a five-minute ring of press
  timestamps and a per-key counter for the session. Nothing is written.

## IPC protocol

Newline-delimited JSON, same shape on the shell's stdin/stdout pipe and on the
socket (one socket client at a time), `flock()` on `ctl.sock.lock`.

```json
{"cmd": "play", "key": 30}            → {"ok": true, "latency_ms": 0.4}
{"cmd": "load", "pack": "topre"}      → {"ok": true, "pack": "topre"}
{"cmd": "mousepack", "pack": "razer"} → {"ok": true, "mouse_pack": "razer"}
{"cmd": "volume", "value": 70}        → {"ok": true, "volume": 70}          keyboard
{"cmd": "mousevolume", "value": 40}   → {"ok": true, "mouse_volume": 40}    mouse buttons
{"cmd": "play", "key": 30, "down": false} → release sample
{"cmd": "velocity", "value": true}    → {"ok": true, "velocity": true}
{"cmd": "release", "value": true}     → {"ok": true, "release": true}
{"cmd": "room", "value": "wood"}      → {"ok": true, "room": "wood"}       none|desk|tray|wood|wall
{"cmd": "stats"}                      → {"ok": true, "kpm": 61, "total": 812, "rhythm": [...10], "top": [[30, 90], ...]}
{"cmd": "theme", "slug": "tokyo-night"} → plays a key, emits {"evt": "theme", "slug": ...}
{"cmd": "mute", "toggle": true}       → {"ok": true, "muted": true}
{"cmd": "mouse", "value": false}      → {"ok": true, "mouse": false}
{"cmd": "enable", "value": true}      → {"ok": true, "enabled": true}
{"cmd": "status"}                     → {"ok": true, "pack": ..., "packs": [...], ...}
{"cmd": "ping", "t": 123}             → {"ok": true, "pong": 123}
{"cmd": "quit"}                       → {"ok": true, "quit": true}
```

Daemon-initiated events: `{"evt": "hello", ...status, "denied": bool}` on
connect (status includes `packs`, `mouse_packs`, `rooms`, `devices`),
`{"evt": "key", "key": 30, "latency_ms": 0.4}` per sounded press,
`{"evt": "theme", "slug": ...}` after the theme hook.
Latency is measured from the evdev read to the pw-play spawn.

Try it by hand:

```bash
python3 bin/omaclackd --socket=/tmp/st.sock &
printf '{"cmd":"play","key":57}\n{"cmd":"quit"}\n' | nc -U /tmp/st.sock
```

## Sound packs

```
sounds/<pack>/<code>.opus        press   (stereo 48 kHz Opus, ~1 KB, panned by key position)
sounds/<pack>/up/<code>.opus     release
sounds/<pack>/{,up/}default.opus fallback for unmapped keys
sounds/<pack>/pack.json          name, credit, source, license, release: recorded|derived, optional keys map
sounds/mouse/<pack>/...          left/right/middle(.opus) + up/, keys map to BTN_* codes
```

Keycodes are Linux `KEY_*` numbers (`30` = A, `57` = space, `28` = enter).
Your own packs go in `~/.config/omarchy/omaclack/packs/` (and `packs/mouse/`)
and show up with a dot after their name. Import any Mechvibes, MechvibesDX or
plain folder-of-wavs pack:

```bash
tools/omaclack-import ~/Downloads/some-mechvibes-pack.zip          # keyboard
tools/omaclack-import ~/Downloads/clicks --mouse --name "My mouse"  # mouse
```

Then click "rescan packs" in the panel. Needs `ffmpeg`.

### Keyboard packs (25)

| pack | name | source |
|---|---|---|
| `mx-blue`, `mx-blue-pbt`, `mx-brown`, `mx-brown-pbt`, `mx-red`, `mx-black`, `mx-black-pbt` | Cherry MX Blue/Brown/Red/Black, ABS and PBT caps | [MechvibesDX](https://github.com/hainguyents13/mechvibes-dx), per key, press + release |
| `topre`, `eg-oreo`, `eg-crystal-purple` | Topre, Everglide Oreo, Everglide Crystal Purple | MechvibesDX, per key, press + release |
| `mx-red-pbt`, `nk-cream` | Cherry MX Red PBT, Novelkeys Cream | [Mechvibes](https://github.com/hainguyents13/mechvibes), per key, release derived |
| `holy-panda`, `buckling-spring`, `box-navy`, `alps-blue`, `alpaca`, `ink-black`, `ink-red`, `nk-cream-kbsim`, `mx-black-kbsim`, `mx-blue-kbsim`, `mx-brown-kbsim`, `topre-kbsim`, `turquoise` | Holy Panda, Buckling Spring, Kailh Box Navy, Alps Blue, Alpaca, Gateron Ink Black/Red, NK Cream, Cherry MX Black/Blue/Brown, Topre, Tecsee Turquoise | [kbsim](https://github.com/tplai/kbsim), per row, press + release |

"Release derived" means no release was recorded, so the release is a short,
quieter, slightly higher copy of the press. The panel says so under the chips.

### Mouse packs (10)

| pack | recording |
|---|---|
| `logitech` | OwlStorm, [Freesound 320146](https://freesound.org/s/320146/), CC0, via Typetone |
| `razer` | Katsuhira, [Freesound 555394](https://freesound.org/s/555394/), CC0, via Typetone |
| `crisp` | Six Ways, [Freesound 223445](https://freesound.org/s/223445/), CC0, via Typetone |
| `soft`, `deep` | Breviceps, [Freesound 447938](https://freesound.org/s/447938/), CC0, via Typetone |
| `studio` | 1j01, [OpenGameArt middle click](https://opengameart.org/content/middle-mouse-click), CC0, via Typetone |
| `wooden`, `ping`, `chat`, `vibrate` | MechvibesDX mouse packs, MIT |

Each mouse sample is split into the press and its release so one physical
click is one sound.

Rebuild everything from upstream checkouts with
`tools/build_sounds.py <mechvibes> <mechvibes-dx> <kbsim> <typetone>` (needs
`ffmpeg` and `libsndfile`, build-time only; every file is checked to open in
libsndfile because that is what `pw-play` decodes with).

## Credits

- **Mechvibes** and **MechvibesDX** by [hainguyents13](https://github.com/hainguyents13),
  MIT. The Cherry MX, Topre, Everglide and NK Cream packs are sliced from their
  sprites using MechvibesDX's press/release timings; the wooden, ping, chat and
  vibrate mouse packs are MechvibesDX's.
- **kbsim** by [Thomas Lai](https://github.com/tplai/kbsim), MIT. Thirteen
  packs are its `press/` and `release/` samples.
- **Omarchy Typetone** by [phuclh](https://github.com/phuclh/omarchy-typetone),
  MIT. The mouse packs are its `mouse-sounds/` renders of CC0 Freesound
  recordings by OwlStorm, Katsuhira and Six Ways.

Both licenses are reproduced in `sounds/LICENSES.md`.

## Development

```bash
python3 -m unittest discover -s tests            # Python daemon internals + protocol, pack checks
OMACLACKD_BIN=bin/omaclackd-x86_64 python3 -m unittest discover -s tests   # protocol tests against the Rust build
tools/bench_daemon.py rust bin/omaclackd-x86_64  # startup, footprint, key-to-sound onset
tools/build_sounds.py <mechvibes> <mechvibes-dx> <kbsim> <typetone>   # re-import all packs (ffmpeg)
tools/omaclack-import <folder-or-zip> [--mouse]  # add a pack to ~/.config/omarchy/omaclack/packs
tools/omaclack-theme-hook <slug>                 # what the Omarchy theme-set hook runs
tools/dev-reload                                 # nudge the shell when the plugin dir is a symlink
```

Saving any file under `~/.config/omarchy/plugins/<id>/` hot-reloads the whole
plugin, service and daemon included. Two gotchas when the plugin directory is a
**symlink** to your checkout: the shell's `inotifywait -r` does not follow
symlinks, so edits go unnoticed until you run `tools/dev-reload` (recreates the
symlink, which the watcher does see); and a QML file that fails to compile can
stay cached in the running shell after you fix it, so if the panel still will
not open after a reload, `omarchy restart shell`.

## Why Rust, measured

`tools/bench_daemon.py <label> <command>` runs a build against a private null
sink and reports startup, footprint, idle CPU, command round trip and
key-to-sound onset (command sent to first sample on the sink monitor,
including the capture path, which is identical for every backend). On this
laptop (Ryzen, PipeWire 1.6.8), re-measured 2026-09-06:

| build | startup | RSS | idle CPU / 10 s | cmd round trip p50 | onset p50 | onset p95 | onset after 4 s idle |
|---|---|---|---|---|---|---|---|
| Python, pw-play per key | 20 ms | 17.4 MB | 0 ms | 1.0 ms | 39 ms | 42 ms | 39 ms |
| Rust, pw-play per key | 2 ms | 4.5 MB | 0 ms | 0.7 ms | 39 ms | 41 ms | 38 ms |
| Rust, in-process stream | 50 ms | 18.0 MB | 0 ms | 0.2 ms | **19 ms** | **22 ms** | — |

Idle CPU is zero in all three. RSS matches the earlier table exactly. The
language change alone does not cut key-to-sound: Python and Rust-spawn both
land at 39 ms p50 here. The win is architectural. Not spawning pw-play saves
its process start and PipeWire connection on every key, cutting key-to-sound
from ~39 ms to ~19 ms. In-process cold onset (first key after 4 s idle) was
not independently confirmed this run: the null-sink detector saw extra bursts
once the stream reactivated, so that cell is left blank rather than guessed.
The Rust in-process build spends its memory on pre-decoded samples for the
two active packs; the Python daemon spends the same on the interpreter.

Build: `cd daemon && cargo build --release && cp target/release/omaclackd
../bin/omaclackd-x86_64`. Needs libpipewire and libsndfile headers. The
committed binary is for x86_64; other CPUs fall back to Python automatically.

## Theme hook

The panel's "theme hook not installed" chip runs
`omarchy hook install theme-set tools/omaclack-theme-hook`. After that every
theme change plays a key and the panel offers "use this pack for theme X";
bound themes switch packs automatically. Bindings live in `omaclack.json`.

## Privacy

Reads keycodes only to pick a sample; never stores, logs, or transmits them.
The socket is `0600` inside `$XDG_RUNTIME_DIR`. No network access anywhere.

## License

MIT
