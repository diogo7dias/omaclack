# Handover: Omaclack

You are taking over this project from the previous assistant. This document is
addressed to you, the next LLM. Read it fully before touching anything. Your
job is, in order: review, find and fix bugs, verify, then propose improvements.
Be thorough. Assume nothing below is correct until you have checked it.

Owner: diogo7dias (they/them). Repo: https://github.com/diogo7dias/omaclack
(public, MIT). The owner's machine is an Omarchy (Arch + Hyprland + Quickshell
"Quattro" shell) laptop, x86_64, one 1920x1200 display at 1.25 scale,
PipeWire 1.6, `AT Translated Set 2 keyboard` internal keyboard, `LIFT Mouse`.
The plugin directory `~/.config/omarchy/plugins/io.github.diogo7dias.omaclack`
is a symlink to the checkout at `~/Work/omaclack`.

## What this is

Mechanical keyboard and mouse click sounds for the Omarchy shell, modelled on
Mechey (macOS) and Mechvibes. Design goals the owner set and repeated:
tiny, private (no logging, no network), zero battery drain when idle, and
"click icon, hear sound, pick pack, done. No bloat."

## What was built (chronological, so you know the history)

1. Original plugin: QML bar widget + panel + service, Python daemon reading
   `/dev/input/event*`, one `pw-play` process per key on synthetic WAV packs.
2. Shell to daemon control moved from a Unix socket (Quickshell's Socket
   client never connected) to the daemon's own stdin/stdout pipe. The socket
   remains for CLI, tests and hooks.
3. Synthetic sounds replaced by real recordings: Mechvibes, MechvibesDX,
   kbsim (MIT) and Omarchy Typetone's CC0 mouse renders. Per-key Opus files,
   press and release, stereo panned by key position. 25 keyboard packs,
   10 mouse packs, 4,524 files, ~4 MB. Every file is validated to open in
   libsndfile at build time because pw-play decodes with it and it rejects a
   few percent of ffmpeg's short Opus files as malformed (that was the "A key
   is silent" bug).
4. Renamed from SoundTap to Omaclack (name checked for collisions).
5. Features: velocity (louder when typing fast), release sounds, room presets
   via a `pipewire -c` filter-chain child with synthesised impulse responses,
   separate mouse volume and pack, mute while any app records the microphone
   (Quickshell PipeWire binding), quiet hours, ignore list by app id,
   in-memory typing stats, evdev device name and a pack hint, theme-set hook
   with per-theme pack binding, user packs dir and an import tool for
   Mechvibes/DX/plain packs, scrollable panel, ten logo candidates page.
6. Rust daemon (`daemon/`) with the same JSON protocol and an in-process
   PipeWire stream (samples pre-decoded with libsndfile, mixed in the RT
   callback, stream inactive when idle). Python daemon kept as fallback via a
   launcher. Measured with `tools/bench_daemon.py`: key to sound 49 ms
   (Python + pw-play) vs 36 ms (Rust + pw-play) vs 18 ms (Rust in-process).

## Layout

```
manifest.json          plugin id io.github.diogo7dias.omaclack, kinds service + bar-widget, keepLoaded
Service.qml            daemon Process owner, settings (~/.config/omarchy/omaclack.json), all set* API
BarWidget.qml          22 px glyph (keycap + waveform on a Canvas), click/right-click/wheel, hosts Panel via Loader
Panel.qml              the popup: status, keyboard volume/pack, feel, mouse, quiet, ignore, typing, latency, more
bin/omaclackd          sh launcher: exec omaclackd-$(uname -m) if present, else python3 omaclackd.py
bin/omaclackd-x86_64   committed Rust release binary (~700 KB)
bin/omaclackd.py       Python daemon (stdlib only), protocol reference implementation
daemon/                Rust crate: main.rs (loop, protocol), ipc.rs, evdev.rs, packs.rs, player.rs, room.rs, stats.rs
sounds/<pack>/         <code>.opus, up/<code>.opus, default.opus, pack.json; sounds/mouse/<pack>/ likewise
sounds/LICENSES.md     upstream license texts
tools/build_sounds.py  imports all packs from four upstream checkouts (ffmpeg + libsndfile)
tools/omaclack-import  imports one user pack into ~/.config/omarchy/omaclack/packs
tools/omaclack-theme-hook   Omarchy theme-set hook -> daemon "theme" command
tools/bench_daemon.py  startup / RSS / idle CPU / round trip / key-to-sound onset on a null sink
tools/dev-reload       recreates the plugin symlink so the shell's inotify watcher reloads
tests/test_omaclackd.py 23 tests; OMACLACKD_BIN=bin/omaclackd-x86_64 runs the process-level ones on Rust
design/logos.html      glyph candidates
.github/ISSUE_TEMPLATE bug and pack request
```

