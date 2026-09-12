# Omaclack

Mechanical keyboard and mouse click sounds for [Omarchy](https://omarchy.org)
(Quattro shell). Seven keyboard packs and four mouse packs cut from real
switch and button recordings: every key is its own sample, keys sound on
press, panned by key position;
mouse buttons click on press and release. The default Rust daemon mixes into one PipeWire stream; other CPUs
fall back to one `pw-play` per event. Everything stays on this machine: no
network, no logging, no keystrokes written to disk. A 1.4 MB clone, 5.5 MB on
disk (the packs are 677 KB of audio in 693 files, so most of that is your
filesystem rounding each one up to a block).

```bash
omarchy plugin add https://github.com/diogo7dias/omaclack.git --enable
```

Bar widget with the same Nerd Font keyboard glyph style as the shell's own
icons; it dims while sound is off or muted. Left-click opens the panel,
right-click toggles, the wheel nudges keyboard volume. The panel is one page:

- keyboard: volume, pack, velocity (louder when you type fast), plus a hint
  for the keyboard you are actually typing on
- mouse: on/off, pack and volume of its own
- quiet: mute while any app records the microphone, quiet hours, and apps to
  ignore by Wayland app id
- your own packs: import any Mechvibes or MechvibesDX pack into the packs folder

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

Wayland clients cannot hear every key; that is a compositor security rule, not
an Omaclack limitation. Hyprland's Lua `input.keyboard.key` event exists but
must not block or fork, so it cannot drive per-key audio. A Hyprland plugin
could forward keycodes to the daemon socket without the `input` group; that
is a separate `.so` loaded by the compositor, not a Wayland protocol. Until
someone ships that, the `input` group is the real door.

### Requirements

- Omarchy Quattro (`omarchy-shell`).
- PipeWire and a libsndfile that decodes Opus (standard on Omarchy).
- x86_64 or aarch64 for the Rust daemon (CI attaches both to GitHub releases);
  anything else runs the Python 3.10+ fallback (stdlib only, needs `pw-play`).

No sudo at runtime, no systemd units, no extra packages.

## Removal

```bash
omarchy plugin disable io.github.diogo7dias.omaclack
omarchy plugin remove io.github.diogo7dias.omaclack
```

The daemon dies with the shell (stdin pipe closes). Nothing is left listening.

User data outside the plugin directory is left alone:

```
~/.config/omarchy/omaclack.json
~/.config/omarchy/omaclack/packs/
```

Delete those yourself if you want them gone. The `input` group is not removed.

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
  sounds, modifiers included. Keys are press only. Mouse buttons play from a
  separate mouse pack and also click on release (`up/<name>.opus`, 30%
  quieter). Velocity scales a
  key from 80% (unhurried) to 100% (the previous key was under 100 ms ago).
- **Playback (Rust, default)**: every sample of the current keyboard and
  mouse pack is decoded once with libsndfile (the decoder pw-play uses) into
  48 kHz stereo float. One PipeWire stream owned by the daemon mixes the live
  voices in its realtime callback, filling exactly the frames each cycle asks
  for. The stream is created inactive and only activated while something
  plays; 2.5 s after the last key it deactivates so the sink can suspend.
- **Playback (Python fallback)**: `pw-play --volume <gain> <key>.opus` per
  event, capped at 24 in flight. No audio passes through Python.
- **Ignore list and calls**: the service watches the focused Wayland toplevel
  and mutes the daemon while its app id is listed, and, via Quickshell's
  PipeWire binding, while any `Stream/Input/Audio` node exists (an app
  recording the microphone). The daemon never learns which app is focused.
- **Stats** live in the daemon's memory only: a five-minute ring of press
  timestamps plus per-key counts for that window, and a session total with
  no codes. Nothing is written. The control socket never sees a live
  keystream; key events (latency only, no keycode) go to the parent pipe.

## IPC protocol

Newline-delimited JSON, same shape on the shell's stdin/stdout pipe and on the
socket (one socket client at a time), `flock()` on `ctl.sock.lock`.

```json
{"cmd": "play", "key": 30}            → {"ok": true, "latency_ms": 0.4}
{"cmd": "load", "pack": "topre"}      → {"ok": true, "pack": "topre"}
{"cmd": "mousepack", "pack": "razer"} → {"ok": true, "mouse_pack": "razer"}
{"cmd": "volume", "value": 70}        → {"ok": true, "volume": 70}          keyboard
{"cmd": "mousevolume", "value": 40}   → {"ok": true, "mouse_volume": 40}    mouse buttons
{"cmd": "play", "key": 272, "down": false} → mouse release sample (keys: {"ok": false})
{"cmd": "velocity", "value": true}    → {"ok": true, "velocity": true}
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
connect (status includes `packs`, `mouse_packs`, `devices`),
`{"evt": "key", "latency_ms": 0.4}` per sounded press on the parent pipe
only (no keycode; the control socket is not a keystream),
`{"evt": "theme", "slug": ...}` after a `theme` command (the panel ignores both events).
Latency is measured from the evdev read to the sample being handed to PipeWire.

Try it by hand:

```bash
sock=$(mktemp -d)/ctl.sock
python3 bin/omaclackd --socket="$sock" &
printf '{"cmd":"play","key":57}\n{"cmd":"quit"}\n' | nc -U "$sock"
```

## Sound packs

```
sounds/<pack>/<code>.opus        press   (stereo 48 kHz Opus, ~1 KB, panned by key position)
sounds/<pack>/default.opus       fallback for unmapped keys
sounds/<pack>/pack.json          name, credit, source, license, optional keys map
sounds/mouse/<pack>/...          left/right/middle(.opus) + up/ for the release,
                                 release: recorded|derived, keys map to BTN_* codes
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

### Keyboard packs (7)

| pack | name | character |
|---|---|---|
| `mx-blue` | Cherry MX Blue | clicky |
| `mx-brown` | Cherry MX Brown | tactile |
| `mx-red` | Cherry MX Red | light linear |
| `mx-black` | Cherry MX Black | heavy linear, deeper than red |
| `eg-purple` | Everglide Crystal Purple | clicky, tighter than MX Blue |
| `eg-oreo` | Everglide Oreo | creamy linear, short hits |
| `topre` | Topre Purple Hybrid | thocky electrocapacitive |

All seven come from [MechvibesDX](https://github.com/hainguyents13/mechvibes-dx)
sprites, where all 99 keys are recorded separately, so no two neighbouring
keys repeat the same sample.

### Mouse packs (4)

| pack | recording |
|---|---|
| `logitech` | OwlStorm, [Freesound 320146](https://freesound.org/s/320146/), CC0, via Typetone |
| `razer` | Katsuhira, [Freesound 555394](https://freesound.org/s/555394/), CC0, via Typetone |
| `crisp` | Six Ways, [Freesound 223445](https://freesound.org/s/223445/), CC0, via Typetone |
| `soft` | Breviceps, [Freesound 447938](https://freesound.org/s/447938/), CC0, via Typetone |

Each mouse sample is split into the press and its release so one physical
click is one sound. Only real button recordings ship; the effect-style packs
(ping, chat, vibrate, wooden) were dropped because they do not sound like a
mouse.

Rebuild everything from upstream checkouts with
`tools/build_sounds.py <mechvibes> <mechvibes-dx> <kbsim> <typetone>` (needs
`ffmpeg` and `libsndfile`, build-time only; every file is checked to open in
libsndfile because that is what `pw-play` decodes with).

## Credits

- **MechvibesDX** by [hainguyents13](https://github.com/hainguyents13), MIT.
  Every keyboard pack is sliced from its sprites using its press timings.
- **Omarchy Typetone** by [phuclh](https://github.com/phuclh/omarchy-typetone),
  MIT. The mouse packs are its `mouse-sounds/` renders of CC0 Freesound
  recordings by OwlStorm, Katsuhira and Six Ways.

Both licenses are reproduced in `sounds/LICENSES.md`.

## Development

```bash
python3 -m unittest discover -s tests            # Python daemon internals + protocol, pack checks
OMACLACKD_BIN=bin/omaclackd-x86_64 python3 -m unittest discover -s tests   # protocol tests against the Rust build
cd daemon && cargo test                          # packs, velocity, stats, evdev unit tests
tools/bench_daemon.py rust bin/omaclackd-x86_64  # startup, footprint, key-to-sound onset
tools/build_sounds.py <mechvibes> <mechvibes-dx> <kbsim> <typetone>   # re-import all packs (ffmpeg)
tools/omaclack-import <folder-or-zip> [--mouse]  # add a pack to ~/.config/omarchy/omaclack/packs
tools/dev-reload                                 # nudge the shell when the plugin dir is a symlink
```

`omarchy plugin add` installs by cloning, so anything committed is downloaded
forever, and neither a stripped ELF nor an Opus file delta-compresses: a
rebuilt `bin/omaclackd-x86_64` costs another ~700 KB of history, a re-encoded
pack about 100 KB. Refresh the committed binary on version bumps rather than on
every build, and re-run `tools/build_sounds.py` only when a pack actually
changes.

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

## Privacy

Omaclack never leaves this computer. There is no account, no telemetry, no
update ping, no HTTP client. The committed daemon links PipeWire and
libsndfile only; `grep` the tree for `http`, `AF_INET`, `urllib` and you
will not find a runtime caller.

What happens to a key:

| step | what |
|---|---|
| Read | Linux keycode from `/dev/input/event*` (or a mouse button). Not the character, not a keymap. |
| Used for | picking `sounds/<pack>/<code>.opus`. The daemon also keeps a five-minute in-memory count for its `stats` command; the panel no longer shows it. |
| Written | never. Settings in `~/.config/omarchy/omaclack.json` are pack names, volumes, quiet hours, ignore-list app ids. No keystrokes. |
| Sent off-machine | never. |
| Sent on-machine | latency of that press, no keycode, over the parent pipe to omarchy-shell. The control socket (`0600`, directory `0700` when it is `$XDG_RUNTIME_DIR/omaclack`) does not stream keys. |

The `input` group is the honest local caveat. Joining it lets *every* process
of your user open `/dev/input`, not just Omaclack. That is Linux's model, not
Omaclack phoning home. Wayland clients still cannot snoop keys; dropping the
group would need a Hyprland compositor plugin.

Stop it: disable the plugin, or `omarchy plugin disable io.github.diogo7dias.omaclack`.
The daemon dies with the shell (stdin pipe closes). Nothing is left listening.

## License

MIT
