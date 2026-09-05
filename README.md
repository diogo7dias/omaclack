# SoundTap

Mechanical keyboard typing sounds for [Omarchy](https://omarchy.org) (Quattro shell).
Per-key sound packs cut from real switch recordings, one `pw-play` per
keypress, no network, no logging. About 800 KB installed.

Bar widget with a ripple glyph. Click it for the panel: on/off, keyboard
volume and pack, mouse clicks with their own volume and pack, ignored apps,
and a live latency chart. Right-click toggles, the wheel nudges keyboard volume.

## Install

```bash
omarchy plugin add https://github.com/diogo7dias/soundtap.git --enable
```

Manual: copy this directory to `~/.config/omarchy/plugins/io.github.ddm.soundtap/`,
then `omarchy-shell shell rescanPlugins` and `omarchy plugin enable io.github.ddm.soundtap`.

The widget lands in the bar's right section. Move it with `omarchy bar move`.

### Input access (one-time)

The helper reads `/dev/input/event*` directly. Your user needs the `input` group:

```bash
sudo usermod -aG input "$USER"   # then log out and back in
```

Until then the panel shows `no /dev/input access` and nothing plays.

### Requirements

- Omarchy Quattro (`omarchy-shell`).
- PipeWire with `pw-play` and a libsndfile that decodes Opus (standard on Omarchy).
- Python 3.10+ (stdlib only).

No sudo at runtime, no systemd units, no extra packages.

## How it works

```
omarchy-shell
 └─ Service.qml            reactive state, settings file, JSON over the daemon's pipe
     └─ bin/soundtapd      Python: evdev reader + key filter + JSON stdin/stdout + ctl socket
         └─ pw-play        one short-lived process per keypress: `pw-play --volume g <key>.opus`
```

- **Service.qml** spawns the daemon with `Quickshell.Io.Process` and talks JSON
  lines over that process's own stdin/stdout. The pipe is also the lifeline:
  shell exits, pipe closes, daemon exits. Nothing to connect or reconnect.
  State persists to `~/.config/omarchy/soundtap.json`.
  The daemon additionally binds `$XDG_RUNTIME_DIR/soundtap/ctl.sock` with the
  same protocol for the CLI, tests and the benchmark.
- **soundtapd** opens every `/dev/input/event*` it can, `select()`s on them,
  keeps only key-down events for keyboard codes (`< 0x100`) and mouse buttons
  (`BTN_LEFT..BTN_TASK`). Same key within 30 ms is dropped; every other press
  sounds, modifiers included. Mouse buttons play from a separate mouse pack.
- **Playback**: each keypress spawns `pw-play --volume <gain> sounds/<pack>/<code>.opus`
  (capped at 16 in flight, children reaped without threads). No audio bytes pass
  through Python: pw-play decodes the Opus file with libsndfile and exits when
  it ends. Measured against a persistent paced stream, the one-shot approach
  had identical onset latency (within 2 ms), so it stays: nothing resident, no
  stream state, and the daemon idles in `select()` between keys.
- **Ignore list**: the service watches the focused Wayland toplevel and mutes
  the daemon while its app id is listed. The daemon itself never learns which
  app is focused.

## IPC protocol

Newline-delimited JSON, same shape on the shell's stdin/stdout pipe and on the
socket (one socket client at a time), `flock()` on `ctl.sock.lock`.

```json
{"cmd": "play", "key": 30}            → {"ok": true, "latency_ms": 0.4}
{"cmd": "load", "pack": "topre"}      → {"ok": true, "pack": "topre"}
{"cmd": "mousepack", "pack": "razer"} → {"ok": true, "mouse_pack": "razer"}
{"cmd": "volume", "value": 70}        → {"ok": true, "volume": 70}          keyboard
{"cmd": "mousevolume", "value": 40}   → {"ok": true, "mouse_volume": 40}    mouse buttons
{"cmd": "mute", "toggle": true}       → {"ok": true, "muted": true}
{"cmd": "mouse", "value": false}      → {"ok": true, "mouse": false}
{"cmd": "enable", "value": true}      → {"ok": true, "enabled": true}
{"cmd": "status"}                     → {"ok": true, "pack": ..., "packs": [...], ...}
{"cmd": "ping", "t": 123}             → {"ok": true, "pong": 123}
{"cmd": "quit"}                       → {"ok": true, "quit": true}
```

Daemon-initiated events: `{"evt": "hello", ...status, "denied": bool}` on
connect, `{"evt": "key", "key": 30, "latency_ms": 0.4}` per sounded key.
Latency is measured from the evdev read to the pw-play spawn.

Try it by hand:

```bash
python3 bin/soundtapd --socket=/tmp/st.sock &
printf '{"cmd":"play","key":57}\n{"cmd":"quit"}\n' | nc -U /tmp/st.sock
```

## Sound packs

`sounds/<pack>/<keycode>.opus` plus `default.opus` (fallback for unmapped keys
and mouse buttons) and `pack.json` with `name`, `credit`, `source`, `license`
and an optional `keys` map (`{"30": "row3.opus"}`) for packs that share one
sample across many keys. Keycodes are Linux `KEY_*` numbers (`30` = A,
`57` = space, `28` = enter). Files are mono 48 kHz Opus, about 1.5 KB each.

Nine packs ship, all MIT-licensed recordings from two projects:

| pack              | name            | source                                                          |
|-------------------|-----------------|-----------------------------------------------------------------|
| `mx-blue`         | Cherry MX Blue  | [Mechvibes](https://github.com/hainguyents13/mechvibes) per-key |
| `mx-brown`        | Cherry MX Brown | Mechvibes per-key                                               |
| `mx-red`          | Cherry MX Red   | Mechvibes per-key                                               |
| `mx-black`        | Cherry MX Black | Mechvibes per-key                                               |
| `topre`           | Topre           | Mechvibes per-key                                               |
| `holy-panda`      | Holy Panda      | [kbsim](https://github.com/tplai/kbsim) per-row                 |
| `buckling-spring` | Buckling Spring | kbsim per-row                                                   |
| `box-navy`        | Kailh Box Navy  | kbsim per-row                                                   |
| `alps-blue`       | Alps Blue       | kbsim per-row                                                   |

Mouse buttons use their own packs and volume. Packs live under
`sounds/mouse/<pack>/` with `left.opus`, `right.opus`, `middle.opus` (side
buttons reuse middle), each trimmed to the press and its release so one
physical click is one sound:

| pack       | recording                                                        |
|------------|------------------------------------------------------------------|
| `logitech` | OwlStorm, [Freesound 320146](https://freesound.org/s/320146/), CC0 |
| `razer`    | Katsuhira, [Freesound 555394](https://freesound.org/s/555394/), CC0 |
| `crisp`    | Six Ways, [Freesound 223445](https://freesound.org/s/223445/), CC0 |

These are the trimmed and filtered renders from
[Omarchy Typetone](https://github.com/phuclh/omarchy-typetone) (MIT).

The panel shows the credit under each set of chips; clicking it opens the
source. Rebuild from upstream checkouts with
`tools/build_sounds.py <mechvibes> <kbsim> <typetone>` (needs `ffmpeg` and
`libsndfile`, build-time only; every file is checked to open in libsndfile
because that is what `pw-play` decodes with).

Add your own: drop a folder with at least `default.opus` (or `.wav`, `.ogg`,
`.flac`) into `sounds/` and it appears in the panel on the next connect.

## Credits

- **Mechvibes** by [hainguyents13](https://github.com/hainguyents13/mechvibes),
  MIT. The Cherry MX and Topre packs are sliced from its `src/audio/*` sprites.
- **kbsim** by [Thomas Lai](https://github.com/tplai/kbsim), MIT. The Holy
  Panda, Buckling Spring, Box Navy and Alps Blue packs are its `press/` samples.
- **Omarchy Typetone** by [phuclh](https://github.com/phuclh/omarchy-typetone),
  MIT. The mouse packs are its `mouse-sounds/` renders of CC0 Freesound
  recordings by OwlStorm, Katsuhira and Six Ways.

Both licenses are reproduced in `sounds/LICENSES.md`.

## Development

```bash
python3 -m unittest discover -s tests            # daemon, player, filter, protocol, pack checks
tools/build_sounds.py <mechvibes> <kbsim> <typetone>   # re-import sound packs (ffmpeg)
tools/dev-reload                                 # nudge the shell when the plugin dir is a symlink
```

Saving any file under `~/.config/omarchy/plugins/<id>/` hot-reloads the whole
plugin, service and daemon included. Two gotchas when the plugin directory is a
**symlink** to your checkout: the shell's `inotifywait -r` does not follow
symlinks, so edits go unnoticed until you run `tools/dev-reload` (recreates the
symlink, which the watcher does see); and a QML file that fails to compile can
stay cached in the running shell after you fix it, so if the panel still will
not open after a reload, `omarchy restart shell`.

## Privacy

Reads keycodes only to pick a sample; never stores, logs, or transmits them.
The socket is `0600` inside `$XDG_RUNTIME_DIR`. No network access anywhere.

## License

MIT