## Protocol (both daemons must stay identical)

Newline JSON on the daemon's stdin/stdout (the shell) and on
`$XDG_RUNTIME_DIR/omaclack/ctl.sock` (one client at a time, `flock` on
`ctl.sock.lock`, exit code 2 if another daemon holds it). Commands: play
(key, down), load, mousepack, volume, mousevolume, mute, enable, mouse,
velocity, release, room, stats, status, theme, ping, quit. Events: hello (full
status + denied), key (code, latency_ms), theme (slug). See README "IPC
protocol". Service.qml resyncs everything in `pushState()` after each hello.

## How to verify anything

```bash
python3 -m unittest -v tests/test_omaclackd.py                       # Python daemon + packs
OMACLACKD_BIN=bin/omaclackd-x86_64 python3 -m unittest tests/test_omaclackd.py   # Rust daemon
cd daemon && cargo build --release && cp target/release/omaclackd ../bin/.new && mv -f ../bin/.new ../bin/omaclackd-x86_64
tools/bench_daemon.py rust bin/omaclackd-x86_64
tools/dev-reload                                  # then check: journalctl --user _COMM=quickshell --since "1 min ago" | grep -iE 'omaclack|WARN scene'
omarchy restart shell                             # needed when a QML compile error was cached, or Service.qml changed
omarchy-shell shell summon io.github.diogo7dias.omaclack '{}'   # open the panel; hide with "hide"
grim -s 0.6 -o eDP-1 /tmp/panel.png              # screenshot to look at the panel
printf '{"cmd":"status"}\n' | socat - UNIX-CONNECT:$XDG_RUNTIME_DIR/omaclack/ctl.sock
```

Toolchain: Rust is installed via mise (`~/.local/share/mise/shims/cargo`,
put the shims dir on PATH). ffmpeg, libsndfile, libpipewire headers, parecord
and pactl are present. Upstream checkouts used by `build_sounds.py` live in a
scratch dir and may be gone; re-clone with sparse checkout if you need them
(mechvibes `src/audio`, mechvibes-dx `soundpacks`, kbsim
`src/assets/audio`, omarchy-typetone `mouse-sounds`).

Two Quickshell gotchas that cost time: a QML property named `data` or `right`
on an Item silently kills the whole Panel (it is a compile error printed as
`WARN scene` in the journal, and the widget's Loader used to swallow it, now
it logs "Panel.qml failed to load"); and after fixing a compile error the old
compiled copy can stay cached until `omarchy restart shell`. The inotify
watcher does not follow the plugin symlink, hence `tools/dev-reload`.

## Known weak spots (start your review here)

These are things the previous assistant knows are imperfect or untested.
Verify each, fix what is broken, and look for what is missing from this list.

Daemon, Rust:
- Room reconnect drops the stream and creates a new one; voices queued
  during the swap may be lost. `set_target` is only called from the room
  command. Untested: room switch while keys are playing.
- Mixer clamps the sum to [-1, 1] per sample; 24 voices can clip audibly
  under key mashing. No soft limiter, no per-voice fade on eviction (the
  oldest voice is dropped abruptly at the cap).
- `sounds_dir()` is derived from the executable path (`bin/../sounds`). If
  someone copies the binary elsewhere it finds no packs and reports none.
- Idle deactivation: a 500 ms timer in the PipeWire thread checks
  `last_activity`; activation is a `Wake` message. Not measured: CPU while
  typing continuously (stream active means one graph wakeup per quantum).
- `decode()` linear-resamples non-48 kHz user packs. Fine for clicks, but
  not checked with a real 44.1 kHz user pack end to end.
- The `denied` flag only reflects EACCES on open; a user outside the `input`
  group sees "no /dev/input access" only if at least one open fails that way.
- `Stats::report` is O(window) on every panel poll (1 Hz while open). Fine,
  but the histogram bucket math should match the Python one exactly; check.
- No unit tests for Rust internals (packs, velocity, stats, evdev parsing);
  only the Python module has those. Consider `cargo test` mirrors.
- Signals: SIGTERM sets a flag polled at most every 3 s (poll timeout). The
  room child is killed on normal exit but orphaned on SIGKILL.

Daemon, Python (`bin/omaclackd.py`):
- Must be kept protocol-identical with Rust; there is no automated check that
  status fields match between the two beyond the shared tests. Diff them.
- Reaps pw-play children only on the next play; a burst then silence leaves
  up to 24 zombies until the next key. Harmless but ugly.

Service.qml:
- `micInUse` treats any `Stream/Input/Audio` node as a call: screen recorders
  with audio, visualisers and voice assistants will mute typing sounds. A
  filter on `application.name` / `media.role` or an allow-list would be better.
  Also, the 5 s fallback timer means up to 5 s before unmute if the
  removal signal is missed.
