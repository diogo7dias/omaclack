# Soundtap — Omarchy Keyboard Sound Plugin

Build an Omarchy Quattro plugin that plays mechanical keyboard sounds per keystroke. Similar concept to Mechey for macOS. Build in this repo (`~/code/soundtap`).

## Repo Layout (create these files)
```
io.github.ddm.soundtap/
├── manifest.json
├── Service.qml          # headless service kind: spawns helper daemon via Quickshell.Io.Process
├── BarWidget.qml        # bar-widget kind: 22x22 ripple icon, click opens panel
├── Panel.qml            # panel kind: toggle, pack selector, volume slider, mouse switch, denylist, latency display
├── bin/
│   └── soundtapd        # Python daemon: reads evdev, plays wav via pw-play, JSON protocol over unix socket
├── sounds/
│   ├── cherry-blue/
│   ├── cherry-brown/
│   ├── topre/
│   └── typewrite/
├── assets/
│   └── ripple.svg       # Hermarchy style: near-black SVG path, hairline
├── README.md
└── LICENSE              # MIT
```

## Architecture

### Plugin Kinds (from cliampui pattern - proven sibling)
The plugin declares BOTH kinds in manifest:
- `"kinds": ["service", "bar-widget"]`
- `"entryPoints": { "service": "Service.qml", "barWidget": "BarWidget.qml" }`

**Service.qml** (headless singleton, lives for shell lifetime):
- Spawns `bin/soundtapd` via `Quickshell.Io.Process`
- Connects to `$XDG_RUNTIME_DIR/soundtap/ctl.sock` for IPC
- Exposes reactive QML properties: `enabled`, `volume`, `currentPack`, `mouseEnabled`, `denylist`, `lastEvent`
- Persists state to `~/.config/omarchy/soundtap.json`
- Uses `Scope` to keep Process persistent across panel open/close

**BarWidget.qml + Panel.qml**:
- Bar widget: simple Icon/Image element with `onClicked: panel.open()` or similar OpenPanel pattern
- Panel: inherits from `PanelWindow` base type from `import Quickshell`
- UI sections: On/off switch, Pack chips, Volume slider, Mouse clicks switch, Ignore apps text list, Latency chart (horizontal bars)

### Helper Daemon (bin/soundtapd)
Python script (stdlib only). Runs as child of omarchy-shell.

**Input:**
- Reads `/dev/input/event*` using `select()` loop (single-threaded)
- Filters by `EV_KEY` for key events, `EV_MSC`+`BTN_*` for mouse clicks  
- Key codes < 0x100 only (skip consumer/vendor-only)
- Debounce: ignore same key within 30ms (configurable)
- Modifier keys alone: no sound unless followed by another key within 100ms

**Audio:**
- Plays WAV files via `subprocess.Popen(["pw-play", "--volume=N", "-"])` piping WAV bytes to stdin
- Each keycode maps to a file: `sounds/<pack>/<keycode>.wav`
- Missing files → fall back to `sounds/<pack>/default.wav`
- Packs shipped with 8-16 well-recorded keys + default.wav per pack

**IPC Protocol** (JSON lines over Unix socket at `$XDG_RUNTIME_DIR/soundtap/ctl.sock`):
```json
{"cmd": "play", "key": 30}           → {"ok": true, "latency_ms": 2.1}
{"cmd": "load", "pack": "cherry-blue"}
{"cmd": "volume", "value": 70}
{"cmd": "mute", "toggle": true/false}
{"cmd": "quit"}
```
Single client, `flock()` on socket. No logging, privacy-first.

### Sound Design
Create 4 starter packs. Each pack has individual WAV files per keycode + a default.wav fallback. Files should be short (< 80ms), 44.1kHz mono WAV. Recordings can be synthetic/percussive sounds if real recordings aren't possible — we can swap them later. Generate simple clicks/taps programmatically if needed.

Each pack gets distinct character:
- **cherry-blue**: crisp clicky, sharp transient
- **cherry-brown**: tactile bump, muted thock
- **topre**: deep rounded bottoming-out sound
- **typewrite**: metal stamp/clack, medium-long decay

You can use Python `sounddevice` or just generate minimal WAV files with structured data (PCM samples). If you don't have hardware synthesizer tools, create plausible synthetic percussive WAVs — they just need to not sound bad. The user will add better packs later.

### Manifest Format (exact schema - match omarchy docs)
```json
{
  "schemaVersion": 1,
  "id": "io.github.ddm.soundtap",
  "name": "SoundTap",
  "version": "1.0.0",
  "author": "Diogo M.",
  "license": "MIT",
  "description": "Mechanical keyboard typing sounds for Omarchy. Per-key sound packs, low-latency, Privacy-first.",
  "kinds": ["service", "bar-widget"],
  "entryPoints": {
    "service": "Service.qml",
    "barWidget": "BarWidget.qml"
  },
  "barWidget": {
    "displayName": "SoundTap",
    "category": "Utilities",
    "allowMultiple": false,
    "defaultSection": "right"
  }
}
```

### Panel UI Details
Panel content follows Hermarchy aesthetic (near-black background, hairline borders, tiny caps, monospace-ish, 95% mono colors):

1. **Status Switch** — big on/off toggle at top
2. **Volume Slider** — label left/right (0-100%), smooth slider
3. **Packs** — horizontal chip layout, one selected, others gray; "Get More Packs" button at end
4. **Mouse Clicks** — small switch beside volume
5. **Ignore These Apps** — comma-separated list of app IDs
6. **Latency Chart** — 50px wide mini-bar chart showing last 50 round-trip pings (vertical bars, color-coded green/yellow/red based on ms)

### Important Notes
- Plugins run inside omarchy-shell process (unsandboxed, but local-only anyway)
- This plugin ONLY reads input events and plays audio locally. No network. No external dependencies beyond PipeWire's pw-play (standard on Omarchy).
- The daemon runs as a child of omarchy-shell; when shell exits, daemon dies.
- No sudo required, no system services. Just user-space.
- For evdev access: the user may need to be in the `input` group, or udev rule. Document in README with one-line fix.

### Build Checklist
1. Create ALL files listed in repo layout
2. Write valid manifest.json 
3. Implement Service.qml with Process spawn + socket connect + reactive properties
4. Implement BarWidget.qml with ripple icon + open panel handler
5. Implement Panel.qml with all UI sections
6. Write soundtapd.py daemon with evdev reader + pw-play wrapper + JSON IPC
7. Create 4 sound packs with synthetic WAV files
8. Add Ripple SVG asset
9. Write comprehensive README.md with install instructions
10. Verify the folder structure is complete and consistent

Generate plausible synthetic WAV audio files programmatically in Python — don't wait for real recordings. We'll iterate on sounds later. Focus on getting the entire plugin working end-to-end.

IMPORTANT: Start by creating the directory structure and all skeleton files first, then fill each one in. Work through everything without waiting for approval — only block on genuinely undecidable design choices.