- Quiet hours: volumes sent to the daemon are scaled, the panel shows the
  unscaled values (intended), but `setQuietPercent` only pushes when already
  inside quiet hours; check the edge at the boundary minute.
- `deviceHint` regexes are broad (`rk` matches many names). Hint only shows
  when the current pack differs from the suggested one.
- Pack fallback on a vanished pack keys off the error string containing
  `default.opus`; both daemons format it that way, keep them in sync.
- `themePacks` binding is only exercised by hand; no test.
- Config is written with `version: 2` and read leniently; there is no
  migration if fields change again.

Panel.qml:
- It is taller than the owner's 1200 px screen and scrolls inside the card
  with no scrollbar and no visual affordance that it scrolls. Consider a
  compact mode, tabs (Keyboard / Mouse / Quiet / Stats), or fewer chips
  (25 pack chips is a lot).
- The latency chart mixes two series: per-key spawn latency from the daemon
  (sub-millisecond) and ping round trips through the pipe (a few ms). It was
  meaningful when latency was the question; now it mostly shows pipe RTT.
- Inline components (`Switch`, `Chip`, `Credit`, `SectionLabel`) rely on
  `parent.<prop>` lookups; fragile if wrapped in another Item.
- The "theme hook not installed" chip runs a shell command through the bar;
  no feedback on success. After installation the chip text only changes
  once a theme event has arrived.
- Mouse side buttons (BTN_SIDE/EXTRA/FORWARD/BACK) reuse the middle sample.
- No keyboard navigation inside the panel; other Omarchy panels support
  Escape and arrow switching via `switchPanelFrom` (wired but untested).

Sounds and tools:
- Two packs (`mx-red-pbt`, `nk-cream`) have derived releases (pitched,
  quieter copies of the press). Labelled in the panel. If a real release
  source turns up, replace.
- kbsim packs produce a file per key (row sample copied per key) purely so
  each key can be panned; that is ~100 near-identical files per pack. A
  pan-at-playback design would shrink those packs to five files.
- `omaclack-import` sets `license: "see source"` and does not check the
  source pack's license; it also trusts config.json shapes loosely.
- `build_sounds.py` is slow (~2 min) and re-encodes everything; no
  incremental mode.
- `LICENSES.md` was assembled by hand; verify every pack's attribution
  matches its `pack.json` and the upstream file.

Docs and repo:
- README is long; the install one-liner is at the top, but the feature list
  and the benchmark table should be checked against the code after your
  fixes. No screenshot or video yet: the previous assistant could not record
  real typing. The owner may supply a clip; wire it into the README.
- Only an x86_64 binary is committed. No CI. Propose a GitHub Actions
  workflow that runs the tests, builds the Rust daemon for x86_64 and aarch64,
  and attaches binaries to releases.
- `design/logos.html` is a design artefact living in the repo; decide if it
  stays.

## Your tasks, in order

1. Read the code, all of it: the three QML files, both daemons, the tools,
   the tests. Compare Python and Rust daemons command by command and status
   field by field. Note every discrepancy.
2. Run everything in "How to verify anything". Reproduce the benchmark and
   confirm the README table is honest on this machine.
3. Exercise the plugin live through the shell: open the panel, switch packs,
   rooms, mouse packs, toggle everything, set quiet hours to now, start
   `parecord --raw /dev/null` to trigger call mute, install the theme hook and
   run `omarchy theme set <slug>` twice, import a pack with the tool, click
   "rescan packs". Watch the journal for QML warnings the whole time.
4. Fix what you find. Small, reviewable commits with clear messages. Do not
   widen scope while fixing. Keep both daemons protocol-identical and keep
   the tests passing on both (`OMACLACKD_BIN`).
5. Do a second full pass after your fixes, as a reviewer who has not seen
   the code before. Look specifically for: silent failures (a Loader that
   does not log, a send that drops), state that can drift between the
   service and the daemon, anything that writes to disk that should not,
   anything that could drain battery (timers, polling, active streams).
6. Write your findings and proposals to the owner: what was broken, what you
   changed, what you deliberately left alone and why, and a ranked list of
   improvements with effort estimates. Suggested candidates: CI and
   multi-arch binaries; a compact or tabbed panel; smarter call detection;
   pan at playback instead of per-key files for row packs; a soft limiter in
   the mixer; Rust unit tests; a hero clip in the README; a Wayland-native
   input path that does not need the `input` group, if one exists for
   Hyprland (research it, do not assume).

Style the owner expects: short messages, answer first, no hedging, numbers
in tables, code in fenced blocks, names of files only when the reader must
open them. Commit messages end with the co-author trailer used in `git log`.
Ask before anything destructive or outward-facing; otherwise proceed.
